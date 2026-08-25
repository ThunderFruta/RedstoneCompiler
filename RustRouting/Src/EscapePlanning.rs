use crate::Assignment::{
    ParseContractRequirements, SelectionHasPoweredAccessWitnessExact,
    SortCandidatesWithDeadline,
};
use crate::AssignmentPlanning::{
    PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline,
    PlanAuthoritativeCandidateGroupsWithInitialExpansionDeadlineAndCrossAir,
};
use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{
    AssignmentCandidate, AssignmentPoweredAccessConstraint, ClaimMask, ClaimMaskBuildError,
    Direction, Position, RoutingAssignmentResult, TemplateRoutingAssignmentResult,
};
use crate::RoutingThreadPool;

const MAXIMUM_MEMBER_ESCAPE_SHARD_COUNT: usize = 8;
use pyo3::PyResult;
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap, HashMap, HashSet, VecDeque};
use std::hash::{BuildHasherDefault, Hasher};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

#[derive(Debug)]
struct EscapeClaimNode {
    Wire: Position,
    Air: Option<Position>,
    WireBloom: [u64; 4],
    SupportBloom: [u64; 4],
    AirBloom: [u64; 4],
    Parent: Option<Arc<EscapeClaimNode>>,
}

const ESCAPE_DIRECTION_STATE_COUNT: usize = 13;
const ESCAPE_POWER_STATE_COUNT: usize = 16;

#[derive(Default)]
struct PackedStateHasher(u64);

impl Hasher for PackedStateHasher {
    fn finish(&self) -> u64 {
        self.0
    }

    fn write(&mut self, Bytes: &[u8]) {
        let mut Value = 0u64;
        for (Index, Byte) in Bytes.iter().take(8).enumerate() {
            Value |= u64::from(*Byte) << (Index * 8);
        }
        self.0 = Value;
    }

    fn write_usize(&mut self, Value: usize) {
        self.0 = Value as u64;
    }
}

type PackedStateMap<Value> = HashMap<usize, Value, BuildHasherDefault<PackedStateHasher>>;

const ESCAPE_EXPANSION_LEASE_SIZE: usize = 4096;

struct SharedEscapeExpansionBudget {
    MaximumExpansionCount: usize,
    ReservedExpansionCount: AtomicUsize,
    CommittedExpansionCount: AtomicUsize,
}

impl SharedEscapeExpansionBudget {
    fn New(MaximumExpansionCount: usize) -> Self {
        Self {
            MaximumExpansionCount,
            ReservedExpansionCount: AtomicUsize::new(0),
            CommittedExpansionCount: AtomicUsize::new(0),
        }
    }

    fn ExpansionCount(&self) -> usize {
        self.CommittedExpansionCount.load(Ordering::Relaxed)
    }
}

struct SharedEscapeExpansionLease<'a> {
    Budget: &'a SharedEscapeExpansionBudget,
    ReservedExpansionCount: usize,
    RemainingExpansionCount: usize,
}

impl<'a> SharedEscapeExpansionLease<'a> {
    fn New(Budget: &'a SharedEscapeExpansionBudget) -> Self {
        Self {
            Budget,
            ReservedExpansionCount: 0,
            RemainingExpansionCount: 0,
        }
    }

    fn CommitAndRelease(&mut self) {
        if self.ReservedExpansionCount == 0 {
            return;
        }
        let ConsumedExpansionCount = self
            .ReservedExpansionCount
            .saturating_sub(self.RemainingExpansionCount);
        self.Budget
            .CommittedExpansionCount
            .fetch_add(ConsumedExpansionCount, Ordering::Relaxed);
        if self.RemainingExpansionCount > 0 {
            self.Budget
                .ReservedExpansionCount
                .fetch_sub(self.RemainingExpansionCount, Ordering::Relaxed);
        }
        self.ReservedExpansionCount = 0;
        self.RemainingExpansionCount = 0;
    }

    fn TryConsume(&mut self) -> bool {
        if self.RemainingExpansionCount == 0 {
            self.CommitAndRelease();
            loop {
                if self.Budget.ExpansionCount() >= self.Budget.MaximumExpansionCount {
                    return false;
                }
                let ReservedExpansionCount =
                    self.Budget.ReservedExpansionCount.load(Ordering::Relaxed);
                if ReservedExpansionCount >= self.Budget.MaximumExpansionCount {
                    // Other workers own the remaining finite allowance. They
                    // either consume it or return their unused lease before
                    // this worker can determine that the exact shared cap is
                    // exhausted.
                    std::thread::yield_now();
                    continue;
                }
                let LeaseExpansionCount = ESCAPE_EXPANSION_LEASE_SIZE.min(
                    self.Budget
                        .MaximumExpansionCount
                        .saturating_sub(ReservedExpansionCount),
                );
                if self
                    .Budget
                    .ReservedExpansionCount
                    .compare_exchange_weak(
                        ReservedExpansionCount,
                        ReservedExpansionCount.saturating_add(LeaseExpansionCount),
                        Ordering::Relaxed,
                        Ordering::Relaxed,
                    )
                    .is_ok()
                {
                    self.ReservedExpansionCount = LeaseExpansionCount;
                    self.RemainingExpansionCount = LeaseExpansionCount;
                    break;
                }
            }
        }
        self.RemainingExpansionCount -= 1;
        true
    }
}

impl Drop for SharedEscapeExpansionLease<'_> {
    fn drop(&mut self) {
        self.CommitAndRelease();
    }
}

fn TryCountEscapeExpansion(
    LocalExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    SharedExpansionLease: Option<&mut SharedEscapeExpansionLease<'_>>,
) -> bool {
    if let Some(Lease) = SharedExpansionLease {
        if !Lease.TryConsume() {
            return false;
        }
    } else if *LocalExpansionCount >= MaximumExpansionCount {
        return false;
    }
    *LocalExpansionCount += 1;
    true
}

fn LayeredEscapeLowerBound(
    Current: Position,
    PriorDirection: Direction,
    Ingress: Position,
    BendPenalty: usize,
) -> usize {
    let Delta = (
        Ingress.0 - Current.0,
        Ingress.1 - Current.1,
        Ingress.2 - Current.2,
    );
    let RequiredDirections = [
        (Delta.0.signum(), 0, 0),
        (0, Delta.1.signum(), 0),
        (0, 0, Delta.2.signum()),
    ]
    .into_iter()
    .enumerate()
    .filter_map(|(Axis, DirectionValue)| {
        [Delta.0, Delta.1, Delta.2][Axis]
            .ne(&0)
            .then_some(DirectionValue)
    })
    .collect::<Vec<_>>();
    let Manhattan = Delta.0.unsigned_abs() as usize
        + Delta.1.unsigned_abs() as usize
        + Delta.2.unsigned_abs() as usize;
    let DirectionChangeCount = if RequiredDirections.is_empty() {
        0
    } else if PriorDirection == (0, 0, 0) {
        RequiredDirections.len().saturating_sub(1)
    } else if RequiredDirections.contains(&PriorDirection) {
        RequiredDirections.len().saturating_sub(1)
    } else {
        RequiredDirections.len()
    };
    Manhattan.saturating_add(DirectionChangeCount.saturating_mul(BendPenalty))
}

#[derive(Clone, Copy)]
struct PoweredEscapeVisit {
    Cost: u32,
    ParentState: u32,
    ClaimRecord: u32,
}

impl PoweredEscapeVisit {
    fn New(Cost: usize, ParentState: usize, ClaimRecord: usize) -> Self {
        Self {
            Cost: u32::try_from(Cost).expect("bounded powered escape cost fits u32"),
            ParentState: if ParentState == usize::MAX {
                u32::MAX
            } else {
                u32::try_from(ParentState).expect("powered escape state index fits u32")
            },
            ClaimRecord: u32::try_from(ClaimRecord)
                .expect("powered escape claim record index fits u32"),
        }
    }

    fn CostValue(self) -> usize {
        self.Cost as usize
    }

    fn ParentStateValue(self) -> usize {
        if self.ParentState == u32::MAX {
            usize::MAX
        } else {
            self.ParentState as usize
        }
    }

    fn ClaimRecordValue(self) -> usize {
        self.ClaimRecord as usize
    }
}
const ESCAPE_INITIAL_DIRECTION_STATE: usize = 0;

fn EscapeDirectionStateIndex(DirectionValue: Direction) -> usize {
    match DirectionValue {
        (0, 0, 0) => ESCAPE_INITIAL_DIRECTION_STATE,
        (1, 0, 0) => 1,
        (-1, 0, 0) => 2,
        (0, 0, 1) => 3,
        (0, 0, -1) => 4,
        (1, 1, 0) => 5,
        (1, -1, 0) => 6,
        (-1, 1, 0) => 7,
        (-1, -1, 0) => 8,
        (0, 1, 1) => 9,
        (0, -1, 1) => 10,
        (0, 1, -1) => 11,
        (0, -1, -1) => 12,
        _ => panic!("escape graph contains an unsupported direction"),
    }
}

fn PoweredEscapeStateIndex(
    NodeIndex: usize,
    DirectionValue: Direction,
    PowerRemaining: u8,
) -> usize {
    (NodeIndex * ESCAPE_DIRECTION_STATE_COUNT + EscapeDirectionStateIndex(DirectionValue))
        * ESCAPE_POWER_STATE_COUNT
        + usize::from(PowerRemaining)
}

fn PoweredEscapeStateNodeIndex(StateIndex: usize) -> usize {
    StateIndex / (ESCAPE_DIRECTION_STATE_COUNT * ESCAPE_POWER_STATE_COUNT)
}

struct IndexedEscapeGraph {
    Positions: Vec<Position>,
    PositionIndices: HashMap<Position, usize>,
    NeighborIndices: Vec<Vec<usize>>,
}

impl IndexedEscapeGraph {
    fn New(Adjacency: &HashMap<Position, Vec<Position>>) -> Self {
        let mut Positions: Vec<Position> = Adjacency.keys().copied().collect();
        Positions.sort_unstable();
        let PositionIndices: HashMap<Position, usize> = Positions
            .iter()
            .copied()
            .enumerate()
            .map(|(Index, PositionValue)| (PositionValue, Index))
            .collect();
        let NeighborIndices = Positions
            .iter()
            .map(|PositionValue| {
                Adjacency
                    .get(PositionValue)
                    .into_iter()
                    .flatten()
                    .map(|Neighbor| {
                        *PositionIndices
                            .get(Neighbor)
                            .expect("escape adjacency references an unknown node")
                    })
                    .collect()
            })
            .collect();
        Self {
            Positions,
            PositionIndices,
            NeighborIndices,
        }
    }

    fn StateIndex(&self, NodeIndex: usize, DirectionValue: Direction) -> usize {
        NodeIndex * ESCAPE_DIRECTION_STATE_COUNT + EscapeDirectionStateIndex(DirectionValue)
    }

    fn StatePosition(&self, StateIndex: usize) -> Position {
        self.Positions[StateIndex / ESCAPE_DIRECTION_STATE_COUNT]
    }
}

struct IndexedEscapeWorkspace {
    BestCosts: Vec<usize>,
    Epochs: Vec<u32>,
    ParentStates: Vec<usize>,
    ClaimRecordByState: Vec<usize>,
    ClaimRecords: Vec<IndexedEscapeClaimNode>,
    PoweredVisits: PackedStateMap<PoweredEscapeVisit>,
    Epoch: u32,
}

impl IndexedEscapeWorkspace {
    fn New(StateCount: usize) -> Self {
        Self {
            BestCosts: vec![usize::MAX; StateCount],
            Epochs: vec![0; StateCount],
            ParentStates: vec![usize::MAX; StateCount],
            ClaimRecordByState: vec![usize::MAX; StateCount],
            ClaimRecords: Vec::new(),
            PoweredVisits: PackedStateMap::with_capacity_and_hasher(
                StateCount
                    .saturating_div(ESCAPE_DIRECTION_STATE_COUNT)
                    .saturating_mul(8),
                BuildHasherDefault::default(),
            ),
            Epoch: 0,
        }
    }

    fn BeginSearch(&mut self) {
        self.ClaimRecords.clear();
        self.Epoch = self.Epoch.wrapping_add(1);
        if self.Epoch == 0 {
            self.Epochs.fill(0);
            self.Epoch = 1;
        }
    }

    fn Cost(&self, StateIndex: usize) -> usize {
        if self.Epochs[StateIndex] == self.Epoch {
            self.BestCosts[StateIndex]
        } else {
            usize::MAX
        }
    }

    fn SetState(
        &mut self,
        StateIndex: usize,
        Cost: usize,
        ParentState: usize,
        ClaimRecordIndex: usize,
    ) {
        self.Epochs[StateIndex] = self.Epoch;
        self.BestCosts[StateIndex] = Cost;
        self.ParentStates[StateIndex] = ParentState;
        self.ClaimRecordByState[StateIndex] = ClaimRecordIndex;
    }
}

fn EscapeClaimBloomIndex(PositionValue: Position) -> usize {
    // This is only a negative filter.  A bloom hit always falls through to
    // the exact parent-chain comparison below, so collisions cannot change
    // legality or deterministic path selection.
    let mut Value = (PositionValue.0 as u32).wrapping_mul(0x9E37_79B1);
    Value ^= (PositionValue.1 as u32)
        .wrapping_mul(0x85EB_CA77)
        .rotate_left(11);
    Value ^= (PositionValue.2 as u32)
        .wrapping_mul(0xC2B2_AE3D)
        .rotate_left(19);
    Value ^= Value >> 16;
    (Value as usize) & 255
}

fn EscapeClaimBloomContainsIndex(Bloom: &[u64; 4], Index: usize) -> bool {
    Bloom[Index / 64] & (1u64 << (Index % 64)) != 0
}

fn EscapeClaimBloomInsertIndex(Bloom: &mut [u64; 4], Index: usize) {
    Bloom[Index / 64] |= 1u64 << (Index % 64);
}

fn ExtendEscapeClaims(
    Parent: Option<Arc<EscapeClaimNode>>,
    Current: Option<Position>,
    Next: Position,
) -> Option<Arc<EscapeClaimNode>> {
    let NextSupport = (Next.0, Next.1 - 1, Next.2);
    let NextAir = Current.and_then(|CurrentValue| {
        if CurrentValue.1 == Next.1 {
            None
        } else {
            let Lower = if CurrentValue.1 < Next.1 {
                CurrentValue
            } else {
                Next
            };
            Some((Lower.0, Lower.1 + 1, Lower.2))
        }
    });
    if NextAir.is_some_and(|Air| Air == Next || Air == NextSupport) {
        return None;
    }
    let NextBloomIndex = EscapeClaimBloomIndex(Next);
    let NextSupportBloomIndex = EscapeClaimBloomIndex(NextSupport);
    let NextAirBloomIndex = NextAir.map(EscapeClaimBloomIndex);
    let MustCheckExactClaims = Parent.as_ref().is_some_and(|Node| {
        EscapeClaimBloomContainsIndex(&Node.AirBloom, NextBloomIndex)
            || EscapeClaimBloomContainsIndex(&Node.SupportBloom, NextBloomIndex)
            || EscapeClaimBloomContainsIndex(&Node.WireBloom, NextSupportBloomIndex)
            || EscapeClaimBloomContainsIndex(&Node.AirBloom, NextSupportBloomIndex)
            || NextAirBloomIndex.is_some_and(|Index| {
                EscapeClaimBloomContainsIndex(&Node.WireBloom, Index)
                    || EscapeClaimBloomContainsIndex(&Node.SupportBloom, Index)
            })
    });
    if MustCheckExactClaims {
        let mut Cursor = Parent.clone();
        while let Some(Node) = Cursor {
            let ExistingSupport = (Node.Wire.0, Node.Wire.1 - 1, Node.Wire.2);
            if Node.Air == Some(Next)
                || ExistingSupport == Next
                || Node.Wire == NextSupport
                || Node.Air == Some(NextSupport)
                || NextAir.is_some_and(|Air| Air == Node.Wire || Air == ExistingSupport)
            {
                return None;
            }
            Cursor = Node.Parent.clone();
        }
    }
    let mut WireBloom = Parent.as_ref().map_or([0; 4], |Node| Node.WireBloom);
    let mut SupportBloom = Parent.as_ref().map_or([0; 4], |Node| Node.SupportBloom);
    let mut AirBloom = Parent.as_ref().map_or([0; 4], |Node| Node.AirBloom);
    EscapeClaimBloomInsertIndex(&mut WireBloom, NextBloomIndex);
    EscapeClaimBloomInsertIndex(&mut SupportBloom, NextSupportBloomIndex);
    if let Some(Index) = NextAirBloomIndex {
        EscapeClaimBloomInsertIndex(&mut AirBloom, Index);
    }
    Some(Arc::new(EscapeClaimNode {
        Wire: Next,
        Air: NextAir,
        WireBloom,
        SupportBloom,
        AirBloom,
        Parent,
    }))
}

#[derive(Clone, Copy)]
struct IndexedEscapeClaimNode {
    Wire: Position,
    Air: Option<Position>,
    WireBloom: [u64; 4],
    SupportBloom: [u64; 4],
    AirBloom: [u64; 4],
    Parent: Option<usize>,
}

fn ExtendIndexedEscapeClaims(
    Claims: &[IndexedEscapeClaimNode],
    Parent: Option<usize>,
    Current: Option<Position>,
    Next: Position,
) -> Option<IndexedEscapeClaimNode> {
    let NextSupport = (Next.0, Next.1 - 1, Next.2);
    let NextAir = Current.and_then(|CurrentValue| {
        if CurrentValue.1 == Next.1 {
            None
        } else {
            let Lower = if CurrentValue.1 < Next.1 {
                CurrentValue
            } else {
                Next
            };
            Some((Lower.0, Lower.1 + 1, Lower.2))
        }
    });
    if NextAir.is_some_and(|Air| Air == Next || Air == NextSupport) {
        return None;
    }
    let NextBloomIndex = EscapeClaimBloomIndex(Next);
    let NextSupportBloomIndex = EscapeClaimBloomIndex(NextSupport);
    let NextAirBloomIndex = NextAir.map(EscapeClaimBloomIndex);
    let MustCheckExactClaims = Parent.is_some_and(|Index| {
        let Node = &Claims[Index];
        EscapeClaimBloomContainsIndex(&Node.AirBloom, NextBloomIndex)
            || EscapeClaimBloomContainsIndex(&Node.SupportBloom, NextBloomIndex)
            || EscapeClaimBloomContainsIndex(&Node.WireBloom, NextSupportBloomIndex)
            || EscapeClaimBloomContainsIndex(&Node.AirBloom, NextSupportBloomIndex)
            || NextAirBloomIndex.is_some_and(|BloomIndex| {
                EscapeClaimBloomContainsIndex(&Node.WireBloom, BloomIndex)
                    || EscapeClaimBloomContainsIndex(&Node.SupportBloom, BloomIndex)
            })
    });
    if MustCheckExactClaims {
        let mut Cursor = Parent;
        while let Some(Index) = Cursor {
            let Node = &Claims[Index];
            let ExistingSupport = (Node.Wire.0, Node.Wire.1 - 1, Node.Wire.2);
            if Node.Air == Some(Next)
                || ExistingSupport == Next
                || Node.Wire == NextSupport
                || Node.Air == Some(NextSupport)
                || NextAir.is_some_and(|Air| Air == Node.Wire || Air == ExistingSupport)
            {
                return None;
            }
            Cursor = Node.Parent;
        }
    }
    let mut WireBloom = Parent.map_or([0; 4], |Index| Claims[Index].WireBloom);
    let mut SupportBloom = Parent.map_or([0; 4], |Index| Claims[Index].SupportBloom);
    let mut AirBloom = Parent.map_or([0; 4], |Index| Claims[Index].AirBloom);
    EscapeClaimBloomInsertIndex(&mut WireBloom, NextBloomIndex);
    EscapeClaimBloomInsertIndex(&mut SupportBloom, NextSupportBloomIndex);
    if let Some(BloomIndex) = NextAirBloomIndex {
        EscapeClaimBloomInsertIndex(&mut AirBloom, BloomIndex);
    }
    Some(IndexedEscapeClaimNode {
        Wire: Next,
        Air: NextAir,
        WireBloom,
        SupportBloom,
        AirBloom,
        Parent,
    })
}

pub(crate) type EscapeRequest = (
    String,
    Position,
    Vec<Position>,
    Vec<(Position, Direction)>,
    Vec<Position>,
    Vec<Position>,
    bool,
);
pub(crate) type EscapePathCandidate = (Position, Direction, Vec<Position>);
pub(crate) type EscapeRequestResult = (String, Vec<EscapePathCandidate>, usize, bool);
pub(crate) type LayeredEscapeMemberRequest = (
    String,
    Vec<(Position, Vec<Position>)>,
    Vec<EscapeRequest>,
    usize,
);
pub(crate) type LayeredEscapeMemberResult =
    (String, String, Vec<EscapeRequestResult>, usize, bool, bool);
pub(crate) type LayeredAccessEscapeGraphValue = (String, Vec<(Position, Vec<Position>)>);
pub(crate) type AccessRegionGraphRecipeValue = (
    String,
    (i32, i32, i32, i32, i32, i32),
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Direction>,
);
pub(crate) type AccessRegionGraphResultValue =
    (String, Vec<(Position, Vec<Position>)>, usize, usize, bool);
pub(crate) type LayeredAccessEscapeMemberValue = (
    String,
    Vec<i64>,
    usize,
    Vec<EscapeRequest>,
    Vec<(String, String, String)>,
    i32,
    usize,
);

fn BuildAccessRegionGraphFromRecipe(
    Recipe: &AccessRegionGraphRecipeValue,
    Deadline: &RuntimeDeadline,
) -> AccessRegionGraphResultValue {
    let (
        MemberId,
        (MinimumX, MaximumX, MinimumY, MaximumY, MinimumZ, MaximumZ),
        AllowedAccessValues,
        ActualBlockValues,
        ElectricalBlockValues,
        SolidBlockValues,
        TorchPoweredSupportValues,
        NeighborOffsets,
    ) = Recipe;
    let AllowedAccess = AllowedAccessValues.iter().copied().collect::<HashSet<_>>();
    let ActualBlocks = ActualBlockValues.iter().copied().collect::<HashSet<_>>();
    let ElectricalBlocks = ElectricalBlockValues
        .iter()
        .copied()
        .collect::<HashSet<_>>();
    let SolidBlocks = SolidBlockValues.iter().copied().collect::<HashSet<_>>();
    let TorchPoweredSupports = TorchPoweredSupportValues
        .iter()
        .copied()
        .collect::<HashSet<_>>();
    let mut StaticKeepOut = ElectricalBlocks
        .union(&SolidBlocks)
        .copied()
        .collect::<HashSet<_>>();
    for PositionValue in ElectricalBlocks.union(&SolidBlocks) {
        for (DeltaX, DeltaY, DeltaZ) in NeighborOffsets {
            StaticKeepOut.insert((
                PositionValue.0 + DeltaX,
                PositionValue.1 + DeltaY,
                PositionValue.2 + DeltaZ,
            ));
        }
    }
    let mut Nodes = Vec::new();
    let mut CheckedColumns = 0usize;
    for X in *MinimumX..=*MaximumX {
        for Z in *MinimumZ..=*MaximumZ {
            if CheckedColumns % 32 == 0 && Deadline.Check() {
                return (MemberId.clone(), Vec::new(), 0, 0, false);
            }
            CheckedColumns += 1;
            for Y in *MinimumY..=*MaximumY {
                let PositionValue = (X, Y, Z);
                let Support = (X, Y - 1, Z);
                if TorchPoweredSupports.contains(&Support) {
                    continue;
                }
                let IsLegal = if AllowedAccess.contains(&PositionValue) {
                    !ActualBlocks.contains(&PositionValue)
                } else {
                    !ActualBlocks.contains(&PositionValue)
                        && !ActualBlocks.contains(&Support)
                        && !StaticKeepOut.contains(&PositionValue)
                };
                if IsLegal {
                    Nodes.push(PositionValue);
                }
            }
        }
    }
    Nodes.sort_unstable();
    Nodes.dedup();
    let NodeSet = Nodes.iter().copied().collect::<HashSet<_>>();
    let mut DirectedEdgeCount = 0usize;
    let mut Adjacency = Vec::with_capacity(Nodes.len());
    for (NodeIndex, PositionValue) in Nodes.iter().copied().enumerate() {
        if NodeIndex % 256 == 0 && Deadline.Check() {
            return (MemberId.clone(), Vec::new(), 0, 0, false);
        }
        let mut Neighbors = Vec::with_capacity(NeighborOffsets.len());
        for (DeltaX, DeltaY, DeltaZ) in NeighborOffsets {
            let Neighbor = (
                PositionValue.0 + DeltaX,
                PositionValue.1 + DeltaY,
                PositionValue.2 + DeltaZ,
            );
            if !NodeSet.contains(&Neighbor) {
                continue;
            }
            if PositionValue.1 != Neighbor.1 {
                let (Lower, Upper) = if PositionValue.1 < Neighbor.1 {
                    (PositionValue, Neighbor)
                } else {
                    (Neighbor, PositionValue)
                };
                let Headroom = (Lower.0, Lower.1 + 1, Lower.2);
                let UpperSupport = (Upper.0, Upper.1 - 1, Upper.2);
                if SolidBlocks.contains(&Headroom)
                    || ActualBlocks.contains(&Headroom)
                    || (ActualBlocks.contains(&UpperSupport)
                        && !SolidBlocks.contains(&UpperSupport))
                {
                    continue;
                }
            }
            Neighbors.push(Neighbor);
        }
        Neighbors.sort_unstable();
        Neighbors.dedup();
        DirectedEdgeCount = DirectedEdgeCount.saturating_add(Neighbors.len());
        Adjacency.push((PositionValue, Neighbors));
    }
    (
        MemberId.clone(),
        Adjacency,
        Nodes.len(),
        DirectedEdgeCount / 2,
        true,
    )
}

pub(crate) fn BuildAccessRegionGraphCatalogWithDeadline(
    Recipes: Vec<AccessRegionGraphRecipeValue>,
    Deadline: RuntimeDeadline,
) -> (Vec<AccessRegionGraphResultValue>, bool) {
    let Results = RoutingThreadPool().install(|| {
        Recipes
            .par_iter()
            .map(|Recipe| BuildAccessRegionGraphFromRecipe(Recipe, &Deadline))
            .collect::<Vec<_>>()
    });
    let Complete = Results.iter().all(|Value| Value.4) && !Deadline.WasExceeded();
    (Results, Complete)
}
pub(crate) type LayeredAccessGuideSignalValue = (
    String,
    Vec<String>,
    usize,
    Vec<(i32, i32)>,
    Option<String>,
    Option<usize>,
);
pub(crate) type LayeredAccessBaseClaimValue = (
    String,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
);
pub(crate) type LayeredAccessGuideControlsValue = (
    Vec<i32>,
    i32,
    i32,
    usize,
    usize,
    usize,
    usize,
    usize,
    Vec<Position>,
    Vec<LayeredAccessGuideSignalValue>,
    Vec<LayeredAccessBaseClaimValue>,
    Vec<(String, Vec<Vec<Position>>)>,
);
pub(crate) type LayeredAccessGuideMemberValue = (
    String,
    Vec<i64>,
    usize,
    Vec<EscapeRequest>,
    Vec<(String, String, String)>,
    i32,
    usize,
    LayeredAccessGuideControlsValue,
);
pub(crate) type SelectedLayeredGuideValue = (
    String,
    String,
    Vec<(String, String)>,
    i32,
    String,
    i32,
    Vec<Position>,
    Vec<Vec<Position>>,
    Vec<Position>,
    Vec<Vec<Position>>,
    Vec<(Position, String)>,
);
type PreparedLayeredAccessGuideDomain = (
    BTreeMap<String, Vec<AssignmentCandidate>>,
    usize,
    HashMap<String, SelectedLayeredGuideValue>,
    Arc<Vec<Vec<(usize, usize)>>>,
);
pub(crate) type LayeredAccessGuideSelectionResult = (
    TemplateRoutingAssignmentResult,
    Option<LayeredEscapeMemberResult>,
    Vec<SelectedLayeredGuideValue>,
    usize,
);
pub(crate) type LayeredAccessEscapeSelectionResult = (
    TemplateRoutingAssignmentResult,
    Option<LayeredEscapeMemberResult>,
    usize,
);

fn BuildOneDerivedEscapeRequest(
    Adjacency: &HashMap<Position, Vec<Position>>,
    IndexedGraph: &IndexedEscapeGraph,
    Workspace: &mut IndexedEscapeWorkspace,
    Request: EscapeRequest,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    MaximumY: Option<i32>,
    mut SharedExpansionLease: Option<&mut SharedEscapeExpansionLease<'_>>,
) -> (EscapeRequestResult, bool, bool) {
    let (
        RequestId,
        Start,
        OrderedIngresses,
        RejectedStates,
        FixedPrefix,
        AllowedNodes,
        FirstPathPerIngress,
    ) = Request;
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE_VERBOSE").is_some() {
        eprintln!(
            "native layered escape request={} start={:?} ingresses={:?} first_path_per_ingress={}",
            RequestId,
            Start,
            OrderedIngresses,
            FirstPathPerIngress,
        );
    }
    let Ingresses: HashSet<Position> = OrderedIngresses.iter().copied().collect();
    let RejectedStateSet: HashSet<(Position, Direction)> = RejectedStates.into_iter().collect();
    // An empty request mask denotes the member's complete immutable graph.
    // Layered access catalogs otherwise repeated the same large position
    // vector and HashSet construction for every terminal prefix.
    let AllowsAllNodes = AllowedNodes.is_empty();
    let AllowedNodeSet: HashSet<Position> = AllowedNodes.into_iter().collect();
    let AllowsNode = |PositionValue: &Position| {
        MaximumY.is_none_or(|Value| PositionValue.1 <= Value)
            && (AllowsAllNodes || AllowedNodeSet.contains(PositionValue))
    };
    let InitialDirection: Direction = (0, 0, 0);
    let mut RemainingIngressStates: HashSet<(Position, Direction)> = Ingresses
        .iter()
        .flat_map(|Ingress| {
            let mut States: Vec<(Position, Direction)> = Adjacency
                .get(Ingress)
                .into_iter()
                .flatten()
                .filter(|Neighbor| AllowsNode(Neighbor))
                .map(|Neighbor| {
                    (
                        *Ingress,
                        (
                            Ingress.0 - Neighbor.0,
                            Ingress.1 - Neighbor.1,
                            Ingress.2 - Neighbor.2,
                        ),
                    )
                })
                .collect();
            if *Ingress == Start {
                States.push((*Ingress, InitialDirection));
            }
            States
        })
        .filter(|State| !RejectedStateSet.contains(State))
        .collect();
    let mut RemainingIngresses = Ingresses.clone();
    let mut Candidates = Vec::new();
    if !Adjacency.contains_key(&Start)
        || !AllowsNode(&Start)
        || Ingresses
            .iter()
            .any(|Ingress| !Adjacency.contains_key(Ingress) || !AllowsNode(Ingress))
    {
        return ((RequestId, Candidates, 0, true), false, false);
    }

    let mut PrefixClaims: Option<Arc<EscapeClaimNode>> = None;
    let mut PriorPrefixPosition = None;
    for PositionValue in &FixedPrefix {
        PrefixClaims = ExtendEscapeClaims(PrefixClaims, PriorPrefixPosition, *PositionValue);
        if PrefixClaims.is_none() {
            return ((RequestId, Candidates, 0, true), false, false);
        }
        PriorPrefixPosition = Some(*PositionValue);
    }
    if PriorPrefixPosition != Some(Start) {
        PrefixClaims = ExtendEscapeClaims(PrefixClaims, PriorPrefixPosition, Start);
        if PrefixClaims.is_none() {
            return ((RequestId, Candidates, 0, true), false, false);
        }
    }
    let StartState = (Start, InitialDirection);
    let StartClaims = PrefixClaims.expect("escape start claim");
    let mut ExpansionCount = 0usize;
    let mut WorkCapExceeded = false;
    let mut DeadlineExceeded = false;

    if !FirstPathPerIngress {
        let AllowedNodeIndices = if AllowsAllNodes && MaximumY.is_none() {
            None
        } else {
            let mut Values = vec![false; IndexedGraph.Positions.len()];
            if AllowsAllNodes {
                for (Index, PositionValue) in IndexedGraph.Positions.iter().enumerate() {
                    Values[Index] = AllowsNode(PositionValue);
                }
            } else {
                for PositionValue in &AllowedNodeSet {
                    if AllowsNode(PositionValue) {
                        if let Some(Index) = IndexedGraph.PositionIndices.get(PositionValue) {
                            Values[*Index] = true;
                        }
                    }
                }
            }
            Some(Values)
        };
        let StartNodeIndex = *IndexedGraph
            .PositionIndices
            .get(&Start)
            .expect("validated escape start is indexed");
        let StartStateIndex = IndexedGraph.StateIndex(StartNodeIndex, InitialDirection);
        let mut IndexedPrefixClaims = Vec::with_capacity(FixedPrefix.len() + 1);
        let mut IndexedPrefixParent = None;
        let mut IndexedPriorPrefixPosition = None;
        for PositionValue in &FixedPrefix {
            let Claim = ExtendIndexedEscapeClaims(
                &IndexedPrefixClaims,
                IndexedPrefixParent,
                IndexedPriorPrefixPosition,
                *PositionValue,
            )
            .expect("Arc-validated escape prefix must remain self-legal");
            IndexedPrefixClaims.push(Claim);
            IndexedPrefixParent = Some(IndexedPrefixClaims.len() - 1);
            IndexedPriorPrefixPosition = Some(*PositionValue);
        }
        if IndexedPriorPrefixPosition != Some(Start) {
            let Claim = ExtendIndexedEscapeClaims(
                &IndexedPrefixClaims,
                IndexedPrefixParent,
                IndexedPriorPrefixPosition,
                Start,
            )
            .expect("Arc-validated escape start must remain self-legal");
            IndexedPrefixClaims.push(Claim);
            IndexedPrefixParent = Some(IndexedPrefixClaims.len() - 1);
        }
        let IndexedStartClaimRecord = IndexedPrefixParent.expect("escape start claim");
        Workspace.BeginSearch();
        Workspace
            .ClaimRecords
            .extend_from_slice(&IndexedPrefixClaims);
        Workspace.SetState(StartStateIndex, 0, usize::MAX, IndexedStartClaimRecord);
        let mut Frontier = BinaryHeap::from([Reverse((
            0usize,
            Start,
            InitialDirection,
            StartNodeIndex,
            StartStateIndex,
        ))]);
        while let Some(Reverse((
            Cost,
            Current,
            PriorDirection,
            CurrentNodeIndex,
            CurrentStateIndex,
        ))) = Frontier.pop()
        {
            if Workspace.Cost(CurrentStateIndex) != Cost {
                continue;
            }
            if SharedExpansionLease.is_none() && ExpansionCount >= MaximumExpansionCount {
                WorkCapExceeded = true;
                break;
            }
            if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                DeadlineExceeded = true;
                break;
            }
            if !TryCountEscapeExpansion(
                &mut ExpansionCount,
                MaximumExpansionCount,
                SharedExpansionLease.as_deref_mut(),
            ) {
                WorkCapExceeded = true;
                break;
            }
            let CurrentState = (Current, PriorDirection);
            if RemainingIngressStates.remove(&CurrentState) {
                let mut ReversePath = Vec::new();
                let mut Cursor = CurrentStateIndex;
                loop {
                    ReversePath.push(IndexedGraph.StatePosition(Cursor));
                    let ParentState = Workspace.ParentStates[Cursor];
                    if ParentState == usize::MAX {
                        break;
                    }
                    Cursor = ParentState;
                }
                ReversePath.reverse();
                Candidates.push((Current, PriorDirection, ReversePath));
                if RemainingIngressStates.is_empty() {
                    break;
                }
            }
            for NextNodeIndex in &IndexedGraph.NeighborIndices[CurrentNodeIndex] {
                if AllowedNodeIndices
                    .as_ref()
                    .is_some_and(|Values| !Values[*NextNodeIndex])
                {
                    continue;
                }
                let Next = IndexedGraph.Positions[*NextNodeIndex];
                let DirectionValue = (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                let TurnCost =
                    if PriorDirection != InitialDirection && DirectionValue != PriorDirection {
                        BendPenalty
                    } else {
                        0
                    };
                let NextCost = Cost.saturating_add(1).saturating_add(TurnCost);
                let NextStateIndex = IndexedGraph.StateIndex(*NextNodeIndex, DirectionValue);
                if NextCost >= Workspace.Cost(NextStateIndex) {
                    continue;
                }
                let CurrentClaimRecord = Workspace.ClaimRecordByState[CurrentStateIndex];
                let Some(NextClaims) = ExtendIndexedEscapeClaims(
                    &Workspace.ClaimRecords,
                    Some(CurrentClaimRecord),
                    Some(Current),
                    Next,
                ) else {
                    continue;
                };
                Workspace.ClaimRecords.push(NextClaims);
                let NextClaimRecord = Workspace.ClaimRecords.len() - 1;
                Workspace.SetState(NextStateIndex, NextCost, CurrentStateIndex, NextClaimRecord);
                Frontier.push(Reverse((
                    NextCost,
                    Next,
                    DirectionValue,
                    *NextNodeIndex,
                    NextStateIndex,
                )));
            }
        }
        return (
            (
                RequestId,
                Candidates,
                ExpansionCount,
                !WorkCapExceeded && !DeadlineExceeded,
            ),
            WorkCapExceeded,
            DeadlineExceeded,
        );
    }

    if FirstPathPerIngress {
        // Large face-restricted access graphs need target-directed search:
        // flooding their full directional state space for every terminal is
        // representation work when the caller consumes one path per ingress.
        // Fixed-band callers request the complete shared traversal below so
        // their exact directional-state bound remains authoritative.
        let AllowedNodeIndices = if AllowsAllNodes && MaximumY.is_none() {
            None
        } else {
            let mut Values = vec![false; IndexedGraph.Positions.len()];
            if AllowsAllNodes {
                for (Index, PositionValue) in IndexedGraph.Positions.iter().enumerate() {
                    Values[Index] = AllowsNode(PositionValue);
                }
            } else {
                for PositionValue in &AllowedNodeSet {
                    if AllowsNode(PositionValue) {
                        if let Some(Index) = IndexedGraph.PositionIndices.get(PositionValue) {
                            Values[*Index] = true;
                        }
                    }
                }
            }
            Some(Values)
        };
        let StartNodeIndex = *IndexedGraph
            .PositionIndices
            .get(&Start)
            .expect("validated escape start is indexed");
        let StartStateIndex = IndexedGraph.StateIndex(StartNodeIndex, InitialDirection);
        let mut IndexedPrefixClaims = Vec::with_capacity(FixedPrefix.len() + 1);
        let mut IndexedPrefixParent = None;
        let mut IndexedPriorPrefixPosition = None;
        for PositionValue in &FixedPrefix {
            let Claim = ExtendIndexedEscapeClaims(
                &IndexedPrefixClaims,
                IndexedPrefixParent,
                IndexedPriorPrefixPosition,
                *PositionValue,
            )
            .expect("Arc-validated escape prefix must remain self-legal");
            IndexedPrefixClaims.push(Claim);
            IndexedPrefixParent = Some(IndexedPrefixClaims.len() - 1);
            IndexedPriorPrefixPosition = Some(*PositionValue);
        }
        if IndexedPriorPrefixPosition != Some(Start) {
            let Claim = ExtendIndexedEscapeClaims(
                &IndexedPrefixClaims,
                IndexedPrefixParent,
                IndexedPriorPrefixPosition,
                Start,
            )
            .expect("Arc-validated escape start must remain self-legal");
            IndexedPrefixClaims.push(Claim);
            IndexedPrefixParent = Some(IndexedPrefixClaims.len() - 1);
        }
        let IndexedStartClaimRecord = IndexedPrefixParent.expect("escape start claim");
        for Ingress in OrderedIngresses {
            Workspace.BeginSearch();
            Workspace
                .ClaimRecords
                .extend_from_slice(&IndexedPrefixClaims);
            Workspace.SetState(StartStateIndex, 0, usize::MAX, IndexedStartClaimRecord);
            let mut OriginalFrontier = BinaryHeap::from([Reverse((
                0usize,
                0usize,
                Start,
                InitialDirection,
                StartNodeIndex,
                StartStateIndex,
            ))]);
            let mut OriginalCandidates = Vec::new();
            let mut RemainingTargetStates = RemainingIngressStates.clone();
            while let Some(Reverse((
                _EstimatedCost,
                Cost,
                Current,
                PriorDirection,
                CurrentNodeIndex,
                CurrentStateIndex,
            ))) = OriginalFrontier.pop()
            {
                if Workspace.Cost(CurrentStateIndex) != Cost {
                    continue;
                }
                if SharedExpansionLease.is_none() && ExpansionCount >= MaximumExpansionCount {
                    WorkCapExceeded = true;
                    break;
                }
                if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    DeadlineExceeded = true;
                    break;
                }
                if !TryCountEscapeExpansion(
                    &mut ExpansionCount,
                    MaximumExpansionCount,
                    SharedExpansionLease.as_deref_mut(),
                ) {
                    WorkCapExceeded = true;
                    break;
                }
                if Current == Ingress
                    && !RejectedStateSet.contains(&(Current, PriorDirection))
                    && RemainingTargetStates.remove(&(Current, PriorDirection))
                {
                    let mut ReversePath = Vec::new();
                    let mut Cursor = CurrentStateIndex;
                    loop {
                        ReversePath.push(IndexedGraph.StatePosition(Cursor));
                        let ParentState = Workspace.ParentStates[Cursor];
                        if ParentState == usize::MAX {
                            break;
                        }
                        Cursor = ParentState;
                    }
                    ReversePath.reverse();
                    OriginalCandidates.push((Current, PriorDirection, ReversePath));
                    break;
                }
                for NextNodeIndex in &IndexedGraph.NeighborIndices[CurrentNodeIndex] {
                    if AllowedNodeIndices
                        .as_ref()
                        .is_some_and(|Values| !Values[*NextNodeIndex])
                    {
                        continue;
                    }
                    let Next = IndexedGraph.Positions[*NextNodeIndex];
                    let DirectionValue =
                        (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                    let TurnCost =
                        if PriorDirection != InitialDirection && DirectionValue != PriorDirection {
                            BendPenalty
                        } else {
                            0
                        };
                    let NextCost = Cost.saturating_add(1).saturating_add(TurnCost);
                    let NextStateIndex = IndexedGraph.StateIndex(*NextNodeIndex, DirectionValue);
                    if NextCost >= Workspace.Cost(NextStateIndex) {
                        continue;
                    }
                    let CurrentClaimRecord = Workspace.ClaimRecordByState[CurrentStateIndex];
                    let Some(NextClaims) = ExtendIndexedEscapeClaims(
                        &Workspace.ClaimRecords,
                        Some(CurrentClaimRecord),
                        Some(Current),
                        Next,
                    ) else {
                        continue;
                    };
                    Workspace.ClaimRecords.push(NextClaims);
                    let NextClaimRecord = Workspace.ClaimRecords.len() - 1;
                    Workspace.SetState(
                        NextStateIndex,
                        NextCost,
                        CurrentStateIndex,
                        NextClaimRecord,
                    );
                    let Heuristic =
                        LayeredEscapeLowerBound(Next, DirectionValue, Ingress, BendPenalty);
                    OriginalFrontier.push(Reverse((
                        NextCost.saturating_add(Heuristic),
                        NextCost,
                        Next,
                        DirectionValue,
                        *NextNodeIndex,
                        NextStateIndex,
                    )));
                }
            }
            let OriginalIsPowerCertified =
                OriginalCandidates
                    .iter()
                    .any(|(_Current, _Direction, Path)| {
                        let WirePath = FixedPrefix
                            .iter()
                            .copied()
                            .chain(Path.iter().copied().skip(1))
                            .collect::<Vec<_>>();
                        ExactLayeredAccessPathCanCarryPower(true, &WirePath)
                            && ExactLayeredAccessPathCanCarryPower(false, &WirePath)
                    });
            let OriginalAlternativeBlockedNode = OriginalCandidates
                .first()
                .and_then(|(_Current, _Direction, Path)| {
                        Path.iter()
                            .copied()
                            .skip(1)
                            .take(Path.len().saturating_sub(2))
                            .nth(Path.len().saturating_sub(2) / 2)
                    });
            Candidates.extend(OriginalCandidates);
            if WorkCapExceeded || DeadlineExceeded {
                break;
            }
            let mut AlternativeBlockedNodes = BTreeSet::<Position>::new();
            AlternativeBlockedNodes.extend(OriginalAlternativeBlockedNode);
            let AlternativeCount = if OriginalIsPowerCertified { 1 } else { 3 };
            let Visits = &mut Workspace.PoweredVisits;
            for AlternativeIndex in 0..AlternativeCount {
                let StartPoweredState =
                    PoweredEscapeStateIndex(StartNodeIndex, InitialDirection, 15);
                Visits.clear();
                Visits.insert(
                    StartPoweredState,
                    PoweredEscapeVisit::New(0, usize::MAX, IndexedStartClaimRecord),
                );
                let mut ClaimRecords = IndexedPrefixClaims.clone();
                let mut Frontier = BinaryHeap::from([Reverse((
                    0usize,
                    0usize,
                    Start,
                    InitialDirection,
                    0u8,
                    StartNodeIndex,
                    15u8,
                ))]);
                let mut PoweredCandidateFound = false;
                while let Some(Reverse((
                    _EstimatedCost,
                    Cost,
                    Current,
                    PriorDirection,
                    _PowerTieBreak,
                    CurrentNodeIndex,
                    PowerRemaining,
                ))) = Frontier.pop()
                {
                    let CurrentState =
                        PoweredEscapeStateIndex(CurrentNodeIndex, PriorDirection, PowerRemaining);
                    if Visits.get(&CurrentState).map(|Visit| Visit.CostValue()) != Some(Cost) {
                        continue;
                    }
                    if SharedExpansionLease.is_none() && ExpansionCount >= MaximumExpansionCount {
                        WorkCapExceeded = true;
                        break;
                    }
                    if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                        DeadlineExceeded = true;
                        break;
                    }
                    if !TryCountEscapeExpansion(
                        &mut ExpansionCount,
                        MaximumExpansionCount,
                        SharedExpansionLease.as_deref_mut(),
                    ) {
                        WorkCapExceeded = true;
                        break;
                    }
                    if Current == Ingress && !RejectedStateSet.contains(&(Current, PriorDirection))
                    {
                        let mut ReversePath = Vec::new();
                        let mut Cursor = CurrentState;
                        loop {
                            ReversePath
                                .push(IndexedGraph.Positions[PoweredEscapeStateNodeIndex(Cursor)]);
                            let ParentState = Visits[&Cursor].ParentStateValue();
                            if ParentState == usize::MAX {
                                break;
                            }
                            Cursor = ParentState;
                        }
                        ReversePath.reverse();
                        AlternativeBlockedNodes.extend(
                            ReversePath
                                .iter()
                                .copied()
                                .skip(1)
                                .take(ReversePath.len().saturating_sub(2))
                                .nth(ReversePath.len().saturating_sub(2) / 2),
                        );
                        Candidates.push((Current, PriorDirection, ReversePath));
                        PoweredCandidateFound = true;
                        break;
                    }
                    for NextNodeIndex in &IndexedGraph.NeighborIndices[CurrentNodeIndex] {
                        if AllowedNodeIndices
                            .as_ref()
                            .is_some_and(|Values| !Values[*NextNodeIndex])
                        {
                            continue;
                        }
                        let Next = IndexedGraph.Positions[*NextNodeIndex];
                        if (OriginalIsPowerCertified || AlternativeIndex > 0)
                            && AlternativeBlockedNodes.contains(&Next)
                        {
                            continue;
                        }
                        let DirectionValue =
                            (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                        let TurnCost = if PriorDirection != InitialDirection
                            && DirectionValue != PriorDirection
                        {
                            BendPenalty
                        } else {
                            0
                        };
                        let NextPower = if PriorDirection != InitialDirection
                            && PriorDirection == DirectionValue
                            && DirectionValue.1 == 0
                            && DirectionValue.0.abs() + DirectionValue.2.abs() == 1
                        {
                            15
                        } else {
                            PowerRemaining.saturating_sub(1)
                        };
                        if NextPower == 0 {
                            continue;
                        }
                        let NextCost = Cost.saturating_add(1).saturating_add(TurnCost);
                        let NextState =
                            PoweredEscapeStateIndex(*NextNodeIndex, DirectionValue, NextPower);
                        if NextCost
                            >= Visits
                                .get(&NextState)
                                .map_or(usize::MAX, |Visit| Visit.CostValue())
                        {
                            continue;
                        }
                        let CurrentClaimRecord = Visits[&CurrentState].ClaimRecordValue();
                        let Some(NextClaims) = ExtendIndexedEscapeClaims(
                            &ClaimRecords,
                            Some(CurrentClaimRecord),
                            Some(Current),
                            Next,
                        ) else {
                            continue;
                        };
                        ClaimRecords.push(NextClaims);
                        let NextClaimRecord = ClaimRecords.len() - 1;
                        Visits.insert(
                            NextState,
                            PoweredEscapeVisit::New(NextCost, CurrentState, NextClaimRecord),
                        );
                        let Heuristic =
                            LayeredEscapeLowerBound(Next, DirectionValue, Ingress, BendPenalty);
                        Frontier.push(Reverse((
                            NextCost.saturating_add(Heuristic),
                            NextCost,
                            Next,
                            DirectionValue,
                            15u8.saturating_sub(NextPower),
                            *NextNodeIndex,
                            NextPower,
                        )));
                    }
                }
                if WorkCapExceeded
                    || DeadlineExceeded
                    || !PoweredCandidateFound
                    || AlternativeBlockedNodes.is_empty()
                {
                    break;
                }
            }
            if WorkCapExceeded || DeadlineExceeded {
                break;
            }
        }
        return (
            (
                RequestId,
                Candidates,
                ExpansionCount,
                !WorkCapExceeded && !DeadlineExceeded,
            ),
            WorkCapExceeded,
            DeadlineExceeded,
        );
    }

    let mut Frontier = BinaryHeap::from([Reverse((0usize, Start, InitialDirection))]);
    let mut BestCost = HashMap::from([(StartState, 0usize)]);
    let mut Parent: HashMap<(Position, Direction), Option<(Position, Direction)>> =
        HashMap::from([(StartState, None)]);
    let mut ClaimsByState: HashMap<(Position, Direction), Arc<EscapeClaimNode>> =
        HashMap::from([(StartState, StartClaims)]);

    while let Some(Reverse((Cost, Current, PriorDirection))) = Frontier.pop() {
        let CurrentState = (Current, PriorDirection);
        if BestCost.get(&CurrentState).copied() != Some(Cost) {
            continue;
        }
        if SharedExpansionLease.is_none() && ExpansionCount >= MaximumExpansionCount {
            WorkCapExceeded = true;
            break;
        }
        if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            DeadlineExceeded = true;
            break;
        }
        if !TryCountEscapeExpansion(
            &mut ExpansionCount,
            MaximumExpansionCount,
            SharedExpansionLease.as_deref_mut(),
        ) {
            WorkCapExceeded = true;
            break;
        }

        if Ingresses.contains(&Current)
            && !RejectedStateSet.contains(&CurrentState)
            && (!FirstPathPerIngress || RemainingIngresses.contains(&Current))
        {
            let mut ReversePath = Vec::new();
            let mut Cursor = Some(CurrentState);
            while let Some(State) = Cursor {
                ReversePath.push(State.0);
                Cursor = Parent.get(&State).copied().flatten();
            }
            ReversePath.reverse();
            Candidates.push((Current, PriorDirection, ReversePath));
            RemainingIngressStates.remove(&CurrentState);
            RemainingIngresses.remove(&Current);
            if (FirstPathPerIngress && RemainingIngresses.is_empty())
                || (!FirstPathPerIngress && RemainingIngressStates.is_empty())
            {
                break;
            }
        }

        if let Some(Neighbors) = Adjacency.get(&Current) {
            for Next in Neighbors {
                if !AllowsNode(Next) {
                    continue;
                }
                let DirectionValue = (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                let TurnCost =
                    if PriorDirection != InitialDirection && DirectionValue != PriorDirection {
                        BendPenalty
                    } else {
                        0
                    };
                let NextCost = Cost.saturating_add(1).saturating_add(TurnCost);
                let NextState = (*Next, DirectionValue);
                if NextCost >= BestCost.get(&NextState).copied().unwrap_or(usize::MAX) {
                    continue;
                }
                let CurrentClaims = ClaimsByState
                    .get(&CurrentState)
                    .expect("settled escape state claims");
                let Some(NextClaims) =
                    ExtendEscapeClaims(Some(CurrentClaims.clone()), Some(Current), *Next)
                else {
                    continue;
                };
                BestCost.insert(NextState, NextCost);
                Parent.insert(NextState, Some(CurrentState));
                ClaimsByState.insert(NextState, NextClaims);
                Frontier.push(Reverse((NextCost, *Next, DirectionValue)));
            }
        }
    }

    (
        (
            RequestId,
            Candidates,
            ExpansionCount,
            !WorkCapExceeded && !DeadlineExceeded,
        ),
        WorkCapExceeded,
        DeadlineExceeded,
    )
}

/// Enumerate the least-cost path to every settled ingress direction-state.
///
/// Python retains exact redstone-claim validation.  Returning every settled
/// ingress state lets it reject a self-conflicting first path and consider
/// the same later direction states as the original bounded Python oracle.
pub(crate) fn BuildDerivedEscapeStatePathsWithDeadline(
    AdjacencyValues: Vec<(Position, Vec<Position>)>,
    Requests: Vec<EscapeRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> (String, Vec<EscapeRequestResult>, usize, bool, bool) {
    BuildDerivedEscapeStatePathsWithMaximumYAndDeadline(
        AdjacencyValues,
        Requests,
        BendPenalty,
        MaximumExpansionCount,
        Deadline,
        None,
    )
}

struct PreparedEscapeTraversalGraph {
    Adjacency: HashMap<Position, Vec<Position>>,
    IndexedGraph: IndexedEscapeGraph,
    IndexedStateCount: usize,
    CompleteRequestUpperBound: usize,
}

impl PreparedEscapeTraversalGraph {
    fn New(AdjacencyValues: Vec<(Position, Vec<Position>)>) -> Self {
        let Adjacency: HashMap<Position, Vec<Position>> = AdjacencyValues
            .into_iter()
            .map(|(PositionValue, mut Neighbors)| {
                Neighbors.sort_unstable();
                Neighbors.dedup();
                (PositionValue, Neighbors)
            })
            .collect();
        let IndexedGraph = IndexedEscapeGraph::New(&Adjacency);
        let IndexedStateCount = IndexedGraph
            .Positions
            .len()
            .saturating_mul(ESCAPE_DIRECTION_STATE_COUNT);
        let CompleteRequestUpperBound = 1usize.saturating_add(
            Adjacency
                .values()
                .map(Vec::len)
                .fold(0usize, usize::saturating_add),
        );
        Self {
            Adjacency,
            IndexedGraph,
            IndexedStateCount,
            CompleteRequestUpperBound,
        }
    }
}

fn BuildDerivedEscapeStatePathsWithMaximumYAndDeadline(
    AdjacencyValues: Vec<(Position, Vec<Position>)>,
    Requests: Vec<EscapeRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    MaximumY: Option<i32>,
) -> (String, Vec<EscapeRequestResult>, usize, bool, bool) {
    let Prepared = PreparedEscapeTraversalGraph::New(AdjacencyValues);
    BuildDerivedEscapeStatePathsWithPreparedGraphAndDeadline(
        &Prepared,
        Requests,
        BendPenalty,
        MaximumExpansionCount,
        Deadline,
        MaximumY,
        true,
    )
}

fn BuildDerivedEscapeStatePathsWithPreparedGraphAndDeadline(
    Prepared: &PreparedEscapeTraversalGraph,
    Requests: Vec<EscapeRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    MaximumY: Option<i32>,
    AllowParallelRequests: bool,
) -> (String, Vec<EscapeRequestResult>, usize, bool, bool) {
    let Adjacency = &Prepared.Adjacency;
    let IndexedGraph = &Prepared.IndexedGraph;
    let IndexedStateCount = Prepared.IndexedStateCount;
    // A directional state is either the initial start state or one directed
    // graph edge.  First-path requests additionally certify up to three
    // powered alternatives per ingress, whose state includes one of fifteen
    // remaining-power values.  Derive each request's complete bound from its
    // immutable input before deciding whether independent requests can run
    // in parallel.
    let CompleteRequestUpperBound = Prepared.CompleteRequestUpperBound;
    let PoweredRequestUpperBound = 1usize.saturating_add(
        CompleteRequestUpperBound
            .saturating_sub(1)
            .saturating_mul(15),
    );
    let RequestUpperBounds = Requests
        .iter()
        .map(|Request| {
            if Request.6 {
                Request.2.len().saturating_mul(
                    CompleteRequestUpperBound
                        .saturating_add(PoweredRequestUpperBound.saturating_mul(3)),
                )
            } else {
                CompleteRequestUpperBound
            }
        })
        .collect::<Vec<_>>();

    if AllowParallelRequests
        && Requests
            .iter()
            .any(|Request| Request.6 && Request.2.len() > 1)
    {
        // First-path requests search every ingress independently and append
        // their candidates in declared ingress order. Split only that exact
        // independence boundary into deterministic work units, then shard
        // those units so each worker reuses one indexed workspace. Exact
        // chunk leases amortize shared accounting while preserving the
        // unchanged finite cap and exact final expansion count.
        let mut WorkUnits = Vec::<(usize, usize, EscapeRequest)>::new();
        let mut ExpectedUnitCounts = vec![0usize; Requests.len()];
        for (RequestIndex, Request) in Requests.iter().enumerate() {
            if Request.6 && Request.2.len() > 1 {
                for Ingress in &Request.2 {
                    let mut UnitRequest = Request.clone();
                    UnitRequest.2 = vec![*Ingress];
                    WorkUnits.push((WorkUnits.len(), RequestIndex, UnitRequest));
                    ExpectedUnitCounts[RequestIndex] += 1;
                }
            } else {
                WorkUnits.push((WorkUnits.len(), RequestIndex, Request.clone()));
                ExpectedUnitCounts[RequestIndex] = 1;
            }
        }

        let WorkerCount = RoutingThreadPool()
            .current_num_threads()
            .clamp(1, MAXIMUM_MEMBER_ESCAPE_SHARD_COUNT)
            .min(WorkUnits.len().max(1));
        let mut WorkShards = (0..WorkerCount)
            .map(|_| Vec::<(usize, usize, EscapeRequest)>::new())
            .collect::<Vec<_>>();
        for WorkUnit in WorkUnits {
            // Request construction groups related ingress variants next to
            // each other.  A plain modulo schedule therefore maps the same
            // expensive variant ordinal to one worker for thousands of
            // terminals.  Fold higher ordinal bits into the shard index so
            // the immutable work units remain deterministic while repeated
            // variant patterns are distributed across the existing pool.
            let MixedUnitIndex = WorkUnit.0 ^ (WorkUnit.0 >> 3) ^ (WorkUnit.0 >> 6);
            let ShardIndex = MixedUnitIndex % WorkerCount;
            WorkShards[ShardIndex].push(WorkUnit);
        }
        let SharedExpansionBudget = SharedEscapeExpansionBudget::New(MaximumExpansionCount);
        let ShardOutcomes = RoutingThreadPool().install(|| {
            WorkShards
                .into_par_iter()
                .map(|UnitChunk| {
                    let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
                    let mut ExpansionLease =
                        SharedEscapeExpansionLease::New(&SharedExpansionBudget);
                    let mut Outcomes = Vec::with_capacity(UnitChunk.len());
                    for (UnitIndex, RequestIndex, Request) in UnitChunk {
                        let (Result, UnitWorkCap, UnitDeadline) = BuildOneDerivedEscapeRequest(
                            Adjacency,
                            IndexedGraph,
                            &mut Workspace,
                            Request,
                            BendPenalty,
                            MaximumExpansionCount,
                            Deadline.clone(),
                            MaximumY,
                            Some(&mut ExpansionLease),
                        );
                        Outcomes.push((UnitIndex, RequestIndex, Result, UnitWorkCap, UnitDeadline));
                        if UnitWorkCap || UnitDeadline {
                            break;
                        }
                    }
                    Outcomes
                })
                .collect::<Vec<_>>()
        });
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            eprintln!(
                "native escape shard expansions: {:?}",
                ShardOutcomes
                    .iter()
                    .map(|Outcomes| Outcomes.iter().map(|Value| Value.2 .2).sum::<usize>())
                    .collect::<Vec<_>>()
            );
            let mut UnitExpansions = ShardOutcomes
                .iter()
                .flatten()
                .map(|Value| (Value.2 .2, Value.0, Value.2 .0.clone()))
                .collect::<Vec<_>>();
            UnitExpansions.sort_unstable_by(|First, Second| Second.cmp(First));
            eprintln!(
                "native escape largest units: {:?}",
                UnitExpansions.into_iter().take(24).collect::<Vec<_>>()
            );
        }
        let mut UnitOutcomes = ShardOutcomes.into_iter().flatten().collect::<Vec<_>>();
        UnitOutcomes.sort_unstable_by_key(|Value| Value.0);
        let ExpansionCount = SharedExpansionBudget.ExpansionCount();
        let WorkCapExceeded = UnitOutcomes
            .iter()
            .any(|(_UnitIndex, _RequestIndex, _Result, WorkCap, _Deadline)| *WorkCap);
        let DeadlineExceeded = UnitOutcomes
            .iter()
            .any(|(_UnitIndex, _RequestIndex, _Result, _WorkCap, DeadlineValue)| *DeadlineValue);

        let mut CompletedUnitCounts = vec![0usize; Requests.len()];
        let mut Results = Requests
            .iter()
            .map(|Request| (Request.0.clone(), Vec::new(), 0usize, true))
            .collect::<Vec<EscapeRequestResult>>();
        for (_UnitIndex, RequestIndex, UnitResult, _UnitWorkCap, _UnitDeadline) in UnitOutcomes {
            CompletedUnitCounts[RequestIndex] += 1;
            Results[RequestIndex].1.extend(UnitResult.1);
            Results[RequestIndex].2 = Results[RequestIndex].2.saturating_add(UnitResult.2);
            Results[RequestIndex].3 &= UnitResult.3;
        }
        for RequestIndex in 0..Results.len() {
            Results[RequestIndex].3 &=
                CompletedUnitCounts[RequestIndex] == ExpectedUnitCounts[RequestIndex];
        }
        let Status = if DeadlineExceeded {
            "DeadlineExceeded"
        } else if WorkCapExceeded {
            "WorkCapExceeded"
        } else {
            "Complete"
        };
        return (
            Status.to_string(),
            Results,
            ExpansionCount,
            WorkCapExceeded,
            DeadlineExceeded,
        );
    }
    let CompleteBatchUpperBound = RequestUpperBounds
        .iter()
        .copied()
        .fold(0usize, usize::saturating_add);
    let CanParallelizeCompleteBatch = AllowParallelRequests
        && Requests.len() > 1
        && CompleteBatchUpperBound <= MaximumExpansionCount;

    let (Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded) =
        if CanParallelizeCompleteBatch {
            let RequestCount = Requests.len();
            let WorkerCount = RoutingThreadPool().current_num_threads().max(1);
            let ChunkSize = RequestCount.saturating_add(WorkerCount - 1) / WorkerCount;
            let Outcomes: Vec<(EscapeRequestResult, bool, bool)> =
                RoutingThreadPool().install(|| {
                    Requests
                        .into_par_iter()
                        .zip(RequestUpperBounds.into_par_iter())
                        .chunks(ChunkSize.max(1))
                        .map(|RequestChunk| {
                            let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
                            RequestChunk
                                .into_iter()
                                .map(|(Request, RequestUpperBound)| {
                                    BuildOneDerivedEscapeRequest(
                                        Adjacency,
                                        IndexedGraph,
                                        &mut Workspace,
                                        Request,
                                        BendPenalty,
                                        RequestUpperBound,
                                        Deadline.clone(),
                                        MaximumY,
                                        None,
                                    )
                                })
                                .collect::<Vec<_>>()
                        })
                        .collect::<Vec<_>>()
                        .into_iter()
                        .flatten()
                        .collect()
                });
            let ExpansionCount = Outcomes
                .iter()
                .map(|(Result, _WorkCap, _Deadline)| Result.2)
                .sum();
            let WorkCapExceeded = Outcomes
                .iter()
                .any(|(_Result, WorkCap, _Deadline)| *WorkCap);
            let DeadlineExceeded = Outcomes
                .iter()
                .any(|(_Result, _WorkCap, DeadlineValue)| *DeadlineValue);
            (
                Outcomes.into_iter().map(|(Result, _, _)| Result).collect(),
                ExpansionCount,
                WorkCapExceeded,
                DeadlineExceeded,
            )
        } else if AllowParallelRequests && Requests.len() > 1 {
            // A complete portfolio upper bound can exceed the shared cap even
            // when every realized request is small. Schedule deterministic
            // prefix waves whose summed exact request bounds fit inside the
            // remaining cap. A wave therefore cannot overspend shared work,
            // while independent requests still use the native worker pool.
            let RequestValues = Requests
                .into_iter()
                .zip(RequestUpperBounds)
                .collect::<Vec<_>>();
            let mut Results = Vec::with_capacity(RequestValues.len());
            let mut ExpansionCount = 0usize;
            let mut WorkCapExceeded = false;
            let mut DeadlineExceeded = false;
            let mut RequestOffset = 0usize;
            while RequestOffset < RequestValues.len() {
                let RemainingExpansionCount = MaximumExpansionCount.saturating_sub(ExpansionCount);
                if RemainingExpansionCount == 0 {
                    WorkCapExceeded = true;
                    break;
                }
                let mut WaveEnd = RequestOffset;
                let mut WaveUpperBound = 0usize;
                while let Some((_Request, RequestUpperBound)) = RequestValues.get(WaveEnd) {
                    let NextUpperBound = WaveUpperBound.saturating_add(*RequestUpperBound);
                    if NextUpperBound > RemainingExpansionCount {
                        break;
                    }
                    WaveUpperBound = NextUpperBound;
                    WaveEnd += 1;
                }
                if WaveEnd == RequestOffset {
                    let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
                    let (Result, RequestWorkCap, RequestDeadline) = BuildOneDerivedEscapeRequest(
                        Adjacency,
                        IndexedGraph,
                        &mut Workspace,
                        RequestValues[RequestOffset].0.clone(),
                        BendPenalty,
                        RemainingExpansionCount,
                        Deadline.clone(),
                        MaximumY,
                        None,
                    );
                    ExpansionCount = ExpansionCount.saturating_add(Result.2);
                    Results.push(Result);
                    WorkCapExceeded = RequestWorkCap;
                    DeadlineExceeded = RequestDeadline;
                    RequestOffset += 1;
                } else {
                    let WaveResults = RoutingThreadPool().install(|| {
                        RequestValues[RequestOffset..WaveEnd]
                            .par_iter()
                            .map(|(Request, RequestUpperBound)| {
                                let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
                                BuildOneDerivedEscapeRequest(
                                    Adjacency,
                                    IndexedGraph,
                                    &mut Workspace,
                                    Request.clone(),
                                    BendPenalty,
                                    *RequestUpperBound,
                                    Deadline.clone(),
                                    MaximumY,
                                    None,
                                )
                            })
                            .collect::<Vec<_>>()
                    });
                    ExpansionCount = ExpansionCount.saturating_add(
                        WaveResults
                            .iter()
                            .map(|(Result, _WorkCap, _Deadline)| Result.2)
                            .sum::<usize>(),
                    );
                    WorkCapExceeded = WaveResults
                        .iter()
                        .any(|(_Result, WorkCap, _Deadline)| *WorkCap);
                    DeadlineExceeded = WaveResults
                        .iter()
                        .any(|(_Result, _WorkCap, DeadlineValue)| *DeadlineValue);
                    Results.extend(
                        WaveResults
                            .into_iter()
                            .map(|(Result, _WorkCap, _Deadline)| Result),
                    );
                    RequestOffset = WaveEnd;
                }
                if WorkCapExceeded || DeadlineExceeded {
                    break;
                }
            }
            (Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded)
        } else {
            let mut Results = Vec::with_capacity(Requests.len());
            let mut ExpansionCount = 0usize;
            let mut WorkCapExceeded = false;
            let mut DeadlineExceeded = false;
            let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
            for Request in Requests {
                let RemainingExpansionCount = MaximumExpansionCount.saturating_sub(ExpansionCount);
                let (Result, RequestWorkCap, RequestDeadline) = BuildOneDerivedEscapeRequest(
                    Adjacency,
                    IndexedGraph,
                    &mut Workspace,
                    Request,
                    BendPenalty,
                    RemainingExpansionCount,
                    Deadline.clone(),
                    MaximumY,
                    None,
                );
                ExpansionCount = ExpansionCount.saturating_add(Result.2);
                Results.push(Result);
                WorkCapExceeded = RequestWorkCap;
                DeadlineExceeded = RequestDeadline;
                if WorkCapExceeded || DeadlineExceeded {
                    break;
                }
            }
            (Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded)
        };
    let Status = if DeadlineExceeded {
        "DeadlineExceeded"
    } else if WorkCapExceeded {
        "WorkCapExceeded"
    } else {
        "Complete"
    };
    (
        Status.to_string(),
        Results,
        ExpansionCount,
        WorkCapExceeded,
        DeadlineExceeded,
    )
}

#[derive(Clone)]
struct DeferredAccessCandidateValue {
    Variable: String,
    CandidateId: String,
    OwnerSignal: String,
    IngressY: i32,
    Portal: Position,
    OrderedWire: Vec<Position>,
    Wire: Vec<Position>,
    Support: Vec<Position>,
    Air: Vec<Position>,
    Electrical: Vec<Position>,
}

#[derive(Clone, Eq, Hash, PartialEq)]
struct LayeredGuideAccessRampCacheKey {
    MemberIndex: usize,
    LayerIndex: usize,
    Axis: String,
    Lane: i32,
    PortalIdentity: Vec<Position>,
    Guide: Vec<Position>,
    GuideExpansion: usize,
    RequiredWire: Vec<Position>,
    ForeignBlockedNodes: Vec<Position>,
    OwnerSignal: String,
    DetachedSeedAccessPaths: Vec<Vec<Position>>,
    SourceDetachedAnchorIndex: Option<usize>,
}

type LayeredGuideAccessRampResult =
    Option<(Vec<Position>, Vec<Vec<Position>>, Vec<Vec<Position>>, bool)>;
struct LayeredGuideAccessRampCache {
    Values: Mutex<HashMap<
        LayeredGuideAccessRampCacheKey,
        Arc<OnceLock<Option<LayeredGuideAccessRampResult>>>,
    >>,
    HitCount: AtomicUsize,
    MissCount: AtomicUsize,
    KnownPoweredWitnessCount: AtomicUsize,
    ExhaustivePoweredProofCount: AtomicUsize,
}

impl LayeredGuideAccessRampCache {
    fn New() -> Self {
        Self {
            Values: Mutex::new(HashMap::new()),
            HitCount: AtomicUsize::new(0),
            MissCount: AtomicUsize::new(0),
            KnownPoweredWitnessCount: AtomicUsize::new(0),
            ExhaustivePoweredProofCount: AtomicUsize::new(0),
        }
    }

    fn GetCell(
        &self,
        Key: LayeredGuideAccessRampCacheKey,
    ) -> Arc<OnceLock<Option<LayeredGuideAccessRampResult>>> {
        let mut Values = self
            .Values
            .lock()
            .expect("layered guide access-ramp cache lock is not poisoned");
        if let Some(Value) = Values.get(&Key) {
            self.HitCount.fetch_add(1, Ordering::Relaxed);
            return Value.clone();
        }
        self.MissCount.fetch_add(1, Ordering::Relaxed);
        let Value = Arc::new(OnceLock::new());
        Values.insert(Key, Value.clone());
        Value
    }

    fn Counts(&self) -> (usize, usize, usize, usize, usize) {
        (
            self.HitCount.load(Ordering::Relaxed),
            self.MissCount.load(Ordering::Relaxed),
            self.KnownPoweredWitnessCount.load(Ordering::Relaxed),
            self.ExhaustivePoweredProofCount.load(Ordering::Relaxed),
            self.Values
                .lock()
                .expect("layered guide access-ramp cache lock is not poisoned")
                .len(),
        )
    }
}

fn AccessNeighborPositions((X, Y, Z): Position) -> [Position; 12] {
    [
        (X + 1, Y, Z),
        (X - 1, Y, Z),
        (X, Y, Z + 1),
        (X, Y, Z - 1),
        (X + 1, Y + 1, Z),
        (X + 1, Y - 1, Z),
        (X - 1, Y + 1, Z),
        (X - 1, Y - 1, Z),
        (X, Y + 1, Z + 1),
        (X, Y - 1, Z + 1),
        (X, Y + 1, Z - 1),
        (X, Y - 1, Z - 1),
    ]
}

fn EraseAccessPathLoops(Values: impl IntoIterator<Item = Position>) -> Vec<Position> {
    let mut Result = Vec::new();
    let mut PositionIndices = HashMap::new();
    for PositionValue in Values {
        if let Some(PriorIndex) = PositionIndices.get(&PositionValue).copied() {
            for Removed in Result.drain((PriorIndex + 1)..) {
                PositionIndices.remove(&Removed);
            }
            continue;
        }
        PositionIndices.insert(PositionValue, Result.len());
        Result.push(PositionValue);
    }
    Result
}

fn BuildDeferredAccessCandidate(
    Variable: String,
    CandidateId: String,
    OwnerSignal: String,
    IngressY: i32,
    WirePath: Vec<Position>,
) -> Option<DeferredAccessCandidateValue> {
    let Portal = *WirePath.last()?;
    let OrderedWire = EraseAccessPathLoops(WirePath);
    let Wire = OrderedWire.iter().copied().collect::<BTreeSet<_>>();
    if Wire.is_empty() {
        return None;
    }
    let Support = Wire
        .iter()
        .map(|(X, Y, Z)| (*X, Y - 1, *Z))
        .collect::<BTreeSet<_>>();
    let mut Air = BTreeSet::new();
    for PositionValue in &Wire {
        for Neighbor in AccessNeighborPositions(*PositionValue) {
            if Neighbor <= *PositionValue
                || Neighbor.1 == PositionValue.1
                || !Wire.contains(&Neighbor)
            {
                continue;
            }
            let Lower = if PositionValue.1 < Neighbor.1 {
                *PositionValue
            } else {
                Neighbor
            };
            Air.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    if !Air.is_disjoint(&Wire)
        || Wire.iter().any(|(X, Y, Z)| {
            let SupportPosition = (*X, Y - 1, *Z);
            Wire.contains(&SupportPosition) || Air.contains(&SupportPosition)
        })
    {
        return None;
    }
    let Electrical = Wire
        .iter()
        .flat_map(|PositionValue| {
            std::iter::once(*PositionValue).chain(AccessNeighborPositions(*PositionValue))
        })
        .collect::<BTreeSet<_>>();
    Some(DeferredAccessCandidateValue {
        Variable,
        CandidateId,
        OwnerSignal,
        IngressY,
        Portal,
        OrderedWire,
        Wire: Wire.into_iter().collect(),
        Support: Support.into_iter().collect(),
        Air: Air.into_iter().collect(),
        Electrical: Electrical.into_iter().collect(),
    })
}

fn SortedAccessPositionsIntersect(First: &[Position], Second: &[Position]) -> bool {
    let mut FirstIndex = 0usize;
    let mut SecondIndex = 0usize;
    while FirstIndex < First.len() && SecondIndex < Second.len() {
        match First[FirstIndex].cmp(&Second[SecondIndex]) {
            std::cmp::Ordering::Less => FirstIndex += 1,
            std::cmp::Ordering::Greater => SecondIndex += 1,
            std::cmp::Ordering::Equal => return true,
        }
    }
    false
}

fn CrossCandidateRequiredAir(
    First: &DeferredAccessCandidateValue,
    Second: &DeferredAccessCandidateValue,
) -> Vec<Position> {
    let mut Air = BTreeSet::new();
    for FirstPosition in &First.Wire {
        for SecondPosition in &Second.Wire {
            let (Lower, Higher) = if FirstPosition <= SecondPosition {
                (*FirstPosition, *SecondPosition)
            } else {
                (*SecondPosition, *FirstPosition)
            };
            if Higher.1 == Lower.1 || !AccessNeighborPositions(Lower).contains(&Higher) {
                continue;
            }
            Air.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    Air.into_iter().collect()
}

fn DeferredAccessCandidatesConflict(
    First: &DeferredAccessCandidateValue,
    Second: &DeferredAccessCandidateValue,
) -> bool {
    let StaticConflict = SortedAccessPositionsIntersect(&First.Support, &Second.Wire)
        || SortedAccessPositionsIntersect(&First.Support, &Second.Air)
        || SortedAccessPositionsIntersect(&Second.Support, &First.Wire)
        || SortedAccessPositionsIntersect(&Second.Support, &First.Air)
        || SortedAccessPositionsIntersect(&First.Air, &Second.Wire)
        || SortedAccessPositionsIntersect(&Second.Air, &First.Wire);
    if StaticConflict {
        return true;
    }
    let CrossAir = CrossCandidateRequiredAir(First, Second);
    if SortedAccessPositionsIntersect(&CrossAir, &First.Wire)
        || SortedAccessPositionsIntersect(&CrossAir, &Second.Wire)
        || SortedAccessPositionsIntersect(&CrossAir, &First.Support)
        || SortedAccessPositionsIntersect(&CrossAir, &Second.Support)
    {
        return true;
    }
    First.OwnerSignal != Second.OwnerSignal
        && (SortedAccessPositionsIntersect(&First.Wire, &Second.Electrical)
            || SortedAccessPositionsIntersect(&Second.Wire, &First.Electrical))
}

struct LayeredFrozenBaseClaimIndex {
    Wire: HashSet<Position>,
    Support: HashSet<Position>,
    Air: HashSet<Position>,
    WireOwners: HashMap<Position, HashSet<String>>,
    ElectricalOwners: HashMap<Position, HashSet<String>>,
}

impl LayeredFrozenBaseClaimIndex {
    fn New(Values: &[DeferredAccessCandidateValue]) -> Self {
        let mut Result = Self {
            Wire: HashSet::new(),
            Support: HashSet::new(),
            Air: HashSet::new(),
            WireOwners: HashMap::new(),
            ElectricalOwners: HashMap::new(),
        };
        for Value in Values {
            Result.Wire.extend(Value.Wire.iter().copied());
            Result.Support.extend(Value.Support.iter().copied());
            Result.Air.extend(Value.Air.iter().copied());
            for PositionValue in &Value.Wire {
                Result
                    .WireOwners
                    .entry(*PositionValue)
                    .or_default()
                    .insert(Value.OwnerSignal.clone());
            }
            for PositionValue in &Value.Electrical {
                Result
                    .ElectricalOwners
                    .entry(*PositionValue)
                    .or_default()
                    .insert(Value.OwnerSignal.clone());
            }
        }
        Result
    }

    fn Conflicts(&self, Value: &DeferredAccessCandidateValue) -> bool {
        Value.Support.iter().any(|PositionValue| {
            self.Wire.contains(PositionValue) || self.Air.contains(PositionValue)
        }) || Value.Wire.iter().any(|PositionValue| {
            self.Support.contains(PositionValue)
                || self.Air.contains(PositionValue)
                || self
                    .ElectricalOwners
                    .get(PositionValue)
                    .is_some_and(|Owners| {
                        Owners.iter().any(|Owner| Owner != &Value.OwnerSignal)
                    })
        }) || Value.Air.iter().any(|PositionValue| {
            self.Support.contains(PositionValue) || self.Wire.contains(PositionValue)
        }) || Value.Electrical.iter().any(|PositionValue| {
            self.WireOwners
                .get(PositionValue)
                .is_some_and(|Owners| {
                    Owners.iter().any(|Owner| Owner != &Value.OwnerSignal)
                })
        })
    }
}

fn BuildFixedPrefixAccessCandidates(
    Requests: &[EscapeRequest],
    RequestMetadata: &[(String, String, String)],
) -> HashMap<String, DeferredAccessCandidateValue> {
    let MetadataByRequestId = RequestMetadata
        .iter()
        .map(|(RequestId, Variable, OwnerSignal)| (RequestId.as_str(), (Variable, OwnerSignal)))
        .collect::<HashMap<_, _>>();
    Requests
        .iter()
        .filter_map(|Request| {
            let (Variable, OwnerSignal) = MetadataByRequestId.get(Request.0.as_str())?;
            BuildDeferredAccessCandidate(
                (*Variable).clone(),
                format!("{}#fixed-prefix", Request.0),
                (*OwnerSignal).clone(),
                Request.1 .1,
                EraseAccessPathLoops(Request.4.iter().copied()),
            )
            .map(|Value| ((*Variable).clone(), Value))
        })
        .collect()
}

fn ExactLayeredAccessPathCanCarryPower(SourceToPortal: bool, WirePath: &[Position]) -> bool {
    if WirePath.len() <= 1 {
        return true;
    }
    let OrderedPath = if SourceToPortal {
        WirePath.to_vec()
    } else {
        WirePath.iter().rev().copied().collect::<Vec<_>>()
    };
    let mut Power = 15u8;
    for Index in 0..OrderedPath.len() - 1 {
        let mut NextPower = Power.saturating_sub(1);
        if Index > 0 && Index + 1 < OrderedPath.len() && Power > 0 {
            let Previous = OrderedPath[Index - 1];
            let Current = OrderedPath[Index];
            let Next = OrderedPath[Index + 1];
            let Incoming = (
                Current.0 - Previous.0,
                Current.1 - Previous.1,
                Current.2 - Previous.2,
            );
            let Outgoing = (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
            if Incoming == Outgoing && Incoming.1 == 0 && Incoming.0.abs() + Incoming.2.abs() == 1 {
                NextPower = 15;
            }
        }
        Power = NextPower;
        if Power == 0 {
            return false;
        }
    }
    true
}

fn BuildDeferredLayeredAccessCandidates(
    Requests: &[EscapeRequest],
    RequestResults: &[EscapeRequestResult],
    RequestMetadata: &[(String, String, String)],
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<(BTreeMap<String, String>, Vec<DeferredAccessCandidateValue>)>> {
    let RequestById = Requests
        .iter()
        .map(|Request| (Request.0.clone(), Request))
        .collect::<HashMap<_, _>>();
    let MetadataById = RequestMetadata
        .iter()
        .map(|(RequestId, Variable, OwnerSignal)| {
            (RequestId.clone(), (Variable.clone(), OwnerSignal.clone()))
        })
        .collect::<HashMap<_, _>>();
    if MetadataById.len() != RequestMetadata.len()
        || RequestById.len() != Requests.len()
        || RequestById
            .keys()
            .any(|RequestId| !MetadataById.contains_key(RequestId))
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access request metadata must exactly own every request",
        ));
    }
    let mut RequiredVariables = BTreeMap::<String, String>::new();
    for (_RequestId, Variable, OwnerSignal) in RequestMetadata {
        if !Variable.starts_with("__access_terminal__:")
            || Variable == "__access_terminal__:"
            || OwnerSignal.is_empty()
        {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "layered access variables and owners must be nonempty: request={:?} variable={:?} owner={:?}",
                _RequestId, Variable, OwnerSignal,
            )));
        }
        if RequiredVariables
            .insert(Variable.clone(), OwnerSignal.clone())
            .is_some_and(|Existing| Existing != *OwnerSignal)
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "one layered access variable cannot have multiple owners",
            ));
        }
    }
    let mut DeferredValues = Vec::new();
    let mut SeenPhysicalValues = BTreeSet::new();
    for IncludePowerAlternatives in [false, true] {
        for (ResultIndex, (RequestId, Candidates, _ExpansionCount, Complete)) in
            RequestResults.iter().enumerate()
        {
            if ResultIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Ok(None);
            }
            if !Complete {
                return Ok(None);
            }
            let Some(Request) = RequestById.get(RequestId) else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "layered access result references an unknown request",
                ));
            };
            let Some((Variable, OwnerSignal)) = MetadataById.get(RequestId) else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "layered access result has no variable owner",
                ));
            };
            let mut SeenIngresses = HashMap::<Position, usize>::new();
            let mut OriginalCandidateCount = 0usize;
            for (CandidateIndex, (Ingress, _Direction, Path)) in Candidates.iter().enumerate() {
                if CandidateIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Ok(None);
                }
                let IngressOccurrence = SeenIngresses.entry(*Ingress).or_insert(0);
                let IsPowerAlternative = Request.6 && *IngressOccurrence > 0;
                let OriginalCandidateIndex = if IsPowerAlternative {
                    OriginalCandidateCount.saturating_sub(1)
                } else {
                    let Value = OriginalCandidateCount;
                    OriginalCandidateCount += 1;
                    Value
                };
                *IngressOccurrence += 1;
                if IsPowerAlternative != IncludePowerAlternatives {
                    continue;
                }
                let WirePath = EraseAccessPathLoops(
                    Request
                        .4
                        .iter()
                        .copied()
                        .chain(Path.iter().copied().skip(1)),
                );
                if !SeenPhysicalValues.insert((Variable.clone(), WirePath.clone())) {
                    continue;
                }
                let CandidateId = if IsPowerAlternative {
                    if *IngressOccurrence == 2 {
                        format!("{}#{}:power", RequestId, OriginalCandidateIndex)
                    } else {
                        format!(
                            "{}#{}:power:{}",
                            RequestId,
                            OriginalCandidateIndex,
                            IngressOccurrence.saturating_sub(1),
                        )
                    }
                } else {
                    format!("{}#{}", RequestId, OriginalCandidateIndex)
                };
                if let Some(Value) = BuildDeferredAccessCandidate(
                    Variable.clone(),
                    CandidateId,
                    OwnerSignal.clone(),
                    Ingress.1,
                    WirePath,
                ) {
                    DeferredValues.push(Value);
                }
            }
        }
    }
    // Preserve physically distinct powered alternatives.  A longer wire set
    // may be the first path with legal repeater sites, so subset dominance is
    // not sound for the exact layered power contract.
    DeferredValues.sort_by(|First, Second| {
        First
            .Variable
            .cmp(&Second.Variable)
            .then_with(|| First.Portal.cmp(&Second.Portal))
            .then_with(|| First.Wire.len().cmp(&Second.Wire.len()))
            .then_with(|| First.Wire.cmp(&Second.Wire))
            .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
    });
    Ok(Some((RequiredVariables, DeferredValues)))
}

fn BuildLayeredAccessCandidateGroups(
    Requests: &[EscapeRequest],
    RequestResults: &[EscapeRequestResult],
    RequestMetadata: &[(String, String, String)],
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<(BTreeMap<String, Vec<AssignmentCandidate>>, usize)>> {
    let Some((RequiredVariables, DeferredValues)) =
        BuildDeferredLayeredAccessCandidates(Requests, RequestResults, RequestMetadata, Deadline)?
    else {
        return Ok(None);
    };
    let ResourcePositions = DeferredValues
        .iter()
        .flat_map(|Value| {
            Value
                .Wire
                .iter()
                .chain(&Value.Support)
                .chain(&Value.Air)
                .chain(&Value.Electrical)
                .copied()
        })
        .collect::<BTreeSet<_>>();
    let ResourceIndex = ResourcePositions
        .into_iter()
        .enumerate()
        .map(|(Index, PositionValue)| (PositionValue, Index))
        .collect::<HashMap<_, _>>();
    let ResourceCount = ResourceIndex.len().max(1);
    let mut Groups = RequiredVariables
        .keys()
        .map(|Variable| (Variable.clone(), Vec::new()))
        .collect::<BTreeMap<_, _>>();
    for (Index, Value) in DeferredValues.into_iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        let Remap = |Positions: &[Position]| {
            Positions
                .iter()
                .map(|PositionValue| ResourceIndex[PositionValue])
                .collect::<Vec<_>>()
        };
        let Claims = match ClaimMask::FromIndicesWithDeadline(
            ResourceCount,
            &Remap(&Value.Wire),
            &Remap(&Value.Support),
            &Remap(&Value.Air),
            &Remap(&Value.Electrical),
            Deadline,
        ) {
            Ok(Value) => Arc::new(Value),
            Err(ClaimMaskBuildError::DeadlineExceeded) => return Ok(None),
            Err(ClaimMaskBuildError::IndexOutOfRange) => {
                unreachable!("layered access positions were indexed before claim construction")
            }
        };
        let LogicalKey = Value
            .Variable
            .strip_prefix("__access_terminal__:")
            .expect("validated layered access variable");
        let Contract = format!(
            "access-stub:{}={};access-portal:{}={};access-layer:{}={}",
            LogicalKey,
            Value.CandidateId,
            LogicalKey,
            LayeredAccessPortalContractValue(Value.Portal),
            Value.OwnerSignal,
            Value.IngressY,
        );
        Groups
            .get_mut(&Value.Variable)
            .expect("validated layered access variable owns a group")
            .push(AssignmentCandidate {
                CandidateId: Value.CandidateId,
                OwnerSignal: Value.OwnerSignal,
                TemplateRequirements: ParseContractRequirements(&Contract),
                ForbiddenCandidateIds: Arc::new(Vec::new()),
                OrderedWire: Arc::new(Value.OrderedWire),
                PoweredAccessConstraint: None,
                Claims,
                MaterialCost: 0,
                FootprintGrowth: 0,
                Length: Value.Wire.len().min(i32::MAX as usize) as i32,
                BendCount: 0,
                ViaCount: 0,
            });
    }
    Ok(Some((Groups, ResourceCount)))
}

#[derive(Clone)]
struct DeferredGuideCandidateValue {
    Variable: String,
    CandidateId: String,
    OwnerSignal: String,
    Requirements: Vec<(String, String)>,
    Portals: Vec<Position>,
    RoutingY: i32,
    Axis: String,
    Lane: i32,
    Guide: Vec<Position>,
    AccessRamps: Vec<Vec<Position>>,
    DetailedHintPaths: Vec<Vec<Position>>,
    CertifiedRepeaters: Vec<(Position, String)>,
    PhysicalGuide: Vec<Position>,
    SupportedAccessChoices: BTreeSet<(String, String)>,
    CertifiedAccessTuples: Arc<Vec<Vec<(String, String)>>>,
    TerminalVariables: Vec<String>,
    DetachedSeedAccessPaths: Vec<Vec<Position>>,
    SourceTerminalVariable: Option<String>,
    SourceDetachedAnchorIndex: Option<usize>,
    PoweredCorridorHint: bool,
    Claims: DeferredAccessCandidateValue,
    Priority: (usize, usize, usize, usize, usize, usize, String, i32),
}

fn LayeredAccessPortalContractValue((X, Y, Z): Position) -> String {
    format!("{X},{Y},{Z}")
}

fn BuildLayeredGuideAccessContract(
    Requirements: &[(String, String)],
    AccessValueByChoice: &HashMap<(String, String), Position>,
) -> String {
    Requirements
        .iter()
        .map(|(Variable, CandidateId)| {
            let LogicalKey = Variable
                .strip_prefix("__access_terminal__:")
                .expect("validated guide access requirement");
            let AccessPortal = AccessValueByChoice
                .get(&(Variable.clone(), CandidateId.clone()))
                .expect("validated guide access requirement names an access value");
            format!(
                "access-portal:{}={}",
                LogicalKey,
                LayeredAccessPortalContractValue(*AccessPortal),
            )
        })
        .collect::<Vec<_>>()
        .join(";")
}

fn LayeredAccessTerminalVariablePosition(Variable: &str) -> Option<Position> {
    let (_Identity, EncodedPosition) = Variable.rsplit_once('@')?;
    let mut Coordinates = EncodedPosition.split(',');
    let X = Coordinates.next()?.parse().ok()?;
    let Y = Coordinates.next()?.parse().ok()?;
    let Z = Coordinates.next()?.parse().ok()?;
    Coordinates.next().is_none().then_some((X, Y, Z))
}

fn LayeredGuideTerminalSpan(TerminalVariables: &[String]) -> i32 {
    let Positions = TerminalVariables
        .iter()
        .filter_map(|Variable| LayeredAccessTerminalVariablePosition(Variable))
        .collect::<Vec<_>>();
    if Positions.len() != TerminalVariables.len() || Positions.is_empty() {
        return 0;
    }
    let MinimumX = Positions.iter().map(|Position| Position.0).min().unwrap();
    let MaximumX = Positions.iter().map(|Position| Position.0).max().unwrap();
    let MinimumZ = Positions.iter().map(|Position| Position.2).min().unwrap();
    let MaximumZ = Positions.iter().map(|Position| Position.2).max().unwrap();
    MaximumX
        .saturating_sub(MinimumX)
        .saturating_add(MaximumZ.saturating_sub(MinimumZ))
}

fn CandidateLayeredGuideLanes(Center: i32, Count: usize, Pitch: i32) -> Vec<i32> {
    let mut Result = vec![Center];
    let mut Offset = Pitch;
    while Result.len() < Count {
        Result.push(Center - Offset);
        if Result.len() < Count {
            Result.push(Center + Offset);
        }
        Offset += Pitch;
    }
    Result
}

fn RasterizeLayeredGuideSegment(
    First: (i32, i32),
    Second: (i32, i32),
    Result: &mut BTreeSet<(i32, i32)>,
) {
    if First.0 == Second.0 {
        for Z in First.1.min(Second.1)..=First.1.max(Second.1) {
            Result.insert((First.0, Z));
        }
    } else if First.1 == Second.1 {
        for X in First.0.min(Second.0)..=First.0.max(Second.0) {
            Result.insert((X, First.1));
        }
    }
}

fn BuildLayeredGuideSpine(
    Terminals: &[(i32, i32)],
    Axis: &str,
    Lane: i32,
    RoutingY: i32,
) -> Vec<Position> {
    let mut Guide = BTreeSet::new();
    if Axis == "X" {
        let Minimum = Terminals.iter().map(|Value| Value.0).min().unwrap_or(0);
        let Maximum = Terminals.iter().map(|Value| Value.0).max().unwrap_or(0);
        RasterizeLayeredGuideSegment((Minimum, Lane), (Maximum, Lane), &mut Guide);
        for (X, Z) in Terminals {
            RasterizeLayeredGuideSegment((*X, *Z), (*X, Lane), &mut Guide);
        }
    } else {
        let Minimum = Terminals.iter().map(|Value| Value.1).min().unwrap_or(0);
        let Maximum = Terminals.iter().map(|Value| Value.1).max().unwrap_or(0);
        RasterizeLayeredGuideSegment((Lane, Minimum), (Lane, Maximum), &mut Guide);
        for (X, Z) in Terminals {
            RasterizeLayeredGuideSegment((*X, *Z), (Lane, *Z), &mut Guide);
        }
    }
    Guide.into_iter().map(|(X, Z)| (X, RoutingY, Z)).collect()
}

fn LayeredAccessTupleIsSelfLegal(Values: &[&DeferredAccessCandidateValue]) -> bool {
    Values.iter().enumerate().all(|(Index, First)| {
        Values
            .iter()
            .skip(Index + 1)
            .all(|Second| !DeferredAccessCandidatesConflict(First, Second))
    })
}

fn FindCompleteLayeredAccessWitness(
    AccessByVariable: &BTreeMap<String, Vec<&DeferredAccessCandidateValue>>,
    Deadline: &RuntimeDeadline,
) -> Option<HashMap<String, String>> {
    // This witness only seeds portal diversity; it is never a feasibility
    // proof and the complete catalog remains available when it is absent.
    // Keep its bounded lookahead small so a hard access-only member cannot
    // consume the portfolio's absolute routing deadline.
    const MAXIMUM_ACCESS_CERTIFICATE_EXPANSIONS: usize = 512;
    let mut Variables = AccessByVariable.keys().cloned().collect::<Vec<_>>();
    Variables.sort_by_key(|Variable| (AccessByVariable[Variable].len(), Variable.clone()));
    let mut Selected = HashMap::<String, &DeferredAccessCandidateValue>::new();
    let mut ExpansionCount = 0usize;

    fn Search<'a>(
        VariableIndex: usize,
        Variables: &[String],
        AccessByVariable: &BTreeMap<String, Vec<&'a DeferredAccessCandidateValue>>,
        Selected: &mut HashMap<String, &'a DeferredAccessCandidateValue>,
        ExpansionCount: &mut usize,
        Deadline: &RuntimeDeadline,
    ) -> bool {
        if Deadline.Check() || *ExpansionCount >= MAXIMUM_ACCESS_CERTIFICATE_EXPANSIONS {
            return false;
        }
        if VariableIndex == Variables.len() {
            return true;
        }
        let Variable = &Variables[VariableIndex];
        for Candidate in &AccessByVariable[Variable] {
            if Deadline.Check() || *ExpansionCount >= MAXIMUM_ACCESS_CERTIFICATE_EXPANSIONS {
                return false;
            }
            if Selected
                .values()
                .any(|Previous| DeferredAccessCandidatesConflict(Candidate, Previous))
            {
                continue;
            }
            *ExpansionCount += 1;
            Selected.insert(Variable.clone(), Candidate);
            if Search(
                VariableIndex + 1,
                Variables,
                AccessByVariable,
                Selected,
                ExpansionCount,
                Deadline,
            ) {
                return true;
            }
            Selected.remove(Variable);
        }
        false
    }

    let Complete = Search(
        0,
        &Variables,
        AccessByVariable,
        &mut Selected,
        &mut ExpansionCount,
        Deadline,
    );
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered complete access certificate complete={} variables={} expansions={}",
            Complete,
            Variables.len(),
            ExpansionCount,
        );
    }
    Complete.then(|| {
        Selected
            .into_iter()
            .map(|(Variable, Candidate)| (Variable, Candidate.CandidateId.clone()))
            .collect()
    })
}

struct LayeredAccessClaimOccupancy {
    Wire: HashMap<Position, usize>,
    Support: HashMap<Position, usize>,
    Air: HashMap<Position, usize>,
}

impl LayeredAccessClaimOccupancy {
    fn New(GuideClaims: &DeferredAccessCandidateValue) -> Self {
        let Count = |Values: &[Position]| {
            Values
                .iter()
                .copied()
                .map(|PositionValue| (PositionValue, 1usize))
                .collect::<HashMap<_, _>>()
        };
        Self {
            Wire: Count(&GuideClaims.Wire),
            Support: Count(&GuideClaims.Support),
            Air: Count(&GuideClaims.Air),
        }
    }

    fn Conflicts(&self, Value: &DeferredAccessCandidateValue) -> bool {
        if Value.Support.iter().any(|PositionValue| {
            self.Wire.contains_key(PositionValue) || self.Air.contains_key(PositionValue)
        }) || Value.Wire.iter().any(|PositionValue| {
            self.Support.contains_key(PositionValue) || self.Air.contains_key(PositionValue)
        }) || Value.Air.iter().any(|PositionValue| {
            self.Wire.contains_key(PositionValue) || self.Support.contains_key(PositionValue)
        }) {
            return true;
        }
        false
    }

    fn Add(&mut self, Value: &DeferredAccessCandidateValue) {
        for (Counts, Positions) in [
            (&mut self.Wire, &Value.Wire),
            (&mut self.Support, &Value.Support),
            (&mut self.Air, &Value.Air),
        ] {
            for PositionValue in Positions {
                *Counts.entry(*PositionValue).or_default() += 1;
            }
        }
    }

    fn Remove(&mut self, Value: &DeferredAccessCandidateValue) {
        for (Counts, Positions) in [
            (&mut self.Wire, &Value.Wire),
            (&mut self.Support, &Value.Support),
            (&mut self.Air, &Value.Air),
        ] {
            for PositionValue in Positions {
                let Count = Counts
                    .get_mut(PositionValue)
                    .expect("selected layered access claim is occupied");
                *Count -= 1;
                if *Count == 0 {
                    Counts.remove(PositionValue);
                }
            }
        }
    }
}

struct LayeredWireForestCheckpoint {
    ExistingPositions: Vec<Position>,
    NewPositions: Vec<Position>,
    UnionChanges: Vec<(Position, Position, usize)>,
    ComponentCount: usize,
    CycleCount: usize,
}

struct LayeredWireForestOccupancy {
    ActiveCounts: HashMap<Position, usize>,
    ParentByPosition: HashMap<Position, Position>,
    SizeByRoot: HashMap<Position, usize>,
    ComponentCount: usize,
    CycleCount: usize,
}

impl LayeredWireForestOccupancy {
    fn New(_GraphAdjacency: &HashMap<Position, Vec<Position>>) -> Self {
        Self {
            ActiveCounts: HashMap::new(),
            ParentByPosition: HashMap::new(),
            SizeByRoot: HashMap::new(),
            ComponentCount: 0,
            CycleCount: 0,
        }
    }

    fn FindRoot(&self, mut PositionValue: Position) -> Position {
        loop {
            let Parent = self.ParentByPosition[&PositionValue];
            if Parent == PositionValue {
                return PositionValue;
            }
            PositionValue = Parent;
        }
    }

    fn Add(&mut self, Positions: &[Position]) -> LayeredWireForestCheckpoint {
        let mut Checkpoint = LayeredWireForestCheckpoint {
            ExistingPositions: Vec::new(),
            NewPositions: Vec::new(),
            UnionChanges: Vec::new(),
            ComponentCount: self.ComponentCount,
            CycleCount: self.CycleCount,
        };
        for PositionValue in Positions {
            if let Some(Count) = self.ActiveCounts.get_mut(PositionValue) {
                *Count += 1;
                Checkpoint.ExistingPositions.push(*PositionValue);
                continue;
            }
            self.ActiveCounts.insert(*PositionValue, 1);
            self.ParentByPosition.insert(*PositionValue, *PositionValue);
            self.SizeByRoot.insert(*PositionValue, 1);
            self.ComponentCount += 1;
            Checkpoint.NewPositions.push(*PositionValue);
            for Neighbor in AccessNeighborPositions(*PositionValue)
                .into_iter()
                .filter(|Neighbor| self.ActiveCounts.contains_key(Neighbor))
            {
                let mut FirstRoot = self.FindRoot(*PositionValue);
                let mut SecondRoot = self.FindRoot(Neighbor);
                if FirstRoot == SecondRoot {
                    self.CycleCount += 1;
                    continue;
                }
                if self.SizeByRoot[&FirstRoot] < self.SizeByRoot[&SecondRoot] {
                    std::mem::swap(&mut FirstRoot, &mut SecondRoot);
                }
                let FirstSize = self.SizeByRoot[&FirstRoot];
                self.ParentByPosition.insert(SecondRoot, FirstRoot);
                self.SizeByRoot
                    .insert(FirstRoot, FirstSize + self.SizeByRoot[&SecondRoot]);
                self.ComponentCount -= 1;
                Checkpoint
                    .UnionChanges
                    .push((SecondRoot, FirstRoot, FirstSize));
            }
        }
        Checkpoint
    }

    fn Restore(&mut self, Checkpoint: LayeredWireForestCheckpoint) {
        for (ChildRoot, ParentRoot, PreviousParentSize) in Checkpoint.UnionChanges.into_iter().rev()
        {
            self.ParentByPosition.insert(ChildRoot, ChildRoot);
            self.SizeByRoot.insert(ParentRoot, PreviousParentSize);
        }
        for PositionValue in Checkpoint.NewPositions.into_iter().rev() {
            self.ActiveCounts.remove(&PositionValue);
            self.ParentByPosition.remove(&PositionValue);
            self.SizeByRoot.remove(&PositionValue);
        }
        for PositionValue in Checkpoint.ExistingPositions {
            let Count = self
                .ActiveCounts
                .get_mut(&PositionValue)
                .expect("existing layered wire remains active");
            *Count -= 1;
        }
        self.ComponentCount = Checkpoint.ComponentCount;
        self.CycleCount = Checkpoint.CycleCount;
    }

    fn IsCompleteConnectedBundle(&self, TerminalVariables: &[String]) -> bool {
        self.ComponentCount == 1
            && TerminalVariables.iter().all(|Variable| {
                LayeredAccessTerminalVariablePosition(Variable)
                    .is_some_and(|PositionValue| self.ActiveCounts.contains_key(&PositionValue))
            })
    }
}

struct LayeredPoweredWitnessWorkspace {
    WireMask: Vec<bool>,
    TargetMask: Vec<bool>,
    PowerMaskByState: Vec<u16>,
    TouchedWireIndices: Vec<usize>,
    TouchedTargetIndices: Vec<usize>,
    TouchedStateIndices: Vec<usize>,
}

impl LayeredPoweredWitnessWorkspace {
    fn New(IndexedGraph: &IndexedEscapeGraph) -> Self {
        Self {
            WireMask: vec![false; IndexedGraph.Positions.len()],
            TargetMask: vec![false; IndexedGraph.Positions.len()],
            PowerMaskByState: vec![
                0;
                IndexedGraph.Positions.len() * ESCAPE_DIRECTION_STATE_COUNT
            ],
            TouchedWireIndices: Vec::new(),
            TouchedTargetIndices: Vec::new(),
            TouchedStateIndices: Vec::new(),
        }
    }

    fn Reset(&mut self) {
        for Index in self.TouchedWireIndices.drain(..) {
            self.WireMask[Index] = false;
        }
        for Index in self.TouchedTargetIndices.drain(..) {
            self.TargetMask[Index] = false;
        }
        for Index in self.TouchedStateIndices.drain(..) {
            self.PowerMaskByState[Index] = 0;
        }
    }

    fn AddWirePosition(
        &mut self,
        IndexedGraph: &IndexedEscapeGraph,
        PositionValue: Position,
    ) -> bool {
        let Some(Index) = IndexedGraph.PositionIndices.get(&PositionValue).copied() else {
            return false;
        };
        if !self.WireMask[Index] {
            self.WireMask[Index] = true;
            self.TouchedWireIndices.push(Index);
        }
        true
    }

    fn AddTargetIndex(&mut self, Index: usize) -> bool {
        if self.TargetMask[Index] {
            return false;
        }
        self.TargetMask[Index] = true;
        self.TouchedTargetIndices.push(Index);
        true
    }

    fn RecordStatePower(&mut self, StateIndex: usize, Power: u8) -> bool {
        if !(1..=15).contains(&Power) {
            return false;
        }
        let ExistingMask = self.PowerMaskByState[StateIndex];
        let PowerBit = 1u16 << Power;
        let Dominated = if Power == 14 || Power == 15 {
            ExistingMask & PowerBit != 0
        } else {
            (Power..=13).any(|CandidatePower| {
                ExistingMask & (1u16 << CandidatePower) != 0
            })
        };
        if Dominated {
            return false;
        }
        if ExistingMask == 0 {
            self.TouchedStateIndices.push(StateIndex);
        }
        self.PowerMaskByState[StateIndex] |= PowerBit;
        true
    }
}

fn LayeredPoweredWitnessHasSelfExcitingCycle(
    IndexedGraph: &IndexedEscapeGraph,
    WitnessNodeIndices: &HashSet<usize>,
    RepeaterEndpoints: &HashMap<usize, (usize, usize)>,
    Deadline: &RuntimeDeadline,
) -> Option<bool> {
    if RepeaterEndpoints.is_empty() {
        return Some(false);
    }
    let mut OrderedNodeIndices = WitnessNodeIndices.iter().copied().collect::<Vec<_>>();
    OrderedNodeIndices.sort_unstable();
    let LocalIndexByNode = OrderedNodeIndices
        .iter()
        .enumerate()
        .map(|(LocalIndex, NodeIndex)| (*NodeIndex, LocalIndex))
        .collect::<HashMap<_, _>>();
    let mut Adjacency = vec![Vec::<usize>::new(); OrderedNodeIndices.len()];
    let mut ReverseAdjacency = vec![Vec::<usize>::new(); OrderedNodeIndices.len()];
    let mut WorkCount = 0usize;
    for (LocalIndex, CurrentNode) in OrderedNodeIndices.iter().copied().enumerate() {
        if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        WorkCount = WorkCount.saturating_add(1);
        let Current = IndexedGraph.Positions[CurrentNode];
        let CandidateNodes = if let Some((_InputNode, OutputNode)) =
            RepeaterEndpoints.get(&CurrentNode)
        {
            vec![*OutputNode]
        } else {
            let (X, Y, Z) = Current;
            [
                (X + 1, Y, Z),
                (X - 1, Y, Z),
                (X, Y, Z + 1),
                (X, Y, Z - 1),
                (X + 1, Y + 1, Z),
                (X + 1, Y - 1, Z),
                (X - 1, Y + 1, Z),
                (X - 1, Y - 1, Z),
                (X, Y + 1, Z + 1),
                (X, Y - 1, Z + 1),
                (X, Y + 1, Z - 1),
                (X, Y - 1, Z - 1),
            ]
            .into_iter()
            .filter_map(|PositionValue| {
                IndexedGraph.PositionIndices.get(&PositionValue).copied()
            })
            .filter(|NeighborNode| {
                WitnessNodeIndices.contains(NeighborNode)
                    && RepeaterEndpoints
                        .get(NeighborNode)
                        .is_none_or(|(InputNode, _OutputNode)| *InputNode == CurrentNode)
            })
            .collect::<Vec<_>>()
        };
        for CandidateNode in CandidateNodes {
            let Some(NeighborLocalIndex) = LocalIndexByNode.get(&CandidateNode).copied()
            else {
                continue;
            };
            Adjacency[LocalIndex].push(NeighborLocalIndex);
            ReverseAdjacency[NeighborLocalIndex].push(LocalIndex);
        }
        Adjacency[LocalIndex].sort_unstable();
        Adjacency[LocalIndex].dedup();
    }

    // One strongly-connected-components pass replaces a separate physical
    // reachability search for every repeater.  A repeater is self-exciting
    // exactly when its input, body, and output lie in the same directed SCC.
    let mut Visited = vec![false; OrderedNodeIndices.len()];
    let mut FinishOrder = Vec::with_capacity(OrderedNodeIndices.len());
    for Start in 0..OrderedNodeIndices.len() {
        if Visited[Start] {
            continue;
        }
        Visited[Start] = true;
        let mut Pending = vec![(Start, 0usize)];
        while let Some((Current, NextIndex)) = Pending.last_mut() {
            if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            WorkCount = WorkCount.saturating_add(1);
            if *NextIndex < Adjacency[*Current].len() {
                let Neighbor = Adjacency[*Current][*NextIndex];
                *NextIndex += 1;
                if !Visited[Neighbor] {
                    Visited[Neighbor] = true;
                    Pending.push((Neighbor, 0));
                }
            } else {
                let (Finished, _NextIndex) = Pending.pop().expect("pending DFS is non-empty");
                FinishOrder.push(Finished);
            }
        }
    }
    let mut ComponentByLocalIndex = vec![usize::MAX; OrderedNodeIndices.len()];
    let mut ComponentIndex = 0usize;
    for Start in FinishOrder.into_iter().rev() {
        if ComponentByLocalIndex[Start] != usize::MAX {
            continue;
        }
        ComponentByLocalIndex[Start] = ComponentIndex;
        let mut Pending = vec![Start];
        while let Some(Current) = Pending.pop() {
            if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            WorkCount = WorkCount.saturating_add(1);
            for Neighbor in ReverseAdjacency[Current].iter().copied() {
                if ComponentByLocalIndex[Neighbor] == usize::MAX {
                    ComponentByLocalIndex[Neighbor] = ComponentIndex;
                    Pending.push(Neighbor);
                }
            }
        }
        ComponentIndex = ComponentIndex.saturating_add(1);
    }
    for (RepeaterNode, (InputNode, OutputNode)) in RepeaterEndpoints {
        let Some(RepeaterLocalIndex) = LocalIndexByNode.get(RepeaterNode).copied() else {
            continue;
        };
        let Some(InputLocalIndex) = LocalIndexByNode.get(InputNode).copied() else {
            continue;
        };
        let Some(OutputLocalIndex) = LocalIndexByNode.get(OutputNode).copied() else {
            continue;
        };
        let Component = ComponentByLocalIndex[RepeaterLocalIndex];
        if ComponentByLocalIndex[InputLocalIndex] == Component
            && ComponentByLocalIndex[OutputLocalIndex] == Component
        {
            return Some(true);
        }
    }
    Some(false)
}

fn LayeredGuideAccessBundleHasPoweredTreeWitness(
    IndexedGraph: &IndexedEscapeGraph,
    Workspace: &mut LayeredPoweredWitnessWorkspace,
    GuideClaims: &DeferredAccessCandidateValue,
    DetailedHintPaths: &[Vec<Position>],
    TerminalVariables: &[String],
    SelectedByDomain: &[&DeferredAccessCandidateValue],
    DetachedSeedAccessPaths: &[Vec<Position>],
    SourceTerminalVariable: Option<&str>,
    SourceDetachedAnchorIndex: Option<usize>,
    Deadline: &RuntimeDeadline,
) -> Option<Option<(Vec<Vec<Position>>, Vec<(Position, String)>)>> {
    Workspace.Reset();
    if !GuideClaims
        .OrderedWire
        .iter()
        .copied()
        .chain(
            SelectedByDomain
                .iter()
                .flat_map(|Candidate| Candidate.OrderedWire.iter().copied()),
        )
        .chain(DetachedSeedAccessPaths.iter().flatten().copied())
        .chain(DetailedHintPaths.iter().flatten().copied())
        .all(|PositionValue| Workspace.AddWirePosition(IndexedGraph, PositionValue))
    {
        return Some(None);
    }
    let RootDomainIndex = SourceTerminalVariable
        .and_then(|SourceVariable| {
            TerminalVariables
                .iter()
                .position(|Variable| Variable == SourceVariable)
        });
    let mut SourcePaths = Vec::<&[Position]>::new();
    let mut TargetPaths = Vec::<&[Position]>::new();
    if let Some(RootDomainIndex) = RootDomainIndex {
        SourcePaths.push(SelectedByDomain[RootDomainIndex].OrderedWire.as_slice());
        TargetPaths.extend(
            SelectedByDomain
                .iter()
                .enumerate()
                .filter(|(DomainIndex, _Candidate)| *DomainIndex != RootDomainIndex)
                .map(|(_DomainIndex, Candidate)| Candidate.OrderedWire.as_slice()),
        );
        TargetPaths.extend(DetachedSeedAccessPaths.iter().map(Vec::as_slice));
    } else if let Some(SourceDetachedAnchorIndex) = SourceDetachedAnchorIndex {
        let Some(SourcePath) = DetachedSeedAccessPaths.get(SourceDetachedAnchorIndex) else {
            return Some(None);
        };
        SourcePaths.push(SourcePath.as_slice());
        TargetPaths.extend(
            SelectedByDomain
                .iter()
                .map(|Candidate| Candidate.OrderedWire.as_slice()),
        );
        TargetPaths.extend(
            DetachedSeedAccessPaths
                .iter()
                .enumerate()
                .filter(|(Index, _Path)| *Index != SourceDetachedAnchorIndex)
                .map(|(_Index, Path)| Path.as_slice()),
        );
    }
    if SourcePaths.is_empty() || TargetPaths.is_empty() {
        return Some(Some((Vec::new(), Vec::new())));
    }
    let mut CanReachAllTargets = |SourcePath: &[Position], TargetPaths: &[&[Position]]| {
        let Some(Source) = SourcePath.first().copied() else {
            return Some(None);
        };
        let Some(SourceIndex) = IndexedGraph.PositionIndices.get(&Source).copied() else {
            return Some(None);
        };
        if !Workspace.WireMask[SourceIndex] {
            return Some(None);
        }
        for TargetPath in TargetPaths {
            let Some(TargetPosition) = TargetPath.first().copied() else {
                return Some(None);
            };
            let Some(TargetIndex) = IndexedGraph.PositionIndices.get(&TargetPosition).copied()
            else {
                return Some(None);
            };
            if !Workspace.WireMask[TargetIndex] || !Workspace.AddTargetIndex(TargetIndex) {
                return Some(None);
            }
        }
        let InitialStateIndex = IndexedGraph.StateIndex(SourceIndex, (0, 0, 0));
        let InitialPoweredStateIndex = PoweredEscapeStateIndex(SourceIndex, (0, 0, 0), 15);
        Workspace.RecordStatePower(InitialStateIndex, 15);
        let mut Pending = VecDeque::from([(
            SourceIndex,
            ESCAPE_INITIAL_DIRECTION_STATE,
            15u8,
        )]);
        let mut ParentByState = HashMap::<usize, usize>::new();
        let mut ReachedTargetStates = Vec::new();
        let mut ReachedTargetCount = 0usize;
        let mut WorkCount = 0usize;
        while let Some((CurrentIndex, PriorDirectionIndex, PowerRemaining)) = Pending.pop_front() {
            if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            WorkCount = WorkCount.saturating_add(1);
            if Workspace.TargetMask[CurrentIndex] {
                Workspace.TargetMask[CurrentIndex] = false;
                ReachedTargetCount += 1;
                ReachedTargetStates.push(
                    (CurrentIndex * ESCAPE_DIRECTION_STATE_COUNT + PriorDirectionIndex)
                        * ESCAPE_POWER_STATE_COUNT
                        + usize::from(PowerRemaining),
                );
                if ReachedTargetCount == Workspace.TouchedTargetIndices.len() {
                    break;
                }
            }
            let Current = IndexedGraph.Positions[CurrentIndex];
            for NextIndex in IndexedGraph.NeighborIndices[CurrentIndex].iter().copied() {
                if !Workspace.WireMask[NextIndex] {
                    continue;
                }
                let Next = IndexedGraph.Positions[NextIndex];
                let DirectionValue = (
                    Next.0 - Current.0,
                    Next.1 - Current.1,
                    Next.2 - Current.2,
                );
                if PriorDirectionIndex != ESCAPE_INITIAL_DIRECTION_STATE {
                    let ReverseDirection = (
                        -DirectionValue.0,
                        -DirectionValue.1,
                        -DirectionValue.2,
                    );
                    if EscapeDirectionStateIndex(ReverseDirection) == PriorDirectionIndex {
                        continue;
                    }
                }
                let DirectionIndex = EscapeDirectionStateIndex(DirectionValue);
                let State = IndexedGraph.StateIndex(NextIndex, DirectionValue);
                let PoweredParentState =
                    (CurrentIndex * ESCAPE_DIRECTION_STATE_COUNT + PriorDirectionIndex)
                        * ESCAPE_POWER_STATE_COUNT
                        + usize::from(PowerRemaining);
                let DustPower = PowerRemaining.saturating_sub(1);
                if DustPower > 0 && Workspace.RecordStatePower(State, DustPower) {
                    let PoweredState =
                        PoweredEscapeStateIndex(NextIndex, DirectionValue, DustPower);
                    ParentByState.insert(PoweredState, PoweredParentState);
                    Pending.push_back((NextIndex, DirectionIndex, DustPower));
                }
                let CanPlaceRepeater = PriorDirectionIndex
                    != ESCAPE_INITIAL_DIRECTION_STATE
                    && PriorDirectionIndex == DirectionIndex
                    && (1..=4).contains(&DirectionIndex)
                    && PowerRemaining < 14;
                if CanPlaceRepeater && Workspace.RecordStatePower(State, 14) {
                    let PoweredState =
                        PoweredEscapeStateIndex(NextIndex, DirectionValue, 14);
                    ParentByState.insert(PoweredState, PoweredParentState);
                    Pending.push_back((NextIndex, DirectionIndex, 14));
                }
            }
        }
        if ReachedTargetCount != Workspace.TouchedTargetIndices.len() {
            return Some(None);
        }

        // Reconstruct the deterministic witness rather than treating
        // directional reachability through a feedback loop as a valid
        // combinational route.  Access conductors are fixed; guide and
        // detailed-hint cells are advisory and become physical only when the
        // settled witness actually uses them.  A strength increase identifies
        // the repeater at the parent state, with its exact input and output
        // neighbors.
        let mut WitnessNodeIndices = SelectedByDomain
            .iter()
            .flat_map(|Candidate| Candidate.OrderedWire.iter())
            .chain(DetachedSeedAccessPaths.iter().flatten())
            .filter_map(|PositionValue| IndexedGraph.PositionIndices.get(PositionValue))
            .copied()
            .collect::<HashSet<_>>();
        let mut RepeaterEndpoints = HashMap::<usize, (usize, usize)>::new();
        let mut WitnessPaths = Vec::<Vec<Position>>::new();
        for TargetState in ReachedTargetStates {
            let mut CurrentState = TargetState;
            let mut ReconstructedStates = HashSet::new();
            let mut ReconstructedPath = Vec::<Position>::new();
            while CurrentState != InitialPoweredStateIndex {
                if !ReconstructedStates.insert(CurrentState) {
                    return Some(None);
                }
                if ReconstructedStates.len() % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                let Some(ParentState) = ParentByState.get(&CurrentState).copied() else {
                    return Some(None);
                };
                let CurrentNode = PoweredEscapeStateNodeIndex(CurrentState);
                let ParentNode = PoweredEscapeStateNodeIndex(ParentState);
                WitnessNodeIndices.insert(CurrentNode);
                WitnessNodeIndices.insert(ParentNode);
                ReconstructedPath.push(IndexedGraph.Positions[CurrentNode]);
                let CurrentPower = (CurrentState % ESCAPE_POWER_STATE_COUNT) as u8;
                let ParentPower = (ParentState % ESCAPE_POWER_STATE_COUNT) as u8;
                if CurrentPower > ParentPower {
                    let Some(InputState) = ParentByState.get(&ParentState).copied() else {
                        return Some(None);
                    };
                    let InputNode = PoweredEscapeStateNodeIndex(InputState);
                    if RepeaterEndpoints
                        .insert(ParentNode, (InputNode, CurrentNode))
                        .is_some_and(|Existing| Existing != (InputNode, CurrentNode))
                    {
                        return Some(None);
                    }
                }
                CurrentState = ParentState;
            }
            ReconstructedPath.push(Source);
            ReconstructedPath.reverse();
            ReconstructedPath.dedup();
            WitnessPaths.push(ReconstructedPath);
        }
        let HasCycle = LayeredPoweredWitnessHasSelfExcitingCycle(
            IndexedGraph,
            &WitnessNodeIndices,
            &RepeaterEndpoints,
            Deadline,
        )?;
        if HasCycle {
            return Some(None);
        }
        let mut WitnessRepeaters = Vec::<(Position, String)>::new();
        for (RepeaterNode, (InputNode, OutputNode)) in RepeaterEndpoints {
            let Repeater = IndexedGraph.Positions[RepeaterNode];
            let Input = IndexedGraph.Positions[InputNode];
            let Output = IndexedGraph.Positions[OutputNode];
            let Delta = (
                Output.0 - Repeater.0,
                Output.1 - Repeater.1,
                Output.2 - Repeater.2,
            );
            if Input
                != (
                    Repeater.0 - Delta.0,
                    Repeater.1 - Delta.1,
                    Repeater.2 - Delta.2,
                )
            {
                return Some(None);
            }
            let Facing = match Delta {
                (1, 0, 0) => "west",
                (-1, 0, 0) => "east",
                (0, 0, 1) => "north",
                (0, 0, -1) => "south",
                _ => return Some(None),
            };
            WitnessRepeaters.push((Repeater, Facing.to_owned()));
        }
        WitnessRepeaters.sort_unstable();
        Some(Some((WitnessPaths, WitnessRepeaters)))
    };
    CanReachAllTargets(SourcePaths[0], &TargetPaths)
}

fn LayeredGuideHasSelfLegalAccessBundle<'a>(
    GuideClaims: &DeferredAccessCandidateValue,
    FixedBaseValues: &[&DeferredAccessCandidateValue],
    TerminalVariables: &[String],
    PortalTuple: &[&DeferredAccessCandidateValue],
    Domains: &[Vec<&'a DeferredAccessCandidateValue>],
    PreferredRequirements: &[Vec<(String, String)>],
    Deadline: &RuntimeDeadline,
) -> Result<(
    Vec<Vec<(String, String)>>,
    BTreeSet<(String, String)>,
    usize,
), ()> {
    if TerminalVariables.len() != PortalTuple.len() || Domains.len() != PortalTuple.len() {
        return Ok((Vec::new(), BTreeSet::new(), 0));
    }
    let MatchingDomains = Domains
        .iter()
        .zip(PortalTuple)
        .enumerate()
        .map(|(DomainIndex, (Domain, PortalValue))| {
            let mut Matching = Domain
                .iter()
                .copied()
                .filter(|Access| Access.Portal == PortalValue.Portal)
                .collect::<Vec<_>>();
            Matching.sort_by_key(|Access| {
                (
                    PreferredRequirements
                        .iter()
                        .position(|Requirements| {
                            Requirements.iter().any(|(Variable, CandidateId)| {
                                Variable == &TerminalVariables[DomainIndex]
                                    && CandidateId == &Access.CandidateId
                            })
                        })
                        .unwrap_or(usize::MAX),
                    Access.CandidateId.as_str(),
                )
            });
            Matching
        })
        .collect::<Vec<_>>();
    if MatchingDomains.iter().any(Vec::is_empty) {
        return Ok((Vec::new(), BTreeSet::new(), 0));
    }
    let MatchingDomains = MatchingDomains
        .into_iter()
        .map(|Domain| {
            Domain
                .into_iter()
                .filter(|Candidate| {
                    !DeferredAccessCandidatesConflict(GuideClaims, Candidate)
                        && FixedBaseValues.iter().all(|BaseValue| {
                            !DeferredAccessCandidatesConflict(BaseValue, Candidate)
                        })
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    if MatchingDomains.iter().any(Vec::is_empty) {
        return Ok((Vec::new(), BTreeSet::new(), 0));
    }
    let mut DomainOrder = (0..MatchingDomains.len()).collect::<Vec<_>>();
    DomainOrder.sort_by_key(|DomainIndex| (MatchingDomains[*DomainIndex].len(), *DomainIndex));
    let AllChoices = MatchingDomains
        .iter()
        .enumerate()
        .flat_map(|(DomainIndex, Domain)| {
            Domain.iter().map(move |Candidate| {
                (
                    TerminalVariables[DomainIndex].clone(),
                    Candidate.CandidateId.clone(),
                )
            })
        })
        .collect::<BTreeSet<_>>();
    for BaseValue in FixedBaseValues {
        if DeferredAccessCandidatesConflict(GuideClaims, BaseValue) {
            return Ok((Vec::new(), BTreeSet::new(), 0));
        }
    }
    let mut Selected = Vec::<(usize, &'a DeferredAccessCandidateValue)>::new();
    let mut Witnesses = Vec::<Vec<(String, String)>>::new();
    let mut SupportedChoices = BTreeSet::<(String, String)>::new();
    let mut ExpansionCount = 0usize;
    fn Search<'a>(
        OrderIndex: usize,
        DomainOrder: &[usize],
        TerminalVariables: &[String],
        Domains: &[Vec<&'a DeferredAccessCandidateValue>],
        Selected: &mut Vec<(usize, &'a DeferredAccessCandidateValue)>,
        Witnesses: &mut Vec<Vec<(String, String)>>,
        AllChoices: &BTreeSet<(String, String)>,
        SupportedChoices: &mut BTreeSet<(String, String)>,
        GuideClaims: &DeferredAccessCandidateValue,
        Deadline: &RuntimeDeadline,
        ExpansionCount: &mut usize,
    ) -> Result<(), ()> {
        if Deadline.Check() {
            return Err(());
        }
        *ExpansionCount = ExpansionCount.saturating_add(1);
        let Some(DomainIndex) = DomainOrder.get(OrderIndex).copied() else {
            let SelectedByDomain = (0..TerminalVariables.len())
                .map(|DomainIndex| {
                    Selected
                        .iter()
                        .find(|(SelectedDomainIndex, _Candidate)| {
                            *SelectedDomainIndex == DomainIndex
                        })
                        .map(|(_SelectedDomainIndex, Candidate)| *Candidate)
                        .expect("complete layered guide witness owns every terminal")
                })
                .collect::<Vec<_>>();
            let Requirements = SelectedByDomain
                .iter()
                .enumerate()
                .map(|(DomainIndex, Candidate)| {
                    (
                        TerminalVariables[DomainIndex].clone(),
                        Candidate.CandidateId.clone(),
                    )
                })
                .collect::<Vec<_>>();
            if SupportedChoices != AllChoices {
                SupportedChoices.extend(Requirements.iter().cloned());
            }
            Witnesses.push(Requirements);
            return Ok(());
        };
        for Candidate in &Domains[DomainIndex] {
            if Selected.iter().any(|(PreviousIndex, Previous)| {
                TerminalVariables[*PreviousIndex] == TerminalVariables[DomainIndex]
                    && Previous.CandidateId != Candidate.CandidateId
                    || DeferredAccessCandidatesConflict(Candidate, Previous)
            })
            {
                continue;
            }
            Selected.push((DomainIndex, Candidate));
            Search(
                OrderIndex + 1,
                DomainOrder,
                TerminalVariables,
                Domains,
                Selected,
                Witnesses,
                AllChoices,
                SupportedChoices,
                GuideClaims,
                Deadline,
                ExpansionCount,
            )?;
            Selected.pop();
        }
        Ok(())
    }
    Search(
        0,
        &DomainOrder,
        TerminalVariables,
        &MatchingDomains,
        &mut Selected,
        &mut Witnesses,
        &AllChoices,
        &mut SupportedChoices,
        GuideClaims,
        Deadline,
        &mut ExpansionCount,
    )?;
    // Search order is already deterministic and reflects the physical access
    // preference supplied by the preceding portal tuple.  Sorting opaque stub
    // identifiers here discards that ordering and can bind an otherwise good
    // guide to a remote representative path.  Each complete assignment is
    // unique because every terminal variable is selected exactly once.
    Ok((Witnesses, SupportedChoices, ExpansionCount))
}

fn BuildLayeredGuideNecessaryAccessRamps(
    GraphAdjacency: &HashMap<Position, Vec<Position>>,
    IndexedGraph: &IndexedEscapeGraph,
    ProofCounters: &LayeredGuideAccessRampCache,
    Guide: &[Position],
    GuideExpansion: usize,
    PortalTuple: &[&DeferredAccessCandidateValue],
    BaseClaimIndex: &LayeredFrozenBaseClaimIndex,
    RequiredWire: &HashSet<Position>,
    ForeignBlockedNodes: &HashSet<Position>,
    OwnerSignal: &str,
    DetachedSeedAccessPaths: &[Vec<Position>],
    SourceDetachedAnchorIndex: Option<usize>,
    Deadline: &RuntimeDeadline,
) -> Option<LayeredGuideAccessRampResult> {
    let GuideColumns = Guide
        .iter()
        .map(|(X, _Y, Z)| (*X, *Z))
        .collect::<BTreeSet<_>>();
    let Expansion = GuideExpansion.min(i32::MAX as usize) as i32;
    let mut AllowedColumns = GuideColumns
        .iter()
        .flat_map(|(GuideX, GuideZ)| {
            (-Expansion..=Expansion).flat_map(move |DeltaX| {
                (-Expansion..=Expansion).filter_map(move |DeltaZ| {
                    (DeltaX.abs() + DeltaZ.abs() <= Expansion)
                        .then_some((*GuideX + DeltaX, *GuideZ + DeltaZ))
                })
            })
        })
        .collect::<HashSet<_>>();
    // Fixed internal pin attachments can end inside a component access pocket
    // that is not itself inside the guide corridor.  Retain the complete
    // ordered attachment paths and admit an equally bounded corridor around
    // them so the native catalog owns the exact graph path from the physical
    // pin attachment to the guide.  Only the selected shortest ramp is
    // reserved; this does not turn the expanded corridor into capacity.
    AllowedColumns.extend(
        DetachedSeedAccessPaths
            .iter()
            .flatten()
            .flat_map(|(PathX, _PathY, PathZ)| {
                (-Expansion..=Expansion).flat_map(move |DeltaX| {
                    (-Expansion..=Expansion).filter_map(move |DeltaZ| {
                        (DeltaX.abs() + DeltaZ.abs() <= Expansion)
                            .then_some((*PathX + DeltaX, *PathZ + DeltaZ))
                    })
                })
            }),
    );
    // A compact guide factor owns portal endpoints, not one arbitrary full
    // escape path used while enumerating that endpoint.  Exact access paths
    // remain separate variables in the shared claim solver, which rejects
    // every incompatible guide/stub combination.  Treating the enumerator's
    // representative path as mandatory here could discard a guide even when
    // another candidate ending at the same portal was compatible.
    // Necessary connectivity must use the same immutable foreign-claim
    // vocabulary as selected-world routing.  A wire node cannot occupy a
    // foreign electrical, support, or air claim, and its support cannot
    // replace a foreign wire/air block one level below.  Project only these
    // exact unary contradictions here; vertical edge headroom remains part
    // of the later complete claim check because it depends on a node pair.
    // Do not materialize the allowed-node set by scanning the complete graph
    // for every guide candidate.  The guide corridor is already represented
    // by an exact column set, so membership can be tested lazily while the
    // bounded traversal visits nodes.  RCA-sized portfolios contain thousands
    // of guide candidates over the same graph; the old full-graph scan made
    // certification proportional to candidates times every graph node even
    // when only a small corridor was reachable.
    let IsAllowed = |PositionValue: &Position| {
        RequiredWire.contains(PositionValue)
            || (
                !ForeignBlockedNodes.contains(PositionValue)
                    && AllowedColumns.contains(&(
                        PositionValue.0,
                        PositionValue.2,
                    ))
            )
    };
    let Terminals = PortalTuple
        .iter()
        .map(|Value| Value.Portal)
        .chain(
            DetachedSeedAccessPaths
                .iter()
                .filter_map(|Path| Path.last().copied()),
        )
        .collect::<BTreeSet<_>>();
    let GuideNodes = Guide
        .iter()
        .copied()
        .filter(|PositionValue| {
            GraphAdjacency.contains_key(PositionValue) && IsAllowed(PositionValue)
        })
        .collect::<HashSet<_>>();
    if Terminals.is_empty()
        || GuideNodes.is_empty()
        || Terminals
            .iter()
            .any(|PositionValue| {
                !GraphAdjacency.contains_key(PositionValue) || !IsAllowed(PositionValue)
            })
    {
        if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL")
            .ok()
            .as_deref()
            == Some(OwnerSignal)
        {
            eprintln!(
                "native layered ramp validation signal={} terminals={:?} missing_terminals={:?} guide_nodes={} raw_guide_nodes={}",
                OwnerSignal,
                Terminals,
                Terminals
                    .iter()
                    .filter(|PositionValue| !GraphAdjacency.contains_key(PositionValue))
                    .collect::<Vec<_>>(),
                GuideNodes.len(),
                Guide.len(),
            );
        }
        return Some(None);
    }
    let mut ExpansionCount = 0usize;
    let mut AccessRamps = Vec::new();
    let mut ConnectivityWitnessPaths = Vec::<Vec<Position>>::new();
    let mut PhysicalGuide = GuideNodes.iter().copied().collect::<Vec<_>>();
    PhysicalGuide.sort_unstable();
    let NeedsExactRamp = !PortalTuple.is_empty() && !DetachedSeedAccessPaths.is_empty();
    if !NeedsExactRamp {
        let mut Reached = GuideNodes.clone();
        let mut ParentTowardGuide = HashMap::<Position, Position>::new();
        let mut Pending = VecDeque::from(PhysicalGuide.clone());
        let mut UnreachedTerminals = Terminals
            .iter()
            .copied()
            .filter(|Terminal| !Reached.contains(Terminal))
            .collect::<HashSet<_>>();
        while !UnreachedTerminals.is_empty() {
            let Some(Current) = Pending.pop_front() else {
                return Some(None);
            };
            if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            ExpansionCount = ExpansionCount.saturating_add(1);
            for Neighbor in GraphAdjacency.get(&Current).into_iter().flatten().copied() {
                if !IsAllowed(&Neighbor) || !Reached.insert(Neighbor) {
                    continue;
                }
                ParentTowardGuide.insert(Neighbor, Current);
                UnreachedTerminals.remove(&Neighbor);
                Pending.push_back(Neighbor);
            }
        }
        for Terminal in Terminals.iter().copied() {
            let mut Current = Terminal;
            let mut WitnessPath = vec![Current];
            while !GuideNodes.contains(&Current) {
                Current = ParentTowardGuide[&Current];
                WitnessPath.push(Current);
            }
            ConnectivityWitnessPaths.push(WitnessPath);
            // Exterior portals own their exact shell path through a separate
            // access-stub variable.  This portal-to-guide traversal is not a
            // capacity reservation, but retaining its complete deterministic
            // BFS witness gives selected-world routing a finite exact subgraph
            // to try before opening the whole expanded corridor.
            AccessRamps.push(vec![Terminal]);
        }
    }
    for Terminal in Terminals.iter().copied() {
        if !NeedsExactRamp {
            continue;
        }
        let mut Reached = HashSet::from([Terminal]);
        let mut Parent = HashMap::<Position, Position>::new();
        let mut Pending = VecDeque::from([Terminal]);
        let mut CandidateGuideNodes = GuideNodes
            .contains(&Terminal)
            .then_some(vec![Terminal])
            .unwrap_or_default();
        let SelectedPath = loop {
            if !CandidateGuideNodes.is_empty() {
                let mut CandidatePaths = CandidateGuideNodes
                    .drain(..)
                    .map(|GuideNode| {
                        let mut Current = GuideNode;
                        let mut Path = vec![Current];
                        while Current != Terminal {
                            Current = Parent[&Current];
                            Path.push(Current);
                        }
                        Path.reverse();
                        Path
                    })
                    .collect::<Vec<_>>();
                // This is the same (length, path) order as the previous
                // complete-corridor enumeration.  Processing one BFS layer at
                // a time lets us stop only after the first self-legal shortest
                // ramp has been proved, without dropping any finite shape.
                CandidatePaths.sort();
                if let Some(Path) = CandidatePaths.into_iter().find(|CandidatePath| {
                let CombinedWire = PhysicalGuide
                    .iter()
                    .copied()
                    .chain(DetachedSeedAccessPaths.iter().flatten().copied())
                    .chain(AccessRamps.iter().flatten().copied())
                    .chain(CandidatePath.iter().copied())
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>();
                let Some(Claims) = BuildDeferredAccessCandidate(
                    format!("__route_guide_bundle__:{}", OwnerSignal),
                    format!("__route_guide_bundle__:{}", OwnerSignal),
                    OwnerSignal.to_string(),
                    Guide.first().map(|Value| Value.1).unwrap_or(Terminal.1),
                    CombinedWire,
                ) else {
                    return false;
                };
                !BaseClaimIndex.Conflicts(&Claims)
                }) {
                    break Some(Path);
                }
            }
            let CurrentLayerSize = Pending.len();
            if CurrentLayerSize == 0 {
                break None;
            }
            let mut NextGuideNodes = Vec::new();
            for _ in 0..CurrentLayerSize {
                let Current = Pending
                    .pop_front()
                    .expect("current BFS layer retains every queued node");
                if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                ExpansionCount = ExpansionCount.saturating_add(1);
                for Neighbor in GraphAdjacency
                    .get(&Current)
                    .into_iter()
                    .flatten()
                    .copied()
                {
                    if !IsAllowed(&Neighbor) || !Reached.insert(Neighbor) {
                        continue;
                    }
                    Parent.insert(Neighbor, Current);
                    if GuideNodes.contains(&Neighbor) {
                        NextGuideNodes.push(Neighbor);
                    }
                    Pending.push_back(Neighbor);
                }
            }
            CandidateGuideNodes = NextGuideNodes;
        };
        let Some(Path) = SelectedPath else {
            return Some(None);
        };
        ConnectivityWitnessPaths.push(Path.clone());
        AccessRamps.push(Path);
    }
    let mut PoweredCorridorHint = true;
    if PortalTuple.is_empty() && DetachedSeedAccessPaths.len() == 2 {
        PoweredCorridorHint = false;
        let HintWire = PhysicalGuide
            .iter()
            .copied()
            .chain(DetachedSeedAccessPaths.iter().flatten().copied())
            .chain(ConnectivityWitnessPaths.iter().flatten().copied())
            .collect::<BTreeSet<_>>();
        let HintClaims = BuildDeferredAccessCandidate(
            format!("__route_guide_hint__:{OwnerSignal}"),
            format!("__route_guide_hint__:{OwnerSignal}"),
            OwnerSignal.to_string(),
            Guide.first().map(|Value| Value.1).unwrap_or(0),
            HintWire.iter().copied().collect(),
        )
        .filter(|Claims| !BaseClaimIndex.Conflicts(Claims));
        if let Some(SourceDetachedAnchorIndex) =
            SourceDetachedAnchorIndex.filter(|Index| *Index < 2)
            .filter(|_Index| HintClaims.is_some())
        {
            let TargetDetachedAnchorIndex = 1usize - SourceDetachedAnchorIndex;
            if let (Some(SourcePath), Some(TargetIndex)) = (
                DetachedSeedAccessPaths.get(SourceDetachedAnchorIndex),
                DetachedSeedAccessPaths
                    .get(TargetDetachedAnchorIndex)
                    .and_then(|Path| Path.last())
                    .and_then(|Target| IndexedGraph.PositionIndices.get(Target))
                    .copied(),
            ) {
            let mut BestPowerByState = vec![
                0u8;
                IndexedGraph
                    .Positions
                    .len()
                    .saturating_mul(ESCAPE_DIRECTION_STATE_COUNT)
            ];
            let mut Pending = VecDeque::new();
            let mut PriorPosition = None;
            let mut PowerRemaining =
                crate::PathRouting::MAXIMUM_UNREFRESHED_DUST_LENGTH;
            for PositionValue in SourcePath.iter().copied() {
                let Some(PositionIndex) =
                    IndexedGraph.PositionIndices.get(&PositionValue).copied()
                else {
                    continue;
                };
                let DirectionValue = PriorPosition
                    .map(|Prior: Position| {
                        (
                            PositionValue.0 - Prior.0,
                            PositionValue.1 - Prior.1,
                            PositionValue.2 - Prior.2,
                        )
                    })
                    .unwrap_or((0, 0, 0));
                let DirectionIndex = EscapeDirectionStateIndex(DirectionValue);
                let StateIndex = IndexedGraph.StateIndex(PositionIndex, DirectionValue);
                if PowerRemaining > BestPowerByState[StateIndex] {
                    BestPowerByState[StateIndex] = PowerRemaining;
                    Pending.push_back((PositionIndex, DirectionIndex, PowerRemaining));
                }
                PriorPosition = Some(PositionValue);
                PowerRemaining = PowerRemaining.saturating_sub(1);
            }
            let mut PoweredExpansionCount = 0usize;
            while let Some((CurrentIndex, PriorDirectionIndex, CurrentPower)) =
                Pending.pop_front()
            {
                if PoweredExpansionCount % DEADLINE_CHECK_INTERVAL == 0
                    && Deadline.Check()
                {
                    return None;
                }
                PoweredExpansionCount = PoweredExpansionCount.saturating_add(1);
                if CurrentIndex == TargetIndex {
                    PoweredCorridorHint = true;
                    break;
                }
                let Current = IndexedGraph.Positions[CurrentIndex];
                for NextIndex in IndexedGraph.NeighborIndices[CurrentIndex].iter().copied() {
                    let Next = IndexedGraph.Positions[NextIndex];
                    if !HintWire.contains(&Next) {
                        continue;
                    }
                    let DirectionValue = (
                        Next.0 - Current.0,
                        Next.1 - Current.1,
                        Next.2 - Current.2,
                    );
                    let DirectionIndex = EscapeDirectionStateIndex(DirectionValue);
                    let NextPower = if PriorDirectionIndex
                        != ESCAPE_INITIAL_DIRECTION_STATE
                        && PriorDirectionIndex == DirectionIndex
                        && (1..=4).contains(&DirectionIndex)
                    {
                        crate::PathRouting::MAXIMUM_UNREFRESHED_DUST_LENGTH
                    } else {
                        CurrentPower.saturating_sub(1)
                    };
                    if NextPower == 0 {
                        continue;
                    }
                    let NextStateIndex = IndexedGraph.StateIndex(NextIndex, DirectionValue);
                    if NextPower <= BestPowerByState[NextStateIndex] {
                        continue;
                    }
                    BestPowerByState[NextStateIndex] = NextPower;
                    Pending.push_back((NextIndex, DirectionIndex, NextPower));
                }
            }
            ProofCounters
                .ExhaustivePoweredProofCount
                .fetch_add(1, Ordering::Relaxed);
            if PoweredCorridorHint {
                ProofCounters
                    .KnownPoweredWitnessCount
                    .fetch_add(1, Ordering::Relaxed);
            } else if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL")
                .ok()
                .as_deref()
                == Some(OwnerSignal)
            {
                eprintln!(
                    "native layered fixed-factor powered hint missing signal={} expansions={}",
                    OwnerSignal,
                    PoweredExpansionCount,
                );
            }
        }
        }
    }
    Some(Some((
        PhysicalGuide,
        AccessRamps,
        ConnectivityWitnessPaths,
        PoweredCorridorHint,
    )))
}

fn LayeredGuideControlsHaveFixedBaseConflict(
    Controls: &LayeredAccessGuideControlsValue,
) -> bool {
    let BaseValues = Controls
        .10
        .iter()
        .enumerate()
        .map(|(Index, (Signal, Wire, Support, Air, Electrical))| {
            let Sorted = |Values: &[Position]| {
                Values
                    .iter()
                    .copied()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect()
            };
            DeferredAccessCandidateValue {
                Variable: format!("__base_claim__:{}:{}", Signal, Index),
                CandidateId: format!("__base_claim_value__:{}:{}", Signal, Index),
                OwnerSignal: Signal.clone(),
                IngressY: 0,
                Portal: Wire.first().copied().unwrap_or((0, 0, 0)),
                OrderedWire: Wire.to_vec(),
                Wire: Sorted(Wire),
                Support: Sorted(Support),
                Air: Sorted(Air),
                Electrical: Sorted(Electrical),
            }
        })
        .collect::<Vec<_>>();
    BaseValues.iter().enumerate().any(|(FirstIndex, First)| {
        BaseValues
            .iter()
            .skip(FirstIndex + 1)
            .any(|Second| DeferredAccessCandidatesConflict(First, Second))
    })
}

fn BuildLayeredAccessGuideCandidateGroups(
    Requests: &[EscapeRequest],
    RequestResults: &[EscapeRequestResult],
    RequestMetadata: &[(String, String, String)],
    Controls: &LayeredAccessGuideControlsValue,
    GraphAdjacencyValues: &[(Position, Vec<Position>)],
    MemberIndex: usize,
    GraphIndex: usize,
    MaximumY: i32,
    RequirePowerCertifiedAccess: bool,
    SharedAccessRampCache: &LayeredGuideAccessRampCache,
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<PreparedLayeredAccessGuideDomain>> {
    let DomainStartedAt = Instant::now();
    let mut DebugStageStartedAt = Instant::now();
    let Some((RequiredVariables, AllAccessValues)) =
        BuildDeferredLayeredAccessCandidates(Requests, RequestResults, RequestMetadata, Deadline)?
    else {
        return Ok(None);
    };
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered domain stage=deferred-access values={} elapsed={:.3}s",
            AllAccessValues.len(),
            DebugStageStartedAt.elapsed().as_secs_f64(),
        );
    }
    DebugStageStartedAt = Instant::now();
    let (
        RoutingYs,
        MinimumX,
        MinimumZ,
        TrackPitch,
        LaneCount,
        MaximumShapesPerSignal,
        GuideExpansion,
        RegionExpansion,
        FabricNodeCandidateValues,
        SignalValues,
        BaseClaimValues,
        DetachedSeedAnchorValues,
    ) = Controls;
    if RoutingYs.is_empty() || *TrackPitch < 2 || *LaneCount < 1 || *MaximumShapesPerSignal < 1 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access guide controls require routing planes, pitch, lanes, and shapes",
        ));
    }
    let SourceAccessVariables = SignalValues
        .iter()
        .filter_map(
            |(
                _Signal,
                _TerminalVariables,
                _VariantCount,
                _RegionTerminals,
                SourceTerminalVariable,
                _SourceDetachedAnchorIndex,
            )| SourceTerminalVariable.clone(),
        )
        .collect::<HashSet<_>>();
    let AccessValues = if RequirePowerCertifiedAccess {
        AllAccessValues
            .into_iter()
            .filter(|Access| {
                ExactLayeredAccessPathCanCarryPower(
                    SourceAccessVariables.contains(&Access.Variable),
                    &Access.OrderedWire,
                )
            })
            .collect::<Vec<_>>()
    } else {
        AllAccessValues
    };
    let BaseValues = BaseClaimValues
        .iter()
        .enumerate()
        .map(|(Index, (Signal, Wire, Support, Air, Electrical))| {
            let Sorted = |Values: &[Position]| {
                Values
                    .iter()
                    .copied()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect()
            };
            DeferredAccessCandidateValue {
                Variable: format!("__base_claim__:{}:{}", Signal, Index),
                CandidateId: format!("__base_claim_value__:{}:{}", Signal, Index),
                OwnerSignal: Signal.clone(),
                IngressY: 0,
                Portal: Wire.first().copied().unwrap_or((0, 0, 0)),
                OrderedWire: Wire.to_vec(),
                Wire: Sorted(Wire),
                Support: Sorted(Support),
                Air: Sorted(Air),
                Electrical: Sorted(Electrical),
            }
        })
        .collect::<Vec<_>>();
    let BaseValuesByOwner = BaseValues.iter().fold(
        HashMap::<String, Vec<&DeferredAccessCandidateValue>>::new(),
        |mut Result, Value| {
            Result
                .entry(Value.OwnerSignal.clone())
                .or_default()
                .push(Value);
            Result
        },
    );
    let RequiredWireByOwner = SignalValues
        .iter()
        .map(|(
            Signal,
            _TerminalVariables,
            _PortalVariantLimit,
            _RegionTerminals,
            _SourceTerminalVariable,
            _SourceDetachedAnchorIndex,
        )| {
            let RequiredWire = BaseValuesByOwner
                .get(Signal)
                .into_iter()
                .flatten()
                .flat_map(|Value| Value.Wire.iter().copied())
                .collect::<HashSet<_>>();
            (Signal.clone(), RequiredWire)
        })
        .collect::<HashMap<_, _>>();
    let ForeignBlockedNodesByOwner = SignalValues
        .iter()
        .map(|(
            Signal,
            _TerminalVariables,
            _PortalVariantLimit,
            _RegionTerminals,
            _SourceTerminalVariable,
            _SourceDetachedAnchorIndex,
        )| {
            let ForeignBlockedNodes = BaseValues
                .iter()
                .filter(|Value| Value.OwnerSignal != *Signal)
                .flat_map(|Value| {
                    Value
                        .Electrical
                        .iter()
                        .chain(&Value.Support)
                        .chain(&Value.Air)
                        .copied()
                        .chain(
                            Value
                                .Wire
                                .iter()
                                .chain(&Value.Air)
                                .map(|PositionValue| {
                                    (
                                        PositionValue.0,
                                        PositionValue.1 + 1,
                                        PositionValue.2,
                                    )
                                }),
                        )
                })
                .collect::<HashSet<_>>();
            (Signal.clone(), ForeignBlockedNodes)
        })
        .collect::<HashMap<_, _>>();
    if BaseValues.iter().enumerate().any(|(FirstIndex, First)| {
        BaseValues
            .iter()
            .skip(FirstIndex + 1)
            .any(|Second| DeferredAccessCandidatesConflict(First, Second))
    }) {
        // Base claims are already frozen into the selected placement. They
        // are constraints, not assignment choices. A contradictory fixed
        // base makes this non-exhaustive member incomplete without exposing
        // the claims as synthetic singleton variables to the solver.
        return Ok(Some((
            BTreeMap::from([("__fixed_base_claim_conflict__".to_string(), Vec::new())]),
            1,
            HashMap::new(),
            Arc::new(vec![Vec::new()]),
        )));
    }
    let BaseClaimIndex = Arc::new(LayeredFrozenBaseClaimIndex::New(&BaseValues));
    // Frozen placement-owned claims are part of the same physical world as
    // every access path.  Reject an access value that contradicts them before
    // it can become either an assignment value or a guide witness.  The old
    // guide-only check allowed native selection to choose a stub whose merged
    // selected-world claims were rejected during Python handoff.
    let AccessValues = AccessValues
        .into_iter()
        .filter(|Access| !BaseClaimIndex.Conflicts(Access))
        .collect::<Vec<_>>();
    // Guide requirements and access variables must refer to the same exact
    // finite physical domain.  Powered alternatives are distinct paths, not
    // compatibility-only witnesses, and subset dominance is unsound because
    // a longer path can provide the only legal repeater sites.
    let GuideAccessValues = AccessValues.clone();
    let AccessByVariable = GuideAccessValues.iter().fold(
        BTreeMap::<String, Vec<&DeferredAccessCandidateValue>>::new(),
        |mut Result, Value| {
            Result
                .entry(Value.Variable.clone())
                .or_default()
                .push(Value);
            Result
        },
    );
    let CompleteAccessWitness =
        Arc::new(FindCompleteLayeredAccessWitness(&AccessByVariable, Deadline));
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered domain stage=access-base-witness values={} base_values={} elapsed={:.3}s",
            GuideAccessValues.len(),
            BaseValues.len(),
            DebugStageStartedAt.elapsed().as_secs_f64(),
        );
    }
    DebugStageStartedAt = Instant::now();
    let DetachedSeedAnchorsByOwner = DetachedSeedAnchorValues
        .iter()
        .map(|(Signal, Anchors)| (Signal.as_str(), Anchors.as_slice()))
        .collect::<HashMap<_, _>>();
    let GraphAdjacency = Arc::new(
        GraphAdjacencyValues
            .iter()
            .filter(|(PositionValue, _Neighbors)| PositionValue.1 <= MaximumY)
            .map(|(PositionValue, Neighbors)| {
                (
                    *PositionValue,
                    Neighbors
                        .iter()
                        .copied()
                        .filter(|Neighbor| Neighbor.1 <= MaximumY)
                        .collect::<Vec<_>>(),
                )
            })
            .collect::<HashMap<_, _>>(),
    );
    let IndexedGraph = Arc::new(IndexedEscapeGraph::New(&GraphAdjacency));
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered domain stage=indexed-graph nodes={} elapsed={:.3}s",
            GraphAdjacency.len(),
            DebugStageStartedAt.elapsed().as_secs_f64(),
        );
    }
    DebugStageStartedAt = Instant::now();
    let mut MemberAssignedColumns = FabricNodeCandidateValues
        .iter()
        .copied()
        .filter(|PositionValue| GraphAdjacency.contains_key(PositionValue))
        .map(|PositionValue| (PositionValue.0, PositionValue.2))
        .collect::<HashSet<_>>();
    let AccessAssignedColumns = GuideAccessValues
        .iter()
        .flat_map(|Value| Value.Wire.iter())
        .map(|PositionValue| (PositionValue.0, PositionValue.2))
        .collect::<HashSet<_>>();
    MemberAssignedColumns.extend(AccessAssignedColumns.iter().copied());
    let Expansion = (*RegionExpansion).min(i32::MAX as usize) as i32;
    for (
        _Signal,
        _TerminalVariables,
        _PortalVariantLimit,
        RegionTerminals,
        _SourceTerminalVariable,
        _SourceDetachedAnchorIndex,
    ) in SignalValues
    {
        let Terminals = RegionTerminals
            .iter()
            .copied()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if Terminals.is_empty() {
            continue;
        }
        for Axis in ["X", "Z"] {
            let mut Coordinates = Terminals
                .iter()
                .map(|Value| if Axis == "X" { Value.1 } else { Value.0 })
                .collect::<Vec<_>>();
            Coordinates.sort_unstable();
            let Center = Coordinates[Coordinates.len() / 2];
            let TrackAnchor = if Axis == "X" { *MinimumZ } else { *MinimumX };
            let Pitch = *TrackPitch as i32;
            let AlignedCenter =
                TrackAnchor + (Center - TrackAnchor + Pitch / 2).div_euclid(Pitch) * Pitch;
            let Guide = BuildLayeredGuideSpine(&Terminals, Axis, AlignedCenter, RoutingYs[0]);
            MemberAssignedColumns.extend(Guide.iter().flat_map(|(GuideX, _GuideY, GuideZ)| {
                (-Expansion..=Expansion).flat_map(move |DeltaX| {
                    (-Expansion..=Expansion).filter_map(move |DeltaZ| {
                        (DeltaX.abs() + DeltaZ.abs() <= Expansion)
                            .then_some((*GuideX + DeltaX, *GuideZ + DeltaZ))
                    })
                })
            }));
        }
    }
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered domain stage=assigned-columns columns={} elapsed={:.3}s",
            MemberAssignedColumns.len(),
            DebugStageStartedAt.elapsed().as_secs_f64(),
        );
    }
    let mut SignalOrder = (0..SignalValues.len()).collect::<Vec<_>>();
    SignalOrder.sort_by_key(|SignalIndex| {
        let (
            Signal,
            TerminalVariables,
            PortalVariantLimit,
            _RegionTerminals,
            _SourceTerminalVariable,
            _SourceDetachedAnchorIndex,
        ) =
            &SignalValues[*SignalIndex];
        (
            Reverse(TerminalVariables.len()),
            Reverse(LayeredGuideTerminalSpan(TerminalVariables)),
            *PortalVariantLimit,
            Signal.clone(),
            *SignalIndex,
        )
    });
    let FirstEmptySignalOrderIndex = std::sync::atomic::AtomicUsize::new(usize::MAX);
    let GuideValuesBySignal =
        RoutingThreadPool().install(|| {
            SignalOrder
            .par_iter()
            .enumerate()
            .with_max_len(1)
            .map(|(OrderIndex, SignalIndex)| -> PyResult<Option<Vec<DeferredGuideCandidateValue>>> {
        if OrderIndex
            > FirstEmptySignalOrderIndex.load(std::sync::atomic::Ordering::SeqCst)
        {
            return Ok(Some(Vec::new()));
        }
        let SignalIndex = *SignalIndex;
        let (
            Signal,
            TerminalVariables,
            PortalVariantLimit,
            _RegionTerminals,
            SourceTerminalVariable,
            SourceDetachedAnchorIndex,
        ) =
            &SignalValues[SignalIndex];
        let SignalStartedAt = Instant::now();
        let mut PoweredWitnessWorkspace = LayeredPoweredWitnessWorkspace::New(&IndexedGraph);
        if SignalIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        let DetachedSeedAnchors = DetachedSeedAnchorsByOwner
            .get(Signal.as_str())
            .copied()
            .unwrap_or(&[]);
        if Signal.is_empty()
            || TerminalVariables.len() + DetachedSeedAnchors.len() < 2
            || *PortalVariantLimit < 1
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "layered guide signals require an owner, source, target, and portal limit",
            ));
        }
        if TerminalVariables
            .iter()
            .any(|Variable| !RequiredVariables.contains_key(Variable))
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "layered guide signal references an unknown access variable",
            ));
        }
        let mut SignalShapes = Vec::<DeferredGuideCandidateValue>::new();
        let mut ClaimBundleRejectionCount = 0usize;
        let mut AccessRampConnectivityRejectionCount = 0usize;
        let mut AccessRampSelfConflictRejectionCount = 0usize;
        let mut AccessRampBaseConflictRejectionCount = 0usize;
        let mut PoweredTreeRejectionCount = 0usize;
        let mut AccessWitnessExpansionCount = 0usize;
        let mut AccessWitnessByPhysicalGuide = HashMap::<
            (usize, String, i32, Vec<Position>),
            (
                Arc<Vec<Vec<(String, String)>>>,
                BTreeSet<(String, String)>,
            ),
        >::new();
        let mut AccessRampsByPhysicalGuide = HashMap::<
            (usize, String, i32, Vec<Position>),
            LayeredGuideAccessRampResult,
        >::new();
        let mut PreferredAccessWitnessByPortalTuple =
            HashMap::<Vec<Position>, Vec<Vec<(String, String)>>>::new();
        for (LayerIndex, RoutingY) in RoutingYs.iter().enumerate() {
            let Domains = TerminalVariables
                .iter()
                .map(|Variable| {
                    let mut Domain = AccessByVariable
                        .get(Variable)
                        .cloned()
                        .unwrap_or_default();
                    Domain.sort_by(|First, Second| {
                        First
                            .IngressY
                            .abs_diff(*RoutingY)
                            .cmp(&Second.IngressY.abs_diff(*RoutingY))
                            .then_with(|| First.Wire.len().cmp(&Second.Wire.len()))
                            .then_with(|| First.IngressY.cmp(&Second.IngressY))
                            .then_with(|| First.Portal.cmp(&Second.Portal))
                            .then_with(|| First.Wire.cmp(&Second.Wire))
                            .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
                    });
                    Domain
                })
                .collect::<Vec<_>>();
            if Domains.iter().any(Vec::is_empty) {
                continue;
            }
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE_VERBOSE").is_some() {
                eprintln!(
                    "native layered access domains signal={} routing_y={} values={:?}",
                    Signal,
                    RoutingY,
                    Domains.iter().map(|Domain| Domain.iter().map(|Value| (Value.CandidateId.as_str(), Value.Portal, Value.Wire.len())).collect::<Vec<_>>()).collect::<Vec<_>>(),
                );
            }
            let mut PortalTuples = CompleteAccessWitness
                .as_ref()
                .as_ref()
                .and_then(|Witness| {
                    TerminalVariables
                        .iter()
                        .map(|Variable| {
                            let CandidateId = Witness.get(Variable)?;
                            AccessByVariable[Variable]
                                .iter()
                                .copied()
                                .find(|Value| &Value.CandidateId == CandidateId)
                        })
                        .collect::<Option<Vec<_>>>()
                })
                .filter(|Values| LayeredAccessTupleIsSelfLegal(Values))
                .into_iter()
                .collect::<Vec<_>>();
            let mut PrimaryPortalTupleCount;
            if RequiredVariables.len() > 64 {
                // Match the authoritative large-demand policy: retain a
                // rotated diagonal of the ranked terminal domains, then add
                // bounded single-terminal rank perturbations for high fanout.
                // This preserves access diversity without expanding the full
                // Cartesian product.
                let VariantCount = (*PortalVariantLimit)
                    .min(Domains.iter().map(Vec::len).max().unwrap_or(0));
                let mut SeenPortalIds = PortalTuples
                    .iter()
                    .map(|Values| {
                        Values
                            .iter()
                            .map(|Value| Value.CandidateId.clone())
                            .collect::<Vec<_>>()
                    })
                    .collect::<BTreeSet<_>>();
                for Variant in 0..VariantCount {
                    let mut Candidate: Vec<&DeferredAccessCandidateValue> =
                        Vec::with_capacity(Domains.len());
                    let mut Coherent = true;
                    for (DomainIndex, Domain) in Domains.iter().enumerate() {
                        if let Some(PreviousIndex) = TerminalVariables[..DomainIndex]
                            .iter()
                            .position(|Value| Value == &TerminalVariables[DomainIndex])
                        {
                            let PreviousId = &Candidate[PreviousIndex].CandidateId;
                            if let Some(Value) = Domain
                                .iter()
                                .copied()
                                .find(|Value| &Value.CandidateId == PreviousId)
                            {
                                Candidate.push(Value);
                            } else {
                                Coherent = false;
                                break;
                            }
                        } else {
                            Candidate.push(Domain[(Variant + DomainIndex) % Domain.len()]);
                        }
                    }
                    let CandidateIds = Candidate
                        .iter()
                        .map(|Value| Value.CandidateId.clone())
                        .collect::<Vec<_>>();
                    if Coherent
                        && LayeredAccessTupleIsSelfLegal(&Candidate)
                        && SeenPortalIds.insert(CandidateIds)
                    {
                        PortalTuples.push(Candidate);
                    }
                }
                // Preserve the former high-fanout witness pool exactly.  The
                // additional tuples below are replacement witnesses only and
                // must not perturb any previously finite shape.
                if TerminalVariables.len() >= 5 && !PortalTuples.is_empty() {
                    let Baseline = PortalTuples[0].clone();
                    let MaximumRankOffset = 3usize;
                    'Perturbations: for RankOffset in 1..MaximumRankOffset {
                        for (DomainIndex, Domain) in Domains.iter().enumerate() {
                            let BaselineIndex = Domain
                                .iter()
                                .position(|Value| {
                                    Value.CandidateId
                                        == Baseline[DomainIndex].CandidateId
                                })
                                .expect("diagonal portal belongs to its domain");
                            let mut Candidate = Baseline.clone();
                            Candidate[DomainIndex] =
                                Domain[(BaselineIndex + RankOffset) % Domain.len()];
                            let CandidateIds = Candidate
                                .iter()
                                .map(|Value| Value.CandidateId.clone())
                                .collect::<Vec<_>>();
                            if LayeredAccessTupleIsSelfLegal(&Candidate)
                                && SeenPortalIds.insert(CandidateIds)
                            {
                                PortalTuples.push(Candidate);
                                if PortalTuples.len() >= 16 {
                                    break 'Perturbations;
                                }
                            }
                        }
                    }
                }
                PrimaryPortalTupleCount = PortalTuples.len();
                if !PortalTuples.is_empty() && PortalTuples.len() < 16 {
                    let TupleDomains = Domains
                        .iter()
                        .map(|Domain| {
                            let mut Values = Domain.clone();
                            Values.sort_by(|First, Second| {
                                First
                                    .IngressY
                                    .abs_diff(*RoutingY)
                                    .cmp(&Second.IngressY.abs_diff(*RoutingY))
                                    .then_with(|| First.Wire.len().cmp(&Second.Wire.len()))
                                    .then_with(|| First.IngressY.cmp(&Second.IngressY))
                                    .then_with(|| First.Portal.cmp(&Second.Portal))
                                    .then_with(|| First.Wire.cmp(&Second.Wire))
                                    .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
                            });
                            Values
                        })
                        .collect::<Vec<_>>();
                    let TupleScore = |Indices: &[usize]| {
                        let LayerDistance = Indices
                            .iter()
                            .enumerate()
                            .map(|(DomainIndex, CandidateIndex)| {
                                TupleDomains[DomainIndex][*CandidateIndex]
                                    .IngressY
                                    .abs_diff(*RoutingY) as usize
                            })
                            .sum::<usize>();
                        let Length = Indices
                            .iter()
                            .enumerate()
                            .map(|(DomainIndex, CandidateIndex)| {
                                TupleDomains[DomainIndex][*CandidateIndex].Wire.len()
                            })
                            .sum::<usize>();
                        let Ids = Indices
                            .iter()
                            .enumerate()
                            .map(|(DomainIndex, CandidateIndex)| {
                                TupleDomains[DomainIndex][*CandidateIndex]
                                    .CandidateId
                                    .clone()
                            })
                            .collect::<Vec<_>>();
                        (LayerDistance, Length, Ids)
                    };
                    let InitialIndices = vec![0usize; TupleDomains.len()];
                    let (InitialLayerDistance, InitialLength, InitialIds) =
                        TupleScore(&InitialIndices);
                    let mut TupleFrontier = BinaryHeap::from([Reverse((
                        InitialLayerDistance,
                        InitialLength,
                        InitialIds,
                        InitialIndices.clone(),
                    ))]);
                    let mut SeenIndexTuples = HashSet::from([InitialIndices]);
                    let mut CompletedTupleStates = 0usize;
                    while let Some(Reverse((_LayerDistance, _Length, _Ids, Indices))) =
                        TupleFrontier.pop()
                    {
                        if CompletedTupleStates % DEADLINE_CHECK_INTERVAL == 0
                            && Deadline.Check()
                        {
                            return Ok(None);
                        }
                        CompletedTupleStates += 1;
                        let Candidate = Indices
                            .iter()
                            .enumerate()
                            .map(|(DomainIndex, CandidateIndex)| {
                                TupleDomains[DomainIndex][*CandidateIndex]
                            })
                            .collect::<Vec<_>>();
                        let Coherent = TerminalVariables.iter().enumerate().all(
                            |(DomainIndex, Variable)| {
                                TerminalVariables[..DomainIndex]
                                    .iter()
                                    .position(|Previous| Previous == Variable)
                                    .is_none_or(|PreviousIndex| {
                                        Candidate[PreviousIndex].CandidateId
                                            == Candidate[DomainIndex].CandidateId
                                    })
                            },
                        );
                        let CandidateIds = Candidate
                            .iter()
                            .map(|Value| Value.CandidateId.clone())
                            .collect::<Vec<_>>();
                        if Coherent
                            && LayeredAccessTupleIsSelfLegal(&Candidate)
                            && SeenPortalIds.insert(CandidateIds)
                        {
                            PortalTuples.push(Candidate);
                            if PortalTuples.len() >= 16 {
                                break;
                            }
                        }
                        for DomainIndex in 0..TupleDomains.len() {
                            let mut NextIndices = Indices.clone();
                            NextIndices[DomainIndex] += 1;
                            if NextIndices[DomainIndex] >= TupleDomains[DomainIndex].len()
                                || !SeenIndexTuples.insert(NextIndices.clone())
                            {
                                continue;
                            }
                            let (NextLayerDistance, NextLength, NextIds) =
                                TupleScore(&NextIndices);
                            TupleFrontier.push(Reverse((
                                NextLayerDistance,
                                NextLength,
                                NextIds,
                                NextIndices,
                            )));
                        }
                    }
                }
            } else {
            // Preserve layer-distinct access worlds before filling the
            // remaining finite tuple frontier by cost.  A globally nearest
            // Cartesian prefix can contain only one ingress layer and erase
            // the mixed-layer stub bundle needed by an otherwise legal guide.
            // The rotated diagonal is deterministic and every retained tuple
            // remains an exact physical access choice.
            let VariantCount = (*PortalVariantLimit)
                .min(Domains.iter().map(Vec::len).max().unwrap_or(0));
            let mut SeenPortalIds = PortalTuples
                .iter()
                .map(|Values| {
                    Values
                        .iter()
                        .map(|Value| Value.CandidateId.clone())
                        .collect::<Vec<_>>()
                })
                .collect::<BTreeSet<_>>();
            for Variant in 0..VariantCount {
                let Candidate = Domains
                    .iter()
                    .enumerate()
                    .map(|(DomainIndex, Domain)| {
                        Domain[(Variant + DomainIndex) % Domain.len()]
                    })
                    .collect::<Vec<_>>();
                let CandidateIds = Candidate
                    .iter()
                    .map(|Value| Value.CandidateId.clone())
                    .collect::<Vec<_>>();
                if LayeredAccessTupleIsSelfLegal(&Candidate)
                    && SeenPortalIds.insert(CandidateIds)
                {
                    PortalTuples.push(Candidate);
                }
            }
            PrimaryPortalTupleCount = PortalTuples.len();
            // Fill replacement witnesses from the exact least-cost Cartesian
            // frontier.  These are tried only when a primary tuple cannot
            // certify the requested guide, so they do not perturb an already
            // valid primary physical shape.
            let TupleDomains = Domains
                .iter()
                .map(|Domain| {
                    let mut Values = Domain.clone();
                    Values.sort_by(|First, Second| {
                        First
                            .IngressY
                            .abs_diff(*RoutingY)
                            .cmp(&Second.IngressY.abs_diff(*RoutingY))
                            .then_with(|| First.Wire.len().cmp(&Second.Wire.len()))
                            .then_with(|| First.IngressY.cmp(&Second.IngressY))
                            .then_with(|| First.Portal.cmp(&Second.Portal))
                            .then_with(|| First.Wire.cmp(&Second.Wire))
                            .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
                    });
                    Values
                })
                .collect::<Vec<_>>();
            let TupleScore = |Indices: &[usize]| {
                let LayerDistance = Indices
                    .iter()
                    .enumerate()
                    .map(|(DomainIndex, CandidateIndex)| {
                        TupleDomains[DomainIndex][*CandidateIndex]
                            .IngressY
                            .abs_diff(*RoutingY) as usize
                    })
                    .sum::<usize>();
                let Length = Indices
                    .iter()
                    .enumerate()
                    .map(|(DomainIndex, CandidateIndex)| {
                        TupleDomains[DomainIndex][*CandidateIndex].Wire.len()
                    })
                    .sum::<usize>();
                let Ids = Indices
                    .iter()
                    .enumerate()
                    .map(|(DomainIndex, CandidateIndex)| {
                        TupleDomains[DomainIndex][*CandidateIndex]
                            .CandidateId
                            .clone()
                    })
                    .collect::<Vec<_>>();
                (LayerDistance, Length, Ids)
            };
            let InitialIndices = vec![0usize; Domains.len()];
            let (InitialLayerDistance, InitialLength, InitialIds) =
                TupleScore(&InitialIndices);
            let mut TupleFrontier = BinaryHeap::from([Reverse((
                InitialLayerDistance,
                InitialLength,
                InitialIds,
                InitialIndices.clone(),
            ))]);
            let mut SeenIndexTuples = HashSet::from([InitialIndices]);
            let mut CompletedTupleStates = 0usize;
            while let Some(Reverse((_LayerDistance, _Length, _Ids, Indices))) =
                TupleFrontier.pop()
            {
                if CompletedTupleStates % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Ok(None);
                }
                CompletedTupleStates += 1;
                let Candidate = Indices
                    .iter()
                    .enumerate()
                    .map(|(DomainIndex, CandidateIndex)| {
                        TupleDomains[DomainIndex][*CandidateIndex]
                    })
                    .collect::<Vec<_>>();
                let Coherent = TerminalVariables.iter().enumerate().all(
                    |(DomainIndex, Variable)| {
                        TerminalVariables[..DomainIndex]
                            .iter()
                            .position(|Previous| Previous == Variable)
                            .is_none_or(|PreviousIndex| {
                                Candidate[PreviousIndex].CandidateId
                                    == Candidate[DomainIndex].CandidateId
                            })
                    },
                );
                let CandidateIds = Candidate
                    .iter()
                    .map(|Value| Value.CandidateId.clone())
                    .collect::<Vec<_>>();
                if Coherent
                    && LayeredAccessTupleIsSelfLegal(&Candidate)
                    && SeenPortalIds.insert(CandidateIds)
                {
                    PortalTuples.push(Candidate);
                    if PortalTuples.len() >= 16 {
                        break;
                    }
                }
                for DomainIndex in 0..Domains.len() {
                    let mut NextIndices = Indices.clone();
                    NextIndices[DomainIndex] += 1;
                    if NextIndices[DomainIndex] >= TupleDomains[DomainIndex].len()
                        || !SeenIndexTuples.insert(NextIndices.clone())
                    {
                        continue;
                    }
                    let (NextLayerDistance, NextLength, NextIds) = TupleScore(&NextIndices);
                    TupleFrontier.push(Reverse((
                        NextLayerDistance,
                        NextLength,
                        NextIds,
                        NextIndices,
                    )));
                }
            }
            if PrimaryPortalTupleCount == 0 {
                PrimaryPortalTupleCount =
                    (*PortalVariantLimit).min(PortalTuples.len());
            }
            }
            if PrimaryPortalTupleCount == 0 {
                continue;
            }
            let PhysicalPortalVariantCount =
                (*PortalVariantLimit).min(PrimaryPortalTupleCount);
            for Variant in 0..PhysicalPortalVariantCount {
                let BaseTuple = &PortalTuples[Variant];
                let BaseTerminals = BaseTuple
                    .iter()
                    .map(|Value| {
                        let PositionValue = Value.Portal;
                        (PositionValue.0, PositionValue.2)
                    })
                    .chain(
                        DetachedSeedAnchorsByOwner
                            .get(Signal.as_str())
                            .into_iter()
                            .flat_map(|Values| Values.iter())
                            .filter_map(|Path| Path.last())
                            .map(|PositionValue| {
                                (PositionValue.0, PositionValue.2)
                            }),
                    )
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>();
                let XSpan = BaseTerminals.iter().map(|Value| Value.0).max().unwrap()
                    - BaseTerminals.iter().map(|Value| Value.0).min().unwrap();
                let ZSpan = BaseTerminals.iter().map(|Value| Value.1).max().unwrap()
                    - BaseTerminals.iter().map(|Value| Value.1).min().unwrap();
                let PreferredAxis = if XSpan >= ZSpan { "X" } else { "Z" };
                for (AxisIndex, Axis) in [
                    PreferredAxis,
                    if PreferredAxis == "X" { "Z" } else { "X" },
                ]
                .into_iter()
                .enumerate()
                {
                    let mut Coordinates = BaseTerminals
                        .iter()
                        .map(|Value| if Axis == "X" { Value.1 } else { Value.0 })
                        .collect::<Vec<_>>();
                    Coordinates.sort_unstable();
                    let Center = Coordinates[Coordinates.len() / 2];
                    let TrackAnchor = if Axis == "X" { *MinimumZ } else { *MinimumX };
                    let AlignedCenter = TrackAnchor
                        + (Center - TrackAnchor + (*TrackPitch as i32) / 2)
                            .div_euclid(*TrackPitch as i32)
                            * (*TrackPitch as i32);
                    let LaneValues = CandidateLayeredGuideLanes(
                        AlignedCenter,
                        *LaneCount,
                        *TrackPitch as i32,
                    );
                    for (LaneIndex, Lane) in LaneValues.into_iter().enumerate() {
                        let PortalPhase = 1 + AxisIndex * 3 + LaneIndex;
                        let PrimaryTupleIndex =
                            (Variant + PortalPhase) % PrimaryPortalTupleCount;
                        let ShapeCount = (PhysicalPortalVariantCount * 2 * *LaneCount).max(1);
                        let ShapeIndex = LaneIndex
                            + *LaneCount * (Variant + PhysicalPortalVariantCount * AxisIndex);
                        let PortalShapeRank =
                            (ShapeIndex + ShapeCount - (LayerIndex % ShapeCount)) % ShapeCount;
                        let PerLayerRequestLimit = MaximumShapesPerSignal
                            .saturating_add(RoutingYs.len() - 1)
                            / RoutingYs.len();
                        if PortalShapeRank >= PerLayerRequestLimit {
                            continue;
                        }
                        let FallbackTupleCount =
                            PortalTuples.len().saturating_sub(PrimaryPortalTupleCount);
                        let FallbackStart = if FallbackTupleCount == 0 {
                            0
                        } else {
                            (ShapeIndex + LayerIndex) % FallbackTupleCount
                        };
                        let TupleIndices = std::iter::once(PrimaryTupleIndex)
                            .chain((0..FallbackTupleCount).map(|FallbackOffset| {
                                PrimaryPortalTupleCount
                                    + (FallbackStart + FallbackOffset) % FallbackTupleCount
                            }))
                            .collect::<Vec<_>>();
                        for (TupleAttemptIndex, TupleIndex) in
                            TupleIndices.into_iter().enumerate()
                        {
                        let PortalTuple = &PortalTuples[TupleIndex];
                        let Terminals = PortalTuple
                            .iter()
                            .map(|Value| {
                                let PositionValue = Value.Portal;
                                (PositionValue.0, PositionValue.2)
                            })
                            .chain(
                                DetachedSeedAnchorsByOwner
                                    .get(Signal.as_str())
                                    .into_iter()
                                    .flat_map(|Values| Values.iter())
                                    .filter_map(|Path| Path.last())
                                    .map(|PositionValue| {
                                        (PositionValue.0, PositionValue.2)
                                    }),
                            )
                            .collect::<BTreeSet<_>>()
                            .into_iter()
                            .collect::<Vec<_>>();
                        let Guide = BuildLayeredGuideSpine(
                            &Terminals,
                            Axis,
                            Lane,
                            *RoutingY,
                        );
                        let PrimaryCandidateId = format!(
                            "__native_guide__:{}:{}:{}:{}:{}:{}",
                            Signal, LayerIndex, Variant, Axis, Lane, PortalShapeRank,
                        );
                        let CandidateId = if TupleAttemptIndex == 0 {
                            PrimaryCandidateId
                        } else {
                            format!("{}:fallback:{}", PrimaryCandidateId, TupleIndex)
                        };
                        let PortalIdentity =
                            PortalTuple.iter().map(|Value| Value.Portal).collect::<Vec<_>>();
                        let PhysicalGuideKey = (
                            LayerIndex,
                            Axis.to_string(),
                            Lane,
                            PortalIdentity.clone(),
                        );
                        let SameOwnerBaseValues = BaseValuesByOwner
                            .get(Signal)
                            .map(Vec::as_slice)
                            .unwrap_or(&[]);
                        let RequiredWire = &RequiredWireByOwner[Signal];
                        let ForeignBlockedNodes = &ForeignBlockedNodesByOwner[Signal];
                        let DetachedSeedAccessPaths = DetachedSeedAnchorsByOwner
                            .get(Signal.as_str())
                            .copied()
                            .unwrap_or(&[]);
                        let AccessRamps = if let Some(Cached) =
                            AccessRampsByPhysicalGuide.get(&PhysicalGuideKey)
                        {
                            Cached.clone()
                        } else {
                            let CacheKey = LayeredGuideAccessRampCacheKey {
                                MemberIndex,
                                LayerIndex,
                                Axis: Axis.to_string(),
                                Lane,
                                PortalIdentity: PortalIdentity.clone(),
                                Guide: Guide.clone(),
                                GuideExpansion: *GuideExpansion,
                                RequiredWire: RequiredWire
                                    .iter()
                                    .copied()
                                    .collect::<BTreeSet<_>>()
                                    .into_iter()
                                    .collect(),
                                ForeignBlockedNodes: ForeignBlockedNodes
                                    .iter()
                                    .copied()
                                    .collect::<BTreeSet<_>>()
                                    .into_iter()
                                    .collect(),
                                OwnerSignal: Signal.clone(),
                                DetachedSeedAccessPaths:
                                    DetachedSeedAccessPaths.to_vec(),
                                SourceDetachedAnchorIndex:
                                    *SourceDetachedAnchorIndex,
                            };
                            let CacheCell = SharedAccessRampCache.GetCell(CacheKey);
                            let Complete = if let Some(Cached) = CacheCell.get() {
                                Cached.clone()
                            } else {
                                let Computed = BuildLayeredGuideNecessaryAccessRamps(
                                        &GraphAdjacency,
                                        &IndexedGraph,
                                        SharedAccessRampCache,
                                        &Guide,
                                        *GuideExpansion,
                                        PortalTuple,
                                        &BaseClaimIndex,
                                        RequiredWire,
                                        ForeignBlockedNodes,
                                        Signal,
                                        DetachedSeedAccessPaths,
                                        *SourceDetachedAnchorIndex,
                                        Deadline,
                                    );
                                // An outer None means the bounded proof did not
                                // complete.  It is not a negative certificate
                                // and must never poison later exact reuse.
                                if Computed.is_some() {
                                    let _ = CacheCell.set(Computed.clone());
                                    CacheCell
                                        .get()
                                        .cloned()
                                        .unwrap_or(Computed)
                                } else {
                                    Computed
                                }
                            };
                            let Some(Complete) = Complete else {
                                return Ok(None);
                            };
                            AccessRampsByPhysicalGuide
                                .insert(PhysicalGuideKey.clone(), Complete.clone());
                            Complete
                        };
                        let Some((
                            PhysicalGuide,
                            AccessRamps,
                            mut DetailedHintPaths,
                            _InitialPoweredCorridorHint,
                        )) = AccessRamps else {
                            AccessRampConnectivityRejectionCount += 1;
                            continue;
                        };
                        let CombinedGuideWire = PhysicalGuide
                            .iter()
                            .copied()
                            // Mixed exterior/internal nets own their selected
                            // stub and exact fixed ramps at compact selection.
                            // Wholly internal nets have no independent stub
                            // variable, so their detached branches remain
                            // selected-world seeds instead of synthetic guide
                            // capacity.
                            .chain(
                                (!PortalTuple.is_empty())
                                    .then_some(
                                        DetachedSeedAccessPaths
                                            .iter()
                                            .flatten()
                                            .copied(),
                                    )
                                    .into_iter()
                                    .flatten(),
                            )
                            .chain(
                                (!PortalTuple.is_empty())
                                    .then_some(AccessRamps.iter().flatten().copied())
                                    .into_iter()
                                    .flatten(),
                            )
                            .collect::<BTreeSet<_>>()
                            .into_iter()
                            .collect::<Vec<_>>();
                        let Some(Claims) = BuildDeferredAccessCandidate(
                            format!("__route_guide__:{}", Signal),
                            CandidateId.clone(),
                            Signal.clone(),
                            *RoutingY,
                            CombinedGuideWire,
                        ) else {
                            if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL")
                                .ok()
                                .as_deref()
                                == Some(Signal.as_str())
                                && Lane == -6
                            {
                                let CombinedWire = PhysicalGuide
                                    .iter()
                                    .copied()
                                    .chain(
                                        DetachedSeedAccessPaths
                                            .iter()
                                            .flatten()
                                            .copied(),
                                    )
                                    .chain(AccessRamps.iter().flatten().copied())
                                    .collect::<BTreeSet<_>>();
                                let SupportWireConflicts = CombinedWire
                                    .iter()
                                    .filter_map(|(X, Y, Z)| {
                                        let Support = (*X, Y - 1, *Z);
                                        CombinedWire.contains(&Support).then_some((
                                            (*X, *Y, *Z),
                                            Support,
                                        ))
                                    })
                                    .collect::<Vec<_>>();
                                eprintln!(
                                    "native layered ramp self-conflict signal={} lane={} guide={:?} detached={:?} ramps={:?} support_wire={:?} guide_legal={} ramp_legal={:?}",
                                    Signal,
                                    Lane,
                                    PhysicalGuide,
                                    DetachedSeedAccessPaths,
                                    AccessRamps,
                                    SupportWireConflicts,
                                    BuildDeferredAccessCandidate(
                                        "guide".to_string(),
                                        "guide".to_string(),
                                        Signal.clone(),
                                        *RoutingY,
                                        PhysicalGuide.clone(),
                                    )
                                    .is_some(),
                                    AccessRamps
                                        .iter()
                                        .map(|Path| BuildDeferredAccessCandidate(
                                            "ramp".to_string(),
                                            "ramp".to_string(),
                                            Signal.clone(),
                                            *RoutingY,
                                            Path.clone(),
                                        )
                                        .is_some())
                                        .collect::<Vec<_>>(),
                                );
                            }
                            AccessRampSelfConflictRejectionCount += 1;
                            continue;
                        };
                        if BaseClaimIndex.Conflicts(&Claims) {
                            if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL")
                                .ok()
                                .as_deref()
                                == Some(Signal.as_str())
                            {
                                let WireConflicts = Claims
                                    .Wire
                                    .iter()
                                    .copied()
                                    .filter(|PositionValue| {
                                        BaseClaimIndex.Support.contains(PositionValue)
                                            || BaseClaimIndex.Air.contains(PositionValue)
                                            || BaseClaimIndex
                                                .ElectricalOwners
                                                .get(PositionValue)
                                                .is_some_and(|Owners| {
                                                    Owners.iter().any(|Owner| Owner != Signal)
                                                })
                                    })
                                    .collect::<Vec<_>>();
                                let SupportConflicts = Claims
                                    .Support
                                    .iter()
                                    .copied()
                                    .filter(|PositionValue| {
                                        BaseClaimIndex.Wire.contains(PositionValue)
                                            || BaseClaimIndex.Air.contains(PositionValue)
                                    })
                                    .collect::<Vec<_>>();
                                let AirConflicts = Claims
                                    .Air
                                    .iter()
                                    .copied()
                                    .filter(|PositionValue| {
                                        BaseClaimIndex.Support.contains(PositionValue)
                                            || BaseClaimIndex.Wire.contains(PositionValue)
                                    })
                                    .collect::<Vec<_>>();
                                eprintln!(
                                    "native layered final base conflict signal={} lane={} wire={:?} support={:?} air={:?}",
                                    Signal,
                                    Lane,
                                    WireConflicts,
                                    SupportConflicts,
                                    AirConflicts,
                                );
                            }
                            AccessRampBaseConflictRejectionCount += 1;
                            continue;
                        }
                        let (
                            AccessWitnessRequirementSets,
                            SupportedAccessChoices,
                        ) = if let Some(Cached) =
                            AccessWitnessByPhysicalGuide.get(&PhysicalGuideKey)
                        {
                            Cached.clone()
                        } else {
                            let (
                                Complete,
                                SupportedChoices,
                                WitnessExpansionCount,
                            ) = match LayeredGuideHasSelfLegalAccessBundle(
                                &Claims,
                                SameOwnerBaseValues,
                                TerminalVariables,
                                PortalTuple,
                                &Domains,
                                PreferredAccessWitnessByPortalTuple
                                    .get(&PortalIdentity)
                                    .map(Vec::as_slice)
                                    .unwrap_or(&[]),
                                Deadline,
                            ) {
                                Ok(Value) => Value,
                                Err(()) => return Ok(None),
                            };
                            AccessWitnessExpansionCount = AccessWitnessExpansionCount
                                .saturating_add(WitnessExpansionCount);
                            let Complete = Arc::new(Complete);
                            AccessWitnessByPhysicalGuide
                                .insert(
                                    PhysicalGuideKey.clone(),
                                    (
                                        Complete.clone(),
                                        SupportedChoices.clone(),
                                    ),
                                );
                            (Complete, SupportedChoices)
                        };
                        if AccessWitnessRequirementSets.is_empty() {
                            ClaimBundleRejectionCount += 1;
                            continue;
                        }
                        let CachedWitnesses = PreferredAccessWitnessByPortalTuple
                            .entry(PortalIdentity)
                            .or_default();
                        if CachedWitnesses.is_empty() {
                            CachedWitnesses.extend(
                                AccessWitnessRequirementSets
                                    .iter()
                                    .take(1)
                                    .cloned(),
                            );
                        }
                        if let Some(AccessWitnessRequirements) =
                            AccessWitnessRequirementSets.first()
                        {
                            let SelectedAccessValues = AccessWitnessRequirements
                                .iter()
                                .map(|(Variable, CandidateId)| {
                                    AccessByVariable.get(Variable).and_then(|Values| {
                                        Values.iter().copied().find(|Value| {
                                            Value.CandidateId == *CandidateId
                                        })
                                    })
                                })
                                .collect::<Option<Vec<_>>>();
                            let Some(SelectedAccessValues) = SelectedAccessValues else {
                                return Err(pyo3::exceptions::PyValueError::new_err(
                                    "layered powered witness references an unknown access value",
                                ));
                            };
                            let Some(PoweredWitness) =
                                LayeredGuideAccessBundleHasPoweredTreeWitness(
                                    &IndexedGraph,
                                    &mut PoweredWitnessWorkspace,
                                    &Claims,
                                    &DetailedHintPaths,
                                    TerminalVariables,
                                    &SelectedAccessValues,
                                    DetachedSeedAccessPaths,
                                    SourceTerminalVariable.as_deref(),
                                    *SourceDetachedAnchorIndex,
                                    Deadline,
                                )
                            else {
                                return Ok(None);
                            };
                            if std::env::var("RCS_DEBUG_LAYERED_POWERED_SIGNAL")
                                .ok()
                                .is_some_and(|DebugSignal| DebugSignal == *Signal)
                                && CandidateId.contains(":6:0:X:41:0:fallback:10")
                            {
                                eprintln!(
                                    "native layered powered witness detail signal={} candidate={} terminal_count={} requirement_count={} selected_count={} selected_wire_lengths={:?} source={:?} detached_count={} source_detached={:?} witness={:?}",
                                    Signal,
                                    CandidateId,
                                    TerminalVariables.len(),
                                    AccessWitnessRequirements.len(),
                                    SelectedAccessValues.len(),
                                    SelectedAccessValues
                                        .iter()
                                        .map(|Value| Value.OrderedWire.len())
                                        .collect::<Vec<_>>(),
                                    SourceTerminalVariable,
                                    DetachedSeedAccessPaths.len(),
                                    SourceDetachedAnchorIndex,
                                    PoweredWitness.as_ref().map(|(Paths, Repeaters)| (
                                        Paths.iter().map(Vec::len).collect::<Vec<_>>(),
                                        Repeaters.len(),
                                    )),
                                );
                            }
                            let PoweredCorridorHint = PoweredWitness.is_some();
                            let mut CertifiedRepeaters = Vec::new();
                            if let Some((CertifiedPaths, RepeaterValues)) = PoweredWitness {
                                if !CertifiedPaths.is_empty() {
                                    DetailedHintPaths = CertifiedPaths;
                                }
                                CertifiedRepeaters = RepeaterValues;
                            }
                            SharedAccessRampCache
                                .ExhaustivePoweredProofCount
                                .fetch_add(1, Ordering::Relaxed);
                            if PoweredCorridorHint {
                                SharedAccessRampCache
                                    .KnownPoweredWitnessCount
                                    .fetch_add(1, Ordering::Relaxed);
                            }
                            let AccessWitnessLength = AccessWitnessRequirements
                                .iter()
                                .filter_map(|(Variable, CandidateId)| {
                                    AccessByVariable.get(Variable).and_then(|Values| {
                                        Values.iter().find(|Value| {
                                            Value.CandidateId == *CandidateId
                                        })
                                    })
                                })
                                .map(|Value| Value.Wire.len())
                                .sum::<usize>();
                            SignalShapes.push(DeferredGuideCandidateValue {
                                Variable: format!("__route_guide__:{}", Signal),
                                CandidateId: CandidateId.clone(),
                                OwnerSignal: Signal.clone(),
                                Requirements: AccessWitnessRequirements.clone(),
                                Portals: PortalTuple
                                    .iter()
                                    .map(|Value| Value.Portal)
                                    .collect(),
                                RoutingY: *RoutingY,
                                Axis: Axis.to_string(),
                                Lane,
                                Guide: Guide.clone(),
                                AccessRamps: AccessRamps.clone(),
                                DetailedHintPaths: DetailedHintPaths.clone(),
                                CertifiedRepeaters,
                                PhysicalGuide: PhysicalGuide.clone(),
                                SupportedAccessChoices: (
                                    SupportedAccessChoices.clone()
                                ),
                                CertifiedAccessTuples: Arc::clone(
                                    &AccessWitnessRequirementSets
                                ),
                                TerminalVariables: TerminalVariables.clone(),
                                DetachedSeedAccessPaths: DetachedSeedAccessPaths.to_vec(),
                                SourceTerminalVariable: SourceTerminalVariable.clone(),
                                SourceDetachedAnchorIndex: *SourceDetachedAnchorIndex,
                                PoweredCorridorHint,
                                Claims: Claims.clone(),
                                Priority: (
                                    Guide.len(),
                                    PortalShapeRank,
                                    LayerIndex,
                                    LaneIndex,
                                    usize::from(Axis != PreferredAxis),
                                    AccessWitnessLength,
                                    Axis.to_string(),
                                    Lane,
                                ),
                            });
                        }
                        }
                    }
                }
            }
        }
        if SignalShapes.is_empty()
            && std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
        {
            eprintln!(
                "native layered guide empty signal={} claim_bundle_rejections={} ramp_connectivity_rejections={} ramp_self_conflict_rejections={} ramp_base_conflict_rejections={} powered_tree_rejections={}",
                Signal,
                ClaimBundleRejectionCount,
                AccessRampConnectivityRejectionCount,
                AccessRampSelfConflictRejectionCount,
                AccessRampBaseConflictRejectionCount,
                PoweredTreeRejectionCount,
            );
        }
        SignalShapes.sort_by(|First, Second| {
            usize::from(!First.PoweredCorridorHint)
                .cmp(&usize::from(!Second.PoweredCorridorHint))
                .then_with(|| First.Priority.cmp(&Second.Priority))
                .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
        });
        if std::env::var("RCS_DEBUG_LAYERED_POWERED_SIGNAL")
            .ok()
            .is_some_and(|DebugSignal| DebugSignal == *Signal)
        {
            eprintln!(
                "native layered signal candidate order signal={} terminals={:?} source_terminal={:?} detached_count={} source_detached={:?} total={} powered={} candidates={:?}",
                Signal,
                TerminalVariables,
                SourceTerminalVariable,
                DetachedSeedAnchors.len(),
                SourceDetachedAnchorIndex,
                SignalShapes.len(),
                SignalShapes.iter().filter(|Value| Value.PoweredCorridorHint).count(),
                SignalShapes
                    .iter()
                    .take(32)
                    .map(|Value| (
                        Value.CandidateId.as_str(),
                        Value.RoutingY,
                        Value.Axis.as_str(),
                        Value.Lane,
                        Value.PoweredCorridorHint,
                        &Value.Priority,
                        Value.Guide.first(),
                        Value.Guide.last(),
                    ))
                    .collect::<Vec<_>>(),
            );
        }
        SignalShapes.dedup_by(|First, Second| {
            First.RoutingY == Second.RoutingY
                && First.Axis == Second.Axis
                && First.Lane == Second.Lane
                && First.Guide == Second.Guide
                && First.Portals == Second.Portals
                && First.Requirements == Second.Requirements
        });
        let PhysicalShapeIdsByRoutingY = SignalShapes.iter().fold(
            BTreeMap::<i32, Vec<String>>::new(),
            |mut Result, Value| {
                let ShapeId = Value
                    .CandidateId
                    .split_once(":access:")
                    .map_or(Value.CandidateId.as_str(), |(Base, _Suffix)| Base);
                let Values = Result.entry(Value.RoutingY).or_default();
                if !Values.iter().any(|Existing| Existing == ShapeId) {
                    Values.push(ShapeId.to_string());
                }
                Result
            },
        );
        let mut RetainedPhysicalShapeIds = HashSet::<String>::new();
        let mut LayerOffset = 0usize;
        while RetainedPhysicalShapeIds.len() < *MaximumShapesPerSignal {
            let mut Added = false;
            for Values in PhysicalShapeIdsByRoutingY.values() {
                let Some(ShapeId) = Values.get(LayerOffset) else {
                    continue;
                };
                RetainedPhysicalShapeIds.insert(ShapeId.clone());
                Added = true;
                if RetainedPhysicalShapeIds.len() >= *MaximumShapesPerSignal {
                    break;
                }
            }
            if !Added {
                break;
            }
            LayerOffset += 1;
        }
        SignalShapes.retain(|Value| {
            let ShapeId = Value
                .CandidateId
                .split_once(":access:")
                .map_or(Value.CandidateId.as_str(), |(Base, _Suffix)| Base);
            RetainedPhysicalShapeIds.contains(ShapeId)
        });
        if SignalShapes.is_empty() {
            FirstEmptySignalOrderIndex.fetch_min(
                OrderIndex,
                std::sync::atomic::Ordering::SeqCst,
            );
        }
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            eprintln!(
                    "native layered signal factors signal={} shapes={} witness_expansions={} elapsed={:.3}s",
                    Signal,
                    SignalShapes.len(),
                    AccessWitnessExpansionCount,
                    SignalStartedAt.elapsed().as_secs_f64(),
                );
        }
        Ok(Some(SignalShapes))
            })
            .collect::<Vec<_>>()
        });
    let FirstEmptySignalOrderIndex =
        FirstEmptySignalOrderIndex.load(std::sync::atomic::Ordering::SeqCst);
    if FirstEmptySignalOrderIndex != usize::MAX {
        let SignalIndex = SignalOrder[FirstEmptySignalOrderIndex];
        let (
            Signal,
            _TerminalVariables,
            _PortalVariantLimit,
            _RegionTerminals,
            _SourceTerminalVariable,
            _SourceDetachedAnchorIndex,
        ) =
            &SignalValues[SignalIndex];
        return Ok(Some((
            BTreeMap::from([(format!("__route_guide__:{Signal}"), Vec::new())]),
            1,
            HashMap::new(),
            Arc::new(vec![Vec::new()]),
        )));
    }
    let mut GuideValues = Vec::<DeferredGuideCandidateValue>::new();
    for (SignalIndex, Outcome) in SignalOrder.iter().zip(GuideValuesBySignal) {
        let (
            _Signal,
            _TerminalVariables,
            _PortalVariantLimit,
            _RegionTerminals,
            _SourceTerminalVariable,
            _SourceDetachedAnchorIndex,
        ) =
            &SignalValues[*SignalIndex];
        let Some(mut SignalShapes) = Outcome? else {
            return Ok(None);
        };
        GuideValues.append(&mut SignalShapes);
    }
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered guide factor extraction guides={} elapsed={:.3}s",
            GuideValues.len(),
            DomainStartedAt.elapsed().as_secs_f64(),
        );
    }
    let CandidateWirePositions = AccessValues
        .iter()
        .flat_map(|Value| Value.Wire.iter().copied())
        .chain(
            GuideValues
                .iter()
                .flat_map(|Value| Value.Claims.Wire.iter().copied()),
        )
        .collect::<HashSet<_>>();
    let mut ResourcePositions = AccessValues
        .iter()
        .flat_map(|Value| {
            Value
                .Wire
                .iter()
                .chain(&Value.Support)
                .chain(&Value.Air)
                .chain(&Value.Electrical)
                .copied()
        })
        .chain(GuideValues.iter().flat_map(|Value| {
            Value
                .Claims
                .Wire
                .iter()
                .chain(&Value.Claims.Support)
                .chain(&Value.Claims.Air)
                .chain(&Value.Claims.Electrical)
                .copied()
        }))
        .collect::<BTreeSet<_>>();
    for First in &CandidateWirePositions {
        for Second in AccessNeighborPositions(*First) {
            if Second.1 == First.1 || !CandidateWirePositions.contains(&Second) {
                continue;
            }
            let Lower = if First.1 < Second.1 { *First } else { Second };
            ResourcePositions.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    let ResourcePositions = ResourcePositions.into_iter().collect::<Vec<_>>();
    let ResourceIndex = ResourcePositions
        .iter()
        .copied()
        .enumerate()
        .map(|(Index, PositionValue)| (PositionValue, Index))
        .collect::<HashMap<_, _>>();
    let ResourceCount = ResourceIndex.len().max(1);
    let mut CrossAirByWire = vec![Vec::<(usize, usize)>::new(); ResourceCount];
    for First in &CandidateWirePositions {
        let Some(FirstIndex) = ResourceIndex.get(First).copied() else {
            continue;
        };
        for Second in AccessNeighborPositions(*First) {
            if Second.1 == First.1 || !CandidateWirePositions.contains(&Second) {
                continue;
            }
            let Some(SecondIndex) = ResourceIndex.get(&Second).copied() else {
                continue;
            };
            let Lower = if First.1 < Second.1 { *First } else { Second };
            let AirPosition = (Lower.0, Lower.1 + 1, Lower.2);
            let Some(AirIndex) = ResourceIndex.get(&AirPosition).copied() else {
                continue;
            };
            CrossAirByWire[FirstIndex].push((SecondIndex, AirIndex));
        }
    }
    for Values in &mut CrossAirByWire {
        Values.sort_unstable();
        Values.dedup();
    }
    let mut Groups = RequiredVariables
        .keys()
        .map(|Variable| (Variable.clone(), Vec::new()))
        .collect::<BTreeMap<_, _>>();
    for (
        Signal,
        _TerminalVariables,
        _VariantCount,
        _RegionTerminals,
        _SourceTerminalVariable,
        _SourceDetachedAnchorIndex,
    ) in SignalValues
    {
        Groups
            .entry(format!("__route_guide__:{}", Signal))
            .or_default();
    }
    let BuildClaims = |Value: &DeferredAccessCandidateValue| {
        let Remap = |Positions: &[Position]| {
            Positions
                .iter()
                .map(|PositionValue| ResourceIndex[PositionValue])
                .collect::<Vec<_>>()
        };
        ClaimMask::FromIndicesWithDeadline(
            ResourceCount,
            &Remap(&Value.Wire),
            &Remap(&Value.Support),
            &Remap(&Value.Air),
            &Remap(&Value.Electrical),
            Deadline,
        )
    };
    let AccessValueByChoice = GuideAccessValues
        .iter()
        .map(|Value| {
            (
                (Value.Variable.clone(), Value.CandidateId.clone()),
                Value.Portal,
            )
        })
        .collect::<HashMap<_, _>>();
    for Value in AccessValues {
        let Claims = match BuildClaims(&Value) {
            Ok(Value) => Arc::new(Value),
            Err(ClaimMaskBuildError::DeadlineExceeded) => return Ok(None),
            Err(ClaimMaskBuildError::IndexOutOfRange) => unreachable!(),
        };
        let LogicalKey = Value
            .Variable
            .strip_prefix("__access_terminal__:")
            .expect("validated layered access variable");
        let Contract = format!(
            "access-stub:{}={};access-portal:{}={}",
            LogicalKey,
            Value.CandidateId,
            LogicalKey,
            LayeredAccessPortalContractValue(Value.Portal),
        );
        Groups
            .get_mut(&Value.Variable)
            .unwrap()
            .push(AssignmentCandidate {
                CandidateId: Value.CandidateId,
                OwnerSignal: Value.OwnerSignal,
                TemplateRequirements: ParseContractRequirements(&Contract),
                ForbiddenCandidateIds: Arc::new(Vec::new()),
                OrderedWire: Arc::new(Value.OrderedWire),
                PoweredAccessConstraint: None,
                Claims,
                MaterialCost: 0,
                FootprintGrowth: 0,
                Length: Value.Wire.len().min(i32::MAX as usize) as i32,
                BendCount: 0,
                ViaCount: 0,
            });
    }
    let mut GuideRecipes = HashMap::new();
    for Value in GuideValues {
        let PortalByAccessVariable = Value
            .Requirements
            .iter()
            .map(|(Variable, CandidateId)| {
                (
                    Variable.clone(),
                    AccessValueByChoice[&(Variable.clone(), CandidateId.clone())],
                )
            })
            .collect::<HashMap<_, _>>();
        let mut ForbiddenCandidateIds = GuideAccessValues
            .iter()
            .filter(|Access| {
                PortalByAccessVariable
                    .get(&Access.Variable)
                    .is_some_and(|Portal| *Portal == Access.Portal)
                    && !Value.SupportedAccessChoices.contains(&(
                        Access.Variable.clone(),
                        Access.CandidateId.clone(),
                    ))
            })
            .map(|Access| (Access.Variable.clone(), Access.CandidateId.clone()))
            .collect::<Vec<_>>();
        ForbiddenCandidateIds.sort();
        ForbiddenCandidateIds.dedup();
        let Claims = match BuildClaims(&Value.Claims) {
            Ok(Value) => Arc::new(Value),
            Err(ClaimMaskBuildError::DeadlineExceeded) => return Ok(None),
            Err(ClaimMaskBuildError::IndexOutOfRange) => unreachable!(),
        };
        let PoweredWitnessWire = Value
            .Claims
            .OrderedWire
            .iter()
            .copied()
            .chain(Value.DetailedHintPaths.iter().flatten().copied())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        let Contract = BuildLayeredGuideAccessContract(&Value.Requirements, &AccessValueByChoice);
        GuideRecipes.insert(
            Value.CandidateId.clone(),
            (
                Value.Variable.clone(),
                Value.CandidateId.clone(),
                Value.Requirements.clone(),
                Value.RoutingY,
                Value.Axis.clone(),
                Value.Lane,
                Value.Guide.clone(),
                Value.AccessRamps.clone(),
                Value.PhysicalGuide.clone(),
                Value.DetailedHintPaths.clone(),
                Value.CertifiedRepeaters.clone(),
            ),
        );
        Groups
            .get_mut(&Value.Variable)
            .unwrap()
            .push(AssignmentCandidate {
                CandidateId: Value.CandidateId,
                OwnerSignal: Value.OwnerSignal,
                TemplateRequirements: ParseContractRequirements(&Contract),
                ForbiddenCandidateIds: Arc::new(ForbiddenCandidateIds),
                OrderedWire: Arc::new(PoweredWitnessWire),
                PoweredAccessConstraint: Some(Arc::new(AssignmentPoweredAccessConstraint {
                    HasPoweredTreeWitness: Value.PoweredCorridorHint,
                    GraphAdjacency: Arc::clone(&GraphAdjacency),
                    TerminalVariables: Arc::new(Value.TerminalVariables.clone()),
                    DetachedSeedAccessPaths: Arc::new(
                        Value.DetachedSeedAccessPaths.clone(),
                    ),
                    SourceTerminalVariable: Value.SourceTerminalVariable.clone(),
                    SourceDetachedAnchorIndex: Value.SourceDetachedAnchorIndex,
                    PreferredAccessCandidateTuples: Arc::clone(
                        &Value.CertifiedAccessTuples,
                    ),
                })),
                Claims,
                // Preserve the shared guide enumerator's physical preference
                // order in the native assignment objective.  Re-sorting only
                // by guide length and the opaque candidate id made equal-length
                // lanes choose by hash identity, which could turn a compact
                // feasible witness into an unnecessarily remote detailed lane.
                MaterialCost: Value
                    .Priority
                    .2
                    .saturating_add(
                        usize::from(!Value.PoweredCorridorHint).saturating_mul(16),
                    )
                    .min(i32::MAX as usize) as i32,
                FootprintGrowth: Value.Priority.1.min(i32::MAX as usize) as i32,
                Length: Value.Priority.0.min(i32::MAX as usize) as i32,
                BendCount: Value.Priority.3.min(i32::MAX as usize) as i32,
                ViaCount: Value.Priority.4.min(i32::MAX as usize) as i32,
            });
    }
    Ok(Some((
        Groups,
        ResourceCount,
        GuideRecipes,
        Arc::new(CrossAirByWire),
    )))
}

#[derive(Clone)]
struct LayeredCatalogClaimOccupancy {
    Wire: Vec<usize>,
    Support: Vec<usize>,
    Air: Vec<usize>,
    Electrical: Vec<usize>,
    WireByOwner: HashMap<(usize, usize), usize>,
    ElectricalByOwner: HashMap<(usize, usize), usize>,
    CrossAirByWire: Arc<Vec<Vec<(usize, usize)>>>,
}

impl LayeredCatalogClaimOccupancy {
    fn New(ResourceCount: usize, CrossAirByWire: Arc<Vec<Vec<(usize, usize)>>>) -> Self {
        Self {
            Wire: vec![0; ResourceCount],
            Support: vec![0; ResourceCount],
            Air: vec![0; ResourceCount],
            Electrical: vec![0; ResourceCount],
            WireByOwner: HashMap::new(),
            ElectricalByOwner: HashMap::new(),
            CrossAirByWire,
        }
    }

    fn IsCompatible(&self, Candidate: &AssignmentCandidate, Owner: usize) -> bool {
        let (Wire, Support, Air, Electrical) = Candidate.Claims.IndexSets();
        let StaticCompatible = Wire.iter().all(|Resource| {
            self.Support[*Resource] == 0
                && self.Air[*Resource] == 0
                && self.Electrical[*Resource]
                    == *self
                        .ElectricalByOwner
                        .get(&(*Resource, Owner))
                        .unwrap_or(&0)
        }) && Support
            .iter()
            .all(|Resource| self.Wire[*Resource] == 0 && self.Air[*Resource] == 0)
            && Air
                .iter()
                .all(|Resource| self.Wire[*Resource] == 0 && self.Support[*Resource] == 0)
            && Electrical.iter().all(|Resource| {
                self.Wire[*Resource] == *self.WireByOwner.get(&(*Resource, Owner)).unwrap_or(&0)
            });
        StaticCompatible
            && Wire.iter().all(|Resource| {
                self.CrossAirByWire[*Resource]
                    .iter()
                    .all(|(OtherWire, AirResource)| {
                        self.WireByOwner
                            .get(&(*OtherWire, Owner))
                            .copied()
                            .unwrap_or(0)
                            == 0
                            || (self.Wire[*AirResource] == 0
                                && Wire.binary_search(AirResource).is_err()
                                && self.Support[*AirResource] == 0
                                && Support.binary_search(AirResource).is_err())
                    })
            })
    }

    fn Add(&mut self, Candidate: &AssignmentCandidate, Owner: usize) {
        let (Wire, Support, Air, Electrical) = Candidate.Claims.IndexSets();
        for Resource in Wire {
            for (OtherWire, AirResource) in &self.CrossAirByWire[*Resource] {
                let ExistingOtherWireCount = self
                    .WireByOwner
                    .get(&(*OtherWire, Owner))
                    .copied()
                    .unwrap_or(0);
                self.Air[*AirResource] += ExistingOtherWireCount;
            }
        }
        for Resource in Wire {
            self.Wire[*Resource] += 1;
            *self.WireByOwner.entry((*Resource, Owner)).or_default() += 1;
        }
        for Resource in Support {
            self.Support[*Resource] += 1;
        }
        for Resource in Air {
            self.Air[*Resource] += 1;
        }
        for Resource in Electrical {
            self.Electrical[*Resource] += 1;
            *self
                .ElectricalByOwner
                .entry((*Resource, Owner))
                .or_default() += 1;
        }
    }

    fn Remove(&mut self, Candidate: &AssignmentCandidate, Owner: usize) {
        let (Wire, Support, Air, Electrical) = Candidate.Claims.IndexSets();
        for Resource in Wire {
            for (OtherWire, AirResource) in &self.CrossAirByWire[*Resource] {
                let ExistingOtherWireCount = self
                    .WireByOwner
                    .get(&(*OtherWire, Owner))
                    .copied()
                    .unwrap_or(0);
                let CandidateOtherWireCount = usize::from(Wire.binary_search(OtherWire).is_ok());
                self.Air[*AirResource] -=
                    ExistingOtherWireCount.saturating_sub(CandidateOtherWireCount);
            }
        }
        for Resource in Wire {
            self.Wire[*Resource] -= 1;
            let Key = (*Resource, Owner);
            let Value = self
                .WireByOwner
                .get_mut(&Key)
                .expect("selected wire owner occupancy");
            *Value -= 1;
            if *Value == 0 {
                self.WireByOwner.remove(&Key);
            }
        }
        for Resource in Support {
            self.Support[*Resource] -= 1;
        }
        for Resource in Air {
            self.Air[*Resource] -= 1;
        }
        for Resource in Electrical {
            self.Electrical[*Resource] -= 1;
            let Key = (*Resource, Owner);
            let Value = self
                .ElectricalByOwner
                .get_mut(&Key)
                .expect("selected electrical owner occupancy");
            *Value -= 1;
            if *Value == 0 {
                self.ElectricalByOwner.remove(&Key);
            }
        }
    }
}

#[derive(Clone)]
struct LayeredCatalogSelectionState {
    SelectedByVariable: BTreeMap<String, usize>,
    SelectedOrder: Vec<(String, usize)>,
    RequirementChoices: BTreeMap<String, String>,
    Occupancy: LayeredCatalogClaimOccupancy,
}

impl LayeredCatalogSelectionState {
    fn New(ResourceCount: usize, CrossAirByWire: Arc<Vec<Vec<(usize, usize)>>>) -> Self {
        Self {
            SelectedByVariable: BTreeMap::new(),
            SelectedOrder: Vec::new(),
            RequirementChoices: BTreeMap::new(),
            Occupancy: LayeredCatalogClaimOccupancy::New(ResourceCount, CrossAirByWire),
        }
    }
}

struct LayeredCatalogSearchContext<'a> {
    Groups: &'a BTreeMap<String, Vec<AssignmentCandidate>>,
    CandidateIndexByIdByVariable: &'a HashMap<String, HashMap<String, usize>>,
    PreferredCandidateIdByVariable: &'a HashMap<String, String>,
    AccessChoicesByPortalRequirement: &'a HashMap<(String, String), Vec<(String, usize)>>,
    AccessChoicesByStubRequirement: &'a HashMap<(String, String), Vec<(String, usize)>>,
    OwnerIndexByName: &'a HashMap<String, usize>,
    SharedExpansionCount: &'a std::sync::atomic::AtomicUsize,
    MaximumExpansionCount: usize,
    Deadline: &'a RuntimeDeadline,
    ExpansionCount: usize,
    MaximumSelectedCount: usize,
    DeepestFailureDepth: usize,
    DeepestFailureNet: Option<String>,
    SearchVariant: usize,
    LocalMaximumExpansionCount: Option<usize>,
    LocalBudgetExhausted: bool,
    BudgetExhausted: bool,
    DeadlineExceeded: bool,
    FailureNet: Option<String>,
}

fn RecordLayeredCatalogFailure(
    Context: &mut LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    Variable: &str,
) {
    if State.SelectedByVariable.len() >= Context.DeepestFailureDepth {
        Context.DeepestFailureDepth = State.SelectedByVariable.len();
        Context.DeepestFailureNet = Some(Variable.to_string());
    }
    Context.FailureNet = Some(Variable.to_string());
}

fn LayeredCatalogRotatedIndices(
    Count: usize,
    Identity: &str,
    SearchVariant: usize,
) -> std::vec::IntoIter<usize> {
    if Count == 0 {
        return Vec::new().into_iter();
    }
    if Count == 1 {
        return vec![0].into_iter();
    }
    let IdentityValue = Identity.bytes().fold(0usize, |Value, Byte| {
        Value.wrapping_mul(131).wrapping_add(Byte as usize)
    });
    let AlternativeCount = Count - 1;
    let AlternativeStart = IdentityValue
        .wrapping_add(SearchVariant)
        % AlternativeCount;
    let mut Result = Vec::with_capacity(Count);
    // Candidate zero is the exact pairwise warm-start value after the
    // deterministic domain reordering above.  Preserve that information while
    // still rotating the remaining alternatives between bounded branches.
    Result.push(0);
    Result.extend(
        (0..AlternativeCount).map(|Offset| 1 + (AlternativeStart + Offset) % AlternativeCount),
    );
    Result.into_iter()
}

fn ConsumeLayeredCatalogExpansion(
    Context: &mut LayeredCatalogSearchContext,
    Variable: &str,
) -> bool {
    if Context.Deadline.Check() {
        Context.DeadlineExceeded = true;
        Context.FailureNet = Some(Variable.to_string());
        return false;
    }
    if Context
        .LocalMaximumExpansionCount
        .is_some_and(|Maximum| Context.ExpansionCount >= Maximum)
    {
        Context.LocalBudgetExhausted = true;
        Context.FailureNet = Some(Variable.to_string());
        return false;
    }
    if Context
        .SharedExpansionCount
        .fetch_update(
            std::sync::atomic::Ordering::SeqCst,
            std::sync::atomic::Ordering::SeqCst,
            |Value| (Value < Context.MaximumExpansionCount).then_some(Value + 1),
        )
        .is_err()
    {
        Context.BudgetExhausted = true;
        Context.FailureNet = Some(Variable.to_string());
        return false;
    }
    Context.ExpansionCount += 1;
    true
}

fn RollbackLayeredCatalogSelection(
    Context: &LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    SelectedCheckpoint: usize,
    NewRequirementNames: &mut Vec<String>,
) {
    while State.SelectedOrder.len() > SelectedCheckpoint {
        let (Variable, CandidateIndex) = State
            .SelectedOrder
            .pop()
            .expect("selected catalog value beyond checkpoint");
        let Candidate = &Context.Groups[&Variable][CandidateIndex];
        let Owner = Context.OwnerIndexByName[&Candidate.OwnerSignal];
        State.Occupancy.Remove(Candidate, Owner);
        State.SelectedByVariable.remove(&Variable);
    }
    for Name in NewRequirementNames.drain(..).rev() {
        State.RequirementChoices.remove(&Name);
    }
}

fn ApplyLayeredCatalogCandidate(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    Variable: &str,
    CandidateIndex: usize,
    NewRequirementNames: &mut Vec<String>,
) -> bool {
    if let Some(SelectedIndex) = State.SelectedByVariable.get(Variable) {
        return *SelectedIndex == CandidateIndex;
    }
    let Candidate = &Context.Groups[Variable][CandidateIndex];
    for (Name, Value) in Candidate.TemplateRequirements.iter() {
        if State
            .RequirementChoices
            .get(Name)
            .is_some_and(|SelectedValue| SelectedValue != Value)
        {
            return false;
        }
    }
    let Owner = Context.OwnerIndexByName[&Candidate.OwnerSignal];
    if !State.Occupancy.IsCompatible(Candidate, Owner) {
        return false;
    }
    for (Name, Value) in Candidate.TemplateRequirements.iter() {
        if !State.RequirementChoices.contains_key(Name) {
            State.RequirementChoices.insert(Name.clone(), Value.clone());
            NewRequirementNames.push(Name.clone());
        }
    }
    State
        .SelectedByVariable
        .insert(Variable.to_string(), CandidateIndex);
    State.Occupancy.Add(Candidate, Owner);
    State
        .SelectedOrder
        .push((Variable.to_string(), CandidateIndex));
    Context.MaximumSelectedCount = Context
        .MaximumSelectedCount
        .max(State.SelectedByVariable.len());
    true
}

fn ApplyCompatibleWarmLayeredCatalogAccess(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    AccessVariables: &[String],
    WarmCandidateIdByVariable: &HashMap<String, String>,
    WarmRequirementNames: &mut Vec<String>,
) -> bool {
    for Variable in AccessVariables {
        if State.SelectedByVariable.contains_key(Variable) {
            continue;
        }
        let Some(PreferredCandidateId) = WarmCandidateIdByVariable.get(Variable) else {
            continue;
        };
        let Some(CandidateIndex) = Context.Groups[Variable]
            .iter()
            .position(|Candidate| Candidate.CandidateId == *PreferredCandidateId)
        else {
            continue;
        };
        let Candidate = &Context.Groups[Variable][CandidateIndex];
        let PortalContractIsFixed = Candidate.TemplateRequirements.iter().all(|(Name, Value)| {
            !Name.starts_with("access-portal:") || State.RequirementChoices.get(Name) == Some(Value)
        });
        if !PortalContractIsFixed {
            continue;
        }
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if !ConsumeLayeredCatalogExpansion(Context, Variable) {
            return false;
        }
        if ApplyLayeredCatalogCandidate(
            Context,
            State,
            Variable,
            CandidateIndex,
            &mut NewRequirementNames,
        ) {
            WarmRequirementNames.extend(NewRequirementNames);
        } else {
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
        }
    }
    true
}

fn LayeredCatalogVariableIsEligible(
    Context: &LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    Variable: &str,
    Candidates: &[AssignmentCandidate],
) -> bool {
    if !Variable.starts_with("__access_terminal__:") {
        return true;
    }
    Candidates
        .first()
        .and_then(|Candidate| {
            Candidate
                .TemplateRequirements
                .iter()
                .find(|(Name, _Value)| Name.starts_with("access-portal:"))
        })
        .is_none_or(|(Name, _Value)| {
            State.RequirementChoices.contains_key(Name)
                || !Context.Groups.iter().any(|(OtherVariable, Values)| {
                    OtherVariable.starts_with("__route_guide__:")
                        && Values.iter().any(|Candidate| {
                            Candidate
                                .TemplateRequirements
                                .iter()
                                .any(|(RequirementName, _RequirementValue)| {
                                    RequirementName == Name
                                })
                        })
                })
        })
}

fn SearchLayeredCatalogFactors(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        return false;
    }
    if State.SelectedByVariable.len() == Context.Groups.len() {
        return true;
    }
    let mut BestVariable = None::<String>;
    let mut BestCandidates = Vec::<usize>::new();
    for (Variable, Candidates) in Context.Groups {
        if State.SelectedByVariable.contains_key(Variable)
            || !LayeredCatalogVariableIsEligible(Context, State, Variable, Candidates)
        {
            continue;
        }
        let mut ViableCandidates = Vec::new();
        for CandidateIndex in 0..Candidates.len() {
            if Context.Deadline.Check() {
                Context.DeadlineExceeded = true;
                Context.FailureNet = Some(Variable.clone());
                return false;
            }
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let Viable = ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            if Viable {
                ViableCandidates.push(CandidateIndex);
            }
        }
        if ViableCandidates.is_empty() {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        if BestVariable.as_ref().is_none_or(|BestName| {
            (ViableCandidates.len(), Variable) < (BestCandidates.len(), BestName)
        }) {
            BestVariable = Some(Variable.clone());
            BestCandidates = ViableCandidates;
        }
    }
    let Some(Variable) = BestVariable else {
        Context.FailureNet = Context
            .Groups
            .keys()
            .find(|Variable| !State.SelectedByVariable.contains_key(*Variable))
            .cloned();
        return false;
    };
    let DiversifiedCandidateOrder = LayeredCatalogRotatedIndices(
        BestCandidates.len(),
        &Variable,
        Context.SearchVariant,
    )
    .map(|ChoiceIndex| BestCandidates[ChoiceIndex])
    .collect::<Vec<_>>();
    let FutureVariables = Context
        .Groups
        .keys()
        .filter(|FutureVariable| {
            FutureVariable.as_str() != Variable
                && !State.SelectedByVariable.contains_key(*FutureVariable)
        })
        .cloned()
        .collect::<Vec<_>>();
    let mut RankedCandidateOrder = Vec::with_capacity(DiversifiedCandidateOrder.len());
    for (OrderIndex, CandidateIndex) in
        DiversifiedCandidateOrder.into_iter().enumerate()
    {
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        let Applied = ApplyLayeredCatalogCandidate(
            Context,
            State,
            &Variable,
            CandidateIndex,
            &mut NewRequirementNames,
        );
        let mut FutureDeadEndCount = 0usize;
        let mut FutureConflictCount = 0usize;
        if Applied {
            for FutureVariable in &FutureVariables {
                let FutureCandidates = &Context.Groups[FutureVariable];
                if !LayeredCatalogVariableIsEligible(
                    Context,
                    State,
                    FutureVariable,
                    FutureCandidates,
                ) {
                    continue;
                }
                let mut CompatibleFutureCount = 0usize;
                for FutureCandidateIndex in 0..FutureCandidates.len() {
                    if Context.Deadline.Check() {
                        Context.DeadlineExceeded = true;
                        Context.FailureNet = Some(FutureVariable.clone());
                        break;
                    }
                    let FutureCheckpoint = State.SelectedOrder.len();
                    let mut FutureRequirementNames = Vec::new();
                    let FutureSupported = ApplyLayeredCatalogCandidate(
                        Context,
                        State,
                        FutureVariable,
                        FutureCandidateIndex,
                        &mut FutureRequirementNames,
                    );
                    RollbackLayeredCatalogSelection(
                        Context,
                        State,
                        FutureCheckpoint,
                        &mut FutureRequirementNames,
                    );
                    CompatibleFutureCount += usize::from(FutureSupported);
                }
                if Context.DeadlineExceeded {
                    break;
                }
                FutureDeadEndCount += usize::from(CompatibleFutureCount == 0);
                FutureConflictCount = FutureConflictCount.saturating_add(
                    FutureCandidates.len().saturating_sub(CompatibleFutureCount),
                );
            }
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.DeadlineExceeded {
            return false;
        }
        RankedCandidateOrder.push((
            FutureDeadEndCount,
            FutureConflictCount,
            OrderIndex,
            CandidateIndex,
        ));
    }
    RankedCandidateOrder.sort_unstable();
    let CandidateOrder = RankedCandidateOrder
        .into_iter()
        .map(|(_DeadEnds, _Conflicts, _OrderIndex, CandidateIndex)| CandidateIndex)
        .collect::<Vec<_>>();
    for CandidateIndex in CandidateOrder {
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, &Variable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                &Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogFactors(Context, State)
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted
            || Context.DeadlineExceeded
            || Context.LocalBudgetExhausted
        {
            return false;
        }
    }
    Context.FailureNet = Some(Variable);
    false
}

fn SearchLayeredCatalogPortalChoices(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    GuideVariable: &str,
    GuideCandidateIndex: usize,
    PortalRequirements: &[(String, String)],
    RequirementIndex: usize,
    RemainingGuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        return false;
    }
    let Some((Name, Value)) = PortalRequirements.get(RequirementIndex) else {
        let SignalNames = Context.Groups.keys().cloned().collect::<Vec<_>>();
        let SignalIndexByName = SignalNames
            .iter()
            .enumerate()
            .map(|(Index, Variable)| (Variable.as_str(), Index))
            .collect::<HashMap<_, _>>();
        let mut Selection = vec![None; SignalNames.len()];
        let Some(GuideSignalIndex) = SignalIndexByName.get(GuideVariable).copied() else {
            Context.FailureNet = Some(GuideVariable.to_string());
            return false;
        };
        Selection[GuideSignalIndex] = Some(GuideCandidateIndex);
        let Some(Constraint) = Context.Groups[GuideVariable][GuideCandidateIndex]
            .PoweredAccessConstraint
            .as_ref()
        else {
            Context.FailureNet = Some(GuideVariable.to_string());
            return false;
        };
        for AccessVariable in Constraint.TerminalVariables.iter() {
            let Some(AccessSignalIndex) = SignalIndexByName.get(AccessVariable.as_str()).copied()
            else {
                Context.FailureNet = Some(AccessVariable.clone());
                return false;
            };
            let Some(AccessCandidateIndex) =
                State.SelectedByVariable.get(AccessVariable).copied()
            else {
                Context.FailureNet = Some(AccessVariable.clone());
                return false;
            };
            Selection[AccessSignalIndex] = Some(AccessCandidateIndex);
        }
        let mut PoweredFailureNet = None;
        match SelectionHasPoweredAccessWitnessExact(
            Context.Groups,
            &SignalNames,
            &Selection,
            Context.Deadline,
            &mut PoweredFailureNet,
        ) {
            Some(true) => {}
            Some(false) => {
                Context.FailureNet = PoweredFailureNet.or_else(|| Some(GuideVariable.to_string()));
                return false;
            }
            None => {
                Context.DeadlineExceeded = true;
                Context.FailureNet = PoweredFailureNet.or_else(|| Some(GuideVariable.to_string()));
                return false;
            }
        }
        return SearchLayeredCatalogGuidesByPortal(
            Context,
            State,
            RemainingGuideVariables,
            AccessVariables,
        );
    };
    let Some(Choices) = Context
        .AccessChoicesByPortalRequirement
        .get(&(Name.clone(), Value.clone()))
    else {
        Context.FailureNet = Some(Name.clone());
        return false;
    };
    let Some(AccessVariable) = Choices.first().map(|(Variable, _Index)| Variable) else {
        Context.FailureNet = Some(Name.clone());
        return false;
    };
    if let Some(SelectedCandidateIndex) = State.SelectedByVariable.get(AccessVariable) {
        let SelectedCandidate = &Context.Groups[AccessVariable][*SelectedCandidateIndex];
        if !SelectedCandidate
            .TemplateRequirements
            .iter()
            .any(|(SelectedName, SelectedValue)| SelectedName == Name && SelectedValue == Value)
        {
            Context.FailureNet = Some(AccessVariable.clone());
            return false;
        }
        return SearchLayeredCatalogPortalChoices(
            Context,
            State,
            GuideVariable,
            GuideCandidateIndex,
            PortalRequirements,
            RequirementIndex + 1,
            RemainingGuideVariables,
            AccessVariables,
        );
    }
    let mut ChoiceOrder = LayeredCatalogRotatedIndices(Choices.len(), Name, Context.SearchVariant)
        .collect::<Vec<_>>();
    let WitnessRequirementName = Name
        .strip_prefix("access-portal:")
        .map(|LogicalKey| format!("access-witness:{LogicalKey}"));
    let PreferredCandidateId = WitnessRequirementName
        .as_ref()
        .and_then(|WitnessName| {
            Context.Groups[GuideVariable][GuideCandidateIndex]
                .TemplateRequirements
                .iter()
                .find(|(RequirementName, _Value)| RequirementName == WitnessName)
                .map(|(_Name, Value)| Value)
        })
        .or_else(|| Context.PreferredCandidateIdByVariable.get(AccessVariable));
    if let Some(PreferredChoicePosition) = PreferredCandidateId.and_then(|PreferredId| {
        ChoiceOrder.iter().position(|ChoiceIndex| {
            let (ChoiceVariable, CandidateIndex) = &Choices[*ChoiceIndex];
            Context.Groups[ChoiceVariable][*CandidateIndex].CandidateId == *PreferredId
        })
    }) {
        let PreferredChoice = ChoiceOrder.remove(PreferredChoicePosition);
        ChoiceOrder.insert(0, PreferredChoice);
    }
    for ChoiceIndex in ChoiceOrder {
        let (ChoiceVariable, CandidateIndex) = &Choices[ChoiceIndex];
        if ChoiceVariable != AccessVariable {
            Context.FailureNet = Some(Name.clone());
            return false;
        }
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, AccessVariable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                AccessVariable,
                *CandidateIndex,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogPortalChoices(
                Context,
                State,
                GuideVariable,
                GuideCandidateIndex,
                PortalRequirements,
                RequirementIndex + 1,
                RemainingGuideVariables,
                AccessVariables,
            )
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted
            || Context.DeadlineExceeded
            || Context.LocalBudgetExhausted
        {
            return false;
        }
        if Context
            .FailureNet
            .as_deref()
            .is_some_and(|FailureVariable| {
                FailureVariable.starts_with("__route_guide__:")
                    && FailureVariable != GuideVariable
                    && !RemainingGuideVariables
                        .iter()
                        .any(|Variable| Variable == FailureVariable)
            })
        {
            // A complete witness check found that this repair disconnects a
            // guide which is still fixed to the indexed warm witness.  Bubble
            // that exact guide to the repair frontier immediately instead of
            // exhaustively varying the current guide against a value that is
            // already known to participate in the failure.
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, AccessVariable);
    false
}

fn LayeredCatalogPortalChoicesHaveSupport(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    PortalRequirements: &[(String, String)],
    RequirementIndex: usize,
) -> bool {
    let RemainingRequirements = &PortalRequirements[RequirementIndex..];
    if RemainingRequirements.is_empty() {
        return true;
    }
    let mut SelectedRequirement = None::<(usize, String, Vec<(String, usize)>)>;
    for (RelativeIndex, (Name, Value)) in RemainingRequirements.iter().enumerate() {
        let Choices = Context
            .AccessChoicesByPortalRequirement
            .get(&(Name.clone(), Value.clone()))
            .cloned()
            .unwrap_or_default();
        let Some(AccessVariable) = Choices.first().map(|(Variable, _Index)| Variable.clone())
        else {
            return false;
        };
        if let Some(SelectedCandidateIndex) = State.SelectedByVariable.get(&AccessVariable) {
            if !Context.Groups[&AccessVariable][*SelectedCandidateIndex]
                .TemplateRequirements
                .iter()
                .any(|(SelectedName, SelectedValue)| SelectedName == Name && SelectedValue == Value)
            {
                return false;
            }
            let mut NextRequirements = RemainingRequirements.to_vec();
            NextRequirements.remove(RelativeIndex);
            return LayeredCatalogPortalChoicesHaveSupport(Context, State, &NextRequirements, 0);
        }
        let mut ViableChoices = Vec::new();
        for (ChoiceVariable, CandidateIndex) in Choices {
            if ChoiceVariable != AccessVariable {
                return false;
            }
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let Viable = ApplyLayeredCatalogCandidate(
                Context,
                State,
                &AccessVariable,
                CandidateIndex,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            if Viable {
                ViableChoices.push((AccessVariable.clone(), CandidateIndex));
            }
        }
        if ViableChoices.is_empty() {
            return false;
        }
        if SelectedRequirement
            .as_ref()
            .is_none_or(|(_BestIndex, BestVariable, BestChoices)| {
                (ViableChoices.len(), &AccessVariable) < (BestChoices.len(), BestVariable)
            })
        {
            SelectedRequirement = Some((RelativeIndex, AccessVariable, ViableChoices));
        }
    }
    let Some((RelativeIndex, AccessVariable, ViableChoices)) = SelectedRequirement else {
        return true;
    };
    let mut NextRequirements = RemainingRequirements.to_vec();
    NextRequirements.remove(RelativeIndex);
    let ChoiceOrder =
        LayeredCatalogRotatedIndices(ViableChoices.len(), &AccessVariable, Context.SearchVariant)
            .collect::<Vec<_>>();
    for ChoiceIndex in ChoiceOrder {
        let (_ChoiceVariable, CandidateIndex) = &ViableChoices[ChoiceIndex];
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        let Supported =
            ApplyLayeredCatalogCandidate(
                Context,
                State,
                &AccessVariable,
                *CandidateIndex,
                &mut NewRequirementNames,
            ) && LayeredCatalogPortalChoicesHaveSupport(Context, State, &NextRequirements, 0);
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Supported {
            return true;
        }
    }
    false
}

fn ApplyLayeredCatalogExactStubRequirements(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    Requirements: &[(String, String)],
    CountExpansions: bool,
    NewRequirementNames: &mut Vec<String>,
) -> bool {
    for Requirement in Requirements {
        let Some(Choices) = Context.AccessChoicesByStubRequirement.get(Requirement) else {
            Context.FailureNet = Some(Requirement.0.clone());
            return false;
        };
        if Choices.len() != 1 {
            Context.FailureNet = Some(Requirement.0.clone());
            return false;
        }
        let (Variable, CandidateIndex) = &Choices[0];
        if State.SelectedByVariable.get(Variable) == Some(CandidateIndex) {
            continue;
        }
        if (CountExpansions && !ConsumeLayeredCatalogExpansion(Context, Variable))
            || !ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                *CandidateIndex,
                NewRequirementNames,
            )
        {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
    }
    true
}

fn ApplyLayeredCatalogCertifiedAccessTuple(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    CandidateTuple: &[(String, String)],
    CountExpansions: bool,
    NewRequirementNames: &mut Vec<String>,
) -> bool {
    for (Variable, CandidateId) in CandidateTuple {
        let Some(CandidateIndex) = Context
            .CandidateIndexByIdByVariable
            .get(Variable)
            .and_then(|Indices| Indices.get(CandidateId))
            .copied()
        else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        if State.SelectedByVariable.get(Variable) == Some(&CandidateIndex) {
            continue;
        }
        if (CountExpansions && !ConsumeLayeredCatalogExpansion(Context, Variable))
            || !ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                CandidateIndex,
                NewRequirementNames,
            )
        {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
    }
    true
}

fn LayeredCatalogSelectedGuideUsesCertifiedTuple(
    Context: &LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    Variable: &str,
) -> bool {
    let Some(CandidateIndex) = State.SelectedByVariable.get(Variable).copied() else {
        return false;
    };
    let Some(Constraint) = Context.Groups[Variable][CandidateIndex]
        .PoweredAccessConstraint
        .as_ref()
    else {
        return true;
    };
    Constraint
        .PreferredAccessCandidateTuples
        .iter()
        .any(|CandidateTuple| {
            CandidateTuple.iter().all(|(AccessVariable, CandidateId)| {
                State
                    .SelectedByVariable
                    .get(AccessVariable)
                    .is_some_and(|AccessCandidateIndex| {
                        Context.Groups[AccessVariable][*AccessCandidateIndex].CandidateId
                            == *CandidateId
                    })
            })
        })
}

fn LayeredCatalogSelectedGuideHasPoweredWitness(
    Context: &LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    Variable: &str,
) -> Option<bool> {
    let GuideCandidateIndex = State.SelectedByVariable.get(Variable).copied()?;
    let Constraint = Context.Groups[Variable][GuideCandidateIndex]
        .PoweredAccessConstraint
        .as_ref()?;
    let SignalNames = Context.Groups.keys().cloned().collect::<Vec<_>>();
    let SignalIndexByName = SignalNames
        .iter()
        .enumerate()
        .map(|(Index, Signal)| (Signal.as_str(), Index))
        .collect::<HashMap<_, _>>();
    let mut Selection = vec![None; SignalNames.len()];
    Selection[*SignalIndexByName.get(Variable)?] = Some(GuideCandidateIndex);
    for AccessVariable in Constraint.TerminalVariables.iter() {
        let AccessSignalIndex = *SignalIndexByName.get(AccessVariable.as_str())?;
        Selection[AccessSignalIndex] = State.SelectedByVariable.get(AccessVariable).copied();
        Selection[AccessSignalIndex]?;
    }
    let mut FailureNet = None;
    SelectionHasPoweredAccessWitnessExact(
        Context.Groups,
        &SignalNames,
        &Selection,
        Context.Deadline,
        &mut FailureNet,
    )
}

fn SearchLayeredCatalogSelectedGuideTuples(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    GuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
        return false;
    }
    if GuideVariables.is_empty() {
        if !SearchLayeredCatalogAccessByPortal(Context, State, AccessVariables) {
            return false;
        }
        return State
            .SelectedByVariable
            .keys()
            .filter(|Variable| Variable.starts_with("__route_guide__:"))
            .all(|Variable| {
                LayeredCatalogSelectedGuideHasPoweredWitness(Context, State, Variable)
                    == Some(true)
            });
    }

    let mut BestVariableIndex = 0usize;
    let mut BestVariable = None::<String>;
    let mut BestTupleIndices = Vec::<usize>::new();
    for (VariableIndex, Variable) in GuideVariables.iter().enumerate() {
        if Context.Deadline.Check() {
            Context.DeadlineExceeded = true;
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        let Some(GuideCandidateIndex) = State.SelectedByVariable.get(Variable).copied() else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        let Some(Constraint) = Context.Groups[Variable][GuideCandidateIndex]
            .PoweredAccessConstraint
            .as_ref()
        else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        let mut ViableTupleIndices = Vec::new();
        for (TupleIndex, CandidateTuple) in
            Constraint.PreferredAccessCandidateTuples.iter().enumerate()
        {
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let PreviousFailureNet = Context.FailureNet.clone();
            let Viable = ApplyLayeredCatalogCertifiedAccessTuple(
                Context,
                State,
                CandidateTuple,
                false,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            Context.FailureNet = PreviousFailureNet;
            if Viable {
                ViableTupleIndices.push(TupleIndex);
            }
        }
        if ViableTupleIndices.is_empty() {
            RecordLayeredCatalogFailure(Context, State, Variable);
            return false;
        }
        ViableTupleIndices.sort_by_key(|TupleIndex| {
            Constraint.PreferredAccessCandidateTuples[*TupleIndex]
                .iter()
                .filter(|(AccessVariable, CandidateId)| {
                    Context
                        .PreferredCandidateIdByVariable
                        .get(AccessVariable)
                        .is_some_and(|PreferredId| PreferredId != CandidateId)
                })
                .count()
        });
        if BestVariable.as_ref().is_none_or(|BestName| {
            (ViableTupleIndices.len(), Variable) < (BestTupleIndices.len(), BestName)
        }) {
            BestVariableIndex = VariableIndex;
            BestVariable = Some(Variable.clone());
            BestTupleIndices = ViableTupleIndices;
        }
    }

    let Variable = BestVariable.expect("selected guide tuple search has a best variable");
    let GuideCandidateIndex = State.SelectedByVariable[&Variable];
    let CertifiedTuples = Arc::clone(
        &Context.Groups[&Variable][GuideCandidateIndex]
            .PoweredAccessConstraint
            .as_ref()
            .expect("selected guide tuple search owns an access constraint")
            .PreferredAccessCandidateTuples,
    );
    let mut RemainingGuideVariables = GuideVariables.to_vec();
    RemainingGuideVariables.remove(BestVariableIndex);
    for TupleIndex in BestTupleIndices {
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ApplyLayeredCatalogCertifiedAccessTuple(
            Context,
            State,
            &CertifiedTuples[TupleIndex],
            true,
            &mut NewRequirementNames,
        ) && SearchLayeredCatalogSelectedGuideTuples(
            Context,
            State,
            &RemainingGuideVariables,
            AccessVariables,
        ) {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, &Variable);
    false
}

fn LayeredCatalogAccessVariablesForGuides(
    Context: &LayeredCatalogSearchContext,
    GuideVariables: &BTreeSet<String>,
) -> BTreeSet<String> {
    GuideVariables
        .iter()
        .flat_map(|Variable| Context.Groups[Variable].iter())
        .filter_map(|Candidate| Candidate.PoweredAccessConstraint.as_ref())
        .flat_map(|Constraint| Constraint.TerminalVariables.iter().cloned())
        .collect()
}

fn TryLayeredCatalogGuideCandidate(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    Variable: &str,
    CandidateIndex: usize,
    RemainingGuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    let CertifiedTuples = Context.Groups[Variable][CandidateIndex]
        .PoweredAccessConstraint
        .as_ref()
        .map(|Constraint| Arc::clone(&Constraint.PreferredAccessCandidateTuples))
        .unwrap_or_default();
    let mut CertifiedTupleOrder = (0..CertifiedTuples.len()).collect::<Vec<_>>();
    CertifiedTupleOrder.sort_by_key(|TupleIndex| {
        (
            CertifiedTuples[*TupleIndex]
                .iter()
                .filter(|(AccessVariable, CandidateId)| {
                    Context
                        .PreferredCandidateIdByVariable
                        .get(AccessVariable)
                        .is_some_and(|PreferredId| PreferredId != CandidateId)
                })
                .count(),
            *TupleIndex,
        )
    });
    for TupleIndex in CertifiedTupleOrder {
        let CandidateTuple = &CertifiedTuples[TupleIndex];
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, Variable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            )
            && ApplyLayeredCatalogCertifiedAccessTuple(
                Context,
                State,
                CandidateTuple,
                true,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogGuidesByPortal(
                Context,
                State,
                RemainingGuideVariables,
                AccessVariables,
            )
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted
            || Context.DeadlineExceeded
            || Context.LocalBudgetExhausted
        {
            return false;
        }
    }
    let SelectedCheckpoint = State.SelectedOrder.len();
    let mut NewRequirementNames = Vec::new();
    let mut PortalRequirements = Context.Groups[Variable][CandidateIndex]
        .TemplateRequirements
        .iter()
        .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
        .cloned()
        .collect::<Vec<_>>();
    PortalRequirements.sort_by_key(|Requirement| {
        (
            Context
                .AccessChoicesByPortalRequirement
                .get(Requirement)
                .map(Vec::len)
                .unwrap_or(0),
            Requirement.clone(),
        )
    });
    if ConsumeLayeredCatalogExpansion(Context, Variable)
        && ApplyLayeredCatalogCandidate(
            Context,
            State,
            Variable,
            CandidateIndex,
            &mut NewRequirementNames,
        )
        && SearchLayeredCatalogPortalChoices(
            Context,
            State,
            Variable,
            CandidateIndex,
            &PortalRequirements,
            0,
            RemainingGuideVariables,
            AccessVariables,
        )
    {
        return true;
    }
    RollbackLayeredCatalogSelection(
        Context,
        State,
        SelectedCheckpoint,
        &mut NewRequirementNames,
    );
    false
}

/// Search the exact powered witness basis without materializing unioned guide
/// candidates.  Every value is a reference to one guide candidate plus one of
/// its certified access tuples; applying those referenced candidates to the
/// shared occupancy is equivalent to the expanded bundle domain, but keeps the
/// catalog factorized.  The retained tuples are a sufficient witness basis,
/// not an exhaustive relation, so a failed search is only a seed failure and
/// must fall through to the complete portal search.
fn SearchLayeredCatalogCertifiedBundleSeed(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    GuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
        return false;
    }
    if GuideVariables.is_empty() {
        if !SearchLayeredCatalogAccessByPortal(Context, State, AccessVariables) {
            return false;
        }
        return State
            .SelectedByVariable
            .keys()
            .filter(|Variable| Variable.starts_with("__route_guide__:"))
            .all(|Variable| {
                LayeredCatalogSelectedGuideHasPoweredWitness(Context, State, Variable)
                    == Some(true)
            });
    }

    let mut BestVariableIndex = 0usize;
    let mut BestVariable = None::<String>;
    let mut BestBundles = Vec::<(usize, usize, usize)>::new();
    for (VariableIndex, Variable) in GuideVariables.iter().take(1).enumerate() {
        if Context.Deadline.Check() {
            Context.DeadlineExceeded = true;
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        let mut Bundles = Vec::<(usize, usize, usize)>::new();
        let mut OrderIndex = 0usize;
        for CandidateIndex in LayeredCatalogRotatedIndices(
            Context.Groups[Variable].len(),
            Variable,
            Context.SearchVariant,
        ) {
            let Candidate = &Context.Groups[Variable][CandidateIndex];
            let Some(Constraint) = Candidate.PoweredAccessConstraint.as_ref() else {
                continue;
            };
            for (TupleIndex, CandidateTuple) in
                Constraint.PreferredAccessCandidateTuples.iter().enumerate()
            {
                let SelectedCheckpoint = State.SelectedOrder.len();
                let mut NewRequirementNames = Vec::new();
                let PreviousFailureNet = Context.FailureNet.clone();
                let Viable = ApplyLayeredCatalogCandidate(
                    Context,
                    State,
                    Variable,
                    CandidateIndex,
                    &mut NewRequirementNames,
                ) && ApplyLayeredCatalogCertifiedAccessTuple(
                    Context,
                    State,
                    CandidateTuple,
                    false,
                    &mut NewRequirementNames,
                );
                RollbackLayeredCatalogSelection(
                    Context,
                    State,
                    SelectedCheckpoint,
                    &mut NewRequirementNames,
                );
                Context.FailureNet = PreviousFailureNet;
                if Viable {
                    Bundles.push((CandidateIndex, TupleIndex, OrderIndex));
                }
                OrderIndex = OrderIndex.saturating_add(1);
            }
        }
        if Bundles.is_empty() {
            RecordLayeredCatalogFailure(Context, State, Variable);
            return false;
        }
        Bundles.sort_by_key(|(CandidateIndex, TupleIndex, OriginalOrder)| {
            let Candidate = &Context.Groups[Variable][*CandidateIndex];
            let CandidateTuple = &Candidate
                .PoweredAccessConstraint
                .as_ref()
                .expect("certified bundle seed owns an access constraint")
                .PreferredAccessCandidateTuples[*TupleIndex];
            let WarmMismatchCount = usize::from(
                Context
                    .PreferredCandidateIdByVariable
                    .get(Variable)
                    .is_some_and(|CandidateId| CandidateId != &Candidate.CandidateId),
            ) + CandidateTuple
                .iter()
                .filter(|(AccessVariable, CandidateId)| {
                    Context
                        .PreferredCandidateIdByVariable
                        .get(AccessVariable)
                        .is_some_and(|PreferredId| PreferredId != CandidateId)
                })
                .count();
            (WarmMismatchCount, *OriginalOrder)
        });
        if BestVariable.as_ref().is_none_or(|BestName| {
            (Bundles.len(), Variable) < (BestBundles.len(), BestName)
        }) {
            BestVariableIndex = VariableIndex;
            BestVariable = Some(Variable.clone());
            BestBundles = Bundles;
        }
    }

    let Variable = BestVariable.expect("nonempty guide seed has a best variable");
    let mut RemainingGuideVariables = GuideVariables.to_vec();
    RemainingGuideVariables.remove(BestVariableIndex);
    for (CandidateIndex, TupleIndex, _OriginalOrder) in BestBundles {
        let CandidateTuple = Arc::clone(
            &Context.Groups[&Variable][CandidateIndex]
                .PoweredAccessConstraint
                .as_ref()
                .expect("certified bundle seed owns an access constraint")
                .PreferredAccessCandidateTuples,
        );
        let CandidateTuple = &CandidateTuple[TupleIndex];
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, &Variable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                &Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            )
            && ApplyLayeredCatalogCertifiedAccessTuple(
                Context,
                State,
                CandidateTuple,
                true,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogCertifiedBundleSeed(
                Context,
                State,
                &RemainingGuideVariables,
                AccessVariables,
            )
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, &Variable);
    false
}

fn SearchLayeredCatalogGuidesByPortal(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    GuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        return false;
    }
    if GuideVariables.is_empty() {
        if !SearchLayeredCatalogAccessByPortal(Context, State, AccessVariables) {
            return false;
        }
        return State
            .SelectedByVariable
            .keys()
            .filter(|Variable| Variable.starts_with("__route_guide__:"))
            .all(|Variable| {
                LayeredCatalogSelectedGuideHasPoweredWitness(Context, State, Variable)
                    == Some(true)
            });
    }
    let mut BestVariableIndex = 0usize;
    let mut BestVariable = None::<String>;
    let mut BestViableCandidates = Vec::new();
    // The domains are already deterministically ordered by finite candidate
    // count.  Probing every remaining guide and every certified tuple at each
    // node repeats almost the entire RCA catalog merely to recompute MRV.
    // Selecting the first ordered guide preserves the complete DFS domain and
    // the shared work/deadline bounds while making each tuple probe occur only
    // when that guide is actually branched.
    for (VariableIndex, Variable) in GuideVariables.iter().take(1).enumerate() {
        let mut ViableCandidates = Vec::new();
        let mut CandidateOrder = LayeredCatalogRotatedIndices(
            Context.Groups[Variable].len(),
            Variable,
            Context.SearchVariant,
        )
        .collect::<Vec<_>>();
        if let Some(PreferredCandidateIndex) = Context
            .PreferredCandidateIdByVariable
            .get(Variable)
            .and_then(|CandidateId| {
                Context.CandidateIndexByIdByVariable[Variable].get(CandidateId)
            })
            .copied()
        {
            if let Some(PreferredPosition) = CandidateOrder
                .iter()
                .position(|CandidateIndex| *CandidateIndex == PreferredCandidateIndex)
            {
                CandidateOrder[..=PreferredPosition].rotate_right(1);
            }
        }
        for CandidateIndex in CandidateOrder {
            if Context.Deadline.Check() {
                Context.DeadlineExceeded = true;
                Context.FailureNet = Some(Variable.clone());
                return false;
            }
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let Supported = ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            if Supported {
                ViableCandidates.push(CandidateIndex);
            }
        }
        if ViableCandidates.is_empty() {
            RecordLayeredCatalogFailure(Context, State, Variable);
            return false;
        }
        if BestVariable.as_ref().is_none_or(|BestName| {
            (ViableCandidates.len(), Variable) < (BestViableCandidates.len(), BestName)
        }) {
            BestVariableIndex = VariableIndex;
            BestVariable = Some(Variable.clone());
            BestViableCandidates = ViableCandidates;
        }
    }
    let Variable = BestVariable.expect("nonempty guide frontier has a best variable");
    let mut RemainingGuideVariables = GuideVariables.to_vec();
    RemainingGuideVariables.remove(BestVariableIndex);
    let RankedCandidateIndices = BestViableCandidates;
    let PreviousLocalMaximumExpansionCount = Context.LocalMaximumExpansionCount;
    let PreviousLocalBudgetExhausted = Context.LocalBudgetExhausted;
    let RemainingExpansionCount = PreviousLocalMaximumExpansionCount
        .unwrap_or(Context.MaximumExpansionCount)
        .saturating_sub(Context.ExpansionCount);
    let ShallowBranchExpansionAllowance = RemainingExpansionCount
        .checked_div(RankedCandidateIndices.len().saturating_add(1))
        .unwrap_or(0)
        .clamp(64, 1_024);
    let mut DeferredCandidateIndices = Vec::new();
    for CandidateIndex in &RankedCandidateIndices {
        Context.LocalMaximumExpansionCount = Some(
            PreviousLocalMaximumExpansionCount
                .unwrap_or(Context.MaximumExpansionCount)
                .min(
                    Context
                        .ExpansionCount
                        .saturating_add(ShallowBranchExpansionAllowance),
                ),
        );
        Context.LocalBudgetExhausted = false;
        if TryLayeredCatalogGuideCandidate(
            Context,
            State,
            &Variable,
            *CandidateIndex,
            &RemainingGuideVariables,
            AccessVariables,
        ) {
            Context.LocalMaximumExpansionCount = PreviousLocalMaximumExpansionCount;
            Context.LocalBudgetExhausted = PreviousLocalBudgetExhausted;
            return true;
        }
        let BranchBudgetExhausted = Context.LocalBudgetExhausted;
        Context.LocalMaximumExpansionCount = PreviousLocalMaximumExpansionCount;
        Context.LocalBudgetExhausted = PreviousLocalBudgetExhausted;
        if BranchBudgetExhausted {
            DeferredCandidateIndices.push(*CandidateIndex);
        }
        if Context.BudgetExhausted || Context.DeadlineExceeded {
            return false;
        }
    }
    for CandidateIndex in DeferredCandidateIndices {
        if TryLayeredCatalogGuideCandidate(
            Context,
            State,
            &Variable,
            CandidateIndex,
            &RemainingGuideVariables,
            AccessVariables,
        ) {
            return true;
        }
        if Context.BudgetExhausted || Context.DeadlineExceeded {
            return false;
        }
        if Context.LocalBudgetExhausted {
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, &Variable);
    false
}

fn SearchLayeredCatalogAccessByPortal(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        return false;
    }
    let mut BestVariable = None::<String>;
    let mut BestChoices = Vec::<(String, usize)>::new();
    for Variable in AccessVariables {
        if State.SelectedByVariable.contains_key(Variable) {
            continue;
        }
        let Some((PortalName, _PortalValue)) =
            Context.Groups[Variable].first().and_then(|Candidate| {
                Candidate
                    .TemplateRequirements
                    .iter()
                    .find(|(Name, _Value)| Name.starts_with("access-portal:"))
            })
        else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        let Some(SelectedPortalValue) = State.RequirementChoices.get(PortalName) else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        let Some(Choices) = Context
            .AccessChoicesByPortalRequirement
            .get(&(PortalName.clone(), SelectedPortalValue.clone()))
        else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        if Choices.is_empty() {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        let mut ViableChoices = Vec::new();
        for (ChoiceVariable, CandidateIndex) in Choices {
            if ChoiceVariable != Variable {
                Context.FailureNet = Some(Variable.clone());
                return false;
            }
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let Viable = ApplyLayeredCatalogCandidate(
                Context,
                State,
                ChoiceVariable,
                *CandidateIndex,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            if Viable {
                ViableChoices.push((ChoiceVariable.clone(), *CandidateIndex));
            }
        }
        if ViableChoices.is_empty() {
            RecordLayeredCatalogFailure(Context, State, Variable);
            return false;
        }
        if BestVariable
            .as_ref()
            .is_none_or(|BestName| (ViableChoices.len(), Variable) < (BestChoices.len(), BestName))
        {
            BestVariable = Some(Variable.clone());
            BestChoices = ViableChoices;
        }
    }
    let Some(Variable) = BestVariable else {
        return true;
    };
    for (ChoiceVariable, CandidateIndex) in BestChoices {
        if ChoiceVariable != Variable {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, &Variable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                &Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogAccessByPortal(Context, State, AccessVariables)
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded {
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, &Variable);
    false
}

fn LayeredCatalogGuideVariableForFailure(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    FailureVariable: Option<&str>,
) -> Option<String> {
    let FailureVariable = FailureVariable?;
    if FailureVariable.starts_with("__route_guide__:") {
        return Groups
            .contains_key(FailureVariable)
            .then(|| FailureVariable.to_string());
    }
    let OwnerSignal = Groups.get(FailureVariable)?.first()?.OwnerSignal.clone();
    let GuideVariable = format!("__route_guide__:{OwnerSignal}");
    Groups.contains_key(&GuideVariable).then_some(GuideVariable)
}

fn LayeredCatalogBlockingSelectedGuide(
    Context: &LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    RepairGuideVariables: &BTreeSet<String>,
) -> Option<String> {
    let SelectedGuides = State
        .SelectedByVariable
        .iter()
        .filter(|(Variable, _CandidateIndex)| {
            Variable.starts_with("__route_guide__:") && !RepairGuideVariables.contains(*Variable)
        })
        .map(|(Variable, CandidateIndex)| (Variable, &Context.Groups[Variable][*CandidateIndex]))
        .collect::<Vec<_>>();
    let mut ConflictCounts = BTreeMap::<String, usize>::new();
    for RepairVariable in RepairGuideVariables {
        for Candidate in Context.Groups[RepairVariable].iter().take(32) {
            for (SelectedVariable, SelectedCandidate) in &SelectedGuides {
                if Candidate.Claims.Conflicts(&SelectedCandidate.Claims) {
                    *ConflictCounts
                        .entry((*SelectedVariable).clone())
                        .or_default() += 1;
                }
            }
            for Requirement in Candidate
                .TemplateRequirements
                .iter()
                .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
            {
                for (AccessVariable, AccessCandidateIndex) in Context
                    .AccessChoicesByPortalRequirement
                    .get(Requirement)
                    .into_iter()
                    .flatten()
                {
                    let AccessCandidate = &Context.Groups[AccessVariable][*AccessCandidateIndex];
                    for (SelectedVariable, SelectedCandidate) in &SelectedGuides {
                        if AccessCandidate.Claims.Conflicts(&SelectedCandidate.Claims) {
                            *ConflictCounts
                                .entry((*SelectedVariable).clone())
                                .or_default() += 1;
                        }
                    }
                }
            }
        }
    }
    ConflictCounts
        .into_iter()
        .max_by(|First, Second| First.1.cmp(&Second.1).then_with(|| Second.0.cmp(&First.0)))
        .filter(|(_Variable, Count)| *Count > 0)
        .map(|(Variable, _Count)| Variable)
}

type LayeredCatalogBundleDecodeMap = HashMap<(String, String), Vec<(String, String)>>;

fn BuildUniqueLayeredCatalogBundleGroups(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Deadline: &RuntimeDeadline,
) -> PyResult<
    Option<(
        BTreeMap<String, Vec<AssignmentCandidate>>,
        LayeredCatalogBundleDecodeMap,
    )>,
> {
    let AccessVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__access_terminal__:"))
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut ReferencedAccessVariables = BTreeSet::<String>::new();
    let mut BundledGroups = Groups
        .iter()
        .filter(|(Variable, _Values)| Variable.starts_with("__base_claim__:"))
        .map(|(Variable, Values)| (Variable.clone(), Values.clone()))
        .collect::<BTreeMap<_, _>>();
    let mut DecodeMap = LayeredCatalogBundleDecodeMap::new();
    for (Variable, Values) in Groups
        .iter()
        .filter(|(Variable, _Values)| Variable.starts_with("__route_guide__:"))
    {
        let mut BundledValues = Vec::with_capacity(Values.len());
        for Candidate in Values {
            if Deadline.Check() {
                return Ok(None);
            }
            let Some(Constraint) = Candidate.PoweredAccessConstraint.as_ref() else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "compact guide bundle is missing its exact access certificate",
                ));
            };
            let mut AccessSelectionCombinations = Vec::new();
            for CandidateTuple in Constraint.PreferredAccessCandidateTuples.iter() {
                let mut AccessSelections = BTreeMap::<String, usize>::new();
                for (AccessVariable, AccessCandidateId) in CandidateTuple {
                    let Some(AccessCandidateIndex) = Groups
                        .get(AccessVariable)
                        .and_then(|Values| {
                            Values.iter().position(|Value| {
                                Value.CandidateId == *AccessCandidateId
                            })
                        })
                    else {
                        return Err(pyo3::exceptions::PyValueError::new_err(
                            "layered guide certificate references an unknown access candidate",
                        ));
                    };
                    if AccessSelections
                        .insert(AccessVariable.clone(), AccessCandidateIndex)
                        .is_some_and(|Previous| Previous != AccessCandidateIndex)
                    {
                        return Err(pyo3::exceptions::PyValueError::new_err(
                            "layered guide certificate assigns one access variable twice",
                        ));
                    }
                }
                AccessSelectionCombinations.push(AccessSelections);
            }
            AccessSelectionCombinations.sort();
            AccessSelectionCombinations.dedup();
            let CombinationCount = AccessSelectionCombinations.len();
            for (CombinationIndex, AccessSelections) in
                AccessSelectionCombinations.into_iter().enumerate()
            {
                let mut CombinedClaims = (*Candidate.Claims).clone();
                let mut CombinedRequirements = Candidate
                    .TemplateRequirements
                    .iter()
                    .filter(|(Name, _Value)| {
                        !Name.starts_with("access-stub:")
                            && !Name.starts_with("access-portal:")
                    })
                    .cloned()
                    .collect::<Vec<_>>();
                let mut DecodedValues = vec![(Variable.clone(), Candidate.CandidateId.clone())];
                let mut MaterialCost = Candidate.MaterialCost;
                let mut FootprintGrowth = Candidate.FootprintGrowth;
                let mut Length = Candidate.Length;
                let mut BendCount = Candidate.BendCount;
                let mut ViaCount = Candidate.ViaCount;
                let mut SelfLegal = true;
                for (AccessVariable, AccessCandidateIndex) in AccessSelections {
                    let AccessCandidate = Groups
                        .get(&AccessVariable)
                        .and_then(|Candidates| Candidates.get(AccessCandidateIndex))
                        .ok_or_else(|| {
                            pyo3::exceptions::PyValueError::new_err(
                                "layered guide requirement references an unknown access candidate",
                            )
                        })?;
                    if AccessCandidate.OwnerSignal != Candidate.OwnerSignal {
                        return Err(pyo3::exceptions::PyValueError::new_err(
                            "layered guide and required access choice have different owners",
                        ));
                    }
                    let Some(SelfConflict) = CombinedClaims
                        .SameOwnerConflictsWithDeadline(&AccessCandidate.Claims, Deadline)
                    else {
                        return Ok(None);
                    };
                    if SelfConflict {
                        SelfLegal = false;
                        break;
                    }
                    if !CombinedClaims.UnionWithDeadline(&AccessCandidate.Claims, Deadline) {
                        return Ok(None);
                    }
                    CombinedRequirements.extend(
                        AccessCandidate
                            .TemplateRequirements
                            .iter()
                            .filter(|(Name, _Value)| {
                                !Name.starts_with("access-stub:")
                                    && !Name.starts_with("access-portal:")
                            })
                            .cloned(),
                    );
                    MaterialCost = MaterialCost.saturating_add(AccessCandidate.MaterialCost);
                    FootprintGrowth =
                        FootprintGrowth.saturating_add(AccessCandidate.FootprintGrowth);
                    Length = Length.saturating_add(AccessCandidate.Length);
                    BendCount = BendCount.saturating_add(AccessCandidate.BendCount);
                    ViaCount = ViaCount.saturating_add(AccessCandidate.ViaCount);
                    ReferencedAccessVariables.insert(AccessVariable.clone());
                    DecodedValues.push((AccessVariable, AccessCandidate.CandidateId.clone()));
                }
                if !SelfLegal {
                    continue;
                }
                CombinedRequirements.sort();
                if CombinedRequirements
                    .windows(2)
                    .any(|Pair| Pair[0].0 == Pair[1].0 && Pair[0].1 != Pair[1].1)
                {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "layered bundle contains incompatible named requirements",
                    ));
                }
                CombinedRequirements.dedup();
                DecodedValues.sort();
                let BundleCandidateId = if CombinationCount == 1 {
                    Candidate.CandidateId.clone()
                } else {
                    format!("{}:bundle:{}", Candidate.CandidateId, CombinationIndex,)
                };
                DecodeMap.insert((Variable.clone(), BundleCandidateId.clone()), DecodedValues);
                BundledValues.push(AssignmentCandidate {
                    CandidateId: BundleCandidateId,
                    OwnerSignal: Candidate.OwnerSignal.clone(),
                    TemplateRequirements: Arc::new(CombinedRequirements),
                    ForbiddenCandidateIds: Arc::new(Vec::new()),
                    OrderedWire: Arc::new(Vec::new()),
                    PoweredAccessConstraint: None,
                    Claims: Arc::new(CombinedClaims),
                    MaterialCost,
                    FootprintGrowth,
                    Length,
                    BendCount,
                    ViaCount,
                });
            }
        }
        BundledGroups.insert(Variable.clone(), BundledValues);
    }
    for Variable in AccessVariables.difference(&ReferencedAccessVariables) {
        BundledGroups.insert(
            Variable.clone(),
            Groups
                .get(Variable)
                .expect("standalone access variable belongs to the source catalog")
                .clone(),
        );
    }
    Ok(Some((BundledGroups, DecodeMap)))
}

fn EnumerateExactLayeredGuideAccessTuples(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    GuideVariable: &str,
    GuideCandidateIndex: usize,
    Deadline: &RuntimeDeadline,
) -> Option<Vec<Vec<(String, String)>>> {
    let GuideCandidate = Groups.get(GuideVariable)?.get(GuideCandidateIndex)?;
    let Constraint = GuideCandidate.PoweredAccessConstraint.as_ref()?;
    let TerminalVariables = Constraint
        .TerminalVariables
        .iter()
        .cloned()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let Domains = TerminalVariables
        .iter()
        .map(|Variable| {
            Groups.get(Variable).map(|Candidates| {
                Candidates
                    .iter()
                    .enumerate()
                    .filter(|(_CandidateIndex, Candidate)| {
                        Candidate.TemplateRequirements.iter().all(
                            |(CandidateName, CandidateValue)| {
                                GuideCandidate.TemplateRequirements.iter().all(
                                    |(GuideName, GuideValue)| {
                                        CandidateName != GuideName
                                            || CandidateValue == GuideValue
                                    },
                                )
                            },
                        )
                    })
                    .map(|(CandidateIndex, _Candidate)| CandidateIndex)
                    .collect::<Vec<_>>()
            })
        })
        .collect::<Option<Vec<_>>>()?;
    if Domains.iter().any(Vec::is_empty) {
        return Some(Vec::new());
    }
    let SignalNames = Groups.keys().cloned().collect::<Vec<_>>();
    let SignalIndexByName = SignalNames
        .iter()
        .enumerate()
        .map(|(Index, Variable)| (Variable.as_str(), Index))
        .collect::<HashMap<_, _>>();
    let GuideSignalIndex = *SignalIndexByName.get(GuideVariable)?;
    let mut Selection = vec![None; SignalNames.len()];
    Selection[GuideSignalIndex] = Some(GuideCandidateIndex);
    let mut SelectedAccess = Vec::<(usize, usize)>::new();
    let mut Result = Vec::<Vec<(String, String)>>::new();
    fn Search(
        Offset: usize,
        Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
        GuideVariable: &str,
        TerminalVariables: &[String],
        Domains: &[Vec<usize>],
        SignalNames: &[String],
        SignalIndexByName: &HashMap<&str, usize>,
        Selection: &mut [Option<usize>],
        SelectedAccess: &mut Vec<(usize, usize)>,
        Result: &mut Vec<Vec<(String, String)>>,
        Deadline: &RuntimeDeadline,
    ) -> Option<()> {
        if Deadline.Check() {
            return None;
        }
        let Some(Variable) = TerminalVariables.get(Offset) else {
            let mut FailureNet = None;
            if SelectionHasPoweredAccessWitnessExact(
                Groups,
                SignalNames,
                Selection,
                Deadline,
                &mut FailureNet,
            )? {
                let mut Tuple = SelectedAccess
                    .iter()
                    .map(|(SignalIndex, CandidateIndex)| {
                        (
                            SignalNames[*SignalIndex].clone(),
                            Groups[&SignalNames[*SignalIndex]][*CandidateIndex]
                                .CandidateId
                                .clone(),
                        )
                    })
                    .collect::<Vec<_>>();
                Tuple.sort();
                Result.push(Tuple);
            }
            return Some(());
        };
        let SignalIndex = *SignalIndexByName.get(Variable.as_str())?;
        for CandidateIndex in &Domains[Offset] {
            let Candidate = &Groups[Variable][*CandidateIndex];
            let SelfLegal = !Groups[GuideVariable][Selection[
                *SignalIndexByName.get(GuideVariable)?
            ]?]
                .Claims
                .SameOwnerConflictsWithDeadline(&Candidate.Claims, Deadline)?
                && SelectedAccess.iter().all(|(PriorSignalIndex, PriorCandidateIndex)| {
                    !Candidate.Claims.SameOwnerConflicts(
                        &Groups[&SignalNames[*PriorSignalIndex]][*PriorCandidateIndex].Claims,
                    )
                });
            if !SelfLegal {
                continue;
            }
            Selection[SignalIndex] = Some(*CandidateIndex);
            SelectedAccess.push((SignalIndex, *CandidateIndex));
            Search(
                Offset + 1,
                Groups,
                GuideVariable,
                TerminalVariables,
                Domains,
                SignalNames,
                SignalIndexByName,
                Selection,
                SelectedAccess,
                Result,
                Deadline,
            )?;
            SelectedAccess.pop();
            Selection[SignalIndex] = None;
        }
        Some(())
    }
    Search(
        0,
        Groups,
        GuideVariable,
        &TerminalVariables,
        &Domains,
        &SignalNames,
        &SignalIndexByName,
        &mut Selection,
        &mut SelectedAccess,
        &mut Result,
        Deadline,
    )?;
    Result.sort();
    Result.dedup();
    Some(Result)
}

fn CloseLayeredCatalogWarmGuideTuples(
    Groups: &mut BTreeMap<String, Vec<AssignmentCandidate>>,
    WarmSelections: &[(String, String)],
    Deadline: &RuntimeDeadline,
) -> Option<BTreeSet<String>> {
    let WarmCandidateIdByVariable = WarmSelections
        .iter()
        .cloned()
        .collect::<HashMap<_, _>>();
    let SignalNames = Groups.keys().cloned().collect::<Vec<_>>();
    let SignalIndexByName = SignalNames
        .iter()
        .enumerate()
        .map(|(Index, Variable)| (Variable.as_str(), Index))
        .collect::<HashMap<_, _>>();
    let GuideVariables = SignalNames
        .iter()
        .filter(|Variable| Variable.starts_with("__route_guide__:"))
        .cloned()
        .collect::<Vec<_>>();
    let mut WarmTupleUpdates = Vec::<(String, usize, Vec<(String, String)>)>::new();
    let mut RepairGuideVariables = BTreeSet::<String>::new();

    for GuideVariable in &GuideVariables {
        if Deadline.Check() {
            return None;
        }
        let Some(GuideCandidateId) = WarmCandidateIdByVariable.get(GuideVariable) else {
            RepairGuideVariables.insert(GuideVariable.clone());
            continue;
        };
        let Some(GuideCandidateIndex) = Groups[GuideVariable]
            .iter()
            .position(|Candidate| Candidate.CandidateId == *GuideCandidateId)
        else {
            RepairGuideVariables.insert(GuideVariable.clone());
            continue;
        };
        let Some(Constraint) = Groups[GuideVariable][GuideCandidateIndex]
            .PoweredAccessConstraint
            .as_ref()
        else {
            RepairGuideVariables.insert(GuideVariable.clone());
            continue;
        };
        let mut WarmTuple = Vec::<(String, String)>::new();
        let mut Selection = vec![None; SignalNames.len()];
        Selection[*SignalIndexByName.get(GuideVariable.as_str())?] = Some(GuideCandidateIndex);
        let mut CompleteTuple = true;
        for TerminalVariable in Constraint.TerminalVariables.iter() {
            let Some(AccessCandidateId) = WarmCandidateIdByVariable.get(TerminalVariable) else {
                CompleteTuple = false;
                break;
            };
            let Some(AccessCandidateIndex) = Groups[TerminalVariable]
                .iter()
                .position(|Candidate| Candidate.CandidateId == *AccessCandidateId)
            else {
                CompleteTuple = false;
                break;
            };
            Selection[*SignalIndexByName.get(TerminalVariable.as_str())?] =
                Some(AccessCandidateIndex);
            WarmTuple.push((TerminalVariable.clone(), AccessCandidateId.clone()));
        }
        if !CompleteTuple {
            RepairGuideVariables.insert(GuideVariable.clone());
            continue;
        }
        WarmTuple.sort();
        WarmTuple.dedup();
        if Constraint
            .PreferredAccessCandidateTuples
            .iter()
            .any(|CandidateTuple| CandidateTuple == &WarmTuple)
        {
            continue;
        }
        let mut FailureNet = None;
        match SelectionHasPoweredAccessWitnessExact(
            Groups,
            &SignalNames,
            &Selection,
            Deadline,
            &mut FailureNet,
        ) {
            Some(true) => {
                WarmTupleUpdates.push((
                    GuideVariable.clone(),
                    GuideCandidateIndex,
                    WarmTuple,
                ));
            }
            Some(false) => {
                RepairGuideVariables.insert(GuideVariable.clone());
            }
            None => return None,
        }
    }

    for (GuideVariable, GuideCandidateIndex, WarmTuple) in WarmTupleUpdates {
        let Constraint = Arc::make_mut(
            Groups
                .get_mut(&GuideVariable)?
                .get_mut(GuideCandidateIndex)?
                .PoweredAccessConstraint
                .as_mut()?,
        );
        let Tuples = Arc::make_mut(&mut Constraint.PreferredAccessCandidateTuples);
        Tuples.push(WarmTuple);
        Tuples.sort();
        Tuples.dedup();
    }

    let mut ExactTupleUpdates = Vec::<(String, usize, Vec<Vec<(String, String)>>)>::new();
    for GuideVariable in &RepairGuideVariables {
        for GuideCandidateIndex in 0..Groups[GuideVariable].len() {
            let Tuples = EnumerateExactLayeredGuideAccessTuples(
                Groups,
                GuideVariable,
                GuideCandidateIndex,
                Deadline,
            )?;
            ExactTupleUpdates.push((
                GuideVariable.clone(),
                GuideCandidateIndex,
                Tuples,
            ));
        }
    }
    for (GuideVariable, GuideCandidateIndex, Tuples) in ExactTupleUpdates {
        let Constraint = Arc::make_mut(
            Groups
                .get_mut(&GuideVariable)?
                .get_mut(GuideCandidateIndex)?
                .PoweredAccessConstraint
                .as_mut()?,
        );
        Constraint.PreferredAccessCandidateTuples = Arc::new(Tuples);
    }
    Some(RepairGuideVariables)
}

fn SolveLayeredCatalogCandidateGroups(
    Groups: &mut BTreeMap<String, Vec<AssignmentCandidate>>,
    ResourceCount: usize,
    CrossAirByWire: Arc<Vec<Vec<(usize, usize)>>>,
    ExternalWarmSelections: Option<&[(String, String)]>,
    MaximumExpansionCount: usize,
    SharedExpansionCount: &std::sync::atomic::AtomicUsize,
    Deadline: &RuntimeDeadline,
) -> PyResult<RoutingAssignmentResult> {
    let UnsupportedVariables = Groups
        .keys()
        .filter(|Variable| {
            !Variable.starts_with("__access_terminal__:")
                && !Variable.starts_with("__route_guide__:")
                && !Variable.starts_with("__base_claim__:")
                && Variable.as_str() != "__fixed_base_claim_conflict__"
        })
        .cloned()
        .collect::<Vec<_>>();
    if !UnsupportedVariables.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!(
                "layered access catalog contains unsupported variable kinds: {:?}",
                UnsupportedVariables,
            ),
        ));
    }
    let GuidePortalChoicesByRequirement = Groups
        .iter()
        .filter(|(Variable, _Values)| Variable.starts_with("__route_guide__:"))
        .flat_map(|(_Variable, Values)| Values)
        .flat_map(|Candidate| Candidate.TemplateRequirements.iter())
        .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
        .fold(
            HashMap::<String, HashSet<String>>::new(),
            |mut Result, (Name, Value)| {
                Result
                    .entry(Name.clone())
                    .or_default()
                    .insert(Value.clone());
                Result
            },
        );
    for (Variable, Values) in Groups.iter_mut() {
        if !Variable.starts_with("__access_terminal__:") {
            continue;
        }
        Values.retain(|Candidate| {
            Candidate.TemplateRequirements.iter().all(|(Name, Value)| {
                !Name.starts_with("access-portal:")
                    || GuidePortalChoicesByRequirement
                        .get(Name)
                        .is_none_or(|Choices| Choices.contains(Value))
            })
        });
    }
    let AccessPortalChoices = Groups
        .iter()
        .filter(|(Variable, _Values)| Variable.starts_with("__access_terminal__:"))
        .flat_map(|(_Variable, Values)| Values)
        .flat_map(|Candidate| Candidate.TemplateRequirements.iter())
        .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
        .cloned()
        .collect::<HashSet<_>>();
    for (Variable, Values) in Groups.iter_mut() {
        if !Variable.starts_with("__route_guide__:") {
            continue;
        }
        Values.retain(|Candidate| {
            Candidate.TemplateRequirements.iter().all(|Requirement| {
                !Requirement.0.starts_with("access-portal:")
                    || AccessPortalChoices.contains(Requirement)
            })
        });
    }
    if let Some((Variable, _Values)) = Groups.iter().find(|(_Variable, Values)| Values.is_empty()) {
        let ExpansionCount = SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
        return Ok(RoutingAssignmentResult {
            Success: false,
            SelectedCandidateIds: Vec::new(),
            ExpansionCount,
            BudgetExhausted: false,
            DeadlineExceeded: Deadline.WasExceeded(),
            CompletedWork: ExpansionCount,
            FailureNet: Some(Variable.clone()),
            ConflictSignals: Vec::new(),
            ConflictResourceIndices: Vec::new(),
            PairwiseIncompatibleSignals: Vec::new(),
            PairwiseCompatibilityComplete: !Deadline.WasExceeded(),
        });
    }
    for Values in Groups.values_mut() {
        if !SortCandidatesWithDeadline(Values, Deadline) {
            let ExpansionCount = SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
            return Ok(RoutingAssignmentResult {
                Success: false,
                SelectedCandidateIds: Vec::new(),
                ExpansionCount,
                BudgetExhausted: false,
                DeadlineExceeded: true,
                CompletedWork: ExpansionCount,
                FailureNet: None,
                ConflictSignals: Vec::new(),
                ConflictResourceIndices: Vec::new(),
                PairwiseIncompatibleSignals: Vec::new(),
                PairwiseCompatibilityComplete: false,
            });
        }
    }
    if std::env::var_os("RCS_EXPERIMENTAL_SINGLE_WITNESS_BUNDLES").is_some() {
        let Some((mut BundledGroups, BundleDecodeMap)) =
            BuildUniqueLayeredCatalogBundleGroups(Groups, Deadline)?
        else {
            let ExpansionCount =
                SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
            return Ok(RoutingAssignmentResult {
                Success: false,
                SelectedCandidateIds: Vec::new(),
                ExpansionCount,
                BudgetExhausted: false,
                DeadlineExceeded: true,
                CompletedWork: ExpansionCount,
                FailureNet: None,
                ConflictSignals: Vec::new(),
                ConflictResourceIndices: Vec::new(),
                PairwiseIncompatibleSignals: Vec::new(),
                PairwiseCompatibilityComplete: false,
            });
        };
        let ExpansionCountBeforeBundledSolve =
            SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
        let mut BundledAssignment =
            PlanAuthoritativeCandidateGroupsWithInitialExpansionDeadlineAndCrossAir(
                &mut BundledGroups,
                ResourceCount,
                ExpansionCountBeforeBundledSolve,
                MaximumExpansionCount,
                Deadline.clone(),
                true,
                false,
                Some(SharedExpansionCount),
                Some(CrossAirByWire.as_slice()),
            )?;
        if BundledAssignment.Success {
            BundledAssignment.SelectedCandidateIds = BundledAssignment
                .SelectedCandidateIds
                .iter()
                .flat_map(|(Variable, CandidateId)| {
                    BundleDecodeMap
                        .get(&(Variable.clone(), CandidateId.clone()))
                        .cloned()
                        .unwrap_or_else(|| vec![(Variable.clone(), CandidateId.clone())])
                })
                .collect();
            BundledAssignment.SelectedCandidateIds.sort();
            BundledAssignment.SelectedCandidateIds.dedup();
        }
        return Ok(BundledAssignment);
    }
    let AccessVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__access_terminal__:"))
        .cloned()
        .collect::<Vec<_>>();
    let mut GuideVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__route_guide__:"))
        .cloned()
        .collect::<Vec<_>>();
    // A caller-provided complete witness may seed exact validation.  The
    // production batch otherwise enters the portal-aware bounded search
    // directly; constructing a member-local all-pairs compatibility matrix
    // duplicates the exact claim checks and dominates portfolio runtime.
    let mut WarmSelections = if let Some(ExternalWarmSelections) = ExternalWarmSelections {
        ExternalWarmSelections.to_vec()
    } else {
        Vec::new()
    };
    let mut WarmCandidateIdByVariable = WarmSelections.iter().cloned().collect::<HashMap<_, _>>();
    for (Variable, Values) in Groups.iter_mut() {
        let Some(PreferredCandidateId) = WarmCandidateIdByVariable.get(Variable) else {
            continue;
        };
        if let Some(PreferredIndex) = Values
            .iter()
            .position(|Candidate| &Candidate.CandidateId == PreferredCandidateId)
        {
            Values[..=PreferredIndex].rotate_right(1);
        }
    }
    let AccessChoicesByPortalRequirement = AccessVariables
        .iter()
        .flat_map(|Variable| {
            Groups[Variable]
                .iter()
                .enumerate()
                .flat_map(move |(CandidateIndex, Candidate)| {
                    Candidate
                        .TemplateRequirements
                        .iter()
                        .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
                        .map(move |(Name, Value)| {
                            (
                                (Name.clone(), Value.clone()),
                                (Variable.clone(), CandidateIndex),
                            )
                        })
                })
        })
        .fold(
            HashMap::<(String, String), Vec<(String, usize)>>::new(),
            |mut Result, (Requirement, Choice)| {
                Result.entry(Requirement).or_default().push(Choice);
                Result
            },
    );
    if ExternalWarmSelections.is_none() {
        let ExpansionCountBeforeIndexedSolve =
            SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
        // This first solve supplies a deterministic seed by selecting each
        // guide together with one exact referenced access tuple.  The retained
        // support basis is not an infeasibility proof; the exact powered
        // closure and portal-aware repair below remain authoritative.
        let IndexedAssignment =
            PlanAuthoritativeCandidateGroupsWithInitialExpansionDeadlineAndCrossAir(
            Groups,
            ResourceCount,
            ExpansionCountBeforeIndexedSolve,
            MaximumExpansionCount,
            Deadline.clone(),
            true,
            false,
            Some(SharedExpansionCount),
            Some(CrossAirByWire.as_slice()),
        )?;
        if !IndexedAssignment.Success {
            return Ok(IndexedAssignment);
        }
        return Ok(IndexedAssignment);
    }
    let Some(PrecomputedRepairGuideVariables) = CloseLayeredCatalogWarmGuideTuples(
        Groups,
        &WarmSelections,
        Deadline,
    ) else {
        let ExpansionCount =
            SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
        return Ok(RoutingAssignmentResult {
            Success: false,
            SelectedCandidateIds: Vec::new(),
            ExpansionCount,
            BudgetExhausted: false,
            DeadlineExceeded: true,
            CompletedWork: ExpansionCount,
            FailureNet: None,
            ConflictSignals: Vec::new(),
            ConflictResourceIndices: Vec::new(),
            PairwiseIncompatibleSignals: Vec::new(),
            PairwiseCompatibilityComplete: false,
        });
    };
    let AccessChoicesByStubRequirement = AccessVariables
        .iter()
        .flat_map(|Variable| {
            Groups[Variable]
                .iter()
                .enumerate()
                .flat_map(move |(CandidateIndex, Candidate)| {
                    Candidate
                        .TemplateRequirements
                        .iter()
                        .filter(|(Name, _Value)| Name.starts_with("access-stub:"))
                        .map(move |(Name, Value)| {
                            (
                                (Name.clone(), Value.clone()),
                                (Variable.clone(), CandidateIndex),
                            )
                        })
                })
        })
        .fold(
            HashMap::<(String, String), Vec<(String, usize)>>::new(),
            |mut Result, (Requirement, Choice)| {
                Result.entry(Requirement).or_default().push(Choice);
                Result
            },
        );
    // Compact guide values bind exact portals, while access candidates remain
    // independent variables.  The portal-aware search below composes those
    // values lazily with owner-aware claim checks, avoiding an expanded
    // guide-by-stub bundle domain.
    let BundleDecodeMap = LayeredCatalogBundleDecodeMap::new();
    let AccessVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__access_terminal__:"))
        .cloned()
        .collect::<Vec<_>>();
    GuideVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__route_guide__:"))
        .cloned()
        .collect::<Vec<_>>();
    GuideVariables.sort_by_key(|Variable| {
        let CertifiedTupleCount = Groups[Variable]
            .iter()
            .filter_map(|Candidate| Candidate.PoweredAccessConstraint.as_ref())
            .map(|Constraint| Constraint.PreferredAccessCandidateTuples.len())
            .fold(0usize, usize::saturating_add);
        (
            CertifiedTupleCount,
            Groups[Variable].len(),
            Variable.clone(),
        )
    });
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered guide variable order {:?}",
            GuideVariables
                .iter()
                .map(|Variable| (Variable, Groups[Variable].len()))
                .collect::<Vec<_>>(),
        );
    }
    let OwnerIndexByName = Groups
        .values()
        .flatten()
        .map(|Candidate| Candidate.OwnerSignal.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .enumerate()
        .map(|(Index, Owner)| (Owner, Index))
        .collect::<HashMap<_, _>>();
    let CandidateIndexByIdByVariable = Groups
        .iter()
        .map(|(Variable, Candidates)| {
            (
                Variable.clone(),
                Candidates
                    .iter()
                    .enumerate()
                    .map(|(CandidateIndex, Candidate)| {
                        (Candidate.CandidateId.clone(), CandidateIndex)
                    })
                    .collect::<HashMap<_, _>>(),
            )
        })
        .collect::<HashMap<_, _>>();
    let mut Context = LayeredCatalogSearchContext {
        Groups,
        CandidateIndexByIdByVariable: &CandidateIndexByIdByVariable,
        PreferredCandidateIdByVariable: &WarmCandidateIdByVariable,
        AccessChoicesByPortalRequirement: &AccessChoicesByPortalRequirement,
        AccessChoicesByStubRequirement: &AccessChoicesByStubRequirement,
        OwnerIndexByName: &OwnerIndexByName,
        SharedExpansionCount,
        MaximumExpansionCount,
        Deadline,
        ExpansionCount: 0,
        MaximumSelectedCount: 0,
        DeepestFailureDepth: 0,
        DeepestFailureNet: None,
        SearchVariant: 0,
        LocalMaximumExpansionCount: None,
        LocalBudgetExhausted: false,
        BudgetExhausted: false,
        DeadlineExceeded: false,
        FailureNet: None,
    };

    let mut State = LayeredCatalogSelectionState::New(ResourceCount, CrossAirByWire.clone());
    let mut WarmRequirementNames = Vec::new();
    let mut WarmWitnessIsExact = WarmSelections.len() == Context.Groups.len();
    let WarmBaseVariables = Context
        .Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__base_claim__:"))
        .cloned()
        .collect::<Vec<_>>();
    let WarmGuideVariables = Context
        .Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__route_guide__:"))
        .cloned()
        .collect::<Vec<_>>();
    let mut RepairGuideVariables = PrecomputedRepairGuideVariables;
    let mut PriorityRepairGuideVariables = RepairGuideVariables.clone();
    for Variable in &WarmBaseVariables {
        if !WarmWitnessIsExact {
            break;
        }
        let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
            WarmWitnessIsExact = false;
            break;
        };
        let Some(CandidateIndex) = Context.Groups.get(Variable).and_then(|Values| {
            Values
                .iter()
                .position(|Candidate| Candidate.CandidateId == *CandidateId)
        }) else {
            WarmWitnessIsExact = false;
            break;
        };
        if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
            || !ApplyLayeredCatalogCandidate(
                &mut Context,
                &mut State,
                Variable,
                CandidateIndex,
                &mut WarmRequirementNames,
            )
        {
            WarmWitnessIsExact = false;
            break;
        }
    }
    if WarmWitnessIsExact {
        for Variable in &WarmGuideVariables {
            let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
                RepairGuideVariables.insert(Variable.clone());
                continue;
            };
            let Some(CandidateIndex) = Context.Groups.get(Variable).and_then(|Values| {
                Values
                    .iter()
                    .position(|Candidate| Candidate.CandidateId == *CandidateId)
            }) else {
                RepairGuideVariables.insert(Variable.clone());
                continue;
            };
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
                || !ApplyLayeredCatalogCandidate(
                    &mut Context,
                    &mut State,
                    Variable,
                    CandidateIndex,
                    &mut NewRequirementNames,
                )
            {
                RollbackLayeredCatalogSelection(
                    &Context,
                    &mut State,
                    SelectedCheckpoint,
                    &mut NewRequirementNames,
                );
                RepairGuideVariables.insert(Variable.clone());
            } else {
                WarmRequirementNames.extend(NewRequirementNames);
            }
        }
    }
    if WarmWitnessIsExact {
        WarmWitnessIsExact = ApplyCompatibleWarmLayeredCatalogAccess(
            &mut Context,
            &mut State,
            &AccessVariables,
            &WarmCandidateIdByVariable,
            &mut WarmRequirementNames,
        );
    }
    if WarmWitnessIsExact {
        if RepairGuideVariables.is_empty() {
            WarmWitnessIsExact =
                SearchLayeredCatalogAccessByPortal(&mut Context, &mut State, &AccessVariables);
        } else {
            WarmWitnessIsExact = false;
        }
    }
    if WarmWitnessIsExact {
        for Variable in &WarmGuideVariables {
            if LayeredCatalogSelectedGuideHasPoweredWitness(&Context, &State, Variable)
                != Some(true)
            {
                RepairGuideVariables.insert(Variable.clone());
            }
        }
        if !RepairGuideVariables.is_empty() {
            PriorityRepairGuideVariables = RepairGuideVariables.clone();
            let mut FixedGuideState =
                LayeredCatalogSelectionState::New(ResourceCount, CrossAirByWire.clone());
            let mut FixedGuideRequirementNames = Vec::new();
            let mut FixedGuideReplayComplete = true;
            for Variable in WarmBaseVariables.iter().chain(WarmGuideVariables.iter()) {
                let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
                    FixedGuideReplayComplete = false;
                    break;
                };
                let Some(CandidateIndex) = Context.Groups[Variable]
                    .iter()
                    .position(|Candidate| Candidate.CandidateId == *CandidateId)
                else {
                    FixedGuideReplayComplete = false;
                    break;
                };
                if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
                    || !ApplyLayeredCatalogCandidate(
                        &mut Context,
                        &mut FixedGuideState,
                        Variable,
                        CandidateIndex,
                        &mut FixedGuideRequirementNames,
                    )
                {
                    FixedGuideReplayComplete = false;
                    break;
                }
            }
            if FixedGuideReplayComplete {
                let mut FixedGuideOrder = WarmGuideVariables.clone();
                FixedGuideOrder.sort_by_key(|Variable| {
                    let CandidateIndex = FixedGuideState.SelectedByVariable[Variable];
                    let TupleCount = Context.Groups[Variable][CandidateIndex]
                        .PoweredAccessConstraint
                        .as_ref()
                        .map(|Constraint| Constraint.PreferredAccessCandidateTuples.len())
                        .unwrap_or(usize::MAX);
                    (TupleCount, Variable.clone())
                });
                let RemainingExpansionCount = Context.MaximumExpansionCount.saturating_sub(
                    Context
                        .SharedExpansionCount
                        .load(std::sync::atomic::Ordering::SeqCst),
                );
                Context.LocalMaximumExpansionCount = Some(
                    Context
                        .ExpansionCount
                        .saturating_add(RemainingExpansionCount / 2),
                );
                Context.LocalBudgetExhausted = false;
                WarmWitnessIsExact = SearchLayeredCatalogSelectedGuideTuples(
                    &mut Context,
                    &mut FixedGuideState,
                    &FixedGuideOrder,
                    &AccessVariables,
                );
                Context.LocalMaximumExpansionCount = None;
                Context.LocalBudgetExhausted = false;
                if WarmWitnessIsExact {
                    State = FixedGuideState;
                    RepairGuideVariables.clear();
                }
            }
            if !WarmWitnessIsExact {
                // Preserve the exact repair frontier.  Widening every guide
                // here destroys the useful pairwise witness and recreates the
                // full Cartesian search.  The repair loop below grows this set
                // from concrete powered or capacity failures when necessary.
                if let Some(FailureGuideVariable) = [
                    Context.DeepestFailureNet.as_deref(),
                    Context.FailureNet.as_deref(),
                ]
                .into_iter()
                .filter_map(|FailureVariable| {
                    LayeredCatalogGuideVariableForFailure(
                        Context.Groups,
                        FailureVariable,
                    )
                })
                .find(|Variable| !RepairGuideVariables.contains(Variable))
                {
                    RepairGuideVariables.insert(FailureGuideVariable);
                }
                Context.FailureNet = None;
            }
        }
    }
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered warm tuple repair {:?}",
            RepairGuideVariables
                .iter()
                .map(|Variable| {
                    let CandidateId = WarmCandidateIdByVariable.get(Variable);
                    let TupleCount = CandidateId
                        .and_then(|CandidateId| {
                            Context.Groups[Variable]
                                .iter()
                                .find(|Candidate| Candidate.CandidateId == *CandidateId)
                        })
                        .and_then(|Candidate| Candidate.PoweredAccessConstraint.as_ref())
                        .map(|Constraint| Constraint.PreferredAccessCandidateTuples.len())
                        .unwrap_or(0);
                    (Variable, TupleCount)
                })
                .collect::<Vec<_>>(),
        );
    }
    if !WarmWitnessIsExact && !Context.BudgetExhausted && !Context.DeadlineExceeded {
        if let Some(FailureGuideVariable) =
            LayeredCatalogGuideVariableForFailure(Context.Groups, Context.FailureNet.as_deref())
        {
            RepairGuideVariables.insert(FailureGuideVariable);
        }
    }
    let RunGlobalParallelRepair = !WarmWitnessIsExact
        && RepairGuideVariables.len() == WarmGuideVariables.len();
    while !WarmWitnessIsExact
        && !RepairGuideVariables.is_empty()
        && !RunGlobalParallelRepair
        && !Context.BudgetExhausted
        && !Context.DeadlineExceeded
    {
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            eprintln!(
                "native layered repair iteration variables={:?} shared_expansions={}",
                RepairGuideVariables,
                Context
                    .SharedExpansionCount
                    .load(std::sync::atomic::Ordering::SeqCst),
            );
        }
        State = LayeredCatalogSelectionState::New(ResourceCount, CrossAirByWire.clone());
        WarmRequirementNames.clear();
        Context.FailureNet = None;
        let mut FixedWitnessComplete = true;
        for Variable in &WarmBaseVariables {
            let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
                FixedWitnessComplete = false;
                Context.FailureNet = Some(Variable.clone());
                break;
            };
            let Some(CandidateIndex) = Context.Groups[Variable]
                .iter()
                .position(|Candidate| Candidate.CandidateId == *CandidateId)
            else {
                FixedWitnessComplete = false;
                Context.FailureNet = Some(Variable.clone());
                break;
            };
            if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
                || !ApplyLayeredCatalogCandidate(
                    &mut Context,
                    &mut State,
                    Variable,
                    CandidateIndex,
                    &mut WarmRequirementNames,
                )
            {
                FixedWitnessComplete = false;
                Context.FailureNet = Some(Variable.clone());
                break;
            }
        }
        let RepairCountBeforeFixedGuides = RepairGuideVariables.len();
        if FixedWitnessComplete {
            for Variable in &WarmGuideVariables {
                if RepairGuideVariables.contains(Variable) {
                    continue;
                }
                let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
                    RepairGuideVariables.insert(Variable.clone());
                    continue;
                };
                let Some(CandidateIndex) = Context.Groups[Variable]
                    .iter()
                    .position(|Candidate| Candidate.CandidateId == *CandidateId)
                else {
                    RepairGuideVariables.insert(Variable.clone());
                    continue;
                };
                let SelectedCheckpoint = State.SelectedOrder.len();
                let mut NewRequirementNames = Vec::new();
                if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
                    || !ApplyLayeredCatalogCandidate(
                        &mut Context,
                        &mut State,
                        Variable,
                        CandidateIndex,
                        &mut NewRequirementNames,
                    )
                {
                    RollbackLayeredCatalogSelection(
                        &Context,
                        &mut State,
                        SelectedCheckpoint,
                        &mut NewRequirementNames,
                    );
                    RepairGuideVariables.insert(Variable.clone());
                } else {
                    WarmRequirementNames.extend(NewRequirementNames);
                }
            }
        }
        if !FixedWitnessComplete {
            break;
        }
        let RepairAccessVariables =
            LayeredCatalogAccessVariablesForGuides(&Context, &RepairGuideVariables);
        let FixedAccessVariables = AccessVariables
            .iter()
            .filter(|Variable| !RepairAccessVariables.contains(*Variable))
            .cloned()
            .collect::<Vec<_>>();
        FixedWitnessComplete = ApplyCompatibleWarmLayeredCatalogAccess(
            &mut Context,
            &mut State,
            &FixedAccessVariables,
            &WarmCandidateIdByVariable,
            &mut WarmRequirementNames,
        );
        if !FixedWitnessComplete {
            break;
        }
        if RepairGuideVariables.len() != RepairCountBeforeFixedGuides {
            continue;
        }
        let mut RepairGuideOrder = GuideVariables
            .iter()
            .filter(|Variable| RepairGuideVariables.contains(*Variable))
            .cloned()
            .collect::<Vec<_>>();
        RepairGuideOrder.sort_by_key(|Variable| {
            (
                usize::from(!PriorityRepairGuideVariables.contains(Variable)),
                GuideVariables
                    .iter()
                    .position(|CandidateVariable| CandidateVariable == Variable)
                    .unwrap_or(usize::MAX),
            )
        });
        Context.DeepestFailureDepth = 0;
        Context.DeepestFailureNet = None;
        Context.LocalBudgetExhausted = false;
        let SharedExpansionCountBeforeRepair = Context
            .SharedExpansionCount
            .load(std::sync::atomic::Ordering::SeqCst);
        let RemainingExpansionCount = Context
            .MaximumExpansionCount
            .saturating_sub(SharedExpansionCountBeforeRepair);
        let FixedGuideCount = WarmGuideVariables
            .len()
            .saturating_sub(RepairGuideVariables.len());
        let RepairExpansionAllowance = RemainingExpansionCount
            .checked_div(FixedGuideCount.saturating_add(1))
            .unwrap_or(0)
            .max(128);
        Context.LocalMaximumExpansionCount = Some(
            Context
                .ExpansionCount
                .saturating_add(RepairExpansionAllowance),
        );
        WarmWitnessIsExact = SearchLayeredCatalogGuidesByPortal(
            &mut Context,
            &mut State,
            &RepairGuideOrder,
            &AccessVariables,
        );
        Context.LocalMaximumExpansionCount = None;
        if WarmWitnessIsExact {
            for Variable in &WarmGuideVariables {
                if LayeredCatalogSelectedGuideHasPoweredWitness(&Context, &State, Variable)
                    != Some(true)
                {
                    RepairGuideVariables.insert(Variable.clone());
                }
            }
            if RepairGuideVariables
                .iter()
                .any(|Variable| !RepairGuideOrder.contains(Variable))
            {
                WarmWitnessIsExact = false;
            }
        }
        if WarmWitnessIsExact || Context.BudgetExhausted || Context.DeadlineExceeded {
            break;
        }
        let FailureGuideVariable = [
            Context.DeepestFailureNet.as_deref(),
            Context.FailureNet.as_deref(),
        ]
        .into_iter()
        .filter_map(|FailureVariable| {
            LayeredCatalogGuideVariableForFailure(Context.Groups, FailureVariable)
        })
        .find(|Variable| !RepairGuideVariables.contains(Variable))
        .or_else(|| LayeredCatalogBlockingSelectedGuide(&Context, &State, &RepairGuideVariables));
        let Some(FailureGuideVariable) = FailureGuideVariable else {
            break;
        };
        if !RepairGuideVariables.insert(FailureGuideVariable) {
            break;
        }
    }
    if WarmWitnessIsExact {
        let ExpansionCount = SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
        let mut SelectedCandidateIds = State
            .SelectedByVariable
            .iter()
            .flat_map(|(Variable, CandidateIndex)| {
                let CandidateId = &Context.Groups[Variable][*CandidateIndex].CandidateId;
                BundleDecodeMap
                    .get(&(Variable.clone(), CandidateId.clone()))
                    .cloned()
                    .unwrap_or_else(|| vec![(Variable.clone(), CandidateId.clone())])
            })
            .collect::<Vec<_>>();
        SelectedCandidateIds.sort();
        SelectedCandidateIds.dedup();
        return Ok(RoutingAssignmentResult {
            Success: true,
            SelectedCandidateIds,
            ExpansionCount,
            BudgetExhausted: false,
            DeadlineExceeded: false,
            CompletedWork: ExpansionCount,
            FailureNet: None,
            ConflictSignals: Vec::new(),
            ConflictResourceIndices: Vec::new(),
            PairwiseIncompatibleSignals: Vec::new(),
            PairwiseCompatibilityComplete: true,
        });
    }
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        let ExpansionCount = SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
        return Ok(RoutingAssignmentResult {
            Success: false,
            SelectedCandidateIds: Vec::new(),
            ExpansionCount,
            BudgetExhausted: Context.BudgetExhausted,
            DeadlineExceeded: Context.DeadlineExceeded,
            CompletedWork: ExpansionCount,
            FailureNet: Context.FailureNet.clone(),
            ConflictSignals: Vec::new(),
            ConflictResourceIndices: Vec::new(),
            PairwiseIncompatibleSignals: Vec::new(),
            PairwiseCompatibilityComplete: true,
        });
    }
    RollbackLayeredCatalogSelection(&Context, &mut State, 0, &mut WarmRequirementNames);
    Context.FailureNet = None;
    let BaseVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__base_claim__:"))
        .cloned()
        .collect::<Vec<_>>();
    let mut BaseComplete = true;
    for Variable in &BaseVariables {
        let mut NewRequirementNames = Vec::new();
        if Groups[Variable].len() != 1
            || !ApplyLayeredCatalogCandidate(
                &mut Context,
                &mut State,
                Variable,
                0,
                &mut NewRequirementNames,
            )
        {
            Context.FailureNet = Some(Variable.clone());
            BaseComplete = false;
            break;
        }
    }
    let mut Success = false;
    if BaseComplete && !RunGlobalParallelRepair {
        Success = SearchLayeredCatalogGuidesByPortal(
            &mut Context,
            &mut State,
            &GuideVariables,
            &AccessVariables,
        );
    }
    if BaseComplete && RunGlobalParallelRepair {
        GuideVariables.sort_by_key(|Variable| {
            (
                usize::from(!PriorityRepairGuideVariables.contains(Variable)),
                Groups[Variable].len(),
                Variable.clone(),
            )
        });
        if let Some(RootVariable) = GuideVariables.first().cloned() {
            let RootVariableIndex = GuideVariables
                .iter()
                .position(|Variable| Variable == &RootVariable)
                .expect("selected root guide belongs to guide order");
            let mut RemainingGuideVariables = GuideVariables.clone();
            RemainingGuideVariables.remove(RootVariableIndex);
            let RemainingMemberBudget = MaximumExpansionCount
                .saturating_sub(SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst));
            let WaveSize = RoutingThreadPool()
                .current_num_threads()
                .max(1)
                .min((RemainingMemberBudget / 1_500).max(1));
            let RootCandidateCount = Groups[&RootVariable].len();
            let mut FirstWaveIndices = (0..WaveSize.min(RootCandidateCount))
                .map(|SampleIndex| {
                    SampleIndex.saturating_mul(RootCandidateCount)
                        / WaveSize.min(RootCandidateCount).max(1)
                })
                .collect::<Vec<_>>();
            FirstWaveIndices.sort_unstable();
            FirstWaveIndices.dedup();
            let FirstWaveIndexSet = FirstWaveIndices.iter().copied().collect::<HashSet<_>>();
            let mut RootCandidateIndices = FirstWaveIndices
                .into_iter()
                .chain(
                    (0..RootCandidateCount)
                        .filter(|CandidateIndex| !FirstWaveIndexSet.contains(CandidateIndex)),
                )
                .collect::<Vec<_>>();
            if let Some(PreferredRootCandidateIndex) = WarmCandidateIdByVariable
                .get(&RootVariable)
                .and_then(|CandidateId| {
                    CandidateIndexByIdByVariable[&RootVariable].get(CandidateId)
                })
                .copied()
            {
                if let Some(PreferredPosition) = RootCandidateIndices
                    .iter()
                    .position(|CandidateIndex| *CandidateIndex == PreferredRootCandidateIndex)
                {
                    RootCandidateIndices[..=PreferredPosition].rotate_right(1);
                }
            }
            let EffectiveWaveSize = WaveSize.min(RootCandidateIndices.len()).max(1);
            let ShallowRootBranchExpansionAllowance = 128usize.min(RemainingMemberBudget.max(1));
            let mut PendingRootCandidates = RootCandidateIndices
                .into_iter()
                .map(|CandidateIndex| {
                    (
                        CandidateIndex,
                        ShallowRootBranchExpansionAllowance,
                        false,
                        Vec::<String>::new(),
                    )
                })
                .collect::<std::collections::VecDeque<_>>();
            let mut DeferredDeepCandidates = Vec::<(usize, usize, Option<String>)>::new();
            let mut AnyLocalBudgetExhausted = false;
            'RootWaves: while !PendingRootCandidates.is_empty() {
                let CurrentWaveSize = EffectiveWaveSize;
                let Wave = (0..CurrentWaveSize)
                    .filter_map(|_Index| PendingRootCandidates.pop_front())
                    .collect::<Vec<_>>();
                let Outcomes = RoutingThreadPool().install(|| {
                    Wave.par_iter()
                        .map(
                            |(CandidateIndex, BranchExpansionAllowance, IsDeep, PriorityGuides)| {
                                let mut BranchRemainingGuideVariables =
                                    RemainingGuideVariables.clone();
                                for PriorityVariable in PriorityGuides.iter().rev() {
                                    if let Some(PriorityVariableIndex) =
                                        BranchRemainingGuideVariables
                                            .iter()
                                            .position(|Variable| Variable == PriorityVariable)
                                    {
                                        let PriorityVariable = BranchRemainingGuideVariables
                                            .remove(PriorityVariableIndex);
                                        BranchRemainingGuideVariables.insert(0, PriorityVariable);
                                    }
                                }
                                let mut BranchContext = LayeredCatalogSearchContext {
                                    Groups,
                                    CandidateIndexByIdByVariable:
                                        &CandidateIndexByIdByVariable,
                                    PreferredCandidateIdByVariable:
                                        &WarmCandidateIdByVariable,
                                    AccessChoicesByPortalRequirement:
                                        &AccessChoicesByPortalRequirement,
                                    AccessChoicesByStubRequirement: &AccessChoicesByStubRequirement,
                                    OwnerIndexByName: &OwnerIndexByName,
                                    SharedExpansionCount,
                                    MaximumExpansionCount,
                                    Deadline,
                                    ExpansionCount: 0,
                                    MaximumSelectedCount: State.SelectedByVariable.len(),
                                    DeepestFailureDepth: 0,
                                    DeepestFailureNet: None,
                                    SearchVariant: CandidateIndex.wrapping_mul(17),
                                    LocalMaximumExpansionCount: Some(*BranchExpansionAllowance),
                                    LocalBudgetExhausted: false,
                                    BudgetExhausted: false,
                                    DeadlineExceeded: false,
                                    FailureNet: None,
                                };
                                let mut BranchState = State.clone();
                                let mut NewRequirementNames = Vec::new();
                                let BranchSuccess = ConsumeLayeredCatalogExpansion(
                                    &mut BranchContext,
                                    &RootVariable,
                                ) && ApplyLayeredCatalogCandidate(
                                    &mut BranchContext,
                                    &mut BranchState,
                                    &RootVariable,
                                    *CandidateIndex,
                                    &mut NewRequirementNames,
                                ) && {
                                    let mut PortalRequirements = Groups[&RootVariable]
                                        [*CandidateIndex]
                                        .TemplateRequirements
                                        .iter()
                                        .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
                                        .cloned()
                                        .collect::<Vec<_>>();
                                    PortalRequirements.sort_by_key(|Requirement| {
                                        (
                                            AccessChoicesByPortalRequirement
                                                .get(Requirement)
                                                .map(Vec::len)
                                                .unwrap_or(0),
                                            Requirement.clone(),
                                        )
                                    });
                                    SearchLayeredCatalogPortalChoices(
                                        &mut BranchContext,
                                        &mut BranchState,
                                        &RootVariable,
                                        *CandidateIndex,
                                        &PortalRequirements,
                                        0,
                                        &BranchRemainingGuideVariables,
                                        &AccessVariables,
                                    )
                                };
                                (
                                    *CandidateIndex,
                                    *IsDeep,
                                    PriorityGuides.clone(),
                                    BranchSuccess,
                                    BranchState,
                                    BranchContext.BudgetExhausted,
                                    BranchContext.DeadlineExceeded,
                                    BranchContext.FailureNet,
                                    BranchContext.ExpansionCount,
                                    BranchContext.MaximumSelectedCount,
                                    BranchContext.DeepestFailureDepth,
                                    BranchContext.DeepestFailureNet,
                                    BranchContext.LocalBudgetExhausted,
                                )
                            },
                        )
                        .collect::<Vec<_>>()
                });
                for (
                    BranchCandidateIndex,
                    BranchWasDeep,
                    _BranchPriorityGuides,
                    BranchSuccess,
                    BranchState,
                    BranchBudgetExhausted,
                    BranchDeadlineExceeded,
                    BranchFailureNet,
                    _BranchExpansionCount,
                    BranchMaximumSelectedCount,
                    _BranchDeepestFailureDepth,
                    BranchDeepestFailureNet,
                    BranchLocalBudgetExhausted,
                ) in Outcomes
                {
                    AnyLocalBudgetExhausted |= BranchLocalBudgetExhausted;
                    if BranchSuccess {
                        State = BranchState;
                        Success = true;
                        break 'RootWaves;
                    }
                    if !BranchWasDeep && BranchLocalBudgetExhausted {
                        DeferredDeepCandidates.push((
                            BranchMaximumSelectedCount,
                            BranchCandidateIndex,
                            BranchDeepestFailureNet
                                .clone()
                                .filter(|Variable| Variable.starts_with("__route_guide__:")),
                        ));
                    }
                    if Context.FailureNet.is_none() {
                        Context.FailureNet = BranchFailureNet;
                    }
                    Context.BudgetExhausted |= BranchBudgetExhausted;
                    Context.DeadlineExceeded |= BranchDeadlineExceeded;
                }
                if SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst)
                    >= MaximumExpansionCount
                {
                    Context.BudgetExhausted = true;
                }
                if Context.BudgetExhausted || Context.DeadlineExceeded {
                    break;
                }
                if PendingRootCandidates.is_empty() {
                    if !DeferredDeepCandidates.is_empty() {
                        DeferredDeepCandidates.sort_by_key(
                            |(Depth, CandidateIndex, _PriorityGuide)| {
                                (std::cmp::Reverse(*Depth), *CandidateIndex)
                            },
                        );
                        DeferredDeepCandidates.dedup_by_key(
                            |(_Depth, CandidateIndex, _PriorityGuide)| *CandidateIndex,
                        );
                        let RemainingGlobalExpansions = MaximumExpansionCount.saturating_sub(
                            SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst),
                        );
                        let DeepCandidateCount = DeferredDeepCandidates.len().min(4);
                        let DeepBranchExpansionAllowance = RemainingGlobalExpansions
                            .checked_div(DeepCandidateCount.max(1))
                            .unwrap_or(0);
                        if DeepBranchExpansionAllowance > ShallowRootBranchExpansionAllowance {
                            for (_Depth, CandidateIndex, PriorityGuide) in
                                DeferredDeepCandidates.drain(..DeepCandidateCount)
                            {
                                PendingRootCandidates.push_back((
                                    CandidateIndex,
                                    DeepBranchExpansionAllowance,
                                    true,
                                    PriorityGuide.into_iter().collect(),
                                ));
                            }
                        }
                        DeferredDeepCandidates.clear();
                    }
                }
            }
            if !Success && AnyLocalBudgetExhausted {
                Context.BudgetExhausted = true;
            }
        } else {
            Success =
                SearchLayeredCatalogAccessByPortal(&mut Context, &mut State, &AccessVariables);
        }
    }
    let ExpansionCount = SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
    let mut SelectedCandidateIds = State
        .SelectedByVariable
        .iter()
        .flat_map(|(Variable, CandidateIndex)| {
            let CandidateId = &Groups[Variable][*CandidateIndex].CandidateId;
            BundleDecodeMap
                .get(&(Variable.clone(), CandidateId.clone()))
                .cloned()
                .unwrap_or_else(|| vec![(Variable.clone(), CandidateId.clone())])
        })
        .collect::<Vec<_>>();
    SelectedCandidateIds.sort();
    SelectedCandidateIds.dedup();
    let Assignment = RoutingAssignmentResult {
        Success,
        SelectedCandidateIds,
        ExpansionCount,
        BudgetExhausted: Context.BudgetExhausted,
        DeadlineExceeded: Context.DeadlineExceeded || Deadline.WasExceeded(),
        CompletedWork: ExpansionCount,
        FailureNet: Context.FailureNet,
        ConflictSignals: Vec::new(),
        ConflictResourceIndices: Vec::new(),
        PairwiseIncompatibleSignals: Vec::new(),
        PairwiseCompatibilityComplete: false,
    };
    Ok(Assignment)
}

fn LayeredAccessTemplateResult(
    Status: &str,
    Success: bool,
    Complete: bool,
    IncompleteReason: &str,
    SelectedTemplateId: Option<String>,
    SelectedTemplateObjective: Vec<i64>,
    SelectedCandidateIds: Vec<(String, String)>,
    ExpansionCount: usize,
    BudgetExhausted: bool,
    DeadlineExceeded: bool,
    FailureNet: Option<String>,
    ConflictSignals: Vec<String>,
    ConflictResourceIndices: Vec<usize>,
    PairwiseIncompatibleSignals: Vec<(String, String)>,
    PairwiseCompatibilityComplete: bool,
    AttemptedTemplateIds: Vec<String>,
    AttemptPairwiseIncompatibleSignals: Vec<(String, Vec<(String, String)>)>,
    AttemptFailureNets: Vec<(String, Option<String>)>,
    AttemptExpansionCounts: Vec<(String, usize)>,
    AttemptPartialCandidateIds: Vec<(String, Vec<(String, String)>)>,
    EscapeExpansionCount: usize,
    StartedAt: Instant,
) -> TemplateRoutingAssignmentResult {
    TemplateRoutingAssignmentResult {
        Status: Status.to_string(),
        Success,
        Complete,
        Unsatisfiable: false,
        IncompleteReason: IncompleteReason.to_string(),
        SelectedTemplateId,
        SelectedTemplateObjective,
        SelectedCandidateIds,
        ExpansionCount,
        BudgetExhausted,
        DeadlineExceeded,
        CompletedWork: ExpansionCount,
        FailureNet,
        ConflictSignals,
        ConflictResourceIndices,
        PairwiseIncompatibleSignals,
        PairwiseCompatibilityComplete,
        AttemptedTemplateIds,
        AttemptPairwiseIncompatibleSignals,
        AttemptFailureNets,
        AttemptExpansionCounts,
        AttemptPartialCandidateIds,
        NonExhaustiveTemplateDomain: true,
        CompactMaskTelemetry: vec![
            ("NativeBatchCallCount".to_string(), 1),
            ("EscapeExpansionCount".to_string(), EscapeExpansionCount),
            (
                "ElapsedMilliseconds".to_string(),
                StartedAt.elapsed().as_millis() as usize,
            ),
        ],
    }
}

/// Traverse each exact layer world lazily in deterministic member order,
/// compose exact access claims in Rust, and return the first complete
/// capacity witness.  One deadline and one assignment expansion counter
/// cover the operation; an incomplete earlier member can never be skipped.
pub(crate) fn SolveLayeredAccessEscapeFactorCatalogWithDeadline(
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    mut Members: Vec<LayeredAccessEscapeMemberValue>,
    BendPenalty: usize,
    MaximumAssignmentExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<LayeredAccessEscapeSelectionResult> {
    let StartedAt = Instant::now();
    if Graphs.is_empty() || Members.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access selection requires graphs and members",
        ));
    }
    if Graphs
        .iter()
        .any(|(GraphId, _Adjacency)| GraphId.is_empty())
        || Graphs
            .iter()
            .map(|(GraphId, _Adjacency)| GraphId)
            .collect::<BTreeSet<_>>()
            .len()
            != Graphs.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access graph ids must be nonempty and unique",
        ));
    }
    Members.sort_by(|First, Second| First.1.cmp(&Second.1).then_with(|| First.0.cmp(&Second.0)));
    if Members.iter().any(|Member| {
        Member.0.is_empty() || Member.2 >= Graphs.len() || Member.3.is_empty() || Member.6 < 1
    }) || Members
        .iter()
        .map(|Member| &Member.0)
        .collect::<BTreeSet<_>>()
        .len()
        != Members.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access members require exact unique identities, graphs, requests, and caps",
        ));
    }
    let EffectiveMaximumExpansionCount = MaximumAssignmentExpansionCount.clamp(1, 1_000_000);
    let mut AssignmentExpansionCount = 0usize;
    let mut EscapeExpansionCount = 0usize;
    let mut AttemptedTemplateIds = Vec::new();
    let mut AttemptPairwiseIncompatibleSignals = Vec::new();
    let mut AttemptFailureNets = Vec::new();
    let mut AttemptExpansionCounts = Vec::new();
    let mut AttemptPartialCandidateIds = Vec::new();
    let mut FirstConflictSignals = Vec::new();
    let mut FirstConflictResourceIndices = Vec::new();
    let mut FirstPairwiseIncompatibleSignals = Vec::new();
    let mut CertifiedFixedConflictByGraph = HashMap::<usize, (String, String)>::new();
    let mut PrefetchedEscapeResults = HashMap::<
        String,
        (
            Vec<EscapeRequest>,
            String,
            Vec<EscapeRequestResult>,
            usize,
            bool,
            bool,
        ),
    >::new();

    for MemberIndex in 0..Members.len() {
        let (
            TemplateId,
            TemplateObjective,
            GraphIndex,
            mut Requests,
            RequestMetadata,
            MaximumY,
            EscapeExpansionLimit,
        ) = Members[MemberIndex].clone();
        if Deadline.Check() {
            let Result = LayeredAccessTemplateResult(
                "Incomplete",
                false,
                false,
                "assignment-deadline",
                None,
                Vec::new(),
                Vec::new(),
                AssignmentExpansionCount,
                false,
                true,
                None,
                FirstConflictSignals,
                FirstConflictResourceIndices,
                FirstPairwiseIncompatibleSignals,
                false,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, None, EscapeExpansionCount));
        }
        AttemptedTemplateIds.push(TemplateId.clone());
        for Request in &mut Requests {
            Request.2.retain(|Ingress| Ingress.1 <= MaximumY);
        }
        let FixedPrefixCandidates = BuildFixedPrefixAccessCandidates(&Requests, &RequestMetadata);
        if let Some((FirstVariable, SecondVariable)) =
            CertifiedFixedConflictByGraph.get(&GraphIndex)
        {
            let FixedConflictStillApplies = FixedPrefixCandidates
                .get(FirstVariable)
                .zip(FixedPrefixCandidates.get(SecondVariable))
                .is_some_and(|(First, Second)| DeferredAccessCandidatesConflict(First, Second));
            if FixedConflictStillApplies {
                let Conflict = (FirstVariable.clone(), SecondVariable.clone());
                AttemptFailureNets.push((TemplateId.clone(), Some(FirstVariable.clone())));
                AttemptExpansionCounts.push((TemplateId.clone(), 0));
                AttemptPartialCandidateIds.push((TemplateId.clone(), Vec::new()));
                AttemptPairwiseIncompatibleSignals
                    .push((TemplateId.clone(), vec![Conflict.clone()]));
                if FirstPairwiseIncompatibleSignals.is_empty() {
                    FirstPairwiseIncompatibleSignals.push(Conflict);
                }
                continue;
            }
        }
        let (_GraphId, Adjacency) = &Graphs[GraphIndex];
        let (
            EffectiveRequests,
            EscapeStatus,
            EscapeResults,
            MemberEscapeExpansionCount,
            EscapeWorkCapExceeded,
            EscapeDeadlineExceeded,
        ) = if let Some(Prefetched) = PrefetchedEscapeResults.remove(&TemplateId) {
            Prefetched
        } else {
            let Outcome = BuildDerivedEscapeStatePathsWithMaximumYAndDeadline(
                Adjacency.clone(),
                Requests.clone(),
                BendPenalty,
                EscapeExpansionLimit,
                Deadline.clone(),
                Some(MaximumY),
            );
            (
                Requests.clone(),
                Outcome.0,
                Outcome.1,
                Outcome.2,
                Outcome.3,
                Outcome.4,
            )
        };
        Requests = EffectiveRequests;
        EscapeExpansionCount = EscapeExpansionCount.saturating_add(MemberEscapeExpansionCount);
        let SelectedMemberResult = (
            TemplateId.clone(),
            EscapeStatus,
            EscapeResults.clone(),
            MemberEscapeExpansionCount,
            EscapeWorkCapExceeded,
            EscapeDeadlineExceeded,
        );
        if EscapeWorkCapExceeded || EscapeDeadlineExceeded {
            let Result = LayeredAccessTemplateResult(
                "Incomplete",
                false,
                false,
                if EscapeDeadlineExceeded {
                    "assignment-deadline"
                } else {
                    "escape-work-cap"
                },
                None,
                Vec::new(),
                Vec::new(),
                AssignmentExpansionCount,
                EscapeWorkCapExceeded,
                EscapeDeadlineExceeded,
                None,
                FirstConflictSignals,
                FirstConflictResourceIndices,
                FirstPairwiseIncompatibleSignals,
                false,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, Some(SelectedMemberResult), EscapeExpansionCount));
        }
        let Some((mut Groups, ResourceCount)) = BuildLayeredAccessCandidateGroups(
            &Requests,
            &EscapeResults,
            &RequestMetadata,
            &Deadline,
        )?
        else {
            let Result = LayeredAccessTemplateResult(
                "Incomplete",
                false,
                false,
                "assignment-deadline",
                None,
                Vec::new(),
                Vec::new(),
                AssignmentExpansionCount,
                false,
                true,
                None,
                FirstConflictSignals,
                FirstConflictResourceIndices,
                FirstPairwiseIncompatibleSignals,
                false,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, Some(SelectedMemberResult), EscapeExpansionCount));
        };
        let InitialExpansionCount = AssignmentExpansionCount;
        let Assignment = PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline(
            &mut Groups,
            ResourceCount,
            AssignmentExpansionCount,
            EffectiveMaximumExpansionCount,
            Deadline.clone(),
            true,
            true,
            None,
        )?;
        AssignmentExpansionCount = Assignment
            .ExpansionCount
            .max(AssignmentExpansionCount)
            .min(EffectiveMaximumExpansionCount);
        AttemptFailureNets.push((TemplateId.clone(), Assignment.FailureNet.clone()));
        AttemptExpansionCounts.push((
            TemplateId.clone(),
            AssignmentExpansionCount.saturating_sub(InitialExpansionCount),
        ));
        AttemptPartialCandidateIds
            .push((TemplateId.clone(), Assignment.SelectedCandidateIds.clone()));
        AttemptPairwiseIncompatibleSignals.push((
            TemplateId.clone(),
            Assignment.PairwiseIncompatibleSignals.clone(),
        ));
        if FirstConflictSignals.is_empty() && !Assignment.ConflictSignals.is_empty() {
            FirstConflictSignals = Assignment.ConflictSignals.clone();
            FirstConflictResourceIndices = Assignment.ConflictResourceIndices.clone();
        }
        if FirstPairwiseIncompatibleSignals.is_empty()
            && !Assignment.PairwiseIncompatibleSignals.is_empty()
        {
            FirstPairwiseIncompatibleSignals = Assignment.PairwiseIncompatibleSignals.clone();
        }
        if Assignment.DeadlineExceeded || Assignment.BudgetExhausted {
            let Result = LayeredAccessTemplateResult(
                "Incomplete",
                false,
                false,
                if Assignment.DeadlineExceeded {
                    "assignment-deadline"
                } else {
                    "assignment-work-cap"
                },
                None,
                Vec::new(),
                Vec::new(),
                AssignmentExpansionCount,
                Assignment.BudgetExhausted,
                Assignment.DeadlineExceeded,
                Assignment.FailureNet,
                FirstConflictSignals,
                FirstConflictResourceIndices,
                FirstPairwiseIncompatibleSignals,
                Assignment.PairwiseCompatibilityComplete,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, Some(SelectedMemberResult), EscapeExpansionCount));
        }
        if !Assignment.Success {
            if let Some((FirstVariable, SecondVariable)) = Assignment
                .PairwiseIncompatibleSignals
                .iter()
                .find(|(FirstVariable, SecondVariable)| {
                    FixedPrefixCandidates
                        .get(FirstVariable)
                        .zip(FixedPrefixCandidates.get(SecondVariable))
                        .is_some_and(|(First, Second)| {
                            DeferredAccessCandidatesConflict(First, Second)
                        })
                })
            {
                CertifiedFixedConflictByGraph
                    .insert(GraphIndex, (FirstVariable.clone(), SecondVariable.clone()));
            }
            if !CertifiedFixedConflictByGraph.contains_key(&GraphIndex) {
                let FutureMembers = Members
                    .iter()
                    .skip(MemberIndex + 1)
                    .filter(|Member| Member.2 == GraphIndex)
                    .filter(|Member| !PrefetchedEscapeResults.contains_key(&Member.0))
                    .cloned()
                    .collect::<Vec<_>>();
                let Prefetched = RoutingThreadPool().install(|| {
                    FutureMembers
                        .into_par_iter()
                        .map(
                            |(
                                FutureTemplateId,
                                _FutureObjective,
                                FutureGraphIndex,
                                mut FutureRequests,
                                _FutureMetadata,
                                FutureMaximumY,
                                FutureEscapeExpansionLimit,
                            )| {
                                for Request in &mut FutureRequests {
                                    Request.2.retain(|Ingress| Ingress.1 <= FutureMaximumY);
                                }
                                let Outcome = BuildDerivedEscapeStatePathsWithMaximumYAndDeadline(
                                    Graphs[FutureGraphIndex].1.clone(),
                                    FutureRequests.clone(),
                                    BendPenalty,
                                    FutureEscapeExpansionLimit,
                                    Deadline.clone(),
                                    Some(FutureMaximumY),
                                );
                                (
                                    FutureTemplateId,
                                    (
                                        FutureRequests,
                                        Outcome.0,
                                        Outcome.1,
                                        Outcome.2,
                                        Outcome.3,
                                        Outcome.4,
                                    ),
                                )
                            },
                        )
                        .collect::<Vec<_>>()
                });
                PrefetchedEscapeResults.extend(Prefetched);
            }
        }
        if Assignment.Success {
            let Result = LayeredAccessTemplateResult(
                "Feasible",
                true,
                true,
                "",
                Some(TemplateId),
                TemplateObjective,
                Assignment.SelectedCandidateIds,
                AssignmentExpansionCount,
                false,
                false,
                None,
                Vec::new(),
                Vec::new(),
                Vec::new(),
                true,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, Some(SelectedMemberResult), EscapeExpansionCount));
        }
    }
    let Result = LayeredAccessTemplateResult(
        "Incomplete",
        false,
        false,
        "non-exhaustive-template-domain",
        None,
        Vec::new(),
        Vec::new(),
        AssignmentExpansionCount,
        false,
        false,
        None,
        FirstConflictSignals,
        FirstConflictResourceIndices,
        FirstPairwiseIncompatibleSignals,
        true,
        AttemptedTemplateIds,
        AttemptPairwiseIncompatibleSignals,
        AttemptFailureNets,
        AttemptExpansionCounts,
        AttemptPartialCandidateIds,
        EscapeExpansionCount,
        StartedAt,
    );
    Ok((Result, None, EscapeExpansionCount))
}

/// Traverse every exact layer member once, compose access and canonical guide
/// factors in native memory, and select one coherent member under one shared
/// assignment counter and absolute deadline.  No Python access-stub or guide
/// domain is materialized before this operation returns its selected witness.
pub(crate) fn SolveLayeredAccessGuideFactorCatalogWithDeadline(
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    mut Members: Vec<LayeredAccessGuideMemberValue>,
    BendPenalty: usize,
    MaximumAssignmentExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<LayeredAccessGuideSelectionResult> {
    let StartedAt = Instant::now();
    if Graphs.is_empty() || Members.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access-guide selection requires graphs and members",
        ));
    }
    Members.sort_by(|First, Second| First.1.cmp(&Second.1).then_with(|| First.0.cmp(&Second.0)));
    if Members.iter().any(|Member| {
        Member.0.is_empty()
            || Member.2 >= Graphs.len()
            || Member.3.is_empty()
            || Member.6 < 1
            || Member.7 .0.is_empty()
            || Member.7 .5 < 1
    }) || Members
        .iter()
        .map(|Member| &Member.0)
        .collect::<BTreeSet<_>>()
        .len()
        != Members.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access-guide members require exact unique complete controls",
        ));
    }
    let mut EscapeExpansionCount = 0usize;
    let mut AssignmentExpansionCount = 0usize;
    let EffectiveMaximumExpansionCount = MaximumAssignmentExpansionCount.clamp(1, 1_000_000);
    let mut AttemptedTemplateIds = Vec::new();
    let mut AttemptPairwiseIncompatibleSignals = Vec::new();
    let mut AttemptFailureNets = Vec::new();
    let mut AttemptExpansionCounts = Vec::new();
    let mut AttemptPartialCandidateIds = Vec::new();
    let mut FirstConflictSignals = Vec::new();
    let mut FirstConflictResourceIndices = Vec::new();
    let mut FirstPairwiseIncompatibleSignals = Vec::new();
    // Member views of one immutable placement graph frequently differ only
    // in interface contract.  Their guide factors still require the exact
    // same connectivity and powered-path proofs.  Keep those proofs scoped
    // to this one native catalog call and intern them by complete physical
    // identity; unrelated graph/ceiling/claim worlds remain independent.
    let SharedAccessRampCache = Arc::new(LayeredGuideAccessRampCache::New());
    let mut MemberStart = 0usize;
    while MemberStart < Members.len() {
        // Members are already in immutable objective/template order. Evaluate
        // one deterministic native frontier at a time, then consume outcomes
        // in that same order. This uses the existing worker pool and shared
        // assignment/deadline bounds; it neither prunes worlds nor permits a
        // later witness to outrank an earlier complete feasible member.
        let FrontierObjective = &Members[MemberStart].1;
        let MemberEnd = Members[MemberStart..]
            .iter()
            .position(|Member| &Member.1 != FrontierObjective)
            .map_or(Members.len(), |Offset| MemberStart + Offset);
        let TierMembers = &Members[MemberStart..MemberEnd];
        let FixedBaseConflictMemberIds = TierMembers
            .iter()
            .filter(|Member| LayeredGuideControlsHaveFixedBaseConflict(&Member.7))
            .map(|Member| Member.0.clone())
            .collect::<HashSet<_>>();
        for (TierMemberOffset, Member) in TierMembers.iter().enumerate() {
            let MemberIndex = MemberStart + TierMemberOffset;
            let EscapeStartedAt = Instant::now();
            let EscapeResult = if FixedBaseConflictMemberIds.contains(&Member.0) {
                (
                    Member.0.clone(),
                    "Complete".to_string(),
                    Vec::new(),
                    0usize,
                    false,
                    false,
                )
            } else {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE_VERBOSE").is_some() {
                    eprintln!(
                        "native layered member requests member={} requests={:?}",
                        Member.0,
                        Member.3.iter().map(|Request| (Request.0.as_str(), Request.1, Request.2.as_slice())).collect::<Vec<_>>(),
                    );
                }
                let (EscapeStatus, mut EscapeResults, MemberEscapeExpansionCount, WorkCapExceeded, DeadlineExceeded) =
                    BuildLayeredAccessEscapeViewCatalogWithDeadline(
                        Graphs.clone(),
                        vec![(
                            Member.0.clone(),
                            Member.1.clone(),
                            Member.2,
                            Member.3.clone(),
                            Member.4.clone(),
                            Member.5,
                            Member.6,
                        )],
                        BendPenalty,
                        Member.6,
                        Deadline.clone(),
                    )?;
                EscapeExpansionCount =
                    EscapeExpansionCount.saturating_add(MemberEscapeExpansionCount);
                EscapeResults.pop().unwrap_or((
                    Member.0.clone(),
                    EscapeStatus,
                    Vec::new(),
                    MemberEscapeExpansionCount,
                    WorkCapExceeded,
                    DeadlineExceeded,
                ))
            };
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "native layered member escape member={} expansions={} elapsed={:.3}s",
                    Member.0,
                    EscapeResult.3,
                    EscapeStartedAt.elapsed().as_secs_f64(),
                );
            }
            let DomainStartedAt = Instant::now();
            let PreparedDomain = if FixedBaseConflictMemberIds.contains(&Member.0) {
                Some((
                    BTreeMap::from([(
                        "__fixed_base_claim_conflict__".to_string(),
                        Vec::new(),
                    )]),
                    1,
                    HashMap::new(),
                    Arc::new(vec![Vec::new()]),
                ))
            } else if EscapeResult.4 || EscapeResult.5 || EscapeResult.1 != "Complete" {
                None
            } else {
                BuildLayeredAccessGuideCandidateGroups(
                    &Member.3,
                    &EscapeResult.2,
                    &Member.4,
                    &Member.7,
                    &Graphs[Member.2].1,
                    MemberIndex,
                    Member.2,
                    Member.5,
                    true,
                    &SharedAccessRampCache,
                    &Deadline,
                )?
            };
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                let (
                    CacheHits,
                    CacheMisses,
                    KnownPoweredWitnesses,
                    ExhaustivePoweredProofs,
                    CacheEntries,
                ) = SharedAccessRampCache.Counts();
                eprintln!(
                    "native layered member domain member={} elapsed={:.3}s ramp_cache_hits={} misses={} known_powered_witnesses={} exhaustive_powered_proofs={} entries={}",
                    Member.0,
                    DomainStartedAt.elapsed().as_secs_f64(),
                    CacheHits,
                    CacheMisses,
                    KnownPoweredWitnesses,
                    ExhaustivePoweredProofs,
                    CacheEntries,
                );
            }
            AttemptedTemplateIds.push(Member.0.clone());
            if EscapeResult.4 || EscapeResult.5 || EscapeResult.1 != "Complete" {
                let Result = LayeredAccessTemplateResult(
                    "Incomplete",
                    false,
                    false,
                    if EscapeResult.5 {
                        "assignment-deadline"
                    } else {
                        "escape-work-cap"
                    },
                    None,
                    Vec::new(),
                    Vec::new(),
                    AssignmentExpansionCount,
                    EscapeResult.4,
                    EscapeResult.5,
                    None,
                    FirstConflictSignals,
                    FirstConflictResourceIndices,
                    FirstPairwiseIncompatibleSignals,
                    false,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((Result, Some(EscapeResult), Vec::new(), EscapeExpansionCount));
            }
            let RemainingMemberExpansionCount =
                EffectiveMaximumExpansionCount.saturating_sub(AssignmentExpansionCount);
            if RemainingMemberExpansionCount == 0 {
                let Result = LayeredAccessTemplateResult(
                    "Incomplete",
                    false,
                    false,
                    "assignment-work-cap",
                    None,
                    Vec::new(),
                    Vec::new(),
                    AssignmentExpansionCount,
                    true,
                    false,
                    None,
                    FirstConflictSignals,
                    FirstConflictResourceIndices,
                    FirstPairwiseIncompatibleSignals,
                    false,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((Result, Some(EscapeResult), Vec::new(), EscapeExpansionCount));
            }
            let AssignmentOutcome: PyResult<
                Option<(
                    crate::Models::RoutingAssignmentResult,
                    HashMap<String, SelectedLayeredGuideValue>,
                )>,
            > = (|| {
                let Some((
                    mut Groups,
                    ResourceCount,
                    GuideRecipes,
                    CrossAirByWire,
                )) =
                    PreparedDomain
                else {
                    return Ok(None);
                };
                let MemberExpansionCount = std::sync::atomic::AtomicUsize::new(0);
                let AssignmentStartedAt = Instant::now();
                let mut Assignment = SolveLayeredCatalogCandidateGroups(
                    &mut Groups,
                    ResourceCount,
                    CrossAirByWire,
                    None,
                    RemainingMemberExpansionCount,
                    &MemberExpansionCount,
                    &Deadline,
                )?;
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "native layered member assignment member={} expansions={} elapsed={:.3}s success={}",
                        Member.0,
                        MemberExpansionCount.load(std::sync::atomic::Ordering::SeqCst),
                        AssignmentStartedAt.elapsed().as_secs_f64(),
                        Assignment.Success,
                    );
                }
                Assignment.ExpansionCount =
                    MemberExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
                Ok(Some((Assignment, GuideRecipes)))
            })();
            let AssignmentOutcome = AssignmentOutcome?;
            if let Some((Assignment, _GuideRecipes)) = AssignmentOutcome.as_ref() {
                AssignmentExpansionCount = AssignmentExpansionCount
                    .saturating_add(Assignment.ExpansionCount)
                    .min(EffectiveMaximumExpansionCount);
            }
            let Some((Assignment, GuideRecipes)) = AssignmentOutcome else {
                let Result = LayeredAccessTemplateResult(
                    "Incomplete",
                    false,
                    false,
                    "assignment-deadline",
                    None,
                    Vec::new(),
                    Vec::new(),
                    AssignmentExpansionCount,
                    false,
                    true,
                    None,
                    FirstConflictSignals,
                    FirstConflictResourceIndices,
                    FirstPairwiseIncompatibleSignals,
                    false,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((Result, Some(EscapeResult), Vec::new(), EscapeExpansionCount));
            };
            AttemptFailureNets.push((Member.0.clone(), Assignment.FailureNet.clone()));
            AttemptExpansionCounts.push((Member.0.clone(), Assignment.ExpansionCount));
            AttemptPartialCandidateIds
                .push((Member.0.clone(), Assignment.SelectedCandidateIds.clone()));
            AttemptPairwiseIncompatibleSignals.push((
                Member.0.clone(),
                Assignment.PairwiseIncompatibleSignals.clone(),
            ));
            if FirstConflictSignals.is_empty() {
                FirstConflictSignals = Assignment.ConflictSignals.clone();
                FirstConflictResourceIndices = Assignment.ConflictResourceIndices.clone();
            }
            if FirstPairwiseIncompatibleSignals.is_empty() {
                FirstPairwiseIncompatibleSignals = Assignment.PairwiseIncompatibleSignals.clone();
            }
            if Assignment.DeadlineExceeded || Assignment.BudgetExhausted {
                let Result = LayeredAccessTemplateResult(
                    "Incomplete",
                    false,
                    false,
                    if Assignment.DeadlineExceeded {
                        "assignment-deadline"
                    } else {
                        "assignment-work-cap"
                    },
                    None,
                    Vec::new(),
                    Vec::new(),
                    AssignmentExpansionCount,
                    Assignment.BudgetExhausted,
                    Assignment.DeadlineExceeded,
                    Assignment.FailureNet,
                    FirstConflictSignals,
                    FirstConflictResourceIndices,
                    FirstPairwiseIncompatibleSignals,
                    Assignment.PairwiseCompatibilityComplete,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((Result, Some(EscapeResult), Vec::new(), EscapeExpansionCount));
            }
            if Assignment.Success {
                let SelectedAccessCandidateIdByVariable = Assignment
                    .SelectedCandidateIds
                    .iter()
                    .filter(|(Variable, _CandidateId)| Variable.starts_with("__access_terminal__:"))
                    .cloned()
                    .collect::<HashMap<_, _>>();
                let SelectedGuides = Assignment
                    .SelectedCandidateIds
                    .iter()
                    .filter_map(|(_Variable, CandidateId)| GuideRecipes.get(CandidateId).cloned())
                    .map(|mut Guide| {
                        let CertifiedAccessCandidateIds = Guide.2.clone();
                        for (AccessVariable, AccessCandidateId) in &mut Guide.2 {
                            *AccessCandidateId = SelectedAccessCandidateIdByVariable
                                .get(AccessVariable)
                                .expect(
                                    "selected guide portal contract has an exact access witness",
                                )
                                .clone();
                        }
                        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                            && CertifiedAccessCandidateIds != Guide.2
                        {
                            eprintln!(
                                "native layered selected guide substituted access witness signal={} certified={:?} selected={:?}",
                                Guide.0,
                                CertifiedAccessCandidateIds,
                                Guide.2,
                            );
                        }
                        Guide
                    })
                    .collect::<Vec<_>>();
                let Result = LayeredAccessTemplateResult(
                    "Feasible",
                    true,
                    true,
                    "",
                    Some(Member.0.clone()),
                    Member.1.clone(),
                    Assignment.SelectedCandidateIds,
                    AssignmentExpansionCount,
                    false,
                    false,
                    None,
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    true,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((
                    Result,
                    Some(EscapeResult),
                    SelectedGuides,
                    EscapeExpansionCount,
                ));
            }
        }
        MemberStart = MemberEnd;
    }
    let Result = LayeredAccessTemplateResult(
        "Incomplete",
        false,
        false,
        "non-exhaustive-template-domain",
        None,
        Vec::new(),
        Vec::new(),
        AssignmentExpansionCount,
        false,
        false,
        None,
        FirstConflictSignals,
        FirstConflictResourceIndices,
        FirstPairwiseIncompatibleSignals,
        true,
        AttemptedTemplateIds,
        AttemptPairwiseIncompatibleSignals,
        AttemptFailureNets,
        AttemptExpansionCounts,
        AttemptPartialCandidateIds,
        EscapeExpansionCount,
        StartedAt,
    );
    Ok((Result, None, Vec::new(), EscapeExpansionCount))
}

/// Evaluate independently fingerprinted layer/member graphs in one native
/// operation.  Every member retains its exact adjacency, request masks, and
/// finite expansion bound; only scheduling and the absolute deadline are
/// shared.  The caller sets the portfolio cap to the sum of those immutable
/// member bounds, so parallel execution cannot borrow work from another
/// physical world or make completion order-dependent.
pub(crate) fn BuildLayeredEscapeStatePathCatalogWithDeadline(
    Members: Vec<LayeredEscapeMemberRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> (String, Vec<LayeredEscapeMemberResult>, usize, bool, bool) {
    let DeclaredExpansionUpperBound = Members
        .iter()
        .fold(0usize, |Total, Member| Total.saturating_add(Member.3));
    if DeclaredExpansionUpperBound > MaximumExpansionCount {
        return ("WorkCapExceeded".to_string(), Vec::new(), 0, true, false);
    }
    let mut Outcomes: Vec<LayeredEscapeMemberResult> = Vec::with_capacity(Members.len());
    for (MemberId, Adjacency, Requests, MemberExpansionLimit) in Members {
        let (Status, Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded) =
            BuildDerivedEscapeStatePathsWithDeadline(
                Adjacency,
                Requests,
                BendPenalty,
                MemberExpansionLimit,
                Deadline.clone(),
            );
        Outcomes.push((
            MemberId,
            Status,
            Results,
            ExpansionCount,
            WorkCapExceeded,
            DeadlineExceeded,
        ));
        if DeadlineExceeded {
            break;
        }
    }
    let ExpansionCount = Outcomes
        .iter()
        .fold(0usize, |Total, Result| Total.saturating_add(Result.3));
    let WorkCapExceeded =
        Outcomes.iter().any(|Result| Result.4) || ExpansionCount > MaximumExpansionCount;
    let DeadlineExceeded = Outcomes.iter().any(|Result| Result.5);
    let Status = if DeadlineExceeded {
        "DeadlineExceeded"
    } else if WorkCapExceeded {
        "WorkCapExceeded"
    } else {
        "Complete"
    };
    (
        Status.to_string(),
        Outcomes,
        ExpansionCount,
        WorkCapExceeded,
        DeadlineExceeded,
    )
}

fn ReuseExactEscapeRequestResultAtMaximumY(
    CachedRequest: &EscapeRequest,
    CachedResult: &EscapeRequestResult,
    Request: &EscapeRequest,
    MaximumY: i32,
) -> Option<EscapeRequestResult> {
    if !CachedResult.3
        || CachedRequest.0 != Request.0
        || CachedRequest.1 != Request.1
        || CachedRequest.3 != Request.3
        || CachedRequest.4 != Request.4
        || CachedRequest.5 != Request.5
        || CachedRequest.6 != Request.6
    {
        return None;
    }
    let CachedIngresses = CachedRequest.2.iter().copied().collect::<BTreeSet<_>>();
    let RequestedIngresses = Request.2.iter().copied().collect::<BTreeSet<_>>();
    if !RequestedIngresses.is_subset(&CachedIngresses) {
        return None;
    }
    let RelevantCandidates = CachedResult
        .1
        .iter()
        .filter(|(Ingress, _Direction, _Path)| RequestedIngresses.contains(Ingress))
        .cloned()
        .collect::<Vec<_>>();
    if RelevantCandidates
        .iter()
        .any(|(_Ingress, _Direction, Path)| {
            Path.iter().any(|PositionValue| PositionValue.1 > MaximumY)
        })
    {
        return None;
    }
    Some((Request.0.clone(), RelevantCandidates, 0, true))
}

/// Traverse every exact layer view over a shared set of immutable source
/// graphs.  A view owns its own request mask, Y ceiling, and finite work cap;
/// sharing a graph index never projects a path from another layer.  Rayon may
/// schedule independent views concurrently, while indexed collection keeps
/// the declared member order deterministic.
pub(crate) fn BuildLayeredAccessEscapeViewCatalogWithDeadline(
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    Members: Vec<LayeredAccessEscapeMemberValue>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<(String, Vec<LayeredEscapeMemberResult>, usize, bool, bool)> {
    if Graphs.is_empty() || Members.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access view catalog requires graphs and members",
        ));
    }
    if Graphs
        .iter()
        .any(|(GraphId, _Adjacency)| GraphId.is_empty())
        || Graphs
            .iter()
            .map(|(GraphId, _Adjacency)| GraphId)
            .collect::<BTreeSet<_>>()
            .len()
            != Graphs.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access view graph ids must be nonempty and unique",
        ));
    }
    if Members.iter().any(|Member| {
        Member.0.is_empty() || Member.2 >= Graphs.len() || Member.3.is_empty() || Member.6 < 1
    }) || Members
        .iter()
        .map(|Member| &Member.0)
        .collect::<BTreeSet<_>>()
        .len()
        != Members.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access views require exact unique identities, graphs, requests, and caps",
        ));
    }
    let DeclaredExpansionUpperBound = Members
        .iter()
        .fold(0usize, |Total, Member| Total.saturating_add(Member.6));
    if DeclaredExpansionUpperBound > MaximumExpansionCount {
        return Ok(("WorkCapExceeded".to_string(), Vec::new(), 0, true, false));
    }
    let ReferencedGraphIndices = Members
        .iter()
        .map(|Member| Member.2)
        .collect::<BTreeSet<_>>();
    let PreparedGraphs = Graphs
        .into_iter()
        .enumerate()
        .map(|(GraphIndex, (_GraphId, Adjacency))| {
            ReferencedGraphIndices
                .contains(&GraphIndex)
                .then(|| Arc::new(PreparedEscapeTraversalGraph::New(Adjacency)))
        })
        .collect::<Vec<_>>();
    let mut MembersByGraph = BTreeMap::<usize, Vec<(usize, LayeredAccessEscapeMemberValue)>>::new();
    for (MemberIndex, Member) in Members.into_iter().enumerate() {
        MembersByGraph
            .entry(Member.2)
            .or_default()
            .push((MemberIndex, Member));
    }
    for GraphMembers in MembersByGraph.values_mut() {
        GraphMembers.sort_by(|First, Second| {
            Second
                .1
                 .5
                .cmp(&First.1 .5)
                .then_with(|| First.1 .1.cmp(&Second.1 .1))
                .then_with(|| First.1 .0.cmp(&Second.1 .0))
        });
    }
    let MemberCount = MembersByGraph.values().map(Vec::len).sum::<usize>();
    let mut IndexedOutcomes = std::iter::repeat_with(|| None)
        .take(MemberCount)
        .collect::<Vec<Option<LayeredEscapeMemberResult>>>();
    let mut CachedByGraphAndRequestId = MembersByGraph
        .keys()
        .map(|GraphIndex| {
            (
                *GraphIndex,
                HashMap::<String, (EscapeRequest, EscapeRequestResult)>::new(),
            )
        })
        .collect::<HashMap<_, _>>();
    let MaximumWaveCount = MembersByGraph.values().map(Vec::len).max().unwrap_or(0);
    for WaveIndex in 0..MaximumWaveCount {
        struct PreparedWaveMember {
            OriginalIndex: usize,
            MemberId: String,
            GraphIndex: usize,
            Requests: Vec<EscapeRequest>,
            ReusedResults: HashMap<String, EscapeRequestResult>,
            ExpansionLimit: usize,
            ExpectedUnitCounts: Vec<usize>,
        }
        struct WaveWorkUnit {
            UnitIndex: usize,
            MemberIndex: usize,
            GraphIndex: usize,
            RequestIndex: usize,
            MaximumY: i32,
            Request: EscapeRequest,
        }

        let mut PreparedMembers = Vec::<PreparedWaveMember>::new();
        let mut WorkUnits = Vec::<WaveWorkUnit>::new();
        for (GraphIndex, GraphMembers) in &MembersByGraph {
            let Some((
                OriginalIndex,
                (
                    MemberId,
                    _Objective,
                    _MemberGraphIndex,
                    MemberRequests,
                    _RequestMetadata,
                    MaximumY,
                    MemberExpansionLimit,
                ),
            )) = GraphMembers.get(WaveIndex)
            else {
                continue;
            };
            let mut Requests = MemberRequests.clone();
            for Request in &mut Requests {
                Request.2.retain(|Ingress| Ingress.1 <= *MaximumY);
            }
            let CachedByRequestId = CachedByGraphAndRequestId
                .get(GraphIndex)
                .expect("referenced graph cache");
            let mut ReusedResults = HashMap::<String, EscapeRequestResult>::new();
            let MemberIndex = PreparedMembers.len();
            let mut ExpectedUnitCounts = vec![0usize; Requests.len()];
            for (RequestIndex, Request) in Requests.iter().enumerate() {
                let Reused =
                    CachedByRequestId
                        .get(&Request.0)
                        .and_then(|(CachedRequest, CachedResult)| {
                            ReuseExactEscapeRequestResultAtMaximumY(
                                CachedRequest,
                                CachedResult,
                                Request,
                                *MaximumY,
                            )
                        });
                if let Some(ResultValue) = Reused {
                    ReusedResults.insert(Request.0.clone(), ResultValue);
                } else if Request.6 && Request.2.len() > 1 {
                    for Ingress in &Request.2 {
                        let mut UnitRequest = Request.clone();
                        UnitRequest.2 = vec![*Ingress];
                        WorkUnits.push(WaveWorkUnit {
                            UnitIndex: WorkUnits.len(),
                            MemberIndex,
                            GraphIndex: *GraphIndex,
                            RequestIndex,
                            MaximumY: *MaximumY,
                            Request: UnitRequest,
                        });
                        ExpectedUnitCounts[RequestIndex] += 1;
                    }
                } else {
                    WorkUnits.push(WaveWorkUnit {
                        UnitIndex: WorkUnits.len(),
                        MemberIndex,
                        GraphIndex: *GraphIndex,
                        RequestIndex,
                        MaximumY: *MaximumY,
                        Request: Request.clone(),
                    });
                    ExpectedUnitCounts[RequestIndex] = 1;
                }
            }
            PreparedMembers.push(PreparedWaveMember {
                OriginalIndex: *OriginalIndex,
                MemberId: MemberId.clone(),
                GraphIndex: *GraphIndex,
                Requests,
                ReusedResults,
                ExpansionLimit: *MemberExpansionLimit,
                ExpectedUnitCounts,
            });
        }

        let ExpansionBudgets = PreparedMembers
            .iter()
            .map(|Member| Arc::new(SharedEscapeExpansionBudget::New(Member.ExpansionLimit)))
            .collect::<Vec<_>>();
        let WorkerCount = RoutingThreadPool()
            .current_num_threads()
            .max(1)
            .min(WorkUnits.len().max(1));
        let mut WorkShards = (0..WorkerCount)
            .map(|_| Vec::<WaveWorkUnit>::new())
            .collect::<Vec<_>>();
        for WorkUnit in WorkUnits {
            let MixedUnitIndex =
                WorkUnit.UnitIndex ^ (WorkUnit.UnitIndex >> 3) ^ (WorkUnit.UnitIndex >> 6);
            WorkShards[MixedUnitIndex % WorkerCount].push(WorkUnit);
        }
        for WorkShard in &mut WorkShards {
            WorkShard.sort_by(|First, Second| {
                First
                    .GraphIndex
                    .cmp(&Second.GraphIndex)
                    .then_with(|| First.MemberIndex.cmp(&Second.MemberIndex))
                    .then_with(|| First.UnitIndex.cmp(&Second.UnitIndex))
            });
        }
        let ShardOutcomes = RoutingThreadPool().install(|| {
            WorkShards
                .into_par_iter()
                .map(|WorkShard| {
                    let mut ActiveGraphIndex = usize::MAX;
                    let mut Workspace = None::<IndexedEscapeWorkspace>;
                    let mut Outcomes = Vec::with_capacity(WorkShard.len());
                    for WorkUnit in WorkShard {
                        if Deadline.Check() {
                            break;
                        }
                        if ActiveGraphIndex != WorkUnit.GraphIndex {
                            let PreparedGraph = PreparedGraphs[WorkUnit.GraphIndex]
                                .as_ref()
                                .expect("referenced graph is prepared");
                            Workspace =
                                Some(IndexedEscapeWorkspace::New(PreparedGraph.IndexedStateCount));
                            ActiveGraphIndex = WorkUnit.GraphIndex;
                        }
                        let PreparedGraph = PreparedGraphs[WorkUnit.GraphIndex]
                            .as_ref()
                            .expect("referenced graph is prepared");
                        let mut ExpansionLease = SharedEscapeExpansionLease::New(
                            ExpansionBudgets[WorkUnit.MemberIndex].as_ref(),
                        );
                        let (Result, WorkCapExceeded, DeadlineExceeded) =
                            BuildOneDerivedEscapeRequest(
                                &PreparedGraph.Adjacency,
                                &PreparedGraph.IndexedGraph,
                                Workspace.as_mut().expect("active graph workspace"),
                                WorkUnit.Request,
                                BendPenalty,
                                PreparedMembers[WorkUnit.MemberIndex].ExpansionLimit,
                                Deadline.clone(),
                                Some(WorkUnit.MaximumY),
                                Some(&mut ExpansionLease),
                            );
                        Outcomes.push((
                            WorkUnit.UnitIndex,
                            WorkUnit.MemberIndex,
                            WorkUnit.RequestIndex,
                            Result,
                            WorkCapExceeded,
                            DeadlineExceeded,
                        ));
                        if DeadlineExceeded {
                            break;
                        }
                    }
                    Outcomes
                })
                .collect::<Vec<_>>()
        });
        let DeadlineExceeded = Deadline.Check()
            || ShardOutcomes.iter().flatten().any(
                |(_UnitIndex, _MemberIndex, _RequestIndex, _Result, _WorkCap, DeadlineValue)| {
                    *DeadlineValue
                },
            );
        let mut UnitOutcomes = ShardOutcomes.into_iter().flatten().collect::<Vec<_>>();
        UnitOutcomes.sort_by_key(|Value| Value.0);
        for (MemberIndex, Member) in PreparedMembers.into_iter().enumerate() {
            let mut CompletedUnitCounts = vec![0usize; Member.Requests.len()];
            let mut ResultByRequestId = Member.ReusedResults;
            let mut MemberWorkCapExceeded = false;
            let mut PendingResults = Member
                .Requests
                .iter()
                .map(|Request| (Request.0.clone(), Vec::new(), 0usize, true))
                .collect::<Vec<EscapeRequestResult>>();
            for (
                _UnitIndex,
                UnitMemberIndex,
                RequestIndex,
                UnitResult,
                UnitWorkCapExceeded,
                _UnitDeadlineExceeded,
            ) in UnitOutcomes.iter().filter(|Value| Value.1 == MemberIndex)
            {
                CompletedUnitCounts[*RequestIndex] += 1;
                PendingResults[*RequestIndex]
                    .1
                    .extend(UnitResult.1.iter().cloned());
                PendingResults[*RequestIndex].2 =
                    PendingResults[*RequestIndex].2.saturating_add(UnitResult.2);
                PendingResults[*RequestIndex].3 &= UnitResult.3;
                MemberWorkCapExceeded |= *UnitWorkCapExceeded;
                debug_assert_eq!(*UnitMemberIndex, MemberIndex);
            }
            for RequestIndex in 0..PendingResults.len() {
                if Member.ExpectedUnitCounts[RequestIndex] == 0 {
                    continue;
                }
                PendingResults[RequestIndex].3 &=
                    CompletedUnitCounts[RequestIndex] == Member.ExpectedUnitCounts[RequestIndex];
                if PendingResults[RequestIndex].3 {
                    let ResultValue = PendingResults[RequestIndex].clone();
                    ResultByRequestId.insert(ResultValue.0.clone(), ResultValue);
                }
            }
            let OrderedResults = Member
                .Requests
                .iter()
                .filter_map(|Request| ResultByRequestId.get(&Request.0).cloned())
                .collect::<Vec<_>>();
            let ExpansionCount = ExpansionBudgets[MemberIndex].ExpansionCount();
            MemberWorkCapExceeded |= !DeadlineExceeded
                && Member
                    .ExpectedUnitCounts
                    .iter()
                    .zip(&CompletedUnitCounts)
                    .any(|(Expected, Completed)| Expected != Completed);
            let MemberComplete = !MemberWorkCapExceeded
                && !DeadlineExceeded
                && OrderedResults.len() == Member.Requests.len()
                && OrderedResults.iter().all(|ResultValue| ResultValue.3);
            let EffectiveStatus = if MemberComplete {
                "Complete"
            } else if DeadlineExceeded {
                "DeadlineExceeded"
            } else {
                "WorkCapExceeded"
            };
            if MemberComplete {
                let CachedByRequestId = CachedByGraphAndRequestId
                    .get_mut(&Member.GraphIndex)
                    .expect("referenced graph cache");
                for (Request, ResultValue) in Member
                    .Requests
                    .iter()
                    .cloned()
                    .zip(OrderedResults.iter().cloned())
                {
                    CachedByRequestId.insert(Request.0.clone(), (Request, ResultValue));
                }
            }
            IndexedOutcomes[Member.OriginalIndex] = Some((
                Member.MemberId,
                EffectiveStatus.to_string(),
                OrderedResults,
                ExpansionCount,
                MemberWorkCapExceeded,
                DeadlineExceeded,
            ));
        }
        if DeadlineExceeded {
            break;
        }
    }
    let Outcomes = IndexedOutcomes.into_iter().flatten().collect::<Vec<_>>();
    let ExpansionCount = Outcomes
        .iter()
        .fold(0usize, |Total, Result| Total.saturating_add(Result.3));
    let WorkCapExceeded =
        Outcomes.iter().any(|Result| Result.4) || ExpansionCount > MaximumExpansionCount;
    let DeadlineExceeded = Outcomes.iter().any(|Result| Result.5);
    let Status = if DeadlineExceeded {
        "DeadlineExceeded"
    } else if WorkCapExceeded {
        "WorkCapExceeded"
    } else {
        "Complete"
    };
    Ok((
        Status.to_string(),
        Outcomes,
        ExpansionCount,
        WorkCapExceeded,
        DeadlineExceeded,
    ))
}

#[cfg(test)]
mod Tests {
    use super::*;

    #[test]
    fn AccessClaimCompositionRejectsEmergentCrossPathAirSupportConflict() {
        let First = BuildDeferredAccessCandidate(
            "first".to_string(),
            "first-value".to_string(),
            "signal".to_string(),
            2,
            vec![(75, 2, 14)],
        )
        .expect("single-cell access claim");
        let Second = BuildDeferredAccessCandidate(
            "second".to_string(),
            "second-value".to_string(),
            "signal".to_string(),
            3,
            vec![(75, 3, 13), (75, 4, 14)],
        )
        .expect("individually legal rising access claim");

        assert!(DeferredAccessCandidatesConflict(&First, &Second));
    }

    #[test]
    fn LayeredGuideContractBindsPortalBeforeExactStubSelection() {
        let Variable = "__access_terminal__:Net:root".to_string();
        let CandidateId = "stub-choice-7".to_string();
        let Contract = BuildLayeredGuideAccessContract(
            &[(Variable.clone(), CandidateId.clone())],
            &HashMap::from([((Variable, CandidateId), (3, 2, -4))]),
        );

        assert_eq!(Contract, "access-portal:Net:root=3,2,-4");
        let Requirements = ParseContractRequirements(&Contract);
        assert!(!Requirements.contains(&(
            "access-stub:Net:root".to_string(),
            "stub-choice-7".to_string(),
        )));
        assert!(
            Requirements.contains(&("access-portal:Net:root".to_string(), "3,2,-4".to_string(),))
        );
    }

    #[test]
    fn EnumeratesIngressDirectionStatesDeterministically() {
        let Result = BuildDerivedEscapeStatePathsWithDeadline(
            vec![
                ((0, 0, 0), vec![(1, 0, 0), (0, 0, 1)]),
                ((1, 0, 0), vec![(0, 0, 0), (1, 0, 1)]),
                ((0, 0, 1), vec![(0, 0, 0), (1, 0, 1)]),
                ((1, 0, 1), vec![(1, 0, 0), (0, 0, 1)]),
            ],
            vec![(
                "request".to_string(),
                (0, 0, 0),
                vec![(1, 0, 1)],
                Vec::new(),
                Vec::new(),
                vec![(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)],
                false,
            )],
            4,
            128,
            RuntimeDeadline::Unlimited(),
        );
        assert_eq!(Result.0, "Complete");
        assert_eq!(Result.1.len(), 1);
        assert_eq!(Result.1[0].1.len(), 2);
        assert_eq!(Result.1[0].1[0].2, vec![(0, 0, 0), (1, 0, 0), (1, 0, 1)]);

        let Rejected = BuildDerivedEscapeStatePathsWithDeadline(
            vec![
                ((0, 0, 0), vec![(1, 0, 0), (0, 0, 1)]),
                ((1, 0, 0), vec![(0, 0, 0), (1, 0, 1)]),
                ((0, 0, 1), vec![(0, 0, 0), (1, 0, 1)]),
                ((1, 0, 1), vec![(1, 0, 0), (0, 0, 1)]),
            ],
            vec![(
                "request".to_string(),
                (0, 0, 0),
                vec![(1, 0, 1)],
                vec![((1, 0, 1), (0, 0, 1))],
                Vec::new(),
                vec![(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)],
                false,
            )],
            4,
            128,
            RuntimeDeadline::Unlimited(),
        );
        assert_eq!(Rejected.1[0].1[0].2, vec![(0, 0, 0), (0, 0, 1), (1, 0, 1)]);
    }

    #[test]
    fn WorkCapIsIncompleteNotUnsatisfiable() {
        let Result = BuildDerivedEscapeStatePathsWithDeadline(
            vec![((0, 0, 0), vec![(1, 0, 0)]), ((1, 0, 0), vec![(0, 0, 0)])],
            vec![(
                "request".to_string(),
                (0, 0, 0),
                vec![(1, 0, 0)],
                Vec::new(),
                Vec::new(),
                vec![(0, 0, 0), (1, 0, 0)],
                false,
            )],
            4,
            1,
            RuntimeDeadline::Unlimited(),
        );
        assert_eq!(Result.0, "WorkCapExceeded");
        assert!(Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn BoundedRequestWavesStayCompleteBelowTheSharedCap() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (10, 0, 0);
        let D = (11, 0, 0);
        let Result = BuildDerivedEscapeStatePathsWithDeadline(
            vec![(A, vec![B]), (B, vec![A]), (C, vec![D]), (D, vec![C])],
            vec![
                (
                    "first".to_string(),
                    A,
                    vec![B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    true,
                ),
                (
                    "second".to_string(),
                    C,
                    vec![D],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    true,
                ),
            ],
            4,
            200,
            RuntimeDeadline::Unlimited(),
        );

        assert_eq!(Result.0, "Complete");
        assert_eq!(Result.1.len(), 2);
        assert!(Result.1.iter().all(|Request| Request.3));
        assert!(Result.2 <= 200);
        assert!(!Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn EmptyAllowedNodeMaskIsTheExactFullGraphSentinel() {
        let Adjacency = vec![
            ((0, 0, 0), vec![(1, 0, 0), (0, 0, 1)]),
            ((1, 0, 0), vec![(0, 0, 0), (1, 0, 1)]),
            ((0, 0, 1), vec![(0, 0, 0), (1, 0, 1)]),
            ((1, 0, 1), vec![(1, 0, 0), (0, 0, 1)]),
        ];
        let BuildRequest = |AllowedNodes: Vec<Position>| {
            vec![(
                "request".to_string(),
                (0, 0, 0),
                vec![(1, 0, 1)],
                Vec::new(),
                Vec::new(),
                AllowedNodes,
                false,
            )]
        };
        let Explicit = BuildDerivedEscapeStatePathsWithDeadline(
            Adjacency.clone(),
            BuildRequest(vec![(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)]),
            4,
            128,
            RuntimeDeadline::Unlimited(),
        );
        let Sentinel = BuildDerivedEscapeStatePathsWithDeadline(
            Adjacency,
            BuildRequest(Vec::new()),
            4,
            128,
            RuntimeDeadline::Unlimited(),
        );

        assert_eq!(Sentinel, Explicit);
    }

    #[test]
    fn LayeredCatalogPreservesMemberOrderAndExactGraphs() {
        let BuildMember = |MemberId: &str, Offset: i32| {
            (
                MemberId.to_string(),
                vec![
                    ((Offset, 0, 0), vec![(Offset + 1, 0, 0)]),
                    ((Offset + 1, 0, 0), vec![(Offset, 0, 0)]),
                ],
                vec![(
                    "request".to_string(),
                    (Offset, 0, 0),
                    vec![(Offset + 1, 0, 0)],
                    Vec::new(),
                    Vec::new(),
                    vec![(Offset, 0, 0), (Offset + 1, 0, 0)],
                    false,
                )],
                3usize,
            )
        };
        let Result = BuildLayeredEscapeStatePathCatalogWithDeadline(
            vec![BuildMember("upper", 0), BuildMember("lower", 10)],
            4,
            6,
            RuntimeDeadline::Unlimited(),
        );

        assert_eq!(Result.0, "Complete");
        assert_eq!(Result.1[0].0, "upper");
        assert_eq!(Result.1[1].0, "lower");
        assert_eq!(Result.1[0].2[0].1[0].2.first(), Some(&(0, 0, 0)));
        assert_eq!(Result.1[1].2[0].1[0].2.first(), Some(&(10, 0, 0)));
        assert!(!Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn LayeredCatalogRejectsAnInsufficientSharedCapBeforeWork() {
        let Result = BuildLayeredEscapeStatePathCatalogWithDeadline(
            vec![("member".to_string(), Vec::new(), Vec::new(), 2usize)],
            4,
            1,
            RuntimeDeadline::Unlimited(),
        );

        assert_eq!(Result.0, "WorkCapExceeded");
        assert!(Result.1.is_empty());
        assert_eq!(Result.2, 0);
        assert!(Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn LayeredAccessViewCatalogSearchesEachCeilingExactly() {
        let Graph = vec![
            ((0, 0, 0), vec![(0, 0, 1), (1, 1, 0)]),
            ((0, 0, 1), vec![(0, 0, 0), (1, 0, 1)]),
            ((1, 0, 1), vec![(0, 0, 1), (2, 0, 1)]),
            ((2, 0, 1), vec![(1, 0, 1), (2, 0, 0)]),
            ((1, 1, 0), vec![(0, 0, 0), (2, 0, 0)]),
            ((2, 0, 0), vec![(2, 0, 1), (1, 1, 0)]),
        ];
        let BuildMember = |MemberId: &str, MaximumY: i32| {
            (
                MemberId.to_string(),
                vec![0],
                0usize,
                vec![(
                    "request".to_string(),
                    (0, 0, 0),
                    vec![(2, 0, 0)],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    false,
                )],
                vec![(
                    "request".to_string(),
                    "__access_terminal__:signal:root".to_string(),
                    "signal".to_string(),
                )],
                MaximumY,
                128usize,
            )
        };
        let Result = BuildLayeredAccessEscapeViewCatalogWithDeadline(
            vec![("graph".to_string(), Graph)],
            vec![BuildMember("lower", 0), BuildMember("upper", 1)],
            4,
            256,
            RuntimeDeadline::Unlimited(),
        )
        .expect("layered access view catalog should be valid");

        assert_eq!(Result.0, "Complete");
        assert_eq!(Result.1[0].0, "lower");
        assert_eq!(Result.1[1].0, "upper");
        let LowerPaths = &Result.1[0].2[0].1;
        let UpperPaths = &Result.1[1].2[0].1;
        assert!(LowerPaths
            .iter()
            .all(|Value| { Value.2.iter().all(|PositionValue| PositionValue.1 <= 0) }));
        assert!(UpperPaths
            .iter()
            .any(|Value| { Value.2.contains(&(1, 1, 0)) }));
        assert!(!Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn LayeredAccessSelectionKeepsOneIngressLayerPerOwnerSignal() {
        let Requests = vec![
            (
                "a-low".to_string(),
                (0, 0, 0),
                vec![(1, 0, 0)],
                Vec::new(),
                vec![(0, 0, 0)],
                Vec::new(),
                true,
            ),
            (
                "a-high".to_string(),
                (0, 2, 0),
                vec![(1, 2, 0)],
                Vec::new(),
                vec![(-2, 2, 0), (-1, 2, 0), (0, 2, 0)],
                Vec::new(),
                true,
            ),
            (
                "b-low".to_string(),
                (10, 0, 0),
                vec![(11, 0, 0)],
                Vec::new(),
                vec![(8, 0, 0), (9, 0, 0), (10, 0, 0)],
                Vec::new(),
                true,
            ),
            (
                "b-high".to_string(),
                (10, 2, 0),
                vec![(11, 2, 0)],
                Vec::new(),
                vec![(10, 2, 0)],
                Vec::new(),
                true,
            ),
        ];
        let RequestResults = vec![
            (
                "a-low".to_string(),
                vec![((1, 0, 0), (1, 0, 0), vec![(0, 0, 0), (1, 0, 0)])],
                1,
                true,
            ),
            (
                "a-high".to_string(),
                vec![((1, 2, 0), (1, 0, 0), vec![(0, 2, 0), (1, 2, 0)])],
                1,
                true,
            ),
            (
                "b-low".to_string(),
                vec![((11, 0, 0), (1, 0, 0), vec![(10, 0, 0), (11, 0, 0)])],
                1,
                true,
            ),
            (
                "b-high".to_string(),
                vec![((11, 2, 0), (1, 0, 0), vec![(10, 2, 0), (11, 2, 0)])],
                1,
                true,
            ),
        ];
        let RequestMetadata = vec![
            (
                "a-low".to_string(),
                "__access_terminal__:Net:root".to_string(),
                "Net".to_string(),
            ),
            (
                "a-high".to_string(),
                "__access_terminal__:Net:root".to_string(),
                "Net".to_string(),
            ),
            (
                "b-low".to_string(),
                "__access_terminal__:Net:target-0".to_string(),
                "Net".to_string(),
            ),
            (
                "b-high".to_string(),
                "__access_terminal__:Net:target-0".to_string(),
                "Net".to_string(),
            ),
        ];
        let (mut Groups, ResourceCount) = BuildLayeredAccessCandidateGroups(
            &Requests,
            &RequestResults,
            &RequestMetadata,
            &RuntimeDeadline::Unlimited(),
        )
        .expect("valid layered access fixture")
        .expect("unlimited fixture cannot expire");
        let Result = PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline(
            &mut Groups,
            ResourceCount,
            0,
            64,
            RuntimeDeadline::Unlimited(),
            true,
            true,
            None,
        )
        .expect("layer-coherent access assignment");

        assert!(Result.Success);
        assert_eq!(
            Result.SelectedCandidateIds,
            vec![
                (
                    "__access_terminal__:Net:root".to_string(),
                    "a-low#0".to_string(),
                ),
                (
                    "__access_terminal__:Net:target-0".to_string(),
                    "b-low#0".to_string(),
                ),
            ],
        );
    }
}

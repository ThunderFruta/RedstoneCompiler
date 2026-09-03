//! Escape graph state, budgets, claims, and public value contracts.

use crate::Core::Models::{Direction, Position};
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

pub(super) const MAXIMUM_MEMBER_ESCAPE_SHARD_COUNT: usize = 8;

#[derive(Debug)]
pub(super) struct EscapeClaimNode {
    pub(super) Wire: Position,
    pub(super) Air: Option<Position>,
    pub(super) WireBloom: [u64; 4],
    pub(super) SupportBloom: [u64; 4],
    pub(super) AirBloom: [u64; 4],
    pub(super) Parent: Option<Arc<EscapeClaimNode>>,
}

pub(super) const ESCAPE_DIRECTION_STATE_COUNT: usize = 13;
pub(super) const ESCAPE_POWER_STATE_COUNT: usize = 16;

#[derive(Default)]
pub(super) struct PackedStateHasher(u64);

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

pub(super) type PackedStateMap<Value> =
    HashMap<usize, Value, BuildHasherDefault<PackedStateHasher>>;

pub(super) const ESCAPE_EXPANSION_LEASE_SIZE: usize = 4096;

pub(super) struct SharedEscapeExpansionBudget {
    pub(super) MaximumExpansionCount: usize,
    pub(super) ReservedExpansionCount: AtomicUsize,
    pub(super) CommittedExpansionCount: AtomicUsize,
}

impl SharedEscapeExpansionBudget {
    pub(super) fn New(MaximumExpansionCount: usize) -> Self {
        Self {
            MaximumExpansionCount,
            ReservedExpansionCount: AtomicUsize::new(0),
            CommittedExpansionCount: AtomicUsize::new(0),
        }
    }

    pub(super) fn ExpansionCount(&self) -> usize {
        self.CommittedExpansionCount.load(Ordering::Relaxed)
    }
}

pub(super) struct SharedEscapeExpansionLease<'a> {
    pub(super) Budget: &'a SharedEscapeExpansionBudget,
    pub(super) ReservedExpansionCount: usize,
    pub(super) RemainingExpansionCount: usize,
}

impl<'a> SharedEscapeExpansionLease<'a> {
    pub(super) fn New(Budget: &'a SharedEscapeExpansionBudget) -> Self {
        Self {
            Budget,
            ReservedExpansionCount: 0,
            RemainingExpansionCount: 0,
        }
    }

    pub(super) fn CommitAndRelease(&mut self) {
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

    pub(super) fn TryConsume(&mut self) -> bool {
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

pub(super) fn TryCountEscapeExpansion(
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

pub(super) fn LayeredEscapeLowerBound(
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
pub(super) struct PoweredEscapeVisit {
    pub(super) Cost: u32,
    pub(super) ParentState: u32,
    pub(super) ClaimRecord: u32,
}

impl PoweredEscapeVisit {
    pub(super) fn New(Cost: usize, ParentState: usize, ClaimRecord: usize) -> Self {
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

    pub(super) fn CostValue(self) -> usize {
        self.Cost as usize
    }

    pub(super) fn ParentStateValue(self) -> usize {
        if self.ParentState == u32::MAX {
            usize::MAX
        } else {
            self.ParentState as usize
        }
    }

    pub(super) fn ClaimRecordValue(self) -> usize {
        self.ClaimRecord as usize
    }
}
pub(super) const ESCAPE_INITIAL_DIRECTION_STATE: usize = 0;

pub(super) fn EscapeDirectionStateIndex(DirectionValue: Direction) -> usize {
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

pub(super) fn PoweredEscapeStateIndex(
    NodeIndex: usize,
    DirectionValue: Direction,
    PowerRemaining: u8,
) -> usize {
    (NodeIndex * ESCAPE_DIRECTION_STATE_COUNT + EscapeDirectionStateIndex(DirectionValue))
        * ESCAPE_POWER_STATE_COUNT
        + usize::from(PowerRemaining)
}

pub(super) fn PoweredEscapeStateNodeIndex(StateIndex: usize) -> usize {
    StateIndex / (ESCAPE_DIRECTION_STATE_COUNT * ESCAPE_POWER_STATE_COUNT)
}

pub(super) struct IndexedEscapeGraph {
    pub(super) Positions: Vec<Position>,
    pub(super) PositionIndices: HashMap<Position, usize>,
    pub(super) NeighborIndices: Vec<Vec<usize>>,
}

impl IndexedEscapeGraph {
    pub(super) fn New(Adjacency: &HashMap<Position, Vec<Position>>) -> Self {
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

    pub(super) fn StateIndex(&self, NodeIndex: usize, DirectionValue: Direction) -> usize {
        NodeIndex * ESCAPE_DIRECTION_STATE_COUNT + EscapeDirectionStateIndex(DirectionValue)
    }

    pub(super) fn StatePosition(&self, StateIndex: usize) -> Position {
        self.Positions[StateIndex / ESCAPE_DIRECTION_STATE_COUNT]
    }
}

pub(super) struct IndexedEscapeWorkspace {
    pub(super) BestCosts: Vec<usize>,
    pub(super) Epochs: Vec<u32>,
    pub(super) ParentStates: Vec<usize>,
    pub(super) ClaimRecordByState: Vec<usize>,
    pub(super) ClaimRecords: Vec<IndexedEscapeClaimNode>,
    pub(super) PoweredVisits: PackedStateMap<PoweredEscapeVisit>,
    pub(super) Epoch: u32,
}

impl IndexedEscapeWorkspace {
    pub(super) fn New(StateCount: usize) -> Self {
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

    pub(super) fn BeginSearch(&mut self) {
        self.ClaimRecords.clear();
        self.Epoch = self.Epoch.wrapping_add(1);
        if self.Epoch == 0 {
            self.Epochs.fill(0);
            self.Epoch = 1;
        }
    }

    pub(super) fn Cost(&self, StateIndex: usize) -> usize {
        if self.Epochs[StateIndex] == self.Epoch {
            self.BestCosts[StateIndex]
        } else {
            usize::MAX
        }
    }

    pub(super) fn SetState(
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

pub(super) fn EscapeClaimBloomIndex(PositionValue: Position) -> usize {
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

pub(super) fn EscapeClaimBloomContainsIndex(Bloom: &[u64; 4], Index: usize) -> bool {
    Bloom[Index / 64] & (1u64 << (Index % 64)) != 0
}

pub(super) fn EscapeClaimBloomInsertIndex(Bloom: &mut [u64; 4], Index: usize) {
    Bloom[Index / 64] |= 1u64 << (Index % 64);
}

pub(super) fn ExtendEscapeClaims(
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
pub(super) struct IndexedEscapeClaimNode {
    pub(super) Wire: Position,
    pub(super) Air: Option<Position>,
    pub(super) WireBloom: [u64; 4],
    pub(super) SupportBloom: [u64; 4],
    pub(super) AirBloom: [u64; 4],
    pub(super) Parent: Option<usize>,
}

pub(super) fn ExtendIndexedEscapeClaims(
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

#![allow(non_snake_case)]

use pyo3::prelude::*;
use rayon::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BinaryHeap, HashMap, HashSet};
use std::rc::Rc;
use std::sync::OnceLock;

type Position = (i32, i32, i32);
type Edge = (Position, Position);
type Direction = (i32, i32, i32);
type SearchState = (Position, Direction);
type Position2 = (i32, i32);
type RectilinearEdge = (Position2, Position2);
const MAXIMUM_EXPANSIONS: usize = 1_000_000;
const HEURISTIC_WEIGHT: i32 = 4;
const BLOCKED_EDGE_COST: i32 = 1_000_000_000;

#[derive(Clone, Default)]
struct ClaimMask {
    Wire: Vec<u64>,
    Support: Vec<u64>,
    Air: Vec<u64>,
    Electrical: Vec<u64>,
}

impl ClaimMask {
    fn New(ResourceCount: usize) -> Self {
        let WordCount = ResourceCount.div_ceil(64);
        Self {
            Wire: vec![0; WordCount],
            Support: vec![0; WordCount],
            Air: vec![0; WordCount],
            Electrical: vec![0; WordCount],
        }
    }

    fn FromIndices(
        ResourceCount: usize,
        Wire: &[usize],
        Support: &[usize],
        Air: &[usize],
        Electrical: &[usize],
    ) -> Option<Self> {
        let mut Result = Self::New(ResourceCount);
        for (Values, Mask) in [
            (Wire, &mut Result.Wire),
            (Support, &mut Result.Support),
            (Air, &mut Result.Air),
            (Electrical, &mut Result.Electrical),
        ] {
            for Index in Values {
                if *Index >= ResourceCount {
                    return None;
                }
                Mask[*Index / 64] |= 1u64 << (*Index % 64);
            }
        }
        Some(Result)
    }

    fn Intersects(First: &[u64], Second: &[u64]) -> bool {
        First.iter().zip(Second).any(|(A, B)| A & B != 0)
    }

    fn Conflicts(&self, Other: &Self) -> bool {
        Self::Intersects(&self.Wire, &Other.Electrical)
            || Self::Intersects(&Other.Wire, &self.Electrical)
            || Self::Intersects(&self.Support, &Other.Wire)
            || Self::Intersects(&self.Support, &Other.Air)
            || Self::Intersects(&Other.Support, &self.Wire)
            || Self::Intersects(&Other.Support, &self.Air)
            || Self::Intersects(&self.Air, &Other.Wire)
            || Self::Intersects(&Other.Air, &self.Wire)
    }

    fn UnionWith(&mut self, Other: &Self) {
        for (Target, Source) in [
            (&mut self.Wire, &Other.Wire),
            (&mut self.Support, &Other.Support),
            (&mut self.Air, &Other.Air),
            (&mut self.Electrical, &Other.Electrical),
        ] {
            for (TargetWord, SourceWord) in Target.iter_mut().zip(Source) {
                *TargetWord |= SourceWord;
            }
        }
    }

    fn ConflictIndices(&self, Other: &Self) -> Vec<usize> {
        let mut Result = HashSet::new();
        for (First, Second) in [
            (&self.Wire, &Other.Electrical),
            (&Other.Wire, &self.Electrical),
            (&self.Support, &Other.Wire),
            (&self.Support, &Other.Air),
            (&Other.Support, &self.Wire),
            (&Other.Support, &self.Air),
            (&self.Air, &Other.Wire),
            (&Other.Air, &self.Wire),
        ] {
            for (WordIndex, (A, B)) in First.iter().zip(Second).enumerate() {
                let mut Value = A & B;
                while Value != 0 {
                    let Bit = Value.trailing_zeros() as usize;
                    Result.insert(WordIndex * 64 + Bit);
                    Value &= Value - 1;
                }
            }
        }
        let mut Values: Vec<_> = Result.into_iter().collect();
        Values.sort_unstable();
        Values
    }
}

#[pyclass]
#[derive(Clone)]
struct PortalCandidate {
    #[pyo3(get)]
    PortalId: String,
    #[pyo3(get)]
    Target: Position,
    #[pyo3(get)]
    Path: Vec<Position>,
    #[pyo3(get)]
    WireClaims: Vec<Position>,
    #[pyo3(get)]
    SupportClaims: Vec<Position>,
    #[pyo3(get)]
    AirClaims: Vec<Position>,
    #[pyo3(get)]
    ElectricalClaims: Vec<Position>,
    #[pyo3(get)]
    Length: usize,
    #[pyo3(get)]
    BendCount: usize,
    #[pyo3(get)]
    ViaCount: usize,
}

#[derive(Clone)]
struct AssignmentCandidate {
    CandidateId: String,
    Claims: ClaimMask,
    MaterialCost: i32,
    FootprintGrowth: i32,
    Length: i32,
    BendCount: i32,
    ViaCount: i32,
}

#[pyclass]
struct RoutingAssignmentResult {
    #[pyo3(get)]
    Success: bool,
    #[pyo3(get)]
    SelectedCandidateIds: Vec<(String, String)>,
    #[pyo3(get)]
    ExpansionCount: usize,
    #[pyo3(get)]
    FailureNet: Option<String>,
    #[pyo3(get)]
    ConflictResourceIndices: Vec<usize>,
}

fn RoutingThreadPool() -> &'static ThreadPool {
    static POOL: OnceLock<ThreadPool> = OnceLock::new();
    POOL.get_or_init(|| {
        let Available = std::thread::available_parallelism()
            .map(|Value| Value.get())
            .unwrap_or(1);
        let Requested = std::env::var("RC_ROUTING_THREADS")
            .ok()
            .and_then(|Value| Value.parse::<usize>().ok())
            .filter(|Value| *Value > 0)
            .unwrap_or_else(|| Available.div_ceil(2));
        ThreadPoolBuilder::new()
            .num_threads(Requested.clamp(1, Available))
            .thread_name(|Index| format!("redstone-router-{Index}"))
            .build()
            .expect("could not create native routing thread pool")
    })
}

#[pyfunction]
fn GenerateRectilinearTopology(TerminalValues: Vec<Position2>) -> Vec<RectilinearEdge> {
    let mut Terminals = TerminalValues;
    Terminals.sort_unstable();
    Terminals.dedup();
    if Terminals.len() < 2 {
        return Vec::new();
    }
    let mut Tree = vec![Terminals.remove(0)];
    let mut Result = Vec::new();
    while !Terminals.is_empty() {
        let (TerminalIndex, Anchor) = Terminals
            .iter()
            .enumerate()
            .flat_map(|(Index, Terminal)| {
                Tree.iter().map(move |Existing| {
                    (
                        Index,
                        *Existing,
                        (Terminal.0 - Existing.0).abs() + (Terminal.1 - Existing.1).abs(),
                        *Terminal,
                    )
                })
            })
            .min_by_key(|(Index, Existing, Distance, Terminal)| {
                (*Distance, *Terminal, *Existing, *Index)
            })
            .map(|(Index, Existing, _Distance, _Terminal)| (Index, Existing))
            .unwrap();
        let Terminal = Terminals.remove(TerminalIndex);
        let Corner = (Terminal.0, Anchor.1);
        if Anchor != Corner {
            Result.push((Anchor, Corner));
        }
        if Corner != Terminal {
            Result.push((Corner, Terminal));
        }
        Tree.push(Terminal);
        if Corner != Anchor && Corner != Terminal {
            Tree.push(Corner);
        }
        Tree.sort_unstable();
        Tree.dedup();
    }
    Result
}

#[pyfunction]
fn GetRoutingThreadCount() -> usize {
    RoutingThreadPool().current_num_threads()
}

fn NormalizeEdge(First: Position, Second: Position) -> Edge {
    if First <= Second {
        (First, Second)
    } else {
        (Second, First)
    }
}

fn ManhattanDistance(First: Position, Second: Position) -> i32 {
    (First.0 - Second.0).abs() + (First.1 - Second.1).abs() + (First.2 - Second.2).abs()
}

fn PhysicalNeighbors(PositionValue: Position) -> Vec<Position> {
    let (X, Y, Z) = PositionValue;
    let mut Result = vec![(X + 1, Y, Z), (X - 1, Y, Z), (X, Y, Z + 1), (X, Y, Z - 1)];
    for (DeltaX, DeltaZ) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
        Result.push((X + DeltaX, Y + 1, Z + DeltaZ));
        Result.push((X + DeltaX, Y - 1, Z + DeltaZ));
    }
    Result
}

fn BuildPortalCandidate(
    PortalId: String,
    Target: Position,
    Path: Vec<Position>,
) -> PortalCandidate {
    let WireSet: HashSet<_> = Path.iter().copied().collect();
    let SupportSet: HashSet<_> = WireSet
        .iter()
        .map(|Value| (Value.0, Value.1 - 1, Value.2))
        .collect();
    let mut AirSet = HashSet::new();
    let mut BendCount = 0usize;
    let mut ViaCount = 0usize;
    let mut PreviousDirection = None;
    for Values in Path.windows(2) {
        let First = Values[0];
        let Second = Values[1];
        let Direction = (Second.0 - First.0, Second.1 - First.1, Second.2 - First.2);
        if let Some(Previous) = PreviousDirection {
            if Previous != Direction {
                BendCount += 1;
            }
        }
        PreviousDirection = Some(Direction);
        if First.1 != Second.1 {
            ViaCount += 1;
            let Lower = if First.1 < Second.1 { First } else { Second };
            AirSet.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    let ElectricalSet: HashSet<_> = WireSet
        .iter()
        .flat_map(|Value| std::iter::once(*Value).chain(PhysicalNeighbors(*Value)))
        .collect();
    let mut WireClaims: Vec<_> = WireSet.into_iter().collect();
    let mut SupportClaims: Vec<_> = SupportSet.into_iter().collect();
    let mut AirClaims: Vec<_> = AirSet.into_iter().collect();
    let mut ElectricalClaims: Vec<_> = ElectricalSet.into_iter().collect();
    WireClaims.sort_unstable();
    SupportClaims.sort_unstable();
    AirClaims.sort_unstable();
    ElectricalClaims.sort_unstable();
    PortalCandidate {
        PortalId,
        Target,
        Length: Path.len(),
        Path,
        WireClaims,
        SupportClaims,
        AirClaims,
        ElectricalClaims,
        BendCount,
        ViaCount,
    }
}

fn CandidateOrder(Value: &AssignmentCandidate) -> (i32, i32, i32, i32, i32, String) {
    (
        Value.MaterialCost,
        Value.FootprintGrowth,
        Value.Length,
        Value.BendCount,
        Value.ViaCount,
        Value.CandidateId.clone(),
    )
}

fn AssignCandidates(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Remaining: &[String],
    Owned: &ClaimMask,
    BaseBySignal: &BTreeMap<String, ClaimMask>,
    Selected: &mut Vec<(String, String)>,
    ExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    FailureNet: &mut Option<String>,
    ConflictResources: &mut Vec<usize>,
) -> bool {
    let mut Domains: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for Signal in Remaining {
        let Compatible: Vec<_> = Groups[Signal]
            .iter()
            .enumerate()
            .filter(|Value| {
                !Value.1.Claims.Conflicts(Owned)
                    && !BaseBySignal.iter().any(|(BaseSignal, BaseClaims)| {
                        BaseSignal != Signal && Value.1.Claims.Conflicts(BaseClaims)
                    })
            })
            .map(|(Index, _Value)| Index)
            .collect();
        Domains.insert(Signal.clone(), Compatible);
    }
    AssignCandidateDomains(
        Groups,
        &Domains,
        Selected,
        ExpansionCount,
        MaximumExpansionCount,
        FailureNet,
        ConflictResources,
    )
}

fn AssignCandidateDomains(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Domains: &BTreeMap<String, Vec<usize>>,
    Selected: &mut Vec<(String, String)>,
    ExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    FailureNet: &mut Option<String>,
    ConflictResources: &mut Vec<usize>,
) -> bool {
    if Domains.is_empty() {
        return true;
    }
    let Signal = Domains
        .iter()
        .min_by_key(|(Signal, Values)| (Values.len(), *Signal))
        .map(|(Signal, _Values)| Signal.clone())
        .unwrap();
    if Domains[&Signal].is_empty() {
        *FailureNet = Some(Signal);
        return false;
    }
    for CandidateIndex in &Domains[&Signal] {
        let Candidate = &Groups[&Signal][*CandidateIndex];
        *ExpansionCount += 1;
        if *ExpansionCount > MaximumExpansionCount {
            *FailureNet = Some(Signal.clone());
            return false;
        }
        let mut NextDomains = BTreeMap::new();
        let mut Consistent = true;
        for (OtherSignal, OtherDomain) in Domains {
            if OtherSignal == &Signal {
                continue;
            }
            let Compatible: Vec<_> = OtherDomain
                .iter()
                .copied()
                .filter(|OtherIndex| {
                    !Candidate
                        .Claims
                        .Conflicts(&Groups[OtherSignal][*OtherIndex].Claims)
                })
                .collect();
            if Compatible.is_empty() {
                *FailureNet = Some(OtherSignal.clone());
                for OtherIndex in OtherDomain {
                    ConflictResources.extend(
                        Candidate
                            .Claims
                            .ConflictIndices(&Groups[OtherSignal][*OtherIndex].Claims),
                    );
                }
                ConflictResources.sort_unstable();
                ConflictResources.dedup();
                Consistent = false;
                break;
            }
            NextDomains.insert(OtherSignal.clone(), Compatible);
        }
        if !Consistent {
            continue;
        }
        Selected.push((Signal.clone(), Candidate.CandidateId.clone()));
        if AssignCandidateDomains(
            Groups,
            &NextDomains,
            Selected,
            ExpansionCount,
            MaximumExpansionCount,
            FailureNet,
            ConflictResources,
        ) {
            return true;
        }
        Selected.pop();
    }
    false
}

#[derive(Clone, Copy, Eq, PartialEq)]
struct QueueItem {
    EstimatedCost: i32,
    Cost: i32,
    Sequence: usize,
    PositionValue: Position,
    IncomingDirection: Direction,
}

struct PathClaims {
    Wire: Position,
    Support: Position,
    RequiredAir: Option<Position>,
    Parent: Option<Rc<PathClaims>>,
}

impl PathClaims {
    fn ContainsWire(&self, PositionValue: Position) -> bool {
        let mut Current = Some(self);
        while let Some(Claims) = Current {
            if Claims.Wire == PositionValue {
                return true;
            }
            Current = Claims.Parent.as_deref();
        }
        false
    }

    fn ContainsSupport(&self, PositionValue: Position) -> bool {
        let mut Current = Some(self);
        while let Some(Claims) = Current {
            if Claims.Support == PositionValue {
                return true;
            }
            Current = Claims.Parent.as_deref();
        }
        false
    }

    fn ContainsRequiredAir(&self, PositionValue: Position) -> bool {
        let mut Current = Some(self);
        while let Some(Claims) = Current {
            if Claims.RequiredAir == Some(PositionValue) {
                return true;
            }
            Current = Claims.Parent.as_deref();
        }
        false
    }
}

fn ExtendPathClaims(
    Existing: &Rc<PathClaims>,
    Current: Position,
    Neighbor: Position,
) -> Option<Rc<PathClaims>> {
    if Existing.ContainsWire(Neighbor) {
        return None;
    }
    let Support = (Neighbor.0, Neighbor.1 - 1, Neighbor.2);
    if Existing.ContainsSupport(Neighbor)
        || Existing.ContainsRequiredAir(Neighbor)
        || Existing.ContainsWire(Support)
        || Existing.ContainsRequiredAir(Support)
    {
        return None;
    }
    let RequiredAir = if Current.1 != Neighbor.1 {
        let Lower = if Current.1 < Neighbor.1 {
            Current
        } else {
            Neighbor
        };
        let Headroom = (Lower.0, Lower.1 + 1, Lower.2);
        if Existing.ContainsWire(Headroom) || Existing.ContainsSupport(Headroom) {
            return None;
        }
        Some(Headroom)
    } else {
        None
    };
    Some(Rc::new(PathClaims {
        Wire: Neighbor,
        Support,
        RequiredAir,
        Parent: Some(Rc::clone(Existing)),
    }))
}

impl Ord for QueueItem {
    fn cmp(&self, Other: &Self) -> Ordering {
        Other
            .EstimatedCost
            .cmp(&self.EstimatedCost)
            .then_with(|| Other.Cost.cmp(&self.Cost))
            .then_with(|| Other.Sequence.cmp(&self.Sequence))
    }
}

impl PartialOrd for QueueItem {
    fn partial_cmp(&self, Other: &Self) -> Option<Ordering> {
        Some(self.cmp(Other))
    }
}

fn FindPath(
    Adjacency: &HashMap<Position, Vec<Position>>,
    Starts: &[Position],
    Target: Position,
    PreferredRoutingY: i32,
    BlockedNodes: &HashSet<Position>,
    NodeCosts: &HashMap<Position, i32>,
    EdgeCosts: &HashMap<Edge, i32>,
    BendPenalty: i32,
    ViaPenalty: i32,
    ProgressPenalty: i32,
    MaximumExpansionCount: usize,
) -> Option<Vec<Position>> {
    let StartSet: HashSet<Position> = Starts
        .iter()
        .copied()
        .filter(|Value| Adjacency.contains_key(Value))
        .collect();
    if StartSet.is_empty() || !Adjacency.contains_key(&Target) {
        return None;
    }
    if StartSet.contains(&Target) {
        return Some(Vec::new());
    }

    let mut Heap = BinaryHeap::new();
    let mut Costs: HashMap<SearchState, i32> = HashMap::new();
    let mut Previous: HashMap<SearchState, SearchState> = HashMap::new();
    let mut Claims: HashMap<SearchState, Rc<PathClaims>> = HashMap::new();
    let mut Sequence = 0usize;
    let mut Expanded = 0usize;
    let StartDirection = (0, 0, 0);
    for Start in &StartSet {
        let StartState = (*Start, StartDirection);
        Costs.insert(StartState, 0);
        Claims.insert(
            StartState,
            Rc::new(PathClaims {
                Wire: *Start,
                Support: (Start.0, Start.1 - 1, Start.2),
                RequiredAir: None,
                Parent: None,
            }),
        );
        Heap.push(QueueItem {
            EstimatedCost: ManhattanDistance(*Start, Target) * HEURISTIC_WEIGHT,
            Cost: 0,
            Sequence,
            PositionValue: *Start,
            IncomingDirection: StartDirection,
        });
        Sequence += 1;
    }

    while let Some(Item) = Heap.pop() {
        let Current = Item.PositionValue;
        let CurrentState = (Current, Item.IncomingDirection);
        if Costs.get(&CurrentState).copied() != Some(Item.Cost) {
            continue;
        }
        Expanded += 1;
        if Expanded > MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS) {
            return None;
        }
        if Current == Target {
            let mut Branch = Vec::new();
            let mut Cursor = CurrentState;
            while Cursor.1 != StartDirection {
                Branch.push(Cursor.0);
                Cursor = *Previous.get(&Cursor)?;
            }
            Branch.reverse();
            return Some(Branch);
        }

        for Neighbor in Adjacency.get(&Current).into_iter().flatten() {
            if BlockedNodes.contains(Neighbor) && !StartSet.contains(Neighbor) {
                continue;
            }
            if EdgeCosts
                .get(&NormalizeEdge(Current, *Neighbor))
                .copied()
                .unwrap_or(0)
                >= BLOCKED_EDGE_COST
            {
                continue;
            }
            let OutgoingDirection = (
                Neighbor.0 - Current.0,
                Neighbor.1 - Current.1,
                Neighbor.2 - Current.2,
            );
            let Some(NextClaims) = Claims
                .get(&CurrentState)
                .and_then(|Value| ExtendPathClaims(Value, Current, *Neighbor))
            else {
                continue;
            };
            let StepCost = 1 + if Neighbor.1 == Current.1 {
                0
            } else {
                ViaPenalty.max(0)
            };
            let TurnCost = if Item.IncomingDirection == StartDirection
                || Item.IncomingDirection == OutgoingDirection
            {
                0
            } else {
                BendPenalty.max(0)
            };
            let LayerCost = (Neighbor.1 - PreferredRoutingY).abs();
            let ProgressCost =
                if ManhattanDistance(*Neighbor, Target) >= ManhattanDistance(Current, Target) {
                    ProgressPenalty.max(0)
                } else {
                    0
                };
            let NewCost = Item.Cost
                + StepCost
                + TurnCost
                + LayerCost
                + ProgressCost
                + NodeCosts.get(Neighbor).copied().unwrap_or(0)
                + EdgeCosts
                    .get(&NormalizeEdge(Current, *Neighbor))
                    .copied()
                    .unwrap_or(0);
            let NeighborState = (*Neighbor, OutgoingDirection);
            if NewCost >= Costs.get(&NeighborState).copied().unwrap_or(i32::MAX) {
                continue;
            }
            Costs.insert(NeighborState, NewCost);
            Previous.insert(NeighborState, CurrentState);
            Claims.insert(NeighborState, NextClaims);
            Heap.push(QueueItem {
                EstimatedCost: NewCost + ManhattanDistance(*Neighbor, Target) * HEURISTIC_WEIGHT,
                Cost: NewCost,
                Sequence,
                PositionValue: *Neighbor,
                IncomingDirection: OutgoingDirection,
            });
            Sequence += 1;
        }
    }
    None
}

#[pyclass]
struct RoutingContext {
    Adjacency: HashMap<Position, Vec<Position>>,
    NodesByColumn: HashMap<(i32, i32), Vec<Position>>,
}

#[pymethods]
impl RoutingContext {
    #[new]
    fn New(
        _Bounds: (i32, i32, i32, i32, i32, i32),
        _PlacementBounds: (i32, i32, i32, i32),
        NodeValues: Vec<Position>,
        EdgeValues: Vec<Edge>,
    ) -> PyResult<Self> {
        let Nodes: HashSet<Position> = NodeValues.into_iter().collect();
        let mut Adjacency: HashMap<Position, Vec<Position>> = Nodes
            .iter()
            .copied()
            .map(|Value| (Value, Vec::new()))
            .collect();
        for (First, Second) in EdgeValues {
            if !Nodes.contains(&First) || !Nodes.contains(&Second) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "resource graph edge references a missing node",
                ));
            }
            Adjacency.get_mut(&First).unwrap().push(Second);
            Adjacency.get_mut(&Second).unwrap().push(First);
        }
        for Values in Adjacency.values_mut() {
            Values.sort();
            Values.dedup();
        }
        let mut NodesByColumn: HashMap<(i32, i32), Vec<Position>> = HashMap::new();
        for PositionValue in &Nodes {
            NodesByColumn
                .entry((PositionValue.0, PositionValue.2))
                .or_default()
                .push(*PositionValue);
        }
        for Values in NodesByColumn.values_mut() {
            Values.sort_unstable();
        }
        Ok(Self {
            Adjacency,
            NodesByColumn,
        })
    }

    fn GeneratePortalCandidates(
        &self,
        Starts: Vec<Position>,
        PortalTargets: Vec<Position>,
        AllowedNodeValues: Vec<Position>,
        PreferredRoutingY: i32,
        MaximumPortalCount: usize,
        MaximumExpansionCount: usize,
    ) -> Vec<PortalCandidate> {
        let AllowedNodes: HashSet<_> = AllowedNodeValues.into_iter().collect();
        let BlockedNodes: HashSet<_> = AllowedNodes
            .iter()
            .flat_map(|Value| self.Adjacency.get(Value).into_iter().flatten())
            .filter(|Value| !AllowedNodes.contains(Value))
            .copied()
            .collect();
        let mut Targets = PortalTargets;
        Targets.sort_unstable();
        Targets.dedup();
        let mut Candidates = Vec::new();
        for Target in Targets {
            let Some(Path) = FindPath(
                &self.Adjacency,
                &Starts,
                Target,
                PreferredRoutingY,
                &BlockedNodes,
                &HashMap::new(),
                &HashMap::new(),
                1,
                1,
                0,
                MaximumExpansionCount,
            ) else {
                continue;
            };
            let Candidate = BuildPortalCandidate(
                format!("Portal:{},{},{}", Target.0, Target.1, Target.2),
                Target,
                Path,
            );
            let WireSet: HashSet<_> = Candidate.WireClaims.iter().copied().collect();
            let IsDominated = Candidates.iter().any(|Existing: &PortalCandidate| {
                Existing.Length <= Candidate.Length
                    && Existing.BendCount <= Candidate.BendCount
                    && Existing.ViaCount <= Candidate.ViaCount
                    && Existing
                        .WireClaims
                        .iter()
                        .all(|Value| WireSet.contains(Value))
            });
            if !IsDominated {
                Candidates.push(Candidate);
            }
        }
        Candidates.sort_by_key(|Value| {
            (
                Value.Length,
                Value.BendCount,
                Value.ViaCount,
                Value.Target,
                Value.PortalId.clone(),
            )
        });
        Candidates.truncate(MaximumPortalCount.clamp(1, 64));
        Candidates
    }

    fn GenerateRouteTree(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        MaximumExpansionCount: usize,
    ) -> Option<Vec<Position>> {
        let AllowedNodes: HashSet<_> = AllowedNodeValues.into_iter().collect();
        let BlockedNodes: HashSet<_> = AllowedNodes
            .iter()
            .flat_map(|Value| self.Adjacency.get(Value).into_iter().flatten())
            .filter(|Value| !AllowedNodes.contains(Value))
            .copied()
            .collect();
        let NodeCosts: HashMap<_, _> = if GuidePenalty <= 0 || PreferredColumns.is_empty() {
            HashMap::new()
        } else {
            AllowedNodes
                .iter()
                .map(|Value| {
                    let Distance = PreferredColumns
                        .iter()
                        .map(|Column| (Value.0 - Column.0).abs() + (Value.2 - Column.1).abs())
                        .min()
                        .unwrap_or(0);
                    (*Value, Distance * GuidePenalty)
                })
                .collect()
        };
        let mut Tree: HashSet<_> = Starts
            .into_iter()
            .filter(|Value| AllowedNodes.contains(Value))
            .collect();
        if Tree.is_empty() {
            return None;
        }
        let mut RemainingBranches = TargetBranches;
        while !RemainingBranches.is_empty() {
            let SelectedIndex = RemainingBranches
                .iter()
                .enumerate()
                .min_by_key(|(_, Branch)| {
                    let Target = Branch
                        .last()
                        .copied()
                        .unwrap_or((i32::MAX, i32::MAX, i32::MAX));
                    let Distance = Tree
                        .iter()
                        .map(|Start| ManhattanDistance(*Start, Target))
                        .min()
                        .unwrap_or(i32::MAX);
                    (Distance, Target, Branch.len())
                })
                .map(|(Index, _)| Index)?;
            let Branch = RemainingBranches.remove(SelectedIndex);
            let Target = *Branch.last()?;
            if Tree.contains(&Target) {
                Tree.extend(Branch);
                continue;
            }
            let mut TreeStarts: Vec<_> = Tree.iter().copied().collect();
            TreeStarts.sort_unstable();
            let ExistingSupport: HashSet<_> = Tree
                .iter()
                .map(|Value| (Value.0, Value.1 - 1, Value.2))
                .collect();
            let mut ExistingAir = HashSet::new();
            for First in &Tree {
                for Second in self.Adjacency.get(First).into_iter().flatten() {
                    if !Tree.contains(Second) || Second <= First || First.1 == Second.1 {
                        continue;
                    }
                    let Lower = if First.1 < Second.1 { *First } else { *Second };
                    ExistingAir.insert((Lower.0, Lower.1 + 1, Lower.2));
                }
            }
            let BranchNodes: HashSet<_> = Branch.iter().copied().collect();
            let BranchSupport: HashSet<_> = BranchNodes
                .iter()
                .map(|Value| (Value.0, Value.1 - 1, Value.2))
                .collect();
            let mut BranchAir = HashSet::new();
            for Values in Branch.windows(2) {
                if Values[0].1 != Values[1].1 {
                    let Lower = if Values[0].1 < Values[1].1 {
                        Values[0]
                    } else {
                        Values[1]
                    };
                    BranchAir.insert((Lower.0, Lower.1 + 1, Lower.2));
                }
            }
            let mut DynamicBlocked = BlockedNodes.clone();
            for Node in &AllowedNodes {
                let Support = (Node.0, Node.1 - 1, Node.2);
                if ExistingSupport.contains(Node)
                    || ExistingAir.contains(Node)
                    || BranchSupport.contains(Node)
                    || BranchAir.contains(Node)
                    || Tree.contains(&Support)
                    || ExistingAir.contains(&Support)
                    || BranchNodes.contains(&Support)
                    || BranchAir.contains(&Support)
                {
                    DynamicBlocked.insert(*Node);
                }
            }
            DynamicBlocked.remove(&Target);
            let mut DynamicEdgeCosts = HashMap::new();
            for First in &AllowedNodes {
                for Second in self.Adjacency.get(First).into_iter().flatten() {
                    if !AllowedNodes.contains(Second) || Second <= First || First.1 == Second.1 {
                        continue;
                    }
                    let Lower = if First.1 < Second.1 { *First } else { *Second };
                    let Headroom = (Lower.0, Lower.1 + 1, Lower.2);
                    if Tree.contains(&Headroom)
                        || ExistingSupport.contains(&Headroom)
                        || BranchNodes.contains(&Headroom)
                        || BranchSupport.contains(&Headroom)
                    {
                        DynamicEdgeCosts.insert(NormalizeEdge(*First, *Second), BLOCKED_EDGE_COST);
                    }
                }
            }
            let Path = FindPath(
                &self.Adjacency,
                &TreeStarts,
                Target,
                PreferredRoutingY,
                &DynamicBlocked,
                &NodeCosts,
                &DynamicEdgeCosts,
                BendPenalty,
                ViaPenalty,
                0,
                MaximumExpansionCount,
            )?;
            Tree.extend(Path);
            Tree.extend(Branch);
        }
        let mut Result: Vec<_> = Tree.into_iter().collect();
        Result.sort_unstable();
        Some(Result)
    }

    #[allow(clippy::type_complexity)]
    fn GenerateRouteTrees(
        &self,
        PythonValue: Python<'_>,
        Requests: Vec<(
            Vec<Position>,
            Vec<Vec<Position>>,
            Vec<(i32, i32)>,
            Vec<Position>,
            Vec<(i32, i32)>,
            i32,
            i32,
            i32,
            i32,
            usize,
        )>,
    ) -> Vec<Option<Vec<Position>>> {
        PythonValue.allow_threads(|| {
            RoutingThreadPool().install(|| {
                Requests
                    .into_par_iter()
                    .map(
                        |(
                            Starts,
                            TargetBranches,
                            AllowedColumns,
                            RequiredNodes,
                            PreferredColumns,
                            PreferredRoutingY,
                            GuidePenalty,
                            BendPenalty,
                            ViaPenalty,
                            MaximumExpansionCount,
                        )| {
                            let mut AllowedNodes = HashSet::new();
                            for Column in AllowedColumns {
                                if let Some(Values) = self.NodesByColumn.get(&Column) {
                                    AllowedNodes.extend(Values.iter().copied());
                                }
                            }
                            AllowedNodes.extend(RequiredNodes);
                            self.GenerateRouteTree(
                                Starts,
                                TargetBranches,
                                AllowedNodes.into_iter().collect(),
                                PreferredColumns,
                                PreferredRoutingY,
                                GuidePenalty,
                                BendPenalty,
                                ViaPenalty,
                                MaximumExpansionCount,
                            )
                        },
                    )
                    .collect()
            })
        })
    }

    #[allow(clippy::type_complexity)]
    fn PlanAuthoritativeRoutes(
        &self,
        CandidateValues: Vec<(
            String,
            String,
            Vec<usize>,
            Vec<usize>,
            Vec<usize>,
            Vec<usize>,
            i32,
            i32,
            i32,
            i32,
            i32,
        )>,
        ResourceCount: usize,
        MaximumExpansionCount: usize,
    ) -> PyResult<RoutingAssignmentResult> {
        if ResourceCount == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "resource count must be positive",
            ));
        }
        let mut Groups: BTreeMap<String, Vec<AssignmentCandidate>> = BTreeMap::new();
        for (
            Signal,
            CandidateId,
            Wire,
            Support,
            Air,
            Electrical,
            MaterialCost,
            FootprintGrowth,
            Length,
            BendCount,
            ViaCount,
        ) in CandidateValues
        {
            let Some(Claims) =
                ClaimMask::FromIndices(ResourceCount, &Wire, &Support, &Air, &Electrical)
            else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "candidate references a resource outside the indexed graph",
                ));
            };
            Groups
                .entry(Signal.clone())
                .or_default()
                .push(AssignmentCandidate {
                    CandidateId,
                    Claims,
                    MaterialCost,
                    FootprintGrowth,
                    Length,
                    BendCount,
                    ViaCount,
                });
        }
        if Groups.values().any(Vec::is_empty) {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "every signal requires at least one route candidate",
            ));
        }
        for Values in Groups.values_mut() {
            Values.sort_by_key(|Value| CandidateOrder(Value));
        }
        let Remaining: Vec<_> = Groups.keys().cloned().collect();
        let mut Selected = Vec::new();
        let mut ExpansionCount = 0usize;
        let mut FailureNet = None;
        let mut ConflictResources = Vec::new();
        let Success = AssignCandidates(
            &Groups,
            &Remaining,
            &ClaimMask::New(ResourceCount),
            &BTreeMap::new(),
            &mut Selected,
            &mut ExpansionCount,
            MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS),
            &mut FailureNet,
            &mut ConflictResources,
        );
        Selected.sort();
        Ok(RoutingAssignmentResult {
            Success,
            SelectedCandidateIds: if Success { Selected } else { Vec::new() },
            ExpansionCount,
            FailureNet,
            ConflictResourceIndices: ConflictResources,
        })
    }

    #[allow(clippy::type_complexity)]
    fn PlanAuthoritativeRoutesWithBase(
        &self,
        CandidateValues: Vec<(
            String,
            String,
            Vec<usize>,
            Vec<usize>,
            Vec<usize>,
            Vec<usize>,
            i32,
            i32,
            i32,
            i32,
            i32,
        )>,
        BaseValues: Vec<(String, Vec<usize>, Vec<usize>, Vec<usize>, Vec<usize>)>,
        ResourceCount: usize,
        MaximumExpansionCount: usize,
    ) -> PyResult<RoutingAssignmentResult> {
        if ResourceCount == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "resource count must be positive",
            ));
        }
        let mut BaseBySignal: BTreeMap<String, ClaimMask> = BTreeMap::new();
        for (Signal, Wire, Support, Air, Electrical) in BaseValues {
            let Some(Claims) =
                ClaimMask::FromIndices(ResourceCount, &Wire, &Support, &Air, &Electrical)
            else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "base ownership references a resource outside the indexed graph",
                ));
            };
            if let Some(Existing) = BaseBySignal.get_mut(&Signal) {
                Existing.UnionWith(&Claims);
            } else {
                BaseBySignal.insert(Signal, Claims);
            }
        }
        let BaseSignals: Vec<_> = BaseBySignal.keys().cloned().collect();
        for (Index, Signal) in BaseSignals.iter().enumerate() {
            let Claims = &BaseBySignal[Signal];
            for OtherSignal in BaseSignals.iter().skip(Index + 1) {
                if Claims.Conflicts(&BaseBySignal[OtherSignal]) {
                    return Ok(RoutingAssignmentResult {
                        Success: false,
                        SelectedCandidateIds: Vec::new(),
                        ExpansionCount: 0,
                        FailureNet: Some(Signal.clone()),
                        ConflictResourceIndices: Claims.ConflictIndices(&BaseBySignal[OtherSignal]),
                    });
                }
            }
        }

        let mut Groups: BTreeMap<String, Vec<AssignmentCandidate>> = BTreeMap::new();
        for (
            Signal,
            CandidateId,
            Wire,
            Support,
            Air,
            Electrical,
            MaterialCost,
            FootprintGrowth,
            Length,
            BendCount,
            ViaCount,
        ) in CandidateValues
        {
            let Some(Claims) =
                ClaimMask::FromIndices(ResourceCount, &Wire, &Support, &Air, &Electrical)
            else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "candidate references a resource outside the indexed graph",
                ));
            };
            Groups.entry(Signal).or_default().push(AssignmentCandidate {
                CandidateId,
                Claims,
                MaterialCost,
                FootprintGrowth,
                Length,
                BendCount,
                ViaCount,
            });
        }
        for Values in Groups.values_mut() {
            Values.sort_by_key(|Value| CandidateOrder(Value));
        }
        let Remaining: Vec<_> = Groups.keys().cloned().collect();
        let mut Selected = Vec::new();
        let mut ExpansionCount = 0usize;
        let mut FailureNet = None;
        let mut ConflictResources = Vec::new();
        let Success = AssignCandidates(
            &Groups,
            &Remaining,
            &ClaimMask::New(ResourceCount),
            &BaseBySignal,
            &mut Selected,
            &mut ExpansionCount,
            MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS),
            &mut FailureNet,
            &mut ConflictResources,
        );
        Selected.sort();
        Ok(RoutingAssignmentResult {
            Success,
            SelectedCandidateIds: if Success { Selected } else { Vec::new() },
            ExpansionCount,
            FailureNet,
            ConflictResourceIndices: ConflictResources,
        })
    }

    fn FindPathOnResourceGraph(
        &self,
        Starts: Vec<Position>,
        Target: Position,
        PreferredRoutingY: i32,
        BlockedNodeValues: Vec<Position>,
        NodeCostValues: Vec<(Position, i32)>,
        EdgeCostValues: Vec<(Edge, i32)>,
        BendPenalty: i32,
        ViaPenalty: i32,
        ProgressPenalty: i32,
        MaximumExpansionCount: usize,
    ) -> Option<Vec<Position>> {
        FindPath(
            &self.Adjacency,
            &Starts,
            Target,
            PreferredRoutingY,
            &BlockedNodeValues.into_iter().collect(),
            &NodeCostValues.into_iter().collect(),
            &EdgeCostValues.into_iter().collect(),
            BendPenalty,
            ViaPenalty,
            ProgressPenalty,
            MaximumExpansionCount,
        )
    }

    fn FindPathsOnResourceGraph(
        &self,
        PythonValue: Python<'_>,
        Starts: Vec<Position>,
        Targets: Vec<Position>,
        PreferredRoutingY: i32,
        BlockedNodeValues: Vec<Position>,
        NodeCostValues: Vec<(Position, i32)>,
        EdgeCostValues: Vec<(Edge, i32)>,
        BendPenalty: i32,
        ViaPenalty: i32,
        ProgressPenalty: i32,
        MaximumExpansionCount: usize,
    ) -> Vec<Option<Vec<Position>>> {
        let BlockedNodes: HashSet<Position> = BlockedNodeValues.into_iter().collect();
        let NodeCosts: HashMap<Position, i32> = NodeCostValues.into_iter().collect();
        let EdgeCosts: HashMap<Edge, i32> = EdgeCostValues.into_iter().collect();
        PythonValue.allow_threads(|| {
            RoutingThreadPool().install(|| {
                Targets
                    .par_iter()
                    .map(|Target| {
                        FindPath(
                            &self.Adjacency,
                            &Starts,
                            *Target,
                            PreferredRoutingY,
                            &BlockedNodes,
                            &NodeCosts,
                            &EdgeCosts,
                            BendPenalty,
                            ViaPenalty,
                            ProgressPenalty,
                            MaximumExpansionCount,
                        )
                    })
                    .collect()
            })
        })
    }

    fn NodeCount(&self) -> usize {
        self.Adjacency.len()
    }

    fn EdgeCount(&self) -> usize {
        self.Adjacency.values().map(Vec::len).sum::<usize>() / 2
    }
}

#[pymodule]
fn RustRouting(Module: &Bound<'_, PyModule>) -> PyResult<()> {
    Module.add_class::<RoutingContext>()?;
    Module.add_class::<PortalCandidate>()?;
    Module.add_class::<RoutingAssignmentResult>()?;
    Module.add_function(wrap_pyfunction!(GetRoutingThreadCount, Module)?)?;
    Module.add_function(wrap_pyfunction!(GenerateRectilinearTopology, Module)?)?;
    Ok(())
}

#[cfg(test)]
mod Tests {
    use super::*;

    #[test]
    fn GraphTraversalCannotUseUnlistedTransition() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (2, 0, 0);
        let Adjacency = HashMap::from([(A, vec![B]), (B, vec![A]), (C, vec![])]);
        let Result = FindPath(
            &Adjacency,
            &[A],
            C,
            0,
            &HashSet::new(),
            &HashMap::new(),
            &HashMap::new(),
            0,
            0,
            0,
            100,
        );
        assert!(Result.is_none());
    }

    #[test]
    fn ClaimMasksDetectRedstoneCrossCategoryConflicts() {
        let Wire = ClaimMask::FromIndices(8, &[2], &[], &[], &[2, 3]).unwrap();
        let Neighbor = ClaimMask::FromIndices(8, &[3], &[], &[], &[2, 3]).unwrap();
        let Isolated = ClaimMask::FromIndices(8, &[6], &[], &[], &[5, 6, 7]).unwrap();
        assert!(Wire.Conflicts(&Neighbor));
        assert!(!Wire.Conflicts(&Isolated));
    }

    #[test]
    fn MrvAssignmentSelectsAZeroConflictAlternative() {
        let Candidate = |Id: &str, Wire: usize, Electrical: &[usize]| AssignmentCandidate {
            CandidateId: Id.to_string(),
            Claims: ClaimMask::FromIndices(16, &[Wire], &[], &[], Electrical).unwrap(),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([
            ("A".to_string(), vec![Candidate("A0", 2, &[1, 2, 3])]),
            (
                "B".to_string(),
                vec![
                    Candidate("B0", 3, &[2, 3, 4]),
                    Candidate("B1", 8, &[7, 8, 9]),
                ],
            ),
        ]);
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut Failure = None;
        let mut Conflicts = Vec::new();
        assert!(AssignCandidates(
            &Groups,
            &["A".to_string(), "B".to_string()],
            &ClaimMask::New(16),
            &BTreeMap::new(),
            &mut Selected,
            &mut Expansions,
            16,
            &mut Failure,
            &mut Conflicts,
        ));
        assert!(Selected.contains(&("B".to_string(), "B1".to_string())));
    }

    #[test]
    fn AssignmentBudgetHardFails() {
        let Candidate = AssignmentCandidate {
            CandidateId: "A0".to_string(),
            Claims: ClaimMask::FromIndices(4, &[0], &[], &[], &[0]).unwrap(),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([("A".to_string(), vec![Candidate])]);
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut Failure = None;
        let mut Conflicts = Vec::new();
        assert!(!AssignCandidates(
            &Groups,
            &["A".to_string()],
            &ClaimMask::New(4),
            &BTreeMap::new(),
            &mut Selected,
            &mut Expansions,
            0,
            &mut Failure,
            &mut Conflicts,
        ));
        assert_eq!(Failure, Some("A".to_string()));
    }

    #[test]
    fn AssignmentRespectsPreOwnedBaseClaims() {
        let Candidate = |Id: &str, Wire: usize, Electrical: &[usize]| AssignmentCandidate {
            CandidateId: Id.to_string(),
            Claims: ClaimMask::FromIndices(16, &[Wire], &[], &[], Electrical).unwrap(),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([(
            "Extension".to_string(),
            vec![
                Candidate("blocked", 3, &[2, 3, 4]),
                Candidate("clear", 10, &[9, 10, 11]),
            ],
        )]);
        let Base = ClaimMask::FromIndices(16, &[2], &[], &[], &[1, 2, 3]).unwrap();
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut Failure = None;
        let mut Conflicts = Vec::new();
        assert!(AssignCandidates(
            &Groups,
            &["Extension".to_string()],
            &ClaimMask::New(16),
            &BTreeMap::from([("Base".to_string(), Base)]),
            &mut Selected,
            &mut Expansions,
            16,
            &mut Failure,
            &mut Conflicts,
        ));
        assert_eq!(
            Selected,
            vec![("Extension".to_string(), "clear".to_string())]
        );
    }

    #[test]
    fn AssignmentMergesSameSignalBaseClaims() {
        let Candidate = AssignmentCandidate {
            CandidateId: "extension".to_string(),
            Claims: ClaimMask::FromIndices(16, &[3], &[], &[], &[2, 3, 4]).unwrap(),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([("Signal".to_string(), vec![Candidate])]);
        let Base = ClaimMask::FromIndices(16, &[2], &[], &[], &[1, 2, 3]).unwrap();
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut Failure = None;
        let mut Conflicts = Vec::new();
        assert!(AssignCandidates(
            &Groups,
            &["Signal".to_string()],
            &ClaimMask::New(16),
            &BTreeMap::from([("Signal".to_string(), Base)]),
            &mut Selected,
            &mut Expansions,
            16,
            &mut Failure,
            &mut Conflicts,
        ));
        assert_eq!(
            Selected,
            vec![("Signal".to_string(), "extension".to_string())]
        );
    }

    #[test]
    fn RectilinearTopologyIsDeterministicAndAxisAligned() {
        let First = GenerateRectilinearTopology(vec![(4, 4), (0, 0), (4, 0)]);
        let Second = GenerateRectilinearTopology(vec![(4, 0), (4, 4), (0, 0)]);
        assert_eq!(First, Second);
        assert!(First.iter().all(|(A, B)| A.0 == B.0 || A.1 == B.1));
    }
}

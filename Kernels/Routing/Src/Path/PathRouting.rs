//! Deadline-aware physical resource-graph path search.

use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{Direction, Edge, PortalCandidate, Position, SearchState};
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::rc::Rc;

const MAXIMUM_EXPANSIONS: usize = 2_000_000;
const HEURISTIC_WEIGHT: i32 = 4;
pub(crate) const MAXIMUM_UNREFRESHED_DUST_LENGTH: u8 = 15;
const REPEATER_SEARCH_PENALTY: i32 = 24;
// Retain the three-unit value for bounded local repair radii.  The complete
// path search itself must consider an earlier legal refresh because a turny
// suffix can have no repeater site inside this final window.
pub(crate) const REPEATER_TURN_HEADROOM: u8 = 3;
pub(crate) const BLOCKED_EDGE_COST: i32 = 1_000_000_000;

pub(crate) fn NormalizeEdge(First: Position, Second: Position) -> Edge {
    if First <= Second {
        (First, Second)
    } else {
        (Second, First)
    }
}

pub(crate) fn ManhattanDistance(First: Position, Second: Position) -> i32 {
    (First.0 - Second.0).abs() + (First.1 - Second.1).abs() + (First.2 - Second.2).abs()
}

fn RepeaterCostLowerBound(PositionValue: Position, Target: Position, RemainingStrength: u8) -> i32 {
    let Distance = ManhattanDistance(PositionValue, Target);
    let ReachWithoutRefresh = i32::from(RemainingStrength.saturating_sub(1));
    let UncoveredDistance = (Distance - ReachWithoutRefresh).max(0);
    let RefreshReach = i32::from(MAXIMUM_UNREFRESHED_DUST_LENGTH - 1);
    let RequiredRefreshCount = (UncoveredDistance + RefreshReach - 1) / RefreshReach;
    RequiredRefreshCount * REPEATER_SEARCH_PENALTY
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

pub(crate) fn BuildPortalCandidate(
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

#[derive(Clone, Copy, Eq, PartialEq)]
struct QueueItem {
    EstimatedCost: i32,
    Cost: i32,
    Sequence: usize,
    PositionValue: Position,
    IncomingDirection: Direction,
    RemainingStrength: u8,
}

struct PathClaims {
    Wire: Position,
    Support: Position,
    RequiredAir: Option<Position>,
    Parent: Option<Rc<PathClaims>>,
}

#[allow(dead_code)]
pub(crate) struct PathSearchResult {
    pub(crate) Status: String,
    pub(crate) NoPathReason: String,
    pub(crate) Path: Vec<Position>,
    pub(crate) RepeaterReservations: Vec<(Position, String)>,
    pub(crate) ExpansionCount: usize,
    pub(crate) RepeaterConstraintFailures: usize,
    pub(crate) StatePath: Vec<SearchState>,
    pub(crate) RepeaterRejectedCount: usize,
    pub(crate) IsRouted: bool,
    pub(crate) IsBudgetExpired: bool,
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

#[allow(clippy::too_many_arguments)]
pub(crate) fn FindPathDetailedWithDeadline(
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
    EnforceSignalStrength: bool,
    Deadline: &RuntimeDeadline,
) -> Option<PathSearchResult> {
    let Failure = |Status: &str,
                   NoPathReason: &str,
                   RepeaterRejectedCount: usize,
                   RepeaterConstraintFailures: usize,
                   ExpansionCount: usize|
     -> PathSearchResult {
        PathSearchResult {
            Status: Status.to_string(),
            NoPathReason: NoPathReason.to_string(),
            Path: Vec::new(),
            RepeaterConstraintFailures,
            RepeaterReservations: Vec::new(),
            ExpansionCount,
            StatePath: Vec::new(),
            RepeaterRejectedCount,
            IsRouted: false,
            IsBudgetExpired: Status == "BudgetExpired",
        }
    };
    if Deadline.Check() {
        return Some(Failure("BudgetExpired", "BudgetExpired", 0, 0, 0));
    }
    let StartDirection = (0, 0, 0);
    let StartStates: Vec<_> = Starts
        .iter()
        .copied()
        .map(|Start| (Start, StartDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH))
        .collect();
    FindPathFromStatesDetailedWithDeadline(
        Adjacency,
        None,
        None,
        &StartStates,
        Target,
        PreferredRoutingY,
        BlockedNodes,
        NodeCosts,
        &HashMap::new(),
        &HashMap::new(),
        EdgeCosts,
        BendPenalty,
        ViaPenalty,
        ProgressPenalty,
        MaximumExpansionCount,
        EnforceSignalStrength,
        &HashSet::new(),
        &[],
        0,
        Deadline,
    )
}

fn BuildTargetContinuationRepeaterReservations(
    StartState: SearchState,
    Continuation: &[Position],
    EnforceSignalStrength: bool,
    ForbiddenRepeaterPositions: &HashSet<Position>,
) -> Option<Vec<(Position, String)>> {
    if Continuation.is_empty() {
        return Some(Vec::new());
    }
    if Continuation[0] != StartState.0 {
        return None;
    }
    let StartDirection = (0, 0, 0);
    let mut CurrentState = StartState;
    let mut RepeaterReservations = Vec::new();
    for Next in Continuation.iter().skip(1) {
        let Direction = (
            Next.0 - CurrentState.0 .0,
            Next.1 - CurrentState.0 .1,
            Next.2 - CurrentState.0 .2,
        );
        let CanPlaceRepeater = CurrentState.1 != StartDirection
            && CurrentState.1 == Direction
            && Direction.1 == 0
            && CurrentState.0 .1 == Next.1
            && CurrentState.2 < MAXIMUM_UNREFRESHED_DUST_LENGTH - 1
            && !ForbiddenRepeaterPositions.contains(&CurrentState.0);
        let RemainingStrength = if !EnforceSignalStrength {
            MAXIMUM_UNREFRESHED_DUST_LENGTH
        } else if CanPlaceRepeater {
            let Facing = match (Direction.0, Direction.2) {
                (1, 0) => "west",
                (-1, 0) => "east",
                (0, 1) => "north",
                (0, -1) => "south",
                _ => return None,
            };
            RepeaterReservations.push((CurrentState.0, Facing.to_string()));
            MAXIMUM_UNREFRESHED_DUST_LENGTH - 1
        } else if CurrentState.2 > 1 {
            CurrentState.2 - 1
        } else {
            return None;
        };
        CurrentState = (*Next, Direction, RemainingStrength);
    }
    Some(RepeaterReservations)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn FindPathFromStatesDetailedWithDeadline(
    Adjacency: &HashMap<Position, Vec<Position>>,
    AllowedColumns: Option<&HashSet<(i32, i32)>>,
    AdditionalAllowedNodes: Option<&HashSet<Position>>,
    StartStates: &[SearchState],
    Target: Position,
    PreferredRoutingY: i32,
    BlockedNodes: &HashSet<Position>,
    NodeCosts: &HashMap<Position, i32>,
    AdditionalNodeCosts: &HashMap<Position, i32>,
    ColumnCosts: &HashMap<(i32, i32), i32>,
    EdgeCosts: &HashMap<Edge, i32>,
    BendPenalty: i32,
    ViaPenalty: i32,
    ProgressPenalty: i32,
    MaximumExpansionCount: usize,
    EnforceSignalStrength: bool,
    ForbiddenRepeaterPositions: &HashSet<Position>,
    TargetContinuation: &[Position],
    RejectedCountHint: usize,
    Deadline: &RuntimeDeadline,
) -> Option<PathSearchResult> {
    let Failure = |Status: &str,
                   NoPathReason: &str,
                   RepeaterRejectedCount: usize,
                   RepeaterConstraintFailures: usize,
                   ExpansionCount: usize|
     -> PathSearchResult {
        PathSearchResult {
            Status: Status.to_string(),
            NoPathReason: NoPathReason.to_string(),
            Path: Vec::new(),
            RepeaterConstraintFailures,
            RepeaterReservations: Vec::new(),
            ExpansionCount,
            StatePath: Vec::new(),
            RepeaterRejectedCount,
            IsRouted: false,
            IsBudgetExpired: Status == "BudgetExpired",
        }
    };
    let mut RepeaterRejectedCount = RejectedCountHint;
    let mut RepeaterConstraintFailures = 0usize;
    if Deadline.Check() {
        return Some(Failure(
            "BudgetExpired",
            "BudgetExpired",
            RepeaterRejectedCount,
            RepeaterConstraintFailures,
            0,
        ));
    }
    let StartStates: Vec<_> = StartStates
        .iter()
        .copied()
        .filter(|Value| Adjacency.contains_key(&Value.0))
        .collect();
    let StartStateSet: HashSet<SearchState> = StartStates.iter().copied().collect();
    if StartStates.is_empty() || !Adjacency.contains_key(&Target) {
        return Some(Failure(
            "NoPath",
            "NoPathGeometry",
            RepeaterRejectedCount,
            RepeaterConstraintFailures,
            0,
        ));
    }
    if let Some(StartState) = StartStates
        .iter()
        .filter(|Value| Value.0 == Target)
        .min()
        .copied()
    {
        if let Some(RepeaterReservations) = BuildTargetContinuationRepeaterReservations(
            StartState,
            TargetContinuation,
            EnforceSignalStrength,
            ForbiddenRepeaterPositions,
        ) {
            return Some(PathSearchResult {
                Status: "Routed".to_string(),
                NoPathReason: String::new(),
                Path: vec![Target],
                RepeaterConstraintFailures,
                RepeaterReservations,
                ExpansionCount: 0,
                StatePath: vec![StartState],
                RepeaterRejectedCount,
                IsRouted: true,
                IsBudgetExpired: false,
            });
        }
    }
    let mut LastNoPathReason = "NoPathGeometry";
    let RequiresStrengthState = EnforceSignalStrength;

    let mut Heap = BinaryHeap::new();
    let mut Costs: HashMap<SearchState, i32> = HashMap::new();
    let mut Previous: HashMap<SearchState, SearchState> = HashMap::new();
    let mut Claims: HashMap<SearchState, Rc<PathClaims>> = HashMap::new();
    let mut Sequence = 0usize;
    let mut Expanded = 0usize;
    let StartDirection = (0, 0, 0);
    for StartState in &StartStates {
        let Start = StartState.0;
        Costs.insert(*StartState, 0);
        Claims.insert(
            *StartState,
            Rc::new(PathClaims {
                Wire: Start,
                Support: (Start.0, Start.1 - 1, Start.2),
                RequiredAir: None,
                Parent: None,
            }),
        );
        Heap.push(QueueItem {
            EstimatedCost: ManhattanDistance(Start, Target) * HEURISTIC_WEIGHT
                + RepeaterCostLowerBound(Start, Target, StartState.2),
            Cost: 0,
            Sequence,
            PositionValue: Start,
            IncomingDirection: StartState.1,
            RemainingStrength: StartState.2,
        });
        Sequence += 1;
    }

    while let Some(Item) = Heap.pop() {
        if Expanded % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Some(Failure(
                "BudgetExpired",
                "BudgetExpired",
                RepeaterRejectedCount,
                RepeaterConstraintFailures,
                Expanded,
            ));
        }
        let Current = Item.PositionValue;
        let CurrentState = (Current, Item.IncomingDirection, Item.RemainingStrength);
        if Costs.get(&CurrentState).copied() != Some(Item.Cost) {
            continue;
        }
        Expanded += 1;
        if Expanded >= MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS) {
            return Some(Failure(
                "NoPath",
                "SearchLimitReached",
                RepeaterRejectedCount,
                RepeaterConstraintFailures,
                Expanded,
            ));
        }
        if Current == Target {
            let Some(TargetContinuationRepeaters) = BuildTargetContinuationRepeaterReservations(
                CurrentState,
                TargetContinuation,
                EnforceSignalStrength,
                ForbiddenRepeaterPositions,
            ) else {
                LastNoPathReason = "NoPathContinuation";
                continue;
            };
            let mut States = vec![CurrentState];
            let mut Cursor = CurrentState;
            while !StartStateSet.contains(&Cursor) {
                Cursor = *Previous.get(&Cursor)?;
                States.push(Cursor);
            }
            States.reverse();
            let Path = if States.len() == 1 {
                vec![Target]
            } else {
                States.iter().skip(1).map(|State| State.0).collect()
            };
            let mut RepeaterReservations = Vec::new();
            for Values in States.windows(2) {
                if Values[1].2 <= Values[0].2 {
                    continue;
                }
                let PositionValue = Values[0].0;
                let Next = Values[1].0;
                let Facing = match (Next.0 - PositionValue.0, Next.2 - PositionValue.2) {
                    (1, 0) => "west",
                    (-1, 0) => "east",
                    (0, 1) => "north",
                    (0, -1) => "south",
                    _ => {
                        return Some(Failure(
                            "NoPath",
                            "NoPathGeometry",
                            RepeaterRejectedCount,
                            RepeaterConstraintFailures,
                            Expanded,
                        ));
                    }
                };
                RepeaterReservations.push((PositionValue, Facing.to_string()));
            }
            RepeaterReservations.extend(TargetContinuationRepeaters);
            RepeaterReservations.sort_unstable();
            RepeaterReservations.dedup();
            return Some(PathSearchResult {
                Status: "Routed".to_string(),
                NoPathReason: String::new(),
                Path,
                RepeaterConstraintFailures,
                RepeaterReservations,
                ExpansionCount: Expanded,
                StatePath: States,
                RepeaterRejectedCount,
                IsRouted: true,
                IsBudgetExpired: false,
            });
        }

        for Neighbor in Adjacency.get(&Current).into_iter().flatten() {
            if AllowedColumns.is_some_and(|Columns| {
                !Columns.contains(&(Neighbor.0, Neighbor.2))
                    && !AdditionalAllowedNodes.is_some_and(|Values| Values.contains(Neighbor))
            }) {
                continue;
            }
            // A blocked start is a legal launch state, but it is not a legal
            // interior node for a path launched from another start.  Entering
            // it would replace its authoritative incoming direction and can
            // orient a repeater against the physical path that powers it.
            if BlockedNodes.contains(Neighbor) {
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
            let BaseCost = Item.Cost
                + StepCost
                + TurnCost
                + LayerCost
                + ProgressCost
                + NodeCosts.get(Neighbor).copied().unwrap_or(0)
                + AdditionalNodeCosts.get(Neighbor).copied().unwrap_or(0)
                + ColumnCosts
                    .get(&(Neighbor.0, Neighbor.2))
                    .copied()
                    .unwrap_or(0)
                + EdgeCosts
                    .get(&NormalizeEdge(Current, *Neighbor))
                    .copied()
                    .unwrap_or(0);
            let CanPlaceRepeater = Item.IncomingDirection != StartDirection
                && Item.IncomingDirection == OutgoingDirection
                && Item.IncomingDirection.1 == 0
                && Current.1 == Neighbor.1
                && !ForbiddenRepeaterPositions.contains(&Current)
                && Item.RemainingStrength < MAXIMUM_UNREFRESHED_DUST_LENGTH - 1;
            let mut StrengthOptions = Vec::with_capacity(2);
            if !RequiresStrengthState {
                StrengthOptions.push((MAXIMUM_UNREFRESHED_DUST_LENGTH, 0));
            } else {
                if Item.RemainingStrength > 1 {
                    StrengthOptions.push((Item.RemainingStrength - 1, 0));
                }
                if CanPlaceRepeater {
                    StrengthOptions
                        .push((MAXIMUM_UNREFRESHED_DUST_LENGTH - 1, REPEATER_SEARCH_PENALTY));
                }
                if StrengthOptions.is_empty() {
                    RepeaterConstraintFailures += 1;
                    RepeaterRejectedCount += 1;
                }
            }
            for (RemainingStrength, RepeaterCost) in StrengthOptions {
                let NewCost = BaseCost + RepeaterCost;
                let NeighborState = (*Neighbor, OutgoingDirection, RemainingStrength);
                // Below the freshly repeated strength, a no-more-costly state
                // at the same position/direction with more remaining power
                // can perform every future dust or repeater transition of a
                // weaker state.  Strength fourteen is deliberately excluded:
                // a weaker state may place a repeater at this exact cell,
                // while the current representation records a refresh only
                // when strength increases.
                let DominanceCeiling = if RemainingStrength < MAXIMUM_UNREFRESHED_DUST_LENGTH - 1 {
                    MAXIMUM_UNREFRESHED_DUST_LENGTH - 2
                } else {
                    RemainingStrength
                };
                let Dominated = (RemainingStrength..=DominanceCeiling).any(|CandidateStrength| {
                    Costs
                        .get(&(*Neighbor, OutgoingDirection, CandidateStrength))
                        .is_some_and(|Cost| *Cost <= NewCost)
                });
                if Dominated {
                    continue;
                }
                if RemainingStrength < MAXIMUM_UNREFRESHED_DUST_LENGTH - 1 {
                    for CandidateStrength in 1..=RemainingStrength {
                        let CandidateState = (*Neighbor, OutgoingDirection, CandidateStrength);
                        if Costs
                            .get(&CandidateState)
                            .is_some_and(|Cost| *Cost >= NewCost)
                        {
                            Costs.remove(&CandidateState);
                        }
                    }
                }
                Costs.insert(NeighborState, NewCost);
                Previous.insert(NeighborState, CurrentState);
                Claims.insert(NeighborState, Rc::clone(&NextClaims));
                Heap.push(QueueItem {
                    EstimatedCost: NewCost
                        + ManhattanDistance(*Neighbor, Target) * HEURISTIC_WEIGHT
                        + RepeaterCostLowerBound(*Neighbor, Target, RemainingStrength),
                    Cost: NewCost,
                    Sequence,
                    PositionValue: *Neighbor,
                    IncomingDirection: OutgoingDirection,
                    RemainingStrength,
                });
                Sequence += 1;
            }
        }
    }
    let NoPathReason = if RepeaterConstraintFailures > 0 && LastNoPathReason == "NoPathGeometry" {
        "NoRepeater"
    } else {
        LastNoPathReason
    };
    Some(Failure(
        "NoPath",
        NoPathReason,
        RepeaterRejectedCount,
        RepeaterConstraintFailures,
        Expanded,
    ))
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn FindPathWithDeadline(
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
    EnforceSignalStrength: bool,
    Deadline: &RuntimeDeadline,
) -> Option<Vec<Position>> {
    FindPathDetailedWithDeadline(
        Adjacency,
        Starts,
        Target,
        PreferredRoutingY,
        BlockedNodes,
        NodeCosts,
        EdgeCosts,
        BendPenalty,
        ViaPenalty,
        ProgressPenalty,
        MaximumExpansionCount,
        EnforceSignalStrength,
        Deadline,
    )
    .and_then(|Result| {
        if Result.Status == "Routed" {
            Some(Result.Path)
        } else {
            None
        }
    })
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn FindPath(
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
    FindPathWithDeadline(
        Adjacency,
        Starts,
        Target,
        PreferredRoutingY,
        BlockedNodes,
        NodeCosts,
        EdgeCosts,
        BendPenalty,
        ViaPenalty,
        ProgressPenalty,
        MaximumExpansionCount,
        false,
        &RuntimeDeadline::Unlimited(),
    )
}

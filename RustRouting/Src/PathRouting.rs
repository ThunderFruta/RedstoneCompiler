use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{Direction, Edge, PortalCandidate, Position, SearchState};
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::rc::Rc;

const MAXIMUM_EXPANSIONS: usize = 1_000_000;
const HEURISTIC_WEIGHT: i32 = 4;
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
    Deadline: &RuntimeDeadline,
) -> Option<Vec<Position>> {
    if Deadline.Check() {
        return None;
    }
    let StartSet: HashSet<Position> = Starts
        .iter()
        .copied()
        .filter(|Value| Adjacency.contains_key(Value))
        .collect();
    if StartSet.is_empty() || !Adjacency.contains_key(&Target) {
        return None;
    }
    if StartSet.contains(&Target) {
        return Some(vec![Target]);
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
        if Expanded % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
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
        &RuntimeDeadline::Unlimited(),
    )
}

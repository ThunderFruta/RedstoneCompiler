use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{
    ClaimAwareDetailedRouteTreeRequest, DetailedRouteTreeRequest, FactorizedRouteTreeAccessPayload,
    FactorizedRouteTreeGuidePayload, FactorizedRouteTreeRequest,
    FactorizedRouteTreeSelectionResult, PortalCandidate, PortalCandidateBatchResult, Position,
    Position2, RouteTreeBatchResult, RouteTreeDetailedBatchResult, RouteTreeSearchResult,
    RoutingContext, SearchState,
};
use crate::PathRouting::{
    BuildPortalCandidate, FindPathFromStatesDetailedWithDeadline, FindPathWithDeadline,
    ManhattanDistance, NormalizeEdge, BLOCKED_EDGE_COST, MAXIMUM_UNREFRESHED_DUST_LENGTH,
};
use crate::RoutingThreadPool;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::{BTreeMap, BinaryHeap, HashMap, HashSet, VecDeque};
use std::time::Instant;

struct PreparedDetailedRouteGuide {
    AllowedNodes: HashSet<Position>,
    AllowedColumns: HashSet<Position2>,
    UseColumnMembership: bool,
    BoundaryBlockedNodes: HashSet<Position>,
    NodeCosts: HashMap<Position, i32>,
    ColumnCosts: HashMap<Position2, i32>,
    PreferredColumns: Vec<(i32, i32)>,
    ExactHintNodes: HashSet<Position>,
    CertifiedPaths: Vec<Vec<Position>>,
    CertifiedRepeaters: Vec<(Position, String)>,
    GuidePenalty: i32,
}

struct PreparedFactorizedRouteTreeAccess {
    Starts: Vec<Position>,
    SourceBranch: Vec<Position>,
    TargetBranches: Vec<Vec<Position>>,
    FrozenTargetBranches: Vec<Vec<Position>>,
    RequiredNodes: HashSet<Position>,
    BlockedNodes: HashSet<Position>,
    MandatoryWire: HashSet<Position>,
    MandatorySupport: HashSet<Position>,
    MandatoryAir: HashSet<Position>,
    MandatoryElectrical: HashSet<Position>,
}

#[derive(Clone)]
struct ExactSelectedWorldRouteClaims {
    Wire: HashSet<Position>,
    Support: HashSet<Position>,
    Air: HashSet<Position>,
    Electrical: HashSet<Position>,
}

#[derive(Clone)]
struct ExactSelectedWorldRouteCandidate {
    RequestIndex: usize,
    Nodes: Vec<Position>,
    RepeaterReservations: Vec<(Position, String)>,
    Claims: ExactSelectedWorldRouteClaims,
}

fn PositionSetsIntersect(First: &HashSet<Position>, Second: &HashSet<Position>) -> bool {
    if First.len() <= Second.len() {
        First.iter().any(|Value| Second.contains(Value))
    } else {
        Second.iter().any(|Value| First.contains(Value))
    }
}

fn ExactSelectedWorldClaimsConflict(
    First: &ExactSelectedWorldRouteClaims,
    Second: &ExactSelectedWorldRouteClaims,
) -> bool {
    PositionSetsIntersect(&First.Wire, &Second.Electrical)
        || PositionSetsIntersect(&Second.Wire, &First.Electrical)
        || PositionSetsIntersect(&First.Support, &Second.Wire)
        || PositionSetsIntersect(&First.Support, &Second.Air)
        || PositionSetsIntersect(&Second.Support, &First.Wire)
        || PositionSetsIntersect(&Second.Support, &First.Air)
        || PositionSetsIntersect(&First.Air, &Second.Wire)
        || PositionSetsIntersect(&Second.Air, &First.Wire)
}

fn ExactSelectedWorldConflictResources(
    First: &ExactSelectedWorldRouteClaims,
    Second: &ExactSelectedWorldRouteClaims,
) -> Vec<(&'static str, Position)> {
    let mut Resources = Vec::new();
    for (Kind, FirstValues, SecondValues) in [
        ("wire-electrical", &First.Wire, &Second.Electrical),
        ("electrical-wire", &First.Electrical, &Second.Wire),
        ("support-wire", &First.Support, &Second.Wire),
        ("support-air", &First.Support, &Second.Air),
        ("wire-support", &First.Wire, &Second.Support),
        ("air-support", &First.Air, &Second.Support),
        ("air-wire", &First.Air, &Second.Wire),
        ("wire-air", &First.Wire, &Second.Air),
    ] {
        Resources.extend(
            FirstValues
                .intersection(SecondValues)
                .copied()
                .map(|PositionValue| (Kind, PositionValue)),
        );
    }
    Resources.sort_unstable();
    Resources.dedup();
    Resources
}

fn RedstoneNeighborPositions(PositionValue: Position) -> [Position; 12] {
    let (X, Y, Z) = PositionValue;
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

fn BuildExactSelectedWorldRouteClaims(
    Context: &RoutingContext,
    Nodes: &[Position],
    Access: &PreparedFactorizedRouteTreeAccess,
) -> ExactSelectedWorldRouteClaims {
    let mut Wire = Access.MandatoryWire.clone();
    Wire.extend(Nodes.iter().copied());
    let mut Support = Access.MandatorySupport.clone();
    let mut Air = Access.MandatoryAir.clone();
    let mut Electrical = Access.MandatoryElectrical.clone();
    for PositionValue in &Wire {
        Support.insert((PositionValue.0, PositionValue.1 - 1, PositionValue.2));
        Electrical.insert(*PositionValue);
        Electrical.extend(RedstoneNeighborPositions(*PositionValue));
    }
    for PositionValue in Nodes {
        for Neighbor in Context.Adjacency.get(PositionValue).into_iter().flatten() {
            if Neighbor.1 == PositionValue.1 || !Wire.contains(Neighbor) {
                continue;
            }
            let Lower = if PositionValue.1 < Neighbor.1 {
                *PositionValue
            } else {
                *Neighbor
            };
            Air.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    ExactSelectedWorldRouteClaims {
        Wire,
        Support,
        Air,
        Electrical,
    }
}

fn FindExactSelectedWorldMovableConflictNodes(
    Context: &RoutingContext,
    Candidate: &ExactSelectedWorldRouteCandidate,
    Access: &PreparedFactorizedRouteTreeAccess,
    Other: &ExactSelectedWorldRouteClaims,
) -> Vec<Position> {
    let CandidateNodeSet = Candidate.Nodes.iter().copied().collect::<HashSet<_>>();
    let mut Result = HashSet::new();
    for Node in CandidateNodeSet.iter().copied().filter(|Value| {
        // The fifth factorized payload field is the selected request's
        // additional allowed-node domain.  Python widens it with the
        // bounded access-repair corridor before this call; it is not an
        // immutable physical claim.  MandatoryWire is the authoritative
        // set of fixed access/portal conductors that a no-good may not
        // remove.
        !Access.MandatoryWire.contains(Value)
    }) {
        let Support = (Node.0, Node.1 - 1, Node.2);
        if Other.Electrical.contains(&Node)
            || Other.Support.contains(&Node)
            || Other.Air.contains(&Node)
            || Other.Wire.contains(&Support)
            || Other.Air.contains(&Support)
        {
            Result.insert(Node);
        }
        for Neighbor in Context.Adjacency.get(&Node).into_iter().flatten() {
            if Neighbor.1 == Node.1 || !CandidateNodeSet.contains(Neighbor) {
                continue;
            }
            let Lower = if Node.1 < Neighbor.1 { Node } else { *Neighbor };
            let RequiredAir = (Lower.0, Lower.1 + 1, Lower.2);
            if Other.Wire.contains(&RequiredAir) || Other.Support.contains(&RequiredAir) {
                Result.insert(Node);
                if !Access.MandatoryWire.contains(Neighbor) {
                    Result.insert(*Neighbor);
                }
            }
        }
    }
    let mut Values = Result.into_iter().collect::<Vec<_>>();
    Values.sort_unstable();
    Values
}

fn BuildExactSelectedWorldForeignBlockedNodes(
    _Context: &RoutingContext,
    ForeignClaims: impl IntoIterator<Item = ExactSelectedWorldRouteClaims>,
) -> HashSet<Position> {
    let mut Blocked = HashSet::new();
    for Claims in ForeignClaims {
        Blocked.extend(Claims.Electrical.iter().copied());
        Blocked.extend(Claims.Support.iter().copied());
        Blocked.extend(Claims.Air.iter().copied());
        Blocked.extend(
            Claims
                .Wire
                .iter()
                .chain(&Claims.Air)
                .map(|Value| (Value.0, Value.1 + 1, Value.2)),
        );
    }
    Blocked
}

fn SearchExactSelectedWorldAssignment(
    CandidateGroups: &[Vec<ExactSelectedWorldRouteCandidate>],
    GroupIndex: usize,
    SelectedCandidateIndices: &mut Vec<usize>,
    ExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    Deadline: &RuntimeDeadline,
) -> Option<Vec<usize>> {
    if Deadline.Check() || *ExpansionCount >= MaximumExpansionCount {
        return None;
    }
    if GroupIndex >= CandidateGroups.len() {
        return Some(SelectedCandidateIndices.clone());
    }
    for (CandidateIndex, Candidate) in CandidateGroups[GroupIndex].iter().enumerate() {
        if *ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        *ExpansionCount = ExpansionCount.saturating_add(1);
        if *ExpansionCount > MaximumExpansionCount {
            return None;
        }
        if SelectedCandidateIndices.iter().enumerate().any(
            |(PriorGroupIndex, PriorCandidateIndex)| {
                ExactSelectedWorldClaimsConflict(
                    &Candidate.Claims,
                    &CandidateGroups[PriorGroupIndex][*PriorCandidateIndex].Claims,
                )
            },
        ) {
            continue;
        }
        SelectedCandidateIndices.push(CandidateIndex);
        if let Some(Result) = SearchExactSelectedWorldAssignment(
            CandidateGroups,
            GroupIndex + 1,
            SelectedCandidateIndices,
            ExpansionCount,
            MaximumExpansionCount,
            Deadline,
        ) {
            return Some(Result);
        }
        SelectedCandidateIndices.pop();
    }
    None
}

fn IsPreparedRouteNodeAllowed(
    Guide: &PreparedDetailedRouteGuide,
    AdditionalAllowedNodes: &HashSet<Position>,
    Value: &Position,
) -> bool {
    Guide.AllowedNodes.contains(Value)
        || (Guide.UseColumnMembership && Guide.AllowedColumns.contains(&(Value.0, Value.2)))
        || AdditionalAllowedNodes.contains(Value)
}

fn DetailedRouteTreeBudgetExpiredResult() -> RouteTreeSearchResult {
    RouteTreeSearchResult {
        Status: "BudgetExpired".to_string(),
        NoPathReason: "BudgetExpired".to_string(),
        Nodes: Vec::new(),
        TargetPaths: Vec::new(),
        BoundaryFrontierNodes: Vec::new(),
        RepeaterReservations: Vec::new(),
        ExpansionCount: 0,
        RepeaterRejectedCount: 0,
        RepeaterConstraintFailureCount: 0,
        ConflictResources: Vec::new(),
        RejectedPathCount: 0,
        NoGoodCount: 0,
        ElapsedMilliseconds: 0,
        IsRouted: false,
        IsBudgetExpired: true,
    }
}

fn BuildRootedTreeBlockages(
    Context: &RoutingContext,
    Guide: &PreparedDetailedRouteGuide,
    AdditionalAllowedNodes: &HashSet<Position>,
    BaseBlockedNodes: &HashSet<Position>,
    Tree: &HashSet<Position>,
    ReservedNodes: &HashSet<Position>,
    Deadline: &RuntimeDeadline,
) -> Option<(HashSet<Position>, HashMap<(Position, Position), i32>)> {
    let OccupiedNodes: HashSet<_> = Tree.union(ReservedNodes).copied().collect();
    let Supports: HashSet<_> = OccupiedNodes
        .iter()
        .map(|Value| (Value.0, Value.1 - 1, Value.2))
        .collect();
    let mut RequiredAir = HashSet::new();
    for (Index, First) in OccupiedNodes.iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        for Second in Context.Adjacency.get(First).into_iter().flatten() {
            if !OccupiedNodes.contains(Second) || Second <= First || First.1 == Second.1 {
                continue;
            }
            let Lower = if First.1 < Second.1 { *First } else { *Second };
            RequiredAir.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    let mut BlockedNodes = BaseBlockedNodes.clone();
    let LocalBlockedNodes = Supports.iter().chain(RequiredAir.iter()).copied().chain(
        OccupiedNodes
            .iter()
            .chain(RequiredAir.iter())
            .map(|Value| (Value.0, Value.1 + 1, Value.2)),
    );
    for (Index, Node) in LocalBlockedNodes.enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        if IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, &Node) {
            BlockedNodes.insert(Node);
        }
    }
    let mut EdgeCosts = HashMap::new();
    for (Index, Headroom) in OccupiedNodes.iter().chain(Supports.iter()).enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        let Lower = (Headroom.0, Headroom.1 - 1, Headroom.2);
        if !IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, &Lower) {
            continue;
        }
        for Second in Context.Adjacency.get(&Lower).into_iter().flatten() {
            if !IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, Second)
                || Lower.1 == Second.1
            {
                continue;
            }
            let EdgeLower = if Lower.1 < Second.1 { Lower } else { *Second };
            let EdgeHeadroom = (EdgeLower.0, EdgeLower.1 + 1, EdgeLower.2);
            if EdgeHeadroom == *Headroom {
                EdgeCosts.insert(NormalizeEdge(Lower, *Second), BLOCKED_EDGE_COST);
            }
        }
    }
    Some((BlockedNodes, EdgeCosts))
}

fn RepeaterFacing(Current: Position, Next: Position) -> Option<String> {
    match (Next.0 - Current.0, Next.2 - Current.2) {
        (1, 0) => Some("west".to_string()),
        (-1, 0) => Some("east".to_string()),
        (0, 1) => Some("north".to_string()),
        (0, -1) => Some("south".to_string()),
        _ => None,
    }
}

fn EraseCanonicalRoutePathLoops(
    Values: impl IntoIterator<Item = Position>,
) -> Vec<Position> {
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

fn PropagateCanonicalRoutePowerWithParents(
    Root: Position,
    Nodes: &HashSet<Position>,
    Repeaters: &HashMap<Position, String>,
    Adjacency: &HashMap<Position, Vec<Position>>,
) -> (HashMap<Position, u8>, HashMap<Position, Position>) {
    let OutputDelta = |Facing: &str| match Facing {
        "west" => Some((1, 0, 0)),
        "east" => Some((-1, 0, 0)),
        "north" => Some((0, 0, 1)),
        "south" => Some((0, 0, -1)),
        _ => None,
    };
    let mut Powers = HashMap::from([(Root, MAXIMUM_UNREFRESHED_DUST_LENGTH)]);
    let mut Parents = HashMap::new();
    let mut Pending = BinaryHeap::from([(MAXIMUM_UNREFRESHED_DUST_LENGTH, Root)]);
    while let Some((Power, Current)) = Pending.pop() {
        if Powers.get(&Current).copied() != Some(Power) {
            continue;
        }
        let Candidates = if let Some(Facing) = Repeaters.get(&Current) {
            OutputDelta(Facing)
                .map(|Delta| {
                    (
                        Current.0 + Delta.0,
                        Current.1 + Delta.1,
                        Current.2 + Delta.2,
                    )
                })
                .filter(|Value| Nodes.contains(Value))
                .filter(|Value| {
                    Adjacency
                        .get(&Current)
                        .is_some_and(|Neighbors| Neighbors.contains(Value))
                })
                .map(|Value| (Value, MAXIMUM_UNREFRESHED_DUST_LENGTH))
                .into_iter()
                .collect::<Vec<_>>()
        } else {
            Adjacency
                .get(&Current)
                .into_iter()
                .flatten()
                .copied()
                .filter(|Value| Nodes.contains(Value))
                .filter_map(|Neighbor| {
                    if let Some(Facing) = Repeaters.get(&Neighbor) {
                        let Delta = OutputDelta(Facing)?;
                        let Input = (
                            Neighbor.0 - Delta.0,
                            Neighbor.1 - Delta.1,
                            Neighbor.2 - Delta.2,
                        );
                        (Current == Input && Power > 0)
                            .then_some((Neighbor, MAXIMUM_UNREFRESHED_DUST_LENGTH))
                    } else {
                        (Power > 1).then_some((Neighbor, Power - 1))
                    }
                })
                .collect::<Vec<_>>()
        };
        for (Neighbor, CandidatePower) in Candidates {
            if CandidatePower <= Powers.get(&Neighbor).copied().unwrap_or(0) {
                continue;
            }
            Powers.insert(Neighbor, CandidatePower);
            Parents.insert(Neighbor, Current);
            Pending.push((CandidatePower, Neighbor));
        }
    }
    (Powers, Parents)
}

fn PropagateCanonicalRoutePower(
    Root: Position,
    Nodes: &HashSet<Position>,
    Repeaters: &HashMap<Position, String>,
    Adjacency: &HashMap<Position, Vec<Position>>,
) -> HashMap<Position, u8> {
    PropagateCanonicalRoutePowerWithParents(Root, Nodes, Repeaters, Adjacency).0
}

fn FindSelfExcitingRepeaterCycles(
    Nodes: &HashSet<Position>,
    RepeaterValues: &[(Position, String)],
) -> Vec<(Position, Vec<Position>)> {
    let Repeaters = RepeaterValues.iter().cloned().collect::<HashMap<_, _>>();
    let OutputDelta = |Facing: &str| match Facing {
        "west" => Some((1, 0, 0)),
        "east" => Some((-1, 0, 0)),
        "north" => Some((0, 0, 1)),
        "south" => Some((0, 0, -1)),
        _ => None,
    };
    let DirectedNeighbors = |Current: Position| {
        if let Some(Facing) = Repeaters.get(&Current) {
            return OutputDelta(Facing)
                .map(|Delta| {
                    (
                        Current.0 + Delta.0,
                        Current.1 + Delta.1,
                        Current.2 + Delta.2,
                    )
                })
                .filter(|Value| Nodes.contains(Value))
                .into_iter()
                .collect::<Vec<_>>();
        }
        let mut Values = RedstoneNeighborPositions(Current)
            .into_iter()
            .filter(|Value| Nodes.contains(Value))
            .filter(|Neighbor| {
                let Some(Facing) = Repeaters.get(Neighbor) else {
                    return true;
                };
                let Some(Delta) = OutputDelta(Facing) else {
                    return false;
                };
                Current
                    == (
                        Neighbor.0 - Delta.0,
                        Neighbor.1 - Delta.1,
                        Neighbor.2 - Delta.2,
                    )
            })
            .collect::<Vec<_>>();
        Values.sort_unstable();
        Values
    };

    let mut OrderedRepeaters = RepeaterValues.to_vec();
    OrderedRepeaters.sort_unstable();
    let mut Result = Vec::new();
    for (Repeater, Facing) in OrderedRepeaters {
        let Some(Delta) = OutputDelta(&Facing) else {
            continue;
        };
        let Input = (
            Repeater.0 - Delta.0,
            Repeater.1 - Delta.1,
            Repeater.2 - Delta.2,
        );
        let Output = (
            Repeater.0 + Delta.0,
            Repeater.1 + Delta.1,
            Repeater.2 + Delta.2,
        );
        if !Nodes.contains(&Input) || !Nodes.contains(&Output) {
            continue;
        }
        let mut Pending = VecDeque::from([Output]);
        let mut Parent = HashMap::from([(Output, None::<Position>)]);
        while let Some(Current) = Pending.pop_front() {
            if Current == Input {
                break;
            }
            for Neighbor in DirectedNeighbors(Current) {
                if Neighbor == Repeater || Parent.contains_key(&Neighbor) {
                    continue;
                }
                Parent.insert(Neighbor, Some(Current));
                Pending.push_back(Neighbor);
            }
        }
        if !Parent.contains_key(&Input) {
            continue;
        }
        let mut Cycle = vec![Repeater];
        let mut Cursor = Some(Input);
        while let Some(Value) = Cursor {
            Cycle.push(Value);
            Cursor = Parent[&Value];
        }
        Cycle.sort_unstable();
        Cycle.dedup();
        Result.push((Repeater, Cycle));
    }
    Result
}

impl RoutingContext {
    fn PrepareDetailedRouteGuide(
        &self,
        AllowedNodeValues: &[Position],
        PreferredColumns: &[(i32, i32)],
        ExternalNodeCostValues: &[(Position, i32)],
        GuidePenalty: i32,
        Deadline: &RuntimeDeadline,
    ) -> Option<PreparedDetailedRouteGuide> {
        let AllowedNodes: HashSet<_> = AllowedNodeValues.iter().copied().collect();
        let mut BoundaryBlockedNodes = HashSet::new();
        for (Index, Value) in AllowedNodes.iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                if !AllowedNodes.contains(Neighbor) {
                    BoundaryBlockedNodes.insert(*Neighbor);
                }
            }
        }
        let mut NodeCosts: HashMap<_, _> = ExternalNodeCostValues
            .iter()
            .copied()
            .filter(|(_PositionValue, Cost)| *Cost > 0)
            .collect();
        if GuidePenalty > 0 && !PreferredColumns.is_empty() {
            for (Index, Value) in AllowedNodes.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                let Distance = PreferredColumns
                    .iter()
                    .map(|Column| (Value.0 - Column.0).abs() + (Value.2 - Column.1).abs())
                    .min()
                    .unwrap_or(0);
                *NodeCosts.entry(*Value).or_default() += Distance * GuidePenalty;
            }
        }
        Some(PreparedDetailedRouteGuide {
            AllowedNodes,
            AllowedColumns: HashSet::new(),
            UseColumnMembership: false,
            BoundaryBlockedNodes,
            NodeCosts,
            ColumnCosts: HashMap::new(),
            PreferredColumns: PreferredColumns.to_vec(),
            ExactHintNodes: HashSet::new(),
            CertifiedPaths: Vec::new(),
            CertifiedRepeaters: Vec::new(),
            GuidePenalty,
        })
    }

    fn PrepareDetailedRouteGuideFromColumns(
        &self,
        AllowedColumnValues: &[(i32, i32)],
        PreferredColumns: &[(i32, i32)],
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        Deadline: &RuntimeDeadline,
    ) -> Option<PreparedDetailedRouteGuide> {
        if Deadline.Check() {
            return None;
        }
        let AllowedColumns: HashSet<_> = AllowedColumnValues.iter().copied().collect();
        let ColumnCosts = if GuidePenalty > 0 && !PreferredColumns.is_empty() {
            AllowedColumns
                .iter()
                .map(|Column| {
                    let Distance = PreferredColumns
                        .iter()
                        .map(|Preferred| {
                            (Column.0 - Preferred.0).abs() + (Column.1 - Preferred.1).abs()
                        })
                        .min()
                        .unwrap_or(0);
                    (*Column, Distance * GuidePenalty)
                })
                .collect()
        } else {
            HashMap::new()
        };
        let NodeCosts = AllowedColumns
            .iter()
            .flat_map(|Column| self.NodesByColumn.get(Column).into_iter().flatten())
            .filter_map(|PositionValue| {
                let Distance = (PositionValue.1 - PreferredRoutingY).abs();
                (Distance > 0)
                    .then_some((*PositionValue, Distance.saturating_mul(GuidePenalty.max(1))))
            })
            .collect::<HashMap<_, _>>();
        Some(PreparedDetailedRouteGuide {
            AllowedNodes: HashSet::new(),
            AllowedColumns,
            UseColumnMembership: true,
            BoundaryBlockedNodes: HashSet::new(),
            NodeCosts,
            ColumnCosts,
            PreferredColumns: PreferredColumns.to_vec(),
            ExactHintNodes: HashSet::new(),
            CertifiedPaths: Vec::new(),
            CertifiedRepeaters: Vec::new(),
            GuidePenalty,
        })
    }

    fn CertifyRouteFactorConnectivityWithDeadline(
        &self,
        AllowedColumns: Vec<(i32, i32)>,
        RequiredAllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        ConnectivityRequiredNodeValues: Vec<Position>,
        Start: Position,
        MaximumExpansionCount: usize,
        Deadline: &RuntimeDeadline,
    ) -> (bool, bool, usize) {
        let mut AllowedNodes = HashSet::new();
        for (Index, Column) in AllowedColumns.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return (false, false, 0);
            }
            if let Some(Values) = self.NodesByColumn.get(&Column) {
                AllowedNodes.extend(Values.iter().copied());
            }
        }
        AllowedNodes.extend(RequiredAllowedNodeValues);
        let mut BlockedNodes = HashSet::with_capacity(BlockedNodeValues.len());
        for (Index, Value) in BlockedNodeValues.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return (false, false, 0);
            }
            BlockedNodes.insert(Value);
        }
        let RequiredNodes: HashSet<_> = ConnectivityRequiredNodeValues.into_iter().collect();
        if !AllowedNodes.contains(&Start)
            || BlockedNodes.contains(&Start)
            || RequiredNodes.is_empty()
            || RequiredNodes
                .iter()
                .any(|Value| !AllowedNodes.contains(Value) || BlockedNodes.contains(Value))
        {
            return (true, false, 0);
        }
        let ExpansionLimit = MaximumExpansionCount.max(1);
        let mut Reached = HashSet::from([Start]);
        let mut RemainingRequired = RequiredNodes;
        RemainingRequired.remove(&Start);
        if RemainingRequired.is_empty() {
            return (true, true, 0);
        }
        let mut Pending = vec![Start];
        let mut ExpansionCount = 0usize;
        while let Some(Current) = Pending.pop() {
            if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return (false, false, ExpansionCount);
            }
            if ExpansionCount >= ExpansionLimit {
                return (false, false, ExpansionCount);
            }
            ExpansionCount += 1;
            for Neighbor in self.Adjacency.get(&Current).into_iter().flatten() {
                if AllowedNodes.contains(Neighbor)
                    && !BlockedNodes.contains(Neighbor)
                    && Reached.insert(*Neighbor)
                {
                    RemainingRequired.remove(Neighbor);
                    if RemainingRequired.is_empty() {
                        return (true, true, ExpansionCount);
                    }
                    Pending.push(*Neighbor);
                }
            }
        }
        if Deadline.Check() {
            return (false, false, ExpansionCount);
        }
        (true, RemainingRequired.is_empty(), ExpansionCount)
    }

    pub(crate) fn CertifyRouteFactorConnectivityNative(
        &self,
        AllowedColumns: Vec<(i32, i32)>,
        RequiredAllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        ConnectivityRequiredNodeValues: Vec<Position>,
        Start: Position,
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> (bool, bool, usize) {
        let Ok(Deadline) = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        else {
            return (false, false, 0);
        };
        self.CertifyRouteFactorConnectivityWithDeadline(
            AllowedColumns,
            RequiredAllowedNodeValues,
            BlockedNodeValues,
            ConnectivityRequiredNodeValues,
            Start,
            MaximumExpansionCount,
            &Deadline,
        )
    }

    pub(crate) fn CertifyRouteFactorConnectivityBatchNative(
        &self,
        Requests: Vec<(
            Vec<(i32, i32)>,
            i32,
            Vec<Position>,
            Vec<Position>,
            Vec<Position>,
            Position,
            usize,
        )>,
        MaximumRuntimeMilliseconds: u64,
    ) -> Vec<(bool, bool, usize)> {
        let Ok(Deadline) = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        else {
            return vec![(false, false, 0); Requests.len()];
        };
        let RequestCount = Requests.len();
        let mut IndexedPositions: Vec<Position> = self.Adjacency.keys().copied().collect();
        IndexedPositions.sort_unstable();
        let PositionIndex: HashMap<Position, usize> = IndexedPositions
            .iter()
            .copied()
            .enumerate()
            .map(|(Index, PositionValue)| (PositionValue, Index))
            .collect();
        let IndexedAdjacency: Vec<Vec<usize>> = IndexedPositions
            .iter()
            .map(|PositionValue| {
                self.Adjacency
                    .get(PositionValue)
                    .into_iter()
                    .flatten()
                    .filter_map(|Neighbor| PositionIndex.get(Neighbor).copied())
                    .collect()
            })
            .collect();
        let IndexedNodesByColumn: HashMap<(i32, i32), Vec<usize>> = self
            .NodesByColumn
            .iter()
            .map(|(Column, Positions)| {
                (
                    *Column,
                    Positions
                        .iter()
                        .filter_map(|PositionValue| PositionIndex.get(PositionValue).copied())
                        .collect(),
                )
            })
            .collect();
        if Deadline.Check() {
            return vec![(false, false, 0); RequestCount];
        }
        RoutingThreadPool().install(|| {
            Requests
                .into_par_iter()
                .map(
                    |(
                        GuideColumns,
                        GuideExpansion,
                        RequiredAllowedNodeValues,
                        BlockedNodeValues,
                        ConnectivityRequiredNodeValues,
                        Start,
                        MaximumExpansionCount,
                    )| {
                        if Deadline.Check() {
                            return (false, false, 0);
                        }
                        if GuideExpansion < 0 {
                            return (true, false, 0);
                        }
                        let mut AllowedNodes = vec![false; IndexedPositions.len()];
                        let mut PreparedColumnCount = 0usize;
                        for (GuideX, GuideZ) in GuideColumns {
                            for DeltaX in -GuideExpansion..=GuideExpansion {
                                for DeltaZ in -GuideExpansion..=GuideExpansion {
                                    if DeltaX.abs() + DeltaZ.abs() <= GuideExpansion {
                                        if PreparedColumnCount % DEADLINE_CHECK_INTERVAL == 0
                                            && Deadline.Check()
                                        {
                                            return (false, false, 0);
                                        }
                                        if let Some(Indices) = IndexedNodesByColumn
                                            .get(&(GuideX + DeltaX, GuideZ + DeltaZ))
                                        {
                                            for Index in Indices {
                                                AllowedNodes[*Index] = true;
                                            }
                                        }
                                        PreparedColumnCount += 1;
                                    }
                                }
                            }
                        }
                        for PositionValue in RequiredAllowedNodeValues {
                            if let Some(Index) = PositionIndex.get(&PositionValue) {
                                AllowedNodes[*Index] = true;
                            }
                        }
                        let mut BlockedNodes = vec![false; IndexedPositions.len()];
                        for PositionValue in BlockedNodeValues {
                            if let Some(Index) = PositionIndex.get(&PositionValue) {
                                BlockedNodes[*Index] = true;
                            }
                        }
                        let Some(StartIndex) = PositionIndex.get(&Start).copied() else {
                            return (true, false, 0);
                        };
                        let mut RequiredNodes = vec![false; IndexedPositions.len()];
                        let mut RemainingRequiredCount = 0usize;
                        for PositionValue in ConnectivityRequiredNodeValues {
                            let Some(Index) = PositionIndex.get(&PositionValue).copied() else {
                                return (true, false, 0);
                            };
                            if !RequiredNodes[Index] {
                                RequiredNodes[Index] = true;
                                RemainingRequiredCount += 1;
                            }
                        }
                        if !AllowedNodes[StartIndex]
                            || BlockedNodes[StartIndex]
                            || RemainingRequiredCount == 0
                            || RequiredNodes.iter().enumerate().any(|(Index, Required)| {
                                *Required && (!AllowedNodes[Index] || BlockedNodes[Index])
                            })
                        {
                            return (true, false, 0);
                        }
                        if RequiredNodes[StartIndex] {
                            RequiredNodes[StartIndex] = false;
                            RemainingRequiredCount -= 1;
                        }
                        if RemainingRequiredCount == 0 {
                            return (true, true, 0);
                        }
                        let ExpansionLimit = MaximumExpansionCount.max(1);
                        let mut Reached = vec![false; IndexedPositions.len()];
                        Reached[StartIndex] = true;
                        let mut Pending = vec![StartIndex];
                        let mut ExpansionCount = 0usize;
                        while let Some(CurrentIndex) = Pending.pop() {
                            if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                                return (false, false, ExpansionCount);
                            }
                            if ExpansionCount >= ExpansionLimit {
                                return (false, false, ExpansionCount);
                            }
                            ExpansionCount += 1;
                            for NeighborIndex in &IndexedAdjacency[CurrentIndex] {
                                if AllowedNodes[*NeighborIndex]
                                    && !BlockedNodes[*NeighborIndex]
                                    && !Reached[*NeighborIndex]
                                {
                                    Reached[*NeighborIndex] = true;
                                    if RequiredNodes[*NeighborIndex] {
                                        RequiredNodes[*NeighborIndex] = false;
                                        RemainingRequiredCount -= 1;
                                        if RemainingRequiredCount == 0 {
                                            return (true, true, ExpansionCount);
                                        }
                                    }
                                    Pending.push(*NeighborIndex);
                                }
                            }
                        }
                        if Deadline.Check() {
                            return (false, false, ExpansionCount);
                        }
                        (true, RemainingRequiredCount == 0, ExpansionCount)
                    },
                )
                .collect()
        })
    }

    pub(crate) fn GeneratePortalCandidatesNative(
        &self,
        Starts: Vec<Position>,
        PortalTargets: Vec<Position>,
        AllowedNodeValues: Vec<Position>,
        PreferredRoutingY: i32,
        MaximumPortalCount: usize,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> Vec<PortalCandidate> {
        let Ok(Deadline) = RuntimeDeadline::FromSeconds(MaximumRuntimeSeconds) else {
            return Vec::new();
        };
        if Deadline.Check() {
            return Vec::new();
        }
        let mut AllowedNodes = HashSet::with_capacity(AllowedNodeValues.len());
        for (Index, Value) in AllowedNodeValues.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Vec::new();
            }
            AllowedNodes.insert(Value);
        }
        let Starts: Vec<_> = Starts
            .into_iter()
            .filter(|Value| AllowedNodes.contains(Value) && self.Adjacency.contains_key(Value))
            .collect();
        if Starts.is_empty() {
            return Vec::new();
        }
        let mut BlockedNodes = HashSet::new();
        for (Index, Value) in AllowedNodes.iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Vec::new();
            }
            for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                if !AllowedNodes.contains(Neighbor) {
                    BlockedNodes.insert(*Neighbor);
                }
            }
        }
        let mut Targets = PortalTargets;
        Targets.sort_unstable();
        Targets.dedup();
        let mut Candidates = Vec::new();
        for Target in Targets {
            if Deadline.Check() {
                return Vec::new();
            }
            let Some(mut Path) = FindPathWithDeadline(
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
                false,
                &Deadline,
            ) else {
                continue;
            };
            let Some(FirstStep) = Path.first().copied() else {
                continue;
            };
            if !Starts.contains(&FirstStep) {
                let Some(AccessAnchor) = Starts.iter().copied().find(|Start| {
                    self.Adjacency
                        .get(Start)
                        .map(|Neighbors| Neighbors.contains(&FirstStep))
                        .unwrap_or(false)
                }) else {
                    continue;
                };
                Path.insert(0, AccessAnchor);
            }
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

    pub(crate) fn GenerateRouteTreeNative(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> Option<Vec<Position>> {
        self.GenerateRouteTreeWithCostsNative(
            Starts,
            TargetBranches,
            AllowedNodeValues,
            BlockedNodeValues,
            PreferredColumns,
            Vec::new(),
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            false,
            MaximumExpansionCount,
            MaximumRuntimeSeconds,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn GenerateRouteTreeWithCostsNative(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        ExternalNodeCostValues: Vec<(Position, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> Option<Vec<Position>> {
        let Ok(Deadline) = RuntimeDeadline::FromSeconds(MaximumRuntimeSeconds) else {
            return None;
        };
        if Deadline.Check() {
            return None;
        }
        let mut AllowedNodes = HashSet::with_capacity(AllowedNodeValues.len());
        for (Index, Value) in AllowedNodeValues.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            AllowedNodes.insert(Value);
        }
        let mut ExplicitBlockedNodes = HashSet::with_capacity(BlockedNodeValues.len());
        for (Index, Value) in BlockedNodeValues.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            ExplicitBlockedNodes.insert(Value);
        }
        if TargetBranches.iter().any(|Branch| {
            Branch
                .iter()
                .any(|Value| ExplicitBlockedNodes.contains(Value))
        }) {
            return None;
        }
        let mut BlockedNodes = ExplicitBlockedNodes;
        for (Index, Value) in AllowedNodes.iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                if !AllowedNodes.contains(Neighbor) {
                    BlockedNodes.insert(*Neighbor);
                }
            }
        }
        let mut NodeCosts: HashMap<Position, i32> = ExternalNodeCostValues
            .into_iter()
            .filter(|(_PositionValue, Cost)| *Cost > 0)
            .collect();
        if GuidePenalty > 0 && !PreferredColumns.is_empty() {
            for (Index, Value) in AllowedNodes.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                let Distance = PreferredColumns
                    .iter()
                    .map(|Column| (Value.0 - Column.0).abs() + (Value.2 - Column.1).abs())
                    .min()
                    .unwrap_or(0);
                *NodeCosts.entry(*Value).or_default() += Distance * GuidePenalty;
            }
        }
        let Starts: Vec<_> = Starts
            .into_iter()
            .filter(|Value| {
                AllowedNodes.contains(Value)
                    && self.Adjacency.contains_key(Value)
                    && !BlockedNodes.contains(Value)
            })
            .collect();
        if Starts.is_empty() {
            return None;
        }

        let RootStart = Starts[0];
        let mut Tree: HashSet<_> = HashSet::new();
        for Start in Starts {
            if Deadline.Check() {
                return None;
            }
            Tree.insert(Start);
        }
        let BranchTargets: Vec<Position> = TargetBranches
            .iter()
            .filter_map(|Branch| Branch.last().copied())
            .collect();

        let BuildRootComponent = |Tree: &HashSet<Position>| -> HashSet<Position> {
            let mut Stack = Vec::new();
            let mut Component = HashSet::new();
            if !Tree.contains(&RootStart) {
                return Component;
            }
            Component.insert(RootStart);
            Stack.push(RootStart);
            while let Some(Value) = Stack.pop() {
                if Component.len() % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return HashSet::new();
                }
                let Neighbors = self.Adjacency.get(&Value).into_iter().flatten();
                for Neighbor in Neighbors {
                    if !Tree.contains(Neighbor) || Component.contains(Neighbor) {
                        continue;
                    }
                    Component.insert(*Neighbor);
                    Stack.push(*Neighbor);
                }
            }
            Component
        };

        let mut RootComponent = BuildRootComponent(&Tree);
        if RootComponent.is_empty() {
            return None;
        }

        let StartsToConnect: Vec<Position> = Tree
            .iter()
            .filter(|Value| !RootComponent.contains(Value))
            .copied()
            .collect();
        if !StartsToConnect.is_empty() {
            for Start in StartsToConnect {
                if Deadline.Check() {
                    return None;
                }
                let TreeStarts: Vec<_> = RootComponent.iter().copied().collect();
                let PathToStart = FindPathWithDeadline(
                    &self.Adjacency,
                    &TreeStarts,
                    Start,
                    PreferredRoutingY,
                    &BlockedNodes,
                    &NodeCosts,
                    &HashMap::new(),
                    BendPenalty,
                    ViaPenalty,
                    0,
                    MaximumExpansionCount,
                    EnforceSignalStrength,
                    &Deadline,
                )?;
                if PathToStart.is_empty() {
                    return None;
                }
                Tree.extend(PathToStart);
                RootComponent = BuildRootComponent(&Tree);
                if RootComponent.is_empty() {
                    return None;
                }
            }
        }

        let IsBranchChain = |Branch: &Vec<Position>| -> bool {
            Branch.windows(2).all(|Window| {
                self.Adjacency.contains_key(&Window[0])
                    && self.Adjacency[&Window[0]].contains(&Window[1])
            })
        };

        let IsConnectedToRoot = |Target: &Position, RootComponent: &HashSet<Position>| -> bool {
            RootComponent.contains(Target)
        };

        let mut RemainingBranches = TargetBranches.to_vec();
        while !RemainingBranches.is_empty() {
            if Deadline.Check() {
                return None;
            }
            let SelectedIndex = RemainingBranches
                .iter()
                .enumerate()
                .min_by_key(|(_, Branch)| {
                    let Target = Branch
                        .last()
                        .copied()
                        .unwrap_or((i32::MAX, i32::MAX, i32::MAX));
                    let Distance = RootComponent
                        .iter()
                        .map(|Start| ManhattanDistance(*Start, Target))
                        .min()
                        .unwrap_or(i32::MAX);
                    (Distance, Target, Branch.len())
                })
                .map(|(Index, _)| Index)?;
            let Branch = RemainingBranches.remove(SelectedIndex);
            let Target = *Branch.first()?;
            if !IsBranchChain(&Branch) {
                return None;
            }

            let mut TreeStarts: Vec<_> = RootComponent.iter().copied().collect();
            TreeStarts.sort_unstable();
            let ExistingSupport: HashSet<_> = Tree
                .iter()
                .map(|Value| (Value.0, Value.1 - 1, Value.2))
                .collect();
            let mut ExistingAir = HashSet::new();
            for (Index, First) in Tree.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
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
            for (Index, Node) in AllowedNodes.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
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
            for (Index, First) in AllowedNodes.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
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
            let NeedsPath = !IsConnectedToRoot(&Target, &RootComponent);
            if !Tree.contains(&Target) || NeedsPath {
                let Path = FindPathWithDeadline(
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
                    EnforceSignalStrength,
                    &Deadline,
                )?;
                Tree.extend(Path);
            } else if Branch.len() > 1 {
                let ExistingInBranch: HashSet<_> = Branch
                    .iter()
                    .filter(|Value| Tree.contains(Value))
                    .copied()
                    .collect();
                if ExistingInBranch.len() > 1 {
                    return None;
                }
            }

            Tree.extend(Branch);
            RootComponent = BuildRootComponent(&Tree);
            if RootComponent.is_empty() {
                return None;
            }
        }

        if !BranchTargets
            .iter()
            .all(|Target| IsConnectedToRoot(Target, &RootComponent))
        {
            return None;
        }

        let mut Result: Vec<_> = Tree.into_iter().collect();
        Result.sort_unstable();
        Some(Result)
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn GenerateRouteTreeDetailedNative(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        ExternalNodeCostValues: Vec<(Position, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> RouteTreeSearchResult {
        let Ok(Deadline) =
            RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds.max(1)))
        else {
            return DetailedRouteTreeBudgetExpiredResult();
        };
        self.GenerateRouteTreeDetailedWithDeadlineNative(
            Starts,
            TargetBranches,
            AllowedNodeValues,
            BlockedNodeValues,
            PreferredColumns,
            ExternalNodeCostValues,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            MaximumExpansionCount,
            &Deadline,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn GenerateRouteTreeDetailedWithDeadlineNative(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        ExternalNodeCostValues: Vec<(Position, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        MaximumExpansionCount: usize,
        Deadline: &RuntimeDeadline,
    ) -> RouteTreeSearchResult {
        let Some(Guide) = self.PrepareDetailedRouteGuide(
            &AllowedNodeValues,
            &PreferredColumns,
            &ExternalNodeCostValues,
            GuidePenalty,
            Deadline,
        ) else {
            return DetailedRouteTreeBudgetExpiredResult();
        };
        self.GenerateRouteTreeDetailedPreparedWithDeadlineNative(
            &Starts,
            &TargetBranches,
            &TargetBranches,
            &Guide,
            &HashSet::new(),
            &HashSet::new(),
            &BlockedNodeValues.into_iter().collect(),
            PreferredRoutingY,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            None,
            &HashSet::new(),
            "",
            MaximumExpansionCount,
            Deadline,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn GenerateRouteTreeDetailedPreparedWithDeadlineNative(
        &self,
        Starts: &[Position],
        TargetBranches: &[Vec<Position>],
        FrozenTargetBranches: &[Vec<Position>],
        Guide: &PreparedDetailedRouteGuide,
        AdditionalAllowedNodes: &HashSet<Position>,
        UnblockedAdditionalNodes: &HashSet<Position>,
        BaseBlockedNodes: &HashSet<Position>,
        PreferredRoutingY: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        FrozenSourceBranch: Option<&[Position]>,
        ForbiddenRepeaterPositions: &HashSet<Position>,
        DebugLabel: &str,
        MaximumExpansionCount: usize,
        Deadline: &RuntimeDeadline,
    ) -> RouteTreeSearchResult {
        let Failure = |Status: &str,
                       NoPathReason: &str,
                       RepeaterRejectedCount: usize,
                       RepeaterConstraintFailureCount: usize,
                       ExpansionCount: usize| {
            let Expired = Deadline.Check();
            let EffectiveStatus = if Expired { "BudgetExpired" } else { Status };
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected detailed failure signal={} status={} reason={} expansions={}",
                    DebugLabel, EffectiveStatus, NoPathReason, ExpansionCount,
                );
            }
            RouteTreeSearchResult {
                Status: EffectiveStatus.to_string(),
                NoPathReason: NoPathReason.to_string(),
                Nodes: Vec::new(),
                TargetPaths: Vec::new(),
                BoundaryFrontierNodes: Vec::new(),
                RepeaterReservations: Vec::new(),
                ExpansionCount,
                RepeaterRejectedCount,
                RepeaterConstraintFailureCount,
                ConflictResources: Vec::new(),
                RejectedPathCount: 0,
                NoGoodCount: 0,
                ElapsedMilliseconds: 0,
                IsRouted: false,
                IsBudgetExpired: Expired,
            }
        };
        let mut BlockedNodes = BaseBlockedNodes.clone();
        BlockedNodes.extend(Guide.BoundaryBlockedNodes.iter().copied());
        for Value in UnblockedAdditionalNodes {
            BlockedNodes.remove(Value);
        }
        for (Index, Value) in AdditionalAllowedNodes.iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Failure("NoPath", "SearchLimitReached", 0, 0, 0);
            }
            for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                if !IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, Neighbor) {
                    BlockedNodes.insert(*Neighbor);
                }
            }
        }
        if TargetBranches.iter().any(|Branch| {
            Branch.is_empty()
                || Branch.windows(2).any(|Values| {
                    !self
                        .Adjacency
                        .get(&Values[0])
                        .is_some_and(|Neighbors| Neighbors.contains(&Values[1]))
                })
                || Branch.iter().any(|Value| BlockedNodes.contains(Value))
        }) {
            return Failure("NoPath", "NoPathGeometry", 0, 0, 0);
        }
        let mut AdditionalNodeCosts = HashMap::new();
        if Guide.GuidePenalty > 0 && !Guide.PreferredColumns.is_empty() {
            for Value in AdditionalAllowedNodes {
                if Guide.AllowedNodes.contains(Value)
                    || (Guide.UseColumnMembership
                        && Guide.AllowedColumns.contains(&(Value.0, Value.2)))
                {
                    continue;
                }
                let Distance = Guide
                    .PreferredColumns
                    .iter()
                    .map(|Column| (Value.0 - Column.0).abs() + (Value.2 - Column.1).abs())
                    .min()
                    .unwrap_or(0);
                AdditionalNodeCosts.insert(*Value, Distance * Guide.GuidePenalty);
            }
        }
        let Starts: Vec<_> = Starts
            .iter()
            .copied()
            .filter(|Value| {
                IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, Value)
                    && self.Adjacency.contains_key(Value)
                    && !BlockedNodes.contains(Value)
            })
            .collect();
        let Some(Root) = Starts.first().copied() else {
            return Failure("NoPath", "NoPathGeometry", 0, 0, 0);
        };

        let StartDirection = (0, 0, 0);
        let RootState = (Root, StartDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH);
        let mut Tree = HashSet::from([Root]);
        let mut StateByNode = HashMap::from([(Root, RootState)]);
        let mut ParentByNode: HashMap<Position, Position> = HashMap::new();
        let mut Repeaters: HashMap<Position, String> = HashMap::new();
        let mut ExpansionCount = 0usize;
        let FrozenReservedAccessNodes = FrozenSourceBranch
            .map(|SourceBranch| {
                SourceBranch
                    .iter()
                    .chain(FrozenTargetBranches.iter().flatten())
                    .copied()
                    .collect::<HashSet<_>>()
            })
            .unwrap_or_default();

        // A compact powered certificate is an exact selected-world warm
        // witness, not merely an unordered corridor hint.  Accept it only
        // after revalidating graph adjacency, frozen access ownership,
        // repeater direction, blocked resources, canonical power, and
        // self-excitation against this detailed request.  A failed witness
        // falls through to the ordinary bounded materializer in the same
        // native invocation.
        if FrozenSourceBranch.is_some() && !Guide.CertifiedPaths.is_empty() {
            let CertifiedGeometryComplete = Guide.CertifiedPaths.iter().all(|Path| {
                Path.first().copied() == Some(Root)
                    && Path.windows(2).all(|Values| {
                        self.Adjacency
                            .get(&Values[0])
                            .is_some_and(|Neighbors| Neighbors.contains(&Values[1]))
                    })
                    && Path.iter().all(|PositionValue| {
                        self.Adjacency.contains_key(PositionValue)
                            && (!BlockedNodes.contains(PositionValue)
                                || UnblockedAdditionalNodes.contains(PositionValue))
                            && IsPreparedRouteNodeAllowed(
                                Guide,
                                AdditionalAllowedNodes,
                                PositionValue,
                            )
                    })
            });
            let CertifiedRepeaterMap = Guide
                .CertifiedRepeaters
                .iter()
                .cloned()
                .collect::<HashMap<_, _>>();
            let CertifiedRepeaterComplete = CertifiedRepeaterMap.len()
                == Guide.CertifiedRepeaters.len()
                && CertifiedRepeaterMap.iter().all(|(PositionValue, Facing)| {
                    !ForbiddenRepeaterPositions.contains(PositionValue)
                        && matches!(Facing.as_str(), "west" | "east" | "north" | "south")
                });
            if std::env::var("RCS_DEBUG_CERTIFIED_WARM_SIGNAL")
                .ok()
                .is_some_and(|Signal| Signal == DebugLabel)
                && !(CertifiedGeometryComplete && CertifiedRepeaterComplete)
            {
                eprintln!(
                    "selected certified warm precheck signal={} geometry={} repeaters={} root={:?} path_starts={:?} blocked={:?} disallowed={:?}",
                    DebugLabel,
                    CertifiedGeometryComplete,
                    CertifiedRepeaterComplete,
                    Root,
                    Guide.CertifiedPaths.iter().filter_map(|Path| Path.first()).collect::<Vec<_>>(),
                    Guide.CertifiedPaths
                        .iter()
                        .flatten()
                        .filter(|PositionValue| BlockedNodes.contains(PositionValue))
                        .take(16)
                        .collect::<Vec<_>>(),
                    Guide.CertifiedPaths
                        .iter()
                        .flatten()
                        .filter(|PositionValue| !IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, PositionValue))
                        .take(16)
                        .collect::<Vec<_>>(),
                );
            }
            if CertifiedGeometryComplete && CertifiedRepeaterComplete {
                let CertifiedNodes = Guide
                    .CertifiedPaths
                    .iter()
                    .flatten()
                    .copied()
                    .chain(FrozenReservedAccessNodes.iter().copied())
                    .collect::<HashSet<_>>();
                let CertifiedRepeaterValues = Guide.CertifiedRepeaters.clone();
                let CertifiedPowers = PropagateCanonicalRoutePower(
                    Root,
                    &CertifiedNodes,
                    &CertifiedRepeaterMap,
                    &self.Adjacency,
                );
                let RequiredTargetsPowered = TargetBranches
                    .iter()
                    .filter_map(|Branch| Branch.last())
                    .all(|Target| CertifiedPowers.contains_key(Target));
                let FrozenClaimsPresent = FrozenReservedAccessNodes
                    .iter()
                    .all(|PositionValue| CertifiedNodes.contains(PositionValue));
                let NoSelfExcitingCycle = FindSelfExcitingRepeaterCycles(
                    &CertifiedNodes,
                    &CertifiedRepeaterValues,
                )
                .is_empty();
                if std::env::var("RCS_DEBUG_CERTIFIED_WARM_SIGNAL")
                    .ok()
                    .is_some_and(|Signal| Signal == DebugLabel)
                {
                    eprintln!(
                        "selected certified warm signal={} geometry={} repeaters={} powered={} frozen={} cycle_free={} root={:?} path_starts={:?} unpowered_targets={:?}",
                        DebugLabel,
                        CertifiedGeometryComplete,
                        CertifiedRepeaterComplete,
                        RequiredTargetsPowered,
                        FrozenClaimsPresent,
                        NoSelfExcitingCycle,
                        Root,
                        Guide.CertifiedPaths.iter().filter_map(|Path| Path.first()).collect::<Vec<_>>(),
                        TargetBranches
                            .iter()
                            .filter_map(|Branch| Branch.last())
                            .filter(|Target| !CertifiedPowers.contains_key(Target))
                            .collect::<Vec<_>>(),
                    );
                }
                if RequiredTargetsPowered && FrozenClaimsPresent && NoSelfExcitingCycle {
                    let mut Nodes = CertifiedNodes.into_iter().collect::<Vec<_>>();
                    Nodes.sort_unstable();
                    let mut RepeaterReservations = CertifiedRepeaterValues;
                    RepeaterReservations.sort_unstable();
                    return RouteTreeSearchResult {
                        Status: "Routed".to_string(),
                        NoPathReason: String::new(),
                        Nodes,
                        TargetPaths: Guide
                            .CertifiedPaths
                            .iter()
                            .filter_map(|Path| {
                                Path.last().copied().map(|Target| (Target, Path.clone()))
                            })
                            .collect(),
                        BoundaryFrontierNodes: Vec::new(),
                        RepeaterReservations,
                        ExpansionCount: Guide
                            .CertifiedPaths
                            .iter()
                            .map(Vec::len)
                            .sum(),
                        RepeaterRejectedCount: 0,
                        RepeaterConstraintFailureCount: 0,
                        ConflictResources: Vec::new(),
                        RejectedPathCount: 0,
                        NoGoodCount: 0,
                        ElapsedMilliseconds: 0,
                        IsRouted: true,
                        IsBudgetExpired: false,
                    };
                }
            }
        }

        let RouteIntoTree = |Target: Position,
                             TargetContinuation: &[Position],
                             ReservedNodes: &HashSet<Position>,
                             TreeValue: &HashSet<Position>,
                             States: &HashMap<Position, SearchState>,
                             ExistingRepeaters: &HashMap<Position, String>|
         -> Option<crate::PathRouting::PathSearchResult> {
            let (mut DynamicBlocked, EdgeCosts) = BuildRootedTreeBlockages(
                self,
                Guide,
                AdditionalAllowedNodes,
                &BlockedNodes,
                TreeValue,
                ReservedNodes,
                &Deadline,
            )?;
            // An already committed repeater is a directed component, not a
            // bidirectional dust node.  A later branch may attach to powered
            // dust on either side, but it must not traverse the repeater as
            // ordinary wire and silently reverse its electrical direction.
            DynamicBlocked.extend(ExistingRepeaters.keys().copied());
            // Frozen access branches are likewise exact directed terminal
            // geometry.  They remain physical claims, but global routing may
            // enter only at the selected portal instead of using a branch
            // interior as a shortcut in the opposite power direction.
            DynamicBlocked.extend(ReservedNodes.iter().copied());
            DynamicBlocked.remove(&Target);
            let mut StartStates: Vec<_> = if EnforceSignalStrength {
                States.values().copied().collect()
            } else {
                TreeValue
                    .iter()
                    .copied()
                    .map(|Value| (Value, (0, 0, 0), MAXIMUM_UNREFRESHED_DUST_LENGTH))
                    .collect()
            };
            StartStates.sort_unstable();
            let mut ConsumedExpansionCount = 0usize;
            if FrozenSourceBranch.is_some() {
                let mut GeometryStartStates = StartStates.clone();
                let mut RefreshLaunchByState =
                    HashMap::<SearchState, (SearchState, SearchState, String)>::new();
                for StartState in &StartStates {
                    if StartState.2 <= 1 {
                        continue;
                    }
                    for First in self
                        .Adjacency
                        .get(&StartState.0)
                        .into_iter()
                        .flatten()
                        .copied()
                    {
                        let Direction = (
                            First.0 - StartState.0 .0,
                            First.1 - StartState.0 .1,
                            First.2 - StartState.0 .2,
                        );
                        if Direction.1 != 0 || Direction.0.abs() + Direction.2.abs() != 1 {
                            continue;
                        }
                        let Second = (First.0 + Direction.0, First.1, First.2 + Direction.2);
                        if DynamicBlocked.contains(&First)
                            || DynamicBlocked.contains(&Second)
                            || TreeValue.contains(&First)
                            || TreeValue.contains(&Second)
                            || ReservedNodes.contains(&First)
                            || ReservedNodes.contains(&Second)
                            || ForbiddenRepeaterPositions.contains(&First)
                            || !IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, &First)
                            || !IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, &Second)
                            || !self
                                .Adjacency
                                .get(&First)
                                .is_some_and(|Neighbors| Neighbors.contains(&Second))
                            || EdgeCosts
                                .get(&NormalizeEdge(StartState.0, First))
                                .copied()
                                .unwrap_or(0)
                                >= BLOCKED_EDGE_COST
                            || EdgeCosts
                                .get(&NormalizeEdge(First, Second))
                                .copied()
                                .unwrap_or(0)
                                >= BLOCKED_EDGE_COST
                        {
                            continue;
                        }
                        let FirstState = (First, Direction, StartState.2.saturating_sub(1));
                        let SecondState = (Second, Direction, MAXIMUM_UNREFRESHED_DUST_LENGTH);
                        let Some(Facing) = RepeaterFacing(First, Second) else {
                            continue;
                        };
                        GeometryStartStates.push(SecondState);
                        RefreshLaunchByState.entry(SecondState).or_insert((
                            *StartState,
                            FirstState,
                            Facing,
                        ));
                    }
                }
                GeometryStartStates.sort_unstable();
                GeometryStartStates.dedup();
                if let Some(mut GeometryResult) = FindPathFromStatesDetailedWithDeadline(
                    &self.Adjacency,
                    Guide.UseColumnMembership.then_some(&Guide.AllowedColumns),
                    Some(AdditionalAllowedNodes),
                    &GeometryStartStates,
                    Target,
                    PreferredRoutingY,
                    &DynamicBlocked,
                    &Guide.NodeCosts,
                    &AdditionalNodeCosts,
                    &Guide.ColumnCosts,
                    &EdgeCosts,
                    BendPenalty,
                    ViaPenalty,
                    0,
                    MaximumExpansionCount.div_ceil(8).max(1),
                    false,
                    ForbiddenRepeaterPositions,
                    TargetContinuation,
                    0,
                    &Deadline,
                ) {
                    ConsumedExpansionCount = GeometryResult.ExpansionCount;
                    if GeometryResult.Status == "Routed" {
                        let mut PoweredStatePath = Vec::new();
                        let mut GeometryRepeaters = Vec::new();
                        let mut GeometryPowerLegal =
                            GeometryResult.StatePath.first().is_some_and(|State| {
                                if let Some((OriginalState, FirstState, Facing)) =
                                    RefreshLaunchByState.get(State)
                                {
                                    PoweredStatePath.extend([*OriginalState, *FirstState, *State]);
                                    GeometryRepeaters.push((FirstState.0, Facing.clone()));
                                    true
                                } else if let Some(OriginalState) = States.get(&State.0).copied() {
                                    PoweredStatePath.push(OriginalState);
                                    true
                                } else {
                                    false
                                }
                            });
                        for GeometryIndex in 1..GeometryResult.StatePath.len() {
                            if !GeometryPowerLegal {
                                break;
                            }
                            let CurrentState = *PoweredStatePath
                                .last()
                                .expect("powered geometry path has a start");
                            let Next = GeometryResult.StatePath[GeometryIndex].0;
                            let Direction = (
                                Next.0 - CurrentState.0 .0,
                                Next.1 - CurrentState.0 .1,
                                Next.2 - CurrentState.0 .2,
                            );
                            let CanRefreshCurrent = !TreeValue.contains(&CurrentState.0)
                                && CurrentState.1 != (0, 0, 0)
                                && CurrentState.1 == Direction
                                && Direction.1 == 0
                                && CurrentState.2 <= crate::PathRouting::REPEATER_TURN_HEADROOM
                                && !ForbiddenRepeaterPositions.contains(&CurrentState.0);
                            let CanRefreshNext = GeometryResult
                                .StatePath
                                .get(GeometryIndex + 1)
                                .map(|AfterState| {
                                    let After = AfterState.0;
                                    let NextDirection =
                                        (After.0 - Next.0, After.1 - Next.1, After.2 - Next.2);
                                    !TreeValue.contains(&Next)
                                        && Direction == NextDirection
                                        && Direction.1 == 0
                                        && !ForbiddenRepeaterPositions.contains(&Next)
                                })
                                .unwrap_or(false);
                            let RemainingStrength = if CanRefreshCurrent {
                                let Some(Facing) = RepeaterFacing(CurrentState.0, Next) else {
                                    GeometryPowerLegal = false;
                                    break;
                                };
                                GeometryRepeaters.push((CurrentState.0, Facing));
                                MAXIMUM_UNREFRESHED_DUST_LENGTH
                            } else if CurrentState.2 > 1 {
                                CurrentState.2 - 1
                            } else if CanRefreshNext {
                                let After = GeometryResult.StatePath[GeometryIndex + 1].0;
                                let Some(Facing) = RepeaterFacing(Next, After) else {
                                    GeometryPowerLegal = false;
                                    break;
                                };
                                GeometryRepeaters.push((Next, Facing));
                                MAXIMUM_UNREFRESHED_DUST_LENGTH
                            } else {
                                GeometryPowerLegal = false;
                                break;
                            };
                            PoweredStatePath.push((Next, Direction, RemainingStrength));
                        }
                        if !GeometryPowerLegal
                            && std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                        {
                            eprintln!(
                                "selected geometry power handoff signal={} geometry={:?} powered_prefix={:?} repeaters={:?}",
                                DebugLabel,
                                GeometryResult.StatePath,
                                PoweredStatePath,
                                GeometryRepeaters,
                            );
                        }
                        if GeometryPowerLegal {
                            GeometryResult.StatePath = PoweredStatePath;
                            GeometryResult.RepeaterReservations = GeometryRepeaters;
                            return Some(GeometryResult);
                        }
                    }
                }
            }
            let mut Result = FindPathFromStatesDetailedWithDeadline(
                &self.Adjacency,
                Guide.UseColumnMembership.then_some(&Guide.AllowedColumns),
                Some(AdditionalAllowedNodes),
                &StartStates,
                Target,
                PreferredRoutingY,
                &DynamicBlocked,
                &Guide.NodeCosts,
                &AdditionalNodeCosts,
                &Guide.ColumnCosts,
                &EdgeCosts,
                BendPenalty,
                ViaPenalty,
                0,
                MaximumExpansionCount.saturating_sub(ConsumedExpansionCount),
                EnforceSignalStrength,
                ForbiddenRepeaterPositions,
                TargetContinuation,
                0,
                &Deadline,
            );
            if let Some(Value) = Result.as_mut() {
                Value.ExpansionCount = Value.ExpansionCount.saturating_add(ConsumedExpansionCount);
            }
            Result
        };

        let FrozenSourceRepairAllowedNodes = FrozenSourceBranch.map(|_| {
            AdditionalAllowedNodes
                .iter()
                .copied()
                .filter(|Candidate| {
                    Starts.iter().any(|SourceNode| {
                        (Candidate.0 - SourceNode.0).abs() + (Candidate.2 - SourceNode.2).abs()
                            <= i32::from(crate::PathRouting::REPEATER_TURN_HEADROOM)
                    })
                })
                .collect::<HashSet<_>>()
        });
        let RouteFrozenSourceIntoTree = |Target: Position,
                                         TreeValue: &HashSet<Position>,
                                         States: &HashMap<Position, SearchState>,
                                         ExistingRepeaters: &HashMap<Position, String>|
         -> Option<crate::PathRouting::PathSearchResult> {
            let SourceAllowed = FrozenSourceRepairAllowedNodes.as_ref()?;
            let (mut DynamicBlocked, EdgeCosts) = BuildRootedTreeBlockages(
                self,
                Guide,
                AdditionalAllowedNodes,
                &BlockedNodes,
                TreeValue,
                &FrozenReservedAccessNodes,
                &Deadline,
            )?;
            DynamicBlocked.remove(&Target);
            for (Index, Value) in SourceAllowed.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                    if !SourceAllowed.contains(Neighbor) {
                        DynamicBlocked.insert(*Neighbor);
                    }
                }
            }
            let SourceNodeCosts = SourceAllowed
                .iter()
                .map(|Candidate| {
                    let Distance = Starts
                        .iter()
                        .map(|SourceNode| {
                            (Candidate.0 - SourceNode.0).abs() + (Candidate.2 - SourceNode.2).abs()
                        })
                        .min()
                        .unwrap_or(0);
                    (*Candidate, Distance * BendPenalty.max(1))
                })
                .collect::<HashMap<_, _>>();
            // Every already powered state in the bounded source-repair
            // region is an exact legal launch frontier.  Retaining only the
            // geometrically nearest state loses witnesses when that state
            // reaches a diagonal access rise with strength one while a
            // slightly farther straight state can legally launch a
            // refresher.  Keep the complete deterministic frontier; the
            // native path search still shares this request's unchanged work
            // cap and absolute deadline.
            let mut StartStates = States
                .values()
                .copied()
                .filter(|State| !ExistingRepeaters.contains_key(&State.0))
                .collect::<Vec<_>>();
            StartStates.sort_unstable();
            StartStates.dedup();
            DynamicBlocked.extend(TreeValue.iter().copied());
            DynamicBlocked.extend(FrozenReservedAccessNodes.iter().copied());
            if let Some(SourceBranch) = FrozenSourceBranch {
                for PositionValue in SourceBranch {
                    DynamicBlocked.remove(PositionValue);
                }
            }
            for StartState in &StartStates {
                DynamicBlocked.remove(&StartState.0);
            }
            DynamicBlocked.remove(&Target);
            FindPathFromStatesDetailedWithDeadline(
                &self.Adjacency,
                None,
                Some(SourceAllowed),
                &StartStates,
                Target,
                PreferredRoutingY,
                &DynamicBlocked,
                &HashMap::new(),
                &SourceNodeCosts,
                &HashMap::new(),
                &EdgeCosts,
                BendPenalty,
                ViaPenalty,
                0,
                MaximumExpansionCount,
                EnforceSignalStrength,
                ForbiddenRepeaterPositions,
                &[],
                0,
                &Deadline,
            )
        };

        let RouteFrozenTargetIntoTree = |Branch: &[Position],
                                         TreeValue: &HashSet<Position>,
                                         States: &HashMap<Position, SearchState>,
                                         ExistingRepeaters: &HashMap<Position, String>,
                                         RepairRadius: i32,
                                         PoweredStartValues: Option<&HashMap<Position, u8>>,
                                         LocalMaximumExpansionCount: usize|
         -> Option<crate::PathRouting::PathSearchResult> {
            let Target = Branch.last().copied()?;
            let TargetAllowed = AdditionalAllowedNodes
                .iter()
                .copied()
                .filter(|Candidate| {
                    Branch.iter().any(|BranchNode| {
                        (Candidate.0 - BranchNode.0).abs()
                            + (Candidate.1 - BranchNode.1).abs()
                            + (Candidate.2 - BranchNode.2).abs()
                            <= RepairRadius
                    })
                })
                .collect::<HashSet<_>>();
            let Some((mut DynamicBlocked, EdgeCosts)) = BuildRootedTreeBlockages(
                self,
                Guide,
                AdditionalAllowedNodes,
                &BlockedNodes,
                TreeValue,
                &HashSet::new(),
                &Deadline,
            ) else {
                return None;
            };
            // Existing tree nodes are authoritative powered launch states,
            // not undirected interior dust.  Keep them blocked so a path
            // cannot enter a second frontier with a different canonical
            // incoming direction or cross an existing repeater backwards.
            // The path kernel permits a blocked state to launch while
            // rejecting later traversal into it.
            DynamicBlocked.extend(TreeValue.iter().copied());
            DynamicBlocked.remove(&Target);
            for (Index, Value) in TargetAllowed.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                    if !TargetAllowed.contains(Neighbor) && !TreeValue.contains(Neighbor) {
                        DynamicBlocked.insert(*Neighbor);
                    }
                }
            }
            let mut StartStates = States
                .values()
                .copied()
                .filter_map(|mut State| {
                    // A repeater body is not an undirected launch node.  Its
                    // powered output neighbor is already represented by its
                    // own state; starting a new path from the repeater body
                    // can otherwise leave through the wrong face and produce
                    // a route that the canonical directed power audit rightly
                    // rejects.
                    if ExistingRepeaters.contains_key(&State.0) {
                        return None;
                    }
                    if let Some(PoweredValues) = PoweredStartValues {
                        State.2 = PoweredValues.get(&State.0).copied()?;
                    }
                    (TargetAllowed.contains(&State.0)
                        || self.Adjacency.get(&State.0).is_some_and(|Neighbors| {
                            Neighbors.iter().any(|Value| TargetAllowed.contains(Value))
                        }))
                    .then_some(State)
                })
                .collect::<Vec<_>>();
            StartStates.sort_unstable();
            let TargetNodeCosts = TargetAllowed
                .iter()
                .map(|Candidate| {
                    let Distance = Branch
                        .iter()
                        .map(|BranchNode| {
                            (Candidate.0 - BranchNode.0).abs()
                                + (Candidate.1 - BranchNode.1).abs()
                                + (Candidate.2 - BranchNode.2).abs()
                        })
                        .min()
                        .unwrap_or(0);
                    (*Candidate, Distance * BendPenalty.max(1))
                })
                .collect::<HashMap<_, _>>();
            let Result = FindPathFromStatesDetailedWithDeadline(
                &self.Adjacency,
                None,
                Some(&TargetAllowed),
                &StartStates,
                Target,
                PreferredRoutingY,
                &DynamicBlocked,
                &Guide.NodeCosts,
                &TargetNodeCosts,
                &Guide.ColumnCosts,
                &EdgeCosts,
                BendPenalty,
                ViaPenalty,
                BendPenalty.max(1),
                LocalMaximumExpansionCount,
                EnforceSignalStrength,
                ForbiddenRepeaterPositions,
                &[],
                0,
                &Deadline,
            );
            Result
        };

        // A factorized access payload contains immutable terminal-to-ingress
        // geometry.  Those claims must survive materialization, but they are
        // not independently powered roots.  Route once from the real source
        // to the selected ingress so the native search can use the exact
        // prefix when it is legal or add one powered bypass when it is not.
        // Retain the immutable access cells only after all powered branches
        // have been built, preventing an unpowered prefix cell from becoming
        // a fresh signal-strength seed.
        let RetainedMandatorySourceNodes = if FrozenSourceBranch.is_some() {
            Starts.iter().copied().collect::<HashSet<_>>()
        } else {
            HashSet::new()
        };
        let mut FrozenSourceFrontierState = None;
        if let Some(SourceBranch) = FrozenSourceBranch {
            if SourceBranch.first().copied() != Some(Root) {
                return Failure("NoPath", "NoPathGeometry", 0, 0, ExpansionCount);
            }
            let mut CurrentState = RootState;
            for (BranchIndex, Next) in SourceBranch.iter().copied().enumerate().skip(1) {
                if BlockedNodes.contains(&Next)
                    || !IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, &Next)
                    || !self
                        .Adjacency
                        .get(&CurrentState.0)
                        .is_some_and(|Neighbors| Neighbors.contains(&Next))
                {
                    return Failure("NoPath", "NoPathGeometry", 0, 0, ExpansionCount);
                }
                let Direction = (
                    Next.0 - CurrentState.0 .0,
                    Next.1 - CurrentState.0 .1,
                    Next.2 - CurrentState.0 .2,
                );
                let CanPhysicallyRefreshCurrent = CurrentState.1 != StartDirection
                    && CurrentState.1 == Direction
                    && Direction.1 == 0
                    && !ForbiddenRepeaterPositions.contains(&CurrentState.0);
                // A long diagonal or turn can leave no legal repeater site
                // after the current straight cell.  Waiting only for the
                // ordinary low-strength threshold then strands an otherwise
                // exact frozen source branch at strength one.  Refresh at the
                // last usable straight cell precisely when the current power
                // cannot reach the next usable site (or the ingress).  This
                // is the source-directed counterpart of the immutable target
                // branch look-ahead below.
                let CurrentBranchIndex = BranchIndex - 1;
                let NextRefreshDistance = (BranchIndex..SourceBranch.len().saturating_sub(1))
                    .find_map(|CandidateIndex| {
                        let Before = SourceBranch[CandidateIndex - 1];
                        let Candidate = SourceBranch[CandidateIndex];
                        let After = SourceBranch[CandidateIndex + 1];
                        let Incoming = (
                            Candidate.0 - Before.0,
                            Candidate.1 - Before.1,
                            Candidate.2 - Before.2,
                        );
                        let Outgoing = (
                            After.0 - Candidate.0,
                            After.1 - Candidate.1,
                            After.2 - Candidate.2,
                        );
                        if Incoming != Outgoing
                            || Incoming.1 != 0
                            || ForbiddenRepeaterPositions.contains(&Candidate)
                        {
                            return None;
                        }
                        Some(CandidateIndex - CurrentBranchIndex)
                    })
                    .unwrap_or(SourceBranch.len() - 1 - CurrentBranchIndex);
                let MustRefreshCurrent = CurrentState.2
                    <= crate::PathRouting::REPEATER_TURN_HEADROOM
                    || NextRefreshDistance >= usize::from(CurrentState.2);
                let CanRefreshCurrent = CanPhysicallyRefreshCurrent && MustRefreshCurrent;
                let CanRefreshNext = SourceBranch
                    .get(BranchIndex + 1)
                    .map(|After| {
                        let NextDirection = (
                            After.0 - Next.0,
                            After.1 - Next.1,
                            After.2 - Next.2,
                        );
                        Direction == NextDirection
                            && Direction.1 == 0
                            && !ForbiddenRepeaterPositions.contains(&Next)
                    })
                    .unwrap_or(false);
                let RemainingStrength = if CanRefreshCurrent {
                    let Some(Facing) = RepeaterFacing(CurrentState.0, Next) else {
                        return Failure("NoPath", "NoPathGeometry", 1, 0, ExpansionCount);
                    };
                    Repeaters.insert(CurrentState.0, Facing);
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else if CurrentState.2 > 1 {
                    CurrentState.2 - 1
                } else if CanRefreshNext {
                    let Some(After) = SourceBranch.get(BranchIndex + 1).copied() else {
                        break;
                    };
                    let Some(Facing) = RepeaterFacing(Next, After) else {
                        return Failure("NoPath", "NoPathGeometry", 1, 0, ExpansionCount);
                    };
                    Repeaters.insert(Next, Facing);
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else {
                    break;
                };
                let NextState = (Next, Direction, RemainingStrength);
                ParentByNode.insert(Next, CurrentState.0);
                Tree.insert(Next);
                StateByNode.insert(Next, NextState);
                CurrentState = NextState;
            }
            FrozenSourceFrontierState = Some(CurrentState);
        }
        if let (Some(SourceIngress), Some(FrontierState), Some(SourceAllowed)) = (
            FrozenSourceBranch.and_then(|Branch| Branch.last().copied()),
            FrozenSourceFrontierState,
            FrozenSourceRepairAllowedNodes.as_ref(),
        ) {
            if FrontierState.0 != SourceIngress
                && FrontierState.2 <= crate::PathRouting::REPEATER_TURN_HEADROOM.saturating_add(1)
            {
                let Some((DynamicBlocked, EdgeCosts)) = BuildRootedTreeBlockages(
                    self,
                    Guide,
                    AdditionalAllowedNodes,
                    &BlockedNodes,
                    &Tree,
                    &FrozenReservedAccessNodes,
                    &Deadline,
                ) else {
                    return Failure("NoPath", "NoPathGeometry", 0, 0, ExpansionCount);
                };
                let mut RepeaterLaneCandidates = Vec::new();
                for First in self
                    .Adjacency
                    .get(&FrontierState.0)
                    .into_iter()
                    .flatten()
                    .copied()
                {
                    let Direction = (
                        First.0 - FrontierState.0 .0,
                        First.1 - FrontierState.0 .1,
                        First.2 - FrontierState.0 .2,
                    );
                    if Direction.1 != 0
                        || Direction == (0, 0, 0)
                        || Direction.0.abs() + Direction.2.abs() != 1
                    {
                        continue;
                    }
                    let Second = (First.0 + Direction.0, First.1, First.2 + Direction.2);
                    if !SourceAllowed.contains(&First)
                        || !SourceAllowed.contains(&Second)
                        || DynamicBlocked.contains(&First)
                        || DynamicBlocked.contains(&Second)
                        || Tree.contains(&First)
                        || Tree.contains(&Second)
                        || FrozenReservedAccessNodes.contains(&First)
                        || FrozenReservedAccessNodes.contains(&Second)
                        || ForbiddenRepeaterPositions.contains(&First)
                        || !self
                            .Adjacency
                            .get(&First)
                            .is_some_and(|Neighbors| Neighbors.contains(&Second))
                        || EdgeCosts
                            .get(&NormalizeEdge(FrontierState.0, First))
                            .copied()
                            .unwrap_or(0)
                            >= BLOCKED_EDGE_COST
                        || EdgeCosts
                            .get(&NormalizeEdge(First, Second))
                            .copied()
                            .unwrap_or(0)
                            >= BLOCKED_EDGE_COST
                    {
                        continue;
                    }
                    RepeaterLaneCandidates.push((
                        ManhattanDistance(Second, SourceIngress),
                        First,
                        Second,
                        Direction,
                    ));
                }
                RepeaterLaneCandidates.sort_unstable();
                if let Some((_Distance, First, Second, Direction)) =
                    RepeaterLaneCandidates.into_iter().next()
                {
                    let FirstState = (First, Direction, FrontierState.2.saturating_sub(1));
                    let SecondState = (Second, Direction, MAXIMUM_UNREFRESHED_DUST_LENGTH);
                    ParentByNode.insert(First, FrontierState.0);
                    ParentByNode.insert(Second, First);
                    Tree.insert(First);
                    Tree.insert(Second);
                    StateByNode.insert(First, FirstState);
                    StateByNode.insert(Second, SecondState);
                    let Some(Facing) = RepeaterFacing(First, Second) else {
                        return Failure("NoPath", "NoPathGeometry", 1, 0, ExpansionCount);
                    };
                    Repeaters.insert(First, Facing);
                }
            }
        }
        let StartsToConnect = if let Some(SourceBranch) = FrozenSourceBranch {
            SourceBranch.last().copied().into_iter().collect::<Vec<_>>()
        } else {
            Starts.iter().copied().skip(1).collect::<Vec<_>>()
        };
        for Start in StartsToConnect {
            if Tree.contains(&Start) {
                continue;
            }
            let Result = if FrozenSourceBranch.is_some() {
                RouteFrozenSourceIntoTree(Start, &Tree, &StateByNode, &Repeaters)
            } else {
                RouteIntoTree(Start, &[], &HashSet::new(), &Tree, &StateByNode, &Repeaters)
            };
            let Some(Result) = Result else {
                return Failure(
                    "NoPath",
                    "NoPathGeometry",
                    usize::from(EnforceSignalStrength),
                    0,
                    ExpansionCount,
                );
            };
            if Result.Status != "Routed" {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected source ingress failure signal={} target={:?} status={} reason={} expansions={} frontier={:?}",
                        DebugLabel,
                        Start,
                        Result.Status,
                        Result.NoPathReason,
                        Result.ExpansionCount,
                        FrozenSourceFrontierState,
                    );
                }
                return Failure(
                    "NoPath",
                    if Result.NoPathReason.is_empty() {
                        "NoPathGeometry"
                    } else {
                        &Result.NoPathReason
                    },
                    Result.RepeaterRejectedCount,
                    Result.RepeaterConstraintFailures,
                    ExpansionCount + Result.ExpansionCount,
                );
            };
            ExpansionCount += Result.ExpansionCount;
            for Values in Result.StatePath.windows(2) {
                let Previous = Values[0].0;
                let Current = Values[1].0;
                if Tree.insert(Current) {
                    ParentByNode.insert(Current, Previous);
                }
                StateByNode.insert(Current, Values[1]);
            }
            for (PositionValue, Facing) in Result.RepeaterReservations {
                Repeaters.entry(PositionValue).or_insert(Facing);
            }
        }

        // Some immutable target-side claim fragments are already connected
        // to the real source through their exact frozen geometry.  Python
        // deliberately omits those branches from TargetBranches, because
        // no new route is required.  Import their exact root component here
        // instead of leaving their portal cells reserved-but-unpowered.
        // Only powered branch portals become global attachment frontiers;
        // arbitrary access interiors remain directed immutable geometry.
        let mut RootedFrozenPortalNodes = HashSet::new();
        if FrozenSourceBranch.is_some() {
            let ImmutableNodes = Tree
                .iter()
                .copied()
                .chain(FrozenTargetBranches.iter().flatten().copied())
                .collect::<HashSet<_>>();
            let mut RootedNodes = HashSet::from([Root]);
            let mut RootedParent = HashMap::<Position, Position>::new();
            let mut Pending = VecDeque::from([Root]);
            while let Some(Current) = Pending.pop_front() {
                let mut Neighbors = self
                    .Adjacency
                    .get(&Current)
                    .into_iter()
                    .flatten()
                    .copied()
                    .filter(|Value| ImmutableNodes.contains(Value))
                    .collect::<Vec<_>>();
                Neighbors.sort_unstable();
                for Neighbor in Neighbors {
                    if RootedNodes.insert(Neighbor) {
                        RootedParent.insert(Neighbor, Current);
                        Pending.push_back(Neighbor);
                    }
                }
            }
            for PositionValue in RootedNodes.iter().copied() {
                if Tree.insert(PositionValue) {
                    if let Some(Previous) = RootedParent.get(&PositionValue).copied() {
                        ParentByNode.insert(PositionValue, Previous);
                    }
                }
            }
            let RootedPowers =
                PropagateCanonicalRoutePower(Root, &RootedNodes, &Repeaters, &self.Adjacency);
            for PositionValue in RootedNodes.iter().copied() {
                let Some(Power) = RootedPowers.get(&PositionValue).copied() else {
                    continue;
                };
                let State = if PositionValue == Root {
                    RootState
                } else if let Some(Previous) = RootedParent.get(&PositionValue).copied() {
                    (
                        PositionValue,
                        (
                            PositionValue.0 - Previous.0,
                            PositionValue.1 - Previous.1,
                            PositionValue.2 - Previous.2,
                        ),
                        Power,
                    )
                } else {
                    continue;
                };
                StateByNode.insert(PositionValue, State);
            }
            for Branch in FrozenTargetBranches {
                if let Some(Portal) = Branch.first().copied() {
                    if RootedPowers.contains_key(&Portal) {
                        RootedFrozenPortalNodes.insert(Portal);
                    }
                }
            }
        }

        let mut TargetPaths = Vec::new();
        // Frozen access geometry is immutable signal ownership, but only the
        // selected ingress is a legal launch point for global routing.  Keep
        // the powered stub in the physical tree while excluding its interior
        // nodes (and later target-stub interiors) from the global attachment
        // frontier.
        let mut GlobalRoutingNodes = if FrozenSourceBranch.is_some() {
            let mut Values = FrozenSourceBranch
                .and_then(|Branch| Branch.last().copied())
                .into_iter()
                .collect::<HashSet<_>>();
            Values.extend(RootedFrozenPortalNodes.iter().copied());
            Values
        } else {
            Tree.clone()
        };
        let RetainedMandatoryTargetNodes = if FrozenSourceBranch.is_some() {
            FrozenTargetBranches
                .iter()
                .flatten()
                .copied()
                .collect::<HashSet<_>>()
        } else {
            HashSet::new()
        };
        let mut RemainingBranches = TargetBranches.to_vec();
        while !RemainingBranches.is_empty() {
            let Some(SelectedIndex) = RemainingBranches
                .iter()
                .enumerate()
                .min_by_key(|(_, Branch)| {
                    let Terminal = Branch
                        .last()
                        .copied()
                        .unwrap_or((i32::MAX, i32::MAX, i32::MAX));
                    let Attachment = if FrozenSourceBranch.is_some() {
                        Branch.first().copied().unwrap_or(Terminal)
                    } else {
                        Terminal
                    };
                    let Distance = GlobalRoutingNodes
                        .iter()
                        .map(|Start| ManhattanDistance(*Start, Attachment))
                        .min()
                        .unwrap_or(i32::MAX);
                    (Distance, Attachment, Terminal, Branch.len())
                })
                .map(|(Index, _)| Index)
            else {
                return Failure("NoPath", "NoPathGeometry", 0, 0, ExpansionCount);
            };
            let Branch = RemainingBranches.remove(SelectedIndex);
            let PortalTarget = Branch[0];
            let ReservedNodes: HashSet<_> = if FrozenSourceBranch.is_some() {
                FrozenReservedAccessNodes.clone()
            } else {
                Branch.iter().copied().collect()
            };
            let EarlyRepairRadius = i32::from(crate::PathRouting::REPEATER_TURN_HEADROOM);
            let EarlyTargetAllowed = AdditionalAllowedNodes
                .iter()
                .copied()
                .filter(|Candidate| {
                    Branch.iter().any(|BranchNode| {
                        ManhattanDistance(*Candidate, *BranchNode) <= EarlyRepairRadius
                    })
                })
                .collect::<HashSet<_>>();
            let EarlyPoweredFrontierCount = StateByNode
                .values()
                .filter(|State| {
                    EarlyTargetAllowed.contains(&State.0)
                        || self.Adjacency.get(&State.0).is_some_and(|Neighbors| {
                            Neighbors
                                .iter()
                                .any(|Value| EarlyTargetAllowed.contains(Value))
                        })
                })
                .count();
            if FrozenSourceBranch.is_none() && EarlyPoweredFrontierCount >= 3 {
                if let Some(LocalResult) =
                    RouteFrozenTargetIntoTree(
                        &Branch,
                        &Tree,
                        &StateByNode,
                        &Repeaters,
                        EarlyRepairRadius,
                        None,
                        MaximumExpansionCount.saturating_sub(ExpansionCount),
                    )
                {
                    ExpansionCount = ExpansionCount.saturating_add(LocalResult.ExpansionCount);
                    if LocalResult.Status == "Routed" {
                        for Values in LocalResult.StatePath.windows(2) {
                            let Previous = Values[0].0;
                            let Current = Values[1].0;
                            if Tree.insert(Current) {
                                ParentByNode.insert(Current, Previous);
                            }
                            StateByNode.insert(Current, Values[1]);
                        }
                        for (PositionValue, Facing) in LocalResult.RepeaterReservations {
                            Repeaters.entry(PositionValue).or_insert(Facing);
                        }
                        let Target = Branch
                            .last()
                            .copied()
                            .expect("frozen target branch is nonempty");
                        let mut Path = vec![Target];
                        let mut Cursor = Target;
                        while Cursor != Root {
                            let Some(Previous) = ParentByNode.get(&Cursor).copied() else {
                                return Failure("NoPath", "NoPathGeometry", 0, 0, ExpansionCount);
                            };
                            Path.push(Previous);
                            Cursor = Previous;
                        }
                        Path.reverse();
                        TargetPaths.push((Target, Path));
                        continue;
                    }
                }
            }
            let PhysicalPowersBeforePortalAttachment = FrozenSourceBranch.map(|_| {
                let PhysicalNodes = Tree
                    .union(&FrozenReservedAccessNodes)
                    .copied()
                    .collect::<HashSet<_>>();
                PropagateCanonicalRoutePower(Root, &PhysicalNodes, &Repeaters, &self.Adjacency)
            });
            if !Tree.contains(&PortalTarget)
                || PhysicalPowersBeforePortalAttachment
                    .as_ref()
                    .is_some_and(|Values| !Values.contains_key(&PortalTarget))
            {
                // The selected access branch is immutable, so the portal
                // arrival state must have one coherent repeater continuation
                // through that exact branch.  Deferring this constraint until
                // the final audit admits cheap portal paths that can never
                // power their terminal and then spends the finite work share
                // rediscovering a different ingress.  The native path kernel
                // already validates the continuation without expanding its
                // fixed geometry into search states.
                let TargetContinuation = Branch.as_slice();
                let EligibleGlobalStates = StateByNode
                    .iter()
                    .filter_map(|(PositionValue, State)| {
                        if !GlobalRoutingNodes.contains(PositionValue)
                            || Repeaters.contains_key(PositionValue)
                        {
                            return None;
                        }
                        let mut PoweredState = *State;
                        if let Some(PhysicalPowers) = PhysicalPowersBeforePortalAttachment.as_ref()
                        {
                            PoweredState.2 = PhysicalPowers.get(PositionValue).copied()?;
                        }
                        Some((*PositionValue, PoweredState))
                    })
                    .collect::<HashMap<_, _>>();
                let Result = RouteIntoTree(
                    PortalTarget,
                    TargetContinuation,
                    &ReservedNodes,
                    &Tree,
                    &EligibleGlobalStates,
                    &Repeaters,
                );
                let Some(Result) = Result else {
                    if let Some(LocalResult) = FrozenSourceBranch.and_then(|_| {
                        RouteFrozenTargetIntoTree(
                            &Branch,
                            &Tree,
                            &StateByNode,
                            &Repeaters,
                            i32::from(crate::PathRouting::REPEATER_TURN_HEADROOM),
                            PhysicalPowersBeforePortalAttachment.as_ref(),
                            MaximumExpansionCount.saturating_sub(ExpansionCount),
                        )
                    }) {
                        ExpansionCount = ExpansionCount.saturating_add(LocalResult.ExpansionCount);
                        if LocalResult.Status == "Routed" {
                            for Values in LocalResult.StatePath.windows(2) {
                                let Previous = Values[0].0;
                                let Current = Values[1].0;
                                if Tree.insert(Current) {
                                    ParentByNode.insert(Current, Previous);
                                }
                                StateByNode.insert(Current, Values[1]);
                            }
                            for (PositionValue, Facing) in LocalResult.RepeaterReservations {
                                Repeaters.entry(PositionValue).or_insert(Facing);
                            }
                            let Target = Branch
                                .last()
                                .copied()
                                .expect("frozen target branch is nonempty");
                            let mut Path = vec![Target];
                            let mut Cursor = Target;
                            while Cursor != Root {
                                let Some(Previous) = ParentByNode.get(&Cursor).copied() else {
                                    return Failure(
                                        "NoPath",
                                        "NoPathGeometry",
                                        0,
                                        0,
                                        ExpansionCount,
                                    );
                                };
                                Path.push(Previous);
                                Cursor = Previous;
                            }
                            Path.reverse();
                            TargetPaths.push((Target, Path));
                            continue;
                        }
                    }
                    return Failure(
                        "NoPath",
                        "NoPathGeometry",
                        usize::from(EnforceSignalStrength),
                        0,
                        ExpansionCount,
                    );
                };
                if Result.Status != "Routed" {
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                        || std::env::var_os("RCS_DEBUG_SELECTED_WORLD_SUMMARY").is_some()
                    {
                        eprintln!(
                            "selected portal attachment failure signal={} portal={:?} status={} reason={} expansions={} eligible_starts={} tree_nodes={}",
                            DebugLabel,
                            PortalTarget,
                            Result.Status,
                            Result.NoPathReason,
                            Result.ExpansionCount,
                            EligibleGlobalStates.len(),
                            Tree.len(),
                        );
                    }
                    if let Some(LocalResult) = FrozenSourceBranch.and_then(|_| {
                        RouteFrozenTargetIntoTree(
                            &Branch,
                            &Tree,
                            &StateByNode,
                            &Repeaters,
                            i32::from(crate::PathRouting::REPEATER_TURN_HEADROOM),
                            PhysicalPowersBeforePortalAttachment.as_ref(),
                            MaximumExpansionCount.saturating_sub(ExpansionCount),
                        )
                    }) {
                        ExpansionCount = ExpansionCount.saturating_add(LocalResult.ExpansionCount);
                        if LocalResult.Status == "Routed" {
                            for Values in LocalResult.StatePath.windows(2) {
                                let Previous = Values[0].0;
                                let Current = Values[1].0;
                                if Tree.insert(Current) {
                                    ParentByNode.insert(Current, Previous);
                                }
                                StateByNode.insert(Current, Values[1]);
                            }
                            for (PositionValue, Facing) in LocalResult.RepeaterReservations {
                                Repeaters.entry(PositionValue).or_insert(Facing);
                            }
                            let Target = Branch
                                .last()
                                .copied()
                                .expect("frozen target branch is nonempty");
                            let mut Path = vec![Target];
                            let mut Cursor = Target;
                            while Cursor != Root {
                                let Some(Previous) = ParentByNode.get(&Cursor).copied() else {
                                    return Failure(
                                        "NoPath",
                                        "NoPathGeometry",
                                        0,
                                        0,
                                        ExpansionCount,
                                    );
                                };
                                Path.push(Previous);
                                Cursor = Previous;
                            }
                            Path.reverse();
                            TargetPaths.push((Target, Path));
                            continue;
                        }
                    }
                    return Failure(
                        "NoPath",
                        if Result.NoPathReason.is_empty() {
                            "NoPathGeometry"
                        } else {
                            &Result.NoPathReason
                        },
                        Result.RepeaterRejectedCount,
                        Result.RepeaterConstraintFailures,
                        ExpansionCount + Result.ExpansionCount,
                    );
                };
                ExpansionCount += Result.ExpansionCount;
                for Values in Result.StatePath.windows(2) {
                    let Previous = Values[0].0;
                    let Current = Values[1].0;
                    if Tree.insert(Current) {
                        ParentByNode.insert(Current, Previous);
                    }
                    StateByNode.insert(Current, Values[1]);
                    GlobalRoutingNodes.insert(Current);
                }
                for (PositionValue, Facing) in Result.RepeaterReservations {
                    Repeaters.entry(PositionValue).or_insert(Facing);
                }
            }

            // In the factorized selected world, connect every chosen portal
            // before placing repeaters on any immutable target branch.  A
            // refresher committed while powering the first branch must not
            // turn a shared portal junction into a directed obstacle for a
            // later branch.  The final frozen-branch phase below materializes
            // the exact terminal geometry after all portals are powered.
            if FrozenSourceBranch.is_some() {
                continue;
            }

            let mut CurrentState = *StateByNode.get(&PortalTarget).unwrap();
            let BranchContinuation = Branch
                .iter()
                .copied()
                .skip(1)
                .enumerate()
                .collect::<Vec<_>>();
            let mut BranchContinuationIncomplete = false;
            for (ContinuationIndex, Next) in BranchContinuation {
                let PhysicalNextPower = if FrozenSourceBranch.is_some() {
                    let PhysicalNodes = Tree
                        .union(&FrozenReservedAccessNodes)
                        .copied()
                        .collect::<HashSet<_>>();
                    let PhysicalPowers = PropagateCanonicalRoutePower(
                        Root,
                        &PhysicalNodes,
                        &Repeaters,
                        &self.Adjacency,
                    );
                    let Some(CurrentPower) = PhysicalPowers.get(&CurrentState.0).copied() else {
                        BranchContinuationIncomplete = true;
                        break;
                    };
                    CurrentState.2 = CurrentPower;
                    PhysicalPowers.get(&Next).copied()
                } else {
                    None
                };
                if Tree.contains(&Next) {
                    CurrentState = *StateByNode.get(&Next).unwrap();
                    if let Some(Power) = PhysicalNextPower {
                        CurrentState.2 = Power;
                    }
                    continue;
                }
                let Direction = (
                    Next.0 - CurrentState.0 .0,
                    Next.1 - CurrentState.0 .1,
                    Next.2 - CurrentState.0 .2,
                );
                let CanPlaceRepeater = CurrentState.1 != StartDirection
                    && CurrentState.1 == Direction
                    && Direction.1 == 0
                    && CurrentState.2 <= crate::PathRouting::REPEATER_TURN_HEADROOM;
                let RemainingStrength = if let Some(Power) =
                    PhysicalNextPower.filter(|Power| *Power > 0)
                {
                    Power
                } else if !EnforceSignalStrength {
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else if CanPlaceRepeater {
                    let Some(Facing) = RepeaterFacing(CurrentState.0, Next) else {
                        return Failure("NoPath", "NoPathGeometry", 1, 0, ExpansionCount);
                    };
                    Repeaters.entry(CurrentState.0).or_insert(Facing);
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else if CurrentState.2 > 1 {
                    CurrentState.2 - 1
                } else {
                    if FrozenSourceBranch.is_none() {
                        return Failure("NoPath", "NoRepeaterRepairPath", 1, 1, ExpansionCount);
                    }
                    let RepairEndIndex = ContinuationIndex + 1;
                    let RepairStartIndex = RepairEndIndex.saturating_sub(
                        usize::from(crate::PathRouting::REPEATER_TURN_HEADROOM) * 2,
                    );
                    let RepairBranch = &Branch[RepairStartIndex..=RepairEndIndex];
                    let RepairAllowed = AdditionalAllowedNodes
                        .iter()
                        .copied()
                        .filter(|Candidate| {
                            RepairBranch.iter().any(|BranchNode| {
                                (Candidate.0 - BranchNode.0).abs()
                                    + (Candidate.1 - BranchNode.1).abs()
                                    + (Candidate.2 - BranchNode.2).abs()
                                    <= i32::from(crate::PathRouting::REPEATER_TURN_HEADROOM)
                            })
                        })
                        .collect::<HashSet<_>>();
                    let Some((RepairBlocked, RepairEdgeCosts)) = BuildRootedTreeBlockages(
                        self,
                        Guide,
                        AdditionalAllowedNodes,
                        &BlockedNodes,
                        &Tree,
                        &FrozenReservedAccessNodes,
                        &Deadline,
                    ) else {
                        return Failure("NoPath", "NoPathGeometry", 1, 1, ExpansionCount);
                    };
                    let mut RepeaterLaneCandidates = Vec::new();
                    let mut RepairAnchorStates = RepairBranch
                        .iter()
                        .rev()
                        .filter_map(|PositionValue| StateByNode.get(PositionValue).copied())
                        .collect::<Vec<_>>();
                    RepairAnchorStates.dedup_by_key(|State| State.0);
                    for AnchorState in RepairAnchorStates {
                        for First in self
                            .Adjacency
                            .get(&AnchorState.0)
                            .into_iter()
                            .flatten()
                            .copied()
                        {
                            let LaneDirection = (
                                First.0 - AnchorState.0 .0,
                                First.1 - AnchorState.0 .1,
                                First.2 - AnchorState.0 .2,
                            );
                            if LaneDirection.1 != 0
                                || LaneDirection == (0, 0, 0)
                                || LaneDirection.0.abs() + LaneDirection.2.abs() != 1
                            {
                                continue;
                            }
                            let Second = (
                                First.0 + LaneDirection.0,
                                First.1,
                                First.2 + LaneDirection.2,
                            );
                            if !RepairAllowed.contains(&First)
                                || !RepairAllowed.contains(&Second)
                                || RepairBlocked.contains(&First)
                                || RepairBlocked.contains(&Second)
                                || Tree.contains(&First)
                                || Tree.contains(&Second)
                                || FrozenReservedAccessNodes.contains(&First)
                                || FrozenReservedAccessNodes.contains(&Second)
                                || ForbiddenRepeaterPositions.contains(&First)
                                || !self
                                    .Adjacency
                                    .get(&First)
                                    .is_some_and(|Neighbors| Neighbors.contains(&Second))
                                || RepairEdgeCosts
                                    .get(&NormalizeEdge(AnchorState.0, First))
                                    .copied()
                                    .unwrap_or(0)
                                    >= BLOCKED_EDGE_COST
                                || RepairEdgeCosts
                                    .get(&NormalizeEdge(First, Second))
                                    .copied()
                                    .unwrap_or(0)
                                    >= BLOCKED_EDGE_COST
                            {
                                continue;
                            }
                            RepeaterLaneCandidates.push((
                                ManhattanDistance(Second, Next),
                                ManhattanDistance(AnchorState.0, Next),
                                AnchorState,
                                First,
                                Second,
                                LaneDirection,
                            ));
                        }
                    }
                    RepeaterLaneCandidates.sort_unstable();
                    let mut SelectedRepair = None;
                    let mut LastRepairReason = "NoRepeater".to_string();
                    for (_Distance, _AnchorDistance, AnchorState, First, Second, LaneDirection) in
                        RepeaterLaneCandidates
                    {
                        if Deadline.Check() || ExpansionCount >= MaximumExpansionCount {
                            break;
                        }
                        let Some(Facing) = RepeaterFacing(First, Second) else {
                            continue;
                        };
                        let SecondState = (Second, LaneDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH);
                        let mut CandidateRepairBlocked = RepairBlocked.clone();
                        CandidateRepairBlocked.extend(Tree.iter().copied());
                        CandidateRepairBlocked.insert(First);
                        CandidateRepairBlocked.remove(&Second);
                        CandidateRepairBlocked.remove(&Next);
                        let Some(RepairResult) = FindPathFromStatesDetailedWithDeadline(
                            &self.Adjacency,
                            None,
                            Some(&RepairAllowed),
                            &[SecondState],
                            Next,
                            PreferredRoutingY,
                            &CandidateRepairBlocked,
                            &Guide.NodeCosts,
                            &HashMap::new(),
                            &Guide.ColumnCosts,
                            &RepairEdgeCosts,
                            BendPenalty,
                            ViaPenalty,
                            0,
                            MaximumExpansionCount.saturating_sub(ExpansionCount),
                            true,
                            ForbiddenRepeaterPositions,
                            &[],
                            0,
                            &Deadline,
                        ) else {
                            continue;
                        };
                        ExpansionCount += RepairResult.ExpansionCount;
                        if RepairResult.Status == "Routed" {
                            SelectedRepair = Some((
                                AnchorState,
                                First,
                                Second,
                                LaneDirection,
                                Facing,
                                SecondState,
                                RepairResult,
                            ));
                            break;
                        }
                        if !RepairResult.NoPathReason.is_empty() {
                            LastRepairReason = RepairResult.NoPathReason;
                        }
                    }
                    let Some((
                        AnchorState,
                        First,
                        Second,
                        LaneDirection,
                        Facing,
                        _SecondState,
                        RepairResult,
                    )) = SelectedRepair
                    else {
                        let _ = LastRepairReason;
                        BranchContinuationIncomplete = true;
                        break;
                    };
                    let FirstState = (First, LaneDirection, AnchorState.2.saturating_sub(1));
                    let SecondState = (Second, LaneDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH);
                    ParentByNode.insert(First, AnchorState.0);
                    ParentByNode.insert(Second, First);
                    Tree.insert(First);
                    Tree.insert(Second);
                    StateByNode.insert(First, FirstState);
                    StateByNode.insert(Second, SecondState);
                    Repeaters.insert(First, Facing);
                    for (PositionValue, RepeaterFacingValue) in &RepairResult.RepeaterReservations {
                        Repeaters
                            .entry(*PositionValue)
                            .or_insert_with(|| RepeaterFacingValue.clone());
                    }
                    let mut RepairedState = SecondState;
                    for GeometryState in RepairResult.StatePath.iter().skip(1) {
                        let RepairedNext = GeometryState.0;
                        if Tree.insert(RepairedNext) {
                            ParentByNode.insert(RepairedNext, RepairedState.0);
                        }
                        StateByNode.insert(RepairedNext, *GeometryState);
                        RepairedState = *GeometryState;
                    }
                    let Some(RepairedState) = StateByNode.get(&Next).copied() else {
                        return Failure("NoPath", "NoPathGeometry", 1, 1, ExpansionCount);
                    };
                    CurrentState = RepairedState;
                    continue;
                };
                let NextState = (Next, Direction, RemainingStrength);
                ParentByNode.insert(Next, CurrentState.0);
                Tree.insert(Next);
                StateByNode.insert(Next, NextState);
                CurrentState = NextState;
            }

            if BranchContinuationIncomplete {
                continue;
            }

            let Target = CurrentState.0;
            let mut Path = vec![Target];
            let mut Cursor = Target;
            while Cursor != Root {
                let Some(Previous) = ParentByNode.get(&Cursor).copied() else {
                    return Failure("NoPath", "NoPathGeometry", 0, 0, ExpansionCount);
                };
                Path.push(Previous);
                Cursor = Previous;
            }
            Path.reverse();
            TargetPaths.push((Target, Path));
        }
        if FrozenSourceBranch.is_some() {
            let mut PhysicalNodes = Tree
                .union(&FrozenReservedAccessNodes)
                .copied()
                .collect::<HashSet<_>>();
            let mut PhysicalPowers =
                PropagateCanonicalRoutePower(Root, &PhysicalNodes, &Repeaters, &self.Adjacency);
            // Protect targets only after their deterministic branch turn has
            // been processed.  A future immutable branch may be incidentally
            // powered before its own repeater decision; treating every such
            // target as already committed makes an intermediate ordering
            // state stricter than the completed tree.
            let mut RequiredPoweredTargets = HashSet::new();
            for Branch in FrozenTargetBranches {
                let Some(Target) = Branch.last().copied() else {
                    continue;
                };
                PhysicalNodes = Tree
                    .union(&FrozenReservedAccessNodes)
                    .copied()
                    .collect::<HashSet<_>>();
                let (CurrentPhysicalPowers, CanonicalPowerParentByNode) =
                    PropagateCanonicalRoutePowerWithParents(
                        Root,
                        &PhysicalNodes,
                        &Repeaters,
                        &self.Adjacency,
                    );
                PhysicalPowers = CurrentPhysicalPowers;
                if PhysicalPowers.contains_key(&Target) {
                    RequiredPoweredTargets.insert(Target);
                    continue;
                }
                // The immutable target branch can begin with a long diagonal,
                // leaving no legal refresh site after its portal.  In that
                // case the refresher belongs on the already selected rooted
                // guide path before the portal.  Re-evaluate that one exact
                // root-to-terminal path with the branch included in the
                // look-ahead, then accept it only when canonical directed
                // propagation still powers every earlier committed target.
                if let Some(Portal) = Branch.first().copied() {
                    let mut RootToPortal = vec![Portal];
                    let mut Cursor = Portal;
                    let mut Seen = HashSet::from([Portal]);
                    while Cursor != Root {
                        let Some(Previous) =
                            CanonicalPowerParentByNode.get(&Cursor).copied()
                        else {
                            RootToPortal.clear();
                            break;
                        };
                        if !Seen.insert(Previous) {
                            RootToPortal.clear();
                            break;
                        }
                        RootToPortal.push(Previous);
                        Cursor = Previous;
                    }
                    if !RootToPortal.is_empty() {
                        RootToPortal.reverse();
                        let RawCombinedPath = RootToPortal
                            .into_iter()
                            .chain(Branch.iter().copied().skip(1))
                            .collect::<Vec<_>>();
                        let CombinedPath =
                            EraseCanonicalRoutePathLoops(RawCombinedPath.iter().copied());
                        let mut CandidateTree = Tree.clone();
                        let mut CandidateStateByNode = StateByNode.clone();
                        let mut CandidateParentByNode = ParentByNode.clone();
                        let mut CandidateRepeaters = Repeaters.clone();
                        for PositionValue in &RawCombinedPath {
                            CandidateRepeaters.remove(PositionValue);
                        }
                        let mut CurrentState = (Root, StartDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH);
                        let mut ExactCombinedPathComplete = true;
                        let mut ExactCombinedPathFailureReason = "";
                        for PathIndex in 1..CombinedPath.len() {
                            let Next = CombinedPath[PathIndex];
                            if !self
                                .Adjacency
                                .get(&CurrentState.0)
                                .is_some_and(|Neighbors| Neighbors.contains(&Next))
                            {
                                ExactCombinedPathComplete = false;
                                ExactCombinedPathFailureReason = "non-adjacent";
                                break;
                            }
                            let Direction = (
                                Next.0 - CurrentState.0 .0,
                                Next.1 - CurrentState.0 .1,
                                Next.2 - CurrentState.0 .2,
                            );
                            let CurrentFacing = RepeaterFacing(CurrentState.0, Next);
                            let ExistingRepeaterFacing =
                                CandidateRepeaters.get(&CurrentState.0).cloned();
                            if ExistingRepeaterFacing
                                .as_ref()
                                .is_some_and(|Facing| Some(Facing) != CurrentFacing.as_ref())
                            {
                                ExactCombinedPathComplete = false;
                                ExactCombinedPathFailureReason = "existing-repeater-direction";
                                break;
                            }
                            let CanPhysicallyRefreshCurrent = CurrentState.1 != StartDirection
                                && CurrentState.1 == Direction
                                && Direction.1 == 0
                                && CurrentFacing.is_some()
                                && !ForbiddenRepeaterPositions.contains(&CurrentState.0);
                            let CurrentPathIndex = PathIndex - 1;
                            let NextRefreshDistance =
                                (PathIndex..CombinedPath.len().saturating_sub(1))
                                    .find_map(|CandidateIndex| {
                                        let Before = CombinedPath[CandidateIndex - 1];
                                        let Candidate = CombinedPath[CandidateIndex];
                                        let After = CombinedPath[CandidateIndex + 1];
                                        let Incoming = (
                                            Candidate.0 - Before.0,
                                            Candidate.1 - Before.1,
                                            Candidate.2 - Before.2,
                                        );
                                        let Outgoing = (
                                            After.0 - Candidate.0,
                                            After.1 - Candidate.1,
                                            After.2 - Candidate.2,
                                        );
                                        if Incoming != Outgoing
                                            || Incoming.1 != 0
                                            || ForbiddenRepeaterPositions.contains(&Candidate)
                                        {
                                            return None;
                                        }
                                        let Facing = RepeaterFacing(Candidate, After)?;
                                        if CandidateRepeaters
                                            .get(&Candidate)
                                            .is_some_and(|Value| Value != &Facing)
                                        {
                                            return None;
                                        }
                                        Some(CandidateIndex - CurrentPathIndex)
                                    })
                                    .unwrap_or(
                                        CombinedPath.len() - 1 - CurrentPathIndex,
                                    );
                            let MustRefreshCurrent = CurrentState.2
                                <= crate::PathRouting::REPEATER_TURN_HEADROOM
                                || NextRefreshDistance >= usize::from(CurrentState.2);
                            let RefreshCurrent = ExistingRepeaterFacing.is_some()
                                || (CanPhysicallyRefreshCurrent && MustRefreshCurrent);
                            let RemainingStrength = if RefreshCurrent {
                                let Some(Facing) = CurrentFacing else {
                                    ExactCombinedPathComplete = false;
                                    ExactCombinedPathFailureReason = "non-horizontal-refresh";
                                    break;
                                };
                                CandidateRepeaters.insert(CurrentState.0, Facing);
                                MAXIMUM_UNREFRESHED_DUST_LENGTH
                            } else if CurrentState.2 > 1 {
                                CurrentState.2 - 1
                            } else {
                                ExactCombinedPathComplete = false;
                                ExactCombinedPathFailureReason = "strength";
                                break;
                            };
                            let NextState = (Next, Direction, RemainingStrength);
                            if CandidateTree.insert(Next) {
                                CandidateParentByNode.insert(Next, CurrentState.0);
                            }
                            CandidateStateByNode.insert(Next, NextState);
                            CurrentState = NextState;
                        }
                        if ExactCombinedPathComplete {
                            let CandidatePhysicalNodes = CandidateTree
                                .union(&FrozenReservedAccessNodes)
                                .copied()
                                .collect::<HashSet<_>>();
                            let CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                                Root,
                                &CandidatePhysicalNodes,
                                &CandidateRepeaters,
                                &self.Adjacency,
                            );
                            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                                && !CandidatePhysicalPowers.contains_key(&Target)
                            {
                                eprintln!(
                                    "selected rooted target audit signal={} target={:?} target_power={:?} path={:?} path_powers={:?} path_repeaters={:?}",
                                    DebugLabel,
                                    Target,
                                    CandidatePhysicalPowers.get(&Target),
                                    CombinedPath,
                                    CombinedPath
                                        .iter()
                                        .map(|PositionValue| (
                                            *PositionValue,
                                            CandidatePhysicalPowers.get(PositionValue).copied(),
                                        ))
                                        .collect::<Vec<_>>(),
                                    CombinedPath
                                        .iter()
                                        .filter_map(|PositionValue| {
                                            CandidateRepeaters
                                                .get(PositionValue)
                                                .map(|Facing| (*PositionValue, Facing.clone()))
                                        })
                                        .collect::<Vec<_>>(),
                                );
                            }
                            if CandidatePhysicalPowers.contains_key(&Target)
                                && RequiredPoweredTargets.iter().all(|RequiredTarget| {
                                    CandidatePhysicalPowers.contains_key(RequiredTarget)
                                })
                            {
                                Tree = CandidateTree;
                                StateByNode = CandidateStateByNode;
                                ParentByNode = CandidateParentByNode;
                                Repeaters = CandidateRepeaters;
                                PhysicalPowers = CandidatePhysicalPowers;
                                RequiredPoweredTargets.insert(Target);
                                continue;
                            }
                        } else if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                            eprintln!(
                                "selected rooted target incomplete signal={} target={:?} reason={} path={:?}",
                                DebugLabel,
                                Target,
                                ExactCombinedPathFailureReason,
                                CombinedPath,
                            );
                        }
                    }
                }
                // The access catalog already selected this exact immutable
                // branch.  If its portal is powered, materialize the branch
                // directly and place refreshers only at legal straight
                // states.  Running a general graph search for known geometry
                // needlessly consumes the selected signal's finite work
                // share and can obscure a simple certified witness.
                let mut ExactPoweredSuffixAccepted = false;
                let PoweredBranchStartIndices = (0..Branch.len().saturating_sub(1))
                    .rev()
                    .filter(|BranchIndex| PhysicalPowers.contains_key(&Branch[*BranchIndex]))
                    .collect::<Vec<_>>();
                for StartIndex in PoweredBranchStartIndices {
                    let Portal = Branch[StartIndex];
                    if let Some(CurrentPower) = PhysicalPowers.get(&Portal).copied() {
                        let IncomingDirection = CanonicalPowerParentByNode
                            .get(&Portal)
                            .map(|Previous| {
                                (
                                    Portal.0 - Previous.0,
                                    Portal.1 - Previous.1,
                                    Portal.2 - Previous.2,
                                )
                            })
                            .unwrap_or(StartDirection);
                        let mut CurrentState = (Portal, IncomingDirection, CurrentPower);
                        let mut CandidateTree = Tree.clone();
                        let mut CandidateStateByNode = StateByNode.clone();
                        let mut CandidateParentByNode = ParentByNode.clone();
                        let mut CandidateRepeaters = Repeaters.clone();
                        let mut ExactBranchComplete = true;
                        for BranchIndex in StartIndex + 1..Branch.len() {
                            let Next = Branch[BranchIndex];
                            if !self
                                .Adjacency
                                .get(&CurrentState.0)
                                .is_some_and(|Neighbors| Neighbors.contains(&Next))
                            {
                                ExactBranchComplete = false;
                                break;
                            }
                            let Direction = (
                                Next.0 - CurrentState.0 .0,
                                Next.1 - CurrentState.0 .1,
                                Next.2 - CurrentState.0 .2,
                            );
                            let CanPhysicallyRefreshCurrent = CurrentState.1 != StartDirection
                                && CurrentState.1 == Direction
                                && Direction.1 == 0
                                && !ForbiddenRepeaterPositions.contains(&CurrentState.0);
                            // A fixed access branch may leave its last
                            // horizontal repeater site well before a long
                            // diagonal rise or descent.  Waiting solely for
                            // low signal strength then makes the immutable
                            // suffix impossible even though refreshing at the
                            // last straight site is legal.  Look ahead to the
                            // next legal refresh site (or the target) and
                            // refresh here exactly when the current strength
                            // cannot reach it.
                            let CurrentBranchIndex = BranchIndex - 1;
                            let NextRefreshDistance = (BranchIndex..Branch.len().saturating_sub(1))
                                .find_map(|CandidateIndex| {
                                    let Before = Branch[CandidateIndex - 1];
                                    let Candidate = Branch[CandidateIndex];
                                    let After = Branch[CandidateIndex + 1];
                                    let Incoming = (
                                        Candidate.0 - Before.0,
                                        Candidate.1 - Before.1,
                                        Candidate.2 - Before.2,
                                    );
                                    let Outgoing = (
                                        After.0 - Candidate.0,
                                        After.1 - Candidate.1,
                                        After.2 - Candidate.2,
                                    );
                                    if Incoming != Outgoing
                                        || Incoming.1 != 0
                                        || ForbiddenRepeaterPositions.contains(&Candidate)
                                    {
                                        return None;
                                    }
                                    let Facing = RepeaterFacing(Candidate, After)?;
                                    if CandidateRepeaters
                                        .get(&Candidate)
                                        .is_some_and(|Value| Value != &Facing)
                                    {
                                        return None;
                                    }
                                    Some(CandidateIndex - CurrentBranchIndex)
                                })
                                .unwrap_or(Branch.len() - 1 - CurrentBranchIndex);
                            let MustRefreshCurrent = CurrentState.2
                                <= crate::PathRouting::REPEATER_TURN_HEADROOM
                                || NextRefreshDistance >= usize::from(CurrentState.2);
                            let CanRefreshCurrent =
                                CanPhysicallyRefreshCurrent && MustRefreshCurrent;
                            let CanRefreshNext = Branch
                                .get(BranchIndex + 1)
                                .map(|After| {
                                    let NextDirection =
                                        (After.0 - Next.0, After.1 - Next.1, After.2 - Next.2);
                                    Direction == NextDirection
                                        && Direction.1 == 0
                                        && !ForbiddenRepeaterPositions.contains(&Next)
                                })
                                .unwrap_or(false);
                            let (RepeaterPosition, RepeaterAfter, RemainingStrength) =
                                if CanRefreshCurrent {
                                    (
                                        Some(CurrentState.0),
                                        Some(Next),
                                        MAXIMUM_UNREFRESHED_DUST_LENGTH,
                                    )
                                } else if CurrentState.2 > 1 {
                                    (None, None, CurrentState.2 - 1)
                                } else if CanRefreshNext {
                                    (
                                        Some(Next),
                                        Branch.get(BranchIndex + 1).copied(),
                                        MAXIMUM_UNREFRESHED_DUST_LENGTH,
                                    )
                                } else {
                                    ExactBranchComplete = false;
                                    break;
                                };
                            if let (Some(RepeaterPosition), Some(RepeaterAfter)) =
                                (RepeaterPosition, RepeaterAfter)
                            {
                                let Some(Facing) = RepeaterFacing(RepeaterPosition, RepeaterAfter)
                                else {
                                    ExactBranchComplete = false;
                                    break;
                                };
                                if CandidateRepeaters
                                    .get(&RepeaterPosition)
                                    .is_some_and(|Value| Value != &Facing)
                                {
                                    ExactBranchComplete = false;
                                    break;
                                }
                                CandidateRepeaters.insert(RepeaterPosition, Facing);
                            }
                            let NextState = (Next, Direction, RemainingStrength);
                            if CandidateTree.insert(Next) {
                                CandidateParentByNode.insert(Next, CurrentState.0);
                            }
                            CandidateStateByNode.insert(Next, NextState);
                            CurrentState = NextState;
                        }
                        if ExactBranchComplete {
                            let CandidatePhysicalNodes = CandidateTree
                                .union(&FrozenReservedAccessNodes)
                                .copied()
                                .collect::<HashSet<_>>();
                            let CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                                Root,
                                &CandidatePhysicalNodes,
                                &CandidateRepeaters,
                                &self.Adjacency,
                            );
                            if CandidatePhysicalPowers.contains_key(&Target)
                                && RequiredPoweredTargets.iter().all(|RequiredTarget| {
                                    CandidatePhysicalPowers.contains_key(RequiredTarget)
                                })
                            {
                                Tree = CandidateTree;
                                StateByNode = CandidateStateByNode;
                                ParentByNode = CandidateParentByNode;
                                Repeaters = CandidateRepeaters;
                                PhysicalPowers = CandidatePhysicalPowers;
                                RequiredPoweredTargets.insert(Target);
                                ExactPoweredSuffixAccepted = true;
                                break;
                            }
                        }
                    }
                    if ExactPoweredSuffixAccepted {
                        break;
                    }
                }
                if ExactPoweredSuffixAccepted {
                    continue;
                }
                if let Some(LocalResult) = RouteFrozenTargetIntoTree(
                    Branch,
                    &Tree,
                    &StateByNode,
                    &Repeaters,
                    i32::from(crate::PathRouting::REPEATER_TURN_HEADROOM) + 1,
                    Some(&PhysicalPowers),
                    MaximumExpansionCount
                        .saturating_sub(ExpansionCount)
                        .div_ceil(16),
                ) {
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                        eprintln!(
                            "selected frozen target local signal={} branch={:?} status={} reason={} expansions={} state_path={:?} repeaters={:?}",
                            DebugLabel,
                            Branch,
                            LocalResult.Status,
                            LocalResult.NoPathReason,
                            LocalResult.ExpansionCount,
                            LocalResult.StatePath,
                            LocalResult.RepeaterReservations,
                        );
                    }
                    ExpansionCount = ExpansionCount.saturating_add(LocalResult.ExpansionCount);
                    if LocalResult.Status == "Routed" {
                        let mut CandidateTree = Tree.clone();
                        let mut CandidateStateByNode = StateByNode.clone();
                        let mut CandidateParentByNode = ParentByNode.clone();
                        let mut CandidateRepeaters = Repeaters.clone();
                        for Values in LocalResult.StatePath.windows(2) {
                            let Previous = Values[0].0;
                            let Current = Values[1].0;
                            if CandidateTree.insert(Current) {
                                CandidateParentByNode.insert(Current, Previous);
                            }
                            CandidateStateByNode.insert(Current, Values[1]);
                        }
                        for (PositionValue, Facing) in LocalResult.RepeaterReservations {
                            CandidateRepeaters.entry(PositionValue).or_insert(Facing);
                        }
                        let CandidatePhysicalNodes = CandidateTree
                            .union(&FrozenReservedAccessNodes)
                            .copied()
                            .collect::<HashSet<_>>();
                        let CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                            Root,
                            &CandidatePhysicalNodes,
                            &CandidateRepeaters,
                            &self.Adjacency,
                        );
                        if CandidatePhysicalPowers.contains_key(&Target)
                            && RequiredPoweredTargets.iter().all(|RequiredTarget| {
                                CandidatePhysicalPowers.contains_key(RequiredTarget)
                            })
                        {
                            Tree = CandidateTree;
                            StateByNode = CandidateStateByNode;
                            ParentByNode = CandidateParentByNode;
                            Repeaters = CandidateRepeaters;
                            PhysicalPowers = CandidatePhysicalPowers;
                            RequiredPoweredTargets.insert(Target);
                            continue;
                        }
                    }
                }
                PhysicalNodes = Tree.union(&FrozenReservedAccessNodes).copied().collect();
                PhysicalPowers =
                    PropagateCanonicalRoutePower(Root, &PhysicalNodes, &Repeaters, &self.Adjacency);
                if PhysicalPowers.contains_key(&Target) {
                    continue;
                }

                // A selected access branch is immutable ownership, not a
                // promise that its final ingress can be refreshed locally.
                // When the bounded local continuation has no powered start,
                // search one exact powered bypass through the already
                // selected guide domain.  This remains part of the same
                // native materialization invocation and shares its expansion
                // counter and absolute deadline.
                let Some((mut DynamicBlocked, EdgeCosts)) = BuildRootedTreeBlockages(
                    self,
                    Guide,
                    AdditionalAllowedNodes,
                    &BlockedNodes,
                    &Tree,
                    &FrozenReservedAccessNodes,
                    &Deadline,
                ) else {
                    continue;
                };
                // This is a multi-source bypass from the already powered
                // tree, not permission to traverse immutable tree geometry
                // as ordinary dust.  In particular, walking through an
                // existing repeater body ignores its direction and produces
                // a strength state that canonical propagation cannot realize.
                // Block the complete existing conductor first; exact powered
                // launch nodes and the one selected access join are reopened
                // below.
                DynamicBlocked.extend(Tree.iter().copied());
                DynamicBlocked.extend(FrozenReservedAccessNodes.iter().copied());
                let BranchNodeSet = Branch.iter().copied().collect::<HashSet<_>>();
                let IndependentPhysicalNodes = PhysicalNodes
                    .difference(&BranchNodeSet)
                    .copied()
                    .collect::<HashSet<_>>();
                let IndependentPowers = PropagateCanonicalRoutePower(
                    Root,
                    &IndependentPhysicalNodes,
                    &Repeaters,
                    &self.Adjacency,
                );
                let mut PoweredStartStates = StateByNode
                    .values()
                    .filter_map(|State| {
                        if Repeaters.contains_key(&State.0)
                            || BranchNodeSet.contains(&State.0)
                            || BaseBlockedNodes.contains(&State.0)
                        {
                            return None;
                        }
                        let mut PoweredState = *State;
                        PoweredState.2 = IndependentPowers.get(&State.0).copied()?;
                        Some(PoweredState)
                    })
                    .collect::<Vec<_>>();
                PoweredStartStates.sort_unstable();
                PoweredStartStates.dedup();
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected frozen target repair signal={} portal={:?} target={:?} branch_nodes={} portal_power={:?} powered_starts={} tree_nodes={}",
                        DebugLabel,
                        Branch.first(),
                        Branch.last(),
                        Branch.len(),
                        Branch.first().and_then(|Portal| PhysicalPowers.get(Portal)),
                        PoweredStartStates.len(),
                        Tree.len(),
                    );
                }
                if PoweredStartStates.is_empty() {
                    continue;
                }
                // The selected compact factor owns the complete immutable
                // target branch as same-signal dust.  Requiring a bypass to
                // reach only the terminal throws away legal joins along that
                // conductor and is especially costly for a long diagonal
                // access ramp.  Join the nearest node in the terminal's
                // final powered suffix, then let the canonical directed
                // repeater audit below decide whether the terminal is truly
                // driven.  A failed audit remains a normal incomplete
                // materialization; no geometry is accepted by proximity.
                let MinimumSuffixIndex = Branch
                    .len()
                    .saturating_sub(MAXIMUM_UNREFRESHED_DUST_LENGTH as usize)
                    .max(
                        Branch
                            .iter()
                            .enumerate()
                            .filter_map(|(BranchIndex, PositionValue)| {
                                Repeaters
                                    .contains_key(PositionValue)
                                    .then_some(BranchIndex.saturating_add(1))
                            })
                            .max()
                            .unwrap_or(0)
                            .min(Branch.len().saturating_sub(1)),
                    );
                let (BypassTargetIndex, BypassTarget) = Branch
                    .iter()
                    .copied()
                    .enumerate()
                    .skip(MinimumSuffixIndex)
                    .min_by_key(|(BranchIndex, PositionValue)| {
                        (
                            PoweredStartStates
                                .iter()
                                .map(|State| ManhattanDistance(State.0, *PositionValue))
                                .min()
                                .unwrap_or(i32::MAX),
                            *BranchIndex,
                            *PositionValue,
                        )
                    })
                    .unwrap_or((Branch.len().saturating_sub(1), Target));
                DynamicBlocked.remove(&BypassTarget);
                let Some(BypassResult) = FindPathFromStatesDetailedWithDeadline(
                    &self.Adjacency,
                    Guide.UseColumnMembership.then_some(&Guide.AllowedColumns),
                    Some(AdditionalAllowedNodes),
                    &PoweredStartStates,
                    BypassTarget,
                    PreferredRoutingY,
                    &DynamicBlocked,
                    &Guide.NodeCosts,
                    &AdditionalNodeCosts,
                    &Guide.ColumnCosts,
                    &EdgeCosts,
                    BendPenalty,
                    ViaPenalty,
                    BendPenalty.max(1),
                    MaximumExpansionCount.saturating_sub(ExpansionCount),
                    EnforceSignalStrength,
                    ForbiddenRepeaterPositions,
                    &Branch[BypassTargetIndex..],
                    0,
                    &Deadline,
                ) else {
                    continue;
                };
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected frozen target bypass signal={} status={} reason={} expansions={}",
                        DebugLabel,
                        BypassResult.Status,
                        BypassResult.NoPathReason,
                        BypassResult.ExpansionCount,
                    );
                }
                ExpansionCount = ExpansionCount.saturating_add(BypassResult.ExpansionCount);
                if BypassResult.Status != "Routed" {
                    continue;
                }
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected frozen target bypass witness signal={} target={:?} states={:?} repeaters={:?}",
                        DebugLabel,
                        Target,
                        BypassResult.StatePath,
                        BypassResult.RepeaterReservations,
                    );
                }
                let mut CandidateTree = Tree.clone();
                let mut CandidateStateByNode = StateByNode.clone();
                let mut CandidateParentByNode = ParentByNode.clone();
                let mut CandidateRepeaters = Repeaters.clone();
                for PositionValue in &Branch[..=BypassTargetIndex] {
                    CandidateRepeaters.remove(PositionValue);
                }
                for Values in BypassResult.StatePath.windows(2) {
                    let Previous = Values[0].0;
                    let Current = Values[1].0;
                    if CandidateTree.insert(Current) {
                        CandidateParentByNode.insert(Current, Previous);
                    }
                    CandidateStateByNode.insert(Current, Values[1]);
                }
                for (PositionValue, Facing) in BypassResult.RepeaterReservations {
                    CandidateRepeaters.entry(PositionValue).or_insert(Facing);
                }
                let CandidatePhysicalNodes = CandidateTree
                    .union(&FrozenReservedAccessNodes)
                    .copied()
                    .collect::<HashSet<_>>();
                let mut CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                    Root,
                    &CandidatePhysicalNodes,
                    &CandidateRepeaters,
                    &self.Adjacency,
                );
                if CandidatePhysicalPowers.contains_key(&Target)
                    && RequiredPoweredTargets
                        .iter()
                        .any(|RequiredTarget| !CandidatePhysicalPowers.contains_key(RequiredTarget))
                    && BypassTargetIndex >= 3
                {
                    let Junction = Branch[BypassTargetIndex - 1];
                    let SiblingRepeater = Branch[BypassTargetIndex - 2];
                    let SiblingOutput = Branch[BypassTargetIndex - 3];
                    let InputDirection = (
                        SiblingRepeater.0 - Junction.0,
                        SiblingRepeater.1 - Junction.1,
                        SiblingRepeater.2 - Junction.2,
                    );
                    let OutputDirection = (
                        SiblingOutput.0 - SiblingRepeater.0,
                        SiblingOutput.1 - SiblingRepeater.1,
                        SiblingOutput.2 - SiblingRepeater.2,
                    );
                    if InputDirection == OutputDirection
                        && InputDirection.1 == 0
                        && !ForbiddenRepeaterPositions.contains(&SiblingRepeater)
                    {
                        if let Some(Facing) =
                            RepeaterFacing(SiblingRepeater, SiblingOutput)
                        {
                            CandidateRepeaters.insert(SiblingRepeater, Facing);
                            CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                                Root,
                                &CandidatePhysicalNodes,
                                &CandidateRepeaters,
                                &self.Adjacency,
                            );
                        }
                    }
                }
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected frozen target bypass audit signal={} target={:?} target_power={:?} candidate_repeaters={:?}",
                        DebugLabel,
                        Target,
                        CandidatePhysicalPowers.get(&Target),
                        CandidateRepeaters,
                    );
                    eprintln!(
                        "selected frozen target bypass required powers signal={} values={:?}",
                        DebugLabel,
                        RequiredPoweredTargets
                            .iter()
                            .copied()
                            .map(|RequiredTarget| (
                                RequiredTarget,
                                CandidatePhysicalPowers.get(&RequiredTarget).copied(),
                            ))
                            .collect::<Vec<_>>(),
                    );
                    eprintln!(
                        "selected frozen target bypass path powers signal={} values={:?}",
                        DebugLabel,
                        BypassResult
                            .StatePath
                            .iter()
                            .map(|State| (
                                State.0,
                                CandidatePhysicalNodes.contains(&State.0),
                                CandidatePhysicalPowers.get(&State.0).copied(),
                                CandidateRepeaters.get(&State.0),
                            ))
                            .collect::<Vec<_>>(),
                    );
                    eprintln!(
                        "selected frozen target bypass branch powers signal={} values={:?}",
                        DebugLabel,
                        Branch
                            .iter()
                            .map(|PositionValue| (
                                *PositionValue,
                                CandidatePhysicalNodes.contains(PositionValue),
                                CandidatePhysicalPowers.get(PositionValue).copied(),
                            ))
                            .collect::<Vec<_>>(),
                    );
                }
                if CandidatePhysicalPowers.contains_key(&Target)
                    && RequiredPoweredTargets
                        .iter()
                        .all(|RequiredTarget| CandidatePhysicalPowers.contains_key(RequiredTarget))
                {
                    Tree = CandidateTree;
                    StateByNode = CandidateStateByNode;
                    ParentByNode = CandidateParentByNode;
                    Repeaters = CandidateRepeaters;
                    PhysicalNodes = CandidatePhysicalNodes;
                    PhysicalPowers = CandidatePhysicalPowers;
                    RequiredPoweredTargets.insert(Target);
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                        eprintln!(
                            "selected frozen target bypass committed signal={} target={:?} power={:?}",
                            DebugLabel,
                            Target,
                            PhysicalPowers.get(&Target),
                        );
                    }
                }
            }
            let UnpoweredFrozenBranches = FrozenTargetBranches
                .iter()
                .filter(|Branch| {
                    Branch
                        .last()
                        .is_none_or(|Target| !PhysicalPowers.contains_key(Target))
                })
                .collect::<Vec<_>>();
            if !UnpoweredFrozenBranches.is_empty() {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected final unpowered frozen branches signal={} targets={:?} powers={:?}",
                        DebugLabel,
                        UnpoweredFrozenBranches
                            .iter()
                            .filter_map(|Branch| Branch.last().copied())
                            .collect::<Vec<_>>(),
                        UnpoweredFrozenBranches
                            .iter()
                            .filter_map(|Branch| Branch.last().copied())
                            .map(|Target| (Target, PhysicalPowers.get(&Target).copied()))
                            .collect::<Vec<_>>(),
                    );
                }
                // The immutable access branch is fixed by the compact
                // contract, but the generated root-to-portal tree and its
                // repeater placement are not.  Preserve that distinction in
                // the failure evidence: the claim-aware wrapper may cut one
                // mutable conductor or forbid one placed repeater and search
                // the exact alternative inside this same bounded call.
                let mut ConflictResources = Vec::new();
                for Branch in UnpoweredFrozenBranches {
                    for PortalAnchor in Branch.iter().copied().filter(|Value| {
                        Tree.contains(Value) || ParentByNode.contains_key(Value)
                    }) {
                        let mut Cursor = PortalAnchor;
                        let mut Seen = HashSet::new();
                        while Seen.insert(Cursor) {
                            ConflictResources.push((
                                "RepeaterPowerPath".to_string(),
                                Cursor,
                            ));
                            if Repeaters.contains_key(&Cursor) {
                                ConflictResources.push((
                                    "RepeaterPowerAnchor".to_string(),
                                    Cursor,
                                ));
                            }
                            if Cursor == Root {
                                break;
                            }
                            let Some(Previous) = ParentByNode.get(&Cursor).copied() else {
                                break;
                            };
                            Cursor = Previous;
                        }
                    }
                }
                if ConflictResources.is_empty() {
                    ConflictResources.extend(Tree.iter().copied().map(|Value| {
                        ("RepeaterPowerPath".to_string(), Value)
                    }));
                }
                ConflictResources.sort_unstable();
                ConflictResources.dedup();
                let mut Result =
                    Failure("NoPath", "NoRepeaterFinalPowerAudit", 1, 1, ExpansionCount);
                Result.ConflictResources = ConflictResources;
                return Result;
            }
        }
        // A repeater embedded in an induced same-signal cycle can preserve a
        // transient pulse after the real source goes low.  This is not a
        // signal-strength issue: the routed node set itself supplies an
        // alternate directed output-to-input path around the repeater.  A
        // cycle repeater that is unnecessary for source-to-target delivery is
        // therefore a dust cell, not a legal refresh element.  Demote only
        // after exact canonical propagation proves every required target
        // remains powered; an essential cycle is rejected instead of emitted
        // as a stateful combinational route.
        Tree.extend(RetainedMandatorySourceNodes);
        Tree.extend(RetainedMandatoryTargetNodes);
        let mut PhysicalNodes = Tree
            .union(&FrozenReservedAccessNodes)
            .copied()
            .collect::<HashSet<_>>();
        let RequiredTargets = TargetBranches
            .iter()
            .chain(FrozenTargetBranches.iter())
            .filter_map(|Branch| Branch.last().copied())
            .collect::<HashSet<_>>();
        loop {
            let mut RepeaterValues = Repeaters
                .iter()
                .map(|(PositionValue, Facing)| (*PositionValue, Facing.clone()))
                .collect::<Vec<_>>();
            RepeaterValues.sort_unstable();
            let Cycles = FindSelfExcitingRepeaterCycles(&PhysicalNodes, &RepeaterValues);
            if Cycles.is_empty() {
                break;
            }
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                let CurrentPowers = PropagateCanonicalRoutePower(
                    Root,
                    &PhysicalNodes,
                    &Repeaters,
                    &self.Adjacency,
                );
                eprintln!(
                    "selected detailed cycle target powers signal={} values={:?}",
                    DebugLabel,
                    RequiredTargets
                        .iter()
                        .copied()
                        .map(|Target| (Target, CurrentPowers.get(&Target).copied()))
                        .collect::<Vec<_>>(),
                );
            }
            let CycleCount = Cycles.len();
            let mut Demoted = false;
            for (Repeater, _Cycle) in &Cycles {
                let mut CandidateRepeaters = Repeaters.clone();
                CandidateRepeaters.remove(Repeater);
                let CandidatePowers = PropagateCanonicalRoutePower(
                    Root,
                    &PhysicalNodes,
                    &CandidateRepeaters,
                    &self.Adjacency,
                );
                if RequiredTargets
                    .iter()
                    .all(|Target| CandidatePowers.contains_key(Target))
                {
                    Repeaters = CandidateRepeaters;
                    Demoted = true;
                    break;
                }
            }
            if !Demoted {
                // The generated route is physically a tree candidate, but
                // immutable access fragments can induce an extra dust edge
                // after union.  Remove one redundant mutable cycle cell
                // directly when exact canonical propagation proves that all
                // targets stay powered and the cycle frontier shrinks.  This
                // preserves every mandatory selected-world claim and avoids
                // rebuilding the complete route merely to break an induced
                // loop around one repeater.
                'CycleDustCut: for (_Repeater, Cycle) in &Cycles {
                    let mut OrderedCycleNodes = Cycle.clone();
                    OrderedCycleNodes.sort_unstable();
                    OrderedCycleNodes.dedup();
                    for CutNode in OrderedCycleNodes {
                        if CutNode == Root
                            || RequiredTargets.contains(&CutNode)
                            || UnblockedAdditionalNodes.contains(&CutNode)
                        {
                            continue;
                        }
                        let mut CandidatePhysicalNodes = PhysicalNodes.clone();
                        if !CandidatePhysicalNodes.remove(&CutNode) {
                            continue;
                        }
                        let mut CandidateRepeaters = Repeaters.clone();
                        CandidateRepeaters.remove(&CutNode);
                        let CandidatePowers = PropagateCanonicalRoutePower(
                            Root,
                            &CandidatePhysicalNodes,
                            &CandidateRepeaters,
                            &self.Adjacency,
                        );
                        if RequiredTargets
                            .iter()
                            .any(|Target| !CandidatePowers.contains_key(Target))
                        {
                            continue;
                        }
                        let mut CandidateRepeaterValues = CandidateRepeaters
                            .iter()
                            .map(|(PositionValue, FacingValue)| {
                                (*PositionValue, FacingValue.clone())
                            })
                            .collect::<Vec<_>>();
                        CandidateRepeaterValues.sort_unstable();
                        if FindSelfExcitingRepeaterCycles(
                            &CandidatePhysicalNodes,
                            &CandidateRepeaterValues,
                        )
                        .len()
                            >= CycleCount
                        {
                            continue;
                        }
                        Tree.remove(&CutNode);
                        ParentByNode.remove(&CutNode);
                        StateByNode.remove(&CutNode);
                        PhysicalNodes = CandidatePhysicalNodes;
                        Repeaters = CandidateRepeaters;
                        Demoted = true;
                        break 'CycleDustCut;
                    }
                }
            }
            if !Demoted {
                // Parallel same-signal rails can make an essential repeater
                // self-exciting through the adjacent dust rail.  Removing or
                // translating the essential repeater only moves the loop.  A
                // directed companion on the proven cycle can cut that bypass
                // while preserving the original refresh.  Accept only an
                // exact placement that powers every target and strictly
                // reduces the complete cycle set.
                let FacingValues = [
                    ("east", (-1, 0, 0)),
                    ("north", (0, 0, 1)),
                    ("south", (0, 0, -1)),
                    ("west", (1, 0, 0)),
                ];
                'CycleCompanion: for (_Repeater, Cycle) in &Cycles {
                    let mut CandidatePositions = Cycle.clone();
                    CandidatePositions.sort_unstable();
                    CandidatePositions.dedup();
                    for CandidatePosition in CandidatePositions {
                        if CandidatePosition == Root
                            || RequiredTargets.contains(&CandidatePosition)
                            || Repeaters.contains_key(&CandidatePosition)
                            || ForbiddenRepeaterPositions.contains(&CandidatePosition)
                        {
                            continue;
                        }
                        for (Facing, OutputDelta) in FacingValues {
                            if Deadline.Check() || ExpansionCount >= MaximumExpansionCount {
                                return Failure(
                                    "NoPath",
                                    "SearchLimitReached",
                                    1,
                                    1,
                                    ExpansionCount,
                                );
                            }
                            ExpansionCount += 1;
                            let Input = (
                                CandidatePosition.0 - OutputDelta.0,
                                CandidatePosition.1 - OutputDelta.1,
                                CandidatePosition.2 - OutputDelta.2,
                            );
                            let Output = (
                                CandidatePosition.0 + OutputDelta.0,
                                CandidatePosition.1 + OutputDelta.1,
                                CandidatePosition.2 + OutputDelta.2,
                            );
                            if !PhysicalNodes.contains(&Input)
                                || !PhysicalNodes.contains(&Output)
                                || !self
                                    .Adjacency
                                    .get(&CandidatePosition)
                                    .is_some_and(|Neighbors| {
                                        Neighbors.contains(&Input) && Neighbors.contains(&Output)
                                    })
                            {
                                continue;
                            }
                            let mut CandidateRepeaters = Repeaters.clone();
                            CandidateRepeaters
                                .insert(CandidatePosition, Facing.to_string());
                            let CandidatePowers = PropagateCanonicalRoutePower(
                                Root,
                                &PhysicalNodes,
                                &CandidateRepeaters,
                                &self.Adjacency,
                            );
                            let MissingTargets = RequiredTargets
                                .iter()
                                .copied()
                                .filter(|Target| !CandidatePowers.contains_key(Target))
                                .collect::<Vec<_>>();
                            if !MissingTargets.is_empty() {
                                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                                    eprintln!(
                                        "selected detailed cycle companion signal={} position={:?} facing={} missing_targets={:?}",
                                        DebugLabel, CandidatePosition, Facing, MissingTargets,
                                    );
                                }
                                continue;
                            }
                            let mut CandidateRepeaterValues = CandidateRepeaters
                                .iter()
                                .map(|(PositionValue, FacingValue)| {
                                    (*PositionValue, FacingValue.clone())
                                })
                                .collect::<Vec<_>>();
                            CandidateRepeaterValues.sort_unstable();
                            let CandidateCycleCount = FindSelfExcitingRepeaterCycles(
                                &PhysicalNodes,
                                &CandidateRepeaterValues,
                            )
                            .len();
                            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                                eprintln!(
                                    "selected detailed cycle companion signal={} position={:?} facing={} cycles_before={} cycles_after={}",
                                    DebugLabel,
                                    CandidatePosition,
                                    Facing,
                                    CycleCount,
                                    CandidateCycleCount,
                                );
                            }
                            if CandidateCycleCount >= CycleCount {
                                continue;
                            }
                            Repeaters = CandidateRepeaters;
                            Demoted = true;
                            break 'CycleCompanion;
                        }
                    }
                }
            }
            if !Demoted {
                let mut OrderedNodes = PhysicalNodes.iter().copied().collect::<Vec<_>>();
                OrderedNodes.sort_unstable();
                let FacingValues = [
                    ("east", (-1, 0, 0)),
                    ("north", (0, 0, 1)),
                    ("south", (0, 0, -1)),
                    ("west", (1, 0, 0)),
                ];
                'CycleRepeater: for (RemovedRepeater, _Cycle) in &Cycles {
                    let mut BaseRepeaters = Repeaters.clone();
                    BaseRepeaters.remove(RemovedRepeater);
                    for CandidatePosition in &OrderedNodes {
                        if *CandidatePosition == Root
                            || RequiredTargets.contains(CandidatePosition)
                            || BaseRepeaters.contains_key(CandidatePosition)
                            || ForbiddenRepeaterPositions.contains(CandidatePosition)
                        {
                            continue;
                        }
                        for (Facing, OutputDelta) in FacingValues {
                            let Input = (
                                CandidatePosition.0 - OutputDelta.0,
                                CandidatePosition.1 - OutputDelta.1,
                                CandidatePosition.2 - OutputDelta.2,
                            );
                            let Output = (
                                CandidatePosition.0 + OutputDelta.0,
                                CandidatePosition.1 + OutputDelta.1,
                                CandidatePosition.2 + OutputDelta.2,
                            );
                            if !PhysicalNodes.contains(&Input)
                                || !PhysicalNodes.contains(&Output)
                                || !self
                                    .Adjacency
                                    .get(CandidatePosition)
                                    .is_some_and(|Neighbors| {
                                        Neighbors.contains(&Input) && Neighbors.contains(&Output)
                                    })
                            {
                                continue;
                            }
                            let mut CandidateRepeaters = BaseRepeaters.clone();
                            CandidateRepeaters.insert(*CandidatePosition, Facing.to_string());
                            let CandidatePowers = PropagateCanonicalRoutePower(
                                Root,
                                &PhysicalNodes,
                                &CandidateRepeaters,
                                &self.Adjacency,
                            );
                            if RequiredTargets
                                .iter()
                                .any(|Target| !CandidatePowers.contains_key(Target))
                            {
                                continue;
                            }
                            let mut CandidateRepeaterValues = CandidateRepeaters
                                .iter()
                                .map(|(PositionValue, FacingValue)| {
                                    (*PositionValue, FacingValue.clone())
                                })
                                .collect::<Vec<_>>();
                            CandidateRepeaterValues.sort_unstable();
                            if FindSelfExcitingRepeaterCycles(
                                &PhysicalNodes,
                                &CandidateRepeaterValues,
                            )
                            .len()
                                >= CycleCount
                            {
                                continue;
                            }
                            Repeaters = CandidateRepeaters;
                            Demoted = true;
                            break 'CycleRepeater;
                        }
                    }
                }
            }
            if !Demoted {
                let SelectedCycle = Cycles.iter().min_by_key(|(Repeater, Cycle)| {
                    (
                        RequiredTargets
                            .iter()
                            .map(|Target| ManhattanDistance(*Repeater, *Target))
                            .min()
                            .unwrap_or(0),
                        Cycle.len(),
                        *Repeater,
                    )
                });
                let mut Result = Failure(
                    "NoPath",
                    "NoRepeaterSelfExcitingCycle",
                    1,
                    1,
                    ExpansionCount,
                );
                Result.ConflictResources = SelectedCycle
                    .into_iter()
                    .flat_map(|(Repeater, Cycle)| {
                        std::iter::once(("RepeaterCycleAnchor".to_string(), *Repeater)).chain(
                            Cycle
                                .iter()
                                .copied()
                                .map(|PositionValue| ("RepeaterCycle".to_string(), PositionValue)),
                        )
                    })
                    .collect();
                return Result;
            }
        }
        TargetPaths.sort_by_key(|Value| Value.0);
        let mut RepeaterReservations: Vec<_> = Repeaters.into_iter().collect();
        RepeaterReservations.sort_by_key(|Value| Value.0);
        // Boundary diagnostics are proportional to the routed tree, not the
        // entire sparse ownership region.  Scanning every allowed node for
        // every net made pass zero scale as nets times region size.
        let mut FinalNodes: Vec<_> = Tree.into_iter().collect();
        FinalNodes.sort_unstable();
        let BoundaryFrontierNodes = FinalNodes
            .iter()
            .filter(|Value| {
                self.Adjacency
                    .get(Value)
                    .into_iter()
                    .flatten()
                    .any(|Neighbor| {
                        !IsPreparedRouteNodeAllowed(Guide, AdditionalAllowedNodes, Neighbor)
                    })
            })
            .copied()
            .collect();
        RouteTreeSearchResult {
            Status: "Routed".to_string(),
            NoPathReason: String::new(),
            Nodes: FinalNodes,
            TargetPaths,
            BoundaryFrontierNodes,
            RepeaterReservations,
            ExpansionCount,
            RepeaterRejectedCount: 0,
            RepeaterConstraintFailureCount: 0,
            ConflictResources: Vec::new(),
            RejectedPathCount: 0,
            NoGoodCount: 0,
            ElapsedMilliseconds: 0,
            IsRouted: true,
            IsBudgetExpired: false,
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn GenerateRouteTreeClaimAwarePreparedDetailedNative(
        &self,
        Starts: &[Position],
        TargetBranches: &[Vec<Position>],
        FrozenTargetBranches: &[Vec<Position>],
        Guide: &PreparedDetailedRouteGuide,
        AdditionalAllowedNodes: &HashSet<Position>,
        BaseBlockedNodes: &HashSet<Position>,
        PreferredRoutingY: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        FrozenSourceBranch: Option<&[Position]>,
        MaximumExpansionCount: usize,
        Deadline: &RuntimeDeadline,
        MandatoryWire: &HashSet<Position>,
        MandatorySupport: &HashSet<Position>,
        MandatoryAir: &HashSet<Position>,
        DebugLabel: &str,
    ) -> RouteTreeSearchResult {
        let Started = Instant::now();
        // Only immutable conductors are retained route nodes.  Mandatory air
        // is an exclusion: if a generated path occupies one of those cells,
        // that mutable wire position must remain eligible for an exact
        // no-good cut.
        let MandatoryNodes: HashSet<_> = MandatoryWire.iter().copied().collect();
        let mut SearchBlocked = BaseBlockedNodes.clone();
        let mut PendingSearchStates = Vec::new();
        let mut TotalExpansions = 0usize;
        let mut RejectedPathCount = 0usize;
        let mut NoGoodCount = 0usize;
        let mut LastConflictResources = Vec::new();
        let mut ForbiddenRepeaterPositions = HashSet::new();
        let StrictHintNodes = MandatoryWire
            .union(&Guide.ExactHintNodes)
            .copied()
            .collect::<HashSet<_>>();
        let StrictMandatoryGuide = PreparedDetailedRouteGuide {
            AllowedNodes: StrictHintNodes.clone(),
            AllowedColumns: HashSet::new(),
            UseColumnMembership: false,
            BoundaryBlockedNodes: Guide.BoundaryBlockedNodes.clone(),
            NodeCosts: HashMap::new(),
            ColumnCosts: HashMap::new(),
            PreferredColumns: Guide.PreferredColumns.clone(),
            ExactHintNodes: HashSet::new(),
            CertifiedPaths: Vec::new(),
            CertifiedRepeaters: Vec::new(),
            GuidePenalty: 0,
        };
        let mut UseStrictMandatoryGuide = !StrictHintNodes.is_empty();

        loop {
            if Deadline.Check() || TotalExpansions >= MaximumExpansionCount {
                let mut Result = DetailedRouteTreeBudgetExpiredResult();
                Result.ExpansionCount = TotalExpansions;
                Result.ConflictResources = LastConflictResources;
                Result.RejectedPathCount = RejectedPathCount;
                Result.NoGoodCount = NoGoodCount;
                Result.ElapsedMilliseconds = Started.elapsed().as_millis() as u64;
                return Result;
            }
            let RemainingExpansionCount = MaximumExpansionCount.saturating_sub(TotalExpansions);
            // Traverse exact no-good branches depth-first under the one
            // shared counter.  Dividing the remaining work by every queued
            // sibling made each branch incomplete even when the first
            // deterministic cycle-breaking branch had a witness well below
            // the unchanged total cap.  A completely disproven branch leaves
            // its unused work for the next sibling; an exhausted branch keeps
            // the overall result typed incomplete.
            let SearchExpansionCount = RemainingExpansionCount.max(1);
            let StrictAttempt = UseStrictMandatoryGuide;
            let SearchGuide = if StrictAttempt {
                &StrictMandatoryGuide
            } else {
                Guide
            };
            let SearchAllowedNodes = if StrictAttempt {
                &StrictHintNodes
            } else {
                AdditionalAllowedNodes
            };
            let mut Result = self.GenerateRouteTreeDetailedPreparedWithDeadlineNative(
                Starts,
                TargetBranches,
                FrozenTargetBranches,
                SearchGuide,
                SearchAllowedNodes,
                MandatoryWire,
                &SearchBlocked,
                PreferredRoutingY,
                BendPenalty,
                ViaPenalty,
                EnforceSignalStrength,
                FrozenSourceBranch,
                &ForbiddenRepeaterPositions,
                DebugLabel,
                SearchExpansionCount,
                Deadline,
            );
            TotalExpansions += Result.ExpansionCount;
            Result.ExpansionCount = TotalExpansions;
            Result.RejectedPathCount = RejectedPathCount;
            Result.NoGoodCount = NoGoodCount;
            Result.ElapsedMilliseconds = Started.elapsed().as_millis() as u64;
            if StrictAttempt && std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected strict spine nodes={} starts={} targets={} status={} reason={} expansions={}",
                    MandatoryWire.len(),
                    Starts.len(),
                    TargetBranches.len() + FrozenTargetBranches.len(),
                    Result.Status,
                    Result.NoPathReason,
                    Result.ExpansionCount,
                );
            }
            if Result.Status != "Routed" {
                if StrictAttempt
                    && !Result.IsBudgetExpired
                    && TotalExpansions < MaximumExpansionCount
                    && !Deadline.Check()
                {
                    // The exact selected access/portal/spine claims are the
                    // cheapest materialization domain.  They are not assumed
                    // sufficient: a complete negative strict attempt simply
                    // opens the selected guide corridor inside this same
                    // bounded native invocation.
                    UseStrictMandatoryGuide = false;
                    continue;
                }
                if Result.NoPathReason == "NoRepeaterFinalPowerAudit" {
                    LastConflictResources = Result.ConflictResources.clone();
                    let mut OrderedCutNodes = LastConflictResources
                        .iter()
                        .filter_map(|(Kind, PositionValue)| {
                            (Kind == "RepeaterPowerPath").then_some(*PositionValue)
                        })
                        .filter(|PositionValue| {
                            !MandatoryNodes.contains(PositionValue)
                                && !SearchBlocked.contains(PositionValue)
                        })
                        .collect::<Vec<_>>();
                    let PreferredColumns = Guide
                        .PreferredColumns
                        .iter()
                        .copied()
                        .collect::<HashSet<_>>();
                    OrderedCutNodes.sort_by_key(|PositionValue| {
                        (
                            std::cmp::Reverse(
                                PreferredColumns.contains(&(PositionValue.0, PositionValue.2)),
                            ),
                            std::cmp::Reverse(PositionValue.1),
                            PositionValue.0,
                            PositionValue.2,
                        )
                    });
                    OrderedCutNodes.dedup();
                    let mut RepeaterPositions = LastConflictResources
                        .iter()
                        .filter_map(|(Kind, PositionValue)| {
                            (Kind == "RepeaterPowerAnchor").then_some(*PositionValue)
                        })
                        .filter(|PositionValue| {
                            !ForbiddenRepeaterPositions.contains(PositionValue)
                        })
                        .collect::<Vec<_>>();
                    RepeaterPositions.sort_unstable();
                    RepeaterPositions.dedup();
                    RejectedPathCount += 1;
                    if (!OrderedCutNodes.is_empty() || !RepeaterPositions.is_empty())
                        && NoGoodCount < 64
                    {
                        let SearchBlockedBeforeNoGood = SearchBlocked.clone();
                        let ForbiddenRepeatersBeforeNoGood =
                            ForbiddenRepeaterPositions.clone();
                        for RepeaterPosition in RepeaterPositions.into_iter().rev() {
                            let mut AlternativeForbidden =
                                ForbiddenRepeatersBeforeNoGood.clone();
                            AlternativeForbidden.insert(RepeaterPosition);
                            PendingSearchStates.push((
                                SearchBlockedBeforeNoGood.clone(),
                                AlternativeForbidden,
                            ));
                        }
                        for CutNode in OrderedCutNodes.into_iter().rev() {
                            let mut AlternativeSearchBlocked =
                                SearchBlockedBeforeNoGood.clone();
                            AlternativeSearchBlocked.insert(CutNode);
                            PendingSearchStates.push((
                                AlternativeSearchBlocked,
                                ForbiddenRepeatersBeforeNoGood.clone(),
                            ));
                        }
                        if let Some((NextSearchBlocked, NextForbiddenRepeaters)) =
                            PendingSearchStates.pop()
                        {
                            SearchBlocked = NextSearchBlocked;
                            ForbiddenRepeaterPositions = NextForbiddenRepeaters;
                            NoGoodCount += 1;
                            continue;
                        }
                    }
                }
                if Result.NoPathReason == "NoRepeaterSelfExcitingCycle" {
                    LastConflictResources = Result.ConflictResources.clone();
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                        eprintln!(
                            "selected detailed repeater-cycle signal={} resources={:?} expansions={}",
                            DebugLabel, LastConflictResources, Result.ExpansionCount,
                        );
                    }
                    let CycleRepeaterPositions = LastConflictResources
                        .iter()
                        .filter_map(|(Kind, PositionValue)| {
                            (Kind == "RepeaterCycleAnchor").then_some(*PositionValue)
                        })
                        .filter(|PositionValue| !ForbiddenRepeaterPositions.contains(PositionValue))
                        .collect::<Vec<_>>();
                    let mut OrderedCutNodes = LastConflictResources
                        .iter()
                        .map(|(_Kind, PositionValue)| *PositionValue)
                        .filter(|PositionValue| {
                            !SearchBlocked.contains(PositionValue)
                        })
                        .collect::<Vec<_>>();
                    OrderedCutNodes.sort_unstable();
                    OrderedCutNodes.dedup();
                    RejectedPathCount += 1;
                    if (!OrderedCutNodes.is_empty() || !CycleRepeaterPositions.is_empty())
                        && NoGoodCount < 64
                    {
                        let SearchBlockedBeforeNoGood = SearchBlocked.clone();
                        let ForbiddenRepeatersBeforeNoGood = ForbiddenRepeaterPositions.clone();
                        // A directed-cycle no-good is the disjunction of
                        // removing one mutable cycle conductor or not placing
                        // one cycle-closing repeater.  Retain every exact
                        // branch.  Try the stronger geometry escape first so
                        // a long parallel dust loop does not simply move its
                        // forced repeater one cell per invocation.
                        for RepeaterPosition in CycleRepeaterPositions.iter().copied().rev() {
                            let mut AlternativeForbidden =
                                ForbiddenRepeatersBeforeNoGood.clone();
                            AlternativeForbidden.insert(RepeaterPosition);
                            PendingSearchStates.push((
                                SearchBlockedBeforeNoGood.clone(),
                                AlternativeForbidden,
                            ));
                        }
                        for CutNode in OrderedCutNodes.iter().copied().rev() {
                            let mut AlternativeSearchBlocked = SearchBlockedBeforeNoGood.clone();
                            AlternativeSearchBlocked.insert(CutNode);
                            PendingSearchStates.push((
                                AlternativeSearchBlocked,
                                ForbiddenRepeatersBeforeNoGood.clone(),
                            ));
                        }
                        if OrderedCutNodes.is_empty() {
                            ForbiddenRepeaterPositions
                                .insert(CycleRepeaterPositions[0]);
                        } else {
                            SearchBlocked.extend(OrderedCutNodes);
                            ForbiddenRepeaterPositions = ForbiddenRepeatersBeforeNoGood;
                        }
                        NoGoodCount += 1;
                        continue;
                    }
                }
                if let Some((AlternativeSearchBlocked, AlternativeForbiddenRepeaters)) =
                    PendingSearchStates.pop()
                {
                    SearchBlocked = AlternativeSearchBlocked;
                    ForbiddenRepeaterPositions = AlternativeForbiddenRepeaters;
                    continue;
                }
                if RejectedPathCount > 0 && !LastConflictResources.is_empty() {
                    Result.NoPathReason = "SelfClaimConflict".to_string();
                }
                Result.ConflictResources = LastConflictResources;
                return Result;
            }
            UseStrictMandatoryGuide = false;

            let RouteWire: HashSet<_> = Result.Nodes.iter().copied().collect();
            let mut InvalidRepeaterPositions = Result
                .RepeaterReservations
                .iter()
                .filter_map(|(PositionValue, Facing)| {
                    let OutputDelta = match Facing.as_str() {
                        "west" => Some((1, 0)),
                        "east" => Some((-1, 0)),
                        "north" => Some((0, 1)),
                        "south" => Some((0, -1)),
                        _ => None,
                    }?;
                    let ConnectedNeighbors = [
                        (1, 0, 0),
                        (-1, 0, 0),
                        (0, 0, 1),
                        (0, 0, -1),
                        (1, 1, 0),
                        (-1, 1, 0),
                        (0, 1, 1),
                        (0, 1, -1),
                        (1, -1, 0),
                        (-1, -1, 0),
                        (0, -1, 1),
                        (0, -1, -1),
                    ]
                    .into_iter()
                    .map(|Delta| {
                        (
                            PositionValue.0 + Delta.0,
                            PositionValue.1 + Delta.1,
                            PositionValue.2 + Delta.2,
                        )
                    })
                    .filter(|Neighbor| RouteWire.contains(Neighbor))
                    .collect::<Vec<_>>();
                    let FlatNeighbors = ConnectedNeighbors
                        .iter()
                        .filter(|Neighbor| Neighbor.1 == PositionValue.1)
                        .copied()
                        .collect::<Vec<_>>();
                    let Output = (
                        PositionValue.0 + OutputDelta.0,
                        PositionValue.1,
                        PositionValue.2 + OutputDelta.1,
                    );
                    let Input = (
                        PositionValue.0 - OutputDelta.0,
                        PositionValue.1,
                        PositionValue.2 - OutputDelta.1,
                    );
                    (!FlatNeighbors.contains(&Input) || !FlatNeighbors.contains(&Output))
                        .then_some(*PositionValue)
                })
                .collect::<Vec<_>>();
            InvalidRepeaterPositions.sort_unstable();
            InvalidRepeaterPositions.dedup();
            if !InvalidRepeaterPositions.is_empty() {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected detailed invalid repeaters signal={} positions={:?}",
                        DebugLabel, InvalidRepeaterPositions,
                    );
                }
                LastConflictResources = InvalidRepeaterPositions
                    .iter()
                    .map(|Value| ("Repeater".to_string(), *Value))
                    .collect();
                RejectedPathCount += 1;
                let CutNodes = InvalidRepeaterPositions
                    .into_iter()
                    .filter(|Value| !ForbiddenRepeaterPositions.contains(Value))
                    .collect::<HashSet<_>>();
                if CutNodes.is_empty() {
                    Result.Status = "NoPath".to_string();
                    Result.NoPathReason = "SelfClaimConflict".to_string();
                    Result.Nodes.clear();
                    Result.TargetPaths.clear();
                    Result.RepeaterReservations.clear();
                    Result.IsRouted = false;
                    Result.ConflictResources = LastConflictResources;
                    Result.RejectedPathCount = RejectedPathCount;
                    Result.NoGoodCount = NoGoodCount;
                    Result.ElapsedMilliseconds = Started.elapsed().as_millis() as u64;
                    return Result;
                }
                ForbiddenRepeaterPositions.extend(CutNodes);
                NoGoodCount += 1;
                continue;
            }
            let RouteSupport: HashSet<_> = RouteWire
                .iter()
                .map(|Value| (Value.0, Value.1 - 1, Value.2))
                .collect();
            let mut RouteAir = HashSet::new();
            for First in &RouteWire {
                for (DeltaX, DeltaZ) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
                    for DeltaY in [-1, 1] {
                        let Second = (First.0 + DeltaX, First.1 + DeltaY, First.2 + DeltaZ);
                        if Second <= *First || !RouteWire.contains(&Second) {
                            continue;
                        }
                        let Lower = if First.1 < Second.1 { *First } else { Second };
                        RouteAir.insert((Lower.0, Lower.1 + 1, Lower.2));
                    }
                }
            }
            let CombinedWire: HashSet<_> = MandatoryWire.union(&RouteWire).copied().collect();
            let CombinedSupport: HashSet<_> =
                MandatorySupport.union(&RouteSupport).copied().collect();
            // A legal vertical primitive may be formed by one immutable
            // access node and one newly-routed node.  Unioning air claims
            // computed independently for those two sets misses that exact
            // cross-boundary headroom.  Recompute over the physical union,
            // matching RoutingResourceGraph.BuildRouteClaims.
            let mut CombinedAir = MandatoryAir.clone();
            for First in &CombinedWire {
                for Second in self.Adjacency.get(First).into_iter().flatten() {
                    if Second <= First || Second.1 == First.1 || !CombinedWire.contains(Second) {
                        continue;
                    }
                    let Lower = if First.1 < Second.1 { *First } else { *Second };
                    CombinedAir.insert((Lower.0, Lower.1 + 1, Lower.2));
                }
            }
            let WireOrAir: HashSet<_> = CombinedWire.union(&CombinedAir).copied().collect();
            let SupportConflicts: HashSet<_> =
                CombinedSupport.intersection(&WireOrAir).copied().collect();
            let AirConflicts: HashSet<_> =
                CombinedAir.intersection(&CombinedWire).copied().collect();
            if SupportConflicts.is_empty() && AirConflicts.is_empty() {
                Result.ConflictResources.clear();
                Result.RejectedPathCount = RejectedPathCount;
                Result.NoGoodCount = NoGoodCount;
                Result.ElapsedMilliseconds = Started.elapsed().as_millis() as u64;
                return Result;
            }

            LastConflictResources = SupportConflicts
                .iter()
                .map(|Value| ("Support".to_string(), *Value))
                .chain(AirConflicts.iter().map(|Value| ("Air".to_string(), *Value)))
                .collect();
            LastConflictResources.sort_unstable();
            LastConflictResources.dedup();
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected detailed static conflict signal={} resources={:?}",
                    DebugLabel, LastConflictResources,
                );
            }

            let mut CutNodes = HashSet::new();
            let AddAirContributorCutNodes =
                |AirPosition: Position, Values: &mut HashSet<Position>| {
                    for First in &CombinedWire {
                        for Second in self.Adjacency.get(First).into_iter().flatten() {
                            if Second <= First
                                || Second.1 == First.1
                                || !CombinedWire.contains(Second)
                            {
                                continue;
                            }
                            let Lower = if First.1 < Second.1 { *First } else { *Second };
                            if (Lower.0, Lower.1 + 1, Lower.2) == AirPosition {
                                if RouteWire.contains(First) {
                                    Values.insert(*First);
                                }
                                if RouteWire.contains(Second) {
                                    Values.insert(*Second);
                                }
                            }
                        }
                    }
                };
            for PositionValue in &SupportConflicts {
                if RouteWire.contains(PositionValue) {
                    CutNodes.insert(*PositionValue);
                }
                let SupportedNode = (PositionValue.0, PositionValue.1 + 1, PositionValue.2);
                if RouteSupport.contains(PositionValue) {
                    CutNodes.insert(SupportedNode);
                }
                if CombinedAir.contains(PositionValue) {
                    AddAirContributorCutNodes(*PositionValue, &mut CutNodes);
                }
            }
            for PositionValue in &AirConflicts {
                if RouteWire.contains(PositionValue) {
                    CutNodes.insert(*PositionValue);
                }
                if CombinedAir.contains(PositionValue) {
                    AddAirContributorCutNodes(*PositionValue, &mut CutNodes);
                }
            }
            CutNodes.retain(|Value| !MandatoryNodes.contains(Value));
            CutNodes.retain(|Value| !SearchBlocked.contains(Value));
            RejectedPathCount += 1;
            if CutNodes.is_empty() {
                Result.Status = "NoPath".to_string();
                Result.NoPathReason = "SelfClaimConflict".to_string();
                Result.Nodes.clear();
                Result.TargetPaths.clear();
                Result.RepeaterReservations.clear();
                Result.IsRouted = false;
                Result.ConflictResources = LastConflictResources;
                Result.RejectedPathCount = RejectedPathCount;
                Result.NoGoodCount = NoGoodCount;
                Result.ElapsedMilliseconds = Started.elapsed().as_millis() as u64;
                return Result;
            }
            // A self-conflict is a disjunction: at least one mutable
            // contributor must leave the tree.  Search those exact branches
            // individually, beginning with the least guide-disruptive node.
            // Blocking the complete contributor set first can disconnect an
            // otherwise repairable branch and consume the whole finite cap
            // before any of the actual disjunctive alternatives is visited.
            let mut OrderedCutNodes = CutNodes.into_iter().collect::<Vec<_>>();
            let PreferredColumns = Guide
                .PreferredColumns
                .iter()
                .copied()
                .collect::<HashSet<_>>();
            OrderedCutNodes.sort_by_key(|PositionValue| {
                (
                    std::cmp::Reverse(
                        PreferredColumns.contains(&(PositionValue.0, PositionValue.2)),
                    ),
                    std::cmp::Reverse(PositionValue.1),
                    PositionValue.0,
                    PositionValue.2,
                )
            });
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected detailed self-conflict signal={} resources={:?} cut_nodes={:?}",
                    DebugLabel, LastConflictResources, OrderedCutNodes,
                );
            }
            let SearchBlockedBeforeNoGood = SearchBlocked.clone();
            for CutNode in OrderedCutNodes.iter().copied().rev() {
                let mut AlternativeSearchBlocked = SearchBlockedBeforeNoGood.clone();
                AlternativeSearchBlocked.insert(CutNode);
                PendingSearchStates.push((
                    AlternativeSearchBlocked,
                    ForbiddenRepeaterPositions.clone(),
                ));
            }
            let (NextSearchBlocked, NextForbiddenRepeaterPositions) =
                PendingSearchStates
                    .pop()
                    .expect("a self-conflict has at least one movable contributor");
            SearchBlocked = NextSearchBlocked;
            ForbiddenRepeaterPositions = NextForbiddenRepeaterPositions;
            NoGoodCount += 1;
            if NoGoodCount >= 64 {
                Result.Status = "NoPath".to_string();
                Result.NoPathReason = "SelfClaimConflict".to_string();
                Result.Nodes.clear();
                Result.TargetPaths.clear();
                Result.RepeaterReservations.clear();
                Result.IsRouted = false;
                Result.ConflictResources = LastConflictResources;
                Result.RejectedPathCount = RejectedPathCount;
                Result.NoGoodCount = NoGoodCount;
                Result.ElapsedMilliseconds = Started.elapsed().as_millis() as u64;
                return Result;
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn GenerateRouteTreeClaimAwareDetailedNative(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        ExternalNodeCostValues: Vec<(Position, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
        MandatoryWireValues: Vec<Position>,
        MandatorySupportValues: Vec<Position>,
        MandatoryAirValues: Vec<Position>,
        MandatoryElectricalValues: Vec<Position>,
    ) -> RouteTreeSearchResult {
        let Ok(Deadline) =
            RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds.max(1)))
        else {
            return DetailedRouteTreeBudgetExpiredResult();
        };
        let Some(Guide) = self.PrepareDetailedRouteGuide(
            &AllowedNodeValues,
            &PreferredColumns,
            &ExternalNodeCostValues,
            GuidePenalty,
            &Deadline,
        ) else {
            return DetailedRouteTreeBudgetExpiredResult();
        };
        let MandatoryWire: HashSet<_> = MandatoryWireValues.into_iter().collect();
        let MandatorySupport: HashSet<_> = MandatorySupportValues.into_iter().collect();
        let MandatoryAir: HashSet<_> = MandatoryAirValues.into_iter().collect();
        // Same-owner electrical proximity is legal.  Retain the parameter in
        // the exact interface because its identity participates in Python's
        // certificate, while support/air/wire contradictions are the static
        // self-legality rules enforced by the routing claim model.
        let _MandatoryElectrical: HashSet<_> = MandatoryElectricalValues.into_iter().collect();
        self.GenerateRouteTreeClaimAwarePreparedDetailedNative(
            &Starts,
            &TargetBranches,
            &TargetBranches,
            &Guide,
            &HashSet::new(),
            &BlockedNodeValues.into_iter().collect(),
            PreferredRoutingY,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            None,
            MaximumExpansionCount,
            &Deadline,
            &MandatoryWire,
            &MandatorySupport,
            &MandatoryAir,
            "",
        )
    }
}

pub(crate) fn GenerateRouteTreeClaimAwareDetailedBatchNative(
    Context: &RoutingContext,
    Requests: Vec<ClaimAwareDetailedRouteTreeRequest>,
    MaximumRuntimeMilliseconds: u64,
) -> RouteTreeDetailedBatchResult {
    let Started = Instant::now();
    let SearchResults: Vec<RouteTreeSearchResult> = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .map(
                |(
                    (
                        Starts,
                        TargetBranches,
                        AllowedNodes,
                        BlockedNodes,
                        PreferredColumns,
                        NodeCosts,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        EnforceSignalStrength,
                        MaximumExpansionCount,
                    ),
                    (MandatoryWire, MandatorySupport, MandatoryAir, MandatoryElectrical),
                )| {
                    let Elapsed = Started.elapsed().as_millis() as u64;
                    let Remaining = MaximumRuntimeMilliseconds.saturating_sub(Elapsed);
                    if Remaining == 0 {
                        return DetailedRouteTreeBudgetExpiredResult();
                    }
                    Context.GenerateRouteTreeClaimAwareDetailedNative(
                        Starts,
                        TargetBranches,
                        AllowedNodes,
                        BlockedNodes,
                        PreferredColumns,
                        NodeCosts,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        EnforceSignalStrength,
                        MaximumExpansionCount,
                        Remaining,
                        MandatoryWire,
                        MandatorySupport,
                        MandatoryAir,
                        MandatoryElectrical,
                    )
                },
            )
            .collect()
    });
    let CompletionMask: Vec<_> = SearchResults
        .iter()
        .map(|Value| !Value.IsBudgetExpired)
        .collect();
    let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
    RouteTreeDetailedBatchResult {
        DeadlineExceeded: CompletionMask.iter().any(|Value| !*Value),
        CompletedWork,
        TotalWork: CompletionMask.len(),
        SearchResults,
    }
}

/// Generates independent repeater-aware route trees against one immutable
/// negotiated-pass snapshot.  The shared deadline is deliberately passed into
/// every search rather than split into per-worker budgets: a queued request
/// cannot consume time after the pass has expired, and a completed result
/// remains at the same input index regardless of worker scheduling.
pub(crate) fn GenerateRouteTreeDetailedBatchNative(
    Context: &RoutingContext,
    Requests: Vec<DetailedRouteTreeRequest>,
    MaximumRuntimeMilliseconds: u64,
) -> RouteTreeDetailedBatchResult {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .expect("u64 millisecond deadlines must be representable");
    let TotalWork = Requests.len();
    let SearchResults: Vec<RouteTreeSearchResult> = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .map(
                |(
                    Starts,
                    TargetBranches,
                    AllowedNodeValues,
                    BlockedNodeValues,
                    PreferredColumns,
                    NodeCostValues,
                    PreferredRoutingY,
                    GuidePenalty,
                    BendPenalty,
                    ViaPenalty,
                    EnforceSignalStrength,
                    MaximumExpansionCount,
                )| {
                    if Deadline.Check() {
                        return DetailedRouteTreeBudgetExpiredResult();
                    }
                    Context.GenerateRouteTreeDetailedWithDeadlineNative(
                        Starts,
                        TargetBranches,
                        AllowedNodeValues,
                        BlockedNodeValues,
                        PreferredColumns,
                        NodeCostValues,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        EnforceSignalStrength,
                        MaximumExpansionCount,
                        &Deadline,
                    )
                },
            )
            .collect()
    });
    let CompletedWork = SearchResults
        .iter()
        .filter(|Result| !Result.IsBudgetExpired)
        .count();
    RouteTreeDetailedBatchResult {
        SearchResults,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    }
}

#[allow(clippy::type_complexity)]
pub(crate) fn GeneratePortalCandidateBatchesNative(
    Context: &RoutingContext,
    Requests: Vec<(
        Vec<Position>,
        Vec<Position>,
        Vec<Position>,
        i32,
        usize,
        usize,
    )>,
    MaximumRuntimeMilliseconds: Option<u64>,
) -> PyResult<PortalCandidateBatchResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(MaximumRuntimeMilliseconds)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let TotalWork = Requests.len();
    Deadline.Check();
    let WorkResults: Vec<(Vec<PortalCandidate>, bool)> = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .map(
                |(
                    Starts,
                    PortalTargets,
                    AllowedNodes,
                    PreferredRoutingY,
                    MaximumPortalCount,
                    MaximumExpansionCount,
                )| {
                    if Deadline.Check() {
                        return (Vec::new(), false);
                    }
                    let Candidates = Context.GeneratePortalCandidatesNative(
                        Starts,
                        PortalTargets,
                        AllowedNodes,
                        PreferredRoutingY,
                        MaximumPortalCount,
                        MaximumExpansionCount,
                        Deadline
                            .RemainingMilliseconds()
                            .map(|Value| Value as f64 / 1_000.0),
                    );
                    let Completed = !Deadline.Check();
                    (Candidates, Completed)
                },
            )
            .collect()
    });
    let CompletedWork = WorkResults
        .iter()
        .filter(|(_Candidates, Completed)| *Completed)
        .count();
    let CompletionMask = WorkResults
        .iter()
        .map(|(_Candidates, Completed)| *Completed)
        .collect();
    let Candidates = WorkResults
        .into_iter()
        .map(|(Values, _Completed)| Values)
        .collect();
    Ok(PortalCandidateBatchResult {
        Candidates,
        CompletionMask,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    })
}

/// Expands interned request factors inside Rust and performs the same finite
/// detailed route-tree batch as `GenerateRouteTreesNative`.  Repeated portal
/// payloads and guide-column expansion cross the Python boundary once rather
/// than once per Cartesian request value.
pub(crate) fn GenerateRouteTreesFactorizedNative(
    Context: &RoutingContext,
    AccessPayloads: Vec<FactorizedRouteTreeAccessPayload>,
    GuidePayloads: Vec<FactorizedRouteTreeGuidePayload>,
    Requests: Vec<FactorizedRouteTreeRequest>,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<RouteTreeBatchResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let TotalWork = Requests.len();
    for (AccessIndex, GuideIndex, ..) in &Requests {
        if *AccessIndex >= AccessPayloads.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "factorized route-tree request has an invalid access payload index",
            ));
        }
        if *GuideIndex >= GuidePayloads.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "factorized route-tree request has an invalid guide payload index",
            ));
        }
    }
    let PreparedAccessPayloads: Vec<_> = AccessPayloads
        .into_iter()
        .map(
            |(
                Starts,
                SourceBranch,
                TargetBranches,
                FrozenTargetBranches,
                RequiredNodes,
                BlockedNodes,
                MandatoryWire,
                MandatorySupport,
                MandatoryAir,
                MandatoryElectrical,
            )| PreparedFactorizedRouteTreeAccess {
                Starts,
                SourceBranch,
                TargetBranches,
                FrozenTargetBranches,
                RequiredNodes: RequiredNodes.into_iter().collect(),
                BlockedNodes: BlockedNodes.into_iter().collect(),
                MandatoryWire: MandatoryWire.into_iter().collect(),
                MandatorySupport: MandatorySupport.into_iter().collect(),
                MandatoryAir: MandatoryAir.into_iter().collect(),
                MandatoryElectrical: MandatoryElectrical.into_iter().collect(),
            },
        )
        .collect();
    let mut AllowedNodeValuesByGuide = Vec::with_capacity(GuidePayloads.len());
    for (
        GuideIndex,
        (
            AllowedColumns,
            _PreferredColumns,
            _ExactHintNodes,
            _CertifiedPaths,
            _CertifiedRepeaters,
        ),
    ) in
        GuidePayloads.iter().enumerate()
    {
        let mut AllowedNodes = Vec::new();
        for (ColumnIndex, Column) in AllowedColumns.iter().enumerate() {
            if (GuideIndex + ColumnIndex) % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Ok(RouteTreeBatchResult {
                    RouteTrees: vec![None; TotalWork],
                    RepeaterReservations: vec![Vec::new(); TotalWork],
                    CompletionMask: vec![false; TotalWork],
                    DeadlineExceeded: true,
                    CompletedWork: 0,
                    TotalWork,
                });
            }
            if let Some(Values) = Context.NodesByColumn.get(Column) {
                AllowedNodes.extend(Values.iter().copied());
            }
        }
        AllowedNodes.sort_unstable();
        AllowedNodes.dedup();
        if std::env::var_os("RCS_DEBUG_SELECTED_WORLD_FULL_GRAPH").is_some() {
            AllowedNodes = Context.Adjacency.keys().copied().collect();
            AllowedNodes.sort_unstable();
        }
        AllowedNodeValuesByGuide.push(AllowedNodes);
    }
    let mut PreparedGuideIndexByKey = HashMap::new();
    let mut PreparedGuides = Vec::new();
    for (_AccessIndex, GuideIndex, _PreferredRoutingY, GuidePenalty, ..) in &Requests {
        let Key = (*GuideIndex, *GuidePenalty);
        if PreparedGuideIndexByKey.contains_key(&Key) {
            continue;
        }
        let Some(mut PreparedGuide) = Context.PrepareDetailedRouteGuide(
            &AllowedNodeValuesByGuide[*GuideIndex],
            &GuidePayloads[*GuideIndex].1,
            &[],
            *GuidePenalty,
            &Deadline,
        ) else {
            return Ok(RouteTreeBatchResult {
                RouteTrees: vec![None; TotalWork],
                RepeaterReservations: vec![Vec::new(); TotalWork],
                CompletionMask: vec![false; TotalWork],
                DeadlineExceeded: true,
                CompletedWork: 0,
                TotalWork,
            });
        };
        PreparedGuide.ExactHintNodes.extend(
            GuidePayloads[*GuideIndex]
                .2
                .iter()
                .copied()
                .filter(|Value| Context.Adjacency.contains_key(Value)),
        );
        PreparedGuide.CertifiedPaths = GuidePayloads[*GuideIndex].3.clone();
        PreparedGuide.CertifiedRepeaters = GuidePayloads[*GuideIndex].4.clone();
        PreparedGuideIndexByKey.insert(Key, PreparedGuides.len());
        PreparedGuides.push(PreparedGuide);
    }
    let PreparedRequests: Vec<_> = Requests
        .into_iter()
        .map(|Request| {
            let PreparedGuideIndex = PreparedGuideIndexByKey[&(Request.1, Request.3)];
            (PreparedGuideIndex, Request)
        })
        .collect();
    let WorkResults: Vec<(Option<Vec<Position>>, Vec<(Position, String)>, bool)> =
        RoutingThreadPool().install(|| {
            PreparedRequests
                .into_par_iter()
                .map(
                    |(
                        PreparedGuideIndex,
                        (
                            AccessIndex,
                            _GuideIndex,
                            PreferredRoutingY,
                            _GuidePenalty,
                            BendPenalty,
                            ViaPenalty,
                            MaximumExpansionCount,
                        ),
                    )| {
                        if Deadline.Check() {
                            return (None, Vec::new(), false);
                        }
                        let Access = &PreparedAccessPayloads[AccessIndex];
                        let SearchResult = Context
                            .GenerateRouteTreeClaimAwarePreparedDetailedNative(
                                &Access.Starts,
                                &Access.TargetBranches,
                                &Access.FrozenTargetBranches,
                                &PreparedGuides[PreparedGuideIndex],
                                &Access.RequiredNodes,
                                &Access.BlockedNodes,
                                PreferredRoutingY,
                                BendPenalty,
                                ViaPenalty,
                                true,
                                Some(&Access.SourceBranch),
                                MaximumExpansionCount,
                                &Deadline,
                                &Access.MandatoryWire,
                                &Access.MandatorySupport,
                                &Access.MandatoryAir,
                                "",
                            );
                        let Completed = !SearchResult.IsBudgetExpired;
                        let RouteTree = SearchResult.IsRouted.then_some(SearchResult.Nodes);
                        (RouteTree, SearchResult.RepeaterReservations, Completed)
                    },
                )
                .collect()
        });
    let CompletionMask: Vec<_> = WorkResults
        .iter()
        .map(|(_RouteTree, _Repeaters, Completed)| *Completed)
        .collect();
    let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
    let (RouteTrees, RepeaterReservations): (Vec<_>, Vec<_>) = WorkResults
        .into_iter()
        .map(|(RouteTree, Repeaters, _Completed)| (RouteTree, Repeaters))
        .unzip();
    Ok(RouteTreeBatchResult {
        RouteTrees,
        RepeaterReservations,
        CompletionMask,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    })
}

/// Generate exact selected-world route candidates in deterministic waves and
/// solve their physical claim capacity inside the same bounded native call.
/// This avoids eagerly expanding every declared request shape before the
/// assignment solver can use its first witness.  A successful witness is
/// exact; exhausted work without a witness remains incomplete.
pub(crate) fn GenerateAndAssignRouteTreesFactorizedNative(
    Context: &RoutingContext,
    AccessPayloads: Vec<FactorizedRouteTreeAccessPayload>,
    GuidePayloads: Vec<FactorizedRouteTreeGuidePayload>,
    Requests: Vec<FactorizedRouteTreeRequest>,
    SignalRequestIndices: Vec<(String, Vec<usize>)>,
    MaximumAssignmentExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<FactorizedRouteTreeSelectionResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let TotalWork = Requests.len();
    if SignalRequestIndices.is_empty()
        || SignalRequestIndices
            .iter()
            .any(|(Signal, Values)| Signal.is_empty() || Values.is_empty())
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "factorized selected-world assignment requires nonempty signal domains",
        ));
    }
    let mut SeenRequestIndices = HashSet::new();
    for (_Signal, Values) in &SignalRequestIndices {
        for RequestIndex in Values {
            if *RequestIndex >= Requests.len() || !SeenRequestIndices.insert(*RequestIndex) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "factorized selected-world signal domains must uniquely own valid requests",
                ));
            }
        }
    }
    for (AccessIndex, GuideIndex, ..) in &Requests {
        if *AccessIndex >= AccessPayloads.len() || *GuideIndex >= GuidePayloads.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "factorized selected-world request references an invalid payload",
            ));
        }
    }
    let PreparedAccessPayloads: Vec<_> = AccessPayloads
        .into_iter()
        .map(
            |(
                Starts,
                SourceBranch,
                TargetBranches,
                FrozenTargetBranches,
                RequiredNodes,
                BlockedNodes,
                MandatoryWire,
                MandatorySupport,
                MandatoryAir,
                MandatoryElectrical,
            )| PreparedFactorizedRouteTreeAccess {
                Starts,
                SourceBranch,
                TargetBranches,
                FrozenTargetBranches,
                RequiredNodes: RequiredNodes.into_iter().collect(),
                BlockedNodes: BlockedNodes.into_iter().collect(),
                MandatoryWire: MandatoryWire.into_iter().collect(),
                MandatorySupport: MandatorySupport.into_iter().collect(),
                MandatoryAir: MandatoryAir.into_iter().collect(),
                MandatoryElectrical: MandatoryElectrical.into_iter().collect(),
            },
        )
        .collect();
    let GroupCount = SignalRequestIndices.len();
    let mut PreparedGuideIndexByKey = HashMap::new();
    let mut PreparedGuides = Vec::new();
    let mut CandidateGroups = vec![Vec::<ExactSelectedWorldRouteCandidate>::new(); GroupCount];
    let mut CompletionMask = vec![false; TotalWork];
    let mut NextRequestOffsetByGroup = vec![0usize; GroupCount];
    let mut RouteExpansionCountByRequest = vec![0usize; TotalWork];
    let mut AlternativeBlockedNodesByRequest = vec![HashSet::<Position>::new(); TotalWork];
    let EffectiveMaximumAssignmentExpansionCount =
        MaximumAssignmentExpansionCount.clamp(1, 1_000_000);
    let mut AssignmentExpansionCount = 0usize;
    let mut GeneratedRequestCount = 0usize;
    let mut SawIncompleteRequest = false;
    let mut ReevaluateCandidateAssignment = false;
    let mut GreedyRepairAttemptKeys =
        HashSet::<(usize, usize, Vec<(usize, usize)>)>::new();
    let mut GreedyBlockedNodesByForeignCandidate =
        HashMap::<(usize, usize, usize), HashSet<Position>>::new();
    loop {
        let HasEmptyCandidateGroup = CandidateGroups.iter().any(Vec::is_empty);
        let Wave = if ReevaluateCandidateAssignment {
            Vec::new()
        } else {
            SignalRequestIndices
                .iter()
                .enumerate()
                .filter(|(GroupIndex, _Value)| {
                    !HasEmptyCandidateGroup || CandidateGroups[*GroupIndex].is_empty()
                })
                .filter_map(|(GroupIndex, (_Signal, RequestIndices))| {
                    RequestIndices
                        .get(NextRequestOffsetByGroup[GroupIndex])
                        .copied()
                        .map(|RequestIndex| (GroupIndex, RequestIndex))
                })
                .collect::<Vec<_>>()
        };
        if Deadline.Check() || (Wave.is_empty() && !ReevaluateCandidateAssignment) {
            let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
            let DeadlineExceeded = Deadline.Check();
            return Ok(FactorizedRouteTreeSelectionResult {
                RouteTrees: vec![None; TotalWork],
                RepeaterReservations: vec![Vec::new(); TotalWork],
                CompletionMask,
                SelectedRequestIndices: Vec::new(),
                Success: false,
                Complete: !DeadlineExceeded && !SawIncompleteRequest && Wave.is_empty(),
                DeadlineExceeded,
                WorkCapExceeded: SawIncompleteRequest,
                AssignmentExpansionCount,
                GeneratedRequestCount,
                GeneratedRequestCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), NextRequestOffsetByGroup[GroupIndex])
                    })
                    .collect(),
                CandidateCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), CandidateGroups[GroupIndex].len())
                    })
                    .collect(),
                CompletedWork,
                TotalWork,
            });
        }
        ReevaluateCandidateAssignment = false;
        for (_GroupIndex, RequestIndex) in &Wave {
            let Request = Requests[*RequestIndex];
            let Key = (Request.1, Request.2, Request.3);
            if PreparedGuideIndexByKey.contains_key(&Key) {
                continue;
            }
            let PreparedGuide = if std::env::var_os(
                "RCS_DEBUG_SELECTED_WORLD_FULL_GRAPH",
            )
            .is_some()
            {
                let AllNodes = Context.Adjacency.keys().copied().collect::<Vec<_>>();
                let VerticalCosts = AllNodes
                    .iter()
                    .filter_map(|PositionValue| {
                        let Distance = (PositionValue.1 - Request.2).abs();
                        (Distance > 0).then_some((
                            *PositionValue,
                            Distance.saturating_mul(Request.3.max(1)),
                        ))
                    })
                    .collect::<Vec<_>>();
                Context.PrepareDetailedRouteGuide(
                    &AllNodes,
                    &GuidePayloads[Request.1].1,
                    &VerticalCosts,
                    Request.3,
                    &Deadline,
                )
            } else {
                Context.PrepareDetailedRouteGuideFromColumns(
                    &GuidePayloads[Request.1].0,
                    &GuidePayloads[Request.1].1,
                    Request.2,
                    Request.3,
                    &Deadline,
                )
            };
            let Some(mut PreparedGuide) = PreparedGuide else {
                continue;
            };
            PreparedGuide.ExactHintNodes.extend(
                GuidePayloads[Request.1]
                    .2
                    .iter()
                    .copied()
                    .filter(|Value| Context.Adjacency.contains_key(Value)),
            );
            PreparedGuide.CertifiedPaths = GuidePayloads[Request.1].3.clone();
            PreparedGuide.CertifiedRepeaters = GuidePayloads[Request.1].4.clone();
            PreparedGuideIndexByKey.insert(Key, PreparedGuides.len());
            PreparedGuides.push(PreparedGuide);
        }
        if Deadline.Check() {
            continue;
        }
        for (GroupIndex, _RequestIndex) in &Wave {
            NextRequestOffsetByGroup[*GroupIndex] += 1;
        }
        let WaveResults = RoutingThreadPool().install(|| {
            Wave.par_iter()
                .map(|(GroupIndex, RequestIndex)| {
                    if Deadline.Check() {
                        return (*GroupIndex, *RequestIndex, None, false, 0usize);
                    }
                    let (
                        AccessIndex,
                        GuideIndex,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        MaximumExpansionCount,
                    ) = Requests[*RequestIndex];
                    let Some(PreparedGuideIndex) = PreparedGuideIndexByKey
                        .get(&(GuideIndex, PreferredRoutingY, GuidePenalty))
                        .copied()
                    else {
                        return (*GroupIndex, *RequestIndex, None, false, 0usize);
                    };
                    let Access = &PreparedAccessPayloads[AccessIndex];
                    let SearchResult = Context.GenerateRouteTreeClaimAwarePreparedDetailedNative(
                        &Access.Starts,
                        &Access.TargetBranches,
                        &Access.FrozenTargetBranches,
                        &PreparedGuides[PreparedGuideIndex],
                        &Access.RequiredNodes,
                        &Access.BlockedNodes,
                        PreferredRoutingY,
                        BendPenalty,
                        ViaPenalty,
                        true,
                        Some(&Access.SourceBranch),
                        MaximumExpansionCount,
                        &Deadline,
                        &Access.MandatoryWire,
                        &Access.MandatorySupport,
                        &Access.MandatoryAir,
                        &SignalRequestIndices[*GroupIndex].0,
                    );
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                        || std::env::var_os("RCS_DEBUG_SELECTED_WORLD_SUMMARY").is_some()
                    {
                        eprintln!(
                            "selected detailed signal={} status={} reason={} expansions={} rejected={} nogoods={}",
                            SignalRequestIndices[*GroupIndex].0,
                            SearchResult.Status,
                            SearchResult.NoPathReason,
                            SearchResult.ExpansionCount,
                            SearchResult.RejectedPathCount,
                            SearchResult.NoGoodCount,
                        );
                    }
                    let Complete = !SearchResult.IsBudgetExpired;
                    let Candidate = SearchResult.IsRouted.then(|| {
                        let Claims = BuildExactSelectedWorldRouteClaims(
                            Context,
                            &SearchResult.Nodes,
                            Access,
                        );
                        ExactSelectedWorldRouteCandidate {
                            RequestIndex: *RequestIndex,
                            Nodes: SearchResult.Nodes,
                            RepeaterReservations: SearchResult.RepeaterReservations,
                            Claims,
                        }
                    });
                    (
                        *GroupIndex,
                        *RequestIndex,
                        Candidate,
                        Complete,
                        SearchResult.ExpansionCount,
                    )
                })
                .collect::<Vec<_>>()
        });
        for (GroupIndex, RequestIndex, Candidate, Complete, RouteExpansionCount) in WaveResults {
            GeneratedRequestCount += 1;
            RouteExpansionCountByRequest[RequestIndex] =
                RouteExpansionCountByRequest[RequestIndex].saturating_add(RouteExpansionCount);
            CompletionMask[RequestIndex] = Complete;
            SawIncompleteRequest |= !Complete;
            if let Some(Value) = Candidate {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE_VERBOSE").is_some() {
                    eprintln!(
                        "selected detailed candidate signal={} nodes={:?} repeaters={:?}",
                        SignalRequestIndices[GroupIndex].0,
                        Value.Nodes,
                        Value.RepeaterReservations,
                    );
                }
                CandidateGroups[GroupIndex].push(Value);
            }
        }
        if Deadline.Check() || CandidateGroups.iter().any(Vec::is_empty) {
            continue;
        }
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            for FirstIndex in 0..CandidateGroups.len() {
                for SecondIndex in FirstIndex + 1..CandidateGroups.len() {
                    if ExactSelectedWorldClaimsConflict(
                        &CandidateGroups[FirstIndex][0].Claims,
                        &CandidateGroups[SecondIndex][0].Claims,
                    ) {
                        let FirstAccess = &PreparedAccessPayloads
                            [Requests[CandidateGroups[FirstIndex][0].RequestIndex].0];
                        let SecondAccess = &PreparedAccessPayloads
                            [Requests[CandidateGroups[SecondIndex][0].RequestIndex].0];
                        eprintln!(
                            "selected detailed cross-conflict first={} second={} first_nodes={:?} second_nodes={:?}",
                            SignalRequestIndices[FirstIndex].0,
                            SignalRequestIndices[SecondIndex].0,
                            FindExactSelectedWorldMovableConflictNodes(
                                Context,
                                &CandidateGroups[FirstIndex][0],
                                FirstAccess,
                                &CandidateGroups[SecondIndex][0].Claims,
                            ),
                            FindExactSelectedWorldMovableConflictNodes(
                                Context,
                                &CandidateGroups[SecondIndex][0],
                                SecondAccess,
                                &CandidateGroups[FirstIndex][0].Claims,
                            ),
                        );
                    }
                }
            }
        }
        let mut SelectedCandidateIndices = Vec::with_capacity(GroupCount);
        if let Some(SelectedCandidateIndices) = SearchExactSelectedWorldAssignment(
            &CandidateGroups,
            0,
            &mut SelectedCandidateIndices,
            &mut AssignmentExpansionCount,
            EffectiveMaximumAssignmentExpansionCount,
            &Deadline,
        ) {
            let mut RouteTrees = vec![None; TotalWork];
            let mut RepeaterReservations = vec![Vec::new(); TotalWork];
            let mut SelectedRequestIndices = Vec::with_capacity(GroupCount);
            for (GroupIndex, CandidateIndex) in SelectedCandidateIndices.into_iter().enumerate() {
                let Candidate = &CandidateGroups[GroupIndex][CandidateIndex];
                RouteTrees[Candidate.RequestIndex] = Some(Candidate.Nodes.clone());
                RepeaterReservations[Candidate.RequestIndex] =
                    Candidate.RepeaterReservations.clone();
                SelectedRequestIndices.push(Candidate.RequestIndex);
            }
            let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
            return Ok(FactorizedRouteTreeSelectionResult {
                RouteTrees,
                RepeaterReservations,
                CompletionMask,
                SelectedRequestIndices,
                Success: true,
                Complete: true,
                DeadlineExceeded: false,
                WorkCapExceeded: false,
                AssignmentExpansionCount,
                GeneratedRequestCount,
                GeneratedRequestCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), NextRequestOffsetByGroup[GroupIndex])
                    })
                    .collect(),
                CandidateCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), CandidateGroups[GroupIndex].len())
                    })
                    .collect(),
                CompletedWork,
                TotalWork,
            });
        }
        // The independently generated least-cost trees can form a chain of
        // obsolete pairwise conflicts: repairing a late signal against the
        // first candidate of its sibling does not help after that sibling has
        // already moved.  Before adding another pairwise no-good, build one
        // deterministic partial witness in signal order.  When a group has no
        // compatible existing value, materialize one alternative against the
        // exact claims already selected for every earlier group.  This stays
        // inside the same native invocation and consumes the request's
        // original expansion share and absolute deadline.
        let mut GreedyGroupOrder = (0..CandidateGroups.len()).collect::<Vec<_>>();
        GreedyGroupOrder.sort_by_key(|GroupIndex| {
            let Candidate = &CandidateGroups[*GroupIndex][0];
            let Access = &PreparedAccessPayloads[Requests[Candidate.RequestIndex].0];
            let mut ConflictDegree = 0usize;
            let mut ImmovableConflictDegree = 0usize;
            let mut MovableConflictNodeCount = 0usize;
            for (OtherGroupIndex, OtherGroup) in CandidateGroups.iter().enumerate() {
                if OtherGroupIndex == *GroupIndex
                    || !ExactSelectedWorldClaimsConflict(
                        &Candidate.Claims,
                        &OtherGroup[0].Claims,
                    )
                {
                    continue;
                }
                ConflictDegree += 1;
                let MovableConflictNodes = FindExactSelectedWorldMovableConflictNodes(
                    Context,
                    Candidate,
                    Access,
                    &OtherGroup[0].Claims,
                );
                if MovableConflictNodes.is_empty() {
                    ImmovableConflictDegree += 1;
                }
                MovableConflictNodeCount = MovableConflictNodeCount
                    .saturating_add(MovableConflictNodes.len());
            }
            (
                CandidateGroups[*GroupIndex].len(),
                std::cmp::Reverse(ImmovableConflictDegree),
                std::cmp::Reverse(ConflictDegree),
                MovableConflictNodeCount,
                *GroupIndex,
            )
        });
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            eprintln!(
                "selected detailed greedy order={:?}",
                GreedyGroupOrder
                    .iter()
                    .map(|GroupIndex| SignalRequestIndices[*GroupIndex].0.clone())
                    .collect::<Vec<_>>()
            );
        }
        let mut GreedySelectedCandidateIndices = BTreeMap::<usize, usize>::new();
        let mut AddedGreedyAlternative = false;
        let mut AdvancedGreedyNoGood = false;
        for GroupIndex in GreedyGroupOrder {
            let CompatibleCandidateIndex = CandidateGroups[GroupIndex]
                .iter()
                .enumerate()
                .filter(|(_CandidateIndex, Candidate)| {
                    GreedySelectedCandidateIndices.iter().all(
                        |(PriorGroupIndex, PriorCandidateIndex)| {
                            !ExactSelectedWorldClaimsConflict(
                                &Candidate.Claims,
                                &CandidateGroups[*PriorGroupIndex][*PriorCandidateIndex].Claims,
                            )
                        },
                    )
                })
                .min_by_key(|(CandidateIndex, Candidate)| {
                    let mut FutureDeadEndCount = 0usize;
                    let mut FutureConflictCount = 0usize;
                    for (FutureGroupIndex, FutureGroup) in
                        CandidateGroups.iter().enumerate()
                    {
                        if FutureGroupIndex == GroupIndex
                            || GreedySelectedCandidateIndices
                                .contains_key(&FutureGroupIndex)
                        {
                            continue;
                        }
                        let CompatibleFutureCount = FutureGroup
                            .iter()
                            .filter(|FutureCandidate| {
                                !ExactSelectedWorldClaimsConflict(
                                    &Candidate.Claims,
                                    &FutureCandidate.Claims,
                                )
                            })
                            .count();
                        FutureDeadEndCount +=
                            usize::from(CompatibleFutureCount == 0);
                        FutureConflictCount = FutureConflictCount
                            .saturating_add(
                                FutureGroup.len() - CompatibleFutureCount,
                            );
                    }
                    (
                        FutureDeadEndCount,
                        FutureConflictCount,
                        *CandidateIndex,
                    )
                })
                .map(|(CandidateIndex, _Candidate)| CandidateIndex);
            if let Some(CandidateIndex) = CompatibleCandidateIndex {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected detailed greedy choose signal={} candidate={}",
                        SignalRequestIndices[GroupIndex].0,
                        CandidateIndex,
                    );
                }
                GreedySelectedCandidateIndices.insert(GroupIndex, CandidateIndex);
                continue;
            }
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                for (CandidateIndex, Candidate) in CandidateGroups[GroupIndex].iter().enumerate() {
                    let Access =
                        &PreparedAccessPayloads[Requests[Candidate.RequestIndex].0];
                    for (PriorGroupIndex, PriorCandidateIndex) in
                        &GreedySelectedCandidateIndices
                    {
                        let PriorCandidate =
                            &CandidateGroups[*PriorGroupIndex][*PriorCandidateIndex];
                        if ExactSelectedWorldClaimsConflict(
                            &Candidate.Claims,
                            &PriorCandidate.Claims,
                        ) {
                            eprintln!(
                                "selected detailed greedy conflict signal={} candidate={} prior={} prior_candidate={} movable={:?} resources={:?}",
                                SignalRequestIndices[GroupIndex].0,
                                CandidateIndex,
                                SignalRequestIndices[*PriorGroupIndex].0,
                                PriorCandidateIndex,
                                FindExactSelectedWorldMovableConflictNodes(
                                    Context,
                                    Candidate,
                                    Access,
                                    &PriorCandidate.Claims,
                                ),
                                ExactSelectedWorldConflictResources(
                                    &Candidate.Claims,
                                    &PriorCandidate.Claims,
                                ),
                            );
                        }
                    }
                }
            }
            let mut RepairOptions = CandidateGroups[GroupIndex]
                .iter()
                .enumerate()
                .filter_map(|(CandidateIndex, Candidate)| {
                    let Access = &PreparedAccessPayloads[Requests[Candidate.RequestIndex].0];
                    let mut ConflictNodes = HashSet::new();
                    let mut HasNewPairConflictNode = false;
                    for (PriorGroupIndex, PriorCandidateIndex) in
                        &GreedySelectedCandidateIndices
                    {
                        let PairConflictNodes =
                            FindExactSelectedWorldMovableConflictNodes(
                            Context,
                            Candidate,
                            Access,
                            &CandidateGroups[*PriorGroupIndex][*PriorCandidateIndex].Claims,
                        );
                        let RetainedPairNoGood =
                            GreedyBlockedNodesByForeignCandidate.get(&(
                                Candidate.RequestIndex,
                                *PriorGroupIndex,
                                *PriorCandidateIndex,
                            ));
                        HasNewPairConflictNode |= PairConflictNodes.iter().any(
                            |PositionValue| {
                                RetainedPairNoGood
                                    .is_none_or(|Values| {
                                        !Values.contains(PositionValue)
                                    })
                            },
                        );
                        ConflictNodes.extend(PairConflictNodes);
                    }
                    if ConflictNodes.is_empty() || !HasNewPairConflictNode {
                        return None;
                    }
                    let mut NewConflictNodes = ConflictNodes.into_iter().collect::<Vec<_>>();
                    NewConflictNodes.sort_unstable();
                    if NewConflictNodes.is_empty() {
                        return None;
                    }
                    let RepairAttemptKey = (
                        GroupIndex,
                        CandidateIndex,
                        GreedySelectedCandidateIndices
                            .iter()
                            .map(|(PriorGroupIndex, PriorCandidateIndex)| {
                                (*PriorGroupIndex, *PriorCandidateIndex)
                            })
                            .collect::<Vec<_>>(),
                    );
                    (!GreedyRepairAttemptKeys.contains(&RepairAttemptKey)).then_some((
                        NewConflictNodes.len(),
                        GroupIndex,
                        CandidateIndex,
                        Candidate.clone(),
                        NewConflictNodes,
                        RepairAttemptKey,
                        None,
                    ))
                })
                .collect::<Vec<_>>();
            for (CurrentCandidateIndex, CurrentCandidate) in
                CandidateGroups[GroupIndex].iter().enumerate()
            {
                for (PriorGroupIndex, PriorCandidateIndex) in
                    &GreedySelectedCandidateIndices
                {
                    let PriorCandidate =
                        &CandidateGroups[*PriorGroupIndex][*PriorCandidateIndex];
                    if !ExactSelectedWorldClaimsConflict(
                        &CurrentCandidate.Claims,
                        &PriorCandidate.Claims,
                    ) {
                        continue;
                    }
                    let PriorAccess = &PreparedAccessPayloads
                        [Requests[PriorCandidate.RequestIndex].0];
                    let PriorConflictNodes = FindExactSelectedWorldMovableConflictNodes(
                        Context,
                        PriorCandidate,
                        PriorAccess,
                        &CurrentCandidate.Claims,
                    );
                    if PriorConflictNodes.is_empty()
                        || PriorConflictNodes.iter().all(|PositionValue| {
                            GreedyBlockedNodesByForeignCandidate
                                .get(&(
                                    PriorCandidate.RequestIndex,
                                    GroupIndex,
                                    CurrentCandidateIndex,
                                ))
                                .is_some_and(|Values| {
                                    Values.contains(PositionValue)
                                })
                        })
                    {
                        continue;
                    }
                    let mut NewConflictNodes =
                        PriorConflictNodes.into_iter().collect::<Vec<_>>();
                    NewConflictNodes.sort_unstable();
                    if NewConflictNodes.is_empty() {
                        continue;
                    }
                    let mut ForeignCandidateIndices = GreedySelectedCandidateIndices
                        .iter()
                        .filter(|(SelectedGroupIndex, _CandidateIndex)| {
                            **SelectedGroupIndex != *PriorGroupIndex
                        })
                        .map(|(SelectedGroupIndex, CandidateIndex)| {
                            (*SelectedGroupIndex, *CandidateIndex)
                        })
                        .collect::<Vec<_>>();
                    ForeignCandidateIndices.push((GroupIndex, CurrentCandidateIndex));
                    ForeignCandidateIndices.sort_unstable();
                    let RepairAttemptKey = (
                        *PriorGroupIndex,
                        *PriorCandidateIndex,
                        ForeignCandidateIndices,
                    );
                    if GreedyRepairAttemptKeys.contains(&RepairAttemptKey) {
                        continue;
                    }
                    RepairOptions.push((
                        NewConflictNodes.len(),
                        *PriorGroupIndex,
                        *PriorCandidateIndex,
                        PriorCandidate.clone(),
                        NewConflictNodes,
                        RepairAttemptKey,
                        Some(CurrentCandidateIndex),
                    ));
                }
            }
            let RepairOption = RepairOptions.into_iter().min_by_key(
                |(
                    NodeCount,
                    RepairGroupIndex,
                    CandidateIndex,
                    Candidate,
                    Nodes,
                    _RepairAttemptKey,
                    FixedCurrentCandidateIndex,
                )| {
                    let RequestIndex = Candidate.RequestIndex;
                    let RemainingExpansionCount = Requests[RequestIndex]
                        .6
                        .saturating_sub(RouteExpansionCountByRequest[RequestIndex]);
                    (
                        // First try materializing the group that actually
                        // failed against the complete partial witness.  The
                        // reverse options remain available, but preferring
                        // them here reroutes an already accepted sibling once
                        // for every candidate of the failing group and can
                        // consume that sibling's bounded request share before
                        // the failing group is searched at all.
                        FixedCurrentCandidateIndex.is_some() as usize,
                        std::cmp::Reverse(RemainingExpansionCount),
                        *NodeCount,
                        *RepairGroupIndex,
                        *CandidateIndex,
                        Nodes.first().copied(),
                    )
                },
            );
            let Some((
                _NodeCount,
                RepairGroupIndex,
                _CandidateIndex,
                BaseCandidate,
                NewConflictNodes,
                RepairAttemptKey,
                FixedCurrentCandidateIndex,
            )) = RepairOption
            else {
                break;
            };
            let ForeignCandidateIndices = RepairAttemptKey.2.clone();
            GreedyRepairAttemptKeys.insert(RepairAttemptKey);
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected detailed greedy repair signal={} candidate={} blocked_nodes={}",
                    SignalRequestIndices[RepairGroupIndex].0,
                    _CandidateIndex,
                    NewConflictNodes.len(),
                );
            }
            let RequestIndex = BaseCandidate.RequestIndex;
            let (
                AccessIndex,
                GuideIndex,
                PreferredRoutingY,
                GuidePenalty,
                BendPenalty,
                ViaPenalty,
                MaximumExpansionCount,
            ) = Requests[RequestIndex];
            let Access = &PreparedAccessPayloads[AccessIndex];
            AdvancedGreedyNoGood = true;
            let RemainingExpansionCount =
                MaximumExpansionCount.saturating_sub(RouteExpansionCountByRequest[RequestIndex]);
            if RemainingExpansionCount == 0 {
                SawIncompleteRequest = true;
                break;
            }
            let Some(PreparedGuideIndex) = PreparedGuideIndexByKey
                .get(&(GuideIndex, PreferredRoutingY, GuidePenalty))
                .copied()
            else {
                SawIncompleteRequest = true;
                break;
            };
            let mut GreedyBlockedNodes = Access.BlockedNodes.clone();
            for (ForeignGroupIndex, ForeignCandidateIndex) in
                ForeignCandidateIndices
            {
                let ForeignCandidate =
                    &CandidateGroups[ForeignGroupIndex][ForeignCandidateIndex];
                let PairConflictNodes = FindExactSelectedWorldMovableConflictNodes(
                    Context,
                    &BaseCandidate,
                    Access,
                    &ForeignCandidate.Claims,
                );
                let ExactForeignBlockedNodes =
                    BuildExactSelectedWorldForeignBlockedNodes(
                        Context,
                        [ForeignCandidate.Claims.clone()],
                    );
                let LocalExactForeignBlockedNodes = ExactForeignBlockedNodes
                    .into_iter()
                    .filter(|PositionValue| {
                        PairConflictNodes.iter().any(|ConflictNode| {
                            ManhattanDistance(*PositionValue, *ConflictNode) <= 2
                        })
                    })
                    .collect::<Vec<_>>();
                let RetainedPairNoGood = GreedyBlockedNodesByForeignCandidate
                    .entry((
                        RequestIndex,
                        ForeignGroupIndex,
                        ForeignCandidateIndex,
                    ))
                    .or_default();
                RetainedPairNoGood.extend(PairConflictNodes);
                RetainedPairNoGood.extend(LocalExactForeignBlockedNodes);
                GreedyBlockedNodes.extend(RetainedPairNoGood.iter().copied());
            }
            GreedyBlockedNodes.extend(NewConflictNodes);
            let SearchResult = Context.GenerateRouteTreeClaimAwarePreparedDetailedNative(
                &Access.Starts,
                &Access.TargetBranches,
                &Access.FrozenTargetBranches,
                &PreparedGuides[PreparedGuideIndex],
                &Access.RequiredNodes,
                &GreedyBlockedNodes,
                PreferredRoutingY,
                BendPenalty,
                ViaPenalty,
                true,
                Some(&Access.SourceBranch),
                RemainingExpansionCount,
                &Deadline,
                &Access.MandatoryWire,
                &Access.MandatorySupport,
                &Access.MandatoryAir,
                &SignalRequestIndices[RepairGroupIndex].0,
            );
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected detailed greedy result signal={} status={} reason={} expansions={} blocked_total={} foreign_sets={}",
                    SignalRequestIndices[RepairGroupIndex].0,
                    SearchResult.Status,
                    SearchResult.NoPathReason,
                    SearchResult.ExpansionCount,
                    GreedyBlockedNodes.len(),
                    usize::from(FixedCurrentCandidateIndex.is_some()),
                );
            }
            GeneratedRequestCount += 1;
            RouteExpansionCountByRequest[RequestIndex] = RouteExpansionCountByRequest[RequestIndex]
                .saturating_add(SearchResult.ExpansionCount);
            if SearchResult.IsBudgetExpired {
                SawIncompleteRequest = true;
                break;
            }
            if SearchResult.IsRouted {
                let Claims =
                    BuildExactSelectedWorldRouteClaims(Context, &SearchResult.Nodes, Access);
                let NewCandidate = ExactSelectedWorldRouteCandidate {
                    RequestIndex,
                    Nodes: SearchResult.Nodes,
                    RepeaterReservations: SearchResult.RepeaterReservations,
                    Claims,
                };
                if !CandidateGroups[RepairGroupIndex].iter().any(|Existing| {
                    Existing.Nodes == NewCandidate.Nodes
                        && Existing.RepeaterReservations == NewCandidate.RepeaterReservations
                }) {
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                        eprintln!(
                            "selected detailed greedy candidate signal={} nodes={:?} repeaters={:?}",
                            SignalRequestIndices[RepairGroupIndex].0,
                            NewCandidate.Nodes,
                            NewCandidate.RepeaterReservations,
                        );
                    }
                    CandidateGroups[RepairGroupIndex].push(NewCandidate);
                    AddedGreedyAlternative = true;
                }
            }
            break;
        }
        if AddedGreedyAlternative || (AdvancedGreedyNoGood && !SawIncompleteRequest) {
            ReevaluateCandidateAssignment = true;
            continue;
        }
        // A selected guide factor can admit multiple exact route trees.  If
        // the least-cost trees conflict across signals, add one exact
        // movable-node no-good and materialize the next tree inside this
        // same bounded native operation.  The request's original expansion
        // cap and the shared absolute deadline remain authoritative.
        let BlockingPair = (0..CandidateGroups.len()).find_map(|FirstIndex| {
            (FirstIndex + 1..CandidateGroups.len()).find_map(|SecondIndex| {
                CandidateGroups[FirstIndex]
                    .iter()
                    .all(|First| {
                        CandidateGroups[SecondIndex].iter().all(|Second| {
                            ExactSelectedWorldClaimsConflict(&First.Claims, &Second.Claims)
                        })
                    })
                    .then_some((FirstIndex, SecondIndex))
            })
        });
        let mut AddedConflictAlternative = false;
        if let Some((FirstIndex, SecondIndex)) = BlockingPair {
            let mut AlternativeOptions = Vec::<(usize, usize, usize, Vec<Position>)>::new();
            let AggregateInitialConflictNodes = |GroupIndex: usize,
                                                 Candidate: &ExactSelectedWorldRouteCandidate|
             -> Vec<Position> {
                let Access = &PreparedAccessPayloads[Requests[Candidate.RequestIndex].0];
                let mut Values = HashSet::new();
                for (OtherGroupIndex, OtherGroup) in CandidateGroups.iter().enumerate() {
                    if OtherGroupIndex == GroupIndex {
                        continue;
                    }
                    let Some(Other) = OtherGroup.first() else {
                        continue;
                    };
                    Values.extend(FindExactSelectedWorldMovableConflictNodes(
                        Context,
                        Candidate,
                        Access,
                        &Other.Claims,
                    ));
                }
                let mut Values = Values.into_iter().collect::<Vec<_>>();
                Values.sort_unstable();
                Values
            };
            for (CandidateIndex, Candidate) in CandidateGroups[FirstIndex].iter().enumerate() {
                let Nodes = AggregateInitialConflictNodes(FirstIndex, Candidate);
                if !Nodes.is_empty() {
                    AlternativeOptions.push((Nodes.len(), FirstIndex, CandidateIndex, Nodes));
                }
            }
            for (CandidateIndex, Candidate) in CandidateGroups[SecondIndex].iter().enumerate() {
                let Nodes = AggregateInitialConflictNodes(SecondIndex, Candidate);
                if !Nodes.is_empty() {
                    AlternativeOptions.push((Nodes.len(), SecondIndex, CandidateIndex, Nodes));
                }
            }
            AlternativeOptions.sort_by_key(|(NodeCount, GroupIndex, CandidateIndex, Nodes)| {
                (*NodeCount, *GroupIndex, *CandidateIndex, Nodes[0])
            });
            for (_NodeCount, GroupIndex, CandidateIndex, Nodes) in AlternativeOptions {
                if Deadline.Check() {
                    break;
                }
                let Candidate = &CandidateGroups[GroupIndex][CandidateIndex];
                let RequestIndex = Candidate.RequestIndex;
                let (
                    AccessIndex,
                    GuideIndex,
                    PreferredRoutingY,
                    GuidePenalty,
                    BendPenalty,
                    ViaPenalty,
                    MaximumExpansionCount,
                ) = Requests[RequestIndex];
                let RemainingExpansionCount = MaximumExpansionCount
                    .saturating_sub(RouteExpansionCountByRequest[RequestIndex]);
                if RemainingExpansionCount == 0 {
                    SawIncompleteRequest = true;
                    continue;
                }
                let NewNoGoodNodes = Nodes
                    .into_iter()
                    .filter(|Value| !AlternativeBlockedNodesByRequest[RequestIndex].contains(Value))
                    .collect::<Vec<_>>();
                if NewNoGoodNodes.is_empty() {
                    continue;
                }
                // Retain the original exact candidate, but make this
                // alternative leave the complete movable footprint of its
                // current cross-signal conflict.  Excluding one lexicographic
                // cell at a time merely translated the same path around the
                // next adjacent claim and consumed the bounded request share
                // without producing a physically distinct witness.
                AlternativeBlockedNodesByRequest[RequestIndex].extend(NewNoGoodNodes);
                let Access = &PreparedAccessPayloads[AccessIndex];
                let mut AlternativeBlockedNodes = Access.BlockedNodes.clone();
                AlternativeBlockedNodes.extend(
                    AlternativeBlockedNodesByRequest[RequestIndex]
                        .iter()
                        .copied(),
                );
                let Some(PreparedGuideIndex) = PreparedGuideIndexByKey
                    .get(&(GuideIndex, PreferredRoutingY, GuidePenalty))
                    .copied()
                else {
                    SawIncompleteRequest = true;
                    continue;
                };
                let SearchResult = Context.GenerateRouteTreeClaimAwarePreparedDetailedNative(
                    &Access.Starts,
                    &Access.TargetBranches,
                    &Access.FrozenTargetBranches,
                    &PreparedGuides[PreparedGuideIndex],
                    &Access.RequiredNodes,
                    &AlternativeBlockedNodes,
                    PreferredRoutingY,
                    BendPenalty,
                    ViaPenalty,
                    true,
                    Some(&Access.SourceBranch),
                    RemainingExpansionCount,
                    &Deadline,
                    &Access.MandatoryWire,
                    &Access.MandatorySupport,
                    &Access.MandatoryAir,
                    &SignalRequestIndices[GroupIndex].0,
                );
                GeneratedRequestCount += 1;
                RouteExpansionCountByRequest[RequestIndex] = RouteExpansionCountByRequest
                    [RequestIndex]
                    .saturating_add(SearchResult.ExpansionCount);
                if SearchResult.IsBudgetExpired {
                    SawIncompleteRequest = true;
                    break;
                }
                if SearchResult.IsRouted {
                    let Claims =
                        BuildExactSelectedWorldRouteClaims(Context, &SearchResult.Nodes, Access);
                    let NewCandidate = ExactSelectedWorldRouteCandidate {
                        RequestIndex,
                        Nodes: SearchResult.Nodes,
                        RepeaterReservations: SearchResult.RepeaterReservations,
                        Claims,
                    };
                    if !CandidateGroups[GroupIndex].iter().any(|Existing| {
                        Existing.Nodes == NewCandidate.Nodes
                            && Existing.RepeaterReservations == NewCandidate.RepeaterReservations
                    }) {
                        CandidateGroups[GroupIndex].push(NewCandidate);
                        AddedConflictAlternative = true;
                        break;
                    }
                }
            }
        }
        if AddedConflictAlternative {
            ReevaluateCandidateAssignment = true;
            continue;
        }
        if AssignmentExpansionCount >= EffectiveMaximumAssignmentExpansionCount {
            let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
            return Ok(FactorizedRouteTreeSelectionResult {
                RouteTrees: vec![None; TotalWork],
                RepeaterReservations: vec![Vec::new(); TotalWork],
                CompletionMask,
                SelectedRequestIndices: Vec::new(),
                Success: false,
                Complete: false,
                DeadlineExceeded: false,
                WorkCapExceeded: true,
                AssignmentExpansionCount,
                GeneratedRequestCount,
                GeneratedRequestCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), NextRequestOffsetByGroup[GroupIndex])
                    })
                    .collect(),
                CandidateCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), CandidateGroups[GroupIndex].len())
                    })
                    .collect(),
                CompletedWork,
                TotalWork,
            });
        }
    }
}

#[allow(clippy::type_complexity)]
pub(crate) fn GenerateRouteTreesNative(
    Context: &RoutingContext,
    Requests: Vec<(
        Vec<Position>,
        Vec<Vec<Position>>,
        Vec<(i32, i32)>,
        Vec<Position>,
        Vec<Position>,
        Vec<(i32, i32)>,
        i32,
        i32,
        i32,
        i32,
        usize,
    )>,
    MaximumRuntimeMilliseconds: Option<u64>,
) -> PyResult<RouteTreeBatchResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(MaximumRuntimeMilliseconds)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let TotalWork = Requests.len();
    Deadline.Check();
    let WorkResults: Vec<(Option<Vec<Position>>, bool)> = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .map(
                |(
                    Starts,
                    TargetBranches,
                    AllowedColumns,
                    RequiredNodes,
                    BlockedNodeValues,
                    PreferredColumns,
                    PreferredRoutingY,
                    GuidePenalty,
                    BendPenalty,
                    ViaPenalty,
                    MaximumExpansionCount,
                )| {
                    if Deadline.Check() {
                        return (None, false);
                    }
                    let mut AllowedNodes = HashSet::new();
                    for (Index, Column) in AllowedColumns.into_iter().enumerate() {
                        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                            return (None, false);
                        }
                        if let Some(Values) = Context.NodesByColumn.get(&Column) {
                            AllowedNodes.extend(Values.iter().copied());
                        }
                    }
                    AllowedNodes.extend(RequiredNodes);
                    let RouteTree = Context.GenerateRouteTreeNative(
                        Starts,
                        TargetBranches,
                        AllowedNodes.into_iter().collect(),
                        BlockedNodeValues,
                        PreferredColumns,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        MaximumExpansionCount,
                        Deadline
                            .RemainingMilliseconds()
                            .map(|Value| Value as f64 / 1_000.0),
                    );
                    let Completed = !Deadline.Check();
                    (RouteTree, Completed)
                },
            )
            .collect()
    });
    let CompletedWork = WorkResults
        .iter()
        .filter(|(_RouteTree, Completed)| *Completed)
        .count();
    let CompletionMask = WorkResults
        .iter()
        .map(|(_RouteTree, Completed)| *Completed)
        .collect();
    let RouteTrees = WorkResults
        .into_iter()
        .map(|(Value, _Completed)| Value)
        .collect();
    Ok(RouteTreeBatchResult {
        RouteTrees,
        RepeaterReservations: vec![Vec::new(); TotalWork],
        CompletionMask,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    })
}

#[cfg(test)]
mod Tests {
    use super::*;

    #[test]
    fn ForeignClaimKeepoutCoversExactBlockAndVerticalHeadroomConflicts() {
        let VerticalLower = (0, 0, 0);
        let VerticalUpper = (1, 1, 0);
        let Context = RoutingContext {
            Adjacency: HashMap::from([
                (VerticalLower, vec![VerticalUpper]),
                (VerticalUpper, vec![VerticalLower]),
            ]),
            NodesByColumn: HashMap::new(),
        };
        let Claims = ExactSelectedWorldRouteClaims {
            Wire: HashSet::from([(4, 0, 0), VerticalUpper]),
            Support: HashSet::from([(5, 0, 0), VerticalUpper]),
            Air: HashSet::from([(6, 0, 0)]),
            Electrical: HashSet::from([(7, 0, 0)]),
        };

        let Blocked = BuildExactSelectedWorldForeignBlockedNodes(&Context, [Claims]);

        assert!(Blocked.contains(&(5, 0, 0)));
        assert!(Blocked.contains(&(6, 0, 0)));
        assert!(Blocked.contains(&(7, 0, 0)));
        assert!(Blocked.contains(&(4, 1, 0)));
        assert!(Blocked.contains(&(6, 1, 0)));
        assert!(Blocked.contains(&VerticalUpper));
    }

    fn LinearContext() -> RoutingContext {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        RoutingContext {
            Adjacency: HashMap::from([(A, vec![B]), (B, vec![A])]),
            NodesByColumn: HashMap::from([((0, 0), vec![A]), ((1, 0), vec![B])]),
        }
    }

    #[test]
    fn PortalBatchReportsImmediateDeadlineAndNoCompletedWork() {
        let Result = GeneratePortalCandidateBatchesNative(
            &LinearContext(),
            vec![(
                vec![(0, 0, 0)],
                vec![(1, 0, 0)],
                vec![(0, 0, 0), (1, 0, 0)],
                0,
                4,
                128,
            )],
            Some(0),
        )
        .unwrap();
        assert!(Result.DeadlineExceeded);
        assert_eq!(Result.CompletedWork, 0);
        assert_eq!(Result.TotalWork, 1);
        assert_eq!(Result.Candidates.len(), 1);
        assert_eq!(Result.CompletionMask, vec![false]);
        assert_eq!(
            Result.CompletionMask.iter().filter(|Value| **Value).count(),
            Result.CompletedWork,
        );
    }

    #[test]
    fn CompactConnectivityBatchReportsReachableAndBlockedProofs() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let Results = LinearContext().CertifyRouteFactorConnectivityBatchNative(
            vec![
                (
                    vec![(0, 0), (1, 0)],
                    0,
                    Vec::new(),
                    Vec::new(),
                    vec![B],
                    A,
                    2,
                ),
                (vec![(0, 0), (1, 0)], 0, Vec::new(), vec![B], vec![B], A, 2),
            ],
            1_000,
        );
        assert_eq!(Results, vec![(true, true, 1), (true, false, 0)]);
    }

    #[test]
    fn CompactConnectivityBatchKeepsWorkAndDeadlineExhaustionIncomplete() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (2, 0, 0);
        let Context = RoutingContext {
            Adjacency: HashMap::from([(A, vec![B]), (B, vec![A, C]), (C, vec![B])]),
            NodesByColumn: HashMap::from([((0, 0), vec![A]), ((1, 0), vec![B]), ((2, 0), vec![C])]),
        };
        let Request = (
            vec![(0, 0), (1, 0), (2, 0)],
            0,
            Vec::new(),
            Vec::new(),
            vec![C],
            A,
            1,
        );
        assert_eq!(
            Context.CertifyRouteFactorConnectivityBatchNative(vec![Request.clone()], 1_000,),
            vec![(false, false, 1)],
        );
        assert_eq!(
            Context.CertifyRouteFactorConnectivityBatchNative(vec![Request], 0,),
            vec![(false, false, 0)],
        );
    }

    #[test]
    fn RouteTreeBatchReportsImmediateDeadlineAndNoCompletedWork() {
        let Result = GenerateRouteTreesNative(
            &LinearContext(),
            vec![(
                vec![(0, 0, 0)],
                vec![vec![(1, 0, 0)]],
                vec![(0, 0), (1, 0)],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                0,
                1,
                1,
                1,
                128,
            )],
            Some(0),
        )
        .unwrap();
        assert!(Result.DeadlineExceeded);
        assert_eq!(Result.CompletedWork, 0);
        assert_eq!(Result.TotalWork, 1);
        assert_eq!(Result.RouteTrees.len(), 1);
        assert_eq!(Result.CompletionMask, vec![false]);
    }

    #[test]
    fn DetailedRouteTreeBatchPreservesRequestOrderAndTypedResults() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let Result = GenerateRouteTreeDetailedBatchNative(
            &LinearContext(),
            vec![
                (
                    vec![A],
                    vec![vec![B]],
                    vec![A, B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    false,
                    128,
                ),
                (
                    vec![(99, 0, 0)],
                    vec![vec![B]],
                    vec![A, B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    false,
                    128,
                ),
            ],
            1_000,
        );

        assert_eq!(Result.TotalWork, 2);
        assert_eq!(Result.CompletedWork, 2);
        assert!(!Result.DeadlineExceeded);
        assert!(Result.SearchResults[0].IsRouted);
        assert_eq!(Result.SearchResults[1].Status, "NoPath");
        assert_eq!(Result.SearchResults[1].NoPathReason, "NoPathGeometry");
    }

    #[test]
    fn DetailedRouteTreeBatchReportsImmediateDeadlineForEveryRequest() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let Result = GenerateRouteTreeDetailedBatchNative(
            &LinearContext(),
            vec![
                (
                    vec![A],
                    vec![vec![B]],
                    vec![A, B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    false,
                    128,
                ),
                (
                    vec![A],
                    vec![vec![B]],
                    vec![A, B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    false,
                    128,
                ),
            ],
            0,
        );

        assert!(Result.DeadlineExceeded);
        assert_eq!(Result.CompletedWork, 0);
        assert_eq!(Result.TotalWork, 2);
        assert!(Result
            .SearchResults
            .iter()
            .all(|SearchResult| SearchResult.IsBudgetExpired));
    }

    #[test]
    fn RouteTreeIgnoresAllowedStartsOutsideResourceGraph() {
        let Context = LinearContext();
        let OffGraph = (99, 0, 0);
        let Result = Context.GenerateRouteTreeNative(
            vec![OffGraph, (0, 0, 0)],
            vec![vec![(1, 0, 0)]],
            vec![OffGraph, (0, 0, 0), (1, 0, 0)],
            Vec::new(),
            vec![(0, 0), (1, 0)],
            0,
            0,
            0,
            0,
            128,
            None,
        );

        assert_eq!(
            Result.map(|Values| Values.into_iter().collect::<HashSet<_>>()),
            Some(HashSet::from([(0, 0, 0), (1, 0, 0)])),
        );
    }

    #[test]
    fn RouteTreeBatchHonorsExplicitBlockedNodes() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (0, 0, 1);
        let D = (1, 0, 1);
        let Context = RoutingContext {
            Adjacency: HashMap::from([
                (A, vec![B, C]),
                (B, vec![A, D]),
                (C, vec![A, D]),
                (D, vec![B, C]),
            ]),
            NodesByColumn: HashMap::from([
                ((0, 0), vec![A]),
                ((1, 0), vec![B]),
                ((0, 1), vec![C]),
                ((1, 1), vec![D]),
            ]),
        };
        let AllowedColumns = vec![(0, 0), (1, 0), (0, 1), (1, 1)];
        let Result = GenerateRouteTreesNative(
            &Context,
            vec![
                (
                    vec![B, A],
                    vec![vec![D]],
                    AllowedColumns.clone(),
                    Vec::new(),
                    vec![B],
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    128,
                ),
                (
                    vec![A],
                    vec![vec![D]],
                    AllowedColumns,
                    Vec::new(),
                    vec![D],
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    128,
                ),
            ],
            None,
        )
        .unwrap();

        assert_eq!(Result.RouteTrees[0], Some(vec![A, C, D]));
        assert_eq!(Result.RouteTrees[1], None);
        assert_eq!(Result.CompletionMask, vec![true, true]);
        assert_eq!(Result.CompletedWork, 2);
        assert!(!Result.DeadlineExceeded);
    }

    #[test]
    fn PortalIncludesGraphAccessAnchorAndAdjacentPath() {
        let Context = LinearContext();
        let Start = (0, 0, 0);
        let Target = (1, 0, 0);
        let Values = Context.GeneratePortalCandidatesNative(
            vec![(99, 0, 0), Start],
            vec![Target],
            vec![(99, 0, 0), Start, Target],
            0,
            4,
            128,
            None,
        );

        assert_eq!(Values.len(), 1);
        assert_eq!(Values[0].Path, vec![Start, Target]);
        assert!(Values[0].WireClaims.contains(&Start));
        assert!(Values[0].Path.windows(2).all(|Values| Context
            .Adjacency
            .get(&Values[0])
            .map(|Neighbors| Neighbors.contains(&Values[1]))
            .unwrap_or(false)));
    }

    #[test]
    fn ClaimAwareTreeRejectsCheapestSelfConflictAndKeepsLaterPath() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (0, 0, 1);
        let D = (1, 0, 1);
        let Context = RoutingContext {
            Adjacency: HashMap::from([
                (A, vec![B, C]),
                (B, vec![A, D]),
                (C, vec![A, D]),
                (D, vec![B, C]),
            ]),
            NodesByColumn: HashMap::new(),
        };
        let Result = Context.GenerateRouteTreeClaimAwareDetailedNative(
            vec![A],
            vec![vec![D]],
            vec![A, B, C, D],
            Vec::new(),
            Vec::new(),
            Vec::new(),
            0,
            0,
            0,
            0,
            false,
            128,
            1_000,
            Vec::new(),
            vec![B],
            Vec::new(),
            Vec::new(),
        );

        assert!(Result.IsRouted);
        assert_eq!(Result.Nodes, vec![A, C, D]);
        assert_eq!(Result.RejectedPathCount, 1);
        assert_eq!(Result.NoGoodCount, 1);
        assert!(Result.ConflictResources.is_empty());
    }

    #[test]
    fn ClaimAwareTreeReportsCompleteStaticSelfConflict() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let Result = LinearContext().GenerateRouteTreeClaimAwareDetailedNative(
            vec![A],
            vec![vec![B]],
            vec![A, B],
            Vec::new(),
            Vec::new(),
            Vec::new(),
            0,
            0,
            0,
            0,
            false,
            128,
            1_000,
            Vec::new(),
            vec![B],
            Vec::new(),
            Vec::new(),
        );

        assert_eq!(Result.Status, "NoPath");
        assert_eq!(Result.NoPathReason, "SelfClaimConflict");
        assert_eq!(Result.RejectedPathCount, 1);
        assert!(!Result.ConflictResources.is_empty());
    }
}

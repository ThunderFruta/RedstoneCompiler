use super::*;

pub(in crate::Generation) fn IsPreparedRouteNodeAllowed(
    Guide: &PreparedDetailedRouteGuide,
    AdditionalAllowedNodes: &HashSet<Position>,
    Value: &Position,
) -> bool {
    Guide.AllowedNodes.contains(Value)
        || (Guide.UseColumnMembership && Guide.AllowedColumns.contains(&(Value.0, Value.2)))
        || AdditionalAllowedNodes.contains(Value)
}

pub(in crate::Generation) fn DetailedRouteTreeBudgetExpiredResult() -> RouteTreeSearchResult {
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

pub(in crate::Generation) fn BuildRootedTreeBlockages(
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

pub(in crate::Generation) fn RepeaterFacing(Current: Position, Next: Position) -> Option<String> {
    match (Next.0 - Current.0, Next.2 - Current.2) {
        (1, 0) => Some("west".to_string()),
        (-1, 0) => Some("east".to_string()),
        (0, 1) => Some("north".to_string()),
        (0, -1) => Some("south".to_string()),
        _ => None,
    }
}

pub(in crate::Generation) fn EraseCanonicalRoutePathLoops(
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

pub(in crate::Generation) fn PropagateCanonicalRoutePowerWithParents(
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

pub(in crate::Generation) fn PropagateCanonicalRoutePower(
    Root: Position,
    Nodes: &HashSet<Position>,
    Repeaters: &HashMap<Position, String>,
    Adjacency: &HashMap<Position, Vec<Position>>,
) -> HashMap<Position, u8> {
    PropagateCanonicalRoutePowerWithParents(Root, Nodes, Repeaters, Adjacency).0
}

pub(in crate::Generation) fn FindSelfExcitingRepeaterCycles(
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

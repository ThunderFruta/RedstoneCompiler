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

pub(in crate::Generation) fn RepeaterInputFacing(
    Current: Position,
    Next: Position,
) -> Option<String> {
    match (Next.0 - Current.0, Next.2 - Current.2) {
        (1, 0) => Some("west".to_string()),
        (-1, 0) => Some("east".to_string()),
        (0, 1) => Some("north".to_string()),
        (0, -1) => Some("south".to_string()),
        _ => None,
    }
}

#[cfg(test)]
mod RepeaterInputFacingTests {
    use super::RepeaterInputFacing;

    #[test]
    fn CardinalStepsReturnJavaInputSide() {
        let Origin = (0, 0, 0);
        assert_eq!(
            RepeaterInputFacing(Origin, (1, 0, 0)).as_deref(),
            Some("west")
        );
        assert_eq!(
            RepeaterInputFacing(Origin, (-1, 0, 0)).as_deref(),
            Some("east")
        );
        assert_eq!(
            RepeaterInputFacing(Origin, (0, 0, 1)).as_deref(),
            Some("north")
        );
        assert_eq!(
            RepeaterInputFacing(Origin, (0, 0, -1)).as_deref(),
            Some("south")
        );
    }

    #[test]
    fn VerticalAndNonAdjacentStepsHaveNoFacing() {
        let Origin = (0, 0, 0);
        assert_eq!(RepeaterInputFacing(Origin, (0, 1, 0)), None);
        assert_eq!(RepeaterInputFacing(Origin, (2, 0, 0)), None);
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

pub(in crate::Generation) enum DeadlineAwareElectricalResult<T> {
    Complete(T),
    DeadlineExhausted,
}

pub(in crate::Generation) fn PropagateCanonicalRoutePowerWithParents(
    Root: Position,
    Nodes: &HashSet<Position>,
    Repeaters: &HashMap<Position, String>,
    Adjacency: &HashMap<Position, Vec<Position>>,
) -> (HashMap<Position, u8>, HashMap<Position, Position>) {
    match PropagateCanonicalRoutePowerWithParentsWithDeadline(
        Root,
        Nodes,
        Repeaters,
        Adjacency,
        &RuntimeDeadline::Unlimited(),
    ) {
        DeadlineAwareElectricalResult::Complete(Result) => Result,
        DeadlineAwareElectricalResult::DeadlineExhausted => {
            unreachable!("an unlimited electrical validation deadline cannot expire")
        }
    }
}

fn PropagateCanonicalRoutePowerWithParentsWithDeadline(
    Root: Position,
    Nodes: &HashSet<Position>,
    Repeaters: &HashMap<Position, String>,
    Adjacency: &HashMap<Position, Vec<Position>>,
    Deadline: &RuntimeDeadline,
) -> DeadlineAwareElectricalResult<(HashMap<Position, u8>, HashMap<Position, Position>)> {
    if Deadline.Check() {
        return DeadlineAwareElectricalResult::DeadlineExhausted;
    }
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
    let mut Steps = 0usize;
    macro_rules! ElectricalStep {
        () => {
            Steps = match Steps.checked_add(1) {
                Some(Value) => Value,
                None => return DeadlineAwareElectricalResult::DeadlineExhausted,
            };
            if Steps % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return DeadlineAwareElectricalResult::DeadlineExhausted;
            }
        };
    }
    while let Some((Power, Current)) = Pending.pop() {
        ElectricalStep!();
        if Powers.get(&Current).copied() != Some(Power) {
            continue;
        }
        let mut Candidates = Vec::new();
        if let Some(Facing) = Repeaters.get(&Current) {
            ElectricalStep!();
            if let Some(Value) = OutputDelta(Facing)
                .map(|Delta| {
                    (
                        Current.0 + Delta.0,
                        Current.1 + Delta.1,
                        Current.2 + Delta.2,
                    )
                })
                .filter(|Value| Nodes.contains(Value))
            {
                let mut Adjacent = false;
                for Neighbor in Adjacency.get(&Current).into_iter().flatten() {
                    ElectricalStep!();
                    if *Neighbor == Value {
                        Adjacent = true;
                        break;
                    }
                }
                if Adjacent {
                    Candidates.push((Value, MAXIMUM_UNREFRESHED_DUST_LENGTH));
                }
            }
        } else {
            for Neighbor in Adjacency.get(&Current).into_iter().flatten().copied() {
                ElectricalStep!();
                if !Nodes.contains(&Neighbor) {
                    continue;
                }
                let Candidate = if let Some(Facing) = Repeaters.get(&Neighbor) {
                    let Some(Delta) = OutputDelta(Facing) else {
                        continue;
                    };
                    let Input = (
                        Neighbor.0 - Delta.0,
                        Neighbor.1 - Delta.1,
                        Neighbor.2 - Delta.2,
                    );
                    (Current == Input && Power > 0)
                        .then_some((Neighbor, MAXIMUM_UNREFRESHED_DUST_LENGTH))
                } else {
                    (Power > 1).then_some((Neighbor, Power - 1))
                };
                if let Some(Candidate) = Candidate {
                    Candidates.push(Candidate);
                }
            }
        }
        for (Neighbor, CandidatePower) in Candidates {
            ElectricalStep!();
            if CandidatePower <= Powers.get(&Neighbor).copied().unwrap_or(0) {
                continue;
            }
            Powers.insert(Neighbor, CandidatePower);
            Parents.insert(Neighbor, Current);
            Pending.push((CandidatePower, Neighbor));
        }
    }
    if Deadline.Check() {
        DeadlineAwareElectricalResult::DeadlineExhausted
    } else {
        DeadlineAwareElectricalResult::Complete((Powers, Parents))
    }
}

pub(in crate::Generation) fn PropagateCanonicalRoutePower(
    Root: Position,
    Nodes: &HashSet<Position>,
    Repeaters: &HashMap<Position, String>,
    Adjacency: &HashMap<Position, Vec<Position>>,
) -> HashMap<Position, u8> {
    PropagateCanonicalRoutePowerWithParents(Root, Nodes, Repeaters, Adjacency).0
}

pub(in crate::Generation) fn PropagateCanonicalRoutePowerWithDeadline(
    Root: Position,
    Nodes: &HashSet<Position>,
    Repeaters: &HashMap<Position, String>,
    Adjacency: &HashMap<Position, Vec<Position>>,
    Deadline: &RuntimeDeadline,
) -> DeadlineAwareElectricalResult<HashMap<Position, u8>> {
    match PropagateCanonicalRoutePowerWithParentsWithDeadline(
        Root, Nodes, Repeaters, Adjacency, Deadline,
    ) {
        DeadlineAwareElectricalResult::Complete((Powers, _Parents)) => {
            DeadlineAwareElectricalResult::Complete(Powers)
        }
        DeadlineAwareElectricalResult::DeadlineExhausted => {
            DeadlineAwareElectricalResult::DeadlineExhausted
        }
    }
}

pub(in crate::Generation) fn FindSelfExcitingRepeaterCycles(
    Nodes: &HashSet<Position>,
    RepeaterValues: &[(Position, String)],
) -> Vec<(Position, Vec<Position>)> {
    match FindSelfExcitingRepeaterCyclesWithDeadline(
        Nodes,
        RepeaterValues,
        &RuntimeDeadline::Unlimited(),
    ) {
        DeadlineAwareElectricalResult::Complete(Result) => Result,
        DeadlineAwareElectricalResult::DeadlineExhausted => {
            unreachable!("an unlimited electrical validation deadline cannot expire")
        }
    }
}

pub(in crate::Generation) fn FindSelfExcitingRepeaterCyclesWithDeadline(
    Nodes: &HashSet<Position>,
    RepeaterValues: &[(Position, String)],
    Deadline: &RuntimeDeadline,
) -> DeadlineAwareElectricalResult<Vec<(Position, Vec<Position>)>> {
    if Deadline.Check() {
        return DeadlineAwareElectricalResult::DeadlineExhausted;
    }
    let mut Steps = 0usize;
    macro_rules! ElectricalStep {
        () => {
            Steps = match Steps.checked_add(1) {
                Some(Value) => Value,
                None => return DeadlineAwareElectricalResult::DeadlineExhausted,
            };
            if Steps % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return DeadlineAwareElectricalResult::DeadlineExhausted;
            }
        };
    }
    let mut Repeaters = HashMap::with_capacity(RepeaterValues.len());
    let mut OrderedRepeaters = std::collections::BTreeSet::new();
    for Value in RepeaterValues.iter().cloned() {
        ElectricalStep!();
        Repeaters.insert(Value.0, Value.1.clone());
        OrderedRepeaters.insert(Value);
    }
    let OutputDelta = |Facing: &str| match Facing {
        "west" => Some((1, 0, 0)),
        "east" => Some((-1, 0, 0)),
        "north" => Some((0, 0, 1)),
        "south" => Some((0, 0, -1)),
        _ => None,
    };
    let mut Result = Vec::new();
    for (Repeater, Facing) in OrderedRepeaters {
        ElectricalStep!();
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
            ElectricalStep!();
            if Current == Input {
                break;
            }
            let mut DirectedNeighbors = Vec::new();
            if let Some(Facing) = Repeaters.get(&Current) {
                ElectricalStep!();
                if let Some(Value) = OutputDelta(Facing)
                    .map(|NeighborDelta| {
                        (
                            Current.0 + NeighborDelta.0,
                            Current.1 + NeighborDelta.1,
                            Current.2 + NeighborDelta.2,
                        )
                    })
                    .filter(|Value| Nodes.contains(Value))
                {
                    DirectedNeighbors.push(Value);
                }
            } else {
                for Neighbor in RedstoneNeighborPositions(Current) {
                    ElectricalStep!();
                    if !Nodes.contains(&Neighbor) {
                        continue;
                    }
                    let Permitted = if let Some(Facing) = Repeaters.get(&Neighbor) {
                        let Some(NeighborDelta) = OutputDelta(Facing) else {
                            continue;
                        };
                        Current
                            == (
                                Neighbor.0 - NeighborDelta.0,
                                Neighbor.1 - NeighborDelta.1,
                                Neighbor.2 - NeighborDelta.2,
                            )
                    } else {
                        true
                    };
                    if Permitted {
                        DirectedNeighbors.push(Neighbor);
                    }
                }
                DirectedNeighbors.sort_unstable();
            }
            for Neighbor in DirectedNeighbors {
                ElectricalStep!();
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
        let mut Cycle = std::collections::BTreeSet::from([Repeater]);
        let mut Cursor = Some(Input);
        while let Some(Value) = Cursor {
            ElectricalStep!();
            Cycle.insert(Value);
            Cursor = Parent[&Value];
        }
        let mut OrderedCycle = Vec::with_capacity(Cycle.len());
        for Value in Cycle {
            ElectricalStep!();
            OrderedCycle.push(Value);
        }
        Result.push((Repeater, OrderedCycle));
    }
    if Deadline.Check() {
        DeadlineAwareElectricalResult::DeadlineExhausted
    } else {
        DeadlineAwareElectricalResult::Complete(Result)
    }
}

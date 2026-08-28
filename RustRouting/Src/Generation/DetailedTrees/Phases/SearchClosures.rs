macro_rules! BuildPreparedDetailedRouteClosures {
    (
        $SelfContext:expr,
        $Starts:ident,
        $Guide:ident,
        $AdditionalAllowedNodes:ident,
        $PreferredRoutingY:ident,
        $BendPenalty:ident,
        $ViaPenalty:ident,
        $EnforceSignalStrength:ident,
        $FrozenSourceBranch:ident,
        $ForbiddenRepeaterPositions:ident,
        $DebugLabel:ident,
        $MaximumExpansionCount:ident,
        $Deadline:ident,
        $BlockedNodes:ident,
        $AdditionalNodeCosts:ident,
        $ExpansionCount:ident,
        $FrozenReservedAccessNodes:ident,
        $FrozenSourceRepairAllowedNodes:ident,
        $RouteIntoTree:ident,
        $RouteFrozenSourceIntoTree:ident,
        $RouteFrozenTargetIntoTree:ident
    ) => {
        let $RouteIntoTree = |Target: Position,
                             TargetContinuation: &[Position],
                             ReservedNodes: &HashSet<Position>,
                             TreeValue: &HashSet<Position>,
                             States: &HashMap<Position, SearchState>,
                             ExistingRepeaters: &HashMap<Position, String>|
         -> Option<crate::Path::PathRouting::PathSearchResult> {
            let (mut DynamicBlocked, EdgeCosts) = BuildRootedTreeBlockages(
                $SelfContext,
                $Guide,
                $AdditionalAllowedNodes,
                &$BlockedNodes,
                TreeValue,
                ReservedNodes,
                &$Deadline,
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
            let mut StartStates: Vec<_> = if $EnforceSignalStrength {
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
            if $FrozenSourceBranch.is_some() {
                let mut GeometryStartStates = StartStates.clone();
                let mut RefreshLaunchByState =
                    HashMap::<SearchState, (SearchState, SearchState, String)>::new();
                for StartState in &StartStates {
                    if StartState.2 <= 1 {
                        continue;
                    }
                    for First in $SelfContext
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
                            || $ForbiddenRepeaterPositions.contains(&First)
                            || !IsPreparedRouteNodeAllowed($Guide, $AdditionalAllowedNodes, &First)
                            || !IsPreparedRouteNodeAllowed($Guide, $AdditionalAllowedNodes, &Second)
                            || !$SelfContext
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
                    &$SelfContext.Adjacency,
                    $Guide.UseColumnMembership.then_some(&$Guide.AllowedColumns),
                    Some($AdditionalAllowedNodes),
                    &GeometryStartStates,
                    Target,
                    $PreferredRoutingY,
                    &DynamicBlocked,
                    &$Guide.NodeCosts,
                    &$AdditionalNodeCosts,
                    &$Guide.ColumnCosts,
                    &EdgeCosts,
                    $BendPenalty,
                    $ViaPenalty,
                    0,
                    $MaximumExpansionCount.div_ceil(8).max(1),
                    false,
                    $ForbiddenRepeaterPositions,
                    TargetContinuation,
                    0,
                    &$Deadline,
                ) {
                    ConsumedExpansionCount = GeometryResult.$ExpansionCount;
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
                                && CurrentState.2
                                    <= crate::Path::PathRouting::REPEATER_TURN_HEADROOM
                                && !$ForbiddenRepeaterPositions.contains(&CurrentState.0);
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
                                        && !$ForbiddenRepeaterPositions.contains(&Next)
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
                                $DebugLabel,
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
                &$SelfContext.Adjacency,
                $Guide.UseColumnMembership.then_some(&$Guide.AllowedColumns),
                Some($AdditionalAllowedNodes),
                &StartStates,
                Target,
                $PreferredRoutingY,
                &DynamicBlocked,
                &$Guide.NodeCosts,
                &$AdditionalNodeCosts,
                &$Guide.ColumnCosts,
                &EdgeCosts,
                $BendPenalty,
                $ViaPenalty,
                0,
                $MaximumExpansionCount.saturating_sub(ConsumedExpansionCount),
                $EnforceSignalStrength,
                $ForbiddenRepeaterPositions,
                TargetContinuation,
                0,
                &$Deadline,
            );
            if let Some(Value) = Result.as_mut() {
                Value.$ExpansionCount = Value.$ExpansionCount.saturating_add(ConsumedExpansionCount);
            }
            Result
        };

        let $FrozenSourceRepairAllowedNodes = $FrozenSourceBranch.map(|_| {
            $AdditionalAllowedNodes
                .iter()
                .copied()
                .filter(|Candidate| {
                    $Starts.iter().any(|SourceNode| {
                        (Candidate.0 - SourceNode.0).abs() + (Candidate.2 - SourceNode.2).abs()
                            <= i32::from(crate::Path::PathRouting::REPEATER_TURN_HEADROOM)
                    })
                })
                .collect::<HashSet<_>>()
        });
        let $RouteFrozenSourceIntoTree =
            |Target: Position,
             TreeValue: &HashSet<Position>,
             States: &HashMap<Position, SearchState>,
             ExistingRepeaters: &HashMap<Position, String>|
             -> Option<crate::Path::PathRouting::PathSearchResult> {
                let SourceAllowed = $FrozenSourceRepairAllowedNodes.as_ref()?;
                let (mut DynamicBlocked, EdgeCosts) = BuildRootedTreeBlockages(
                    $SelfContext,
                    $Guide,
                    $AdditionalAllowedNodes,
                    &$BlockedNodes,
                    TreeValue,
                    &$FrozenReservedAccessNodes,
                    &$Deadline,
                )?;
                DynamicBlocked.remove(&Target);
                for (Index, Value) in SourceAllowed.iter().enumerate() {
                    if Index % DEADLINE_CHECK_INTERVAL == 0 && $Deadline.Check() {
                        return None;
                    }
                    for Neighbor in $SelfContext.Adjacency.get(Value).into_iter().flatten() {
                        if !SourceAllowed.contains(Neighbor) {
                            DynamicBlocked.insert(*Neighbor);
                        }
                    }
                }
                let SourceNodeCosts = SourceAllowed
                    .iter()
                    .map(|Candidate| {
                        let Distance = $Starts
                            .iter()
                            .map(|SourceNode| {
                                (Candidate.0 - SourceNode.0).abs()
                                    + (Candidate.2 - SourceNode.2).abs()
                            })
                            .min()
                            .unwrap_or(0);
                        (*Candidate, Distance * $BendPenalty.max(1))
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
                DynamicBlocked.extend($FrozenReservedAccessNodes.iter().copied());
                if let Some(SourceBranch) = $FrozenSourceBranch {
                    for PositionValue in SourceBranch {
                        DynamicBlocked.remove(PositionValue);
                    }
                }
                for StartState in &StartStates {
                    DynamicBlocked.remove(&StartState.0);
                }
                DynamicBlocked.remove(&Target);
                FindPathFromStatesDetailedWithDeadline(
                    &$SelfContext.Adjacency,
                    None,
                    Some(SourceAllowed),
                    &StartStates,
                    Target,
                    $PreferredRoutingY,
                    &DynamicBlocked,
                    &HashMap::new(),
                    &SourceNodeCosts,
                    &HashMap::new(),
                    &EdgeCosts,
                    $BendPenalty,
                    $ViaPenalty,
                    0,
                    $MaximumExpansionCount,
                    $EnforceSignalStrength,
                    $ForbiddenRepeaterPositions,
                    &[],
                    0,
                    &$Deadline,
                )
            };

        let $RouteFrozenTargetIntoTree =
            |Branch: &[Position],
             TreeValue: &HashSet<Position>,
             States: &HashMap<Position, SearchState>,
             ExistingRepeaters: &HashMap<Position, String>,
             RepairRadius: i32,
             PoweredStartValues: Option<&HashMap<Position, u8>>,
             LocalMaximumExpansionCount: usize|
             -> Option<crate::Path::PathRouting::PathSearchResult> {
                let Target = Branch.last().copied()?;
                let TargetAllowed = $AdditionalAllowedNodes
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
                    $SelfContext,
                    $Guide,
                    $AdditionalAllowedNodes,
                    &$BlockedNodes,
                    TreeValue,
                    &HashSet::new(),
                    &$Deadline,
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
                    if Index % DEADLINE_CHECK_INTERVAL == 0 && $Deadline.Check() {
                        return None;
                    }
                    for Neighbor in $SelfContext.Adjacency.get(Value).into_iter().flatten() {
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
                            || $SelfContext.Adjacency.get(&State.0).is_some_and(|Neighbors| {
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
                        (*Candidate, Distance * $BendPenalty.max(1))
                    })
                    .collect::<HashMap<_, _>>();
                let Result = FindPathFromStatesDetailedWithDeadline(
                    &$SelfContext.Adjacency,
                    None,
                    Some(&TargetAllowed),
                    &StartStates,
                    Target,
                    $PreferredRoutingY,
                    &DynamicBlocked,
                    &$Guide.NodeCosts,
                    &TargetNodeCosts,
                    &$Guide.ColumnCosts,
                    &EdgeCosts,
                    $BendPenalty,
                    $ViaPenalty,
                    $BendPenalty.max(1),
                    LocalMaximumExpansionCount,
                    $EnforceSignalStrength,
                    $ForbiddenRepeaterPositions,
                    &[],
                    0,
                    &$Deadline,
                );
                Result
            };
    };
}

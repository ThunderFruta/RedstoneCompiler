macro_rules! FinalizePreparedDetailedRoute {
    (
        $SelfContext:expr,
        $TargetBranches:ident,
        $FrozenTargetBranches:ident,
        $Guide:ident,
        $AdditionalAllowedNodes:ident,
        $UnblockedAdditionalNodes:ident,
        $ForbiddenRepeaterPositions:ident,
        $DebugLabel:ident,
        $MaximumExpansionCount:ident,
        $Deadline:ident,
        $Failure:ident,
        $Root:ident,
        $Tree:ident,
        $StateByNode:ident,
        $ParentByNode:ident,
        $Repeaters:ident,
        $ExpansionCount:ident,
        $FrozenReservedAccessNodes:ident,
        $RetainedMandatorySourceNodes:ident,
        $TargetPaths:ident,
        $RetainedMandatoryTargetNodes:ident
    ) => {{
        // A repeater embedded in an induced same-signal cycle can preserve a
        // transient pulse after the real source goes low.  This is not a
        // signal-strength issue: the routed node set itself supplies an
        // alternate directed output-to-input path around the repeater.  A
        // cycle repeater that is unnecessary for source-to-target delivery is
        // therefore a dust cell, not a legal refresh element.  Demote only
        // after exact canonical propagation proves every required target
        // remains powered; an essential cycle is rejected instead of emitted
        // as a stateful combinational route.
        $Tree.extend($RetainedMandatorySourceNodes);
        $Tree.extend($RetainedMandatoryTargetNodes);
        let mut PhysicalNodes = $Tree
            .union(&$FrozenReservedAccessNodes)
            .copied()
            .collect::<HashSet<_>>();
        let RequiredTargets = $TargetBranches
            .iter()
            .chain($FrozenTargetBranches.iter())
            .filter_map(|Branch| Branch.last().copied())
            .collect::<HashSet<_>>();
        loop {
            let mut RepeaterValues = $Repeaters
                .iter()
                .map(|(PositionValue, Facing)| (*PositionValue, Facing.clone()))
                .collect::<Vec<_>>();
            RepeaterValues.sort_unstable();
            let Cycles = FindSelfExcitingRepeaterCycles(&PhysicalNodes, &RepeaterValues);
            if Cycles.is_empty() {
                break;
            }
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                let CurrentPowers =
                    PropagateCanonicalRoutePower($Root, &PhysicalNodes, &$Repeaters, &$SelfContext.Adjacency);
                eprintln!(
                    "selected detailed cycle target powers signal={} values={:?}",
                    $DebugLabel,
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
                let mut CandidateRepeaters = $Repeaters.clone();
                CandidateRepeaters.remove(Repeater);
                let CandidatePowers = PropagateCanonicalRoutePower(
                    $Root,
                    &PhysicalNodes,
                    &CandidateRepeaters,
                    &$SelfContext.Adjacency,
                );
                if RequiredTargets
                    .iter()
                    .all(|Target| CandidatePowers.contains_key(Target))
                {
                    $Repeaters = CandidateRepeaters;
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
                        if CutNode == $Root
                            || RequiredTargets.contains(&CutNode)
                            || $UnblockedAdditionalNodes.contains(&CutNode)
                        {
                            continue;
                        }
                        let mut CandidatePhysicalNodes = PhysicalNodes.clone();
                        if !CandidatePhysicalNodes.remove(&CutNode) {
                            continue;
                        }
                        let mut CandidateRepeaters = $Repeaters.clone();
                        CandidateRepeaters.remove(&CutNode);
                        let CandidatePowers = PropagateCanonicalRoutePower(
                            $Root,
                            &CandidatePhysicalNodes,
                            &CandidateRepeaters,
                            &$SelfContext.Adjacency,
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
                        $Tree.remove(&CutNode);
                        $ParentByNode.remove(&CutNode);
                        $StateByNode.remove(&CutNode);
                        PhysicalNodes = CandidatePhysicalNodes;
                        $Repeaters = CandidateRepeaters;
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
                        if CandidatePosition == $Root
                            || RequiredTargets.contains(&CandidatePosition)
                            || $Repeaters.contains_key(&CandidatePosition)
                            || $ForbiddenRepeaterPositions.contains(&CandidatePosition)
                        {
                            continue;
                        }
                        for (Facing, OutputDelta) in FacingValues {
                            if $Deadline.Check() || $ExpansionCount >= $MaximumExpansionCount {
                                return $Failure(
                                    "NoPath",
                                    "SearchLimitReached",
                                    1,
                                    1,
                                    $ExpansionCount,
                                );
                            }
                            $ExpansionCount += 1;
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
                                || !$SelfContext.Adjacency.get(&CandidatePosition).is_some_and(
                                    |Neighbors| {
                                        Neighbors.contains(&Input) && Neighbors.contains(&Output)
                                    },
                                )
                            {
                                continue;
                            }
                            let mut CandidateRepeaters = $Repeaters.clone();
                            CandidateRepeaters.insert(CandidatePosition, Facing.to_string());
                            let CandidatePowers = PropagateCanonicalRoutePower(
                                $Root,
                                &PhysicalNodes,
                                &CandidateRepeaters,
                                &$SelfContext.Adjacency,
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
                                        $DebugLabel, CandidatePosition, Facing, MissingTargets,
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
                                    $DebugLabel,
                                    CandidatePosition,
                                    Facing,
                                    CycleCount,
                                    CandidateCycleCount,
                                );
                            }
                            if CandidateCycleCount >= CycleCount {
                                continue;
                            }
                            $Repeaters = CandidateRepeaters;
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
                    let mut BaseRepeaters = $Repeaters.clone();
                    BaseRepeaters.remove(RemovedRepeater);
                    for CandidatePosition in &OrderedNodes {
                        if *CandidatePosition == $Root
                            || RequiredTargets.contains(CandidatePosition)
                            || BaseRepeaters.contains_key(CandidatePosition)
                            || $ForbiddenRepeaterPositions.contains(CandidatePosition)
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
                                || !$SelfContext
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
                                $Root,
                                &PhysicalNodes,
                                &CandidateRepeaters,
                                &$SelfContext.Adjacency,
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
                            $Repeaters = CandidateRepeaters;
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
                let mut Result = $Failure(
                    "NoPath",
                    "NoRepeaterSelfExcitingCycle",
                    1,
                    1,
                    $ExpansionCount,
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
        $TargetPaths.sort_by_key(|Value| Value.0);
        let mut RepeaterReservations: Vec<_> = $Repeaters.into_iter().collect();
        RepeaterReservations.sort_by_key(|Value| Value.0);
        // Boundary diagnostics are proportional to the routed tree, not the
        // entire sparse ownership region.  Scanning every allowed node for
        // every net made pass zero scale as nets times region size.
        let mut FinalNodes: Vec<_> = $Tree.into_iter().collect();
        FinalNodes.sort_unstable();
        let BoundaryFrontierNodes = FinalNodes
            .iter()
            .filter(|Value| {
                $SelfContext.Adjacency
                    .get(Value)
                    .into_iter()
                    .flatten()
                    .any(|Neighbor| {
                        !IsPreparedRouteNodeAllowed($Guide, $AdditionalAllowedNodes, Neighbor)
                    })
            })
            .copied()
            .collect();
        RouteTreeSearchResult {
            Status: "Routed".to_string(),
            NoPathReason: String::new(),
            Nodes: FinalNodes,
            $TargetPaths,
            BoundaryFrontierNodes,
            RepeaterReservations,
            $ExpansionCount,
            RepeaterRejectedCount: 0,
            RepeaterConstraintFailureCount: 0,
            ConflictResources: Vec::new(),
            RejectedPathCount: 0,
            NoGoodCount: 0,
            ElapsedMilliseconds: 0,
            IsRouted: true,
            IsBudgetExpired: false,
        }
    }};
}

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
        $ExpansionAdmission:ident,
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
        $SourcePaths:ident,
        $TargetPaths:ident,
        $RetainedMandatoryTargetNodes:ident
    ) => {{
        macro_rules! FinalizationDeadline {
            () => {
                if $Deadline.Check() {
                    return $Failure(
                        "NoPath",
                        "SearchLimitReached",
                        0,
                        0,
                        $ExpansionCount,
                    );
                }
            };
        }
        macro_rules! FinalizationPowers {
            ($Nodes:expr, $RepeaterMap:expr) => {{
                match PropagateCanonicalRoutePowerWithDeadline(
                    $Root,
                    $Nodes,
                    $RepeaterMap,
                    &$SelfContext.Adjacency,
                    $Deadline,
                ) {
                    DeadlineAwareElectricalResult::Complete(Value) => Value,
                    DeadlineAwareElectricalResult::DeadlineExhausted => {
                        return $Failure("NoPath", "SearchLimitReached", 0, 0, $ExpansionCount)
                    }
                }
            }};
        }
        macro_rules! FinalizationCycles {
            ($Nodes:expr, $RepeaterValues:expr) => {{
                match FindSelfExcitingRepeaterCyclesWithDeadline(
                    $Nodes,
                    $RepeaterValues,
                    $Deadline,
                ) {
                    DeadlineAwareElectricalResult::Complete(Value) => Value,
                    DeadlineAwareElectricalResult::DeadlineExhausted => {
                        return $Failure("NoPath", "SearchLimitReached", 0, 0, $ExpansionCount)
                    }
                }
            }};
        }
        FinalizationDeadline!();
        // A repeater embedded in an induced same-signal cycle can preserve a
        // transient pulse after the real source goes low.  This is not a
        // signal-strength issue: the routed node set itself supplies an
        // alternate directed output-to-input path around the repeater.  A
        // cycle repeater that is unnecessary for source-to-target delivery is
        // therefore a dust cell, not a legal refresh element.  Demote only
        // after exact canonical propagation proves every required target
        // remains powered; an essential cycle is rejected instead of emitted
        // as a stateful combinational route.
        for PositionValue in $RetainedMandatorySourceNodes {
            FinalizationDeadline!();
            $Tree.insert(PositionValue);
        }
        for PositionValue in $RetainedMandatoryTargetNodes {
            FinalizationDeadline!();
            $Tree.insert(PositionValue);
        }
        let mut PhysicalNodes = HashSet::new();
        for PositionValue in $Tree.iter().chain($FrozenReservedAccessNodes.iter()) {
            FinalizationDeadline!();
            PhysicalNodes.insert(*PositionValue);
        }
        let mut RequiredTargets = HashSet::new();
        for Branch in $TargetBranches.iter().chain($FrozenTargetBranches.iter()) {
            FinalizationDeadline!();
            if let Some(Target) = Branch.last().copied() {
                RequiredTargets.insert(Target);
            }
        }
        loop {
            FinalizationDeadline!();
            let mut RepeaterValues = Vec::with_capacity($Repeaters.len());
            for (PositionValue, Facing) in &$Repeaters {
                FinalizationDeadline!();
                RepeaterValues.push((*PositionValue, Facing.clone()));
            }
            RepeaterValues.sort_unstable();
            FinalizationDeadline!();
            let Cycles = FinalizationCycles!(&PhysicalNodes, &RepeaterValues);
            if Cycles.is_empty() {
                break;
            }
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                let CurrentPowers = FinalizationPowers!(&PhysicalNodes, &$Repeaters);
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
                FinalizationDeadline!();
                let mut CandidateRepeaters = $Repeaters.clone();
                FinalizationDeadline!();
                CandidateRepeaters.remove(Repeater);
                let CandidatePowers =
                    FinalizationPowers!(&PhysicalNodes, &CandidateRepeaters);
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
                    FinalizationDeadline!();
                    let mut OrderedCycleNodes = Cycle.clone();
                    OrderedCycleNodes.sort_unstable();
                    FinalizationDeadline!();
                    OrderedCycleNodes.dedup();
                    for CutNode in OrderedCycleNodes {
                        FinalizationDeadline!();
                        if CutNode == $Root
                            || RequiredTargets.contains(&CutNode)
                            || $UnblockedAdditionalNodes.contains(&CutNode)
                        {
                            continue;
                        }
                        let mut CandidatePhysicalNodes = PhysicalNodes.clone();
                        FinalizationDeadline!();
                        if !CandidatePhysicalNodes.remove(&CutNode) {
                            continue;
                        }
                        let mut CandidateRepeaters = $Repeaters.clone();
                        FinalizationDeadline!();
                        CandidateRepeaters.remove(&CutNode);
                        let CandidatePowers =
                            FinalizationPowers!(&CandidatePhysicalNodes, &CandidateRepeaters);
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
                        FinalizationDeadline!();
                        if FinalizationCycles!(
                            &CandidatePhysicalNodes,
                            &CandidateRepeaterValues
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
                    FinalizationDeadline!();
                    let mut CandidatePositions = Cycle.clone();
                    CandidatePositions.sort_unstable();
                    FinalizationDeadline!();
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
                            if $Deadline.Check()
                                || $ExpansionCount >= $MaximumExpansionCount
                            {
                                return $Failure(
                                    "NoPath",
                                    "SearchLimitReached",
                                    1,
                                    1,
                                    $ExpansionCount,
                                );
                            }
                            if $ExpansionAdmission.is_some_and(|Admission| {
                                !Admission.TryAdmitOne(ExpansionWorkPhase::Route)
                            }) {
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
                            FinalizationDeadline!();
                            CandidateRepeaters.insert(CandidatePosition, Facing.to_string());
                            let CandidatePowers =
                                FinalizationPowers!(&PhysicalNodes, &CandidateRepeaters);
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
                            FinalizationDeadline!();
                            let CandidateCycleCount =
                                FinalizationCycles!(&PhysicalNodes, &CandidateRepeaterValues).len();
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
                FinalizationDeadline!();
                let FacingValues = [
                    ("east", (-1, 0, 0)),
                    ("north", (0, 0, 1)),
                    ("south", (0, 0, -1)),
                    ("west", (1, 0, 0)),
                ];
                'CycleRepeater: for (RemovedRepeater, _Cycle) in &Cycles {
                    FinalizationDeadline!();
                    let mut BaseRepeaters = $Repeaters.clone();
                    FinalizationDeadline!();
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
                            FinalizationDeadline!();
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
                            FinalizationDeadline!();
                            CandidateRepeaters.insert(*CandidatePosition, Facing.to_string());
                            let CandidatePowers =
                                FinalizationPowers!(&PhysicalNodes, &CandidateRepeaters);
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
                            FinalizationDeadline!();
                            if FinalizationCycles!(
                                &PhysicalNodes,
                                &CandidateRepeaterValues
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
                FinalizationDeadline!();
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
        FinalizationDeadline!();
        $TargetPaths.sort_by_key(|Value| Value.0);
        FinalizationDeadline!();
        let mut RepeaterReservations = Vec::with_capacity($Repeaters.len());
        for Value in $Repeaters {
            FinalizationDeadline!();
            RepeaterReservations.push(Value);
        }
        RepeaterReservations.sort_by_key(|Value| Value.0);
        FinalizationDeadline!();
        // Boundary diagnostics are proportional to the routed tree, not the
        // entire sparse ownership region.  Scanning every allowed node for
        // every net made pass zero scale as nets times region size.
        let mut FinalNodes = Vec::with_capacity($Tree.len());
        for Value in $Tree {
            FinalizationDeadline!();
            FinalNodes.push(Value);
        }
        FinalNodes.sort_unstable();
        FinalizationDeadline!();
        let mut BoundaryFrontierNodes = Vec::new();
        for Value in &FinalNodes {
            FinalizationDeadline!();
            let mut IsBoundary = false;
            for Neighbor in $SelfContext.Adjacency.get(Value).into_iter().flatten() {
                FinalizationDeadline!();
                if !IsPreparedRouteNodeAllowed($Guide, $AdditionalAllowedNodes, Neighbor) {
                    IsBoundary = true;
                    break;
                }
            }
            if IsBoundary {
                BoundaryFrontierNodes.push(*Value);
            }
        }
        FinalizationDeadline!();
        RouteTreeSearchResult {
            Status: "Routed".to_string(),
            NoPathReason: String::new(),
            Nodes: FinalNodes,
            $SourcePaths,
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

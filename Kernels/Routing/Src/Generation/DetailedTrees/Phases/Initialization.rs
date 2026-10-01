macro_rules! InitializePreparedDetailedRouteSearch {
    (
        $SelfContext:expr,
        $Starts:ident,
        $TargetBranches:ident,
        $FrozenTargetBranches:ident,
        $Guide:ident,
        $AdditionalAllowedNodes:ident,
        $UnblockedAdditionalNodes:ident,
        $BaseBlockedNodes:ident,
        $FrozenSourceBranch:ident,
        $ForbiddenRepeaterPositions:ident,
        $DebugLabel:ident,
        $MaximumExpansionCount:ident,
        $ExpansionAdmission:ident,
        $Deadline:ident,
        $Failure:ident,
        $BlockedNodes:ident,
        $AdditionalNodeCosts:ident,
        $Root:ident,
        $StartDirection:ident,
        $RootState:ident,
        $Tree:ident,
        $StateByNode:ident,
        $ParentByNode:ident,
        $Repeaters:ident,
        $ExpansionCount:ident,
        $FrozenReservedAccessNodes:ident,
        $TargetPaths:ident
    ) => {
        let $Failure = |Status: &str,
                       NoPathReason: &str,
                       RepeaterRejectedCount: usize,
                       RepeaterConstraintFailureCount: usize,
                       $ExpansionCount: usize| {
            let Expired = $Deadline.Check();
            let EffectiveStatus = if Expired { "BudgetExpired" } else { Status };
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected detailed failure signal={} status={} reason={} expansions={}",
                    $DebugLabel, EffectiveStatus, NoPathReason, $ExpansionCount,
                );
            }
            RouteTreeSearchResult {
                Status: EffectiveStatus.to_string(),
                NoPathReason: NoPathReason.to_string(),
                Nodes: Vec::new(),
                SourcePaths: Vec::new(),
                $TargetPaths: Vec::new(),
                BoundaryFrontierNodes: Vec::new(),
                RepeaterReservations: Vec::new(),
                $ExpansionCount,
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
        let mut $BlockedNodes = $BaseBlockedNodes.clone();
        $BlockedNodes.extend($Guide.BoundaryBlockedNodes.iter().copied());
        for Value in $UnblockedAdditionalNodes {
            $BlockedNodes.remove(Value);
        }
        for (Index, Value) in $AdditionalAllowedNodes.iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && $Deadline.Check() {
                return $Failure("NoPath", "SearchLimitReached", 0, 0, 0);
            }
            for Neighbor in $SelfContext.Adjacency.get(Value).into_iter().flatten() {
                if !IsPreparedRouteNodeAllowed($Guide, $AdditionalAllowedNodes, Neighbor) {
                    $BlockedNodes.insert(*Neighbor);
                }
            }
        }
        if $TargetBranches.iter().any(|Branch| {
            Branch.is_empty()
                || Branch.windows(2).any(|Values| {
                    !$SelfContext
                        .Adjacency
                        .get(&Values[0])
                        .is_some_and(|Neighbors| Neighbors.contains(&Values[1]))
                })
                || Branch.iter().any(|Value| $BlockedNodes.contains(Value))
        }) {
            return $Failure("NoPath", "NoPathGeometry", 0, 0, 0);
        }
        let mut $AdditionalNodeCosts = HashMap::new();
        if $Guide.GuidePenalty > 0 && !$Guide.PreferredColumns.is_empty() {
            for Value in $AdditionalAllowedNodes {
                if $Guide.AllowedNodes.contains(Value)
                    || ($Guide.UseColumnMembership
                        && $Guide.AllowedColumns.contains(&(Value.0, Value.2)))
                {
                    continue;
                }
                let Distance = $Guide
                    .PreferredColumns
                    .iter()
                    .map(|Column| (Value.0 - Column.0).abs() + (Value.2 - Column.1).abs())
                    .min()
                    .unwrap_or(0);
                $AdditionalNodeCosts.insert(*Value, Distance * $Guide.GuidePenalty);
            }
        }
        let $Starts: Vec<_> = $Starts
            .iter()
            .copied()
            .filter(|Value| {
                IsPreparedRouteNodeAllowed($Guide, $AdditionalAllowedNodes, Value)
                    && $SelfContext.Adjacency.contains_key(Value)
                    && !$BlockedNodes.contains(Value)
            })
            .collect();
        let Some($Root) = $Starts.first().copied() else {
            return $Failure("NoPath", "NoPathGeometry", 0, 0, 0);
        };

        let $StartDirection = (0, 0, 0);
        let $RootState = ($Root, $StartDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH);
        let mut $Tree = HashSet::from([$Root]);
        let mut $StateByNode = HashMap::from([($Root, $RootState)]);
        let mut $ParentByNode: HashMap<Position, Position> = HashMap::new();
        let mut $Repeaters: HashMap<Position, String> = HashMap::new();
        let mut $ExpansionCount = 0usize;
        let $FrozenReservedAccessNodes = $FrozenSourceBranch
            .map(|SourceBranch| {
                SourceBranch
                    .iter()
                    .chain($FrozenTargetBranches.iter().flatten())
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
        if $FrozenSourceBranch.is_some() && !$Guide.CertifiedPaths.is_empty() {
            let CertifiedGeometryComplete = $Guide.CertifiedPaths.iter().all(|Path| {
                !$Deadline.Check()
                    && Path.first().copied() == Some($Root)
                    && Path.windows(2).all(|Values| {
                        !$Deadline.Check()
                            && $SelfContext
                                .Adjacency
                                .get(&Values[0])
                                .is_some_and(|Neighbors| Neighbors.contains(&Values[1]))
                    })
                    && Path.iter().all(|PositionValue| {
                        !$Deadline.Check()
                            && $SelfContext.Adjacency.contains_key(PositionValue)
                            && (!$BlockedNodes.contains(PositionValue)
                                || $UnblockedAdditionalNodes.contains(PositionValue))
                            && IsPreparedRouteNodeAllowed(
                                $Guide,
                                $AdditionalAllowedNodes,
                                PositionValue,
                            )
                    })
            });
            let mut CertifiedRepeaterMap = HashMap::new();
            let mut CertifiedRepeaterComplete = true;
            for (PositionValue, Facing) in $Guide.CertifiedRepeaters.iter().cloned() {
                if $Deadline.Check()
                    || $ForbiddenRepeaterPositions.contains(&PositionValue)
                    || !matches!(Facing.as_str(), "west" | "east" | "north" | "south")
                    || CertifiedRepeaterMap.insert(PositionValue, Facing).is_some()
                {
                    CertifiedRepeaterComplete = false;
                    break;
                }
            }
            if std::env::var("RCS_DEBUG_CERTIFIED_WARM_SIGNAL")
                .ok()
                .is_some_and(|Signal| Signal == $DebugLabel)
                && !(CertifiedGeometryComplete && CertifiedRepeaterComplete)
            {
                eprintln!(
                    "selected certified warm precheck signal={} geometry={} repeaters={} root={:?} path_starts={:?} blocked={:?} disallowed={:?}",
                    $DebugLabel,
                    CertifiedGeometryComplete,
                    CertifiedRepeaterComplete,
                    $Root,
                    $Guide.CertifiedPaths.iter().filter_map(|Path| Path.first()).collect::<Vec<_>>(),
                    $Guide.CertifiedPaths
                        .iter()
                        .flatten()
                        .filter(|PositionValue| $BlockedNodes.contains(PositionValue))
                        .take(16)
                        .collect::<Vec<_>>(),
                    $Guide.CertifiedPaths
                        .iter()
                        .flatten()
                        .filter(|PositionValue| !IsPreparedRouteNodeAllowed($Guide, $AdditionalAllowedNodes, PositionValue))
                        .take(16)
                        .collect::<Vec<_>>(),
                );
            }
            if CertifiedGeometryComplete && CertifiedRepeaterComplete {
                let mut CertifiedNodes = HashSet::new();
                for PositionValue in $Guide
                    .CertifiedPaths
                    .iter()
                    .flatten()
                    .copied()
                    .chain($FrozenReservedAccessNodes.iter().copied())
                {
                    if $Deadline.Check() {
                        return $Failure("NoPath", "SearchLimitReached", 0, 0, 0);
                    }
                    CertifiedNodes.insert(PositionValue);
                }
                let CertifiedRepeaterValues = $Guide.CertifiedRepeaters.clone();
                let (CertifiedPowers, CertifiedParents) =
                    match PropagateCanonicalRoutePowerWithParentsWithDeadline(
                        $Root,
                        &CertifiedNodes,
                        &CertifiedRepeaterMap,
                        &$SelfContext.Adjacency,
                        $Deadline,
                    ) {
                        DeadlineAwareElectricalResult::Complete(Value) => Value,
                        DeadlineAwareElectricalResult::DeadlineExhausted => {
                            return $Failure("NoPath", "SearchLimitReached", 0, 0, 0)
                        }
                    };
                let mut CertifiedSourcePaths = Vec::with_capacity($Starts.len());
                let mut CertifiedSourcePathsComplete = true;
                for Start in $Starts.iter().copied() {
                    let mut Path = vec![Start];
                    let mut Cursor = Start;
                    while Cursor != $Root {
                        if $Deadline.Check() || Path.len() > CertifiedNodes.len() {
                            CertifiedSourcePathsComplete = false;
                            break;
                        }
                        let Some(Previous) = CertifiedParents.get(&Cursor).copied() else {
                            CertifiedSourcePathsComplete = false;
                            break;
                        };
                        Path.push(Previous);
                        Cursor = Previous;
                    }
                    if !CertifiedSourcePathsComplete {
                        break;
                    }
                    Path.reverse();
                    CertifiedSourcePaths.push(Path);
                }
                let RequiredTargetsPowered = $TargetBranches
                    .iter()
                    .filter_map(|Branch| Branch.last())
                    .all(|Target| !$Deadline.Check() && CertifiedPowers.contains_key(Target));
                let FrozenClaimsPresent = $FrozenReservedAccessNodes
                    .iter()
                    .all(|PositionValue| {
                        !$Deadline.Check() && CertifiedNodes.contains(PositionValue)
                    });
                let NoSelfExcitingCycle = match FindSelfExcitingRepeaterCyclesWithDeadline(
                    &CertifiedNodes,
                    &CertifiedRepeaterValues,
                    $Deadline,
                ) {
                    DeadlineAwareElectricalResult::Complete(Cycles) => Cycles.is_empty(),
                    DeadlineAwareElectricalResult::DeadlineExhausted => {
                        return $Failure("NoPath", "SearchLimitReached", 0, 0, 0)
                    }
                };
                if std::env::var("RCS_DEBUG_CERTIFIED_WARM_SIGNAL")
                    .ok()
                    .is_some_and(|Signal| Signal == $DebugLabel)
                {
                    eprintln!(
                        "selected certified warm signal={} geometry={} repeaters={} powered={} frozen={} cycle_free={} root={:?} path_starts={:?} unpowered_targets={:?}",
                        $DebugLabel,
                        CertifiedGeometryComplete,
                        CertifiedRepeaterComplete,
                        RequiredTargetsPowered,
                        FrozenClaimsPresent,
                        NoSelfExcitingCycle,
                        $Root,
                        $Guide.CertifiedPaths.iter().filter_map(|Path| Path.first()).collect::<Vec<_>>(),
                        $TargetBranches
                            .iter()
                            .filter_map(|Branch| Branch.last())
                            .filter(|Target| !CertifiedPowers.contains_key(Target))
                            .collect::<Vec<_>>(),
                    );
                }
                if RequiredTargetsPowered
                    && FrozenClaimsPresent
                    && NoSelfExcitingCycle
                    && CertifiedSourcePathsComplete
                {
                    let mut TargetPaths = Vec::with_capacity($Guide.CertifiedPaths.len());
                    let mut CertifiedExpansionCount = 0usize;
                    for Path in &$Guide.CertifiedPaths {
                        if $Deadline.Check() {
                            return $Failure("NoPath", "SearchLimitReached", 0, 0, 0);
                        }
                        let Some(Target) = Path.last().copied() else {
                            return $Failure("NoPath", "NoPathGeometry", 0, 0, 0);
                        };
                        CertifiedExpansionCount = match CertifiedExpansionCount
                            .checked_add(Path.len())
                        {
                            Some(Value) => Value,
                            None => {
                                return $Failure("NoPath", "SearchLimitReached", 0, 0, 0)
                            }
                        };
                        TargetPaths.push((Target, Path.clone()));
                    }
                    if CertifiedExpansionCount > $MaximumExpansionCount {
                        let mut Result = DetailedRouteTreeBudgetExpiredResult();
                        Result.NoPathReason = "SearchLimitReached".to_string();
                        return Result;
                    }
                    if let Some(Admission) = $ExpansionAdmission {
                        for _ in 0..CertifiedExpansionCount {
                            if $Deadline.Check()
                                || !Admission.TryAdmitOne(ExpansionWorkPhase::Route)
                            {
                                let mut Result = DetailedRouteTreeBudgetExpiredResult();
                                Result.NoPathReason = "SearchLimitReached".to_string();
                                Result.ExpansionCount = $ExpansionCount;
                                return Result;
                            }
                            $ExpansionCount += 1;
                        }
                    } else {
                        $ExpansionCount = CertifiedExpansionCount;
                    }
                    let mut Nodes = CertifiedNodes.into_iter().collect::<Vec<_>>();
                    Nodes.sort_unstable();
                    let mut RepeaterReservations = CertifiedRepeaterValues;
                    RepeaterReservations.sort_unstable();
                    if $Deadline.Check() {
                        return $Failure("NoPath", "SearchLimitReached", 0, 0, 0);
                    }
                    return RouteTreeSearchResult {
                        Status: "Routed".to_string(),
                        NoPathReason: String::new(),
                        Nodes,
                        SourcePaths: CertifiedSourcePaths,
                        $TargetPaths: TargetPaths,
                        BoundaryFrontierNodes: Vec::new(),
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
                    };
                }
            }
        }
        if $Deadline.Check() {
            return $Failure("NoPath", "SearchLimitReached", 0, 0, 0);
        }
    };
}

macro_rules! IntegratePreparedDetailedSource {
    (
        $SelfContext:expr,
        $Starts:ident,
        $TargetBranches:ident,
        $FrozenTargetBranches:ident,
        $Guide:ident,
        $AdditionalAllowedNodes:ident,
        $EnforceSignalStrength:ident,
        $FrozenSourceBranch:ident,
        $ForbiddenRepeaterPositions:ident,
        $DebugLabel:ident,
        $Deadline:ident,
        $Failure:ident,
        $BlockedNodes:ident,
        $Root:ident,
        $StartDirection:ident,
        $RootState:ident,
        $Tree:ident,
        $StateByNode:ident,
        $ParentByNode:ident,
        $Repeaters:ident,
        $ExpansionCount:ident,
        $FrozenReservedAccessNodes:ident,
        $RouteIntoTree:ident,
        $RouteFrozenSourceIntoTree:ident,
        $FrozenSourceRepairAllowedNodes:ident,
        $RetainedMandatorySourceNodes:ident,
        $FrozenSourceFrontierState:ident,
        $RootedFrozenPortalNodes:ident,
        $TargetPaths:ident,
        $GlobalRoutingNodes:ident,
        $RetainedMandatoryTargetNodes:ident
    ) => {
        // A factorized access payload contains immutable terminal-to-ingress
        // geometry.  Those claims must survive materialization, but they are
        // not independently powered roots.  Route once from the real source
        // to the selected ingress so the native search can use the exact
        // prefix when it is legal or add one powered bypass when it is not.
        // Retain the immutable access cells only after all powered branches
        // have been built, preventing an unpowered prefix cell from becoming
        // a fresh signal-strength seed.
        let $RetainedMandatorySourceNodes = if $FrozenSourceBranch.is_some() {
            $Starts.iter().copied().collect::<HashSet<_>>()
        } else {
            HashSet::new()
        };
        let mut $FrozenSourceFrontierState = None;
        if let Some(SourceBranch) = $FrozenSourceBranch {
            if SourceBranch.first().copied() != Some($Root) {
                return $Failure("NoPath", "NoPathGeometry", 0, 0, $ExpansionCount);
            }
            let mut CurrentState = $RootState;
            for (BranchIndex, Next) in SourceBranch.iter().copied().enumerate().skip(1) {
                if $BlockedNodes.contains(&Next)
                    || !IsPreparedRouteNodeAllowed($Guide, $AdditionalAllowedNodes, &Next)
                    || !$SelfContext
                        .Adjacency
                        .get(&CurrentState.0)
                        .is_some_and(|Neighbors| Neighbors.contains(&Next))
                {
                    return $Failure("NoPath", "NoPathGeometry", 0, 0, $ExpansionCount);
                }
                let Direction = (
                    Next.0 - CurrentState.0 .0,
                    Next.1 - CurrentState.0 .1,
                    Next.2 - CurrentState.0 .2,
                );
                let CanPhysicallyRefreshCurrent = CurrentState.1 != $StartDirection
                    && CurrentState.1 == Direction
                    && Direction.1 == 0
                    && !$ForbiddenRepeaterPositions.contains(&CurrentState.0);
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
                            || $ForbiddenRepeaterPositions.contains(&Candidate)
                        {
                            return None;
                        }
                        Some(CandidateIndex - CurrentBranchIndex)
                    })
                    .unwrap_or(SourceBranch.len() - 1 - CurrentBranchIndex);
                let MustRefreshCurrent = CurrentState.2
                    <= crate::Path::PathRouting::REPEATER_TURN_HEADROOM
                    || NextRefreshDistance >= usize::from(CurrentState.2);
                let CanRefreshCurrent = CanPhysicallyRefreshCurrent && MustRefreshCurrent;
                let CanRefreshNext = SourceBranch
                    .get(BranchIndex + 1)
                    .map(|After| {
                        let NextDirection = (After.0 - Next.0, After.1 - Next.1, After.2 - Next.2);
                        Direction == NextDirection
                            && Direction.1 == 0
                            && !$ForbiddenRepeaterPositions.contains(&Next)
                    })
                    .unwrap_or(false);
                let RemainingStrength = if CanRefreshCurrent {
                    let Some(Facing) = RepeaterFacing(CurrentState.0, Next) else {
                        return $Failure("NoPath", "NoPathGeometry", 1, 0, $ExpansionCount);
                    };
                    $Repeaters.insert(CurrentState.0, Facing);
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else if CurrentState.2 > 1 {
                    CurrentState.2 - 1
                } else if CanRefreshNext {
                    let Some(After) = SourceBranch.get(BranchIndex + 1).copied() else {
                        break;
                    };
                    let Some(Facing) = RepeaterFacing(Next, After) else {
                        return $Failure("NoPath", "NoPathGeometry", 1, 0, $ExpansionCount);
                    };
                    $Repeaters.insert(Next, Facing);
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else {
                    break;
                };
                let NextState = (Next, Direction, RemainingStrength);
                $ParentByNode.insert(Next, CurrentState.0);
                $Tree.insert(Next);
                $StateByNode.insert(Next, NextState);
                CurrentState = NextState;
            }
            $FrozenSourceFrontierState = Some(CurrentState);
        }
        if let (Some(SourceIngress), Some(FrontierState), Some(SourceAllowed)) = (
            $FrozenSourceBranch.and_then(|Branch| Branch.last().copied()),
            $FrozenSourceFrontierState,
            $FrozenSourceRepairAllowedNodes.as_ref(),
        ) {
            if FrontierState.0 != SourceIngress
                && FrontierState.2
                    <= crate::Path::PathRouting::REPEATER_TURN_HEADROOM.saturating_add(1)
            {
                let Some((DynamicBlocked, EdgeCosts)) = BuildRootedTreeBlockages(
                    $SelfContext,
                    $Guide,
                    $AdditionalAllowedNodes,
                    &$BlockedNodes,
                    &$Tree,
                    &$FrozenReservedAccessNodes,
                    &$Deadline,
                ) else {
                    return $Failure("NoPath", "NoPathGeometry", 0, 0, $ExpansionCount);
                };
                let mut RepeaterLaneCandidates = Vec::new();
                for First in $SelfContext
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
                        || $Tree.contains(&First)
                        || $Tree.contains(&Second)
                        || $FrozenReservedAccessNodes.contains(&First)
                        || $FrozenReservedAccessNodes.contains(&Second)
                        || $ForbiddenRepeaterPositions.contains(&First)
                        || !$SelfContext
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
                    $ParentByNode.insert(First, FrontierState.0);
                    $ParentByNode.insert(Second, First);
                    $Tree.insert(First);
                    $Tree.insert(Second);
                    $StateByNode.insert(First, FirstState);
                    $StateByNode.insert(Second, SecondState);
                    let Some(Facing) = RepeaterFacing(First, Second) else {
                        return $Failure("NoPath", "NoPathGeometry", 1, 0, $ExpansionCount);
                    };
                    $Repeaters.insert(First, Facing);
                }
            }
        }
        let StartsToConnect = if let Some(SourceBranch) = $FrozenSourceBranch {
            SourceBranch.last().copied().into_iter().collect::<Vec<_>>()
        } else {
            $Starts.iter().copied().skip(1).collect::<Vec<_>>()
        };
        for Start in StartsToConnect {
            if $Tree.contains(&Start) {
                continue;
            }
            let Result = if $FrozenSourceBranch.is_some() {
                $RouteFrozenSourceIntoTree(Start, &$Tree, &$StateByNode, &$Repeaters)
            } else {
                $RouteIntoTree(Start, &[], &HashSet::new(), &$Tree, &$StateByNode, &$Repeaters)
            };
            let Some(Result) = Result else {
                return $Failure(
                    "NoPath",
                    "NoPathGeometry",
                    usize::from($EnforceSignalStrength),
                    0,
                    $ExpansionCount,
                );
            };
            if Result.Status != "Routed" {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected source ingress failure signal={} target={:?} status={} reason={} expansions={} frontier={:?}",
                        $DebugLabel,
                        Start,
                        Result.Status,
                        Result.NoPathReason,
                        Result.$ExpansionCount,
                        $FrozenSourceFrontierState,
                    );
                }
                return $Failure(
                    "NoPath",
                    if Result.NoPathReason.is_empty() {
                        "NoPathGeometry"
                    } else {
                        &Result.NoPathReason
                    },
                    Result.RepeaterRejectedCount,
                    Result.RepeaterConstraintFailures,
                    $ExpansionCount + Result.$ExpansionCount,
                );
            };
            $ExpansionCount += Result.$ExpansionCount;
            for Values in Result.StatePath.windows(2) {
                let Previous = Values[0].0;
                let Current = Values[1].0;
                if $Tree.insert(Current) {
                    $ParentByNode.insert(Current, Previous);
                }
                $StateByNode.insert(Current, Values[1]);
            }
            for (PositionValue, Facing) in Result.RepeaterReservations {
                $Repeaters.entry(PositionValue).or_insert(Facing);
            }
        }

        // Some immutable target-side claim fragments are already connected
        // to the real source through their exact frozen geometry.  Python
        // deliberately omits those branches from TargetBranches, because
        // no new route is required.  Import their exact root component here
        // instead of leaving their portal cells reserved-but-unpowered.
        // Only powered branch portals become global attachment frontiers;
        // arbitrary access interiors remain directed immutable geometry.
        let mut $RootedFrozenPortalNodes = HashSet::new();
        if $FrozenSourceBranch.is_some() {
            let ImmutableNodes = $Tree
                .iter()
                .copied()
                .chain($FrozenTargetBranches.iter().flatten().copied())
                .collect::<HashSet<_>>();
            let mut RootedNodes = HashSet::from([$Root]);
            let mut RootedParent = HashMap::<Position, Position>::new();
            let mut Pending = VecDeque::from([$Root]);
            while let Some(Current) = Pending.pop_front() {
                let mut Neighbors = $SelfContext
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
                if $Tree.insert(PositionValue) {
                    if let Some(Previous) = RootedParent.get(&PositionValue).copied() {
                        $ParentByNode.insert(PositionValue, Previous);
                    }
                }
            }
            let RootedPowers =
                PropagateCanonicalRoutePower($Root, &RootedNodes, &$Repeaters, &$SelfContext.Adjacency);
            for PositionValue in RootedNodes.iter().copied() {
                let Some(Power) = RootedPowers.get(&PositionValue).copied() else {
                    continue;
                };
                let State = if PositionValue == $Root {
                    $RootState
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
                $StateByNode.insert(PositionValue, State);
            }
            for Branch in $FrozenTargetBranches {
                if let Some(Portal) = Branch.first().copied() {
                    if RootedPowers.contains_key(&Portal) {
                        $RootedFrozenPortalNodes.insert(Portal);
                    }
                }
            }
        }

        let mut $TargetPaths = Vec::new();
        // Frozen access geometry is immutable signal ownership, but only the
        // selected ingress is a legal launch point for global routing.  Keep
        // the powered stub in the physical tree while excluding its interior
        // nodes (and later target-stub interiors) from the global attachment
        // frontier.
        let mut $GlobalRoutingNodes = if $FrozenSourceBranch.is_some() {
            let mut Values = $FrozenSourceBranch
                .and_then(|Branch| Branch.last().copied())
                .into_iter()
                .collect::<HashSet<_>>();
            Values.extend($RootedFrozenPortalNodes.iter().copied());
            Values
        } else {
            $Tree.clone()
        };
        let $RetainedMandatoryTargetNodes = if $FrozenSourceBranch.is_some() {
            $FrozenTargetBranches
                .iter()
                .flatten()
                .copied()
                .collect::<HashSet<_>>()
        } else {
            HashSet::new()
        };
    };
}

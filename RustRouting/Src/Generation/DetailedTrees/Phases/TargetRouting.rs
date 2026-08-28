macro_rules! RoutePreparedDetailedTargets {
    (
        $SelfContext:expr,
        $TargetBranches:ident,
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
        $Failure:ident,
        $BlockedNodes:ident,
        $Root:ident,
        $StartDirection:ident,
        $Tree:ident,
        $StateByNode:ident,
        $ParentByNode:ident,
        $Repeaters:ident,
        $ExpansionCount:ident,
        $FrozenReservedAccessNodes:ident,
        $RouteIntoTree:ident,
        $RouteFrozenTargetIntoTree:ident,
        $TargetPaths:ident,
        $GlobalRoutingNodes:ident
    ) => {
        let mut RemainingBranches = $TargetBranches.to_vec();
        while !RemainingBranches.is_empty() {
            let Some(SelectedIndex) = RemainingBranches
                .iter()
                .enumerate()
                .min_by_key(|(_, Branch)| {
                    let Terminal = Branch
                        .last()
                        .copied()
                        .unwrap_or((i32::MAX, i32::MAX, i32::MAX));
                    let Attachment = if $FrozenSourceBranch.is_some() {
                        Branch.first().copied().unwrap_or(Terminal)
                    } else {
                        Terminal
                    };
                    let Distance = $GlobalRoutingNodes
                        .iter()
                        .map(|Start| ManhattanDistance(*Start, Attachment))
                        .min()
                        .unwrap_or(i32::MAX);
                    (Distance, Attachment, Terminal, Branch.len())
                })
                .map(|(Index, _)| Index)
            else {
                return $Failure("NoPath", "NoPathGeometry", 0, 0, $ExpansionCount);
            };
            let Branch = RemainingBranches.remove(SelectedIndex);
            let PortalTarget = Branch[0];
            let ReservedNodes: HashSet<_> = if $FrozenSourceBranch.is_some() {
                $FrozenReservedAccessNodes.clone()
            } else {
                Branch.iter().copied().collect()
            };
            let EarlyRepairRadius = i32::from(crate::Path::PathRouting::REPEATER_TURN_HEADROOM);
            let EarlyTargetAllowed = $AdditionalAllowedNodes
                .iter()
                .copied()
                .filter(|Candidate| {
                    Branch.iter().any(|BranchNode| {
                        ManhattanDistance(*Candidate, *BranchNode) <= EarlyRepairRadius
                    })
                })
                .collect::<HashSet<_>>();
            let EarlyPoweredFrontierCount = $StateByNode
                .values()
                .filter(|State| {
                    EarlyTargetAllowed.contains(&State.0)
                        || $SelfContext.Adjacency.get(&State.0).is_some_and(|Neighbors| {
                            Neighbors
                                .iter()
                                .any(|Value| EarlyTargetAllowed.contains(Value))
                        })
                })
                .count();
            if $FrozenSourceBranch.is_none() && EarlyPoweredFrontierCount >= 3 {
                if let Some(LocalResult) = $RouteFrozenTargetIntoTree(
                    &Branch,
                    &$Tree,
                    &$StateByNode,
                    &$Repeaters,
                    EarlyRepairRadius,
                    None,
                    $MaximumExpansionCount.saturating_sub($ExpansionCount),
                ) {
                    $ExpansionCount = $ExpansionCount.saturating_add(LocalResult.$ExpansionCount);
                    if LocalResult.Status == "Routed" {
                        for Values in LocalResult.StatePath.windows(2) {
                            let Previous = Values[0].0;
                            let Current = Values[1].0;
                            if $Tree.insert(Current) {
                                $ParentByNode.insert(Current, Previous);
                            }
                            $StateByNode.insert(Current, Values[1]);
                        }
                        for (PositionValue, Facing) in LocalResult.RepeaterReservations {
                            $Repeaters.entry(PositionValue).or_insert(Facing);
                        }
                        let Target = Branch
                            .last()
                            .copied()
                            .expect("frozen target branch is nonempty");
                        let mut Path = vec![Target];
                        let mut Cursor = Target;
                        while Cursor != $Root {
                            let Some(Previous) = $ParentByNode.get(&Cursor).copied() else {
                                return $Failure("NoPath", "NoPathGeometry", 0, 0, $ExpansionCount);
                            };
                            Path.push(Previous);
                            Cursor = Previous;
                        }
                        Path.reverse();
                        $TargetPaths.push((Target, Path));
                        continue;
                    }
                }
            }
            let PhysicalPowersBeforePortalAttachment = $FrozenSourceBranch.map(|_| {
                let PhysicalNodes = $Tree
                    .union(&$FrozenReservedAccessNodes)
                    .copied()
                    .collect::<HashSet<_>>();
                PropagateCanonicalRoutePower($Root, &PhysicalNodes, &$Repeaters, &$SelfContext.Adjacency)
            });
            if !$Tree.contains(&PortalTarget)
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
                let EligibleGlobalStates = $StateByNode
                    .iter()
                    .filter_map(|(PositionValue, State)| {
                        if !$GlobalRoutingNodes.contains(PositionValue)
                            || $Repeaters.contains_key(PositionValue)
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
                let Result = $RouteIntoTree(
                    PortalTarget,
                    TargetContinuation,
                    &ReservedNodes,
                    &$Tree,
                    &EligibleGlobalStates,
                    &$Repeaters,
                );
                let Some(Result) = Result else {
                    if let Some(LocalResult) = $FrozenSourceBranch.and_then(|_| {
                        $RouteFrozenTargetIntoTree(
                            &Branch,
                            &$Tree,
                            &$StateByNode,
                            &$Repeaters,
                            i32::from(crate::Path::PathRouting::REPEATER_TURN_HEADROOM),
                            PhysicalPowersBeforePortalAttachment.as_ref(),
                            $MaximumExpansionCount.saturating_sub($ExpansionCount),
                        )
                    }) {
                        $ExpansionCount = $ExpansionCount.saturating_add(LocalResult.$ExpansionCount);
                        if LocalResult.Status == "Routed" {
                            for Values in LocalResult.StatePath.windows(2) {
                                let Previous = Values[0].0;
                                let Current = Values[1].0;
                                if $Tree.insert(Current) {
                                    $ParentByNode.insert(Current, Previous);
                                }
                                $StateByNode.insert(Current, Values[1]);
                            }
                            for (PositionValue, Facing) in LocalResult.RepeaterReservations {
                                $Repeaters.entry(PositionValue).or_insert(Facing);
                            }
                            let Target = Branch
                                .last()
                                .copied()
                                .expect("frozen target branch is nonempty");
                            let mut Path = vec![Target];
                            let mut Cursor = Target;
                            while Cursor != $Root {
                                let Some(Previous) = $ParentByNode.get(&Cursor).copied() else {
                                    return $Failure(
                                        "NoPath",
                                        "NoPathGeometry",
                                        0,
                                        0,
                                        $ExpansionCount,
                                    );
                                };
                                Path.push(Previous);
                                Cursor = Previous;
                            }
                            Path.reverse();
                            $TargetPaths.push((Target, Path));
                            continue;
                        }
                    }
                    return $Failure(
                        "NoPath",
                        "NoPathGeometry",
                        usize::from($EnforceSignalStrength),
                        0,
                        $ExpansionCount,
                    );
                };
                if Result.Status != "Routed" {
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                        || std::env::var_os("RCS_DEBUG_SELECTED_WORLD_SUMMARY").is_some()
                    {
                        eprintln!(
                            "selected portal attachment failure signal={} portal={:?} status={} reason={} expansions={} eligible_starts={} tree_nodes={}",
                            $DebugLabel,
                            PortalTarget,
                            Result.Status,
                            Result.NoPathReason,
                            Result.$ExpansionCount,
                            EligibleGlobalStates.len(),
                            $Tree.len(),
                        );
                    }
                    if let Some(LocalResult) = $FrozenSourceBranch.and_then(|_| {
                        $RouteFrozenTargetIntoTree(
                            &Branch,
                            &$Tree,
                            &$StateByNode,
                            &$Repeaters,
                            i32::from(crate::Path::PathRouting::REPEATER_TURN_HEADROOM),
                            PhysicalPowersBeforePortalAttachment.as_ref(),
                            $MaximumExpansionCount.saturating_sub($ExpansionCount),
                        )
                    }) {
                        $ExpansionCount = $ExpansionCount.saturating_add(LocalResult.$ExpansionCount);
                        if LocalResult.Status == "Routed" {
                            for Values in LocalResult.StatePath.windows(2) {
                                let Previous = Values[0].0;
                                let Current = Values[1].0;
                                if $Tree.insert(Current) {
                                    $ParentByNode.insert(Current, Previous);
                                }
                                $StateByNode.insert(Current, Values[1]);
                            }
                            for (PositionValue, Facing) in LocalResult.RepeaterReservations {
                                $Repeaters.entry(PositionValue).or_insert(Facing);
                            }
                            let Target = Branch
                                .last()
                                .copied()
                                .expect("frozen target branch is nonempty");
                            let mut Path = vec![Target];
                            let mut Cursor = Target;
                            while Cursor != $Root {
                                let Some(Previous) = $ParentByNode.get(&Cursor).copied() else {
                                    return $Failure(
                                        "NoPath",
                                        "NoPathGeometry",
                                        0,
                                        0,
                                        $ExpansionCount,
                                    );
                                };
                                Path.push(Previous);
                                Cursor = Previous;
                            }
                            Path.reverse();
                            $TargetPaths.push((Target, Path));
                            continue;
                        }
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
                    $GlobalRoutingNodes.insert(Current);
                }
                for (PositionValue, Facing) in Result.RepeaterReservations {
                    $Repeaters.entry(PositionValue).or_insert(Facing);
                }
            }

            // In the factorized selected world, connect every chosen portal
            // before placing repeaters on any immutable target branch.  A
            // refresher committed while powering the first branch must not
            // turn a shared portal junction into a directed obstacle for a
            // later branch.  The final frozen-branch phase below materializes
            // the exact terminal geometry after all portals are powered.
            if $FrozenSourceBranch.is_some() {
                continue;
            }

            let mut CurrentState = *$StateByNode.get(&PortalTarget).unwrap();
            let BranchContinuation = Branch
                .iter()
                .copied()
                .skip(1)
                .enumerate()
                .collect::<Vec<_>>();
            let mut BranchContinuationIncomplete = false;
            for (ContinuationIndex, Next) in BranchContinuation {
                let PhysicalNextPower = if $FrozenSourceBranch.is_some() {
                    let PhysicalNodes = $Tree
                        .union(&$FrozenReservedAccessNodes)
                        .copied()
                        .collect::<HashSet<_>>();
                    let PhysicalPowers = PropagateCanonicalRoutePower(
                        $Root,
                        &PhysicalNodes,
                        &$Repeaters,
                        &$SelfContext.Adjacency,
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
                if $Tree.contains(&Next) {
                    CurrentState = *$StateByNode.get(&Next).unwrap();
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
                let CanPlaceRepeater = CurrentState.1 != $StartDirection
                    && CurrentState.1 == Direction
                    && Direction.1 == 0
                    && CurrentState.2 <= crate::Path::PathRouting::REPEATER_TURN_HEADROOM;
                let RemainingStrength = if let Some(Power) =
                    PhysicalNextPower.filter(|Power| *Power > 0)
                {
                    Power
                } else if !$EnforceSignalStrength {
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else if CanPlaceRepeater {
                    let Some(Facing) = RepeaterFacing(CurrentState.0, Next) else {
                        return $Failure("NoPath", "NoPathGeometry", 1, 0, $ExpansionCount);
                    };
                    $Repeaters.entry(CurrentState.0).or_insert(Facing);
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else if CurrentState.2 > 1 {
                    CurrentState.2 - 1
                } else {
                    if $FrozenSourceBranch.is_none() {
                        return $Failure("NoPath", "NoRepeaterRepairPath", 1, 1, $ExpansionCount);
                    }
                    let RepairEndIndex = ContinuationIndex + 1;
                    let RepairStartIndex = RepairEndIndex.saturating_sub(
                        usize::from(crate::Path::PathRouting::REPEATER_TURN_HEADROOM) * 2,
                    );
                    let RepairBranch = &Branch[RepairStartIndex..=RepairEndIndex];
                    let RepairAllowed = $AdditionalAllowedNodes
                        .iter()
                        .copied()
                        .filter(|Candidate| {
                            RepairBranch.iter().any(|BranchNode| {
                                (Candidate.0 - BranchNode.0).abs()
                                    + (Candidate.1 - BranchNode.1).abs()
                                    + (Candidate.2 - BranchNode.2).abs()
                                    <= i32::from(crate::Path::PathRouting::REPEATER_TURN_HEADROOM)
                            })
                        })
                        .collect::<HashSet<_>>();
                    let Some((RepairBlocked, RepairEdgeCosts)) = BuildRootedTreeBlockages(
                        $SelfContext,
                        $Guide,
                        $AdditionalAllowedNodes,
                        &$BlockedNodes,
                        &$Tree,
                        &$FrozenReservedAccessNodes,
                        &$Deadline,
                    ) else {
                        return $Failure("NoPath", "NoPathGeometry", 1, 1, $ExpansionCount);
                    };
                    let mut RepeaterLaneCandidates = Vec::new();
                    let mut RepairAnchorStates = RepairBranch
                        .iter()
                        .rev()
                        .filter_map(|PositionValue| $StateByNode.get(PositionValue).copied())
                        .collect::<Vec<_>>();
                    RepairAnchorStates.dedup_by_key(|State| State.0);
                    for AnchorState in RepairAnchorStates {
                        for First in $SelfContext
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
                                || $Tree.contains(&First)
                                || $Tree.contains(&Second)
                                || $FrozenReservedAccessNodes.contains(&First)
                                || $FrozenReservedAccessNodes.contains(&Second)
                                || $ForbiddenRepeaterPositions.contains(&First)
                                || !$SelfContext
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
                        if $Deadline.Check() || $ExpansionCount >= $MaximumExpansionCount {
                            break;
                        }
                        let Some(Facing) = RepeaterFacing(First, Second) else {
                            continue;
                        };
                        let SecondState = (Second, LaneDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH);
                        let mut CandidateRepairBlocked = RepairBlocked.clone();
                        CandidateRepairBlocked.extend($Tree.iter().copied());
                        CandidateRepairBlocked.insert(First);
                        CandidateRepairBlocked.remove(&Second);
                        CandidateRepairBlocked.remove(&Next);
                        let Some(RepairResult) = FindPathFromStatesDetailedWithDeadline(
                            &$SelfContext.Adjacency,
                            None,
                            Some(&RepairAllowed),
                            &[SecondState],
                            Next,
                            $PreferredRoutingY,
                            &CandidateRepairBlocked,
                            &$Guide.NodeCosts,
                            &HashMap::new(),
                            &$Guide.ColumnCosts,
                            &RepairEdgeCosts,
                            $BendPenalty,
                            $ViaPenalty,
                            0,
                            $MaximumExpansionCount.saturating_sub($ExpansionCount),
                            true,
                            $ForbiddenRepeaterPositions,
                            &[],
                            0,
                            &$Deadline,
                        ) else {
                            continue;
                        };
                        $ExpansionCount += RepairResult.$ExpansionCount;
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
                    $ParentByNode.insert(First, AnchorState.0);
                    $ParentByNode.insert(Second, First);
                    $Tree.insert(First);
                    $Tree.insert(Second);
                    $StateByNode.insert(First, FirstState);
                    $StateByNode.insert(Second, SecondState);
                    $Repeaters.insert(First, Facing);
                    for (PositionValue, RepeaterFacingValue) in &RepairResult.RepeaterReservations {
                        $Repeaters
                            .entry(*PositionValue)
                            .or_insert_with(|| RepeaterFacingValue.clone());
                    }
                    let mut RepairedState = SecondState;
                    for GeometryState in RepairResult.StatePath.iter().skip(1) {
                        let RepairedNext = GeometryState.0;
                        if $Tree.insert(RepairedNext) {
                            $ParentByNode.insert(RepairedNext, RepairedState.0);
                        }
                        $StateByNode.insert(RepairedNext, *GeometryState);
                        RepairedState = *GeometryState;
                    }
                    let Some(RepairedState) = $StateByNode.get(&Next).copied() else {
                        return $Failure("NoPath", "NoPathGeometry", 1, 1, $ExpansionCount);
                    };
                    CurrentState = RepairedState;
                    continue;
                };
                let NextState = (Next, Direction, RemainingStrength);
                $ParentByNode.insert(Next, CurrentState.0);
                $Tree.insert(Next);
                $StateByNode.insert(Next, NextState);
                CurrentState = NextState;
            }

            if BranchContinuationIncomplete {
                continue;
            }

            let Target = CurrentState.0;
            let mut Path = vec![Target];
            let mut Cursor = Target;
            while Cursor != $Root {
                let Some(Previous) = $ParentByNode.get(&Cursor).copied() else {
                    return $Failure("NoPath", "NoPathGeometry", 0, 0, $ExpansionCount);
                };
                Path.push(Previous);
                Cursor = Previous;
            }
            Path.reverse();
            $TargetPaths.push((Target, Path));
        }
    };
}

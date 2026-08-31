macro_rules! IntegratePreparedDetailedFrozenBranches {
    (
        $SelfContext:expr,
        $FrozenTargetBranches:ident,
        $Guide:ident,
        $AdditionalAllowedNodes:ident,
        $BaseBlockedNodes:ident,
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
        $AdditionalNodeCosts:ident,
        $Root:ident,
        $StartDirection:ident,
        $Tree:ident,
        $StateByNode:ident,
        $ParentByNode:ident,
        $Repeaters:ident,
        $ExpansionCount:ident,
        $FrozenReservedAccessNodes:ident,
        $RouteFrozenTargetIntoTree:ident
    ) => {
        if $FrozenSourceBranch.is_some() {
            let mut PhysicalNodes = $Tree
                .union(&$FrozenReservedAccessNodes)
                .copied()
                .collect::<HashSet<_>>();
            let mut PhysicalPowers =
                PropagateCanonicalRoutePower($Root, &PhysicalNodes, &$Repeaters, &$SelfContext.Adjacency);
            // Protect targets only after their deterministic branch turn has
            // been processed.  A future immutable branch may be incidentally
            // powered before its own repeater decision; treating every such
            // target as already committed makes an intermediate ordering
            // state stricter than the completed tree.
            let mut RequiredPoweredTargets = HashSet::new();
            for Branch in $FrozenTargetBranches {
                let Some(Target) = Branch.last().copied() else {
                    continue;
                };
                PhysicalNodes = $Tree
                    .union(&$FrozenReservedAccessNodes)
                    .copied()
                    .collect::<HashSet<_>>();
                let (CurrentPhysicalPowers, CanonicalPowerParentByNode) =
                    PropagateCanonicalRoutePowerWithParents(
                        $Root,
                        &PhysicalNodes,
                        &$Repeaters,
                        &$SelfContext.Adjacency,
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
                    while Cursor != $Root {
                        let Some(Previous) = CanonicalPowerParentByNode.get(&Cursor).copied()
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
                        let mut CandidateTree = $Tree.clone();
                        let mut CandidateStateByNode = $StateByNode.clone();
                        let mut CandidateParentByNode = $ParentByNode.clone();
                        let mut CandidateRepeaters = $Repeaters.clone();
                        for PositionValue in &RawCombinedPath {
                            CandidateRepeaters.remove(PositionValue);
                        }
                        let mut CurrentState =
                            ($Root, $StartDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH);
                        let mut ExactCombinedPathComplete = true;
                        let mut ExactCombinedPathFailureReason = "";
                        for PathIndex in 1..CombinedPath.len() {
                            let Next = CombinedPath[PathIndex];
                            if !$SelfContext
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
                            let CurrentFacing = RepeaterInputFacing(CurrentState.0, Next);
                            let ExistingRepeaterInputFacing =
                                CandidateRepeaters.get(&CurrentState.0).cloned();
                            if ExistingRepeaterInputFacing
                                .as_ref()
                                .is_some_and(|Facing| Some(Facing) != CurrentFacing.as_ref())
                            {
                                ExactCombinedPathComplete = false;
                                ExactCombinedPathFailureReason = "existing-repeater-direction";
                                break;
                            }
                            let CanPhysicallyRefreshCurrent = CurrentState.1 != $StartDirection
                                && CurrentState.1 == Direction
                                && Direction.1 == 0
                                && CurrentFacing.is_some()
                                && !$ForbiddenRepeaterPositions.contains(&CurrentState.0);
                            let CurrentPathIndex = PathIndex - 1;
                            let NextRefreshDistance = (PathIndex
                                ..CombinedPath.len().saturating_sub(1))
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
                                        || $ForbiddenRepeaterPositions.contains(&Candidate)
                                    {
                                        return None;
                                    }
                                    let Facing = RepeaterInputFacing(Candidate, After)?;
                                    if CandidateRepeaters
                                        .get(&Candidate)
                                        .is_some_and(|Value| Value != &Facing)
                                    {
                                        return None;
                                    }
                                    Some(CandidateIndex - CurrentPathIndex)
                                })
                                .unwrap_or(CombinedPath.len() - 1 - CurrentPathIndex);
                            let MustRefreshCurrent = CurrentState.2
                                <= crate::Path::PathRouting::REPEATER_TURN_HEADROOM
                                || NextRefreshDistance >= usize::from(CurrentState.2);
                            let RefreshCurrent = ExistingRepeaterInputFacing.is_some()
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
                                .union(&$FrozenReservedAccessNodes)
                                .copied()
                                .collect::<HashSet<_>>();
                            let CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                                $Root,
                                &CandidatePhysicalNodes,
                                &CandidateRepeaters,
                                &$SelfContext.Adjacency,
                            );
                            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                                && !CandidatePhysicalPowers.contains_key(&Target)
                            {
                                eprintln!(
                                    "selected rooted target audit signal={} target={:?} target_power={:?} path={:?} path_powers={:?} path_repeaters={:?}",
                                    $DebugLabel,
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
                                $Tree = CandidateTree;
                                $StateByNode = CandidateStateByNode;
                                $ParentByNode = CandidateParentByNode;
                                $Repeaters = CandidateRepeaters;
                                PhysicalPowers = CandidatePhysicalPowers;
                                RequiredPoweredTargets.insert(Target);
                                continue;
                            }
                        } else if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                            eprintln!(
                                "selected rooted target incomplete signal={} target={:?} reason={} path={:?}",
                                $DebugLabel,
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
                            .unwrap_or($StartDirection);
                        let mut CurrentState = (Portal, IncomingDirection, CurrentPower);
                        let mut CandidateTree = $Tree.clone();
                        let mut CandidateStateByNode = $StateByNode.clone();
                        let mut CandidateParentByNode = $ParentByNode.clone();
                        let mut CandidateRepeaters = $Repeaters.clone();
                        let mut ExactBranchComplete = true;
                        for BranchIndex in StartIndex + 1..Branch.len() {
                            let Next = Branch[BranchIndex];
                            if !$SelfContext
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
                            let CanPhysicallyRefreshCurrent = CurrentState.1 != $StartDirection
                                && CurrentState.1 == Direction
                                && Direction.1 == 0
                                && !$ForbiddenRepeaterPositions.contains(&CurrentState.0);
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
                                        || $ForbiddenRepeaterPositions.contains(&Candidate)
                                    {
                                        return None;
                                    }
                                    let Facing = RepeaterInputFacing(Candidate, After)?;
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
                                <= crate::Path::PathRouting::REPEATER_TURN_HEADROOM
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
                                        && !$ForbiddenRepeaterPositions.contains(&Next)
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
                                let Some(Facing) = RepeaterInputFacing(RepeaterPosition, RepeaterAfter)
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
                                .union(&$FrozenReservedAccessNodes)
                                .copied()
                                .collect::<HashSet<_>>();
                            let CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                                $Root,
                                &CandidatePhysicalNodes,
                                &CandidateRepeaters,
                                &$SelfContext.Adjacency,
                            );
                            if CandidatePhysicalPowers.contains_key(&Target)
                                && RequiredPoweredTargets.iter().all(|RequiredTarget| {
                                    CandidatePhysicalPowers.contains_key(RequiredTarget)
                                })
                            {
                                $Tree = CandidateTree;
                                $StateByNode = CandidateStateByNode;
                                $ParentByNode = CandidateParentByNode;
                                $Repeaters = CandidateRepeaters;
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
                if let Some(LocalResult) = $RouteFrozenTargetIntoTree(
                    Branch,
                    &$Tree,
                    &$StateByNode,
                    &$Repeaters,
                    i32::from(crate::Path::PathRouting::REPEATER_TURN_HEADROOM) + 1,
                    Some(&PhysicalPowers),
                    $MaximumExpansionCount
                        .saturating_sub($ExpansionCount)
                        .div_ceil(16),
                ) {
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                        eprintln!(
                            "selected frozen target local signal={} branch={:?} status={} reason={} expansions={} state_path={:?} repeaters={:?}",
                            $DebugLabel,
                            Branch,
                            LocalResult.Status,
                            LocalResult.NoPathReason,
                            LocalResult.$ExpansionCount,
                            LocalResult.StatePath,
                            LocalResult.RepeaterReservations,
                        );
                    }
                    $ExpansionCount = $ExpansionCount.saturating_add(LocalResult.$ExpansionCount);
                    if LocalResult.Status == "Routed" {
                        let mut CandidateTree = $Tree.clone();
                        let mut CandidateStateByNode = $StateByNode.clone();
                        let mut CandidateParentByNode = $ParentByNode.clone();
                        let mut CandidateRepeaters = $Repeaters.clone();
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
                            .union(&$FrozenReservedAccessNodes)
                            .copied()
                            .collect::<HashSet<_>>();
                        let CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                            $Root,
                            &CandidatePhysicalNodes,
                            &CandidateRepeaters,
                            &$SelfContext.Adjacency,
                        );
                        if CandidatePhysicalPowers.contains_key(&Target)
                            && RequiredPoweredTargets.iter().all(|RequiredTarget| {
                                CandidatePhysicalPowers.contains_key(RequiredTarget)
                            })
                        {
                            $Tree = CandidateTree;
                            $StateByNode = CandidateStateByNode;
                            $ParentByNode = CandidateParentByNode;
                            $Repeaters = CandidateRepeaters;
                            PhysicalPowers = CandidatePhysicalPowers;
                            RequiredPoweredTargets.insert(Target);
                            continue;
                        }
                    }
                }
                PhysicalNodes = $Tree.union(&$FrozenReservedAccessNodes).copied().collect();
                PhysicalPowers =
                    PropagateCanonicalRoutePower($Root, &PhysicalNodes, &$Repeaters, &$SelfContext.Adjacency);
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
                    $SelfContext,
                    $Guide,
                    $AdditionalAllowedNodes,
                    &$BlockedNodes,
                    &$Tree,
                    &$FrozenReservedAccessNodes,
                    &$Deadline,
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
                DynamicBlocked.extend($Tree.iter().copied());
                DynamicBlocked.extend($FrozenReservedAccessNodes.iter().copied());
                let BranchNodeSet = Branch.iter().copied().collect::<HashSet<_>>();
                let IndependentPhysicalNodes = PhysicalNodes
                    .difference(&BranchNodeSet)
                    .copied()
                    .collect::<HashSet<_>>();
                let IndependentPowers = PropagateCanonicalRoutePower(
                    $Root,
                    &IndependentPhysicalNodes,
                    &$Repeaters,
                    &$SelfContext.Adjacency,
                );
                let mut PoweredStartStates = $StateByNode
                    .values()
                    .filter_map(|State| {
                        if $Repeaters.contains_key(&State.0)
                            || BranchNodeSet.contains(&State.0)
                            || $BaseBlockedNodes.contains(&State.0)
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
                        $DebugLabel,
                        Branch.first(),
                        Branch.last(),
                        Branch.len(),
                        Branch.first().and_then(|Portal| PhysicalPowers.get(Portal)),
                        PoweredStartStates.len(),
                        $Tree.len(),
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
                                $Repeaters
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
                    &$SelfContext.Adjacency,
                    $Guide.UseColumnMembership.then_some(&$Guide.AllowedColumns),
                    Some($AdditionalAllowedNodes),
                    &PoweredStartStates,
                    BypassTarget,
                    $PreferredRoutingY,
                    &DynamicBlocked,
                    &$Guide.NodeCosts,
                    &$AdditionalNodeCosts,
                    &$Guide.ColumnCosts,
                    &EdgeCosts,
                    $BendPenalty,
                    $ViaPenalty,
                    $BendPenalty.max(1),
                    $MaximumExpansionCount.saturating_sub($ExpansionCount),
                    $EnforceSignalStrength,
                    $ForbiddenRepeaterPositions,
                    &Branch[BypassTargetIndex..],
                    0,
                    &$Deadline,
                ) else {
                    continue;
                };
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected frozen target bypass signal={} status={} reason={} expansions={}",
                        $DebugLabel,
                        BypassResult.Status,
                        BypassResult.NoPathReason,
                        BypassResult.$ExpansionCount,
                    );
                }
                $ExpansionCount = $ExpansionCount.saturating_add(BypassResult.$ExpansionCount);
                if BypassResult.Status != "Routed" {
                    continue;
                }
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected frozen target bypass witness signal={} target={:?} states={:?} repeaters={:?}",
                        $DebugLabel,
                        Target,
                        BypassResult.StatePath,
                        BypassResult.RepeaterReservations,
                    );
                }
                let mut CandidateTree = $Tree.clone();
                let mut CandidateStateByNode = $StateByNode.clone();
                let mut CandidateParentByNode = $ParentByNode.clone();
                let mut CandidateRepeaters = $Repeaters.clone();
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
                    .union(&$FrozenReservedAccessNodes)
                    .copied()
                    .collect::<HashSet<_>>();
                let mut CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                    $Root,
                    &CandidatePhysicalNodes,
                    &CandidateRepeaters,
                    &$SelfContext.Adjacency,
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
                        && !$ForbiddenRepeaterPositions.contains(&SiblingRepeater)
                    {
                        if let Some(Facing) = RepeaterInputFacing(SiblingRepeater, SiblingOutput) {
                            CandidateRepeaters.insert(SiblingRepeater, Facing);
                            CandidatePhysicalPowers = PropagateCanonicalRoutePower(
                                $Root,
                                &CandidatePhysicalNodes,
                                &CandidateRepeaters,
                                &$SelfContext.Adjacency,
                            );
                        }
                    }
                }
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected frozen target bypass audit signal={} target={:?} target_power={:?} candidate_repeaters={:?}",
                        $DebugLabel,
                        Target,
                        CandidatePhysicalPowers.get(&Target),
                        CandidateRepeaters,
                    );
                    eprintln!(
                        "selected frozen target bypass required powers signal={} values={:?}",
                        $DebugLabel,
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
                        $DebugLabel,
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
                        $DebugLabel,
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
                    $Tree = CandidateTree;
                    $StateByNode = CandidateStateByNode;
                    $ParentByNode = CandidateParentByNode;
                    $Repeaters = CandidateRepeaters;
                    PhysicalPowers = CandidatePhysicalPowers;
                    RequiredPoweredTargets.insert(Target);
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                        eprintln!(
                            "selected frozen target bypass committed signal={} target={:?} power={:?}",
                            $DebugLabel,
                            Target,
                            PhysicalPowers.get(&Target),
                        );
                    }
                }
            }
            let UnpoweredFrozenBranches = $FrozenTargetBranches
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
                        $DebugLabel,
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
                    for PortalAnchor in Branch
                        .iter()
                        .copied()
                        .filter(|Value| $Tree.contains(Value) || $ParentByNode.contains_key(Value))
                    {
                        let mut Cursor = PortalAnchor;
                        let mut Seen = HashSet::new();
                        while Seen.insert(Cursor) {
                            ConflictResources.push(("RepeaterPowerPath".to_string(), Cursor));
                            if $Repeaters.contains_key(&Cursor) {
                                ConflictResources.push(("RepeaterPowerAnchor".to_string(), Cursor));
                            }
                            if Cursor == $Root {
                                break;
                            }
                            let Some(Previous) = $ParentByNode.get(&Cursor).copied() else {
                                break;
                            };
                            Cursor = Previous;
                        }
                    }
                }
                if ConflictResources.is_empty() {
                    ConflictResources.extend(
                        $Tree.iter()
                            .copied()
                            .map(|Value| ("RepeaterPowerPath".to_string(), Value)),
                    );
                }
                ConflictResources.sort_unstable();
                ConflictResources.dedup();
                let mut Result =
                    $Failure("NoPath", "NoRepeaterFinalPowerAudit", 1, 1, $ExpansionCount);
                Result.ConflictResources = ConflictResources;
                return Result;
            }
        }
    };
}

use super::*;

impl RoutingContext {
    #[allow(clippy::too_many_arguments)]
    pub(in crate::Generation) fn GenerateRouteTreeClaimAwarePreparedDetailedNative(
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
                        .filter(|PositionValue| !ForbiddenRepeaterPositions.contains(PositionValue))
                        .collect::<Vec<_>>();
                    RepeaterPositions.sort_unstable();
                    RepeaterPositions.dedup();
                    RejectedPathCount += 1;
                    if (!OrderedCutNodes.is_empty() || !RepeaterPositions.is_empty())
                        && NoGoodCount < 64
                    {
                        let SearchBlockedBeforeNoGood = SearchBlocked.clone();
                        let ForbiddenRepeatersBeforeNoGood = ForbiddenRepeaterPositions.clone();
                        for RepeaterPosition in RepeaterPositions.into_iter().rev() {
                            let mut AlternativeForbidden = ForbiddenRepeatersBeforeNoGood.clone();
                            AlternativeForbidden.insert(RepeaterPosition);
                            PendingSearchStates
                                .push((SearchBlockedBeforeNoGood.clone(), AlternativeForbidden));
                        }
                        for CutNode in OrderedCutNodes.into_iter().rev() {
                            let mut AlternativeSearchBlocked = SearchBlockedBeforeNoGood.clone();
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
                        .filter(|PositionValue| !SearchBlocked.contains(PositionValue))
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
                            let mut AlternativeForbidden = ForbiddenRepeatersBeforeNoGood.clone();
                            AlternativeForbidden.insert(RepeaterPosition);
                            PendingSearchStates
                                .push((SearchBlockedBeforeNoGood.clone(), AlternativeForbidden));
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
                            ForbiddenRepeaterPositions.insert(CycleRepeaterPositions[0]);
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
                PendingSearchStates
                    .push((AlternativeSearchBlocked, ForbiddenRepeaterPositions.clone()));
            }
            let (NextSearchBlocked, NextForbiddenRepeaterPositions) = PendingSearchStates
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

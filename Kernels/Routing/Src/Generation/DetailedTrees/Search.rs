use super::*;

impl RoutingContext {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn GenerateRouteTreeDetailedNative(
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
    ) -> RouteTreeSearchResult {
        let Ok(Deadline) =
            RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds.max(1)))
        else {
            return DetailedRouteTreeBudgetExpiredResult();
        };
        self.GenerateRouteTreeDetailedWithDeadlineNative(
            Starts,
            TargetBranches,
            AllowedNodeValues,
            BlockedNodeValues,
            PreferredColumns,
            ExternalNodeCostValues,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            MaximumExpansionCount,
            &Deadline,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(in crate::Generation) fn GenerateRouteTreeDetailedWithDeadlineNative(
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
        Deadline: &RuntimeDeadline,
    ) -> RouteTreeSearchResult {
        self.GenerateRouteTreeDetailedWithAdmissionNative(
            &Starts,
            &TargetBranches,
            &AllowedNodeValues,
            &BlockedNodeValues,
            &PreferredColumns,
            &ExternalNodeCostValues,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            MaximumExpansionCount,
            None,
            Deadline,
            false,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(in crate::Generation) fn GenerateRouteTreeDetailedWithAdmissionNative(
        &self,
        Starts: &[Position],
        TargetBranches: &[Vec<Position>],
        AllowedNodeValues: &[Position],
        BlockedNodeValues: &[Position],
        PreferredColumns: &[(i32, i32)],
        ExternalNodeCostValues: &[(Position, i32)],
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        MaximumExpansionCount: usize,
        ExpansionAdmission: Option<&RequestExpansionAdmissionV1>,
        Deadline: &RuntimeDeadline,
        RepairSelfClaims: bool,
    ) -> RouteTreeSearchResult {
        let Some(Guide) = self.PrepareDetailedRouteGuide(
            AllowedNodeValues,
            PreferredColumns,
            ExternalNodeCostValues,
            GuidePenalty,
            Deadline,
        ) else {
            return DetailedRouteTreeBudgetExpiredResult();
        };
        let mut BaseBlockedNodes = HashSet::with_capacity(BlockedNodeValues.len());
        for (Index, PositionValue) in BlockedNodeValues.iter().copied().enumerate() {
            if Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return DetailedRouteTreeBudgetExpiredResult();
            }
            BaseBlockedNodes.insert(PositionValue);
        }
        if Deadline.Check() {
            return DetailedRouteTreeBudgetExpiredResult();
        }
        let Result = self.GenerateRouteTreeDetailedPreparedWithDeadlineNative(
            Starts,
            TargetBranches,
            TargetBranches,
            &Guide,
            &HashSet::new(),
            &HashSet::new(),
            &BaseBlockedNodes,
            PreferredRoutingY,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            None,
            &HashSet::new(),
            "",
            MaximumExpansionCount,
            ExpansionAdmission,
            Deadline,
        );
        if !RepairSelfClaims || !Result.IsRouted {
            return Result;
        }
        // Required starts and ordered target suffixes cannot be no-good cuts.
        // Keep the original candidate and prepared guide so a legal route
        // consumes exactly the same admitted search work as before repair.
        let mut MandatoryWire = HashSet::new();
        for Value in Starts.iter().chain(TargetBranches.iter().flatten()) {
            if Deadline.Check() {
                let mut Expired = DetailedRouteTreeBudgetExpiredResult();
                Expired.ExpansionCount =
                    ExpansionAdmission.map_or(Result.ExpansionCount, |Value| Value.RouteCount());
                return Expired;
            }
            MandatoryWire.insert(*Value);
        }
        self.GenerateRouteTreeClaimAwarePreparedWithAdmissionNative(
            Starts,
            TargetBranches,
            TargetBranches,
            &Guide,
            &HashSet::new(),
            &BaseBlockedNodes,
            PreferredRoutingY,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            None,
            MaximumExpansionCount,
            Deadline,
            &MandatoryWire,
            &HashSet::new(),
            &HashSet::new(),
            "",
            ExpansionAdmission,
            Some(Result),
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(in crate::Generation) fn GenerateRouteTreeDetailedPreparedWithDeadlineNative(
        &self,
        Starts: &[Position],
        TargetBranches: &[Vec<Position>],
        FrozenTargetBranches: &[Vec<Position>],
        Guide: &PreparedDetailedRouteGuide,
        AdditionalAllowedNodes: &HashSet<Position>,
        UnblockedAdditionalNodes: &HashSet<Position>,
        BaseBlockedNodes: &HashSet<Position>,
        PreferredRoutingY: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        FrozenSourceBranch: Option<&[Position]>,
        ForbiddenRepeaterPositions: &HashSet<Position>,
        DebugLabel: &str,
        MaximumExpansionCount: usize,
        ExpansionAdmission: Option<&RequestExpansionAdmissionV1>,
        Deadline: &RuntimeDeadline,
    ) -> RouteTreeSearchResult {
        InitializePreparedDetailedRouteSearch!(
            self,
            Starts,
            TargetBranches,
            FrozenTargetBranches,
            Guide,
            AdditionalAllowedNodes,
            UnblockedAdditionalNodes,
            BaseBlockedNodes,
            FrozenSourceBranch,
            ForbiddenRepeaterPositions,
            DebugLabel,
            MaximumExpansionCount,
            ExpansionAdmission,
            Deadline,
            Failure,
            BlockedNodes,
            AdditionalNodeCosts,
            Root,
            StartDirection,
            RootState,
            Tree,
            StateByNode,
            ParentByNode,
            Repeaters,
            ExpansionCount,
            FrozenReservedAccessNodes,
            TargetPaths
        );
        BuildPreparedDetailedRouteClosures!(
            self,
            Starts,
            Guide,
            AdditionalAllowedNodes,
            PreferredRoutingY,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            FrozenSourceBranch,
            ForbiddenRepeaterPositions,
            DebugLabel,
            MaximumExpansionCount,
            ExpansionAdmission,
            Deadline,
            BlockedNodes,
            AdditionalNodeCosts,
            ExpansionCount,
            FrozenReservedAccessNodes,
            FrozenSourceRepairAllowedNodes,
            RouteIntoTree,
            RouteFrozenSourceIntoTree,
            RouteFrozenTargetIntoTree
        );
        IntegratePreparedDetailedSource!(
            self,
            Starts,
            TargetBranches,
            FrozenTargetBranches,
            Guide,
            AdditionalAllowedNodes,
            EnforceSignalStrength,
            FrozenSourceBranch,
            ForbiddenRepeaterPositions,
            DebugLabel,
            Deadline,
            Failure,
            BlockedNodes,
            Root,
            StartDirection,
            RootState,
            Tree,
            StateByNode,
            ParentByNode,
            Repeaters,
            ExpansionCount,
            FrozenReservedAccessNodes,
            RouteIntoTree,
            RouteFrozenSourceIntoTree,
            FrozenSourceRepairAllowedNodes,
            RetainedMandatorySourceNodes,
            FrozenSourceFrontierState,
            RootedFrozenPortalNodes,
            SourcePaths,
            TargetPaths,
            GlobalRoutingNodes,
            RetainedMandatoryTargetNodes
        );
        RoutePreparedDetailedTargets!(
            self,
            TargetBranches,
            Guide,
            AdditionalAllowedNodes,
            PreferredRoutingY,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            FrozenSourceBranch,
            ForbiddenRepeaterPositions,
            DebugLabel,
            MaximumExpansionCount,
            ExpansionAdmission,
            Deadline,
            Failure,
            BlockedNodes,
            Root,
            StartDirection,
            Tree,
            StateByNode,
            ParentByNode,
            Repeaters,
            ExpansionCount,
            FrozenReservedAccessNodes,
            RouteIntoTree,
            RouteFrozenTargetIntoTree,
            TargetPaths,
            GlobalRoutingNodes
        );
        IntegratePreparedDetailedFrozenBranches!(
            self,
            FrozenTargetBranches,
            Guide,
            AdditionalAllowedNodes,
            BaseBlockedNodes,
            PreferredRoutingY,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            FrozenSourceBranch,
            ForbiddenRepeaterPositions,
            DebugLabel,
            MaximumExpansionCount,
            ExpansionAdmission,
            Deadline,
            Failure,
            BlockedNodes,
            AdditionalNodeCosts,
            Root,
            StartDirection,
            Tree,
            StateByNode,
            ParentByNode,
            Repeaters,
            ExpansionCount,
            FrozenReservedAccessNodes,
            RouteFrozenTargetIntoTree
        );
        FinalizePreparedDetailedRoute!(
            self,
            TargetBranches,
            FrozenTargetBranches,
            Guide,
            AdditionalAllowedNodes,
            UnblockedAdditionalNodes,
            ForbiddenRepeaterPositions,
            DebugLabel,
            MaximumExpansionCount,
            ExpansionAdmission,
            Deadline,
            Failure,
            Root,
            Tree,
            StateByNode,
            ParentByNode,
            Repeaters,
            ExpansionCount,
            FrozenReservedAccessNodes,
            RetainedMandatorySourceNodes,
            SourcePaths,
            TargetPaths,
            RetainedMandatoryTargetNodes
        )
    }
}

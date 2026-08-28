"""Bootstrap phase of authoritative routing."""
from __future__ import annotations
from ..RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState, PhaseOutcome

def RunBootstrap(State: AuthoritativeRoutingState, Services: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run the Bootstrap phase against shared routing state."""
    RoutingCallStarted = Services.monotonic()
    if State.EscalationHistory:
        LastAttempt = State.EscalationHistory[-1]
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='RoutingAttempt', AffectedNets=tuple((str(Signal) for Signal in LastAttempt.get('AffectedSignals', ()))), RepairActions=(), Detail='the fixed routing attempt is terminal; automatic relaunch is disabled', Diagnostics={'Action': 'terminal-fixed-domain-incomplete', 'RejectedAutomaticAction': LastAttempt.get('Action', ''), 'Complete': False}))
    State.FrozenPreparedPortalCache = State.Resources.FrozenPreparedPortalDomainCache
    if State.FrozenPreparedPortalCache is not None:
        if State.PrepareClusterInterfaceAssignmentOnly or State.PrepareComponentRoutingProblemOnly or State.PreparePhysicalComponentAssemblyOnly:
            State.PreparedPortalCache = State.FrozenPreparedPortalCache
            State.RawPortalCache = State.FrozenPreparedPortalCache.RawPortalCache
        else:
            State.PreparedPortalCache = None
            State.RawPortalCache = None
            if not State.ClusterInterfaceFrozenReservations:
                State.ClusterInterfaceFrozenReservations = State.FrozenPreparedPortalCache.Reservations
            if not State.ClusterInterfaceFrozenPatternFingerprints:
                State.ClusterInterfaceFrozenPatternFingerprints = {Signal: Services.BuildClusterLeaseSignalPatternFingerprint(State.FrozenPreparedPortalCache.Reservations, Signal) for Signal in {Reservation.Signal for Reservation in State.FrozenPreparedPortalCache.Reservations}}
    State.HasExactPhysicalAssemblyChannels = bool(State.Resources.FrozenPhysicalComponentAssemblyPlan is not None and State.Resources.FrozenPhysicalComponentAssemblyPlan.Channels and all((Channel.ReservedPathNodes for Channel in State.Resources.FrozenPhysicalComponentAssemblyPlan.Channels)))
    if (State.FrozenPreparedPortalCache is not None or State.HasExactPhysicalAssemblyChannels) and (not State.PrepareClusterInterfaceAssignmentOnly) and (not State.PrepareComponentRoutingProblemOnly) and (not State.PreparePhysicalComponentAssemblyOnly) and (not State.ValidateClusterInterfaceForeignAccessOnly) and (State.Resources.FrozenInterfaceGlobalCandidatePlacementIdentity == id(State.Placed)) and State.Resources.FrozenInterfaceGlobalCandidateCache:
        CachedComponentSignals = frozenset() if State.HasExactPhysicalAssemblyChannels else frozenset((str(Signal) for Signal in getattr(getattr(State.Placed, 'InterClusterRoutingChannel', None), 'AffectedSignals', ())))
        State.RetainedCandidateCache = {str(Signal): tuple(Values) for Signal, Values in dict(State.Resources.FrozenInterfaceGlobalCandidateCache).items() if str(Signal) not in CachedComponentSignals}
        State.RetainedCandidateMetadata = {str(Signal): dict(Values) for Signal, Values in dict(State.Resources.FrozenInterfaceGlobalCandidateMetadata or {}).items() if str(Signal) not in CachedComponentSignals}
        State.RegenerateSignals = frozenset((*State.RegenerateSignals, *CachedComponentSignals))
    State.CallerProvidedRawPortalCache = State.RawPortalCache is not None
    if Services.RustRoutingContext is None:
        raise ValueError('authoritative routing requires the Rust router')
    if State.Deadline is None:
        State.Deadline = Services.RoutingDeadline.Start(State.Policy.RuntimeBudgetSeconds)
    State.RoutingStarted = State.SharedRoutingStarted if State.SharedRoutingStarted is not None else Services.monotonic()
    PlacementLocalRouteDiagnostics = getattr(State.Placed, 'LocalRouteDiagnostics', {}) or {}
    State.PlacementRelocationDiagnostics = PlacementLocalRouteDiagnostics.get('__PlacementRelocation__', {})
    ClusterPinBankRepairDiagnostics = PlacementLocalRouteDiagnostics.get('__ClusterPinBankRepair__', {})
    RepeaterReadyPortalRepairDiagnostics = PlacementLocalRouteDiagnostics.get('__RepeaterReadyPortalRepair__', {})
    State.RepeaterReadyPortalRepairSignals = frozenset((str(Signal) for Signal in (RepeaterReadyPortalRepairDiagnostics.get('Signals', ()) if isinstance(RepeaterReadyPortalRepairDiagnostics, dict) else ())))
    State.RepeaterReadyPortalExtensionLength = max(2, int(RepeaterReadyPortalRepairDiagnostics.get('ExtensionLength', 3))) if State.RepeaterReadyPortalRepairSignals else 3
    State.RepeaterReadyPortalMaximumExtensions = max(1, int(RepeaterReadyPortalRepairDiagnostics.get('MaximumExtensionsPerPortal', 2))) if State.RepeaterReadyPortalRepairSignals else 2
    State.ClusterPinBankRepairSignals = frozenset((str(Signal) for Signal in (ClusterPinBankRepairDiagnostics.get('Signals', ()) if isinstance(ClusterPinBankRepairDiagnostics, dict) else ())))
    State.ClusterPinBankCandidateDomainOffsets = {Signal: max(0, int(ClusterPinBankRepairDiagnostics.get('CandidateDomainOffset', 0))) for Signal in State.ClusterPinBankRepairSignals}
    State.ClusterPinBankCandidateDomainOffsets.update({str(Signal): max(0, int(Offset)) for Signal, Offset in (State.ClusterLeaseCandidateDomainOffsets or {}).items()})
    State.ClusterPinBankRepairFingerprint = str(ClusterPinBankRepairDiagnostics.get('ProfileFingerprint', '')) if isinstance(ClusterPinBankRepairDiagnostics, dict) else ''
    State.EffectiveAvoidRoutingPositionsBySignal = {str(Signal): frozenset(Positions) for Signal, Positions in (State.AvoidRoutingPositionsBySignal or {}).items()}
    State.PlacementWasRelocated = bool(State.PlacementRelocationDiagnostics)
    State.CoordinatedCandidateDiversificationSignals = frozenset((str(Signal) for Signal in (State.PlacementRelocationDiagnostics.get('CoordinatedCandidateDiversificationSignals', ()) if isinstance(State.PlacementRelocationDiagnostics, dict) else ())))
    ConfiguredCoordinatedCandidateDiversityBoost = max(0, int(State.PlacementRelocationDiagnostics.get('CoordinatedCandidateDiversityLevel', 0))) if isinstance(State.PlacementRelocationDiagnostics, dict) else 0
    State.ConfiguredCoordinatedCandidateDiversityFixedLevel = max(0, int(State.PlacementRelocationDiagnostics.get('CoordinatedCandidateDiversificationFixedLevel', 0))) if isinstance(State.PlacementRelocationDiagnostics, dict) else 0
    State.CoordinatedCandidateDiversityLevel = min(State.Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations - 1, State.ConfiguredCoordinatedCandidateDiversityFixedLevel) if State.CoordinatedCandidateDiversificationSignals and State.ConfiguredCoordinatedCandidateDiversityFixedLevel > 0 else Services.SelectEffectiveCoordinatedCandidateDiversityLevel(State.CandidateDiversityLevel, ConfiguredCoordinatedCandidateDiversityBoost, State.Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations, bool(State.CoordinatedCandidateDiversificationSignals))
    State.PlacementRecipeDiagnostics = PlacementLocalRouteDiagnostics.get('__PlacementRecipe__', {})
    State.TransactionalLeasePrescreenSignals = Services.SelectTransactionalLeasePrescreenSignals(State.PlacementRecipeDiagnostics)
    State.ExactLegalRetainedJointStateCount = Services.CountExactLegalRetainedJointStates(PlacementLocalRouteDiagnostics)
    State.JointHigherOrderConstraintCount, State.JointPairwiseConstraintCount = Services.CountJointAssignmentConstraintKinds(PlacementLocalRouteDiagnostics)
    State.JointHigherOrderConstraintSignals = Services.SelectJointHigherOrderConstraintSignals(PlacementLocalRouteDiagnostics)
    JointPairwiseConstraintSignals = Services.SelectJointPairwiseConstraintSignals(PlacementLocalRouteDiagnostics)
    State.JointAssignmentConstraintSignals = frozenset((*State.JointHigherOrderConstraintSignals, *JointPairwiseConstraintSignals))
    State.HasCumulativeAssignmentConstraints = Services.HasCumulativeJointAssignmentConstraintMaturity(PlacementLocalRouteDiagnostics)
    PlacementTopologyDemandDiagnostics = PlacementLocalRouteDiagnostics.get('__TopologyDemandProfile__', {})
    State.TopologyRequiresJointPortfolio = bool(PlacementTopologyDemandDiagnostics.get('RequiresJointPortfolio', False)) if isinstance(PlacementTopologyDemandDiagnostics, dict) else False
    DenseBoundaryLeaseInterface = sum((1 + len(tuple(getattr(Request, 'TargetTerminals', ()))) for Request in getattr(State.Placed, 'ClusterBoundaryLeaseRequests', ()))) >= 16
    State.TopologyRequiresJointPortfolio = State.TopologyRequiresJointPortfolio or DenseBoundaryLeaseInterface
    PlacementAssignmentCutDiagnostics = State.PlacementRelocationDiagnostics.get('AssignmentCut', {}) if isinstance(State.PlacementRelocationDiagnostics, dict) else {}
    CurrentTopologyCutSignals = frozenset((str(Signal) for Signal in (PlacementAssignmentCutDiagnostics.get('ConflictSignals', ()) if isinstance(PlacementAssignmentCutDiagnostics, dict) else ())))
    RequiredTopologyRelocationSignals = frozenset((str(Signal) for Signal in (State.PlacementRelocationDiagnostics.get('RequiredSignals', ()) if isinstance(State.PlacementRelocationDiagnostics, dict) else ())))
    State.PriorityInterfaceCutSignals = CurrentTopologyCutSignals or RequiredTopologyRelocationSignals if State.TopologyRequiresJointPortfolio else frozenset()
    State.ApplyTopologyPressurePortfolioStagedProof = Services.ShouldStageTopologyPressureJointPortfolio(State.ExactLegalRetainedJointStateCount, State.TopologyRequiresJointPortfolio)
    State.ApplyMaturePortfolioSearchCaps = Services.ShouldCapMatureCumulativeJointPortfolio(State.PlacementWasRelocated, State.ExactLegalRetainedJointStateCount, State.HasCumulativeAssignmentConstraints)
    State.ApplyStagedPortfolioProof = State.ApplyMaturePortfolioSearchCaps or State.ApplyTopologyPressurePortfolioStagedProof
    if State.RequireCompleteClusterInterfaceDomain:
        State.ApplyMaturePortfolioSearchCaps = False
        State.ApplyStagedPortfolioProof = False
    PlacementWasBroadlyRelocated = isinstance(State.PlacementRelocationDiagnostics, dict) and (len(State.PlacementRelocationDiagnostics.get('PrioritySignals', ())) >= 3 or len(State.PlacementRelocationDiagnostics.get('Clusters', ())) > 3)
    State.StageTimings: dict[str, float] = {}
    EscalationState = (State.Policy.QualityTarget, State.AdaptiveLayerFloor if State.AdaptiveLayerFloor is not None else 0, State.ReservationVariant, State.LaneDiversityLevel, State.CandidateDiversityLevel, bool(State.Policy.AdaptiveRouting.Enabled), bool(State.SkipStrictPortalReservation), bool(State.Policy.GlobalRouting.EnableCapacityAwareGuides), tuple(sorted(State.LocalClaimReleaseHistory)), tuple(sorted(State.RegenerateSignals)), tuple(sorted(State.AvoidRoutingPositions)), tuple(((Signal, tuple(sorted(Positions))) for Signal, Positions in sorted(State.EffectiveAvoidRoutingPositionsBySignal.items()))), tuple(sorted(State.CoordinatedCandidateDiversificationSignals)), State.CoordinatedCandidateDiversityLevel, tuple(sorted(State.ClusterPinBankCandidateDomainOffsets.items())), tuple(((Nogood.Signal, Nogood.PatternFingerprint, Nogood.CandidateFailureFingerprint) for Nogood in State.ClusterLeaseCandidateRealizabilityNogoods)), tuple(sorted(State.PriorityInterfaceCutSignals)))
    State.StageCount = 6
    State.WorkTelemetry: dict[str, object] = {'SignalCount': 0, 'TerminalCount': 0, 'PortalRequestCount': 0, 'PortalTargetCount': 0, 'RouteTreeRequestCount': 0, 'CandidateDiversityLevel': State.CandidateDiversityLevel, 'ReservationVariant': State.ReservationVariant, 'LaneDiversityLevel': State.LaneDiversityLevel, 'CoordinatedCandidateDiversificationSignals': sorted(State.CoordinatedCandidateDiversificationSignals), 'CoordinatedCandidateDiversityLevel': State.CoordinatedCandidateDiversityLevel, 'PriorityInterfaceCutSignals': sorted(State.PriorityInterfaceCutSignals), 'ConfiguredCoordinatedCandidateDiversityBoost': ConfiguredCoordinatedCandidateDiversityBoost, 'ExactLegalRetainedJointStateCount': State.ExactLegalRetainedJointStateCount, 'JointHigherOrderConstraintCount': State.JointHigherOrderConstraintCount, 'JointPairwiseConstraintCount': State.JointPairwiseConstraintCount, 'JointHigherOrderConstraintSignals': sorted(State.JointHigherOrderConstraintSignals), 'JointPairwiseConstraintSignals': sorted(JointPairwiseConstraintSignals), 'HasCumulativeAssignmentConstraints': State.HasCumulativeAssignmentConstraints, 'ApplyMaturePortfolioSearchCaps': State.ApplyMaturePortfolioSearchCaps, 'ApplyTopologyPressurePortfolioStagedProof': State.ApplyTopologyPressurePortfolioStagedProof, 'ApplyStagedPortfolioProof': State.ApplyStagedPortfolioProof, 'ClusterBoundaryLeaseReservationVariant': State.ReservationVariant, 'ClusterPinBankRepair': {'Enabled': bool(State.ClusterPinBankCandidateDomainOffsets), 'Signals': sorted(State.ClusterPinBankRepairSignals), 'CandidateDomainOffsets': dict(sorted(State.ClusterPinBankCandidateDomainOffsets.items())), 'ProfileFingerprint': State.ClusterPinBankRepairFingerprint}}
    ExistingLocalClaimRelease = (getattr(State.Placed, 'LocalRouteDiagnostics', {}) or {}).get('PreRoutingLocalClaimRelease', {})
    if ExistingLocalClaimRelease:
        State.WorkTelemetry['LocalClaimReleasePreScreen'] = {**dict(ExistingLocalClaimRelease), 'RawPortalCacheReuseRequested': True}
    State.AdaptiveExpiresAt = State.Deadline.ExpiresAt

    def CurrentRuntimeBudgetDiagnostics(AdditionalDiagnostics: dict[str, object] | None=None) -> dict[str, object]:
        Diagnostics = dict(State.WorkTelemetry)
        Diagnostics.update({'FixedRoutingControls': {'PortalMode': 'unreserved' if State.SkipStrictPortalReservation else 'reserved', 'ReservationVariant': State.ReservationVariant, 'LaneDiversityLevel': State.LaneDiversityLevel, 'CandidateDiversityLevel': State.CandidateDiversityLevel, 'ConfiguredLayerFloor': max(0, State.AdaptiveLayerFloor or 0)}, 'StageTimingsSeconds': {Stage: round(Seconds, 6) for Stage, Seconds in State.StageTimings.items()}, 'Deadline': State.Deadline.ToDictionary()})
        Diagnostics.update(AdditionalDiagnostics or {})
        return Diagnostics
    State.CurrentRuntimeBudgetDiagnostics = CurrentRuntimeBudgetDiagnostics

    def StructuredRoutingStageError(Failure: RoutingFailure) -> RoutingStageError:
        """Attach complete measured work telemetry to every typed failure."""
        return Services.BuildTelemetryRoutingStageError(Failure, State.CurrentRuntimeBudgetDiagnostics(dict(Failure.Diagnostics or {})))
    State.StructuredRoutingStageError = StructuredRoutingStageError

    def PlanNegotiatedWithTelemetry(*Arguments: object, **KeywordArguments: object) -> NegotiatedRoutePlan:
        """Preserve planner telemetry when a nested negotiated pass fails."""
        try:
            return Services.PlanNegotiatedRouteTrees(*Arguments, **KeywordArguments)
        except Services.RoutingStageError as Error:
            raise State.StructuredRoutingStageError(Error.Failure) from Error
    State.PlanNegotiatedWithTelemetry = PlanNegotiatedWithTelemetry
    if EscalationState in State.EscalationStates:
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.Stagnated, Stage='RoutingEscalation', Detail='routing escalation requested a previously evaluated control state; returning to placement without repeating work', Diagnostics={'EscalationStates': [Value for Value in State.EscalationStates], 'EscalationState': EscalationState}))
    State.EscalationStates = (*State.EscalationStates, EscalationState)

    def CheckRuntimeBudget(Stage: str, AdditionalDiagnostics: dict[str, object] | None=None) -> None:
        Current = Services.monotonic()
        if Current < State.Deadline.ExpiresAt and Current < State.AdaptiveExpiresAt:
            return
        Services.EnforceRoutingRuntimeLimit(Deadline=State.Deadline, AdaptiveStartedAt=State.RoutingStarted, AdaptiveExpiresAt=State.AdaptiveExpiresAt, Stage=Stage, Diagnostics=State.CurrentRuntimeBudgetDiagnostics(AdditionalDiagnostics))
    State.CheckRuntimeBudget = CheckRuntimeBudget
    if State.ProgressCallback is not None:
        State.ProgressCallback(0, State.StageCount)
    DisableLocalBaseClaims = bool(Services.os.environ.get('RCS_DISABLE_LOCAL_BASE_CLAIMS')) or bool(Services.os.environ.get('RCS_DISABLE_LOCAL_CLAIMS'))
    State.AllLocalClaims = tuple(getattr(State.Placed, 'LocalRouteClaims', ()) or ())
    State.SignalTargets = Services._CollectSignalTargets(State.Placed)
    State.LocalClaimsBySignal: dict[str, tuple[Services.LocalRouteClaim, ...]] = Services.defaultdict(tuple)
    for Claim in State.AllLocalClaims:
        State.LocalClaimsBySignal[Claim.Signal] = (*State.LocalClaimsBySignal[Claim.Signal], Claim)
    State.FrozenComponentClaims = tuple((Claim for Claim in State.AllLocalClaims if int(getattr(Claim, 'ClusterId', 0)) < 0))
    State.HasRoutedComponentTemplate = bool(getattr(State.Placed, 'RoutedComponentTemplates', ()))
    State.RoutedComponentForeignEscapeSignals = frozenset((Claim.Signal for Claim in State.FrozenComponentClaims if int(getattr(Claim, 'ClusterId', 0)) == -2))
    State.LocalClaims = Services.SelectAuthoritativeBaseClaims(State.AllLocalClaims, DisableLocalBaseClaims)
    ProfilePlacement = State.Placed
    if State.Resources.PreparingPhysicalComponentGlobalChannels:
        ProfilePlacement = Services.replace(State.Placed, LocalRouteClaims=tuple((Claim for Claim in State.AllLocalClaims if int(getattr(Claim, 'ClusterId', 0)) >= 0)))
    State.Profiles = Services.BuildNetRoutingProfiles(ProfilePlacement, AccessLength=State.Policy.Placement.PinEscapeLength)
    State.WholeDesignProfiles = dict(State.Profiles)
    State.PhysicalAssemblyPlan = State.Resources.FrozenPhysicalComponentAssemblyPlan
    State.FrozenPostClosurePortalHandoffApplied = False
    if State.PhysicalAssemblyPlan is not None:
        State.PhysicalProblem = State.Resources.PreparedComponentRoutingProblem or State.Resources.PreparedPhysicalComponentUnboundProblem
        if State.PhysicalProblem is None:
            raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentGlobalPlanning', Detail='physical global planning has an assembly plan but no bound component problem'))
        if State.PhysicalProblem.PhysicalAssemblyPlan != State.PhysicalAssemblyPlan:
            raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentGlobalPlanning', Detail='physical global planning requires the component problem to bind the exact assembly plan', Diagnostics={'PhysicalAssemblyPlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint, 'ProblemPhysicalAssemblyPlanFingerprint': str(getattr(State.PhysicalProblem.PhysicalAssemblyPlan, 'PlanFingerprint', '')), 'ImplicitForeignTransitDomainCount': 0}))
        State.Profiles = Services.ApplyPhysicalComponentAssemblyGlobalProfiles(State.Profiles, State.PhysicalProblem, State.PhysicalAssemblyPlan)
        if State.Resources.PreparingPhysicalComponentGlobalChannels:
            ExactGlobalSignals = frozenset(State.Resources.PhysicalComponentExactGlobalChannelSignals)
            MissingExactGlobalSignals = tuple(sorted(ExactGlobalSignals - set(State.Profiles)))
            if MissingExactGlobalSignals:
                raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentGlobalPlanning', AffectedNets=MissingExactGlobalSignals, Detail='the physical exact-global cut references signals outside the frozen assembly routing profile'))
            State.Profiles = {Signal: Profile for Signal, Profile in State.Profiles.items() if Signal in ExactGlobalSignals}
            State.WorkTelemetry['PhysicalComponentExactGlobalCut'] = {'SignalCount': len(ExactGlobalSignals), 'Signals': sorted(ExactGlobalSignals), 'OrdinaryDetailedRoutingDeferred': True}
        if State.Resources.PreparingPhysicalComponentGlobalChannels:
            FrozenPostClosurePortalHandoffTelemetry = Services.BuildFrozenPostClosurePortalHandoffTelemetry(State.Resources, State.Resources.PreparedPhysicalComponentPortFactorDomain, State.PhysicalAssemblyPlan)
            State.RawPortalCache = State.Resources.FrozenPhysicalComponentPostClosurePortalHandoff.RawPortalGeometryCache
            State.FrozenPostClosurePortalHandoffApplied = True
            State.WorkTelemetry['FrozenPostClosurePortalHandoff'] = FrozenPostClosurePortalHandoffTelemetry
    else:
        State.Profiles = Services.ApplyRoutedComponentGlobalProfiles(State.Placed, State.Profiles)
    BoundaryLeaseRequests = tuple(getattr(State.Placed, 'ClusterBoundaryLeaseRequests', ()) or ())
    State.BoundaryLeaseSignals = frozenset((str(Request.Signal) for Request in BoundaryLeaseRequests if str(Request.Signal) in State.Profiles))
    State.CompleteClusterInterfaceAccess = bool(getattr(State.Placed, 'CompleteClusterInterfaceAccess', False))
    State.InterClusterChannel = getattr(State.Placed, 'InterClusterRoutingChannel', None)
    State.PlacementAccessFabric = getattr(State.Placed, 'PlacementAccessFabric', None)
    State.PlacementAccessAssignment = getattr(State.Placed, 'PlacementAccessAssignment', None)
    SelectedPlacementAccessStubIndices = {(str(Signal), tuple(Terminal)): int(StubIndex) for Signal, Terminal, StubIndex in getattr(State.PlacementAccessAssignment, 'SelectedStubIndices', ())}
    State.PlacementAccessDomains = {}
    for Domain in getattr(State.PlacementAccessFabric, 'TerminalDomains', ()):
        DomainIdentity = (str(Domain.Signal), tuple(Domain.Terminal))
        SelectedStubIndex = SelectedPlacementAccessStubIndices.get(DomainIdentity)
        State.PlacementAccessDomains[DomainIdentity] = Services.replace(Domain, EscapeStubs=(Domain.EscapeStubs[SelectedStubIndex],)) if SelectedStubIndex is not None else Domain
    _AccessContractMinimumX, _AccessContractMaximumX, _AccessContractMinimumZ, _AccessContractMaximumZ, State.PlacementAccessContractPositions, State.PlacementAccessOuterBounds = Services.ResolvePlacementAccessFabricRegionContract(0, 0, 0, 0, State.PlacementAccessFabric, State.PlacementAccessDomains)
    State.PlacementAccessContractFingerprint = Services.BuildStableFingerprint(('placement-access-fabric-region-v1', str(getattr(State.PlacementAccessFabric, 'FabricFingerprint', '')), State.PlacementAccessOuterBounds, tuple(sorted(State.PlacementAccessContractPositions))))
    DeclaredInterClusterChannelSignals = frozenset((str(Signal) for Signal in getattr(State.InterClusterChannel, 'AffectedSignals', ())))
    AffectedClusterSet = frozenset((int(Cluster) for Cluster in getattr(State.InterClusterChannel, 'AffectedClusters', ())))
    State.InterClusterChannelSignals = DeclaredInterClusterChannelSignals if State.HasRoutedComponentTemplate else DeclaredInterClusterChannelSignals if not (State.CompleteClusterInterfaceAccess and AffectedClusterSet) else Services.SelectComponentIncidentSignals(BoundaryLeaseRequests, AffectedClusterSet, State.Profiles)
    State.LeaseOwnershipSignals = Services.SelectClusterLeaseOwnershipSignals(State.Profiles, State.BoundaryLeaseSignals, State.CompleteClusterInterfaceAccess, State.InterClusterChannelSignals)
    ComponentBoundaryLeaseRequests = tuple((Request for Request in BoundaryLeaseRequests if str(Request.Signal) in State.LeaseOwnershipSignals and (not AffectedClusterSet or (int(Request.SourceCluster) in AffectedClusterSet and int(Request.TargetCluster) in AffectedClusterSet))))
    ComponentBoundaryTerminalPairs = frozenset(((str(Request.Signal), tuple(Terminal)) for Request in ComponentBoundaryLeaseRequests for Terminal in (*((Request.SourceTerminal,) if Request.SourceTerminal is not None else ()), *Request.TargetTerminals)))
    if State.PrepareComponentRoutingProblemOnly and State.RequireCompleteClusterInterfaceDomain and (State.InterClusterChannel is not None) and State.InterClusterChannelSignals:
        UnrestrictedComponentPreparationProfileCount = len(State.Profiles)
        State.Profiles = Services.SelectComponentPreparationProfiles(State.Profiles, State.InterClusterChannelSignals, State.InterClusterChannel, State.LocalClaims, GuideExpansion=State.Policy.DetailedRouting.GuideExpansion, TrackPitch=State.Technology.TrackPitch)
        State.LeaseOwnershipSignals = frozenset((Signal for Signal in State.LeaseOwnershipSignals if Signal in State.Profiles))
        State.BoundaryLeaseSignals = frozenset((Signal for Signal in State.BoundaryLeaseSignals if Signal in State.Profiles))
        ComponentBoundaryTerminalPairs = frozenset((Pair for Pair in ComponentBoundaryTerminalPairs if Pair[0] in State.Profiles))
        State.WorkTelemetry['ComponentPreparationProfileFilter'] = {'UnrestrictedProfileCount': UnrestrictedComponentPreparationProfileCount, 'RetainedProfileCount': len(State.Profiles), 'ComponentSignalCount': len(State.InterClusterChannelSignals), 'PassiveForeignProfileCount': len(set(State.Profiles) - State.InterClusterChannelSignals), 'InteractionRadius': State.Policy.DetailedRouting.GuideExpansion + State.Technology.TrackPitch + 3}
    if State.PrepareClusterInterfaceAssignmentOnly and State.CompleteClusterInterfaceAccess and State.InterClusterChannelSignals:
        RestrictedProfiles = {}
        for Signal in sorted(State.LeaseOwnershipSignals):
            Profile = State.Profiles[Signal]
            Terminals = tuple(sorted((Terminal for CandidateSignal, Terminal in ComponentBoundaryTerminalPairs if CandidateSignal == Signal)))
            if not Terminals:
                continue
            Root = Profile.Root if Profile.Root in Terminals else Terminals[0]

            def AccessPath(Terminal: Position3) -> tuple[Position3, ...]:
                if Terminal == Profile.Root:
                    return Profile.SourceAccessPath
                return Profile.TargetAccessPaths.get(Terminal, (Terminal,))
            Targets = tuple((Terminal for Terminal in Terminals if Terminal != Root))
            RestrictedProfiles[Signal] = Services.replace(Profile, Root=Root, Targets=Targets, Span=max((abs(Terminal[0] - Root[0]) + abs(Terminal[2] - Root[2]) for Terminal in Targets)) if Targets else 0, Fanout=len(Targets), SourceAccessPath=AccessPath(Root), TargetAccessPaths={Terminal: AccessPath(Terminal) for Terminal in Targets})
        State.Profiles = RestrictedProfiles
        State.LeaseOwnershipSignals = frozenset(State.Profiles)
        State.BoundaryLeaseSignals = frozenset((Signal for Signal in State.BoundaryLeaseSignals if Signal in State.Profiles))
        ComponentBoundaryTerminalPairs = frozenset((Pair for Pair in ComponentBoundaryTerminalPairs if Pair[0] in State.Profiles))
    State.BoundaryLeaseTerminalPairs = ComponentBoundaryTerminalPairs if State.CompleteClusterInterfaceAccess else frozenset(((str(Request.Signal), tuple(Terminal)) for Request in BoundaryLeaseRequests for Terminal in (*((Request.SourceTerminal,) if Request.SourceTerminal is not None else ()), *Request.TargetTerminals) if str(Request.Signal) in State.Profiles))
    State.WorkTelemetry['ClusterBoundaryLeases'] = {'Enabled': bool(State.BoundaryLeaseSignals), 'LeaseExtent': 'complete-pin-access-to-routing-track' if State.CompleteClusterInterfaceAccess else 'first-segment', 'CompleteClusterInterfaceAccess': State.CompleteClusterInterfaceAccess, 'OwnershipScope': 'dense-component' if State.CompleteClusterInterfaceAccess and State.InterClusterChannelSignals else 'boundary-requests', 'DenseComponentSignals': sorted(State.InterClusterChannelSignals), 'SignalCount': len(State.LeaseOwnershipSignals), 'TerminalCount': len(State.BoundaryLeaseTerminalPairs), 'Signals': sorted(State.LeaseOwnershipSignals), 'Requests': [Request.ToDictionary() for Request in (ComponentBoundaryLeaseRequests if State.CompleteClusterInterfaceAccess else BoundaryLeaseRequests) if hasattr(Request, 'ToDictionary')]}
    State.WorkTelemetry['InterClusterRoutingChannel'] = State.InterClusterChannel.ToDictionary() if State.InterClusterChannel is not None else {'Enabled': False}
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: profile fingerprint={Services.BuildStableFingerprint(tuple(((Signal, Profile.Root, Profile.Targets, Profile.SourceAccessPath, tuple(sorted(Profile.TargetAccessPaths.items()))) for Signal, Profile in sorted(State.Profiles.items()))))} pin_escape_length={State.Policy.Placement.PinEscapeLength} local_claims={len(State.AllLocalClaims)} resource_identity={id(State.Resources)}', flush=True)
    State.UseNegotiatedRouting = Services.ShouldUseNegotiatedRouting(State.Policy, len(State.Profiles))
    AllowRelocatedStarvationLaneRetry = Services.ShouldRetryRelocatedCandidateStarvation(State.PlacementWasRelocated, str(State.PlacementRecipeDiagnostics.get('SourceGenerator')) if isinstance(State.PlacementRecipeDiagnostics, dict) and State.PlacementRecipeDiagnostics.get('SourceGenerator') is not None else None, int(State.PlacementRelocationDiagnostics.get('Variant', 0)) if isinstance(State.PlacementRelocationDiagnostics, dict) else 0, int(State.PlacementRecipeDiagnostics.get('RoutingSpacing', 0)) if isinstance(State.PlacementRecipeDiagnostics, dict) else 0, State.UseNegotiatedRouting, len(State.Profiles), len(State.PlacementRelocationDiagnostics.get('PrioritySignals', ())) if isinstance(State.PlacementRelocationDiagnostics, dict) else 0)
    State.UseNegotiatedPortalDomain = State.Policy.NegotiatedRouting.Enabled and len(State.Profiles) > 32
    State.Demand = Services.EstimateRoutingDemand(State.Placed, State.Profiles)
    State.AdaptiveBudget = Services.DeriveRoutingBudget(State.Demand, State.Policy, State.Technology)
    State.AdaptiveExpiresAt = min(State.Deadline.ExpiresAt, State.RoutingStarted + State.AdaptiveBudget.RuntimeSeconds)
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: effective routing deadline remaining={State.Deadline.RemainingSeconds():.3f}s adaptive_remaining={max(0.0, State.AdaptiveExpiresAt - Services.monotonic()):.3f}s shared_start_age={Services.monotonic() - State.RoutingStarted:.3f}s', flush=True)
    State.CheckRuntimeBudget('RoutingBudget')
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: adaptive budget candidates={State.AdaptiveBudget.ClusterCellCeiling} layers={State.AdaptiveBudget.LayerCount} portals_per_terminal={State.AdaptiveBudget.PortalsPerTerminal} lanes={State.AdaptiveBudget.LaneCount} candidates_per_net={State.AdaptiveBudget.CandidatesPerNet} candidate_expansions_per_net={State.AdaptiveBudget.CandidateExpansionsPerNet} assignment_expansions={State.AdaptiveBudget.AssignmentExpansions} runtime_seconds={State.AdaptiveBudget.RuntimeSeconds:.3f}', flush=True)
    State.WorkTelemetry['SignalCount'] = len(State.Profiles)
    State.WorkTelemetry['TerminalCount'] = State.Demand.TerminalCount
    if State.LocalClaims:
        Services.ValidateLocalRouteClaims(State.Resources.ResourceGraph, State.LocalClaims)
    State.PreRouteLocalClaimChoicesBySignal, PreRouteLocalClaimChoiceRejections = Services.BuildPreRouteLocalClaimChoices(tuple(getattr(State.Placed, 'DerivedLocalRouteClaims', ()) or ()), State.Profiles, State.Resources.ResourceGraph)
    State.PreRouteLocalClaimChoices = tuple((Choice for Signal in sorted(State.PreRouteLocalClaimChoicesBySignal) for Choice in State.PreRouteLocalClaimChoicesBySignal[Signal]))
    State.PreRouteLocalClaimChoiceById = {Choice.ChoiceId: Choice for Choice in State.PreRouteLocalClaimChoices}
    State.PreRouteLocalClaimDomainFingerprint = Services.BuildStableFingerprint({'Choices': [(Choice.Signal, Choice.ChoiceId, Choice.ClaimFingerprint) for Choice in State.PreRouteLocalClaimChoices], 'Rejections': PreRouteLocalClaimChoiceRejections})
    if State.PreRouteLocalClaimChoices or PreRouteLocalClaimChoiceRejections:
        State.WorkTelemetry['PreRouteLocalClaimChoices'] = {'DomainFingerprint': State.PreRouteLocalClaimDomainFingerprint, 'Choices': [Choice.ToDictionary() for Choice in State.PreRouteLocalClaimChoices], 'Rejections': list(PreRouteLocalClaimChoiceRejections), 'Complete': True}
    if not State.Profiles:
        return PhaseOutcome(Returned=True, Value=Services.RoutedDesign(Module=State.Placed.Module, PlacedGates=State.Placed.PlacedGates, Wires=[], Supports=[], Repeaters={}, NetWires={}, ZeroResourceConflicts=True))
    return PhaseOutcome()

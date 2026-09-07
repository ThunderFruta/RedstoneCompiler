"""CandidatePreparation phase of authoritative routing."""
from __future__ import annotations
from ..RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState, PhaseOutcome

def BuildProtectedRoutingNodesBySignal(
    Profiles,
    LocalClaimsBySignal,
    SelectedPinAccessWitness=None,
):
    """Collect every fixed node that must constrain candidate generation.

    A signal omitted from detailed routing can still own placement-selected
    access material through a frozen/local tree.  Keep that material in this
    projection so another signal does not generate candidates through it and
    then fail immediately at the exact base-claim assignment boundary.
    """
    SelectedAccessNodesBySignal = {
        str(Signal): frozenset(Claims.WireCells)
        for Signal, Claims in getattr(
            SelectedPinAccessWitness,
            "ClaimsBySignal",
            (),
        )
    }
    Signals = tuple(sorted({
        *map(str, Profiles),
        *map(str, LocalClaimsBySignal),
        *SelectedAccessNodesBySignal,
    }))
    Result = {}
    for Signal in Signals:
        Profile = Profiles.get(Signal)
        Result[Signal] = frozenset({
            *(
                Profile.SourceAccessPath
                if Profile is not None
                else ()
            ),
            *(
                tuple(
                    Position
                    for Path in Profile.TargetAccessPaths.values()
                    for Position in Path
                )
                if Profile is not None
                else ()
            ),
            *(
                Position
                for Claim in LocalClaimsBySignal.get(Signal, ())
                for Position in Claim.Nodes
            ),
            *SelectedAccessNodesBySignal.get(Signal, ()),
        })
    return Result


def BuildForeignSelectedPinAccessClaimsBySignal(
    Signals,
    SelectedPinAccessWitness=None,
):
    """Bind each routed signal to every foreign selected-access claim."""
    SelectedClaimsBySignal = {
        str(Signal): Claims
        for Signal, Claims in getattr(
            SelectedPinAccessWitness,
            "ClaimsBySignal",
            (),
        )
    }
    return {
        str(Signal): tuple(
            (Owner, Claims)
            for Owner, Claims in sorted(SelectedClaimsBySignal.items())
            if Owner != str(Signal)
        )
        for Signal in sorted(map(str, Signals))
    }


def FindForeignSelectedPinAccessConflictSignals(
    Signal,
    Claims,
    ForeignClaimsBySignal,
    ConflictPredicate,
):
    """Return exact immutable access owners conflicting with one claim set."""
    return tuple(
        Owner
        for Owner, ForeignClaims in ForeignClaimsBySignal.get(
            str(Signal),
            (),
        )
        if ConflictPredicate(Claims, ForeignClaims)
    )

def RunCandidatePreparation(State: AuthoritativeRoutingState, Services: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run the CandidatePreparation phase against shared routing state."""
    State.CandidateRequestCount = 0
    State.RouteTreeNativeDeadlineExceeded = False
    State.PhysicalDescriptorOwnerByRequestId: dict[int, tuple[str, str]] = {}
    State.CompletedPhysicalDescriptorFingerprintsBySignal: dict[str, set[str]] = Services.defaultdict(set)
    State.NegotiatedPlan: Services.NegotiatedRoutePlan | None = None
    if State.UseNegotiatedRouting:
        State.RetainedCandidateCache = None
        State.RetainedCandidateMetadata = None
        State.PriorCandidateCache = None
        State.PriorCandidateMetadata = None

    def GenerateRouteTreesWithDeadline(Requests: list[tuple[Any, ...]]) -> list[Any]:
        if State.RouteTreeNativeDeadlineExceeded:
            return [None] * len(Requests)
        if not State.RouteTreeNativeDeadlineExceeded:
            State.CheckRuntimeBudget('Candidate')
        MaterializedRequests: list[tuple[Services.Any, ...]] = []
        MaterializedRequestIndexes: list[int] = []
        for RequestIndex, Request in enumerate(Requests):
            MaterializedRequest = Request.Materialize() if isinstance(Request, Services.LazyCandidateRouteRequest) else Request
            if MaterializedRequest is None:
                continue
            MaterializedRequestIndexes.append(RequestIndex)
            MaterializedRequests.append(MaterializedRequest)
        if State.Resources.PreparingPhysicalComponentGlobalChannels:
            LazyDomainTelemetry = State.WorkTelemetry.setdefault('PhysicalGlobalLazyCandidateRequestDomain', {'ConsumedDescriptorCount': 0, 'MaterializedPayloadCount': 0, 'RejectedBeforeNativeCount': 0})
            LazyDomainTelemetry['ConsumedDescriptorCount'] = int(LazyDomainTelemetry['ConsumedDescriptorCount']) + len(Requests)
            LazyDomainTelemetry['MaterializedPayloadCount'] = int(LazyDomainTelemetry['MaterializedPayloadCount']) + len(MaterializedRequests)
            LazyDomainTelemetry['RejectedBeforeNativeCount'] = int(LazyDomainTelemetry['RejectedBeforeNativeCount']) + (len(Requests) - len(MaterializedRequests))
        if not MaterializedRequests:
            for Request in Requests:
                Owner = State.PhysicalDescriptorOwnerByRequestId.get(id(Request))
                if Owner is not None:
                    State.CompletedPhysicalDescriptorFingerprintsBySignal[Owner[0]].add(Owner[1])
            return [None] * len(Requests)
        PhysicalRequestCache = State.Resources.PhysicalGlobalRouteTreeResultCache if State.PhysicalAssemblyPlan is not None else None
        PhysicalResourceGraphFingerprint = str(getattr(State.PhysicalAssemblyPlan, 'ResourceGraphFingerprint', '')) if State.PhysicalAssemblyPlan is not None else ''
        if State.PhysicalAssemblyPlan is not None and (not PhysicalResourceGraphFingerprint):
            PhysicalResourceGraphFingerprint = Services.BuildStableFingerprint((getattr(State.Resources.ResourceGraph, 'GraphVersion', ''), len(getattr(State.Resources.ResourceGraph, 'Nodes', ())), len(getattr(State.Resources.ResourceGraph, 'Edges', ()))))
        PhysicalTechnologyFingerprint = str(getattr(State.PhysicalAssemblyPlan, 'TechnologyFingerprint', '')) if State.PhysicalAssemblyPlan is not None else ''
        if State.PhysicalAssemblyPlan is not None and (not PhysicalTechnologyFingerprint):
            PhysicalTechnologyFingerprint = Services.BuildStableFingerprint((getattr(State.Technology, 'TechnologyVersion', ''), repr(State.Technology)))
        RequestCacheKeys = [Services.BuildPhysicalGlobalRouteTreeResultCacheKey(Request, Services.BuildStableFingerprint((PhysicalResourceGraphFingerprint, State.PhysicalExteriorRegionFingerprint)), PhysicalTechnologyFingerprint) for Request in MaterializedRequests] if PhysicalRequestCache is not None else []
        MissingByKey: dict[str, tuple[Services.Any, ...]] = {}
        CachedValuesByKey: dict[str, Services.Any] = {}
        if PhysicalRequestCache is not None:
            for Key, Request in zip(RequestCacheKeys, MaterializedRequests):
                if Key in PhysicalRequestCache:
                    CachedValuesByKey[Key] = Services.TouchPhysicalGlobalRouteTreeResult(PhysicalRequestCache, Key)
                else:
                    MissingByKey.setdefault(Key, Request)
            State.WorkTelemetry['PhysicalGlobalRouteTreeResultCache'] = {'RequestCount': len(MaterializedRequests), 'DescriptorCount': len(Requests), 'RejectedBeforeNativeCount': len(Requests) - len(MaterializedRequests), 'HitCount': len(MaterializedRequests) - len(MissingByKey), 'MissCount': len(MissingByKey), 'StoredResultCount': len(PhysicalRequestCache), 'MaximumStoredResultCount': Services.MaximumPhysicalGlobalRouteTreeResultCacheEntries}
            if not MissingByKey:
                MaterializedValues = [CachedValuesByKey[Key] for Key in RequestCacheKeys]
                Values = [None] * len(Requests)
                for RequestIndex, Value in zip(MaterializedRequestIndexes, MaterializedValues):
                    Values[RequestIndex] = Value
                for Request in Requests:
                    Owner = State.PhysicalDescriptorOwnerByRequestId.get(id(Request))
                    if Owner is not None:
                        State.CompletedPhysicalDescriptorFingerprintsBySignal[Owner[0]].add(Owner[1])
                return Values
        NativeRequests = list(MissingByKey.values()) if PhysicalRequestCache is not None else MaterializedRequests
        NativeCompletionMask: tuple[bool, ...]
        if hasattr(State.Context, 'GenerateRouteTreesBounded'):
            BatchResult = State.Context.GenerateRouteTreesBounded(NativeRequests, Services.RemainingRoutingRuntimeMilliseconds(State.Deadline, State.AdaptiveExpiresAt))
            CompletionMask = Services.ReadRouteTreeBatchCompletionMask(BatchResult, len(NativeRequests))
            State.WorkTelemetry['RouteTreeCompletedWork'] = int(State.WorkTelemetry.get('RouteTreeCompletedWork', 0)) + BatchResult.CompletedWork
            NativeValues = list(BatchResult.RouteTrees)
            NativeCompletionMask = CompletionMask
            if BatchResult.DeadlineExceeded:
                State.RouteTreeNativeDeadlineExceeded = True
                State.WorkTelemetry['RouteTreeCompletionMask'] = {'RequestCount': len(CompletionMask), 'CompletedRequestIndices': [Index for Index, Completed in enumerate(CompletionMask) if Completed], 'Exact': hasattr(BatchResult, 'CompletionMask')}
                if PhysicalRequestCache is not None:
                    CompletedNativeValuesByKey = tuple(((Key, Value) for Key, Value, Completed in zip(MissingByKey, NativeValues, CompletionMask) if Completed))
                    EvictedCount = Services.RetainPhysicalGlobalRouteTreeResults(PhysicalRequestCache, CompletedNativeValuesByKey)
                    State.WorkTelemetry['PhysicalGlobalRouteTreeResultCache'].update({'DeadlineRetainedResultCount': len(CompletedNativeValuesByKey), 'DeadlineRetentionEvictedCount': EvictedCount, 'StoredResultCountAfterDeadlineRetention': len(PhysicalRequestCache)})
        else:
            NativeValues = State.Context.GenerateRouteTrees(NativeRequests)
            NativeCompletionMask = (True,) * len(NativeRequests)
        if PhysicalRequestCache is not None:
            NativeValuesByKey = {Key: Value for Key, Value, Completed in zip(MissingByKey, NativeValues, NativeCompletionMask) if Completed}
            EvictedCount = Services.RetainPhysicalGlobalRouteTreeResults(PhysicalRequestCache, NativeValuesByKey.items())
            State.WorkTelemetry['PhysicalGlobalRouteTreeResultCache'].update({'EvictedCount': EvictedCount, 'StoredResultCountAfterRetention': len(PhysicalRequestCache)})
            ResultValuesByKey = {**CachedValuesByKey, **NativeValuesByKey}
            MaterializedValues = [ResultValuesByKey.get(Key) for Key in RequestCacheKeys]
            MaterializedCompletionMask = tuple((Key in ResultValuesByKey for Key in RequestCacheKeys))
        else:
            MaterializedValues = NativeValues
            MaterializedCompletionMask = NativeCompletionMask
        Values = [None] * len(Requests)
        CompletedRequestIndexes = set()
        for RequestIndex, Value, Completed in zip(MaterializedRequestIndexes, MaterializedValues, MaterializedCompletionMask):
            Values[RequestIndex] = Value
            if Completed:
                CompletedRequestIndexes.add(RequestIndex)
        CompletedRequestIndexes.update(set(range(len(Requests))) - set(MaterializedRequestIndexes))
        for RequestIndex in CompletedRequestIndexes:
            Owner = State.PhysicalDescriptorOwnerByRequestId.get(id(Requests[RequestIndex]))
            if Owner is not None:
                State.CompletedPhysicalDescriptorFingerprintsBySignal[Owner[0]].add(Owner[1])
        if not State.RouteTreeNativeDeadlineExceeded and (not State.Resources.PreparingPhysicalComponentGlobalChannels):
            State.CheckRuntimeBudget('Candidate')
        return Values
    State.GenerateRouteTreesWithDeadline = GenerateRouteTreesWithDeadline
    State.InitialRequestLimit = max(1, min(State.Policy.TrackAssignment.MaximumRouteCandidatesPerNet, State.Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal) if State.Policy.AdaptiveRouting.Enabled else State.Policy.TrackAssignment.MaximumRouteCandidatesPerNet)
    CandidateExpansionRequestLimit = State.InitialRequestLimit
    if not State.UseNegotiatedRouting and State.Policy.AdaptiveRouting.Enabled and (State.Demand.TerminalCount > 64):
        ConfiguredExactInitialRequestFloor = int(Services.os.environ.get('RCS_EXACT_INITIAL_REQUEST_FLOOR', '32'))
        ExactInitialRequestFloor = ConfiguredExactInitialRequestFloor
        if State.PortalSliceLimited:
            ExactInitialRequestFloor = min(ExactInitialRequestFloor, 16)
        PreMaturePortfolioExactInitialRequestFloor = ExactInitialRequestFloor
        ExactInitialRequestFloor = Services.SelectMaturePortfolioExactInitialRequestFloor(ExactInitialRequestFloor, State.ApplyMaturePortfolioSearchCaps)
        State.WorkTelemetry['MaturePortfolioSearchCaps'] = {**dict(State.WorkTelemetry['MaturePortfolioSearchCaps']), 'ConfiguredExactInitialRequestFloor': ConfiguredExactInitialRequestFloor, 'RequestedExactInitialRequestFloor': PreMaturePortfolioExactInitialRequestFloor, 'EffectiveExactInitialRequestFloor': ExactInitialRequestFloor, 'ExactInitialRequestFloorCap': 8 if State.ApplyMaturePortfolioSearchCaps else None}
        State.InitialRequestLimit = max(ExactInitialRequestFloor, State.InitialRequestLimit)
    if State.PlacementWasRelocated and isinstance(State.PlacementRelocationDiagnostics, dict) and (int(State.PlacementRelocationDiagnostics.get('Variant', 0)) >= 3):
        State.InitialRequestLimit = max(6, State.InitialRequestLimit)
    State.UseSparseCandidateBootstrap = not State.UseNegotiatedRouting and State.PlacementWasRelocated and (State.CandidateDiversityLevel == 0) and (33 <= len(State.Profiles) <= 72) and isinstance(State.PlacementRecipeDiagnostics, dict) and (int(State.PlacementRecipeDiagnostics.get('RoutingSpacing', 0)) >= 10)
    if State.UseSparseCandidateBootstrap:
        State.InitialRequestLimit = max(36, State.InitialRequestLimit)
    State.HasPhysicalComponentRoutingContract = bool(not State.Resources.PreparingPhysicalComponentGlobalChannels and (State.HasRoutedComponentTemplate or State.HasExactPhysicalAssemblyChannels))
    State.RoutedComponentGlobalProbe = State.HasPhysicalComponentRoutingContract
    if State.HasPhysicalComponentRoutingContract:
        State.WorkTelemetry['RoutedComponentGlobalHandoff'] = {'Enabled': True, 'InitialRequestLimit': State.InitialRequestLimit, 'Disposition': 'bounded initial component-state admission followed by ordinary adaptive global assignment'}
    ActiveCandidateSignalCount = sum((1 for Signal in State.Profiles if not (Signal not in State.RegenerateSignals and (State.RetainedCandidateCache or {}).get(Signal))))
    ProvisionalCandidateRequestCount = max(1, (CandidateExpansionRequestLimit if State.Resources.PreparingPhysicalComponentGlobalChannels else State.InitialRequestLimit) * ActiveCandidateSignalCount)
    BaseCandidateExpansionLimit = min(State.AdaptiveBudget.CandidateExpansionsPerNet, max(State.Policy.DetailedRouting.MinimumCandidateExpansionLimit, State.Policy.AdaptiveRouting.MaximumCandidateGenerationExpansions // ProvisionalCandidateRequestCount)) if State.Policy.AdaptiveRouting.Enabled else State.Policy.DetailedRouting.StrictMaximumExpansions if not State.Profiles else max(State.Policy.DetailedRouting.MinimumCandidateExpansionLimit, min(State.Policy.DetailedRouting.StrictMaximumExpansions, 12000000 // ProvisionalCandidateRequestCount))
    State.BaseCandidateExpansionLimits = {Signal: min(State.Policy.DetailedRouting.StrictMaximumExpansions, BaseCandidateExpansionLimit * max(1, (max(1, len(Profile.Targets)) - 1).bit_length())) for Signal, Profile in State.Profiles.items()}
    State.CandidateExpansionLimits = {Signal: State.BaseCandidateExpansionLimits[Signal] for Signal in State.Profiles}
    State.CandidatesBySignal: dict[str, list[Services.NetRouteCandidate]] = Services.defaultdict(list)
    State.CandidateLimitsBySignal: dict[str, int] = {}
    State.CandidateDiagnostics: dict[str, dict[str, object]] = {}
    State.RawTrackAssignmentExtractionIncompleteReasons: dict[str, str] = {}
    State.ForeignSelectedPinAccessClaimsBySignal = (
        BuildForeignSelectedPinAccessClaimsBySignal(
            State.Profiles,
            State.PlacementPinAccessWitness,
        )
        if State.Policy.PlacementAccess.Enabled
        else {
            str(Signal): ()
            for Signal in sorted(State.Profiles)
        }
    )
    State.ForeignSelectedAccessRequiredClaimConflictBySignal = (
        Services.Counter()
    )
    State.ForeignSelectedAccessCandidateConflictBySignal = (
        Services.Counter()
    )
    State.ForeignSelectedAccessConflictSignalsBySignal = {
        str(Signal): []
        for Signal in sorted(State.Profiles)
    }
    SelectedAccessAuthorityDiagnostics = {
        'Enabled': bool(State.Policy.PlacementAccess.Enabled),
        'WitnessFingerprint': str(getattr(
            State.PlacementPinAccessWitness,
            'WitnessFingerprint',
            '',
        )),
        'ForeignClaimOwnerCountBySignal': {
            Signal: len(Claims)
            for Signal, Claims in sorted(
                State.ForeignSelectedPinAccessClaimsBySignal.items()
            )
        },
        'ExactBlockedWireNodeCountBySignal': {},
        'RequiredClaimRejectionCountBySignal': (
            State.ForeignSelectedAccessRequiredClaimConflictBySignal
        ),
        'CandidateRejectionCountBySignal': (
            State.ForeignSelectedAccessCandidateConflictBySignal
        ),
        'ConflictSignalsBySignal': (
            State.ForeignSelectedAccessConflictSignalsBySignal
        ),
    }
    State.WorkTelemetry['SelectedPinAccessCandidateAuthority'] = (
        SelectedAccessAuthorityDiagnostics
    )
    if State.PlacementAccessFabric is not None and State.PlacementAccessAssignment is not None and getattr(State.PlacementAccessAssignment, 'Success', False):
        FabricGuide = frozenset(((int(Position[0]), int(Position[2])) for Position in State.PlacementAccessFabric.Nodes))
        for Signal, RouteNodes in getattr(State.PlacementAccessAssignment, 'SignalRoutes', ()):
            Profile = State.Profiles.get(Signal)
            if Profile is None:
                continue
            RouteNodes = tuple(RouteNodes)
            Layer = next((CandidateLayer for CandidateLayer in range(State.LayerCount) if RouteNodes and State.Technology.RoutingY(State.MinimumY, CandidateLayer) == int(RouteNodes[0][1])), None)
            SourcePortalValues = () if Layer is None else State.Portals.get((Signal, Profile.Root, Layer), ())
            TargetPortalValues = () if Layer is None else tuple((State.Portals.get((Signal, Target, Layer), ()) for Target in Profile.Targets))
            if Layer is None or not SourcePortalValues or any((not Values for Values in TargetPortalValues)):
                raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='PlacementAccessWitnessRealization', AffectedNets=(Signal,), Detail='the frozen placement-access witness has no exact authoritative portal domain', Diagnostics={'Complete': False, 'FabricFingerprint': State.PlacementAccessFabric.FabricFingerprint, 'AssignmentFingerprint': State.PlacementAccessAssignment.AssignmentFingerprint}))
            (RejectionCounts): Services.Counter[str] = Services.Counter()
            Candidate = Services.PortalOperations._MaterializeCandidate(Signal, Profile, SourcePortalValues[0], tuple((Values[0] for Values in TargetPortalValues)), FabricGuide, Layer, 'X', 0, 0, list(RouteNodes), State.Region, State.Resources, State.Technology, State.Policy.DetailedRouting.LengthPenalty, State.Policy.DetailedRouting.CandidateBendWeight, State.Policy.DetailedRouting.CandidateViaWeight, State.Policy.DetailedRouting.LayerPenalty, 0, State.Policy.DetailedRouting.RepeaterPenalty, RejectionCounts=RejectionCounts)
            if Candidate is None:
                raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='PlacementAccessWitnessRealization', AffectedNets=(Signal,), Detail='the frozen placement-access tree failed exact materialization', Diagnostics={'Complete': False, 'Rejections': dict(RejectionCounts), 'FabricFingerprint': State.PlacementAccessFabric.FabricFingerprint, 'AssignmentFingerprint': State.PlacementAccessAssignment.AssignmentFingerprint}))
            ForeignAccessConflicts = (
                FindForeignSelectedPinAccessConflictSignals(
                    Signal,
                    Candidate.Claims,
                    State.ForeignSelectedPinAccessClaimsBySignal,
                    Services.ComponentClaimsConflict,
                )
            )
            if ForeignAccessConflicts:
                State.ForeignSelectedAccessCandidateConflictBySignal[
                    Signal
                ] += 1
                State.ForeignSelectedAccessConflictSignalsBySignal[Signal] = (
                    list(ForeignAccessConflicts)
                )
                raise Services.RoutingStageError(Services.RoutingFailure(
                    Reason=(
                        Services.RoutingFailureReason
                        .ClusterInterfaceSolveIncomplete
                    ),
                    Stage='PlacementAccessWitnessRealization',
                    AffectedNets=(Signal, *ForeignAccessConflicts),
                    Detail=(
                        'the frozen placement-access tree conflicts with a '
                        'foreign selected pin-access claim'
                    ),
                    Diagnostics={
                        'Complete': False,
                        'ConflictSignals': list(ForeignAccessConflicts),
                        'SelectedPinAccessWitnessFingerprint': str(getattr(
                            State.PlacementPinAccessWitness,
                            'WitnessFingerprint',
                            '',
                        )),
                        'FabricFingerprint': (
                            State.PlacementAccessFabric.FabricFingerprint
                        ),
                        'AssignmentFingerprint': (
                            State.PlacementAccessAssignment
                            .AssignmentFingerprint
                        ),
                    },
                ))
            State.CandidatesBySignal[Signal] = [Candidate]
            State.CandidateLimitsBySignal[Signal] = 1
            State.CandidateDiagnostics[Signal] = {'FrozenPlacementAccessWitness': True, 'Requests': 0, 'RoutedTrees': 1, 'Materialized': 1, 'DeferredRequests': 0, 'Rejections': {}}
    InterfacePreparationSignals = State.LeaseOwnershipSignals if State.PrepareClusterInterfaceAssignmentOnly else frozenset(State.Profiles)
    State.CandidateSignalOrder = sorted(InterfacePreparationSignals, key=lambda Value: (-len(State.Profiles[Value].Targets), -max((abs(State.Profiles[Value].Root[0] - Target[0]) + abs(State.Profiles[Value].Root[2] - Target[2]) for Target in State.Profiles[Value].Targets)), Value))
    State.CandidatePortalPhaseBySignal = {Signal: Index for Index, Signal in enumerate(State.CandidateSignalOrder)}
    ProtectedNodesBySignal = BuildProtectedRoutingNodesBySignal(
        State.Profiles,
        State.LocalClaimsBySignal,
        State.PlacementPinAccessWitness
        if State.Policy.PlacementAccess.Enabled
        else None,
    )
    ForeignExclusionStarted = Services.monotonic()
    State.ForeignBlockedNodesBySignal = Services.BuildForeignElectricalExclusionsBySignal(ProtectedNodesBySignal, State.Technology, DeferredPairwiseSignals=State.Resources.PhysicalComponentExactGlobalChannelSignals if State.Resources.PreparingPhysicalComponentGlobalChannels else frozenset())
    State.ForeignSelectedPinAccessBlockedWireNodesBySignal = {
        Signal: Services.ImmutableRoutingClaimsBlockedWireNodes(
            Claims
            for _Owner, Claims in (
                State.ForeignSelectedPinAccessClaimsBySignal.get(
                    Signal,
                    (),
                )
            )
        )
        for Signal in ProtectedNodesBySignal
    }
    if State.Policy.PlacementAccess.Enabled:
        State.ForeignBlockedNodesBySignal = {
            Signal: frozenset((
                *State.ForeignBlockedNodesBySignal.get(Signal, ()),
                *State.ForeignSelectedPinAccessBlockedWireNodesBySignal.get(
                    Signal,
                    (),
                ),
            ))
            for Signal in ProtectedNodesBySignal
        }
    SelectedAccessAuthorityDiagnostics[
        'ExactBlockedWireNodeCountBySignal'
    ] = {
        Signal: len(Nodes)
        for Signal, Nodes in sorted(
            State.ForeignSelectedPinAccessBlockedWireNodesBySignal.items()
        )
    }
    State.StageTimings['ForeignElectricalExclusionProjection'] = Services.monotonic() - ForeignExclusionStarted
    State.FrozenComponentBlockedWireNodesBySignal = {Signal: Services.FrozenComponentBlockedWireNodes(Signal, State.FrozenComponentClaims) for Signal in State.Profiles} if State.HasRoutedComponentTemplate else {}
    if State.PlanningPhysicalComponentExterior:
        ComponentGlobalKeepoutNodes = frozenset(State.PhysicalAssemblyPlan.GlobalKeepoutNodes)
        (DeclaredPassageNodesBySignal): dict[str, frozenset[tuple[int, int, int]]] = {Port.Signal: frozenset(Port.GlobalPath) for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan)}
        for Contract in State.PhysicalAssemblyPlan.Feedthroughs:
            DeclaredPassageNodesBySignal[Contract.Signal] = frozenset((*DeclaredPassageNodesBySignal.get(Contract.Signal, frozenset()), *Contract.ReservedPathNodes))
        State.FrozenComponentBlockedWireNodesBySignal = {Signal: frozenset((*State.FrozenComponentBlockedWireNodesBySignal.get(Signal, frozenset()), *ComponentGlobalKeepoutNodes - DeclaredPassageNodesBySignal.get(Signal, frozenset()))) for Signal in State.Profiles}
    State.CertifiedApertureDomain = Services.BuildCertifiedPhysicalComponentApertureDomain(State.PhysicalAssemblyPlan, Complete=True) if State.PlanningPhysicalComponentExterior else None
    if State.CertifiedApertureDomain is not None:
        State.Resources.PhysicalComponentApertureDomainCache[State.CertifiedApertureDomain.DomainFingerprint] = State.CertifiedApertureDomain
        State.WorkTelemetry['CertifiedPhysicalComponentApertureDomain'] = {'DomainFingerprint': State.CertifiedApertureDomain.DomainFingerprint, 'StableKeepoutCoreFingerprint': State.CertifiedApertureDomain.StableKeepoutCoreFingerprint, 'StableKeepoutCoreNodeCount': len(State.CertifiedApertureDomain.StableKeepoutCoreNodes), 'CrossingSignals': list(State.CertifiedApertureDomain.CrossingSignals), 'ApertureCount': len(State.CertifiedApertureDomain.Factors), 'Complete': State.CertifiedApertureDomain.Complete}
    GlobalApertureClaimsByPortSignal = {Port.Signal: Port.GlobalClaims for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan)} if State.PlanningPhysicalComponentExterior else {}
    AssemblySpecificSiblingApertureClaimsBySignal = {Signal: tuple((GlobalApertureClaimsByPortSignal[Port.Signal] for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan) if Port.Signal != Signal)) for Signal in State.Profiles} if State.PlanningPhysicalComponentExterior else {}
    State.AssemblySpecificSiblingAperturesBySignal = {Signal: tuple(((Port.Signal, GlobalApertureClaimsByPortSignal[Port.Signal]) for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan) if Port.Signal != Signal)) for Signal in State.Profiles} if State.PlanningPhysicalComponentExterior else {}
    AssemblySpecificSiblingBlockedWireNodesBySignal = {Signal: Services.ImmutableRoutingClaimsBlockedWireNodes((Claims for _SiblingSignal, Claims in Apertures)) for Signal, Apertures in State.AssemblySpecificSiblingAperturesBySignal.items()}
    AssemblySpecificSiblingFullPortAperturesBySignal = {Signal: tuple(((Port.Signal, Port.GlobalClaims) for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan) if Port.Signal != Signal)) for Signal in State.Profiles} if State.PlanningPhysicalComponentExterior else {}
    AssemblySpecificSiblingGlobalPathAperturesBySignal = {Signal: tuple(((Port.Signal, State.Resources.ResourceGraph.BuildRouteClaims(frozenset(Port.GlobalPath))) for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan) if Port.Signal != Signal)) for Signal in State.Profiles} if State.PlanningPhysicalComponentExterior else {}
    State.SiblingApertureConflictSetsBySignal: dict[str, list[frozenset[str]]] = Services.defaultdict(list)
    State.SiblingApertureRequiredClaimConflictsBySignal: Services.Counter[str] = Services.Counter()
    State.SiblingApertureSeamOwnershipBySignal: dict[str, dict[str, object]] = {}
    if AssemblySpecificSiblingApertureClaimsBySignal:
        State.WorkTelemetry['AssemblySpecificSiblingApertureFilter'] = {'Exact': True, 'SeamOwnershipComparison': 'full-port-claims-vs-global-path-only', 'ClaimsFingerprintBySignal': {Signal: Services.BuildStableFingerprint(tuple((Services._BuildApertureClaimsFingerprint(Claims) for Claims in ClaimsValues))) for Signal, ClaimsValues in sorted(AssemblySpecificSiblingApertureClaimsBySignal.items())}, 'GlobalPathClaimsFingerprintBySignal': {Signal: Services.BuildStableFingerprint(tuple((Services._BuildApertureClaimsFingerprint(Claims) for _SiblingSignal, Claims in ClaimsValues))) for Signal, ClaimsValues in sorted(AssemblySpecificSiblingGlobalPathAperturesBySignal.items())}, 'FullPortClaimsFingerprintBySignal': {Signal: Services.BuildStableFingerprint(tuple((Services._BuildApertureClaimsFingerprint(Claims) for _SiblingSignal, Claims in ClaimsValues))) for Signal, ClaimsValues in sorted(AssemblySpecificSiblingFullPortAperturesBySignal.items())}, 'BlockedWireNodeCountBySignal': {Signal: len(Nodes) for Signal, Nodes in sorted(AssemblySpecificSiblingBlockedWireNodesBySignal.items())}, 'RequiredClaimConflictCountsBySignal': State.SiblingApertureRequiredClaimConflictsBySignal}

    def AssemblySpecificSiblingApertureConflictSignals(Signal: str, Claims: RoutingResourceClaims) -> tuple[str, ...]:
        ConflictSignals = tuple(sorted((SiblingSignal for SiblingSignal, SiblingClaims in State.AssemblySpecificSiblingAperturesBySignal.get(Signal, ()) if Services.ComponentClaimsConflict(Claims, SiblingClaims))))
        FullPortConflictSignals, GlobalPathConflictSignals, LocalInteriorOnlyConflictSignals = Services.ClassifySiblingApertureSeamOwnershipConflicts(Claims, AssemblySpecificSiblingFullPortAperturesBySignal.get(Signal, ()), AssemblySpecificSiblingGlobalPathAperturesBySignal.get(Signal, ()))
        OwnershipDiagnostics = State.SiblingApertureSeamOwnershipBySignal.setdefault(Signal, {'ComparedCandidateCount': 0, 'ActualGlobalApertureConflictCandidateCount': 0, 'FullPortClaimConflictCandidateCount': 0, 'GlobalPathConflictCandidateCount': 0, 'LocalInteriorOnlyConflictCandidateCount': 0, 'ActualGlobalApertureConflictSignals': [], 'FullPortClaimConflictSignals': [], 'GlobalPathConflictSignals': [], 'LocalInteriorOnlyConflictSignals': []})
        OwnershipDiagnostics['ComparedCandidateCount'] += 1
        if ConflictSignals:
            OwnershipDiagnostics['ActualGlobalApertureConflictCandidateCount'] += 1
        if FullPortConflictSignals:
            OwnershipDiagnostics['FullPortClaimConflictCandidateCount'] += 1
        OwnershipDiagnostics['ActualGlobalApertureConflictSignals'] = sorted({*OwnershipDiagnostics['ActualGlobalApertureConflictSignals'], *ConflictSignals})
        OwnershipDiagnostics['FullPortClaimConflictSignals'] = sorted({*OwnershipDiagnostics['FullPortClaimConflictSignals'], *FullPortConflictSignals})
        if GlobalPathConflictSignals:
            OwnershipDiagnostics['GlobalPathConflictCandidateCount'] += 1
        if LocalInteriorOnlyConflictSignals:
            OwnershipDiagnostics['LocalInteriorOnlyConflictCandidateCount'] += 1
        OwnershipDiagnostics['GlobalPathConflictSignals'] = sorted({*OwnershipDiagnostics['GlobalPathConflictSignals'], *GlobalPathConflictSignals})
        OwnershipDiagnostics['LocalInteriorOnlyConflictSignals'] = sorted({*OwnershipDiagnostics['LocalInteriorOnlyConflictSignals'], *LocalInteriorOnlyConflictSignals})
        if ConflictSignals:
            State.SiblingApertureConflictSetsBySignal[Signal].append(frozenset(ConflictSignals))
        return ConflictSignals
    State.AssemblySpecificSiblingApertureConflictSignals = AssemblySpecificSiblingApertureConflictSignals
    State.PortalTupleClaimsBySignal: dict[str, dict[tuple[str, ...], Services.RoutingResourceClaims]] = Services.defaultdict(dict)

    def BuildSelfLegalPortalTuples(Profile: Any, SourcePortals: tuple[PinAccessPortal, ...], TargetPortalSets: list[tuple[PinAccessPortal, ...]]) -> tuple[tuple[tuple[PinAccessPortal, ...], ...], dict[str, object]]:
        """Enumerate a bounded, exact-claim-legal net-wide portal product."""
        Domains = (SourcePortals, *TargetPortalSets)
        CompletePortalTupleCount = Services.prod((len(Domain) for Domain in Domains))
        EvaluatedCompletePortalTupleIds: set[tuple[str, ...]] = set()
        RejectedConflictResources: set[Services.RoutingResourceId] = set()
        FrozenComponentConflictTupleCount = 0
        FrozenComponentConflictSignals: set[str] = set()
        AccessPaths = (Profile.SourceAccessPath, *(Profile.TargetAccessPaths[Target] for Target in Profile.Targets))
        FixedAccessNodes = frozenset((Position for AccessPath in AccessPaths for Position in AccessPath))

        def IsSelfLegal(CandidatePortals: tuple[PinAccessPortal, ...], *, RecordCompleteClaims: bool=True) -> bool:
            nonlocal FrozenComponentConflictTupleCount
            PortalIds = tuple((Portal.PortalId for Portal in CandidatePortals))
            Nodes = {*FixedAccessNodes, *(Position for CandidatePortal in CandidatePortals for Position in CandidatePortal.Path)}
            Claims = State.Resources.ResourceGraph.BuildRouteClaims(Nodes)
            SelfConflicts = Services.FindSelfClaimConflicts({Profile.Signal: Claims})
            FrozenBlockers = Services.PortalTupleConflictsWithFrozenComponentClaims(Profile.Signal, Claims, State.FrozenComponentClaims)
            IsLegal = not SelfConflicts and (not FrozenBlockers)
            if len(CandidatePortals) == len(Domains):
                EvaluatedCompletePortalTupleIds.add(PortalIds)
                RejectedConflictResources.update(SelfConflicts)
                if FrozenBlockers:
                    FrozenComponentConflictTupleCount += 1
                    FrozenComponentConflictSignals.update(FrozenBlockers)
            if IsLegal and RecordCompleteClaims and (len(CandidatePortals) == len(Domains)):
                State.PortalTupleClaimsBySignal[Profile.Signal][PortalIds] = Claims
            return IsLegal
        ExactPhysicalSignal = bool(State.PhysicalAssemblyPlan is not None and Profile.Signal in State.ExactPhysicalPortalSignalsForPreparation)
        if ExactPhysicalSignal:
            MaximumTupleCount = 16
            LegalCandidates: list[tuple[int, tuple[str, ...], tuple[Services.PinAccessPortal, ...]]] = []
            CoveredCompleteTupleCount = 0
            PrefixCheckCount = 0

            def Visit(DomainIndex: int, Selected: tuple[PinAccessPortal, ...]) -> None:
                nonlocal CoveredCompleteTupleCount, PrefixCheckCount
                for Portal in Domains[DomainIndex]:
                    Candidate = (*Selected, Portal)
                    PrefixCheckCount += 1
                    if PrefixCheckCount % 128 == 0:
                        State.CheckRuntimeBudget('InitialCandidateAssignment', {'Phase': 'exact-physical-portal-tuple-proof', 'Signal': Profile.Signal, 'PrefixCheckCount': PrefixCheckCount, 'CoveredPortalTupleCount': CoveredCompleteTupleCount, 'CompletePortalTupleCount': CompletePortalTupleCount})
                    if not IsSelfLegal(Candidate, RecordCompleteClaims=False):
                        CoveredCompleteTupleCount += Services.prod((len(Value) for Value in Domains[DomainIndex + 1:]))
                        continue
                    if DomainIndex + 1 < len(Domains):
                        Visit(DomainIndex + 1, Candidate)
                        continue
                    CoveredCompleteTupleCount += 1
                    PortalIds = tuple((Value.PortalId for Value in Candidate))
                    LegalCandidates.append((sum((Value.Cost for Value in Candidate)), PortalIds, Candidate))
            if Domains and all(Domains):
                Visit(0, ())
            LegalCandidates.sort(key=lambda Value: (Value[0], Value[1]))
            LegalTuples = tuple((Value[2] for Value in LegalCandidates[:MaximumTupleCount]))
            for Candidate in LegalTuples:
                IsSelfLegal(Candidate, RecordCompleteClaims=True)
            DomainComplete = bool(all(Domains) and CoveredCompleteTupleCount == CompletePortalTupleCount)
            RetainedLegalWitnessDomainComplete = bool(DomainComplete and len(LegalCandidates) <= MaximumTupleCount)
            EmptyProofComplete = bool(DomainComplete and (not LegalCandidates))
            return (LegalTuples, {'CompletePortalTupleCount': CompletePortalTupleCount, 'EvaluatedPortalTupleCount': CoveredCompleteTupleCount if DomainComplete else len(EvaluatedCompletePortalTupleIds), 'ActuallyEvaluatedPortalTupleCount': len(EvaluatedCompletePortalTupleIds), 'CoveredPortalTupleCount': CoveredCompleteTupleCount, 'PortalTupleDomainComplete': RetainedLegalWitnessDomainComplete, 'PortalTupleExhaustiveSearchComplete': DomainComplete, 'PortalTupleEmptyProofComplete': EmptyProofComplete, 'RetainedLegalWitnessDomainComplete': RetainedLegalWitnessDomainComplete, 'DiscoveredLegalPortalTupleCount': len(LegalCandidates), 'TerminalPortalDomainCounts': tuple((len(Domain) for Domain in Domains)), 'ConflictResources': tuple(sorted(RejectedConflictResources, key=str)), 'FrozenComponentConflictTupleCount': FrozenComponentConflictTupleCount, 'FrozenComponentConflictSignals': tuple(sorted(FrozenComponentConflictSignals)), 'FactorizedPrefixCheckCount': PrefixCheckCount, 'RetainedLegalPortalTupleCount': len(LegalTuples)})
        if State.Demand.TerminalCount > 64:
            State.WorkTelemetry['PortalTupleMode'] = 'bounded-diagonal'
            VariantCount = min(State.RoutePortalVariantCounts[Profile.Signal], max((len(Domain) for Domain in Domains)))
            DiagonalCandidates = tuple((tuple((Domain[(Variant + DomainIndex) % len(Domain)] for DomainIndex, Domain in enumerate(Domains))) for Variant in range(VariantCount)))
            if len(Profile.Targets) < 4:
                LegalTuples = tuple((CandidatePortals for CandidatePortals in DiagonalCandidates if IsSelfLegal(CandidatePortals)))
                return (LegalTuples, {'CompletePortalTupleCount': CompletePortalTupleCount, 'EvaluatedPortalTupleCount': len(EvaluatedCompletePortalTupleIds), 'TerminalPortalDomainCounts': tuple((len(Domain) for Domain in Domains)), 'ConflictResources': tuple(sorted(RejectedConflictResources, key=str)), 'FrozenComponentConflictTupleCount': FrozenComponentConflictTupleCount, 'FrozenComponentConflictSignals': tuple(sorted(FrozenComponentConflictSignals))})
            MaximumTupleCount = 16
            CandidateTuples: list[tuple[Services.PinAccessPortal, ...]] = []
            SeenPortalTuples: set[tuple[str, ...]] = set()
            ComponentHandoffBeamFallbackApplied = False

            def AddPortalTuple(CandidatePortals: tuple[PinAccessPortal, ...]) -> None:
                PortalIds = tuple((Portal.PortalId for Portal in CandidatePortals))
                if len(CandidateTuples) >= MaximumTupleCount or PortalIds in SeenPortalTuples or (not IsSelfLegal(CandidatePortals)):
                    return
                SeenPortalTuples.add(PortalIds)
                CandidateTuples.append(CandidatePortals)
            for CandidatePortals in DiagonalCandidates:
                AddPortalTuple(CandidatePortals)
            if not CandidateTuples and State.PhysicalAssemblyPlan is not None:
                ComponentHandoffBeamFallbackApplied = True
                PortalBeam: list[tuple[int, tuple[Services.PinAccessPortal, ...]]] = [(0, ())]
                for Domain in Domains:
                    NextBeam: dict[tuple[str, ...], tuple[int, tuple[Services.PinAccessPortal, ...]]] = {}
                    for PreviousCost, PreviousPortals in PortalBeam:
                        for Portal in Domain:
                            CandidatePortals = (*PreviousPortals, Portal)
                            if not IsSelfLegal(CandidatePortals, RecordCompleteClaims=False):
                                continue
                            PortalIds = tuple((Value.PortalId for Value in CandidatePortals))
                            Candidate = (PreviousCost + Portal.Cost, CandidatePortals)
                            Existing = NextBeam.get(PortalIds)
                            if Existing is None or Candidate[0] < Existing[0]:
                                NextBeam[PortalIds] = Candidate
                    PortalBeam = sorted(NextBeam.values(), key=lambda Value: (Value[0], tuple((Portal.PortalId for Portal in Value[1]))))[:MaximumTupleCount]
                    if not PortalBeam:
                        break
                for _Cost, CandidatePortals in PortalBeam:
                    if len(CandidatePortals) == len(Domains):
                        AddPortalTuple(CandidatePortals)
            if CandidateTuples:
                BaselinePortals = CandidateTuples[0]
                for RankOffset in (1, 2):
                    for DomainIndex, Domain in enumerate(Domains):
                        BaselinePortal = BaselinePortals[DomainIndex]
                        BaselineIndex = next((Index for Index, Portal in enumerate(Domain) if Portal.PortalId == BaselinePortal.PortalId))
                        Perturbed = list(BaselinePortals)
                        Perturbed[DomainIndex] = Domain[(BaselineIndex + RankOffset) % len(Domain)]
                        AddPortalTuple(tuple(Perturbed))
            return (tuple(CandidateTuples), {'CompletePortalTupleCount': CompletePortalTupleCount, 'EvaluatedPortalTupleCount': len(EvaluatedCompletePortalTupleIds), 'TerminalPortalDomainCounts': tuple((len(Domain) for Domain in Domains)), 'ConflictResources': tuple(sorted(RejectedConflictResources, key=str)), 'FrozenComponentConflictTupleCount': FrozenComponentConflictTupleCount, 'FrozenComponentConflictSignals': tuple(sorted(FrozenComponentConflictSignals)), 'ComponentHandoffBeamFallbackApplied': ComponentHandoffBeamFallbackApplied})
        Beam: list[tuple[int, tuple[Services.PinAccessPortal, ...]]] = [(0, ())]
        for AccessPath, Domain in zip(AccessPaths, Domains):
            Next: dict[tuple[str, ...], tuple[int, tuple[Services.PinAccessPortal, ...]]] = {}
            for PreviousCost, PreviousPortals in Beam:
                for Portal in Domain:
                    CandidatePortals = (*PreviousPortals, Portal)
                    if not IsSelfLegal(CandidatePortals):
                        continue
                    PortalIds = tuple((Value.PortalId for Value in CandidatePortals))
                    Candidate = (PreviousCost + Portal.Cost, CandidatePortals)
                    Existing = Next.get(PortalIds)
                    if Existing is None or Candidate[0] < Existing[0]:
                        Next[PortalIds] = Candidate
            Beam = sorted(Next.values(), key=lambda Value: (Value[0], tuple((Portal.PortalId for Portal in Value[1]))))[:16]
            if not Beam:
                break
        LegalTuples = tuple((PortalsValue for _Cost, PortalsValue in Beam if len(PortalsValue) == len(Domains)))
        return (LegalTuples, {'CompletePortalTupleCount': CompletePortalTupleCount, 'EvaluatedPortalTupleCount': len(EvaluatedCompletePortalTupleIds), 'TerminalPortalDomainCounts': tuple((len(Domain) for Domain in Domains)), 'ConflictResources': tuple(sorted(RejectedConflictResources, key=str)), 'FrozenComponentConflictTupleCount': FrozenComponentConflictTupleCount, 'FrozenComponentConflictSignals': tuple(sorted(FrozenComponentConflictSignals))})
    State.LegalPortalTuplesBySignalLayer: dict[tuple[str, int], tuple[tuple[Services.PinAccessPortal, ...], ...]] = {}

    def PortalDomainForTrunkLayer(Signal: str, Terminal: Position3, TrunkLayer: int) -> tuple[PinAccessPortal, ...]:
        """Return legal endpoint portals ranked around one trunk layer.

        Pin access owns its own layer. The route candidate's layer is the
        preferred global trunk plane and may differ from one or more endpoint
        portal layers; native three-dimensional routing connects them with
        ordinary vias.
        """
        return tuple(sorted({Portal.PortalId: Portal for Layer in range(State.LayerCount) for Portal in State.Portals.get((Signal, Terminal, Layer), ())}.values(), key=lambda Portal: (abs(Portal.Layer - TrunkLayer), Portal.Cost, Portal.Layer, Portal.PortalId)))
    State.PortalDomainForTrunkLayer = PortalDomainForTrunkLayer
    (ExactPhysicalPortalDomainCertificatesBySignal): dict[str, dict[str, object]] = {}
    if State.PhysicalAssemblyPlan is not None:
        ExactPortsBySignal = {Port.Signal: Port for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan)}
        ExactChannelsBySignal = {Channel.Signal: Channel for Channel in State.PhysicalAssemblyPlan.PlanningChannels}
        PhysicalProblemIdentityBound = bool(State.PhysicalProblem is not None and State.PhysicalProblem.PhysicalAssemblyPlan == State.PhysicalAssemblyPlan)
        FrozenPhysicalPreparation = State.Resources.PreparedPhysicalComponentPortFactorDomain
        CurrentAuthoritativeResourceGraphFingerprint = Services.BuildPhysicalExteriorResourceGraphFingerprint(State.Resources.ResourceGraph, State.PhysicalExteriorRegionFingerprint, State.Region)
        CurrentTechnologyFingerprint = Services.BuildStableFingerprint(repr(getattr(State.Resources.ResourceGraph, 'Technology', None)))
        SharedCertificateIdentityConditions = Services.BuildExactPhysicalPortalCertificateIdentityConditions(State.PhysicalAssemblyPlan, State.PhysicalProblem, FrozenPhysicalPreparation, CurrentAuthoritativeResourceGraphFingerprint, State.PhysicalExteriorRegionFingerprint, CurrentTechnologyFingerprint)
        for Signal, Port in sorted(ExactPortsBySignal.items()):
            Profile = State.Profiles.get(Signal)
            Channel = ExactChannelsBySignal.get(Signal)
            if Profile is None or Channel is None:
                continue
            PortLayer = int(Channel.Layer)
            Terminals = (Profile.Root, *Profile.Targets)
            EligibleTerminalLayers = tuple(sorted(((Terminal, PortLayer) if Terminal == Port.Attachment else (Terminal, Layer) for Terminal in Terminals for Layer in ((PortLayer,) if Terminal == Port.Attachment else range(State.LayerCount)))))
            PresentTerminalLayers = tuple(((Terminal, Layer) for Terminal, Layer in EligibleTerminalLayers if (Signal, Terminal, Layer) in State.Portals))
            GenericTerminalLayerKeys = frozenset(((Signal, Terminal, Layer) for Terminal, Layer in EligibleTerminalLayers if Terminal != Port.Attachment))
            CompletedGenericTerminalLayerKeys = frozenset((Key for Key in GenericTerminalLayerKeys if Key in State.CompletePortalDomainKeys))
            FullConfiguredPortalBreadthRequested = bool(State.RoutePortalVariantCounts.get(Signal, 0) >= State.DemandDerivedPortalLimit)
            PortalRequestDomainFingerprint = str(State.PortalRequestDomainFingerprintBySignal.get(Signal, ''))
            ExactAttachmentValidationFingerprint = str(State.ExactAttachmentDiagnostics.get('ExactAttachmentValidationFingerprint', ''))
            PlacementAndInterfaceIdentityBound = bool(PhysicalProblemIdentityBound and SharedCertificateIdentityConditions['PlacementIdentityMatch'] and SharedCertificateIdentityConditions['InterfaceIdentityMatch'])
            SeamIdentity = (Port.Signal, Port.Direction, tuple(Port.Attachment), tuple((tuple(Value) for Value in Port.GlobalPath)), Port.Capacity, Port.ReservationFingerprint)
            CertificateIdentityConditions = {**SharedCertificateIdentityConditions, 'SeamIdentityBound': bool(Port.Signal == Signal and Channel.Signal == Signal and Port.ReservationFingerprint and (Port.Capacity > 0) and Channel.ReservationFingerprint), 'FullConfiguredPortalBreadthRequested': FullConfiguredPortalBreadthRequested, 'PortalRequestDomainFingerprintPresent': bool(PortalRequestDomainFingerprint), 'ExactAttachmentValidationFingerprintPresent': bool(ExactAttachmentValidationFingerprint), 'TerminalLayerPresenceComplete': PresentTerminalLayers == EligibleTerminalLayers, 'GenericTerminalLayerCompletionComplete': CompletedGenericTerminalLayerKeys == GenericTerminalLayerKeys}
            Complete = all(CertificateIdentityConditions.values())
            CertificateFingerprint = Services.BuildStableFingerprint(('exact-physical-portal-domain-certificate-v1', State.PhysicalAssemblyPlan.PlanFingerprint, State.PhysicalAssemblyPlan.ResourceGraphFingerprint, State.PhysicalAssemblyPlan.TechnologyFingerprint, State.PhysicalAssemblyPlan.PlacementFingerprint, State.PhysicalAssemblyPlan.InterfaceFingerprint, Signal, SeamIdentity, EligibleTerminalLayers, tuple(((Terminal, Layer, tuple((Portal.PortalId for Portal in State.Portals.get((Signal, Terminal, Layer), ())))) for Terminal, Layer in EligibleTerminalLayers)), FullConfiguredPortalBreadthRequested, tuple(sorted(CompletedGenericTerminalLayerKeys)), PortalRequestDomainFingerprint, ExactAttachmentValidationFingerprint, tuple(sorted(CertificateIdentityConditions.items()))))
            ExactPhysicalPortalDomainCertificatesBySignal[Signal] = {'CertificateFingerprint': CertificateFingerprint, 'PlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint, 'ResourceGraphFingerprint': State.PhysicalAssemblyPlan.ResourceGraphFingerprint, 'TechnologyFingerprint': State.PhysicalAssemblyPlan.TechnologyFingerprint, 'PlacementFingerprint': State.PhysicalAssemblyPlan.PlacementFingerprint, 'InterfaceFingerprint': State.PhysicalAssemblyPlan.InterfaceFingerprint, 'SeamFingerprint': Services.BuildStableFingerprint(SeamIdentity), 'PortalRequestDomainFingerprint': PortalRequestDomainFingerprint, 'ExactAttachmentValidationFingerprint': ExactAttachmentValidationFingerprint, 'ExpectedTerminalLayerCount': len(EligibleTerminalLayers), 'PresentTerminalLayerCount': len(PresentTerminalLayers), 'EligibleLayers': list(range(State.LayerCount)), 'CompletedGenericTerminalLayerCount': len(CompletedGenericTerminalLayerKeys), 'ExpectedGenericTerminalLayerCount': len(GenericTerminalLayerKeys), 'FullConfiguredPortalBreadthRequested': FullConfiguredPortalBreadthRequested, 'PlacementAndInterfaceIdentityBound': PlacementAndInterfaceIdentityBound, 'IdentityConditions': dict(CertificateIdentityConditions), 'IdentityMismatchConditions': sorted((Name for Name, Matches in CertificateIdentityConditions.items() if not Matches)), 'CurrentResourceGraphFingerprint': CurrentAuthoritativeResourceGraphFingerprint, 'PreparationResourceGraphFingerprint': str(getattr(FrozenPhysicalPreparation, 'ResourceGraphFingerprint', '')), 'CurrentExteriorRegionFingerprint': State.PhysicalExteriorRegionFingerprint, 'PreparationExteriorRegionFingerprint': str(getattr(FrozenPhysicalPreparation, 'ExteriorRegionFingerprint', '')), 'Complete': Complete}
        State.WorkTelemetry['ExactPhysicalPortalDomainCertificates'] = {Signal: dict(Certificate) for Signal, Certificate in sorted(ExactPhysicalPortalDomainCertificatesBySignal.items())}
    MandatoryPreScreenStarted = Services.monotonic()
    MandatoryPreScreenPreparedSignals = 0
    MandatoryPreScreenSkippedSignals = 0
    State.PortalTupleFeasibilityBySignal: dict[str, list[dict[str, object]]] = Services.defaultdict(list)
    (MandatoryPortalFactorDomainsBySignal): dict[str, tuple[tuple[Services.PinAccessPortal, ...], ...]] = {}
    (MandatoryFixedAccessNodesBySignal): dict[str, frozenset[Services.Position3]] = {}
    for SignalIndex, Signal in enumerate(State.CandidateSignalOrder):
        if not Services.ShouldPrepareMandatoryPortalTuples(bool(State.CandidatesBySignal.get(Signal)), bool((State.RetainedCandidateCache or {}).get(Signal)), Signal in State.RegenerateSignals):
            MandatoryPreScreenSkippedSignals += 1
            continue
        MandatoryPreScreenPreparedSignals += 1
        Profile = State.Profiles[Signal]
        LayerOrder = tuple(range(State.LayerCount))
        if State.CoarsePlan is not None:
            PlannedLayer = State.CoarsePlan.Layers[Signal]
            LayerOrder = (PlannedLayer,) + tuple((Layer for Layer in LayerOrder if Layer != PlannedLayer))
        for Layer in LayerOrder:
            State.CheckRuntimeBudget('InitialCandidateAssignment', {'Phase': 'mandatory-portal-claim-prescreen', 'CompletedSignals': SignalIndex, 'TotalSignals': len(State.CandidateSignalOrder), 'Signal': Signal, 'Layer': Layer})
            SourcePortals = State.PortalDomainForTrunkLayer(Signal, Profile.Root, Layer)
            TargetPortalSets = [State.PortalDomainForTrunkLayer(Signal, Target, Layer) for Target in Profile.Targets]
            if not SourcePortals or any((not Values for Values in TargetPortalSets)):
                continue
            MandatoryPortalFactorDomainsBySignal.setdefault(Signal, (SourcePortals, *TargetPortalSets))
            MandatoryFixedAccessNodesBySignal.setdefault(Signal, frozenset((Position for Path in (Profile.SourceAccessPath, *(Profile.TargetAccessPaths[Target] for Target in Profile.Targets)) for Position in Path)))
            LegalTuples, TupleFeasibility = BuildSelfLegalPortalTuples(Profile, SourcePortals, TargetPortalSets)
            State.LegalPortalTuplesBySignalLayer[Signal, Layer] = LegalTuples
            State.PortalTupleFeasibilityBySignal[Signal].append({**TupleFeasibility, 'Layer': Layer, 'LegalPortalTupleCount': len(LegalTuples)})
    State.StageTimings['MandatoryPortalClaimPreScreen'] = Services.monotonic() - MandatoryPreScreenStarted
    State.WorkTelemetry['MandatoryPortalClaimPreScreen'] = {'PreparedSignalCount': MandatoryPreScreenPreparedSignals, 'SkippedRetainedSignalCount': MandatoryPreScreenSkippedSignals, 'Scope': 'dense-component' if State.PrepareClusterInterfaceAssignmentOnly else 'complete-design', 'EmptyNetWidePortalTupleSignals': sorted((Signal for Signal, Values in State.PortalTupleFeasibilityBySignal.items() if Values and (not any((int(Value['LegalPortalTupleCount']) > 0 for Value in Values)))))}
    State.WorkTelemetry['PortalTuplePreparation'] = {Signal: {'RoutePortalVariantCount': State.RoutePortalVariantCounts[Signal], 'LayerLegalTupleCounts': {str(Layer): len(State.LegalPortalTuplesBySignalLayer.get((Signal, Layer), ())) for Layer in range(State.LayerCount)}, 'LayerFeasibilityCount': len(State.PortalTupleFeasibilityBySignal.get(Signal, ())), 'ConflictResourceCount': len({str(Resource) for Value in State.PortalTupleFeasibilityBySignal.get(Signal, ()) for Resource in Value.get('ConflictResources', ())}), 'FrozenComponentConflictTupleCount': sum((int(Value.get('FrozenComponentConflictTupleCount', 0)) for Value in State.PortalTupleFeasibilityBySignal.get(Signal, ()))), 'FrozenComponentConflictSignals': sorted({str(BlockerSignal) for Value in State.PortalTupleFeasibilityBySignal.get(Signal, ()) for BlockerSignal in Value.get('FrozenComponentConflictSignals', ())}), 'ComponentHandoffBeamFallbackApplied': any((bool(Value.get('ComponentHandoffBeamFallbackApplied', False)) for Value in State.PortalTupleFeasibilityBySignal.get(Signal, ()))), 'TerminalPortalCounts': {str(ProfileTerminal): sum((len(State.Portals.get((Signal, ProfileTerminal, Layer), ())) for Layer in range(State.LayerCount))) for ProfileTerminal in (State.Profiles[Signal].Root, *State.Profiles[Signal].Targets)}} for Signal in State.CandidateSignalOrder if Services.ShouldPrepareMandatoryPortalTuples(bool(State.CandidatesBySignal.get(Signal)), bool((State.RetainedCandidateCache or {}).get(Signal)), Signal in State.RegenerateSignals)}
    if State.ValidatePhysicalComponentForeignPortalSupportOnly:
        if State.PhysicalAssemblyPlan is None:
            raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentForeignPortalSupport', Detail='foreign portal validation requires a frozen physical assembly plan'))
        ExactComponentSignals = Services.SelectPhysicalComponentExactGlobalChannelSignals(State.PhysicalAssemblyPlan)
        (RejectedTerminalProofs): list[dict[str, object]] = []
        ProofDomainComplete = True
        for Signal, Profile in sorted(State.Profiles.items()):
            if Signal in ExactComponentSignals:
                continue
            FixedAccessNodes = frozenset((Position for Path in (Profile.SourceAccessPath, *(Profile.TargetAccessPaths[Target] for Target in Profile.Targets)) for Position in Path))
            for Terminal in (Profile.Root, *Profile.Targets):
                TerminalKeys = tuple(((Signal, Terminal, Layer) for Layer in range(State.LayerCount)))
                TerminalDomainComplete = all((Key in State.CompletePortalDomainKeys for Key in TerminalKeys))
                ProofDomainComplete = bool(ProofDomainComplete and TerminalDomainComplete)
                TerminalPortals = tuple((Portal for Key in TerminalKeys for Portal in State.Portals.get(Key, ())))
                CandidateBlockers = tuple((Services.PortalTupleConflictsWithFrozenComponentClaims(Signal, State.Resources.ResourceGraph.BuildRouteClaims(FixedAccessNodes | frozenset(Portal.Path)), State.FrozenComponentClaims) for Portal in TerminalPortals))
                if TerminalDomainComplete and (not TerminalPortals or all(CandidateBlockers)):
                    RejectedTerminalProofs.append({'Signal': Signal, 'Terminal': list(Terminal), 'PortalCandidateCount': len(TerminalPortals), 'BlockingComponentSignals': sorted({Blocker for Blockers in CandidateBlockers for Blocker in Blockers}), 'Complete': True})
        if RejectedTerminalProofs:
            OrdinarySignals = frozenset((str(Proof['Signal']) for Proof in RejectedTerminalProofs))
            BlockingSignals = frozenset((str(Signal) for Proof in RejectedTerminalProofs for Signal in Proof['BlockingComponentSignals']))
            ConflictPairs = tuple(sorted({tuple(sorted((str(Proof['Signal']), str(Blocker)))) for Proof in RejectedTerminalProofs for Blocker in Proof['BlockingComponentSignals']}))
            raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentChannelCapacityUnsatisfiable, Stage='PhysicalComponentForeignPortalSupport', AffectedNets=tuple(sorted(OrdinarySignals | BlockingSignals)), Detail='the selected exterior contract removes every legal portal for an ordinary global terminal', Diagnostics={'GlobalPlanDomainComplete': ProofDomainComplete, 'CompleteAssignmentCutProof': ProofDomainComplete, 'PhysicalAssemblyPlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint, 'RejectedTerminalProofs': RejectedTerminalProofs, 'ConflictGraph': {'Classification': 'component-ordinary-portal-support-cut', 'ConflictSignals': sorted(OrdinarySignals | BlockingSignals), 'PairwiseIncompatibleEdges': [list(Pair) for Pair in ConflictPairs]}, 'ImplicitForeignTransitDomainCount': 0}))
        if not ProofDomainComplete:
            raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentForeignPortalSupport', Detail='ordinary portal support was sampled from an incomplete authoritative portal domain', Diagnostics={'GlobalPlanDomainComplete': False, 'CompleteAssignmentCutProof': False, 'PhysicalAssemblyPlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint}))
        raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.Stagnated, Stage='PhysicalComponentForeignPortalSupportValidated', Detail='the selected exterior contract preserves an ordinary portal at every terminal', Diagnostics={'Complete': ProofDomainComplete, 'PhysicalAssemblyPlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint}))

    def PortalTupleDomainIsCompleteForSignals(Signals: Iterable[str]) -> bool:
        """Require exact coverage of every prepared physical portal tuple."""
        for Signal in Signals:
            FeasibilityValues = State.PortalTupleFeasibilityBySignal.get(Signal, ())
            if not Services.PortalTupleFeasibilityDomainIsComplete(FeasibilityValues):
                return False
        return True
    (ExactEmptyPortalTupleEvidence): list[Services.MandatoryPortalTupleSelfConflictEvidence] = []
    for Signal in State.CandidateSignalOrder:
        FeasibilityValues = State.PortalTupleFeasibilityBySignal.get(Signal, ())
        if not FeasibilityValues or any((int(Value['LegalPortalTupleCount']) > 0 for Value in FeasibilityValues)):
            continue
        PortalCertificate = ExactPhysicalPortalDomainCertificatesBySignal.get(Signal)
        if PortalCertificate is None or not bool(PortalCertificate.get('Complete', False)) or (not Services.PortalTupleEmptyProofDomainIsComplete(FeasibilityValues, ExpectedLayers=range(State.LayerCount))):
            continue
        CompletePortalTupleCount = sum((int(Value['CompletePortalTupleCount']) for Value in FeasibilityValues))
        EvaluatedPortalTupleCount = sum((int(Value['EvaluatedPortalTupleCount']) for Value in FeasibilityValues))
        ExactEmptyPortalTupleEvidence.append(Services.MandatoryPortalTupleSelfConflictEvidence(Signal=Signal, CompletePortalTupleCount=CompletePortalTupleCount, EvaluatedPortalTupleCount=EvaluatedPortalTupleCount, TerminalPortalDomainCounts=tuple((int(Count) for Value in FeasibilityValues for Count in Value['TerminalPortalDomainCounts'])), ConflictResources=tuple(sorted({Resource for Value in FeasibilityValues for Resource in Value['ConflictResources']}, key=str)), PortalDomainCertificateFingerprint=str(PortalCertificate['CertificateFingerprint']), PhysicalAssemblyPlanFingerprint=str(PortalCertificate['PlanFingerprint']), ResourceGraphFingerprint=str(PortalCertificate['ResourceGraphFingerprint']), TechnologyFingerprint=str(PortalCertificate['TechnologyFingerprint']), PlacementFingerprint=str(PortalCertificate['PlacementFingerprint']), InterfaceFingerprint=str(PortalCertificate['InterfaceFingerprint']), SeamFingerprint=str(PortalCertificate['SeamFingerprint']), PortalRequestDomainFingerprint=str(PortalCertificate['PortalRequestDomainFingerprint']), ExactAttachmentValidationFingerprint=str(PortalCertificate['ExactAttachmentValidationFingerprint'])))
    if ExactEmptyPortalTupleEvidence:
        raise State.StructuredRoutingStageError(Services.BuildMandatoryPortalTupleSelfConflictFailure(tuple(ExactEmptyPortalTupleEvidence), StageTimings={**State.StageTimings, 'MandatoryPortalClaimPreScreen': Services.monotonic() - MandatoryPreScreenStarted}))
    IncompleteExactEmptyPortalTupleSignals = tuple(sorted((Signal for Signal in State.CandidateSignalOrder if Signal in ExactPhysicalPortalDomainCertificatesBySignal and State.PortalTupleFeasibilityBySignal.get(Signal) and (not any((int(Value['LegalPortalTupleCount']) > 0 for Value in State.PortalTupleFeasibilityBySignal[Signal]))) and (not (bool(ExactPhysicalPortalDomainCertificatesBySignal[Signal].get('Complete', False)) and Services.PortalTupleEmptyProofDomainIsComplete(State.PortalTupleFeasibilityBySignal[Signal], ExpectedLayers=range(State.LayerCount)))))))
    if IncompleteExactEmptyPortalTupleSignals:
        raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentGlobalPortalDomainIncomplete', AffectedNets=IncompleteExactEmptyPortalTupleSignals, Detail='the generated exact-plan portal tuple domain is empty, but raw request or layer coverage is incomplete', Diagnostics={'GlobalPlanDomainComplete': False, 'CompleteAssignmentCutProof': False, 'AssemblyPlanDependencySignals': list(IncompleteExactEmptyPortalTupleSignals), 'ExactPhysicalPortalDomainCertificates': {Signal: dict(ExactPhysicalPortalDomainCertificatesBySignal[Signal]) for Signal in IncompleteExactEmptyPortalTupleSignals}, 'PortalTuplePreparation': {Signal: list(State.PortalTupleFeasibilityBySignal[Signal]) for Signal in IncompleteExactEmptyPortalTupleSignals}, 'ComponentFabricConstructionComplete': True, 'OwnershipSearchComplete': False, 'ImplicitForeignTransitDomainCount': 0}))
    State.MandatoryPortalCuts = ()
    if not State.RetainedCandidateCache:
        State.MandatoryPortalCuts = Services.FindAllUnavoidableMandatoryClaimCuts({Signal: tuple(dict.fromkeys(State.PortalTupleClaimsBySignal.get(Signal, {}).values())) for Signal in State.CandidateSignalOrder}, WorkCheck=lambda Details: State.CheckRuntimeBudget('InitialCandidateAssignment', Details))
        if State.MandatoryPortalCuts:
            MandatoryPortalCutSignals = frozenset((Signal for Pair, _Positions in State.MandatoryPortalCuts for Signal in Pair))
            MandatoryPortalCutDomainComplete = bool(not State.PortalSliceLimited and (not State.RetainedPortfolioPortalProfileFrozen) and PortalTupleDomainIsCompleteForSignals(MandatoryPortalCutSignals))
            State.WorkTelemetry['MandatoryPortalCutPreScreen'] = {'ObservedPairCount': len(State.MandatoryPortalCuts), 'Signals': sorted(MandatoryPortalCutSignals), 'PortalTupleDomainComplete': MandatoryPortalCutDomainComplete, 'ContinuedToCandidateCompletion': not MandatoryPortalCutDomainComplete}
            if MandatoryPortalCutDomainComplete:
                raise State.StructuredRoutingStageError(Services.BuildUnavoidableMandatoryClaimCutFailure(State.MandatoryPortalCuts, State.StageTimings, PairwiseNoGoodEdges=tuple((Pair for Pair, _Positions in State.MandatoryPortalCuts))))
            if State.Resources.PreparingPhysicalComponentGlobalChannels and (not State.PortalSliceLimited) and (not State.RetainedPortfolioPortalProfileFrozen):
                ResourceGraphFingerprint = (str(getattr(State.PhysicalAssemblyPlan, 'ResourceGraphFingerprint', '')) if State.PhysicalAssemblyPlan is not None else '') or Services.BuildStableFingerprint((getattr(State.Resources.ResourceGraph, 'GraphVersion', ''), len(getattr(State.Resources.ResourceGraph, 'Nodes', ())), len(getattr(State.Resources.ResourceGraph, 'Edges', ()))))
                TechnologyFingerprint = (str(getattr(State.PhysicalAssemblyPlan, 'TechnologyFingerprint', '')) if State.PhysicalAssemblyPlan is not None else '') or Services.BuildStableFingerprint((getattr(State.Technology, 'TechnologyVersion', ''), repr(State.Technology)))
                Certificates = []
                CacheHitCount = 0
                for Pair, _SampledPositions in State.MandatoryPortalCuts:
                    Pair = tuple(sorted(map(str, Pair)))
                    if any((Signal not in MandatoryPortalFactorDomainsBySignal for Signal in Pair)):
                        continue
                    DomainFingerprint = Services.BuildMandatoryPortalPairDomainFingerprint(Pair, MandatoryFixedAccessNodesBySignal, MandatoryPortalFactorDomainsBySignal, State.FrozenComponentClaims, ResourceGraphFingerprint, TechnologyFingerprint)
                    Certificate, CacheHit = Services.GetMandatoryPortalPairFeasibilityCertificate(State.Resources.PhysicalGlobalMandatoryPortalPairCertificateCache, Signals=Pair, FixedAccessNodesBySignal=MandatoryFixedAccessNodesBySignal, PortalDomainsBySignal=MandatoryPortalFactorDomainsBySignal, FrozenComponentClaims=tuple(State.FrozenComponentClaims), ResourceGraph=State.Resources.ResourceGraph, DomainFingerprint=DomainFingerprint, ShouldStop=State.Deadline.IsExpired)
                    Certificates.append(Certificate)
                    CacheHitCount += int(CacheHit)
                CertifiedCuts = Services.SelectCertifiedMandatoryPortalPairCuts(Certificates)
                State.WorkTelemetry['MandatoryPortalPairFactorFeasibility'] = {'TriggeredPairCount': len(State.MandatoryPortalCuts), 'CertificateCount': len(Certificates), 'CompleteCertificateCount': sum((int(Value.Complete) for Value in Certificates)), 'UnsatisfiableCertificateCount': len(CertifiedCuts), 'CacheHitCount': CacheHitCount, 'ExpansionCount': sum((Value.ExpansionCount for Value in Certificates))}
                if CertifiedCuts:
                    CertifiedPairwiseNoGoodEdges = tuple((Certificate.Signals for Certificate in Certificates if Certificate.Complete and Certificate.Feasible is False and (frozenset(Certificate.DependencySignals) <= frozenset(Certificate.Signals))))
                    raise State.StructuredRoutingStageError(Services.BuildUnavoidableMandatoryClaimCutFailure(CertifiedCuts, State.StageTimings, PairwiseNoGoodEdges=CertifiedPairwiseNoGoodEdges))
    State.RouteRequestsBySignal = {}
    State.RouteMetadataBySignal = {}
    State.CandidateRequestShapeDomainFingerprintBySignal: dict[str, str] = {}
    State.CandidateRequestDependencyComponentsBySignal: dict[str, dict[str, object]] = {}
    State.ApertureCandidateDomainIdentityBySignal: dict[str, Services.PhysicalSignalApertureCandidateDomainIdentity] = {}
    State.PhysicalCandidateRequestShapesBySignal: dict[str, tuple[Services.CandidateRequestShapeDescriptor, ...]] = {}
    State.PhysicalRequestDescriptorFingerprintsBySignal: dict[str, tuple[str, ...]] = {}
    State.PhysicalRequestDomainFingerprintsBySignal: dict[str, str] = {}
    State.PortableRouteDomainPreparationBySignal: dict[str, Services.PortablePhysicalSignalRouteDomainPreparation] = {}
    State.PreSiblingCandidatesBySignal: dict[str, list[Services.NetRouteCandidate]] = Services.defaultdict(list)
    State.PreSiblingCandidateIdsBySignal: dict[str, set[str]] = Services.defaultdict(set)
    State.PreSiblingCandidateMetadataBySignal: dict[str, dict[str, tuple[str, int, int, int]]] = Services.defaultdict(dict)
    State.CompleteExteriorRouteDomainSignals: set[str] = set()
    State.IncompletePreSiblingDomainSignals: set[str] = set()
    State.DeferredRouteRequestCountsBySignal: Services.Counter[str] = Services.Counter()
    State.PhysicalGlobalCandidateSuffixConsumers: dict[str, Services.Callable[[int], dict[str, object]]] = {}
    State.ForeignPortalOverlapBySignal: Services.Counter[str] = Services.Counter()
    State.FrozenComponentPortalConflictBySignal: Services.Counter[str] = Services.Counter()
    State.CoordinatedCandidateProfileTelemetry: dict[str, dict[str, object]] = {}
    State.WorkTelemetry['CoordinatedCandidateDiversificationProfiles'] = State.CoordinatedCandidateProfileTelemetry
    State.CandidateAxisLaneBySignal: dict[str, dict[str, tuple[str, int, int, int]]] = {}
    for Signal, Values in (State.PriorCandidateMetadata or {}).items():
        if Signal in State.Profiles:
            State.CandidateAxisLaneBySignal[Signal] = dict(Values)
    for Signal, Values in (State.RetainedCandidateCache or {}).items():
        if Signal in State.Profiles and Signal not in State.RegenerateSignals:
            RetainedValues = []
            RetainedMetadata = dict(
                (State.RetainedCandidateMetadata or {}).get(Signal, {})
            )
            for Candidate in Values:
                ConflictSignals = FindForeignSelectedPinAccessConflictSignals(
                    Signal,
                    Candidate.Claims,
                    State.ForeignSelectedPinAccessClaimsBySignal,
                    Services.ComponentClaimsConflict,
                )
                if ConflictSignals:
                    State.ForeignSelectedAccessCandidateConflictBySignal[
                        Signal
                    ] += 1
                    State.ForeignSelectedAccessConflictSignalsBySignal[
                        Signal
                    ] = sorted({
                        *State.ForeignSelectedAccessConflictSignalsBySignal[
                            Signal
                        ],
                        *ConflictSignals,
                    })
                    continue
                RetainedValues.append(Candidate)
            State.CandidatesBySignal[Signal] = RetainedValues
            State.CandidateAxisLaneBySignal[Signal] = {
                Candidate.CandidateId: RetainedMetadata[Candidate.CandidateId]
                for Candidate in RetainedValues
                if Candidate.CandidateId in RetainedMetadata
            }
    State.InvariantRequestPayloadCacheDiagnostics: dict[str, int] = {'ConsideredRequestShapeCount': 0, 'MaterializedRequestCount': 0, 'AccessPayloadCacheHits': 0, 'AccessPayloadCacheMisses': 0, 'SelfConflictCacheHits': 0, 'ForeignSelectedAccessConflictCacheHits': 0, 'ForeignSelectedAccessRequiredClaimRejections': 0, 'GuidePayloadCacheHits': 0, 'GuidePayloadCacheMisses': 0, 'ConnectivityFactorChecks': 0, 'ConnectivityFactorCacheHits': 0, 'ConnectivityFactorPruned': 0}
    State.WorkTelemetry['InvariantRequestPayloadCache'] = State.InvariantRequestPayloadCacheDiagnostics
    State.PhysicalRouteFactorAdjacency: dict[Services.Position3, set[Services.Position3]] = Services.defaultdict(set)
    if State.Resources.PreparingPhysicalComponentGlobalChannels:
        for First, Second in State.Region.Edges:
            State.PhysicalRouteFactorAdjacency[First].add(Second)
            State.PhysicalRouteFactorAdjacency[Second].add(First)
    State.PhysicalRouteFactorConnectivityCache: dict[tuple[tuple[str, ...], tuple[frozenset[Services.Position2], int]], bool] = {}
    CandidateConstructionRank = {Signal: Index for Index, Signal in enumerate(State.CandidateSignalOrder)}
    State.CachedCertifiedEmptySignals = frozenset((str(Continuation.Signal) for Continuation in (State.Resources.PhysicalSignalRouteDomainContinuationCache.values() if State.Resources.PreparingPhysicalComponentGlobalChannels else ()) if isinstance(Continuation, Services.PhysicalSignalRouteDomainContinuation) and Continuation.Complete and (not Continuation.Candidates)))
    State.PhysicalCandidateConstructionOrder = tuple(sorted(State.CandidateSignalOrder, key=lambda Signal: (Signal not in State.CachedCertifiedEmptySignals, CandidateConstructionRank[Signal])))
    return PhaseOutcome()

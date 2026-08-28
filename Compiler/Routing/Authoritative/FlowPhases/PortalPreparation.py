"""PortalPreparation phase of authoritative routing."""
from __future__ import annotations
from ..RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState, PhaseOutcome

def RunPortalPreparation(State: AuthoritativeRoutingState, Services: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run the PortalPreparation phase against shared routing state."""
    ReusableRawPortalEntries = State.RawPortalCache.PortalEntries if State.RawPortalCache is not None else ()
    (PortableValidatedCompletePortalDomainKeys): frozenset[tuple[str, Services.Position3, int]] = frozenset()
    (PortableValidatedPolicyCompleteEmptyPortalDomainKeys): frozenset[tuple[str, Services.Position3, int]] = frozenset()
    (PortablePortalProofReusableSignalSet): frozenset[str] = frozenset()
    if State.ResourceRawPortalReusePlan is not None and State.ResourceRawPortalReusePlan.PortableAcrossPlacement and (State.RawPortalCache is State.ResourceRawPortalReusePlan.Cache):
        RegionNodes = frozenset(State.Region.Nodes)
        RegionEdges = frozenset(State.Region.Edges)
        (CachedEntriesBySignal): dict[str, list[tuple[tuple[str, Services.Position3, int], tuple[Services.PinAccessPortal, ...]]]] = Services.defaultdict(list)
        for Key, Values in State.RawPortalCache.PortalEntries:
            CachedEntriesBySignal[Key[0]].append((Key, Values))
        PortableSignalTranslations = dict(State.ResourceRawPortalReusePlan.SignalTranslations)
        PortableSignalTransforms = {Signal: (Transform, Translation) for Signal, Transform, Translation in State.ResourceRawPortalReusePlan.SignalPlanarTransforms}
        ValidatedEntries = []
        ValidatedSignals = set()
        ExactPhysicalPortalTerminals = frozenset(((Port.Signal, Port.Attachment) for Port in (Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan) if State.PhysicalAssemblyPlan is not None else ())))
        for Signal in sorted(State.ResourceRawPortalReusePlan.ReusedSignals):
            SignalDelta = PortableSignalTranslations.get(Signal, (0, 0, 0))
            SignalTransform, SignalDelta = PortableSignalTransforms.get(Signal, ('Identity', SignalDelta))

            def TranslatePortablePosition(Position: Position3) -> Position3:
                return Services.TransformPlanarRoutingPosition(Position, SignalTransform, SignalDelta)
            ExpectedKeys = {(Signal, Terminal, Layer) for Terminal in (State.Profiles[Signal].Root, *State.Profiles[Signal].Targets) for Layer in range(State.LayerCount) if (Signal, Terminal) not in ExactPhysicalPortalTerminals}
            SignalEntries = [((Key[0], TranslatePortablePosition(Key[1]), Key[2]), Values) for Key, Values in CachedEntriesBySignal.get(Signal, []) if (Signal, TranslatePortablePosition(Key[1])) not in ExactPhysicalPortalTerminals]
            if {Key for Key, _Values in SignalEntries} != ExpectedKeys:
                continue
            RebuiltSignalEntries = []
            SignalIsValid = True
            for Key, Values in SignalEntries:
                RebuiltValues = []
                for Portal in Values:
                    Rebuilt = Services.MaterializeValidatedPortablePortalPositiveWitness(Portal, Signal=Signal, Terminal=Key[1], Layer=Key[2], Transform=SignalTransform, Translation=SignalDelta, ResourceGraph=State.Resources.ResourceGraph, RegionNodes=RegionNodes, RegionEdges=RegionEdges)
                    if Rebuilt is None:
                        SignalIsValid = False
                        break
                    RebuiltValues.append(Rebuilt)
                if not SignalIsValid:
                    break
                RebuiltSignalEntries.append((Key, tuple(RebuiltValues)))
            if not SignalIsValid:
                continue
            ValidatedSignals.add(Signal)
            ValidatedEntries.extend(RebuiltSignalEntries)
        ValidatedPositiveSignalSet = frozenset(ValidatedSignals)
        PositiveReusableSignalSet = Services.SelectPortablePortalPositiveReusableSignals(ValidatedPositiveSignalSet)
        PortablePortalProofReusableSignalSet = Services.SelectPortablePortalProofReusableSignals(ValidatedPositiveSignalSet, State.PhysicalPortSignals)
        State.ResourceRawPortalReusePlan = Services.replace(State.ResourceRawPortalReusePlan, ReusedSignals=PositiveReusableSignalSet, GeneratedSignals=frozenset(State.RawPortalVariantCounts) - PositiveReusableSignalSet)
        ReusableRawPortalEntries = tuple(sorted(((Key, Values) for Key, Values in ValidatedEntries if Key[0] in PositiveReusableSignalSet)))
        PortableValidatedCompletePortalDomainKeys = Services.TransformPortableCompletePortalDomainKeys(State.RawPortalCache.CompletePortalDomainKeys, PortableSignalTransforms, (Key for Key, _Values in ReusableRawPortalEntries), ExactPhysicalPortalTerminals, State.PhysicalPortSignals)
        PortableValidatedPolicyCompleteEmptyPortalDomainKeys = Services.TransformPortableCompletePortalDomainKeys(State.RawPortalCache.PolicyCompleteEmptyPortalDomainKeys, PortableSignalTransforms, (Key for Key, _Values in ReusableRawPortalEntries), ExactPhysicalPortalTerminals, State.PhysicalPortSignals)
        State.WorkTelemetry['PortablePortalProofRegeneration'] = {'ValidatedPositiveSignals': sorted(ValidatedPositiveSignalSet), 'PositiveReusableSignals': sorted(PositiveReusableSignalSet), 'ProofReusableSignals': sorted(PortablePortalProofReusableSignalSet), 'RegeneratedExactPlanSignals': sorted((ValidatedPositiveSignalSet & State.PhysicalPortSignals) - PositiveReusableSignalSet), 'PositiveReuseValidation': 'current-resource-graph-region-and-claims', 'CompletenessDisposition': 'exact-plan-request-domain-proof-not-transferred', 'Reason': 'positive-witness-only-reuse'}
        if not PositiveReusableSignalSet:
            State.ResourceRawPortalReusePlan = None
            State.RawPortalCache = None
    CacheMatches = bool(State.FrozenPostClosurePortalHandoffApplied or (State.RawPortalCache is not None and (State.FrozenPreparedPortalCache is not None or State.RawPortalCache.Matches(State.Placed, State.Resources, State.Region, State.LayerCount, State.PortalLimit, State.RawPortalVariantCounts, State.Policy.DetailedRouting.GuideExpansion, State.Policy.DetailedRouting.StrictMaximumExpansions, State.PortalAccessGeometryFingerprint))))
    ExpectedGenericPortalDomainKeys = frozenset(((Signal, Terminal, Layer) for Signal in State.RawPortalVariantCounts for Terminal in (State.Profiles[Signal].Root, *State.Profiles[Signal].Targets) for Layer in range(State.LayerCount) if (Signal, Terminal) not in State.PhysicalPortTerminals))
    MissingExactCachePortalDomainKeys = frozenset()
    if CacheMatches and State.RawPortalCache is not None:
        MissingExactCachePortalDomainKeys, ExactCacheReusableSignals, ExactCacheGeneratedSignals = Services.PartitionExpectedGenericPortalDomainKeys(ExpectedGenericPortalDomainKeys, State.RawPortalCache.CompletePortalDomainKeys)
        if MissingExactCachePortalDomainKeys:
            State.ResourceRawPortalReusePlan = Services.RawPortalGeometryReusePlan(Cache=State.RawPortalCache, ReusedSignals=ExactCacheReusableSignals, GeneratedSignals=ExactCacheGeneratedSignals, ExactMatch=False)
            CacheMatches = False
            State.WorkTelemetry['ExactPortalCacheCompletenessRepair'] = {'Applied': True, 'MissingKeyCount': len(MissingExactCachePortalDomainKeys), 'MissingKeys': [[Signal, list(Terminal), Layer] for Signal, Terminal, Layer in sorted(MissingExactCachePortalDomainKeys)], 'ReusedSignals': sorted(ExactCacheReusableSignals), 'GeneratedSignals': sorted(ExactCacheGeneratedSignals), 'ExactSeamKeysExcluded': True}
    PartialPortalCacheMatches = bool(not CacheMatches and State.ResourceRawPortalReusePlan is not None and (not State.ResourceRawPortalReusePlan.ExactMatch) and (State.RawPortalCache is State.ResourceRawPortalReusePlan.Cache) and (State.ResourceRawPortalReusePlan.PortableAcrossPlacement or (State.RawPortalCache.Region is State.Region and State.RawPortalCache.GuidePlan is State.CoarsePlan and (State.RawPortalCache.AssignedColumns == State.EffectiveAssignedColumns) and (State.RawPortalCache.ReservedAccess == State.ReservedAccess))))
    ReusedPortalSignals = State.ResourceRawPortalReusePlan.ReusedSignals if PartialPortalCacheMatches and State.ResourceRawPortalReusePlan is not None else frozenset()
    ReusablePortalDomainKeys = frozenset((Key for Key, _Values in ReusableRawPortalEntries))
    IncompleteClosedComponentReuseSignals = frozenset((Signal for Signal in ReusedPortalSignals if any(((Signal, Terminal, Layer) not in ReusablePortalDomainKeys for OwnedSignal, Terminal in State.ClosedComponentOwnedTerminalPairs if OwnedSignal == Signal for Layer in range(State.LayerCount)))))
    if IncompleteClosedComponentReuseSignals:
        ReusedPortalSignals = frozenset(ReusedPortalSignals - IncompleteClosedComponentReuseSignals)
        State.WorkTelemetry['ClosedComponentPortalReuseRepair'] = {'Applied': True, 'RegeneratedSignals': sorted(IncompleteClosedComponentReuseSignals), 'Reason': 'incomplete-owned-terminal-domain'}
    State.WorkTelemetry['RawPortalValidatedPlanarTransforms'] = {Signal: {'Transform': Transform, 'Translation': list(Translation)} for Signal, Transform, Translation in (State.ResourceRawPortalReusePlan.SignalPlanarTransforms if State.ResourceRawPortalReusePlan is not None else ()) if Signal in ReusedPortalSignals}
    State.WorkTelemetry['PortablePortalDomainCompletenessTransfer'] = {'Applied': bool(PartialPortalCacheMatches and State.ResourceRawPortalReusePlan is not None and State.ResourceRawPortalReusePlan.PortableAcrossPlacement), 'TransferredCompleteKeyCount': len(PortableValidatedCompletePortalDomainKeys), 'ExactSeamKeysExcluded': True}
    GeneratedPortalSignals = (State.ResourceRawPortalReusePlan.GeneratedSignals if PartialPortalCacheMatches and State.ResourceRawPortalReusePlan is not None else frozenset() if CacheMatches else frozenset(State.RawPortalVariantCounts)) | IncompleteClosedComponentReuseSignals
    TransactionalLeasePrescreenCompleted = False

    def RunTransactionalLeasePrescreen(CandidatePortals: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]]) -> None:
        nonlocal TransactionalLeasePrescreenCompleted
        if not State.TransactionalLeasePrescreenSignals or State.PreparePortalGeometryOnly:
            return
        PrescreenStarted = Services.monotonic()
        try:
            _PrescreenPortals, PrescreenReservations = Services.ReserveClusterBoundaryLeases({Key: Values for Key, Values in CandidatePortals.items() if Key[0] in State.TransactionalLeasePrescreenSignals}, {Signal: Profile for Signal, Profile in State.Profiles.items() if Signal in State.TransactionalLeasePrescreenSignals}, State.Resources, ReservationVariant=State.ReservationVariant, PriorityInterfaceCutSignals=State.TransactionalLeasePrescreenSignals, MaximumExpansions=min(5000, State.AdaptiveBudget.AssignmentExpansions), UseCompleteClusterInterfaceAccess=State.CompleteClusterInterfaceAccess, WorkCheck=lambda Details: State.CheckRuntimeBudget('TransactionalLeasePrescreen', Details))
        except Services.RoutingStageError as Error:
            PrescreenDiagnostics = dict(Error.Failure.Diagnostics or {})
            raise Services.RoutingStageError(Services.replace(Error.Failure, Diagnostics={**PrescreenDiagnostics, 'Action': 'reject-transactional-pair-before-full-routing', 'TransactionalLeasePrescreen': {'Authoritative': True, 'Signals': sorted(State.TransactionalLeasePrescreenSignals), 'Result': 'infeasible', 'ElapsedSeconds': round(Services.monotonic() - PrescreenStarted, 6)}})) from Error
        TransactionalLeasePrescreenCompleted = True
        State.WorkTelemetry['TransactionalLeasePrescreen'] = {'Authoritative': True, 'Signals': sorted(State.TransactionalLeasePrescreenSignals), 'Result': 'feasible', 'ReservationCount': len(PrescreenReservations), 'ElapsedSeconds': round(Services.monotonic() - PrescreenStarted, 6)}
    if 'LocalClaimReleasePreScreen' in State.WorkTelemetry:
        State.WorkTelemetry['LocalClaimReleasePreScreen'] = {**dict(State.WorkTelemetry['LocalClaimReleasePreScreen']), 'ReusedRawPortalGeometry': CacheMatches or PartialPortalCacheMatches}
    (PortalRequests): list[tuple[Services.Any, ...]] = []

    def BuildReusableConfiguredPortalRequests() -> dict[tuple[str, Position3, int], tuple[Any, ...]]:
        CachedItems = tuple(zip(getattr(State.RawPortalCache, 'ConfiguredPortalRequestMetadata', ()), getattr(State.RawPortalCache, 'ConfiguredPortalRequests', ())))
        if not CachedItems:
            return {}
        ReusedSignals = ReusedPortalSignals if PartialPortalCacheMatches else frozenset(State.Profiles)
        PortableTransforms = {Signal: (Transform, Translation) for Signal, Transform, Translation in (State.ResourceRawPortalReusePlan.SignalPlanarTransforms if PartialPortalCacheMatches and State.ResourceRawPortalReusePlan is not None and State.ResourceRawPortalReusePlan.PortableAcrossPlacement else ())}
        Result = {}
        for Metadata, Request in CachedItems:
            Signal, Terminal, Layer = Metadata
            if Signal not in ReusedSignals or Signal not in State.Profiles:
                continue
            Transform, Translation = PortableTransforms.get(Signal, ('Identity', (0, 0, 0)))

            def TransformPosition(Position: Position3) -> Position3:
                return Services.TransformPlanarRoutingPosition(Position, Transform, Translation)
            CurrentTerminal = TransformPosition(Terminal)
            CurrentProfile = State.Profiles[Signal]
            if CurrentTerminal not in {CurrentProfile.Root, *CurrentProfile.Targets}:
                continue
            Starts, Targets, AllowedNodes, RoutingY, CandidateLimit, MaximumExpansions = Request
            CurrentRequest = (tuple((TransformPosition(Position) for Position in Starts)), tuple((TransformPosition(Position) for Position in Targets)), tuple((TransformPosition(Position) for Position in AllowedNodes)), TransformPosition((0, int(RoutingY), 0))[1], int(CandidateLimit), int(MaximumExpansions))
            Result[Signal, CurrentTerminal, int(Layer)] = CurrentRequest
        return Result
    ConfiguredPortalRequestByMetadata = BuildReusableConfiguredPortalRequests()
    PortalNativeDeadlineExceeded = False
    PreparedAccessProblem = None
    UnboundOwnedSignalFrontierProofCompleted = False
    EarlyAccessProblemSeconds = 0.0
    if CacheMatches:
        assert State.RawPortalCache is not None
        State.CompletePortalDomainKeys = set(State.RawPortalCache.CompletePortalDomainKeys)
        State.PortalRequestDomainFingerprintBySignal = dict(State.RawPortalCache.PortalRequestDomainFingerprints)
        State.Context = State.RawPortalCache.Context if State.RawPortalCache.Region is State.Region else Services.RustRoutingContext(State.Bounds, (State.MinimumX, State.MaximumX, State.MinimumZ, State.MaximumZ), sorted(State.Region.Nodes), sorted(State.Region.Edges))
        RawPortals = State.RawPortalCache.BuildPortalDictionary()
        State.WorkTelemetry.update({'PortalCacheHit': True, 'PortalRequestCount': State.RawPortalCache.RequestCount, 'PortalTargetCount': State.RawPortalCache.TargetCount, 'PortalStarvationFallbackCount': State.RawPortalCache.StarvationCount, 'PortalCompletedWork': 0, 'PortalBatchCount': 0, 'PortalPartialCacheHit': False, 'PortalCacheMode': 'exact', 'PortalCacheReusedSignals': sorted(State.Profiles), 'PortalCacheGeneratedSignals': [], 'PortalReusedRequestCount': State.RawPortalCache.RequestCount, 'PortalGeneratedRequestCount': 0})
        State.EffectiveRawPortalCache = State.RawPortalCache if State.RawPortalCache.Region is State.Region else Services.replace(State.RawPortalCache, Region=State.Region, Context=State.Context, AssignmentIndexed=State.Resources.ResourceGraph.BuildIndexedGraph(State.Region), AssignmentEncodingCache={})
    else:
        State.CompletePortalDomainKeys = set(PortableValidatedCompletePortalDomainKeys) if PartialPortalCacheMatches and State.ResourceRawPortalReusePlan is not None and State.ResourceRawPortalReusePlan.PortableAcrossPlacement else {Key for Key in (State.RawPortalCache.CompletePortalDomainKeys if State.RawPortalCache is not None else ()) if Key[0] in ReusedPortalSignals}
        State.PortalRequestDomainFingerprintBySignal = {Signal: Fingerprint for Signal, Fingerprint in (State.RawPortalCache.PortalRequestDomainFingerprints if State.RawPortalCache is not None else ()) if Signal in ReusedPortalSignals and (State.ResourceRawPortalReusePlan is None or not State.ResourceRawPortalReusePlan.PortableAcrossPlacement or Signal in PortablePortalProofReusableSignalSet)}
        State.Context = State.RawPortalCache.Context if PartialPortalCacheMatches and State.RawPortalCache is not None and (State.ResourceRawPortalReusePlan is not None) and (not State.ResourceRawPortalReusePlan.PortableAcrossPlacement) else Services.RustRoutingContext(State.Bounds, (State.MinimumX, State.MaximumX, State.MinimumZ, State.MaximumZ), sorted(State.Region.Nodes), sorted(State.Region.Edges))
        (GeneratedRawPortals): dict[tuple[str, Services.Position3, int], tuple[Services.PinAccessPortal, ...]] = {}
        (CompleteGeneratedPortalDomainKeys): set[tuple[str, Services.Position3, int]] = set()
        (PolicyCompleteEmptyPortalDomainKeys): set[tuple[str, Services.Position3, int]] = set(PortableValidatedPolicyCompleteEmptyPortalDomainKeys if PartialPortalCacheMatches and State.ResourceRawPortalReusePlan is not None and State.ResourceRawPortalReusePlan.PortableAcrossPlacement else (Key for Key in getattr(State.RawPortalCache, 'PolicyCompleteEmptyPortalDomainKeys', ()) if Key[0] in ReusedPortalSignals))
        (PortalRequestDomainRecordsBySignal): dict[str, list[tuple[object, ...]]] = Services.defaultdict(list)
        CachedSignalRequestCounts = dict(State.RawPortalCache.SignalRequestCounts) if PartialPortalCacheMatches and State.RawPortalCache is not None else {}
        CachedSignalTargetCounts = dict(State.RawPortalCache.SignalTargetCounts) if PartialPortalCacheMatches and State.RawPortalCache is not None else {}
        CachedSignalStarvationCounts = dict(State.RawPortalCache.SignalStarvationCounts) if PartialPortalCacheMatches and State.RawPortalCache is not None else {}
        SignalRequestCounts = {Signal: CachedSignalRequestCounts[Signal] if Signal in ReusedPortalSignals else 0 for Signal in State.Profiles}
        SignalTargetCounts = {Signal: CachedSignalTargetCounts[Signal] if Signal in ReusedPortalSignals else 0 for Signal in State.Profiles}
        SignalStarvationCounts = {Signal: CachedSignalStarvationCounts[Signal] if Signal in ReusedPortalSignals else 0 for Signal in State.Profiles}
        RegionNodeSet = frozenset(State.Region.Nodes)
        (NodesByColumn): dict[Services.Position2, list[Services.Position3]] = Services.defaultdict(list)
        for Position in State.Region.Nodes:
            NodesByColumn[Position[0], Position[2]].append(Position)
        (NodesByLayer): dict[int, tuple[Services.Position3, ...]] = {State.Technology.RoutingY(State.MinimumY, LayerIndex): tuple(sorted((Position for Position in State.Region.Nodes if Position[1] == State.Technology.RoutingY(State.MinimumY, LayerIndex)))) for LayerIndex in range(State.LayerCount)}
        PortalStarvationCount = sum(SignalStarvationCounts.values())
        PortalRequestMetadata = []
        OrderedPortalSignals = tuple(sorted(State.RawPortalVariantCounts, key=lambda Signal: (Signal not in State.ExactPhysicalPortalSignalsForPreparation, Signal)))
        for SignalIndex, Signal in enumerate(OrderedPortalSignals):
            State.CheckRuntimeBudget('PortalRequestPreparation', {'PreparedSignalCount': SignalIndex, 'PortalSignalCount': len(OrderedPortalSignals), 'PreparedPortalRequestCount': len(PortalRequests)})
            if Signal in ReusedPortalSignals:
                continue
            Profile = State.Profiles[Signal]
            TerminalPaths = Services.SelectGenericPortalTerminalPaths(Profile, None if State.PreparePhysicalComponentAssemblyOnly else State.PhysicalAssemblyPlan)
            for TerminalIndex, (Terminal, AccessPath) in enumerate(TerminalPaths):
                State.CheckRuntimeBudget('PortalRequestPreparation', {'Signal': Signal, 'PreparedTerminalCount': TerminalIndex, 'TerminalCount': len(TerminalPaths), 'PreparedPortalRequestCount': len(PortalRequests)})
                AccessColumns = {(X, Z) for X, _Y, Z in AccessPath}
                AllowedColumns = {(AccessX + DeltaX, AccessZ + DeltaZ) for AccessX, AccessZ in AccessColumns for DeltaX in range(-State.Policy.DetailedRouting.GuideExpansion, State.Policy.DetailedRouting.GuideExpansion + 1) for DeltaZ in range(-State.Policy.DetailedRouting.GuideExpansion, State.Policy.DetailedRouting.GuideExpansion + 1) if abs(DeltaX) + abs(DeltaZ) <= State.Policy.DetailedRouting.GuideExpansion}
                AllowedNodeSet = {Position for Column in AllowedColumns for Position in NodesByColumn.get(Column, ())} | set(AccessPath)
                AccessFabricDomain = State.PlacementAccessDomains.get((str(Signal), tuple(Terminal)))
                if AccessFabricDomain is not None:
                    AllowedNodeSet.update((Position for Stub in AccessFabricDomain.EscapeStubs for Position in Stub.Path))
                AllowedNodes = sorted(AllowedNodeSet)
                for Layer in range(State.LayerCount):
                    RoutingY = State.Technology.RoutingY(State.MinimumY, Layer)
                    if AccessFabricDomain is not None and (not any((int(Stub.Ingress[1]) == RoutingY for Stub in AccessFabricDomain.EscapeStubs))):
                        GeneratedRawPortals[Signal, Terminal, Layer] = ()
                        CompleteGeneratedPortalDomainKeys.add((Signal, Terminal, Layer))
                        PolicyCompleteEmptyPortalDomainKeys.add((Signal, Terminal, Layer))
                        PortalRequestDomainRecordsBySignal[Signal].append((Terminal, int(Layer), 'ineligible-placement-access-fabric-layer', str(State.GuideInputFingerprint)))
                        continue
                    if State.RequireCompleteClusterInterfaceDomain and State.InterfaceDeckLayer is not None and (Signal in State.InterClusterChannelSignals) and (Layer != int(State.InterfaceDeckLayer)):
                        GeneratedRawPortals[Signal, Terminal, Layer] = ()
                        CompleteGeneratedPortalDomainKeys.add((Signal, Terminal, Layer))
                        PolicyCompleteEmptyPortalDomainKeys.add((Signal, Terminal, Layer))
                        PortalRequestDomainRecordsBySignal[Signal].append((Terminal, int(Layer), 'ineligible-interface-deck-layer', str(State.GuideInputFingerprint)))
                        continue
                    if State.InterfaceDeckLayer is not None and Layer == int(State.InterfaceDeckLayer) and (Signal not in State.InterClusterChannelSignals):
                        GeneratedRawPortals[Signal, Terminal, Layer] = ()
                        CompleteGeneratedPortalDomainKeys.add((Signal, Terminal, Layer))
                        PolicyCompleteEmptyPortalDomainKeys.add((Signal, Terminal, Layer))
                        PortalRequestDomainRecordsBySignal[Signal].append((Terminal, int(Layer), 'reserved-interface-deck-layer', str(State.GuideInputFingerprint)))
                        continue
                    (ChannelIngressTargets): list[Services.Position3] = []
                    if State.InterClusterChannel is not None and Signal in State.InterClusterChannelSignals:
                        RawIngressTargets = {Position for Lane in State.InterClusterChannel.Lanes if Lane.Layer == Layer for Ingress in Lane.IngressNodes for Position in ((int(Ingress[0]), RoutingY, int(Ingress[2])),) if Position in RegionNodeSet}
                        AccessTerminal = AccessPath[-1]
                        RankedChannelIngressTargets = sorted(RawIngressTargets, key=lambda Position: (abs(Position[0] - AccessTerminal[0]) + abs(Position[2] - AccessTerminal[2]), Position))
                        ChannelIngressTargets = RankedChannelIngressTargets if State.RequireCompleteClusterInterfaceDomain else RankedChannelIngressTargets[:4]
                        (ChannelColumns): set[Services.Position2] = set()
                        for Ingress in ChannelIngressTargets:
                            for X in range(min(AccessTerminal[0], Ingress[0]), max(AccessTerminal[0], Ingress[0]) + 1):
                                ChannelColumns.add((X, AccessTerminal[2]))
                                ChannelColumns.add((X, Ingress[2]))
                            for Z in range(min(AccessTerminal[2], Ingress[2]), max(AccessTerminal[2], Ingress[2]) + 1):
                                ChannelColumns.add((AccessTerminal[0], Z))
                                ChannelColumns.add((Ingress[0], Z))
                        AllowedNodeSet.update((Position for Column in ChannelColumns for Position in NodesByColumn.get(Column, ())))
                        AllowedNodeSet.update(ChannelIngressTargets)
                        AllowedNodes = sorted(AllowedNodeSet)
                    AccessFabricIngressTargets = sorted({tuple(Stub.Ingress) for Stub in AccessFabricDomain.EscapeStubs if int(Stub.Ingress[1]) == RoutingY and tuple(Stub.Ingress) in RegionNodeSet}) if AccessFabricDomain is not None else []
                    PortalStarts = list(Services.SelectGraphAccessStarts(AccessPath, RegionNodeSet, PreferOutermost=Signal in State.TransactionalLeasePrescreenSignals))
                    PortalAllowedNodes = list(AllowedNodes)
                    PortalTargets = sorted((Position for Position in AllowedNodes if Position[1] == RoutingY), key=lambda Position: (min((abs(Position[0] - AccessPosition[0]) + abs(Position[1] - AccessPosition[1]) + abs(Position[2] - AccessPosition[2]) for AccessPosition in AccessPath)), abs(Position[0] - AccessPath[-1][0]), abs(Position[2] - AccessPath[-1][2]), Position))
                    if ChannelIngressTargets:
                        ChannelTargetSet = frozenset(ChannelIngressTargets)
                        PortalTargets = list(ChannelIngressTargets) if State.RequireCompleteClusterInterfaceDomain else [*ChannelIngressTargets, *(Position for Position in PortalTargets if Position not in ChannelTargetSet)]
                    if AccessFabricIngressTargets:
                        AccessFabricTargetSet = frozenset(AccessFabricIngressTargets)
                        PortalTargets = [*AccessFabricIngressTargets, *(Position for Position in PortalTargets if Position not in AccessFabricTargetSet)]
                    if len(PortalTargets) == 0:
                        GlobalLayerTargets = list(NodesByLayer.get(RoutingY, ()))
                        if GlobalLayerTargets:
                            AccessTerminal = AccessPath[-1]
                            PortalAllowedNodes = list(sorted(set(PortalAllowedNodes) | set(GlobalLayerTargets)))
                            PortalTargets = sorted(GlobalLayerTargets, key=lambda Position: (abs(Position[0] - AccessTerminal[0]) + abs(Position[2] - AccessTerminal[2]), abs(Position[0] - AccessTerminal[0]), abs(Position[2] - AccessTerminal[2]), Position))
                            PortalStarvationCount += 1
                            SignalStarvationCounts[Signal] += 1
                    MaxPortalTargets = len(PortalTargets) if State.RequireCompleteClusterInterfaceDomain and State.InterfaceDeckLayer is not None and (Signal in State.InterClusterChannelSignals) and (Layer == int(State.InterfaceDeckLayer)) else max(1, min(len(PortalTargets), State.RawPortalVariantCounts[Signal]))
                    PortalTargets = PortalTargets[:MaxPortalTargets]
                    PortalCandidateLimit = max(State.RawPortalVariantCounts[Signal], len(PortalTargets)) if State.RequireCompleteClusterInterfaceDomain and State.InterfaceDeckLayer is not None and (Signal in State.InterClusterChannelSignals) and (Layer == int(State.InterfaceDeckLayer)) else State.RawPortalVariantCounts[Signal]
                    PortalRequests.append((list(PortalStarts), PortalTargets, PortalAllowedNodes, RoutingY, PortalCandidateLimit, State.Policy.DetailedRouting.StrictMaximumExpansions))
                    PortalRequestMetadata.append((Signal, Terminal, Layer))
                    PortalRequestDomainRecordsBySignal[Signal].append((Terminal, int(Layer), tuple(PortalStarts), tuple(PortalTargets), Services.BuildStableFingerprint(tuple(PortalAllowedNodes)), int(RoutingY), int(PortalCandidateLimit), int(State.Policy.DetailedRouting.StrictMaximumExpansions), str(State.GuideInputFingerprint), tuple(State.Bounds)))
                    SignalRequestCounts[Signal] += 1
                    SignalTargetCounts[Signal] += len(PortalTargets)
        for Signal, Records in sorted(PortalRequestDomainRecordsBySignal.items()):
            State.PortalRequestDomainFingerprintBySignal[Signal] = Services.BuildConfiguredPortalRequestDomainFingerprint(Signal, int(State.RawPortalVariantCounts[Signal]), int(State.Policy.DetailedRouting.StrictMaximumExpansions), str(State.GuideInputFingerprint), tuple(State.Bounds), tuple(sorted(Records)))
        ConfiguredPortalRequestByMetadata.update({Metadata: Request for Metadata, Request in zip(PortalRequestMetadata, PortalRequests)})
        ReusedPortalRequestCount = sum((SignalRequestCounts[Signal] for Signal in ReusedPortalSignals))
        GeneratedPortalRequestCount = len(PortalRequests)
        State.WorkTelemetry['PortalRequestCount'] = sum(SignalRequestCounts.values())
        State.WorkTelemetry['PortalTargetCount'] = sum(SignalTargetCounts.values())
        State.WorkTelemetry['PortalStarvationFallbackCount'] = PortalStarvationCount
        State.WorkTelemetry['PortalCacheHit'] = False
        State.WorkTelemetry['PortalPartialCacheHit'] = PartialPortalCacheMatches
        State.WorkTelemetry['PortalCacheMode'] = 'partial-signal' if PartialPortalCacheMatches else 'miss'
        State.WorkTelemetry['PortalCacheReusedSignals'] = sorted(ReusedPortalSignals)
        State.WorkTelemetry['PortalCacheGeneratedSignals'] = sorted(GeneratedPortalSignals)
        State.WorkTelemetry['PortalReusedRequestCount'] = ReusedPortalRequestCount
        State.WorkTelemetry['PortalGeneratedRequestCount'] = GeneratedPortalRequestCount
        PortalBatchCount = 0
        PortalCompletedWork = 0

        def GeneratePortalRequestBatch(Requests: list[tuple[Any, ...]], Stage: str) -> tuple[list[Any], tuple[bool, ...], bool]:
            nonlocal PortalBatchCount, PortalCompletedWork
            if not Requests:
                return ([], (), False)
            PortalBatchCount += 1
            State.CheckRuntimeBudget(Stage)
            if hasattr(State.Context, 'GeneratePortalCandidateBatchesBounded'):
                PortalBatchResult = State.Context.GeneratePortalCandidateBatchesBounded(Requests, Services.RemainingRoutingRuntimeMilliseconds(State.Deadline, State.AdaptiveExpiresAt))
                PortalCompletedWork += PortalBatchResult.CompletedWork
                try:
                    CandidateValues, CompletionMask = Services.ReadPortalBatchCandidatesAndCompletionMask(PortalBatchResult, len(Requests))
                except ValueError as Error:
                    raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.PhysicalComponentAssemblyIncomplete if State.PhysicalAssemblyPlan is not None else Services.RoutingFailureReason.RuntimeBudgetExceeded, Stage=Stage, Detail='native portal results do not align with the configured request domain', Diagnostics={'PortalRequestCount': len(Requests), 'PortalResultCount': len(getattr(PortalBatchResult, 'Candidates', ())), 'PortalCompletedWork': int(PortalBatchResult.CompletedWork), 'GlobalPlanDomainComplete': False, 'CompleteAssignmentCutProof': False})) from Error
                return (CandidateValues, CompletionMask, bool(PortalBatchResult.DeadlineExceeded))
            Results = State.Context.GeneratePortalCandidateBatches(Requests)
            PortalCompletedWork += len(Results)
            ResultValues = list(Results)
            if len(ResultValues) != len(Requests):
                raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.PhysicalComponentAssemblyIncomplete if State.PhysicalAssemblyPlan is not None else Services.RoutingFailureReason.RuntimeBudgetExceeded, Stage=Stage, Detail='unbounded portal results do not align with the configured request domain', Diagnostics={'PortalRequestCount': len(Requests), 'PortalResultCount': len(ResultValues), 'GlobalPlanDomainComplete': False, 'CompleteAssignmentCutProof': False}))
            return (ResultValues, (True,) * len(Requests), False)

        def MaterializePortalRequestBatch(Metadata: list[tuple[str, Position3, int]], Results: list[Any], CompletionMask: tuple[bool, ...], Stage: str) -> None:
            CompletedEntries = Services.SelectCompletedPortalBatchEntries(Metadata, Results, CompletionMask)
            for PortalResultIndex, ((Signal, Terminal, Layer), Values) in enumerate(CompletedEntries):
                if not PortalNativeDeadlineExceeded and PortalResultIndex % 16 == 0:
                    State.CheckRuntimeBudget(Stage, {'ProcessedPortalRequests': PortalResultIndex, 'PortalRequestCount': len(Metadata)})
                Profile = State.Profiles[Signal]
                AccessPath = Profile.SourceAccessPath if Terminal == Profile.Root else Profile.TargetAccessPaths[Terminal]
                EffectiveValues = tuple((Value for Value in Values if Services.PortalPathRespectsOutwardAccess(Value.Path, AccessPath))) if Signal in State.TransactionalLeasePrescreenSignals else Values
                GeneratedRawPortals[Signal, Terminal, Layer] = tuple((Services._PortalFromRust(Signal, Terminal, Layer, Value, State.Resources) for Value in EffectiveValues))
                CompleteGeneratedPortalDomainKeys.add((Signal, Terminal, Layer))
        if State.PreparePhysicalComponentAssemblyOnly and (not State.Resources.PreparingPhysicalComponentGlobalChannels) and (State.UnboundOwnedSignalFrontierProofCallback is not None):
            OwnedTerminalPairs = State.ClosedComponentOwnedTerminalPairs
            AvailableOwnedPortalDomainKeys = {Key for Key, _Values in ReusableRawPortalEntries if (Key[0], Key[1]) in OwnedTerminalPairs} | {Key for Key in GeneratedRawPortals if (Key[0], Key[1]) in OwnedTerminalPairs} | {Key for Key in PortalRequestMetadata if (Key[0], Key[1]) in OwnedTerminalPairs}
            MissingOwnedConfiguredRequests = tuple(((Key, ConfiguredPortalRequestByMetadata[Key]) for Key in sorted(((Signal, Terminal, Layer) for Signal, Terminal in OwnedTerminalPairs for Layer in range(State.LayerCount))) if Key not in AvailableOwnedPortalDomainKeys and Key in ConfiguredPortalRequestByMetadata))
            if MissingOwnedConfiguredRequests:
                PortalRequestMetadata.extend((Key for Key, _Request in MissingOwnedConfiguredRequests))
                PortalRequests.extend((Request for _Key, Request in MissingOwnedConfiguredRequests))
            OwnedRequests, OwnedRequestMetadata, PortalRequests, PortalRequestMetadata = Services.PartitionPhysicalOwnedTerminalPortalRequests(PortalRequests, PortalRequestMetadata, OwnedTerminalPairs)
            RemainingOwnedRequests = list(OwnedRequests)
            RemainingOwnedRequestMetadata = list(OwnedRequestMetadata)
            PortalNativeDeadlineExceeded = False
            while RemainingOwnedRequests:
                OwnedResults, OwnedCompletionMask, OwnedDeadlineExceeded = GeneratePortalRequestBatch(RemainingOwnedRequests, 'PhysicalOwnedTerminalPortalEligibility')
                PortalNativeDeadlineExceeded = bool(PortalNativeDeadlineExceeded or OwnedDeadlineExceeded)
                MaterializePortalRequestBatch(RemainingOwnedRequestMetadata, OwnedResults, OwnedCompletionMask, 'PhysicalOwnedTerminalPortalMaterialization')
                if all(OwnedCompletionMask):
                    RemainingOwnedRequests = []
                    RemainingOwnedRequestMetadata = []
                    break
                IncompleteIndexes = tuple((Index for Index, Complete in enumerate(OwnedCompletionMask) if not Complete))
                if PortalNativeDeadlineExceeded or not IncompleteIndexes or len(IncompleteIndexes) == len(RemainingOwnedRequests):
                    break
                RemainingOwnedRequests = [RemainingOwnedRequests[Index] for Index in IncompleteIndexes]
                RemainingOwnedRequestMetadata = [RemainingOwnedRequestMetadata[Index] for Index in IncompleteIndexes]
            if not PortalNativeDeadlineExceeded:
                OwnedPortalDictionary = {Key: Values for Key, Values in ReusableRawPortalEntries if (Key[0], Key[1]) in OwnedTerminalPairs}
                OwnedPortalDictionary.update({Key: Values for Key, Values in GeneratedRawPortals.items() if (Key[0], Key[1]) in OwnedTerminalPairs})
                ExpectedOwnedPortalDomainKeys = frozenset(((Signal, Terminal, Layer) for Signal, Terminal in OwnedTerminalPairs for Layer in range(State.LayerCount)))
                PreparedOwnedPortalDomainKeys = frozenset(OwnedPortalDictionary)
                if PreparedOwnedPortalDomainKeys != ExpectedOwnedPortalDomainKeys:
                    raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalOwnedTerminalPortalEligibility', AffectedNets=tuple(sorted({Signal for Signal, _Terminal, _Layer in ExpectedOwnedPortalDomainKeys - PreparedOwnedPortalDomainKeys})), Detail='the owned-terminal portal slice is incomplete', Diagnostics={'OwnedTerminalPairCount': len(OwnedTerminalPairs), 'ExpectedPortalDomainKeyCount': len(ExpectedOwnedPortalDomainKeys), 'PreparedPortalDomainKeyCount': len(PreparedOwnedPortalDomainKeys), 'MissingPortalDomainKeys': [[Signal, list(Terminal), Layer] for Signal, Terminal, Layer in sorted(ExpectedOwnedPortalDomainKeys - PreparedOwnedPortalDomainKeys)], 'ReusedPortalSignals': sorted(ReusedPortalSignals), 'GeneratedPortalSignals': sorted(GeneratedPortalSignals), 'IncompleteClosedComponentReuseSignals': sorted(IncompleteClosedComponentReuseSignals), 'ConfiguredMissingRequestCount': len(MissingOwnedConfiguredRequests), 'CompleteAssignmentCutProof': False}))
                AccessPlacementFingerprint = State.ClusterInterfaceStateFingerprint or Services.BuildStableFingerprint(tuple(sorted(((Gate.X, Gate.Y, Gate.Z, str(Gate.Kind), Gate.Rotation, Gate.MirrorX) for Gate in State.Placed.PlacedGates))))
                EarlyAccessProblemStartedAt = Services.monotonic()
                PreparedAccessProblem = Services.BuildComponentRoutingProblem(Placed=State.Placed, Profiles=State.Profiles, RawPortals=OwnedPortalDictionary, PlacementFingerprint=AccessPlacementFingerprint, LocalTemplateFingerprint=State.ClusterInterfaceLocalRouteFingerprint, ResourceGraph=State.Resources.ResourceGraph)
                EarlyAccessProblemSeconds = Services.monotonic() - EarlyAccessProblemStartedAt
                State.UnboundOwnedSignalFrontierProofCallback(PreparedAccessProblem)
                UnboundOwnedSignalFrontierProofCompleted = True
                State.WorkTelemetry['PhysicalOwnedTerminalPortalEligibility'] = {'Complete': True, 'Feasible': True, 'OwnedTerminalPairCount': len(OwnedTerminalPairs), 'OwnedPortalDomainKeyCount': len(PreparedOwnedPortalDomainKeys), 'DeferredPortalRequestCount': len(PortalRequests), 'WholeDesignGuideIdentityPreserved': True, 'WholeDesignRegionIdentityPreserved': True}
        if State.TransactionalLeasePrescreenSignals and (not State.PreparePortalGeometryOnly):
            PrescreenRequestIndices = tuple((RequestIndex for RequestIndex, (Signal, _Terminal, _Layer) in enumerate(PortalRequestMetadata) if Signal in State.TransactionalLeasePrescreenSignals))
            PrescreenRequestIndexSet = frozenset(PrescreenRequestIndices)
            PrescreenRequests = [PortalRequests[RequestIndex] for RequestIndex in PrescreenRequestIndices]
            PrescreenRequestMetadata = [PortalRequestMetadata[RequestIndex] for RequestIndex in PrescreenRequestIndices]
            PrescreenResults, PrescreenCompletionMask, PrescreenDeadlineExceeded = GeneratePortalRequestBatch(PrescreenRequests, 'TransactionalLeasePrescreenPortal')
            PortalNativeDeadlineExceeded = PrescreenDeadlineExceeded
            MaterializePortalRequestBatch(PrescreenRequestMetadata, PrescreenResults, PrescreenCompletionMask, 'TransactionalLeasePrescreenPortalMaterialization')
            PrescreenGeneratedSignals = frozenset((Signal for Signal in State.TransactionalLeasePrescreenSignals if Signal in GeneratedPortalSignals))
            PrescreenGeneratedEntries = tuple(sorted(((Key, Values) for Key, Values in GeneratedRawPortals.items() if Key[0] in State.TransactionalLeasePrescreenSignals)))
            PrescreenRawPortalEntries = Services.MergeSignalScopedRawPortalEntries(ReusableRawPortalEntries, PrescreenGeneratedEntries, PrescreenGeneratedSignals) if PartialPortalCacheMatches and State.RawPortalCache is not None else PrescreenGeneratedEntries
            if not PortalNativeDeadlineExceeded:
                RunTransactionalLeasePrescreen(dict(PrescreenRawPortalEntries))
                PortalRequests = [Request for RequestIndex, Request in enumerate(PortalRequests) if RequestIndex not in PrescreenRequestIndexSet]
                PortalRequestMetadata = [Metadata for RequestIndex, Metadata in enumerate(PortalRequestMetadata) if RequestIndex not in PrescreenRequestIndexSet]
        if not PortalNativeDeadlineExceeded:
            PortalResults, PortalCompletionMask, PortalDeadlineExceeded = GeneratePortalRequestBatch(PortalRequests, 'Portal')
            PortalNativeDeadlineExceeded = PortalDeadlineExceeded
            MaterializePortalRequestBatch(PortalRequestMetadata, PortalResults, PortalCompletionMask, 'PortalMaterialization')
        State.WorkTelemetry['PortalCompletedWork'] = PortalCompletedWork
        State.WorkTelemetry['PortalBatchCount'] = PortalBatchCount
        State.WorkTelemetry['PortalNativeDeadlineExceeded'] = PortalNativeDeadlineExceeded
        PreservedIncompletePortalSignals = frozenset()
        if PortalNativeDeadlineExceeded and MissingExactCachePortalDomainKeys and PartialPortalCacheMatches and (State.RawPortalCache is not None) and (State.ResourceRawPortalReusePlan is not None) and (not State.ResourceRawPortalReusePlan.PortableAcrossPlacement):
            CachedRequestFingerprints = dict(State.RawPortalCache.PortalRequestDomainFingerprints)
            PreservedIncompletePortalSignals = Services.SelectMatchingPartialPortalReplaySignals(GeneratedPortalSignals, State.PortalRequestDomainFingerprintBySignal, CachedRequestFingerprints, State.ResourceRawPortalReusePlan.PortableAcrossPlacement)
            State.CompletePortalDomainKeys.update((Key for Key in State.RawPortalCache.CompletePortalDomainKeys if Key[0] in PreservedIncompletePortalSignals))
        CachedEntriesForPublication = tuple(((Key, Values) for Key, Values in ReusableRawPortalEntries if Key[0] in ReusedPortalSignals or Key[0] in PreservedIncompletePortalSignals))
        RawPortalEntries, PublishedCompletePortalDomainKeys = Services.MergePartialRawPortalBatchWork(CachedEntriesForPublication, tuple(sorted(GeneratedRawPortals.items())), State.CompletePortalDomainKeys, CompleteGeneratedPortalDomainKeys, GeneratedPortalSignals, PortalNativeDeadlineExceeded)
        State.CompletePortalDomainKeys = set(PublishedCompletePortalDomainKeys)
        State.WorkTelemetry['PortalPartialReplay'] = {'PreservedIncompleteSignals': sorted(PreservedIncompletePortalSignals), 'RequiresRequestDomainFingerprintMatch': True, 'PortableCarryForwardAllowed': False}
        if not PortalNativeDeadlineExceeded and State.FrozenPreparedPortalCache is not None and (not State.PrepareClusterInterfaceAssignmentOnly):
            FullPortalDictionary = dict(RawPortalEntries)
            FrozenPortalCount = 0
            for Key, FrozenValues in State.FrozenPreparedPortalCache.PortalEntries:
                ExistingValues = FullPortalDictionary.get(Key, ())
                SeenPortalIdentities = {(Value.PortalId, tuple(Value.Path), Value.Claims) for Value in ExistingValues}
                AddedValues = tuple((Value for Value in FrozenValues if (Value.PortalId, tuple(Value.Path), Value.Claims) not in SeenPortalIdentities))
                if not AddedValues:
                    continue
                FullPortalDictionary[Key] = (*ExistingValues, *AddedValues)
                FrozenPortalCount += len(AddedValues)
            RawPortalEntries = tuple(sorted(FullPortalDictionary.items()))
            State.WorkTelemetry['FrozenInterfacePortalHandoff'] = {'Applied': True, 'FrozenSignalCount': len({Key[0] for Key, _Values in State.FrozenPreparedPortalCache.PortalEntries}), 'MergedPortalCount': FrozenPortalCount, 'GlobalPortalEntryCount': len(RawPortalEntries)}
        RawPortals = dict(RawPortalEntries)
        if not PortalNativeDeadlineExceeded and getattr(State.Placed, 'RoutedComponentTemplates', ()):
            RawPortals, ForeignEscapeHandoffDiagnostics = Services.PreserveRoutedComponentForeignEscapes(State.Placed, RawPortals)
            RawPortalEntries = tuple(sorted(RawPortals.items()))
            State.WorkTelemetry['RoutedComponentForeignEscapeHandoff'] = ForeignEscapeHandoffDiagnostics
            RemovedComponentPortalSignals = frozenset((Key[0] for Key in RawPortals if Key[0] not in State.Profiles))
            RawPortals = {Key: Values for Key, Values in RawPortals.items() if Key[0] in State.Profiles}
            RawPortalEntries = tuple(sorted(RawPortals.items()))
            State.WorkTelemetry['RoutedComponentGlobalPortalScope'] = {'ProfileSignalCount': len(State.Profiles), 'RemovedComponentSignalCount': len(RemovedComponentPortalSignals), 'RemovedComponentSignals': sorted(RemovedComponentPortalSignals), 'RetainedPortalEntryCount': len(RawPortalEntries)}
        State.EffectiveRawPortalCache = Services.RawPortalGeometryCache(PlacementGeometryFingerprint=Services.BuildRawPortalPlacementGeometryFingerprint(State.Placed), ResourceGeometryFingerprint=Services.BuildRawPortalResourceGeometryFingerprint(State.Resources), PlacedReference=State.Placed, ResourcesReference=State.Resources, Region=State.Region, LayerCount=State.LayerCount, PortalLimit=State.PortalLimit, PortalVariantCounts=tuple(sorted(State.RawPortalVariantCounts.items())), GuideExpansion=State.Policy.DetailedRouting.GuideExpansion, StrictMaximumExpansions=State.Policy.DetailedRouting.StrictMaximumExpansions, Context=State.Context, AssignmentIndexed=State.Resources.ResourceGraph.BuildIndexedGraph(State.Region), PortalEntries=RawPortalEntries, RequestCount=sum(SignalRequestCounts.values()), TargetCount=int(State.WorkTelemetry['PortalTargetCount']), StarvationCount=PortalStarvationCount, AccessGeometryFingerprint=State.PortalAccessGeometryFingerprint, AssignedColumns=State.EffectiveAssignedColumns, ReservedAccess=State.ReservedAccess, GuidePlanPrepared=True, GuideInputFingerprint=State.GuideInputFingerprint, GuidePlan=State.CoarsePlan, SignalRequestCounts=tuple(sorted(SignalRequestCounts.items())), SignalTargetCounts=tuple(sorted(SignalTargetCounts.items())), SignalStarvationCounts=tuple(sorted(SignalStarvationCounts.items())), RetainedPortfolioSliceLimited=State.PortalSliceLimited or State.RetainedPortfolioPortalProfileFrozen, PhysicalGlobalKeepoutFingerprint=State.PhysicalGlobalKeepoutFingerprint, CompletePortalDomainKeys=tuple(sorted(State.CompletePortalDomainKeys)), PolicyCompleteEmptyPortalDomainKeys=tuple(sorted(PolicyCompleteEmptyPortalDomainKeys)), PortalRequestDomainFingerprints=tuple(sorted(State.PortalRequestDomainFingerprintBySignal.items())), ExteriorRegionFingerprint=State.PhysicalExteriorRegionFingerprint, AuthoritativeResourceGraphFingerprint=Services.BuildPhysicalExteriorResourceGraphFingerprint(State.Resources.ResourceGraph, State.PhysicalExteriorRegionFingerprint, State.Region), ConfiguredPortalRequests=tuple((Request for _Metadata, Request in sorted(ConfiguredPortalRequestByMetadata.items()))), ConfiguredPortalRequestMetadata=tuple((Metadata for Metadata in sorted(ConfiguredPortalRequestByMetadata))))
    Services.RetainRawPortalGeometryCache(State.Resources, State.EffectiveRawPortalCache)
    if not CacheMatches and PortalNativeDeadlineExceeded:
        Services.EnforceRoutingRuntimeLimit(Deadline=State.Deadline, AdaptiveStartedAt=State.RoutingStarted, AdaptiveExpiresAt=State.AdaptiveExpiresAt, Stage='Portal', Diagnostics={**State.CurrentRuntimeBudgetDiagnostics(), 'PortalCompletedWork': PortalCompletedWork, 'PortalRequestCount': len(PortalRequests), 'PortalCompleteKeyCount': len(State.EffectiveRawPortalCache.CompletePortalDomainKeys), 'PartialRawPortalCachePublished': True}, NativeDeadlineExceeded=True)
    if State.PreparePhysicalComponentAssemblyOnly and (not State.Resources.PreparingPhysicalComponentGlobalChannels):
        AccessPlacementFingerprint = State.ClusterInterfaceStateFingerprint or Services.BuildStableFingerprint(tuple(sorted(((Gate.X, Gate.Y, Gate.Z, str(Gate.Kind), Gate.Rotation, Gate.MirrorX) for Gate in State.Placed.PlacedGates))))
        AccessProblemStartedAt = Services.monotonic()
        if PreparedAccessProblem is None:
            PreparedAccessProblem = Services.BuildComponentRoutingProblem(Placed=State.Placed, Profiles=State.Profiles, RawPortals=RawPortals, PlacementFingerprint=AccessPlacementFingerprint, LocalTemplateFingerprint=State.ClusterInterfaceLocalRouteFingerprint, ResourceGraph=State.Resources.ResourceGraph)
        AccessProblemSeconds = EarlyAccessProblemSeconds + Services.monotonic() - AccessProblemStartedAt
        if State.UnboundOwnedSignalFrontierProofCallback is not None and (not UnboundOwnedSignalFrontierProofCompleted):
            State.UnboundOwnedSignalFrontierProofCallback(PreparedAccessProblem)
        ComponentGraphFingerprint = str(getattr(getattr(State.Placed, 'ComponentGraph', None), 'StructuralFingerprint', ''))
        AccessCertificateStartedAt = Services.monotonic()
        PreparedAccessCertificate = Services.BuildComponentCutAccessFeasibilityCertificate(PreparedAccessProblem, State.Resources.ResourceGraph, LayerCount=State.LayerCount, MinimumPlacementY=State.MinimumY, ComponentGraphFingerprint=ComponentGraphFingerprint, RequiredLayerBySignal={Port.Signal: int(State.CoarsePlan.Layers.get(Port.Signal, 0)) for Port in PreparedAccessProblem.Interface.Ports}, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('ComponentAccessCertification', Diagnostics))
        AccessCertificateSeconds = Services.monotonic() - AccessCertificateStartedAt
        State.Resources.PreparedPhysicalComponentUnboundProblem = PreparedAccessProblem
        State.Resources.PreparedComponentAccessCertificate = PreparedAccessCertificate
        State.WorkTelemetry['ComponentAccessCertificate'] = PreparedAccessCertificate.ToDictionary()
        if not PreparedAccessCertificate.Complete:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAccessCertificationIncomplete, Stage='ComponentAccessCertification', AffectedNets=PreparedAccessCertificate.AffectedSignals, Detail='the local component access domain was not completed', Diagnostics=PreparedAccessCertificate.ToDictionary()))
        if not PreparedAccessCertificate.Feasible:
            AccessFailureReason = Services.RoutingFailureReason.ComponentPerimeterSeamUnsatisfiable if 'seam' in PreparedAccessCertificate.ProofKind else Services.RoutingFailureReason.ComponentTerminalAccessUnsatisfiable
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=AccessFailureReason, Stage='ComponentAccessCertification', AffectedNets=PreparedAccessCertificate.AffectedSignals, Detail='the placed component cut has no complete local terminal-to-perimeter access contract', Diagnostics=PreparedAccessCertificate.ToDictionary()))
        PhysicalExteriorIngressNodes = frozenset((tuple(Candidate.Attachment) for Domain in PreparedAccessCertificate.PortDomains for Candidate in Domain.Candidates))
        PhysicalExteriorIngressColumns = frozenset(((Position[0], Position[2]) for Position in PhysicalExteriorIngressNodes))
        EnvelopeMinimum = PreparedAccessCertificate.EnvelopeMinimum
        EnvelopeMaximum = PreparedAccessCertificate.EnvelopeMaximum
        MinimumEnvelopeX = int(EnvelopeMinimum[0])
        MaximumEnvelopeX = int(EnvelopeMaximum[0])
        MinimumEnvelopeZ = int(EnvelopeMinimum[2])
        MaximumEnvelopeZ = int(EnvelopeMaximum[2])
        PhysicalExteriorPerimeterColumns = frozenset((*((X, MinimumEnvelopeZ - 1) for X in range(MinimumEnvelopeX - 1, MaximumEnvelopeX + 2)), *((X, MaximumEnvelopeZ + 1) for X in range(MinimumEnvelopeX - 1, MaximumEnvelopeX + 2)), *((MinimumEnvelopeX - 1, Z) for Z in range(MinimumEnvelopeZ, MaximumEnvelopeZ + 1)), *((MaximumEnvelopeX + 1, Z) for Z in range(MinimumEnvelopeZ, MaximumEnvelopeZ + 1))))
        State.EffectiveAssignedColumns = frozenset((*State.EffectiveAssignedColumns, *PhysicalExteriorIngressColumns, *PhysicalExteriorPerimeterColumns))
        State.ReservedAccess = frozenset((*State.ReservedAccess, *PhysicalExteriorIngressNodes))
        DiscoveryBounds = State.Bounds
        ExteriorClosureColumns = frozenset((*PhysicalExteriorIngressColumns, *PhysicalExteriorPerimeterColumns))
        if ExteriorClosureColumns or PhysicalExteriorIngressNodes:
            State.Bounds = (min(DiscoveryBounds[0], *(Column[0] for Column in ExteriorClosureColumns)), max(DiscoveryBounds[1], *(Column[0] for Column in ExteriorClosureColumns)), min(DiscoveryBounds[2], *(Position[1] for Position in PhysicalExteriorIngressNodes)), max(DiscoveryBounds[3], *(Position[1] for Position in PhysicalExteriorIngressNodes)), min(DiscoveryBounds[4], *(Column[1] for Column in ExteriorClosureColumns)), max(DiscoveryBounds[5], *(Column[1] for Column in ExteriorClosureColumns)))
        ExteriorRegionStartedAt = Services.monotonic()
        State.Region = State.Resources.ResourceGraph.BuildRegion(State.Bounds, AllowedColumns=State.EffectiveAssignedColumns, AllowedAccess=State.ReservedAccess, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('PhysicalExteriorResourceGraph', Diagnostics))
        State.PhysicalExteriorRegionFingerprint = Services.BuildStableFingerprint(('physical-exterior-routing-region-v1', State.Bounds, tuple(sorted(State.EffectiveAssignedColumns)), tuple(sorted(State.ReservedAccess)), getattr(State.Resources.ResourceGraph, 'GraphVersion', ''), getattr(State.Technology, 'TechnologyVersion', ''), repr(State.Technology)))
        ClosedRegionNodes = frozenset(State.Region.Nodes)
        ClosureAddedNodes = frozenset((Position for Position in ClosedRegionNodes if (Position[0], Position[2]) in ExteriorClosureColumns or Position in PhysicalExteriorIngressNodes))
        ClosureRequestItems = tuple(sorted(ConfiguredPortalRequestByMetadata.items()))
        ClosedPortalEntries = dict(State.EffectiveRawPortalCache.PortalEntries)
        ClosedPolicyCompleteEmptyPortalDomainKeys = tuple(sorted(getattr(State.EffectiveRawPortalCache, 'PolicyCompleteEmptyPortalDomainKeys', ())))
        (ClosedCompletePortalDomainKeys): tuple[tuple[str, Services.Position3, int], ...] = ClosedPolicyCompleteEmptyPortalDomainKeys
        (ClosedPortalRequestFingerprints): tuple[tuple[str, str], ...] = ()
        ClosedPortalDomainComplete = False
        ClosureContext = Services.RustRoutingContext(State.Bounds, (State.MinimumX, State.MaximumX, State.MinimumZ, State.MaximumZ), sorted(State.Region.Nodes), sorted(State.Region.Edges))
        ClosedRequests = []
        for Metadata, Request in ClosureRequestItems:
            Signal, Terminal, _Layer = Metadata
            Profile = State.Profiles[Signal]
            AccessPath = Profile.SourceAccessPath if Terminal == Profile.Root else Profile.TargetAccessPaths[Terminal]
            Starts, _Targets, AllowedNodes, RoutingY, CandidateLimit, MaximumExpansions = Request
            ClosedAllowedNodes = tuple(sorted(frozenset((*AllowedNodes, *ClosureAddedNodes)) & ClosedRegionNodes))
            ClosedTargets = tuple(sorted((Position for Position in ClosedAllowedNodes if Position[1] == int(RoutingY)), key=lambda Position: (min((abs(Position[0] - AccessPosition[0]) + abs(Position[1] - AccessPosition[1]) + abs(Position[2] - AccessPosition[2]) for AccessPosition in AccessPath)), abs(Position[0] - AccessPath[-1][0]), abs(Position[2] - AccessPath[-1][2]), Position)))
            ClosedTargets = ClosedTargets[:max(1, int(CandidateLimit))]
            ClosedRequests.append((list(Starts), list(ClosedTargets), list(ClosedAllowedNodes), int(RoutingY), int(CandidateLimit), int(MaximumExpansions)))
        if ClosureRequestItems:
            if hasattr(ClosureContext, 'GeneratePortalCandidateBatchesBounded'):
                ClosedBatchResult = ClosureContext.GeneratePortalCandidateBatchesBounded(ClosedRequests, Services.RemainingRoutingRuntimeMilliseconds(State.Deadline, State.AdaptiveExpiresAt))
                ClosedResults, ClosedCompletionMask = Services.ReadPortalBatchCandidatesAndCompletionMask(ClosedBatchResult, len(ClosedRequests))
                ClosedDeadlineExceeded = bool(ClosedBatchResult.DeadlineExceeded)
            else:
                ClosedResults = list(ClosureContext.GeneratePortalCandidateBatches(ClosedRequests))
                ClosedCompletionMask = (True,) * len(ClosedRequests)
                ClosedDeadlineExceeded = False
            ClosedPortalDomainComplete = bool(not ClosedDeadlineExceeded and len(ClosedResults) == len(ClosureRequestItems) and (len(ClosedCompletionMask) == len(ClosureRequestItems)) and all(ClosedCompletionMask))
            if ClosedPortalDomainComplete:
                for (Signal, Terminal, Layer), Values in zip((Value[0] for Value in ClosureRequestItems), ClosedResults):
                    Profile = State.Profiles[Signal]
                    AccessPath = Profile.SourceAccessPath if Terminal == Profile.Root else Profile.TargetAccessPaths[Terminal]
                    EffectiveValues = tuple((Value for Value in Values if Services.PortalPathRespectsOutwardAccess(Value.Path, AccessPath))) if Signal in State.TransactionalLeasePrescreenSignals else Values
                    ClosedPortalEntries[Signal, Terminal, Layer] = tuple((Services._PortalFromRust(Signal, Terminal, Layer, Value, State.Resources) for Value in EffectiveValues))
                ClosedCompletePortalDomainKeys = Services.MergePostClosurePortalCompletionKeys(ClosedPolicyCompleteEmptyPortalDomainKeys, (Metadata for Metadata, _Request in ClosureRequestItems))
                ClosedPortalRequestFingerprints = tuple(sorted(((Signal, Services.BuildStableFingerprint(('post-closure-portal-request-domain-v1', Signal, State.PhysicalExteriorRegionFingerprint, tuple((Key for Key in ClosedPolicyCompleteEmptyPortalDomainKeys if Key[0] == Signal)), tuple((Request for (RequestSignal, _Terminal, _Layer), Request in zip((Value[0] for Value in ClosureRequestItems), ClosedRequests) if RequestSignal == Signal))))) for Signal in {Metadata[0] for Metadata, _Request in ClosureRequestItems})))
        State.EffectiveRawPortalCache = Services.replace(State.EffectiveRawPortalCache, Region=State.Region, Context=ClosureContext, AssignmentIndexed=State.Resources.ResourceGraph.BuildIndexedGraph(State.Region), AssignedColumns=State.EffectiveAssignedColumns, ReservedAccess=State.ReservedAccess, PortalEntries=tuple(sorted(ClosedPortalEntries.items())), CompletePortalDomainKeys=ClosedCompletePortalDomainKeys, PolicyCompleteEmptyPortalDomainKeys=ClosedPolicyCompleteEmptyPortalDomainKeys, PortalRequestDomainFingerprints=ClosedPortalRequestFingerprints, ExteriorRegionFingerprint=State.PhysicalExteriorRegionFingerprint if ClosedPortalDomainComplete else '', AuthoritativeResourceGraphFingerprint=Services.BuildPhysicalExteriorResourceGraphFingerprint(State.Resources.ResourceGraph, State.PhysicalExteriorRegionFingerprint, State.Region) if ClosedPortalDomainComplete else '', ConfiguredPortalRequests=tuple(ClosedRequests), ConfiguredPortalRequestMetadata=tuple((Metadata for Metadata, _Request in ClosureRequestItems)))
        RawPortalEntries = State.EffectiveRawPortalCache.PortalEntries
        RawPortals = State.EffectiveRawPortalCache.BuildPortalDictionary()
        State.WorkTelemetry['PhysicalExteriorRegionClosure'] = {'Applied': True, 'IngressNodeCount': len(PhysicalExteriorIngressNodes), 'IngressColumnCount': len(PhysicalExteriorIngressColumns), 'PerimeterColumnCount': len(PhysicalExteriorPerimeterColumns), 'CertificateFingerprint': PreparedAccessCertificate.CertificateFingerprint, 'DiscoveryBounds': list(DiscoveryBounds), 'AuthoritativeBounds': list(State.Bounds), 'BoundsExpanded': State.Bounds != DiscoveryBounds, 'RegionFingerprint': State.PhysicalExteriorRegionFingerprint, 'RegionNodeCount': len(State.Region.Nodes), 'RegionEdgeCount': len(State.Region.Edges), 'PortalDomainRegenerated': True, 'PortalDomainComplete': ClosedPortalDomainComplete, 'PortalRequestCount': len(ClosedRequests), 'PolicyCompleteEmptyPortalDomainCount': len(ClosedPolicyCompleteEmptyPortalDomainKeys), 'Seconds': Services.monotonic() - ExteriorRegionStartedAt}
        Services.RetainRawPortalGeometryCache(State.Resources, State.EffectiveRawPortalCache)
    if State.PrepareComponentRoutingProblemOnly:
        Problem = Services.BuildComponentRoutingProblem(Placed=State.Placed, Profiles=State.Profiles, RawPortals=RawPortals, PlacementFingerprint=State.ClusterInterfaceStateFingerprint or Services.BuildStableFingerprint(tuple(sorted(((Gate.X, Gate.Y, Gate.Z, str(Gate.Kind), Gate.Rotation, Gate.MirrorX) for Gate in State.Placed.PlacedGates)))), LocalTemplateFingerprint=State.ClusterInterfaceLocalRouteFingerprint, ResourceGraph=State.Resources.ResourceGraph)
        State.Resources.PreparedComponentRoutingProblem = Problem
        raise Services.ComponentRoutingProblemPrepared(Problem)
    if not TransactionalLeasePrescreenCompleted:
        RunTransactionalLeasePrescreen(RawPortals)
    if State.PreparePortalGeometryOnly:
        raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.Stagnated, Stage='PortalGeometryPreparation', Detail='immutable portal geometry prepared for lease ownership states', Diagnostics={'RawPortalCachePublished': True, 'GlobalGuidePlanCacheHit': State.ReuseCachedGuidePlan, 'PortalCacheHit': CacheMatches, 'PortalRequestCount': State.WorkTelemetry['PortalRequestCount'], 'PortalTargetCount': State.WorkTelemetry['PortalTargetCount']}))
    State.UnreservedPortalMode = State.UseNegotiatedPortalDomain or not State.Policy.AdaptiveRouting.Enabled or State.SkipStrictPortalReservation
    if State.RepeaterReadyPortalRepairSignals:
        State.PreparedPortalCache = None
    if State.PreparedPortalCache is None and (not State.ClusterPinBankCandidateDomainOffsets) and (not State.ClusterLeaseCandidateRealizabilityNogoods) and (not State.RepeaterReadyPortalRepairSignals):
        State.PreparedPortalCache = Services.SelectPreparedPortalDomainCache(State.Resources.PreparedPortalDomainCaches, State.EffectiveRawPortalCache, State.UnreservedPortalMode, State.ReservationVariant)
    PreparedCacheMatches = bool(State.PreparedPortalCache is not None and (State.FrozenPreparedPortalCache is State.PreparedPortalCache or State.PreparedPortalCache.Matches(State.EffectiveRawPortalCache, State.UnreservedPortalMode, State.ReservationVariant)))
    if PreparedCacheMatches:
        assert State.PreparedPortalCache is not None
        State.Portals = State.PreparedPortalCache.BuildPortalDictionary()
        State.PortalReservations = State.PreparedPortalCache.Reservations
        EffectivePreparedPortalCache = State.PreparedPortalCache
    else:
        State.Portals = dict(RawPortals)
        if State.RepeaterReadyPortalRepairSignals:
            State.Portals, RepeaterReadyPortalTelemetry = Services.BuildRepeaterReadyPortalDomains(State.Portals, State.RepeaterReadyPortalRepairSignals, State.Region, State.Resources, ExtensionLength=State.RepeaterReadyPortalExtensionLength, MaximumExtensionsPerPortal=State.RepeaterReadyPortalMaximumExtensions)
            State.WorkTelemetry['RepeaterReadyPortalRepair'] = RepeaterReadyPortalTelemetry
        if State.BoundaryLeaseSignals and (not State.DeferClusterBoundaryLeaseUntilCapacityPrecheck):
            LeasePortals = {Key: Values for Key, Values in State.Portals.items() if (Key[0], Key[1]) in State.BoundaryLeaseTerminalPairs}
            LeaseStateCount = 1
            (LeaseStates): list[tuple[dict[tuple[str, Services.Position3, int], tuple[Services.PinAccessPortal, ...]], tuple[Services.PortalReservation, ...]]] = []
            (LeaseOwnershipFingerprints): set[str] = set()
            for LeaseVariant in range(LeaseStateCount):
                State.CheckRuntimeBudget('ClusterBoundaryLease', {'Phase': 'portfolio-materialization', 'Variant': LeaseVariant, 'MaximumVariants': LeaseStateCount})
                ReservedLeasePortals, LeaseReservations = Services.ReserveClusterBoundaryLeases(LeasePortals, State.Profiles, State.Resources, ReservationVariant=State.ReservationVariant + LeaseVariant, MaximumExpansions=State.AdaptiveBudget.AssignmentExpansions, UseCompleteClusterInterfaceAccess=State.CompleteClusterInterfaceAccess, RequireCompleteClusterInterfaceDomain=State.RequireCompleteClusterInterfaceDomain, RequiredInterfaceLayer=State.InterfaceDeckLayer, SignalCandidateDomainOffsets=State.ClusterPinBankCandidateDomainOffsets, CandidateRealizabilityNogoods=(*State.ClusterLeaseCandidateRealizabilityNogoods, *State.ClusterInterfaceRealizabilityNogoods), ForbiddenOwnershipAssignmentFingerprints=State.ForbiddenClusterInterfaceAssignmentFingerprints, RequiredPatternFingerprintsBySignal=State.ClusterInterfaceFrozenPatternFingerprints, RequiredReservations=State.ClusterInterfaceFrozenReservations, PriorityInterfaceCutSignals=State.PriorityInterfaceCutSignals, WorkCheck=lambda Details: State.CheckRuntimeBudget('ClusterBoundaryLease', Details))
                OwnershipFingerprint = Services.BuildStableFingerprint(tuple(((Value.Signal, Value.Terminal, Value.Layer, Value.PortalId, Value.FirstSegment) for Value in LeaseReservations)))
                if OwnershipFingerprint in LeaseOwnershipFingerprints:
                    continue
                LeaseOwnershipFingerprints.add(OwnershipFingerprint)
                LeaseStates.append((ReservedLeasePortals, LeaseReservations))
            if not LeaseStates:
                raise RuntimeError('cluster-boundary lease portfolio is empty')
            State.Portals.update(LeaseStates[0][0])
            State.PortalReservations = LeaseStates[0][1]
            State.WorkTelemetry['ClusterBoundaryLeases'] = {**dict(State.WorkTelemetry['ClusterBoundaryLeases']), 'Status': 'reserved', 'ReservationCount': len(State.PortalReservations), 'PortfolioStateCount': len(LeaseStates), 'ActivePortfolioStateIndex': 0, 'PortfolioOwnershipFingerprints': sorted(LeaseOwnershipFingerprints), 'Layers': {str(Layer): sum((Value.Layer == Layer for Value in State.PortalReservations)) for Layer in sorted({Value.Layer for Value in State.PortalReservations})}, 'OwnershipFingerprint': Services.BuildStableFingerprint(tuple(((Value.Signal, Value.Terminal, Value.Layer, Value.PortalId, Value.FirstSegment) for Value in State.PortalReservations))), 'PinBankRepair': {'Signals': sorted(State.ClusterPinBankRepairSignals), 'CandidateDomainOffsets': dict(sorted(State.ClusterPinBankCandidateDomainOffsets.items())), 'ProfileFingerprint': State.ClusterPinBankRepairFingerprint}, 'CandidateRealizabilityNogoods': [Nogood.ToDictionary() for Nogood in (*State.ClusterLeaseCandidateRealizabilityNogoods, *State.ClusterInterfaceRealizabilityNogoods)]}
            State.Resources.ClusterBoundaryLeaseOwnershipFingerprints[State.ReservationVariant] = str(State.WorkTelemetry['ClusterBoundaryLeases']['OwnershipFingerprint'])
        elif State.BoundaryLeaseSignals:
            State.WorkTelemetry['ClusterBoundaryLeases'] = {**dict(State.WorkTelemetry['ClusterBoundaryLeases']), 'Status': 'deferred-for-capacity-repair-precheck', 'DeferredUntil': 'local-disjoint-seam-reservation'}
            State.PortalReservations = ()
        elif State.UnreservedPortalMode:
            State.Portals = {Key: tuple(sorted(Value, key=lambda Value: (Value.Cost, Value.PortalId))) for Key, Value in sorted(State.Portals.items())}
            State.PortalReservations = ()
        elif State.UseNegotiatedRouting:
            State.Portals, State.PortalReservations = Services.ReserveNegotiatedBoundaryEscapes(State.Portals, State.Profiles, State.Resources, ReservationVariant=State.ReservationVariant, MaximumExpansions=State.AdaptiveBudget.AssignmentExpansions, WorkCheck=lambda Details: State.CheckRuntimeBudget('PortalReservation', Details))
        else:
            State.Portals, State.PortalReservations = Services.ReserveBoundaryPortals(State.Portals, ReservationVariant=State.ReservationVariant, MaximumExpansions=State.AdaptiveBudget.AssignmentExpansions, RequireConflictFree=False, StrictTerminalThreshold=4)
        EffectivePreparedPortalCache = Services.PreparedPortalDomainCache(RawPortalCache=State.EffectiveRawPortalCache, UnreservedPortalMode=State.UnreservedPortalMode, ReservationVariant=State.ReservationVariant, PortalEntries=tuple(sorted(State.Portals.items())), Reservations=State.PortalReservations)
    if State.PlacementAccessFabric is not None and State.PlacementAccessAssignment is not None:
        State.Portals = Services.ApplyPlacementAccessAssignmentPortalDomains(State.Portals, State.PlacementAccessFabric, State.PlacementAccessAssignment, State.Resources.ResourceGraph, State.Technology, State.MinimumY, State.LayerCount)
        PlacementAccessTerminalKeys = frozenset(((str(Signal), tuple(Terminal)) for Signal, Terminal, _StubIndex in State.PlacementAccessAssignment.SelectedStubIndices))
        State.PortalReservations = tuple((Reservation for Reservation in State.PortalReservations if (Reservation.Signal, Reservation.Terminal) not in PlacementAccessTerminalKeys)) + tuple((Services.PortalReservation(Signal=Signal, Terminal=Terminal, Layer=Layer, SlotIndex=0, PortalId=Values[0].PortalId, Claims=Values[0].Claims, Purpose='placement-access-fabric', FirstSegment=Values[0].Path) for (Signal, Terminal, Layer), Values in sorted(State.Portals.items()) if (Signal, Terminal) in PlacementAccessTerminalKeys))
    elif State.PlacementAccessFabric is not None:
        State.Portals = Services.ApplyPlacementAccessFabricPortalDomains(State.Portals, State.PlacementAccessFabric, State.Resources.ResourceGraph, State.Technology, State.MinimumY, State.LayerCount)
        FabricTerminalKeys = frozenset(((str(Domain.Signal), tuple(Domain.Terminal)) for Domain in State.PlacementAccessFabric.TerminalDomains))
        State.PortalReservations = tuple((Reservation for Reservation in State.PortalReservations if (Reservation.Signal, Reservation.Terminal) not in FabricTerminalKeys))
    State.ExactAttachmentDiagnostics: dict[str, object] = {}
    if State.PhysicalAssemblyPlan is not None and (not State.PreparePhysicalComponentAssemblyOnly) and State.Resources.PreparingPhysicalComponentGlobalChannels:
        State.Portals = Services.ApplyPhysicalComponentAssemblyPortalDomains(State.Portals, State.PhysicalAssemblyPlan, State.Resources.ResourceGraph)
        State.ExactAttachmentDiagnostics = Services.ValidatePhysicalComponentExactAttachmentPortals(State.Profiles, State.Portals, State.PhysicalAssemblyPlan, State.LayerCount)
        PortSignalTerminals = frozenset(((Port.Signal, Port.Attachment) for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan)))
        State.PortalReservations = tuple((Reservation for Reservation in State.PortalReservations if (Reservation.Signal, Reservation.Terminal) not in PortSignalTerminals))
        EffectivePreparedPortalCache = Services.PreparedPortalDomainCache(RawPortalCache=State.EffectiveRawPortalCache, UnreservedPortalMode=State.UnreservedPortalMode, ReservationVariant=State.ReservationVariant, PortalEntries=tuple(sorted(State.Portals.items())), Reservations=State.PortalReservations)
        State.WorkTelemetry['PhysicalComponentExactPortals'] = {**State.ExactAttachmentDiagnostics, 'PlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint, 'PortCount': len(Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan)), 'PortalIds': [Values[0].PortalId for Key, Values in sorted(State.Portals.items()) if (Key[0], Key[1]) in PortSignalTerminals]}
    State.WorkTelemetry['PreparedPortalDomainResourceCacheSelected'] = PreparedCacheMatches
    if State.PreparePhysicalComponentAssemblyOnly and (not State.Resources.PreparingPhysicalComponentGlobalChannels):
        PlacementFingerprint = State.ClusterInterfaceStateFingerprint or Services.BuildStableFingerprint(tuple(sorted(((Gate.X, Gate.Y, Gate.Z, str(Gate.Kind), Gate.Rotation, Gate.MirrorX) for Gate in State.Placed.PlacedGates))))
        assert PreparedAccessProblem is not None
        assert PreparedAccessCertificate is not None
        Problem = PreparedAccessProblem
        PortFactorDomainStartedAt = Services.monotonic()
        Preparation = Services.PreparePhysicalComponentPortFactorDomain(State.Placed, Problem, State.CoarsePlan, State.Resources, LayerCount=min(State.LayerCount, State.Technology.MaximumRoutableLayerCount), AccessCertificate=PreparedAccessCertificate, AuthoritativeRegion=State.Region, AuthoritativeRegionFingerprint=State.PhysicalExteriorRegionFingerprint, Profiles=State.Profiles, FrozenComponentClaims=State.FrozenComponentClaims, TechnologyFingerprint=Services.BuildStableFingerprint(repr(State.Technology)), WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('PhysicalComponentAssembly', Diagnostics))
        State.Resources.FrozenPhysicalComponentPostClosurePortalHandoff = Services.FrozenPhysicalComponentPostClosurePortalHandoff(PreparationDomainFingerprint=Preparation.DomainFingerprint, PlacementFingerprint=Preparation.PlacementFingerprint, ComponentGraphFingerprint=Preparation.ComponentGraphFingerprint, ResourceGraphFingerprint=Preparation.ResourceGraphFingerprint, ExteriorRegionFingerprint=Preparation.ExteriorRegionFingerprint, RawPortalGeometryCache=State.EffectiveRawPortalCache)
        MandatoryPortalFactorDomains = Services.BuildPhysicalBoundaryMandatoryPortalFactorDomains(Preparation, State.Profiles, State.EffectiveRawPortalCache, State.Resources.ResourceGraph, State.FrozenComponentClaims)
        MandatoryPortalFactorDiagnostics = Services.PublishPhysicalBoundaryMandatoryPortalFactorDomains(State.Resources, MandatoryPortalFactorDomains)
        PortFactorDomainSeconds = Services.monotonic() - PortFactorDomainStartedAt
        Preparation = Services.replace(Preparation, PreparationStageTimings=(('ComponentProblemConstruction', AccessProblemSeconds), ('ComponentAccessCertification', AccessCertificateSeconds), ('PhysicalPortFactorPreparation', PortFactorDomainSeconds)))
        State.Resources.PreparedPhysicalComponentPortFactorDomain = Preparation
        State.Resources.PreparedPhysicalComponentUnboundProblem = Problem
        State.Resources.PreparedComponentAccessCertificate = PreparedAccessCertificate
        State.Resources.FrozenPhysicalComponentGlobalGuidePlan = State.CoarsePlan
        Services.RetainPreparedPortalDomainCache(State.Resources, EffectivePreparedPortalCache)
        State.WorkTelemetry['PhysicalComponentPortFactorDomain'] = {'DomainFingerprint': Preparation.DomainFingerprint, 'Complete': Preparation.Complete, 'Feasible': Preparation.Feasible, 'FeedthroughEndpointDomains': [Domain.ToDictionary() for Domain in Preparation.FeedthroughEndpointDomains], 'LaneFactorExpansionCount': Preparation.LaneFactorExpansionCount, 'AccessFactorExpansionCount': Preparation.AccessFactorExpansionCount, 'SeamFactorExpansionCount': Preparation.SeamFactorExpansionCount, 'ExteriorFixedClaimCertificateCount': len(Preparation.ExteriorFixedClaimCertificates), 'ExteriorFixedClaimRejectedApertureCount': sum((Certificate.Complete and (not Certificate.Feasible) for Certificate in Preparation.ExteriorFixedClaimCertificates)), 'MandatoryPortalFactorDomains': MandatoryPortalFactorDiagnostics, 'ImplicitForeignTransitDomainCount': 0, 'PreparationStageTimings': dict(Preparation.PreparationStageTimings)}
        if State.PreparePhysicalComponentPortFactorDomainOnly:
            return PhaseOutcome(Returned=True, Value=Preparation)
        Assembly = Services.SolvePreparedPhysicalComponentPortFactorDomain(Preparation, State.Resources, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('PhysicalComponentAssembly', Diagnostics))
        State.Resources.PreparedComponentRoutingProblem = Assembly.Problem
        State.Resources.PreparedPhysicalComponentAssembly = Assembly
        State.Resources.FrozenPhysicalComponentGlobalGuidePlan = Assembly.GlobalGuidePlan
        State.WorkTelemetry['PhysicalComponentAssembly'] = {**Assembly.Plan.ToDictionary(), 'GlobalGuidePlanCacheHit': State.ReuseCachedGuidePlan, 'PortalCacheHit': CacheMatches, 'ImplicitForeignTransitDomainCount': 0}
        raise Services.PhysicalComponentAssemblyPrepared(Assembly)
    State.BoundaryLeaseReservations = tuple((Value for Value in State.PortalReservations if Value.Purpose == 'cluster-boundary-lease'))
    if State.Resources.PreparedClusterInterfaceAssignment is not None:
        PlacementVariantFingerprint = Services.BuildStableFingerprint(tuple(sorted(((Gate.X, Gate.Y, Gate.Z, str(Gate.Kind), Gate.Rotation) for Gate in State.Placed.PlacedGates))))
        OwnershipFingerprint = Services.BuildClusterInterfaceReservationAssignmentFingerprint(State.BoundaryLeaseReservations)
        CurrentAssignment = State.Resources.PreparedClusterInterfaceAssignment
        BoundProblem = Services.replace(CurrentAssignment.Problem, PlacementVariantFingerprint=PlacementVariantFingerprint, OwnershipFingerprint=OwnershipFingerprint)
        OwnershipAssignmentFingerprint = CurrentAssignment.OwnershipAssignmentFingerprint or CurrentAssignment.AssignmentFingerprint
        FullAssignmentFingerprint = Services.BuildStableFingerprint((State.ClusterInterfaceStateFingerprint or PlacementVariantFingerprint, State.ClusterInterfaceLocalRouteFingerprint, OwnershipAssignmentFingerprint))
        CurrentAssignment = Services.replace(CurrentAssignment, Problem=BoundProblem, AssignmentFingerprint=FullAssignmentFingerprint, OwnershipAssignmentFingerprint=OwnershipAssignmentFingerprint)
        State.Resources.PreparedClusterInterfaceAssignment = CurrentAssignment
        State.WorkTelemetry['InterfaceAssignment'] = CurrentAssignment.ToDictionary()
        if State.PrepareClusterInterfaceAssignmentOnly and FullAssignmentFingerprint in State.ForbiddenClusterInterfaceAssignmentFingerprints:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceUnsatisfiable, Stage='ClusterInterfaceUnsatisfiable', Detail='the exact interface re-solve reproduced a forbidden complete assignment fingerprint', Diagnostics={'InterfaceAssignment': CurrentAssignment.ToDictionary(), 'RepeatedAssignmentFingerprint': FullAssignmentFingerprint, 'RouteTreeRealizabilityAttempted': False, 'InterfaceSolve': {'Complete': True, 'ExecutableRepairAllowed': False}}))
    FrozenInterfaceAssignment = State.Resources.FrozenClusterInterfaceAssignment
    if FrozenInterfaceAssignment is not None:
        ObservedAssignmentFingerprint = Services.BuildClusterInterfaceReservationAssignmentFingerprint(State.BoundaryLeaseReservations)
        if ObservedAssignmentFingerprint != (FrozenInterfaceAssignment.OwnershipAssignmentFingerprint or FrozenInterfaceAssignment.AssignmentFingerprint):
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceInvariantViolation, Stage='ClusterInterfaceAssignmentInvariant', Detail='routing changed the frozen cluster-interface ownership assignment', Diagnostics={'ExpectedAssignmentFingerprint': FrozenInterfaceAssignment.OwnershipAssignmentFingerprint or FrozenInterfaceAssignment.AssignmentFingerprint, 'ObservedAssignmentFingerprint': ObservedAssignmentFingerprint, 'FrozenInterfaceAssignment': FrozenInterfaceAssignment.ToDictionary()}))
        State.WorkTelemetry['FrozenInterfaceAssignment'] = {'Applied': True, 'AssignmentFingerprint': FrozenInterfaceAssignment.AssignmentFingerprint}
    CurrentClusterLeaseSignalPatternFingerprints = {Signal: Services.BuildClusterLeaseSignalPatternFingerprint(State.BoundaryLeaseReservations, Signal) for Signal in {Reservation.Signal for Reservation in State.BoundaryLeaseReservations}}
    ChangedClusterLeaseSignals = frozenset((Signal for Signal in set(CurrentClusterLeaseSignalPatternFingerprints) | set(State.PriorClusterLeaseSignalPatternFingerprints or {}) if CurrentClusterLeaseSignalPatternFingerprints.get(Signal) != (State.PriorClusterLeaseSignalPatternFingerprints or {}).get(Signal))) if State.PriorClusterLeaseSignalPatternFingerprints is not None else frozenset()
    if ChangedClusterLeaseSignals:
        State.RegenerateSignals = frozenset((*State.RegenerateSignals, *ChangedClusterLeaseSignals))
        State.WorkTelemetry['CandidateRealizabilityLeaseReuse'] = {'PriorSignalCount': len(State.PriorClusterLeaseSignalPatternFingerprints or {}), 'CurrentSignalCount': len(CurrentClusterLeaseSignalPatternFingerprints), 'ChangedSignals': sorted(ChangedClusterLeaseSignals), 'ChangedSignalCount': len(ChangedClusterLeaseSignals), 'RetainedSignalCount': max(0, len(CurrentClusterLeaseSignalPatternFingerprints) - len(ChangedClusterLeaseSignals))}
    LeaseCandidatesFiltered = 0
    (LeaseCandidatesFilteredByTerminal): Services.Counter[tuple[str, Services.Position3]] = Services.Counter()
    (LeaseCandidateBlockersByTerminal): dict[tuple[str, Services.Position3], set[str]] = Services.defaultdict(set)
    if State.BoundaryLeaseReservations:
        if len(State.BoundaryLeaseTerminalPairs) < 16:
            for Key, Values in tuple(State.Portals.items()):
                Signal = Key[0]
                BlockingSignalsByPortal = {Portal.PortalId: frozenset((Reservation.Signal for Reservation in State.BoundaryLeaseReservations if Reservation.Signal != Signal and Services._ClaimsConflict(Signal, Portal.Claims, Reservation.Signal, Reservation.Claims))) for Portal in Values}
                FilteredValues = tuple((Portal for Portal in Values if not BlockingSignalsByPortal[Portal.PortalId]))
                LeaseCandidatesFiltered += len(Values) - len(FilteredValues)
                LeaseCandidatesFilteredByTerminal[Signal, Key[1]] += len(Values) - len(FilteredValues)
                for Portal in Values:
                    LeaseCandidateBlockersByTerminal[Signal, Key[1]].update(BlockingSignalsByPortal[Portal.PortalId])
                State.Portals[Key] = FilteredValues
            for Signal in State.Profiles:
                ForeignLeaseNodes = frozenset((Position for Reservation in State.BoundaryLeaseReservations if Reservation.Signal != Signal for Position in Reservation.FirstSegment))
                if ForeignLeaseNodes:
                    State.EffectiveAvoidRoutingPositionsBySignal[Signal] = State.EffectiveAvoidRoutingPositionsBySignal.get(Signal, frozenset()) | ForeignLeaseNodes
        State.WorkTelemetry['ClusterBoundaryLeases'] = {**dict(State.WorkTelemetry['ClusterBoundaryLeases']), 'ForeignPortalCandidatesExcluded': LeaseCandidatesFiltered, 'ForeignCandidateExclusionMode': 'hard-first-segment' if len(State.BoundaryLeaseTerminalPairs) < 16 else 'native-signal-owned-base-claims', 'ForeignCandidateExcludedNodeCount': len(frozenset((Position for Reservation in State.BoundaryLeaseReservations for Position in Reservation.FirstSegment))), 'ForeignPortalCandidatesExcludedByTerminal': {f'{Signal}:{Terminal}': Count for (Signal, Terminal), Count in sorted(LeaseCandidatesFilteredByTerminal.items()) if Count}, 'ForeignPortalCandidateBlockersByTerminal': {f'{Signal}:{Terminal}': sorted(Blockers) for (Signal, Terminal), Blockers in sorted(LeaseCandidateBlockersByTerminal.items()) if Blockers}}
    if State.ValidateClusterInterfaceForeignAccessOnly:
        TerminalPortalCounts = {(Signal, Terminal): sum((len(State.Portals.get((Signal, Terminal, Layer), ())) for Layer in range(State.LayerCount))) for Signal, Profile in State.Profiles.items() for Terminal in (Profile.Root, *Profile.Targets)}
        EmptyTerminalDomains = tuple(sorted(((Signal, Terminal) for (Signal, Terminal), Count in TerminalPortalCounts.items() if Count == 0)))
        ValidationDiagnostics = {'EffectiveGlobalPortalDomainComplete': True, 'InterfacePatternFingerprints': dict(sorted(CurrentClusterLeaseSignalPatternFingerprints.items())), 'EmptyTerminalDomains': [{'Signal': Signal, 'Terminal': list(Terminal), 'BlockingInterfaceSignals': sorted(LeaseCandidateBlockersByTerminal.get((Signal, Terminal), ())), 'BlockingInterfacePatternFingerprints': {BlockingSignal: CurrentClusterLeaseSignalPatternFingerprints.get(BlockingSignal, '') for BlockingSignal in sorted(LeaseCandidateBlockersByTerminal.get((Signal, Terminal), ()))}, 'ExcludedCandidateCount': int(LeaseCandidatesFilteredByTerminal.get((Signal, Terminal), 0))} for Signal, Terminal in EmptyTerminalDomains], 'TerminalCount': len(TerminalPortalCounts), 'CandidateCount': sum(TerminalPortalCounts.values()), 'RejectedInterfaceAssignment': State.Resources.FrozenClusterInterfaceAssignment.ToDictionary() if State.Resources.FrozenClusterInterfaceAssignment is not None else None}
        if EmptyTerminalDomains:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceUnsatisfiable, Stage='ClusterInterfaceForeignAccessStarved', AffectedNets=tuple(sorted({Signal for Signal, _Terminal in EmptyTerminalDomains})), Detail='the frozen component assignment removes every effective ordinary-global portal for at least one foreign terminal', RepairActions=(), Diagnostics={'ForeignAccessValidation': ValidationDiagnostics, 'RouteTreeRealizabilityAttempted': False}))
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.Stagnated, Stage='ClusterInterfaceForeignAccessValidated', Detail='the frozen component assignment preserves at least one effective ordinary-global portal for every terminal', RepairActions=(), Diagnostics={'ForeignAccessValidation': ValidationDiagnostics}))
    State.ReservedPortalSeedBySignal: dict[str, tuple[int, Services.PinAccessPortal, tuple[Services.PinAccessPortal, ...]]] = dict(EffectivePreparedPortalCache.ReservedPortalSeedEntries)
    PrepareOptionalPortalSeed = not State.ApplyMaturePortfolioSearchCaps and Services.ShouldPrepareOptionalPortalSeed(State.UnreservedPortalMode, list(State.Profiles), EffectivePreparedPortalCache.SeedReservationPrepared)
    if PrepareOptionalPortalSeed:
        SeedReservationExpansions = min(State.AdaptiveBudget.AssignmentExpansions, 2000 if State.Demand.TerminalCount > 64 else 8000)
        SeedDiagonalVariants = 1 if State.Demand.TerminalCount > 64 else None
        SeedStarted = Services.monotonic()
        SeedSliceSeconds = Services.SelectOptionalPortalSeedSliceSeconds(max(0.0, min(State.Deadline.ExpiresAt, State.AdaptiveExpiresAt) - SeedStarted))
        SeedWorkCheck = Services.BuildOptionalPortalSeedWorkCheck(SeedStarted + SeedSliceSeconds, lambda Details: State.CheckRuntimeBudget('PortalSeedReservation', Details))
        try:
            if SeedSliceSeconds <= 0:
                raise Services.OptionalPortalSeedSliceExpired
            ReservedSeedPortals, _ReservedSeedClaims = Services.ReserveNegotiatedBoundaryEscapes(dict(RawPortals), State.Profiles, State.Resources, ReservationVariant=State.ReservationVariant, MaximumExpansions=SeedReservationExpansions, MaximumDiagonalVariants=SeedDiagonalVariants, WorkCheck=SeedWorkCheck)
            SeedWorkCheck({'Phase': 'complete'})
        except Services.OptionalPortalSeedSliceExpired:
            State.WorkTelemetry['PortalSeedReservation'] = {'Result': 'unavailable-local-slice', 'MaximumExpansions': SeedReservationExpansions, 'MaximumDiagonalVariants': SeedDiagonalVariants, 'SliceSeconds': round(SeedSliceSeconds, 6), 'ElapsedSeconds': round(Services.monotonic() - SeedStarted, 6)}
        except Services.RoutingStageError as Error:
            State.WorkTelemetry['PortalSeedReservation'] = {'Result': 'unavailable', 'Reason': Error.Failure.Reason.value, 'MaximumExpansions': SeedReservationExpansions, 'MaximumDiagonalVariants': SeedDiagonalVariants, 'SliceSeconds': round(SeedSliceSeconds, 6), 'ElapsedSeconds': round(Services.monotonic() - SeedStarted, 6)}
        else:
            for Signal, Profile in State.Profiles.items():
                for Layer in range(State.LayerCount):
                    SourceValues = ReservedSeedPortals.get((Signal, Profile.Root, Layer), ())
                    TargetValues = tuple((ReservedSeedPortals.get((Signal, Target, Layer), ()) for Target in Profile.Targets))
                    if len(SourceValues) != 1 or any((len(Values) != 1 for Values in TargetValues)):
                        continue
                    State.ReservedPortalSeedBySignal[Signal] = (Layer, SourceValues[0], tuple((Values[0] for Values in TargetValues)))
                    break
            State.WorkTelemetry['PortalSeedReservation'] = {'Result': 'seeded', 'SignalCount': len(State.ReservedPortalSeedBySignal), 'MaximumExpansions': SeedReservationExpansions, 'MaximumDiagonalVariants': SeedDiagonalVariants, 'SliceSeconds': round(SeedSliceSeconds, 6), 'ElapsedSeconds': round(Services.monotonic() - SeedStarted, 6)}
    elif State.ApplyMaturePortfolioSearchCaps and (not EffectivePreparedPortalCache.SeedReservationPrepared):
        State.WorkTelemetry['PortalSeedReservation'] = {'Result': 'skipped-mature-cumulative-portfolio', 'SignalCount': 0}
    elif EffectivePreparedPortalCache.SeedReservationPrepared:
        State.WorkTelemetry['PortalSeedReservation'] = {'Result': 'cached', 'SignalCount': len(State.ReservedPortalSeedBySignal)}
    if not EffectivePreparedPortalCache.SeedReservationPrepared:
        EffectivePreparedPortalCache = Services.replace(EffectivePreparedPortalCache, SeedReservationPrepared=True, ReservedPortalSeedEntries=tuple(sorted(State.ReservedPortalSeedBySignal.items())))
    if not State.RepeaterReadyPortalRepairSignals:
        Services.RetainPreparedPortalDomainCache(State.Resources, EffectivePreparedPortalCache)
    else:
        State.WorkTelemetry['RepeaterReadyPortalRepair'] = {**dict(State.WorkTelemetry.get('RepeaterReadyPortalRepair', {})), 'PreparedPortalCacheRetained': False, 'Reason': 'one-shot power-access domains must not replace the ordinary prepared portal cache'}
    State.StageTimings['PortalGeneration'] = Services.monotonic() - State.PortalStarted
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: portal generation elapsed={State.StageTimings['PortalGeneration']:.3f}s variants={max(State.RoutePortalVariantCounts.values(), default=0)}', flush=True)
    State.CheckRuntimeBudget('Portal')
    if State.ProgressCallback is not None:
        State.ProgressCallback(3, State.StageCount)
    State.CandidateStarted = Services.monotonic()
    return PhaseOutcome()

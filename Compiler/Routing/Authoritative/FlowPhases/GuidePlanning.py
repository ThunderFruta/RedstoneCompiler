"""GuidePlanning phase of authoritative routing."""
from __future__ import annotations
from ..RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState, PhaseOutcome

def RunGuidePlanning(State: AuthoritativeRoutingState, Services: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run the GuidePlanning phase against shared routing state."""
    State.PortalLimit = min(State.Policy.TrackAssignment.MaximumPortalsPerTerminal, State.AdaptiveBudget.PortalsPerTerminal) if State.Policy.AdaptiveRouting.Enabled else State.Policy.TrackAssignment.MaximumPortalsPerTerminal
    State.DemandDerivedPortalLimit = State.PortalLimit
    State.PortalSliceLimited = Services.ShouldLimitRetainedPortfolioPortalDomain(State.Policy.AdaptiveRouting.Enabled, State.ApplyStagedPortfolioProof, State.ExactLegalRetainedJointStateCount, State.RawPortalCache is not None, State.Deadline.RemainingSeconds(), State.PortalLimit) and (not State.RequireCompleteClusterInterfaceDomain)
    if State.PortalSliceLimited:
        State.PortalLimit = 2
    PreMaturePortfolioPortalLimit = State.PortalLimit
    State.PortalLimit = Services.SelectMaturePortfolioPortalLimit(State.PortalLimit, State.ApplyMaturePortfolioSearchCaps)
    State.WorkTelemetry['PortalSliceLimited'] = State.PortalSliceLimited
    State.WorkTelemetry['ClusterInterfaceDomainMode'] = 'complete' if State.RequireCompleteClusterInterfaceDomain else 'adaptive'
    State.WorkTelemetry['EffectivePortalLimit'] = State.PortalLimit
    State.RouteLaneCount = min(State.Policy.GlobalRouting.CandidateLaneCount, State.AdaptiveBudget.LaneCount, State.Policy.AdaptiveRouting.InitialLaneCount) if State.Policy.AdaptiveRouting.Enabled else State.Policy.GlobalRouting.CandidateLaneCount
    State.RoutePortalVariantCounts = {Signal: (State.PortalLimit if State.PortalSliceLimited else max(min(State.PortalLimit, State.AdaptiveBudget.PortalsPerTerminal), min(12 if len(Profile.Targets) >= 4 else 6, State.Policy.TrackAssignment.MaximumPortalsPerTerminal) if len(Profile.Targets) >= 4 or 200 <= State.Demand.TerminalCount <= 256 or bool(State.LocalClaims) else 1)) if State.Policy.AdaptiveRouting.Enabled else State.PortalLimit for Signal, Profile in State.Profiles.items()}
    if State.ApplyMaturePortfolioSearchCaps:
        State.RoutePortalVariantCounts = {Signal: Services.SelectMaturePortfolioPortalLimit(VariantCount, True) for Signal, VariantCount in State.RoutePortalVariantCounts.items()}
    ApplyCoordinatedPortalDomain = State.CoordinatedCandidateDiversityLevel > State.CandidateDiversityLevel or (State.ConfiguredCoordinatedCandidateDiversityFixedLevel > 0 and bool(State.CoordinatedCandidateDiversificationSignals))
    State.RoutePortalVariantCounts = {Signal: Services.SelectCoordinatedPortalVariantCount(VariantCount, State.DemandDerivedPortalLimit, ApplyCoordinatedPortalDomain and Signal in State.CoordinatedCandidateDiversificationSignals) for Signal, VariantCount in State.RoutePortalVariantCounts.items()}
    PreparedExactPhysicalPlan = getattr(State.Resources, 'FrozenPhysicalComponentAssemblyPlan', None)
    State.ExactPhysicalPortalSignalsForPreparation = frozenset((Port.Signal for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(PreparedExactPhysicalPlan))) if PreparedExactPhysicalPlan is not None else frozenset()
    for Signal in State.ExactPhysicalPortalSignalsForPreparation:
        if Signal in State.RoutePortalVariantCounts:
            State.RoutePortalVariantCounts[Signal] = State.DemandDerivedPortalLimit
    State.RetainedPortfolioPortalProfileFrozen = Services.ShouldRetainBoundedPortfolioPortalProfile(State.ApplyStagedPortfolioProof, State.ExactLegalRetainedJointStateCount, State.RawPortalCache)
    if State.RetainedPortfolioPortalProfileFrozen:
        assert State.RawPortalCache is not None
        State.PortalLimit = State.RawPortalCache.PortalLimit
        State.RoutePortalVariantCounts = dict(State.RawPortalCache.PortalVariantCounts)
        for Signal in State.ExactPhysicalPortalSignalsForPreparation:
            if Signal in State.RoutePortalVariantCounts:
                State.RoutePortalVariantCounts[Signal] = State.DemandDerivedPortalLimit
    ReusedRawPortalProfile = Services.RawPortalProfileMatchesRequestedControls(State.RawPortalCache, State.PortalLimit, State.RoutePortalVariantCounts)
    State.WorkTelemetry['MaturePortfolioSearchCaps'] = {'Applied': State.ApplyMaturePortfolioSearchCaps, 'Reason': 'relocated-access-distinct-cumulative-assignment-constraints' if State.ApplyMaturePortfolioSearchCaps else 'structural-maturity-gate-not-satisfied', 'RequestedPortalLimit': PreMaturePortfolioPortalLimit, 'DemandDerivedPortalLimit': State.DemandDerivedPortalLimit, 'EffectivePortalLimit': State.PortalLimit, 'MaximumEffectivePortalVariantCount': max(State.RoutePortalVariantCounts.values(), default=0), 'CoordinatedPortalDomainSignals': sorted(State.CoordinatedCandidateDiversificationSignals if ApplyCoordinatedPortalDomain else ()), 'ReusedRawPortalProfile': ReusedRawPortalProfile, 'RetainedPortfolioPortalProfileFrozen': State.RetainedPortfolioPortalProfileFrozen, 'PortalLimitCap': 6 if State.ApplyMaturePortfolioSearchCaps else None}
    State.PortalAccessGeometryFingerprint = (*Services.BuildPortalAccessGeometryFingerprint(State.Profiles), ('packed-boundary-lease-v1', tuple(sorted(State.BoundaryLeaseSignals))), (str(getattr(State.InterClusterChannel, 'PhysicalModel', 'bounded-inter-cluster-channel-v1')), str(getattr(State.InterClusterChannel, 'ChannelFingerprint', ''))), ('placement-access-fabric-region-v1', State.PlacementAccessContractFingerprint))
    State.PhysicalGlobalKeepoutFingerprint = str(getattr(State.PhysicalAssemblyPlan, 'GlobalKeepoutFingerprint', ''))
    State.MinimumX = min((Gate.X for Gate in State.Placed.PlacedGates))
    State.MaximumX = max((Gate.X + Services.RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1 for Gate in State.Placed.PlacedGates))
    State.MinimumZ = min((Gate.Z for Gate in State.Placed.PlacedGates))
    State.MaximumZ = max((Gate.Z + Services.RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1 for Gate in State.Placed.PlacedGates))
    State.MinimumY = min((Gate.Y for Gate in State.Placed.PlacedGates))
    State.MinimumX, State.MaximumX, State.MinimumZ, State.MaximumZ, _PlacementAccessContractPositions, _PlacementAccessOuterBounds = Services.ResolvePlacementAccessFabricRegionContract(State.MinimumX, State.MaximumX, State.MinimumZ, State.MaximumZ, State.PlacementAccessFabric, State.PlacementAccessDomains)
    if _PlacementAccessContractPositions != State.PlacementAccessContractPositions:
        raise RuntimeError('placement access region contract changed during routing')
    if _PlacementAccessOuterBounds != State.PlacementAccessOuterBounds:
        raise RuntimeError('placement access outer bounds changed during routing')
    State.ReservedAccess = frozenset((Position for Profile in State.Profiles.values() for Path in (Profile.SourceAccessPath, *Profile.TargetAccessPaths.values()) for Position in Path)) | frozenset((Position for Claim in State.AllLocalClaims for Position in Claim.Nodes))
    if State.PlacementAccessFabric is not None:
        State.ReservedAccess = frozenset((*State.ReservedAccess, *State.PlacementAccessContractPositions))
    if State.Resources.PreparingPhysicalComponentGlobalChannels and State.Resources.PreparedPhysicalComponentPortFactorDomain is not None and State.Resources.PreparedPhysicalComponentPortFactorDomain.Complete:
        State.ReservedAccess = frozenset((*State.ReservedAccess, *(Position for _Signal, LaneFactors in State.Resources.PreparedPhysicalComponentPortFactorDomain.LaneFactorsBySignal for LaneFactor in LaneFactors for Seam in LaneFactor.Seams for Position in Seam.GlobalPath)))
    MaximumAccessY = max((Position[1] for Position in State.ReservedAccess), default=State.MinimumY)
    EffectiveRoutingHeight = max(State.MaximumRoutingHeight, MaximumAccessY - State.MinimumY)
    State.InterfaceDeckLayer = getattr(State.InterClusterChannel, 'InterfaceDeckLayer', None)
    if State.InterfaceDeckLayer is not None:
        EffectiveRoutingHeight = max(EffectiveRoutingHeight, State.Technology.RoutingY(State.MinimumY, int(State.InterfaceDeckLayer)) - State.MinimumY + 1)
    PolicyLayerLimit = State.Policy.Placement.MaximumRoutingLayers
    MinimumLayerCount = min(State.Technology.MinimumRoutingLayerCount, PolicyLayerLimit) if PolicyLayerLimit > 0 else State.Technology.MinimumRoutingLayerCount
    RequiredAccessLayerCount = Services.RequiredRoutingLayerCountForAccess(State.MinimumY, State.ReservedAccess, State.Policy.DetailedRouting.GuideExpansion, State.Technology, MinimumLayerCount=MinimumLayerCount)
    RequiredPhysicalAssemblyLayerCount = Services.RequiredPhysicalAssemblyRoutingLayerCount(State.PhysicalAssemblyPlan)
    RouteLayers = getattr(State.Placed, 'RouteLayers', None) or {}
    MaximumLayerCount = Services.SelectHierarchicalRoutingMaximumLayerCount(PolicyLayerLimit, State.Technology.MaximumRoutableLayerCount, State.InterfaceDeckLayer, State.PhysicalAssemblyPlan)
    Services.ValidatePhysicalAssemblyRoutingLayerLimit(State.PhysicalAssemblyPlan, RequiredPhysicalAssemblyLayerCount, MaximumLayerCount, PolicyLayerLimit if PolicyLayerLimit > 0 else State.Technology.MaximumRoutableLayerCount, State.Technology.MaximumRoutableLayerCount)
    if RequiredPhysicalAssemblyLayerCount:
        EffectiveRoutingHeight = max(EffectiveRoutingHeight, State.Technology.RoutingY(State.MinimumY, RequiredPhysicalAssemblyLayerCount - 1) - State.MinimumY + 3)
    HeightCapacity = max(MinimumLayerCount, (EffectiveRoutingHeight - 2) // State.Technology.RoutingLayerPitch)
    State.EffectiveMaximumLayerCount = min(MaximumLayerCount, HeightCapacity)
    if RequiredPhysicalAssemblyLayerCount:
        State.EffectiveMaximumLayerCount = max(State.EffectiveMaximumLayerCount, RequiredPhysicalAssemblyLayerCount)
    if State.InterfaceDeckLayer is not None:
        State.EffectiveMaximumLayerCount = max(State.EffectiveMaximumLayerCount, int(State.InterfaceDeckLayer) + 1)
    NegotiatedLayerFloor = Services.ceil(State.Demand.TerminalCount / max(1, State.Policy.NegotiatedRouting.TilePitchInTracks * State.Technology.TrackPitch)) if State.UseNegotiatedPortalDomain else 0
    State.LayerCount = Services.SelectInitialRoutingLayerCount(MinimumLayerCount=MinimumLayerCount, EffectiveMaximumLayerCount=State.EffectiveMaximumLayerCount, RequiredAccessLayerCount=max(RequiredAccessLayerCount, RequiredPhysicalAssemblyLayerCount), AdaptiveLayerCount=State.AdaptiveBudget.LayerCount if State.Policy.AdaptiveRouting.Enabled else MinimumLayerCount, AdaptiveLayerFloor=State.AdaptiveLayerFloor or 0, NegotiatedLayerFloor=NegotiatedLayerFloor, ExistingRouteLayerCount=max(RouteLayers.values(), default=0) + 1, PlacementWasRelocated=State.PlacementWasRelocated, ForceMaximumAfterPlacementRelocation=State.Policy.Placement.ForceMaximumRoutingLayersAfterPlacementRelocation)
    if State.InterfaceDeckLayer is not None:
        State.LayerCount = max(State.LayerCount, int(State.InterfaceDeckLayer) + 1)
    if State.FrozenPostClosurePortalHandoffApplied and State.RawPortalCache is not None and (State.RawPortalCache.LayerCount != State.LayerCount):
        raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentGlobalPlanning', Detail='the global-channel pass selected a routing layer domain different from the frozen post-closure portal handoff', Diagnostics={'PreparedLayerCount': State.RawPortalCache.LayerCount, 'SelectedLayerCount': State.LayerCount, 'ImplicitForeignTransitDomainCount': 0}))
    State.WorkTelemetry['PhysicalAssemblyLayerContract'] = {'PlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint if State.PhysicalAssemblyPlan is not None else '', 'RequiredPhysicalAssemblyLayerCount': RequiredPhysicalAssemblyLayerCount, 'RequiredAccessLayerCount': RequiredAccessLayerCount, 'SelectedLayerCount': State.LayerCount, 'EffectiveMaximumLayerCount': State.EffectiveMaximumLayerCount, 'PolicyMaximumLayerCount': PolicyLayerLimit if PolicyLayerLimit > 0 else State.Technology.MaximumRoutableLayerCount, 'TechnologyMaximumLayerCount': State.Technology.MaximumRoutableLayerCount, 'InterfaceDeckLayer': State.InterfaceDeckLayer, 'InterfaceDeckAuthorization': 'explicit-hierarchical-deck' if State.InterfaceDeckLayer is not None else 'flat-policy', 'Satisfied': State.LayerCount >= RequiredPhysicalAssemblyLayerCount}
    State.DedicatedInterfaceDeckNodes = frozenset(((int(Cell[0]), State.Technology.RoutingY(State.MinimumY, int(Lane.Layer)), int(Cell[2])) for Lane in (State.InterClusterChannel.Lanes if State.InterClusterChannel is not None else ()) if State.InterfaceDeckLayer is not None and int(Lane.Layer) == int(State.InterfaceDeckLayer) for Cell in Lane.Cells))
    State.ClosedComponentOwnedTerminalPairs = Services.SelectClosedComponentOwnedTerminalPairs(State.Placed, State.Profiles) if State.PreparePhysicalComponentAssemblyOnly and (not State.Resources.PreparingPhysicalComponentGlobalChannels) else frozenset()
    ComponentGuideProfileSignals = frozenset(
        Signal
        for Signal, _Terminal in State.ClosedComponentOwnedTerminalPairs
    )
    SelectiveComponentGuidePlanning = bool(
        State.PreparePhysicalComponentAssemblyOnly
        and not State.Resources.PreparingPhysicalComponentGlobalChannels
        and ComponentGuideProfileSignals
    )
    GuidePlanningProfiles = (
        {
            Signal: State.Profiles[Signal]
            for Signal in sorted(ComponentGuideProfileSignals)
        }
        if SelectiveComponentGuidePlanning
        else State.Profiles
    )
    State.WorkTelemetry['PhysicalComponentGuidePreparation'] = {
        'Selective': SelectiveComponentGuidePlanning,
        'PreparedSignalCount': len(GuidePlanningProfiles),
        'WholeDesignSignalCount': len(State.Profiles),
        'PreparedSignals': sorted(GuidePlanningProfiles),
    }
    State.RawPortalVariantCounts = dict(State.RoutePortalVariantCounts)
    for OwnedSignal, _Terminal in State.ClosedComponentOwnedTerminalPairs:
        State.RawPortalVariantCounts.setdefault(OwnedSignal, max(1, int(State.DemandDerivedPortalLimit)))
    State.WorkTelemetry['PhysicalComponentPortalPreparation'] = {'Selective': False, 'AuthoritativeScope': 'whole-design', 'PreparedSignals': sorted(State.RawPortalVariantCounts), 'DeferredSignalCount': 0}
    State.RawPortalPlacementGeometryFingerprint = Services.BuildRawPortalPlacementGeometryFingerprint(State.Placed)
    State.RawPortalResourceGeometryFingerprint = Services.BuildRawPortalResourceGeometryFingerprint(State.Resources)
    State.ResourceRawPortalReusePlan: Services.RawPortalGeometryReusePlan | None = None
    if State.RawPortalCache is None:
        State.ResourceRawPortalReusePlan = Services.SelectRawPortalGeometryReusePlan(State.Resources.RawPortalGeometryCaches, State.Placed, State.Resources, State.LayerCount, State.PortalLimit, State.RawPortalVariantCounts, State.Policy.DetailedRouting.GuideExpansion, State.Policy.DetailedRouting.StrictMaximumExpansions, State.PortalAccessGeometryFingerprint, State.CoordinatedCandidateDiversificationSignals, AllowPortableSignalReuse=State.TopologyRequiresJointPortfolio and State.PlacementWasRelocated or State.PreparePhysicalComponentAssemblyOnly or State.Resources.PreparingPhysicalComponentGlobalChannels, PhysicalGlobalKeepoutFingerprint=State.PhysicalGlobalKeepoutFingerprint, PlacementGeometryFingerprint=State.RawPortalPlacementGeometryFingerprint, ResourceGeometryFingerprint=State.RawPortalResourceGeometryFingerprint)
        if State.ResourceRawPortalReusePlan is not None:
            State.RawPortalCache = State.ResourceRawPortalReusePlan.Cache
    State.WorkTelemetry['RawPortalResourceCacheSelected'] = State.ResourceRawPortalReusePlan is not None
    State.WorkTelemetry['RawPortalPortablePlanarTransforms'] = {Signal: {'Transform': Transform, 'Translation': list(Translation)} for Signal, Transform, Translation in (State.ResourceRawPortalReusePlan.SignalPlanarTransforms if State.ResourceRawPortalReusePlan is not None else ())}
    State.WorkTelemetry['RawPortalCacheCallerProvided'] = State.CallerProvidedRawPortalCache
    State.WorkTelemetry['MaturePortfolioSearchCaps'] = {**dict(State.WorkTelemetry['MaturePortfolioSearchCaps']), 'ReusedRawPortalProfile': ReusedRawPortalProfile}
    State.GuideInputFingerprint = Services.BuildCapacityAwareGuideInputFingerprint(GuidePlanningProfiles, State.LayerCount, State.MinimumX, State.MinimumZ, State.Policy.GlobalRouting, State.Technology, State.Policy.Placement.LocalFanoutDistance)
    if State.PhysicalAssemblyPlan is not None:
        State.GuideInputFingerprint = Services.BuildStableFingerprint((State.GuideInputFingerprint, Services.BuildPhysicalAssemblyGuideContractFingerprint(State.PhysicalAssemblyPlan)))
    CachedGuideInputFingerprint = State.RawPortalCache.GuideInputFingerprint if State.RawPortalCache is not None else ''
    State.WorkTelemetry['GuideInputFingerprint'] = State.GuideInputFingerprint
    State.WorkTelemetry['CachedGuideInputFingerprint'] = CachedGuideInputFingerprint
    State.WorkTelemetry['GlobalGuidePlanInputFingerprintMatch'] = bool(CachedGuideInputFingerprint and CachedGuideInputFingerprint == State.GuideInputFingerprint)
    GuidePlanningStarted = Services.monotonic()
    State.ReuseCachedGuidePlan = bool(State.RawPortalCache is not None and State.RawPortalCache.MatchesGuidePlan(State.Placed, State.Resources, State.LayerCount, State.GuideInputFingerprint))
    FrozenPhysicalComponentGuidePlan = getattr(State.Resources, 'FrozenPhysicalComponentGlobalGuidePlan', None)
    State.PlanningPhysicalComponentExterior = bool(State.PhysicalAssemblyPlan is not None and State.Resources.PreparingPhysicalComponentGlobalChannels)
    PhysicalAssemblyPortSignalsForGuide = frozenset((Port.Signal for Port in (Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan) if State.PlanningPhysicalComponentExterior else ())))
    ReuseFrozenPhysicalPortGuidePlan = bool(State.PlanningPhysicalComponentExterior and Services.CanReuseFrozenPhysicalPortGuidePlan(State.Profiles, PhysicalAssemblyPortSignalsForGuide, FrozenPhysicalComponentGuidePlan))
    BuildGlobalGuidePlan = Services.ShouldBuildCapacityAwareGlobalGuidePlan(Enabled=State.Policy.GlobalRouting.EnableCapacityAwareGuides, PrepareComponentRoutingProblemOnly=State.PrepareComponentRoutingProblemOnly, RequireCompleteClusterInterfaceDomain=State.RequireCompleteClusterInterfaceDomain, HasInterClusterRoutingChannel=State.InterClusterChannel is not None)
    ComponentGuideAffectedClusters = frozenset((int(Value) for Value in getattr(State.InterClusterChannel, 'AffectedClusters', ())))
    ComponentGuideObstacleCoordinates = tuple({(int(Cell[0]), int(Cell[2])) for Lane in (State.InterClusterChannel.Lanes if State.InterClusterChannel is not None else ()) for Cell in Lane.Cells} | {(int(Position[0]), int(Position[2])) for Claim in getattr(State.Placed, 'LocalRouteClaims', ()) or () if int(getattr(Claim, 'ClusterId', -1)) in ComponentGuideAffectedClusters for Position in (*getattr(Claim, 'Nodes', ()), *getattr(getattr(Claim, 'Claims', None), 'WireCells', ()), *getattr(getattr(Claim, 'Claims', None), 'SupportCells', ()), *getattr(getattr(Claim, 'Claims', None), 'RequiredAirCells', ()), *getattr(getattr(Claim, 'Claims', None), 'ElectricalCells', ()))})
    ComponentGuideObstacleBounds = (min((X for X, _Z in ComponentGuideObstacleCoordinates)) - 1, max((X for X, _Z in ComponentGuideObstacleCoordinates)) + 1, min((Z for _X, Z in ComponentGuideObstacleCoordinates)) - 1, max((Z for _X, Z in ComponentGuideObstacleCoordinates)) + 1) if ComponentGuideObstacleCoordinates else None
    if State.PlanningPhysicalComponentExterior:
        ComponentGuideObstacleBounds = (State.PhysicalAssemblyPlan.EnvelopeMinimum[0], State.PhysicalAssemblyPlan.EnvelopeMaximum[0], State.PhysicalAssemblyPlan.EnvelopeMinimum[2], State.PhysicalAssemblyPlan.EnvelopeMaximum[2])
    (ComponentGuideObstacleCellsByLayer): dict[int, frozenset[tuple[int, int]]] = {}
    (ComponentGuideObstacleExemptCellsBySignal): dict[str, dict[int, frozenset[tuple[int, int]]]] = {}
    if State.PlanningPhysicalComponentExterior:
        RoutingLayerByY = {State.Technology.RoutingY(State.MinimumY, Layer): Layer for Layer in range(State.LayerCount)}
        (MutableObstacleCellsByLayer): dict[int, set[tuple[int, int]]] = Services.defaultdict(set)
        for X, Y, Z in State.PhysicalAssemblyPlan.GlobalKeepoutNodes:
            Layer = RoutingLayerByY.get(int(Y))
            if Layer is not None:
                MutableObstacleCellsByLayer[Layer].add((int(X), int(Z)))
        ComponentGuideObstacleCellsByLayer = {Layer: frozenset(Cells) for Layer, Cells in MutableObstacleCellsByLayer.items()}
        for Port in Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan):
            (MutableExemptions): dict[int, set[tuple[int, int]]] = Services.defaultdict(set)
            for X, Y, Z in Port.GlobalPath:
                Layer = RoutingLayerByY.get(int(Y))
                if Layer is not None:
                    MutableExemptions[Layer].add((int(X), int(Z)))
            ComponentGuideObstacleExemptCellsBySignal[Port.Signal] = {Layer: frozenset(Cells) for Layer, Cells in MutableExemptions.items()}
    ComponentGuideOwnedSignals = frozenset((str(Signal) for Signal in getattr(State.InterClusterChannel, 'AffectedSignals', ())))
    if ReuseFrozenPhysicalPortGuidePlan:
        State.CoarsePlan = FrozenPhysicalComponentGuidePlan
    elif State.ReuseCachedGuidePlan:
        assert State.RawPortalCache is not None
        State.CoarsePlan = State.RawPortalCache.GuidePlan
    else:
        State.CoarsePlan = Services.BuildCapacityAwareGuidePlan(GuidePlanningProfiles, State.LayerCount, State.MinimumX, State.MinimumZ, State.Policy.GlobalRouting, State.Technology, State.Policy.Placement.LocalFanoutDistance, ComponentObstacleBounds=ComponentGuideObstacleBounds if State.PreparePhysicalComponentAssemblyOnly and State.PhysicalAssemblyPlan is None else None, ComponentObstacleCellsByLayer=ComponentGuideObstacleCellsByLayer if State.PhysicalAssemblyPlan is not None else None, ComponentObstacleExemptCellsBySignal=ComponentGuideObstacleExemptCellsBySignal if State.PhysicalAssemblyPlan is not None else None, ComponentOwnedSignals=ComponentGuideOwnedSignals if State.PreparePhysicalComponentAssemblyOnly else frozenset(), SeedPlan=State.RawPortalCache.GuidePlan if State.RawPortalCache is not None else None, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('Guide', Diagnostics)) if BuildGlobalGuidePlan else None
    if State.PlanningPhysicalComponentExterior:
        if State.CoarsePlan is None:
            raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentGlobalPlanning', Detail='physical assembly requires a complete global guide plan', Diagnostics={'PhysicalAssemblyPlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint}))
        FrozenAxes = dict(getattr(FrozenPhysicalComponentGuidePlan, 'Axes', {}))
        FrozenLanes = dict(getattr(FrozenPhysicalComponentGuidePlan, 'Lanes', {}))
        State.CoarsePlan = Services.replace(State.CoarsePlan, Guides={**dict(State.CoarsePlan.Guides), **{Channel.Signal: frozenset(Channel.GuideCells) for Channel in State.PhysicalAssemblyPlan.PlanningChannels if Channel.Signal in PhysicalAssemblyPortSignalsForGuide}}, Layers={**dict(State.CoarsePlan.Layers), **{Channel.Signal: int(Channel.Layer) for Channel in State.PhysicalAssemblyPlan.PlanningChannels if Channel.Signal in PhysicalAssemblyPortSignalsForGuide}}, Axes={**dict(State.CoarsePlan.Axes), **{Signal: FrozenAxes[Signal] for Signal in PhysicalAssemblyPortSignalsForGuide if Signal in FrozenAxes}}, Lanes={**dict(State.CoarsePlan.Lanes), **{Signal: FrozenLanes[Signal] for Signal in PhysicalAssemblyPortSignalsForGuide if Signal in FrozenLanes}})
    State.DerivedPerimeterAccess = bool(State.PlacementAccessFabric is not None and getattr(State.PlacementAccessFabric, 'TopologyKind', '') == 'derived-perimeter-access-v1')
    if State.PlacementAccessFabric is not None and State.CoarsePlan is not None:
        FabricGuideColumns = frozenset(((int(Position[0]), int(Position[2])) for Position in State.PlacementAccessFabric.Nodes))
        FabricSignals = frozenset((str(Domain.Signal) for Domain in State.PlacementAccessFabric.TerminalDomains))
        FabricLayers = tuple(sorted({Layer for Layer in range(State.LayerCount) if any((int(Ingress[1]) == State.Technology.RoutingY(State.MinimumY, Layer) for Ingress in State.PlacementAccessFabric.IngressNodes))}))
        FabricLayer = FabricLayers[0] if FabricLayers else None
        State.CoarsePlan = Services.replace(State.CoarsePlan, Guides={**dict(State.CoarsePlan.Guides), **{Signal: frozenset((*State.CoarsePlan.Guides.get(Signal, ()), *FabricGuideColumns)) for Signal in FabricSignals if Signal in State.Profiles}}, Layers={**dict(State.CoarsePlan.Layers), **({Signal: FabricLayer for Signal in FabricSignals if Signal in State.Profiles} if FabricLayer is not None else {})})
    FactorizedGuideIdentity = None
    State.FactorizedGuideFingerprintBySignal: dict[str, str] = {}
    if State.CoarsePlan is not None:
        LocalGuideInputFingerprints = dict(getattr(State.CoarsePlan, 'LocalInputFingerprintsBySignal', {}))
        if set(LocalGuideInputFingerprints) == set(State.CoarsePlan.Guides):
            FactorizedGuideIdentity = Services.BuildFactorizedPhysicalGuideIdentity(State.CoarsePlan, LocalGuideInputFingerprints)
            State.FactorizedGuideFingerprintBySignal = FactorizedGuideIdentity.FactorFingerprintBySignal()
    State.WorkTelemetry['FactorizedGuideFingerprintBySignal'] = dict(sorted(State.FactorizedGuideFingerprintBySignal.items()))
    State.WorkTelemetry['JointGuideCapacityAssignmentFingerprint'] = FactorizedGuideIdentity.JointCapacityAssignmentFingerprint if FactorizedGuideIdentity is not None else ''
    State.WorkTelemetry['GlobalGuidePlanCacheHit'] = bool(State.ReuseCachedGuidePlan or ReuseFrozenPhysicalPortGuidePlan)
    State.WorkTelemetry['FrozenPhysicalPortGuidePlanReused'] = bool(ReuseFrozenPhysicalPortGuidePlan)
    State.WorkTelemetry['PhysicalAssemblyGuideContractApplied'] = bool(State.PhysicalAssemblyPlan is not None)
    State.WorkTelemetry['GlobalGuidePlanDeferredToComponentHandoff'] = bool(not State.ReuseCachedGuidePlan and State.Policy.GlobalRouting.EnableCapacityAwareGuides and (not BuildGlobalGuidePlan))
    State.StageTimings['GlobalGuidePlanning'] = Services.monotonic() - GuidePlanningStarted
    State.CheckRuntimeBudget('Guide')
    if State.ProgressCallback is not None:
        State.ProgressCallback(1, State.StageCount)
    ActiveMaximumY = max(MaximumAccessY + 1, State.Technology.RoutingY(State.MinimumY, State.LayerCount - 1) + 1)
    PhysicalMaximumY = State.MinimumY + EffectiveRoutingHeight
    State.Bounds = (State.MinimumX - State.SearchMarginX, State.MaximumX + State.SearchMarginX, State.MinimumY, min(PhysicalMaximumY, ActiveMaximumY), State.MinimumZ - State.SearchMarginZ, State.MaximumZ + State.SearchMarginZ)
    (AssignedColumns): set[Services.Position2] = set()
    PreparedExteriorGuideColumnsBySignal = Services.BuildPreparedPhysicalExteriorGuideColumnsBySignal(State.Resources.PreparedPhysicalComponentPortFactorDomain) if State.Resources.PreparingPhysicalComponentGlobalChannels else {}
    State.WorkTelemetry['PreparedPhysicalExteriorGuideFabric'] = {'Enabled': bool(PreparedExteriorGuideColumnsBySignal), 'Signals': sorted(PreparedExteriorGuideColumnsBySignal), 'ColumnCounts': {Signal: len(Columns) for Signal, Columns in sorted(PreparedExteriorGuideColumnsBySignal.items())}, 'Fingerprint': Services.BuildStableFingerprint(tuple(((Signal, tuple(sorted(Columns))) for Signal, Columns in sorted(PreparedExteriorGuideColumnsBySignal.items()))))}
    RegionExpansion = State.Policy.DetailedRouting.GuideExpansion + State.Technology.TrackPitch * State.LaneDiversityLevel
    for Signal, Profile in GuidePlanningProfiles.items():
        TerminalColumns = tuple(((Path[-1][0], Path[-1][2]) for Path in (Profile.SourceAccessPath, *Profile.TargetAccessPaths.values())))
        if Profile.Seed is not None and Profile.Seed.ContinuationNodes:
            TerminalColumns = tuple(dict.fromkeys((*((Position[0], Position[2]) for Position in Profile.Seed.ContinuationNodes), *TerminalColumns)))
        PreparedExteriorGuide = PreparedExteriorGuideColumnsBySignal.get(Signal)
        if PreparedExteriorGuide:
            BaseGuides = (PreparedExteriorGuide,)
        elif State.CoarsePlan is not None:
            BaseGuides = (frozenset({*State.CoarsePlan.Guides[Signal], *Services._BuildGuide(TerminalColumns, State.CoarsePlan.Axes[Signal], State.CoarsePlan.Lanes[Signal])}),)
        else:
            BaseGuides = ()
        for Axis in ('X', 'Z') if not BaseGuides else ():
            Coordinates = sorted((Z for _X, Z in TerminalColumns)) if Axis == 'X' else sorted((X for X, _Z in TerminalColumns))
            Center = Coordinates[len(Coordinates) // 2]
            TrackAnchor = State.MinimumZ if Axis == 'X' else State.MinimumX
            AlignedCenter = TrackAnchor + (Center - TrackAnchor + State.Technology.TrackPitch // 2) // State.Technology.TrackPitch * State.Technology.TrackPitch
            BaseGuide = Services._BuildGuide(TerminalColumns, Axis, AlignedCenter)
            BaseGuides = (*BaseGuides, BaseGuide)
        for BaseGuide in BaseGuides:
            AssignedColumns.update(((GuideX + DeltaX, GuideZ + DeltaZ) for GuideX, GuideZ in BaseGuide for DeltaX in range(-RegionExpansion, RegionExpansion + 1) for DeltaZ in range(-RegionExpansion, RegionExpansion + 1) if abs(DeltaX) + abs(DeltaZ) <= RegionExpansion))
    if State.InterClusterChannel is not None:
        ChannelIngressColumns = tuple(sorted({(int(Ingress[0]), int(Ingress[2])) for Lane in State.InterClusterChannel.Lanes for Ingress in Lane.IngressNodes}))
        AssignedColumns.update(((int(Cell[0]), int(Cell[2])) for Lane in State.InterClusterChannel.Lanes for Cell in Lane.Cells))
        for Signal in sorted(State.InterClusterChannelSignals):
            Profile = State.Profiles.get(Signal)
            if Profile is None:
                continue
            for AccessPath in (Profile.SourceAccessPath, *Profile.TargetAccessPaths.values()):
                if not AccessPath or not ChannelIngressColumns:
                    continue
                AccessColumn = (int(AccessPath[-1][0]), int(AccessPath[-1][2]))
                for IngressColumn in sorted(ChannelIngressColumns, key=lambda Value: (abs(Value[0] - AccessColumn[0]) + abs(Value[1] - AccessColumn[1]), Value))[:4]:
                    for X in range(min(AccessColumn[0], IngressColumn[0]), max(AccessColumn[0], IngressColumn[0]) + 1):
                        AssignedColumns.add((X, AccessColumn[1]))
                        AssignedColumns.add((X, IngressColumn[1]))
                    for Z in range(min(AccessColumn[1], IngressColumn[1]), max(AccessColumn[1], IngressColumn[1]) + 1):
                        AssignedColumns.add((AccessColumn[0], Z))
                        AssignedColumns.add((IngressColumn[0], Z))
    if State.PlacementAccessFabric is not None:
        AssignedColumns.update(((int(Position[0]), int(Position[2])) for Position in State.PlacementAccessFabric.Nodes))
        AssignedColumns.update(((int(Position[0]), int(Position[2])) for Domain in State.PlacementAccessFabric.TerminalDomains for Stub in Domain.EscapeStubs for Position in Stub.Path))
    State.PhysicalPortTerminals = frozenset(((Port.Signal, Port.Attachment) for Port in (Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan) if State.PhysicalAssemblyPlan is not None else ())))
    State.PhysicalPortSignals = frozenset((Signal for Signal, _Terminal in State.PhysicalPortTerminals))
    State.PhysicalPortGuidesBySignal: dict[str, frozenset[Services.Position2]] = {}
    if State.PlanningPhysicalComponentExterior:
        PhysicalChannelsBySignal = {Channel.Signal: Channel for Channel in State.PhysicalAssemblyPlan.PlanningChannels}
        for Signal in sorted(State.PhysicalPortSignals):
            Channel = PhysicalChannelsBySignal.get(Signal)
            if Channel is None:
                raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentGlobalPlanning', AffectedNets=(Signal,), Detail='physical port is missing its authoritative global corridor', Diagnostics={'PhysicalAssemblyPlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint, 'MissingPhysicalPortGuideSignals': [Signal]}))
            ReservedGuide = frozenset(Channel.GuideCells)
            PlannedGuide = frozenset(State.CoarsePlan.Guides.get(Signal, ())) if State.CoarsePlan is not None else ReservedGuide
            if not ReservedGuide or PlannedGuide != ReservedGuide:
                raise Services.RoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentGlobalPlanning', AffectedNets=(Signal,), Detail='physical port corridor differs from the authoritative guide plan', Diagnostics={'PhysicalAssemblyPlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint, 'PhysicalPortGuideSignal': Signal, 'ReservedGuideFingerprint': Services.BuildStableFingerprint(tuple(sorted(ReservedGuide))), 'PlannedGuideFingerprint': Services.BuildStableFingerprint(tuple(sorted(PlannedGuide)))}))
            State.PhysicalPortGuidesBySignal[Signal] = ReservedGuide
    AssignedColumns.update(Services.BuildPinnedOrdinaryPortalReuseColumns(State.ResourceRawPortalReusePlan, State.PhysicalPortTerminals))
    (PhysicalExteriorIngressColumns): frozenset[Services.Position2] = frozenset()
    (PhysicalExteriorPerimeterColumns): frozenset[Services.Position2] = frozenset()
    PreparedAccessCertificate = None
    State.WorkTelemetry['PhysicalExteriorRegionClosure'] = {'Applied': bool(PhysicalExteriorIngressColumns or PhysicalExteriorPerimeterColumns), 'IngressColumnCount': len(PhysicalExteriorIngressColumns), 'PerimeterColumnCount': len(PhysicalExteriorPerimeterColumns), 'CertificateFingerprint': str(getattr(PreparedAccessCertificate, 'CertificateFingerprint', ''))}
    ResourceGraphStarted = Services.monotonic()
    if State.FrozenPostClosurePortalHandoffApplied:
        assert State.RawPortalCache is not None
        State.Bounds = tuple(State.RawPortalCache.Region.Bounds)
        State.EffectiveAssignedColumns = State.RawPortalCache.AssignedColumns
        State.ReservedAccess = State.RawPortalCache.ReservedAccess
    else:
        State.EffectiveAssignedColumns = frozenset(AssignedColumns)
    ReuseCachedRegion = bool(State.FrozenPostClosurePortalHandoffApplied or (State.RawPortalCache is not None and State.RawPortalCache.MatchesPlacementResources(State.Placed, State.Resources) and (State.RawPortalCache.AssignedColumns == State.EffectiveAssignedColumns) and (State.RawPortalCache.ReservedAccess == State.ReservedAccess) and (getattr(State.RawPortalCache.Region, 'Bounds', None) == State.Bounds)))
    State.Region = State.RawPortalCache.Region if ReuseCachedRegion and State.RawPortalCache is not None else State.Resources.ResourceGraph.BuildRegion(State.Bounds, AllowedColumns=State.EffectiveAssignedColumns, AllowedAccess=State.ReservedAccess, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('ResourceGraph', Diagnostics))
    State.PhysicalExteriorRegionFingerprint = State.RawPortalCache.ExteriorRegionFingerprint if State.FrozenPostClosurePortalHandoffApplied and State.RawPortalCache is not None else Services.BuildStableFingerprint(('physical-exterior-routing-region-v1', State.Bounds, tuple(sorted(State.EffectiveAssignedColumns)), tuple(sorted(State.ReservedAccess)), getattr(State.Resources.ResourceGraph, 'GraphVersion', ''), getattr(State.Technology, 'TechnologyVersion', ''), repr(State.Technology)))
    State.WorkTelemetry['ResourceGraphCacheHit'] = ReuseCachedRegion
    State.StageTimings['ResourceGraph'] = Services.monotonic() - ResourceGraphStarted
    if State.ProgressCallback is not None:
        State.ProgressCallback(2, State.StageCount)
    State.PortalStarted = Services.monotonic()
    State.CheckRuntimeBudget('Portal')
    return PhaseOutcome()

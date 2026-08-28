"""CandidateMaterialization phase of authoritative routing."""
from __future__ import annotations
from ..RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState, PhaseOutcome

def RunCandidateMaterialization(State: AuthoritativeRoutingState, Services: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run the CandidateMaterialization phase against shared routing state."""
    for Signal in State.PhysicalCandidateConstructionOrder:
        if Signal in State.RegenerateSignals and State.CandidatesBySignal.get(Signal):
            State.CandidatesBySignal.pop(Signal, None)
            State.CandidateAxisLaneBySignal.pop(Signal, None)
        if State.Resources.PreparingPhysicalComponentGlobalChannels and State.CandidatesBySignal.get(Signal):
            State.CandidatesBySignal.pop(Signal, None)
            State.CandidateAxisLaneBySignal.pop(Signal, None)
        if State.CandidatesBySignal.get(Signal) and (not (State.Resources.PreparingPhysicalComponentGlobalChannels and Signal in State.IncompletePreSiblingDomainSignals and State.RouteRequestsBySignal.get(Signal))):
            State.RouteRequestsBySignal[Signal] = []
            State.RouteMetadataBySignal[Signal] = []
            continue
        Profile = State.Profiles[Signal]
        State.CheckRuntimeBudget('Candidate', {'Phase': 'request-payload-signal', 'Signal': Signal})
        CandidateExpansionLimit = State.CandidateExpansionLimits[Signal]
        SignalCandidateDiversityLevel = State.CoordinatedCandidateDiversityLevel if Signal in State.CoordinatedCandidateDiversificationSignals and State.ConfiguredCoordinatedCandidateDiversityFixedLevel > 0 else max(State.CandidateDiversityLevel, State.CoordinatedCandidateDiversityLevel if Signal in State.CoordinatedCandidateDiversificationSignals else 0)
        RouteRequests = []
        RouteMetadata = []
        (RoutePriorities): list[tuple[object, ...]] = []
        (RouteShapeDescriptors): list[Services.CandidateRequestShapeDescriptor] = []
        SeedNodeSet = frozenset((Position for Claim in (Profile.Seed.LocalClaims if Profile.Seed is not None else ()) for Position in Claim.Nodes))
        SeedStarts, DetachedSeedAnchors = Services.PartitionLocalClaimSeedComponents(Profile, State.Resources.ResourceGraph)
        DetachedSeedObstacleNodes = Services.BuildDetachedLocalClaimObstacleNodes(Profile, SeedStarts, State.Resources.ResourceGraph)
        if Services.os.environ.get('RCS_DEBUG_SEED_COMPONENTS') == Signal:
            print(f'[debug] authoritative: hierarchical seed components signal={Signal} root={Profile.Root} claimCount={(len(Profile.Seed.LocalClaims) if Profile.Seed else 0)} seedNodeCount={len(SeedNodeSet)} rootSeedCount={len(SeedStarts)} detachedAnchors={DetachedSeedAnchors} targets={Profile.Targets}', flush=True)
        TargetAccessPaths = tuple((Profile.TargetAccessPaths[Target] for Target in Profile.Targets))
        FixedRequiredNodes = frozenset({*SeedNodeSet, *Profile.SourceAccessPath, *(Position for TargetAccessPath in TargetAccessPaths for Position in TargetAccessPath)})
        SortedBlockedNodeBase = tuple(sorted(State.ForeignBlockedNodesBySignal[Signal] | DetachedSeedObstacleNodes | State.AvoidRoutingPositions | State.EffectiveAvoidRoutingPositionsBySignal.get(Signal, frozenset()) | State.FrozenComponentBlockedWireNodesBySignal.get(Signal, frozenset()) | (State.DedicatedInterfaceDeckNodes if Signal not in State.InterClusterChannelSignals else frozenset())))
        (AccessPayloadByPortalTuple): dict[tuple[str, ...], Services.InvariantRouteRequestNodePayload] = {}
        (SelfConflictingPortalTuples): set[tuple[str, ...]] = set()
        (GuidePayloadByGeometry): dict[tuple[frozenset[Services.Position2], int], tuple[tuple[Services.Position2, ...], tuple[Services.Position2, ...]]] = {}
        SignalInitialRequestLimit = max(State.InitialRequestLimit, 128 if len(Profile.Targets) >= 4 or 200 <= State.Demand.TerminalCount <= 256 else 16) if len(Profile.Targets) >= 4 or 200 <= State.Demand.TerminalCount <= 256 or bool(State.LocalClaims) else State.InitialRequestLimit
        if not State.UseNegotiatedRouting and 33 <= len(State.Profiles) <= 72 and (SignalCandidateDiversityLevel >= 2):
            SignalInitialRequestLimit = min(SignalInitialRequestLimit, 32 if SignalCandidateDiversityLevel >= 5 and len(Profile.Targets) >= 4 else 16)
        if State.RoutedComponentGlobalProbe and State.CandidateDiversityLevel == 0:
            SignalInitialRequestLimit = min(SignalInitialRequestLimit, 8)
        RequestWindowOffset = 0
        ApplyCoordinatedPortalWindow = Signal in State.CoordinatedCandidateDiversificationSignals and (SignalCandidateDiversityLevel > State.CandidateDiversityLevel or State.ConfiguredCoordinatedCandidateDiversityFixedLevel > 0)
        CoordinatedRequestWindowOffset = RequestWindowOffset if ApplyCoordinatedPortalWindow else 0
        if ApplyCoordinatedPortalWindow:
            State.CoordinatedCandidateProfileTelemetry[Signal] = {'EffectiveCandidateDiversityLevel': SignalCandidateDiversityLevel, 'RequestWindowOffset': RequestWindowOffset, 'ReservedPortalTupleOffset': CoordinatedRequestWindowOffset, 'SignalInitialRequestLimit': SignalInitialRequestLimit, 'BaseCandidateExpansionLimit': State.BaseCandidateExpansionLimits[Signal], 'EffectiveCandidateExpansionLimit': State.CandidateExpansionLimits[Signal]}
        SignalPortalPhase = State.CandidatePortalPhaseBySignal[Signal]
        PortalSeed = State.ReservedPortalSeedBySignal.get(Signal)
        PortalSeedPending = PortalSeed is not None
        UnreservedPerLayerRequestLimit = max(1, Services.ceil(SignalInitialRequestLimit / State.LayerCount))
        LayerOrder = tuple(range(State.LayerCount))
        if State.CoarsePlan is not None:
            PlannedLayer = State.CoarsePlan.Layers[Signal]
            LayerOrder = (PlannedLayer,) + tuple((Layer for Layer in LayerOrder if Layer != PlannedLayer))
        for Layer in LayerOrder:
            LayerPriority = 0 if Layer == LayerOrder[0] else 1
            SourcePortals = State.PortalDomainForTrunkLayer(Signal, Profile.Root, Layer)
            TargetPortalSets = [State.PortalDomainForTrunkLayer(Signal, Target, Layer) for Target in Profile.Targets]
            if not SourcePortals or any((not Values for Values in TargetPortalSets)):
                continue
            LegalPortalTuples = State.LegalPortalTuplesBySignalLayer.get((Signal, Layer), ())
            if not LegalPortalTuples:
                continue
            RoutingY = State.Technology.RoutingY(State.MinimumY, Layer)
            PhysicalPortalVariantCount = min(State.RoutePortalVariantCounts[Signal], len(LegalPortalTuples))
            for Variant in range(PhysicalPortalVariantCount):
                SourcePortal, *BaseTargetPortalValues = LegalPortalTuples[Variant]
                BaseTargetPortals = tuple(BaseTargetPortalValues)
                Terminals = tuple(((Portal.Path[-1][0], Portal.Path[-1][2]) for Portal in (SourcePortal, *BaseTargetPortals)))
                if DetachedSeedAnchors:
                    Terminals = tuple(dict.fromkeys((*Terminals, *((Position[0], Position[2]) for Position in DetachedSeedAnchors))))
                XSpan = max((X for X, _Z in Terminals)) - min((X for X, _Z in Terminals))
                ZSpan = max((Z for _X, Z in Terminals)) - min((Z for _X, Z in Terminals))
                PreferredAxis = 'X' if XSpan >= ZSpan else 'Z'
                for AxisIndex, Axis in enumerate((PreferredAxis, 'Z' if PreferredAxis == 'X' else 'X')):
                    AxisPriority = 0 if State.CoarsePlan is not None and Axis == State.CoarsePlan.Axes[Signal] else 1
                    Coordinates = sorted((Z for _X, Z in Terminals)) if Axis == 'X' else sorted((X for X, _Z in Terminals))
                    Center = Coordinates[len(Coordinates) // 2]
                    TrackAnchor = State.MinimumZ if Axis == 'X' else State.MinimumX
                    AlignedCenter = TrackAnchor + (Center - TrackAnchor + State.Technology.TrackPitch // 2) // State.Technology.TrackPitch * State.Technology.TrackPitch
                    if State.CoarsePlan is not None and Axis == State.CoarsePlan.Axes[Signal]:
                        AlignedCenter = State.CoarsePlan.Lanes[Signal]
                    LaneValues = Services.CandidateLanes(AlignedCenter, State.RouteLaneCount, State.Technology.TrackPitch)
                    if State.UnreservedPortalMode and State.PlacementAccessFabric is None and (len(State.Profiles) <= 32):
                        LaneValues = LaneValues[:1]
                    for LaneIndex, Lane in enumerate(LaneValues):
                        UsePhysicalGlobalLazyRequestDomain = bool(State.Resources.PreparingPhysicalComponentGlobalChannels and Signal not in State.PhysicalPortSignals)
                        PortalShapeRank = Services.CandidatePortalShapeRank(Variant, AxisIndex, LaneIndex, Layer, PhysicalPortalVariantCount, len(LaneValues), RequestWindowOffset + SignalPortalPhase) if State.UnreservedPortalMode or ApplyCoordinatedPortalWindow or UsePhysicalGlobalLazyRequestDomain else Variant
                        SparseBootstrapRanks = tuple((Services.CandidatePortalShapeRank(Variant, AxisIndex, LaneIndex, Layer, PhysicalPortalVariantCount, len(LaneValues), 0 + SignalPortalPhase) for BootstrapLevel in range(6))) if State.UseSparseCandidateBootstrap else ()
                        InitiallyDeferredRequestShape = Services.ShouldDeferUnreservedCandidateRequestShape(UnreservedPortalMode=State.UnreservedPortalMode or UsePhysicalGlobalLazyRequestDomain, UseSparseCandidateBootstrap=State.UseSparseCandidateBootstrap, SparseBootstrapRanks=SparseBootstrapRanks, PortalShapeRank=PortalShapeRank, UnreservedPerLayerRequestLimit=UnreservedPerLayerRequestLimit, CompleteCoordinatedSignalWindow=Services.ShouldCompletePhysicalCandidateRequestWindow(State.Resources.PreparingPhysicalComponentGlobalChannels, ApplyCoordinatedPortalWindow, SignalCandidateDiversityLevel, State.CandidateDiversityLevel, Signal in State.PhysicalPortSignals))
                        if InitiallyDeferredRequestShape:
                            if not UsePhysicalGlobalLazyRequestDomain:
                                State.DeferredRouteRequestCountsBySignal[Signal] += 1
                                continue
                        State.InvariantRequestPayloadCacheDiagnostics['ConsideredRequestShapeCount'] += 1
                        ConsideredRequestShapeCount = State.InvariantRequestPayloadCacheDiagnostics['ConsideredRequestShapeCount']
                        if ConsideredRequestShapeCount % 64 == 1:
                            State.CheckRuntimeBudget('Candidate', {'Phase': 'invariant-request-payload', 'Signal': Signal, 'Layer': Layer, 'ConsideredRequestShapeCount': ConsideredRequestShapeCount})
                        if PortalSeedPending and PortalSeed is not None and (Layer == PortalSeed[0]):
                            _SeedLayer, SourcePortal, TargetPortals = PortalSeed
                            PortalSeedPending = False
                        else:
                            PortalPhase = 1 + AxisIndex * 3 + LaneIndex
                            SourcePortal, *TargetPortalValues = LegalPortalTuples[Services.CandidatePortalTupleIndex(Variant, PortalPhase, len(LegalPortalTuples), CoordinatedRequestWindowOffset)]
                            TargetPortals = tuple(TargetPortalValues)
                        PortalNodes = frozenset({Position for Portal in (SourcePortal, *TargetPortals) for Position in Portal.Path})
                        if PortalNodes & State.ForeignBlockedNodesBySignal[Signal]:
                            State.ForeignPortalOverlapBySignal[Signal] += 1
                        PortalForeignAccessOverlap = bool(PortalNodes & State.ForeignBlockedNodesBySignal[Signal])
                        Terminals = tuple(((Portal.Path[-1][0], Portal.Path[-1][2]) for Portal in (SourcePortal, *TargetPortals)))
                        if DetachedSeedAnchors:
                            Terminals = tuple(dict.fromkeys((*Terminals, *((Position[0], Position[2]) for Position in DetachedSeedAnchors))))
                        Guide = Services.SelectAuthoritativeRouteRequestGuide(Terminals, Axis, Lane, ReservedPhysicalGuide=frozenset(State.CoarsePlan.Guides[Signal]) if (State.Resources.PreparingPhysicalComponentGlobalChannels or State.PlacementAccessFabric is not None) and State.CoarsePlan is not None and (Signal in State.CoarsePlan.Guides) else State.PhysicalPortGuidesBySignal.get(Signal), AllowPhysicalCorridorVariant=bool(State.Resources.PreparingPhysicalComponentGlobalChannels))
                        if State.PrepareClusterInterfaceAssignmentOnly and Signal in State.InterClusterChannelSignals and State.DedicatedInterfaceDeckNodes:
                            Guide = frozenset(((X, Z) for X, _Y, Z in State.DedicatedInterfaceDeckNodes))
                        IsPlannedGuide = State.CoarsePlan is not None and Layer == State.CoarsePlan.Layers[Signal] and (Axis == State.CoarsePlan.Axes[Signal]) and (Lane == State.CoarsePlan.Lanes[Signal])
                        GuideExpansion = int(State.PlacementAccessFabric.AccessRingTrackCount) * int(State.Technology.TrackPitch) if State.DerivedPerimeterAccess else 0 if State.PlacementAccessFabric is not None else State.Policy.GlobalRouting.IntraClusterEnvelope if IsPlannedGuide and Signal in State.CoarsePlan.LocalSignals else State.Policy.DetailedRouting.GuideExpansion
                        RequestPriority = (1 if ApplyCoordinatedPortalWindow and PortalForeignAccessOverlap else 0, min((BootstrapIndex * 2 + BootstrapRank for BootstrapIndex, BootstrapRank in enumerate(SparseBootstrapRanks)), default=PortalShapeRank) if State.UseSparseCandidateBootstrap else PortalShapeRank, LayerPriority, LaneIndex, AxisPriority, Axis, Lane)
                        ShapeDescriptor = Services.CandidateRequestShapeDescriptor(SourcePortal=SourcePortal, TargetPortals=tuple(TargetPortals), Guide=Guide, Layer=Layer, Axis=Axis, Lane=Lane, Variant=Variant, PortalShapeRank=PortalShapeRank, RoutingY=RoutingY, GuideExpansion=GuideExpansion, InitiallyDeferred=InitiallyDeferredRequestShape, Priority=RequestPriority)

                        def BuildCandidateRequest(*, Shape=ShapeDescriptor, SignalValue=Signal, ProfileValue=Profile, PortalNodesValue=PortalNodes, SeedStartsValue=tuple(SeedStarts), SeedNodeSetValue=SeedNodeSet, TargetAccessPathsValue=TargetAccessPaths, DetachedSeedAnchorsValue=DetachedSeedAnchors, FixedRequiredNodesValue=FixedRequiredNodes, SortedBlockedNodeBaseValue=SortedBlockedNodeBase, CandidateExpansionLimitValue=CandidateExpansionLimit, GuidePayloadCache=GuidePayloadByGeometry, AccessPayloadCache=AccessPayloadByPortalTuple, SelfConflictCache=SelfConflictingPortalTuples) -> tuple[Any, ...] | None:
                            GuidePayloadKey = (Shape.Guide, Shape.GuideExpansion)
                            GuidePayload = GuidePayloadCache.get(GuidePayloadKey)
                            if GuidePayload is None:
                                State.InvariantRequestPayloadCacheDiagnostics['GuidePayloadCacheMisses'] += 1
                                GuidePayload = Services.BuildInvariantRouteRequestGuidePayload(Shape.Guide, Shape.GuideExpansion)
                                GuidePayloadCache[GuidePayloadKey] = GuidePayload
                            else:
                                State.InvariantRequestPayloadCacheDiagnostics['GuidePayloadCacheHits'] += 1
                            CandidateColumns, SortedGuide = GuidePayload
                            PortalTupleKey = tuple((Portal.PortalId for Portal in (Shape.SourcePortal, *Shape.TargetPortals)))
                            if PortalTupleKey in SelfConflictCache:
                                State.InvariantRequestPayloadCacheDiagnostics['SelfConflictCacheHits'] += 1
                                return None
                            NodePayload = AccessPayloadCache.get(PortalTupleKey)
                            if NodePayload is None:
                                State.InvariantRequestPayloadCacheDiagnostics['AccessPayloadCacheMisses'] += 1
                                NodePayload = Services.BuildInvariantRouteRequestNodePayload(FixedRequiredNodesValue, PortalNodesValue, SortedBlockedNodeBaseValue)
                                RequiredClaims = None if SeedNodeSetValue else State.PortalTupleClaimsBySignal[SignalValue].get(PortalTupleKey)
                                if RequiredClaims is None:
                                    RequiredClaims = State.Resources.ResourceGraph.BuildRouteClaims(NodePayload.RequiredNodeSet)
                                if Services.FindSelfClaimConflicts({SignalValue: RequiredClaims}):
                                    SelfConflictCache.add(PortalTupleKey)
                                    return None
                                if any((Claim.Signal != SignalValue and Services.ComponentClaimsConflict(RequiredClaims, Claim.Claims) for Claim in State.FrozenComponentClaims)):
                                    State.FrozenComponentPortalConflictBySignal[SignalValue] += 1
                                    return None
                                if any((Services.ComponentClaimsConflict(RequiredClaims, SiblingClaims) for _SiblingSignal, SiblingClaims in State.AssemblySpecificSiblingAperturesBySignal.get(SignalValue, ()))):
                                    State.SiblingApertureRequiredClaimConflictsBySignal[SignalValue] += 1
                                    return None
                                AccessPayloadCache[PortalTupleKey] = NodePayload
                            else:
                                State.InvariantRequestPayloadCacheDiagnostics['AccessPayloadCacheHits'] += 1
                            if State.Resources.PreparingPhysicalComponentGlobalChannels:
                                ConnectivityKey = (PortalTupleKey, GuidePayloadKey)
                                ConnectivitySupported = State.PhysicalRouteFactorConnectivityCache.get(ConnectivityKey)
                                if ConnectivitySupported is None:
                                    State.InvariantRequestPayloadCacheDiagnostics['ConnectivityFactorChecks'] += 1
                                    ConnectivitySupported = Services.PhysicalRouteRequestFactorHasNecessaryConnectivity(State.PhysicalRouteFactorAdjacency, frozenset(State.Region.Nodes), NodePayload.RequiredNodeSet, frozenset(NodePayload.BlockedNodes), frozenset(CandidateColumns))
                                    State.PhysicalRouteFactorConnectivityCache[ConnectivityKey] = ConnectivitySupported
                                else:
                                    State.InvariantRequestPayloadCacheDiagnostics['ConnectivityFactorCacheHits'] += 1
                                if not ConnectivitySupported:
                                    State.InvariantRequestPayloadCacheDiagnostics['ConnectivityFactorPruned'] += 1
                                    return None
                            TargetBranches = list(Services._BuildTargetPortalBranches(Shape.TargetPortals, TargetAccessPathsValue))
                            TargetBranches.extend(([Anchor] for Anchor in DetachedSeedAnchorsValue))
                            TargetBranches = list(Services.FilterSourceConnectedTargetBranches(ProfileValue.Root, (*SeedStartsValue, *ProfileValue.SourceAccessPath, *Shape.SourcePortal.Path), TargetBranches, State.Resources.ResourceGraph))
                            if len(TargetBranches) > 1:
                                SourcePosition = Shape.SourcePortal.Path[-1]
                                TargetBranches.sort(key=lambda Branch: (-(abs(Branch[0][0] - SourcePosition[0]) + abs(Branch[0][1] - SourcePosition[1]) + abs(Branch[0][2] - SourcePosition[2])), Branch[-1], len(Branch)))
                                BranchOffset = Shape.PortalShapeRank % len(TargetBranches)
                                TargetBranches = TargetBranches[BranchOffset:] + TargetBranches[:BranchOffset]
                            State.InvariantRequestPayloadCacheDiagnostics['MaterializedRequestCount'] += 1
                            return (list(dict.fromkeys((*SeedStartsValue, *ProfileValue.SourceAccessPath, *Shape.SourcePortal.Path))), TargetBranches, list(CandidateColumns), list(NodePayload.RequiredNodes), list(NodePayload.BlockedNodes), list(SortedGuide), Shape.RoutingY, State.Policy.DetailedRouting.MinimumGuidePenalty, State.Policy.DetailedRouting.StrictBendPenalty, State.Policy.DetailedRouting.StrictViaPenalty, CandidateExpansionLimitValue)
                        (RequestValue): Services.Any
                        if State.Resources.PreparingPhysicalComponentGlobalChannels:
                            RequestValue = Services.LazyCandidateRouteRequest(ShapeDescriptor, BuildCandidateRequest)
                        else:
                            RequestValue = BuildCandidateRequest()
                            if RequestValue is None:
                                continue
                        RouteRequests.append(RequestValue)
                        RouteMetadata.append((SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant))
                        RoutePriorities.append(RequestPriority)
                        RouteShapeDescriptors.append(ShapeDescriptor)
        OrderedRequests = sorted(zip(RoutePriorities, RouteRequests, RouteMetadata, RouteShapeDescriptors), key=lambda Value: (1 if Value[3].InitiallyDeferred else 0, Value[0][0], Value[0][2], Value[0][1], Value[0][3], Value[0][4], Value[0][5], Value[0][6]))
        UniqueOrderedRequests = []
        (SeenRequestGeometry): set[tuple[object, ...]] = set()
        for Priority, Request, Metadata, Descriptor in OrderedRequests:
            SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, _Variant = Metadata
            RequestGeometry = Services.TrackPortfolioOperations.BuildCandidateRequestGeometryIdentity(SourcePortal.PortalId, tuple((Portal.PortalId for Portal in TargetPortals)), Guide, Layer, Axis, Lane, ImmutablePhysicalGuide=(State.Resources.PreparingPhysicalComponentGlobalChannels or State.PlacementAccessFabric is not None) and State.CoarsePlan is not None and (Signal in State.CoarsePlan.Guides) or Signal in State.PhysicalPortGuidesBySignal)
            if RequestGeometry in SeenRequestGeometry:
                continue
            SeenRequestGeometry.add(RequestGeometry)
            UniqueOrderedRequests.append((Priority, Request, Metadata, Descriptor))
        RouteRequests = [Value[1] for Value in UniqueOrderedRequests]
        RouteMetadata = [Value[2] for Value in UniqueOrderedRequests]
        RouteShapeDescriptors = [Value[3] for Value in UniqueOrderedRequests]
        State.PhysicalCandidateRequestShapesBySignal[Signal] = tuple(RouteShapeDescriptors)
        State.RouteRequestsBySignal[Signal] = RouteRequests
        State.RouteMetadataBySignal[Signal] = RouteMetadata
        PhysicalPort = next((Port for Port in (Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan) if State.PhysicalAssemblyPlan is not None else ()) if Port.Signal == Signal), None)
        PhysicalChannel = next((Channel for Channel in (State.PhysicalAssemblyPlan.PlanningChannels if State.PhysicalAssemblyPlan is not None else ()) if Channel.Signal == Signal), None)
        PhysicalRequestShapeDependencies = tuple(sorted({Services.BuildPhysicalCandidateRequestShapeDependencyIdentity(Descriptor) for Descriptor in RouteShapeDescriptors}))
        State.CandidateRequestShapeDomainFingerprintBySignal[Signal] = Services.BuildStableFingerprint(('physical-signal-request-dependency-domain-v2', Services.BuildPhysicalPortGlobalContractFingerprint(PhysicalPort) if PhysicalPort is not None else '', PhysicalChannel.ReservationFingerprint if PhysicalChannel is not None else '', State.FactorizedGuideFingerprintBySignal.get(Signal, ''), State.PhysicalExteriorRegionFingerprint, State.CertifiedApertureDomain.StableKeepoutCoreFingerprint if State.CertifiedApertureDomain is not None else '', getattr(State.Resources.ResourceGraph, 'GraphVersion', ''), getattr(State.Technology, 'TechnologyVersion', ''), repr(State.Technology), Signal, tuple(sorted(FixedRequiredNodes)), SortedBlockedNodeBase, tuple(SeedStarts), tuple(DetachedSeedAnchors), CandidateExpansionLimit, State.Policy.DetailedRouting.MinimumGuidePenalty, State.Policy.DetailedRouting.StrictBendPenalty, State.Policy.DetailedRouting.StrictViaPenalty, PhysicalRequestShapeDependencies))
        State.PhysicalRequestDomainFingerprintsBySignal[Signal] = State.CandidateRequestShapeDomainFingerprintBySignal[Signal]
        State.CandidateRequestDependencyComponentsBySignal[Signal] = {'GlobalContractFingerprint': Services.BuildPhysicalPortGlobalContractFingerprint(PhysicalPort) if PhysicalPort is not None else '', 'ChannelFingerprint': PhysicalChannel.ReservationFingerprint if PhysicalChannel is not None else '', 'GuideFactorFingerprint': State.FactorizedGuideFingerprintBySignal.get(Signal, ''), 'ExteriorRegionFingerprint': State.PhysicalExteriorRegionFingerprint, 'GlobalKeepoutFingerprint': State.CertifiedApertureDomain.StableKeepoutCoreFingerprint if State.CertifiedApertureDomain is not None else '', 'FixedRequiredNodesFingerprint': Services.BuildStableFingerprint(tuple(sorted(FixedRequiredNodes))), 'BlockedNodesFingerprint': Services.BuildStableFingerprint(SortedBlockedNodeBase), 'SeedStartsFingerprint': Services.BuildStableFingerprint(tuple(SeedStarts)), 'DetachedSeedAnchorsFingerprint': Services.BuildStableFingerprint(tuple(DetachedSeedAnchors)), 'DescriptorDomainFingerprint': Services.BuildStableFingerprint(PhysicalRequestShapeDependencies), 'DescriptorCount': len(PhysicalRequestShapeDependencies)}
        if State.CertifiedApertureDomain is not None and State.Resources.PreparingPhysicalComponentGlobalChannels:
            State.ApertureCandidateDomainIdentityBySignal[Signal] = Services.BuildPhysicalSignalApertureCandidateDomainIdentity(State.CertifiedApertureDomain, Signal, State.CandidateRequestShapeDomainFingerprintBySignal[Signal], SortedBlockedNodeBase, CoverageCursor=0, Complete=False)
        if PhysicalPort is not None and PhysicalChannel is not None:
            State.PortableRouteDomainPreparationBySignal[Signal] = Services.PreparePortablePhysicalSignalRouteDomain(State.PhysicalAssemblyPlan, Signal, RouteShapeDescriptors, FixedRequiredNodes, SortedBlockedNodeBase, SeedStarts, DetachedSeedAnchors)
        State.PhysicalRequestDescriptorFingerprintsBySignal[Signal] = tuple((Services.BuildStableFingerprint(Descriptor.DomainIdentity()) for Descriptor in RouteShapeDescriptors))
        for Request, DescriptorFingerprint in zip(RouteRequests, State.PhysicalRequestDescriptorFingerprintsBySignal[Signal]):
            State.PhysicalDescriptorOwnerByRequestId[id(Request)] = (Signal, DescriptorFingerprint)
        if State.Resources.PreparingPhysicalComponentGlobalChannels and Signal in State.CachedCertifiedEmptySignals and (Signal in State.ApertureCandidateDomainIdentityBySignal):
            EarlyContinuation = Services.SelectReplayablePhysicalSignalRouteDomainContinuation(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal[Signal].StableDomainFingerprint, Signal, State.PhysicalRequestDomainFingerprintsBySignal[Signal], State.PhysicalRequestDescriptorFingerprintsBySignal[Signal])
            if EarlyContinuation is not None and Services.PhysicalSignalRouteDomainIsCertifiedEmpty(EarlyContinuation, Signal=Signal, PreSiblingDomainFingerprint=State.ApertureCandidateDomainIdentityBySignal[Signal].StableDomainFingerprint, RequestDomainFingerprint=State.PhysicalRequestDomainFingerprintsBySignal[Signal]):
                raise State.StructuredRoutingStageError(Services.BuildCertifiedEmptyPhysicalSignalRouteDomainFailure(Signal, EarlyContinuation, State.CandidateRequestDependencyComponentsBySignal.get(Signal, {})))
        if bool(Services.os.environ.get('RCS_DEBUG_NEGOTIATED_REQUESTS')):
            print('[debug] authoritative: negotiated-route-requests', f'signal={Signal}', f'requests={len(RouteRequests)}', f'metadata={len(RouteMetadata)}')
    if State.Resources.PreparingPhysicalComponentGlobalChannels:
        RestoredExteriorDomainTelemetry = {}
        CurrentPortalIds = frozenset((Portal.PortalId for Values in State.Portals.values() for Portal in Values))
        for Signal, Identity in sorted(State.ApertureCandidateDomainIdentityBySignal.items()):
            DescriptorFingerprints = State.PhysicalRequestDescriptorFingerprintsBySignal.get(Signal, ())
            Continuation = Services.SelectReplayablePhysicalSignalRouteDomainContinuation(State.Resources.PhysicalSignalRouteDomainContinuationCache, Identity.StableDomainFingerprint, Signal, State.PhysicalRequestDomainFingerprintsBySignal.get(Signal, ''), DescriptorFingerprints)
            PortableReplay = False
            PortablePreparation = State.PortableRouteDomainPreparationBySignal.get(Signal)
            PortableReplayReason = 'exact-continuation-hit'
            if Continuation is None and PortablePreparation is not None:
                Continuation, PortableReplayReason = Services.SelectPreparedPortablePhysicalSignalRouteDomainContinuation(State.Resources.PhysicalGlobalApertureTemplateCache, PortablePreparation)
                PortableReplay = Continuation is not None
            if PortableReplay and Continuation is not None:
                Continuation, _PortablePublished = Services.RetainPhysicalSignalRouteDomainDescriptorProgress(State.Resources.PhysicalSignalRouteDomainContinuationCache, PreSiblingDomainFingerprint=Identity.StableDomainFingerprint, Signal=Signal, RequestDomainFingerprint=State.PhysicalRequestDomainFingerprintsBySignal.get(Signal, ''), RequestDescriptorFingerprints=DescriptorFingerprints, CompletedDescriptorFingerprints=(), Candidates=Continuation.Candidates, CandidateMetadata=dict(Continuation.CandidateMetadata))
            if Continuation is None:
                Continuation, _ZeroProgressPublished = Services.RetainPhysicalSignalRouteDomainDescriptorProgress(State.Resources.PhysicalSignalRouteDomainContinuationCache, PreSiblingDomainFingerprint=Identity.StableDomainFingerprint, Signal=Signal, RequestDomainFingerprint=State.PhysicalRequestDomainFingerprintsBySignal.get(Signal, ''), RequestDescriptorFingerprints=DescriptorFingerprints, CompletedDescriptorFingerprints=(), Candidates=(), CandidateMetadata={})
                if PortablePreparation is not None:
                    RestoredExteriorDomainTelemetry[Signal] = {'Reused': False, 'PortableReplay': False, 'Reason': PortableReplayReason}
            if not PortableReplay and isinstance(Continuation, Services.PhysicalSignalRouteDomainContinuation) and Services.PhysicalSignalRouteDomainIsCertifiedEmpty(Continuation, Signal=Signal, PreSiblingDomainFingerprint=Identity.StableDomainFingerprint, RequestDomainFingerprint=State.PhysicalRequestDomainFingerprintsBySignal.get(Signal, '')):
                raise State.StructuredRoutingStageError(Services.BuildCertifiedEmptyPhysicalSignalRouteDomainFailure(Signal, Continuation, State.CandidateRequestDependencyComponentsBySignal.get(Signal, {})))
            if any((Candidate.SourcePortalId not in CurrentPortalIds or any((PortalId not in CurrentPortalIds for PortalId in Candidate.TargetPortalIds.values())) for Candidate in Continuation.Candidates)):
                RestoredExteriorDomainTelemetry[Signal] = {'StableDomainFingerprint': Identity.StableDomainFingerprint, 'Reused': False, 'Reason': 'portal-identity-rebind-required', 'Complete': True}
                continue
            if isinstance(Continuation, Services.PhysicalSignalRouteDomainContinuation):
                State.WorkTelemetry.setdefault('PhysicalSignalRouteDomainDescriptorProgress', {})[Signal] = {**Continuation.ToProgressDictionary(), 'StrictlyAdvanced': False, 'Restored': True, 'PortableReplayProvenance': PortableReplay}
            State.PreSiblingCandidatesBySignal[Signal].extend(Continuation.Candidates)
            State.PreSiblingCandidateIdsBySignal[Signal].update((Candidate.CandidateId for Candidate in Continuation.Candidates))
            State.PreSiblingCandidateMetadataBySignal[Signal].update(dict(Continuation.CandidateMetadata))
            Filtered = Services.FilterPhysicalCandidatesAgainstSiblingApertures(Continuation.Candidates, State.AssemblySpecificSiblingAperturesBySignal.get(Signal, ()), ConflictClassifier=lambda Claims, SignalValue=Signal: State.AssemblySpecificSiblingApertureConflictSignals(SignalValue, Claims))
            State.CandidatesBySignal[Signal] = list(Filtered)
            State.CandidateAxisLaneBySignal.setdefault(Signal, {}).update({Candidate.CandidateId: Metadata for Candidate in Filtered if (Metadata := State.PreSiblingCandidateMetadataBySignal[Signal].get(Candidate.CandidateId)) is not None})
            if Continuation.Complete:
                State.RouteRequestsBySignal[Signal] = []
                State.RouteMetadataBySignal[Signal] = []
                State.CompleteExteriorRouteDomainSignals.add(Signal)
            else:
                CompletedDescriptors = Continuation.CompletedDescriptorFingerprints
                PendingRows = Services.SelectPendingPhysicalRouteDescriptorRows(State.RouteRequestsBySignal[Signal], State.RouteMetadataBySignal[Signal], DescriptorFingerprints, CompletedDescriptors)
                State.RouteRequestsBySignal[Signal] = [Request for Request, _Metadata, _Fingerprint in PendingRows]
                State.RouteMetadataBySignal[Signal] = [Metadata for _Request, Metadata, _Fingerprint in PendingRows]
                State.CompletedPhysicalDescriptorFingerprintsBySignal[Signal].update(CompletedDescriptors)
                State.IncompletePreSiblingDomainSignals.add(Signal)
            RestoredExteriorDomainTelemetry[Signal] = {'StableDomainFingerprint': Identity.StableDomainFingerprint, 'PreSiblingCandidateCount': len(Continuation.Candidates), 'FilteredCandidateCount': len(Filtered), 'SiblingFilterReapplied': True, 'PortableReplay': PortableReplay, 'PortalIdsRebound': PortableReplay, 'PortableReplayReason': PortableReplayReason, 'CompletedDescriptorCount': len(Continuation.CompletedDescriptorFingerprints), 'DescriptorCount': len(DescriptorFingerprints), 'RemainingDescriptorCount': len(Continuation.RemainingDescriptorFingerprints), 'Complete': Continuation.Complete, 'PortableCompletionTransferred': False}
        State.WorkTelemetry['PhysicalExteriorRouteDomainReuse'] = {'ReusedSignalCount': len(State.CompleteExteriorRouteDomainSignals), 'ReusedSignals': sorted(State.CompleteExteriorRouteDomainSignals), 'Domains': RestoredExteriorDomainTelemetry, 'CompleteDomainsOnly': True, 'PortableProofReuse': False, 'PortableLookup': {'HitCount': sum((Services.SelectPortableReplayTelemetryReason(Value) == 'hit' for Value in RestoredExteriorDomainTelemetry.values())), 'StructuralBucketMissCount': sum((Services.SelectPortableReplayTelemetryReason(Value) == 'structural-bucket-miss' for Value in RestoredExteriorDomainTelemetry.values())), 'FullIdentityMismatchCount': sum((Services.SelectPortableReplayTelemetryReason(Value) == 'full-identity-mismatch' for Value in RestoredExteriorDomainTelemetry.values())), 'PortalRebindMismatchCount': sum((Services.SelectPortableReplayTelemetryReason(Value) == 'portal-rebind-mismatch' for Value in RestoredExteriorDomainTelemetry.values()))}}
        State.WorkTelemetry['PhysicalGlobalCandidateRequestDependencyFingerprints'] = dict(sorted(State.CandidateRequestShapeDomainFingerprintBySignal.items()))
        State.WorkTelemetry['PhysicalGlobalCandidateRequestDependencyComponents'] = dict(sorted(State.CandidateRequestDependencyComponentsBySignal.items()))
        State.WorkTelemetry['PhysicalSignalApertureCandidateDomains'] = {Signal: {'DomainFingerprint': Identity.DomainFingerprint, 'ApertureFingerprint': Identity.ApertureFingerprint, 'StableKeepoutCoreFingerprint': Identity.StableKeepoutCoreFingerprint, 'BlockedNodesFingerprint': Identity.BlockedNodesFingerprint, 'CoverageCursor': Identity.CoverageCursor, 'Complete': Identity.Complete} for Signal, Identity in sorted(State.ApertureCandidateDomainIdentityBySignal.items())}
        State.WorkTelemetry['PhysicalPortGlobalContractFingerprintBySignal'] = {Port.Signal: Services.BuildPhysicalPortGlobalContractFingerprint(Port) for Port in sorted(Services.SelectPhysicalAssemblyGlobalBoundaryPorts(State.PhysicalAssemblyPlan), key=lambda Value: Value.Signal)}
        State.WorkTelemetry['PhysicalChannelFingerprintBySignal'] = {Channel.Signal: str(Channel.ReservationFingerprint) for Channel in sorted(getattr(State.PhysicalAssemblyPlan, 'PlanningChannels', ()), key=lambda Value: Value.Signal)}
        CurrentRequestApertureFactorKeys = frozenset((*((Signal, 'request-factor:' + Identity.StableDomainFingerprint) for Signal, Identity in sorted(State.ApertureCandidateDomainIdentityBySignal.items())), *((Factor.Signal, 'aperture-factor:' + Factor.ApertureFingerprint) for Factor in (State.CertifiedApertureDomain.Factors if State.CertifiedApertureDomain is not None else ()))))
        MatchingRequestApertureNoGood = next((NoGood for NoGood in sorted(State.Resources.RejectedPhysicalGlobalRequestApertureFactorSets, key=lambda Value: tuple(sorted(Value))) if NoGood and NoGood.issubset(CurrentRequestApertureFactorKeys)), None)
        if MatchingRequestApertureNoGood is not None:
            AffectedSignals = tuple(sorted({Signal for Signal, _Fingerprint in MatchingRequestApertureNoGood}))
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.TrackAssignmentConflict, Stage='PhysicalComponentGlobalRequestFactorDomain', AffectedNets=AffectedSignals, RepairActions=(), Detail='a completed request/aperture support clause rejects the selected physical global factor plan', Diagnostics={'PhysicalComponentGlobalPlanning': True, 'GlobalPlanDomainComplete': True, 'CompleteAssignmentCutProof': True, 'RequestApertureFactorProofReused': True, 'RequestApertureFactorNoGood': [list(Key) for Key in sorted(MatchingRequestApertureNoGood)], 'ConflictGraph': {'Classification': 'request-aperture-factor-no-good', 'ConflictSignals': list(AffectedSignals), 'NoCandidateSignals': list(AffectedSignals[:1]), 'PairwiseIncompatibleEdges': []}, 'ExecutableLegacyRepairCascade': False}))
        ReusablePhysicalCandidatesBySignal = Services.SelectReusablePhysicalPortCorridorCandidates(State.Resources.PhysicalPortCorridorDomainCache, {Port.Signal: Port for Port in getattr(State.PhysicalAssemblyPlan, 'Ports', ())}, State.CandidateRequestShapeDomainFingerprintBySignal, str(getattr(State.PhysicalAssemblyPlan, 'ResourceGraphFingerprint', '')), str(getattr(State.PhysicalAssemblyPlan, 'TechnologyFingerprint', '')), State.PhysicalCandidateRequestShapesBySignal) if State.PhysicalAssemblyPlan is not None else {}
        for Signal, Candidates in ReusablePhysicalCandidatesBySignal.items():
            if Signal in State.CompleteExteriorRouteDomainSignals:
                continue
            if Candidates:
                State.IncompletePreSiblingDomainSignals.add(Signal)
            State.CandidatesBySignal[Signal].extend(Services.FilterPhysicalCandidatesAgainstSiblingApertures(Candidates, State.AssemblySpecificSiblingAperturesBySignal.get(Signal, ()), ConflictClassifier=lambda Claims, SignalValue=Signal: State.AssemblySpecificSiblingApertureConflictSignals(SignalValue, Claims)))
            State.RouteRequestsBySignal[Signal] = []
            State.RouteMetadataBySignal[Signal] = []
        State.WorkTelemetry['PhysicalPortCorridorDomainReuse'] = {'CachedDomainCount': len(State.Resources.PhysicalPortCorridorDomainCache), 'ReusedSignalCount': len(ReusablePhysicalCandidatesBySignal), 'ReusedSignals': sorted(ReusablePhysicalCandidatesBySignal), 'ReusedCandidateCount': sum((len(Candidates) for Candidates in ReusablePhysicalCandidatesBySignal.values())), 'CompleteDomainsOnly': True}
    (LocalClaimReleaseDiagnostics): dict[str, object] = {'ReleasedSignals': [], 'OriginalLocalClaimCount': len(State.AllLocalClaims), 'RetainedLocalClaimCount': len(State.LocalClaims), 'ReusedRawPortalGeometry': False, 'RetainedCandidateSignals': sorted((Signal for Signal, Values in (State.RetainedCandidateCache or {}).items() if Values))}
    if State.UseNegotiatedRouting and State.LocalClaims and (not State.LocalClaimReleaseHistory):
        InitialAccessWindow = min(2, State.Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal) if State.Demand.TerminalCount > 64 else State.Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal
        (MandatoryAccessClaimsBySignal): dict[str, tuple[Services.RoutingResourceClaims, ...]] = {}
        LocalClaimNodesBySignal = {Signal: {Position for Claim in State.LocalClaims if Claim.Signal == Signal for Position in Claim.Nodes} for Signal in {Claim.Signal for Claim in State.LocalClaims}}
        for Signal in sorted(State.RouteRequestsBySignal):
            MandatoryValues = []
            for RequestIndex, Request in enumerate(State.RouteRequestsBySignal[Signal][:InitialAccessWindow]):
                if RequestIndex % 16 == 0:
                    State.CheckRuntimeBudget('LocalClaimReleasePreScreen', {'Signal': Signal, 'RequestIndex': RequestIndex})
                MandatoryNodes = set(Request[3]) - LocalClaimNodesBySignal.get(Signal, set())
                MandatoryValues.append(State.Resources.ResourceGraph.BuildRouteClaims(MandatoryNodes))
            if MandatoryValues:
                MandatoryAccessClaimsBySignal[Signal] = tuple(MandatoryValues)
        ReleaseSelection = Services.SelectAccessAwareLocalClaimReleases(MandatoryAccessClaimsBySignal, State.LocalClaims, MaximumExpansions=min(8192, State.AdaptiveBudget.AssignmentExpansions), WorkCheck=lambda Details: State.CheckRuntimeBudget('LocalClaimReleasePreScreen', Details))
        LocalClaimReleaseDiagnostics.update(ReleaseSelection.ToDictionary())
        State.WorkTelemetry['LocalClaimReleasePreScreen'] = LocalClaimReleaseDiagnostics
        if ReleaseSelection.ReleasedSignals:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='LocalClaimReleasePreScreen', AffectedNets=tuple(ReleaseSelection.ReleasedSignals), RepairActions=(), Detail='the fixed routing attempt conflicts with immutable local claims; automatic claim release is disabled', Diagnostics={'Action': 'terminal-fixed-domain-incomplete', 'Complete': False, 'LocalClaimReleaseSelection': ReleaseSelection.ToDictionary()}))
    if State.UseNegotiatedRouting:
        try:
            State.NegotiatedPlan = State.PlanNegotiatedWithTelemetry(State.Context, State.Profiles, State.RouteRequestsBySignal, State.RouteMetadataBySignal, State.Region, State.ReservedAccess, State.Resources, State.Technology, State.Policy, State.Deadline, State.AdaptiveExpiresAt, State.CheckRuntimeBudget, RegenerateSignals=State.RegenerateSignals, SeedCandidatesBySignal=State.CandidatesBySignal, LocalClaimReleaseDiagnostics=LocalClaimReleaseDiagnostics, RequestHigherLayerOnExactCut=State.Policy.AdaptiveRouting.Enabled and State.LayerCount < State.EffectiveMaximumLayerCount, AdvancePlacementOnExhaustedExactCut=State.TopologyRequiresJointPortfolio)
        except Services.RoutingStageError as Error:
            raise
        MissingNegotiatedSignals = tuple(sorted(set(State.Profiles) - set(State.NegotiatedPlan.SelectedCandidates)))
        if MissingNegotiatedSignals:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.TrackAssignmentConflict, Stage='NegotiatedDetailedRouting', AffectedNets=MissingNegotiatedSignals, Detail='negotiated detailed routing produced no legal route tree; legacy candidate materialization is not a fallback', RepairActions=('RelocateAffectedClusters',), Diagnostics={'MissingSignals': list(MissingNegotiatedSignals), 'OverflowProgression': list(State.NegotiatedPlan.OverflowProgression), **State.NegotiatedPlan.Diagnostics}))
        State.CandidatesBySignal = Services.defaultdict(list, {Signal: [Candidate] for Signal, Candidate in State.NegotiatedPlan.SelectedCandidates.items()})
        State.WorkTelemetry['NegotiatedRouting'] = {'Algorithm': 'negotiated-route-trees-v1', 'Iterations': len(State.NegotiatedPlan.Iterations), 'OverflowProgression': list(State.NegotiatedPlan.OverflowProgression), 'ReroutedSignals': list(State.NegotiatedPlan.ReroutedSignals), 'CachedNodeCount': State.NegotiatedPlan.CachedNodeCount, 'CachedEdgeCount': State.NegotiatedPlan.CachedEdgeCount, **State.NegotiatedPlan.Diagnostics}
    State.WorkTelemetry['CandidateRequestConstructionSeconds'] = round(Services.monotonic() - State.CandidateStarted, 6)
    State.CandidateRequestCount = max(1, sum((min(len(State.RouteRequestsBySignal[Signal]), State.InitialRequestLimit) for Signal in State.CandidateSignalOrder)))
    State.WorkTelemetry['InitialCandidateRequestsPerSignal'] = State.InitialRequestLimit
    State.WorkTelemetry['InitialRouteTreeRequestCount'] = State.CandidateRequestCount
    State.WorkTelemetry['RouteTreeRequestCount'] = State.CandidateRequestCount
    (InitialRequestsBySignal): dict[str, list[tuple[Services.Any, ...]]] = {}
    (InitialMetadataBySignal): dict[str, list[tuple[Services.Any, ...]]] = {}
    (InitialResultSlices): dict[str, tuple[int, int]] = {}
    (BatchedInitialRequests): list[tuple[Services.Any, ...]] = []
    UseMatureStagedInitialCandidateScheduler = Services.ShouldUseMatureStagedInitialCandidateScheduler(State.ApplyStagedPortfolioProof, State.CandidateDiversityLevel, State.ReservationVariant, State.LaneDiversityLevel, State.SkipStrictPortalReservation, bool(State.RetainedCandidateCache), bool(State.PriorCandidateCache), AllowPriorCandidateCache=State.ApplyTopologyPressurePortfolioStagedProof, ForcePhysicalAssemblyPlanning=bool(State.PhysicalAssemblyPlan is not None and (not State.HasExactPhysicalAssemblyChannels)))
    (CoordinatedContinuationTelemetry): dict[str, dict[str, object]] = {}
    State.WorkTelemetry['CoordinatedCandidateRequestContinuations'] = CoordinatedContinuationTelemetry

    def PortalTupleTelemetryIdentity(Metadata: tuple[Any, ...]) -> tuple[object, ...]:
        SourcePortal, TargetPortals, _Guide, Layer, Axis, Lane, Variant = Metadata
        return (SourcePortal.PortalId, tuple((Portal.PortalId for Portal in TargetPortals)), Layer, Axis, Lane, Variant)

    def MandatoryAccessTupleTelemetryIdentity(Metadata: tuple[Any, ...]) -> tuple[object, ...]:
        PortalTuple = PortalTupleTelemetryIdentity(Metadata)
        return (PortalTuple[0], PortalTuple[1], PortalTuple[2])

    def PortalTupleHasForeignAccessOverlap(Signal: str, Metadata: tuple[Any, ...]) -> bool:
        SourcePortal, TargetPortals, *_Rest = Metadata
        return bool({Position for Portal in (SourcePortal, *TargetPortals) for Position in Portal.Path} & State.ForeignBlockedNodesBySignal[Signal])
    for Signal in State.CandidateSignalOrder:
        if State.CandidatesBySignal.get(Signal) and (not (State.Resources.PreparingPhysicalComponentGlobalChannels and Signal in State.IncompletePreSiblingDomainSignals and State.RouteRequestsBySignal.get(Signal))):
            continue
        ApplyCoordinatedInitialWindow = Signal in State.CoordinatedCandidateDiversificationSignals and State.CoordinatedCandidateDiversityLevel > State.CandidateDiversityLevel
        SignalInitialWindowLimit = min(State.InitialRequestLimit, len(State.RouteRequestsBySignal[Signal]))
        SignalContinuationWindowLimit = SignalInitialWindowLimit
        ProofRequests = State.RouteRequestsBySignal[Signal][:SignalInitialWindowLimit]
        ProofMetadata = State.RouteMetadataBySignal[Signal][:SignalInitialWindowLimit]
        SignalRequests = State.RouteRequestsBySignal[Signal][:SignalContinuationWindowLimit]
        SignalMetadata = State.RouteMetadataBySignal[Signal][:SignalContinuationWindowLimit]
        StartIndex = len(BatchedInitialRequests)
        BatchedInitialRequests.extend(SignalRequests)
        InitialRequestsBySignal[Signal] = SignalRequests
        InitialMetadataBySignal[Signal] = SignalMetadata
        CoordinatedProfile = State.CoordinatedCandidateProfileTelemetry.get(Signal)
        if CoordinatedProfile is not None:
            CoordinatedProfile.update({'SelectedInitialRequestLimit': SignalInitialWindowLimit, 'SelectedInitialRequestCount': len(ProofRequests), 'SelectedInitialRequestFingerprint': Services.BuildStableFingerprint(ProofRequests), 'SelectedInitialPortalTupleFingerprint': Services.BuildStableFingerprint([PortalTupleTelemetryIdentity(Metadata) for Metadata in ProofMetadata]), 'SelectedInitialForeignAccessOverlapCount': sum((PortalTupleHasForeignAccessOverlap(Signal, Metadata) for Metadata in ProofMetadata))})
        if SignalContinuationWindowLimit > SignalInitialWindowLimit:
            ContinuationRequests = SignalRequests[SignalInitialWindowLimit:]
            ContinuationMetadata = SignalMetadata[SignalInitialWindowLimit:]
            ProofRequestFingerprints = {Services.BuildStableFingerprint(Request) for Request in ProofRequests}
            ContinuationRequestFingerprints = {Services.BuildStableFingerprint(Request) for Request in ContinuationRequests}
            ProofMandatoryAccessTuples = {MandatoryAccessTupleTelemetryIdentity(Metadata) for Metadata in ProofMetadata}
            ContinuationMandatoryAccessTuples = {MandatoryAccessTupleTelemetryIdentity(Metadata) for Metadata in ContinuationMetadata}
            CoordinatedContinuationTelemetry[Signal] = {'Applied': True, 'EffectiveContinuationLevel': State.CoordinatedCandidateDiversityLevel + 1, 'GlobalCandidateDiversityLevel': State.CandidateDiversityLevel, 'CurrentProofCount': len(ProofRequests), 'ContinuationLimit': len(SignalRequests), 'ContinuationRequestCount': len(ContinuationRequests), 'ContinuationRequestFingerprint': Services.BuildStableFingerprint(ContinuationRequests), 'ContinuationPortalTupleFingerprint': Services.BuildStableFingerprint([PortalTupleTelemetryIdentity(Metadata) for Metadata in ContinuationMetadata]), 'ContinuationMandatoryAccessTupleFingerprint': Services.BuildStableFingerprint(sorted(ContinuationMandatoryAccessTuples)), 'RequestIntersectionCount': len(ProofRequestFingerprints.intersection(ContinuationRequestFingerprints)), 'MandatoryAccessTupleIntersectionCount': len(ProofMandatoryAccessTuples.intersection(ContinuationMandatoryAccessTuples)), 'ProofMandatoryAccessTupleCount': len(ProofMandatoryAccessTuples), 'ContinuationMandatoryAccessTupleCount': len(ContinuationMandatoryAccessTuples), 'ContinuationForeignAccessOverlapCount': sum((PortalTupleHasForeignAccessOverlap(Signal, Metadata) for Metadata in ContinuationMetadata)), 'Verdict': 'planned'}
        InitialResultSlices[Signal] = (StartIndex, len(BatchedInitialRequests))
    ExecutedInitialRequestCountsBySignal = {Signal: len(Requests) for Signal, Requests in InitialRequestsBySignal.items()}
    InitialNativeBatchStarted = Services.monotonic()
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: initial native batch requests={len(BatchedInitialRequests)} fingerprint={Services.BuildStableFingerprint(BatchedInitialRequests)}', flush=True)
    (StagedInitialResult): Services.StagedInitialRouteTreeResult | None = None
    if UseMatureStagedInitialCandidateScheduler:
        StagedInitialResult = Services.GenerateStagedInitialRouteTrees(State.CandidateSignalOrder, InitialRequestsBySignal, State.GenerateRouteTreesWithDeadline, lambda Signal: Services.MayAdvanceStagedCandidateOnExhaustion(State.ApplyMaturePortfolioSearchCaps, State.ExactLegalRetainedJointStateCount, Signal, State.JointHigherOrderConstraintSignals), WorkCheck=lambda Details: None if State.RouteTreeNativeDeadlineExceeded else State.CheckRuntimeBudget('MatureStagedInitialCandidateScheduler', Details), StopAfterEverySignalHasTree=bool(State.CoordinatedCandidateDiversificationSignals or State.ApplyTopologyPressurePortfolioStagedProof or (State.PhysicalAssemblyPlan is not None and (not State.HasExactPhysicalAssemblyChannels))))
        BatchedInitialTrees = list(StagedInitialResult.RouteTrees)
        State.RouteTreeBatchCount = StagedInitialResult.BatchCount
        State.WorkTelemetry['MatureStagedInitialCandidateScheduler'] = {'Applied': True, 'FullPoolGenerated': StagedInitialResult.FullPoolGenerated, 'EverySignalHasTree': StagedInitialResult.EverySignalHasTree, 'ExhaustedSignals': list(StagedInitialResult.ExhaustedSignals), 'ExecutedRequestCount': StagedInitialResult.ExecutedRequestCount, 'PlannedRequestCount': StagedInitialResult.PlannedRequestCount, 'BatchCount': StagedInitialResult.BatchCount, 'ExecutedRequestCountsBySignal': dict(StagedInitialResult.ExecutedRequestCountsBySignal), 'FirstSuccessfulRequestIndicesBySignal': dict(StagedInitialResult.FirstSuccessfulRequestIndicesBySignal)}
        ExecutedRequestCountsBySignal = dict(StagedInitialResult.ExecutedRequestCountsBySignal)
        ExecutedInitialRequestCountsBySignal = dict(ExecutedRequestCountsBySignal)
        FirstSuccessfulRequestIndicesBySignal = dict(StagedInitialResult.FirstSuccessfulRequestIndicesBySignal)
        for Signal, Continuation in CoordinatedContinuationTelemetry.items():
            ProofCount = int(Continuation['CurrentProofCount'])
            FirstSuccessfulIndex = FirstSuccessfulRequestIndicesBySignal.get(Signal)
            ExecutedCount = ExecutedRequestCountsBySignal.get(Signal, 0)
            if FirstSuccessfulIndex is not None and FirstSuccessfulIndex < ProofCount:
                Verdict = 'proof-window-recovered'
            elif FirstSuccessfulIndex is not None:
                Verdict = 'continuation-recovered'
            elif Signal in StagedInitialResult.ExhaustedSignals:
                Verdict = 'continuation-exhausted'
            elif ExecutedCount <= ProofCount:
                Verdict = 'continuation-not-reached'
            else:
                Verdict = 'continuation-interrupted'
            Continuation.update({'ExecutedRequestCount': ExecutedCount, 'FirstSuccessfulRequestIndex': FirstSuccessfulIndex, 'Verdict': Verdict})
        State.WorkTelemetry['InitialRouteTreeRequestCount'] = StagedInitialResult.ExecutedRequestCount
        State.WorkTelemetry['RouteTreeRequestCount'] = StagedInitialResult.ExecutedRequestCount
        State.WorkTelemetry['PlannedInitialRouteTreeRequestCount'] = StagedInitialResult.PlannedRequestCount
    else:
        BatchedInitialTrees = State.GenerateRouteTreesWithDeadline(BatchedInitialRequests) if BatchedInitialRequests else []
        State.RouteTreeBatchCount = 1 if BatchedInitialRequests else 0
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: initial native batch result routed={sum((Value is not None for Value in BatchedInitialTrees))} fingerprint={Services.BuildStableFingerprint(BatchedInitialTrees)}', flush=True)
    State.WorkTelemetry['InitialNativeCandidateBatchSeconds'] = round(Services.monotonic() - InitialNativeBatchStarted, 6)
    if StagedInitialResult is not None and StagedInitialResult.ExhaustedSignals:
        ExhaustedSignals = StagedInitialResult.ExhaustedSignals
        (ExhaustedSignalDiagnostics): dict[str, dict[str, object]] = {}
        for Signal in ExhaustedSignals:
            Profile = State.Profiles[Signal]
            ExecutedInitialRequestCount = ExecutedInitialRequestCountsBySignal.get(Signal, 0)
            SignalDiagnostics = {'Requests': ExecutedInitialRequestCount, 'RoutedTrees': 0, 'Materialized': 0, 'DeferredRequests': State.DeferredRouteRequestCountsBySignal[Signal] + len(State.RouteRequestsBySignal[Signal]) - ExecutedInitialRequestCount, 'SeedNodes': sum((len(Claim.Nodes) for Claim in (Profile.Seed.LocalClaims if Profile.Seed is not None else ()))), 'SourcePortals': sum((len(State.Portals.get((Signal, Profile.Root, Layer), ())) for Layer in range(State.LayerCount))), 'TargetPortals': sum((len(State.Portals.get((Signal, Target, Layer), ())) for Target in Profile.Targets for Layer in range(State.LayerCount))), 'ForeignBlockedNodes': len(State.ForeignBlockedNodesBySignal[Signal]), 'ForeignPortalOverlapRequests': State.ForeignPortalOverlapBySignal[Signal], 'FrozenComponentPortalConflictRequests': State.FrozenComponentPortalConflictBySignal[Signal], 'PriorCandidates': 0, 'Rejections': {}}
            State.CandidateDiagnostics[Signal] = SignalDiagnostics
            ExhaustedSignalDiagnostics[Signal] = SignalDiagnostics
        PrimarySignal = ExhaustedSignals[0]
        PrimarySignalDiagnostics = ExhaustedSignalDiagnostics[PrimarySignal]
        CandidateFailureFingerprint = Services.BuildStableFingerprint({'Signals': list(ExhaustedSignals), 'Diagnostics': ExhaustedSignalDiagnostics, 'PortalReservations': [Value.ToDictionary() for Value in State.PortalReservations], 'LayerCount': State.LayerCount, 'LaneCount': State.RouteLaneCount})
        CandidateStarvationClassFingerprint = Services.BuildCandidateStarvationClassFingerprint(PrimarySignal, PrimarySignalDiagnostics)
        PortfolioAdvance = {'Stage': 'CandidateGeneration', 'Action': 'advance-retained-joint-portfolio-candidate-starvation', 'AffectedSignals': list(ExhaustedSignals), 'ExactLegalRetainedJointStateCount': State.ExactLegalRetainedJointStateCount, 'JointHigherOrderConstraintCount': State.JointHigherOrderConstraintCount, 'JointPairwiseConstraintCount': State.JointPairwiseConstraintCount, 'CandidateFailureFingerprint': CandidateFailureFingerprint, 'CandidateStarvationClassFingerprint': CandidateStarvationClassFingerprint, 'Diagnostics': ExhaustedSignalDiagnostics}
        State.WorkTelemetry['PortfolioCandidateStarvationAdvance'] = PortfolioAdvance
        State.StageTimings['CandidateGeneration'] = Services.monotonic() - State.CandidateStarted
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.TrackAssignmentConflict, Stage='Candidate', AffectedNets=tuple(ExhaustedSignals), RepairActions=('AdvancePlacementCandidate',), Detail='the complete staged initial candidate window produced no native route tree while another exact-legal access-distinct joint placement remains', Diagnostics={'Action': PortfolioAdvance['Action'], 'ConflictGraph': {'Classification': 'candidate-starvation-placement-conflict', 'ConflictSignals': list(ExhaustedSignals), 'NoCandidateSignals': list(ExhaustedSignals), 'RelocationSignals': list(ExhaustedSignals), 'PriorityRelocationSignals': list(ExhaustedSignals)}, 'CandidateDiagnostics': ExhaustedSignalDiagnostics, 'ExactLegalRetainedJointStateCount': State.ExactLegalRetainedJointStateCount, 'JointHigherOrderConstraintCount': State.JointHigherOrderConstraintCount, 'JointPairwiseConstraintCount': State.JointPairwiseConstraintCount, 'MatureStagedInitialCandidateScheduler': State.WorkTelemetry['MatureStagedInitialCandidateScheduler']}))
    (SeedPoolPreMaterializedSignals): list[str] = []
    if StagedInitialResult is not None and StagedInitialResult.EverySignalHasTree and (not StagedInitialResult.FullPoolGenerated) and (not State.Resources.PreparingPhysicalComponentGlobalChannels):
        for Signal in State.CandidateSignalOrder:
            if State.CandidatesBySignal.get(Signal):
                continue
            ResultStart, ResultEnd = InitialResultSlices[Signal]
            ExecutedInitialRequestCount = ExecutedInitialRequestCountsBySignal.get(Signal, 0)
            RoutedTrees = BatchedInitialTrees[ResultStart:min(ResultEnd, ResultStart + ExecutedInitialRequestCount)]
            MetadataValues = InitialMetadataBySignal[Signal][:ExecutedInitialRequestCount]
            Profile = State.Profiles[Signal]
            for RoutedTree, Metadata in zip(RoutedTrees, MetadataValues):
                if RoutedTree is None:
                    continue
                SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant = Metadata
                (RejectionCounts): Services.Counter[str] = Services.Counter()
                Candidate = Services.PortalOperations._MaterializeCandidate(Signal, Profile, SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant, RoutedTree, State.Region, State.Resources, State.Technology, State.Policy.DetailedRouting.LengthPenalty, State.Policy.DetailedRouting.CandidateBendWeight, State.Policy.DetailedRouting.CandidateViaWeight, State.Policy.DetailedRouting.LayerPenalty, 0 if State.CoarsePlan is None else (len(Guide.symmetric_difference(State.CoarsePlan.Guides[Signal])) + (0 if Layer == State.CoarsePlan.Layers[Signal] else State.Policy.GlobalRouting.OverflowPenalty)) * State.Policy.GlobalRouting.ExistingGuideHintWeight, State.Policy.DetailedRouting.RepeaterPenalty, RejectionCounts=RejectionCounts)
                if Candidate is None:
                    continue
                if any((Claim.Signal != Signal and Services.ComponentClaimsConflict(Candidate.Claims, Claim.Claims) for Claim in State.FrozenComponentClaims)):
                    RejectionCounts['FrozenComponentConflict'] += 1
                    continue
                if Candidate.CandidateId not in State.PreSiblingCandidateIdsBySignal[Signal]:
                    State.PreSiblingCandidateIdsBySignal[Signal].add(Candidate.CandidateId)
                    State.PreSiblingCandidatesBySignal[Signal].append(Candidate)
                    State.PreSiblingCandidateMetadataBySignal[Signal][Candidate.CandidateId] = (Axis, Lane, Layer, Candidate.SeedNodeCount)
                SiblingApertureConflictSignals = State.AssemblySpecificSiblingApertureConflictSignals(Signal, Candidate.Claims)
                if SiblingApertureConflictSignals:
                    RejectionCounts['SiblingApertureConflict'] += 1
                    State.CandidateDiagnostics.setdefault(Signal, {}).setdefault('SiblingApertureConflictSignals', [])[:] = sorted({*State.CandidateDiagnostics.get(Signal, {}).get('SiblingApertureConflictSignals', ()), *SiblingApertureConflictSignals})
                    continue
                State.CandidatesBySignal[Signal].append(Candidate)
                State.CandidateAxisLaneBySignal.setdefault(Signal, {})[Candidate.CandidateId] = (Axis, Lane, Layer, Candidate.SeedNodeCount)
                SeedPoolPreMaterializedSignals.append(Signal)
                break
        State.WorkTelemetry['CoordinatedSeedPoolPreMaterialization'] = {'Applied': True, 'MaterializedSignalCount': len(SeedPoolPreMaterializedSignals), 'MaterializedSignals': sorted(SeedPoolPreMaterializedSignals), 'UnmaterializedSignals': sorted(set(State.CandidateSignalOrder) - set(SeedPoolPreMaterializedSignals) - {Signal for Signal in State.CandidateSignalOrder if State.CandidatesBySignal.get(Signal)})}
    CandidateSignalRank = {Signal: Index for Index, Signal in enumerate(State.CandidateSignalOrder)}

    def InitialRoutedTreeCount(Signal: str) -> int:
        if State.CandidatesBySignal.get(Signal):
            return len(State.CandidatesBySignal[Signal])
        ResultStart, ResultEnd = InitialResultSlices[Signal]
        ExecutedInitialRequestCount = ExecutedInitialRequestCountsBySignal.get(Signal, 0)
        return sum((Value is not None for Value in BatchedInitialTrees[ResultStart:min(ResultEnd, ResultStart + ExecutedInitialRequestCount)]))
    CandidateMaterializationOrder = sorted(State.CandidateSignalOrder, key=lambda Signal: (-InitialRoutedTreeCount(Signal), CandidateSignalRank[Signal]))
    State.MaximumCandidates = min(State.Policy.TrackAssignment.MaximumRouteCandidatesPerNet, State.AdaptiveBudget.CandidatesPerNet) if State.Policy.AdaptiveRouting.Enabled else State.Policy.TrackAssignment.MaximumRouteCandidatesPerNet
    for Signal in CandidateMaterializationOrder:
        if Signal in State.CompleteExteriorRouteDomainSignals:
            State.CandidateLimitsBySignal[Signal] = len(State.CandidatesBySignal[Signal])
            State.CandidateDiagnostics[Signal] = {'Cached': True, 'PreSiblingDomainCached': True, 'Requests': 0, 'RoutedTrees': 0, 'Materialized': len(State.CandidatesBySignal[Signal]), 'PreSiblingMaterialized': len(State.PreSiblingCandidatesBySignal[Signal]), 'DeferredRequests': 0, 'SourcePortals': 0, 'TargetPortals': 0, 'Rejections': {}}
            continue
        if State.CandidatesBySignal.get(Signal) and (not (State.Resources.PreparingPhysicalComponentGlobalChannels and Signal in State.IncompletePreSiblingDomainSignals and State.RouteRequestsBySignal.get(Signal))):
            State.CandidateLimitsBySignal[Signal] = len(State.CandidatesBySignal[Signal])
            State.CandidateDiagnostics[Signal] = {'Cached': True, 'Requests': 0, 'RoutedTrees': 0, 'Materialized': len(State.CandidatesBySignal[Signal]), 'DeferredRequests': 0, 'SourcePortals': 0, 'TargetPortals': 0, 'Rejections': {}}
            continue
        Profile = State.Profiles[Signal]
        CandidateExpansionLimit = State.CandidateExpansionLimits[Signal]
        RouteRequests = State.RouteRequestsBySignal[Signal]
        RouteMetadata = State.RouteMetadataBySignal[Signal]
        ExecutedInitialRequestCount = ExecutedInitialRequestCountsBySignal.get(Signal, 0)
        InitialRouteRequests = InitialRequestsBySignal[Signal][:ExecutedInitialRequestCount]
        InitialRouteMetadata = InitialMetadataBySignal[Signal][:ExecutedInitialRequestCount]
        ResultStart, ResultEnd = InitialResultSlices[Signal]
        RoutedTrees = BatchedInitialTrees[ResultStart:min(ResultEnd, ResultStart + ExecutedInitialRequestCount)]
        (RejectionCounts): Services.Counter[str] = Services.Counter()
        State.CandidateDiagnostics[Signal] = {'Requests': len(InitialRouteRequests), 'RoutedTrees': sum((Value is not None for Value in RoutedTrees)), 'Materialized': 0, 'DeferredRequests': State.DeferredRouteRequestCountsBySignal[Signal] + len(RouteRequests) - len(InitialRouteRequests), 'SeedNodes': sum((len(Claim.Nodes) for Claim in (Profile.Seed.LocalClaims if Profile.Seed is not None else ()))), 'SourcePortals': sum((len(State.Portals.get((Signal, Profile.Root, Layer), ())) for Layer in range(State.LayerCount))), 'TargetPortals': sum((len(State.Portals.get((Signal, Target, Layer), ())) for Target in Profile.Targets for Layer in range(State.LayerCount))), 'ForeignBlockedNodes': len(State.ForeignBlockedNodesBySignal[Signal]), 'ForeignPortalOverlapRequests': State.ForeignPortalOverlapBySignal[Signal], 'FrozenComponentPortalConflictRequests': State.FrozenComponentPortalConflictBySignal[Signal]}

        def MaterializeBatch(Trees: list[Any], MetadataValues: list[tuple[Any, ...]], *, SignalValue: str=Signal, ProfileValue: Any=Profile, RejectionCountsValue: Counter[str]=RejectionCounts) -> None:
            for RoutedTree, Metadata in zip(Trees, MetadataValues):
                SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant = Metadata
                Candidate = Services.PortalOperations._MaterializeCandidate(SignalValue, ProfileValue, SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant, RoutedTree, State.Region, State.Resources, State.Technology, State.Policy.DetailedRouting.LengthPenalty, State.Policy.DetailedRouting.CandidateBendWeight, State.Policy.DetailedRouting.CandidateViaWeight, State.Policy.DetailedRouting.LayerPenalty, 0 if State.CoarsePlan is None else (len(Guide.symmetric_difference(State.CoarsePlan.Guides[Signal])) + (0 if Layer == State.CoarsePlan.Layers[Signal] else State.Policy.GlobalRouting.OverflowPenalty)) * State.Policy.GlobalRouting.ExistingGuideHintWeight, State.Policy.DetailedRouting.RepeaterPenalty, RejectionCounts=RejectionCountsValue)
                if Candidate is not None:
                    if any((Claim.Signal != SignalValue and Services.ComponentClaimsConflict(Candidate.Claims, Claim.Claims) for Claim in State.FrozenComponentClaims)):
                        RejectionCountsValue['FrozenComponentConflict'] += 1
                        continue
                    if Candidate.CandidateId not in State.PreSiblingCandidateIdsBySignal[SignalValue]:
                        State.PreSiblingCandidateIdsBySignal[SignalValue].add(Candidate.CandidateId)
                        State.PreSiblingCandidatesBySignal[SignalValue].append(Candidate)
                        State.PreSiblingCandidateMetadataBySignal[SignalValue][Candidate.CandidateId] = (Axis, Lane, Layer, Candidate.SeedNodeCount)
                    SiblingApertureConflictSignals = State.AssemblySpecificSiblingApertureConflictSignals(SignalValue, Candidate.Claims)
                    if SiblingApertureConflictSignals:
                        RejectionCountsValue['SiblingApertureConflict'] += 1
                        State.CandidateDiagnostics[SignalValue]['SiblingApertureConflictSignals'] = sorted({*State.CandidateDiagnostics[SignalValue].get('SiblingApertureConflictSignals', ()), *SiblingApertureConflictSignals})
                        continue
                    State.CandidatesBySignal[SignalValue].append(Candidate)
                    State.CandidateDiagnostics[SignalValue]['Materialized'] += 1
                    State.CandidateAxisLaneBySignal.setdefault(SignalValue, {})[Candidate.CandidateId] = (Axis, Lane, Layer, Candidate.SeedNodeCount)
        if State.Resources.PreparingPhysicalComponentGlobalChannels:

            def ConsumePhysicalGlobalCandidateSuffix(MaximumRequestCount: int, *, SignalValue: str=Signal, RouteRequestsValue: list[Any]=RouteRequests, RouteMetadataValue: list[tuple[Any, ...]]=RouteMetadata, MaterializeBatchValue: Callable[[list[Any], list[tuple[Any, ...]]], None]=MaterializeBatch, RejectionCountsValue: Counter[str]=RejectionCounts) -> dict[str, object]:
                if MaximumRequestCount < 1:
                    raise ValueError('MaximumRequestCount must be positive')
                Diagnostics = State.CandidateDiagnostics[SignalValue]
                StartIndex = int(Diagnostics['Requests'])
                EndIndex = min(len(RouteRequestsValue), StartIndex + MaximumRequestCount)
                Requests = RouteRequestsValue[StartIndex:EndIndex]
                MetadataValues = RouteMetadataValue[StartIndex:EndIndex]
                if Requests:
                    Trees = State.GenerateRouteTreesWithDeadline(Requests)
                    State.RouteTreeBatchCount += 1
                    State.CandidateRequestCount += len(Requests)
                    MaterializeBatchValue(Trees, MetadataValues)
                    Diagnostics['Requests'] = EndIndex
                    Diagnostics['RoutedTrees'] = int(Diagnostics['RoutedTrees']) + sum((Tree is not None for Tree in Trees))
                    Diagnostics['DeferredRequests'] = max(0, int(Diagnostics['DeferredRequests']) - len(Requests))
                    Diagnostics['Rejections'] = dict(RejectionCountsValue)
                    ProgressIdentity = State.ApertureCandidateDomainIdentityBySignal[SignalValue]
                    Progress, StrictlyAdvanced = Services.RetainPhysicalSignalRouteDomainDescriptorProgress(State.Resources.PhysicalSignalRouteDomainContinuationCache, PreSiblingDomainFingerprint=ProgressIdentity.StableDomainFingerprint, Signal=SignalValue, RequestDomainFingerprint=State.PhysicalRequestDomainFingerprintsBySignal[SignalValue], RequestDescriptorFingerprints=State.PhysicalRequestDescriptorFingerprintsBySignal[SignalValue], CompletedDescriptorFingerprints=State.CompletedPhysicalDescriptorFingerprintsBySignal[SignalValue], Candidates=State.PreSiblingCandidatesBySignal[SignalValue], CandidateMetadata=State.PreSiblingCandidateMetadataBySignal[SignalValue])
                    Diagnostics['DeferredRequests'] = len(Progress.RemainingDescriptorFingerprints)
                    State.WorkTelemetry.setdefault('PhysicalSignalRouteDomainDescriptorProgress', {})[SignalValue] = {**Progress.ToProgressDictionary(), 'StrictlyAdvanced': StrictlyAdvanced}
                    if Progress.Complete:
                        State.CompleteExteriorRouteDomainSignals.add(SignalValue)
                        State.IncompletePreSiblingDomainSignals.discard(SignalValue)
                    if State.RouteTreeNativeDeadlineExceeded and Progress.RemainingDescriptorFingerprints:
                        Services.EnforceRoutingRuntimeLimit(Deadline=State.Deadline, AdaptiveStartedAt=State.RoutingStarted, AdaptiveExpiresAt=State.AdaptiveExpiresAt, Stage='Candidate', Diagnostics={**State.CurrentRuntimeBudgetDiagnostics(), 'Signal': SignalValue, 'CompletedDescriptorCount': len(Progress.CompletedDescriptorFingerprints), 'RemainingDescriptorCount': len(Progress.RemainingDescriptorFingerprints), 'DescriptorProgressPublished': True, 'GlobalPlanDomainComplete': False, 'CompleteAssignmentCutProof': False}, NativeDeadlineExceeded=True)
                return {'Signal': SignalValue, 'StartIndex': StartIndex, 'EndIndex': EndIndex, 'RequestCount': len(Requests), 'RemainingRequestCount': int(Diagnostics['DeferredRequests']), 'MaterializedCandidateCount': len(State.CandidatesBySignal[SignalValue])}
            State.PhysicalGlobalCandidateSuffixConsumers[Signal] = ConsumePhysicalGlobalCandidateSuffix
        MaterializeBatch(RoutedTrees, InitialRouteMetadata)
        PriorValues = tuple((State.PriorCandidateCache or {}).get(Signal, ()))
        if PriorValues:
            State.IncompletePreSiblingDomainSignals.add(Signal)
        PriorCandidateIds = frozenset((Candidate.CandidateId for Candidate in PriorValues))
        ExistingCandidateIds = {Candidate.CandidateId for Candidate in State.CandidatesBySignal[Signal]}
        State.CandidatesBySignal[Signal].extend((Candidate for Candidate in Services.FilterPhysicalCandidatesAgainstSiblingApertures(PriorValues, State.AssemblySpecificSiblingAperturesBySignal.get(Signal, ()), ConflictClassifier=lambda Claims, SignalValue=Signal: State.AssemblySpecificSiblingApertureConflictSignals(SignalValue, Claims)) if Candidate.CandidateId not in ExistingCandidateIds and (not any((Claim.Signal != Signal and Services.ComponentClaimsConflict(Candidate.Claims, Claim.Claims) for Claim in State.FrozenComponentClaims)))))
        State.CandidateDiagnostics[Signal]['PriorCandidates'] = len(PriorValues)
        State.CandidateDiagnostics[Signal]['Rejections'] = dict(RejectionCounts)
        InitialRoutedTreeCountValue = int(State.CandidateDiagnostics[Signal]['RoutedTrees'])
        InitialFixedLegalityRejectedEveryRoutedTree = bool(State.PlacementWasRelocated and InitialRoutedTreeCountValue > 0 and (sum((int(RejectionCounts.get(Reason, 0)) for Reason in ('SelfClaimConflict', 'NoRepeater'))) >= InitialRoutedTreeCountValue))
        CutScopedFixedLegalityContinuationApplied = False
        CutScopedFixedLegalityContinuationExhausted = False
        if Services.ShouldContinueCutScopedFixedLegalityWindow(PlacementWasRelocated=State.PlacementWasRelocated, ExactLegalRetainedJointStateCount=State.ExactLegalRetainedJointStateCount, HasCumulativeAssignmentConstraints=State.HasCumulativeAssignmentConstraints, CandidateDiversityLevel=State.CandidateDiversityLevel, ReservationVariant=State.ReservationVariant, LaneDiversityLevel=State.LaneDiversityLevel, SkipStrictPortalReservation=State.SkipStrictPortalReservation, Signal=Signal, JointAssignmentConstraintSignals=State.JointAssignmentConstraintSignals, RoutedTreeCount=InitialRoutedTreeCountValue, MaterializedCandidateCount=len(State.CandidatesBySignal[Signal]), AllRoutedTreesRejectedByFixedLegality=InitialFixedLegalityRejectedEveryRoutedTree, DeferredRequestCount=int(State.CandidateDiagnostics[Signal]['DeferredRequests']), HasCompleteClusterBoundaryLease=bool(State.BoundaryLeaseReservations)):
            ContinuationStart = len(InitialRouteRequests)
            ContinuationEnd = min(len(RouteRequests), ContinuationStart + State.InitialRequestLimit)
            ContinuationRequests = RouteRequests[ContinuationStart:ContinuationEnd]
            ContinuationMetadata = RouteMetadata[ContinuationStart:ContinuationEnd]
            if ContinuationRequests:
                ContinuationStarted = Services.monotonic()
                ContinuationTrees = State.GenerateRouteTreesWithDeadline(ContinuationRequests)
                State.RouteTreeBatchCount += 1
                State.CandidateRequestCount += len(ContinuationRequests)
                MaterializeBatch(ContinuationTrees, ContinuationMetadata)
                State.CandidateDiagnostics[Signal]['Requests'] += len(ContinuationRequests)
                State.CandidateDiagnostics[Signal]['RoutedTrees'] += sum((Tree is not None for Tree in ContinuationTrees))
                State.CandidateDiagnostics[Signal]['DeferredRequests'] = max(0, int(State.CandidateDiagnostics[Signal]['DeferredRequests']) - len(ContinuationRequests))
                State.CandidateDiagnostics[Signal]['Rejections'] = dict(RejectionCounts)
                CutScopedFixedLegalityContinuationApplied = True
                ContinuedRoutedTreeCount = int(State.CandidateDiagnostics[Signal]['RoutedTrees'])
                CutScopedFixedLegalityContinuationExhausted = bool(not State.CandidatesBySignal[Signal] and ContinuedRoutedTreeCount > 0 and (sum((int(RejectionCounts.get(Reason, 0)) for Reason in ('SelfClaimConflict', 'NoRepeater'))) >= ContinuedRoutedTreeCount))
                ContinuationTelemetry = {'Applied': True, 'Signal': Signal, 'StartIndex': ContinuationStart, 'EndIndex': ContinuationEnd, 'RequestCount': len(ContinuationRequests), 'RoutedTreeCount': sum((Tree is not None for Tree in ContinuationTrees)), 'MaterializedCandidateCount': len(State.CandidatesBySignal[Signal]), 'ExhaustedByFixedLegality': CutScopedFixedLegalityContinuationExhausted, 'ElapsedSeconds': round(Services.monotonic() - ContinuationStarted, 6), 'RequestFingerprint': Services.BuildStableFingerprint(ContinuationRequests)}
                State.WorkTelemetry.setdefault('CutScopedFixedLegalityContinuations', []).append(ContinuationTelemetry)
        if State.Resources.PreparingPhysicalComponentGlobalChannels:
            ZeroWitnessContinuationRequestLimit = max(State.InitialRequestLimit, min(32, len(RouteRequests)))
            ContinuationStart = int(State.CandidateDiagnostics[Signal]['Requests'])
            while not State.CandidatesBySignal[Signal] and ContinuationStart < len(RouteRequests):
                ContinuationEnd = min(len(RouteRequests), ContinuationStart + ZeroWitnessContinuationRequestLimit)
                ContinuationRequests = RouteRequests[ContinuationStart:ContinuationEnd]
                ContinuationMetadata = RouteMetadata[ContinuationStart:ContinuationEnd]
                ContinuationTrees = State.GenerateRouteTreesWithDeadline(ContinuationRequests)
                State.RouteTreeBatchCount += 1
                State.CandidateRequestCount += len(ContinuationRequests)
                MaterializeBatch(ContinuationTrees, ContinuationMetadata)
                State.CandidateDiagnostics[Signal]['Requests'] += len(ContinuationRequests)
                State.CandidateDiagnostics[Signal]['RoutedTrees'] += sum((Tree is not None for Tree in ContinuationTrees))
                State.CandidateDiagnostics[Signal]['DeferredRequests'] = max(0, int(State.CandidateDiagnostics[Signal]['DeferredRequests']) - len(ContinuationRequests))
                ContinuationStart = ContinuationEnd
            State.WorkTelemetry.setdefault('PhysicalComponentGlobalCandidateContinuations', []).append({'Signal': Signal, 'ExecutedRequestCount': int(State.CandidateDiagnostics[Signal]['Requests']), 'RemainingRequestCount': int(State.CandidateDiagnostics[Signal]['DeferredRequests']), 'MaterializedCandidateCount': len(State.CandidatesBySignal[Signal]), 'ContinuationRequestLimit': ZeroWitnessContinuationRequestLimit, 'RecursiveRetryCount': 0})
        State.CandidateDiagnostics[Signal]['Rejections'] = dict(RejectionCounts)
        if State.CoarsePlan is None:

            def CandidateOrder(Value: NetRouteCandidate) -> tuple[Any, ...]:
                return (Value.MaterialCost, Value.FootprintGrowth, -State.CandidateAxisLaneBySignal[Signal][Value.CandidateId][3], Value.IncrementalMaterialCost, Value.IncrementalLength, Value.Length, Value.BendCount, Value.ViaCount, Value.CandidateId)
        else:
            PlannedAxis = State.CoarsePlan.Axes[Signal]
            PlannedLane = State.CoarsePlan.Lanes[Signal]
            PlannedLayer = State.CoarsePlan.Layers[Signal]

            def CandidateOrder(Value: NetRouteCandidate) -> tuple[Any, ...]:
                CandidateAxis, CandidateLane, CandidateLayer, SeedNodes = State.CandidateAxisLaneBySignal[Signal][Value.CandidateId]
                return (Value.MaterialCost, 0 if CandidateAxis == PlannedAxis else 1, 0 if CandidateLane == PlannedLane else 1, 0 if CandidateLayer == PlannedLayer else 1, Value.FootprintGrowth, -SeedNodes, Value.IncrementalMaterialCost, Value.IncrementalLength, Value.Length, Value.BendCount, Value.ViaCount, Value.CandidateId)
        (CandidatesByTrack): dict[tuple[int, frozenset[Services.Position2]], list[Services.NetRouteCandidate]] = Services.defaultdict(list)
        for Candidate in State.CandidatesBySignal[Signal]:
            Key = (Candidate.Layer, Candidate.Guide)
            CandidatesByTrack[Key].append(Candidate)
        for Values in CandidatesByTrack.values():
            Values.sort(key=CandidateOrder)
        State.MaximumCandidates = min(State.Policy.TrackAssignment.MaximumRouteCandidatesPerNet, State.AdaptiveBudget.CandidatesPerNet) if State.Policy.AdaptiveRouting.Enabled else State.Policy.TrackAssignment.MaximumRouteCandidatesPerNet
        if State.Policy.AdaptiveRouting.Enabled:
            ClaimWork = max(1, Profile.Span) * max(1, len(Profile.Targets))
            WorkScale = max(1, Services.ceil(Services.sqrt(ClaimWork / State.Policy.AdaptiveRouting.CandidateClaimWorkQuantum)))
            SignalMaximumCandidates = max(State.Policy.AdaptiveRouting.MinimumCandidatesPerNet, min(State.MaximumCandidates * max(1, WorkScale), State.Policy.TrackAssignment.MaximumRouteCandidatesPerNet))
        else:
            SignalMaximumCandidates = min(len(State.CandidatesBySignal[Signal]), State.Policy.TrackAssignment.MaximumRouteCandidatesPerNet)
        State.CandidateLimitsBySignal[Signal] = SignalMaximumCandidates
        PerLayer = max(1, SignalMaximumCandidates // State.LayerCount)
        DiverseCandidates = []
        if State.UnreservedPortalMode:
            for Layer in range(State.LayerCount):
                LayerTracks = sorted((Values for (TrackLayer, _Guide), Values in CandidatesByTrack.items() if TrackLayer == Layer), key=lambda Values: CandidateOrder(Values[0]))
                for Values in LayerTracks:
                    DiverseCandidates.extend(Values)
        else:
            for Layer in range(State.LayerCount):
                LayerTracks = sorted((Values for (TrackLayer, _Guide), Values in CandidatesByTrack.items() if TrackLayer == Layer), key=lambda Values: CandidateOrder(Values[0]))
                LayerValues = []
                VariantIndex = 0
                while len(LayerValues) < PerLayer:
                    Added = False
                    for Values in LayerTracks:
                        if VariantIndex < len(Values):
                            LayerValues.append(Values[VariantIndex])
                            Added = True
                            if len(LayerValues) == PerLayer:
                                break
                    if not Added:
                        break
                    VariantIndex += 1
                DiverseCandidates.extend(LayerValues)
        State.CandidatesBySignal[Signal] = Services.SelectBoundedDiverseCandidatePool(DiverseCandidates, SignalMaximumCandidates, PriorCandidateIds)
        if State.Resources.PreparingPhysicalComponentGlobalChannels and Signal in State.ApertureCandidateDomainIdentityBySignal:
            ProgressIdentity = State.ApertureCandidateDomainIdentityBySignal[Signal]
            Progress, ProgressStrictlyAdvanced = Services.RetainPhysicalSignalRouteDomainDescriptorProgress(State.Resources.PhysicalSignalRouteDomainContinuationCache, PreSiblingDomainFingerprint=ProgressIdentity.StableDomainFingerprint, Signal=Signal, RequestDomainFingerprint=State.PhysicalRequestDomainFingerprintsBySignal[Signal], RequestDescriptorFingerprints=State.PhysicalRequestDescriptorFingerprintsBySignal[Signal], CompletedDescriptorFingerprints=State.CompletedPhysicalDescriptorFingerprintsBySignal[Signal], Candidates=State.PreSiblingCandidatesBySignal[Signal], CandidateMetadata=State.PreSiblingCandidateMetadataBySignal[Signal])
            RemainingDescriptorCount = len(Progress.RemainingDescriptorFingerprints)
            State.CandidateDiagnostics[Signal]['DeferredRequests'] = RemainingDescriptorCount
            State.WorkTelemetry.setdefault('PhysicalSignalRouteDomainDescriptorProgress', {})[Signal] = {**Progress.ToProgressDictionary(), 'StrictlyAdvanced': ProgressStrictlyAdvanced}
            if Progress.Complete:
                State.CompleteExteriorRouteDomainSignals.add(Signal)
                State.IncompletePreSiblingDomainSignals.discard(Signal)
        if not State.CandidatesBySignal[Signal] and (not State.PreRouteLocalClaimChoicesBySignal.get(Signal)) and (not State.RouteTreeNativeDeadlineExceeded or Signal in State.CompleteExteriorRouteDomainSignals):
            Rejections = State.CandidateDiagnostics[Signal].get('Rejections', {})
            RoutedTreeCount = int(State.CandidateDiagnostics[Signal].get('RoutedTrees', 0))
            FixedLegalityRejectedEveryRoutedTree = bool(State.PlacementWasRelocated and RoutedTreeCount > 0 and (sum((int(Rejections.get(Reason, 0)) for Reason in ('SelfClaimConflict', 'NoRepeater'))) >= RoutedTreeCount))
            SeedRejectedEveryRoutedTree = bool(FixedLegalityRejectedEveryRoutedTree and State.LocalClaimsBySignal.get(Signal))
            CandidateFailureFingerprint = Services.BuildStableFingerprint({'Signal': Signal, 'Diagnostics': State.CandidateDiagnostics[Signal], 'PortalReservations': [Value.ToDictionary() for Value in State.PortalReservations], 'LayerCount': State.LayerCount, 'LaneCount': State.RouteLaneCount})
            CandidateRequestDomainFingerprint = State.CandidateRequestShapeDomainFingerprintBySignal.get(Signal) if State.Resources.PreparingPhysicalComponentGlobalChannels else Services.BuildStableFingerprint(('native-route-request-domain-v1', Signal, tuple(State.RouteRequestsBySignal.get(Signal, ())[:State.InitialRequestLimit]), tuple(State.RouteMetadataBySignal.get(Signal, ())[:State.InitialRequestLimit]), State.LayerCount, State.RouteLaneCount))
            if State.Resources.PreparingPhysicalComponentGlobalChannels:
                RemainingRequestCounts = Services.BuildPhysicalRouteDescriptorRemainingCounts(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDomainFingerprintsBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal)
                CapturedCorridorDomains = Services.CaptureCompletePhysicalPortCorridorDomains(State.PhysicalAssemblyPlan, State.PreSiblingCandidatesBySignal, State.CandidateRequestShapeDomainFingerprintBySignal, RemainingRequestCounts, State.Resources)
                CapturedExteriorContinuations = Services.RetainCompletePhysicalSignalRouteDomainContinuations(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal, State.PhysicalRequestDomainFingerprintsBySignal, RemainingRequestCounts, State.PreSiblingCandidatesBySignal, State.PreSiblingCandidateMetadataBySignal)
                CapturedPortableExteriorContinuations = Services.RetainCompletePortablePhysicalSignalRouteDomains(State.Resources.PhysicalGlobalApertureTemplateCache, State.PortableRouteDomainPreparationBySignal, RemainingRequestCounts, State.PreSiblingCandidatesBySignal, State.PreSiblingCandidateMetadataBySignal)
                GlobalCandidateDomainComplete = Services.IsPhysicalCandidateRequestDomainComplete(int(State.CandidateDiagnostics[Signal].get('DeferredRequests', 0)), State.Deadline.IsExpired())
                IndependentEmptyCandidateDomainSignals = (Signal,) if GlobalCandidateDomainComplete and (not tuple(State.PreSiblingCandidatesBySignal.get(Signal, ()))) else ()
                RequestApertureFactorNoGood = frozenset()
                RequestAperturePortNoGood = frozenset()
                AlternativeAperturePortNoGoods = ()
                SignalLocalRequestFactorProofComplete = False
                RequestIdentity = State.ApertureCandidateDomainIdentityBySignal.get(Signal)
                if GlobalCandidateDomainComplete and RequestIdentity is not None and (State.CertifiedApertureDomain is not None) and State.CertifiedApertureDomain.Complete:
                    RequestApertureFactorNoGood = Services.BuildMinimalPhysicalRequestApertureNoGood(Signal, RequestIdentity.StableDomainFingerprint, State.SiblingApertureConflictSetsBySignal.get(Signal, ()), {Factor.Signal: Factor.ApertureFingerprint for Factor in State.CertifiedApertureDomain.Factors})
                    if RequestApertureFactorNoGood:
                        DependencyComponents = State.CandidateRequestDependencyComponentsBySignal.get(Signal, {})
                        SignalLocalRequestFactorProofComplete = Services.PhysicalSignalLocalCandidateRequestFactorProofComplete(Signal, DependencyComponents, State.Resources.PhysicalComponentExactGlobalChannelSignals, State.PhysicalPortGuidesBySignal, State.CertifiedApertureDomain)
                        State.Resources.RejectedPhysicalGlobalRequestApertureFactorSets.add(RequestApertureFactorNoGood)
                        RequestAperturePortNoGood = Services.BuildPhysicalRequestAperturePortNoGood(State.PhysicalAssemblyPlan, RequestApertureFactorNoGood, SignalLocalRequestFactorProofComplete=SignalLocalRequestFactorProofComplete, PortSolverCacheKey=Services.BuildPhysicalComponentPortSolverCacheKey(str(getattr(State.Resources.PreparedPhysicalComponentPortFactorDomain, 'DomainFingerprint', ''))) if getattr(State.Resources, 'PreparedPhysicalComponentPortFactorDomain', None) is not None else '')
                        if RequestAperturePortNoGood:
                            State.Resources.RejectedPhysicalComponentPortReservationSets.add(RequestAperturePortNoGood)
                        PreparedPortDomain = getattr(State.Resources, 'PreparedPhysicalComponentPortFactorDomain', None)
                        MatchingVictimFactors = tuple((Factor for Factor in State.CertifiedApertureDomain.Factors if Factor.Signal == Signal))
                        if SignalLocalRequestFactorProofComplete and PreparedPortDomain is not None and (len(MatchingVictimFactors) == 1):
                            AlternativeAperturePortNoGoods = Services.BuildCompletePhysicalRequestAlternativeApertureNoGoods(Signal, MatchingVictimFactors[0].PortGlobalContractFingerprint, State.PreSiblingCandidatesBySignal.get(Signal, ()), dict(PreparedPortDomain.BoundaryPortReservationsBySignal))
                            State.Resources.RejectedPhysicalComponentPortReservationSets.update(AlternativeAperturePortNoGoods)
                State.CandidateDiagnostics[Signal]['SiblingApertureSeamOwnership'] = dict(State.SiblingApertureSeamOwnershipBySignal.get(Signal, {}))
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.TrackAssignmentConflict if GlobalCandidateDomainComplete else Services.RoutingFailureReason.DetailedSearchExhausted, Stage='PhysicalComponentGlobalCandidateDomain', AffectedNets=tuple(dict.fromkeys((Signal, *map(str, State.CandidateDiagnostics[Signal].get('SiblingApertureConflictSignals', ()))))), RepairActions=(), Detail='the fixed physical assembly produced no route in its current finite global candidate domain', Diagnostics={'PhysicalComponentGlobalPlanning': True, 'GlobalPlanDomainComplete': GlobalCandidateDomainComplete, 'CompleteAssignmentCutProof': GlobalCandidateDomainComplete, 'IndependentEmptyCandidateDomainSignals': list(IndependentEmptyCandidateDomainSignals), 'CandidateDomainFingerprint': CandidateRequestDomainFingerprint, 'CandidateDiagnostics': State.CandidateDiagnostics[Signal], 'RemainingRequestCounts': RemainingRequestCounts, 'PhysicalPortCorridorDomains': [Domain.ToDictionary() for Domain in CapturedCorridorDomains], 'PhysicalPortCorridorDomainCacheSize': len(State.Resources.PhysicalPortCorridorDomainCache), 'PhysicalExteriorRouteDomains': [{'Signal': Value.Signal, 'StableDomainFingerprint': Value.PreSiblingDomainFingerprint, 'CandidateCount': len(Value.Candidates), 'Complete': Value.Complete} for Value in CapturedExteriorContinuations], 'PortablePhysicalExteriorRouteDomainsPublished': [{'Signal': Value.Signal, 'PortableDomainFingerprint': Value.PortableDomainFingerprint, 'CandidateCount': len(Value.Candidates), 'Complete': Value.Complete} for Value in CapturedPortableExteriorContinuations], 'PortablePhysicalExteriorRouteDomainBucketCount': sum((str(Key).startswith('portable-route-domain-bucket:') for Key in State.Resources.PhysicalGlobalApertureTemplateCache)), 'RequestApertureFactorProofComplete': bool(RequestApertureFactorNoGood), 'SignalLocalRequestFactorProofComplete': SignalLocalRequestFactorProofComplete, 'RequestApertureFactorNoGood': [list(Key) for Key in sorted(RequestApertureFactorNoGood)], 'RequestAperturePortNoGood': [list(Key) for Key in sorted(RequestAperturePortNoGood)], 'AlternativeAperturePortNoGoods': [[list(Key) for Key in sorted(Clause)] for Clause in AlternativeAperturePortNoGoods], 'ExecutableLegacyRepairCascade': False}))
            if State.HasPhysicalComponentRoutingContract and RoutedTreeCount == 0 and (int(State.CandidateDiagnostics[Signal].get('Requests', 0)) > 0):
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.DetailedSearchExhausted, Stage='Candidate', AffectedNets=(Signal,), RepairActions=(), Detail='the immutable routed-component state blocked a complete bounded global candidate window', Diagnostics={'Action': 'advance-routed-component-global-starvation', 'CandidateDiagnostics': State.CandidateDiagnostics[Signal], 'CandidateFailureFingerprint': CandidateFailureFingerprint, 'CandidateDiversityLevel': State.CandidateDiversityLevel, 'RoutedComponentGlobalHandoff': {'Enabled': True, 'Disposition': 'advance-access-distinct-component-state'}}))
            if Services.ShouldRejectRoutedComponentForeignEscape(HasRoutedComponentTemplate=State.HasRoutedComponentTemplate, IsSelectedForeignEscape=Signal in State.RoutedComponentForeignEscapeSignals, CandidateDiversityLevel=State.CandidateDiversityLevel, CandidateCount=len(State.CandidatesBySignal[Signal])):
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.TrackAssignmentConflict, Stage='Candidate', AffectedNets=(Signal,), RepairActions=(), Detail='the selected routed-component foreign escape has no legal global continuation after the bounded handoff windows', Diagnostics={'Action': 'reject-routed-component-foreign-escape', 'CandidateDiagnostics': State.CandidateDiagnostics[Signal], 'CandidateFailureFingerprint': CandidateFailureFingerprint, 'CandidateDiversityLevel': State.CandidateDiversityLevel, 'SelectedForeignEscapeSignal': True, 'RoutedComponentGlobalHandoff': {'Enabled': True, 'Disposition': 'return-selected-escape-no-good-to-component-csp'}}))

            def RaiseTerminalCandidateIncomplete(Action: str) -> None:
                if State.PrepareRawTrackAssignmentDomainOnly:
                    State.RawTrackAssignmentExtractionIncompleteReasons[str(Signal)] = Action
                    return
                if State.PrepareTrackAssignmentOnly:
                    raise Services.TrackAssignmentPrepared(Services.TrackAssignmentPreparation(Success=False, SelectedCandidateIds=(), CandidateCounts=tuple(sorted(((str(CandidateSignal), len(CandidateValues)) for CandidateSignal, CandidateValues in State.CandidatesBySignal.items()))), ConflictSignals=(str(Signal),), ConflictResourceIndices=(), ExpansionCount=0, Complete=False, IncompleteReason=Action, Diagnostics=tuple(sorted(((str(Key), Value) for Key, Value in {**State.CandidateDiagnostics[Signal], 'PortalTupleFeasibility': State.PortalTupleFeasibilityBySignal.get(Signal, ())}.items())))))
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='Candidate', AffectedNets=(str(Signal),), RepairActions=(), Detail='the one fixed candidate domain produced no legal route; automatic relaunch is disabled', Diagnostics={'Action': 'terminal-fixed-domain-incomplete', 'RejectedAutomaticAction': Action, 'Complete': False, 'CandidateFailureFingerprint': CandidateFailureFingerprint, 'CandidateDiagnostics': State.CandidateDiagnostics[Signal], 'PortalTupleFeasibility': State.PortalTupleFeasibilityBySignal.get(Signal, ())}))
            RaiseTerminalCandidateIncomplete('fixed-domain-exhausted')
        if not State.RouteTreeNativeDeadlineExceeded:
            State.CheckRuntimeBudget('Candidate')
    if State.FrozenPreparedPortalCache is not None and (not State.PrepareClusterInterfaceAssignmentOnly) and (not State.ValidateClusterInterfaceForeignAccessOnly) and all((State.CandidatesBySignal.get(Signal) or State.PreRouteLocalClaimChoicesBySignal.get(Signal) for Signal in State.Profiles)):
        State.Resources.FrozenInterfaceGlobalCandidateCache = {Signal: tuple(Values) for Signal, Values in State.CandidatesBySignal.items()}
        State.Resources.FrozenInterfaceGlobalCandidateMetadata = {Signal: dict(Values) for Signal, Values in State.CandidateAxisLaneBySignal.items()}
        State.Resources.FrozenInterfaceGlobalCandidatePlacementIdentity = id(State.Placed)
        State.WorkTelemetry['FrozenInterfaceGlobalCandidateCache'] = {'StoredSignalCount': len(State.CandidatesBySignal), 'RegeneratedComponentSignals': sorted(State.InterClusterChannelSignals)}
    if State.PrepareClusterInterfaceAssignmentOnly:
        PreparedAssignment = State.Resources.PreparedClusterInterfaceAssignment
        if PreparedAssignment is None:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceUnsatisfiable, Stage='ClusterInterfaceUnsatisfiable', Detail='the qualifying placement produced no complete cluster-interface ownership assignment', Diagnostics={'InterfaceAssignment': None, 'ClusterBoundaryLeases': dict(State.WorkTelemetry.get('ClusterBoundaryLeases', {}))}))
        raise Services.ClusterInterfaceAssignmentPrepared(PreparedAssignment)
    NativeDeadlineIncompleteSignals = tuple(sorted((Signal for Signal in State.Profiles if not State.CandidatesBySignal.get(Signal) and (not State.PreRouteLocalClaimChoicesBySignal.get(Signal)) and (Signal not in State.CompleteExteriorRouteDomainSignals))))
    if State.RouteTreeNativeDeadlineExceeded and NativeDeadlineIncompleteSignals:
        Services.EnforceRoutingRuntimeLimit(Deadline=State.Deadline, AdaptiveStartedAt=State.RoutingStarted, AdaptiveExpiresAt=State.AdaptiveExpiresAt, Stage='Candidate', Diagnostics={**State.CurrentRuntimeBudgetDiagnostics(), 'PhysicalSignalRouteDomainDescriptorProgress': dict(State.WorkTelemetry.get('PhysicalSignalRouteDomainDescriptorProgress', {})), 'DescriptorProgressPublished': True, 'NativeDeadlineIncompleteSignals': list(NativeDeadlineIncompleteSignals), 'RawResultCacheAuthoritative': False, 'GlobalPlanDomainComplete': False, 'CompleteAssignmentCutProof': False}, NativeDeadlineExceeded=True)
    State.StageTimings['CandidateGeneration'] = Services.monotonic() - State.CandidateStarted
    State.AssignmentPreparationStarted = Services.monotonic()
    State.WorkTelemetry['RouteTreeRequestCount'] = State.CandidateRequestCount
    if State.ProgressCallback is not None:
        State.ProgressCallback(4, State.StageCount)
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: candidate generation complete signals={len(State.CandidatesBySignal)} batches={State.RouteTreeBatchCount} request_construction={State.WorkTelemetry['CandidateRequestConstructionSeconds']} initial_native={State.WorkTelemetry['InitialNativeCandidateBatchSeconds']} total={Services.monotonic() - State.CandidateStarted:.3f}', flush=True)
        for Signal in sorted(State.CandidatesBySignal):
            Diagnostics = State.CandidateDiagnostics[Signal]
            print(f'[debug] authoritative: signal diagnostics signal={Signal} requests={Diagnostics['Requests']} routed={Diagnostics['RoutedTrees']} materialized={Diagnostics['Materialized']} source_portals={Diagnostics['SourcePortals']} target_portals={Diagnostics['TargetPortals']} self_conflict={Diagnostics.get('Rejections', {}).get('SelfClaimConflict', 0)} disconnected={Diagnostics.get('Rejections', {}).get('Disconnected', 0)} no_repeater={Diagnostics.get('Rejections', {}).get('NoRepeater', 0)} limit={State.CandidateLimitsBySignal[Signal]} final={len(State.CandidatesBySignal[Signal])}', flush=True)
    return PhaseOutcome()

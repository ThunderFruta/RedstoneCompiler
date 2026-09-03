"""Materialization phase of authoritative routing."""
from __future__ import annotations
from ..RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState, PhaseOutcome

def RunMaterialization(State: AuthoritativeRoutingState, Services: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run the Materialization phase against shared routing state."""
    InitialAssignmentExpansionCount = State.Result.ExpansionCount
    (Selected): dict[str, Services.NetRouteCandidate] = {}
    (SelectedLocalClaimChoicesBySignal): dict[str, Services.PreRouteLocalClaimChoice] = {}
    for SignalValue, CandidateIdValue in State.Result.SelectedCandidateIds:
        Signal = str(SignalValue)
        CandidateId = str(CandidateIdValue)
        LocalChoice = State.PreRouteLocalClaimChoiceById.get(CandidateId)
        if LocalChoice is not None:
            if LocalChoice.Signal != Signal:
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='PreRouteLocalClaimChoice', AffectedNets=(Signal,), Detail='the native assignment selected a local claim under a different logical signal'))
            if Signal in SelectedLocalClaimChoicesBySignal:
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='PreRouteLocalClaimChoice', AffectedNets=(Signal,), Detail='the native assignment selected duplicate local claims'))
            SelectedLocalClaimChoicesBySignal[Signal] = LocalChoice
            continue
        Candidate = State.CandidateLookup.get(CandidateId)
        if Candidate is None or Candidate.Signal != Signal:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='PreRouteLocalClaimChoice', AffectedNets=(Signal,), Detail='the native assignment selected an unknown ordinary candidate'))
        Selected[Signal] = Candidate
    SelectedClaimChoiceSignals = frozenset(SelectedLocalClaimChoicesBySignal)
    if frozenset((*Selected, *SelectedClaimChoiceSignals)) != frozenset(State.Profiles):
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='PreRouteLocalClaimChoice', Detail='the selected ordinary/local candidate values do not cover the immutable routing profile domain', Diagnostics={'SelectedOrdinarySignals': sorted(Selected), 'SelectedLocalClaimSignals': sorted(SelectedClaimChoiceSignals), 'ProfileSignals': sorted(State.Profiles)}))
    AssignmentExpansionCount = InitialAssignmentExpansionCount
    RepairIterations = []
    (ReroutedSignals): set[str] = set()
    if State.NegotiatedPlan is not None:
        RepairIterations.extend(State.NegotiatedPlan.Iterations)
        ReroutedSignals.update(State.NegotiatedPlan.ReroutedSignals)

    def ReportMaterializationStage(Stage: str) -> None:
        if State.DiagnosticCallback is None:
            return
        Values = tuple(Selected.values())
        State.DiagnosticCallback(Services.RoutingIterationMetrics(Iteration=1, Stage=Stage, ConflictCount=0, ReroutedNets=len(ReroutedSignals), AverageLength=sum((Value.Length for Value in Values)) / len(Values) if Values else 0.0, BendCount=sum((Value.BendCount for Value in Values)), ViaCount=sum((Value.ViaCount for Value in Values))), None)
    if State.CoarsePlan is not None and State.NegotiatedPlan is None and (not SelectedLocalClaimChoicesBySignal):
        if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            print('[debug] authoritative: entering offline repair loop', flush=True)
        (CongestionHistory): Services.Counter[Services.Position2] = Services.Counter()

        def SelectionQuality(Values: dict[str, NetRouteCandidate]) -> tuple[int, int, int, int]:
            ColumnUsage = Services.Counter(((X, Z) for Candidate in Values.values() for X, _Y, Z in Candidate.Nodes))
            return (max((Count - 1 for Count in ColumnUsage.values()), default=0), sum((Candidate.Length for Candidate in Values.values())), sum((Candidate.BendCount for Candidate in Values.values())), sum((Candidate.ViaCount for Candidate in Values.values())))
        CurrentQuality = SelectionQuality(Selected)
        StagnationCount = 0
        for PassIndex in range(State.Policy.GlobalRouting.MaximumRipupPasses):
            State.CheckRuntimeBudget('CongestionRepair')
            if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                print(f'[debug] authoritative: congestion pass {PassIndex}', flush=True)
            ColumnUsage = Services.Counter(((X, Z) for Candidate in Selected.values() for X, _Y, Z in Candidate.Nodes))
            OverflowColumns = {Position: Count - 1 for Position, Count in ColumnUsage.items() if Count > 2}
            if not OverflowColumns:
                break
            Contributions = Services.Counter({Signal: sum((OverflowColumns.get((X, Z), 0) for X, _Y, Z in Candidate.Nodes)) for Signal, Candidate in Selected.items()})
            Offenders = tuple((Signal for Signal, Score in sorted(Contributions.items(), key=lambda Value: (-Value[1], -Selected[Value[0]].Length, -Selected[Value[0]].BendCount, -Selected[Value[0]].ViaCount, Value[0])) if Score > 0))[:4]
            if not Offenders:
                break
            CongestionHistory.update(OverflowColumns)
            RepairSets = {Signal: State.CandidatesBySignal[Signal] if Signal in Offenders else [Selected[Signal]] for Signal in Selected}
            RepairResult = State.PlanAssignment(State.EncodeCandidateValues(RepairSets, CongestionHistory))
            State.RaiseForNativeAssignmentDeadline(RepairResult)
            State.CheckRuntimeBudget('CongestionRepair')
            if not RepairResult.Success:
                break
            Repaired = {Signal: State.CandidateLookup[CandidateId] for Signal, CandidateId in RepairResult.SelectedCandidateIds}
            RepairedQuality = SelectionQuality(Repaired)
            RepairIterations.append(Services.RoutingIterationMetrics(Iteration=PassIndex + 2, Stage='Localized congestion repair', ConflictCount=0, ReroutedNets=sum((Repaired[Signal].CandidateId != Selected[Signal].CandidateId for Signal in Selected)), AverageLength=sum((Value.Length for Value in Repaired.values())) / len(Repaired), BendCount=sum((Value.BendCount for Value in Repaired.values())), ViaCount=sum((Value.ViaCount for Value in Repaired.values())), ConflictSignals=tuple(Offenders)))
            if RepairedQuality >= CurrentQuality:
                StagnationCount += 1
                if StagnationCount >= State.Policy.GlobalRouting.StagnationPassLimit:
                    break
                continue
            StagnationCount = 0
            ReroutedSignals.update((Signal for Signal in Selected if Repaired[Signal].CandidateId != Selected[Signal].CandidateId))
            Selected = Repaired
            CurrentQuality = RepairedQuality
            State.Result = RepairResult
        if Services.ShouldRunShapeOptimization(State.Policy.QualityTarget):
            ShapeOrder = tuple(sorted(Selected, key=lambda Signal: (-Selected[Signal].ViaCount, -Selected[Signal].BendCount, -Selected[Signal].Length, Signal)))
            ShapeBatchSize = max(1, min(6, len(ShapeOrder)))
            for ShapePass in range(State.Policy.GlobalRouting.MaximumRipupPasses):
                State.CheckRuntimeBudget('ShapeOptimization')
                if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                    print(f'[debug] authoritative: shape pass {ShapePass}', flush=True)
                Start = ShapePass * ShapeBatchSize
                ShapeOffenders = ShapeOrder[Start:Start + ShapeBatchSize]
                if not ShapeOffenders:
                    break
                RepairSets = {Signal: State.CandidatesBySignal[Signal] if Signal in ShapeOffenders else [Selected[Signal]] for Signal in Selected}
                ShapeResult = State.PlanAssignment(State.EncodeCandidateValues(RepairSets, OptimizeShape=True))
                State.RaiseForNativeAssignmentDeadline(ShapeResult)
                State.CheckRuntimeBudget('ShapeOptimization')
                if not ShapeResult.Success:
                    continue
                Shaped = {Signal: State.CandidateLookup[CandidateId] for Signal, CandidateId in ShapeResult.SelectedCandidateIds}
                ShapedQuality = SelectionQuality(Shaped)
                RepairIterations.append(Services.RoutingIterationMetrics(Iteration=len(RepairIterations) + 2, Stage='Localized shape repair', ConflictCount=0, ReroutedNets=sum((Shaped[Signal].CandidateId != Selected[Signal].CandidateId for Signal in Selected)), AverageLength=sum((Value.Length for Value in Shaped.values())) / len(Shaped), BendCount=sum((Value.BendCount for Value in Shaped.values())), ViaCount=sum((Value.ViaCount for Value in Shaped.values())), ConflictSignals=tuple(ShapeOffenders)))
                if ShapedQuality >= CurrentQuality:
                    continue
                ReroutedSignals.update((Signal for Signal in Selected if Shaped[Signal].CandidateId != Selected[Signal].CandidateId))
                Selected = Shaped
                CurrentQuality = ShapedQuality
                State.Result = ShapeResult
    State.CheckRuntimeBudget('Materialization')
    ReportMaterializationStage('Authoritative assignment')
    SelectedClaimsBySignal = {Signal: Value.Claims for Signal, Value in Selected.items()}
    for Signal, Choice in sorted(SelectedLocalClaimChoicesBySignal.items()):
        SelectedClaimsBySignal[Signal] = Choice.Claim.Claims
    if SelectedLocalClaimChoicesBySignal:
        State.WorkTelemetry['SelectedPreRouteLocalClaimChoices'] = {'DomainFingerprint': State.PreRouteLocalClaimDomainFingerprint, 'Selected': [Choice.ToDictionary() for _Signal, Choice in sorted(SelectedLocalClaimChoicesBySignal.items())]}
    for Signal, SignalClaims in sorted(State.LocalClaimsBySignal.items()):
        State.CheckRuntimeBudget('MaterializationClaims')
        if not SignalClaims:
            continue
        LocalClaimsBySignalResource = frozenset((Resource for Claim in SignalClaims for Resource in Claim.Claims.ResourceIds))
        if Signal in SelectedClaimsBySignal:
            SelectedClaimsBySignal[Signal] = Services.RoutingResourceClaims(WireCells=frozenset(SelectedClaimsBySignal[Signal].WireCells) | frozenset((Resource.Position for Resource in LocalClaimsBySignalResource if Resource.Kind == Services.RoutingResourceKind.Wire)), SupportCells=frozenset(SelectedClaimsBySignal[Signal].SupportCells) | frozenset((Resource.Position for Resource in LocalClaimsBySignalResource if Resource.Kind == Services.RoutingResourceKind.Support)), RequiredAirCells=frozenset(SelectedClaimsBySignal[Signal].RequiredAirCells) | frozenset((Resource.Position for Resource in LocalClaimsBySignalResource if Resource.Kind == Services.RoutingResourceKind.Air)), ElectricalCells=frozenset(SelectedClaimsBySignal[Signal].ElectricalCells) | frozenset((Resource.Position for Resource in LocalClaimsBySignalResource if Resource.Kind == Services.RoutingResourceKind.Electrical)))
        else:
            SelectedClaimsBySignal[Signal] = Services.RoutingResourceClaims(WireCells=frozenset((Resource.Position for Resource in LocalClaimsBySignalResource if Resource.Kind == Services.RoutingResourceKind.Wire)), SupportCells=frozenset((Resource.Position for Resource in LocalClaimsBySignalResource if Resource.Kind == Services.RoutingResourceKind.Support)), RequiredAirCells=frozenset((Resource.Position for Resource in LocalClaimsBySignalResource if Resource.Kind == Services.RoutingResourceKind.Air)), ElectricalCells=frozenset((Resource.Position for Resource in LocalClaimsBySignalResource if Resource.Kind == Services.RoutingResourceKind.Electrical)))
    ClaimsBySignal = SelectedClaimsBySignal
    State.CheckRuntimeBudget('AssignmentDrc')
    Conflicts = Services.FindClaimConflicts(ClaimsBySignal, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('AssignmentDrc', Diagnostics))
    if Conflicts:
        First = min(Conflicts, key=str)
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.FinalDrcViolation, Stage='AssignmentDrc', AffectedNets=Conflicts[First], Resources=(str(First),), Locations=(First.Position,), Detail='Rust assignment disagrees with authoritative Python claims'))
    ReportMaterializationStage('Assignment ownership validation')
    SignalOrder = tuple(sorted(Selected))
    PortalLookup = {Portal.PortalId: Portal for Values in State.Portals.values() for Portal in Values}
    MissingCandidatePortalBindings = tuple(sorted(((Signal, Candidate.CandidateId, Candidate.SourcePortalId, tuple(sorted(Candidate.TargetPortalIds.values()))) for Signal, Candidate in Selected.items() if Candidate.SourcePortalId not in PortalLookup or any((PortalId not in PortalLookup for PortalId in Candidate.TargetPortalIds.values())))))
    if MissingCandidatePortalBindings:
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=tuple(sorted({Signal for Signal, _CandidateId, _Source, _Targets in MissingCandidatePortalBindings})), Detail='selected candidate portal identity is absent from the authoritative portal domain', Diagnostics={'MissingCandidatePortalBindings': [{'Signal': Signal, 'CandidateId': CandidateId, 'SourcePortalId': SourcePortalId, 'TargetPortalIds': list(TargetPortalIds)} for Signal, CandidateId, SourcePortalId, TargetPortalIds in MissingCandidatePortalBindings], 'PortalCount': len(PortalLookup)}))
    MissingProfileSignals = tuple(sorted(set(Selected) - set(State.Profiles)))
    if MissingProfileSignals:
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=MissingProfileSignals, Detail='selected candidate signal missing routing profile', Diagnostics={'Profiles': sorted(State.Profiles)}))
    ResourceClaimsBySignal = {Signal: frozenset((Resource for Resource in Candidate.Claims.ResourceIds if Resource.Kind != Services.RoutingResourceKind.Electrical)) for Signal, Candidate in Selected.items()}
    ResourceClaimsBySignal.update({Signal: frozenset((Resource for Resource in Choice.Claim.Claims.ResourceIds if Resource.Kind != Services.RoutingResourceKind.Electrical)) for Signal, Choice in SelectedLocalClaimChoicesBySignal.items()})
    ResourceUsage = Services.Counter((Resource for Claims in ResourceClaimsBySignal.values() for Resource in Claims))
    Plan = Services.ChannelPlan(Profiles={Signal: Profile for Signal, Profile in State.Profiles.items() if Signal not in SelectedClaimChoiceSignals}, SignalOrder=SignalOrder, TrunkSignals=frozenset((Signal for Signal, Profile in State.Profiles.items() if Signal not in SelectedClaimChoiceSignals and Profile.IsTrunk)), Guides={Signal: Candidate.Guide for Signal, Candidate in Selected.items()}, CorridorUsage={}, CorridorCosts={}, CorridorCapacity=1, Layers={Signal: Candidate.Layer for Signal, Candidate in Selected.items()}, ResourceUsage=dict(ResourceUsage), ResourceOverflow={}, ResourceClaimsBySignal=ResourceClaimsBySignal, SourceAccessTransitions={Signal: tuple(dict.fromkeys((*State.Profiles[Signal].SourceAccessPath, *PortalLookup[Candidate.SourcePortalId].Path))) for Signal, Candidate in Selected.items()}, TargetAccessTransitions={Signal: {Target: tuple(dict.fromkeys((*State.Profiles[Signal].TargetAccessPaths[Target], *PortalLookup[PortalId].Path))) for Target, PortalId in Candidate.TargetPortalIds.items()} for Signal, Candidate in Selected.items()})
    Producers = {Signal: Gate for Gate in State.Placed.PlacedGates if Gate.OutputPin is not None for Signal in Gate.Outputs}
    if State.PhysicalAssemblyPlan is not None:
        Producers.update({Signal: Services.SimpleNamespace(OutputPin=Profile.Root) for Signal, Profile in State.Profiles.items()})
    Targets = {Signal: list(Profile.Targets) for Signal, Profile in State.Profiles.items()}
    for Signal in set(State.LocalClaimsBySignal):
        if Signal not in Targets:
            SignalSignalTargets = State.SignalTargets.get(Signal)
            if SignalSignalTargets:
                Targets[Signal] = list(SignalSignalTargets)
    SelectedRouteOrLocalSignals = frozenset((*Selected, *SelectedLocalClaimChoicesBySignal))
    MissingTargetSignals = tuple(sorted(SelectedRouteOrLocalSignals - set(Targets)))
    if MissingTargetSignals:
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=MissingTargetSignals, Detail='selected signal missing routing targets', Diagnostics={'Targets': sorted(Targets)}))
    State.CheckRuntimeBudget('RouteMaterialization')
    NetWires = {Signal: set(Candidate.Nodes) for Signal, Candidate in Selected.items()}
    NetWires.update({Signal: set(Choice.Claim.Nodes) for Signal, Choice in SelectedLocalClaimChoicesBySignal.items()})
    LocalSignalWireClaims = {Signal: tuple((Claim.Nodes for Claim in SignalClaims)) for Signal, SignalClaims in State.LocalClaimsBySignal.items() if Signal in State.SignalTargets and (not State.Resources.PreparingPhysicalComponentGlobalChannels or Signal in Selected)}
    for Signal, ClaimNodes in LocalSignalWireClaims.items():
        NetWires.setdefault(Signal, set()).update(*ClaimNodes)
    (FinalColumnContributors): dict[Services.Position2, list[str]] = Services.defaultdict(list)
    for Signal, Positions in NetWires.items():
        for Column in {(X, Z) for X, _Y, Z in Positions}:
            FinalColumnContributors[Column].append(Signal)
    FinalColumnOverflowHotspots = [{'Column': list(Column), 'Count': len(Signals), 'Signals': sorted(Signals)} for Column, Signals in sorted(FinalColumnContributors.items(), key=lambda Value: (-len(Value[1]), Value[0])) if len(Signals) > 2][:8]
    (Supports): set[Services.Position3] = set()
    SupportPositionCount = 0
    for Signal, Positions in NetWires.items():
        for X, Y, Z in Positions:
            SupportPositionCount += 1
            if SupportPositionCount % 256 == 0:
                State.CheckRuntimeBudget('PhysicalGraphMaterialization', {'Phase': 'supports', 'Signal': Signal, 'ProcessedPositions': SupportPositionCount})
            Supports.add((X, Y - 1, Z))
    Supports.difference_update(State.Resources.StaticGeometry.ActualBlocks)
    State.CheckRuntimeBudget('PhysicalGraphMaterialization')
    PhysicalGraphs = Services.BuildPhysicalGraphs(NetWires, State.Resources.StaticGeometry.ActualBlocks, Supports, State.Resources.StaticGeometry.SolidBlocks, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('PhysicalGraphMaterialization', Diagnostics))
    MissingSourceSignals = tuple(sorted(SelectedRouteOrLocalSignals - set(Producers)))
    if MissingSourceSignals:
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=MissingSourceSignals, Detail='selected signal has no routable source gate output pin', Diagnostics={'ProducerCount': len(Producers), 'SelectedCount': len(Selected)}))
    MissingNoOutputSignals = tuple(sorted((Signal for Signal, Producer in Producers.items() if Signal in SelectedRouteOrLocalSignals and Producer.OutputPin is None)))
    if MissingNoOutputSignals:
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=MissingNoOutputSignals, Detail='selected source gate has no output pin'))
    State.CheckRuntimeBudget('PhysicalConnectivityValidation')
    ValidationProducers = {Signal: Producers[Signal] for Signal in SelectedRouteOrLocalSignals} if State.Resources.PreparingPhysicalComponentGlobalChannels else Producers
    ValidationTargets = {Signal: Targets[Signal] for Signal in SelectedRouteOrLocalSignals} if State.Resources.PreparingPhysicalComponentGlobalChannels else Targets
    Services.ValidatePhysicalRoutes(PhysicalGraphs, ValidationProducers, ValidationTargets, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('PhysicalConnectivityValidation', Diagnostics))
    State.CheckRuntimeBudget('PhysicalConnectivityValidation')
    ReportMaterializationStage('Physical connectivity validation')
    Tracks = {}
    (Owners): dict[Services.RoutingResourceId, list[str]] = Services.defaultdict(list)
    for Signal, Candidate in Selected.items():
        State.CheckRuntimeBudget('RepeaterPlanning')
        if Candidate.SourcePortalId not in PortalLookup:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=(Signal,), Detail='candidate source portal id missing from portal lookup', Diagnostics={'SourcePortalId': Candidate.SourcePortalId, 'CandidateId': Candidate.CandidateId, 'PortalCount': len(PortalLookup)}))
        Graph = PhysicalGraphs[Signal]
        FallbackReservations, Paths = Services._ReserveRepeaters(Signal, Producers[Signal].OutputPin, tuple(Targets[Signal]), Graph, State.Technology)
        Reservations = Candidate.RepeaterReservations if Candidate.RepeaterReservations else FallbackReservations
        for Resource in ResourceClaimsBySignal[Signal]:
            Owners[Resource].append(Signal)
        SourcePortal = PortalLookup[Candidate.SourcePortalId]
        for Target in Targets[Signal]:
            if Target not in Candidate.TargetPortalIds:
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=(Signal,), Detail='candidate target missing portal mapping', Diagnostics={'Signal': Signal, 'Target': list(Target), 'CandidateId': Candidate.CandidateId}))
            if Candidate.TargetPortalIds[Target] not in PortalLookup:
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=(Signal,), Detail='candidate target portal id missing from portal lookup', Diagnostics={'Signal': Signal, 'Target': list(Target), 'PortalId': Candidate.TargetPortalIds[Target]}))
            if Target not in State.Profiles[Signal].TargetAccessPaths:
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.NoConnectedGlobalRoute, Stage='Materialization', AffectedNets=(Signal,), Detail='target missing profile access path', Diagnostics={'Signal': Signal, 'Target': list(Target)}))
        TargetPortals = {Target: PortalLookup[Candidate.TargetPortalIds[Target]] for Target in State.Profiles[Signal].Targets}
        Tracks[Signal] = Services.AssignedTrack(Signal=Signal, TrackId=Candidate.CandidateId, Layer=Candidate.Layer, Guide=Candidate.Guide, RepeaterSites=frozenset(((Position[0], Position[2]) for Position in Candidate.RepeaterWaypoints)), RepeaterWaypointsByTarget={Target: () for Target in Targets[Signal]}, ReservedResources=ResourceClaimsBySignal[Signal], RepeaterReservations=Reservations, AssignedPathsByTarget=Paths, SourcePinAccessPath=tuple(dict.fromkeys((*State.Profiles[Signal].SourceAccessPath, *SourcePortal.Path))), TargetPinAccessPathsByTarget={Target: tuple(dict.fromkeys((*reversed(TargetPortals[Target].Path), *reversed(State.Profiles[Signal].TargetAccessPaths[Target])))) for Target in Targets[Signal]}, SelectedPortalIds=(Candidate.SourcePortalId, *(Candidate.TargetPortalIds[Target] for Target in Targets[Signal])), OwnedNodes=Candidate.Nodes, OwnedEdges=Candidate.Edges)
    for Signal, Choice in sorted(SelectedLocalClaimChoicesBySignal.items()):
        State.CheckRuntimeBudget('LocalClaimMaterialization')
        Claim = Choice.Claim
        if Claim.RepeaterReservations:
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='PreRouteLocalClaimChoice', AffectedNets=(Signal,), Detail='selected local tree carries repeater reservations outside the frozen capacity encoding'))
        for Resource in ResourceClaimsBySignal[Signal]:
            Owners[Resource].append(Signal)
        Tracks[Signal] = Services.AssignedTrack(Signal=Signal, TrackId=Choice.ChoiceId, Layer=0, Guide=frozenset(((Position[0], Position[2]) for Position in Claim.Nodes)), RepeaterSites=frozenset(), RepeaterWaypointsByTarget={Target: () for Target in Targets[Signal]}, ReservedResources=ResourceClaimsBySignal[Signal], RepeaterReservations=(), AssignedPathsByTarget={Target: () for Target in Targets[Signal]}, SourcePinAccessPath=(), TargetPinAccessPathsByTarget={Target: () for Target in Targets[Signal]}, SelectedPortalIds=(), OwnedNodes=Claim.Nodes, OwnedEdges=Claim.Edges)
    ReportMaterializationStage('Repeater reservation planning')
    TrackAssignmentValue = Services.TrackAssignment(Tracks=Tracks, ResourceOwners={Resource: tuple(Values) for Resource, Values in Owners.items()})
    State.CheckRuntimeBudget('RepeaterMaterialization')
    Repeaters = Services.MaterializeReservedRepeaters(NetWires, Producers, Targets, PhysicalGraphs, Tracks, State.Technology, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('RepeaterMaterialization', Diagnostics))
    State.CheckRuntimeBudget('RepeaterMaterialization')
    ReportMaterializationStage('Repeater signal-strength validation')
    Assignment = Services.RoutingAssignment(SelectedCandidates=Selected, ResourceOwners=TrackAssignmentValue.ResourceOwners, ExpansionCount=AssignmentExpansionCount, PortalCount=sum((len(Values) for Values in State.Portals.values())), CandidateCount=len(State.CandidateLookup) + len(State.PreRouteLocalClaimChoices))
    OwnershipCounts = Services.Counter((Resource.Kind.value for Claims in ClaimsBySignal.values() for Resource in Claims.ResourceIds))
    State.CheckRuntimeBudget('MaterializationComplete')
    State.StageTimings['Total'] = Services.monotonic() - State.RoutingStarted
    return PhaseOutcome(Returned=True, Value=Services.RoutedDesign(Module=State.Placed.Module, PlacedGates=State.Placed.PlacedGates, Wires=sorted(set().union(*NetWires.values())), Supports=sorted(Supports), RepeaterInputFacings=Repeaters, NetWires={Signal: sorted(Positions) for Signal, Positions in NetWires.items()}, RoutingMetrics=Services.MeasureRoutingStage('Authoritative Rust', NetWires, Plan, ReroutedNets=len(ReroutedSignals), Iterations=tuple(RepairIterations)), GlobalPlan=Plan, TrackAssignment=TrackAssignmentValue, TechnologyVersion=State.Technology.TechnologyVersion, EffectivePolicy=State.Policy.ToDictionary(), ResourceGraphVersion=State.Resources.ResourceGraph.GraphVersion, ResourceGraphNodeCount=State.Resources.ResourceGraph.CachedNodeCount, ResourceGraphEdgeCount=State.Resources.ResourceGraph.CachedEdgeCount, ResourceOwnershipCounts=dict(OwnershipCounts), RepeaterReservationCount=sum((len(Track.RepeaterReservations) for Track in Tracks.values())), ZeroResourceConflicts=True, RoutingAssignment=Assignment, PortalCount=Assignment.PortalCount, RouteCandidateCount=Assignment.CandidateCount, CandidateRequestCount=State.CandidateRequestCount, CandidateExpansionLimit=max(State.CandidateExpansionLimits.values()), AssignmentExpansionCount=Assignment.ExpansionCount, RoutingStageTimings={Stage: round(Seconds, 6) for Stage, Seconds in State.StageTimings.items()}, GlobalGuideDiagnostics=State.CoarsePlan.ToDictionary() if State.CoarsePlan is not None else {}, NegotiatedRoutingDiagnostics=dict(State.WorkTelemetry.get('NegotiatedRouting', {})) if State.NegotiatedPlan is not None else {}, RoutingControlEffectiveness={'GuideFirstEnabled': State.CoarsePlan is not None, 'StrictLocalGuideCount': len(State.CoarsePlan.LocalSignals) if State.CoarsePlan is not None else 0, 'GuidePlanningPasses': len(State.CoarsePlan.Iterations) if State.CoarsePlan is not None else 0, 'GuideOverflowPeak': State.CoarsePlan.OverflowPeak if State.CoarsePlan is not None else 0, 'CandidateBendWeight': State.Policy.DetailedRouting.CandidateBendWeight, 'CandidateViaWeight': State.Policy.DetailedRouting.CandidateViaWeight, 'LayerPenalty': State.Policy.DetailedRouting.LayerPenalty, 'RoutingDemandEstimate': State.Demand.ToDictionary(), 'DerivedRoutingBudget': State.AdaptiveBudget.ToDictionary(), 'FixedRoutingControls': {'LayerCount': State.LayerCount, 'MaximumPortalsPerTerminal': State.PortalLimit, 'LaneCount': State.RouteLaneCount, 'MaximumCandidatesPerNet': State.MaximumCandidates, 'CandidateLimitsBySignal': dict(sorted(State.CandidateLimitsBySignal.items())), 'CandidateLayersBySignal': {Signal: sorted({Candidate.Layer for Candidate in Values}) for Signal, Values in sorted(State.CandidatesBySignal.items())}}, 'Deadline': State.Deadline.ToDictionary(), 'RustAssignmentUsed': True, 'NativeBatching': {'PortalRequestCount': State.WorkTelemetry['PortalRequestCount'], 'PortalTargetCount': State.WorkTelemetry['PortalTargetCount'], 'RouteTreeRequestCount': State.CandidateRequestCount, 'PortalBatchCount': State.WorkTelemetry['PortalBatchCount'], 'PortalCacheHit': State.WorkTelemetry['PortalCacheHit'], 'PortalPartialCacheHit': State.WorkTelemetry['PortalPartialCacheHit'], 'PortalCacheMode': State.WorkTelemetry['PortalCacheMode'], 'PortalCacheReusedSignals': State.WorkTelemetry['PortalCacheReusedSignals'], 'PortalCacheGeneratedSignals': State.WorkTelemetry['PortalCacheGeneratedSignals'], 'PortalReusedRequestCount': State.WorkTelemetry['PortalReusedRequestCount'], 'PortalGeneratedRequestCount': State.WorkTelemetry['PortalGeneratedRequestCount'], 'RouteTreeBatchCount': State.RouteTreeBatchCount, 'InitialCandidateRequestsPerSignal': State.InitialRequestLimit, 'CandidateDiagnostics': {Signal: {Key: Value for Key, Value in Values.items() if Key != 'Rejections'} for Signal, Values in sorted(State.CandidateDiagnostics.items())}, 'DeterministicRequestOrdering': True}, 'PortalReservations': [Value.ToDictionary() for Value in State.PortalReservations], 'RustAssignmentExpansionLimit': State.AssignmentExpansionLimit, 'RustAssignmentExpansions': InitialAssignmentExpansionCount, 'PrePlacementTrackAssignmentHandoff': dict(State.WorkTelemetry.get('PrePlacementTrackAssignmentHandoff', {'Applied': False})), 'LayerCappedAssignmentAttempts': State.LayerCappedAssignmentAttempts, 'LocalizedRepairPasses': len(RepairIterations), 'LocalizedReroutedNetCount': len(ReroutedSignals), 'LocalizedRepairOffenders': [list(Iteration.ConflictSignals) for Iteration in RepairIterations], 'FinalColumnOverflowHotspots': FinalColumnOverflowHotspots, 'CandidateRejectionReasons': {Signal: Values.get('Rejections', {}) for Signal, Values in sorted(State.CandidateDiagnostics.items())}, 'LocalGlobalTargetCounts': {Signal: {'LocalTargets': len(Profile.Seed.ConnectedTargets) if Profile.Seed is not None else 0, 'GlobalTargets': len(Profile.Targets)} for Signal, Profile in sorted(State.Profiles.items())}, 'IncrementalExtensions': {Signal: {'FullTreeLength': Candidate.Length, 'IncrementalLength': Candidate.IncrementalLength, 'IncrementalMaterial': Candidate.IncrementalMaterialCost, 'ReusedLocalNodeCount': Candidate.SeedNodeCount, 'AvoidedDuplicateTrunkNodes': Candidate.SeedNodeCount} for Signal, Candidate in sorted(Selected.items())}, 'SameSignalReuseNodeCount': sum((Candidate.SeedNodeCount for Candidate in Selected.values())), 'LayerDeviations': {Signal: {'SelectedLayer': Candidate.Layer, 'PreferredLayer': State.Policy.Organization.PreferredXLayer if ':X:' in Candidate.CandidateId else State.Policy.Organization.PreferredZLayer} for Signal, Candidate in sorted(Selected.items()) if State.Policy.Organization.Enabled and Candidate.Layer != (State.Policy.Organization.PreferredXLayer if ':X:' in Candidate.CandidateId else State.Policy.Organization.PreferredZLayer)}}))
    return PhaseOutcome()

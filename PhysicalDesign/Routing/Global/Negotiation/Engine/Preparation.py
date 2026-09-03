"""Cohesive preparation and finalization phases for one domain."""

from __future__ import annotations

from ...Flow.RunState import AuthoritativeRoutingServices, PhaseOutcome
from .State import NegotiatedRoutingState

def RunInitialization(RunState: NegotiatedRoutingState, RunServices: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run negotiated-routing initialization."""
    RunState.Negotiated = RunState.Policy.NegotiatedRouting
    RunState.TileSize = 4 * RunState.Technology.TrackPitch
    RunState.SignalOrder = tuple(sorted(RunState.Profiles, key=lambda Signal: (-RunState.Profiles[Signal].Fanout, -RunState.Profiles[Signal].Criticality, -RunState.Profiles[Signal].Span, Signal)))
    RunState.TerminalCount = sum((1 + len(Profile.Targets) for Profile in RunState.Profiles.values()))
    RunState.HasValidatedLocalClaims = bool((RunState.LocalClaimReleaseDiagnostics or {}).get('OriginalLocalClaimCount', 0))
    HasSubstantialLocalClaims = int((RunState.LocalClaimReleaseDiagnostics or {}).get('OriginalLocalClaimCount', 0)) >= 8
    RunState.InitialDetailedRequestWindow = (min(3, RunState.Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal) if RunState.TerminalCount > 256 else max(32, RunState.Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal) if HasSubstantialLocalClaims else RunState.Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal) if RunState.TerminalCount >= 200 else max(16, RunState.Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal) if RunState.HasValidatedLocalClaims else RunState.Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal
    RunState.ContextNodes = set(RunState.Region.Nodes)
    RunState.ContextEdges = set(RunState.Region.Edges)
    RunState.RegionStates: dict[str, RunServices.NegotiatedRegionState] = {}
    for Signal in RunState.SignalOrder:
        SignalMetadata = RunState.RouteMetadataBySignal.get(Signal, ())
        GuideColumns = set(SignalMetadata[0][2]) if SignalMetadata else set()
        if not GuideColumns:
            SignalRequests = RunState.RouteRequestsBySignal.get(Signal, ())
            GuideColumns = {tuple(Column) for Column in SignalRequests[0][2]} if SignalRequests else set()
        if not GuideColumns:
            GuideColumns = set(RunServices.BuildNegotiatedFallbackGuideColumns(RunState.Profiles[Signal], RunState.Region.Bounds, list(RunState.RouteRequestsBySignal.get(Signal, ()))))
        InitialTiles = RunServices.BuildNegotiatedInitialTiles(GuideColumns, RunState.Region.Bounds, RunState.TileSize)
        InitialColumns = RunServices.BuildNegotiatedInitialColumns(GuideColumns, RunState.Region.Bounds, RunState.TileSize)
        RunState.RegionStates[Signal] = RunServices.NegotiatedRegionState(Signal=Signal, TileSize=RunState.TileSize, Bounds=RunState.Region.Bounds, ActiveTiles=set(InitialTiles), ActiveColumns=set(InitialColumns), AddedNodes=set(), AddedEdges=set(), BoundaryTouches=set(), ExpandedSides=[], ExpansionEvents=[])
    MaterializeInitialHalo = RunState.TerminalCount <= 64
    if MaterializeInitialHalo:
        InitialColumns = frozenset({Column for State in RunState.RegionStates.values() for Column in State.ActiveColumns})
        InitialRegion = RunState.Resources.ResourceGraph.BuildRegion(RunState.Region.Bounds, AllowedColumns=InitialColumns, AllowedAccess=RunState.ReservedAccess, WorkCheck=lambda Diagnostics: RunState.CheckRuntimeBudget('ResourceGraphExpansion', {'Cause': 'initial-one-tile-halo', **Diagnostics}))
        InitialDeltaNodes = set(InitialRegion.Nodes) - RunState.ContextNodes
        InitialDeltaEdges = set(InitialRegion.Edges) - RunState.ContextEdges
        if InitialDeltaNodes or InitialDeltaEdges:
            RunState.Context.AddRegion(sorted(InitialDeltaNodes), sorted(InitialDeltaEdges))
            RunState.ContextNodes.update(InitialDeltaNodes)
            RunState.ContextEdges.update(InitialDeltaEdges)
        RunState.Region = InitialRegion
    else:
        InitialDeltaNodes = set()
        InitialDeltaEdges = set()
    RunState.NodesByColumn: dict[RunServices.Position2, list[RunServices.Position3]] = RunServices.defaultdict(list)
    for Position in RunState.Region.Nodes:
        RunState.NodesByColumn[Position[0], Position[2]].append(Position)
    for Values in RunState.NodesByColumn.values():
        Values.sort()

    def InitialOwnedNodeCount(State: NegotiatedRegionState) -> int:
        return sum((len(RunState.NodesByColumn.get(Column, ())) for Column in State.ActiveColumns))
    InitialOwnedNodeCount = InitialOwnedNodeCount
    for State in RunState.RegionStates.values():
        if MaterializeInitialHalo:
            OwnedColumns = State.ActiveColumns
            State.AddedNodes.update((Position for Position in RunState.Region.Nodes if (Position[0], Position[2]) in OwnedColumns))
            State.AddedEdges.update((Edge for Edge in RunState.Region.Edges if Edge[0] in State.AddedNodes and Edge[1] in State.AddedNodes))
        State.ExpansionEvents.append({'Cause': 'initial-one-tile-halo', 'HaloSize': RunState.TileSize, 'ActiveTileCount': len(State.ActiveTiles), 'OwnedNodeCount': len(State.AddedNodes) if MaterializeInitialHalo else InitialOwnedNodeCount(State), 'OwnedEdgeCount': len(State.AddedEdges), 'AddedNodeCount': len(InitialDeltaNodes), 'AddedEdgeCount': len(InitialDeltaEdges)})
    RunState.Selected: dict[str, RunServices.NetRouteCandidate] = {Signal: Values[0] for Signal, Values in (RunState.SeedCandidatesBySignal or {}).items() if Values}
    RunState.IsPartialSeedCompletion = len(RunState.Selected) < len(RunState.Profiles) and len(RunState.Selected) * 10 >= len(RunState.Profiles) * 9
    RunState.RepairStates: dict[str, RunServices.NegotiatedRouteTreeState] = {}
    RunState.BranchRepairEvents: list[dict[str, object]] = []
    RunState.CumulativeConflictSignals: set[str] = set()
    RunState.History: RunServices.Counter[RunServices.Position3] = RunServices.Counter()
    RunState.ReroutedSignals: set[str] = set()
    RunState.Iterations: list[RunServices.RoutingIterationMetrics] = []
    RunState.OverflowProgression: list[int] = []
    RunState.BestOverflowConflictCount: int | None = None
    RunState.StagnationCount = 0
    RunState.CurrentPassIndex = 0
    RunState.MandatoryClaimsCache: dict[tuple[str, int], RunServices.RoutingResourceClaims] = {}
    RunState.MandatoryClaimsByPortalSignature: dict[tuple[str, int, tuple[int, ...]], RunServices.RoutingResourceClaims] = {}
    RunState.RejectionCountsBySignal: dict[str, RunServices.Counter[str]] = RunServices.defaultdict(RunServices.Counter)
    RunState.RepairBranchOutcomes: dict[str, dict[str, str]] = {}
    RunState.MandatorySelfConflictsBySignal: dict[str, set[RunServices.RoutingResourceId]] = RunServices.defaultdict(set)
    RunState.SearchExpansionEscalations: dict[str, int] = {}
    RunState.NativeSearchDiagnosticsBySignal: dict[str, list[dict[str, object]]] = RunServices.defaultdict(list)
    RunState.RouteRequestDiagnostics: dict[str, dict[str, object]] = {}
    RunState.InitialCandidateOptions: dict[str, dict[str, RunServices.NetRouteCandidate]] = RunServices.defaultdict(dict, {Signal: {Candidate.CandidateId: Candidate for Candidate in Values} for Signal, Values in (RunState.InitialCandidatesBySignal or {}).items() if Values})
    for Signal, Values in (RunState.SeedCandidatesBySignal or {}).items():
        for Candidate in Values:
            RunState.InitialCandidateOptions[Signal][Candidate.CandidateId] = Candidate
    RunState.CachedRepairSelections: list[dict[str, object]] = []
    RunState.InitialAssignmentDiagnostics: dict[str, object] = {}
    RunState.ExactAssignmentCutSignals: set[str] = set()
    RunState.ExactAssignmentCandidateRetries: list[dict[str, object]] = []
    RunState.NegotiatedAssignmentAttempts: list[dict[str, object]] = []
    RunState.CandidatePairConflictCountCache: dict[tuple[str, str], int] = {}
    RunState.CandidateClaimMasks: dict[str, tuple[int, int, int, int]] = {}
    RunState.ClaimPositionIndices: dict[RunServices.Position3, int] = {}
    RunState.CandidatePresentPositions: dict[str, RunServices.Counter[RunServices.Position3]] = {}
    RunState.InitialDetailedBatchResults: dict[tuple[str, int], RunServices.Any] = {}
    RunState.InitialDetailedBatchPreflightConflicts: dict[tuple[str, int], frozenset[RunServices.RoutingResourceId]] = {}
    RunState.InitialDetailedBatchRequestIndices: dict[str, tuple[int, ...]] = {}
    RunState.InitialDetailedBatchDiagnostics: dict[str, object] = {'Enabled': False, 'ScheduledRequestCount': 0, 'RequestCount': 0, 'BatchCount': 0, 'CompletedWork': 0, 'DeadlineExceeded': False, 'WorkerCount': 1, 'PreflightRejectedRequestCount': 0, 'GlobalPortfolio': False, 'MaterializationCacheHits': 0, 'MaterializationCacheMisses': 0}
    RunState.MaterializedCandidateCache: dict[tuple[str, int, frozenset[RunServices.Position3], tuple[tuple[RunServices.Position3, str], ...]], tuple[RunServices.NetRouteCandidate | None, dict[str, object]]] = {}
    FixedTerminalPositions = tuple((Position for Profile in RunState.Profiles.values() for Position in (Profile.Root, *Profile.Targets)))

    def EnvelopeQuality(Values: Iterable[NetRouteCandidate]) -> tuple[int, int, int, int, int, int, int, int, int]:
        """Score cached legal trees without invoking another path search."""
        Candidates = tuple(Values)
        Envelope = RunServices.BuildRoutingEnvelope((*FixedTerminalPositions, *(Position for Candidate in Candidates for Position in Candidate.Nodes)), (Position for Candidate in Candidates for Position in Candidate.Claims.SupportCells), (Reservation.Position for Candidate in Candidates for Reservation in Candidate.RepeaterReservations))
        return (Envelope.Width * Envelope.Height * Envelope.Depth, Envelope.Height, Envelope.Width * Envelope.Depth, Envelope.Width, Envelope.Depth, Envelope.RouteBlockCount + Envelope.SupportBlockCount, sum((Candidate.Length for Candidate in Candidates)), sum((Candidate.BendCount for Candidate in Candidates)), sum((Candidate.ViaCount for Candidate in Candidates)), sum((len(Candidate.RepeaterReservations) for Candidate in Candidates)))
    RunState.EnvelopeQuality = EnvelopeQuality

    def CandidateEnvelopeQuality(Candidate: NetRouteCandidate) -> tuple[int, int, int, int, int, int]:
        Envelope = Candidate.Envelope
        if Envelope is None:
            return (0, 0, 0, 0, 0, Candidate.Length)
        return (Envelope.Width * Envelope.Height * Envelope.Depth, Envelope.Height, Envelope.Width * Envelope.Depth, Envelope.Width, Envelope.Depth, Envelope.RouteBlockCount + Envelope.SupportBlockCount)
    RunState.CandidateEnvelopeQuality = CandidateEnvelopeQuality
    return PhaseOutcome()

def RunPreparation(RunState: NegotiatedRoutingState, RunServices: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run negotiated-routing preparation."""

    def TryInitialCandidateAssignment(OptimizeEnvelope: bool=False) -> dict[str, NetRouteCandidate] | None:
        """Select one legal member of the already bounded initial tree pool.

        Pass zero materializes several portal/layer choices per signal.  A
        greedy provisional forest may conflict even though that bounded pool
        contains a capacity-one assignment.  Solve that exact small choice
        before invoking negotiated rip-up; this preserves the negotiated
        route-tree algorithm while avoiding a false placement cut.
        """

        def InitialCandidateOrder(Candidate: NetRouteCandidate) -> tuple[Any, ...]:
            if not OptimizeEnvelope:
                return (*((Candidate.Layer,) if RunState.Policy.TrackAssignment.MinimizeMaximumRoutingLayer else ()), Candidate.MaterialCost, Candidate.CandidateId)
            return (*((Candidate.Layer,) if RunState.Policy.TrackAssignment.MinimizeMaximumRoutingLayer else ()), *RunState.CandidateEnvelopeQuality(Candidate), Candidate.Length, Candidate.BendCount, Candidate.ViaCount, len(Candidate.RepeaterReservations), Candidate.MaterialCost, Candidate.CandidateId)
        CandidateSets = {Signal: tuple(sorted(Values.values(), key=InitialCandidateOrder)) for Signal, Values in RunState.InitialCandidateOptions.items() if Values}
        if set(CandidateSets) != set(RunState.SignalOrder):
            RunState.InitialAssignmentDiagnostics.update({'Result': 'incomplete-candidate-domain', 'MissingSignals': sorted(set(RunState.SignalOrder) - set(CandidateSets)), 'CandidateCounts': {Signal: len(Values) for Signal, Values in sorted(CandidateSets.items())}})
            return None
        ExpansionLimit = max(1, min(RunState.Policy.TrackAssignment.MaximumAssignmentExpansions, RunState.Policy.AdaptiveRouting.MaximumAssignmentExpansions, RunState.Policy.AdaptiveRouting.BaseAssignmentExpansions + RunState.Policy.AdaptiveRouting.AssignmentExpansionsPerNet * len(RunState.Profiles) + RunState.Policy.AdaptiveRouting.AssignmentExpansionsPerTerminal * RunState.TerminalCount))
        if hasattr(RunState.Context, 'PlanAuthoritativeRoutesBounded'):
            AssignmentResourcePositions = tuple(sorted({Position for Values in CandidateSets.values() for Candidate in Values for Positions in (Candidate.Claims.WireCells, Candidate.Claims.SupportCells, Candidate.Claims.RequiredAirCells, Candidate.Claims.ElectricalCells) for Position in Positions}))
            if AssignmentResourcePositions:
                AssignmentIndexed = RunServices.IndexedRoutingResourceGraph(ResourcePositions=AssignmentResourcePositions, PositionIndices={Position: Index for Index, Position in enumerate(AssignmentResourcePositions)})
                CandidateLookup = {Candidate.CandidateId: Candidate for Values in CandidateSets.values() for Candidate in Values}
                CandidateValues = []
                for Signal, Values in sorted(CandidateSets.items()):
                    for Candidate in Values:
                        Wire, Support, Air, Electrical = AssignmentIndexed.EncodeClaims(Candidate.Claims)
                        if OptimizeEnvelope:
                            EnvelopeOrder = RunState.CandidateEnvelopeQuality(Candidate)
                            OrderValues = (Candidate.Layer, EnvelopeOrder[0], EnvelopeOrder[1], EnvelopeOrder[2], Candidate.MaterialCost)
                        elif RunState.Policy.TrackAssignment.MinimizeMaximumRoutingLayer:
                            OrderValues = (Candidate.Layer, Candidate.MaterialCost, Candidate.FootprintGrowth, Candidate.Length, Candidate.BendCount)
                        else:
                            OrderValues = (Candidate.MaterialCost, Candidate.FootprintGrowth, Candidate.Length, Candidate.BendCount, Candidate.ViaCount)
                        CandidateValues.append((Signal, Candidate.CandidateId, list(Wire), list(Support), list(Air), list(Electrical), *OrderValues))
                NativeResult = RunState.Context.PlanAuthoritativeRoutesBounded(CandidateValues, len(AssignmentResourcePositions), ExpansionLimit, RunServices.RemainingRoutingRuntimeMilliseconds(RunState.Deadline, RunState.AdaptiveExpiresAt))
                RunState.CheckRuntimeBudget('InitialCandidateAssignment', {'ExpansionCount': int(NativeResult.ExpansionCount), 'ExpansionLimit': ExpansionLimit, 'Native': True})
                ConflictSignals = tuple((str(Signal) for Signal in NativeResult.ConflictSignals))
                NativeFailureWitness = {}
                for WitnessSignal, WitnessCandidateId in NativeResult.SelectedCandidateIds:
                    Candidate = CandidateLookup.get(str(WitnessCandidateId))
                    if Candidate is None:
                        continue
                    NativeFailureWitness[str(WitnessSignal)] = {'CandidateId': Candidate.CandidateId, 'SourcePortalId': Candidate.SourcePortalId, 'TargetPortalIds': dict(sorted(Candidate.TargetPortalIds.items()))}
                RunState.InitialAssignmentDiagnostics.update({'Result': 'assigned' if NativeResult.Success else 'no-assignment', 'ExpansionCount': int(NativeResult.ExpansionCount), 'ExpansionLimit': ExpansionLimit, 'BudgetExhausted': bool(NativeResult.BudgetExhausted), 'DeadlineExceeded': bool(NativeResult.DeadlineExceeded), 'Native': True, 'FailureNet': NativeResult.FailureNet, 'ConflictSignals': list(ConflictSignals), 'CandidateCounts': {Signal: len(Values) for Signal, Values in sorted(CandidateSets.items())}, 'DeadEnds': [{'BlockedSignal': NativeResult.FailureNet, 'AssignedCandidates': NativeFailureWitness, 'BlockedCandidates': []}] if not NativeResult.Success else []})
                if not NativeResult.Success:
                    return None
                return {str(Signal): CandidateLookup[str(CandidateId)] for Signal, CandidateId in NativeResult.SelectedCandidateIds}
        ExpansionCount = 0
        Assignment: dict[str, RunServices.NetRouteCandidate] = {}
        AssignmentDeadEnds: list[dict[str, object]] = []
        AssignmentConflictCache: dict[tuple[str, str], int] = {}

        def AssignmentClaimConflictCount(First: NetRouteCandidate, Second: NetRouteCandidate) -> int:
            Key = tuple(sorted((First.CandidateId, Second.CandidateId)))
            Cached = AssignmentConflictCache.get(Key)
            if Cached is not None:
                return Cached
            Count = RunState.ClaimConflictCount(First.Claims, Second.Claims)
            AssignmentConflictCache[Key] = Count
            return Count

        def DescribeCandidateConflicts(Candidate: NetRouteCandidate) -> dict[str, object]:
            Result: dict[str, object] = {}
            for AssignedSignal, AssignedCandidate in sorted(Assignment.items()):
                Conflicts = RunServices.FindClaimConflicts({Candidate.Signal: Candidate.Claims, AssignedSignal: AssignedCandidate.Claims})
                if Conflicts:
                    Result[AssignedSignal] = {'Count': len(Conflicts), 'Resources': [str(Resource) for Resource in sorted(Conflicts, key=str)[:16]]}
            return Result

        def Search(Remaining: tuple[str, ...]) -> bool:
            nonlocal ExpansionCount
            if not Remaining:
                return True
            AvailableBySignal: list[tuple[str, tuple[RunServices.NetRouteCandidate, ...]]] = []
            for Signal in Remaining:
                Available = tuple((Candidate for Candidate in CandidateSets[Signal] if all((AssignmentClaimConflictCount(Candidate, Other) == 0 for Other in Assignment.values()))))
                if not Available:
                    if len(AssignmentDeadEnds) < 16:
                        AssignmentDeadEnds.append({'BlockedSignal': Signal, 'AssignedCandidates': {AssignedSignal: {'CandidateId': AssignedCandidate.CandidateId, 'SourcePortalId': AssignedCandidate.SourcePortalId, 'TargetPortalIds': dict(sorted(AssignedCandidate.TargetPortalIds.items()))} for AssignedSignal, AssignedCandidate in sorted(Assignment.items())}, 'BlockedCandidates': [{'CandidateId': Candidate.CandidateId, 'SourcePortalId': Candidate.SourcePortalId, 'TargetPortalIds': dict(sorted(Candidate.TargetPortalIds.items())), 'Conflicts': DescribeCandidateConflicts(Candidate)} for Candidate in CandidateSets[Signal]]})
                    return False
                AvailableBySignal.append((Signal, Available))
            Signal, Available = min(AvailableBySignal, key=lambda Value: (len(Value[1]), -RunState.Profiles[Value[0]].Fanout, -RunState.Profiles[Value[0]].Span, Value[0]))
            NextRemaining = tuple((Value for Value in Remaining if Value != Signal))
            RankedAvailable = tuple(sorted(Available, key=lambda Candidate: (sum((AssignmentClaimConflictCount(Candidate, OtherCandidate) for OtherSignal in NextRemaining for OtherCandidate in CandidateSets[OtherSignal])), *(RunState.EnvelopeQuality((*Assignment.values(), Candidate)) if OptimizeEnvelope else InitialCandidateOrder(Candidate)), Candidate.CandidateId)))
            for Candidate in RankedAvailable:
                ExpansionCount += 1
                if ExpansionCount % 8 == 0:
                    RunState.CheckRuntimeBudget('InitialCandidateAssignment', {'ExpansionCount': ExpansionCount, 'ExpansionLimit': ExpansionLimit})
                if ExpansionCount > ExpansionLimit:
                    return False
                Assignment[Signal] = Candidate
                if Search(NextRemaining):
                    return True
                del Assignment[Signal]
            return False
        if not Search(RunState.SignalOrder):
            RunState.InitialAssignmentDiagnostics.update({'Result': 'no-assignment', 'ExpansionCount': ExpansionCount, 'ExpansionLimit': ExpansionLimit, 'DeadEnds': AssignmentDeadEnds, 'CandidateCounts': {Signal: len(Values) for Signal, Values in sorted(CandidateSets.items())}})
            return None
        RunState.InitialAssignmentDiagnostics.update({'Result': 'assigned', 'ExpansionCount': ExpansionCount, 'ExpansionLimit': ExpansionLimit, 'CandidateCounts': {Signal: len(Values) for Signal, Values in sorted(CandidateSets.items())}, 'SelectedEnvelope': RunServices.BuildRoutingEnvelope((Position for Candidate in Assignment.values() for Position in Candidate.Nodes), (Position for Candidate in Assignment.values() for Position in Candidate.Claims.SupportCells), (Reservation.Position for Candidate in Assignment.values() for Reservation in Candidate.RepeaterReservations)).ToDictionary()})
        return dict(Assignment)
    RunState.TryInitialCandidateAssignment = TryInitialCandidateAssignment

    def ExpandSignalRegion(Signal: str, Side: str, Cause: str, Touches: tuple[Position3, ...]=()) -> bool:
        State = RunState.RegionStates[Signal]
        DeltaBySide = {'MinimumX': (-1, 0), 'MaximumX': (1, 0), 'MinimumZ': (0, -1), 'MaximumZ': (0, 1)}
        DeltaX, DeltaZ = DeltaBySide[Side]
        BoundaryTiles = [Tile for Tile in sorted(State.ActiveTiles) if (Tile[0] + DeltaX, Tile[1] + DeltaZ) not in State.ActiveTiles and RunServices._NegotiatedTileIntersectsBounds((Tile[0] + DeltaX, Tile[1] + DeltaZ), State.Bounds, State.TileSize)]
        if not BoundaryTiles:
            return False
        AnchorTile = RunServices._NegotiatedTileForColumn((Touches[0][0], Touches[0][2]), State.Bounds, State.TileSize) if Touches else BoundaryTiles[0]
        SelectedBoundaryTile = min(BoundaryTiles, key=lambda Tile: (abs(Tile[0] - AnchorTile[0]) + abs(Tile[1] - AnchorTile[1]), Tile))
        ExpandedTiles = frozenset({*State.ActiveTiles, (SelectedBoundaryTile[0] + DeltaX, SelectedBoundaryTile[1] + DeltaZ)})
        if ExpandedTiles == frozenset(State.ActiveTiles):
            return False
        State.ActiveTiles = set(ExpandedTiles)
        AddedTile = (SelectedBoundaryTile[0] + DeltaX, SelectedBoundaryTile[1] + DeltaZ)
        State.ActiveColumns.update(RunServices.NegotiatedColumnsForTiles(frozenset({AddedTile}), State.Bounds, State.TileSize))
        ExpandedRegion = RunState.Resources.ResourceGraph.BuildRegion(RunState.Region.Bounds, AllowedColumns=frozenset(State.ActiveColumns), AllowedAccess=RunState.ReservedAccess, WorkCheck=lambda Diagnostics: RunState.CheckRuntimeBudget('ResourceGraphExpansion', {'Signal': Signal, 'Side': Side, 'Cause': Cause, **Diagnostics}))
        DeltaNodes = set(ExpandedRegion.Nodes) - RunState.ContextNodes
        DeltaEdges = set(ExpandedRegion.Edges) - RunState.ContextEdges
        if DeltaNodes or DeltaEdges:
            RunState.Context.AddRegion(sorted(DeltaNodes), sorted(DeltaEdges))
            RunState.ContextNodes.update(DeltaNodes)
            RunState.ContextEdges.update(DeltaEdges)
            for Position in sorted(DeltaNodes):
                Column = (Position[0], Position[2])
                RunState.NodesByColumn[Column].append(Position)
            for Values in RunState.NodesByColumn.values():
                Values.sort()
        OwnedColumns = State.ActiveColumns
        State.AddedNodes.update((Position for Position in ExpandedRegion.Nodes if (Position[0], Position[2]) in OwnedColumns))
        State.AddedEdges.update((Edge for Edge in ExpandedRegion.Edges if Edge[0] in State.AddedNodes and Edge[1] in State.AddedNodes))
        State.BoundaryTouches.update(Touches)
        State.ExpandedSides.append(Side)
        State.ExpansionEvents.append({'Cause': Cause, 'Side': Side, 'BoundaryTouches': [list(Value) for Value in Touches], 'ActiveTileCount': len(State.ActiveTiles), 'AddedNodeCount': len(DeltaNodes), 'AddedEdgeCount': len(DeltaEdges), 'TotalNodeCount': len(RunState.ContextNodes), 'TotalEdgeCount': len(RunState.ContextEdges)})
        return True
    RunState.ExpandSignalRegion = ExpandSignalRegion

    def PreferredExpansionSides(Signal: str, Candidate: NetRouteCandidate | None=None, Hotspots: tuple[Position3, ...]=()) -> tuple[str, ...]:
        State = RunState.RegionStates[Signal]
        Touches = RunServices.FindNegotiatedBoundaryTouches(Candidate.Nodes if Candidate is not None else State.BoundaryTouches, State.ActiveTiles, State.Bounds, State.TileSize)
        if Touches:
            return tuple(sorted(Touches, key=lambda Side: (-len(Touches[Side]), Side)))
        Profile = RunState.Profiles[Signal]
        Root = Profile.SourceAccessPath[-1]
        Points = Hotspots or tuple(Profile.Targets)
        if not Points:
            return ('MaximumX', 'MaximumZ', 'MinimumX', 'MinimumZ')
        Point = max(Points, key=lambda Value: (abs(Value[0] - Root[0]) + abs(Value[2] - Root[2]), Value))
        DeltaX = Point[0] - Root[0]
        DeltaZ = Point[2] - Root[2]
        Primary = ('MaximumX' if DeltaX >= 0 else 'MinimumX') if abs(DeltaX) >= abs(DeltaZ) else 'MaximumZ' if DeltaZ >= 0 else 'MinimumZ'
        Ordered = (Primary, 'MaximumX', 'MaximumZ', 'MinimumX', 'MinimumZ')
        return tuple(dict.fromkeys(Ordered))
    RunState.PreferredExpansionSides = PreferredExpansionSides

    def CandidatePresentPositionCounts(Candidate: NetRouteCandidate) -> Counter[Position3]:
        Cached = RunState.CandidatePresentPositions.get(Candidate.CandidateId)
        if Cached is not None:
            return Cached
        Result: RunServices.Counter[RunServices.Position3] = RunServices.Counter()
        for Position in Candidate.Claims.ElectricalCells | Candidate.Claims.SupportCells | Candidate.Claims.RequiredAirCells:
            Result[Position] += 1
        for X, Y, Z in Candidate.Claims.WireCells | Candidate.Claims.RequiredAirCells:
            Result[X, Y + 1, Z] += 1
        RunState.CandidatePresentPositions[Candidate.CandidateId] = Result
        return Result
    RunState.CandidatePresentPositionCounts = CandidatePresentPositionCounts

    def CandidateNodeCosts(Signal: str, PresentPositionCounts: Counter[Position3]) -> list[tuple[Position3, int]]:
        if RunState.CurrentPassIndex == 0:
            return []
        Costs: RunServices.Counter[RunServices.Position3] = RunServices.Counter(RunState.History)
        Present = RunState.Negotiated.PresentConflictPenalty * (RunState.CurrentPassIndex + 1)
        for Position, Count in PresentPositionCounts.items():
            if Count > 0:
                Costs[Position] += Present * Count
        Required = {Position for Path in (RunState.Profiles[Signal].SourceAccessPath, *RunState.Profiles[Signal].TargetAccessPaths.values()) for Position in Path}
        return sorted(((Position, Cost) for Position, Cost in Costs.items() if Cost > 0 and Position not in Required))
    RunState.CandidateNodeCosts = CandidateNodeCosts

    def ExactAssignmentCompletionNodeCosts(Signal: str, RequestIndex: int) -> list[tuple[Position3, int]]:
        """Steer one request around a concrete opposing exact-cut tree."""
        ConflictSignals = {str(Value) for Value in RunState.InitialAssignmentDiagnostics.get('ConflictSignals', ()) if str(Value) != Signal}
        FailureWitnessBySignal: dict[str, str] = {}
        for DeadEnd in RunState.InitialAssignmentDiagnostics.get('DeadEnds', ()):
            if not isinstance(DeadEnd, dict):
                continue
            AssignedCandidates = DeadEnd.get('AssignedCandidates', {})
            if not isinstance(AssignedCandidates, dict):
                continue
            for WitnessSignal, CandidateDiagnostic in AssignedCandidates.items():
                if not isinstance(CandidateDiagnostic, dict):
                    continue
                CandidateId = CandidateDiagnostic.get('CandidateId')
                if CandidateId is not None:
                    FailureWitnessBySignal[str(WitnessSignal)] = str(CandidateId)
        Costs: RunServices.Counter[RunServices.Position3] = RunServices.Counter()
        CompletionConflictPenalty = RunState.Negotiated.PresentConflictPenalty
        for OtherSignal in ConflictSignals:
            CandidateValues = RunState.InitialCandidateOptions.get(OtherSignal, {})
            WitnessCandidate = CandidateValues.get(FailureWitnessBySignal.get(OtherSignal, ''))
            OtherCandidates = (WitnessCandidate,) if WitnessCandidate is not None else tuple(sorted(CandidateValues.values(), key=lambda Value: (Value.MaterialCost, Value.CandidateId)))
            if not OtherCandidates:
                continue
            OtherCandidate = OtherCandidates[RequestIndex % len(OtherCandidates)]
            ConflictFootprint = OtherCandidate.Claims.WireCells | OtherCandidate.Claims.ElectricalCells | OtherCandidate.Claims.SupportCells | OtherCandidate.Claims.RequiredAirCells
            for Position in ConflictFootprint:
                Costs[Position] += CompletionConflictPenalty
            for X, Y, Z in OtherCandidate.Claims.WireCells | OtherCandidate.Claims.RequiredAirCells:
                Costs[X, Y + 1, Z] += CompletionConflictPenalty
            for X, Y, Z in OtherCandidate.Claims.WireCells:
                Costs[X, Y - 1, Z] += CompletionConflictPenalty
        Required = {Position for Path in (RunState.Profiles[Signal].SourceAccessPath, *RunState.Profiles[Signal].TargetAccessPaths.values()) for Position in Path}
        return sorted(((Position, Cost) for Position, Cost in Costs.items() if Cost > 0 and Position not in Required))
    RunState.ExactAssignmentCompletionNodeCosts = ExactAssignmentCompletionNodeCosts

    def ClaimConflictCount(First: RoutingResourceClaims, Second: RoutingResourceClaims) -> int:
        return len(RunServices.ClaimConflictPositions(First, Second))
    RunState.ClaimConflictCount = ClaimConflictCount

    def CandidateClaimConflictCount(First: NetRouteCandidate, Second: NetRouteCandidate) -> int:
        Key = tuple(sorted((First.CandidateId, Second.CandidateId)))
        Cached = RunState.CandidatePairConflictCountCache.get(Key)
        if Cached is not None:
            return Cached

        def ClaimMasks(Candidate: NetRouteCandidate) -> tuple[int, int, int, int]:
            CachedMasks = RunState.CandidateClaimMasks.get(Candidate.CandidateId)
            if CachedMasks is not None:
                return CachedMasks

            def PositionMask(Positions: frozenset[Position3]) -> int:
                Result = 0
                for Position in sorted(Positions):
                    PositionIndex = RunState.ClaimPositionIndices.setdefault(Position, len(RunState.ClaimPositionIndices))
                    Result |= 1 << PositionIndex
                return Result
            Result = tuple((PositionMask(Positions) for Positions in (Candidate.Claims.WireCells, Candidate.Claims.SupportCells, Candidate.Claims.RequiredAirCells, Candidate.Claims.ElectricalCells)))
            RunState.CandidateClaimMasks[Candidate.CandidateId] = Result
            return Result
        FirstWire, FirstSupport, FirstAir, FirstElectrical = ClaimMasks(First)
        SecondWire, SecondSupport, SecondAir, SecondElectrical = ClaimMasks(Second)
        ConflictPositions = FirstWire & SecondElectrical | SecondWire & FirstElectrical | FirstSupport & (SecondWire | SecondAir) | SecondSupport & (FirstWire | FirstAir) | FirstAir & SecondWire | SecondAir & FirstWire
        Count = ConflictPositions.bit_count()
        RunState.CandidatePairConflictCountCache[Key] = Count
        return Count
    RunState.CandidateClaimConflictCount = CandidateClaimConflictCount

    def RequestMandatoryClaims(Signal: str, RequestIndex: int) -> RoutingResourceClaims:
        SourcePortal, TargetPortals, _Guide, _Layer, _Axis, _Lane, _Variant = RunState.RouteMetadataBySignal[Signal][RequestIndex]
        MandatoryNodes = {*RunState.Profiles[Signal].SourceAccessPath, *SourcePortal.Path, *(Position for Target in RunState.Profiles[Signal].Targets for Position in RunState.Profiles[Signal].TargetAccessPaths[Target]), *(Position for Portal in TargetPortals for Position in Portal.Path)}
        CacheKey = (Signal, RequestIndex)
        MandatoryClaims = RunState.MandatoryClaimsCache.get(CacheKey)
        if MandatoryClaims is None:
            PortalSignature = (Signal, SourcePortal.PortalId, tuple((Portal.PortalId for Portal in TargetPortals)))
            MandatoryClaims = RunState.MandatoryClaimsByPortalSignature.get(PortalSignature)
            if MandatoryClaims is None:
                MandatoryClaims = RunState.Resources.ResourceGraph.BuildRouteClaims(MandatoryNodes)
                RunState.MandatoryClaimsByPortalSignature[PortalSignature] = MandatoryClaims
            RunState.MandatoryClaimsCache[CacheKey] = MandatoryClaims
        return MandatoryClaims
    RunState.RequestMandatoryClaims = RequestMandatoryClaims

    def RequestMandatoryConflictCount(Signal: str, RequestIndex: int, Candidates: dict[str, NetRouteCandidate] | None=None) -> int:
        MandatoryClaims = RunState.RequestMandatoryClaims(Signal, RequestIndex)
        return sum((RunState.ClaimConflictCount(MandatoryClaims, Other.Claims) for OtherSignal, Other in (RunState.Selected if Candidates is None else Candidates).items() if OtherSignal != Signal))
    RunState.RequestMandatoryConflictCount = RequestMandatoryConflictCount

    def RaiseIfUnavoidableMandatoryAssignmentCut() -> None:
        if not RunState.InitialAssignmentDiagnostics.get('Native') or RunState.InitialAssignmentDiagnostics.get('Result') != 'no-assignment' or RunState.InitialAssignmentDiagnostics.get('BudgetExhausted', False) or RunState.InitialAssignmentDiagnostics.get('DeadlineExceeded', False):
            return
        NativeCutSignals = tuple(sorted({*(str(Signal) for Signal in RunState.InitialAssignmentDiagnostics.get('ConflictSignals', ())), *((str(RunState.InitialAssignmentDiagnostics['FailureNet']),) if RunState.InitialAssignmentDiagnostics.get('FailureNet') else ())}))
        MandatoryClaimsBySignal = {Signal: tuple({RunState.RequestMandatoryClaims(Signal, RequestIndex) for RequestIndex in range(len(RunState.RouteMetadataBySignal.get(Signal, ())))}) for Signal in NativeCutSignals}
        MandatoryCut = RunServices.FindUnavoidableMandatoryClaimCut(MandatoryClaimsBySignal)
        if MandatoryCut is None:
            return
        CutSignals, CutPositions = MandatoryCut
        if not RunServices.PortalTupleDomainIsCompleteForSignals(CutSignals):
            return
        RunState.ExactAssignmentCutSignals.update(CutSignals)
        raise RunServices.RoutingStageError(RunServices.RoutingFailure(Reason=RunServices.RoutingFailureReason.TrackAssignmentConflict, Stage='InitialCandidateAssignment', AffectedNets=CutSignals, Locations=tuple(sorted(CutPositions))[:32], RepairActions=('RelocateAffectedClusters',), Detail='every generated fixed portal/access alternative for the native dead-end pair conflicts', Diagnostics={'InitialAssignment': dict(RunState.InitialAssignmentDiagnostics), 'MandatoryAlternativeCounts': {Signal: len(MandatoryClaimsBySignal[Signal]) for Signal in CutSignals}, 'MandatoryConflictPositionCount': len(CutPositions), 'MandatoryAccessProof': RunServices.BuildGeneratedFixedPortalDomainExhaustionProof(CutPositions, 1), 'ConflictGraph': {'Classification': 'mandatory-boundary-capacity-cut', 'ConflictSignals': list(CutSignals), 'CongestionCutSignals': list(CutSignals), 'RelocationSignals': list(CutSignals), 'PriorityRelocationSignals': list(CutSignals)}}))
    RunState.RaiseIfUnavoidableMandatoryAssignmentCut = RaiseIfUnavoidableMandatoryAssignmentCut

    def RouteRequest(Signal: str, RequestIndex: int, NodeCosts: list[tuple[Position3, int]], MinimumExpansionCount: int | None=None, MaximumRuntimeMilliseconds: int | None=None, MaximumExpansionCountOverride: int | None=None) -> NetRouteCandidate | None:
        RouteRequestStarted = RunServices.monotonic()
        Requests = RunState.RouteRequestsBySignal.get(Signal, ())
        MetadataValues = RunState.RouteMetadataBySignal.get(Signal, ())
        if not Requests or not MetadataValues:
            return None
        RequestIndex %= min(len(Requests), len(MetadataValues))
        Starts, TargetBranches, _AllowedColumns, RequiredNodes, BlockedNodeValues, PreferredColumns, PreferredRoutingY, GuidePenalty, BendPenalty, ViaPenalty, MaximumExpansionCount = Requests[RequestIndex]
        EffectiveMaximumExpansionCount = min(RunState.Policy.DetailedRouting.StrictBaseExpansions if MaximumExpansionCountOverride is None else MaximumExpansionCountOverride, max(MaximumExpansionCount, MinimumExpansionCount or 0))
        RepairState = RunState.RepairStates.get(Signal)
        if RepairState is not None and RepairState.PrunedTargets:
            RootedStarts = list(Starts)
            RetainedNodes = set(RepairState.RetainedNodes)
            RetainedNodes.update(RunState.Profiles[Signal].SourceAccessPath)
            Starts = list(dict.fromkeys((*RootedStarts, *sorted(RetainedNodes))))
            TargetBranches = [Branch for Target, Branch in zip(RepairState.PrunedBranchIds, RepairState.PrunedBranchPaths)]
        MandatorySelfConflicts = RunState.InitialDetailedBatchPreflightConflicts.get((Signal, RequestIndex), frozenset())
        if not MandatorySelfConflicts:
            MandatorySelfConflicts = RunServices.FindSelfClaimConflicts({Signal: RunState.RequestMandatoryClaims(Signal, RequestIndex)})
        if MandatorySelfConflicts:
            RunState.MandatorySelfConflictsBySignal[Signal].update(MandatorySelfConflicts)
            RunState.RejectionCountsBySignal[Signal]['MandatorySelfClaimConflict'] += 1
            return None
        ActiveColumns = RunState.RegionStates[Signal].ActiveColumns
        AllowedNodes = {Position for Column in ActiveColumns for Position in RunState.NodesByColumn.get(tuple(Column), ())}
        AllowedNodes.update((tuple(Position) for Position in RequiredNodes))
        RunState.CheckRuntimeBudget('NegotiatedDetailedRouting', {'Signal': Signal, 'RequestIndex': RequestIndex, 'NegotiatedIteration': RunState.CurrentPassIndex, 'SelectedSignalCount': len(RunState.Selected), 'OverflowProgression': list(RunState.OverflowProgression)})
        if not hasattr(RunState.Context, 'GenerateRouteTreeDetailedBounded'):
            raise ValueError('negotiated routing requires the diagnostic Rust routing API')
        SearchBlockedNodes = set(BlockedNodeValues)
        RequiredNodeSet = set(RequiredNodes)
        if RunState.IsPartialSeedCompletion:
            PartialCompletionBlockedNodes: set[RunServices.Position3] = set()
            for OtherSignal, OtherCandidate in RunState.Selected.items():
                if OtherSignal == Signal:
                    continue
                PartialCompletionBlockedNodes.update(OtherCandidate.Claims.ElectricalCells | OtherCandidate.Claims.SupportCells | OtherCandidate.Claims.RequiredAirCells)
                for X, Y, Z in OtherCandidate.Claims.WireCells | OtherCandidate.Claims.RequiredAirCells:
                    PartialCompletionBlockedNodes.add((X, Y + 1, Z))
                for X, Y, Z in OtherCandidate.Claims.WireCells:
                    PartialCompletionBlockedNodes.add((X, Y - 1, Z))
            SearchBlockedNodes.update(PartialCompletionBlockedNodes - RequiredNodeSet)
        SelfClaimCutCount = 0
        BatchedSearchResult = RunState.InitialDetailedBatchResults.pop((Signal, RequestIndex), None) if RunState.CurrentPassIndex == 0 else None
        while True:
            if BatchedSearchResult is not None:
                SearchResult = BatchedSearchResult
                BatchedSearchResult = None
            else:
                SearchResult = RunState.Context.GenerateRouteTreeDetailedBounded(Starts, TargetBranches, sorted(AllowedNodes), sorted(SearchBlockedNodes), PreferredColumns, NodeCosts, PreferredRoutingY, GuidePenalty, BendPenalty, ViaPenalty, True, EffectiveMaximumExpansionCount, min(RunState.Negotiated.MaximumRouteTreeRequestMilliseconds if MaximumRuntimeMilliseconds is None else MaximumRuntimeMilliseconds, RunServices.RemainingRoutingRuntimeMilliseconds(RunState.Deadline, RunState.AdaptiveExpiresAt)))
            if SearchResult.Status != 'Routed':
                break
            RoutedClaims = RunState.Resources.ResourceGraph.BuildRouteClaims(SearchResult.Nodes)
            SelfClaimConflicts = RunServices.FindSelfClaimConflicts({Signal: RoutedClaims})
            if not SelfClaimConflicts:
                break
            if SelfClaimCutCount >= 3:
                break
            ConflictPositions = {Resource.Position for Resource in SelfClaimConflicts}
            CutNodes = {Node for Node in SearchResult.Nodes if Node not in RequiredNodeSet and any((abs(Node[0] - Position[0]) + abs(Node[1] - Position[1]) + abs(Node[2] - Position[2]) <= 1 for Position in ConflictPositions))}
            CutNodes -= SearchBlockedNodes
            if not CutNodes:
                break
            SearchBlockedNodes.update(CutNodes)
            SelfClaimCutCount += 1
        FrontierNodes = tuple(SearchResult.BoundaryFrontierNodes)
        FrontierTouches = RunServices.FindNegotiatedBoundaryTouches(FrontierNodes, RunState.RegionStates[Signal].ActiveTiles, RunState.RegionStates[Signal].Bounds, RunState.RegionStates[Signal].TileSize)
        if SearchResult.Status == 'Routed':
            RunState.RegionStates[Signal].BoundaryTouches.update(FrontierNodes)
        RunState.RouteRequestDiagnostics[Signal] = {'Status': SearchResult.Status, 'NoPathReason': SearchResult.NoPathReason, 'ExpansionCount': SearchResult.ExpansionCount, 'MaximumExpansionCount': EffectiveMaximumExpansionCount, 'BoundaryFrontierNodes': [list(Value) for Value in FrontierNodes], 'BoundaryFrontierTouches': {Side: [list(Value) for Value in Values] for Side, Values in FrontierTouches.items()}}
        NativeSearchDiagnostics = {'RequestIndex': RequestIndex, 'Iteration': RunState.CurrentPassIndex, 'Status': SearchResult.Status, 'NoPathReason': SearchResult.NoPathReason if SearchResult.Status == 'NoPath' else '', 'ExpansionCount': SearchResult.ExpansionCount, 'MaximumExpansionCount': EffectiveMaximumExpansionCount, 'BoundaryFrontierNodes': [list(Value) for Value in FrontierNodes], 'RepeaterReservationCount': len(SearchResult.RepeaterReservations), 'RepeaterRejectedCount': SearchResult.RepeaterRejectedCount, 'SelfClaimCutCount': SelfClaimCutCount, 'RemainingSelfClaimConflicts': [str(Resource) for Resource in sorted(SelfClaimConflicts if SearchResult.Status == 'Routed' else {}, key=str)]}
        RunState.NativeSearchDiagnosticsBySignal[Signal].append(NativeSearchDiagnostics)
        if SearchResult.RepeaterRejectedCount:
            RunState.RejectionCountsBySignal[Signal]['NoRepeater'] += SearchResult.RepeaterRejectedCount
        if SearchResult.Status == 'BudgetExpired':
            RunState.RejectionCountsBySignal[Signal]['NativeBudgetExpired'] += 1
        elif SearchResult.Status == 'NoPath':
            RunState.RejectionCountsBySignal[Signal]['NoPath'] += 1
            if SearchResult.NoPathReason == 'NoRepeater':
                RunState.RejectionCountsBySignal[Signal]['NoRepeater'] += 1
        if RepairState is not None and RepairState.PrunedTargets:
            for Target in RepairState.PrunedTargets:
                RunState.RepairBranchOutcomes.setdefault(Signal, {})[str(Target)] = 'Rejected'
        RoutedTree = list(SearchResult.Nodes) if SearchResult.Status == 'Routed' else None
        if RoutedTree is not None and RepairState is not None:
            RoutedTree = sorted({*RoutedTree, *RepairState.RetainedNodes})
        SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant = MetadataValues[RequestIndex]
        MaterializationDiagnostics: dict[str, object] = {}
        MaterializationNodes = frozenset({*(RoutedTree or ()), *RunState.Profiles[Signal].SourceAccessPath, *SourcePortal.Path, *(Position for Target in RunState.Profiles[Signal].Targets for Position in RunState.Profiles[Signal].TargetAccessPaths[Target]), *(Position for Portal in TargetPortals for Position in Portal.Path)})
        MaterializationCacheKey = (Signal, Layer, MaterializationNodes, tuple(sorted(SearchResult.RepeaterReservations)))
        CachedMaterialization = RunState.MaterializedCandidateCache.get(MaterializationCacheKey)
        if CachedMaterialization is not None:
            Candidate, CachedDiagnostics = CachedMaterialization
            MaterializationDiagnostics.update(CachedDiagnostics)
            MaterializationDiagnostics['CacheHit'] = True
            RunState.InitialDetailedBatchDiagnostics['MaterializationCacheHits'] = int(RunState.InitialDetailedBatchDiagnostics['MaterializationCacheHits']) + 1
        else:
            Candidate = RunServices.PortalOperations._MaterializeCandidate(Signal, RunState.Profiles[Signal], SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant, RoutedTree, RunState.Region, RunState.Resources, RunState.Technology, RunState.Policy.DetailedRouting.LengthPenalty, RunState.Policy.DetailedRouting.CandidateBendWeight, RunState.Policy.DetailedRouting.CandidateViaWeight, RunState.Policy.DetailedRouting.LayerPenalty, RepeaterPenalty=RunState.Policy.DetailedRouting.RepeaterPenalty, NativeRepeaterReservations=tuple(SearchResult.RepeaterReservations), RejectionCounts=RunState.RejectionCountsBySignal[Signal], MaterializationDiagnostics=MaterializationDiagnostics)
            RunState.MaterializedCandidateCache[MaterializationCacheKey] = (Candidate, dict(MaterializationDiagnostics))
            RunState.InitialDetailedBatchDiagnostics['MaterializationCacheMisses'] = int(RunState.InitialDetailedBatchDiagnostics['MaterializationCacheMisses']) + 1
        NativeSearchDiagnostics['Materialization'] = MaterializationDiagnostics
        RunState.RouteRequestDiagnostics[Signal]['Materialization'] = MaterializationDiagnostics
        if Candidate is not None and RepairState is not None and RepairState.PrunedTargets:
            TargetPaths = {Target for Target, _Path in Candidate.TargetPaths.items()}
            for Target in RepairState.PrunedBranchIds:
                RunState.RepairBranchOutcomes.setdefault(Signal, {})[str(Target)] = 'Committed' if Target in TargetPaths else 'Lost'
        RouteRequestElapsed = RunServices.monotonic() - RouteRequestStarted
        if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')) and RouteRequestElapsed >= 0.25:
            print(f'[debug] authoritative: detailed route request iteration={RunState.CurrentPassIndex + 1} signal={Signal} request={RequestIndex} status={SearchResult.Status} materialized={Candidate is not None} elapsed={RouteRequestElapsed:.3f}s', flush=True)
        return Candidate
    RunState.RouteRequest = RouteRequest

    def BuildPassZeroDetailedSearchRequest(Signal: str, RequestIndex: int, MaximumExpansionCountOverride: int | None=None, NodeCosts: list[tuple[Position3, int]] | None=None) -> tuple[Any, ...] | None:
        """Freeze one independent initial detailed search for native batching."""
        Requests = RunState.RouteRequestsBySignal.get(Signal, ())
        MetadataValues = RunState.RouteMetadataBySignal.get(Signal, ())
        RequestCount = min(len(Requests), len(MetadataValues))
        if RequestCount == 0:
            return None
        RequestIndex %= RequestCount
        Starts, TargetBranches, _AllowedColumns, RequiredNodes, BlockedNodeValues, PreferredColumns, PreferredRoutingY, GuidePenalty, BendPenalty, ViaPenalty, MaximumExpansionCount = Requests[RequestIndex]
        MandatorySelfConflicts = RunServices.FindSelfClaimConflicts({Signal: RunState.RequestMandatoryClaims(Signal, RequestIndex)})
        if MandatorySelfConflicts:
            RunState.InitialDetailedBatchPreflightConflicts[Signal, RequestIndex] = frozenset(MandatorySelfConflicts)
            return None
        ActiveColumns = RunState.RegionStates[Signal].ActiveColumns
        AllowedNodes = {Position for Column in ActiveColumns for Position in RunState.NodesByColumn.get(tuple(Column), ())}
        AllowedNodes.update((tuple(Position) for Position in RequiredNodes))
        return (list(Starts), [list(Branch) for Branch in TargetBranches], sorted(AllowedNodes), sorted(BlockedNodeValues), sorted(PreferredColumns), list(NodeCosts or ()), PreferredRoutingY, GuidePenalty, BendPenalty, ViaPenalty, True, min(MaximumExpansionCount, RunState.Policy.DetailedRouting.StrictBaseExpansions) if MaximumExpansionCountOverride is None else min(RunState.Policy.AdaptiveRouting.MaximumCandidateGenerationExpansions, max(1, MaximumExpansionCountOverride)))
    RunState.BuildPassZeroDetailedSearchRequest = BuildPassZeroDetailedSearchRequest

    def PreparePassZeroDetailedSearchBatch(Signal: str, RequestIndices: tuple[int, ...], RemainingSignalCount: int) -> None:
        """Batch one signal's already-selected initial alternatives.

        The outer signal order changes capacity feedback and must remain
        serial.  Alternatives for the *current* signal share a frozen region,
        no present congestion, and no repair tree, so they are safe to search
        concurrently without changing which request IDs the serial selector
        considers.
        """
        if not hasattr(RunState.Context, 'GenerateRouteTreeDetailedBatchBounded'):
            return
        RunState.InitialDetailedBatchRequestIndices[Signal] = RequestIndices
        Scheduled = [(RequestIndex, Request) for RequestIndex in RequestIndices if (Request := RunState.BuildPassZeroDetailedSearchRequest(Signal, RequestIndex)) is not None]
        try:
            WorkerCount = max(1, int(RunServices.GetRustRoutingThreadCount()))
        except Exception:
            WorkerCount = 1
        RunState.InitialDetailedBatchDiagnostics.update({'Enabled': True, 'ScheduledRequestCount': int(RunState.InitialDetailedBatchDiagnostics['ScheduledRequestCount']) + len(RequestIndices), 'RequestCount': int(RunState.InitialDetailedBatchDiagnostics['RequestCount']) + len(Scheduled), 'WorkerCount': WorkerCount, 'PreflightRejectedRequestCount': len(RunState.InitialDetailedBatchPreflightConflicts)})
        RemainingMilliseconds = RunServices.RemainingRoutingRuntimeMilliseconds(RunState.Deadline, RunState.AdaptiveExpiresAt)
        SignalRuntimeMilliseconds = max(1, min(RunState.Negotiated.MaximumRouteTreeRequestMilliseconds, RemainingMilliseconds // max(1, RemainingSignalCount)))
        RunState.InitialDetailedBatchDiagnostics.setdefault('PerSignalRuntimeMilliseconds', {})[Signal] = SignalRuntimeMilliseconds
        for StartIndex in range(0, len(Scheduled), WorkerCount):
            Chunk = Scheduled[StartIndex:StartIndex + WorkerCount]
            RunState.CheckRuntimeBudget('NegotiatedDetailedRouting', {'Iteration': 0, 'Signal': Signal, 'BatchStartIndex': StartIndex, 'BatchRequestCount': len(Chunk)})
            MaximumRuntimeMilliseconds = min(SignalRuntimeMilliseconds, RunServices.RemainingRoutingRuntimeMilliseconds(RunState.Deadline, RunState.AdaptiveExpiresAt))
            if MaximumRuntimeMilliseconds <= 0:
                RunState.CheckRuntimeBudget('NegotiatedDetailedRouting', {'Iteration': 0, 'Signal': Signal})
                return
            BatchStarted = RunServices.monotonic()
            BatchResult = RunState.Context.GenerateRouteTreeDetailedBatchBounded([Request for _Index, Request in Chunk], MaximumRuntimeMilliseconds)
            if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                print(f'[debug] authoritative: pass-zero detailed batch signal={Signal} budget_ms={MaximumRuntimeMilliseconds} elapsed={RunServices.monotonic() - BatchStarted:.3f}s completed={BatchResult.CompletedWork} expired={BatchResult.DeadlineExceeded}', flush=True)
            SearchResults = list(BatchResult.SearchResults)
            if len(SearchResults) != len(Chunk):
                raise ValueError('detailed route-tree batch returned an unexpected result count')
            for (RequestIndex, _Request), SearchResult in zip(Chunk, SearchResults):
                RunState.InitialDetailedBatchResults[Signal, RequestIndex] = SearchResult
            RunState.InitialDetailedBatchDiagnostics['BatchCount'] = int(RunState.InitialDetailedBatchDiagnostics['BatchCount']) + 1
            RunState.InitialDetailedBatchDiagnostics['CompletedWork'] = int(RunState.InitialDetailedBatchDiagnostics['CompletedWork']) + int(BatchResult.CompletedWork)
            RunState.InitialDetailedBatchDiagnostics['DeadlineExceeded'] = bool(RunState.InitialDetailedBatchDiagnostics['DeadlineExceeded'] or BatchResult.DeadlineExceeded)
    RunState.PreparePassZeroDetailedSearchBatch = PreparePassZeroDetailedSearchBatch
    RunState.ConflictSignals: tuple[str, ...] = RunState.SignalOrder
    FinalConflicts: dict[RunServices.RoutingResourceId, tuple[str, ...]] = {}
    return PhaseOutcome()

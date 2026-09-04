"""Negotiated routing search phase."""
from __future__ import annotations
from ...Orchestration.RunState import AuthoritativeRoutingServices, PhaseOutcome
from .State import NegotiatedRoutingState

def RunSearch(RunState: NegotiatedRoutingState, RunServices: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run negotiated-routing search."""
    for PassIndex in range(RunState.Negotiated.MaximumIterations):
        PassStarted = RunServices.monotonic()
        RunState.CurrentPassIndex = PassIndex
        RunState.CheckRuntimeBudget('NegotiatedDetailedRouting', {'Iteration': PassIndex, 'SelectedSignals': len(RunState.Selected)})
        if PassIndex > 0 and RunState.StagnationCount > 0 and RunState.RepairStates:
            RunState.RepairStates = {}
            RunState.BranchRepairEvents.append({'Iteration': PassIndex, 'Action': 'full-tree-ripup-after-stagnation', 'PriorConflictCount': RunState.OverflowProgression[-1] if RunState.OverflowProgression else None})
        SignalsToRoute = RunState.SignalOrder if PassIndex == 0 else RunState.ConflictSignals
        DiscoveredCandidateThisPass = False
        PresentPositionCounts: RunServices.Counter[RunServices.Position3] = RunServices.Counter()
        for Candidate in RunState.Selected.values():
            PresentPositionCounts.update(RunState.CandidatePresentPositionCounts(Candidate))

        def UpdatePresentPositionCounts(Candidate: NetRouteCandidate, Delta: int) -> None:
            for Position, Count in RunState.CandidatePresentPositionCounts(Candidate).items():
                PresentPositionCounts[Position] += Delta * Count
        UpdatePresentPositionCounts = UpdatePresentPositionCounts

        def CommitSelectedCandidate(Signal: str, Candidate: NetRouteCandidate) -> None:
            RunState.Selected[Signal] = Candidate
            UpdatePresentPositionCounts(Candidate, 1)
        CommitSelectedCandidate = CommitSelectedCandidate
        for SignalIndex, Signal in enumerate(SignalsToRoute):
            Existing = RunState.Selected.pop(Signal, None)
            if Existing is not None:
                UpdatePresentPositionCounts(Existing, -1)
            NodeCostStarted = RunServices.monotonic()
            SignalNodeCosts = RunState.CandidateNodeCosts(Signal, PresentPositionCounts)
            NodeCostElapsed = RunServices.monotonic() - NodeCostStarted
            if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')) and NodeCostElapsed >= 0.25:
                print(f'[debug] authoritative: negotiated node costs iteration={PassIndex + 1} signal={Signal} positions={len(SignalNodeCosts)} elapsed={NodeCostElapsed:.3f}s', flush=True)
            Best: RunServices.NetRouteCandidate | None = None
            BestScore: tuple[RunServices.Any, ...] | None = None

            def ConsiderCandidate(Candidate: NetRouteCandidate) -> int:
                nonlocal Best, BestScore, DiscoveredCandidateThisPass
                'Retain the best current net tree using the normal score.'
                IsNewCandidate = Candidate.CandidateId not in RunState.InitialCandidateOptions[Signal]
                RunState.InitialCandidateOptions[Signal][Candidate.CandidateId] = Candidate
                DiscoveredCandidateThisPass = DiscoveredCandidateThisPass or IsNewCandidate
                PairConflicts = sum((RunState.CandidateClaimConflictCount(Candidate, Other) for Other in RunState.Selected.values()))
                HistoryCost = sum((RunState.History[Position] for Position in Candidate.Claims.ElectricalCells | Candidate.Claims.SupportCells | Candidate.Claims.RequiredAirCells))
                Score = (PairConflicts, HistoryCost, *((Candidate.Layer,) if RunState.Policy.TrackAssignment.MinimizeMaximumRoutingLayer else ()), Candidate.MaterialCost, Candidate.CandidateId)
                if BestScore is None or Score < BestScore:
                    Best = Candidate
                    BestScore = Score
                return PairConflicts
            ConsiderCandidate = ConsiderCandidate
            if PassIndex == 0 and RunState.IsPartialSeedCompletion and (Existing is None):
                for Candidate in RunState.InitialCandidateOptions.get(Signal, {}).values():
                    ConsiderCandidate(Candidate)
                if Best is not None and BestScore is not None and (BestScore[0] == 0):
                    CommitSelectedCandidate(Signal, Best)
                    continue
            if PassIndex == 0 and Existing is not None and (Signal not in RunState.RegenerateSignals):
                RunState.InitialCandidateOptions[Signal][Existing.CandidateId] = Existing
                ConsiderCandidate(Existing)
                if RunState.CompleteSeedDomain:
                    for Candidate in RunState.InitialCandidateOptions.get(Signal, {}).values():
                        ConsiderCandidate(Candidate)
                    if Best is not None:
                        CommitSelectedCandidate(Signal, Best)
                        continue
            if PassIndex > 0:
                CandidateRescoreStarted = RunServices.monotonic()
                for Candidate in RunState.InitialCandidateOptions.get(Signal, {}).values():
                    ConsiderCandidate(Candidate)
                if Best is not None and BestScore is not None:
                    RunState.CachedRepairSelections.append({'Iteration': PassIndex, 'Signal': Signal, 'CandidateId': Best.CandidateId, 'ConflictCount': BestScore[0], 'HistoryCost': BestScore[1]})
                    if BestScore[0] == 0:
                        CommitSelectedCandidate(Signal, Best)
                        continue
                CandidateRescoreElapsed = RunServices.monotonic() - CandidateRescoreStarted
                if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')) and CandidateRescoreElapsed >= 0.25:
                    print(f'[debug] authoritative: negotiated candidate rescore iteration={PassIndex + 1} signal={Signal} candidates={len(RunState.InitialCandidateOptions.get(Signal, {}))} elapsed={CandidateRescoreElapsed:.3f}s', flush=True)
            RequestCount = len(RunState.RouteRequestsBySignal.get(Signal, ()))
            if RequestCount == 0 and Existing is not None and (Signal not in RunState.RegenerateSignals):
                CommitSelectedCandidate(Signal, Existing)
                continue
            BaseRequestWindow = (8 if RunState.IsPartialSeedCompletion else max(RunState.InitialDetailedRequestWindow, 32 if RunState.HasValidatedLocalClaims and len(RunState.Profiles[Signal].Targets) >= 4 else RunState.InitialDetailedRequestWindow)) if PassIndex == 0 else 2 if 200 <= RunState.TerminalCount <= 256 or RunState.HasValidatedLocalClaims else 4
            RequestWindowSize = min(BaseRequestWindow, RequestCount)
            RankedRequestIndices = sorted(range(RequestCount), key=lambda RequestIndex: (RunState.RequestMandatoryConflictCount(Signal, RequestIndex), (RequestIndex - PassIndex * RequestWindowSize - SignalIndex) % RequestCount, RequestIndex))
            AttemptCount = min(RequestWindowSize, RequestCount)
            AttemptedRequestIndices = tuple(RankedRequestIndices[:AttemptCount])
            if PassIndex == 0:
                RunState.PreparePassZeroDetailedSearchBatch(Signal, AttemptedRequestIndices, len(SignalsToRoute) - SignalIndex)
            for RequestIndex in AttemptedRequestIndices:
                Candidate = RunState.RouteRequest(Signal, RequestIndex, SignalNodeCosts)
                if Candidate is None:
                    continue
                if PassIndex == 0:
                    RunState.InitialCandidateOptions[Signal][Candidate.CandidateId] = Candidate
                PairConflicts = ConsiderCandidate(Candidate)
                if PairConflicts == 0 and (PassIndex > 0 or RunState.IsPartialSeedCompletion):
                    break
            if Best is None:
                SearchLimitedRequestIndices = tuple((RequestIndex for RequestIndex in AttemptedRequestIndices if any((Diagnostic['RequestIndex'] == RequestIndex and Diagnostic['Iteration'] == PassIndex and (Diagnostic['NoPathReason'] == 'SearchLimitReached') for Diagnostic in RunState.NativeSearchDiagnosticsBySignal[Signal]))))
                if SearchLimitedRequestIndices:
                    RunState.SearchExpansionEscalations[Signal] = RunState.Policy.DetailedRouting.StrictBaseExpansions
                    for SearchLimitedRequestIndex in SearchLimitedRequestIndices:
                        Candidate = RunState.RouteRequest(Signal, SearchLimitedRequestIndex, SignalNodeCosts, MinimumExpansionCount=RunState.Policy.DetailedRouting.StrictBaseExpansions)
                        if Candidate is None:
                            continue
                        if PassIndex == 0:
                            RunState.InitialCandidateOptions[Signal][Candidate.CandidateId] = Candidate
                        PairConflicts = ConsiderCandidate(Candidate)
                        if PairConflicts == 0 and PassIndex > 0:
                            break
                MaterializationRejected = any((Diagnostic['RequestIndex'] in AttemptedRequestIndices and Diagnostic['Status'] == 'Routed' and (dict(Diagnostic.get('Materialization', {})).get('Status') not in (None, 'accepted')) for Diagnostic in RunState.NativeSearchDiagnosticsBySignal[Signal]))
                if MaterializationRejected and Best is None:
                    for RetryRequestIndex in RankedRequestIndices[AttemptCount:]:
                        Candidate = RunState.RouteRequest(Signal, RetryRequestIndex, SignalNodeCosts)
                        if Candidate is None:
                            continue
                        if PassIndex == 0:
                            RunState.InitialCandidateOptions[Signal][Candidate.CandidateId] = Candidate
                        PairConflicts = ConsiderCandidate(Candidate)
                        if PairConflicts == 0 and PassIndex > 0:
                            break
                NativeRepeaterCut = any((Diagnostic['RequestIndex'] in AttemptedRequestIndices and Diagnostic['Status'] == 'NoPath' and (Diagnostic['NoPathReason'] == 'NoRepeater') for Diagnostic in RunState.NativeSearchDiagnosticsBySignal[Signal]))
                if NativeRepeaterCut and Best is None:
                    for RetryRequestIndex in RankedRequestIndices[AttemptCount:]:
                        Candidate = RunState.RouteRequest(Signal, RetryRequestIndex, SignalNodeCosts, MinimumExpansionCount=RunState.Policy.DetailedRouting.StrictBaseExpansions)
                        if Candidate is None:
                            continue
                        if PassIndex == 0:
                            RunState.InitialCandidateOptions[Signal][Candidate.CandidateId] = Candidate
                        PairConflicts = ConsiderCandidate(Candidate)
                        if PairConflicts == 0 and PassIndex > 0:
                            break
                if Best is not None:
                    if PassIndex == 0:
                        RunState.InitialCandidateOptions[Signal][Best.CandidateId] = Best
                    CommitSelectedCandidate(Signal, Best)
                    continue
                Expanded = False
                LastRequest = RunState.RouteRequestDiagnostics.get(Signal, {})
                FailedTouches = {}
                if isinstance(LastRequest.get('BoundaryFrontierTouches'), dict):
                    FailedTouches = {Key: tuple((tuple(Value) for Value in ListValue)) for Key, ListValue in LastRequest['BoundaryFrontierTouches'].items() if isinstance(ListValue, list)}
                if not FailedTouches:
                    FailedTouches = RunServices.FindNegotiatedBoundaryTouches(RunState.RegionStates[Signal].BoundaryTouches, RunState.RegionStates[Signal].ActiveTiles, RunState.RegionStates[Signal].Bounds, RunState.RegionStates[Signal].TileSize)
                CanExpandSparseRegion = LastRequest.get('Status') == 'NoPath' and (LastRequest.get('NoPathReason') == 'SearchLimitReached' or any((Value for Value in FailedTouches.values())))
                if CanExpandSparseRegion:
                    FailureCause = 'failed-search-frontier'
                    if LastRequest.get('NoPathReason') == 'NoPathContinuation':
                        FailureCause = 'cheapest-continuation-leaves-region'
                    if any((Value for Value in FailedTouches.values())):
                        FailureCause = 'route-tree-boundary-frontier'
                    for Side in RunState.PreferredExpansionSides(Signal, Existing):
                        if RunState.ExpandSignalRegion(Signal, Side, FailureCause, FailedTouches.get(Side, ())):
                            Expanded = True
                            break
                if Expanded:
                    ExpandedRequestIndices = RankedRequestIndices[AttemptCount:AttemptCount * 2]
                    if not ExpandedRequestIndices:
                        ExpandedRequestIndices = RankedRequestIndices[:AttemptCount]
                    ExpandedMinimumExpansionCount = RunServices.SelectNegotiatedExpandedRequestMinimumExpansionCount(bool(SearchLimitedRequestIndices), NativeRepeaterCut, RunState.Policy.DetailedRouting.StrictBaseExpansions)
                    for ExpandedRequestIndex in ExpandedRequestIndices:
                        Best = RunState.RouteRequest(Signal, ExpandedRequestIndex, SignalNodeCosts, MinimumExpansionCount=ExpandedMinimumExpansionCount)
                        if Best is not None:
                            break
                if Best is not None:
                    RunServices.RetainNegotiatedInitialCandidateOption(RunState.InitialCandidateOptions, Signal, Best, PassIndex)
                    CommitSelectedCandidate(Signal, Best)
                    continue
                if Existing is not None and Signal not in RunState.RegenerateSignals:
                    CommitSelectedCandidate(Signal, Existing)
                    continue
                FailureSignals = set(RunState.CumulativeConflictSignals)
                FailureSignals.add(Signal)
                Rejections = RunState.RejectionCountsBySignal[Signal]
                FailureReason = RunServices.RoutingFailureReason.NoPinAccessPattern if RequestCount == 0 or Rejections.get('MandatorySelfClaimConflict', 0) > 0 else RunServices.RoutingFailureReason.RepeaterAccessInfeasible if Rejections.get('NoRepeater', 0) > 0 else RunServices.RoutingFailureReason.GlobalCongestionUnresolved
                MandatoryConflicts = RunState.MandatorySelfConflictsBySignal[Signal]
                raise RunServices.RoutingStageError(RunServices.RoutingFailure(Reason=FailureReason, Stage='NegotiatedDetailedRouting', AffectedNets=tuple(sorted(FailureSignals)), Locations=tuple(sorted({Resource.Position for Resource in MandatoryConflicts}))[:32], Resources=tuple(sorted((str(Resource) for Resource in MandatoryConflicts)))[:32], RepairActions=('RelocateProducerConsumerClusters', 'ExpandOffenderHalo'), Detail='mandatory source/target access geometry conflicts with its own wire, support, or headroom claims' if MandatoryConflicts else 'no legal portal-aware route tree was found in the bounded negotiated sparse region', Diagnostics={'RequestCount': RequestCount, 'AttemptedRequestCount': AttemptCount, 'Iteration': PassIndex, 'Rejections': dict(sorted(Rejections.items())), 'InitialDetailedBatch': dict(RunState.InitialDetailedBatchDiagnostics), 'SearchExpansionEscalations': dict(sorted(RunState.SearchExpansionEscalations.items())), 'CachedNodeCount': RunState.Resources.ResourceGraph.CachedNodeCount, 'Region': {'HaloSize': RunState.TileSize, 'ActiveTiles': [list(Value) for Value in sorted(RunState.RegionStates[Signal].ActiveTiles)], 'BoundaryTouches': [list(Value) for Value in sorted(RunState.RegionStates[Signal].BoundaryTouches)], 'ExpandedSides': list(RunState.RegionStates[Signal].ExpandedSides), 'ExpansionEvents': list(RunState.RegionStates[Signal].ExpansionEvents), 'NativeSearch': list(RunState.NativeSearchDiagnosticsBySignal[Signal])}, 'ConflictGraph': {'Classification': 'mandatory-access-self-conflict' if MandatoryConflicts else 'sparse-region-route-cut', 'ConflictSignals': sorted(FailureSignals), 'RelocationSignals': sorted(FailureSignals), 'RequestSignals': {'Signal': Signal, 'RequestCount': RequestCount, 'AttemptedRequestCount': AttemptCount, 'FailedSignalCount': len(FailureSignals), 'RequestlessSignals': sorted((CandidateSignal for CandidateSignal in SignalsToRoute if not RunState.RouteRequestsBySignal.get(CandidateSignal)))}}}))
            CommitSelectedCandidate(Signal, Best)
            BoundaryTouches = RunServices.FindNegotiatedBoundaryTouches(Best.Nodes, RunState.RegionStates[Signal].ActiveTiles, RunState.RegionStates[Signal].Bounds, RunState.RegionStates[Signal].TileSize)
            for Values in BoundaryTouches.values():
                RunState.RegionStates[Signal].BoundaryTouches.update(Values)
            if Existing is not None and Existing.CandidateId != Best.CandidateId:
                RunState.ReroutedSignals.add(Signal)
        if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            print(f'[debug] authoritative: negotiated signal routing iteration={PassIndex + 1} signals={len(SignalsToRoute)} elapsed={RunServices.monotonic() - PassStarted:.3f}s', flush=True)
        FinalConflicts = RunServices.FindClaimConflicts({Signal: Candidate.Claims for Signal, Candidate in RunState.Selected.items()})
        if PassIndex == 0 and FinalConflicts and (not RunState.CompleteSeedDomain):
            RunState.InitialAssignmentDiagnostics.clear()
            InitialAssignmentStarted = RunServices.monotonic()
            InitialAssignment = RunState.TryInitialCandidateAssignment()
            if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                print(f'[debug] authoritative: initial candidate assignment result={RunState.InitialAssignmentDiagnostics.get('Result')} expansions={RunState.InitialAssignmentDiagnostics.get('ExpansionCount')} elapsed={RunServices.monotonic() - InitialAssignmentStarted:.3f}s', flush=True)
            if InitialAssignment is None:
                RunState.RaiseIfUnavoidableMandatoryAssignmentCut()
            if InitialAssignment is None and RunState.RequestHigherLayerOnExactCut and (RunState.InitialAssignmentDiagnostics.get('Result') in {'incomplete-candidate-domain', 'no-assignment'}):
                AffectedSignals = tuple(sorted({*(str(Signal) for Signal in RunState.InitialAssignmentDiagnostics.get('ConflictSignals', ())), *(str(Signal) for Signal in RunState.InitialAssignmentDiagnostics.get('MissingSignals', ())), *((str(RunState.InitialAssignmentDiagnostics['FailureNet']),) if RunState.InitialAssignmentDiagnostics.get('FailureNet') else ())}))
                raise RunServices.RoutingStageError(RunServices.RoutingFailure(Reason=RunServices.RoutingFailureReason.TrackAssignmentConflict, Stage='InitialCandidateAssignment', AffectedNets=AffectedSignals, RepairActions=('AddRoutingLayer',), Detail='the bounded candidate pool has no capacity-one assignment at the current routing-layer ceiling', Diagnostics={'InitialAssignment': dict(RunState.InitialAssignmentDiagnostics)}))
            AdaptiveCompletionAttempts: set[tuple[str, int, str]] = set()
            AdaptiveCompletionQuickDiscoveryCuts: set[tuple[str, ...]] = set()
            AdaptiveCompletionDuplicateCounts: RunServices.Counter[tuple[str, int]] = RunServices.Counter()

            def ExactAssignmentCompletionCutKey() -> tuple[str, ...]:
                return tuple(sorted({*(str(Value) for Value in RunState.InitialAssignmentDiagnostics.get('ConflictSignals', ())), *(str(Value) for Value in RunState.InitialAssignmentDiagnostics.get('MissingSignals', ())), *((str(RunState.InitialAssignmentDiagnostics['FailureNet']),) if RunState.InitialAssignmentDiagnostics.get('FailureNet') else ())}))
            ExactAssignmentCompletionCutKey = ExactAssignmentCompletionCutKey

            def PendingAdaptiveRequestIndices(CandidateSignal: str, AttemptTier: str) -> set[int]:
                return RunServices.SelectPendingExactAssignmentCompletionRequestIndices(CandidateSignal, len(RunState.RouteRequestsBySignal.get(CandidateSignal, ())), AttemptTier, AdaptiveCompletionAttempts)
            PendingAdaptiveRequestIndices = PendingAdaptiveRequestIndices
            try:
                AdaptiveCompletionBatchSize = max(1, min(8, int(RunServices.GetRustRoutingThreadCount())))
            except Exception:
                AdaptiveCompletionBatchSize = 1
            CompletionRoundLimit = max(1, min(4 if RunState.AdvancePlacementOnExhaustedExactCut or (RunState.TerminalCount <= 256 and RunState.HasValidatedLocalClaims) else 2, RunState.Policy.TrackAssignment.ReassignmentLimit * RunState.Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations, sum((RunServices.ceil(len(Requests) / AdaptiveCompletionBatchSize) for Requests in RunState.RouteRequestsBySignal.values() if Requests))))
            CompletionNegotiationReserveMilliseconds = RunServices.SelectExactAssignmentCompletionReserveMilliseconds(RunState.AdvancePlacementOnExhaustedExactCut, RunState.TerminalCount, RunState.HasValidatedLocalClaims, RunState.Policy.AdaptiveRouting.MaximumRuntimeSeconds)
            CompletionCutKeys: list[tuple[str, ...]] = []
            for CompletionRound in range(1, CompletionRoundLimit + 2):
                if InitialAssignment is not None:
                    break
                if CompletionRound > CompletionRoundLimit and (not RunServices.ShouldContinueDistinctExactCutFrontier(RunState.AdvancePlacementOnExhaustedExactCut, tuple(CompletionCutKeys), CompletionRoundLimit)):
                    break
                CompletionRuntimeMilliseconds = RunServices.RemainingRoutingRuntimeMilliseconds(RunState.Deadline, RunState.AdaptiveExpiresAt) - CompletionNegotiationReserveMilliseconds
                if CompletionRuntimeMilliseconds <= 0:
                    break
                ConflictFrequency: RunServices.Counter[str] = RunServices.Counter()
                ConflictFrequency.update((str(ConflictSignal) for ConflictSignal in RunState.InitialAssignmentDiagnostics.get('ConflictSignals', ())))
                MissingSignals = tuple((str(MissingSignal) for MissingSignal in RunState.InitialAssignmentDiagnostics.get('MissingSignals', ())))
                ConflictFrequency.update({MissingSignal: max(1, len(RunState.SignalOrder)) for MissingSignal in MissingSignals})
                BlockedFrequency: RunServices.Counter[str] = RunServices.Counter()
                for DeadEnd in RunState.InitialAssignmentDiagnostics.get('DeadEnds', ()):
                    if not isinstance(DeadEnd, dict):
                        continue
                    BlockedSignal = DeadEnd.get('BlockedSignal')
                    if BlockedSignal is not None:
                        BlockedFrequency[str(BlockedSignal)] += 1
                    BlockedCandidates = DeadEnd.get('BlockedCandidates', ())
                    if not isinstance(BlockedCandidates, list | tuple):
                        continue
                    for CandidateDiagnostic in BlockedCandidates:
                        if not isinstance(CandidateDiagnostic, dict):
                            continue
                        Conflicts = CandidateDiagnostic.get('Conflicts', {})
                        if isinstance(Conflicts, dict):
                            ConflictFrequency.update((str(ConflictSignal) for ConflictSignal in Conflicts))
                CompletionCutKey = ExactAssignmentCompletionCutKey()
                QuickDiscoveryEnabled = CompletionCutKey not in AdaptiveCompletionQuickDiscoveryCuts
                CompletionBatchMode = ('quick-discovery' if QuickDiscoveryEnabled else 'strict-proof') if RunState.AdvancePlacementOnExhaustedExactCut else 'bounded-discovery'
                RankedAdaptiveSignals = sorted((CandidateSignal for CandidateSignal in CompletionCutKey if RunState.RouteRequestsBySignal.get(CandidateSignal)), key=lambda CandidateSignal: (RunServices.ExactAssignmentCompletionSignalOrderKey(CandidateSignal, frozenset(MissingSignals), sum((1 for AttemptSignal, _RequestIndex, _AttemptTier in AdaptiveCompletionAttempts if AttemptSignal == CandidateSignal)), ConflictFrequency[CandidateSignal], BlockedFrequency[CandidateSignal], len(RunState.InitialCandidateOptions.get(CandidateSignal, {}))),))
                if not RankedAdaptiveSignals:
                    break
                RetryRequestLimits: dict[tuple[str, int], tuple[int, int]] = {}
                for Signal in RankedAdaptiveSignals:
                    PendingRequestIndices = PendingAdaptiveRequestIndices(Signal, CompletionBatchMode)
                    for Diagnostic in RunState.NativeSearchDiagnosticsBySignal[Signal]:
                        IsSearchLimited = Diagnostic.get('Status') == 'NoPath' and Diagnostic.get('NoPathReason') == 'SearchLimitReached'
                        IsBudgetExpired = Diagnostic.get('Status') == 'BudgetExpired'
                        if not (IsSearchLimited or IsBudgetExpired):
                            continue
                        RequestIndex = int(Diagnostic['RequestIndex'])
                        if RequestIndex not in PendingRequestIndices:
                            continue
                        ExpansionLimit = max(240000, RunState.Policy.DetailedRouting.RepairMaximumExpansions)
                        RequestKey = (Signal, RequestIndex)
                        ExistingLimits = RetryRequestLimits.get(RequestKey, (0, 0))
                        RetryRequestLimits[RequestKey] = (max(ExistingLimits[0], ExpansionLimit), max(ExistingLimits[1], 4000))
                    for RequestIndex in PendingRequestIndices:
                        RetryRequestLimits.setdefault((Signal, RequestIndex), (max(240000, RunState.Policy.DetailedRouting.RepairMaximumExpansions), 4000))

                def AdaptiveRequestDomainConflictScore(CandidateSignal: str, RequestIndex: int) -> tuple[int, int, int]:
                    MandatoryClaims = RunState.RequestMandatoryClaims(CandidateSignal, RequestIndex)
                    BlockedDomainCount = 0
                    MinimumConflictTotal = 0
                    for OtherSignal, CandidateValues in RunState.InitialCandidateOptions.items():
                        if OtherSignal == CandidateSignal or not CandidateValues:
                            continue
                        MinimumConflictCount = min((RunState.ClaimConflictCount(MandatoryClaims, Candidate.Claims) for Candidate in CandidateValues.values()))
                        BlockedDomainCount += int(MinimumConflictCount > 0)
                        MinimumConflictTotal += MinimumConflictCount
                    return (BlockedDomainCount, MinimumConflictTotal, RequestIndex)
                AdaptiveRequestDomainConflictScore = AdaptiveRequestDomainConflictScore
                DomainConflictScoresByRequest: dict[tuple[str, int], tuple[int, int, int]] = {RequestKey: AdaptiveRequestDomainConflictScore(*RequestKey) for RequestKey in RetryRequestLimits}
                RankedRequestIndicesBySignal: dict[str, tuple[int, ...]] = {}
                for Signal in RankedAdaptiveSignals:
                    RankedRetryRequestPool = sorted((RequestIndex for CandidateSignal, RequestIndex in RetryRequestLimits if CandidateSignal == Signal), key=lambda RequestIndex: (AdaptiveCompletionDuplicateCounts[Signal, RequestIndex], *DomainConflictScoresByRequest[Signal, RequestIndex]))
                    if not RankedRetryRequestPool:
                        continue
                    RankedRetryRequestValues, SignalBatchMode = RunServices.SelectExactAssignmentCompletionRequestBatch(RankedRetryRequestPool, {RequestIndex: DomainConflictScoresByRequest[Signal, RequestIndex] for RequestIndex in RankedRetryRequestPool}, len(RankedRetryRequestPool), RunState.AdvancePlacementOnExhaustedExactCut, QuickDiscoveryEnabled)
                    if SignalBatchMode != CompletionBatchMode:
                        raise ValueError('exact completion selected mixed runtime tiers')
                    RankedRequestIndicesBySignal[Signal] = RankedRetryRequestValues
                RankedRetryRequests = list(RunServices.SelectExactAssignmentCompletionCutWideRequests(RankedAdaptiveSignals, RankedRequestIndicesBySignal, AdaptiveCompletionBatchSize, {Signal: len(RunState.InitialCandidateOptions.get(Signal, {})) for Signal in RankedAdaptiveSignals}))
                if not RankedRetryRequests:
                    break
                CompletionCutKeys.append(CompletionCutKey)
                if CompletionBatchMode == 'quick-discovery':
                    AdaptiveCompletionQuickDiscoveryCuts.add(CompletionCutKey)
                EffectiveRetryLimits: dict[tuple[str, int], tuple[tuple[int, int, int], int, int]] = {}
                for RequestKey in RankedRetryRequests:
                    DomainConflictScore = DomainConflictScoresByRequest[RequestKey]
                    ExpansionLimit, RuntimeMilliseconds = RetryRequestLimits[RequestKey]
                    if DomainConflictScore[0] == 0 and RunState.AdvancePlacementOnExhaustedExactCut and (CompletionBatchMode == 'strict-proof'):
                        ExpansionLimit = min(RunState.Policy.AdaptiveRouting.MaximumCandidateGenerationExpansions, max(1000000, ExpansionLimit))
                        RuntimeMilliseconds = max(16000, RuntimeMilliseconds)
                    EffectiveRetryLimits[RequestKey] = (DomainConflictScore, ExpansionLimit, RuntimeMilliseconds)
                CompletionNodeCostsByRequest = {(Signal, RequestIndex): RunState.ExactAssignmentCompletionNodeCosts(Signal, RequestIndex) for Signal, RequestIndex in RankedRetryRequests}
                CompletionBatchSignalRequestCounts = RunServices.Counter((Signal for Signal, _RequestIndex in RankedRetryRequests))
                AdaptiveBatchRuntimeMilliseconds = 0
                if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                    print(f'[debug] authoritative: exact candidate completion round={CompletionRound} signals={dict(CompletionBatchSignalRequestCounts)} pending={len(RetryRequestLimits)} selected={len(RankedRetryRequests)} mode={CompletionBatchMode}', flush=True)
                if RankedRetryRequests and hasattr(RunState.Context, 'GenerateRouteTreeDetailedBatchBounded'):
                    AdaptiveBatchRequests = [(Signal, RequestIndex, Request) for Signal, RequestIndex in RankedRetryRequests if (Request := RunState.BuildPassZeroDetailedSearchRequest(Signal, RequestIndex, EffectiveRetryLimits[Signal, RequestIndex][1], CompletionNodeCostsByRequest[Signal, RequestIndex])) is not None]
                    if AdaptiveBatchRequests:
                        AdaptiveBatchStarted = RunServices.monotonic()
                        AdaptiveBatchRuntimeMilliseconds = min(max((EffectiveRetryLimits[Signal, RequestIndex][2] for Signal, RequestIndex, _Request in AdaptiveBatchRequests)), CompletionRuntimeMilliseconds)
                        AdaptiveBatchResult = RunState.Context.GenerateRouteTreeDetailedBatchBounded([Request for _Signal, _RequestIndex, Request in AdaptiveBatchRequests], AdaptiveBatchRuntimeMilliseconds)
                        AdaptiveBatchSearchResults = list(AdaptiveBatchResult.SearchResults)
                        if len(AdaptiveBatchSearchResults) != len(AdaptiveBatchRequests):
                            raise ValueError('adaptive detailed route-tree batch returned an unexpected result count')
                        for (Signal, RequestIndex, _Request), SearchResult in zip(AdaptiveBatchRequests, AdaptiveBatchSearchResults):
                            RunState.InitialDetailedBatchResults[Signal, RequestIndex] = SearchResult
                        if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                            print(f'[debug] authoritative: exact candidate batch round={CompletionRound} signals={dict(CompletionBatchSignalRequestCounts)} requests={len(AdaptiveBatchRequests)} budget_ms={AdaptiveBatchRuntimeMilliseconds} elapsed={RunServices.monotonic() - AdaptiveBatchStarted:.3f}s completed={AdaptiveBatchResult.CompletedWork} expired={AdaptiveBatchResult.DeadlineExceeded}', flush=True)
                for Signal, RequestIndex in RankedRetryRequests:
                    RequestKey = (Signal, RequestIndex)
                    DomainConflictScore, ExpansionLimit, RuntimeMilliseconds = EffectiveRetryLimits[RequestKey]
                    AdaptiveCompletionAttempts.add((Signal, RequestIndex, CompletionBatchMode))
                    Candidate = RunState.RouteRequest(Signal, RequestIndex, CompletionNodeCostsByRequest[RequestKey], MinimumExpansionCount=ExpansionLimit, MaximumRuntimeMilliseconds=RuntimeMilliseconds, MaximumExpansionCountOverride=ExpansionLimit)
                    IsNewCandidate = bool(Candidate is not None and Candidate.CandidateId not in RunState.InitialCandidateOptions[Signal])
                    if Candidate is not None and (not IsNewCandidate):
                        AdaptiveCompletionDuplicateCounts[Signal, RequestIndex] += 1
                    RunState.ExactAssignmentCandidateRetries.append({'Signal': Signal, 'RequestIndex': RequestIndex, 'CompletionRound': CompletionRound, 'ProgressContinuation': CompletionRound > CompletionRoundLimit, 'CompletionBatchMode': CompletionBatchMode, 'CompletionBatchSignals': dict(CompletionBatchSignalRequestCounts), 'SharedBatchRuntimeMilliseconds': AdaptiveBatchRuntimeMilliseconds, 'CompletionTrigger': {'Reason': 'exact-assignment-dead-end', 'CutKey': list(CompletionCutKey), 'ConflictFrequency': ConflictFrequency[Signal], 'BlockedFrequency': BlockedFrequency[Signal]}, 'MaximumRuntimeMilliseconds': RuntimeMilliseconds, 'MaximumExpansionCount': ExpansionLimit, 'DomainConflictScore': list(DomainConflictScore), 'Result': 'accepted' if IsNewCandidate else 'duplicate' if Candidate is not None else 'rejected', 'Search': dict(RunState.RouteRequestDiagnostics.get(Signal, {}))})
                    if IsNewCandidate:
                        RunState.InitialCandidateOptions[Signal][Candidate.CandidateId] = Candidate
                RunState.InitialAssignmentDiagnostics.clear()
                CompletionAssignmentStarted = RunServices.monotonic()
                InitialAssignment = RunState.TryInitialCandidateAssignment()
                if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                    print(f'[debug] authoritative: completion candidate assignment round={CompletionRound} result={RunState.InitialAssignmentDiagnostics.get('Result')} expansions={RunState.InitialAssignmentDiagnostics.get('ExpansionCount')} elapsed={RunServices.monotonic() - CompletionAssignmentStarted:.3f}s', flush=True)
                if InitialAssignment is None:
                    RunState.RaiseIfUnavoidableMandatoryAssignmentCut()
            if InitialAssignment is None and RunState.ExactAssignmentCandidateRetries and (not RunState.RequestHigherLayerOnExactCut) and RunState.AdvancePlacementOnExhaustedExactCut:
                MissingCompletionSignals = tuple(sorted((str(Signal) for Signal in RunState.InitialAssignmentDiagnostics.get('MissingSignals', ()))))
                AffectedSignals = tuple(sorted({*MissingCompletionSignals, *(str(Signal) for Signal in RunState.InitialAssignmentDiagnostics.get('ConflictSignals', ())), *((str(RunState.InitialAssignmentDiagnostics['FailureNet']),) if RunState.InitialAssignmentDiagnostics.get('FailureNet') else ())}))
                raise RunServices.RoutingStageError(RunServices.RoutingFailure(Reason=RunServices.RoutingFailureReason.TrackAssignmentConflict, Stage='InitialCandidateAssignment', AffectedNets=AffectedSignals, RepairActions=('RelocateAffectedClusters',), Detail='targeted exact-cut candidate completion exhausted at the maximum routing-layer ceiling', Diagnostics={'InitialAssignment': dict(RunState.InitialAssignmentDiagnostics), 'ExactAssignmentCandidateRetries': list(RunState.ExactAssignmentCandidateRetries), 'ConflictGraph': {'Classification': 'candidate-starvation-placement-conflict' if MissingCompletionSignals else 'higher-order-placement-conflict', 'ConflictSignals': list(AffectedSignals), 'RelocationSignals': list(AffectedSignals), 'NoCandidateSignals': list(MissingCompletionSignals), 'CandidateCounts': dict(RunState.InitialAssignmentDiagnostics.get('CandidateCounts', {}))}}))
            if InitialAssignment is not None:
                EnvelopeAssignment = RunState.TryInitialCandidateAssignment(OptimizeEnvelope=True)
                if EnvelopeAssignment is not None:
                    BaselineQuality = RunState.EnvelopeQuality(InitialAssignment.values())
                    EnvelopeCandidateQuality = RunState.EnvelopeQuality(EnvelopeAssignment.values())
                    RunState.InitialAssignmentDiagnostics['EnvelopeSelection'] = {'Baseline': list(BaselineQuality), 'Candidate': list(EnvelopeCandidateQuality), 'Selected': 'envelope' if EnvelopeCandidateQuality < BaselineQuality else 'baseline'}
                    if EnvelopeCandidateQuality < BaselineQuality:
                        InitialAssignment = EnvelopeAssignment
                RunState.Selected = InitialAssignment
                FinalConflicts = RunServices.FindClaimConflicts({Signal: Candidate.Claims for Signal, Candidate in RunState.Selected.items()})
            else:
                CandidateCounts = RunState.InitialAssignmentDiagnostics.get('CandidateCounts', {})
                if RunState.InitialAssignmentDiagnostics.get('Native') and (not RunState.InitialAssignmentDiagnostics.get('BudgetExhausted', False)) and (not RunState.InitialAssignmentDiagnostics.get('DeadlineExceeded', False)):
                    RunState.ExactAssignmentCutSignals.update((str(Signal) for Signal in RunState.InitialAssignmentDiagnostics.get('ConflictSignals', ())))
                    if RunState.InitialAssignmentDiagnostics.get('FailureNet'):
                        RunState.ExactAssignmentCutSignals.add(str(RunState.InitialAssignmentDiagnostics['FailureNet']))
                for DeadEnd in RunState.InitialAssignmentDiagnostics.get('DeadEnds', ()):
                    if not isinstance(DeadEnd, dict):
                        continue
                    AssignedCandidates = DeadEnd.get('AssignedCandidates', {})
                    SingletonAssigned = {str(Signal) for Signal in (AssignedCandidates if isinstance(AssignedCandidates, dict) else {}) if int(CandidateCounts.get(Signal, 0) if isinstance(CandidateCounts, dict) else 0) == 1}
                    BlockedCandidates = DeadEnd.get('BlockedCandidates', ())
                    if SingletonAssigned and isinstance(BlockedCandidates, list | tuple) and BlockedCandidates and all((bool(SingletonAssigned & set(Candidate.get('Conflicts', {}) if isinstance(Candidate, dict) and isinstance(Candidate.get('Conflicts', {}), dict) else {})) for Candidate in BlockedCandidates)):
                        RunState.ExactAssignmentCutSignals.update(SingletonAssigned)
                        BlockedSignal = DeadEnd.get('BlockedSignal')
                        if BlockedSignal is not None:
                            RunState.ExactAssignmentCutSignals.add(str(BlockedSignal))
        if RunServices.ShouldRetryNegotiatedExactAssignment(PassIndex, bool(FinalConflicts), RunState.CompleteSeedDomain, DiscoveredCandidateThisPass) and (RunState.TerminalCount <= 256 and RunState.HasValidatedLocalClaims or PassIndex + 1 >= RunState.Negotiated.MaximumIterations or RunState.StagnationCount >= max(0, RunState.Negotiated.StagnationPassLimit - 2)):
            RunState.InitialAssignmentDiagnostics.clear()
            AssignmentStarted = RunServices.monotonic()
            NegotiatedAssignment = RunState.TryInitialCandidateAssignment()
            AttemptDiagnostics = {'Iteration': PassIndex + 1, 'ElapsedSeconds': round(RunServices.monotonic() - AssignmentStarted, 6), 'CandidateCount': sum((len(Values) for Values in RunState.InitialCandidateOptions.values())), 'CandidateCounts': {Signal: len(Values) for Signal, Values in sorted(RunState.InitialCandidateOptions.items())}, 'Assignment': dict(RunState.InitialAssignmentDiagnostics)}
            if NegotiatedAssignment is not None:
                NegotiatedConflicts = RunServices.FindClaimConflicts({Signal: Candidate.Claims for Signal, Candidate in NegotiatedAssignment.items()})
                AttemptDiagnostics['ResidualConflictCount'] = len(NegotiatedConflicts)
                if not NegotiatedConflicts:
                    RunState.Selected = NegotiatedAssignment
                    FinalConflicts = {}
                    AttemptDiagnostics['Result'] = 'assigned'
                else:
                    AttemptDiagnostics['Result'] = 'assigned-with-conflicts'
            else:
                AttemptDiagnostics['Result'] = 'no-assignment'
            RunState.NegotiatedAssignmentAttempts.append(AttemptDiagnostics)
            if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                print(f'[debug] authoritative: retained-domain assignment iteration={PassIndex + 1} candidates={AttemptDiagnostics['CandidateCount']} result={AttemptDiagnostics['Result']} elapsed={AttemptDiagnostics['ElapsedSeconds']:.3f}s', flush=True)
        ConflictSignalCounts = RunServices.Counter((Signal for Signals in FinalConflicts.values() for Signal in Signals))
        RunState.ConflictSignals = tuple(sorted(ConflictSignalCounts, key=lambda Signal: (-ConflictSignalCounts[Signal], Signal)))
        RunState.CumulativeConflictSignals.update(RunState.ConflictSignals)
        ConflictCount = len(FinalConflicts)
        RunState.OverflowProgression.append(ConflictCount)
        if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            print(f'[debug] authoritative: negotiated overflow iteration={PassIndex + 1} conflicts={ConflictCount} signals={len(RunState.ConflictSignals)} elapsed={RunServices.monotonic() - PassStarted:.3f}s', flush=True)
        RunState.Iterations.append(RunServices.RoutingIterationMetrics(Iteration=PassIndex + 1, Stage='Negotiated detailed routing', ConflictCount=ConflictCount, ReroutedNets=len(SignalsToRoute) if PassIndex else 0, AverageLength=sum((Value.Length for Value in RunState.Selected.values())) / max(1, len(RunState.Selected)), BendCount=sum((Value.BendCount for Value in RunState.Selected.values())), ViaCount=sum((Value.ViaCount for Value in RunState.Selected.values())), ConflictSignals=RunState.ConflictSignals))
        if not FinalConflicts and len(RunState.Selected) == len(RunState.Profiles):
            if RunState.Policy.TrackAssignment.MinimizeMaximumRoutingLayer:
                LayerOptimizedAssignment = RunState.TryInitialCandidateAssignment()
                if LayerOptimizedAssignment is not None:
                    LayerOptimizedConflicts = RunServices.FindClaimConflicts({Signal: Candidate.Claims for Signal, Candidate in LayerOptimizedAssignment.items()})
                    if not LayerOptimizedConflicts:
                        RunState.Selected = LayerOptimizedAssignment
                        RunState.InitialAssignmentDiagnostics['FinalLayerOptimization'] = {'Applied': True, 'MaximumLayer': max((Candidate.Layer for Candidate in RunState.Selected.values()))}
                    else:
                        RunState.InitialAssignmentDiagnostics['FinalLayerOptimization'] = {'Applied': False, 'Reason': 'claim-conflict'}
            return PhaseOutcome(Returned=True, Value=RunServices.NegotiatedRoutePlan(SelectedCandidates=RunState.Selected, Iterations=tuple(RunState.Iterations), ReroutedSignals=tuple(sorted(RunState.ReroutedSignals)), OverflowProgression=tuple(RunState.OverflowProgression), CachedNodeCount=RunState.Resources.ResourceGraph.CachedNodeCount, CachedEdgeCount=RunState.Resources.ResourceGraph.CachedEdgeCount, Diagnostics={'HaloSize': RunState.TileSize, 'Regions': {Signal: {'ActiveTiles': [list(Value) for Value in sorted(State.ActiveTiles)], 'BoundaryTouches': [list(Value) for Value in sorted(State.BoundaryTouches)], 'ExpandedSides': list(State.ExpandedSides), 'ExpansionEvents': list(State.ExpansionEvents), 'OwnedNodeCount': len(State.AddedNodes), 'OwnedEdgeCount': len(State.AddedEdges)} for Signal, State in sorted(RunState.RegionStates.items())}, 'BranchRepairs': RunState.BranchRepairEvents, 'InitialAssignment': dict(RunState.InitialAssignmentDiagnostics), 'InitialCandidateLayers': {Signal: sorted({Candidate.Layer for Candidate in Values.values()}) for Signal, Values in sorted(RunState.InitialCandidateOptions.items())}, 'InitialDetailedBatch': dict(RunState.InitialDetailedBatchDiagnostics), 'ExactAssignmentCandidateRetries': list(RunState.ExactAssignmentCandidateRetries), 'NegotiatedAssignmentAttempts': list(RunState.NegotiatedAssignmentAttempts), 'InitialDetailedRequestWindow': RunState.InitialDetailedRequestWindow, 'CachedRepairSelections': RunState.CachedRepairSelections, 'TerminalCount': RunState.TerminalCount, 'SearchExpansionEscalations': dict(sorted(RunState.SearchExpansionEscalations.items())), 'CumulativeConflictSignals': sorted(RunState.CumulativeConflictSignals), 'RepeaterRejections': {Signal: dict(sorted(Values.items())) for Signal, Values in sorted(RunState.RejectionCountsBySignal.items())}, 'NativeSearch': {Signal: list(Values) for Signal, Values in sorted(RunState.NativeSearchDiagnosticsBySignal.items())}}))
        MandatoryClaimsBySelectedSignal: dict[str, RunServices.RoutingResourceClaims] = {}
        for Signal, Candidate in RunState.Selected.items():
            for RequestIndex, Metadata in enumerate(RunState.RouteMetadataBySignal.get(Signal, ())):
                SourcePortal, TargetPortals, *_Rest = Metadata
                if SourcePortal.PortalId != Candidate.SourcePortalId:
                    continue
                if tuple(sorted((Portal.PortalId for Portal in TargetPortals))) != tuple(sorted(Candidate.TargetPortalIds.values())):
                    continue
                MandatoryClaimsBySelectedSignal[Signal] = RunState.RequestMandatoryClaims(Signal, RequestIndex)
                break
        MandatoryCutResources = {Resource for Resource, Signals in FinalConflicts.items() if any((Resource in MandatoryClaimsBySelectedSignal.get(Signal, RunServices.RoutingResourceClaims()).ResourceIds for Signal in Signals))}
        ImmediateMandatoryPlacementCut = PassIndex == 0 and (not RunState.ExactAssignmentCutSignals) and (not 200 <= RunState.TerminalCount <= 256) and (not RunState.HasValidatedLocalClaims) and (len(MandatoryCutResources) >= max(4, (len(RunState.SignalOrder) + 3) // 4))
        if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            print(f'[debug] authoritative: mandatory placement cut gate pass={PassIndex + 1} immediate={ImmediateMandatoryPlacementCut} exact_cut={sorted(RunState.ExactAssignmentCutSignals)} terminal_count={RunState.TerminalCount} validated_local_claims={RunState.HasValidatedLocalClaims} mandatory_resources={len(MandatoryCutResources)}', flush=True)
        if FinalConflicts and MandatoryCutResources and (ImmediateMandatoryPlacementCut or (PassIndex >= 2 and RunState.BestOverflowConflictCount is not None and (ConflictCount >= RunState.BestOverflowConflictCount) and (not 200 <= RunState.TerminalCount <= 256 and (not RunState.HasValidatedLocalClaims) or RunState.StagnationCount >= max(0, RunState.Negotiated.StagnationPassLimit - 2)))):
            CutSignals = tuple(sorted({Signal for Resource, Signals in FinalConflicts.items() for Signal in Signals}))
            AffectedSignals = tuple(sorted({*CutSignals, *RunState.CumulativeConflictSignals}))
            raise RunServices.RoutingStageError(RunServices.RoutingFailure(Reason=RunServices.RoutingFailureReason.TrackAssignmentConflict, Stage='NegotiatedDetailedRouting', AffectedNets=AffectedSignals, Locations=tuple(sorted({Resource.Position for Resource in MandatoryCutResources}))[:32], Resources=tuple(sorted((str(Resource) for Resource in MandatoryCutResources)))[:32], RepairActions=('RelocateAffectedClusters',), Detail='stagnant negotiated overflow includes mandatory portal/access ownership and cannot be repaired only by region expansion', Diagnostics={'OverflowProgression': list(RunState.OverflowProgression), 'MandatoryConflictResourceCount': len(MandatoryCutResources), 'MandatoryConflictClaims': {str(Resource): list(Signals) for Resource, Signals in sorted(FinalConflicts.items(), key=lambda Value: str(Value[0])) if Resource in MandatoryCutResources}, 'InitialAssignment': dict(RunState.InitialAssignmentDiagnostics), 'InitialDetailedBatch': dict(RunState.InitialDetailedBatchDiagnostics), 'ExactAssignmentCandidateRetries': list(RunState.ExactAssignmentCandidateRetries), 'NegotiatedAssignmentAttempts': list(RunState.NegotiatedAssignmentAttempts), 'NativeSearch': {Signal: list(RunState.NativeSearchDiagnosticsBySignal.get(Signal, ())) for Signal in sorted(RunState.ExactAssignmentCutSignals)}, 'RepeaterRejections': {Signal: dict(RunState.RejectionCountsBySignal.get(Signal, {})) for Signal in sorted(RunState.ExactAssignmentCutSignals)}, 'ExactAssignmentCutRequests': {Signal: [{'RequestIndex': RequestIndex, 'Layer': int(Metadata[3]), 'Axis': str(Metadata[4]), 'Lane': int(Metadata[5]), 'Variant': int(Metadata[6]), 'SourcePortalId': Metadata[0].PortalId, 'TargetPortals': {str(Target): {'PortalId': Portal.PortalId, 'Path': [list(Position) for Position in Portal.Path]} for Target, Portal in zip(RunState.Profiles[Signal].Targets, Metadata[1])}} for RequestIndex, Metadata in enumerate(RunState.RouteMetadataBySignal.get(Signal, ()))] for Signal in sorted(RunState.ExactAssignmentCutSignals)}, 'ExactAssignmentCutCandidates': {Signal: [{'CandidateId': Candidate.CandidateId, 'SourcePortalId': Candidate.SourcePortalId, 'TargetPortalIds': {str(Target): PortalId for Target, PortalId in sorted(Candidate.TargetPortalIds.items())}} for Candidate in sorted(RunState.InitialCandidateOptions.get(Signal, {}).values(), key=lambda Value: Value.CandidateId)] for Signal in sorted(RunState.ExactAssignmentCutSignals)}, 'LocalClaimReleasePreScreen': {**dict(RunState.LocalClaimReleaseDiagnostics or {}), 'OriginalCutResources': [str(Resource) for Resource in sorted(MandatoryCutResources, key=str)], 'CandidateOnlyResidualConflictResources': [str(Resource) for Resource in sorted(MandatoryCutResources, key=str)] if not (RunState.LocalClaimReleaseDiagnostics or {}).get('ReleasedSignals') else [], 'FinalExactAssignmentResult': RunState.InitialAssignmentDiagnostics.get('Result')}, 'ConflictGraph': {'Classification': 'mandatory-boundary-capacity-cut', 'ConflictSignals': list(AffectedSignals), 'CongestionCutSignals': list(AffectedSignals), 'RelocationSignals': list(AffectedSignals), 'PriorityRelocationSignals': sorted(RunState.ExactAssignmentCutSignals)}}))
        for Resource, Signals in FinalConflicts.items():
            Increment = RunState.Negotiated.HistoryIncrement * max(1, len(Signals) - 1)
            RunState.History[Resource.Position] += Increment
            for Neighbor in RunState.Technology.NeighborPositions(Resource.Position):
                RunState.History[Neighbor] += max(1, Increment // 2)
        RepairStateStarted = RunServices.monotonic()
        RunState.RepairStates = {}
        ExpandedForConflict = False
        ShouldExpandForConflict = RunState.BestOverflowConflictCount is not None and ConflictCount >= RunState.BestOverflowConflictCount
        for Signal in RunState.ConflictSignals:
            Candidate = RunState.Selected[Signal]
            SignalConflictResources = {Resource for Resource, Signals in FinalConflicts.items() if Signal in Signals}
            RepairState = RunServices.BuildNegotiatedRouteTreeState(Candidate, SignalConflictResources)
            if not RepairState.PrunedTargets:
                continue
            RunState.RepairStates[Signal] = RepairState
            RetainedTargets = RepairState.RetainedTargets
            PrunedTargets = RepairState.PrunedTargets
            RetainedNodes = set(RepairState.RetainedNodes)
            RemovedNodes = set(Candidate.Nodes) - RetainedNodes
            RemovedEdges = {Edge for Edge in Candidate.Edges if Edge[0] in RemovedNodes or Edge[1] in RemovedNodes}
            BranchOutcomes = {str(Target): RunState.RepairBranchOutcomes.get(Signal, {}).get(str(Target), 'Unknown') for Target in PrunedTargets}
            RunState.BranchRepairEvents.append({'Iteration': PassIndex + 1, 'Signal': Signal, 'RetainedBranches': [{'Target': list(Target), 'Path': [list(Value) for Value in Path]} for Target, Path in zip(RetainedTargets, RepairState.RetainedBranchPaths)], 'PrunedBranches': [{'Target': list(Target), 'PrunedPath': [list(Value) for Value in Path], 'Outcome': BranchOutcomes.get(str(Target), 'Unknown')} for Target, Path in zip(PrunedTargets, RepairState.PrunedBranchPaths)], 'RetainedBranchCount': len(RetainedTargets), 'PrunedBranchCount': len(PrunedTargets), 'RemovedNodeCount': len(RemovedNodes), 'RemovedEdgeCount': len(RemovedEdges), 'RemovedBranchCount': len(RepairState.PrunedBranchTailClaims), 'PrunedBranchClaimCounts': [len(Claims) for Claims in RepairState.PrunedBranchTailClaims], 'RemovedNodes': [list(Value) for Value in sorted(RemovedNodes)], 'ConflictResources': [str(Value) for Value in sorted(SignalConflictResources, key=str)]})
            Touches = RunServices.FindNegotiatedBoundaryTouches(Candidate.Nodes, RunState.RegionStates[Signal].ActiveTiles, RunState.RegionStates[Signal].Bounds, RunState.RegionStates[Signal].TileSize)
            if Touches and ShouldExpandForConflict and (not ExpandedForConflict):
                for Side in RunState.PreferredExpansionSides(Signal, Candidate):
                    if Side not in Touches:
                        continue
                    SignalExpanded = RunState.ExpandSignalRegion(Signal, Side, 'route-tree-boundary-touch', Touches.get(Side, ()))
                    ExpandedForConflict = SignalExpanded or ExpandedForConflict
                    if SignalExpanded:
                        break
        if bool(RunServices.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            print(f'[debug] authoritative: negotiated repair states iteration={PassIndex + 1} states={len(RunState.RepairStates)} expanded={ExpandedForConflict} elapsed={RunServices.monotonic() - RepairStateStarted:.3f}s', flush=True)
        if RunState.BestOverflowConflictCount is not None and ConflictCount >= RunState.BestOverflowConflictCount:
            RunState.StagnationCount += 1
        else:
            RunState.StagnationCount = 0
            RunState.BestOverflowConflictCount = ConflictCount
        if RunState.StagnationCount >= RunState.Negotiated.StagnationPassLimit - 1 and (not ExpandedForConflict):
            Hotspots = tuple(sorted({Resource.Position for Resource in FinalConflicts}))
            for Signal in RunState.ConflictSignals:
                if ExpandedForConflict:
                    break
                for Side in RunState.PreferredExpansionSides(Signal, RunState.Selected[Signal], Hotspots):
                    if RunState.ExpandSignalRegion(Signal, Side, 'stagnant-overflow', Hotspots):
                        ExpandedForConflict = True
                        break
            if ExpandedForConflict:
                RunState.StagnationCount = 0
                continue
            else:
                break
    Hotspots = tuple(sorted({Resource.Position for Resource in FinalConflicts}))
    FinalAffectedSignals = tuple(sorted(set(RunState.ConflictSignals) | RunState.CumulativeConflictSignals))
    raise RunServices.RoutingStageError(RunServices.RoutingFailure(Reason=RunServices.RoutingFailureReason.DetailedCongestionUnresolved, Stage='NegotiatedDetailedRouting', AffectedNets=FinalAffectedSignals, Locations=Hotspots[:32], RepairActions=('RelocateAffectedClusters', 'ExpandCongestedCut'), Detail=f'negotiated route trees retained capacity-one conflicts after {len(RunState.Iterations)} deterministic passes', Diagnostics={'Algorithm': 'negotiated-route-trees-v1', 'ConflictGraph': {'Classification': 'detailed-congestion-cut', 'ConflictSignals': list(FinalAffectedSignals), 'RelocationSignals': list(FinalAffectedSignals), 'ResourceHotspots': [list(Value) for Value in Hotspots[:32]]}, 'OverflowProgression': RunState.OverflowProgression, 'ConflictResources': {str(Resource): list(Signals) for Resource, Signals in sorted(FinalConflicts.items(), key=lambda Value: str(Value[0]))}, 'CachedNodeCount': RunState.Resources.ResourceGraph.CachedNodeCount, 'CachedEdgeCount': RunState.Resources.ResourceGraph.CachedEdgeCount, 'HaloSize': RunState.TileSize, 'Regions': {Signal: {'ActiveTiles': [list(Value) for Value in sorted(State.ActiveTiles)], 'BoundaryTouches': [list(Value) for Value in sorted(State.BoundaryTouches)], 'ExpandedSides': list(State.ExpandedSides), 'ExpansionEvents': list(State.ExpansionEvents), 'OwnedNodeCount': len(State.AddedNodes), 'OwnedEdgeCount': len(State.AddedEdges)} for Signal, State in sorted(RunState.RegionStates.items())}, 'BranchRepairs': RunState.BranchRepairEvents, 'InitialDetailedBatch': dict(RunState.InitialDetailedBatchDiagnostics), 'InitialDetailedRequestWindow': RunState.InitialDetailedRequestWindow, 'CachedRepairSelections': RunState.CachedRepairSelections, 'NegotiatedAssignmentAttempts': list(RunState.NegotiatedAssignmentAttempts), 'TerminalCount': RunState.TerminalCount, 'SearchExpansionEscalations': dict(sorted(RunState.SearchExpansionEscalations.items())), 'CumulativeConflictSignals': sorted(RunState.CumulativeConflictSignals), 'RepeaterRejections': {Signal: dict(sorted(Values.items())) for Signal, Values in sorted(RunState.RejectionCountsBySignal.items())}, 'NativeSearch': {Signal: list(Values) for Signal, Values in sorted(RunState.NativeSearchDiagnosticsBySignal.items())}}))
    return PhaseOutcome()

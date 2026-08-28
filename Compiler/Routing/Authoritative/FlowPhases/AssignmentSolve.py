"""AssignmentSolve phase of authoritative routing."""
from __future__ import annotations
from ..RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState, PhaseOutcome

def RunAssignmentSolve(State: AuthoritativeRoutingState, Services: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run the AssignmentSolve phase against shared routing state."""
    (PhysicalGlobalAssignmentSuffixHistory): list[dict[str, object]] = []
    (PhysicalGlobalCompletedPairNoGoodEdges): tuple[tuple[str, str], ...] = ()
    PhysicalGlobalConflictResult = State.Result
    if PhysicalGlobalCompletedPairNoGoodEdges:
        PhysicalGlobalConflictResult = Services.SimpleNamespace(Success=False, SelectedCandidateIds=tuple(State.Result.SelectedCandidateIds), ExpansionCount=int(getattr(State.Result, 'ExpansionCount', 0)), CompletedWork=int(getattr(State.Result, 'CompletedWork', 0)), BudgetExhausted=bool(getattr(State.Result, 'BudgetExhausted', False)), DeadlineExceeded=bool(getattr(State.Result, 'DeadlineExceeded', False)), FailureNet=PhysicalGlobalCompletedPairNoGoodEdges[0][0], ConflictSignals=tuple(sorted({Signal for Edge in PhysicalGlobalCompletedPairNoGoodEdges for Signal in Edge})), ConflictResourceIndices=(), PairwiseIncompatibleSignals=PhysicalGlobalCompletedPairNoGoodEdges, PairwiseCompatibilityComplete=True)
    SelectedAssignmentSignals = frozenset((str(Signal) for Signal, _CandidateId in State.Result.SelectedCandidateIds))
    MissingAssignmentSignals = tuple(sorted(frozenset(State.Profiles) - SelectedAssignmentSignals))
    if State.Result.Success and MissingAssignmentSignals:
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch if State.PhysicalAssemblyPlan is not None else Services.RoutingFailureReason.TrackAssignmentConflict, Stage='PhysicalComponentGlobalAssignmentIdentity' if State.PhysicalAssemblyPlan is not None else 'TrackAssignmentIdentity', AffectedNets=MissingAssignmentSignals, Detail='authoritative assignment reported success without one selected candidate for every routed signal', Diagnostics={'MissingAssignmentSignals': list(MissingAssignmentSignals), 'SelectedAssignmentSignals': sorted(SelectedAssignmentSignals), 'PhysicalAssemblyPlanFingerprint': State.PhysicalAssemblyPlan.PlanFingerprint if State.PhysicalAssemblyPlan is not None else '', 'ImplicitForeignTransitDomainCount': 0}))
    State.CheckRuntimeBudget('Track')
    State.StageTimings['Assignment'] = Services.monotonic() - State.AssignmentStarted
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        SelectedAssignmentSignals = {str(Signal) for Signal, _CandidateId in State.Result.SelectedCandidateIds}
        print(f'[debug] authoritative: assignment result success={State.Result.Success} expansions={State.Result.ExpansionCount} selected={len(State.Result.SelectedCandidateIds)} missing={sorted(frozenset(State.Profiles) - SelectedAssignmentSignals)} budget_exhausted={Services.ShouldGrowAssignmentBudget(State.Result)}', f' elapsed={State.StageTimings['Assignment']:.3f}s', flush=True)
    if not State.Result.Success:
        if State.Policy.AdaptiveRouting.Enabled:
            ConflictClassificationStarted = Services.monotonic()
            PortalById = {Portal.PortalId: Portal for Values in State.Portals.values() for Portal in Values}
            MandatoryClaimsBySignal = {}
            if State.CandidateDiversityLevel == 0:
                for Signal in sorted(State.CandidatesBySignal):
                    Profile = State.Profiles[Signal]
                    AccessNodes = {*Profile.SourceAccessPath, *(Position for Path in Profile.TargetAccessPaths.values() for Position in Path)}
                    Claims = []
                    SeenClaims = set()
                    for Candidate in State.CandidatesBySignal.get(Signal, ()):
                        CandidatePortals = [PortalById.get(Candidate.SourcePortalId), *(PortalById.get(PortalId) for PortalId in Candidate.TargetPortalIds.values())]
                        if any((Portal is None for Portal in CandidatePortals)):
                            continue
                        MandatoryClaims = State.Resources.ResourceGraph.BuildRouteClaims({*AccessNodes, *(Position for Portal in CandidatePortals if Portal is not None for Position in Portal.Path)})
                        Signature = MandatoryClaims.ResourceIds
                        if Signature in SeenClaims:
                            continue
                        SeenClaims.add(Signature)
                        Claims.append(MandatoryClaims)
                    MandatoryClaimsBySignal[Signal] = tuple(Claims)
            MandatoryCuts = Services.FindAllUnavoidableMandatoryClaimCuts(MandatoryClaimsBySignal) if State.CandidateDiversityLevel == 0 and (not PhysicalGlobalCompletedPairNoGoodEdges) else ()
            if MandatoryCuts:
                SelectedMandatoryCuts = MandatoryCuts
                CoverageRepairSignals = tuple(sorted({Signal for Pair, _Positions in SelectedMandatoryCuts for Signal in Pair}))
                CutSignals = tuple(sorted({Signal for Pair, _Positions in SelectedMandatoryCuts for Signal in Pair}))
                CutPositions = frozenset({Position for _Pair, Positions in SelectedMandatoryCuts for Position in Positions})
                ConflictGraph = {'Classification': 'mandatory-boundary-capacity-cut' if State.MandatoryPortalCuts else 'portal-coverage-pair-conflict', 'FailureNet': getattr(State.Result, 'FailureNet', None), 'BudgetExhausted': False, 'ExpansionCount': int(getattr(State.Result, 'ExpansionCount', 0)), 'CandidateCounts': {Signal: len(State.CandidatesBySignal[Signal]) for Signal in sorted(State.CandidatesBySignal)}, 'NoCandidateSignals': [], 'NativeConflictSignals': list(CutSignals), 'CongestionCutSignals': list(CutSignals), 'ConflictSignals': list(CutSignals), 'PairwiseIncompatibleEdges': [list(Pair) for Pair, _Positions in SelectedMandatoryCuts], 'ResourceHotspots': [list(Position) for Position in sorted(CutPositions)[:32]], 'PortalReservations': [Value.ToDictionary() for Value in State.PortalReservations], 'MandatoryAlternativeCounts': {Signal: len(MandatoryClaimsBySignal[Signal]) for Signal in CutSignals}, 'CandidateCoverageRepairPairs': [{'Signals': list(Pair), 'CompatibleAlternatives': 0, 'TotalAlternatives': len(MandatoryClaimsBySignal[Pair[0]]) * len(MandatoryClaimsBySignal[Pair[1]])} for Pair, _Positions in SelectedMandatoryCuts], 'CandidateCoverageRepairSignals': list(CoverageRepairSignals)}
            else:
                SelectedPartialSignals = frozenset((str(Signal) for Signal, _CandidateId in getattr(State.Result, 'SelectedCandidateIds', ())))
                MissingPartialSignals = frozenset(State.Profiles) - SelectedPartialSignals
                UsePartialAssignmentRepairCut = not State.UseNegotiatedRouting and 33 <= len(State.Profiles) <= 72 and State.PlacementWasRelocated and (State.CandidateDiversityLevel > 0) and bool(MissingPartialSignals) and (len(SelectedPartialSignals) * 10 >= len(State.Profiles) * 9)
            if not MandatoryCuts and UsePartialAssignmentRepairCut:
                ConflictSignals = sorted(MissingPartialSignals)
                ConflictGraph = {'Classification': 'relocated-higher-order-conflict', 'FailureNet': getattr(State.Result, 'FailureNet', None), 'BudgetExhausted': False, 'ExpansionCount': int(getattr(State.Result, 'ExpansionCount', 0)), 'CandidateCounts': {Signal: len(State.CandidatesBySignal[Signal]) for Signal in sorted(State.CandidatesBySignal)}, 'NoCandidateSignals': [], 'NativeConflictSignals': ConflictSignals, 'CongestionCutSignals': ConflictSignals, 'ConflictSignals': ConflictSignals, 'PairwiseIncompatibleEdges': [], 'ResourceHotspots': [], 'PortalReservations': [Value.ToDictionary() for Value in State.PortalReservations], 'CandidateCoverageRepairPairs': [], 'CandidateCoverageRepairSignals': []}
            elif not MandatoryCuts:
                ConflictGraph = Services.BuildRoutingConflictGraph(State.CandidatesBySignal, PhysicalGlobalConflictResult, State.AssignmentIndexed.ResourcePositions, State.PortalReservations, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('ConflictClassification', Diagnostics))
            StackedConflictPairs = tuple((tuple(Pair) for Pair in ConflictGraph['PairwiseIncompatibleEdges'] if len(Pair) == 2 and Pair[0] in State.Profiles and (Pair[1] in State.Profiles) and ((State.Profiles[Pair[0]].Root[0], State.Profiles[Pair[0]].Root[2]) == (State.Profiles[Pair[1]].Root[0], State.Profiles[Pair[1]].Root[2])) and (State.Profiles[Pair[0]].Root[1] != State.Profiles[Pair[1]].Root[1])))
            HasPlacementRelocation = State.PlacementWasRelocated
            if StackedConflictPairs:
                ConflictGraph['Classification'] = 'stacked-placement-conflict'
                ConflictGraph['StackedConflictPairs'] = StackedConflictPairs
            elif ConflictGraph['Classification'] == 'higher-order-placement-conflict' and HasPlacementRelocation and (len(ConflictGraph['PairwiseIncompatibleEdges']) == 1):
                ConflictGraph['Classification'] = 'relocated-pairwise-incompatibility'
                ConflictGraph['ConflictSignals'] = list(ConflictGraph['PairwiseIncompatibleEdges'][0])
            elif ConflictGraph['Classification'] == 'higher-order-placement-conflict' and HasPlacementRelocation:
                ConflictGraph['Classification'] = 'relocated-higher-order-conflict'
            elif ConflictGraph['Classification'] == 'pairwise-incompatibility' and len(ConflictGraph['PairwiseIncompatibleEdges']) >= 2:
                ConflictGraph['Classification'] = 'relocated-multi-pair-conflict' if HasPlacementRelocation else 'multi-pair-placement-conflict'
                ConflictGraph['ConflictSignals'] = sorted({Signal for Pair in ConflictGraph['PairwiseIncompatibleEdges'] for Signal in Pair})
            elif HasPlacementRelocation and ConflictGraph['Classification'] == 'larger-matching-failure':
                ConflictGraph['Classification'] = 'relocated-larger-matching-failure'
            elif HasPlacementRelocation and ConflictGraph['Classification'] == 'pairwise-incompatibility':
                ConflictGraph['Classification'] = 'relocated-pairwise-incompatibility'
            PairSignals = sorted({str(Signal) for Pair in ConflictGraph.get('PairwiseIncompatibleEdges', ()) if isinstance(Pair, tuple | list) and len(Pair) == 2 for Signal in Pair})
            if PairSignals and ConflictGraph['Classification'] not in {'mandatory-boundary-capacity-cut', 'portal-coverage-pair-conflict'}:
                MandatoryClaimsByPairSignal = {}
                for Signal in PairSignals:
                    Profile = State.Profiles[Signal]
                    AccessNodes = {*Profile.SourceAccessPath, *(Position for Path in Profile.TargetAccessPaths.values() for Position in Path)}
                    Claims = []
                    SeenClaims = set()
                    for Candidate in State.CandidatesBySignal.get(Signal, ()):
                        CandidatePortals = [PortalById.get(Candidate.SourcePortalId), *(PortalById.get(PortalId) for PortalId in Candidate.TargetPortalIds.values())]
                        if any((Portal is None for Portal in CandidatePortals)):
                            continue
                        MandatoryClaims = State.Resources.ResourceGraph.BuildRouteClaims({*AccessNodes, *(Position for Portal in CandidatePortals if Portal is not None for Position in Portal.Path)})
                        Signature = MandatoryClaims.ResourceIds
                        if Signature in SeenClaims:
                            continue
                        SeenClaims.add(Signature)
                        Claims.append(MandatoryClaims)
                    MandatoryClaimsByPairSignal[Signal] = tuple(Claims)
                CompleteMandatoryPairCoverage = Services.BuildCompleteMandatoryClaimCutCoverage(MandatoryClaimsByPairSignal, bool(State.MandatoryPortalCuts))
                if CompleteMandatoryPairCoverage is not None:
                    ConflictGraph.update(CompleteMandatoryPairCoverage)
            ConflictGraph['RelocationSignals'] = Services.SelectPlacementRelocationSignals(ConflictGraph)
            ConflictGraph['PriorityRelocationSignals'] = Services.SelectPriorityPlacementRelocationSignals(ConflictGraph)
            HasPairwiseIncompatibility = bool(ConflictGraph['PairwiseIncompatibleEdges'])
            AffectedCandidateSignals = frozenset(ConflictGraph.get('ConflictSignals', ()))
            if not AffectedCandidateSignals:
                AffectedCandidateSignals = frozenset(State.CandidatesBySignal)
            if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                print(f'[debug] authoritative: conflict graph classification={ConflictGraph['Classification']} portal_mode={('unreserved' if State.UnreservedPortalMode else 'reserved')} reservation_variant={State.ReservationVariant} lane_diversity={State.LaneDiversityLevel} layers={State.LayerCount} pairwise={HasPairwiseIncompatibility} edges={ConflictGraph['PairwiseIncompatibleEdges'][:4]} coverage_repair_signals={len(ConflictGraph.get('CandidateCoverageRepairSignals', ()))} classification_elapsed={Services.monotonic() - ConflictClassificationStarted:.3f}s runtime_left={State.AdaptiveBudget.RuntimeSeconds - (Services.monotonic() - State.RoutingStarted):.3f}', flush=True)
                if ConflictGraph['PairwiseIncompatibleEdges']:
                    FirstSignal, SecondSignal = ConflictGraph['PairwiseIncompatibleEdges'][0]
                    (ConflictLocations): Services.Counter[Services.Position3] = Services.Counter()
                    for FirstCandidate in State.CandidatesBySignal[FirstSignal]:
                        for SecondCandidate in State.CandidatesBySignal[SecondSignal]:
                            FirstClaims = FirstCandidate.Claims
                            SecondClaims = SecondCandidate.Claims
                            Locations = FirstClaims.WireCells & SecondClaims.WireCells | FirstClaims.RequiredAirCells & SecondClaims.WireCells | SecondClaims.RequiredAirCells & FirstClaims.WireCells | FirstClaims.ElectricalCells & SecondClaims.WireCells | SecondClaims.ElectricalCells & FirstClaims.WireCells
                            ConflictLocations.update(Locations)
                    print(f'[debug] authoritative: first pair details signals=({FirstSignal},{SecondSignal}) candidate_counts=({len(State.CandidatesBySignal[FirstSignal])},{len(State.CandidatesBySignal[SecondSignal])}) roots=({State.Profiles[FirstSignal].Root},{State.Profiles[SecondSignal].Root}) access=({State.Profiles[FirstSignal].SourceAccessPath},{State.Profiles[SecondSignal].SourceAccessPath}) target_access=({State.Profiles[FirstSignal].TargetAccessPaths},{State.Profiles[SecondSignal].TargetAccessPaths}) coarse=({((State.CoarsePlan.Layers[FirstSignal], State.CoarsePlan.Axes[FirstSignal], State.CoarsePlan.Lanes[FirstSignal]) if State.CoarsePlan is not None else None)},{((State.CoarsePlan.Layers[SecondSignal], State.CoarsePlan.Axes[SecondSignal], State.CoarsePlan.Lanes[SecondSignal]) if State.CoarsePlan is not None else None)}) tracks=({sorted({State.CandidateAxisLaneBySignal.get(FirstSignal, {}).get(Value.CandidateId, ('reused', 0, Value.Layer, Value.SeedNodeCount))[:3] for Value in State.CandidatesBySignal[FirstSignal]})},{sorted({State.CandidateAxisLaneBySignal.get(SecondSignal, {}).get(Value.CandidateId, ('reused', 0, Value.Layer, Value.SeedNodeCount))[:3] for Value in State.CandidatesBySignal[SecondSignal]})}) hotspots={ConflictLocations.most_common(6)}', flush=True)
        else:
            ConflictGraph = {'Classification': 'complete candidate set assignment failure', 'ConflictSignals': tuple((Signal for Signal, Candidates in State.CandidatesBySignal.items() if not Candidates)), 'PairwiseIncompatibleEdges': (), 'NoCandidateSignals': [Signal for Signal, Candidates in State.CandidatesBySignal.items() if not Candidates], 'ResourceHotspots': [], 'PortalReservations': [Value.ToDictionary() for Value in State.PortalReservations]}
            HasPairwiseIncompatibility = False
        CandidateFingerprint = Services.BuildStableFingerprint({Signal: [Candidate.CandidateId for Candidate in State.CandidatesBySignal[Signal]] for Signal in sorted(State.CandidatesBySignal)})
        ConflictFingerprint = Services.BuildStableFingerprint(ConflictGraph)
        if State.Resources.PreparingPhysicalComponentGlobalChannels:
            RemainingRequestCounts = Services.BuildPhysicalRouteDescriptorRemainingCounts(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDomainFingerprintsBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal)
            NativeConflictSignals = tuple(sorted({str(Signal) for Signal in getattr(PhysicalGlobalConflictResult, 'ConflictSignals', ()) if str(Signal) in State.CandidatesBySignal}))
            SelectedAssignmentSignals = frozenset((str(Signal) for Signal, _CandidateId in State.Result.SelectedCandidateIds))
            RelevantAssignmentSignals = NativeConflictSignals or tuple(sorted(set(State.CandidatesBySignal) - SelectedAssignmentSignals))
            CandidateDomainComplete = Services.PhysicalGlobalAssignmentDomainIsComplete(RelevantAssignmentSignals, RemainingRequestCounts, Services.ShouldGrowAssignmentBudget(State.Result), State.Deadline.IsExpired())
            CapturedCorridorDomains = ()
            if not State.Deadline.IsExpired():
                CapturedCorridorDomains = Services.CaptureCompletePhysicalPortCorridorDomains(State.PhysicalAssemblyPlan, State.PreSiblingCandidatesBySignal, State.CandidateRequestShapeDomainFingerprintBySignal, RemainingRequestCounts, State.Resources)
            PairwiseEdges = tuple((tuple(map(str, Edge)) for Edge in ConflictGraph.get('PairwiseIncompatibleEdges', ()) if isinstance(Edge, (tuple, list)) and len(Edge) == 2))
            FinalExteriorRemainingRequestCounts = Services.BuildPhysicalRouteDescriptorRemainingCounts(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDomainFingerprintsBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal)
            FinalCompletedExteriorContinuations = Services.RetainCompletePhysicalSignalRouteDomainContinuations(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal, State.PhysicalRequestDomainFingerprintsBySignal, FinalExteriorRemainingRequestCounts, State.PreSiblingCandidatesBySignal, State.PreSiblingCandidateMetadataBySignal)
            CompletedExteriorSignals = frozenset((Continuation.Signal for Continuation in (*State.CompletedExteriorContinuations, *FinalCompletedExteriorContinuations)))
            PairwisePortReservationNoGoodProofComplete = bool(CandidateDomainComplete and PairwiseEdges and Services.ConflictClassificationSupportsPhysicalPortPairNoGoods(str(ConflictGraph.get('Classification', ''))) and all((First != Second and First not in State.IncompletePreSiblingDomainSignals and (Second not in State.IncompletePreSiblingDomainSignals) and (First in CompletedExteriorSignals) and (Second in CompletedExteriorSignals) and Services.PhysicalSignalLocalCandidateRequestFactorProofComplete(First, State.CandidateRequestDependencyComponentsBySignal.get(First, {}), State.Resources.PhysicalComponentExactGlobalChannelSignals, State.PhysicalPortGuidesBySignal, State.CertifiedApertureDomain) and Services.PhysicalSignalLocalCandidateRequestFactorProofComplete(Second, State.CandidateRequestDependencyComponentsBySignal.get(Second, {}), State.Resources.PhysicalComponentExactGlobalChannelSignals, State.PhysicalPortGuidesBySignal, State.CertifiedApertureDomain) and Services.CompletePhysicalCandidatePairDomainsHaveNoSupport(State.PreSiblingCandidatesBySignal.get(First, ()), State.PreSiblingCandidatesBySignal.get(Second, ())) for First, Second in PairwiseEdges)))
            (HigherOrderPortReservationNoGoodSignals): tuple[str, ...] = ()
            (HigherOrderPortReservationNoGoodCandidateCounts): dict[str, int] = {}
            (HigherOrderPortReservationNoGoodProofAttempts): list[dict[str, object]] = []

            def ProveIndependentCandidateCoreUnsatisfiable(Signals: tuple[str, ...]) -> bool:
                if len(Signals) < 2:
                    return False
                if not all((Signal in CompletedExteriorSignals and Signal not in State.IncompletePreSiblingDomainSignals and State.PreSiblingCandidatesBySignal.get(Signal, ()) and Services.PhysicalSignalLocalCandidateRequestFactorProofComplete(Signal, State.CandidateRequestDependencyComponentsBySignal.get(Signal, {}), State.Resources.PhysicalComponentExactGlobalChannelSignals, State.PhysicalPortGuidesBySignal, State.CertifiedApertureDomain) for Signal in Signals)):
                    return False
                CoreCandidates = {Signal: list(State.PreSiblingCandidatesBySignal.get(Signal, ())) for Signal in Signals}
                CoreResult = State.PlanAssignment(State.EncodeCandidateValues(CoreCandidates), AvoidExactNoGoods=False)
                CompleteUnsatisfiable = bool(not getattr(CoreResult, 'Success', False) and (not getattr(CoreResult, 'BudgetExhausted', False)) and (not getattr(CoreResult, 'DeadlineExceeded', False)))
                HigherOrderPortReservationNoGoodProofAttempts.append({'Signals': list(Signals), 'CandidateCounts': {Signal: len(CoreCandidates[Signal]) for Signal in Signals}, 'ExpansionCount': int(getattr(CoreResult, 'ExpansionCount', 0)), 'CompleteUnsatisfiable': CompleteUnsatisfiable})
                return CompleteUnsatisfiable
            ProposedHigherOrderCore = tuple(sorted({str(Signal) for Signal in getattr(PhysicalGlobalConflictResult, 'ConflictSignals', ()) if str(Signal) in State.PreSiblingCandidatesBySignal}))
            if CandidateDomainComplete and len(ProposedHigherOrderCore) >= 3 and ProveIndependentCandidateCoreUnsatisfiable(ProposedHigherOrderCore):
                MinimalCore = list(ProposedHigherOrderCore)
                for Signal in tuple(MinimalCore):
                    TrialCore = tuple((Value for Value in MinimalCore if Value != Signal))
                    if ProveIndependentCandidateCoreUnsatisfiable(TrialCore):
                        MinimalCore = list(TrialCore)
                HigherOrderPortReservationNoGoodSignals = tuple(MinimalCore)
                HigherOrderPortReservationNoGoodCandidateCounts = {Signal: len(State.PreSiblingCandidatesBySignal.get(Signal, ())) for Signal in HigherOrderPortReservationNoGoodSignals}
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.TrackAssignmentConflict, Stage='PhysicalComponentGlobalAssignmentDomain', AffectedNets=tuple(sorted(ConflictGraph.get('ConflictSignals', ()))), RepairActions=(), Detail='the fixed physical assembly global assignment domain is unsatisfiable' if CandidateDomainComplete else 'physical assembly global assignment stopped before its finite candidate domain was complete', Diagnostics={'PhysicalComponentGlobalPlanning': True, 'GlobalPlanDomainComplete': CandidateDomainComplete, 'CompleteAssignmentCutProof': CandidateDomainComplete, 'IndependentEmptyCandidateDomainSignals': list(State.WorkTelemetry.get('PhysicalGlobalForeignPortalCandidateCertificates', {}).get('IndependentEmptyCandidateDomainSignals', ())), 'PairwisePortReservationNoGoodProofComplete': PairwisePortReservationNoGoodProofComplete, 'PairwisePortReservationNoGoodEdges': [list(Edge) for Edge in (PairwiseEdges if PairwisePortReservationNoGoodProofComplete else ())], 'HigherOrderPortReservationNoGoodProofComplete': bool(HigherOrderPortReservationNoGoodSignals), 'HigherOrderPortReservationNoGoodSignals': list(HigherOrderPortReservationNoGoodSignals), 'HigherOrderPortReservationNoGoodCandidateCounts': dict(sorted(HigherOrderPortReservationNoGoodCandidateCounts.items())), 'HigherOrderPortReservationNoGoodProofAttempts': HigherOrderPortReservationNoGoodProofAttempts, 'FinalCompletedExteriorRouteDomainSignals': sorted(CompletedExteriorSignals), 'AssignmentBudgetExhausted': bool(Services.ShouldGrowAssignmentBudget(State.Result)), 'RelevantAssignmentSignals': list(RelevantAssignmentSignals), 'RemainingRequestCounts': RemainingRequestCounts, 'PhysicalPortCorridorDomains': [Domain.ToDictionary() for Domain in CapturedCorridorDomains], 'PhysicalPortCorridorDomainCacheSize': len(State.Resources.PhysicalPortCorridorDomainCache), 'AssignmentSuffixExpansion': PhysicalGlobalAssignmentSuffixHistory, 'CandidateFingerprint': CandidateFingerprint, 'ConflictFingerprint': ConflictFingerprint, 'ConflictGraph': ConflictGraph, 'ExecutableLegacyRepairCascade': False}))
        Locations = tuple((State.AssignmentIndexed.ResourcePositions[Index] for Index in State.Result.ConflictResourceIndices[:8]))
        ZeroCompatibilityPairs = []
        if not State.Policy.AdaptiveRouting.Enabled:
            Signals = sorted(State.CandidatesBySignal)
            for SignalIndex, FirstSignal in enumerate(Signals):
                for SecondSignal in Signals[SignalIndex + 1:]:
                    if not any((not Services.FindClaimConflicts({FirstSignal: First.Claims, SecondSignal: Second.Claims}, WorkCheck=lambda Diagnostics: State.CheckRuntimeBudget('CompatibilityConflictClassification', Diagnostics)) for First in State.CandidatesBySignal[FirstSignal] for Second in State.CandidatesBySignal[SecondSignal])):
                        ZeroCompatibilityPairs.append((FirstSignal, SecondSignal))
        raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete if Services.ShouldGrowAssignmentBudget(State.Result) else Services.RoutingFailureReason.TrackAssignmentConflict, Stage='Track', AffectedNets=(State.Result.FailureNet,) if State.Result.FailureNet else (), Locations=Locations, Detail=f'Rust MRV assignment found no exact capacity-one selection after {State.Result.ExpansionCount} expansions; budget_exhausted={Services.ShouldGrowAssignmentBudget(State.Result)}; failure_net_candidates={(len(State.CandidatesBySignal.get(State.Result.FailureNet, ())) if State.Result.FailureNet else 0)}; conflict_classification={ConflictGraph['Classification']}; pairwise_unroutable={(ConflictGraph['PairwiseIncompatibleEdges'] if State.Policy.AdaptiveRouting.Enabled else ZeroCompatibilityPairs)[:4]}', Diagnostics={**ConflictGraph, 'ConflictGraph': ConflictGraph, 'Complete': not Services.ShouldGrowAssignmentBudget(State.Result), 'CandidateFingerprint': CandidateFingerprint, 'ConflictFingerprint': ConflictFingerprint}))
    if State.ProgressCallback is not None:
        State.ProgressCallback(5, State.StageCount)
    return PhaseOutcome()

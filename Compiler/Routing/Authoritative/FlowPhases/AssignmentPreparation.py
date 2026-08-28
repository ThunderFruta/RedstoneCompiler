"""AssignmentPreparation phase of authoritative routing."""
from __future__ import annotations
from ..RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState, PhaseOutcome

def RunAssignmentPreparation(State: AuthoritativeRoutingState, Services: AuthoritativeRoutingServices) -> PhaseOutcome:
    """Run the AssignmentPreparation phase against shared routing state."""
    if State.Resources.PreparingPhysicalComponentGlobalChannels:
        State.WorkTelemetry['PhysicalGlobalCandidateDomainCompletion'] = {'CompleteBeforeAssignment': all((int(Diagnostics.get('DeferredRequests', 0)) == 0 for Diagnostics in State.CandidateDiagnostics.values())), 'SignalCount': len(State.PhysicalGlobalCandidateSuffixConsumers), 'Records': [], 'NativeAssignmentBeforeCompletionCount': 0, 'Strategy': 'lazy-exact-assignment-cuts'}
        CompletedExteriorRemainingRequestCounts = Services.BuildPhysicalRouteDescriptorRemainingCounts(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDomainFingerprintsBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal)
        State.CompletedExteriorContinuations = Services.RetainCompletePhysicalSignalRouteDomainContinuations(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal, State.PhysicalRequestDomainFingerprintsBySignal, CompletedExteriorRemainingRequestCounts, State.PreSiblingCandidatesBySignal, State.PreSiblingCandidateMetadataBySignal)
        CompletedPortableExteriorContinuations = Services.RetainCompletePortablePhysicalSignalRouteDomains(State.Resources.PhysicalGlobalApertureTemplateCache, State.PortableRouteDomainPreparationBySignal, CompletedExteriorRemainingRequestCounts, State.PreSiblingCandidatesBySignal, State.PreSiblingCandidateMetadataBySignal)
        State.WorkTelemetry['PhysicalExteriorRouteDomainCapture'] = {'CapturedSignalCount': len(State.CompletedExteriorContinuations), 'CapturedSignals': [Value.Signal for Value in State.CompletedExteriorContinuations], 'CacheSize': len(State.Resources.PhysicalSignalRouteDomainContinuationCache), 'PortableBucketCount': sum((str(Key).startswith('portable-route-domain-bucket:') for Key in State.Resources.PhysicalGlobalApertureTemplateCache)), 'PreSibling': True, 'CompleteDomainsOnly': True, 'PortableCapturedSignalCount': len(CompletedPortableExteriorContinuations), 'PortablePublishedSignals': [Value.Signal for Value in CompletedPortableExteriorContinuations]}
        State.CheckRuntimeBudget('PhysicalComponentGlobalCandidatePrefix')
        FilteredPhysicalCandidatesBySignal, StalePhysicalCandidateIdsBySignal = Services.FilterPhysicalCandidatesToCurrentPortalDomain(State.CandidatesBySignal, State.Portals)
        State.CandidatesBySignal = {Signal: list(Values) for Signal, Values in FilteredPhysicalCandidatesBySignal.items()}
        State.WorkTelemetry['PhysicalCandidatePortalIdentity'] = {'RemovedCandidateCount': sum((len(Values) for Values in StalePhysicalCandidateIdsBySignal.values())), 'RemovedCandidateIdsBySignal': {Signal: list(Values) for Signal, Values in sorted(StalePhysicalCandidateIdsBySignal.items())}, 'VisiblePortalCount': len({Portal.PortalId for Values in State.Portals.values() for Portal in Values})}
        CompleteEmptyDomainSignals, PortalIdentityMismatchSignals = Services.ClassifyEmptyPhysicalCandidateDomains(State.CandidatesBySignal, StalePhysicalCandidateIdsBySignal, CertifiedCurrentEmptyDomainSignals=(Signal for Signal in State.CompleteExteriorRouteDomainSignals if not tuple(State.CandidatesBySignal.get(Signal, ()))))
        EmptyCurrentPortalDomainSignals = tuple(sorted((*CompleteEmptyDomainSignals, *PortalIdentityMismatchSignals)))
        if EmptyCurrentPortalDomainSignals:
            if CompleteEmptyDomainSignals and (not PortalIdentityMismatchSignals):
                CompletedRequestApertureClauses = tuple((Clause for Signal in CompleteEmptyDomainSignals for Clause in (Services.BuildMinimalPhysicalRequestApertureNoGood(Signal, State.ApertureCandidateDomainIdentityBySignal[Signal].StableDomainFingerprint, State.SiblingApertureConflictSetsBySignal.get(Signal, ()), {Factor.Signal: Factor.ApertureFingerprint for Factor in (State.CertifiedApertureDomain.Factors if State.CertifiedApertureDomain is not None else ())}),) if Clause and Signal in State.ApertureCandidateDomainIdentityBySignal and (State.CertifiedApertureDomain is not None) and State.CertifiedApertureDomain.Complete))
                SelectedRequestApertureClause = min(CompletedRequestApertureClauses, key=lambda Clause: (len(Clause), tuple(sorted(Clause)))) if CompletedRequestApertureClauses else frozenset()
                SelectedRequestSignals = frozenset((Signal for Signal, Fingerprint in SelectedRequestApertureClause if Fingerprint.startswith('request-factor:')))
                SignalLocalRequestFactorProofComplete = bool(len(SelectedRequestSignals) == 1 and Services.PhysicalSignalLocalCandidateRequestFactorProofComplete(next(iter(SelectedRequestSignals)), State.CandidateRequestDependencyComponentsBySignal.get(next(iter(SelectedRequestSignals)), {}), State.Resources.PhysicalComponentExactGlobalChannelSignals, State.PhysicalPortGuidesBySignal, State.CertifiedApertureDomain))
                RequestAperturePortNoGood = Services.BuildPhysicalRequestAperturePortNoGood(State.PhysicalAssemblyPlan, SelectedRequestApertureClause, SignalLocalRequestFactorProofComplete=SignalLocalRequestFactorProofComplete, PortSolverCacheKey=Services.BuildPhysicalComponentPortSolverCacheKey(str(getattr(State.Resources.PreparedPhysicalComponentPortFactorDomain, 'DomainFingerprint', ''))) if getattr(State.Resources, 'PreparedPhysicalComponentPortFactorDomain', None) is not None else '') if SelectedRequestApertureClause and State.PhysicalAssemblyPlan is not None else frozenset()
                AlternativeAperturePortNoGoods = ()
                if SignalLocalRequestFactorProofComplete and len(SelectedRequestSignals) == 1:
                    RequestSignal = next(iter(SelectedRequestSignals))
                    PreparedPortDomain = getattr(State.Resources, 'PreparedPhysicalComponentPortFactorDomain', None)
                    MatchingVictimFactors = tuple((Factor for Factor in State.CertifiedApertureDomain.Factors if Factor.Signal == RequestSignal))
                    if PreparedPortDomain is not None and len(MatchingVictimFactors) == 1:
                        AlternativeAperturePortNoGoods = Services.BuildCompletePhysicalRequestAlternativeApertureNoGoods(RequestSignal, MatchingVictimFactors[0].PortGlobalContractFingerprint, State.PreSiblingCandidatesBySignal.get(RequestSignal, ()), dict(PreparedPortDomain.BoundaryPortReservationsBySignal))
                        State.Resources.RejectedPhysicalComponentPortReservationSets.update(AlternativeAperturePortNoGoods)
                CertifiedConflictSignals = tuple(sorted({Signal for Signal, _FingerprintValue in SelectedRequestApertureClause}))
                if SelectedRequestApertureClause:
                    State.Resources.RejectedPhysicalGlobalRequestApertureFactorSets.add(SelectedRequestApertureClause)
                if RequestAperturePortNoGood:
                    State.Resources.RejectedPhysicalComponentPortReservationSets.add(RequestAperturePortNoGood)
                IndependentEmptyDomainSignals = tuple(sorted((Signal for Signal in CompleteEmptyDomainSignals if not tuple(State.PreSiblingCandidatesBySignal.get(Signal, ())))))
                raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentChannelCapacityUnsatisfiable, Stage='PhysicalComponentGlobalCandidateDomain', AffectedNets=CertifiedConflictSignals or CompleteEmptyDomainSignals, Detail='the complete authoritative candidate domain has no route compatible with the fixed assembly apertures', Diagnostics={'GlobalPlanDomainComplete': True, 'CompleteAssignmentCutProof': True, 'RequestApertureFactorProofComplete': bool(SelectedRequestApertureClause), 'RequestApertureFactorNoGood': [list(Key) for Key in sorted(SelectedRequestApertureClause)], 'SignalLocalRequestFactorProofComplete': SignalLocalRequestFactorProofComplete, 'RequestAperturePortNoGood': [list(Key) for Key in sorted(RequestAperturePortNoGood)], 'AlternativeAperturePortNoGoods': [[list(Key) for Key in sorted(Clause)] for Clause in AlternativeAperturePortNoGoods], 'IndependentEmptyCandidateDomainSignals': list(IndependentEmptyDomainSignals), 'CandidateDomainCompletion': State.WorkTelemetry.get('PhysicalGlobalCandidateDomainCompletion', {}), 'ConflictGraph': {'Classification': 'candidate-starvation-placement-conflict', 'ConflictSignals': list(CertifiedConflictSignals or CompleteEmptyDomainSignals), 'NoCandidateSignals': list(CertifiedConflictSignals[:1] or CompleteEmptyDomainSignals), 'RelocationSignals': list(CertifiedConflictSignals or CompleteEmptyDomainSignals), 'PriorityRelocationSignals': list(CertifiedConflictSignals or CompleteEmptyDomainSignals), 'CompleteAssignmentCutProof': True}, 'RemovedCandidateIdsBySignal': {Signal: list(Values) for Signal, Values in sorted(StalePhysicalCandidateIdsBySignal.items())}, 'ImplicitForeignTransitDomainCount': 0}))
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='PhysicalComponentGlobalCandidateIdentity', AffectedNets=PortalIdentityMismatchSignals, Detail='completed physical candidate domain contains no route bound to the current portal contract', Diagnostics={'RemovedCandidateIdsBySignal': {Signal: list(Values) for Signal, Values in sorted(StalePhysicalCandidateIdsBySignal.items())}, 'ImplicitForeignTransitDomainCount': 0}))
    State.CandidateLookup = {Candidate.CandidateId: Candidate for Values in State.CandidatesBySignal.values() for Candidate in Values}
    BaseLocalClaims = State.LocalClaims
    State.AssignmentIndexed = State.EffectiveRawPortalCache.AssignmentIndexed
    AssignmentEncodingCache = State.EffectiveRawPortalCache.AssignmentEncodingCache
    (BaseValues): list[tuple[Services.Any, ...]] | None = None

    def EnsurePhysicalAssignmentIndexComplete() -> None:
        """Index all claims in the current immutable capacity domain."""
        nonlocal AssignmentEncodingCache, BaseValues
        if not (State.Resources.PreparingPhysicalComponentGlobalChannels or State.PreRouteLocalClaimChoices):
            return
        Extended = Services.ExtendIndexedRoutingResourceGraph(State.AssignmentIndexed, (*(Candidate.Claims for SignalCandidates in State.CandidatesBySignal.values() for Candidate in SignalCandidates), *(Claim.Claims for Claim in BaseLocalClaims), *(Choice.Claim.Claims for Choice in State.PreRouteLocalClaimChoices), *(Reservation.Claims for Reservation in State.BoundaryLeaseReservations)))
        if Extended is State.AssignmentIndexed:
            return
        OldResourceCount = len(State.AssignmentIndexed.ResourcePositions)
        State.AssignmentIndexed = Extended
        AssignmentEncodingCache = {}
        BaseValues = None
        Rebuilds = State.WorkTelemetry.setdefault('PhysicalAssignmentIndexRebuilds', [])
        Rebuilds.append({'OldResourceCount': OldResourceCount, 'NewResourceCount': len(State.AssignmentIndexed.ResourcePositions), 'AddedResourceCount': len(State.AssignmentIndexed.ResourcePositions) - OldResourceCount, 'CandidateCount': sum(map(len, State.CandidatesBySignal.values())), 'PreRouteLocalClaimChoiceCount': len(State.PreRouteLocalClaimChoices)})

    def EncodeCandidateValues(CandidateSets: dict[str, list[NetRouteCandidate]], CongestionHistory: Counter[Position2] | None=None, OptimizeShape: bool=False) -> list[tuple[Any, ...]]:
        EnsurePhysicalAssignmentIndexComplete()
        Values = []
        History = CongestionHistory or Services.Counter()
        for Signal in sorted(CandidateSets):
            for Candidate in CandidateSets[Signal]:
                UnindexedPositions = Services.FindUnindexedClaimPositions(State.AssignmentIndexed, Candidate.Claims)
                if UnindexedPositions:
                    Rejections = State.WorkTelemetry.setdefault('UnindexedCandidateClaims', {})
                    SignalRejections = Rejections.setdefault(Signal, {'CandidateCount': 0, 'ExamplePositions': []})
                    SignalRejections['CandidateCount'] += 1
                    Examples = SignalRejections['ExamplePositions']
                    for Position in sorted(UnindexedPositions):
                        if Position not in Examples and len(Examples) < 16:
                            Examples.append(Position)
                    continue
                UseEncodingCache = not History and (not OptimizeShape)
                CachedValue = AssignmentEncodingCache.get(Candidate.CandidateId) if UseEncodingCache else None
                if CachedValue is not None:
                    Values.append(CachedValue)
                    continue
                Wire, Support, Air, Electrical = State.AssignmentIndexed.EncodeClaims(Candidate.Claims)
                HistoryCost = sum((History[X, Z] for X, _Y, Z in Candidate.Nodes)) * State.Policy.Repair.HistoryIncrement
                MaterialCost = Candidate.Length * 10000 + Candidate.BendCount * 100 + Candidate.ViaCount if OptimizeShape else Candidate.MaterialCost
                Value = (Signal, Candidate.CandidateId, list(Wire), list(Support), list(Air), list(Electrical), MaterialCost + HistoryCost, Candidate.FootprintGrowth, Candidate.Length, Candidate.BendCount, Candidate.ViaCount)
                Values.append(Value)
                if UseEncodingCache:
                    AssignmentEncodingCache[Candidate.CandidateId] = Value
            for Choice in State.PreRouteLocalClaimChoicesBySignal.get(Signal, ()):
                UnindexedPositions = Services.FindUnindexedClaimPositions(State.AssignmentIndexed, Choice.Claim.Claims)
                if UnindexedPositions:
                    raise RuntimeError('pre-route local claim choice has unindexed claims')
                Wire, Support, Air, Electrical = State.AssignmentIndexed.EncodeClaims(Choice.Claim.Claims)
                Values.append((Signal, Choice.ChoiceId, list(Wire), list(Support), list(Air), list(Electrical), Choice.MaterialCost, len({(X, Z) for X, _Y, Z in Choice.Claim.Nodes}), len(Choice.Claim.Nodes), 0, 0))
        return Values
    State.EncodeCandidateValues = EncodeCandidateValues
    PhysicalAssignmentArcCompatibilityCache = State.Resources.PhysicalGlobalAssignmentArcCompatibilityCache
    (PhysicalAssignmentArcTelemetry): dict[str, object] = {}
    PhysicalAssignmentArcIndex = Services.GetPhysicalGlobalAssignmentArcIndex(State.Resources, Persistent=State.Resources.PreparingPhysicalComponentGlobalChannels)

    def ArcConsistentPhysicalCandidateSets(CandidateSets: Mapping[str, Iterable[NetRouteCandidate]] | None=None) -> dict[str, list[NetRouteCandidate]]:
        CandidateSets = State.CandidatesBySignal if CandidateSets is None else {Signal: list(Values) for Signal, Values in CandidateSets.items()}
        Services.BeginPhysicalAssignmentArcPass(PhysicalAssignmentArcTelemetry)

        def Compatible(First: NetRouteCandidate, Second: NetRouteCandidate) -> bool:
            Key = tuple(sorted((First.CandidateId, Second.CandidateId)))
            Cached = PhysicalAssignmentArcCompatibilityCache.get(Key)
            if Cached is None:
                Cached = not Services.MandatoryClaimsConflict(First.Claims, Second.Claims)
                PhysicalAssignmentArcCompatibilityCache[Key] = Cached
                while len(PhysicalAssignmentArcCompatibilityCache) > 262144:
                    PhysicalAssignmentArcCompatibilityCache.pop(next(iter(PhysicalAssignmentArcCompatibilityCache)))
            return Cached
        AddedComparisonCount = PhysicalAssignmentArcIndex.Extend(CandidateSets, Compatible)
        PriorSupportIntersectionCount = PhysicalAssignmentArcIndex.SupportIntersectionCount
        Mutable, PruneCount = PhysicalAssignmentArcIndex.Propagate(CandidateSets)
        PhysicalAssignmentArcTelemetry.update({'Complete': True, 'PruneCount': PruneCount, 'CompatibilityCheckCount': len(PhysicalAssignmentArcCompatibilityCache), 'IncrementalComparisonCount': PhysicalAssignmentArcIndex.ComparisonCount, 'AddedComparisonCount': AddedComparisonCount, 'SupportIntersectionCount': PhysicalAssignmentArcIndex.SupportIntersectionCount - PriorSupportIntersectionCount, 'CandidateCounts': {Signal: len(Values) for Signal, Values in sorted(Mutable.items())}})
        if any((not Values for Values in Mutable.values())):
            EmptySignals = tuple(sorted((Signal for Signal, Values in Mutable.items() if not Values)))
            BlockerSignalsByEmptySignal: dict[str, list[str]] = {}
            for EmptySignal in EmptySignals:
                Blockers = []
                for OtherSignal in sorted(Mutable):
                    if OtherSignal == EmptySignal:
                        continue
                    OtherCandidates = Mutable[OtherSignal]
                    if not OtherCandidates:
                        continue
                    if any((not PhysicalAssignmentArcIndex.HasSupport(str(Candidate.CandidateId), OtherSignal, {str(OtherCandidate.CandidateId) for OtherCandidate in OtherCandidates}) for Candidate in CandidateSets[EmptySignal])):
                        Blockers.append(OtherSignal)
                BlockerSignalsByEmptySignal[EmptySignal] = Blockers
            PhysicalAssignmentArcTelemetry['Applied'] = False
            PhysicalAssignmentArcTelemetry['EmptySignals'] = list(EmptySignals)
            PhysicalAssignmentArcTelemetry['BlockerSignalsByEmptySignal'] = BlockerSignalsByEmptySignal
            return Mutable
        PhysicalAssignmentArcTelemetry['Applied'] = True
        return Mutable
    State.AssignmentExpansionLimit = State.Policy.TrackAssignment.MaximumAssignmentExpansions
    (PublishedPhysicalLocalAccessCandidateNoGoods): set[frozenset[tuple[str, str]]] = set()
    (PublishedPhysicalForeignPortalCandidateNoGoods): set[frozenset[tuple[str, str]]] = set()
    PhysicalForeignPortalDomainsPrepared = False
    (PhysicalForeignPortalDomains): list[tuple[str, Services.Position3, tuple[Services.RoutingResourceClaims, ...]]] = []
    (PhysicalForeignPortalCompleteIndexesByDomain): dict[int, frozenset[int]] = {}
    (PhysicalForeignPortalOccurrencesByWireCell): dict[Services.Position3, frozenset[tuple[int, int]]] = {}
    (PhysicalForeignPortalOccurrencesBySupportCell): dict[Services.Position3, frozenset[tuple[int, int]]] = {}
    (PhysicalForeignPortalOccurrencesByRequiredAirCell): dict[Services.Position3, frozenset[tuple[int, int]]] = {}
    (PhysicalForeignPortalOccurrencesByElectricalCell): dict[Services.Position3, frozenset[tuple[int, int]]] = {}
    PhysicalForeignPortalIncompleteTerminalCount = 0
    (PhysicalForeignPortalCandidateClaimsById): dict[str, Services.RoutingResourceClaims] = {}
    (PhysicalForeignPortalBlockedByCandidateAndDomain): dict[tuple[str, int], frozenset[int]] = {}
    (PhysicalForeignPortalCertifiedCandidateIds): set[str] = set()
    (PhysicalForeignPortalUnaryRejectedCandidateIdsBySignal): dict[str, set[str]] = Services.defaultdict(set)
    PhysicalForeignPortalCompatibilityCheckCount = 0

    def PublishPhysicalGlobalForeignPortalCandidateNoGoods(CandidateSets: Mapping[str, Iterable[NetRouteCandidate]]) -> None:
        """Preserve one complete ordinary portal per terminal.

        Component exterior candidates are tested against the immutable
        whole-design portal fabric prepared before the interface CSP.  A
        unary clause is complete when one exterior candidate blocks every
        portal for a foreign terminal; a binary clause is complete when the
        union of two candidates' blocker sets covers that same finite domain.
        This is the exact pre-assignment counterpart of the post-selection
        foreign-portal validation and prevents a known-bad contract from
        consuming an authoritative global-routing slice.
        """
        nonlocal PhysicalForeignPortalCompatibilityCheckCount, PhysicalForeignPortalDomainsPrepared, PhysicalForeignPortalIncompleteTerminalCount
        if not State.Resources.PreparingPhysicalComponentGlobalChannels:
            return
        Handoff = State.Resources.FrozenPhysicalComponentPostClosurePortalHandoff
        if Handoff is None:
            return
        Cache = Handoff.RawPortalGeometryCache
        if not isinstance(Cache, Services.RawPortalGeometryCache):
            return
        ExactSignals = frozenset(State.Resources.PhysicalComponentExactGlobalChannelSignals)
        OrderedCandidates = tuple((Candidate for Signal in sorted(CandidateSets) for Candidate in sorted(CandidateSets[Signal], key=lambda Value: Value.CandidateId)))
        if not OrderedCandidates:
            return
        NewCandidates = tuple((Candidate for Candidate in OrderedCandidates if Candidate.CandidateId not in PhysicalForeignPortalCertifiedCandidateIds))
        FixedClaimsBySignal: dict[str, tuple[Services.RoutingResourceClaims, ...]] = {Signal: tuple((Claim.Claims for Claim in State.FrozenComponentClaims if Claim.Signal == Signal)) for Signal in ExactSignals}

        def CompileCandidateClaims(Candidate: NetRouteCandidate) -> RoutingResourceClaims:
            Claims = (Candidate.Claims, *FixedClaimsBySignal.get(Candidate.Signal, ()))
            return Services.RoutingResourceClaims(WireCells=frozenset().union(*(Value.WireCells for Value in Claims)), SupportCells=frozenset().union(*(Value.SupportCells for Value in Claims)), RequiredAirCells=frozenset().union(*(Value.RequiredAirCells for Value in Claims)), ElectricalCells=frozenset().union(*(Value.ElectricalCells for Value in Claims)))
        for Candidate in NewCandidates:
            PhysicalForeignPortalCandidateClaimsById[Candidate.CandidateId] = CompileCandidateClaims(Candidate)
        if not PhysicalForeignPortalDomainsPrepared:
            RawPortals = Cache.BuildPortalDictionary()
            CompleteKeys = frozenset(Cache.CompletePortalDomainKeys)
            for Signal, Profile in sorted(State.WholeDesignProfiles.items()):
                if Signal in ExactSignals:
                    continue
                FixedAccessNodes = frozenset((Position for Path in (Profile.SourceAccessPath, *(Profile.TargetAccessPaths[Target] for Target in Profile.Targets)) for Position in Path))
                for Terminal in (Profile.Root, *Profile.Targets):
                    Keys = tuple(((Signal, Terminal, Layer) for Layer in range(Cache.LayerCount)))
                    if not all((Key in CompleteKeys for Key in Keys)):
                        PhysicalForeignPortalIncompleteTerminalCount += 1
                        continue
                    PortalsById = {Portal.PortalId: Portal for Key in Keys for Portal in RawPortals.get(Key, ())}
                    if not PortalsById:
                        continue
                    PortalClaims = tuple((State.Resources.ResourceGraph.BuildRouteClaims(FixedAccessNodes | frozenset(Portal.Path)) for Portal in sorted(PortalsById.values(), key=lambda Value: Value.PortalId)))
                    PhysicalForeignPortalDomains.append((str(Signal), Terminal, PortalClaims))
            PhysicalForeignPortalCompleteIndexesByDomain.update({DomainIndex: frozenset(range(len(PortalClaims))) for DomainIndex, (_Signal, _Terminal, PortalClaims) in enumerate(PhysicalForeignPortalDomains)})
            MutableOccurrencesByClaimKind: tuple[Services.defaultdict[Services.Position3, set[tuple[int, int]]], ...] = tuple((Services.defaultdict(set) for _Index in range(4)))
            for DomainIndex, (_Signal, _Terminal, PortalClaims) in enumerate(PhysicalForeignPortalDomains):
                for PortalIndex, Claims in enumerate(PortalClaims):
                    Occurrence = (DomainIndex, PortalIndex)
                    for ClaimIndex, Cells in enumerate((Claims.WireCells, Claims.SupportCells, Claims.RequiredAirCells, Claims.ElectricalCells)):
                        for Cell in Cells:
                            MutableOccurrencesByClaimKind[ClaimIndex][Cell].add(Occurrence)
            for Target, Mutable in zip((PhysicalForeignPortalOccurrencesByWireCell, PhysicalForeignPortalOccurrencesBySupportCell, PhysicalForeignPortalOccurrencesByRequiredAirCell, PhysicalForeignPortalOccurrencesByElectricalCell), MutableOccurrencesByClaimKind, strict=True):
                Target.update({Cell: frozenset(Occurrences) for Cell, Occurrences in Mutable.items()})
            PhysicalForeignPortalDomainsPrepared = True
        UnaryCount = 0
        BinaryCount = 0
        ComparisonCount = 0
        for Candidate in NewCandidates:
            Literal = (Candidate.Signal, Candidate.CandidateId)
            CandidateUnaryPublished = False
            CandidateClaims = PhysicalForeignPortalCandidateClaimsById[Candidate.CandidateId]
            BlockedOccurrences: set[tuple[int, int]] = set()

            def AddBlockedOccurrences(Cells: Iterable[Position3], Index: Mapping[Position3, frozenset[tuple[int, int]]]) -> None:
                nonlocal ComparisonCount
                for Cell in Cells:
                    ComparisonCount += 1
                    BlockedOccurrences.update(Index.get(Cell, ()))
            AddBlockedOccurrences(CandidateClaims.WireCells | CandidateClaims.SupportCells | CandidateClaims.RequiredAirCells | CandidateClaims.ElectricalCells, PhysicalForeignPortalOccurrencesByWireCell)
            AddBlockedOccurrences(CandidateClaims.WireCells | CandidateClaims.RequiredAirCells, PhysicalForeignPortalOccurrencesBySupportCell)
            AddBlockedOccurrences(CandidateClaims.SupportCells, PhysicalForeignPortalOccurrencesByRequiredAirCell)
            AddBlockedOccurrences(CandidateClaims.WireCells, PhysicalForeignPortalOccurrencesByElectricalCell)
            BlockedIndexesByDomain: dict[int, set[int]] = Services.defaultdict(set)
            for DomainIndex, PortalIndex in BlockedOccurrences:
                BlockedIndexesByDomain[DomainIndex].add(PortalIndex)
            for DomainIndex in range(len(PhysicalForeignPortalDomains)):
                Blocked = frozenset(BlockedIndexesByDomain.get(DomainIndex, ()))
                PhysicalForeignPortalBlockedByCandidateAndDomain[Candidate.CandidateId, DomainIndex] = Blocked
                if Blocked == PhysicalForeignPortalCompleteIndexesByDomain[DomainIndex]:
                    Clause = frozenset((Literal,))
                    PhysicalForeignPortalUnaryRejectedCandidateIdsBySignal[Candidate.Signal].add(Candidate.CandidateId)
                    if Clause not in State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets:
                        State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(Clause)
                        PublishedPhysicalForeignPortalCandidateNoGoods.add(Clause)
                        UnaryCount += 1
                    CandidateUnaryPublished = True
            if CandidateUnaryPublished:
                continue
        NewCandidateIds = frozenset((Candidate.CandidateId for Candidate in NewCandidates))
        CandidateIdsBySignal = {str(Signal): frozenset((Candidate.CandidateId for Candidate in Candidates)) for Signal, Candidates in CandidateSets.items()}
        IndependentEmptyCandidateDomainSignals = tuple(sorted((Signal for Signal, CandidateIds in CandidateIdsBySignal.items() if CandidateIds and Signal in State.CompleteExteriorRouteDomainSignals and (CandidateIds <= frozenset(PhysicalForeignPortalUnaryRejectedCandidateIdsBySignal.get(Signal, ()))))))
        if not IndependentEmptyCandidateDomainSignals:
            for FirstIndex, First in enumerate(OrderedCandidates):
                if First.CandidateId in PhysicalForeignPortalUnaryRejectedCandidateIdsBySignal.get(First.Signal, ()):
                    continue
                FirstLiteral = (First.Signal, First.CandidateId)
                for Second in OrderedCandidates[FirstIndex + 1:]:
                    if First.Signal == Second.Signal:
                        continue
                    if First.CandidateId not in NewCandidateIds and Second.CandidateId not in NewCandidateIds:
                        continue
                    if Second.CandidateId in PhysicalForeignPortalUnaryRejectedCandidateIdsBySignal.get(Second.Signal, ()):
                        continue
                    SecondLiteral = (Second.Signal, Second.CandidateId)
                    Clause = frozenset((FirstLiteral, SecondLiteral))
                    if Clause in State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets:
                        continue
                    for DomainIndex in range(len(PhysicalForeignPortalDomains)):
                        FirstBlocked = PhysicalForeignPortalBlockedByCandidateAndDomain[First.CandidateId, DomainIndex]
                        SecondBlocked = PhysicalForeignPortalBlockedByCandidateAndDomain[Second.CandidateId, DomainIndex]
                        if not FirstBlocked or not SecondBlocked:
                            continue
                        if FirstBlocked | SecondBlocked == PhysicalForeignPortalCompleteIndexesByDomain[DomainIndex]:
                            State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(Clause)
                            PublishedPhysicalForeignPortalCandidateNoGoods.add(Clause)
                            BinaryCount += 1
                            break
        PhysicalForeignPortalCertifiedCandidateIds.update(NewCandidateIds)
        PhysicalForeignPortalCompatibilityCheckCount += ComparisonCount
        State.WorkTelemetry['PhysicalGlobalForeignPortalCandidateCertificates'] = {'ForeignTerminalDomainCount': len(PhysicalForeignPortalDomains), 'IncompleteForeignTerminalDomainCount': PhysicalForeignPortalIncompleteTerminalCount, 'CandidateCount': len(OrderedCandidates), 'NewCandidateCount': len(NewCandidates), 'CertifiedCandidateCount': len(PhysicalForeignPortalCertifiedCandidateIds), 'CompatibilityCheckCount': PhysicalForeignPortalCompatibilityCheckCount, 'IncrementalCompatibilityCheckCount': ComparisonCount, 'PublishedUnaryClauseCount': UnaryCount, 'PublishedBinaryClauseCount': BinaryCount, 'BinaryCompilationSkippedForUnaryEmptyCore': bool(IndependentEmptyCandidateDomainSignals), 'RetainedClauseCount': len(PublishedPhysicalForeignPortalCandidateNoGoods), 'IndependentEmptyCandidateDomainSignals': list(IndependentEmptyCandidateDomainSignals), 'Complete': PhysicalForeignPortalIncompleteTerminalCount == 0}

    def PublishPhysicalGlobalLocalAccessCandidateNoGoods(CandidateSets: Mapping[str, Iterable[NetRouteCandidate]]) -> None:
        """Pre-screen exact exterior choices against complete local access.

        A component-global candidate is unary-incompatible when it conflicts
        with every candidate in one complete owned-terminal domain.  Two
        exterior candidates are binary-incompatible when their combined
        blocker sets cover such a domain.  These certificates use the same
        exact claim predicate as final tree-frontier compilation, so they can
        be learned before assignment without materializing a local route.
        """
        if not State.Resources.PreparingPhysicalComponentGlobalChannels:
            return
        PhysicalProblem = State.Resources.PreparedComponentRoutingProblem
        if PhysicalProblem is None:
            return
        CompleteDomains = tuple((Domain for Domain in PhysicalProblem.OwnedTerminalDomains if Domain.Complete and Domain.Candidates))
        if not CompleteDomains:
            return
        OrderedCandidates = tuple((Candidate for Signal in sorted(CandidateSets) for Candidate in sorted(CandidateSets[Signal], key=lambda Value: Value.CandidateId)))
        BlockedByCandidateAndDomain: dict[tuple[str, int], frozenset[int]] = {}
        CompleteIndexesByDomain = {DomainIndex: frozenset(range(len(Domain.Candidates))) for DomainIndex, Domain in enumerate(CompleteDomains)}
        UnaryCount = 0
        BinaryCount = 0
        for Candidate in OrderedCandidates:
            Literal = (Candidate.Signal, Candidate.CandidateId)
            CandidateUnaryPublished = False
            for DomainIndex, Domain in enumerate(CompleteDomains):
                Blocked = frozenset() if Domain.Signal == Candidate.Signal else frozenset((AccessIndex for AccessIndex, Access in enumerate(Domain.Candidates) if Services.ComponentClaimsConflict(Candidate.Claims, Access.Claims)))
                BlockedByCandidateAndDomain[Candidate.CandidateId, DomainIndex] = Blocked
                if Blocked == CompleteIndexesByDomain[DomainIndex]:
                    Clause = frozenset((Literal,))
                    if Clause not in State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets:
                        State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(Clause)
                        PublishedPhysicalLocalAccessCandidateNoGoods.add(Clause)
                        UnaryCount += 1
                    CandidateUnaryPublished = True
            if CandidateUnaryPublished:
                continue
        for FirstIndex, First in enumerate(OrderedCandidates):
            FirstLiteral = (First.Signal, First.CandidateId)
            for Second in OrderedCandidates[FirstIndex + 1:]:
                if First.Signal == Second.Signal:
                    continue
                SecondLiteral = (Second.Signal, Second.CandidateId)
                Clause = frozenset((FirstLiteral, SecondLiteral))
                if Clause in State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets:
                    continue
                for DomainIndex in range(len(CompleteDomains)):
                    if BlockedByCandidateAndDomain[First.CandidateId, DomainIndex] | BlockedByCandidateAndDomain[Second.CandidateId, DomainIndex] == CompleteIndexesByDomain[DomainIndex]:
                        State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(Clause)
                        PublishedPhysicalLocalAccessCandidateNoGoods.add(Clause)
                        BinaryCount += 1
                        break
        State.WorkTelemetry['PhysicalGlobalLocalAccessCandidateCertificates'] = {'CompleteTerminalDomainCount': len(CompleteDomains), 'CandidateCount': len(OrderedCandidates), 'PublishedUnaryClauseCount': UnaryCount, 'PublishedBinaryClauseCount': BinaryCount, 'RetainedClauseCount': len(PublishedPhysicalLocalAccessCandidateNoGoods), 'Complete': True}

    def PlanAssignment(Values: list[tuple[Any, ...]] | None=None, *, AvoidExactNoGoods: bool=True) -> Any:
        nonlocal BaseValues
        if State.Resources.PreparingPhysicalComponentGlobalChannels:
            PublishPhysicalGlobalForeignPortalCandidateNoGoods(State.CandidatesBySignal)
            IndependentEmptySignals = tuple(sorted(map(str, State.WorkTelemetry.get('PhysicalGlobalForeignPortalCandidateCertificates', {}).get('IndependentEmptyCandidateDomainSignals', ()))))
            if IndependentEmptySignals:
                State.WorkTelemetry['PhysicalGlobalForeignPortalCandidateCertificates']['NativeAssignmentSkipped'] = True
                return Services.SimpleNamespace(Success=False, SelectedCandidateIds=(), ExpansionCount=0, CompletedWork=0, BudgetExhausted=False, DeadlineExceeded=False, ConflictSignals=IndependentEmptySignals)
        if Values is None:
            AssignmentCandidateSets = State.CandidatesBySignal
            PublishPhysicalGlobalLocalAccessCandidateNoGoods(AssignmentCandidateSets)
            if State.Resources.PreparingPhysicalComponentGlobalChannels:
                Services.BeginPhysicalAssignmentArcPass(PhysicalAssignmentArcTelemetry)
                PhysicalAssignmentArcTelemetry.update({'Applied': False, 'Complete': True, 'Reason': 'native-exact-assignment-authority', 'CandidateCounts': {Signal: len(Candidates) for Signal, Candidates in sorted(State.CandidatesBySignal.items())}})
            Values = State.EncodeCandidateValues(AssignmentCandidateSets)
            if State.Resources.PreparingPhysicalComponentGlobalChannels and any((not any((Value[0] == Signal for Value in Values)) for Signal in State.CandidatesBySignal)):
                CompleteBeforeAssignment = bool(State.WorkTelemetry.get('PhysicalGlobalCandidateDomainCompletion', {}).get('CompleteBeforeAssignment', False))
                if CompleteBeforeAssignment and PhysicalAssignmentArcTelemetry.get('EmptySignals'):
                    PhysicalAssignmentArcTelemetry['EmptyDomainProofComplete'] = True
                    return Services.SimpleNamespace(Success=False, SelectedCandidateIds=(), ExpansionCount=0, CompletedWork=0, BudgetExhausted=False, DeadlineExceeded=False, ConflictSignals=tuple(sorted({*map(str, PhysicalAssignmentArcTelemetry.get('EmptySignals', ())), *(str(Blocker) for Blockers in dict(PhysicalAssignmentArcTelemetry.get('BlockerSignalsByEmptySignal', {})).values() for Blocker in Blockers)})))
                else:
                    PhysicalAssignmentArcTelemetry['Applied'] = False
                    PhysicalAssignmentArcTelemetry['EncodingRemovedSignal'] = True
                    Values = State.EncodeCandidateValues(State.CandidatesBySignal)
        if BaseValues is None:
            BaseValues = []
            for Claim in BaseLocalClaims:
                Wire, Support, Air, Electrical = State.AssignmentIndexed.EncodeClaims(Claim.Claims)
                BaseValues.append((Claim.Signal, list(Wire), list(Support), list(Air), list(Electrical)))
            for Reservation in State.BoundaryLeaseReservations:
                Wire, Support, Air, Electrical = State.AssignmentIndexed.EncodeClaims(Reservation.Claims)
                BaseValues.append((Reservation.Signal, list(Wire), list(Support), list(Air), list(Electrical)))

        def PlanNative(NativeValues: list[tuple[Any, ...]]) -> Any:
            if BaseValues:
                Arguments = (NativeValues, BaseValues, len(State.AssignmentIndexed.ResourcePositions), State.AssignmentExpansionLimit)
                if hasattr(State.Context, 'PlanAuthoritativeRoutesWithBaseBounded'):
                    return State.Context.PlanAuthoritativeRoutesWithBaseBounded(*Arguments, Services.RemainingRoutingRuntimeMilliseconds(State.Deadline, State.AdaptiveExpiresAt))
                try:
                    return State.Context.PlanAuthoritativeRoutesWithBase(*Arguments, Services.RemainingRoutingRuntimeMilliseconds(State.Deadline, State.AdaptiveExpiresAt) / 1000.0)
                except TypeError as Error:
                    if 'positional arguments' not in str(Error):
                        raise
                    return State.Context.PlanAuthoritativeRoutesWithBase(*Arguments)
            Arguments = (NativeValues, len(State.AssignmentIndexed.ResourcePositions), State.AssignmentExpansionLimit)
            if hasattr(State.Context, 'PlanAuthoritativeRoutesBounded'):
                return State.Context.PlanAuthoritativeRoutesBounded(*Arguments, Services.RemainingRoutingRuntimeMilliseconds(State.Deadline, State.AdaptiveExpiresAt))
            try:
                return State.Context.PlanAuthoritativeRoutes(*Arguments, Services.RemainingRoutingRuntimeMilliseconds(State.Deadline, State.AdaptiveExpiresAt) / 1000.0)
            except TypeError as Error:
                if 'positional arguments' not in str(Error):
                    raise
                return State.Context.PlanAuthoritativeRoutes(*Arguments)
        if not State.Resources.PreparingPhysicalComponentGlobalChannels or not AvoidExactNoGoods:
            return PlanNative(Values)
        ResultValue = Services.PlanPhysicalGlobalAssignmentAvoidingExactNoGoods(Values, State.Resources.ForbiddenPhysicalComponentGlobalCandidateSets, PlanNative, DeadlineExpired=State.Deadline.IsExpired)
        State.WorkTelemetry['PhysicalGlobalNativeAssignmentResult'] = {'Success': bool(getattr(ResultValue, 'Success', False)), 'SelectedCandidateIds': [[str(Signal), str(CandidateId)] for Signal, CandidateId in getattr(ResultValue, 'SelectedCandidateIds', ())], 'ExpansionCount': int(getattr(ResultValue, 'ExpansionCount', 0)), 'CompletedWork': int(getattr(ResultValue, 'CompletedWork', 0)), 'BudgetExhausted': bool(getattr(ResultValue, 'BudgetExhausted', False)), 'DeadlineExceeded': bool(getattr(ResultValue, 'DeadlineExceeded', False)), 'FailureNet': getattr(ResultValue, 'FailureNet', None), 'ConflictSignals': [str(Signal) for Signal in getattr(ResultValue, 'ConflictSignals', ())], 'ConflictResourceIndices': [int(Index) for Index in getattr(ResultValue, 'ConflictResourceIndices', ())], 'PairwiseIncompatibleSignals': [[str(FirstSignal), str(SecondSignal)] for FirstSignal, SecondSignal in getattr(ResultValue, 'PairwiseIncompatibleSignals', ())], 'PairwiseCompatibilityComplete': bool(getattr(ResultValue, 'PairwiseCompatibilityComplete', False)), 'ExactNoGoodDomainExhausted': bool(getattr(ResultValue, 'ExactNoGoodDomainExhausted', False))}
        return ResultValue
    State.PlanAssignment = PlanAssignment

    def RaiseForNativeAssignmentDeadline(Result: Any) -> None:
        if not getattr(Result, 'DeadlineExceeded', False):
            return
        Services.EnforceRoutingRuntimeLimit(Deadline=State.Deadline, AdaptiveStartedAt=State.RoutingStarted, AdaptiveExpiresAt=State.AdaptiveExpiresAt, Stage='TrackAssignment', Diagnostics=State.CurrentRuntimeBudgetDiagnostics({'CompletedWork': getattr(Result, 'CompletedWork', 0)}), NativeDeadlineExceeded=True)
    State.RaiseForNativeAssignmentDeadline = RaiseForNativeAssignmentDeadline
    State.StageTimings['AssignmentPreparation'] = Services.monotonic() - State.AssignmentPreparationStarted
    if bool(Services.os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: assignment preparation elapsed={State.StageTimings['AssignmentPreparation']:.3f}s', flush=True)
    (PhysicalGlobalPreAssignmentCompletion): list[dict[str, object]] = []
    if State.Resources.PreparingPhysicalComponentGlobalChannels:
        RemainingBeforeAssignment = Services.BuildPhysicalRouteDescriptorRemainingCounts(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDomainFingerprintsBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal)
        for Signal in sorted(RemainingBeforeAssignment):
            RemainingCount = int(RemainingBeforeAssignment[Signal])
            Consumer = State.PhysicalGlobalCandidateSuffixConsumers.get(Signal)
            if RemainingCount <= 0 or Consumer is None:
                continue
            Record = Consumer(RemainingCount)
            PhysicalGlobalPreAssignmentCompletion.append(dict(Record))
            State.CandidateLookup.update({Candidate.CandidateId: Candidate for Candidate in State.CandidatesBySignal[Signal]})
            State.CheckRuntimeBudget('PhysicalComponentGlobalCandidateDomainCompletion')
        State.WorkTelemetry['PhysicalGlobalPreAssignmentDomainCompletion'] = {'Applied': bool(PhysicalGlobalPreAssignmentCompletion), 'Signals': [str(Record['Signal']) for Record in PhysicalGlobalPreAssignmentCompletion], 'ExecutedRequestCount': sum((int(Record['RequestCount']) for Record in PhysicalGlobalPreAssignmentCompletion)), 'Complete': not any(Services.BuildPhysicalRouteDescriptorRemainingCounts(State.Resources.PhysicalSignalRouteDomainContinuationCache, State.ApertureCandidateDomainIdentityBySignal, State.PhysicalRequestDomainFingerprintsBySignal, State.PhysicalRequestDescriptorFingerprintsBySignal).values())}
    State.AssignmentStarted = Services.monotonic()
    PublishPhysicalGlobalLocalAccessCandidateNoGoods(State.CandidatesBySignal)
    CandidateDomainFingerprint = Services.BuildTrackAssignmentCandidateDomainFingerprint(State.Resources, State.CandidatesBySignal, State.PreRouteLocalClaimChoicesBySignal)
    if State.PrepareRawTrackAssignmentDomainOnly:
        IncompleteReasons = tuple(sorted(((str(Signal), str(Reason)) for Signal, Reason in State.RawTrackAssignmentExtractionIncompleteReasons.items())))
        DeferredRequestCounts = tuple(sorted(((str(Signal), int(Diagnostics.get('DeferredRequests', 0))) for Signal, Diagnostics in State.CandidateDiagnostics.items() if int(Diagnostics.get('DeferredRequests', 0)) > 0)))
        IncompleteDeferredSignals = tuple((Signal for Signal, _Count in DeferredRequestCounts)) if State.Resources.PreparingPhysicalComponentGlobalChannels else ()
        IncompletePhysicalPreSiblingSignals = tuple(sorted(map(str, State.IncompletePreSiblingDomainSignals))) if State.Resources.PreparingPhysicalComponentGlobalChannels else ()
        IncompleteSignals = tuple(sorted({*(Signal for Signal, _Reason in IncompleteReasons), *IncompleteDeferredSignals, *IncompletePhysicalPreSiblingSignals}))
        RawDomainComplete = bool(not State.RouteTreeNativeDeadlineExceeded and (not State.Deadline.IsExpired()) and (not IncompleteSignals))
        RawDomainIncompleteReason = '' if RawDomainComplete else 'candidate-domain-incomplete'
        raise Services.RawTrackAssignmentDomainPrepared(Services.BuildRawTrackAssignmentDomain(Signals=tuple(sorted(State.Profiles)), CandidatesBySignal=State.CandidatesBySignal, LocalChoicesBySignal=State.PreRouteLocalClaimChoicesBySignal, BaseLocalClaims=BaseLocalClaims, BoundaryLeaseReservations=State.BoundaryLeaseReservations, AssignmentIndexed=State.AssignmentIndexed, CandidateDomainFingerprint=CandidateDomainFingerprint, LocalClaimDomainFingerprint=State.PreRouteLocalClaimDomainFingerprint, PlacementFingerprint=Services.BuildRawPortalPlacementGeometryFingerprint(State.Placed), ResourceGraphFingerprint=Services.BuildRawPortalResourceGeometryFingerprint(State.Resources), PortalDomainFingerprint=Services.BuildRawTrackAssignmentPortalDomainFingerprint(State.Portals, State.BoundaryLeaseReservations), Complete=RawDomainComplete, IncompleteReason=RawDomainIncompleteReason, MaximumAssignmentExpansions=State.AssignmentExpansionLimit, MinimizeMaximumRoutingLayer=State.Policy.TrackAssignment.MinimizeMaximumRoutingLayer, Diagnostics=(('CandidateRequestCount', State.CandidateRequestCount), ('RouteTreeNativeDeadlineExceeded', State.RouteTreeNativeDeadlineExceeded), ('IncompleteSignals', IncompleteSignals), ('ExcludedConfiguredRequestCounts', DeferredRequestCounts), ('DeferredRequestsRequireCompletion', State.Resources.PreparingPhysicalComponentGlobalChannels), ('CandidateExtractionIncompleteReasons', IncompleteReasons)), NativeAssignmentContext=State.EffectiveRawPortalCache.Context))
    State.LayerCappedAssignmentAttempts: list[dict[str, int | bool]] = []
    State.Result = None
    if State.FrozenTrackAssignmentPreparation is not None:
        FrozenSelections = tuple(((str(Signal), str(CandidateId)) for Signal, CandidateId in State.FrozenTrackAssignmentPreparation.SelectedCandidateIds))
        FrozenLocalSelections = tuple(((str(Signal), str(ChoiceId)) for Signal, ChoiceId in State.FrozenTrackAssignmentPreparation.SelectedLocalClaimChoiceIds))
        FrozenSignals = frozenset((Signal for Signal, _CandidateId in (*FrozenSelections, *FrozenLocalSelections)))
        CandidateIdsBySignal = {Signal: frozenset((Candidate.CandidateId for Candidate in Candidates)) for Signal, Candidates in State.CandidatesBySignal.items()}
        MissingFrozenSelections = tuple(sorted(((Signal, CandidateId) for Signal, CandidateId in FrozenSelections if CandidateId not in CandidateIdsBySignal.get(Signal, ()))))
        MissingFrozenLocalSelections = tuple(sorted(((Signal, ChoiceId) for Signal, ChoiceId in FrozenLocalSelections if ChoiceId not in {Choice.ChoiceId for Choice in State.PreRouteLocalClaimChoicesBySignal.get(Signal, ())})))
        if FrozenSignals != frozenset(State.CandidatesBySignal) or len(FrozenSignals) != len(FrozenSelections) + len(FrozenLocalSelections) or MissingFrozenSelections or MissingFrozenLocalSelections or (bool(State.FrozenTrackAssignmentPreparation.LocalClaimDomainFingerprint) and State.FrozenTrackAssignmentPreparation.LocalClaimDomainFingerprint != State.PreRouteLocalClaimDomainFingerprint) or (bool(State.FrozenTrackAssignmentPreparation.CandidateDomainFingerprint) and State.FrozenTrackAssignmentPreparation.CandidateDomainFingerprint != CandidateDomainFingerprint):
            raise State.StructuredRoutingStageError(Services.RoutingFailure(Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='FrozenTrackAssignmentHandoff', RepairActions=(), Detail='the authoritative candidate domain no longer contains the pre-placement capacity witness', Diagnostics={'Complete': False, 'FrozenSignalCount': len(FrozenSignals), 'RoutedSignalCount': len(State.CandidatesBySignal), 'MissingFrozenSelections': [list(Value) for Value in MissingFrozenSelections[:16]], 'MissingFrozenLocalSelections': [list(Value) for Value in MissingFrozenLocalSelections[:16]], 'FrozenLocalClaimDomainFingerprint': State.FrozenTrackAssignmentPreparation.LocalClaimDomainFingerprint, 'CurrentLocalClaimDomainFingerprint': State.PreRouteLocalClaimDomainFingerprint, 'FrozenCandidateDomainFingerprint': State.FrozenTrackAssignmentPreparation.CandidateDomainFingerprint, 'CurrentCandidateDomainFingerprint': CandidateDomainFingerprint}))
        State.Result = Services.SimpleNamespace(Success=True, SelectedCandidateIds=(*FrozenSelections, *FrozenLocalSelections), ExpansionCount=0, ConflictSignals=(), ConflictResourceIndices=(), BudgetExhausted=False, DeadlineExceeded=False)
        State.WorkTelemetry['PrePlacementTrackAssignmentHandoff'] = {'Applied': True, 'SelectedSignalCount': len(FrozenSignals), 'SelectedLocalClaimChoiceCount': len(FrozenLocalSelections), 'PrePlacementExpansionCount': State.FrozenTrackAssignmentPreparation.ExpansionCount, 'NativeAssignmentExpansionCount': 0}
    if State.Result is None and State.Policy.TrackAssignment.MinimizeMaximumRoutingLayer and all(State.CandidatesBySignal.values()):
        MinimumFeasibleLayer = max((min((Candidate.Layer for Candidate in Values)) for Values in State.CandidatesBySignal.values()))
        MaximumCandidateLayer = max((Candidate.Layer for Values in State.CandidatesBySignal.values() for Candidate in Values))
        for MaximumAssignedLayer in range(MinimumFeasibleLayer, MaximumCandidateLayer + 1):
            LayerCappedCandidateSets = {Signal: [Candidate for Candidate in Values if Candidate.Layer <= MaximumAssignedLayer] for Signal, Values in State.CandidatesBySignal.items()}
            LayerCappedValues = State.EncodeCandidateValues(LayerCappedCandidateSets)
            if not LayerCappedValues or any((not any((Value[0] == Signal for Value in LayerCappedValues)) for Signal in State.CandidatesBySignal)):
                continue
            State.Result = State.PlanAssignment(LayerCappedValues)
            State.RaiseForNativeAssignmentDeadline(State.Result)
            State.LayerCappedAssignmentAttempts.append({'MaximumAssignedLayer': MaximumAssignedLayer, 'Success': bool(State.Result.Success), 'ExpansionCount': int(State.Result.ExpansionCount)})
            if State.Result.Success:
                break
            if Services.ShouldGrowAssignmentBudget(State.Result):
                State.Result = None
                break
    if State.Result is None:
        State.Result = State.PlanAssignment()
        State.RaiseForNativeAssignmentDeadline(State.Result)
    if State.PrepareTrackAssignmentOnly:
        SelectedLocalClaimChoiceIds = tuple(sorted(((str(Signal), str(CandidateId)) for Signal, CandidateId in State.Result.SelectedCandidateIds if str(CandidateId) in State.PreRouteLocalClaimChoiceById)))
        SelectedCapacityResourceIds = tuple(sorted({str(ResourceId) for Signal, CandidateId in State.Result.SelectedCandidateIds for ResourceId in (State.PreRouteLocalClaimChoiceById[str(CandidateId)].Claim.Claims.ResourceIds if str(CandidateId) in State.PreRouteLocalClaimChoiceById else State.CandidateLookup[str(CandidateId)].Claims.ResourceIds if str(CandidateId) in State.CandidateLookup else ())}))
        raise Services.TrackAssignmentPrepared(Services.TrackAssignmentPreparation(Success=bool(State.Result.Success), SelectedCandidateIds=tuple(sorted(((str(Signal), str(CandidateId)) for Signal, CandidateId in State.Result.SelectedCandidateIds if str(CandidateId) not in State.PreRouteLocalClaimChoiceById))), CandidateCounts=tuple(sorted(((str(Signal), len(Candidates) + len(State.PreRouteLocalClaimChoicesBySignal.get(str(Signal), ()))) for Signal, Candidates in State.CandidatesBySignal.items()))), ConflictSignals=tuple(sorted(map(str, getattr(State.Result, 'ConflictSignals', ())))), ConflictResourceIndices=tuple(sorted(map(int, getattr(State.Result, 'ConflictResourceIndices', ())))), ExpansionCount=int(State.Result.ExpansionCount), Complete=not bool(getattr(State.Result, 'BudgetExhausted', False) or getattr(State.Result, 'DeadlineExceeded', False)), SelectedLocalClaimChoiceIds=SelectedLocalClaimChoiceIds, LocalClaimDomainFingerprint=State.PreRouteLocalClaimDomainFingerprint, CandidateDomainFingerprint=CandidateDomainFingerprint, SelectedCapacityResourceIds=SelectedCapacityResourceIds))
    return PhaseOutcome()

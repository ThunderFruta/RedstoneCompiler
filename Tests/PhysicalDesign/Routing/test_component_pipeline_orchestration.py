"""Orchestration contracts for component pipeline."""

from ._component_pipeline_contracts import *


def test_pre_route_fabric_summary_exposes_frontier_not_all_stub_claims():
    """Failure artifacts keep the proof frontier without duplicating stubs."""
    IncompleteDomain = SimpleNamespace(
        Signal="Signal",
        Terminal=(1, 2, 3),
        EscapeStubs=(object(), object()),
        Complete=False,
        IncompleteReason="no-legal-fabric-escape",
    )
    CompleteDomain = SimpleNamespace(
        Signal="Other",
        Terminal=(4, 5, 6),
        EscapeStubs=(object(),),
        Complete=True,
        IncompleteReason="",
    )
    Fabric = SimpleNamespace(
        FabricFingerprint="fabric",
        TopologyKind="derived-perimeter-access-v1",
        Complete=False,
        IncompleteReason="no-legal-fabric-escape",
        AccessRingTrackCount=1,
        AccessRingFingerprint="ring",
        OuterBounds=(-2, -3, 8, 9),
        ActiveFaces=("north", "south"),
        Nodes=((0, 1, 0),),
        Edges=(),
        CapacityResourceIds=("resource",),
        TerminalDomains=(IncompleteDomain, CompleteDomain),
        LegalEscapeExpansionCount=41,
        LegalEscapeExpansionLimit=64,
        LegalEscapeWorkLimitKind="derived-direction-state-v1",
        LegalEscapeDirectionStateUpperBound=128,
        PhysicalClaims=SimpleNamespace(
            WireCells=frozenset({(0, 1, 0)}),
            SupportCells=frozenset(),
            RequiredAirCells=frozenset(),
            ElectricalCells=frozenset(),
        ),
    )

    Summary = SummarizePreRouteAccessFabric(Fabric)

    assert Summary is not None
    assert Summary["TerminalDomainCount"] == 2
    assert Summary["CompleteTerminalDomainCount"] == 1
    assert Summary["LegalEscapeExpansionLimit"] == 64
    assert Summary["LegalEscapeWorkLimitKind"] == "derived-direction-state-v1"
    assert Summary["LegalEscapeDirectionStateUpperBound"] == 128
    assert Summary["IncompleteTerminalDomains"] == [{
        "Signal": "Signal",
        "Terminal": [1, 2, 3],
        "EscapeStubCount": 2,
        "IncompleteReason": "no-legal-fabric-escape",
    }]
    assert "TerminalDomains" not in Summary

def test_prepare_track_assignment_stops_before_route_tree_construction(
    monkeypatch: pytest.MonkeyPatch,
):
    Expected = TrackAssignmentPreparation(
        Success=True,
        SelectedCandidateIds=(("Signal", "candidate"),),
        CandidateCounts=(("Signal", 1),),
        ConflictSignals=(),
        ConflictResourceIndices=(),
        ExpansionCount=1,
        Complete=True,
    )
    Calls: list[dict[str, object]] = []

    def Prepare(*_Arguments: object, **KeywordArguments: object) -> None:
        Calls.append(dict(KeywordArguments))
        raise TrackAssignmentPrepared(Expected)

    monkeypatch.setattr(Pcb, "BuildPcbRoutingConfigurations", lambda _Value: (object(),))
    monkeypatch.setattr(Pcb, "RoutePcbAttempt", Prepare)

    Actual = Pcb.PrepareTrackAssignment(
        SimpleNamespace(),
        Resources=SimpleNamespace(),
        Policy=DefaultPhysicalDesignPolicy,
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert Actual is Expected
    assert len(Calls) == 1
    assert Calls[0]["Policy"] is DefaultPhysicalDesignPolicy
    assert Calls[0]["PrepareTrackAssignmentOnly"] is True
    assert Calls[0][
        "DeferClusterBoundaryLeaseUntilCapacityPrecheck"
    ] is False
    assert isinstance(Calls[0]["Deadline"], RoutingDeadline)

    Pcb.PrepareTrackAssignment(
        SimpleNamespace(),
        Resources=SimpleNamespace(),
        Policy=DefaultPhysicalDesignPolicy,
        Deadline=RoutingDeadline.Start(1.0),
        DeferClusterBoundaryLeaseUntilCapacityPrecheck=True,
    )
    assert Calls[1][
        "DeferClusterBoundaryLeaseUntilCapacityPrecheck"
    ] is True

def test_translated_port_options_with_portable_alias_project_distinct_apertures():
    FactorDomain, Certificate = _PhysicalPairApertureProjectionFixture()

    Clauses, Diagnostics = (
        ProjectCompletePhysicalPortPairCertificateToApertureClauses(
            FactorDomain,
            Certificate,
        )
    )

    assert frozenset((("Alpha", "alpha-absolute-left"),)) in Clauses
    assert frozenset((("Alpha", "alpha-absolute-right"),)) in Clauses
    assert all(
        "portable-reservation" not in Fingerprint
        for Clause in Clauses
        for _Signal, Fingerprint in Clause
    )
    assert Diagnostics["ApertureProjectionComplete"] is True
    assert Diagnostics["ApertureProjectionOptionCount"] == 3

def test_detailed_failure_rejects_exact_channels_not_port_assignment():
    Plan = SimpleNamespace(
        PlanFingerprint="plan",
        PortAssignmentFingerprint="ports",
        Channels=(
            SimpleNamespace(Signal="A", RouteCandidateId="route-a"),
            SimpleNamespace(Signal="B", RouteCandidateId="route-b"),
        ),
    )
    Design = SimpleNamespace(
        RoutingAssignment=SimpleNamespace(SelectedCandidates={
            "A": SimpleNamespace(CandidateId="route-a"),
            "B": SimpleNamespace(CandidateId="route-b"),
        }),
    )
    Resources = SimpleNamespace(
        ForbiddenPhysicalComponentGlobalCandidateSets=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )

    Diagnostics = RecordPhysicalComponentDetailedRoutingNoGood(
        Plan,
        Design,
        Resources,
    )

    assert Resources.ForbiddenPhysicalComponentGlobalCandidateSets == {
        frozenset((("A", "route-a"), ("B", "route-b")))
    }
    assert Resources.RejectedPhysicalComponentAssemblyPlanFingerprints == {
        "plan"
    }
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Diagnostics["ForbiddenGlobalCandidateSet"] == [
        ["A", "route-a"],
        ["B", "route-b"],
    ]
    assert Diagnostics["RejectedPhysicalAssemblyPlanFingerprint"] == "plan"
    assert Diagnostics["PortAssignmentRejected"] is False
    with pytest.raises(ValueError, match="assignment identity mismatch"):
        RecordPhysicalComponentDetailedRoutingNoGood(
            SimpleNamespace(
                PlanFingerprint="different-plan",
                PortAssignmentFingerprint="ports",
                Channels=(SimpleNamespace(
                    Signal="A",
                    RouteCandidateId="different",
                ),),
            ),
            Design,
            Resources,
        )

def test_typed_straight_access_native_batch_requires_bulk_work():
    Select = (
        AuthoritativePortalPreparation
        .ShouldUseNativeTypedStraightClaimBatch
    )

    assert not Select(
        NativeAvailable=True,
        WorkItemCount=18,
        WirePositionCount=54,
    )
    assert Select(
        NativeAvailable=True,
        WorkItemCount=64,
        WirePositionCount=512,
    )
    assert not Select(
        NativeAvailable=False,
        WorkItemCount=64,
        WirePositionCount=512,
    )

def test_local_incidence_color_ignores_downstream_signal_names():
    InputKind = SimpleNamespace(value="INPUT")
    NandKind = SimpleNamespace(value="NAND")
    Module = SimpleNamespace(
        Inputs=("A", "B"),
        Outputs=(),
        Gates=(
            SimpleNamespace(
                Kind=InputKind,
                Inputs=(),
                Outputs=("A",),
            ),
            SimpleNamespace(
                Kind=InputKind,
                Inputs=(),
                Outputs=("B",),
            ),
            SimpleNamespace(
                Kind=NandKind,
                Inputs=("A", "X"),
                Outputs=("DifferentA",),
            ),
            SimpleNamespace(
                Kind=NandKind,
                Inputs=("B", "Y"),
                Outputs=("DifferentB",),
            ),
        ),
    )

    Colors = PlacementFeedback.BuildSignalLocalIncidenceFingerprints(Module)

    assert Colors["A"] == Colors["B"]

def test_candidate_stage_portal_progress_builds_resumable_cursor():
    Cursor, CompletedWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            {
                "UnderlyingFailure": {
                    "Diagnostics": {
                        "PortalCompletedWork": 28,
                        "PortalRequestCount": 40,
                        "PortalCacheMode": "partial-signal",
                        "RawPortalResourceCacheSelected": True,
                    },
                },
            },
        )
    )

    assert CompletedWork == 28
    assert Cursor is not None
    assert Cursor.PlanFingerprint == "plan-a"
    assert Cursor.ApertureDomainFingerprint == "aperture-a"
    assert Cursor.CompletedWork == 28

def test_descriptor_retention_keeps_full_two_signal_universe_across_rollover():
    def Continuation(AlphaCompleted, BetaCompleted):
        Diagnostics = {
            "PhysicalSignalRouteDomainDescriptorProgress": {
                "Alpha": {
                    "PreSiblingDomainFingerprint": "pre-alpha",
                    "RequestDomainFingerprint": "request-alpha",
                    "DescriptorUniverseFingerprint": "universe-alpha",
                    "DescriptorCount": 2,
                    "CompletedDescriptorCount": len(AlphaCompleted),
                    "CompletedDescriptorFingerprints": list(
                        AlphaCompleted
                    ),
                },
                "Beta": {
                    "PreSiblingDomainFingerprint": "pre-beta",
                    "RequestDomainFingerprint": "request-beta",
                    "DescriptorUniverseFingerprint": "universe-beta",
                    "DescriptorCount": 2,
                    "CompletedDescriptorCount": len(BetaCompleted),
                    "CompletedDescriptorFingerprints": list(
                        BetaCompleted
                    ),
                },
            },
        }
        Cursor, CompletedWork = (
            BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
                "plan-a",
                "aperture-a",
                Diagnostics,
            )
        )
        assert Cursor is not None
        return AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            SimpleNamespace(PlanFingerprint="plan-a", Ports=()),
            {
                "Alpha": "request-alpha",
                "Beta": "request-beta",
            },
            {
                "Alpha": 2 - len(AlphaCompleted),
                "Beta": 2 - len(BetaCompleted),
            },
            (),
            ("aperture-a",),
            CompletedWork=CompletedWork,
            ResumeCursor=Cursor,
        )

    First = Continuation(("alpha-0",), ())
    Second = Continuation(("alpha-0",), ("beta-1",))
    assert (
        First.ResumeCursor.State.UniverseIdentities
        == Second.ResumeCursor.State.UniverseIdentities
    )

    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=Second,
        ExistingEntry=SimpleNamespace(Continuation=First),
    )

    assert Admission["Retained"] is True
    assert Admission["DescriptorCompletedSetSuperset"] is True
    assert Admission["DescriptorStrictAddition"] is True

def test_portable_conversion_publishes_exact_full_universe_before_retry():
    Cache = {}
    PortableCandidate = SimpleNamespace(
        CandidateId="portable-alpha",
        Payload="translated",
    )
    Alpha, _Advanced = (
        AuthoritativeCandidateGuides
        .RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint="pre-alpha",
            Signal="Alpha",
            RequestDomainFingerprint="request-alpha",
            RequestDescriptorFingerprints=("alpha-0", "alpha-1"),
            CompletedDescriptorFingerprints=("alpha-0", "alpha-1"),
            Candidates=(PortableCandidate,),
            CandidateMetadata={
                "portable-alpha": ("X", 0, 0, 2),
            },
        )
    )
    Beta, _Advanced = (
        AuthoritativeCandidateGuides
        .RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint="pre-beta",
            Signal="Beta",
            RequestDomainFingerprint="request-beta",
            RequestDescriptorFingerprints=("beta-0", "beta-1"),
            CompletedDescriptorFingerprints=(),
            Candidates=(),
            CandidateMetadata={},
        )
    )
    FirstDiagnostics = {
        "PhysicalSignalRouteDomainDescriptorProgress": {
            "Alpha": {
                **Alpha.ToProgressDictionary(),
                "PortableReplayProvenance": True,
            },
            "Beta": Beta.ToProgressDictionary(),
        },
    }

    ReplayedAlpha = (
        AuthoritativeCandidateGuides
        .SelectReplayablePhysicalSignalRouteDomainContinuation(
            Cache,
            "pre-alpha",
            "Alpha",
            "request-alpha",
            ("alpha-0", "alpha-1"),
        )
    )
    assert ReplayedAlpha is Alpha
    BetaAdvanced, StrictlyAdvanced = (
        AuthoritativeCandidateGuides
        .RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint="pre-beta",
            Signal="Beta",
            RequestDomainFingerprint="request-beta",
            RequestDescriptorFingerprints=("beta-0", "beta-1"),
            CompletedDescriptorFingerprints=("beta-1",),
            Candidates=(),
            CandidateMetadata={},
        )
    )
    assert StrictlyAdvanced
    SecondDiagnostics = {
        "PhysicalSignalRouteDomainDescriptorProgress": {
            "Alpha": {
                **ReplayedAlpha.ToProgressDictionary(),
                "PortableReplayProvenance": False,
            },
            "Beta": BetaAdvanced.ToProgressDictionary(),
        },
    }

    def Continuation(Diagnostics):
        Cursor, CompletedWork = (
            BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
                "plan-a",
                "aperture-a",
                Diagnostics,
            )
        )
        assert Cursor is not None
        return AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            SimpleNamespace(PlanFingerprint="plan-a", Ports=()),
            {
                "Alpha": "request-alpha",
                "Beta": "request-beta",
            },
            {
                "Alpha": 0,
                "Beta": (
                    2
                    - len(BetaAdvanced.CompletedDescriptorFingerprints)
                    if Diagnostics is SecondDiagnostics
                    else 2
                ),
            },
            (),
            ("aperture-a",),
            CompletedWork=CompletedWork,
            ResumeCursor=Cursor,
        )

    First = Continuation(FirstDiagnostics)
    Second = Continuation(SecondDiagnostics)
    assert (
        First.ResumeCursor.State.UniverseIdentities
        == Second.ResumeCursor.State.UniverseIdentities
    )
    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=Second,
        ExistingEntry=SimpleNamespace(Continuation=First),
    )
    assert Admission["Retained"] is True
    assert Admission["DescriptorStrictAddition"] is True

def test_descriptor_retention_rejects_equal_cardinality_different_sets():
    First = _DescriptorContinuation(("descriptor-0",))
    Existing = SimpleNamespace(Continuation=First)
    DifferentSet = _DescriptorContinuation(("descriptor-1",))

    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=DifferentSet,
        ExistingEntry=Existing,
    )

    assert Admission["Retained"] is False
    assert Admission["Reason"] == "descriptor-completion-is-not-a-superset"

def test_raw_route_lru_growth_is_not_descriptor_progress():
    Cursor, CompletedWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            {
                "RouteTreeCompletedWork": 23,
                "PhysicalComponentGlobalCandidateContinuations": [{
                    "Signal": "SignalA",
                    "ExecutedRequestCount": 23,
                    "RemainingRequestCount": 40,
                    "MaterializedCandidateCount": 2,
                }],
                "PhysicalGlobalRouteTreeResultCache": {
                    "DescriptorCount": 63,
                    "StoredResultCount": 23,
                    "StoredResultCountAfterDeadlineRetention": 23,
                },
            },
        )
    )

    assert Cursor is None
    assert CompletedWork == 0

def test_descriptor_progress_ignores_raw_route_lru_growth():
    FirstCursor, FirstWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            _DescriptorProgressDiagnostics(
                ("descriptor-0",),
                StoredRouteResults=1,
            ),
        )
    )
    GrownCursor, GrownWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            _DescriptorProgressDiagnostics(
                ("descriptor-0",),
                StoredRouteResults=99,
            ),
        )
    )

    assert FirstCursor is not None and GrownCursor is not None
    assert FirstWork == GrownWork == 1
    assert FirstCursor.CursorFingerprint == GrownCursor.CursorFingerprint

def test_descriptor_retention_two_signal_rollover_requires_zero_seeded_universe():
    def Diagnostics(*, IncludeSignalB, CompletedB=()):
        Progress = {
            "SignalA": {
                "PreSiblingDomainFingerprint": "pre-sibling-a",
                "RequestDomainFingerprint": "request-a",
                "DescriptorUniverseFingerprint": "universe-a",
                "DescriptorCount": 2,
                "CompletedDescriptorCount": 1,
                "CompletedDescriptorFingerprints": ["a-0"],
            },
        }
        if IncludeSignalB:
            Progress["SignalB"] = {
                "PreSiblingDomainFingerprint": "pre-sibling-b",
                "RequestDomainFingerprint": "request-b",
                "DescriptorUniverseFingerprint": "universe-b",
                "DescriptorCount": 2,
                "CompletedDescriptorCount": len(CompletedB),
                "CompletedDescriptorFingerprints": list(CompletedB),
            }
        return {
            "PhysicalSignalRouteDomainDescriptorProgress": Progress,
        }

    def Continuation(DiagnosticsValue):
        Cursor, CompletedWork = (
            BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
                "plan-a",
                "aperture-a",
                DiagnosticsValue,
            )
        )
        assert Cursor is not None
        return AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            SimpleNamespace(PlanFingerprint="plan-a", Ports=()),
            {"SignalA": "request-a", "SignalB": "request-b"},
            {"SignalA": 1, "SignalB": 1},
            (),
            ("aperture-a",),
            CompletedWork=CompletedWork,
            ResumeCursor=Cursor,
        )

    ZeroSeededFirst = Continuation(Diagnostics(
        IncludeSignalB=True,
    ))
    Second = Continuation(Diagnostics(
        IncludeSignalB=True,
        CompletedB=("b-1",),
    ))
    Aperture = {"DomainFingerprint": "aperture-a", "Complete": True}

    Admitted = ClassifyPhysicalGlobalPlanRetentionAdmission(
        Aperture,
        Continuation=Second,
        ExistingEntry=SimpleNamespace(Continuation=ZeroSeededFirst),
    )

    assert Admitted["Retained"] is True
    assert Admitted["DescriptorStrictAddition"] is True

    OmittedSignalFirst = Continuation(Diagnostics(
        IncludeSignalB=False,
    ))
    Rejected = ClassifyPhysicalGlobalPlanRetentionAdmission(
        Aperture,
        Continuation=Second,
        ExistingEntry=SimpleNamespace(Continuation=OmittedSignalFirst),
    )

    assert Rejected["Retained"] is False
    assert Rejected["Reason"] == (
        "descriptor-universe-or-identity-mismatch"
    )

@pytest.mark.parametrize(
    "DeferLocalCompositeSelection",
    (False, True),
)
def test_deferred_boundary_selection_preserves_global_only_stage_ownership(
    monkeypatch,
    DeferLocalCompositeSelection,
):
    Preparation = SimpleNamespace(
        LocalAccessFactorsBySignal=(("Signal", ("local",)),),
        ApertureFactorsBySignal=(("Signal", ("aperture",)),),
        LocalApertureSupportBySignal=(("Signal", ("support",)),),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentBoundaryAssignmentFingerprints=set(),
        PreferredPhysicalComponentGlobalContractsBySignal={},
        PreferredPhysicalComponentApertureContractsBySignal={},
        PreferredPhysicalComponentPortReservationsBySignal={},
        PhysicalComponentAperturePortalSlackBySignal={},
        PhysicalBoundaryPairSupportCache={},
    )
    Calls = []

    def IterClosed(_Preparation, **Keywords):
        Calls.append(("closed", Keywords))
        return iter(())

    def IterGlobal(_Domains, **Keywords):
        Calls.append(("global", Keywords))
        return iter(())

    monkeypatch.setattr(
        PhysicalPortSearch,
        "IterClosedComponentContracts",
        IterClosed,
    )
    monkeypatch.setattr(
        PhysicalPortSearch,
        "IterPhysicalBoundaryPortAssignments",
        IterGlobal,
    )

    assert tuple(PhysicalPortSearch.IterPreparedPhysicalBoundaryAssignments(
        Preparation,
        Resources,
        {"Signal": ()},
        (),
        (),
        "solver-domain",
        SimpleNamespace(Technology=SimpleNamespace(TrackPitch=3)),
        lambda _Diagnostics: None,
        None,
        DeferLocalCompositeSelection=DeferLocalCompositeSelection,
    )) == ()

    assert Calls[0][1]["IncludeLocalCompositeFactors"] is (
        not DeferLocalCompositeSelection
    )
    for Key in (
        "LocalAccessFactorsBySignal",
        "ApertureFactorsBySignal",
        "LocalApertureSupportBySignal",
    ):
        assert Calls[1][1][Key] is not None
    assert Calls[1][1]["CertifiedNoGoodProjectionOnly"] is (
        DeferLocalCompositeSelection
    )
    assert Calls[1][1]["PersistentPairSupportCache"] is (
        Resources.PhysicalBoundaryPairSupportCache
    )

def test_global_route_reuse_ignores_local_port_contract_only():
    def Plan(LocalPath, GlobalPath=((2, 2, 0), (2, 2, -1))):
        Port = SimpleNamespace(
            Signal="Alpha",
            Direction="input",
            Attachment=(2, 2, 0),
            GlobalPath=GlobalPath,
            LocalPath=LocalPath,
            Capacity=1,
        )
        Channel = SimpleNamespace(
            Signal="Alpha",
            Layer=0,
            GuideCells=((2, 0), (2, -1)),
            Capacity=1,
            FeedthroughComponentIds=(),
            ReservationFingerprint="channel-alpha",
        )
        return SimpleNamespace(
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="resource",
            TechnologyFingerprint="technology",
            EnvelopeMinimum=(0, 0, 0),
            EnvelopeMaximum=(4, 4, 4),
            GlobalKeepoutFingerprint="keepout",
            Ports=(Port,),
            PlanningChannels=(Channel,),
            Feedthroughs=(),
        )

    First = Plan(((0, 2, 0), (2, 2, 0)))
    LocalChanged = Plan(((0, 3, 0), (2, 2, 0)))
    GlobalChanged = Plan(
        ((0, 2, 0), (2, 2, 0)),
        ((2, 2, 0), (3, 2, 0)),
    )

    assert PhysicalAssemblyGlobalRouteCanBeRebound(First, LocalChanged)
    assert not PhysicalAssemblyGlobalRouteCanBeRebound(
        First,
        GlobalChanged,
    )
    assert BuildPhysicalAssemblyGlobalReuseFingerprint(First).startswith(
        "global-assembly-reuse-v1:"
    )

def test_foreign_dependent_mandatory_pair_cannot_project_port_subset():
    def Port(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Capacity=1,
            Attachment=(X, 2, 0),
            GlobalPath=((X, 2, 0), (X, 2, -1)),
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ReservationFingerprint=f"reservation-{Signal}",
        )

    Plan = SimpleNamespace(
        PlanFingerprint="foreign-dependent-plan",
        PortAssignmentFingerprint="foreign-dependent-assignment",
        Ports=(Port("PortA", 0), Port("PortB", 2), Port("Unrelated", 4)),
        Feedthroughs=(),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="InitialCandidateAssignment",
            AffectedNets=("PortA", "PortB"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "MandatoryAccessProof": {
                    "Kind": "generated-fixed-portal-domain-exhausted",
                    "Complete": True,
                    "PortalTupleDomainComplete": True,
                    "ProofScope": "complete-portal-tuple-domain",
                    "BudgetExhausted": False,
                    "DeadlineExceeded": False,
                },
                # The observed edge exists, but its exact certificate also
                # depended on a frozen foreign owner and was not promoted.
                "PairwisePortReservationNoGoodProofComplete": False,
                "PairwisePortReservationNoGoodEdges": [],
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": ["PortA", "PortB"],
                    "CongestionCutSignals": ["PortA", "PortB"],
                    "PairwiseIncompatibleEdges": [["PortA", "PortB"]],
                },
            },
        ),
        Plan,
        DeadlineExpired=False,
    )

    assert not Failure.Diagnostics[
        "MandatoryPairDependencyIdentityComplete"
    ]
    assert not Failure.Diagnostics[
        "AssemblyPlanDependencyIdentityComplete"
    ]
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )
    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    Clause = next(iter(
        Resources.RejectedPhysicalComponentPortReservationSets
    ))
    assert {Signal for Signal, _Fingerprint in Clause} == {
        "PortA",
        "PortB",
        "Unrelated",
    }
    assert Diagnostics["NoGoodScope"] == (
        "exact-assembly-port-aperture-set"
    )
    assert not Diagnostics[
        "AssemblyPlanDependencyProjectionProofComplete"
    ]

def test_global_contract_recommendation_mixes_one_certified_family():
    Domains = tuple(
        replace(
            Domain,
            PortableRequestFamilyFingerprint="portable-family",
        )
        for Domain in _MixedPhysicalCorridorDomains()
    )
    Recommendation = SelectPhysicalComponentGlobalContractRecommendation(
        Domains,
        ("A", "B"),
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        PortableRequestFamilyFingerprint="portable-family",
    )

    assert Recommendation is not None
    assert {
        Signal: Factor.PortGlobalContractFingerprint
        for Signal, Factor in Recommendation.items()
    } == {
        "A": "global-a-1",
        "B": "global-b-2",
    }

def test_global_contract_recommendation_rejects_forbidden_mixed_tuple():
    Domains = tuple(
        replace(
            Domain,
            PortableRequestFamilyFingerprint="portable-family",
        )
        for Domain in _MixedPhysicalCorridorDomains()
    )
    Recommendation = SelectPhysicalComponentGlobalContractRecommendation(
        Domains,
        ("A", "B"),
        RejectedSets=(frozenset((
            ("A", "global-a-1"),
            ("B", "global-b-2"),
        )),),
        CompatibilityCache={},
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        PortableRequestFamilyFingerprint="portable-family",
    )

    assert Recommendation is not None
    assert {
        Signal: Factor.PortGlobalContractFingerprint
        for Signal, Factor in Recommendation.items()
    } == {
        "A": "global-a-2",
        "B": "global-b-1",
    }

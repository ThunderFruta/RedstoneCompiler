"""Cache Lifetime contracts for component pipeline."""

from ._component_pipeline_contracts import *


def test_singleton_repair_transition_key_requires_exact_geometry_identity():
    Build = (
        PlacementPhysicalAssembly
        .BuildSingletonLocalFactorRepairTransitionKey
    )

    First = Build(
        "NandNet4",
        1,
        {
            "SelectedClusterIndices": [3],
            "InvalidatedSignals": [
                "Propagate1",
                "NandNet4",
                "NandNet5",
            ],
        },
    )

    assert First == Build(
        "NandNet4",
        1,
        {
            "SelectedClusterIndices": [3],
            "InvalidatedSignals": [
                "NandNet5",
                "NandNet4",
                "Propagate1",
            ],
        },
    )
    assert First != Build(
        "NandNet4",
        0,
        {
            "SelectedClusterIndices": [2],
            "InvalidatedSignals": [
                "A1",
                "NandNet3",
                "NandNet4",
            ],
        },
    )
    assert not Build("NandNet4", 1, {})

def test_uncached_portal_failure_does_not_build_resume_cursor():
    Cursor, CompletedWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            {
                "PortalCompletedWork": 28,
                "PortalRequestCount": 40,
                "PortalCacheMode": "disabled",
                "RawPortalResourceCacheSelected": False,
            },
        )
    )

    assert CompletedWork == 28
    assert Cursor is None

@pytest.mark.parametrize(
    "ChangedIdentity",
    (
        {"PreSibling": "pre-sibling-b"},
        {"RequestDomain": "request-b"},
        {"Universe": "universe-b"},
        {"DescriptorCount": 4},
    ),
)
def test_descriptor_retention_rejects_universe_or_identity_mismatch(
    ChangedIdentity,
):
    First = _DescriptorContinuation(("descriptor-0",))
    Existing = SimpleNamespace(Continuation=First)
    Changed = _DescriptorContinuation(
        ("descriptor-0", "descriptor-1"),
        **ChangedIdentity,
    )

    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=Changed,
        ExistingEntry=Existing,
    )

    assert Admission["Retained"] is False
    assert Admission["Reason"] == (
        "descriptor-universe-or-identity-mismatch"
    )

def test_retained_plan_resume_preserves_aperture_and_fairness_state():
    Plan = SimpleNamespace(
        PlanFingerprint="plan-a",
        PortAssignmentFingerprint="ports-a",
        Ports=(),
    )
    Assembly = SimpleNamespace(Plan=Plan)
    Aperture = {
        "DomainFingerprint": "aperture-a",
        "Complete": True,
    }
    FirstCursor = PhysicalGlobalPlanResumeCursor(
        "cursor-6", "plan-a", "aperture-a", 6, object(),
    )
    FirstContinuation = (
        AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            Plan,
            {"Signal": "request-a"},
            {"Signal": 4},
            (),
            ("access-certificate", "aperture-a"),
            CompletedWork=6,
            ResumeCursor=FirstCursor,
        )
    )
    FreshAdmission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        Aperture,
        Continuation=FirstContinuation,
    )
    assert FreshAdmission["Retained"] is True
    Frontier = AuthoritativeCandidateGuides.RetainIncompletePhysicalGlobalPlan(
        {},
        Assembly,
        FirstContinuation,
        EnqueuedSequence=0,
    )
    assert AuthoritativeCandidateGuides.ShouldScheduleRetainedPhysicalGlobalPlan(
        Frontier,
        PreviousPlanWasRetained=False,
    )
    Resumed, Frontier = (
        AuthoritativeCandidateGuides.SelectNextRetainedPhysicalGlobalPlan(
            Frontier,
            ScheduleSequence=1,
        )
    )
    assert Resumed.Assembly is Assembly
    RefreshCursor = PhysicalGlobalPlanResumeCursor(
        "cursor-8", "plan-a", "aperture-a", 8, object(),
    )
    Refresh = AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
        Plan,
        {"Signal": "request-a"},
        {"Signal": 2},
        (),
        ("access-certificate", "aperture-a"),
        CompletedWork=2,
        ResumeCursor=RefreshCursor,
    )
    RefreshAdmission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        Aperture,
        Continuation=Refresh,
        ExistingEntry=Resumed,
    )
    assert RefreshAdmission["Retained"] is True
    Frontier = AuthoritativeCandidateGuides.RetainIncompletePhysicalGlobalPlan(
        Frontier,
        Assembly,
        Refresh,
        EnqueuedSequence=2,
    )

    Entry = Frontier["plan-a"]
    assert Entry.ScheduleCount == 1
    assert Entry.AccumulatedCompletedWork == 8
    assert Entry.Continuation.RemainingRequestCounts == (("Signal", 2),)
    assert "aperture-a" in Entry.Continuation.CertificateFingerprints
    assert AuthoritativeCandidateGuides.ShouldScheduleRetainedPhysicalGlobalPlan(
        Frontier,
        PreviousPlanWasRetained=True,
    )

def test_request_aperture_proof_retains_global_determinants_and_scope():
    Ports = tuple(
        SimpleNamespace(
            Signal=Signal,
            Direction="input",
            Attachment=(Index, 2, 0),
            GlobalPath=((Index, 2, 0), (Index, 2, -1)),
            Capacity=1,
            ReservationFingerprint=f"reservation-{Signal.lower()}",
            Claims=SimpleNamespace(
                ResourceIds=frozenset((f"wire:{Signal}",)),
            ),
            GlobalClaims=SimpleNamespace(
                ResourceIds=frozenset((f"global-wire:{Signal}",)),
            ),
        )
        for Index, Signal in enumerate((
            "Victim",
            "Blocker",
            *(f"Unrelated{Index}" for Index in range(16)),
        ))
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=Ports,
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalCandidateDomain",
            AffectedNets=("Victim", "Blocker"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "RequestApertureFactorProofComplete": True,
                "RequestApertureFactorNoGood": [
                    ["Victim", "request-factor:victim"],
                    ["Blocker", "aperture-factor:blocker"],
                ],
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        PreparedPhysicalComponentPortFactorDomain=SimpleNamespace(
            DomainFingerprint="prepared-port-domain",
        ),
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "request-aperture-factor-port-set"
    )
    assert Diagnostics["AssemblyPortCount"] == 18
    assert Diagnostics["NoGoodConstraintArity"] == 21
    assert len(Resources.RejectedPhysicalComponentPortReservationSets) == 1
    NoGood = next(iter(
        Resources.RejectedPhysicalComponentPortReservationSets
    ))
    assert {Port.Signal for Port in Ports} == {
        Signal for Signal, _Fingerprint in NoGood
    }
    assert {
        (Port.Signal, BuildPhysicalPortGlobalContractFingerprint(Port))
        for Port in Ports
    } <= NoGood
    assert any(
        Fingerprint.startswith("local-signal-domain:")
        for _Signal, Fingerprint in NoGood
    )
    ExpectedRetainedContracts = {
        Port.Signal: BuildPhysicalPortGlobalContractFingerprint(Port)
        for Port in Ports
        if Port.Signal != "Blocker"
    }
    assert Diagnostics["MinimumDeltaReplanPivotSignal"] == "Blocker"
    assert Diagnostics["BoundaryTraversalFocusSignal"] == ""
    assert Diagnostics["BoundaryIteratorContinuationPreserved"] is True
    assert Diagnostics["BoundaryIteratorCacheCleared"] is False
    assert Diagnostics["MinimumDeltaRetainedGlobalContracts"] == (
        ExpectedRetainedContracts
    )
    assert (
        Resources.PreferredPhysicalComponentGlobalContractsBySignal
        == ExpectedRetainedContracts
    )

def test_global_plan_dependency_identity_tracks_only_fixed_cut_contracts():
    Plan = SimpleNamespace(Ports=(
        SimpleNamespace(
            Signal="PortA",
            Direction="input",
            Attachment=(1, 2, 3),
            GlobalPath=((1, 2, 3), (0, 2, 3)),
            Capacity=1,
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),
        SimpleNamespace(
            Signal="PortB",
            Direction="output",
            Attachment=(9, 2, 3),
            GlobalPath=((9, 2, 3), (10, 2, 3)),
            Capacity=1,
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),
    ))

    First = BuildPhysicalGlobalPlanDependencyFingerprint(
        Plan,
        ("Foreign", "PortA"),
    )
    Reordered = BuildPhysicalGlobalPlanDependencyFingerprint(
        Plan,
        ("PortA", "Foreign", "PortA"),
    )
    UnrelatedPortChanged = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(
            Plan.Ports[0],
            SimpleNamespace(
                **{
                    **vars(Plan.Ports[1]),
                    "Attachment": (11, 2, 3),
                }
            ),
        )),
        ("Foreign", "PortA"),
    )
    DependentPortChanged = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(
            SimpleNamespace(
                **{
                    **vars(Plan.Ports[0]),
                    "Attachment": (2, 2, 3),
                }
            ),
            Plan.Ports[1],
        )),
        ("Foreign", "PortA"),
    )

    assert First == Reordered == UnrelatedPortChanged
    assert First != DependentPortChanged

def test_global_dependency_identity_includes_exact_aperture_claims():
    BasePort = SimpleNamespace(
        Signal="PortA",
        Direction="input",
        Attachment=(1, 2, 3),
        GlobalPath=((1, 2, 3), (0, 2, 3)),
        Capacity=1,
        ReservationFingerprint="reservation-a",
        Claims=SimpleNamespace(ResourceIds=frozenset(("wire:1",))),
        GlobalClaims=SimpleNamespace(
            ResourceIds=frozenset(("global-wire:1",))
        ),
    )
    Base = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(BasePort,)),
        ("PortA",),
    )
    ChangedClaims = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(SimpleNamespace(
            **{
                **vars(BasePort),
                "GlobalClaims": SimpleNamespace(
                    ResourceIds=frozenset(("global-wire:2",)),
                ),
            }
        ),)),
        ("PortA",),
    )
    ChangedLocalReservation = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(SimpleNamespace(
            **{
                **vars(BasePort),
                "ReservationFingerprint": "reservation-b",
            }
        ),)),
        ("PortA",),
    )

    assert Base != ChangedClaims
    assert Base == ChangedLocalReservation

def test_global_aperture_identity_ignores_local_seam_claims():
    GlobalClaims = SimpleNamespace(
        ResourceIds=frozenset(("global-wire",)),
    )
    BasePort = SimpleNamespace(
        Signal="PortA",
        Direction="input",
        Attachment=(1, 2, 3),
        GlobalPath=((1, 2, 3), (0, 2, 3)),
        Capacity=1,
        ReservationFingerprint="local-reservation-a",
        Claims=SimpleNamespace(ResourceIds=frozenset(("local-a",))),
        GlobalClaims=GlobalClaims,
    )
    ChangedLocal = SimpleNamespace(
        **{
            **vars(BasePort),
            "ReservationFingerprint": "local-reservation-b",
            "Claims": SimpleNamespace(
                ResourceIds=frozenset(("local-b",)),
            ),
        }
    )

    assert BuildPhysicalPortApertureContractFingerprint(
        BasePort
    ) == BuildPhysicalPortApertureContractFingerprint(ChangedLocal)

def test_global_cut_family_identity_ignores_candidate_variant_identity():
    First = {
        "Classification": "multi-pair-placement-conflict",
        "FailureNet": "PortC",
        "ConflictSignals": ["PortC", "PortA", "PortB"],
        "PairwiseIncompatibleEdges": [
            ["PortA", "PortC"],
            ["PortB", "PortC"],
        ],
        "CandidateCounts": {"PortA": 48, "PortC": 47},
        "PortalReservations": [{"PortalId": "variant-a"}],
    }
    Second = {
        **First,
        "ConflictSignals": ["PortB", "PortA", "PortC"],
        "PairwiseIncompatibleEdges": [
            ["PortC", "PortB"],
            ["PortC", "PortA"],
        ],
        "CandidateCounts": {"PortA": 72, "PortC": 72},
        "PortalReservations": [{"PortalId": "variant-b"}],
    }

    assert BuildPhysicalGlobalPlanCutFamilyFingerprint(First) == (
        BuildPhysicalGlobalPlanCutFamilyFingerprint(Second)
    )

def test_pair_relation_deadline_retains_only_current_exact_pair_clause(
    monkeypatch,
):
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=tuple(
            SimpleNamespace(
                Signal=Signal,
                ReservationFingerprint=f"reservation-{Signal.lower()}",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            )
            for Signal in ("PortA", "PortB")
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA", "PortB"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "PairwisePortReservationNoGoodProofComplete": True,
                "PairwisePortReservationNoGoodEdges": [
                    ["PortA", "PortB"],
                ],
                "MandatoryAccessProof": {
                    "Kind": "generated-fixed-portal-domain-exhausted",
                    "Complete": True,
                    "PortalTupleDomainComplete": True,
                    "ProofScope": "complete-portal-tuple-domain",
                    "BudgetExhausted": False,
                    "DeadlineExceeded": False,
                },
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
    Preparation = SimpleNamespace(DomainFingerprint="prepared-domain")
    Resources = SimpleNamespace(
        PreparedPhysicalComponentPortFactorDomain=Preparation,
        PhysicalBoundaryMandatoryPortalFactorDomainCache={
            ("prepared-domain", "PortA", "aperture-a"):
            SimpleNamespace(Signal="PortA", Complete=True),
            ("prepared-domain", "PortB", "aperture-b"):
            SimpleNamespace(Signal="PortB", Complete=True),
        },
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )
    DeadlineChecks = []

    def Compile(
        _Preparation,
        Signals,
        _Resources,
        ShouldStop=None,
        **_Options,
    ):
        DeadlineChecks.append(ShouldStop())
        return SimpleNamespace(
            RelationFingerprint="pair-relation",
            Signals=tuple(sorted(Signals)),
            ExpectedOptionPairCount=4,
            Certificates=(),
            UnsatisfiableApertureClauses=(),
            ForeignDependencyCertificateCount=0,
            Complete=False,
        )

    monkeypatch.setattr(
        BoundaryRelations,
        "CompilePhysicalBoundaryMandatoryPortalPairRelation",
        Compile,
    )

    NoGood = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
        ShouldStop=lambda: True,
    )

    assert DeadlineChecks == [True]
    assert NoGood["NoGoodConstraintArity"] == 2
    assert len(Resources.RejectedPhysicalComponentPortReservationSets) == 1
    assert NoGood[
        "CompiledMandatoryPortalPairRelations"
    ][0]["Complete"] is False

def test_new_aperture_clause_purges_matching_retained_global_plan():
    def Port(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Capacity=1,
            Attachment=(X, 2, 0),
            GlobalPath=((X, 2, 0), (X, 2, -1)),
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        )

    RejectedPlan = SimpleNamespace(
        PlanFingerprint="rejected-plan",
        Ports=(Port("PortA", 0), Port("PortB", 2)),
    )
    RetainedPlan = SimpleNamespace(
        PlanFingerprint="retained-plan",
        Ports=(Port("PortA", 8), Port("PortB", 2)),
    )
    Clause = frozenset(
        (
            PortValue.Signal,
            BuildPhysicalPortApertureContractFingerprint(PortValue),
        )
        for PortValue in RejectedPlan.Ports
    )
    Frontier = {
        "rejected-plan": SimpleNamespace(
            Assembly=SimpleNamespace(Plan=RejectedPlan)
        ),
        "retained-plan": SimpleNamespace(
            Assembly=SimpleNamespace(Plan=RetainedPlan)
        ),
    }

    Pruned, RejectedFingerprints = (
        PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses(
            Frontier,
            (Clause,),
        )
    )

    assert tuple(Pruned) == ("retained-plan",)
    assert RejectedFingerprints == ("rejected-plan",)

def test_replan_reuses_retained_factor_domain(monkeypatch):
    Preparation = SimpleNamespace(DomainFingerprint="same-domain")
    Resources = SimpleNamespace(
        PreparedPhysicalComponentPortFactorDomain=Preparation,
        PreparedComponentRoutingProblem=None,
        PreparedPhysicalComponentAssembly=None,
        FrozenPhysicalComponentAssemblyPlan=None,
    )
    Assembly = SimpleNamespace(
        Problem=object(),
        Plan=SimpleNamespace(PlanFingerprint="next-plan"),
    )
    Seen = []

    def Solve(
        Value,
        *,
        Resources,
        Deadline,
        DeferLocalCompositeSelection=True,
        RequiredBoundaryPorts=None,
    ):
        assert DeferLocalCompositeSelection
        assert RequiredBoundaryPorts is None
        Seen.append(Value)
        return Assembly

    monkeypatch.setattr(
        RoutingPcb,
        "SolvePreparedPhysicalComponentEligibility",
        Solve,
    )
    Result = ReplanPhysicalComponentAssembly(
        SimpleNamespace(),
        Resources=Resources,
        Deadline=SimpleNamespace(),
    )

    assert Result is Assembly
    assert Seen == [Preparation]
    assert Resources.PreparedPhysicalComponentPortFactorDomain is Preparation
    assert Resources.FrozenPhysicalComponentAssemblyPlan is Assembly.Plan

import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest

from Compiler.Placement.PcbFlow import (
    BuildPhysicalComponentPlacementFeedback,
    ClassifyPhysicalGlobalPlanRetentionAdmission,
    IsClusterInterfaceStateIncomplete,
    IsCompletePhysicalAssemblyUnsatisfiable,
    _PlaceAndRoutePcbWithPolicy,
)
import Compiler.Routing.AuthoritativePlanner as AuthoritativePlanner
from Compiler.Routing.ComponentPipeline import (
    BuildPhysicalAssemblyGlobalReuseFingerprint,
    BuildPhysicalGlobalPlanCutFamilyFingerprint,
    BuildPhysicalGlobalPlanDependencyFingerprint,
    BuildPhysicalPortApertureContractFingerprint,
    BuildPhysicalPortGlobalContractFingerprint,
    BuildPhysicalRequestAperturePortNoGood,
    ClassifyPhysicalComponentGlobalPlanningFailure,
    RecordPhysicalComponentGlobalPlanNoGood,
    PhysicalAssemblyGlobalRouteCanBeRebound,
    SelectPhysicalComponentGlobalContractRecommendation,
    SelectPhysicalComponentExactGlobalChannelSignals,
)
import Compiler.Routing.Pcb as RoutingPcb
from Compiler.Routing.Pcb import (
    ReplanPhysicalComponentAssembly,
    SolvePreparedPhysicalComponentEligibility,
)
from Compiler.Routing.Failures import (
    RoutingAssignmentCut,
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.ResourceGraph import RoutingResourceClaims
from Compiler.Routing.Models import (
    PhysicalGlobalPlanResumeCursor,
    PhysicalPortCorridorDomain,
    PhysicalPortCorridorFactor,
)


def _MixedPhysicalCorridorDomains():
    def Factor(Signal, Suffix, Node):
        Claims = RoutingResourceClaims(
            WireCells=frozenset((Node,)),
            ElectricalCells=frozenset((Node,)),
        )
        return PhysicalPortCorridorFactor(
            Signal=Signal,
            PortReservationFingerprint=(
                f"reservation-{Signal.lower()}-{Suffix}"
            ),
            PortGlobalContractFingerprint=(
                f"global-{Signal.lower()}-{Suffix}"
            ),
            RequestDependencyFingerprint=(
                f"request-{Signal.lower()}-{Suffix}"
            ),
            RouteCandidateId=f"route-{Signal.lower()}-{Suffix}",
            RouteCandidateFingerprint=(
                f"route-fingerprint-{Signal.lower()}-{Suffix}"
            ),
            NormalizedIdentityFingerprint=(
                f"normalized-{Signal.lower()}-{Suffix}"
            ),
            Layer=0,
            Nodes=frozenset((Node,)),
            Claims=Claims,
            Candidate=SimpleNamespace(
                CandidateId=f"route-{Signal.lower()}-{Suffix}",
                Claims=Claims,
            ),
        )

    def Domain(FactorValue):
        return PhysicalPortCorridorDomain(
            DomainFingerprint=(
                "domain-" + FactorValue.NormalizedIdentityFingerprint
            ),
            Signal=FactorValue.Signal,
            PortReservationFingerprint=(
                FactorValue.PortReservationFingerprint
            ),
            PortGlobalContractFingerprint=(
                FactorValue.PortGlobalContractFingerprint
            ),
            RequestDependencyFingerprint=(
                FactorValue.RequestDependencyFingerprint
            ),
            ResourceGraphFingerprint="resource-graph",
            TechnologyFingerprint="technology",
            Factors=(FactorValue,),
            Complete=True,
        )

    # The two original plan tuples conflict on their shared exact node:
    # (A1, B1) at node 0 and (A2, B2) at node 10.  Cross-plan tuples are
    # compatible, allowing the recommendation to reuse cached exact factors
    # without pretending either failed complete tuple was feasible.
    return tuple(map(Domain, (
        Factor("A", "1", (0, 1, 0)),
        Factor("B", "1", (0, 1, 0)),
        Factor("A", "2", (10, 1, 0)),
        Factor("B", "2", (10, 1, 0)),
    )))


def test_local_unsat_rejects_only_the_complete_assembly_plan():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Start = Source.index("ComponentSolve = CompileClosedComponent(")
    End = Source.index("assert ComponentSolve.Template is not None", Start)
    LocalCompilation = Source[Start:End]

    assert "RecordPhysicalComponentLocalCompilationNoGood" in (
        LocalCompilation
    )
    assert "GlobalChannelDesign" in LocalCompilation
    assert "RejectedPhysicalComponentPortAssignmentFingerprints" not in (
        LocalCompilation
    )
    assert "RejectedPhysicalComponentPortReservationsBySignal" not in (
        LocalCompilation
    )
    assert "RejectedPhysicalComponentPortReservationSets" not in (
        LocalCompilation
    )
    assert "local-unsat-reject-complete-assembly-plan" in LocalCompilation
    assert "PerSignalReservationFeedbackUsed\": False" in LocalCompilation
    assert "ReplanPhysicalAssemblyWithTiming(" in LocalCompilation


def test_local_compilation_requires_explicit_admission_without_floor():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Start = Source.index("ActiveComponentDeadline = SharedInterfaceDeadline")
    Compile = Source.index("ComponentSolve = CompileClosedComponent(", Start)
    End = Source.index("if not ComponentSolve.Feasible:", Compile)
    Admission = Source[Start:Compile]
    Invocation = Source[Compile:End]

    assert "BuildLocalComponentCompilationAdmissionFailure(" in Admission
    assert "ActiveComponentRemainingSeconds <= 0" in Admission
    assert "DeadlineSeconds=(" in Invocation
    assert "ActiveComponentRemainingSeconds" in Invocation
    assert "max(" not in Invocation


def test_physical_planning_promotes_proof_guided_retained_execution():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Schedule = Source.index("BuildClusterInterfaceStageSchedule(")
    PlanningDeadline = Source.index(
        "SharedInterfacePlanningDeadline = RoutingDeadline(",
        Schedule,
    )
    StateDeadline = Source.index(
        "InterfaceDeadline = (",
        PlanningDeadline,
    )
    Admission = Source.index(
        "if InterfaceDeadline.IsExpired():",
        StateDeadline,
    )
    Preparation = Source.index(
        "PreparePhysicalComponentEligibility(",
        Admission,
    )

    assert Schedule < PlanningDeadline < StateDeadline < Admission < Preparation
    Selection = Source[StateDeadline:Admission]
    assert 'InterfaceWorkPhase == "prepare-eligibility"' in Selection
    assert "and ActiveComponentCutSignals" in Selection
    assert "SharedInterfaceDeadline" in Selection
    assert "SharedInterfacePlanningDeadline" in Selection


@pytest.mark.parametrize(
    "Reason, ProofKey",
    (
        (
            RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            "PortAssignmentProofComplete",
        ),
        (
            RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
            "GlobalPlanDomainComplete",
        ),
    ),
)
def test_complete_physical_assembly_proof_survives_deadline_expiry(
    Reason,
    ProofKey,
):
    CompleteProof = IsCompletePhysicalAssemblyUnsatisfiable(
        Reason,
        {ProofKey: True},
    )

    assert CompleteProof
    assert not IsClusterInterfaceStateIncomplete(
        FailureReason=Reason,
        InterfaceDeadlineExpired=True,
        ComponentSolveStatus="",
        ExplicitCompleteUnsatProof=CompleteProof,
    )


def test_deadline_expiry_is_incomplete_without_complete_proof():
    Reason = RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    CompleteProof = IsCompletePhysicalAssemblyUnsatisfiable(Reason, {})

    assert not CompleteProof
    assert IsClusterInterfaceStateIncomplete(
        FailureReason=Reason,
        InterfaceDeadlineExpired=True,
        ComponentSolveStatus="",
        ExplicitCompleteUnsatProof=CompleteProof,
    )


def test_explicit_complete_proof_overrides_stale_incomplete_status():
    assert not IsClusterInterfaceStateIncomplete(
        FailureReason=(
            RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        ),
        InterfaceDeadlineExpired=True,
        ComponentSolveStatus="incomplete",
        ExplicitCompleteUnsatProof=True,
    )


def test_complete_port_assignment_core_advances_placement_after_deadline():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Exhaustive = Source.index("if StateExhaustive and not StateIncomplete:")
    Advance = Source.index(
        "if ComponentAccessCoreSignals:",
        Exhaustive,
    )
    Reorder = Source.index(
        "ReorderRemainingPlacementsForAccessCore(",
        Advance,
    )

    assert "InterfaceDeadline.IsExpired()" not in Source[Advance:Reorder]


def test_minimal_physical_port_core_builds_explicit_placement_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        AffectedNets=("UnusedBroadSignal",),
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreSignals": ["Beta", "Alpha", "Beta"],
            "PortAssignmentUnsatCoreFingerprint": "physical-core",
            "PhysicalAssemblyPlanFingerprint": "plan",
            "DomainFingerprint": "domain",
        },
    )

    Feedback = BuildPhysicalComponentPlacementFeedback(Failure)

    assert Feedback is not None
    assert Feedback.ProofFingerprint == "physical-core"
    assert Feedback.RelocationSignals == ("Alpha", "Beta")
    assert Feedback.SourcePlanFingerprint == "plan"
    assert Feedback.DomainFingerprint == "domain"


@pytest.mark.parametrize(
    "Diagnostics",
    (
        {},
        {
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": False,
            "PortAssignmentUnsatCoreSignals": ["Alpha"],
        },
        {
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreSignals": [],
        },
    ),
)
def test_incomplete_or_nonminimal_port_core_cannot_drive_placement(
    Diagnostics,
):
    assert BuildPhysicalComponentPlacementFeedback(RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics=Diagnostics,
    )) is None


def test_two_assembly_plans_reuse_one_prepared_factor_domain(monkeypatch):
    Preparation = SimpleNamespace(
        DomainFingerprint="stable-factor-domain",
        Problem=object(),
        CoarsePlan=object(),
        AccessCertificate=object(),
    )
    FirstAssembly = SimpleNamespace(
        Problem=object(),
        Plan=SimpleNamespace(PlanFingerprint="plan-a"),
        GlobalGuidePlan=Preparation.CoarsePlan,
    )
    SecondAssembly = SimpleNamespace(
        Problem=object(),
        Plan=SimpleNamespace(PlanFingerprint="plan-b"),
        GlobalGuidePlan=Preparation.CoarsePlan,
    )
    Calls = []

    def Solve(
        Value,
        Resources,
        *,
        WorkCheck=None,
        DeferLocalCompositeSelection=False,
        RequiredBoundaryPorts=None,
    ):
        assert DeferLocalCompositeSelection
        assert RequiredBoundaryPorts is None
        Calls.append((Value, Value.DomainFingerprint))
        return (FirstAssembly, SecondAssembly)[len(Calls) - 1]

    monkeypatch.setattr(
        AuthoritativePlanner,
        "SolvePreparedPhysicalComponentPortFactorDomain",
        Solve,
    )
    Resources = SimpleNamespace(
        PreparedComponentRoutingProblem=None,
        PreparedPhysicalComponentAssembly=None,
        FrozenPhysicalComponentAssemblyPlan=None,
        FrozenPhysicalComponentGlobalGuidePlan=None,
    )
    Deadline = SimpleNamespace(RaiseIfExpired=lambda *_Args: None)

    First = SolvePreparedPhysicalComponentEligibility(
        Preparation,
        Resources=Resources,
        Deadline=Deadline,
    )
    Second = SolvePreparedPhysicalComponentEligibility(
        Preparation,
        Resources=Resources,
        Deadline=Deadline,
    )

    assert First.Plan.PlanFingerprint == "plan-a"
    assert Second.Plan.PlanFingerprint == "plan-b"
    assert Calls == [
        (Preparation, "stable-factor-domain"),
        (Preparation, "stable-factor-domain"),
    ]
    assert Resources.FrozenPhysicalComponentGlobalGuidePlan is (
        Preparation.CoarsePlan
    )


def test_retained_placement_exhausts_prepared_domain_before_advancing():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    QueueStart = Source.index('"prepare-eligibility",')
    PhaseOrder = Source.index(
        'if Entry[1][0] == "prepare-eligibility"',
        QueueStart,
    )
    SolveMarker = Source.index(
        'InterfaceCandidateQueue.insert(0, (\n'
        '                        "solve-prepared-eligibility",',
        PhaseOrder,
    )
    SolveCall = Source.index(
        "SolvePreparedPhysicalComponentEligibility(",
        SolveMarker,
    )

    assert QueueStart < PhaseOrder < SolveMarker < SolveCall
    assert "PreparedEligibilityByState[" in Source[PhaseOrder:SolveMarker]


def test_complete_global_plan_failure_replans_before_local_compilation():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index(
        "def ReserveAuthoritativeGlobalChannels("
    )
    ReserveEnd = Source.index(
        "PreparedAssembly, GlobalChannelDesign = (",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]

    Classify = Reservation.index(
        "ClassifyPhysicalComponentGlobalPlanningFailure("
    )
    RecordNoGood = Reservation.index(
        "RecordPhysicalComponentGlobalPlanNoGood(",
        Classify,
    )
    Reject = Reservation.index(
        "global-planning-reject-physical-plan",
        RecordNoGood,
    )
    Replan = Reservation.index(
        "SelectFreshOrRetainedAssembly()",
        Reject,
    )
    Continue = Reservation.index("continue", Replan)

    assert Classify < RecordNoGood < Reject < Replan < Continue
    assert "RejectedPhysicalComponentPortAssignmentFingerprints" not in (
        Reservation[Classify:Replan]
    )
    assert "LocalCompilationEntered\": False" in Reservation
    assert "LocalTemplateReopened\": False" in Reservation


def test_incomplete_global_plan_is_retained_without_recording_a_no_good():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index(
        "def ReserveAuthoritativeGlobalChannels("
    )
    ReserveEnd = Source.index(
        "PreparedAssembly, GlobalChannelDesign = (",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]

    Incomplete = Reservation.index(
        ".PhysicalComponentAssemblyIncomplete"
    )
    Retain = Reservation.index(
        "RetainIncompleteGlobalPlan(",
        Incomplete,
    )
    Defer = Reservation.index(
        "incomplete-plan-retained",
        Retain,
    )
    Replan = Reservation.index(
        "SelectFreshOrRetainedAssembly()",
        Defer,
    )

    assert Incomplete < Retain < Defer < Replan
    assert '"NoGoodRecorded": False' in Reservation[Retain:Replan]
    assert '"CursorResumeAvailable": False' in Reservation[Retain:Replan]
    assert "RecordPhysicalComponentGlobalPlanNoGood(" not in (
        Reservation[Incomplete:Replan]
    )


def test_incomplete_global_plan_timing_closes_before_next_plan_selection():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index(
        "def ReserveAuthoritativeGlobalChannels("
    )
    ReserveEnd = Source.index(
        "PreparedAssembly, GlobalChannelDesign = (",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]
    Incomplete = Reservation.index(
        ".PhysicalComponentAssemblyIncomplete"
    )
    Retained = Reservation.index(
        'GlobalPlanningAttemptResult = (\n'
        '                                    "incomplete-plan-retained"',
        Incomplete,
    )
    Record = Reservation.index(
        "RecordPhysicalComponentStageTiming(",
        Retained,
    )
    MarkRecorded = Reservation.index(
        "GlobalPlanningAttemptRecorded = True",
        Record,
    )
    SelectNext = Reservation.index(
        "SelectFreshOrRetainedAssembly()",
        MarkRecorded,
    )

    assert Retained < Record < MarkRecorded < SelectNext


def test_local_structural_caches_span_retained_placement_candidate_loop():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    CandidateLoop = Source.index(
        "while (\n"
        "            InterfaceCandidateQueue\n"
        "            or PendingProofGuidedPlacementByComponentVariant\n"
        "        ):"
    )
    CacheDeclarations = (
        "ComponentVariantPortfolioCache: dict[Any, Any] = {}",
        "ComponentNetVariantConstructionCache: dict[Any, Any] = {}",
        "ComponentRouteClaimsConstructionCache: dict[Any, Any] = {}",
        "ComponentNetVariantDiscoveryStateCache: dict[Any, Any] = {}",
    )

    for Declaration in CacheDeclarations:
        assert Source.count(Declaration) == 1
        assert Source.index(Declaration) < CandidateLoop

    CandidateBody = Source[CandidateLoop:]
    assert all(
        Declaration not in CandidateBody
        for Declaration in CacheDeclarations
    )
    assert (
        "RoutingResourcesByRetainedPlacementFingerprint"
        in Source[:CandidateLoop]
    )


def test_frontier_retention_requires_complete_aperture_and_progress():
    CompleteAperture = {
        "DomainFingerprint": "aperture-a",
        "Complete": True,
    }

    Plan = SimpleNamespace(PlanFingerprint="plan-a", Ports=())
    WithoutCursor = (
        AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
            Plan, {}, {}, (), ("aperture-a",), CompletedWork=8,
        )
    )
    assert not ClassifyPhysicalGlobalPlanRetentionAdmission(
        {**CompleteAperture, "Complete": False},
        Continuation=WithoutCursor,
    )["Retained"]
    NonResumable = ClassifyPhysicalGlobalPlanRetentionAdmission(
        CompleteAperture,
        Continuation=WithoutCursor,
    )
    assert NonResumable["Retained"] is False
    assert NonResumable["Reason"] == "resume-cursor-unavailable"
    with pytest.raises(ValueError, match="requires a resumable cursor"):
        AuthoritativePlanner.RetainIncompletePhysicalGlobalPlan(
            {},
            SimpleNamespace(Plan=Plan),
            WithoutCursor,
            EnqueuedSequence=0,
        )

    with pytest.raises(ValueError, match="no resumable progress"):
        AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
            Plan, {}, {}, (), ("aperture-a",),
            CompletedWork=8,
            ResumeCursor=PhysicalGlobalPlanResumeCursor(
                "cursor-empty", "plan-a", "aperture-a", 8, None,
            ),
        )

    Cursor = PhysicalGlobalPlanResumeCursor(
        CursorFingerprint="cursor-a",
        PlanFingerprint="plan-a",
        ApertureDomainFingerprint="aperture-a",
        CompletedWork=8,
        State=object(),
    )
    Resumable = AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
        Plan, {}, {}, (), ("aperture-a",),
        CompletedWork=8,
        ResumeCursor=Cursor,
    )
    Positive = ClassifyPhysicalGlobalPlanRetentionAdmission(
        CompleteAperture,
        Continuation=Resumable,
    )
    assert Positive["Retained"] is True
    assert Positive["Reason"] == "typed-resumable-progress"


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
        AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
    Frontier = AuthoritativePlanner.RetainIncompletePhysicalGlobalPlan(
        {},
        Assembly,
        FirstContinuation,
        EnqueuedSequence=0,
    )
    assert AuthoritativePlanner.ShouldScheduleRetainedPhysicalGlobalPlan(
        Frontier,
        PreviousPlanWasRetained=False,
    )
    Resumed, Frontier = (
        AuthoritativePlanner.SelectNextRetainedPhysicalGlobalPlan(
            Frontier,
            ScheduleSequence=1,
        )
    )
    assert Resumed.Assembly is Assembly
    RefreshCursor = PhysicalGlobalPlanResumeCursor(
        "cursor-8", "plan-a", "aperture-a", 8, object(),
    )
    Refresh = AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
    Frontier = AuthoritativePlanner.RetainIncompletePhysicalGlobalPlan(
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
    assert not AuthoritativePlanner.ShouldScheduleRetainedPhysicalGlobalPlan(
        Frontier,
        PreviousPlanWasRetained=True,
    )


def test_retained_global_plans_are_serviced_before_another_fresh_plan():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Start = Source.index("def SelectFreshOrRetainedAssembly(")
    End = Source.index("CurrentAssembly = Assembly", Start)
    Selector = Source[Start:End]

    Fairness = Selector.index(
        "ShouldScheduleRetainedPhysicalGlobalPlan("
    )
    Retained = Selector.index(
        "SelectRetainedAssembly(",
        Fairness,
    )
    Fresh = Selector.index(
        "ReplanPhysicalAssemblyWithTiming()",
        Retained,
    )

    assert Fairness < Retained < Fresh
    assert "PreviousPlanWasRetained" in Selector


def test_physical_component_pipeline_records_explicit_stage_durations():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)

    assert '"DurationSeconds"' in Source
    assert '"ElapsedSinceRoutingStartSeconds"' in Source
    assert '"PhysicalComponentStageTimings"' in Source
    for Stage in (
        "PhysicalEligibilityPreparation",
        "PhysicalEligibilitySolve",
        "AuthoritativeGlobalReserve",
        "PhysicalAssemblyReplan",
        "BoundLocalCompilation",
        "GlobalRelaxedLocalProof",
    ):
        assert f'"{Stage}"' in Source


def test_successful_global_plan_returns_its_frontier_source_identity():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index("def ReserveAuthoritativeGlobalChannels(")
    ReserveEnd = Source.index(
        "PhysicalAssemblyPlan = PreparedAssembly.Plan",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]

    assert "SuccessfulGlobalPlanWasRetained = (" in Reservation
    assert "PreviousGlobalPlanWasRetained" in Reservation
    assert "if SuccessfulGlobalPlanWasRetained" in Source[ReserveStart:]


def test_single_port_global_proof_records_only_targeted_reservation_no_good():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=(
            SimpleNamespace(
                Signal="PortA",
                ReservationFingerprint="reservation-a",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
            SimpleNamespace(
                Signal="PortB",
                ReservationFingerprint="reservation-b",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA",),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={
            "stale-frontier": object(),
        },
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "exact-assembly-port-aperture-set"
    )
    assert Diagnostics["NoGoodSignals"] == ["PortA"]
    ApertureKeys = {
        Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", ApertureKeys["PortA"]),
            ("PortB", ApertureKeys["PortB"]),
        )),
    }
    assert not Resources.RejectedPhysicalComponentPortReservationsBySignal
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Diagnostics["BoundaryTraversalFocusSignal"] == "PortA"
    assert Diagnostics["BoundaryTraversalPrioritySignals"] == [
        "PortA",
    ]
    assert Diagnostics["BoundaryTraversalEpoch"] == 1
    assert Resources.PhysicalComponentBoundaryAssignmentIteratorCache == {}


def test_joint_port_global_proof_records_only_targeted_reservation_tuple():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=(
            SimpleNamespace(
                Signal="PortA",
                ReservationFingerprint="reservation-a",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
            SimpleNamespace(
                Signal="PortB",
                ReservationFingerprint="reservation-b",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
            SimpleNamespace(
                Signal="PortC",
                ReservationFingerprint="reservation-c",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
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
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
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

    assert Diagnostics["NoGoodScope"] == (
        "exact-assembly-port-aperture-set"
    )
    assert Diagnostics["NoGoodSignals"] == ["PortA", "PortB"]
    GlobalKeys = {
        Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", GlobalKeys["PortA"]),
            ("PortB", GlobalKeys["PortB"]),
            ("PortC", GlobalKeys["PortC"]),
        )),
    }
    assert not Resources.RejectedPhysicalComponentPortReservationsBySignal
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Diagnostics["BoundaryTraversalFocusSignal"] == "PortA"
    assert Diagnostics["BoundaryTraversalPrioritySignals"] == [
        "PortB",
        "PortA",
    ]

    Rotated = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )
    assert Rotated["BoundaryTraversalFocusSignal"] == "PortB"
    assert Rotated["BoundaryTraversalPrioritySignals"] == [
        "PortA",
        "PortB",
    ]
    assert Rotated["BoundaryTraversalEpoch"] == 2


def test_complete_independent_empty_route_domains_reject_exact_ports():
    Ports = tuple(
        SimpleNamespace(
            Signal=Signal,
            Direction="output",
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
        for Index, Signal in enumerate(("EmptyA", "EmptyB", "Sibling"))
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=Ports,
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=(
                RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
            ),
            Stage="PhysicalComponentGlobalCandidateDomain",
            AffectedNets=("EmptyA", "EmptyB", "Sibling"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "IndependentEmptyCandidateDomainSignals": [
                    "EmptyA",
                    "EmptyB",
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
        "independent-empty-global-route-domain"
    )
    assert set(
        Resources.RejectedPhysicalComponentPortReservationsBySignal
    ) == {"EmptyA", "EmptyB"}
    assert "Sibling" not in (
        Resources.RejectedPhysicalComponentPortReservationsBySignal
    )
    assert not Resources.RejectedPhysicalComponentPortReservationSets


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


def test_request_aperture_proof_projects_only_required_local_claims():
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
        for Index, Signal in enumerate(("Alpha", "Beta", "Gamma"))
    )
    NoGood = BuildPhysicalRequestAperturePortNoGood(
        SimpleNamespace(Ports=Ports),
        frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
        )),
    )

    assert {
        (Port.Signal, BuildPhysicalPortGlobalContractFingerprint(Port))
        for Port in Ports
    } <= NoGood
    assert (
        "Alpha",
        BuildPhysicalPortApertureContractFingerprint(Ports[0]),
    ) in NoGood
    assert (
        "Beta",
        BuildPhysicalPortApertureContractFingerprint(Ports[1]),
    ) in NoGood
    assert all(
        not (
            Signal == "Gamma"
            and Fingerprint.startswith("aperture-contract-v2:")
        )
        for Signal, Fingerprint in NoGood
    )


def test_certified_signal_local_request_aperture_proof_is_domain_scoped():
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
        for Index, Signal in enumerate(("Alpha", "Beta", "Gamma"))
    )
    NoGood = BuildPhysicalRequestAperturePortNoGood(
        SimpleNamespace(Ports=Ports),
        frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
        )),
        SignalLocalRequestFactorProofComplete=True,
        PortSolverCacheKey="solver-domain",
    )

    assert {
        (Port.Signal, BuildPhysicalPortGlobalContractFingerprint(Port))
        for Port in Ports
    } <= NoGood
    assert (
        "Alpha",
        "local-signal-domain:solver-domain",
    ) in NoGood
    assert {
        (
            "Alpha",
            BuildPhysicalPortApertureContractFingerprint(Ports[0]),
        ),
        (
            "Beta",
            BuildPhysicalPortApertureContractFingerprint(Ports[1]),
        ),
    } <= NoGood
    assert (
        "Gamma",
        BuildPhysicalPortApertureContractFingerprint(Ports[2]),
    ) not in NoGood

    Unscoped = BuildPhysicalRequestAperturePortNoGood(
        SimpleNamespace(Ports=Ports),
        frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
        )),
        SignalLocalRequestFactorProofComplete=True,
    )
    assert {
        (Port.Signal, BuildPhysicalPortGlobalContractFingerprint(Port))
        for Port in Ports
    } <= Unscoped


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


def test_global_planning_classifier_exposes_proof_dependency_identities():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(
            Signal="PortA",
            Direction="input",
            Attachment=(1, 2, 3),
                GlobalPath=((1, 2, 3),),
                Capacity=1,
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="PhysicalComponentGlobalAssignmentDomain",
        AffectedNets=("PortA", "Foreign"),
        Diagnostics={
            "GlobalPlanDomainComplete": True,
            "CompleteAssignmentCutProof": True,
            "CandidateFingerprint": "candidate-domain",
            "ConflictFingerprint": "conflict-domain",
            "ConflictGraph": {
                "Classification": "pairwise-incompatibility",
                "ConflictSignals": ["Foreign", "PortA"],
                "PairwiseIncompatibleEdges": [["PortA", "Foreign"]],
            },
        },
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    Diagnostics = Classified.Diagnostics
    assert Diagnostics["GlobalPlanDependencyFingerprint"].startswith(
        "global-dependency-v2:"
    )
    assert Diagnostics["GlobalPlanCutFamilyFingerprint"].startswith(
        "global-cut-family-v1:"
    )
    assert Diagnostics["GlobalPlanProofFingerprint"].startswith(
        "global-proof-v1:"
    )


def test_complete_pairwise_global_cut_records_each_exact_reservation_edge():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=tuple(
            SimpleNamespace(
                Signal=Signal,
                ReservationFingerprint=f"reservation-{Signal.lower()}",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            )
            for Signal in ("PortA", "PortB", "PortC")
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA", "PortB", "PortC"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "PairwisePortReservationNoGoodProofComplete": True,
                "ConflictGraph": {
                    "PairwiseIncompatibleEdges": [
                        ["PortA", "PortB"],
                        ["PortA", "PortC"],
                    ],
                },
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
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
        "pairwise-port-aperture-reservation-sets"
    )
    GlobalKeys = {
        Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", GlobalKeys["PortA"]),
            ("PortB", GlobalKeys["PortB"]),
        )),
        frozenset((
            ("PortA", GlobalKeys["PortA"]),
            ("PortC", GlobalKeys["PortC"]),
        )),
    }
    assert len(Diagnostics["NoGoodReservationSets"]) == 2


def test_global_contract_recommendation_rejects_uncertified_mixed_factors():
    Recommendation = SelectPhysicalComponentGlobalContractRecommendation(
        _MixedPhysicalCorridorDomains(),
        ("A", "B"),
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
    )

    assert Recommendation is None


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


def test_complete_global_cut_without_pair_dependency_proof_records_joint_tuple():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=tuple(
            SimpleNamespace(
                Signal=Signal,
                ReservationFingerprint=f"reservation-{Signal.lower()}",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            )
            for Signal in ("PortA", "PortB", "PortC")
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA", "PortB", "PortC"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                # These are route-candidate conflicts under the full plan,
                # not a proof that either port pair is independently
                # infeasible under every assignment of the third port.
                "ConflictGraph": {
                    "PairwiseIncompatibleEdges": [
                        ["PortA", "PortB"],
                        ["PortA", "PortC"],
                    ],
                },
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
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
        "exact-assembly-port-aperture-set"
    )
    assert not Diagnostics[
        "PairwisePortReservationNoGoodProofComplete"
    ]
    GlobalKeys = {
        Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", GlobalKeys["PortA"]),
            ("PortB", GlobalKeys["PortB"]),
            ("PortC", GlobalKeys["PortC"]),
        )),
    }
    assert Diagnostics["NoGoodReservationSets"] == []


def test_global_port_replans_route_and_bind_each_exact_corridor_contract():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index(
        "def ReserveAuthoritativeGlobalChannels(",
    )
    ReserveEnd = Source.index(
        "PreparedAssembly, GlobalChannelDesign = (",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]

    assert "PreparePhysicalComponentGlobalPlanningPlacement(" in Reservation
    assert "PreparingPhysicalComponentGlobalChannels = True" in Reservation
    assert "RoutePcbDesign(" in Reservation
    assert "BindPhysicalComponentAssemblyGlobalChannels(" in Reservation
    assert "CurrentAssembly" in Reservation


def test_local_interface_factor_portfolio_runs_after_fixed_plan_proof():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Compile = Source.index("CompileClosedComponent(")
    RelaxedProof = Source.index(
        "ProveGlobalRelaxedLocalUnsatisfiability(", Compile
    )
    RecordNoGood = Source.index(
        "RecordPhysicalComponentLocalCompilationNoGood(", RelaxedProof
    )
    CertifyPortfolio = Source.index(
        "CertifyLocalInterfaceFactorPortfolio(", RecordNoGood
    )
    Replan = Source.index(
        "ReplanPhysicalAssemblyWithTiming()", CertifyPortfolio
    )

    assert Compile < RelaxedProof < RecordNoGood < CertifyPortfolio < Replan


def test_local_feedback_proofs_use_the_authoritative_component_deadline():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Compile = Source.index("CompileClosedComponent(")
    FeedbackDeadline = Source.index(
        "FeedbackProofDeadline = ActiveComponentDeadline",
        Compile,
    )
    RelaxedProof = Source.index(
        "ProveGlobalRelaxedLocalUnsatisfiability(",
        FeedbackDeadline,
    )
    CertifyPortfolio = Source.index(
        "CertifyLocalInterfaceFactorPortfolio(",
        RelaxedProof,
    )
    Replan = Source.index(
        "ReplanPhysicalAssemblyWithTiming()",
        CertifyPortfolio,
    )
    RelaxedSlice = Source[RelaxedProof:CertifyPortfolio]
    PortfolioSlice = Source[CertifyPortfolio:Replan]

    assert FeedbackDeadline < RelaxedProof < CertifyPortfolio < Replan
    assert "FeedbackProofRemainingSeconds" in RelaxedSlice
    assert "FeedbackProofDeadline.RaiseIfExpired(" in RelaxedSlice
    assert "FeedbackProofDeadline" in PortfolioSlice
    assert "FeedbackProofDeadline.RaiseIfExpired(" in PortfolioSlice
    assert "FeedbackPlanningDeadline" not in RelaxedSlice
    assert "FeedbackPlanningDeadline" not in PortfolioSlice


def test_incomplete_local_factor_portfolio_stops_before_global_replan():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Compile = Source.index("CompileClosedComponent(")
    CertifyPortfolio = Source.index(
        "CertifyLocalInterfaceFactorPortfolio(", Compile
    )
    TypedIncomplete = Source.index(
        '"CertificationIncomplete"',
        CertifyPortfolio,
    )
    Replan = Source.index(
        "ReplanPhysicalAssemblyWithTiming()", CertifyPortfolio
    )
    Guard = Source[CertifyPortfolio:Replan]

    assert CertifyPortfolio < TypedIncomplete < Replan
    assert 'not PortfolioDiagnostics.get(' in Guard
    assert '"Complete", False' in Guard
    assert '"FeasibleWitness"' in Guard
    assert '"GlobalReplanEntered": False' in Guard


def test_physical_guide_overlay_preserves_complete_ordinary_plan_coverage():
    Source = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    OverlayStart = Source.index(
        "# Rebuild ordinary whole-design guides against the current profile"
    )
    OverlayEnd = Source.index(
        'WorkTelemetry["GlobalGuidePlanCacheHit"]',
        OverlayStart,
    )
    Overlay = Source[OverlayStart:OverlayEnd]

    OrdinaryGuides = Overlay.index("**dict(CoarsePlan.Guides)")
    PhysicalGuides = Overlay.index(
        "Channel.Signal: frozenset(Channel.GuideCells)",
        OrdinaryGuides,
    )
    OrdinaryLayers = Overlay.index("**dict(CoarsePlan.Layers)")
    PhysicalLayers = Overlay.index(
        "Channel.Signal: int(Channel.Layer)",
        OrdinaryLayers,
    )

    assert OrdinaryGuides < PhysicalGuides
    assert OrdinaryLayers < PhysicalLayers
    assert Overlay.count(
        "if Channel.Signal in PhysicalAssemblyPortSignalsForGuide"
    ) == 2
    assert "CoarsePlan = FrozenPhysicalComponentGuidePlan" not in Overlay


def test_prepared_solve_preserves_typed_deadline_and_domain(monkeypatch):
    Preparation = SimpleNamespace(
        DomainFingerprint="prepared-domain",
    )

    def Expire(*_Args, **_KeywordArgs):
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.RuntimeBudgetExceeded,
            Stage="PhysicalComponentAssembly",
            Detail="shared routing deadline expired",
            Diagnostics={"PortAssignmentExpansionCount": 164210},
        ))

    monkeypatch.setattr(
        AuthoritativePlanner,
        "SolvePreparedPhysicalComponentPortFactorDomain",
        Expire,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )
    Deadline = SimpleNamespace(RaiseIfExpired=lambda *_Args: None)

    with pytest.raises(RoutingStageError) as Raised:
        SolvePreparedPhysicalComponentEligibility(
            Preparation,
            Resources=Resources,
            Deadline=Deadline,
        )

    Failure = Raised.value.Failure
    assert Failure.Reason == (
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete
    )
    assert Failure.Stage == "PhysicalComponentAssemblyIncomplete"
    assert Failure.Diagnostics["DomainFingerprint"] == "prepared-domain"
    assert Failure.Diagnostics["PreparedFactorDomainReused"] is True
    assert Failure.Diagnostics[
        "PhysicalComponentAssemblyClassification"
    ]["Operation"] == "solve-prepared-eligibility"


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


def test_global_planning_classifier_preserves_explicit_domain_proof():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="PhysicalComponentGlobalAssignmentDomain",
        AffectedNets=("A", "B"),
        Diagnostics={
            "GlobalPlanDomainComplete": True,
            "CompleteAssignmentCutProof": True,
            "EscalationHistory": (),
        },
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        SimpleNamespace(PlanFingerprint="physical-plan"),
        DeadlineExpired=False,
    )

    assert Classified.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Classified.Diagnostics["GlobalPlanDomainComplete"] is True
    assert Classified.Diagnostics["UnderlyingEscalationHistory"] == []
    assert Classified.Diagnostics["ExecutableLegacyRepairCascade"] is False
    Cut = RoutingAssignmentCut.FromFailure(Classified)
    assert Cut is not None
    assert Cut.CompleteAssignmentCutProof is True
    assert Cut.ConflictSignals == ("A", "B")


def test_global_mandatory_cut_skips_unrelated_port_reassignment():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="GeneratedPortalCapacityCertification",
        AffectedNets=("ForeignA", "ForeignB"),
        Diagnostics={
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
                "ConflictSignals": ["ForeignA", "ForeignB"],
                "PairwiseIncompatibleEdges": [
                    ["ForeignA", "ForeignB"],
                ],
            },
        },
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(
            Signal="ComponentPort",
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Diagnostics["PlanIndependentMandatoryCut"] is True
    assert Classified.Diagnostics[
        "AssemblyPlanReassignmentAllowed"
    ] is False


def test_complete_non_port_global_cut_skips_port_reassignment():
    Failure = RoutingFailure(
        Reason=(
            RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
        ),
        Stage="PhysicalComponentGlobalPlanning",
        AffectedNets=("OrdinaryA", "OrdinaryB"),
        Diagnostics={"GlobalPlanDomainComplete": True},
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(
            Signal="ComponentPort",
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Diagnostics["PlanIndependentGlobalCut"] is True
    assert Classified.Diagnostics[
        "AssemblyPlanReassignmentAllowed"
    ] is False


def test_global_mandatory_cut_includes_graph_dependency_signals():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="GeneratedPortalCapacityCertification",
        AffectedNets=("ForeignA",),
        Diagnostics={
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
                "ConflictSignals": ["ForeignB"],
                "CongestionCutSignals": ["ComponentPort"],
            },
        },
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(
            Signal="ComponentPort",
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.AffectedNets == (
        "ComponentPort",
        "ForeignA",
        "ForeignB",
    )
    assert Classified.Diagnostics[
        "AssemblyPlanDependencySignals"
    ] == ["ComponentPort", "ForeignA", "ForeignB"]
    assert Classified.Diagnostics["ConflictGraph"][
        "ConflictSignals"
    ] == ["ComponentPort", "ForeignA", "ForeignB"]
    assert Classified.Diagnostics["PlanIndependentMandatoryCut"] is False
    assert Classified.Diagnostics[
        "AssemblyPlanReassignmentAllowed"
    ] is True


def test_bounded_fixed_portal_sample_cannot_claim_complete_global_domain():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="InitialCandidateAssignment",
        AffectedNets=("OrdinaryA", "OrdinaryB"),
        Diagnostics={
            "GlobalPlanDomainComplete": True,
            "CompleteAssignmentCutProof": True,
            "MandatoryAccessProof": {
                "Kind": "generated-fixed-portal-domain-exhausted",
                "Complete": True,
                "BudgetExhausted": False,
                "DeadlineExceeded": False,
            },
        },
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(Signal="ComponentPort"),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Reason == (
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete
    )
    assert Classified.Diagnostics["GlobalPlanDomainComplete"] is False
    assert Classified.Diagnostics[
        "AmbiguousFixedPortalProofRejected"
    ] is True


def test_exact_global_cut_contains_only_ports_and_feedthroughs():
    def Claims(Wires):
        return RoutingResourceClaims(
            WireCells=frozenset(Wires),
            ElectricalCells=frozenset(Wires),
        )

    def Channel(Signal, Wires, Feedthroughs=()):
        return SimpleNamespace(
            Signal=Signal,
            Claims=Claims(Wires),
            FeedthroughComponentIds=Feedthroughs,
        )

    Channels = (
        Channel("Port", {(0, 1, 0)}),
        Channel("Feed", {(5, 1, 0)}, ("component",)),
        Channel("ConflictA", {(10, 1, 0)}),
        Channel("ConflictB", {(10, 1, 0)}),
        Channel("Ordinary", {(20, 1, 0)}),
    )
    Plan = SimpleNamespace(
        Ports=(SimpleNamespace(Signal="Port"),),
        Channels=(),
        Corridors=Channels,
        PlanningChannels=Channels,
    )

    assert SelectPhysicalComponentExactGlobalChannelSignals(Plan) == {
        "Feed",
        "Port",
    }


def test_exact_global_preparation_excludes_unowned_corridors_from_base_claims():
    Source = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    IndexStart = Source.index(
        "def EnsurePhysicalAssignmentIndexComplete()"
    )
    IndexEnd = Source.index(
        "EnsurePhysicalAssignmentIndexComplete()",
        IndexStart + len(
            "def EnsurePhysicalAssignmentIndexComplete()"
        ),
    )
    IndexPreparation = Source[IndexStart:IndexEnd]
    BaseStart = Source.index("if BaseValues is None:", IndexEnd)
    BaseEnd = Source.index("if BaseValues:", BaseStart)
    BasePreparation = Source[BaseStart:BaseEnd]
    Assignment = Source[BaseEnd:Source.index(
        "def RaiseForNativeAssignmentDeadline",
        BaseEnd,
    )]

    assert "PhysicalAssemblyPlan.PlanningChannels" not in IndexPreparation
    assert "Channel.Claims" not in IndexPreparation
    assert "AssignmentIndexed.EncodeClaims(Channel.Claims)" not in (
        BasePreparation
    )
    assert "ExactPhysicalSignals" not in BasePreparation
    assert "PlanAuthoritativeRoutesWithBaseBounded" in Assignment
    assert "PlanAuthoritativeRoutesWithBase(" in Assignment


def test_global_complete_nonmandatory_proof_without_port_skips_reassignment():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="InitialCandidateAssignment",
        AffectedNets=("ForeignA", "ForeignB"),
        Diagnostics={
            "MandatoryAccessProof": {
                "Kind": "ordinary-route-domain-exhausted",
                "Complete": True,
                "BudgetExhausted": False,
                "DeadlineExceeded": False,
            },
            "ConflictGraph": {
                "Classification": "detailed-congestion-cut",
                "ConflictSignals": ["ForeignA", "ForeignB"],
            },
        },
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(Signal="ComponentPort"),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Diagnostics["GlobalPlanDomainComplete"] is True
    assert Classified.Diagnostics["PlanIndependentMandatoryCut"] is False
    assert Classified.Diagnostics[
        "AssemblyPlanReassignmentAllowed"
    ] is False

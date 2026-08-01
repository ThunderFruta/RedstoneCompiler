import inspect
from types import SimpleNamespace

import pytest

from Compiler.Placement.PcbFlow import _PlaceAndRoutePcbWithPolicy
import Compiler.Routing.AuthoritativePlanner as AuthoritativePlanner
import Compiler.Routing.Pcb as RoutingPcb
from Compiler.Routing.Pcb import (
    ReplanPhysicalComponentAssembly,
    SolvePreparedPhysicalComponentEligibility,
)
from Compiler.Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)


def test_local_unsat_rejects_only_the_complete_assembly_plan():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Start = Source.index("ComponentSolve = CompileClosedComponent(")
    End = Source.index("assert ComponentSolve.Template is not None", Start)
    LocalCompilation = Source[Start:End]

    assert "RejectedPhysicalComponentPortAssignmentFingerprints" in (
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
    assert "ReplanPhysicalComponentAssembly(" in LocalCompilation


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

    def Solve(Value, Resources, *, WorkCheck=None):
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


def test_retained_placements_prepare_eligibility_before_any_solve():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    QueueStart = Source.index('"prepare-eligibility",')
    PhaseOrder = Source.index(
        'if Entry[1][0] == "prepare-eligibility"',
        QueueStart,
    )
    SolveMarker = Source.index(
        '"solve-prepared-eligibility",',
        PhaseOrder,
    )
    SolveCall = Source.index(
        "SolvePreparedPhysicalComponentEligibility(",
        SolveMarker,
    )

    assert QueueStart < PhaseOrder < SolveMarker < SolveCall
    assert "PreparedEligibilityByState[" in Source[PhaseOrder:SolveMarker]


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

    def Solve(Value, *, Resources, Deadline):
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

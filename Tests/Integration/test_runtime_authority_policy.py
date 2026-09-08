"""Outcome-first coverage for Joint's production Runtime authority policy."""

from dataclasses import FrozenInstanceError, replace
from time import monotonic

import pytest

from PhysicalDesign.Contracts.Failures import (
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Contracts.Runtime import (
    RuntimeCancellationSnapshot,
    RuntimeFreshness,
    RuntimeLifecycle,
    RuntimeWorkRequest,
    RuntimeWorkScope,
)
from PhysicalDesign.Orchestration.RuntimePolicy import (
    AttachPhysicalComponentUnaryRuntimeDiagnostics,
    CompilePhysicalComponentUnarySupportWithRuntimeAuthority,
    DecidePhysicalComponentUnaryRuntime,
)
from PhysicalDesign.Policy import (
    RoutingAwarePlacementAccessPhysicalDesignPolicy,
    RuntimeExecutionPolicy,
)
import PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains as SymbolicDomains
from PhysicalDesign.Runtime.Reliability import RoutingDeadline
from PhysicalDesign.Runtime.SpawnedWork import (
    RuntimeSpawnedWorkCleanupIncomplete,
    RuntimeSpawnedWorkOwnedContinuation,
)
from Tests.PhysicalDesign.Runtime.test_bounded_work import (
    _RealUnaryInputs,
)


Policy = RoutingAwarePlacementAccessPhysicalDesignPolicy


def _Deadline(ExpiresAt: float = 120.0) -> RoutingDeadline:
    return RoutingDeadline(
        StartedAt=0.0,
        ExpiresAt=ExpiresAt,
        ExpirationKind="StageReserveExpired",
    )


def _Request(DeadlineAt: float) -> RuntimeWorkRequest:
    return RuntimeWorkRequest(
        TaskIdentity="unary:test",
        Operation="compile-physical-symbolic-unary-aperture",
        Scope=RuntimeWorkScope(
            DomainIdentity="unary-domain",
            DependencyIdentities=("factor-domain", "resource-graph"),
        ),
        Lifecycle=RuntimeLifecycle.Queued,
        Freshness=RuntimeFreshness.Current,
        DeadlineAt=DeadlineAt,
        WorkCap=100,
        Cancellation=RuntimeCancellationSnapshot(
            Requested=False,
            Identity="unary:test:cancellation",
        ),
    )


def test_runtime_policy_round_trips_exactly_and_rejects_corruption() -> None:
    RuntimePolicy = RuntimeExecutionPolicy(
        Enabled=True,
        ForceTerminationAuthorized=False,
    )
    Document = RuntimePolicy.ToDictionary()

    assert RuntimeExecutionPolicy.FromDictionary(Document) == RuntimePolicy
    assert Document["SchemaVersion"] == "joint-runtime-execution-policy-v1"
    assert Document["PolicyIdentity"].startswith("joint-runtime-policy-v1:")
    assert Document["PressureIdentity"].startswith(
        "joint-runtime-pressure-v1:"
    )
    with pytest.raises(FrozenInstanceError):
        RuntimePolicy.Enabled = False
    with pytest.raises(ValueError, match="policy identity mismatch"):
        RuntimeExecutionPolicy.FromDictionary({
            **Document,
            "PolicyIdentity": "forged-policy",
        })
    with pytest.raises(ValueError, match="invalid .* fields"):
        RuntimeExecutionPolicy.FromDictionary({**Document, "Extra": True})


@pytest.mark.parametrize(
    "Change",
    (
        {"Enabled": True},
        {"ExecutionMode": "future-pool"},
        {"CleanupReserveSeconds": 1.25},
        {"MaximumCooperativeGraceSeconds": 0.2},
        {"ForceTerminationAuthorized": False},
        {"UnifiedNativeAuthority": True},
    ),
)
def test_runtime_policy_identity_covers_every_authority_field(Change) -> None:
    Baseline = RuntimeExecutionPolicy(Enabled=False)
    Changed = replace(Baseline, **Change)

    assert Baseline.PolicyIdentity != Changed.PolicyIdentity
    assert Baseline.PressureIdentity == Changed.PressureIdentity


@pytest.mark.parametrize(
    "Changes",
    (
        {"Enabled": 1},
        {"ExecutionMode": ""},
        {"CleanupReserveSeconds": 1},
        {"CleanupReserveSeconds": float("inf")},
        {"CleanupReserveSeconds": 0.0},
        {"MaximumCooperativeGraceSeconds": -0.1},
        {
            "CleanupReserveSeconds": 0.5,
            "MaximumCooperativeGraceSeconds": 0.6,
        },
        {"ForceTerminationAuthorized": 1},
        {"UnifiedNativeAuthority": 1},
    ),
)
def test_runtime_policy_rejects_inexact_or_unsafe_fields(Changes) -> None:
    with pytest.raises((TypeError, ValueError)):
        RuntimeExecutionPolicy(**Changes)


@pytest.mark.parametrize(
    ("RuntimePolicy", "Reason"),
    (
        (None, "missing-policy"),
        (object(), "unsupported-policy"),
        (RuntimeExecutionPolicy(Enabled=False), "policy-disabled"),
        (
            RuntimeExecutionPolicy(Enabled=True, ExecutionMode="future-pool"),
            "unsupported-execution-mode",
        ),
        (
            RuntimeExecutionPolicy(Enabled=True, PressureMode="deny"),
            "pressure-denied",
        ),
        (
            RuntimeExecutionPolicy(Enabled=True, PressureMode="future"),
            "unsupported-pressure-mode",
        ),
        (
            RuntimeExecutionPolicy(Enabled=True, UnifiedNativeAuthority=True),
            "unified-native-authority-unsupported",
        ),
    ),
)
def test_unavailable_runtime_modes_preserve_diagnosed_serial_fallback(
    RuntimePolicy,
    Reason,
) -> None:
    Decision = DecidePhysicalComponentUnaryRuntime(
        replace(Policy, RuntimeExecution=RuntimePolicy),
        _Deadline(),
        ObservedAt=100.0,
    )

    assert Decision.ExecutionMode == "serial"
    assert Decision.ExecutionDeadlineAt == 120.0
    assert Decision.CleanupCutoffAt == 120.0
    assert Decision.WorkDeadlineAt is None
    assert Decision.FallbackReason == Reason
    assert Decision.UnifiedNativeAuthority is False
    with pytest.raises(RuntimeError, match="serial runtime decision"):
        Decision.BuildAuthority(_Request(120.0))
    Diagnostics = AttachPhysicalComponentUnaryRuntimeDiagnostics(
        Decision,
        {"Complete": True},
    )
    assert Diagnostics["RuntimeAuthorityDecision"]["ExecutionMode"] == "serial"
    assert Diagnostics["RuntimeAuthorityDecision"]["FallbackReason"] == Reason


def test_enabled_policy_derives_exact_authority_from_enclosing_deadline() -> None:
    RuntimePolicy = RuntimeExecutionPolicy(
        Enabled=True,
        CleanupReserveSeconds=1.25,
        MaximumCooperativeGraceSeconds=0.25,
        ForceTerminationAuthorized=False,
    )
    Decision = DecidePhysicalComponentUnaryRuntime(
        replace(Policy, RuntimeExecution=RuntimePolicy),
        _Deadline(200.0),
        ObservedAt=150.0,
    )
    Authority = Decision.BuildAuthority(_Request(198.75))

    assert Decision.ExecutionMode == "bounded-spawn"
    assert Decision.ExecutionDeadlineAt == 198.75
    assert Authority.WorkDeadlineAt == 198.75
    assert Authority.CleanupCutoffAt == 200.0
    assert Authority.MaximumCooperativeGraceSeconds == 0.25
    assert Authority.ForceTerminationAuthorized is False
    assert Authority.PolicyIdentity == RuntimePolicy.PolicyIdentity
    assert Authority.PressureIdentity == RuntimePolicy.PressureIdentity
    assert Decision.UnifiedNativeAuthority is False
    with pytest.raises(ValueError, match="decided deadline"):
        Decision.BuildAuthority(_Request(198.5))


def test_pressure_identity_changes_without_changing_runtime_policy_identity() -> None:
    Admitted = RuntimeExecutionPolicy(Enabled=True, PressureMode="admit")
    Denied = replace(Admitted, PressureMode="deny")

    assert Admitted.PolicyIdentity == Denied.PolicyIdentity
    assert Admitted.PressureIdentity != Denied.PressureIdentity


@pytest.mark.parametrize("ObservedAt", (119.0, 120.0, 121.0))
def test_insufficient_window_fails_before_admission_without_a_proof(
    ObservedAt,
) -> None:
    with pytest.raises(RoutingStageError) as Error:
        DecidePhysicalComponentUnaryRuntime(
            Policy,
            _Deadline(120.0),
            ObservedAt=ObservedAt,
        )

    Failure = Error.value.Failure
    Decision = Failure.Diagnostics["RuntimeAuthorityDecision"]
    assert Failure.Reason is RoutingFailureReason.RuntimeBudgetExceeded
    assert Failure.Stage == "PhysicalComponentUnaryRuntimeAdmission"
    assert Decision["AdmissionReason"] == "insufficient-useful-work-window"
    assert Decision["ChildAdmissionAttempted"] is False
    assert Decision["InfeasibilityProofPublished"] is False
    assert "ProofIdentity" not in Decision
    assert "Unsatisfiable" not in Failure.ToDictionary()


def test_real_unary_insufficient_window_never_reaches_child_admission(
    monkeypatch,
) -> None:
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    Spawned = False

    def RejectSpawn(*_Arguments, **_Options):
        nonlocal Spawned
        Spawned = True
        raise AssertionError("an impossible authority window must not spawn")

    monkeypatch.setattr(
        SymbolicDomains,
        "ExecuteBoundedSpawnedWorkBatch",
        RejectSpawn,
    )
    ObservedAt = float(monotonic())
    with pytest.raises(RoutingStageError) as Error:
        CompilePhysicalComponentUnarySupportWithRuntimeAuthority(
            Policy,
            _Deadline(ObservedAt + 0.5),
            Problem,
            FactorDomain,
            Signals,
            ObservedAt=ObservedAt,
            NetStateCache={},
        )

    Failure = Error.value.Failure
    Decision = Failure.Diagnostics["RuntimeAuthorityDecision"]
    assert Spawned is False
    assert Failure.Reason is RoutingFailureReason.RuntimeBudgetExceeded
    assert Decision["ChildAdmissionAttempted"] is False
    assert Decision["InfeasibilityProofPublished"] is False


def test_real_unary_path_uses_exact_production_authority(
    monkeypatch,
) -> None:
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    Submitted = []
    Original = SymbolicDomains.ExecuteBoundedSpawnedWorkBatch

    def Observe(Items, Operation, Limits, **Options):
        Submitted.extend(Items)
        return Original(Items, Operation, Limits, **Options)

    monkeypatch.setattr(
        SymbolicDomains,
        "ExecuteBoundedSpawnedWorkBatch",
        Observe,
    )
    Deadline = _Deadline(monotonic() + 30.0)
    Clauses, Diagnostics = (
        CompilePhysicalComponentUnarySupportWithRuntimeAuthority(
            Policy,
            Deadline,
            Problem,
            FactorDomain,
            Signals,
            ObservedAt=float(monotonic()),
            NetStateCache={},
        )
    )

    assert Submitted
    assert Clauses
    assert Diagnostics["Complete"] is True
    assert Policy.RuntimeExecution.CleanupReserveSeconds == 1.0
    assert Policy.RuntimeExecution.MaximumCooperativeGraceSeconds == 0.1
    RuntimeDecision = Diagnostics["RuntimeAuthorityDecision"]
    assert RuntimeDecision["ExecutionMode"] == "bounded-spawn"
    assert RuntimeDecision["WorkDeadlineAt"] == (
        Deadline.ExpiresAt - Policy.RuntimeExecution.CleanupReserveSeconds
    )
    assert RuntimeDecision["CleanupCutoffAt"] == Deadline.ExpiresAt
    for Item in Submitted:
        assert Item.Request.DeadlineAt == Item.Authority.WorkDeadlineAt
        assert Item.Authority.CleanupCutoffAt == Deadline.ExpiresAt
        assert Item.Authority.PolicyIdentity == (
            Policy.RuntimeExecution.PolicyIdentity
        )
        assert Item.Authority.PressureIdentity == (
            Policy.RuntimeExecution.PressureIdentity
        )
        assert Item.Authority.ForceTerminationAuthorized is True


def test_real_unary_cleanup_breach_propagates_exact_continuation_identity(
    monkeypatch,
) -> None:
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    Handle = object()
    Receipt = object()
    Continuation = RuntimeSpawnedWorkOwnedContinuation(
        TaskIdentity="unary:owned",
        Handle=Handle,
        Receipt=Receipt,
    )
    CleanupError = RuntimeSpawnedWorkCleanupIncomplete((Continuation,))

    def Breach(*_Arguments, **_Options):
        raise CleanupError

    monkeypatch.setattr(
        SymbolicDomains,
        "ExecuteBoundedSpawnedWorkBatch",
        Breach,
    )

    with pytest.raises(RuntimeSpawnedWorkCleanupIncomplete) as Error:
        CompilePhysicalComponentUnarySupportWithRuntimeAuthority(
            Policy,
            _Deadline(monotonic() + 30.0),
            Problem,
            FactorDomain,
            Signals,
            ObservedAt=float(monotonic()),
            NetStateCache={},
        )

    assert Error.value is CleanupError
    assert Error.value.OwnedContinuations == (Continuation,)
    assert Error.value.OwnedContinuations[0].Handle is Handle
    assert Error.value.OwnedContinuations[0].Receipt is Receipt


def test_real_unary_disabled_policy_reports_actual_serial_fallback(
    monkeypatch,
) -> None:
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)

    def RejectSpawn(*_Arguments, **_Options):
        raise AssertionError("disabled production policy must not spawn")

    monkeypatch.setattr(
        SymbolicDomains,
        "ExecuteBoundedSpawnedWorkBatch",
        RejectSpawn,
    )
    Clauses, Diagnostics = (
        CompilePhysicalComponentUnarySupportWithRuntimeAuthority(
            replace(
                Policy,
                RuntimeExecution=RuntimeExecutionPolicy(Enabled=False),
            ),
            _Deadline(monotonic() + 30.0),
            Problem,
            FactorDomain,
            Signals,
            ObservedAt=float(monotonic()),
            NetStateCache={},
        )
    )

    assert Clauses
    assert Diagnostics["Complete"] is True
    Decision = Diagnostics["RuntimeAuthorityDecision"]
    assert Decision["ExecutionMode"] == "serial"
    assert Decision["AuthorizedExecutionMode"] == "serial"
    assert Decision["RequestedExecutionMode"] == "bounded-spawn"
    assert Decision["FallbackReason"] == "policy-disabled"


def test_runtime_policy_explicitly_does_not_authorize_native_work() -> None:
    RuntimePolicy = Policy.RuntimeExecution
    assert RuntimePolicy is not None
    assert RuntimePolicy.UnifiedNativeAuthority is False
    Decision = DecidePhysicalComponentUnaryRuntime(
        Policy,
        _Deadline(),
        ObservedAt=100.0,
    )
    assert Decision.ToDictionary()["UnifiedNativeAuthority"] is False

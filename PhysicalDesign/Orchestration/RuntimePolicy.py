"""Joint-owned policy decision for bounded symbolic unary workers."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Iterable

from PhysicalDesign.Contracts.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Contracts.Runtime import (
    RuntimeWorkAuthority,
    RuntimeWorkRequest,
)
from PhysicalDesign.Policy import PhysicalDesignPolicy, RuntimeExecutionPolicy
import PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains as SymbolicDomains
from PhysicalDesign.Runtime.Reliability import RoutingDeadline


@dataclass(frozen=True)
class PhysicalComponentUnaryRuntimeDecision:
    """One immutable coordinator decision before unary child admission."""

    ExecutionMode: str
    RequestedExecutionMode: str
    FallbackReason: str | None
    ExecutionDeadlineAt: float
    WorkDeadlineAt: float | None
    CleanupCutoffAt: float
    CleanupReserveSeconds: float | None
    MaximumCooperativeGraceSeconds: float | None
    ForceTerminationAuthorized: bool | None
    PolicyIdentity: str
    PressureIdentity: str
    UnifiedNativeAuthority: bool = False
    SchemaVersion: str = "physical-unary-runtime-decision-v1"

    def BuildAuthority(
        self,
        Request: RuntimeWorkRequest,
    ) -> RuntimeWorkAuthority:
        """Bind one admitted request to the exact coordinator grant."""
        if self.ExecutionMode != "bounded-spawn":
            raise RuntimeError("serial runtime decision cannot grant authority")
        if self.WorkDeadlineAt is None:
            raise RuntimeError("bounded-spawn decision has no work deadline")
        if Request.DeadlineAt != self.WorkDeadlineAt:
            raise ValueError("runtime request does not carry the decided deadline")
        if self.MaximumCooperativeGraceSeconds is None:
            raise RuntimeError("bounded-spawn decision has no cooperative grace")
        if self.ForceTerminationAuthorized is None:
            raise RuntimeError("bounded-spawn decision has no force policy")
        return RuntimeWorkAuthority(
            WorkDeadlineAt=self.WorkDeadlineAt,
            CleanupCutoffAt=self.CleanupCutoffAt,
            MaximumCooperativeGraceSeconds=(
                self.MaximumCooperativeGraceSeconds
            ),
            ForceTerminationAuthorized=self.ForceTerminationAuthorized,
            PolicyIdentity=self.PolicyIdentity,
            PressureIdentity=self.PressureIdentity,
        )

    def ToDictionary(
        self,
        *,
        ActualExecutionMode: str | None = None,
    ) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "ExecutionMode": ActualExecutionMode or self.ExecutionMode,
            "AuthorizedExecutionMode": self.ExecutionMode,
            "RequestedExecutionMode": self.RequestedExecutionMode,
            "FallbackReason": self.FallbackReason,
            "ExecutionDeadlineAt": self.ExecutionDeadlineAt,
            "WorkDeadlineAt": self.WorkDeadlineAt,
            "CleanupCutoffAt": self.CleanupCutoffAt,
            "CleanupReserveSeconds": self.CleanupReserveSeconds,
            "MaximumCooperativeGraceSeconds": (
                self.MaximumCooperativeGraceSeconds
            ),
            "ForceTerminationAuthorized": self.ForceTerminationAuthorized,
            "PolicyIdentity": self.PolicyIdentity,
            "PressureIdentity": self.PressureIdentity,
            "UnifiedNativeAuthority": self.UnifiedNativeAuthority,
        }


def _SerialDecision(
    InterfaceDeadline: RoutingDeadline,
    Policy: RuntimeExecutionPolicy | None,
    Reason: str,
) -> PhysicalComponentUnaryRuntimeDecision:
    return PhysicalComponentUnaryRuntimeDecision(
        ExecutionMode="serial",
        RequestedExecutionMode=(
            "missing" if Policy is None else Policy.ExecutionMode
        ),
        FallbackReason=Reason,
        ExecutionDeadlineAt=float(InterfaceDeadline.ExpiresAt),
        WorkDeadlineAt=None,
        CleanupCutoffAt=float(InterfaceDeadline.ExpiresAt),
        CleanupReserveSeconds=(
            None if Policy is None else Policy.CleanupReserveSeconds
        ),
        MaximumCooperativeGraceSeconds=(
            None if Policy is None else Policy.MaximumCooperativeGraceSeconds
        ),
        ForceTerminationAuthorized=(
            None if Policy is None else Policy.ForceTerminationAuthorized
        ),
        PolicyIdentity=(
            "joint-runtime-policy-v1:missing"
            if Policy is None
            else Policy.PolicyIdentity
        ),
        PressureIdentity=(
            "joint-runtime-pressure-v1:missing"
            if Policy is None
            else Policy.PressureIdentity
        ),
    )


def DecidePhysicalComponentUnaryRuntime(
    Policy: PhysicalDesignPolicy,
    InterfaceDeadline: RoutingDeadline,
    *,
    ObservedAt: float,
) -> PhysicalComponentUnaryRuntimeDecision:
    """Select spawn authority or an explicit serial fallback before admission."""
    if type(Policy) is not PhysicalDesignPolicy:
        raise TypeError("Policy must be an exact PhysicalDesignPolicy")
    if type(InterfaceDeadline) is not RoutingDeadline:
        raise TypeError("InterfaceDeadline must be an exact RoutingDeadline")
    if type(ObservedAt) is not float or not isfinite(ObservedAt):
        raise TypeError("ObservedAt must be a finite exact float")
    RuntimePolicy = Policy.RuntimeExecution
    if RuntimePolicy is None:
        return _SerialDecision(InterfaceDeadline, None, "missing-policy")
    if type(RuntimePolicy) is not RuntimeExecutionPolicy:
        return _SerialDecision(InterfaceDeadline, None, "unsupported-policy")
    if not RuntimePolicy.Enabled:
        return _SerialDecision(
            InterfaceDeadline,
            RuntimePolicy,
            "policy-disabled",
        )
    if RuntimePolicy.ExecutionMode != "bounded-spawn":
        return _SerialDecision(
            InterfaceDeadline,
            RuntimePolicy,
            "unsupported-execution-mode",
        )
    if RuntimePolicy.PressureMode == "deny":
        return _SerialDecision(
            InterfaceDeadline,
            RuntimePolicy,
            "pressure-denied",
        )
    if RuntimePolicy.PressureMode != "admit":
        return _SerialDecision(
            InterfaceDeadline,
            RuntimePolicy,
            "unsupported-pressure-mode",
        )
    if RuntimePolicy.UnifiedNativeAuthority:
        return _SerialDecision(
            InterfaceDeadline,
            RuntimePolicy,
            "unified-native-authority-unsupported",
        )

    CleanupCutoffAt = float(InterfaceDeadline.ExpiresAt)
    WorkDeadlineAt = CleanupCutoffAt - RuntimePolicy.CleanupReserveSeconds
    if ObservedAt >= WorkDeadlineAt:
        Diagnostics = {
            "RuntimeAuthorityDecision": {
                "SchemaVersion": "physical-unary-runtime-decision-v1",
                "ExecutionMode": "not-admitted",
                "AuthorizedExecutionMode": "bounded-spawn",
                "RequestedExecutionMode": RuntimePolicy.ExecutionMode,
                "AdmissionReason": "insufficient-useful-work-window",
                "ObservedAt": ObservedAt,
                "WorkDeadlineAt": WorkDeadlineAt,
                "CleanupCutoffAt": CleanupCutoffAt,
                "CleanupReserveSeconds": (
                    RuntimePolicy.CleanupReserveSeconds
                ),
                "PolicyIdentity": RuntimePolicy.PolicyIdentity,
                "PressureIdentity": RuntimePolicy.PressureIdentity,
                "UnifiedNativeAuthority": False,
                "ChildAdmissionAttempted": False,
                "InfeasibilityProofPublished": False,
            },
            "Deadline": InterfaceDeadline.ToDictionary(),
            "DeadlineExpirationKind": InterfaceDeadline.ExpirationKind,
        }
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.RuntimeBudgetExceeded,
            Stage="PhysicalComponentUnaryRuntimeAdmission",
            Detail=(
                "the interface window cannot fund useful unary work and its "
                "reserved cleanup interval"
            ),
            RepairActions=(),
            Diagnostics=Diagnostics,
        ))
    return PhysicalComponentUnaryRuntimeDecision(
        ExecutionMode="bounded-spawn",
        RequestedExecutionMode=RuntimePolicy.ExecutionMode,
        FallbackReason=None,
        ExecutionDeadlineAt=WorkDeadlineAt,
        WorkDeadlineAt=WorkDeadlineAt,
        CleanupCutoffAt=CleanupCutoffAt,
        CleanupReserveSeconds=RuntimePolicy.CleanupReserveSeconds,
        MaximumCooperativeGraceSeconds=(
            RuntimePolicy.MaximumCooperativeGraceSeconds
        ),
        ForceTerminationAuthorized=(
            RuntimePolicy.ForceTerminationAuthorized
        ),
        PolicyIdentity=RuntimePolicy.PolicyIdentity,
        PressureIdentity=RuntimePolicy.PressureIdentity,
    )


def AttachPhysicalComponentUnaryRuntimeDiagnostics(
    Decision: PhysicalComponentUnaryRuntimeDecision,
    UnaryDiagnostics: dict[str, object],
) -> dict[str, object]:
    """Report the actual execution path without changing unary result facts."""
    ActualExecutionMode = (
        "bounded-spawn"
        if "UnarySignalSubmittedTaskCount" in UnaryDiagnostics
        else "serial"
    )
    return {
        **UnaryDiagnostics,
        "RuntimeAuthorityDecision": Decision.ToDictionary(
            ActualExecutionMode=ActualExecutionMode,
        ),
    }


def CompilePhysicalComponentUnarySupportWithRuntimeAuthority(
    Policy: PhysicalDesignPolicy,
    InterfaceDeadline: RoutingDeadline,
    Problem: Any,
    FactorDomain: Any,
    SignalDomain: Iterable[str],
    *,
    ObservedAt: float,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    NetStateCache: dict[str, Any] | None = None,
    CompletedClauseCache: dict[str, Any] | None = None,
    RouteClaimsConstructionCache: dict[Any, Any] | None = None,
    AllowParallelSignalCompilation: bool = True,
) -> tuple[frozenset[frozenset[tuple[str, str]]], dict[str, object]]:
    """Run the unary public API under one pre-admission Joint decision."""
    Decision = DecidePhysicalComponentUnaryRuntime(
        Policy,
        InterfaceDeadline,
        ObservedAt=ObservedAt,
    )
    Clauses, Diagnostics = (
        SymbolicDomains.CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            SignalDomain,
            DeadlineSeconds=None,
            AbsoluteDeadlineAt=Decision.ExecutionDeadlineAt,
            RuntimeAuthorityFactory=(
                Decision.BuildAuthority
                if Decision.ExecutionMode == "bounded-spawn"
                else None
            ),
            WorkCheck=WorkCheck,
            NetStateCache=NetStateCache,
            CompletedClauseCache=CompletedClauseCache,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
            AllowParallelSignalCompilation=AllowParallelSignalCompilation,
        )
    )
    return Clauses, AttachPhysicalComponentUnaryRuntimeDiagnostics(
        Decision,
        Diagnostics,
    )

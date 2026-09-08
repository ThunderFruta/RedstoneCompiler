"""Synchronous bounded-work adapter over the shared routing deadline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

from ..Contracts.Runtime import (
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeLifecycle,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
    RuntimeWorkExecution,
    RuntimeWorkProduct,
    RuntimeWorkRequest,
    RuntimeWorkResult,
)
from .Reliability import RoutingDeadline


class _BoundedWorkStop(Exception):
    def __init__(
        self,
        Reason: RuntimeTerminalReason,
        Lifecycle: RuntimeLifecycle,
    ) -> None:
        self.Reason = Reason
        self.Lifecycle = Lifecycle
        super().__init__(Reason.value)


@dataclass
class RuntimeWorkControl:
    """Process-local checkpoint state; it creates no competing deadline."""

    Request: RuntimeWorkRequest
    Deadline: RoutingDeadline
    CancellationCheck: Callable[[], bool]
    WorkUnits: int = 0

    def Checkpoint(self) -> None:
        """Observe the deadline and cancellation state at a safe point."""
        if self.Deadline.IsExpired():
            raise _BoundedWorkStop(
                RuntimeTerminalReason.DeadlineExhausted,
                RuntimeLifecycle.Completed,
            )
        if self.Request.Cancellation.Requested or self.CancellationCheck():
            raise _BoundedWorkStop(
                RuntimeTerminalReason.Cancelled,
                RuntimeLifecycle.TerminatedGracefully,
            )

    def ObservePerformedWork(self, Units: int) -> None:
        """Record work already performed, then classify any exceeded bound."""
        if isinstance(Units, bool) or not isinstance(Units, int) or Units < 0:
            raise ValueError("performed Units must be a non-negative integer")
        self.WorkUnits += Units
        if self.Deadline.IsExpired():
            raise _BoundedWorkStop(
                RuntimeTerminalReason.DeadlineExhausted,
                RuntimeLifecycle.Completed,
            )
        if self.Request.Cancellation.Requested or self.CancellationCheck():
            raise _BoundedWorkStop(
                RuntimeTerminalReason.Cancelled,
                RuntimeLifecycle.TerminatedGracefully,
            )
        if self.WorkUnits > self.Request.WorkCap:
            raise _BoundedWorkStop(
                RuntimeTerminalReason.WorkCapExhausted,
                RuntimeLifecycle.Completed,
            )

    def StopIfWorkCapReached(self) -> None:
        """Classify an incomplete operation that consumed its full allowance."""
        if self.WorkUnits >= self.Request.WorkCap:
            raise _BoundedWorkStop(
                RuntimeTerminalReason.WorkCapExhausted,
                RuntimeLifecycle.Completed,
            )


Payload = TypeVar("Payload")


def _UnresolvedExecution(
    Request: RuntimeWorkRequest,
    Control: RuntimeWorkControl,
    Reason: RuntimeTerminalReason,
    Lifecycle: RuntimeLifecycle,
    *,
    Diagnostics: tuple[tuple[str, str], ...] = (),
) -> RuntimeWorkExecution[Payload]:
    return RuntimeWorkExecution(
        Result=RuntimeWorkResult(
            TaskIdentity=Request.TaskIdentity,
            Operation=Request.Operation,
            Scope=Request.Scope,
            SearchOutcome=RuntimeSearchOutcome.Unresolved,
            Lifecycle=Lifecycle,
            Freshness=Request.Freshness,
            ClaimStrength=RuntimeClaimStrength.Continuation,
            CommitEligibility=RuntimeCommitEligibility.Ineligible,
            TerminalReason=Reason,
            WorkUnits=Control.WorkUnits,
            Diagnostics=Diagnostics,
        ),
        Value=None,
    )


def ExecuteBoundedRuntimeWork(
    Request: RuntimeWorkRequest,
    Deadline: RoutingDeadline,
    Operation: Callable[[RuntimeWorkControl], RuntimeWorkProduct[Payload]],
    *,
    CancellationCheck: Callable[[], bool] | None = None,
) -> RuntimeWorkExecution[Payload]:
    """Run one operation and return a terminal typed result on every exit."""
    if Request.DeadlineAt != Deadline.ExpiresAt:
        raise ValueError("work request must carry the exact shared deadline")
    Control = RuntimeWorkControl(
        Request=Request,
        Deadline=Deadline,
        CancellationCheck=(CancellationCheck or (lambda: False)),
    )
    try:
        if Request.WorkCap == 0:
            raise _BoundedWorkStop(
                RuntimeTerminalReason.WorkCapExhausted,
                RuntimeLifecycle.Completed,
            )
        Control.Checkpoint()
        Product = Operation(Control)
        if not isinstance(Product, RuntimeWorkProduct):
            raise TypeError("bounded operation must return RuntimeWorkProduct")
        # A completed value cannot win a race with its already-expired bound.
        Control.Checkpoint()
        Result = RuntimeWorkResult(
            TaskIdentity=Request.TaskIdentity,
            Operation=Request.Operation,
            Scope=Request.Scope,
            SearchOutcome=Product.SearchOutcome,
            Lifecycle=RuntimeLifecycle.Completed,
            Freshness=Request.Freshness,
            ClaimStrength=Product.ClaimStrength,
            CommitEligibility=RuntimeCommitEligibility.Ineligible,
            TerminalReason=Product.TerminalReason,
            CandidateIdentity=Product.CandidateIdentity,
            ProofIdentity=Product.ProofIdentity,
            WorkUnits=Control.WorkUnits,
            Diagnostics=Product.Diagnostics,
        )
        return RuntimeWorkExecution(Result=Result, Value=Product.Value)
    except _BoundedWorkStop as Stop:
        return _UnresolvedExecution(
            Request,
            Control,
            Stop.Reason,
            Stop.Lifecycle,
        )
    except Exception as Error:
        return _UnresolvedExecution(
            Request,
            Control,
            RuntimeTerminalReason.WorkerFailure,
            RuntimeLifecycle.Failed,
            Diagnostics=(("ExceptionType", type(Error).__name__),),
        )

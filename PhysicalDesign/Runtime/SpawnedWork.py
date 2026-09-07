"""Bounded admission and backpressure for one-shot spawned work batches."""

from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
import multiprocessing
import pickle
from time import monotonic
from typing import Callable, Generic, TypeVar

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


Payload = TypeVar("Payload")
ResultPayload = TypeVar("ResultPayload")


@dataclass(frozen=True)
class RuntimeSpawnedWorkLimits:
    """One batch's hard queue, process, payload, and result bounds."""

    MaximumQueuedTasks: int
    MaximumInFlightTasks: int
    MaximumPayloadBytes: int
    MaximumResultBytes: int

    def __post_init__(self) -> None:
        for Name, Value, Minimum in (
            ("MaximumQueuedTasks", self.MaximumQueuedTasks, 0),
            ("MaximumInFlightTasks", self.MaximumInFlightTasks, 1),
            ("MaximumPayloadBytes", self.MaximumPayloadBytes, 1),
            # Leave room for the small typed unresolved envelope used when a
            # successful process-local result exceeds its publication bound.
            ("MaximumResultBytes", self.MaximumResultBytes, 1024),
        ):
            if isinstance(Value, bool) or not isinstance(Value, int) or Value < Minimum:
                raise ValueError(f"{Name} must be an integer >= {Minimum}")

    @property
    def MaximumAdmittedTasks(self) -> int:
        return self.MaximumQueuedTasks + self.MaximumInFlightTasks


@dataclass(frozen=True)
class RuntimeSpawnedWorkItem(Generic[Payload]):
    """One immutable request and the exact payload sent to its child."""

    Request: RuntimeWorkRequest
    Payload: Payload


@dataclass(frozen=True)
class RuntimeSpawnedWorkBatch(Generic[ResultPayload]):
    """Input-ordered outcomes plus observed admission/resource maxima."""

    Executions: tuple[tuple[str, RuntimeWorkExecution[ResultPayload]], ...]
    CompletionOrder: tuple[str, ...]
    AdmittedTaskCount: int
    AdmissionRejectedTaskCount: int
    SubmittedTaskCount: int
    PeakQueuedTaskCount: int
    PeakInFlightTaskCount: int
    MaximumObservedPayloadBytes: int
    MaximumObservedResultBytes: int


@dataclass(frozen=True)
class _SpawnedChildReturn(Generic[ResultPayload]):
    Execution: RuntimeWorkExecution[ResultPayload]
    ResultBytes: int


def _UnresolvedExecution(
    Request: RuntimeWorkRequest,
    Reason: RuntimeTerminalReason,
    Lifecycle: RuntimeLifecycle = RuntimeLifecycle.Completed,
    *,
    Diagnostics: tuple[tuple[str, str], ...] = (),
) -> RuntimeWorkExecution:
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
            WorkUnits=0,
            Diagnostics=Diagnostics,
        ),
        Value=None,
    )


def _ExecuteSpawnedWorkItem(
    Operation: Callable[
        [Payload, RuntimeWorkRequest],
        RuntimeWorkProduct[ResultPayload],
    ],
    Item: RuntimeSpawnedWorkItem[Payload],
    MaximumResultBytes: int,
) -> _SpawnedChildReturn[ResultPayload]:
    """Run one already-admitted item and bound what crosses to the parent."""
    Request = Item.Request
    if monotonic() >= Request.DeadlineAt:
        return _SpawnedChildReturn(
            _UnresolvedExecution(Request, RuntimeTerminalReason.DeadlineExhausted),
            0,
        )
    if Request.Cancellation.Requested:
        return _SpawnedChildReturn(
            _UnresolvedExecution(
                Request,
                RuntimeTerminalReason.Cancelled,
                RuntimeLifecycle.TerminatedGracefully,
            ),
            0,
        )
    if Request.WorkCap == 0:
        return _SpawnedChildReturn(
            _UnresolvedExecution(Request, RuntimeTerminalReason.WorkCapExhausted),
            0,
        )
    try:
        Product = Operation(Item.Payload, Request)
        if not isinstance(Product, RuntimeWorkProduct):
            raise TypeError("spawned operation must return RuntimeWorkProduct")
        if monotonic() >= Request.DeadlineAt:
            return _SpawnedChildReturn(
                _UnresolvedExecution(
                    Request,
                    RuntimeTerminalReason.DeadlineExhausted,
                ),
                0,
            )
        Execution = RuntimeWorkExecution(
            Result=RuntimeWorkResult(
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
                WorkUnits=0,
                Diagnostics=Product.Diagnostics,
            ),
            Value=Product.Value,
        )
        ResultBytes = len(pickle.dumps(Execution, protocol=pickle.HIGHEST_PROTOCOL))
        if ResultBytes > MaximumResultBytes:
            return _SpawnedChildReturn(
                _UnresolvedExecution(
                    Request,
                    RuntimeTerminalReason.ResultLimitExceeded,
                    Diagnostics=(
                        ("MaximumResultBytes", str(MaximumResultBytes)),
                        ("ObservedResultBytes", str(ResultBytes)),
                    ),
                ),
                ResultBytes,
            )
        return _SpawnedChildReturn(Execution, ResultBytes)
    except BaseException as Error:
        return _SpawnedChildReturn(
            _UnresolvedExecution(
                Request,
                RuntimeTerminalReason.WorkerFailure,
                RuntimeLifecycle.Failed,
                Diagnostics=(("ExceptionType", type(Error).__name__),),
            ),
            0,
        )


def ExecuteBoundedSpawnedWorkBatch(
    Items: tuple[RuntimeSpawnedWorkItem[Payload], ...],
    Operation: Callable[
        [Payload, RuntimeWorkRequest],
        RuntimeWorkProduct[ResultPayload],
    ],
    Limits: RuntimeSpawnedWorkLimits,
    *,
    WaitObserver: Callable[[str, str], None] | None = None,
) -> RuntimeSpawnedWorkBatch[ResultPayload]:
    """Admit a finite batch and submit only as child capacity becomes free."""
    TaskIdentities = tuple(Item.Request.TaskIdentity for Item in Items)
    if len(set(TaskIdentities)) != len(TaskIdentities):
        raise ValueError("spawned work task identities must be unique")

    Executions: list[RuntimeWorkExecution[ResultPayload] | None] = [
        None for _Item in Items
    ]
    PayloadBytesByIndex: dict[int, int] = {}
    EligibleIndexes = []
    for Index, Item in enumerate(Items):
        Request = Item.Request
        if monotonic() >= Request.DeadlineAt:
            Executions[Index] = _UnresolvedExecution(
                Request,
                RuntimeTerminalReason.DeadlineExhausted,
            )
            continue
        if Request.WorkCap == 0:
            Executions[Index] = _UnresolvedExecution(
                Request,
                RuntimeTerminalReason.WorkCapExhausted,
            )
            continue
        try:
            PayloadBytes = len(
                pickle.dumps(Item, protocol=pickle.HIGHEST_PROTOCOL)
            )
        except Exception as Error:
            Executions[Index] = _UnresolvedExecution(
                Request,
                RuntimeTerminalReason.WorkerFailure,
                RuntimeLifecycle.Failed,
                Diagnostics=(("ExceptionType", type(Error).__name__),),
            )
            continue
        PayloadBytesByIndex[Index] = PayloadBytes
        if PayloadBytes > Limits.MaximumPayloadBytes:
            Executions[Index] = _UnresolvedExecution(
                Request,
                RuntimeTerminalReason.PayloadLimitExceeded,
                Diagnostics=(
                    ("MaximumPayloadBytes", str(Limits.MaximumPayloadBytes)),
                    ("ObservedPayloadBytes", str(PayloadBytes)),
                ),
            )
            continue
        EligibleIndexes.append(Index)

    AdmittedIndexes = EligibleIndexes[:Limits.MaximumAdmittedTasks]
    RejectedIndexes = EligibleIndexes[Limits.MaximumAdmittedTasks:]
    for Index in RejectedIndexes:
        Executions[Index] = _UnresolvedExecution(
            Items[Index].Request,
            RuntimeTerminalReason.AdmissionRejected,
            Diagnostics=(
                ("MaximumAdmittedTasks", str(Limits.MaximumAdmittedTasks)),
            ),
        )

    Ready = deque(AdmittedIndexes)
    PeakQueued = max(0, len(Ready) - Limits.MaximumInFlightTasks)
    PeakInFlight = 0
    Submitted = 0
    CompletionOrder = []
    MaximumObservedResultBytes = 0
    Futures: dict[object, int] = {}

    if Ready:
        try:
            Context = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(
                max_workers=min(Limits.MaximumInFlightTasks, len(Ready)),
                mp_context=Context,
            ) as Executor:
                while Ready or Futures:
                    while Ready and len(Futures) < Limits.MaximumInFlightTasks:
                        Index = Ready.popleft()
                        Item = Items[Index]
                        if monotonic() >= Item.Request.DeadlineAt:
                            Executions[Index] = _UnresolvedExecution(
                                Item.Request,
                                RuntimeTerminalReason.DeadlineExhausted,
                            )
                            continue
                        Future = Executor.submit(
                            _ExecuteSpawnedWorkItem,
                            Operation,
                            Item,
                            Limits.MaximumResultBytes,
                        )
                        Futures[Future] = Index
                        Submitted += 1
                        PeakInFlight = max(PeakInFlight, len(Futures))
                    if not Futures:
                        continue
                    Now = monotonic()
                    UnexpiredDeadlines = tuple(
                        Items[Index].Request.DeadlineAt
                        for Index in Futures.values()
                        if Items[Index].Request.DeadlineAt > Now
                    )
                    Remaining = (
                        None
                        if not UnexpiredDeadlines
                        else max(0.0, min(UnexpiredDeadlines) - Now)
                    )
                    WaitingTaskIdentities = tuple(
                        Items[Index].Request.TaskIdentity
                        for Index in sorted(Futures.values())
                    )
                    if WaitObserver is not None:
                        for TaskIdentity in WaitingTaskIdentities:
                            WaitObserver(TaskIdentity, "begin")
                    try:
                        Done, _Pending = wait(
                            tuple(Futures),
                            timeout=Remaining,
                            return_when=FIRST_COMPLETED,
                        )
                    finally:
                        if WaitObserver is not None:
                            for TaskIdentity in WaitingTaskIdentities:
                                WaitObserver(TaskIdentity, "end")
                    if not Done:
                        Now = monotonic()
                        for Future, Index in tuple(Futures.items()):
                            if (
                                Items[Index].Request.DeadlineAt <= Now
                                and Future.cancel()
                            ):
                                Executions[Index] = _UnresolvedExecution(
                                    Items[Index].Request,
                                    RuntimeTerminalReason.DeadlineExhausted,
                                )
                                del Futures[Future]
                        continue
                    for Future in sorted(Done, key=lambda Value: Futures[Value]):
                        Index = Futures.pop(Future)
                        Request = Items[Index].Request
                        try:
                            if monotonic() >= Request.DeadlineAt:
                                Executions[Index] = _UnresolvedExecution(
                                    Request,
                                    RuntimeTerminalReason.DeadlineExhausted,
                                )
                                CompletionOrder.append(Request.TaskIdentity)
                                continue
                            ChildReturn = Future.result()
                            if not isinstance(ChildReturn, _SpawnedChildReturn):
                                raise TypeError(
                                    "spawned worker returned an invalid envelope"
                                )
                            Executions[Index] = ChildReturn.Execution
                            MaximumObservedResultBytes = max(
                                MaximumObservedResultBytes,
                                ChildReturn.ResultBytes,
                            )
                        except Exception as Error:
                            Executions[Index] = _UnresolvedExecution(
                                Request,
                                RuntimeTerminalReason.WorkerFailure,
                                RuntimeLifecycle.Failed,
                                Diagnostics=((
                                    "ExceptionType",
                                    type(Error).__name__,
                                ),),
                            )
                        CompletionOrder.append(Request.TaskIdentity)
        except Exception as Error:
            for Index in AdmittedIndexes:
                if Executions[Index] is None:
                    Executions[Index] = _UnresolvedExecution(
                        Items[Index].Request,
                        RuntimeTerminalReason.WorkerFailure,
                        RuntimeLifecycle.Failed,
                        Diagnostics=(("ExceptionType", type(Error).__name__),),
                    )

    if any(Execution is None for Execution in Executions):
        raise RuntimeError("spawned work batch did not settle every request")
    SettledExecutions = tuple(
        (Item.Request.TaskIdentity, Executions[Index])
        for Index, Item in enumerate(Items)
    )
    return RuntimeSpawnedWorkBatch(
        Executions=SettledExecutions,
        CompletionOrder=tuple(CompletionOrder),
        AdmittedTaskCount=len(AdmittedIndexes),
        AdmissionRejectedTaskCount=len(RejectedIndexes),
        SubmittedTaskCount=Submitted,
        PeakQueuedTaskCount=PeakQueued,
        PeakInFlightTaskCount=PeakInFlight,
        MaximumObservedPayloadBytes=max(PayloadBytesByIndex.values(), default=0),
        MaximumObservedResultBytes=MaximumObservedResultBytes,
    )

"""Bounded admission and backpressure for one-shot spawned work batches."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import pickle
from time import monotonic, sleep
from typing import Callable, Generic, TypeVar

from ..Contracts.Runtime import (
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeLifecycle,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
    RuntimeWorkAuthority,
    RuntimeWorkExecution,
    RuntimeWorkProduct,
    RuntimeWorkRequest,
    RuntimeWorkResult,
)
from .OneShotProcess import (
    BuildRuntimeOneShotProcessLimits,
    RuntimeOneShotProcessHandle,
    RuntimeOneShotProcessReceipt,
    StartRuntimeOneShotProcess,
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
    """One immutable request, authority grant, and exact child payload."""

    Request: RuntimeWorkRequest
    Authority: RuntimeWorkAuthority
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
class RuntimeSpawnedWorkOwnedContinuation:
    """One exact still-owned handle and its latest public receipt."""

    TaskIdentity: str
    Handle: RuntimeOneShotProcessHandle
    Receipt: RuntimeOneShotProcessReceipt


class RuntimeSpawnedWorkCleanupIncomplete(RuntimeError):
    """Surface every continuation that could not be released by its cutoff."""

    def __init__(
        self,
        OwnedContinuations: tuple[RuntimeSpawnedWorkOwnedContinuation, ...],
    ) -> None:
        if not OwnedContinuations:
            raise ValueError("cleanup-incomplete requires owned continuations")
        self.OwnedContinuations = OwnedContinuations
        super().__init__(
            "spawned work cleanup cutoff expired before release: "
            + ",".join(
                Continuation.TaskIdentity
                for Continuation in OwnedContinuations
            )
        )


@dataclass(frozen=True)
class RuntimeSpawnedWorkProduct(
    RuntimeWorkProduct[ResultPayload],
    Generic[ResultPayload],
):
    """Spawn-safe product with authoritative performed-work accounting."""

    WorkUnits: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            isinstance(self.WorkUnits, bool)
            or not isinstance(self.WorkUnits, int)
            or self.WorkUnits < 0
        ):
            raise ValueError("WorkUnits must be a non-negative integer")


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
    WorkUnits: int = 0,
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
            WorkUnits=WorkUnits,
            Diagnostics=Diagnostics,
        ),
        Value=None,
    )


def _ValidateChildExecution(
    Request: RuntimeWorkRequest,
    Execution: object,
    RemainingBatchWork: int,
) -> tuple[RuntimeWorkExecution | None, str | None]:
    """Bind decoded child output to its exact admitted request and grant."""
    if type(Execution) is not RuntimeWorkExecution:
        return None, "ExecutionType"
    Result = Execution.Result
    if type(Result) is not RuntimeWorkResult:
        return None, "ResultType"
    if Result.TaskIdentity != Request.TaskIdentity:
        return None, "TaskIdentity"
    if Result.Operation != Request.Operation:
        return None, "Operation"
    if Result.Scope != Request.Scope:
        return None, "Scope"
    if Result.Freshness is not Request.Freshness:
        return None, "Freshness"
    if Result.CommitEligibility is not RuntimeCommitEligibility.Ineligible:
        return None, "CommitEligibility"
    if (
        isinstance(Result.WorkUnits, bool)
        or not isinstance(Result.WorkUnits, int)
        or Result.WorkUnits < 0
        or Result.WorkUnits > Request.WorkCap
    ):
        return None, "WorkUnits"
    if Result.WorkUnits > RemainingBatchWork:
        return None, "AggregateWorkUnits"
    if Result.SearchOutcome is RuntimeSearchOutcome.Prepared:
        if Execution.Value is None:
            return None, "PreparedValue"
        if Result.CandidateIdentity is None or Result.ProofIdentity is not None:
            return None, "PreparedClaim"
    elif Result.SearchOutcome is RuntimeSearchOutcome.Infeasible:
        if Execution.Value is not None:
            return None, "InfeasibleValue"
        if Result.ProofIdentity is None or Result.CandidateIdentity is not None:
            return None, "InfeasibleProof"
    elif Result.SearchOutcome is RuntimeSearchOutcome.Unresolved:
        if Execution.Value is not None:
            return None, "UnresolvedValue"
        if Result.ProofIdentity is not None:
            return None, "UnresolvedProof"
    else:
        return None, "SearchOutcome"
    return Execution, None


def _ExecuteSpawnedWorkItem(
    Operation: Callable[
        [Payload, RuntimeWorkRequest],
        RuntimeWorkProduct[ResultPayload],
    ],
    Item: RuntimeSpawnedWorkItem[Payload],
    MaximumResultBytes: int,
    CancellationCheck: Callable[[], bool] | None = None,
) -> _SpawnedChildReturn[ResultPayload]:
    """Run one already-admitted item and bound what crosses to the parent."""
    Request = Item.Request
    if monotonic() >= Request.DeadlineAt:
        return _SpawnedChildReturn(
            _UnresolvedExecution(Request, RuntimeTerminalReason.DeadlineExhausted),
            0,
        )
    if Request.Cancellation.Requested or (
        CancellationCheck is not None and CancellationCheck()
    ):
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
        WorkUnits = getattr(Product, "WorkUnits", 0)
        if (
            isinstance(WorkUnits, bool)
            or not isinstance(WorkUnits, int)
            or WorkUnits < 0
            or WorkUnits > Request.WorkCap
        ):
            raise ValueError("spawned work units exceed the request allowance")
        if monotonic() >= Request.DeadlineAt:
            return _SpawnedChildReturn(
                _UnresolvedExecution(
                    Request,
                    RuntimeTerminalReason.DeadlineExhausted,
                    WorkUnits=WorkUnits,
                ),
                0,
            )
        if CancellationCheck is not None and CancellationCheck():
            return _SpawnedChildReturn(
                _UnresolvedExecution(
                    Request,
                    RuntimeTerminalReason.Cancelled,
                    RuntimeLifecycle.TerminatedGracefully,
                    WorkUnits=WorkUnits,
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
                WorkUnits=WorkUnits,
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
                    WorkUnits=WorkUnits,
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


def _ExecuteSpawnedWorkEnvelope(
    EncodedPayload: bytes,
    CancellationCheck: Callable[[], bool],
) -> bytes:
    """Decode one bounded batch item and return its exact encoded execution."""
    Operation, Item, MaximumResultBytes = pickle.loads(EncodedPayload)
    ChildReturn = _ExecuteSpawnedWorkItem(
        Operation,
        Item,
        MaximumResultBytes,
        CancellationCheck,
    )
    return pickle.dumps(
        ChildReturn.Execution,
        protocol=pickle.HIGHEST_PROTOCOL,
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
    """Admit finite one-shot work and release capacity only after child reap."""
    TaskIdentities = tuple(Item.Request.TaskIdentity for Item in Items)
    if len(set(TaskIdentities)) != len(TaskIdentities):
        raise ValueError("spawned work task identities must be unique")
    for Item in Items:
        if type(Item.Authority) is not RuntimeWorkAuthority:
            raise TypeError("spawned work authority must be exact")
        if Item.Authority.WorkDeadlineAt != Item.Request.DeadlineAt:
            raise ValueError("spawned work authority must carry the request deadline")
        if Item.Authority.CleanupCutoffAt <= Item.Request.DeadlineAt:
            raise ValueError(
                "spawned work requires a positive caller-owned cleanup allowance"
            )

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
        if Request.Cancellation.Requested:
            Executions[Index] = _UnresolvedExecution(
                Request,
                RuntimeTerminalReason.Cancelled,
                RuntimeLifecycle.TerminatedGracefully,
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
    Handles: dict[RuntimeOneShotProcessHandle, int] = {}
    ReceiptsByHandle: dict[
        RuntimeOneShotProcessHandle,
        RuntimeOneShotProcessReceipt,
    ] = {}
    WaitBegun: set[RuntimeOneShotProcessHandle] = set()
    BatchWorkGrant = sum(
        Items[Index].Request.WorkCap for Index in AdmittedIndexes
    )
    AcceptedWorkUnits = 0

    def RememberReceipt(
        Handle: RuntimeOneShotProcessHandle,
        Receipt: RuntimeOneShotProcessReceipt,
    ) -> RuntimeOneShotProcessReceipt:
        ReceiptsByHandle[Handle] = Receipt
        return Receipt

    def OwnedContinuations(
    ) -> tuple[RuntimeSpawnedWorkOwnedContinuation, ...]:
        Continuations = []
        for Handle, Index in sorted(
            Handles.items(),
            key=lambda Entry: Entry[1],
        ):
            Receipt = ReceiptsByHandle.get(Handle)
            if Receipt is None:
                Receipt = getattr(Handle, "LastReceipt", None)
                if Receipt is not None:
                    RememberReceipt(Handle, Receipt)
            if Receipt is None:
                raise RuntimeError(
                    "spawned work lost the receipt for an owned continuation"
                )
            if (
                not getattr(Receipt, "OutstandingOwnership", False)
                and getattr(Receipt, "ResourcesClosed", False)
            ):
                continue
            Continuations.append(RuntimeSpawnedWorkOwnedContinuation(
                TaskIdentity=Items[Index].Request.TaskIdentity,
                Handle=Handle,
                Receipt=Receipt,
            ))
        return tuple(Continuations)

    def EarliestCleanupCutoff() -> float | None:
        OwnedIndexes = tuple(
            Index
            for Handle, Index in Handles.items()
            if (
                getattr(
                    ReceiptsByHandle.get(
                        Handle,
                        getattr(Handle, "LastReceipt", None),
                    ),
                    "OutstandingOwnership",
                    False,
                )
                or not getattr(
                    ReceiptsByHandle.get(
                        Handle,
                        getattr(Handle, "LastReceipt", None),
                    ),
                    "ResourcesClosed",
                    False,
                )
            )
        )
        if not OwnedIndexes:
            return None
        return min(
            Items[Index].Authority.CleanupCutoffAt
            for Index in OwnedIndexes
        )

    def RequireCleanupAuthority() -> None:
        Cutoff = EarliestCleanupCutoff()
        if Cutoff is not None and monotonic() >= Cutoff:
            CleanupError = RuntimeSpawnedWorkCleanupIncomplete(
                OwnedContinuations()
            )
            ParentControl = next((
                getattr(Continuation.Receipt, "ParentControl", None)
                for Continuation in CleanupError.OwnedContinuations
                if getattr(Continuation.Receipt, "ParentControl", None)
                is not None
            ), None)
            if ParentControl is not None:
                raise CleanupError from ParentControl
            raise CleanupError

    def CallHandleAction(
        Handle: RuntimeOneShotProcessHandle,
        Action: str,
        *,
        PropagateParentControl: bool = True,
    ):
        RequireCleanupAuthority()
        PreviousReceipt = ReceiptsByHandle.get(Handle)
        if PreviousReceipt is None:
            PreviousReceipt = getattr(Handle, "LastReceipt", None)
            if PreviousReceipt is None:
                raise RuntimeError("owned handle has no public receipt")
            RememberReceipt(Handle, PreviousReceipt)
        try:
            Receipt = getattr(Handle, Action)()
        except BaseException as Error:
            Cutoff = EarliestCleanupCutoff()
            if Cutoff is not None and monotonic() >= Cutoff:
                LatestReceipt = getattr(Handle, "LastReceipt", None)
                LatestParentControl = getattr(
                    LatestReceipt,
                    "ParentControl",
                    None,
                )
                if LatestParentControl is not None:
                    RememberReceipt(Handle, LatestReceipt)
                ParentControl = getattr(
                    PreviousReceipt,
                    "ParentControl",
                    None,
                ) or LatestParentControl
                CleanupError = RuntimeSpawnedWorkCleanupIncomplete(
                    OwnedContinuations()
                )
                if ParentControl is not None:
                    raise CleanupError from ParentControl
                raise RuntimeSpawnedWorkCleanupIncomplete(
                    OwnedContinuations()
                ) from Error
            LatestReceipt = getattr(Handle, "LastReceipt", None)
            if LatestReceipt is not None:
                RememberReceipt(Handle, LatestReceipt)
                ParentControl = getattr(
                    LatestReceipt,
                    "ParentControl",
                    None,
                )
                if PropagateParentControl and ParentControl is not None:
                    raise ParentControl
            raise
        Cutoff = EarliestCleanupCutoff()
        if Cutoff is not None and monotonic() >= Cutoff:
            ReturnedParentControl = getattr(
                Receipt,
                "ParentControl",
                None,
            )
            if ReturnedParentControl is not None:
                RememberReceipt(Handle, Receipt)
            ParentControl = (
                ReturnedParentControl
                or getattr(PreviousReceipt, "ParentControl", None)
            )
            CleanupError = RuntimeSpawnedWorkCleanupIncomplete(
                OwnedContinuations()
            )
            if ParentControl is not None:
                raise CleanupError from ParentControl
            raise CleanupError
        if Receipt is not None:
            Receipt = RememberReceipt(Handle, Receipt)
            ParentControl = getattr(Receipt, "ParentControl", None)
            if PropagateParentControl and ParentControl is not None:
                raise ParentControl
            return Receipt
        Receipt = getattr(Handle, "LastReceipt", None)
        if Receipt is not None:
            Receipt = RememberReceipt(Handle, Receipt)
            ParentControl = getattr(Receipt, "ParentControl", None)
            if PropagateParentControl and ParentControl is not None:
                raise ParentControl
            return Receipt
        return PreviousReceipt

    def CallWaitObserver(
        Handle: RuntimeOneShotProcessHandle,
        Index: int,
        Action: str,
    ) -> None:
        if WaitObserver is None:
            return
        RequireCleanupAuthority()
        try:
            WaitObserver(Items[Index].Request.TaskIdentity, Action)
        except BaseException as Error:
            Cutoff = EarliestCleanupCutoff()
            if Cutoff is not None and monotonic() >= Cutoff:
                raise RuntimeSpawnedWorkCleanupIncomplete(
                    OwnedContinuations()
                ) from Error
            raise
        RequireCleanupAuthority()

    def SettleExitedHandle(
        Handle: RuntimeOneShotProcessHandle,
        Index: int,
        *,
        PropagateParentControl: bool = True,
    ) -> bool:
        nonlocal MaximumObservedResultBytes, AcceptedWorkUnits
        Receipt = CallHandleAction(
            Handle,
            "ReapIfExited",
            PropagateParentControl=PropagateParentControl,
        )
        if not Receipt.Reaped:
            return False
        Request = Items[Index].Request
        AcceptedExecutionWorkUnits = 0
        ObservedResultBytes = 0
        try:
            RequireCleanupAuthority()
            if (
                Receipt.WorkDeadlineObserved
                or Receipt.CancellationRequested
                or Receipt.ForceSignalSent
                or monotonic() >= Request.DeadlineAt
            ):
                Execution = _UnresolvedExecution(
                    Request,
                    RuntimeTerminalReason.DeadlineExhausted,
                )
            elif Receipt.PublishedResult is None:
                Execution = _UnresolvedExecution(
                    Request,
                    RuntimeTerminalReason.WorkerFailure,
                    RuntimeLifecycle.Failed,
                    Diagnostics=((
                        "ProcessDiagnostic",
                        Receipt.ResultDiagnostic
                        or Receipt.OperationalFailure
                        or "ResultUnavailable",
                    ),),
                )
            else:
                Execution = pickle.loads(Receipt.PublishedResult)
                RequireCleanupAuthority()
                Execution, ValidationFailure = _ValidateChildExecution(
                    Request,
                    Execution,
                    BatchWorkGrant - AcceptedWorkUnits,
                )
                if ValidationFailure is not None:
                    Execution = _UnresolvedExecution(
                        Request,
                        RuntimeTerminalReason.WorkerFailure,
                        RuntimeLifecycle.Failed,
                        Diagnostics=((
                            "ChildExecutionValidation",
                            ValidationFailure,
                        ),),
                    )
                else:
                    AcceptedExecutionWorkUnits = Execution.Result.WorkUnits
                ResultBytes = len(Receipt.PublishedResult)
                ObservedResultBytes = int(
                    dict(Execution.Result.Diagnostics).get(
                        "ObservedResultBytes",
                        ResultBytes,
                    )
                )
            RequireCleanupAuthority()
        except BaseException:
            raise
        Receipt = CallHandleAction(
            Handle,
            "CloseReleased",
            PropagateParentControl=PropagateParentControl,
        )
        if (
            not Receipt.ResourcesClosed
            or not Receipt.ReleaseAcknowledged
            or Receipt.OutstandingOwnership
        ):
            return False
        AcceptedWorkUnits += AcceptedExecutionWorkUnits
        MaximumObservedResultBytes = max(
            MaximumObservedResultBytes,
            ObservedResultBytes,
        )
        Executions[Index] = Execution
        CompletionOrder.append(Request.TaskIdentity)
        Handles.pop(Handle, None)
        ReceiptsByHandle.pop(Handle, None)
        if Handle in WaitBegun:
            WaitBegun.remove(Handle)
            CallWaitObserver(Handle, Index, "end")
        return True

    def RecoverHandles(
    ) -> tuple[
        tuple[RuntimeSpawnedWorkOwnedContinuation, ...],
        BaseException | None,
    ]:
        ParentControl = None

        def CaptureParentControl(Error: BaseException | None = None) -> None:
            nonlocal ParentControl
            if ParentControl is not None:
                return
            Candidates = []
            if Error is not None:
                if not isinstance(Error, Exception):
                    Candidates.append(Error)
                Cause = getattr(Error, "__cause__", None)
                if Cause is not None and not isinstance(Cause, Exception):
                    Candidates.append(Cause)
            Candidates.extend(
                getattr(Receipt, "ParentControl", None)
                for Receipt in ReceiptsByHandle.values()
            )
            ParentControl = next(
                (
                    Candidate
                    for Candidate in Candidates
                    if Candidate is not None
                    and not isinstance(Candidate, Exception)
                ),
                None,
            )

        def RecoveryOutcome(
            Owned: tuple[RuntimeSpawnedWorkOwnedContinuation, ...] | None = None,
        ) -> tuple[
            tuple[RuntimeSpawnedWorkOwnedContinuation, ...],
            BaseException | None,
        ]:
            CaptureParentControl()
            return (
                OwnedContinuations() if Owned is None else Owned,
                ParentControl,
            )

        CaptureParentControl()
        for Handle in tuple(Handles):
            Receipt = ReceiptsByHandle.get(Handle)
            if Receipt is None:
                continue
            if (
                not getattr(Receipt, "OutstandingOwnership", False)
                and getattr(Receipt, "ResourcesClosed", False)
            ):
                Handles.pop(Handle, None)
                ReceiptsByHandle.pop(Handle, None)
                WaitBegun.discard(Handle)
        try:
            RequireCleanupAuthority()
        except RuntimeSpawnedWorkCleanupIncomplete as Error:
            CaptureParentControl(Error)
            return RecoveryOutcome(Error.OwnedContinuations)
        for Handle in tuple(Handles):
            try:
                Index = Handles[Handle]
                Existing = ReceiptsByHandle[Handle]
                if (
                    getattr(Existing, "ExactUnstarted", False)
                    and getattr(Existing, "UnstartedReason", None)
                    == "AllocationFailure"
                ):
                    CallHandleAction(
                        Handle,
                        "CloseReleased",
                        PropagateParentControl=False,
                    )
                    Handles.pop(Handle, None)
                    ReceiptsByHandle.pop(Handle, None)
                    continue
                Receipt = CallHandleAction(
                    Handle,
                    "RequestCancellation",
                    PropagateParentControl=False,
                )
                if (
                    Items[Index].Authority.ForceTerminationAuthorized
                    and Receipt.OutstandingOwnership
                    and not Receipt.ProcessExitObserved
                ):
                    CallHandleAction(
                        Handle,
                        "ForceTerminate",
                        PropagateParentControl=False,
                    )
            except RuntimeSpawnedWorkCleanupIncomplete as Error:
                CaptureParentControl(Error)
                return RecoveryOutcome(Error.OwnedContinuations)
            except BaseException as Error:
                CaptureParentControl(Error)
                continue
        while Handles:
            try:
                RequireCleanupAuthority()
            except RuntimeSpawnedWorkCleanupIncomplete as Error:
                CaptureParentControl(Error)
                return RecoveryOutcome(Error.OwnedContinuations)
            Progressed = False
            for Handle, Index in tuple(Handles.items()):
                try:
                    Receipt = CallHandleAction(
                        Handle,
                        "Observe",
                        PropagateParentControl=False,
                    )
                    if Receipt.ProcessExitObserved:
                        Progressed = (
                            SettleExitedHandle(
                                Handle,
                                Index,
                                PropagateParentControl=False,
                            )
                            or Progressed
                        )
                except RuntimeSpawnedWorkCleanupIncomplete as Error:
                    CaptureParentControl(Error)
                    return RecoveryOutcome(Error.OwnedContinuations)
                except BaseException as Error:
                    CaptureParentControl(Error)
                    continue
            if not Handles:
                return RecoveryOutcome(())
            Now = monotonic()
            if Progressed:
                continue
            CleanupCutoff = EarliestCleanupCutoff()
            if CleanupCutoff is None or Now >= CleanupCutoff:
                return RecoveryOutcome()
            sleep(max(0.0, min(
                0.005,
                CleanupCutoff - Now,
            )))
        return RecoveryOutcome(())

    if Ready:
        try:
            while Ready or Handles:
                while Ready and len(Handles) < Limits.MaximumInFlightTasks:
                    RequireCleanupAuthority()
                    Index = Ready.popleft()
                    Item = Items[Index]
                    if monotonic() >= Item.Request.DeadlineAt:
                        Executions[Index] = _UnresolvedExecution(
                            Item.Request,
                            RuntimeTerminalReason.DeadlineExhausted,
                        )
                        continue
                    Handle = None
                    try:
                        EncodedPayload = pickle.dumps(
                            (Operation, Item, Limits.MaximumResultBytes),
                            protocol=pickle.HIGHEST_PROTOCOL,
                        )
                        Handle = StartRuntimeOneShotProcess(
                            Item.Request,
                            Item.Authority,
                            EncodedPayload,
                            _ExecuteSpawnedWorkEnvelope,
                            BuildRuntimeOneShotProcessLimits(
                                _ExecuteSpawnedWorkEnvelope,
                                len(EncodedPayload),
                                Limits.MaximumResultBytes,
                            ),
                        )
                        Handles[Handle] = Index
                        LatestReceipt = getattr(Handle, "LastReceipt", None)
                        InitialReceipt = (
                            LatestReceipt
                            if getattr(LatestReceipt, "ParentControl", None)
                            is not None
                            else (
                            getattr(Handle, "AdmissionReceipt", None)
                            or LatestReceipt
                            if getattr(
                                LatestReceipt,
                                "CleanupCutoffBreached",
                                False,
                            )
                            else LatestReceipt
                            )
                        )
                        if InitialReceipt is None:
                            raise RuntimeError(
                                "started one-shot handle has no initial receipt"
                            )
                        Receipt = RememberReceipt(Handle, InitialReceipt)
                        if (
                            getattr(Receipt, "ExactUnstarted", False)
                            and Receipt.ResourcesClosed
                            and not getattr(
                                Receipt,
                                "OutstandingOwnership",
                                False,
                            )
                        ):
                            Handles.pop(Handle, None)
                            ReceiptsByHandle.pop(Handle, None)
                            ParentControl = getattr(
                                Receipt,
                                "ParentControl",
                                None,
                            )
                            if ParentControl is not None:
                                raise ParentControl
                            if Receipt.UnstartedReason == "WorkDeadlineExpired":
                                Executions[Index] = _UnresolvedExecution(
                                    Item.Request,
                                    RuntimeTerminalReason.DeadlineExhausted,
                                )
                            else:
                                Executions[Index] = _UnresolvedExecution(
                                    Item.Request,
                                    RuntimeTerminalReason.WorkerFailure,
                                    RuntimeLifecycle.Failed,
                                    Diagnostics=tuple(
                                        (Name, Value)
                                        for Name, Value in (
                                            (
                                                "OperationalFailure",
                                                Receipt.OperationalFailure,
                                            ),
                                            (
                                                "UnstartedReason",
                                                Receipt.UnstartedReason,
                                            ),
                                        )
                                        if Value is not None
                                    ),
                                )
                            continue
                        RequireCleanupAuthority()
                        Receipt = CallHandleAction(Handle, "Observe")
                        if (
                            getattr(Receipt, "ExactUnstarted", False)
                            and getattr(Receipt, "UnstartedReason", None)
                            == "AllocationFailure"
                            and not Receipt.ResourcesClosed
                        ):
                            Receipt = CallHandleAction(
                                Handle,
                                "CloseReleased",
                            )
                        if Receipt.ResourcesClosed:
                            Handles.pop(Handle, None)
                            ReceiptsByHandle.pop(Handle, None)
                            if Receipt.UnstartedReason == "WorkDeadlineExpired":
                                Executions[Index] = _UnresolvedExecution(
                                    Item.Request,
                                    RuntimeTerminalReason.DeadlineExhausted,
                                )
                            else:
                                Executions[Index] = _UnresolvedExecution(
                                    Item.Request,
                                    RuntimeTerminalReason.WorkerFailure,
                                    RuntimeLifecycle.Failed,
                                    Diagnostics=tuple(
                                        (Name, Value)
                                        for Name, Value in (
                                            (
                                                "OperationalFailure",
                                                Receipt.OperationalFailure,
                                            ),
                                            (
                                                "UnstartedReason",
                                                Receipt.UnstartedReason,
                                            ),
                                        )
                                        if Value is not None
                                    ),
                                )
                            continue
                        Submitted += 1
                        PeakInFlight = max(PeakInFlight, len(Handles))
                        ParentControl = getattr(Receipt, "ParentControl", None)
                        if ParentControl is not None:
                            raise ParentControl
                    except Exception as Error:
                        if Handle is not None and Handle in Handles:
                            raise
                        Executions[Index] = _UnresolvedExecution(
                            Item.Request,
                            RuntimeTerminalReason.WorkerFailure,
                            RuntimeLifecycle.Failed,
                            Diagnostics=(("ExceptionType", type(Error).__name__),),
                        )
                if not Handles:
                    continue
                for Handle, Index in tuple(Handles.items()):
                    if Handle not in WaitBegun:
                        CallWaitObserver(Handle, Index, "begin")
                        WaitBegun.add(Handle)
                Progressed = False
                for Handle, Index in tuple(Handles.items()):
                    Receipt = CallHandleAction(Handle, "Observe")
                    ParentControl = getattr(Receipt, "ParentControl", None)
                    if ParentControl is not None:
                        raise ParentControl
                    if Receipt.ProcessExitObserved:
                        Progressed = SettleExitedHandle(Handle, Index) or Progressed
                        continue
                    if monotonic() >= Items[Index].Request.DeadlineAt:
                        Receipt = CallHandleAction(
                            Handle,
                            "RequestCancellation",
                        )
                        if (
                            Items[Index].Authority.ForceTerminationAuthorized
                            and Receipt.OutstandingOwnership
                            and not Receipt.ProcessExitObserved
                        ):
                            Receipt = CallHandleAction(
                                Handle,
                                "ForceTerminate",
                            )
                        if Receipt.ProcessExitObserved:
                            Progressed = SettleExitedHandle(Handle, Index) or Progressed
                if Progressed or not Handles:
                    continue
                Now = monotonic()
                CleanupCutoff = EarliestCleanupCutoff()
                if CleanupCutoff is None:
                    continue
                if Now >= CleanupCutoff:
                    raise RuntimeSpawnedWorkCleanupIncomplete(
                        OwnedContinuations()
                    )
                FutureWorkDeadlines = tuple(
                    Items[Index].Request.DeadlineAt
                    for Index in Handles.values()
                    if Items[Index].Request.DeadlineAt > Now
                )
                SleepSeconds = min(
                    0.005,
                    CleanupCutoff - Now,
                )
                if FutureWorkDeadlines:
                    SleepSeconds = min(
                        SleepSeconds,
                        min(FutureWorkDeadlines) - Now,
                    )
                sleep(max(0.0, SleepSeconds))
        except RuntimeSpawnedWorkCleanupIncomplete:
            raise
        except Exception as Error:
            Owned, CleanupParentControl = RecoverHandles()
            if Owned:
                raise RuntimeSpawnedWorkCleanupIncomplete(Owned) from (
                    CleanupParentControl or Error
                )
            if CleanupParentControl is not None:
                raise CleanupParentControl
            for Index in AdmittedIndexes:
                if Executions[Index] is None:
                    Executions[Index] = _UnresolvedExecution(
                        Items[Index].Request,
                        RuntimeTerminalReason.WorkerFailure,
                        RuntimeLifecycle.Failed,
                        Diagnostics=(("ExceptionType", type(Error).__name__),),
                    )
        except BaseException as Error:
            Owned, CleanupParentControl = RecoverHandles()
            if Owned:
                raise RuntimeSpawnedWorkCleanupIncomplete(Owned) from Error
            raise

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

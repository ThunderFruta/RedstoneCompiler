"""Independent admission tests for bounded one-shot spawned work."""

from argparse import ArgumentParser
from collections import Counter
from concurrent.futures import ProcessPoolExecutor as StandardProcessPoolExecutor
from pathlib import Path
import pickle
from time import monotonic, sleep
from types import MethodType

import pytest

from PhysicalDesign.Contracts.Runtime import (
    RuntimeCancellationSnapshot,
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeFreshness,
    RuntimeLifecycle,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
    RuntimeWorkExecution,
    RuntimeWorkProduct,
    RuntimeWorkRequest,
    RuntimeWorkResult,
    RuntimeWorkScope,
)
import PhysicalDesign.Runtime.SpawnedWork as SpawnedWork
from PhysicalDesign.Runtime.SpawnedWork import (
    ExecuteBoundedSpawnedWorkBatch,
    RuntimeSpawnedWorkItem,
    RuntimeSpawnedWorkLimits,
)


def _Request(Index: int, *, DeadlineAt: float | None = None) -> RuntimeWorkRequest:
    return RuntimeWorkRequest(
        TaskIdentity=f"spawned-{Index}",
        Operation="bounded-test-operation",
        Scope=RuntimeWorkScope(
            DomainIdentity=f"domain-{Index}",
            DependencyIdentities=("fixture-v1",),
        ),
        Lifecycle=RuntimeLifecycle.Queued,
        Freshness=RuntimeFreshness.Current,
        DeadlineAt=(
            monotonic() + 10.0 if DeadlineAt is None else DeadlineAt
        ),
        WorkCap=1,
        Cancellation=RuntimeCancellationSnapshot(
            Requested=False,
            Identity=f"spawned-{Index}:cancellation",
        ),
    )


def _EchoProduct(Payload, Request):
    Delay, Value = Payload
    if Delay:
        sleep(Delay)
    return RuntimeWorkProduct(
        Value=Value,
        SearchOutcome=RuntimeSearchOutcome.Prepared,
        ClaimStrength=RuntimeClaimStrength.Complete,
        TerminalReason=RuntimeTerminalReason.Prepared,
        CandidateIdentity=f"candidate:{Request.Scope.DomainIdentity}:{Value}",
    )


def _FailingProduct(_Payload, _Request):
    raise RuntimeError("controlled spawned failure")


def _ObservedProduct(Payload, Request):
    ObservationDirectory, Delay, Value = Payload
    ObservationRoot = Path(ObservationDirectory)
    with (ObservationRoot / f"{Request.TaskIdentity}.started").open("x") as File:
        File.write(str(monotonic()))
    sleep(Delay)
    with (ObservationRoot / f"{Request.TaskIdentity}.completed").open("x") as File:
        File.write(str(monotonic()))
    return _EchoProduct((0.0, Value), Request)


def _ExpectedEchoExecution(Request, Value):
    return RuntimeWorkExecution(
        Result=RuntimeWorkResult(
            TaskIdentity=Request.TaskIdentity,
            Operation=Request.Operation,
            Scope=Request.Scope,
            SearchOutcome=RuntimeSearchOutcome.Prepared,
            Lifecycle=RuntimeLifecycle.Completed,
            Freshness=Request.Freshness,
            ClaimStrength=RuntimeClaimStrength.Complete,
            CommitEligibility=RuntimeCommitEligibility.Ineligible,
            TerminalReason=RuntimeTerminalReason.Prepared,
            CandidateIdentity=(
                f"candidate:{Request.Scope.DomainIdentity}:{Value}"
            ),
            WorkUnits=0,
        ),
        Value=Value,
    )


def _Items(Count: int) -> tuple[RuntimeSpawnedWorkItem, ...]:
    return tuple(
        RuntimeSpawnedWorkItem(
            Request=_Request(Index),
            Payload=(0.0, f"value-{Index}"),
        )
        for Index in range(Count)
    )


def _Limits(
    *,
    Queued: int = 2,
    InFlight: int = 1,
    PayloadBytes: int = 1_000_000,
    ResultBytes: int = 1_000_000,
) -> RuntimeSpawnedWorkLimits:
    return RuntimeSpawnedWorkLimits(
        MaximumQueuedTasks=Queued,
        MaximumInFlightTasks=InFlight,
        MaximumPayloadBytes=PayloadBytes,
        MaximumResultBytes=ResultBytes,
    )


def test_exact_queue_and_in_flight_limits_apply_backpressure():
    Batch = ExecuteBoundedSpawnedWorkBatch(
        _Items(3),
        _EchoProduct,
        _Limits(Queued=2, InFlight=1),
    )

    assert Batch.AdmittedTaskCount == 3
    assert Batch.AdmissionRejectedTaskCount == 0
    assert Batch.SubmittedTaskCount == 3
    assert Batch.PeakQueuedTaskCount == 2
    assert Batch.PeakInFlightTaskCount == 1
    assert all(
        Execution.Result.SearchOutcome is RuntimeSearchOutcome.Prepared
        for _Task, Execution in Batch.Executions
    )


def test_capacity_plus_one_is_rejected_without_becoming_a_proof():
    Batch = ExecuteBoundedSpawnedWorkBatch(
        _Items(4),
        _EchoProduct,
        _Limits(Queued=2, InFlight=1),
    )
    Rejected = Batch.Executions[-1][1]

    assert Batch.AdmittedTaskCount == 3
    assert Batch.AdmissionRejectedTaskCount == 1
    assert Batch.SubmittedTaskCount == 3
    assert Rejected.Value is None
    assert Rejected.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Rejected.Result.TerminalReason is RuntimeTerminalReason.AdmissionRejected
    assert Rejected.Result.CommitEligibility is RuntimeCommitEligibility.Ineligible
    assert Rejected.Result.ProofIdentity is None


def test_payload_and_result_bounds_fail_closed_without_publication():
    BaseItem = RuntimeSpawnedWorkItem(
        Request=_Request(0),
        Payload=(0.0, b"x" * 4096),
    )
    PayloadRejected = ExecuteBoundedSpawnedWorkBatch(
        (BaseItem,),
        _EchoProduct,
        _Limits(PayloadBytes=32),
    ).Executions[0][1]
    ResultRejected = ExecuteBoundedSpawnedWorkBatch(
        (BaseItem,),
        _EchoProduct,
        _Limits(PayloadBytes=1_000_000, ResultBytes=1024),
    ).Executions[0][1]

    assert PayloadRejected.Value is None
    assert PayloadRejected.Result.TerminalReason is (
        RuntimeTerminalReason.PayloadLimitExceeded
    )
    assert PayloadRejected.Result.ProofIdentity is None
    assert ResultRejected.Value is None
    assert ResultRejected.Result.TerminalReason is (
        RuntimeTerminalReason.ResultLimitExceeded
    )
    assert ResultRejected.Result.ProofIdentity is None


def test_exact_serialized_payload_and_result_limits_accept_then_reject_one_byte_less():
    PayloadItem = RuntimeSpawnedWorkItem(
        Request=_Request(0),
        Payload=(0.0, b"payload" * 128),
    )
    ExactPayloadBytes = len(
        pickle.dumps(PayloadItem, protocol=pickle.HIGHEST_PROTOCOL)
    )
    PayloadAccepted = ExecuteBoundedSpawnedWorkBatch(
        (PayloadItem,),
        _EchoProduct,
        _Limits(PayloadBytes=ExactPayloadBytes),
    ).Executions[0][1]
    PayloadRejected = ExecuteBoundedSpawnedWorkBatch(
        (
            RuntimeSpawnedWorkItem(
                Request=_Request(1),
                Payload=PayloadItem.Payload,
            ),
        ),
        _EchoProduct,
        _Limits(PayloadBytes=ExactPayloadBytes - 1),
    ).Executions[0][1]

    ResultItem = RuntimeSpawnedWorkItem(
        Request=_Request(2),
        Payload=(0.0, b"result" * 1_024),
    )
    ExactResultBytes = len(
        pickle.dumps(
            _ExpectedEchoExecution(
                ResultItem.Request,
                ResultItem.Payload[1],
            ),
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    )
    ResultAccepted = ExecuteBoundedSpawnedWorkBatch(
        (ResultItem,),
        _EchoProduct,
        _Limits(ResultBytes=ExactResultBytes),
    ).Executions[0][1]
    ResultRejected = ExecuteBoundedSpawnedWorkBatch(
        (ResultItem,),
        _EchoProduct,
        _Limits(ResultBytes=ExactResultBytes - 1),
    ).Executions[0][1]

    assert PayloadAccepted.Result.SearchOutcome is RuntimeSearchOutcome.Prepared
    assert PayloadRejected.Result.TerminalReason is (
        RuntimeTerminalReason.PayloadLimitExceeded
    )
    assert ResultAccepted.Result.SearchOutcome is RuntimeSearchOutcome.Prepared
    assert ResultRejected.Result.TerminalReason is (
        RuntimeTerminalReason.ResultLimitExceeded
    )


def test_child_system_exit_is_typed_worker_failure():
    Item = RuntimeSpawnedWorkItem(
        Request=_Request(0),
        Payload=None,
    )

    Batch = ExecuteBoundedSpawnedWorkBatch(
        (Item,),
        ArgumentParser.exit,
        _Limits(Queued=0, InFlight=1),
    )
    Execution = Batch.Executions[0][1]

    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.Lifecycle is RuntimeLifecycle.Failed
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.WorkerFailure
    assert Execution.Result.CommitEligibility is RuntimeCommitEligibility.Ineligible
    assert Execution.Result.ProofIdentity is None
    assert dict(Execution.Result.Diagnostics)["ExceptionType"] == "SystemExit"


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
def test_parent_base_exception_from_wait_observer_propagates(Interrupt):
    Item = RuntimeSpawnedWorkItem(
        Request=_Request(0),
        Payload=(0.0, "value"),
    )

    def InterruptParent(_TaskIdentity, _Action):
        raise Interrupt("parent interruption")

    with pytest.raises(Interrupt, match="parent interruption"):
        ExecuteBoundedSpawnedWorkBatch(
            (Item,),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
            WaitObserver=InterruptParent,
        )


def test_failed_running_cancellation_keeps_third_request_queued(
    monkeypatch,
    tmp_path,
):
    ParentObservations = []

    class ObservedExecutor:
        def __init__(self, **Arguments):
            self.Executor = StandardProcessPoolExecutor(**Arguments)

        def __enter__(self):
            self.Executor.__enter__()
            return self

        def __exit__(self, *Arguments):
            return self.Executor.__exit__(*Arguments)

        def submit(self, Function, *Arguments):
            Item = Arguments[1]
            TaskIdentity = Item.Request.TaskIdentity
            ParentObservations.append((
                "submitted",
                TaskIdentity,
                monotonic(),
            ))
            Future = self.Executor.submit(Function, *Arguments)
            OriginalCancel = Future.cancel

            def ObserveCancel(_Future):
                Result = OriginalCancel()
                ParentObservations.append((
                    "cancelled",
                    TaskIdentity,
                    monotonic(),
                    Result,
                ))
                return Result

            Future.cancel = MethodType(ObserveCancel, Future)
            return Future

    monkeypatch.setattr(SpawnedWork, "ProcessPoolExecutor", ObservedExecutor)
    StartedAt = monotonic()
    Items = (
        RuntimeSpawnedWorkItem(
            Request=_Request(0, DeadlineAt=StartedAt + 1.5),
            Payload=(str(tmp_path), 2.2, "first"),
        ),
        RuntimeSpawnedWorkItem(
            Request=_Request(1, DeadlineAt=StartedAt + 10.0),
            Payload=(str(tmp_path), 3.0, "second"),
        ),
        RuntimeSpawnedWorkItem(
            Request=_Request(2, DeadlineAt=StartedAt + 10.0),
            Payload=(str(tmp_path), 0.0, "third"),
        ),
    )
    Batch = ExecuteBoundedSpawnedWorkBatch(
        Items,
        _ObservedProduct,
        _Limits(Queued=1, InFlight=2),
    )

    SubmittedAt = {
        Task: At
        for Kind, Task, At, *_Rest in ParentObservations
        if Kind == "submitted"
    }
    StartedAtByTask = {
        Item.Request.TaskIdentity: float(
            (tmp_path / f"{Item.Request.TaskIdentity}.started").read_text()
        )
        for Item in Items
    }
    CompletedAtByTask = {
        Item.Request.TaskIdentity: float(
            (tmp_path / f"{Item.Request.TaskIdentity}.completed").read_text()
        )
        for Item in Items
    }
    FailedCancellation = next(
        Value
        for Value in ParentObservations
        if Value[0] == "cancelled" and Value[1] == "spawned-0"
    )
    SubmittedTasks = tuple(
        Task
        for Kind, Task, _At, *_Rest in ParentObservations
        if Kind == "submitted"
    )
    CancellationAt = FailedCancellation[2]

    assert FailedCancellation[3] is False
    assert Counter(SubmittedTasks) == Counter({
        "spawned-0": 1,
        "spawned-1": 1,
        "spawned-2": 1,
    })
    assert StartedAtByTask["spawned-0"] <= CancellationAt
    assert CancellationAt < CompletedAtByTask["spawned-0"]
    assert StartedAtByTask["spawned-1"] <= CancellationAt
    assert CancellationAt < CompletedAtByTask["spawned-1"]
    assert len(tuple(tmp_path.glob("*.started"))) == 3
    assert len(tuple(tmp_path.glob("*.completed"))) == 3
    assert SubmittedAt["spawned-0"] <= StartedAtByTask["spawned-0"]
    assert SubmittedAt["spawned-1"] <= StartedAtByTask["spawned-1"]
    assert SubmittedAt["spawned-2"] <= StartedAtByTask["spawned-2"]
    assert SubmittedAt["spawned-2"] >= CompletedAtByTask["spawned-0"]
    assert StartedAtByTask["spawned-2"] >= CompletedAtByTask["spawned-0"]
    assert Batch.Executions[0][1].Result.TerminalReason is (
        RuntimeTerminalReason.DeadlineExhausted
    )
    assert Batch.Executions[1][1].Result.SearchOutcome is (
        RuntimeSearchOutcome.Prepared
    )
    assert Batch.Executions[2][1].Result.SearchOutcome is (
        RuntimeSearchOutcome.Prepared
    )


def test_earlier_deadline_does_not_discard_a_later_running_task():
    StartedAt = monotonic()
    Earlier = RuntimeSpawnedWorkItem(
        Request=_Request(0, DeadlineAt=StartedAt + 0.50),
        Payload=(0.80, "earlier"),
    )
    Later = RuntimeSpawnedWorkItem(
        Request=_Request(1, DeadlineAt=StartedAt + 3.0),
        Payload=(0.80, "later"),
    )
    Together = ExecuteBoundedSpawnedWorkBatch(
        (Earlier, Later),
        _EchoProduct,
        _Limits(Queued=0, InFlight=2),
    )
    LaterAlone = ExecuteBoundedSpawnedWorkBatch(
        (
            RuntimeSpawnedWorkItem(
                Request=_Request(2),
                Payload=(0.0, "later"),
            ),
        ),
        _EchoProduct,
        _Limits(Queued=0, InFlight=1),
    )

    EarlierResult = Together.Executions[0][1]
    LaterResult = Together.Executions[1][1]
    assert Together.SubmittedTaskCount == 2
    assert Together.PeakInFlightTaskCount == 2
    assert EarlierResult.Result.TerminalReason is (
        RuntimeTerminalReason.DeadlineExhausted
    )
    assert LaterResult.Result.SearchOutcome is RuntimeSearchOutcome.Prepared
    assert LaterResult.Value == LaterAlone.Executions[0][1].Value
    assert LaterAlone.Executions[0][1].Result.SearchOutcome is (
        RuntimeSearchOutcome.Prepared
    )


def test_deadline_before_start_never_submits_the_operation():
    Item = RuntimeSpawnedWorkItem(
        Request=_Request(0, DeadlineAt=monotonic() - 1.0),
        Payload=(0.0, "late"),
    )

    Batch = ExecuteBoundedSpawnedWorkBatch(
        (Item,),
        _EchoProduct,
        _Limits(),
    )
    Execution = Batch.Executions[0][1]

    assert Batch.AdmittedTaskCount == 0
    assert Batch.SubmittedTaskCount == 0
    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.DeadlineExhausted
    assert Execution.Result.ProofIdentity is None


def test_spawned_worker_failure_is_unresolved_and_releases_capacity():
    Batch = ExecuteBoundedSpawnedWorkBatch(
        _Items(2),
        _FailingProduct,
        _Limits(Queued=1, InFlight=1),
    )

    assert Batch.SubmittedTaskCount == 2
    assert Batch.PeakInFlightTaskCount == 1
    assert all(
        Execution.Result.Lifecycle is RuntimeLifecycle.Failed
        and Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
        and Execution.Result.TerminalReason is RuntimeTerminalReason.WorkerFailure
        and Execution.Result.ProofIdentity is None
        for _Task, Execution in Batch.Executions
    )


def test_worker_count_and_completion_order_do_not_change_results():
    Items = tuple(
        RuntimeSpawnedWorkItem(
            Request=_Request(Index),
            Payload=(Delay, f"value-{Index}"),
        )
        for Index, Delay in enumerate((0.30, 0.15, 0.0))
    )
    Serial = ExecuteBoundedSpawnedWorkBatch(
        Items,
        _EchoProduct,
        _Limits(Queued=2, InFlight=1),
    )
    Parallel = ExecuteBoundedSpawnedWorkBatch(
        Items,
        _EchoProduct,
        _Limits(Queued=0, InFlight=3),
    )

    def Observable(Batch):
        return tuple(
            (
                Task,
                Execution.Value,
                Execution.Result.SearchOutcome,
                Execution.Result.CandidateIdentity,
            )
            for Task, Execution in Batch.Executions
        )

    assert Observable(Serial) == Observable(Parallel)
    assert Serial.CompletionOrder != Parallel.CompletionOrder
    assert Serial.PeakInFlightTaskCount == 1
    assert Parallel.PeakInFlightTaskCount == 3

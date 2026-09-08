"""Independent admission tests for bounded one-shot spawned work."""

from argparse import ArgumentParser
from dataclasses import replace
from pathlib import Path
import multiprocessing
import pickle
import socket
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

import PhysicalDesign.Runtime.SpawnedWork as SpawnedWork
import PhysicalDesign.Runtime.OneShotProcess as OneShotProcess
from PhysicalDesign.Contracts.Runtime import (
    RuntimeCancellationSnapshot,
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeFreshness,
    RuntimeLifecycle,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
    RuntimeWorkAuthority,
    RuntimeWorkExecution,
    RuntimeWorkProduct,
    RuntimeWorkRequest,
    RuntimeWorkResult,
    RuntimeWorkScope,
)
from PhysicalDesign.Runtime.SpawnedWork import (
    ExecuteBoundedSpawnedWorkBatch,
    RuntimeSpawnedWorkItem,
    RuntimeSpawnedWorkLimits,
)
from PhysicalDesign.Runtime.OneShotProcess import StartRuntimeOneShotProcess
from Tests.PhysicalDesign.Runtime.test_one_shot_process import (
    _Authority as _OneShotAuthority,
    _EchoBytes as _OneShotEchoBytes,
    _Limits as _OneShotLimits,
    _OpenSocketInodes,
    _PublishEntryThenIgnoreCancellationUntilFixtureExpiry,
    _RecoverAndClose,
    _Request as _OneShotRequest,
    _SharedMemoryResources,
    _StartOwned as _StartOneShotOwned,
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


def _Item(Request, Payload, *, CleanupSeconds: float = 1.0):
    return RuntimeSpawnedWorkItem(
        Request=Request,
        Authority=RuntimeWorkAuthority(
            WorkDeadlineAt=Request.DeadlineAt,
            CleanupCutoffAt=Request.DeadlineAt + CleanupSeconds,
            ForceTerminationAuthorized=True,
        ),
        Payload=Payload,
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
        _Item(_Request(Index), (0.0, f"value-{Index}"))
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


class _ControlledClock:
    def __init__(self, StartedAt: float) -> None:
        self.Now = StartedAt
        self.Sleeps = []

    def Read(self) -> float:
        return self.Now

    def Sleep(self, Seconds: float) -> None:
        assert Seconds >= 0.0
        self.Sleeps.append(Seconds)
        Previous = self.Now
        self.Now += Seconds
        if self.Now <= Previous:
            self.Now = Previous + 1e-9


class _FailCloseOnceWithExactErrorSocket(socket.socket):
    def __init__(self, *, fileno: int, Error: BaseException) -> None:
        super().__init__(fileno=fileno)
        self._Error = Error
        self._CloseFailed = False

    def close(self) -> None:
        if not self._CloseFailed:
            self._CloseFailed = True
            raise self._Error
        super().close()

    def __reduce_ex__(self, _Protocol):
        return multiprocessing.reduction._reduce_socket(self)


def _InjectExactChildSocketCloseFailure(monkeypatch, Error):
    OriginalSocketPair = OneShotProcess.socket.socketpair
    PairCount = 0

    def FaultingSocketPair(*Arguments, **KeywordArguments):
        nonlocal PairCount
        Pair = OriginalSocketPair(*Arguments, **KeywordArguments)
        PairCount += 1
        if PairCount != 1:
            return Pair
        ParentSocket, ChildSocket = Pair
        return ParentSocket, _FailCloseOnceWithExactErrorSocket(
            fileno=ChildSocket.detach(),
            Error=Error,
        )

    monkeypatch.setattr(
        OneShotProcess.socket,
        "socketpair",
        FaultingSocketPair,
    )


class _UnreleasableControlledHandle:
    def __init__(
        self,
        Clock: _ControlledClock,
        ForceOutcome: str,
        *,
        ParentControl=None,
        ReleaseOnCancellation=False,
    ) -> None:
        self.Clock = Clock
        self.ForceOutcome = ForceOutcome
        self.ReleaseOnCancellation = ReleaseOnCancellation
        self.CloseCalls = 0
        self.Receipt = SimpleNamespace(
            ResourcesClosed=False,
            ProcessExitObserved=False,
            Reaped=False,
            ReleaseAcknowledged=False,
            WorkDeadlineObserved=False,
            CancellationRequested=False,
            ForceTerminationRequested=False,
            ForceTerminationDenied=False,
            ForceSignalSent=False,
            OutstandingOwnership=True,
            CleanupCutoffBreached=False,
            PublishedResult=None,
            ResultDiagnostic=None,
            OperationalFailure=None,
            ParentControl=ParentControl,
        )
        self.LastReceipt = self.Receipt

    def Observe(self):
        return self.Receipt

    def RequestCancellation(self):
        self.Receipt.CancellationRequested = True
        if self.ReleaseOnCancellation:
            self.Receipt.ProcessExitObserved = True
        return self.Receipt

    def ForceTerminate(self):
        self.Receipt.ForceTerminationRequested = True
        if self.ForceOutcome == "denied":
            self.Receipt.ForceTerminationDenied = True
        else:
            self.Receipt.OperationalFailure = "ControlledForceFailure"
        return self.Receipt

    def AdvanceUntil(self, AbsoluteCutoffAt):
        self.Clock.Sleep(max(0.0, AbsoluteCutoffAt - self.Clock.Read()))
        self.Receipt.CleanupCutoffBreached = True
        return self.Receipt

    def ReapIfExited(self):
        if self.Receipt.ProcessExitObserved:
            self.Receipt.Reaped = True
        return self.Receipt

    def CloseReleased(self):
        self.CloseCalls += 1
        if self.Receipt.Reaped:
            self.Receipt.ResourcesClosed = True
            self.Receipt.ReleaseAcknowledged = True
            self.Receipt.OutstandingOwnership = False
            return
        raise AssertionError("an unreleased controlled handle cannot be closed")


def _InstallUnreleasableControlledHandles(
    monkeypatch,
    Clock,
    *,
    ForceOutcome="failure",
    ParentControl=None,
    ReleaseOnCancellation=False,
):
    Handles = []

    def StartControlled(*_Arguments, **_KeywordArguments):
        Handle = _UnreleasableControlledHandle(
            Clock,
            ForceOutcome,
            ParentControl=ParentControl,
            ReleaseOnCancellation=ReleaseOnCancellation,
        )
        Handles.append(Handle)
        return Handle

    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        StartControlled,
    )
    return Handles


class _CutoffActionHandle:
    def __init__(
        self,
        Clock,
        Actions,
        TaskIdentity,
        *,
        CrossOnObserveCall=None,
        CrossTo=None,
    ):
        self.Clock = Clock
        self.Actions = Actions
        self.CrossOnObserveCall = CrossOnObserveCall
        self.CrossTo = CrossTo
        self.ObserveCalls = 0
        self.LastReceipt = SimpleNamespace(
            TaskIdentity=TaskIdentity,
            ResourcesClosed=False,
            ProcessExitObserved=False,
            Reaped=False,
            ReleaseAcknowledged=False,
            WorkDeadlineObserved=False,
            CancellationRequested=False,
            ForceTerminationRequested=False,
            ForceTerminationDenied=False,
            ForceSignalSent=False,
            OutstandingOwnership=True,
            CleanupCutoffBreached=False,
            PublishedResult=None,
            ResultDiagnostic=None,
            OperationalFailure=None,
            ParentControl=None,
        )

    def _Action(self, Name):
        self.Actions.append((self.LastReceipt.TaskIdentity, Name, self.Clock.Read()))

    def Observe(self):
        self._Action("observe")
        self.ObserveCalls += 1
        if self.ObserveCalls == self.CrossOnObserveCall:
            self.Clock.Now = self.CrossTo
        return SimpleNamespace(**vars(self.LastReceipt))

    def RequestCancellation(self):
        self._Action("cancel")
        return self.LastReceipt

    def ForceTerminate(self):
        self._Action("force")
        return self.LastReceipt

    def ReapIfExited(self):
        self._Action("reap")
        return self.LastReceipt

    def CloseReleased(self):
        self._Action("close")


def test_observe_crossing_earliest_cleanup_cutoff_stops_every_later_action(
    monkeypatch,
):
    Clock = _ControlledClock(10.0)
    Actions = []
    Handles = []

    def StartControlled(_Request, *_Arguments, **_KeywordArguments):
        Handle = _CutoffActionHandle(
            Clock,
            Actions,
            _Request.TaskIdentity,
            CrossOnObserveCall=(2 if _Request.TaskIdentity == "spawned-0" else None),
            CrossTo=10.02,
        )
        Handles.append(Handle)
        return Handle

    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(SpawnedWork, "StartRuntimeOneShotProcess", StartControlled)
    Items = (
        _Item(_Request(0, DeadlineAt=10.01), None, CleanupSeconds=0.01),
        _Item(_Request(1, DeadlineAt=10.03), None, CleanupSeconds=0.02),
    )

    with pytest.raises(SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            Items,
            _EchoProduct,
            _Limits(Queued=0, InFlight=2),
        )

    assert tuple(
        Continuation.TaskIdentity
        for Continuation in Caught.value.OwnedContinuations
    ) == ("spawned-0", "spawned-1")
    assert tuple(
        Continuation.Receipt
        for Continuation in Caught.value.OwnedContinuations
    ) == tuple(Handle.LastReceipt for Handle in Handles)
    assert Actions == [
        ("spawned-0", "observe", 10.0),
        ("spawned-1", "observe", 10.0),
        ("spawned-0", "observe", 10.0),
    ]
    assert all(ActionAt < 10.02 for _Task, _Action, ActionAt in Actions)


@pytest.mark.parametrize("Interrupt", (None, KeyboardInterrupt, SystemExit))
def test_wait_observer_crossing_cleanup_cutoff_uses_last_in_authority_receipts(
    monkeypatch,
    Interrupt,
):
    Clock = _ControlledClock(20.0)
    Actions = []
    Handles = []
    ParentControl = (
        None if Interrupt is None else Interrupt("wait observer crossed cutoff")
    )

    def StartControlled(_Request, *_Arguments, **_KeywordArguments):
        Handle = _CutoffActionHandle(
            Clock,
            Actions,
            _Request.TaskIdentity,
        )
        Handles.append(Handle)
        return Handle

    def CrossCutoff(TaskIdentity, Action):
        Actions.append((TaskIdentity, f"wait-{Action}", Clock.Read()))
        Clock.Now = 20.021
        if ParentControl is not None:
            raise ParentControl

    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(SpawnedWork, "StartRuntimeOneShotProcess", StartControlled)
    Items = (
        _Item(_Request(0, DeadlineAt=20.01), None, CleanupSeconds=0.01),
        _Item(_Request(1, DeadlineAt=20.03), None, CleanupSeconds=0.02),
    )

    with pytest.raises(SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            Items,
            _EchoProduct,
            _Limits(Queued=0, InFlight=2),
            WaitObserver=CrossCutoff,
        )

    assert tuple(
        Continuation.Receipt
        for Continuation in Caught.value.OwnedContinuations
    ) == tuple(Handle.LastReceipt for Handle in Handles)
    assert Actions == [
        ("spawned-0", "observe", 20.0),
        ("spawned-1", "observe", 20.0),
        ("spawned-0", "wait-begin", 20.0),
    ]
    assert all(ActionAt < 20.02 for _Task, _Action, ActionAt in Actions)
    if ParentControl is not None:
        assert Caught.value.__cause__ is ParentControl


def test_cleanup_cutoff_blocks_the_next_queued_admission(monkeypatch):
    Clock = _ControlledClock(30.0)
    Actions = []
    Handles = []

    def StartControlled(_Request, *_Arguments, **_KeywordArguments):
        Handle = _CutoffActionHandle(
            Clock,
            Actions,
            _Request.TaskIdentity,
            CrossOnObserveCall=2,
            CrossTo=30.021,
        )
        Handles.append(Handle)
        return Handle

    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(SpawnedWork, "StartRuntimeOneShotProcess", StartControlled)
    Items = (
        _Item(_Request(0, DeadlineAt=30.01), None, CleanupSeconds=0.01),
        _Item(_Request(1, DeadlineAt=31.0), None, CleanupSeconds=0.50),
    )

    with pytest.raises(SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete):
        ExecuteBoundedSpawnedWorkBatch(
            Items,
            _EchoProduct,
            _Limits(Queued=1, InFlight=1),
        )

    assert len(Handles) == 1
    assert Handles[0].LastReceipt.TaskIdentity == "spawned-0"
    assert Actions == [
        ("spawned-0", "observe", 30.0),
        ("spawned-0", "observe", 30.0),
    ]


def test_start_crossing_surfaces_pre_admission_receipt(monkeypatch):
    Clock = _ControlledClock(35.0)
    PreAdmission = SimpleNamespace(
        TaskIdentity="spawned-0",
        CleanupCutoffBreached=False,
        ResourcesClosed=False,
        OutstandingOwnership=True,
        ParentControl=None,
    )
    PostCrossing = SimpleNamespace(**vars(PreAdmission))
    PostCrossing.CleanupCutoffBreached = True

    class CrossingStartHandle:
        AdmissionReceipt = PreAdmission
        LastReceipt = PostCrossing

        def Observe(self):
            raise AssertionError("no handle action may follow crossing admission")

    Handle = CrossingStartHandle()

    def StartControlled(*_Arguments, **_KeywordArguments):
        Clock.Now = 35.021
        return Handle

    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(SpawnedWork, "StartRuntimeOneShotProcess", StartControlled)
    Item = _Item(
        _Request(0, DeadlineAt=35.01),
        None,
        CleanupSeconds=0.01,
    )

    with pytest.raises(SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            (Item,),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
        )

    Continuation = Caught.value.OwnedContinuations[0]
    assert Continuation.Handle is Handle
    assert Continuation.Receipt is PreAdmission
    assert Continuation.Handle.LastReceipt is PostCrossing


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
def test_handle_receipt_control_crossing_cutoff_is_preserved_as_cause(
    monkeypatch,
    Interrupt,
):
    Clock = _ControlledClock(45.0)
    ParentControl = Interrupt("handle receipt crossed cutoff")

    class CrossingControlHandle(_CutoffActionHandle):
        def Observe(self):
            self.ObserveCalls += 1
            if self.ObserveCalls == 1:
                return self.LastReceipt
            Clock.Now = 45.021
            self.LastReceipt = SimpleNamespace(**vars(self.LastReceipt))
            self.LastReceipt.ParentControl = ParentControl
            return self.LastReceipt

    Handle = CrossingControlHandle(Clock, [], "spawned-0")
    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        lambda *_Arguments, **_KeywordArguments: Handle,
    )
    Item = _Item(
        _Request(0, DeadlineAt=45.01),
        None,
        CleanupSeconds=0.01,
    )

    with pytest.raises(SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            (Item,),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
        )

    assert Caught.value.__cause__ is ParentControl
    assert Caught.value.OwnedContinuations[0].Receipt is Handle.LastReceipt
    assert Caught.value.OwnedContinuations[0].Receipt.ParentControl is (
        ParentControl
    )


class _ReleasedResultHandle:
    def __init__(self, TaskIdentity, PublishedResult):
        self.ObserveCalls = 0
        self.LastReceipt = SimpleNamespace(
            TaskIdentity=TaskIdentity,
            ResourcesClosed=False,
            ProcessExitObserved=False,
            Reaped=False,
            ReleaseAcknowledged=False,
            WorkDeadlineObserved=False,
            CancellationRequested=False,
            ForceTerminationRequested=False,
            ForceTerminationDenied=False,
            ForceSignalSent=False,
            OutstandingOwnership=True,
            CleanupCutoffBreached=False,
            PublishedResult=None,
            ResultDiagnostic=None,
            OperationalFailure=None,
            ParentControl=None,
        )
        self._PublishedResult = PublishedResult

    def Observe(self):
        self.ObserveCalls += 1
        if self.ObserveCalls >= 2:
            self.LastReceipt.ProcessExitObserved = True
        return self.LastReceipt

    def ReapIfExited(self):
        self.LastReceipt.Reaped = True
        self.LastReceipt.PublishedResult = self._PublishedResult
        return self.LastReceipt

    def CloseReleased(self):
        self.LastReceipt.ResourcesClosed = True
        self.LastReceipt.ReleaseAcknowledged = True
        self.LastReceipt.OutstandingOwnership = False


@pytest.mark.parametrize(
    "Mutation, ExpectedDiagnostic",
    (
        ("task", "TaskIdentity"),
        ("operation", "Operation"),
        ("scope", "Scope"),
        ("freshness", "Freshness"),
        ("work-cap", "WorkUnits"),
        ("commit-eligibility", "CommitEligibility"),
        ("prepared-value", "PreparedValue"),
    ),
)
def test_parent_rejects_forged_child_execution_against_exact_request(
    monkeypatch,
    Mutation,
    ExpectedDiagnostic,
):
    Request = _Request(0)
    Execution = _ExpectedEchoExecution(Request, "value")
    Result = Execution.Result
    if Mutation == "task":
        Result = replace(Result, TaskIdentity="forged-task")
    elif Mutation == "operation":
        Result = replace(Result, Operation="forged-operation")
    elif Mutation == "scope":
        Result = replace(
            Result,
            Scope=RuntimeWorkScope(
                DomainIdentity="forged-domain",
                DependencyIdentities=("fixture-v1",),
            ),
        )
    elif Mutation == "freshness":
        Result = replace(Result, Freshness=RuntimeFreshness.StaleUnreviewed)
    elif Mutation == "work-cap":
        Result = replace(Result, WorkUnits=Request.WorkCap + 1)
    elif Mutation == "commit-eligibility":
        Result = replace(
            Result,
            CommitEligibility=RuntimeCommitEligibility.Eligible,
        )
    elif Mutation == "prepared-value":
        Execution = replace(Execution, Value=None)
    if Mutation != "prepared-value":
        Execution = replace(Execution, Result=Result)
    Encoded = pickle.dumps(Execution, protocol=pickle.HIGHEST_PROTOCOL)
    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        lambda *_Arguments, **_KeywordArguments: _ReleasedResultHandle(
            Request.TaskIdentity,
            Encoded,
        ),
    )

    Returned = ExecuteBoundedSpawnedWorkBatch(
        (_Item(Request, None),),
        _EchoProduct,
        _Limits(Queued=0, InFlight=1),
    ).Executions[0][1]

    assert Returned.Value is None
    assert Returned.Result.TaskIdentity == Request.TaskIdentity
    assert Returned.Result.Operation == Request.Operation
    assert Returned.Result.Scope == Request.Scope
    assert Returned.Result.Freshness == Request.Freshness
    assert Returned.Result.WorkUnits == 0
    assert Returned.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Returned.Result.Lifecycle is RuntimeLifecycle.Failed
    assert Returned.Result.TerminalReason is RuntimeTerminalReason.WorkerFailure
    assert Returned.Result.ProofIdentity is None
    assert dict(Returned.Result.Diagnostics) == {
        "ChildExecutionValidation": ExpectedDiagnostic,
    }


def test_parent_rejects_child_work_above_remaining_aggregate_grant():
    Request = _Request(0)
    Execution = replace(
        _ExpectedEchoExecution(Request, "value"),
        Result=replace(
            _ExpectedEchoExecution(Request, "value").Result,
            WorkUnits=1,
        ),
    )

    Validated, Failure = SpawnedWork._ValidateChildExecution(
        Request,
        Execution,
        0,
    )

    assert Validated is None
    assert Failure == "AggregateWorkUnits"


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
def test_post_reap_cleanup_parent_control_re_raises_after_release(
    monkeypatch,
    Interrupt,
):
    Request = _Request(0)
    Encoded = pickle.dumps(
        _ExpectedEchoExecution(Request, "value"),
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    ParentControl = Interrupt("post-reap cleanup control")

    class ParentControlCloseHandle(_ReleasedResultHandle):
        def __init__(self):
            super().__init__(Request.TaskIdentity, Encoded)
            self.CloseCalls = 0

        def CloseReleased(self):
            self.CloseCalls += 1
            if self.CloseCalls == 1:
                self.LastReceipt.ParentControl = ParentControl
                raise RuntimeError("controlled post-reap close failure")
            self.LastReceipt.ResourcesClosed = True
            self.LastReceipt.ReleaseAcknowledged = True
            self.LastReceipt.OutstandingOwnership = False

    Handle = ParentControlCloseHandle()
    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        lambda *_Arguments, **_KeywordArguments: Handle,
    )

    with pytest.raises(Interrupt) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            (_Item(Request, None),),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
        )

    assert Caught.value is ParentControl
    assert Handle.CloseCalls == 2
    assert Handle.LastReceipt.ResourcesClosed


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("ReleaseAfterControl", (True, False))
def test_cleanup_action_control_survives_ordinary_error_recovery(
    monkeypatch,
    Interrupt,
    ReleaseAfterControl,
):
    Clock = _ControlledClock(48.0)
    ParentControl = Interrupt("cleanup action parent control")

    class RecoveryControlHandle:
        def __init__(self):
            self.ObserveCalls = 0
            self.CancelCalls = 0
            self.LastReceipt = SimpleNamespace(
                TaskIdentity="spawned-0",
                ExactUnstarted=False,
                UnstartedReason=None,
                ResourcesClosed=False,
                ProcessExitObserved=False,
                Reaped=False,
                ReleaseAcknowledged=False,
                WorkDeadlineObserved=False,
                CancellationRequested=False,
                ForceTerminationRequested=False,
                ForceTerminationDenied=False,
                ForceSignalSent=False,
                OutstandingOwnership=True,
                CleanupCutoffBreached=False,
                PublishedResult=None,
                ResultDiagnostic=None,
                OperationalFailure=None,
                ParentControl=None,
            )

        def Observe(self):
            self.ObserveCalls += 1
            if self.CancelCalls and ReleaseAfterControl:
                self.LastReceipt.ProcessExitObserved = True
            return self.LastReceipt

        def RequestCancellation(self):
            self.CancelCalls += 1
            self.LastReceipt.CancellationRequested = True
            self.LastReceipt.ParentControl = ParentControl
            raise ParentControl

        def ForceTerminate(self):
            return self.LastReceipt

        def ReapIfExited(self):
            if self.LastReceipt.ProcessExitObserved:
                self.LastReceipt.Reaped = True
            return self.LastReceipt

        def CloseReleased(self):
            if not self.LastReceipt.Reaped:
                raise RuntimeError("controlled handle is not reaped")
            self.LastReceipt.ResourcesClosed = True
            self.LastReceipt.ReleaseAcknowledged = True
            self.LastReceipt.OutstandingOwnership = False

    Handle = RecoveryControlHandle()
    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        lambda *_Arguments, **_KeywordArguments: Handle,
    )

    def FailOrdinaryParentObserver(_TaskIdentity, _Action):
        raise RuntimeError("controlled ordinary parent failure")

    Item = _Item(
        _Request(0, DeadlineAt=48.01),
        None,
        CleanupSeconds=0.01,
    )
    if ReleaseAfterControl:
        with pytest.raises(Interrupt) as Caught:
            ExecuteBoundedSpawnedWorkBatch(
                (Item,),
                _EchoProduct,
                _Limits(Queued=0, InFlight=1),
                WaitObserver=FailOrdinaryParentObserver,
            )
        assert Caught.value is ParentControl
        assert Handle.LastReceipt.ResourcesClosed
        assert Handle.LastReceipt.ReleaseAcknowledged
        assert not Handle.LastReceipt.OutstandingOwnership
    else:
        with pytest.raises(
            SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete
        ) as Caught:
            ExecuteBoundedSpawnedWorkBatch(
                (Item,),
                _EchoProduct,
                _Limits(Queued=0, InFlight=1),
                WaitObserver=FailOrdinaryParentObserver,
            )
        assert Caught.value.__cause__ is ParentControl
        assert len(Caught.value.OwnedContinuations) == 1
        assert Caught.value.OwnedContinuations[0].Handle is Handle
        assert Caught.value.OwnedContinuations[0].Receipt.ParentControl is (
            ParentControl
        )
        assert Handle.LastReceipt.OutstandingOwnership
        assert not Handle.LastReceipt.ResourcesClosed


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("ObserverAction", ("begin", "end"))
def test_wait_observer_control_is_not_retried_after_release(
    monkeypatch,
    Interrupt,
    ObserverAction,
):
    Request = _Request(0)
    Encoded = pickle.dumps(
        _ExpectedEchoExecution(Request, "value"),
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    ParentControl = Interrupt(f"{ObserverAction} observer parent control")
    Handle = _ReleasedResultHandle(Request.TaskIdentity, Encoded)
    ObservedActions = []
    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        lambda *_Arguments, **_KeywordArguments: Handle,
    )

    def InterruptSelectedObserver(_TaskIdentity, Action):
        ObservedActions.append(Action)
        if Action == ObserverAction:
            raise ParentControl

    with pytest.raises(Interrupt) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            (_Item(Request, None),),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
            WaitObserver=InterruptSelectedObserver,
        )

    assert Caught.value is ParentControl
    assert ObservedActions == (
        ["begin"] if ObserverAction == "begin" else ["begin", "end"]
    )
    assert Handle.LastReceipt.ResourcesClosed
    assert Handle.LastReceipt.ReleaseAcknowledged
    assert not Handle.LastReceipt.OutstandingOwnership


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("ReleaseOnCancellation", (True, False))
def test_receipt_parent_control_keeps_exact_object_through_batch_cleanup(
    monkeypatch,
    Interrupt,
    ReleaseOnCancellation,
):
    Clock = _ControlledClock(50.0)
    ParentControl = Interrupt("controlled one-shot parent control")
    Handles = _InstallUnreleasableControlledHandles(
        monkeypatch,
        Clock,
        ParentControl=ParentControl,
        ReleaseOnCancellation=ReleaseOnCancellation,
    )
    Item = _Item(
        _Request(0, DeadlineAt=50.01),
        (0.0, "value"),
        CleanupSeconds=0.01,
    )

    if ReleaseOnCancellation:
        with pytest.raises(Interrupt) as Caught:
            ExecuteBoundedSpawnedWorkBatch(
                (Item,),
                _EchoProduct,
                _Limits(Queued=0, InFlight=1),
            )
        assert Caught.value is ParentControl
        assert Handles[0].Receipt.ReleaseAcknowledged
        assert Handles[0].Receipt.ResourcesClosed
        assert Handles[0].Receipt.PublishedResult is None
    else:
        with pytest.raises(
            SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete
        ) as Caught:
            ExecuteBoundedSpawnedWorkBatch(
                (Item,),
                _EchoProduct,
                _Limits(Queued=0, InFlight=1),
            )
        assert Caught.value.__cause__ is ParentControl
        assert Caught.value.OwnedContinuations[0].Handle is Handles[0]
        assert Caught.value.OwnedContinuations[0].Receipt is Handles[0].Receipt
        assert Handles[0].Receipt.OutstandingOwnership
        assert not Handles[0].Receipt.ReleaseAcknowledged
        assert Handles[0].Receipt.PublishedResult is None


@pytest.mark.parametrize("Boundary", ("post-start", "child-socket-close"))
@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
def test_real_parent_control_boundary_re_raises_exact_object_after_cleanup(
    monkeypatch,
    Boundary,
    Interrupt,
):
    ParentControl = Interrupt(f"controlled {Boundary} parent control")
    if Boundary == "post-start":
        OriginalStart = multiprocessing.context.SpawnProcess.start

        def StartThenInterrupt(Process):
            OriginalStart(Process)
            raise ParentControl

        monkeypatch.setattr(
            multiprocessing.context.SpawnProcess,
            "start",
            StartThenInterrupt,
        )
    else:
        _InjectExactChildSocketCloseFailure(monkeypatch, ParentControl)
    Item = _Item(_Request(0), (0.0, "value"))

    with pytest.raises(Interrupt) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            (Item,),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
        )

    assert Caught.value is ParentControl


@pytest.mark.parametrize("Boundary", ("post-start", "child-socket-close"))
@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
def test_real_parent_control_receipt_survives_unreleasable_cleanup_cutoff(
    monkeypatch,
    tmp_path,
    Boundary,
    Interrupt,
):
    ParentControl = Interrupt(f"controlled {Boundary} parent control")
    if Boundary == "post-start":
        OriginalStart = multiprocessing.context.SpawnProcess.start

        def StartThenInterrupt(Process):
            OriginalStart(Process)
            raise ParentControl

        monkeypatch.setattr(
            multiprocessing.context.SpawnProcess,
            "start",
            StartThenInterrupt,
        )
    else:
        _InjectExactChildSocketCloseFailure(monkeypatch, ParentControl)
    Marker = tmp_path / f"{Boundary}-{Interrupt.__name__}.entered"
    Payload = str(Marker).encode("utf-8") + b"\x00" + b"1.0"
    StartedAt = monotonic()
    DeadlineAt = StartedAt + 0.50
    CleanupAt = StartedAt + 0.65
    Handle = _StartOneShotOwned(
        _OneShotRequest(
            DeadlineAt,
            TaskIdentity=f"{Boundary}-{Interrupt.__name__}",
        ),
        _OneShotAuthority(DeadlineAt, CleanupAt, Force=False),
        Payload,
        _PublishEntryThenIgnoreCancellationUntilFixtureExpiry,
        _OneShotLimits(
            _PublishEntryThenIgnoreCancellationUntilFixtureExpiry,
            Payload,
        ),
    )
    monkeypatch.undo()
    try:
        EntryDeadline = monotonic() + 0.40
        while not Marker.exists() and monotonic() < EntryDeadline:
            sleep(0.005)
        assert Marker.exists()

        Receipt = Handle.RequestCancellation()
        assert Receipt.ParentControl is ParentControl
        Receipt = Handle.AdvanceUntil(float(CleanupAt))
        assert Receipt.ParentControl is ParentControl
        assert Receipt.CleanupCutoffBreached
        assert Receipt.OutstandingOwnership
        assert not Receipt.ReleaseAcknowledged
        assert Receipt.PublishedResult is None

        ExitDeadline = monotonic() + 2.0
        while not Receipt.ProcessExitObserved and monotonic() < ExitDeadline:
            sleep(0.005)
            Receipt = Handle.Observe()
        assert Receipt.ProcessExitObserved
        Receipt = Handle.ReapIfExited()
        assert Receipt.Reaped
        assert Receipt.OutstandingOwnership
        assert not Receipt.ReleaseAcknowledged
        assert Receipt.ParentControl is ParentControl
        Handle.CloseReleased()
        assert Handle.LastReceipt.ReleaseAcknowledged
        assert not Handle.LastReceipt.OutstandingOwnership
    finally:
        _RecoverAndClose(Handle)


@pytest.mark.parametrize("ForceOutcome", ("denied", "failure"))
def test_cleanup_cutoff_failure_returns_every_exact_owned_continuation(
    monkeypatch,
    ForceOutcome,
):
    Clock = _ControlledClock(100.0)
    Handles = _InstallUnreleasableControlledHandles(
        monkeypatch,
        Clock,
        ForceOutcome=ForceOutcome,
    )
    Items = tuple(
        _Item(
            _Request(Index, DeadlineAt=100.01),
            (0.0, f"value-{Index}"),
            CleanupSeconds=0.01,
        )
        for Index in range(3)
    )

    with pytest.raises(
        SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete
    ) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            Items,
            _EchoProduct,
            _Limits(Queued=1, InFlight=2),
        )

    Error = Caught.value
    assert Clock.Read() == pytest.approx(100.02)
    assert len(Clock.Sleeps) < 20
    assert len(Handles) == 2
    assert tuple(
        Continuation.TaskIdentity
        for Continuation in Error.OwnedContinuations
    ) == ("spawned-0", "spawned-1")
    assert tuple(
        Continuation.Handle
        for Continuation in Error.OwnedContinuations
    ) == tuple(Handles)
    assert all(
        Continuation.Receipt is Handle.Receipt
        for Continuation, Handle in zip(Error.OwnedContinuations, Handles)
    )
    assert all(
        Continuation.Receipt.ForceTerminationRequested
        and Continuation.Receipt.OutstandingOwnership
        and not Continuation.Receipt.ReleaseAcknowledged
        and Continuation.Receipt.PublishedResult is None
        and Continuation.Handle.CloseCalls == 0
        for Continuation in Error.OwnedContinuations
    )
    if ForceOutcome == "denied":
        assert all(
            Continuation.Receipt.ForceTerminationDenied
            and Continuation.Receipt.OperationalFailure is None
            for Continuation in Error.OwnedContinuations
        )
    else:
        assert all(
            not Continuation.Receipt.ForceTerminationDenied
            and Continuation.Receipt.OperationalFailure
            == "ControlledForceFailure"
            for Continuation in Error.OwnedContinuations
        )


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
def test_parent_base_exception_surfaces_cleanup_ownership_at_original_cutoff(
    monkeypatch,
    Interrupt,
):
    Clock = _ControlledClock(200.0)
    Handles = _InstallUnreleasableControlledHandles(monkeypatch, Clock)
    Item = _Item(
        _Request(0, DeadlineAt=200.01),
        (0.0, "value"),
        CleanupSeconds=0.01,
    )
    ParentInterruption = Interrupt("controlled parent interruption")

    def InterruptParent(_TaskIdentity, _Action):
        raise ParentInterruption

    with pytest.raises(
        SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete
    ) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            (Item,),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
            WaitObserver=InterruptParent,
        )

    Error = Caught.value
    assert Clock.Read() == pytest.approx(200.02)
    assert len(Clock.Sleeps) < 20
    assert Error.__cause__ is ParentInterruption
    assert len(Error.OwnedContinuations) == 1
    assert Error.OwnedContinuations[0].Handle is Handles[0]
    assert Error.OwnedContinuations[0].Receipt is Handles[0].Receipt
    assert Handles[0].Receipt.OutstandingOwnership
    assert not Handles[0].Receipt.ReleaseAcknowledged
    assert Handles[0].CloseCalls == 0


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


def test_batch_requires_explicit_positive_cleanup_allowance():
    Request = _Request(0)
    Item = RuntimeSpawnedWorkItem(
        Request=Request,
        Authority=RuntimeWorkAuthority(
            WorkDeadlineAt=Request.DeadlineAt,
            CleanupCutoffAt=Request.DeadlineAt,
            ForceTerminationAuthorized=True,
        ),
        Payload=(0.0, "value"),
    )

    with pytest.raises(ValueError):
        ExecuteBoundedSpawnedWorkBatch(
            (Item,),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
        )


def test_false_force_authority_allows_natural_exit_before_cleanup_cutoff():
    StartedAt = monotonic()
    Request = _Request(0, DeadlineAt=StartedAt + 0.15)
    Item = RuntimeSpawnedWorkItem(
        Request=Request,
        Authority=RuntimeWorkAuthority(
            WorkDeadlineAt=Request.DeadlineAt,
            CleanupCutoffAt=StartedAt + 0.50,
            ForceTerminationAuthorized=False,
        ),
        Payload=(0.20, "late"),
    )

    Batch = ExecuteBoundedSpawnedWorkBatch(
        (Item,),
        _EchoProduct,
        _Limits(Queued=0, InFlight=1),
    )
    Execution = Batch.Executions[0][1]

    assert Execution.Value is None
    assert Execution.Result.TerminalReason is (
        RuntimeTerminalReason.DeadlineExhausted
    )
    assert Batch.SubmittedTaskCount == 1


def test_false_force_authority_retains_capacity_and_queued_work_at_cutoff(
    tmp_path,
):
    StartedAt = monotonic()
    FirstRequest = _Request(0, DeadlineAt=StartedAt + 0.20)
    SecondRequest = _Request(1, DeadlineAt=StartedAt + 2.0)
    Items = (
        RuntimeSpawnedWorkItem(
            Request=FirstRequest,
            Authority=RuntimeWorkAuthority(
                WorkDeadlineAt=FirstRequest.DeadlineAt,
                CleanupCutoffAt=StartedAt + 0.30,
                ForceTerminationAuthorized=False,
            ),
            Payload=(str(tmp_path), 0.50, "first"),
        ),
        _Item(
            SecondRequest,
            (str(tmp_path), 0.0, "second"),
        ),
    )

    try:
        with pytest.raises(
            SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete
        ) as Caught:
            ExecuteBoundedSpawnedWorkBatch(
                Items,
                _ObservedProduct,
                _Limits(Queued=1, InFlight=1),
            )
        Continuation = Caught.value.OwnedContinuations[0]
        Receipt = Continuation.Receipt
        assert Continuation.TaskIdentity == "spawned-0"
        assert Receipt.CancellationRequested
        assert not Receipt.ForceTerminationRequested
        assert not Receipt.ForceSignalSent
        assert Receipt.OutstandingOwnership
        assert not Receipt.ReleaseAcknowledged
        assert (tmp_path / "spawned-0.started").exists()
        assert not (tmp_path / "spawned-1.started").exists()
    finally:
        if "Continuation" in locals():
            Handle = Continuation.Handle
            ReapDeadline = monotonic() + 2.0
            while monotonic() < ReapDeadline:
                Receipt = Handle.Observe()
                if Receipt.ProcessExitObserved:
                    Receipt = Handle.ReapIfExited()
                    if Receipt.Reaped:
                        Handle.CloseReleased()
                        break
                sleep(0.005)
            assert Handle.Observe().ResourcesClosed


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
    BaseItem = _Item(_Request(0), (0.0, b"x" * 4096))
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
    PayloadItem = _Item(_Request(0), (0.0, b"payload" * 128))
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
            _Item(_Request(1), PayloadItem.Payload),
        ),
        _EchoProduct,
        _Limits(PayloadBytes=ExactPayloadBytes - 1),
    ).Executions[0][1]

    ResultItem = _Item(_Request(2), (0.0, b"result" * 1_024))
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
    Item = _Item(_Request(0), None)

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
    Item = _Item(_Request(0), (0.0, "value"))

    def InterruptParent(_TaskIdentity, _Action):
        raise Interrupt("parent interruption")

    with pytest.raises(Interrupt, match="parent interruption"):
        ExecuteBoundedSpawnedWorkBatch(
            (Item,),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
            WaitObserver=InterruptParent,
        )


def test_forced_deadline_release_admits_the_next_queued_request(tmp_path):
    StartedAt = monotonic()
    Items = (
        _Item(
            _Request(0, DeadlineAt=StartedAt + 0.50),
            (str(tmp_path), 2.0, "first"),
            CleanupSeconds=0.75,
        ),
        _Item(
            _Request(1, DeadlineAt=StartedAt + 3.0),
            (str(tmp_path), 1.0, "second"),
        ),
        _Item(
            _Request(2, DeadlineAt=StartedAt + 3.0),
            (str(tmp_path), 0.0, "third"),
        ),
    )
    Batch = ExecuteBoundedSpawnedWorkBatch(
        Items,
        _ObservedProduct,
        _Limits(Queued=1, InFlight=2),
    )

    StartedAtByTask = {
        Item.Request.TaskIdentity: float(
            (tmp_path / f"{Item.Request.TaskIdentity}.started").read_text()
        )
        for Item in Items
    }
    CompletedSecondAt = float((tmp_path / "spawned-1.completed").read_text())

    assert Batch.SubmittedTaskCount == 3
    assert len(tuple(tmp_path.glob("*.started"))) == 3
    assert not (tmp_path / "spawned-0.completed").exists()
    assert StartedAtByTask["spawned-2"] < CompletedSecondAt
    assert Batch.Executions[0][1].Result.TerminalReason is (
        RuntimeTerminalReason.DeadlineExhausted
    )
    assert Batch.Executions[1][1].Result.SearchOutcome is (
        RuntimeSearchOutcome.Prepared
    )
    assert Batch.Executions[2][1].Result.SearchOutcome is (
        RuntimeSearchOutcome.Prepared
    )


def test_running_deadline_does_not_wait_for_natural_worker_completion(tmp_path):
    StartedAt = monotonic()
    Item = _Item(
        _Request(0, DeadlineAt=StartedAt + 0.20),
        (str(tmp_path), 1.20, "late"),
        CleanupSeconds=0.60,
    )

    Batch = ExecuteBoundedSpawnedWorkBatch(
        (Item,),
        _ObservedProduct,
        _Limits(Queued=0, InFlight=1),
    )
    ReturnedAfter = monotonic() - StartedAt
    Execution = Batch.Executions[0][1]

    assert (tmp_path / "spawned-0.started").exists()
    assert ReturnedAfter < 0.80
    assert not (tmp_path / "spawned-0.completed").exists()
    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.DeadlineExhausted


def test_earlier_deadline_does_not_discard_a_later_running_task():
    StartedAt = monotonic()
    Earlier = _Item(
        _Request(0, DeadlineAt=StartedAt + 0.50),
        (0.80, "earlier"),
    )
    Later = _Item(
        _Request(1, DeadlineAt=StartedAt + 3.0),
        (0.80, "later"),
    )
    Together = ExecuteBoundedSpawnedWorkBatch(
        (Earlier, Later),
        _EchoProduct,
        _Limits(Queued=0, InFlight=2),
    )
    LaterAlone = ExecuteBoundedSpawnedWorkBatch(
        (
            _Item(_Request(2), (0.0, "later")),
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
    Item = _Item(
        _Request(0, DeadlineAt=monotonic() - 1.0),
        (0.0, "late"),
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


def test_pre_cancelled_request_returns_without_starting_a_process(monkeypatch):
    Starts = []
    Request = _Request(0)
    Request = replace(
        Request,
        Lifecycle=RuntimeLifecycle.CancellationRequested,
        Cancellation=RuntimeCancellationSnapshot(
            Requested=True,
            Identity="spawned-0:pre-cancelled",
            Reason="caller-cancelled-before-admission",
        ),
    )

    def RejectStart(*Arguments, **_KeywordArguments):
        Starts.append(Arguments)
        raise AssertionError("pre-cancelled work must not allocate or start")

    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        RejectStart,
    )

    Batch = ExecuteBoundedSpawnedWorkBatch(
        (_Item(Request, None),),
        _EchoProduct,
        _Limits(Queued=0, InFlight=1),
    )
    Execution = Batch.Executions[0][1]

    assert Starts == []
    assert Batch.AdmittedTaskCount == 0
    assert Batch.SubmittedTaskCount == 0
    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.Cancelled
    assert Execution.Result.Lifecycle is RuntimeLifecycle.TerminatedGracefully
    assert Execution.Result.WorkUnits == 0
    assert Execution.Result.ProofIdentity is None


@pytest.mark.parametrize(
    "UnstartedReason, ExpectedReason, ExpectedLifecycle",
    (
        (
            "WorkDeadlineExpired",
            RuntimeTerminalReason.DeadlineExhausted,
            RuntimeLifecycle.Completed,
        ),
        (
            "AllocationFailure",
            RuntimeTerminalReason.WorkerFailure,
            RuntimeLifecycle.Failed,
        ),
    ),
)
def test_unstarted_receipt_reason_controls_batch_classification(
    monkeypatch,
    UnstartedReason,
    ExpectedReason,
    ExpectedLifecycle,
):
    class ExactUnstartedHandle:
        def __init__(self):
            self.Receipt = SimpleNamespace(
                ExactUnstarted=True,
                ResourcesClosed=True,
                OutstandingOwnership=False,
                UnstartedReason=UnstartedReason,
                OperationalFailure=(
                    "RuntimeError"
                    if UnstartedReason == "AllocationFailure"
                    else None
                ),
                ParentControl=None,
            )
            self.LastReceipt = self.Receipt

        def Observe(self):
            return self.Receipt

    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        lambda *_Arguments, **_KeywordArguments: ExactUnstartedHandle(),
    )
    Item = _Item(_Request(0), (0.0, "value"))

    Batch = ExecuteBoundedSpawnedWorkBatch(
        (Item,),
        _EchoProduct,
        _Limits(Queued=0, InFlight=1),
    )
    Execution = Batch.Executions[0][1]

    assert Execution.Value is None
    assert Execution.Result.TerminalReason is ExpectedReason
    assert Execution.Result.Lifecycle is ExpectedLifecycle
    if UnstartedReason == "AllocationFailure":
        assert dict(Execution.Result.Diagnostics) == {
            "OperationalFailure": "RuntimeError",
            "UnstartedReason": "AllocationFailure",
        }


def test_closed_unstarted_deadline_receipt_crossing_cleanup_has_no_continuation(
    monkeypatch,
):
    Clock = _ControlledClock(32.0)

    class ClosedDeadlineHandle:
        def __init__(self):
            self.LastReceipt = SimpleNamespace(
                TaskIdentity="spawned-0",
                ExactUnstarted=True,
                UnstartedReason="WorkDeadlineExpired",
                OperationalFailure=None,
                ParentControl=None,
                ResourcesClosed=True,
                ReleaseAcknowledged=True,
                OutstandingOwnership=False,
            )

    Handle = ClosedDeadlineHandle()

    def ReturnClosedDeadlineHandle(*_Arguments, **_KeywordArguments):
        Clock.Now = 34.0
        return Handle

    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        ReturnClosedDeadlineHandle,
    )
    Item = _Item(
        _Request(0, DeadlineAt=33.0),
        None,
        CleanupSeconds=1.0,
    )

    Batch = ExecuteBoundedSpawnedWorkBatch(
        (Item,),
        _EchoProduct,
        _Limits(Queued=0, InFlight=1),
    )
    Execution = Batch.Executions[0][1]

    assert Batch.SubmittedTaskCount == 0
    assert Execution.Value is None
    assert Execution.Result.TerminalReason is (
        RuntimeTerminalReason.DeadlineExhausted
    )
    assert Handle.LastReceipt.ResourcesClosed
    assert Handle.LastReceipt.ReleaseAcknowledged
    assert not Handle.LastReceipt.OutstandingOwnership


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
def test_closed_unstarted_allocation_control_re_raises_exact_object(
    monkeypatch,
    Interrupt,
):
    ParentControl = Interrupt("pre-allocation parent control")
    AllocationCalls = []

    def RaiseBeforeAllocation(*_Arguments, **_KeywordArguments):
        AllocationCalls.append("request-memory")
        raise ParentControl

    monkeypatch.setattr(OneShotProcess, "SharedMemory", RaiseBeforeAllocation)

    with pytest.raises(Interrupt) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            (_Item(_Request(0), None),),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
        )

    assert Caught.value is ParentControl
    assert AllocationCalls == ["request-memory"]


@pytest.mark.parametrize(
    "FailureStage",
    (
        "request-memory",
        "result-memory",
        "readiness-socket",
        "cancellation-socket",
        "completion-socket",
        "process",
    ),
)
def test_each_one_shot_allocation_failure_closes_created_resources(
    monkeypatch,
    FailureStage,
):
    SharedMemoryBefore = _SharedMemoryResources()
    SocketsBefore = _OpenSocketInodes()
    OriginalSharedMemory = OneShotProcess.SharedMemory
    OriginalSocketPair = OneShotProcess.socket.socketpair
    MemoryCalls = 0
    SocketCalls = 0

    def ControlledSharedMemory(*Arguments, **KeywordArguments):
        nonlocal MemoryCalls
        MemoryCalls += 1
        Stage = "request-memory" if MemoryCalls == 1 else "result-memory"
        if FailureStage == Stage:
            raise RuntimeError(f"controlled {FailureStage} failure")
        return OriginalSharedMemory(*Arguments, **KeywordArguments)

    def ControlledSocketPair(*Arguments, **KeywordArguments):
        nonlocal SocketCalls
        SocketCalls += 1
        Stage = (
            "readiness-socket",
            "cancellation-socket",
            "completion-socket",
        )[SocketCalls - 1]
        if FailureStage == Stage:
            raise RuntimeError(f"controlled {FailureStage} failure")
        return OriginalSocketPair(*Arguments, **KeywordArguments)

    def FailProcessConstruction(_Context, *_Arguments, **_KeywordArguments):
        raise RuntimeError("controlled process construction failure")

    monkeypatch.setattr(OneShotProcess, "SharedMemory", ControlledSharedMemory)
    monkeypatch.setattr(
        OneShotProcess.socket,
        "socketpair",
        ControlledSocketPair,
    )
    if FailureStage == "process":
        monkeypatch.setattr(
            multiprocessing.context.SpawnContext,
            "Process",
            FailProcessConstruction,
        )
    DeadlineAt = monotonic() + 3.0
    Handle = StartRuntimeOneShotProcess(
        _OneShotRequest(
            DeadlineAt,
            TaskIdentity=f"allocation-{FailureStage}",
        ),
        _OneShotAuthority(DeadlineAt, DeadlineAt + 1.0),
        b"",
        _OneShotEchoBytes,
        _OneShotLimits(_OneShotEchoBytes, b""),
    )
    monkeypatch.undo()

    Receipt = Handle.Observe()
    assert Receipt.ExactUnstarted
    assert Receipt.UnstartedReason == "AllocationFailure"
    assert Receipt.OperationalFailure == "RuntimeError"
    assert Receipt.RequestSharedMemoryName is None
    assert Receipt.ResultSharedMemoryName is None
    assert Receipt.SynchronizationResourceIdentities == ()
    assert Receipt.ResourcesClosed
    assert _SharedMemoryResources() == SharedMemoryBefore
    assert _OpenSocketInodes() == SocketsBefore
    Handle.CloseReleased()


def test_allocation_rollback_attempts_every_resource_after_cleanup_failure(
    monkeypatch,
):
    Actions = []

    class ControlledMemory:
        def __init__(self, Name, FailClose=False):
            self.name = Name
            self.buf = bytearray(1_000_000)
            self.FailClose = FailClose

        def close(self):
            Actions.append((self.name, "close"))
            if self.FailClose:
                raise RuntimeError(f"controlled {self.name} close failure")

        def unlink(self):
            Actions.append((self.name, "unlink"))

    class ControlledSocket:
        def __init__(self, Name, FailClose=False):
            self.Name = Name
            self.FailClose = FailClose

        def setblocking(self, _Blocking):
            return None

        def fileno(self):
            return -1

        def close(self):
            Actions.append((self.Name, "close"))
            if self.FailClose:
                raise RuntimeError(f"controlled {self.Name} close failure")

    Memories = [
        ControlledMemory("request-memory", FailClose=True),
        ControlledMemory("result-memory"),
    ]
    SocketPairCalls = 0

    def ControlledSharedMemory(*_Arguments, **_KeywordArguments):
        return Memories.pop(0)

    def ControlledSocketPair(*_Arguments, **_KeywordArguments):
        nonlocal SocketPairCalls
        SocketPairCalls += 1
        Parent = ControlledSocket(f"socket-{SocketPairCalls}-parent")
        Child = ControlledSocket(
            f"socket-{SocketPairCalls}-child",
            FailClose=SocketPairCalls == 3,
        )
        return Parent, Child

    def FailProcessConstruction(_Context, *_Arguments, **_KeywordArguments):
        raise ValueError("controlled original allocation failure")

    monkeypatch.setattr(OneShotProcess, "SharedMemory", ControlledSharedMemory)
    monkeypatch.setattr(OneShotProcess.socket, "socketpair", ControlledSocketPair)
    monkeypatch.setattr(
        multiprocessing.context.SpawnContext,
        "Process",
        FailProcessConstruction,
    )
    DeadlineAt = monotonic() + 3.0

    Handle = StartRuntimeOneShotProcess(
        _OneShotRequest(DeadlineAt, TaskIdentity="allocation-ledger"),
        _OneShotAuthority(DeadlineAt, DeadlineAt + 1.0),
        b"",
        _OneShotEchoBytes,
        _OneShotLimits(_OneShotEchoBytes, b""),
    )
    Receipt = Handle.LastReceipt

    assert Receipt.ExactUnstarted
    assert Receipt.UnstartedReason == "AllocationFailure"
    assert Receipt.OperationalFailure == "ValueError"
    assert Receipt.OutstandingOwnership
    assert not Receipt.ResourcesClosed
    assert set(Receipt.AllocationCleanupFailures) == {
        "RequestSharedMemory.close:RuntimeError",
        "CompletionChildSocket.close:RuntimeError",
    }
    assert Actions == [
        ("socket-3-child", "close"),
        ("socket-3-parent", "close"),
        ("socket-2-child", "close"),
        ("socket-2-parent", "close"),
        ("socket-1-child", "close"),
        ("socket-1-parent", "close"),
        ("result-memory", "close"),
        ("result-memory", "unlink"),
        ("request-memory", "close"),
        ("request-memory", "unlink"),
    ]


def test_spawned_batch_surfaces_unreleased_allocation_ledger_at_cutoff(
    monkeypatch,
):
    Clock = _ControlledClock(40.0)

    class AllocationCleanupIncompleteHandle:
        def __init__(self):
            self.CloseCalls = 0
            self.CancelCalls = 0
            self.ForceCalls = 0
            self.LastReceipt = SimpleNamespace(
                TaskIdentity="spawned-0",
                ExactUnstarted=True,
                UnstartedReason="AllocationFailure",
                ResourcesClosed=False,
                ProcessExitObserved=False,
                Reaped=False,
                ReleaseAcknowledged=False,
                WorkDeadlineObserved=False,
                CancellationRequested=False,
                ForceTerminationRequested=False,
                ForceTerminationDenied=False,
                ForceSignalSent=False,
                OutstandingOwnership=True,
                CleanupCutoffBreached=False,
                PublishedResult=None,
                ResultDiagnostic=None,
                OperationalFailure="ValueError",
                ParentControl=None,
                RequestSharedMemoryName="retained-request-memory",
                ResultSharedMemoryName=None,
                AllocationCleanupFailures=(
                    "RequestSharedMemory.close:RuntimeError",
                ),
            )

        def Observe(self):
            return self.LastReceipt

        def CloseReleased(self):
            self.CloseCalls += 1
            raise RuntimeError("controlled persistent cleanup failure")

        def RequestCancellation(self):
            self.CancelCalls += 1
            return self.LastReceipt

        def ForceTerminate(self):
            self.ForceCalls += 1
            return self.LastReceipt

    Handle = AllocationCleanupIncompleteHandle()
    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "sleep", Clock.Sleep)
    monkeypatch.setattr(
        SpawnedWork,
        "StartRuntimeOneShotProcess",
        lambda *_Arguments, **_KeywordArguments: Handle,
    )
    Item = _Item(
        _Request(0, DeadlineAt=40.01),
        None,
        CleanupSeconds=0.01,
    )

    with pytest.raises(SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete) as Caught:
        ExecuteBoundedSpawnedWorkBatch(
            (Item,),
            _EchoProduct,
            _Limits(Queued=0, InFlight=1),
        )

    Continuation = Caught.value.OwnedContinuations[0]
    assert Continuation.Handle is Handle
    assert Continuation.Receipt is Handle.LastReceipt
    assert Continuation.Receipt.RequestSharedMemoryName == (
        "retained-request-memory"
    )
    assert Continuation.Receipt.AllocationCleanupFailures == (
        "RequestSharedMemory.close:RuntimeError",
    )
    assert Continuation.Receipt.OutstandingOwnership
    assert not Continuation.Receipt.ResourcesClosed
    assert Handle.CloseCalls >= 2
    assert Handle.CancelCalls == 0
    assert Handle.ForceCalls == 0


def test_reap_retains_capacity_until_owned_resources_are_closed(monkeypatch):
    Clock = _ControlledClock(55.0)
    Closed = []

    class ExitedProcess:
        pid = 321
        sentinel = object()
        exitcode = 0

        def join(self, timeout):
            assert timeout == 0

        def close(self):
            Closed.append("process")

    Process = ExitedProcess()
    DeadlineAt = 56.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity="reap-before-release"),
        _OneShotAuthority(DeadlineAt, 57.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"r" * 32,
    )
    Handle._Process = Process
    Handle._AllocationResources = [("process", "Process", Process)]
    Handle._ProcessExitObserved = True
    Handle._ProcessExitObservedAt = Clock.Read()
    Handle._ExitCode = 0
    Handle._OutstandingOwnership = True
    Handle._Receipt()
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)
    monkeypatch.setattr(
        OneShotProcess,
        "wait",
        lambda *_Arguments, **_KeywordArguments: (Process.sentinel,),
    )

    Reaped = Handle.ReapIfExited()

    assert Reaped.Reaped
    assert not Reaped.ResourcesClosed
    assert Reaped.OutstandingOwnership
    assert not Reaped.ReleaseAcknowledged
    assert Closed == []

    Handle.CloseReleased()
    Released = Handle.LastReceipt

    assert Released.ResourcesClosed
    assert Released.ReleaseAcknowledged
    assert not Released.OutstandingOwnership
    assert Closed == ["process"]


def _ControlledReleasedOneShotHandle(Clock, Resources):
    DeadlineAt = Clock.Read() + 1.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity="released-cleanup"),
        _OneShotAuthority(DeadlineAt, DeadlineAt + 1.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"c" * 32,
    )
    Handle._RequestMemory = Resources["RequestSharedMemory"]
    Handle._ResultMemory = Resources["ResultSharedMemory"]
    Handle._ReadinessSocket = Resources["ReadinessParentSocket"]
    Handle._CancellationSocket = Resources["CancellationParentSocket"]
    Handle._CompletionSocket = Resources["CompletionParentSocket"]
    Handle._Process = Resources["Process"]
    Handle._AllocationResources = [
        (
            "memory" if "SharedMemory" in Name else (
                "process" if Name == "Process" else "socket"
            ),
            Name,
            Resource,
        )
        for Name, Resource in Resources.items()
    ]
    Handle._Reaped = True
    Handle._ReleaseAcknowledged = False
    Handle._OutstandingOwnership = True
    Handle._ResourcesClosed = False
    Handle._Receipt()
    return Handle


def test_post_reap_close_stops_inside_primitive_at_cleanup_cutoff(monkeypatch):
    Clock = _ControlledClock(60.0)
    Actions = []

    class ControlledResource:
        def __init__(self, Name, *, CrossOnClose=False):
            self.Name = Name
            self.name = Name
            self.CrossOnClose = CrossOnClose

        def fileno(self):
            return -1

        def close(self):
            Actions.append((self.Name, "close", Clock.Read()))
            if self.CrossOnClose:
                Clock.Now = 62.0

        def unlink(self):
            Actions.append((self.Name, "unlink", Clock.Read()))

    Resources = {
        "RequestSharedMemory": ControlledResource(
            "request",
            CrossOnClose=True,
        ),
        "ResultSharedMemory": ControlledResource("result"),
        "ReadinessParentSocket": ControlledResource("readiness"),
        "CancellationParentSocket": ControlledResource("cancellation"),
        "CompletionParentSocket": ControlledResource("completion"),
        "Process": ControlledResource("process"),
    }
    Handle = _ControlledReleasedOneShotHandle(Clock, Resources)
    Before = Handle.LastReceipt
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)

    with pytest.raises(RuntimeError, match="cleanup cutoff"):
        Handle.CloseReleased()

    assert Actions == [("request", "close", 60.0)]
    assert Handle.LastReceipt is Before
    assert Handle.LastReceipt.ResourcesClosed is False
    assert Handle.LastReceipt.OutstandingOwnership
    assert not Handle.LastReceipt.ReleaseAcknowledged


def test_post_reap_cleanup_attempts_all_resources_and_ledgers_failures(
    monkeypatch,
):
    Clock = _ControlledClock(70.0)
    Actions = []

    class ControlledResource:
        def __init__(self, Name, *, FailClose=False, FailUnlink=False):
            self.Name = Name
            self.name = Name
            self.FailClose = FailClose
            self.FailUnlink = FailUnlink

        def fileno(self):
            return -1

        def close(self):
            Actions.append((self.Name, "close"))
            if self.FailClose:
                self.FailClose = False
                raise RuntimeError(f"controlled {self.Name} close")

        def unlink(self):
            Actions.append((self.Name, "unlink"))
            if self.FailUnlink:
                self.FailUnlink = False
                raise ValueError(f"controlled {self.Name} unlink")

    Resources = {
        "RequestSharedMemory": ControlledResource(
            "request",
            FailClose=True,
        ),
        "ResultSharedMemory": ControlledResource(
            "result",
            FailUnlink=True,
        ),
        "ReadinessParentSocket": ControlledResource("readiness"),
        "CancellationParentSocket": ControlledResource("cancellation"),
        "CompletionParentSocket": ControlledResource("completion"),
        "Process": ControlledResource("process"),
    }
    Handle = _ControlledReleasedOneShotHandle(Clock, Resources)
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)

    with pytest.raises(RuntimeError, match="cleanup remains incomplete"):
        Handle.CloseReleased()

    assert Actions == [
        ("request", "close"),
        ("request", "unlink"),
        ("result", "close"),
        ("result", "unlink"),
        ("readiness", "close"),
        ("cancellation", "close"),
        ("completion", "close"),
        ("process", "close"),
    ]
    assert set(Handle.LastReceipt.ResourceCleanupFailures) == {
        "RequestSharedMemory.close:RuntimeError",
        "ResultSharedMemory.unlink:ValueError",
    }
    assert not Handle.LastReceipt.ResourcesClosed
    assert Handle.LastReceipt.OutstandingOwnership
    assert not Handle.LastReceipt.ReleaseAcknowledged
    assert Handle.LastReceipt.RequestSharedMemoryName == "request"
    assert Handle.LastReceipt.ResultSharedMemoryName == "result"

    Handle.CloseReleased()
    assert Handle.LastReceipt.ResourcesClosed
    assert not Handle.LastReceipt.OutstandingOwnership
    assert Handle.LastReceipt.ReleaseAcknowledged


def test_post_reap_cleanup_preserves_prior_failure_when_later_close_crosses(
    monkeypatch,
):
    Clock = _ControlledClock(75.0)
    Actions = []

    class ControlledResource:
        def __init__(self, Name, *, FailClose=False, CrossOnClose=False):
            self.Name = Name
            self.name = Name
            self.FailClose = FailClose
            self.CrossOnClose = CrossOnClose

        def fileno(self):
            return -1

        def close(self):
            Actions.append((self.Name, "close", Clock.Read()))
            if self.FailClose:
                raise RuntimeError(f"controlled {self.Name} close")
            if self.CrossOnClose:
                Clock.Now = 77.0

        def unlink(self):
            Actions.append((self.Name, "unlink", Clock.Read()))

    Resources = {
        "RequestSharedMemory": ControlledResource(
            "request",
            FailClose=True,
        ),
        "ResultSharedMemory": ControlledResource(
            "result",
            CrossOnClose=True,
        ),
        "ReadinessParentSocket": ControlledResource("readiness"),
        "CancellationParentSocket": ControlledResource("cancellation"),
        "CompletionParentSocket": ControlledResource("completion"),
        "Process": ControlledResource("process"),
    }
    Handle = _ControlledReleasedOneShotHandle(Clock, Resources)
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)

    with pytest.raises(RuntimeError, match="cleanup cutoff"):
        Handle.CloseReleased()

    assert Actions == [
        ("request", "close", 75.0),
        ("request", "unlink", 75.0),
        ("result", "close", 75.0),
    ]
    assert Handle.LastReceipt.ResourceCleanupFailures == (
        "RequestSharedMemory.close:RuntimeError",
    )
    assert not Handle.LastReceipt.ResourcesClosed


def test_allocation_cleanup_preserves_prior_failure_when_later_close_crosses(
    monkeypatch,
):
    Clock = _ControlledClock(85.0)
    Actions = []

    class ControlledSocket:
        def __init__(self, Name, *, FailClose=False, CrossOnClose=False):
            self.Name = Name
            self.FailClose = FailClose
            self.CrossOnClose = CrossOnClose

        def fileno(self):
            return -1

        def close(self):
            Actions.append((self.Name, "close", Clock.Read()))
            if self.FailClose:
                raise RuntimeError(f"controlled {self.Name} close")
            if self.CrossOnClose:
                Clock.Now = 87.0

    DeadlineAt = 86.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity="allocation-mixed"),
        _OneShotAuthority(DeadlineAt, 87.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"m" * 32,
    )
    Cross = ControlledSocket("cross", CrossOnClose=True)
    Failure = ControlledSocket("failure", FailClose=True)
    Handle._AllocationResources = [
        ("socket", "CrossSocket", Cross),
        ("socket", "FailureSocket", Failure),
    ]
    Handle._ChildSocketCopies = (Cross, Failure)
    Handle._ExactUnstarted = True
    Handle._UnstartedReason = "AllocationFailure"
    Handle._OperationalFailure = "ValueError"
    Handle._OutstandingOwnership = True
    Handle._Receipt()
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)

    Handle._CleanupAllocationResources()

    assert Actions == [
        ("failure", "close", 85.0),
        ("cross", "close", 85.0),
    ]
    assert Handle.LastReceipt.AllocationCleanupFailures == (
        "FailureSocket.close:RuntimeError",
    )
    assert Handle.LastReceipt.OutstandingOwnership
    assert not Handle.LastReceipt.ResourcesClosed


def test_startup_stops_after_allocation_crosses_cleanup_cutoff(monkeypatch):
    Clock = _ControlledClock(135.0)
    Actions = []

    class CrossingMemory:
        name = "crossing-request-memory"
        buf = bytearray(1_000_000)

        def close(self):
            Actions.append(("memory", "close", Clock.Read()))

        def unlink(self):
            Actions.append(("memory", "unlink", Clock.Read()))

    def ControlledSharedMemory(*_Arguments, **_KeywordArguments):
        Actions.append(("memory", "allocate", Clock.Read()))
        Clock.Now = 137.0
        return CrossingMemory()

    def RejectSocketPair(*_Arguments, **_KeywordArguments):
        Actions.append(("socket", "allocate", Clock.Read()))
        raise AssertionError("socket allocation must not follow cutoff crossing")

    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)
    monkeypatch.setattr(OneShotProcess, "SharedMemory", ControlledSharedMemory)
    monkeypatch.setattr(OneShotProcess.socket, "socketpair", RejectSocketPair)
    DeadlineAt = 136.0

    Handle = StartRuntimeOneShotProcess(
        _OneShotRequest(DeadlineAt, TaskIdentity="startup-cross"),
        _OneShotAuthority(DeadlineAt, 137.0),
        b"",
        _OneShotEchoBytes,
        _OneShotLimits(_OneShotEchoBytes, b""),
    )

    assert Actions == [("memory", "allocate", 135.0)]
    assert Handle.LastReceipt.RequestSharedMemoryName == (
        "crossing-request-memory"
    )
    assert Handle.LastReceipt.OutstandingOwnership
    assert not Handle.LastReceipt.ResourcesClosed
    assert Handle.LastReceipt.ExactUnstarted
    assert Handle.LastReceipt.UnstartedReason == "CleanupCutoffExpired"


def test_startup_header_write_captures_immediate_pre_action_receipt(
    monkeypatch,
):
    Clock = _ControlledClock(145.0)
    Actions = []
    OriginalHeader = OneShotProcess._REQUEST_HEADER
    MemoryCount = 0
    SocketCount = 0

    class ControlledMemory:
        def __init__(self, Name):
            self.name = Name
            self.buf = bytearray(1_000_000)

        def close(self):
            Actions.append((self.name, "close"))

        def unlink(self):
            Actions.append((self.name, "unlink"))

    class ControlledSocket:
        def __init__(self, Name):
            self.Name = Name

        def fileno(self):
            return -1

        def setblocking(self, _Blocking):
            Actions.append((self.Name, "setblocking"))

        def close(self):
            Actions.append((self.Name, "close"))

    class CrossingHeader:
        size = OriginalHeader.size

        def pack_into(self, *Arguments):
            Actions.append(("request-header", "write"))
            OriginalHeader.pack_into(*Arguments)
            Clock.Now = 147.0

    def ControlledSharedMemory(*_Arguments, **_KeywordArguments):
        nonlocal MemoryCount
        MemoryCount += 1
        Actions.append((f"memory-{MemoryCount}", "allocate"))
        return ControlledMemory(f"memory-{MemoryCount}")

    def ControlledSocketPair(*_Arguments, **_KeywordArguments):
        nonlocal SocketCount
        SocketCount += 1
        Actions.append((f"socket-{SocketCount}", "allocate"))
        return (
            ControlledSocket(f"socket-{SocketCount}-parent"),
            ControlledSocket(f"socket-{SocketCount}-child"),
        )

    def RejectProcess(*_Arguments, **_KeywordArguments):
        Actions.append(("process", "construct"))
        raise AssertionError("process construction must not follow header crossing")

    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)
    monkeypatch.setattr(OneShotProcess, "SharedMemory", ControlledSharedMemory)
    monkeypatch.setattr(OneShotProcess.socket, "socketpair", ControlledSocketPair)
    monkeypatch.setattr(OneShotProcess, "_REQUEST_HEADER", CrossingHeader())
    monkeypatch.setattr(
        multiprocessing.context.SpawnContext,
        "Process",
        RejectProcess,
    )
    DeadlineAt = 146.0

    Handle = StartRuntimeOneShotProcess(
        _OneShotRequest(DeadlineAt, TaskIdentity="header-cross"),
        _OneShotAuthority(DeadlineAt, 147.0),
        b"",
        _OneShotEchoBytes,
        _OneShotLimits(_OneShotEchoBytes, b""),
    )

    assert ("request-header", "write") in Actions
    assert ("process", "construct") not in Actions
    assert Handle.AdmissionReceipt.CleanupCutoffBreached is False
    assert Handle.AdmissionReceipt.RequestSharedMemoryName == "memory-1"
    assert Handle.AdmissionReceipt.ResultSharedMemoryName == "memory-2"
    assert Handle.LastReceipt.CleanupCutoffBreached is True
    assert Handle.LastReceipt.UnstartedReason == "CleanupCutoffExpired"


def test_child_socket_cleanup_preserves_prior_failure_when_later_close_crosses(
    monkeypatch,
):
    Clock = _ControlledClock(95.0)
    Actions = []

    class ControlledSocket:
        def __init__(self, Name, *, FailClose=False, CrossOnClose=False):
            self.Name = Name
            self.FailClose = FailClose
            self.CrossOnClose = CrossOnClose

        def fileno(self):
            return -1

        def close(self):
            Actions.append((self.Name, "close", Clock.Read()))
            if self.FailClose:
                raise RuntimeError(f"controlled {self.Name} close")
            if self.CrossOnClose:
                Clock.Now = 97.0

    DeadlineAt = 96.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity="child-socket-mixed"),
        _OneShotAuthority(DeadlineAt, 97.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"s" * 32,
    )
    Failure = ControlledSocket("failure", FailClose=True)
    Cross = ControlledSocket("cross", CrossOnClose=True)
    Untouched = ControlledSocket("untouched")
    Handle._ChildSocketCopies = (Failure, Cross, Untouched)
    Handle._AllocationResources = [
        ("socket", "FailureChildSocket", Failure),
        ("socket", "CrossChildSocket", Cross),
        ("socket", "UntouchedChildSocket", Untouched),
    ]
    Handle._OutstandingOwnership = True
    Handle._Receipt()
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)

    CompletedWithinAuthority = Handle._CloseChildSocketCopiesAfterStart()

    assert not CompletedWithinAuthority
    assert Actions == [
        ("failure", "close", 95.0),
        ("cross", "close", 95.0),
    ]
    assert Handle.LastReceipt.SynchronizationCleanupFailures == (
        "RuntimeError",
    )
    assert Handle.LastReceipt.OutstandingOwnership


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("Boundary", ("allocation", "child-close", "post-reap"))
def test_crossing_primitive_action_publishes_exact_parent_control(
    monkeypatch,
    Interrupt,
    Boundary,
):
    Clock = _ControlledClock(155.0)
    ParentControl = Interrupt(f"{Boundary} crossed cutoff")

    class CrossingResource:
        name = "crossing-resource"
        buf = bytearray(1_000_000)

        def fileno(self):
            return -1

        def setblocking(self, _Blocking):
            return None

        def close(self):
            Clock.Now = 157.0
            raise ParentControl

        def unlink(self):
            return None

    DeadlineAt = 156.0
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)
    if Boundary == "allocation":
        Calls = 0

        def ControlledSharedMemory(*_Arguments, **_KeywordArguments):
            nonlocal Calls
            Calls += 1
            if Calls == 2:
                Clock.Now = 157.0
                raise ParentControl
            return CrossingResource()

        monkeypatch.setattr(
            OneShotProcess,
            "SharedMemory",
            ControlledSharedMemory,
        )
        Handle = StartRuntimeOneShotProcess(
            _OneShotRequest(DeadlineAt, TaskIdentity="allocation-control-cross"),
            _OneShotAuthority(DeadlineAt, 157.0),
            b"",
            _OneShotEchoBytes,
            _OneShotLimits(_OneShotEchoBytes, b""),
        )
        FailureLedger = (Handle.LastReceipt.OperationalFailure,)
    else:
        Handle = OneShotProcess.RuntimeOneShotProcessHandle(
            _OneShotRequest(DeadlineAt, TaskIdentity=f"{Boundary}-control-cross"),
            _OneShotAuthority(DeadlineAt, 157.0),
            _OneShotLimits(_OneShotEchoBytes, b""),
            0,
            b"p" * 32,
        )
        Resource = CrossingResource()
        if Boundary == "child-close":
            Handle._ChildSocketCopies = (Resource,)
            Handle._AllocationResources = [
                ("socket", "CrossingChildSocket", Resource),
            ]
            Handle._OutstandingOwnership = True
            Handle._Receipt()
            assert not Handle._CloseChildSocketCopiesAfterStart()
            FailureLedger = (
                Handle.LastReceipt.SynchronizationCleanupFailures
            )
        else:
            Handle._RequestMemory = Resource
            Handle._AllocationResources = [
                ("memory", "RequestSharedMemory", Resource),
            ]
            Handle._Reaped = True
            Handle._ReleaseAcknowledged = False
            Handle._OutstandingOwnership = True
            Handle._Receipt()
            with pytest.raises(RuntimeError, match="cleanup cutoff"):
                Handle.CloseReleased()
            FailureLedger = Handle.LastReceipt.ResourceCleanupFailures

    assert Handle.LastReceipt.ParentControl is ParentControl
    assert Handle.LastReceipt.CleanupCutoffBreached
    assert FailureLedger
    assert Handle.LastReceipt.ResourcesClosed is False
    if Boundary == "allocation":
        BatchClock = _ControlledClock(155.0)

        def ReturnCrossedHandle(*_Arguments, **_KeywordArguments):
            BatchClock.Now = 157.0
            return Handle

        monkeypatch.setattr(SpawnedWork, "monotonic", BatchClock.Read)
        monkeypatch.setattr(SpawnedWork, "sleep", BatchClock.Sleep)
        monkeypatch.setattr(
            SpawnedWork,
            "StartRuntimeOneShotProcess",
            ReturnCrossedHandle,
        )
        Request = _Request(0, DeadlineAt=156.0)
        Item = _Item(Request, None, CleanupSeconds=1.0)
        with pytest.raises(
            SpawnedWork.RuntimeSpawnedWorkCleanupIncomplete
        ) as Caught:
            ExecuteBoundedSpawnedWorkBatch(
                (Item,),
                _EchoProduct,
                _Limits(Queued=0, InFlight=1),
            )
        assert Caught.value.__cause__ is ParentControl
        assert Caught.value.OwnedContinuations[0].Receipt.ParentControl is (
            ParentControl
        )


@pytest.mark.parametrize("Interrupt", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("Action", ("cancel", "observe", "force", "reap"))
def test_external_action_control_crossing_is_published_before_rethrow(
    monkeypatch,
    Interrupt,
    Action,
):
    Clock = _ControlledClock(165.0)
    Actions = []
    ParentControl = Interrupt(f"{Action} crossed cutoff")

    class ControlledSocket:
        def fileno(self):
            return -1

        def send(self, _Token):
            Actions.append("send")
            Clock.Now = 167.0
            raise ParentControl

        def recv(self, _Bytes):
            Actions.append("recv")
            Clock.Now = 167.0
            raise ParentControl

    class ControlledProcess:
        pid = 123
        sentinel = object()
        exitcode = 0

        def kill(self):
            Actions.append("kill")
            Clock.Now = 167.0
            raise ParentControl

        def join(self, timeout):
            Actions.append(("join", timeout))
            Clock.Now = 167.0
            raise ParentControl

    DeadlineAt = 166.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity=f"external-{Action}-cross"),
        _OneShotAuthority(DeadlineAt, 167.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"e" * 32,
    )
    Handle._Process = ControlledProcess()
    Handle._OutstandingOwnership = True
    if Action == "cancel":
        Handle._CancellationSocket = ControlledSocket()
    elif Action == "observe":
        Handle._ReadinessSocket = ControlledSocket()
    elif Action == "reap":
        Handle._ProcessExitObserved = True
    Handle._Receipt()
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)
    if Action in {"force", "reap"}:
        Handle.Observe = lambda: Handle.LastReceipt

    with pytest.raises(Interrupt) as Caught:
        if Action == "cancel":
            Handle.RequestCancellation()
        elif Action == "observe":
            Handle.Observe()
        elif Action == "force":
            Handle.ForceTerminate()
        else:
            Handle.ReapIfExited()

    assert Caught.value is ParentControl
    assert len(Actions) == 1
    assert Handle.LastReceipt.ParentControl is ParentControl
    assert Handle.LastReceipt.CleanupCutoffBreached
    assert Handle.LastReceipt.OperationalFailure is not None


def test_advance_until_at_cleanup_cutoff_does_not_observe(monkeypatch):
    Clock = _ControlledClock(80.0)
    DeadlineAt = 79.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity="advance-cutoff"),
        _OneShotAuthority(DeadlineAt, 80.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"a" * 32,
    )
    Handle._OutstandingOwnership = True
    Handle._ResourcesClosed = False
    Handle._Receipt()
    Calls = []

    def RejectObserve():
        Calls.append(Clock.Read())
        raise AssertionError("Observe must not run at cleanup cutoff")

    Handle.Observe = RejectObserve
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)

    Receipt = Handle.AdvanceUntil(80.0)

    assert Receipt is Handle.LastReceipt
    assert Receipt.CleanupCutoffBreached
    assert Receipt.CleanupCutoffObservedAt == 80.0
    assert Calls == []


def test_observe_stops_when_readiness_receive_crosses_cleanup_cutoff(
    monkeypatch,
):
    Clock = _ControlledClock(105.0)
    Actions = []

    class ControlledProcess:
        pid = 123
        sentinel = object()

    class CrossingReadinessSocket:
        def fileno(self):
            return -1

        def recv(self, _Bytes):
            Actions.append(("readiness", Clock.Read()))
            Clock.Now = 107.0
            return b"R"

    DeadlineAt = 106.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity="observe-cross"),
        _OneShotAuthority(DeadlineAt, 107.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"o" * 32,
    )
    Handle._Process = ControlledProcess()
    Handle._ReadinessSocket = CrossingReadinessSocket()
    Handle._OutstandingOwnership = True
    Handle._Receipt()
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)
    monkeypatch.setattr(
        OneShotProcess,
        "wait",
        lambda *_Arguments, **_KeywordArguments: Actions.append((
            "sentinel",
            Clock.Read(),
        )),
    )

    Receipt = Handle.Observe()

    assert Actions == [("readiness", 105.0)]
    assert Receipt.CleanupCutoffBreached
    assert not Receipt.ReadinessObserved
    assert not Receipt.ProcessExitObserved


def test_cancellation_signal_crossing_cutoff_does_not_observe_afterward(
    monkeypatch,
):
    Clock = _ControlledClock(115.0)
    Actions = []

    class CrossingCancellationSocket:
        def fileno(self):
            return -1

        def send(self, _Token):
            Actions.append(("send", Clock.Read()))
            Clock.Now = 117.0
            return 1

    DeadlineAt = 116.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity="cancel-cross"),
        _OneShotAuthority(DeadlineAt, 117.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"x" * 32,
    )
    Handle._CancellationSocket = CrossingCancellationSocket()
    Handle._OutstandingOwnership = True
    Handle._Receipt()
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)
    OriginalObserve = Handle.Observe

    def RecordObserve():
        Actions.append(("observe", Clock.Read()))
        return OriginalObserve()

    Handle.Observe = RecordObserve

    Receipt = Handle.RequestCancellation()

    assert Actions == [("send", 115.0)]
    assert Receipt.CancellationRequested
    assert Receipt.CleanupCutoffBreached


@pytest.mark.parametrize("Action", ("force", "reap"))
def test_observe_crossing_cutoff_blocks_force_and_reap_followup(
    monkeypatch,
    Action,
):
    Clock = _ControlledClock(125.0)
    Actions = []

    class ControlledProcess:
        pid = 123
        exitcode = 0

        def kill(self):
            Actions.append(("kill", Clock.Read()))

        def join(self, timeout):
            Actions.append(("join", timeout, Clock.Read()))

    DeadlineAt = 126.0
    Handle = OneShotProcess.RuntimeOneShotProcessHandle(
        _OneShotRequest(DeadlineAt, TaskIdentity=f"{Action}-cross"),
        _OneShotAuthority(DeadlineAt, 127.0),
        _OneShotLimits(_OneShotEchoBytes, b""),
        0,
        b"f" * 32,
    )
    Handle._Process = ControlledProcess()
    Handle._OutstandingOwnership = True
    if Action == "reap":
        Handle._ProcessExitObserved = True
    Handle._Receipt()
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)

    def CrossDuringObserve():
        Actions.append(("observe", Clock.Read()))
        Clock.Now = 127.0
        return Handle.LastReceipt

    Handle.Observe = CrossDuringObserve

    Receipt = (
        Handle.ForceTerminate()
        if Action == "force"
        else Handle.ReapIfExited()
    )

    assert Actions == [("observe", 125.0)]
    assert Receipt.CleanupCutoffBreached
    assert not Receipt.ForceSignalSent
    assert not Receipt.Reaped


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
        _Item(_Request(Index), (Delay, f"value-{Index}"))
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

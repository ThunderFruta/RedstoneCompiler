"""Specification-first real-child tests for owned one-shot supervision."""

from __future__ import annotations

import multiprocessing
from multiprocessing.shared_memory import SharedMemory
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import socket
from struct import pack_into
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from PhysicalDesign.Contracts.Runtime import (
    RuntimeCancellationSnapshot,
    RuntimeFreshness,
    RuntimeLifecycle,
    RuntimeWorkAuthority,
    RuntimeWorkRequest,
    RuntimeWorkScope,
)
from PhysicalDesign.Runtime.OneShotProcess import (
    RuntimeOneShotProcessLimits,
    StartRuntimeOneShotProcess,
)
import PhysicalDesign.Runtime.OneShotProcess as OneShotProcess


_REQUEST_HEADER_BYTES = 100
_RESULT_HEADER_BYTES = 88
_WITNESSES = {}
_WITNESS_SEQUENCE = 0


class _FailCloseOnceSocket(socket.socket):
    """Socket wrapper that retains its descriptor across one close fault."""

    def __init__(self, *, fileno: int, FailureType) -> None:
        super().__init__(fileno=fileno)
        self._FailureType = FailureType
        self._CloseFailed = False

    def close(self) -> None:
        if not self._CloseFailed:
            self._CloseFailed = True
            raise self._FailureType("controlled child-copy close failure")
        super().close()

    def __reduce_ex__(self, _Protocol):
        return multiprocessing.reduction._reduce_socket(self)


def _EchoBytes(Payload: bytes, _CancellationRequested) -> bytes:
    return Payload


def _WaitForCancellation(
    _Payload: bytes,
    CancellationRequested,
) -> bytes:
    while not CancellationRequested():
        sleep(0.005)
    return b"cancelled"


def _IgnoreCancellationThenEcho(
    Payload: bytes,
    _CancellationRequested,
) -> bytes:
    DelayText, Result = Payload.split(b"\x00", 1)
    sleep(float(DelayText.decode("ascii")))
    return Result


def _IgnoreCancellationUntilFixtureExpiry(
    Payload: bytes,
    _CancellationRequested,
) -> bytes:
    ExpiresAt = monotonic() + float(Payload.decode("ascii"))
    while monotonic() < ExpiresAt:
        sleep(0.05)
    return b"fixture-expired"


def _PublishEntryThenIgnoreCancellationUntilFixtureExpiry(
    Payload: bytes,
    _CancellationRequested,
) -> bytes:
    MarkerBytes, DurationBytes = Payload.split(b"\x00", 1)
    MarkerPath = Path(MarkerBytes.decode("utf-8"))
    PendingPath = MarkerPath.with_name(f".{MarkerPath.name}.pending")
    EnteredAt = monotonic()
    with PendingPath.open("x", encoding="utf-8") as File:
        File.write(json.dumps({
            "entered_at": EnteredAt,
            "pid": os.getpid(),
        }, sort_keys=True))
        File.write("\n")
        File.flush()
        os.fsync(File.fileno())
    os.replace(PendingPath, MarkerPath)
    ExpiresAt = monotonic() + float(DurationBytes.decode("ascii"))
    while monotonic() < ExpiresAt:
        sleep(0.05)
    return b"fixture-expired"


def _CrashWithoutCompletion(
    _Payload: bytes,
    _CancellationRequested,
) -> bytes:
    os._exit(17)


def _ReturnSizedResult(
    Payload: bytes,
    _CancellationRequested,
) -> bytes:
    return b"x" * int(Payload.decode("ascii"))


def _ReturnChildProcessName(
    _Payload: bytes,
    _CancellationRequested,
) -> bytes:
    return multiprocessing.current_process().name.encode("utf-8")


def _WriteEntryMarkerThenEcho(
    Payload: bytes,
    _CancellationRequested,
) -> bytes:
    Path(Payload.decode("utf-8")).write_bytes(b"entered")
    return b"entered"


def _WaitForSiblingRelease(Release) -> None:
    Release.wait()


def _OperationReferenceBytes(Operation) -> bytes:
    return f"{Operation.__module__}\x00{Operation.__qualname__}".encode("utf-8")


def _Request(
    DeadlineAt: float,
    *,
    Index: int = 0,
    TaskIdentity: str | None = None,
) -> RuntimeWorkRequest:
    return RuntimeWorkRequest(
        TaskIdentity=(
            f"one-shot-{Index}" if TaskIdentity is None else TaskIdentity
        ),
        Operation="encoded-one-shot-test",
        Scope=RuntimeWorkScope(
            DomainIdentity=f"one-shot-domain-{Index}",
            DependencyIdentities=("fixture-v1",),
        ),
        Lifecycle=RuntimeLifecycle.Queued,
        Freshness=RuntimeFreshness.Current,
        DeadlineAt=DeadlineAt,
        WorkCap=1,
        Cancellation=RuntimeCancellationSnapshot(
            Requested=False,
            Identity=f"one-shot-cancel-{Index}",
        ),
    )


def _Authority(
    WorkDeadlineAt: float,
    CleanupCutoffAt: float,
    *,
    Force: bool = True,
) -> RuntimeWorkAuthority:
    return RuntimeWorkAuthority(
        WorkDeadlineAt=WorkDeadlineAt,
        CleanupCutoffAt=CleanupCutoffAt,
        ForceTerminationAuthorized=Force,
    )


def _Limits(
    Operation,
    Payload: bytes,
    *,
    ResultCapacity: int = 1024,
    ExtraRequestBytes: int = 0,
) -> RuntimeOneShotProcessLimits:
    return RuntimeOneShotProcessLimits(
        MaximumRequestBytes=(
            _REQUEST_HEADER_BYTES
            + len(_OperationReferenceBytes(Operation))
            + len(Payload)
            + ExtraRequestBytes
        ),
        MaximumResultBytes=ResultCapacity,
    )


def _PidExists(Pid: int | None) -> bool:
    if Pid is None:
        return False
    try:
        os.kill(Pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _OpenSocketInodes() -> frozenset[int]:
    Inodes = set()
    for Descriptor in Path("/proc/self/fd").iterdir():
        try:
            Target = os.readlink(Descriptor)
        except (FileNotFoundError, OSError):
            continue
        if Target.startswith("socket:[") and Target.endswith("]"):
            Inodes.add(int(Target[8:-1]))
    return frozenset(Inodes)


def _NamedSemaphoreResources() -> frozenset[str]:
    return frozenset(
        PathValue.name
        for PathValue in Path("/dev/shm").glob("sem.*")
    )


def _SharedMemoryResources() -> frozenset[str]:
    return frozenset(
        PathValue.name
        for PathValue in Path("/dev/shm").glob("psm_*")
    )


def _PidStartTicks(Pid: int | None) -> int | None:
    if Pid is None:
        return None
    try:
        Stat = Path(f"/proc/{Pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    ClosingParenthesis = Stat.rfind(")")
    FieldsAfterCommand = Stat[ClosingParenthesis + 2:].split()
    return int(FieldsAfterCommand[19])


def _PidMatchesWitness(Witness) -> bool:
    Pid = Witness["pid"]
    StartTicks = Witness["pid_start_ticks"]
    if Pid is None or StartTicks is None:
        return False
    return _PidStartTicks(Pid) == StartTicks


def _NamespaceIdentities() -> dict[str, str]:
    return {
        Name: os.readlink(f"/proc/self/ns/{Name}")
        for Name in ("pid", "mnt", "ipc", "net")
    }


def _AppendWitness(Event) -> None:
    Ledger = os.environ.get("RC_ONESHOT_WITNESS_LEDGER")
    if Ledger is None:
        return
    with Path(Ledger).open("a", encoding="utf-8") as File:
        File.write(json.dumps(Event, sort_keys=True) + "\n")
        File.flush()
        os.fsync(File.fileno())


def _StartOwned(*Arguments):
    global _WITNESS_SEQUENCE
    _WITNESS_SEQUENCE += 1
    Request = Arguments[0]
    Witness = {
        "witness_id": f"handle-{_WITNESS_SEQUENCE}",
        "kind": "handle",
        "task": (
            Request.TaskIdentity
            if len(Request.TaskIdentity) <= 128
            else f"sha256:{sha256(Request.TaskIdentity.encode()).hexdigest()}"
        ),
        "task_length": len(Request.TaskIdentity),
        "pid": None,
        "pid_start_ticks": None,
        "request_shm": None,
        "result_shm": None,
        "sync_inodes": [],
        "namespaces": _NamespaceIdentities(),
        "closed": False,
    }
    OriginalSharedMemory = OneShotProcess.SharedMemory
    OriginalSocketPair = OneShotProcess.socket.socketpair
    OriginalStart = multiprocessing.context.SpawnProcess.start
    AllocatedNames = []

    def ObservedSharedMemory(*MemoryArguments, **MemoryKeywords):
        Memory = OriginalSharedMemory(*MemoryArguments, **MemoryKeywords)
        if MemoryKeywords.get("create") is True:
            AllocatedNames.append(Memory.name)
            if len(AllocatedNames) >= 1:
                Witness["request_shm"] = AllocatedNames[0]
            if len(AllocatedNames) >= 2:
                Witness["result_shm"] = AllocatedNames[1]
            _AppendWitness({"event": "allocated", **Witness})
        return Memory

    def ObservedSocketPair(*SocketArguments, **SocketKeywords):
        Pair = OriginalSocketPair(*SocketArguments, **SocketKeywords)
        Witness["sync_inodes"].extend(
            os.fstat(Value.fileno()).st_ino for Value in Pair
        )
        _AppendWitness({"event": "sync-allocated", **Witness})
        return Pair

    def RecordStarted(Process) -> None:
        try:
            Pid = Process.pid
        except (AssertionError, ValueError):
            return
        if Pid is None or Witness["pid"] is not None:
            return
        Witness["pid"] = Pid
        Witness["pid_start_ticks"] = _PidStartTicks(Pid)
        _AppendWitness({"event": "started", **Witness})

    def ObservedStart(Process):
        StopPolling = Event()

        def PollPublicPid() -> None:
            while not StopPolling.wait(0.001):
                RecordStarted(Process)
                if Witness["pid"] is not None:
                    return

        Poller = Thread(target=PollPublicPid, daemon=True)
        Poller.start()
        try:
            return OriginalStart(Process)
        finally:
            RecordStarted(Process)
            StopPolling.set()
            Poller.join(timeout=0.1)

    OneShotProcess.SharedMemory = ObservedSharedMemory
    OneShotProcess.socket.socketpair = ObservedSocketPair
    multiprocessing.context.SpawnProcess.start = ObservedStart
    try:
        Handle = StartRuntimeOneShotProcess(*Arguments)
    finally:
        OneShotProcess.SharedMemory = OriginalSharedMemory
        OneShotProcess.socket.socketpair = OriginalSocketPair
        multiprocessing.context.SpawnProcess.start = OriginalStart
    _WITNESSES[id(Handle)] = Witness
    if not AllocatedNames:
        _AppendWitness({"event": "owned", **Witness})
    return Handle


def _InjectChildSocketCloseFailure(monkeypatch, FailureType):
    OriginalSocketPair = OneShotProcess.socket.socketpair
    State = {"pair_count": 0, "retained_inode": None}

    def FaultingSocketPair(*Arguments, **KeywordArguments):
        Pair = OriginalSocketPair(*Arguments, **KeywordArguments)
        State["pair_count"] += 1
        if State["pair_count"] != 1:
            return Pair
        ParentSocket, ChildSocket = Pair
        FaultingChild = _FailCloseOnceSocket(
            fileno=ChildSocket.detach(),
            FailureType=FailureType,
        )
        State["retained_inode"] = os.fstat(FaultingChild.fileno()).st_ino
        return ParentSocket, FaultingChild

    monkeypatch.setattr(
        OneShotProcess.socket,
        "socketpair",
        FaultingSocketPair,
    )
    return State


def _RegisterSibling(Process, Task: str):
    global _WITNESS_SEQUENCE
    _WITNESS_SEQUENCE += 1
    Witness = {
        "witness_id": f"sibling-{_WITNESS_SEQUENCE}",
        "kind": "sibling",
        "task": Task,
        "pid": Process.pid,
        "pid_start_ticks": _PidStartTicks(Process.pid),
        "request_shm": None,
        "result_shm": None,
        "sync_inodes": [],
        "namespaces": _NamespaceIdentities(),
        "closed": False,
    }
    _WITNESSES[id(Process)] = Witness
    _AppendWitness({"event": "owned", **Witness})


def _ArmHandleWitness(Handle, Receipt) -> None:
    Witness = _WITNESSES[id(Handle)]
    Witness["pid"] = Receipt.StartedPid
    Witness["pid_start_ticks"] = _PidStartTicks(Receipt.StartedPid)
    Witness["request_shm"] = Receipt.RequestSharedMemoryName
    Witness["result_shm"] = Receipt.ResultSharedMemoryName
    Witness["sync_inodes"] = list(
        Receipt.SynchronizationResourceIdentities
    )
    Witness["armed"] = True
    _AppendWitness({
        "event": "armed",
        "readiness_observed": Receipt.ReadinessObserved,
        **Witness,
    })


def _UnlinkWitnessMemory(Name: str | None) -> None:
    if Name is None:
        return
    SharedMemoryPath = Path("/dev/shm") / Name.lstrip("/")
    try:
        SharedMemoryPath.unlink()
    except FileNotFoundError:
        pass


def _IndependentCloseWitness(Owner, *, UsedFallback: bool) -> None:
    Witness = _WITNESSES.get(id(Owner))
    if Witness is None or Witness["closed"]:
        return
    Pid = Witness["pid"]
    if _PidMatchesWitness(Witness):
        try:
            os.kill(Pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        EndAt = monotonic() + 2.0
        while _PidMatchesWitness(Witness) and monotonic() < EndAt:
            try:
                os.waitpid(Pid, os.WNOHANG)
            except ChildProcessError:
                pass
            sleep(0.005)
    _UnlinkWitnessMemory(Witness["request_shm"])
    _UnlinkWitnessMemory(Witness["result_shm"])
    assert not _PidMatchesWitness(Witness)
    for Name in (Witness["request_shm"], Witness["result_shm"]):
        if Name is not None:
            assert not (Path("/dev/shm") / Name.lstrip("/")).exists()
    assert set(Witness["sync_inodes"]).isdisjoint(_OpenSocketInodes())
    Witness["closed"] = True
    _AppendWitness({
        "event": "closed",
        "used_fallback": UsedFallback,
        **Witness,
    })


def _PollFor(Observe, Predicate, Seconds: float = 3.0):
    EndAt = monotonic() + Seconds
    Last = None
    while monotonic() < EndAt:
        Last = Observe()
        if Predicate(Last):
            return Last
        sleep(0.005)
    return Last


def _AssertSharedMemoryAbsent(Name: str | None) -> None:
    if Name is None:
        return
    with pytest.raises(FileNotFoundError):
        SharedMemory(name=Name)


def _RecoverAndClose(Handle) -> None:
    """Bounded exact-handle cleanup for a test that did not finish normally."""
    UsedFallback = False
    try:
        Receipt = Handle.Observe()
        if Receipt.ResourcesClosed:
            _IndependentCloseWitness(Handle, UsedFallback=False)
            return
        if Receipt.OutstandingOwnership and not Receipt.ProcessExitObserved:
            Receipt = Handle.RequestCancellation()
            Receipt = _PollFor(
                Handle.Observe,
                lambda Value: Value.ProcessExitObserved,
                Seconds=0.5,
            )
        if (
            Receipt is not None
            and Receipt.OutstandingOwnership
            and not Receipt.ProcessExitObserved
            and Receipt.ForceTerminationAuthorized
        ):
            Handle.ForceTerminate()
            Receipt = _PollFor(
                Handle.Observe,
                lambda Value: Value.ProcessExitObserved,
                Seconds=2.0,
            )
        if Receipt is not None and Receipt.ProcessExitObserved:
            Receipt = Handle.ReapIfExited()
        if (
            Receipt is not None
            and (
                Receipt.Reaped
                or not Receipt.OutstandingOwnership
            )
            and not Receipt.ChildExistenceUncertain
        ):
            Handle.CloseReleased()
    except BaseException:
        UsedFallback = True
    finally:
        Witness = _WITNESSES.get(id(Handle))
        if Witness is not None and (
            _PidMatchesWitness(Witness)
            or any(
                Name is not None
                and (Path("/dev/shm") / Name.lstrip("/")).exists()
                for Name in (Witness["request_shm"], Witness["result_shm"])
            )
            or not set(Witness["sync_inodes"]).isdisjoint(_OpenSocketInodes())
        ):
            UsedFallback = True
        _IndependentCloseWitness(Handle, UsedFallback=UsedFallback)


def _ReapAndClose(Handle):
    Receipt = _PollFor(
        Handle.Observe,
        lambda Value: Value.ProcessExitObserved,
        Seconds=3.0,
    )
    assert Receipt is not None and Receipt.ProcessExitObserved
    Receipt = Handle.ReapIfExited()
    assert Receipt.Reaped
    assert not Receipt.ReleaseAcknowledged
    assert Receipt.ReleaseAcknowledgedAt is None
    assert Receipt.OutstandingOwnership
    assert not Receipt.ResourcesClosed
    Pid = Receipt.StartedPid
    RequestName = Receipt.RequestSharedMemoryName
    ResultName = Receipt.ResultSharedMemoryName
    Handle.CloseReleased()
    Receipt = Handle.LastReceipt
    assert Receipt.ReleaseAcknowledged
    assert not Receipt.OutstandingOwnership
    assert Receipt.ResourcesClosed
    assert not _PidExists(Pid)
    _AssertSharedMemoryAbsent(RequestName)
    _AssertSharedMemoryAbsent(ResultName)
    _IndependentCloseWitness(Handle, UsedFallback=False)
    print(
        "ONE_SHOT_RESOURCE"
        f" task_length={len(Receipt.TaskIdentity)}"
        f" task_sha256={sha256(Receipt.TaskIdentity.encode()).hexdigest()}"
        f" pid={Pid}"
        f" request_shm={RequestName}"
        f" result_shm={ResultName}"
        f" exit_code={Receipt.ExitCode}"
        f" reaped={Receipt.Reaped}"
        f" released={Receipt.ReleaseAcknowledged}"
        " pid_absent=True request_absent=True result_absent=True"
    )
    return Receipt


def _CloseAlreadyReaped(Handle, Receipt) -> None:
    assert Receipt.Reaped
    assert not Receipt.ReleaseAcknowledged
    assert Receipt.ReleaseAcknowledgedAt is None
    assert Receipt.OutstandingOwnership
    assert not Receipt.ResourcesClosed
    Pid = Receipt.StartedPid
    RequestName = Receipt.RequestSharedMemoryName
    ResultName = Receipt.ResultSharedMemoryName
    Handle.CloseReleased()
    Released = Handle.LastReceipt
    assert Released.ReleaseAcknowledged
    assert not Released.OutstandingOwnership
    assert Released.ResourcesClosed
    assert not _PidExists(Pid)
    _AssertSharedMemoryAbsent(RequestName)
    _AssertSharedMemoryAbsent(ResultName)
    _IndependentCloseWitness(Handle, UsedFallback=False)


def test_limits_are_exact_total_envelope_capacities():
    with pytest.raises(ValueError):
        RuntimeOneShotProcessLimits(
            MaximumRequestBytes=_REQUEST_HEADER_BYTES - 1,
            MaximumResultBytes=_RESULT_HEADER_BYTES,
        )
    with pytest.raises(ValueError):
        RuntimeOneShotProcessLimits(
            MaximumRequestBytes=_REQUEST_HEADER_BYTES,
            MaximumResultBytes=_RESULT_HEADER_BYTES - 1,
        )
    with pytest.raises(TypeError):
        RuntimeOneShotProcessLimits(
            MaximumRequestBytes=True,
            MaximumResultBytes=_RESULT_HEADER_BYTES,
        )


@pytest.mark.parametrize("Field", ("MaximumRequestBytes", "MaximumResultBytes"))
def test_capacity_int_subclasses_are_rejected_without_invoking_hooks(Field):
    class BudgetInt(int):
        Calls = {
            "reduce": 0,
            "int": 0,
            "index": 0,
            "compare": 0,
        }

        def __new__(cls, Value):
            Instance = super().__new__(cls, Value)
            Instance.Hidden = b"x" * 4096
            return Instance

        def __reduce_ex__(self, Protocol):
            type(self).Calls["reduce"] += 1
            return super().__reduce_ex__(Protocol)

        def __int__(self):
            type(self).Calls["int"] += 1
            return super().__int__()

        def __index__(self):
            type(self).Calls["index"] += 1
            return super().__index__()

        def __lt__(self, Other):
            type(self).Calls["compare"] += 1
            return super().__lt__(Other)

    Before = (
        _NamedSemaphoreResources(),
        _OpenSocketInodes(),
        _SharedMemoryResources(),
    )
    Arguments = {
        "MaximumRequestBytes": _REQUEST_HEADER_BYTES,
        "MaximumResultBytes": _RESULT_HEADER_BYTES,
    }
    Arguments[Field] = BudgetInt(Arguments[Field])

    with pytest.raises(TypeError):
        RuntimeOneShotProcessLimits(**Arguments)

    assert BudgetInt.Calls == {
        "reduce": 0,
        "int": 0,
        "index": 0,
        "compare": 0,
    }
    assert (
        _NamedSemaphoreResources(),
        _OpenSocketInodes(),
        _SharedMemoryResources(),
    ) == Before


def test_new_boundary_rejects_non_float_and_mismatched_deadlines():
    IntegerRequest = _Request(123)
    Limits = _Limits(_EchoBytes, b"")

    with pytest.raises(TypeError):
        StartRuntimeOneShotProcess(
            IntegerRequest,
            _Authority(123.0, 124.0),
            b"",
            _EchoBytes,
            Limits,
        )

    StartedAt = monotonic()
    with pytest.raises(ValueError):
        StartRuntimeOneShotProcess(
            _Request(StartedAt + 1.0),
            _Authority(StartedAt + 1.1, StartedAt + 2.0),
            b"",
            _EchoBytes,
            Limits,
        )
    with pytest.raises(ValueError):
        StartRuntimeOneShotProcess(
            _Request(StartedAt + 1.0),
            _Authority(StartedAt + 1.0, StartedAt + 0.9),
            b"",
            _EchoBytes,
            Limits,
        )


def test_expired_work_is_exact_unstarted_without_allocation():
    DeadlineAt = monotonic() - 1.0
    Payload = b"never-start"
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload),
    )

    Receipt = Handle.Observe()
    assert Receipt.ExactUnstarted
    assert Receipt.UnstartedReason == "WorkDeadlineExpired"
    assert not Receipt.StartRequested
    assert Receipt.StartedPid is None
    assert Receipt.RequestSharedMemoryName is None
    assert Receipt.ResultSharedMemoryName is None
    assert Receipt.SynchronizationResourceIdentities == ()
    assert not Receipt.OutstandingOwnership
    assert Receipt.PublishedResult is None
    print(
        "ONE_SHOT_UNSTARTED task=one-shot-0 pid=None request_shm=None"
        " result_shm=None outstanding=False"
    )
    Handle.CloseReleased()
    _IndependentCloseWitness(Handle, UsedFallback=False)


def test_equal_cutoffs_and_zero_byte_envelopes_succeed():
    Payload = b""
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt),
        Payload,
        _EchoBytes,
        _Limits(
            _EchoBytes,
            Payload,
            ResultCapacity=_RESULT_HEADER_BYTES,
        ),
    )
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        Receipt = _ReapAndClose(Handle)
        assert Receipt.RequestEnvelopeBytes == (
            _REQUEST_HEADER_BYTES + len(_OperationReferenceBytes(_EchoBytes))
        )
        assert Receipt.ResultEnvelopeBytesObserved == _RESULT_HEADER_BYTES
        assert Receipt.ResultPayloadBytesObserved == 0
        assert Receipt.PublishedResult == b""
    finally:
        _RecoverAndClose(Handle)


def test_request_total_boundary_counts_header_operation_and_payload():
    Payload = b"request-boundary"
    ReferenceBytes = _OperationReferenceBytes(_EchoBytes)
    ExactTotal = _REQUEST_HEADER_BYTES + len(ReferenceBytes) + len(Payload)
    DeadlineAt = monotonic() + 3.0
    Authority = _Authority(DeadlineAt, DeadlineAt + 1.0)

    with pytest.raises(ValueError):
        StartRuntimeOneShotProcess(
            _Request(DeadlineAt),
            Authority,
            Payload,
            _EchoBytes,
            RuntimeOneShotProcessLimits(
                MaximumRequestBytes=ExactTotal - 1,
                MaximumResultBytes=_RESULT_HEADER_BYTES + len(Payload),
            ),
        )

    Handle = _StartOwned(
        _Request(DeadlineAt),
        Authority,
        Payload,
        _EchoBytes,
        RuntimeOneShotProcessLimits(
            MaximumRequestBytes=ExactTotal,
            MaximumResultBytes=_RESULT_HEADER_BYTES + len(Payload),
        ),
    )
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        Receipt = _ReapAndClose(Handle)
        assert Receipt.RequestEnvelopeBytes == ExactTotal
        assert Receipt.PublishedResult == Payload
    finally:
        _RecoverAndClose(Handle)


def test_unbounded_parent_task_identity_is_not_spawn_transport_metadata():
    TaskIdentity = "parent-task:" + ("x" * (1024 * 1024))
    Payload = b""
    ExpectedChildName = b"RuntimeOneShot"
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity=TaskIdentity),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _ReturnChildProcessName,
        _Limits(
            _ReturnChildProcessName,
            Payload,
            ResultCapacity=_RESULT_HEADER_BYTES + len(ExpectedChildName),
        ),
    )
    try:
        Handle.AdvanceUntil(float(DeadlineAt))
        Receipt = _ReapAndClose(Handle)
        assert Receipt.TaskIdentity == TaskIdentity
        assert Receipt.PublishedResult == ExpectedChildName
    finally:
        _RecoverAndClose(Handle)


def test_result_total_boundary_accepts_exact_and_rejects_plus_one():
    for Index, ExtraResultByte, Expected in (
        (0, False, b"result-boundary"),
        (1, True, None),
    ):
        Payload = b"result-boundary" + (b"x" if ExtraResultByte else b"")
        DeadlineAt = monotonic() + 3.0
        Handle = _StartOwned(
            _Request(DeadlineAt, Index=Index),
            _Authority(DeadlineAt, DeadlineAt + 1.0),
            Payload,
            _EchoBytes,
            _Limits(
                _EchoBytes,
                Payload,
                ResultCapacity=_RESULT_HEADER_BYTES + len(b"result-boundary"),
            ),
        )
        try:
            Handle.AdvanceUntil(float(DeadlineAt))
            Receipt = _ReapAndClose(Handle)
            assert Receipt.ResultEnvelopeBytesObserved == (
                _RESULT_HEADER_BYTES + len(Payload)
            )
            assert Receipt.PublishedResult == Expected
            if ExtraResultByte:
                assert not Receipt.ResultValid
                assert Receipt.ResultDiagnostic == "ResultLimitExceeded"
            else:
                assert Receipt.ResultValid
        finally:
            _RecoverAndClose(Handle)


def test_complete_foreign_result_frames_are_rejected_by_both_handles():
    Payload = b"binding-result"
    DeadlineAt = monotonic() + 4.0
    Authority = _Authority(DeadlineAt, DeadlineAt + 1.0)
    Limits = _Limits(
        _EchoBytes,
        Payload,
        ResultCapacity=_RESULT_HEADER_BYTES + len(Payload),
    )
    Handles = []
    try:
        for Index in range(2):
            Handles.append(_StartOwned(
                _Request(
                    DeadlineAt,
                    Index=Index,
                    TaskIdentity=f"foreign-frame-{Index}",
                ),
                Authority,
                Payload,
                _EchoBytes,
                Limits,
            ))
        Terminal = [
            Handle.AdvanceUntil(float(DeadlineAt))
            for Handle in Handles
        ]
        assert all(Value.ProcessExitObserved for Value in Terminal)
        assert all(Value.ResultCompletionObserved for Value in Terminal)

        Memories = [
            SharedMemory(name=Value.ResultSharedMemoryName)
            for Value in Terminal
        ]
        try:
            First = bytes(Memories[0].buf[:])
            Second = bytes(Memories[1].buf[:])
            Memories[0].buf[:] = Second
            Memories[1].buf[:] = First
        finally:
            for Memory in Memories:
                Memory.close()

        Reaped = [Handle.ReapIfExited() for Handle in Handles]
        assert all(Value.PublishedResult is None for Value in Reaped)
        assert all(not Value.ResultValid for Value in Reaped)
        assert all(
            Value.ResultDiagnostic == "ResultBindingInvalid"
            for Value in Reaped
        )
        assert len({Value.InvocationBindingIdentity for Value in Reaped}) == 2
        assert all(
            len(Value.InvocationBindingIdentity) == 64
            for Value in Reaped
        )
        for Handle, Receipt in zip(Handles, Reaped):
            _CloseAlreadyReaped(Handle, Receipt)
    finally:
        for Handle in Handles:
            _RecoverAndClose(Handle)


def test_result_binding_valid_control_publishes_exact_bytes():
    Payload = b"binding-control"
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity="binding-valid-control"),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _EchoBytes,
        _Limits(
            _EchoBytes,
            Payload,
            ResultCapacity=_RESULT_HEADER_BYTES + len(Payload),
        ),
    )
    try:
        Handle.AdvanceUntil(float(DeadlineAt))
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ResultValid
        assert Receipt.PublishedResult == Payload
        assert len(Receipt.InvocationBindingIdentity) == 64
    finally:
        _RecoverAndClose(Handle)


def test_real_child_readiness_precedes_cooperative_cancellation():
    DeadlineAt = monotonic() + 2.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0, Force=False),
        b"",
        _WaitForCancellation,
        _Limits(_WaitForCancellation, b""),
    )
    try:
        Receipt = _PollFor(
            Handle.Observe,
            lambda Value: Value.ReadinessObserved,
        )
        assert Receipt is not None and Receipt.ReadinessObserved
        assert Receipt.StartRequested
        assert Receipt.StartedPid is not None
        assert _PidExists(Receipt.StartedPid)
        assert Receipt.OutstandingOwnership

        Receipt = Handle.RequestCancellation()
        assert Receipt.CancellationRequested
        assert Receipt.OutstandingOwnership
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ExitCode == 0
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_success_requires_terminal_observation_reap_and_release():
    Payload = b"publish-only-after-reap"
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _EchoBytes,
        _Limits(
            _EchoBytes,
            Payload,
            ResultCapacity=_RESULT_HEADER_BYTES + len(Payload),
        ),
    )
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        assert Receipt.ResultCompletionObserved
        assert Receipt.PublishedResult is None
        assert not Receipt.Reaped
        assert not Receipt.ReleaseAcknowledged
        assert Receipt.OutstandingOwnership

        Receipt = _ReapAndClose(Handle)
        assert Receipt.ResultValid
        assert Receipt.ExitCode == 0
        assert Receipt.PublishedResult == Payload
        assert not Receipt.OutstandingOwnership
    finally:
        _RecoverAndClose(Handle)


def test_work_expiry_is_cooperative_and_late_result_is_not_published():
    DeadlineAt = monotonic() + 0.25
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0, Force=False),
        b"",
        _WaitForCancellation,
        _Limits(_WaitForCancellation, b""),
    )
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt + 1.0))
        assert Receipt.ProcessExitObserved
        Receipt = _ReapAndClose(Handle)
        assert Receipt.WorkDeadlineObserved
        assert Receipt.CancellationRequested
        assert Receipt.ResultCompletionObserved
        assert Receipt.ResultValid
        assert Receipt.ResultDiagnostic == "LateResultDiscarded"
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_complete_late_output_keeps_only_diagnostics():
    Payload = b"0.30\x00late-value"
    DeadlineAt = monotonic() + 0.15
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0, Force=False),
        Payload,
        _IgnoreCancellationThenEcho,
        _Limits(_IgnoreCancellationThenEcho, Payload),
    )
    try:
        Handle.AdvanceUntil(float(DeadlineAt + 1.0))
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ExitCode == 0
        assert Receipt.ResultValid
        assert Receipt.ResultPayloadBytesObserved == len(b"late-value")
        assert Receipt.ResultDiagnostic == "LateResultDiscarded"
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_abnormal_exit_without_completion_never_blocks_or_publishes():
    DeadlineAt = monotonic() + 2.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        b"",
        _CrashWithoutCompletion,
        _Limits(_CrashWithoutCompletion, b""),
    )
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        assert Receipt.ExitCode == 17
        assert not Receipt.ResultCompletionObserved
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ResultDiagnostic == "CompletionNotObserved"
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_oversized_cancellation_datagram_is_not_a_valid_token(monkeypatch):
    OriginalSocketPair = OneShotProcess.socket.socketpair
    Pairs = []

    def CaptureSocketPair(*Arguments, **KeywordArguments):
        Pair = OriginalSocketPair(*Arguments, **KeywordArguments)
        Pairs.append(Pair)
        return Pair

    monkeypatch.setattr(OneShotProcess.socket, "socketpair", CaptureSocketPair)
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity="oversized-cancellation-token"),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        b"",
        _WaitForCancellation,
        _Limits(_WaitForCancellation, b""),
    )
    monkeypatch.undo()
    try:
        Receipt = _PollFor(
            Handle.Observe,
            lambda Value: Value.ReadinessObserved,
        )
        assert Receipt is not None and Receipt.ReadinessObserved
        assert len(Pairs) == 3
        assert Pairs[1][0].send(b"CX") == 2
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ExitCode not in (None, 0)
        assert not Receipt.ResultCompletionObserved
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_empty_cancellation_datagram_is_not_no_signal(monkeypatch):
    OriginalSocketPair = OneShotProcess.socket.socketpair
    Pairs = []

    def CaptureSocketPair(*Arguments, **KeywordArguments):
        Pair = OriginalSocketPair(*Arguments, **KeywordArguments)
        Pairs.append(Pair)
        return Pair

    monkeypatch.setattr(OneShotProcess.socket, "socketpair", CaptureSocketPair)
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity="empty-cancellation-token"),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        b"",
        _WaitForCancellation,
        _Limits(_WaitForCancellation, b""),
    )
    monkeypatch.undo()
    try:
        Receipt = _PollFor(
            Handle.Observe,
            lambda Value: Value.ReadinessObserved,
        )
        assert Receipt is not None and Receipt.ReadinessObserved
        assert len(Pairs) == 3
        assert Pairs[1][0].send(b"") == 0
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ExitCode not in (None, 0)
        assert not Receipt.CancellationRequested
        assert not Receipt.ResultCompletionObserved
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_oversized_completion_datagram_is_not_a_valid_token(monkeypatch):
    OriginalSocketPair = OneShotProcess.socket.socketpair
    CompletionSender = None
    PairCount = 0

    def CaptureSocketPair(*Arguments, **KeywordArguments):
        nonlocal CompletionSender, PairCount
        Pair = OriginalSocketPair(*Arguments, **KeywordArguments)
        PairCount += 1
        if PairCount == 3:
            CompletionSender = OneShotProcess.socket.socket(
                fileno=os.dup(Pair[1].fileno())
            )
            CompletionSender.setblocking(False)
        return Pair

    monkeypatch.setattr(OneShotProcess.socket, "socketpair", CaptureSocketPair)
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity="oversized-completion-token"),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        b"",
        _CrashWithoutCompletion,
        _Limits(_CrashWithoutCompletion, b""),
    )
    monkeypatch.undo()
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        assert not Receipt.ResultCompletionObserved
        assert CompletionSender is not None
        assert CompletionSender.send(b"DX") == 2
        CompletionSender.close()
        CompletionSender = None

        Receipt = Handle.Observe()
        assert not Receipt.ResultCompletionObserved
        assert Receipt.OperationalFailure == "CompletionSignalInvalid"
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ResultDiagnostic == "CompletionNotObserved"
        assert Receipt.PublishedResult is None
    finally:
        if CompletionSender is not None:
            CompletionSender.close()
        _RecoverAndClose(Handle)


def test_release_timestamp_follows_bounded_validation_reap_and_close():
    ResultBytes = 32 * 1024 * 1024
    Payload = str(ResultBytes).encode("ascii")
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 2.0),
        Payload,
        _ReturnSizedResult,
        _Limits(
            _ReturnSizedResult,
            Payload,
            ResultCapacity=_RESULT_HEADER_BYTES + ResultBytes,
        ),
    )
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        assert Receipt.ResultCompletionObserved

        TargetAt = DeadlineAt - 0.003
        while True:
            Remaining = TargetAt - monotonic()
            if Remaining <= 0:
                break
            if Remaining > 0.010:
                sleep(Remaining - 0.005)

        ReapStartedAt = monotonic()
        assert ReapStartedAt < DeadlineAt
        Receipt = Handle.ReapIfExited()
        ReapReturnedAt = monotonic()

        assert DeadlineAt <= ReapReturnedAt
        assert Receipt.Reaped
        assert not Receipt.ReleaseAcknowledged
        assert Receipt.ReleaseAcknowledgedAt is None
        assert Receipt.OutstandingOwnership
        assert not Receipt.ResourcesClosed
        assert Receipt.ResultValid
        assert Receipt.ResultDiagnostic == "LateResultDiscarded"
        assert Receipt.PublishedResult is None

        Pid = Receipt.StartedPid
        RequestName = Receipt.RequestSharedMemoryName
        ResultName = Receipt.ResultSharedMemoryName
        Handle.CloseReleased()
        Released = Handle.LastReceipt
        assert Released.ReleaseAcknowledged
        assert Released.ReleaseAcknowledgedAt is not None
        assert Released.ReleaseAcknowledgedAt >= ReapReturnedAt
        assert not Released.OutstandingOwnership
        assert Released.ResourcesClosed
        assert not _PidExists(Pid)
        _AssertSharedMemoryAbsent(RequestName)
        _AssertSharedMemoryAbsent(ResultName)
        _IndependentCloseWitness(Handle, UsedFallback=False)
        print(
            "ONE_SHOT_RESOURCE"
            f" task={Receipt.TaskIdentity} pid={Pid}"
            f" request_shm={RequestName} result_shm={ResultName}"
            f" exit_code={Receipt.ExitCode} reaped=True released=True"
            " pid_absent=True request_absent=True result_absent=True"
            f" reap_started={ReapStartedAt:.9f}"
            f" deadline={DeadlineAt:.9f}"
            f" reap_returned={ReapReturnedAt:.9f}"
        )
    finally:
        _RecoverAndClose(Handle)


@pytest.mark.parametrize(
    ("Mutation", "ExpectedDiagnostic"),
    (
        ("magic", "ResultMagicInvalid"),
        ("version", "ResultVersionInvalid"),
        ("reserved", "ResultReservedBytesInvalid"),
        ("length", "ResultLimitExceeded"),
        ("digest", "ResultDigestInvalid"),
        ("payload", "ResultDigestInvalid"),
        ("padding", "ResultPaddingInvalid"),
    ),
)
def test_public_terminal_result_corruption_fails_closed(
    Mutation,
    ExpectedDiagnostic,
):
    Payload = b"corruption-target"
    ResultCapacity = _RESULT_HEADER_BYTES + len(Payload) + 4
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload, ResultCapacity=ResultCapacity),
    )
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        assert Receipt.ResultCompletionObserved
        assert Receipt.ResultSharedMemoryName is not None

        Memory = SharedMemory(name=Receipt.ResultSharedMemoryName)
        try:
            if Mutation == "magic":
                Memory.buf[0] ^= 1
            elif Mutation == "version":
                Memory.buf[8] ^= 1
            elif Mutation == "reserved":
                Memory.buf[9] = 1
            elif Mutation == "length":
                pack_into("!Q", Memory.buf, 16, ResultCapacity)
            elif Mutation == "digest":
                Memory.buf[56] ^= 1
            elif Mutation == "payload":
                Memory.buf[_RESULT_HEADER_BYTES] ^= 1
            elif Mutation == "padding":
                Memory.buf[_RESULT_HEADER_BYTES + len(Payload)] = 1
        finally:
            Memory.close()

        Receipt = _ReapAndClose(Handle)
        assert Receipt.ResultAvailable
        assert not Receipt.ResultValid
        assert Receipt.ResultDiagnostic == ExpectedDiagnostic
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_force_denial_retains_child_and_does_not_touch_sibling():
    Context = multiprocessing.get_context("spawn")
    SiblingRelease = Context.Event()
    Sibling = Context.Process(target=_WaitForSiblingRelease, args=(SiblingRelease,))
    Sibling.start()
    _RegisterSibling(Sibling, "force-denied-isolation")
    SiblingPid = Sibling.pid
    DeadlineAt = monotonic() + 2.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0, Force=False),
        b"",
        _WaitForCancellation,
        _Limits(_WaitForCancellation, b""),
    )
    try:
        Receipt = _PollFor(
            Handle.Observe,
            lambda Value: Value.ReadinessObserved,
        )
        assert Receipt is not None and Receipt.ReadinessObserved
        OwnedPid = Receipt.StartedPid

        Receipt = Handle.ForceTerminate()
        assert Receipt.ForceTerminationRequested
        assert Receipt.ForceTerminationDenied
        assert not Receipt.ForceSignalSent
        assert Receipt.OutstandingOwnership
        assert _PidExists(OwnedPid)
        assert Sibling.is_alive()

        Handle.RequestCancellation()
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ExitCode == 0
        assert Sibling.is_alive()
    finally:
        _RecoverAndClose(Handle)
        SiblingRelease.set()
        Sibling.join(timeout=2.0)
        if Sibling.is_alive():
            Sibling.kill()
            Sibling.join(timeout=2.0)
        assert not _PidExists(SiblingPid)
        print(
            f"ONE_SHOT_SIBLING pid={SiblingPid}"
            " survived_owned_action=True pid_absent_after_release=True"
        )
        _IndependentCloseWitness(Sibling, UsedFallback=False)
        Sibling.close()


def test_explicit_authorized_force_kills_only_owned_child():
    Context = multiprocessing.get_context("spawn")
    SiblingRelease = Context.Event()
    Sibling = Context.Process(target=_WaitForSiblingRelease, args=(SiblingRelease,))
    Sibling.start()
    _RegisterSibling(Sibling, "force-granted-isolation")
    SiblingPid = Sibling.pid
    FixturePayload = b"10.0"
    DeadlineAt = monotonic() + 2.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0, Force=True),
        FixturePayload,
        _IgnoreCancellationUntilFixtureExpiry,
        _Limits(_IgnoreCancellationUntilFixtureExpiry, FixturePayload),
    )
    try:
        Receipt = _PollFor(
            Handle.Observe,
            lambda Value: Value.ReadinessObserved,
        )
        assert Receipt is not None and Receipt.ReadinessObserved
        OwnedPid = Receipt.StartedPid
        assert _PidExists(OwnedPid)

        Receipt = Handle.ForceTerminate()
        assert Receipt.ForceSignalSent
        assert Receipt.OutstandingOwnership
        assert Sibling.is_alive()
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ExitCode is not None and Receipt.ExitCode < 0
        assert Receipt.PublishedResult is None
        assert not _PidExists(OwnedPid)
        assert Sibling.is_alive()
    finally:
        _RecoverAndClose(Handle)
        SiblingRelease.set()
        Sibling.join(timeout=2.0)
        if Sibling.is_alive():
            Sibling.kill()
            Sibling.join(timeout=2.0)
        assert not _PidExists(SiblingPid)
        print(
            f"ONE_SHOT_SIBLING pid={SiblingPid}"
            " survived_owned_action=True pid_absent_after_release=True"
        )
        _IndependentCloseWitness(Sibling, UsedFallback=False)
        Sibling.close()


def test_explicit_force_after_observed_exit_sends_no_signal():
    Payload = b"already-exited"
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0, Force=True),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload),
    )
    try:
        Receipt = Handle.AdvanceUntil(float(DeadlineAt))
        assert Receipt.ProcessExitObserved
        assert Receipt.ExitCode == 0

        Receipt = Handle.ForceTerminate()
        assert Receipt.ForceTerminationRequested
        assert not Receipt.ForceSignalSent
        assert Receipt.ExitCode == 0
        Receipt = _ReapAndClose(Handle)
        assert Receipt.PublishedResult == Payload
    finally:
        _RecoverAndClose(Handle)


def test_cleanup_breach_remains_after_explicit_recovery(tmp_path):
    def ReceiptFacts(Value):
        if Value is None:
            return None
        PublishedResult = Value.PublishedResult
        return {
            "cancellation_requested": Value.CancellationRequested,
            "cancellation_requested_at": Value.CancellationRequestedAt,
            "child_existence_uncertain": Value.ChildExistenceUncertain,
            "cleanup_cutoff_at": Value.CleanupCutoffAt,
            "cleanup_cutoff_breached": Value.CleanupCutoffBreached,
            "cleanup_cutoff_observed_at": Value.CleanupCutoffObservedAt,
            "exact_unstarted": Value.ExactUnstarted,
            "exit_code": Value.ExitCode,
            "force_signal_sent": Value.ForceSignalSent,
            "force_signal_sent_at": Value.ForceSignalSentAt,
            "operational_failure": Value.OperationalFailure,
            "outstanding_ownership": Value.OutstandingOwnership,
            "process_exit_observed": Value.ProcessExitObserved,
            "process_exit_observed_at": Value.ProcessExitObservedAt,
            "published_result": (
                None
                if PublishedResult is None
                else {
                    "bytes": len(PublishedResult),
                    "sha256": sha256(PublishedResult).hexdigest(),
                }
            ),
            "readiness_observed": Value.ReadinessObserved,
            "readiness_observed_at": Value.ReadinessObservedAt,
            "reaped": Value.Reaped,
            "release_acknowledged": Value.ReleaseAcknowledged,
            "resources_closed": Value.ResourcesClosed,
            "result_completion_observed": Value.ResultCompletionObserved,
            "result_diagnostic": Value.ResultDiagnostic,
            "result_valid": Value.ResultValid,
            "start_exception_observed": Value.StartExceptionObserved,
            "start_requested": Value.StartRequested,
            "started_pid": Value.StartedPid,
            "synchronization_cleanup_failures": (
                Value.SynchronizationCleanupFailures
            ),
            "synchronization_resource_identities": (
                Value.SynchronizationResourceIdentities
            ),
            "task_identity": Value.TaskIdentity,
            "unstarted_reason": Value.UnstartedReason,
            "work_deadline_at": Value.WorkDeadlineAt,
            "work_deadline_observed": Value.WorkDeadlineObserved,
            "work_deadline_observed_at": Value.WorkDeadlineObservedAt,
        }

    MarkerPath = tmp_path / "operation-entered.json"
    FixturePayload = (
        str(MarkerPath).encode("utf-8") + b"\x00" + b"10.0"
    )
    DeadlineAt = monotonic() + 3.0
    CleanupAt = DeadlineAt + 0.15
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, CleanupAt, Force=True),
        FixturePayload,
        _PublishEntryThenIgnoreCancellationUntilFixtureExpiry,
        _Limits(
            _PublishEntryThenIgnoreCancellationUntilFixtureExpiry,
            FixturePayload,
        ),
    )
    try:
        Receipt = None
        while monotonic() < DeadlineAt:
            Receipt = Handle.Observe()
            if MarkerPath.exists() or Receipt.ProcessExitObserved:
                break
            sleep(0.005)
        Receipt = Handle.Observe()
        SetupObservedAt = monotonic()
        MarkerExists = MarkerPath.exists()
        MarkerText = (
            MarkerPath.read_text(encoding="utf-8")
            if MarkerExists
            else None
        )
        EntryFacts = None
        MarkerParseFailure = None
        if MarkerText is not None:
            try:
                EntryFacts = json.loads(MarkerText)
            except (TypeError, ValueError) as Error:
                MarkerParseFailure = f"{type(Error).__name__}: {Error}"
        SetupPidExists = _PidExists(Receipt.StartedPid)
        SetupPidStartTicks = _PidStartTicks(Receipt.StartedPid)
        print(
            "ONE_SHOT_CLEANUP_BREACH_SETUP "
            + json.dumps({
                "cleanup_cutoff_at": CleanupAt,
                "marker_exists": MarkerExists,
                "marker_parse_failure": MarkerParseFailure,
                "marker_text": MarkerText,
                "observed_at": SetupObservedAt,
                "pid_exists": SetupPidExists,
                "pid_start_ticks": SetupPidStartTicks,
                "receipt": ReceiptFacts(Receipt),
                "work_deadline_at": DeadlineAt,
            }, sort_keys=True),
        )
        assert MarkerExists, "operation body did not enter before deadline"
        assert MarkerParseFailure is None
        assert EntryFacts is not None
        assert set(EntryFacts) == {"entered_at", "pid"}
        assert type(EntryFacts["entered_at"]) is float
        assert type(EntryFacts["pid"]) is int

        assert Receipt.ReadinessObserved
        assert Receipt.StartedPid == EntryFacts["pid"]
        assert EntryFacts["entered_at"] < DeadlineAt
        assert not Receipt.ExactUnstarted
        assert not Receipt.ProcessExitObserved
        assert Receipt.OutstandingOwnership
        assert Receipt.OperationalFailure is None
        PidStartTicks = SetupPidStartTicks
        assert PidStartTicks is not None
        assert SetupPidExists
        assert _PidStartTicks(Receipt.StartedPid) == PidStartTicks

        AdvanceStartedAt = monotonic()
        Receipt = Handle.AdvanceUntil(float(CleanupAt + 0.5))
        AdvanceReturnedAt = monotonic()
        print(
            "ONE_SHOT_CLEANUP_BREACH_POST_ADVANCE "
            + json.dumps({
                "advance_returned_at": AdvanceReturnedAt,
                "advance_started_at": AdvanceStartedAt,
                "cleanup_cutoff_at": CleanupAt,
                "entry_facts": EntryFacts,
                "expected_pid_start_ticks": PidStartTicks,
                "marker_exists": MarkerPath.exists(),
                "receipt": ReceiptFacts(Receipt),
                "work_deadline_at": DeadlineAt,
            }, sort_keys=True),
        )
        assert Receipt.StartedPid == EntryFacts["pid"]
        assert AdvanceStartedAt < CleanupAt
        assert AdvanceReturnedAt >= CleanupAt
        assert not Receipt.ProcessExitObserved
        assert Receipt.OutstandingOwnership
        assert Receipt.CleanupCutoffBreached
        assert Receipt.CleanupCutoffObservedAt is not None
        assert Receipt.CleanupCutoffObservedAt >= CleanupAt
        assert Receipt.WorkDeadlineAt == DeadlineAt
        assert Receipt.CleanupCutoffAt == CleanupAt
        assert Receipt.WorkDeadlineObserved
        assert Receipt.CancellationRequested
        ReturnedPidExists = _PidExists(Receipt.StartedPid)
        ReturnedPidStartTicks = _PidStartTicks(Receipt.StartedPid)
        print(
            "ONE_SHOT_CLEANUP_BREACH_POST_ADVANCE_LIVENESS "
            + json.dumps({
                "expected_pid": EntryFacts["pid"],
                "expected_pid_start_ticks": PidStartTicks,
                "pid_exists": ReturnedPidExists,
                "returned_pid": Receipt.StartedPid,
                "returned_pid_start_ticks": ReturnedPidStartTicks,
            }, sort_keys=True),
        )
        assert ReturnedPidExists
        assert ReturnedPidStartTicks == PidStartTicks
        BreachAt = Receipt.CleanupCutoffObservedAt

        Receipt = Handle.ForceTerminate()
        assert Receipt.ForceSignalSent
        Receipt = _ReapAndClose(Handle)
        assert Receipt.CleanupCutoffBreached
        assert Receipt.CleanupCutoffObservedAt == BreachAt
        assert not Receipt.OutstandingOwnership
    finally:
        _RecoverAndClose(Handle)


def test_child_copy_close_failure_after_start_retains_owned_continuation(
    monkeypatch,
):
    Fault = _InjectChildSocketCloseFailure(monkeypatch, OSError)
    Payload = b"post-start-close-failure"
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity="post-start-close-failure"),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload),
    )
    monkeypatch.undo()
    try:
        Receipt = Handle.Observe()
        assert Receipt.StartRequested
        assert not Receipt.StartExceptionObserved
        assert Receipt.StartedPid is not None
        assert Receipt.OperationalFailure == "SynchronizationCleanupFailure"
        assert Receipt.SynchronizationCleanupFailures == ("OSError",)
        assert Fault["retained_inode"] is not None
        assert Fault["retained_inode"] in Receipt.SynchronizationResourceIdentities
        assert len(Receipt.SynchronizationResourceIdentities) == 4

        Receipt = _ReapAndClose(Handle)
        assert Receipt.PublishedResult == Payload
        Closed = Handle.Observe()
        assert Closed.ResourcesClosed
        assert Closed.SynchronizationResourceIdentities == ()
        assert Closed.SynchronizationCleanupFailures == ("OSError",)
        assert Fault["retained_inode"] not in _OpenSocketInodes()
    finally:
        _RecoverAndClose(Handle)


def test_start_error_and_child_copy_interrupt_are_additive(monkeypatch):
    OriginalStart = multiprocessing.context.SpawnProcess.start
    Fault = _InjectChildSocketCloseFailure(monkeypatch, KeyboardInterrupt)

    def StartThenRaise(Process):
        OriginalStart(Process)
        raise RuntimeError("controlled post-start exception")

    monkeypatch.setattr(
        multiprocessing.context.SpawnProcess,
        "start",
        StartThenRaise,
    )
    Payload = b"start-error-close-interrupt"
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity="start-error-close-interrupt"),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload),
    )
    monkeypatch.undo()
    try:
        Receipt = Handle.Observe()
        assert Receipt.StartRequested
        assert Receipt.StartExceptionObserved
        assert Receipt.StartedPid is not None
        assert Receipt.OperationalFailure == "RuntimeError"
        assert Receipt.SynchronizationCleanupFailures == ("KeyboardInterrupt",)
        assert Fault["retained_inode"] is not None
        assert Fault["retained_inode"] in Receipt.SynchronizationResourceIdentities
        assert len(Receipt.SynchronizationResourceIdentities) == 4

        Receipt = _ReapAndClose(Handle)
        assert Receipt.ResultValid
        assert Receipt.ResultDiagnostic == "StartFailureResultDiscarded"
        assert Receipt.PublishedResult is None
        Closed = Handle.Observe()
        assert Closed.ResourcesClosed
        assert Closed.SynchronizationResourceIdentities == ()
        assert Closed.SynchronizationCleanupFailures == ("KeyboardInterrupt",)
        assert Fault["retained_inode"] not in _OpenSocketInodes()
    finally:
        _RecoverAndClose(Handle)


def test_start_exception_after_real_start_retains_and_resolves_owned_child(
    monkeypatch,
):
    OriginalStart = multiprocessing.context.SpawnProcess.start

    def StartThenRaise(Process):
        OriginalStart(Process)
        raise RuntimeError("controlled post-start exception")

    monkeypatch.setattr(
        multiprocessing.context.SpawnProcess,
        "start",
        StartThenRaise,
    )
    Payload = b"post-start"
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload),
    )
    monkeypatch.undo()
    try:
        Receipt = Handle.Observe()
        assert Receipt.StartRequested
        assert Receipt.StartExceptionObserved
        assert Receipt.OperationalFailure == "RuntimeError"
        assert Receipt.StartedPid is not None
        Receipt = _ReapAndClose(Handle)
        assert not Receipt.ChildExistenceUncertain
        assert Receipt.ResultValid
        assert Receipt.ResultDiagnostic == "StartFailureResultDiscarded"
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_parent_interrupt_after_real_start_returns_owned_continuation(
    monkeypatch,
):
    OriginalStart = multiprocessing.context.SpawnProcess.start

    def StartThenInterrupt(Process):
        OriginalStart(Process)
        raise KeyboardInterrupt("controlled post-start interruption")

    monkeypatch.setattr(
        multiprocessing.context.SpawnProcess,
        "start",
        StartThenInterrupt,
    )
    Payload = b"post-start-interrupt"
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload),
    )
    monkeypatch.undo()
    try:
        Receipt = Handle.Observe()
        assert Receipt.StartRequested
        assert Receipt.StartExceptionObserved
        assert Receipt.OperationalFailure == "KeyboardInterrupt"
        assert Receipt.StartedPid is not None
        assert Receipt.OutstandingOwnership
        Receipt = _ReapAndClose(Handle)
        assert Receipt.ResultValid
        assert Receipt.ResultDiagnostic == "StartFailureResultDiscarded"
        assert Receipt.PublishedResult is None
    finally:
        _RecoverAndClose(Handle)


def test_child_uses_original_deadline_when_public_start_returns_late(
    monkeypatch,
    tmp_path,
):
    OriginalStart = multiprocessing.context.SpawnProcess.start

    def DelayedStart(Process):
        sleep(0.20)
        return OriginalStart(Process)

    monkeypatch.setattr(
        multiprocessing.context.SpawnProcess,
        "start",
        DelayedStart,
    )
    Marker = tmp_path / "operation-entered"
    Payload = str(Marker).encode("utf-8")
    DeadlineAt = monotonic() + 0.10
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0, Force=False),
        Payload,
        _WriteEntryMarkerThenEcho,
        _Limits(_WriteEntryMarkerThenEcho, Payload),
    )
    monkeypatch.undo()
    try:
        Receipt = _ReapAndClose(Handle)
        assert Receipt.StartRequested
        assert Receipt.StartedPid is not None
        assert Receipt.ReadinessObserved
        assert Receipt.WorkDeadlineObserved
        assert Receipt.CancellationRequested
        assert not Receipt.ResultCompletionObserved
        assert Receipt.PublishedResult is None
        assert not Marker.exists()
    finally:
        _RecoverAndClose(Handle)


@pytest.mark.skipif(
    os.environ.get("RC_ONESHOT_WATCHDOG_DRILL") != "1",
    reason="independent watchdog recovery diagnostic only",
)
def test_watchdog_recovery_drill_stalls_after_durable_ownership_witness():
    FixturePayload = b"30.0"
    DeadlineAt = monotonic() + 20.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 5.0, Force=False),
        FixturePayload,
        _IgnoreCancellationUntilFixtureExpiry,
        _Limits(_IgnoreCancellationUntilFixtureExpiry, FixturePayload),
    )
    Receipt = _PollFor(
        Handle.Observe,
        lambda Value: Value.ReadinessObserved,
    )
    assert Receipt is not None and Receipt.ReadinessObserved
    assert Receipt.StartedPid is not None
    assert Receipt.RequestSharedMemoryName is not None
    assert Receipt.ResultSharedMemoryName is not None
    _ArmHandleWitness(Handle, Receipt)
    sleep(30.0)
    pytest.fail("outer watchdog failed to interrupt the recovery drill")


def test_retained_closed_handle_releases_all_synchronization_resources():
    Payload = b"retained-close"
    SharedCutoffAt = monotonic() + 1.0
    Before = _OpenSocketInodes()
    Handle = _StartOwned(
        _Request(SharedCutoffAt, TaskIdentity="retained-close-resources"),
        _Authority(SharedCutoffAt, SharedCutoffAt),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload),
    )
    try:
        Handle.AdvanceUntil(float(SharedCutoffAt))
        Receipt = Handle.ReapIfExited()
        assert Receipt.Reaped and not Receipt.ReleaseAcknowledged
        assert Receipt.OutstandingOwnership
        assert monotonic() < SharedCutoffAt
        Identities = Receipt.SynchronizationResourceIdentities
        assert len(Identities) == 3
        assert len(set(Identities)) == 3
        assert set(Identities).isdisjoint(Before)
        assert set(Identities) <= _OpenSocketInodes()

        _CloseAlreadyReaped(Handle, Receipt)
        Closed = Handle.Observe()
        assert Closed.ResourcesClosed
        assert Closed.SynchronizationResourceIdentities == ()
        assert set(Identities).isdisjoint(_OpenSocketInodes())

        sleep(max(0.0, SharedCutoffAt - monotonic() + 0.02))
        assert Handle.Observe() == Closed
        assert Handle.RequestCancellation() == Closed
        assert Handle.ForceTerminate() == Closed
        assert Handle.ReapIfExited() == Closed
        assert Handle.AdvanceUntil(float(monotonic() + 0.1)) == Closed
        Handle.CloseReleased()
        Handle.CloseReleased()
        assert Handle.Observe() == Closed
        assert set(Identities).isdisjoint(_OpenSocketInodes())
    finally:
        _RecoverAndClose(Handle)


def test_retained_closed_handle_has_no_backend_sync_resource_or_mutation():
    Payload = b"backend-independent-retained-close"
    DeadlineAt = monotonic() + 3.0
    SemaphoresBefore = _NamedSemaphoreResources()
    SocketsBefore = _OpenSocketInodes()
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity="backend-independent-retained-close"),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        Payload,
        _EchoBytes,
        _Limits(_EchoBytes, Payload),
    )
    try:
        Receipt = _PollFor(
            Handle.Observe,
            lambda Value: Value.ReadinessObserved,
        )
        assert Receipt is not None and Receipt.ReadinessObserved
        OwnedSemaphores = _NamedSemaphoreResources() - SemaphoresBefore
        OwnedSockets = _OpenSocketInodes() - SocketsBefore

        Handle.AdvanceUntil(float(DeadlineAt))
        Receipt = Handle.ReapIfExited()
        assert Receipt.Reaped and not Receipt.ReleaseAcknowledged
        assert Receipt.OutstandingOwnership
        _CloseAlreadyReaped(Handle, Receipt)

        Closed = Handle.Observe()
        assert Closed.ResourcesClosed
        assert OwnedSemaphores.isdisjoint(_NamedSemaphoreResources())
        assert OwnedSockets.isdisjoint(_OpenSocketInodes())
        assert Handle.RequestCancellation() == Closed
        assert Handle.ForceTerminate() == Closed
        assert Handle.ReapIfExited() == Closed
        assert Handle.AdvanceUntil(float(monotonic() + 0.1)) == Closed
        Handle.CloseReleased()
        assert Handle.Observe() == Closed
        assert OwnedSemaphores.isdisjoint(_NamedSemaphoreResources())
        assert OwnedSockets.isdisjoint(_OpenSocketInodes())
    finally:
        _RecoverAndClose(Handle)


def test_prestart_construction_failure_closes_allocated_sync_resources(
    monkeypatch,
):
    Before = _OpenSocketInodes()

    def FailProcessConstruction(_Context, *Arguments, **KeywordArguments):
        raise RuntimeError("controlled process construction failure")

    monkeypatch.setattr(
        multiprocessing.context.SpawnContext,
        "Process",
        FailProcessConstruction,
    )
    DeadlineAt = monotonic() + 3.0
    Handle = _StartOwned(
        _Request(DeadlineAt, TaskIdentity="prestart-sync-cleanup"),
        _Authority(DeadlineAt, DeadlineAt + 1.0),
        b"",
        _EchoBytes,
        _Limits(_EchoBytes, b""),
    )
    monkeypatch.undo()
    try:
        Receipt = Handle.Observe()
        assert Receipt.ExactUnstarted
        assert Receipt.UnstartedReason == "AllocationFailure"
        assert Receipt.OperationalFailure == "RuntimeError"
        assert Receipt.SynchronizationResourceIdentities == ()
        assert Receipt.ResourcesClosed
        assert _OpenSocketInodes() == Before
        Handle.CloseReleased()
        assert Handle.Observe() == Receipt
        _IndependentCloseWitness(Handle, UsedFallback=False)
    finally:
        _RecoverAndClose(Handle)


def test_live_close_rejects_and_caller_finally_retains_continuation():
    DeadlineAt = monotonic() + 2.0
    Handle = _StartOwned(
        _Request(DeadlineAt),
        _Authority(DeadlineAt, DeadlineAt + 1.0, Force=False),
        b"",
        _WaitForCancellation,
        _Limits(_WaitForCancellation, b""),
    )
    ContinuedInFinally = False
    try:
        Receipt = _PollFor(
            Handle.Observe,
            lambda Value: Value.ReadinessObserved,
        )
        assert Receipt is not None and Receipt.ReadinessObserved
        with pytest.raises(RuntimeError):
            Handle.CloseReleased()

        try:
            raise RuntimeError("controlled caller failure")
        finally:
            ContinuedInFinally = True
            Handle.RequestCancellation()
            Receipt = _ReapAndClose(Handle)
            assert Receipt.ReleaseAcknowledged
    except RuntimeError as Error:
        assert str(Error) == "controlled caller failure"
    finally:
        _RecoverAndClose(Handle)
    assert ContinuedInFinally

"""Shared-clock interleavings with real spawned processes and explicit work gates."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from threading import Timer
from time import monotonic, sleep

from PhysicalDesign.Contracts.Runtime import (
    RuntimeClaimStrength,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
    RuntimeWorkProduct,
)
import PhysicalDesign.Runtime.OneShotProcess as OneShotProcess
import PhysicalDesign.Runtime.SpawnedWork as SpawnedWork


_CLOCK_ENVIRONMENT = "RC_RUNTIME_FIXTURE_CLOCK"
_OriginalChildEntry = OneShotProcess._RunRuntimeOneShotChild
_ChildClock = None
_ChildCancellationSocket = None


def _WriteAtomically(PathValue: Path, Value) -> None:
    Pending = PathValue.with_name(f".{PathValue.name}.{os.getpid()}.pending")
    with Pending.open("w", encoding="utf-8") as File:
        json.dump(Value, File, sort_keys=True)
        File.write("\n")
        File.flush()
        os.fsync(File.fileno())
    os.replace(Pending, PathValue)


class SharedRuntimeClock:
    """One immutable deadline domain shared by the parent and spawned child."""

    def __init__(self, PathValue: Path) -> None:
        self.Path = PathValue

    def Read(self) -> float:
        return float(json.loads(self.Path.read_text(encoding="utf-8")))

    def Set(self, Value: float) -> None:
        assert type(Value) is float
        if self.Path.exists():
            assert Value >= self.Read(), "the fixture clock must not go backwards"
        _WriteAtomically(self.Path, Value)


def InstallSharedRuntimeClock(monkeypatch, Directory: Path) -> SharedRuntimeClock:
    Clock = SharedRuntimeClock(Directory / "runtime-clock.json")
    Clock.Set(1000.0)
    monkeypatch.setenv(_CLOCK_ENVIRONMENT, str(Clock.Path))
    monkeypatch.setattr(OneShotProcess, "monotonic", Clock.Read)
    monkeypatch.setattr(SpawnedWork, "monotonic", Clock.Read)
    monkeypatch.setattr(
        OneShotProcess,
        "_RunRuntimeOneShotChild",
        _RunChildWithSharedClock,
    )
    return Clock


def _RunChildWithSharedClock(*Arguments) -> None:
    """Install only Runtime clocks, then execute the unmodified production entry."""
    global _ChildClock, _ChildCancellationSocket
    _ChildClock = SharedRuntimeClock(Path(os.environ[_CLOCK_ENVIRONMENT]))
    _ChildCancellationSocket = Arguments[5]
    OneShotProcess.monotonic = _ChildClock.Read
    SpawnedWork.monotonic = _ChildClock.Read
    _OriginalChildEntry(*Arguments)


class OperationGate:
    def __init__(self, Directory: Path) -> None:
        self.Directory = Directory
        self.Directory.mkdir(exist_ok=True)
        self.EntryPath = Directory / "entered.json"
        self.CancellationPath = Directory / "cancellation.json"
        self.ReleasePath = Directory / "release"
        self.CompletedPath = Directory / "completed.json"
        self.WatchdogPath = Directory / "watchdog-expired"

    def _WaitFor(self, PathValue: Path) -> dict:
        # This is a failing fixture watchdog, never an extension of Runtime authority.
        ExpiresAt = monotonic() + 3.0
        while not PathValue.exists() and monotonic() < ExpiresAt:
            sleep(0.005)
        assert PathValue.exists(), f"fixture event was not observed: {PathValue.name}"
        return json.loads(PathValue.read_text(encoding="utf-8"))

    def WaitForEntry(self) -> dict:
        return self._WaitFor(self.EntryPath)

    def WaitForCancellation(self) -> dict:
        return self._WaitFor(self.CancellationPath)

    def Release(self) -> None:
        self.ReleasePath.touch(exist_ok=True)

    def StartReleaseWatchdog(self, EnteredAt: float, Seconds: float) -> Timer:
        def ReleaseBrokenFixture() -> None:
            self.WatchdogPath.touch(exist_ok=True)
            self.Release()

        Watchdog = Timer(
            max(0.0, EnteredAt + Seconds - monotonic()),
            ReleaseBrokenFixture,
        )
        Watchdog.daemon = True
        Watchdog.start()
        return Watchdog


def _PublishOperationEntry(Directory: str) -> OperationGate:
    assert _ChildClock is not None
    assert _ChildCancellationSocket is not None
    Gate = OperationGate(Path(Directory))
    _WriteAtomically(Gate.EntryPath, {
        "pid": os.getpid(),
        "clock_at": _ChildClock.Read(),
        "real_entered_at": monotonic(),
    })
    return Gate


def _WaitAtOperationGate(Directory: str) -> None:
    Gate = _PublishOperationEntry(Directory)
    CancellationSeen = False
    while not Gate.ReleasePath.exists():
        if not CancellationSeen:
            try:
                Token = _ChildCancellationSocket.recv(2, socket.MSG_PEEK)
            except BlockingIOError:
                Token = None
            if Token is not None:
                assert Token == b"C", "fixture observed an invalid cancellation token"
                CancellationSeen = True
                _WriteAtomically(Gate.CancellationPath, {
                    "pid": os.getpid(),
                    "clock_at": _ChildClock.Read(),
                })
        sleep(0.005)
    _WriteAtomically(Gate.CompletedPath, {
        "pid": os.getpid(),
        "clock_at": _ChildClock.Read(),
    })


def GatedEchoBytes(Payload: bytes, _CancellationRequested) -> bytes:
    Directory, Value = Payload.split(b"\x00", 1)
    _WaitAtOperationGate(Directory.decode("utf-8"))
    return Value


def CooperativeBytesAfterEntry(Payload: bytes, CancellationRequested) -> bytes:
    Gate = _PublishOperationEntry(Payload.decode("utf-8"))
    while not CancellationRequested():
        if Gate.ReleasePath.exists():
            return b"fixture-released-before-cancellation"
        sleep(0.005)
    _WriteAtomically(Gate.CancellationPath, {
        "pid": os.getpid(),
        "clock_at": _ChildClock.Read(),
        "production_callback_returned": True,
    })
    return b"cancelled"


def GatedObservedProduct(Payload, Request):
    Directory, Value = Payload
    _WaitAtOperationGate(Directory)
    return RuntimeWorkProduct(
        Value=Value,
        SearchOutcome=RuntimeSearchOutcome.Prepared,
        ClaimStrength=RuntimeClaimStrength.Complete,
        TerminalReason=RuntimeTerminalReason.Prepared,
        CandidateIdentity=f"candidate:{Request.Scope.DomainIdentity}:{Value}",
    )

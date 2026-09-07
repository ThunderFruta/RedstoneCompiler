"""Owned Linux one-shot process supervision for bounded encoded bytes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from math import isfinite
import multiprocessing
from multiprocessing.connection import wait
from multiprocessing.shared_memory import SharedMemory
from os import fstat
from secrets import token_bytes
import socket
from struct import Struct, pack
import sys
from time import monotonic
from types import FunctionType
from typing import Callable

from ..Contracts.Runtime import (
    RuntimeLifecycle,
    RuntimeWorkAuthority,
    RuntimeWorkRequest,
)


_PROTOCOL_VERSION = 1
_REQUEST_MAGIC = b"RC1REQ01"
_RESULT_MAGIC = b"RC1RES01"
_REQUEST_HEADER = Struct("!8sB7xIdQ32s32s")
_RESULT_HEADER = Struct("!8sB7xQ32s32s")


@dataclass(frozen=True)
class RuntimeOneShotProcessLimits:
    """Exact total encoded request and result shared-memory capacities."""

    MaximumRequestBytes: int
    MaximumResultBytes: int

    def __post_init__(self) -> None:
        for Name, Value, Minimum in (
            ("MaximumRequestBytes", self.MaximumRequestBytes, _REQUEST_HEADER.size),
            ("MaximumResultBytes", self.MaximumResultBytes, _RESULT_HEADER.size),
        ):
            if type(Value) is not int:
                raise TypeError(f"{Name} must be an integer")
            if Value < Minimum:
                raise ValueError(f"{Name} must be at least {Minimum}")


@dataclass(frozen=True)
class RuntimeOneShotProcessReceipt:
    """One immutable public observation of a caller-owned child handle."""

    TaskIdentity: str
    StartRequested: bool
    StartedPid: int | None
    ReadinessObserved: bool
    ReadinessObservedAt: float | None
    ExactUnstarted: bool
    UnstartedReason: str | None
    StartExceptionObserved: bool
    ChildExistenceUncertain: bool
    OperationalFailure: str | None
    InvocationBindingIdentity: str
    SynchronizationResourceIdentities: tuple[int, ...]
    SynchronizationCleanupFailures: tuple[str, ...]
    RequestSharedMemoryName: str | None
    ResultSharedMemoryName: str | None
    RequestEnvelopeBytes: int
    RequestCapacityBytes: int
    ResultCapacityBytes: int
    ResultCompletionObserved: bool
    ResultCompletionObservedAt: float | None
    ResultAvailable: bool
    ResultEnvelopeBytesObserved: int | None
    ResultPayloadBytesObserved: int | None
    ResultValid: bool
    ResultDiagnostic: str | None
    WorkDeadlineAt: float
    CleanupCutoffAt: float
    WorkDeadlineObserved: bool
    WorkDeadlineObservedAt: float | None
    CancellationRequested: bool
    CancellationRequestedAt: float | None
    ForceTerminationAuthorized: bool
    ForceTerminationRequested: bool
    ForceTerminationDenied: bool
    ForceSignalSent: bool
    ForceSignalSentAt: float | None
    ProcessExitObserved: bool
    ProcessExitObservedAt: float | None
    ExitCode: int | None
    Reaped: bool
    ReapedAt: float | None
    ReleaseAcknowledged: bool
    ReleaseAcknowledgedAt: float | None
    CleanupCutoffBreached: bool
    CleanupCutoffObservedAt: float | None
    OutstandingOwnership: bool
    ResourcesClosed: bool
    PublishedResult: bytes | None


def _ResolveOperation(ModuleName: str, QualifiedName: str) -> Callable:
    Value = import_module(ModuleName)
    for Name in QualifiedName.split("."):
        Value = getattr(Value, Name)
    if type(Value) is not FunctionType:
        raise TypeError("one-shot operation reference must resolve to a function")
    return Value


def _OperationReference(Operation: Callable) -> bytes:
    if type(Operation) is not FunctionType:
        raise TypeError("one-shot operation must be a module-level function")
    ModuleName = Operation.__module__
    QualifiedName = Operation.__qualname__
    if (
        not isinstance(ModuleName, str)
        or not ModuleName
        or not isinstance(QualifiedName, str)
        or not QualifiedName
        or "." in QualifiedName
        or "<locals>" in QualifiedName
        or "\x00" in ModuleName
        or "\x00" in QualifiedName
    ):
        raise ValueError("one-shot operation must be an importable module function")
    if _ResolveOperation(ModuleName, QualifiedName) is not Operation:
        raise ValueError("one-shot operation reference is not stable on import")
    return f"{ModuleName}\x00{QualifiedName}".encode("utf-8")


def _DecodeOperationReference(Encoded: bytes) -> tuple[str, str]:
    try:
        Text = Encoded.decode("utf-8")
    except UnicodeDecodeError as Error:
        raise ValueError("operation reference is not UTF-8") from Error
    if Text.count("\x00") != 1:
        raise ValueError("operation reference has invalid framing")
    ModuleName, QualifiedName = Text.split("\x00")
    if not ModuleName or not QualifiedName or "." in QualifiedName:
        raise ValueError("operation reference is not a module-level function")
    return ModuleName, QualifiedName


def _RunRuntimeOneShotChild(
    RequestSharedMemoryName: str,
    ResultSharedMemoryName: str,
    MaximumRequestBytes: int,
    MaximumResultBytes: int,
    ReadinessSocket: socket.socket,
    CancellationSocket: socket.socket,
    CompletionSocket: socket.socket,
) -> None:
    """Spawn-compatible child entry using only bounded encoded application data."""
    RequestMemory = SharedMemory(name=RequestSharedMemoryName)
    ResultMemory = SharedMemory(name=ResultSharedMemoryName)
    for SynchronizationSocket in (
        ReadinessSocket,
        CancellationSocket,
        CompletionSocket,
    ):
        SynchronizationSocket.setblocking(False)
    try:
        RequestBytes = bytes(RequestMemory.buf[:MaximumRequestBytes])
        if len(RequestBytes) < _REQUEST_HEADER.size:
            raise ValueError("request allocation is smaller than its header")
        (
            Magic,
            Version,
            OperationReferenceLength,
            WorkDeadlineAt,
            PayloadLength,
            InvocationBinding,
            ExpectedDigest,
        ) = _REQUEST_HEADER.unpack_from(RequestBytes)
        if Magic != _REQUEST_MAGIC or Version != _PROTOCOL_VERSION:
            raise ValueError("request protocol identity is invalid")
        if RequestBytes[9:16] != b"\x00" * 7:
            raise ValueError("request reserved bytes are nonzero")
        if not isfinite(WorkDeadlineAt):
            raise ValueError("request deadline is not finite")
        EnvelopeBytes = (
            _REQUEST_HEADER.size + OperationReferenceLength + PayloadLength
        )
        if EnvelopeBytes > MaximumRequestBytes:
            raise ValueError("request envelope exceeds its allocation")
        if any(RequestBytes[EnvelopeBytes:]):
            raise ValueError("request padding is nonzero")
        Body = RequestBytes[_REQUEST_HEADER.size:EnvelopeBytes]
        OperationReference = Body[:OperationReferenceLength]
        EncodedPayload = Body[OperationReferenceLength:]
        DeadlineBytes = pack("!d", WorkDeadlineAt)
        if (
            sha256(DeadlineBytes + InvocationBinding + Body).digest()
            != ExpectedDigest
        ):
            raise ValueError("request digest does not match")
        ModuleName, QualifiedName = _DecodeOperationReference(OperationReference)
        Operation = _ResolveOperation(ModuleName, QualifiedName)

        CancellationObserved = False

        def CancellationRequested() -> bool:
            nonlocal CancellationObserved
            if CancellationObserved:
                return True
            if monotonic() >= WorkDeadlineAt:
                CancellationObserved = True
                return True
            try:
                CancellationToken = CancellationSocket.recv(2)
            except BlockingIOError:
                return False
            if CancellationToken != b"C":
                raise ValueError("cancellation token is invalid")
            CancellationObserved = True
            return CancellationObserved

        if ReadinessSocket.send(b"R") != 1:
            raise RuntimeError("readiness token was not sent")
        if CancellationRequested():
            return
        Result = Operation(EncodedPayload, CancellationRequested)
        if type(Result) is not bytes:
            raise TypeError("one-shot operation must return exact bytes")

        ResultEnvelopeBytes = _RESULT_HEADER.size + len(Result)
        ResultMemory.buf[:MaximumResultBytes] = b"\x00" * MaximumResultBytes
        if ResultEnvelopeBytes > MaximumResultBytes:
            _RESULT_HEADER.pack_into(
                ResultMemory.buf,
                0,
                _RESULT_MAGIC,
                _PROTOCOL_VERSION,
                len(Result),
                InvocationBinding,
                sha256(InvocationBinding + Result).digest(),
            )
            if CompletionSocket.send(b"D") != 1:
                raise RuntimeError("completion token was not sent")
            return
        _RESULT_HEADER.pack_into(
            ResultMemory.buf,
            0,
            _RESULT_MAGIC,
            _PROTOCOL_VERSION,
            len(Result),
            InvocationBinding,
            sha256(InvocationBinding + Result).digest(),
        )
        ResultMemory.buf[_RESULT_HEADER.size:ResultEnvelopeBytes] = Result
        if CompletionSocket.send(b"D") != 1:
            raise RuntimeError("completion token was not sent")
    finally:
        RequestMemory.close()
        ResultMemory.close()
        ReadinessSocket.close()
        CancellationSocket.close()
        CompletionSocket.close()


class RuntimeOneShotProcessHandle:
    """Continuing caller ownership of one process and its bounded IPC."""

    def __init__(
        self,
        Request: RuntimeWorkRequest,
        Authority: RuntimeWorkAuthority,
        Limits: RuntimeOneShotProcessLimits,
        RequestEnvelopeBytes: int,
        InvocationBinding: bytes,
    ) -> None:
        self._Request = Request
        self._Authority = Authority
        self._Limits = Limits
        self._RequestEnvelopeBytes = RequestEnvelopeBytes
        self._InvocationBinding = InvocationBinding
        self._Process = None
        self._RequestMemory: SharedMemory | None = None
        self._ResultMemory: SharedMemory | None = None
        self._ReadinessSocket: socket.socket | None = None
        self._CancellationSocket: socket.socket | None = None
        self._CompletionSocket: socket.socket | None = None
        self._ChildSocketCopies: tuple[socket.socket, ...] = ()
        self._SynchronizationCleanupFailures: tuple[str, ...] = ()

        self._StartRequested = False
        self._StartedPid: int | None = None
        self._ReadinessObserved = False
        self._ReadinessObservedAt: float | None = None
        self._ExactUnstarted = False
        self._UnstartedReason: str | None = None
        self._StartExceptionObserved = False
        self._ChildExistenceUncertain = False
        self._OperationalFailure: str | None = None
        self._ResultCompletionObserved = False
        self._ResultCompletionObservedAt: float | None = None
        self._ResultAvailable = False
        self._ResultEnvelopeBytesObserved: int | None = None
        self._ResultPayloadBytesObserved: int | None = None
        self._ResultValid = False
        self._ResultDiagnostic: str | None = None
        self._ResultDecoded = False
        self._CandidateResult: bytes | None = None
        self._WorkDeadlineObserved = False
        self._WorkDeadlineObservedAt: float | None = None
        self._CancellationRequested = False
        self._CancellationRequestedAt: float | None = None
        self._ForceTerminationRequested = False
        self._ForceTerminationDenied = False
        self._ForceSignalSent = False
        self._ForceSignalSentAt: float | None = None
        self._ProcessExitObserved = False
        self._ProcessExitObservedAt: float | None = None
        self._ExitCode: int | None = None
        self._Reaped = False
        self._ReapedAt: float | None = None
        self._ReleaseAcknowledged = False
        self._ReleaseAcknowledgedAt: float | None = None
        self._CleanupCutoffBreached = False
        self._CleanupCutoffObservedAt: float | None = None
        self._OutstandingOwnership = False
        self._ResourcesClosed = False
        self._PublishedResult: bytes | None = None

    def _SynchronizationResourceIdentities(self) -> tuple[int, ...]:
        Identities = []
        for SynchronizationSocket in (
            self._ReadinessSocket,
            self._CancellationSocket,
            self._CompletionSocket,
            *self._ChildSocketCopies,
        ):
            if SynchronizationSocket is None:
                continue
            try:
                Identity = fstat(SynchronizationSocket.fileno()).st_ino
            except (OSError, ValueError):
                continue
            Identities.append(Identity)
        return tuple(Identities)

    def _Receipt(self) -> RuntimeOneShotProcessReceipt:
        return RuntimeOneShotProcessReceipt(
            TaskIdentity=self._Request.TaskIdentity,
            StartRequested=self._StartRequested,
            StartedPid=self._StartedPid,
            ReadinessObserved=self._ReadinessObserved,
            ReadinessObservedAt=self._ReadinessObservedAt,
            ExactUnstarted=self._ExactUnstarted,
            UnstartedReason=self._UnstartedReason,
            StartExceptionObserved=self._StartExceptionObserved,
            ChildExistenceUncertain=self._ChildExistenceUncertain,
            OperationalFailure=self._OperationalFailure,
            InvocationBindingIdentity=self._InvocationBinding.hex(),
            SynchronizationResourceIdentities=(
                self._SynchronizationResourceIdentities()
            ),
            SynchronizationCleanupFailures=(
                self._SynchronizationCleanupFailures
            ),
            RequestSharedMemoryName=(
                None if self._RequestMemory is None else self._RequestMemory.name
            ),
            ResultSharedMemoryName=(
                None if self._ResultMemory is None else self._ResultMemory.name
            ),
            RequestEnvelopeBytes=self._RequestEnvelopeBytes,
            RequestCapacityBytes=self._Limits.MaximumRequestBytes,
            ResultCapacityBytes=self._Limits.MaximumResultBytes,
            ResultCompletionObserved=self._ResultCompletionObserved,
            ResultCompletionObservedAt=self._ResultCompletionObservedAt,
            ResultAvailable=self._ResultAvailable,
            ResultEnvelopeBytesObserved=self._ResultEnvelopeBytesObserved,
            ResultPayloadBytesObserved=self._ResultPayloadBytesObserved,
            ResultValid=self._ResultValid,
            ResultDiagnostic=self._ResultDiagnostic,
            WorkDeadlineAt=self._Authority.WorkDeadlineAt,
            CleanupCutoffAt=self._Authority.CleanupCutoffAt,
            WorkDeadlineObserved=self._WorkDeadlineObserved,
            WorkDeadlineObservedAt=self._WorkDeadlineObservedAt,
            CancellationRequested=self._CancellationRequested,
            CancellationRequestedAt=self._CancellationRequestedAt,
            ForceTerminationAuthorized=(
                self._Authority.ForceTerminationAuthorized
            ),
            ForceTerminationRequested=self._ForceTerminationRequested,
            ForceTerminationDenied=self._ForceTerminationDenied,
            ForceSignalSent=self._ForceSignalSent,
            ForceSignalSentAt=self._ForceSignalSentAt,
            ProcessExitObserved=self._ProcessExitObserved,
            ProcessExitObservedAt=self._ProcessExitObservedAt,
            ExitCode=self._ExitCode,
            Reaped=self._Reaped,
            ReapedAt=self._ReapedAt,
            ReleaseAcknowledged=self._ReleaseAcknowledged,
            ReleaseAcknowledgedAt=self._ReleaseAcknowledgedAt,
            CleanupCutoffBreached=self._CleanupCutoffBreached,
            CleanupCutoffObservedAt=self._CleanupCutoffObservedAt,
            OutstandingOwnership=self._OutstandingOwnership,
            ResourcesClosed=self._ResourcesClosed,
            PublishedResult=self._PublishedResult,
        )

    def _CloseChildSocketCopiesAfterStart(self) -> None:
        Remaining = []
        for SynchronizationSocket in self._ChildSocketCopies:
            try:
                SynchronizationSocket.close()
            except BaseException as Error:
                Remaining.append(SynchronizationSocket)
                self._SynchronizationCleanupFailures += (
                    type(Error).__name__,
                )
        self._ChildSocketCopies = tuple(Remaining)
        if Remaining and self._OperationalFailure is None:
            self._OperationalFailure = "SynchronizationCleanupFailure"

    def _RequestCancellationAt(self, ObservedAt: float) -> None:
        if self._ResourcesClosed or self._CancellationRequested:
            return
        self._CancellationRequested = True
        self._CancellationRequestedAt = ObservedAt
        if self._CancellationSocket is not None:
            try:
                SentBytes = self._CancellationSocket.send(b"C")
            except (BlockingIOError, OSError):
                SentBytes = 0
            if SentBytes != 1:
                self._OperationalFailure = "CancellationSignalUnavailable"

    def _ObserveCutoffs(self, ObservedAt: float) -> None:
        if (
            not self._WorkDeadlineObserved
            and ObservedAt >= self._Authority.WorkDeadlineAt
        ):
            self._WorkDeadlineObserved = True
            self._WorkDeadlineObservedAt = ObservedAt
            self._RequestCancellationAt(ObservedAt)
        if (
            not self._CleanupCutoffBreached
            and self._OutstandingOwnership
            and ObservedAt >= self._Authority.CleanupCutoffAt
        ):
            self._CleanupCutoffBreached = True
            self._CleanupCutoffObservedAt = ObservedAt

    @staticmethod
    def _ReceiveControlToken(
        SynchronizationSocket: socket.socket | None,
        ExpectedToken: bytes,
    ) -> bool | None:
        if SynchronizationSocket is None:
            return None
        try:
            Token = SynchronizationSocket.recv(2)
        except BlockingIOError:
            return None
        except OSError:
            return False
        return Token == ExpectedToken

    def Observe(self) -> RuntimeOneShotProcessReceipt:
        """Return a nonblocking public state snapshot."""
        if self._ResourcesClosed:
            return self._Receipt()
        ObservedAt = monotonic()
        self._ObserveCutoffs(ObservedAt)
        if self._Process is None:
            return self._Receipt()

        try:
            Pid = self._Process.pid
        except (AssertionError, ValueError):
            Pid = None
        if Pid is not None:
            self._StartedPid = Pid
            self._ChildExistenceUncertain = False

        if not self._ReadinessObserved:
            ReadinessObserved = self._ReceiveControlToken(
                self._ReadinessSocket,
                b"R",
            )
            if ReadinessObserved is True:
                self._ReadinessObserved = True
                self._ReadinessObservedAt = ObservedAt
            elif ReadinessObserved is False:
                self._OperationalFailure = "ReadinessSignalInvalid"

        SentinelReady = False
        try:
            SentinelReady = bool(wait((self._Process.sentinel,), timeout=0))
        except (AssertionError, OSError, ValueError):
            SentinelReady = False
        if SentinelReady and not self._ProcessExitObserved:
            self._ProcessExitObserved = True
            self._ProcessExitObservedAt = ObservedAt
            self._ExitCode = self._Process.exitcode
        elif self._ProcessExitObserved:
            self._ExitCode = self._Process.exitcode

        if self._ProcessExitObserved and not self._ResultCompletionObserved:
            CompletionObserved = self._ReceiveControlToken(
                self._CompletionSocket,
                b"D",
            )
            if CompletionObserved is True:
                self._ResultCompletionObserved = True
                self._ResultCompletionObservedAt = ObservedAt
                self._ResultAvailable = True
            elif CompletionObserved is False:
                self._OperationalFailure = "CompletionSignalInvalid"
        return self._Receipt()

    def AdvanceUntil(
        self,
        AbsoluteCutoffAt: float,
    ) -> RuntimeOneShotProcessReceipt:
        """Wait on the owned process sentinel within the original cleanup grant."""
        if self._ResourcesClosed:
            return self._Receipt()
        if type(AbsoluteCutoffAt) is not float or not isfinite(AbsoluteCutoffAt):
            raise TypeError("AbsoluteCutoffAt must be an exact finite float")
        EffectiveCutoffAt = min(
            AbsoluteCutoffAt,
            self._Authority.CleanupCutoffAt,
        )
        while True:
            Receipt = self.Observe()
            if Receipt.ProcessExitObserved or not Receipt.OutstandingOwnership:
                return Receipt
            Current = monotonic()
            if Current >= EffectiveCutoffAt:
                return self.Observe()
            NextStopAt = EffectiveCutoffAt
            if not self._WorkDeadlineObserved:
                NextStopAt = min(NextStopAt, self._Authority.WorkDeadlineAt)
            try:
                Sentinel = self._Process.sentinel
            except (AssertionError, OSError, ValueError):
                return self._Receipt()
            wait((Sentinel,), timeout=max(0.0, NextStopAt - Current))

    def RequestCancellation(self) -> RuntimeOneShotProcessReceipt:
        """Request cooperative cancellation without releasing ownership."""
        if self._ResourcesClosed:
            return self._Receipt()
        self._RequestCancellationAt(monotonic())
        return self.Observe()

    def ForceTerminate(self) -> RuntimeOneShotProcessReceipt:
        """Explicitly kill only this Linux child when exact authority permits."""
        if self._ResourcesClosed:
            return self._Receipt()
        RequestedAt = monotonic()
        self._ForceTerminationRequested = True
        if (
            not self._Authority.ForceTerminationAuthorized
            or not sys.platform.startswith("linux")
        ):
            self._ForceTerminationDenied = True
            return self.Observe()
        if self._Process is None or self._Reaped:
            return self.Observe()
        Receipt = self.Observe()
        if Receipt.ProcessExitObserved:
            return Receipt
        try:
            self._Process.kill()
        except (AssertionError, OSError, ValueError) as Error:
            self._OperationalFailure = type(Error).__name__
            return self.Observe()
        self._ForceSignalSent = True
        self._ForceSignalSentAt = RequestedAt
        return self.Observe()

    def _DecodeResultBeforeReap(self, ObservedAt: float) -> None:
        if self._ResultDecoded:
            return
        self._ResultDecoded = True
        if not self._ResultCompletionObserved or self._ResultMemory is None:
            self._ResultDiagnostic = "CompletionNotObserved"
            return
        Encoded = bytes(
            self._ResultMemory.buf[:self._Limits.MaximumResultBytes]
        )
        if len(Encoded) < _RESULT_HEADER.size:
            self._ResultDiagnostic = "ResultHeaderMissing"
            return
        Magic, Version, ResultLength, InvocationBinding, ExpectedDigest = (
            _RESULT_HEADER.unpack_from(Encoded)
        )
        self._ResultEnvelopeBytesObserved = _RESULT_HEADER.size + ResultLength
        self._ResultPayloadBytesObserved = ResultLength
        if Magic != _RESULT_MAGIC:
            self._ResultDiagnostic = "ResultMagicInvalid"
            return
        if Version != _PROTOCOL_VERSION:
            self._ResultDiagnostic = "ResultVersionInvalid"
            return
        if Encoded[9:16] != b"\x00" * 7:
            self._ResultDiagnostic = "ResultReservedBytesInvalid"
            return
        if self._ResultEnvelopeBytesObserved > len(Encoded):
            self._ResultDiagnostic = "ResultLimitExceeded"
            return
        if InvocationBinding != self._InvocationBinding:
            self._ResultDiagnostic = "ResultBindingInvalid"
            return
        if any(Encoded[self._ResultEnvelopeBytesObserved:]):
            self._ResultDiagnostic = "ResultPaddingInvalid"
            return
        Result = Encoded[_RESULT_HEADER.size:self._ResultEnvelopeBytesObserved]
        if sha256(InvocationBinding + Result).digest() != ExpectedDigest:
            self._ResultDiagnostic = "ResultDigestInvalid"
            return
        self._ResultValid = True
        if ObservedAt >= self._Authority.WorkDeadlineAt:
            self._ResultDiagnostic = "LateResultDiscarded"
            return
        if self._ExitCode != 0:
            self._ResultDiagnostic = "AbnormalExitResultDiscarded"
            return
        self._CandidateResult = Result

    def ReapIfExited(self) -> RuntimeOneShotProcessReceipt:
        """Decode terminal output, then nonblockingly reap an observed exit."""
        Receipt = self.Observe()
        if self._Reaped or not Receipt.ProcessExitObserved:
            return Receipt
        self._DecodeResultBeforeReap(monotonic())
        try:
            self._Process.join(timeout=0)
        except (AssertionError, OSError, ValueError) as Error:
            self._OperationalFailure = type(Error).__name__
            return self._Receipt()
        self._ExitCode = self._Process.exitcode
        if self._ExitCode is None:
            return self._Receipt()
        ReleasedAt = monotonic()
        self._ObserveCutoffs(ReleasedAt)
        self._Reaped = True
        self._ReapedAt = ReleasedAt
        self._ReleaseAcknowledged = True
        self._ReleaseAcknowledgedAt = ReleasedAt
        self._OutstandingOwnership = False
        if (
            self._CandidateResult is not None
            and self._ResultValid
            and self._ExitCode == 0
            and not self._StartExceptionObserved
            and not self._CancellationRequested
            and not self._WorkDeadlineObserved
            and not self._ForceSignalSent
            and ReleasedAt < self._Authority.WorkDeadlineAt
        ):
            self._PublishedResult = self._CandidateResult
        elif self._CandidateResult is not None:
            if self._StartExceptionObserved:
                self._ResultDiagnostic = "StartFailureResultDiscarded"
            elif ReleasedAt >= self._Authority.WorkDeadlineAt:
                self._ResultDiagnostic = "LateResultDiscarded"
        self._CandidateResult = None
        return self._Receipt()

    def CloseReleased(self) -> None:
        """Close/unlink resources only after absence or explicit reap/release."""
        if self._ResourcesClosed:
            return
        if self._ChildExistenceUncertain or self._OutstandingOwnership:
            raise RuntimeError("one-shot process ownership is still outstanding")
        if self._Process is not None and not self._Reaped:
            raise RuntimeError("one-shot process has not been reaped")
        self._CloseChildSocketCopiesAfterStart()
        if self._ChildSocketCopies:
            raise RuntimeError("synchronization resources remain owned")
        for Memory in (self._RequestMemory, self._ResultMemory):
            if Memory is None:
                continue
            Memory.close()
            try:
                Memory.unlink()
            except FileNotFoundError:
                pass
        for SynchronizationSocket in (
            self._ReadinessSocket,
            self._CancellationSocket,
            self._CompletionSocket,
        ):
            if SynchronizationSocket is not None:
                SynchronizationSocket.close()
        if self._Process is not None:
            self._Process.close()
        self._RequestMemory = None
        self._ResultMemory = None
        self._ReadinessSocket = None
        self._CancellationSocket = None
        self._CompletionSocket = None
        self._Process = None
        self._ResourcesClosed = True


def _ValidateBoundary(
    Request: RuntimeWorkRequest,
    Authority: RuntimeWorkAuthority,
    EncodedPayload: bytes,
    Operation: Callable,
    Limits: RuntimeOneShotProcessLimits,
) -> bytes:
    if type(Request) is not RuntimeWorkRequest:
        raise TypeError("Request must be an exact RuntimeWorkRequest")
    if type(Authority) is not RuntimeWorkAuthority:
        raise TypeError("Authority must be an exact RuntimeWorkAuthority")
    if type(Limits) is not RuntimeOneShotProcessLimits:
        raise TypeError("Limits must be exact RuntimeOneShotProcessLimits")
    if type(Request.DeadlineAt) is not float or not isfinite(Request.DeadlineAt):
        raise TypeError("Request.DeadlineAt must be an exact finite float")
    if Request.DeadlineAt != Authority.WorkDeadlineAt:
        raise ValueError("request and authority work deadlines must match exactly")
    if Authority.CleanupCutoffAt < Authority.WorkDeadlineAt:
        raise ValueError("cleanup cutoff before work deadline is unsupported")
    if Request.Lifecycle is not RuntimeLifecycle.Queued:
        raise ValueError("one-shot process requires a queued request")
    if type(EncodedPayload) is not bytes:
        raise TypeError("EncodedPayload must be exact bytes")
    return _OperationReference(Operation)


def StartRuntimeOneShotProcess(
    Request: RuntimeWorkRequest,
    Authority: RuntimeWorkAuthority,
    EncodedPayload: bytes,
    Operation: Callable[[bytes, Callable[[], bool]], bytes],
    Limits: RuntimeOneShotProcessLimits,
) -> RuntimeOneShotProcessHandle:
    """Allocate and start one owned spawned process under exact v1 authority."""
    if not sys.platform.startswith("linux"):
        raise NotImplementedError("one-shot process supervision is Linux-only")
    OperationReference = _ValidateBoundary(
        Request,
        Authority,
        EncodedPayload,
        Operation,
        Limits,
    )
    RequestEnvelopeBytes = (
        _REQUEST_HEADER.size + len(OperationReference) + len(EncodedPayload)
    )
    if RequestEnvelopeBytes > Limits.MaximumRequestBytes:
        raise ValueError("encoded request exceeds MaximumRequestBytes")
    InvocationBinding = token_bytes(32)
    Handle = RuntimeOneShotProcessHandle(
        Request,
        Authority,
        Limits,
        RequestEnvelopeBytes,
        InvocationBinding,
    )
    Current = monotonic()
    if Current >= Authority.WorkDeadlineAt:
        Handle._ExactUnstarted = True
        Handle._UnstartedReason = "WorkDeadlineExpired"
        Handle._WorkDeadlineObserved = True
        Handle._WorkDeadlineObservedAt = Current
        Handle._ResourcesClosed = True
        return Handle
    if Request.WorkCap == 0:
        Handle._ExactUnstarted = True
        Handle._UnstartedReason = "WorkCapExhausted"
        Handle._ResourcesClosed = True
        return Handle

    Context = multiprocessing.get_context("spawn")
    CreatedMemory = []
    CreatedSockets = []
    try:
        RequestMemory = SharedMemory(
            create=True,
            size=Limits.MaximumRequestBytes,
        )
        CreatedMemory.append(RequestMemory)
        ResultMemory = SharedMemory(
            create=True,
            size=Limits.MaximumResultBytes,
        )
        CreatedMemory.append(ResultMemory)
        RequestMemory.buf[:Limits.MaximumRequestBytes] = (
            b"\x00" * Limits.MaximumRequestBytes
        )
        ResultMemory.buf[:Limits.MaximumResultBytes] = (
            b"\x00" * Limits.MaximumResultBytes
        )
        ReadinessParent, ReadinessChild = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )
        CreatedSockets.extend((ReadinessParent, ReadinessChild))
        CancellationParent, CancellationChild = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )
        CreatedSockets.extend((CancellationParent, CancellationChild))
        CompletionParent, CompletionChild = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )
        CreatedSockets.extend((CompletionParent, CompletionChild))
        for SynchronizationSocket in CreatedSockets:
            SynchronizationSocket.setblocking(False)
        DeadlineBytes = pack("!d", Authority.WorkDeadlineAt)
        Body = OperationReference + EncodedPayload
        _REQUEST_HEADER.pack_into(
            RequestMemory.buf,
            0,
            _REQUEST_MAGIC,
            _PROTOCOL_VERSION,
            len(OperationReference),
            Authority.WorkDeadlineAt,
            len(EncodedPayload),
            InvocationBinding,
            sha256(DeadlineBytes + InvocationBinding + Body).digest(),
        )
        RequestMemory.buf[
            _REQUEST_HEADER.size:RequestEnvelopeBytes
        ] = Body
        Handle._RequestMemory = RequestMemory
        Handle._ResultMemory = ResultMemory
        Handle._ReadinessSocket = ReadinessParent
        Handle._CancellationSocket = CancellationParent
        Handle._CompletionSocket = CompletionParent
        Handle._ChildSocketCopies = (
            ReadinessChild,
            CancellationChild,
            CompletionChild,
        )
        Handle._Process = Context.Process(
            target=_RunRuntimeOneShotChild,
            args=(
                RequestMemory.name,
                ResultMemory.name,
                Limits.MaximumRequestBytes,
                Limits.MaximumResultBytes,
                ReadinessChild,
                CancellationChild,
                CompletionChild,
            ),
            name="RuntimeOneShot",
            daemon=False,
        )
        Handle._OutstandingOwnership = True
    except BaseException as Error:
        for SynchronizationSocket in reversed(CreatedSockets):
            SynchronizationSocket.close()
        for Memory in reversed(CreatedMemory):
            Memory.close()
            try:
                Memory.unlink()
            except FileNotFoundError:
                pass
        Handle._RequestMemory = None
        Handle._ResultMemory = None
        Handle._ReadinessSocket = None
        Handle._CancellationSocket = None
        Handle._CompletionSocket = None
        Handle._ChildSocketCopies = ()
        Handle._Process = None
        Handle._ExactUnstarted = True
        Handle._UnstartedReason = "AllocationFailure"
        Handle._OperationalFailure = type(Error).__name__
        Handle._OutstandingOwnership = False
        Handle._ResourcesClosed = True
        if not isinstance(Error, Exception):
            raise
        return Handle

    Handle._StartRequested = True
    try:
        Handle._Process.start()
    except BaseException as Error:
        Handle._OperationalFailure = type(Error).__name__
        Handle._StartExceptionObserved = True
        Handle._ChildExistenceUncertain = True
        return Handle
    finally:
        Handle._CloseChildSocketCopiesAfterStart()
    Handle._StartedPid = Handle._Process.pid
    Current = monotonic()
    Handle._ObserveCutoffs(Current)
    return Handle

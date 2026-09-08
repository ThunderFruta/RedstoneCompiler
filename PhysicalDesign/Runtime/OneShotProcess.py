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


def BuildRuntimeOneShotProcessLimits(
    Operation: Callable[[bytes, Callable[[], bool]], bytes],
    EncodedPayloadBytes: int,
    MaximumResultPayloadBytes: int,
) -> RuntimeOneShotProcessLimits:
    """Build exact transport capacities around caller-bounded payload bytes."""
    if type(EncodedPayloadBytes) is not int or EncodedPayloadBytes < 0:
        raise ValueError("EncodedPayloadBytes must be a non-negative integer")
    if type(MaximumResultPayloadBytes) is not int or MaximumResultPayloadBytes < 0:
        raise ValueError(
            "MaximumResultPayloadBytes must be a non-negative integer"
        )
    return RuntimeOneShotProcessLimits(
        MaximumRequestBytes=(
            _REQUEST_HEADER.size
            + len(_OperationReference(Operation))
            + EncodedPayloadBytes
        ),
        MaximumResultBytes=(
            _RESULT_HEADER.size + MaximumResultPayloadBytes
        ),
    )


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
    ParentControl: BaseException | None
    InvocationBindingIdentity: str
    SynchronizationResourceIdentities: tuple[int, ...]
    SynchronizationCleanupFailures: tuple[str, ...]
    AllocationCleanupFailures: tuple[str, ...]
    ResourceCleanupFailures: tuple[str, ...]
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
    MaximumCooperativeGraceSeconds: float
    PolicyIdentity: str
    PressureIdentity: str
    WorkDeadlineObserved: bool
    WorkDeadlineObservedAt: float | None
    CancellationRequested: bool
    CancellationRequestedAt: float | None
    ForceTerminationEligibleAt: float | None
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
        self._AllocationCleanupFailures: tuple[str, ...] = ()
        self._ResourceCleanupFailures: tuple[str, ...] = ()
        self._AllocationResources: list[tuple[str, str, object]] = []

        self._StartRequested = False
        self._StartedPid: int | None = None
        self._ReadinessObserved = False
        self._ReadinessObservedAt: float | None = None
        self._ExactUnstarted = False
        self._UnstartedReason: str | None = None
        self._StartExceptionObserved = False
        self._ChildExistenceUncertain = False
        self._OperationalFailure: str | None = None
        self._ParentControl: BaseException | None = None
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
        self._LastReceipt: RuntimeOneShotProcessReceipt | None = None
        self._Receipt()
        self._AdmissionReceipt = self._LastReceipt

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
        Receipt = RuntimeOneShotProcessReceipt(
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
            ParentControl=self._ParentControl,
            InvocationBindingIdentity=self._InvocationBinding.hex(),
            SynchronizationResourceIdentities=(
                self._SynchronizationResourceIdentities()
            ),
            SynchronizationCleanupFailures=(
                self._SynchronizationCleanupFailures
            ),
            AllocationCleanupFailures=self._AllocationCleanupFailures,
            ResourceCleanupFailures=self._ResourceCleanupFailures,
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
            MaximumCooperativeGraceSeconds=(
                self._Authority.MaximumCooperativeGraceSeconds
            ),
            PolicyIdentity=self._Authority.PolicyIdentity,
            PressureIdentity=self._Authority.PressureIdentity,
            WorkDeadlineObserved=self._WorkDeadlineObserved,
            WorkDeadlineObservedAt=self._WorkDeadlineObservedAt,
            CancellationRequested=self._CancellationRequested,
            CancellationRequestedAt=self._CancellationRequestedAt,
            ForceTerminationEligibleAt=(
                None
                if (
                    self._CancellationRequestedAt is None
                    or not self._Authority.ForceTerminationAuthorized
                )
                else self._Authority.ForceTerminationEligibleAt(
                    self._CancellationRequestedAt
                )
            ),
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
        self._LastReceipt = Receipt
        return Receipt

    @property
    def LastReceipt(self) -> RuntimeOneShotProcessReceipt:
        """Return the last receipt captured by an in-authority handle action."""
        if self._LastReceipt is None:
            return self._Receipt()
        return self._LastReceipt

    @property
    def AdmissionReceipt(self) -> RuntimeOneShotProcessReceipt:
        """Return the receipt captured before the latest startup action."""
        return self._AdmissionReceipt

    def _CaptureAdmissionReceipt(self) -> None:
        self._AdmissionReceipt = self._Receipt()

    def _CleanupAllocationResources(self) -> None:
        """Attempt every pre-start cleanup and retain any unproved ownership."""
        CleanupOrder = list(reversed(self._AllocationResources))
        for Kind, Name, Resource in CleanupOrder:
            if monotonic() >= self._Authority.CleanupCutoffAt:
                return
            Released = True
            CloseFailure = None
            CloseParentControl = None
            try:
                Resource.close()
            except BaseException as Error:
                Released = False
                CloseFailure = f"{Name}.close:{type(Error).__name__}"
                if not isinstance(Error, Exception) and self._ParentControl is None:
                    self._ParentControl = Error
                    CloseParentControl = Error
            Current = monotonic()
            if Current >= self._Authority.CleanupCutoffAt:
                if CloseParentControl is not None:
                    self._AllocationCleanupFailures += (CloseFailure,)
                    self._CleanupCutoffBreached = True
                    self._CleanupCutoffObservedAt = Current
                    self._Receipt()
                return
            if CloseFailure is not None:
                self._AllocationCleanupFailures += (CloseFailure,)
                self._Receipt()
            if Kind == "memory":
                UnlinkFailure = None
                UnlinkParentControl = None
                try:
                    Resource.unlink()
                except FileNotFoundError:
                    pass
                except BaseException as Error:
                    Released = False
                    UnlinkFailure = f"{Name}.unlink:{type(Error).__name__}"
                    if (
                        not isinstance(Error, Exception)
                        and self._ParentControl is None
                    ):
                        self._ParentControl = Error
                        UnlinkParentControl = Error
                Current = monotonic()
                if Current >= self._Authority.CleanupCutoffAt:
                    if UnlinkParentControl is not None:
                        self._AllocationCleanupFailures += (UnlinkFailure,)
                        self._CleanupCutoffBreached = True
                        self._CleanupCutoffObservedAt = Current
                        self._Receipt()
                    return
                if UnlinkFailure is not None:
                    self._AllocationCleanupFailures += (UnlinkFailure,)
                    self._Receipt()
            if Released:
                RetainedResourceIdentities = {
                    id(OwnedResource)
                    for _OwnedKind, _OwnedName, OwnedResource
                    in self._AllocationResources
                    if id(OwnedResource) != id(Resource)
                }
                self._ApplyRetainedResourceLedger(
                    RetainedResourceIdentities
                )
                self._Receipt()

        if monotonic() >= self._Authority.CleanupCutoffAt:
            return
        self._OutstandingOwnership = bool(self._AllocationResources)
        self._ResourcesClosed = not self._AllocationResources
        if self._ResourcesClosed:
            self._ReleaseAcknowledged = True
            self._ReleaseAcknowledgedAt = monotonic()
        self._Receipt()

    def _EnsureResourceLedger(self) -> None:
        if self._AllocationResources:
            return
        Seen = set()
        for Kind, Name, Resource in (
            ("memory", "RequestSharedMemory", self._RequestMemory),
            ("memory", "ResultSharedMemory", self._ResultMemory),
            ("socket", "ReadinessParentSocket", self._ReadinessSocket),
            ("socket", "CancellationParentSocket", self._CancellationSocket),
            ("socket", "CompletionParentSocket", self._CompletionSocket),
            *(
                ("socket", f"ChildSocket{Index}", Resource)
                for Index, Resource in enumerate(self._ChildSocketCopies)
            ),
            ("process", "Process", self._Process),
        ):
            if Resource is None or id(Resource) in Seen:
                continue
            Seen.add(id(Resource))
            self._AllocationResources.append((Kind, Name, Resource))

    def _ApplyRetainedResourceLedger(
        self,
        RetainedResourceIdentities: set[int],
    ) -> None:
        self._AllocationResources = [
            Resource
            for Resource in self._AllocationResources
            if id(Resource[2]) in RetainedResourceIdentities
        ]
        self._RequestMemory = None
        self._ResultMemory = None
        self._ReadinessSocket = None
        self._CancellationSocket = None
        self._CompletionSocket = None
        self._Process = None
        ChildSockets = []
        for _Kind, Name, Resource in self._AllocationResources:
            if Name == "RequestSharedMemory":
                self._RequestMemory = Resource
            elif Name == "ResultSharedMemory":
                self._ResultMemory = Resource
            elif Name == "ReadinessParentSocket":
                self._ReadinessSocket = Resource
            elif Name == "CancellationParentSocket":
                self._CancellationSocket = Resource
            elif Name == "CompletionParentSocket":
                self._CompletionSocket = Resource
            elif Name == "Process":
                self._Process = Resource
            else:
                ChildSockets.append(Resource)
        self._ChildSocketCopies = tuple(ChildSockets)

    def _CleanupReleasedResources(self) -> None:
        """Exhaust a released handle's resources within cleanup authority."""
        RecoveryAfterBreach = self._CleanupCutoffBreached
        self._EnsureResourceLedger()
        CleanupOrder = list(self._AllocationResources)
        for Kind, Name, Resource in CleanupOrder:
            if (
                not RecoveryAfterBreach
                and monotonic() >= self._Authority.CleanupCutoffAt
            ):
                raise RuntimeError("one-shot cleanup cutoff reached")
            Released = True
            CloseFailure = None
            CloseParentControl = None
            try:
                Resource.close()
            except BaseException as Error:
                Released = False
                CloseFailure = f"{Name}.close:{type(Error).__name__}"
                if not isinstance(Error, Exception) and self._ParentControl is None:
                    self._ParentControl = Error
                    CloseParentControl = Error
            Current = monotonic()
            if (
                not RecoveryAfterBreach
                and Current >= self._Authority.CleanupCutoffAt
            ):
                if CloseParentControl is not None:
                    self._ResourceCleanupFailures += (CloseFailure,)
                    self._CleanupCutoffBreached = True
                    self._CleanupCutoffObservedAt = Current
                    self._Receipt()
                raise RuntimeError("one-shot cleanup cutoff crossed")
            if CloseFailure is not None:
                self._ResourceCleanupFailures += (CloseFailure,)
                self._Receipt()
            if Kind == "memory":
                UnlinkFailure = None
                UnlinkParentControl = None
                try:
                    Resource.unlink()
                except FileNotFoundError:
                    pass
                except BaseException as Error:
                    Released = False
                    UnlinkFailure = f"{Name}.unlink:{type(Error).__name__}"
                    if (
                        not isinstance(Error, Exception)
                        and self._ParentControl is None
                    ):
                        self._ParentControl = Error
                        UnlinkParentControl = Error
                Current = monotonic()
                if (
                    not RecoveryAfterBreach
                    and Current >= self._Authority.CleanupCutoffAt
                ):
                    if UnlinkParentControl is not None:
                        self._ResourceCleanupFailures += (UnlinkFailure,)
                        self._CleanupCutoffBreached = True
                        self._CleanupCutoffObservedAt = Current
                        self._Receipt()
                    raise RuntimeError("one-shot cleanup cutoff crossed")
                if UnlinkFailure is not None:
                    self._ResourceCleanupFailures += (UnlinkFailure,)
                    self._Receipt()
            if Released:
                RetainedResourceIdentities = {
                    id(OwnedResource)
                    for _OwnedKind, _OwnedName, OwnedResource
                    in self._AllocationResources
                    if id(OwnedResource) != id(Resource)
                }
                self._ApplyRetainedResourceLedger(
                    RetainedResourceIdentities
                )
                self._Receipt()
        if (
            not RecoveryAfterBreach
            and monotonic() >= self._Authority.CleanupCutoffAt
        ):
            raise RuntimeError("one-shot cleanup cutoff crossed")
        self._ResourcesClosed = not self._AllocationResources
        self._OutstandingOwnership = bool(self._AllocationResources)
        if self._ResourcesClosed:
            if not self._ReleaseAcknowledged:
                self._ReleaseAcknowledged = True
                self._ReleaseAcknowledgedAt = monotonic()
        self._Receipt()
        if self._AllocationResources:
            raise RuntimeError("one-shot resource cleanup remains incomplete")

    def _CloseChildSocketCopiesAfterStart(self) -> bool:
        OriginalCopies = self._ChildSocketCopies
        for SynchronizationSocket in OriginalCopies:
            if monotonic() >= self._Authority.CleanupCutoffAt:
                return False
            Failure = None
            ParentControl = None
            try:
                SynchronizationSocket.close()
            except BaseException as Error:
                Failure = type(Error).__name__
                if not isinstance(Error, Exception) and self._ParentControl is None:
                    self._ParentControl = Error
                    ParentControl = Error
            Current = monotonic()
            if Current >= self._Authority.CleanupCutoffAt:
                if ParentControl is not None:
                    self._SynchronizationCleanupFailures += (Failure,)
                    self._CleanupCutoffBreached = True
                    self._CleanupCutoffObservedAt = Current
                    self._Receipt()
                return False
            if Failure is not None:
                self._SynchronizationCleanupFailures += (Failure,)
                self._Receipt()
                continue
            self._ChildSocketCopies = tuple(
                Resource
                for Resource in self._ChildSocketCopies
                if id(Resource) != id(SynchronizationSocket)
            )
            self._AllocationResources = [
                Resource
                for Resource in self._AllocationResources
                if id(Resource[2]) != id(SynchronizationSocket)
            ]
            self._Receipt()
        if self._ChildSocketCopies and self._OperationalFailure is None:
            self._OperationalFailure = "SynchronizationCleanupFailure"
            self._Receipt()
        return True

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
            except BaseException as Error:
                self._RecordExternalActionControl(
                    "CancellationSignalFailure",
                    Error,
                )
                raise
            if SentBytes != 1:
                self._OperationalFailure = "CancellationSignalUnavailable"

    def _ObserveCutoffs(self, ObservedAt: float) -> None:
        if (
            not self._CleanupCutoffBreached
            and self._OutstandingOwnership
            and ObservedAt >= self._Authority.CleanupCutoffAt
        ):
            self._CleanupCutoffBreached = True
            self._CleanupCutoffObservedAt = ObservedAt
            return
        if (
            not self._WorkDeadlineObserved
            and ObservedAt >= self._Authority.WorkDeadlineAt
        ):
            self._WorkDeadlineObserved = True
            self._WorkDeadlineObservedAt = ObservedAt
            self._RequestCancellationAt(ObservedAt)

    def _OriginalCleanupAuthorityCrossed(
        self,
        RecoveryAfterBreach: bool,
    ) -> bool:
        if RecoveryAfterBreach:
            return False
        Current = monotonic()
        if Current < self._Authority.CleanupCutoffAt:
            return False
        if not self._CleanupCutoffBreached:
            self._CleanupCutoffBreached = True
            self._CleanupCutoffObservedAt = Current
        return True

    def _RecordExternalActionControl(
        self,
        Action: str,
        Error: BaseException,
    ) -> None:
        if not isinstance(Error, Exception) and self._ParentControl is None:
            self._ParentControl = Error
        self._OperationalFailure = f"{Action}:{type(Error).__name__}"
        Current = monotonic()
        if Current >= self._Authority.CleanupCutoffAt:
            self._CleanupCutoffBreached = True
            self._CleanupCutoffObservedAt = Current
        self._Receipt()

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
        RecoveryAfterBreach = self._CleanupCutoffBreached
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        ObservedAt = monotonic()
        self._ObserveCutoffs(ObservedAt)
        if self._CleanupCutoffBreached and not RecoveryAfterBreach:
            return self._Receipt()
        if self._Process is None:
            return self._Receipt()

        try:
            Pid = self._Process.pid
        except (AssertionError, ValueError):
            Pid = None
        except BaseException as Error:
            self._RecordExternalActionControl("ProcessPidObservation", Error)
            raise
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        if Pid is not None:
            self._StartedPid = Pid
            self._ChildExistenceUncertain = False

        if not self._ReadinessObserved:
            try:
                ReadinessObserved = self._ReceiveControlToken(
                    self._ReadinessSocket,
                    b"R",
                )
            except BaseException as Error:
                self._RecordExternalActionControl(
                    "ReadinessObservation",
                    Error,
                )
                raise
            if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
                return self._Receipt()
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
        except BaseException as Error:
            self._RecordExternalActionControl("ProcessSentinelObservation", Error)
            raise
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        if SentinelReady and not self._ProcessExitObserved:
            self._ProcessExitObserved = True
            self._ProcessExitObservedAt = ObservedAt
            try:
                self._ExitCode = self._Process.exitcode
            except BaseException as Error:
                self._RecordExternalActionControl(
                    "ProcessExitCodeObservation",
                    Error,
                )
                raise
        elif self._ProcessExitObserved:
            try:
                self._ExitCode = self._Process.exitcode
            except BaseException as Error:
                self._RecordExternalActionControl(
                    "ProcessExitCodeObservation",
                    Error,
                )
                raise

        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()

        if self._ProcessExitObserved and not self._ResultCompletionObserved:
            try:
                CompletionObserved = self._ReceiveControlToken(
                    self._CompletionSocket,
                    b"D",
                )
            except BaseException as Error:
                self._RecordExternalActionControl(
                    "CompletionObservation",
                    Error,
                )
                raise
            if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
                return self._Receipt()
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
            Current = monotonic()
            if Current >= EffectiveCutoffAt:
                if (
                    Current >= self._Authority.CleanupCutoffAt
                    and not self._CleanupCutoffBreached
                ):
                    self._CleanupCutoffBreached = True
                    self._CleanupCutoffObservedAt = Current
                    return self._Receipt()
                return self.LastReceipt
            CurrentReceipt = self.LastReceipt
            if (
                self._Authority.ForceTerminationAuthorized
                and CurrentReceipt.ForceTerminationEligibleAt is not None
                and Current >= CurrentReceipt.ForceTerminationEligibleAt
                and not CurrentReceipt.ForceSignalSent
                and CurrentReceipt.OutstandingOwnership
                and not CurrentReceipt.ProcessExitObserved
            ):
                return CurrentReceipt
            PreviousReceipt = self.LastReceipt
            Receipt = self.Observe()
            Current = monotonic()
            if Current >= EffectiveCutoffAt:
                if (
                    Current >= self._Authority.CleanupCutoffAt
                    and not self._CleanupCutoffBreached
                ):
                    self._CleanupCutoffBreached = True
                    self._CleanupCutoffObservedAt = Current
                    return self._Receipt()
                return PreviousReceipt
            if (
                self._Authority.ForceTerminationAuthorized
                and Receipt.ForceTerminationEligibleAt is not None
                and Current >= Receipt.ForceTerminationEligibleAt
                and not Receipt.ForceSignalSent
                and Receipt.OutstandingOwnership
                and not Receipt.ProcessExitObserved
            ):
                return Receipt
            if Receipt.ProcessExitObserved or not Receipt.OutstandingOwnership:
                return Receipt
            Current = monotonic()
            if Current >= EffectiveCutoffAt:
                return Receipt
            NextStopAt = EffectiveCutoffAt
            if not self._WorkDeadlineObserved:
                NextStopAt = min(NextStopAt, self._Authority.WorkDeadlineAt)
            if (
                Receipt.ForceTerminationEligibleAt is not None
                and Current < Receipt.ForceTerminationEligibleAt
            ):
                NextStopAt = min(
                    NextStopAt,
                    Receipt.ForceTerminationEligibleAt,
                )
            try:
                Sentinel = self._Process.sentinel
            except (AssertionError, OSError, ValueError):
                return self._Receipt()
            except BaseException as Error:
                self._RecordExternalActionControl(
                    "ProcessSentinelObservation",
                    Error,
                )
                raise
            try:
                wait((Sentinel,), timeout=max(0.0, NextStopAt - Current))
            except BaseException as Error:
                self._RecordExternalActionControl(
                    "ProcessWaitFailure",
                    Error,
                )
                raise

    def RequestCancellation(self) -> RuntimeOneShotProcessReceipt:
        """Request cooperative cancellation without releasing ownership."""
        if self._ResourcesClosed:
            return self._Receipt()
        RecoveryAfterBreach = self._CleanupCutoffBreached
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        self._RequestCancellationAt(monotonic())
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        return self.Observe()

    def ForceTerminate(self) -> RuntimeOneShotProcessReceipt:
        """Explicitly kill only this Linux child when exact authority permits."""
        if self._ResourcesClosed:
            return self._Receipt()
        RecoveryAfterBreach = self._CleanupCutoffBreached
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        RequestedAt = monotonic()
        self._ForceTerminationRequested = True
        if (
            not self._Authority.ForceTerminationAuthorized
            or not sys.platform.startswith("linux")
        ):
            self._ForceTerminationDenied = True
            return self.Observe()
        ForceEligibleAt = (
            None
            if self._CancellationRequestedAt is None
            else self._Authority.ForceTerminationEligibleAt(
                self._CancellationRequestedAt
            )
        )
        if ForceEligibleAt is None or RequestedAt < ForceEligibleAt:
            self._ForceTerminationDenied = True
            return self.Observe()
        if self._Process is None or self._Reaped:
            return self.Observe()
        Receipt = self.Observe()
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        if self._CleanupCutoffBreached and not RecoveryAfterBreach:
            return Receipt
        if Receipt.ProcessExitObserved:
            return Receipt
        try:
            self._Process.kill()
        except (AssertionError, OSError, ValueError) as Error:
            if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
                return self._Receipt()
            self._OperationalFailure = type(Error).__name__
            return self.Observe()
        except BaseException as Error:
            self._RecordExternalActionControl(
                "ForceTerminationFailure",
                Error,
            )
            raise
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
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
        if self._ResourcesClosed:
            return self._Receipt()
        RecoveryAfterBreach = self._CleanupCutoffBreached
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        Receipt = self.Observe()
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        if self._CleanupCutoffBreached and not RecoveryAfterBreach:
            return Receipt
        if self._Reaped or not Receipt.ProcessExitObserved:
            return Receipt
        self._DecodeResultBeforeReap(monotonic())
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        try:
            self._Process.join(timeout=0)
        except (AssertionError, OSError, ValueError) as Error:
            self._OperationalFailure = type(Error).__name__
            return self._Receipt()
        except BaseException as Error:
            self._RecordExternalActionControl("ProcessReapFailure", Error)
            raise
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        try:
            ExitCode = self._Process.exitcode
        except BaseException as Error:
            self._RecordExternalActionControl(
                "ProcessExitCodeObservation",
                Error,
            )
            raise
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        self._ExitCode = ExitCode
        if ExitCode is None:
            return self._Receipt()
        ReleasedAt = monotonic()
        if self._OriginalCleanupAuthorityCrossed(RecoveryAfterBreach):
            return self._Receipt()
        self._ObserveCutoffs(ReleasedAt)
        self._Reaped = True
        self._ReapedAt = ReleasedAt
        self._OutstandingOwnership = not self._ResourcesClosed
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
        if (
            self._ExactUnstarted
            and self._UnstartedReason == "AllocationFailure"
            and self._Process is None
            and self._AllocationResources
        ):
            self._CleanupAllocationResources()
            if self._AllocationResources:
                raise RuntimeError("one-shot allocation cleanup remains incomplete")
            return
        if (
            self._ChildExistenceUncertain
            or (self._OutstandingOwnership and not self._Reaped)
        ):
            raise RuntimeError("one-shot process ownership is still outstanding")
        if self._Process is not None and not self._Reaped:
            raise RuntimeError("one-shot process has not been reaped")
        self._CleanupReleasedResources()


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
    """Allocate and start one owned spawned process under exact v2 authority."""
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

    def StartupCutoffReached() -> bool:
        CurrentAt = monotonic()
        if CurrentAt < Authority.CleanupCutoffAt:
            return False
        if not Handle._StartRequested:
            Handle._ExactUnstarted = True
            Handle._UnstartedReason = "CleanupCutoffExpired"
        Handle._CleanupCutoffBreached = True
        Handle._CleanupCutoffObservedAt = CurrentAt
        Handle._OutstandingOwnership = bool(Handle._AllocationResources)
        Handle._ResourcesClosed = not Handle._AllocationResources
        if Handle._ResourcesClosed:
            Handle._ReleaseAcknowledged = True
            Handle._ReleaseAcknowledgedAt = CurrentAt
        Handle._Receipt()
        return True

    Current = monotonic()
    if Current >= Authority.WorkDeadlineAt:
        Handle._ExactUnstarted = True
        Handle._UnstartedReason = "WorkDeadlineExpired"
        Handle._WorkDeadlineObserved = True
        Handle._WorkDeadlineObservedAt = Current
        Handle._ResourcesClosed = True
        Handle._ReleaseAcknowledged = True
        Handle._ReleaseAcknowledgedAt = Current
        Handle._Receipt()
        return Handle
    if Request.WorkCap == 0:
        Handle._ExactUnstarted = True
        Handle._UnstartedReason = "WorkCapExhausted"
        Handle._ResourcesClosed = True
        Handle._ReleaseAcknowledged = True
        Handle._ReleaseAcknowledgedAt = Current
        Handle._Receipt()
        return Handle

    Context = multiprocessing.get_context("spawn")
    try:
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
        RequestMemory = SharedMemory(
            create=True,
            size=Limits.MaximumRequestBytes,
        )
        Handle._RequestMemory = RequestMemory
        Handle._AllocationResources.append((
            "memory",
            "RequestSharedMemory",
            RequestMemory,
        ))
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
        ResultMemory = SharedMemory(
            create=True,
            size=Limits.MaximumResultBytes,
        )
        Handle._ResultMemory = ResultMemory
        Handle._AllocationResources.append((
            "memory",
            "ResultSharedMemory",
            ResultMemory,
        ))
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
        RequestMemory.buf[:Limits.MaximumRequestBytes] = (
            b"\x00" * Limits.MaximumRequestBytes
        )
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
        ResultMemory.buf[:Limits.MaximumResultBytes] = (
            b"\x00" * Limits.MaximumResultBytes
        )
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
        ReadinessParent, ReadinessChild = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )
        Handle._ReadinessSocket = ReadinessParent
        Handle._ChildSocketCopies += (ReadinessChild,)
        Handle._AllocationResources.extend((
            ("socket", "ReadinessParentSocket", ReadinessParent),
            ("socket", "ReadinessChildSocket", ReadinessChild),
        ))
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
        CancellationParent, CancellationChild = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )
        Handle._CancellationSocket = CancellationParent
        Handle._ChildSocketCopies += (CancellationChild,)
        Handle._AllocationResources.extend((
            ("socket", "CancellationParentSocket", CancellationParent),
            ("socket", "CancellationChildSocket", CancellationChild),
        ))
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
        CompletionParent, CompletionChild = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_DGRAM,
        )
        Handle._CompletionSocket = CompletionParent
        Handle._ChildSocketCopies += (CompletionChild,)
        Handle._AllocationResources.extend((
            ("socket", "CompletionParentSocket", CompletionParent),
            ("socket", "CompletionChildSocket", CompletionChild),
        ))
        if StartupCutoffReached():
            return Handle
        for _Kind, _Name, SynchronizationSocket in (
            Resource
            for Resource in Handle._AllocationResources
            if Resource[0] == "socket"
        ):
            if StartupCutoffReached():
                return Handle
            Handle._CaptureAdmissionReceipt()
            SynchronizationSocket.setblocking(False)
            if StartupCutoffReached():
                return Handle
        DeadlineBytes = pack("!d", Authority.WorkDeadlineAt)
        Body = OperationReference + EncodedPayload
        Handle._CaptureAdmissionReceipt()
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
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
        RequestMemory.buf[
            _REQUEST_HEADER.size:RequestEnvelopeBytes
        ] = Body
        if StartupCutoffReached():
            return Handle
        Handle._CaptureAdmissionReceipt()
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
        Handle._AllocationResources.append((
            "process",
            "Process",
            Handle._Process,
        ))
        Handle._OutstandingOwnership = True
        if StartupCutoffReached():
            return Handle
    except BaseException as Error:
        Handle._Process = None
        Handle._ExactUnstarted = True
        Handle._UnstartedReason = "AllocationFailure"
        Handle._OperationalFailure = type(Error).__name__
        if not isinstance(Error, Exception):
            Handle._ParentControl = Error
        Current = monotonic()
        if Current >= Authority.CleanupCutoffAt:
            Handle._CleanupCutoffBreached = True
            Handle._CleanupCutoffObservedAt = Current
        Handle._OutstandingOwnership = bool(Handle._AllocationResources)
        Handle._ResourcesClosed = not Handle._AllocationResources
        Handle._Receipt()
        Handle._CleanupAllocationResources()
        return Handle

    if StartupCutoffReached():
        return Handle
    Handle._StartRequested = True
    Handle._Receipt()
    Handle._CaptureAdmissionReceipt()
    try:
        Handle._Process.start()
    except BaseException as Error:
        Handle._OperationalFailure = type(Error).__name__
        Handle._StartExceptionObserved = True
        Handle._ChildExistenceUncertain = True
        if not isinstance(Error, Exception):
            Handle._ParentControl = Error
        if StartupCutoffReached():
            return Handle
        Handle._Receipt()
        Handle._CaptureAdmissionReceipt()
        Handle._CloseChildSocketCopiesAfterStart()
        return Handle
    if StartupCutoffReached():
        return Handle
    Handle._CaptureAdmissionReceipt()
    ChildSocketCleanupWithinAuthority = (
        Handle._CloseChildSocketCopiesAfterStart()
    )
    if not ChildSocketCleanupWithinAuthority:
        return Handle
    if StartupCutoffReached():
        return Handle
    Handle._CaptureAdmissionReceipt()
    StartedPid = Handle._Process.pid
    if StartupCutoffReached():
        return Handle
    Handle._StartedPid = StartedPid
    Current = monotonic()
    Handle._ObserveCutoffs(Current)
    Handle._Receipt()
    return Handle

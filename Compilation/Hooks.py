"""Opt-in bounded compiler observations and offline trace diagnosis."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
import os
import re
import stat
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from queue import Queue, Empty, Full
from threading import Thread, Event
from time import monotonic
from typing import Callable, Iterator, Any
from uuid import uuid4


SchemaVersion = "compiler-action-trace-v1"
IndexSchemaVersion = "compiler-action-index-v1"
LegacyStageSchemaVersion = "compiler-action-stage-v1"
StageSchemaVersion = "compiler-action-stage-v2"
DefaultStorageBytes = 8_000_000
DeliveringHooks: ContextVar[object] = ContextVar("DeliveringHooks", default=None)
ActiveHooks: ContextVar[CompilerHooks | None] = ContextVar("CompilerHooks", default=None)


def _CopyFields(Value: object, Budget: list[int], Depth: int = 0) -> object:
    """Copy only bounded JSON values; never expose compiler objects to observers."""
    Budget[0] -= 1
    if Budget[0] < 0 or Depth > 8:
        raise ValueError("observation exceeds field budget")
    if Value is None or type(Value) in (bool, int, float, str):
        if isinstance(Value, str) and len(Value) > 2048:
            raise ValueError("observation string exceeds field budget")
        if type(Value) is int and Value.bit_length() > 256:
            raise ValueError("observation integer exceeds field budget")
        return Value
    if type(Value) is dict:
        Result = {}
        for Key, Item in Value.items():
            if type(Key) is not str or len(Key) > 128:
                raise ValueError("observation requires short string keys")
            Result[Key] = _CopyFields(Item, Budget, Depth + 1)
        return Result
    if type(Value) in (list, tuple):
        return [_CopyFields(Item, Budget, Depth + 1) for Item in Value]
    raise ValueError("observation requires JSON values")


class CompilerHooks:
    """Capture selected stages. Live callbacks must return promptly.

    Limits cover retained events and their serialized bytes. Rejected events and
    callback errors are counted separately; traces never certify compiler success.
    Use a new instance for each run. Context is local to the calling execution;
    worker processes require their own explicitly supplied hooks.
    """

    def __init__(self, *, Stages: tuple[str, ...] = (), MaxEvents: int = 4096,
                 MaxBytes: int = 2_000_000, MaxStorageBytes: int = DefaultStorageBytes,
                 Callback: Callable[[dict[str, object]], None] | None = None) -> None:
        if type(MaxEvents) is not int or MaxEvents < 1 or type(MaxBytes) is not int or MaxBytes < 1:
            raise ValueError("hook limits must be positive integers")
        if type(MaxStorageBytes) is not int or MaxStorageBytes < 1:
            raise ValueError("hook storage limit must be a positive integer")
        if len(Stages) > MaxEvents:
            raise ValueError("hook selector count exceeds event limit")
        if any(type(Stage) is not str or not Stage or len(Stage) > 128 for Stage in Stages):
            raise ValueError("hook stages must be short nonempty strings")
        self.Stages = tuple(sorted(set(Stages)))
        self.MaxEvents = MaxEvents
        self.MaxBytes = MaxBytes
        self.MaxStorageBytes = MaxStorageBytes
        self.Callback = Callback
        self.Events: list[dict[str, object]] = []
        self.DroppedEvents = 0
        self.CallbackErrors = 0
        self.WriteError: str | None = None
        self.SavedDirectory: Path | None = None
        self.SavedIndexPath: Path | None = None
        self.SavedStageCount = 0
        self.Outcome = "not-run"
        self.Run: dict[str, object] = {}
        self.Failure: dict[str, object] | None = None
        self.Bytes = 0
        self.StartedAt = monotonic()
        self.CallbackQueue: Queue = Queue(maxsize=MaxEvents)
        self.CallbackThread: Thread | None = None
        self.CallbackStop = Event()
        self.CallbackIdle = Event()
        self.CallbackIdle.set()
        self.DroppedCallbacks = 0

    def Emit(self, Stage: str, Action: str, **Fields: object) -> None:
        if self.Stages and not any(Stage == Selected or Stage.startswith(Selected + ".") for Selected in self.Stages):
            return
        if DeliveringHooks.get() is self:
            self.DroppedEvents += 1
            return
        if len(self.Events) >= self.MaxEvents or self.Bytes >= self.MaxBytes:
            self.DroppedEvents += 1
            return
        try:
            Record = _CopyFields({"Sequence": len(self.Events), "Stage": Stage,
                "Action": Action, "ElapsedSeconds": monotonic() - self.StartedAt,
                "Fields": Fields}, [256])
            Payload = json.dumps(Record, sort_keys=True, separators=(",", ":"), allow_nan=False)
            Size = len(Payload.encode("utf-8"))
            if self.Bytes + Size > self.MaxBytes:
                self.DroppedEvents += 1
                return
            self.Events.append(json.loads(Payload))
            self.Bytes += Size
            if self.Callback is not None:
                if self.CallbackStop.is_set():
                    self.DroppedCallbacks += 1
                    return
                self.CallbackIdle.clear()
                try:
                    self.CallbackQueue.put_nowait((self.Callback, Payload))
                except Full:
                    self.DroppedCallbacks += 1
                    return
                if self.CallbackThread is None:
                    self.CallbackThread = Thread(target=self._DeliverCallbacks,
                        name="compiler-hook-callbacks", daemon=True)
                    try:
                        self.CallbackThread.start()
                    except (RuntimeError, OSError):
                        self.Callback = None
                        self.CallbackThread = None
                        while True:
                            try:
                                self.CallbackQueue.get_nowait()
                            except Empty:
                                break
                            self.CallbackQueue.task_done()
                            self.DroppedCallbacks += 1
                        self.CallbackIdle.set()
        except (TypeError, ValueError, OverflowError, RuntimeError, OSError):
            self.DroppedEvents += 1

    def _DeliverCallbacks(self) -> None:
        Token = ActiveHooks.set(self)
        DeliveringToken = DeliveringHooks.set(self)
        try:
            while not self.CallbackStop.is_set() or not self.CallbackQueue.empty():
                try:
                    Callback, Payload = self.CallbackQueue.get(timeout=0.05)
                except Empty:
                    continue
                try:
                    Callback(json.loads(Payload))
                except BaseException:
                    self.CallbackErrors += 1
                finally:
                    self.CallbackQueue.task_done()
                    if self.CallbackQueue.unfinished_tasks == 0:
                        self.CallbackIdle.set()
        finally:
            DeliveringHooks.reset(DeliveringToken)
            ActiveHooks.reset(Token)

    def WaitForCallbacks(self, TimeoutSeconds: float = 1.0) -> bool:
        """Optional caller-owned wait; compiler operations never wait for listeners."""
        return self.CallbackIdle.wait(TimeoutSeconds)

    def Close(self) -> None:
        """Stop delivery after queued events; never wait for arbitrary listener code."""
        self.CallbackStop.set()

    def Document(self) -> dict[str, object]:
        return json.loads(json.dumps({"SchemaVersion": SchemaVersion,
            "SelectedStages": self.Stages, "Outcome": self.Outcome, "Run": self.Run,
            "Failure": self.Failure, "DroppedEvents": self.DroppedEvents,
            "CallbackErrors": self.CallbackErrors,
            "PendingCallbacks": self.CallbackQueue.unfinished_tasks,
            "DroppedCallbacks": self.DroppedCallbacks, "MaxEvents": self.MaxEvents,
            "MaxBytes": self.MaxBytes, "MaxStorageBytes": self.MaxStorageBytes,
            "Events": self.Events}))

    @property
    def Publication(self) -> dict[str, object]:
        """Return confirmed publication state without granting compiler authority."""
        return {"Status": "Saved" if self.SavedIndexPath is not None else
                "Unavailable" if self.WriteError is not None else "NotRun",
            "DirectoryPath": str(self.SavedDirectory) if self.SavedDirectory else None,
            "IndexPath": str(self.SavedIndexPath) if self.SavedIndexPath else None,
            "HookFileCount": self.SavedStageCount, "EventCount": len(self.Events),
            "DroppedEvents": self.DroppedEvents, "WriteError": self.WriteError}

    def SaveHooks(self, Directory: Path) -> None:
        """Publish compact members through owned descriptors, then their index."""
        Parent = Root = None
        TemporaryName = None
        ReservationName = None
        self.SavedDirectory = None
        self.SavedIndexPath = None
        self.SavedStageCount = 0
        try:
            Snapshot = self.Document()
            Events = Snapshot.pop("Events")
            Header = {Name: Value for Name, Value in Snapshot.items() if Name != "SchemaVersion"}
            MetadataHash = sha256(_EncodeDocument(Header)).hexdigest()
            Groups: dict[str, list[dict[str, object]]] = {}
            for Record in Events:
                Groups.setdefault(Record["Stage"], []).append(Record)
            Files, Payloads = [], []
            TotalBytes = 0
            for Stage, Records in sorted(Groups.items()):
                Name = _StageFileName(Stage)
                Member = {"SchemaVersion": StageSchemaVersion,
                    "Stage": Stage, "MetadataSha256": MetadataHash, "Events": Records}
                Payload = _EncodeDocument(Member)
                TotalBytes += len(Payload)
                if TotalBytes > self.MaxStorageBytes:
                    raise ValueError("compiler hook publication exceeds storage limit")
                Payloads.append((Name, Payload))
                Files.append({"Stage": Stage, "Name": Name, "SizeBytes": len(Payload),
                    "Sha256": sha256(Payload).hexdigest(), "EventCount": len(Records)})
            Index = {**Snapshot, "SchemaVersion": IndexSchemaVersion,
                "EventCount": len(Events), "Files": Files}
            IndexBytes = _EncodeDocument(Index)
            if TotalBytes + len(IndexBytes) > self.MaxStorageBytes:
                raise ValueError("compiler hook publication exceeds storage limit")
            Directory.parent.mkdir(parents=True, exist_ok=True)
            Parent = os.open(Directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            ReservationName = ".compiler-hooks-" + uuid4().hex
            os.mkdir(ReservationName, mode=0o700, dir_fd=Parent)
            Root = os.open(ReservationName, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=Parent)
            if os.listdir(Root):
                raise ValueError("reserved compiler hook directory is not empty")
            _PublishOwnedDirectory(Parent, ReservationName, Directory.name)
            ReservationName = None
            _RequireOwnedDirectory(Directory, Parent, Root)
            for Name, Payload in Payloads:
                _WriteOwnedFile(Root, Name, Payload)
            _RequireOwnedDirectory(Directory, Parent, Root)
            TemporaryName = ".index-" + uuid4().hex
            _WriteOwnedFile(Root, TemporaryName, IndexBytes)
            _RequireOwnedDirectory(Directory, Parent, Root)
            os.link(TemporaryName, "Index.json", src_dir_fd=Root, dst_dir_fd=Root,
                    follow_symlinks=False)
            _RequireOwnedDirectory(Directory, Parent, Root)
            self.SavedDirectory = Directory
            self.SavedIndexPath = Directory / "Index.json"
            self.SavedStageCount = len(Files)
            self.WriteError = None
        except (OSError, TypeError, ValueError) as Error:
            self.WriteError = type(Error).__name__
        finally:
            if TemporaryName is not None and Root is not None:
                try:
                    os.unlink(TemporaryName, dir_fd=Root)
                except OSError:
                    pass
            if ReservationName is not None and Parent is not None and Root is not None:
                try:
                    Reserved = os.stat(ReservationName, dir_fd=Parent, follow_symlinks=False)
                    Owned = os.fstat(Root)
                    if stat.S_ISDIR(Reserved.st_mode) and (Reserved.st_dev, Reserved.st_ino) == (Owned.st_dev, Owned.st_ino):
                        os.rmdir(ReservationName, dir_fd=Parent)
                except OSError:
                    pass
            for Descriptor in (Root, Parent):
                if Descriptor is not None:
                    try:
                        os.close(Descriptor)
                    except OSError:
                        pass


    def Save(self, PathValue: Path) -> None:
        """Atomically replace the diagnostic trace; failed I/O stays diagnostic."""
        TemporaryPath = None
        try:
            PathValue.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=PathValue.parent,
                                    prefix=".compiler-trace-", delete=False) as Stream:
                TemporaryPath = Path(Stream.name)
                json.dump(self.Document(), Stream, sort_keys=True, indent=2, allow_nan=False)
                Stream.write("\n")
            TemporaryPath.replace(PathValue)
        except (OSError, TypeError, ValueError) as Error:
            self.WriteError = type(Error).__name__
        finally:
            if TemporaryPath is not None:
                try:
                    TemporaryPath.unlink(missing_ok=True)
                except OSError:
                    pass


@contextmanager
def CaptureCompilerActions(Hooks: CompilerHooks) -> Iterator[CompilerHooks]:
    Token = ActiveHooks.set(Hooks)
    try:
        yield Hooks
    finally:
        ActiveHooks.reset(Token)


def EmitCompilerAction(Stage: str, Action: str, **Fields: object) -> None:
    Hooks = ActiveHooks.get()
    if Hooks is not None:
        Hooks.Emit(Stage, Action, **Fields)


def RunCompilerOperation(Stage: str, Function: Callable[..., Any], *Arguments: Any, **Keywords: Any) -> Any:
    """Tap one operation without replacing its return value or exception."""
    if ActiveHooks.get() is None:
        return Function(*Arguments, **Keywords)
    EmitCompilerAction(Stage, "begin")
    try:
        Result = Function(*Arguments, **Keywords)
    except BaseException as Error:
        EmitCompilerAction(Stage, "failed", ErrorType=type(Error).__name__)
        raise
    EmitCompilerAction(Stage, "finish")
    return Result


def ObserveCompilerRun(Function: Callable[..., Any]) -> Callable[..., Any]:
    """Attach explicit hooks to a compile and save on both success and failure."""
    @wraps(Function)
    def Run(*Arguments: Any, **Keywords: Any) -> Any:
        Hooks = Keywords.get("Hooks")
        if Hooks is None:
            return Function(*Arguments, **Keywords)
        if not isinstance(Hooks, CompilerHooks):
            raise TypeError("Hooks must be CompilerHooks")
        if Hooks.Outcome != "not-run":
            raise ValueError("each compiler run requires fresh hooks")
        OutputPath = Path(Keywords["OutputPath"])
        Hooks.Run = {"InputPath": str(Keywords.get("InputPath", ""))[:2048],
            "OutputPath": str(OutputPath)[:2048]}
        with CaptureCompilerActions(Hooks):
            Hooks.Outcome = "running"
            try:
                Result = RunCompilerOperation("compile", Function, *Arguments, **Keywords)
            except BaseException as Error:
                Hooks.Outcome = "failed"
                Failure = getattr(Error, "Failure", None)
                Hooks.Failure = {"ErrorType": type(Error).__name__,
                    "Stage": str(getattr(Failure, "Stage", "compile"))[:128]}
                raise
            else:
                Hooks.Outcome = "completed"
                return Result
            finally:
                Hooks.Close()
                Hooks.SaveHooks(OutputPath.with_suffix(".CompilerHooks"))
    return Run


def _PublishOwnedDirectory(Parent: int, SourceName: str, TargetName: str) -> None:
    """Expose an already-open private reservation without replacing any entry."""
    import ctypes
    import errno

    Rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if Rename is None:
        raise OSError(errno.ENOTSUP, "atomic directory reservation is unavailable")
    Rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    Rename.restype = ctypes.c_int
    if Rename(Parent, os.fsencode(SourceName), Parent, os.fsencode(TargetName), 1) != 0:
        Error = ctypes.get_errno()
        raise OSError(Error, os.strerror(Error), TargetName)


def _RequireOwnedDirectory(Directory: Path, Parent: int, Root: int) -> None:
    Owned = os.fstat(Root)
    Relative = os.stat(Directory.name, dir_fd=Parent, follow_symlinks=False)
    Current = Directory.stat(follow_symlinks=False)
    if any(not stat.S_ISDIR(Item.st_mode) or
            (Item.st_dev, Item.st_ino) != (Owned.st_dev, Owned.st_ino)
            for Item in (Relative, Current)):
        raise ValueError("compiler hook directory identity changed")


def _WriteOwnedFile(Root: int, Name: str, Payload: bytes) -> None:
    Descriptor = os.open(Name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         mode=0o600, dir_fd=Root)
    try:
        Stream = os.fdopen(Descriptor, "wb")
    except BaseException:
        os.close(Descriptor)
        raise
    with Stream:
        Stream.write(Payload)


def _StageFileName(Stage: str) -> str:
    Slug = re.sub(r"[^a-z0-9]+", "-", Stage.lower()).strip("-")[:40] or "stage"
    return f"Hook-{Slug}-{sha256(Stage.encode('utf-8')).hexdigest()}.json"


def _EncodeDocument(Document: dict[str, object]) -> bytes:
    return (json.dumps(Document, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _ReadJsonFile(Descriptor: int, Budget: list[int]) -> tuple[dict[str, object], bytes]:
    if not stat.S_ISREG(os.fstat(Descriptor).st_mode):
        raise ValueError("compiler trace must be a regular file")
    Chunks = []
    while True:
        Chunk = os.read(Descriptor, min(65536, Budget[0] + 1))
        if not Chunk:
            break
        Budget[0] -= len(Chunk)
        if Budget[0] < 0:
            raise ValueError("compiler trace exceeds read limit")
        Chunks.append(Chunk)
    Payload = b"".join(Chunks)
    try:
        Document = json.loads(Payload, parse_constant=lambda Value: _InvalidNumber())
    except (RecursionError, UnicodeError) as Error:
        raise ValueError("invalid compiler trace JSON") from Error
    if type(Document) is not dict:
        raise ValueError("invalid compiler trace document")
    return Document, Payload


def _InvalidNumber() -> None:
    raise ValueError("nonfinite compiler trace number")


def _ReadChild(Root: int, Name: str, Budget: list[int]) -> tuple[dict[str, object], bytes]:
    Descriptor = os.open(Name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=Root)
    try:
        return _ReadJsonFile(Descriptor, Budget)
    finally:
        os.close(Descriptor)


def _ValidateEvents(Document: dict[str, object], *, Strict: bool = False) -> list[dict[str, object]]:
    Events = Document.get("Events")
    if type(Events) is not list:
        raise ValueError("invalid compiler trace events")
    LastSequence = -1
    for Record in Events:
        if type(Record) is not dict or type(Record.get("Stage")) is not str or type(Record.get("Action")) is not str:
            raise ValueError("invalid compiler trace event")
        if Strict:
            Sequence = Record.get("Sequence")
            if type(Sequence) is not int or Sequence <= LastSequence:
                raise ValueError("invalid compiler trace sequence")
            if type(Record.get("Fields")) is not dict or type(Record.get("ElapsedSeconds")) not in (int, float):
                raise ValueError("invalid compiler trace event fields")
            LastSequence = Sequence
    return Events


def _ReadIndex(Root: int, Index: dict[str, object], Budget: list[int],
               SelectedName: str | None = None, IndexSize: int = 0,
               InitialMember: tuple[str, dict[str, object], bytes] | None = None) -> dict[str, object]:
    if Index.get("SchemaVersion") != IndexSchemaVersion:
        raise ValueError("unsupported compiler hook index")
    Files, Count = Index.get("Files"), Index.get("EventCount")
    if type(Files) is not list or type(Count) is not int or Count < 0 or len(Files) > Count:
        raise ValueError("invalid compiler hook inventory")
    for Name in ("MaxEvents", "MaxBytes"):
        if type(Index.get(Name)) is not int or Index[Name] < 1:
            raise ValueError("invalid compiler hook limit")
    for Name in ("DroppedEvents", "CallbackErrors", "PendingCallbacks", "DroppedCallbacks"):
        if type(Index.get(Name)) is not int or Index[Name] < 0:
            raise ValueError("invalid compiler hook counter")
    if Count > Index["MaxEvents"] or Index.get("Outcome") not in ("not-run", "running", "failed", "completed"):
        raise ValueError("invalid compiler hook outcome or count")
    if type(Index.get("SelectedStages")) is not list or any(type(Stage) is not str for Stage in Index["SelectedStages"]):
        raise ValueError("invalid compiler hook selectors")
    if type(Index.get("Run")) is not dict or (Index.get("Failure") is not None and type(Index["Failure"]) is not dict):
        raise ValueError("invalid compiler hook run metadata")
    StorageLimit = Index.get("MaxStorageBytes")
    if StorageLimit is not None and (type(StorageLimit) is not int or StorageLimit < 1):
        raise ValueError("invalid compiler hook storage limit")
    StorageBytes = IndexSize
    if StorageLimit is not None and StorageBytes > StorageLimit:
        raise ValueError("compiler hook inventory exceeds storage limit")
    Header = {Name: Value for Name, Value in Index.items() if Name not in ("SchemaVersion", "EventCount", "Files")}
    HeaderBytes = _EncodeDocument(Header)
    MetadataHash = sha256(HeaderBytes).hexdigest()
    Events, Stages, Names = [], set(), set()
    SelectedEvents, SelectedStage = None, None
    for Entry in Files:
        if type(Entry) is not dict or set(Entry) != {"Stage", "Name", "SizeBytes", "Sha256", "EventCount"}:
            raise ValueError("invalid compiler hook member")
        Stage, Name = Entry["Stage"], Entry["Name"]
        if type(Stage) is not str or type(Name) is not str or Name != _StageFileName(Stage) or Stage in Stages or Name in Names:
            raise ValueError("unsafe or duplicate compiler hook member")
        if type(Entry["SizeBytes"]) is not int or Entry["SizeBytes"] < 1 or type(Entry["EventCount"]) is not int or Entry["EventCount"] < 1:
            raise ValueError("invalid compiler hook member size or count")
        if type(Entry["Sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", Entry["Sha256"]):
            raise ValueError("invalid compiler hook member digest")
        if InitialMember is not None and Name == InitialMember[0]:
            Member, Payload = InitialMember[1], InitialMember[2]
        else:
            Member, Payload = _ReadChild(Root, Name, Budget)
        StorageBytes += len(Payload)
        if StorageLimit is not None and StorageBytes > StorageLimit:
            raise ValueError("compiler hook inventory exceeds storage limit")
        if len(Payload) != Entry["SizeBytes"] or sha256(Payload).hexdigest() != Entry["Sha256"]:
            raise ValueError("compiler hook member identity mismatch")
        if Member.get("Stage") != Stage:
            raise ValueError("compiler hook member stage mismatch")
        if Member.get("SchemaVersion") == StageSchemaVersion:
            if set(Member) != {"SchemaVersion", "Stage", "MetadataSha256", "Events"} or Member["MetadataSha256"] != MetadataHash:
                raise ValueError("compiler hook member metadata reference mismatch")
        elif Member.get("SchemaVersion") == LegacyStageSchemaVersion:
            MemberHeader = {Key: Value for Key, Value in Member.items() if Key not in ("SchemaVersion", "Stage", "Events")}
            if _EncodeDocument(MemberHeader) != HeaderBytes:
                raise ValueError("compiler hook member metadata mismatch")
        else:
            raise ValueError("unsupported compiler hook member schema")
        Records = _ValidateEvents(Member, Strict=True)
        if len(Records) != Entry["EventCount"] or any(Record["Stage"] != Stage for Record in Records):
            raise ValueError("compiler hook member event mismatch")
        Stages.add(Stage)
        Names.add(Name)
        Events.extend(Records)
        if Name == SelectedName:
            SelectedEvents, SelectedStage = Records, Stage
    Events.sort(key=lambda Record: Record["Sequence"])
    if len(Events) != Count or any(Record["Sequence"] != Ordinal for Ordinal, Record in enumerate(Events)):
        raise ValueError("incomplete compiler hook event sequence")
    if sum(len(_EncodeDocument(Record)) - 1 for Record in Events) > Index["MaxBytes"]:
        raise ValueError("compiler hook events exceed shared byte limit")
    if SelectedName is not None and SelectedEvents is None:
        raise ValueError("compiler hook file is absent from index")
    return {**Header, "SchemaVersion": SchemaVersion,
        "CoverageScope": "stage" if SelectedName else "run", "Stage": SelectedStage,
        "Events": SelectedEvents if SelectedName else Events}


def ReadCompilerTrace(PathValue: Path, *, MaxFileBytes: int = DefaultStorageBytes) -> dict[str, object]:
    """Read legacy JSON or a verified split index/member within one total budget."""
    if type(MaxFileBytes) is not int or MaxFileBytes < 1:
        raise ValueError("compiler trace read limit must be positive")
    PathValue = Path(PathValue)
    Budget = [MaxFileBytes]
    if PathValue.is_dir():
        Directory, Name = PathValue, "Index.json"
    else:
        Directory, Name = PathValue.parent, PathValue.name
    Root = os.open(Directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        Document, _Payload = _ReadChild(Root, Name, Budget)
        Version = Document.get("SchemaVersion")
        if Name == "Index.json" and Version != IndexSchemaVersion:
            raise ValueError("unsupported compiler hook index")
        if Version == SchemaVersion:
            _ValidateEvents(Document)
            return Document
        if Version == IndexSchemaVersion:
            return _ReadIndex(Root, Document, Budget, IndexSize=len(_Payload))
        if Version in (StageSchemaVersion, LegacyStageSchemaVersion):
            Index, IndexPayload = _ReadChild(Root, "Index.json", Budget)
            return _ReadIndex(Root, Index, Budget, SelectedName=Name,
                IndexSize=len(IndexPayload), InitialMember=(Name, Document, _Payload))
        raise ValueError("unsupported compiler trace")
    finally:
        os.close(Root)


def DiagnoseCompilerTrace(Document: dict[str, object]) -> dict[str, object]:
    """Summarize observed failure and last action without inferring acceptance."""
    Events = Document["Events"]
    return {"Outcome": Document.get("Outcome", "unknown"),
        "Failure": Document.get("Failure"),
        "CoverageScope": Document.get("CoverageScope", "run"),
        "Stage": Document.get("Stage"),
        "FailedOperations": [Event["Stage"] for Event in Events if Event["Action"] == "failed"],
        "LastEvent": Events[-1] if Events else None,
        "Truncated": bool(Document.get("DroppedEvents")),
        "CallbackErrors": Document.get("CallbackErrors", 0),
        "PendingCallbacks": Document.get("PendingCallbacks", 0),
        "DroppedCallbacks": Document.get("DroppedCallbacks", 0),
        "Coverage": "selected instrumented operations only; no physical acceptance inference"}


if __name__ == "__main__":
    import argparse
    Parser = argparse.ArgumentParser(description="Diagnose a saved compiler action trace")
    Parser.add_argument("trace", type=Path)
    print(json.dumps(DiagnoseCompilerTrace(ReadCompilerTrace(Parser.parse_args().trace)), indent=2))

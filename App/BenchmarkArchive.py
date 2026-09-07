"""Immutable, commit-stamped archive publication for router benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Callable, Mapping, Sequence


ArchiveSchemaVersion = "router-benchmark-archive-v1"
ArchiveManifestName = "ArchiveManifest.json"
ArchiveChecksumsName = "SHA256SUMS"
ArchiveResultName = "BenchmarkResult.json"
GitRunner = Callable[[Path, Sequence[str]], bytes]


@dataclass(frozen=True)
class VerifiedRegularFile:
    """Bytes and descriptor-derived identity from one safe file open."""

    Data: bytes
    SizeBytes: int
    Sha256: str


@dataclass(frozen=True)
class BenchmarkSourceIdentity:
    """Git identity that distinguishes clean commits and dirty source states."""

    Head: str
    ShortHead: str
    Branch: str
    Detached: bool
    Dirty: bool
    StatusSha256: str
    StatusEntries: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkArchiveIdentity:
    """Sortable archive name bound to one source observation."""

    ArchiveId: str
    CapturedAtUtc: str
    Source: BenchmarkSourceIdentity


@dataclass(frozen=True)
class BenchmarkArchiveContext:
    """Immutable inputs used to seal or mirror one benchmark session."""

    Identity: BenchmarkArchiveIdentity
    ArchiveDirectory: Path
    SourceDirectory: Path
    Command: tuple[str, ...]
    WorkingDirectory: Path
    MatrixMode: str
    RoutingThreads: int | None
    BaselineMode: str | None
    StartedAtUtc: str


def RunGit(RepositoryRoot: Path, Arguments: Sequence[str]) -> bytes:
    """Run one read-only Git query and return its exact stdout bytes."""
    Completed = subprocess.run(
        ("git", *Arguments),
        cwd=RepositoryRoot,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return Completed.stdout


def ReadBenchmarkSourceIdentity(
    RepositoryRoot: Path,
    *,
    GitQuery: GitRunner = RunGit,
) -> BenchmarkSourceIdentity:
    """Read HEAD, branch, and exact ordinary status without following ignores."""
    Root = RepositoryRoot.resolve()
    try:
        Head = GitQuery(Root, ("rev-parse", "HEAD")).decode("ascii").strip()
        Branch = GitQuery(
            Root,
            ("branch", "--show-current"),
        ).decode("utf-8", errors="surrogateescape").strip()
        StatusBytes = GitQuery(
            Root,
            (
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
            ),
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as Error:
        raise RuntimeError(
            f"benchmark archive requires a readable Git identity: {Error}"
        ) from Error
    if re.fullmatch(r"[0-9a-f]{40}", Head) is None:
        raise RuntimeError(
            f"benchmark archive requires a full 40-character Git commit: {Head!r}"
        )
    Entries = tuple(
        Entry.decode("utf-8", errors="surrogateescape")
        for Entry in StatusBytes.split(b"\0")
        if Entry
    )
    return BenchmarkSourceIdentity(
        Head=Head,
        ShortHead=Head[:12],
        Branch=Branch,
        Detached=not Branch,
        Dirty=bool(StatusBytes),
        StatusSha256=sha256(StatusBytes).hexdigest(),
        StatusEntries=Entries,
    )


def BuildBenchmarkArchiveIdentity(
    RepositoryRoot: Path,
    *,
    CapturedAtUtc: datetime | None = None,
    GitQuery: GitRunner = RunGit,
) -> BenchmarkArchiveIdentity:
    """Build the timestamped, commit-stamped identity for one archive."""
    Captured = (CapturedAtUtc or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    Source = ReadBenchmarkSourceIdentity(
        RepositoryRoot,
        GitQuery=GitQuery,
    )
    Timestamp = Captured.strftime("%Y%m%dT%H%M%S.%fZ")
    ArchiveId = f"{Timestamp}-{Source.ShortHead}"
    if Source.Dirty:
        ArchiveId += f"-dirty-{Source.StatusSha256[:12]}"
    return BenchmarkArchiveIdentity(
        ArchiveId=ArchiveId,
        CapturedAtUtc=Captured.isoformat(),
        Source=Source,
    )


def BuildBenchmarkArchiveDirectory(
    OutputRoot: Path,
    DateLabel: str,
    Identity: BenchmarkArchiveIdentity,
) -> Path:
    """Return the unique final path for one ordinary or mirrored archive."""
    return (
        OutputRoot.resolve(strict=False)
        / DateLabel
        / "Archives"
        / Identity.ArchiveId
    )


def EnsureArchiveTargetAvailable(ArchiveDirectory: Path) -> None:
    """Fail before execution rather than merge with an existing archive."""
    if ArchiveDirectory.exists() or ArchiveDirectory.is_symlink():
        raise FileExistsError(
            f"benchmark archive target already exists: {ArchiveDirectory}"
        )


def Sha256File(PathValue: Path) -> str:
    """Hash bytes from one safely opened regular-file descriptor."""
    Verified = _ReadVerifiedRegularFileWithoutFollowing(PathValue)
    if Verified is None:
        raise ValueError(
            f"benchmark archive file is not safely readable: {PathValue}"
        )
    return Verified.Sha256


def _SafeOpenPrimitivesAvailable() -> bool:
    """Require fd-relative, non-following primitives with no unsafe fallback."""
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_NONBLOCK")
        and os.open in getattr(os, "supports_dir_fd", frozenset())
    )


def _ReadVerifiedRegularFileWithoutFollowing(
    Value: Path,
) -> VerifiedRegularFile | None:
    """Read one regular file through stable non-following directory handles."""
    if not _SafeOpenPrimitivesAvailable():
        return None
    Absolute = Path(os.path.abspath(os.fspath(Value)))
    DirectoryDescriptors = _OpenDirectoryWithoutFollowing(Absolute.parent)
    if DirectoryDescriptors is None:
        return None
    try:
        return _ReadVerifiedRegularFileAt(
            DirectoryDescriptors[-1],
            (Absolute.name,),
        )
    finally:
        for DirectoryDescriptor in reversed(DirectoryDescriptors):
            os.close(DirectoryDescriptor)


def _OpenDirectoryWithoutFollowing(Value: Path) -> list[int] | None:
    """Open every absolute directory component without following a symlink."""
    if not _SafeOpenPrimitivesAvailable():
        return None
    Absolute = Path(os.path.abspath(os.fspath(Value)))
    Anchor = Path(Absolute.anchor)
    Parts = Absolute.parts[1:] if Absolute.anchor else Absolute.parts
    DirectoryFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    DirectoryDescriptors: list[int] = []
    try:
        CurrentDescriptor = os.open(Anchor, DirectoryFlags)
        DirectoryDescriptors.append(CurrentDescriptor)
        for Part in Parts:
            CurrentDescriptor = os.open(
                Part,
                DirectoryFlags,
                dir_fd=CurrentDescriptor,
            )
            DirectoryDescriptors.append(CurrentDescriptor)
        return DirectoryDescriptors
    except OSError:
        for DirectoryDescriptor in reversed(DirectoryDescriptors):
            os.close(DirectoryDescriptor)
        return None


def _ReadVerifiedRegularFileAt(
    RootDescriptor: int,
    RelativeParts: Sequence[str],
) -> VerifiedRegularFile | None:
    """Read one validated relative leaf below an already-open directory."""
    if (
        not RelativeParts
        or any(Part in {"", ".", ".."} or "/" in Part for Part in RelativeParts)
    ):
        return None
    DirectoryFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    FileFlags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    DirectoryDescriptors: list[int] = []
    Descriptor: int | None = None
    CurrentDescriptor = RootDescriptor
    try:
        for Part in RelativeParts[:-1]:
            try:
                CurrentDescriptor = os.open(
                    Part,
                    DirectoryFlags,
                    dir_fd=CurrentDescriptor,
                )
            except OSError:
                return None
            DirectoryDescriptors.append(CurrentDescriptor)
        try:
            Descriptor = os.open(
                RelativeParts[-1],
                FileFlags,
                dir_fd=CurrentDescriptor,
            )
        except OSError:
            return None
        DescriptorStat = os.fstat(Descriptor)
        if not stat.S_ISREG(DescriptorStat.st_mode):
            return None
        Chunks: list[bytes] = []
        while True:
            Chunk = os.read(Descriptor, 1024 * 1024)
            if not Chunk:
                break
            Chunks.append(Chunk)
        Data = b"".join(Chunks)
        if len(Data) != DescriptorStat.st_size:
            return None
        return VerifiedRegularFile(
            Data=Data,
            SizeBytes=DescriptorStat.st_size,
            Sha256=sha256(Data).hexdigest(),
        )
    finally:
        if Descriptor is not None:
            os.close(Descriptor)
        for DirectoryDescriptor in reversed(DirectoryDescriptors):
            os.close(DirectoryDescriptor)


def _ReadVerifiedRegularFileBelow(
    Root: Path,
    RelativeParts: Sequence[str],
) -> VerifiedRegularFile | None:
    """Read a lexical relative path below one safely opened root."""
    RootDescriptors = _OpenDirectoryWithoutFollowing(Root)
    if RootDescriptors is None:
        return None
    try:
        return _ReadVerifiedRegularFileAt(
            RootDescriptors[-1],
            RelativeParts,
        )
    finally:
        for DirectoryDescriptor in reversed(RootDescriptors):
            os.close(DirectoryDescriptor)


def _AtomicWriteText(PathValue: Path, Text: str) -> None:
    _AtomicWriteBytes(PathValue, Text.encode("utf-8"))


def _AtomicWriteBytes(PathValue: Path, Data: bytes) -> None:
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    TemporaryPath = PathValue.with_name(
        f".{PathValue.name}.tmp-P{os.getpid()}"
    )
    TemporaryPath.write_bytes(Data)
    TemporaryPath.replace(PathValue)


def _AtomicWriteBytesAt(
    DirectoryDescriptor: int,
    Name: str,
    Data: bytes,
) -> None:
    """Atomically replace one root-level file below a retained directory fd."""
    if Name in {"", ".", ".."} or "/" in Name:
        raise ValueError("benchmark archive output name is invalid")
    TemporaryName = f".{Name}.tmp-P{os.getpid()}"
    Flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    Descriptor: int | None = None
    try:
        Descriptor = os.open(
            TemporaryName,
            Flags,
            0o600,
            dir_fd=DirectoryDescriptor,
        )
        View = memoryview(Data)
        Written = 0
        while Written < len(View):
            Count = os.write(Descriptor, View[Written:])
            if Count <= 0:
                raise OSError("benchmark archive output write made no progress")
            Written += Count
        os.fsync(Descriptor)
        os.close(Descriptor)
        Descriptor = None
        os.rename(
            TemporaryName,
            Name,
            src_dir_fd=DirectoryDescriptor,
            dst_dir_fd=DirectoryDescriptor,
        )
    except Exception:
        if Descriptor is not None:
            os.close(Descriptor)
        try:
            os.unlink(TemporaryName, dir_fd=DirectoryDescriptor)
        except OSError:
            pass
        raise


def _PrettyJson(Value: object) -> str:
    return json.dumps(
        Value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ) + "\n"


def _RelativeFiles(
    Root: Path,
    *,
    ExcludedRelativePaths: frozenset[str] = frozenset(),
) -> list[Path]:
    Files: list[Path] = []
    for PathValue in Root.rglob("*"):
        if PathValue.is_symlink():
            raise ValueError(
                "benchmark archive evidence must not contain symlinks: "
                f"{PathValue.relative_to(Root)}"
            )
        RelativePath = PathValue.relative_to(Root).as_posix()
        if (
            PathValue.is_file()
            and RelativePath not in ExcludedRelativePaths
        ):
            Files.append(PathValue)
    return sorted(Files, key=lambda Value: Value.relative_to(Root).as_posix())


def BuildArchiveFileInventory(Root: Path) -> list[dict[str, object]]:
    """Hash every evidence file without creating a self-referential manifest."""
    return _BuildInventoryRecords(_CaptureArchiveFiles(Root))


def _CaptureArchiveFiles(
    Root: Path,
    *,
    ExcludedRelativePaths: frozenset[str] = frozenset({
        ArchiveManifestName,
        ArchiveChecksumsName,
    }),
) -> dict[str, VerifiedRegularFile]:
    """Capture each archive file exactly once for one sealing observation."""
    RootDescriptors = _OpenDirectoryWithoutFollowing(Root)
    if RootDescriptors is None:
        raise ValueError("benchmark archive root is not safely readable")
    try:
        return _CaptureArchiveFilesAt(
            RootDescriptors[-1],
            ExcludedRelativePaths,
        )
    finally:
        for RootDescriptor in reversed(RootDescriptors):
            os.close(RootDescriptor)


def _CaptureArchiveFilesAt(
    RootDescriptor: int,
    ExcludedRelativePaths: frozenset[str],
) -> dict[str, VerifiedRegularFile]:
    """Capture a complete tree below one caller-retained archive root."""
    Result: dict[str, VerifiedRegularFile] = {}
    _CaptureDirectoryFiles(
        RootDescriptor,
        (),
        ExcludedRelativePaths,
        Result,
    )
    return Result


def _CaptureDirectoryFiles(
    DirectoryDescriptor: int,
    Prefix: tuple[str, ...],
    ExcludedRelativePaths: frozenset[str],
    Result: dict[str, VerifiedRegularFile],
) -> None:
    """Capture one directory tree from a retained root descriptor."""
    DirectoryFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        Entries = sorted(
            os.scandir(DirectoryDescriptor),
            key=lambda Entry: Entry.name,
        )
    except OSError as Error:
        raise ValueError(
            "benchmark archive evidence directory changed while reading"
        ) from Error
    for Entry in Entries:
        Name = Entry.name
        if Name in {"", ".", ".."} or "/" in Name:
            raise ValueError("benchmark archive evidence name is invalid")
        RelativeParts = (*Prefix, Name)
        RelativePath = "/".join(RelativeParts)
        try:
            ChildDescriptor = os.open(
                Name,
                DirectoryFlags,
                dir_fd=DirectoryDescriptor,
            )
        except OSError:
            ChildDescriptor = None
        if ChildDescriptor is not None:
            try:
                if not stat.S_ISDIR(os.fstat(ChildDescriptor).st_mode):
                    raise ValueError(
                        "benchmark archive evidence entry changed while reading"
                    )
                _CaptureDirectoryFiles(
                    ChildDescriptor,
                    RelativeParts,
                    ExcludedRelativePaths,
                    Result,
                )
            finally:
                os.close(ChildDescriptor)
            continue
        Verified = _ReadVerifiedRegularFileAt(
            DirectoryDescriptor,
            (Name,),
        )
        if Verified is None:
            raise ValueError(
                "benchmark archive evidence changed or is not safely readable: "
                f"{RelativePath}"
            )
        if RelativePath not in ExcludedRelativePaths:
            Result[RelativePath] = Verified


def _BuildInventoryRecords(
    Files: Mapping[str, VerifiedRegularFile],
) -> list[dict[str, object]]:
    return [
        {
            "Path": RelativePath,
            "SizeBytes": Files[RelativePath].SizeBytes,
            "Sha256": Files[RelativePath].Sha256,
        }
        for RelativePath in sorted(Files)
    ]


def _ReadRegularFileWithoutFollowing(Value: Path) -> bytes | None:
    """Read one regular file without traversing a symlink component."""
    Verified = _ReadVerifiedRegularFileWithoutFollowing(Value)
    return Verified.Data if Verified is not None else None


def _ReadRoutingFailureSurface(
    ArchiveRoot: Path,
    RunName: str,
    ArtifactRecord: object,
    VerifiedFiles: Mapping[str, VerifiedRegularFile] | None = None,
) -> dict[str, object]:
    if not isinstance(ArtifactRecord, dict):
        return {}
    RecordedPath = ArtifactRecord.get("Path")
    RecordedSha256 = ArtifactRecord.get("Sha256")
    if not isinstance(RecordedPath, str) or not isinstance(RecordedSha256, str):
        return {}
    RawParts = RecordedPath.split("/")
    RunIndex = next(
        (
            Index
            for Index in range(len(RawParts) - 1, -1, -1)
            if RawParts[Index] == RunName
        ),
        None,
    )
    if RunIndex is None:
        return {}
    RelativeParts = RawParts[RunIndex:]
    if (
        RunName in {"", ".", ".."}
        or any(Part in {"", ".", ".."} for Part in RelativeParts)
    ):
        return {}
    RelativePath = "/".join(RelativeParts)
    Candidate = (
        VerifiedFiles.get(RelativePath)
        if VerifiedFiles is not None
        else _ReadVerifiedRegularFileBelow(
            ArchiveRoot,
            RelativeParts,
        )
    )
    if Candidate is None:
        return {}
    if Candidate.Sha256 != RecordedSha256:
        return {}
    try:
        Payload = json.loads(Candidate.Data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(Payload, dict):
        return {}
    Failure = Payload.get("Failure", Payload)
    if not isinstance(Failure, dict):
        return {}
    StrategyValue = Payload.get("Strategy", {})
    Strategy = StrategyValue if isinstance(StrategyValue, dict) else {}
    PolicyValue = Payload.get("Policy")
    Policy = PolicyValue if isinstance(PolicyValue, dict) else None
    ReproductionValue = Payload.get("Reproduction", {})
    Reproduction = (
        ReproductionValue if isinstance(ReproductionValue, dict) else {}
    )
    PolicyIdentity = None
    if Policy is not None:
        PolicyIdentity = {
            "PolicyVersion": Policy.get("PolicyVersion"),
            "Seed": Policy.get("Seed"),
            "Sha256": sha256(json.dumps(
                Policy,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest(),
            "Snapshot": Policy,
        }
    return {
        Key: Failure.get(Key)
        for Key in ("Stage", "Reason", "Detail")
        if Failure.get(Key) is not None
    } | {
        "RoutingIdentity": {
            "RequestedStrategy": Strategy.get("Requested"),
            "UsedStrategy": Strategy.get("Used"),
            "FallbackUsed": Strategy.get("FallbackUsed"),
            "PolicyIdentity": PolicyIdentity,
            "ReproductionRequestedStrategy": Reproduction.get(
                "RequestedStrategy"
            ),
        },
    }


def _FailClosedAcceptanceVerdict(Value: object) -> bool:
    """Return true only for a literal JSON boolean true verdict."""
    return Value is True


def ValidateExactAcceptanceVerdicts(
    BenchmarkManifest: Mapping[str, object],
) -> None:
    """Reject sealed/public acceptance data with non-boolean verdicts."""
    if type(BenchmarkManifest.get("Accepted")) is not bool:
        raise ValueError("manifest Accepted must be an exact boolean")
    RawRuns = BenchmarkManifest.get("Runs", [])
    if not isinstance(RawRuns, list):
        raise ValueError("manifest Runs must be a list")
    for Index, RawRun in enumerate(RawRuns):
        if not isinstance(RawRun, dict):
            continue
        if type(RawRun.get("Accepted")) is not bool:
            raise ValueError(
                f"run {Index} Accepted must be an exact boolean"
            )


def BuildArchiveRunSurface(
    BenchmarkManifest: Mapping[str, object],
    ArchiveRoot: Path,
    VerifiedFiles: Mapping[str, VerifiedRegularFile] | None = None,
) -> list[dict[str, object]]:
    """Project the full acceptance manifest into a compact pass/fail surface."""
    RawRuns = BenchmarkManifest.get("Runs", [])
    if not isinstance(RawRuns, list):
        return []
    Surface: list[dict[str, object]] = []
    for RawRun in RawRuns:
        if not isinstance(RawRun, dict):
            continue
        EvaluationValue = RawRun.get("Evaluation", {})
        Evaluation = (
            EvaluationValue if isinstance(EvaluationValue, dict) else {}
        )
        ProcessValue = Evaluation.get("Process", {})
        Process = ProcessValue if isinstance(ProcessValue, dict) else {}
        ObservedValue = Evaluation.get("Observed", {})
        Observed = ObservedValue if isinstance(ObservedValue, dict) else {}
        ConfiguredRoutingIdentityValue = Observed.get(
            "ConfiguredRoutingIdentity", {}
        )
        ConfiguredRoutingIdentity = (
            ConfiguredRoutingIdentityValue
            if isinstance(ConfiguredRoutingIdentityValue, dict)
            else {}
        )
        ActualRoutingIdentityValue = Observed.get(
            "ActualRoutingIdentity", {}
        )
        ActualRoutingIdentity = (
            ActualRoutingIdentityValue
            if isinstance(ActualRoutingIdentityValue, dict)
            else {}
        )
        FailuresValue = Evaluation.get("Failures", [])
        Failures = (
            [str(Value) for Value in FailuresValue]
            if isinstance(FailuresValue, list)
            else []
        )
        RunName = str(RawRun.get("RunName", ""))
        ArtifactRecords = Evaluation.get("Artifacts", {})
        ArtifactRecord = (
            ArtifactRecords.get("RoutingFailure")
            if isinstance(ArtifactRecords, dict)
            else None
        )
        Resolution = Observed.get("FailureArtifactResolution")
        AuthoritativeFailureArtifact = bool(
            isinstance(Resolution, dict)
            and Resolution.get("Status") in {"direct", "nested"}
            and isinstance(ArtifactRecord, dict)
            and Resolution.get("Path") == ArtifactRecord.get("Path")
        )
        FailureSurface = (
            _ReadRoutingFailureSurface(
                ArchiveRoot,
                RunName,
                ArtifactRecord,
                VerifiedFiles,
            )
            if AuthoritativeFailureArtifact
            else {}
        )
        Surface.append({
            "Sequence": RawRun.get("Sequence"),
            "RunName": RunName,
            "Circuit": RawRun.get("Circuit"),
            "Status": RawRun.get("Status"),
            "Accepted": _FailClosedAcceptanceVerdict(
                RawRun.get("Accepted")
            ),
            "WallRuntimeSeconds": Process.get("WallRuntimeSeconds"),
            "ReturnCode": Process.get("ReturnCode"),
            "TimedOut": Process.get("TimedOut"),
            "Stage": FailureSurface.get("Stage"),
            "Reason": FailureSurface.get("Reason"),
            "Detail": FailureSurface.get("Detail"),
            "ConfiguredRoutingIdentity": ConfiguredRoutingIdentity,
            "ActualRoutingIdentity": ActualRoutingIdentity,
            "FailureRoutingIdentity": (
                Observed.get("FailureRoutingIdentity")
                or FailureSurface.get("RoutingIdentity")
            ),
            "RoutingIdentityChecks": Observed.get(
                "RoutingIdentityChecks"
            ),
            "ValidationStatus": Observed.get("FabricValidationStatus"),
            "ValidationVectors": Observed.get("FabricValidationVectors"),
            "ValidationBackend": Observed.get("FabricValidationBackend"),
            "FabricFixtureSha256": Observed.get("FabricFixtureSha256"),
            "Failures": Failures,
            "MissingRequiredArtifacts": sorted(
                Failure.removeprefix("missing required artifact: ")
                for Failure in Failures
                if Failure.startswith("missing required artifact: ")
            ),
        })
    return Surface


def _BuildBenchmarkResult(
    BenchmarkManifest: Mapping[str, object],
    ArchiveRoot: Path,
    ExitCode: int,
    VerifiedFiles: Mapping[str, VerifiedRegularFile] | None = None,
) -> dict[str, object]:
    Runs = BuildArchiveRunSurface(
        BenchmarkManifest,
        ArchiveRoot,
        VerifiedFiles,
    )
    return {
        "Status": BenchmarkManifest.get("Status", "UNKNOWN"),
        "Accepted": _FailClosedAcceptanceVerdict(
            BenchmarkManifest.get("Accepted")
        ),
        "ExitCode": ExitCode,
        "PassedRuns": sum(Run.get("Status") == "PASSED" for Run in Runs),
        "FailedRuns": sum(Run.get("Status") == "FAILED" for Run in Runs),
        "SkippedRuns": sum(Run.get("Status") == "SKIPPED" for Run in Runs),
        "Runs": Runs,
    }


def _ReadSourceContentObservation(
    Value: object,
) -> tuple[str, int] | None:
    """Return one complete canonical source-content observation."""
    if not isinstance(Value, Mapping):
        return None
    AggregateSha256 = Value.get("AggregateSha256")
    FileCount = Value.get("FileCount")
    if (
        not isinstance(AggregateSha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", AggregateSha256) is None
        or type(FileCount) is not int
        or FileCount < 0
    ):
        return None
    return AggregateSha256, FileCount


def _BuildArchiveManifest(
    Context: BenchmarkArchiveContext,
    BenchmarkManifest: Mapping[str, object],
    ArchiveRoot: Path,
    EndSource: BenchmarkSourceIdentity,
    *,
    CompletedAtUtc: str,
    WallSeconds: float,
    ExitCode: int,
    ExitClassification: str,
    PublicationStatus: str,
    PublicationFailure: str | None,
    FileInventory: list[dict[str, object]] | None = None,
    VerifiedFiles: Mapping[str, VerifiedRegularFile] | None = None,
) -> dict[str, object]:
    StartSource = Context.Identity.Source
    SourceStable = (
        StartSource.Head == EndSource.Head
        and StartSource.Branch == EndSource.Branch
        and StartSource.Detached == EndSource.Detached
        and StartSource.StatusSha256 == EndSource.StatusSha256
    )
    ProvenanceValue = BenchmarkManifest.get("SourceProvenance", {})
    Provenance = ProvenanceValue if isinstance(ProvenanceValue, dict) else {}
    SourceContentValue = Provenance.get("SourceContent", {})
    SourceContent = (
        SourceContentValue if isinstance(SourceContentValue, dict) else {}
    )
    EndSourceContent: Mapping[str, object] | None = None
    ProvenanceChecksValue = BenchmarkManifest.get("ProvenanceChecks", [])
    if isinstance(ProvenanceChecksValue, list) and ProvenanceChecksValue:
        CheckValue = ProvenanceChecksValue[-1]
        if isinstance(CheckValue, dict):
            CheckProvenanceValue = CheckValue.get("SourceProvenance")
            if isinstance(CheckProvenanceValue, dict):
                CheckSourceContentValue = CheckProvenanceValue.get(
                    "SourceContent"
                )
                if isinstance(CheckSourceContentValue, dict):
                    EndSourceContent = CheckSourceContentValue
    EndSourceContentSummary = (
        {
            "AggregateSha256": EndSourceContent.get("AggregateSha256"),
            "FileCount": EndSourceContent.get("FileCount"),
        }
        if EndSourceContent is not None
        else None
    )
    StartSourceContentObservation = _ReadSourceContentObservation(
        SourceContent
    )
    EndSourceContentObservation = _ReadSourceContentObservation(
        EndSourceContent
    )
    SourceContentStable = (
        StartSourceContentObservation == EndSourceContentObservation
        if (
            StartSourceContentObservation is not None
            and EndSourceContentObservation is not None
        )
        else None
    )
    return {
        "SchemaVersion": ArchiveSchemaVersion,
        "ArchiveId": Context.Identity.ArchiveId,
        "CapturedAtUtc": Context.Identity.CapturedAtUtc,
        "StartedAtUtc": Context.StartedAtUtc,
        "CompletedAtUtc": CompletedAtUtc,
        "Publication": {
            "Status": PublicationStatus,
            "Complete": PublicationStatus == "SEALED",
            "Failure": PublicationFailure,
        },
        "Invocation": {
            "Arguments": list(Context.Command),
            "WorkingDirectory": str(Context.WorkingDirectory.resolve()),
            "MatrixMode": Context.MatrixMode,
            "RoutingThreads": Context.RoutingThreads,
            "BaselineMode": Context.BaselineMode,
        },
        "Source": {
            "Start": asdict(StartSource),
            "End": asdict(EndSource),
            "Stable": SourceStable,
            "AcceptanceProvenanceStable": BenchmarkManifest.get(
                "SourceProvenanceStable"
            ),
            "SourceContent": {
                "Start": {
                    "AggregateSha256": SourceContent.get("AggregateSha256"),
                    "FileCount": SourceContent.get("FileCount"),
                },
                "End": EndSourceContentSummary,
                "Stable": SourceContentStable,
            },
        },
        "Runtime": {
            "WallSeconds": max(0.0, float(WallSeconds)),
            "Environment": BenchmarkManifest.get("Environment"),
            "NativeExtension": Provenance.get("NativeExtension"),
            "Policy": Provenance.get("Policy"),
            "RoutingIdentity": BenchmarkManifest.get("RoutingIdentity"),
            "BenchmarkInputs": Provenance.get("BenchmarkInputs"),
            "PhysicalTemplates": Provenance.get("PhysicalTemplates"),
        },
        "Benchmark": {
            **_BuildBenchmarkResult(
                BenchmarkManifest,
                ArchiveRoot,
                ExitCode,
                VerifiedFiles,
            ),
            "ExitClassification": ExitClassification,
        },
        "Files": (
            FileInventory
            if FileInventory is not None
            else BuildArchiveFileInventory(ArchiveRoot)
        ),
    }


def _VerifiedBytes(Data: bytes) -> VerifiedRegularFile:
    return VerifiedRegularFile(
        Data=Data,
        SizeBytes=len(Data),
        Sha256=sha256(Data).hexdigest(),
    )


def _WriteChecksums(
    ArchiveRoot: Path,
    VerifiedFiles: Mapping[str, VerifiedRegularFile] | None = None,
    RootDescriptor: int | None = None,
) -> VerifiedRegularFile:
    """Write checksum records from the sealing operation's captured bytes."""
    Captured = (
        dict(VerifiedFiles)
        if VerifiedFiles is not None
        else _CaptureArchiveFiles(
            ArchiveRoot,
            ExcludedRelativePaths=frozenset({ArchiveChecksumsName}),
        )
    )
    Lines = [
        f"{Captured[RelativePath].Sha256}  {RelativePath}"
        for RelativePath in sorted(Captured)
    ]
    Data = ("\n".join(Lines) + ("\n" if Lines else "")).encode("utf-8")
    if RootDescriptor is None:
        _AtomicWriteBytes(ArchiveRoot / ArchiveChecksumsName, Data)
    else:
        _AtomicWriteBytesAt(RootDescriptor, ArchiveChecksumsName, Data)
    return _VerifiedBytes(Data)


def _VerifyCapturedArchiveFiles(
    ArchiveRoot: Path,
    Expected: Mapping[str, VerifiedRegularFile],
    RootDescriptor: int | None = None,
) -> None:
    """Fail closed if any published byte differs from the captured seal."""
    Actual = (
        _CaptureArchiveFilesAt(RootDescriptor, frozenset())
        if RootDescriptor is not None
        else _CaptureArchiveFiles(
            ArchiveRoot,
            ExcludedRelativePaths=frozenset(),
        )
    )
    if set(Actual) != set(Expected):
        raise ValueError("benchmark archive seal inventory changed")
    for RelativePath, Identity in Expected.items():
        ActualIdentity = Actual[RelativePath]
        if (
            ActualIdentity.SizeBytes != Identity.SizeBytes
            or ActualIdentity.Sha256 != Identity.Sha256
            or ActualIdentity.Data != Identity.Data
        ):
            raise ValueError(
                "benchmark archive seal input changed: "
                f"{RelativePath}"
            )


def _CopyDirectoryFromDescriptor(
    SourceDescriptor: int,
    DestinationDescriptor: int,
) -> None:
    """Recursively mirror only descriptor-opened regular files/directories."""
    DirectoryFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    FileFlags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        Entries = sorted(os.scandir(SourceDescriptor), key=lambda Entry: Entry.name)
    except OSError as Error:
        raise ValueError(
            "benchmark archive source directory changed during mirroring"
        ) from Error
    for Entry in Entries:
        Name = Entry.name
        if Name in {"", ".", ".."} or "/" in Name:
            raise ValueError("benchmark archive source entry name is invalid")
        try:
            ChildDescriptor = os.open(
                Name,
                DirectoryFlags,
                dir_fd=SourceDescriptor,
            )
        except OSError:
            ChildDescriptor = None
        if ChildDescriptor is not None:
            try:
                ChildStat = os.fstat(ChildDescriptor)
                if not stat.S_ISDIR(ChildStat.st_mode):
                    raise ValueError(
                        "benchmark archive source entry changed during mirroring"
                    )
                os.mkdir(
                    Name,
                    stat.S_IMODE(ChildStat.st_mode),
                    dir_fd=DestinationDescriptor,
                )
                DestinationChildDescriptor = os.open(
                    Name,
                    DirectoryFlags,
                    dir_fd=DestinationDescriptor,
                )
                try:
                    _CopyDirectoryFromDescriptor(
                        ChildDescriptor,
                        DestinationChildDescriptor,
                    )
                    os.fchmod(
                        DestinationChildDescriptor,
                        stat.S_IMODE(ChildStat.st_mode),
                    )
                    os.utime(
                        DestinationChildDescriptor,
                        ns=(ChildStat.st_atime_ns, ChildStat.st_mtime_ns),
                    )
                finally:
                    os.close(DestinationChildDescriptor)
            finally:
                os.close(ChildDescriptor)
            continue
        try:
            FileDescriptor = os.open(
                Name,
                FileFlags,
                dir_fd=SourceDescriptor,
            )
        except OSError as Error:
            raise ValueError(
                "benchmark archive evidence must not contain symlinks or changed entries: "
                f"{Name}"
            ) from Error
        try:
            FileStat = os.fstat(FileDescriptor)
            if not stat.S_ISREG(FileStat.st_mode):
                raise ValueError(
                    "benchmark archive source contains a non-regular entry: "
                    f"{Name}"
                )
            Chunks: list[bytes] = []
            while True:
                Chunk = os.read(FileDescriptor, 1024 * 1024)
                if not Chunk:
                    break
                Chunks.append(Chunk)
            Data = b"".join(Chunks)
            if len(Data) != FileStat.st_size:
                raise ValueError(
                    "benchmark archive source changed while mirroring: "
                    f"{Name}"
                )
        finally:
            os.close(FileDescriptor)
        DestinationFileDescriptor = os.open(
            Name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            stat.S_IMODE(FileStat.st_mode),
            dir_fd=DestinationDescriptor,
        )
        try:
            View = memoryview(Data)
            Written = 0
            while Written < len(View):
                Count = os.write(
                    DestinationFileDescriptor,
                    View[Written:],
                )
                if Count <= 0:
                    raise OSError(
                        "benchmark archive mirror write made no progress"
                    )
                Written += Count
            os.fchmod(
                DestinationFileDescriptor,
                stat.S_IMODE(FileStat.st_mode),
            )
            os.utime(
                DestinationFileDescriptor,
                ns=(FileStat.st_atime_ns, FileStat.st_mtime_ns),
            )
        finally:
            os.close(DestinationFileDescriptor)


def _CopyArchiveSource(Source: Path, Staging: Path) -> list[int]:
    """Mirror one lexical source root and retain the opened staging root."""
    SourceDescriptors = _OpenDirectoryWithoutFollowing(Source)
    if SourceDescriptors is None:
        raise ValueError(
            "benchmark archive source is not a safely readable directory"
        )
    StagingParentDescriptors: list[int] | None = None
    StagingDescriptor: int | None = None
    try:
        StagingParentDescriptors = _OpenDirectoryWithoutFollowing(
            Staging.parent
        )
        if StagingParentDescriptors is None:
            raise ValueError(
                "benchmark archive staging parent is not safely readable"
            )
        os.mkdir(Staging.name, dir_fd=StagingParentDescriptors[-1])
        StagingDescriptor = os.open(
            Staging.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=StagingParentDescriptors[-1],
        )
        _CopyDirectoryFromDescriptor(
            SourceDescriptors[-1],
            StagingDescriptor,
        )
    except Exception:
        if StagingDescriptor is not None:
            os.close(StagingDescriptor)
        if StagingParentDescriptors is not None:
            for Descriptor in reversed(StagingParentDescriptors):
                os.close(Descriptor)
        raise
    finally:
        for SourceDescriptor in reversed(SourceDescriptors):
            os.close(SourceDescriptor)
    assert StagingParentDescriptors is not None
    assert StagingDescriptor is not None
    return [*StagingParentDescriptors, StagingDescriptor]


def PublishBenchmarkArchive(
    Context: BenchmarkArchiveContext,
    BenchmarkManifest: Mapping[str, object],
    *,
    CompletedAtUtc: str,
    WallSeconds: float,
    ExitCode: int,
    ExitClassification: str,
    PublicationStatus: str = "SEALED",
    PublicationFailure: str | None = None,
    SourceIdentityReader: Callable[[Path], BenchmarkSourceIdentity] = (
        ReadBenchmarkSourceIdentity
    ),
) -> Path:
    """Seal an in-place archive or atomically mirror one legacy session."""
    if PublicationStatus not in {"SEALED", "PARTIAL", "INTERRUPTED"}:
        raise ValueError(
            "unsupported archive publication status: "
            f"{PublicationStatus}"
        )
    if PublicationStatus == "SEALED":
        ValidateExactAcceptanceVerdicts(BenchmarkManifest)
    Target = Path(os.path.abspath(os.fspath(Context.ArchiveDirectory)))
    Source = Path(os.path.abspath(os.fspath(Context.SourceDirectory)))
    Mirror = Source != Target
    Staging = Target
    StagingDescriptors: list[int]
    if Mirror:
        EnsureArchiveTargetAvailable(Target)
        Target.parent.mkdir(parents=True, exist_ok=True)
        Staging = Target.parent / f".{Target.name}.tmp-P{os.getpid()}"
        EnsureArchiveTargetAvailable(Staging)
        StagingDescriptors = _CopyArchiveSource(Source, Staging)
    else:
        Staging.mkdir(parents=True, exist_ok=True)
        OpenedStaging = _OpenDirectoryWithoutFollowing(Staging)
        if OpenedStaging is None:
            raise ValueError(
                "benchmark archive staging root is not safely readable"
            )
        StagingDescriptors = OpenedStaging

    ArchiveManifest: dict[str, object] | None = None
    try:
        StagingDescriptor = StagingDescriptors[-1]
        BenchmarkResultBytes = _PrettyJson(dict(BenchmarkManifest)).encode(
            "utf-8"
        )
        _AtomicWriteBytesAt(
            StagingDescriptor,
            ArchiveResultName,
            BenchmarkResultBytes,
        )
        CapturedFiles = _CaptureArchiveFilesAt(
            StagingDescriptor,
            frozenset({ArchiveManifestName, ArchiveChecksumsName}),
        )
        EndSource = SourceIdentityReader(Context.WorkingDirectory)
        ArchiveManifest = _BuildArchiveManifest(
            Context,
            BenchmarkManifest,
            Staging,
            EndSource,
            CompletedAtUtc=CompletedAtUtc,
            WallSeconds=WallSeconds,
            ExitCode=ExitCode,
            ExitClassification=ExitClassification,
            PublicationStatus=PublicationStatus,
            PublicationFailure=PublicationFailure,
            FileInventory=_BuildInventoryRecords(CapturedFiles),
            VerifiedFiles=CapturedFiles,
        )
        ArchiveManifestBytes = _PrettyJson(ArchiveManifest).encode("utf-8")
        _AtomicWriteBytesAt(
            StagingDescriptor,
            ArchiveManifestName,
            ArchiveManifestBytes,
        )
        ChecksumInputs = {
            **CapturedFiles,
            ArchiveManifestName: _VerifiedBytes(ArchiveManifestBytes),
        }
        Checksums = _WriteChecksums(
            Staging,
            ChecksumInputs,
            StagingDescriptor,
        )
        _VerifyCapturedArchiveFiles(
            Staging,
            {
                **ChecksumInputs,
                ArchiveChecksumsName: Checksums,
            },
            StagingDescriptor,
        )
        if Mirror:
            StagingStat = os.fstat(StagingDescriptor)
            StagingEntryStat = os.stat(
                Staging.name,
                dir_fd=StagingDescriptors[-2],
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(StagingEntryStat.st_mode)
                or StagingEntryStat.st_dev != StagingStat.st_dev
                or StagingEntryStat.st_ino != StagingStat.st_ino
            ):
                raise ValueError(
                    "benchmark archive staging root changed before publication"
                )
            os.rename(
                Staging.name,
                Target.name,
                src_dir_fd=StagingDescriptors[-2],
                dst_dir_fd=StagingDescriptors[-2],
            )
        return Target
    except Exception as Error:
        if ArchiveManifest is not None:
            try:
                ArchiveManifest["Publication"] = {
                    "Complete": False,
                    "Failure": f"archive sealing failed: {type(Error).__name__}",
                    "Status": "PARTIAL",
                }
                _AtomicWriteBytesAt(
                    StagingDescriptors[-1],
                    ArchiveManifestName,
                    _PrettyJson(ArchiveManifest).encode("utf-8"),
                )
                try:
                    os.unlink(
                        ArchiveChecksumsName,
                        dir_fd=StagingDescriptors[-1],
                    )
                except FileNotFoundError:
                    pass
            except OSError:
                pass
        if Mirror:
            try:
                _AtomicWriteBytesAt(
                    StagingDescriptors[-1],
                    "ARCHIVE_PARTIAL.txt",
                    b"Archive mirroring did not complete. Original evidence remains intact.\n",
                )
                StagingStat = os.fstat(StagingDescriptors[-1])
                StagingEntryStat = os.stat(
                    Staging.name,
                    dir_fd=StagingDescriptors[-2],
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISDIR(StagingEntryStat.st_mode)
                    and StagingEntryStat.st_dev == StagingStat.st_dev
                    and StagingEntryStat.st_ino == StagingStat.st_ino
                ):
                    os.rename(
                        Staging.name,
                        Target.name,
                        src_dir_fd=StagingDescriptors[-2],
                        dst_dir_fd=StagingDescriptors[-2],
                    )
            except OSError:
                pass
        raise
    finally:
        for Descriptor in reversed(StagingDescriptors):
            os.close(Descriptor)

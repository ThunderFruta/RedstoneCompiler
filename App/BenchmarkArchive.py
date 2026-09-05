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
import subprocess
from typing import Callable, Mapping, Sequence


ArchiveSchemaVersion = "router-benchmark-archive-v1"
ArchiveManifestName = "ArchiveManifest.json"
ArchiveChecksumsName = "SHA256SUMS"
ArchiveResultName = "BenchmarkResult.json"
GitRunner = Callable[[Path, Sequence[str]], bytes]


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
    """Hash one regular file incrementally."""
    Digest = sha256()
    with PathValue.open("rb") as InputFile:
        for Chunk in iter(lambda: InputFile.read(1024 * 1024), b""):
            Digest.update(Chunk)
    return Digest.hexdigest()


def _AtomicWriteText(PathValue: Path, Text: str) -> None:
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    TemporaryPath = PathValue.with_name(
        f".{PathValue.name}.tmp-P{os.getpid()}"
    )
    TemporaryPath.write_text(Text, encoding="utf-8", newline="\n")
    TemporaryPath.replace(PathValue)


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
    Excluded = frozenset({ArchiveManifestName, ArchiveChecksumsName})
    return [
        {
            "Path": PathValue.relative_to(Root).as_posix(),
            "SizeBytes": PathValue.stat().st_size,
            "Sha256": Sha256File(PathValue),
        }
        for PathValue in _RelativeFiles(
            Root,
            ExcludedRelativePaths=Excluded,
        )
    ]


def _ReadRoutingFailureSurface(
    ArchiveRoot: Path,
    RunName: str,
) -> dict[str, object]:
    RunDirectory = ArchiveRoot / RunName
    if not RunDirectory.is_dir():
        return {}
    Candidates = sorted(
        RunDirectory.rglob("*.RoutingFailure.json"),
        key=lambda Value: Value.as_posix(),
    )
    if not Candidates:
        return {}
    try:
        Payload = json.loads(Candidates[-1].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    Failure = Payload.get("Failure", Payload)
    if not isinstance(Failure, dict):
        return {}
    return {
        Key: Failure.get(Key)
        for Key in ("Stage", "Reason", "Detail")
        if Failure.get(Key) is not None
    }


def BuildArchiveRunSurface(
    BenchmarkManifest: Mapping[str, object],
    ArchiveRoot: Path,
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
        FailuresValue = Evaluation.get("Failures", [])
        Failures = (
            [str(Value) for Value in FailuresValue]
            if isinstance(FailuresValue, list)
            else []
        )
        RunName = str(RawRun.get("RunName", ""))
        FailureSurface = _ReadRoutingFailureSurface(ArchiveRoot, RunName)
        Surface.append({
            "Sequence": RawRun.get("Sequence"),
            "RunName": RunName,
            "Circuit": RawRun.get("Circuit"),
            "Status": RawRun.get("Status"),
            "Accepted": bool(RawRun.get("Accepted")),
            "WallRuntimeSeconds": Process.get("WallRuntimeSeconds"),
            "ReturnCode": Process.get("ReturnCode"),
            "TimedOut": Process.get("TimedOut"),
            "Stage": FailureSurface.get("Stage"),
            "Reason": FailureSurface.get("Reason"),
            "Detail": FailureSurface.get("Detail"),
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
) -> dict[str, object]:
    Runs = BuildArchiveRunSurface(BenchmarkManifest, ArchiveRoot)
    return {
        "Status": BenchmarkManifest.get("Status", "UNKNOWN"),
        "Accepted": bool(BenchmarkManifest.get("Accepted")),
        "ExitCode": ExitCode,
        "PassedRuns": sum(Run.get("Status") == "PASSED" for Run in Runs),
        "FailedRuns": sum(Run.get("Status") == "FAILED" for Run in Runs),
        "SkippedRuns": sum(Run.get("Status") == "SKIPPED" for Run in Runs),
        "Runs": Runs,
    }


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
    EndSourceContent: Mapping[str, object] = SourceContent
    ProvenanceChecksValue = BenchmarkManifest.get("ProvenanceChecks", [])
    if isinstance(ProvenanceChecksValue, list):
        for CheckValue in reversed(ProvenanceChecksValue):
            if not isinstance(CheckValue, dict):
                continue
            CheckProvenanceValue = CheckValue.get("SourceProvenance", {})
            if not isinstance(CheckProvenanceValue, dict):
                continue
            CheckSourceContentValue = CheckProvenanceValue.get(
                "SourceContent",
                {},
            )
            if isinstance(CheckSourceContentValue, dict):
                EndSourceContent = CheckSourceContentValue
                break
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
                "End": {
                    "AggregateSha256": EndSourceContent.get(
                        "AggregateSha256"
                    ),
                    "FileCount": EndSourceContent.get("FileCount"),
                },
                "Stable": (
                    SourceContent.get("AggregateSha256")
                    == EndSourceContent.get("AggregateSha256")
                ),
            },
        },
        "Runtime": {
            "WallSeconds": max(0.0, float(WallSeconds)),
            "Environment": BenchmarkManifest.get("Environment"),
            "NativeExtension": Provenance.get("NativeExtension"),
            "Policy": Provenance.get("Policy"),
            "BenchmarkInputs": Provenance.get("BenchmarkInputs"),
            "PhysicalTemplates": Provenance.get("PhysicalTemplates"),
        },
        "Benchmark": {
            **_BuildBenchmarkResult(
                BenchmarkManifest,
                ArchiveRoot,
                ExitCode,
            ),
            "ExitClassification": ExitClassification,
        },
        "Files": BuildArchiveFileInventory(ArchiveRoot),
    }


def _WriteChecksums(ArchiveRoot: Path) -> None:
    Files = _RelativeFiles(
        ArchiveRoot,
        ExcludedRelativePaths=frozenset({ArchiveChecksumsName}),
    )
    Lines = [
        f"{Sha256File(PathValue)}  {PathValue.relative_to(ArchiveRoot).as_posix()}"
        for PathValue in Files
    ]
    _AtomicWriteText(
        ArchiveRoot / ArchiveChecksumsName,
        "\n".join(Lines) + ("\n" if Lines else ""),
    )


def _CopyArchiveSource(Source: Path, Staging: Path) -> None:
    if not Source.is_dir():
        raise FileNotFoundError(
            f"benchmark archive source directory does not exist: {Source}"
        )
    _RelativeFiles(Source)
    shutil.copytree(Source, Staging, copy_function=shutil.copy2)


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
    Target = Context.ArchiveDirectory.resolve(strict=False)
    Source = Context.SourceDirectory.resolve(strict=False)
    Mirror = Source != Target
    Staging = Target
    if Mirror:
        EnsureArchiveTargetAvailable(Target)
        Target.parent.mkdir(parents=True, exist_ok=True)
        Staging = Target.parent / f".{Target.name}.tmp-P{os.getpid()}"
        EnsureArchiveTargetAvailable(Staging)
        _CopyArchiveSource(Source, Staging)
    else:
        Staging.mkdir(parents=True, exist_ok=True)

    try:
        _RelativeFiles(Staging)
        _AtomicWriteText(
            Staging / ArchiveResultName,
            _PrettyJson(dict(BenchmarkManifest)),
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
        )
        _AtomicWriteText(
            Staging / ArchiveManifestName,
            _PrettyJson(ArchiveManifest),
        )
        _WriteChecksums(Staging)
        if Mirror:
            Staging.replace(Target)
        return Target
    except Exception:
        if Mirror and Staging.exists() and not Target.exists():
            try:
                _AtomicWriteText(
                    Staging / "ARCHIVE_PARTIAL.txt",
                    "Archive mirroring did not complete. Original evidence remains intact.\n",
                )
                Staging.replace(Target)
            except OSError:
                pass
        raise

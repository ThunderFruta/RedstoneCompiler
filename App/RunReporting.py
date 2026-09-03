"""Shared human-readable run reporting and immutable evidence helpers."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from threading import Lock
from typing import IO, Iterable, Mapping


ReportFileNames = frozenset({"Summary.txt", "RawDump.txt"})
SafeEnvironmentNames = (
    "PYTHONHASHSEED",
    "RC_ROUTING_THREADS",
    "RC_ROUTING_TELEMETRY",
    "OMP_NUM_THREADS",
    "RAYON_NUM_THREADS",
)


def BuildRunId() -> str:
    """Return a collision-resistant, sortable UTC run identifier."""
    Timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{Timestamp}-P{os.getpid()}"


def UtcTimestamp() -> str:
    """Return one ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _RunGit(RepositoryRoot: Path, Arguments: list[str]) -> str:
    try:
        Completed = subprocess.run(
            ["git", *Arguments],
            cwd=RepositoryRoot,
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if Completed.returncode != 0:
        return "unavailable"
    return Completed.stdout.rstrip() or "clean"


def BuildGitIdentity(RepositoryRoot: Path) -> dict[str, object]:
    """Capture checkout identity without modifying the working tree."""
    Root = RepositoryRoot.resolve(strict=False)
    Status = _RunGit(Root, ["status", "--short"])
    return {
        "RepositoryRoot": str(Root),
        "Branch": _RunGit(Root, ["branch", "--show-current"]),
        "Head": _RunGit(Root, ["rev-parse", "HEAD"]),
        "Dirty": Status not in {"clean", "unavailable"},
        "Status": Status,
        "Worktrees": _RunGit(Root, ["worktree", "list", "--porcelain"]),
    }


def _LoadedNativeExtensions() -> list[dict[str, str]]:
    Extensions: list[dict[str, str]] = []
    SeenPaths: set[Path] = set()
    for Name, Module in sorted(sys.modules.items()):
        RawPath = getattr(Module, "__file__", None)
        if not RawPath or "RustRouting" not in Name + str(RawPath):
            continue
        ModulePath = Path(RawPath).resolve(strict=False)
        if ModulePath in SeenPaths or not ModulePath.is_file():
            continue
        SeenPaths.add(ModulePath)
        try:
            Digest = sha256(ModulePath.read_bytes()).hexdigest()
        except OSError:
            Digest = "unavailable"
        Extensions.append({
            "Module": Name,
            "Path": str(ModulePath),
            "Sha256": Digest,
        })
    return Extensions


def BuildRuntimeProvenance() -> dict[str, object]:
    """Capture safe host/runtime facts without serializing arbitrary secrets."""
    return {
        "PythonExecutable": sys.executable,
        "PythonVersion": platform.python_version(),
        "Platform": platform.platform(),
        "Machine": platform.machine(),
        "LogicalCpuCount": os.cpu_count() or 1,
        "SafeEnvironment": {
            Name: os.environ[Name]
            for Name in SafeEnvironmentNames
            if Name in os.environ
        },
        "LoadedNativeExtensions": _LoadedNativeExtensions(),
    }


def Sha256File(PathValue: Path) -> str:
    """Hash one artifact incrementally."""
    Digest = sha256()
    with PathValue.open("rb") as ArtifactFile:
        while True:
            Chunk = ArtifactFile.read(1024 * 1024)
            if not Chunk:
                break
            Digest.update(Chunk)
    return Digest.hexdigest()


def BuildArtifactInventory(
    Roots: Iterable[Path],
) -> list[dict[str, object]]:
    """Inventory regular artifact files while excluding report self-hashes."""
    Files: set[Path] = set()
    for Root in Roots:
        Resolved = Root.resolve(strict=False)
        if Resolved.is_file():
            Files.add(Resolved)
        elif Resolved.is_dir():
            Files.update(
                PathValue.resolve(strict=False)
                for PathValue in Resolved.rglob("*")
                if PathValue.is_file()
            )
    Inventory = []
    for ArtifactPath in sorted(Files, key=str):
        if ArtifactPath.name in ReportFileNames:
            continue
        try:
            Inventory.append({
                "Path": str(ArtifactPath),
                "Bytes": ArtifactPath.stat().st_size,
                "Sha256": Sha256File(ArtifactPath),
            })
        except OSError as Error:
            Inventory.append({
                "Path": str(ArtifactPath),
                "Error": str(Error),
            })
    return Inventory


def _JsonText(Value: object) -> str:
    return json.dumps(Value, indent=2, sort_keys=True, default=str)


def _NormalizeSummary(Value: str, MaximumCharacters: int = 180) -> str:
    Summary = " ".join(str(Value).split())
    if len(Summary) <= MaximumCharacters:
        return Summary
    return Summary[: MaximumCharacters - 3].rstrip() + "..."


def FormatResultLines(
    *,
    Result: str,
    WallSeconds: float,
    CpuSeconds: float | None,
    Summary: str,
    RawReportPath: Path,
    FailureType: str | None = None,
    CpuDetails: Mapping[str, object] | None = None,
    TimingDetails: Mapping[str, object] | None = None,
    IncludeRoutingDetails: bool = False,
) -> list[str]:
    """Format concise terminal output or the detailed saved-report variant."""
    ResultLine = f"RESULT: {Result}"
    if FailureType:
        ResultLine += f" — {FailureType}"
    TimeLine = f"TIME: total wall={max(0.0, WallSeconds):.3f}s"
    CalculatedAverageCores: float | None = None
    if CpuSeconds is not None:
        SafeCpuSeconds = max(0.0, CpuSeconds)
        UtilizationPercent = (
            SafeCpuSeconds / WallSeconds * 100.0
            if WallSeconds > 0.0
            else 0.0
        )
        CalculatedAverageCores = (
            SafeCpuSeconds / WallSeconds if WallSeconds > 0.0 else 0.0
        )
        TimeLine += (
            f" cpu={SafeCpuSeconds:.3f}s"
            f" utilization={UtilizationPercent:.1f}%"
        )
    Lines = [
        ResultLine,
        TimeLine,
    ]
    if TimingDetails is not None:
        Intervals = TimingDetails.get("Intervals", {})
        SafeIntervals = Intervals if isinstance(Intervals, Mapping) else {}
        for IntervalName in ("Routing",):
            Interval = SafeIntervals.get(IntervalName)
            if not isinstance(Interval, Mapping):
                Lines.append(f"TIME: {IntervalName.lower()} not-run")
                continue
            IntervalWall = Interval.get("WallSeconds")
            IntervalCpu = Interval.get("CpuSeconds")
            if not isinstance(IntervalWall, (int, float)):
                continue
            IntervalLine = (
                f"TIME: {IntervalName.lower()} "
                f"wall={max(0.0, float(IntervalWall)):.3f}s"
            )
            if isinstance(IntervalCpu, (int, float)):
                SafeIntervalCpu = max(0.0, float(IntervalCpu))
                IntervalUtilization = (
                    SafeIntervalCpu / float(IntervalWall) * 100.0
                    if IntervalWall > 0.0
                    else 0.0
                )
                IntervalLine += (
                    f" cpu={SafeIntervalCpu:.3f}s"
                    f" utilization={IntervalUtilization:.1f}%"
                )
            Lines.append(IntervalLine)
        RoutingStages = TimingDetails.get("RoutingStages", ())
        if IncludeRoutingDetails and isinstance(RoutingStages, (list, tuple)):
            for Stage in RoutingStages:
                if not isinstance(Stage, Mapping):
                    continue
                StageName = str(Stage.get("Stage", "unknown"))
                StageWall = Stage.get("WallSeconds")
                StageCpu = Stage.get("CpuSeconds")
                EventCount = Stage.get("Events")
                if not isinstance(StageWall, (int, float)):
                    continue
                StageLine = (
                    f"  {StageName}: "
                    f"wall={max(0.0, float(StageWall)):.3f}s"
                )
                if isinstance(StageCpu, (int, float)):
                    StageLine += f" cpu={max(0.0, float(StageCpu)):.3f}s"
                    if StageWall >= 0.1:
                        StageLine += f" average_cores={float(StageCpu) / float(StageWall):.2f}"
                if isinstance(EventCount, int) and EventCount > 1:
                    StageLine += f" events={EventCount}"
                Lines.append(StageLine)
        Validation = SafeIntervals.get("Validation")
        if not isinstance(Validation, Mapping):
            Lines.append("TIME: validation not-run")
        else:
            ValidationWall = Validation.get("WallSeconds")
            ValidationCpu = Validation.get("CpuSeconds")
            if isinstance(ValidationWall, (int, float)):
                ValidationLine = (
                    "TIME: validation "
                    f"wall={max(0.0, float(ValidationWall)):.3f}s"
                )
                if isinstance(ValidationCpu, (int, float)):
                    SafeValidationCpu = max(0.0, float(ValidationCpu))
                    ValidationUtilization = (
                        SafeValidationCpu / float(ValidationWall) * 100.0
                        if ValidationWall > 0.0
                        else 0.0
                    )
                    ValidationLine += (
                        f" cpu={SafeValidationCpu:.3f}s"
                        f" utilization={ValidationUtilization:.1f}%"
                    )
                Lines.append(ValidationLine)
    Details = dict(CpuDetails or {})
    CpuParts = []
    for Key, Label in (
        ("UserSeconds", "user"),
        ("SystemSeconds", "system"),
        ("ChildCpuSeconds", "child"),
    ):
        Value = Details.get(Key)
        if isinstance(Value, (int, float)):
            CpuParts.append(f"{Label}={max(0.0, float(Value)):.3f}s")
    AverageCores = Details.get("AverageCores", CalculatedAverageCores)
    if isinstance(AverageCores, (int, float)):
        CpuParts.append(
            f"average_cores={max(0.0, float(AverageCores)):.2f}"
        )
    LogicalCpus = Details.get("LogicalCpus")
    if LogicalCpus is not None:
        CpuParts.append(f"logical_cpus={LogicalCpus}")
    RoutingLimit = Details.get("NativeRoutingLimit")
    if RoutingLimit is not None:
        CpuParts.append(f"routing_limit={RoutingLimit}")
    if CpuParts:
        Lines.append("CPU: " + " ".join(CpuParts))
    Detailed = TimingDetails.get("Detailed") if TimingDetails else None
    if IncludeRoutingDetails and isinstance(Detailed, Mapping):
        Lines.append(
            "TELEMETRY: " + str(Detailed.get("Status", "unavailable"))
            + f" samples={Detailed.get('SampleCount', 0)}"
            + f" raw={RawReportPath.parent / 'RoutingTelemetry.samples.jsonl'}"
        )
    Lines.extend([
        f"OUTPUT: {_NormalizeSummary(Summary)}",
        f"RAW REPORT: {RawReportPath.resolve(strict=False)}",
    ])
    return Lines


def _AtomicWriteText(PathValue: Path, Text: str) -> None:
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    TemporaryPath = PathValue.with_name(
        f".{PathValue.name}.tmp-P{os.getpid()}"
    )
    TemporaryPath.write_text(Text, encoding="utf-8")
    TemporaryPath.replace(PathValue)


@dataclass(frozen=True)
class RunReportResult:
    SummaryPath: Path
    RawReportPath: Path
    ResultLines: tuple[str, ...]


def WriteRunReport(
    *,
    RunDirectory: Path,
    Result: str,
    WallSeconds: float,
    CpuSeconds: float | None,
    Summary: str,
    RepositoryRoot: Path,
    StartedAtUtc: str,
    CompletedAtUtc: str,
    Command: Iterable[str],
    WorkingDirectory: Path,
    Stdout: str = "",
    Stderr: str = "",
    FailureType: str | None = None,
    CpuDetails: Mapping[str, object] | None = None,
    TimingDetails: Mapping[str, object] | None = None,
    ExceptionText: str = "",
    Details: Mapping[str, object] | None = None,
    ArtifactRoots: Iterable[Path] = (),
) -> RunReportResult:
    """Persist the concise summary and comprehensive text evidence atomically."""
    Directory = RunDirectory.resolve(strict=False)
    Directory.mkdir(parents=True, exist_ok=True)
    SummaryPath = Directory / "Summary.txt"
    RawReportPath = Directory / "RawDump.txt"
    ResultArguments = dict(
        Result=Result,
        WallSeconds=WallSeconds,
        CpuSeconds=CpuSeconds,
        Summary=Summary,
        RawReportPath=RawReportPath,
        FailureType=FailureType,
        CpuDetails=CpuDetails,
        TimingDetails=TimingDetails,
    )
    ResultLines = FormatResultLines(**ResultArguments)
    SavedLines = FormatResultLines(**ResultArguments, IncludeRoutingDetails=True)
    InventoryRoots = [Directory, *ArtifactRoots]
    Sections: list[tuple[str, str]] = [
        ("RUN", "\n".join(SavedLines)),
        ("TIMESTAMPS", _JsonText({
            "StartedAtUtc": StartedAtUtc,
            "CompletedAtUtc": CompletedAtUtc,
        })),
        ("COMMAND", _JsonText({
            "Arguments": list(Command),
            "WorkingDirectory": str(WorkingDirectory.resolve(strict=False)),
        })),
        ("GIT IDENTITY", _JsonText(BuildGitIdentity(RepositoryRoot))),
        ("RUNTIME PROVENANCE", _JsonText(BuildRuntimeProvenance())),
        ("DETAILS", _JsonText(dict(Details or {}))),
        ("STDOUT", Stdout.rstrip()),
        ("STDERR", Stderr.rstrip()),
        ("EXCEPTION", ExceptionText.rstrip()),
        ("ARTIFACT INVENTORY", _JsonText(BuildArtifactInventory(InventoryRoots))),
    ]
    RawText = "\n\n".join(
        f"===== {Name} =====\n{Text if Text else '<empty>'}"
        for Name, Text in Sections
    ) + "\n"
    _AtomicWriteText(RawReportPath, RawText)
    _AtomicWriteText(SummaryPath, "\n".join(SavedLines) + "\n")
    return RunReportResult(
        SummaryPath=SummaryPath,
        RawReportPath=RawReportPath,
        ResultLines=tuple(ResultLines),
    )


class _TeeStream:
    """Write terminal text to its original destination and an in-memory copy."""

    def __init__(self, Original: IO[str]) -> None:
        self.Original = Original
        self.Buffer = StringIO()
        self.Lock = Lock()

    def write(self, Value: str) -> int:
        with self.Lock:
            self.Buffer.write(Value)
            Written = self.Original.write(Value)
            return len(Value) if Written is None else Written

    def flush(self) -> None:
        with self.Lock:
            self.Original.flush()

    def isatty(self) -> bool:
        return self.Original.isatty()

    def fileno(self) -> int:
        return self.Original.fileno()

    @property
    def encoding(self) -> str | None:
        return getattr(self.Original, "encoding", None)

    def GetValue(self) -> str:
        with self.Lock:
            return self.Buffer.getvalue()


class CaptureTerminalOutput:
    """Tee Python stdout/stderr while retaining complete report text."""

    def __init__(self) -> None:
        self.Stdout = _TeeStream(sys.stdout)
        self.Stderr = _TeeStream(sys.stderr)
        self._StdoutRedirect = redirect_stdout(self.Stdout)
        self._StderrRedirect = redirect_stderr(self.Stderr)

    def __enter__(self) -> "CaptureTerminalOutput":
        self._StdoutRedirect.__enter__()
        self._StderrRedirect.__enter__()
        return self

    def __exit__(self, *Arguments: object) -> None:
        self._StderrRedirect.__exit__(*Arguments)
        self._StdoutRedirect.__exit__(*Arguments)

    @property
    def StdoutText(self) -> str:
        return self.Stdout.GetValue()

    @property
    def StderrText(self) -> str:
        return self.Stderr.GetValue()


def PromoteRunArtifacts(
    *,
    RunDirectory: Path,
    RunBaseName: str,
    StableOutputPath: Path,
) -> list[Path]:
    """Atomically promote successful core artifacts to stable circuit paths."""
    StableBaseName = StableOutputPath.stem
    Promoted: list[Path] = []
    Suffixes = (
        ".litematic",
        ".ServerUpdated.litematic",
        ".Nand.json",
        ".PhysicalDesign.json",
        ".PhysicalFixture.json",
        ".FabricFixture.json",
    )
    StableOutputPath.parent.mkdir(parents=True, exist_ok=True)
    for Suffix in Suffixes:
        SourcePath = RunDirectory / f"{RunBaseName}{Suffix}"
        if not SourcePath.is_file():
            continue
        DestinationPath = StableOutputPath.parent / f"{StableBaseName}{Suffix}"
        TemporaryPath = DestinationPath.with_name(
            f".{DestinationPath.name}.promote-P{os.getpid()}"
        )
        shutil.copy2(SourcePath, TemporaryPath)
        TemporaryPath.replace(DestinationPath)
        Promoted.append(DestinationPath)
    return Promoted

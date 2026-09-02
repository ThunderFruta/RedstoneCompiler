"""Argument-driven RedstoneCompiler compiler entrypoint."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr
import json
from io import StringIO
from math import isfinite
from pathlib import Path
import shutil
import subprocess
import sys
import os
import time
import traceback
from threading import Event, Lock, Thread, active_count

if __package__:
    from .Pipeline import CompileSvToLitematic
    from .PhysicalValidation import PhysicalValidationProgress
    from Compiler.Placement.Flow.Results import PcbProgress
    from .Routing.Policy import RoutingStrategy
    from .RunReporting import (
        BuildRunId,
        CaptureTerminalOutput,
        FormatResultLines,
        PromoteRunArtifacts,
        UtcTimestamp,
        WriteRunReport,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from Compiler.Pipeline import CompileSvToLitematic
    from Compiler.PhysicalValidation import PhysicalValidationProgress
    from Compiler.Placement.Flow.Results import PcbProgress
    from Compiler.Routing.Policy import RoutingStrategy
    from Compiler.RunReporting import (
        BuildRunId,
        CaptureTerminalOutput,
        FormatResultLines,
        PromoteRunArtifacts,
        UtcTimestamp,
        WriteRunReport,
    )


MinecraftSchematicsDirectory = Path(
    "/home/bananawewe/.local/share/PrismLauncher/instances/wee 26.2/minecraft/schematics"
)
DefaultsPath = (
    Path.home()
    / ".config"
    / "RedstoneCompiler"
    / "Defaults.json"
)
BuiltInDefaults = {
    "InputPath": "Examples/FullAdder.sv",
    "OutputDirectory": "Output",
    "OutputName": "",
    "TopModule": "",
    "WorkDirectory": "Cache/Frontend",
    "PushToMinecraft": True,
    "MinecraftDirectory": str(MinecraftSchematicsDirectory),
    "PushFilePath": "Output/FullAdder/FullAdder.litematic",
        "TraceSupportBlocks": [
        "minecraft:light_gray_concrete",
        "minecraft:yellow_concrete",
        "minecraft:lime_concrete",
        "minecraft:light_blue_concrete",
        "minecraft:red_concrete",
        "minecraft:orange_concrete",
        "minecraft:magenta_concrete",
    ],
}


class CpuRunTelemetry:
    """Process CPU and thread telemetry for one CLI invocation."""

    def __init__(self) -> None:
        self.StartedAt = time.monotonic()
        self.StartTimes = os.times()
        self.CompileStartedAt: float | None = None
        self.CompileStartTimes: os.times_result | None = None
        self.CompileFinishedAt: float | None = None
        self.CompileFinishTimes: os.times_result | None = None
        self.IntervalStarts: dict[str, tuple[float, os.times_result]] = {}
        self.IntervalFinishes: dict[str, tuple[float, os.times_result]] = {}
        self.RoutingStages: list[tuple[str, float, os.times_result]] = []
        self.PeakOsThreads = self.ReadOsThreadCount()
        self.PeakPythonThreads = active_count()
        self.LastOsThreads = self.PeakOsThreads
        self.LastPythonThreads = self.PeakPythonThreads
        self.StopEvent = Event()
        self.SampleThread = Thread(
            target=self.Sample,
            name="redstone-cpu-telemetry",
            daemon=True,
        )
        self.SampleThread.start()
        self.HasPrinted = False
        self.PrintLock = Lock()
        self.Stopped = False

    @staticmethod
    def ReadOsThreadCount() -> int:
        """Return Linux process thread count without requiring psutil."""
        try:
            with open(
                "/proc/self/status",
                "r",
                encoding="utf-8",
            ) as StatusFile:
                for Line in StatusFile:
                    if Line.startswith("Threads:"):
                        return max(1, int(Line.split(":", 1)[1]))
        except OSError:
            pass
        return 1

    def Sample(self) -> None:
        # Thread-count sampling is observational and must not contend with
        # the bounded Python placement/factor orchestration at 10 Hz.  Native
        # routing pools live long enough for a 2 Hz sample to retain useful
        # peak telemetry without consuming a material share of an 8-second
        # compile budget.
        while not self.StopEvent.wait(0.5):
            self.LastOsThreads = self.ReadOsThreadCount()
            self.LastPythonThreads = active_count()
            self.PeakOsThreads = max(self.PeakOsThreads, self.LastOsThreads)
            self.PeakPythonThreads = max(
                self.PeakPythonThreads,
                self.LastPythonThreads,
            )

    def BeginCompilation(self) -> None:
        self.CompileStartedAt = time.monotonic()
        self.CompileStartTimes = os.times()

    def FinishCompilation(self) -> None:
        if self.CompileStartedAt is not None:
            self.CompileFinishedAt = time.monotonic()
            self.CompileFinishTimes = os.times()

    def RecordPipelineTimingEvent(self, Name: str, Event: str) -> None:
        """Capture exact pipeline interval boundaries for final reporting."""
        if Name == "RoutingStage":
            self.RecordRoutingStage(Event)
            return
        CapturedAt = time.monotonic()
        CapturedTimes = os.times()
        if Event == "begin":
            self.IntervalStarts[Name] = (CapturedAt, CapturedTimes)
            self.IntervalFinishes.pop(Name, None)
            if Name == "Routing":
                self.RoutingStages = [
                    ("routing setup", CapturedAt, CapturedTimes),
                ]
            return
        if Event == "finish" and Name in self.IntervalStarts:
            self.IntervalFinishes[Name] = (CapturedAt, CapturedTimes)

    def RecordRoutingStage(self, StageValue: str) -> None:
        """Record one stable routing stage transition."""
        Stage = str(StageValue)
        # Progress text may append per-net/iteration diagnostics after a
        # separator.  Keep the stable stage name so the final CPU report is
        # useful rather than hundreds of near-duplicate lines.
        StageParts = Stage.split(" | ")
        if StageParts and StageParts[0].startswith("spacing "):
            StageParts = StageParts[1:]
        if StageParts:
            Stage = StageParts[0]
        if self.RoutingStages and self.RoutingStages[-1][0] == Stage:
            return
        self.RoutingStages.append((Stage, time.monotonic(), os.times()))

    def RecordRoutingProgress(self, Progress: PcbProgress) -> None:
        """Record routing progress without coupling timing to rendering."""
        self.RecordRoutingStage(str(Progress.Stage))

    @staticmethod
    def FormatInterval(
        Label: str,
        StartedAt: float,
        StartedTimes: os.times_result,
        FinishedAt: float,
        FinishedTimes: os.times_result,
    ) -> str:
        WallSeconds = max(0.0, FinishedAt - StartedAt)
        UserSeconds = max(0.0, FinishedTimes.user - StartedTimes.user)
        SystemSeconds = max(0.0, FinishedTimes.system - StartedTimes.system)
        ChildUserSeconds = max(
            0.0,
            FinishedTimes.children_user - StartedTimes.children_user,
        )
        ChildSystemSeconds = max(
            0.0,
            FinishedTimes.children_system - StartedTimes.children_system,
        )
        ChildCpuSeconds = ChildUserSeconds + ChildSystemSeconds
        CpuSeconds = UserSeconds + SystemSeconds + ChildCpuSeconds
        AverageCores = CpuSeconds / WallSeconds if WallSeconds else 0.0
        return (
            f"  {Label}: wall={WallSeconds:.3f}s "
            f"cpu={CpuSeconds:.3f}s "
            f"(user={UserSeconds:.3f}s system={SystemSeconds:.3f}s "
            f"child_cpu={ChildCpuSeconds:.3f}s) "
            f"average_cores={AverageCores:.2f}"
        )

    def BuildSummary(self) -> dict[str, object]:
        """Stop sampling and return complete interval and stage measurements."""
        with self.PrintLock:
            if not self.Stopped:
                self.Stopped = True
                self.StopEvent.set()
                self.SampleThread.join(timeout=0.25)
        FinishedAt = time.monotonic()
        FinishedTimes = os.times()
        self.LastOsThreads = self.ReadOsThreadCount()
        self.LastPythonThreads = active_count()
        self.PeakOsThreads = max(self.PeakOsThreads, self.LastOsThreads)
        self.PeakPythonThreads = max(
            self.PeakPythonThreads,
            self.LastPythonThreads,
        )
        Intervals: dict[str, object] = {
            "Total": self.MeasureInterval(
                self.StartedAt,
                self.StartTimes,
                FinishedAt,
                FinishedTimes,
            ),
        }
        if self.CompileStartedAt is not None:
            Intervals["Compile"] = self.MeasureInterval(
                self.CompileStartedAt,
                self.CompileStartTimes or self.StartTimes,
                self.CompileFinishedAt or FinishedAt,
                self.CompileFinishTimes or FinishedTimes,
            )
        for Name, (IntervalStartedAt, IntervalStartedTimes) in (
            self.IntervalStarts.items()
        ):
            IntervalFinishedAt, IntervalFinishedTimes = (
                self.IntervalFinishes.get(Name, (FinishedAt, FinishedTimes))
            )
            Intervals[Name] = self.MeasureInterval(
                IntervalStartedAt,
                IntervalStartedTimes,
                IntervalFinishedAt,
                IntervalFinishedTimes,
            )
        StageMeasurements = []
        if self.RoutingStages:
            StageTotals: dict[str, list[float]] = {}
            for Index, (Stage, StartedAt, StartedTimes) in enumerate(
                self.RoutingStages
            ):
                if Index + 1 < len(self.RoutingStages):
                    _NextStage, EndedAt, EndedTimes = (
                        self.RoutingStages[Index + 1]
                    )
                else:
                    EndedAt, EndedTimes = self.IntervalFinishes.get(
                        "Routing",
                        (
                            self.CompileFinishedAt or FinishedAt,
                            self.CompileFinishTimes or FinishedTimes,
                        ),
                    )
                Totals = StageTotals.setdefault(
                    Stage,
                    [0.0, 0.0, 0.0, 0.0, 0.0],
                )
                Totals[0] += max(0.0, EndedAt - StartedAt)
                Totals[1] += max(0.0, EndedTimes.user - StartedTimes.user)
                Totals[2] += max(
                    0.0,
                    EndedTimes.system - StartedTimes.system,
                )
                Totals[3] += max(
                    0.0,
                    (
                        EndedTimes.children_user
                        + EndedTimes.children_system
                        - StartedTimes.children_user
                        - StartedTimes.children_system
                    ),
                )
                Totals[4] += 1
            for Stage, (
                WallSeconds,
                UserSeconds,
                SystemSeconds,
                ChildCpuSeconds,
                Count,
            ) in (
                StageTotals.items()
            ):
                CpuSeconds = UserSeconds + SystemSeconds + ChildCpuSeconds
                AverageCores = CpuSeconds / WallSeconds if WallSeconds else 0.0
                StageMeasurements.append({
                    "Stage": Stage,
                    "Events": int(Count),
                    "WallSeconds": WallSeconds,
                    "CpuSeconds": CpuSeconds,
                    "UserSeconds": UserSeconds,
                    "SystemSeconds": SystemSeconds,
                    "ChildCpuSeconds": ChildCpuSeconds,
                    "AverageCores": AverageCores,
                })
        return {
            "Intervals": Intervals,
            "RoutingStages": StageMeasurements,
            "Threads": {
                "OsCurrent": self.LastOsThreads,
                "OsPeak": self.PeakOsThreads,
                "PythonCurrent": self.LastPythonThreads,
                "PythonPeak": self.PeakPythonThreads,
                "NativeRoutingLimit": os.environ.get(
                    "RC_ROUTING_THREADS", "auto"
                ),
                "LogicalCpus": os.cpu_count() or 1,
            },
        }

    @staticmethod
    def MeasureInterval(
        StartedAt: float,
        StartedTimes: os.times_result,
        FinishedAt: float,
        FinishedTimes: os.times_result,
    ) -> dict[str, float]:
        """Return numeric wall and CPU evidence for one interval."""
        WallSeconds = max(0.0, FinishedAt - StartedAt)
        UserSeconds = max(0.0, FinishedTimes.user - StartedTimes.user)
        SystemSeconds = max(0.0, FinishedTimes.system - StartedTimes.system)
        ChildCpuSeconds = max(
            0.0,
            FinishedTimes.children_user
            + FinishedTimes.children_system
            - StartedTimes.children_user
            - StartedTimes.children_system,
        )
        CpuSeconds = UserSeconds + SystemSeconds + ChildCpuSeconds
        return {
            "WallSeconds": WallSeconds,
            "CpuSeconds": CpuSeconds,
            "UserSeconds": UserSeconds,
            "SystemSeconds": SystemSeconds,
            "ChildCpuSeconds": ChildCpuSeconds,
            "AverageCores": CpuSeconds / WallSeconds if WallSeconds else 0.0,
        }

    def PrintSummary(self) -> None:
        """Compatibility printer; normal CLI finalization writes a report."""
        Summary = self.BuildSummary()
        print("CPU telemetry:")
        for Name, Interval in Summary["Intervals"].items():
            assert isinstance(Interval, dict)
            print(
                f"  {str(Name).lower()}: "
                f"wall={Interval['WallSeconds']:.3f}s "
                f"cpu={Interval['CpuSeconds']:.3f}s"
            )


def ParsePositiveSeconds(Value: str) -> float:
    """Parse one finite, positive CLI duration in seconds."""
    try:
        Seconds = float(Value)
    except ValueError as Error:
        raise argparse.ArgumentTypeError(
            "routing deadline must be a number of seconds"
        ) from Error
    if not isfinite(Seconds) or Seconds <= 0:
        raise argparse.ArgumentTypeError(
            "routing deadline must be finite and positive"
        )
    return Seconds


def ParseTraceSupportBlocks(Value: object | None) -> tuple[str, ...]:
    """Parse a configurable support block palette into block IDs."""
    if Value is None:
        return ()
    if isinstance(Value, str):
        Values = [Item.strip() for Item in Value.split(",")]
    elif isinstance(Value, (tuple, list)):
        Values = [str(Item).strip() for Item in Value]
    else:
        raise ValueError(
            "trace support blocks must be a comma-separated string or list",
        )
    return tuple(Item for Item in Values if Item)


def BuildParser() -> argparse.ArgumentParser:
    Parser = argparse.ArgumentParser(
        description="Compile scalar combinational SystemVerilog to NAND logic and Litematica"
    )
    Parser.add_argument("--input", "--example", "-i", type=Path, help="Input SystemVerilog file")
    Parser.add_argument("--output", "-o", type=Path, help="Output .litematic file")
    Parser.add_argument(
        "--outputname",
        type=str,
        help="Artifact base name; when set, --output is treated as a directory",
    )
    Parser.add_argument("--diagram", type=Path, help="Output NAND diagram JSON")
    Parser.add_argument("--top", "--topmodule", type=str, default=None, help="Top module name")
    Parser.add_argument(
        "--routing-strategy",
        choices=tuple(Value.value for Value in RoutingStrategy),
        default=RoutingStrategy.Default.value,
        help=(
            "Routing strategy selection. Only `default` is available."
        ),
    )
    Parser.add_argument(
        "--workdir",
        type=Path,
        default=Path("Cache/Frontend"),
        help="Working directory for compiler artifacts",
    )
    Parser.add_argument(
        "--routing-threads",
        type=int,
        default=None,
        help="Override Rust routing worker count (RC_ROUTING_THREADS)",
    )
    Parser.add_argument(
        "--routing-deadline-seconds",
        type=ParsePositiveSeconds,
        default=None,
        help=(
            "Override the selected router policy with one absolute routing "
            "deadline, starting when PCB placement begins"
        ),
    )
    Parser.add_argument(
        "--trace-support-blocks",
        type=str,
        help=(
            "Comma-separated block IDs for per-signal route supports; "
            "defaults to light gray, yellow, lime, light blue, red, orange, magenta"
        ),
    )
    Parser.add_argument(
        "--push",
        action="store_true",
        help="Push the compiled litematic to the Minecraft client",
    )
    Parser.add_argument(
        "--push-file",
        type=Path,
        help="Push an existing .litematic to the Minecraft client without compiling",
    )
    Parser.add_argument(
        "--minecraft-directory",
        type=Path,
        help="Minecraft client schematics directory",
    )
    Parser.add_argument(
        "--defaults-file",
        type=Path,
        default=DefaultsPath,
        help="Persistent compiler defaults file",
    )
    return Parser


def LoadDefaults(PathValue: Path) -> dict[str, object]:
    """Load persistent guided defaults, filling missing values safely."""
    Defaults = dict(BuiltInDefaults)
    if not PathValue.is_file():
        return Defaults
    try:
        Loaded = json.loads(PathValue.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as Error:
        raise ValueError(f"Could not read defaults file {PathValue}: {Error}") from Error
    if not isinstance(Loaded, dict):
        raise ValueError(f"Defaults file must contain a JSON object: {PathValue}")
    for Name in BuiltInDefaults:
        if Name in Loaded:
            Defaults[Name] = Loaded[Name]
    if not str(Defaults["InputPath"]).strip():
        Defaults["InputPath"] = BuiltInDefaults["InputPath"]
    if not str(Defaults["PushFilePath"]).strip():
        Defaults["PushFilePath"] = BuiltInDefaults["PushFilePath"]
    return Defaults


def ParsePromptPath(Value: str) -> Path:
    """Accept a pasted shell-quoted path without making quotes part of it."""
    Normalized = Value.strip()
    while (
        len(Normalized) >= 2
        and Normalized[0] == Normalized[-1]
        and Normalized[0] in {"'", '"'}
    ):
        Normalized = Normalized[1:-1].strip()
    return Path(Normalized).expanduser()


def PushToMinecraft(
    LitematicPath: Path,
    MinecraftDirectory: Path = MinecraftSchematicsDirectory,
) -> Path:
    """Copy a generated litematic into the Minecraft client's schematic folder."""
    LitematicPath = LitematicPath.expanduser().resolve()
    MinecraftDirectory = MinecraftDirectory.expanduser().resolve()
    if not LitematicPath.is_file():
        raise FileNotFoundError(f"Litematic does not exist: {LitematicPath}")
    if LitematicPath.suffix.lower() != ".litematic":
        raise ValueError(f"Only .litematic files can be pushed: {LitematicPath}")

    MinecraftDirectory.mkdir(parents=True, exist_ok=True)
    DestinationPath = MinecraftDirectory / LitematicPath.name
    if LitematicPath != DestinationPath:
        shutil.copy2(LitematicPath, DestinationPath)
    return DestinationPath


def _PytestSummary(Output: str, ReturnCode: int) -> str:
    """Extract pytest's compact terminal summary without losing raw output."""
    for Line in reversed(Output.splitlines()):
        Normalized = Line.strip().strip("=").strip()
        if not Normalized:
            continue
        if any(
            Token in Normalized
            for Token in (" passed", " failed", " error", " skipped")
        ):
            return Normalized
    return (
        "Pytest completed successfully."
        if ReturnCode == 0
        else f"Pytest exited with code {ReturnCode}."
    )


def RunPytest() -> int:
    """Run pytest live and persist one complete report under Output/Pytest."""
    RepositoryRoot = Path(__file__).resolve().parent.parent
    Command = [sys.executable, "-m", "pytest", "-q", "Tests"]
    Environment = os.environ.copy()
    Environment["RC_RUN_SCALE_TESTS"] = "0"
    RunDirectory = RepositoryRoot / "Output" / "Pytest" / BuildRunId()
    StartedAtUtc = UtcTimestamp()
    StartedAt = time.monotonic()
    StartedTimes = os.times()
    Process = subprocess.Popen(
        Command,
        cwd=RepositoryRoot,
        env=Environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    StdoutParts: list[str] = []
    StderrParts: list[str] = []

    def Pump(Stream, Destination, Parts: list[str]) -> None:
        if Stream is None:
            return
        for Line in iter(Stream.readline, ""):
            Parts.append(Line)
            Destination.write(Line)
            Destination.flush()
        Stream.close()

    StdoutThread = Thread(
        target=Pump,
        args=(Process.stdout, sys.stdout, StdoutParts),
        daemon=True,
    )
    StderrThread = Thread(
        target=Pump,
        args=(Process.stderr, sys.stderr, StderrParts),
        daemon=True,
    )
    StdoutThread.start()
    StderrThread.start()
    ReturnCode = Process.wait()
    StdoutThread.join()
    StderrThread.join()
    FinishedTimes = os.times()
    WallSeconds = time.monotonic() - StartedAt
    CpuInterval = CpuRunTelemetry.MeasureInterval(
        StartedAt,
        StartedTimes,
        StartedAt + WallSeconds,
        FinishedTimes,
    )
    Stdout = "".join(StdoutParts)
    Stderr = "".join(StderrParts)
    Summary = _PytestSummary(f"{Stdout}\n{Stderr}", ReturnCode)
    try:
        Report = WriteRunReport(
            RunDirectory=RunDirectory,
            Result="SUCCESS" if ReturnCode == 0 else "FAILURE",
            WallSeconds=WallSeconds,
            CpuSeconds=CpuInterval["CpuSeconds"],
            CpuDetails=CpuInterval,
            Summary=Summary,
            RepositoryRoot=RepositoryRoot,
            StartedAtUtc=StartedAtUtc,
            CompletedAtUtc=UtcTimestamp(),
            Command=Command,
            WorkingDirectory=RepositoryRoot,
            Stdout=Stdout,
            Stderr=Stderr,
            FailureType=(
                None if ReturnCode == 0 else f"Pytest: exit-{ReturnCode}"
            ),
            Details={
                "ExitCode": ReturnCode,
                "CpuTelemetry": CpuInterval,
            },
        )
    except OSError as Error:
        print("RESULT: FAILURE — Reporting: write-failed", file=sys.stderr)
        CpuSeconds = float(CpuInterval["CpuSeconds"])
        Utilization = CpuSeconds / WallSeconds * 100.0 if WallSeconds else 0.0
        print(
            f"TIME: total wall={WallSeconds:.3f}s cpu={CpuSeconds:.3f}s "
            f"utilization={Utilization:.1f}% "
            f"average_cores={CpuSeconds / WallSeconds if WallSeconds else 0.0:.2f}",
            file=sys.stderr,
        )
        print(
            f"OUTPUT: Pytest finished, but its report could not be saved: {Error}",
            file=sys.stderr,
        )
        return 1
    print("\n".join(Report.ResultLines))
    return ReturnCode


class TerminalProgressReporter:
    """Render routing status in place and terminate it exactly once."""

    def __init__(self, CpuTelemetry: CpuRunTelemetry | None = None) -> None:
        self.StartTime = time.monotonic()
        self.Interactive = sys.stderr.isatty()
        self.RefreshIntervalSeconds = 0.1
        self.LastPrintedPercent = -10
        self.LastBest: tuple[int | None, int | None] = (None, None)
        self.LastRenderKey: tuple[object, ...] | None = None
        self.LastRenderAt = 0.0
        self.HasRendered = False
        self.LatestProgress: PcbProgress | None = None
        self.RenderLock = Lock()
        self.RefreshStop = Event()
        self.RefreshThread: Thread | None = None
        self.CpuTelemetry = CpuTelemetry
        if self.Interactive:
            self.RefreshThread = Thread(
                target=self._RefreshLoop,
                name="redstone-routing-progress",
                daemon=True,
            )
            self.RefreshThread.start()

    def __call__(self, Progress: PcbProgress) -> None:
        if self.CpuTelemetry is not None:
            self.CpuTelemetry.RecordRoutingProgress(Progress)
        with self.RenderLock:
            self.LatestProgress = Progress
            self._Render(Progress)

    def _RefreshLoop(self) -> None:
        """Refresh elapsed time while routing is between progress callbacks."""
        while not self.RefreshStop.wait(self.RefreshIntervalSeconds):
            with self.RenderLock:
                if self.LatestProgress is not None:
                    self._Render(self.LatestProgress)

    def _Render(self, Progress: PcbProgress) -> None:
        """Render one captured progress state; caller holds RenderLock."""
        Percent = min(
            100,
            int(Progress.Completed * 100 / max(1, Progress.Total)),
        )
        Filled = int(Percent * 30 / 100)
        Bar = "#" * Filled + "-" * (30 - Filled)
        Elapsed = time.monotonic() - self.StartTime
        if Progress.BestBlocks is None:
            BestText = "best searching"
        else:
            BestText = (
                f"best {Progress.BestBlocks}b "
                f"{Progress.BestWidth}x{Progress.BestDepth} "
                f"footprint {Progress.BestFootprint}"
            )
        Prefix = (
            f"[{Bar}] {Percent:3d}% "
            f"{Progress.Completed}/{Progress.Total} {Progress.Unit} "
            f"t{Progress.Workers} ok{Progress.Valid} fail{Progress.Failed} | "
        )
        Suffix = f" | {BestText} | {Elapsed:.1f}s"
        Line = f"{Prefix}{Progress.Stage}{Suffix}"
        BestKey = (Progress.BestBlocks, Progress.BestFootprint)
        if self.Interactive:
            RenderKey = (
                Progress.Completed,
                Progress.Total,
                Progress.Workers,
                Progress.Valid,
                Progress.Failed,
                Progress.Stage,
                BestKey,
            )
            CurrentTime = time.monotonic()
            if (
                RenderKey == self.LastRenderKey
                and CurrentTime - self.LastRenderAt < self.RefreshIntervalSeconds
            ):
                return
            TerminalWidth = max(
                20,
                min(shutil.get_terminal_size(fallback=(120, 24)).columns - 1, 160),
            )
            if len(Line) > TerminalWidth:
                FixedLength = len(Prefix) + len(Suffix) + 3
                StageLength = max(0, TerminalWidth - FixedLength)
                if StageLength > 0:
                    Line = (
                        f"{Prefix}{Progress.Stage[:StageLength]}...{Suffix}"
                    )
                else:
                    Line = f"{Line[:TerminalWidth - 3]}..."
            print(
                f"\r\x1b[2K{Line}",
                end="",
                file=sys.stderr,
                flush=True,
            )
            self.LastRenderKey = RenderKey
            self.LastRenderAt = CurrentTime
            self.HasRendered = True
            return
        if (
            Percent >= self.LastPrintedPercent + 10
            or BestKey != self.LastBest
        ):
            print(Line, file=sys.stderr, flush=True)
            self.LastPrintedPercent = Percent
            self.LastBest = BestKey

    def Finish(self) -> None:
        """Move output following an interactive progress line to a new line."""
        self.RefreshStop.set()
        if self.RefreshThread is not None:
            self.RefreshThread.join(timeout=self.RefreshIntervalSeconds * 2)
            self.RefreshThread = None
        with self.RenderLock:
            if self.Interactive and self.HasRendered:
                print(file=sys.stderr, flush=True)
                self.HasRendered = False


class TerminalValidationProgressReporter:
    """Render validation independently after the routing bar has closed."""

    def __init__(self) -> None:
        self.Interactive = sys.stderr.isatty()
        self.RefreshIntervalSeconds = 0.1
        self.StartTime: float | None = None
        self.LatestProgress: PhysicalValidationProgress | None = None
        self.LastRenderKey: tuple[object, ...] | None = None
        self.LastRenderAt = 0.0
        self.HasRendered = False
        self.RenderLock = Lock()
        self.RefreshStop = Event()
        self.RefreshThread: Thread | None = None

    def __call__(self, Progress: PhysicalValidationProgress) -> None:
        with self.RenderLock:
            if self.StartTime is None:
                self.StartTime = time.monotonic()
                if self.Interactive:
                    self.RefreshThread = Thread(
                        target=self._RefreshLoop,
                        name="redstone-validation-progress",
                        daemon=True,
                    )
                    self.RefreshThread.start()
            self.LatestProgress = Progress
            self._Render(Progress)

    def _RefreshLoop(self) -> None:
        while not self.RefreshStop.wait(self.RefreshIntervalSeconds):
            with self.RenderLock:
                if self.LatestProgress is not None:
                    self._Render(self.LatestProgress)

    def _Render(self, Progress: PhysicalValidationProgress) -> None:
        StartedAt = self.StartTime or time.monotonic()
        Elapsed = max(0.0, time.monotonic() - StartedAt)
        BarWidth = 30
        SafeTotal = max(1, Progress.Total)
        SafeCompleted = max(0, min(Progress.Total, Progress.Completed))
        Percent = min(100, int(SafeCompleted * 100 / SafeTotal))
        Filled = int(Percent * BarWidth / 100)
        Bar = "#" * Filled + "-" * (BarWidth - Filled)
        PercentText = f"{Percent:3d}%"
        CompletedText = str(SafeCompleted)
        TotalText = str(Progress.Total) if Progress.Total > 0 else "?"
        StatusText = (
            f" | {Progress.Status.upper()}"
            if Progress.Status is not None
            else ""
        )
        Prefix = (
            "FABRIC CHECK"
            if str(Progress.Backend or "").startswith("fabric")
            else "VALIDATION"
        )
        Line = (
            f"{Prefix} [{Bar}] {PercentText} "
            f"{CompletedText}/{TotalText} vectors | {Progress.Stage}"
            f"{StatusText} | {Elapsed:.1f}s"
        )
        RenderKey = (
            Progress.Completed,
            Progress.Total,
            Progress.Stage,
            Progress.Status,
        )
        if self.Interactive:
            CurrentTime = time.monotonic()
            if (
                RenderKey == self.LastRenderKey
                and CurrentTime - self.LastRenderAt < self.RefreshIntervalSeconds
            ):
                return
            TerminalWidth = max(
                20,
                min(shutil.get_terminal_size(fallback=(120, 24)).columns - 1, 160),
            )
            if len(Line) > TerminalWidth:
                Line = f"{Line[:TerminalWidth - 3]}..."
            print(
                f"\r\x1b[2K{Line}",
                end="",
                file=sys.stderr,
                flush=True,
            )
            self.HasRendered = True
            self.LastRenderAt = CurrentTime
        elif RenderKey != self.LastRenderKey:
            print(Line, file=sys.stderr, flush=True)
        self.LastRenderKey = RenderKey

    def Finish(self) -> None:
        self.RefreshStop.set()
        if self.RefreshThread is not None:
            self.RefreshThread.join(timeout=self.RefreshIntervalSeconds * 2)
            self.RefreshThread = None
        with self.RenderLock:
            if self.Interactive and self.HasRendered:
                print(file=sys.stderr, flush=True)
                self.HasRendered = False


def BuildProgressReporter(
    CpuTelemetry: CpuRunTelemetry | None = None,
) -> TerminalProgressReporter:
    """Build a terminal-aware PCB routing progress callback."""
    return TerminalProgressReporter(CpuTelemetry)


def PrintRoutingFailureSummary(Error: Exception, OutputPath: Path | None) -> None:
    """Print compact, actionable physical-routing evidence for CLI failures."""
    Failure = getattr(Error, "Failure", None)
    if Failure is None:
        return
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(getattr(Failure, "Diagnostics", None), dict)
        else {}
    )
    print("Routing failure details:", file=sys.stderr)
    if Failure.AffectedNets:
        print(
            "  affected nets: " + ", ".join(Failure.AffectedNets),
            file=sys.stderr,
        )
    if Failure.Resources:
        print(
            "  affected resources: " + ", ".join(Failure.Resources),
            file=sys.stderr,
        )
    Deadline = Diagnostics.get("Deadline", {})
    if isinstance(Deadline, dict):
        Elapsed = Deadline.get("ElapsedSeconds")
        Remaining = Deadline.get("RemainingMilliseconds")
        if Elapsed is not None or Remaining is not None:
            Expired = Deadline.get("Expired")
            ExpiredText = (
                f"expired={str(Expired).lower()} "
                if isinstance(Expired, bool)
                else ""
            )
            print(
                "  deadline: "
                f"elapsed={Elapsed}s remaining={Remaining}ms "
                f"{ExpiredText}"
                f"kind={Deadline.get('ExpirationKind', '')}",
                file=sys.stderr,
            )
    Core = Diagnostics.get("ComponentRoutabilityCore", {})
    if isinstance(Core, dict) and Core.get("Complete", False):
        print(
            "  routability core: "
            f"{Core.get('CoreFingerprint', '')} "
            f"signals={','.join(map(str, Core.get('Signals', ())))}",
            file=sys.stderr,
        )
    Attempts = Diagnostics.get("CompletedComponentStateAttempts", ())
    if isinstance(Attempts, list) and Attempts:
        print(
            f"  component placement attempts ({len(Attempts)}):",
            file=sys.stderr,
        )
        for Attempt in Attempts[:24]:
            if not isinstance(Attempt, dict):
                continue
            AttemptFailure = Attempt.get("Failure", {})
            if not isinstance(AttemptFailure, dict):
                AttemptFailure = {}
            AttemptCore = AttemptFailure.get(
                "OwnershipUnsatCoreFingerprint", ""
            )
            Nets = ",".join(map(
                str,
                AttemptFailure.get("AffectedNets", ()),
            ))
            print(
                "    "
                f"{Attempt.get('CandidateId', '<unknown>')} "
                f"result={Attempt.get('Result', '')} "
                f"placement={Attempt.get('PlacementFingerprint', '')} "
                f"failure={AttemptFailure.get('Stage', '')}:"
                f"{AttemptFailure.get('Reason', '')} "
                f"nets={Nets} core={AttemptCore}",
                file=sys.stderr,
            )
        if len(Attempts) > 24:
            print("    ... remaining attempts are in the artifact", file=sys.stderr)
    Decisions = Diagnostics.get("PlacementGenerationDecisions", ())
    if isinstance(Decisions, list) and Decisions:
        print("  latest placement decisions:", file=sys.stderr)
        for Decision in Decisions[-6:]:
            if isinstance(Decision, dict):
                print(
                    "    "
                    f"{Decision.get('Result', '')} "
                    f"signals={','.join(map(str, Decision.get('RelocationSignals', ())))} "
                    f"placement={Decision.get('PlacementFingerprint', '')}",
                    file=sys.stderr,
                )
    if OutputPath is not None:
        print(
            "  full diagnostic artifact: "
            f"{OutputPath.with_suffix('.RoutingFailure.json')}",
            file=sys.stderr,
        )


def _FormatFabricDiagnosticValue(Value: object) -> str:
    """Format one Fabric diagnostic value compactly and deterministically."""
    if isinstance(Value, bool):
        return str(Value).lower()
    if Value is None:
        return "unknown"
    return str(Value)


def _FormatFabricBlockState(State: object) -> str:
    """Format a serialized Minecraft block state using command syntax."""
    if not isinstance(State, dict):
        return "unknown"
    Name = str(State.get("Name", "unknown"))
    Properties = State.get("Properties")
    if not isinstance(Properties, dict) or not Properties:
        return Name
    PropertyText = ",".join(
        f"{Key}={_FormatFabricDiagnosticValue(Value)}"
        for Key, Value in sorted(Properties.items())
    )
    return f"{Name}[{PropertyText}]"


def _FormatFabricCoordinates(Value: object) -> str:
    """Format one fixture or world XYZ tuple without accepting partial data."""
    if (
        not isinstance(Value, (list, tuple))
        or len(Value) != 3
        or not all(type(Axis) is int for Axis in Value)
    ):
        return "unknown"
    return f"({Value[0]}, {Value[1]}, {Value[2]})"


def _FabricFailureTraceFromDiagnostics(
    Diagnostics: object,
) -> dict[str, object] | None:
    """Extract a Fabric failure trace from routing-failure diagnostics."""
    if not isinstance(Diagnostics, dict):
        return None
    FabricValidation = Diagnostics.get("FabricFinalCheck")
    if not isinstance(FabricValidation, dict):
        return None
    FabricDiagnostics = FabricValidation.get("Diagnostics")
    if not isinstance(FabricDiagnostics, dict):
        return None
    FailureTrace = FabricDiagnostics.get("FailureTrace")
    return FailureTrace if isinstance(FailureTrace, dict) else None


def _LoadFabricFailureTrace(
    Error: Exception,
    OutputPath: Path | None,
) -> tuple[dict[str, object] | None, Path | None]:
    """Load retained Fabric trace evidence from the exception or run artifact."""
    Failure = getattr(Error, "Failure", None)
    FailureTrace = _FabricFailureTraceFromDiagnostics(
        getattr(Failure, "Diagnostics", None),
    )
    if FailureTrace is not None:
        return FailureTrace, None
    if OutputPath is None:
        return None, None
    ArtifactPath = OutputPath.with_suffix(".RoutingFailure.json")
    try:
        Artifact = json.loads(ArtifactPath.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None, ArtifactPath
    if not isinstance(Artifact, dict):
        return None, ArtifactPath
    ArtifactFailure = Artifact.get("Failure")
    if not isinstance(ArtifactFailure, dict):
        return None, ArtifactPath
    if ArtifactFailure.get("Stage") != "FabricFinalCheck":
        return None, ArtifactPath
    return (
        _FabricFailureTraceFromDiagnostics(ArtifactFailure.get("Diagnostics")),
        ArtifactPath,
    )


def _FailedOutputProbe(
    FailureTrace: dict[str, object],
) -> dict[str, object] | None:
    """Return the observed probe for the output named by a Fabric failure."""
    FailedOutput = FailureTrace.get("FailedOutput")
    Entries = FailureTrace.get("SubcircuitTrace")
    if not isinstance(Entries, list):
        return None
    for Entry in Entries:
        if not isinstance(Entry, dict):
            continue
        Output = Entry.get("Output")
        if not isinstance(Output, dict) or Output.get("Signal") != FailedOutput:
            continue
        Blocks = Output.get("Blocks")
        if isinstance(Blocks, list):
            for Block in Blocks:
                if isinstance(Block, dict):
                    return Block
    return None


def PrintFabricFailureSummary(
    Error: Exception,
    OutputPath: Path | None,
) -> None:
    """Print the exact retained Fabric block state and coordinates."""
    IsFabricFailure = str(Error).startswith("FabricFinalCheck:")
    Failure = getattr(Error, "Failure", None)
    IsFabricFailure = IsFabricFailure or (
        Failure is not None
        and str(getattr(Failure, "Stage", "")) == "FabricFinalCheck"
    )
    if not IsFabricFailure:
        return
    FailureTrace, ArtifactPath = _LoadFabricFailureTrace(Error, OutputPath)
    print("Fabric failure details:", file=sys.stderr)
    if FailureTrace is None:
        print(
            "  exact block and coordinates: unavailable "
            "(failure trace was not retained)",
            file=sys.stderr,
        )
        if ArtifactPath is not None:
            print(f"  diagnostic artifact: {ArtifactPath}", file=sys.stderr)
        return

    FailureKind = _FormatFabricDiagnosticValue(
        FailureTrace.get("FailureKind"),
    )
    FailedOutput = _FormatFabricDiagnosticValue(
        FailureTrace.get("FailedOutput"),
    )
    Expected = _FormatFabricDiagnosticValue(FailureTrace.get("Expected"))
    Actual = _FormatFabricDiagnosticValue(FailureTrace.get("Actual"))
    print(f"  kind: {FailureKind}", file=sys.stderr)
    print(
        f"  output: {FailedOutput} expected={Expected} actual={Actual}",
        file=sys.stderr,
    )
    GlobalVectorIndex = FailureTrace.get("GlobalVectorIndex")
    if GlobalVectorIndex is not None:
        print(
            "  validation: "
            f"vector={_FormatFabricDiagnosticValue(GlobalVectorIndex)}",
            file=sys.stderr,
        )

    Block = FailureTrace.get("FirstFailingBlock")
    EvidenceKind = "first mismatching block"
    if not isinstance(Block, dict):
        Block = _FailedOutputProbe(FailureTrace)
        EvidenceKind = (
            "failed-output probe; no mismatching block was identified "
            "before timeout"
        )
    if not isinstance(Block, dict):
        print(
            "  exact block and coordinates: unavailable "
            "(trace contained no observed output probe)",
            file=sys.stderr,
        )
    else:
        print(
            f"  block: {_FormatFabricBlockState(Block.get('State'))}",
            file=sys.stderr,
        )
        print(
            "  coords: "
            f"fixture={_FormatFabricCoordinates(Block.get('FixturePosition'))} "
            f"world={_FormatFabricCoordinates(Block.get('WorldPosition'))}",
            file=sys.stderr,
        )
        print(f"  evidence: {EvidenceKind}", file=sys.stderr)
    if ArtifactPath is not None:
        print(f"  diagnostic artifact: {ArtifactPath}", file=sys.stderr)


def _FailureType(Error: BaseException) -> str:
    """Return the shortest stable typed failure available."""
    Failure = getattr(Error, "Failure", None)
    if Failure is not None:
        Stage = str(getattr(Failure, "Stage", Error.__class__.__name__))
        ReasonValue = getattr(Failure, "Reason", "failure")
        Reason = str(getattr(ReasonValue, "value", ReasonValue))
        return f"{Stage}: {Reason}"
    MessageParts = str(Error).split(":", 2)
    if (
        len(MessageParts) >= 2
        and all(MessageParts[:2])
        and " " not in MessageParts[0]
        and "/" not in MessageParts[1]
        and len(MessageParts[0]) <= 64
        and len(MessageParts[1]) <= 64
    ):
        return f"{MessageParts[0]}: {MessageParts[1]}"
    return Error.__class__.__name__


def _FailureSummary(Error: BaseException) -> str:
    Message = " ".join(str(Error).split())
    if Message:
        return Message
    return f"The operation raised {Error.__class__.__name__}."


def PrintOperationFailure(
    Error: BaseException,
    FailureDiagnostics: str = "",
) -> None:
    """Restore the complete pre-reporting CLI failure message and evidence."""
    print(f"Operation failed: {Error}", file=sys.stderr)
    if FailureDiagnostics:
        print(FailureDiagnostics, file=sys.stderr, end=(
            "" if FailureDiagnostics.endswith("\n") else "\n"
        ))


def _AtomicCopy(SourcePath: Path, DestinationPath: Path) -> None:
    DestinationPath.parent.mkdir(parents=True, exist_ok=True)
    TemporaryPath = DestinationPath.with_name(
        f".{DestinationPath.name}.promote-P{os.getpid()}"
    )
    shutil.copy2(SourcePath, TemporaryPath)
    TemporaryPath.replace(DestinationPath)


def _CompileResultDetails(Result, CpuTelemetry: dict[str, object]) -> dict[str, object]:
    """Serialize the detailed success evidence formerly printed to terminal."""
    Composition = Result.BlockComposition
    RoutingMetrics = Result.RoutingMetrics
    return {
        "LogicOptimization": {
            "OriginalIrGates": Result.OriginalLogicGateCount,
            "OptimizedIrGates": Result.OptimizedLogicGateCount,
            "NandGates": Result.NandGateCount,
        },
        "RoutingMetrics": (
            {
                "Stage": RoutingMetrics.Stage,
                "NetCount": RoutingMetrics.NetCount,
                "TotalLength": RoutingMetrics.TotalLength,
                "BendCount": RoutingMetrics.BendCount,
                "ViaCount": RoutingMetrics.ViaCount,
                "ReroutedNets": RoutingMetrics.ReroutedNets,
                "ConflictCount": RoutingMetrics.ConflictCount,
                "CorridorOverflowPeak": RoutingMetrics.CorridorOverflowPeak,
            }
            if RoutingMetrics is not None
            else None
        ),
        "Layout": {
            "EstimatedBlocks": Result.EstimatedBlocks,
            "Width": Result.Width,
            "Depth": Result.Depth,
            "Footprint": Composition.Footprint,
            "XyFootprint": Composition.XYFootprint,
            "FullFootprint": Composition.FullFootprint,
        },
        "BlockComposition": {
            "ComponentOwnedFunctionalBlocks": (
                Composition.ComponentOwnedFunctionalBlocks
            ),
            "ComponentFunctionalShare": Composition.ComponentFunctionalShare,
            "RoutingOwnedFunctionalBlocks": (
                Composition.RoutingOwnedFunctionalBlocks
            ),
            "RoutingFunctionalShare": Composition.RoutingFunctionalShare,
            "RawDustBlocks": Composition.RawDustBlocks,
            "RawDustFunctionalShare": Composition.RawDustFunctionalShare,
            "SupportBlocks": Composition.SupportBlocks,
            "AnnotationBlocks": Composition.AnnotationBlocks,
        },
        "RoutingStrategy": {
            "Requested": Result.RequestedStrategy,
            "Used": Result.UsedStrategy,
            "FallbackUsed": Result.FallbackUsed,
            "FallbackReason": Result.FallbackReason,
        },
        "PipelineRuntimeSeconds": Result.RuntimeSeconds,
        "MaximumNetLengthShare": Result.MaximumNetLengthShare,
        "MchprsValidation": vars(Result.MchprsValidation),
        "FabricFinalCheck": vars(Result.FabricFinalCheck),
        "Artifacts": {
            "Litematic": str(Result.OutputPath),
            "NandJson": str(Result.DiagramPath),
            "PhysicalDesign": str(Result.PhysicalDesignPath),
        },
        "CpuTelemetry": CpuTelemetry,
    }


def _CompileOutputSummary(Result, StableOutputPath: Path) -> str:
    """Build a compact but useful successful compiler output line."""
    Metrics = Result.RoutingMetrics
    ConflictText = (
        f" conflicts={Metrics.ConflictCount}"
        if Metrics is not None
        else ""
    )
    return (
        f"{StableOutputPath.stem} | "
        f"mchprs={Result.MchprsValidation.Status.upper()} "
        f"fabric={Result.FabricFinalCheck.Status.upper()} | "
        f"nand={Result.NandGateCount} blocks={Result.EstimatedBlocks} "
        f"size={Result.Width}x{Result.Depth}{ConflictText} | "
        f"litematic={StableOutputPath.resolve(strict=False)}"
    )


def Main(Args: list[str] | None = None) -> int:
    RawArgs = list(sys.argv[1:] if Args is None else Args)
    Parser = BuildParser()
    Parsed = Parser.parse_args(RawArgs)
    OutputPath: Path | None = None

    try:
        Defaults = LoadDefaults(Parsed.defaults_file)
        MinecraftDirectory = (
            Parsed.minecraft_directory
            if Parsed.minecraft_directory is not None
            else Path(str(Defaults["MinecraftDirectory"]))
        )
        if Parsed.push_file is not None:
            DestinationPath = PushToMinecraft(
                Parsed.push_file,
                MinecraftDirectory,
            )
            print(f"Pushed to Minecraft: {DestinationPath}")
            return 0

        if Parsed.input is None:
            Parser.error("--input is required")
        InputPath = Parsed.input
        if Parsed.outputname:
            OutputDirectory = Parsed.output or Path("Output") / Parsed.outputname
            OutputPath = OutputDirectory / f"{Parsed.outputname}.litematic"
        else:
            OutputPath = Parsed.output or (
                Path("Output")
                / InputPath.stem
                / f"{InputPath.stem}.litematic"
            )
        DiagramPath = Parsed.diagram or OutputPath.with_suffix(".Nand.json")
        TopModule = Parsed.top
        Workdir = Parsed.workdir
        PushResult = Parsed.push
        TraceSupportBlocks = ParseTraceSupportBlocks(
            Parsed.trace_support_blocks
            if Parsed.trace_support_blocks is not None
            else Defaults.get("TraceSupportBlocks"),
        )
        if OutputPath.suffix.lower() != ".litematic":
            OutputPath = OutputPath.with_suffix(".litematic")
        if Parsed.routing_threads is not None:
            if Parsed.routing_threads <= 0:
                Parser.error("--routing-threads must be positive")
            os.environ["RC_ROUTING_THREADS"] = str(Parsed.routing_threads)
    except (FileNotFoundError, ValueError, NotImplementedError) as Error:
        print(f"Operation failed: {Error}", file=sys.stderr)
        return 1

    RepositoryRoot = Path(__file__).resolve().parent.parent
    StableOutputPath = OutputPath
    StableDiagramPath = DiagramPath
    RunDirectory = StableOutputPath.parent / "Runs" / BuildRunId()
    RunOutputPath = RunDirectory / StableOutputPath.name
    RunDiagramPath = RunDirectory / StableOutputPath.with_suffix(
        ".Nand.json"
    ).name
    OutputPath = RunOutputPath
    StartedAtUtc = UtcTimestamp()
    RunStartedAt = time.monotonic()
    CpuTelemetry = CpuRunTelemetry()
    ProgressReporter = BuildProgressReporter(CpuTelemetry)
    ValidationProgressReporter = TerminalValidationProgressReporter()
    Capture = CaptureTerminalOutput()
    Error: BaseException | None = None
    ExceptionText = ""
    Result = None

    def RecordPipelineTimingEvent(Name: str, Event: str) -> None:
        CpuTelemetry.RecordPipelineTimingEvent(Name, Event)
        if Name == "Routing" and Event == "finish":
            ProgressReporter.Finish()
        if Name == "Validation" and Event == "finish":
            ValidationProgressReporter.Finish()

    try:
        with Capture:
            print(
                "Generating clustered PCB-style multilayer routing...",
                file=sys.stderr,
            )
            try:
                CpuTelemetry.BeginCompilation()
                Result = CompileSvToLitematic(
                    InputPath=InputPath,
                    OutputPath=RunOutputPath,
                    DiagramPath=RunDiagramPath,
                    TopModule=TopModule,
                    Workdir=Workdir,
                    ProgressCallback=ProgressReporter,
                    RoutingStrategyValue=Parsed.routing_strategy,
                    RoutingDeadlineSeconds=Parsed.routing_deadline_seconds,
                    TraceSupportBlocks=TraceSupportBlocks,
                    TimingCallback=RecordPipelineTimingEvent,
                    ValidationProgressCallback=ValidationProgressReporter,
                )
            finally:
                CpuTelemetry.FinishCompilation()
                ProgressReporter.Finish()
                ValidationProgressReporter.Finish()
    except KeyboardInterrupt as Caught:
        Error = Caught
        ExceptionText = traceback.format_exc()
    except Exception as Caught:
        Error = Caught
        ExceptionText = traceback.format_exc()

    PromotedPaths: list[Path] = []
    DestinationPath: Path | None = None
    if Error is None and Result is not None:
        try:
            PromotedPaths = PromoteRunArtifacts(
                RunDirectory=RunDirectory,
                RunBaseName=StableOutputPath.stem,
                StableOutputPath=StableOutputPath,
            )
            if Result.DiagramPath.is_file():
                _AtomicCopy(Result.DiagramPath, StableDiagramPath)
                if StableDiagramPath not in PromotedPaths:
                    PromotedPaths.append(StableDiagramPath)
            # Prove report persistence before an optional external push.  The
            # final report below replaces this provisional evidence after the
            # push result and complete timing are known.
            WriteRunReport(
                RunDirectory=RunDirectory,
                Result="SUCCESS",
                WallSeconds=time.monotonic() - RunStartedAt,
                CpuSeconds=None,
                Summary=(
                    f"{StableOutputPath.stem} compiled and passed Fabric "
                    "validation; finalization is pending."
                ),
                RepositoryRoot=RepositoryRoot,
                StartedAtUtc=StartedAtUtc,
                CompletedAtUtc=UtcTimestamp(),
                Command=[sys.executable, str(RepositoryRoot / "Main.py"), *RawArgs],
                WorkingDirectory=RepositoryRoot,
                Stdout=Capture.StdoutText,
                Stderr=Capture.StderrText,
                Details={"ReportPersistenceProbe": True},
                ArtifactRoots=PromotedPaths,
            )
            if PushResult:
                DestinationPath = PushToMinecraft(
                    StableOutputPath,
                    MinecraftDirectory,
                )
        except Exception as Caught:
            Error = Caught
            ExceptionText = traceback.format_exc()

    Telemetry = CpuTelemetry.BuildSummary()
    TotalInterval = Telemetry["Intervals"]["Total"]
    assert isinstance(TotalInterval, dict)
    WallSeconds = time.monotonic() - RunStartedAt
    CpuSeconds = float(TotalInterval["CpuSeconds"])

    FailureDiagnostics = ""
    if Error is not None and isinstance(Error, Exception):
        DiagnosticBuffer = StringIO()
        with redirect_stderr(DiagnosticBuffer):
            PrintRoutingFailureSummary(Error, RunOutputPath)
            PrintFabricFailureSummary(Error, RunOutputPath)
        FailureDiagnostics = DiagnosticBuffer.getvalue()

    if Error is None and Result is not None:
        ReportResult = "SUCCESS"
        FailureType = None
        Summary = (
            _CompileOutputSummary(Result, StableOutputPath)
        )
        Details = _CompileResultDetails(Result, Telemetry)
        Details["ResolvedInputs"] = {
            "SystemVerilog": str(InputPath),
            "TopModule": TopModule,
            "WorkDirectory": str(Workdir),
        }
        Details["StableArtifacts"] = [str(PathValue) for PathValue in PromotedPaths]
        Details["MinecraftDestination"] = (
            str(DestinationPath) if DestinationPath is not None else None
        )
        ExitCode = 0
    else:
        assert Error is not None
        ReportResult = "CANCELLED" if isinstance(Error, KeyboardInterrupt) else "FAILURE"
        FailureType = (
            "KeyboardInterrupt" if isinstance(Error, KeyboardInterrupt)
            else _FailureType(Error)
        )
        Summary = (
            "Compilation was cancelled by the user."
            if isinstance(Error, KeyboardInterrupt)
            else _FailureSummary(Error)
        )
        Details = {
            "ResolvedInputs": {
                "SystemVerilog": str(InputPath),
                "TopModule": TopModule,
                "WorkDirectory": str(Workdir),
            },
            "StableOutputPath": str(StableOutputPath),
            "RunOutputPath": str(RunOutputPath),
            "PromotedBeforeFailure": [str(PathValue) for PathValue in PromotedPaths],
            "CpuTelemetry": Telemetry,
            "RoutingFailureSummary": FailureDiagnostics,
        }
        ExitCode = 130 if isinstance(Error, KeyboardInterrupt) else 1

    try:
        Report = WriteRunReport(
            RunDirectory=RunDirectory,
            Result=ReportResult,
            WallSeconds=WallSeconds,
            CpuSeconds=CpuSeconds,
            Summary=Summary,
            RepositoryRoot=RepositoryRoot,
            StartedAtUtc=StartedAtUtc,
            CompletedAtUtc=UtcTimestamp(),
            Command=[sys.executable, str(RepositoryRoot / "Main.py"), *RawArgs],
            WorkingDirectory=RepositoryRoot,
            Stdout=Capture.StdoutText,
            Stderr=Capture.StderrText + FailureDiagnostics,
            FailureType=FailureType,
            CpuDetails={
                **TotalInterval,
                **(
                    Telemetry["Threads"]
                    if isinstance(Telemetry.get("Threads"), dict)
                    else {}
                ),
            },
            TimingDetails=Telemetry,
            ExceptionText=ExceptionText,
            Details=Details,
            ArtifactRoots=PromotedPaths,
        )
    except OSError as ReportError:
        FallbackLines = FormatResultLines(
            Result="FAILURE",
            FailureType="Reporting: write-failed",
            WallSeconds=WallSeconds,
            CpuSeconds=CpuSeconds,
            Summary=(
                "The operation finished, but its required report could not "
                f"be saved: {ReportError}"
            ),
            RawReportPath=(
                RunDirectory.resolve(strict=False) / "RawDump.txt"
            ),
            CpuDetails={
                **TotalInterval,
                **(
                    Telemetry["Threads"]
                    if isinstance(Telemetry.get("Threads"), dict)
                    else {}
                ),
            },
            TimingDetails=Telemetry,
        )
        print("\n".join(FallbackLines), file=sys.stderr)
        if Error is not None:
            PrintOperationFailure(Error, FailureDiagnostics)
        return 1
    print("\n".join(Report.ResultLines))
    if Error is not None:
        PrintOperationFailure(Error, FailureDiagnostics)
    return ExitCode


if __name__ == "__main__":
    raise SystemExit(Main())

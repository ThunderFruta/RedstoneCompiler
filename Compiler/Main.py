"""Guided and argument-driven RedstoneCompiler entrypoint."""

from __future__ import annotations

import argparse
import atexit
import json
from math import isfinite
from pathlib import Path
import shutil
import sys
import os
import time
from threading import Event, Lock, Thread, active_count

if __package__:
    from .Pipeline import CompileSvToLitematic
    from .Placement.PcbFlow import PcbProgress
    from .Routing.Policy import RoutingStrategy
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from Compiler.Pipeline import CompileSvToLitematic
    from Compiler.Placement.PcbFlow import PcbProgress
    from Compiler.Routing.Policy import RoutingStrategy


MinecraftSchematicsDirectory = Path(
    "/home/bananawewe/Documents/curseforge/minecraft/Instances/wee/schematics"
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

    def RecordRoutingProgress(self, Progress: PcbProgress) -> None:
        """Record only stage transitions; rendering remains independent."""
        Stage = str(Progress.Stage)
        # Progress text may append per-net/iteration diagnostics after a
        # separator.  Keep the stable stage name so the final CPU report is
        # useful rather than hundreds of near-duplicate lines.
        if " | " in Stage:
            Stage = Stage.split(" | ", 1)[-1]
        if self.RoutingStages and self.RoutingStages[-1][0] == Stage:
            return
        self.RoutingStages.append((Stage, time.monotonic(), os.times()))

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

    def PrintSummary(self) -> None:
        """Print exactly once, including after an unexpected traceback."""
        with self.PrintLock:
            if self.HasPrinted:
                return
            self.HasPrinted = True
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
        print("CPU telemetry:")
        print(self.FormatInterval(
            "total",
            self.StartedAt,
            self.StartTimes,
            FinishedAt,
            FinishedTimes,
        ))
        if self.CompileStartedAt is not None:
            print(self.FormatInterval(
                "compile",
                self.CompileStartedAt,
                self.CompileStartTimes or self.StartTimes,
                self.CompileFinishedAt or FinishedAt,
                self.CompileFinishTimes or FinishedTimes,
            ))
        if self.RoutingStages:
            print("  routing stages:")
            StageTotals: dict[str, list[float]] = {}
            for Index, (Stage, StartedAt, StartedTimes) in enumerate(
                self.RoutingStages
            ):
                if Index + 1 < len(self.RoutingStages):
                    _NextStage, EndedAt, EndedTimes = (
                        self.RoutingStages[Index + 1]
                    )
                else:
                    EndedAt = self.CompileFinishedAt or FinishedAt
                    EndedTimes = self.CompileFinishTimes or FinishedTimes
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
                print(
                    f"    {Stage} (events={int(Count)}): "
                    f"wall={WallSeconds:.3f}s cpu={CpuSeconds:.3f}s "
                    f"(user={UserSeconds:.3f}s system={SystemSeconds:.3f}s "
                    f"child_cpu={ChildCpuSeconds:.3f}s) "
                    f"average_cores={AverageCores:.2f}"
                )
        print(
            "  threads: "
            f"os_current={self.LastOsThreads} "
            f"os_peak={self.PeakOsThreads} "
            f"python_current={self.LastPythonThreads} "
            f"python_peak={self.PeakPythonThreads} "
            f"native_routing_limit={os.environ.get('RC_ROUTING_THREADS', 'auto')} "
            f"logical_cpus={os.cpu_count() or 1}"
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
        "--guided",
        action="store_true",
        help="Open the guided compile menu",
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
        help="Persistent guided-menu defaults file",
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


def SaveDefaults(
    PathValue: Path,
    Defaults: dict[str, object],
) -> None:
    """Persist guided defaults in a stable, human-readable format."""
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    PathValue.write_text(
        json.dumps(Defaults, indent=2) + "\n",
        encoding="utf-8",
    )


def PromptText(Label: str, Default: str = "") -> str:
    DefaultText = f" [{Default}]" if Default else ""
    Value = input(f"{Label}{DefaultText}: ").strip()
    return Value if Value else Default


def PromptBoolean(Label: str, Default: bool) -> bool:
    DefaultText = "Y/n" if Default else "y/N"
    while True:
        Value = input(f"{Label} [{DefaultText}]: ").strip().lower()
        if not Value:
            return Default
        if Value in {"y", "yes"}:
            return True
        if Value in {"n", "no"}:
            return False
        print("Enter y or n.")


def ShowDefaults(
    Defaults: dict[str, object],
    PathValue: Path,
) -> None:
    print(f"Defaults file: {PathValue}")
    for Name, Value in Defaults.items():
        DisplayValue = Value
        if Name == "TopModule" and not Value:
            DisplayValue = "auto-detect"
        if Name == "OutputName" and not Value:
            DisplayValue = "input filename"
        print(f"  {Name}: {DisplayValue}")


def ConfigureDefaults(
    Defaults: dict[str, object],
    PathValue: Path,
) -> dict[str, object]:
    """Edit persistent defaults using guided prompts."""
    Updated = dict(Defaults)
    print("Configure Defaults")
    print("Press Enter to retain the displayed value.")
    Updated["InputPath"] = PromptText(
        "Default SystemVerilog file (blank means prompt)",
        str(Defaults["InputPath"]),
    )
    Updated["OutputDirectory"] = PromptText(
        "Output directory",
        str(Defaults["OutputDirectory"]),
    )
    Updated["OutputName"] = PromptText(
        "Output name (blank means input filename)",
        str(Defaults["OutputName"]),
    )
    TraceBlocksValue = Defaults.get("TraceSupportBlocks", ())
    TraceBlocksDisplay = (
        ",".join(TraceBlocksValue)
        if isinstance(TraceBlocksValue, (list, tuple))
        else str(TraceBlocksValue)
    )
    Updated["TraceSupportBlocks"] = ParseTraceSupportBlocks(
        PromptText(
            "Trace support blocks (comma-separated block IDs)",
            TraceBlocksDisplay,
        )
    )
    Updated["TopModule"] = PromptText(
        "Top module (blank means auto-detect)",
        str(Defaults["TopModule"]),
    )
    Updated["WorkDirectory"] = PromptText(
        "Compiler work directory",
        str(Defaults["WorkDirectory"]),
    )
    Updated["PushToMinecraft"] = PromptBoolean(
        "Push after compiling",
        bool(Defaults["PushToMinecraft"]),
    )
    Updated["MinecraftDirectory"] = PromptText(
        "Minecraft schematics directory",
        str(Defaults["MinecraftDirectory"]),
    )
    Updated["PushFilePath"] = PromptText(
        "Default litematic to push",
        str(Defaults["PushFilePath"]),
    )
    SaveDefaults(PathValue, Updated)
    print(f"Saved defaults: {PathValue}")
    return Updated


def PromptPath(Label: str, Default: Path | None = None) -> Path:
    DefaultText = f" [{Default}]" if Default is not None else ""
    while True:
        Value = input(f"{Label}{DefaultText}: ").strip()
        if Value:
            return ParsePromptPath(Value)
        if Default is not None:
            return Default
        print("A path is required.")


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


def PromptCompile(
    Defaults: dict[str, object],
) -> tuple[Path, Path, Path, str | None, Path, bool, tuple[str, ...]]:
    DefaultInput = str(Defaults["InputPath"])
    InputPath = PromptPath(
        "SystemVerilog file",
        Path(DefaultInput) if DefaultInput else None,
    )
    TopValue = PromptText(
        "Top module",
        str(Defaults["TopModule"]),
    )
    OutputDirectory = PromptPath(
        "Output directory",
        Path(str(Defaults["OutputDirectory"])),
    )
    DefaultOutputName = str(Defaults["OutputName"]) or InputPath.stem
    BaseName = PromptText("Output name", DefaultOutputName)
    PushResult = PromptBoolean(
        "Push to Minecraft after compiling",
        bool(Defaults["PushToMinecraft"]),
    )
    TraceSupportBlocks = ParseTraceSupportBlocks(Defaults.get("TraceSupportBlocks"))
    ArtifactDirectory = OutputDirectory / BaseName
    OutputPath = ArtifactDirectory / f"{BaseName}.litematic"
    DiagramPath = ArtifactDirectory / f"{BaseName}.Nand.json"
    Workdir = Path(str(Defaults["WorkDirectory"]))
    return (
        InputPath,
        OutputPath,
        DiagramPath,
        TopValue or None,
        Workdir,
        PushResult,
        TraceSupportBlocks,
    )


def GuidedMenu(
    Defaults: dict[str, object],
    DefaultsFile: Path,
) -> tuple[
    tuple[
        Path,
        Path,
        Path,
        str | None,
        Path,
        bool,
        tuple[str, ...],
    ]
    | None,
    dict[str, object],
]:
    while True:
        print("RedstoneCompiler")
        print("1. Compile SystemVerilog")
        print("2. Configure defaults")
        print("3. Show defaults")
        print("4. Push an existing litematic to Minecraft")
        print("5. Reset defaults")
        print("6. Exit")
        Choice = input("Select an option [1]: ").strip() or "1"
        if Choice == "1":
            return PromptCompile(Defaults), Defaults
        if Choice == "2":
            Defaults = ConfigureDefaults(Defaults, DefaultsFile)
            continue
        if Choice == "3":
            ShowDefaults(Defaults, DefaultsFile)
            continue
        if Choice == "4":
            LitematicPath = PromptPath(
                "Litematic file",
                Path(str(Defaults["PushFilePath"])),
            )
            DestinationPath = PushToMinecraft(
                LitematicPath,
                Path(str(Defaults["MinecraftDirectory"])),
            )
            print(f"Pushed to Minecraft: {DestinationPath}")
            continue
        if Choice == "5":
            if PromptBoolean("Reset all defaults", False):
                Defaults = dict(BuiltInDefaults)
                SaveDefaults(DefaultsFile, Defaults)
                print(f"Reset defaults: {DefaultsFile}")
            continue
        if Choice == "6":
            return None, Defaults
        print(f"Unknown menu option: {Choice}")


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
            print(
                "  deadline: "
                f"elapsed={Elapsed}s remaining={Remaining}ms "
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

        if Parsed.guided or not RawArgs:
            Guided, Defaults = GuidedMenu(
                Defaults,
                Parsed.defaults_file,
            )
            if Guided is None:
                return 0
            (
                InputPath,
                OutputPath,
                DiagramPath,
                TopModule,
                Workdir,
                PushResult,
                TraceSupportBlocks,
            ) = Guided
            MinecraftDirectory = Path(str(Defaults["MinecraftDirectory"]))
        else:
            if Parsed.input is None:
                Parser.error("--input is required outside guided mode")
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

        # Start telemetry after guided input.  Otherwise the total interval
        # includes arbitrary time spent answering CLI prompts and can look
        # like a harness timeout despite the compiler having finished within
        # its actual routing budget.
        CpuTelemetry = CpuRunTelemetry()
        atexit.register(CpuTelemetry.PrintSummary)
        SearchDescription = "Generating clustered PCB-style multilayer routing..."
        print(SearchDescription, file=sys.stderr)
        ProgressReporter = BuildProgressReporter(CpuTelemetry)
        try:
            if Parsed.routing_threads is not None:
                if Parsed.routing_threads <= 0:
                    Parser.error("--routing-threads must be positive")
                os.environ["RC_ROUTING_THREADS"] = str(Parsed.routing_threads)
            CpuTelemetry.BeginCompilation()
            Result = CompileSvToLitematic(
                InputPath=InputPath,
                OutputPath=OutputPath,
                DiagramPath=DiagramPath,
                TopModule=TopModule,
                Workdir=Workdir,
                ProgressCallback=ProgressReporter,
                RoutingStrategyValue=Parsed.routing_strategy,
                RoutingDeadlineSeconds=Parsed.routing_deadline_seconds,
                TraceSupportBlocks=TraceSupportBlocks,
            )
        finally:
            CpuTelemetry.FinishCompilation()
            ProgressReporter.Finish()

        DestinationPath = None
        if PushResult:
            DestinationPath = PushToMinecraft(
                Result.OutputPath,
                MinecraftDirectory,
            )
    except (FileNotFoundError, ValueError, NotImplementedError) as Error:
        print(f"Operation failed: {Error}", file=sys.stderr)
        PrintRoutingFailureSummary(Error, OutputPath)
        return 1

    print(
        f"Logic optimization: {Result.OriginalLogicGateCount} -> "
        f"{Result.OptimizedLogicGateCount} IR gates"
    )
    print(f"Compiled {Result.NandGateCount} NAND gates")
    if Result.RoutingMetrics is not None:
        Metrics = Result.RoutingMetrics
        print(
            "Routing quality: "
            f"stage={Metrics.Stage}, nets={Metrics.NetCount}, "
            f"length={Metrics.TotalLength}, bends={Metrics.BendCount}, "
            f"vias={Metrics.ViaCount}, rerouted={Metrics.ReroutedNets}, "
            f"conflicts={Metrics.ConflictCount}, "
            f"overflow_peak={Metrics.CorridorOverflowPeak}"
        )
    ResultLabel = "PCB layout generated"
    Composition = Result.BlockComposition
    print(
        f"{ResultLabel}: {Result.EstimatedBlocks} blocks, "
        f"{Result.Width}x{Result.Depth}, "
        f"xz_footprint {Composition.Footprint}, "
        f"xy_footprint {Composition.XYFootprint}, "
        f"full_footprint {Composition.FullFootprint}"
    )
    print(
        "Block composition: "
        f"components={Composition.ComponentOwnedFunctionalBlocks} "
        f"({Composition.ComponentFunctionalShare:.1%}), "
        f"routing={Composition.RoutingOwnedFunctionalBlocks} "
        f"({Composition.RoutingFunctionalShare:.1%}), "
        f"dust={Composition.RawDustBlocks} "
        f"({Composition.RawDustFunctionalShare:.1%} functional), "
        f"support={Composition.SupportBlocks}, "
        f"annotations={Composition.AnnotationBlocks}"
    )
    print("Output mode: Authoritative resource-graph PCB router")
    print(
        "Routing strategy: "
        f"requested={Result.RequestedStrategy}, used={Result.UsedStrategy}, "
        f"fallback={'yes' if Result.FallbackUsed else 'no'}"
    )
    if Result.FallbackReason:
        print(f"Fallback reason: {Result.FallbackReason}")
    print(
        f"Run summary: runtime={Result.RuntimeSeconds:.3f}s, "
        f"max_net_share={Result.MaximumNetLengthShare:.3%}"
    )
    print(f"NAND JSON: {Result.DiagramPath}")
    print(f"NAND DOT:  {Result.DotPath}")
    print(
        f"Redstone simulation: "
        f"{'PASS' if Result.TruthTablePassed else 'FAIL'} "
        f"({Result.TruthTableRows} truth-table rows)"
    )
    print(f"Truth table: {Result.TruthTablePath}")
    print(f"Litematic: {Result.OutputPath}")
    if DestinationPath is not None:
        print(f"Minecraft: {DestinationPath}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(Main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130) from None

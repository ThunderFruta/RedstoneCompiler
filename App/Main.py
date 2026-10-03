"""Project-root guided and flag-driven RedstoneCompiler entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from App import CompilerCli as CompilerCli


def BuildParser() -> argparse.ArgumentParser:
    """Expose compiler flags plus the root-owned guided-menu switch."""
    Parser = CompilerCli.BuildParser()
    Parser.add_argument(
        "--guided",
        action="store_true",
        help="Open the project-root guided menu",
    )
    return Parser


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


def PromptPath(Label: str, Default: Path | None = None) -> Path:
    DefaultText = f" [{Default}]" if Default is not None else ""
    while True:
        Value = input(f"{Label}{DefaultText}: ").strip()
        if Value:
            return CompilerCli.ParsePromptPath(Value)
        if Default is not None:
            return Default
        print("A path is required.")


def RunDebuggingIoDryRun() -> Path:
    """Write matching success and simulated-failure reports through real I/O."""
    import tempfile
    import time
    from App.RunReporting import BuildRunId, UtcTimestamp, WriteRunReport

    RepositoryRoot = Path(__file__).resolve().parent.parent
    OutputRoot = RepositoryRoot / "Output" / "DebuggingIO"
    OutputRoot.mkdir(parents=True, exist_ok=True)
    RunRoot = Path(tempfile.mkdtemp(
        prefix=f"{BuildRunId()}-dry-run-",
        dir=OutputRoot,
    ))
    StartedAt = time.monotonic()

    SuccessDirectory = RunRoot / "Success"
    SuccessDirectory.mkdir()
    SuccessOutput = "Dry-run success: standard output was captured.\n"
    SuccessReport = WriteRunReport(
        RunDirectory=SuccessDirectory,
        Result="SUCCESS",
        WallSeconds=max(0.001, time.monotonic() - StartedAt),
        CpuSeconds=0.0,
        Summary="Synthetic success report from the debugging I/O dry run.",
        RepositoryRoot=RepositoryRoot,
        StartedAtUtc=UtcTimestamp(),
        CompletedAtUtc=UtcTimestamp(),
        Command=[sys.executable, "Main.py", "--synthetic-debugging-io-success"],
        WorkingDirectory=RepositoryRoot,
        Stdout=SuccessOutput,
        Details={"DebuggingDryRun": True, "OutcomeSource": "synthetic"},
    )

    FailureDirectory = RunRoot / "Failure"
    FailureDirectory.mkdir()
    FailureArtifact = FailureDirectory / "DryRun.RoutingFailure.json"
    FailureArtifact.write_text(json.dumps({
        "SchemaVersion": "routing-failure-v1",
        "Failure": {
            "Stage": "DebuggingIoDryRun",
            "Reason": "SyntheticFailure",
            "Detail": "This failure is simulated to exercise report output.",
        },
    }, indent=2) + "\n", encoding="utf-8")
    FailureReport = WriteRunReport(
        RunDirectory=FailureDirectory,
        Result="FAILURE",
        WallSeconds=max(0.001, time.monotonic() - StartedAt),
        CpuSeconds=0.0,
        Summary="Synthetic failure report from the debugging I/O dry run.",
        RepositoryRoot=RepositoryRoot,
        StartedAtUtc=UtcTimestamp(),
        CompletedAtUtc=UtcTimestamp(),
        Command=[sys.executable, "Main.py", "--synthetic-debugging-io-failure"],
        WorkingDirectory=RepositoryRoot,
        Stdout="Dry-run failure: captured output before the synthetic failure.\n",
        Stderr="Synthetic failure output for debugging report display.\n",
        FailureType="Debugging I/O dry run: SyntheticFailure",
        ExceptionText="Synthetic exception text; no compiler operation was run.",
        Details={"DebuggingDryRun": True, "OutcomeSource": "synthetic"},
        RoutingFailurePath=FailureArtifact,
    )

    print("DEBUGGING I/O DRY RUN: synthetic success and failure reports saved.")
    print("SUCCESS REPORT:")
    print("\n".join(SuccessReport.ResultLines))
    print(f"SUCCESS REPORT DIRECTORY: {SuccessDirectory}")
    print("FAILURE REPORT:")
    print("\n".join(FailureReport.ResultLines))
    print(f"FAILURE REPORT DIRECTORY: {FailureDirectory}")
    print(f"DRY RUN FILES: {RunRoot}")
    return RunRoot


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
    Updated["TraceSupportBlocks"] = CompilerCli.ParseTraceSupportBlocks(
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


def BuildGuidedCompileArguments(
    Defaults: dict[str, object],
    DefaultsFile: Path,
) -> list[str]:
    """Translate guided answers into the compiler's ordinary flag contract."""
    DefaultInput = str(Defaults["InputPath"])
    InputPath = PromptPath(
        "SystemVerilog file",
        Path(DefaultInput) if DefaultInput else None,
    )
    TopValue = PromptText("Top module", str(Defaults["TopModule"]))
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
    TraceSupportBlocks = CompilerCli.ParseTraceSupportBlocks(
        Defaults.get("TraceSupportBlocks")
    )
    ArtifactDirectory = OutputDirectory / BaseName
    Arguments = [
        "--input",
        str(InputPath),
        "--output",
        str(ArtifactDirectory / f"{BaseName}.litematic"),
        "--diagram",
        str(ArtifactDirectory / f"{BaseName}.Nand.json"),
        "--workdir",
        str(Defaults["WorkDirectory"]),
        "--defaults-file",
        str(DefaultsFile),
        "--minecraft-directory",
        str(Defaults["MinecraftDirectory"]),
    ]
    if TopValue:
        Arguments.extend(("--top", TopValue))
    if PushResult:
        Arguments.append("--push")
    if TraceSupportBlocks:
        Arguments.extend((
            "--trace-support-blocks",
            ",".join(TraceSupportBlocks),
        ))
    return Arguments


def MoreOptionsMenu(
    Defaults: dict[str, object],
    DefaultsFile: Path,
) -> dict[str, object]:
    """Run defaults and artifact utilities, returning current defaults."""
    while True:
        print("More options")
        print("1. Dry run report I/O (success and failure)")
        print("2. Configure defaults")
        print("3. Show defaults")
        print("4. Push an existing litematic to Minecraft")
        print("5. Back")
        Choice = input("Select an option [5]: ").strip() or "5"
        if Choice == "1":
            try:
                RunDebuggingIoDryRun()
            except (OSError, ValueError) as Error:
                print(f"Debugging I/O dry run failed: {Error}")
            continue
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
            DestinationPath = CompilerCli.PushToMinecraft(
                LitematicPath,
                Path(str(Defaults["MinecraftDirectory"])),
            )
            print(f"Pushed to Minecraft: {DestinationPath}")
            continue
        if Choice == "5":
            return Defaults
        print(f"Unknown menu option: {Choice}")


def DebuggingMenu(
    Defaults: dict[str, object],
    DefaultsFile: Path,
) -> list[str] | None:
    """Expose hook capture and existing diagnostic tools through guided input."""
    while True:
        print("Debugging")
        print("1. Compile with compiler hooks")
        print("2. Diagnose a saved compiler trace")
        print("3. Inspect saved routing CPU telemetry")
        print("4. Review source structure")
        print("5. Back")
        Choice = input("Select an option [5]: ").strip() or "5"
        try:
            if Choice == "1":
                Arguments = BuildGuidedCompileArguments(Defaults, DefaultsFile)
                while True:
                    Value = PromptText("Hook stages (comma-separated; Enter for all)")
                    Stages = [Stage.strip() for Stage in Value.split(",") if Stage.strip()]
                    try:
                        for Stage in Stages:
                            CompilerCli.ParseCompilerHookStage(Stage)
                    except argparse.ArgumentTypeError as Error:
                        print(f"Invalid hook stages: {Error}")
                        continue
                    break
                if Stages:
                    for Stage in Stages:
                        Arguments.extend(("--compiler-hook-stage", Stage))
                else:
                    Arguments.append("--compiler-hooks")
                return Arguments
            if Choice == "2":
                from Compilation.Hooks import ReadCompilerTrace, DiagnoseCompilerTrace

                TracePath = PromptPath("Compiler trace JSON file")
                Diagnosis = DiagnoseCompilerTrace(ReadCompilerTrace(TracePath))
                print(json.dumps(Diagnosis, indent=2))
                continue
            if Choice == "3":
                Directory = PromptPath("Compiler run directory")
                SummaryPath = Directory / "RoutingTelemetry.txt"
                with SummaryPath.open("rb") as Stream:
                    Summary = Stream.read(1_000_001)
                if len(Summary) > 1_000_000:
                    raise ValueError("saved telemetry summary exceeds display limit")
                print(Summary.decode("utf-8"))
                continue
            if Choice == "4":
                from Tools.Routing.ReviewSourceStructure import Main as ReviewMain

                ReviewMain([])
                continue
            if Choice == "5":
                return None
            print(f"Unknown menu option: {Choice}")
        except (OSError, ValueError) as Error:
            print(f"Debugging tool failed: {Error}")


def RunBenchmark(Args: list[str] | None = None) -> int:
    """Run the router acceptance benchmark through its canonical script."""
    from Tools.Routing.RunRouterAcceptance import Main as AcceptanceMain

    RawArgs = list(sys.argv[1:] if Args is None else Args)
    if not RawArgs:
        RawArgs = ["--matrix", "default"]
    return AcceptanceMain(RawArgs)


def GuidedMenu(
    Defaults: dict[str, object],
    DefaultsFile: Path,
) -> tuple[list[str] | None, dict[str, object]]:
    """Run the project-level interactive menu and return compiler flags."""
    while True:
        print("RedstoneCompiler")
        print("1. Compile SystemVerilog")
        print("2. PyTest")
        print("3. Benchmark")
        print("4. Debugging")
        print("5. More options")
        print("6. Exit")
        Choice = input("Select an option [1]: ").strip() or "1"
        if Choice == "1":
            return BuildGuidedCompileArguments(Defaults, DefaultsFile), Defaults
        if Choice == "2":
            CompilerCli.RunPytest()
            continue
        if Choice == "3":
            RunBenchmark([])
            continue
        if Choice == "4":
            Arguments = DebuggingMenu(Defaults, DefaultsFile)
            if Arguments is not None:
                return Arguments, Defaults
            continue
        if Choice == "5":
            Defaults = MoreOptionsMenu(Defaults, DefaultsFile)
            continue
        if Choice == "6":
            return None, Defaults
        print(f"Unknown menu option: {Choice}")


def Main(Args: list[str] | None = None) -> int:
    """Own guided interaction at the root and delegate flag runs."""
    RawArgs = list(sys.argv[1:] if Args is None else Args)
    Parsed = BuildParser().parse_args(RawArgs)
    if RawArgs and not Parsed.guided:
        return CompilerCli.Main(RawArgs)
    try:
        Defaults = CompilerCli.LoadDefaults(Parsed.defaults_file)
        GuidedArguments, _Defaults = GuidedMenu(
            Defaults,
            Parsed.defaults_file,
        )
        if GuidedArguments is None:
            return 0
        return CompilerCli.Main(GuidedArguments)
    except (FileNotFoundError, ValueError, NotImplementedError) as Error:
        print(f"Operation failed: {Error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(Main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130) from None

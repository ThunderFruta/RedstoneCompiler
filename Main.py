"""Project-root guided and flag-driven RedstoneCompiler entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from Compiler import Main as CompilerCli


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
        print("1. Configure defaults")
        print("2. Show defaults")
        print("3. Push an existing litematic to Minecraft")
        print("4. Back")
        Choice = input("Select an option [4]: ").strip() or "4"
        if Choice == "1":
            Defaults = ConfigureDefaults(Defaults, DefaultsFile)
            continue
        if Choice == "2":
            ShowDefaults(Defaults, DefaultsFile)
            continue
        if Choice == "3":
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
        if Choice == "4":
            return Defaults
        print(f"Unknown menu option: {Choice}")


def RunBenchmark(Args: list[str] | None = None) -> int:
    """Run the router acceptance benchmark through its canonical script."""
    from Scripts.Routing.RunRouterAcceptance import Main as AcceptanceMain

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
        print("4. More options")
        print("5. Exit")
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
            Defaults = MoreOptionsMenu(Defaults, DefaultsFile)
            continue
        if Choice == "5":
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

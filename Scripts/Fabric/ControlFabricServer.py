#!/usr/bin/env python3
"""Configure or control a running local RedstoneCompiler Fabric server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Compiler.FabricServer import FabricServerConfiguration, FabricServerSupervisor


def BuildParser() -> argparse.ArgumentParser:
    """Build the explicit non-interactive command interface."""
    Parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --server-root .runtime/fabric-26.2 --configure-quiet-world\n"
            "  %(prog)s --server-root .runtime/fabric-26.2 --pause\n"
            "  %(prog)s --server-root .runtime/fabric-26.2 --step 20\n"
            "  %(prog)s --server-root .runtime/fabric-26.2 --resume"
        ),
    )
    Parser.add_argument(
        "--server-root",
        type=Path,
        required=True,
        help="directory of the already-running local Fabric server",
    )
    Actions = Parser.add_mutually_exclusive_group(required=True)
    Actions.add_argument("--configure-quiet-world", action="store_true", help="disable mob spawning, drops, daylight, and weather cycles")
    Actions.add_argument("--pause", action="store_true", help="freeze simulation ticks")
    Actions.add_argument("--resume", action="store_true", help="unfreeze simulation ticks")
    Actions.add_argument("--step", type=int, metavar="TICKS", help="advance an already-paused server by this many ticks")
    Actions.add_argument(
        "--clear",
        action="store_true",
        help="clear all blocks inside every imported fixture's full bounds without resetting the world",
    )
    return Parser


def GuidedArguments() -> list[str]:
    """Ask for one safe, explicit server action when no flags were supplied."""
    DefaultRoot = ".runtime/fabric-26.2"
    print("RedstoneCompiler Fabric server control")
    Root = input(f"Server root [{DefaultRoot}]: ").strip() or DefaultRoot
    print("1) Configure quiet world\n2) Pause ticks\n3) Resume ticks\n4) Step paused ticks\n5) Clear all imported blocks\n0) Exit")
    Choice = input("Choose an action [0]: ").strip() or "0"
    Actions = {
        "1": ["--configure-quiet-world"],
        "2": ["--pause"],
        "3": ["--resume"],
        "5": ["--clear"],
    }
    if Choice == "0":
        raise EOFError
    if Choice == "4":
        Ticks = input("Ticks to advance [1]: ").strip() or "1"
        return ["--server-root", Root, "--step", Ticks]
    if Choice not in Actions:
        raise ValueError("choose 0, 1, 2, 3, 4, or 5")
    return ["--server-root", Root, *Actions[Choice]]


def BuildClearRegions(ServerRoot: Path) -> list[dict[str, object]]:
    """Return absolute clear bounds for every fixture imported by this tool."""
    Regions = []
    for FixturePath in sorted((ServerRoot / "fixtures").glob("*.FabricFixture.json")):
        Fixture = json.loads(FixturePath.read_text(encoding="utf-8"))
        Arena = Fixture["Arena"]
        Origin = [int(Value) for Value in Arena["Origin"]]
        Bounds = Arena.get("Bounds")
        if Bounds is None:
            Positions = [Block["Position"] for Block in Fixture["Blocks"]]
            if not Positions:
                continue
            Minimum = [min(Position[Axis] for Position in Positions) for Axis in range(3)]
            Maximum = [max(Position[Axis] for Position in Positions) for Axis in range(3)]
        else:
            Minimum = [int(Value) for Value in Bounds["Minimum"]]
            Maximum = [int(Value) for Value in Bounds["Maximum"]]
        Regions.append({
            "Minimum": [Origin[Axis] + Minimum[Axis] for Axis in range(3)],
            "Maximum": [Origin[Axis] + Maximum[Axis] for Axis in range(3)],
            "Fixture": FixturePath.name,
        })
    return Regions


def main(Arguments: list[str] | None = None) -> int:
    Parser = BuildParser()
    RawArguments = list(sys.argv[1:] if Arguments is None else Arguments)
    if not RawArguments:
        try:
            RawArguments = GuidedArguments()
        except (EOFError, KeyboardInterrupt):
            print("No action selected. Run with --help for explicit commands.")
            return 2
        except ValueError as Error:
            Parser.error(str(Error))
    Arguments = Parser.parse_args(RawArguments)
    if Arguments.configure_quiet_world:
        Action, StepTicks = "ConfigureQuietWorld", None
    elif Arguments.pause:
        Action, StepTicks = "PauseTicks", None
    elif Arguments.resume:
        Action, StepTicks = "ResumeTicks", None
    elif Arguments.clear:
        Action, StepTicks = "ClearImportedBlocks", None
    else:
        if Arguments.step <= 0:
            Parser.error("--step must be positive")
        Action, StepTicks = "StepTicks", Arguments.step
    ServerRoot = Arguments.server_root.resolve()
    ClearRegions = BuildClearRegions(ServerRoot) if Arguments.clear else None
    Result = FabricServerSupervisor(
        FabricServerConfiguration(Root=ServerRoot),
    ).ControlRunningServer(
        Action=Action,
        StepTicks=StepTicks,
        ClearRegions=ClearRegions,
    )
    print({"Status": Result.Status, "Diagnostics": Result.Diagnostics})
    return 0 if Result.Status != "infrastructure-failure" else 1


if __name__ == "__main__":
    raise SystemExit(main())

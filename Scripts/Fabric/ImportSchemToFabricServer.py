#!/usr/bin/env python3
"""Import a Sponge .schem into the running local Fabric server."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Compiler.FabricServer import (
    BuildFabricFixtureFromSchem,
    CaptureServerUpdatedLitematic,
    FabricServerConfiguration,
    FabricServerSupervisor,
    ResolveFabricServerRoot,
    WriteFabricFixture,
)


def BuildParser() -> argparse.ArgumentParser:
    """Build the explicit non-interactive command interface."""
    Parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  %(prog)s build.schem "
            "--origin 0 64 0 --replace\n\n"
            "Repeat the command after saving the schematic to hot-reload it. "
            "Omit --replace to layer blocks over the existing world."
        ),
    )
    Parser.add_argument("Schem", type=Path, help="Sponge v2/v3 .schem or compiler .litematic file to load")
    Parser.add_argument(
        "--server-root",
        type=Path,
        default=ResolveFabricServerRoot(),
        help=(
            "running Fabric server directory "
            f"(default: {ResolveFabricServerRoot()})"
        ),
    )
    Parser.add_argument(
        "--origin",
        nargs=3,
        type=int,
        metavar=("X", "Y", "Z"),
        default=(0, 64, 0),
        help="world coordinate for the schematic's minimum corner (default: 0 64 0)",
    )
    Parser.add_argument("--replace", action="store_true", help="clear the schematic bounding box before loading")
    Parser.add_argument(
        "--server-updated-output",
        type=Path,
        help=(
            "write the post-update server snapshot here "
            "(default: <schematic>.ServerUpdated.litematic)"
        ),
    )
    Parser.add_argument(
        "--no-server-updated-litematic",
        action="store_true",
        help="load only; do not capture the server's post-update block states",
    )
    return Parser


def GuidedArguments() -> list[str]:
    """Ask for one hot-reload request when the command has no flags."""
    print("RedstoneCompiler schematic hot reload")
    Schem = input("Path to the .schem or .litematic file: ").strip().strip("'\"")
    if not Schem:
        raise ValueError("a .schem or .litematic path is required")
    DefaultRoot = str(ResolveFabricServerRoot())
    Root = input(f"Server root [{DefaultRoot}]: ").strip() or DefaultRoot
    Origin = input("Origin X Y Z [0 64 0]: ").strip() or "0 64 0"
    Coordinates = Origin.split()
    if len(Coordinates) != 3:
        raise ValueError("origin must contain exactly X Y Z")
    Replace = input("Clear the schematic bounds first? [Y/n]: ").strip().lower()
    Result = [Schem, "--server-root", Root, "--origin", *Coordinates]
    if Replace not in {"n", "no"}:
        Result.append("--replace")
    return Result


def main(Arguments: list[str] | None = None) -> int:
    Parser = BuildParser()
    RawArguments = list(sys.argv[1:] if Arguments is None else Arguments)
    if not RawArguments:
        try:
            RawArguments = GuidedArguments()
        except (EOFError, KeyboardInterrupt):
            print("No schematic selected. Run with --help for explicit commands.")
            return 2
        except ValueError as Error:
            Parser.error(str(Error))
    Arguments = Parser.parse_args(RawArguments)
    if Arguments.no_server_updated_litematic and Arguments.server_updated_output:
        Parser.error(
            "--no-server-updated-litematic cannot be combined with "
            "--server-updated-output",
        )
    Fixture = BuildFabricFixtureFromSchem(
        Arguments.Schem,
        Origin=tuple(Arguments.origin),
        ResetBeforeLoad=Arguments.replace,
    )
    FixturePath = Arguments.server_root.resolve() / "fixtures" / f"{Arguments.Schem.stem}.FabricFixture.json"
    Artifact = WriteFabricFixture(FixturePath, Fixture)
    Supervisor = FabricServerSupervisor(
        FabricServerConfiguration(Root=Arguments.server_root.resolve()),
    )
    Result = Supervisor.LoadIntoRunningServer(Fixture=Artifact)
    Snapshot: dict[str, object] | None = None
    Status = Result.Status
    if Result.Status == "loaded" and not Arguments.no_server_updated_litematic:
        OutputPath = (
            Arguments.server_updated_output
            or Arguments.Schem.with_name(
                f"{Arguments.Schem.stem}.ServerUpdated.litematic",
            )
        )
        try:
            SnapshotArtifact = CaptureServerUpdatedLitematic(
                Supervisor=Supervisor,
                Fixture=Fixture,
                SourcePath=Arguments.Schem,
                OutputPath=OutputPath,
            )
            Snapshot = {
                "Path": str(SnapshotArtifact.Path),
                "RequestedPositionCount": SnapshotArtifact.RequestedPositionCount,
                "ObservedBlockCount": SnapshotArtifact.ObservedBlockCount,
                "WorldReadRequests": SnapshotArtifact.WorldReadRequests,
                "InputCountSetToZero": SnapshotArtifact.InputCountSetToZero,
                "SnapshotReadPasses": SnapshotArtifact.SnapshotReadPasses,
                "InputZeroGameTime": SnapshotArtifact.InputZeroGameTime,
                "FirstObservedGameTime": SnapshotArtifact.FirstObservedGameTime,
                "LastObservedGameTime": SnapshotArtifact.LastObservedGameTime,
            }
        except (OSError, RuntimeError, ValueError) as Error:
            Status = "snapshot-failure"
            Snapshot = {"Error": str(Error)}
    print({
        "Status": Status,
        "Fixture": str(Artifact.Path),
        "Inputs": Artifact.InputCount,
        "Outputs": Artifact.OutputCount,
        "ServerUpdatedLitematic": Snapshot,
        "Diagnostics": Result.Diagnostics,
    })
    return 0 if Status == "loaded" else 1


if __name__ == "__main__":
    raise SystemExit(main())

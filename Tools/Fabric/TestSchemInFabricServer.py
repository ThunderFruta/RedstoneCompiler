#!/usr/bin/env python3
"""Test a pasted or manually edited compiler circuit in the Fabric server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Callable


RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Validation.Fabric import BuildFabricFixtureFromSchem, BuildImportedSchematicVectors, FabricServerConfiguration, FabricServerSupervisor, FabricServerValidationResult, ReadFabricFixture, ReadNandModule, ReadSvModule, ResolveFabricServerRoot, WriteFabricFixture


def BuildParser() -> argparse.ArgumentParser:
    """Build the explicit non-interactive schematic test interface."""
    Parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Normal mode clears and reloads the fixture before testing. Use "
            "--existing-state <design.sv> to preserve manually edited world "
            "blocks and test them against the SystemVerilog source."
        ),
    )
    Parser.add_argument(
        "Schem",
        nargs="?",
        type=Path,
        help="previously imported compiler .litematic or .schem artifact",
    )
    Parser.add_argument(
        "--existing-state",
        type=Path,
        metavar="SV",
        help=(
            "do not clear or paste blocks; test the current world using this "
            "SystemVerilog file as the logic oracle; input lever states are "
            "restored after the test"
        ),
    )
    Parser.add_argument(
        "--top",
        help="top module when --existing-state points to a multi-module SV file",
    )
    Parser.add_argument(
        "--litematic",
        type=Path,
        help=(
            "litematic used only to derive a missing existing-world port map; "
            "it is never pasted"
        ),
    )
    Parser.add_argument(
        "--origin",
        nargs=3,
        type=int,
        metavar=("X", "Y", "Z"),
        help=(
            "world origin of the manually pasted circuit when deriving its "
            "port map (default: 0 64 0)"
        ),
    )
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
        "--fixture",
        type=Path,
        help=(
            "port-map fixture path (default: <server-root>/fixtures/"
            "<schem-or-sv-stem>.FabricFixture.json)"
        ),
    )
    Parser.add_argument(
        "--nand",
        type=Path,
        help="NAND oracle path (default: adjacent <schem>.Nand.json)",
    )
    TestMode = Parser.add_mutually_exclusive_group()
    TestMode.add_argument(
        "--all",
        action="store_true",
        help="test every derived truth-table row (the default)",
    )
    TestMode.add_argument(
        "--vector-index",
        "--one",
        "--one-at-a-time",
        dest="vector_index",
        type=int,
        help=(
            "test exactly one zero-based truth-table row; use --all to run "
            "the complete table"
        ),
    )
    TestMode.add_argument(
        "--all-one-at-a-time",
        "--all-sequential",
        dest="all_one_at_a_time",
        action="store_true",
        help=(
            "run every row as its own Fabric validation and report each "
            "row separately"
        ),
    )
    return Parser


def GuidedArguments() -> list[str]:
    """Collect one pasted-fixture or existing-world truth-table test request."""
    print("RedstoneCompiler schematic truth-table test")
    print(
        "1) Reload the imported fixture, then test it\n"
        "2) Test the existing world state without clearing or pasting"
    )
    StateMode = input("Choose world-state mode [1]: ").strip() or "1"
    if StateMode in {"1", "reload", "fixture"}:
        Schem = input("Path to the imported .schem or .litematic file: ").strip().strip("'\"")
        if not Schem:
            raise ValueError("a .schem or .litematic path is required")
        Arguments = [Schem]
    elif StateMode in {"2", "existing", "existing-state"}:
        Source = input("SystemVerilog file describing the existing circuit: ").strip().strip("'\"")
        if not Source:
            raise ValueError("a SystemVerilog source path is required")
        Arguments = ["--existing-state", Source]
    else:
        raise ValueError("choose 1 to reload the fixture or 2 to test existing state")
    DefaultRoot = str(ResolveFabricServerRoot())
    Root = input(f"Server root [{DefaultRoot}]: ").strip() or DefaultRoot
    Arguments.extend(("--server-root", Root))
    print(
        "1) Test one selected truth-table row\n"
        "2) Test all truth-table rows\n"
        "3) Test all truth-table rows one at a time"
    )
    Mode = input("Choose test mode [2]: ").strip() or "2"
    if Mode in {"2", "all"}:
        return [*Arguments, "--all"]
    if Mode in {"3", "all-one-at-a-time", "all-sequential"}:
        return [*Arguments, "--all-one-at-a-time"]
    if Mode not in {"1", "one"}:
        raise ValueError("choose 1 for one row, 2 for all rows, or 3 for sequential rows")
    VectorIndex = input("Truth-table row index (zero-based): ").strip()
    try:
        ParsedIndex = int(VectorIndex)
    except ValueError as Error:
        raise ValueError("truth-table row index must be a non-negative integer") from Error
    if ParsedIndex < 0:
        raise ValueError("truth-table row index must be a non-negative integer")
    return [*Arguments, "--vector-index", str(ParsedIndex)]


def DefaultFixturePath(Schem: Path, ServerRoot: Path) -> Path:
    """Return the fixture created by the matching import command."""
    return ServerRoot / "fixtures" / f"{Schem.stem}.FabricFixture.json"


def DefaultNandPath(Schem: Path) -> Path:
    """Return the compiler NAND oracle emitted beside the schematic."""
    return Schem.with_suffix(".Nand.json")


def ExistingLitematicCandidates(SvPath: Path) -> list[Path]:
    """Return conventional litematic locations for one SV source stem."""
    Stem = SvPath.stem
    Candidates = [
        SvPath.with_suffix(".litematic"),
        RepositoryRoot / "Output" / Stem / f"{Stem}.litematic",
    ]
    DefaultsPath = Path.home() / ".config" / "RedstoneCompiler" / "Defaults.json"
    try:
        Defaults = json.loads(DefaultsPath.read_text(encoding="utf-8"))
        MinecraftDirectory = Defaults.get("MinecraftDirectory")
        if isinstance(MinecraftDirectory, str) and MinecraftDirectory.strip():
            Candidates.append(
                Path(MinecraftDirectory).expanduser() / f"{Stem}.litematic",
            )
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    PrismInstances = (
        Path.home() / ".local/share/PrismLauncher/instances"
    )
    Candidates.extend(sorted(
        PrismInstances.glob(f"*/minecraft/schematics/{Stem}.litematic"),
    ))
    return list(dict.fromkeys(Path(Value).resolve() for Value in Candidates))


def ResolveExistingLitematic(
    SvPath: Path,
    ExplicitPath: Path | None,
) -> Path:
    """Resolve the read-only litematic used to register live port positions."""
    if ExplicitPath is not None:
        Resolved = ExplicitPath.expanduser().resolve()
        if not Resolved.is_file():
            raise FileNotFoundError(
                f"existing-state litematic does not exist: {Resolved}",
            )
        return Resolved
    Candidates = ExistingLitematicCandidates(SvPath)
    Match = next((PathValue for PathValue in Candidates if PathValue.is_file()), None)
    if Match is not None:
        return Match
    Locations = ", ".join(str(PathValue) for PathValue in Candidates)
    raise FileNotFoundError(
        "existing-state port map is missing and no matching litematic was found; "
        f"checked: {Locations}; specify one with --litematic",
    )


def ReadOrRegisterExistingFixture(
    *,
    FixturePath: Path,
    SvPath: Path,
    LitematicPath: Path | None,
    Origin: tuple[int, int, int] | None,
) -> tuple[object, dict[str, object], Path | None]:
    """Read a port map or derive it from a litematic without loading blocks."""
    Regenerate = LitematicPath is not None or Origin is not None
    if FixturePath.is_file() and not Regenerate:
        Artifact, Document = ReadFabricFixture(FixturePath)
        return Artifact, Document, None
    SourceLitematic = ResolveExistingLitematic(SvPath, LitematicPath)
    Document = BuildFabricFixtureFromSchem(
        SourceLitematic,
        Origin=Origin or (0, 64, 0),
        ResetBeforeLoad=False,
    )
    Artifact = WriteFabricFixture(FixturePath, Document)
    return Artifact, Document, SourceLitematic


def SelectTruthTableVectors(
    Vectors: list[dict[str, object]],
    VectorIndex: int | None,
) -> tuple[list[dict[str, object]], int | None]:
    """Return every derived vector or one explicit zero-based truth-table row."""
    if VectorIndex is None:
        return Vectors, None
    if VectorIndex < 0 or VectorIndex >= len(Vectors):
        raise ValueError(
            "truth-table row index is out of range: "
            f"{VectorIndex}; available rows are 0 through {len(Vectors) - 1}",
        )
    return [Vectors[VectorIndex]], VectorIndex


def BuildOneAtATimeResult(
    RowResults: list[FabricServerValidationResult],
) -> FabricServerValidationResult:
    """Combine independently executed rows without hiding any failed status."""
    Statuses = [Result.Status for Result in RowResults]
    if not RowResults or any(Status == "infrastructure-failure" for Status in Statuses):
        Status = "infrastructure-failure"
    elif any(Status == "timeout" for Status in Statuses):
        Status = "timeout"
    elif any(Status != "passed" for Status in Statuses):
        Status = "mismatch"
    else:
        Status = "passed"
    FailedIndexes = [
        Index
        for Index, Result in enumerate(RowResults)
        if Result.Status != "passed"
    ]
    return FabricServerValidationResult(
        Status=Status,
        Backend=next(
            (Result.Backend for Result in RowResults if Result.Backend is not None),
            None,
        ),
        RuntimeSeconds=sum(Result.RuntimeSeconds for Result in RowResults),
        Diagnostics={
            "Mode": "all-one-at-a-time",
            "TestedVectors": len(RowResults),
            "PassedVectors": len(RowResults) - len(FailedIndexes),
            "FailedVectorIndexes": FailedIndexes,
        },
    )


def PauseForNextTruthTableRow(
    CompletedVectorIndex: int,
    VectorCount: int,
) -> None:
    """Require explicit user acknowledgement before the next sequential row."""
    input(
        f"Row {CompletedVectorIndex + 1}/{VectorCount} complete. "
        f"Press Enter to test row {CompletedVectorIndex + 2}/{VectorCount}: ",
    )


def TestAllVectorsOneAtATime(
    Supervisor: FabricServerSupervisor,
    Fixture: object,
    Vectors: list[dict[str, object]],
    ReportRow: Callable[[dict[str, object]], None] | None = None,
    PauseAfterRow: Callable[[int, int], None] | None = None,
    ExistingState: bool = False,
) -> tuple[FabricServerValidationResult, list[dict[str, object]]]:
    """Run every vector independently, pausing between observable rows."""
    Results: list[FabricServerValidationResult] = []
    Rows: list[dict[str, object]] = []
    Validate = Supervisor.ValidateExisting if ExistingState else Supervisor.Validate
    for VectorIndex, Vector in enumerate(Vectors):
        Result = Validate(Fixture=Fixture, Vectors=[Vector])
        Results.append(Result)
        Row = {
            "VectorIndex": VectorIndex,
            "Inputs": Vector["Inputs"],
            "Expected": Vector["Expected"],
            "Status": Result.Status,
            "RuntimeSeconds": Result.RuntimeSeconds,
        }
        if Result.Status != "passed":
            Row["Diagnostics"] = Result.Diagnostics
        Rows.append(Row)
        if ReportRow is not None:
            ReportRow(Row)
        if PauseAfterRow is not None and VectorIndex + 1 < len(Vectors):
            PauseAfterRow(VectorIndex, len(Vectors))
    return BuildOneAtATimeResult(Results), Rows


def Main(Arguments: list[str] | None = None) -> int:
    """Drive all, one, or sequential rows and report the Fabric result."""
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
    Parsed = Parser.parse_args(RawArguments)
    if Parsed.existing_state is None and Parsed.Schem is None:
        Parser.error("Schem is required unless --existing-state specifies an SV file")
    if Parsed.existing_state is not None and Parsed.nand is not None:
        Parser.error("--nand cannot be combined with --existing-state; the SV file is the oracle")
    if Parsed.existing_state is None and Parsed.top is not None:
        Parser.error("--top is only valid with --existing-state")
    if Parsed.existing_state is None and Parsed.litematic is not None:
        Parser.error("--litematic is only valid with --existing-state")
    if Parsed.existing_state is None and Parsed.origin is not None:
        Parser.error("--origin is only valid with --existing-state")
    ExistingState = Parsed.existing_state is not None
    Schem = Parsed.Schem.expanduser().resolve() if Parsed.Schem is not None else None
    SvPath = Parsed.existing_state.expanduser().resolve() if ExistingState else None
    ServerRoot = Parsed.server_root.expanduser().resolve()
    FixtureSource = Schem or SvPath
    assert FixtureSource is not None
    FixturePath = (
        Parsed.fixture or DefaultFixturePath(FixtureSource, ServerRoot)
    ).expanduser().resolve()
    NandPath = (
        (Parsed.nand or DefaultNandPath(Schem)).expanduser().resolve()
        if not ExistingState and Schem is not None
        else None
    )
    try:
        if ExistingState and SvPath is not None:
            Fixture, Document, RegisteredFrom = ReadOrRegisterExistingFixture(
                FixturePath=FixturePath,
                SvPath=SvPath,
                LitematicPath=Parsed.litematic,
                Origin=tuple(Parsed.origin) if Parsed.origin is not None else None,
            )
        else:
            Fixture, Document = ReadFabricFixture(FixturePath)
            RegisteredFrom = None
        Module = (
            ReadSvModule(SvPath, TopModule=Parsed.top)
            if ExistingState and SvPath is not None
            else ReadNandModule(NandPath)
        )
        AllVectors = BuildImportedSchematicVectors(Document, Module)
        Supervisor = FabricServerSupervisor(
            FabricServerConfiguration(Root=ServerRoot),
        )
        if Parsed.all_one_at_a_time:
            Vectors = AllVectors
            SelectedVectorIndex = None
            Result, RowResults = TestAllVectorsOneAtATime(
                Supervisor,
                Fixture,
                Vectors,
                lambda Row: print({"Event": "truth-table-row", **Row}),
                PauseForNextTruthTableRow,
                ExistingState=ExistingState,
            )
            Mode = "all-one-at-a-time"
        else:
            Vectors, SelectedVectorIndex = SelectTruthTableVectors(
                AllVectors,
                Parsed.vector_index,
            )
            Validate = Supervisor.ValidateExisting if ExistingState else Supervisor.Validate
            Result = Validate(Fixture=Fixture, Vectors=Vectors)
            RowResults = []
            Mode = "one" if SelectedVectorIndex is not None else "all"
    except (OSError, ValueError) as Error:
        print({"Status": "infrastructure-failure", "Error": str(Error)})
        return 1
    except (EOFError, KeyboardInterrupt):
        print({"Status": "interrupted", "Mode": "all-one-at-a-time"})
        return 2
    print({
        "Status": Result.Status,
        "Backend": Result.Backend,
        "Fixture": str(Fixture.Path),
        "FixtureRegisteredFrom": (
            str(RegisteredFrom) if RegisteredFrom is not None else None
        ),
        "WorldState": "existing" if ExistingState else "fixture-reloaded",
        "Sv": str(SvPath) if SvPath is not None else None,
        "Nand": str(NandPath) if NandPath is not None else None,
        "Mode": Mode,
        "Vectors": len(Vectors),
        "TotalVectors": len(AllVectors),
        "VectorIndex": SelectedVectorIndex,
        "SelectedInputs": Vectors[0]["Inputs"] if SelectedVectorIndex is not None else None,
        "SelectedExpected": Vectors[0]["Expected"] if SelectedVectorIndex is not None else None,
        "RowsReported": len(RowResults),
        "RuntimeSeconds": Result.RuntimeSeconds,
        "Diagnostics": Result.Diagnostics,
    })
    return 0 if Result.Status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(Main())

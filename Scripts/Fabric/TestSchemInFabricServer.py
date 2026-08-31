#!/usr/bin/env python3
"""Test an imported compiler schematic against its NAND truth table in Fabric."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Callable


RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Compiler.FabricServer import (
    BuildImportedSchematicVectors,
    FabricServerConfiguration,
    FabricServerSupervisor,
    FabricServerValidationResult,
    ReadFabricFixture,
    ReadNandModule,
    ResolveFabricServerRoot,
)


def BuildParser() -> argparse.ArgumentParser:
    """Build the explicit non-interactive schematic test interface."""
    Parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The schematic must first be loaded with "
            "ImportSchemToFabricServer.py. The tester derives the matching "
            ".FabricFixture.json from the server and .Nand.json oracle from "
            "the schematic directory."
        ),
    )
    Parser.add_argument(
        "Schem",
        type=Path,
        help="previously imported compiler .litematic or .schem artifact",
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
        help="imported fixture path (default: <server-root>/fixtures/<schem>.FabricFixture.json)",
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
    """Collect one post-import truth-table test request."""
    print("RedstoneCompiler schematic truth-table test")
    Schem = input("Path to the imported .schem or .litematic file: ").strip().strip("'\"")
    if not Schem:
        raise ValueError("a .schem or .litematic path is required")
    DefaultRoot = str(ResolveFabricServerRoot())
    Root = input(f"Server root [{DefaultRoot}]: ").strip() or DefaultRoot
    Arguments = [Schem, "--server-root", Root]
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
) -> tuple[FabricServerValidationResult, list[dict[str, object]]]:
    """Run every vector independently, pausing between observable rows."""
    Results: list[FabricServerValidationResult] = []
    Rows: list[dict[str, object]] = []
    for VectorIndex, Vector in enumerate(Vectors):
        Result = Supervisor.Validate(Fixture=Fixture, Vectors=[Vector])
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
    Schem = Parsed.Schem.expanduser().resolve()
    ServerRoot = Parsed.server_root.expanduser().resolve()
    FixturePath = (Parsed.fixture or DefaultFixturePath(Schem, ServerRoot)).expanduser().resolve()
    NandPath = (Parsed.nand or DefaultNandPath(Schem)).expanduser().resolve()
    try:
        Fixture, Document = ReadFabricFixture(FixturePath)
        Module = ReadNandModule(NandPath)
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
            )
            Mode = "all-one-at-a-time"
        else:
            Vectors, SelectedVectorIndex = SelectTruthTableVectors(
                AllVectors,
                Parsed.vector_index,
            )
            Result = Supervisor.Validate(Fixture=Fixture, Vectors=Vectors)
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
        "Nand": str(NandPath),
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

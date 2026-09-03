#!/usr/bin/env python3
"""Report routing source-size and ownership signals without gating tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


ScriptDirectory = Path(__file__).resolve().parent
RepositoryRoot = ScriptDirectory.parents[1]
if str(ScriptDirectory) not in sys.path:
    sys.path.insert(0, str(ScriptDirectory))

from CaptureRoutingDesignSnapshot import BuildRoutingSourceManifest


ReviewTargets = {
    "ImplementationModulePhysicalLines": 3_000,
    "OrchestratorPhysicalLines": 500,
    "PythonDefinitionSpanLines": 1_000,
    "SmallImplementationModulePhysicalLines": 150,
}
OwnershipRoots = (
    "Compiler/Placement/Access",
    "Compiler/Placement/Core",
    "Compiler/Placement/Flow",
    "Compiler/Routing/Authoritative",
    "Compiler/Routing/Components",
    "Compiler/Routing/Contracts",
    "Compiler/Routing/Interfaces",
    "RustRouting/Src",
)
OrchestratorPaths = (
    "Compiler/Placement/Core/Commit.py",
    "Compiler/Placement/Flow/Runner.py",
    "Compiler/Routing/Authoritative/BoundaryLeases.py",
    "Compiler/Routing/Authoritative/Flow.py",
    "Compiler/Routing/Authoritative/NegotiatedTrees.py",
    "Compiler/Routing/Authoritative/PortPreparation.py",
    "Compiler/Routing/Authoritative/PortSolving/__init__.py",
    "Compiler/Routing/Components/Pipeline.py",
    "RustRouting/Src/Lib.rs",
)


def BuildSourceReview(Root: Path) -> dict[str, object]:
    """Build one deterministic, advisory-only source review."""
    Manifest = BuildRoutingSourceManifest(Root)
    Files = tuple(Manifest["Files"])
    Ownership = []
    for OwnershipRoot in OwnershipRoots:
        OwnedFiles = tuple(
            Record
            for Record in Files
            if str(Record["Path"]) == OwnershipRoot
            or str(Record["Path"]).startswith(f"{OwnershipRoot}/")
        )
        Ownership.append({
            "Root": OwnershipRoot,
            "FileCount": len(OwnedFiles),
            "PhysicalLines": sum(
                int(Record["PhysicalLines"])
                for Record in OwnedFiles
            ),
        })
    FilesByPath = {str(Record["Path"]): Record for Record in Files}
    Orchestrators = [
        {
            "Path": RelativePath,
            "PhysicalLines": int(FilesByPath[RelativePath]["PhysicalLines"]),
        }
        for RelativePath in OrchestratorPaths
        if RelativePath in FilesByPath
    ]
    return {
        "Status": "advisory",
        "ReviewTargets": ReviewTargets,
        "Totals": Manifest["Metrics"]["Totals"],
        "Ownership": Ownership,
        "Orchestrators": Orchestrators,
        "LargestFiles": Manifest["Metrics"]["LargestFiles"][:15],
        "LargestPythonDefinitions": (
            Manifest["Metrics"]["LargestPythonDefinitions"][:15]
        ),
    }


def RenderText(Review: dict[str, object]) -> str:
    """Render the review as concise terminal text."""
    Lines = [
        "SOURCE STRUCTURE REVIEW (ADVISORY)",
        "Review targets do not affect process exit status.",
        "",
        "TARGETS",
    ]
    for Name, Value in Review["ReviewTargets"].items():
        Lines.append(f"- {Name}: {Value}")
    Lines.extend(("", "OWNERSHIP"))
    for Record in Review["Ownership"]:
        Lines.append(
            f"- {Record['Root']}: {Record['FileCount']} files, "
            f"{Record['PhysicalLines']} physical lines"
        )
    Lines.extend(("", "ORCHESTRATORS"))
    for Record in Review["Orchestrators"]:
        Lines.append(f"- {Record['Path']}: {Record['PhysicalLines']} lines")
    Lines.extend(("", "LARGEST FILES"))
    for Record in Review["LargestFiles"]:
        Lines.append(f"- {Record['Path']}: {Record['PhysicalLines']} lines")
    Lines.extend(("", "LARGEST PYTHON DEFINITIONS"))
    for Record in Review["LargestPythonDefinitions"]:
        Lines.append(
            f"- {Record['QualifiedName']} "
            f"({Record['Path']}:{Record['Line']}): "
            f"{Record['PythonAstSpanLines']} lines"
        )
    return "\n".join(Lines)


def ParseArguments(Arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the advisory output format."""
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="terminal text or deterministic JSON",
    )
    return Parser.parse_args(Arguments)


def Main(Arguments: Sequence[str] | None = None) -> int:
    """Print the review; fail only when source inspection cannot complete."""
    Options = ParseArguments(Arguments)
    try:
        Review = BuildSourceReview(RepositoryRoot)
    except (OSError, SyntaxError, ValueError) as Error:
        print(f"source review failed: {Error}", file=sys.stderr)
        return 2
    if Options.format == "json":
        print(json.dumps(Review, indent=2, sort_keys=True))
    else:
        print(RenderText(Review))
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())

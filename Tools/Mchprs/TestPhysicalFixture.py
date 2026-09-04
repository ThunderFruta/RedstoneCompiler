#!/usr/bin/env python3
"""Exhaustively validate one compiler physical fixture with MCHPRS."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys


RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Validation.Mchprs import MchprsValidator
from Validation.Physical import PhysicalFixtureArtifact


def BuildParser() -> argparse.ArgumentParser:
    """Build the standalone MCHPRS validation parser."""
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument("fixture", type=Path, help="physical fixture JSON")
    Parser.add_argument("--nand", type=Path, required=True, help="NAND oracle JSON")
    return Parser


def Main(Arguments: list[str] | None = None) -> int:
    """Run one exhaustive embedded validation and print structured JSON."""
    Parsed = BuildParser().parse_args(Arguments)
    FixturePath = Parsed.fixture.expanduser().resolve()
    Encoded = FixturePath.read_bytes()
    Fixture = json.loads(Encoded)
    Artifact = PhysicalFixtureArtifact(
        Path=FixturePath,
        Sha256=sha256(Encoded).hexdigest(),
        BlockCount=len(Fixture["Blocks"]),
        InputCount=len(Fixture["Inputs"]),
        OutputCount=len(Fixture["Outputs"]),
    )
    Result = MchprsValidator().Validate(
        Fixture=Artifact,
        LogicPath=Parsed.nand.expanduser().resolve(),
    )
    print(json.dumps({
        "Status": Result.Status,
        "Backend": Result.Backend,
        "RuntimeSeconds": Result.RuntimeSeconds,
        "Diagnostics": Result.Diagnostics,
    }, sort_keys=True))
    return 0 if Result.Status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(Main())

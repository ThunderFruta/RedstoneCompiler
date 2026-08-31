#!/usr/bin/env python3
"""Replay the canonical CLA4 mandatory-access conflict in under five seconds."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic
from typing import Any


RepositoryRoot = Path(__file__).resolve().parents[1]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Compiler.Placement.Core.MandatoryAccess import (
    MeasureMandatoryAccessConflictProfile,
)
from Compiler.Placement.Access.Capacity import (
    FixedPlacementPinAccessDomain,
    FixedPlacementPinAccessStatus,
    ReplayFixedPlacementPinAccessUnsatisfiableCore,
    SolveFixedPlacementPinAccessDomains,
)
from Compiler.Placement.Geometry import (
    BuildPlacementPinAccessWitness,
    PlacedGate,
)


SchemaVersion = "cla4-mandatory-access-replay-v2"
DefaultFixturePath = (
    RepositoryRoot / "Tests/Fixtures/Cla4MandatoryAccessReplay.json"
)


class MandatoryAccessReplayStatus(str, Enum):
    """Typed fixed-placement access outcome used by the replay boundary."""

    Conflict = "Conflict"
    Clean = "Clean"


@dataclass(frozen=True)
class Cla4AccessReplayResult:
    """Deterministic replay evidence, separate from full CLA4 acceptance."""

    Status: MandatoryAccessReplayStatus
    Passed: bool
    RuntimeSeconds: float
    MaximumRuntimeSeconds: float
    SourceArtifactMatched: bool
    SourceCandidateMatched: bool
    ExpectedProfileMatched: bool
    RepeatedProfileMatched: bool
    FixedPlacementSolveMatched: bool
    UnsatisfiableCoreReplayed: bool
    Profile: dict[str, object]
    FixedPlacementSolve: dict[str, object]

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": SchemaVersion,
            "Status": self.Status.value,
            "Passed": self.Passed,
            "RuntimeSeconds": round(self.RuntimeSeconds, 6),
            "MaximumRuntimeSeconds": self.MaximumRuntimeSeconds,
            "SourceArtifactMatched": self.SourceArtifactMatched,
            "SourceCandidateMatched": self.SourceCandidateMatched,
            "ExpectedProfileMatched": self.ExpectedProfileMatched,
            "RepeatedProfileMatched": self.RepeatedProfileMatched,
            "FixedPlacementSolveMatched": self.FixedPlacementSolveMatched,
            "UnsatisfiableCoreReplayed": self.UnsatisfiableCoreReplayed,
            "Profile": self.Profile,
            "FixedPlacementSolve": self.FixedPlacementSolve,
            "AcceptanceBoundary": (
                "Fast fixed-access conflict replay only; this is not routed "
                "or simulated CLA4 acceptance."
            ),
        }


def LoadFixture(FixturePath: Path) -> dict[str, Any]:
    """Load one versioned deterministic replay fixture."""
    Payload = json.loads(FixturePath.read_text(encoding="utf-8"))
    if Payload.get("SchemaVersion") != SchemaVersion:
        raise ValueError(
            "unsupported CLA4 access replay schema: "
            f"{Payload.get('SchemaVersion')!r}"
        )
    return Payload


def BuildReplayGate(Definition: dict[str, Any]) -> PlacedGate:
    """Build the smallest placed output that retains the recorded access ray."""
    OutputPin = tuple(int(Value) for Value in Definition["OutputPin"])
    OutputDirection = tuple(
        int(Value) for Value in Definition["OutputDirection"]
    )
    if len(OutputPin) != 3 or len(OutputDirection) != 3:
        raise ValueError("replay pins and directions must have three coordinates")
    return PlacedGate(
        Name=str(Definition["Name"]),
        Kind="NAND",
        X=OutputPin[0],
        Y=OutputPin[1],
        Z=OutputPin[2],
        Outputs=[str(Definition["Signal"])],
        Inputs=[],
        Attrs={},
        InputPins=[],
        OutputPin=OutputPin,
        Rotation=0,
        MirrorX=False,
        InputDirections=[],
        OutputDirection=OutputDirection,
    )


def Sha256File(PathValue: Path) -> str:
    """Return the stable digest used to bind the replay to its source evidence."""
    return sha256(PathValue.read_bytes()).hexdigest()


def FindSourceCandidate(
    SourcePayload: dict[str, Any],
    ExpectedCandidate: dict[str, Any],
) -> dict[str, Any] | None:
    """Locate the exact placement decision from which the replay was sliced."""
    Decisions = SourcePayload.get("Failure", {}).get(
        "Diagnostics", {}
    ).get("PlacementGenerationDecisions", [])
    for Decision in Decisions:
        if (
            Decision.get("SourceGenerator")
            == ExpectedCandidate.get("SourceGenerator")
            and Decision.get("Result") == ExpectedCandidate.get("Result")
            and Decision.get("ConflictFingerprint")
            == ExpectedCandidate.get("ConflictFingerprint")
        ):
            return Decision
    return None


def NormalizeSourceProfile(Profile: dict[str, Any]) -> dict[str, object]:
    """Select only immutable source fields represented by the replay fixture."""
    return {
        "OwnershipFingerprint": Profile.get("OwnershipFingerprint"),
        "ConflictFingerprint": Profile.get("ConflictFingerprint"),
        "SignalCount": Profile.get("SignalCount"),
        "ClaimCount": Profile.get("ClaimCount"),
        "ConflictResourceCount": Profile.get("ConflictResourceCount"),
        "ConflictSignals": Profile.get("ConflictSignals"),
        "CrossConflicts": [
            {
                "Kind": Conflict.get("Kind"),
                "Owners": Conflict.get("Owners"),
                "Position": Conflict.get("Position"),
            }
            for Conflict in Profile.get("CrossConflicts", [])
        ],
    }


def RunCla4AccessReplay(
    FixturePath: Path = DefaultFixturePath,
) -> Cla4AccessReplayResult:
    """Replay twice and verify provenance, exact evidence, and wall time."""
    Started = monotonic()
    Fixture = LoadFixture(FixturePath)
    SourceDefinition = Fixture["SourceArtifact"]
    SourcePath = RepositoryRoot / SourceDefinition["Path"]
    SourceArtifactMatched = (
        SourcePath.is_file()
        and Sha256File(SourcePath) == SourceDefinition["Sha256"]
    )
    SourceCandidateMatched = False
    if SourceArtifactMatched:
        SourcePayload = json.loads(SourcePath.read_text(encoding="utf-8"))
        Candidate = FindSourceCandidate(
            SourcePayload,
            Fixture["SourceCandidate"],
        )
        if Candidate is not None:
            ExpectedSourceProfile = {
                Key: Value
                for Key, Value in Fixture["SourceCandidate"].items()
                if Key not in {"SourceGenerator", "Result"}
            }
            SourceCandidateMatched = (
                NormalizeSourceProfile(Candidate["MandatoryAccessProfile"])
                == ExpectedSourceProfile
            )

    ReplaySlice = Fixture["ReplaySlice"]
    Gates = tuple(BuildReplayGate(Value) for Value in ReplaySlice["Gates"])
    Signals = tuple(str(Value) for Value in ReplaySlice["Signals"])
    Profile = MeasureMandatoryAccessConflictProfile(Gates, Signals)
    RepeatedProfile = MeasureMandatoryAccessConflictProfile(Gates, Signals)
    ProfileDictionary = Profile.ToDictionary()
    ExpectedProfileMatched = (
        ProfileDictionary == ReplaySlice["ExpectedProfile"]
    )
    RepeatedProfileMatched = (
        RepeatedProfile.ToDictionary() == ProfileDictionary
    )
    AccessWitness = BuildPlacementPinAccessWitness(
        Gates,
        AccessLength=3,
        RequireCatalogMatch=False,
    )
    FixedPlacementSolve = SolveFixedPlacementPinAccessDomains(
        FixedPlacementPinAccessDomain(
            DomainId=(
                f"{Selection.GateName}:{Selection.Role}:{Selection.PinId}"
            ),
            Signal=Selection.Signal,
            Terminal=Selection.Terminal,
            Options=(Selection,),
            Complete=True,
        )
        for Selection in AccessWitness.Selections
    )
    FixedPlacementSolveMatched = (
        FixedPlacementSolve.Status
        is FixedPlacementPinAccessStatus.Unsatisfiable
        and FixedPlacementSolve.UnsatisfiableCore is not None
        and list(FixedPlacementSolve.UnsatisfiableCore.Signals)
        == ProfileDictionary["ConflictSignals"]
    )
    UnsatisfiableCoreReplayed = False
    if FixedPlacementSolve.UnsatisfiableCore is not None:
        ReplayedSolve = ReplayFixedPlacementPinAccessUnsatisfiableCore(
            FixedPlacementSolve.UnsatisfiableCore
        )
        UnsatisfiableCoreReplayed = (
            ReplayedSolve.UnsatisfiableCore is not None
            and ReplayedSolve.UnsatisfiableCore.CoreFingerprint
            == FixedPlacementSolve.UnsatisfiableCore.CoreFingerprint
        )
    RuntimeSeconds = monotonic() - Started
    MaximumRuntimeSeconds = float(ReplaySlice["MaximumRuntimeSeconds"])
    Status = (
        MandatoryAccessReplayStatus.Conflict
        if Profile.HasConflicts
        else MandatoryAccessReplayStatus.Clean
    )
    Passed = all((
        SourceArtifactMatched,
        SourceCandidateMatched,
        ExpectedProfileMatched,
        RepeatedProfileMatched,
        FixedPlacementSolveMatched,
        UnsatisfiableCoreReplayed,
        Status is MandatoryAccessReplayStatus.Conflict,
        RuntimeSeconds <= MaximumRuntimeSeconds,
    ))
    return Cla4AccessReplayResult(
        Status=Status,
        Passed=Passed,
        RuntimeSeconds=RuntimeSeconds,
        MaximumRuntimeSeconds=MaximumRuntimeSeconds,
        SourceArtifactMatched=SourceArtifactMatched,
        SourceCandidateMatched=SourceCandidateMatched,
        ExpectedProfileMatched=ExpectedProfileMatched,
        RepeatedProfileMatched=RepeatedProfileMatched,
        FixedPlacementSolveMatched=FixedPlacementSolveMatched,
        UnsatisfiableCoreReplayed=UnsatisfiableCoreReplayed,
        Profile=ProfileDictionary,
        FixedPlacementSolve=FixedPlacementSolve.ToDictionary(),
    )


def ParseArguments() -> argparse.Namespace:
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument(
        "--fixture",
        type=Path,
        default=DefaultFixturePath,
        help="versioned replay fixture",
    )
    return Parser.parse_args()


def Main() -> int:
    Options = ParseArguments()
    Result = RunCla4AccessReplay(Options.fixture.resolve())
    print(json.dumps(Result.ToDictionary(), indent=2, sort_keys=True))
    return 0 if Result.Passed else 1


if __name__ == "__main__":
    raise SystemExit(Main())

#!/usr/bin/env python3
"""Capture a reproducible routing-design source and evidence snapshot.

The capture is intentionally read-only until final publication into a fresh
timestamped directory. It records exact Git/source identities, summarizes one
explicit CLA4 routing-failure artifact without reclassifying structural
failure as timeout, copies only explicitly named evidence, and emits stable
JSON, Markdown, and SHA-256 manifests.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


RepositoryRoot = Path(__file__).resolve().parents[2]
GeneratorPath = Path(__file__).resolve()
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))
SchemaVersion = "routing-design-snapshot-v3"
SourceScopeVersion = "routing-implementation-source-v1"
RuntimeProvenanceVersion = "routing-runtime-provenance-v1"
AcceptanceManifestSchemaVersion = "router-acceptance-manifest-v2"
AcceptanceProfileSchemaVersion = "routing-design-acceptance-profile-v1"
DefaultOutputRoot = (
    RepositoryRoot
    / "Output/DesignSnapshots/RoutingAwarePlacementAccess"
)
LocalTimeZone = ZoneInfo("America/New_York")
LegacyAcceptanceCases = (
    {
        "Name": "FullAdder",
        "ExamplePath": "Assets/Examples/FullAdder.sv",
        "TopModule": "FullAdder",
        "HistoricalBaselineRequiredRuns": 5,
        "TruthTableRows": 8,
        "FabricCanaryCount": 8,
        "StandaloneRuntimeCeilingSeconds": 15.0,
        "StandaloneRoutingDeadlineSeconds": 13.0,
        "HistoricalRuntimeCeilingSeconds": 10.0,
        "HistoricalRoutingDeadlineSeconds": 8.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "RippleCarryAdder4",
        "ExamplePath": "Assets/Examples/RippleCarryAdder4.sv",
        "TopModule": "RippleCarryAdder4",
        "HistoricalBaselineRequiredRuns": 3,
        "TruthTableRows": 512,
        "FabricCanaryCount": 20,
        "StandaloneRuntimeCeilingSeconds": 25.0,
        "StandaloneRoutingDeadlineSeconds": 23.0,
        "HistoricalRuntimeCeilingSeconds": 25.0,
        "HistoricalRoutingDeadlineSeconds": 23.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "RippleCarryAdder8",
        "ExamplePath": "Assets/Examples/RippleCarryAdder8.sv",
        "TopModule": "RippleCarryAdder8",
        "HistoricalBaselineRequiredRuns": 3,
        "TruthTableRows": 131_072,
        "FabricCanaryCount": 36,
        "StandaloneRuntimeCeilingSeconds": 30.0,
        "StandaloneRoutingDeadlineSeconds": 28.0,
        "HistoricalRuntimeCeilingSeconds": 30.0,
        "HistoricalRoutingDeadlineSeconds": 28.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "CarryLookaheadAdder4",
        "ExamplePath": "Assets/Examples/CarryLookaheadAdder4.sv",
        "TopModule": "CarryLookaheadAdder4",
        "HistoricalBaselineRequiredRuns": 2,
        "TruthTableRows": 512,
        "FabricCanaryCount": 20,
        "StandaloneRuntimeCeilingSeconds": 120.0,
        "StandaloneRoutingDeadlineSeconds": 118.0,
        "HistoricalRuntimeCeilingSeconds": 120.0,
        "HistoricalRoutingDeadlineSeconds": 118.0,
        "NeedsExactInterfaceProof": True,
    },
)
ExpandedAcceptanceCases = (
    {
        "Name": "HalfAdder",
        "ExamplePath": "Assets/Examples/HalfAdder.sv",
        "TopModule": "HalfAdder",
        "HistoricalBaselineRequiredRuns": 3,
        "TruthTableRows": 4,
        "FabricCanaryCount": 4,
        "RuntimeCeilingSeconds": 10.0,
        "RoutingDeadlineSeconds": 8.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "FullAdder",
        "ExamplePath": "Assets/Examples/FullAdder.sv",
        "TopModule": "FullAdder",
        "HistoricalBaselineRequiredRuns": 5,
        "TruthTableRows": 8,
        "FabricCanaryCount": 8,
        "RuntimeCeilingSeconds": 15.0,
        "RoutingDeadlineSeconds": 13.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "RippleCarryAdder4",
        "ExamplePath": "Assets/Examples/RippleCarryAdder4.sv",
        "TopModule": "RippleCarryAdder4",
        "HistoricalBaselineRequiredRuns": 3,
        "TruthTableRows": 512,
        "FabricCanaryCount": 20,
        "RuntimeCeilingSeconds": 25.0,
        "RoutingDeadlineSeconds": 23.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "RippleCarryAdder8",
        "ExamplePath": "Assets/Examples/RippleCarryAdder8.sv",
        "TopModule": "RippleCarryAdder8",
        "HistoricalBaselineRequiredRuns": 3,
        "TruthTableRows": 131_072,
        "FabricCanaryCount": 36,
        "RuntimeCeilingSeconds": 30.0,
        "RoutingDeadlineSeconds": 28.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "DecimalToBinary4",
        "ExamplePath": "Assets/Examples/DecimalToBinary4.sv",
        "TopModule": "DecimalToBinary4",
        "HistoricalBaselineRequiredRuns": 3,
        "TruthTableRows": 1_024,
        "FabricCanaryCount": 22,
        "RuntimeCeilingSeconds": 30.0,
        "RoutingDeadlineSeconds": 28.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "TFlipFlopLatch",
        "ExamplePath": "Assets/Examples/TFlipFlopLatch.sv",
        "TopModule": "TFlipFlopLatch",
        "HistoricalBaselineRequiredRuns": 3,
        "TruthTableRows": 8,
        "FabricCanaryCount": 8,
        "RuntimeCeilingSeconds": 15.0,
        "RoutingDeadlineSeconds": 13.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "CarryLookaheadAdder4",
        "ExamplePath": "Assets/Examples/CarryLookaheadAdder4.sv",
        "TopModule": "CarryLookaheadAdder4",
        "HistoricalBaselineRequiredRuns": 2,
        "TruthTableRows": 512,
        "FabricCanaryCount": 20,
        "RuntimeCeilingSeconds": 120.0,
        "RoutingDeadlineSeconds": 118.0,
        "NeedsExactInterfaceProof": True,
    },
)
RoutingEnvironmentNames = (
    "PYTHONHASHSEED",
    "RAYON_NUM_THREADS",
    "OMP_NUM_THREADS",
    "RUST_BACKTRACE",
    "RUST_LOG",
)


@dataclass(frozen=True)
class SnapshotConfiguration:
    """Immutable inputs for one fresh, timestamped evidence capture."""

    RepositoryRoot: Path
    OutputRoot: Path
    CapturedAtUtc: datetime
    Cla4FailurePath: Path | None
    AcceptanceManifestPath: Path | None = None
    ArtifactPaths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class VerifiedFileObservation:
    """Exact bytes and fstat identity from one non-following file open."""

    Path: Path
    Data: bytes
    SizeBytes: int
    Sha256: str
    Device: int
    Inode: int

    def PublicRecord(self) -> dict[str, object]:
        return {
            "Path": str(self.Path),
            "SizeBytes": self.SizeBytes,
            "Sha256": self.Sha256,
        }


@dataclass(frozen=True)
class VerifiedArchiveEvidence:
    """One complete sealed archive observed below one retained root fd."""

    Root: Path
    AcceptanceManifestRelativePath: str
    Identity: dict[str, object]
    ObservationsByRelativePath: Mapping[str, VerifiedFileObservation]

    @property
    def AcceptanceManifest(self) -> VerifiedFileObservation:
        return self.ObservationsByRelativePath[
            self.AcceptanceManifestRelativePath
        ]

    def ObservationForPath(
        self,
        PathValue: Path,
    ) -> VerifiedFileObservation | None:
        RawPath = os.fspath(PathValue)
        RawParts = RawPath.split("/")
        if (
            any(Part in {".", ".."} for Part in RawParts)
            or any(Part == "" for Part in RawParts[1:])
        ):
            return None
        Absolute = Path(os.path.abspath(RawPath))
        if not Absolute.is_relative_to(self.Root):
            return None
        Relative = Absolute.relative_to(self.Root).as_posix()
        return self.ObservationsByRelativePath.get(Relative)


class RoutingDesignSnapshot(dict[str, object]):
    """JSON-compatible snapshot with process-local captured artifact bytes."""

    ArtifactObservationsBySnapshotPath: Mapping[
        str,
        VerifiedFileObservation,
    ]

    def __init__(
        self,
        Values: Mapping[str, object],
        *,
        ArtifactObservationsBySnapshotPath: Mapping[
            str,
            VerifiedFileObservation,
        ],
    ) -> None:
        super().__init__(Values)
        self.ArtifactObservationsBySnapshotPath = dict(
            ArtifactObservationsBySnapshotPath
        )


def CanonicalJsonBytes(Value: object) -> bytes:
    """Encode portable evidence with sorted keys and fixed separators."""
    return json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def PrettyJsonText(Value: object) -> str:
    """Encode one stable human-readable JSON document."""
    return json.dumps(
        Value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"


def Sha256Bytes(Value: bytes) -> str:
    """Return the hexadecimal SHA-256 digest of exact bytes."""
    return sha256(Value).hexdigest()


def IsCanonicalSha256(Value: object) -> bool:
    """Return whether `Value` is one lowercase hexadecimal SHA-256."""
    return (
        isinstance(Value, str)
        and len(Value) == 64
        and all(Character in "0123456789abcdef" for Character in Value)
    )


def ExactJsonValueMatches(Actual: object, Expected: object) -> bool:
    """Compare JSON scalars without Python's bool/integer equivalence."""
    return type(Actual) is type(Expected) and Actual == Expected


def ReadSingleCommandValue(
    Command: object,
    Name: str,
) -> str | None:
    """Read exactly one string-valued command option without inference."""
    if (
        not isinstance(Command, list)
        or not all(isinstance(Value, str) for Value in Command)
    ):
        return None
    Values = [
        Command[Index + 1]
        for Index, Value in enumerate(Command[:-1])
        if Value == Name
    ]
    return Values[0] if len(Values) == 1 else None


def BuildProfileCase(
    Case: dict[str, object],
    *,
    BaselineMode: str | None,
    ProfileId: str,
) -> dict[str, object]:
    """Build one exporter-owned literal interpretation of a producer case."""
    HistoricalRuns = int(Case["HistoricalBaselineRequiredRuns"])
    if ProfileId == "legacy-four":
        Prefix = "Standalone" if BaselineMode is None else "Historical"
        RuntimeCeilingSeconds = float(
            Case[f"{Prefix}RuntimeCeilingSeconds"]
        )
        RoutingDeadlineSeconds = float(
            Case[f"{Prefix}RoutingDeadlineSeconds"]
        )
    else:
        RuntimeCeilingSeconds = float(Case["RuntimeCeilingSeconds"])
        RoutingDeadlineSeconds = float(Case["RoutingDeadlineSeconds"])
    RequiredRuns = 1 if BaselineMode is None else HistoricalRuns
    return {
        "Name": Case["Name"],
        "ExamplePath": Case["ExamplePath"],
        "TopModule": Case["TopModule"],
        "RequiredRuns": RequiredRuns,
        "HistoricalBaselineRequiredRuns": HistoricalRuns,
        "MchprsTruthTableRows": Case["TruthTableRows"],
        "FabricCanaryCount": Case["FabricCanaryCount"],
        "RuntimeCeilingSeconds": RuntimeCeilingSeconds,
        "PublicationReserveSeconds": 2.0,
        "RoutingDeadlineSeconds": RoutingDeadlineSeconds,
        "MaximumOverflowPeak": 1,
        "NeedsExactInterfaceProof": Case["NeedsExactInterfaceProof"],
    }


def ExpectedManifestCase(
    ProfileCase: dict[str, object],
    *,
    BaselineMode: str | None,
) -> dict[str, object]:
    """Return the exact fields that the existing producer must have emitted."""
    Result = {
        "Name": ProfileCase["Name"],
        "ExamplePath": ProfileCase["ExamplePath"],
        "TopModule": ProfileCase["TopModule"],
        "RequiredRuns": ProfileCase["RequiredRuns"],
        "TruthTableRows": ProfileCase["MchprsTruthTableRows"],
        "RuntimeCeilingSeconds": ProfileCase["RuntimeCeilingSeconds"],
        "MaximumOverflowPeak": ProfileCase["MaximumOverflowPeak"],
        "NeedsExactInterfaceProof": ProfileCase[
            "NeedsExactInterfaceProof"
        ],
        "PublicationReserveSeconds": ProfileCase[
            "PublicationReserveSeconds"
        ],
        "RoutingDeadlineSeconds": ProfileCase["RoutingDeadlineSeconds"],
    }
    if BaselineMode is None:
        Result["HistoricalBaselineRequiredRuns"] = ProfileCase[
            "HistoricalBaselineRequiredRuns"
        ]
    return Result


def SelectAcceptanceProfile(
    Payload: dict[str, object],
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Infer and validate one exact exporter-owned manifest interpretation."""
    if "AcceptanceProfile" in Payload or "ProfileId" in Payload:
        raise ValueError("acceptance profile is exporter-selected, not caller-supplied")
    MatrixMode = Payload.get("MatrixMode")
    BaselineMode = Payload.get("BaselineMode")
    if Payload.get("ExecutionMode") != "sequential":
        raise ValueError("acceptance manifest ExecutionMode is not sequential")
    if Payload.get("FailFast") is not False:
        raise ValueError("acceptance manifest FailFast is not exact false")
    if BaselineMode not in {None, "capture", "compare"}:
        raise ValueError("acceptance manifest BaselineMode is invalid")
    Cases = Payload.get("Cases")
    if not isinstance(Cases, list):
        raise ValueError("acceptance manifest Cases must be a list")
    CaseNames: list[str] = []
    for Case in Cases:
        if not isinstance(Case, dict) or not isinstance(Case.get("Name"), str):
            raise ValueError("acceptance manifest case must be a named object")
        CaseNames.append(str(Case["Name"]))
    if len(CaseNames) != len(set(CaseNames)):
        raise ValueError("acceptance manifest contains a duplicate case")

    LegacyNames = {str(Case["Name"]) for Case in LegacyAcceptanceCases}
    ExpandedNames = {str(Case["Name"]) for Case in ExpandedAcceptanceCases}
    if set(CaseNames) == LegacyNames:
        if MatrixMode != "default":
            raise ValueError("legacy-four requires MatrixMode default")
        ProfileId = "legacy-four"
        LiteralCases = LegacyAcceptanceCases
    elif set(CaseNames) == ExpandedNames:
        if MatrixMode != "expanded":
            raise ValueError("expanded-seven requires MatrixMode expanded")
        if BaselineMode is not None:
            raise ValueError("expanded-seven requires BaselineMode null")
        ProfileId = "expanded-seven"
        LiteralCases = ExpandedAcceptanceCases
    else:
        raise ValueError("acceptance manifest case set is not authoritative")

    CaseTable = tuple(
        BuildProfileCase(
            dict(Case),
            BaselineMode=BaselineMode,
            ProfileId=ProfileId,
        )
        for Case in LiteralCases
    )
    Authority = {
        "ProfileSchemaVersion": AcceptanceProfileSchemaVersion,
        "ProfileId": ProfileId,
        "CaseCount": len(CaseTable),
        "CaseTable": [dict(Case) for Case in CaseTable],
        "MatrixMode": MatrixMode,
        "BaselineMode": BaselineMode,
    }
    Authority["ProfileSha256"] = Sha256Bytes(CanonicalJsonBytes(Authority))
    return Authority, CaseTable


def ValidateCaseMatrix(
    Payload: dict[str, object],
    CaseTable: tuple[dict[str, object], ...],
) -> list[dict[str, object]]:
    """Validate exact existing producer metadata and normalize profile order."""
    RawCases = Payload.get("Cases")
    assert isinstance(RawCases, list)
    CasesByName = {str(Case["Name"]): Case for Case in RawCases}
    CaseMatrix: list[dict[str, object]] = []
    for ProfileCase in CaseTable:
        Expected = ExpectedManifestCase(
            ProfileCase,
            BaselineMode=Payload.get("BaselineMode"),
        )
        Case = CasesByName[str(ProfileCase["Name"])]
        for Key, ExpectedValue in Expected.items():
            if not ExactJsonValueMatches(Case.get(Key), ExpectedValue):
                raise ValueError(
                    "acceptance manifest case matrix mismatch for "
                    f"{ProfileCase['Name']}.{Key}: "
                    f"{Case.get(Key)!r} != {ExpectedValue!r}"
                )
        CaseMatrix.append({Key: Case[Key] for Key in Expected})
    return CaseMatrix


def ValidateRunCommand(
    Run: dict[str, object],
    ProfileCase: dict[str, object],
    RequestedStrategy: str,
    ProducerRoot: Path,
) -> None:
    """Require one exact input/top/deadline/strategy command interpretation."""
    Command = Run.get("Command")
    Input = ReadSingleCommandValue(Command, "--input")
    TopModule = ReadSingleCommandValue(Command, "--topmodule")
    Strategy = ReadSingleCommandValue(Command, "--routing-strategy")
    Deadline = ReadSingleCommandValue(
        Command,
        "--routing-deadline-seconds",
    )
    if not isinstance(Command, list) or len(Command) < 2:
        raise ValueError("acceptance run command is incomplete")
    Entrypoint = Path(str(Command[1]))
    ExpectedEntrypoint = ProducerRoot / "Main.py"
    if Entrypoint != ExpectedEntrypoint:
        raise ValueError("acceptance run command entrypoint is inconsistent")
    ExpectedInput = ProducerRoot / str(ProfileCase["ExamplePath"])
    if Input is None or Path(Input) != ExpectedInput:
        raise ValueError("acceptance run command input is inconsistent")
    if TopModule != ProfileCase["TopModule"]:
        raise ValueError("acceptance run command top module is inconsistent")
    if Strategy != RequestedStrategy:
        raise ValueError("acceptance run command strategy is inconsistent")
    try:
        ParsedDeadline = float(Deadline) if Deadline is not None else None
    except ValueError:
        ParsedDeadline = None
    if ParsedDeadline != float(ProfileCase["RoutingDeadlineSeconds"]):
        raise ValueError("acceptance run command deadline is inconsistent")


def ValidateAcceptanceRuns(
    Payload: dict[str, object],
    CaseTable: tuple[dict[str, object], ...],
    RequestedStrategy: str,
    ProducerRoot: Path,
) -> list[dict[str, object]]:
    """Validate every exact run occurrence and return deterministic order."""
    RawRuns = Payload.get("Runs")
    if not isinstance(RawRuns, list):
        raise ValueError("acceptance manifest Runs must be a list")
    if not all(isinstance(Run, dict) for Run in RawRuns):
        raise ValueError("acceptance manifest run must be an object")
    ExpectedOccurrences: list[tuple[str, int, bool, int, str]] = []
    Sequence = 0
    for ProfileCase in CaseTable:
        CaseName = str(ProfileCase["Name"])
        if Payload.get("BaselineMode") is not None and CaseName == "FullAdder":
            Sequence += 1
            ExpectedOccurrences.append(
                (CaseName, 0, True, Sequence, f"{CaseName}Warmup")
            )
        for Repetition in range(1, int(ProfileCase["RequiredRuns"]) + 1):
            Sequence += 1
            ExpectedOccurrences.append((
                CaseName,
                Repetition,
                False,
                Sequence,
                f"{CaseName}Run{Repetition}",
            ))
    RunsByOccurrence: dict[tuple[str, int, bool], dict[str, object]] = {}
    RunNames: set[str] = set()
    CaseByName = {str(Case["Name"]): Case for Case in CaseTable}
    for Run in RawRuns:
        Circuit = Run.get("Circuit")
        Repetition = Run.get("Repetition")
        Warmup = Run.get("Warmup")
        if (
            not isinstance(Circuit, str)
            or not isinstance(Repetition, int)
            or isinstance(Repetition, bool)
            or type(Warmup) is not bool
        ):
            raise ValueError("acceptance run occurrence is incomplete")
        Occurrence = (Circuit, Repetition, Warmup)
        RunName = Run.get("RunName")
        if Occurrence in RunsByOccurrence or RunName in RunNames:
            raise ValueError("acceptance manifest contains a duplicate run occurrence")
        RunsByOccurrence[Occurrence] = Run
        if not isinstance(RunName, str):
            raise ValueError("acceptance run has no exact RunName")
        RunNames.add(RunName)
    ExpectedKeys = {
        (Circuit, Repetition, Warmup)
        for Circuit, Repetition, Warmup, _Sequence, _RunName
        in ExpectedOccurrences
    }
    if set(RunsByOccurrence) != ExpectedKeys:
        raise ValueError("acceptance manifest run set is not authoritative")

    OrderedRuns: list[dict[str, object]] = []
    for Circuit, Repetition, Warmup, Sequence, RunName in ExpectedOccurrences:
        Run = RunsByOccurrence[(Circuit, Repetition, Warmup)]
        ProfileCase = CaseByName[Circuit]
        if (
            Run.get("RunName") != RunName
            or not ExactJsonValueMatches(Run.get("Sequence"), Sequence)
        ):
            raise ValueError("acceptance run identity or sequence is inconsistent")
        if not isinstance(Run.get("Status"), str) or not Run["Status"]:
            raise ValueError("acceptance run Status is not an exact string")
        if type(Run.get("Accepted")) is not bool:
            raise ValueError("acceptance run Accepted is not an exact boolean")
        ExpectedRequirements = ExpectedManifestCase(
            ProfileCase,
            BaselineMode=Payload.get("BaselineMode"),
        )
        Requirements = Run.get("Requirements")
        if not isinstance(Requirements, dict):
            raise ValueError("acceptance run Requirements is not an object")
        for Key, ExpectedValue in ExpectedRequirements.items():
            if not ExactJsonValueMatches(
                Requirements.get(Key),
                ExpectedValue,
            ):
                raise ValueError(
                    f"acceptance run requirement mismatch: {RunName}.{Key}"
                )
        if (
            not ExactJsonValueMatches(
                Run.get("RequestedRoutingDeadlineSeconds"),
                ProfileCase["RoutingDeadlineSeconds"],
            )
            or not ExactJsonValueMatches(
                Run.get("PublicationReserveSeconds"),
                ProfileCase["PublicationReserveSeconds"],
            )
        ):
            raise ValueError("acceptance run deadline envelope is inconsistent")
        ValidateRunCommand(
            Run,
            ProfileCase,
            RequestedStrategy,
            ProducerRoot,
        )
        Evaluation = Run.get("Evaluation")
        if Evaluation is not None:
            if not isinstance(Evaluation, dict):
                raise ValueError("acceptance run Evaluation is not an object")
            if type(Evaluation.get("Accepted")) is not bool:
                raise ValueError(
                    "acceptance evaluation Accepted is not an exact boolean"
                )
            if Evaluation["Accepted"] is not Run["Accepted"]:
                raise ValueError("acceptance run and evaluation verdicts disagree")
        OrderedRuns.append(Run)
    return OrderedRuns


def BuildPolicySnapshotIdentity(
    Snapshot: dict[str, object],
) -> dict[str, object]:
    """Identify every field of one observed routing policy snapshot."""
    CanonicalSnapshot = json.loads(json.dumps(
        Snapshot,
        sort_keys=True,
        separators=(",", ":"),
    ))
    return {
        "PolicyVersion": CanonicalSnapshot.get("PolicyVersion"),
        "Seed": CanonicalSnapshot.get("Seed"),
        "Sha256": Sha256Bytes(CanonicalJsonBytes(CanonicalSnapshot)),
        "Snapshot": CanonicalSnapshot,
    }


def ReadCommandRoutingStrategy(Command: object) -> str | None:
    """Read one exact routing-strategy command value without inference."""
    if not isinstance(Command, list):
        return None
    Values = [
        str(Command[Index + 1])
        for Index, Value in enumerate(Command[:-1])
        if Value == "--routing-strategy"
    ]
    return Values[0] if len(Values) == 1 else None


def ReadCommandRoutingDeadline(Command: object) -> float | None:
    """Read one finite positive routing deadline without inference."""
    if not isinstance(Command, list):
        return None
    Values = [
        Command[Index + 1]
        for Index, Value in enumerate(Command[:-1])
        if Value == "--routing-deadline-seconds"
    ]
    if len(Values) != 1:
        return None
    try:
        Deadline = float(Values[0])
    except (TypeError, ValueError):
        return None
    if not 0.0 < Deadline < float("inf"):
        return None
    return Deadline


def BuildEffectiveRoutingPolicyIdentity(
    CanonicalPolicySnapshot: dict[str, object],
    RoutingDeadlineSeconds: float,
) -> dict[str, object]:
    """Apply exactly the two production deadline overrides to source policy."""
    Snapshot = deepcopy(CanonicalPolicySnapshot)
    AdaptiveRouting = Snapshot.get("AdaptiveRouting")
    if not isinstance(AdaptiveRouting, dict):
        raise ValueError("canonical policy has no AdaptiveRouting object")
    Snapshot["RuntimeBudgetSeconds"] = RoutingDeadlineSeconds
    AdaptiveRouting["MaximumRuntimeSeconds"] = RoutingDeadlineSeconds
    return BuildPolicySnapshotIdentity(Snapshot)


def ValidatedRunRoutingDeadline(Run: dict[str, object]) -> float | None:
    """Cross-check run, case, command, and any process deadline evidence."""
    Requirements = Run.get("Requirements")
    if not isinstance(Requirements, dict):
        return None
    Values = (
        Run.get("RequestedRoutingDeadlineSeconds"),
        Requirements.get("RoutingDeadlineSeconds"),
        ReadCommandRoutingDeadline(Run.get("Command")),
    )
    if any(
        not isinstance(Value, (int, float)) or isinstance(Value, bool)
        for Value in Values
    ):
        return None
    Deadline = float(Values[0])
    if not 0.0 < Deadline < float("inf") or any(
        float(Value) != Deadline for Value in Values[1:]
    ):
        return None
    Evaluation = Run.get("Evaluation")
    Process = (
        Evaluation.get("Process")
        if isinstance(Evaluation, dict)
        else None
    )
    if isinstance(Process, dict):
        ProcessDeadline = Process.get("RequestedRoutingDeadlineSeconds")
        if ProcessDeadline is not None and (
            not isinstance(ProcessDeadline, (int, float))
            or isinstance(ProcessDeadline, bool)
            or float(ProcessDeadline) != Deadline
        ):
            return None
    return Deadline


def Sha256File(InputPath: Path) -> str:
    """Return a streaming SHA-256 digest without changing the input file."""
    Digest = sha256()
    with InputPath.open("rb") as InputFile:
        for Chunk in iter(lambda: InputFile.read(1024 * 1024), b""):
            Digest.update(Chunk)
    return Digest.hexdigest()


def SafeArchiveOpenPrimitivesAvailable() -> bool:
    """Require fd-relative non-following reads with no unsafe fallback."""
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_NONBLOCK")
        and os.open in getattr(os, "supports_dir_fd", frozenset())
    )


def _ValidatedRelativeParts(RelativePath: str) -> tuple[str, ...]:
    """Return an exact portable relative path or reject lexical ambiguity."""
    Parts = tuple(RelativePath.split("/"))
    if (
        not RelativePath
        or RelativePath.startswith("/")
        or any(Part in {"", ".", ".."} or "\\" in Part for Part in Parts)
        or "/".join(Parts) != RelativePath
    ):
        raise ValueError("archive evidence path is not a normalized relative path")
    return Parts


def _OpenAbsoluteDirectoryWithoutFollowing(Value: Path) -> list[int]:
    """Open an absolute directory through non-following component handles."""
    if not SafeArchiveOpenPrimitivesAvailable():
        raise ValueError("safe archive open primitives are unavailable")
    Absolute = Path(os.path.abspath(os.fspath(Value)))
    Anchor = Path(Absolute.anchor)
    Parts = Absolute.parts[1:] if Absolute.anchor else Absolute.parts
    DirectoryFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    Descriptors: list[int] = []
    try:
        Current = os.open(Anchor, DirectoryFlags)
        Descriptors.append(Current)
        for Part in Parts:
            Current = os.open(Part, DirectoryFlags, dir_fd=Current)
            Descriptors.append(Current)
        return Descriptors
    except OSError as Error:
        for Descriptor in reversed(Descriptors):
            os.close(Descriptor)
        raise ValueError(
            "archive root has a missing, changed, or symlink directory"
        ) from Error


def _ReadVerifiedFileAtRoot(
    Root: Path,
    RootDescriptor: int,
    RelativePath: str,
    *,
    MissingAllowed: bool = False,
) -> VerifiedFileObservation | None:
    """Read one normalized member below an already-open archive root."""
    Parts = _ValidatedRelativeParts(RelativePath)
    DirectoryFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    FileFlags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    DirectoryDescriptors: list[int] = []
    Descriptor: int | None = None
    Current = RootDescriptor
    try:
        for Part in Parts[:-1]:
            try:
                Current = os.open(
                    Part,
                    DirectoryFlags,
                    dir_fd=Current,
                )
            except OSError as Error:
                if MissingAllowed and isinstance(Error, FileNotFoundError):
                    return None
                raise ValueError(
                    "archive evidence parent is missing, changed, or a symlink"
                ) from Error
            DirectoryDescriptors.append(Current)
        try:
            Descriptor = os.open(
                Parts[-1],
                FileFlags,
                dir_fd=Current,
            )
        except OSError as Error:
            if MissingAllowed and isinstance(Error, FileNotFoundError):
                return None
            raise ValueError(
                "archive evidence is missing, changed, or a symlink"
            ) from Error
        DescriptorStat = os.fstat(Descriptor)
        if not stat.S_ISREG(DescriptorStat.st_mode):
            raise ValueError("archive evidence is not a regular file")
        Chunks: list[bytes] = []
        while True:
            Chunk = os.read(Descriptor, 1024 * 1024)
            if not Chunk:
                break
            Chunks.append(Chunk)
        Data = b"".join(Chunks)
        if len(Data) != DescriptorStat.st_size:
            raise ValueError("archive evidence size changed while reading")
        return VerifiedFileObservation(
            Path=Root.joinpath(*Parts),
            Data=Data,
            SizeBytes=DescriptorStat.st_size,
            Sha256=Sha256Bytes(Data),
            Device=DescriptorStat.st_dev,
            Inode=DescriptorStat.st_ino,
        )
    finally:
        if Descriptor is not None:
            os.close(Descriptor)
        for DirectoryDescriptor in reversed(DirectoryDescriptors):
            os.close(DirectoryDescriptor)


def ObserveExplicitFile(InputPath: Path) -> VerifiedFileObservation:
    """Capture one explicit regular file with no symlink traversal."""
    Observation, _SiblingNames = _ObserveExplicitFileAndSiblingNames(
        InputPath
    )
    return Observation


def _ObserveExplicitFileAndSiblingNames(
    InputPath: Path,
) -> tuple[VerifiedFileObservation, frozenset[str]]:
    """Capture one file and its directory names below the same parent fd."""
    Absolute = Path(os.path.abspath(os.fspath(InputPath)))
    DirectoryDescriptors = _OpenAbsoluteDirectoryWithoutFollowing(
        Absolute.parent
    )
    try:
        Observation = _ReadVerifiedFileAtRoot(
            Absolute.parent,
            DirectoryDescriptors[-1],
            Absolute.name,
        )
        assert Observation is not None
        try:
            SiblingNames = frozenset(os.listdir(DirectoryDescriptors[-1]))
        except OSError as Error:
            raise ValueError(
                "explicit artifact parent changed while observing siblings"
            ) from Error
        return Observation, SiblingNames
    finally:
        for Descriptor in reversed(DirectoryDescriptors):
            os.close(Descriptor)


def ReadRegularArchiveBytes(
    ArchiveRoot: Path,
    InputPath: Path,
) -> bytes:
    """Read exact in-archive regular bytes without following symlinks."""
    Root = Path(os.path.abspath(ArchiveRoot))
    if not InputPath.is_absolute():
        raise ValueError("archive evidence path must be absolute")
    Candidate = Path(os.path.abspath(InputPath))
    if not Candidate.is_relative_to(Root):
        raise ValueError("archive evidence path escapes archive root")
    Relative = Candidate.relative_to(Root)
    if not Relative.parts:
        raise ValueError("archive evidence path names the archive root")
    RelativePath = Relative.as_posix()
    DirectoryDescriptors = _OpenAbsoluteDirectoryWithoutFollowing(Root)
    try:
        Observation = _ReadVerifiedFileAtRoot(
            Root,
            DirectoryDescriptors[-1],
            RelativePath,
        )
        assert Observation is not None
        return Observation.Data
    finally:
        for DirectoryDescriptor in reversed(DirectoryDescriptors):
            os.close(DirectoryDescriptor)


def BuildEvaluatorArtifactProjection(
    Evaluation: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Preserve every evaluator artifact presence, type, size, and digest."""
    RawArtifacts = Evaluation.get("Artifacts")
    if RawArtifacts is None:
        return {}
    if not isinstance(RawArtifacts, dict):
        raise ValueError("acceptance evaluation Artifacts is not an object")
    Result: dict[str, dict[str, object]] = {}
    for Name, RawRecord in sorted(RawArtifacts.items()):
        if not isinstance(Name, str) or not isinstance(RawRecord, dict):
            raise ValueError("acceptance evaluator artifact is malformed")
        Exists = RawRecord.get("Exists")
        PathValue = RawRecord.get("Path")
        if type(Exists) is not bool or not isinstance(PathValue, str):
            raise ValueError("acceptance evaluator artifact identity is incomplete")
        Record = deepcopy(RawRecord)
        if Exists:
            if (
                not isinstance(Record.get("SizeBytes"), int)
                or isinstance(Record.get("SizeBytes"), bool)
                or int(Record["SizeBytes"]) < 0
                or not IsCanonicalSha256(Record.get("Sha256"))
            ):
                raise ValueError(
                    "existing acceptance evaluator artifact has no size/hash"
                )
            if Record.get("IsSymlink") not in (None, False):
                raise ValueError("existing acceptance evaluator artifact is a symlink")
            if Record.get("EntryType") not in (None, "file"):
                raise ValueError(
                    "existing acceptance evaluator artifact is not a file"
                )
            Record.setdefault("EntryType", "file")
        else:
            EntryType = Record.get("EntryType")
            if EntryType is not None and not isinstance(EntryType, str):
                raise ValueError("acceptance evaluator artifact type is invalid")
            Record.setdefault("EntryType", None)
        Result[Name] = Record
    return Result


def ReadRunFailureArtifact(
    Run: dict[str, object],
    *,
    ProfileCase: dict[str, object],
    ManifestSourceState: dict[str, object],
    ArchiveEvidence: VerifiedArchiveEvidence | None = None,
) -> dict[str, object] | None:
    """Verify only the evaluator-selected typed failure for one occurrence."""
    Evaluation = Run.get("Evaluation")
    if not isinstance(Evaluation, dict):
        return None
    Artifacts = BuildEvaluatorArtifactProjection(Evaluation)
    FailureRecord = Artifacts.get("RoutingFailure")
    Observed = Evaluation.get("Observed")
    Observed = Observed if isinstance(Observed, dict) else {}
    Resolution = Observed.get("FailureArtifactResolution")
    FailureExists = (
        isinstance(FailureRecord, dict)
        and FailureRecord.get("Exists") is True
    )
    if Resolution is None and not FailureExists:
        return None
    if not isinstance(Resolution, dict) or not FailureExists:
        raise ValueError(
            "typed failure requires evaluator resolution and artifact record"
        )
    if ArchiveEvidence is None:
        raise ValueError(
            "selected failure requires a verified archive seal"
        )
    SelectedPath = Resolution.get("Path")
    if (
        not isinstance(SelectedPath, str)
        or SelectedPath != FailureRecord.get("Path")
    ):
        raise ValueError("failure resolution path and artifact record disagree")
    Observation = ArchiveEvidence.ObservationForPath(Path(SelectedPath))
    if Observation is None:
        raise ValueError(
            "selected failure artifact is not bound to archive seal"
        )
    Data = Observation.Data
    if (
        Observation.SizeBytes != FailureRecord.get("SizeBytes")
        or Observation.Sha256 != FailureRecord.get("Sha256")
    ):
        raise ValueError("selected failure artifact size or digest mismatch")
    RelativePath = Observation.Path.relative_to(ArchiveEvidence.Root).as_posix()
    SealedFiles = {
        str(Record["Path"]): Record
        for Record in ArchiveEvidence.Identity["Files"]
    }
    SealedRecord = SealedFiles.get(RelativePath)
    if SealedRecord is None or (
        SealedRecord.get("SizeBytes") != Observation.SizeBytes
        or SealedRecord.get("Sha256") != Observation.Sha256
    ):
        raise ValueError(
            "selected failure artifact is not bound to archive seal"
        )
    try:
        Payload = json.loads(Data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as Error:
        raise ValueError("selected failure artifact is not valid JSON") from Error
    if not isinstance(Payload, dict) or Payload.get("SchemaVersion") != (
        "routing-failure-v1"
    ):
        raise ValueError("selected failure artifact has the wrong schema")
    Failure = Payload.get("Failure")
    Reproduction = Payload.get("Reproduction")
    if not isinstance(Failure, dict) or not isinstance(Reproduction, dict):
        raise ValueError("selected failure artifact is incomplete")
    if (
        not isinstance(Failure.get("Stage"), str)
        or not Failure["Stage"]
        or not isinstance(Failure.get("Reason"), str)
        or not Failure["Reason"]
    ):
        raise ValueError("selected failure stage/reason is incomplete")
    if (
        Payload.get("SourceState") != ManifestSourceState
        or Reproduction.get("TopModule") != ProfileCase["TopModule"]
        or Reproduction.get("Command") != Run.get("Command")
    ):
        raise ValueError("selected failure artifact provenance is inconsistent")
    ReproductionInput = Reproduction.get("Input")
    if (
        not isinstance(ReproductionInput, dict)
        or ReproductionInput.get("Path")
        != ReadSingleCommandValue(Run.get("Command"), "--input")
        or not IsCanonicalSha256(ReproductionInput.get("Sha256"))
        or not isinstance(ReproductionInput.get("SizeBytes"), int)
        or isinstance(ReproductionInput.get("SizeBytes"), bool)
    ):
        raise ValueError("selected failure input provenance is inconsistent")
    return {
        "Resolution": deepcopy(Resolution),
        "Artifact": FailureRecord,
        "Stage": Failure.get("Stage"),
        "Reason": Failure.get("Reason"),
    }


def BuildSealedArchiveEvidence(
    AcceptanceManifestPath: Path,
) -> VerifiedArchiveEvidence:
    """Observe and validate a complete seal below one retained archive root."""
    ArchiveRoot = Path(os.path.abspath(AcceptanceManifestPath.parent))
    AcceptanceRelativePath = AcceptanceManifestPath.name
    RootDescriptors = _OpenAbsoluteDirectoryWithoutFollowing(ArchiveRoot)
    try:
        RootDescriptor = RootDescriptors[-1]
        AcceptanceObservation = _ReadVerifiedFileAtRoot(
            ArchiveRoot,
            RootDescriptor,
            AcceptanceRelativePath,
        )
        assert AcceptanceObservation is not None
        try:
            json.loads(AcceptanceObservation.Data.decode("utf-8"))
        except UnicodeError as Error:
            raise ValueError("acceptance manifest is not UTF-8 JSON") from Error
        except json.JSONDecodeError:
            raise
        ArchiveObservation = _ReadVerifiedFileAtRoot(
            ArchiveRoot,
            RootDescriptor,
            "ArchiveManifest.json",
            MissingAllowed=True,
        )
        ChecksumsObservation = _ReadVerifiedFileAtRoot(
            ArchiveRoot,
            RootDescriptor,
            "SHA256SUMS",
            MissingAllowed=True,
        )
        if ArchiveObservation is None and ChecksumsObservation is None:
            raise ValueError(
                "acceptance manifest requires a verified archive seal"
            )
        if ArchiveObservation is None or ChecksumsObservation is None:
            raise ValueError("acceptance archive seal is incomplete")
        try:
            Archive = json.loads(ArchiveObservation.Data.decode("utf-8"))
            ChecksumText = ChecksumsObservation.Data.decode("utf-8")
        except (UnicodeError, json.JSONDecodeError) as Error:
            raise ValueError("acceptance archive seal is malformed") from Error
        if (
            not isinstance(Archive, dict)
            or Archive.get("SchemaVersion") != "router-benchmark-archive-v1"
            or Archive.get("Publication")
            != {"Complete": True, "Failure": None, "Status": "SEALED"}
        ):
            raise ValueError("acceptance archive is not sealed")
        RawFiles = Archive.get("Files")
        if not isinstance(RawFiles, list):
            raise ValueError("acceptance archive file inventory is missing")
        FilesByPath: dict[str, dict[str, object]] = {}
        for Record in RawFiles:
            if (
                not isinstance(Record, dict)
                or not isinstance(Record.get("Path"), str)
            ):
                raise ValueError(
                    "acceptance archive file inventory is malformed"
                )
            RelativePath = str(Record["Path"])
            try:
                _ValidatedRelativeParts(RelativePath)
            except ValueError as Error:
                raise ValueError(
                    "acceptance archive file identity is invalid"
                ) from Error
            if (
                RelativePath in FilesByPath
                or not isinstance(Record.get("SizeBytes"), int)
                or isinstance(Record.get("SizeBytes"), bool)
                or not IsCanonicalSha256(Record.get("Sha256"))
            ):
                raise ValueError(
                    "acceptance archive file identity is invalid"
                )
            FilesByPath[RelativePath] = Record
        Checksums: dict[str, str] = {}
        for Line in ChecksumText.splitlines():
            Digest, Separator, RelativePath = Line.partition("  ")
            try:
                _ValidatedRelativeParts(RelativePath)
            except ValueError as Error:
                raise ValueError(
                    "acceptance archive checksum record is invalid"
                ) from Error
            if (
                Separator != "  "
                or not IsCanonicalSha256(Digest)
                or RelativePath in Checksums
            ):
                raise ValueError(
                    "acceptance archive checksum record is invalid"
                )
            Checksums[RelativePath] = Digest
        if set(Checksums) != {*FilesByPath, "ArchiveManifest.json"}:
            raise ValueError(
                "acceptance archive checksum inventory is inconsistent"
            )
        if Checksums["ArchiveManifest.json"] != ArchiveObservation.Sha256:
            raise ValueError(
                "acceptance archive manifest digest is inconsistent"
            )
        Observations: dict[str, VerifiedFileObservation] = {
            "ArchiveManifest.json": ArchiveObservation,
            "SHA256SUMS": ChecksumsObservation,
        }
        for RelativePath, Digest in Checksums.items():
            if RelativePath == "ArchiveManifest.json":
                Observation = ArchiveObservation
            elif RelativePath == AcceptanceRelativePath:
                Observation = AcceptanceObservation
            else:
                Observation = _ReadVerifiedFileAtRoot(
                    ArchiveRoot,
                    RootDescriptor,
                    RelativePath,
                )
                assert Observation is not None
            if Observation.Sha256 != Digest:
                raise ValueError(
                    f"acceptance archive checksum mismatch: {RelativePath}"
                )
            Record = FilesByPath.get(RelativePath)
            if Record is not None and (
                Record.get("SizeBytes") != Observation.SizeBytes
                or Record.get("Sha256") != Observation.Sha256
            ):
                raise ValueError(
                    f"acceptance archive inventory mismatch: {RelativePath}"
                )
            Observations[RelativePath] = Observation
        AcceptanceRecord = FilesByPath.get(AcceptanceRelativePath)
        if AcceptanceRecord is None or (
            AcceptanceRecord.get("Sha256") != AcceptanceObservation.Sha256
            or AcceptanceRecord.get("SizeBytes")
            != AcceptanceObservation.SizeBytes
        ):
            raise ValueError(
                "acceptance manifest is not bound to archive seal"
            )
        Identity = {
            "ArchiveId": Archive.get("ArchiveId"),
            "SchemaVersion": Archive["SchemaVersion"],
            "ArchiveRoot": str(ArchiveRoot),
            "ArchiveManifest": ArchiveObservation.PublicRecord(),
            "SHA256SUMS": {
                **ChecksumsObservation.PublicRecord(),
                "EntryCount": len(Checksums),
            },
            "Publication": deepcopy(Archive["Publication"]),
            "Invocation": deepcopy(Archive.get("Invocation")),
            "Source": deepcopy(Archive.get("Source")),
            "Files": deepcopy(RawFiles),
        }
        return VerifiedArchiveEvidence(
            Root=ArchiveRoot,
            AcceptanceManifestRelativePath=AcceptanceRelativePath,
            Identity=Identity,
            ObservationsByRelativePath=Observations,
        )
    finally:
        for Descriptor in reversed(RootDescriptors):
            os.close(Descriptor)


def BuildSealedArchiveIdentity(
    AcceptanceManifestPath: Path,
) -> dict[str, object]:
    """Return the public identity from one descriptor-bound seal observation."""
    return BuildSealedArchiveEvidence(AcceptanceManifestPath).Identity


def RunGit(
    Root: Path,
    Arguments: Sequence[str],
) -> bytes:
    """Run one read-only Git query and return exact stdout bytes."""
    Result = subprocess.run(
        ("git", *Arguments),
        cwd=Root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return Result.stdout


def RelativeDisplayPath(PathValue: Path, Root: Path) -> str:
    """Return a repository-relative path when possible, else the basename."""
    try:
        return PathValue.resolve().relative_to(Root.resolve()).as_posix()
    except ValueError:
        return PathValue.name


def BuildSnapshotFileRecord(
    PathValue: Path,
    DisplayPath: str,
) -> dict[str, object]:
    """Return existence, byte size, and SHA-256 without mutating a file."""
    if not PathValue.exists():
        return {
            "Path": DisplayPath,
            "Exists": False,
        }
    if PathValue.is_symlink():
        LinkTarget = os.readlink(PathValue)
        LinkBytes = LinkTarget.encode("utf-8")
        return {
            "Path": DisplayPath,
            "Exists": True,
            "IsSymlink": True,
            "LinkTarget": LinkTarget,
            "SizeBytes": len(LinkBytes),
            "Sha256": Sha256Bytes(LinkBytes),
        }
    if not PathValue.is_file():
        return {
            "Path": DisplayPath,
            "Exists": True,
            "IsRegularFile": False,
        }
    return {
        "Path": DisplayPath,
        "Exists": True,
        "IsRegularFile": True,
        "SizeBytes": PathValue.stat().st_size,
        "Sha256": Sha256File(PathValue),
    }


def ParsePorcelainV1Z(StatusBytes: bytes) -> list[dict[str, object]]:
    """Parse `git status --porcelain=v1 -z` including rename origins."""
    Fields = StatusBytes.split(b"\0")
    Entries: list[dict[str, object]] = []
    FieldIndex = 0
    while FieldIndex < len(Fields):
        Field = Fields[FieldIndex]
        FieldIndex += 1
        if not Field:
            continue
        if len(Field) < 4 or Field[2:3] != b" ":
            raise ValueError(f"invalid porcelain-v1-z entry: {Field!r}")
        IndexStatus = chr(Field[0])
        WorktreeStatus = chr(Field[1])
        PathText = Field[3:].decode("utf-8", errors="surrogateescape")
        Entry: dict[str, object] = {
            "IndexStatus": IndexStatus,
            "WorktreeStatus": WorktreeStatus,
            "Path": PathText,
        }
        if IndexStatus in {"R", "C"} or WorktreeStatus in {"R", "C"}:
            if FieldIndex >= len(Fields) or not Fields[FieldIndex]:
                raise ValueError("rename/copy status is missing its origin")
            Entry["OriginalPath"] = Fields[FieldIndex].decode(
                "utf-8",
                errors="surrogateescape",
            )
            FieldIndex += 1
        Entries.append(Entry)
    return Entries


def ReadDetailedGitState(Root: Path) -> dict[str, object]:
    """Capture revision, branch, patches, and untracked state read-only."""
    Revision = RunGit(Root, ("rev-parse", "HEAD")).decode().strip()
    Branch = RunGit(Root, ("branch", "--show-current")).decode().strip()
    StatusBytes = RunGit(
        Root,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    )
    Entries = ParsePorcelainV1Z(StatusBytes)
    StagedPatch = RunGit(
        Root,
        (
            "diff",
            "--cached",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
        ),
    )
    UnstagedPatch = RunGit(
        Root,
        ("diff", "--binary", "--no-ext-diff", "--no-textconv"),
    )

    UntrackedFiles: list[dict[str, object]] = []
    for Entry in Entries:
        if (
            Entry["IndexStatus"] != "?"
            or Entry["WorktreeStatus"] != "?"
        ):
            continue
        RelativePath = Path(str(Entry["Path"]))
        UntrackedFiles.append(BuildSnapshotFileRecord(
            Root / RelativePath,
            RelativePath.as_posix(),
        ))
    UntrackedFiles.sort(key=lambda Value: str(Value["Path"]))
    UntrackedAggregate = Sha256Bytes(CanonicalJsonBytes(UntrackedFiles))

    return {
        "Revision": Revision,
        "Branch": Branch,
        "Dirty": bool(Entries),
        "StatusEntries": Entries,
        "StatusPorcelainBytes": len(StatusBytes),
        "StatusPorcelainSha256": Sha256Bytes(StatusBytes),
        "StagedPatchBytes": len(StagedPatch),
        "StagedPatchSha256": Sha256Bytes(StagedPatch),
        "UnstagedPatchBytes": len(UnstagedPatch),
        "UnstagedPatchSha256": Sha256Bytes(UnstagedPatch),
        "UntrackedTree": {
            "FileCount": len(UntrackedFiles),
            "AggregateSha256": UntrackedAggregate,
            "Files": UntrackedFiles,
        },
    }


def IsRoutingImplementationSource(RelativePath: str) -> bool:
    """Return whether a path belongs to the explicit implementation scope."""
    if RelativePath == "Main.py":
        return True
    if RelativePath.endswith(".py") and RelativePath.startswith((
        "App/",
        "Compilation/",
        "PhysicalDesign/",
        "RedstoneCompiler/",
        "Validation/",
    )):
        return True
    return (
        RelativePath.endswith(".rs")
        and RelativePath.startswith("Kernels/Routing/Src/")
    )


def IterPythonDefinitions(
    SourceText: str,
    RelativePath: str,
) -> Iterable[dict[str, object]]:
    """Yield deterministic Python AST definition spans with qualified names."""
    RootNode = ast.parse(SourceText, filename=RelativePath)

    def VisitBody(
        Body: Sequence[ast.stmt],
        Prefix: tuple[str, ...],
    ) -> Iterable[dict[str, object]]:
        for Node in Body:
            if isinstance(Node, ast.ClassDef):
                QualifiedName = ".".join((*Prefix, Node.name))
                EndLine = int(Node.end_lineno or Node.lineno)
                yield {
                    "Path": RelativePath,
                    "Kind": "Class",
                    "QualifiedName": QualifiedName,
                    "Line": Node.lineno,
                    "EndLine": EndLine,
                    "PythonAstSpanLines": EndLine - Node.lineno + 1,
                }
                yield from VisitBody(Node.body, (*Prefix, Node.name))
            elif isinstance(Node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                QualifiedName = ".".join((*Prefix, Node.name))
                EndLine = int(Node.end_lineno or Node.lineno)
                yield {
                    "Path": RelativePath,
                    "Kind": (
                        "AsyncFunction"
                        if isinstance(Node, ast.AsyncFunctionDef)
                        else "Function"
                    ),
                    "QualifiedName": QualifiedName,
                    "Line": Node.lineno,
                    "EndLine": EndLine,
                    "PythonAstSpanLines": EndLine - Node.lineno + 1,
                }
                yield from VisitBody(Node.body, (*Prefix, Node.name))

    yield from VisitBody(RootNode.body, ())


def BuildRoutingSourceManifest(Root: Path) -> dict[str, object]:
    """Hash and measure the explicitly versioned implementation-source scope."""
    InventoryBytes = RunGit(
        Root,
        ("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
    )
    Inventory = sorted({
        Value.decode("utf-8", errors="surrogateescape")
        for Value in InventoryBytes.split(b"\0")
        if Value
    })
    DeletedInventoryBytes = RunGit(
        Root,
        ("ls-files", "--deleted", "-z"),
    )
    DeletedPaths = frozenset(
        Value.decode("utf-8", errors="surrogateescape")
        for Value in DeletedInventoryBytes.split(b"\0")
        if Value
    )
    ResolvedRoot = Root.resolve()
    SourcePaths: list[str] = []
    for RelativePath in Inventory:
        if not IsRoutingImplementationSource(RelativePath):
            continue
        AbsolutePath = Root / RelativePath
        CurrentPath = Root
        for Part in Path(RelativePath).parts:
            CurrentPath = CurrentPath / Part
            if CurrentPath.is_symlink():
                raise ValueError(
                    "implementation-source symlink is not allowed: "
                    f"{RelativePath}"
                )
        if not AbsolutePath.exists():
            # A clean-break refactor can legitimately remove tracked source
            # before the audited commit is created.  Git's deleted set is
            # already captured in checkout provenance, while the source
            # manifest describes only implementation bytes that still exist.
            if RelativePath in DeletedPaths:
                continue
            raise FileNotFoundError(
                f"implementation source is missing: {RelativePath}"
            )
        if not AbsolutePath.is_file():
            raise ValueError(
                f"implementation source is not a regular file: {RelativePath}"
            )
        if not AbsolutePath.resolve().is_relative_to(ResolvedRoot):
            raise ValueError(
                f"implementation source escapes repository: {RelativePath}"
            )
        SourcePaths.append(RelativePath)

    FileRecords: list[dict[str, object]] = []
    Definitions: list[dict[str, object]] = []
    ByLanguage: dict[str, dict[str, int]] = {}
    for RelativePath in SourcePaths:
        AbsolutePath = Root / RelativePath
        SourceBytes = AbsolutePath.read_bytes()
        SourceText = SourceBytes.decode("utf-8")
        Lines = SourceText.splitlines()
        Language = "Python" if RelativePath.endswith(".py") else "Rust"
        Record = {
            "Path": RelativePath,
            "Language": Language,
            "SizeBytes": len(SourceBytes),
            "Sha256": Sha256Bytes(SourceBytes),
            "PhysicalLines": len(Lines),
            "NonBlankLines": sum(1 for Line in Lines if Line.strip()),
        }
        FileRecords.append(Record)
        LanguageTotals = ByLanguage.setdefault(Language, {
            "FileCount": 0,
            "SizeBytes": 0,
            "PhysicalLines": 0,
            "NonBlankLines": 0,
        })
        for Key in (
            "FileCount",
            "SizeBytes",
            "PhysicalLines",
            "NonBlankLines",
        ):
            LanguageTotals[Key] += (
                1 if Key == "FileCount" else int(Record[Key])
            )
        if Language == "Python":
            Definitions.extend(IterPythonDefinitions(
                SourceText,
                RelativePath,
            ))

    Totals = {
        "FileCount": len(FileRecords),
        "SizeBytes": sum(int(Value["SizeBytes"]) for Value in FileRecords),
        "PhysicalLines": sum(
            int(Value["PhysicalLines"]) for Value in FileRecords
        ),
        "NonBlankLines": sum(
            int(Value["NonBlankLines"]) for Value in FileRecords
        ),
        "PythonDefinitionCount": len(Definitions),
    }
    LargestFiles = sorted(
        FileRecords,
        key=lambda Value: (
            -int(Value["PhysicalLines"]),
            str(Value["Path"]),
        ),
    )[:20]
    LargestDefinitions = sorted(
        Definitions,
        key=lambda Value: (
            -int(Value["PythonAstSpanLines"]),
            str(Value["Path"]),
            int(Value["Line"]),
        ),
    )[:30]
    ContentIdentity = [
        {
            "Path": Value["Path"],
            "SizeBytes": Value["SizeBytes"],
            "Sha256": Value["Sha256"],
        }
        for Value in FileRecords
    ]
    return {
        "ScopeVersion": SourceScopeVersion,
        "AggregateSha256": Sha256Bytes(CanonicalJsonBytes(ContentIdentity)),
        "FileCount": len(FileRecords),
        "Files": FileRecords,
        "Metrics": {
            "Definitions": {
                "PhysicalLines": "UTF-8 splitlines count",
                "NonBlankLines": "lines whose stripped value is nonempty",
                "PythonAstSpanLines": "AST end_lineno - lineno + 1",
            },
            "Totals": Totals,
            "ByLanguage": ByLanguage,
            "LargestFiles": LargestFiles,
            "LargestPythonDefinitions": LargestDefinitions,
        },
    }


def BuildRequiredRepositoryFileRecord(
    Root: Path,
    RelativePath: str,
) -> dict[str, object]:
    """Hash one required regular repository file without following symlinks."""
    PathValue = Root / RelativePath
    if PathValue.is_symlink():
        raise ValueError(f"required provenance file is a symlink: {RelativePath}")
    if not PathValue.is_file():
        raise FileNotFoundError(
            f"required provenance file is missing: {RelativePath}"
        )
    if not PathValue.resolve().is_relative_to(Root.resolve()):
        raise ValueError(
            f"required provenance file escapes repository: {RelativePath}"
        )
    return {
        "Path": RelativePath,
        "Exists": True,
        "IsRegularFile": True,
        "SizeBytes": PathValue.stat().st_size,
        "Sha256": Sha256File(PathValue),
    }


def BuildFileRecordSet(
    Records: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Return a sorted file-record collection with one aggregate identity."""
    SortedRecords = sorted(Records, key=lambda Value: str(Value["Path"]))
    Identity = [
        {
            "Path": Value["Path"],
            "SizeBytes": Value["SizeBytes"],
            "Sha256": Value["Sha256"],
        }
        for Value in SortedRecords
    ]
    return {
        "FileCount": len(SortedRecords),
        "AggregateSha256": Sha256Bytes(CanonicalJsonBytes(Identity)),
        "Files": SortedRecords,
    }


def BuildCurrentRuntimeProvenance(Root: Path) -> dict[str, object]:
    """Capture current interpreter, inputs, templates, build files, and native code."""
    BenchmarkRecords = [
        BuildRequiredRepositoryFileRecord(Root, str(Case["ExamplePath"]))
        for Case in ExpandedAcceptanceCases
    ]
    TemplateInventory = RunGit(
        Root,
        ("ls-files", "-z", "--", "Assets/Templates"),
    )
    TemplatePaths = sorted(
        Value.decode("utf-8", errors="surrogateescape")
        for Value in TemplateInventory.split(b"\0")
        if Value
    )
    if not TemplatePaths:
        raise ValueError("no tracked template files were found")
    TemplateRecords = [
        BuildRequiredRepositoryFileRecord(Root, RelativePath)
        for RelativePath in TemplatePaths
    ]
    BuildRecords = [
        BuildRequiredRepositoryFileRecord(Root, RelativePath)
        for RelativePath in (
            "Kernels/Routing/Cargo.toml",
            "Kernels/Routing/Cargo.lock",
            "Tools/Routing/RunRouterAcceptance.py",
            "pyproject.toml",
            "Assets/Templates/__init__.py",
        )
    ]

    NativeModule = importlib.import_module("RedstoneCompiler.RustRouting")
    RawNativePath = getattr(NativeModule, "__file__", None)
    if not isinstance(RawNativePath, str) or not RawNativePath:
        raise ValueError("loaded native extension has no __file__")
    NativePath = Path(RawNativePath)
    if NativePath.is_symlink():
        raise ValueError("loaded native extension is a symlink")
    ResolvedNativePath = NativePath.resolve()
    if not ResolvedNativePath.is_file():
        raise FileNotFoundError(
            f"loaded native extension is missing: {ResolvedNativePath}"
        )
    if not ResolvedNativePath.is_relative_to(Root.resolve()):
        raise ValueError(
            "loaded native extension is outside the snapshot repository: "
            f"{ResolvedNativePath}"
        )
    NativeRelativePath = ResolvedNativePath.relative_to(
        Root.resolve()
    ).as_posix()
    NativeRecord = BuildRequiredRepositoryFileRecord(
        Root,
        NativeRelativePath,
    )
    NativeRecord.update({
        "Loaded": True,
        "Module": "RedstoneCompiler.RustRouting",
    })
    PolicyModule = importlib.import_module("PhysicalDesign.Policy")
    RoutingStrategy = getattr(PolicyModule, "RoutingStrategy")
    PolicyForRoutingStrategy = getattr(
        PolicyModule,
        "PolicyForRoutingStrategy",
    )
    Policy = PolicyForRoutingStrategy(RoutingStrategy.Default)
    PolicySnapshot = Policy.ToDictionary()
    PolicyRecord = {
        "RoutingStrategy": RoutingStrategy.Default.value,
        "RequestedRoutingStrategy": RoutingStrategy.Default.value,
        "UsedRoutingStrategy": RoutingStrategy.Default.value,
        **BuildPolicySnapshotIdentity(PolicySnapshot),
    }

    RoutingEnvironment = {
        Name: Value
        for Name, Value in sorted(os.environ.items())
        if (
            Name in RoutingEnvironmentNames
            or Name.startswith("RC_")
            or Name.startswith("RCS_")
        )
    }
    return {
        "SchemaVersion": RuntimeProvenanceVersion,
        "Python": {
            "Version": platform.python_version(),
            "Implementation": platform.python_implementation(),
            "Executable": sys.executable,
            "ResolvedExecutable": str(Path(sys.executable).resolve()),
            "Prefix": sys.prefix,
            "BasePrefix": sys.base_prefix,
        },
        "Platform": {
            "Description": platform.platform(),
            "System": platform.system(),
            "Release": platform.release(),
            "Machine": platform.machine(),
        },
        "RoutingEnvironment": RoutingEnvironment,
        "BenchmarkInputs": BuildFileRecordSet(BenchmarkRecords),
        "TrackedTemplates": BuildFileRecordSet(TemplateRecords),
        "BuildInputs": BuildFileRecordSet(BuildRecords),
        "LoadedNativeExtension": NativeRecord,
        "DefaultRoutingPolicy": PolicyRecord,
    }


def SummarizeCla4Failure(
    FailurePath: Path,
    *,
    Observation: VerifiedFileObservation | None = None,
    ArchiveEvidence: VerifiedArchiveEvidence | None = None,
    ObservedSiblingNames: frozenset[str] | None = None,
) -> dict[str, object]:
    """Extract typed CLA4 placement evidence without timeout reclassification."""
    SiblingNames: frozenset[str]
    if Observation is None:
        Observation, SiblingNames = _ObserveExplicitFileAndSiblingNames(
            FailurePath
        )
    else:
        ExpectedPath = Path(os.path.abspath(os.fspath(FailurePath)))
        if Observation.Path != ExpectedPath:
            raise ValueError("CLA4 failure path does not match its observation")
        if ArchiveEvidence is None:
            if ObservedSiblingNames is None:
                raise ValueError(
                    "CLA4 failure observation has no sibling-name observation"
                )
            SiblingNames = ObservedSiblingNames
        else:
            RelativeParent = Observation.Path.parent.relative_to(
                ArchiveEvidence.Root
            )
            SiblingNames = frozenset(
                Path(RelativePath).name
                for RelativePath in ArchiveEvidence.ObservationsByRelativePath
                if Path(RelativePath).parent == RelativeParent
            )
    Payload = json.loads(Observation.Data.decode("utf-8"))
    if not isinstance(Payload, dict):
        raise ValueError("CLA4 failure artifact must contain a JSON object")
    if Payload.get("SchemaVersion") != "routing-failure-v1":
        raise ValueError(
            "CLA4 failure must use routing-failure-v1, got "
            f"{Payload.get('SchemaVersion')!r}"
        )
    Reproduction = dict(Payload.get("Reproduction", {}))
    if Reproduction.get("TopModule") != "CarryLookaheadAdder4":
        raise ValueError(
            "failure artifact is not CarryLookaheadAdder4: "
            f"{Reproduction.get('TopModule')!r}"
        )
    OutputIdentity = Payload.get("OutputIdentity")
    if not isinstance(OutputIdentity, dict):
        OutputIdentity = Reproduction.get("Output")
    if not isinstance(OutputIdentity, dict):
        raise ValueError("CLA4 failure has no output identity object")
    OutputStem = OutputIdentity.get("Stem")
    OutputName = OutputIdentity.get("Name")
    OutputFormat = OutputIdentity.get("Format")
    if not isinstance(OutputStem, str) or not OutputStem:
        raise ValueError("CLA4 failure output identity has no stem")
    if not isinstance(OutputName, str) or not OutputName:
        raise ValueError("CLA4 failure output identity has no name")
    if Path(OutputName).name != OutputName:
        raise ValueError("CLA4 failure output name must be a basename")
    ExpectedFailureName = f"{OutputStem}.RoutingFailure.json"
    if Observation.Path.name != ExpectedFailureName:
        raise ValueError(
            "CLA4 failure filename does not match output identity: "
            f"{Observation.Path.name!r} != {ExpectedFailureName!r}"
        )
    ReproductionOutput = Reproduction.get("Output")
    if isinstance(ReproductionOutput, dict):
        for Key in ("Stem", "Name", "Format"):
            if (
                ReproductionOutput.get(Key) is not None
                and ReproductionOutput.get(Key) != OutputIdentity.get(Key)
            ):
                raise ValueError(
                    "CLA4 failure output identities disagree for "
                    f"{Key}"
                )
    SuccessArtifactNames = (
        OutputName,
        f"{OutputStem}.PhysicalDesign.json",
        f"{OutputStem}.TruthTable.txt",
    )
    ExistingSuccessArtifacts = [
        Name for Name in SuccessArtifactNames if Name in SiblingNames
    ]
    if ExistingSuccessArtifacts:
        raise ValueError(
            "mixed/stale CLA4 evidence: failure artifact coexists with "
            "success artifacts: "
            + ", ".join(ExistingSuccessArtifacts)
        )
    Failure = dict(Payload.get("Failure", {}))
    Diagnostics = dict(Failure.get("Diagnostics", {}))
    Deadline = dict(
        Diagnostics.get("Deadline")
        or Payload.get("Deadline")
        or {}
    )
    Reason = str(Failure.get("Reason", ""))
    TimedOut = bool(Deadline.get("Expired", False)) or Reason in {
        "RuntimeBudgetExceeded",
        "Timeout",
    }

    CandidateSummary: list[dict[str, object]] = []
    for Decision in Diagnostics.get("PlacementGenerationDecisions", ()):
        if not isinstance(Decision, dict):
            continue
        Profile = Decision.get("MandatoryAccessProfile")
        if not isinstance(Profile, dict):
            continue
        CandidateSummary.append({
            "SourceGenerator": Decision.get("SourceGenerator"),
            "Result": Decision.get("Result"),
            "ElapsedSeconds": Decision.get("ElapsedSeconds"),
            "RoutingSpacing": Decision.get("RoutingSpacing"),
            "JointPlacementCandidateIndex": Decision.get(
                "JointPlacementCandidateIndex"
            ),
            "SignalCount": Profile.get("SignalCount"),
            "ClaimCount": Profile.get("ClaimCount"),
            "ExactConflictCount": Profile.get("ExactConflictCount"),
            "ConflictResourceCount": Profile.get("ConflictResourceCount"),
            "ConflictSignals": Profile.get("ConflictSignals", []),
            "CrossConflicts": Profile.get("CrossConflicts", []),
            "SelfConflicts": Profile.get("SelfConflicts", []),
            "OwnershipFingerprint": Profile.get("OwnershipFingerprint"),
            "ConflictFingerprint": Profile.get("ConflictFingerprint"),
        })

    NativeWork = dict(Payload.get("NativeWork", {}))
    RequestCounts = dict(NativeWork.get("RequestCounts", {}))
    Policy = Payload.get("Policy")
    if not isinstance(Policy, dict):
        raise ValueError("CLA4 failure has no complete Policy object")
    Strategy = Payload.get("Strategy")
    if not isinstance(Strategy, dict):
        raise ValueError("CLA4 failure has no Strategy object")
    PolicyIdentity = BuildPolicySnapshotIdentity(Policy)
    RoutingIdentity = {
        "RequestedStrategy": Strategy.get("Requested"),
        "UsedStrategy": Strategy.get("Used"),
        "FallbackUsed": Strategy.get("FallbackUsed"),
        "PolicyIdentity": PolicyIdentity,
        "ReproductionRequestedStrategy": Reproduction.get(
            "RequestedStrategy"
        ),
        "ReproductionCommandRoutingStrategy": ReadCommandRoutingStrategy(
            Reproduction.get("Command")
        ),
    }
    return {
        "EvidenceKind": "DIAGNOSTIC_FAILURE",
        "ArtifactSha256": Observation.Sha256,
        "ArtifactSourceState": Payload.get("SourceState", {}),
        "Stage": Failure.get("Stage"),
        "Reason": Reason,
        "Detail": Failure.get("Detail"),
        "RuntimeSeconds": Payload.get("RuntimeSeconds"),
        "Deadline": Deadline,
        "TimedOut": TimedOut,
        "CandidateSummary": CandidateSummary,
        "NativeRequestCounts": RequestCounts,
        "DetailedRoutingStarted": any(
            int(Value or 0) > 0 for Value in RequestCounts.values()
        ),
        "SuccessArtifactsPublished": bool(ExistingSuccessArtifacts),
        "SuccessArtifactAbsence": {
            "Verified": True,
            "CheckedNames": list(SuccessArtifactNames),
        },
        "OutputIdentity": {
            "Stem": OutputStem,
            "Name": OutputName,
            "Format": OutputFormat,
        },
        "PolicyVersion": Policy.get("PolicyVersion"),
        "ConfiguredRoutingIdentity": None,
        "ObservedRoutingIdentity": RoutingIdentity,
        "TechnologyVersion": dict(Payload.get("Technology", {})).get(
            "TechnologyVersion"
        ),
        "Reproduction": {
            "TopModule": Reproduction.get("TopModule"),
            "RequestedStrategy": Reproduction.get("RequestedStrategy"),
            "Input": Reproduction.get("Input"),
            "Command": Reproduction.get("Command"),
        },
    }


def SummarizeNandDiagram(
    ArtifactPaths: Sequence[Path],
    *,
    ObservationsByPath: Mapping[Path, VerifiedFileObservation] | None = None,
) -> dict[str, object] | None:
    """Summarize the explicitly supplied CLA4 NAND JSON when present."""
    DiagramPaths = [
        PathValue
        for PathValue in ArtifactPaths
        if PathValue.name.endswith(".Nand.json")
    ]
    if not DiagramPaths:
        return None
    if len(DiagramPaths) != 1:
        raise ValueError(
            "exactly one explicit CLA4 .Nand.json artifact is allowed"
        )
    DiagramPath = DiagramPaths[0]
    AbsoluteDiagramPath = Path(os.path.abspath(os.fspath(DiagramPath)))
    Observation = (
        ObservationsByPath.get(AbsoluteDiagramPath)
        if ObservationsByPath is not None
        else None
    )
    if Observation is None:
        Observation = ObserveExplicitFile(DiagramPath)
    Payload = json.loads(Observation.Data.decode("utf-8"))
    if not isinstance(Payload, dict):
        raise ValueError("CLA4 NAND diagram root must be a JSON object")
    if Payload.get("Module") != "CarryLookaheadAdder4":
        raise ValueError(
            "NAND diagram is not CarryLookaheadAdder4: "
            f"{Payload.get('Module')!r}"
        )
    for Field in ("Inputs", "Outputs", "Gates"):
        if not isinstance(Payload.get(Field), list):
            raise ValueError(f"CLA4 NAND diagram {Field} must be a list")
    Inputs = list(Payload["Inputs"])
    Outputs = list(Payload["Outputs"])
    Gates = list(Payload["Gates"])
    if not all(isinstance(Value, str) for Value in (*Inputs, *Outputs)):
        raise ValueError("CLA4 NAND diagram root ports must be strings")
    GateCounts: dict[str, int] = {}
    for GateIndex, Gate in enumerate(Gates):
        if not isinstance(Gate, dict):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} must be an object"
            )
        if not isinstance(Gate.get("Name"), str) or not Gate.get("Name"):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} has no string Name"
            )
        if not isinstance(Gate.get("Kind"), str) or not Gate.get("Kind"):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} has no string Kind"
            )
        if not isinstance(Gate.get("Inputs"), list):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} Inputs must be a list"
            )
        if not isinstance(Gate.get("Outputs"), list):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} Outputs must be a list"
            )
        Kind = str(Gate["Kind"])
        GateCounts[Kind] = GateCounts.get(Kind, 0) + 1
    return {
        "ArtifactSha256": Observation.Sha256,
        "Module": Payload.get("Module"),
        "InputCount": len(Inputs),
        "OutputCount": len(Outputs),
        "GateCount": len(Gates),
        "GateCountsByKind": dict(sorted(GateCounts.items())),
    }


def SummarizeCla4ProcessTimeout(
    ManifestPath: Path,
    *,
    Observation: VerifiedFileObservation | None = None,
    ArchiveEvidence: VerifiedArchiveEvidence | None = None,
) -> dict[str, object]:
    """Retain a harness timeout when the process could not publish a failure.

    This is process evidence only: no compiler proof or completed routing
    stage is inferred, and the absence of a typed compiler failure stays visible.
    """
    Observation = Observation or ObserveExplicitFile(ManifestPath)
    Payload = json.loads(Observation.Data.decode("utf-8"))
    if Payload.get("SchemaVersion") != AcceptanceManifestSchemaVersion:
        raise ValueError("timeout evidence requires an acceptance manifest")
    Runs = [Run for Run in Payload.get("Runs", ()) if Run.get("Circuit") == "CarryLookaheadAdder4"]
    if len(Runs) != 1:
        raise ValueError("timeout capture requires one explicit CLA4 run")
    Run = Runs[0]
    Process = Run.get("Evaluation", {}).get("Process", {})
    if Process.get("TimedOut") is not True or Process.get("ReturnCode") != 124 or Run.get("Accepted") is not False:
        raise ValueError("CLA4 run is not a recorded process timeout")
    Paths = Run.get("ArtifactPaths", {})
    for Key in ("RoutingFailure", "Schematic", "PhysicalDesign", "TruthTable"):
        PathText = Paths.get(Key)
        if not isinstance(PathText, str):
            raise ValueError("timeout capture has missing paths or mixed compiler evidence")
        Candidate = Path(PathText)
        if ArchiveEvidence is not None:
            Absolute = Path(os.path.abspath(os.fspath(Candidate)))
            if Absolute.is_relative_to(ArchiveEvidence.Root):
                if ArchiveEvidence.ObservationForPath(Absolute) is not None:
                    raise ValueError(
                        "timeout capture has missing paths or mixed compiler evidence"
                    )
                continue
        if os.path.lexists(Candidate):
            raise ValueError("timeout capture has missing paths or mixed compiler evidence")
    Provenance = Payload.get("SourceProvenance", {})
    ConfiguredRoutingIdentity = {
        "RequestedStrategy": Provenance.get(
            "RequestedRoutingStrategy", "default"
        ),
        "UsedStrategy": Provenance.get("ExpectedUsedRoutingStrategy"),
        "CommandRoutingStrategy": ReadCommandRoutingStrategy(
            Run.get("Command")
        ),
        "PolicyIdentity": Provenance.get("Policy"),
    }
    return {
        "EvidenceKind": "ACCEPTANCE_PROCESS_TIMEOUT",
        "ArtifactSha256": Observation.Sha256,
        "ArtifactSourceState": Payload.get("SourceState", {}),
        "Stage": "AcceptanceProcess", "Reason": "ProcessTimeout",
        "Detail": "process timed out before publishing a typed compiler failure",
        "RuntimeSeconds": Process.get("WallRuntimeSeconds"),
        "Deadline": Process, "TimedOut": True, "CandidateSummary": [],
        "NativeRequestCounts": {}, "DetailedRoutingStarted": None,
        "SuccessArtifactsPublished": False,
        "SuccessArtifactAbsence": {"Verified": True, "CheckedNames": [Path(Paths[Key]).name for Key in ("Schematic", "PhysicalDesign", "TruthTable")]},
        "OutputIdentity": {"Stem": Run["RunName"], "Name": Path(Paths["Schematic"]).name, "Format": "litematic"},
        "PolicyVersion": None,
        "ConfiguredRoutingIdentity": ConfiguredRoutingIdentity,
        "ObservedRoutingIdentity": {
            "RequestedStrategy": None,
            "UsedStrategy": None,
            "FallbackUsed": None,
            "PolicyIdentity": None,
            "ReproductionRequestedStrategy": None,
            "ReproductionCommandRoutingStrategy": None,
        },
        "TechnologyVersion": None,
        "Reproduction": {
            "TopModule": "CarryLookaheadAdder4",
            "RequestedStrategy": ConfiguredRoutingIdentity[
                "RequestedStrategy"
            ],
            "Input": Provenance.get("BenchmarkInputs", {}).get("CarryLookaheadAdder4"),
            "Command": Run.get("Command"),
        },
    }


def BuildFileIdentityMap(
    Records: object,
) -> dict[str, tuple[object, object]]:
    """Index well-formed file records by relative path for cross-checks."""
    if not isinstance(Records, list):
        raise ValueError("provenance file records must be a list")
    Result: dict[str, tuple[object, object]] = {}
    for Record in Records:
        if not isinstance(Record, dict):
            raise ValueError("provenance file record must be an object")
        RelativePath = Record.get("Path")
        Digest = Record.get("Sha256")
        SizeBytes = Record.get("SizeBytes")
        if (
            not isinstance(RelativePath, str)
            or not RelativePath
            or not isinstance(Digest, str)
            or len(Digest) != 64
            or not isinstance(SizeBytes, int)
        ):
            raise ValueError("provenance file record is incomplete")
        if RelativePath in Result:
            raise ValueError(
                f"duplicate provenance file path: {RelativePath}"
            )
        Result[RelativePath] = (SizeBytes, Digest)
    return Result


def ResolveUsedRoutingStrategy(RequestedStrategy: str) -> str:
    """Resolve one request through the same public strategy contract as compile."""
    PolicyModule = importlib.import_module("PhysicalDesign.Policy")
    UsedStrategy = PolicyModule.ExecutionStrategyForRequest(
        RequestedStrategy
    )
    return str(UsedStrategy.value)


def AcceptanceProducerSha256(Value: object) -> str:
    """Reproduce the acceptance producer's canonical aggregate serialization."""
    return Sha256Bytes(json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8"))


def RoutingReceiptMatches(
    Identity: dict[str, object],
    ExpectedIdentity: dict[str, object],
) -> bool:
    """Require a complete matching receipt with exact no-fallback evidence."""
    return (
        Identity.get("FallbackUsed") is False
        and Identity == ExpectedIdentity
    )


def EvaluatorRejectedRoutingIdentity(
    Checks: dict[str, object] | None,
    Names: tuple[str, ...],
) -> bool:
    """Preserve every applicable explicit evaluator rejection."""
    return Checks is not None and any(
        Checks.get(Name) is False for Name in Names
    )


def BuildAcceptanceRunSummary(
    Run: dict[str, object],
    *,
    RequestedStrategy: str,
    ResolvedUsedStrategy: str,
    PolicyRecord: dict[str, object],
    PolicyIdentity: dict[str, object],
    ProfileCase: dict[str, object] | None = None,
    ManifestSourceState: dict[str, object] | None = None,
    ArchiveEvidence: VerifiedArchiveEvidence | None = None,
) -> dict[str, object]:
    """Project one run and independently classify its routing receipt."""
    Status = Run.get("Status")
    Accepted = Run.get("Accepted")
    if not isinstance(Status, str) or not Status:
        raise ValueError("acceptance run Status is not an exact string")
    if type(Accepted) is not bool:
        raise ValueError("acceptance run Accepted is not an exact boolean")
    EvaluationValue = Run.get("Evaluation")
    Evaluation = (
        EvaluationValue if isinstance(EvaluationValue, dict) else {}
    )
    ObservedValue = Evaluation.get("Observed")
    Observed = ObservedValue if isinstance(ObservedValue, dict) else {}
    ProcessValue = Evaluation.get("Process")
    Process = ProcessValue if isinstance(ProcessValue, dict) else {}
    CommandStrategy = ReadCommandRoutingStrategy(Run.get("Command"))

    ConfiguredValue = Observed.get("ConfiguredRoutingIdentity")
    ConfiguredIdentity = (
        ConfiguredValue if isinstance(ConfiguredValue, dict) else None
    )
    ActualValue = Observed.get("ActualRoutingIdentity")
    ActualIdentity = ActualValue if isinstance(ActualValue, dict) else None
    if ActualIdentity is not None and not any(
        Value is not None for Value in ActualIdentity.values()
    ):
        ActualIdentity = None
    FailureValue = Observed.get("FailureRoutingIdentity")
    FailureIdentity = (
        FailureValue if isinstance(FailureValue, dict) else None
    )
    if FailureIdentity is not None and not any(
        Value is not None for Value in FailureIdentity.values()
    ):
        FailureIdentity = None
    ChecksValue = Observed.get("RoutingIdentityChecks")
    Checks = ChecksValue if isinstance(ChecksValue, dict) else None
    EvaluatorFailures = Evaluation.get("Failures")
    if EvaluatorFailures is None:
        EvaluatorFailures = []
    if not isinstance(EvaluatorFailures, list) or not all(
        isinstance(Value, str) for Value in EvaluatorFailures
    ):
        raise ValueError("acceptance evaluator Failures is not a string list")
    Artifacts = BuildEvaluatorArtifactProjection(Evaluation)

    ExpectedConfiguredIdentity = {
        "RequestedStrategy": RequestedStrategy,
        "UsedStrategy": ResolvedUsedStrategy,
        "CommandRoutingStrategy": RequestedStrategy,
        "PolicyIdentity": PolicyRecord,
    }
    ExpectedActualIdentity = {
        "RequestedStrategy": RequestedStrategy,
        "UsedStrategy": ResolvedUsedStrategy,
        "FallbackUsed": False,
        "PolicyIdentity": PolicyIdentity,
    }
    FailureRoutingDeadline = ValidatedRunRoutingDeadline(Run)
    EffectiveFailurePolicyIdentity = (
        BuildEffectiveRoutingPolicyIdentity(
            dict(PolicyIdentity["Snapshot"]),
            FailureRoutingDeadline,
        )
        if FailureRoutingDeadline is not None
        else None
    )
    ExpectedFailureIdentity = (
        {
            **ExpectedActualIdentity,
            "PolicyIdentity": EffectiveFailurePolicyIdentity,
            "ReproductionRequestedStrategy": RequestedStrategy,
            "ReproductionCommandRoutingStrategy": RequestedStrategy,
        }
        if EffectiveFailurePolicyIdentity is not None
        else None
    )
    CommandMatches = CommandStrategy == RequestedStrategy
    ConfiguredMatches = (
        ConfiguredIdentity is None
        or ConfiguredIdentity == ExpectedConfiguredIdentity
    )
    CommonCheckNames = (
        "ConfiguredCommandMatches",
        "ConfiguredSourcePolicyMatches",
    )
    CommonEvaluatorRejected = EvaluatorRejectedRoutingIdentity(
        Checks,
        CommonCheckNames,
    )
    Receipts = sum(
        Identity is not None
        for Identity in (ActualIdentity, FailureIdentity)
    )
    ReceiptClaimed = bool(
        Checks is not None
        and (
            Checks.get("ActualArtifactPresent") is True
            or Checks.get("FailureArtifactPresent") is True
        )
    )
    if Receipts == 0:
        RoutingIdentityConsistent = (
            None
            if (
                CommandMatches
                and ConfiguredMatches
                and not ReceiptClaimed
                and not CommonEvaluatorRejected
            )
            else False
        )
    elif Receipts > 1 or ConfiguredIdentity is None:
        RoutingIdentityConsistent = False
    elif ActualIdentity is not None:
        ActualEvaluatorRejected = EvaluatorRejectedRoutingIdentity(
            Checks,
            (
                *CommonCheckNames,
                "ActualArtifactPresent",
                "ActualRequestedStrategyMatches",
                "ActualUsedStrategyMatches",
                "ActualFallbackDisabled",
                "ActualPolicySnapshotMatches",
                "ActualPolicyIdentityMatches",
            ),
        )
        RoutingIdentityConsistent = (
            CommandMatches
            and ConfiguredMatches
            and not ActualEvaluatorRejected
            and RoutingReceiptMatches(
                ActualIdentity,
                ExpectedActualIdentity,
            )
        )
    else:
        FailureEvaluatorRejected = EvaluatorRejectedRoutingIdentity(
            Checks,
            (
                *CommonCheckNames,
                "FailureArtifactPresent",
                "FailureStrategyMatches",
                "FailureReproductionMatches",
                "FailurePolicyIdentityMatches",
            ),
        )
        RoutingIdentityConsistent = (
            CommandMatches
            and ConfiguredMatches
            and not FailureEvaluatorRejected
            and ExpectedFailureIdentity is not None
            and RoutingReceiptMatches(
                FailureIdentity,
                ExpectedFailureIdentity,
            )
        )

    FailureArtifact = None
    if (
        ProfileCase is not None
        and ArchiveEvidence is not None
        and ManifestSourceState is not None
    ):
        FailureArtifact = ReadRunFailureArtifact(
            Run,
            ProfileCase=ProfileCase,
            ManifestSourceState=ManifestSourceState,
            ArchiveEvidence=ArchiveEvidence,
        )
    BackendReceiptMatches = (
        Evaluation.get("Accepted") is True
        and RoutingIdentityConsistent is True
        and ProfileCase is not None
        and Observed.get("FabricValidationStatus") == "passed"
        and Observed.get("FabricValidationVectors")
        == ProfileCase["FabricCanaryCount"]
    )
    if Accepted is True and not BackendReceiptMatches:
        raise ValueError(
            "accepted run has no profile-matching backend receipt"
        )
    if Accepted is True and BackendReceiptMatches:
        BackendState = {
            "State": "passed",
            "ProfileChecked": True,
            "MchprsTruthTableRows": ProfileCase[
                "MchprsTruthTableRows"
            ],
            "FabricCanaryCount": ProfileCase["FabricCanaryCount"],
        }
    elif FailureArtifact is not None:
        BackendState = {
            "State": "not-run",
            "ProfileChecked": False,
            "UpstreamStage": FailureArtifact.get("Stage"),
            "UpstreamReason": FailureArtifact.get("Reason"),
        }
    else:
        BackendState = {
            "State": "unknown",
            "ProfileChecked": False,
        }

    return {
        "RunName": Run.get("RunName"),
        "Status": Status,
        "Accepted": Accepted,
        "EvaluatorAccepted": Evaluation.get("Accepted"),
        "EvaluatorFailures": deepcopy(EvaluatorFailures),
        "Artifacts": Artifacts,
        "CommandRoutingStrategy": CommandStrategy,
        "ConfiguredRoutingIdentity": ConfiguredIdentity,
        "ActualRoutingIdentity": ActualIdentity,
        "FailureRoutingIdentity": FailureIdentity,
        "RoutingIdentityChecks": Checks,
        "FailureRoutingDeadlineSeconds": FailureRoutingDeadline,
        "RoutingIdentityConsistent": RoutingIdentityConsistent,
        "FailureArtifact": FailureArtifact,
        "BackendState": BackendState,
        "TimedOut": Process.get("TimedOut"),
        "ReturnCode": Process.get("ReturnCode"),
        "Process": deepcopy(Process),
    }


def SummarizeAcceptanceManifest(
    ManifestPath: Path,
    Cla4Failure: dict[str, object],
    CurrentSource: dict[str, object],
    CurrentRuntime: dict[str, object],
    CurrentCheckout: dict[str, object] | None = None,
    Generator: dict[str, object] | None = None,
    ArchiveEvidence: VerifiedArchiveEvidence | None = None,
) -> dict[str, object]:
    """Validate and summarize one authoritative native acceptance manifest."""
    Evidence = ArchiveEvidence or BuildSealedArchiveEvidence(ManifestPath)
    ManifestObservation = Evidence.ObservationForPath(ManifestPath)
    if ManifestObservation is None or (
        ManifestObservation.Path != Evidence.AcceptanceManifest.Path
    ):
        raise ValueError("acceptance manifest does not match archive observation")
    Payload = json.loads(ManifestObservation.Data.decode("utf-8"))
    if not isinstance(Payload, dict):
        raise ValueError("acceptance manifest root must be a JSON object")
    if Payload.get("SchemaVersion") != AcceptanceManifestSchemaVersion:
        raise ValueError(
            "acceptance manifest must use "
            f"{AcceptanceManifestSchemaVersion}, got "
            f"{Payload.get('SchemaVersion')!r}"
        )
    if Payload.get("SourceProvenanceStable") is not True:
        raise ValueError("acceptance manifest source provenance is not stable")
    if not isinstance(Payload.get("Status"), str) or not Payload["Status"]:
        raise ValueError("acceptance manifest Status is not an exact string")
    if type(Payload.get("Accepted")) is not bool:
        raise ValueError("acceptance manifest Accepted is not an exact boolean")
    ProfileAuthority, ProfileCases = SelectAcceptanceProfile(Payload)
    CaseMatrix = ValidateCaseMatrix(Payload, ProfileCases)
    ArchiveIdentity = Evidence.Identity

    FailureSourceState = Cla4Failure.get("ArtifactSourceState")
    ManifestSourceState = Payload.get("SourceState")
    SourceProvenance = Payload.get("SourceProvenance")
    if not isinstance(FailureSourceState, dict):
        raise ValueError("CLA4 failure has no source-state object")
    if not isinstance(ManifestSourceState, dict):
        raise ValueError("acceptance manifest has no source-state object")
    if not isinstance(SourceProvenance, dict):
        raise ValueError("acceptance manifest has no SourceProvenance object")
    FailureRevision = FailureSourceState.get("Revision")
    ManifestRevision = ManifestSourceState.get("Revision")
    ManifestDirty = ManifestSourceState.get("Dirty")
    GitProvenance = SourceProvenance.get("Git")
    if not isinstance(GitProvenance, dict):
        raise ValueError("acceptance source provenance has no Git object")
    ProvenanceRevision = GitProvenance.get("Revision")
    if (
        not isinstance(FailureRevision, str)
        or not FailureRevision
        or type(ManifestDirty) is not bool
        or ManifestRevision != FailureRevision
        or ProvenanceRevision != FailureRevision
        or FailureSourceState.get("Dirty") is not ManifestDirty
        or GitProvenance.get("Dirty") is not ManifestDirty
    ):
        raise ValueError(
            "acceptance manifest and CLA4 failure source revisions disagree"
        )
    if ArchiveIdentity is not None:
        ArchiveInvocation = ArchiveIdentity.get("Invocation")
        ArchiveSource = ArchiveIdentity.get("Source")
        if not isinstance(ArchiveInvocation, dict) or (
            ArchiveInvocation.get("MatrixMode") != Payload.get("MatrixMode")
            or ArchiveInvocation.get("BaselineMode")
            != Payload.get("BaselineMode")
        ):
            raise ValueError("acceptance archive invocation is inconsistent")
        if not isinstance(ArchiveSource, dict):
            raise ValueError("acceptance archive source identity is missing")
        StartSource = ArchiveSource.get("Start")
        EndSource = ArchiveSource.get("End")
        if (
            ArchiveSource.get("Stable") is not True
            or ArchiveSource.get("AcceptanceProvenanceStable") is not True
            or not isinstance(StartSource, dict)
            or not isinstance(EndSource, dict)
            or StartSource.get("Head") != ManifestRevision
            or EndSource.get("Head") != ManifestRevision
            or StartSource.get("Dirty") is not ManifestSourceState.get("Dirty")
            or EndSource.get("Dirty") is not ManifestSourceState.get("Dirty")
        ):
            raise ValueError("acceptance archive source identity is inconsistent")

    ExpectedPolicyVersion = SourceProvenance.get("ExpectedPolicyVersion")
    RequestedStrategy = SourceProvenance.get("RequestedRoutingStrategy")
    ExpectedUsedStrategy = SourceProvenance.get(
        "ExpectedUsedRoutingStrategy"
    )
    PolicyRecord = SourceProvenance.get("Policy")
    if not isinstance(PolicyRecord, dict):
        raise ValueError("acceptance source provenance has no Policy object")
    PolicySnapshot = PolicyRecord.get("Snapshot")
    if not isinstance(PolicySnapshot, dict):
        raise ValueError("acceptance policy provenance has no Snapshot object")
    ComputedPolicyIdentity = BuildPolicySnapshotIdentity(PolicySnapshot)
    if (
        not isinstance(RequestedStrategy, str)
        or not RequestedStrategy
        or not isinstance(ExpectedUsedStrategy, str)
        or not ExpectedUsedStrategy
        or PolicyRecord.get("RequestedRoutingStrategy") != RequestedStrategy
        or PolicyRecord.get("UsedRoutingStrategy") != ExpectedUsedStrategy
        or PolicyRecord.get("PolicyVersion") != ExpectedPolicyVersion
        or PolicyRecord.get("Sha256") != ComputedPolicyIdentity["Sha256"]
        or PolicyRecord.get("Seed") != ComputedPolicyIdentity["Seed"]
    ):
        raise ValueError(
            "acceptance strategy and complete policy provenance disagree"
        )
    ResolvedUsedStrategy = ResolveUsedRoutingStrategy(RequestedStrategy)
    ManifestRoutingIdentity = Payload.get("RoutingIdentity")
    ExpectedConfiguredIdentity = {
        "ConfiguredRequestedStrategy": RequestedStrategy,
        "ExpectedUsedStrategy": ExpectedUsedStrategy,
        "PolicyIdentity": PolicyRecord,
    }
    if ManifestRoutingIdentity != ExpectedConfiguredIdentity:
        raise ValueError("acceptance manifest routing identity is inconsistent")

    RawRuns = Payload.get("Runs")
    if not isinstance(RawRuns, list) or not RawRuns:
        raise ValueError("acceptance manifest Runs must be a nonempty list")
    if ArchiveIdentity is not None:
        Invocation = ArchiveIdentity.get("Invocation")
        WorkingDirectory = (
            Invocation.get("WorkingDirectory")
            if isinstance(Invocation, dict)
            else None
        )
        if (
            not isinstance(WorkingDirectory, str)
            or not Path(WorkingDirectory).is_absolute()
        ):
            raise ValueError("acceptance archive producer root is invalid")
        ProducerRoot = Path(WorkingDirectory)
    else:
        FirstCommand = (
            RawRuns[0].get("Command")
            if isinstance(RawRuns[0], dict)
            else None
        )
        if not isinstance(FirstCommand, list) or len(FirstCommand) < 2:
            raise ValueError("acceptance run command is incomplete")
        ProducerRoot = Path(str(FirstCommand[1])).parent
    OrderedRuns = ValidateAcceptanceRuns(
        Payload,
        ProfileCases,
        RequestedStrategy,
        ProducerRoot,
    )
    Cla4Runs = [
        Run
        for Run in OrderedRuns
        if Run.get("Circuit") == "CarryLookaheadAdder4"
    ]
    Cla4ProfileCase = next(
        Case
        for Case in ProfileCases
        if Case["Name"] == "CarryLookaheadAdder4"
    )
    Cla4CaseDeadline = float(Cla4ProfileCase["RoutingDeadlineSeconds"])

    FailureKind = Cla4Failure.get("EvidenceKind")
    ConfiguredFailureIdentity = Cla4Failure.get("ConfiguredRoutingIdentity")
    ObservedFailureIdentity = Cla4Failure.get("ObservedRoutingIdentity")
    FailureRoutingIdentityMatches: bool | None
    if FailureKind == "ACCEPTANCE_PROCESS_TIMEOUT":
        ExpectedTimeoutConfigured = {
            "RequestedStrategy": RequestedStrategy,
            "UsedStrategy": ExpectedUsedStrategy,
            "CommandRoutingStrategy": RequestedStrategy,
            "PolicyIdentity": PolicyRecord,
        }
        if ConfiguredFailureIdentity != ExpectedTimeoutConfigured:
            raise ValueError(
                "timeout configured routing identity is inconsistent"
            )
        if not isinstance(ObservedFailureIdentity, dict) or any(
            ObservedFailureIdentity.get(Name) is not None
            for Name in (
                "RequestedStrategy",
                "UsedStrategy",
                "FallbackUsed",
                "PolicyIdentity",
                "ReproductionRequestedStrategy",
                "ReproductionCommandRoutingStrategy",
            )
        ):
            raise ValueError(
                "timeout must not invent observed routing identity"
            )
        FailureRoutingIdentityMatches = None
    else:
        Cla4RoutingDeadlines = {
            ValidatedRunRoutingDeadline(Run) for Run in Cla4Runs
        }
        if (
            Cla4RoutingDeadlines != {Cla4CaseDeadline}
        ):
            raise ValueError(
                "CLA4 invocation routing deadline is inconsistent"
            )
        EffectiveFailurePolicyIdentity = BuildEffectiveRoutingPolicyIdentity(
            PolicySnapshot,
            Cla4CaseDeadline,
        )
        if not isinstance(ObservedFailureIdentity, dict):
            raise ValueError("CLA4 failure has no observed routing identity")
        ObservedPolicyIdentity = ObservedFailureIdentity.get("PolicyIdentity")
        if (
            ObservedFailureIdentity.get("RequestedStrategy")
            != RequestedStrategy
            or ObservedFailureIdentity.get("UsedStrategy")
            != ExpectedUsedStrategy
            or ObservedFailureIdentity.get("FallbackUsed") is not False
            or ObservedFailureIdentity.get("ReproductionRequestedStrategy")
            != RequestedStrategy
            or ObservedFailureIdentity.get(
                "ReproductionCommandRoutingStrategy"
            )
            != RequestedStrategy
            or ObservedPolicyIdentity != EffectiveFailurePolicyIdentity
        ):
            raise ValueError(
                "acceptance manifest and CLA4 failure routing identities disagree"
            )
        FailureRoutingIdentityMatches = True

    SourceContent = SourceProvenance.get("SourceContent")
    if not isinstance(SourceContent, dict):
        raise ValueError(
            "acceptance source provenance has no SourceContent object"
        )
    HistoricalSourceFiles = BuildFileIdentityMap(SourceContent.get("Files"))
    if not isinstance(SourceContent.get("AggregateSha256"), str):
        raise ValueError("acceptance SourceContent has no aggregate digest")
    if SourceContent.get("FileCount") != len(HistoricalSourceFiles):
        raise ValueError("acceptance SourceContent file count is inconsistent")
    if SourceContent.get("AggregateSha256") != AcceptanceProducerSha256(
        SourceContent["Files"]
    ):
        raise ValueError(
            "acceptance SourceContent aggregate is inconsistent"
        )
    CurrentSourceFiles = BuildFileIdentityMap(CurrentSource.get("Files"))
    CurrentBuildFiles = BuildFileIdentityMap(
        dict(CurrentRuntime["BuildInputs"])["Files"]
    )
    CurrentSourceCoverage = dict(CurrentSourceFiles)
    CurrentSourceCoverage.update(CurrentBuildFiles)
    CurrentSourceMatches = all(
        CurrentSourceCoverage.get(RelativePath) == Identity
        for RelativePath, Identity in HistoricalSourceFiles.items()
    )

    BenchmarkInputs = SourceProvenance.get("BenchmarkInputs")
    if not isinstance(BenchmarkInputs, dict):
        raise ValueError(
            "acceptance source provenance has no BenchmarkInputs object"
        )
    CurrentBenchmarkFiles = BuildFileIdentityMap(
        dict(CurrentRuntime["BenchmarkInputs"])["Files"]
    )
    BenchmarkSummary: dict[str, dict[str, object]] = {}
    BenchmarkInputsMatchCurrent = True
    for ProfileCase in ProfileCases:
        CaseName = str(ProfileCase["Name"])
        Record = BenchmarkInputs.get(CaseName)
        if not isinstance(Record, dict):
            raise ValueError(
                f"acceptance benchmark input is missing: {CaseName}"
            )
        RelativePath = ProfileCase["ExamplePath"]
        if (
            Record.get("Exists") is not True
            or Record.get("Path") != RelativePath
            or not isinstance(Record.get("Sha256"), str)
            or not isinstance(Record.get("SizeBytes"), int)
        ):
            raise ValueError(
                f"acceptance benchmark input is incomplete: {CaseName}"
            )
        Identity = (Record["SizeBytes"], Record["Sha256"])
        BenchmarkInputsMatchCurrent &= (
            CurrentBenchmarkFiles.get(str(RelativePath)) == Identity
        )
        BenchmarkSummary[CaseName] = {
            "Path": RelativePath,
            "SizeBytes": Record["SizeBytes"],
            "Sha256": Record["Sha256"],
        }
    FailureInput = dict(
        dict(Cla4Failure.get("Reproduction", {})).get("Input", {})
    )
    Cla4Benchmark = BenchmarkSummary["CarryLookaheadAdder4"]
    if FailureInput.get("Sha256") != Cla4Benchmark["Sha256"]:
        raise ValueError(
            "acceptance CLA4 input does not match failure reproduction input"
        )

    PhysicalTemplates = SourceProvenance.get("PhysicalTemplates")
    if not isinstance(PhysicalTemplates, dict):
        raise ValueError(
            "acceptance source provenance has no PhysicalTemplates object"
        )
    TemplateRecords = PhysicalTemplates.get("Templates")
    if not isinstance(TemplateRecords, dict):
        raise ValueError("acceptance PhysicalTemplates has no Templates object")
    if PhysicalTemplates.get("AggregateSha256") != (
        AcceptanceProducerSha256(TemplateRecords)
    ):
        raise ValueError(
            "acceptance PhysicalTemplates aggregate is inconsistent"
        )
    CurrentTemplateFiles = BuildFileIdentityMap(
        dict(CurrentRuntime["TrackedTemplates"])["Files"]
    )
    TemplateSummary: dict[str, dict[str, object]] = {}
    PhysicalTemplatesMatchCurrent = True
    for TemplateName in ("Input", "Nand", "Output"):
        Record = TemplateRecords.get(TemplateName)
        if not isinstance(Record, dict):
            raise ValueError(
                f"acceptance physical template is missing: {TemplateName}"
            )
        RelativePath = Record.get("Path")
        if (
            Record.get("Exists") is not True
            or not isinstance(RelativePath, str)
            or not isinstance(Record.get("Sha256"), str)
            or not isinstance(Record.get("SizeBytes"), int)
        ):
            raise ValueError(
                f"acceptance physical template is incomplete: {TemplateName}"
            )
        Identity = (Record["SizeBytes"], Record["Sha256"])
        PhysicalTemplatesMatchCurrent &= (
            CurrentTemplateFiles.get(RelativePath) == Identity
        )
        TemplateSummary[TemplateName] = {
            "Path": RelativePath,
            "SizeBytes": Record["SizeBytes"],
            "Sha256": Record["Sha256"],
        }

    NativeExtension = SourceProvenance.get("NativeExtension")
    if not isinstance(NativeExtension, dict):
        raise ValueError(
            "acceptance source provenance has no NativeExtension object"
        )
    if (
        NativeExtension.get("Exists") is not True
        or NativeExtension.get("Loaded") is not True
        or not isinstance(NativeExtension.get("Path"), str)
        or not isinstance(NativeExtension.get("Sha256"), str)
        or not isinstance(NativeExtension.get("SizeBytes"), int)
    ):
        raise ValueError("acceptance native extension record is incomplete")
    CurrentNative = dict(CurrentRuntime["LoadedNativeExtension"])
    NativeExtensionMatchesCurrent = (
        NativeExtension.get("Path") == CurrentNative.get("Path")
        and NativeExtension.get("SizeBytes") == CurrentNative.get("SizeBytes")
        and NativeExtension.get("Sha256") == CurrentNative.get("Sha256")
    )

    BuildInputsMatchCurrent = all(
        HistoricalSourceFiles.get(RelativePath) == Identity
        for RelativePath, Identity in CurrentBuildFiles.items()
    )
    DefaultPolicy = dict(CurrentRuntime["DefaultRoutingPolicy"])
    PolicyModule = importlib.import_module("PhysicalDesign.Policy")
    SelectedPolicy = PolicyModule.PolicyForRoutingStrategy(
        ResolvedUsedStrategy
    )
    SelectedPolicySnapshot = SelectedPolicy.ToDictionary()
    CurrentPolicy = {
        "RoutingStrategy": RequestedStrategy,
        "RequestedRoutingStrategy": RequestedStrategy,
        "UsedRoutingStrategy": ResolvedUsedStrategy,
        **BuildPolicySnapshotIdentity(SelectedPolicySnapshot),
    }
    PolicyMatchesCurrent = PolicyRecord == CurrentPolicy
    DefaultPolicyMatchesCurrent = PolicyRecord == DefaultPolicy

    ProfileCasesByName = {
        str(Case["Name"]): Case for Case in ProfileCases
    }
    RunSummaries = [
        BuildAcceptanceRunSummary(
            Run,
            RequestedStrategy=RequestedStrategy,
            ResolvedUsedStrategy=ResolvedUsedStrategy,
            PolicyRecord=PolicyRecord,
            PolicyIdentity=ComputedPolicyIdentity,
            ProfileCase=ProfileCasesByName[str(Run["Circuit"])],
            ManifestSourceState=ManifestSourceState,
            ArchiveEvidence=Evidence,
        )
        for Run in OrderedRuns
    ]
    MeasuredRuns = [
        Run
        for Run in OrderedRuns
        if Run.get("Warmup") is False
    ]
    if Payload["Accepted"] is True and not all(
        Run.get("Accepted") is True for Run in MeasuredRuns
    ):
        raise ValueError(
            "accepted manifest contains a non-accepted required run"
        )
    Cla4FailureArtifacts = [
        dict(Run["FailureArtifact"])["Artifact"]
        for Run in RunSummaries
        if Run.get("RunName", "").startswith("CarryLookaheadAdder4")
        and isinstance(Run.get("FailureArtifact"), dict)
    ]
    if Cla4FailureArtifacts and not any(
        Record.get("Sha256") == Cla4Failure.get("ArtifactSha256")
        for Record in Cla4FailureArtifacts
    ):
        raise ValueError(
            "explicit CLA4 failure is not an evaluator-selected occurrence"
        )

    ArtifactProducer = {
        "AcceptanceManifest": {
            **ManifestObservation.PublicRecord(),
        },
        "Archive": ArchiveIdentity,
        "SourceState": deepcopy(ManifestSourceState),
        "SourceProvenance": {
            "Git": deepcopy(GitProvenance),
            "SourceContent": deepcopy(SourceContent),
            "BenchmarkInputs": deepcopy(BenchmarkInputs),
            "PhysicalTemplates": deepcopy(PhysicalTemplates),
            "NativeExtension": deepcopy(NativeExtension),
            "Policy": deepcopy(PolicyRecord),
        },
        "RoutingIdentity": deepcopy(ManifestRoutingIdentity),
        "ProvenanceChecks": deepcopy(Payload.get("ProvenanceChecks")),
    }
    Exporter = {
        "Checkout": deepcopy(CurrentCheckout),
        "Generator": deepcopy(Generator),
        "RoutingSource": deepcopy(CurrentSource),
        "RuntimeProvenance": deepcopy(CurrentRuntime),
    }

    return {
        "EvidenceKind": "NATIVE_ACCEPTANCE_MANIFEST",
        "ArtifactSha256": ManifestObservation.Sha256,
        "SchemaVersion": Payload["SchemaVersion"],
        "Accepted": Payload.get("Accepted"),
        "Status": Payload.get("Status"),
        "ExecutionMode": Payload.get("ExecutionMode"),
        "MatrixMode": Payload.get("MatrixMode"),
        "FailFast": Payload.get("FailFast"),
        "BaselineMode": Payload.get("BaselineMode"),
        "AcceptanceProfile": ProfileAuthority,
        "ArtifactProducer": ArtifactProducer,
        "Exporter": Exporter,
        "SourceProvenanceStable": True,
        "SourceState": ManifestSourceState,
        "ExpectedPolicyVersion": ExpectedPolicyVersion,
        "RoutingIdentity": {
            "Configured": ExpectedConfiguredIdentity,
            "FailureConfigured": ConfiguredFailureIdentity,
            "FailureObserved": ObservedFailureIdentity,
        },
        "PolicyIdentity": PolicyRecord,
        "PolicySha256": PolicyRecord.get("Sha256"),
        "Runs": RunSummaries,
        "CaseMatrix": CaseMatrix,
        "BenchmarkInputs": BenchmarkSummary,
        "SourceContent": {
            "FileCount": SourceContent.get("FileCount"),
            "AggregateSha256": SourceContent.get("AggregateSha256"),
        },
        "PhysicalTemplates": {
            "AggregateSha256": PhysicalTemplates.get("AggregateSha256"),
            "Templates": TemplateSummary,
        },
        "NativeExtension": {
            "Path": NativeExtension.get("Path"),
            "SizeBytes": NativeExtension.get("SizeBytes"),
            "Sha256": NativeExtension.get("Sha256"),
        },
        "CrossChecks": {
            "FailureRevisionMatches": True,
            "FailurePolicyMatches": FailureRoutingIdentityMatches,
            "FailureRoutingIdentityMatches": (
                FailureRoutingIdentityMatches
            ),
            "ConfiguredRoutingIdentityMatches": True,
            "FailureInputMatches": True,
            "AuthoritativeCaseMatrixMatches": True,
            "CurrentRoutingSourceMatches": CurrentSourceMatches,
            "CurrentBenchmarkInputsMatch": BenchmarkInputsMatchCurrent,
            "CurrentPhysicalTemplatesMatch": PhysicalTemplatesMatchCurrent,
            "CurrentNativeExtensionMatches": NativeExtensionMatchesCurrent,
            "CurrentBuildInputsMatch": BuildInputsMatchCurrent,
            "CurrentSelectedPolicyMatches": PolicyMatchesCurrent,
            "CurrentDefaultPolicyMatches": DefaultPolicyMatchesCurrent,
        },
    }


def BuildArtifactManifest(
    ArtifactPaths: Sequence[Path],
) -> list[dict[str, object]]:
    """Hash explicitly selected evidence and assign collision-free copy paths."""
    Records, _Observations = CaptureArtifactManifest(ArtifactPaths)
    return Records


def CaptureArtifactManifest(
    ArtifactPaths: Sequence[Path],
    *,
    Preverified: Mapping[Path, VerifiedFileObservation] | None = None,
    ArchiveEvidence: VerifiedArchiveEvidence | None = None,
) -> tuple[
    list[dict[str, object]],
    dict[str, VerifiedFileObservation],
]:
    """Capture explicit artifacts once and retain bytes for staged output."""
    Records: list[dict[str, object]] = []
    SeenSources: set[Path] = set()
    SeenNames: set[str] = set()
    ObservationsBySnapshotPath: dict[str, VerifiedFileObservation] = {}
    PreverifiedByPath = dict(Preverified or {})
    for PathValue in ArtifactPaths:
        AbsolutePath = Path(os.path.abspath(os.fspath(PathValue)))
        if AbsolutePath in SeenSources:
            continue
        SeenSources.add(AbsolutePath)
        Observation = PreverifiedByPath.get(AbsolutePath)
        if ArchiveEvidence is not None and AbsolutePath.is_relative_to(
            ArchiveEvidence.Root
        ):
            Observation = ArchiveEvidence.ObservationForPath(AbsolutePath)
            if Observation is None:
                raise ValueError(
                    "explicit archive artifact is absent from verified seal: "
                    f"{PathValue}"
                )
        if Observation is None:
            try:
                Observation = ObserveExplicitFile(AbsolutePath)
            except ValueError as Error:
                raise FileNotFoundError(
                    f"explicit artifact is missing or unsafe: {PathValue}"
                ) from Error
        if Observation.Path != AbsolutePath:
            raise ValueError("explicit artifact observation path mismatch")
        if AbsolutePath.name in SeenNames:
            raise ValueError(
                "explicit artifacts have a duplicate basename: "
                f"{AbsolutePath.name}"
            )
        SeenNames.add(AbsolutePath.name)
        SnapshotPath = f"Artifacts/{AbsolutePath.name}"
        Records.append({
            "OriginalPath": str(AbsolutePath),
            "SnapshotPath": SnapshotPath,
            "SizeBytes": Observation.SizeBytes,
            "Sha256": Observation.Sha256,
        })
        ObservationsBySnapshotPath[SnapshotPath] = Observation
    Records.sort(key=lambda Value: str(Value["SnapshotPath"]))
    return Records, ObservationsBySnapshotPath


def BuildExactEvidence(Snapshot: dict[str, object]) -> dict[str, object]:
    """Build a timestamp-free identity that retains every raw artifact hash."""
    Exact = deepcopy(Snapshot)
    for Key in (
        "SnapshotId",
        "CapturedAtUtc",
        "CapturedAtLocal",
        "ExactEvidenceSha256",
        "PortableSemanticEvidenceSha256",
    ):
        Exact.pop(Key, None)
    for Artifact in Exact.get("Artifacts", []):
        Artifact.pop("OriginalPath", None)
    return Exact


def BuildPortableSemanticEvidence(
    Snapshot: dict[str, object],
) -> dict[str, object]:
    """Build path-free semantic evidence without raw evidence-file hashes."""
    Failure = deepcopy(dict(Snapshot.get("Cla4Failure", {})))
    Failure.pop("ArtifactSha256", None)
    Reproduction = dict(Failure.get("Reproduction", {}))
    Reproduction.pop("Command", None)
    InputRecord = dict(Reproduction.get("Input", {}))
    InputRecord.pop("Path", None)
    if InputRecord:
        Reproduction["Input"] = InputRecord
    Failure["Reproduction"] = Reproduction

    NandDiagram = deepcopy(Snapshot.get("NandDiagram"))
    if isinstance(NandDiagram, dict):
        NandDiagram.pop("ArtifactSha256", None)
    AcceptanceManifest = deepcopy(Snapshot.get("AcceptanceManifest"))
    if isinstance(AcceptanceManifest, dict):
        AcceptanceManifest.pop("ArtifactSha256", None)

    Runtime = deepcopy(dict(Snapshot.get("CurrentRuntimeProvenance", {})))
    PythonRecord = dict(Runtime.get("Python", {}))
    for Key in (
        "Executable",
        "ResolvedExecutable",
        "Prefix",
        "BasePrefix",
    ):
        PythonRecord.pop(Key, None)
    Runtime["Python"] = PythonRecord

    if isinstance(AcceptanceManifest, dict):
        for Run in AcceptanceManifest.get("Runs", []):
            for Record in dict(Run.get("Artifacts", {})).values():
                if isinstance(Record, dict):
                    Record.pop("Path", None)
            FailureArtifact = Run.get("FailureArtifact")
            if isinstance(FailureArtifact, dict):
                Resolution = FailureArtifact.get("Resolution")
                if isinstance(Resolution, dict):
                    Resolution.pop("Path", None)
                    Resolution.pop("CandidatePath", None)
                Artifact = FailureArtifact.get("Artifact")
                if isinstance(Artifact, dict):
                    Artifact.pop("Path", None)
        Producer = AcceptanceManifest.get("ArtifactProducer")
        if isinstance(Producer, dict):
            ManifestRecord = Producer.get("AcceptanceManifest")
            if isinstance(ManifestRecord, dict):
                ManifestRecord.pop("Path", None)
            Archive = Producer.get("Archive")
            if isinstance(Archive, dict):
                Archive.pop("ArchiveRoot", None)
                for Name in ("ArchiveManifest", "SHA256SUMS"):
                    Record = Archive.get(Name)
                    if isinstance(Record, dict):
                        Record.pop("Path", None)
                Invocation = Archive.get("Invocation")
                if isinstance(Invocation, dict):
                    Invocation.pop("Arguments", None)
                    Invocation.pop("WorkingDirectory", None)
            Producer.pop("ProvenanceChecks", None)
            ProducerSource = Producer.get("SourceProvenance")
            if isinstance(ProducerSource, dict):
                Native = ProducerSource.get("NativeExtension")
                if isinstance(Native, dict):
                    Native.pop("Path", None)
                    Native.pop("ProbePythonExecutable", None)
        Exporter = AcceptanceManifest.get("Exporter")
        if isinstance(Exporter, dict):
            ExporterCheckout = dict(Exporter.get("Checkout") or {})
            Exporter["Checkout"] = {
                "Revision": ExporterCheckout.get("Revision"),
                "Dirty": ExporterCheckout.get("Dirty"),
            }
            ExporterGenerator = Exporter.get("Generator")
            if isinstance(ExporterGenerator, dict):
                ExporterGenerator.pop("Path", None)
            Exporter["RuntimeProvenance"] = deepcopy(Runtime)

    Checkout = dict(Snapshot.get("Checkout", {}))
    Generator = dict(Snapshot.get("Generator", {}))
    Generator.pop("Path", None)
    return {
        "SchemaVersion": Snapshot.get("SchemaVersion"),
        "Generator": Generator,
        "Checkout": {
            "Revision": Checkout.get("Revision"),
            "Dirty": Checkout.get("Dirty"),
        },
        "Source": deepcopy(Snapshot.get("Source")),
        "CurrentRuntimeProvenance": Runtime,
        "Cla4Failure": Failure,
        "AcceptanceManifest": AcceptanceManifest,
        "NandDiagram": NandDiagram,
    }


def BuildRoutingDesignSnapshot(
    Configuration: SnapshotConfiguration,
) -> dict[str, object]:
    """Assemble stable machine evidence and reject mixed source states."""
    CapturedAtUtc = Configuration.CapturedAtUtc.astimezone(timezone.utc).replace(
        microsecond=0
    )
    CapturedAtLocal = CapturedAtUtc.astimezone(LocalTimeZone)
    Checkout = ReadDetailedGitState(Configuration.RepositoryRoot)
    Source = BuildRoutingSourceManifest(Configuration.RepositoryRoot)
    CurrentRuntime = BuildCurrentRuntimeProvenance(
        Configuration.RepositoryRoot
    )
    ArchiveEvidence = (
        BuildSealedArchiveEvidence(Configuration.AcceptanceManifestPath)
        if Configuration.AcceptanceManifestPath is not None
        else None
    )
    FailureSource = Configuration.Cla4FailurePath or Configuration.AcceptanceManifestPath
    if FailureSource is None:
        raise ValueError("capture requires CLA4 compiler failure or an explicit timeout manifest")
    FailureAbsolutePath = Path(os.path.abspath(os.fspath(FailureSource)))
    FailureObservation = (
        ArchiveEvidence.ObservationForPath(FailureAbsolutePath)
        if ArchiveEvidence is not None
        else None
    )
    FailureSiblingNames: frozenset[str] | None = None
    if FailureObservation is None:
        FailureObservation, FailureSiblingNames = (
            _ObserveExplicitFileAndSiblingNames(FailureAbsolutePath)
        )
    Cla4Failure = (
        SummarizeCla4Failure(
            FailureSource,
            Observation=FailureObservation,
            ArchiveEvidence=ArchiveEvidence,
            ObservedSiblingNames=FailureSiblingNames,
        )
        if Configuration.Cla4FailurePath is not None
        else SummarizeCla4ProcessTimeout(
            FailureSource,
            Observation=FailureObservation,
            ArchiveEvidence=ArchiveEvidence,
        )
    )
    ArtifactInputs: list[Path] = [FailureSource]
    if Configuration.AcceptanceManifestPath is not None:
        ArtifactInputs.append(Configuration.AcceptanceManifestPath)
        assert ArchiveEvidence is not None
        ArtifactInputs.extend((
            ArchiveEvidence.Root / "ArchiveManifest.json",
            ArchiveEvidence.Root / "SHA256SUMS",
        ))
    ArtifactInputs.extend(Configuration.ArtifactPaths)
    Artifacts, ArtifactObservations = CaptureArtifactManifest(
        ArtifactInputs,
        Preverified={FailureAbsolutePath: FailureObservation},
        ArchiveEvidence=ArchiveEvidence,
    )
    ObservationsByOriginalPath = {
        Path(str(Record["OriginalPath"])): ArtifactObservations[
            str(Record["SnapshotPath"])
        ]
        for Record in Artifacts
    }
    NandDiagram = SummarizeNandDiagram(
        Configuration.ArtifactPaths,
        ObservationsByPath=ObservationsByOriginalPath,
    )
    Generator = BuildSnapshotFileRecord(
        GeneratorPath,
        RelativeDisplayPath(GeneratorPath, Configuration.RepositoryRoot),
    )
    AcceptanceManifest = None
    if Configuration.AcceptanceManifestPath is not None:
        AcceptanceManifest = SummarizeAcceptanceManifest(
            Configuration.AcceptanceManifestPath,
            Cla4Failure,
            Source,
            CurrentRuntime,
            Checkout,
            Generator,
            ArchiveEvidence,
        )
    ArtifactHashes = {
        Path(str(Value["OriginalPath"])): Value["Sha256"]
        for Value in Artifacts
    }
    if ArtifactHashes.get(FailureAbsolutePath) != (
        Cla4Failure["ArtifactSha256"]
    ):
        raise RuntimeError("CLA4 failure evidence changed during capture")
    if NandDiagram is not None:
        NandPaths = [
            Path(os.path.abspath(os.fspath(PathValue)))
            for PathValue in Configuration.ArtifactPaths
            if PathValue.name.endswith(".Nand.json")
        ]
        if ArtifactHashes.get(NandPaths[0]) != NandDiagram["ArtifactSha256"]:
            raise RuntimeError("NAND evidence changed during capture")
    if AcceptanceManifest is not None:
        AcceptancePath = Configuration.AcceptanceManifestPath
        assert AcceptancePath is not None
        AcceptanceAbsolutePath = Path(os.path.abspath(os.fspath(AcceptancePath)))
        if ArtifactHashes.get(AcceptanceAbsolutePath) != (
            AcceptanceManifest["ArtifactSha256"]
        ):
            raise RuntimeError(
                "acceptance evidence changed during capture"
            )
        ProducerArchive = dict(
            AcceptanceManifest.get("ArtifactProducer", {})
        ).get("Archive")
        if isinstance(ProducerArchive, dict):
            for Name in ("ArchiveManifest", "SHA256SUMS"):
                Record = ProducerArchive.get(Name)
                if not isinstance(Record, dict):
                    raise RuntimeError("acceptance archive identity is incomplete")
                RecordPath = Path(str(Record.get("Path")))
                RecordAbsolutePath = Path(os.path.abspath(os.fspath(RecordPath)))
                if ArtifactHashes.get(RecordAbsolutePath) != Record.get(
                    "Sha256"
                ):
                    raise RuntimeError(
                        "acceptance archive seal changed during capture"
                    )
    Cla4Failure["CurrentRevisionMatchesArtifact"] = (
        str(dict(Cla4Failure.get("ArtifactSourceState", {})).get(
            "Revision",
            "",
        ))
        == str(Checkout["Revision"])
    )
    CheckoutAfter = ReadDetailedGitState(Configuration.RepositoryRoot)
    SourceAfter = BuildRoutingSourceManifest(Configuration.RepositoryRoot)
    CurrentRuntimeAfter = BuildCurrentRuntimeProvenance(
        Configuration.RepositoryRoot
    )
    if (
        CheckoutAfter != Checkout
        or SourceAfter != Source
        or CurrentRuntimeAfter != CurrentRuntime
    ):
        raise RuntimeError("source/provenance changed during capture")
    TimestampText = CapturedAtUtc.strftime("%Y-%m-%dT%H:%M:%SZ")
    Snapshot = RoutingDesignSnapshot({
        "SchemaVersion": SchemaVersion,
        "CapturedAtUtc": TimestampText,
        "CapturedAtLocal": CapturedAtLocal.isoformat(timespec="seconds"),
        "Generator": Generator,
        "Checkout": Checkout,
        "Source": Source,
        "CurrentRuntimeProvenance": CurrentRuntime,
        "Cla4Failure": Cla4Failure,
        "AcceptanceManifest": AcceptanceManifest,
        "NandDiagram": NandDiagram,
        "Artifacts": Artifacts,
    }, ArtifactObservationsBySnapshotPath=ArtifactObservations)
    ExactDigest = Sha256Bytes(CanonicalJsonBytes(
        BuildExactEvidence(Snapshot)
    ))
    PortableSemanticDigest = Sha256Bytes(CanonicalJsonBytes(
        BuildPortableSemanticEvidence(Snapshot)
    ))
    SnapshotId = (
        CapturedAtUtc.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + PortableSemanticDigest[:16]
    )
    Snapshot["SnapshotId"] = SnapshotId
    Snapshot["ExactEvidenceSha256"] = ExactDigest
    Snapshot["PortableSemanticEvidenceSha256"] = PortableSemanticDigest
    return Snapshot


def RenderSnapshotMarkdown(Snapshot: dict[str, object]) -> str:
    """Render a deterministic human projection of `Snapshot.json`."""
    Checkout = dict(Snapshot["Checkout"])
    Source = dict(Snapshot["Source"])
    Metrics = dict(Source["Metrics"])
    Totals = dict(Metrics["Totals"])
    Runtime = dict(Snapshot["CurrentRuntimeProvenance"])
    PythonRecord = dict(Runtime["Python"])
    NativeRecord = dict(Runtime["LoadedNativeExtension"])
    Failure = dict(Snapshot["Cla4Failure"])
    Deadline = dict(Failure.get("Deadline", {}))
    Lines = [
        "# Routing design snapshot",
        "",
        f"- Snapshot ID: `{Snapshot['SnapshotId']}`",
        f"- Captured UTC: `{Snapshot['CapturedAtUtc']}`",
        f"- Captured local: `{Snapshot['CapturedAtLocal']}`",
        f"- Exact evidence SHA-256: `{Snapshot['ExactEvidenceSha256']}`",
        "- Portable semantic evidence SHA-256: "
        f"`{Snapshot['PortableSemanticEvidenceSha256']}`",
        f"- Revision: `{Checkout['Revision']}`",
        f"- Branch: `{Checkout['Branch']}`",
        f"- Dirty: `{str(Checkout['Dirty']).lower()}`",
        f"- Status SHA-256: `{Checkout['StatusPorcelainSha256']}`",
        "",
        "## Source",
        "",
        f"- Scope: `{Source['ScopeVersion']}`",
        f"- Aggregate SHA-256: `{Source['AggregateSha256']}`",
        f"- Files: `{Totals['FileCount']}`",
        f"- Physical lines: `{Totals['PhysicalLines']}`",
        f"- Nonblank lines: `{Totals['NonBlankLines']}`",
        "",
        "### Largest Python definitions",
        "",
        "| Definition | File | AST span lines |",
        "| --- | --- | ---: |",
    ]
    for Definition in Metrics["LargestPythonDefinitions"][:10]:
        Lines.append(
            f"| `{Definition['QualifiedName']}` | "
            f"`{Definition['Path']}` | "
            f"{Definition['PythonAstSpanLines']} |"
        )
    Lines.extend([
        "",
        "## Current runtime provenance",
        "",
        f"- Python: `{PythonRecord.get('Implementation')} "
        f"{PythonRecord.get('Version')}`",
        f"- Python executable: `{PythonRecord.get('Executable')}`",
        f"- Platform: `{dict(Runtime['Platform']).get('Description')}`",
        f"- Default policy: "
        f"`{dict(Runtime['DefaultRoutingPolicy']).get('PolicyVersion')}`",
        f"- Native extension: `{NativeRecord.get('Path')}`",
        f"- Native SHA-256: `{NativeRecord.get('Sha256')}`",
        f"- Benchmark input aggregate: "
        f"`{dict(Runtime['BenchmarkInputs']).get('AggregateSha256')}`",
        f"- Tracked template aggregate: "
        f"`{dict(Runtime['TrackedTemplates']).get('AggregateSha256')}`",
        "",
    ])
    Acceptance = Snapshot.get("AcceptanceManifest")
    if isinstance(Acceptance, dict):
        CrossChecks = dict(Acceptance.get("CrossChecks", {}))
        Profile = dict(Acceptance.get("AcceptanceProfile", {}))
        Lines.extend([
            "## Native acceptance-manifest evidence",
            "",
            f"- Manifest status: `{Acceptance.get('Status')}`",
            f"- Accepted: `{str(Acceptance.get('Accepted')).lower()}`",
            f"- Matrix: `{Acceptance.get('MatrixMode')}`",
            f"- Profile: `{Profile.get('ProfileId')}`",
            f"- Profile schema: `{Profile.get('ProfileSchemaVersion')}`",
            f"- Profile SHA-256: `{Profile.get('ProfileSha256')}`",
            f"- Profile cases: `{Profile.get('CaseCount')}`",
            f"- Policy: `{Acceptance.get('ExpectedPolicyVersion')}`",
            f"- Source provenance stable: "
            f"`{str(Acceptance.get('SourceProvenanceStable')).lower()}`",
            "",
            "| Cross-check | Result |",
            "| --- | --- |",
        ])
        for Name, Result in sorted(CrossChecks.items()):
            Lines.append(f"| `{Name}` | `{str(Result).lower()}` |")
        Lines.extend([
            "",
            "| Run | Status | Accepted | Backend | Failure stage | Reason |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for Run in Acceptance.get("Runs", []):
            FailureArtifact = Run.get("FailureArtifact") or {}
            BackendState = Run.get("BackendState") or {}
            Lines.append(
                f"| `{Run.get('RunName')}` | `{Run.get('Status')}` | "
                f"`{str(Run.get('Accepted')).lower()}` | "
                f"`{BackendState.get('State')}` | "
                f"`{FailureArtifact.get('Stage')}` | "
                f"`{FailureArtifact.get('Reason')}` |"
            )
    Lines.extend([
        "",
        "## CLA4 failure",
        "",
        f"- Stage: `{Failure.get('Stage')}`",
        f"- Reason: `{Failure.get('Reason')}`",
        f"- Detail: `{Failure.get('Detail')}`",
        f"- Runtime seconds: `{Failure.get('RuntimeSeconds')}`",
        f"- Timed out: `{str(Failure.get('TimedOut')).lower()}`",
        f"- Deadline expired: `{str(Deadline.get('Expired')).lower()}`",
        f"- Remaining milliseconds: `{Deadline.get('RemainingMilliseconds')}`",
        f"- Detailed routing started: "
        f"`{str(Failure.get('DetailedRoutingStarted')).lower()}`",
        "- Success-artifact absence verified: "
        f"`{str(dict(Failure.get('SuccessArtifactAbsence', {})).get('Verified')).lower()}`",
        "",
        "### Placement candidates",
        "",
        "| Generator | Elapsed s | Claims | Conflicts | Signals |",
        "| --- | ---: | ---: | ---: | --- |",
    ])
    for Candidate in Failure.get("CandidateSummary", []):
        Signals = ", ".join(Candidate.get("ConflictSignals", []))
        Lines.append(
            f"| `{Candidate.get('SourceGenerator')}` | "
            f"{Candidate.get('ElapsedSeconds')} | "
            f"{Candidate.get('ClaimCount')} | "
            f"{Candidate.get('ConflictResourceCount')} | "
            f"{Signals} |"
        )
    Lines.extend([
        "",
        "## Copied artifacts",
        "",
        "| Snapshot path | Bytes | SHA-256 |",
        "| --- | ---: | --- |",
    ])
    for Artifact in Snapshot["Artifacts"]:
        Lines.append(
            f"| [{Path(str(Artifact['SnapshotPath'])).name}]"
            f"({Artifact['SnapshotPath']}) | "
            f"{Artifact['SizeBytes']} | `{Artifact['Sha256']}` |"
        )
    Lines.extend([
        "",
        "This snapshot records a typed structural placement failure. It does "
        "not establish CLA4 routing acceptance.",
        "",
    ])
    return "\n".join(Lines)


def ValidateSnapshotIdentities(
    Configuration: SnapshotConfiguration,
    Snapshot: dict[str, object],
) -> None:
    """Reject mutation of schema, timestamps, evidence, or snapshot identity."""
    CapturedAtUtc = Configuration.CapturedAtUtc.astimezone(timezone.utc).replace(
        microsecond=0
    )
    ExpectedCapturedAtUtc = CapturedAtUtc.strftime("%Y-%m-%dT%H:%M:%SZ")
    ExpectedCapturedAtLocal = CapturedAtUtc.astimezone(LocalTimeZone).isoformat(
        timespec="seconds"
    )
    if Snapshot.get("SchemaVersion") != SchemaVersion:
        raise RuntimeError("snapshot evidence identity mismatch")
    if (
        Snapshot.get("CapturedAtUtc") != ExpectedCapturedAtUtc
        or Snapshot.get("CapturedAtLocal") != ExpectedCapturedAtLocal
    ):
        raise RuntimeError("snapshot evidence identity mismatch")
    ExactDigest = Sha256Bytes(CanonicalJsonBytes(
        BuildExactEvidence(Snapshot)
    ))
    PortableSemanticDigest = Sha256Bytes(CanonicalJsonBytes(
        BuildPortableSemanticEvidence(Snapshot)
    ))
    ExpectedSnapshotId = (
        CapturedAtUtc.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + PortableSemanticDigest[:16]
    )
    if (
        Snapshot.get("ExactEvidenceSha256") != ExactDigest
        or Snapshot.get("PortableSemanticEvidenceSha256")
        != PortableSemanticDigest
        or Snapshot.get("SnapshotId") != ExpectedSnapshotId
    ):
        raise RuntimeError("snapshot evidence identity mismatch")


def WriteSnapshotStaged(
    Configuration: SnapshotConfiguration,
    Snapshot: dict[str, object],
) -> Path:
    """Stage a checked bundle and rename it into one fresh final path."""
    CapturedAtUtc = Configuration.CapturedAtUtc.astimezone(timezone.utc).replace(
        microsecond=0
    )
    DirectoryName = CapturedAtUtc.strftime("%Y%m%dT%H%M%SZ")
    OutputRoot = Configuration.OutputRoot.resolve()
    TargetDirectory = OutputRoot / DirectoryName
    if TargetDirectory.exists():
        raise FileExistsError(
            f"snapshot target already exists: {TargetDirectory}"
        )
    ValidateSnapshotIdentities(Configuration, Snapshot)
    CheckoutNow = ReadDetailedGitState(Configuration.RepositoryRoot)
    SourceNow = BuildRoutingSourceManifest(Configuration.RepositoryRoot)
    RuntimeNow = BuildCurrentRuntimeProvenance(
        Configuration.RepositoryRoot
    )
    if (
        CheckoutNow != Snapshot.get("Checkout")
        or SourceNow != Snapshot.get("Source")
        or RuntimeNow != Snapshot.get("CurrentRuntimeProvenance")
    ):
        raise RuntimeError("source/provenance changed during capture")
    OutputRoot.mkdir(parents=True, exist_ok=True)
    TemporaryDirectory = Path(tempfile.mkdtemp(
        prefix=f".{DirectoryName}.",
        dir=OutputRoot,
    ))
    try:
        ResolvedTemporaryDirectory = TemporaryDirectory.resolve()
        ArtifactDirectory = TemporaryDirectory / "Artifacts"
        ArtifactDirectory.mkdir()
        CapturedArtifacts = getattr(
            Snapshot,
            "ArtifactObservationsBySnapshotPath",
            None,
        )
        if not isinstance(CapturedArtifacts, Mapping):
            raise RuntimeError(
                "snapshot publication requires retained artifact observations"
            )
        for Artifact in Snapshot["Artifacts"]:
            SourcePath = Path(str(Artifact["OriginalPath"]))
            RelativeSnapshotPath = Path(str(Artifact["SnapshotPath"]))
            if RelativeSnapshotPath.is_absolute():
                raise ValueError(
                    "snapshot artifact path must be relative: "
                    f"{RelativeSnapshotPath}"
                )
            DestinationPath = (
                TemporaryDirectory / RelativeSnapshotPath
            ).resolve()
            if not DestinationPath.is_relative_to(ResolvedTemporaryDirectory):
                raise ValueError(
                    "snapshot artifact path escapes temporary bundle: "
                    f"{RelativeSnapshotPath}"
                )
            DestinationPath.parent.mkdir(parents=True, exist_ok=True)
            Observation = CapturedArtifacts.get(
                RelativeSnapshotPath.as_posix()
            )
            if not isinstance(Observation, VerifiedFileObservation) or (
                Observation.SizeBytes != Artifact["SizeBytes"]
                or Observation.Sha256 != Artifact["Sha256"]
            ):
                raise RuntimeError(
                    f"captured artifact identity mismatch: {SourcePath}"
                )
            DestinationPath.write_bytes(Observation.Data)
            if Sha256File(DestinationPath) != Artifact["Sha256"]:
                raise RuntimeError(
                    f"copied artifact hash mismatch: {SourcePath}"
                )

        SnapshotJsonPath = TemporaryDirectory / "Snapshot.json"
        SnapshotMarkdownPath = TemporaryDirectory / "Snapshot.md"
        SnapshotJsonPath.write_text(
            PrettyJsonText(Snapshot),
            encoding="utf-8",
            newline="\n",
        )
        SnapshotMarkdownPath.write_text(
            RenderSnapshotMarkdown(Snapshot),
            encoding="utf-8",
            newline="\n",
        )

        HashedPaths = sorted((
            SnapshotJsonPath,
            SnapshotMarkdownPath,
            *ArtifactDirectory.iterdir(),
        ), key=lambda Value: Value.relative_to(TemporaryDirectory).as_posix())
        ChecksumLines = [
            f"{Sha256File(PathValue)}  "
            f"{PathValue.relative_to(TemporaryDirectory).as_posix()}"
            for PathValue in HashedPaths
        ]
        (TemporaryDirectory / "SHA256SUMS").write_text(
            "\n".join(ChecksumLines) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        TemporaryDirectory.replace(TargetDirectory)
    except Exception:
        shutil.rmtree(TemporaryDirectory, ignore_errors=True)
        raise
    return TargetDirectory


def ParseTimestamp(Value: str) -> datetime:
    """Parse a UTC CLI timestamp in compact or ISO filesystem-safe form."""
    for Format in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H-%M-%SZ"):
        try:
            return datetime.strptime(Value, Format).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        "timestamp must be YYYYMMDDTHHMMSSZ or YYYY-MM-DDTHH-MM-SSZ"
    )


def ParseArguments(Arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse one explicit, non-discovering snapshot request."""
    Parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  %(prog)s --cla4-failure Output/Failure.RoutingFailure.json "
            "--artifact Output/Failure.Nand.json\n\n"
            "The source artifact is read-only; output is published only to a "
            "fresh timestamped directory."
        ),
    )
    Parser.add_argument(
        "--output-root",
        type=Path,
        default=DefaultOutputRoot,
        help="fresh timestamped snapshot parent directory",
    )
    Parser.add_argument(
        "--timestamp",
        type=ParseTimestamp,
        help="fixed UTC capture timestamp for reproduction/tests",
    )
    Parser.add_argument(
        "--cla4-failure",
        type=Path,
        help="explicit CarryLookaheadAdder4 routing-failure JSON; omit only with a recorded process-timeout manifest",
    )
    Parser.add_argument(
        "--acceptance-manifest",
        type=Path,
        help="explicit native acceptance manifest to copy and hash",
    )
    Parser.add_argument(
        "--artifact",
        type=Path,
        action="append",
        default=[],
        help="additional explicit evidence file; repeat as needed",
    )
    return Parser.parse_args(Arguments)


def GuidedArguments() -> list[str]:
    """Collect the explicit evidence inputs required for a snapshot."""
    print("RedstoneCompiler routing evidence snapshot")
    Failure = input("CLA4 routing-failure JSON path: ").strip()
    if not Failure:
        raise ValueError("a CLA4 routing-failure JSON path is required")
    OutputRoot = input(
        f"Output root [{DefaultOutputRoot.relative_to(RepositoryRoot)}]: "
    ).strip() or str(DefaultOutputRoot.relative_to(RepositoryRoot))
    Arguments = ["--cla4-failure", Failure, "--output-root", OutputRoot]
    Manifest = input("Acceptance manifest path (optional): ").strip()
    if Manifest:
        Arguments.extend(["--acceptance-manifest", Manifest])
    while True:
        Artifact = input("Additional artifact path (blank to finish): ").strip()
        if not Artifact:
            break
        Arguments.extend(["--artifact", Artifact])
    return Arguments


def Main(Arguments: Sequence[str] | None = None) -> int:
    """Capture and publish one timestamped design snapshot."""
    RawArguments = list(sys.argv[1:] if Arguments is None else Arguments)
    if not RawArguments:
        try:
            RawArguments = GuidedArguments()
        except (EOFError, KeyboardInterrupt):
            print("No snapshot source selected. Run with --help for explicit commands.")
            return 2
        except ValueError as Error:
            raise SystemExit(str(Error)) from Error
    Parsed = ParseArguments(RawArguments)
    CapturedAtUtc = Parsed.timestamp or datetime.now(timezone.utc).replace(
        microsecond=0
    )
    Configuration = SnapshotConfiguration(
        RepositoryRoot=RepositoryRoot,
        OutputRoot=Parsed.output_root,
        CapturedAtUtc=CapturedAtUtc,
        Cla4FailurePath=Parsed.cla4_failure,
        AcceptanceManifestPath=Parsed.acceptance_manifest,
        ArtifactPaths=tuple(Parsed.artifact),
    )
    Snapshot = BuildRoutingDesignSnapshot(Configuration)
    OutputDirectory = WriteSnapshotStaged(Configuration, Snapshot)
    print(OutputDirectory)
    print(f"SnapshotId={Snapshot['SnapshotId']}")
    print(
        "ExactEvidenceSha256="
        f"{Snapshot['ExactEvidenceSha256']}"
    )
    print(
        "PortableSemanticEvidenceSha256="
        f"{Snapshot['PortableSemanticEvidenceSha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())

#!/usr/bin/env python3
"""Run and judge the router-v10 physical acceptance matrix sequentially."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isclose, isfinite
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
from time import monotonic
from typing import Any, Callable


RepositoryRoot = Path(__file__).resolve().parents[1]
# The compiler's routing deadline begins at placement-flow entry, while this
# process timer also includes frontend startup and typed-artifact publication.
# This capture-only grace never changes the per-case acceptance ceiling.
SubprocessDeadlineGraceSeconds = 2.0
DefaultRoutingPublicationReserveSeconds = SubprocessDeadlineGraceSeconds
MaximumDeadlineOverrunSeconds = 1.0
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))


@dataclass(frozen=True)
class AcceptanceCase:
    """One circuit and its immutable acceptance requirements."""

    Name: str
    ExamplePath: Path
    TopModule: str
    RequiredRuns: int
    TruthTableRows: int
    RuntimeCeilingSeconds: float
    MaximumOverflowPeak: int = 1
    PublicationReserveSeconds: float = (
        DefaultRoutingPublicationReserveSeconds
    )

    def __post_init__(self) -> None:
        if (
            not isfinite(self.RuntimeCeilingSeconds)
            or self.RuntimeCeilingSeconds <= 0.0
        ):
            raise ValueError("runtime ceiling must be finite and positive")
        if (
            not isfinite(self.PublicationReserveSeconds)
            or self.PublicationReserveSeconds <= 0.0
        ):
            raise ValueError("publication reserve must be finite and positive")
        if self.PublicationReserveSeconds >= self.RuntimeCeilingSeconds:
            raise ValueError(
                "publication reserve must be below the runtime ceiling"
            )

    @property
    def RoutingDeadlineSeconds(self) -> float:
        """Return the bounded router budget inside the wall-clock ceiling."""
        return self.RuntimeCeilingSeconds - self.PublicationReserveSeconds

    def ToDictionary(self) -> dict[str, object]:
        Result = asdict(self)
        Result["ExamplePath"] = str(self.ExamplePath)
        Result["RoutingDeadlineSeconds"] = self.RoutingDeadlineSeconds
        return Result


AcceptanceCases = (
    AcceptanceCase(
        Name="FullAdder",
        ExamplePath=Path("Examples/FullAdder.sv"),
        TopModule="FullAdder",
        RequiredRuns=5,
        TruthTableRows=8,
        RuntimeCeilingSeconds=10.0,
    ),
    AcceptanceCase(
        Name="RippleCarryAdder4",
        ExamplePath=Path("Examples/RippleCarryAdder4.sv"),
        TopModule="RippleCarryAdder4",
        RequiredRuns=2,
        TruthTableRows=512,
        RuntimeCeilingSeconds=25.0,
    ),
    AcceptanceCase(
        Name="CarryLookaheadAdder4",
        ExamplePath=Path("Examples/CarryLookaheadAdder4.sv"),
        TopModule="CarryLookaheadAdder4",
        RequiredRuns=2,
        TruthTableRows=512,
        RuntimeCeilingSeconds=120.0,
    ),
)

RouteMetricFields = (
    "Length",
    "Bends",
    "Vias",
    "ReroutedNets",
    "RoutingPasses",
    "Conflicts",
    "OverflowPeak",
    "AccessOverflowPeak",
    "AccessOverflowCells",
    "PerNetLength",
    "MaximumNetLengthShare",
)


@dataclass(frozen=True)
class AcceptanceConfiguration:
    """Resolved paths and controls for one sequential acceptance session."""

    RepositoryRoot: Path
    OutputRoot: Path
    DateLabel: str
    PythonExecutable: Path
    DryRun: bool = False
    RoutingThreads: int | None = None
    ExpectedSeed: int = 0

    @property
    def RecoveryRoot(self) -> Path:
        return (
            self.OutputRoot
            / self.DateLabel
            / "RouterV10Recovery"
        ).resolve(strict=False)

    @property
    def ManifestPath(self) -> Path:
        return self.RecoveryRoot / "AcceptanceManifest.json"


@dataclass(frozen=True)
class AcceptanceCommandResult:
    """Captured result of one compiler process."""

    ReturnCode: int
    Stdout: str
    Stderr: str
    RuntimeSeconds: float
    TimedOut: bool = False


def UtcNow() -> str:
    return datetime.now(timezone.utc).isoformat()


def DecodeProcessText(Value: object) -> str:
    if Value is None:
        return ""
    if isinstance(Value, bytes):
        return Value.decode("utf-8", errors="replace")
    return str(Value)


def RunCompilerCommand(
    *,
    Command: list[str],
    WorkingDirectory: Path,
    Environment: dict[str, str],
    TimeoutSeconds: float,
) -> AcceptanceCommandResult:
    """Run one compiler process with a hard wall-clock ceiling."""
    Started = monotonic()
    try:
        Completed = subprocess.run(
            Command,
            cwd=WorkingDirectory,
            env=Environment,
            capture_output=True,
            text=True,
            timeout=TimeoutSeconds,
            check=False,
        )
        return AcceptanceCommandResult(
            ReturnCode=Completed.returncode,
            Stdout=Completed.stdout,
            Stderr=Completed.stderr,
            RuntimeSeconds=monotonic() - Started,
        )
    except subprocess.TimeoutExpired as Error:
        return AcceptanceCommandResult(
            ReturnCode=124,
            Stdout=DecodeProcessText(Error.stdout),
            Stderr=(
                DecodeProcessText(Error.stderr)
                + "\nAcceptance harness terminated the run at its "
                + f"{TimeoutSeconds:.3f}s wall-clock ceiling.\n"
            ),
            RuntimeSeconds=monotonic() - Started,
            TimedOut=True,
        )


def ReadSourceState(RepoRoot: Path) -> dict[str, object]:
    """Read the exact Git revision and dirty marker without changing the tree."""
    try:
        Revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=RepoRoot,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        Status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=RepoRoot,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return {"Revision": Revision, "Dirty": bool(Status.strip())}
    except (OSError, subprocess.SubprocessError):
        return {
            "Revision": os.environ.get("RC_SOURCE_REVISION", "unknown"),
            "Dirty": os.environ.get("RC_SOURCE_DIRTY", "unknown"),
        }


def BuildEnvironmentRecord(
    Configuration: AcceptanceConfiguration,
) -> dict[str, object]:
    """Capture reproducibility settings without copying unrelated secrets."""
    RoutingEnvironment = {
        Name: Value
        for Name, Value in sorted(os.environ.items())
        if Name.startswith("RC_") or Name.startswith("RCS_")
    }
    RoutingEnvironment["PYTHONHASHSEED"] = str(Configuration.ExpectedSeed)
    if Configuration.RoutingThreads is not None:
        RoutingEnvironment["RC_ROUTING_THREADS"] = str(
            Configuration.RoutingThreads
        )
    return {
        "PythonVersion": platform.python_version(),
        "PythonExecutable": str(Configuration.PythonExecutable),
        "Platform": platform.platform(),
        "WorkingDirectory": str(Configuration.RepositoryRoot),
        "PolicySeed": Configuration.ExpectedSeed,
        "RoutingEnvironment": RoutingEnvironment,
    }


def BuildChildEnvironment(
    Configuration: AcceptanceConfiguration,
) -> dict[str, str]:
    Result = dict(os.environ)
    Result["PYTHONHASHSEED"] = str(Configuration.ExpectedSeed)
    if Configuration.RoutingThreads is not None:
        Result["RC_ROUTING_THREADS"] = str(Configuration.RoutingThreads)
    return Result


def BuildRunArtifacts(RunDirectory: Path, RunName: str) -> dict[str, Path]:
    OutputPath = RunDirectory / f"{RunName}.litematic"
    return {
        "RunDirectory": RunDirectory,
        "Schematic": OutputPath,
        "TruthTable": OutputPath.with_suffix(".TruthTable.txt"),
        "PhysicalDesign": OutputPath.with_suffix(".PhysicalDesign.json"),
        "RoutingFailure": OutputPath.with_suffix(".RoutingFailure.json"),
        "Diagram": OutputPath.with_suffix(".Nand.json"),
        "Dot": OutputPath.with_suffix(".Nand.dot"),
        "Stdout": RunDirectory / "stdout.log",
        "Stderr": RunDirectory / "stderr.log",
        "Workdir": RunDirectory / "Frontend",
    }


def BuildCompilerCommand(
    Configuration: AcceptanceConfiguration,
    Case: AcceptanceCase,
    RunName: str,
    Artifacts: dict[str, Path],
) -> list[str]:
    Command = [
        str(Configuration.PythonExecutable),
        str(Configuration.RepositoryRoot / "Main.py"),
        "--input",
        str((Configuration.RepositoryRoot / Case.ExamplePath).resolve()),
        "--topmodule",
        Case.TopModule,
        "--output",
        str(Artifacts["RunDirectory"]),
        "--outputname",
        RunName,
        "--diagram",
        str(Artifacts["Diagram"]),
        "--workdir",
        str(Artifacts["Workdir"]),
        "--routing-strategy",
        "new-router-first",
        "--routing-deadline-seconds",
        str(Case.RoutingDeadlineSeconds),
    ]
    if Configuration.RoutingThreads is not None:
        Command.extend(
            ["--routing-threads", str(Configuration.RoutingThreads)]
        )
    return Command


def BuildFileRecord(Value: Path) -> dict[str, object]:
    Result: dict[str, object] = {
        "Path": str(Value.resolve(strict=False)),
        "Exists": Value.is_file(),
    }
    if Value.is_file():
        Data = Value.read_bytes()
        Result.update({
            "SizeBytes": len(Data),
            "Sha256": sha256(Data).hexdigest(),
        })
    return Result


def CanonicalizeNbt(Value: object) -> object:
    """Convert parsed NBT values into stable JSON-compatible structures."""
    if hasattr(Value, "TagType") and hasattr(Value, "Value"):
        return {
            "TagType": int(getattr(Value, "TagType")),
            "Value": CanonicalizeNbt(getattr(Value, "Value")),
        }
    if isinstance(Value, dict):
        return {
            str(Key): CanonicalizeNbt(Item)
            for Key, Item in sorted(Value.items(), key=lambda Pair: str(Pair[0]))
        }
    if isinstance(Value, tuple):
        return [CanonicalizeNbt(Item) for Item in Value]
    if isinstance(Value, list):
        return [CanonicalizeNbt(Item) for Item in Value]
    if isinstance(Value, bytes):
        return {"BytesHex": Value.hex()}
    return Value


def BuildEmittedDesignDigest(SchematicPath: Path) -> str:
    """Hash the emitted regions while excluding timestamps and output names."""
    from SchemEncoder.Writer262 import ReadNbt

    Root = ReadNbt(SchematicPath)
    Regions = Root.get("Regions")
    if Regions is None:
        raise ValueError("litematic contains no Regions compound")
    Encoded = json.dumps(
        CanonicalizeNbt(Regions),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(Encoded).hexdigest()


def ReadNested(Value: object, *Names: str) -> object:
    Current = Value
    for Name in Names:
        if not isinstance(Current, dict) or Name not in Current:
            return None
        Current = Current[Name]
    return Current


def EvaluateRun(
    *,
    Case: AcceptanceCase,
    Process: AcceptanceCommandResult,
    Artifacts: dict[str, Path],
    ExpectedSeed: int,
    DesignDigestBuilder: Callable[[Path], str] = BuildEmittedDesignDigest,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Judge one completed process solely from its result and durable artifacts."""
    Failures: list[str] = []
    ProcessEnvelopeSeconds = (
        Case.RoutingDeadlineSeconds + Case.PublicationReserveSeconds
    )
    ProcessEnvelopeValid = (
        isfinite(Case.RoutingDeadlineSeconds)
        and Case.RoutingDeadlineSeconds > 0.0
        and Case.RoutingDeadlineSeconds < Case.RuntimeCeilingSeconds
        and isclose(
            ProcessEnvelopeSeconds,
            Case.RuntimeCeilingSeconds,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    )
    if not ProcessEnvelopeValid:
        Failures.append("invalid routing deadline/publication envelope")
    DeadlineOverrunSeconds = max(
        0.0,
        Process.RuntimeSeconds - Case.RuntimeCeilingSeconds,
    )
    DeadlineOverrunWithinLimit = (
        DeadlineOverrunSeconds < MaximumDeadlineOverrunSeconds
    )
    if Process.TimedOut:
        Failures.append("process timed out")
    if Process.ReturnCode != 0:
        Failures.append(f"process exited {Process.ReturnCode}")
    if Process.RuntimeSeconds > Case.RuntimeCeilingSeconds:
        Failures.append(
            "wall runtime exceeded ceiling: "
            f"{Process.RuntimeSeconds:.6f}s > {Case.RuntimeCeilingSeconds:.6f}s"
        )
    if not DeadlineOverrunWithinLimit:
        Failures.append(
            "deadline overrun did not stay below "
            f"{MaximumDeadlineOverrunSeconds:.6f}s: "
            f"{DeadlineOverrunSeconds:.6f}s"
        )

    for Name in ("Schematic", "TruthTable", "PhysicalDesign"):
        if not Artifacts[Name].is_file():
            Failures.append(f"missing required artifact: {Name}")
    if Artifacts["RoutingFailure"].is_file():
        Failures.append("routing failure artifact exists")

    PhysicalDocument: dict[str, object] | None = None
    if Artifacts["PhysicalDesign"].is_file():
        try:
            Loaded = json.loads(
                Artifacts["PhysicalDesign"].read_text(encoding="utf-8")
            )
            if not isinstance(Loaded, dict):
                raise ValueError("physical design root is not an object")
            PhysicalDocument = Loaded
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as Error:
            Failures.append(f"invalid PhysicalDesign JSON: {Error}")

    Evidence: dict[str, object] | None = None
    Observed: dict[str, object] = {}
    if PhysicalDocument is not None:
        Strategy = ReadNested(PhysicalDocument, "Strategy")
        if not isinstance(Strategy, dict):
            Failures.append("missing Strategy evidence")
            Strategy = {}
        if Strategy.get("Requested") != "new-router-first":
            Failures.append("requested strategy is not new-router-first")
        if Strategy.get("Used") != "new-router-first":
            Failures.append("used strategy is not new-router-first")
        if Strategy.get("FallbackUsed") is not False:
            Failures.append("fallback was used or not explicitly disabled")

        RouterReliability = ReadNested(PhysicalDocument, "RouterReliability")
        if not isinstance(RouterReliability, dict):
            Failures.append("missing RouterReliability evidence")
            RouterReliability = {}
        if RouterReliability.get("RunVerdict") != "ROUTED_AND_SIMULATED":
            Failures.append("successful router reliability verdict is missing")

        PolicySeed = ReadNested(PhysicalDocument, "Policy", "Seed")
        PolicyVersion = ReadNested(
            PhysicalDocument,
            "Policy",
            "PolicyVersion",
        )
        if PolicyVersion != "physical-design-v10-routability-feedback":
            Failures.append(
                "policy version is not physical-design-v10-routability-feedback"
            )
        if PolicySeed != ExpectedSeed:
            Failures.append(
                f"policy seed mismatch: {PolicySeed!r} != {ExpectedSeed!r}"
            )

        RunSummary = ReadNested(PhysicalDocument, "RunSummary")
        if not isinstance(RunSummary, dict):
            Failures.append("missing RunSummary evidence")
            RunSummary = {}
        PhysicalRuntime = RunSummary.get("RuntimeSeconds")
        if not isinstance(PhysicalRuntime, (int, float)) or isinstance(
            PhysicalRuntime, bool
        ):
            Failures.append("missing numeric RunSummary.RuntimeSeconds")
        elif float(PhysicalRuntime) > Case.RuntimeCeilingSeconds:
            Failures.append(
                "reported runtime exceeded ceiling: "
                f"{float(PhysicalRuntime):.6f}s > "
                f"{Case.RuntimeCeilingSeconds:.6f}s"
            )
        if RunSummary.get("TruthTablePassed") is not True:
            Failures.append("truth table did not pass")
        if RunSummary.get("TruthTableRows") != Case.TruthTableRows:
            Failures.append(
                "truth-table row count mismatch: "
                f"{RunSummary.get('TruthTableRows')!r} != {Case.TruthTableRows}"
            )
        if RunSummary.get("SimulationBackend") not in {
            "python",
            "native-parallel",
        }:
            Failures.append("simulation backend is missing or non-authoritative")
        if RunSummary.get("Conflicts") != 0:
            Failures.append("RunSummary.Conflicts is not zero")
        OverflowPeak = RunSummary.get("OverflowPeak")
        if not isinstance(OverflowPeak, int) or isinstance(OverflowPeak, bool):
            Failures.append("missing integer RunSummary.OverflowPeak")
        elif OverflowPeak > Case.MaximumOverflowPeak:
            Failures.append(
                f"overflow peak exceeded {Case.MaximumOverflowPeak}: {OverflowPeak}"
            )

        FinalValidation = ReadNested(PhysicalDocument, "FinalValidation")
        if not isinstance(FinalValidation, dict):
            Failures.append("missing FinalValidation evidence")
            FinalValidation = {}
        if FinalValidation.get("ValidationMode") != "authoritative-exact":
            Failures.append("final validation mode is not authoritative-exact")
        if FinalValidation.get("ZeroConflicts") is not True:
            Failures.append("FinalValidation.ZeroConflicts is not true")
        if FinalValidation.get("ConflictCount") != 0:
            Failures.append("FinalValidation.ConflictCount is not zero")
        if FinalValidation.get("UnresolvedClaimCount") != 0:
            Failures.append("unresolved claim count is not zero")
        if FinalValidation.get("UnresolvedClaims") != []:
            Failures.append("unresolved claim list is not empty")

        PlacementFingerprint = ReadNested(
            RouterReliability,
            "Fingerprints",
            "Placement",
        )
        if not isinstance(PlacementFingerprint, str) or not PlacementFingerprint:
            Failures.append("missing placement fingerprint")

        Ownership = ReadNested(
            PhysicalDocument,
            "RoutingResourceGraph",
            "OwnershipCounts",
        )
        if not isinstance(Ownership, dict):
            Failures.append("missing resource ownership counts")

        MissingRouteMetrics = [
            Name for Name in RouteMetricFields if Name not in RunSummary
        ]
        if MissingRouteMetrics:
            Failures.append(
                "missing route metrics: " + ", ".join(MissingRouteMetrics)
            )
        RouteMetrics = {
            Name: RunSummary.get(Name) for Name in RouteMetricFields
        }

        DesignDigest = None
        if Artifacts["Schematic"].is_file():
            try:
                DesignDigest = DesignDigestBuilder(Artifacts["Schematic"])
            except Exception as Error:
                Failures.append(f"could not digest emitted design: {Error}")

        Observed = {
            "ReportedRuntimeSeconds": PhysicalRuntime,
            "TruthTablePassed": RunSummary.get("TruthTablePassed"),
            "TruthTableRows": RunSummary.get("TruthTableRows"),
            "Conflicts": RunSummary.get("Conflicts"),
            "UnresolvedClaimCount": FinalValidation.get(
                "UnresolvedClaimCount"
            ),
            "OverflowPeak": OverflowPeak,
            "PolicySeed": PolicySeed,
        }
        if (
            isinstance(PlacementFingerprint, str)
            and PlacementFingerprint
            and isinstance(Ownership, dict)
            and not MissingRouteMetrics
            and isinstance(DesignDigest, str)
            and DesignDigest
        ):
            Evidence = {
                "PlacementFingerprint": PlacementFingerprint,
                "OwnershipCounts": Ownership,
                "RouteMetrics": RouteMetrics,
                "EmittedDesignSha256": DesignDigest,
            }

    ArtifactRecords = {
        Name: BuildFileRecord(PathValue)
        for Name, PathValue in Artifacts.items()
        if Name != "RunDirectory"
    }
    Result = {
        "Accepted": not Failures,
        "Failures": Failures,
        "Process": {
            "ReturnCode": Process.ReturnCode,
            "TimedOut": Process.TimedOut,
            "WallRuntimeSeconds": round(Process.RuntimeSeconds, 6),
            "RuntimeCeilingSeconds": Case.RuntimeCeilingSeconds,
            "RequestedRoutingDeadlineSeconds": (
                Case.RoutingDeadlineSeconds
            ),
            "PublicationReserveSeconds": Case.PublicationReserveSeconds,
            "ProcessEnvelopeSeconds": ProcessEnvelopeSeconds,
            "ProcessEnvelopeValid": ProcessEnvelopeValid,
            "DeadlineOverrunSeconds": round(DeadlineOverrunSeconds, 6),
            "MaximumDeadlineOverrunSeconds": MaximumDeadlineOverrunSeconds,
            "DeadlineOverrunWithinLimit": DeadlineOverrunWithinLimit,
        },
        "Observed": Observed,
        "Artifacts": ArtifactRecords,
    }
    return Result, Evidence


def ClearPriorRunArtifacts(Artifacts: dict[str, Path]) -> None:
    """Remove only exact prior outputs that could masquerade as this run."""
    for Name in (
        "Schematic",
        "TruthTable",
        "PhysicalDesign",
        "RoutingFailure",
        "Diagram",
        "Dot",
    ):
        Artifacts[Name].unlink(missing_ok=True)


def WriteManifest(PathValue: Path, Manifest: dict[str, object]) -> None:
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    TemporaryPath = PathValue.with_suffix(".json.tmp")
    TemporaryPath.write_text(
        json.dumps(Manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    TemporaryPath.replace(PathValue)


def BuildPlannedRuns(
    Configuration: AcceptanceConfiguration,
) -> list[dict[str, object]]:
    Runs = []
    Sequence = 0
    for Case in AcceptanceCases:
        for RunIndex in range(1, Case.RequiredRuns + 1):
            Sequence += 1
            RunName = f"{Case.Name}Run{RunIndex}"
            RunDirectory = Configuration.RecoveryRoot / RunName
            Artifacts = BuildRunArtifacts(RunDirectory, RunName)
            Command = BuildCompilerCommand(
                Configuration,
                Case,
                RunName,
                Artifacts,
            )
            Runs.append({
                "Sequence": Sequence,
                "RunName": RunName,
                "Circuit": Case.Name,
                "Repetition": RunIndex,
                "Requirements": Case.ToDictionary(),
                "RequestedRoutingDeadlineSeconds": (
                    Case.RoutingDeadlineSeconds
                ),
                "PublicationReserveSeconds": (
                    Case.PublicationReserveSeconds
                ),
                "Command": Command,
                "CommandText": shlex.join(Command),
                "ArtifactPaths": {
                    Name: str(Value.resolve(strict=False))
                    for Name, Value in Artifacts.items()
                },
                "Status": "PLANNED",
                "Accepted": False,
            })
    return Runs


def RunAcceptance(
    Configuration: AcceptanceConfiguration,
    *,
    CommandRunner: Callable[..., AcceptanceCommandResult] = RunCompilerCommand,
    SourceStateProvider: Callable[[Path], dict[str, object]] = ReadSourceState,
    DesignDigestBuilder: Callable[[Path], str] = BuildEmittedDesignDigest,
    UtcNowProvider: Callable[[], str] = UtcNow,
) -> dict[str, object]:
    """Execute the immutable matrix serially and persist a complete manifest."""
    PlannedRuns = BuildPlannedRuns(Configuration)
    Manifest: dict[str, object] = {
        "SchemaVersion": "router-acceptance-manifest-v1",
        "Status": "DRY_RUN" if Configuration.DryRun else "RUNNING",
        "Accepted": False,
        "ExecutionMode": "sequential",
        "RoutingDeadlinePolicy": {
            "Mode": "wall-ceiling-minus-publication-reserve",
            "DefaultPublicationReserveSeconds": (
                DefaultRoutingPublicationReserveSeconds
            ),
            "WallRuntimeCeilingsUnchanged": True,
        },
        "SubprocessDeadlineGraceSeconds": SubprocessDeadlineGraceSeconds,
        "MaximumDeadlineOverrunSeconds": MaximumDeadlineOverrunSeconds,
        "StartedAtUtc": UtcNowProvider(),
        "CompletedAtUtc": None,
        "RecoveryRoot": str(Configuration.RecoveryRoot),
        "ManifestPath": str(Configuration.ManifestPath),
        "SourceState": SourceStateProvider(Configuration.RepositoryRoot),
        "Environment": BuildEnvironmentRecord(Configuration),
        "Cases": [Case.ToDictionary() for Case in AcceptanceCases],
        "Runs": PlannedRuns,
    }
    WriteManifest(Configuration.ManifestPath, Manifest)
    if Configuration.DryRun:
        Manifest["CompletedAtUtc"] = UtcNowProvider()
        WriteManifest(Configuration.ManifestPath, Manifest)
        return Manifest

    Baselines: dict[str, tuple[str, dict[str, object]]] = {}
    ChildEnvironment = BuildChildEnvironment(Configuration)
    CompletedRuns: list[dict[str, object]] = []
    for Planned in PlannedRuns:
        Case = next(
            Value for Value in AcceptanceCases
            if Value.Name == Planned["Circuit"]
        )
        RunName = str(Planned["RunName"])
        RunDirectory = Configuration.RecoveryRoot / RunName
        Artifacts = BuildRunArtifacts(RunDirectory, RunName)
        RunDirectory.mkdir(parents=True, exist_ok=True)
        ClearPriorRunArtifacts(Artifacts)
        Command = list(Planned["Command"])
        StartedAtUtc = UtcNowProvider()
        try:
            Process = CommandRunner(
                Command=Command,
                WorkingDirectory=Configuration.RepositoryRoot,
                Environment=ChildEnvironment,
                TimeoutSeconds=(
                    Case.RuntimeCeilingSeconds
                    + SubprocessDeadlineGraceSeconds
                ),
            )
        except Exception as Error:
            Process = AcceptanceCommandResult(
                ReturnCode=127,
                Stdout="",
                Stderr=f"acceptance command runner failed: {Error}\n",
                RuntimeSeconds=0.0,
            )
        Artifacts["Stdout"].write_text(Process.Stdout, encoding="utf-8")
        Artifacts["Stderr"].write_text(Process.Stderr, encoding="utf-8")
        Evaluation, Evidence = EvaluateRun(
            Case=Case,
            Process=Process,
            Artifacts=Artifacts,
            ExpectedSeed=Configuration.ExpectedSeed,
            DesignDigestBuilder=DesignDigestBuilder,
        )

        Determinism = {
            "BaselineRun": None,
            "MatchesBaseline": False,
            "MismatchFields": [],
            "Evidence": Evidence,
        }
        if Evidence is not None:
            Baseline = Baselines.get(Case.Name)
            if Baseline is None:
                Baselines[Case.Name] = (RunName, Evidence)
                Determinism.update({
                    "BaselineRun": RunName,
                    "MatchesBaseline": True,
                })
            else:
                BaselineName, BaselineEvidence = Baseline
                MismatchFields = [
                    Name
                    for Name in (
                        "PlacementFingerprint",
                        "OwnershipCounts",
                        "RouteMetrics",
                        "EmittedDesignSha256",
                    )
                    if Evidence.get(Name) != BaselineEvidence.get(Name)
                ]
                Determinism.update({
                    "BaselineRun": BaselineName,
                    "MatchesBaseline": not MismatchFields,
                    "MismatchFields": MismatchFields,
                })
                for Name in MismatchFields:
                    Evaluation["Failures"].append(
                        f"determinism mismatch: {Name}"
                    )
                Evaluation["Accepted"] = not Evaluation["Failures"]

        Completed = {
            **Planned,
            "Status": "PASSED" if Evaluation["Accepted"] else "FAILED",
            "Accepted": Evaluation["Accepted"],
            "StartedAtUtc": StartedAtUtc,
            "CompletedAtUtc": UtcNowProvider(),
            "EnvironmentOverrides": {
                "PYTHONHASHSEED": ChildEnvironment["PYTHONHASHSEED"],
                **(
                    {
                        "RC_ROUTING_THREADS": ChildEnvironment[
                            "RC_ROUTING_THREADS"
                        ]
                    }
                    if "RC_ROUTING_THREADS" in ChildEnvironment
                    else {}
                ),
            },
            "Evaluation": Evaluation,
            "Determinism": Determinism,
        }
        CompletedRuns.append(Completed)
        Manifest["Runs"] = [
            *CompletedRuns,
            *PlannedRuns[len(CompletedRuns):],
        ]
        WriteManifest(Configuration.ManifestPath, Manifest)

    Manifest["Runs"] = CompletedRuns
    Manifest["Accepted"] = (
        len(CompletedRuns) == sum(
            Case.RequiredRuns for Case in AcceptanceCases
        )
        and all(bool(Run["Accepted"]) for Run in CompletedRuns)
    )
    Manifest["Status"] = "PASSED" if Manifest["Accepted"] else "FAILED"
    Manifest["CompletedAtUtc"] = UtcNowProvider()
    WriteManifest(Configuration.ManifestPath, Manifest)
    return Manifest


def DefaultPythonExecutable(RepoRoot: Path) -> Path:
    VirtualEnvironmentPython = RepoRoot / ".venv" / "bin" / "python"
    if VirtualEnvironmentPython.is_file():
        return VirtualEnvironmentPython.resolve()
    return Path(sys.executable).resolve()


def ParseDateLabel(Value: str) -> str:
    try:
        return date.fromisoformat(Value).isoformat()
    except ValueError as Error:
        raise argparse.ArgumentTypeError(
            "date must use YYYY-MM-DD"
        ) from Error


def BuildParser() -> argparse.ArgumentParser:
    Parser = argparse.ArgumentParser(
        description=(
            "Run the fixed router-v10 acceptance matrix sequentially and "
            "write a machine-readable evidence manifest."
        )
    )
    Parser.add_argument(
        "--date",
        dest="DateLabel",
        type=ParseDateLabel,
        default=date.today().isoformat(),
        help="dated acceptance directory (default: today)",
    )
    Parser.add_argument(
        "--output-root",
        dest="OutputRoot",
        type=Path,
        default=Path("Output/Acceptance"),
    )
    Parser.add_argument(
        "--python",
        dest="PythonExecutable",
        type=Path,
        default=None,
        help="compiler Python executable (default: repo .venv or current Python)",
    )
    Parser.add_argument(
        "--routing-threads",
        dest="RoutingThreads",
        type=int,
        default=None,
    )
    Parser.add_argument(
        "--dry-run",
        dest="DryRun",
        action="store_true",
        help="write the complete nine-run plan without launching the compiler",
    )
    return Parser


def Main(Arguments: list[str] | None = None) -> int:
    Parsed = BuildParser().parse_args(Arguments)
    if Parsed.RoutingThreads is not None and Parsed.RoutingThreads <= 0:
        raise SystemExit("--routing-threads must be positive")
    RepoRoot = RepositoryRoot.resolve()
    PythonExecutable = (
        Parsed.PythonExecutable.resolve()
        if Parsed.PythonExecutable is not None
        else DefaultPythonExecutable(RepoRoot)
    )
    OutputRoot = (
        Parsed.OutputRoot
        if Parsed.OutputRoot.is_absolute()
        else RepoRoot / Parsed.OutputRoot
    ).resolve(strict=False)
    Configuration = AcceptanceConfiguration(
        RepositoryRoot=RepoRoot,
        OutputRoot=OutputRoot,
        DateLabel=Parsed.DateLabel,
        PythonExecutable=PythonExecutable,
        DryRun=Parsed.DryRun,
        RoutingThreads=Parsed.RoutingThreads,
    )
    Manifest = RunAcceptance(Configuration)
    print(f"Acceptance manifest: {Configuration.ManifestPath}")
    print(f"Acceptance status: {Manifest['Status']}")
    if Configuration.DryRun:
        return 0
    return 0 if Manifest["Accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(Main())

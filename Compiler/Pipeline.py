"""Pipeline orchestration for end-to-end compilation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import os
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, Callable

from SVDecoder import Sv
from .Synthesis.Diagram import WriteNandDiagram
from .Synthesis.LogicOptimization import OptimizeLogic
from .Synthesis.NandTransform import ToNandOnly
from .Synthesis.Validation import ValidateNandOnlyDesign
from Compiler.Placement.Flow.Candidates import ApplyRoutingRuntimeBudget
from Compiler.Placement.Flow.Results import PcbProgress
from Compiler.Placement.Flow.Runner import PlaceAndRoutePcb
from .FabricServer import (
    BuildExpectedVectors,
    BuildFabricFixture,
    CaptureServerUpdatedLitematic,
    FabricServerConfiguration,
    FabricServerSnapshotArtifact,
    FabricServerSupervisor,
    FabricServerValidationResult,
    WriteFabricFixture,
)
from SchemEncoder import SchemWriter
from SchemEncoder.SchemWriter import BlockCompositionMetrics
from .Routing.ChannelPlanner import RoutingStageMetrics
from .Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from .Routing.Policy import (
    ExecutionStrategyForRequest,
    PhysicalDesignPolicy,
    PolicyForRoutingStrategy,
    RoutingStrategy,
)
from .Routing.Reliability import BuildStableFingerprint
from .Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)


@dataclass
class CompileResult:
    """Result returned by a full compile run."""

    OutputPath: Path
    DiagramPath: Path
    NandGateCount: int
    Stages: list[str]
    EstimatedBlocks: int
    Width: int
    Depth: int
    OriginalLogicGateCount: int
    OptimizedLogicGateCount: int
    FabricServerValidation: FabricServerValidationResult
    RoutingMetrics: RoutingStageMetrics | None
    PhysicalDesignPath: Path
    RequestedStrategy: str
    UsedStrategy: str
    FallbackUsed: bool
    FallbackReason: str | None
    RuntimeSeconds: float
    MaximumNetLengthShare: float
    BlockComposition: BlockCompositionMetrics


def _JsonDiagnosticDefault(Value: object) -> object:
    if hasattr(Value, "value"):
        return getattr(Value, "value")
    if isinstance(Value, Path):
        return str(Value)
    if isinstance(Value, (set, frozenset, tuple)):
        return list(Value)
    return str(Value)


def _JsonDiagnosticValue(Value: object) -> object:
    """Recursively make diagnostic mapping keys JSON-compatible."""
    if isinstance(Value, dict):
        return {
            (
                Key
                if isinstance(Key, (str, int, float, bool)) or Key is None
                else str(Key)
            ): _JsonDiagnosticValue(Item)
            for Key, Item in Value.items()
        }
    if isinstance(Value, (list, tuple, set, frozenset)):
        return [_JsonDiagnosticValue(Item) for Item in Value]
    return Value


RoutingFailureArtifactAggregateDiagnosticKeys = frozenset({
    "AdaptiveEscalationHistory",
    "EscalationHistory",
    "PlacementAttempts",
    "RoutingEscalationState",
})


def RequireFabricServerValidation(
    Result: FabricServerValidationResult,
) -> None:
    """Reject an artifact unless the authoritative server observed a pass."""
    if Result.Status != "passed":
        Detail = Result.Diagnostics.get("Error") or Result.Diagnostics.get("Reason")
        raise ValueError(
            "FabricServerValidation:"
            f"{Result.Status}"
            + (f":{Detail}" if Detail else "")
        )


def BuildRoutingFailureArtifactSnapshot(
    Failure: RoutingFailure,
) -> dict[str, object]:
    """Return one failure without duplicating top-level aggregate evidence."""
    Snapshot = Failure.ToDictionary()
    Diagnostics = Snapshot.get("Diagnostics")
    if isinstance(Diagnostics, dict):
        Snapshot["Diagnostics"] = {
            Key: Value
            for Key, Value in Diagnostics.items()
            if Key not in RoutingFailureArtifactAggregateDiagnosticKeys
        }
    return Snapshot


def ReadSourceState() -> dict[str, object]:
    """Read the current revision and dirty marker without mutating the tree."""
    RepositoryRoot = Path(__file__).resolve().parent.parent
    try:
        Revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=RepositoryRoot,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        DirtyOutput = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
            cwd=RepositoryRoot,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return {
            "Revision": Revision,
            "Dirty": bool(DirtyOutput.strip()),
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "Revision": os.environ.get("RC_SOURCE_REVISION", "unknown"),
            "Dirty": os.environ.get("RC_SOURCE_DIRTY", "unknown"),
        }


def NormalizeArtifactPath(Value: Path | str | None) -> str | None:
    """Return a stable absolute path without requiring the target to exist."""
    if Value is None:
        return None
    return str(Path(Value).expanduser().resolve(strict=False))


def SuccessArtifactPaths(OutputPath: Path) -> dict[str, Path]:
    """Return every artifact whose presence means a compile was successful."""
    return {
        "Schematic": OutputPath,
        "PhysicalDesign": OutputPath.with_suffix(".PhysicalDesign.json"),
        "FabricFixture": OutputPath.with_suffix(".FabricFixture.json"),
    }


def ObsoleteArtifactPaths(OutputPath: Path) -> tuple[Path, ...]:
    """Return artifacts produced by the removed in-house simulator."""
    return (OutputPath.with_suffix(".TruthTable.txt"),)


def ClearStaleSuccessArtifacts(OutputPath: Path) -> list[str]:
    """Remove prior success artifacts before a new compile can fail."""
    Removed = []
    ArtifactPaths = (
        *SuccessArtifactPaths(OutputPath).values(),
        *ObsoleteArtifactPaths(OutputPath),
    )
    for ArtifactPath in ArtifactPaths:
        if ArtifactPath.exists():
            ArtifactPath.unlink()
            Removed.append(NormalizeArtifactPath(ArtifactPath))
    return sorted(Value for Value in Removed if Value is not None)


def BuildFileIdentity(Value: Path | None) -> dict[str, object] | None:
    """Describe one source file sufficiently for local reproduction."""
    if Value is None:
        return None
    Identity: dict[str, object] = {
        "Path": NormalizeArtifactPath(Value),
        "Exists": Value.exists(),
    }
    if Value.is_file():
        try:
            SourceBytes = Value.read_bytes()
            Identity["Sha256"] = sha256(SourceBytes).hexdigest()
            Identity["SizeBytes"] = len(SourceBytes)
        except OSError as Error:
            Identity["ReadError"] = str(Error)
    return Identity


def BuildEnvironmentSnapshot() -> dict[str, object]:
    """Capture reproducibility fields without serializing arbitrary secrets."""
    RoutingEnvironment = {
        Name: os.environ[Name]
        for Name in (
            "RC_ROUTING_THREADS",
            "RCS_DEBUG_AUTHORITATIVE",
            "RC_SOURCE_REVISION",
            "RC_SOURCE_DIRTY",
        )
        if Name in os.environ
    }
    return {
        "PythonVersion": platform.python_version(),
        "PythonExecutable": NormalizeArtifactPath(sys.executable),
        "Platform": platform.platform(),
        "WorkingDirectory": NormalizeArtifactPath(Path.cwd()),
        "RoutingEnvironment": RoutingEnvironment,
    }


def BuildReproductionEnvelope(
    *,
    InputPath: Path | None,
    OutputPath: Path,
    DiagramPath: Path | None,
    Workdir: Path | None,
    TopModule: str | None,
    RequestedStrategy: RoutingStrategy,
) -> dict[str, object]:
    """Build source, command, and normalized output identity fields."""
    return {
        "Input": BuildFileIdentity(InputPath),
        "Output": {
            "Path": NormalizeArtifactPath(OutputPath),
            "Directory": NormalizeArtifactPath(OutputPath.parent),
            "Name": OutputPath.name,
            "Stem": OutputPath.stem,
            "Format": OutputPath.suffix.lstrip(".").lower(),
        },
        "DiagramPath": NormalizeArtifactPath(DiagramPath),
        "Workdir": NormalizeArtifactPath(Workdir),
        "TopModule": TopModule,
        "RequestedStrategy": RequestedStrategy.value,
        "Command": [sys.executable, *sys.argv],
    }


def BuildRoutingFingerprintEnvelope(
    Evidence: dict[str, object],
) -> dict[str, object]:
    """Normalize placement, graph, search, and conflict fingerprints."""
    SelectedPlacement = Evidence.get("SelectedPlacementCandidate", {})
    if not isinstance(SelectedPlacement, dict):
        SelectedPlacement = {}
    PlacementAttempts = Evidence.get("PlacementAttempts", [])
    LastPlacement = (
        PlacementAttempts[-1]
        if isinstance(PlacementAttempts, (list, tuple))
        and PlacementAttempts
        and isinstance(PlacementAttempts[-1], dict)
        else {}
    )
    PlacementCandidates = Evidence.get("PlacementCandidates", [])
    FirstPlacementCandidate = (
        PlacementCandidates[0]
        if isinstance(PlacementCandidates, (list, tuple))
        and PlacementCandidates
        and isinstance(PlacementCandidates[0], dict)
        else {}
    )
    ResourceGraph = Evidence.get(
        "RoutingResourceGraph",
        Evidence.get("ResourceGraph", {}),
    )
    if not ResourceGraph:
        ResourceGraph = {
            Name: Evidence[Name]
            for Name in (
                "ResourceGraphVersion",
                "ResourceGraphNodeCount",
                "ResourceGraphEdgeCount",
                "ResourceCount",
            )
            if Name in Evidence
        }
    ResourceGraphFingerprint = Evidence.get(
        "ResourceGraphFingerprint",
        Evidence.get("RoutingResourceGraphFingerprint"),
    )
    if (
        ResourceGraphFingerprint is None
        and isinstance(ResourceGraph, dict)
        and ResourceGraph
    ):
        # Graph identity is topology and ownership. Stage timings are
        # measurements, not routing state, and would make two byte-identical
        # fixed-seed routes report different fingerprints.
        ResourceGraphFingerprint = BuildStableFingerprint({
            Name: Value
            for Name, Value in ResourceGraph.items()
            if Name != "StageTimingsSeconds"
        })
    return {
        "Placement": Evidence.get(
            "PlacementFingerprint",
            SelectedPlacement.get(
                "PlacementFingerprint",
                LastPlacement.get(
                    "PlacementFingerprint",
                    FirstPlacementCandidate.get("PlacementFingerprint"),
                ),
            ),
        ),
        "ResourceGraph": ResourceGraphFingerprint,
        "Candidate": Evidence.get("CandidateFingerprint"),
        "Conflict": Evidence.get("ConflictFingerprint"),
        "EffectiveWork": Evidence.get("EffectiveWorkFingerprint"),
    }


def BuildNativeWorkSummary(
    Evidence: dict[str, object],
    *,
    AssumeAllRequestsCompleted: bool = False,
) -> dict[str, object]:
    """Normalize native batching and completed-work evidence."""
    NativeBatching = Evidence.get("NativeBatching", {})
    if not isinstance(NativeBatching, dict):
        NativeBatching = {}
    WorkTelemetry = Evidence.get("WorkTelemetry", {})
    if not isinstance(WorkTelemetry, dict):
        WorkTelemetry = {}
    RequestCounts = {}
    for Name in (
        "PortalRequestCount",
        "PortalTargetCount",
        "RouteTreeRequestCount",
        "PortalBatchCount",
        "RouteTreeBatchCount",
        "CandidateRequestCount",
    ):
        Value = Evidence.get(Name, NativeBatching.get(Name))
        if Value is not None:
            RequestCounts[Name] = Value
    CompletedWork = {}
    for Name in (
        "CompletedWork",
        "PortalCompletedWork",
        "RouteTreeCompletedWork",
    ):
        Value = Evidence.get(
            Name,
            WorkTelemetry.get(Name, NativeBatching.get(Name)),
        )
        if Value is not None:
            CompletedWork[Name] = Value
    if AssumeAllRequestsCompleted:
        for CompletedName, RequestName in (
            ("PortalCompletedWork", "PortalRequestCount"),
            ("RouteTreeCompletedWork", "RouteTreeRequestCount"),
        ):
            if RequestName in RequestCounts:
                CompletedWork.setdefault(
                    CompletedName,
                    RequestCounts[RequestName],
                )
    Reported = Evidence.get(
        "NativeWork",
        Evidence.get("NativeWorkSummary", {}),
    )
    if not isinstance(Reported, dict):
        Reported = {"Value": Reported}
    return {
        "Batching": NativeBatching,
        "RequestCounts": RequestCounts,
        "CompletedWork": CompletedWork,
        "Assignment": {
            "RustUsed": Evidence.get("RustAssignmentUsed"),
            "ExpansionLimit": Evidence.get("RustAssignmentExpansionLimit"),
            "Expansions": Evidence.get(
                "RustAssignmentExpansions",
                Evidence.get("AssignmentExpansions"),
            ),
        },
        "Reported": Reported,
    }


def BuildSuccessRouterReliability(
    Evidence: dict[str, object],
) -> dict[str, object]:
    """Build the successful-run envelope parallel to routing-failure-v1."""
    return {
        "SchemaVersion": "router-reliability-v1",
        "RunVerdict": "ROUTED_AWAITING_FABRIC_SERVER_VALIDATION",
        "PlacementCandidates": Evidence.get(
            "PlacementFeedbackCandidates", []
        ),
        "SelectedPlacementCandidate": Evidence.get(
            "SelectedPlacementCandidate"
        ),
        "Fingerprints": BuildRoutingFingerprintEnvelope(Evidence),
        "NativeWork": BuildNativeWorkSummary(
            Evidence,
            AssumeAllRequestsCompleted=True,
        ),
        "Deadline": Evidence.get("Deadline", {}),
    }


def BuildPartialArtifactPaths(
    *,
    OutputPath: Path,
    DiagramPath: Path | None = None,
    ExplicitPaths: object = None,
) -> dict[str, str]:
    """Return known partial artifacts, excluding absent success outputs."""
    Candidates: dict[str, Path | str | None] = {
        "Diagram": DiagramPath,
        **SuccessArtifactPaths(OutputPath),
    }
    ExplicitNames: set[str] = set()
    if isinstance(ExplicitPaths, dict):
        ExplicitValues = {
            str(Name): Value for Name, Value in ExplicitPaths.items()
        }
        ExplicitNames.update(ExplicitValues)
        Candidates.update(ExplicitValues)
    elif isinstance(ExplicitPaths, (list, tuple)):
        ExplicitValues = {
            f"Explicit-{Index:03d}": Value
            for Index, Value in enumerate(ExplicitPaths)
        }
        ExplicitNames.update(ExplicitValues)
        Candidates.update(ExplicitValues)
    Result = {}
    for Name, Value in Candidates.items():
        if Value is None:
            continue
        PathValue = Path(Value)
        if Name not in ExplicitNames and not PathValue.exists():
            continue
        Normalized = NormalizeArtifactPath(PathValue)
        if Normalized is not None:
            Result[Name] = Normalized
    return dict(sorted(Result.items()))


def BuildFabricServerSnapshotDocument(
    Artifact: FabricServerSnapshotArtifact,
    OutputPath: Path,
) -> dict[str, object]:
    """Describe the authoritative all-zero world state published as output."""
    return {
        "Path": NormalizeArtifactPath(OutputPath),
        "State": "all-inputs-zero-server-updated",
        "RequestedPositionCount": Artifact.RequestedPositionCount,
        "ObservedBlockCount": Artifact.ObservedBlockCount,
        "WorldReadRequests": Artifact.WorldReadRequests,
        "InputCountSetToZero": Artifact.InputCountSetToZero,
        "SnapshotReadPasses": Artifact.SnapshotReadPasses,
        "InputZeroGameTime": Artifact.InputZeroGameTime,
        "FirstObservedGameTime": Artifact.FirstObservedGameTime,
        "LastObservedGameTime": Artifact.LastObservedGameTime,
    }


def PublishSuccessArtifacts(
    *,
    Routed: object,
    Rendered: object,
    PhysicalDesignDocument: dict[str, object],
    FabricFixture: dict[str, object] | None = None,
    FabricServerSnapshotSupervisor: FabricServerSupervisor | None = None,
    OutputPath: Path,
) -> Path:
    """Stage all success outputs and publish a settled server snapshot.

    A compiler run always supplies both ``FabricFixture`` and
    ``FabricServerSnapshotSupervisor``.  The static litematic is written only
    into the private staging directory so the existing compiler-side
    orientation audit and I/O labels remain authoritative.  Minecraft then
    supplies the final block-state snapshot after all fixture inputs have been
    reset to zero.  Direct callers that do not supply a supervisor retain the
    static writer behavior for narrow unit tests and fixture-only workflows.
    """
    if FabricServerSnapshotSupervisor is not None and FabricFixture is None:
        raise ValueError(
            "Fabric server snapshot requires a Fabric fixture",
        )
    ArtifactPaths = SuccessArtifactPaths(OutputPath)
    OutputPath.parent.mkdir(parents=True, exist_ok=True)
    ClearStaleSuccessArtifacts(OutputPath)
    try:
        with TemporaryDirectory(
            dir=OutputPath.parent,
            prefix=f".{OutputPath.stem}-publish-",
        ) as TemporaryDirectoryValue:
            TemporaryRoot = Path(TemporaryDirectoryValue)
            TemporaryOutputPath = TemporaryRoot / OutputPath.name
            TemporaryPhysicalDesignPath = (
                TemporaryRoot / ArtifactPaths["PhysicalDesign"].name
            )
            if FabricServerSnapshotSupervisor is None:
                SchemWriter.WriteLitematic(
                    Routed,
                    OutputPath=TemporaryOutputPath,
                    Build=Rendered,
                )
            else:
                TemporaryStaticOutputPath = TemporaryRoot / (
                    f"{OutputPath.stem}.Static{OutputPath.suffix}"
                )
                SchemWriter.WriteLitematic(
                    Routed,
                    OutputPath=TemporaryStaticOutputPath,
                    Build=Rendered,
                )
                SnapshotArtifact = CaptureServerUpdatedLitematic(
                    Supervisor=FabricServerSnapshotSupervisor,
                    Fixture=FabricFixture,
                    SourcePath=TemporaryStaticOutputPath,
                    OutputPath=TemporaryOutputPath,
                )
                RunSummary = PhysicalDesignDocument.get("RunSummary")
                if isinstance(RunSummary, dict):
                    RunSummary["FabricServerSnapshot"] = (
                        BuildFabricServerSnapshotDocument(
                            SnapshotArtifact,
                            OutputPath,
                        )
                    )
            if FabricFixture is not None:
                TemporaryFixturePath = (
                    TemporaryRoot / ArtifactPaths["FabricFixture"].name
                )
                WriteFabricFixture(TemporaryFixturePath, FabricFixture)
            RepeaterOrientation = getattr(
                Rendered,
                "RepeaterOrientation",
                {},
            )
            FinalValidation = PhysicalDesignDocument.get("FinalValidation")
            if isinstance(FinalValidation, dict):
                FinalValidation["RepeaterOrientationReadbackPassed"] = (
                    RepeaterOrientation.get("ReadbackPassed") is True
                )
            TemporaryPhysicalDesignPath.write_text(
                json.dumps(
                    PhysicalDesignDocument,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
            TemporaryOutputPath.replace(ArtifactPaths["Schematic"])
            if FabricFixture is not None:
                TemporaryFixturePath.replace(ArtifactPaths["FabricFixture"])
            TemporaryPhysicalDesignPath.replace(ArtifactPaths["PhysicalDesign"])
    except Exception:
        ClearStaleSuccessArtifacts(OutputPath)
        raise
    return ArtifactPaths["PhysicalDesign"]


def WriteRoutingFailureArtifact(
    *,
    OutputPath: Path,
    RequestedStrategy: RoutingStrategy,
    Failure: RoutingFailure,
    StartedAt: float,
    InputPath: Path | None = None,
    DiagramPath: Path | None = None,
    Workdir: Path | None = None,
    TopModule: str | None = None,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    EffectivePolicy: PhysicalDesignPolicy | None = None,
) -> Path:
    """Persist one stable typed diagnostic without converting failure to success."""
    Diagnostics = dict(Failure.Diagnostics or {})
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    Reproduction = BuildReproductionEnvelope(
        InputPath=InputPath,
        OutputPath=OutputPath,
        DiagramPath=DiagramPath,
        Workdir=Workdir,
        TopModule=TopModule,
        RequestedStrategy=RequestedStrategy,
    )
    UsedStrategy = ExecutionStrategyForRequest(RequestedStrategy)
    FailurePath = OutputPath.with_suffix(".RoutingFailure.json")
    FailurePath.write_text(
        json.dumps(
            _JsonDiagnosticValue({
                "SchemaVersion": "routing-failure-v1",
                "Policy": (
                    EffectivePolicy or PolicyForRoutingStrategy(UsedStrategy)
                ).ToDictionary(),
                "Strategy": {
                    "Requested": RequestedStrategy.value,
                    "Used": UsedStrategy.value,
                    "FallbackUsed": False,
                },
                "SourceState": ReadSourceState(),
                "Environment": BuildEnvironmentSnapshot(),
                "Reproduction": Reproduction,
                "OutputIdentity": Reproduction["Output"],
                "Technology": asdict(Technology),
                "Failure": BuildRoutingFailureArtifactSnapshot(Failure),
                "Affected": {
                    "Nets": list(Failure.AffectedNets),
                    "Resources": list(Failure.Resources),
                    "Locations": [
                        list(Location) for Location in Failure.Locations
                    ],
                },
                "EffectiveControls": Diagnostics.get(
                    "FixedRoutingControls",
                    {},
                ),
                "Fingerprints": BuildRoutingFingerprintEnvelope(Diagnostics),
                "NativeWork": BuildNativeWorkSummary(Diagnostics),
                "PartialArtifactPaths": BuildPartialArtifactPaths(
                    OutputPath=OutputPath,
                    DiagramPath=DiagramPath,
                    ExplicitPaths=Diagnostics.get("PartialArtifactPaths"),
                ),
                "ConflictGraph": ConflictGraph,
                "StageTimingsSeconds": Diagnostics.get(
                    "StageTimingsSeconds", {}
                ),
                "Deadline": Diagnostics.get("Deadline", {}),
                "RuntimeSeconds": round(monotonic() - StartedAt, 6),
            }),
            sort_keys=True,
            separators=(",", ":"),
            default=_JsonDiagnosticDefault,
        ) + "\n",
        encoding="utf-8",
    )
    return FailurePath


def TryWriteRoutingFailureArtifact(**Arguments: object) -> Path | None:
    """Best-effort wrapper that never hides the original routing exception."""
    try:
        return WriteRoutingFailureArtifact(**Arguments)
    except Exception as ArtifactError:
        print(
            "Could not write routing failure diagnostics: "
            f"{ArtifactError}",
            file=sys.stderr,
        )
        return None


def CompileSvToLitematic(
    *,
    InputPath: Path,
    OutputPath: Path,
    DiagramPath: Path,
    TopModule: str | None = None,
    Workdir: Path = Path("Cache/Frontend"),
    ProgressCallback: Callable[[PcbProgress], None] | None = None,
    RoutingStrategyValue: RoutingStrategy | str = RoutingStrategy.Default,
    RoutingDeadlineSeconds: float | None = None,
    TraceSupportBlocks: tuple[str, ...] | list[str] | None = None,
) -> CompileResult:
    """Run the complete SV to NAND diagram and litematic flow."""

    StartedAt = monotonic()
    RequestedStrategy = RoutingStrategy.Parse(RoutingStrategyValue)
    EffectivePolicy = ApplyRoutingRuntimeBudget(
        PolicyForRoutingStrategy(
            ExecutionStrategyForRequest(RequestedStrategy)
        ),
        RoutingDeadlineSeconds,
    )
    Workdir.mkdir(parents=True, exist_ok=True)
    OutputPath.parent.mkdir(parents=True, exist_ok=True)
    DiagramPath.parent.mkdir(parents=True, exist_ok=True)
    ClearStaleSuccessArtifacts(OutputPath)
    RoutingFailurePath = OutputPath.with_suffix(".RoutingFailure.json")
    RoutingFailurePath.unlink(missing_ok=True)
    Stages = []

    Netlist = Sv.ParseSvToNetlist(
        InputPath=InputPath,
        TopModule=TopModule,
        Workdir=Workdir,
    )
    Stages.append("parse")

    OriginalLogicGateCount = len(Netlist.Modules[Netlist.Top].Gates)
    OptimizedIR = OptimizeLogic(Netlist)
    OptimizedLogicGateCount = len(OptimizedIR.Modules[OptimizedIR.Top].Gates)
    Stages.append("logic_optimization")

    NandIR = ToNandOnly(OptimizedIR)
    ValidateNandOnlyDesign(NandIR)
    Stages.append("nand_transform")

    WriteNandDiagram(NandIR, DiagramPath)
    Stages.append("nand_diagram")

    try:
        Physical = PlaceAndRoutePcb(
            NandIR,
            ProgressCallback=ProgressCallback,
            Strategy=RequestedStrategy,
            Policy=EffectivePolicy,
            RoutingDeadlineSeconds=RoutingDeadlineSeconds,
        )
    except RoutingStageError as Error:
        TryWriteRoutingFailureArtifact(
            OutputPath=OutputPath,
            RequestedStrategy=RequestedStrategy,
            Failure=Error.Failure,
            StartedAt=StartedAt,
            InputPath=InputPath,
            DiagramPath=DiagramPath,
            Workdir=Workdir,
            TopModule=TopModule,
            EffectivePolicy=EffectivePolicy,
        )
        raise
    except ValueError as Error:
        TryWriteRoutingFailureArtifact(
            OutputPath=OutputPath,
            RequestedStrategy=RequestedStrategy,
            Failure=RoutingFailure(
                Reason=RoutingFailureReason.DetailedSearchExhausted,
                Stage="PlacementRouting",
                Detail=str(Error),
            ),
            StartedAt=StartedAt,
            InputPath=InputPath,
            DiagramPath=DiagramPath,
            Workdir=Workdir,
            TopModule=TopModule,
            EffectivePolicy=EffectivePolicy,
        )
        raise
    Stages.append("pcb_placement")
    Routed = Physical.Routed
    Stages.append("pcb_routing")
    Stages.append("route_cleanup")
    ValidateNandOnlyDesign(Physical.Placed, NandIR)
    Routed.TraceSupportBlocks = (
        tuple(TraceSupportBlocks) if TraceSupportBlocks is not None else ()
    )
    Rendered = SchemWriter.BuildLitematicBlockMap(
        Routed,
        TraceSupportBlocks=Routed.TraceSupportBlocks,
    )
    Composition = Rendered.Composition
    NandModule = NandIR.Modules[NandIR.Top]
    FabricFixture = BuildFabricFixture(
        RoutedDesign=Routed,
        Rendered=Rendered,
        Module=NandModule,
    )
    ServerSupervisor = FabricServerSupervisor(
        FabricServerConfiguration.FromEnvironment(),
    )
    with TemporaryDirectory(
        dir=OutputPath.parent,
        prefix=f".{OutputPath.stem}-fabric-validate-",
    ) as FixtureDirectory:
        ValidationFixture = WriteFabricFixture(
            Path(FixtureDirectory) / OutputPath.with_suffix(".FabricFixture.json").name,
            FabricFixture,
        )
        FabricServerValidation = ServerSupervisor.Validate(
            Fixture=ValidationFixture,
            Vectors=BuildExpectedVectors(
                NandModule,
                (Value["Name"] for Value in FabricFixture["Inputs"]),
                (Value["Name"] for Value in FabricFixture["Outputs"]),
                IncludeTraceValues=True,
            ),
        )
    Stages.append("fabric_server_validation")
    if FabricServerValidation.Status != "passed":
        TryWriteRoutingFailureArtifact(
            OutputPath=OutputPath,
            RequestedStrategy=RequestedStrategy,
            Failure=RoutingFailure(
                Reason=RoutingFailureReason.FinalDrcViolation,
                Stage="FabricServerValidation",
                Detail=(
                    "authoritative Fabric validation did not pass: "
                    f"{FabricServerValidation.Status}"
                ),
                Diagnostics={
                    "FabricServerValidation": asdict(FabricServerValidation),
                },
            ),
            StartedAt=StartedAt,
            InputPath=InputPath,
            DiagramPath=DiagramPath,
            Workdir=Workdir,
            TopModule=TopModule,
            EffectivePolicy=EffectivePolicy,
        )
        RequireFabricServerValidation(FabricServerValidation)

    GlobalPlan = Routed.GlobalPlan
    Metrics = Routed.RoutingMetrics
    PerNetLengths = {
        Signal: len(set(Positions))
        for Signal, Positions in sorted(Routed.NetWires.items())
    }
    TotalPerNetLength = sum(PerNetLengths.values())
    MaximumNetLengthShare = (
        max(PerNetLengths.values(), default=0) / TotalPerNetLength
        if TotalPerNetLength
        else 0.0
    )
    SelectedSignals = (
        set(Routed.RoutingAssignment.SelectedCandidates)
        if Routed.RoutingAssignment is not None
        else set()
    )
    UnresolvedClaims = sorted(
        set(Routed.NetWires)
        - SelectedSignals
        - set(Routed.FrozenNetSignals)
    )
    RuntimeSeconds = monotonic() - StartedAt
    DemandInfo = Routed.RoutingControlEffectiveness.get(
        "RoutingDemandEstimate", {}
    )
    NandCount = int(DemandInfo.get("NandCount", 0))
    TotalHpwl = int(DemandInfo.get("TotalHpwl", 0))
    RoutedNetCount = max(1, len(PerNetLengths))
    NormalizedQuality = {
        "LengthToHpwlRatio": round(
            (Metrics.TotalLength if Metrics is not None else 0)
            / max(1, TotalHpwl),
            6,
        ),
        "RoutingBlocksPerNand": round(
            Composition.RoutingOwnedFunctionalBlocks / max(1, NandCount),
            6,
        ),
        "NonAirBlocksPerNand": round(
            Composition.NonAirBlocks / max(1, NandCount),
            6,
        ),
        "AverageBendsPerNet": round(
            (Metrics.BendCount if Metrics is not None else 0) / RoutedNetCount,
            6,
        ),
        "AverageViasPerNet": round(
            (Metrics.ViaCount if Metrics is not None else 0) / RoutedNetCount,
            6,
        ),
        "OverflowCellsPerNet": round(
            (Metrics.AccessOverflowCells if Metrics is not None else 0)
            / RoutedNetCount,
            6,
        ),
    }
    RoutingResourceGraphDocument = {
        "Version": Routed.ResourceGraphVersion,
        "NodeCount": Routed.ResourceGraphNodeCount,
        "EdgeCount": Routed.ResourceGraphEdgeCount,
        "OwnershipCounts": Routed.ResourceOwnershipCounts,
        "RepeaterReservations": Routed.RepeaterReservationCount,
        "ZeroConflicts": Routed.ZeroResourceConflicts,
        "PortalCount": Routed.PortalCount,
        "RouteCandidateCount": Routed.RouteCandidateCount,
        "CandidateRequestCount": Routed.CandidateRequestCount,
        "CandidateExpansionLimit": Routed.CandidateExpansionLimit,
        "AssignmentExpansions": Routed.AssignmentExpansionCount,
        "StageTimingsSeconds": Routed.RoutingStageTimings,
    }
    SuccessReproduction = BuildReproductionEnvelope(
        InputPath=InputPath,
        OutputPath=OutputPath,
        DiagramPath=DiagramPath,
        Workdir=Workdir,
        TopModule=TopModule,
        RequestedStrategy=RequestedStrategy,
    )
    SuccessEvidence = {
        **Routed.RoutingControlEffectiveness,
        "NegotiatedRouting": Routed.NegotiatedRoutingDiagnostics,
        "RoutingResourceGraph": RoutingResourceGraphDocument,
        "RepeaterOptimization": Rendered.RepeaterOptimization,
    }
    RouterReliabilityDocument = BuildSuccessRouterReliability(SuccessEvidence)
    # The routing envelope is created before the live world probe, but the
    # persisted success document is written only after that probe passes.
    # Record the completed end-to-end verdict rather than leaving consumers
    # to infer it from a separate final-validation object.
    RouterReliabilityDocument["RunVerdict"] = (
        "ROUTED_AND_FABRIC_SERVER_VALIDATED"
    )
    PhysicalDesignDocument = {
        "Strategy": {
            "Requested": Physical.RequestedStrategy,
            "Used": Physical.UsedStrategy,
            "FallbackUsed": Physical.FallbackUsed,
            "FallbackReason": Physical.FallbackReason,
            "RejectedRewriteDiagnostics": Physical.RejectedRewriteDiagnostics,
        },
        "SourceState": ReadSourceState(),
        "Environment": BuildEnvironmentSnapshot(),
        "Reproduction": SuccessReproduction,
        "OutputIdentity": SuccessReproduction["Output"],
        "Policy": Physical.Policy.ToDictionary(),
        "Technology": asdict(Physical.Technology),
        "RouterReliability": RouterReliabilityDocument,
        "PlanningContracts": Physical.PlanningContracts,
        "GlobalGuidePlanning": Routed.GlobalGuideDiagnostics,
        "NegotiatedRouting": Routed.NegotiatedRoutingDiagnostics,
        "RoutingFootprint": Routed.RoutingFootprintDiagnostics,
        "RoutingControlEffectiveness": Routed.RoutingControlEffectiveness,
        "RepeaterOptimization": Rendered.RepeaterOptimization,
        "RunSummary": {
            "RuntimeSeconds": round(RuntimeSeconds, 6),
            "Width": Composition.Width,
            "Height": Composition.Height,
            "Depth": Composition.Depth,
            "XYFootprint": Composition.XYFootprint,
            "Footprint": Composition.Footprint,
            "FullFootprint": Composition.FullFootprint,
            "EstimatedBlocks": Physical.EstimatedBlocks,
            "ExactNonAirBlocks": Composition.NonAirBlocks,
            "Length": Metrics.TotalLength if Metrics is not None else 0,
            "Bends": Metrics.BendCount if Metrics is not None else 0,
            "Vias": Metrics.ViaCount if Metrics is not None else 0,
            "ReroutedNets": (
                Metrics.ReroutedNets if Metrics is not None else 0
            ),
            "RoutingPasses": (
                max(1, len(Metrics.Iterations))
                if Metrics is not None
                else 0
            ),
            "Conflicts": Metrics.ConflictCount if Metrics is not None else 0,
            "OverflowPeak": (
                Metrics.CorridorOverflowPeak if Metrics is not None else 0
            ),
            "AccessOverflowPeak": (
                Metrics.AccessOverflowPeak if Metrics is not None else 0
            ),
            "AccessOverflowCells": (
                Metrics.AccessOverflowCells if Metrics is not None else 0
            ),
            "PerNetLength": PerNetLengths,
            "MaximumNetLengthShare": round(MaximumNetLengthShare, 6),
            "FabricServerValidation": asdict(FabricServerValidation),
            "FabricFixture": {
                "Path": NormalizeArtifactPath(
                    OutputPath.with_suffix(".FabricFixture.json"),
                ),
                "Sha256": ValidationFixture.Sha256,
                "BlockCount": ValidationFixture.BlockCount,
                "InputCount": ValidationFixture.InputCount,
                "OutputCount": ValidationFixture.OutputCount,
            },
        },
        "BlockComposition": Composition.ToDictionary(),
        "RepeaterOrientation": Rendered.RepeaterOrientation,
        "NormalizedQuality": NormalizedQuality,
        "FinalValidation": {
            "ValidationMode": "fabric-server-authoritative",
            "FabricServerValidationRequired": True,
            "FabricServerValidationStatus": FabricServerValidation.Status,
            "ZeroConflicts": Routed.ZeroResourceConflicts,
            "ConflictCount": 0 if Routed.ZeroResourceConflicts else 1,
            "UnresolvedClaims": UnresolvedClaims,
            "UnresolvedClaimCount": len(UnresolvedClaims),
            "RepeaterOrientationPassed": (
                Rendered.RepeaterOrientation.get("Passed") is True
            ),
            "RepeaterOrientationMismatchCount": int(
                Rendered.RepeaterOrientation.get("MismatchCount", 0)
            ),
            "RepeaterOrientationReadbackRequired": True,
        },
        "RoutingResourceGraph": RoutingResourceGraphDocument,
        "GlobalRouting": (
            {
                "SignalOrder": list(GlobalPlan.SignalOrder),
                "Layers": GlobalPlan.Layers,
                "ResourceCount": len(GlobalPlan.ResourceUsage),
                "OwnedResourceCount": (
                    len(Routed.TrackAssignment.ResourceOwners)
                    if Routed.TrackAssignment is not None
                    else 0
                ),
                "Overflow": {
                    str(Resource): Value
                    for Resource, Value in GlobalPlan.ResourceOverflow.items()
                },
            }
            if GlobalPlan is not None
            else None
        ),
    }
    PhysicalDesignPath = PublishSuccessArtifacts(
        Routed=Routed,
        Rendered=Rendered,
        PhysicalDesignDocument=PhysicalDesignDocument,
        FabricFixture=FabricFixture,
        FabricServerSnapshotSupervisor=ServerSupervisor,
        OutputPath=OutputPath,
    )
    Stages.append("fabric_server_snapshot")
    Stages.append("litematic_writer")
    Stages.append("physical_design_diagnostics")

    NandGateCount = sum(
        1
        for Gate in NandIR.Modules[NandIR.Top].Gates
        if Gate.Kind.value == "NAND"
    )
    return CompileResult(
        OutputPath=OutputPath,
        DiagramPath=DiagramPath,
        NandGateCount=NandGateCount,
        Stages=Stages,
        EstimatedBlocks=Composition.NonAirBlocks,
        Width=Composition.Width,
        Depth=Composition.Depth,
        OriginalLogicGateCount=OriginalLogicGateCount,
        OptimizedLogicGateCount=OptimizedLogicGateCount,
        FabricServerValidation=FabricServerValidation,
        RoutingMetrics=Routed.RoutingMetrics,
        PhysicalDesignPath=PhysicalDesignPath,
        RequestedStrategy=Physical.RequestedStrategy,
        UsedStrategy=Physical.UsedStrategy,
        FallbackUsed=Physical.FallbackUsed,
        FallbackReason=Physical.FallbackReason,
        RuntimeSeconds=RuntimeSeconds,
        MaximumNetLengthShare=MaximumNetLengthShare,
        BlockComposition=Composition,
    )

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
from .Placement.PcbFlow import (
    ApplyRoutingRuntimeBudget,
    PcbProgress,
    PlaceAndRoutePcb,
)
from SchemEncoder import Writer262
from SchemEncoder.Writer262 import BlockCompositionMetrics
from .Simulation.Redstone import (
    SimulateRenderedMinecraftTruthTable,
    SimulateRoutedTruthTable,
    WriteTruthTable,
)
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
    DotPath: Path
    NandGateCount: int
    Stages: list[str]
    EstimatedBlocks: int
    Width: int
    Depth: int
    OriginalLogicGateCount: int
    OptimizedLogicGateCount: int
    TruthTablePath: Path
    TruthTablePassed: bool
    TruthTableRows: int
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
        "TruthTable": OutputPath.with_suffix(".TruthTable.txt"),
        "PhysicalDesign": OutputPath.with_suffix(".PhysicalDesign.json"),
    }


def ClearStaleSuccessArtifacts(OutputPath: Path) -> list[str]:
    """Remove prior success artifacts before a new compile can fail."""
    Removed = []
    for ArtifactPath in SuccessArtifactPaths(OutputPath).values():
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
        "RunVerdict": "ROUTED_AND_SIMULATED",
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
    DotPath: Path | None = None,
    ExplicitPaths: object = None,
) -> dict[str, str]:
    """Return known partial artifacts, excluding absent success outputs."""
    Candidates: dict[str, Path | str | None] = {
        "Diagram": DiagramPath,
        "Dot": DotPath,
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


def PublishSuccessArtifacts(
    *,
    Routed: object,
    Rendered: object,
    Simulation: object,
    PhysicalDesignDocument: dict[str, object],
    OutputPath: Path,
) -> tuple[Path, Path]:
    """Stage all success outputs and publish metadata after the schematic."""
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
            TemporaryTruthTablePath = (
                TemporaryRoot / ArtifactPaths["TruthTable"].name
            )
            TemporaryPhysicalDesignPath = (
                TemporaryRoot / ArtifactPaths["PhysicalDesign"].name
            )
            Writer262.WriteLitematic(
                Routed,
                OutputPath=TemporaryOutputPath,
                Build=Rendered,
            )
            WriteTruthTable(Simulation, TemporaryTruthTablePath)
            TemporaryPhysicalDesignPath.write_text(
                json.dumps(
                    PhysicalDesignDocument,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
            TemporaryOutputPath.replace(ArtifactPaths["Schematic"])
            TemporaryTruthTablePath.replace(ArtifactPaths["TruthTable"])
            TemporaryPhysicalDesignPath.replace(ArtifactPaths["PhysicalDesign"])
    except Exception:
        ClearStaleSuccessArtifacts(OutputPath)
        raise
    return ArtifactPaths["TruthTable"], ArtifactPaths["PhysicalDesign"]


def WriteRoutingFailureArtifact(
    *,
    OutputPath: Path,
    RequestedStrategy: RoutingStrategy,
    Failure: RoutingFailure,
    StartedAt: float,
    InputPath: Path | None = None,
    DiagramPath: Path | None = None,
    DotPath: Path | None = None,
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
                    DotPath=DotPath,
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

    DotPath = WriteNandDiagram(NandIR, DiagramPath)
    Stages.append("nand_diagram")

    ValidatedSimulation = None

    def ValidateRoutedCandidate(RoutedCandidate: object) -> None:
        nonlocal ValidatedSimulation
        CandidateSimulation = SimulateRoutedTruthTable(
            RoutedCandidate,
            ReferenceModule=OptimizedIR.Modules[OptimizedIR.Top],
        )
        if not CandidateSimulation.Passed:
            Failed = CandidateSimulation.FailedRows[0]
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.ElectricalConflict,
                    Stage="PhysicalSimulation",
                    Detail=(
                        "routed placement failed physical truth-table validation"
                    ),
                    Diagnostics={
                        "Inputs": [int(Value) for Value in Failed.Inputs],
                        "ExpectedOutputs": [
                            int(Value) for Value in Failed.ExpectedOutputs
                        ],
                        "SimulatedOutputs": [
                            int(Value) for Value in Failed.SimulatedOutputs
                        ],
                        "SimulationBackend": CandidateSimulation.Backend,
                        "SimulationRuntimeSeconds": (
                            CandidateSimulation.RuntimeSeconds
                        ),
                    },
                )
            )
        ValidatedSimulation = CandidateSimulation

    try:
        Physical = PlaceAndRoutePcb(
            NandIR,
            ProgressCallback=ProgressCallback,
            Strategy=RequestedStrategy,
            Policy=EffectivePolicy,
            RoutedValidationCallback=ValidateRoutedCandidate,
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
            DotPath=DotPath,
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
            DotPath=DotPath,
            Workdir=Workdir,
            TopModule=TopModule,
            EffectivePolicy=EffectivePolicy,
        )
        raise
    Stages.append("pcb_placement")
    Routed = Physical.Routed
    Stages.append("pcb_routing")
    Stages.append("route_cleanup")

    Simulation = ValidatedSimulation or SimulateRoutedTruthTable(
        Routed,
        ReferenceModule=OptimizedIR.Modules[OptimizedIR.Top],
    )
    Stages.append("redstone_simulation")
    if not Simulation.Passed:
        Failed = Simulation.FailedRows[0]
        raise ValueError(
            "Physical redstone truth table failed: "
            f"inputs={tuple(int(Value) for Value in Failed.Inputs)}, "
            "expected="
            f"{tuple(int(Value) for Value in Failed.ExpectedOutputs)}, "
            "simulated="
            f"{tuple(int(Value) for Value in Failed.SimulatedOutputs)}"
        )
    TruthTablePath = OutputPath.with_suffix(".TruthTable.txt")

    ValidateNandOnlyDesign(Physical.Placed, NandIR)
    Routed.TraceSupportBlocks = (
        tuple(TraceSupportBlocks) if TraceSupportBlocks is not None else ()
    )
    Rendered = Writer262.BuildLitematicBlockMap(
        Routed,
        TraceSupportBlocks=Routed.TraceSupportBlocks,
    )
    Composition = Rendered.Composition

    # The routed-net check above is an inexpensive candidate filter.  The
    # final acceptance decision must instead come from the exact rendered
    # Minecraft block layout, where torches, opaque support blocks, and dust
    # can couple otherwise distinct logical nets.
    Simulation = SimulateRenderedMinecraftTruthTable(
        Routed,
        ReferenceModule=OptimizedIR.Modules[OptimizedIR.Top],
    )
    if not Simulation.Passed:
        Failed = Simulation.FailedRows[0]
        raise ValueError(
            "Rendered Minecraft redstone truth table failed: "
            f"inputs={tuple(int(Value) for Value in Failed.Inputs)}, "
            f"expected={tuple(int(Value) for Value in Failed.ExpectedOutputs)}, "
            f"simulated={tuple(int(Value) for Value in Failed.SimulatedOutputs)}"
        )

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
            "TruthTablePassed": Simulation.Passed,
            "TruthTableRows": len(Simulation.Rows),
            "SimulationBackend": Simulation.Backend,
            "SimulationRuntimeSeconds": Simulation.RuntimeSeconds,
            "SimulationDiagnostics": Simulation.Diagnostics,
        },
        "BlockComposition": Composition.ToDictionary(),
        "NormalizedQuality": NormalizedQuality,
        "FinalValidation": {
            "ValidationMode": "authoritative-exact",
            "ZeroConflicts": Routed.ZeroResourceConflicts,
            "ConflictCount": 0 if Routed.ZeroResourceConflicts else 1,
            "UnresolvedClaims": UnresolvedClaims,
            "UnresolvedClaimCount": len(UnresolvedClaims),
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
    TruthTablePath, PhysicalDesignPath = PublishSuccessArtifacts(
        Routed=Routed,
        Rendered=Rendered,
        Simulation=Simulation,
        PhysicalDesignDocument=PhysicalDesignDocument,
        OutputPath=OutputPath,
    )
    Stages.append("litematic_writer")
    Stages.append("truth_table")
    Stages.append("physical_design_diagnostics")

    NandGateCount = sum(
        1
        for Gate in NandIR.Modules[NandIR.Top].Gates
        if Gate.Kind.value == "NAND"
    )
    return CompileResult(
        OutputPath=OutputPath,
        DiagramPath=DiagramPath,
        DotPath=DotPath,
        NandGateCount=NandGateCount,
        Stages=Stages,
        EstimatedBlocks=Composition.NonAirBlocks,
        Width=Composition.Width,
        Depth=Composition.Depth,
        OriginalLogicGateCount=OriginalLogicGateCount,
        OptimizedLogicGateCount=OptimizedLogicGateCount,
        TruthTablePath=TruthTablePath,
        TruthTablePassed=Simulation.Passed,
        TruthTableRows=len(Simulation.Rows),
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

"""Pipeline orchestration for end-to-end compilation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from time import monotonic
from typing import Callable

from SVDecoder import Sv
from .Synthesis.Diagram import WriteNandDiagram
from .Synthesis.LogicOptimization import OptimizeLogic
from .Synthesis.NandTransform import ToNandOnly
from .Synthesis.Validation import ValidateNandOnlyDesign
from .Placement.PcbFlow import PcbProgress, PlaceAndRoutePcb
from SchemEncoder import Writer262
from SchemEncoder.Writer262 import BlockCompositionMetrics
from .Simulation.Redstone import SimulateRoutedTruthTable, WriteTruthTable
from .Routing.ChannelPlanner import RoutingStageMetrics
from .Routing.Policy import RoutingStrategy


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


def CompileSvToLitematic(
    *,
    InputPath: Path,
    OutputPath: Path,
    DiagramPath: Path,
    TopModule: str | None = None,
    Workdir: Path = Path("Cache/Frontend"),
    ProgressCallback: Callable[[PcbProgress], None] | None = None,
    RoutingStrategyValue: RoutingStrategy | str = RoutingStrategy.NewRouterFirst,
) -> CompileResult:
    """Run the complete SV to NAND diagram and litematic flow."""

    StartedAt = monotonic()
    RequestedStrategy = RoutingStrategy.Parse(RoutingStrategyValue)
    Workdir.mkdir(parents=True, exist_ok=True)
    OutputPath.parent.mkdir(parents=True, exist_ok=True)
    DiagramPath.parent.mkdir(parents=True, exist_ok=True)
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

    Physical = PlaceAndRoutePcb(
        NandIR,
        ProgressCallback=ProgressCallback,
        Strategy=RequestedStrategy,
    )
    if (
        RequestedStrategy == RoutingStrategy.Hybrid
        and Physical.UsedStrategy == RoutingStrategy.NewRouterFirst.value
        and Physical.Policy.QualityGate.Enabled
    ):
        RewriteRouted = Physical.Routed
        RewriteMetrics = RewriteRouted.RoutingMetrics
        RewriteLengths = {
            Signal: len(set(Positions))
            for Signal, Positions in RewriteRouted.NetWires.items()
        }
        RewriteTotalLength = sum(RewriteLengths.values())
        RewriteMaximumShare = (
            max(RewriteLengths.values(), default=0) / RewriteTotalLength
            if RewriteTotalLength
            else 0.0
        )
        Gate = Physical.Policy.QualityGate
        MaterialGate = Physical.Policy.MaterialObjective
        RewriteBuild = Writer262.BuildLitematicBlockMap(RewriteRouted)
        RewriteComposition = RewriteBuild.Composition
        QualityFailures = []
        if RewriteMetrics is None:
            QualityFailures.append("routing metrics unavailable")
        else:
            if RewriteMetrics.CorridorOverflowPeak > Gate.MaximumCorridorOverflowPeak:
                QualityFailures.append(
                    "corridor overflow peak "
                    f"{RewriteMetrics.CorridorOverflowPeak} > "
                    f"{Gate.MaximumCorridorOverflowPeak}"
                )
            if RewriteMetrics.NetCount and (
                RewriteMetrics.BendCount / RewriteMetrics.NetCount
                > Gate.MaximumAverageBendsPerNet
            ):
                QualityFailures.append(
                    f"average bends/net {RewriteMetrics.BendCount / RewriteMetrics.NetCount:.3f} "
                    f"> {Gate.MaximumAverageBendsPerNet:.3f}"
                )
            if RewriteMetrics.NetCount and (
                RewriteMetrics.ViaCount / RewriteMetrics.NetCount
                > Gate.MaximumAverageViasPerNet
            ):
                QualityFailures.append(
                    f"average vias/net {RewriteMetrics.ViaCount / RewriteMetrics.NetCount:.3f} "
                    f"> {Gate.MaximumAverageViasPerNet:.3f}"
                )
        if RewriteMaximumShare > Gate.MaximumNetLengthShare:
            QualityFailures.append(
                f"maximum net share {RewriteMaximumShare:.3%} > "
                f"{Gate.MaximumNetLengthShare:.3%}"
            )
        if not RewriteRouted.ZeroResourceConflicts:
            QualityFailures.append("authoritative resource conflicts remain")
        if MaterialGate.Enabled:
            if (
                RewriteComposition.ComponentFunctionalShare
                < MaterialGate.MinimumComponentFunctionalShare
            ):
                QualityFailures.append(
                    "component functional share "
                    f"{RewriteComposition.ComponentFunctionalShare:.3%} < "
                    f"{MaterialGate.MinimumComponentFunctionalShare:.3%}"
                )
            if (
                RewriteComposition.RoutingFunctionalShare
                > MaterialGate.MaximumRoutingFunctionalShare
            ):
                QualityFailures.append(
                    "routing functional share "
                    f"{RewriteComposition.RoutingFunctionalShare:.3%} > "
                    f"{MaterialGate.MaximumRoutingFunctionalShare:.3%}"
                )
            if (
                RewriteComposition.RawDustFunctionalShare
                > MaterialGate.MaximumRawDustFunctionalShare
            ):
                QualityFailures.append(
                    "raw dust functional share "
                    f"{RewriteComposition.RawDustFunctionalShare:.3%} > "
                    f"{MaterialGate.MaximumRawDustFunctionalShare:.3%}"
                )
            if RewriteComposition.Footprint > MaterialGate.MaximumFootprint:
                QualityFailures.append(
                    f"exact footprint {RewriteComposition.Footprint} > "
                    f"{MaterialGate.MaximumFootprint}"
                )
            if RewriteComposition.NonAirBlocks > MaterialGate.MaximumNonAirBlocks:
                QualityFailures.append(
                    f"non-air blocks {RewriteComposition.NonAirBlocks} > "
                    f"{MaterialGate.MaximumNonAirBlocks}"
                )
        if QualityFailures:
            RejectedDiagnostics = {
                "Reason": "metric quality gate",
                "Failures": QualityFailures,
                "Metrics": {
                    "Length": RewriteMetrics.TotalLength if RewriteMetrics else 0,
                    "Bends": RewriteMetrics.BendCount if RewriteMetrics else 0,
                    "Vias": RewriteMetrics.ViaCount if RewriteMetrics else 0,
                    "OverflowPeak": (
                        RewriteMetrics.CorridorOverflowPeak if RewriteMetrics else 0
                    ),
                    "MaximumNetLengthShare": round(RewriteMaximumShare, 6),
                },
                "RoutingControls": RewriteRouted.RoutingControlEffectiveness,
                "BlockComposition": RewriteComposition.ToDictionary(),
            }
            Physical = PlaceAndRoutePcb(
                NandIR,
                ProgressCallback=ProgressCallback,
                Strategy=RoutingStrategy.Compatibility,
            )
            Physical.RequestedStrategy = RoutingStrategy.Hybrid.value
            Physical.FallbackUsed = True
            Physical.FallbackReason = "local-first rejected by metric quality gate: " + "; ".join(
                QualityFailures
            )
            Physical.RejectedRewriteDiagnostics = RejectedDiagnostics
    Stages.append("pcb_placement")
    Routed = Physical.Routed
    Stages.append("pcb_routing")
    Stages.append("route_cleanup")

    Simulation = SimulateRoutedTruthTable(
        Routed,
        ReferenceModule=OptimizedIR.Modules[OptimizedIR.Top],
    )
    if (
        not Simulation.Passed
        and RequestedStrategy == RoutingStrategy.Hybrid
        and Physical.UsedStrategy == RoutingStrategy.NewRouterFirst.value
    ):
        Failed = Simulation.FailedRows[0]
        SimulationFallbackReason = (
            "local-first physical simulation failed: "
            f"inputs={tuple(int(Value) for Value in Failed.Inputs)}"
        )
        Physical = PlaceAndRoutePcb(
            NandIR,
            ProgressCallback=ProgressCallback,
            Strategy=RoutingStrategy.Compatibility,
        )
        Physical.RequestedStrategy = RoutingStrategy.Hybrid.value
        Physical.FallbackUsed = True
        Physical.FallbackReason = SimulationFallbackReason
        Physical.RejectedRewriteDiagnostics = {
            "Reason": "physical simulation failure",
            "Failure": SimulationFallbackReason,
        }
        Routed = Physical.Routed
        Simulation = SimulateRoutedTruthTable(
            Routed,
            ReferenceModule=OptimizedIR.Modules[OptimizedIR.Top],
        )
    TruthTablePath = WriteTruthTable(Simulation, OutputPath.with_suffix(".TruthTable.txt"))
    Stages.append("redstone_simulation")
    Stages.append("truth_table")
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

    ValidateNandOnlyDesign(Physical.Placed, NandIR)
    Rendered = Writer262.BuildLitematicBlockMap(Routed)
    Composition = Rendered.Composition

    PhysicalDesignPath = OutputPath.with_suffix(".PhysicalDesign.json")
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
    PhysicalDesignPath.write_text(
        json.dumps(
            {
                "Strategy": {
                    "Requested": Physical.RequestedStrategy,
                    "Used": Physical.UsedStrategy,
                    "FallbackUsed": Physical.FallbackUsed,
                    "FallbackReason": Physical.FallbackReason,
                    "RejectedRewriteDiagnostics": Physical.RejectedRewriteDiagnostics,
                },
                "Policy": Physical.Policy.ToDictionary(),
                "Technology": asdict(Physical.Technology),
                "PlanningContracts": Physical.PlanningContracts,
                "GlobalGuidePlanning": Routed.GlobalGuideDiagnostics,
                "RoutingControlEffectiveness": Routed.RoutingControlEffectiveness,
                "RunSummary": {
                    "RuntimeSeconds": round(RuntimeSeconds, 6),
                    "Width": Composition.Width,
                    "Height": Composition.Height,
                    "Depth": Composition.Depth,
                    "Footprint": Composition.Footprint,
                    "EstimatedBlocks": Physical.EstimatedBlocks,
                    "ExactNonAirBlocks": Composition.NonAirBlocks,
                    "Length": Metrics.TotalLength if Metrics is not None else 0,
                    "Bends": Metrics.BendCount if Metrics is not None else 0,
                    "Vias": Metrics.ViaCount if Metrics is not None else 0,
                    "ReroutedNets": Metrics.ReroutedNets if Metrics is not None else 0,
                    "RoutingPasses": max(1, len(Metrics.Iterations)) if Metrics is not None else 0,
                    "Conflicts": Metrics.ConflictCount if Metrics is not None else 0,
                    "OverflowPeak": Metrics.CorridorOverflowPeak if Metrics is not None else 0,
                    "AccessOverflowPeak": Metrics.AccessOverflowPeak if Metrics is not None else 0,
                    "AccessOverflowCells": Metrics.AccessOverflowCells if Metrics is not None else 0,
                    "PerNetLength": PerNetLengths,
                    "MaximumNetLengthShare": round(MaximumNetLengthShare, 6),
                    "TruthTablePassed": Simulation.Passed,
                    "TruthTableRows": len(Simulation.Rows),
                    "SimulationBackend": Simulation.Backend,
                    "SimulationRuntimeSeconds": Simulation.RuntimeSeconds,
                },
                "BlockComposition": Composition.ToDictionary(),
                "NormalizedQuality": NormalizedQuality,
                "FinalValidation": {
                    "ZeroConflicts": Routed.ZeroResourceConflicts,
                    "ConflictCount": 0 if Routed.ZeroResourceConflicts else 1,
                    "UnresolvedClaims": UnresolvedClaims,
                    "UnresolvedClaimCount": len(UnresolvedClaims),
                },
                "RoutingResourceGraph": {
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
                },
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
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    Stages.append("physical_design_diagnostics")

    Writer262.WriteLitematic(Routed, OutputPath=OutputPath, Build=Rendered)
    Stages.append("litematic_writer")

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

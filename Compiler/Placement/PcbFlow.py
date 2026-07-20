"""PCB-only physical placement and routing orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from time import monotonic
from typing import Any, Callable

from ..Cells.Library import GetCellMacro
from ..Routing.Pcb import RoutePcbDesign
from ..Routing.Models import RoutedDesign
from ..Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from ..Routing.LocalFirst import (
    BuildLocalFirstSnapshot,
    MeasurePlacementRoutingFeedback,
)
from ..Routing.Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from ..Routing.Policy import (
    PolicyForRoutingStrategy,
    RoutingStrategy,
)
from ..Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from .Pcb import PlacePcbGraph
from .Rotation import RotatedCellSize
from .Geometry import PlacedDesign
from ..Synthesis.Validation import ValidateNandOnlyDesign


@dataclass(frozen=True)
class PcbProgress:
    Completed: int
    Total: int
    Workers: int
    Valid: int
    BestBlocks: int | None
    BestWidth: int | None
    BestDepth: int | None
    BestFootprint: int | None
    Failed: int
    Stage: str = "preparing routing"
    Unit: str = "routing passes"


@dataclass
class PcbResult:
    Placed: PlacedDesign
    Routed: RoutedDesign
    Footprint: int
    EstimatedBlocks: int
    Width: int
    Depth: int
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology
    RequestedStrategy: str = RoutingStrategy.Compatibility.value
    UsedStrategy: str = RoutingStrategy.Compatibility.value
    FallbackUsed: bool = False
    FallbackReason: str | None = None
    PlanningContracts: dict[str, object] | None = None
    RejectedRewriteDiagnostics: dict[str, object] | None = None


def MeasurePcbDesign(
    Placed: PlacedDesign,
    Routed: RoutedDesign,
) -> tuple[int, int, int, int]:
    """Measure the final PCB footprint and emitted block estimate."""
    Positions = list(Routed.Wires) + list(Routed.Supports)
    for Gate in Placed.PlacedGates:
        Width, Depth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        Positions.append((Gate.X, Gate.Y, Gate.Z))
        Positions.append((Gate.X + Width - 1, Gate.Y, Gate.Z + Depth - 1))
    if not Positions:
        return (0, 0, 0, 0)

    MinimumX = min(Position[0] for Position in Positions)
    MaximumX = max(Position[0] for Position in Positions)
    MinimumZ = min(Position[2] for Position in Positions)
    MaximumZ = max(Position[2] for Position in Positions)
    Width = MaximumX - MinimumX + 1
    Depth = MaximumZ - MinimumZ + 1
    Footprint = Width * Depth
    EstimatedBlocks = len(Routed.Wires) + sum(
        GetCellMacro(Gate.Kind).EstimatedBlocks
        for Gate in Placed.PlacedGates
    )
    return Footprint, EstimatedBlocks, Width, Depth


def PlaceAndRoutePcb(
    Netlist: Any,
    ProgressCallback: Callable[[PcbProgress], None] | None = None,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    Strategy: RoutingStrategy | str | None = None,
) -> PcbResult:
    """Place and route through compatibility, local-first, or hybrid policy."""
    if Strategy is None:
        return _PlaceAndRoutePcbWithPolicy(
            Netlist,
            ProgressCallback=ProgressCallback,
            Policy=Policy,
            Technology=Technology,
            RequestedStrategy=RoutingStrategy.Compatibility,
            UsedStrategy=RoutingStrategy.Compatibility,
        )
    RequestedStrategy = RoutingStrategy.Parse(Strategy)
    if RequestedStrategy == RoutingStrategy.Compatibility:
        return _PlaceAndRoutePcbWithPolicy(
            Netlist,
            ProgressCallback=ProgressCallback,
            Policy=PolicyForRoutingStrategy(RoutingStrategy.Compatibility),
            Technology=Technology,
            RequestedStrategy=RequestedStrategy,
            UsedStrategy=RoutingStrategy.Compatibility,
        )
    if RequestedStrategy == RoutingStrategy.NewRouterFirst:
        return _PlaceAndRoutePcbWithPolicy(
            Netlist,
            ProgressCallback=ProgressCallback,
            Policy=PolicyForRoutingStrategy(RoutingStrategy.NewRouterFirst),
            Technology=Technology,
            RequestedStrategy=RequestedStrategy,
            UsedStrategy=RoutingStrategy.NewRouterFirst,
        )
    try:
        return _PlaceAndRoutePcbWithPolicy(
            Netlist,
            ProgressCallback=ProgressCallback,
            Policy=PolicyForRoutingStrategy(RoutingStrategy.NewRouterFirst),
            Technology=Technology,
            RequestedStrategy=RequestedStrategy,
            UsedStrategy=RoutingStrategy.NewRouterFirst,
        )
    except (RoutingStageError, ValueError, NotImplementedError) as Error:
        Result = _PlaceAndRoutePcbWithPolicy(
            Netlist,
            ProgressCallback=ProgressCallback,
            Policy=PolicyForRoutingStrategy(RoutingStrategy.Compatibility),
            Technology=Technology,
            RequestedStrategy=RequestedStrategy,
            UsedStrategy=RoutingStrategy.Compatibility,
        )
        Result.FallbackUsed = True
        Result.FallbackReason = str(Error)
        return Result


def _PlaceAndRoutePcbWithPolicy(
    Netlist: Any,
    ProgressCallback: Callable[[PcbProgress], None] | None,
    Policy: PhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology,
    RequestedStrategy: RoutingStrategy,
    UsedStrategy: RoutingStrategy,
) -> PcbResult:
    """Execute one immutable policy through the template PCB backend."""
    Started = monotonic()
    Module = Netlist.Modules[Netlist.Top]
    ValidateNandOnlyDesign(Netlist)
    if not Module.Gates:
        EmptyPlaced = PlacedDesign(Module=Module, PlacedGates=[])
        EmptyRouted = RoutedDesign(
            Module=Module,
            PlacedGates=[],
            Wires=[],
            Supports=[],
            Repeaters={},
            NetWires={},
        )
        return PcbResult(
            Placed=EmptyPlaced,
            Routed=EmptyRouted,
            Footprint=0,
            EstimatedBlocks=0,
            Width=0,
            Depth=0,
            Policy=Policy,
            Technology=Technology,
            RequestedStrategy=RequestedStrategy.value,
            UsedStrategy=UsedStrategy.value,
        )

    RoutingSpacing = Policy.Placement.RoutingSpacing
    if ProgressCallback is not None:
        ProgressCallback(
            PcbProgress(
                Completed=0,
                Total=1,
                Workers=0,
                Valid=0,
                BestBlocks=None,
                BestWidth=None,
                BestDepth=None,
                BestFootprint=None,
                Failed=0,
                Stage=f"spacing {RoutingSpacing} | placing clustered NAND graph",
            )
        )
    PlacementCandidates = [
        (
            RoutingSpacing,
            PlacePcbGraph(
                Netlist,
                RoutingSpacing=RoutingSpacing,
                PlacementPolicy=Policy.Placement,
                ClusterPolicy=Policy.Clustering,
                MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                PackingPolicy=Policy.NandPacking,
            ),
        )
    ]
    if Policy.NandPacking.Enabled:
        PlacementCandidates.append(
            (
                RoutingSpacing,
                PlacePcbGraph(
                    Netlist,
                    RoutingSpacing=RoutingSpacing,
                    PlacementPolicy=Policy.Placement,
                    ClusterPolicy=Policy.Clustering,
                    MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                    MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                    PackingPolicy=replace(
                        Policy.NandPacking,
                        GraphBeamEnabled=False,
                    ),
                ),
            )
        )
        PlacementCandidates.append(
            (
                RoutingSpacing,
                PlacePcbGraph(
                    Netlist,
                    RoutingSpacing=RoutingSpacing,
                    PlacementPolicy=Policy.Placement,
                    ClusterPolicy=Policy.Clustering,
                    MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                    MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                    PackingPolicy=replace(
                        Policy.NandPacking,
                        GraphBeamEnabled=False,
                        MaximumLocalRouteLength=(
                            Policy.NandPacking.DirectConnectMaximumLength
                        ),
                    ),
                ),
            )
        )
        PlacementCandidates.append(
            (
                RoutingSpacing,
                PlacePcbGraph(
                    Netlist,
                    RoutingSpacing=RoutingSpacing,
                    PlacementPolicy=Policy.Placement,
                    ClusterPolicy=Policy.Clustering,
                    MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                    MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                    PackingPolicy=replace(
                        Policy.NandPacking,
                        MaximumLocalRouteLength=(
                            Policy.NandPacking.DirectConnectMaximumLength
                        ),
                    ),
                ),
            )
        )
        PlacementCandidates.append(
            (
                RoutingSpacing,
                PlacePcbGraph(
                    Netlist,
                    RoutingSpacing=RoutingSpacing,
                    PlacementPolicy=Policy.Placement,
                    ClusterPolicy=Policy.Clustering,
                    MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                    MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                    PackingPolicy=None,
                ),
            )
        )
    PlacementFeedback = []
    OrderedPlacements = []
    if (
        Policy.QualityTarget == "local-first"
        and Policy.Placement.RoutingFeedbackIterations > 0
    ):
        AlternativeCount = min(
            max(
                Policy.Placement.RoutingFeedbackIterations,
                Policy.NandPacking.PlacementFeedbackIterations,
            ),
            Policy.Placement.RoutingSpacingAlternatives,
        )
        for Delta in range(1, AlternativeCount + 1):
            AlternativeSpacing = max(0, RoutingSpacing - Delta)
            if AlternativeSpacing == RoutingSpacing:
                continue
            PlacementCandidates.append(
                (
                    AlternativeSpacing,
                    PlacePcbGraph(
                        Netlist,
                        RoutingSpacing=AlternativeSpacing,
                        PlacementPolicy=Policy.Placement,
                        ClusterPolicy=Policy.Clustering,
                        MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                        MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                        PackingPolicy=Policy.NandPacking,
                    ),
                )
            )
        ScoredPlacements = [
            (
                MeasurePlacementRoutingFeedback(
                    Candidate,
                    CandidateSpacing,
                    Policy,
                    Technology,
                ),
                Candidate,
            )
            for CandidateSpacing, Candidate in PlacementCandidates
        ]
        ScoredPlacements.sort(
            key=lambda Value: (
                0 if Value[1].PackedClusters else 1,
                Value[0].GuideOverflowPeak,
                Value[0].GuideOverflowCells,
                Value[0].PinEscapeConflictCount,
                -Value[0].FrozenLocalNetCount,
                -Value[0].PreOwnedNodeCount,
                abs(Value[0].RoutingSpacing - RoutingSpacing),
                Value[0].WeightedLocalityCost,
                Value[0].GateFootprint,
            )
        )
        PlacementFeedback = [
            Value.ToDictionary() for Value, _Candidate in ScoredPlacements
        ]
        SelectedFeedback, Placement = ScoredPlacements[0]
        RoutingSpacing = SelectedFeedback.RoutingSpacing
        OrderedPlacements = ScoredPlacements[
            : max(1, Policy.NandPacking.RetainedPlacementCandidates)
        ]
    else:
        Placement = PlacementCandidates[0][1]
        OrderedPlacements = [(None, Placement)]

    def ReportRoutingProgress(
        Completed: int,
        Total: int,
        Workers: int,
        Valid: int,
        Failed: int,
        BestRouted: RoutedDesign | None,
        Stage: str,
    ) -> None:
        if ProgressCallback is None:
            return
        BestFootprint = None
        BestBlocks = None
        BestWidth = None
        BestDepth = None
        if BestRouted is not None:
            (
                BestFootprint,
                BestBlocks,
                BestWidth,
                BestDepth,
            ) = MeasurePcbDesign(Placement.Placed, BestRouted)
        ProgressCallback(
            PcbProgress(
                Completed=Completed,
                Total=Total,
                Workers=Workers,
                Valid=Valid,
                BestBlocks=BestBlocks,
                BestWidth=BestWidth,
                BestDepth=BestDepth,
                BestFootprint=BestFootprint,
                Failed=Failed,
                Stage=f"spacing {RoutingSpacing} | {Stage}",
            )
        )

    LastRoutingError = None
    Routed = None
    PlacementAttemptFailures = []
    for Feedback, CandidatePlacement in OrderedPlacements:
        if monotonic() - Started >= Policy.RuntimeBudgetSeconds:
            LastRoutingError = RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.RuntimeBudgetExceeded,
                    Stage="Budget",
                    Detail=(
                        f"adaptive runtime budget {Policy.RuntimeBudgetSeconds:.3f}s exhausted"
                    ),
                )
            )
            break
        Placement = CandidatePlacement
        if Feedback is not None:
            RoutingSpacing = Feedback.RoutingSpacing
        try:
            Routed = RoutePcbDesign(
                Placement,
                ProgressCallback=ReportRoutingProgress,
                Policy=Policy,
            )
            break
        except (RoutingStageError, ValueError) as Error:
            LastRoutingError = Error
            PlacementAttemptFailures.append(
                {
                    "RoutingSpacing": RoutingSpacing,
                    "PackedNandPlacement": bool(CandidatePlacement.PackedClusters),
                    "Failure": str(Error),
                }
            )
    if Routed is None:
        assert LastRoutingError is not None
        raise LastRoutingError
    ValidateNandOnlyDesign(Placement.Placed, Netlist)
    if PlacementFeedback:
        Routed.RoutingControlEffectiveness["PlacementFeedbackCandidates"] = (
            PlacementFeedback
        )
        Routed.RoutingControlEffectiveness["SelectedRoutingSpacing"] = RoutingSpacing
        Routed.RoutingControlEffectiveness["PlacementAttemptFailures"] = (
            PlacementAttemptFailures
        )
    if ProgressCallback is not None:
        (
            FinalFootprint,
            FinalBlocks,
            FinalWidth,
            FinalDepth,
        ) = MeasurePcbDesign(Placement.Placed, Routed)
        ProgressCallback(
            PcbProgress(
                Completed=1,
                Total=1,
                Workers=0,
                Valid=1,
                BestBlocks=FinalBlocks,
                BestWidth=FinalWidth,
                BestDepth=FinalDepth,
                BestFootprint=FinalFootprint,
                Failed=0,
                Stage="routing complete",
            )
        )
    Routed.SupportBlock = Technology.DefaultSupportBlock
    Footprint, EstimatedBlocks, Width, Depth = MeasurePcbDesign(
        Placement.Placed,
        Routed,
    )
    Snapshot = BuildLocalFirstSnapshot(
        Placement,
        Routed,
        LocalFanoutDistance=Policy.Placement.LocalFanoutDistance,
        LocalRouteBudget=10,
    )
    PlanningContracts = Snapshot.ToDictionary()
    PlanningContracts["PackedNandClusters"] = [
        {
            "ClusterId": Cluster.ClusterId,
            "MemberNands": list(Cluster.MemberNands),
            "BoundarySignals": list(Cluster.BoundarySignals),
            "InternalSignals": list(Cluster.InternalSignals),
            "RelativePlacements": {
                Name: list(Value)
                for Name, Value in sorted(Cluster.RelativePlacements.items())
            },
            "DirectConnections": list(Cluster.DirectConnections),
            "LocalClaimSignals": list(Cluster.LocalClaimSignals),
            "BoundaryTerminals": [
                list(Position) for Position in Cluster.BoundaryTerminals
            ],
            "ExactLocalRoutingBlocks": Cluster.ExactLocalRoutingBlocks,
            "GlobalEntrances": Cluster.GlobalEntrances,
            "RejectionReasons": list(Cluster.RejectionReasons),
            "StructuralSignature": Cluster.StructuralSignature,
            "ReusedFromClusterId": Cluster.ReusedFromClusterId,
            "StructuralMapping": dict(sorted(
                (Cluster.StructuralMapping or {}).items()
            )),
        }
        for Cluster in Placement.PackedClusters
    ]
    PlanningContracts["StructuralReuse"] = {
        "Enabled": Policy.NandPacking.EnableStructuralReuse,
        "ReuseScope": "relative-placement",
        "LocalRoutesRecomputedAndValidated": True,
        "UniqueTemplates": len({
            Cluster.StructuralSignature
            for Cluster in Placement.PackedClusters
            if Cluster.StructuralSignature
        }),
        "ReusedClusters": sum(
            Cluster.ReusedFromClusterId is not None
            for Cluster in Placement.PackedClusters
        ),
    }
    PlanningContracts["LocalRouteClaims"] = [
        {
            "Signal": Claim.Signal,
            "ClusterId": Claim.ClusterId,
            "Root": list(Claim.Root),
            "ConnectedTargets": [list(Value) for Value in Claim.ConnectedTargets],
            "BoundaryNodes": [list(Value) for Value in Claim.BoundaryNodes],
            "NodeCount": len(Claim.Nodes),
            "EdgeCount": len(Claim.Edges),
            "PreOwnedResourceCount": len(Claim.Claims.ResourceIds),
            "ExactRouteSignalBlocks": Claim.ExactRouteSignalBlocks,
            "ExactRouteRefreshBlocks": Claim.ExactRouteRefreshBlocks,
            "ExactRouteSupportBlocks": Claim.ExactRouteSupportBlocks,
        }
        for Claim in Placement.Placed.LocalRouteClaims
    ]
    PlanningContracts["LocalRouteDiagnostics"] = (
        Placement.Placed.LocalRouteDiagnostics or {}
    )
    PlanningContracts["RoutingDemandEstimate"] = (
        Routed.RoutingControlEffectiveness.get("RoutingDemandEstimate", {})
    )
    PlanningContracts["DerivedRoutingBudget"] = (
        Routed.RoutingControlEffectiveness.get("DerivedRoutingBudget", {})
    )
    return PcbResult(
        Placed=Placement.Placed,
        Routed=Routed,
        Footprint=Footprint,
        EstimatedBlocks=EstimatedBlocks,
        Width=Width,
        Depth=Depth,
        Policy=Policy,
        Technology=Technology,
        RequestedStrategy=RequestedStrategy.value,
        UsedStrategy=UsedStrategy.value,
        PlanningContracts=PlanningContracts,
    )

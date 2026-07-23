"""PCB-only physical placement and routing orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
import os
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
from ..Routing.Reliability import BuildStableFingerprint, RoutingDeadline
from ..Routing.Actions import ValidatePlacedCellElectricalIsolation
from ..Routing.Actions.Geometry import BuildRoutingResources
from ..Routing.Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from ..Routing.Policy import (
    ExecutionStrategyForRequest,
    PolicyForRoutingStrategy,
    RoutingStrategy,
)
from ..Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from .Pcb import (
    FindMandatoryAccessConflictSignals,
    PcbPlacement,
    PlacePcbGraph,
)
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
    RequestedStrategy: str = RoutingStrategy.NewRouterFirst.value
    UsedStrategy: str = RoutingStrategy.NewRouterFirst.value
    FallbackUsed: bool = False
    FallbackReason: str | None = None
    PlanningContracts: dict[str, object] | None = None
    RejectedRewriteDiagnostics: dict[str, object] | None = None


@dataclass(frozen=True)
class PcbPlacementCandidate:
    """One deterministic legal placement retained for authoritative routing."""

    CandidateId: str
    SourceGenerator: str
    RoutingSpacing: int
    PlacementFingerprint: str
    FeedbackScore: tuple[int, ...]
    BoundaryOverflow: int
    PinScarcityCount: int
    GuideOverflowPeak: int
    GuideOverflowCells: int
    PinEscapeConflictCount: int
    EstimatedGlobalExtensionNodes: int
    EstimatedGlobalExtensionNets: int
    PreOwnedNodeCount: int
    Placement: PcbPlacement
    Feedback: Any | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CandidateId": self.CandidateId,
            "SourceGenerator": self.SourceGenerator,
            "RoutingSpacing": self.RoutingSpacing,
            "PlacementFingerprint": self.PlacementFingerprint,
            "FeedbackScore": list(self.FeedbackScore),
            "BoundaryOverflow": self.BoundaryOverflow,
            "PinScarcityCount": self.PinScarcityCount,
            "GuideOverflowPeak": self.GuideOverflowPeak,
            "GuideOverflowCells": self.GuideOverflowCells,
            "PinEscapeConflictCount": self.PinEscapeConflictCount,
            "EstimatedGlobalExtensionNodes": (
                self.EstimatedGlobalExtensionNodes
            ),
            "EstimatedGlobalExtensionNets": self.EstimatedGlobalExtensionNets,
            "PreOwnedNodeCount": self.PreOwnedNodeCount,
            "RoutePressure": (
                self.PreOwnedNodeCount + self.EstimatedGlobalExtensionNodes
            ),
            "PackedNandPlacement": bool(self.Placement.PackedClusters),
            "LocalClaimCount": len(
                self.Placement.Placed.LocalRouteClaims or ()
            ),
        }


def ApplyRoutingRuntimeBudget(
    Policy: PhysicalDesignPolicy,
    RoutingDeadlineSeconds: float | None,
) -> PhysicalDesignPolicy:
    """Return the immutable policy carrying the effective absolute budget."""
    if RoutingDeadlineSeconds is None:
        return Policy
    if (
        isinstance(RoutingDeadlineSeconds, bool)
        or not isfinite(RoutingDeadlineSeconds)
        or RoutingDeadlineSeconds <= 0
    ):
        raise ValueError("RoutingDeadlineSeconds must be finite and positive")
    EffectiveSeconds = float(RoutingDeadlineSeconds)
    return replace(
        Policy,
        RuntimeBudgetSeconds=EffectiveSeconds,
        AdaptiveRouting=replace(
            Policy.AdaptiveRouting,
            MaximumRuntimeSeconds=min(
                Policy.AdaptiveRouting.MaximumRuntimeSeconds,
                EffectiveSeconds,
            ),
        ),
    )


@dataclass(frozen=True)
class PlacementGenerationRequest:
    """One deterministic placement recipe, before its expensive construction."""

    SourceGenerator: str
    RoutingSpacing: int
    PackingPolicy: Any


@dataclass(frozen=True)
class PlacementGenerationPlan:
    """Bounded primary recipes plus spacing recipes deferred until useful."""

    PrimaryRequests: tuple[PlacementGenerationRequest, ...]
    DeferredRequests: tuple[PlacementGenerationRequest, ...]
    MaximumAttempts: int


def BuildPlacementGenerationPlan(
    Policy: PhysicalDesignPolicy,
    PreferPackedPlacements: bool = False,
) -> PlacementGenerationPlan:
    """Build a deterministic, recipe-deduplicated placement generation plan."""
    RoutingSpacing = Policy.Placement.RoutingSpacing
    InitialPackedSpacing = RoutingSpacing
    PrimaryRequests: list[PlacementGenerationRequest] = []
    DeferredRequests: list[PlacementGenerationRequest] = []
    RecipeKeys: set[tuple[int, Any]] = set()

    def AddRequest(
        Target: list[PlacementGenerationRequest],
        SourceGenerator: str,
        CandidateSpacing: int,
        CandidatePacking: Any,
    ) -> None:
        RecipeKey = (CandidateSpacing, CandidatePacking)
        if RecipeKey in RecipeKeys:
            return
        RecipeKeys.add(RecipeKey)
        Target.append(
            PlacementGenerationRequest(
                SourceGenerator=SourceGenerator,
                RoutingSpacing=CandidateSpacing,
                PackingPolicy=CandidatePacking,
            )
        )

    if Policy.NandPacking.Enabled:
        # Start with the bounded row construction and the unpacked oracle.
        # Structure-aware alternatives remain available after both primaries
        # fail, under the same absolute deadline.
        UnpackedSpacing = (
            max(0, RoutingSpacing - 1)
            if Policy.Placement.EnableRoutingFeedback
            else RoutingSpacing
        )
        DeferUnpackedOracle = PreferPackedPlacements
        AddRequest(
            PrimaryRequests,
            "row-beam",
            InitialPackedSpacing,
            replace(Policy.NandPacking, GraphBeamEnabled=False),
        )
        if not DeferUnpackedOracle:
            AddRequest(
                PrimaryRequests,
                "unpacked",
                UnpackedSpacing,
                replace(Policy.NandPacking, Enabled=False),
            )
        # This intentionally repeats the primary row recipe after routing
        # feedback exists. RelocationSignals makes it new physical geometry,
        # so recipe-level deduplication must not discard it.
        DeferredRequests.append(
            PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=InitialPackedSpacing,
                PackingPolicy=replace(
                    Policy.NandPacking,
                    GraphBeamEnabled=False,
                ),
            )
        )
        AddRequest(
            DeferredRequests,
            "row-beam-direct-only",
            InitialPackedSpacing,
            replace(
                Policy.NandPacking,
                GraphBeamEnabled=False,
                MaximumLocalRouteLength=(
                    Policy.NandPacking.DirectConnectMaximumLength
                ),
            ),
        )
        if DeferUnpackedOracle:
            AddRequest(
                DeferredRequests,
                "unpacked",
                UnpackedSpacing,
                replace(Policy.NandPacking, Enabled=False),
            )
        if (
            Policy.Placement.EnableRoutingFeedback
            and Policy.Placement.RoutingSpacingAlternatives > 0
        ):
            for Delta in range(
                1,
                min(
                    Policy.Placement.RoutingFeedbackIterations,
                    Policy.Placement.RoutingSpacingAlternatives,
                ) + 1,
            ):
                WiderSpacing = RoutingSpacing + Delta
                AddRequest(
                    DeferredRequests,
                    f"unpacked-spacing-{WiderSpacing}",
                    WiderSpacing,
                    replace(Policy.NandPacking, Enabled=False),
                )
        if UnpackedSpacing != RoutingSpacing:
            AddRequest(
                DeferredRequests,
                "unpacked-configured-spacing",
                RoutingSpacing,
                replace(Policy.NandPacking, Enabled=False),
            )
        AddRequest(
            DeferredRequests,
            "configured-packing",
            RoutingSpacing,
            Policy.NandPacking,
        )
        AddRequest(
            DeferredRequests,
            "graph-beam-direct-only",
            RoutingSpacing,
            replace(
                Policy.NandPacking,
                MaximumLocalRouteLength=(
                    Policy.NandPacking.DirectConnectMaximumLength
                ),
            ),
        )
    else:
        AddRequest(
            PrimaryRequests,
            "configured-packing",
            RoutingSpacing,
            Policy.NandPacking,
        )

    if (
        Policy.Placement.EnableRoutingFeedback
        and Policy.Placement.RoutingFeedbackIterations > 0
    ):
        AlternativeCount = min(
            Policy.Placement.RoutingFeedbackIterations,
            Policy.Placement.RoutingSpacingAlternatives,
        )
        for Delta in range(1, AlternativeCount + 1):
            for AlternativeSpacing in (
                max(0, RoutingSpacing - Delta),
                RoutingSpacing + Delta,
            ):
                if AlternativeSpacing == RoutingSpacing:
                    continue
                AddRequest(
                    DeferredRequests,
                    f"spacing-{AlternativeSpacing}",
                    AlternativeSpacing,
                    Policy.NandPacking,
                )

    if Policy.NandPacking.Enabled and PreferPackedPlacements:
        PackedGeneratorPriority = {
            "row-beam-conflict-relocation": 0,
            "row-beam-direct-only": 1,
            "configured-packing": 2,
            "graph-beam-direct-only": 3,
            "unpacked": 4,
        }
        DeferredRequests.sort(
            key=lambda Request: (
                PackedGeneratorPriority.get(Request.SourceGenerator, 3),
                Request.SourceGenerator,
            )
        )

    MaximumAttempts = len(PrimaryRequests) + len(DeferredRequests)
    return PlacementGenerationPlan(
        PrimaryRequests=tuple(PrimaryRequests),
        DeferredRequests=tuple(DeferredRequests),
        MaximumAttempts=max(1, MaximumAttempts),
    )


def PlacementCandidateOrder(
    Value: PcbPlacementCandidate,
    ConfiguredSpacing: int,
) -> tuple[object, ...]:
    """Return the stable demand-first order used for placement failover."""
    return (
        0 if Value.Placement.PackedClusters else 1,
        0 if (Value.Placement.Placed.LocalRouteClaims or ()) else 1,
        Value.FeedbackScore,
        abs(Value.RoutingSpacing - ConfiguredSpacing),
        Value.PlacementFingerprint,
    )


def PlacementNeedsDemandDiversity(
    Candidates: list[PcbPlacementCandidate],
    ConfiguredSpacing: int,
) -> bool:
    """Return whether the best generated placement still needs more diversity."""
    if not Candidates:
        return True
    Best = min(
        Candidates,
        key=lambda Value: PlacementCandidateOrder(Value, ConfiguredSpacing),
    )
    return any((
        Best.BoundaryOverflow,
        Best.PinScarcityCount,
        Best.GuideOverflowPeak,
        Best.GuideOverflowCells,
        Best.PinEscapeConflictCount,
    ))


def PlacementGenerationRoutingReserveSeconds(
    Policy: PhysicalDesignPolicy,
) -> float:
    """Reserve an explicit part of the one deadline for routing a legal candidate."""
    TotalSeconds = Policy.RuntimeBudgetSeconds
    return min(
        max(0.0, TotalSeconds - 0.001),
        Policy.AdaptiveRouting.MaximumRuntimeSeconds,
        max(0.01, TotalSeconds * 0.20),
    )


def FailureRequestsPlacementAdvance(Failure: RoutingFailure) -> bool:
    """Return whether a typed failure forbids same-candidate recovery work."""
    Diagnostics = Failure.Diagnostics or {}
    Action = str(Diagnostics.get("Action", ""))
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    return (
        Action.startswith("advance-placement")
        or Failure.Reason == RoutingFailureReason.NoPinAccessPattern
        or any(
            str(RepairAction).startswith("AdvancePlacement")
            for RepairAction in Failure.RepairActions
        )
        or (
            isinstance(ConflictGraph, dict)
            and ConflictGraph.get("Classification")
            == "mandatory-boundary-capacity-cut"
        )
    )


def FailureRequiresPackedAccessRepair(Failure: RoutingFailure) -> bool:
    """Return whether a typed fixed-access cut requires local geometry repair."""
    ConflictGraph = (Failure.Diagnostics or {}).get("ConflictGraph", {})
    return (
        Failure.Reason == RoutingFailureReason.NoPinAccessPattern
        or (
            isinstance(ConflictGraph, dict)
            and ConflictGraph.get("Classification")
            == "mandatory-boundary-capacity-cut"
        )
    )


def ExtractPlacementRelocationSignals(
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Return typed routing offenders that should alter later placement."""
    # AffectedNets is allowed to describe the larger assignment frontier.  A
    # structured conflict graph is the more precise physical diagnosis, so do
    # not turn a three-net conflict back into a broad cluster move by unioning
    # the whole frontier into it.
    Signals: set[str] = set()
    Diagnostics = Failure.Diagnostics or {}
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    if isinstance(ConflictGraph, dict):
        RelocationValues = ConflictGraph.get("RelocationSignals", ())
        if isinstance(RelocationValues, tuple | list) and RelocationValues:
            return frozenset(str(Value) for Value in RelocationValues)
        for Key in (
            "ConflictSignals",
            "NativeConflictSignals",
            "NoCandidateSignals",
            "CumulativeConflictSignals",
            "CongestionCutSignals",
            "ConflictCutSignals",
        ):
            Values = ConflictGraph.get(Key, ())
            if isinstance(Values, tuple | list):
                Signals.update(str(Value) for Value in Values)
        Rebalancing = ConflictGraph.get("ConflictResources", ())
        if isinstance(Rebalancing, dict):
            Signals.update(
                str(Signal)
                for SignalsForResource in Rebalancing.values()
                if isinstance(SignalsForResource, tuple | list)
                for Signal in SignalsForResource
            )
        Pairwise = ConflictGraph.get("PairwiseIncompatibleEdges", ())
        if isinstance(Pairwise, tuple | list):
            Signals.update(
                str(Signal)
                for Pair in Pairwise
                if isinstance(Pair, tuple | list)
                for Signal in Pair
            )
    for Key in ("ConflictSignals", "NativeConflictSignals"):
        Values = Diagnostics.get(Key, ())
        if isinstance(Values, tuple | list):
            Signals.update(str(Value) for Value in Values)
    if not Signals:
        Signals.update(str(Value) for Value in Failure.AffectedNets)
    return frozenset(sorted(Signals))


def BuildPlacementFingerprint(Placement: PcbPlacement) -> str:
    """Fingerprint geometry and pre-owned local claims for deduplication."""
    return BuildStableFingerprint({
        "Gates": [
            (
                Gate.Name,
                Gate.Kind,
                Gate.X,
                Gate.Y,
                Gate.Z,
                Gate.Rotation,
                getattr(Gate, "MirrorX", False),
            )
            for Gate in sorted(
                Placement.Placed.PlacedGates,
                key=lambda Value: Value.Name,
            )
        ],
        "LocalClaims": [
            (
                Claim.Signal,
                Claim.ClusterId,
                tuple(sorted(Claim.Nodes)),
            )
            for Claim in sorted(
                Placement.Placed.LocalRouteClaims or (),
                key=lambda Value: (Value.Signal, Value.ClusterId),
            )
        ],
    })


def SelectReleasableLocalClaimSignals(
    AffectedSignals: frozenset[str],
    Claims: tuple[Any, ...],
) -> frozenset[str]:
    """Return only affected signals that actually own local claims."""
    AvailableSignals = frozenset(Claim.Signal for Claim in Claims)
    return AffectedSignals & AvailableSignals


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
    RoutedValidationCallback: Callable[[RoutedDesign], None] | None = None,
    RoutingDeadlineSeconds: float | None = None,
) -> PcbResult:
    """Run the explicitly selected router without an automatic fallback."""
    RequestedStrategy = RoutingStrategy.Parse(Strategy or RoutingStrategy.NewRouterFirst)
    UsedStrategy = ExecutionStrategyForRequest(RequestedStrategy)
    ActivePolicy = (
        Policy
        if Policy != DefaultPhysicalDesignPolicy
        and UsedStrategy != RoutingStrategy.Compatibility
        else PolicyForRoutingStrategy(UsedStrategy)
    )
    ActivePolicy = ApplyRoutingRuntimeBudget(
        ActivePolicy,
        RoutingDeadlineSeconds,
    )
    return _PlaceAndRoutePcbWithPolicy(
        Netlist,
        ProgressCallback=ProgressCallback,
        Policy=ActivePolicy,
        Technology=Technology,
        RequestedStrategy=RequestedStrategy,
        UsedStrategy=UsedStrategy,
        RoutedValidationCallback=RoutedValidationCallback,
    )


def _PlaceAndRoutePcbWithPolicy(
    Netlist: Any,
    ProgressCallback: Callable[[PcbProgress], None] | None,
    Policy: PhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology,
    RequestedStrategy: RoutingStrategy,
    UsedStrategy: RoutingStrategy,
    RoutedValidationCallback: Callable[[RoutedDesign], None] | None = None,
) -> PcbResult:
    """Execute one immutable policy through the template PCB backend."""
    Deadline = RoutingDeadline.Start(Policy.RuntimeBudgetSeconds)
    Started = Deadline.StartedAt
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
    ConfiguredRoutingSpacing = RoutingSpacing
    PlacementGenerationFailures: list[dict[str, object]] = []
    PlacementGenerationDecisions: list[dict[str, object]] = []
    LastStructuredPlacementFailure: RoutingFailure | None = None
    UniquePlacements: dict[str, tuple[str, int, PcbPlacement]] = {}
    FeedbackByFingerprint: dict[str, Any] = {}
    PlacementAttemptFailures: list[dict[str, object]] = []
    LastRoutingError: Exception | None = None
    LastStructuredRoutingError: RoutingStageError | None = None
    NeedsFeedbackPlacementGeneration = False
    GenerationPlan = BuildPlacementGenerationPlan(
        Policy,
        PreferPackedPlacements=(
            Policy.NegotiatedRouting.Enabled
            and Policy.NandPacking.Enabled
            and Policy.NandPacking.DeferUnpackedOracle
        ),
    )
    if GenerationPlan.PrimaryRequests:
        ConfiguredRoutingSpacing = (
            GenerationPlan.PrimaryRequests[0].RoutingSpacing
        )
    PlacementGenerationAttempts = 0
    DeferredRequestIndex = 0
    PlacementRelocationSignals: frozenset[str] = frozenset()
    PlacementRelocationPrioritySignals: frozenset[str] = frozenset()
    PlacementRequiredRelocationSignals: frozenset[str] = frozenset()
    LastRelocationSignalsUsed: frozenset[str] = frozenset()
    LastRelocationPrioritySignalsUsed: frozenset[str] = frozenset()
    LastRequiredRelocationSignalsUsed: frozenset[str] = frozenset()
    RelocationGenerationCount = 0
    TotalRelocationGenerationCount = 0
    BaselinePackedGateArea: int | None = None
    RejectedPlacementFingerprints: set[str] = set()
    ProactiveRelocationRequested = False

    def _PackedGateArea(Candidate: PcbPlacement) -> int:
        Gates = Candidate.Placed.PlacedGates
        if not Gates:
            return 0
        MinimumX = min(Gate.X for Gate in Gates)
        MinimumZ = min(Gate.Z for Gate in Gates)
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
            for Gate in Gates
        )
        MaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
            for Gate in Gates
        )
        return (MaximumX - MinimumX + 1) * (MaximumZ - MinimumZ + 1)

    def _InterClusterSignals(Candidate: PcbPlacement) -> frozenset[str]:
        """Return signals whose endpoints span packed-cluster ownership."""
        ClusterByGate = {
            Name: ClusterIndex
            for ClusterIndex, Cluster in enumerate(Candidate.Clusters)
            for Name in Cluster
        }
        ProducerCluster = {
            Signal: ClusterByGate.get(Gate.Name)
            for Gate in Module.Gates
            for Signal in Gate.Outputs
        }
        Result: set[str] = set()
        for Gate in Module.Gates:
            TargetCluster = ClusterByGate.get(Gate.Name)
            if TargetCluster is None:
                continue
            for Signal in Gate.Inputs:
                SourceCluster = ProducerCluster.get(Signal)
                if SourceCluster is None or SourceCluster != TargetCluster:
                    Result.add(Signal)
        return frozenset(Result)

    def _PlacementFailureWithHistory(
        Failure: RoutingFailure,
    ) -> RoutingFailure:
        Diagnostics = dict(Failure.Diagnostics or {})
        Diagnostics.update({
            "PlacementGenerationFailures": PlacementGenerationFailures,
            "PlacementGenerationDecisions": PlacementGenerationDecisions,
            "PlacementAttempts": PlacementAttemptFailures,
            "Deadline": Deadline.ToDictionary(),
        })
        return RoutingFailure(
            Reason=Failure.Reason,
            Stage=Failure.Stage,
            AffectedNets=Failure.AffectedNets,
            Resources=Failure.Resources,
            Locations=Failure.Locations,
            RepairActions=Failure.RepairActions,
            Detail=Failure.Detail,
            Diagnostics=Diagnostics,
        )

    def _TryPlacement(
        Request: PlacementGenerationRequest,
    ) -> bool:
        nonlocal PlacementGenerationAttempts, LastStructuredPlacementFailure
        nonlocal LastRelocationSignalsUsed
        nonlocal LastRelocationPrioritySignalsUsed
        nonlocal LastRequiredRelocationSignalsUsed
        nonlocal RelocationGenerationCount
        nonlocal TotalRelocationGenerationCount
        nonlocal BaselinePackedGateArea
        nonlocal PlacementRelocationSignals
        nonlocal PlacementRelocationPrioritySignals
        nonlocal PlacementRequiredRelocationSignals
        nonlocal ProactiveRelocationRequested
        if PlacementGenerationAttempts >= GenerationPlan.MaximumAttempts:
            return False
        PlacementGenerationAttempts += 1
        SourceGenerator = Request.SourceGenerator
        if SourceGenerator == "row-beam-conflict-relocation":
            RelocationInputsChanged = (
                PlacementRelocationSignals != LastRelocationSignalsUsed
                or PlacementRelocationPrioritySignals
                != LastRelocationPrioritySignalsUsed
                or PlacementRequiredRelocationSignals
                != LastRequiredRelocationSignalsUsed
            )
            if RelocationInputsChanged:
                RelocationGenerationCount = 0
            LastRelocationSignalsUsed = PlacementRelocationSignals
            LastRelocationPrioritySignalsUsed = (
                PlacementRelocationPrioritySignals
            )
            LastRequiredRelocationSignalsUsed = (
                PlacementRequiredRelocationSignals
            )
            RelocationVariant = TotalRelocationGenerationCount + 1
            RelocationGenerationCount += 1
            RelocationSpacingLevel = min(
                TotalRelocationGenerationCount,
                Policy.Placement.RoutingSpacingAlternatives,
            )
            if Policy.NegotiatedRouting.Enabled:
                RelocationSpacingLevel = 0
            if (
                ConfiguredRoutingSpacing
                > Policy.Placement.RoutingSpacing
            ):
                RelocationSpacingLevel = 0
            TotalRelocationGenerationCount += 1
        else:
            RelocationVariant = 0
            RelocationSpacingLevel = 0
        # Every deferred generator evaluates the same cumulative congestion
        # cut.  The dedicated relocation recipe is skipped separately when
        # this set is empty; stripping feedback from later packed recipes
        # recreates the placement that already failed.
        EffectiveRelocationSignals = PlacementRelocationSignals
        EffectiveRelocationPrioritySignals = PlacementRelocationPrioritySignals
        EffectiveRequiredRelocationSignals = PlacementRequiredRelocationSignals
        CandidateSpacing = Request.RoutingSpacing + RelocationSpacingLevel
        CandidatePacking = Request.PackingPolicy
        PlacementStarted = monotonic()
        IsDeferredRequest = Request in GenerationPlan.DeferredRequests
        RemainingGenerationSlots = (
            1
            if IsDeferredRequest
            else max(
                1,
                len(GenerationPlan.PrimaryRequests)
                - PlacementGenerationAttempts
                + 1,
            )
        )
        RoutingReserveSeconds = min(
            PlacementGenerationRoutingReserveSeconds(Policy),
            max(0.01, Deadline.RemainingSeconds() * 0.5),
        )
        AvailableGenerationSeconds = max(
            0.0,
            Deadline.RemainingSeconds() - RoutingReserveSeconds,
        )
        if AvailableGenerationSeconds <= 0:
            PlacementGenerationDecisions.append({
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "Result": "skipped-routing-reserve",
                "RoutingReserveSeconds": round(RoutingReserveSeconds, 6),
                "RemainingSeconds": round(Deadline.RemainingSeconds(), 6),
                "PlacementAttempts": list(PlacementAttemptFailures),
            })
            if not UniquePlacements:
                LastStructuredPlacementFailure = RoutingFailure(
                    Reason=RoutingFailureReason.Stagnated,
                    Stage="PlacementGeneration",
                    Detail=(
                        "placement generation reached the routing reserve "
                        "before producing an exact-legal candidate"
                    ),
                    RepairActions=("AdvancePlacementGenerator",),
                    Diagnostics={
                        "SourceGenerator": SourceGenerator,
                        "RoutingReserveSeconds": RoutingReserveSeconds,
                        "PlacementAttempts": PlacementAttemptFailures,
                    },
                )
                PlacementGenerationFailures.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "PackedNandPlacement": bool(CandidatePacking.Enabled),
                    "Failure": LastStructuredPlacementFailure.Detail,
                    "PlacementGenerationBudgetSeconds": 0.0,
                    "ElapsedSeconds": 0.0,
                    "Diagnostics": (
                        LastStructuredPlacementFailure.ToDictionary()
                    ),
                })
            return False
        PlacementGenerationBudgetSeconds = max(
            0.001,
            AvailableGenerationSeconds / RemainingGenerationSlots,
        )
        PlacementGenerationExpiresAt = min(
            Deadline.ExpiresAt,
            PlacementStarted + PlacementGenerationBudgetSeconds,
        )

        def CheckPlacementGeneration(
            Diagnostics: dict[str, object],
        ) -> None:
            Current = monotonic()
            if (
                Current < Deadline.ExpiresAt
                and Current < PlacementGenerationExpiresAt
            ):
                return
            FailureDiagnostics = {
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "PlacementGenerationAttempt": PlacementGenerationAttempts,
                "MaximumPlacementGenerationAttempts": (
                    GenerationPlan.MaximumAttempts
                ),
                "PlacementGenerationFailures": PlacementGenerationFailures,
                "PlacementGenerationDecisions": PlacementGenerationDecisions,
                "PlacementAttempts": PlacementAttemptFailures,
                "PlacementGenerationDeadline": {
                    "RuntimeBudgetSeconds": round(
                        PlacementGenerationBudgetSeconds,
                        6,
                    ),
                    "ElapsedSeconds": round(
                        Current - PlacementStarted,
                        6,
                    ),
                    "Expired": Current >= PlacementGenerationExpiresAt,
                    "LimitedByGlobalDeadline": (
                        PlacementGenerationExpiresAt >= Deadline.ExpiresAt
                    ),
                    "RoutingReserveSeconds": round(
                        RoutingReserveSeconds,
                        6,
                    ),
                },
                **Diagnostics,
            }
            Deadline.RaiseIfExpired("Placement", FailureDiagnostics)
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.Stagnated,
                    Stage="PlacementGeneration",
                    Detail=(
                        "per-candidate placement generation slice expired; "
                        "advance to the next deterministic generator"
                    ),
                    RepairActions=("AdvancePlacementGenerator",),
                    Diagnostics=FailureDiagnostics,
                )
            )
        Fingerprint: str | None = None
        try:
            CheckPlacementGeneration({"Phase": "placement-generation-start"})
            Candidate = PlacePcbGraph(
                Netlist,
                RoutingSpacing=CandidateSpacing,
                PlacementPolicy=Policy.Placement,
                ClusterPolicy=Policy.Clustering,
                MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
                MaximumEntrancesPerSignal=Policy.Organization.MaximumClusterEntrancesPerSignal,
                PackingPolicy=CandidatePacking,
                RelocationSignals=EffectiveRelocationSignals,
                RelocationPrioritySignals=(
                    EffectiveRelocationPrioritySignals
                ),
                RequiredRelocationSignals=(
                    EffectiveRequiredRelocationSignals
                ),
                RelocationVariant=RelocationVariant,
                WorkCheck=CheckPlacementGeneration,
            )
            PackedGateArea = _PackedGateArea(Candidate)
            if (
                CandidatePacking.Enabled
                and SourceGenerator != "row-beam-conflict-relocation"
                and BaselinePackedGateArea is None
            ):
                BaselinePackedGateArea = PackedGateArea
            MaximumPackedGateArea = (
                int(
                    BaselinePackedGateArea
                    * Policy.NegotiatedRouting.MaximumPackedAreaGrowth
                )
                if BaselinePackedGateArea is not None
                else None
            )
            if (
                CandidatePacking.Enabled
                and MaximumPackedGateArea is not None
                and PackedGateArea > MaximumPackedGateArea
            ):
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "rejected-packed-area-growth",
                    "PackedGateArea": PackedGateArea,
                    "BaselinePackedGateArea": BaselinePackedGateArea,
                    "MaximumPackedGateArea": MaximumPackedGateArea,
                })
                return False
            RecipeDiagnostics = dict(
                Candidate.Placed.LocalRouteDiagnostics or {}
            )
            RecipeDiagnostics["__PlacementRecipe__"] = {
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "Packed": bool(CandidatePacking.Enabled),
            }
            Candidate.Placed.LocalRouteDiagnostics = RecipeDiagnostics
            CheckPlacementGeneration({
                "Phase": "placement-construction-complete",
            })
            ValidatePlacedCellElectricalIsolation(
                Candidate.Placed,
                WorkCheck=CheckPlacementGeneration,
            )
            CheckPlacementGeneration({
                "Phase": "exact-isolation-complete",
            })
            BuildRoutingResources(
                Candidate.Placed,
                WorkCheck=CheckPlacementGeneration,
            )
            CheckPlacementGeneration({
                "Phase": "routing-resource-construction-complete",
            })
            MandatoryConflicts = (
                FindMandatoryAccessConflictSignals(
                    Candidate.Placed.PlacedGates,
                    Candidate.SignalOrder,
                )
                if (
                    CandidatePacking.Enabled
                    and CandidatePacking.EnableProactiveInterClusterRelocation
                )
                else {}
            )
            if MandatoryConflicts and SourceGenerator != "row-beam-conflict-relocation":
                ConflictSignals = frozenset(
                    Signal
                    for Owners in MandatoryConflicts.values()
                    for Signal in Owners
                )
                PlacementRelocationSignals |= ConflictSignals
                PlacementRelocationPrioritySignals |= ConflictSignals
                PlacementRequiredRelocationSignals |= ConflictSignals
                ProactiveRelocationRequested = True
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "rejected-mandatory-access-conflict",
                    "ConflictSignals": sorted(ConflictSignals),
                    "ConflictResourceCount": len(MandatoryConflicts),
                    "ElapsedSeconds": round(
                        monotonic() - PlacementStarted, 6
                    ),
                })
                return False
            ProactiveSignals = (
                _InterClusterSignals(Candidate)
                if (
                    CandidatePacking.Enabled
                    and CandidatePacking.EnableProactiveInterClusterRelocation
                    and SourceGenerator == "row-beam"
                    and len(Candidate.PackedClusters) > 1
                )
                else frozenset()
            )
            if ProactiveSignals:
                PlacementRelocationSignals |= ProactiveSignals
                PlacementRelocationPrioritySignals |= ProactiveSignals
                PlacementRequiredRelocationSignals |= ProactiveSignals
                ProactiveRelocationRequested = True
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "deferred-for-inter-cluster-relocation",
                    "SignalCount": len(ProactiveSignals),
                    "Signals": sorted(ProactiveSignals),
                    "ElapsedSeconds": round(
                        monotonic() - PlacementStarted, 6
                    ),
                })
                return False
            Fingerprint = BuildPlacementFingerprint(Candidate)
            CheckPlacementGeneration({
                "Phase": "placement-fingerprint-complete",
            })
            if Fingerprint in RejectedPlacementFingerprints:
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "rejected-placement-repeat",
                    "PlacementFingerprint": Fingerprint,
                    "ElapsedSeconds": round(
                        monotonic() - PlacementStarted,
                        6,
                    ),
                })
                return False
            Existing = UniquePlacements.get(Fingerprint)
            if Existing is not None:
                PlacementGenerationDecisions.append({
                    "SourceGenerator": SourceGenerator,
                    "RoutingSpacing": CandidateSpacing,
                    "Result": "duplicate-placement",
                    "PlacementFingerprint": Fingerprint,
                    "DuplicateOf": Existing[0],
                    "ElapsedSeconds": round(
                        monotonic() - PlacementStarted,
                        6,
                    ),
                })
                if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                    print(
                        "[debug] authoritative: deduplicated placement "
                        f"source={SourceGenerator} spacing={CandidateSpacing} "
                        f"duplicate_of={Existing[0]} "
                        f"elapsed={monotonic() - PlacementStarted:.3f}s",
                        flush=True,
                    )
                return False
            Feedback = None
            if Policy.Placement.EnableRoutingFeedback:
                Feedback = MeasurePlacementRoutingFeedback(
                    Candidate,
                    CandidateSpacing,
                    Policy,
                    Technology,
                    CheckPlacementGeneration,
                )
                CheckPlacementGeneration({
                    "Phase": "placement-feedback-complete",
                })
            # Publish only after construction, exact legality, resource
            # construction, and feedback all finish inside the same slice.
            UniquePlacements[Fingerprint] = (
                SourceGenerator,
                CandidateSpacing,
                Candidate,
            )
            if Feedback is not None:
                FeedbackByFingerprint[Fingerprint] = Feedback
            PlacementGenerationDecisions.append({
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "Result": "unique-placement",
                "PlacementFingerprint": Fingerprint,
                "RelocationSignals": sorted(EffectiveRelocationSignals),
                "PackedGateArea": PackedGateArea,
                "BaselinePackedGateArea": BaselinePackedGateArea,
                "MaximumPackedGateArea": MaximumPackedGateArea,
                "PackedClusters": [
                    {
                        "ClusterId": Cluster.ClusterId,
                        "Members": list(Cluster.MemberNands),
                        "StackId": Cluster.StackId,
                        "StackLevel": Cluster.StackLevel,
                        "BaseY": Cluster.BaseY,
                    }
                    for Cluster in Candidate.PackedClusters
                ],
                "PlacementGenerationBudgetSeconds": round(
                    PlacementGenerationBudgetSeconds,
                    6,
                ),
                "RoutingReserveSeconds": round(
                    RoutingReserveSeconds,
                    6,
                ),
                "ElapsedSeconds": round(
                    monotonic() - PlacementStarted,
                    6,
                ),
            })
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: generated placement "
                    f"source={SourceGenerator} spacing={CandidateSpacing} "
                    f"elapsed={monotonic() - PlacementStarted:.3f}s",
                    flush=True,
                )
            return True
        except Exception as Error:
            # A candidate that reached a stable fingerprint but failed a later
            # transactional stage was never published.  Remember only its
            # identity so another recipe cannot repeat identical bounded work
            # and starve the next distinct retained placement.
            if Fingerprint is not None:
                RejectedPlacementFingerprints.add(Fingerprint)
            if isinstance(Error, RoutingStageError):
                Failure = Error.Failure
            elif isinstance(Error, ValueError):
                Failure = RoutingFailure(
                    Reason=RoutingFailureReason.PlacementOverlap,
                    Stage="PlacementGeneration",
                    Detail=str(Error),
                    RepairActions=("AdvancePlacementGenerator",),
                    Diagnostics={"ErrorType": type(Error).__name__},
                )
            else:
                Failure = RoutingFailure(
                    Reason=RoutingFailureReason.DetailedSearchExhausted,
                    Stage="PlacementGeneration",
                    Detail=(
                        "unexpected bounded placement-generation failure: "
                        f"{type(Error).__name__}: {Error}"
                    ),
                    RepairActions=("AdvancePlacementGenerator",),
                    Diagnostics={"ErrorType": type(Error).__name__},
                )
            LastStructuredPlacementFailure = Failure
            PlacementGenerationFailures.append({
                "SourceGenerator": SourceGenerator,
                "RoutingSpacing": CandidateSpacing,
                "PackedNandPlacement": bool(CandidatePacking.Enabled),
                "Failure": str(Error),
                "PlacementGenerationBudgetSeconds": round(
                    PlacementGenerationBudgetSeconds,
                    6,
                ),
                "ElapsedSeconds": round(
                    monotonic() - PlacementStarted,
                    6,
                ),
                "Diagnostics": (
                    Failure.ToDictionary()
                ),
            })
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: skipped placement candidate "
                    f"spacing={CandidateSpacing} packing={CandidatePacking.Enabled} "
                    f"reason={Error}",
                    f"elapsed={monotonic() - PlacementStarted:.3f}s",
                    flush=True,
                )
            if Failure.Reason == RoutingFailureReason.RuntimeBudgetExceeded:
                raise RoutingStageError(
                    _PlacementFailureWithHistory(Failure)
                ) from Error
            return False

    def _TakeNextDeferredRequest() -> PlacementGenerationRequest | None:
        nonlocal DeferredRequestIndex
        if PlacementGenerationAttempts >= GenerationPlan.MaximumAttempts:
            return None
        if DeferredRequestIndex < len(GenerationPlan.DeferredRequests):
            Request = GenerationPlan.DeferredRequests[DeferredRequestIndex]
            if Request.SourceGenerator == "row-beam-conflict-relocation":
                DeferredRequestIndex += 1
                if PlacementRelocationSignals:
                    return Request
        if (
            PlacementRelocationSignals
            and TotalRelocationGenerationCount
            < (
                Policy.NegotiatedRouting.MaximumPlacementFeedbackRounds
                if Policy.NegotiatedRouting.Enabled
                else max(1, Policy.NandPacking.PlacementFeedbackIterations + 1)
            )
            and (
                PlacementRelocationSignals != LastRelocationSignalsUsed
                or PlacementRelocationPrioritySignals
                != LastRelocationPrioritySignalsUsed
                or PlacementRequiredRelocationSignals
                != LastRequiredRelocationSignalsUsed
                or RelocationGenerationCount
                < Policy.NegotiatedRouting.MaximumPlacementFeedbackRounds
            )
        ):
            return PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=ConfiguredRoutingSpacing,
                PackingPolicy=replace(
                    Policy.NandPacking,
                    GraphBeamEnabled=False,
                ),
            )
        if DeferredRequestIndex < len(GenerationPlan.DeferredRequests):
            Request = GenerationPlan.DeferredRequests[DeferredRequestIndex]
            if (
                Request.SourceGenerator == "row-beam-direct-only"
                and TotalRelocationGenerationCount >= 2
            ):
                DeferredRequestIndex += 1
                return Request
        if DeferredRequestIndex >= len(GenerationPlan.DeferredRequests):
            return None
        Request = GenerationPlan.DeferredRequests[DeferredRequestIndex]
        DeferredRequestIndex += 1
        return Request

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
    for Request in GenerationPlan.PrimaryRequests:
        if PlacementGenerationAttempts >= GenerationPlan.MaximumAttempts:
            break
        _TryPlacement(Request)

    if ProactiveRelocationRequested:
        for RequestIndex, Request in enumerate(GenerationPlan.DeferredRequests):
            if Request.SourceGenerator != "row-beam-conflict-relocation":
                continue
            _TryPlacement(Request)
            DeferredRequestIndex = RequestIndex + 1
            break

    while not UniquePlacements:
        Request = _TakeNextDeferredRequest()
        if Request is None:
            break
        _TryPlacement(Request)

    if not UniquePlacements:
        BaseFailure = LastStructuredPlacementFailure or RoutingFailure(
            Reason=RoutingFailureReason.PlacementOverlap,
            Stage="Placement",
            Detail="no exact-legal placement candidate was generated",
        )
        FailureDiagnostics = dict(BaseFailure.Diagnostics or {})
        FailureDiagnostics.update({
            "PlacementGenerationFailures": PlacementGenerationFailures,
            "PlacementGenerationDecisions": PlacementGenerationDecisions,
            "PlacementAttempts": PlacementAttemptFailures,
            "Deadline": Deadline.ToDictionary(),
        })
        raise RoutingStageError(
            RoutingFailure(
                Reason=BaseFailure.Reason,
                Stage=BaseFailure.Stage,
                AffectedNets=BaseFailure.AffectedNets,
                Resources=BaseFailure.Resources,
                Locations=BaseFailure.Locations,
                RepairActions=BaseFailure.RepairActions,
                Detail=BaseFailure.Detail,
                Diagnostics=FailureDiagnostics,
            )
        )

    def _BuildCandidateRecords() -> list[PcbPlacementCandidate]:
        CandidateRecords: list[PcbPlacementCandidate] = []
        for CandidateIndex, (
            Fingerprint,
            (SourceGenerator, CandidateSpacing, Candidate),
        ) in enumerate(sorted(UniquePlacements.items())):
            Feedback = None
            if Policy.Placement.EnableRoutingFeedback:
                Feedback = FeedbackByFingerprint.get(Fingerprint)
                if Feedback is None:
                    raise RoutingStageError(
                        _PlacementFailureWithHistory(
                            RoutingFailure(
                                Reason=RoutingFailureReason.Stagnated,
                                Stage="PlacementFeedback",
                                Detail=(
                                    "retained placement was missing its bounded "
                                    "routing-feedback record"
                                ),
                                RepairActions=("AdvancePlacementGenerator",),
                                Diagnostics={
                                    "PlacementFingerprint": Fingerprint,
                                    "SourceGenerator": SourceGenerator,
                                },
                            )
                        )
                    )
            FeedbackScore = (
                Feedback.Score if Feedback is not None else (CandidateIndex,)
            )
            CandidateRecords.append(
                PcbPlacementCandidate(
                    CandidateId=f"Placement-{Fingerprint[:12]}",
                    SourceGenerator=SourceGenerator,
                    RoutingSpacing=CandidateSpacing,
                    PlacementFingerprint=Fingerprint,
                    FeedbackScore=tuple(FeedbackScore),
                    BoundaryOverflow=(
                        Feedback.BoundaryOverflow if Feedback is not None else 0
                    ),
                    PinScarcityCount=(
                        Feedback.PinScarcityCount if Feedback is not None else 0
                    ),
                    GuideOverflowPeak=(
                        Feedback.GuideOverflowPeak if Feedback is not None else 0
                    ),
                    GuideOverflowCells=(
                        Feedback.GuideOverflowCells if Feedback is not None else 0
                    ),
                    PinEscapeConflictCount=(
                        Feedback.PinEscapeConflictCount
                        if Feedback is not None
                        else 0
                    ),
                    EstimatedGlobalExtensionNodes=(
                        Feedback.EstimatedGlobalExtensionNodes
                        if Feedback is not None
                        else 0
                    ),
                    EstimatedGlobalExtensionNets=(
                        Feedback.EstimatedGlobalExtensionNets
                        if Feedback is not None
                        else 0
                    ),
                    PreOwnedNodeCount=(
                        Feedback.PreOwnedNodeCount
                        if Feedback is not None
                        else 0
                    ),
                    Placement=Candidate,
                    Feedback=Feedback,
                )
            )
        CandidateRecords.sort(
            key=lambda Value: PlacementCandidateOrder(
                Value,
                ConfiguredRoutingSpacing,
            )
        )
        return CandidateRecords

    CandidateRecords = _BuildCandidateRecords()
    PlacementGenerationDecisions.append({
        "Result": "deferred-placement-alternatives",
        "Reason": (
            "route exact-legal primary candidates before paying for "
            "structure-aware or spacing recovery"
        ),
        "DemandPressurePresent": PlacementNeedsDemandDiversity(
            CandidateRecords,
            ConfiguredRoutingSpacing,
        ),
        "DeferredCount": len(GenerationPlan.DeferredRequests) - DeferredRequestIndex,
    })

    OrderedPlacements = CandidateRecords[
        : max(1, Policy.NandPacking.RetainedPlacementCandidates)
    ]
    PlacementFeedback = [
        Candidate.ToDictionary() for Candidate in CandidateRecords
    ]
    Placement = OrderedPlacements[0].Placement
    RoutingSpacing = OrderedPlacements[0].RoutingSpacing
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
        for CandidateRecord in OrderedPlacements:
            print(
                "[debug] authoritative: retained placement "
                f"id={CandidateRecord.CandidateId} "
                f"source={CandidateRecord.SourceGenerator} "
                f"score={CandidateRecord.FeedbackScore} "
                f"boundary_overflow={CandidateRecord.BoundaryOverflow} "
                f"pin_scarcity={CandidateRecord.PinScarcityCount} "
                f"packed={bool(CandidateRecord.Placement.PackedClusters)}",
                flush=True,
            )

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
        # RoutePcbDesign owns one candidate, not the complete placement flow.
        # Its completion must remain visibly pending until the shared deadline,
        # authoritative validation, and final result construction all pass.
        EffectiveTotal = max(1, Total)
        CandidateComplete = Completed >= EffectiveTotal or Valid > 0
        EffectiveCompleted = (
            min(Completed, EffectiveTotal - 1)
            if CandidateComplete
            else Completed
        )
        EffectiveValid = 0 if CandidateComplete else Valid
        EffectiveBestRouted = None if CandidateComplete else BestRouted
        EffectiveStage = (
            "routed candidate awaiting validation"
            if CandidateComplete and Failed == 0
            else Stage
        )
        BestFootprint = None
        BestBlocks = None
        BestWidth = None
        BestDepth = None
        if EffectiveBestRouted is not None:
            (
                BestFootprint,
                BestBlocks,
                BestWidth,
                BestDepth,
            ) = MeasurePcbDesign(Placement.Placed, EffectiveBestRouted)
        ProgressCallback(
            PcbProgress(
                Completed=EffectiveCompleted,
                Total=EffectiveTotal,
                Workers=Workers,
                Valid=EffectiveValid,
                BestBlocks=BestBlocks,
                BestWidth=BestWidth,
                BestDepth=BestDepth,
                BestFootprint=BestFootprint,
                Failed=Failed,
                Stage=f"spacing {RoutingSpacing} | {EffectiveStage}",
            )
        )

    def _RouteWithFailedLocalClaimsReleased(
        CandidatePlacement: PcbPlacement,
        AttemptPolicy: PhysicalDesignPolicy,
        Failure: RoutingFailure,
        AdaptiveStartedAt: float,
        AdaptiveExpiresAt: float,
    ) -> tuple[PcbPlacement, RoutedDesign] | None:
        """Release only an unextendable local tree and retain every clean tree.

        A packed local claim is an optimization, not a correctness dependency.
        When its boundary cannot be extended, the affected signal is returned
        to normal global routing while claims owned by unrelated signals remain
        authoritative base ownership.
        """
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print(
                "[debug] authoritative: evaluating local-claim release "
                f"reason={Failure.Reason} stage={Failure.Stage}",
                flush=True,
            )
        if FailureRequestsPlacementAdvance(Failure):
            return None
        ReleasableReasons = {
            RoutingFailureReason.NoBoundaryEscape,
            RoutingFailureReason.PartialTreeExtensionFailed,
            RoutingFailureReason.MultiSourceStagnated,
            RoutingFailureReason.TrackAssignmentConflict,
            RoutingFailureReason.DetailedSearchExhausted,
        }
        Signals = ExtractPlacementRelocationSignals(Failure)
        if not Signals:
            Signals = frozenset(Failure.AffectedNets)
        if Failure.Reason not in ReleasableReasons or not Signals:
            return None
        Original = CandidatePlacement.Placed
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print(
                "[debug] authoritative: candidate local claim signals="
                f"{sorted(Signals)} available="
                f"{sorted({Claim.Signal for Claim in (Original.LocalRouteClaims or ())})}",
                flush=True,
            )
        ExistingClaims = tuple(Original.LocalRouteClaims or ())
        AllSignals = {Claim.Signal for Claim in ExistingClaims}
        if not AllSignals:
            return None
        Signals = SelectReleasableLocalClaimSignals(Signals, ExistingClaims)
        if not Signals:
            return None
        RetainedClaims = tuple(
            Claim for Claim in (Original.LocalRouteClaims or ())
            if Claim.Signal not in Signals
        )
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print(
                "[debug] authoritative: releasing local claims "
                f"signals={sorted(Signals)} original={len(Original.LocalRouteClaims or ())} "
                f"retained={len(RetainedClaims)}",
                flush=True,
            )
        if len(RetainedClaims) == len(Original.LocalRouteClaims or ()):
            return None
        Deadline.RaiseIfExpired(
            "LocalClaimRelease",
            {
                "Phase": "before-reroute",
                "AffectedSignals": sorted(Signals),
            },
        )
        ReleasedDiagnostics = dict(Original.LocalRouteDiagnostics or {})
        ReleasedDiagnostics["ReleasedLocalClaims"] = {
            "Signals": sorted(Signals),
            "Reason": Failure.Reason.value,
            "Stage": Failure.Stage,
        }
        ReleasedPlaced = replace(
            Original,
            LocalRouteClaims=RetainedClaims,
            FrozenNetWires={
                Signal: Nodes
                for Signal, Nodes in (Original.FrozenNetWires or {}).items()
                if Signal not in Signals
            },
            LocalNetBranches={
                Signal: Nodes
                for Signal, Nodes in (Original.LocalNetBranches or {}).items()
                if Signal not in Signals
            },
            LocalNetTargets={
                Signal: Nodes
                for Signal, Nodes in (Original.LocalNetTargets or {}).items()
                if Signal not in Signals
            },
            LocalRouteDiagnostics=ReleasedDiagnostics,
        )
        ReleasedPlacement = replace(CandidatePlacement, Placed=ReleasedPlaced)
        RecoveryStartedAt = monotonic()
        RemainingAdaptiveSeconds = min(
            Deadline.ExpiresAt,
            AdaptiveExpiresAt,
        ) - RecoveryStartedAt
        if RemainingAdaptiveSeconds <= 0:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="LocalClaimRelease",
                    Detail=(
                        "original placement adaptive slice expired before "
                        "same-candidate local-claim recovery"
                    ),
                    RepairActions=("AdvancePlacementCandidate",),
                    Diagnostics={
                        "Action": "advance-placement-adaptive-slice-expired",
                        "AdaptiveStartedAt": AdaptiveStartedAt,
                        "AdaptiveExpiresAt": AdaptiveExpiresAt,
                        "Deadline": Deadline.ToDictionary(),
                    },
                )
            )
        RecoveryPolicy = replace(
            AttemptPolicy,
            RuntimeBudgetSeconds=min(
                AttemptPolicy.RuntimeBudgetSeconds,
                Deadline.RemainingSeconds(),
                RemainingAdaptiveSeconds,
            ),
            AdaptiveRouting=replace(
                AttemptPolicy.AdaptiveRouting,
                MaximumRuntimeSeconds=min(
                    AttemptPolicy.AdaptiveRouting.MaximumRuntimeSeconds,
                    RemainingAdaptiveSeconds,
                ),
            ),
        )
        ReleasedRouted = RoutePcbDesign(
            ReleasedPlacement,
            ProgressCallback=ReportRoutingProgress,
            Policy=RecoveryPolicy,
            Deadline=Deadline,
        )
        if monotonic() >= AdaptiveExpiresAt:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="LocalClaimRelease",
                    Detail=(
                        "same-candidate local-claim recovery exceeded the "
                        "original placement adaptive slice"
                    ),
                    RepairActions=("AdvancePlacementCandidate",),
                    Diagnostics={
                        "Action": "advance-placement-adaptive-slice-expired",
                        "AdaptiveStartedAt": AdaptiveStartedAt,
                        "AdaptiveExpiresAt": AdaptiveExpiresAt,
                        "RecoveryStartedAt": RecoveryStartedAt,
                        "Deadline": Deadline.ToDictionary(),
                    },
                )
            )
        Deadline.RaiseIfExpired(
            "Routing",
            {
                "Recovery": "released-affected-local-claims",
                "AffectedSignals": sorted(Signals),
            },
        )
        Deadline.RaiseIfExpired(
            "RoutedValidation",
            {
                "Phase": "before",
                "Recovery": "released-affected-local-claims",
                "AffectedSignals": sorted(Signals),
            },
        )
        if RoutedValidationCallback is not None:
            RoutedValidationCallback(ReleasedRouted)
        Deadline.RaiseIfExpired(
            "RoutedValidation",
            {
                "Phase": "after",
                "Recovery": "released-affected-local-claims",
                "AffectedSignals": sorted(Signals),
            },
        )
        return ReleasedPlacement, ReleasedRouted

    Routed = None
    SelectedCandidate: PcbPlacementCandidate | None = None
    RoutingPercentageSelectionEnabled = (
        Policy.MaterialObjective.OptimizeRoutingPercentage
        and len(Module.Gates)
        >= Policy.MaterialObjective.MinimumRoutingPercentageSelectionNandCount
    )
    RoutedCandidates: list[tuple[
        tuple[float, int, int, int, int, int, int, str],
        PcbPlacementCandidate,
        PcbPlacement,
        RoutedDesign,
        dict[str, object],
    ]] = []

    def RecordRoutedCandidate(
        Candidate: PcbPlacementCandidate,
        CandidatePlacement: PcbPlacement,
        CandidateRouted: RoutedDesign,
    ) -> None:
        """Score legal routed placements by final volume, then route share."""
        from SchemEncoder.Writer262 import BuildLitematicBlockMap

        Composition = BuildLitematicBlockMap(CandidateRouted).Composition
        Score = (
            Composition.FullFootprint,
            Composition.RoutingFunctionalShare,
            Composition.RoutingOwnedFunctionalBlocks,
            Composition.Footprint,
            Composition.NonAirBlocks,
            Composition.Width,
            Composition.Depth,
            Candidate.CandidateId,
        )
        Diagnostics: dict[str, object] = {
            "CandidateId": Candidate.CandidateId,
            "RoutingFunctionalShare": Composition.RoutingFunctionalShare,
            "RoutingOwnedFunctionalBlocks": (
                Composition.RoutingOwnedFunctionalBlocks
            ),
            "NonAirBlocks": Composition.NonAirBlocks,
            "Footprint": Composition.Footprint,
            "XYFootprint": Composition.XYFootprint,
            "FullFootprint": Composition.FullFootprint,
            "Width": Composition.Width,
            "Height": Composition.Height,
            "Depth": Composition.Depth,
            "Score": list(Score[:-1]),
        }
        RoutedCandidates.append((
            Score,
            Candidate,
            CandidatePlacement,
            CandidateRouted,
            Diagnostics,
        ))

    def _PlacementCandidatesForRouting():
        nonlocal CandidateRecords, OrderedPlacements
        nonlocal LastRoutingError, LastStructuredRoutingError
        nonlocal NeedsFeedbackPlacementGeneration
        AttemptedFingerprints: set[str] = set()
        while True:
            if NeedsFeedbackPlacementGeneration:
                # A typed fixed-access cut is geometry feedback. Generate its
                # deterministic packed repair before routing an unrelated
                # retained placement, which would consume the shared deadline
                # and dilute the cut.
                NeedsFeedbackPlacementGeneration = False
                Request = _TakeNextDeferredRequest()
                if Request is not None:
                    try:
                        _TryPlacement(Request)
                    except RoutingStageError as Error:
                        LastRoutingError = Error
                        LastStructuredRoutingError = Error
                        return
                    CandidateRecords = _BuildCandidateRecords()
                    OrderedPlacements = CandidateRecords[
                        : max(
                            1,
                            Policy.NandPacking.RetainedPlacementCandidates,
                        )
                    ]
                    PlacementFeedback[:] = [
                        Candidate.ToDictionary()
                        for Candidate in CandidateRecords
                    ]
            Pending = [
                Candidate
                for Candidate in OrderedPlacements
                if Candidate.PlacementFingerprint not in AttemptedFingerprints
            ]
            if Pending:
                FeedbackPending = [
                    Candidate
                    for Candidate in Pending
                    if Candidate.SourceGenerator
                    == "row-beam-conflict-relocation"
                ]
                if FeedbackPending:
                    Pending = FeedbackPending
                NextCandidate = Pending[0]
                AttemptedFingerprints.add(NextCandidate.PlacementFingerprint)
                yield NextCandidate
                continue
            if Deadline.IsExpired():
                return
            Request = _TakeNextDeferredRequest()
            if Request is None:
                return
            try:
                _TryPlacement(Request)
            except RoutingStageError as Error:
                LastRoutingError = Error
                LastStructuredRoutingError = Error
                return
            CandidateRecords = _BuildCandidateRecords()
            OrderedPlacements = CandidateRecords[
                : max(1, Policy.NandPacking.RetainedPlacementCandidates)
            ]
            PlacementFeedback[:] = [
                Candidate.ToDictionary() for Candidate in CandidateRecords
            ]

    for CandidateRecord in _PlacementCandidatesForRouting():
        try:
            Deadline.RaiseIfExpired(
                "PlacementCandidateSelection",
                {"PlacementAttempts": PlacementAttemptFailures},
            )
        except RoutingStageError as Error:
            LastRoutingError = Error
            LastStructuredRoutingError = Error
            break
        CandidatePlacement = CandidateRecord.Placement
        Placement = CandidatePlacement
        RoutingSpacing = CandidateRecord.RoutingSpacing
        AttemptedCandidateIds = {
            str(Entry.get("CandidateId"))
            for Entry in PlacementAttemptFailures
            if Entry.get("CandidateId") is not None
        }
        HasRemainingPlacementAlternative = (
            any(
                Candidate.CandidateId != CandidateRecord.CandidateId
                and Candidate.CandidateId not in AttemptedCandidateIds
                for Candidate in OrderedPlacements
            )
            or DeferredRequestIndex < len(GenerationPlan.DeferredRequests)
        )
        RemainingRuntimeSeconds = max(0.001, Deadline.RemainingSeconds())
        RemainingRetainedCandidates = sum(
            Candidate.CandidateId not in AttemptedCandidateIds
            for Candidate in OrderedPlacements
        )
        PlannedRoutingSlots = max(
            1,
            RemainingRetainedCandidates,
        )
        AdaptiveAttemptRuntimeSeconds = min(
            Policy.AdaptiveRouting.MaximumRuntimeSeconds,
            max(
                0.001,
                RemainingRuntimeSeconds / PlannedRoutingSlots,
            ),
        )
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print(
                "[debug] authoritative: trying placement candidate "
                f"id={CandidateRecord.CandidateId} "
                f"claims={len(CandidatePlacement.Placed.LocalRouteClaims or ())} "
                f"packed={bool(CandidatePlacement.PackedClusters)} "
                f"spacing={RoutingSpacing}",
                flush=True,
            )
            print(
                "[debug] authoritative: policy budgets "
                f"overall={Policy.RuntimeBudgetSeconds:.3f}s "
                f"adaptive_max={AdaptiveAttemptRuntimeSeconds:.3f}s "
                f"has_alternative={HasRemainingPlacementAlternative}",
                flush=True,
            )
        AttemptPolicy = replace(
            Policy,
            RuntimeBudgetSeconds=RemainingRuntimeSeconds,
            AdaptiveRouting=replace(
                Policy.AdaptiveRouting,
                MaximumRuntimeSeconds=AdaptiveAttemptRuntimeSeconds,
            ),
        )
        AttemptStarted = monotonic()
        AdaptiveAttemptExpiresAt = min(
            Deadline.ExpiresAt,
            AttemptStarted + AdaptiveAttemptRuntimeSeconds,
        )

        def CheckCandidateValidation(
            Diagnostics: dict[str, object],
        ) -> None:
            Deadline.RaiseIfExpired(
                "PlacementCandidateValidation",
                {
                    "CandidateId": CandidateRecord.CandidateId,
                    "AdaptiveAttemptStartedAt": AttemptStarted,
                    "AdaptiveAttemptExpiresAt": AdaptiveAttemptExpiresAt,
                    **Diagnostics,
                },
            )

        try:
            # Skip impossible candidates early and keep the retry budget for
            # deterministic alternatives that can actually be legalized.
            ValidatePlacedCellElectricalIsolation(
                CandidatePlacement.Placed,
                WorkCheck=CheckCandidateValidation,
            )
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: remaining_runtime_for_attempt="
                    f"{AttemptPolicy.RuntimeBudgetSeconds:.3f}s "
                    f"elapsed_from_start={monotonic()-Started:.3f}s",
                    flush=True,
                )
            Routed = RoutePcbDesign(
                Placement,
                ProgressCallback=ReportRoutingProgress,
                Policy=AttemptPolicy,
                Deadline=Deadline,
            )
            Deadline.RaiseIfExpired(
                "Routing",
                {"PlacementCandidate": CandidateRecord.CandidateId},
            )
            Deadline.RaiseIfExpired(
                "RoutedValidation",
                {
                    "Phase": "before",
                    "PlacementCandidate": CandidateRecord.CandidateId,
                },
            )
            if RoutedValidationCallback is not None:
                RoutedValidationCallback(Routed)
            Deadline.RaiseIfExpired(
                "RoutedValidation",
                {
                    "Phase": "after",
                    "PlacementCandidate": CandidateRecord.CandidateId,
                },
            )
            if RoutingPercentageSelectionEnabled:
                RecordRoutedCandidate(CandidateRecord, Placement, Routed)
            PlacementAttemptFailures.append({
                **CandidateRecord.ToDictionary(),
                "Result": "routed",
                "AdaptiveRuntimeBudgetSeconds": round(
                    AdaptiveAttemptRuntimeSeconds,
                    6,
                ),
                "ElapsedSeconds": round(monotonic() - AttemptStarted, 6),
            })
            if not RoutingPercentageSelectionEnabled:
                SelectedCandidate = CandidateRecord
                break
            if (
                Deadline.RemainingSeconds()
                < Policy.MaterialObjective
                .MinimumRemainingRoutingPercentageSearchSeconds
            ):
                # A legal route is more valuable than spending the final
                # shared-deadline slice comparing alternatives we cannot
                # complete and validate.
                break
            # Keep routing bounded retained alternatives under the same
            # absolute deadline, then publish the smallest real route share.
            continue
        except (RoutingStageError, ValueError) as Error:
            LastRoutingError = Error
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: placement route rejected "
                    f"candidate={CandidateRecord.CandidateId} "
                    f"error_type={type(Error).__name__} error={Error}",
                    flush=True,
                )
            # A returned route is not eligible until every routed validation
            # and deadline check has completed successfully.
            Routed = None
            if (
                isinstance(Error, RoutingStageError)
                and Error.Failure.Reason
                == RoutingFailureReason.RuntimeBudgetExceeded
            ):
                LastStructuredRoutingError = Error
                PlacementAttemptFailures.append(
                    {
                        **CandidateRecord.ToDictionary(),
                        "RoutingSpacing": RoutingSpacing,
                        "PackedNandPlacement": bool(
                            CandidatePlacement.PackedClusters
                        ),
                        "Failure": str(Error),
                        "AdaptiveRuntimeBudgetSeconds": round(
                            AdaptiveAttemptRuntimeSeconds,
                            6,
                        ),
                        "Diagnostics": Error.Failure.ToDictionary(),
                        "ElapsedSeconds": round(
                            monotonic() - AttemptStarted,
                            6,
                        ),
                    }
                )
                # An adaptive candidate slice is not the absolute routing
                # deadline.  Preserve the latter and advance to the next
                # deterministic placement while publication time remains.
                if HasRemainingPlacementAlternative and not Deadline.IsExpired():
                    continue
                break
            if isinstance(Error, RoutingStageError):
                ConflictSignals = ExtractPlacementRelocationSignals(
                    Error.Failure
                )
                if FailureRequestsPlacementAdvance(Error.Failure):
                    NeedsFeedbackPlacementGeneration = True
                if ConflictSignals:
                    PlacementRelocationPrioritySignals = (
                        ConflictSignals
                        or frozenset(Error.Failure.AffectedNets)
                    )
                    if (
                        Error.Failure.Stage == "Candidate"
                        or FailureRequiresPackedAccessRepair(Error.Failure)
                    ):
                        PlacementRequiredRelocationSignals = frozenset((
                            *PlacementRequiredRelocationSignals,
                            *ConflictSignals,
                        ))
                    PlacementRelocationSignals = frozenset((
                        *PlacementRelocationSignals,
                        *ConflictSignals,
                    ))
                    PlacementGenerationDecisions.append({
                        "Result": "routing-conflict-feedback",
                        "CandidateId": CandidateRecord.CandidateId,
                        "RelocationSignals": sorted(
                            PlacementRelocationSignals
                        ),
                    })
                if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                    print(
                        "[debug] authoritative: routing failure "
                        f"reason={Error.Failure.Reason} stage={Error.Failure.Stage} "
                        f"affected={tuple(Error.Failure.AffectedNets)}",
                        flush=True,
                    )
                LastStructuredRoutingError = Error
                if not FailureRequestsPlacementAdvance(Error.Failure):
                    try:
                        Released = _RouteWithFailedLocalClaimsReleased(
                            CandidatePlacement,
                            AttemptPolicy,
                            Error.Failure,
                            AdaptiveStartedAt=AttemptStarted,
                            AdaptiveExpiresAt=AdaptiveAttemptExpiresAt,
                        )
                    except (RoutingStageError, ValueError) as ReleaseError:
                        LastRoutingError = ReleaseError
                        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                            print(
                                "[debug] authoritative: local-claim recovery rejected "
                                f"signals={list(Error.Failure.AffectedNets)} "
                                f"error_type={type(ReleaseError).__name__} "
                                f"error={ReleaseError}",
                                flush=True,
                            )
                        if isinstance(ReleaseError, RoutingStageError):
                            LastStructuredRoutingError = ReleaseError
                            ReleaseConflictSignals = (
                                ExtractPlacementRelocationSignals(
                                    ReleaseError.Failure
                                )
                            )
                            if ReleaseConflictSignals:
                                PlacementRelocationPrioritySignals = (
                                    ReleaseConflictSignals
                                    if PlacementRelocationSignals
                                    else frozenset(
                                        ReleaseError.Failure.AffectedNets
                                    )
                                )
                                PlacementRelocationSignals = frozenset((
                                    *PlacementRelocationSignals,
                                    *ReleaseConflictSignals,
                                ))
                                PlacementGenerationDecisions.append({
                                    "Result": (
                                        "local-claim-recovery-conflict-feedback"
                                    ),
                                    "CandidateId": CandidateRecord.CandidateId,
                                    "RelocationSignals": sorted(
                                        PlacementRelocationSignals
                                    ),
                                })
                        if (
                            isinstance(ReleaseError, RoutingStageError)
                            and ReleaseError.Failure.Reason
                            == RoutingFailureReason.RuntimeBudgetExceeded
                        ):
                            PlacementAttemptFailures.append(
                                {
                                    **CandidateRecord.ToDictionary(),
                                    "RoutingSpacing": RoutingSpacing,
                                    "PackedNandPlacement": bool(
                                        CandidatePlacement.PackedClusters
                                    ),
                                    "Failure": str(ReleaseError),
                                    "AdaptiveRuntimeBudgetSeconds": round(
                                        AdaptiveAttemptRuntimeSeconds,
                                        6,
                                    ),
                                    "Diagnostics": (
                                        ReleaseError.Failure.ToDictionary()
                                    ),
                                    "ElapsedSeconds": round(
                                        monotonic() - AttemptStarted,
                                        6,
                                    ),
                                }
                            )
                            break
                    else:
                        if Released is not None:
                            Placement, Routed = Released
                            if RoutingPercentageSelectionEnabled:
                                RecordRoutedCandidate(
                                    CandidateRecord,
                                    Placement,
                                    Routed,
                                )
                            PlacementAttemptFailures.append(
                                {
                                    **CandidateRecord.ToDictionary(),
                                    "RoutingSpacing": RoutingSpacing,
                                    "PackedNandPlacement": bool(
                                        CandidatePlacement.PackedClusters
                                    ),
                                    "Failure": str(Error),
                                    "AdaptiveRuntimeBudgetSeconds": round(
                                        AdaptiveAttemptRuntimeSeconds,
                                        6,
                                    ),
                                    "AdaptiveAttemptStartedAt": AttemptStarted,
                                    "AdaptiveAttemptExpiresAt": (
                                        AdaptiveAttemptExpiresAt
                                    ),
                                    "Recovery": "released-affected-local-claims",
                                    "ReleasedSignals": list(
                                        Error.Failure.AffectedNets
                                    ),
                                }
                            )
                            if not RoutingPercentageSelectionEnabled:
                                SelectedCandidate = CandidateRecord
                                break
                            if (
                                Deadline.RemainingSeconds()
                                < Policy.MaterialObjective
                                .MinimumRemainingRoutingPercentageSearchSeconds
                            ):
                                break
                            continue
            PlacementAttemptFailures.append(
                {
                    **CandidateRecord.ToDictionary(),
                    "RoutingSpacing": RoutingSpacing,
                    "PackedNandPlacement": bool(CandidatePlacement.PackedClusters),
                    "Failure": str(Error),
                    "AdaptiveRuntimeBudgetSeconds": round(
                        AdaptiveAttemptRuntimeSeconds,
                        6,
                    ),
                    "Diagnostics": (
                        Error.Failure.ToDictionary()
                        if isinstance(Error, RoutingStageError)
                        else {}
                    ),
                    "ElapsedSeconds": round(monotonic() - AttemptStarted, 6),
                }
            )
    if RoutedCandidates:
        (
            _Score,
            SelectedCandidate,
            Placement,
            Routed,
            SelectedCompositionDiagnostics,
        ) = min(RoutedCandidates, key=lambda Value: Value[0])
        RoutingSpacing = SelectedCandidate.RoutingSpacing
    if Routed is None:
        if LastStructuredRoutingError is not None:
            BaseFailure = LastStructuredRoutingError.Failure
        else:
            BaseFailure = RoutingFailure(
                Reason=RoutingFailureReason.DetailedSearchExhausted,
                Stage="PlacementRouting",
                Detail=str(LastRoutingError or "all placement candidates failed"),
            )
        FailureDiagnostics = dict(BaseFailure.Diagnostics or {})
        FailureDiagnostics.update({
            "PlacementCandidates": PlacementFeedback,
            "PlacementGenerationFailures": PlacementGenerationFailures,
            "PlacementGenerationDecisions": PlacementGenerationDecisions,
            "PlacementAttempts": PlacementAttemptFailures,
            "Deadline": Deadline.ToDictionary(),
        })
        raise RoutingStageError(
            RoutingFailure(
                Reason=BaseFailure.Reason,
                Stage=BaseFailure.Stage,
                AffectedNets=BaseFailure.AffectedNets,
                Resources=BaseFailure.Resources,
                Locations=BaseFailure.Locations,
                RepairActions=BaseFailure.RepairActions,
                Detail=BaseFailure.Detail,
                Diagnostics=FailureDiagnostics,
            )
        ) from LastRoutingError
    ValidateNandOnlyDesign(Placement.Placed, Netlist)
    Routed.RoutingControlEffectiveness["PlacementFeedbackCandidates"] = (
        PlacementFeedback
    )
    Routed.RoutingControlEffectiveness["SelectedPlacementCandidate"] = (
        SelectedCandidate.ToDictionary() if SelectedCandidate is not None else None
    )
    Routed.RoutingControlEffectiveness["SelectedRoutingSpacing"] = RoutingSpacing
    Routed.RoutingControlEffectiveness["RoutingPercentageSelection"] = {
        "Enabled": RoutingPercentageSelectionEnabled,
        "Configured": Policy.MaterialObjective.OptimizeRoutingPercentage,
        "MinimumNandCount": (
            Policy.MaterialObjective.MinimumRoutingPercentageSelectionNandCount
        ),
        "NandGateCount": len(Module.Gates),
        "CandidateCount": len(RoutedCandidates),
        "Selected": SelectedCompositionDiagnostics if RoutedCandidates else None,
        "Candidates": [
            Diagnostics
            for _Score, _Candidate, _Placement, _Routed, Diagnostics
            in sorted(RoutedCandidates, key=lambda Value: Value[0])
        ],
    }
    Routed.RoutingControlEffectiveness["PlacementAttempts"] = (
        PlacementAttemptFailures
    )
    Routed.RoutingControlEffectiveness["PlacementGenerationFailures"] = (
        PlacementGenerationFailures
    )
    Routed.RoutingControlEffectiveness["PlacementGenerationDecisions"] = (
        PlacementGenerationDecisions
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
            "StackId": Cluster.StackId,
            "StackLevel": Cluster.StackLevel,
            "BaseY": Cluster.BaseY,
            "BoundaryDemand": dict(sorted((Cluster.BoundaryDemand or {}).items())),
            "EstimatedCorridorLanes": Cluster.EstimatedCorridorLanes,
            "LocalClaimCoverage": Cluster.LocalClaimCoverage,
            "BoundaryDemandRecords": [
                {
                    "Signal": Record.Signal,
                    "UnresolvedTargets": Record.UnresolvedTargets,
                    "RequiredPortalSlots": Record.RequiredPortalSlots,
                    "RequiredCorridorLanes": Record.RequiredCorridorLanes,
                    "PreferredBoundarySide": Record.PreferredBoundarySide,
                }
                for Record in Cluster.BoundaryDemandRecords
            ],
            "BoundaryCapacityRecords": [
                {
                    "BoundarySide": Record.BoundarySide,
                    "LegalPortalSlots": Record.LegalPortalSlots,
                    "LegalCorridorLanes": Record.LegalCorridorLanes,
                    "Overflow": Record.Overflow,
                }
                for Record in Cluster.BoundaryCapacityRecords
            ],
            "BoundaryOverflow": Cluster.BoundaryOverflow,
            "PinScarcityCount": Cluster.PinScarcityCount,
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
    PlanningContracts["PortalReservations"] = (
        Routed.RoutingControlEffectiveness.get("PortalReservations", [])
    )
    if Deadline.IsExpired() and RoutedCandidates:
        # A later optional comparison exhausted the shared deadline after at
        # least one candidate had already completed routing and validation.
        # Publish the best validated candidate instead of discarding it.
        Routed.RoutingControlEffectiveness[
            "RoutingPercentageSelection"]["DeadlineLimited"] = True
    else:
        Deadline.RaiseIfExpired("RoutingFinalization")
    Routed.RoutingControlEffectiveness["Deadline"] = Deadline.ToDictionary()
    Result = PcbResult(
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
    if ProgressCallback is not None:
        ProgressCallback(
            PcbProgress(
                Completed=1,
                Total=1,
                Workers=0,
                Valid=1,
                BestBlocks=EstimatedBlocks,
                BestWidth=Width,
                BestDepth=Depth,
                BestFootprint=Footprint,
                Failed=0,
                Stage="routing complete",
            )
        )
    return Result

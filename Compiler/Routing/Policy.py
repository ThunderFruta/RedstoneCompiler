"""Typed, serializable physical-design search policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class RoutingStrategy(str, Enum):
    """User-visible physical-routing strategy."""

    Compatibility = "compatibility"
    Hybrid = "hybrid"
    NewRouterFirst = "new-router-first"

    @classmethod
    def Parse(cls, Value: str | "RoutingStrategy") -> "RoutingStrategy":
        if isinstance(Value, cls):
            return Value
        if Value == "authoritative-only":
            # Keep legacy alias compatibility but route it to the active
            # organized policy to avoid accidentally invoking a retired path.
            return cls.NewRouterFirst
        return cls(Value)


@dataclass(frozen=True)
class ClusteringPolicy:
    CutWeight: int = 8
    BalanceWeight: int = 4
    MinimumCohesiveCells: int = 9
    CohesiveCellScale: float = 3.0


@dataclass(frozen=True)
class PlacementPolicy:
    CompactPassLimit: int = 12
    RoutingSpacing: int = 6
    LocalFanoutDistance: int = 8
    LocalFanoutWeight: int = 12
    HpwlWeight: int = 2
    PinEscapeLength: int = 3
    MaximumRoutingLayers: int = 0
    RoutingFeedbackIterations: int = 0
    RoutingSpacingAlternatives: int = 0
    PreferWideTerminalBanks: bool = False
    TerminalBankOffsetX: int = 0
    EnableRoutingFeedback: bool = False


@dataclass(frozen=True)
class NandPackingPolicy:
    """Deterministic search bounds for dense groups of ordinary NAND cells."""

    Enabled: bool = False
    MaximumClusterCells: int = 16
    BeamWidth: int = 64
    RetainedPlacementCandidates: int = 8
    DirectConnectMaximumLength: int = 2
    MaximumLocalRouteLength: int = 40
    LocalRouteEnvelope: int = 2
    PlacementFeedbackIterations: int = 3
    GraphBeamEnabled: bool = True
    EnableStructuralReuse: bool = True
    MaximumStructuralReuseMappings: int = 4096
    MaximumFrozenLocalNetNodes: int = 32
    MaximumFrozenLocalTargets: int = 16
    EnableVerticalClusterStacking: bool = True
    MaximumClustersPerStack: int = 4
    ClusterDeckPitch: int = 6

    def __post_init__(self) -> None:
        if self.MaximumStructuralReuseMappings < 1:
            raise ValueError(
                "MaximumStructuralReuseMappings must be positive"
            )
        if self.MaximumClustersPerStack < 1:
            raise ValueError("MaximumClustersPerStack must be positive")
        if self.ClusterDeckPitch < 3:
            raise ValueError("ClusterDeckPitch must preserve support and headroom")


@dataclass(frozen=True)
class MaterialObjectivePolicy:
    """Exact emitted-material gates for the dense local-first path."""

    Enabled: bool = False
    MinimumComponentFunctionalShare: float = 0.60
    MaximumRoutingFunctionalShare: float = 0.40
    # OpenROAD/VPR-style acceptance naming used by docs and diagnostics.
    MaximumRoutingDominance: float = 0.40
    MaximumRouteShare: float = 0.40
    MaximumRawDustFunctionalShare: float = 0.45
    MaximumFootprint: int = 600
    MaximumNonAirBlocks: int = 500

    def __post_init__(self) -> None:
        if not (0.0 <= self.MinimumComponentFunctionalShare <= 1.0):
            raise ValueError("MinimumComponentFunctionalShare must be in [0.0, 1.0]")
        for Name, Value in (
            ("MaximumRoutingFunctionalShare", self.MaximumRoutingFunctionalShare),
            ("MaximumRoutingDominance", self.MaximumRoutingDominance),
            ("MaximumRouteShare", self.MaximumRouteShare),
            ("MaximumRawDustFunctionalShare", self.MaximumRawDustFunctionalShare),
        ):
            if not (0.0 <= Value <= 1.0):
                raise ValueError(
                    f"{Name} must be in [0.0, 1.0]"
                )
        if self.MaximumFootprint < 1:
            raise ValueError("MaximumFootprint must be positive")
        if self.MaximumNonAirBlocks < 1:
            raise ValueError("MaximumNonAirBlocks must be positive")


@dataclass(frozen=True)
class RoutingOrganizationPolicy:
    """Deterministic geometric conventions for organized NAND routing."""

    Enabled: bool = False
    ComponentLayer: int = -1
    PreferredXLayer: int = 0
    PreferredZLayer: int = 1
    BridgeLayers: tuple[int, ...] = (2,)
    ClusterKeepInMargin: int = 1
    BoundaryCorridorWidth: int = 2
    BoundaryCorridorPitch: int = 3
    MaximumLocalBranchDistance: int = 2
    MaximumClusterEntrancesPerSignal: int = 2
    MaximumClusterEntrances: int = 16
    AllowSameSignalMerge: bool = True
    AllowForeignIslandTraversal: bool = False
    MaximumBoundedEscapeDistance: int = 4
    SteinerTopologyMode: str = "native"


@dataclass(frozen=True)
class GlobalRoutingPolicy:
    CandidateLaneCount: int = 25
    CorridorCapacity: int = 1
    OverflowPenalty: int = 6
    ExistingGuideHintWeight: int = 1
    IntraClusterEnvelope: int = 2
    SharedBoundaryEnvelope: int = 6
    MaximumRipupPasses: int = 4
    StagnationPassLimit: int = 2
    EnableCapacityAwareGuides: bool = False


@dataclass(frozen=True)
class NegotiatedRoutingPolicy:
    """Bounded PathFinder-style route-tree negotiation controls."""

    Enabled: bool = False
    TilePitchInTracks: int = 4
    MaximumIterations: int = 32
    StagnationPassLimit: int = 3
    PresentConflictPenalty: int = 96
    HistoryIncrement: int = 32
    MaximumPlacementFeedbackRounds: int = 3
    MaximumPackedAreaGrowth: float = 2.0

    def __post_init__(self) -> None:
        for Name in (
            "TilePitchInTracks",
            "MaximumIterations",
            "StagnationPassLimit",
            "PresentConflictPenalty",
            "HistoryIncrement",
            "MaximumPlacementFeedbackRounds",
        ):
            if getattr(self, Name) < 1:
                raise ValueError(f"{Name} must be positive")
        if self.MaximumPackedAreaGrowth < 1.0:
            raise ValueError("MaximumPackedAreaGrowth cannot shrink the baseline")


@dataclass(frozen=True)
class TrackAssignmentPolicy:
    ReassignmentLimit: int = 8
    ReserveRepeaterSites: bool = True
    MaximumPortalsPerTerminal: int = 8
    MaximumRouteCandidatesPerNet: int = 160
    MaximumAssignmentExpansions: int = 50_000


@dataclass(frozen=True)
class AdaptiveRoutingPolicy:
    """Progressive work bounds derived from design demand."""

    Enabled: bool = False
    InitialPortalsPerTerminal: int = 4
    PortalGrowthFactor: int = 2
    InitialLaneCount: int = 3
    LaneGrowthFactor: int = 2
    MaximumPortalReservationAlternatives: int = 2
    MaximumLaneDiversityEscalations: int = 2
    InitialCandidateRequestsPerSignal: int = 4
    MaximumCandidateDiversityEscalations: int = 3
    InitialCandidatesPerNet: int = 72
    CandidateGrowthFactor: int = 2
    MinimumCandidatesPerNet: int = 16
    CandidateClaimWorkQuantum: int = 64
    MaximumCandidateGenerationExpansions: int = 12_000_000
    InitialAssignmentExpansions: int = 128
    AssignmentGrowthFactor: int = 2
    BaseAssignmentExpansions: int = 10_000
    AssignmentExpansionsPerNet: int = 2_000
    AssignmentExpansionsPerTerminal: int = 500
    MaximumAssignmentExpansions: int = 50_000
    MaximumRuntimeSeconds: float = 120.0

    def __post_init__(self) -> None:
        PositiveIntegers = {
            Name: Value
            for Name, Value in asdict(self).items()
            if Name != "Enabled" and Name != "MaximumRuntimeSeconds"
        }
        Invalid = sorted(
            Name for Name, Value in PositiveIntegers.items() if Value < 1
        )
        if Invalid:
            raise ValueError(
                "adaptive routing controls must be positive: "
                + ", ".join(Invalid)
            )
        for Name in (
            "PortalGrowthFactor",
            "LaneGrowthFactor",
            "CandidateGrowthFactor",
            "AssignmentGrowthFactor",
        ):
            if getattr(self, Name) < 2:
                raise ValueError(f"{Name} must grow by at least 2")
        if self.MaximumRuntimeSeconds <= 0:
            raise ValueError("MaximumRuntimeSeconds must be positive")
        if self.InitialAssignmentExpansions > self.MaximumAssignmentExpansions:
            raise ValueError(
                "InitialAssignmentExpansions cannot exceed its maximum"
            )
        if self.MinimumCandidatesPerNet > self.InitialCandidatesPerNet:
            raise ValueError(
                "MinimumCandidatesPerNet cannot exceed InitialCandidatesPerNet"
            )


@dataclass(frozen=True)
class RoutingAcceptanceProfile:
    """Benchmark-only absolute gates; never a production circuit limit."""

    Name: str
    MaximumRuntimeSeconds: float
    MaximumCorridorOverflowPeak: int = 1
    MaximumFootprint: int | None = None
    MaximumNonAirBlocks: int | None = None
    MinimumComponentFunctionalShare: float | None = None
    MaximumRoutingFunctionalShare: float | None = None
    MaximumRawDustFunctionalShare: float | None = None


@dataclass(frozen=True)
class DetailedRoutingPolicy:
    GuideExpansion: int = 8
    LengthPenalty: int = 3
    MinimumGuidePenalty: int = 1
    StrictGuideMultiplier: int = 2
    StrictBendPenalty: int = 10
    RepairBendPenalty: int = 6
    StrictViaPenalty: int = 7
    RepairViaPenalty: int = 4
    LayerPenalty: int = 0
    CandidateBendWeight: int = 0
    CandidateViaWeight: int = 0
    RepeaterPenalty: int = 2
    StagnationPassLimit: int = 12
    StrictBaseExpansions: int = 40_000
    StrictExpansionsPerNet: int = 3_000
    StrictMaximumExpansions: int = 160_000
    RepairBaseExpansions: int = 100_000
    RepairExpansionsPerNet: int = 8_000
    RepairMaximumExpansions: int = 400_000
    MinimumCandidateExpansionLimit: int = 4_096


@dataclass(frozen=True)
class RepairPolicy:
    MaximumConflictNeighborhoodDepth: int = 6
    HistoryIncrement: int = 16


@dataclass(frozen=True)
class QualityGatePolicy:
    Enabled: bool = False
    MaximumCorridorOverflowPeak: int = 1
    MaximumNetLengthShare: float = 0.20
    MaximumAverageBendsPerNet: float = 5.5
    MaximumAverageViasPerNet: float = 7.34


@dataclass(frozen=True)
class RoutingAttemptPolicy:
    """One named detailed-routing escalation policy."""

    AttemptId: str
    SearchMargin: int
    GuidePenalty: int
    MaximumDetourRatio: float
    MaximumDetourAllowance: int
    MaximumIterations: int
    OrderMode: str = "Natural"

    def __post_init__(self) -> None:
        if self.SearchMargin < 0:
            raise ValueError("SearchMargin cannot be negative")
        if self.GuidePenalty < 1:
            raise ValueError("GuidePenalty must keep the global plan authoritative")
        if self.MaximumIterations < 1:
            raise ValueError("MaximumIterations must be positive")
        if self.OrderMode not in {"Natural", "Reverse"}:
            raise ValueError(f"Unknown routing order mode: {self.OrderMode}")


@dataclass(frozen=True)
class PhysicalDesignPolicy:
    PolicyVersion: str = "physical-design-v1"
    Clustering: ClusteringPolicy = field(default_factory=ClusteringPolicy)
    Placement: PlacementPolicy = field(default_factory=PlacementPolicy)
    NandPacking: NandPackingPolicy = field(default_factory=NandPackingPolicy)
    MaterialObjective: MaterialObjectivePolicy = field(
        default_factory=MaterialObjectivePolicy
    )
    Organization: RoutingOrganizationPolicy = field(
        default_factory=RoutingOrganizationPolicy
    )
    GlobalRouting: GlobalRoutingPolicy = field(default_factory=GlobalRoutingPolicy)
    NegotiatedRouting: NegotiatedRoutingPolicy = field(
        default_factory=NegotiatedRoutingPolicy
    )
    TrackAssignment: TrackAssignmentPolicy = field(default_factory=TrackAssignmentPolicy)
    AdaptiveRouting: AdaptiveRoutingPolicy = field(default_factory=AdaptiveRoutingPolicy)
    DetailedRouting: DetailedRoutingPolicy = field(default_factory=DetailedRoutingPolicy)
    Repair: RepairPolicy = field(default_factory=RepairPolicy)
    QualityGate: QualityGatePolicy = field(default_factory=QualityGatePolicy)
    QualityTarget: str = "first-legal"
    RuntimeBudgetSeconds: float = 420.0
    Seed: int = 0

    def ToDictionary(self) -> dict[str, object]:
        """Return a stable machine-readable policy snapshot."""
        return asdict(self)


DefaultPhysicalDesignPolicy = PhysicalDesignPolicy()

# Compatibility is deliberately a frozen value, not an alias to the evolving
# default policy. This preserves the measured pre-rewrite route.
CompatibilityPhysicalDesignPolicy = PhysicalDesignPolicy(
    PolicyVersion="physical-design-v1-compatibility",
    Placement=PlacementPolicy(),
    NandPacking=NandPackingPolicy(),
    TrackAssignment=TrackAssignmentPolicy(
        MaximumRouteCandidatesPerNet=1536,
        MaximumAssignmentExpansions=100_000,
    ),
    AdaptiveRouting=AdaptiveRoutingPolicy(),
)

LocalFirstPhysicalDesignPolicy = PhysicalDesignPolicy(
    PolicyVersion="physical-design-v11-negotiated-route-trees",
    Placement=PlacementPolicy(
        CompactPassLimit=16,
        RoutingSpacing=6,
        LocalFanoutDistance=8,
        LocalFanoutWeight=16,
        HpwlWeight=3,
        PinEscapeLength=1,
        MaximumRoutingLayers=0,
        RoutingFeedbackIterations=1,
        RoutingSpacingAlternatives=2,
        PreferWideTerminalBanks=False,
        TerminalBankOffsetX=0,
        EnableRoutingFeedback=True,
    ),
    NandPacking=NandPackingPolicy(
        Enabled=True,
        RetainedPlacementCandidates=6,
    ),
    MaterialObjective=MaterialObjectivePolicy(
        Enabled=False,
        MinimumComponentFunctionalShare=0.60,
        MaximumRoutingFunctionalShare=0.40,
        MaximumRoutingDominance=0.40,
        MaximumRouteShare=0.40,
        MaximumRawDustFunctionalShare=0.45,
        MaximumFootprint=600,
        MaximumNonAirBlocks=500,
    ),
    Organization=RoutingOrganizationPolicy(Enabled=True),
    GlobalRouting=GlobalRoutingPolicy(
        CandidateLaneCount=24,
        CorridorCapacity=1,
        OverflowPenalty=12,
        ExistingGuideHintWeight=2,
        IntraClusterEnvelope=2,
        SharedBoundaryEnvelope=6,
        MaximumRipupPasses=2,
        StagnationPassLimit=2,
        EnableCapacityAwareGuides=True,
    ),
    NegotiatedRouting=NegotiatedRoutingPolicy(Enabled=True),
    TrackAssignment=TrackAssignmentPolicy(
        ReassignmentLimit=20,
        ReserveRepeaterSites=True,
        MaximumPortalsPerTerminal=48,
        MaximumRouteCandidatesPerNet=2048,
        MaximumAssignmentExpansions=500_000,
    ),
    AdaptiveRouting=AdaptiveRoutingPolicy(
        Enabled=True,
        InitialPortalsPerTerminal=6,
        PortalGrowthFactor=2,
        InitialLaneCount=4,
        LaneGrowthFactor=2,
        MaximumPortalReservationAlternatives=2,
        MaximumLaneDiversityEscalations=4,
        InitialCandidateRequestsPerSignal=8,
        MaximumCandidateDiversityEscalations=3,
        InitialCandidatesPerNet=96,
        CandidateGrowthFactor=2,
        MinimumCandidatesPerNet=8,
        CandidateClaimWorkQuantum=64,
        MaximumCandidateGenerationExpansions=2_000_000,
        InitialAssignmentExpansions=64,
        AssignmentGrowthFactor=2,
        BaseAssignmentExpansions=8_000,
        AssignmentExpansionsPerNet=1_200,
        AssignmentExpansionsPerTerminal=250,
        MaximumAssignmentExpansions=180_000,
        MaximumRuntimeSeconds=180.0,
    ),
    DetailedRouting=DetailedRoutingPolicy(
        GuideExpansion=3,
        LengthPenalty=8,
        MinimumGuidePenalty=1,
        StrictGuideMultiplier=2,
        StrictBendPenalty=10,
        RepairBendPenalty=6,
        StrictViaPenalty=7,
        RepairViaPenalty=5,
        LayerPenalty=8,
        CandidateBendWeight=8,
        CandidateViaWeight=6,
        RepeaterPenalty=24,
        StagnationPassLimit=6,
        StrictBaseExpansions=25_000,
        StrictExpansionsPerNet=1_200,
        StrictMaximumExpansions=90_000,
        RepairBaseExpansions=70_000,
        RepairExpansionsPerNet=3_000,
        RepairMaximumExpansions=160_000,
        MinimumCandidateExpansionLimit=2_048,
    ),
    QualityGate=QualityGatePolicy(Enabled=False),
    QualityTarget="first-legal",
    RuntimeBudgetSeconds=120.0,
)


RoutingAcceptanceProfiles = {
    "FullAdder": RoutingAcceptanceProfile(
        Name="FullAdder",
        MaximumRuntimeSeconds=10.0,
        MaximumFootprint=600,
        MaximumNonAirBlocks=500,
        MinimumComponentFunctionalShare=0.60,
        MaximumRoutingFunctionalShare=0.40,
        MaximumRawDustFunctionalShare=0.45,
    ),
    "RippleCarryAdder4": RoutingAcceptanceProfile(
        Name="RippleCarryAdder4",
        MaximumRuntimeSeconds=25.0,
    ),
    "CarryLookaheadAdder4": RoutingAcceptanceProfile(
        Name="CarryLookaheadAdder4",
        MaximumRuntimeSeconds=120.0,
    ),
}


def PolicyForRoutingStrategy(Strategy: RoutingStrategy) -> PhysicalDesignPolicy:
    """Resolve the immutable policy attached to one routing implementation."""
    if Strategy == RoutingStrategy.Compatibility:
        return CompatibilityPhysicalDesignPolicy
    return LocalFirstPhysicalDesignPolicy


def ExecutionStrategyForRequest(Strategy: RoutingStrategy) -> RoutingStrategy:
    """Resolve an explicit request without enabling automatic fallback."""
    if Strategy == RoutingStrategy.Compatibility:
        return RoutingStrategy.Compatibility
    return RoutingStrategy.NewRouterFirst


def BuildRoutingAttemptPolicies() -> tuple[RoutingAttemptPolicy, ...]:
    """Build the single authoritative routing attempt."""
    return (
        RoutingAttemptPolicy("Authoritative", 20, 6, 12.0, 320, 4),
    )

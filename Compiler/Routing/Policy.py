"""Typed, serializable physical-design search policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum


class RoutingStrategy(str, Enum):
    """User-visible physical-routing strategy."""

    Default = "default"

    @classmethod
    def Parse(cls, Value: str | "RoutingStrategy") -> "RoutingStrategy":
        if isinstance(Value, cls):
            return Value
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
    # Preserve the base cell-clearance gap, but size the optional routing
    # corridor between cluster columns/rows from the distinct nets that cross
    # that boundary.
    # their established geometry.
    EnableDemandAwareInterClusterSpacing: bool = False
    DemandAwareBoundaryTrackPitch: int = 0
    # A relocation recipe is a geometry change, not evidence that every
    # routing plane is necessary.  Allow policies to climb the layer ladder from
    # the access-derived minimum and only spend vertical headroom after a
    # concrete failure.
    ForceMaximumRoutingLayersAfterPlacementRelocation: bool = False

    def __post_init__(self) -> None:
        if self.DemandAwareBoundaryTrackPitch < 0:
            raise ValueError("DemandAwareBoundaryTrackPitch cannot be negative")


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
    # Placement-owned wiring is selected as one bounded resource assignment
    # per packed cluster.  These are search limits, not circuit-specific
    # heuristics, so the same policy applies to every NAND graph.
    EnableJointLocalRouting: bool = True
    MaximumLocalRouteCandidatesPerSignal: int = 16
    MaximumLocalClusterAssignmentExpansions: int = 4096
    MaximumLocalRepeatersPerNet: int = 2
    # Keep I/O cells outside packed NAND fabric while allowing the shell to be
    # tuned independently from inter-cluster routing corridors.
    TerminalShellClearance: int = 1
    TerminalShellLateralSearch: int = 0
    MaximumTerminalPlacementCandidates: int = 16
    MaximumTerminalAssignmentExpansions: int = 4096
    RequireCompleteLocalFanoutClaims: bool = False
    EnableProactiveInterClusterRelocation: bool = True
    EnableLocalGeometryRepair: bool = True
    LocalGeometryRepairColumnGap: int = 2
    DeferUnpackedOracle: bool = True
    PlacementFeedbackIterations: int = 3
    GraphBeamEnabled: bool = True
    # Choose each packed cluster's rigid world transform together with its
    # grid slot.  Per-NAND graph-beam orientation remains local to the
    # cluster; this search decides how that legal layout meets its neighbours.
    EnableJointClusterOrientation: bool = True
    JointPlacementBeamWidth: int = 64
    JointPlacementPassLimit: int = 12
    RetainedJointPlacementCandidates: int = 6
    EnableStructuralReuse: bool = True
    MaximumStructuralReuseMappings: int = 4096
    MaximumFrozenLocalNetNodes: int = 32
    MaximumFrozenLocalTargets: int = 16
    EnableVerticalClusterStacking: bool = True
    # Repeated structural clusters usually need planar separation because
    # their pin-access geometry is identical.  Keep that conservative default
    # unless a policy explicitly asks the placer to evaluate vertical reuse.
    EnableRepeatedStructuralVerticalStacking: bool = False
    MaximumClustersPerStack: int = 4
    ClusterDeckPitch: int = 6

    def __post_init__(self) -> None:
        if self.JointPlacementBeamWidth < 1:
            raise ValueError("JointPlacementBeamWidth must be positive")
        if self.JointPlacementPassLimit < 1:
            raise ValueError("JointPlacementPassLimit must be positive")
        if self.RetainedJointPlacementCandidates < 1:
            raise ValueError("RetainedJointPlacementCandidates must be positive")
        if self.MaximumStructuralReuseMappings < 1:
            raise ValueError(
                "MaximumStructuralReuseMappings must be positive"
            )
        if self.MaximumLocalRouteCandidatesPerSignal < 1:
            raise ValueError("MaximumLocalRouteCandidatesPerSignal must be positive")
        if self.MaximumLocalClusterAssignmentExpansions < 1:
            raise ValueError(
                "MaximumLocalClusterAssignmentExpansions must be positive"
            )
        if self.MaximumLocalRepeatersPerNet < 0:
            raise ValueError("MaximumLocalRepeatersPerNet cannot be negative")
        if self.LocalGeometryRepairColumnGap < 1:
            raise ValueError("LocalGeometryRepairColumnGap must be positive")
        if self.TerminalShellClearance < 1:
            raise ValueError("TerminalShellClearance must be positive")
        if self.TerminalShellLateralSearch < 0:
            raise ValueError("TerminalShellLateralSearch cannot be negative")
        if self.MaximumTerminalPlacementCandidates < 1:
            raise ValueError("MaximumTerminalPlacementCandidates must be positive")
        if self.MaximumTerminalAssignmentExpansions < 1:
            raise ValueError("MaximumTerminalAssignmentExpansions must be positive")
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
    # Evaluate complete, legal routed placement candidates by their final
    # emitted volume and routing share instead of publishing the first legal
    # route.
    OptimizeRoutingPercentage: bool = False
    MinimumRemainingRoutingPercentageSearchSeconds: float = 15.0
    MinimumRoutingPercentageSelectionNandCount: int = 16

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
        if self.MinimumRemainingRoutingPercentageSearchSeconds <= 0:
            raise ValueError(
                "MinimumRemainingRoutingPercentageSearchSeconds must be positive"
            )
        if self.MinimumRoutingPercentageSelectionNandCount < 1:
            raise ValueError(
                "MinimumRoutingPercentageSelectionNandCount must be positive"
            )


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
    # A tree search builds its guide-cost field over the complete active sparse
    # region before A* can reach a portal.  One hundred milliseconds is below
    # that setup cost on multi-cluster arithmetic designs, so every otherwise
    # legal portal alternative can expire before its search budget is used.
    # Keep this independently bounded, but leave enough time for the
    # configured strict expansion limit to distinguish a real repeater cut
    # from an incomplete search.
    MaximumRouteTreeRequestMilliseconds: int = 500

    def __post_init__(self) -> None:
        for Name in (
            "TilePitchInTracks",
            "MaximumIterations",
            "StagnationPassLimit",
            "PresentConflictPenalty",
            "HistoryIncrement",
            "MaximumPlacementFeedbackRounds",
            "MaximumRouteTreeRequestMilliseconds",
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
    # Capacity assignment is feasibility-first.  Searching the available
    # layer ceilings in order prevents an otherwise legal solution from
    # spending a tall routing plane merely because it was encountered first.
    MinimizeMaximumRoutingLayer: bool = False


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

LocalFirstPhysicalDesignPolicy = PhysicalDesignPolicy(
    PolicyVersion="physical-design-v16-reconvergent-access",
    Placement=PlacementPolicy(
        CompactPassLimit=16,
        RoutingSpacing=5,
        LocalFanoutDistance=8,
        LocalFanoutWeight=16,
        HpwlWeight=3,
        PinEscapeLength=1,
        MaximumRoutingLayers=3,
        RoutingFeedbackIterations=1,
        RoutingSpacingAlternatives=2,
        PreferWideTerminalBanks=False,
        TerminalBankOffsetX=0,
        EnableRoutingFeedback=True,
        EnableDemandAwareInterClusterSpacing=True,
        DemandAwareBoundaryTrackPitch=2,
    ),
    NandPacking=NandPackingPolicy(
        Enabled=True,
        RetainedPlacementCandidates=6,
        MaximumTerminalPlacementCandidates=32,
        MaximumTerminalAssignmentExpansions=65536,
        TerminalShellLateralSearch=0,
        RequireCompleteLocalFanoutClaims=True,
        LocalGeometryRepairColumnGap=1,
    ),
    MaterialObjective=MaterialObjectivePolicy(
        Enabled=True,
        MinimumComponentFunctionalShare=0.60,
        MaximumRoutingFunctionalShare=0.40,
        MaximumRoutingDominance=0.40,
        MaximumRouteShare=0.40,
        MaximumRawDustFunctionalShare=0.45,
        MaximumFootprint=600,
        MaximumNonAirBlocks=500,
        # Full rendered-candidate comparison can require several complete
        # detailed routes.  Keep interactive compilation bounded to one
        # authoritative route; the negotiated router still minimizes volume
        # within that route attempt.
        OptimizeRoutingPercentage=False,
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
    NegotiatedRouting=NegotiatedRoutingPolicy(
        Enabled=True,
        MaximumPlacementFeedbackRounds=1,
        MaximumPackedAreaGrowth=4.0,
        # Retained-domain assignment distinguishes a changing tree set from a
        # real plateau before placement feedback.  RCA4 otherwise repeats the
        # same overflow through its complete acceptance deadline, so keep the
        # bounded three-pass stagnation window.
        StagnationPassLimit=3,
    ),
    TrackAssignment=TrackAssignmentPolicy(
        ReassignmentLimit=20,
        ReserveRepeaterSites=True,
        MaximumPortalsPerTerminal=48,
        MaximumRouteCandidatesPerNet=2048,
        MaximumAssignmentExpansions=500_000,
        MinimizeMaximumRoutingLayer=True,
    ),
    AdaptiveRouting=AdaptiveRoutingPolicy(
        Enabled=True,
        InitialPortalsPerTerminal=6,
        PortalGrowthFactor=2,
        InitialLaneCount=4,
        LaneGrowthFactor=2,
        MaximumPortalReservationAlternatives=2,
        MaximumLaneDiversityEscalations=4,
        InitialCandidateRequestsPerSignal=4,
        MaximumCandidateDiversityEscalations=12,
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
        MaximumRuntimeSeconds=360.0,
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
        # Initial adaptive batches are intentionally small.  If every portal
        # request for a large net exhausts that batch, the authoritative
        # planner retries only that net at this strict budget; 90k is needed
        # to reach a legal repeater refresh site in the RCA8 sparse region.
        StrictBaseExpansions=90_000,
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
    "RippleCarryAdder8": RoutingAcceptanceProfile(
        Name="RippleCarryAdder8",
        MaximumRuntimeSeconds=30.0,
    ),
}


@dataclass(frozen=True)
class RoutingCircuitComplexityProfile:
    """Topology-aware signal complexity captured outside runtime execution names."""

    SignalCount: int = 0
    GateCount: int = 0
    RoutingGraphEdgeCount: int = 0
    MaximumFanout: int = 0
    ReconvergentFanoutCount: int = 0
    PeakBoundaryDemand: int = 0
    MandatoryAccessConflictResources: int = 0

    def __post_init__(self) -> None:
        if self.SignalCount < 0:
            raise ValueError("SignalCount must be non-negative")
        if self.GateCount < 0:
            raise ValueError("GateCount must be non-negative")
        if self.RoutingGraphEdgeCount < 0:
            raise ValueError("RoutingGraphEdgeCount must be non-negative")
        if self.MaximumFanout < 0:
            raise ValueError("MaximumFanout must be non-negative")
        if self.ReconvergentFanoutCount < 0:
            raise ValueError("ReconvergentFanoutCount must be non-negative")
        if self.PeakBoundaryDemand < 0:
            raise ValueError("PeakBoundaryDemand must be non-negative")
        if self.MandatoryAccessConflictResources < 0:
            raise ValueError(
                "MandatoryAccessConflictResources must be non-negative"
            )


def _BuildRoutingPolicyComplexityTier(
    Profile: RoutingCircuitComplexityProfile,
) -> int:
    SignalPressure = max(0, min(2, (Profile.SignalCount - 20) // 20))
    Score = SignalPressure
    if Profile.GateCount >= 72:
        Score += 1
    if Profile.RoutingGraphEdgeCount >= 90:
        Score += 1
    if Profile.MaximumFanout >= 4:
        Score += 1
    if Profile.ReconvergentFanoutCount >= 2:
        Score += 1
    if Profile.PeakBoundaryDemand >= 20:
        Score += 1
    if Profile.MandatoryAccessConflictResources > 0:
        Score += 1
    return min(6, max(0, Score))


def PolicyForRoutingStrategy(Strategy: RoutingStrategy) -> PhysicalDesignPolicy:
    """Resolve the immutable policy attached to one routing implementation."""
    return LocalFirstPhysicalDesignPolicy


def BuildRoutingPolicyForCircuit(
    Policy: PhysicalDesignPolicy,
    ComplexityProfile: RoutingCircuitComplexityProfile | None = None,
) -> PhysicalDesignPolicy:
    """Apply deterministic policy widening from topology metrics only.

    This selection is intentionally independent of any circuit identifier.
    """
    if ComplexityProfile is None:
        return Policy
    ComplexityTier = _BuildRoutingPolicyComplexityTier(ComplexityProfile)
    if ComplexityTier < 4:
        return Policy
    return replace(
        Policy,
        NandPacking=replace(
            Policy.NandPacking,
            RetainedJointPlacementCandidates=min(
                12,
                6 + ComplexityTier,
            ),
        ),
        AdaptiveRouting=replace(
            Policy.AdaptiveRouting,
            MaximumAssignmentExpansions=360_000,
        ),
        DetailedRouting=replace(
            Policy.DetailedRouting,
            StrictBaseExpansions=max(
                Policy.DetailedRouting.StrictBaseExpansions,
                120_000,
            ),
            StrictMaximumExpansions=180_000,
        ),
    )


def ExecutionStrategyForRequest(Strategy: RoutingStrategy) -> RoutingStrategy:
    """Resolve an explicit request without enabling automatic fallback."""
    return RoutingStrategy.Default


def BuildRoutingAttemptPolicies() -> tuple[RoutingAttemptPolicy, ...]:
    """Build the single authoritative routing attempt."""
    return (
        RoutingAttemptPolicy("Authoritative", 20, 6, 12.0, 320, 4),
    )

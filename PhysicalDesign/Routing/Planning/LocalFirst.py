"""Explicit contracts and deterministic feedback for local-first routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections import Counter
from math import ceil
from typing import Any, Callable

from ...Geometry.Placement import GetGateInputAccess
from ...Geometry.Rotation import RotatedCellSize
from .ChannelPlanner import (
    BuildNetRoutingProfiles,
    CandidateLanes,
)
from ...Policy import GlobalRoutingPolicy, PhysicalDesignPolicy
from ...Runtime.Reliability import BuildStableFingerprint
from ...Redstone.Technology import RedstoneRoutingTechnology

Position2 = tuple[int, int]
Position3 = tuple[int, int, int]


@dataclass(frozen=True)
class RoutingDemandEstimate:
    """Circuit-scale routing demand, independent of benchmark identity."""

    NandCount: int
    RoutableNetCount: int
    TerminalCount: int
    MaximumFanout: int
    TotalHpwl: int
    BoundaryDemand: int
    PinScarcityCount: int
    ProjectedCorridorDemand: int
    CongestionEstimate: float

    def ToDictionary(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DerivedRoutingBudget:
    """Concrete deterministic bounds selected from demand and policy."""

    ClusterCellCeiling: int
    LayerCount: int
    PortalsPerTerminal: int
    LaneCount: int
    CandidatesPerNet: int
    CandidateExpansionsPerNet: int
    AssignmentExpansions: int
    ReroutePasses: int
    PlacementAlternatives: int
    RuntimeSeconds: float
    Rationale: tuple[str, ...]

    def ToDictionary(self) -> dict[str, object]:
        Value = asdict(self)
        Value["Rationale"] = list(self.Rationale)
        return Value


def EstimateRoutingDemand(Placed: Any, Profiles: dict[str, Any]) -> RoutingDemandEstimate:
    """Measure demand from the actual placed terminals and unresolved nets."""
    NandCount = sum(Gate.Kind == "NAND" for Gate in Placed.PlacedGates)
    TerminalCount = sum(1 + len(Profile.Targets) for Profile in Profiles.values())
    MaximumFanout = max((len(Profile.Targets) for Profile in Profiles.values()), default=0)
    TotalHpwl = sum(Profile.Span for Profile in Profiles.values())
    BoundaryDemand = sum(
        len(Profile.Targets) + (1 if Profile.Seed is not None else 0)
        for Profile in Profiles.values()
    )
    PinScarcityCount = sum(
        len(set(Profile.SourceAccessPath)) <= 1
        or any(len(set(Path)) <= 1 for Path in Profile.TargetAccessPaths.values())
        for Profile in Profiles.values()
    )
    ProjectedCorridorDemand = sum(
        max(1, Profile.Span) * max(1, len(Profile.Targets))
        for Profile in Profiles.values()
    )
    RoutableNetCount = len(Profiles)
    CongestionEstimate = (
        ProjectedCorridorDemand / max(1, NandCount * TerminalCount)
    )
    return RoutingDemandEstimate(
        NandCount=NandCount,
        RoutableNetCount=RoutableNetCount,
        TerminalCount=TerminalCount,
        MaximumFanout=MaximumFanout,
        TotalHpwl=TotalHpwl,
        BoundaryDemand=BoundaryDemand,
        PinScarcityCount=PinScarcityCount,
        ProjectedCorridorDemand=ProjectedCorridorDemand,
        CongestionEstimate=round(CongestionEstimate, 6),
    )


def DeriveRoutingBudget(
    Demand: RoutingDemandEstimate,
    Policy: PhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology,
) -> DerivedRoutingBudget:
    """Convert measured demand into bounded monotonic routing work."""
    Adaptive = Policy.AdaptiveRouting
    if not Adaptive.Enabled:
        LayerCount = Policy.Placement.MaximumRoutingLayers or Technology.MaximumRoutableLayerCount
        return DerivedRoutingBudget(
            ClusterCellCeiling=Policy.NandPacking.MaximumClusterCells,
            LayerCount=LayerCount,
            PortalsPerTerminal=Policy.TrackAssignment.MaximumPortalsPerTerminal,
            LaneCount=Policy.GlobalRouting.CandidateLaneCount,
            CandidatesPerNet=Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
            CandidateExpansionsPerNet=Policy.DetailedRouting.StrictMaximumExpansions,
            AssignmentExpansions=Policy.TrackAssignment.MaximumAssignmentExpansions,
            ReroutePasses=Policy.GlobalRouting.MaximumRipupPasses,
            PlacementAlternatives=max(1, Policy.NandPacking.RetainedPlacementCandidates),
            RuntimeSeconds=Policy.RuntimeBudgetSeconds,
            Rationale=("compatibility policy uses frozen explicit limits",),
        )
    # Progressive routing starts at the technology minimum.  Additional
    # layers are an escalation resource, not an up-front circuit-size guess.
    LayerCount = Technology.MinimumRoutingLayerCount
    Portals = min(
        Policy.TrackAssignment.MaximumPortalsPerTerminal,
        Adaptive.InitialPortalsPerTerminal
        + Demand.MaximumFanout
        + Demand.PinScarcityCount // max(1, Demand.RoutableNetCount),
    )
    Lanes = min(
        Policy.GlobalRouting.CandidateLaneCount,
        Adaptive.InitialLaneCount + ceil(Demand.CongestionEstimate),
    )
    DemandCandidates = (
        Adaptive.InitialCandidatesPerNet
        + Demand.MaximumFanout * 4
        + ceil(Demand.TotalHpwl / max(1, Demand.NandCount))
        + Demand.PinScarcityCount
        + Demand.RoutableNetCount
    )
    Candidates = min(
        Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
        max(
            Adaptive.InitialCandidatesPerNet,
            DemandCandidates,
        ),
    )
    AssignmentExpansions = min(
        Policy.TrackAssignment.MaximumAssignmentExpansions,
        Adaptive.MaximumAssignmentExpansions,
        Adaptive.BaseAssignmentExpansions
        + Adaptive.AssignmentExpansionsPerNet * Demand.RoutableNetCount
        + Adaptive.AssignmentExpansionsPerTerminal * Demand.TerminalCount,
    )
    CandidateExpansions = min(
        Policy.DetailedRouting.StrictMaximumExpansions,
        Policy.DetailedRouting.StrictBaseExpansions
        + Policy.DetailedRouting.StrictExpansionsPerNet * Demand.RoutableNetCount,
    )
    return DerivedRoutingBudget(
        ClusterCellCeiling=Policy.NandPacking.MaximumClusterCells,
        LayerCount=LayerCount,
        PortalsPerTerminal=max(1, Portals),
        LaneCount=max(1, Lanes),
        CandidatesPerNet=max(1, Candidates),
        CandidateExpansionsPerNet=max(1, CandidateExpansions),
        AssignmentExpansions=max(1, AssignmentExpansions),
        ReroutePasses=Policy.GlobalRouting.MaximumRipupPasses,
        PlacementAlternatives=max(1, Policy.NandPacking.RetainedPlacementCandidates),
        RuntimeSeconds=min(Policy.RuntimeBudgetSeconds, Adaptive.MaximumRuntimeSeconds),
        Rationale=(
            "routing starts at the technology minimum layer count",
            "portal and lane diversity scale with fanout, scarcity, and congestion",
            "candidate and assignment work scale with nets and terminals",
        ),
    )


@dataclass(frozen=True)
class ClusterContract:
    ClusterKey: str
    GateNames: tuple[str, ...]
    InputSignals: tuple[str, ...]
    OutputSignals: tuple[str, ...]
    PinEscapeWindows: dict[str, tuple[Position2, ...]]
    KeepoutColumns: tuple[Position2, ...]


@dataclass(frozen=True)
class PlacementSolution:
    GateSites: dict[str, Position2]
    Hpwl: int
    LocalFanoutPenalty: int
    PinEscapeConflictCount: int


@dataclass(frozen=True)
class GlobalGuide:
    Signal: str
    Layer: int
    Columns: tuple[Position2, ...]
    Demand: int
    IsLocal: bool


@dataclass(frozen=True)
class DetailedRoute:
    Signal: str
    Nodes: tuple[Position3, ...]
    Length: int
    LayerTransitions: int


@dataclass(frozen=True)
class RipupPlan:
    Signals: tuple[str, ...]
    CongestionHistory: dict[Position2, int]
    PassIndex: int
    Stagnated: bool


@dataclass(frozen=True)
class GuidePlanningIteration:
    PassIndex: int
    OverflowPeak: int
    OverflowCells: int
    ReroutedSignals: tuple[str, ...]


@dataclass(frozen=True)
class CoarseGuidePlan:
    Guides: dict[str, frozenset[Position2]]
    Layers: dict[str, int]
    Axes: dict[str, str]
    Lanes: dict[str, int]
    Usage: dict[tuple[int, int, int], int]
    Overflow: dict[tuple[int, int, int], int]
    LocalSignals: frozenset[str]
    Iterations: tuple[GuidePlanningIteration, ...]
    LocalInputFingerprintsBySignal: dict[str, str] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )

    @property
    def OverflowPeak(self) -> int:
        return max(self.Overflow.values(), default=0)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Guides": {
                Signal: [list(Position) for Position in sorted(Guide)]
                for Signal, Guide in sorted(self.Guides.items())
            },
            "Layers": dict(sorted(self.Layers.items())),
            "Axes": dict(sorted(self.Axes.items())),
            "Lanes": dict(sorted(self.Lanes.items())),
            "OverflowPeak": self.OverflowPeak,
            "OverflowCells": len(self.Overflow),
            "LocalSignals": sorted(self.LocalSignals),
            "Iterations": [asdict(Value) for Value in self.Iterations],
        }


CapacityAwareGuideOption = tuple[
    int,
    int,
    int,
    int,
    str,
    int,
    frozenset[Position2],
]


@dataclass(frozen=True)
class CapacityAwareGuideOptionDomain:
    """Deterministic signal-local choices before shared capacity assignment."""

    Signal: str
    LocalInputFingerprint: str
    Options: tuple[CapacityAwareGuideOption, ...]

    @property
    def OptionFingerprints(self) -> tuple[str, ...]:
        return tuple(
            BuildStableFingerprint((
                "capacity-aware-guide-option-v1",
                self.LocalInputFingerprint,
                Escape,
                Length,
                Layer,
                AxisBias,
                Axis,
                Lane,
                tuple(sorted(Guide)),
            ))
            for Escape, Length, Layer, AxisBias, Axis, Lane, Guide
            in self.Options
        )


@dataclass(frozen=True)
class PlacementRoutingFeedback:
    RoutingSpacing: int
    BoundaryOverflow: int
    PinScarcityCount: int
    GuideOverflowPeak: int
    GuideOverflowCells: int
    PinEscapeConflictCount: int
    LocalClaimCoverageRatio: float
    LocalRouteTargets: int
    LocalDirectConnectionCount: int
    EstimatedGlobalExtensionNodes: int
    EstimatedGlobalExtensionNets: int
    RoutingDominanceProxy: float
    FrozenLocalNetCount: int
    PreOwnedNodeCount: int
    Hpwl: int
    LocalFanoutPenalty: int
    WeightedLocalityCost: int
    GateFootprint: int

    @property
    def RoutabilityWorkEstimate(self) -> int:
        """Combine assignment size, fixed ownership, and boundary pressure."""
        return (
            5 * self.EstimatedGlobalExtensionNets
            + self.PreOwnedNodeCount
            + self.EstimatedGlobalExtensionNodes
            + 3 * self.BoundaryOverflow
            + ceil(self.PinScarcityCount / 8)
        )

    @property
    def Score(self) -> tuple[int, ...]:
        # Estimate bounded routing work before applying stable physical
        # tie-breakers. This preserves useful local routing on small designs
        # while preventing severe boundary pressure and fixed ownership from
        # hiding behind a deceptively small global-net count.
        return (
            self.RoutabilityWorkEstimate,
            self.BoundaryOverflow,
            self.PinScarcityCount,
            self.GuideOverflowPeak,
            self.GuideOverflowCells,
            self.PinEscapeConflictCount,
            self.EstimatedGlobalExtensionNets,
            self.PreOwnedNodeCount + self.EstimatedGlobalExtensionNodes,
            -round(self.LocalClaimCoverageRatio * 10_000),
            self.WeightedLocalityCost,
            self.GateFootprint,
            -self.FrozenLocalNetCount,
            -self.LocalDirectConnectionCount,
        )

    def ToDictionary(self) -> dict[str, object]:
        Value = asdict(self)
        Value["RoutabilityWorkEstimate"] = self.RoutabilityWorkEstimate
        Value["Score"] = list(self.Score)
        return Value


@dataclass(frozen=True)
class LocalFirstSnapshot:
    Clusters: tuple[ClusterContract, ...]
    Placement: PlacementSolution
    Guides: tuple[GlobalGuide, ...]
    Routes: tuple[DetailedRoute, ...]

    def ToDictionary(self) -> dict[str, object]:
        return asdict(self)


def _SignalEndpoints(
    Placed: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, tuple[Position3, ...]]:
    Values: dict[str, list[Position3]] = {}
    Gates = list(Placed.PlacedGates)
    for GateIndex, Gate in enumerate(Gates):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "placement-feedback-endpoints",
                "CompletedGates": GateIndex,
                "TotalGates": len(Gates),
            })
        if Gate.OutputPin is not None:
            for Signal in Gate.Outputs:
                Values.setdefault(Signal, []).append(
                    Gate.OutputPin
                )
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, _Direction = GetGateInputAccess(Gate, InputIndex)
            Values.setdefault(Signal, []).append(Pin)
    return {Signal: tuple(Endpoints) for Signal, Endpoints in Values.items()}


def BuildPlacementSolution(
    Placed: Any,
    LocalFanoutDistance: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacementSolution:
    """Measure HPWL, locality, and pin-window overlap for a placed graph."""
    Endpoints = _SignalEndpoints(Placed, WorkCheck=WorkCheck)
    Hpwl = 0
    LocalFanoutPenalty = 0
    for SignalIndex, Values in enumerate(Endpoints.values()):
        if WorkCheck is not None and SignalIndex % 32 == 0:
            WorkCheck({
                "Phase": "placement-feedback-hpwl",
                "CompletedSignals": SignalIndex,
                "TotalSignals": len(Endpoints),
            })
        if len(Values) < 2:
            continue
        Span = (
            max(Value[0] for Value in Values) - min(Value[0] for Value in Values)
            + max(Value[2] for Value in Values) - min(Value[2] for Value in Values)
        )
        Hpwl += Span
        LocalFanoutPenalty += max(0, Span - LocalFanoutDistance)
    # A vertically stacked deck may legitimately share an X/Z column with a
    # lower deck.  Pin-window ownership is an exact physical-cell property,
    # so only the full three-dimensional escape coordinate can represent an
    # overlap here. Detailed routing remains responsible for adjacent-cell
    # electrical exclusions and portal capacity.
    EscapeOwners: dict[Position3, set[str]] = {}
    Gates = list(Placed.PlacedGates)
    for GateIndex, Gate in enumerate(Gates):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "placement-feedback-pin-escape",
                "CompletedGates": GateIndex,
                "TotalGates": len(Gates),
            })
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            Escape = tuple(
                Pin[Axis] + Direction[Axis]
                for Axis in range(3)
            )
            EscapeOwners.setdefault(Escape, set()).add(Signal)
    return PlacementSolution(
        GateSites={Gate.Name: (Gate.X, Gate.Z) for Gate in Placed.PlacedGates},
        Hpwl=Hpwl,
        LocalFanoutPenalty=LocalFanoutPenalty,
        PinEscapeConflictCount=sum(
            len(Signals) - 1 for Signals in EscapeOwners.values() if len(Signals) > 1
        ),
    )


def BuildLocalFirstSnapshot(
    Placement: Any,
    Routed: Any,
    LocalFanoutDistance: int,
    LocalRouteBudget: int,
) -> LocalFirstSnapshot:
    """Capture the placement-to-guide-to-detail contracts of a legal result."""
    Placed = Placement.Placed
    GateByName = {Gate.Name: Gate for Gate in Placed.PlacedGates}
    Clusters = []
    for Index, Names in enumerate(Placement.Clusters):
        Inputs = set()
        Outputs = set()
        PinEscapeWindows: dict[str, set[Position2]] = {}
        KeepoutColumns: set[Position2] = set()
        for Name in Names:
            Gate = GateByName[Name]
            Inputs.update(Gate.Inputs)
            Outputs.update(Gate.Outputs)
            Width, Depth = RotatedCellSize(Gate.Kind, Gate.Rotation)
            KeepoutColumns.update(
                (X, Z)
                for X in range(Gate.X, Gate.X + Width)
                for Z in range(Gate.Z, Gate.Z + Depth)
            )
            if Gate.OutputPin is not None and Gate.OutputDirection is not None:
                for Signal in Gate.Outputs:
                    PinEscapeWindows.setdefault(Signal, set()).add(
                        (
                            Gate.OutputPin[0] + Gate.OutputDirection[0],
                            Gate.OutputPin[2] + Gate.OutputDirection[2],
                        )
                    )
            for InputIndex, Signal in enumerate(Gate.Inputs):
                Pin, Direction = GetGateInputAccess(Gate, InputIndex)
                PinEscapeWindows.setdefault(Signal, set()).add(
                    (Pin[0] + Direction[0], Pin[2] + Direction[2])
                )
        Internal = Inputs & Outputs
        Clusters.append(
            ClusterContract(
                ClusterKey=f"Cluster{Index}",
                GateNames=tuple(sorted(Names)),
                InputSignals=tuple(sorted(Inputs - Internal)),
                OutputSignals=tuple(sorted(Outputs - Internal)),
                PinEscapeWindows={
                    Signal: tuple(sorted(Columns))
                    for Signal, Columns in sorted(PinEscapeWindows.items())
                },
                KeepoutColumns=tuple(sorted(KeepoutColumns)),
            )
        )
    Plan = Routed.GlobalPlan
    Guides = tuple(
        GlobalGuide(
            Signal=Signal,
            Layer=Plan.Layers[Signal],
            Columns=tuple(sorted(Plan.Guides[Signal])),
            Demand=1,
            IsLocal=len(Plan.Guides[Signal]) <= LocalRouteBudget,
        )
        for Signal in sorted(Plan.Guides)
    ) if Plan is not None else ()
    Routes = []
    for Signal, NodesValue in sorted(Routed.NetWires.items()):
        Nodes = tuple(sorted(set(NodesValue)))
        LayerTransitions = sum(
            any(
                Neighbor in Nodes
                for Neighbor in (
                    (X + 1, Y + 1, Z),
                    (X - 1, Y + 1, Z),
                    (X, Y + 1, Z + 1),
                    (X, Y + 1, Z - 1),
                )
            )
            for X, Y, Z in Nodes
        )
        Routes.append(DetailedRoute(Signal, Nodes, len(Nodes), LayerTransitions))
    return LocalFirstSnapshot(
        Clusters=tuple(Clusters),
        Placement=BuildPlacementSolution(Placed, LocalFanoutDistance),
        Guides=Guides,
        Routes=tuple(Routes),
    )


def BuildRipupPlan(
    ConflictSignals: tuple[str, ...],
    CongestionHistory: dict[Position2, int],
    PassIndex: int,
    PreviousSignals: tuple[str, ...] = (),
) -> RipupPlan:
    """Select only current offenders and report deterministic stagnation."""
    Signals = tuple(sorted(set(ConflictSignals)))
    return RipupPlan(
        Signals=Signals,
        CongestionHistory=dict(sorted(CongestionHistory.items())),
        PassIndex=PassIndex,
        Stagnated=bool(Signals) and Signals == tuple(sorted(set(PreviousSignals))),
    )


def _BuildRectilinearGuide(
    Terminals: tuple[Position2, ...],
    Axis: str,
    Lane: int,
    ObstacleBounds: tuple[int, int, int, int] | None = None,
    ObstacleCells: frozenset[Position2] = frozenset(),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> frozenset[Position2]:
    def AddRawSegment(Start: Position2, End: Position2) -> None:
        if Start[0] != End[0] and Start[1] != End[1]:
            raise ValueError("Channel segments must be axis aligned")
        DeltaX = 0 if Start[0] == End[0] else (1 if End[0] > Start[0] else -1)
        DeltaZ = 0 if Start[1] == End[1] else (1 if End[1] > Start[1] else -1)
        Position = Start
        SegmentPositions = 1
        Guide.add(Position)
        while Position != End:
            Position = (Position[0] + DeltaX, Position[1] + DeltaZ)
            Guide.add(Position)
            SegmentPositions += 1
            if WorkCheck is not None and SegmentPositions % 256 == 0:
                WorkCheck({
                    "Phase": "capacity-guide-segment",
                    "Axis": Axis,
                    "Lane": Lane,
                    "ProcessedSegmentPositions": SegmentPositions,
                    "GuidePositionCount": len(Guide),
                })

    def AddSegment(Start: Position2, End: Position2) -> None:
        EffectiveObstacleBounds = ObstacleBounds
        if ObstacleCells:
            SegmentObstacleCells = tuple(
                Position
                for Position in ObstacleCells
                if (
                    Start[0] == End[0] == Position[0]
                    and min(Start[1], End[1])
                    <= Position[1]
                    <= max(Start[1], End[1])
                )
                or (
                    Start[1] == End[1] == Position[1]
                    and min(Start[0], End[0])
                    <= Position[0]
                    <= max(Start[0], End[0])
                )
            )
            EffectiveObstacleBounds = (
                (
                    min(Value[0] for Value in ObstacleCells),
                    max(Value[0] for Value in ObstacleCells),
                    min(Value[1] for Value in ObstacleCells),
                    max(Value[1] for Value in ObstacleCells),
                )
                if SegmentObstacleCells
                else None
            )
        if EffectiveObstacleBounds is None:
            AddRawSegment(Start, End)
            return
        MinimumX, MaximumX, MinimumZ, MaximumZ = (
            EffectiveObstacleBounds
        )
        if (
            Start[1] == End[1]
            and MinimumZ <= Start[1] <= MaximumZ
            and min(Start[0], End[0]) <= MaximumX
            and max(Start[0], End[0]) >= MinimumX
        ):
            DetourZ = min(
                (MinimumZ - 1, MaximumZ + 1),
                key=lambda Value: (abs(Value - Start[1]), Value),
            )
            AddRawSegment(Start, (Start[0], DetourZ))
            AddRawSegment((Start[0], DetourZ), (End[0], DetourZ))
            AddRawSegment((End[0], DetourZ), End)
            return
        if (
            Start[0] == End[0]
            and MinimumX <= Start[0] <= MaximumX
            and min(Start[1], End[1]) <= MaximumZ
            and max(Start[1], End[1]) >= MinimumZ
        ):
            DetourX = min(
                (MinimumX - 1, MaximumX + 1),
                key=lambda Value: (abs(Value - Start[0]), Value),
            )
            AddRawSegment(Start, (DetourX, Start[1]))
            AddRawSegment((DetourX, Start[1]), (DetourX, End[1]))
            AddRawSegment((DetourX, End[1]), End)
            return
        AddRawSegment(Start, End)

    Guide: set[Position2] = set()
    if Axis == "X":
        Minimum = min(X for X, _Z in Terminals)
        Maximum = max(X for X, _Z in Terminals)
        AddSegment((Minimum, Lane), (Maximum, Lane))
        for TerminalIndex, (X, Z) in enumerate(Terminals):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "capacity-guide-terminal",
                    "Axis": Axis,
                    "Lane": Lane,
                    "CompletedTerminals": TerminalIndex,
                    "TotalTerminals": len(Terminals),
                })
            AddSegment((X, Z), (X, Lane))
    else:
        Minimum = min(Z for _X, Z in Terminals)
        Maximum = max(Z for _X, Z in Terminals)
        AddSegment((Lane, Minimum), (Lane, Maximum))
        for TerminalIndex, (X, Z) in enumerate(Terminals):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "capacity-guide-terminal",
                    "Axis": Axis,
                    "Lane": Lane,
                    "CompletedTerminals": TerminalIndex,
                    "TotalTerminals": len(Terminals),
                })
            AddSegment((X, Z), (Lane, Z))
    return frozenset(Guide)


def BuildCapacityAwareGuideOptionDomains(
    Profiles: dict[str, Any],
    LayerCount: int,
    MinimumX: int,
    MinimumZ: int,
    Policy: GlobalRoutingPolicy,
    Technology: RedstoneRoutingTechnology,
    LocalFanoutDistance: int,
    ComponentObstacleBounds: tuple[int, int, int, int] | None = None,
    ComponentObstacleCellsByLayer: dict[
        int, frozenset[Position2]
    ] | None = None,
    ComponentObstacleExemptCellsBySignal: dict[
        str, dict[int, frozenset[Position2]]
    ] | None = None,
    ComponentOwnedSignals: frozenset[str] = frozenset(),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, CapacityAwareGuideOptionDomain]:
    """Build independent deterministic guide choices for every signal."""
    if LayerCount < 1:
        raise ValueError("LayerCount must be positive")
    ComponentObstacleCellsByLayer = dict(
        ComponentObstacleCellsByLayer or {}
    )
    ComponentObstacleExemptCellsBySignal = dict(
        ComponentObstacleExemptCellsBySignal or {}
    )
    Domains: dict[str, CapacityAwareGuideOptionDomain] = {}
    LocalSignals = frozenset(
        Signal
        for Signal, Profile in Profiles.items()
        if Profile.Span <= LocalFanoutDistance
    )
    ProfileItems = list(Profiles.items())
    for ProfileIndex, (Signal, Profile) in enumerate(ProfileItems):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "capacity-guide-profile",
                "CompletedProfiles": ProfileIndex,
                "TotalProfiles": len(ProfileItems),
                "Signal": Signal,
            })
        Terminals = tuple(
            (Path[-1][0], Path[-1][2])
            for Path in (
                Profile.SourceAccessPath,
                *Profile.TargetAccessPaths.values(),
            )
        )
        Values: list[
            tuple[int, int, int, int, str, int, frozenset[Position2]]
        ] = []
        XSpan = max(X for X, _Z in Terminals) - min(X for X, _Z in Terminals)
        ZSpan = max(Z for _X, Z in Terminals) - min(Z for _X, Z in Terminals)
        PreferredAxis = "X" if XSpan >= ZSpan else "Z"
        for Axis in ("X", "Z"):
            Coordinates = (
                sorted(Z for _X, Z in Terminals)
                if Axis == "X"
                else sorted(X for X, _Z in Terminals)
            )
            Center = Coordinates[len(Coordinates) // 2]
            Anchor = MinimumZ if Axis == "X" else MinimumX
            Aligned = Anchor + (
                (Center - Anchor + Technology.TrackPitch // 2)
                // Technology.TrackPitch
            ) * Technology.TrackPitch
            # CandidateLaneCount is the policy ceiling; do not impose a
            # circuit-size-specific five-lane cap here.
            LaneCount = max(1, Policy.CandidateLaneCount)
            Lanes = CandidateLanes(
                Aligned,
                LaneCount,
                Technology.TrackPitch,
            )
            for LaneIndex, Lane in enumerate(Lanes):
                if WorkCheck is not None:
                    WorkCheck({
                        "Phase": "capacity-guide-lane",
                        "Signal": Signal,
                        "Axis": Axis,
                        "CompletedLanes": LaneIndex,
                        "TotalLanes": len(Lanes),
                    })
                Escape = max(
                    0,
                    abs(Lane - Center)
                    - (
                        Policy.IntraClusterEnvelope
                        if Signal in LocalSignals
                        else Policy.SharedBoundaryEnvelope
                    ),
                )
                for Layer in range(LayerCount):
                    ExactObstacleCells = (
                        ComponentObstacleCellsByLayer.get(
                            Layer,
                            frozenset(),
                        )
                        - ComponentObstacleExemptCellsBySignal.get(
                            Signal,
                            {},
                        ).get(Layer, frozenset())
                    )
                    Guide = _BuildRectilinearGuide(
                        Terminals,
                        Axis,
                        Lane,
                        ObstacleBounds=(
                            ComponentObstacleBounds
                            if (
                                Signal not in ComponentOwnedSignals
                                and not ComponentObstacleCellsByLayer
                            )
                            else None
                        ),
                        ObstacleCells=(
                            ExactObstacleCells
                            if Signal not in ComponentOwnedSignals
                            else frozenset()
                        ),
                        WorkCheck=WorkCheck,
                    )
                    Values.append(
                        (
                            Escape,
                            len(Guide),
                            Layer,
                            0 if Axis == PreferredAxis else 1,
                            Axis,
                            Lane,
                            Guide,
                        )
                    )
        if not Values:
            raise ValueError(
                "bounded guide planning produced no candidates"
            )
        SortedValues = tuple(sorted(Values))
        LocalInputFingerprint = BuildStableFingerprint((
            "capacity-aware-signal-guide-input-v1",
            LayerCount,
            MinimumX,
            MinimumZ,
            Policy.CandidateLaneCount,
            Policy.IntraClusterEnvelope,
            Policy.SharedBoundaryEnvelope,
            Technology.TrackPitch,
            tuple(sorted(Terminals)),
            Signal in LocalSignals,
            ComponentObstacleBounds,
            tuple(
                (Layer, tuple(sorted(Cells)))
                for Layer, Cells
                in sorted(ComponentObstacleCellsByLayer.items())
            ),
            tuple(
                (Layer, tuple(sorted(Cells)))
                for Layer, Cells in sorted(
                    ComponentObstacleExemptCellsBySignal.get(
                        Signal,
                        {},
                    ).items()
                )
            ),
            Signal in ComponentOwnedSignals,
        ))
        Domains[Signal] = CapacityAwareGuideOptionDomain(
            Signal=Signal,
            LocalInputFingerprint=LocalInputFingerprint,
            Options=SortedValues,
        )
    return Domains


def AssignCapacityAwareGuideOptionDomains(
    Profiles: dict[str, Any],
    OptionDomains: dict[str, CapacityAwareGuideOptionDomain],
    LayerCount: int,
    Policy: GlobalRoutingPolicy,
    LocalFanoutDistance: int,
    ComponentObstacleBounds: tuple[int, int, int, int] | None = None,
    ComponentObstacleCellsByLayer: dict[
        int, frozenset[Position2]
    ] | None = None,
    ComponentObstacleExemptCellsBySignal: dict[
        str, dict[int, frozenset[Position2]]
    ] | None = None,
    ComponentOwnedSignals: frozenset[str] = frozenset(),
    ReservedGuideResourcesBySignal: dict[
        str, frozenset[tuple[int, int, int]]
    ] | None = None,
    SeedPlan: CoarseGuidePlan | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> CoarseGuidePlan:
    """Jointly select signal-local options under shared corridor capacity."""
    if set(OptionDomains) != set(Profiles):
        raise ValueError("guide option domains must match profile signals")
    if any(
        Domain.Signal != Signal or not Domain.Options
        for Signal, Domain in OptionDomains.items()
    ):
        raise ValueError("guide option domains must be nonempty and keyed by signal")
    ReservedGuideResourcesBySignal = dict(
        ReservedGuideResourcesBySignal or {}
    )
    ComponentObstacleCellsByLayer = dict(
        ComponentObstacleCellsByLayer or {}
    )
    ComponentObstacleExemptCellsBySignal = dict(
        ComponentObstacleExemptCellsBySignal or {}
    )
    ReservedGuideResources = frozenset(
        Resource
        for Resources in ReservedGuideResourcesBySignal.values()
        for Resource in Resources
    )
    Options = {
        Signal: list(Domain.Options)
        for Signal, Domain in OptionDomains.items()
    }
    LocalSignals = frozenset(
        Signal
        for Signal, Profile in Profiles.items()
        if Profile.Span <= LocalFanoutDistance
    )

    Selected: dict[str, tuple[int, str, int, frozenset[Position2]]] = {}
    Usage: Counter[tuple[int, int, int]] = Counter()
    History: Counter[tuple[int, int, int]] = Counter()
    def SelectSignal(Signal: str) -> None:
        Best = None
        SignalOptions = Options[Signal]
        for OptionIndex, (
            Escape,
            Length,
            Layer,
            AxisBias,
            Axis,
            Lane,
            Guide,
        ) in enumerate(SignalOptions):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "capacity-guide-selection",
                    "Signal": Signal,
                    "CompletedOptions": OptionIndex,
                    "TotalOptions": len(SignalOptions),
                })
            OverflowCost = 0
            ColumnOverflowCost = 0
            HistoryCost = 0
            ComponentInteriorCost = 0
            ReservedPortConflictCost = 0
            for GuidePositionIndex, (X, Z) in enumerate(Guide, start=1):
                if WorkCheck is not None and GuidePositionIndex % 256 == 0:
                    WorkCheck({
                        "Phase": "capacity-guide-option-cells",
                        "Signal": Signal,
                        "CompletedOptions": OptionIndex,
                        "ProcessedGuidePositions": GuidePositionIndex,
                        "GuidePositionCount": len(Guide),
                    })
                OverflowCost += max(
                    0,
                    Usage[(Layer, X, Z)] + 1 - Policy.CorridorCapacity,
                )
                ColumnUsage = sum(
                    Usage[(ExistingLayer, X, Z)]
                    for ExistingLayer in range(LayerCount)
                )
                ColumnOverflowCost += max(0, ColumnUsage + 1 - 2)
                HistoryCost += History[(Layer, X, Z)]
                if (
                    (
                        ComponentObstacleBounds is not None
                        or ComponentObstacleCellsByLayer
                    )
                    and Signal not in ComponentOwnedSignals
                ):
                    if ComponentObstacleCellsByLayer:
                        ComponentInteriorCost += (
                            (X, Z)
                            in ComponentObstacleCellsByLayer.get(
                                Layer,
                                frozenset(),
                            )
                            - ComponentObstacleExemptCellsBySignal.get(
                                Signal,
                                {},
                            ).get(Layer, frozenset())
                        )
                    else:
                        assert ComponentObstacleBounds is not None
                        (
                            ComponentMinimumX,
                            ComponentMaximumX,
                            ComponentMinimumZ,
                            ComponentMaximumZ,
                        ) = ComponentObstacleBounds
                        ComponentInteriorCost += bool(
                            ComponentMinimumX <= X <= ComponentMaximumX
                            and ComponentMinimumZ <= Z <= ComponentMaximumZ
                        )
                if (Layer, X, Z) in ReservedGuideResources:
                    ReservedPortConflictCost += sum(
                        Signal != Owner
                        and (Layer, X, Z) in Resources
                        for Owner, Resources
                        in ReservedGuideResourcesBySignal.items()
                    )
            Cost = (
                ReservedPortConflictCost,
                ComponentInteriorCost,
                (OverflowCost + ColumnOverflowCost) * Policy.OverflowPenalty,
                Escape,
                HistoryCost,
                Layer,
                Length,
                AxisBias,
                Lane,
            )
            if Best is None or Cost < Best[0]:
                Best = (Cost, Layer, Axis, Lane, Guide)
        assert Best is not None
        _Cost, Layer, Axis, Lane, Guide = Best
        Selected[Signal] = (Layer, Axis, Lane, Guide)
        for GuidePositionIndex, (X, Z) in enumerate(Guide, start=1):
            Usage[(Layer, X, Z)] += 1
            if WorkCheck is not None and GuidePositionIndex % 256 == 0:
                WorkCheck({
                    "Phase": "capacity-guide-usage",
                    "Signal": Signal,
                    "ProcessedGuidePositions": GuidePositionIndex,
                    "GuidePositionCount": len(Guide),
                })

    Order = sorted(
        Profiles,
        key=lambda Signal: (
            -Profiles[Signal].Criticality,
            -Profiles[Signal].Fanout,
            Signal,
        ),
    )
    SeededSignals: set[str] = set()
    for SignalIndex, Signal in enumerate(Order):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "capacity-guide-initial-selection",
                "CompletedSignals": SignalIndex,
                "TotalSignals": len(Order),
                "Signal": Signal,
            })
        SeedSelection = None
        if (
            SeedPlan is not None
            and Signal in SeedPlan.Guides
            and Signal in SeedPlan.Layers
            and Signal in SeedPlan.Axes
            and Signal in SeedPlan.Lanes
        ):
            Candidate = (
                int(SeedPlan.Layers[Signal]),
                str(SeedPlan.Axes[Signal]),
                int(SeedPlan.Lanes[Signal]),
                frozenset(SeedPlan.Guides[Signal]),
            )
            CandidateMatchesCurrentDomain = any(
                (
                    int(Layer),
                    str(Axis),
                    int(Lane),
                    Guide,
                ) == Candidate
                for (
                    _Escape,
                    _Length,
                    Layer,
                    _AxisBias,
                    Axis,
                    Lane,
                    Guide,
                ) in Options[Signal]
            )
            CandidateAvoidsComponent = bool(
                (
                    ComponentObstacleBounds is None
                    and not ComponentObstacleCellsByLayer
                )
                or Signal in ComponentOwnedSignals
                or not any(
                    (
                        (X, Z)
                        in ComponentObstacleCellsByLayer.get(
                            Candidate[0],
                            frozenset(),
                        )
                        - ComponentObstacleExemptCellsBySignal.get(
                            Signal,
                            {},
                        ).get(Candidate[0], frozenset())
                    )
                    if ComponentObstacleCellsByLayer
                    else (
                        ComponentObstacleBounds is not None
                        and ComponentObstacleBounds[0]
                        <= X
                        <= ComponentObstacleBounds[1]
                        and ComponentObstacleBounds[2]
                        <= Z
                        <= ComponentObstacleBounds[3]
                    )
                    for X, Z in Candidate[3]
                )
            )
            CandidateAvoidsReservations = not any(
                (Candidate[0], X, Z) in ReservedGuideResources
                and any(
                    Signal != Owner
                    and (Candidate[0], X, Z) in Resources
                    for Owner, Resources
                    in ReservedGuideResourcesBySignal.items()
                )
                for X, Z in Candidate[3]
            )
            if (
                CandidateMatchesCurrentDomain
                and CandidateAvoidsComponent
                and CandidateAvoidsReservations
            ):
                SeedSelection = Candidate
        if SeedSelection is None:
            SelectSignal(Signal)
            continue
        Layer, Axis, Lane, Guide = SeedSelection
        Selected[Signal] = (Layer, Axis, Lane, Guide)
        SeededSignals.add(Signal)
        for X, Z in Guide:
            Usage[(Layer, X, Z)] += 1
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "capacity-guide-seed-reuse",
                "Signal": Signal,
                "SeededSignalCount": len(SeededSignals),
                "CompletedSignals": SignalIndex + 1,
                "TotalSignals": len(Order),
            })

    Iterations = []
    PreviousOverflow = None

    def BuildOverflow(Phase: str) -> dict[tuple[int, int, int], int]:
        OverflowValue: dict[tuple[int, int, int], int] = {}
        for UsageIndex, (Resource, Count) in enumerate(Usage.items(), start=1):
            if WorkCheck is not None and UsageIndex % 256 == 0:
                WorkCheck({
                    "Phase": Phase,
                    "ProcessedResources": UsageIndex,
                    "UsageResourceCount": len(Usage),
                })
            if Count > Policy.CorridorCapacity:
                OverflowValue[Resource] = Count - Policy.CorridorCapacity
        return OverflowValue

    for PassIndex in range(Policy.MaximumRipupPasses + 1):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "capacity-guide-ripup-pass",
                "PassIndex": PassIndex,
                "MaximumPasses": Policy.MaximumRipupPasses,
            })
        Overflow = BuildOverflow("capacity-guide-overflow")
        Contributors: Counter[str] = Counter()
        ContributorChecks = 0
        for Signal, (Layer, _Axis, _Lane, Guide) in Selected.items():
            for X, Z in Guide:
                ContributorChecks += 1
                if WorkCheck is not None and ContributorChecks % 256 == 0:
                    WorkCheck({
                        "Phase": "capacity-guide-contributors",
                        "PassIndex": PassIndex,
                        "ProcessedGuidePositions": ContributorChecks,
                    })
                if (Layer, X, Z) in Overflow:
                    Contributors[Signal] += 1
        Offenders = tuple(
            Signal
            for Signal, _Count in Contributors.most_common(
                max(1, min(4, len(Contributors)))
            )
        )
        Iterations.append(
            GuidePlanningIteration(
                PassIndex=PassIndex,
                OverflowPeak=max(Overflow.values(), default=0),
                OverflowCells=len(Overflow),
                ReroutedSignals=Offenders if Overflow else (),
            )
        )
        OverflowKey = (max(Overflow.values(), default=0), len(Overflow))
        if not Overflow or PassIndex == Policy.MaximumRipupPasses:
            break
        if PreviousOverflow is not None and OverflowKey >= PreviousOverflow:
            break
        PreviousOverflow = OverflowKey
        for ResourceIndex, (Resource, Count) in enumerate(
            Overflow.items(),
            start=1,
        ):
            History[Resource] += Count
            if WorkCheck is not None and ResourceIndex % 256 == 0:
                WorkCheck({
                    "Phase": "capacity-guide-history",
                    "PassIndex": PassIndex,
                    "ProcessedResources": ResourceIndex,
                    "OverflowResourceCount": len(Overflow),
                })
        # Remove the whole conflict neighborhood before reselecting.  Releasing
        # one guide at a time makes the first reselected net inherit the old
        # congestion pattern and can falsely look stagnated.
        for OffenderIndex, Signal in enumerate(Offenders):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "capacity-guide-ripup",
                    "PassIndex": PassIndex,
                    "CompletedSignals": OffenderIndex,
                    "TotalSignals": len(Offenders),
                })
            Layer, _Axis, _Lane, Guide = Selected.pop(Signal)
            for GuidePositionIndex, (X, Z) in enumerate(Guide, start=1):
                Usage[(Layer, X, Z)] -= 1
                if WorkCheck is not None and GuidePositionIndex % 256 == 0:
                    WorkCheck({
                        "Phase": "capacity-guide-ripup-cells",
                        "PassIndex": PassIndex,
                        "Signal": Signal,
                        "ProcessedGuidePositions": GuidePositionIndex,
                        "GuidePositionCount": len(Guide),
                    })
        Usage += Counter()
        for Signal in sorted(
            Offenders,
            key=lambda Value: (
                -Profiles[Value].Criticality,
                -Profiles[Value].Fanout,
                Value,
            ),
        ):
            SelectSignal(Signal)

    Overflow = BuildOverflow("capacity-guide-final-overflow")
    return CoarseGuidePlan(
        Guides={Signal: Value[3] for Signal, Value in Selected.items()},
        Layers={Signal: Value[0] for Signal, Value in Selected.items()},
        Axes={Signal: Value[1] for Signal, Value in Selected.items()},
        Lanes={Signal: Value[2] for Signal, Value in Selected.items()},
        Usage=dict(Usage),
        Overflow=Overflow,
        LocalSignals=LocalSignals,
        Iterations=tuple(Iterations),
        LocalInputFingerprintsBySignal={
            Signal: Domain.LocalInputFingerprint
            for Signal, Domain in OptionDomains.items()
        },
    )


def BuildCapacityAwareGuidePlan(
    Profiles: dict[str, Any],
    LayerCount: int,
    MinimumX: int,
    MinimumZ: int,
    Policy: GlobalRoutingPolicy,
    Technology: RedstoneRoutingTechnology,
    LocalFanoutDistance: int,
    ComponentObstacleBounds: tuple[int, int, int, int] | None = None,
    ComponentObstacleCellsByLayer: dict[
        int, frozenset[Position2]
    ] | None = None,
    ComponentObstacleExemptCellsBySignal: dict[
        str, dict[int, frozenset[Position2]]
    ] | None = None,
    ComponentOwnedSignals: frozenset[str] = frozenset(),
    ReservedGuideResourcesBySignal: dict[
        str, frozenset[tuple[int, int, int]]
    ] | None = None,
    SeedPlan: CoarseGuidePlan | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> CoarseGuidePlan:
    """Build signal-local options, then perform authoritative assignment."""
    OptionDomains = BuildCapacityAwareGuideOptionDomains(
        Profiles,
        LayerCount,
        MinimumX,
        MinimumZ,
        Policy,
        Technology,
        LocalFanoutDistance,
        ComponentObstacleBounds=ComponentObstacleBounds,
        ComponentObstacleCellsByLayer=ComponentObstacleCellsByLayer,
        ComponentObstacleExemptCellsBySignal=(
            ComponentObstacleExemptCellsBySignal
        ),
        ComponentOwnedSignals=ComponentOwnedSignals,
        WorkCheck=WorkCheck,
    )
    return AssignCapacityAwareGuideOptionDomains(
        Profiles,
        OptionDomains,
        LayerCount,
        Policy,
        LocalFanoutDistance,
        ComponentObstacleBounds=ComponentObstacleBounds,
        ComponentObstacleCellsByLayer=ComponentObstacleCellsByLayer,
        ComponentObstacleExemptCellsBySignal=(
            ComponentObstacleExemptCellsBySignal
        ),
        ComponentOwnedSignals=ComponentOwnedSignals,
        ReservedGuideResourcesBySignal=ReservedGuideResourcesBySignal,
        SeedPlan=SeedPlan,
        WorkCheck=WorkCheck,
    )


def MeasurePlacementRoutingFeedback(
    Placement: Any,
    RoutingSpacing: int,
    Policy: Any,
    Technology: RedstoneRoutingTechnology,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacementRoutingFeedback:
    """Score one legal placement using the same guide capacities as routing."""
    if WorkCheck is not None:
        WorkCheck({"Phase": "placement-feedback-start"})
    Placed = Placement.Placed
    Profiles = BuildNetRoutingProfiles(
        Placed,
        AccessLength=Policy.Placement.PinEscapeLength,
    )
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "placement-feedback-profiles",
            "ProfileCount": len(Profiles),
        })
    MinimumX = min(Gate.X for Gate in Placed.PlacedGates)
    MinimumZ = min(Gate.Z for Gate in Placed.PlacedGates)
    LayerCount = max(1, Placement.LayerCount)
    Plan = BuildCapacityAwareGuidePlan(
        Profiles,
        LayerCount,
        MinimumX,
        MinimumZ,
        Policy.GlobalRouting,
        Technology,
        Policy.Placement.LocalFanoutDistance,
        WorkCheck=WorkCheck,
    )
    PlacementMetrics = BuildPlacementSolution(
        Placed,
        Policy.Placement.LocalFanoutDistance,
        WorkCheck=WorkCheck,
    )
    LocalClaims = tuple(getattr(Placed, "LocalRouteClaims", ()) or ())
    BoundaryOverflow = sum(
        Cluster.BoundaryOverflow
        for Cluster in getattr(Placement, "PackedClusters", ())
    )
    BoundaryPinScarcity = sum(
        Cluster.PinScarcityCount
        for Cluster in getattr(Placement, "PackedClusters", ())
    )
    TotalTargets = 0
    CoveredTargets = 0
    DirectConnections = 0
    for ProfileIndex, Profile in enumerate(Profiles.values()):
        if WorkCheck is not None and ProfileIndex % 32 == 0:
            WorkCheck({
                "Phase": "placement-feedback-profile-metrics",
                "CompletedProfiles": ProfileIndex,
                "TotalProfiles": len(Profiles),
            })
        Unresolved = len(Profile.Targets)
        Covered = len(Profile.Seed.ConnectedTargets) if Profile.Seed is not None else 0
        TotalTargets += Unresolved + Covered
        CoveredTargets += Covered
        DirectConnections += Covered
        if Profile.Seed is not None and Covered > 0:
            DirectConnections += min(Unresolved, 1)
    RoutingDominanceProxy = (
        CoveredTargets / max(1, TotalTargets) if TotalTargets else 0.0
    )
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Placed.PlacedGates
    )
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Placed.PlacedGates
    )
    if WorkCheck is not None:
        WorkCheck({"Phase": "placement-feedback-complete"})
    return PlacementRoutingFeedback(
        RoutingSpacing=RoutingSpacing,
        BoundaryOverflow=BoundaryOverflow,
        PinScarcityCount=(
            PlacementMetrics.PinEscapeConflictCount + BoundaryPinScarcity
        ),
        GuideOverflowPeak=Plan.OverflowPeak,
        GuideOverflowCells=len(Plan.Overflow),
        PinEscapeConflictCount=PlacementMetrics.PinEscapeConflictCount,
        LocalClaimCoverageRatio=(
            CoveredTargets / max(1, TotalTargets)
            if TotalTargets
            else 0.0
        ),
        LocalRouteTargets=CoveredTargets,
        LocalDirectConnectionCount=DirectConnections,
        EstimatedGlobalExtensionNodes=(
            sum(len(Profile.Targets) for Profile in Profiles.values())
        ),
        EstimatedGlobalExtensionNets=len(Profiles),
        RoutingDominanceProxy=(
            RoutingDominanceProxy
        ),
        FrozenLocalNetCount=len(Placed.FrozenNetWires or {}),
        PreOwnedNodeCount=sum(
            len(Claim.Nodes) for Claim in (Placed.LocalRouteClaims or ())
        ),
        Hpwl=PlacementMetrics.Hpwl,
        LocalFanoutPenalty=PlacementMetrics.LocalFanoutPenalty,
        WeightedLocalityCost=(
            PlacementMetrics.Hpwl * Policy.Placement.HpwlWeight
            + PlacementMetrics.LocalFanoutPenalty
            * Policy.Placement.LocalFanoutWeight
        ),
        GateFootprint=(MaximumX - MinimumX + 1) * (MaximumZ - MinimumZ + 1),
    )

"""Explicit contracts and deterministic feedback for local-first routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import Counter
from math import ceil
from typing import Any

from ..Placement.Geometry import GetGateInputAccess
from ..Placement.Rotation import RotatedCellSize
from .ChannelPlanner import (
    BuildNetRoutingProfiles,
    CandidateLanes,
    RasterizeChannelSegment,
)
from .Policy import GlobalRoutingPolicy, PhysicalDesignPolicy
from .Technology import RedstoneRoutingTechnology

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
    Candidates = min(
        Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
        Adaptive.InitialCandidatesPerNet,
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


@dataclass(frozen=True)
class PlacementRoutingFeedback:
    RoutingSpacing: int
    GuideOverflowPeak: int
    GuideOverflowCells: int
    PinEscapeConflictCount: int
    FrozenLocalNetCount: int
    PreOwnedNodeCount: int
    Hpwl: int
    LocalFanoutPenalty: int
    WeightedLocalityCost: int
    GateFootprint: int

    @property
    def Score(self) -> tuple[int, ...]:
        return (
            self.GuideOverflowPeak,
            self.GuideOverflowCells,
            self.PinEscapeConflictCount,
            -self.FrozenLocalNetCount,
            -self.PreOwnedNodeCount,
            self.WeightedLocalityCost,
            self.GateFootprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        Value = asdict(self)
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


def _SignalEndpoints(Placed: Any) -> dict[str, tuple[Position2, ...]]:
    Values: dict[str, list[Position2]] = {}
    for Gate in Placed.PlacedGates:
        if Gate.OutputPin is not None:
            for Signal in Gate.Outputs:
                Values.setdefault(Signal, []).append(
                    (Gate.OutputPin[0], Gate.OutputPin[2])
                )
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, _Direction = GetGateInputAccess(Gate, InputIndex)
            Values.setdefault(Signal, []).append((Pin[0], Pin[2]))
    return {Signal: tuple(Endpoints) for Signal, Endpoints in Values.items()}


def BuildPlacementSolution(
    Placed: Any,
    LocalFanoutDistance: int,
) -> PlacementSolution:
    """Measure HPWL, locality, and pin-window overlap for a placed graph."""
    Endpoints = _SignalEndpoints(Placed)
    Hpwl = 0
    LocalFanoutPenalty = 0
    for Values in Endpoints.values():
        if len(Values) < 2:
            continue
        Span = (
            max(Value[0] for Value in Values) - min(Value[0] for Value in Values)
            + max(Value[1] for Value in Values) - min(Value[1] for Value in Values)
        )
        Hpwl += Span
        LocalFanoutPenalty += max(0, Span - LocalFanoutDistance)
    EscapeOwners: dict[Position2, set[str]] = {}
    for Gate in Placed.PlacedGates:
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            Column = (Pin[0] + Direction[0], Pin[2] + Direction[2])
            EscapeOwners.setdefault(Column, set()).add(Signal)
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
) -> frozenset[Position2]:
    Guide: set[Position2] = set()
    if Axis == "X":
        Minimum = min(X for X, _Z in Terminals)
        Maximum = max(X for X, _Z in Terminals)
        Guide.update(RasterizeChannelSegment((Minimum, Lane), (Maximum, Lane)))
        for X, Z in Terminals:
            Guide.update(RasterizeChannelSegment((X, Z), (X, Lane)))
    else:
        Minimum = min(Z for _X, Z in Terminals)
        Maximum = max(Z for _X, Z in Terminals)
        Guide.update(RasterizeChannelSegment((Lane, Minimum), (Lane, Maximum)))
        for X, Z in Terminals:
            Guide.update(RasterizeChannelSegment((X, Z), (Lane, Z)))
    return frozenset(Guide)


def BuildCapacityAwareGuidePlan(
    Profiles: dict[str, Any],
    LayerCount: int,
    MinimumX: int,
    MinimumZ: int,
    Policy: GlobalRoutingPolicy,
    Technology: RedstoneRoutingTechnology,
    LocalFanoutDistance: int,
) -> CoarseGuidePlan:
    """Allocate deterministic coarse guides, then rip up overflow offenders."""
    if LayerCount < 1:
        raise ValueError("LayerCount must be positive")
    Options: dict[str, list[tuple[int, int, int, int, str, int, frozenset[Position2]]]] = {}
    LocalSignals = frozenset(
        Signal
        for Signal, Profile in Profiles.items()
        if Profile.Span <= LocalFanoutDistance and Profile.Fanout <= 2
    )
    for Signal, Profile in Profiles.items():
        Terminals = tuple(
            (Path[-1][0], Path[-1][2])
            for Path in (
                Profile.SourceAccessPath,
                *Profile.TargetAccessPaths.values(),
            )
        )
        Values = []
        XSpan = max(X for X, _Z in Terminals) - min(X for X, _Z in Terminals)
        ZSpan = max(Z for _X, Z in Terminals) - min(Z for _X, Z in Terminals)
        PreferredAxis = "X" if XSpan >= ZSpan else "Z"
        for Axis in ("X", "Z"):
            Coordinates = sorted(
                Z for _X, Z in Terminals
            ) if Axis == "X" else sorted(X for X, _Z in Terminals)
            Center = Coordinates[len(Coordinates) // 2]
            Anchor = MinimumZ if Axis == "X" else MinimumX
            Aligned = Anchor + (
                (Center - Anchor + Technology.TrackPitch // 2)
                // Technology.TrackPitch
            ) * Technology.TrackPitch
            LaneCount = min(Policy.CandidateLaneCount, 5)
            for Lane in CandidateLanes(Aligned, LaneCount, Technology.TrackPitch):
                Guide = _BuildRectilinearGuide(Terminals, Axis, Lane)
                Escape = max(0, abs(Lane - Center) - (
                    Policy.IntraClusterEnvelope
                    if Signal in LocalSignals
                    else Policy.SharedBoundaryEnvelope
                ))
                for Layer in range(LayerCount):
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
        Options[Signal] = sorted(Values)

    Selected: dict[str, tuple[int, str, int, frozenset[Position2]]] = {}
    Usage: Counter[tuple[int, int, int]] = Counter()
    History: Counter[tuple[int, int, int]] = Counter()

    def SelectSignal(Signal: str) -> None:
        Best = None
        for Escape, Length, Layer, AxisBias, Axis, Lane, Guide in Options[Signal]:
            OverflowCost = sum(
                max(0, Usage[(Layer, X, Z)] + 1 - Policy.CorridorCapacity)
                for X, Z in Guide
            )
            ColumnOverflowCost = sum(
                max(
                    0,
                    sum(
                        Usage[(ExistingLayer, X, Z)]
                        for ExistingLayer in range(LayerCount)
                    ) + 1 - 2,
                )
                for X, Z in Guide
            )
            HistoryCost = sum(History[(Layer, X, Z)] for X, Z in Guide)
            Cost = (
                (OverflowCost + ColumnOverflowCost) * Policy.OverflowPenalty,
                Escape,
                HistoryCost,
                Length,
                Layer,
                AxisBias,
                Lane,
            )
            if Best is None or Cost < Best[0]:
                Best = (Cost, Layer, Axis, Lane, Guide)
        assert Best is not None
        _Cost, Layer, Axis, Lane, Guide = Best
        Selected[Signal] = (Layer, Axis, Lane, Guide)
        Usage.update((Layer, X, Z) for X, Z in Guide)

    Order = sorted(
        Profiles,
        key=lambda Signal: (
            -Profiles[Signal].Criticality,
            -Profiles[Signal].Fanout,
            Signal,
        ),
    )
    for Signal in Order:
        SelectSignal(Signal)

    Iterations = []
    PreviousOverflow = None
    for PassIndex in range(Policy.MaximumRipupPasses + 1):
        Overflow = {
            Resource: Count - Policy.CorridorCapacity
            for Resource, Count in Usage.items()
            if Count > Policy.CorridorCapacity
        }
        Contributors = Counter(
            Signal
            for Signal, (Layer, _Axis, _Lane, Guide) in Selected.items()
            for X, Z in Guide
            if (Layer, X, Z) in Overflow
        )
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
        History.update(Overflow)
        # Remove the whole conflict neighborhood before reselecting.  Releasing
        # one guide at a time makes the first reselected net inherit the old
        # congestion pattern and can falsely look stagnated.
        for Signal in Offenders:
            Layer, _Axis, _Lane, Guide = Selected.pop(Signal)
            Usage.subtract((Layer, X, Z) for X, Z in Guide)
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

    Overflow = {
        Resource: Count - Policy.CorridorCapacity
        for Resource, Count in Usage.items()
        if Count > Policy.CorridorCapacity
    }
    return CoarseGuidePlan(
        Guides={Signal: Value[3] for Signal, Value in Selected.items()},
        Layers={Signal: Value[0] for Signal, Value in Selected.items()},
        Axes={Signal: Value[1] for Signal, Value in Selected.items()},
        Lanes={Signal: Value[2] for Signal, Value in Selected.items()},
        Usage=dict(Usage),
        Overflow=Overflow,
        LocalSignals=LocalSignals,
        Iterations=tuple(Iterations),
    )


def MeasurePlacementRoutingFeedback(
    Placement: Any,
    RoutingSpacing: int,
    Policy: Any,
    Technology: RedstoneRoutingTechnology,
) -> PlacementRoutingFeedback:
    """Score one legal placement using the same guide capacities as routing."""
    Placed = Placement.Placed
    Profiles = BuildNetRoutingProfiles(
        Placed,
        AccessLength=Policy.Placement.PinEscapeLength,
    )
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
    )
    PlacementMetrics = BuildPlacementSolution(
        Placed,
        Policy.Placement.LocalFanoutDistance,
    )
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Placed.PlacedGates
    )
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Placed.PlacedGates
    )
    return PlacementRoutingFeedback(
        RoutingSpacing=RoutingSpacing,
        GuideOverflowPeak=Plan.OverflowPeak,
        GuideOverflowCells=len(Plan.Overflow),
        PinEscapeConflictCount=PlacementMetrics.PinEscapeConflictCount,
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

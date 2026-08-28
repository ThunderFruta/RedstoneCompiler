"""Boundary demand, routing-cost estimation, and placement guides."""

from __future__ import annotations

from math import (
    ceil,
    sqrt,
)
from typing import (
    Any,
    Callable,
)
from Compiler.Placement.Rotation import (
    RotatedCellSize,
)
from Compiler.Placement.Geometry import (
    GetGateInputAccess,
    PlacedDesign,
)
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
)
from .Channels import (
    InterClusterBoundaryDemand,
    InterClusterGapPlan,
)
from .Clusters import (
    PcbPlacement,
)


def BuildInterClusterBoundaryDemand(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Assignment: dict[int, tuple[int, int]],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[InterClusterBoundaryDemand, ...]:
    """Derive topology-only lane demand across final cluster-grid cuts.

    A signal is counted once per crossed X/Z boundary even when it has several
    consumers beyond that cut.  This lets placement reserve one physical lane
    for a shared routed tree instead of scaling spacing with fanout.
    """
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerClusterBySignal = {
        Signal: ClusterByGate[Gate.Name]
        for Gate in Module.Gates
        if Gate.Name in ClusterByGate
        for Signal in Gate.Outputs
    }
    SignalsByBoundary: dict[tuple[str, int], set[str]] = {}
    for GateIndex, Gate in enumerate(Module.Gates):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "inter-cluster-boundary-demand",
                "CompletedGates": GateIndex,
                "TotalGates": len(Module.Gates),
                "GateName": Gate.Name,
            })
        TargetCluster = ClusterByGate.get(Gate.Name)
        if TargetCluster is None:
            continue
        TargetSlot = Assignment.get(TargetCluster)
        if TargetSlot is None:
            continue
        for Signal in Gate.Inputs:
            SourceCluster = ProducerClusterBySignal.get(Signal)
            if SourceCluster is None or SourceCluster == TargetCluster:
                continue
            SourceSlot = Assignment.get(SourceCluster)
            if SourceSlot is None:
                continue
            SourceColumn, SourceRow = SourceSlot
            TargetColumn, TargetRow = TargetSlot
            for BoundaryIndex in range(
                min(SourceColumn, TargetColumn),
                max(SourceColumn, TargetColumn),
            ):
                SignalsByBoundary.setdefault(("X", BoundaryIndex), set()).add(
                    Signal
                )
            for BoundaryIndex in range(
                min(SourceRow, TargetRow),
                max(SourceRow, TargetRow),
            ):
                SignalsByBoundary.setdefault(("Z", BoundaryIndex), set()).add(
                    Signal
                )
    return tuple(
        InterClusterBoundaryDemand(
            Axis=Axis,
            BoundaryIndex=BoundaryIndex,
            Signals=tuple(sorted(Signals)),
        )
        for (Axis, BoundaryIndex), Signals in sorted(SignalsByBoundary.items())
    )

def BuildInterClusterGapPlan(
    BoundaryDemand: tuple[InterClusterBoundaryDemand, ...],
    ColumnCount: int,
    RowCount: int,
    RoutingSpacing: int,
    TrackPitch: int,
    Enabled: bool,
) -> InterClusterGapPlan:
    """Allocate optional spacing without exceeding the configured corridor.

    The fixed placement gap remains outside this plan.  When disabled every
    boundary retains the uniform configured spacing, which exactly preserves
    the previous coordinate construction.
    """
    if RoutingSpacing < 0:
        raise ValueError("RoutingSpacing cannot be negative")
    if TrackPitch < 1:
        raise ValueError("TrackPitch must be positive")
    LanesByBoundary = {
        (Record.Axis, Record.BoundaryIndex): Record.RequiredCorridorLanes
        for Record in BoundaryDemand
    }

    def OptionalSpacing(Axis: str, BoundaryIndex: int) -> int:
        if not Enabled:
            return RoutingSpacing
        return min(
            RoutingSpacing,
            LanesByBoundary.get((Axis, BoundaryIndex), 0) * TrackPitch,
        )

    return InterClusterGapPlan(
        Enabled=Enabled,
        RoutingSpacing=RoutingSpacing,
        TrackPitch=TrackPitch,
        ColumnExtraSpacing=tuple(
            (BoundaryIndex, OptionalSpacing("X", BoundaryIndex))
            for BoundaryIndex in range(max(0, ColumnCount - 1))
        ),
        RowExtraSpacing=tuple(
            (BoundaryIndex, OptionalSpacing("Z", BoundaryIndex))
            for BoundaryIndex in range(max(0, RowCount - 1))
        ),
        BoundaryDemand=BoundaryDemand,
    )

def PlacementWireCost(Placed: PlacedDesign) -> int:
    """Return weighted center-to-center wire length."""
    Producers = {
        Signal: Gate
        for Gate in Placed.PlacedGates
        for Signal in Gate.Outputs
    }
    Fanout: dict[str, int] = {}
    for Gate in Placed.PlacedGates:
        for Signal in Gate.Inputs:
            Fanout[Signal] = Fanout.get(Signal, 0) + 1
    Cost = 0
    for Gate in Placed.PlacedGates:
        TargetWidth, TargetDepth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        TargetCenter = (
            Gate.X + TargetWidth / 2,
            Gate.Z + TargetDepth / 2,
        )
        for Signal in Gate.Inputs:
            Producer = Producers.get(Signal)
            if Producer is None:
                continue
            SourceWidth, SourceDepth = RotatedCellSize(
                Producer.Kind,
                Producer.Rotation,
            )
            SourceCenter = (
                Producer.X + SourceWidth / 2,
                Producer.Z + SourceDepth / 2,
            )
            Cost += max(1, Fanout.get(Signal, 1)) * round(
                abs(SourceCenter[0] - TargetCenter[0])
                + abs(SourceCenter[1] - TargetCenter[1])
            )
    return Cost

def EstimatePlacementRoutingCost(
    Placed: PlacedDesign,
    MaximumLayerCount: int = 4,
) -> tuple[int, int, int]:
    """Sketch cheap multilayer routes and estimate blockage and congestion."""
    Footprints: set[tuple[int, int]] = set()
    for Gate in Placed.PlacedGates:
        Width, Depth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        Footprints.update(
            (X, Z)
            for X in range(Gate.X, Gate.X + Width)
            for Z in range(Gate.Z, Gate.Z + Depth)
        )

    Producers: dict[str, tuple[int, int]] = {}
    Targets: dict[str, list[tuple[int, int]]] = {}
    for Gate in Placed.PlacedGates:
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            PinX, _PinY, PinZ = Gate.OutputPin
            DirectionX, _DirectionY, DirectionZ = Gate.OutputDirection
            Endpoint = (
                PinX + DirectionX * 2,
                PinZ + DirectionZ * 2,
            )
            for Signal in Gate.Outputs:
                Producers[Signal] = Endpoint
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            PinX, _PinY, PinZ = Pin
            DirectionX, _DirectionY, DirectionZ = Direction
            Targets.setdefault(Signal, []).append(
                (
                    PinX + DirectionX * 2,
                    PinZ + DirectionZ * 2,
                )
            )

    Signals = [Signal for Signal in Producers if Targets.get(Signal)]
    if not Signals:
        return (0, 0, 0)
    LayerCount = min(
        MaximumLayerCount,
        max(2, ceil(sqrt(len(Signals)))),
    )
    Occupied = [set() for _Layer in range(LayerCount)]
    ObstaclePressure = 0
    CongestionPressure = 0
    RouteLength = 0
    OrderedSignals = sorted(
        Signals,
        key=lambda Signal: (
            -len(Targets[Signal]),
            -max(
                abs(Producers[Signal][0] - Target[0])
                + abs(Producers[Signal][1] - Target[1])
                for Target in Targets[Signal]
            ),
            Signal,
        ),
    )
    for Signal in OrderedSignals:
        Options = []
        for Layer in range(LayerCount):
            for XFirst in (True, False):
                Guide = BuildSignalGuide(
                    Producers[Signal],
                    Targets[Signal],
                    XFirst,
                )
                ObstacleHits = sum(
                    Position in Footprints
                    for Position in Guide
                )
                Congestion = sum(
                    4 * (Position in Occupied[Layer])
                    + sum(
                        Neighbor in Occupied[Layer]
                        for Neighbor in (
                            (Position[0] + 1, Position[1]),
                            (Position[0] - 1, Position[1]),
                            (Position[0], Position[1] + 1),
                            (Position[0], Position[1] - 1),
                        )
                    )
                    for Position in Guide
                )
                VerticalLength = Layer * 2 * (1 + len(Targets[Signal]))
                Options.append(
                    (
                        ObstacleHits * (LayerCount - Layer),
                        Congestion,
                        len(Guide) + VerticalLength,
                        Layer,
                        Guide,
                    )
                )
        ObstacleCost, Congestion, Length, Layer, Guide = min(Options)
        ObstaclePressure += ObstacleCost
        CongestionPressure += Congestion
        RouteLength += Length
        Occupied[Layer].update(Guide)
    return ObstaclePressure, CongestionPressure, RouteLength

def PlacementCompactKey(
    Placed: PlacedDesign,
) -> tuple[int, int, int, int, int, int]:
    """Score legal placement by routability before occupied bounds."""
    if not Placed.PlacedGates:
        return (0, 0, 0, 0, 0, 0)
    MinimumX = min(Gate.X for Gate in Placed.PlacedGates)
    MinimumZ = min(Gate.Z for Gate in Placed.PlacedGates)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
        for Gate in Placed.PlacedGates
    )
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
        for Gate in Placed.PlacedGates
    )
    Width = MaximumX - MinimumX
    Depth = MaximumZ - MinimumZ
    WireCost = PlacementWireCost(Placed)
    ObstaclePressure, CongestionPressure, RouteLength = (
        EstimatePlacementRoutingCost(Placed)
    )
    Footprint = Width * Depth
    return (
        ObstaclePressure,
        CongestionPressure * 4 + RouteLength,
        Footprint,
        max(Width, Depth),
        Width + Depth,
        WireCost,
    )

def AddGuideLine(
    Values: set[tuple[int, int]],
    Start: tuple[int, int],
    End: tuple[int, int],
) -> None:
    """Rasterize one orthogonal guide segment."""
    if Start[0] == End[0]:
        for Z in range(min(Start[1], End[1]), max(Start[1], End[1]) + 1):
            Values.add((Start[0], Z))
        return
    for X in range(min(Start[0], End[0]), max(Start[0], End[0]) + 1):
        Values.add((X, Start[1]))

def BuildSignalGuide(
    Source: tuple[int, int],
    Targets: list[tuple[int, int]],
    XFirst: bool,
) -> frozenset[tuple[int, int]]:
    """Build a rectilinear fanout tree biased to one preferred direction."""
    Guide = {Source}
    Remaining = list(Targets)
    while Remaining:
        Target = min(
            Remaining,
            key=lambda Value: min(
                abs(Value[0] - Existing[0]) + abs(Value[1] - Existing[1])
                for Existing in Guide
            ),
        )
        Anchor = min(
            Guide,
            key=lambda Value: (
                abs(Target[0] - Value[0]) + abs(Target[1] - Value[1]),
                Value,
            ),
        )
        Corner = (
            (Target[0], Anchor[1])
            if XFirst
            else (Anchor[0], Target[1])
        )
        AddGuideLine(Guide, Anchor, Corner)
        AddGuideLine(Guide, Corner, Target)
        Remaining.remove(Target)
    return frozenset(Guide)

def AddPcbRoutingGuides(
    Placed: PlacedDesign,
    MaximumLayerCount: int = 0,
) -> PcbPlacement:
    """Attach deterministic routing metadata without performing route planning."""
    Signals = {
        Signal
        for Gate in Placed.PlacedGates
        if Gate.OutputPin is not None
        for Signal in Gate.Outputs
    } & {
        Signal
        for Gate in Placed.PlacedGates
        for Signal in Gate.Inputs
    }
    # A capped three-layer guide plane is sufficient for small circuits, but
    # cannot assign disjoint portal ownership for the 64+ signal arithmetic
    # region. Let only that scale use the technology's full routing ladder.
    EffectiveMaximumLayerCount = (
        DefaultRedstoneRoutingTechnology.MaximumRoutableLayerCount
        if (
            MaximumLayerCount > 0
            and len(Signals) >= 64
        )
        else MaximumLayerCount
    )
    LayerCount = min(
        (
            EffectiveMaximumLayerCount
            if EffectiveMaximumLayerCount > 0
            else DefaultRedstoneRoutingTechnology.MaximumRoutableLayerCount
        ),
        max(
            # An explicitly selected pre-route envelope is a proof-backed
            # routing contract.  Preserve the legacy technology floor only
            # when no finite layer envelope was requested.
            (
                1
                if MaximumLayerCount > 0
                else DefaultRedstoneRoutingTechnology.MinimumRoutingLayerCount
            ),
            ceil(sqrt(max(1, len(Signals)))),
        ),
    )
    Guided = PlacedDesign(
        Module=Placed.Module,
        PlacedGates=Placed.PlacedGates,
        RouteGuides={},
        RouteLayers={},
        FrozenNetWires=Placed.FrozenNetWires,
        LocalNetBranches=Placed.LocalNetBranches,
        LocalNetTargets=Placed.LocalNetTargets,
        LocalRouteClaims=Placed.LocalRouteClaims,
        LocalRouteDiagnostics=Placed.LocalRouteDiagnostics,
        DerivedPerimeterSlotDomain=Placed.DerivedPerimeterSlotDomain,
        DerivedPerimeterSlotAssignment=(
            Placed.DerivedPerimeterSlotAssignment
        ),
    )
    return PcbPlacement(
        Placed=Guided,
        Clusters=(),
        SignalOrder=tuple(sorted(Signals)),
        LayerCount=LayerCount,
        DerivedPerimeterSlotDomain=Guided.DerivedPerimeterSlotDomain,
        DerivedPerimeterSlotAssignment=(
            Guided.DerivedPerimeterSlotAssignment
        ),
    )

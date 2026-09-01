"""Physical connectivity, isolation, and route-tree cleanup actions."""

from __future__ import annotations

from collections import Counter, deque
from typing import Any, Callable

from ..Contracts.Core import Position3
from .Geometry import NeighborPositions


RoutingWorkCheck = Callable[[dict[str, object]], None]


def FindFlatRouteConflicts(
    NetWires: dict[str, set[Position3]],
    WorkCheck: RoutingWorkCheck | None = None,
) -> tuple[set[Position3], Counter[str]]:
    """Return electrical, support, and stair-headroom resource conflicts."""
    ConflictCells, ConflictCounts, _, _ = AnalyzeFlatRouteConflicts(
        NetWires,
        WorkCheck=WorkCheck,
    )
    return ConflictCells, ConflictCounts


def AnalyzeFlatRouteConflicts(
    NetWires: dict[str, set[Position3]],
    WorkCheck: RoutingWorkCheck | None = None,
) -> tuple[
    set[Position3],
    Counter[str],
    set[str],
    Counter[tuple[str, str]],
]:
    """Return conflicts plus ownership details used for selective rip-up."""
    ConflictCells: set[Position3] = set()
    ConflictCounts: Counter[str] = Counter()
    SelfConflictSignals: set[str] = set()
    ConflictPairs: Counter[tuple[str, str]] = Counter()
    Signals = list(NetWires)
    Exclusions: dict[str, set[Position3]] = {}
    Supports: dict[str, set[Position3]] = {}
    Headroom: dict[str, set[Position3]] = {}
    if WorkCheck is not None:
        WorkCheck({"Phase": "start", "SignalCount": len(Signals)})
    ProcessedPositions = 0
    for SignalIndex, (Signal, Positions) in enumerate(
        NetWires.items(),
        start=1,
    ):
        Exclusion = set(Positions)
        SignalSupports: set[Position3] = set()
        RequiredAir: set[Position3] = set()
        for Position in Positions:
            ProcessedPositions += 1
            if WorkCheck is not None and ProcessedPositions % 256 == 0:
                WorkCheck({
                    "Phase": "resource-claims",
                    "Signal": Signal,
                    "CompletedSignals": SignalIndex - 1,
                    "ProcessedPositions": ProcessedPositions,
                })
            X, Y, Z = Position
            SignalSupports.add((X, Y - 1, Z))
            Exclusion.update(NeighborPositions(Position))
            for Neighbor in NeighborPositions(Position):
                if Neighbor not in Positions or Neighbor[1] == Position[1]:
                    continue
                Lower = Position if Position[1] < Neighbor[1] else Neighbor
                RequiredAir.add((Lower[0], Lower[1] + 1, Lower[2]))
        Exclusions[Signal] = Exclusion
        Supports[Signal] = SignalSupports
        Headroom[Signal] = RequiredAir

        SelfConflicts = Positions & (SignalSupports | RequiredAir)
        if SelfConflicts:
            ConflictCells.update(SelfConflicts)
            ConflictCounts[Signal] += len(SelfConflicts)
            SelfConflictSignals.add(Signal)
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "signal-resources-complete",
                "Signal": Signal,
                "CompletedSignals": SignalIndex,
                "ProcessedPositions": ProcessedPositions,
            })

    SignalPairChecks = 0
    for Index, FirstSignal in enumerate(Signals):
        FirstPositions = NetWires[FirstSignal]
        for SecondSignal in Signals[Index + 1 :]:
            SignalPairChecks += 1
            if WorkCheck is not None and SignalPairChecks % 64 == 0:
                WorkCheck({
                    "Phase": "signal-pairs",
                    "FirstSignal": FirstSignal,
                    "SecondSignal": SecondSignal,
                    "SignalPairChecks": SignalPairChecks,
                })
            SecondPositions = NetWires[SecondSignal]
            FirstConflicts = FirstPositions & Exclusions[SecondSignal]
            SecondConflicts = SecondPositions & Exclusions[FirstSignal]
            FirstResourceConflicts = FirstPositions & (
                Supports[SecondSignal] | Headroom[SecondSignal]
            )
            SecondResourceConflicts = SecondPositions & (
                Supports[FirstSignal] | Headroom[FirstSignal]
            )
            FirstHeadroomConflicts = (
                Headroom[FirstSignal] & Supports[SecondSignal]
            )
            SecondHeadroomConflicts = (
                Headroom[SecondSignal] & Supports[FirstSignal]
            )
            ResourceConflicts = (
                FirstResourceConflicts
                | SecondResourceConflicts
                | FirstHeadroomConflicts
                | SecondHeadroomConflicts
            )
            if (
                not FirstConflicts
                and not SecondConflicts
                and not ResourceConflicts
            ):
                continue
            ConflictCells.update(FirstConflicts)
            ConflictCells.update(SecondConflicts)
            ConflictCells.update(ResourceConflicts)
            ConflictCount = (
                len(FirstConflicts)
                + len(SecondConflicts)
                + len(ResourceConflicts)
            )
            ConflictCounts[FirstSignal] += ConflictCount
            ConflictCounts[SecondSignal] += ConflictCount
            ConflictPairs[(FirstSignal, SecondSignal)] += ConflictCount

    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "ProcessedPositions": ProcessedPositions,
            "SignalPairChecks": SignalPairChecks,
            "ConflictCellCount": len(ConflictCells),
        })
    return ConflictCells, ConflictCounts, SelfConflictSignals, ConflictPairs


def BuildPhysicalGraphs(
    NetWires: dict[str, set[Position3]],
    ActualBlocks: set[Position3],
    Supports: set[Position3],
    SolidBlocks: set[Position3] | frozenset[Position3] | None = None,
    WorkCheck: RoutingWorkCheck | None = None,
) -> dict[str, dict[Position3, list[Position3]]]:
    """Build net graphs using only connections Minecraft can physically make."""
    if WorkCheck is not None:
        WorkCheck({"Phase": "start", "SignalCount": len(NetWires)})
    AllWires: set[Position3] = set()
    CollectedWireCount = 0
    for SignalIndex, (Signal, Cells) in enumerate(NetWires.items(), start=1):
        for Cell in Cells:
            AllWires.add(Cell)
            CollectedWireCount += 1
            if WorkCheck is not None and CollectedWireCount % 256 == 0:
                WorkCheck({
                    "Phase": "collect-wire-positions",
                    "Signal": Signal,
                    "CompletedSignals": SignalIndex - 1,
                    "ProcessedPositions": CollectedWireCount,
                })
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "collect-wires",
                "Signal": Signal,
                "CompletedSignals": SignalIndex,
                "WireCount": len(AllWires),
            })
    SolidBlocks = ActualBlocks if SolidBlocks is None else SolidBlocks

    def IsPhysicalEdge(First: Position3, Second: Position3) -> bool:
        DeltaX = abs(First[0] - Second[0])
        DeltaY = abs(First[1] - Second[1])
        DeltaZ = abs(First[2] - Second[2])
        if DeltaY == 0:
            return DeltaX + DeltaZ == 1
        if DeltaY != 1 or DeltaX + DeltaZ != 1:
            return False

        Lower, Upper = (
            (First, Second) if First[1] < Second[1] else (Second, First)
        )
        Support = (Upper[0], Upper[1] - 1, Upper[2])
        Headroom = (Lower[0], Lower[1] + 1, Lower[2])
        SupportIsSolid = (
            Support in Supports or Support in SolidBlocks
        ) and Support not in AllWires
        HeadroomIsClear = (
            Headroom not in Supports
            and Headroom not in SolidBlocks
            and Headroom not in AllWires
        )
        return SupportIsSolid and HeadroomIsClear

    Graphs: dict[str, dict[Position3, list[Position3]]] = {}
    ProcessedCells = 0
    for SignalIndex, (Signal, Cells) in enumerate(NetWires.items(), start=1):
        Graph: dict[Position3, list[Position3]] = {}
        for Cell in Cells:
            ProcessedCells += 1
            if WorkCheck is not None and ProcessedCells % 256 == 0:
                WorkCheck({
                    "Phase": "cells",
                    "Signal": Signal,
                    "CompletedSignals": SignalIndex - 1,
                    "ProcessedCells": ProcessedCells,
                })
            Graph[Cell] = [
                Neighbor
                for Neighbor in NeighborPositions(Cell)
                if Neighbor in Cells and IsPhysicalEdge(Cell, Neighbor)
            ]
        Graphs[Signal] = Graph
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "signal-complete",
                "Signal": Signal,
                "CompletedSignals": SignalIndex,
                "ProcessedCells": ProcessedCells,
            })
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "CompletedSignals": len(Graphs),
            "ProcessedCells": ProcessedCells,
        })
    return Graphs


def ValidatePhysicalRoutes(
    PhysicalGraphs: dict[str, dict[Position3, list[Position3]]],
    Producers: dict[str, Any],
    Targets: dict[str, list[Position3]],
    WorkCheck: RoutingWorkCheck | None = None,
) -> None:
    """Reject a compact route if any sink is not physically powered."""
    if WorkCheck is not None:
        WorkCheck({"Phase": "start", "SignalCount": len(PhysicalGraphs)})
    ExpandedNodes = 0
    for SignalIndex, (Signal, Graph) in enumerate(
        PhysicalGraphs.items(),
        start=1,
    ):
        Producer = Producers.get(Signal)
        if Producer is None:
            raise ValueError(f"missing source gate for routed signal {Signal}")
        if Producer.OutputPin is None:
            raise ValueError(f"routed signal has no source output pin: {Signal}")
        Root = Producer.OutputPin
        Seen = {Root}
        Queue = deque([Root])
        while Queue:
            Cell = Queue.popleft()
            ExpandedNodes += 1
            if WorkCheck is not None and ExpandedNodes % 256 == 0:
                WorkCheck({
                    "Phase": "connectivity",
                    "Signal": Signal,
                    "CompletedSignals": SignalIndex - 1,
                    "ExpandedNodes": ExpandedNodes,
                })
            # A projected physical-component port can be the logical root of
            # an otherwise empty or disconnected candidate.  Treat that as a
            # connectivity failure with the signal identity preserved; a raw
            # graph lookup here used to escape as an untyped Position3
            # KeyError and hid the failed assembly-plan stage.
            for Neighbor in Graph.get(Cell, ()):
                if Neighbor not in Seen:
                    Seen.add(Neighbor)
                    Queue.append(Neighbor)
        Missing = [Target for Target in Targets[Signal] if Target not in Seen]
        if Missing:
            raise ValueError(
                f"Physically disconnected route for net {Signal}: {Missing}"
            )
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "signal-complete",
                "Signal": Signal,
                "CompletedSignals": SignalIndex,
                "ExpandedNodes": ExpandedNodes,
            })
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "CompletedSignals": len(PhysicalGraphs),
            "ExpandedNodes": ExpandedNodes,
        })


def ValidateTemplateIsolation(
    NetWires: dict[str, set[Position3]],
    ActualBlocks: set[Position3] | frozenset[Position3],
    ElectricalBlocks: set[Position3] | frozenset[Position3],
    SolidBlocks: set[Position3] | frozenset[Position3],
    Producers: dict[str, Any],
    Targets: dict[str, list[Position3]],
    AccessBySignal: dict[str, set[Position3]] | None = None,
    TemplateKeepOutBlocks: set[Position3] | frozenset[Position3] | None = None,
    WorkCheck: RoutingWorkCheck | None = None,
) -> None:
    """Reject routed dust that enters or side-powers a cell template."""
    TemplateElectrical = set(ElectricalBlocks) | set(SolidBlocks)
    ExplicitKeepOut = set(TemplateKeepOutBlocks or ())
    TemplateKeepOut = set(TemplateElectrical) | ExplicitKeepOut
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "start",
            "SignalCount": len(NetWires),
            "TemplateElectricalCount": len(TemplateElectrical),
        })
    for PositionIndex, Position in enumerate(TemplateElectrical, start=1):
        TemplateKeepOut.update(NeighborPositions(Position))
        if WorkCheck is not None and PositionIndex % 256 == 0:
            WorkCheck({
                "Phase": "template-keepout",
                "ProcessedPositions": PositionIndex,
                "TemplateElectricalCount": len(TemplateElectrical),
            })
    ProcessedRoutePositions = 0
    for SignalIndex, (Signal, Positions) in enumerate(
        NetWires.items(),
        start=1,
    ):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "signal",
                "Signal": Signal,
                "CompletedSignals": SignalIndex - 1,
                "RoutePositionCount": len(Positions),
            })
        Producer = Producers.get(Signal)
        if Producer is None:
            raise ValueError(f"missing source gate for routed signal {Signal}")
        if Producer.OutputPin is None:
            raise ValueError(f"routed signal has no source output pin: {Signal}")
        AllowedPins = {Producer.OutputPin, *Targets[Signal]}
        if AccessBySignal is not None:
            AllowedPins.update(AccessBySignal.get(Signal, set()))
        Overlaps: set[Position3] = set()
        SidePowering: set[Position3] = set()
        for Position in Positions:
            ProcessedRoutePositions += 1
            if (
                WorkCheck is not None
                and ProcessedRoutePositions % 256 == 0
            ):
                WorkCheck({
                    "Phase": "route-positions",
                    "Signal": Signal,
                    "CompletedSignals": SignalIndex - 1,
                    "ProcessedPositions": ProcessedRoutePositions,
                })
            if Position in AllowedPins and Position not in ExplicitKeepOut:
                continue
            if Position in ActualBlocks:
                Overlaps.add(Position)
            if Position in TemplateKeepOut:
                SidePowering.add(Position)
        if Overlaps:
            raise ValueError(
                f"Route for {Signal} overlaps template blocks: "
                f"{sorted(Overlaps)[:8]}"
            )
        if SidePowering:
            raise ValueError(
                f"Route for {Signal} enters template electrical clearance: "
                f"{sorted(SidePowering)[:8]}"
            )
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "CompletedSignals": len(NetWires),
            "ProcessedRoutePositions": ProcessedRoutePositions,
            "TemplateKeepOutCount": len(TemplateKeepOut),
        })


def SimplifyNetTrees(
    NetWires: dict[str, set[Position3]],
    ActualBlocks: set[Position3],
    Producers: dict[str, Any],
    Targets: dict[str, list[Position3]],
    SolidBlocks: set[Position3] | frozenset[Position3] | None = None,
) -> tuple[dict[str, set[Position3]], set[Position3]]:
    """Remove cycles, dead branches, and dust not on a shortest sink path."""
    Supports = {
        (X, Y - 1, Z)
        for Cells in NetWires.values()
        for X, Y, Z in Cells
    }
    Graphs = BuildPhysicalGraphs(NetWires, ActualBlocks, Supports, SolidBlocks)
    Simplified: dict[str, set[Position3]] = {}

    for Signal, Graph in Graphs.items():
        Producer = Producers.get(Signal)
        if Producer is None:
            raise ValueError(f"missing source gate for routed signal {Signal}")
        if Producer.OutputPin is None:
            raise ValueError(f"routed signal has no source output pin: {Signal}")
        Root = Producer.OutputPin
        Parents = {Root: None}
        Queue = deque([Root])
        while Queue:
            Cell = Queue.popleft()
            for Neighbor in Graph[Cell]:
                if Neighbor in Parents:
                    continue
                Parents[Neighbor] = Cell
                Queue.append(Neighbor)

        Required = {Root}
        for Target in Targets[Signal]:
            if Target not in Parents:
                raise ValueError(
                    f"Cannot simplify disconnected route for net {Signal}"
                )
            Cell = Target
            while Cell is not None:
                Required.add(Cell)
                Cell = Parents[Cell]
        Simplified[Signal] = Required

    SimplifiedSupports = {
        (X, Y - 1, Z)
        for Cells in Simplified.values()
        for X, Y, Z in Cells
    }
    return Simplified, SimplifiedSupports

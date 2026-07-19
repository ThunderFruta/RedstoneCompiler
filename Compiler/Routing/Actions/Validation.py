"""Physical connectivity, isolation, and route-tree cleanup actions."""

from __future__ import annotations

from collections import deque
from typing import Any

from ..Models import Position3
from .Geometry import BuildElectricalExclusions, NeighborPositions


def BuildPhysicalGraphs(
    NetWires: dict[str, set[Position3]],
    ActualBlocks: set[Position3],
    Supports: set[Position3],
    SolidBlocks: set[Position3] | frozenset[Position3] | None = None,
) -> dict[str, dict[Position3, list[Position3]]]:
    """Build net graphs using only connections Minecraft can physically make."""
    AllWires = set().union(*NetWires.values())
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

    return {
        Signal: {
            Cell: [
                Neighbor
                for Neighbor in NeighborPositions(Cell)
                if Neighbor in Cells and IsPhysicalEdge(Cell, Neighbor)
            ]
            for Cell in Cells
        }
        for Signal, Cells in NetWires.items()
    }


def ValidatePhysicalRoutes(
    PhysicalGraphs: dict[str, dict[Position3, list[Position3]]],
    Producers: dict[str, Any],
    Targets: dict[str, list[Position3]],
) -> None:
    """Reject a compact route if any sink is not physically powered."""
    for Signal, Graph in PhysicalGraphs.items():
        Root = Producers[Signal].OutputPin
        Seen = {Root}
        Queue = deque([Root])
        while Queue:
            Cell = Queue.popleft()
            for Neighbor in Graph[Cell]:
                if Neighbor not in Seen:
                    Seen.add(Neighbor)
                    Queue.append(Neighbor)
        Missing = [Target for Target in Targets[Signal] if Target not in Seen]
        if Missing:
            raise ValueError(
                f"Physically disconnected route for net {Signal}: {Missing}"
            )


def ValidateTemplateIsolation(
    NetWires: dict[str, set[Position3]],
    ActualBlocks: set[Position3] | frozenset[Position3],
    ElectricalBlocks: set[Position3] | frozenset[Position3],
    SolidBlocks: set[Position3] | frozenset[Position3],
    Producers: dict[str, Any],
    Targets: dict[str, list[Position3]],
    AccessBySignal: dict[str, set[Position3]] | None = None,
) -> None:
    """Reject routed dust that enters or side-powers a cell template."""
    TemplateKeepOut = BuildElectricalExclusions(
        set(ElectricalBlocks) | set(SolidBlocks)
    )
    for Signal, Positions in NetWires.items():
        AllowedPins = {Producers[Signal].OutputPin, *Targets[Signal]}
        if AccessBySignal is not None:
            AllowedPins.update(AccessBySignal.get(Signal, set()))
        Overlaps = (Positions & set(ActualBlocks)) - AllowedPins
        if Overlaps:
            raise ValueError(
                f"Route for {Signal} overlaps template blocks: "
                f"{sorted(Overlaps)[:8]}"
            )
        SidePowering = (Positions & TemplateKeepOut) - AllowedPins
        if SidePowering:
            raise ValueError(
                f"Route for {Signal} enters template electrical clearance: "
                f"{sorted(SidePowering)[:8]}"
            )


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
        Root = Producers[Signal].OutputPin
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

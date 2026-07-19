"""Route-material scoring and repeater legalization actions."""

from __future__ import annotations

from collections import deque
from typing import Any

from ..Models import Position3
from ..Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from ..Failures import RoutingFailure, RoutingFailureReason, RoutingStageError


def EstimateRouteMaterialCost(
    Branch: list[Position3] | None,
    TemplateWires: set[Position3],
) -> tuple[int, int, int]:
    """Compute extra redstone dust and repeaters excluding template cells."""
    if Branch is None:
        return (10**9, 10**9, 10**9)
    NewDust = sum(1 for Position in Branch if Position not in TemplateWires)
    NewRepeaters = max(0, (NewDust - 1) // 12)
    return (NewDust, NewRepeaters, len(Branch))


def RepeaterFacing(Current: Position3, Next: Position3) -> str:
    DeltaX = Next[0] - Current[0]
    DeltaZ = Next[2] - Current[2]
    Directions = {
        (1, 0): "west",
        (-1, 0): "east",
        (0, 1): "north",
        (0, -1): "south",
    }
    try:
        return Directions[(DeltaX, DeltaZ)]
    except KeyError as Error:
        raise ValueError(
            "A routing repeater must lie on a flat straight run"
        ) from Error


def BuildRepeaters(
    NetWires: dict[str, set[Position3]],
    Producers: dict[str, Any],
    Targets: dict[str, list[Position3]],
    PhysicalGraphs: dict[str, dict[Position3, list[Position3]]],
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> dict[Position3, str]:
    """Legacy repeater synthesis was retired in favor of reserved repeater materialization."""
    raise NotImplementedError(
        "BuildRepeaters is retired. Use MaterializeReservedRepeaters "
        "with authoritative track reservations."
    )


def MaterializeReservedRepeaters(
    NetWires: dict[str, set[Position3]],
    Producers: dict[str, Any],
    Targets: dict[str, list[Position3]],
    PhysicalGraphs: dict[str, dict[Position3, list[Position3]]],
    Tracks: dict[str, Any],
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> dict[Position3, str]:
    """Materialize only the refresh resources fixed by track assignment."""
    Repeaters: dict[Position3, str] = {}
    MaximumRun = Technology.MaximumUnrefreshedDustLength
    for Signal, Cells in NetWires.items():
        Graph = PhysicalGraphs[Signal]
        Track = Tracks[Signal]
        Reserved = {
            Reservation.Position: Reservation
            for Reservation in Track.RepeaterReservations
        }
        for Position, Reservation in Reserved.items():
            Neighbors = Graph.get(Position, ())
            FlatNeighbors = [
                Neighbor for Neighbor in Neighbors if Neighbor[1] == Position[1]
            ]
            StraightPairs = [
                (First, Second)
                for Index, First in enumerate(FlatNeighbors)
                for Second in FlatNeighbors[Index + 1 :]
                if (
                    First[0] == Position[0] == Second[0]
                    or First[2] == Position[2] == Second[2]
                )
            ]
            if Position not in Cells or not StraightPairs:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoRepeaterSite,
                        Stage="Repeater",
                        AffectedNets=(Signal,),
                        Resources=(str(Reservation.Resource),),
                        Locations=(Position,),
                        Detail="reserved refresh resource is not a flat straight routed site",
                    )
                )
            if Reservation.Facing is None:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoRepeaterSite,
                        Stage="Repeater",
                        AffectedNets=(Signal,),
                        Resources=(str(Reservation.Resource),),
                        Locations=(Position,),
                        Detail="reserved refresh resource has no ordered facing",
                    )
                )
            Repeaters[Position] = Reservation.Facing

        Root = Producers[Signal].OutputPin
        BestUnrefreshedRun = {Root: 0}
        Queue = deque((Root,))
        while Queue:
            Current = Queue.popleft()
            for Neighbor in Graph[Current]:
                RunLength = (
                    0
                    if Neighbor in Reserved
                    else BestUnrefreshedRun[Current] + 1
                )
                if RunLength >= MaximumRun:
                    continue
                if RunLength >= BestUnrefreshedRun.get(Neighbor, MaximumRun + 1):
                    continue
                BestUnrefreshedRun[Neighbor] = RunLength
                Queue.append(Neighbor)
        for Target in Targets[Signal]:
            if Target not in BestUnrefreshedRun:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoRepeaterSite,
                        Stage="Repeater",
                        AffectedNets=(Signal,),
                        Locations=(Target,),
                        Detail="reserved refresh sequence allows signal strength to decay to zero",
                    )
                )
    return Repeaters

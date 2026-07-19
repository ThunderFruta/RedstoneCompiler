"""Conflict ownership analysis and bounded repair-selection actions."""

from __future__ import annotations

from collections import Counter

from ..Models import Position3
from .Geometry import BuildElectricalExclusions, NeighborPositions


def FindFlatRouteConflicts(
    NetWires: dict[str, set[Position3]],
) -> tuple[set[Position3], Counter[str]]:
    """Return electrical, support, and stair-headroom resource conflicts."""
    ConflictCells, ConflictCounts, _, _ = AnalyzeFlatRouteConflicts(NetWires)
    return ConflictCells, ConflictCounts


def AnalyzeFlatRouteConflicts(
    NetWires: dict[str, set[Position3]],
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
    Exclusions = {
        Signal: BuildElectricalExclusions(Positions)
        for Signal, Positions in NetWires.items()
    }
    Supports = {
        Signal: {(X, Y - 1, Z) for X, Y, Z in Positions}
        for Signal, Positions in NetWires.items()
    }
    Headroom: dict[str, set[Position3]] = {}
    for Signal, Positions in NetWires.items():
        RequiredAir: set[Position3] = set()
        for Position in Positions:
            for Neighbor in NeighborPositions(Position):
                if Neighbor not in Positions or Neighbor[1] == Position[1]:
                    continue
                Lower = Position if Position[1] < Neighbor[1] else Neighbor
                RequiredAir.add((Lower[0], Lower[1] + 1, Lower[2]))
        Headroom[Signal] = RequiredAir

        SelfConflicts = Positions & (Supports[Signal] | RequiredAir)
        if SelfConflicts:
            ConflictCells.update(SelfConflicts)
            ConflictCounts[Signal] += len(SelfConflicts)
            SelfConflictSignals.add(Signal)

    for Index, FirstSignal in enumerate(Signals):
        FirstPositions = NetWires[FirstSignal]
        for SecondSignal in Signals[Index + 1 :]:
            SecondPositions = NetWires[SecondSignal]
            FirstConflicts = FirstPositions & Exclusions[SecondSignal]
            SecondConflicts = SecondPositions & Exclusions[FirstSignal]
            FirstResourceConflicts = FirstPositions & (
                Supports[SecondSignal] | Headroom[SecondSignal]
            )
            SecondResourceConflicts = SecondPositions & (
                Supports[FirstSignal] | Headroom[FirstSignal]
            )
            FirstHeadroomConflicts = Headroom[FirstSignal] & Supports[SecondSignal]
            SecondHeadroomConflicts = Headroom[SecondSignal] & Supports[FirstSignal]
            ResourceConflicts = (
                FirstResourceConflicts
                | SecondResourceConflicts
                | FirstHeadroomConflicts
                | SecondHeadroomConflicts
            )
            if not FirstConflicts and not SecondConflicts and not ResourceConflicts:
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

    return ConflictCells, ConflictCounts, SelfConflictSignals, ConflictPairs


def SelectConflictRepairSignals(
    NetWires: dict[str, set[Position3]],
    SelfConflictSignals: set[str],
    ConflictPairs: Counter[tuple[str, str]],
) -> set[str]:
    """Legacy conflict-repair selection was retired from the active router."""
    raise NotImplementedError(
        "SelectConflictRepairSignals is retired. "
        "Use the conflict metrics exported from detailed authoritative routing instead."
    )


def ExpandConflictRepairNeighborhood(
    SeedSignal: str,
    ConflictPairs: set[tuple[str, str]],
    Depth: int,
) -> set[str]:
    """Legacy conflict neighborhood expansion was retired with shim migration."""
    raise NotImplementedError(
        "ExpandConflictRepairNeighborhood is retired. "
        "Conflict neighborhoods are no longer expanded by heuristic passes."
    )

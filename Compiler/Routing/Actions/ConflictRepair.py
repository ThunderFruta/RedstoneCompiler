"""Conflict ownership analysis and bounded repair-selection actions."""

from __future__ import annotations

from collections import Counter
from typing import Callable

from ..Models import Position3
from .Geometry import NeighborPositions


def FindFlatRouteConflicts(
    NetWires: dict[str, set[Position3]],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[set[Position3], Counter[str]]:
    """Return electrical, support, and stair-headroom resource conflicts."""
    ConflictCells, ConflictCounts, _, _ = AnalyzeFlatRouteConflicts(
        NetWires,
        WorkCheck=WorkCheck,
    )
    return ConflictCells, ConflictCounts


def AnalyzeFlatRouteConflicts(
    NetWires: dict[str, set[Position3]],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
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

    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "ProcessedPositions": ProcessedPositions,
            "SignalPairChecks": SignalPairChecks,
            "ConflictCellCount": len(ConflictCells),
        })
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

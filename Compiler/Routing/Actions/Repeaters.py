"""Route-material scoring and repeater legalization actions."""

from __future__ import annotations

from heapq import heappop, heappush
from collections import deque
from typing import Any, Callable

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


def _FindPath(
    Graph: dict[Position3, list[Position3]],
    Start: Position3,
    Target: Position3,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[Position3, ...]:
    """Find the shortest Manhattan-valid path between two nodes in a route graph."""
    Parents: dict[Position3, Position3 | None] = {Start: None}
    Pending = deque([Start])
    ExpandedNodes = 0
    while Pending and Target not in Parents:
        Current = Pending.popleft()
        ExpandedNodes += 1
        if WorkCheck is not None and ExpandedNodes % 256 == 0:
            WorkCheck({
                "Phase": "fallback-path-search",
                "ExpandedNodes": ExpandedNodes,
                "DiscoveredNodes": len(Parents),
            })
        for Neighbor in sorted(Graph.get(Current, ())):
            if Neighbor in Parents:
                continue
            Parents[Neighbor] = Current
            Pending.append(Neighbor)
    if Target not in Parents:
        return ()
    Path: list[Position3] = []
    Current: Position3 | None = Target
    while Current is not None:
        Path.append(Current)
        Current = Parents[Current]
    Path.reverse()
    return tuple(Path)


def _StraightRepeaterCandidates(
    Path: tuple[Position3, ...],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[int, str]:
    """Build a map of path index to valid repeater facing for straight candidates."""
    Candidates: dict[int, str] = {}
    for Index in range(1, len(Path) - 1):
        if WorkCheck is not None and Index % 256 == 0:
            WorkCheck({
                "Phase": "fallback-repeater-candidates",
                "ProcessedPathNodes": Index,
                "PathNodeCount": len(Path),
            })
        Previous, Current, Next = Path[Index - 1 : Index + 2]
        if Previous[1] == Current[1] == Next[1] and (
            Previous[0] == Current[0] == Next[0]
            or Previous[2] == Current[2] == Next[2]
        ):
            Delta = (Next[0] - Current[0], Next[2] - Current[2])
            Facing = {
                (1, 0): "west",
                (-1, 0): "east",
                (0, 1): "north",
                (0, -1): "south",
            }[Delta]
            Candidates[Index] = Facing
    return Candidates


def _BuildFallbackRepeaters(
    Root: Position3,
    Target: Position3,
    Graph: dict[Position3, list[Position3]],
    Existing: dict[Position3, str],
    Technology: RedstoneRoutingTechnology,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[dict[Position3, str], bool]:
    """Return extra repeater directions for one target, adding along the route as needed.

    Existing repeaters are treated as usable anchors whenever they are directionally
    valid for the target path.
    """
    Path = _FindPath(Graph, Root, Target, WorkCheck=WorkCheck)
    if not Path:
        return {}, False
    Candidates = _StraightRepeaterCandidates(Path, WorkCheck=WorkCheck)
    if len(Path) - 1 < Technology.MaximumUnrefreshedDustLength:
        return {}, True

    MaximumSegment = max(1, Technology.MaximumUnrefreshedDustLength - 1)
    LastRefresh = 0
    Added: dict[Position3, str] = {}

    while len(Path) - 1 - LastRefresh >= Technology.MaximumUnrefreshedDustLength:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "fallback-repeater-selection",
                "LastRefreshIndex": LastRefresh,
                "PathNodeCount": len(Path),
                "AddedRepeaterCount": len(Added),
            })
        Maximum = min(len(Path) - 2, LastRefresh + MaximumSegment)
        # Reuse an existing reservation on this segment when possible.
        SelectedIndex: int | None = max(
            (
                Index
                for Index in Candidates
                if LastRefresh < Index <= Maximum
                and Path[Index] in Existing
                and Existing[Path[Index]] == Candidates[Index]
            ),
            default=None,
        )
        if SelectedIndex is None:
            Candidate = [
                Index
                for Index in reversed(range(LastRefresh + 1, Maximum + 1))
                if Index in Candidates
            ]
            if not Candidate:
                return {}, False
            SelectedIndex = Candidate[0]
            Added[Path[SelectedIndex]] = Candidates[SelectedIndex]
            Existing[Path[SelectedIndex]] = Candidates[SelectedIndex]
        LastRefresh = SelectedIndex
    return Added, True


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


def _RepeaterOutputDelta(Facing: str) -> Position3:
    return {
        "west": (1, 0, 0),
        "east": (-1, 0, 0),
        "north": (0, 0, 1),
        "south": (0, 0, -1),
    }[Facing]


def PropagateRoutePower(
    Root: Position3,
    Graph: dict[Position3, list[Position3]],
    Repeaters: dict[Position3, str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[Position3, int]:
    """Apply the same directed strength rules as physical simulation."""
    Powers = {Root: 15}
    Pending: list[tuple[int, Position3]] = [(-15, Root)]
    ExpandedNodes = 0
    while Pending:
        NegativePower, Current = heappop(Pending)
        ExpandedNodes += 1
        if WorkCheck is not None and ExpandedNodes % 256 == 0:
            WorkCheck({
                "Phase": "power-propagation",
                "ExpandedNodes": ExpandedNodes,
                "PoweredNodeCount": len(Powers),
                "PendingNodeCount": len(Pending),
            })
        Power = -NegativePower
        if Power != Powers.get(Current):
            continue
        CurrentFacing = Repeaters.get(Current)
        if CurrentFacing is not None:
            Delta = _RepeaterOutputDelta(CurrentFacing)
            Output = tuple(Current[Index] + Delta[Index] for Index in range(3))
            CandidateValues = [(Output, 15)] if Output in Graph[Current] else []
        else:
            CandidateValues = []
            for Neighbor in Graph[Current]:
                NeighborFacing = Repeaters.get(Neighbor)
                if NeighborFacing is not None:
                    Delta = _RepeaterOutputDelta(NeighborFacing)
                    InputPosition = tuple(
                        Neighbor[Index] - Delta[Index] for Index in range(3)
                    )
                    if Current != InputPosition or Power <= 0:
                        continue
                    CandidatePower = 15
                else:
                    CandidatePower = Power - 1
                if CandidatePower > 0:
                    CandidateValues.append((Neighbor, CandidatePower))
        for Neighbor, CandidatePower in CandidateValues:
            if CandidatePower <= Powers.get(Neighbor, 0):
                continue
            Powers[Neighbor] = CandidatePower
            heappush(Pending, (-CandidatePower, Neighbor))
    return Powers


def FindSelfExcitingRepeaterCycles(
    Graph: dict[Position3, list[Position3]],
    Repeaters: dict[Position3, str],
) -> tuple[tuple[Position3, tuple[Position3, ...]], ...]:
    """Return repeaters whose output can feed their own input through dust."""

    def DirectedNeighbors(Current: Position3) -> tuple[Position3, ...]:
        CurrentFacing = Repeaters.get(Current)
        if CurrentFacing is not None:
            Delta = _RepeaterOutputDelta(CurrentFacing)
            Output = tuple(
                Current[Index] + Delta[Index] for Index in range(3)
            )
            return (Output,) if Output in Graph.get(Current, ()) else ()
        Values = []
        for Neighbor in Graph.get(Current, ()):
            NeighborFacing = Repeaters.get(Neighbor)
            if NeighborFacing is not None:
                Delta = _RepeaterOutputDelta(NeighborFacing)
                InputPosition = tuple(
                    Neighbor[Index] - Delta[Index] for Index in range(3)
                )
                if Current != InputPosition:
                    continue
            Values.append(Neighbor)
        return tuple(sorted(Values))

    Cycles = []
    for Repeater, Facing in sorted(Repeaters.items()):
        Delta = _RepeaterOutputDelta(Facing)
        InputPosition = tuple(
            Repeater[Index] - Delta[Index] for Index in range(3)
        )
        OutputPosition = tuple(
            Repeater[Index] + Delta[Index] for Index in range(3)
        )
        if (
            InputPosition not in Graph.get(Repeater, ())
            or OutputPosition not in Graph.get(Repeater, ())
        ):
            continue
        Parents: dict[Position3, Position3 | None] = {
            OutputPosition: None
        }
        Pending = deque((OutputPosition,))
        while Pending and InputPosition not in Parents:
            Current = Pending.popleft()
            for Neighbor in DirectedNeighbors(Current):
                if Neighbor == Repeater or Neighbor in Parents:
                    continue
                Parents[Neighbor] = Current
                Pending.append(Neighbor)
        if InputPosition not in Parents:
            continue
        Path = []
        Current: Position3 | None = InputPosition
        while Current is not None:
            Path.append(Current)
            Current = Parents[Current]
        Cycles.append((Repeater, tuple(reversed(Path))))
    return tuple(Cycles)


def PruneUnneededMaterializedRepeaters(
    Root: Position3,
    Targets: tuple[Position3, ...],
    Graph: dict[Position3, list[Position3]],
    Repeaters: dict[Position3, str],
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[dict[Position3, str], dict[str, object]]:
    """Remove final refreshers only when every sink stays electrically powered.

    This deliberately runs after detailed routing and fallback insertion, not
    while candidates are being generated.  Each trial uses the exact directed
    power model, so branches retain a refresher whenever any downstream target
    still needs it. Removing a repeater turns that cell back into bidirectional
    dust, so a removal must also preserve the absence of directed feedback.
    """
    Original = dict(Repeaters)
    Diagnostics: dict[str, object] = {
        "InitialCount": len(Original),
        "RemovedPositions": [],
        "RetainedPositions": [],
        "PowerValidated": False,
    }
    if not Original:
        Diagnostics["PowerValidated"] = True
        return Original, Diagnostics
    InitialPower = PropagateRoutePower(
        Root,
        Graph,
        Original,
        WorkCheck=WorkCheck,
    )
    if any(InitialPower.get(Target, 0) <= 0 for Target in Targets):
        Diagnostics["RetainedPositions"] = [
            list(Position) for Position in sorted(Original)
        ]
        Diagnostics["Reason"] = "initial-refresh-set-does-not-power-all-targets"
        return Original, Diagnostics

    Distances: dict[Position3, int] = {Root: 0}
    Pending = deque((Root,))
    ExpandedNodes = 0
    while Pending:
        Current = Pending.popleft()
        ExpandedNodes += 1
        if WorkCheck is not None and ExpandedNodes % 256 == 0:
            WorkCheck({
                "Phase": "repeater-pruning-distance",
                "ExpandedNodes": ExpandedNodes,
                "KnownDistances": len(Distances),
            })
        for Neighbor in sorted(Graph.get(Current, ())):
            if Neighbor in Distances:
                continue
            Distances[Neighbor] = Distances[Current] + 1
            Pending.append(Neighbor)

    Retained = dict(Original)
    Removed: list[Position3] = []
    OrderedPositions = sorted(
        Original,
        key=lambda Position: (Distances.get(Position, 10**9), Position),
    )
    for Index, Position in enumerate(OrderedPositions, start=1):
        if WorkCheck is not None and Index % 16 == 0:
            WorkCheck({
                "Phase": "repeater-pruning-trial",
                "ProcessedRepeaters": Index,
                "RepeaterCount": len(OrderedPositions),
                "RemovedCount": len(Removed),
            })
        Trial = dict(Retained)
        Trial.pop(Position, None)
        if FindSelfExcitingRepeaterCycles(Graph, Trial):
            continue
        Powers = PropagateRoutePower(
            Root,
            Graph,
            Trial,
            WorkCheck=WorkCheck,
        )
        if all(Powers.get(Target, 0) > 0 for Target in Targets):
            Retained = Trial
            Removed.append(Position)

    Diagnostics.update({
        "RemovedPositions": [list(Position) for Position in Removed],
        "RetainedPositions": [list(Position) for Position in sorted(Retained)],
        "PowerValidated": True,
    })
    return Retained, Diagnostics


def PruneRedundantRepeaterReservations(
    Root: Position3,
    Targets: tuple[Position3, ...],
    Graph: dict[Position3, list[Position3]],
    Reservations: tuple[Any, ...],
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> tuple[Any, ...]:
    """Prune with one tree walk rather than one full simulation per repeater.

    Reserved repeaters are produced from root-to-sink paths.  On a tree, a
    latest-legal refresh site on each root-to-sink path is sufficient and
    minimizes the number of refreshers.  We construct that union in linear
    graph time, then run one exact power propagation as the safety check.  If
    a cyclic route has a directed-power edge case the tree projection misses,
    preserve the original legal reservation set.
    """
    ByPosition = {Reservation.Position: Reservation for Reservation in Reservations}
    if len(ByPosition) < 2:
        return tuple(ByPosition[Position] for Position in sorted(ByPosition))
    # Multi-target trees can hide branch coverage details when strict pruning is
    # applied too aggressively. Keep every explicit reservation for correctness.
    if len(Targets) > 1:
        return tuple(ByPosition[Position] for Position in sorted(ByPosition))
    Parents: dict[Position3, Position3 | None] = {Root: None}
    Pending = [Root]
    for Current in Pending:
        for Neighbor in sorted(Graph.get(Current, ())):
            if Neighbor in Parents:
                continue
            Parents[Neighbor] = Current
            Pending.append(Neighbor)
    MaximumSegmentLength = Technology.MaximumUnrefreshedDustLength - 1
    Retained: dict[Position3, Any] = {}
    for Target in Targets:
        if Target not in Parents:
            return tuple(ByPosition[Position] for Position in sorted(ByPosition))
        Path = []
        Current: Position3 | None = Target
        while Current is not None:
            Path.append(Current)
            Current = Parents[Current]
        Path.reverse()
        LastRefreshIndex = 0
        while len(Path) - 1 - LastRefreshIndex > MaximumSegmentLength:
            LatestIndex = min(
                len(Path) - 2,
                LastRefreshIndex + MaximumSegmentLength,
            )
            SelectedIndex = next(
                (
                    Index
                    for Index in range(LatestIndex, LastRefreshIndex, -1)
                    if Path[Index] in ByPosition
                ),
                None,
            )
            if SelectedIndex is None:
                return tuple(
                    ByPosition[Position] for Position in sorted(ByPosition)
                )
            Position = Path[SelectedIndex]
            Retained[Position] = ByPosition[Position]
            LastRefreshIndex = SelectedIndex
    Powers = PropagateRoutePower(
        Root,
        Graph,
        {
            Position: Reservation.Facing
            for Position, Reservation in Retained.items()
        },
    )
    if not all(Powers.get(Target, 0) > 0 for Target in Targets):
        return tuple(ByPosition[Position] for Position in sorted(ByPosition))
    return tuple(Retained[Position] for Position in sorted(Retained))


def MaterializeReservedRepeaters(
    NetWires: dict[str, set[Position3]],
    Producers: dict[str, Any],
    Targets: dict[str, list[Position3]],
    PhysicalGraphs: dict[str, dict[Position3, list[Position3]]],
    Tracks: dict[str, Any],
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    PruningDiagnostics: dict[str, dict[str, object]] | None = None,
) -> dict[Position3, str]:
    """Materialize only the refresh resources fixed by track assignment."""
    Repeaters: dict[Position3, str] = {}
    if WorkCheck is not None:
        WorkCheck({"Phase": "start", "SignalCount": len(Tracks)})
    for SignalIndex, Signal in enumerate(Tracks, start=1):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "signal",
                "Signal": Signal,
                "CompletedSignals": SignalIndex - 1,
                "RepeaterCount": len(Repeaters),
            })
        Cells = NetWires.get(Signal, frozenset())
        if not Cells:
            continue
        if Signal not in PhysicalGraphs:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.NoRepeaterSite,
                    Stage="Repeater",
                    AffectedNets=(Signal,),
                    Detail="missing physical graph for routed signal",
                )
            )
        if Signal not in Producers:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.NoRepeaterSite,
                    Stage="Repeater",
                    AffectedNets=(Signal,),
                    Detail="missing source gate for routed signal",
                )
            )
        Graph = PhysicalGraphs[Signal]
        Track = Tracks[Signal]
        Reserved = {}
        ReservedFacing = {}
        SignalRepeaters: dict[Position3, str] = {}
        InvalidReservations: list[dict[str, object]] = []
        for ReservationIndex, Reservation in enumerate(
            Track.RepeaterReservations,
            start=1,
        ):
            Reserved[Reservation.Position] = Reservation
            if Reservation.Facing is not None:
                ReservedFacing[Reservation.Position] = Reservation.Facing
            if WorkCheck is not None and ReservationIndex % 64 == 0:
                WorkCheck({
                    "Phase": "reservation-indexing",
                    "Signal": Signal,
                    "ProcessedReservations": ReservationIndex,
                })
        for ReservationIndex, (Position, Reservation) in enumerate(
            tuple(Reserved.items()),
            start=1,
        ):
            if WorkCheck is not None and ReservationIndex % 64 == 0:
                WorkCheck({
                    "Phase": "reservation-validation",
                    "Signal": Signal,
                    "ProcessedReservations": ReservationIndex,
                    "ReservationCount": len(Reserved),
                })
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
                Reserved.pop(Position, None)
                ReservedFacing.pop(Position, None)
                InvalidReservations.append({
                    "Position": list(Position),
                    "Reason": "not-flat-straight-routed-site",
                })
                continue
            if Reservation.Facing is None:
                Reserved.pop(Position, None)
                InvalidReservations.append({
                    "Position": list(Position),
                    "Reason": "missing-facing",
                })
                continue
            SignalRepeaters[Position] = Reservation.Facing

        if Producers[Signal].OutputPin is None:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.NoRepeaterSite,
                    Stage="Repeater",
                    AffectedNets=(Signal,),
                    Detail="source gate has no output pin",
                )
            )
        Root = Producers[Signal].OutputPin
        BestUnrefreshedRun = PropagateRoutePower(
            Root,
            Graph,
            ReservedFacing,
            WorkCheck=WorkCheck,
        )
        for TargetIndex, Target in enumerate(Targets[Signal], start=1):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "target-validation",
                    "Signal": Signal,
                    "ProcessedTargets": TargetIndex - 1,
                    "TargetCount": len(Targets[Signal]),
                })
            if BestUnrefreshedRun.get(Target, 0) <= 0:
                MissingTargets = []
                for Checked in Targets[Signal]:
                    if BestUnrefreshedRun.get(Checked, 0) <= 0:
                        MissingTargets.append(Checked)
                AddedRepeaters: dict[Position3, str] = {}
                for MissingTarget in MissingTargets:
                    ExtraRepeaters, Okay = _BuildFallbackRepeaters(
                        Root,
                        MissingTarget,
                        Graph,
                        ReservedFacing,
                        Technology,
                        WorkCheck=WorkCheck,
                    )
                    if not Okay:
                        raise RoutingStageError(
                            RoutingFailure(
                                Reason=RoutingFailureReason.NoRepeaterSite,
                                Stage="Repeater",
                                AffectedNets=(Signal,),
                                Locations=(MissingTarget,),
                                Detail="reserved refresh sequence allows signal strength to decay to zero and fallback insertion cannot complete the path",
                            )
                        )
                    AddedRepeaters.update(ExtraRepeaters)
                    if ExtraRepeaters:
                        ReservedFacing.update(ExtraRepeaters)
                if AddedRepeaters:
                    BestUnrefreshedRun = PropagateRoutePower(
                        Root,
                        Graph,
                        ReservedFacing,
                        WorkCheck=WorkCheck,
                    )
                    for MissingTarget in MissingTargets:
                        if BestUnrefreshedRun.get(MissingTarget, 0) <= 0:
                            raise RoutingStageError(
                                RoutingFailure(
                                    Reason=RoutingFailureReason.NoRepeaterSite,
                                    Stage="Repeater",
                                    AffectedNets=(Signal,),
                                    Locations=(MissingTarget,),
                                    Detail="reserved refresh sequence (with fallback) allows signal strength to decay to zero",
                                )
                            )
                    SignalRepeaters.update(AddedRepeaters)
                else:
                    raise RoutingStageError(
                        RoutingFailure(
                            Reason=RoutingFailureReason.NoRepeaterSite,
                            Stage="Repeater",
                            AffectedNets=(Signal,),
                            Locations=(Target,),
                            Detail="reserved refresh sequence allows signal strength to decay to zero and no valid repeater fallback path exists",
                            Diagnostics={
                                "InvalidReservations": InvalidReservations,
                                "ReservedRepeaters": [
                                    {
                                        "Position": list(Position),
                                        "Facing": Facing,
                                    }
                                    for Position, Facing in sorted(
                                        ReservedFacing.items()
                                    )
                                ],
                                "TargetStrength": {
                                    str(Value): BestUnrefreshedRun.get(Value, 0)
                                    for Value in Targets[Signal]
                                },
                            },
                        )
                    )
        PrunedRepeaters, Diagnostics = PruneUnneededMaterializedRepeaters(
            Root,
            tuple(Targets[Signal]),
            Graph,
            SignalRepeaters,
            Technology,
            WorkCheck=WorkCheck,
        )
        if PruningDiagnostics is not None:
            PruningDiagnostics[Signal] = Diagnostics
        Repeaters.update(PrunedRepeaters)
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "CompletedSignals": len(Tracks),
            "RepeaterCount": len(Repeaters),
        })
    return Repeaters

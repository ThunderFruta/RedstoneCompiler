"""Deterministic exact layer/track assignment for global channel routes."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .ChannelPlanner import ChannelPlan, Position2
from .Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from .Technology import (
    DefaultRedstoneRoutingTechnology,
    RepeaterInputFacingForStep,
    RedstoneRoutingTechnology,
)
from .ResourceGraph import (
    RoutingEdge,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
)


def _RepeaterInputFacing(Current, Next):
    return RepeaterInputFacingForStep(Current, Next)


@dataclass(frozen=True)
class AssignedTrack:
    Signal: str
    TrackId: str
    Layer: int
    Guide: frozenset[Position2]
    RepeaterSites: frozenset[Position2]
    RepeaterWaypointsByTarget: dict[
        tuple[int, int, int], tuple[tuple[int, int, int], ...]
    ]
    ReservedResources: frozenset[RoutingResourceId]
    RepeaterReservations: tuple[RoutingReservation, ...]
    AssignedPathsByTarget: dict[
        tuple[int, int, int], tuple[tuple[int, int, int], ...]
    ]
    SourcePinAccessPath: tuple[tuple[int, int, int], ...]
    TargetPinAccessPathsByTarget: dict[
        tuple[int, int, int], tuple[tuple[int, int, int], ...]
    ]
    SelectedPortalIds: tuple[str, ...] = ()
    OwnedNodes: frozenset[tuple[int, int, int]] = frozenset()
    OwnedEdges: frozenset[RoutingEdge] = frozenset()


@dataclass(frozen=True)
class TrackAssignment:
    Tracks: dict[str, AssignedTrack]
    ResourceOwners: dict[RoutingResourceId, tuple[str, ...]]


def _GuidePath(
    Guide: frozenset[Position2],
    Start: Position2,
    End: Position2,
) -> tuple[Position2, ...]:
    Parents: dict[Position2, Position2 | None] = {Start: None}
    Pending = deque((Start,))
    while Pending and End not in Parents:
        X, Z = Pending.popleft()
        for Neighbor in ((X + 1, Z), (X - 1, Z), (X, Z + 1), (X, Z - 1)):
            if Neighbor in Guide and Neighbor not in Parents:
                Parents[Neighbor] = (X, Z)
                Pending.append(Neighbor)
    if End not in Parents:
        return ()
    Result = []
    Current: Position2 | None = End
    while Current is not None:
        Result.append(Current)
        Current = Parents[Current]
    return tuple(reversed(Result))


def _ReserveRepeaterWaypoints(
    Plan: ChannelPlan,
    Signal: str,
    Technology: RedstoneRoutingTechnology,
) -> tuple[
    dict[tuple[int, int, int], tuple[tuple[int, int, int], ...]],
    tuple[RoutingReservation, ...],
    dict[tuple[int, int, int], tuple[tuple[int, int, int], ...]],
]:
    Profile = Plan.Profiles[Signal]
    Guide = Plan.Guides[Signal]
    Eligible = _RepeaterOpportunities(Guide)
    RoutingY = Technology.RoutingY(Profile.Root[1], Plan.Layers[Signal])
    SourceLanding = Technology.AccessLanding(Profile.SourceAccessPath)
    Root2 = (SourceLanding[0], SourceLanding[2])
    Result = {}
    PinReservations = [
        (Profile.SourceAccessPath[1], Profile.SourceAccessPath[2]),
        *((Path[1], Path[0]) for Path in Profile.TargetAccessPaths.values()),
    ]
    Reservations: dict[tuple[int, int, int], RoutingReservation] = {
        Position: RoutingReservation(
            Signal=Signal,
            Resource=RoutingResourceId(RoutingResourceKind.Wire, Position),
            Position=Position,
            Purpose="PinAccessRepeater",
            InputFacing=_RepeaterInputFacing(Position, Next),
        )
        for Position, Next in PinReservations
    }
    AssignedPaths = {}
    for Target in Profile.Targets:
        TargetLanding = Technology.AccessLanding(
            Profile.TargetAccessPaths[Target]
        )
        Target2 = (TargetLanding[0], TargetLanding[2])
        Path = _GuidePath(Guide, Root2, Target2)
        if not Path:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                    Stage="Track",
                    AffectedNets=(Signal,),
                    Locations=(Target,),
                    Detail="global guide has no root-to-target resource path",
                )
            )
        AssignedPaths[Target] = tuple(
            (X, RoutingY, Z) for X, Z in Path
        )
        TargetAccessCost = abs(RoutingY - Target[1]) + 1
        if any(Position in Eligible for Position in Path[1:-1]):
            TargetAccessCost = max(
                TargetAccessCost,
                Technology.MaximumUnrefreshedDustLength - (len(Path) - 1) + 1,
            )
        LastRefresh = 0
        Reserved = []
        RoutingWaypoints = []
        while (
            len(Path) - 1 + TargetAccessCost - LastRefresh
            > Technology.MaximumUnrefreshedDustLength
        ):
            MaximumIndex = min(
                len(Path) - 2,
                LastRefresh + Technology.ReservedRepeaterInterval,
            )
            PreferredIndex = min(
                MaximumIndex,
                LastRefresh + Technology.ReservedRepeaterInterval,
            )
            Candidates = [
                Index
                for Index in range(max(1, LastRefresh + 1), MaximumIndex + 1)
                if Path[Index] in Eligible
            ]
            if not Candidates:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoRepeaterSite,
                        Stage="Track",
                        AffectedNets=(Signal,),
                        Locations=((Path[MaximumIndex][0], RoutingY, Path[MaximumIndex][1]),),
                        Detail="assigned global resources contain no legal refresh site",
                    )
                )
            Selected = min(
                Candidates,
                key=lambda Index: (abs(Index - PreferredIndex), Index),
            )
            Reserved.append((Path[Selected][0], RoutingY, Path[Selected][1]))
            for WaypointIndex in (Selected - 1, Selected, Selected + 1):
                Waypoint = (
                    Path[WaypointIndex][0],
                    RoutingY,
                    Path[WaypointIndex][1],
                )
                if not RoutingWaypoints or RoutingWaypoints[-1] != Waypoint:
                    RoutingWaypoints.append(Waypoint)
            Position = Reserved[-1]
            Reservations.setdefault(
                Position,
                RoutingReservation(
                    Signal=Signal,
                    Resource=RoutingResourceId(
                        RoutingResourceKind.Wire, Position
                    ),
                    Position=Position,
                    Purpose="Repeater",
                    InputFacing=_RepeaterInputFacing(
                        Position,
                        (
                            Path[Selected + 1][0],
                            RoutingY,
                            Path[Selected + 1][1],
                        ),
                    ),
                ),
            )
            LastRefresh = Selected
        Result[Target] = tuple(RoutingWaypoints)
    return (
        Result,
        tuple(Reservations[Position] for Position in sorted(Reservations)),
        AssignedPaths,
    )


def _ValidateConnectedGuide(Signal: str, Guide: frozenset[Position2]) -> None:
    if not Guide:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Global",
                AffectedNets=(Signal,),
                Detail="global guide is empty",
                RepairActions=("reroute_global_net",),
            )
        )
    Seen = {min(Guide)}
    Pending = deque(Seen)
    while Pending:
        X, Z = Pending.popleft()
        for Neighbor in ((X + 1, Z), (X - 1, Z), (X, Z + 1), (X, Z - 1)):
            if Neighbor in Guide and Neighbor not in Seen:
                Seen.add(Neighbor)
                Pending.append(Neighbor)
    if Seen != set(Guide):
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Global",
                AffectedNets=(Signal,),
                Detail="global guide contains disconnected components",
                RepairActions=("reroute_global_net",),
            )
        )


def _RepeaterOpportunities(Guide: frozenset[Position2]) -> frozenset[Position2]:
    """Return straight, non-branch cells where a repeater can be realized."""
    Result = set()
    for X, Z in Guide:
        Horizontal = (X - 1, Z) in Guide and (X + 1, Z) in Guide
        Vertical = (X, Z - 1) in Guide and (X, Z + 1) in Guide
        if Horizontal != Vertical:
            Result.add((X, Z))
    return frozenset(Result)

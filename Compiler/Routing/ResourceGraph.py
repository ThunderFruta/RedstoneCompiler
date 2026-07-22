"""Authoritative physical resource graph for Minecraft redstone routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from collections import deque
from typing import Callable, Iterable

from .Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)

Position2 = tuple[int, int]
Position3 = tuple[int, int, int]
RoutingEdge = tuple[Position3, Position3]


class RoutingResourceKind(str, Enum):
    Wire = "Wire"
    Support = "Support"
    Air = "Air"
    Electrical = "Electrical"


@dataclass(frozen=True)
class RoutingResourceId:
    Kind: RoutingResourceKind
    Position: Position3

    def __str__(self) -> str:
        X, Y, Z = self.Position
        return f"{self.Kind.value}:{X},{Y},{Z}"


@dataclass(frozen=True)
class RoutingReservation:
    Signal: str
    Resource: RoutingResourceId
    Position: Position3
    Purpose: str
    Facing: str | None = None


@dataclass(frozen=True)
class PinAccessSelection:
    Signal: str
    Source: Position3
    Target: Position3
    Path: tuple[Position3, ...]
    ReservedResources: frozenset[RoutingResourceId]


@dataclass(frozen=True)
class PinAccessPortal:
    PortalId: str
    Signal: str
    Terminal: Position3
    Layer: int
    Path: tuple[Position3, ...]
    Edges: frozenset[RoutingEdge]
    Claims: RoutingResourceClaims
    Length: int
    BendCount: int
    ViaCount: int
    Cost: int


@dataclass(frozen=True)
class PortalReservation:
    """A deterministic boundary slot reserved before detailed routing."""

    Signal: str
    Terminal: Position3
    Layer: int
    SlotIndex: int
    PortalId: str
    Claims: RoutingResourceClaims

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "Terminal": list(self.Terminal),
            "Layer": self.Layer,
            "SlotIndex": self.SlotIndex,
            "PortalId": self.PortalId,
            "ClaimCount": len(self.Claims.ResourceIds),
        }


@dataclass(frozen=True)
class NetRouteCandidate:
    CandidateId: str
    Signal: str
    SourcePortalId: str
    TargetPortalIds: dict[Position3, str]
    Nodes: frozenset[Position3]
    Edges: frozenset[RoutingEdge]
    Claims: RoutingResourceClaims
    Layer: int
    Guide: frozenset[Position2]
    RepeaterWaypoints: tuple[Position3, ...]
    MaterialCost: int
    FootprintGrowth: int
    Length: int
    BendCount: int
    ViaCount: int
    IncrementalMaterialCost: int = 0
    IncrementalLength: int = 0
    SeedNodeCount: int = 0


@dataclass(frozen=True)
class RoutingAssignment:
    SelectedCandidates: dict[str, NetRouteCandidate]
    ResourceOwners: dict[RoutingResourceId, tuple[str, ...]]
    ExpansionCount: int
    PortalCount: int
    CandidateCount: int


@dataclass(frozen=True)
class IndexedRoutingResourceGraph:
    ResourcePositions: tuple[Position3, ...]
    PositionIndices: dict[Position3, int]

    def EncodeClaims(
        self,
        Claims: RoutingResourceClaims,
    ) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        return tuple(
            tuple(sorted(self.PositionIndices[Position] for Position in Values))
            for Values in (
                Claims.WireCells,
                Claims.SupportCells,
                Claims.RequiredAirCells,
                Claims.ElectricalCells,
            )
        )


@dataclass(frozen=True)
class RoutingResourceClaims:
    WireCells: frozenset[Position3] = frozenset()
    SupportCells: frozenset[Position3] = frozenset()
    RequiredAirCells: frozenset[Position3] = frozenset()
    ElectricalCells: frozenset[Position3] = frozenset()

    @property
    def ResourceIds(self) -> frozenset[RoutingResourceId]:
        return frozenset(
            [
                *(RoutingResourceId(RoutingResourceKind.Wire, Value) for Value in self.WireCells),
                *(RoutingResourceId(RoutingResourceKind.Support, Value) for Value in self.SupportCells),
                *(RoutingResourceId(RoutingResourceKind.Air, Value) for Value in self.RequiredAirCells),
                *(RoutingResourceId(RoutingResourceKind.Electrical, Value) for Value in self.ElectricalCells),
            ]
        )


@dataclass(frozen=True)
class LocalRouteClaim:
    """One authoritative signal-owned tree fragment fixed by placement."""

    Signal: str
    ClusterId: int
    Root: Position3
    ConnectedTargets: tuple[Position3, ...]
    BoundaryNodes: tuple[Position3, ...]
    Nodes: frozenset[Position3]
    Edges: frozenset[RoutingEdge]
    Claims: RoutingResourceClaims
    RepeaterReservations: tuple[RoutingReservation, ...] = ()
    ExactRouteSignalBlocks: int = 0
    ExactRouteRefreshBlocks: int = 0
    ExactRouteSupportBlocks: int = 0

    @property
    def ExactRoutingBlocks(self) -> int:
        return self.ExactRouteSignalBlocks + self.ExactRouteRefreshBlocks


@dataclass(frozen=True)
class RoutingPrimitive:
    Start: Position3
    End: Position3
    Claims: RoutingResourceClaims
    IsVerticalTransition: bool


@dataclass(frozen=True)
class RoutingGraphRegion:
    Bounds: tuple[int, int, int, int, int, int]
    Nodes: frozenset[Position3]
    Edges: frozenset[RoutingEdge]

    def ContainsEdge(self, First: Position3, Second: Position3) -> bool:
        return NormalizeRoutingEdge(First, Second) in self.Edges


@dataclass
class RoutingResourceGraph:
    """Lazy graph whose nodes and edges are the sole physical legality model."""

    ActualBlocks: frozenset[Position3]
    ElectricalBlocks: frozenset[Position3]
    SolidBlocks: frozenset[Position3]
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology
    GraphVersion: str = "routing-resource-graph-v1"
    _RegionCache: dict[
        tuple[
            tuple[int, int, int, int, int, int],
            frozenset[Position2] | None,
            frozenset[Position3],
        ],
        RoutingGraphRegion,
    ] = field(default_factory=dict, repr=False)
    _RegionCacheOrder: list[
        tuple[
            tuple[int, int, int, int, int, int],
            frozenset[Position2] | None,
            frozenset[Position3],
        ]
    ] = field(default_factory=list, repr=False)

    @cached_property
    def StaticKeepOut(self) -> frozenset[Position3]:
        return frozenset(
            self.Technology.BuildElectricalExclusions(
                set(self.ElectricalBlocks) | set(self.SolidBlocks)
            )
        )

    @property
    def CachedNodeCount(self) -> int:
        return len(set().union(*(Region.Nodes for Region in self._RegionCache.values()))) if self._RegionCache else 0

    @property
    def CachedEdgeCount(self) -> int:
        return len(set().union(*(Region.Edges for Region in self._RegionCache.values()))) if self._RegionCache else 0

    def IsLegalNode(
        self,
        Position: Position3,
        AllowedAccess: frozenset[Position3] = frozenset(),
    ) -> bool:
        if Position in AllowedAccess:
            return Position not in self.ActualBlocks
        Support = (Position[0], Position[1] - 1, Position[2])
        return (
            Position not in self.ActualBlocks
            and Support not in self.ActualBlocks
            and Position not in self.StaticKeepOut
        )

    def BuildPrimitive(
        self,
        First: Position3,
        Second: Position3,
    ) -> RoutingPrimitive | None:
        if Second not in self.Technology.NeighborPositions(First):
            return None
        if not self.CanBuildNeighborPrimitive(First, Second):
            return None
        IsVertical = First[1] != Second[1]
        RequiredAir: set[Position3] = set()
        if IsVertical:
            Lower = First if First[1] < Second[1] else Second
            Headroom = (Lower[0], Lower[1] + 1, Lower[2])
            RequiredAir.add(Headroom)
        WireCells = {First, Second}
        SupportCells = {(X, Y - 1, Z) for X, Y, Z in WireCells}
        ElectricalCells = self.Technology.BuildElectricalExclusions(WireCells)
        return RoutingPrimitive(
            Start=First,
            End=Second,
            Claims=RoutingResourceClaims(
                WireCells=frozenset(WireCells),
                SupportCells=frozenset(SupportCells),
                RequiredAirCells=frozenset(RequiredAir),
                ElectricalCells=frozenset(ElectricalCells),
            ),
            IsVerticalTransition=IsVertical,
        )

    def CanBuildNeighborPrimitive(
        self,
        First: Position3,
        Second: Position3,
    ) -> bool:
        """Check an already-enumerated neighboring edge without claim allocation."""
        if First[1] == Second[1]:
            return True
        Lower = First if First[1] < Second[1] else Second
        Upper = Second if First[1] < Second[1] else First
        Headroom = (Lower[0], Lower[1] + 1, Lower[2])
        if Headroom in self.SolidBlocks or Headroom in self.ActualBlocks:
            return False
        Support = (Upper[0], Upper[1] - 1, Upper[2])
        return not (
            Support in self.ActualBlocks
            and Support not in self.SolidBlocks
        )

    def BuildRegion(
        self,
        Bounds: tuple[int, int, int, int, int, int],
        AllowedColumns: frozenset[Position2] | None = None,
        AllowedAccess: frozenset[Position3] = frozenset(),
        WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ) -> RoutingGraphRegion:
        if WorkCheck is not None:
            WorkCheck({"Phase": "start"})
        Key = (Bounds, AllowedColumns, AllowedAccess)
        Cached = self._RegionCache.get(Key)
        if Cached is not None:
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "complete",
                    "CacheHit": True,
                    "NodeCount": len(Cached.Nodes),
                    "EdgeCount": len(Cached.Edges),
                })
            return Cached
        MinimumX, MaximumX, MinimumY, MaximumY, MinimumZ, MaximumZ = Bounds
        RequestedColumns = (
            frozenset(
                (X, Z)
                for X in range(MinimumX, MaximumX + 1)
                for Z in range(MinimumZ, MaximumZ + 1)
            )
            if AllowedColumns is None
            else frozenset(
                (X, Z)
                for X, Z in AllowedColumns
                if MinimumX <= X <= MaximumX
                and MinimumZ <= Z <= MaximumZ
            )
        )
        BaseRegion: RoutingGraphRegion | None = None
        BaseColumns: frozenset[Position2] = frozenset()
        BaseMaximumY = MinimumY - 1
        BaseScore = -1
        for (
            CandidateBounds,
            CandidateAllowedColumns,
            CandidateAllowedAccess,
        ), CandidateRegion in self._RegionCache.items():
            (
                CandidateMinimumX,
                CandidateMaximumX,
                CandidateMinimumY,
                CandidateMaximumY,
                CandidateMinimumZ,
                CandidateMaximumZ,
            ) = CandidateBounds
            if (
                CandidateAllowedAccess != AllowedAccess
                or CandidateMinimumX != MinimumX
                or CandidateMaximumX != MaximumX
                or CandidateMinimumY != MinimumY
                or CandidateMaximumY > MaximumY
                or CandidateMinimumZ != MinimumZ
                or CandidateMaximumZ != MaximumZ
            ):
                continue
            CandidateColumns = (
                frozenset(
                    (X, Z)
                    for X in range(MinimumX, MaximumX + 1)
                    for Z in range(MinimumZ, MaximumZ + 1)
                )
                if CandidateAllowedColumns is None
                else CandidateAllowedColumns
            )
            Score = (
                len(RequestedColumns & CandidateColumns)
                * max(1, CandidateMaximumY - MinimumY + 1)
            )
            if Score > BaseScore:
                BaseScore = Score
                BaseRegion = CandidateRegion
                BaseColumns = CandidateColumns
                BaseMaximumY = CandidateMaximumY

        Nodes: set[Position3] = set()
        Edges: set[RoutingEdge] = set()
        if BaseRegion is not None:
            Nodes.update(
                Position
                for Position in BaseRegion.Nodes
                if MinimumY <= Position[1] <= MaximumY
                and (
                    (Position[0], Position[2]) in RequestedColumns
                    or Position in AllowedAccess
                )
            )
            Edges.update(
                Edge
                for Edge in BaseRegion.Edges
                if Edge[0] in Nodes and Edge[1] in Nodes
            )
        NewNodes: set[Position3] = set()
        OrderedColumns = sorted(RequestedColumns)
        TotalColumns = len(OrderedColumns)
        for ColumnIndex, (X, Z) in enumerate(OrderedColumns):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "nodes",
                    "CompletedXColumns": ColumnIndex,
                    "TotalXColumns": TotalColumns,
                    "ReusedNodeCount": len(Nodes),
                })
            FirstY = (
                BaseMaximumY + 1
                if BaseRegion is not None and (X, Z) in BaseColumns
                else MinimumY
            )
            for Y in range(FirstY, MaximumY + 1):
                Position = (X, Y, Z)
                if self.IsLegalNode(Position, AllowedAccess):
                    NewNodes.add(Position)
        NewNodes.update(
            Position
            for Position in AllowedAccess
            if MinimumX <= Position[0] <= MaximumX
            and MinimumY <= Position[1] <= MaximumY
            and MinimumZ <= Position[2] <= MaximumZ
            and Position not in self.ActualBlocks
            and Position not in Nodes
        )
        Nodes.update(NewNodes)
        OrderedNewNodes = sorted(NewNodes)
        for NodeIndex, First in enumerate(OrderedNewNodes):
            if WorkCheck is not None and NodeIndex % 256 == 0:
                WorkCheck({
                    "Phase": "edges",
                    "CompletedNodes": NodeIndex,
                    "TotalNodes": len(OrderedNewNodes),
                    "ReusedEdgeCount": len(Edges),
                })
            for Second in self.Technology.NeighborPositions(First):
                if Second not in Nodes:
                    continue
                if self.CanBuildNeighborPrimitive(First, Second):
                    Edges.add(NormalizeRoutingEdge(First, Second))
        Region = RoutingGraphRegion(Bounds, frozenset(Nodes), frozenset(Edges))
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "complete",
                "CacheHit": False,
                "ReusedRegion": BaseRegion is not None,
                "ReusedNodeCount": len(Nodes) - len(NewNodes),
                "BuiltNodeCount": len(NewNodes),
                "NodeCount": len(Region.Nodes),
                "EdgeCount": len(Region.Edges),
            })
        self._RegionCache[Key] = Region
        self._RegionCacheOrder.append(Key)
        while len(self._RegionCacheOrder) > 8:
            EvictedKey = self._RegionCacheOrder.pop(0)
            if EvictedKey != Key:
                self._RegionCache.pop(EvictedKey, None)
        return Region

    def BuildRouteClaims(
        self,
        Positions: Iterable[Position3],
        WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ) -> RoutingResourceClaims:
        WireCells: set[Position3] = set()
        for PositionIndex, Position in enumerate(Positions, start=1):
            WireCells.add(Position)
            if WorkCheck is not None and PositionIndex % 256 == 0:
                WorkCheck({
                    "Phase": "collect-wire-cells",
                    "ProcessedPositions": PositionIndex,
                    "WireCellCount": len(WireCells),
                })
        SupportCells: set[Position3] = set()
        RequiredAirCells: set[Position3] = set()
        ElectricalCells = set(WireCells)
        for PositionIndex, First in enumerate(WireCells, start=1):
            X, Y, Z = First
            SupportCells.add((X, Y - 1, Z))
            ElectricalCells.update(self.Technology.NeighborPositions(First))
            if WorkCheck is not None and PositionIndex % 256 == 0:
                WorkCheck({
                    "Phase": "expand-route-claims",
                    "ProcessedPositions": PositionIndex,
                    "WireCellCount": len(WireCells),
                })
            for Second in self.Technology.NeighborPositions(First):
                if Second not in WireCells or Second <= First:
                    continue
                Primitive = self.BuildPrimitive(First, Second)
                if Primitive is not None:
                    RequiredAirCells.update(Primitive.Claims.RequiredAirCells)
        return RoutingResourceClaims(
            WireCells=frozenset(WireCells),
            SupportCells=frozenset(SupportCells),
            RequiredAirCells=frozenset(RequiredAirCells),
            ElectricalCells=frozenset(ElectricalCells),
        )

    def BuildIndexedGraph(
        self,
        Region: RoutingGraphRegion,
    ) -> IndexedRoutingResourceGraph:
        """Index every position referenced by a legal region claim deterministically."""
        ResourcePositions = set(Region.Nodes)
        for Position in Region.Nodes:
            ResourcePositions.add((Position[0], Position[1] - 1, Position[2]))
            ResourcePositions.update(self.Technology.NeighborPositions(Position))
        for First, Second in Region.Edges:
            if First[1] != Second[1]:
                Lower = First if First[1] < Second[1] else Second
                ResourcePositions.add((Lower[0], Lower[1] + 1, Lower[2]))
        Ordered = tuple(sorted(ResourcePositions))
        return IndexedRoutingResourceGraph(
            ResourcePositions=Ordered,
            PositionIndices={Position: Index for Index, Position in enumerate(Ordered)},
        )


def NormalizeRoutingEdge(First: Position3, Second: Position3) -> RoutingEdge:
    return (First, Second) if First <= Second else (Second, First)


def FindClaimConflicts(
    ClaimsBySignal: dict[str, RoutingResourceClaims],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[RoutingResourceId, tuple[str, ...]]:
    Conflicts: dict[RoutingResourceId, set[str]] = {}
    Signals = sorted(ClaimsBySignal)
    if WorkCheck is not None:
        WorkCheck({"Phase": "start", "SignalCount": len(Signals)})
    SignalPairChecks = 0
    ConflictResourceChecks = 0
    for Index, FirstSignal in enumerate(Signals):
        First = ClaimsBySignal[FirstSignal]
        for SecondSignal in Signals[Index + 1 :]:
            SignalPairChecks += 1
            if WorkCheck is not None and SignalPairChecks % 64 == 0:
                WorkCheck({
                    "Phase": "signal-pairs",
                    "FirstSignal": FirstSignal,
                    "SecondSignal": SecondSignal,
                    "SignalPairChecks": SignalPairChecks,
                })
            Second = ClaimsBySignal[SecondSignal]
            Electrical = (First.WireCells & Second.ElectricalCells) | (
                Second.WireCells & First.ElectricalCells
            )
            Support = (First.SupportCells & (Second.WireCells | Second.RequiredAirCells)) | (
                Second.SupportCells & (First.WireCells | First.RequiredAirCells)
            )
            Air = (First.RequiredAirCells & Second.WireCells) | (
                Second.RequiredAirCells & First.WireCells
            )
            for Kind, Positions in (
                (RoutingResourceKind.Electrical, Electrical),
                (RoutingResourceKind.Support, Support),
                (RoutingResourceKind.Air, Air),
            ):
                for Position in Positions:
                    ConflictResourceChecks += 1
                    if (
                        WorkCheck is not None
                        and ConflictResourceChecks % 256 == 0
                    ):
                        WorkCheck({
                            "Phase": "conflict-resources",
                            "SignalPairChecks": SignalPairChecks,
                            "ConflictResourceChecks": ConflictResourceChecks,
                        })
                    Conflicts.setdefault(RoutingResourceId(Kind, Position), set()).update(
                        (FirstSignal, SecondSignal)
                    )
    Result = {}
    for ConflictIndex, (Resource, Owners) in enumerate(
        Conflicts.items(),
        start=1,
    ):
        if WorkCheck is not None and ConflictIndex % 256 == 0:
            WorkCheck({
                "Phase": "finalize-conflicts",
                "ProcessedConflicts": ConflictIndex,
                "ConflictCount": len(Conflicts),
            })
        Result[Resource] = tuple(sorted(Owners))
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "SignalPairChecks": SignalPairChecks,
            "ConflictResourceChecks": ConflictResourceChecks,
            "ConflictCount": len(Result),
        })
    return Result


def FindSelfClaimConflicts(
    ClaimsBySignal: dict[str, RoutingResourceClaims],
) -> dict[RoutingResourceId, tuple[str, ...]]:
    Conflicts: dict[RoutingResourceId, tuple[str, ...]] = {}
    for Signal, Claims in ClaimsBySignal.items():
        for Position in Claims.RequiredAirCells & Claims.WireCells:
            Conflicts[RoutingResourceId(RoutingResourceKind.Air, Position)] = (Signal,)
        for Position in Claims.SupportCells & (
            Claims.WireCells | Claims.RequiredAirCells
        ):
            Conflicts[RoutingResourceId(RoutingResourceKind.Support, Position)] = (Signal,)
    return Conflicts


def ValidateLocalRouteClaims(
    ResourceGraph: RoutingResourceGraph,
    Claims: Iterable[LocalRouteClaim],
) -> dict[str, RoutingResourceClaims]:
    """Validate and merge placement-owned trees with routing-grade rules."""
    ClaimsBySignal: dict[str, list[LocalRouteClaim]] = {}
    for Claim in Claims:
        if not Claim.Nodes or Claim.Root not in Claim.Nodes:
            raise ValueError(f"Local route claim {Claim.Signal} has no rooted tree")
        Expected = ResourceGraph.BuildRouteClaims(Claim.Nodes)
        if Expected != Claim.Claims:
            raise ValueError(f"Local route claim {Claim.Signal} has stale resource claims")
        Graph = {Position: [] for Position in Claim.Nodes}
        for First, Second in Claim.Edges:
            if First not in Graph or Second not in Graph:
                raise ValueError(f"Local route claim {Claim.Signal} has an external edge")
            if ResourceGraph.BuildPrimitive(First, Second) is None:
                raise ValueError(f"Local route claim {Claim.Signal} has an illegal edge")
            Graph[First].append(Second)
            Graph[Second].append(First)
        Reached = {Claim.Root}
        Pending = deque((Claim.Root,))
        while Pending:
            Current = Pending.popleft()
            for Neighbor in Graph[Current]:
                if Neighbor not in Reached:
                    Reached.add(Neighbor)
                    Pending.append(Neighbor)
        Missing = set(Claim.ConnectedTargets) - Reached
        if Missing:
            raise ValueError(
                f"Local route claim {Claim.Signal} misses targets {sorted(Missing)}"
            )
        ClaimsBySignal.setdefault(Claim.Signal, []).append(Claim)

    Merged: dict[str, RoutingResourceClaims] = {}
    for Signal, SignalClaims in sorted(ClaimsBySignal.items()):
        Nodes = set().union(*(Claim.Nodes for Claim in SignalClaims))
        Merged[Signal] = ResourceGraph.BuildRouteClaims(Nodes)
    Conflicts = FindClaimConflicts(Merged)
    if Conflicts:
        Resource = min(Conflicts, key=str)
        raise ValueError(
            "Local route claims conflict at "
            f"{Resource}: {','.join(Conflicts[Resource])}"
        )
    if FindSelfClaimConflicts(Merged):
        Resource = min(FindSelfClaimConflicts(Merged), key=str)
        raise ValueError(f"Local route claim self-conflict at {Resource}")
    return Merged

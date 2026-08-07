"""Deterministic placement-wide access fabric construction."""

from __future__ import annotations

from collections import deque
from dataclasses import is_dataclass, replace
from hashlib import sha256
from heapq import heappop, heappush
from math import ceil
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from ..Routing.Actions.Geometry import BuildRoutingResources
from ..Routing.ChannelPlanner import BuildNetRoutingProfiles
from ..Routing.Models import (
    PlacementAccessAssignment,
    PlacementAccessEscapeStub,
    PlacementAccessFabric,
    PlacementAccessTerminalDomain,
    Position3,
)
from ..Routing.ResourceGraph import (
    FindSelfClaimConflicts,
    RoutingResourceClaims,
)
from ..Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from .Rotation import RotatedCellSize


def _BuildShortestFabricEscapePaths(
    Starts: Iterable[Position3],
    IngressNodes: frozenset[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    MaximumPaths: int,
) -> tuple[tuple[Position3, ...], ...]:
    """Return the first deterministic ingress paths in one finite graph."""
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in Edges:
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    for Values in Adjacency.values():
        Values.sort()
    Queue = deque()
    Parent: dict[Position3, Position3 | None] = {}
    for Start in sorted(set(Starts)):
        if Start not in Adjacency or Start in Parent:
            continue
        Parent[Start] = None
        Queue.append(Start)
    Results: list[tuple[Position3, ...]] = []
    while Queue and len(Results) < MaximumPaths:
        Current = Queue.popleft()
        if Current in IngressNodes:
            ReversedPath = []
            Cursor: Position3 | None = Current
            while Cursor is not None:
                ReversedPath.append(Cursor)
                Cursor = Parent[Cursor]
            Results.append(tuple(reversed(ReversedPath)))
        for Next in Adjacency.get(Current, ()):
            if Next in Parent:
                continue
            Parent[Next] = Current
            Queue.append(Next)
    return tuple(Results)


def _BuildShortestLegalFabricEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    FixedPrefix: tuple[Position3, ...],
    ResourceGraph: Any,
) -> tuple[tuple[Position3, ...], ...]:
    """Return one deterministic, geometrically distinct path per ingress."""
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in Edges:
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    for Values in Adjacency.values():
        Values.sort()
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if (
        Start not in Adjacency
        or any(Ingress not in Adjacency for Ingress in OrderedIngresses)
    ):
        return ()
    InitialClaims = ResourceGraph.BuildRouteClaims(FixedPrefix)
    if FindSelfClaimConflicts({"PlacementAccess": InitialClaims}):
        return ()
    Results = []
    for Ingress in OrderedIngresses:
        def Distance(Position: Position3) -> int:
            return sum(
                abs(Position[Index] - Ingress[Index])
                for Index in range(3)
            )

        StartState = (Start, (0, 0, 0))
        Frontier = [(Distance(Start), 0, Start, (0, 0, 0))]
        BestCost = {StartState: 0}
        Parent: dict[
            tuple[Position3, Position3],
            tuple[Position3, Position3] | None,
        ] = {StartState: None}
        ReachedPath: tuple[Position3, ...] | None = None
        while Frontier:
            _Score, Cost, Current, PriorDirection = heappop(Frontier)
            CurrentState = (Current, PriorDirection)
            if Cost != BestCost.get(CurrentState):
                continue
            if Current == Ingress:
                ReversedPath = []
                Cursor: tuple[Position3, Position3] | None = CurrentState
                while Cursor is not None:
                    ReversedPath.append(Cursor[0])
                    Cursor = Parent[Cursor]
                Path = tuple(reversed(ReversedPath))
                CompletePath = _ErasePlacementAccessPathLoops((
                    *FixedPrefix,
                    *Path[1:],
                ))
                if not FindSelfClaimConflicts({
                    "PlacementAccess": ResourceGraph.BuildRouteClaims(
                        CompletePath
                    )
                }):
                    ReachedPath = Path
                    break
            for Next in sorted(
                Adjacency.get(Current, ()),
                key=lambda Position: (Distance(Position), Position),
            ):
                Direction = tuple(
                    Next[Index] - Current[Index]
                    for Index in range(3)
                )
                BendPenalty = int(
                    PriorDirection != (0, 0, 0)
                    and Direction != PriorDirection
                ) * 4
                NextCost = Cost + 1 + BendPenalty
                NextState = (Next, Direction)
                if NextCost >= BestCost.get(NextState, 1 << 60):
                    continue
                BestCost[NextState] = NextCost
                Parent[NextState] = CurrentState
                heappush(
                    Frontier,
                    (
                        NextCost + Distance(Next),
                        NextCost,
                        Next,
                        Direction,
                    ),
                )
        if ReachedPath is not None:
            Results.append(ReachedPath)
    return tuple(Results)


def _BuildSharedLegalFabricEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    FixedPrefix: tuple[Position3, ...],
    ResourceGraph: Any,
) -> tuple[tuple[Position3, ...], ...]:
    """Build the compact shared escape tree used by one-plane fabrics."""
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in Edges:
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    for Values in Adjacency.values():
        Values.sort()
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    RemainingIngresses = set(OrderedIngresses)
    if Start not in Adjacency:
        return ()
    Queue = deque((Start,))
    Parent: dict[Position3, Position3 | None] = {Start: None}
    CompletePathByNode = {Start: tuple(FixedPrefix)}
    ReachedPaths: dict[Position3, tuple[Position3, ...]] = {}
    while Queue and RemainingIngresses:
        Current = Queue.popleft()
        if Current in RemainingIngresses:
            ReversedPath = []
            Cursor: Position3 | None = Current
            while Cursor is not None:
                ReversedPath.append(Cursor)
                Cursor = Parent[Cursor]
            ReachedPaths[Current] = tuple(reversed(ReversedPath))
            RemainingIngresses.remove(Current)
        for Next in Adjacency.get(Current, ()):
            if Next in Parent:
                continue
            CandidatePath = _ErasePlacementAccessPathLoops((
                *CompletePathByNode[Current],
                Next,
            ))
            CandidateClaims = ResourceGraph.BuildRouteClaims(CandidatePath)
            if FindSelfClaimConflicts({"PlacementAccess": CandidateClaims}):
                continue
            Parent[Next] = Current
            CompletePathByNode[Next] = CandidatePath
            Queue.append(Next)
    return tuple(
        ReachedPaths[Ingress]
        for Ingress in OrderedIngresses
        if Ingress in ReachedPaths
    )


def _ErasePlacementAccessPathLoops(
    Path: Iterable[Position3],
) -> tuple[Position3, ...]:
    """Erase complete walk loops without inventing non-graph transitions."""
    Result: list[Position3] = []
    PositionIndex: dict[Position3, int] = {}
    for Position in Path:
        PriorIndex = PositionIndex.get(Position)
        if PriorIndex is not None:
            for Removed in Result[PriorIndex + 1:]:
                PositionIndex.pop(Removed, None)
            del Result[PriorIndex + 1:]
            continue
        PositionIndex[Position] = len(Result)
        Result.append(Position)
    return tuple(Result)


def BuildPlacementAccessFabric(
    Placement: Any,
    *,
    Resources: Any | None = None,
    Technology: RedstoneRoutingTechnology = (
        DefaultRedstoneRoutingTechnology
    ),
    AccessLength: int | None = None,
    LaneCount: int | None = None,
    MaximumEscapeStubsPerTerminal: int | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacementAccessFabric:
    """Construct one fixed access fabric from placement and technology."""
    if LaneCount is not None and LaneCount < 1:
        raise ValueError("placement access fabric requires a positive lane count")
    if (
        MaximumEscapeStubsPerTerminal is not None
        and MaximumEscapeStubsPerTerminal < 1
    ):
        raise ValueError("placement access fabric requires escape candidates")
    Placed = Placement.Placed
    Resources = Resources or BuildRoutingResources(Placed, WorkCheck=WorkCheck)
    Profiles = BuildNetRoutingProfiles(
        Placed,
        AccessLength=(Technology.AccessLength if AccessLength is None else AccessLength),
    )
    Gates = tuple(Placed.PlacedGates)
    if not Gates:
        return PlacementAccessFabric(
            FabricFingerprint=sha256(b"empty-placement-access-fabric-v1").hexdigest()[:16],
            Nodes=(),
            Edges=(),
            IngressNodes=(),
            PhysicalClaims=Resources.ResourceGraph.BuildRouteClaims(()),
            CapacityResourceIds=(),
            TerminalDomains=(),
            TopologyKind="fixed-access-band-v1",
            Complete=True,
            Technology=Technology,
        )
    TrackPitch = Technology.TrackPitch
    MinimumX = min(Gate.X for Gate in Gates)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Gates
    )
    MinimumZ = min(Gate.Z for Gate in Gates)
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Gates
    )
    BaseY = min(Gate.Y for Gate in Gates)
    MaximumFabricLayer = max(0, int(Placement.LayerCount) - 1)
    FabricLayerCount = min(
        max(1, int(Placement.LayerCount)),
        max(1, ceil(len(Profiles) / 6)),
    )
    FabricLayers = tuple(range(
        MaximumFabricLayer - FabricLayerCount + 1,
        MaximumFabricLayer + 1,
    ))
    FabricYs = tuple(
        Technology.RoutingY(BaseY, Layer)
        for Layer in FabricLayers
    )
    Margin = TrackPitch * 2
    TerminalPathByIdentity = {
        (str(Signal), tuple(Terminal)): tuple(Path)
        for Signal, Profile in sorted(Profiles.items())
        for Terminal, Path in (
            (Profile.Root, Profile.SourceAccessPath),
            *tuple(sorted(Profile.TargetAccessPaths.items())),
        )
    }
    TerminalPaths = tuple(
        (Signal, Terminal, TerminalPathByIdentity[(Signal, Terminal)])
        for Signal, Terminal in sorted(TerminalPathByIdentity)
    )
    EffectiveLaneCount = (
        min(16, max(4, len(TerminalPaths)))
        if LaneCount is None
        else LaneCount
    )
    EffectiveMaximumEscapeStubs = (
        min(
            max(3, ceil(4 / FabricLayerCount)),
            EffectiveLaneCount,
        ) * FabricLayerCount
        if MaximumEscapeStubsPerTerminal is None
        else MaximumEscapeStubsPerTerminal
    )
    AllowedAccess = frozenset(
        Position
        for _Signal, _Terminal, Path in TerminalPaths
        for Position in Path
    )
    Region = Resources.ResourceGraph.BuildRegion(
        (
            MinimumX - Margin,
            MaximumX + Margin,
            BaseY,
            max(FabricYs),
            MinimumZ - Margin,
            MaximumZ + Margin,
        ),
        AllowedAccess=AllowedAccess,
        WorkCheck=WorkCheck,
    )
    LaneCoordinates = tuple(
        MinimumZ - Margin + TrackPitch * Index
        for Index in range(EffectiveLaneCount)
    )
    SpineCoordinates = tuple(range(
        MinimumX - Margin,
        MaximumX + Margin + 1,
        TrackPitch,
    ))
    MinimumLaneZ = min(LaneCoordinates)
    MaximumLaneZ = max(LaneCoordinates)
    FabricNodes = tuple(sorted(
        Position
        for Position in Region.Nodes
        if (
            Position[1] in FabricYs
            and (
                Position[2] in LaneCoordinates
                or (
                    Position[0] in SpineCoordinates
                    and MinimumLaneZ <= Position[2] <= MaximumLaneZ
                )
            )
        )
    ))
    FabricNodeSet = frozenset(FabricNodes)
    FabricEdges = tuple(sorted(
        (First, Second)
        for First, Second in Region.Edges
        if First in FabricNodeSet and Second in FabricNodeSet
    ))
    IngressNodes = tuple(sorted(
        Position
        for Position in FabricNodes
        if (
            Position[2] in LaneCoordinates
            and (Position[0] - (MinimumX - Margin)) % TrackPitch == 0
        )
    ))
    TerminalDomains = []
    RegionNodeSet = frozenset(Region.Nodes)
    for TerminalIndex, (Signal, Terminal, AccessPath) in enumerate(
        TerminalPaths
    ):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "placement-access-terminal-domain",
                "CompletedTerminalCount": TerminalIndex,
                "TerminalCount": len(TerminalPaths),
                "Signal": Signal,
            })
        EscapePrefix = list(AccessPath)
        if len(AccessPath) >= 2:
            Delta = tuple(
                AccessPath[-1][Index] - AccessPath[-2][Index]
                for Index in range(3)
            )
            for Offset in range(1, TrackPitch + 1):
                Extension = tuple(
                    AccessPath[-1][Index] + Delta[Index] * Offset
                    for Index in range(3)
                )
                if Extension not in RegionNodeSet:
                    break
                EscapePrefix.append(Extension)
        Starts = tuple(
            Position for Position in reversed(EscapePrefix)
            if Position in RegionNodeSet
        )[:1]
        RankedIngressNodes = tuple(sorted(
            IngressNodes,
            key=lambda Position: (
                min(
                    abs(Position[0] - Start[0])
                    + abs(Position[1] - Start[1])
                    + abs(Position[2] - Start[2])
                    for Start in Starts
                ) if Starts else 1 << 30,
                Position,
            ),
        ))
        DiverseIngressNodes = []
        SeenLaneCoordinates = set()
        for Ingress in RankedIngressNodes:
            LaneIdentity = (Ingress[1], Ingress[2])
            if LaneIdentity in SeenLaneCoordinates:
                continue
            SeenLaneCoordinates.add(LaneIdentity)
            DiverseIngressNodes.append(Ingress)
            if len(DiverseIngressNodes) >= EffectiveMaximumEscapeStubs:
                break
        Paths = (
            (
                _BuildSharedLegalFabricEscapePaths
                if FabricLayerCount == 1
                else _BuildShortestLegalFabricEscapePaths
            )(
                Starts[0],
                DiverseIngressNodes,
                Region.Edges,
                tuple(EscapePrefix),
                Resources.ResourceGraph,
            )
            if Starts
            else ()
        )
        Stubs = []
        for Path in Paths:
            StubPath = _ErasePlacementAccessPathLoops((
                *EscapePrefix,
                *Path[1:],
            ))
            Claims = Resources.ResourceGraph.BuildRouteClaims(StubPath)
            if FindSelfClaimConflicts({Signal: Claims}):
                continue
            Stubs.append(PlacementAccessEscapeStub(
                Terminal=Terminal,
                Ingress=Path[-1],
                Path=StubPath,
                PhysicalClaims=Claims,
                CapacityResourceIds=tuple(sorted(
                    Claims.ResourceIds,
                    key=str,
                )),
                Complete=True,
            ))
        Stubs = tuple(Stubs)
        TerminalDomains.append(PlacementAccessTerminalDomain(
            Signal=Signal,
            Terminal=Terminal,
            EscapeStubs=Stubs,
            Complete=bool(Stubs),
            IncompleteReason=("" if Stubs else "no-legal-fabric-escape"),
        ))
    PhysicalClaims = Resources.ResourceGraph.BuildRouteClaims(FabricNodes)
    Complete = all(Domain.Complete for Domain in TerminalDomains)
    CanonicalIdentity = (
        "fixed-access-band-v1",
        getattr(Technology, "TechnologyVersion", ""),
        repr(Technology),
        FabricLayers,
        FabricNodes,
        FabricEdges,
        tuple(
            (
                Domain.Signal,
                Domain.Terminal,
                tuple(
                    (Stub.Ingress, Stub.Path, Stub.CapacityResourceIds)
                    for Stub in Domain.EscapeStubs
                ),
                Domain.Complete,
            )
            for Domain in TerminalDomains
        ),
        Complete,
    )
    return PlacementAccessFabric(
        FabricFingerprint=sha256(repr(CanonicalIdentity).encode("utf-8")).hexdigest()[:16],
        Nodes=FabricNodes,
        Edges=FabricEdges,
        IngressNodes=IngressNodes,
        PhysicalClaims=PhysicalClaims,
        CapacityResourceIds=tuple(sorted(PhysicalClaims.ResourceIds, key=str)),
        TerminalDomains=tuple(TerminalDomains),
        TopologyKind="fixed-access-band-v1",
        Complete=Complete,
        IncompleteReason=("" if Complete else "incomplete-terminal-escape-domain"),
        Technology=Technology,
    )


def AttachPlacementAccessFabric(
    Placement: Any,
    Fabric: PlacementAccessFabric,
) -> Any:
    """Attach one immutable fabric to both placement stage boundaries."""
    AttachedPlaced = (
        replace(Placement.Placed, PlacementAccessFabric=Fabric)
        if is_dataclass(Placement.Placed)
        else SimpleNamespace(**{
            **vars(Placement.Placed),
            "PlacementAccessFabric": Fabric,
        })
    )
    return (
        replace(
            Placement,
            Placed=AttachedPlaced,
            PlacementAccessFabric=Fabric,
        )
        if is_dataclass(Placement)
        else SimpleNamespace(**{
            **vars(Placement),
            "Placed": AttachedPlaced,
            "PlacementAccessFabric": Fabric,
        })
    )


def _MergePlacementAccessClaims(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> RoutingResourceClaims:
    return RoutingResourceClaims(
        WireCells=First.WireCells | Second.WireCells,
        SupportCells=First.SupportCells | Second.SupportCells,
        RequiredAirCells=(
            First.RequiredAirCells | Second.RequiredAirCells
        ),
        ElectricalCells=First.ElectricalCells | Second.ElectricalCells,
    )


def _PlacementAccessClaimsConflict(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> bool:
    return bool(
        (First.WireCells & Second.ElectricalCells)
        or (Second.WireCells & First.ElectricalCells)
        or (
            First.SupportCells
            & (Second.WireCells | Second.RequiredAirCells)
        )
        or (
            Second.SupportCells
            & (First.WireCells | First.RequiredAirCells)
        )
        or (First.RequiredAirCells & Second.WireCells)
        or (Second.RequiredAirCells & First.WireCells)
    )


def SolvePlacementAccessFabricCapacity(
    Fabric: PlacementAccessFabric,
    *,
    MaximumExpansions: int = 50_000,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacementAccessAssignment:
    """Select one compatible escape per terminal in one bounded solve."""
    if MaximumExpansions < 1:
        raise ValueError("placement access capacity requires a work cap")
    if not Fabric.Complete:
        return PlacementAccessAssignment(
            FabricFingerprint=Fabric.FabricFingerprint,
            AssignmentFingerprint="",
            SelectedStubIndices=(),
            CapacityResourceIds=(),
            ExpansionCount=0,
            Success=False,
            Complete=False,
            IncompleteReason=Fabric.IncompleteReason,
        )
    Selected: dict[int, int] = {}
    ClaimsBySignal: dict[str, RoutingResourceClaims] = {}
    SelectedSignalRoutes: dict[str, tuple[Position3, ...]] = {}
    ExpansionCount = 0
    Exhausted = False
    ConflictSignals: set[str] = set()
    MaximumRoutedSignalCount = 0
    FrontierSignals: tuple[str, ...] = ()
    FirstUnroutableSignal = ""
    FabricNodeSet = frozenset(Fabric.Nodes)
    FabricEdgeSet = frozenset(
        tuple(sorted((First, Second))) for First, Second in Fabric.Edges
    )
    EffectiveTechnology = (
        Fabric.Technology or DefaultRedstoneRoutingTechnology
    )
    FabricYValues = tuple(sorted({Position[1] for Position in Fabric.Nodes}))
    FabricZValuesByY = {
        FabricY: tuple(sorted({
            Position[2]
            for Position in Fabric.Nodes
            if Position[1] == FabricY
        }))
        for FabricY in FabricYValues
    }
    TrunkCoordinatesByY = {
        FabricY: tuple(sorted(
            X
            for X in {
                Position[0]
                for Position in Fabric.Nodes
                if Position[1] == FabricY
            }
            if all(
                (X, FabricY, Z) in FabricNodeSet
                for Z in FabricZValuesByY[FabricY]
            )
        ))
        for FabricY in FabricYValues
    }
    LaneCoordinatesByY = {
        FabricY: tuple(sorted(
            Z
            for Z in {
                Position[2]
                for Position in Fabric.Nodes
                if Position[1] == FabricY
            }
            if all(
                (X, FabricY, Z) in FabricNodeSet
                for X in {
                    Position[0]
                    for Position in Fabric.Nodes
                    if Position[1] == FabricY
                }
            )
        ))
        for FabricY in FabricYValues
    }
    TerminalDomainCountBySignal: dict[str, int] = {}
    for Domain in Fabric.TerminalDomains:
        TerminalDomainCountBySignal[Domain.Signal] = (
            TerminalDomainCountBySignal.get(Domain.Signal, 0) + 1
        )

    def BuildSignalRouteCandidates(
        Signal: str,
        Ingresses: tuple[Position3, ...],
    ) -> tuple[tuple[tuple[Position3, ...], RoutingResourceClaims], ...]:
        if len(Ingresses) <= 1:
            Nodes = tuple(Ingresses)
            return ((Nodes, RoutingResourceClaims()),)
        IngressLayers = {Position[1] for Position in Ingresses}
        if len(IngressLayers) != 1:
            return ()
        FabricY = next(iter(IngressLayers))
        MinimumZ = min(Position[2] for Position in Ingresses)
        MaximumZ = max(Position[2] for Position in Ingresses)
        MinimumX = min(Position[0] for Position in Ingresses)
        MaximumX = max(Position[0] for Position in Ingresses)
        Results = []
        SeenNodeSets: set[frozenset[Position3]] = set()

        def RetainRouteNodes(Nodes: set[Position3]) -> None:
            NodeSet = frozenset(Nodes)
            if NodeSet in SeenNodeSets or not NodeSet <= FabricNodeSet:
                return
            if any(
                tuple(sorted((First, Second))) not in FabricEdgeSet
                for First in NodeSet
                for Second in (
                    (First[0] + 1, First[1], First[2]),
                    (First[0], First[1], First[2] + 1),
                )
                if Second in NodeSet
            ):
                return
            OrderedNodes = tuple(sorted(NodeSet))
            Claims = RoutingResourceClaims(
                WireCells=NodeSet,
                SupportCells=frozenset(
                    (X, Y - 1, Z) for X, Y, Z in NodeSet
                ),
                RequiredAirCells=frozenset(),
                ElectricalCells=frozenset(
                    Position
                    for Node in NodeSet
                    for Position in (
                        Node,
                        *EffectiveTechnology.NeighborPositions(Node),
                    )
                ),
            )
            if FindSelfClaimConflicts({Signal: Claims}):
                return
            SeenNodeSets.add(NodeSet)
            Results.append((OrderedNodes, Claims))

        for TrunkX in TrunkCoordinatesByY.get(FabricY, ()):
            Nodes = {
                (TrunkX, FabricY, Z)
                for Z in range(MinimumZ, MaximumZ + 1)
            }
            for IngressX, _IngressY, IngressZ in Ingresses:
                Nodes.update(
                    (X, FabricY, IngressZ)
                    for X in range(
                        min(IngressX, TrunkX),
                        max(IngressX, TrunkX) + 1,
                    )
                )
            RetainRouteNodes(Nodes)
        for TrunkZ in LaneCoordinatesByY.get(FabricY, ()):
            Nodes = {
                (X, FabricY, TrunkZ)
                for X in range(MinimumX, MaximumX + 1)
            }
            for IngressX, _IngressY, IngressZ in Ingresses:
                Nodes.update(
                    (IngressX, FabricY, Z)
                    for Z in range(
                        min(IngressZ, TrunkZ),
                        max(IngressZ, TrunkZ) + 1,
                    )
                )
            RetainRouteNodes(Nodes)
        return tuple(Results)

    def SelectCompleteSignalRoutes() -> bool:
        nonlocal ExpansionCount, Exhausted, SelectedSignalRoutes
        IngressesBySignal: dict[str, list[Position3]] = {}
        for DomainIndex, StubIndex in Selected.items():
            Domain = Fabric.TerminalDomains[DomainIndex]
            IngressesBySignal.setdefault(Domain.Signal, []).append(
                Domain.EscapeStubs[StubIndex].Ingress
            )
        RouteDomains = {
            Signal: BuildSignalRouteCandidates(
                Signal,
                tuple(sorted(set(Ingresses))),
            )
            for Signal, Ingresses in IngressesBySignal.items()
        }
        if any(not Values for Values in RouteDomains.values()):
            ConflictSignals.update(
                Signal for Signal, Values in RouteDomains.items() if not Values
            )
            return False
        RouteClaimsBySignal: dict[str, RoutingResourceClaims] = {}
        RouteNodesBySignal: dict[str, tuple[Position3, ...]] = {}

        def SelectRoute() -> bool:
            nonlocal ExpansionCount, Exhausted, SelectedSignalRoutes
            if len(RouteNodesBySignal) == len(RouteDomains):
                SelectedSignalRoutes = dict(RouteNodesBySignal)
                return True
            Ranked = []
            for Signal, Candidates in RouteDomains.items():
                if Signal in RouteNodesBySignal:
                    continue
                Compatible = []
                for Nodes, RouteClaims in Candidates:
                    CombinedClaims = _MergePlacementAccessClaims(
                        ClaimsBySignal[Signal],
                        RouteClaims,
                    )
                    if FindSelfClaimConflicts({Signal: CombinedClaims}):
                        continue
                    if any(
                        OtherSignal != Signal
                        and _PlacementAccessClaimsConflict(
                            CombinedClaims,
                            _MergePlacementAccessClaims(
                                ClaimsBySignal[OtherSignal],
                                RouteClaimsBySignal.get(
                                    OtherSignal,
                                    RoutingResourceClaims(),
                                ),
                            ),
                        )
                        for OtherSignal in ClaimsBySignal
                    ):
                        continue
                    Compatible.append((Nodes, RouteClaims))
                if not Compatible:
                    ConflictSignals.add(Signal)
                    return False
                Ranked.append((len(Compatible), Signal, Compatible))
            _Count, Signal, Compatible = min(Ranked)
            for Nodes, RouteClaims in Compatible:
                if ExpansionCount >= MaximumExpansions:
                    Exhausted = True
                    return False
                ExpansionCount += 1
                RouteNodesBySignal[Signal] = Nodes
                RouteClaimsBySignal[Signal] = RouteClaims
                if SelectRoute():
                    return True
                RouteNodesBySignal.pop(Signal, None)
                RouteClaimsBySignal.pop(Signal, None)
            return False

        return SelectRoute()

    def CompatibleStubs(
        DomainIndex: int,
    ) -> tuple[tuple[int, RoutingResourceClaims], ...]:
        Domain = Fabric.TerminalDomains[DomainIndex]
        ExistingSignalClaims = ClaimsBySignal.get(
            Domain.Signal,
            RoutingResourceClaims(),
        )
        Compatible = []
        for StubIndex, Stub in enumerate(Domain.EscapeStubs):
            MergedClaims = _MergePlacementAccessClaims(
                ExistingSignalClaims,
                Stub.PhysicalClaims,
            )
            BlockingSignals = tuple(
                Signal
                for Signal, Claims in ClaimsBySignal.items()
                if (
                    Signal != Domain.Signal
                    and _PlacementAccessClaimsConflict(
                        MergedClaims,
                        Claims,
                    )
                )
            )
            if BlockingSignals:
                ConflictSignals.update((Domain.Signal, *BlockingSignals))
                continue
            Compatible.append((StubIndex, MergedClaims))
        return tuple(Compatible)

    def Search() -> bool:
        nonlocal ExpansionCount, Exhausted
        nonlocal MaximumRoutedSignalCount, FrontierSignals
        nonlocal FirstUnroutableSignal
        if len(SelectedSignalRoutes) > MaximumRoutedSignalCount:
            MaximumRoutedSignalCount = len(SelectedSignalRoutes)
            FrontierSignals = tuple(sorted(SelectedSignalRoutes))
        if WorkCheck is not None and ExpansionCount % 256 == 0:
            WorkCheck({
                "Phase": "placement-access-capacity-search",
                "ExpansionCount": ExpansionCount,
                "MaximumExpansions": MaximumExpansions,
                "SelectedTerminalCount": len(Selected),
                "TerminalCount": len(Fabric.TerminalDomains),
            })
        if len(Selected) == len(Fabric.TerminalDomains):
            return len(SelectedSignalRoutes) == len(
                TerminalDomainCountBySignal
            )
        RankedDomains = []
        for DomainIndex, Domain in enumerate(Fabric.TerminalDomains):
            if DomainIndex in Selected:
                continue
            Compatible = CompatibleStubs(DomainIndex)
            if not Compatible:
                return False
            RankedDomains.append((
                0 if Domain.Signal in ClaimsBySignal else 1,
                len(Compatible),
                Domain.Signal,
                Domain.Terminal,
                DomainIndex,
                Compatible,
            ))
        (
            _PartiallySelectedRank,
            _CompatibleCount,
            _Signal,
            _Terminal,
            DomainIndex,
            Compatible,
        ) = min(RankedDomains)
        Domain = Fabric.TerminalDomains[DomainIndex]
        ExistingSignalClaims = ClaimsBySignal.get(
            Domain.Signal,
            RoutingResourceClaims(),
        )
        for StubIndex, MergedClaims in Compatible:
            if ExpansionCount >= MaximumExpansions:
                Exhausted = True
                return False
            ExpansionCount += 1
            Selected[DomainIndex] = StubIndex
            ClaimsBySignal[Domain.Signal] = MergedClaims
            SelectedSignalTerminalCount = sum(
                Fabric.TerminalDomains[Index].Signal == Domain.Signal
                for Index in Selected
            )
            if (
                SelectedSignalTerminalCount
                == TerminalDomainCountBySignal[Domain.Signal]
            ):
                Ingresses = tuple(sorted({
                    Fabric.TerminalDomains[Index]
                    .EscapeStubs[Selected[Index]].Ingress
                    for Index in Selected
                    if Fabric.TerminalDomains[Index].Signal == Domain.Signal
                }))
                RouteCandidates = BuildSignalRouteCandidates(
                    Domain.Signal,
                    Ingresses,
                )
                RoutedCurrentSignal = False
                for RouteNodes, RouteClaims in RouteCandidates:
                    if ExpansionCount >= MaximumExpansions:
                        Exhausted = True
                        break
                    CompleteClaims = _MergePlacementAccessClaims(
                        MergedClaims,
                        RouteClaims,
                    )
                    if FindSelfClaimConflicts({
                        Domain.Signal: CompleteClaims
                    }):
                        continue
                    BlockingSignals = tuple(
                        OtherSignal
                        for OtherSignal, OtherClaims
                        in ClaimsBySignal.items()
                        if (
                            OtherSignal != Domain.Signal
                            and _PlacementAccessClaimsConflict(
                                CompleteClaims,
                                OtherClaims,
                            )
                        )
                    )
                    if BlockingSignals:
                        ConflictSignals.update((
                            Domain.Signal,
                            *BlockingSignals,
                        ))
                        continue
                    ExpansionCount += 1
                    RoutedCurrentSignal = True
                    ClaimsBySignal[Domain.Signal] = CompleteClaims
                    SelectedSignalRoutes[Domain.Signal] = RouteNodes
                    if Search():
                        return True
                    SelectedSignalRoutes.pop(Domain.Signal, None)
                    ClaimsBySignal[Domain.Signal] = MergedClaims
                if not RoutedCurrentSignal and not FirstUnroutableSignal:
                    FirstUnroutableSignal = Domain.Signal
            elif Search():
                return True
            Selected.pop(DomainIndex, None)
            if ExistingSignalClaims.ResourceIds:
                ClaimsBySignal[Domain.Signal] = ExistingSignalClaims
            else:
                ClaimsBySignal.pop(Domain.Signal, None)
        return False

    Success = Search()
    SelectedValues = tuple(
        (
            Fabric.TerminalDomains[Index].Signal,
            Fabric.TerminalDomains[Index].Terminal,
            Selected[Index],
        )
        for Index in sorted(Selected)
    ) if Success else ()
    CapacityResourceIds = tuple(sorted({
        Resource
        for Signal, SignalClaims in ClaimsBySignal.items()
        for Resource in _MergePlacementAccessClaims(
            SignalClaims,
            (
                RoutingResourceClaims(
                    WireCells=frozenset(SelectedSignalRoutes.get(Signal, ())),
                    SupportCells=frozenset(
                        (X, Y - 1, Z)
                        for X, Y, Z in SelectedSignalRoutes.get(Signal, ())
                    ),
                    RequiredAirCells=frozenset(),
                    ElectricalCells=frozenset(
                        Position
                        for Node in SelectedSignalRoutes.get(Signal, ())
                        for Position in (
                            Node,
                            *EffectiveTechnology.NeighborPositions(Node),
                        )
                    ),
                )
            ),
        ).ResourceIds
    }, key=str)) if Success else ()
    AssignmentFingerprint = (
        sha256(repr((
            Fabric.FabricFingerprint,
            SelectedValues,
            tuple(sorted(SelectedSignalRoutes.items())),
            CapacityResourceIds,
        )).encode("utf-8")).hexdigest()[:16]
        if Success
        else ""
    )
    return PlacementAccessAssignment(
        FabricFingerprint=Fabric.FabricFingerprint,
        AssignmentFingerprint=AssignmentFingerprint,
        SelectedStubIndices=SelectedValues,
        CapacityResourceIds=CapacityResourceIds,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=not Exhausted,
        ConflictSignals=(() if Success else tuple(sorted(ConflictSignals))),
        FrontierSignals=(() if Success else FrontierSignals),
        MaximumRoutedSignalCount=MaximumRoutedSignalCount,
        FirstUnroutableSignal=("" if Success else FirstUnroutableSignal),
        IncompleteReason=("work-cap-exhausted" if Exhausted else ""),
        SignalRoutes=tuple(sorted(SelectedSignalRoutes.items())) if Success else (),
    )


def AttachPlacementAccessAssignment(
    Placement: Any,
    Assignment: PlacementAccessAssignment,
) -> Any:
    """Freeze the selected access witness at both placement boundaries."""
    AttachedPlaced = (
        replace(
            Placement.Placed,
            PlacementAccessAssignment=Assignment,
        )
        if is_dataclass(Placement.Placed)
        else SimpleNamespace(**{
            **vars(Placement.Placed),
            "PlacementAccessAssignment": Assignment,
        })
    )
    return (
        replace(
            Placement,
            Placed=AttachedPlaced,
            PlacementAccessAssignment=Assignment,
        )
        if is_dataclass(Placement)
        else SimpleNamespace(**{
            **vars(Placement),
            "Placed": AttachedPlaced,
            "PlacementAccessAssignment": Assignment,
        })
    )

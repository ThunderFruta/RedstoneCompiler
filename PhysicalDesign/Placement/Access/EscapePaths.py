"""Bounded legal escape-path enumeration for placement access."""

from __future__ import annotations

from collections import (
    deque,
)
from hashlib import (
    sha256,
)
from heapq import (
    heappop,
    heappush,
)
from typing import (
    Any,
    Callable,
    Iterable,
)
from PhysicalDesign.Contracts.Core import Position3
from PhysicalDesign.Constraints.PhysicalClaims import MandatoryClaimsConflict
from PhysicalDesign.Resources.ResourceGraph import FindSelfClaimConflicts
from PhysicalDesign.Redstone.Technology import RedstoneRoutingTechnology
from .Geometry import (
    _BuildDerivedPerimeterShellInputFingerprint,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .Geometry import (
        DerivedPerimeterFabricShell,
        _AccessFabricWorkBudget,
    )


def _ValidateDerivedPerimeterFabricShell(
    Shell: DerivedPerimeterFabricShell,
    Placement: Any,
    *,
    Resources: Any,
    Technology: RedstoneRoutingTechnology,
    AccessRingTrackCount: int,
    AccessLength: int,
    BoundarySignals: frozenset[str] | None,
    Assignment: Any,
) -> None:
    """Reject a shell whose immutable inputs differ from this request."""
    if Shell.AccessRingTrackCount != AccessRingTrackCount:
        raise ValueError("derived perimeter shell track count does not match")
    if Shell.AccessLength != AccessLength:
        raise ValueError("derived perimeter shell access length does not match")
    ExpectedTechnologyFingerprint = sha256(repr((
        str(getattr(Technology, "TechnologyVersion", "")),
        repr(Technology),
    )).encode("utf-8")).hexdigest()[:16]
    if Shell.TechnologyFingerprint != ExpectedTechnologyFingerprint:
        raise ValueError("derived perimeter shell technology does not match")
    ExpectedAssignmentFingerprint = str(getattr(
        Assignment,
        "AssignmentFingerprint",
        "",
    ))
    if (
        Shell.PerimeterSlotAssignmentFingerprint
        != ExpectedAssignmentFingerprint
    ):
        raise ValueError("derived perimeter shell assignment does not match")
    ExpectedInputFingerprint = _BuildDerivedPerimeterShellInputFingerprint(
        Placement,
        Resources=Resources,
        Technology=Technology,
        AccessRingTrackCount=AccessRingTrackCount,
        AccessLength=AccessLength,
        BoundarySignals=BoundarySignals,
        Assignment=Assignment,
    )
    if Shell.InputFingerprint != ExpectedInputFingerprint:
        raise ValueError("derived perimeter shell input identity does not match")

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

def _BuildIndependentShortestFabricEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    AlternateIngresses: frozenset[Position3] = frozenset(),
    Adjacency: dict[Position3, tuple[Position3, ...]] | None = None,
) -> tuple[tuple[Position3, ...], ...]:
    """Build one deterministic shortest path independently per ingress."""
    if Adjacency is None:
        MutableAdjacency: dict[Position3, list[Position3]] = {}
        for First, Second in Edges:
            MutableAdjacency.setdefault(First, []).append(Second)
            MutableAdjacency.setdefault(Second, []).append(First)
        Adjacency = {
            Position: tuple(sorted(Values))
            for Position, Values in MutableAdjacency.items()
        }
    if Start not in Adjacency:
        return ()
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if not AlternateIngresses:
        # One deterministic breadth-first tree provides a shortest path to
        # every retained ingress.  Re-running A* once per ingress multiplies
        # identical geometry work and can delay deadline observation.
        Remaining = {
            Ingress for Ingress in OrderedIngresses
            if Ingress in Adjacency
        }
        Parent: dict[Position3, Position3 | None] = {Start: None}
        Frontier = deque((Start,))
        while Frontier and Remaining:
            Current = Frontier.popleft()
            Remaining.discard(Current)
            for Next in Adjacency.get(Current, ()):
                if Next in Parent:
                    continue
                Parent[Next] = Current
                Frontier.append(Next)
        Results = []
        for Ingress in OrderedIngresses:
            if Ingress not in Parent:
                continue
            ReversedPath = []
            Cursor: Position3 | None = Ingress
            while Cursor is not None:
                ReversedPath.append(Cursor)
                Cursor = Parent[Cursor]
            Results.append(tuple(reversed(ReversedPath)))
        return tuple(Results)
    Results = []
    for Ingress in OrderedIngresses:
        if Ingress not in Adjacency:
            continue

        def Distance(Position: Position3) -> int:
            return sum(
                abs(Position[Index] - Ingress[Index])
                for Index in range(3)
            )

        for ReverseTieBreak in (
            (False, True) if Ingress in AlternateIngresses else (False,)
        ):
            def TieKey(Position: Position3) -> tuple[int, int, int]:
                return tuple(
                    -Value if ReverseTieBreak else Value
                    for Value in Position
                )

            Frontier = [(Distance(Start), 0, TieKey(Start), Start)]
            BestCost = {Start: 0}
            Parent: dict[Position3, Position3 | None] = {Start: None}
            while Frontier:
                _Score, Cost, _Tie, Current = heappop(Frontier)
                if Cost != BestCost.get(Current):
                    continue
                if Current == Ingress:
                    break
                for Next in sorted(
                    Adjacency.get(Current, ()),
                    key=lambda Position: (
                        Distance(Position),
                        TieKey(Position),
                    ),
                ):
                    NextCost = Cost + 1
                    if NextCost >= BestCost.get(Next, 1 << 60):
                        continue
                    BestCost[Next] = NextCost
                    Parent[Next] = Current
                    heappush(
                        Frontier,
                        (
                            NextCost + Distance(Next),
                            NextCost,
                            TieKey(Next),
                            Next,
                        ),
                    )
            if Ingress not in Parent:
                continue
            ReversedPath = []
            Cursor: Position3 | None = Ingress
            while Cursor is not None:
                ReversedPath.append(Cursor)
                Cursor = Parent[Cursor]
            Path = tuple(reversed(ReversedPath))
            if Path not in Results:
                Results.append(Path)
    return tuple(Results)

def _BuildDerivedPerimeterCycleRouteNodeSets(
    Ingresses: tuple[Position3, ...],
    FabricY: int,
    FabricEdges: Iterable[tuple[Position3, Position3]],
) -> tuple[tuple[Position3, ...], ...] | None:
    """Enumerate the exact terminal-spanning arc domain of one ring cycle.

    A derived perimeter fabric is a collection of disjoint, planar cycles:
    one for each selected routing layer and ring track.  Given the ingress
    points of one signal, every minimal connected subgraph of a cycle is the
    cycle with one terminal-free gap removed.  Enumerating those gaps is
    finite, deterministic, and complete for this topology; it avoids making
    a single breadth-first tie-break into an accidental placement policy.

    ``None`` means the claimed ring component is not a cycle, so its route
    domain cannot be treated as complete.  An empty tuple means the selected
    ingresses are on different ring components, which is a complete rejection
    of that particular stub selection.
    """
    UniqueIngresses = tuple(sorted(set(Ingresses)))
    if len(UniqueIngresses) <= 1:
        return (UniqueIngresses,)
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in FabricEdges:
        if First[1] != FabricY or Second[1] != FabricY:
            continue
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    if any(Ingress not in Adjacency for Ingress in UniqueIngresses):
        return ()
    for Neighbors in Adjacency.values():
        Neighbors.sort()

    Component: set[Position3] = set()
    Frontier = deque((UniqueIngresses[0],))
    while Frontier:
        Current = Frontier.popleft()
        if Current in Component:
            continue
        Component.add(Current)
        Frontier.extend(
            Next for Next in Adjacency[Current]
            if Next not in Component
        )
    if any(Ingress not in Component for Ingress in UniqueIngresses):
        return ()
    if any(len(Adjacency[Position]) != 2 for Position in Component):
        return None

    Cycle: list[Position3] = []
    Start = min(Component)
    Previous: Position3 | None = None
    Current = Start
    while True:
        if Current in Cycle:
            return None
        Cycle.append(Current)
        Choices = tuple(
            Next for Next in Adjacency[Current]
            if Next != Previous
        )
        if not Choices:
            return None
        Next = min(Choices)
        if Next == Start:
            break
        Previous, Current = Current, Next
    if len(Cycle) != len(Component):
        return None

    CycleLength = len(Cycle)
    CycleIndex = {Position: Index for Index, Position in enumerate(Cycle)}
    TerminalIndices = tuple(sorted(
        CycleIndex[Ingress] for Ingress in UniqueIngresses
    ))
    Results: list[tuple[Position3, ...]] = []
    Seen = set()
    for StartIndex, EndIndex in zip(
        TerminalIndices,
        (*TerminalIndices[1:], TerminalIndices[0]),
    ):
        GapLength = (EndIndex - StartIndex) % CycleLength
        if GapLength == 0:
            return None
        GapInterior = {
            Cycle[(StartIndex + Offset) % CycleLength]
            for Offset in range(1, GapLength)
        }
        Nodes = tuple(sorted(
            Position for Position in Cycle
            if Position not in GapInterior
        ))
        if Nodes in Seen:
            continue
        Seen.add(Nodes)
        Results.append(Nodes)
    return tuple(sorted(Results, key=lambda Nodes: (len(Nodes), Nodes)))

def _BuildShortestLegalFabricEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    FixedPrefix: tuple[Position3, ...],
    ResourceGraph: Any,
    Adjacency: dict[Position3, tuple[Position3, ...]] | None = None,
    ForeignFixedClaims: tuple[Any, ...] = (),
) -> tuple[tuple[Position3, ...], ...]:
    """Return one deterministic, geometrically distinct path per ingress."""
    if Adjacency is None:
        MutableAdjacency: dict[Position3, list[Position3]] = {}
        for First, Second in Edges:
            MutableAdjacency.setdefault(First, []).append(Second)
            MutableAdjacency.setdefault(Second, []).append(First)
        Adjacency = {
            Position: tuple(sorted(Values))
            for Position, Values in MutableAdjacency.items()
        }
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if (
        Start not in Adjacency
        or any(Ingress not in Adjacency for Ingress in OrderedIngresses)
    ):
        return ()
    InitialClaims = ResourceGraph.BuildRouteClaims(FixedPrefix)
    if (
        FindSelfClaimConflicts({"PlacementAccess": InitialClaims})
        or any(
            MandatoryClaimsConflict(InitialClaims, Claims)
            for Claims in ForeignFixedClaims
        )
    ):
        return ()
    Results = []
    for Ingress in OrderedIngresses:
        # This search can touch a large region even though the resulting
        # fabric ring has few nodes.  The region and target are immutable for
        # this terminal, so calculating a Manhattan distance and re-sorting
        # each adjacency fanout at every heap pop only repeats deterministic
        # work.  Cache both once per ingress; path legality and tie-breaking
        # stay exactly the same.
        DistanceByPosition = {
            Position: (
                abs(Position[0] - Ingress[0])
                + abs(Position[1] - Ingress[1])
                + abs(Position[2] - Ingress[2])
            )
            for Position in Adjacency
        }
        OrderedNeighbors = {
            Position: tuple(sorted(
                Neighbors,
                key=lambda Value: (DistanceByPosition[Value], Value),
            ))
            for Position, Neighbors in Adjacency.items()
        }

        StartState = (Start, (0, 0, 0))
        Frontier = [(
            DistanceByPosition[Start],
            0,
            Start,
            (0, 0, 0),
        )]
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
            for Next in OrderedNeighbors.get(Current, ()):
                StepClaims = ResourceGraph.BuildRouteClaims((Current, Next))
                if any(
                    MandatoryClaimsConflict(StepClaims, Claims)
                    for Claims in ForeignFixedClaims
                ):
                    continue
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
                        NextCost + DistanceByPosition[Next],
                        NextCost,
                        Next,
                        Direction,
                    ),
                )
        if ReachedPath is not None:
            Results.append(ReachedPath)
    return tuple(Results)

def _BuildBoundedLegalDerivedEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    FixedPrefix: tuple[Position3, ...],
    ResourceGraph: Any,
    *,
    WorkBudget: _AccessFabricWorkBudget | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    Adjacency: dict[Position3, tuple[Position3, ...]] | None = None,
) -> tuple[tuple[tuple[Position3, ...], ...], bool]:
    """Build a finite legal escape domain with one state search per terminal.

    The old derived-perimeter path first generated geometrically short stubs,
    then re-ran a target-specific legal A* for every rejected ingress.  That
    was an accidental retry cascade inside one nominal access factor.  The
    fixed domain already has all permitted ingresses, so visit its
    ``(position, prior direction)`` state graph once, recording the first
    legal deterministic path for each ingress.  Direction remains part of
    the state because bend cost distinguishes otherwise identical positions.

    A shared work budget stops the construction cleanly.  The caller keeps
    any materialized paths for diagnostics but marks the factor incomplete,
    so a cap can never be misreported as an empty exhaustive domain.
    """
    if Adjacency is None:
        MutableAdjacency: dict[Position3, list[Position3]] = {}
        for First, Second in Edges:
            MutableAdjacency.setdefault(First, []).append(Second)
            MutableAdjacency.setdefault(Second, []).append(First)
        Adjacency = {
            Position: tuple(sorted(Values))
            for Position, Values in MutableAdjacency.items()
        }
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if (
        Start not in Adjacency
        or any(Ingress not in Adjacency for Ingress in OrderedIngresses)
    ):
        return (), not (WorkBudget is not None and WorkBudget.Exhausted)
    InitialClaims = ResourceGraph.BuildRouteClaims(FixedPrefix)
    if FindSelfClaimConflicts({"PlacementAccess": InitialClaims}):
        return (), not (WorkBudget is not None and WorkBudget.Exhausted)

    RemainingIngresses = set(OrderedIngresses)
    InitialDirection = (0, 0, 0)
    StartState = (Start, InitialDirection)
    Frontier: list[tuple[int, Position3, Position3]] = [
        (0, Start, InitialDirection),
    ]
    BestCost: dict[tuple[Position3, Position3], int] = {StartState: 0}
    Parent: dict[
        tuple[Position3, Position3],
        tuple[Position3, Position3] | None,
    ] = {StartState: None}
    ReachedPaths: dict[Position3, tuple[Position3, ...]] = {}

    while Frontier and RemainingIngresses:
        Cost, Current, PriorDirection = heappop(Frontier)
        CurrentState = (Current, PriorDirection)
        if Cost != BestCost.get(CurrentState):
            continue
        if WorkBudget is not None and not WorkBudget.Consume(
            WorkCheck,
            SignalTerminalStart=list(Start),
            RemainingIngressCount=len(RemainingIngresses),
        ):
            break
        if Current in RemainingIngresses:
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
                ReachedPaths[Current] = Path
                RemainingIngresses.remove(Current)
                if not RemainingIngresses:
                    break
        for Next in Adjacency.get(Current, ()):
            Direction = tuple(
                Next[Index] - Current[Index]
                for Index in range(3)
            )
            BendPenalty = int(
                PriorDirection != InitialDirection
                and Direction != PriorDirection
            ) * 4
            NextCost = Cost + 1 + BendPenalty
            NextState = (Next, Direction)
            if NextCost >= BestCost.get(NextState, 1 << 60):
                continue
            BestCost[NextState] = NextCost
            Parent[NextState] = CurrentState
            heappush(Frontier, (NextCost, Next, Direction))

    return (
        tuple(
            ReachedPaths[Ingress]
            for Ingress in OrderedIngresses
            if Ingress in ReachedPaths
        ),
        not (WorkBudget is not None and WorkBudget.Exhausted),
    )

def _BuildFabricIngressSegmentPaths(
    Anchor: Position3,
    Ingresses: Iterable[Position3],
    FabricEdges: Iterable[tuple[Position3, Position3]],
) -> tuple[tuple[Position3, ...], ...]:
    """Connect one legal face anchor to each ingress on its ring segment.

    A frozen slot's normal escape reaches one ring node; its remaining
    alternatives are lateral choices *on that already-materialized physical
    segment*.  Searching the full exterior state graph again for every
    lateral node repeats the same anchor work.  This helper traverses only
    the immutable fabric segment, preserving every reachable ingress and its
    exact edge sequence without scheduling another escape search.
    """
    OrderedIngresses = tuple(dict.fromkeys(Ingresses))
    if not OrderedIngresses:
        return ()
    Adjacency: dict[Position3, list[Position3]] = {}
    for First, Second in FabricEdges:
        Adjacency.setdefault(First, []).append(Second)
        Adjacency.setdefault(Second, []).append(First)
    if Anchor not in Adjacency:
        return ((Anchor,),) if OrderedIngresses == (Anchor,) else ()
    Parent: dict[Position3, Position3 | None] = {Anchor: None}
    Frontier = deque((Anchor,))
    Remaining = set(OrderedIngresses)
    while Frontier and Remaining:
        Current = Frontier.popleft()
        Remaining.discard(Current)
        for Next in sorted(Adjacency.get(Current, ())):
            if Next in Parent:
                continue
            Parent[Next] = Current
            Frontier.append(Next)
    Results: list[tuple[Position3, ...]] = []
    for Ingress in OrderedIngresses:
        if Ingress not in Parent:
            continue
        ReversePath = []
        Cursor: Position3 | None = Ingress
        while Cursor is not None:
            ReversePath.append(Cursor)
            Cursor = Parent[Cursor]
        Results.append(tuple(reversed(ReversePath)))
    return tuple(Results)

def _BuildSharedLegalFabricEscapePaths(
    Start: Position3,
    Ingresses: Iterable[Position3],
    Edges: Iterable[tuple[Position3, Position3]],
    FixedPrefix: tuple[Position3, ...],
    ResourceGraph: Any,
    ForeignFixedClaims: tuple[Any, ...] = (),
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
            if (
                FindSelfClaimConflicts({"PlacementAccess": CandidateClaims})
                or any(
                    MandatoryClaimsConflict(CandidateClaims, Claims)
                    for Claims in ForeignFixedClaims
                )
            ):
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

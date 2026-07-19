"""Rust-backed authoritative portal generation and exact route assignment."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from math import ceil, sqrt
from time import monotonic
from typing import Any, Callable

try:
    from ..RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
    except Exception:
        RustRoutingContext = None

from ..Placement.Rotation import RotatedCellSize
from .Actions import (
    BuildPhysicalGraphs,
    MaterializeReservedRepeaters,
    ValidatePhysicalRoutes,
)
from .ChannelPlanner import (
    BuildNetRoutingProfiles,
    CandidateLanes,
    ChannelPlan,
    MeasureRoutingStage,
    RasterizeChannelSegment,
    RoutingIterationMetrics,
)
from .Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from .Models import RoutedDesign, RoutingResources
from .Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from .ResourceGraph import (
    FindClaimConflicts,
    FindSelfClaimConflicts,
    IndexedRoutingResourceGraph,
    NetRouteCandidate,
    NormalizeRoutingEdge,
    PinAccessPortal,
    RoutingAssignment,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
    ValidateLocalRouteClaims,
)
from .Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from .TrackAssignment import AssignedTrack, TrackAssignment
from .LocalFirst import (
    BuildCapacityAwareGuidePlan,
    DeriveRoutingBudget,
    EstimateRoutingDemand,
)

Position2 = tuple[int, int]
Position3 = tuple[int, int, int]


def _BuildGuide(
    Terminals: tuple[Position2, ...],
    Axis: str,
    Lane: int,
) -> frozenset[Position2]:
    Result: set[Position2] = set()
    if Axis == "X":
        Minimum = min(Position[0] for Position in Terminals)
        Maximum = max(Position[0] for Position in Terminals)
        Result.update(RasterizeChannelSegment((Minimum, Lane), (Maximum, Lane)))
        for X, Z in Terminals:
            Result.update(RasterizeChannelSegment((X, Z), (X, Lane)))
    else:
        Minimum = min(Position[1] for Position in Terminals)
        Maximum = max(Position[1] for Position in Terminals)
        Result.update(RasterizeChannelSegment((Lane, Minimum), (Lane, Maximum)))
        for X, Z in Terminals:
            Result.update(RasterizeChannelSegment((X, Z), (Lane, Z)))
    return frozenset(Result)


def _PortalFromRust(
    Signal: str,
    Terminal: Position3,
    Layer: int,
    Value: Any,
    Resources: RoutingResources,
) -> PinAccessPortal:
    Path = tuple(Value.Path)
    return PinAccessPortal(
        PortalId=f"{Signal}:{Terminal}:{Layer}:{Value.PortalId}",
        Signal=Signal,
        Terminal=Terminal,
        Layer=Layer,
        Path=Path,
        Edges=frozenset(
            NormalizeRoutingEdge(First, Second)
            for First, Second in zip(Path, Path[1:])
        ),
        Claims=Resources.ResourceGraph.BuildRouteClaims(Path),
        Length=Value.Length,
        BendCount=Value.BendCount,
        ViaCount=Value.ViaCount,
        Cost=Value.Length + Value.BendCount * 10 + Value.ViaCount * 7,
    )


def _CountBends(Path: tuple[Position3, ...]) -> int:
    Directions = [
        (
            Second[0] - First[0],
            Second[1] - First[1],
            Second[2] - First[2],
        )
        for First, Second in zip(Path, Path[1:])
    ]
    return sum(First != Second for First, Second in zip(Directions, Directions[1:]))


def _BuildCandidateGraph(
    Nodes: set[Position3],
    Region: Any,
) -> dict[Position3, list[Position3]]:
    Result = {Position: [] for Position in Nodes}
    for Position in sorted(Nodes):
        for Neighbor in DefaultRedstoneRoutingTechnology.NeighborPositions(Position):
            if Neighbor in Nodes and Region.ContainsEdge(Position, Neighbor):
                Result[Position].append(Neighbor)
    return Result


def _FindPath(
    Graph: dict[Position3, list[Position3]],
    Start: Position3,
    Target: Position3,
) -> tuple[Position3, ...]:
    Parents: dict[Position3, Position3 | None] = {Start: None}
    Pending = deque((Start,))
    while Pending and Target not in Parents:
        Current = Pending.popleft()
        for Neighbor in Graph.get(Current, ()):
            if Neighbor not in Parents:
                Parents[Neighbor] = Current
                Pending.append(Neighbor)
    if Target not in Parents:
        return ()
    Result = []
    Current: Position3 | None = Target
    while Current is not None:
        Result.append(Current)
        Current = Parents[Current]
    return tuple(reversed(Result))


def _ReserveRepeaters(
    Signal: str,
    Root: Position3,
    Targets: tuple[Position3, ...],
    Graph: dict[Position3, list[Position3]],
    Technology: RedstoneRoutingTechnology,
) -> tuple[tuple[RoutingReservation, ...], dict[Position3, tuple[Position3, ...]]]:
    Reserved: dict[Position3, RoutingReservation] = {}
    Paths = {}
    for Target in Targets:
        Path = _FindPath(Graph, Root, Target)
        if not Path:
            return (), {}
        Paths[Target] = Path
        LastRefresh = 0
        while len(Path) - 1 - LastRefresh >= Technology.MaximumUnrefreshedDustLength:
            Preferred = min(
                len(Path) - 2,
                LastRefresh + Technology.PreferredRepeaterInterval,
            )
            Maximum = min(
                len(Path) - 2,
                LastRefresh + Technology.MaximumUnrefreshedDustLength - 1,
            )
            Candidates = []
            for Index in range(LastRefresh + 1, Maximum + 1):
                Previous, Current, Next = Path[Index - 1 : Index + 2]
                if (
                    Previous[1] == Current[1] == Next[1]
                    and (
                        Previous[0] == Current[0] == Next[0]
                        or Previous[2] == Current[2] == Next[2]
                    )
                    and len(Graph[Current]) == 2
                ):
                    Candidates.append(Index)
            if not Candidates:
                return (), {}
            Selected = min(Candidates, key=lambda Index: (abs(Index - Preferred), Index))
            Position = Path[Selected]
            Next = Path[Selected + 1]
            Delta = (Next[0] - Position[0], Next[2] - Position[2])
            Facing = {
                (1, 0): "west",
                (-1, 0): "east",
                (0, 1): "north",
                (0, -1): "south",
            }[Delta]
            Reserved.setdefault(
                Position,
                RoutingReservation(
                    Signal=Signal,
                    Resource=RoutingResourceId(RoutingResourceKind.Wire, Position),
                    Position=Position,
                    Purpose="Repeater",
                    Facing=Facing,
                ),
            )
            LastRefresh = Selected
    return tuple(Reserved[Position] for Position in sorted(Reserved)), Paths


def _MaterializeCandidate(
    Signal: str,
    Profile: Any,
    SourcePortal: PinAccessPortal,
    TargetPortals: tuple[PinAccessPortal, ...],
    Guide: frozenset[Position2],
    Layer: int,
    Axis: str,
    Lane: int,
    Variant: int,
    RoutedTree: list[Position3] | None,
    Region: Any,
    Resources: RoutingResources,
    Technology: RedstoneRoutingTechnology,
    LengthPenalty: int,
    BendPenalty: int = 0,
    ViaPenalty: int = 0,
    LayerPenalty: int = 0,
    GuideDeviationPenalty: int = 0,
    RejectionCounts: Counter[str] | None = None,
) -> NetRouteCandidate | None:
    if RoutedTree is None:
        if RejectionCounts is not None:
            RejectionCounts["NoTree"] += 1
        return None
    Nodes = set(RoutedTree)
    SeedNodes = {
        Position
        for Claim in (Profile.Seed.LocalClaims if Profile.Seed is not None else ())
        for Position in Claim.Nodes
    }
    Nodes.update(Profile.SourceAccessPath)
    Nodes.update(SourcePortal.Path)
    for Target, Portal in zip(Profile.Targets, TargetPortals):
        Nodes.update(Profile.TargetAccessPaths[Target])
        Nodes.update(Portal.Path)
    Claims = Resources.ResourceGraph.BuildRouteClaims(Nodes)
    if FindSelfClaimConflicts({Signal: Claims}):
        if RejectionCounts is not None:
            RejectionCounts["SelfClaimConflict"] += 1
        return None
    Graph = _BuildCandidateGraph(Nodes, Region)
    MissingPaths = [
        Target for Target in Profile.Targets
        if not _FindPath(Graph, Profile.Root, Target)
    ]
    if MissingPaths:
        if RejectionCounts is not None:
            RejectionCounts["Disconnected"] += 1
        return None
    RepeaterReservations, Paths = _ReserveRepeaters(
        Signal,
        Profile.Root,
        Profile.Targets,
        Graph,
        Technology,
    )
    if len(Paths) != len(Profile.Targets):
        if RejectionCounts is not None:
            RejectionCounts["NoRepeater"] += 1
        return None
    Edges = frozenset(
        NormalizeRoutingEdge(Position, Neighbor)
        for Position, Neighbors in Graph.items()
        for Neighbor in Neighbors
        if Position < Neighbor
    )
    Length = len(Nodes)
    BendCount = sum(_CountBends(Path) for Path in Paths.values())
    ViaCount = sum(
        First[1] != Second[1]
        for Path in Paths.values()
        for First, Second in zip(Path, Path[1:])
    )
    CandidateId = f"{Signal}:L{Layer}:{Axis}:{Lane}:V{Variant}"
    IncrementalLength = len(Nodes - SeedNodes)
    IncrementalMaterialCost = (
        IncrementalLength * max(1, LengthPenalty)
        + BendCount * max(0, BendPenalty)
        + ViaCount * max(0, ViaPenalty)
        + Layer * max(0, LayerPenalty)
        + max(0, GuideDeviationPenalty)
        + len(RepeaterReservations) * 2
    )
    return NetRouteCandidate(
        CandidateId=CandidateId,
        Signal=Signal,
        SourcePortalId=SourcePortal.PortalId,
        TargetPortalIds={
            Target: Portal.PortalId
            for Target, Portal in zip(Profile.Targets, TargetPortals)
        },
        Nodes=frozenset(Nodes),
        Edges=Edges,
        Claims=Claims,
        Layer=Layer,
        Guide=Guide,
        RepeaterWaypoints=tuple(
            Reservation.Position for Reservation in RepeaterReservations
        ),
        MaterialCost=IncrementalMaterialCost,
        FootprintGrowth=len(Guide),
        Length=Length,
        BendCount=BendCount,
        ViaCount=ViaCount,
        IncrementalMaterialCost=IncrementalMaterialCost,
        IncrementalLength=IncrementalLength,
        SeedNodeCount=len(SeedNodes),
    )


def RouteAuthoritativeResources(
    Placed: Any,
    Resources: RoutingResources,
    SearchMarginX: int,
    SearchMarginZ: int,
    MaximumRoutingHeight: int,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    ProgressCallback: Callable[[int, int], None] | None = None,
    DiagnosticCallback: Callable[
        [RoutingIterationMetrics, str | None], None
    ] | None = None,
    AdaptiveLayerFloor: int | None = None,
    SharedRoutingStarted: float | None = None,
    EscalationHistory: tuple[dict[str, object], ...] = (),
) -> RoutedDesign:
    """Generate portals and select complete capacity-one routes in Rust."""
    if RustRoutingContext is None:
        raise ValueError("authoritative routing requires the Rust router")
    RoutingStarted = (
        SharedRoutingStarted if SharedRoutingStarted is not None else monotonic()
    )
    StageTimings: dict[str, float] = {}
    StageCount = 5

    def CheckRuntimeBudget(Stage: str) -> None:
        Elapsed = monotonic() - RoutingStarted
        if Elapsed <= Policy.RuntimeBudgetSeconds:
            return
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.RuntimeBudgetExceeded,
                Stage=Stage,
                Detail=(
                    f"adaptive runtime budget {Policy.RuntimeBudgetSeconds:.3f}s "
                    f"exhausted after {Elapsed:.3f}s"
                ),
            )
        )
    if ProgressCallback is not None:
        ProgressCallback(0, StageCount)
    LocalClaims = tuple(getattr(Placed, "LocalRouteClaims", ()) or ())
    Profiles = BuildNetRoutingProfiles(
        Placed,
        AccessLength=Policy.Placement.PinEscapeLength,
    )
    Demand = EstimateRoutingDemand(Placed, Profiles)
    AdaptiveBudget = DeriveRoutingBudget(Demand, Policy, Technology)
    if LocalClaims:
        ValidateLocalRouteClaims(Resources.ResourceGraph, LocalClaims)
    if not Profiles:
        return RoutedDesign(
            Module=Placed.Module,
            PlacedGates=Placed.PlacedGates,
            Wires=[],
            Supports=[],
            Repeaters={},
            NetWires={},
            ZeroResourceConflicts=True,
        )
    PortalLimit = (
        min(
            Policy.TrackAssignment.MaximumPortalsPerTerminal,
            AdaptiveBudget.PortalsPerTerminal
            * Policy.AdaptiveRouting.PortalGrowthFactor ** max(
                0,
                (AdaptiveLayerFloor or AdaptiveBudget.LayerCount)
                - AdaptiveBudget.LayerCount,
            ),
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.TrackAssignment.MaximumPortalsPerTerminal
    )
    RouteLaneCount = (
        min(
            Policy.GlobalRouting.CandidateLaneCount,
            AdaptiveBudget.LaneCount
            * Policy.AdaptiveRouting.LaneGrowthFactor ** max(
                0,
                (AdaptiveLayerFloor or AdaptiveBudget.LayerCount)
                - AdaptiveBudget.LayerCount,
            ),
        )
        if Policy.AdaptiveRouting.Enabled
        else min(3, Policy.GlobalRouting.CandidateLaneCount)
    )
    RoutePortalVariantCounts = {
        Signal: (
            min(
                PortalLimit,
                Policy.AdaptiveRouting.InitialPortalsPerTerminal
                * Policy.AdaptiveRouting.PortalGrowthFactor ** max(
                    0,
                    (AdaptiveLayerFloor or AdaptiveBudget.LayerCount)
                    - AdaptiveBudget.LayerCount,
                ),
            )
            if Policy.AdaptiveRouting.Enabled
            else (
                PortalLimit
                if len(Profiles) <= 16 or len(Profile.Targets) >= 4
                else min(4, PortalLimit)
            )
        )
        for Signal, Profile in Profiles.items()
    }
    MinimumX = min(Gate.X for Gate in Placed.PlacedGates)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Placed.PlacedGates
    )
    MinimumZ = min(Gate.Z for Gate in Placed.PlacedGates)
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Placed.PlacedGates
    )
    MinimumY = min(Gate.Y for Gate in Placed.PlacedGates)
    RouteLayers = getattr(Placed, "RouteLayers", None) or {}
    PolicyLayerLimit = Policy.Placement.MaximumRoutingLayers
    MinimumLayerCount = (
        min(Technology.MinimumRoutingLayerCount, PolicyLayerLimit)
        if PolicyLayerLimit > 0
        else Technology.MinimumRoutingLayerCount
    )
    HeightLayerCount = max(
        MinimumLayerCount,
        (MaximumRoutingHeight - 2) // Technology.RoutingLayerPitch,
    )
    LayerCount = min(
        (
            min(Technology.MaximumRoutableLayerCount, PolicyLayerLimit)
            if PolicyLayerLimit > 0
            else Technology.MaximumRoutableLayerCount
        ),
        max(
            MinimumLayerCount,
            HeightLayerCount,
            (
                AdaptiveBudget.LayerCount
                if Policy.AdaptiveRouting.Enabled
                else MinimumLayerCount
            ),
            AdaptiveLayerFloor or 0,
            max(RouteLayers.values(), default=0) + 1,
        ),
    )
    GuidePlanningStarted = monotonic()
    CoarsePlan = (
        BuildCapacityAwareGuidePlan(
            Profiles,
            LayerCount,
            MinimumX,
            MinimumZ,
            Policy.GlobalRouting,
            Technology,
            Policy.Placement.LocalFanoutDistance,
        )
        if Policy.QualityTarget == "local-first"
        else None
    )
    StageTimings["GlobalGuidePlanning"] = monotonic() - GuidePlanningStarted
    CheckRuntimeBudget("Guide")
    if ProgressCallback is not None:
        ProgressCallback(1, StageCount)
    Bounds = (
        MinimumX - SearchMarginX,
        MaximumX + SearchMarginX,
        MinimumY,
        MinimumY + max(MaximumRoutingHeight, 2 * LayerCount + 2),
        MinimumZ - SearchMarginZ,
        MaximumZ + SearchMarginZ,
    )
    ReservedAccess = frozenset(
        Position
        for Profile in Profiles.values()
        for Path in (Profile.SourceAccessPath, *Profile.TargetAccessPaths.values())
        for Position in Path
    ) | frozenset(
        Position for Claim in LocalClaims for Position in Claim.Nodes
    )
    AssignedColumns: set[Position2] = set()
    RegionExpansion = (
        Policy.DetailedRouting.GuideExpansion
        + Policy.GlobalRouting.SharedBoundaryEnvelope
        if CoarsePlan is not None
        else 3 * Policy.DetailedRouting.GuideExpansion
    )
    for Signal, Profile in Profiles.items():
        if CoarsePlan is not None:
            BaseGuides = (CoarsePlan.Guides[Signal],)
        else:
            BaseGuides = ()
        TerminalColumns = tuple(
            (Path[-1][0], Path[-1][2])
            for Path in (
                Profile.SourceAccessPath,
                *Profile.TargetAccessPaths.values(),
            )
        )
        if Profile.Seed is not None and Profile.Seed.ContinuationNodes:
            TerminalColumns = tuple(dict.fromkeys((
                *((Position[0], Position[2]) for Position in Profile.Seed.ContinuationNodes),
                *TerminalColumns,
            )))
        for Axis in (("X", "Z") if not BaseGuides else ()):
            Coordinates = sorted(
                Z for _X, Z in TerminalColumns
            ) if Axis == "X" else sorted(X for X, _Z in TerminalColumns)
            Center = Coordinates[len(Coordinates) // 2]
            TrackAnchor = MinimumZ if Axis == "X" else MinimumX
            AlignedCenter = TrackAnchor + (
                (Center - TrackAnchor + Technology.TrackPitch // 2)
                // Technology.TrackPitch
            ) * Technology.TrackPitch
            BaseGuide = _BuildGuide(TerminalColumns, Axis, AlignedCenter)
            BaseGuides = (*BaseGuides, BaseGuide)
        for BaseGuide in BaseGuides:
            AssignedColumns.update(
                (GuideX + DeltaX, GuideZ + DeltaZ)
                for GuideX, GuideZ in BaseGuide
                for DeltaX in range(-RegionExpansion, RegionExpansion + 1)
                for DeltaZ in range(-RegionExpansion, RegionExpansion + 1)
                if abs(DeltaX) + abs(DeltaZ) <= RegionExpansion
            )
    Region = Resources.ResourceGraph.BuildRegion(
        Bounds,
        AllowedColumns=frozenset(AssignedColumns),
        AllowedAccess=ReservedAccess,
    )
    Context = RustRoutingContext(
        Bounds,
        (MinimumX, MaximumX, MinimumZ, MaximumZ),
        sorted(Region.Nodes),
        sorted(Region.Edges),
    )
    StageTimings["ResourceGraph"] = monotonic() - RoutingStarted
    if ProgressCallback is not None:
        ProgressCallback(2, StageCount)
    PortalStarted = monotonic()
    Portals: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]] = {}
    NodesByColumn: dict[Position2, list[Position3]] = defaultdict(list)
    for Position in Region.Nodes:
        NodesByColumn[(Position[0], Position[2])].append(Position)
    for Signal in sorted(Profiles):
        Profile = Profiles[Signal]
        TerminalPaths = (
            (Profile.Root, Profile.SourceAccessPath),
            *((Target, Profile.TargetAccessPaths[Target]) for Target in Profile.Targets),
        )
        for Terminal, AccessPath in TerminalPaths:
            AccessColumns = {(X, Z) for X, _Y, Z in AccessPath}
            AllowedColumns = {
                (AccessX + DeltaX, AccessZ + DeltaZ)
                for AccessX, AccessZ in AccessColumns
                for DeltaX in range(-Policy.DetailedRouting.GuideExpansion, Policy.DetailedRouting.GuideExpansion + 1)
                for DeltaZ in range(-Policy.DetailedRouting.GuideExpansion, Policy.DetailedRouting.GuideExpansion + 1)
                if abs(DeltaX) + abs(DeltaZ) <= Policy.DetailedRouting.GuideExpansion
            }
            AllowedNodeSet = {
                Position
                for Column in AllowedColumns
                for Position in NodesByColumn.get(Column, ())
            } | set(AccessPath)
            AllowedNodes = sorted(AllowedNodeSet)
            for Layer in range(LayerCount):
                RoutingY = Technology.RoutingY(MinimumY, Layer)
                PortalTargets = sorted(
                    (
                    Position for Position in AllowedNodes if Position[1] == RoutingY
                    ),
                    key=lambda Position: (
                        min(
                            abs(Position[0] - AccessPosition[0])
                            + abs(Position[1] - AccessPosition[1])
                            + abs(Position[2] - AccessPosition[2])
                            for AccessPosition in AccessPath
                        ),
                        abs(Position[0] - AccessPath[-1][0]),
                        abs(Position[2] - AccessPath[-1][2]),
                        Position,
                    ),
                )
                if len(Profiles) > 16:
                    PortalTargets = PortalTargets[: PortalLimit * 4]
                Values = Context.GeneratePortalCandidates(
                    list(AccessPath),
                    PortalTargets,
                    AllowedNodes,
                    RoutingY,
                    PortalLimit,
                    Policy.DetailedRouting.StrictMaximumExpansions,
                )
                Portals[(Signal, Terminal, Layer)] = tuple(
                    _PortalFromRust(Signal, Terminal, Layer, Value, Resources)
                    for Value in Values
                )

    StageTimings["PortalGeneration"] = monotonic() - PortalStarted
    CheckRuntimeBudget("Portal")
    if ProgressCallback is not None:
        ProgressCallback(3, StageCount)
    CandidateStarted = monotonic()
    CandidateRequestCount = max(
        1,
        LayerCount
        * sum(RoutePortalVariantCounts.values())
        * 2
        * RouteLaneCount,
    )
    BaseCandidateExpansionLimit = (
        min(
            AdaptiveBudget.CandidateExpansionsPerNet,
            Policy.AdaptiveRouting.MaximumCandidateGenerationExpansions
            // max(1, CandidateRequestCount),
        )
        if Policy.AdaptiveRouting.Enabled
        else (
            Policy.DetailedRouting.StrictMaximumExpansions
            if len(Profiles) <= 16
            else max(
                512,
                min(
                    Policy.DetailedRouting.StrictMaximumExpansions,
                    12_000_000 // CandidateRequestCount,
                ),
            )
        )
    )
    CandidateExpansionLimits = {
        Signal: min(
            Policy.DetailedRouting.StrictMaximumExpansions,
            BaseCandidateExpansionLimit
            * (2 if len(Profile.Targets) >= 4 else 1),
        )
        for Signal, Profile in Profiles.items()
    }

    CandidatesBySignal: dict[str, list[NetRouteCandidate]] = defaultdict(list)
    CandidateLimitsBySignal: dict[str, int] = {}
    CandidateDiagnostics: dict[str, dict[str, int]] = {}
    for Signal in sorted(
        Profiles,
        key=lambda Value: (
            -len(Profiles[Value].Targets),
            -max(
                abs(Profiles[Value].Root[0] - Target[0])
                + abs(Profiles[Value].Root[2] - Target[2])
                for Target in Profiles[Value].Targets
            ),
            Value,
        ),
    ):
        Profile = Profiles[Signal]
        CandidateExpansionLimit = CandidateExpansionLimits[Signal]
        RouteRequests = []
        RouteMetadata = []
        LayerOrder = tuple(range(LayerCount))
        if CoarsePlan is not None:
            PlannedLayer = CoarsePlan.Layers[Signal]
            LayerOrder = (PlannedLayer,) + tuple(
                Layer for Layer in LayerOrder if Layer != PlannedLayer
            )
        for Layer in LayerOrder:
            SourcePortals = Portals[(Signal, Profile.Root, Layer)]
            TargetPortalSets = [Portals[(Signal, Target, Layer)] for Target in Profile.Targets]
            if not SourcePortals or any(not Values for Values in TargetPortalSets):
                continue
            RoutingY = Technology.RoutingY(MinimumY, Layer)
            for Variant in range(RoutePortalVariantCounts[Signal]):
                SourcePortal = SourcePortals[Variant % len(SourcePortals)]
                BaseTargetPortals = tuple(
                    Values[(Variant + Index) % len(Values)]
                    for Index, Values in enumerate(TargetPortalSets)
                )
                Terminals = tuple(
                    (Portal.Path[-1][0], Portal.Path[-1][2])
                    for Portal in (SourcePortal, *BaseTargetPortals)
                )
                XSpan = max(X for X, _Z in Terminals) - min(X for X, _Z in Terminals)
                ZSpan = max(Z for _X, Z in Terminals) - min(Z for _X, Z in Terminals)
                PreferredAxis = "X" if XSpan >= ZSpan else "Z"
                for AxisIndex, Axis in enumerate(
                    (PreferredAxis, "Z" if PreferredAxis == "X" else "X")
                ):
                    Coordinates = sorted(
                        Z for _X, Z in Terminals
                    ) if Axis == "X" else sorted(X for X, _Z in Terminals)
                    Center = Coordinates[len(Coordinates) // 2]
                    TrackAnchor = MinimumZ if Axis == "X" else MinimumX
                    AlignedCenter = TrackAnchor + (
                        (
                            Center
                            - TrackAnchor
                            + Technology.TrackPitch // 2
                        )
                        // Technology.TrackPitch
                    ) * Technology.TrackPitch
                    if (
                        CoarsePlan is not None
                        and Axis == CoarsePlan.Axes[Signal]
                    ):
                        AlignedCenter = CoarsePlan.Lanes[Signal]
                    for LaneIndex, Lane in enumerate(CandidateLanes(
                        AlignedCenter,
                        RouteLaneCount,
                        Technology.TrackPitch,
                    )):
                        PortalPhase = 1 + AxisIndex * 3 + LaneIndex
                        TargetPortals = tuple(
                            Values[
                                (Variant + PortalPhase * (Index + 1))
                                % len(Values)
                            ]
                            for Index, Values in enumerate(TargetPortalSets)
                        )
                        Terminals = tuple(
                            (Portal.Path[-1][0], Portal.Path[-1][2])
                            for Portal in (SourcePortal, *TargetPortals)
                        )
                        Guide = _BuildGuide(Terminals, Axis, Lane)
                        IsPlannedGuide = (
                            CoarsePlan is not None
                            and Layer == CoarsePlan.Layers[Signal]
                            and Axis == CoarsePlan.Axes[Signal]
                            and Lane == CoarsePlan.Lanes[Signal]
                        )
                        GuideExpansion = (
                            Policy.GlobalRouting.IntraClusterEnvelope
                            if IsPlannedGuide and Signal in CoarsePlan.LocalSignals
                            else Policy.DetailedRouting.GuideExpansion
                        )
                        CandidateColumns = {
                            (GuideX + DeltaX, GuideZ + DeltaZ)
                            for GuideX, GuideZ in Guide
                            for DeltaX in range(-GuideExpansion, GuideExpansion + 1)
                            for DeltaZ in range(-GuideExpansion, GuideExpansion + 1)
                            if abs(DeltaX) + abs(DeltaZ) <= GuideExpansion
                        }
                        SeedNodeSet = {
                            Position
                            for Claim in (
                                Profile.Seed.LocalClaims
                                if Profile.Seed is not None
                                else ()
                            )
                            for Position in Claim.Nodes
                        }
                        RequiredNodes = sorted(
                            SeedNodeSet
                            | set(Profile.SourceAccessPath)
                            | set(SourcePortal.Path)
                            | {
                                Position
                                for Target in Profile.Targets
                                for Position in Profile.TargetAccessPaths[Target]
                            }
                            | {
                                Position
                                for Portal in TargetPortals
                                for Position in Portal.Path
                            }
                        )
                        SeedStarts = (
                            tuple(
                                Position
                                for Claim in Profile.Seed.LocalClaims
                                for Position in Claim.Nodes
                            )
                            if Profile.Seed is not None
                            else ()
                        )
                        RouteRequests.append(
                            (
                                list(
                                    dict.fromkeys(
                                        (
                                            *(
                                                SeedStarts
                                            ),
                                            *Profile.SourceAccessPath,
                                            *SourcePortal.Path,
                                        )
                                    )
                                ),
                                [
                                    list(
                                        dict.fromkeys(
                                            (
                                                *Profile.TargetAccessPaths[Target],
                                                *Portal.Path,
                                            )
                                        )
                                    )
                                    for Target, Portal in zip(
                                        Profile.Targets, TargetPortals
                                    )
                                ],
                                sorted(CandidateColumns),
                                RequiredNodes,
                                sorted(Guide),
                                RoutingY,
                                Policy.DetailedRouting.MinimumGuidePenalty,
                                Policy.DetailedRouting.StrictBendPenalty,
                                Policy.DetailedRouting.StrictViaPenalty,
                                CandidateExpansionLimit,
                            )
                        )
                        RouteMetadata.append(
                            (SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant)
                        )
        RoutedTrees = Context.GenerateRouteTrees(RouteRequests)
        RejectionCounts: Counter[str] = Counter()
        CandidateDiagnostics[Signal] = {
            "Requests": len(RouteRequests),
            "RoutedTrees": sum(Value is not None for Value in RoutedTrees),
            "Materialized": 0,
            "SeedNodes": sum(
                len(Claim.Nodes)
                for Claim in (
                    Profile.Seed.LocalClaims if Profile.Seed is not None else ()
                )
            ),
            "SourcePortals": sum(
                len(Portals[(Signal, Profile.Root, Layer)])
                for Layer in range(LayerCount)
            ),
            "TargetPortals": sum(
                len(Portals[(Signal, Target, Layer)])
                for Target in Profile.Targets
                for Layer in range(LayerCount)
            ),
        }
        for RoutedTree, Metadata in zip(RoutedTrees, RouteMetadata):
            SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant = Metadata
            Candidate = _MaterializeCandidate(
                Signal,
                Profile,
                SourcePortal,
                TargetPortals,
                Guide,
                Layer,
                Axis,
                Lane,
                Variant,
                RoutedTree,
                Region,
                Resources,
                Technology,
                Policy.DetailedRouting.LengthPenalty,
                (
                    Policy.DetailedRouting.CandidateBendWeight
                    if Policy.QualityTarget == "local-first"
                    else 0
                ),
                (
                    Policy.DetailedRouting.CandidateViaWeight
                    if Policy.QualityTarget == "local-first"
                    else 0
                ),
                Policy.DetailedRouting.LayerPenalty,
                (
                    0
                    if CoarsePlan is None
                    else (
                        len(Guide.symmetric_difference(CoarsePlan.Guides[Signal]))
                        + (
                            0
                            if Layer == CoarsePlan.Layers[Signal]
                            else Policy.GlobalRouting.OverflowPenalty
                        )
                    ) * Policy.GlobalRouting.ExistingGuideHintWeight
                ),
                RejectionCounts,
            )
            if Candidate is not None:
                CandidatesBySignal[Signal].append(Candidate)
                CandidateDiagnostics[Signal]["Materialized"] += 1
        CandidateDiagnostics[Signal]["Rejections"] = dict(RejectionCounts)
        CandidateOrder = lambda Value: (
            Value.MaterialCost,
            Value.FootprintGrowth,
            Value.Length,
            Value.BendCount,
            Value.ViaCount,
            Value.CandidateId,
        )
        CandidatesByTrack: dict[
            tuple[int, frozenset[Position2]], list[NetRouteCandidate]
        ] = defaultdict(list)
        for Candidate in CandidatesBySignal[Signal]:
            Key = (Candidate.Layer, Candidate.Guide)
            CandidatesByTrack[Key].append(Candidate)
        for Values in CandidatesByTrack.values():
            Values.sort(key=CandidateOrder)
        MaximumCandidates = (
            min(
                Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
                AdaptiveBudget.CandidatesPerNet
                * Policy.AdaptiveRouting.CandidateGrowthFactor ** max(
                    0,
                    (AdaptiveLayerFloor or AdaptiveBudget.LayerCount)
                    - AdaptiveBudget.LayerCount,
                ),
            )
            if Policy.AdaptiveRouting.Enabled
            else Policy.TrackAssignment.MaximumRouteCandidatesPerNet
        )
        SignalMaximumCandidates = MaximumCandidates
        if Policy.AdaptiveRouting.Enabled:
            ClaimWork = max(1, Profile.Span) * max(1, len(Profile.Targets))
            WorkScale = max(
                1,
                ceil(
                    sqrt(
                        ClaimWork
                        / Policy.AdaptiveRouting.CandidateClaimWorkQuantum
                    )
                ),
            )
            SignalMaximumCandidates = max(
                Policy.AdaptiveRouting.MinimumCandidatesPerNet,
                MaximumCandidates // WorkScale,
            )
        CandidateLimitsBySignal[Signal] = SignalMaximumCandidates
        PerLayer = max(1, SignalMaximumCandidates // LayerCount)
        DiverseCandidates = []
        for Layer in range(LayerCount):
            LayerTracks = sorted(
                (
                    Values
                    for (TrackLayer, _Guide), Values in CandidatesByTrack.items()
                    if TrackLayer == Layer
                ),
                key=lambda Values: CandidateOrder(Values[0]),
            )
            LayerValues = []
            VariantIndex = 0
            while len(LayerValues) < PerLayer:
                Added = False
                for Values in LayerTracks:
                    if VariantIndex < len(Values):
                        LayerValues.append(Values[VariantIndex])
                        Added = True
                        if len(LayerValues) == PerLayer:
                            break
                if not Added:
                    break
                VariantIndex += 1
            DiverseCandidates.extend(LayerValues)
        CandidatesBySignal[Signal] = DiverseCandidates[:SignalMaximumCandidates]
        if not CandidatesBySignal[Signal]:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.DetailedSearchExhausted,
                    Stage="Candidate",
                    AffectedNets=(Signal,),
                    Detail=(
                        "bounded Rust search produced no complete legal route "
                        f"candidate (candidate_expansion_limit={CandidateExpansionLimit}, "
                        f"diagnostics={CandidateDiagnostics[Signal]})"
                    ),
                )
            )
        CheckRuntimeBudget("Candidate")

    StageTimings["CandidateGeneration"] = monotonic() - CandidateStarted
    if ProgressCallback is not None:
        ProgressCallback(4, StageCount)

    CandidateLookup = {
        Candidate.CandidateId: Candidate
        for Values in CandidatesBySignal.values()
        for Candidate in Values
    }
    FrozenSignals = set((getattr(Placed, "FrozenNetWires", None) or {}).keys())
    BaseLocalClaims = tuple(
        Claim for Claim in LocalClaims if Claim.Signal in FrozenSignals
    )
    AssignmentResourcePositions = tuple(
        sorted(
            {
                Position
                for Candidate in CandidateLookup.values()
                for Positions in (
                    Candidate.Claims.WireCells,
                    Candidate.Claims.SupportCells,
                    Candidate.Claims.RequiredAirCells,
                    Candidate.Claims.ElectricalCells,
                )
                for Position in Positions
            }
            | {
                Position
                for Claim in BaseLocalClaims
                for Positions in (
                    Claim.Claims.WireCells,
                    Claim.Claims.SupportCells,
                    Claim.Claims.RequiredAirCells,
                    Claim.Claims.ElectricalCells,
                )
                for Position in Positions
            }
        )
    )
    AssignmentIndexed = IndexedRoutingResourceGraph(
        ResourcePositions=AssignmentResourcePositions,
        PositionIndices={
            Position: Index
            for Index, Position in enumerate(AssignmentResourcePositions)
        },
    )
    def EncodeCandidateValues(
        CandidateSets: dict[str, list[NetRouteCandidate]],
        CongestionHistory: Counter[Position2] | None = None,
        OptimizeShape: bool = False,
    ) -> list[tuple[Any, ...]]:
        Values = []
        History = CongestionHistory or Counter()
        for Signal in sorted(CandidateSets):
            for Candidate in CandidateSets[Signal]:
                Wire, Support, Air, Electrical = AssignmentIndexed.EncodeClaims(
                    Candidate.Claims
                )
                HistoryCost = sum(
                    History[(X, Z)] for X, _Y, Z in Candidate.Nodes
                ) * Policy.Repair.HistoryIncrement
                MaterialCost = (
                    Candidate.BendCount * 10_000
                    + Candidate.ViaCount * 100
                    + Candidate.Length
                    if OptimizeShape
                    else Candidate.MaterialCost
                )
                Values.append(
                    (
                        Signal,
                        Candidate.CandidateId,
                        list(Wire),
                        list(Support),
                        list(Air),
                        list(Electrical),
                        MaterialCost + HistoryCost,
                        Candidate.FootprintGrowth,
                        Candidate.Length,
                        Candidate.BendCount,
                        Candidate.ViaCount,
                    )
                )
        return Values

    BaseValues: list[tuple[Any, ...]] | None = None

    EscalationLevel = max(
        0,
        (AdaptiveLayerFloor or AdaptiveBudget.LayerCount)
        - AdaptiveBudget.LayerCount,
    )
    AssignmentExpansionLimit = (
        min(
            AdaptiveBudget.AssignmentExpansions,
            Policy.AdaptiveRouting.InitialAssignmentExpansions
            * Policy.AdaptiveRouting.AssignmentGrowthFactor ** EscalationLevel,
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.TrackAssignment.MaximumAssignmentExpansions
    )

    def PlanAssignment(Values: list[tuple[Any, ...]] | None = None) -> Any:
        nonlocal BaseValues
        if Values is None:
            Values = EncodeCandidateValues(CandidatesBySignal)
        if BaseValues is None:
            BaseValues = []
            for Claim in BaseLocalClaims:
                Wire, Support, Air, Electrical = AssignmentIndexed.EncodeClaims(
                    Claim.Claims
                )
                BaseValues.append(
                    (
                        Claim.Signal,
                        list(Wire),
                        list(Support),
                        list(Air),
                        list(Electrical),
                    )
                )
        if BaseValues:
            return Context.PlanAuthoritativeRoutesWithBase(
                Values,
                BaseValues,
                len(AssignmentIndexed.ResourcePositions),
                (
                    AssignmentExpansionLimit
                ),
            )
        return Context.PlanAuthoritativeRoutes(
            Values,
            len(AssignmentIndexed.ResourcePositions),
            (
                AssignmentExpansionLimit
            ),
        )

    AssignmentStarted = monotonic()
    Result = PlanAssignment()
    CheckRuntimeBudget("Track")
    StageTimings["Assignment"] = monotonic() - AssignmentStarted
    if ProgressCallback is not None:
        ProgressCallback(5, StageCount)
    if not Result.Success:
        MaximumLayerCount = (
            min(Technology.MaximumRoutableLayerCount, PolicyLayerLimit)
            if PolicyLayerLimit > 0
            else Technology.MaximumRoutableLayerCount
        )
        if (
            Policy.AdaptiveRouting.Enabled
            and LayerCount < MaximumLayerCount
            and CoarsePlan is not None
            and CoarsePlan.OverflowPeak
            > Policy.QualityGate.MaximumCorridorOverflowPeak
            and monotonic() - RoutingStarted < AdaptiveBudget.RuntimeSeconds
        ):
            Escalation = {
                "Stage": "TrackAssignment",
                "FromLayerCount": LayerCount,
                "ToLayerCount": LayerCount + 1,
                "AssignmentExpansions": Result.ExpansionCount,
                "ExactExpansions": Result.ExpansionCount,
                "Reason": "capacity-one assignment exhausted current candidates",
            }
            return RouteAuthoritativeResources(
                Placed,
                Resources,
                SearchMarginX,
                SearchMarginZ,
                MaximumRoutingHeight,
                Policy,
                Technology,
                ProgressCallback,
                DiagnosticCallback,
                AdaptiveLayerFloor=LayerCount + 1,
                SharedRoutingStarted=RoutingStarted,
                EscalationHistory=(*EscalationHistory, Escalation),
            )
        Locations = tuple(
            AssignmentIndexed.ResourcePositions[Index]
            for Index in Result.ConflictResourceIndices[:8]
        )
        ZeroCompatibilityPairs = []
        if not Policy.AdaptiveRouting.Enabled:
            Signals = sorted(CandidatesBySignal)
            for SignalIndex, FirstSignal in enumerate(Signals):
                for SecondSignal in Signals[SignalIndex + 1 :]:
                    if not any(
                        not FindClaimConflicts(
                            {
                                FirstSignal: First.Claims,
                                SecondSignal: Second.Claims,
                            }
                        )
                        for First in CandidatesBySignal[FirstSignal]
                        for Second in CandidatesBySignal[SecondSignal]
                    ):
                        ZeroCompatibilityPairs.append((FirstSignal, SecondSignal))
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="Track",
                AffectedNets=((Result.FailureNet,) if Result.FailureNet else ()),
                Locations=Locations,
                Detail=(
                    "Rust MRV assignment exhausted exact capacity-one candidates "
                    f"after {Result.ExpansionCount} expansions; "
                    f"pairwise_unroutable={ZeroCompatibilityPairs[:4]}"
                ),
            )
        )
    InitialAssignmentExpansionCount = Result.ExpansionCount
    Selected = {
        Signal: CandidateLookup[CandidateId]
        for Signal, CandidateId in Result.SelectedCandidateIds
    }
    AssignmentExpansionCount = InitialAssignmentExpansionCount
    RepairIterations = []
    ReroutedSignals: set[str] = set()
    if CoarsePlan is not None:
        CongestionHistory: Counter[Position2] = Counter()

        def SelectionQuality(
            Values: dict[str, NetRouteCandidate],
        ) -> tuple[int, int, int, int]:
            ColumnUsage = Counter(
                (X, Z)
                for Candidate in Values.values()
                for X, _Y, Z in Candidate.Nodes
            )
            return (
                max((Count - 1 for Count in ColumnUsage.values()), default=0),
                sum(Candidate.BendCount for Candidate in Values.values()),
                sum(Candidate.ViaCount for Candidate in Values.values()),
                sum(Candidate.Length for Candidate in Values.values()),
            )

        CurrentQuality = SelectionQuality(Selected)
        StagnationCount = 0
        for PassIndex in range(Policy.GlobalRouting.MaximumRipupPasses):
            ColumnUsage = Counter(
                (X, Z)
                for Candidate in Selected.values()
                for X, _Y, Z in Candidate.Nodes
            )
            OverflowColumns = {
                Position: Count - 1
                for Position, Count in ColumnUsage.items()
                if Count > 2
            }
            if not OverflowColumns:
                break
            Contributions = Counter(
                {
                    Signal: sum(
                        OverflowColumns.get((X, Z), 0)
                        for X, _Y, Z in Candidate.Nodes
                    )
                    for Signal, Candidate in Selected.items()
                }
            )
            Offenders = tuple(
                Signal
                for Signal, Score in sorted(
                    Contributions.items(),
                    key=lambda Value: (-Value[1], Value[0]),
                )
                if Score > 0
            )[:4]
            if not Offenders:
                break
            CongestionHistory.update(OverflowColumns)
            RepairSets = {
                Signal: (
                    CandidatesBySignal[Signal]
                    if Signal in Offenders
                    else [Selected[Signal]]
                )
                for Signal in Selected
            }
            RepairResult = PlanAssignment(
                EncodeCandidateValues(RepairSets, CongestionHistory)
            )
            if not RepairResult.Success:
                break
            Repaired = {
                Signal: CandidateLookup[CandidateId]
                for Signal, CandidateId in RepairResult.SelectedCandidateIds
            }
            RepairedQuality = SelectionQuality(Repaired)
            RepairIterations.append(
                RoutingIterationMetrics(
                    Iteration=PassIndex + 2,
                    Stage="Localized congestion repair",
                    ConflictCount=0,
                    ReroutedNets=sum(
                        Repaired[Signal].CandidateId != Selected[Signal].CandidateId
                        for Signal in Selected
                    ),
                    AverageLength=(
                        sum(Value.Length for Value in Repaired.values())
                        / len(Repaired)
                    ),
                    BendCount=sum(Value.BendCount for Value in Repaired.values()),
                    ViaCount=sum(Value.ViaCount for Value in Repaired.values()),
                )
            )
            if RepairedQuality >= CurrentQuality:
                StagnationCount += 1
                if StagnationCount >= Policy.GlobalRouting.StagnationPassLimit:
                    break
                continue
            StagnationCount = 0
            ReroutedSignals.update(
                Signal
                for Signal in Selected
                if Repaired[Signal].CandidateId != Selected[Signal].CandidateId
            )
            Selected = Repaired
            CurrentQuality = RepairedQuality
            Result = RepairResult

        # Once overflow is bounded, make a small deterministic sweep over the
        # worst-shaped nets.  This is intentionally separate from congestion
        # cleanup so a via/length improvement cannot reintroduce overflow.
        ShapeOrder = tuple(
            sorted(
                Selected,
                key=lambda Signal: (
                    -Selected[Signal].ViaCount,
                    -Selected[Signal].BendCount,
                    -Selected[Signal].Length,
                    Signal,
                ),
            )
        )
        ShapeBatchSize = max(1, min(6, len(ShapeOrder)))
        for ShapePass in range(Policy.GlobalRouting.MaximumRipupPasses):
            Start = ShapePass * ShapeBatchSize
            ShapeOffenders = ShapeOrder[Start:Start + ShapeBatchSize]
            if not ShapeOffenders:
                break
            RepairSets = {
                Signal: (
                    CandidatesBySignal[Signal]
                    if Signal in ShapeOffenders
                    else [Selected[Signal]]
                )
                for Signal in Selected
            }
            ShapeResult = PlanAssignment(
                EncodeCandidateValues(RepairSets, OptimizeShape=True)
            )
            if not ShapeResult.Success:
                continue
            Shaped = {
                Signal: CandidateLookup[CandidateId]
                for Signal, CandidateId in ShapeResult.SelectedCandidateIds
            }
            ShapedQuality = SelectionQuality(Shaped)
            RepairIterations.append(
                RoutingIterationMetrics(
                    Iteration=len(RepairIterations) + 2,
                    Stage="Localized shape repair",
                    ConflictCount=0,
                    ReroutedNets=sum(
                        Shaped[Signal].CandidateId != Selected[Signal].CandidateId
                        for Signal in Selected
                    ),
                    AverageLength=sum(Value.Length for Value in Shaped.values()) / len(Shaped),
                    BendCount=sum(Value.BendCount for Value in Shaped.values()),
                    ViaCount=sum(Value.ViaCount for Value in Shaped.values()),
                )
            )
            if ShapedQuality >= CurrentQuality:
                continue
            ReroutedSignals.update(
                Signal
                for Signal in Selected
                if Shaped[Signal].CandidateId != Selected[Signal].CandidateId
            )
            Selected = Shaped
            CurrentQuality = ShapedQuality
            Result = ShapeResult
    if DiagnosticCallback is not None:
        SelectedValues = tuple(Selected.values())
        DiagnosticCallback(
            RoutingIterationMetrics(
                Iteration=1,
                Stage="Authoritative assignment",
                ConflictCount=0,
                ReroutedNets=len(ReroutedSignals),
                AverageLength=(
                    sum(Value.Length for Value in SelectedValues)
                    / len(SelectedValues)
                ),
                BendCount=sum(Value.BendCount for Value in SelectedValues),
                ViaCount=sum(Value.ViaCount for Value in SelectedValues),
            ),
            None,
        )
    ClaimsBySignal = {Signal: Value.Claims for Signal, Value in Selected.items()}
    Conflicts = FindClaimConflicts(ClaimsBySignal)
    if Conflicts:
        First = min(Conflicts, key=str)
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.FinalDrcViolation,
                Stage="AssignmentDrc",
                AffectedNets=Conflicts[First],
                Resources=(str(First),),
                Locations=(First.Position,),
                Detail="Rust assignment disagrees with authoritative Python claims",
            )
        )

    SignalOrder = tuple(sorted(Selected))
    PortalLookup = {
        Portal.PortalId: Portal
        for Values in Portals.values()
        for Portal in Values
    }
    ResourceClaimsBySignal = {
        Signal: frozenset(
            Resource
            for Resource in Candidate.Claims.ResourceIds
            if Resource.Kind != RoutingResourceKind.Electrical
        )
        for Signal, Candidate in Selected.items()
    }
    ResourceUsage = Counter(
        Resource
        for Claims in ResourceClaimsBySignal.values()
        for Resource in Claims
    )
    Plan = ChannelPlan(
        Profiles=Profiles,
        SignalOrder=SignalOrder,
        TrunkSignals=frozenset(
            Signal for Signal, Profile in Profiles.items() if Profile.IsTrunk
        ),
        Guides={Signal: Candidate.Guide for Signal, Candidate in Selected.items()},
        CorridorUsage={},
        CorridorCosts={},
        CorridorCapacity=1,
        Layers={Signal: Candidate.Layer for Signal, Candidate in Selected.items()},
        ResourceUsage=dict(ResourceUsage),
        ResourceOverflow={},
        ResourceClaimsBySignal=ResourceClaimsBySignal,
        SourceAccessTransitions={
            Signal: tuple(
                dict.fromkeys(
                    (
                        *Profiles[Signal].SourceAccessPath,
                        *PortalLookup[Candidate.SourcePortalId].Path,
                    )
                )
            )
            for Signal, Candidate in Selected.items()
        },
        TargetAccessTransitions={
            Signal: {
                Target: tuple(
                    dict.fromkeys(
                        (
                            *Profiles[Signal].TargetAccessPaths[Target],
                            *PortalLookup[PortalId].Path,
                        )
                    )
                )
                for Target, PortalId in Candidate.TargetPortalIds.items()
            }
            for Signal, Candidate in Selected.items()
        },
    )
    Producers = {
        Signal: Gate
        for Gate in Placed.PlacedGates
        if Gate.OutputPin is not None
        for Signal in Gate.Outputs
    }
    Targets = {
        Signal: list(Profile.Targets) for Signal, Profile in Profiles.items()
    }
    NetWires = {Signal: set(Candidate.Nodes) for Signal, Candidate in Selected.items()}
    FinalColumnContributors: dict[Position2, list[str]] = defaultdict(list)
    for Signal, Positions in NetWires.items():
        for Column in {(X, Z) for X, _Y, Z in Positions}:
            FinalColumnContributors[Column].append(Signal)
    FinalColumnOverflowHotspots = [
        {
            "Column": list(Column),
            "Count": len(Signals),
            "Signals": sorted(Signals),
        }
        for Column, Signals in sorted(
            FinalColumnContributors.items(),
            key=lambda Value: (-len(Value[1]), Value[0]),
        )
        if len(Signals) > 2
    ][:8]
    Supports = {
        (X, Y - 1, Z)
        for Positions in NetWires.values()
        for X, Y, Z in Positions
    } - set(Resources.StaticGeometry.ActualBlocks)
    PhysicalGraphs = BuildPhysicalGraphs(
        NetWires,
        Resources.StaticGeometry.ActualBlocks,
        Supports,
        Resources.StaticGeometry.SolidBlocks,
    )
    ValidatePhysicalRoutes(PhysicalGraphs, Producers, Targets)
    Tracks = {}
    Owners: dict[RoutingResourceId, list[str]] = defaultdict(list)
    for Signal, Candidate in Selected.items():
        Graph = PhysicalGraphs[Signal]
        Reservations, Paths = _ReserveRepeaters(
            Signal,
            Producers[Signal].OutputPin,
            tuple(Targets[Signal]),
            Graph,
            Technology,
        )
        for Resource in ResourceClaimsBySignal[Signal]:
            Owners[Resource].append(Signal)
        SourcePortal = PortalLookup[Candidate.SourcePortalId]
        TargetPortals = {
            Target: PortalLookup[Candidate.TargetPortalIds[Target]]
            for Target in Profiles[Signal].Targets
        }
        Tracks[Signal] = AssignedTrack(
            Signal=Signal,
            TrackId=Candidate.CandidateId,
            Layer=Candidate.Layer,
            Guide=Candidate.Guide,
            RepeaterSites=frozenset(
                (Position[0], Position[2]) for Position in Candidate.RepeaterWaypoints
            ),
            RepeaterWaypointsByTarget={Target: () for Target in Targets[Signal]},
            ReservedResources=ResourceClaimsBySignal[Signal],
            RepeaterReservations=Reservations,
            AssignedPathsByTarget=Paths,
            SourcePinAccessPath=tuple(
                dict.fromkeys((*Profiles[Signal].SourceAccessPath, *SourcePortal.Path))
            ),
            TargetPinAccessPathsByTarget={
                Target: tuple(
                    dict.fromkeys(
                        (
                            *reversed(TargetPortals[Target].Path),
                            *reversed(Profiles[Signal].TargetAccessPaths[Target]),
                        )
                    )
                )
                for Target in Targets[Signal]
            },
            SelectedPortalIds=(
                Candidate.SourcePortalId,
                *(Candidate.TargetPortalIds[Target] for Target in Targets[Signal]),
            ),
            OwnedNodes=Candidate.Nodes,
            OwnedEdges=Candidate.Edges,
        )
    TrackAssignmentValue = TrackAssignment(
        Tracks=Tracks,
        ResourceOwners={Resource: tuple(Values) for Resource, Values in Owners.items()},
    )
    Repeaters = MaterializeReservedRepeaters(
        NetWires,
        Producers,
        Targets,
        PhysicalGraphs,
        Tracks,
        Technology,
    )
    Assignment = RoutingAssignment(
        SelectedCandidates=Selected,
        ResourceOwners=TrackAssignmentValue.ResourceOwners,
        ExpansionCount=AssignmentExpansionCount,
        PortalCount=sum(len(Values) for Values in Portals.values()),
        CandidateCount=len(CandidateLookup),
    )
    OwnershipCounts = Counter(
        Resource.Kind.value
        for Claims in ClaimsBySignal.values()
        for Resource in Claims.ResourceIds
    )
    StageTimings["Total"] = monotonic() - RoutingStarted
    return RoutedDesign(
        Module=Placed.Module,
        PlacedGates=Placed.PlacedGates,
        Wires=sorted(set().union(*NetWires.values())),
        Supports=sorted(Supports),
        Repeaters=Repeaters,
        NetWires={Signal: sorted(Positions) for Signal, Positions in NetWires.items()},
        RoutingMetrics=MeasureRoutingStage(
            "Authoritative Rust",
            NetWires,
            Plan,
            ReroutedNets=len(ReroutedSignals),
            Iterations=tuple(RepairIterations),
        ),
        GlobalPlan=Plan,
        TrackAssignment=TrackAssignmentValue,
        TechnologyVersion=Technology.TechnologyVersion,
        EffectivePolicy=Policy.ToDictionary(),
        ResourceGraphVersion=Resources.ResourceGraph.GraphVersion,
        ResourceGraphNodeCount=Resources.ResourceGraph.CachedNodeCount,
        ResourceGraphEdgeCount=Resources.ResourceGraph.CachedEdgeCount,
        ResourceOwnershipCounts=dict(OwnershipCounts),
        RepeaterReservationCount=sum(len(Track.RepeaterReservations) for Track in Tracks.values()),
        ZeroResourceConflicts=True,
        RoutingAssignment=Assignment,
        PortalCount=Assignment.PortalCount,
        RouteCandidateCount=Assignment.CandidateCount,
        CandidateRequestCount=CandidateRequestCount,
        CandidateExpansionLimit=max(CandidateExpansionLimits.values()),
        AssignmentExpansionCount=Assignment.ExpansionCount,
        RoutingStageTimings={
            Stage: round(Seconds, 6) for Stage, Seconds in StageTimings.items()
        },
        GlobalGuideDiagnostics=(
            CoarsePlan.ToDictionary() if CoarsePlan is not None else {}
        ),
        RoutingControlEffectiveness={
            "GuideFirstEnabled": CoarsePlan is not None,
            "StrictLocalGuideCount": (
                len(CoarsePlan.LocalSignals) if CoarsePlan is not None else 0
            ),
            "GuidePlanningPasses": (
                len(CoarsePlan.Iterations) if CoarsePlan is not None else 0
            ),
            "GuideOverflowPeak": (
                CoarsePlan.OverflowPeak if CoarsePlan is not None else 0
            ),
            "CandidateBendWeight": Policy.DetailedRouting.CandidateBendWeight,
            "CandidateViaWeight": Policy.DetailedRouting.CandidateViaWeight,
            "LayerPenalty": Policy.DetailedRouting.LayerPenalty,
            "RoutingDemandEstimate": Demand.ToDictionary(),
            "DerivedRoutingBudget": AdaptiveBudget.ToDictionary(),
            "EffectiveAdaptiveControls": {
                "LayerCount": LayerCount,
                "MaximumPortalsPerTerminal": PortalLimit,
                "LaneCount": RouteLaneCount,
                "MaximumCandidatesPerNet": MaximumCandidates,
                "CandidateLimitsBySignal": dict(sorted(CandidateLimitsBySignal.items())),
            },
            "AdaptiveEscalationHistory": list(EscalationHistory),
            "RustAssignmentUsed": True,
            "RustAssignmentExpansionLimit": AssignmentExpansionLimit,
            "RustAssignmentExpansions": InitialAssignmentExpansionCount,
            "LocalizedRepairPasses": len(RepairIterations),
            "LocalizedReroutedNetCount": len(ReroutedSignals),
            "FinalColumnOverflowHotspots": FinalColumnOverflowHotspots,
            "CandidateRejectionReasons": {
                Signal: Values.get("Rejections", {})
                for Signal, Values in sorted(CandidateDiagnostics.items())
            },
            "LocalGlobalTargetCounts": {
                Signal: {
                    "LocalTargets": (
                        len(Profile.Seed.ConnectedTargets)
                        if Profile.Seed is not None
                        else 0
                    ),
                    "GlobalTargets": len(Profile.Targets),
                }
                for Signal, Profile in sorted(Profiles.items())
            },
            "IncrementalExtensions": {
                Signal: {
                    "FullTreeLength": Candidate.Length,
                    "IncrementalLength": Candidate.IncrementalLength,
                    "IncrementalMaterial": Candidate.IncrementalMaterialCost,
                    "ReusedLocalNodeCount": Candidate.SeedNodeCount,
                    "AvoidedDuplicateTrunkNodes": Candidate.SeedNodeCount,
                }
                for Signal, Candidate in sorted(Selected.items())
            },
            "SameSignalReuseNodeCount": sum(
                Candidate.SeedNodeCount for Candidate in Selected.values()
            ),
            "LayerDeviations": {
                Signal: {
                    "SelectedLayer": Candidate.Layer,
                    "PreferredLayer": (
                        Policy.Organization.PreferredXLayer
                        if ":X:" in Candidate.CandidateId
                        else Policy.Organization.PreferredZLayer
                    ),
                }
                for Signal, Candidate in sorted(Selected.items())
                if Policy.Organization.Enabled
                and Candidate.Layer
                != (
                    Policy.Organization.PreferredXLayer
                    if ":X:" in Candidate.CandidateId
                    else Policy.Organization.PreferredZLayer
                )
            },
        },
    )

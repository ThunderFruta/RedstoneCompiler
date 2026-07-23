"""Rust-backed authoritative portal generation and exact route assignment."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from math import ceil, sqrt
from dataclasses import dataclass, replace
import os
from time import monotonic
from typing import Any, Callable

try:
    from ..RustRouting import (
        GetRoutingThreadCount as GetRustRoutingThreadCount,
        RoutingContext as RustRoutingContext,
    )
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import (
            GetRoutingThreadCount as GetRustRoutingThreadCount,
            RoutingContext as RustRoutingContext,
        )
    except Exception:
        RustRoutingContext = None

        def GetRustRoutingThreadCount() -> int:
            return 1

from ..Placement.Rotation import RotatedCellSize
from .Actions import (
    BuildPhysicalGraphs,
    MaterializeReservedRepeaters,
    PropagateRoutePower,
    PruneRedundantRepeaterReservations,
    ValidatePhysicalRoutes,
)
from .ChannelPlanner import (
    BuildNetRoutingProfiles,
    CandidateLanes,
    ChannelPlan,
    MeasureRoutingStage,
    RasterizeChannelSegment,
    RoutingIterationMetrics,
    NegotiatedRoutePlan,
)
from .Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from .Models import RoutedDesign, RoutingResources
from .Reliability import (
    BuildRoutingDeadlineDiagnostics,
    BuildStableFingerprint,
    ChooseRoutingEscalationAction,
    EnforceRoutingRuntimeLimit,
    HasAdaptiveEscalationBudget,
    RemainingRoutingRuntimeMilliseconds,
    RetainUnaffectedCandidateCache,
    SelectBoundedDiverseCandidatePool,
    RoutingDeadline,
    RoutingEscalationState,
)
from .Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from .ResourceGraph import (
    FindClaimConflicts,
    FindSelfClaimConflicts,
    BuildRoutingEnvelope,
    IndexedRoutingResourceGraph,
    LocalRouteClaim,
    NetRouteCandidate,
    NormalizeRoutingEdge,
    PinAccessPortal,
    PortalReservation,
    RoutingAssignment,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
    RoutingResourceClaims,
    ValidateLocalRouteClaims,
)
from ..Placement.Geometry import GetGateInputAccess
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


@dataclass(frozen=True)
class RepeatedWorkTransition:
    """Bounded response when an escalation reproduces identical work."""

    Action: str
    SkipStrictPortalReservation: bool
    Deadline: RoutingDeadline


@dataclass
class NegotiatedRegionState:
    """Mutable per-signal ownership of one lazily expanded route region."""

    Signal: str
    TileSize: int
    Bounds: tuple[int, int, int, int, int, int]
    ActiveTiles: set[Position2]
    ActiveColumns: set[Position2]
    AddedNodes: set[Position3]
    AddedEdges: set[tuple[Position3, Position3]]
    BoundaryTouches: set[Position3]
    ExpandedSides: list[str]
    ExpansionEvents: list[dict[str, object]]


@dataclass(frozen=True)
class NegotiatedRouteTreeState:
    """Branch-level repair state retained between negotiated passes."""

    Candidate: NetRouteCandidate
    RetainedTargets: tuple[Position3, ...]
    PrunedTargets: tuple[Position3, ...]
    RetainedBranchPaths: tuple[tuple[Position3, ...], ...]
    PrunedBranchPaths: tuple[tuple[Position3, ...], ...]
    PrunedBranchTailPaths: tuple[tuple[Position3, ...], ...]
    RetainedBranchClaims: tuple[frozenset[RoutingResourceId], ...]
    PrunedBranchClaims: tuple[frozenset[RoutingResourceId], ...]
    PrunedBranchTailClaims: tuple[frozenset[RoutingResourceId], ...]
    RetainedBranchIds: tuple[Position3, ...]
    PrunedBranchIds: tuple[Position3, ...]
    RetainedNodes: tuple[Position3, ...]
    PrunedNodes: tuple[Position3, ...]
    SharedTrunkNodes: tuple[Position3, ...]


def _NegotiatedTileForColumn(
    Column: Position2,
    Bounds: tuple[int, int, int, int, int, int],
    TileSize: int,
) -> Position2:
    MinimumX, _MaximumX, _MinimumY, _MaximumY, MinimumZ, _MaximumZ = Bounds
    return (
        (Column[0] - MinimumX) // TileSize,
        (Column[1] - MinimumZ) // TileSize,
    )


def _NegotiatedTileIntersectsBounds(
    Tile: Position2,
    Bounds: tuple[int, int, int, int, int, int],
    TileSize: int,
) -> bool:
    MinimumX, MaximumX, _MinimumY, _MaximumY, MinimumZ, MaximumZ = Bounds
    TileMinimumX = MinimumX + Tile[0] * TileSize
    TileMinimumZ = MinimumZ + Tile[1] * TileSize
    return (
        TileMinimumX <= MaximumX
        and TileMinimumX + TileSize - 1 >= MinimumX
        and TileMinimumZ <= MaximumZ
        and TileMinimumZ + TileSize - 1 >= MinimumZ
    )


def BuildNegotiatedInitialTiles(
    GuideColumns: set[Position2] | frozenset[Position2],
    Bounds: tuple[int, int, int, int, int, int],
    TileSize: int,
) -> frozenset[Position2]:
    """Return guide tiles plus one complete negotiated tile halo."""
    if TileSize < 1:
        raise ValueError("Negotiated tile size must be positive")
    GuideTiles = {
        _NegotiatedTileForColumn(Column, Bounds, TileSize)
        for Column in GuideColumns
    }
    Result = {
        (TileX + DeltaX, TileZ + DeltaZ)
        for TileX, TileZ in GuideTiles
        for DeltaX in (-1, 0, 1)
        for DeltaZ in (-1, 0, 1)
        if _NegotiatedTileIntersectsBounds(
            (TileX + DeltaX, TileZ + DeltaZ),
            Bounds,
            TileSize,
        )
    }
    return frozenset(Result)


def BuildNegotiatedInitialColumns(
    GuideColumns: set[Position2] | frozenset[Position2],
    Bounds: tuple[int, int, int, int, int, int],
    HaloSize: int,
) -> frozenset[Position2]:
    """Return the exact clipped block-column halo around a sparse guide."""
    if HaloSize < 1:
        raise ValueError("Negotiated halo size must be positive")
    MinimumX, MaximumX, _MinimumY, _MaximumY, MinimumZ, MaximumZ = Bounds
    return frozenset(
        (X + DeltaX, Z + DeltaZ)
        for X, Z in GuideColumns
        for DeltaX in range(-HaloSize, HaloSize + 1)
        for DeltaZ in range(-HaloSize, HaloSize + 1)
        if MinimumX <= X + DeltaX <= MaximumX
        and MinimumZ <= Z + DeltaZ <= MaximumZ
    )


def BuildNegotiatedFallbackGuideColumns(
    Profile: Any,
    Bounds: tuple[int, int, int, int, int, int],
    Routes: list[tuple[Any, ...]],
) -> frozenset[Position2]:
    """Return non-empty fallback ownership columns from required profile geometry."""
    ProfileColumns: set[Position2] = set()
    for Path in (
        Profile.SourceAccessPath,
        Profile.Root,
        *tuple(Profile.TargetAccessPaths.values()),
        *(
            claim_node
            for Claim in (Profile.Seed.LocalClaims if Profile.Seed is not None else ())
            for claim_node in Claim.Nodes
        ),
    ):
        if Path and isinstance(Path[0], tuple):
            ProfileColumns.update((Value[0], Value[2]) for Value in Path)
        elif Path:
            ProfileColumns.update(((Path[0], Path[2]),))
    for Request in Routes:
        if len(Request) > 2:
            ProfileColumns.update(
                (Value[0], Value[2]) for Value in Request[0]
            )
    if ProfileColumns:
        return frozenset(ProfileColumns)
    MinimumX, _MaximumX, _MinimumY, _MaximumY, MinimumZ, _MaximumZ = Bounds
    return frozenset({(MinimumX, MinimumZ)})


def NegotiatedColumnsForTiles(
    Tiles: set[Position2] | frozenset[Position2],
    Bounds: tuple[int, int, int, int, int, int],
    TileSize: int,
) -> frozenset[Position2]:
    """Materialize only physical X/Z columns owned by the selected tiles."""
    MinimumX, MaximumX, _MinimumY, _MaximumY, MinimumZ, MaximumZ = Bounds
    return frozenset(
        (X, Z)
        for TileX, TileZ in Tiles
        for X in range(
            max(MinimumX, MinimumX + TileX * TileSize),
            min(MaximumX, MinimumX + (TileX + 1) * TileSize - 1) + 1,
        )
        for Z in range(
            max(MinimumZ, MinimumZ + TileZ * TileSize),
            min(MaximumZ, MinimumZ + (TileZ + 1) * TileSize - 1) + 1,
        )
    )


def FindNegotiatedBoundaryTouches(
    Nodes: set[Position3] | frozenset[Position3],
    ActiveTiles: set[Position2] | frozenset[Position2],
    Bounds: tuple[int, int, int, int, int, int],
    TileSize: int,
) -> dict[str, tuple[Position3, ...]]:
    """Identify route nodes that touch an expandable active-region side."""
    MinimumX, MaximumX, _MinimumY, _MaximumY, MinimumZ, MaximumZ = Bounds
    Touches: dict[str, set[Position3]] = defaultdict(set)
    for Node in Nodes:
        X, _Y, Z = Node
        TileX, TileZ = _NegotiatedTileForColumn((X, Z), Bounds, TileSize)
        TileMinimumX = max(MinimumX, MinimumX + TileX * TileSize)
        TileMaximumX = min(MaximumX, TileMinimumX + TileSize - 1)
        TileMinimumZ = max(MinimumZ, MinimumZ + TileZ * TileSize)
        TileMaximumZ = min(MaximumZ, TileMinimumZ + TileSize - 1)
        for Side, IsTouch, Neighbor in (
            ("MinimumX", X == TileMinimumX, (TileX - 1, TileZ)),
            ("MaximumX", X == TileMaximumX, (TileX + 1, TileZ)),
            ("MinimumZ", Z == TileMinimumZ, (TileX, TileZ - 1)),
            ("MaximumZ", Z == TileMaximumZ, (TileX, TileZ + 1)),
        ):
            if (
                IsTouch
                and Neighbor not in ActiveTiles
                and _NegotiatedTileIntersectsBounds(Neighbor, Bounds, TileSize)
            ):
                Touches[Side].add(Node)
    return {
        Side: tuple(sorted(Values))
        for Side, Values in sorted(Touches.items())
    }


def ExpandNegotiatedTiles(
    ActiveTiles: set[Position2] | frozenset[Position2],
    Side: str,
    Bounds: tuple[int, int, int, int, int, int],
    TileSize: int,
) -> frozenset[Position2]:
    """Expand exactly one implicated side by one negotiated tile."""
    DeltaBySide = {
        "MinimumX": (-1, 0),
        "MaximumX": (1, 0),
        "MinimumZ": (0, -1),
        "MaximumZ": (0, 1),
    }
    if Side not in DeltaBySide:
        raise ValueError(f"Unknown negotiated expansion side: {Side}")
    DeltaX, DeltaZ = DeltaBySide[Side]
    Result = set(ActiveTiles)
    for TileX, TileZ in sorted(ActiveTiles):
        Neighbor = (TileX + DeltaX, TileZ + DeltaZ)
        if (
            Neighbor not in ActiveTiles
            and _NegotiatedTileIntersectsBounds(Neighbor, Bounds, TileSize)
        ):
            Result.add(Neighbor)
    return frozenset(Result)


def BuildNegotiatedRouteTreeState(
    Candidate: NetRouteCandidate,
    ConflictResources: set[RoutingResourceId] | frozenset[RoutingResourceId],
    Resources: RoutingResources | None = None,
) -> NegotiatedRouteTreeState:
    """Build branch-preserving repair state from exact conflict contributors."""
    CandidatePaths = dict(sorted(Candidate.TargetPaths.items()))
    EmptyClaims = RoutingResourceClaims()
    ConflictResourceSet = set(ConflictResources)
    if not CandidatePaths:
        return NegotiatedRouteTreeState(
            Candidate=Candidate,
            RetainedTargets=(),
            PrunedTargets=(),
            RetainedBranchPaths=(),
            PrunedBranchPaths=(),
            PrunedBranchTailPaths=(),
            RetainedBranchClaims=(),
            PrunedBranchClaims=(),
            PrunedBranchTailClaims=(),
            RetainedBranchIds=(),
            PrunedBranchIds=(),
            RetainedNodes=(),
            PrunedNodes=(),
            SharedTrunkNodes=(),
        )

    Source = next(iter(CandidatePaths.values()))[0]
    ConflictPositions = {
        Resource.Position for Resource in ConflictResourceSet
    }

    def BranchTouchesConflict(
        Path: tuple[Position3, ...],
        Claims: RoutingResourceClaims,
    ) -> bool:
        if Claims.ResourceIds & ConflictResourceSet:
            return True
        if not ConflictResourceSet:
            return False
        for Position in Path:
            for Conflict in ConflictPositions:
                if (
                    abs(Position[0] - Conflict[0])
                    + abs(Position[1] - Conflict[1])
                    + abs(Position[2] - Conflict[2])
                ) <= 1:
                    return True
        return False

    def BranchConflictIndex(
        Path: tuple[Position3, ...],
        Claims: RoutingResourceClaims,
    ) -> int:
        if not ConflictResourceSet:
            return -1
        ConflictIndex = -1
        for Index, Position in enumerate(Path):
            if not ConflictPositions:
                break
            for Conflict in ConflictPositions:
                if (
                    abs(Position[0] - Conflict[0])
                    + abs(Position[1] - Conflict[1])
                    + abs(Position[2] - Conflict[2])
                ) <= 1:
                    ConflictIndex = Index
        if Claims.ResourceIds & ConflictResourceSet:
            # Keep the most conservative possible span when overlap is known
            # but no explicit route-node locality can be recovered.
            return len(Path) - 1 if ConflictIndex < 0 else ConflictIndex
        if ConflictIndex >= 0:
            return ConflictIndex
        return -1

    def BranchClaimsForPath(
        Path: tuple[Position3, ...],
        FallbackClaims: RoutingResourceClaims,
    ) -> frozenset[RoutingResourceId]:
        if Resources is None or not Path:
            return frozenset(FallbackClaims.ResourceIds)
        return frozenset(
            Resources.ResourceGraph.BuildRouteClaims(Path).ResourceIds
        )

    NodeNeighbors: dict[Position3, set[Position3]] = defaultdict(set)
    for Path in CandidatePaths.values():
        for First, Second in zip(Path, Path[1:]):
            NodeNeighbors[First].add(Second)
            NodeNeighbors[Second].add(First)

    NodeDegree: dict[Position3, int] = {
        Node: len(Neighbors)
        for Node, Neighbors in NodeNeighbors.items()
    }

    def IsBranchPoint(Node: Position3) -> bool:
        return NodeDegree.get(Node, 0) >= 3

    RetainedTargetPaths: dict[Position3, tuple[Position3, ...]] = {}
    PrunedTargetPaths: dict[Position3, tuple[Position3, ...]] = {}
    PrunedTailPaths: dict[Position3, tuple[Position3, ...]] = {}
    RetainedTargetClaims: dict[Position3, frozenset[RoutingResourceId]] = {}
    PrunedTargetClaims: dict[Position3, frozenset[RoutingResourceId]] = {}
    PrunedTailClaims: dict[Position3, frozenset[RoutingResourceId]] = {}
    RetainedNodes: set[Position3] = set()
    PrunedNodes: set[Position3] = set()
    SharedNodeCounts: Counter[Position3] = Counter()

    for Target, Path in CandidatePaths.items():
        if not Path:
            continue
        Claims = Candidate.BranchClaims.get(Target, EmptyClaims)
        BranchClaims = frozenset(Claims.ResourceIds)
        if not BranchTouchesConflict(Path, Claims):
            RetainedTargetPaths[Target] = Path
            RetainedTargetClaims[Target] = BranchClaims
            RetainedNodes.update(Path)
            for Position in Path:
                SharedNodeCounts[Position] += 1
            continue
        BranchpointIndex = 0
        ConflictIndex = BranchConflictIndex(Path, Claims)
        SearchStart = max(0, min(ConflictIndex, len(Path) - 1))
        for Index in range(SearchStart, -1, -1):
            if IsBranchPoint(Path[Index]):
                BranchpointIndex = Index
                break
        PrunedTailPath = tuple(Path[BranchpointIndex + 1 :])
        if not PrunedTailPath:
            RetainedTargetPaths[Target] = Path
            RetainedTargetClaims[Target] = BranchClaims
            RetainedNodes.update(Path)
            for Position in Path:
                SharedNodeCounts[Position] += 1
            continue
        PrunedPath = Path
        RetainedPath = tuple(Path[:BranchpointIndex + 1])
        PrunedTargetPaths[Target] = PrunedPath
        PrunedTailPaths[Target] = PrunedTailPath
        PrunedTargetClaims[Target] = BranchClaims
        PrunedTailClaims[Target] = (
            BranchClaimsForPath(Path, Claims)
            - BranchClaimsForPath(RetainedPath, EmptyClaims)
        )
        RetainedNodes.update(RetainedPath)
        PrunedNodes.update(PrunedTailPath)
        for Position in RetainedPath:
            SharedNodeCounts[Position] += 1

    if not PrunedTargetPaths:
        RetainedTargetPaths = CandidatePaths.copy()
        RetainedTargetClaims = {
            TargetValue: frozenset(
                Candidate.BranchClaims.get(TargetValue, EmptyClaims).ResourceIds
            )
            for TargetValue in CandidatePaths
        }
        RetainedNodes = set(
            Node
            for PathValue in CandidatePaths.values()
            for Node in PathValue
        )
        for PathValue in CandidatePaths.values():
            for Position in PathValue:
                SharedNodeCounts[Position] += 1

    RetainedTargets = tuple(sorted(RetainedTargetPaths))
    PrunedTargets = tuple(sorted(PrunedTargetPaths))
    SharedTrunkNodes = tuple(
        Position
        for Position, Count in SharedNodeCounts.items()
        if Count > 1
    )

    return NegotiatedRouteTreeState(
        Candidate=Candidate,
        RetainedTargets=RetainedTargets,
        PrunedTargets=PrunedTargets,
        RetainedBranchPaths=tuple(
            RetainedTargetPaths[Target]
            for Target in RetainedTargets
        ),
        PrunedBranchPaths=tuple(
            PrunedTargetPaths[Target]
            for Target in PrunedTargets
        ),
        PrunedBranchTailPaths=tuple(
            PrunedTailPaths[Target]
            for Target in PrunedTargets
            if Target in PrunedTailPaths
        ),
        RetainedBranchClaims=tuple(
            RetainedTargetClaims[Target]
            for Target in RetainedTargets
            if Target in RetainedTargetClaims
        ),
        PrunedBranchClaims=tuple(
            PrunedTargetClaims[Target]
            for Target in PrunedTargets
            if Target in PrunedTargetClaims
        ),
        PrunedBranchTailClaims=tuple(
            PrunedTailClaims[Target]
            for Target in PrunedTargets
            if Target in PrunedTailClaims
        ),
        RetainedBranchIds=RetainedTargets,
        PrunedBranchIds=PrunedTargets,
        RetainedNodes=tuple(sorted(RetainedNodes)),
        PrunedNodes=tuple(sorted(PrunedNodes)),
        SharedTrunkNodes=tuple(sorted(SharedTrunkNodes)),
    )


def CandidateRequestWindowOffset(
    InitialRequestsPerSignal: int,
    CandidateGrowthFactor: int,
    LayerCount: int,
    CandidateDiversityLevel: int,
) -> int:
    """Return the per-layer request offset not covered by earlier passes."""
    return sum(
        ceil(
            InitialRequestsPerSignal
            * CandidateGrowthFactor ** PreviousLevel
            / LayerCount
        )
        for PreviousLevel in range(CandidateDiversityLevel)
    )


def CandidatePortalShapeRank(
    Variant: int,
    AxisIndex: int,
    LaneIndex: int,
    LayerIndex: int,
    PortalVariantCount: int,
    LaneCount: int,
    RequestWindowOffset: int,
) -> int:
    """Rank portal starts before alternate axes/lanes, rotating on retries."""
    ShapeCount = max(1, PortalVariantCount * 2 * max(1, LaneCount))
    ShapeIndex = LaneIndex + max(1, LaneCount) * (
        Variant + PortalVariantCount * AxisIndex
    )
    return (
        ShapeIndex - RequestWindowOffset - LayerIndex
    ) % ShapeCount


@dataclass(frozen=True)
class RawPortalGeometryCache:
    """Immutable native portal work reusable across routing-control retries."""

    PlacedIdentity: int
    ResourcesIdentity: int
    Region: Any
    LayerCount: int
    PortalLimit: int
    PortalVariantCounts: tuple[tuple[str, int], ...]
    GuideExpansion: int
    StrictMaximumExpansions: int
    Context: Any
    PortalEntries: tuple[
        tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
    ]
    RequestCount: int
    TargetCount: int
    StarvationCount: int

    def Matches(
        self,
        Placed: Any,
        Resources: RoutingResources,
        Region: Any,
        LayerCount: int,
        PortalLimit: int,
        PortalVariantCounts: dict[str, int],
        GuideExpansion: int,
        StrictMaximumExpansions: int,
    ) -> bool:
        RegionIsCompatible = self.Region is Region or (
            getattr(self.Region, "Bounds", None)
            == getattr(Region, "Bounds", None)
            and getattr(self.Region, "Nodes", frozenset())
            <= getattr(Region, "Nodes", frozenset())
            and getattr(self.Region, "Edges", frozenset())
            <= getattr(Region, "Edges", frozenset())
        )
        return (
            self.PlacedIdentity == id(Placed)
            and self.ResourcesIdentity == id(Resources)
            and RegionIsCompatible
            and self.LayerCount == LayerCount
            and self.PortalLimit == PortalLimit
            and self.PortalVariantCounts
            == tuple(sorted(PortalVariantCounts.items()))
            and self.GuideExpansion == GuideExpansion
            and self.StrictMaximumExpansions == StrictMaximumExpansions
        )

    def BuildPortalDictionary(
        self,
    ) -> dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]]:
        return dict(self.PortalEntries)


@dataclass(frozen=True)
class PreparedPortalDomainCache:
    """Reserved or unreserved portal domain for one unchanged raw geometry."""

    RawPortalCache: RawPortalGeometryCache
    UnreservedPortalMode: bool
    ReservationVariant: int
    PortalEntries: tuple[
        tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
    ]
    Reservations: tuple[PortalReservation, ...]

    def Matches(
        self,
        RawPortalCache: RawPortalGeometryCache,
        UnreservedPortalMode: bool,
        ReservationVariant: int,
    ) -> bool:
        return (
            self.RawPortalCache is RawPortalCache
            and self.UnreservedPortalMode == UnreservedPortalMode
            and self.ReservationVariant == ReservationVariant
        )

    def BuildPortalDictionary(
        self,
    ) -> dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]]:
        return dict(self.PortalEntries)


def ChooseRepeatedWorkTransition(
    UnreservedPortalMode: bool,
    Deadline: RoutingDeadline,
) -> RepeatedWorkTransition:
    """Try unreserved portals once before terminating duplicate work."""
    if UnreservedPortalMode:
        return RepeatedWorkTransition(
            Action="Terminate",
            SkipStrictPortalReservation=True,
            Deadline=Deadline,
        )
    return RepeatedWorkTransition(
        Action="TryUnreservedPortals",
        SkipStrictPortalReservation=True,
        Deadline=Deadline,
    )


def SelectAuthoritativeBaseClaims(
    AllLocalClaims: tuple[LocalRouteClaim, ...],
    DisableLocalBaseClaims: bool,
) -> tuple[LocalRouteClaim, ...]:
    """Preserve every enabled placement-owned claim for exact assignment."""
    return () if DisableLocalBaseClaims else AllLocalClaims


def _CollectSignalTargets(Placed: Any) -> dict[str, tuple[Position3, ...]]:
    """Collect required route targets for every driven signal.

    This is the same terminal map used for routing profiles, including module
    outputs via OUTPUT gate inputs.
    """
    Targets: dict[str, set[Position3]] = defaultdict(set)
    for Gate in Placed.PlacedGates:
        for InputIndex, Signal in enumerate(Gate.Inputs):
            try:
                Pin, _Direction = GetGateInputAccess(Gate, InputIndex)
            except Exception:
                continue
            Targets[Signal].add(Pin)
    return {Signal: tuple(sorted(Positions)) for Signal, Positions in Targets.items()}


def GrowAssignmentExpansionLimit(
    CurrentLimit: int,
    MaximumLimit: int,
    GrowthFactor: int,
) -> int:
    """Grow exact-assignment work smoothly without exceeding its budget."""
    if CurrentLimit < 1 or MaximumLimit < 1 or GrowthFactor < 2:
        raise ValueError("assignment growth controls must be positive and growing")
    return min(MaximumLimit, CurrentLimit * GrowthFactor)


def ShouldGrowAssignmentBudget(Result: Any) -> bool:
    """Grow MRV work only when Rust stopped at its explicit work ceiling."""
    return bool(
        getattr(Result, "BudgetExhausted", False)
        and not getattr(Result, "DeadlineExceeded", False)
    )


def ShouldRunShapeOptimization(QualityTarget: str) -> bool:
    """Keep first-legal routing focused on correctness and bounded completion."""
    return QualityTarget != "first-legal"


def _ClaimsConflict(
    FirstSignal: str,
    First: Any,
    SecondSignal: str,
    Second: Any,
) -> bool:
    """Apply exact resource and electrical conflict rules to two claim sets."""
    # Keep this check strict but cheap: support material may not replace another
    # signal's wire or required air, in addition to redstone interference.
    return bool(
        First.WireCells & Second.WireCells
        or First.SupportCells & (
            Second.WireCells | Second.RequiredAirCells
        )
        or Second.SupportCells & (
            First.WireCells | First.RequiredAirCells
        )
        or First.RequiredAirCells & Second.WireCells
        or Second.RequiredAirCells & First.WireCells
        or First.ElectricalCells & Second.WireCells
        or Second.ElectricalCells & First.WireCells
    )


def ReserveBoundaryPortals(
    Portals: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    ReservationVariant: int = 0,
    MaximumExpansions: int = 50_000,
    RequireConflictFree: bool = False,
    StrictTerminalThreshold: int = 64,
) -> tuple[
    dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    tuple[PortalReservation, ...],
]:
    """Allocate one escape stem for every terminal on each layer.

    Pin access is a placement boundary, not a detailed-routing alternative.  A
    route candidate may choose its trunk later, but it must not be allowed to
    choose a foreign-conflicting stem on the way out of a cell.
    For large terminal sets, exact search is demand-capped with deterministic
    greedy fallback so routing does not stall on huge combinatorial reservations.
    """
    if MaximumExpansions < 1:
        raise ValueError("MaximumExpansions must be positive")

    def _ConflictCount(
        Signal: str,
        Candidate: PinAccessPortal,
        ReservedClaims: dict[str, list[RoutingResourceClaims]],
    ) -> int:
        return sum(
            1
            for OtherSignal, ExistingValues in ReservedClaims.items()
            if OtherSignal != Signal
            for Existing in ExistingValues
            if _ClaimsConflict(Signal, Candidate.Claims, OtherSignal, Existing)
        )

    TerminalLayers: dict[tuple[str, Position3], list[int]] = defaultdict(list)
    TerminalCandidateCounts: Counter[tuple[str, Position3]] = Counter()
    for (Signal, Terminal, Layer), Values in Portals.items():
        TerminalLayers[(Signal, Terminal)].append(Layer)
        TerminalCandidateCounts[(Signal, Terminal)] += len(Values)
    EmptyTerminal = next(
        (
            Key
            for Key in sorted(TerminalLayers)
            if TerminalCandidateCounts[Key] == 0
        ),
        None,
    )
    if EmptyTerminal is not None:
        Signal, Terminal = EmptyTerminal
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoBoundaryEscape,
                Stage="PortalReservation",
                AffectedNets=(Signal,),
                Detail="no boundary-portal geometry available on any layer",
                Diagnostics={
                    "Signal": Signal,
                    "Terminal": list(Terminal),
                    "Layers": sorted(TerminalLayers[EmptyTerminal]),
                    "PortalCandidates": 0,
                },
            )
        )

    # Keep empty per-layer domains in the returned mapping. Candidate
    # construction indexes every physical layer and deliberately skips a
    # layer unless all terminals of the signal can reach it. An inaccessible
    # individual layer is not a no-escape failure when another layer remains.
    Filtered: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]] = {
        Key: () for Key, Values in Portals.items() if not Values
    }
    Reservations: list[PortalReservation] = []
    KeysByLayer: dict[int, list[tuple[str, Position3, int]]] = defaultdict(list)
    for Key in Portals:
        KeysByLayer[Key[2]].append(Key)
    for Layer in sorted(KeysByLayer):
        Domains = {
            Key: tuple(
                Value for Value in sorted(
                    Portals[Key], key=lambda Value: (Value.Cost, Value.PortalId)
                ) if Value.Path
            )
            for Key in sorted(KeysByLayer[Layer], key=lambda Value: (Value[0], Value[1]))
            if Portals[Key]
        }
        if not Domains:
            continue

        # Exact assignment is expensive and can dominate runtime on larger designs.
        # Enable strict reservation only when demand is bounded.
        StrictMode = RequireConflictFree and (len(Domains) <= StrictTerminalThreshold)
        Selections: dict[tuple[str, Position3, int], PinAccessPortal] = {}
        ReservedClaims: dict[str, list[RoutingResourceClaims]] = defaultdict(list)
        ExpansionCount = 0

        def CompatibleValues(
            Key: tuple[str, Position3, int],
        ) -> tuple[PinAccessPortal, ...]:
            Signal = Key[0]
            Values = tuple(
                Value for Value in Domains[Key]
                if not any(
                    _ClaimsConflict(Signal, Value.Claims, OtherSignal, Existing)
                    for OtherSignal, ExistingValues in ReservedClaims.items()
                    if OtherSignal != Signal
                    for Existing in ExistingValues
                )
            )
            if not Values:
                return ()
            Offset = ReservationVariant % len(Values)
            return (*Values[Offset:], *Values[:Offset])

        def AssignEscapes() -> bool:
            nonlocal ExpansionCount
            if len(Selections) == len(Domains):
                return True
            Available = [
                (len(Values), Key, Values)
                for Key in Domains
                if Key not in Selections
                for Values in (CompatibleValues(Key),)
            ]
            if not Available:
                return False
            _Count, Key, Values = min(
                Available,
                key=lambda Value: (Value[0], Value[1][0], Value[1][1]),
            )
            if not Values:
                return False
            Signal = Key[0]
            for Value in Values:
                ExpansionCount += 1
                if ExpansionCount > MaximumExpansions:
                    return False
                Selections[Key] = Value
                ReservedClaims[Signal].append(Value.Claims)
                if AssignEscapes():
                    return True
                ReservedClaims[Signal].pop()
                if not ReservedClaims[Signal]:
                    del ReservedClaims[Signal]
                del Selections[Key]
            return False

        if StrictMode:
            if not AssignEscapes():
                Unassigned = sorted(Key for Key in Domains if Key not in Selections)
                Affected = tuple(sorted({Key[0] for Key in Unassigned}))
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                        Stage="PortalReservation",
                        AffectedNets=Affected,
                        Detail=(
                            "no conflict-free complete pin-escape assignment "
                            f"within {MaximumExpansions} deterministic expansions"
                        ),
                        Diagnostics={
                            "Layer": Layer,
                            "TerminalCount": len(Domains),
                            "ExpansionCount": ExpansionCount,
                            "MaximumExpansions": MaximumExpansions,
                            "UnassignedTerminals": [
                                {"Signal": Key[0], "Terminal": list(Key[1])}
                                for Key in Unassigned[:16]
                            ],
                            "ConflictGraph": {
                                "Classification": "saturated-boundary-cut",
                                "ConflictSignals": list(Affected),
                                "RelocationSignals": list(Affected),
                            },
                        },
                    )
                )
        else:
            # Deterministic least-conflict assignment for bounded throughput.
            for Key in sorted(
                Domains,
                key=lambda Value: (len(Domains[Value]), Value[0], Value[1]),
            ):
                Signal = Key[0]
                OrderedValues = sorted(
                    Domains[Key],
                    key=lambda Value: (
                        _ConflictCount(Signal, Value, ReservedClaims),
                        Value.Cost,
                        Value.PortalId,
                    ),
                )
                # Greedy reservation is the production path for larger
                # terminal sets.  Rotate its deterministic preference order
                # as well as the exact-search path so a reservation retry
                # changes physical portal ownership instead of repeating the
                # same work under a different control label.
                Selected = OrderedValues[
                    ReservationVariant % len(OrderedValues)
                ]
                Selections[Key] = Selected
                ReservedClaims[Signal].append(Selected.Claims)

        for Key in sorted(Selections, key=lambda Value: (Value[0], Value[1])):
            Selected = Selections[Key]
            Signal, Terminal, _ = Key
            Filtered[Key] = (Selected,)
            Reservations.append(PortalReservation(
                Signal=Signal,
                Terminal=Terminal,
                Layer=Layer,
                SlotIndex=0,
                PortalId=Selected.PortalId,
                Claims=Selected.Claims,
            ))
    return Filtered, tuple(Reservations)


def ReserveNegotiatedBoundaryEscapes(
    Portals: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    Profiles: dict[str, Any],
    Resources: RoutingResources,
    ReservationVariant: int = 0,
    MaximumExpansions: int = 50_000,
) -> tuple[
    dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    tuple[PortalReservation, ...],
]:
    """Match each net's terminals to one claim-compatible routing layer.

    Reserving every terminal on every layer over-constrains boundary capacity,
    while selecting each terminal independently can leave a net with no common
    detailed-routing layer.  Treat a net-wide layer/portal tuple as one domain
    value and match those values across signals with capacity-one claims.
    """
    if MaximumExpansions < 1:
        raise ValueError("MaximumExpansions must be positive")
    KeysBySignal: dict[str, list[tuple[str, Position3, int]]] = defaultdict(list)
    for Key, Values in Portals.items():
        if Values:
            KeysBySignal[Key[0]].append(Key)
    TerminalsBySignal = {
        Signal: tuple(sorted({Key[1] for Key in Keys}))
        for Signal, Keys in KeysBySignal.items()
    }
    Domains: dict[
        str,
        list[tuple[
            int,
            int,
            tuple[tuple[Position3, PinAccessPortal], ...],
            RoutingResourceClaims,
        ]],
    ] = {}

    def MergeClaims(
        Signal: str,
        Selection: tuple[tuple[Position3, PinAccessPortal], ...],
    ) -> RoutingResourceClaims:
        Profile = Profiles[Signal]
        MandatoryNodes = {
            Position
            for Terminal, Portal in Selection
            for Position in (
                *(
                    Profile.SourceAccessPath
                    if Terminal == Profile.Root
                    else Profile.TargetAccessPaths[Terminal]
                ),
                *Portal.Path,
            )
        }
        # Rebuild from the complete mandatory union. Unioning claims that were
        # built per stem misses cross-stem stair headroom/support aliases.
        return Resources.ResourceGraph.BuildRouteClaims(MandatoryNodes)
    for Signal in sorted(TerminalsBySignal):
        Terminals = TerminalsBySignal[Signal]
        Layers = sorted({Key[2] for Key in KeysBySignal[Signal]})
        Values = []
        for Layer in Layers:
            TerminalDomains = [
                tuple(sorted(
                    Portals.get((Signal, Terminal, Layer), ()),
                    key=lambda Portal: (Portal.Cost, Portal.PortalId),
                ))
                for Terminal in Terminals
            ]
            if any(not Domain for Domain in TerminalDomains):
                continue
            VariantCount = max(len(Domain) for Domain in TerminalDomains)
            DiagonalValues = []
            for Variant in range(VariantCount):
                Selection = tuple(
                    (
                        Terminal,
                        Domain[(Variant + TerminalIndex) % len(Domain)],
                    )
                    for TerminalIndex, (Terminal, Domain) in enumerate(
                        zip(Terminals, TerminalDomains)
                    )
                )
                Claims = MergeClaims(Signal, Selection)
                if FindSelfClaimConflicts({Signal: Claims}):
                    continue
                DiagonalValues.append((
                    sum(Portal.Cost for _Terminal, Portal in Selection),
                    Layer,
                    Selection,
                    Claims,
                ))
            if DiagonalValues:
                Values.extend(DiagonalValues)
                continue
            # A portal is legal in isolation but a net owns all of its portal
            # stems simultaneously.  Build bounded net-wide tuples and reject
            # support/headroom aliases here, before global capacity matching.
            # Run the product fallback only when the cheap diagonal set has no
            # legal tuple; this keeps the common case proportional to the old
            # reservation cost.
            SelectionBeam: list[
                tuple[
                    int,
                    tuple[tuple[Position3, PinAccessPortal], ...],
                    RoutingResourceClaims,
                ]
            ] = [
                (
                    0,
                    (),
                    Resources.ResourceGraph.BuildRouteClaims(()),
                )
            ]
            MaximumSelectionBeam = min(8, MaximumExpansions)
            for Terminal, Domain in zip(Terminals, TerminalDomains):
                NextSelections: dict[
                    tuple[str, ...],
                    tuple[
                        int,
                        tuple[tuple[Position3, PinAccessPortal], ...],
                        RoutingResourceClaims,
                    ],
                ] = {}
                for PreviousCost, PreviousSelection, _PreviousClaims in SelectionBeam:
                    for Portal in Domain:
                        Selection = (*PreviousSelection, (Terminal, Portal))
                        Claims = MergeClaims(Signal, Selection)
                        if FindSelfClaimConflicts({Signal: Claims}):
                            continue
                        PortalIds = tuple(
                            Value.PortalId for _Terminal, Value in Selection
                        )
                        Candidate = (
                            PreviousCost + Portal.Cost,
                            Selection,
                            Claims,
                        )
                        Existing = NextSelections.get(PortalIds)
                        if Existing is None or Candidate[0] < Existing[0]:
                            NextSelections[PortalIds] = Candidate
                SelectionBeam = sorted(
                    NextSelections.values(),
                    key=lambda Value: (
                        Value[0],
                        tuple(
                            Portal.PortalId
                            for _Terminal, Portal in Value[1]
                        ),
                    ),
                )[:MaximumSelectionBeam]
                if not SelectionBeam:
                    break
            Values.extend(
                (Cost, Layer, Selection, Claims)
                for Cost, Selection, Claims in SelectionBeam
                if len(Selection) == len(Terminals)
            )
        if not Values:
            raise RoutingStageError(RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage="PortalReservation",
                AffectedNets=(Signal,),
                Detail="net terminals have no common legal boundary layer",
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": "saturated-boundary-cut",
                        "ConflictSignals": [Signal],
                        "RelocationSignals": [Signal],
                    },
                },
            ))
        Domains[Signal] = sorted(
            Values,
            key=lambda Value: (Value[0], Value[1], tuple(
                Portal.PortalId for _Terminal, Portal in Value[2]
            )),
        )

    SignalOrder = tuple(sorted(Domains, key=lambda Signal: (
        len(Domains[Signal]),
        -len(TerminalsBySignal[Signal]),
        Signal,
    )))
    Selected: dict[
        str,
        tuple[
            int,
            int,
            tuple[tuple[Position3, PinAccessPortal], ...],
            RoutingResourceClaims,
        ],
    ] = {}
    ExpansionCount = 0

    def Compatible(
        Signal: str,
        Value: tuple[
            int,
            int,
            tuple[tuple[Position3, PinAccessPortal], ...],
            RoutingResourceClaims,
        ],
    ) -> tuple[bool, set[str]]:
        Blockers: set[str] = set()
        for OtherSignal, OtherValue in Selected.items():
            if _ClaimsConflict(
                Signal,
                Value[3],
                OtherSignal,
                OtherValue[3],
            ):
                Blockers.add(OtherSignal)
        return not Blockers, Blockers

    FailedCut: set[str] = set()
    BudgetExhausted = False

    def Search(Depth: int) -> bool:
        nonlocal ExpansionCount, BudgetExhausted
        if Depth >= len(SignalOrder):
            return True
        Signal = SignalOrder[Depth]
        Values = Domains[Signal]
        Offset = ReservationVariant % len(Values)
        OrderedValues = (*Values[Offset:], *Values[:Offset])
        Cut = {Signal}
        for Value in OrderedValues:
            ExpansionCount += 1
            if ExpansionCount > MaximumExpansions:
                BudgetExhausted = True
                FailedCut.update(Cut)
                return False
            IsCompatible, Blockers = Compatible(Signal, Value)
            Cut.update(Blockers)
            if not IsCompatible:
                continue
            Selected[Signal] = Value
            ForwardFeasible = True
            for RemainingSignal in SignalOrder[Depth + 1:]:
                RemainingHasValue = False
                RemainingCut = {RemainingSignal}
                for RemainingValue in Domains[RemainingSignal]:
                    RemainingCompatible, RemainingBlockers = Compatible(
                        RemainingSignal,
                        RemainingValue,
                    )
                    RemainingCut.update(RemainingBlockers)
                    if RemainingCompatible:
                        RemainingHasValue = True
                        break
                if not RemainingHasValue:
                    Cut.update(RemainingCut)
                    ForwardFeasible = False
                    break
            if ForwardFeasible and Search(Depth + 1):
                return True
            Selected.pop(Signal, None)
            if BudgetExhausted:
                return False
        FailedCut.update(Cut)
        return False

    Search(0)

    if len(Selected) != len(SignalOrder):
        Affected = tuple(sorted(FailedCut or set(SignalOrder) - set(Selected)))
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="PortalReservation",
            AffectedNets=Affected,
            Detail=(
                "no capacity-one net-layer boundary matching within "
                f"{MaximumExpansions} deterministic expansions"
            ),
            Diagnostics={
                "ExpansionCount": ExpansionCount,
                "MaximumExpansions": MaximumExpansions,
                "BudgetExhausted": BudgetExhausted,
                "MatchedSignalCount": len(Selected),
                "SignalCount": len(SignalOrder),
                "ConflictGraph": {
                    "Classification": "saturated-boundary-cut",
                    "ConflictSignals": list(Affected),
                    "RelocationSignals": list(Affected),
                },
            },
        ))

    Filtered = {Key: () for Key in Portals}
    Reservations = []
    for Signal in sorted(Selected):
        _Cost, Layer, Selection, _Claims = Selected[Signal]
        for SlotIndex, (Terminal, Portal) in enumerate(Selection):
            Key = (Signal, Terminal, Layer)
            Filtered[Key] = (Portal,)
            Reservations.append(PortalReservation(
                Signal=Signal,
                Terminal=Terminal,
                Layer=Layer,
                SlotIndex=SlotIndex,
                PortalId=Portal.PortalId,
                Claims=Portal.Claims,
            ))
    return Filtered, tuple(Reservations)


def BuildRoutingConflictGraph(
    CandidatesBySignal: dict[str, list[NetRouteCandidate]],
    Result: Any,
    ResourcePositions: tuple[Position3, ...],
    Reservations: tuple[PortalReservation, ...],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Classify an assignment failure without circuit-specific knowledge."""
    Signals = tuple(sorted(CandidatesBySignal))
    NoCandidateSignals = [
        Signal for Signal in Signals if not CandidatesBySignal[Signal]
    ]
    PairwiseEdges = []
    TotalSignalPairs = len(Signals) * max(0, len(Signals) - 1) // 2
    CompletedSignalPairs = 0
    CandidatePairChecks = 0
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "start",
            "CompletedSignalPairs": 0,
            "TotalSignalPairs": TotalSignalPairs,
            "CandidatePairChecks": 0,
        })
    for Index, FirstSignal in enumerate(Signals):
        for SecondSignal in Signals[Index + 1:]:
            Compatible = False
            for First in CandidatesBySignal[FirstSignal]:
                for Second in CandidatesBySignal[SecondSignal]:
                    CandidatePairChecks += 1
                    if WorkCheck is not None:
                        WorkCheck({
                            "Phase": "candidate-pairs",
                            "CompletedSignalPairs": CompletedSignalPairs,
                            "TotalSignalPairs": TotalSignalPairs,
                            "CandidatePairChecks": CandidatePairChecks,
                        })
                    if not _ClaimsConflict(
                        FirstSignal,
                        First.Claims,
                        SecondSignal,
                        Second.Claims,
                    ):
                        Compatible = True
                        break
                if Compatible:
                    break
            if not Compatible:
                PairwiseEdges.append([FirstSignal, SecondSignal])
            CompletedSignalPairs += 1
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "signal-pairs",
                    "CompletedSignalPairs": CompletedSignalPairs,
                    "TotalSignalPairs": TotalSignalPairs,
                    "CandidatePairChecks": CandidatePairChecks,
                })
    Hotspots = [
        list(ResourcePositions[Index])
        for Index in sorted(set(getattr(Result, "ConflictResourceIndices", ())))
        if 0 <= Index < len(ResourcePositions)
    ]
    HotspotPositions = {tuple(Value) for Value in Hotspots}
    CongestionCutSignals = sorted(
        Signal
        for Signal, Candidates in CandidatesBySignal.items()
        if any(
            Resource.Position in HotspotPositions
            for Candidate in Candidates
            for Resource in Candidate.Claims.ResourceIds
        )
    )
    NativeConflictSignals = sorted({
        str(Signal)
        for Signal in getattr(Result, "ConflictSignals", ())
    })
    if len(NativeConflictSignals) >= 3:
        Classification = "higher-order-placement-conflict"
    elif PairwiseEdges:
        Classification = "pairwise-incompatibility"
    elif NoCandidateSignals:
        Classification = "no-candidate"
    elif getattr(Result, "BudgetExhausted", False):
        Classification = "work-budget-exhaustion"
    else:
        Classification = "larger-matching-failure"
    ConflictSignals = sorted({
        *NoCandidateSignals,
        *(
            Signal
            for Pair in PairwiseEdges
            for Signal in Pair
        ),
        *NativeConflictSignals,
        *CongestionCutSignals,
        *(
            (str(Result.FailureNet),)
            if getattr(Result, "FailureNet", None)
            else ()
        ),
    })
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "CompletedSignalPairs": CompletedSignalPairs,
            "TotalSignalPairs": TotalSignalPairs,
            "CandidatePairChecks": CandidatePairChecks,
        })
    return {
        "Classification": Classification,
        "FailureNet": getattr(Result, "FailureNet", None),
        "BudgetExhausted": bool(getattr(Result, "BudgetExhausted", False)),
        "ExpansionCount": int(getattr(Result, "ExpansionCount", 0)),
        "CandidateCounts": {
            Signal: len(CandidatesBySignal[Signal]) for Signal in Signals
        },
        "NoCandidateSignals": NoCandidateSignals,
        "NativeConflictSignals": NativeConflictSignals,
        "CongestionCutSignals": CongestionCutSignals,
        "ConflictSignals": ConflictSignals,
        "PairwiseIncompatibleEdges": PairwiseEdges,
        "ResourceHotspots": Hotspots,
        "PortalReservations": [Value.ToDictionary() for Value in Reservations],
    }


def SelectPlacementRelocationSignals(
    ConflictGraph: dict[str, object],
) -> list[str]:
    """Preserve every contributor identified by the typed congestion cut."""
    Signals: set[str] = set()
    for Key in (
        "NativeConflictSignals",
        "CongestionCutSignals",
        "NoCandidateSignals",
        "ConflictSignals",
    ):
        Values = ConflictGraph.get(Key, ())
        if isinstance(Values, tuple | list):
            Signals.update(str(Value) for Value in Values)
    for Key in ("PairwiseIncompatibleEdges", "StackedConflictPairs"):
        Values = ConflictGraph.get(Key, ())
        if not isinstance(Values, tuple | list):
            continue
        Signals.update(
            str(Signal)
            for Pair in Values
            if isinstance(Pair, tuple | list)
            for Signal in Pair
        )
    FailureNet = ConflictGraph.get("FailureNet")
    if FailureNet:
        Signals.add(str(FailureNet))
    return sorted(Signals)


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


def _BuildTargetPortalBranches(
    TargetPortals: tuple[PinAccessPortal, ...],
    TargetAccessPaths: tuple[tuple[Position3, ...], ...] | None = None,
) -> list[list[Position3]]:
    """Orient complete target escapes from their outer endpoint inward."""
    if (
        TargetAccessPaths is not None
        and len(TargetAccessPaths) != len(TargetPortals)
    ):
        raise ValueError("target portal/access branch count mismatch")
    return [
        list(dict.fromkeys((
            *reversed(Portal.Path),
            *(
                reversed(TargetAccessPaths[Index])
                if TargetAccessPaths is not None
                else ()
            ),
        )))
        for Index, Portal in enumerate(TargetPortals)
    ]


def SelectGraphAccessStarts(
    AccessPath: tuple[Position3, ...],
    RegionNodes: frozenset[Position3],
) -> tuple[Position3, ...]:
    """Keep only terminal access cells represented by the routing graph."""
    return tuple(Position for Position in AccessPath if Position in RegionNodes)


def RequiredRoutingLayerCountForAccess(
    MinimumY: int,
    AccessPositions: frozenset[Position3],
    GuideExpansion: int,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> int:
    """Return the lowest layer count that can serve the highest terminal.

    A redstone stair changes elevation and horizontal position together. The
    terminal portal envelope can therefore bridge at most ``GuideExpansion``
    vertical cells before reaching a routing plane. This is a necessary,
    deterministic layer floor for vertically stacked placements.
    """
    if GuideExpansion < 0:
        raise ValueError("GuideExpansion cannot be negative")
    if not AccessPositions:
        return Technology.MinimumRoutingLayerCount
    HighestAccessY = max(Position[1] for Position in AccessPositions)
    LowestRoutingY = Technology.RoutingY(MinimumY, 0)
    RequiredRoutingY = max(
        LowestRoutingY,
        HighestAccessY - GuideExpansion,
    )
    AdditionalHeight = max(0, RequiredRoutingY - LowestRoutingY)
    return max(
        Technology.MinimumRoutingLayerCount,
        1 + ceil(AdditionalHeight / Technology.RoutingLayerPitch),
    )


def SelectInitialRoutingLayerCount(
    MinimumLayerCount: int,
    EffectiveMaximumLayerCount: int,
    RequiredAccessLayerCount: int,
    AdaptiveLayerCount: int,
    AdaptiveLayerFloor: int,
    NegotiatedLayerFloor: int,
    ExistingRouteLayerCount: int,
    PlacementWasRelocated: bool,
    ForceMaximumAfterPlacementRelocation: bool,
) -> int:
    """Return the smallest legal initial layer budget for one route attempt.

    The fixed access, retained local routes, and negotiated demand each impose
    a lower bound.  A relocation can optionally retain the legacy behavior of
    immediately using all vertical headroom; otherwise it is just another
    geometry candidate and adaptive routing grows one layer at a time.
    """
    if MinimumLayerCount < 1 or EffectiveMaximumLayerCount < MinimumLayerCount:
        raise ValueError("routing layer bounds must be positive and ordered")
    InitialLayerCount = max(
        MinimumLayerCount,
        RequiredAccessLayerCount,
        AdaptiveLayerCount,
        AdaptiveLayerFloor,
        NegotiatedLayerFloor,
        ExistingRouteLayerCount,
    )
    if PlacementWasRelocated and ForceMaximumAfterPlacementRelocation:
        InitialLayerCount = max(InitialLayerCount, EffectiveMaximumLayerCount)
    return min(EffectiveMaximumLayerCount, InitialLayerCount)


def SelectEscalatedRoutingLayerCount(
    LayerCount: int,
    EffectiveMaximumLayerCount: int,
    ConflictClassification: str,
    ForceMaximumAfterPlacementRelocation: bool,
) -> int:
    """Advance the vertical budget without skipping the adaptive ladder."""
    if LayerCount < 1 or EffectiveMaximumLayerCount < LayerCount:
        raise ValueError("routing layer bounds must be positive and ordered")
    if (
        ForceMaximumAfterPlacementRelocation
        and ConflictClassification.startswith("relocated-")
    ):
        return EffectiveMaximumLayerCount
    return min(EffectiveMaximumLayerCount, LayerCount + 1)


def _PortalFromRust(
    Signal: str,
    Terminal: Position3,
    Layer: int,
    Value: Any,
    Resources: RoutingResources,
) -> PinAccessPortal:
    CandidatePath = tuple(Value.Path)
    if not CandidatePath:
        CandidatePath = (Value.Target,)
    return PinAccessPortal(
        PortalId=f"{Signal}:{Terminal}:{Layer}:{Value.PortalId}",
        Signal=Signal,
        Terminal=Terminal,
        Layer=Layer,
        Path=CandidatePath,
        Edges=frozenset(
            NormalizeRoutingEdge(First, Second)
            for First, Second in zip(CandidatePath, CandidatePath[1:])
        ),
        Claims=Resources.ResourceGraph.BuildRouteClaims(CandidatePath),
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
    Resources: Any,
) -> dict[Position3, list[Position3]]:
    Result = {Position: [] for Position in Nodes}
    for Position in sorted(Nodes):
        for Neighbor in DefaultRedstoneRoutingTechnology.NeighborPositions(Position):
            if Neighbor not in Nodes:
                continue
            if Resources.BuildPrimitive(Position, Neighbor) is not None:
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


def _FindComponentNodes(
    Graph: dict[Position3, list[Position3]],
    Start: Position3,
) -> set[Position3]:
    """Return the BFS component reachable from Start in Graph."""
    if Start not in Graph:
        return set()
    Result: set[Position3] = {Start}
    Pending = deque((Start,))
    while Pending:
        Current = Pending.popleft()
        for Neighbor in Graph.get(Current, ()):
            if Neighbor in Result:
                continue
            Result.add(Neighbor)
            Pending.append(Neighbor)
    return Result


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
                ):
                    Candidates.append(Index)
            if not Candidates:
                return (), {}
            # Select the latest legal site.  The subsequent power validation
            # remains authoritative, while this avoids the old fixed safety
            # margin placing a repeater early on every long segment.
            Selected = max(Candidates)
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
    Reservations = tuple(Reserved[Position] for Position in sorted(Reserved))
    Reservations = PruneRedundantRepeaterReservations(
        Root,
        Targets,
        Graph,
        Reservations,
        Technology,
    )
    return Reservations, Paths


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
    RepeaterPenalty: int = 2,
    NativeRepeaterReservations: tuple[tuple[Position3, str], ...] = (),
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
    SelfClaimConflicts = FindSelfClaimConflicts({Signal: Claims})
    if SelfClaimConflicts:
        if os.environ.get("RCS_DEBUG_MATERIALIZE") == Signal:
            print(
                "[debug] authoritative: self-claim conflicts "
                f"signal={Signal} conflicts="
                f"{tuple(sorted(SelfClaimConflicts, key=str))} "
                f"tree={tuple(RoutedTree)}",
                flush=True,
            )
        if RejectionCounts is not None:
            RejectionCounts["SelfClaimConflict"] += 1
        return None
    Graph = _BuildCandidateGraph(Nodes, Resources.ResourceGraph)
    RootComponent = _FindComponentNodes(Graph, Profile.Root)
    MissingPaths = [
        Target for Target in Profile.Targets
        if Target not in RootComponent
    ]
    MissingSeedNodes = sorted(SeedNodes - RootComponent)
    if os.environ.get("RCS_DEBUG_MATERIALIZE") and (MissingPaths or False):
        import os as _os_debug
        if _os_debug.environ.get("RCS_DEBUG_MATERIALIZE") == Signal:
            Starts = tuple(dict.fromkeys((*Profile.SourceAccessPath, *SourcePortal.Path)))
            Component = _FindComponentNodes(Graph, Profile.Root)
            MinX = min(Position[0] for Position in RoutedTree) if RoutedTree else None
            MaxX = max(Position[0] for Position in RoutedTree) if RoutedTree else None
            print(
                "[debug] authoritative: materialization connectivity failure "
                f"signal={Signal} root={Profile.Root} missing={tuple(MissingPaths)} "
                f"nodes={len(Nodes)} tree={len(RoutedTree)}",
                flush=True,
            )
            print(
                "[debug] authoritative: materialization bounds "
                f"x=({MinX},{MaxX}) y=({min(Position[1] for Position in RoutedTree)},"
                f"{max(Position[1] for Position in RoutedTree)}) "
                f"rootInTree={Profile.Root in Nodes} "
                f"rootComponent={len(Component)} startCount={len(Starts)}",
                flush=True,
            )
            print(
                f"[debug] authoritative: materialization starts={Starts}",
                flush=True,
            )
            print(
                "[debug] authoritative: materialization targetPaths=" +
                str({
                    Target: Profile.TargetAccessPaths[Target]
                    for Target in Profile.Targets
                }),
                flush=True,
            )
            print(
                "[debug] authoritative: materialization sourcePath=" +
                str(Profile.SourceAccessPath),
                flush=True,
            )
            print(
                "[debug] authoritative: routedTreePrefix="
                f"{tuple(sorted(RoutedTree))[:32]}",
                flush=True,
            )
    if MissingPaths or MissingSeedNodes:
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
    # The native tree search has already proved these refresh sites while
    # carrying signal strength in its state. Preserve them; the Python tree
    # walk below is only a deterministic path/coverage supplement and can
    # choose a different branch through a cyclic tree.
    def IsFlatStraightRepeaterSite(
        Position: Position3,
        Facing: str,
    ) -> bool:
        FlatNeighbors = tuple(
            Neighbor
            for Neighbor in Graph.get(Position, ())
            if Neighbor[1] == Position[1]
        )
        OutputDelta = {
            "west": (1, 0),
            "east": (-1, 0),
            "north": (0, 1),
            "south": (0, -1),
        }.get(Facing)
        if OutputDelta is None:
            return False
        Output = (
            Position[0] + OutputDelta[0],
            Position[1],
            Position[2] + OutputDelta[1],
        )
        Input = (
            Position[0] - OutputDelta[0],
            Position[1],
            Position[2] - OutputDelta[1],
        )
        return Output in FlatNeighbors and Input in FlatNeighbors

    NativeReservations = {
        Position: RoutingReservation(
            Signal=Signal,
            Resource=RoutingResourceId(RoutingResourceKind.Wire, Position),
            Position=Position,
            Purpose="Repeater",
            Facing=Facing,
        )
        for Position, Facing in NativeRepeaterReservations
        if IsFlatStraightRepeaterSite(Position, Facing)
    }
    EffectiveRepeaterReservations = {
        **{Reservation.Position: Reservation for Reservation in RepeaterReservations},
        **NativeReservations,
    }
    PoweredNodes = PropagateRoutePower(
        Profile.Root,
        Graph,
        {
            Position: Reservation.Facing
            for Position, Reservation in EffectiveRepeaterReservations.items()
            if Reservation.Facing is not None
        },
    )
    if any(PoweredNodes.get(Target, 0) <= 0 for Target in Profile.Targets):
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
    SourcePortalId = SourcePortal.PortalId
    TargetPortalIds = tuple(
        Portal.PortalId for Portal in TargetPortals
    )
    TargetPortalSignature = "-".join(
        str(PortalId) for PortalId in TargetPortalIds
    )
    CandidateId = (
        f"{Signal}:L{Layer}:{Axis}:{Lane}:V{Variant}:"
        f"S{SourcePortalId}:T{TargetPortalSignature}"
    )
    IncrementalLength = len(Nodes - SeedNodes)
    IncrementalMaterialCost = (
        IncrementalLength * max(1, LengthPenalty)
        + BendCount * max(0, BendPenalty)
        + ViaCount * max(0, ViaPenalty)
        + Layer * max(0, LayerPenalty)
        + max(0, GuideDeviationPenalty)
        + len(EffectiveRepeaterReservations) * max(0, RepeaterPenalty)
    )
    TargetPaths = {
        Target: tuple(Path)
        for Target, Path in Paths.items()
    }
    BranchClaims = {
        Target: Resources.ResourceGraph.BuildRouteClaims(Path)
        for Target, Path in TargetPaths.items()
    }
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
            sorted(EffectiveRepeaterReservations)
        ),
        RepeaterReservations=tuple(
            EffectiveRepeaterReservations[Position]
            for Position in sorted(EffectiveRepeaterReservations)
        ),
        MaterialCost=IncrementalMaterialCost,
        FootprintGrowth=len(Guide),
        Length=Length,
        BendCount=BendCount,
        ViaCount=ViaCount,
        IncrementalMaterialCost=IncrementalMaterialCost,
        IncrementalLength=IncrementalLength,
        SeedNodeCount=len(SeedNodes),
        TargetPaths=TargetPaths,
        BranchClaims=BranchClaims,
        Envelope=BuildRoutingEnvelope(
            Nodes,
            Claims.SupportCells,
            EffectiveRepeaterReservations,
        ),
    )


def PlanNegotiatedRouteTrees(
    Context: Any,
    Profiles: dict[str, Any],
    RouteRequestsBySignal: dict[str, list[tuple[Any, ...]]],
    RouteMetadataBySignal: dict[str, list[tuple[Any, ...]]],
    Region: Any,
    ReservedAccess: frozenset[Position3],
    Resources: RoutingResources,
    Technology: RedstoneRoutingTechnology,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
    AdaptiveExpiresAt: float,
    CheckRuntimeBudget: Callable[[str, dict[str, object] | None], None],
    RegenerateSignals: frozenset[str] = frozenset(),
    SeedCandidatesBySignal: dict[str, tuple[Any, ...]] | None = None,
) -> NegotiatedRoutePlan:
    """Route one tree per net and negotiate exact Redstone claim conflicts."""
    Negotiated = Policy.NegotiatedRouting
    TileSize = 4 * Technology.TrackPitch
    SignalOrder = tuple(sorted(
        Profiles,
        key=lambda Signal: (
            -Profiles[Signal].Fanout,
            -Profiles[Signal].Criticality,
            -Profiles[Signal].Span,
            Signal,
        ),
    ))

    ContextNodes = set(Region.Nodes)
    ContextEdges = set(Region.Edges)
    RegionStates: dict[str, NegotiatedRegionState] = {}
    for Signal in SignalOrder:
        SignalMetadata = RouteMetadataBySignal.get(Signal, ())
        GuideColumns = (
            set(SignalMetadata[0][2])
            if SignalMetadata
            else set()
        )
        if not GuideColumns:
            SignalRequests = RouteRequestsBySignal.get(Signal, ())
            GuideColumns = (
                {tuple(Column) for Column in SignalRequests[0][2]}
                if SignalRequests
                else set()
            )
        if not GuideColumns:
            GuideColumns = set(
                BuildNegotiatedFallbackGuideColumns(
                    Profiles[Signal],
                    Region.Bounds,
                    list(RouteRequestsBySignal.get(Signal, ())),
                )
            )
        InitialTiles = BuildNegotiatedInitialTiles(
            GuideColumns,
            Region.Bounds,
            TileSize,
        )
        InitialColumns = BuildNegotiatedInitialColumns(
            GuideColumns,
            Region.Bounds,
            TileSize,
        )
        RegionStates[Signal] = NegotiatedRegionState(
            Signal=Signal,
            TileSize=TileSize,
            Bounds=Region.Bounds,
            ActiveTiles=set(InitialTiles),
            ActiveColumns=set(InitialColumns),
            AddedNodes=set(),
            AddedEdges=set(),
            BoundaryTouches=set(),
            ExpandedSides=[],
            ExpansionEvents=[],
        )

    InitialColumns = frozenset({
        Column
        for State in RegionStates.values()
        for Column in State.ActiveColumns
    })
    InitialRegion = Resources.ResourceGraph.BuildRegion(
        Region.Bounds,
        AllowedColumns=InitialColumns,
        AllowedAccess=ReservedAccess,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "ResourceGraphExpansion",
            {"Cause": "initial-one-tile-halo", **Diagnostics},
        ),
    )
    InitialDeltaNodes = set(InitialRegion.Nodes) - ContextNodes
    InitialDeltaEdges = set(InitialRegion.Edges) - ContextEdges
    if InitialDeltaNodes or InitialDeltaEdges:
        Context.AddRegion(
            sorted(InitialDeltaNodes),
            sorted(InitialDeltaEdges),
        )
        ContextNodes.update(InitialDeltaNodes)
        ContextEdges.update(InitialDeltaEdges)
    Region = InitialRegion

    NodesByColumn: dict[Position2, list[Position3]] = defaultdict(list)
    for Position in Region.Nodes:
        NodesByColumn[(Position[0], Position[2])].append(Position)
    for Values in NodesByColumn.values():
        Values.sort()
    for State in RegionStates.values():
        OwnedColumns = State.ActiveColumns
        State.AddedNodes.update(
            Position
            for Position in Region.Nodes
            if (Position[0], Position[2]) in OwnedColumns
        )
        State.AddedEdges.update(
            Edge
            for Edge in Region.Edges
            if Edge[0] in State.AddedNodes and Edge[1] in State.AddedNodes
        )
        State.ExpansionEvents.append({
            "Cause": "initial-one-tile-halo",
            "HaloSize": TileSize,
            "ActiveTileCount": len(State.ActiveTiles),
            "OwnedNodeCount": len(State.AddedNodes),
            "OwnedEdgeCount": len(State.AddedEdges),
            "AddedNodeCount": len(InitialDeltaNodes),
            "AddedEdgeCount": len(InitialDeltaEdges),
        })

    Selected: dict[str, NetRouteCandidate] = {
        Signal: Values[0]
        for Signal, Values in (SeedCandidatesBySignal or {}).items()
        if Values
    }
    RepairStates: dict[str, NegotiatedRouteTreeState] = {}
    BranchRepairEvents: list[dict[str, object]] = []
    CumulativeConflictSignals: set[str] = set()
    History: Counter[Position3] = Counter()
    ReroutedSignals: set[str] = set()
    Iterations: list[RoutingIterationMetrics] = []
    OverflowProgression: list[int] = []
    PreviousConflictCount: int | None = None
    StagnationCount = 0
    CurrentPassIndex = 0
    MandatoryClaimsCache: dict[tuple[str, int], RoutingResourceClaims] = {}
    MandatoryClaimsByPortalSignature: dict[
        tuple[str, int, tuple[int, ...]], RoutingResourceClaims
    ] = {}
    RejectionCountsBySignal: dict[str, Counter[str]] = defaultdict(Counter)
    RepairBranchOutcomes: dict[str, dict[str, str]] = {}
    MandatorySelfConflictsBySignal: dict[
        str, set[RoutingResourceId]
    ] = defaultdict(set)
    SearchExpansionEscalations: dict[str, int] = {}
    NativeSearchDiagnosticsBySignal: dict[
        str, list[dict[str, object]]
    ] = defaultdict(list)
    RouteRequestDiagnostics: dict[str, dict[str, object]] = {}
    InitialCandidateOptions: dict[str, dict[str, NetRouteCandidate]] = (
        defaultdict(dict)
    )
    InitialAssignmentDiagnostics: dict[str, object] = {}
    # Pass zero has no present-congestion costs or retained repair branches,
    # so its detailed route-tree searches are independent once their sparse
    # regions have been frozen.  The native batch owns only those searches;
    # materialization, claim assignment, and every later repair pass stay
    # serial and deterministic below.
    InitialDetailedBatchResults: dict[tuple[str, int], Any] = {}
    InitialDetailedBatchPreflightConflicts: dict[
        tuple[str, int], frozenset[RoutingResourceId]
    ] = {}
    InitialDetailedBatchRequestIndices: dict[str, tuple[int, ...]] = {}
    InitialDetailedBatchDiagnostics: dict[str, object] = {
        "Enabled": False,
        "ScheduledRequestCount": 0,
        "RequestCount": 0,
        "BatchCount": 0,
        "CompletedWork": 0,
        "DeadlineExceeded": False,
        "WorkerCount": 1,
        "PreflightRejectedRequestCount": 0,
    }
    FixedTerminalPositions = tuple(
        Position
        for Profile in Profiles.values()
        for Position in (Profile.Root, *Profile.Targets)
    )

    def EnvelopeQuality(
        Values: Iterable[NetRouteCandidate],
    ) -> tuple[int, int, int, int, int, int, int, int, int]:
        """Score cached legal trees without invoking another path search."""
        Candidates = tuple(Values)
        Envelope = BuildRoutingEnvelope(
            (*FixedTerminalPositions, *(
                Position
                for Candidate in Candidates
                for Position in Candidate.Nodes
            )),
            (
                Position
                for Candidate in Candidates
                for Position in Candidate.Claims.SupportCells
            ),
            (
                Reservation.Position
                for Candidate in Candidates
                for Reservation in Candidate.RepeaterReservations
            ),
        )
        return (
            Envelope.Width * Envelope.Height * Envelope.Depth,
            Envelope.Height,
            Envelope.Width * Envelope.Depth,
            Envelope.Width,
            Envelope.Depth,
            Envelope.RouteBlockCount + Envelope.SupportBlockCount,
            sum(Candidate.Length for Candidate in Candidates),
            sum(Candidate.BendCount for Candidate in Candidates),
            sum(Candidate.ViaCount for Candidate in Candidates),
            sum(len(Candidate.RepeaterReservations) for Candidate in Candidates),
        )

    def CandidateEnvelopeQuality(
        Candidate: NetRouteCandidate,
    ) -> tuple[int, int, int, int, int, int]:
        Envelope = Candidate.Envelope
        if Envelope is None:
            return (0, 0, 0, 0, 0, Candidate.Length)
        return (
            Envelope.Width * Envelope.Height * Envelope.Depth,
            Envelope.Height,
            Envelope.Width * Envelope.Depth,
            Envelope.Width,
            Envelope.Depth,
            Envelope.RouteBlockCount + Envelope.SupportBlockCount,
        )

    def TryInitialCandidateAssignment(
        OptimizeEnvelope: bool = False,
    ) -> dict[str, NetRouteCandidate] | None:
        """Select one legal member of the already bounded initial tree pool.

        Pass zero materializes several portal/layer choices per signal.  A
        greedy provisional forest may conflict even though that bounded pool
        contains a capacity-one assignment.  Solve that exact small choice
        before invoking negotiated rip-up; this preserves the negotiated
        route-tree algorithm while avoiding a false placement cut.
        """
        def InitialCandidateOrder(
            Candidate: NetRouteCandidate,
        ) -> tuple[Any, ...]:
            if not OptimizeEnvelope:
                return (
                    *(
                        (Candidate.Layer,)
                        if Policy.TrackAssignment.MinimizeMaximumRoutingLayer
                        else ()
                    ),
                    Candidate.MaterialCost,
                    Candidate.CandidateId,
                )
            return (
                *(
                    (Candidate.Layer,)
                    if Policy.TrackAssignment.MinimizeMaximumRoutingLayer
                    else ()
                ),
                *CandidateEnvelopeQuality(Candidate),
                Candidate.Length,
                Candidate.BendCount,
                Candidate.ViaCount,
                len(Candidate.RepeaterReservations),
                Candidate.MaterialCost,
                Candidate.CandidateId,
            )

        CandidateSets = {
            Signal: tuple(sorted(
                Values.values(),
                key=InitialCandidateOrder,
            ))
            for Signal, Values in InitialCandidateOptions.items()
            if Values
        }
        if set(CandidateSets) != set(SignalOrder):
            InitialAssignmentDiagnostics.update({
                "Result": "incomplete-candidate-domain",
                "CandidateCounts": {
                    Signal: len(Values)
                    for Signal, Values in sorted(CandidateSets.items())
                },
            })
            return None
        ExpansionLimit = max(
            1,
            min(
                Policy.AdaptiveRouting.InitialAssignmentExpansions,
                Policy.TrackAssignment.MaximumAssignmentExpansions,
            ),
        )
        ExpansionCount = 0
        Assignment: dict[str, NetRouteCandidate] = {}

        def Search(Remaining: tuple[str, ...]) -> bool:
            nonlocal ExpansionCount
            if not Remaining:
                return True
            AvailableBySignal: list[
                tuple[str, tuple[NetRouteCandidate, ...]]
            ] = []
            for Signal in Remaining:
                Available = tuple(
                    Candidate
                    for Candidate in CandidateSets[Signal]
                    if all(
                        ClaimConflictCount(Candidate.Claims, Other.Claims) == 0
                        for Other in Assignment.values()
                    )
                )
                if not Available:
                    return False
                AvailableBySignal.append((Signal, Available))
            # MRV alone repeatedly chooses lexicographically first, equally
            # sized domains.  Break that tie by the number of incompatible
            # choices it imposes on the still-unassigned forest.  This is
            # standard constraint propagation: it spends the fixed
            # assignment budget on the portal/access bottleneck rather than
            # on independent branches first.
            Signal, Available = min(
                AvailableBySignal,
                key=lambda Value: (
                    len(Value[1]),
                    -sum(
                        ClaimConflictCount(
                            Candidate.Claims,
                            OtherCandidate.Claims,
                        )
                        for Candidate in Value[1]
                        for OtherSignal in Remaining
                        if OtherSignal != Value[0]
                        for OtherCandidate in CandidateSets[OtherSignal]
                    ),
                    Value[0],
                ),
            )
            NextRemaining = tuple(
                Value for Value in Remaining if Value != Signal
            )
            RankedAvailable = tuple(sorted(
                Available,
                key=lambda Candidate: (
                    sum(
                        ClaimConflictCount(
                            Candidate.Claims,
                            OtherCandidate.Claims,
                        )
                        for OtherSignal in NextRemaining
                        for OtherCandidate in CandidateSets[OtherSignal]
                    ),
                    *(
                        EnvelopeQuality((*Assignment.values(), Candidate))
                        if OptimizeEnvelope
                        else InitialCandidateOrder(Candidate)
                    ),
                    Candidate.CandidateId,
                ),
            ))
            for Candidate in RankedAvailable:
                ExpansionCount += 1
                if ExpansionCount > ExpansionLimit:
                    return False
                Assignment[Signal] = Candidate
                if Search(NextRemaining):
                    return True
                del Assignment[Signal]
            return False

        if not Search(SignalOrder):
            InitialAssignmentDiagnostics.update({
                "Result": "no-assignment",
                "ExpansionCount": ExpansionCount,
                "ExpansionLimit": ExpansionLimit,
                "CandidateCounts": {
                    Signal: len(Values)
                    for Signal, Values in sorted(CandidateSets.items())
                },
            })
            return None
        InitialAssignmentDiagnostics.update({
            "Result": "assigned",
            "ExpansionCount": ExpansionCount,
            "ExpansionLimit": ExpansionLimit,
            "CandidateCounts": {
                Signal: len(Values)
                for Signal, Values in sorted(CandidateSets.items())
            },
            "SelectedEnvelope": BuildRoutingEnvelope(
                (
                    Position
                    for Candidate in Assignment.values()
                    for Position in Candidate.Nodes
                ),
                (
                    Position
                    for Candidate in Assignment.values()
                    for Position in Candidate.Claims.SupportCells
                ),
                (
                    Reservation.Position
                    for Candidate in Assignment.values()
                    for Reservation in Candidate.RepeaterReservations
                ),
            ).ToDictionary(),
        })
        return dict(Assignment)

    def ExpandSignalRegion(
        Signal: str,
        Side: str,
        Cause: str,
        Touches: tuple[Position3, ...] = (),
    ) -> bool:
        State = RegionStates[Signal]
        DeltaBySide = {
            "MinimumX": (-1, 0),
            "MaximumX": (1, 0),
            "MinimumZ": (0, -1),
            "MaximumZ": (0, 1),
        }
        DeltaX, DeltaZ = DeltaBySide[Side]
        BoundaryTiles = [
            Tile
            for Tile in sorted(State.ActiveTiles)
            if (
                (Tile[0] + DeltaX, Tile[1] + DeltaZ)
                not in State.ActiveTiles
                and _NegotiatedTileIntersectsBounds(
                    (Tile[0] + DeltaX, Tile[1] + DeltaZ),
                    State.Bounds,
                    State.TileSize,
                )
            )
        ]
        if not BoundaryTiles:
            return False
        AnchorTile = (
            _NegotiatedTileForColumn(
                (Touches[0][0], Touches[0][2]),
                State.Bounds,
                State.TileSize,
            )
            if Touches
            else BoundaryTiles[0]
        )
        SelectedBoundaryTile = min(
            BoundaryTiles,
            key=lambda Tile: (
                abs(Tile[0] - AnchorTile[0])
                + abs(Tile[1] - AnchorTile[1]),
                Tile,
            ),
        )
        ExpandedTiles = frozenset({
            *State.ActiveTiles,
            (
                SelectedBoundaryTile[0] + DeltaX,
                SelectedBoundaryTile[1] + DeltaZ,
            ),
        })
        if ExpandedTiles == frozenset(State.ActiveTiles):
            return False
        State.ActiveTiles = set(ExpandedTiles)
        AddedTile = (
            SelectedBoundaryTile[0] + DeltaX,
            SelectedBoundaryTile[1] + DeltaZ,
        )
        State.ActiveColumns.update(NegotiatedColumnsForTiles(
            frozenset({AddedTile}),
            State.Bounds,
            State.TileSize,
        ))
        ExpandedRegion = Resources.ResourceGraph.BuildRegion(
            Region.Bounds,
            AllowedColumns=frozenset(State.ActiveColumns),
            AllowedAccess=ReservedAccess,
            WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
                "ResourceGraphExpansion",
                {
                    "Signal": Signal,
                    "Side": Side,
                    "Cause": Cause,
                    **Diagnostics,
                },
            ),
        )
        DeltaNodes = set(ExpandedRegion.Nodes) - ContextNodes
        DeltaEdges = set(ExpandedRegion.Edges) - ContextEdges
        if DeltaNodes or DeltaEdges:
            Context.AddRegion(sorted(DeltaNodes), sorted(DeltaEdges))
            ContextNodes.update(DeltaNodes)
            ContextEdges.update(DeltaEdges)
            for Position in sorted(DeltaNodes):
                Column = (Position[0], Position[2])
                NodesByColumn[Column].append(Position)
            for Values in NodesByColumn.values():
                Values.sort()
        OwnedColumns = State.ActiveColumns
        State.AddedNodes.update(
            Position
            for Position in ExpandedRegion.Nodes
            if (Position[0], Position[2]) in OwnedColumns
        )
        State.AddedEdges.update(
            Edge
            for Edge in ExpandedRegion.Edges
            if Edge[0] in State.AddedNodes and Edge[1] in State.AddedNodes
        )
        State.BoundaryTouches.update(Touches)
        State.ExpandedSides.append(Side)
        State.ExpansionEvents.append({
            "Cause": Cause,
            "Side": Side,
            "BoundaryTouches": [list(Value) for Value in Touches],
            "ActiveTileCount": len(State.ActiveTiles),
            "AddedNodeCount": len(DeltaNodes),
            "AddedEdgeCount": len(DeltaEdges),
            "TotalNodeCount": len(ContextNodes),
            "TotalEdgeCount": len(ContextEdges),
        })
        return True

    def PreferredExpansionSides(
        Signal: str,
        Candidate: NetRouteCandidate | None = None,
        Hotspots: tuple[Position3, ...] = (),
    ) -> tuple[str, ...]:
        State = RegionStates[Signal]
        Touches = FindNegotiatedBoundaryTouches(
            (
                Candidate.Nodes
                if Candidate is not None
                else State.BoundaryTouches
            ),
            State.ActiveTiles,
            State.Bounds,
            State.TileSize,
        )
        if Touches:
            return tuple(sorted(
                Touches,
                key=lambda Side: (-len(Touches[Side]), Side),
            ))
        Profile = Profiles[Signal]
        Root = Profile.SourceAccessPath[-1]
        Points = Hotspots or tuple(Profile.Targets)
        if not Points:
            return ("MaximumX", "MaximumZ", "MinimumX", "MinimumZ")
        Point = max(
            Points,
            key=lambda Value: (
                abs(Value[0] - Root[0]) + abs(Value[2] - Root[2]),
                Value,
            ),
        )
        DeltaX = Point[0] - Root[0]
        DeltaZ = Point[2] - Root[2]
        Primary = (
            ("MaximumX" if DeltaX >= 0 else "MinimumX")
            if abs(DeltaX) >= abs(DeltaZ)
            else ("MaximumZ" if DeltaZ >= 0 else "MinimumZ")
        )
        Ordered = (Primary, "MaximumX", "MaximumZ", "MinimumX", "MinimumZ")
        return tuple(dict.fromkeys(Ordered))

    def CandidateNodeCosts(Signal: str) -> list[tuple[Position3, int]]:
        # Pass zero is deliberately permissive: establish a complete
        # provisional forest before asking any net to avoid another.  Applying
        # present congestion while this forest is still being constructed
        # recreates one-shot routing and can starve the nets ordered last.
        if CurrentPassIndex == 0:
            return []
        Costs: Counter[Position3] = Counter(History)
        for OtherSignal, Candidate in Selected.items():
            if OtherSignal == Signal:
                continue
            Present = Negotiated.PresentConflictPenalty * (CurrentPassIndex + 1)
            for Position in (
                Candidate.Claims.ElectricalCells
                | Candidate.Claims.SupportCells
                | Candidate.Claims.RequiredAirCells
            ):
                Costs[Position] += Present
            for X, Y, Z in (
                Candidate.Claims.WireCells
                | Candidate.Claims.RequiredAirCells
            ):
                Costs[(X, Y + 1, Z)] += Present
        Required = {
            Position
            for Path in (
                Profiles[Signal].SourceAccessPath,
                *Profiles[Signal].TargetAccessPaths.values(),
            )
            for Position in Path
        }
        return sorted(
            (Position, Cost)
            for Position, Cost in Costs.items()
            if Cost > 0 and Position not in Required
        )

    def ClaimConflictCount(
        First: RoutingResourceClaims,
        Second: RoutingResourceClaims,
    ) -> int:
        Electrical = (First.WireCells & Second.ElectricalCells) | (
            Second.WireCells & First.ElectricalCells
        )
        Support = (
            First.SupportCells & (Second.WireCells | Second.RequiredAirCells)
        ) | (
            Second.SupportCells & (First.WireCells | First.RequiredAirCells)
        )
        Air = (First.RequiredAirCells & Second.WireCells) | (
            Second.RequiredAirCells & First.WireCells
        )
        return len(Electrical | Support | Air)

    def RequestMandatoryClaims(
        Signal: str,
        RequestIndex: int,
    ) -> RoutingResourceClaims:
        SourcePortal, TargetPortals, _Guide, _Layer, _Axis, _Lane, _Variant = (
            RouteMetadataBySignal[Signal][RequestIndex]
        )
        MandatoryNodes = {
            *Profiles[Signal].SourceAccessPath,
            *SourcePortal.Path,
            *(
                Position
                for Target in Profiles[Signal].Targets
                for Position in Profiles[Signal].TargetAccessPaths[Target]
            ),
            *(
                Position
                for Portal in TargetPortals
                for Position in Portal.Path
            ),
        }
        CacheKey = (Signal, RequestIndex)
        MandatoryClaims = MandatoryClaimsCache.get(CacheKey)
        if MandatoryClaims is None:
            PortalSignature = (
                Signal,
                SourcePortal.PortalId,
                tuple(Portal.PortalId for Portal in TargetPortals),
            )
            MandatoryClaims = MandatoryClaimsByPortalSignature.get(
                PortalSignature
            )
            if MandatoryClaims is None:
                MandatoryClaims = Resources.ResourceGraph.BuildRouteClaims(
                    MandatoryNodes
                )
                MandatoryClaimsByPortalSignature[PortalSignature] = (
                    MandatoryClaims
                )
            MandatoryClaimsCache[CacheKey] = MandatoryClaims
        return MandatoryClaims

    def RequestMandatoryConflictCount(
        Signal: str,
        RequestIndex: int,
        Candidates: dict[str, NetRouteCandidate] | None = None,
    ) -> int:
        MandatoryClaims = RequestMandatoryClaims(Signal, RequestIndex)
        return sum(
            ClaimConflictCount(MandatoryClaims, Other.Claims)
            for OtherSignal, Other in (
                Selected if Candidates is None else Candidates
            ).items()
            if OtherSignal != Signal
        )

    def RouteRequest(
        Signal: str,
        RequestIndex: int,
        NodeCosts: list[tuple[Position3, int]],
        MinimumExpansionCount: int | None = None,
    ) -> NetRouteCandidate | None:
        Requests = RouteRequestsBySignal.get(Signal, ())
        MetadataValues = RouteMetadataBySignal.get(Signal, ())
        if not Requests or not MetadataValues:
            return None
        RequestIndex %= min(len(Requests), len(MetadataValues))
        (
            Starts,
            TargetBranches,
            _AllowedColumns,
            RequiredNodes,
            BlockedNodeValues,
            PreferredColumns,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            MaximumExpansionCount,
        ) = Requests[RequestIndex]
        EffectiveMaximumExpansionCount = min(
            Policy.DetailedRouting.StrictBaseExpansions,
            max(MaximumExpansionCount, MinimumExpansionCount or 0),
        )
        RepairState = RepairStates.get(Signal)
        if RepairState is not None and RepairState.PrunedTargets:
            RootedStarts = list(Starts)
            RetainedNodes = set(RepairState.RetainedNodes)
            RetainedNodes.update(Profiles[Signal].SourceAccessPath)
            # Keep the actual source-access root first. Sorting the retained
            # tree made an arbitrary low-coordinate branch look like a fresh
            # producer and silently reset its redstone strength.
            Starts = list(dict.fromkeys((
                *RootedStarts,
                *sorted(RetainedNodes),
            )))
            TargetBranches = [
                Branch
                for Target, Branch in zip(
                    RepairState.PrunedBranchIds,
                    RepairState.PrunedBranchPaths,
                )
            ]
        MandatorySelfConflicts = InitialDetailedBatchPreflightConflicts.get(
            (Signal, RequestIndex),
            frozenset(),
        )
        if not MandatorySelfConflicts:
            MandatorySelfConflicts = FindSelfClaimConflicts({
                Signal: RequestMandatoryClaims(Signal, RequestIndex)
            })
        if MandatorySelfConflicts:
            MandatorySelfConflictsBySignal[Signal].update(
                MandatorySelfConflicts
            )
            RejectionCountsBySignal[Signal][
                "MandatorySelfClaimConflict"
            ] += 1
            return None
        ActiveColumns = RegionStates[Signal].ActiveColumns
        AllowedNodes = {
            Position
            for Column in ActiveColumns
            for Position in NodesByColumn.get(tuple(Column), ())
        }
        AllowedNodes.update(tuple(Position) for Position in RequiredNodes)
        CheckRuntimeBudget(
            "NegotiatedDetailedRouting",
            {
                "Signal": Signal,
                "RequestIndex": RequestIndex,
                "NegotiatedIteration": CurrentPassIndex,
                "SelectedSignalCount": len(Selected),
                "OverflowProgression": list(OverflowProgression),
            },
        )
        if not hasattr(Context, "GenerateRouteTreeDetailedBounded"):
            raise ValueError(
                "negotiated routing requires the diagnostic Rust routing API"
            )
        SearchBlockedNodes = set(BlockedNodeValues)
        RequiredNodeSet = set(RequiredNodes)
        SelfClaimCutCount = 0
        # A pass-zero result was searched against the same frozen sparse
        # region, empty present-cost map, and no retained repair tree.  Use it
        # once only: if self-claim repair changes the blocked set, or a region
        # expands, the serial call below owns the changed search state.
        BatchedSearchResult = (
            InitialDetailedBatchResults.pop((Signal, RequestIndex), None)
            if CurrentPassIndex == 0
            else None
        )
        while True:
            if BatchedSearchResult is not None:
                SearchResult = BatchedSearchResult
                BatchedSearchResult = None
            else:
                SearchResult = Context.GenerateRouteTreeDetailedBounded(
                    Starts,
                    TargetBranches,
                    sorted(AllowedNodes),
                    sorted(SearchBlockedNodes),
                    PreferredColumns,
                    NodeCosts,
                    PreferredRoutingY,
                    GuidePenalty,
                    BendPenalty,
                    ViaPenalty,
                    True,
                    EffectiveMaximumExpansionCount,
                    min(
                        Negotiated.MaximumRouteTreeRequestMilliseconds,
                        RemainingRoutingRuntimeMilliseconds(
                            Deadline, AdaptiveExpiresAt
                        ),
                    ),
                )
            if SearchResult.Status != "Routed":
                break
            RoutedClaims = Resources.ResourceGraph.BuildRouteClaims(
                SearchResult.Nodes
            )
            SelfClaimConflicts = FindSelfClaimConflicts({
                Signal: RoutedClaims
            })
            if not SelfClaimConflicts:
                break
            if SelfClaimCutCount >= 3:
                break
            ConflictPositions = {
                Resource.Position for Resource in SelfClaimConflicts
            }
            CutNodes = {
                Node
                for Node in SearchResult.Nodes
                if Node not in RequiredNodeSet
                and any(
                    abs(Node[0] - Position[0])
                    + abs(Node[1] - Position[1])
                    + abs(Node[2] - Position[2])
                    <= 1
                    for Position in ConflictPositions
                )
            }
            CutNodes -= SearchBlockedNodes
            if not CutNodes:
                break
            SearchBlockedNodes.update(CutNodes)
            SelfClaimCutCount += 1
        FrontierNodes = tuple(SearchResult.BoundaryFrontierNodes)
        FrontierTouches = FindNegotiatedBoundaryTouches(
            FrontierNodes,
            RegionStates[Signal].ActiveTiles,
            RegionStates[Signal].Bounds,
            RegionStates[Signal].TileSize,
        )
        if SearchResult.Status == "Routed":
            RegionStates[Signal].BoundaryTouches.update(FrontierNodes)
        RouteRequestDiagnostics[Signal] = {
            "Status": SearchResult.Status,
            "NoPathReason": SearchResult.NoPathReason,
            "ExpansionCount": SearchResult.ExpansionCount,
            "MaximumExpansionCount": EffectiveMaximumExpansionCount,
            "BoundaryFrontierNodes": [
                list(Value) for Value in FrontierNodes
            ],
            "BoundaryFrontierTouches": {
                Side: [list(Value) for Value in Values]
                for Side, Values in FrontierTouches.items()
            },
        }
        NativeSearchDiagnosticsBySignal[Signal].append({
            "RequestIndex": RequestIndex,
            "Iteration": CurrentPassIndex,
            "Status": SearchResult.Status,
            "NoPathReason": SearchResult.NoPathReason
            if SearchResult.Status == "NoPath"
            else "",
            "ExpansionCount": SearchResult.ExpansionCount,
            "MaximumExpansionCount": EffectiveMaximumExpansionCount,
            "BoundaryFrontierNodes": [
                list(Value) for Value in FrontierNodes
            ],
            "RepeaterReservationCount": len(
                SearchResult.RepeaterReservations
            ),
            "RepeaterRejectedCount": (
                SearchResult.RepeaterRejectedCount
            ),
            "SelfClaimCutCount": SelfClaimCutCount,
            "RemainingSelfClaimConflicts": [
                str(Resource)
                for Resource in sorted(
                    (
                        SelfClaimConflicts
                        if SearchResult.Status == "Routed"
                        else {}
                    ),
                    key=str,
                )
            ],
        })
        if SearchResult.RepeaterRejectedCount:
            RejectionCountsBySignal[Signal]["NoRepeater"] += (
                SearchResult.RepeaterRejectedCount
            )
        if SearchResult.Status == "BudgetExpired":
            RejectionCountsBySignal[Signal]["NativeBudgetExpired"] += 1
        elif SearchResult.Status == "NoPath":
            RejectionCountsBySignal[Signal]["NoPath"] += 1
            if SearchResult.NoPathReason == "NoRepeater":
                RejectionCountsBySignal[Signal]["NoRepeater"] += 1
        if RepairState is not None and RepairState.PrunedTargets:
            for Target in RepairState.PrunedTargets:
                RepairBranchOutcomes.setdefault(Signal, {})[
                    str(Target)
                ] = "Rejected"
        RoutedTree = (
            list(SearchResult.Nodes)
            if SearchResult.Status == "Routed"
            else None
        )
        if RoutedTree is not None and RepairState is not None:
            # A repair search starts from the retained branch frontier.  Its
            # result contains only the new branch segment, so preserve the
            # clean source trunk and clean target branches when committing.
            RoutedTree = sorted({
                *RoutedTree,
                *RepairState.RetainedNodes,
            })
        SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant = (
            MetadataValues[RequestIndex]
        )
        Candidate = _MaterializeCandidate(
            Signal,
            Profiles[Signal],
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
            Policy.DetailedRouting.CandidateBendWeight,
            Policy.DetailedRouting.CandidateViaWeight,
            Policy.DetailedRouting.LayerPenalty,
            RepeaterPenalty=Policy.DetailedRouting.RepeaterPenalty,
            NativeRepeaterReservations=tuple(
                SearchResult.RepeaterReservations
            ),
            RejectionCounts=RejectionCountsBySignal[Signal],
        )
        if Candidate is not None and RepairState is not None and RepairState.PrunedTargets:
            TargetPaths = {
                Target
                for Target, _Path in Candidate.TargetPaths.items()
            }
            for Target in RepairState.PrunedBranchIds:
                RepairBranchOutcomes.setdefault(Signal, {})[str(Target)] = (
                    "Committed"
                    if Target in TargetPaths
                    else "Lost"
                )
        return Candidate

    def BuildPassZeroDetailedSearchRequest(
        Signal: str,
        RequestIndex: int,
    ) -> tuple[Any, ...] | None:
        """Freeze one independent initial detailed search for native batching."""
        Requests = RouteRequestsBySignal.get(Signal, ())
        MetadataValues = RouteMetadataBySignal.get(Signal, ())
        RequestCount = min(len(Requests), len(MetadataValues))
        if RequestCount == 0:
            return None
        RequestIndex %= RequestCount
        (
            Starts,
            TargetBranches,
            _AllowedColumns,
            RequiredNodes,
            BlockedNodeValues,
            PreferredColumns,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            MaximumExpansionCount,
        ) = Requests[RequestIndex]
        MandatorySelfConflicts = FindSelfClaimConflicts({
            Signal: RequestMandatoryClaims(Signal, RequestIndex)
        })
        if MandatorySelfConflicts:
            InitialDetailedBatchPreflightConflicts[(Signal, RequestIndex)] = (
                frozenset(MandatorySelfConflicts)
            )
            return None
        ActiveColumns = RegionStates[Signal].ActiveColumns
        AllowedNodes = {
            Position
            for Column in ActiveColumns
            for Position in NodesByColumn.get(tuple(Column), ())
        }
        AllowedNodes.update(tuple(Position) for Position in RequiredNodes)
        # Pass zero deliberately has no present/history congestion cost and
        # no repair tree.  Keeping that snapshot explicit is what makes each
        # native request independent and safe to schedule in parallel.
        return (
            list(Starts),
            [list(Branch) for Branch in TargetBranches],
            sorted(AllowedNodes),
            sorted(BlockedNodeValues),
            sorted(PreferredColumns),
            [],
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            True,
            min(
                MaximumExpansionCount,
                Policy.DetailedRouting.StrictBaseExpansions,
            ),
        )

    def PreparePassZeroDetailedSearchBatch(
        Signal: str,
        RequestIndices: tuple[int, ...],
    ) -> None:
        """Batch one signal's already-selected initial alternatives.

        The outer signal order changes capacity feedback and must remain
        serial.  Alternatives for the *current* signal share a frozen region,
        no present congestion, and no repair tree, so they are safe to search
        concurrently without changing which request IDs the serial selector
        considers.
        """
        if not hasattr(Context, "GenerateRouteTreeDetailedBatchBounded"):
            return
        InitialDetailedBatchRequestIndices[Signal] = RequestIndices
        Scheduled = [
            (RequestIndex, Request)
            for RequestIndex in RequestIndices
            if (Request := BuildPassZeroDetailedSearchRequest(
                Signal,
                RequestIndex,
            )) is not None
        ]
        try:
            WorkerCount = max(1, int(GetRustRoutingThreadCount()))
        except Exception:
            WorkerCount = 1
        InitialDetailedBatchDiagnostics.update({
            "Enabled": True,
            "ScheduledRequestCount": int(
                InitialDetailedBatchDiagnostics["ScheduledRequestCount"]
            ) + len(RequestIndices),
            "RequestCount": int(
                InitialDetailedBatchDiagnostics["RequestCount"]
            ) + len(Scheduled),
            "WorkerCount": WorkerCount,
            "PreflightRejectedRequestCount": len(
                InitialDetailedBatchPreflightConflicts
            ),
        })
        for StartIndex in range(0, len(Scheduled), WorkerCount):
            Chunk = Scheduled[StartIndex:StartIndex + WorkerCount]
            CheckRuntimeBudget(
                "NegotiatedDetailedRouting",
                {
                    "Iteration": 0,
                    "Signal": Signal,
                    "BatchStartIndex": StartIndex,
                    "BatchRequestCount": len(Chunk),
                },
            )
            MaximumRuntimeMilliseconds = min(
                Negotiated.MaximumRouteTreeRequestMilliseconds,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ),
            )
            if MaximumRuntimeMilliseconds <= 0:
                CheckRuntimeBudget(
                    "NegotiatedDetailedRouting",
                    {"Iteration": 0, "Signal": Signal},
                )
                return
            BatchResult = Context.GenerateRouteTreeDetailedBatchBounded(
                [Request for _Index, Request in Chunk],
                MaximumRuntimeMilliseconds,
            )
            SearchResults = list(BatchResult.SearchResults)
            if len(SearchResults) != len(Chunk):
                raise ValueError(
                    "detailed route-tree batch returned an unexpected result count"
                )
            for (RequestIndex, _Request), SearchResult in zip(
                Chunk,
                SearchResults,
            ):
                InitialDetailedBatchResults[(Signal, RequestIndex)] = (
                    SearchResult
                )
            InitialDetailedBatchDiagnostics["BatchCount"] = (
                int(InitialDetailedBatchDiagnostics["BatchCount"]) + 1
            )
            InitialDetailedBatchDiagnostics["CompletedWork"] = (
                int(InitialDetailedBatchDiagnostics["CompletedWork"])
                + int(BatchResult.CompletedWork)
            )
            InitialDetailedBatchDiagnostics["DeadlineExceeded"] = bool(
                InitialDetailedBatchDiagnostics["DeadlineExceeded"]
                or BatchResult.DeadlineExceeded
            )

    ConflictSignals: tuple[str, ...] = SignalOrder
    FinalConflicts: dict[RoutingResourceId, tuple[str, ...]] = {}
    for PassIndex in range(Negotiated.MaximumIterations):
        CurrentPassIndex = PassIndex
        CheckRuntimeBudget(
            "NegotiatedDetailedRouting",
            {"Iteration": PassIndex, "SelectedSignals": len(Selected)},
        )
        SignalsToRoute = SignalOrder if PassIndex == 0 else ConflictSignals
        for SignalIndex, Signal in enumerate(SignalsToRoute):
            Existing = Selected.pop(Signal, None)
            SignalNodeCosts = CandidateNodeCosts(Signal)
            Best: NetRouteCandidate | None = None
            BestScore: tuple[Any, ...] | None = None

            def ConsiderCandidate(Candidate: NetRouteCandidate) -> int:
                """Retain the best current net tree using the normal score."""
                nonlocal Best, BestScore
                PairConflicts = sum(
                    ClaimConflictCount(Candidate.Claims, Other.Claims)
                    for Other in Selected.values()
                )
                Score = (
                    PairConflicts,
                    *(
                        (Candidate.Layer,)
                        if Policy.TrackAssignment.MinimizeMaximumRoutingLayer
                        else ()
                    ),
                    Candidate.MaterialCost,
                    Candidate.CandidateId,
                )
                if BestScore is None or Score < BestScore:
                    Best = Candidate
                    BestScore = Score
                return PairConflicts

            RequestCount = len(RouteRequestsBySignal.get(Signal, ()))
            if (
                RequestCount == 0
                and Existing is not None
                and Signal not in RegenerateSignals
            ):
                # Retained candidate geometry is still a valid legal candidate
                # for this signal; keep it in the selection and continue instead
                # of converting a cache-aware candidate pass into an immediate
                # no-pin-access failure.
                Selected[Signal] = Existing
                continue
            # The adaptive policy already defines the bounded initial portal
            # domain.  Pass zero must materialize that complete configured
            # domain for the exact capacity assignment; truncating it to four
            # silently discards half of the legal portal/layer choices before
            # negotiation can distinguish a real placement cut from candidate
            # ordering.  Later repair passes stay local and retain the
            # existing four-request window.
            RequestWindowSize = min(
                (
                    Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal
                    if PassIndex == 0
                    else 4
                ),
                RequestCount,
            )
            RankedRequestIndices = sorted(
                range(RequestCount),
                key=lambda RequestIndex: (
                    RequestMandatoryConflictCount(Signal, RequestIndex),
                    (
                        RequestIndex
                        - PassIndex * RequestWindowSize
                        - SignalIndex
                    ) % RequestCount,
                    RequestIndex,
                ),
            )
            # The first route is feasibility work, not candidate enumeration.
            # Exhaust its already bounded portal/guide domain before declaring
            # the net impossible. Subsequent negotiated repairs remain local.
            # A repeated overflow is precisely when a different bounded
            # portal/tree choice matters most.  Collapsing to one request on
            # stagnation replays the same mandatory portal ownership and
            # converts a candidate-ordering issue into a placement failure.
            AttemptCount = min(RequestWindowSize, RequestCount)
            AttemptedRequestIndices = tuple(
                RankedRequestIndices[:AttemptCount]
            )
            if PassIndex == 0:
                PreparePassZeroDetailedSearchBatch(
                    Signal,
                    AttemptedRequestIndices,
                )
            for RequestIndex in AttemptedRequestIndices:
                Candidate = RouteRequest(
                    Signal,
                    RequestIndex,
                    SignalNodeCosts,
                )
                if Candidate is None:
                    continue
                if PassIndex == 0:
                    InitialCandidateOptions[Signal][
                        Candidate.CandidateId
                    ] = Candidate
                PairConflicts = ConsiderCandidate(Candidate)
                # Pass zero establishes the provisional forest.  It still
                # has to compare the bounded, geometrically diverse request
                # window; accepting request zero unconditionally turns portal
                # ordering into physical ownership and manufactures a fixed
                # access cut before negotiation has begun.
                if PairConflicts == 0 and PassIndex > 0:
                    break
            if Best is None:
                # The global initial-work budget deliberately gives every
                # portal alternative a modest share.  When an entire net's
                # complete initial pool reaches that cap, spend the strict
                # detailed-routing budget on *that net only* before moving
                # placement.  RCA8 previously divided 2M expansions across
                # 712 initial requests, capping A5 at 2,812 expansions even
                # though no route search had established a geometric cut.
                SearchLimitedRequestIndices = tuple(
                    RequestIndex
                    for RequestIndex in AttemptedRequestIndices
                    if any(
                        Diagnostic["RequestIndex"] == RequestIndex
                        and Diagnostic["Iteration"] == PassIndex
                        and Diagnostic["NoPathReason"] == "SearchLimitReached"
                        for Diagnostic in NativeSearchDiagnosticsBySignal[Signal]
                    )
                )
                if SearchLimitedRequestIndices:
                    SearchExpansionEscalations[Signal] = (
                        Policy.DetailedRouting.StrictBaseExpansions
                    )
                    for SearchLimitedRequestIndex in SearchLimitedRequestIndices:
                        Candidate = RouteRequest(
                            Signal,
                            SearchLimitedRequestIndex,
                            SignalNodeCosts,
                            MinimumExpansionCount=(
                                Policy.DetailedRouting.StrictBaseExpansions
                            ),
                        )
                        if Candidate is None:
                            continue
                        if PassIndex == 0:
                            InitialCandidateOptions[Signal][
                                Candidate.CandidateId
                            ] = Candidate
                        PairConflicts = ConsiderCandidate(Candidate)
                        if PairConflicts == 0 and PassIndex > 0:
                            break
                if Best is not None:
                    Selected[Signal] = Best
                    continue
                Expanded = False
                LastRequest = RouteRequestDiagnostics.get(Signal, {})
                FailedTouches = {}
                if isinstance(
                    LastRequest.get("BoundaryFrontierTouches"),
                    dict,
                ):
                    FailedTouches = {
                        Key: tuple(
                            tuple(Value) for Value in ListValue
                        )
                        for Key, ListValue in LastRequest[
                            "BoundaryFrontierTouches"
                        ].items()
                        if isinstance(ListValue, list)
                    }
                if not FailedTouches:
                    FailedTouches = FindNegotiatedBoundaryTouches(
                        RegionStates[Signal].BoundaryTouches,
                        RegionStates[Signal].ActiveTiles,
                        RegionStates[Signal].Bounds,
                        RegionStates[Signal].TileSize,
                    )
                FailureCause = "failed-search-frontier"
                if LastRequest.get("NoPathReason") == "NoPathContinuation":
                    FailureCause = "cheapest-continuation-leaves-region"
                if any(
                    Value for Value in FailedTouches.values()
                ):
                    FailureCause = "route-tree-boundary-frontier"
                for Side in PreferredExpansionSides(Signal, Existing):
                    if ExpandSignalRegion(
                        Signal,
                        Side,
                        FailureCause,
                        FailedTouches.get(Side, ()),
                    ):
                        Expanded = True
                        break
                if Expanded:
                    ExpandedRequestIndices = RankedRequestIndices[
                        AttemptCount:AttemptCount * 2
                    ]
                    if not ExpandedRequestIndices:
                        ExpandedRequestIndices = RankedRequestIndices[
                            :AttemptCount
                        ]
                    for ExpandedRequestIndex in ExpandedRequestIndices:
                        Best = RouteRequest(
                            Signal,
                            ExpandedRequestIndex,
                            SignalNodeCosts,
                        )
                        if Best is not None:
                            break
                if Best is not None:
                    Selected[Signal] = Best
                    continue
                if Existing is not None and Signal not in RegenerateSignals:
                    Selected[Signal] = Existing
                    continue
                FailureSignals = set(SignalsToRoute)
                FailureSignals.update(CumulativeConflictSignals)
                FailureSignals.add(Signal)
                Rejections = RejectionCountsBySignal[Signal]
                FailureReason = (
                    RoutingFailureReason.NoPinAccessPattern
                    if (
                        RequestCount == 0
                        or Rejections.get("MandatorySelfClaimConflict", 0) > 0
                    )
                    else (
                        RoutingFailureReason.RepeaterAccessInfeasible
                        if Rejections.get("NoRepeater", 0) > 0
                        else RoutingFailureReason.GlobalCongestionUnresolved
                    )
                )
                MandatoryConflicts = MandatorySelfConflictsBySignal[Signal]
                raise RoutingStageError(RoutingFailure(
                    Reason=FailureReason,
                    Stage="NegotiatedDetailedRouting",
                    AffectedNets=tuple(sorted(FailureSignals)),
                    Locations=tuple(sorted({
                        Resource.Position
                        for Resource in MandatoryConflicts
                    }))[:32],
                    Resources=tuple(sorted(
                        str(Resource) for Resource in MandatoryConflicts
                    ))[:32],
                    RepairActions=(
                        "RelocateProducerConsumerClusters",
                        "ExpandOffenderHalo",
                    ),
                    Detail=(
                        "mandatory source/target access geometry conflicts "
                        "with its own wire, support, or headroom claims"
                        if MandatoryConflicts
                        else (
                            "no legal portal-aware route tree was found in "
                            "the bounded negotiated sparse region"
                        )
                    ),
                    Diagnostics={
                        "RequestCount": RequestCount,
                        "AttemptedRequestCount": AttemptCount,
                        "Iteration": PassIndex,
                        "Rejections": dict(sorted(Rejections.items())),
                        "InitialDetailedBatch": dict(
                            InitialDetailedBatchDiagnostics
                        ),
                        "SearchExpansionEscalations": dict(
                            sorted(SearchExpansionEscalations.items())
                        ),
                        "CachedNodeCount": Resources.ResourceGraph.CachedNodeCount,
                        "Region": {
                            "HaloSize": TileSize,
                            "ActiveTiles": [
                                list(Value)
                                for Value in sorted(
                                    RegionStates[Signal].ActiveTiles
                                )
                            ],
                            "BoundaryTouches": [
                                list(Value)
                                for Value in sorted(
                                    RegionStates[Signal].BoundaryTouches
                                )
                            ],
                            "ExpandedSides": list(
                                RegionStates[Signal].ExpandedSides
                            ),
                            "ExpansionEvents": list(
                                RegionStates[Signal].ExpansionEvents
                            ),
                            "NativeSearch": list(
                                NativeSearchDiagnosticsBySignal[Signal]
                            ),
                        },
                        "ConflictGraph": {
                            "Classification": (
                                "mandatory-access-self-conflict"
                                if MandatoryConflicts
                                else "sparse-region-route-cut"
                            ),
                            "ConflictSignals": sorted(FailureSignals),
                            "RelocationSignals": sorted(FailureSignals),
                            "RequestSignals": {
                                "Signal": Signal,
                                "RequestCount": RequestCount,
                                "AttemptedRequestCount": AttemptCount,
                                "FailedSignalCount": len(FailureSignals),
                                "RequestlessSignals": sorted(
                                    set(SignalsToRoute) | set(CumulativeConflictSignals)
                                ),
                            },
                        },
                    },
                ))
            Selected[Signal] = Best
            BoundaryTouches = FindNegotiatedBoundaryTouches(
                Best.Nodes,
                RegionStates[Signal].ActiveTiles,
                RegionStates[Signal].Bounds,
                RegionStates[Signal].TileSize,
            )
            for Values in BoundaryTouches.values():
                RegionStates[Signal].BoundaryTouches.update(Values)
            if Existing is not None and Existing.CandidateId != Best.CandidateId:
                ReroutedSignals.add(Signal)

        FinalConflicts = FindClaimConflicts({
            Signal: Candidate.Claims
            for Signal, Candidate in Selected.items()
        })
        if PassIndex == 0 and FinalConflicts:
            InitialAssignment = TryInitialCandidateAssignment()
            if InitialAssignment is not None:
                EnvelopeAssignment = TryInitialCandidateAssignment(
                    OptimizeEnvelope=True,
                )
                if EnvelopeAssignment is not None:
                    BaselineQuality = EnvelopeQuality(
                        InitialAssignment.values()
                    )
                    EnvelopeCandidateQuality = EnvelopeQuality(
                        EnvelopeAssignment.values()
                    )
                    InitialAssignmentDiagnostics["EnvelopeSelection"] = {
                        "Baseline": list(BaselineQuality),
                        "Candidate": list(EnvelopeCandidateQuality),
                        "Selected": (
                            "envelope"
                            if EnvelopeCandidateQuality < BaselineQuality
                            else "baseline"
                        ),
                    }
                    if EnvelopeCandidateQuality < BaselineQuality:
                        InitialAssignment = EnvelopeAssignment
                Selected = InitialAssignment
                FinalConflicts = FindClaimConflicts({
                    Signal: Candidate.Claims
                    for Signal, Candidate in Selected.items()
                })
        ConflictSignals = tuple(sorted({
            Signal
            for Signals in FinalConflicts.values()
            for Signal in Signals
        }))
        CumulativeConflictSignals.update(ConflictSignals)
        ConflictCount = len(FinalConflicts)
        OverflowProgression.append(ConflictCount)
        Iterations.append(RoutingIterationMetrics(
            Iteration=PassIndex + 1,
            Stage="Negotiated detailed routing",
            ConflictCount=ConflictCount,
            ReroutedNets=len(SignalsToRoute) if PassIndex else 0,
            AverageLength=(
                sum(Value.Length for Value in Selected.values())
                / max(1, len(Selected))
            ),
            BendCount=sum(Value.BendCount for Value in Selected.values()),
            ViaCount=sum(Value.ViaCount for Value in Selected.values()),
            ConflictSignals=ConflictSignals,
        ))
        if not FinalConflicts and len(Selected) == len(Profiles):
            if Policy.TrackAssignment.MinimizeMaximumRoutingLayer:
                LayerOptimizedAssignment = TryInitialCandidateAssignment()
                if LayerOptimizedAssignment is not None:
                    LayerOptimizedConflicts = FindClaimConflicts({
                        Signal: Candidate.Claims
                        for Signal, Candidate in LayerOptimizedAssignment.items()
                    })
                    if not LayerOptimizedConflicts:
                        Selected = LayerOptimizedAssignment
                        InitialAssignmentDiagnostics["FinalLayerOptimization"] = {
                            "Applied": True,
                            "MaximumLayer": max(
                                Candidate.Layer
                                for Candidate in Selected.values()
                            ),
                        }
                    else:
                        InitialAssignmentDiagnostics["FinalLayerOptimization"] = {
                            "Applied": False,
                            "Reason": "claim-conflict",
                        }
            return NegotiatedRoutePlan(
                SelectedCandidates=Selected,
                Iterations=tuple(Iterations),
                ReroutedSignals=tuple(sorted(ReroutedSignals)),
                OverflowProgression=tuple(OverflowProgression),
                CachedNodeCount=Resources.ResourceGraph.CachedNodeCount,
                CachedEdgeCount=Resources.ResourceGraph.CachedEdgeCount,
                Diagnostics={
                    "HaloSize": TileSize,
                    "Regions": {
                        Signal: {
                            "ActiveTiles": [
                                list(Value)
                                for Value in sorted(State.ActiveTiles)
                            ],
                            "BoundaryTouches": [
                                list(Value)
                                for Value in sorted(State.BoundaryTouches)
                            ],
                            "ExpandedSides": list(State.ExpandedSides),
                            "ExpansionEvents": list(State.ExpansionEvents),
                            "OwnedNodeCount": len(State.AddedNodes),
                            "OwnedEdgeCount": len(State.AddedEdges),
                        }
                        for Signal, State in sorted(RegionStates.items())
                    },
                    "BranchRepairs": BranchRepairEvents,
                    "InitialAssignment": dict(InitialAssignmentDiagnostics),
                    "InitialCandidateLayers": {
                        Signal: sorted({Candidate.Layer for Candidate in Values.values()})
                        for Signal, Values in sorted(InitialCandidateOptions.items())
                    },
                    "InitialDetailedBatch": dict(
                        InitialDetailedBatchDiagnostics
                    ),
                    "SearchExpansionEscalations": dict(
                        sorted(SearchExpansionEscalations.items())
                    ),
                    "CumulativeConflictSignals": sorted(
                        CumulativeConflictSignals
                    ),
                    "RepeaterRejections": {
                        Signal: dict(sorted(Values.items()))
                        for Signal, Values in sorted(
                            RejectionCountsBySignal.items()
                        )
                    },
                    "NativeSearch": {
                        Signal: list(Values)
                        for Signal, Values in sorted(
                            NativeSearchDiagnosticsBySignal.items()
                        )
                    },
                },
            )
        MandatoryClaimsBySelectedSignal: dict[
            str, RoutingResourceClaims
        ] = {}
        for Signal, Candidate in Selected.items():
            for RequestIndex, Metadata in enumerate(
                RouteMetadataBySignal.get(Signal, ())
            ):
                SourcePortal, TargetPortals, *_Rest = Metadata
                if SourcePortal.PortalId != Candidate.SourcePortalId:
                    continue
                if tuple(sorted(
                    Portal.PortalId for Portal in TargetPortals
                )) != tuple(sorted(Candidate.TargetPortalIds.values())):
                    continue
                MandatoryClaimsBySelectedSignal[Signal] = (
                    RequestMandatoryClaims(Signal, RequestIndex)
                )
                break
        MandatoryCutResources = {
            Resource
            for Resource, Signals in FinalConflicts.items()
            if any(
                Resource
                in MandatoryClaimsBySelectedSignal.get(
                    Signal,
                    RoutingResourceClaims(),
                ).ResourceIds
                for Signal in Signals
            )
        }
        ImmediateMandatoryPlacementCut = (
            PassIndex == 0
            # Several simultaneous fixed portal/access collisions cannot be
            # repaired by negotiated wire rerouting.  Scale this trigger with
            # the routed demand rather than requiring one conflict per net;
            # otherwise medium arithmetic blocks spend most of their deadline
            # rediscovering a placement cut before relocation can begin.
            and len(MandatoryCutResources)
            >= max(4, (len(SignalOrder) + 3) // 4)
        )
        if (
            FinalConflicts
            and MandatoryCutResources
            and (
                ImmediateMandatoryPlacementCut
                or (
                    PassIndex >= 2
                    and PreviousConflictCount is not None
                    and ConflictCount >= PreviousConflictCount
                )
            )
        ):
            CutSignals = tuple(sorted({
                Signal
                for Resource, Signals in FinalConflicts.items()
                for Signal in Signals
            }))
            AffectedSignals = tuple(sorted({
                *CutSignals,
                *CumulativeConflictSignals,
            }))
            raise RoutingStageError(RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="NegotiatedDetailedRouting",
                AffectedNets=AffectedSignals,
                Locations=tuple(sorted({
                    Resource.Position for Resource in MandatoryCutResources
                }))[:32],
                Resources=tuple(sorted(
                    str(Resource) for Resource in MandatoryCutResources
                ))[:32],
                RepairActions=("RelocateAffectedClusters",),
                Detail=(
                    "stagnant negotiated overflow includes mandatory "
                    "portal/access ownership and cannot be repaired only by "
                    "region expansion"
                ),
                Diagnostics={
                    "OverflowProgression": list(OverflowProgression),
                    "MandatoryConflictResourceCount": len(
                        MandatoryCutResources
                    ),
                    "MandatoryConflictClaims": {
                        str(Resource): list(Signals)
                        for Resource, Signals in sorted(
                            FinalConflicts.items(),
                            key=lambda Value: str(Value[0]),
                        )
                        if Resource in MandatoryCutResources
                    },
                    "InitialAssignment": dict(InitialAssignmentDiagnostics),
                    "InitialDetailedBatch": dict(
                        InitialDetailedBatchDiagnostics
                    ),
                    "ConflictGraph": {
                        "Classification": "mandatory-boundary-capacity-cut",
                        "ConflictSignals": list(AffectedSignals),
                        "CongestionCutSignals": list(AffectedSignals),
                        "RelocationSignals": list(AffectedSignals),
                    },
                },
            ))
        for Resource, Signals in FinalConflicts.items():
            Increment = Negotiated.HistoryIncrement * max(1, len(Signals) - 1)
            History[Resource.Position] += Increment
            for Neighbor in Technology.NeighborPositions(Resource.Position):
                History[Neighbor] += max(1, Increment // 2)
        RepairStates = {}
        ExpandedForConflict = False
        for Signal in ConflictSignals:
            Candidate = Selected[Signal]
            SignalConflictResources = {
                Resource
                for Resource, Signals in FinalConflicts.items()
                if Signal in Signals
            }
            RepairState = BuildNegotiatedRouteTreeState(
                Candidate,
                SignalConflictResources,
            )
            if not RepairState.PrunedTargets:
                continue
            RepairStates[Signal] = RepairState
            RetainedTargets = RepairState.RetainedTargets
            PrunedTargets = RepairState.PrunedTargets
            RetainedNodes = set(RepairState.RetainedNodes)
            RemovedNodes = set(Candidate.Nodes) - RetainedNodes
            RemovedEdges = {
                Edge
                for Edge in Candidate.Edges
                if Edge[0] in RemovedNodes or Edge[1] in RemovedNodes
            }
            BranchOutcomes = {
                str(Target): RepairBranchOutcomes.get(
                    Signal,
                    {},
                ).get(
                    str(Target),
                    "Unknown",
                )
                for Target in PrunedTargets
            }
            BranchRepairEvents.append({
                "Iteration": PassIndex + 1,
                "Signal": Signal,
                "RetainedBranches": [
                    {
                        "Target": list(Target),
                        "Path": [list(Value) for Value in Path],
                    }
                    for Target, Path in zip(
                        RetainedTargets,
                        RepairState.RetainedBranchPaths,
                    )
                ],
                "PrunedBranches": [
                    {
                        "Target": list(Target),
                        "PrunedPath": [list(Value) for Value in Path],
                        "Outcome": BranchOutcomes.get(
                            str(Target),
                            "Unknown",
                        ),
                    }
                    for Target, Path in zip(
                        PrunedTargets,
                        RepairState.PrunedBranchPaths,
                    )
                ],
                "RetainedBranchCount": len(RetainedTargets),
                "PrunedBranchCount": len(PrunedTargets),
                "RemovedNodeCount": len(RemovedNodes),
                "RemovedEdgeCount": len(RemovedEdges),
                "RemovedBranchCount": len(RepairState.PrunedBranchTailClaims),
                "PrunedBranchClaimCounts": [
                    len(Claims)
                    for Claims in RepairState.PrunedBranchTailClaims
                ],
                "RemovedNodes": [
                    list(Value)
                    for Value in sorted(RemovedNodes)
                ],
                "ConflictResources": [
                    str(Value) for Value in sorted(
                        SignalConflictResources,
                        key=str,
                    )
                ],
            })
            Touches = FindNegotiatedBoundaryTouches(
                Candidate.Nodes,
                RegionStates[Signal].ActiveTiles,
                RegionStates[Signal].Bounds,
                RegionStates[Signal].TileSize,
            )
            if Touches:
                for Side in PreferredExpansionSides(Signal, Candidate):
                    if Side not in Touches:
                        continue
                    ExpandedForConflict = (
                        ExpandSignalRegion(
                            Signal,
                            Side,
                            "route-tree-boundary-touch",
                            Touches.get(Side, ()),
                        )
                        or ExpandedForConflict
                    )
                    if ExpandedForConflict:
                        break
        if (
            PreviousConflictCount is not None
            and ConflictCount >= PreviousConflictCount
        ):
            StagnationCount += 1
        else:
            StagnationCount = 0
        PreviousConflictCount = ConflictCount
        if StagnationCount >= Negotiated.StagnationPassLimit - 1:
            Hotspots = tuple(sorted({
                Resource.Position for Resource in FinalConflicts
            }))
            for Signal in ConflictSignals:
                for Side in PreferredExpansionSides(
                    Signal,
                    Selected[Signal],
                    Hotspots,
                ):
                    if ExpandSignalRegion(
                        Signal,
                        Side,
                        "stagnant-overflow",
                        Hotspots,
                    ):
                        ExpandedForConflict = True
                        break
            if ExpandedForConflict:
                StagnationCount = 0
                # The required one-tile delta is now retained in diagnostics.
                # A placement whose overflow stayed flat for the complete
                # stagnation window must proceed to the next deterministic
                # repair pass with the same absolute deadline.
                continue
            else:
                break

    Hotspots = tuple(sorted({Resource.Position for Resource in FinalConflicts}))
    FinalAffectedSignals = tuple(sorted(
        set(ConflictSignals)
        | CumulativeConflictSignals
    ))
    raise RoutingStageError(RoutingFailure(
        Reason=RoutingFailureReason.DetailedCongestionUnresolved,
        Stage="NegotiatedDetailedRouting",
        AffectedNets=FinalAffectedSignals,
        Locations=Hotspots[:32],
        RepairActions=("RelocateAffectedClusters", "ExpandCongestedCut"),
        Detail=(
            "negotiated route trees retained capacity-one conflicts after "
            f"{len(Iterations)} deterministic passes"
        ),
        Diagnostics={
            "Algorithm": "negotiated-route-trees-v1",
            "ConflictGraph": {
                "Classification": "detailed-congestion-cut",
                "ConflictSignals": list(FinalAffectedSignals),
                "RelocationSignals": list(FinalAffectedSignals),
                "ResourceHotspots": [list(Value) for Value in Hotspots[:32]],
            },
            "OverflowProgression": OverflowProgression,
            "ConflictResources": {
                str(Resource): list(Signals)
                for Resource, Signals in sorted(
                    FinalConflicts.items(),
                    key=lambda Value: str(Value[0]),
                )
            },
            "CachedNodeCount": Resources.ResourceGraph.CachedNodeCount,
            "CachedEdgeCount": Resources.ResourceGraph.CachedEdgeCount,
            "HaloSize": TileSize,
            "Regions": {
                Signal: {
                    "ActiveTiles": [
                        list(Value) for Value in sorted(State.ActiveTiles)
                    ],
                    "BoundaryTouches": [
                        list(Value) for Value in sorted(State.BoundaryTouches)
                    ],
                    "ExpandedSides": list(State.ExpandedSides),
                    "ExpansionEvents": list(State.ExpansionEvents),
                    "OwnedNodeCount": len(State.AddedNodes),
                    "OwnedEdgeCount": len(State.AddedEdges),
                }
                for Signal, State in sorted(RegionStates.items())
            },
            "BranchRepairs": BranchRepairEvents,
            "InitialDetailedBatch": dict(InitialDetailedBatchDiagnostics),
            "SearchExpansionEscalations": dict(
                sorted(SearchExpansionEscalations.items())
            ),
            "CumulativeConflictSignals": sorted(CumulativeConflictSignals),
            "RepeaterRejections": {
                Signal: dict(sorted(Values.items()))
                for Signal, Values in sorted(RejectionCountsBySignal.items())
            },
            "NativeSearch": {
                Signal: list(Values)
                for Signal, Values in sorted(
                    NativeSearchDiagnosticsBySignal.items()
                )
            },
        },
    ))


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
    ReservationVariant: int = 0,
    LaneDiversityLevel: int = 0,
    CandidateDiversityLevel: int = 0,
    SkipStrictPortalReservation: bool = False,
    EscalationStates: tuple[tuple[object, ...], ...] = (),
    Deadline: RoutingDeadline | None = None,
    RetainedCandidateCache: dict[
        str, tuple[NetRouteCandidate, ...]
    ] | None = None,
    RetainedCandidateMetadata: dict[
        str, dict[str, tuple[str, int, int, int]]
    ] | None = None,
    PriorCandidateCache: dict[
        str, tuple[NetRouteCandidate, ...]
    ] | None = None,
    PriorCandidateMetadata: dict[
        str, dict[str, tuple[str, int, int, int]]
    ] | None = None,
    RegenerateSignals: frozenset[str] = frozenset(),
    RawPortalCache: RawPortalGeometryCache | None = None,
    PreparedPortalCache: PreparedPortalDomainCache | None = None,
) -> RoutedDesign:
    """Generate portals and select complete capacity-one routes in Rust."""
    RoutingCallStarted = monotonic()
    if RustRoutingContext is None:
        raise ValueError("authoritative routing requires the Rust router")
    if Deadline is None:
        Deadline = RoutingDeadline.Start(Policy.RuntimeBudgetSeconds)
    RoutingStarted = (
        SharedRoutingStarted
        if SharedRoutingStarted is not None
        else monotonic()
    )
    PlacementRelocationDiagnostics = (
        (getattr(Placed, "LocalRouteDiagnostics", {}) or {}).get(
            "__PlacementRelocation__",
            {},
        )
    )
    PlacementWasRelocated = bool(PlacementRelocationDiagnostics)
    PlacementRecipeDiagnostics = (
        (getattr(Placed, "LocalRouteDiagnostics", {}) or {}).get(
            "__PlacementRecipe__",
            {},
        )
    )
    AllowRelocatedStarvationLaneRetry = (
        PlacementWasRelocated
        and isinstance(PlacementRecipeDiagnostics, dict)
        and (
            PlacementRecipeDiagnostics.get("SourceGenerator")
            != "row-beam-conflict-relocation"
            or (
                isinstance(PlacementRelocationDiagnostics, dict)
                and int(PlacementRelocationDiagnostics.get("Variant", 0)) >= 4
            )
        )
    )
    PlacementWasBroadlyRelocated = (
        isinstance(PlacementRelocationDiagnostics, dict)
        and (
            len(PlacementRelocationDiagnostics.get("PrioritySignals", ()))
            >= 3
            or len(PlacementRelocationDiagnostics.get("Clusters", ())) > 3
        )
    )
    StageTimings: dict[str, float] = {}
    EscalationState = (
        Policy.QualityTarget,
        AdaptiveLayerFloor if AdaptiveLayerFloor is not None else 0,
        ReservationVariant,
        LaneDiversityLevel,
        CandidateDiversityLevel,
        bool(Policy.AdaptiveRouting.Enabled),
        bool(SkipStrictPortalReservation),
        bool(Policy.GlobalRouting.EnableCapacityAwareGuides),
    )
    if EscalationState in EscalationStates:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.Stagnated,
                Stage="RoutingEscalation",
                Detail=(
                    "routing escalation requested a previously evaluated control "
                    "state; returning to placement without repeating work"
                ),
                Diagnostics={
                    "EscalationHistory": tuple(EscalationHistory),
                    "EscalationStates": [Value for Value in EscalationStates],
                    "EscalationState": EscalationState,
                },
            )
        )
    EscalationStates = (*EscalationStates, EscalationState)

    # The sixth unit is completed by RoutePcbAttempt only after cleanup and
    # compaction.  Keeping it outside this planner prevents a successful
    # assignment from being reported as a fully completed physical design.
    StageCount = 6
    WorkTelemetry: dict[str, object] = {
        "SignalCount": 0,
        "TerminalCount": 0,
        "PortalRequestCount": 0,
        "PortalTargetCount": 0,
        "RouteTreeRequestCount": 0,
        "CandidateDiversityLevel": CandidateDiversityLevel,
        "ReservationVariant": ReservationVariant,
        "LaneDiversityLevel": LaneDiversityLevel,
    }
    RuntimeEscalationState = RoutingEscalationState(
        PortalMode=(
            "unreserved"
            if not Policy.AdaptiveRouting.Enabled or SkipStrictPortalReservation
            else "reserved"
        ),
        ReservationVariant=ReservationVariant,
        LaneDiversityLevel=LaneDiversityLevel,
        CandidateDiversityLevel=CandidateDiversityLevel,
        EffectiveRoutingLayers=max(0, AdaptiveLayerFloor or 0),
        AssignmentBudget=0,
    )
    AssignmentRetryHistory: list[dict[str, object]] = []
    AdaptiveExpiresAt = Deadline.ExpiresAt

    def CurrentRuntimeBudgetDiagnostics(
        AdditionalDiagnostics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return BuildRoutingDeadlineDiagnostics(
            Deadline=Deadline,
            WorkTelemetry=WorkTelemetry,
            EscalationHistory=tuple((
                *EscalationHistory,
                *AssignmentRetryHistory,
            )),
            EscalationState=RuntimeEscalationState,
            StageTimingsSeconds=StageTimings,
            AdditionalDiagnostics=AdditionalDiagnostics,
        )

    def CheckRuntimeBudget(
        Stage: str,
        AdditionalDiagnostics: dict[str, object] | None = None,
    ) -> None:
        Current = monotonic()
        if Current < Deadline.ExpiresAt and Current < AdaptiveExpiresAt:
            return
        EnforceRoutingRuntimeLimit(
            Deadline=Deadline,
            AdaptiveStartedAt=RoutingStarted,
            AdaptiveExpiresAt=AdaptiveExpiresAt,
            Stage=Stage,
            Diagnostics=CurrentRuntimeBudgetDiagnostics(
                AdditionalDiagnostics
            ),
        )
    if ProgressCallback is not None:
        ProgressCallback(0, StageCount)
    DisableLocalBaseClaims = (
        bool(os.environ.get("RCS_DISABLE_LOCAL_BASE_CLAIMS"))
        or bool(os.environ.get("RCS_DISABLE_LOCAL_CLAIMS"))
    )
    AllLocalClaims = tuple(getattr(Placed, "LocalRouteClaims", ()) or ())
    SignalTargets = _CollectSignalTargets(Placed)
    LocalClaimsBySignal: dict[str, tuple[LocalRouteClaim, ...]] = defaultdict(tuple)
    for Claim in AllLocalClaims:
        LocalClaimsBySignal[Claim.Signal] = (
            *LocalClaimsBySignal[Claim.Signal],
            Claim,
        )
    LocalClaims = SelectAuthoritativeBaseClaims(
        AllLocalClaims,
        DisableLocalBaseClaims,
    )
    Profiles = BuildNetRoutingProfiles(
        Placed,
        AccessLength=Policy.Placement.PinEscapeLength,
    )
    Demand = EstimateRoutingDemand(Placed, Profiles)
    AdaptiveBudget = DeriveRoutingBudget(Demand, Policy, Technology)
    AdaptiveExpiresAt = min(
        Deadline.ExpiresAt,
        RoutingStarted + AdaptiveBudget.RuntimeSeconds,
    )
    CheckRuntimeBudget("RoutingBudget")
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
        print(
            "[debug] authoritative: adaptive budget "
            f"candidates={AdaptiveBudget.ClusterCellCeiling} "
            f"layers={AdaptiveBudget.LayerCount} "
            f"portals_per_terminal={AdaptiveBudget.PortalsPerTerminal} "
            f"lanes={AdaptiveBudget.LaneCount} "
            f"candidates_per_net={AdaptiveBudget.CandidatesPerNet} "
            f"candidate_expansions_per_net={AdaptiveBudget.CandidateExpansionsPerNet} "
            f"assignment_expansions={AdaptiveBudget.AssignmentExpansions} "
            f"runtime_seconds={AdaptiveBudget.RuntimeSeconds:.3f}",
            flush=True,
    )
    WorkTelemetry["SignalCount"] = len(Profiles)
    WorkTelemetry["TerminalCount"] = Demand.TerminalCount
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
    # Portal-count diversity remains demand-derived. Vertically stacked
    # terminals may require a higher initial physical layer below; increasing
    # the number of portal variants as well would multiply independent search
    # dimensions without adding a new kind of pin escape.
    PortalLimit = (
        min(
            Policy.TrackAssignment.MaximumPortalsPerTerminal,
            AdaptiveBudget.PortalsPerTerminal,
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.TrackAssignment.MaximumPortalsPerTerminal
    )
    RouteLaneCount = (
        min(
            Policy.GlobalRouting.CandidateLaneCount,
            min(
                AdaptiveBudget.LaneCount,
                Policy.AdaptiveRouting.InitialLaneCount,
            )
            * Policy.AdaptiveRouting.LaneGrowthFactor ** LaneDiversityLevel,
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.GlobalRouting.CandidateLaneCount
    )
    RoutePortalVariantCounts = {
        Signal: (
            min(
                PortalLimit,
                AdaptiveBudget.PortalsPerTerminal,
            )
            if Policy.AdaptiveRouting.Enabled
            else (
                PortalLimit
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
    ReservedAccess = frozenset(
        Position
        for Profile in Profiles.values()
        for Path in (Profile.SourceAccessPath, *Profile.TargetAccessPaths.values())
        for Position in Path
    ) | frozenset(
        Position for Claim in LocalClaims for Position in Claim.Nodes
    )
    MaximumAccessY = max(
        (Position[1] for Position in ReservedAccess),
        default=MinimumY,
    )
    EffectiveRoutingHeight = max(
        MaximumRoutingHeight,
        MaximumAccessY - MinimumY,
    )
    RequiredAccessLayerCount = RequiredRoutingLayerCountForAccess(
        MinimumY,
        ReservedAccess,
        Policy.DetailedRouting.GuideExpansion,
        Technology,
    )
    RouteLayers = getattr(Placed, "RouteLayers", None) or {}
    PolicyLayerLimit = Policy.Placement.MaximumRoutingLayers
    MinimumLayerCount = (
        min(Technology.MinimumRoutingLayerCount, PolicyLayerLimit)
        if PolicyLayerLimit > 0
        else Technology.MinimumRoutingLayerCount
    )
    MaximumLayerCount = (
        min(Technology.MaximumRoutableLayerCount, PolicyLayerLimit)
        if PolicyLayerLimit > 0
        else Technology.MaximumRoutableLayerCount
    )
    # MaximumRoutingHeight is physical headroom, not a request to instantiate
    # every usable routing layer.  Starting with all available elevations made
    # portal work scale with box height (and caused the CLA portal explosion).
    # Adaptive routing starts at the minimum viable layer count and adds one
    # layer only after a concrete guide/assignment failure.
    HeightCapacity = max(
        MinimumLayerCount,
        (EffectiveRoutingHeight - 2) // Technology.RoutingLayerPitch,
    )
    EffectiveMaximumLayerCount = min(MaximumLayerCount, HeightCapacity)
    NegotiatedLayerFloor = (
        ceil(
            Demand.TerminalCount
            / max(
                1,
                Policy.NegotiatedRouting.TilePitchInTracks
                * Technology.TrackPitch,
            )
        )
        if Policy.NegotiatedRouting.Enabled
        else 0
    )
    LayerCount = SelectInitialRoutingLayerCount(
        MinimumLayerCount=MinimumLayerCount,
        EffectiveMaximumLayerCount=EffectiveMaximumLayerCount,
        RequiredAccessLayerCount=RequiredAccessLayerCount,
        AdaptiveLayerCount=(
            AdaptiveBudget.LayerCount
            if Policy.AdaptiveRouting.Enabled
            else MinimumLayerCount
        ),
        AdaptiveLayerFloor=AdaptiveLayerFloor or 0,
        NegotiatedLayerFloor=NegotiatedLayerFloor,
        ExistingRouteLayerCount=max(RouteLayers.values(), default=0) + 1,
        PlacementWasRelocated=PlacementWasRelocated,
        ForceMaximumAfterPlacementRelocation=(
            Policy.Placement.ForceMaximumRoutingLayersAfterPlacementRelocation
        ),
    )
    RuntimeEscalationState = replace(
        RuntimeEscalationState,
        EffectiveRoutingLayers=LayerCount,
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
            WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
                "Guide",
                Diagnostics,
            ),
        )
        if Policy.GlobalRouting.EnableCapacityAwareGuides
        else None
    )
    StageTimings["GlobalGuidePlanning"] = monotonic() - GuidePlanningStarted
    CheckRuntimeBudget("Guide")
    if ProgressCallback is not None:
        ProgressCallback(1, StageCount)
    ActiveMaximumY = max(
        MaximumAccessY + 1,
        Technology.RoutingY(MinimumY, LayerCount - 1) + 1,
    )
    PhysicalMaximumY = MinimumY + EffectiveRoutingHeight
    Bounds = (
        MinimumX - SearchMarginX,
        MaximumX + SearchMarginX,
        MinimumY,
        min(PhysicalMaximumY, ActiveMaximumY),
        MinimumZ - SearchMarginZ,
        MaximumZ + SearchMarginZ,
    )
    AssignedColumns: set[Position2] = set()
    # The coarse planner has already selected and capacity-checked its lane.
    # SharedBoundaryEnvelope is a guide-selection cost envelope, not detailed
    # routing geometry.  Adding it here eagerly materialized a second, much
    # wider corridor.  Start with the selected lane and grow by one isolated
    # track per explicit lane-diversity escalation.  BuildRegion reuses the
    # preceding cached region when this domain grows.
    RegionExpansion = (
        Policy.DetailedRouting.GuideExpansion
        + Technology.TrackPitch * LaneDiversityLevel
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
    ResourceGraphStarted = monotonic()
    Region = Resources.ResourceGraph.BuildRegion(
        Bounds,
        AllowedColumns=frozenset(AssignedColumns),
        AllowedAccess=ReservedAccess,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "ResourceGraph",
            Diagnostics,
        ),
    )
    StageTimings["ResourceGraph"] = monotonic() - ResourceGraphStarted
    if ProgressCallback is not None:
        ProgressCallback(2, StageCount)
    PortalStarted = monotonic()
    CheckRuntimeBudget("Portal")
    CacheMatches = bool(
        RawPortalCache is not None
        and RawPortalCache.Matches(
            Placed,
            Resources,
            Region,
            LayerCount,
            PortalLimit,
            RoutePortalVariantCounts,
            Policy.DetailedRouting.GuideExpansion,
            Policy.DetailedRouting.StrictMaximumExpansions,
        )
    )
    PortalRequests: list[tuple[Any, ...]] = []
    if CacheMatches:
        assert RawPortalCache is not None
        Context = (
            RawPortalCache.Context
            if RawPortalCache.Region is Region
            else RustRoutingContext(
                Bounds,
                (MinimumX, MaximumX, MinimumZ, MaximumZ),
                sorted(Region.Nodes),
                sorted(Region.Edges),
            )
        )
        RawPortals = RawPortalCache.BuildPortalDictionary()
        WorkTelemetry.update({
            "PortalCacheHit": True,
            "PortalRequestCount": RawPortalCache.RequestCount,
            "PortalTargetCount": RawPortalCache.TargetCount,
            "PortalStarvationFallbackCount": RawPortalCache.StarvationCount,
            "PortalCompletedWork": 0,
            "PortalBatchCount": 0,
        })
        EffectiveRawPortalCache = (
            RawPortalCache
            if RawPortalCache.Region is Region
            else replace(
                RawPortalCache,
                Region=Region,
                Context=Context,
            )
        )
    else:
        Context = RustRoutingContext(
            Bounds,
            (MinimumX, MaximumX, MinimumZ, MaximumZ),
            sorted(Region.Nodes),
            sorted(Region.Edges),
        )
        RawPortals: dict[
            tuple[str, Position3, int], tuple[PinAccessPortal, ...]
        ] = {}
        RegionNodeSet = frozenset(Region.Nodes)
        NodesByColumn: dict[Position2, list[Position3]] = defaultdict(list)
        for Position in Region.Nodes:
            NodesByColumn[(Position[0], Position[2])].append(Position)
        NodesByLayer: dict[int, tuple[Position3, ...]] = {
            Technology.RoutingY(MinimumY, LayerIndex): tuple(
                sorted(
                    Position
                    for Position in Region.Nodes
                    if Position[1]
                    == Technology.RoutingY(MinimumY, LayerIndex)
                )
            )
            for LayerIndex in range(LayerCount)
        }
        PortalStarvationCount = 0
        PortalRequestMetadata = []
        for Signal in sorted(Profiles):
            Profile = Profiles[Signal]
            TerminalPaths = (
                (Profile.Root, Profile.SourceAccessPath),
                *(
                    (Target, Profile.TargetAccessPaths[Target])
                    for Target in Profile.Targets
                ),
            )
            for Terminal, AccessPath in TerminalPaths:
                AccessColumns = {(X, Z) for X, _Y, Z in AccessPath}
                AllowedColumns = {
                    (AccessX + DeltaX, AccessZ + DeltaZ)
                    for AccessX, AccessZ in AccessColumns
                    for DeltaX in range(
                        -Policy.DetailedRouting.GuideExpansion,
                        Policy.DetailedRouting.GuideExpansion + 1,
                    )
                    for DeltaZ in range(
                        -Policy.DetailedRouting.GuideExpansion,
                        Policy.DetailedRouting.GuideExpansion + 1,
                    )
                    if abs(DeltaX) + abs(DeltaZ)
                    <= Policy.DetailedRouting.GuideExpansion
                }
                AllowedNodeSet = {
                    Position
                    for Column in AllowedColumns
                    for Position in NodesByColumn.get(Column, ())
                } | set(AccessPath)
                AllowedNodes = sorted(AllowedNodeSet)
                for Layer in range(LayerCount):
                    RoutingY = Technology.RoutingY(MinimumY, Layer)
                    PortalStarts = list(
                        SelectGraphAccessStarts(AccessPath, RegionNodeSet)
                    )
                    PortalAllowedNodes = list(AllowedNodes)
                    PortalTargets = sorted(
                        (
                            Position
                            for Position in AllowedNodes
                            if Position[1] == RoutingY
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
                    if len(PortalTargets) == 0:
                        # Expand only the target envelope. Starts remain
                        # graph-valid terminal cells; unavailable layers do
                        # not produce detached or wrong-elevation fallbacks.
                        GlobalLayerTargets = list(
                            NodesByLayer.get(RoutingY, ())
                        )
                        if GlobalLayerTargets:
                            AccessTerminal = AccessPath[-1]
                            PortalAllowedNodes = list(sorted(
                                set(PortalAllowedNodes)
                                | set(GlobalLayerTargets)
                            ))
                            PortalTargets = sorted(
                                GlobalLayerTargets,
                                key=lambda Position: (
                                    abs(Position[0] - AccessTerminal[0])
                                    + abs(Position[2] - AccessTerminal[2]),
                                    abs(Position[0] - AccessTerminal[0]),
                                    abs(Position[2] - AccessTerminal[2]),
                                    Position,
                                ),
                            )
                            PortalStarvationCount += 1
                    MaxPortalTargets = max(
                        1,
                        min(
                            len(PortalTargets),
                            RoutePortalVariantCounts[Signal],
                        ),
                    )
                    PortalTargets = PortalTargets[:MaxPortalTargets]
                    PortalRequests.append((
                        list(PortalStarts),
                        PortalTargets,
                        PortalAllowedNodes,
                        RoutingY,
                        PortalLimit,
                        Policy.DetailedRouting.StrictMaximumExpansions,
                    ))
                    PortalRequestMetadata.append((Signal, Terminal, Layer))
        WorkTelemetry["PortalRequestCount"] = len(PortalRequests)
        WorkTelemetry["PortalTargetCount"] = sum(
            len(Request[1]) for Request in PortalRequests
        )
        WorkTelemetry["PortalStarvationFallbackCount"] = PortalStarvationCount
        WorkTelemetry["PortalCacheHit"] = False
        WorkTelemetry["PortalBatchCount"] = 1
        CheckRuntimeBudget("Portal")
        if hasattr(Context, "GeneratePortalCandidateBatchesBounded"):
            PortalBatchResult = Context.GeneratePortalCandidateBatchesBounded(
                PortalRequests,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ),
            )
            WorkTelemetry["PortalCompletedWork"] = (
                PortalBatchResult.CompletedWork
            )
            if PortalBatchResult.DeadlineExceeded:
                EnforceRoutingRuntimeLimit(
                    Deadline=Deadline,
                    AdaptiveStartedAt=RoutingStarted,
                    AdaptiveExpiresAt=AdaptiveExpiresAt,
                    Stage="Portal",
                    Diagnostics=CurrentRuntimeBudgetDiagnostics(),
                    NativeDeadlineExceeded=True,
                )
            PortalResults = PortalBatchResult.Candidates
        else:
            PortalResults = Context.GeneratePortalCandidateBatches(
                PortalRequests
            )
            WorkTelemetry["PortalCompletedWork"] = len(PortalResults)
        for (Signal, Terminal, Layer), Values in zip(
            PortalRequestMetadata,
            PortalResults,
        ):
            RawPortals[(Signal, Terminal, Layer)] = tuple(
                _PortalFromRust(Signal, Terminal, Layer, Value, Resources)
                for Value in Values
            )
        EffectiveRawPortalCache = RawPortalGeometryCache(
            PlacedIdentity=id(Placed),
            ResourcesIdentity=id(Resources),
            Region=Region,
            LayerCount=LayerCount,
            PortalLimit=PortalLimit,
            PortalVariantCounts=tuple(sorted(RoutePortalVariantCounts.items())),
            GuideExpansion=Policy.DetailedRouting.GuideExpansion,
            StrictMaximumExpansions=(
                Policy.DetailedRouting.StrictMaximumExpansions
            ),
            Context=Context,
            PortalEntries=tuple(sorted(RawPortals.items())),
            RequestCount=len(PortalRequests),
            TargetCount=int(WorkTelemetry["PortalTargetCount"]),
            StarvationCount=PortalStarvationCount,
        )

    # Negotiated candidate construction retains the bounded portal pool so
    # candidate-level capacity matching can choose a compatible tuple along
    # with each complete route tree.
    UnreservedPortalMode = (
        Policy.NegotiatedRouting.Enabled
        or (not Policy.AdaptiveRouting.Enabled)
        or SkipStrictPortalReservation
    )
    PreparedCacheMatches = bool(
        PreparedPortalCache is not None
        and PreparedPortalCache.Matches(
            EffectiveRawPortalCache,
            UnreservedPortalMode,
            ReservationVariant,
        )
    )
    if PreparedCacheMatches:
        assert PreparedPortalCache is not None
        Portals = PreparedPortalCache.BuildPortalDictionary()
        PortalReservations = PreparedPortalCache.Reservations
        EffectivePreparedPortalCache = PreparedPortalCache
    else:
        Portals = dict(RawPortals)
        if UnreservedPortalMode:
            Portals = {
                Key: tuple(
                    sorted(
                        Value,
                        key=lambda Value: (Value.Cost, Value.PortalId),
                    )
                )
                for Key, Value in sorted(Portals.items())
            }
            PortalReservations = ()
        else:
            if Policy.NegotiatedRouting.Enabled:
                Portals, PortalReservations = ReserveNegotiatedBoundaryEscapes(
                    Portals,
                    Profiles,
                    Resources,
                    ReservationVariant=ReservationVariant,
                    MaximumExpansions=AdaptiveBudget.AssignmentExpansions,
                )
            else:
                Portals, PortalReservations = ReserveBoundaryPortals(
                    Portals,
                    ReservationVariant=ReservationVariant,
                    MaximumExpansions=AdaptiveBudget.AssignmentExpansions,
                    RequireConflictFree=False,
                    StrictTerminalThreshold=4,
                )
        EffectivePreparedPortalCache = PreparedPortalDomainCache(
            RawPortalCache=EffectiveRawPortalCache,
            UnreservedPortalMode=UnreservedPortalMode,
            ReservationVariant=ReservationVariant,
            PortalEntries=tuple(sorted(Portals.items())),
            Reservations=PortalReservations,
        )

    # Keep the unreserved multi-layer domain for detailed routing, but seed a
    # capacity-one portal tuple into it for simultaneous-demand designs.  The
    # previous all-or-nothing reservation mode replaced every domain with one
    # layer and could leave a legal portal tuple without a legal sparse tree.
    # A seed gives exact candidate assignment one globally compatible escape
    # per net while the remaining fixed requests retain routing alternatives.
    ReservedPortalSeedBySignal: dict[
        str,
        tuple[int, PinAccessPortal, tuple[PinAccessPortal, ...]],
    ] = {}
    if UnreservedPortalMode and len(Profiles) > 8:
        try:
            ReservedSeedPortals, _ReservedSeedClaims = (
                ReserveNegotiatedBoundaryEscapes(
                    dict(RawPortals),
                    Profiles,
                    Resources,
                    ReservationVariant=ReservationVariant,
                    MaximumExpansions=AdaptiveBudget.AssignmentExpansions,
                )
            )
        except RoutingStageError as Error:
            WorkTelemetry["PortalSeedReservation"] = {
                "Result": "unavailable",
                "Reason": Error.Failure.Reason.value,
            }
        else:
            for Signal, Profile in Profiles.items():
                for Layer in range(LayerCount):
                    SourceValues = ReservedSeedPortals.get(
                        (Signal, Profile.Root, Layer),
                        (),
                    )
                    TargetValues = tuple(
                        ReservedSeedPortals.get((Signal, Target, Layer), ())
                        for Target in Profile.Targets
                    )
                    if len(SourceValues) != 1 or any(
                        len(Values) != 1 for Values in TargetValues
                    ):
                        continue
                    ReservedPortalSeedBySignal[Signal] = (
                        Layer,
                        SourceValues[0],
                        tuple(Values[0] for Values in TargetValues),
                    )
                    break
            WorkTelemetry["PortalSeedReservation"] = {
                "Result": "seeded",
                "SignalCount": len(ReservedPortalSeedBySignal),
            }

    StageTimings["PortalGeneration"] = monotonic() - PortalStarted
    CheckRuntimeBudget("Portal")
    if ProgressCallback is not None:
        ProgressCallback(3, StageCount)
    CandidateStarted = monotonic()
    CandidateRequestCount = 0
    NegotiatedPlan: NegotiatedRoutePlan | None = None
    UseNegotiatedRouting = (
        Policy.NegotiatedRouting.Enabled
        and bool(Profiles)
    )
    if UseNegotiatedRouting:
        RetainedCandidateCache = None
        RetainedCandidateMetadata = None
        PriorCandidateCache = None
        PriorCandidateMetadata = None

    def GenerateRouteTreesWithDeadline(
        Requests: list[tuple[Any, ...]],
    ) -> list[Any]:
        CheckRuntimeBudget("Candidate")
        if hasattr(Context, "GenerateRouteTreesBounded"):
            BatchResult = Context.GenerateRouteTreesBounded(
                Requests,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ),
            )
            WorkTelemetry["RouteTreeCompletedWork"] = (
                int(WorkTelemetry.get("RouteTreeCompletedWork", 0))
                + BatchResult.CompletedWork
            )
            if BatchResult.DeadlineExceeded:
                EnforceRoutingRuntimeLimit(
                    Deadline=Deadline,
                    AdaptiveStartedAt=RoutingStarted,
                    AdaptiveExpiresAt=AdaptiveExpiresAt,
                    Stage="Candidate",
                    Diagnostics=CurrentRuntimeBudgetDiagnostics(),
                    NativeDeadlineExceeded=True,
                )
            return list(BatchResult.RouteTrees)
        Values = Context.GenerateRouteTrees(Requests)
        CheckRuntimeBudget("Candidate")
        return Values
    InitialRequestLimit = max(
        1,
        (
            Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal
            * Policy.AdaptiveRouting.CandidateGrowthFactor
            ** max(CandidateDiversityLevel, LaneDiversityLevel)
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
    )
    ActiveCandidateSignalCount = sum(
        1
        for Signal in Profiles
        if not (
            Signal not in RegenerateSignals
            and (RetainedCandidateCache or {}).get(Signal)
        )
    )
    ProvisionalCandidateRequestCount = max(
        1,
        InitialRequestLimit * ActiveCandidateSignalCount,
    )
    BaseCandidateExpansionLimit = (
        min(
            AdaptiveBudget.CandidateExpansionsPerNet,
            max(
                Policy.DetailedRouting.MinimumCandidateExpansionLimit,
                Policy.AdaptiveRouting.MaximumCandidateGenerationExpansions
                // ProvisionalCandidateRequestCount,
            ),
        )
        if Policy.AdaptiveRouting.Enabled
        else (
            Policy.DetailedRouting.StrictMaximumExpansions
            if not Profiles
            else max(
                Policy.DetailedRouting.MinimumCandidateExpansionLimit,
                min(
                    Policy.DetailedRouting.StrictMaximumExpansions,
                    12_000_000 // ProvisionalCandidateRequestCount,
                ),
            )
        )
    )
    CandidateExpansionLimits = {
        Signal: min(
            Policy.DetailedRouting.StrictMaximumExpansions,
            BaseCandidateExpansionLimit
            * max(1, (max(1, len(Profile.Targets)) - 1).bit_length()),
        )
        for Signal, Profile in Profiles.items()
    }
    CandidatesBySignal: dict[str, list[NetRouteCandidate]] = defaultdict(list)
    CandidateLimitsBySignal: dict[str, int] = {}
    CandidateDiagnostics: dict[str, dict[str, object]] = {}
    CandidateSignalOrder = sorted(
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
    )
    # Candidate windows are shared capacity work.  Spread their initial portal
    # products deterministically so nearby signals do not begin from the same
    # shape.  The phase remains independent of placement coordinates: small
    # designs need the established ordering to retain their legal portal mix.
    CandidatePortalPhaseBySignal = {
        Signal: Index
        for Index, Signal in enumerate(CandidateSignalOrder)
    }
    ProtectedNodesBySignal = {
        Signal: frozenset(
            {
                *Profiles[Signal].SourceAccessPath,
                *(
                    Position
                    for Path in Profiles[Signal].TargetAccessPaths.values()
                    for Position in Path
                ),
                *(
                    Position
                    for Claim in LocalClaimsBySignal.get(Signal, ())
                    for Position in Claim.Nodes
                ),
            }
        )
        for Signal in Profiles
    }
    ForeignBlockedNodesBySignal = {
        Signal: frozenset(
            Technology.BuildElectricalExclusions(
                set().union(*(
                    ProtectedNodesBySignal[OtherSignal]
                    for OtherSignal in Profiles
                    if OtherSignal != Signal
                ))
            )
            - ProtectedNodesBySignal[Signal]
        )
        for Signal in Profiles
    }

    def BuildSelfLegalPortalTuples(
        Profile: Any,
        SourcePortals: tuple[PinAccessPortal, ...],
        TargetPortalSets: list[tuple[PinAccessPortal, ...]],
    ) -> tuple[tuple[PinAccessPortal, ...], ...]:
        """Enumerate a bounded, exact-claim-legal net-wide portal product."""
        Domains = (SourcePortals, *TargetPortalSets)
        AccessPaths = (
            Profile.SourceAccessPath,
            *(Profile.TargetAccessPaths[Target] for Target in Profile.Targets),
        )
        Beam: list[tuple[int, tuple[PinAccessPortal, ...]]] = [(0, ())]
        for AccessPath, Domain in zip(AccessPaths, Domains):
            Next: dict[
                tuple[str, ...], tuple[int, tuple[PinAccessPortal, ...]]
            ] = {}
            for PreviousCost, PreviousPortals in Beam:
                for Portal in Domain:
                    CandidatePortals = (*PreviousPortals, Portal)
                    Nodes = {
                        Position
                        for CandidateAccessPath, CandidatePortal in zip(
                            AccessPaths, CandidatePortals
                        )
                        for Position in (
                            *CandidateAccessPath,
                            *CandidatePortal.Path,
                        )
                    }
                    if FindSelfClaimConflicts({
                        Profile.Signal: (
                            Resources.ResourceGraph.BuildRouteClaims(Nodes)
                        )
                    }):
                        continue
                    PortalIds = tuple(
                        Value.PortalId for Value in CandidatePortals
                    )
                    Candidate = (
                        PreviousCost + Portal.Cost,
                        CandidatePortals,
                    )
                    Existing = Next.get(PortalIds)
                    if Existing is None or Candidate[0] < Existing[0]:
                        Next[PortalIds] = Candidate
            Beam = sorted(
                Next.values(),
                key=lambda Value: (
                    Value[0],
                    tuple(Portal.PortalId for Portal in Value[1]),
                ),
            )[:16]
            if not Beam:
                break
        return tuple(
            PortalsValue
            for _Cost, PortalsValue in Beam
            if len(PortalsValue) == len(Domains)
        )

    RouteRequestsBySignal = {}
    RouteMetadataBySignal = {}
    DeferredRouteRequestCountsBySignal: Counter[str] = Counter()
    ForeignPortalOverlapBySignal: Counter[str] = Counter()
    CandidateAxisLaneBySignal: dict[str, dict[str, tuple[str, int, int, int]]] = {}
    for Signal, Values in (PriorCandidateMetadata or {}).items():
        if Signal in Profiles:
            CandidateAxisLaneBySignal[Signal] = dict(Values)
    for Signal, Values in (RetainedCandidateCache or {}).items():
        if Signal in Profiles and Signal not in RegenerateSignals:
            CandidatesBySignal[Signal] = list(Values)
            CandidateAxisLaneBySignal[Signal] = dict(
                (RetainedCandidateMetadata or {}).get(Signal, {})
            )
    for Signal in CandidateSignalOrder:
        if (
            Signal in RegenerateSignals
            and CandidatesBySignal.get(Signal)
        ):
            CandidatesBySignal.pop(Signal, None)
            CandidateAxisLaneBySignal.pop(Signal, None)
        if CandidatesBySignal.get(Signal):
            RouteRequestsBySignal[Signal] = []
            RouteMetadataBySignal[Signal] = []
            continue
        Profile = Profiles[Signal]
        CandidateExpansionLimit = CandidateExpansionLimits[Signal]
        RouteRequests = []
        RouteMetadata = []
        RoutePriorities: list[tuple[object, ...]] = []
        RequestWindowOffset = CandidateRequestWindowOffset(
            Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal,
            Policy.AdaptiveRouting.CandidateGrowthFactor,
            LayerCount,
            CandidateDiversityLevel,
        )
        SignalPortalPhase = CandidatePortalPhaseBySignal[Signal]
        PortalSeed = ReservedPortalSeedBySignal.get(Signal)
        PortalSeedPending = PortalSeed is not None
        UnreservedPerLayerRequestLimit = max(
            1,
            ceil(InitialRequestLimit / LayerCount),
        )
        LayerOrder = tuple(range(LayerCount))
        if CoarsePlan is not None:
            PlannedLayer = CoarsePlan.Layers[Signal]
            LayerOrder = (PlannedLayer,) + tuple(
                Layer for Layer in LayerOrder if Layer != PlannedLayer
            )
        for Layer in LayerOrder:
            LayerPriority = 0 if Layer == LayerOrder[0] else 1
            SourcePortals = Portals[(Signal, Profile.Root, Layer)]
            TargetPortalSets = [Portals[(Signal, Target, Layer)] for Target in Profile.Targets]
            if not SourcePortals or any(not Values for Values in TargetPortalSets):
                continue
            LegalPortalTuples = BuildSelfLegalPortalTuples(
                Profile,
                SourcePortals,
                TargetPortalSets,
            )
            if not LegalPortalTuples:
                continue
            RoutingY = Technology.RoutingY(MinimumY, Layer)
            PhysicalPortalVariantCount = min(
                RoutePortalVariantCounts[Signal],
                len(LegalPortalTuples),
            )
            for Variant in range(PhysicalPortalVariantCount):
                SourcePortal, *BaseTargetPortalValues = (
                    LegalPortalTuples[Variant]
                )
                BaseTargetPortals = tuple(BaseTargetPortalValues)
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
                    AxisPriority = (
                        0
                        if CoarsePlan is not None
                        and Axis == CoarsePlan.Axes[Signal]
                        else 1
                    )
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
                    LaneValues = CandidateLanes(
                        AlignedCenter,
                        RouteLaneCount,
                        Technology.TrackPitch,
                    )
                    if LaneDiversityLevel:
                        PreviousLaneCount = min(
                            RouteLaneCount,
                            Policy.AdaptiveRouting.InitialLaneCount
                            * Policy.AdaptiveRouting.LaneGrowthFactor ** (
                                LaneDiversityLevel - 1
                            ),
                        )
                        PreviousLanes = frozenset(CandidateLanes(
                            AlignedCenter,
                            PreviousLaneCount,
                            Technology.TrackPitch,
                        ))
                        # Escalation must expose lanes not present in the
                        # previous pass.  Merely increasing the lane-count
                        # ceiling while retaining the centered prefix retries
                        # exactly the same four candidates.
                        LaneValues = tuple(sorted(
                            LaneValues,
                            key=lambda Value: (
                                0 if Value not in PreviousLanes else 1,
                                abs(Value - AlignedCenter),
                                Value,
                            ),
                        ))
                    if UnreservedPortalMode and len(Profiles) <= 32:
                        LaneValues = LaneValues[:1]
                    for LaneIndex, Lane in enumerate(LaneValues):
                        PortalShapeRank = (
                            CandidatePortalShapeRank(
                                Variant,
                                AxisIndex,
                                LaneIndex,
                                Layer,
                                PhysicalPortalVariantCount,
                                len(LaneValues),
                                RequestWindowOffset + SignalPortalPhase,
                            )
                            if UnreservedPortalMode
                            else Variant
                        )
                        if (
                            UnreservedPortalMode
                            and PortalShapeRank
                            >= UnreservedPerLayerRequestLimit
                        ):
                            DeferredRouteRequestCountsBySignal[Signal] += 1
                            continue
                        if (
                            PortalSeedPending
                            and PortalSeed is not None
                            and Layer == PortalSeed[0]
                        ):
                            _SeedLayer, SourcePortal, TargetPortals = PortalSeed
                            PortalSeedPending = False
                        else:
                            PortalPhase = 1 + AxisIndex * 3 + LaneIndex
                            (
                                SourcePortal,
                                *TargetPortalValues,
                            ) = LegalPortalTuples[
                                (Variant + PortalPhase) % len(LegalPortalTuples)
                            ]
                            TargetPortals = tuple(TargetPortalValues)
                        PortalNodes = {
                            Position
                            for Portal in (SourcePortal, *TargetPortals)
                            for Position in Portal.Path
                        }
                        if PortalNodes & ForeignBlockedNodesBySignal[Signal]:
                            # Portal/access nodes are mandatory candidate
                            # ownership.  Passing one as both required and
                            # blocked makes the native request contradictory.
                            # Keep the overlap visible for diagnostics and let
                            # exact capacity-one assignment reject any actual
                            # inter-net electrical conflict.
                            ForeignPortalOverlapBySignal[Signal] += 1
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
                        RequiredNodeSet = (
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
                        if FindSelfClaimConflicts({
                            Signal: Resources.ResourceGraph.BuildRouteClaims(
                                RequiredNodeSet
                            )
                        }):
                            # Portal/access ownership is part of the route
                            # request, not a post-search suggestion. Invalid
                            # tuples must not consume the bounded detailed
                            # search window ahead of legal combinations.
                            continue
                        RequiredNodes = sorted(RequiredNodeSet)
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
                                _BuildTargetPortalBranches(
                                    TargetPortals,
                                    tuple(
                                        Profile.TargetAccessPaths[Target]
                                        for Target in Profile.Targets
                                    ),
                                ),
                                sorted(CandidateColumns),
                                RequiredNodes,
                                sorted(
                                    ForeignBlockedNodesBySignal[Signal]
                                    - RequiredNodeSet
                                ),
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
                        # Keep the initial pool geometrically diverse: one
                        # axis/lane choice is insufficient to disprove a
                        # capacity-one conflict, while a broad portal-product
                        # search is unnecessarily expensive.
                        RoutePriorities.append((
                            PortalShapeRank,
                            LayerPriority,
                            LaneIndex,
                            AxisPriority,
                            Axis,
                            Lane,
                        ))
        OrderedRequests = sorted(
            zip(RoutePriorities, RouteRequests, RouteMetadata),
            key=lambda Value: (
                Value[0][1],  # establish physical layer diversity first
                Value[0][0],  # then new portal starts
                Value[0][2],  # then lane diversity
                Value[0][3],  # then axis preference
                Value[0][4],
                Value[0][5],
            ),
        )
        UniqueOrderedRequests = []
        SeenRequestGeometry: set[tuple[object, ...]] = set()
        for Priority, Request, Metadata in OrderedRequests:
            (
                SourcePortal,
                TargetPortals,
                Guide,
                Layer,
                Axis,
                Lane,
                _Variant,
            ) = Metadata
            RequestGeometry = (
                SourcePortal.PortalId,
                tuple(Portal.PortalId for Portal in TargetPortals),
                tuple(sorted(Guide)),
                Layer,
                Axis,
                Lane,
            )
            if RequestGeometry in SeenRequestGeometry:
                continue
            SeenRequestGeometry.add(RequestGeometry)
            UniqueOrderedRequests.append((Priority, Request, Metadata))
        RouteRequests = [Value[1] for Value in UniqueOrderedRequests]
        RouteMetadata = [Value[2] for Value in UniqueOrderedRequests]
        RouteRequestsBySignal[Signal] = RouteRequests
        RouteMetadataBySignal[Signal] = RouteMetadata
        if bool(os.environ.get("RCS_DEBUG_NEGOTIATED_REQUESTS")):
            print(
                "[debug] authoritative: negotiated-route-requests",
                f"signal={Signal}",
                f"requests={len(RouteRequests)}",
                f"metadata={len(RouteMetadata)}",
            )

    if UseNegotiatedRouting:
        NegotiatedPlan = PlanNegotiatedRouteTrees(
            Context,
            Profiles,
            RouteRequestsBySignal,
            RouteMetadataBySignal,
            Region,
            ReservedAccess,
            Resources,
            Technology,
            Policy,
            Deadline,
            AdaptiveExpiresAt,
            CheckRuntimeBudget,
            RegenerateSignals=RegenerateSignals,
            SeedCandidatesBySignal=CandidatesBySignal,
        )
        MissingNegotiatedSignals = tuple(sorted(
            set(Profiles) - set(NegotiatedPlan.SelectedCandidates)
        ))
        if MissingNegotiatedSignals:
            raise RoutingStageError(RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="NegotiatedDetailedRouting",
                AffectedNets=MissingNegotiatedSignals,
                Detail=(
                    "negotiated detailed routing produced no legal route tree; "
                    "legacy candidate materialization is not a fallback"
                ),
                RepairActions=("RelocateAffectedClusters",),
                Diagnostics={
                    "MissingSignals": list(MissingNegotiatedSignals),
                    "OverflowProgression": list(
                        NegotiatedPlan.OverflowProgression
                    ),
                    **NegotiatedPlan.Diagnostics,
                },
            ))
        CandidatesBySignal = defaultdict(
            list,
            {
                Signal: [Candidate]
                for Signal, Candidate in NegotiatedPlan.SelectedCandidates.items()
            },
        )
        WorkTelemetry["NegotiatedRouting"] = {
            "Algorithm": "negotiated-route-trees-v1",
            "Iterations": len(NegotiatedPlan.Iterations),
            "OverflowProgression": list(NegotiatedPlan.OverflowProgression),
            "ReroutedSignals": list(NegotiatedPlan.ReroutedSignals),
            "CachedNodeCount": NegotiatedPlan.CachedNodeCount,
            "CachedEdgeCount": NegotiatedPlan.CachedEdgeCount,
            **NegotiatedPlan.Diagnostics,
        }

    WorkTelemetry["CandidateRequestConstructionSeconds"] = round(
        monotonic() - CandidateStarted,
        6,
    )
    CandidateRequestCount = max(
        1,
        sum(
            min(len(RouteRequestsBySignal[Signal]), InitialRequestLimit)
            for Signal in CandidateSignalOrder
        ),
    )
    WorkTelemetry["InitialCandidateRequestsPerSignal"] = InitialRequestLimit
    WorkTelemetry["InitialRouteTreeRequestCount"] = CandidateRequestCount
    WorkTelemetry["RouteTreeRequestCount"] = CandidateRequestCount
    InitialRequestsBySignal: dict[str, list[tuple[Any, ...]]] = {}
    InitialMetadataBySignal: dict[str, list[tuple[Any, ...]]] = {}
    InitialResultSlices: dict[str, tuple[int, int]] = {}
    BatchedInitialRequests: list[tuple[Any, ...]] = []
    for Signal in CandidateSignalOrder:
        if CandidatesBySignal.get(Signal):
            continue
        SignalRequests = RouteRequestsBySignal[Signal][:InitialRequestLimit]
        SignalMetadata = RouteMetadataBySignal[Signal][:InitialRequestLimit]
        StartIndex = len(BatchedInitialRequests)
        BatchedInitialRequests.extend(SignalRequests)
        InitialRequestsBySignal[Signal] = SignalRequests
        InitialMetadataBySignal[Signal] = SignalMetadata
        InitialResultSlices[Signal] = (
            StartIndex,
            len(BatchedInitialRequests),
        )
    InitialNativeBatchStarted = monotonic()
    BatchedInitialTrees = (
        GenerateRouteTreesWithDeadline(BatchedInitialRequests)
        if BatchedInitialRequests
        else []
    )
    WorkTelemetry["InitialNativeCandidateBatchSeconds"] = round(
        monotonic() - InitialNativeBatchStarted,
        6,
    )
    RouteTreeBatchCount = 1 if BatchedInitialRequests else 0
    CandidateSignalRank = {
        Signal: Index for Index, Signal in enumerate(CandidateSignalOrder)
    }

    def InitialRoutedTreeCount(Signal: str) -> int:
        if CandidatesBySignal.get(Signal):
            return len(CandidatesBySignal[Signal])
        ResultStart, ResultEnd = InitialResultSlices[Signal]
        return sum(
            Value is not None
            for Value in BatchedInitialTrees[ResultStart:ResultEnd]
        )

    # Materialize successful signals before handling a starved signal. The
    # native batch has already completed, so this preserves its reusable work
    # and lets offender-only escalation inherit every unaffected candidate.
    CandidateMaterializationOrder = sorted(
        CandidateSignalOrder,
        key=lambda Signal: (
            -InitialRoutedTreeCount(Signal),
            CandidateSignalRank[Signal],
        ),
    )
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
    for Signal in CandidateMaterializationOrder:
        if CandidatesBySignal.get(Signal):
            CandidateLimitsBySignal[Signal] = len(CandidatesBySignal[Signal])
            CandidateDiagnostics[Signal] = {
                "Cached": True,
                "Requests": 0,
                "RoutedTrees": 0,
                "Materialized": len(CandidatesBySignal[Signal]),
                "DeferredRequests": 0,
                "SourcePortals": 0,
                "TargetPortals": 0,
                "Rejections": {},
            }
            continue
        Profile = Profiles[Signal]
        CandidateExpansionLimit = CandidateExpansionLimits[Signal]
        RouteRequests = RouteRequestsBySignal[Signal]
        RouteMetadata = RouteMetadataBySignal[Signal]
        InitialRouteRequests = InitialRequestsBySignal[Signal]
        InitialRouteMetadata = InitialMetadataBySignal[Signal]
        ResultStart, ResultEnd = InitialResultSlices[Signal]
        RoutedTrees = BatchedInitialTrees[ResultStart:ResultEnd]
        RejectionCounts: Counter[str] = Counter()
        CandidateDiagnostics[Signal] = {
            "Requests": len(InitialRouteRequests),
            "RoutedTrees": sum(Value is not None for Value in RoutedTrees),
            "Materialized": 0,
            "DeferredRequests": (
                DeferredRouteRequestCountsBySignal[Signal]
                + len(RouteRequests)
                - len(InitialRouteRequests)
            ),
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
            "ForeignBlockedNodes": len(
                ForeignBlockedNodesBySignal[Signal]
            ),
            "ForeignPortalOverlapRequests": (
                ForeignPortalOverlapBySignal[Signal]
            ),
        }
        def MaterializeBatch(
            Trees: list[Any],
            MetadataValues: list[tuple[Any, ...]],
        ) -> None:
            for RoutedTree, Metadata in zip(Trees, MetadataValues):
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
                    Policy.DetailedRouting.CandidateBendWeight,
                    Policy.DetailedRouting.CandidateViaWeight,
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
                    Policy.DetailedRouting.RepeaterPenalty,
                    RejectionCounts,
                )
                if Candidate is not None:
                    CandidatesBySignal[Signal].append(Candidate)
                    CandidateDiagnostics[Signal]["Materialized"] += 1
                    CandidateAxisLaneBySignal.setdefault(
                        Signal,
                        {},
                    )[Candidate.CandidateId] = (
                        Axis,
                        Lane,
                        Layer,
                        Candidate.SeedNodeCount,
                    )

        MaterializeBatch(RoutedTrees, InitialRouteMetadata)
        PriorValues = tuple((PriorCandidateCache or {}).get(Signal, ()))
        PriorCandidateIds = frozenset(
            Candidate.CandidateId for Candidate in PriorValues
        )
        ExistingCandidateIds = {
            Candidate.CandidateId for Candidate in CandidatesBySignal[Signal]
        }
        CandidatesBySignal[Signal].extend(
            Candidate
            for Candidate in PriorValues
            if Candidate.CandidateId not in ExistingCandidateIds
        )
        CandidateDiagnostics[Signal]["PriorCandidates"] = len(PriorValues)
        # Do not exhaust a signal's deferred request product inline.  A signal
        # with no materialized candidate is regenerated through the typed,
        # offender-only escalation path below; unaffected candidate sets and
        # raw portal geometry remain reusable under the same deadline.
        CandidateDiagnostics[Signal]["Rejections"] = dict(RejectionCounts)
        if CoarsePlan is None:
            def CandidateOrder(Value: NetRouteCandidate) -> tuple[Any, ...]:
                return (
                    Value.MaterialCost,
                    Value.FootprintGrowth,
                    -CandidateAxisLaneBySignal[Signal][Value.CandidateId][3],
                    Value.IncrementalMaterialCost,
                    Value.IncrementalLength,
                    Value.Length,
                    Value.BendCount,
                    Value.ViaCount,
                    Value.CandidateId,
                )
        else:
            PlannedAxis = CoarsePlan.Axes[Signal]
            PlannedLane = CoarsePlan.Lanes[Signal]
            PlannedLayer = CoarsePlan.Layers[Signal]

            def CandidateOrder(Value: NetRouteCandidate) -> tuple[Any, ...]:
                CandidateAxis, CandidateLane, CandidateLayer, SeedNodes = (
                    CandidateAxisLaneBySignal[Signal][Value.CandidateId]
                )
                return (
                    Value.MaterialCost,
                    0 if CandidateAxis == PlannedAxis else 1,
                    0 if CandidateLane == PlannedLane else 1,
                    0 if CandidateLayer == PlannedLayer else 1,
                    Value.FootprintGrowth,
                    -SeedNodes,
                    Value.IncrementalMaterialCost,
                    Value.IncrementalLength,
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
                min(
                    MaximumCandidates * max(1, WorkScale),
                    Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
                ),
            )
        else:
            SignalMaximumCandidates = min(
                len(CandidatesBySignal[Signal]),
                Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
            )
        CandidateLimitsBySignal[Signal] = SignalMaximumCandidates
        PerLayer = max(1, SignalMaximumCandidates // LayerCount)
        DiverseCandidates = []
        if UnreservedPortalMode:
            # Retain every materialized candidate from the bounded batch.
            # Escalation widens the batch or changes lanes/layers under the
            # same absolute deadline instead of front-loading the full portal
            # product.
            for Layer in range(LayerCount):
                LayerTracks = sorted(
                    (
                        Values
                        for (TrackLayer, _Guide), Values in CandidatesByTrack.items()
                        if TrackLayer == Layer
                    ),
                    key=lambda Values: CandidateOrder(Values[0]),
                )
                for Values in LayerTracks:
                    DiverseCandidates.extend(Values)
        else:
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
        CandidatesBySignal[Signal] = SelectBoundedDiverseCandidatePool(
            DiverseCandidates,
            SignalMaximumCandidates,
            PriorCandidateIds,
        )
        if not CandidatesBySignal[Signal]:
            Rejections = CandidateDiagnostics[Signal].get("Rejections", {})
            RoutedTreeCount = int(
                CandidateDiagnostics[Signal].get("RoutedTrees", 0)
            )
            SeedRejectedEveryRoutedTree = bool(
                PlacementWasRelocated
                and LocalClaimsBySignal.get(Signal)
                and RoutedTreeCount > 0
                and sum(
                    int(Rejections.get(Reason, 0))
                    for Reason in ("SelfClaimConflict", "NoRepeater")
                )
                >= RoutedTreeCount
            )
            CandidateFailureFingerprint = BuildStableFingerprint({
                "Signal": Signal,
                "Diagnostics": CandidateDiagnostics[Signal],
                "PortalReservations": [
                    Value.ToDictionary() for Value in PortalReservations
                ],
                "LayerCount": LayerCount,
                "LaneCount": RouteLaneCount,
            })

            def RetryCandidateGeneration(
                Action: str,
                *,
                NextReservationVariant: int = ReservationVariant,
                NextLaneDiversityLevel: int = LaneDiversityLevel,
                NextCandidateDiversityLevel: int = CandidateDiversityLevel,
                NextLayerFloor: int | None = AdaptiveLayerFloor,
                NextSkipStrictPortalReservation: bool = (
                    SkipStrictPortalReservation
                ),
            ) -> RoutedDesign:
                if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                    print(
                        "[debug] authoritative: retrying candidate generation "
                        f"signal={Signal} action={Action} "
                        f"reservation_variant={NextReservationVariant} "
                        f"candidate_diversity={NextCandidateDiversityLevel} "
                        f"lane_diversity={NextLaneDiversityLevel} "
                        f"unreserved={NextSkipStrictPortalReservation} "
                        f"elapsed={monotonic() - RoutingStarted:.3f}",
                        flush=True,
                    )
                PreserveUnaffected = Action in {
                    "regenerate-affected-candidates",
                    "increase-guide-lane-diversity",
                    "add-routing-layer",
                }
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        frozenset({Signal}),
                    )
                    if PreserveUnaffected
                    else ({}, {})
                )
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
                    AdaptiveLayerFloor=NextLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(
                        *EscalationHistory,
                        {
                            "Stage": "CandidateGeneration",
                            "Action": Action,
                            "AffectedSignals": [Signal],
                            "CandidateFailureFingerprint": (
                                CandidateFailureFingerprint
                            ),
                            "Diagnostics": CandidateDiagnostics[Signal],
                        },
                    ),
                    ReservationVariant=NextReservationVariant,
                    LaneDiversityLevel=NextLaneDiversityLevel,
                    CandidateDiversityLevel=NextCandidateDiversityLevel,
                    SkipStrictPortalReservation=(
                        NextSkipStrictPortalReservation
                    ),
                    EscalationStates=EscalationStates,
                    Deadline=Deadline,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    PriorCandidateCache=(
                        {
                            Signal: tuple(CandidatesBySignal[Signal])
                        }
                        if Action in {
                            "increase-guide-lane-diversity",
                            "add-routing-layer",
                        }
                        and CandidatesBySignal.get(Signal)
                        else None
                    ),
                    PriorCandidateMetadata=(
                        {
                            Signal: dict(
                                CandidateAxisLaneBySignal.get(Signal, {})
                            )
                        }
                        if Action in {
                            "increase-guide-lane-diversity",
                            "add-routing-layer",
                        }
                        and CandidatesBySignal.get(Signal)
                        else None
                    ),
                    RegenerateSignals=(
                        frozenset({Signal})
                        if PreserveUnaffected
                        else frozenset()
                    ),
                    RawPortalCache=(
                        EffectiveRawPortalCache
                        if NextLayerFloor is None
                        or NextLayerFloor <= LayerCount
                        else None
                    ),
                    PreparedPortalCache=(
                        EffectivePreparedPortalCache
                        if (
                            (NextLayerFloor is None or NextLayerFloor <= LayerCount)
                            and NextReservationVariant == ReservationVariant
                            and NextSkipStrictPortalReservation
                            == SkipStrictPortalReservation
                        )
                        else None
                    ),
                )

            if (
                Policy.AdaptiveRouting.Enabled
                and CandidateDiagnostics[Signal]["DeferredRequests"] > 0
                and CandidateDiversityLevel + 1
                < Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations
                and not PlacementWasRelocated
            ):
                return RetryCandidateGeneration(
                    "regenerate-affected-candidates",
                    NextCandidateDiversityLevel=CandidateDiversityLevel + 1,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and (
                    CandidateDiversityLevel + 1
                    >= Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations
                    or (
                        PlacementWasRelocated
                        and (
                            not AllowRelocatedStarvationLaneRetry
                            or LaneDiversityLevel >= 1
                        )
                    )
                    or (
                        CandidateDiagnostics[Signal]["DeferredRequests"] == 0
                        and "__PlacementRelocation__"
                        in (
                            getattr(Placed, "LocalRouteDiagnostics", {}) or {}
                        )
                    )
                )
                and (
                    LocalClaimsBySignal.get(Signal)
                    or "__PlacementRelocation__"
                    in (getattr(Placed, "LocalRouteDiagnostics", {}) or {})
                )
            ):
                ConflictGraph = {
                    "Classification": "candidate-starvation-placement-conflict",
                    "ConflictSignals": [Signal],
                    "NoCandidateSignals": [Signal],
                }
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="Candidate",
                        AffectedNets=(Signal,),
                        RepairActions=(
                            "RelocateAffectedClusters",
                            "AdvancePlacementCandidate",
                        ),
                        Detail=(
                            "the fixed placement exhausted bounded candidate "
                            "diversity for one signal"
                        ),
                        Diagnostics={
                            "Action": "advance-placement-candidate-starvation",
                            "ConflictGraph": ConflictGraph,
                            "CandidateDiagnostics": CandidateDiagnostics[Signal],
                            "CandidateFailureFingerprint": (
                                CandidateFailureFingerprint
                            ),
                            "EscalationHistory": tuple(EscalationHistory),
                            "Deadline": Deadline.ToDictionary(),
                        },
                    )
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and not SkipStrictPortalReservation
                and ReservationVariant + 1
                < Policy.AdaptiveRouting.MaximumPortalReservationAlternatives
            ):
                return RetryCandidateGeneration(
                    "alternate-portal-slots",
                    NextReservationVariant=ReservationVariant + 1,
                    NextCandidateDiversityLevel=0,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and not SkipStrictPortalReservation
            ):
                return RetryCandidateGeneration(
                    "try-bounded-unreserved-portals",
                    NextCandidateDiversityLevel=0,
                    NextSkipStrictPortalReservation=True,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and LaneDiversityLevel + 1
                < Policy.AdaptiveRouting.MaximumLaneDiversityEscalations
                and not SeedRejectedEveryRoutedTree
                and (
                    not PlacementWasRelocated
                    or AllowRelocatedStarvationLaneRetry
                )
            ):
                return RetryCandidateGeneration(
                    "increase-guide-lane-diversity",
                    NextLaneDiversityLevel=LaneDiversityLevel + 1,
                    NextCandidateDiversityLevel=CandidateDiversityLevel,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and LayerCount < EffectiveMaximumLayerCount
            ):
                return RetryCandidateGeneration(
                    "add-routing-layer",
                    NextReservationVariant=0,
                    NextLaneDiversityLevel=0,
                    NextCandidateDiversityLevel=0,
                    NextLayerFloor=LayerCount + 1,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and not SkipStrictPortalReservation
            ):
                return RetryCandidateGeneration(
                    "final-bounded-unreserved-portals",
                    NextReservationVariant=0,
                    NextLaneDiversityLevel=0,
                    NextCandidateDiversityLevel=0,
                    NextSkipStrictPortalReservation=True,
                )
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: candidate generation failed to materialize "
                    f"signal={Signal} reason={CandidateDiagnostics[Signal]}",
                    flush=True,
                )
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
                    Diagnostics={
                        "CandidateDiagnostics": CandidateDiagnostics[Signal],
                        "CandidateFailureFingerprint": CandidateFailureFingerprint,
                        "EscalationHistory": tuple(EscalationHistory),
                        "Deadline": Deadline.ToDictionary(),
                    },
                )
            )
        CheckRuntimeBudget("Candidate")

    StageTimings["CandidateGeneration"] = monotonic() - CandidateStarted
    WorkTelemetry["RouteTreeRequestCount"] = CandidateRequestCount
    if ProgressCallback is not None:
        ProgressCallback(4, StageCount)
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
        print(
            "[debug] authoritative: candidate generation complete "
            f"signals={len(CandidatesBySignal)} batches={RouteTreeBatchCount} "
            f"request_construction="
            f"{WorkTelemetry['CandidateRequestConstructionSeconds']} "
            f"initial_native="
            f"{WorkTelemetry['InitialNativeCandidateBatchSeconds']} "
            f"total={monotonic() - CandidateStarted:.3f}",
            flush=True,
        )
        for Signal in sorted(CandidatesBySignal):
            Diagnostics = CandidateDiagnostics[Signal]
            print(
                "[debug] authoritative: signal diagnostics "
                f"signal={Signal} requests={Diagnostics['Requests']} "
                f"routed={Diagnostics['RoutedTrees']} materialized={Diagnostics['Materialized']} "
                f"source_portals={Diagnostics['SourcePortals']} "
                f"target_portals={Diagnostics['TargetPortals']} "
                f"self_conflict={Diagnostics.get('Rejections', {}).get('SelfClaimConflict', 0)} "
                f"disconnected={Diagnostics.get('Rejections', {}).get('Disconnected', 0)} "
                f"no_repeater={Diagnostics.get('Rejections', {}).get('NoRepeater', 0)} "
                f"limit={CandidateLimitsBySignal[Signal]} "
                f"final={len(CandidatesBySignal[Signal])}",
                flush=True,
            )

    CandidateLookup = {
        Candidate.CandidateId: Candidate
        for Values in CandidatesBySignal.values()
        for Candidate in Values
    }
    RuntimeEscalationState = replace(
        RuntimeEscalationState,
        CandidateFingerprint=BuildStableFingerprint({
            Signal: [
                Candidate.CandidateId
                for Candidate in CandidatesBySignal[Signal]
            ]
            for Signal in sorted(CandidatesBySignal)
        }),
    )
    BaseLocalClaims = LocalClaims
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
                    Candidate.Length * 10_000
                    + Candidate.BendCount * 100
                    + Candidate.ViaCount
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
        LayerCount - AdaptiveBudget.LayerCount,
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
    RuntimeEscalationState = replace(
        RuntimeEscalationState,
        AssignmentBudget=AssignmentExpansionLimit,
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
            Arguments = (
                Values,
                BaseValues,
                len(AssignmentIndexed.ResourcePositions),
                AssignmentExpansionLimit,
            )
            if hasattr(Context, "PlanAuthoritativeRoutesWithBaseBounded"):
                return Context.PlanAuthoritativeRoutesWithBaseBounded(
                    *Arguments,
                    RemainingRoutingRuntimeMilliseconds(
                        Deadline,
                        AdaptiveExpiresAt,
                    ),
                )
            try:
                return Context.PlanAuthoritativeRoutesWithBase(
                    *Arguments,
                    RemainingRoutingRuntimeMilliseconds(
                        Deadline,
                        AdaptiveExpiresAt,
                    ) / 1000.0,
                )
            except TypeError as Error:
                if "positional arguments" not in str(Error):
                    raise
                return Context.PlanAuthoritativeRoutesWithBase(*Arguments)
        Arguments = (
            Values,
            len(AssignmentIndexed.ResourcePositions),
            AssignmentExpansionLimit,
        )
        if hasattr(Context, "PlanAuthoritativeRoutesBounded"):
            return Context.PlanAuthoritativeRoutesBounded(
                *Arguments,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ),
            )
        try:
            return Context.PlanAuthoritativeRoutes(
                *Arguments,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ) / 1000.0,
            )
        except TypeError as Error:
            if "positional arguments" not in str(Error):
                raise
            return Context.PlanAuthoritativeRoutes(*Arguments)

    def RaiseForNativeAssignmentDeadline(Result: Any) -> None:
        if not getattr(Result, "DeadlineExceeded", False):
            return
        EnforceRoutingRuntimeLimit(
            Deadline=Deadline,
            AdaptiveStartedAt=RoutingStarted,
            AdaptiveExpiresAt=AdaptiveExpiresAt,
            Stage="TrackAssignment",
            Diagnostics=CurrentRuntimeBudgetDiagnostics({
                "CompletedWork": getattr(Result, "CompletedWork", 0),
            }),
            NativeDeadlineExceeded=True,
        )

    AssignmentStarted = monotonic()
    LayerCappedAssignmentAttempts: list[dict[str, int | bool]] = []
    Result = None
    if Policy.TrackAssignment.MinimizeMaximumRoutingLayer:
        MinimumFeasibleLayer = max(
            min(Candidate.Layer for Candidate in Values)
            for Values in CandidatesBySignal.values()
        )
        MaximumCandidateLayer = max(
            Candidate.Layer
            for Values in CandidatesBySignal.values()
            for Candidate in Values
        )
        for MaximumAssignedLayer in range(
            MinimumFeasibleLayer,
            MaximumCandidateLayer + 1,
        ):
            LayerCappedValues = EncodeCandidateValues({
                Signal: [
                    Candidate
                    for Candidate in Values
                    if Candidate.Layer <= MaximumAssignedLayer
                ]
                for Signal, Values in CandidatesBySignal.items()
            })
            # A lower ceiling which removes every candidate for one signal is
            # not an assignment attempt; move directly to the next ceiling.
            if not LayerCappedValues or any(
                not any(Value[0] == Signal for Value in LayerCappedValues)
                for Signal in CandidatesBySignal
            ):
                continue
            Result = PlanAssignment(LayerCappedValues)
            RaiseForNativeAssignmentDeadline(Result)
            LayerCappedAssignmentAttempts.append({
                "MaximumAssignedLayer": MaximumAssignedLayer,
                "Success": bool(Result.Success),
                "ExpansionCount": int(Result.ExpansionCount),
            })
            if Result.Success:
                break
            # An exhausted bounded search cannot establish that the current
            # ceiling is infeasible.  Preserve the established unrestricted
            # recovery path rather than rejecting a legal compact route.
            if ShouldGrowAssignmentBudget(Result):
                Result = None
                break
    if Result is None:
        Result = PlanAssignment()
        RaiseForNativeAssignmentDeadline(Result)
    if Policy.AdaptiveRouting.Enabled:
        while (
            not Result.Success
            and ShouldGrowAssignmentBudget(Result)
            and AssignmentExpansionLimit < AdaptiveBudget.AssignmentExpansions
            and not (
                PlacementWasRelocated
                and LaneDiversityLevel >= 1
            )
        ):
            NextAssignmentExpansionLimit = GrowAssignmentExpansionLimit(
                AssignmentExpansionLimit,
                AdaptiveBudget.AssignmentExpansions,
                Policy.AdaptiveRouting.AssignmentGrowthFactor,
            )
            if NextAssignmentExpansionLimit <= AssignmentExpansionLimit:
                break
            AssignmentRetryHistory.append({
                "Stage": "TrackAssignment",
                "Action": "increase-assignment-budget",
                "FromAssignmentExpansionLimit": AssignmentExpansionLimit,
                "ToAssignmentExpansionLimit": NextAssignmentExpansionLimit,
                "ExactExpansions": Result.ExpansionCount,
                "Reason": "exact capacity-one assignment exhausted current budget",
            })
            AssignmentExpansionLimit = NextAssignmentExpansionLimit
            RuntimeEscalationState = replace(
                RuntimeEscalationState,
                AssignmentBudget=AssignmentExpansionLimit,
            )
            CheckRuntimeBudget("Track")
            Result = PlanAssignment()
            RaiseForNativeAssignmentDeadline(Result)
    # Assignment work is intentionally not the first escalation lever.  A
    # bounded physical change (portal, lane, layer, placement) must happen
    # before spending more time on the identical candidate geometry.
    CheckRuntimeBudget("Track")
    StageTimings["Assignment"] = monotonic() - AssignmentStarted
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
        print(
            f"[debug] authoritative: assignment result success={Result.Success} "
            f"expansions={Result.ExpansionCount} budget_exhausted={ShouldGrowAssignmentBudget(Result)}",
            flush=True,
        )
    if not Result.Success:
        if Policy.AdaptiveRouting.Enabled:
            ConflictGraph = BuildRoutingConflictGraph(
                CandidatesBySignal,
                Result,
                AssignmentIndexed.ResourcePositions,
                PortalReservations,
                WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
                    "ConflictClassification",
                    Diagnostics,
                ),
            )
            StackedConflictPairs = tuple(
                tuple(Pair)
                for Pair in ConflictGraph["PairwiseIncompatibleEdges"]
                if len(Pair) == 2
                and Pair[0] in Profiles
                and Pair[1] in Profiles
                and (
                    Profiles[Pair[0]].Root[0],
                    Profiles[Pair[0]].Root[2],
                )
                == (
                    Profiles[Pair[1]].Root[0],
                    Profiles[Pair[1]].Root[2],
                )
                and Profiles[Pair[0]].Root[1]
                != Profiles[Pair[1]].Root[1]
            )
            HasPlacementRelocation = PlacementWasRelocated
            if StackedConflictPairs:
                ConflictGraph["Classification"] = (
                    "stacked-placement-conflict"
                )
                ConflictGraph["StackedConflictPairs"] = (
                    StackedConflictPairs
                )
            elif (
                ConflictGraph["Classification"]
                == "higher-order-placement-conflict"
                and HasPlacementRelocation
                and len(ConflictGraph["PairwiseIncompatibleEdges"]) == 1
            ):
                ConflictGraph["Classification"] = (
                    "relocated-pairwise-incompatibility"
                )
                ConflictGraph["ConflictSignals"] = list(
                    ConflictGraph["PairwiseIncompatibleEdges"][0]
                )
            elif (
                ConflictGraph["Classification"]
                == "higher-order-placement-conflict"
                and HasPlacementRelocation
            ):
                ConflictGraph["Classification"] = (
                    "relocated-higher-order-conflict"
                )
            elif (
                ConflictGraph["Classification"]
                == "pairwise-incompatibility"
                and len(ConflictGraph["PairwiseIncompatibleEdges"]) >= 2
            ):
                ConflictGraph["Classification"] = (
                    "relocated-multi-pair-conflict"
                    if HasPlacementRelocation
                    else "multi-pair-placement-conflict"
                )
                ConflictGraph["ConflictSignals"] = sorted({
                    Signal
                    for Pair in ConflictGraph["PairwiseIncompatibleEdges"]
                    for Signal in Pair
                })
            elif (
                HasPlacementRelocation
                and ConflictGraph["Classification"]
                == "larger-matching-failure"
            ):
                ConflictGraph["Classification"] = (
                    "relocated-larger-matching-failure"
                )
            elif (
                HasPlacementRelocation
                and ConflictGraph["Classification"]
                == "pairwise-incompatibility"
            ):
                ConflictGraph["Classification"] = (
                    "relocated-pairwise-incompatibility"
                )
            ConflictGraph["RelocationSignals"] = (
                SelectPlacementRelocationSignals(ConflictGraph)
            )
            HasPairwiseIncompatibility = bool(
                ConflictGraph["PairwiseIncompatibleEdges"]
            )
            AffectedCandidateSignals = frozenset(
                ConflictGraph.get("ConflictSignals", ())
            )
            if not AffectedCandidateSignals:
                AffectedCandidateSignals = frozenset(CandidatesBySignal)
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: conflict graph classification="
                    f"{ConflictGraph['Classification']} "
                    f"portal_mode={'unreserved' if UnreservedPortalMode else 'reserved'} "
                    f"reservation_variant={ReservationVariant} "
                    f"lane_diversity={LaneDiversityLevel} "
                    f"layers={LayerCount} "
                    f"pairwise={HasPairwiseIncompatibility} "
                    f"edges={ConflictGraph['PairwiseIncompatibleEdges'][:4]} "
                    f"runtime_left={AdaptiveBudget.RuntimeSeconds - (monotonic() - RoutingStarted):.3f}",
                    flush=True,
                )
                if ConflictGraph["PairwiseIncompatibleEdges"]:
                    FirstSignal, SecondSignal = (
                        ConflictGraph["PairwiseIncompatibleEdges"][0]
                    )
                    ConflictLocations: Counter[Position3] = Counter()
                    for FirstCandidate in CandidatesBySignal[FirstSignal]:
                        for SecondCandidate in CandidatesBySignal[SecondSignal]:
                            FirstClaims = FirstCandidate.Claims
                            SecondClaims = SecondCandidate.Claims
                            Locations = (
                                FirstClaims.WireCells & SecondClaims.WireCells
                                | FirstClaims.RequiredAirCells
                                & SecondClaims.WireCells
                                | SecondClaims.RequiredAirCells
                                & FirstClaims.WireCells
                                | FirstClaims.ElectricalCells
                                & SecondClaims.WireCells
                                | SecondClaims.ElectricalCells
                                & FirstClaims.WireCells
                            )
                            ConflictLocations.update(Locations)
                    print(
                        "[debug] authoritative: first pair details "
                        f"signals=({FirstSignal},{SecondSignal}) "
                        f"candidate_counts=("
                        f"{len(CandidatesBySignal[FirstSignal])},"
                        f"{len(CandidatesBySignal[SecondSignal])}) "
                        f"roots=({Profiles[FirstSignal].Root},"
                        f"{Profiles[SecondSignal].Root}) "
                        f"access=({Profiles[FirstSignal].SourceAccessPath},"
                        f"{Profiles[SecondSignal].SourceAccessPath}) "
                        f"target_access=("
                        f"{Profiles[FirstSignal].TargetAccessPaths},"
                        f"{Profiles[SecondSignal].TargetAccessPaths}) "
                        f"coarse=("
                        f"{(CoarsePlan.Layers[FirstSignal], CoarsePlan.Axes[FirstSignal], CoarsePlan.Lanes[FirstSignal]) if CoarsePlan is not None else None},"
                        f"{(CoarsePlan.Layers[SecondSignal], CoarsePlan.Axes[SecondSignal], CoarsePlan.Lanes[SecondSignal]) if CoarsePlan is not None else None}) "
                        f"tracks=("
                        f"{sorted({CandidateAxisLaneBySignal[FirstSignal][Value.CandidateId][:3] for Value in CandidatesBySignal[FirstSignal]})},"
                        f"{sorted({CandidateAxisLaneBySignal[SecondSignal][Value.CandidateId][:3] for Value in CandidatesBySignal[SecondSignal]})}) "
                        f"hotspots={ConflictLocations.most_common(6)}",
                        flush=True,
                    )
        else:
            ConflictGraph = {
                "Classification": "complete candidate set assignment failure",
                "ConflictSignals": tuple(
                    Signal for Signal, Candidates in CandidatesBySignal.items()
                    if not Candidates
                ),
                "PairwiseIncompatibleEdges": (),
                "NoCandidateSignals": [
                    Signal
                    for Signal, Candidates in CandidatesBySignal.items()
                    if not Candidates
                ],
                "ResourceHotspots": [],
                "PortalReservations": [
                    Value.ToDictionary() for Value in PortalReservations
                ],
            }
            HasPairwiseIncompatibility = False
        CandidateFingerprint = BuildStableFingerprint({
            Signal: [
                Candidate.CandidateId
                for Candidate in CandidatesBySignal[Signal]
            ]
            for Signal in sorted(CandidatesBySignal)
        })
        ConflictFingerprint = BuildStableFingerprint(ConflictGraph)
        EffectiveState = RoutingEscalationState(
            PortalMode=("unreserved" if UnreservedPortalMode else "reserved"),
            ReservationVariant=ReservationVariant,
            LaneDiversityLevel=LaneDiversityLevel,
            CandidateDiversityLevel=CandidateDiversityLevel,
            EffectiveRoutingLayers=LayerCount,
            AssignmentBudget=AssignmentExpansionLimit,
            CandidateFingerprint=CandidateFingerprint,
            ConflictFingerprint=ConflictFingerprint,
        )
        EffectiveWorkFingerprint = BuildStableFingerprint({
            "PortalMode": EffectiveState.PortalMode,
            "PortalReservations": [
                Value.ToDictionary() for Value in PortalReservations
            ],
            "LaneCount": RouteLaneCount,
            "LayerCount": LayerCount,
            "AssignmentBudget": AssignmentExpansionLimit,
            "CandidateFingerprint": CandidateFingerprint,
            "ConflictFingerprint": ConflictFingerprint,
        })
        PriorWorkFingerprints = {
            Entry.get("EffectiveWorkFingerprint")
            for Entry in EscalationHistory
            if isinstance(Entry, dict)
        }
        if EffectiveWorkFingerprint in PriorWorkFingerprints:
            RepeatedTransition = ChooseRepeatedWorkTransition(
                UnreservedPortalMode,
                Deadline,
            )
            if RepeatedTransition.Action == "TryUnreservedPortals":
                NoOpReservationEscalation = {
                    "Stage": "TrackAssignment",
                    "Action": "try-bounded-unreserved-portals",
                    "Reason": (
                        "reserved portal retry reproduced identical effective "
                        "work; advance once to the complete generated portal set"
                    ),
                    "FromPortalMode": "reserved",
                    "ToPortalMode": "unreserved",
                    "ReservationVariant": ReservationVariant,
                    "RoutingEscalationState": EffectiveState.ToDictionary(),
                    "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                    "CandidateFingerprint": CandidateFingerprint,
                    "ConflictFingerprint": ConflictFingerprint,
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
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(
                        *EscalationHistory,
                        NoOpReservationEscalation,
                    ),
                    ReservationVariant=ReservationVariant,
                    LaneDiversityLevel=LaneDiversityLevel,
                    CandidateDiversityLevel=CandidateDiversityLevel,
                    SkipStrictPortalReservation=(
                        RepeatedTransition.SkipStrictPortalReservation
                    ),
                    EscalationStates=EscalationStates,
                    Deadline=RepeatedTransition.Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                )
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.Stagnated,
                    Stage="TrackAssignment",
                    AffectedNets=tuple(ConflictGraph.get("ConflictSignals", ())),
                    Detail=(
                        "routing escalation reproduced the same portals, "
                        "candidates, conflicts, layers, and assignment budget"
                    ),
                    Diagnostics={
                        "EscalationHistory": tuple(EscalationHistory),
                        "RoutingEscalationState": EffectiveState.ToDictionary(),
                        "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                        "CandidateFingerprint": CandidateFingerprint,
                        "ConflictFingerprint": ConflictFingerprint,
                        "ConflictGraph": ConflictGraph,
                    },
                )
            )
        MaximumLayerCount = (
            min(Technology.MaximumRoutableLayerCount, PolicyLayerLimit)
            if PolicyLayerLimit > 0
            else Technology.MaximumRoutableLayerCount
        )
        PairwiseConflictAttempts = sum(
            1
            for EscalationEntry in EscalationHistory
            if isinstance(EscalationEntry, dict)
            and EscalationEntry.get("Stage") == "TrackAssignment"
            and EscalationEntry.get("ConflictClassification")
            == "pairwise-incompatibility"
        )
        if (
            bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE"))
            and Policy.AdaptiveRouting.Enabled
            and ConflictGraph["Classification"] == "pairwise-incompatibility"
            and ConflictGraph["PairwiseIncompatibleEdges"]
            and PairwiseConflictAttempts >= 6
        ):
            print(
                "[debug] authoritative: pairwise conflict attempt budget reached; "
                f"continuing adaptive escalation (attempts={PairwiseConflictAttempts})",
                flush=True,
            )
        if monotonic() - RoutingStarted < AdaptiveBudget.RuntimeSeconds:
            FailureKind = (
                "assignment work budget remained exhausted"
                if ShouldGrowAssignmentBudget(Result)
                else "complete candidate set has no capacity-one assignment"
            )
            Escalation = {
                "Stage": "TrackAssignment",
                "AssignmentExpansions": Result.ExpansionCount,
                "ExactExpansions": Result.ExpansionCount,
                "BudgetExhausted": ShouldGrowAssignmentBudget(Result),
                "FailureNet": Result.FailureNet,
                "Reason": FailureKind,
                "ConflictClassification": ConflictGraph["Classification"],
                "PairwiseIncompatible": HasPairwiseIncompatibility,
                "RoutingEscalationState": EffectiveState.ToDictionary(),
                "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                "CandidateFingerprint": CandidateFingerprint,
                "ConflictFingerprint": ConflictFingerprint,
            }
            EscalationDecision = ChooseRoutingEscalationAction(
                Classification=ConflictGraph["Classification"],
                BudgetExhausted=bool(
                    getattr(Result, "BudgetExhausted", False)
                ),
                State=EffectiveState,
                MaximumAssignmentBudget=AdaptiveBudget.AssignmentExpansions,
                MaximumReservationVariants=(
                    Policy.AdaptiveRouting.MaximumPortalReservationAlternatives
                    if Policy.AdaptiveRouting.Enabled
                    else ReservationVariant + 1
                ),
                MaximumLaneDiversityLevels=(
                    (
                        Policy.AdaptiveRouting.MaximumLaneDiversityEscalations
                        if (
                            not PlacementWasRelocated
                            or PlacementWasBroadlyRelocated
                            or ConflictGraph["Classification"]
                            == "relocated-pairwise-incompatibility"
                        )
                        else min(
                            2,
                            Policy.AdaptiveRouting.MaximumLaneDiversityEscalations,
                        )
                    )
                    if Policy.AdaptiveRouting.Enabled
                    else LaneDiversityLevel + 1
                ),
                MaximumCandidateDiversityLevels=(
                    Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations
                    if Policy.AdaptiveRouting.Enabled
                    else CandidateDiversityLevel + 1
                ),
                MaximumEffectiveRoutingLayers=(
                    EffectiveMaximumLayerCount
                    if Policy.AdaptiveRouting.Enabled
                    else LayerCount
                ),
            )
            Escalation["Decision"] = EscalationDecision.Action
            Escalation["DecisionReason"] = EscalationDecision.Reason
            AdaptiveElapsedSeconds = monotonic() - RoutingStarted
            AdaptiveRemainingSeconds = max(
                0.0,
                AdaptiveBudget.RuntimeSeconds - AdaptiveElapsedSeconds,
            )
            ObservedPassSeconds = monotonic() - RoutingCallStarted
            if (
                EscalationDecision.Action != "AdvancePlacement"
                and not HasAdaptiveEscalationBudget(
                    AdaptiveRemainingSeconds,
                    ObservedPassSeconds,
                    bool(EscalationHistory),
                )
            ):
                Escalation.update({
                    "Action": "advance-placement-insufficient-adaptive-slice",
                    "AdaptiveElapsedSeconds": round(
                        AdaptiveElapsedSeconds,
                        6,
                    ),
                    "AdaptiveRemainingSeconds": round(
                        AdaptiveRemainingSeconds,
                        6,
                    ),
                    "ObservedPassSeconds": round(ObservedPassSeconds, 6),
                })
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="TrackAssignment",
                        AffectedNets=tuple(
                            sorted(ConflictGraph.get("ConflictSignals", ()))
                        ),
                        Detail=(
                            "the next routing control pass cannot fit the "
                            "remaining per-placement escalation slice"
                        ),
                        RepairActions=("AdvancePlacementCandidate",),
                        Diagnostics={
                            "EscalationHistory": tuple(
                                (*EscalationHistory, Escalation)
                            ),
                            "ConflictGraph": ConflictGraph,
                            "RoutingEscalationState": (
                                EffectiveState.ToDictionary()
                            ),
                            "EffectiveWorkFingerprint": (
                                EffectiveWorkFingerprint
                            ),
                        },
                    )
                )
            if EscalationDecision.Action == "AdvancePlacement":
                Escalation["Action"] = (
                    "advance-placement-conflict-relocation"
                    if ConflictGraph["Classification"]
                    in {
                        "higher-order-placement-conflict",
                        "multi-pair-placement-conflict",
                        "relocated-higher-order-conflict",
                        "relocated-larger-matching-failure",
                        "relocated-multi-pair-conflict",
                        "relocated-pairwise-incompatibility",
                        "stacked-placement-conflict",
                    }
                    else "advance-placement"
                )
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="TrackAssignment",
                        AffectedNets=tuple(
                            sorted(ConflictGraph.get("ConflictSignals", ()))
                        ),
                        RepairActions=(
                            "RelocateAffectedClusters",
                            "AdvancePlacementCandidate",
                        ),
                        Detail=(
                            f"{EscalationDecision.Reason}; the current placement "
                            "has no remaining meaningful routing control change"
                        ),
                        Diagnostics={
                            "EscalationHistory": tuple(
                                (*EscalationHistory, Escalation)
                            ),
                            "ConflictGraph": ConflictGraph,
                            "RoutingEscalationState": EffectiveState.ToDictionary(),
                            "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                        },
                    )
                )
            if (
                EscalationDecision.Action == "RegenerateAffectedCandidates"
            ):
                AffectedSignals = frozenset(
                    ConflictGraph.get("NoCandidateSignals", ())
                )
                if not AffectedSignals:
                    AffectedSignals = frozenset(
                        ConflictGraph.get("ConflictSignals", ())
                    )
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedSignals,
                    )
                )
                Escalation.update({
                    "Action": "regenerate-affected-candidates",
                    "AffectedSignals": sorted(AffectedSignals),
                    "FromCandidateDiversityLevel": CandidateDiversityLevel,
                    "ToCandidateDiversityLevel": CandidateDiversityLevel + 1,
                })
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
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=ReservationVariant,
                    LaneDiversityLevel=LaneDiversityLevel,
                    CandidateDiversityLevel=CandidateDiversityLevel + 1,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=SkipStrictPortalReservation,
                    Deadline=Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    RegenerateSignals=AffectedSignals,
                    PreparedPortalCache=EffectivePreparedPortalCache,
                )
            if (
                EscalationDecision.Action == "ChangePortalReservation"
            ):
                LocalizePortalRegeneration = not LocalClaims
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedCandidateSignals,
                    )
                    if LocalizePortalRegeneration
                    else ({}, {})
                )
                Escalation.update({
                    "Action": "alternate-portal-slots",
                    "AffectedSignals": sorted(AffectedCandidateSignals),
                    "LocalizedCandidateRegeneration": (
                        LocalizePortalRegeneration
                    ),
                    "FromReservationVariant": ReservationVariant,
                    "ToReservationVariant": ReservationVariant + 1,
                })
                return RouteAuthoritativeResources(
                    Placed, Resources, SearchMarginX, SearchMarginZ,
                    MaximumRoutingHeight, Policy, Technology, ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=ReservationVariant + 1,
                    LaneDiversityLevel=LaneDiversityLevel,
                    CandidateDiversityLevel=0,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=SkipStrictPortalReservation,
                    Deadline=Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                    PreparedPortalCache=EffectivePreparedPortalCache,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    RegenerateSignals=(
                        AffectedCandidateSignals
                        if LocalizePortalRegeneration
                        else frozenset()
                    ),
                )
            if EscalationDecision.Action == "TryUnreservedPortals":
                LocalizePortalRegeneration = not LocalClaims
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedCandidateSignals,
                    )
                    if LocalizePortalRegeneration
                    else ({}, {})
                )
                Escalation.update({
                    "Action": "try-bounded-unreserved-portals",
                    "AffectedSignals": sorted(AffectedCandidateSignals),
                    "LocalizedCandidateRegeneration": (
                        LocalizePortalRegeneration
                    ),
                    "FromPortalMode": "reserved",
                    "ToPortalMode": "unreserved",
                    "ReservationVariant": ReservationVariant,
                })
                return RouteAuthoritativeResources(
                    Placed, Resources, SearchMarginX, SearchMarginZ,
                    MaximumRoutingHeight, Policy, Technology, ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=ReservationVariant,
                    LaneDiversityLevel=LaneDiversityLevel,
                    CandidateDiversityLevel=CandidateDiversityLevel,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=True,
                    Deadline=Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    RegenerateSignals=(
                        AffectedCandidateSignals
                        if LocalizePortalRegeneration
                        else frozenset()
                    ),
                )
            if (
                EscalationDecision.Action == "IncreaseLaneDiversity"
            ):
                NextLaneDiversityLevel = LaneDiversityLevel + 1
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedCandidateSignals,
                    )
                )
                Escalation.update({
                    "Action": "increase-guide-lane-diversity",
                    "AffectedSignals": sorted(AffectedCandidateSignals),
                    "FromLaneDiversityLevel": LaneDiversityLevel,
                    "ToLaneDiversityLevel": NextLaneDiversityLevel,
                })
                return RouteAuthoritativeResources(
                    Placed, Resources, SearchMarginX, SearchMarginZ,
                    MaximumRoutingHeight, Policy, Technology, ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=ReservationVariant,
                    LaneDiversityLevel=NextLaneDiversityLevel,
                    CandidateDiversityLevel=CandidateDiversityLevel,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=SkipStrictPortalReservation,
                    Deadline=Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                    PreparedPortalCache=EffectivePreparedPortalCache,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    PriorCandidateCache={
                        Signal: tuple(Values)
                        for Signal, Values in CandidatesBySignal.items()
                        if Values and Signal in AffectedCandidateSignals
                    },
                    PriorCandidateMetadata={
                        Signal: dict(Values)
                        for Signal, Values in CandidateAxisLaneBySignal.items()
                        if Signal in AffectedCandidateSignals
                    },
                    RegenerateSignals=AffectedCandidateSignals,
                )
            if (
                EscalationDecision.Action == "AddRoutingLayer"
            ):
                NextLayerCount = SelectEscalatedRoutingLayerCount(
                    LayerCount=LayerCount,
                    EffectiveMaximumLayerCount=EffectiveMaximumLayerCount,
                    ConflictClassification=str(
                        ConflictGraph["Classification"]
                    ),
                    ForceMaximumAfterPlacementRelocation=(
                        Policy.Placement
                        .ForceMaximumRoutingLayersAfterPlacementRelocation
                    ),
                )
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedCandidateSignals,
                    )
                )
                Escalation.update({
                    "Action": "add-routing-layer",
                    "AffectedSignals": sorted(AffectedCandidateSignals),
                    "FromLayerCount": LayerCount,
                    "ToLayerCount": NextLayerCount,
                })
                return RouteAuthoritativeResources(
                    Placed, Resources, SearchMarginX, SearchMarginZ,
                    MaximumRoutingHeight, Policy, Technology, ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=NextLayerCount,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=0,
                    LaneDiversityLevel=0,
                    CandidateDiversityLevel=0,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=SkipStrictPortalReservation,
                    Deadline=Deadline,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    PriorCandidateCache={
                        Signal: tuple(Values)
                        for Signal, Values in CandidatesBySignal.items()
                        if Values and Signal in AffectedCandidateSignals
                    },
                    PriorCandidateMetadata={
                        Signal: dict(Values)
                        for Signal, Values in CandidateAxisLaneBySignal.items()
                        if Signal in AffectedCandidateSignals
                    },
                    RegenerateSignals=AffectedCandidateSignals,
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
                            },
                            WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
                                "CompatibilityConflictClassification",
                                Diagnostics,
                            ),
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
                    "Rust MRV assignment found no exact capacity-one selection "
                    f"after {Result.ExpansionCount} expansions; "
                    f"budget_exhausted={ShouldGrowAssignmentBudget(Result)}; "
                    f"failure_net_candidates="
                    f"{len(CandidatesBySignal.get(Result.FailureNet, ())) if Result.FailureNet else 0}; "
                    f"conflict_classification={ConflictGraph['Classification']}; "
                    f"pairwise_unroutable="
                    f"{(ConflictGraph['PairwiseIncompatibleEdges'] if Policy.AdaptiveRouting.Enabled else ZeroCompatibilityPairs)[:4]}"
                ),
                Diagnostics={
                    **ConflictGraph,
                    "ConflictGraph": ConflictGraph,
                    "EscalationHistory": tuple(EscalationHistory),
                    "RoutingEscalationState": EffectiveState.ToDictionary(),
                    "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                    "CandidateFingerprint": CandidateFingerprint,
                    "ConflictFingerprint": ConflictFingerprint,
                },
            )
        )
    if ProgressCallback is not None:
        ProgressCallback(5, StageCount)
    InitialAssignmentExpansionCount = Result.ExpansionCount
    Selected = {
        Signal: CandidateLookup[CandidateId]
        for Signal, CandidateId in Result.SelectedCandidateIds
    }
    AssignmentExpansionCount = InitialAssignmentExpansionCount
    RepairIterations = []
    ReroutedSignals: set[str] = set()
    if NegotiatedPlan is not None:
        RepairIterations.extend(NegotiatedPlan.Iterations)
        ReroutedSignals.update(NegotiatedPlan.ReroutedSignals)

    def ReportMaterializationStage(Stage: str) -> None:
        if DiagnosticCallback is None:
            return
        Values = tuple(Selected.values())
        DiagnosticCallback(
            RoutingIterationMetrics(
                Iteration=1,
                Stage=Stage,
                ConflictCount=0,
                ReroutedNets=len(ReroutedSignals),
                AverageLength=(
                    sum(Value.Length for Value in Values) / len(Values)
                    if Values
                    else 0.0
                ),
                BendCount=sum(Value.BendCount for Value in Values),
                ViaCount=sum(Value.ViaCount for Value in Values),
            ),
            None,
        )
    if CoarsePlan is not None and NegotiatedPlan is None:
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print("[debug] authoritative: entering offline repair loop", flush=True)
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
                sum(Candidate.Length for Candidate in Values.values()),
                sum(Candidate.BendCount for Candidate in Values.values()),
                sum(Candidate.ViaCount for Candidate in Values.values()),
            )

        CurrentQuality = SelectionQuality(Selected)
        StagnationCount = 0
        for PassIndex in range(Policy.GlobalRouting.MaximumRipupPasses):
            CheckRuntimeBudget("CongestionRepair")
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(f"[debug] authoritative: congestion pass {PassIndex}", flush=True)
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
                    key=lambda Value: (
                        -Value[1],
                        -Selected[Value[0]].Length,
                        -Selected[Value[0]].BendCount,
                        -Selected[Value[0]].ViaCount,
                        Value[0],
                    ),
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
            RaiseForNativeAssignmentDeadline(RepairResult)
            CheckRuntimeBudget("CongestionRepair")
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
                    ConflictSignals=tuple(Offenders),
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
        if ShouldRunShapeOptimization(Policy.QualityTarget):
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
                CheckRuntimeBudget("ShapeOptimization")
                if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                    print(
                        f"[debug] authoritative: shape pass {ShapePass}",
                        flush=True,
                    )
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
                RaiseForNativeAssignmentDeadline(ShapeResult)
                CheckRuntimeBudget("ShapeOptimization")
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
                            Shaped[Signal].CandidateId
                            != Selected[Signal].CandidateId
                            for Signal in Selected
                        ),
                        AverageLength=(
                            sum(Value.Length for Value in Shaped.values())
                            / len(Shaped)
                        ),
                        BendCount=sum(
                            Value.BendCount for Value in Shaped.values()
                        ),
                        ViaCount=sum(
                            Value.ViaCount for Value in Shaped.values()
                        ),
                        ConflictSignals=tuple(ShapeOffenders),
                    )
                )
                if ShapedQuality >= CurrentQuality:
                    continue
                ReroutedSignals.update(
                    Signal
                    for Signal in Selected
                    if Shaped[Signal].CandidateId
                    != Selected[Signal].CandidateId
                )
                Selected = Shaped
                CurrentQuality = ShapedQuality
                Result = ShapeResult
    CheckRuntimeBudget("Materialization")
    ReportMaterializationStage("Authoritative assignment")
    SelectedClaimsBySignal = {
        Signal: Value.Claims for Signal, Value in Selected.items()
    }
    for Signal, SignalClaims in sorted(LocalClaimsBySignal.items()):
        CheckRuntimeBudget("MaterializationClaims")
        if not SignalClaims:
            continue
        LocalClaimsBySignalResource = frozenset(
            Resource
            for Claim in SignalClaims
            for Resource in Claim.Claims.ResourceIds
        )
        if Signal in SelectedClaimsBySignal:
            SelectedClaimsBySignal[Signal] = RoutingResourceClaims(
                WireCells=(
                    frozenset(SelectedClaimsBySignal[Signal].WireCells)
                    | frozenset(
                        Resource.Position
                        for Resource in LocalClaimsBySignalResource
                        if Resource.Kind == RoutingResourceKind.Wire
                    )
                ),
                SupportCells=(
                    frozenset(SelectedClaimsBySignal[Signal].SupportCells)
                    | frozenset(
                        Resource.Position
                        for Resource in LocalClaimsBySignalResource
                        if Resource.Kind == RoutingResourceKind.Support
                    )
                ),
                RequiredAirCells=(
                    frozenset(SelectedClaimsBySignal[Signal].RequiredAirCells)
                    | frozenset(
                        Resource.Position
                        for Resource in LocalClaimsBySignalResource
                        if Resource.Kind == RoutingResourceKind.Air
                    )
                ),
                ElectricalCells=(
                    frozenset(SelectedClaimsBySignal[Signal].ElectricalCells)
                    | frozenset(
                        Resource.Position
                        for Resource in LocalClaimsBySignalResource
                        if Resource.Kind == RoutingResourceKind.Electrical
                    )
                ),
            )
        else:
            SelectedClaimsBySignal[Signal] = RoutingResourceClaims(
                WireCells=frozenset(
                    Resource.Position
                    for Resource in LocalClaimsBySignalResource
                    if Resource.Kind == RoutingResourceKind.Wire
                ),
                SupportCells=frozenset(
                    Resource.Position
                    for Resource in LocalClaimsBySignalResource
                    if Resource.Kind == RoutingResourceKind.Support
                ),
                RequiredAirCells=frozenset(
                    Resource.Position
                    for Resource in LocalClaimsBySignalResource
                    if Resource.Kind == RoutingResourceKind.Air
                ),
                ElectricalCells=frozenset(
                    Resource.Position
                    for Resource in LocalClaimsBySignalResource
                    if Resource.Kind == RoutingResourceKind.Electrical
                ),
            )
    ClaimsBySignal = SelectedClaimsBySignal
    CheckRuntimeBudget("AssignmentDrc")
    Conflicts = FindClaimConflicts(
        ClaimsBySignal,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "AssignmentDrc",
            Diagnostics,
        ),
    )
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
    ReportMaterializationStage("Assignment ownership validation")

    SignalOrder = tuple(sorted(Selected))
    PortalLookup = {
        Portal.PortalId: Portal
        for Values in Portals.values()
        for Portal in Values
    }
    MissingProfileSignals = tuple(sorted(set(Selected) - set(Profiles)))
    if MissingProfileSignals:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Materialization",
                AffectedNets=MissingProfileSignals,
                Detail="selected candidate signal missing routing profile",
                Diagnostics={"Profiles": sorted(Profiles)},
            )
        )
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
    for Signal in set(LocalClaimsBySignal):
        if Signal not in Targets:
            SignalSignalTargets = SignalTargets.get(Signal)
            if SignalSignalTargets:
                Targets[Signal] = list(SignalSignalTargets)
    MissingTargetSignals = tuple(sorted(set(Selected) - set(Targets)))
    if MissingTargetSignals:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Materialization",
                AffectedNets=MissingTargetSignals,
                Detail="selected signal missing routing targets",
                Diagnostics={"Targets": sorted(Targets)},
            )
        )
    CheckRuntimeBudget("RouteMaterialization")
    NetWires = {Signal: set(Candidate.Nodes) for Signal, Candidate in Selected.items()}
    LocalSignalWireClaims = {
        Signal: tuple(Claim.Nodes for Claim in SignalClaims)
        for Signal, SignalClaims in LocalClaimsBySignal.items()
        if Signal in SignalTargets
    }
    for Signal, ClaimNodes in LocalSignalWireClaims.items():
        NetWires.setdefault(Signal, set()).update(*ClaimNodes)
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
    Supports: set[Position3] = set()
    SupportPositionCount = 0
    for Signal, Positions in NetWires.items():
        for X, Y, Z in Positions:
            SupportPositionCount += 1
            if SupportPositionCount % 256 == 0:
                CheckRuntimeBudget(
                    "PhysicalGraphMaterialization",
                    {
                        "Phase": "supports",
                        "Signal": Signal,
                        "ProcessedPositions": SupportPositionCount,
                    },
                )
            Supports.add((X, Y - 1, Z))
    Supports.difference_update(Resources.StaticGeometry.ActualBlocks)
    CheckRuntimeBudget("PhysicalGraphMaterialization")
    PhysicalGraphs = BuildPhysicalGraphs(
        NetWires,
        Resources.StaticGeometry.ActualBlocks,
        Supports,
        Resources.StaticGeometry.SolidBlocks,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "PhysicalGraphMaterialization",
            Diagnostics,
        ),
    )
    MissingSourceSignals = tuple(sorted(set(Selected) - set(Producers)))
    if MissingSourceSignals:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Materialization",
                AffectedNets=MissingSourceSignals,
                Detail="selected signal has no routable source gate output pin",
                Diagnostics={
                    "ProducerCount": len(Producers),
                    "SelectedCount": len(Selected),
                },
            )
        )
    MissingNoOutputSignals = tuple(
        sorted(
            Signal for Signal, Producer in Producers.items()
            if Signal in Selected and Producer.OutputPin is None
        )
    )
    if MissingNoOutputSignals:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Materialization",
                AffectedNets=MissingNoOutputSignals,
                Detail="selected source gate has no output pin",
            )
        )
    CheckRuntimeBudget("PhysicalConnectivityValidation")
    ValidatePhysicalRoutes(
        PhysicalGraphs,
        Producers,
        Targets,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "PhysicalConnectivityValidation",
            Diagnostics,
        ),
    )
    CheckRuntimeBudget("PhysicalConnectivityValidation")
    ReportMaterializationStage("Physical connectivity validation")
    Tracks = {}
    Owners: dict[RoutingResourceId, list[str]] = defaultdict(list)
    for Signal, Candidate in Selected.items():
        CheckRuntimeBudget("RepeaterPlanning")
        if Candidate.SourcePortalId not in PortalLookup:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                    Stage="Materialization",
                    AffectedNets=(Signal,),
                    Detail="candidate source portal id missing from portal lookup",
                    Diagnostics={
                        "SourcePortalId": Candidate.SourcePortalId,
                        "CandidateId": Candidate.CandidateId,
                        "PortalCount": len(PortalLookup),
                    },
                )
            )
        Graph = PhysicalGraphs[Signal]
        FallbackReservations, Paths = _ReserveRepeaters(
            Signal,
            Producers[Signal].OutputPin,
            tuple(Targets[Signal]),
            Graph,
            Technology,
        )
        Reservations = (
            Candidate.RepeaterReservations
            if Candidate.RepeaterReservations
            else FallbackReservations
        )
        for Resource in ResourceClaimsBySignal[Signal]:
            Owners[Resource].append(Signal)
        SourcePortal = PortalLookup[Candidate.SourcePortalId]
        for Target in Targets[Signal]:
            if Target not in Candidate.TargetPortalIds:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                        Stage="Materialization",
                        AffectedNets=(Signal,),
                        Detail="candidate target missing portal mapping",
                        Diagnostics={
                            "Signal": Signal,
                            "Target": list(Target),
                            "CandidateId": Candidate.CandidateId,
                        },
                    )
                )
            if Candidate.TargetPortalIds[Target] not in PortalLookup:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                        Stage="Materialization",
                        AffectedNets=(Signal,),
                        Detail="candidate target portal id missing from portal lookup",
                        Diagnostics={
                            "Signal": Signal,
                            "Target": list(Target),
                            "PortalId": Candidate.TargetPortalIds[Target],
                        },
                    )
                )
            if Target not in Profiles[Signal].TargetAccessPaths:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                        Stage="Materialization",
                        AffectedNets=(Signal,),
                        Detail="target missing profile access path",
                        Diagnostics={
                            "Signal": Signal,
                            "Target": list(Target),
                        },
                    )
                )
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
    ReportMaterializationStage("Repeater reservation planning")
    TrackAssignmentValue = TrackAssignment(
        Tracks=Tracks,
        ResourceOwners={Resource: tuple(Values) for Resource, Values in Owners.items()},
    )
    CheckRuntimeBudget("RepeaterMaterialization")
    Repeaters = MaterializeReservedRepeaters(
        NetWires,
        Producers,
        Targets,
        PhysicalGraphs,
        Tracks,
        Technology,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "RepeaterMaterialization",
            Diagnostics,
        ),
    )
    CheckRuntimeBudget("RepeaterMaterialization")
    ReportMaterializationStage("Repeater signal-strength validation")
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
    CheckRuntimeBudget("MaterializationComplete")
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
        NegotiatedRoutingDiagnostics=(
            dict(WorkTelemetry.get("NegotiatedRouting", {}))
            if NegotiatedPlan is not None
            else {}
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
                "CandidateLayersBySignal": {
                    Signal: sorted({Candidate.Layer for Candidate in Values})
                    for Signal, Values in sorted(CandidatesBySignal.items())
                },
            },
            "RoutingEscalationState": RoutingEscalationState(
                PortalMode=("unreserved" if UnreservedPortalMode else "reserved"),
                ReservationVariant=ReservationVariant,
                LaneDiversityLevel=LaneDiversityLevel,
                CandidateDiversityLevel=CandidateDiversityLevel,
                EffectiveRoutingLayers=LayerCount,
                AssignmentBudget=AssignmentExpansionLimit,
                CandidateFingerprint=BuildStableFingerprint({
                    Signal: [
                        Candidate.CandidateId
                        for Candidate in CandidatesBySignal[Signal]
                    ]
                    for Signal in sorted(CandidatesBySignal)
                }),
            ).ToDictionary(),
            "Deadline": Deadline.ToDictionary(),
            "AdaptiveEscalationHistory": [
                *EscalationHistory,
                *AssignmentRetryHistory,
            ],
            "RustAssignmentUsed": True,
            "NativeBatching": {
                "PortalRequestCount": WorkTelemetry["PortalRequestCount"],
                "PortalTargetCount": WorkTelemetry["PortalTargetCount"],
                "RouteTreeRequestCount": CandidateRequestCount,
                "PortalBatchCount": WorkTelemetry["PortalBatchCount"],
                "PortalCacheHit": WorkTelemetry["PortalCacheHit"],
                "RouteTreeBatchCount": RouteTreeBatchCount,
                "InitialCandidateRequestsPerSignal": InitialRequestLimit,
                "CandidateDiagnostics": {
                    Signal: {
                        Key: Value
                        for Key, Value in Values.items()
                        if Key != "Rejections"
                    }
                    for Signal, Values in sorted(CandidateDiagnostics.items())
                },
                "DeterministicRequestOrdering": True,
            },
            "PortalReservations": [
                Value.ToDictionary() for Value in PortalReservations
            ],
            "RustAssignmentExpansionLimit": AssignmentExpansionLimit,
            "RustAssignmentExpansions": InitialAssignmentExpansionCount,
            "LayerCappedAssignmentAttempts": LayerCappedAssignmentAttempts,
            "LocalizedRepairPasses": len(RepairIterations),
            "LocalizedReroutedNetCount": len(ReroutedSignals),
            "LocalizedRepairOffenders": [
                list(Iteration.ConflictSignals)
                for Iteration in RepairIterations
            ],
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

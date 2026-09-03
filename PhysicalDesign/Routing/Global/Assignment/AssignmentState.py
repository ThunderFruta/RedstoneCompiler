"""Exact assignment indexes, propagation, and no-goods."""

from __future__ import annotations

from ...Regions.Proofs.Validation import BuildPhysicalPortApertureContractFingerprint

from ...Regions.Proofs.Validation import BuildPhysicalPortLocalContractFingerprint

from ...Regions.Proofs.Validation import BuildPhysicalPortSeamContractFingerprint

from ...Regions.Proofs.Certification import ValidatePhysicalLocalPortPairSupportCertificate

from ....Contracts.Component import PhysicalComponentPortReservation

from ....Contracts.Core import Position2

from ....Contracts.Core import Position3

from ....Contracts.PhysicalInterface import PhysicalComponentPortCspState

from ....Contracts.PhysicalInterface import PhysicalLocalPortPairSupportCertificate

from ....Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain

from ....Contracts.Results import RoutingResources

from ....Interfaces.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint

from ....Resources.ResourceGraph import NetRouteCandidate

from ....Resources.ResourceGraph import RoutingResourceClaims

from ....Resources.ResourceGraph import RoutingResourceId

from collections import Counter

from collections import defaultdict

from dataclasses import dataclass

from dataclasses import field

from math import ceil

from types import SimpleNamespace

from typing import Any

from typing import Callable

from typing import Iterable

from typing import Mapping

@dataclass
class IncrementalPhysicalCandidateArcIndex:
    """Incrementally compile binary candidate compatibility supports.

    Physical candidate domains grow by suffix tranches.  Rechecking every
    old Cartesian pair after each tranche is duplicate work: candidate claims
    are immutable, so a pair's compatibility never changes.  This index
    compares only pairs incident to newly observed candidates and represents
    AC support as candidate-id sets for cheap fixed-point intersections.
    """

    CandidateIdsBySignal: dict[str, set[str]] = field(
        default_factory=dict
    )
    CompatibleIdsByCandidateSignal: dict[
        tuple[str, str], set[str]
    ] = field(default_factory=dict)
    ComparedIdsByCandidateSignal: dict[
        tuple[str, str], set[str]
    ] = field(default_factory=dict)
    ComparisonCount: int = 0
    SupportIntersectionCount: int = 0

    def Extend(
        self,
        CandidateSets: Mapping[str, Iterable[Any]],
        Compatible: Callable[[Any, Any], bool],
    ) -> int:
        """Index each newly introduced cross-signal candidate pair once."""
        CandidatesBySignal = {
            Signal: {
                str(Candidate.CandidateId): Candidate
                for Candidate in Values
            }
            for Signal, Values in CandidateSets.items()
        }
        AddedComparisons = 0
        Signals = tuple(sorted(CandidatesBySignal))
        for FirstIndex, FirstSignal in enumerate(Signals):
            FirstCandidates = CandidatesBySignal[FirstSignal]
            for SecondSignal in Signals[FirstIndex + 1:]:
                SecondCandidates = CandidatesBySignal[SecondSignal]

                def RecordCompatibility(
                    FirstCandidateId: str,
                    SecondCandidateId: str,
                ) -> None:
                    nonlocal AddedComparisons
                    AddedComparisons += 1
                    self.ComparedIdsByCandidateSignal.setdefault(
                        (FirstCandidateId, SecondSignal),
                        set(),
                    ).add(SecondCandidateId)
                    self.ComparedIdsByCandidateSignal.setdefault(
                        (SecondCandidateId, FirstSignal),
                        set(),
                    ).add(FirstCandidateId)
                    if not Compatible(
                        FirstCandidates[FirstCandidateId],
                        SecondCandidates[SecondCandidateId],
                    ):
                        return
                    self.CompatibleIdsByCandidateSignal.setdefault(
                        (FirstCandidateId, SecondSignal),
                        set(),
                    ).add(SecondCandidateId)
                    self.CompatibleIdsByCandidateSignal.setdefault(
                        (SecondCandidateId, FirstSignal),
                        set(),
                    ).add(FirstCandidateId)
                SecondCandidateIds = set(SecondCandidates)
                for FirstCandidateId in sorted(FirstCandidates):
                    ComparedSecondIds = (
                        self.ComparedIdsByCandidateSignal.get(
                            (FirstCandidateId, SecondSignal),
                            set(),
                        )
                    )
                    for SecondCandidateId in sorted(
                        SecondCandidateIds - ComparedSecondIds
                    ):
                        RecordCompatibility(
                            FirstCandidateId,
                            SecondCandidateId,
                        )
        for Signal, Candidates in CandidatesBySignal.items():
            self.CandidateIdsBySignal.setdefault(Signal, set()).update(
                Candidates
            )
        self.ComparisonCount += AddedComparisons
        return AddedComparisons

    def HasSupport(
        self,
        CandidateId: str,
        OtherSignal: str,
        OtherCandidateIds: set[str],
    ) -> bool:
        """Return whether one candidate has support in a current domain."""
        self.SupportIntersectionCount += 1
        return not self.CompatibleIdsByCandidateSignal.get(
            (str(CandidateId), OtherSignal),
            set(),
        ).isdisjoint(OtherCandidateIds)

    def Propagate(
        self,
        CandidateSets: Mapping[str, Iterable[Any]],
    ) -> tuple[dict[str, list[Any]], int]:
        """Enforce binary arc consistency over already indexed supports."""
        Mutable = {
            Signal: list(Values)
            for Signal, Values in CandidateSets.items()
        }
        PruneCount = 0
        Changed = True
        while Changed:
            Changed = False
            CandidateIds = {
                Signal: {
                    str(Candidate.CandidateId)
                    for Candidate in Values
                }
                for Signal, Values in Mutable.items()
            }
            for Signal in sorted(Mutable):
                Retained = [
                    Candidate
                    for Candidate in Mutable[Signal]
                    if all(
                        self.HasSupport(
                            str(Candidate.CandidateId),
                            OtherSignal,
                            CandidateIds[OtherSignal],
                        )
                        for OtherSignal in Mutable
                        if OtherSignal != Signal
                    )
                ]
                PruneCount += len(Mutable[Signal]) - len(Retained)
                if len(Retained) != len(Mutable[Signal]):
                    Mutable[Signal] = Retained
                    Changed = True
                if not Retained:
                    break
            if any(not Values for Values in Mutable.values()):
                break
        return Mutable, PruneCount

def GetPhysicalGlobalAssignmentArcIndex(
    Resources: RoutingResources,
    *,
    Persistent: bool,
) -> IncrementalPhysicalCandidateArcIndex:
    """Reuse compiled candidate supports across physical assembly replans."""
    if not Persistent:
        return IncrementalPhysicalCandidateArcIndex()
    Existing = Resources.PhysicalGlobalAssignmentArcIndex
    if Existing is None:
        Existing = IncrementalPhysicalCandidateArcIndex()
        Resources.PhysicalGlobalAssignmentArcIndex = Existing
    return Existing

def BeginPhysicalAssignmentArcPass(
    Telemetry: dict[str, object],
) -> None:
    """Discard prior-domain conflict witnesses before one AC-3 pass."""
    for Key in (
        "EmptySignals",
        "BlockerSignalsByEmptySignal",
        "EncodingRemovedSignal",
    ):
        Telemetry.pop(Key, None)

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

    ConflictPositions = {
        Resource.Position for Resource in ConflictResourceSet
    }
    ConflictNeighborhood = {
        (
            Position[0] + DeltaX,
            Position[1] + DeltaY,
            Position[2] + DeltaZ,
        )
        for Position in ConflictPositions
        for DeltaX, DeltaY, DeltaZ in (
            (0, 0, 0),
            (-1, 0, 0),
            (1, 0, 0),
            (0, -1, 0),
            (0, 1, 0),
            (0, 0, -1),
            (0, 0, 1),
        )
    }

    def BranchTouchesConflict(
        Path: tuple[Position3, ...],
        BranchClaims: frozenset[RoutingResourceId],
    ) -> bool:
        if BranchClaims & ConflictResourceSet:
            return True
        return any(Position in ConflictNeighborhood for Position in Path)

    def BranchConflictIndex(
        Path: tuple[Position3, ...],
        BranchClaims: frozenset[RoutingResourceId],
    ) -> int:
        if not ConflictResourceSet:
            return -1
        ConflictIndex = max(
            (
                Index
                for Index, Position in enumerate(Path)
                if Position in ConflictNeighborhood
            ),
            default=-1,
        )
        if BranchClaims & ConflictResourceSet:
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
        if not BranchTouchesConflict(Path, BranchClaims):
            RetainedTargetPaths[Target] = Path
            RetainedTargetClaims[Target] = BranchClaims
            RetainedNodes.update(Path)
            for Position in Path:
                SharedNodeCounts[Position] += 1
            continue
        BranchpointIndex = 0
        ConflictIndex = BranchConflictIndex(Path, BranchClaims)
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

def CandidatePortalTupleIndex(
    Variant: int,
    PortalPhase: int,
    PortalTupleCount: int,
    CoordinatedRequestWindowOffset: int = 0,
) -> int:
    """Select one deterministic portal tuple from a targeted request window."""
    if PortalTupleCount < 1:
        raise ValueError("PortalTupleCount must be positive")
    if CoordinatedRequestWindowOffset < 0:
        raise ValueError(
            "CoordinatedRequestWindowOffset cannot be negative"
        )
    return (
        Variant
        + PortalPhase
        + CoordinatedRequestWindowOffset
    ) % PortalTupleCount

@dataclass(frozen=True)
class CandidateRequestShapeDescriptor:
    """Lightweight identity for one native route-tree request shape.

    Physical-global planning can enumerate this finite domain without eagerly
    expanding every guide and blocked-node payload.  The descriptor is later
    materialized monotonically in deterministic batches.
    """

    SourcePortal: Any
    TargetPortals: tuple[Any, ...]
    Guide: frozenset[Position2]
    Layer: int
    Axis: str
    Lane: int
    Variant: int
    PortalShapeRank: int
    RoutingY: int
    GuideExpansion: int
    InitiallyDeferred: bool
    Priority: tuple[object, ...]

    def DomainIdentity(self) -> tuple[object, ...]:
        return (
            self.SourcePortal.PortalId,
            tuple(Portal.PortalId for Portal in self.TargetPortals),
            tuple(sorted(self.Guide)),
            self.Layer,
            self.Axis,
            self.Lane,
            self.Variant,
            self.PortalShapeRank,
            self.RoutingY,
            self.GuideExpansion,
            self.InitiallyDeferred,
            self.Priority,
        )

@dataclass
class LazyCandidateRouteRequest:
    """Materialize an ordinary physical-global request only when executed."""

    Shape: CandidateRequestShapeDescriptor
    Builder: Callable[[], tuple[Any, ...] | None] = field(repr=False)
    _Materialized: tuple[Any, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _WasMaterialized: bool = field(default=False, init=False, repr=False)

    def Materialize(self) -> tuple[Any, ...] | None:
        if not self._WasMaterialized:
            self._Materialized = self.Builder()
            self._WasMaterialized = True
        return self._Materialized

    def __str__(self) -> str:
        return repr((
            "lazy-candidate-route-request-v1",
            self.Shape.DomainIdentity(),
        ))

    def __len__(self) -> int:
        Materialized = self.Materialize()
        return 0 if Materialized is None else len(Materialized)

    def __getitem__(self, Index: int) -> Any:
        Materialized = self.Materialize()
        if Materialized is None:
            raise IndexError("candidate request was rejected before native routing")
        return Materialized[Index]

def ShouldDeferUnreservedCandidateRequestShape(
    *,
    UnreservedPortalMode: bool,
    UseSparseCandidateBootstrap: bool,
    SparseBootstrapRanks: tuple[int, ...],
    PortalShapeRank: int,
    UnreservedPerLayerRequestLimit: int,
    CompleteCoordinatedSignalWindow: bool,
) -> bool:
    """Keep broad shapes deferred unless an exact offender owns the retry.

    A topology cut-scoped coordinated retry has already exhausted the sparse
    canonical windows for one signal. Its remaining route shapes are existing
    bounded work, not a new global search limit, so materialize that signal's
    complete current portal/lane window before rejecting the placement.
    """
    if PortalShapeRank < 0:
        raise ValueError("PortalShapeRank cannot be negative")
    if UnreservedPerLayerRequestLimit < 1:
        raise ValueError(
            "UnreservedPerLayerRequestLimit must be positive"
        )
    if not UnreservedPortalMode or CompleteCoordinatedSignalWindow:
        return False
    return (
        min(SparseBootstrapRanks, default=2) >= 2
        if UseSparseCandidateBootstrap
        else PortalShapeRank >= UnreservedPerLayerRequestLimit
    )

def ShouldCompletePhysicalCandidateRequestWindow(
    PreparingPhysicalComponentGlobalChannels: bool,
    ApplyCoordinatedPortalWindow: bool,
    SignalCandidateDiversityLevel: int,
    CandidateDiversityLevel: int,
    SignalIsPhysicalComponentPort: bool = False,
) -> bool:
    """Eagerly materialize only immutable component-port channel domains."""
    return bool(
        (
            PreparingPhysicalComponentGlobalChannels
            and SignalIsPhysicalComponentPort
        )
        or (
            ApplyCoordinatedPortalWindow
            and SignalCandidateDiversityLevel
            > CandidateDiversityLevel
        )
    )

def IsPhysicalCandidateRequestDomainComplete(
    RemainingRequestCount: int,
    DeadlineExpired: bool,
) -> bool:
    """Report the immutable completion fact for a finite request domain.

    A deadline sampled after the final descriptor cannot invalidate the
    completion proof.  The deadline only distinguishes incomplete domains;
    keep the argument while existing callers transition to that distinction.
    """
    if RemainingRequestCount < 0:
        raise ValueError("RemainingRequestCount cannot be negative")
    del DeadlineExpired
    return RemainingRequestCount == 0

def SelectPhysicalGlobalAssignmentSuffixSignals(
    CandidateSignals: Iterable[str],
    SelectedCandidateIds: Iterable[tuple[str, str]],
    NativeConflictSignals: Iterable[str],
    RemainingRequestCounts: dict[str, int],
) -> tuple[str, ...]:
    """Select the deterministic non-exhausted suffix owners for one cut.

    The exact assignment engine reports the signals in its failed cut.  Older
    engines only expose the partial selected prefix, so missing signals are a
    conservative fallback.  In either case, already exhausted request cursors
    are excluded: rerunning them cannot enlarge the assignment domain.
    """
    Signals = frozenset(str(Signal) for Signal in CandidateSignals)
    NativeSignals = frozenset(
        str(Signal)
        for Signal in NativeConflictSignals
        if str(Signal) in Signals
    )
    SelectedSignals = frozenset(
        str(Signal)
        for Signal, _CandidateId in SelectedCandidateIds
    )
    RelevantSignals = NativeSignals or (Signals - SelectedSignals)
    return tuple(sorted(
        Signal
        for Signal in RelevantSignals
        if int(RemainingRequestCounts.get(Signal, 0)) > 0
    ))

def SelectPhysicalGlobalPairSupportSuffixSignals(
    CandidatesBySignal: dict[str, Iterable[Any]],
    ExhaustedSignals: Iterable[str],
    RemainingRequestCounts: dict[str, int],
) -> tuple[str, ...]:
    """Close lazy partner domains before proving an incompatible pair.

    Exhausting one side of a failed assignment is insufficient when its
    current candidates have zero support in a partner whose route-request
    cursor is still open.  Materialize those partners before classifying the
    fixed physical plan as complete or emitting pairwise port no-goods.
    """
    CompleteSignals = tuple(sorted({
        str(Signal)
        for Signal in ExhaustedSignals
        if (
            str(Signal) in CandidatesBySignal
            and int(RemainingRequestCounts.get(str(Signal), 0)) == 0
        )
    }))
    Result = set()
    for Signal in CompleteSignals:
        SignalCandidates = tuple(CandidatesBySignal.get(Signal, ()))
        if not SignalCandidates:
            continue
        for OtherSignal in sorted(CandidatesBySignal):
            if (
                OtherSignal == Signal
                or int(RemainingRequestCounts.get(OtherSignal, 0)) <= 0
            ):
                continue
            OtherCandidates = tuple(
                CandidatesBySignal.get(OtherSignal, ())
            )
            if not any(
                not _ClaimsConflict(
                    Signal,
                    Candidate.Claims,
                    OtherSignal,
                    OtherCandidate.Claims,
                )
                for Candidate in SignalCandidates
                for OtherCandidate in OtherCandidates
            ):
                Result.add(OtherSignal)
    return tuple(sorted(Result))

def SelectOpenPhysicalGlobalCandidateDomainSignals(
    RemainingRequestCounts: Mapping[str, int],
) -> tuple[str, ...]:
    """Select every unfinished cursor required by an authoritative proof."""
    return tuple(sorted(
        str(Signal)
        for Signal, RemainingCount in RemainingRequestCounts.items()
        if int(RemainingCount) > 0
    ))

def SelectPhysicalGlobalNativePairCutSuffixSignals(
    Result: Any,
    RemainingRequestCounts: Mapping[str, int],
) -> tuple[str, ...]:
    """Advance the minimum finite suffix capable of closing a native pair.

    Native pairwise incompatibility is exact for the candidate prefixes it
    receives.  It becomes a reusable binary clause only after both finite
    descriptor cursors are complete.  Prefer one open endpoint adjacent to a
    completed domain.  Otherwise close both endpoints of the smallest
    reported pair; enumerating unrelated variables cannot prove that clause.
    """
    if not bool(getattr(Result, "PairwiseCompatibilityComplete", False)):
        return ()
    CompletedPeerCandidates = []
    OpenPairCandidates = []
    for FirstSignal, SecondSignal in getattr(
        Result,
        "PairwiseIncompatibleSignals",
        (),
    ):
        First = str(FirstSignal)
        Second = str(SecondSignal)
        FirstRemaining = int(RemainingRequestCounts.get(First, 0))
        SecondRemaining = int(RemainingRequestCounts.get(Second, 0))
        if FirstRemaining == 0 and SecondRemaining > 0:
            CompletedPeerCandidates.append((
                SecondRemaining,
                Second,
                First,
            ))
        elif SecondRemaining == 0 and FirstRemaining > 0:
            CompletedPeerCandidates.append((
                FirstRemaining,
                First,
                Second,
            ))
        elif FirstRemaining > 0 and SecondRemaining > 0:
            OpenPairCandidates.append((
                FirstRemaining + SecondRemaining,
                max(FirstRemaining, SecondRemaining),
                min(FirstRemaining, SecondRemaining),
                tuple(sorted((First, Second))),
            ))
    if CompletedPeerCandidates:
        _Remaining, Signal, _CompletedPeer = min(
            CompletedPeerCandidates
        )
        return (Signal,)
    if OpenPairCandidates:
        _Total, _Maximum, _Minimum, Signals = min(
            OpenPairCandidates
        )
        return Signals
    return ()

def SelectCompletedPhysicalGlobalPairNoGoodEdges(
    Result: Any,
    RemainingRequestCounts: Mapping[str, int],
) -> tuple[tuple[str, str], ...]:
    """Return native pair clauses whose two finite route domains are closed."""
    if not bool(getattr(Result, "PairwiseCompatibilityComplete", False)):
        return ()
    return tuple(sorted({
        tuple(sorted((str(FirstSignal), str(SecondSignal))))
        for FirstSignal, SecondSignal in getattr(
            Result,
            "PairwiseIncompatibleSignals",
            (),
        )
        if str(FirstSignal) != str(SecondSignal)
        and int(RemainingRequestCounts.get(str(FirstSignal), 0)) == 0
        and int(RemainingRequestCounts.get(str(SecondSignal), 0)) == 0
    }))

def PhysicalGlobalAssignmentDomainIsComplete(
    RelevantSignals: Iterable[str],
    RemainingRequestCounts: dict[str, int],
    AssignmentBudgetExhausted: bool,
    DeadlineExpired: bool,
) -> bool:
    """Prove a failed physical-global assignment exhausted its relevant cut."""
    Signals = tuple(dict.fromkeys(str(Signal) for Signal in RelevantSignals))
    return bool(
        Signals
        and not AssignmentBudgetExhausted
        and not DeadlineExpired
        and all(
            int(RemainingRequestCounts.get(Signal, 0)) == 0
            for Signal in Signals
        )
    )

def ConflictClassificationSupportsPhysicalPortPairNoGoods(
    Classification: str,
) -> bool:
    """Return whether reported edges are exact pair-domain conflicts.

    The conflict graph is initially built from exact pair incompatibilities.
    When two or more such edges exist, reporting promotes the graph from
    ``pairwise-incompatibility`` to ``multi-pair-placement-conflict``.  That
    presentation-level promotion must not discard the independently complete
    binary proofs when projecting a failed global plan back into the physical
    port CSP.
    """
    return str(Classification) in {
        "pairwise-incompatibility",
        "multi-pair-placement-conflict",
    }

def SelectExactNoGoodCspBranch(
    Domains: Mapping[str, tuple[Any, ...]],
    SelectedOptionKeysBySignal: Mapping[
        str, frozenset[tuple[str, str]]
    ],
    RejectedSets: Iterable[frozenset[tuple[str, str]]],
    OptionKeys: Callable[[Any], frozenset[tuple[str, str]]],
) -> tuple[str, tuple[Any, ...]]:
    """Order a CSP branch using repeated exact no-good literals.

    The rejected sets remain higher-order constraints.  Activity changes only
    which variable and value are visited first; it never projects a clause to
    a smaller, unsound no-good.  Repeated common literals therefore steer the
    next plan away from a failed shared prefix instead of walking only the
    one literal that differed between recent complete assignments.
    """
    if not Domains:
        raise ValueError("exact no-good branch selection requires a domain")

    ActiveClauses: list[dict[str, frozenset[str]]] = []
    for RejectedSet in RejectedSets:
        Grouped: dict[str, set[str]] = defaultdict(set)
        for Signal, Fingerprint in RejectedSet:
            Grouped[str(Signal)].add(str(Fingerprint))
        Clause = {
            Signal: frozenset(Fingerprints)
            for Signal, Fingerprints in Grouped.items()
        }
        if any(
            Signal in SelectedOptionKeysBySignal
            and not frozenset(
                Fingerprint
                for KeySignal, Fingerprint
                in SelectedOptionKeysBySignal[Signal]
                if KeySignal == Signal
            ).issuperset(Fingerprints)
            for Signal, Fingerprints in Clause.items()
        ):
            continue
        ActiveClauses.append(Clause)

    OptionActivity: dict[str, tuple[int, ...]] = {}
    for Signal, Options in Domains.items():
        Counts = []
        for Option in Options:
            Fingerprints = frozenset(
                Fingerprint
                for KeySignal, Fingerprint in OptionKeys(Option)
                if KeySignal == Signal
            )
            Counts.append(sum(
                1
                for Clause in ActiveClauses
                if Signal in Clause
                and Fingerprints.issuperset(Clause[Signal])
            ))
        OptionActivity[Signal] = tuple(Counts)

    PeakActivity = max(
        (max(Counts, default=0) for Counts in OptionActivity.values()),
        default=0,
    )
    if PeakActivity < 1:
        Signal = min(Domains, key=lambda Value: (len(Domains[Value]), Value))
        return Signal, Domains[Signal]

    Signal = min(
        Domains,
        key=lambda Value: (
            -max(OptionActivity[Value], default=0),
            len(Domains[Value]),
            Value,
        ),
    )
    OrderedOptions = tuple(
        Option
        for _Activity, _Index, Option in sorted(
            (
                (Activity, Index, Option)
                for Index, (Option, Activity) in enumerate(zip(
                    Domains[Signal],
                    OptionActivity[Signal],
                ))
            ),
            key=lambda Value: (Value[0], Value[1]),
        )
    )
    return Signal, OrderedOptions

def OrderPhysicalPortOptionsByPreferences(
    Signal: str,
    Options: Iterable[PhysicalComponentPortReservation],
    PreferredGlobalContractsBySignal: Mapping[str, str],
    PreferredReservationsBySignal: Mapping[str, str],
) -> tuple[PhysicalComponentPortReservation, ...]:
    """Order one exact port domain without changing its membership.

    A completed global contract is the strongest reusable preference.  The
    preceding exact reservation then provides a placement-scoped warm start
    among physically distinct options with that contract.  Exact no-goods and
    arc consistency remain authoritative and can prune either preference.
    """
    PreferredGlobalContract = PreferredGlobalContractsBySignal.get(
        Signal,
        "",
    )
    PreferredReservation = PreferredReservationsBySignal.get(Signal, "")
    return tuple(sorted(
        Options,
        key=lambda Option: (
            bool(PreferredGlobalContract)
            and BuildPhysicalPortGlobalContractFingerprint(Option)
            != PreferredGlobalContract,
            bool(PreferredReservation)
            and Option.ReservationFingerprint != PreferredReservation,
        ),
    ))

def BuildPhysicalPortNoGoodKeys(
    Port: PhysicalComponentPortReservation,
    PortSolverCacheKey: str,
) -> frozenset[tuple[str, str]]:
    """Return exact proof identities represented by one solver-domain option."""
    return frozenset((
        (Port.Signal, Port.ReservationFingerprint),
        (
            Port.Signal,
            BuildPhysicalPortGlobalContractFingerprint(Port),
        ),
        (
            Port.Signal,
            BuildPhysicalPortLocalContractFingerprint(Port),
        ),
        (
            Port.Signal,
            BuildPhysicalPortSeamContractFingerprint(Port),
        ),
        (
            Port.Signal,
            BuildPhysicalPortApertureContractFingerprint(Port),
        ),
        (
            Port.Signal,
            "fabric-domain:" + Port.FabricDomainFingerprint,
        ),
        (
            Port.Signal,
            "local-factor-domain:"
            + PortSolverCacheKey
            + ":"
            + Port.FabricDomainFingerprint,
        ),
        (
            Port.Signal,
            "local-signal-domain:" + PortSolverCacheKey,
        ),
        (
            Port.Signal,
            "scoped-request-reservation:"
            + PortSolverCacheKey
            + ":"
            + Port.ReservationFingerprint,
        ),
    ))

def BuildPhysicalLocalPortPairUnsupportedIndex(
    Certificates: Iterable[PhysicalLocalPortPairSupportCertificate],
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    PortSolverCacheKey: str,
) -> frozenset[
    frozenset[tuple[str, str]]
]:
    """Select complete, identity-matched local pair-support exclusions.

    A certificate is only authoritative for the exact prepared physical
    domain, resource graph, technology, component graph, and fabric against
    which its opposing-net proof completed.  Stale or incomplete cache rows
    are ignored rather than weakening the port CSP.
    """
    ExpectedComponentGraphFingerprint = str(
        Preparation.ComponentGraphFingerprint
    )
    ExpectedFabricFingerprint = str(
        Preparation.Problem.Fabric.FabricFingerprint
    )
    ExpectedResourceGraphFingerprint = str(
        Preparation.ResourceGraphFingerprint
    )
    ExpectedTechnologyFingerprint = str(
        Preparation.AccessCertificate.TechnologyFingerprint
    )
    Unsupported: set[frozenset[tuple[str, str]]] = set()
    for Certificate in Certificates:
        if not ValidatePhysicalLocalPortPairSupportCertificate(
            Certificate,
            Preparation,
            PortSolverCacheKey,
        ):
            continue
        if (
            Certificate.PreparedDomainFingerprint
            != Preparation.DomainFingerprint
            or Certificate.PortSolverCacheKey != PortSolverCacheKey
            or Certificate.ComponentGraphFingerprint
            != ExpectedComponentGraphFingerprint
            or Certificate.FabricFingerprint
            != ExpectedFabricFingerprint
            or Certificate.ResourceGraphFingerprint
            != ExpectedResourceGraphFingerprint
            or Certificate.TechnologyFingerprint
            != ExpectedTechnologyFingerprint
        ):
            continue
        if (
            not Certificate.RowSignal
            or not Certificate.RowContract
            or not Certificate.ColumnSignal
            or Certificate.RowSignal == Certificate.ColumnSignal
            or not Certificate.ColumnContracts
            or not Certificate.ProofFingerprints
        ):
            continue
        ExpectedPairs = frozenset(
            (
                Certificate.RowContract,
                ColumnContract,
            )
            for ColumnContract in Certificate.ColumnContracts
        )
        if Certificate.UnsupportedPairs != ExpectedPairs:
            continue
        # Validation above proves that ``ColumnContracts`` is exactly the
        # complete prepared domain for ``ColumnSignal``.  Resolve the complete
        # unsupported row to one identity-scoped directional clause instead of
        # expanding it back into one binary clause per column contract.  Every
        # option in that prepared column domain carries the local-signal-domain
        # key, so this rejects exactly the same row/column Cartesian pairs while
        # keeping CSP propagation linear in the number of certified rows.
        Unsupported.add(frozenset((
            (
                Certificate.RowSignal,
                Certificate.RowContract,
            ),
            (
                Certificate.ColumnSignal,
                "local-signal-domain:" + PortSolverCacheKey,
            ),
        )))
    return frozenset(Unsupported)

def GetPersistentPhysicalComponentPortCspState(
    Resources: RoutingResources,
    CacheKey: str,
    DomainFingerprint: str,
) -> tuple[PhysicalComponentPortCspState, bool]:
    """Reuse failed prefixes only under monotonic constraint growth."""
    RejectedBySignal = frozenset(
        (str(Signal), str(Fingerprint))
        for Signal, Fingerprints in (
            Resources
            .RejectedPhysicalComponentPortReservationsBySignal.items()
        )
        for Fingerprint in Fingerprints
    )
    RejectedSets = frozenset(
        frozenset(
            (str(Signal), str(Fingerprint))
            for Signal, Fingerprint in RejectedSet
        )
        for RejectedSet in (
            Resources.RejectedPhysicalComponentPortReservationSets
        )
    )
    RejectedAssignments = frozenset(map(
        str,
        Resources.RejectedPhysicalComponentPortAssignmentFingerprints,
    ))
    DeferredAssignments = frozenset(map(
        str,
        Resources.DeferredPhysicalComponentPortAssignmentFingerprints,
    ))
    Existing = Resources.PhysicalComponentPortCspStateCache.get(CacheKey)
    Reusable = bool(
        Existing is not None
        and Existing.DomainFingerprint == DomainFingerprint
        and Existing.RejectedReservationsBySignal.issubset(RejectedBySignal)
        and Existing.RejectedReservationSets.issubset(RejectedSets)
        and Existing.RejectedAssignmentFingerprints.issubset(
            RejectedAssignments
        )
        and Existing.DeferredAssignmentFingerprints.issubset(
            DeferredAssignments
        )
    )
    if not Reusable:
        Existing = PhysicalComponentPortCspState(
            DomainFingerprint=DomainFingerprint,
        )
        Resources.PhysicalComponentPortCspStateCache[CacheKey] = Existing
    assert Existing is not None
    Existing.RejectedReservationsBySignal = RejectedBySignal
    Existing.RejectedReservationSets = RejectedSets
    Existing.RejectedAssignmentFingerprints = RejectedAssignments
    Existing.DeferredAssignmentFingerprints = DeferredAssignments
    return Existing, Reusable

def PropagateExactNoGoodClauses(
    Domains: Mapping[str, tuple[Any, ...]],
    SelectedOptionKeysBySignal: Mapping[
        str, frozenset[tuple[str, str]]
    ],
    RejectedSets: Iterable[frozenset[tuple[str, str]]],
    OptionKeys: Callable[[Any], frozenset[tuple[str, str]]],
) -> dict[str, tuple[Any, ...]] | None:
    """Enforce GAC for forbidden exact tuples over option-key classes."""
    Mutable = {
        str(Signal): tuple(Values)
        for Signal, Values in Domains.items()
    }
    Clauses = []
    for RejectedSet in RejectedSets:
        Grouped: dict[str, set[str]] = defaultdict(set)
        for Signal, Fingerprint in RejectedSet:
            Grouped[str(Signal)].add(str(Fingerprint))
        if Grouped:
            Clauses.append({
                Signal: frozenset(Fingerprints)
                for Signal, Fingerprints in Grouped.items()
            })
    Changed = True
    while Changed:
        Changed = False
        for Clause in Clauses:
            ClauseSatisfied = False
            Unresolved = []
            for Signal, Fingerprints in Clause.items():
                if Signal in SelectedOptionKeysBySignal:
                    SelectedFingerprints = frozenset(
                        Fingerprint
                        for KeySignal, Fingerprint
                        in SelectedOptionKeysBySignal[Signal]
                        if KeySignal == Signal
                    )
                    if not SelectedFingerprints.issuperset(Fingerprints):
                        ClauseSatisfied = True
                        break
                    continue
                if Signal not in Mutable:
                    ClauseSatisfied = True
                    break
                Matching = tuple(
                    Option
                    for Option in Mutable[Signal]
                    if frozenset(
                        Fingerprint
                        for KeySignal, Fingerprint in OptionKeys(Option)
                        if KeySignal == Signal
                    ).issuperset(Fingerprints)
                )
                if not Matching:
                    ClauseSatisfied = True
                    break
                MatchingIdentities = {id(Option) for Option in Matching}
                Escaping = tuple(
                    Option
                    for Option in Mutable[Signal]
                    if id(Option) not in MatchingIdentities
                )
                Unresolved.append((Signal, Escaping))
            if ClauseSatisfied:
                continue
            Escapable = tuple(
                (Signal, Escaping)
                for Signal, Escaping in Unresolved
                if Escaping
            )
            if not Escapable:
                return None
            if len(Escapable) == 1:
                Signal, Escaping = Escapable[0]
                if len(Escaping) != len(Mutable[Signal]):
                    Mutable[Signal] = Escaping
                    Changed = True
    return Mutable

def SelectBinaryExactNoGoodClauses(
    RejectedSets: Iterable[frozenset[tuple[str, str]]],
) -> tuple[frozenset[tuple[str, str]], ...]:
    """Select clauses whose feasibility can be decided by a variable pair."""
    return tuple(
        RejectedSet
        for RejectedSet in RejectedSets
        if len({str(Signal) for Signal, _Fingerprint in RejectedSet}) <= 2
    )

def FindProofQualifiedUniversalNoGoodCore(
    UniversalKeysBySignal: Mapping[
        str, frozenset[tuple[str, str]]
    ],
    RejectedSets: Iterable[frozenset[tuple[str, str]]],
) -> tuple[
    tuple[str, ...], frozenset[tuple[str, str]]
] | None:
    """Return the smallest clause universal to its represented domains."""
    Candidates = []
    for RejectedSet in RejectedSets:
        Clause = frozenset(
            (str(Signal), str(Fingerprint))
            for Signal, Fingerprint in RejectedSet
        )
        Signals = tuple(sorted({Signal for Signal, _ in Clause}))
        if (
            Clause
            and all(Signal in UniversalKeysBySignal for Signal in Signals)
            and all(
                Key in UniversalKeysBySignal[Key[0]]
                for Key in Clause
            )
        ):
            Candidates.append((Signals, Clause))
    if not Candidates:
        return None
    return min(
        Candidates,
        key=lambda Value: (
            len(Value[0]),
            Value[0],
            tuple(sorted(Value[1])),
        ),
    )

def FindProofQualifiedCompleteDomainNoGoodCore(
    Domains: Mapping[str, tuple[Any, ...]],
    RejectedSets: Iterable[frozenset[tuple[str, str]]],
    OptionKeys: Callable[[Any], frozenset[tuple[str, str]]],
) -> tuple[
    tuple[str, ...], frozenset[tuple[str, str]]
] | None:
    """Find a learned clause that every complete-domain assignment contains.

    This is deliberately stronger than observing that a clause rejected the
    last selected assignment.  Every literal in the returned clause must be
    present on every option of its signal's complete, non-empty domain.  The
    clause is therefore an independently checkable UNSAT proof for precisely
    those signals and can be reused as a core without replaying the port CSP.
    """
    UniversalKeysBySignal: dict[
        str, frozenset[tuple[str, str]]
    ] = {}
    for Signal, Values in Domains.items():
        if not Values:
            continue
        KeySets = tuple(
            frozenset(
                (str(KeySignal), str(Fingerprint))
                for KeySignal, Fingerprint in OptionKeys(Value)
                if str(KeySignal) == str(Signal)
            )
            for Value in Values
        )
        UniversalKeysBySignal[str(Signal)] = frozenset.intersection(
            *KeySets
        )
    return FindProofQualifiedUniversalNoGoodCore(
        UniversalKeysBySignal,
        RejectedSets,
    )

def PlanPhysicalGlobalAssignmentAvoidingExactNoGoods(
    CandidateValues: Iterable[tuple[Any, ...]],
    ForbiddenCandidateSets: Iterable[
        frozenset[tuple[str, str]]
    ],
    PlanNative: Callable[[list[tuple[Any, ...]]], Any],
    *,
    DeadlineExpired: Callable[[], bool] = lambda: False,
) -> Any:
    """Find the first complete assignment outside forbidden candidate cores.

    The native assignment API accepts per-candidate domains but no tuple
    no-goods.  The complement of one monotonic core is the union of
    deterministic branches which each exclude one core member.  Recursing on
    a newly found forbidden subset preserves that identity exactly; unary and
    binary learned clauses must not be treated as inert unless they happen to
    equal the whole assignment.
    """
    Values = list(CandidateValues)
    RequiredSignals = frozenset(str(Value[0]) for Value in Values)
    Forbidden = tuple(sorted(
        (
            frozenset(
                (str(Signal), str(CandidateId))
                for Signal, CandidateId in CandidateSet
            )
            for CandidateSet in ForbiddenCandidateSets
            if CandidateSet
        ),
        key=lambda Value: tuple(sorted(Value)),
    ))
    if not Forbidden:
        return PlanNative(Values)

    SeenExclusions: set[frozenset[tuple[str, str]]] = set()
    IncompleteResults: list[Any] = []
    ExpansionCount = 0
    CompletedWork = 0
    DeadlineExceeded = False

    def Search(
        Exclusions: frozenset[tuple[str, str]],
    ) -> Any | None:
        nonlocal ExpansionCount, CompletedWork, DeadlineExceeded
        if Exclusions in SeenExclusions:
            return None
        SeenExclusions.add(Exclusions)
        if DeadlineExpired():
            DeadlineExceeded = True
            return None
        BranchValues = [
            Value
            for Value in Values
            if (str(Value[0]), str(Value[1])) not in Exclusions
        ]
        Result = PlanNative(BranchValues)
        ExpansionCount += int(getattr(Result, "ExpansionCount", 0))
        CompletedWork += int(getattr(Result, "CompletedWork", 0))
        if getattr(Result, "DeadlineExceeded", False):
            DeadlineExceeded = True
            IncompleteResults.append(Result)
            return None
        if not getattr(Result, "Success", False):
            if ShouldGrowAssignmentBudget(Result):
                IncompleteResults.append(Result)
            return None
        Selected = frozenset(
            (str(Signal), str(CandidateId))
            for Signal, CandidateId in Result.SelectedCandidateIds
        )
        if frozenset(Signal for Signal, _CandidateId in Selected) != (
            RequiredSignals
        ):
            return None
        MatchingNoGood = next(
            (Value for Value in Forbidden if Value <= Selected),
            None,
        )
        if MatchingNoGood is None:
            return Result
        for Member in sorted(MatchingNoGood):
            Alternative = Search(frozenset((*Exclusions, Member)))
            if Alternative is not None:
                return Alternative
            if DeadlineExceeded:
                break
        return None

    UnaryExclusions = frozenset(
        next(iter(Clause))
        for Clause in Forbidden
        if len(Clause) == 1
    )
    Result = Search(UnaryExclusions)
    if Result is not None:
        return Result
    if IncompleteResults:
        return IncompleteResults[0]
    return SimpleNamespace(
        Success=False,
        SelectedCandidateIds=(),
        ExpansionCount=ExpansionCount,
        CompletedWork=CompletedWork,
        BudgetExhausted=False,
        DeadlineExceeded=DeadlineExceeded,
        ConflictSignals=tuple(sorted(RequiredSignals)),
        ExactNoGoodDomainExhausted=not DeadlineExceeded,
    )

def ShouldGrowAssignmentBudget(Result: Any) -> bool:
    """Grow MRV work only when Rust stopped at its explicit work ceiling."""
    return bool(
        getattr(Result, "BudgetExhausted", False)
        and not getattr(Result, "DeadlineExceeded", False)
    )

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

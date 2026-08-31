"""Deterministic coarse channel planning for detailed redstone routing."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..Placement.Geometry import (
    BuildPlacementPinAccessWitness,
    PlacementPinAccessWitness,
)
from .Policy import GlobalRoutingPolicy
from .ResourceGraph import (
    FindClaimConflicts,
    FindSelfClaimConflicts,
    RoutingResourceClaims,
    RoutingResourceGraph,
    RoutingResourceId,
    RoutingResourceKind,
    LocalRouteClaim,
)
from .Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)


Position2 = tuple[int, int]
Position3 = tuple[int, int, int]


@dataclass(frozen=True)
class SignalRouteSeed:
    """Pre-owned local tree and unresolved terminals for one signal."""

    Signal: str
    Root: Position3
    LocalClaims: tuple[LocalRouteClaim, ...]
    ConnectedTargets: tuple[Position3, ...]
    UnresolvedTargets: tuple[Position3, ...]
    ContinuationNodes: tuple[Position3, ...]
    PreOwnedResources: frozenset[RoutingResourceId]


def _CountPairClaimConflicts(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> int:
    """Count exact physical conflicts between two candidate owners."""
    Electrical = (First.WireCells & Second.ElectricalCells) | (
        Second.WireCells & First.ElectricalCells
    )
    Air = (First.RequiredAirCells & Second.WireCells) | (
        Second.RequiredAirCells & First.WireCells
    )
    return len(Electrical) + len(Air)


@dataclass(frozen=True)
class NetRoutingProfile:
    """Global-routing importance and geometry for one signal."""

    Signal: str
    Root: Position3
    Targets: tuple[Position3, ...]
    Span: int
    Fanout: int
    RetryCount: int
    Criticality: int
    IsTrunk: bool
    SourceAccessPath: tuple[Position3, ...]
    TargetAccessPaths: dict[Position3, tuple[Position3, ...]]
    Seed: SignalRouteSeed | None = None


@dataclass(frozen=True)
class ChannelPlan:
    """Connected, layer-assigned corridors with concrete resource capacity."""

    Profiles: dict[str, NetRoutingProfile]
    SignalOrder: tuple[str, ...]
    TrunkSignals: frozenset[str]
    Guides: dict[str, frozenset[Position2]]
    CorridorUsage: dict[Position2, int]
    CorridorCosts: dict[Position2, int]
    CorridorCapacity: int
    Layers: dict[str, int]
    ResourceUsage: dict[RoutingResourceId, int]
    ResourceOverflow: dict[RoutingResourceId, int]
    ResourceClaimsBySignal: dict[str, frozenset[RoutingResourceId]]
    SourceAccessTransitions: dict[str, tuple[Position3, ...]]
    TargetAccessTransitions: dict[
        str, dict[Position3, tuple[Position3, ...]]
    ]

    @property
    def IsFeasible(self) -> bool:
        """Return whether the authoritative global plan has zero overflow."""
        return not self.ResourceOverflow


@dataclass(frozen=True)
class RoutingIterationMetrics:
    """Negotiated-routing measurements captured after one pass."""

    Iteration: int
    Stage: str
    ConflictCount: int
    ReroutedNets: int
    AverageLength: float
    BendCount: int
    ViaCount: int
    ConflictSignals: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingCongestionFeedback:
    """A deterministic routing cut returned to cluster placement."""

    Classification: str
    Signals: tuple[str, ...]
    Hotspots: tuple[Position3, ...]
    SaturatedResources: tuple[str, ...] = ()
    OverflowPeak: int = 0

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Classification": self.Classification,
            "Signals": list(self.Signals),
            "Hotspots": [list(Value) for Value in self.Hotspots],
            "SaturatedResources": list(self.SaturatedResources),
            "OverflowPeak": self.OverflowPeak,
        }


@dataclass(frozen=True)
class NegotiatedRoutePlan:
    """Conflict-free route trees produced before final exact validation."""

    SelectedCandidates: dict[str, Any]
    Iterations: tuple[RoutingIterationMetrics, ...]
    ReroutedSignals: tuple[str, ...]
    OverflowProgression: tuple[int, ...]
    CachedNodeCount: int
    CachedEdgeCount: int
    Diagnostics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingStageMetrics:
    """Quality and convergence measurements for a completed routing stage."""

    Stage: str
    NetCount: int
    TotalLength: int
    AverageLength: float
    BendCount: int
    ViaCount: int
    ReroutedNets: int
    ConflictCount: int
    CorridorOverflowPeak: int
    CorridorOverflowCells: int
    Iterations: tuple[RoutingIterationMetrics, ...] = ()
    AccessOverflowPeak: int = 0
    AccessOverflowCells: int = 0


def BuildNetRoutingProfiles(
    Placed: Any,
    RetryCounts: dict[str, int] | None = None,
    AccessLength: int | None = None,
    AccessWitness: PlacementPinAccessWitness | None = None,
) -> dict[str, NetRoutingProfile]:
    """Classify nets while consuming one catalog-derived access witness."""
    RetryCounts = RetryCounts or {}
    AccessLength = (
        DefaultRedstoneRoutingTechnology.AccessLength
        if AccessLength is None
        else AccessLength
    )
    if AccessLength < 1:
        raise ValueError("AccessLength must be positive")
    HasCatalogTransformMetadata = all(
        hasattr(Gate, "Rotation") and hasattr(Gate, "MirrorX")
        for Gate in Placed.PlacedGates
    )
    AccessWitness = AccessWitness or BuildPlacementPinAccessWitness(
        Placed.PlacedGates,
        AccessLength=AccessLength,
        RequireCatalogMatch=HasCatalogTransformMetadata,
    )
    if not AccessWitness.Complete or (
        HasCatalogTransformMetadata and not AccessWitness.CatalogMatched
    ):
        raise ValueError("routing profiles require a complete catalog witness")
    if AccessLength > AccessWitness.AccessLength:
        raise ValueError("routing profile exceeds its frozen access witness")
    ProducerGates = {
        Signal: Gate
        for Gate in Placed.PlacedGates
        if Gate.OutputPin is not None
        for Signal in Gate.Outputs
    }
    Targets: dict[str, list[Position3]] = defaultdict(list)
    TargetAccessPaths: dict[str, dict[Position3, tuple[Position3, ...]]] = defaultdict(dict)
    for Gate in Placed.PlacedGates:
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Selection = AccessWitness.FindSelection(
                str(Signal),
                str(Gate.Name),
                "Target",
                f"Input{InputIndex}",
            )
            Pin = Selection.Terminal
            Targets[Signal].append(Pin)
            TargetAccessPaths[Signal][Pin] = Selection.Path[:AccessLength]

    ClaimsBySignal: dict[str, list[LocalRouteClaim]] = defaultdict(list)
    for Claim in getattr(Placed, "LocalRouteClaims", ()) or ():
        ClaimsBySignal[Claim.Signal].append(Claim)
    LegacyTargets = getattr(Placed, "LocalNetTargets", None) or {}
    FrozenSignals = set((getattr(Placed, "FrozenNetWires", None) or {}).keys())
    RawProfiles: dict[str, NetRoutingProfile] = {}
    for Signal in sorted(Targets):
        Producer = ProducerGates.get(Signal)
        if Producer is None:
            continue
        SourceSelection = AccessWitness.FindSelection(
            str(Signal),
            str(Producer.Name),
            "Source",
            "Output0",
        )
        Root = SourceSelection.Terminal
        SourceAccessPath = SourceSelection.Path[:AccessLength]
        UniqueTargets = tuple(sorted(set(Targets[Signal])))
        SignalClaims = tuple(ClaimsBySignal.get(Signal, ()))
        ConnectedTargets = {
            Target
            for Claim in SignalClaims
            for Target in Claim.ConnectedTargets
        }
        if not SignalClaims:
            ConnectedTargets.update(LegacyTargets.get(Signal, ()))
        UnresolvedTargets = tuple(
            Target for Target in UniqueTargets if Target not in ConnectedTargets
        )
        if Signal in FrozenSignals and not SignalClaims:
            continue
        if not UnresolvedTargets:
            continue
        ContinuationNodes = tuple(sorted({
            Position
            for Claim in SignalClaims
            for Position in (Claim.BoundaryNodes or tuple(Claim.Nodes))
        }))
        PreOwnedResources = frozenset(
            Resource
            for Claim in SignalClaims
            for Resource in Claim.Claims.ResourceIds
        )
        Seed = SignalRouteSeed(
            Signal=Signal,
            Root=Root,
            LocalClaims=SignalClaims,
            ConnectedTargets=tuple(sorted(ConnectedTargets)),
            UnresolvedTargets=UnresolvedTargets,
            ContinuationNodes=ContinuationNodes,
            PreOwnedResources=PreOwnedResources,
        )
        Positions = (Root, *UnresolvedTargets)
        Span = (
            max(Position[0] for Position in Positions)
            - min(Position[0] for Position in Positions)
            + max(Position[2] for Position in Positions)
            - min(Position[2] for Position in Positions)
        )
        Fanout = len(UnresolvedTargets)
        RetryCount = RetryCounts.get(Signal, 0)
        Criticality = Span + Fanout * 8 + RetryCount * 16
        RawProfiles[Signal] = NetRoutingProfile(
            Signal=Signal,
            Root=Root,
            Targets=UnresolvedTargets,
            Span=Span,
            Fanout=Fanout,
            RetryCount=RetryCount,
            Criticality=Criticality,
            IsTrunk=False,
            SourceAccessPath=SourceAccessPath,
            TargetAccessPaths={
                Target: TargetAccessPaths[Signal][Target]
                for Target in UnresolvedTargets
            },
            Seed=Seed,
        )

    if not RawProfiles:
        return {}
    Criticalities = sorted(
        Profile.Criticality for Profile in RawProfiles.values()
    )
    TrunkThreshold = Criticalities[len(Criticalities) // 2]
    return {
        Signal: NetRoutingProfile(
            Signal=Profile.Signal,
            Root=Profile.Root,
            Targets=Profile.Targets,
            Span=Profile.Span,
            Fanout=Profile.Fanout,
            RetryCount=Profile.RetryCount,
            Criticality=Profile.Criticality,
            IsTrunk=(
                Profile.Fanout > 1
                or (
                    Profile.Span >= 8
                    and Profile.Criticality >= TrunkThreshold
                )
            ),
            SourceAccessPath=Profile.SourceAccessPath,
            TargetAccessPaths=Profile.TargetAccessPaths,
            Seed=Profile.Seed,
        )
        for Signal, Profile in RawProfiles.items()
    }


def RasterizeChannelSegment(
    Start: Position2,
    End: Position2,
) -> set[Position2]:
    """Rasterize one axis-aligned channel segment."""
    if Start[0] != End[0] and Start[1] != End[1]:
        raise ValueError("Channel segments must be axis aligned")
    DeltaX = 0 if Start[0] == End[0] else (1 if End[0] > Start[0] else -1)
    DeltaZ = 0 if Start[1] == End[1] else (1 if End[1] > Start[1] else -1)
    Position = Start
    Result = {Position}
    while Position != End:
        Position = (Position[0] + DeltaX, Position[1] + DeltaZ)
        Result.add(Position)
    return Result


def BuildChannelGuide(
    Profile: NetRoutingProfile,
    Axis: str,
    Lane: int,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> frozenset[Position2]:
    """Build one connected trunk with short orthogonal terminal branches."""
    SourceLanding = Technology.AccessLanding(Profile.SourceAccessPath)
    Terminals = (
        (SourceLanding[0], SourceLanding[2]),
        *(
            (Landing[0], Landing[2])
            for Target in Profile.Targets
            for Landing in (
                Technology.AccessLanding(Profile.TargetAccessPaths[Target]),
            )
        ),
    )
    Guide: set[Position2] = set()
    if Axis == "X":
        Minimum = min(Position[0] for Position in Terminals)
        Maximum = max(Position[0] for Position in Terminals)
        Guide.update(RasterizeChannelSegment((Minimum, Lane), (Maximum, Lane)))
        for X, Z in Terminals:
            Guide.update(RasterizeChannelSegment((X, Z), (X, Lane)))
    elif Axis == "Z":
        Minimum = min(Position[1] for Position in Terminals)
        Maximum = max(Position[1] for Position in Terminals)
        Guide.update(RasterizeChannelSegment((Lane, Minimum), (Lane, Maximum)))
        for X, Z in Terminals:
            Guide.update(RasterizeChannelSegment((X, Z), (Lane, Z)))
    else:
        raise ValueError(f"Unknown channel axis: {Axis}")
    return frozenset(Guide)


def CandidateLanes(
    Center: int,
    CandidateCount: int = 9,
    TrackPitch: int | None = None,
) -> tuple[int, ...]:
    """Return deterministic electrically separated lanes around a center."""
    if CandidateCount < 1:
        raise ValueError("CandidateCount must be positive")
    if TrackPitch is None:
        TrackPitch = DefaultRedstoneRoutingTechnology.TrackPitch
    if TrackPitch < 2:
        raise ValueError("TrackPitch must preserve electrical isolation")
    Result = [Center]
    Offset = TrackPitch
    while len(Result) < CandidateCount:
        Result.extend((Center - Offset, Center + Offset))
        Offset += TrackPitch
    return tuple(Result[:CandidateCount])


def HasRequiredRepeaterSites(
    Profile: NetRoutingProfile,
    Guide: frozenset[Position2],
    RoutingY: int,
    Technology: RedstoneRoutingTechnology,
) -> bool:
    """Check refresh capacity before a global candidate can own resources."""
    Eligible = {
        (X, Z)
        for X, Z in Guide
        if (
            ((X - 1, Z) in Guide and (X + 1, Z) in Guide)
            != ((X, Z - 1) in Guide and (X, Z + 1) in Guide)
        )
    }
    SourceLanding = Technology.AccessLanding(Profile.SourceAccessPath)
    Root = (SourceLanding[0], SourceLanding[2])
    for Target in Profile.Targets:
        TargetLanding = Technology.AccessLanding(
            Profile.TargetAccessPaths[Target]
        )
        End = (TargetLanding[0], TargetLanding[2])
        Parents: dict[Position2, Position2 | None] = {Root: None}
        Pending = deque((Root,))
        while Pending and End not in Parents:
            X, Z = Pending.popleft()
            for Neighbor in ((X + 1, Z), (X - 1, Z), (X, Z + 1), (X, Z - 1)):
                if Neighbor in Guide and Neighbor not in Parents:
                    Parents[Neighbor] = (X, Z)
                    Pending.append(Neighbor)
        if End not in Parents:
            return False
        Path = []
        Current: Position2 | None = End
        while Current is not None:
            Path.append(Current)
            Current = Parents[Current]
        Path.reverse()
        LastRefresh = 0
        TargetCost = abs(RoutingY - Target[1]) + 1
        if any(Position in Eligible for Position in Path[1:-1]):
            TargetCost = max(
                TargetCost,
                Technology.MaximumUnrefreshedDustLength - (len(Path) - 1) + 1,
            )
        while (
            len(Path) - 1 + TargetCost - LastRefresh
            > Technology.MaximumUnrefreshedDustLength
        ):
            MaximumIndex = min(
                len(Path) - 2,
                LastRefresh + Technology.ReservedRepeaterInterval,
            )
            Candidates = [
                Index
                for Index in range(max(1, LastRefresh + 1), MaximumIndex + 1)
                if Path[Index] in Eligible
            ]
            if not Candidates:
                return False
            PreferredIndex = min(
                MaximumIndex,
                LastRefresh + Technology.ReservedRepeaterInterval,
            )
            LastRefresh = min(
                Candidates,
                key=lambda Index: (abs(Index - PreferredIndex), Index),
            )
    return True


def _AccessTransitionCandidates(
    AccessPath: tuple[Position3, ...],
    CandidatePositions: set[Position3],
    AllowedColumns: frozenset[Position2],
    ResourceGraph: RoutingResourceGraph,
    AllowedAccess: frozenset[Position3],
    ReachabilityCache: dict[
        tuple[tuple[Position3, ...], int, Position3],
        tuple[dict[Position3, Position3 | None], dict[Position3, int]],
    ],
) -> tuple[tuple[Position3, ...], ...]:
    """Find bounded graph paths from a pin escape to exact backbone nodes."""
    MinimumY = min(Position[1] for Position in AccessPath)
    MaximumY = max(Position[1] for Position in CandidatePositions)
    AccessColumns = {(X, Z) for X, _Y, Z in AccessPath}
    Results = []
    for Start in reversed(AccessPath):
        CacheKey = (AccessPath, MaximumY, Start)
        Cached = ReachabilityCache.get(CacheKey)
        if Cached is None:
            Parents: dict[Position3, Position3 | None] = {Start: None}
            Distances = {Start: 0}
            Pending = deque((Start,))
            while Pending:
                Position = Pending.popleft()
                for Neighbor in sorted(ResourceGraph.Technology.NeighborPositions(Position)):
                    if Neighbor in Parents:
                        continue
                    if Neighbor not in AccessPath and (
                        min(
                            abs(Neighbor[0] - AccessX) + abs(Neighbor[2] - AccessZ)
                            for AccessX, AccessZ in AccessColumns
                        ) > 8
                        or not MinimumY <= Neighbor[1] <= MaximumY
                        or not ResourceGraph.IsLegalNode(
                            Neighbor, frozenset(AccessPath)
                        )
                    ):
                        continue
                    Primitive = ResourceGraph.BuildPrimitive(Position, Neighbor)
                    if Primitive is None:
                        continue
                    Parents[Neighbor] = Position
                    Distances[Neighbor] = Distances[Position] + 1
                    Pending.append(Neighbor)
            ReachabilityCache[CacheKey] = (Parents, Distances)
        else:
            Parents, Distances = Cached
        ReachableGoals = CandidatePositions & set(Parents)
        for Goal in sorted(ReachableGoals, key=lambda Value: (Distances[Value], Value)):
            Path = []
            Current: Position3 | None = Goal
            while Current is not None:
                Path.append(Current)
                Current = Parents[Current]
            Candidate = tuple(reversed(Path))
            if all(
                (Position[0], Position[2]) in AllowedColumns
                or Position in AccessPath
                for Position in Candidate
            ) and Candidate not in Results:
                Results.append(Candidate)
            if len(Results) >= 8:
                return tuple(Results)
    return tuple(Results)


def _PinAccessTransitionOptions(
    Profile: NetRoutingProfile,
    RoutingY: int,
    CandidatePositions: set[Position3],
    ResourceGraph: RoutingResourceGraph,
    ReachabilityCache: dict[
        tuple[tuple[Position3, ...], int, Position3],
        tuple[dict[Position3, Position3 | None], dict[Position3, int]],
    ],
    ForeignClaims: dict[str, RoutingResourceClaims],
) -> tuple[
    tuple[
        tuple[Position3, ...],
        dict[Position3, tuple[Position3, ...]],
        RoutingResourceClaims,
    ],
    ...,
]:
    """Enumerate exact source/sink transition reservations for one candidate."""
    AccessPositions = frozenset(
        {
            *Profile.SourceAccessPath,
            *(
                Position
                for Path in Profile.TargetAccessPaths.values()
                for Position in Path
            ),
        }
    )
    AllowedAccess = frozenset(set(AccessPositions) | CandidatePositions)
    CandidateColumns = {(X, Z) for X, _Y, Z in CandidatePositions}
    AllowedColumns = frozenset(
        (X + DeltaX, Z + DeltaZ)
        for X, Z in CandidateColumns
        for DeltaX in range(-8, 9)
        for DeltaZ in range(-8, 9)
        if abs(DeltaX) + abs(DeltaZ) <= 8
    )
    Terminals = (
        (None, Profile.SourceAccessPath),
        *((Target, Profile.TargetAccessPaths[Target]) for Target in Profile.Targets),
    )
    CandidatesByTerminal = []
    for _Target, AccessPath in Terminals:
        Candidates = _AccessTransitionCandidates(
            AccessPath,
            CandidatePositions,
            AllowedColumns,
            ResourceGraph,
            AllowedAccess,
            ReachabilityCache,
        )
        if not Candidates:
            return ()
        CandidatesByTerminal.append(
            tuple(sorted(Candidates, key=lambda Path: (len(Path), Path)))
        )

    Results = []
    ExpansionCount = 0

    def SelectTransitions(
        Index: int,
        Selected: list[tuple[Position3, ...]],
    ) -> None:
        nonlocal ExpansionCount
        if len(Results) >= 4 or ExpansionCount >= 128:
            return
        if Index == len(Terminals):
            OwnershipPositions = set(CandidatePositions) | set(AccessPositions)
            for Path in Selected:
                OwnershipPositions.update(Path)
            Claims = ResourceGraph.BuildRouteClaims(OwnershipPositions)
            Result = (
                Selected[0],
                {
                    Target: Selected[TargetIndex + 1]
                    for TargetIndex, Target in enumerate(Profile.Targets)
                },
                Claims,
            )
            if Result not in Results:
                Results.append(Result)
            return
        for Path in CandidatesByTerminal[Index]:
            ExpansionCount += 1
            PartialPositions = set(CandidatePositions) | set(AccessPositions)
            for SelectedPath in Selected:
                PartialPositions.update(SelectedPath)
            PartialPositions.update(Path)
            if FindSelfClaimConflicts(
                {
                    Profile.Signal: ResourceGraph.BuildRouteClaims(
                        PartialPositions
                    )
                }
            ):
                continue
            SelectTransitions(Index + 1, [*Selected, Path])

    SelectTransitions(0, [])
    return tuple(
        sorted(
            Results,
            key=lambda Value: (
                len(
                    FindClaimConflicts(
                        {**ForeignClaims, Profile.Signal: Value[2]}
                    )
                ),
                Value[0],
                tuple(sorted(Value[1].items())),
            ),
        )
    )


def CountRouteBends(Positions: set[Position3]) -> int:
    """Count cells whose same-net neighbors form a direction change."""
    Bends = 0
    for X, Y, Z in Positions:
        Directions = {
            (NeighborX - X, NeighborY - Y, NeighborZ - Z)
            for NeighborX, NeighborY, NeighborZ in (
                (X + 1, Y, Z),
                (X - 1, Y, Z),
                (X, Y, Z + 1),
                (X, Y, Z - 1),
                (X + 1, Y + 1, Z),
                (X - 1, Y + 1, Z),
                (X, Y + 1, Z + 1),
                (X, Y + 1, Z - 1),
                (X + 1, Y - 1, Z),
                (X - 1, Y - 1, Z),
                (X, Y - 1, Z + 1),
                (X, Y - 1, Z - 1),
            )
            if (NeighborX, NeighborY, NeighborZ) in Positions
        }
        Axes = {
            (
                1 if DeltaX else 0,
                1 if DeltaY else 0,
                1 if DeltaZ else 0,
            )
            for DeltaX, DeltaY, DeltaZ in Directions
        }
        if len(Axes) > 1:
            Bends += 1
    return Bends


def MeasureRoutingStage(
    Stage: str,
    NetWires: dict[str, set[Position3]],
    Plan: ChannelPlan | None,
    ReroutedNets: int = 0,
    ConflictCount: int = 0,
    Iterations: tuple[RoutingIterationMetrics, ...] = (),
) -> RoutingStageMetrics:
    """Measure routed length, shape, vias, and corridor overflow."""
    Lengths = [len(Positions) for Positions in NetWires.values()]
    AccessColumns = set()
    if Plan is not None:
        AccessColumns.update(
            (X, Z)
            for Path in getattr(Plan, "SourceAccessTransitions", {}).values()
            for X, _Y, Z in Path
        )
        AccessColumns.update(
            (X, Z)
            for Values in getattr(Plan, "TargetAccessTransitions", {}).values()
            for Path in Values.values()
            for X, _Y, Z in Path
        )
    Usage: Counter[Position2] = Counter()
    AccessUsage: Counter[Position2] = Counter()
    for Positions in NetWires.values():
        Usage.update(
            (X, Z) for X, _Y, Z in Positions if (X, Z) not in AccessColumns
        )
        AccessUsage.update(
            (X, Z) for X, _Y, Z in Positions if (X, Z) in AccessColumns
        )
    Capacity = Plan.CorridorCapacity if Plan is not None else 1
    Overflow = {
        Position: Count - Capacity
        for Position, Count in Usage.items()
        if Count > Capacity
    }
    AccessOverflow = {
        Position: Count - Capacity
        for Position, Count in AccessUsage.items()
        if Count > Capacity
    }
    return RoutingStageMetrics(
        Stage=Stage,
        NetCount=len(NetWires),
        TotalLength=sum(Lengths),
        AverageLength=(sum(Lengths) / len(Lengths) if Lengths else 0.0),
        BendCount=sum(CountRouteBends(Positions) for Positions in NetWires.values()),
        ViaCount=sum(
            1
            for Positions in NetWires.values()
            for X, Y, Z in Positions
            if any(
                Neighbor in Positions
                for Neighbor in (
                    (X + 1, Y + 1, Z),
                    (X - 1, Y + 1, Z),
                    (X, Y + 1, Z + 1),
                    (X, Y + 1, Z - 1),
                )
            )
        ),
        ReroutedNets=ReroutedNets,
        ConflictCount=ConflictCount,
        CorridorOverflowPeak=max(Overflow.values(), default=0),
        CorridorOverflowCells=len(Overflow),
        Iterations=Iterations,
        AccessOverflowPeak=max(AccessOverflow.values(), default=0),
        AccessOverflowCells=len(AccessOverflow),
    )

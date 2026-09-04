"""Packed-cluster models and bounded inter-cluster channel construction."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    replace,
)
from collections import (
    deque,
)
from hashlib import (
    sha256,
)
from itertools import (
    combinations,
    product,
)
from typing import (
    Any,
    Iterable,
)
from PhysicalDesign.Geometry.Rotation import RotatedCellSize
from PhysicalDesign.Geometry.Placement import BuildPlacedGate, PlacedGate, PlacedDesign
from PhysicalDesign.Placement.PreRouteInterface import DerivedPerimeterSlotAssignment, DerivedPerimeterSlotDomain
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Contracts.Placement import InterClusterChannelLane, InterClusterRoutingChannel
from PhysicalDesign.Redstone.Rules.Geometry import BuildPlacedCellGeometry
from PhysicalDesign.Redstone.Rules.Geometry import ValidatePlacedCellElectricalIsolation
from PhysicalDesign.Resources.ResourceGraph import LocalRouteClaim, NormalizeRoutingEdge, RoutingResourceClaims, RoutingResourceId, RoutingReservation
from .Channels import (
    BuildClusterBoundaryBundles,
    BuildClusterBoundaryLeaseRequests,
)
@dataclass(frozen=True)
class PackedNandCluster:
    """Physical packing metadata; members remain independent NAND cells."""

    ClusterId: int
    MemberNands: tuple[str, ...]
    BoundarySignals: tuple[str, ...]
    InternalSignals: tuple[str, ...]
    RelativePlacements: dict[str, tuple[int, int, int, bool]]
    DirectConnections: tuple[str, ...]
    LocalClaimSignals: tuple[str, ...] = ()
    BoundaryTerminals: tuple[tuple[int, int, int], ...] = ()
    ExactLocalRoutingBlocks: int = 0
    GlobalEntrances: int = 0
    RejectionReasons: tuple[str, ...] = ()
    StructuralSignature: str = ""
    ReusedFromClusterId: int | None = None
    StructuralMapping: dict[str, str] | None = None
    StackId: int | None = None
    StackLevel: int = 0
    BaseY: int = 1
    BoundaryDemand: dict[str, int] | None = None
    EstimatedCorridorLanes: int = 0
    LocalClaimCoverage: float = 0.0
    BoundaryDemandRecords: tuple[BoundaryDemandRecord, ...] = ()
    BoundaryCapacityRecords: tuple[BoundaryCapacityRecord, ...] = ()
    BoundaryOverflow: int = 0
    PinScarcityCount: int = 0
    LegalEscapeCandidateCounts: tuple[tuple[str, int], ...] = ()
    OrientationRotation: int = 0
    OrientationMirrorX: bool = False

    @property
    def SingleCandidateBoundarySignals(self) -> tuple[str, ...]:
        """Return boundary signals with only one exact legal escape slot."""
        return tuple(
            Signal
            for Signal, Count in self.LegalEscapeCandidateCounts
            if Count == 1
        )

@dataclass(frozen=True)
class ClusterLayoutVariant:
    """One exact rigid transform of a packed-cluster layout."""

    Rotation: int
    MirrorX: bool
    Positions: dict[str, tuple[int, int]]
    Rotations: dict[str, int]
    Mirrors: dict[str, bool]
    Width: int
    Depth: int
    ActualGeometry: dict[str, frozenset[tuple[int, int, int]]]
    ElectricalGeometry: dict[str, frozenset[tuple[int, int, int]]]
    RejectionReason: str | None = None

    @property
    def IsLegal(self) -> bool:
        return self.RejectionReason is None

@dataclass(frozen=True)
class PackedNandClusterCandidate:
    """Transactional packed placement with locally owned route material."""

    ClusterId: int
    Placements: dict[str, tuple[int, int, int, bool]]
    LocalClaims: tuple[LocalRouteClaim, ...]
    BoundaryTerminals: tuple[tuple[int, int, int], ...]
    RoutingOwnedBlocks: int
    RawDustBlocks: int
    SupportBlocks: int
    Footprint: int
    RejectionReasons: tuple[str, ...] = ()
    BoundaryDemandRecords: tuple[BoundaryDemandRecord, ...] = ()
    BoundaryCapacityRecords: tuple[BoundaryCapacityRecord, ...] = ()
    BoundaryOverflow: int = 0
    LocalClaimCoverage: float = 0.0

@dataclass(frozen=True)
class ClusterLocalRouteTemplate:
    """Immutable cluster-relative local routing retained across cut epochs."""

    ClusterId: int
    StructuralSignature: str
    Rotation: int
    MirrorX: bool
    Origin: tuple[int, int, int]
    LocalClaimFingerprint: str
    BoundaryTerminalFingerprint: str
    ClaimCount: int
    BoundaryTerminalCount: int

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ClusterId": self.ClusterId,
            "StructuralSignature": self.StructuralSignature,
            "Rotation": self.Rotation,
            "MirrorX": self.MirrorX,
            "Origin": list(self.Origin),
            "LocalClaimFingerprint": self.LocalClaimFingerprint,
            "BoundaryTerminalFingerprint": self.BoundaryTerminalFingerprint,
            "ClaimCount": self.ClaimCount,
            "BoundaryTerminalCount": self.BoundaryTerminalCount,
        }

@dataclass(frozen=True)
class ClusterLocalRouteTemplateCacheEntry:
    """One immutable, translation-safe internal cluster route template."""

    CacheKey: tuple[object, ...]
    Origin: tuple[int, int, int]
    Claims: tuple[LocalRouteClaim, ...]
    LocalClaimFingerprint: str

def TranslateClusterLocalRouteClaim(
    Claim: LocalRouteClaim,
    Delta: tuple[int, int, int],
) -> LocalRouteClaim:
    """Translate an immutable internal claim without changing its topology."""
    def Translate(Position: tuple[int, int, int]) -> tuple[int, int, int]:
        return tuple(
            Position[Index] + Delta[Index]
            for Index in range(3)
        )

    def TranslateClaims(Claims: RoutingResourceClaims) -> RoutingResourceClaims:
        return RoutingResourceClaims(
            WireCells=frozenset(map(Translate, Claims.WireCells)),
            SupportCells=frozenset(map(Translate, Claims.SupportCells)),
            RequiredAirCells=frozenset(map(Translate, Claims.RequiredAirCells)),
            ElectricalCells=frozenset(map(Translate, Claims.ElectricalCells)),
        )

    return LocalRouteClaim(
        Signal=Claim.Signal,
        ClusterId=Claim.ClusterId,
        Root=Translate(Claim.Root),
        ConnectedTargets=tuple(map(Translate, Claim.ConnectedTargets)),
        BoundaryNodes=tuple(map(Translate, Claim.BoundaryNodes)),
        Nodes=frozenset(map(Translate, Claim.Nodes)),
        Edges=frozenset(
            NormalizeRoutingEdge(Translate(First), Translate(Second))
            for First, Second in Claim.Edges
        ),
        Claims=TranslateClaims(Claim.Claims),
        RepeaterReservations=tuple(
            RoutingReservation(
                Signal=Reservation.Signal,
                Resource=RoutingResourceId(
                    Reservation.Resource.Kind,
                    Translate(Reservation.Resource.Position),
                ),
                Position=Translate(Reservation.Position),
                Purpose=Reservation.Purpose,
                InputFacing=Reservation.InputFacing,
            )
            for Reservation in Claim.RepeaterReservations
        ),
        ExactRouteSignalBlocks=Claim.ExactRouteSignalBlocks,
        ExactRouteRefreshBlocks=Claim.ExactRouteRefreshBlocks,
        ExactRouteSupportBlocks=Claim.ExactRouteSupportBlocks,
    )

@dataclass(frozen=True)
class PcbPlacement:
    """Weighted placement plus global routing metadata."""

    Placed: PlacedDesign
    Clusters: tuple[tuple[str, ...], ...]
    SignalOrder: tuple[str, ...]
    LayerCount: int
    PackedClusters: tuple[PackedNandCluster, ...] = ()
    ClusterBoundaryLeaseRequests: tuple[ClusterBoundaryLeaseRequest, ...] = ()
    ClusterLocalRouteTemplates: tuple[ClusterLocalRouteTemplate, ...] = ()
    ClusterBoundaryLeaseVariant: int = 0
    CompleteClusterInterfaceAccess: bool = False
    MandatoryAccessPreScreenProfile: (
        "MandatoryAccessConflictProfile | None"
    ) = None
    InterClusterRoutingChannel: InterClusterRoutingChannel | None = None
    PlacementAccessFabric: Any | None = None
    PlacementAccessAssignment: Any | None = None
    DerivedPerimeterSlotDomain: Any | None = None
    DerivedPerimeterSlotAssignment: Any | None = None
    # A compact single-component candidate may publish previously materialized
    # complete local trees as alternatives of its immutable pre-route access
    # problem.  They are not live placement ownership until that problem
    # selects them; keeping the source claims here avoids a post-failure
    # release/rebuild cycle.
    DerivedLocalRouteClaims: tuple[Any, ...] = ()
    ComponentGraph: Any | None = None

def BuildPhysicalClusterBoundaryLeaseRequests(
    Source: PcbPlacement,
) -> tuple[ClusterBoundaryLeaseRequest, ...]:
    """Materialize explicit interfaces from committed physical clusters."""
    Existing = tuple(Source.ClusterBoundaryLeaseRequests)
    GateByName = {
        Gate.Name: Gate for Gate in Source.Placed.PlacedGates
    }
    PhysicalOrigins = {
        ClusterIndex: (
            min(GateByName[Name].X for Name in Names if Name in GateByName),
            min(GateByName[Name].Z for Name in Names if Name in GateByName),
        )
        for ClusterIndex, Names in enumerate(Source.Clusters)
        if any(Name in GateByName for Name in Names)
    }
    Generated = BuildClusterBoundaryLeaseRequests(
        BuildClusterBoundaryBundles(
            Source.Placed.Module,
            Source.Clusters,
        ),
        PhysicalOrigins,
        Module=Source.Placed.Module,
        Clusters=Source.Clusters,
        PlacedGates=Source.Placed.PlacedGates,
        IncludePrimaryTerminals=True,
    )
    RequestsByIdentity = {
        (
            str(Value.Signal),
            int(Value.SourceCluster),
            int(Value.TargetCluster),
            Value.SourceTerminal,
            tuple(Value.TargetTerminals),
        ): Value
        for Value in (*Existing, *Generated)
    }
    return tuple(
        RequestsByIdentity[Key]
        for Key in sorted(
            RequestsByIdentity,
            key=repr,
        )
    )

def BuildBoundedInterClusterRoutingChannel(
    Source: PcbPlacement,
    *,
    TrackPitch: int | None = None,
    MaximumAffectedClusters: int = 3,
    MaximumBoundaryStrips: int = 2,
    RoutingLayerCount: int = 3,
    RequiredComponentGateNames: Iterable[str] = (),
    ForcedAffectedClusters: tuple[int, ...] | None = None,
    ForcedRoot: int | None = None,
    ForcedAxisPattern: tuple[str, ...] | None = None,
    ChannelClearanceTracks: int = 0,
    ChannelTopologyVariant: int = 0,
) -> PcbPlacement:
    """Insert one bounded physical channel through the densest interface.

    This is an architectural placement transform, not router feedback.  It
    shifts only a connected component of at most three packed clusters,
    translates their immutable local claims, and exposes ordinary
    capacity-one lane cells on the existing routing layers.
    """
    TrackPitch = (
        DefaultRedstoneRoutingTechnology.TrackPitch
        if TrackPitch is None
        else int(TrackPitch)
    )
    if TrackPitch < 1:
        raise ValueError("inter-cluster channel pitch must be positive")
    if MaximumAffectedClusters < 2 or MaximumAffectedClusters > 3:
        raise ValueError("inter-cluster channel supports two or three clusters")
    if MaximumBoundaryStrips < 1 or MaximumBoundaryStrips > 2:
        raise ValueError("inter-cluster channel supports one or two strips")
    if RoutingLayerCount < 1:
        raise ValueError("inter-cluster channel requires routing layers")
    if ChannelClearanceTracks < 0 or ChannelClearanceTracks > 1:
        raise ValueError("channel clearance tracks must be zero or one")
    if ChannelTopologyVariant < 0:
        raise ValueError("channel topology variant must be non-negative")
    if Source.InterClusterRoutingChannel is not None:
        return Source
    RequiredComponentGateNameSet = frozenset(
        str(Name) for Name in RequiredComponentGateNames
    )

    GateByName = {
        Gate.Name: Gate for Gate in Source.Placed.PlacedGates
    }
    ClusterGates = {
        ClusterIndex: tuple(
            GateByName[Name]
            for Name in Names
            if Name in GateByName
        )
        for ClusterIndex, Names in enumerate(Source.Clusters)
    }
    ClusterGates = {
        ClusterIndex: Gates
        for ClusterIndex, Gates in ClusterGates.items()
        if Gates
    }
    AllBoundaryRequests = (
        BuildPhysicalClusterBoundaryLeaseRequests(Source)
    )
    BoundaryRequests = tuple(
        Request
        for Request in AllBoundaryRequests
        if (
            Request.SourceCluster in ClusterGates
            and Request.TargetCluster in ClusterGates
            and Request.SourceCluster != Request.TargetCluster
        )
    )
    if not BoundaryRequests:
        raise ValueError(
            "dense interface has no inter-cluster boundary requests"
        )

    EdgeDemand: dict[tuple[int, int], int] = {}
    for Request in BoundaryRequests:
        Edge = tuple(sorted((
            int(Request.SourceCluster),
            int(Request.TargetCluster),
        )))
        EdgeDemand[Edge] = EdgeDemand.get(Edge, 0) + (
            1 + len(Request.TargetTerminals)
        )
    LogicalComponentGraph = (
        Source.ComponentGraph or Source.Placed.ComponentGraph
    )
    LogicalComponentByGate = (
        dict(LogicalComponentGraph.GateToComponent)
        if (
            LogicalComponentGraph is not None
            and LogicalComponentGraph.Hierarchical
        )
        else {}
    )
    LogicalClustersByComponent: dict[int, set[int]] = {}
    MixedLogicalClusters: set[int] = set()
    LogicalComponentByCluster: dict[int, int] = {}
    for ClusterIndex, Gates in ClusterGates.items():
        ComponentIds = {
            LogicalComponentByGate[Gate.Name]
            for Gate in Gates
            if Gate.Name in LogicalComponentByGate
        }
        if len(ComponentIds) != 1:
            MixedLogicalClusters.add(ClusterIndex)
            continue
        ComponentId = next(iter(ComponentIds))
        LogicalComponentByCluster[ClusterIndex] = ComponentId
        LogicalClustersByComponent.setdefault(
            ComponentId, set()
        ).add(ClusterIndex)
    AlignedLogicalComponents = {
        frozenset(ClusterIndexes): ComponentId
        for ComponentId, ClusterIndexes
        in LogicalClustersByComponent.items()
        if (
            2 <= len(ClusterIndexes) <= MaximumAffectedClusters
            and not (ClusterIndexes & MixedLogicalClusters)
        )
    }
    def LogicalComponentForClusterSet(
        ClusterSet: frozenset[int],
    ) -> int | None:
        ComponentIds = {
            LogicalComponentByCluster[Cluster]
            for Cluster in ClusterSet
            if Cluster in LogicalComponentByCluster
        }
        return (
            next(iter(ComponentIds))
            if (
                len(ComponentIds) == 1
                and ClusterSet
                == frozenset(LogicalClustersByComponent.get(
                    next(iter(ComponentIds)),
                    (),
                ))
                and frozenset(
                    Gate.Name
                    for Cluster in ClusterSet
                    for Gate in ClusterGates[Cluster]
                    if Gate.Name in LogicalComponentByGate
                )
                == LogicalGateNamesByComponent.get(
                    next(iter(ComponentIds)),
                    frozenset(),
                )
                and len(ComponentIds) == len({
                    LogicalComponentByCluster.get(Cluster)
                    for Cluster in ClusterSet
                })
                and not (ClusterSet & MixedLogicalClusters)
            )
            else None
        )
    LogicalComponentBenefits = {
        Value.ComponentId: (
            len(Value.BoundarySignals),
            -Value.QualifyingReconvergentCutCount,
            -len(Value.GateNames),
        )
        for Value in getattr(
            LogicalComponentGraph,
            "Components",
            (),
        )
    }
    LogicalGateNamesByComponent = {
        Value.ComponentId: frozenset(Value.GateNames)
        for Value in getattr(
            LogicalComponentGraph,
            "Components",
            (),
        )
    }
    CandidateComponents: list[
        tuple[tuple[object, ...], tuple[int, ...]]
    ] = []
    ClusterIndexes = tuple(sorted(ClusterGates))
    for ComponentSize in range(
        min(MaximumAffectedClusters, len(ClusterIndexes)),
        1,
        -1,
    ):
        for Component in combinations(ClusterIndexes, ComponentSize):
            ComponentSet = frozenset(Component)
            ComponentEdges = tuple(
                Edge
                for Edge in EdgeDemand
                if set(Edge) <= ComponentSet
            )
            if len(ComponentEdges) < ComponentSize - 1:
                continue
            Visited = {Component[0]}
            while True:
                Expanded = {
                    Value
                    for Edge in ComponentEdges
                    if set(Edge) & Visited
                    for Value in Edge
                }
                if Expanded <= Visited:
                    break
                Visited.update(Expanded)
            if Visited != set(Component):
                continue
            ComponentGateNames = frozenset(
                Gate.Name
                for Cluster in ComponentSet
                for Gate in ClusterGates[Cluster]
            )
            RequiredGateDomainCovered = bool(
                RequiredComponentGateNameSet
                and RequiredComponentGateNameSet.issubset(
                    ComponentGateNames
                )
            )
            if (
                LogicalComponentGraph is not None
                and LogicalComponentGraph.Hierarchical
                and LogicalComponentForClusterSet(ComponentSet) is None
                and not RequiredGateDomainCovered
            ):
                continue
            StructuralSignatures = tuple(sorted(
                (
                    Source.PackedClusters[Index].StructuralSignature
                    if Index < len(Source.PackedClusters)
                    else str(len(Source.Clusters[Index]))
                )
                for Index in Component
            ))
            CandidateComponents.append((
                (
                    0
                    if LogicalComponentForClusterSet(ComponentSet)
                    is not None
                    else 1,
                    LogicalComponentBenefits.get(
                        LogicalComponentForClusterSet(ComponentSet),
                        (1 << 30, 0, 0),
                    ),
                    -sum(EdgeDemand[Edge] for Edge in ComponentEdges),
                    -ComponentSize,
                    StructuralSignatures,
                    Component,
                ),
                Component,
            ))
    if not CandidateComponents:
        raise ValueError(
            "no connected two-or-three-cluster channel component"
        )
    RankedComponents = tuple(sorted(CandidateComponents))
    if RequiredComponentGateNameSet:
        RankedComponents = tuple(
            Value
            for Value in RankedComponents
            if RequiredComponentGateNameSet.issubset(frozenset(
                Gate.Name
                for Cluster in Value[1]
                for Gate in ClusterGates[Cluster]
            ))
        )
        if not RankedComponents:
            raise ValueError(
                "no legal connected channel preserves the required gate domain"
            )
    if ForcedAffectedClusters is None:
        _ComponentScore, AffectedClusters = RankedComponents[0]
    else:
        ForcedClusterSet = frozenset(map(int, ForcedAffectedClusters))
        ForcedMatches = tuple(
            Value
            for Value in RankedComponents
            if frozenset(Value[1]) == ForcedClusterSet
        )
        if not ForcedMatches:
            raise ValueError(
                "forced channel component is not a legal connected component"
            )
        _ComponentScore, AffectedClusters = ForcedMatches[0]
    AffectedSet = frozenset(AffectedClusters)
    ComponentId = LogicalComponentForClusterSet(AffectedSet)
    TopologyComponent = (
        next(
            (
                Value
                for Value in LogicalComponentGraph.Components
                if Value.ComponentId == ComponentId
            ),
            None,
        )
        if (
            LogicalComponentGraph is not None
            and ComponentId is not None
        )
        else None
    )

    def Envelope(
        Gates: Iterable[PlacedGate],
    ) -> tuple[int, int, int, int]:
        Values = tuple(Gates)
        return (
            min(Gate.X for Gate in Values),
            min(Gate.Z for Gate in Values),
            max(
                Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
                for Gate in Values
            ),
            max(
                Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
                for Gate in Values
            ),
        )

    Envelopes = {
        Cluster: Envelope(ClusterGates[Cluster])
        for Cluster in AffectedClusters
    }
    Centers = {
        Cluster: (
            (Bounds[0] + Bounds[2]) // 2,
            (Bounds[1] + Bounds[3]) // 2,
        )
        for Cluster, Bounds in Envelopes.items()
    }
    ComponentEdges = tuple(
        Edge
        for Edge in EdgeDemand
        if set(Edge) <= AffectedSet
    )
    RankedEdges = sorted(
        ComponentEdges,
        key=lambda Edge: (
            -EdgeDemand[Edge],
            abs(Centers[Edge[0]][0] - Centers[Edge[1]][0])
            + abs(Centers[Edge[0]][1] - Centers[Edge[1]][1]),
            Edge,
        ),
    )
    Parents = {Cluster: Cluster for Cluster in AffectedClusters}

    def Find(Value: int) -> int:
        while Parents[Value] != Value:
            Parents[Value] = Parents[Parents[Value]]
            Value = Parents[Value]
        return Value

    TreeEdges: list[tuple[int, int]] = []
    for First, Second in RankedEdges:
        FirstRoot = Find(First)
        SecondRoot = Find(Second)
        if FirstRoot == SecondRoot:
            continue
        Parents[max(FirstRoot, SecondRoot)] = min(FirstRoot, SecondRoot)
        TreeEdges.append((First, Second))
        if len(TreeEdges) == len(AffectedClusters) - 1:
            break
    if len(TreeEdges) != len(AffectedClusters) - 1:
        raise ValueError("channel component spanning tree is incomplete")
    TreeEdges = TreeEdges[:MaximumBoundaryStrips]

    Adjacency: dict[int, list[int]] = {
        Cluster: [] for Cluster in AffectedClusters
    }
    for First, Second in TreeEdges:
        Adjacency[First].append(Second)
        Adjacency[Second].append(First)
    RankedRoots = tuple(sorted(
        AffectedClusters,
        key=lambda Cluster: (
            Source.PackedClusters[Cluster].StructuralSignature
            if Cluster < len(Source.PackedClusters)
            else "",
            Centers[Cluster],
            Cluster,
        ),
    ))
    if ForcedRoot is None:
        RootFailures = []
        RemainingTopologyVariants = ChannelTopologyVariant
        PreferredAxes = tuple(
            (
                "X"
                if abs(
                    Centers[Edge[0]][0] - Centers[Edge[1]][0]
                ) >= abs(
                    Centers[Edge[0]][1] - Centers[Edge[1]][1]
                )
                else "Z"
            )
            for Edge in TreeEdges
        )
        AxisPatterns = tuple(sorted(
            product(("X", "Z"), repeat=len(TreeEdges)),
            key=lambda Pattern: (
                sum(
                    Axis != PreferredAxes[Index]
                    for Index, Axis in enumerate(Pattern)
                ),
                Pattern,
            ),
        ))
        for RootCandidate in RankedRoots:
            for AxisPattern in AxisPatterns:
                try:
                    Candidate = BuildBoundedInterClusterRoutingChannel(
                        Source,
                        TrackPitch=TrackPitch,
                            MaximumAffectedClusters=MaximumAffectedClusters,
                            MaximumBoundaryStrips=MaximumBoundaryStrips,
                            RoutingLayerCount=RoutingLayerCount,
                            RequiredComponentGateNames=(
                                RequiredComponentGateNameSet
                            ),
                            ForcedAffectedClusters=tuple(AffectedClusters),
                        ForcedRoot=RootCandidate,
                        ForcedAxisPattern=tuple(AxisPattern),
                        ChannelClearanceTracks=ChannelClearanceTracks,
                    )
                    if RemainingTopologyVariants == 0:
                        return Candidate
                    RemainingTopologyVariants -= 1
                except ValueError as Error:
                    RootFailures.append(
                        "root-"
                        f"{RankedRoots.index(RootCandidate)}"
                        f"-axes-{''.join(AxisPattern)}:{Error}"
                    )
        raise ValueError(
            "no legal bounded channel root: "
            + "; ".join(RootFailures)
            + (
                ""
                if not RootFailures else
                f"; requested topology variant {ChannelTopologyVariant} "
                "exceeds legal channel alternatives"
            )
        )
    if ForcedRoot not in AffectedSet:
        raise ValueError("forced channel root is outside the component")
    if (
        ForcedAxisPattern is None
        or len(ForcedAxisPattern) != len(TreeEdges)
        or any(Axis not in {"X", "Z"} for Axis in ForcedAxisPattern)
    ):
        raise ValueError("forced channel axis pattern is invalid")
    Root = ForcedRoot
    AxisByEdge = {
        tuple(sorted(Edge)): ForcedAxisPattern[Index]
        for Index, Edge in enumerate(TreeEdges)
    }
    ClusterTranslations: dict[int, tuple[int, int, int]] = {
        Root: (0, 0, 0)
    }
    StripSeeds: list[tuple[str, int, int, int]] = []
    Queue = deque([(Root, -1)])
    while Queue:
        Parent, GrandParent = Queue.popleft()
        for Child in sorted(Adjacency[Parent]):
            if Child == GrandParent:
                continue
            DeltaX = Centers[Child][0] - Centers[Parent][0]
            DeltaZ = Centers[Child][1] - Centers[Parent][1]
            Axis = AxisByEdge[tuple(sorted((Parent, Child)))]
            Direction = (
                1
                if (DeltaX if Axis == "X" else DeltaZ) >= 0
                else -1
            )
            ParentDelta = ClusterTranslations[Parent]
            ShiftDistance = TrackPitch * (1 + ChannelClearanceTracks)
            Shift = (
                Direction * ShiftDistance,
                0,
                0,
            ) if Axis == "X" else (
                0,
                0,
                Direction * ShiftDistance,
            )
            ClusterTranslations[Child] = tuple(
                ParentDelta[Index] + Shift[Index]
                for Index in range(3)
            )
            if any(
                abs(ClusterTranslations[Child][Index]) > ShiftDistance
                for Index in (0, 2)
            ):
                raise ValueError(
                    "inter-cluster channel exceeds per-axis growth bound"
                )
            # The lane belongs in the newly opened translated gap, not at
            # the child's source boundary.  Using the source coordinate
            # made a clearance transform move the cluster while leaving its
            # channel lane inside the old placed geometry.
            ParentBounds = Envelopes[Parent]
            ChildBounds = Envelopes[Child]
            if Axis == "X":
                GapMinimum, GapMaximum = (
                    (
                        ParentBounds[2] + ParentDelta[0] + 1,
                        ChildBounds[0] + ClusterTranslations[Child][0] - 1,
                    )
                    if Direction > 0 else (
                        ChildBounds[2] + ClusterTranslations[Child][0] + 1,
                        ParentBounds[0] + ParentDelta[0] - 1,
                    )
                )
            else:
                GapMinimum, GapMaximum = (
                    (
                        ParentBounds[3] + ParentDelta[2] + 1,
                        ChildBounds[1] + ClusterTranslations[Child][2] - 1,
                    )
                    if Direction > 0 else (
                        ChildBounds[3] + ClusterTranslations[Child][2] + 1,
                        ParentBounds[1] + ParentDelta[2] - 1,
                    )
                )
            if GapMinimum > GapMaximum:
                raise ValueError("translated channel has no physical gap")
            Coordinate = (GapMinimum + GapMaximum) // 2
            StripSeeds.append((Axis, Coordinate, Parent, Child))
            Queue.append((Child, Parent))

    ModuleGateByName = {
        Gate.Name: Gate for Gate in Source.Placed.Module.Gates
    }
    ClusterByGate = {
        Name: ClusterIndex
        for ClusterIndex, Names in enumerate(Source.Clusters)
        for Name in Names
    }

    def TranslatePosition(
        Position: tuple[int, int, int],
        Delta: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        return tuple(
            int(Position[Index]) + int(Delta[Index])
            for Index in range(3)
        )

    CandidateGates = []
    for Gate in Source.Placed.PlacedGates:
        Cluster = ClusterByGate.get(Gate.Name)
        Delta = ClusterTranslations.get(Cluster, (0, 0, 0))
        if Delta == (0, 0, 0) or Gate.Name not in ModuleGateByName:
            CandidateGates.append(Gate)
            continue
        CandidateGates.append(BuildPlacedGate(
            ModuleGateByName[Gate.Name],
            Gate.X + Delta[0],
            Gate.Y + Delta[1],
            Gate.Z + Delta[2],
            Gate.Rotation,
            Gate.MirrorX,
        ))
    CandidatePlacedForValidation = PlacedDesign(
        Module=Source.Placed.Module,
        PlacedGates=CandidateGates,
    )
    Occupied, _Electrical, _Solid = BuildPlacedCellGeometry(
        CandidatePlacedForValidation
    )
    ValidatePlacedCellElectricalIsolation(
        CandidatePlacedForValidation
    )

    def InterfaceStripExtent(
        Axis: str,
        FirstCluster: int,
        SecondCluster: int,
    ) -> tuple[int, int]:
        PerpendicularCoordinates = []
        Edge = frozenset((FirstCluster, SecondCluster))
        for Request in BoundaryRequests:
            if frozenset((
                Request.SourceCluster,
                Request.TargetCluster,
            )) != Edge:
                continue
            Terminals = (
                *((Request.SourceTerminal,)
                  if Request.SourceTerminal is not None else ()),
                *Request.TargetTerminals,
            )
            for Terminal in Terminals:
                Cluster = (
                    Request.SourceCluster
                    if Terminal == Request.SourceTerminal
                    else Request.TargetCluster
                )
                Delta = ClusterTranslations.get(
                    Cluster,
                    (0, 0, 0),
                )
                PerpendicularCoordinates.append(
                    Terminal[2] + Delta[2]
                    if Axis == "X"
                    else Terminal[0] + Delta[0]
                )
        if not PerpendicularCoordinates:
            PerpendicularCoordinates.extend((
                Centers[FirstCluster][1]
                if Axis == "X"
                else Centers[FirstCluster][0],
                Centers[SecondCluster][1]
                if Axis == "X"
                else Centers[SecondCluster][0],
            ))
        Margin = TrackPitch // 2
        return (
            min(PerpendicularCoordinates) - Margin,
            max(PerpendicularCoordinates) + Margin,
        )

    InsertedBoundaryStrips = tuple(
        (
            Axis,
            Coordinate,
            *InterfaceStripExtent(Axis, First, Second),
        )
        for Axis, Coordinate, First, Second in StripSeeds
    )
    LaneRecords = []
    for Axis, Coordinate, Minimum, Maximum in InsertedBoundaryStrips:
        Direction = "Z" if Axis == "X" else "X"
        for Layer in range(min(
            RoutingLayerCount,
            max(1, Source.LayerCount),
        )):
            RoutingY = DefaultRedstoneRoutingTechnology.RoutingY(
                0,
                Layer,
            )
            Cells = tuple(
                (
                    (Coordinate, RoutingY, Offset)
                    if Axis == "X"
                    else (Offset, RoutingY, Coordinate)
                )
                for Offset in range(Minimum, Maximum + 1)
            )
            if any(Cell in Occupied for Cell in Cells):
                raise ValueError(
                    "inter-cluster channel lane overlaps placed geometry"
                )
            IngressNodes = tuple(
                Cells[Index]
                for Index in range(0, len(Cells), TrackPitch)
            )
            PhysicalClaims = RoutingResourceClaims(
                WireCells=frozenset(Cells),
                SupportCells=frozenset(
                    (X, Y - 1, Z) for X, Y, Z in Cells
                ),
                RequiredAirCells=frozenset(),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions(set(Cells))
                ),
            )
            ClaimsFingerprint = sha256(repr((
                Layer,
                Direction,
                tuple(sorted(PhysicalClaims.ResourceIds, key=repr)),
            )).encode("utf-8")).hexdigest()[:16]
            LaneRecords.append(InterClusterChannelLane(
                Layer=Layer,
                Direction=Direction,
                Cells=Cells,
                IngressNodes=IngressNodes,
                PhysicalClaims=PhysicalClaims,
                ClaimsFingerprint=ClaimsFingerprint,
            ))

    CandidateAffectedSignals = {
        Request.Signal
        for Request in AllBoundaryRequests
        if (
            Request.SourceCluster in AffectedSet
            or Request.TargetCluster in AffectedSet
        )
    }
    if TopologyComponent is not None:
        ClosedSignals = frozenset((
            *TopologyComponent.InternalSignals,
            *TopologyComponent.BoundarySignals,
        ))
        CandidateAffectedSignals.intersection_update(ClosedSignals)
    AffectedSignals = tuple(sorted(CandidateAffectedSignals))
    MinimumX = min(
        (Cell[0] for Lane in LaneRecords for Cell in Lane.Cells),
        default=0,
    )
    MinimumZ = min(
        (Cell[2] for Lane in LaneRecords for Cell in Lane.Cells),
        default=0,
    )
    StructuralClusters = tuple(sorted(
        Source.PackedClusters[Cluster].StructuralSignature
        if Cluster < len(Source.PackedClusters)
        else str(len(Source.Clusters[Cluster]))
        for Cluster in AffectedClusters
    ))
    ChannelFingerprint = sha256(repr((
        StructuralClusters,
        ChannelClearanceTracks,
        tuple(
            (
                Axis,
                Coordinate - (MinimumX if Axis == "X" else MinimumZ),
                Minimum - (MinimumZ if Axis == "X" else MinimumX),
                Maximum - (MinimumZ if Axis == "X" else MinimumX),
            )
            for Axis, Coordinate, Minimum, Maximum
            in InsertedBoundaryStrips
        ),
        tuple(
            (
                Lane.Layer,
                Lane.Direction,
                tuple(
                    (
                        Cell[0] - MinimumX,
                        Cell[1],
                        Cell[2] - MinimumZ,
                    )
                    for Cell in Lane.Cells
                ),
            )
            for Lane in LaneRecords
        ),
    )).encode("utf-8")).hexdigest()[:16]
    Channel = InterClusterRoutingChannel(
        ChannelFingerprint=ChannelFingerprint,
        AffectedClusters=tuple(sorted(AffectedClusters)),
        AffectedSignals=AffectedSignals,
        InsertedBoundaryStrips=InsertedBoundaryStrips,
        ClusterTranslations=tuple(sorted(
            ClusterTranslations.items()
        )),
        Lanes=tuple(LaneRecords),
        TrackPitch=TrackPitch,
        ChannelClearanceTracks=ChannelClearanceTracks,
        MaximumAffectedClusters=MaximumAffectedClusters,
        MaximumBoundaryStrips=MaximumBoundaryStrips,
        ComponentId=ComponentId,
        InterfaceFingerprint=(
            TopologyComponent.StructuralFingerprint
            if TopologyComponent is not None
            else ""
        ),
        DeclaredFeedthroughSignals=(),
    )

    CandidateClaims = tuple(
        TranslateClusterLocalRouteClaim(
            Claim,
            ClusterTranslations.get(Claim.ClusterId, (0, 0, 0)),
        )
        for Claim in Source.Placed.LocalRouteClaims or ()
    )
    CandidateLeaseRequests = tuple(
        replace(
            Request,
            SourceTerminal=(
                TranslatePosition(
                    Request.SourceTerminal,
                    ClusterTranslations.get(
                        Request.SourceCluster,
                        (0, 0, 0),
                    ),
                )
                if Request.SourceTerminal is not None
                else None
            ),
            TargetTerminals=tuple(
                TranslatePosition(
                    Terminal,
                    ClusterTranslations.get(
                        Request.TargetCluster,
                        (0, 0, 0),
                    ),
                )
                for Terminal in Request.TargetTerminals
            ),
        )
        for Request in AllBoundaryRequests
    )
    Diagnostics = dict(Source.Placed.LocalRouteDiagnostics or {})
    DeferredDiagnostics = Diagnostics.get(
        "__DeferredLocalRouting__",
        {},
    )
    if isinstance(DeferredDiagnostics, dict):
        Diagnostics["__DeferredLocalRouting__"] = {
            **DeferredDiagnostics,
            "ScoringOnly": False,
            "Channelized": True,
        }
    Diagnostics["__InterClusterRoutingChannel__"] = (
        Channel.ToDictionary()
    )
    Diagnostics["__InterClusterRoutingChannelSelection__"] = {
        "RequiredComponentGateNames": sorted(
            RequiredComponentGateNameSet
        ),
    }
    CandidatePlaced = PlacedDesign(
        Module=Source.Placed.Module,
        PlacedGates=CandidateGates,
        RouteGuides=None,
        RouteLayers=None,
        FrozenNetWires=None,
        LocalNetBranches=None,
        LocalNetTargets=None,
        LocalRouteClaims=CandidateClaims,
        LocalRouteDiagnostics=Diagnostics,
        ClusterBoundaryLeaseRequests=CandidateLeaseRequests,
        CompleteClusterInterfaceAccess=True,
        InterClusterRoutingChannel=Channel,
        PackedClusters=Source.PackedClusters,
        ComponentGraph=LogicalComponentGraph,
    )
    return replace(
        Source,
        Placed=CandidatePlaced,
        PackedClusters=tuple(
            replace(
                Cluster,
                BoundaryTerminals=tuple(
                    TranslatePosition(
                        Terminal,
                        ClusterTranslations.get(
                            Cluster.ClusterId,
                            (0, 0, 0),
                        ),
                    )
                    for Terminal in Cluster.BoundaryTerminals
                ),
            )
            for Cluster in Source.PackedClusters
        ),
        ClusterBoundaryLeaseRequests=CandidateLeaseRequests,
        ClusterLocalRouteTemplates=tuple(
            replace(
                Template,
                Origin=TranslatePosition(
                    Template.Origin,
                    ClusterTranslations.get(
                        Template.ClusterId,
                        (0, 0, 0),
                    ),
                ),
            )
            for Template in Source.ClusterLocalRouteTemplates
        ),
        CompleteClusterInterfaceAccess=True,
        MandatoryAccessPreScreenProfile=None,
        InterClusterRoutingChannel=Channel,
        ComponentGraph=LogicalComponentGraph,
    )

def BuildBoundedInterClusterRoutingDeck(
    Source: PcbPlacement,
    *,
    TrackPitch: int | None = None,
    MaximumAffectedClusters: int = 3,
    MaximumDeckLanes: int = 2,
    InterfaceDeckLayer: int = 3,
    ComponentVariant: int = 0,
    PreferredSignals: Iterable[str] = (),
    RequiredComponentGateNames: Iterable[str] = (),
    ForcedAffectedClusters: tuple[int, ...] | None = None,
) -> PcbPlacement:
    """Add one component-owned routing deck above the compact three layers."""
    TrackPitch = (
        DefaultRedstoneRoutingTechnology.TrackPitch
        if TrackPitch is None
        else int(TrackPitch)
    )
    if TrackPitch < 1:
        raise ValueError("interface deck pitch must be positive")
    if MaximumAffectedClusters not in {2, 3}:
        raise ValueError("interface deck supports two or three clusters")
    if not 1 <= MaximumDeckLanes <= 12:
        raise ValueError("interface deck supports one through twelve lanes")
    if InterfaceDeckLayer != 3:
        raise ValueError(
            "the bounded interface deck must follow three compact layers"
        )
    if ComponentVariant < 0:
        raise ValueError("component variant cannot be negative")
    PreferredSignalSet = frozenset(
        str(Signal) for Signal in PreferredSignals
    )
    RequiredComponentGateNameSet = frozenset(
        str(Name) for Name in RequiredComponentGateNames
    )

    GateByName = {
        Gate.Name: Gate for Gate in Source.Placed.PlacedGates
    }
    ClusterGates = {
        ClusterIndex: tuple(
            GateByName[Name]
            for Name in Names
            if Name in GateByName
        )
        for ClusterIndex, Names in enumerate(Source.Clusters)
    }
    ClusterGates = {
        ClusterIndex: Gates
        for ClusterIndex, Gates in ClusterGates.items()
        if Gates
    }
    AllBoundaryRequests = (
        BuildPhysicalClusterBoundaryLeaseRequests(Source)
    )
    BoundaryRequests = tuple(
        Request
        for Request in AllBoundaryRequests
        if (
            Request.SourceCluster in ClusterGates
            and Request.TargetCluster in ClusterGates
            and Request.SourceCluster != Request.TargetCluster
        )
    )
    EdgeDemand: dict[tuple[int, int], int] = {}
    for Request in BoundaryRequests:
        Edge = tuple(sorted((
            int(Request.SourceCluster),
            int(Request.TargetCluster),
        )))
        EdgeDemand[Edge] = EdgeDemand.get(Edge, 0) + (
            1 + len(Request.TargetTerminals)
        )
    LogicalComponentGraph = (
        Source.ComponentGraph or Source.Placed.ComponentGraph
    )
    LogicalComponentByGate = (
        dict(LogicalComponentGraph.GateToComponent)
        if (
            LogicalComponentGraph is not None
            and LogicalComponentGraph.Hierarchical
        )
        else {}
    )
    LogicalClustersByComponent: dict[int, set[int]] = {}
    LogicalComponentByCluster: dict[int, int] = {}
    for ClusterIndex, Gates in ClusterGates.items():
        ComponentIds = {
            LogicalComponentByGate[Gate.Name]
            for Gate in Gates
            if Gate.Name in LogicalComponentByGate
        }
        if len(ComponentIds) == 1:
            ComponentId = next(iter(ComponentIds))
            LogicalComponentByCluster[ClusterIndex] = ComponentId
            LogicalClustersByComponent.setdefault(
                ComponentId,
                set(),
            ).add(ClusterIndex)
    AlignedLogicalComponents = {
        frozenset(ClusterIndexes): ComponentId
        for ComponentId, ClusterIndexes
        in LogicalClustersByComponent.items()
        if 2 <= len(ClusterIndexes) <= MaximumAffectedClusters
    }
    def LogicalComponentForClusterSet(
        ClusterSet: frozenset[int],
    ) -> int | None:
        ComponentIds = {
            LogicalComponentByCluster[Cluster]
            for Cluster in ClusterSet
            if Cluster in LogicalComponentByCluster
        }
        return (
            next(iter(ComponentIds))
            if (
                len(ComponentIds) == 1
                and ClusterSet
                == frozenset(LogicalClustersByComponent.get(
                    next(iter(ComponentIds)),
                    (),
                ))
                and frozenset(
                    Gate.Name
                    for Cluster in ClusterSet
                    for Gate in ClusterGates[Cluster]
                    if Gate.Name in LogicalComponentByGate
                )
                == LogicalGateNamesByComponent.get(
                    next(iter(ComponentIds)),
                    frozenset(),
                )
                and all(
                    Cluster in LogicalComponentByCluster
                    for Cluster in ClusterSet
                )
            )
            else None
        )
    LogicalComponentBenefits = {
        Value.ComponentId: (
            len(Value.BoundarySignals),
            -Value.QualifyingReconvergentCutCount,
            -len(Value.GateNames),
        )
        for Value in getattr(
            LogicalComponentGraph,
            "Components",
            (),
        )
    }
    LogicalGateNamesByComponent = {
        Value.ComponentId: frozenset(Value.GateNames)
        for Value in getattr(
            LogicalComponentGraph,
            "Components",
            (),
        )
    }
    CandidateComponents = []
    ClusterIndexes = tuple(sorted(ClusterGates))
    for ComponentSize in range(
        min(MaximumAffectedClusters, len(ClusterIndexes)),
        1,
        -1,
    ):
        for Component in combinations(ClusterIndexes, ComponentSize):
            ComponentSet = frozenset(Component)
            Edges = tuple(
                Edge for Edge in EdgeDemand
                if set(Edge) <= ComponentSet
            )
            if len(Edges) < ComponentSize - 1:
                continue
            Visited = {Component[0]}
            while True:
                Expanded = {
                    Value
                    for Edge in Edges
                    if set(Edge) & Visited
                    for Value in Edge
                }
                if Expanded <= Visited:
                    break
                Visited.update(Expanded)
            if Visited != set(Component):
                continue
            ComponentGateNames = frozenset(
                Gate.Name
                for Cluster in ComponentSet
                for Gate in ClusterGates[Cluster]
            )
            RequiredGateDomainCovered = bool(
                RequiredComponentGateNameSet
                and RequiredComponentGateNameSet.issubset(
                    ComponentGateNames
                )
            )
            if (
                LogicalComponentGraph is not None
                and LogicalComponentGraph.Hierarchical
                and LogicalComponentForClusterSet(ComponentSet) is None
                and not RequiredGateDomainCovered
            ):
                continue
            Signatures = tuple(sorted(
                Source.PackedClusters[Index].StructuralSignature
                if Index < len(Source.PackedClusters)
                else str(len(Source.Clusters[Index]))
                for Index in Component
            ))
            CrossingOwnedTerminalDemand = 0
            CrossingSignals = set()
            IncidentSignals = set()
            InternallyOwnedSignals = set()
            InternallyOwnedTerminalDemand = 0
            PreferredCoveredSignals = set()
            PreferredOwnedTerminalCoverage = 0
            PreferredFullyOwnedRequestCount = 0
            ComponentGates = tuple(
                Gate
                for Cluster in ComponentSet
                for Gate in ClusterGates[Cluster]
            )
            ComponentMinimumX = min(Gate.X for Gate in ComponentGates)
            ComponentMaximumX = max(Gate.X for Gate in ComponentGates)
            ComponentMinimumZ = min(Gate.Z for Gate in ComponentGates)
            ComponentMaximumZ = max(Gate.Z for Gate in ComponentGates)
            PerimeterDepths = []
            DirectedPerimeterPenalties = []

            def RecordPerimeterDepth(
                Terminal: tuple[int, int, int] | None,
                Side: str,
            ) -> None:
                if Terminal is None:
                    return
                X, _Y, Z = Terminal
                SideDepths = {
                    "west": abs(X - ComponentMinimumX),
                    "east": abs(ComponentMaximumX - X),
                    "north": abs(Z - ComponentMinimumZ),
                    "south": abs(ComponentMaximumZ - Z),
                }
                MinimumDepth = min(SideDepths.values())
                DirectedDepth = SideDepths.get(
                    str(Side).lower(),
                    MinimumDepth,
                )
                PerimeterDepths.append(MinimumDepth)
                DirectedPerimeterPenalties.append(
                    max(0, DirectedDepth - MinimumDepth)
                )

            for Request in AllBoundaryRequests:
                SourceSelected = (
                    int(Request.SourceCluster) in ComponentSet
                )
                TargetSelected = (
                    int(Request.TargetCluster) in ComponentSet
                )
                if not (SourceSelected or TargetSelected):
                    continue
                IncidentSignals.add(str(Request.Signal))
                if str(Request.Signal) in PreferredSignalSet:
                    PreferredCoveredSignals.add(
                        str(Request.Signal)
                    )
                    PreferredOwnedTerminalCoverage += (
                        int(
                            SourceSelected
                            and Request.SourceTerminal is not None
                        )
                        + (
                            len(Request.TargetTerminals)
                            if TargetSelected
                            else 0
                        )
                    )
                    PreferredFullyOwnedRequestCount += int(
                        SourceSelected and TargetSelected
                    )
                if SourceSelected and TargetSelected:
                    InternallyOwnedSignals.add(str(Request.Signal))
                    InternallyOwnedTerminalDemand += (
                        int(Request.SourceTerminal is not None)
                        + len(Request.TargetTerminals)
                    )
                if SourceSelected == TargetSelected:
                    continue
                CrossingSignals.add(str(Request.Signal))
                if SourceSelected:
                    RecordPerimeterDepth(
                        Request.SourceTerminal,
                        Request.SourceBoundarySide,
                    )
                else:
                    for Terminal in Request.TargetTerminals:
                        RecordPerimeterDepth(
                            Terminal,
                            Request.TargetBoundarySide,
                        )
                CrossingOwnedTerminalDemand += (
                    int(Request.SourceTerminal is not None)
                    if SourceSelected
                    else len(Request.TargetTerminals)
                )
            PeakInternalDemand = max(
                (EdgeDemand[Edge] for Edge in Edges),
                default=0,
            )
            TotalInternalDemand = sum(
                EdgeDemand[Edge] for Edge in Edges
            )
            PeakInternalSignalCount = max(
                (
                    len({
                        str(Request.Signal)
                        for Request in AllBoundaryRequests
                        if frozenset((
                            int(Request.SourceCluster),
                            int(Request.TargetCluster),
                        )) == frozenset(Edge)
                    })
                    for Edge in Edges
                ),
                default=0,
            )
            if RequiredComponentGateNameSet:
                # A complete capacity proof is rooted at one physical gate
                # access domain.  Once every reported signal touches the
                # component, keep that domain minimal; absorbing additional
                # producers creates a large rectangular access envelope that
                # can hide otherwise valid exterior guide targets.
                Score = (
                    -len(PreferredCoveredSignals),
                    ComponentSize,
                    len(CrossingSignals),
                    CrossingOwnedTerminalDemand,
                    max(DirectedPerimeterPenalties, default=0),
                    sum(DirectedPerimeterPenalties),
                    max(PerimeterDepths, default=0),
                    sum(PerimeterDepths),
                    -PreferredFullyOwnedRequestCount,
                    -PreferredOwnedTerminalCoverage,
                    PeakInternalDemand,
                    TotalInternalDemand,
                    len(IncidentSignals),
                    PeakInternalSignalCount,
                    -len(InternallyOwnedSignals),
                    -InternallyOwnedTerminalDemand,
                    Signatures,
                    Component,
                )
            elif PreferredSignalSet:
                # A learned global cut identifies work that must move inside
                # the routed component.  First maximize exact ownership of
                # that cut; only then prefer the least-demanding residual
                # interface.  Reversing these priorities can select a quiet
                # component that owns one cut terminal and exports the
                # original high-fanout net back to the global router.
                Score = (
                    -len(PreferredCoveredSignals),
                    -PreferredFullyOwnedRequestCount,
                    -PreferredOwnedTerminalCoverage,
                    0
                    if LogicalComponentForClusterSet(ComponentSet)
                    is not None
                    else 1,
                    len(CrossingSignals),
                    CrossingOwnedTerminalDemand,
                    max(DirectedPerimeterPenalties, default=0),
                    sum(DirectedPerimeterPenalties),
                    max(PerimeterDepths, default=0),
                    sum(PerimeterDepths),
                    LogicalComponentBenefits.get(
                        LogicalComponentForClusterSet(ComponentSet),
                        (1 << 30, 0, 0),
                    ),
                    PeakInternalDemand,
                    TotalInternalDemand,
                    len(IncidentSignals),
                    PeakInternalSignalCount,
                    -len(InternallyOwnedSignals),
                    -InternallyOwnedTerminalDemand,
                    -ComponentSize,
                    Signatures,
                    Component,
                )
            else:
                Score = (
                    0,
                    0,
                    0,
                    0
                    if LogicalComponentForClusterSet(ComponentSet)
                    is not None
                    else 1,
                    len(CrossingSignals),
                    CrossingOwnedTerminalDemand,
                    max(DirectedPerimeterPenalties, default=0),
                    sum(DirectedPerimeterPenalties),
                    max(PerimeterDepths, default=0),
                    sum(PerimeterDepths),
                    LogicalComponentBenefits.get(
                        LogicalComponentForClusterSet(ComponentSet),
                        (1 << 30, 0, 0),
                    ),
                    len(IncidentSignals),
                    PeakInternalSignalCount,
                    PeakInternalDemand,
                    TotalInternalDemand,
                    -len(InternallyOwnedSignals),
                    -InternallyOwnedTerminalDemand,
                    -ComponentSize,
                    Signatures,
                    Component,
                )
            CandidateComponents.append((
                Score,
                Component,
                (
                    len(CrossingSignals),
                    CrossingOwnedTerminalDemand,
                    max(DirectedPerimeterPenalties, default=0),
                    sum(DirectedPerimeterPenalties),
                    max(PerimeterDepths, default=0),
                    sum(PerimeterDepths),
                ),
            ))
    if not CandidateComponents:
        raise ValueError(
            "no connected two-or-three-cluster interface deck component"
        )
    RankedComponents = tuple(sorted(CandidateComponents))
    if RequiredComponentGateNameSet:
        RankedComponents = tuple(
            Value
            for Value in RankedComponents
            if RequiredComponentGateNameSet.issubset(frozenset(
                Gate.Name
                for Cluster in Value[1]
                for Gate in ClusterGates[Cluster]
            ))
        )
        if not RankedComponents:
            raise ValueError(
                "no legal connected component preserves the required gate domain"
            )
    if len(ClusterIndexes) <= MaximumAffectedClusters:
        WholePlacementComponents = tuple(
            Value
            for Value in RankedComponents
            if frozenset(Value[1]) == frozenset(ClusterIndexes)
        )
        if WholePlacementComponents:
            RankedComponents = WholePlacementComponents
    if ForcedAffectedClusters is None:
        (
            _Score,
            AffectedClusters,
            SelectedPerimeterAccessScore,
        ) = RankedComponents[ComponentVariant % len(RankedComponents)]
    else:
        ForcedClusterSet = frozenset(map(int, ForcedAffectedClusters))
        ForcedMatches = tuple(
            Value
            for Value in RankedComponents
            if frozenset(Value[1]) == ForcedClusterSet
        )
        if not ForcedMatches:
            raise ValueError(
                "forced deck component is not a legal connected component"
            )
        (
            _Score,
            AffectedClusters,
            SelectedPerimeterAccessScore,
        ) = ForcedMatches[0]
    AffectedSet = frozenset(AffectedClusters)
    ComponentId = LogicalComponentForClusterSet(AffectedSet)
    TopologyComponent = (
        next(
            (
                Value
                for Value in LogicalComponentGraph.Components
                if Value.ComponentId == ComponentId
            ),
            None,
        )
        if (
            LogicalComponentGraph is not None
            and ComponentId is not None
        )
        else None
    )

    def Center(Cluster: int) -> tuple[int, int]:
        Gates = ClusterGates[Cluster]
        return (
            sum(Gate.X for Gate in Gates) // len(Gates),
            sum(Gate.Z for Gate in Gates) // len(Gates),
        )

    Centers = {
        Cluster: Center(Cluster)
        for Cluster in AffectedClusters
    }
    RankedEdges = sorted(
        (
            Edge for Edge in EdgeDemand
            if set(Edge) <= AffectedSet
        ),
        key=lambda Edge: (
            -EdgeDemand[Edge],
            abs(Centers[Edge[0]][0] - Centers[Edge[1]][0])
            + abs(Centers[Edge[0]][1] - Centers[Edge[1]][1]),
            Edge,
        ),
    )
    Parents = {Cluster: Cluster for Cluster in AffectedClusters}

    def Find(Value: int) -> int:
        while Parents[Value] != Value:
            Parents[Value] = Parents[Parents[Value]]
            Value = Parents[Value]
        return Value

    TreeEdges = []
    for First, Second in RankedEdges:
        FirstRoot, SecondRoot = Find(First), Find(Second)
        if FirstRoot == SecondRoot:
            continue
        Parents[max(FirstRoot, SecondRoot)] = min(
            FirstRoot,
            SecondRoot,
        )
        TreeEdges.append((First, Second))
        if len(TreeEdges) == len(AffectedClusters) - 1:
            break
    if len(TreeEdges) != len(AffectedClusters) - 1:
        raise ValueError("interface deck spanning tree is incomplete")
    TreeEdges = TreeEdges[:MaximumDeckLanes]

    PlacementBaseY = min(
        Gate.Y for Gate in Source.Placed.PlacedGates
    )
    RoutingY = DefaultRedstoneRoutingTechnology.RoutingY(
        PlacementBaseY,
        InterfaceDeckLayer,
    )
    Occupied, _Electrical, _Solid = BuildPlacedCellGeometry(Source.Placed)

    def BuildTreeLane(
        DeltaX: int,
        DeltaZ: int,
        XFirstByEdge: tuple[bool, ...],
    ) -> tuple[tuple[tuple[int, int, int], ...], ...] | None:
        def InclusiveRange(Start: int, End: int) -> range:
            return range(
                Start,
                End + (1 if End >= Start else -1),
                1 if End >= Start else -1,
            )

        EdgePaths = []
        for EdgeIndex, (First, Second) in enumerate(TreeEdges):
            FirstX, FirstZ = Centers[First]
            SecondX, SecondZ = Centers[Second]
            StartX, StartZ = FirstX + DeltaX, FirstZ + DeltaZ
            EndX, EndZ = SecondX + DeltaX, SecondZ + DeltaZ
            if XFirstByEdge[EdgeIndex]:
                Cells = tuple((
                    *((X, RoutingY, StartZ)
                      for X in InclusiveRange(StartX, EndX)),
                    *((EndX, RoutingY, Z)
                      for Z in tuple(InclusiveRange(StartZ, EndZ))[1:]),
                ))
            else:
                Cells = tuple((
                    *((StartX, RoutingY, Z)
                      for Z in InclusiveRange(StartZ, EndZ)),
                    *((X, RoutingY, EndZ)
                      for X in tuple(InclusiveRange(StartX, EndX))[1:]),
                ))
            if not Cells or any(
                Position in Occupied
                for X, Y, Z in Cells
                for Position in (
                    (X, Y, Z),
                    (X, Y - 1, Z),
                )
            ):
                return None
            EdgePaths.append(Cells)
        return tuple(EdgePaths)

    def EdgePathsFormTreeFabric(
        EdgePaths: tuple[tuple[tuple[int, int, int], ...], ...],
    ) -> bool:
        Nodes = frozenset(
            Cell for Path in EdgePaths for Cell in Path
        )
        Edges = frozenset(
            tuple(sorted((First, Second)))
            for Path in EdgePaths
            for First, Second in zip(Path, Path[1:])
        )
        if not Nodes or len(Edges) != len(Nodes) - 1:
            return False
        Adjacency = {Node: set() for Node in Nodes}
        for First, Second in Edges:
            Adjacency[First].add(Second)
            Adjacency[Second].add(First)
        Visited = {min(Nodes)}
        Pending = list(Visited)
        while Pending:
            Current = Pending.pop()
            for Neighbor in Adjacency[Current]:
                if Neighbor not in Visited:
                    Visited.add(Neighbor)
                    Pending.append(Neighbor)
        return len(Visited) == len(Nodes)

    LaneCandidates = []
    LaneOffsets = (
        (0, 0),
        *(
            Value
            for Distance in range(1, 7)
            for Value in (
                (-Distance * TrackPitch, 0),
                (Distance * TrackPitch, 0),
                (0, -Distance * TrackPitch),
                (0, Distance * TrackPitch),
            )
        ),
    )
    for DeltaX, DeltaZ in LaneOffsets:
        for XFirstByEdge in product(
            (True, False),
            repeat=len(TreeEdges),
        ):
            EdgePaths = BuildTreeLane(
                DeltaX,
                DeltaZ,
                XFirstByEdge,
            )
            if (
                EdgePaths is None
                or not EdgePathsFormTreeFabric(EdgePaths)
            ):
                continue
            Cells = frozenset(
                Position
                for Path in EdgePaths
                for Position in Path
            )
            LaneCandidates.append((
                (
                    abs(DeltaX) + abs(DeltaZ),
                    DeltaX,
                    DeltaZ,
                    tuple(not Value for Value in XFirstByEdge),
                ),
                EdgePaths,
                Cells,
            ))
    def LanesAreSeparated(
        First: tuple[object, ...],
        Second: tuple[object, ...],
    ) -> bool:
        return not any(
            abs(FirstCell[0] - SecondCell[0])
            + abs(FirstCell[2] - SecondCell[2])
            < TrackPitch
            for FirstCell in First[2]
            for SecondCell in Second[2]
        )

    SelectedTreeLaneValues = []
    for Candidate in sorted(
        LaneCandidates,
        key=lambda Value: Value[0],
    ):
        if all(
            LanesAreSeparated(Candidate, Existing)
            for Existing in SelectedTreeLaneValues
        ):
            SelectedTreeLaneValues.append(Candidate)
            if len(SelectedTreeLaneValues) >= MaximumDeckLanes:
                break
    SelectedTreeLanes = tuple(SelectedTreeLaneValues)
    if not SelectedTreeLanes:
        raise ValueError(
            "no electrically separated component-tree deck lane"
        )

    LaneRecords = []
    for ParallelLaneIndex, LaneCandidate in enumerate(
        SelectedTreeLanes[:MaximumDeckLanes]
    ):
        _LaneScore, EdgePaths, _LaneCells = LaneCandidate
        for EdgeIndex, Cells in enumerate(EdgePaths):
            First, Second = TreeEdges[EdgeIndex]
            IngressNodes = tuple(dict.fromkeys((
                Cells[0],
                Cells[-1],
                *Cells[::TrackPitch],
            )))
            PhysicalClaims = RoutingResourceClaims(
                WireCells=frozenset(Cells),
                SupportCells=frozenset(
                    (X, Y - 1, Z) for X, Y, Z in Cells
                ),
                RequiredAirCells=frozenset(),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions(set(Cells))
                ),
            )
            ClaimsFingerprint = sha256(repr((
                InterfaceDeckLayer,
                tuple(sorted(
                    PhysicalClaims.ResourceIds,
                    key=repr,
                )),
            )).encode("utf-8")).hexdigest()[:16]
            LaneRecords.append(InterClusterChannelLane(
                Layer=InterfaceDeckLayer,
                Direction=f"XZ-Lane{ParallelLaneIndex}",
                Cells=Cells,
                IngressNodes=IngressNodes,
                PhysicalClaims=PhysicalClaims,
                ClaimsFingerprint=ClaimsFingerprint,
            ))

    MinimumX = min(Cell[0] for Lane in LaneRecords for Cell in Lane.Cells)
    MinimumZ = min(Cell[2] for Lane in LaneRecords for Cell in Lane.Cells)
    StructuralClusters = tuple(sorted(
        Source.PackedClusters[Cluster].StructuralSignature
        if Cluster < len(Source.PackedClusters)
        else str(len(Source.Clusters[Cluster]))
        for Cluster in AffectedClusters
    ))
    ChannelFingerprint = sha256(repr((
        "parallel-tree-cluster-interface-deck-v1",
        StructuralClusters,
        InterfaceDeckLayer,
        tuple(
            (
                Lane.Layer,
                tuple(
                    (
                        Cell[0] - MinimumX,
                        Cell[1] - PlacementBaseY,
                        Cell[2] - MinimumZ,
                    )
                    for Cell in Lane.Cells
                ),
            )
            for Lane in LaneRecords
        ),
    )).encode("utf-8")).hexdigest()[:16]
    CandidateAffectedSignals = {
        Request.Signal
        for Request in AllBoundaryRequests
        if (
            (
                Request.SourceCluster in AffectedSet
                and Request.TargetCluster in AffectedSet
            )
            or (
                str(Request.Signal) in PreferredSignalSet
                and (
                    Request.SourceCluster in AffectedSet
                    or Request.TargetCluster in AffectedSet
                )
            )
        )
    }
    if TopologyComponent is not None:
        CandidateAffectedSignals.intersection_update(frozenset((
            *TopologyComponent.InternalSignals,
            *TopologyComponent.BoundarySignals,
        )))
    AffectedSignals = tuple(sorted(CandidateAffectedSignals))
    Deck = InterClusterRoutingChannel(
        ChannelFingerprint=ChannelFingerprint,
        AffectedClusters=tuple(sorted(AffectedClusters)),
        AffectedSignals=AffectedSignals,
        InsertedBoundaryStrips=(),
        ClusterTranslations=tuple(
            (Cluster, (0, 0, 0))
            for Cluster in sorted(AffectedClusters)
        ),
        Lanes=tuple(LaneRecords),
        PhysicalModel="parallel-tree-cluster-interface-deck-v1",
        InterfaceDeckLayer=InterfaceDeckLayer,
        TrackPitch=TrackPitch,
        MaximumAffectedClusters=MaximumAffectedClusters,
        MaximumBoundaryStrips=0,
        ComponentId=ComponentId,
        InterfaceFingerprint=(
            TopologyComponent.StructuralFingerprint
            if TopologyComponent is not None
            else ""
        ),
        DeclaredFeedthroughSignals=(),
    )
    Diagnostics = dict(Source.Placed.LocalRouteDiagnostics or {})
    DeferredDiagnostics = Diagnostics.get(
        "__DeferredLocalRouting__",
        {},
    )
    if isinstance(DeferredDiagnostics, dict):
        Diagnostics["__DeferredLocalRouting__"] = {
            **DeferredDiagnostics,
            "ScoringOnly": False,
            "InterfaceDeck": True,
        }
    Diagnostics["__InterClusterRoutingChannel__"] = Deck.ToDictionary()
    Diagnostics["__InterClusterRoutingDeckSelection__"] = {
        "PreferredSignals": sorted(PreferredSignalSet),
        "RequiredComponentGateNames": sorted(RequiredComponentGateNameSet),
        "ProofRootedMinimalComponent": bool(
            RequiredComponentGateNameSet
        ),
        "SelectedComponentSize": len(AffectedClusters),
        "SelectedPreferredSignalCount": -_Score[0],
        "SelectedPeakInternalEdgeDemand": max(
            (
                EdgeDemand[Edge]
                for Edge in EdgeDemand
                if set(Edge) <= AffectedSet
            ),
            default=0,
        ),
        "SelectedTotalInternalEdgeDemand": sum(
            EdgeDemand[Edge]
            for Edge in EdgeDemand
            if set(Edge) <= AffectedSet
        ),
        "SelectedPreferredFullyOwnedRequestCount": (
            -_Score[1] if PreferredSignalSet else 0
        ),
        "SelectedPreferredOwnedTerminalCoverage": (
            -_Score[2] if PreferredSignalSet else 0
        ),
        "SelectedClusterCount": len(AffectedClusters),
        "SelectedPerimeterAccessScore": list(
            SelectedPerimeterAccessScore[2:]
        ),
        "SelectedBoundaryInterfaceSignalCount": (
            SelectedPerimeterAccessScore[0]
        ),
        "SelectedBoundaryInterfaceTerminalDemand": (
            SelectedPerimeterAccessScore[1]
        ),
        "ComponentVariant": ComponentVariant,
    }
    CandidatePlaced = replace(
        Source.Placed,
        RouteGuides=None,
        RouteLayers=None,
        FrozenNetWires=None,
        LocalNetBranches=None,
        LocalNetTargets=None,
        LocalRouteDiagnostics=Diagnostics,
        ClusterBoundaryLeaseRequests=AllBoundaryRequests,
        CompleteClusterInterfaceAccess=True,
        InterClusterRoutingChannel=Deck,
        PackedClusters=Source.PackedClusters,
    )
    return replace(
        Source,
        Placed=CandidatePlaced,
        ClusterBoundaryLeaseRequests=AllBoundaryRequests,
        CompleteClusterInterfaceAccess=True,
        MandatoryAccessPreScreenProfile=None,
        InterClusterRoutingChannel=Deck,
    )

"""Physical component guides and fixed seam preparation."""

from __future__ import annotations

from ..Components.Validation import BuildPhysicalPortApertureContractFingerprint

from ..Components.Validation import BuildPhysicalPortLocalContractFingerprint

from ..Components.Validation import BuildPhysicalPortSeamContractFingerprint

from ..Contracts.Component import ComponentCutAccessFeasibilityCertificate

from ..Contracts.Component import ComponentFeedthroughContract

from ..Contracts.Component import PhysicalComponentBoundaryPortReservation

from ..Contracts.Component import PhysicalComponentChannelReservation

from ..Contracts.Component import PhysicalComponentFeedthroughEndpointCandidate

from ..Contracts.Component import PhysicalComponentPortReservation

from ..Contracts.Component import PhysicalExteriorApertureFabric

from ..Contracts.Component import PreparedPhysicalComponentFeedthroughEndpointDomain

from ..Contracts.Core import Position2

from ..Contracts.Core import Position3

from ..Contracts.PhysicalInterface import PhysicalPortApertureOptionFactor

from ..Contracts.PhysicalInterface import PhysicalPortExteriorFixedClaimCertificate

from ..Contracts.PhysicalInterface import PhysicalPortLaneFactor

from ..Contracts.PhysicalInterface import PhysicalPortLocalAccessFactor

from ..Contracts.PhysicalInterface import PhysicalPortLocalApertureSupport

from ..Contracts.PhysicalInterface import PreparedPhysicalSignalLocalFactorDomain

from ..Failures import RoutingFailure

from ..Failures import RoutingFailureReason

from ..Failures import RoutingStageError

from ..Interfaces.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint

from ..Interfaces.PhysicalClaims import PortalTupleConflictsWithFrozenComponentClaims

from ..Reliability import BuildStableFingerprint

from ..ResourceGraph import FindClaimConflicts

from ..ResourceGraph import FindSelfClaimConflicts

from ..ResourceGraph import LocalRouteClaim

from ..ResourceGraph import RoutingResourceClaims

from ..ResourceGraph import RoutingResourceId

from ..ResourceGraph import RoutingResourceKind

from collections import Counter

from collections import defaultdict

from collections import deque

from dataclasses import replace

from heapq import heappop

from heapq import heappush

from itertools import product

from math import prod

from typing import Any

from typing import Callable

from typing import Iterable

from typing import Mapping

def ShouldBuildCapacityAwareGlobalGuidePlan(
    *,
    Enabled: bool,
    PrepareComponentRoutingProblemOnly: bool,
    RequireCompleteClusterInterfaceDomain: bool,
    HasInterClusterRoutingChannel: bool,
) -> bool:
    """Keep whole-design guide planning outside exact component preparation."""
    return bool(
        Enabled
        and not (
            PrepareComponentRoutingProblemOnly
            and RequireCompleteClusterInterfaceDomain
            and HasInterClusterRoutingChannel
        )
    )

def CanReuseFrozenPhysicalPortGuidePlan(
    ProfileSignals: Iterable[str],
    PhysicalPortSignals: Iterable[str],
    FrozenPlan: Any,
) -> bool:
    """Return whether immutable port corridors cover the routed cut."""
    Signals = frozenset(map(str, ProfileSignals))
    PortSignals = frozenset(map(str, PhysicalPortSignals))
    Guides = dict(getattr(FrozenPlan, "Guides", {}) or {})
    Layers = dict(getattr(FrozenPlan, "Layers", {}) or {})
    return bool(
        Signals
        and Signals <= PortSignals
        and all(
            Signal in Guides
            and bool(Guides[Signal])
            and Signal in Layers
            for Signal in Signals
        )
    )

def _ClaimsFromResourceIds(
    ResourceIds: Iterable[RoutingResourceId],
) -> RoutingResourceClaims:
    """Convert a guide's capacity ownership into physical claim sets."""
    Values = tuple(ResourceIds)
    return RoutingResourceClaims(
        WireCells=frozenset(
            Value.Position
            for Value in Values
            if Value.Kind == RoutingResourceKind.Wire
        ),
        SupportCells=frozenset(
            Value.Position
            for Value in Values
            if Value.Kind == RoutingResourceKind.Support
        ),
        RequiredAirCells=frozenset(
            Value.Position
            for Value in Values
            if Value.Kind == RoutingResourceKind.Air
        ),
        ElectricalCells=frozenset(
            Value.Position
            for Value in Values
            if Value.Kind == RoutingResourceKind.Electrical
        ),
    )

def FindSignalClaimConflicts(
    ClaimsBySignal: dict[str, RoutingResourceClaims],
    Signal: str,
) -> dict[RoutingResourceId, tuple[str, ...]]:
    """Return only conflicts in which the selected signal participates."""
    return {
        Resource: Signals
        for Resource, Signals in FindClaimConflicts(
            ClaimsBySignal
        ).items()
        if Signal in Signals
    }

def ExpandPhysicalComponentGuideChannels(
    CoarsePlan: Any,
    LayerCount: int,
) -> Any:
    """Resolve remaining coarse overflow by explicit layer channel expansion."""
    if CoarsePlan is None or LayerCount < 1:
        return CoarsePlan
    Guides = dict(CoarsePlan.Guides)
    OriginalLayers = dict(CoarsePlan.Layers)
    InvalidLayerSignals = frozenset(
        Signal
        for Signal in Guides
        if not 0 <= int(OriginalLayers.get(Signal, 0)) < LayerCount
    )
    if (
        not getattr(CoarsePlan, "Overflow", {})
        and not InvalidLayerSignals
    ):
        return CoarsePlan
    Layers = {
        Signal: min(
            LayerCount - 1,
            max(0, int(OriginalLayers.get(Signal, 0))),
        )
        for Signal in Guides
    }
    Usage = Counter()
    for Signal, Guide in Guides.items():
        Layer = int(Layers.get(Signal, 0))
        Usage.update((Layer, X, Z) for X, Z in Guide)

    def OverflowFor(
        Signal: str,
        Layer: int,
    ) -> tuple[int, int]:
        Conflicts = tuple(
            max(0, Usage[(Layer, X, Z)] + 1 - 1)
            for X, Z in Guides[Signal]
        )
        return (
            sum(Value > 0 for Value in Conflicts),
            sum(Conflicts),
        )

    for Signal in sorted(
        Guides,
        key=lambda Value: (
            -sum(
                Usage[(int(Layers.get(Value, 0)), X, Z)] > 1
                for X, Z in Guides[Value]
            ),
            -len(Guides[Value]),
            Value,
        ),
    ):
        CurrentLayer = int(Layers.get(Signal, 0))
        if not any(
            Usage[(CurrentLayer, X, Z)] > 1
            for X, Z in Guides[Signal]
        ):
            continue
        for X, Z in Guides[Signal]:
            Usage[(CurrentLayer, X, Z)] -= 1
        SelectedLayer = min(
            range(LayerCount),
            key=lambda Value: (
                OverflowFor(Signal, Value),
                Value != CurrentLayer,
                Value,
            ),
        )
        Layers[Signal] = SelectedLayer
        Usage.update(
            (SelectedLayer, X, Z) for X, Z in Guides[Signal]
        )
    Overflow = {
        Position: Count - 1
        for Position, Count in Usage.items()
        if Count > 1
    }
    return replace(
        CoarsePlan,
        Layers=Layers,
        Usage=dict(Usage),
        Overflow=Overflow,
    )

def RecomputeCoarseGuideCapacity(
    CoarsePlan: Any,
) -> Any:
    """Recompute capacity after immutable component guides are overlaid."""
    if CoarsePlan is None:
        return None
    Usage = Counter(
        (int(CoarsePlan.Layers.get(Signal, 0)), X, Z)
        for Signal, Guide in CoarsePlan.Guides.items()
        for X, Z in Guide
    )
    Overflow = {
        Position: Count - 1
        for Position, Count in Usage.items()
        if Count > 1
    }
    return replace(
        CoarsePlan,
        Usage=dict(Usage),
        Overflow=Overflow,
    )

def BuildComponentKeepoutGuideCellsByLayer(
    KeepoutClaims: RoutingResourceClaims,
    ResourceGraph: Any,
    *,
    MinimumPlacementY: int,
    LayerCount: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[int, frozenset[tuple[int, int]]]:
    """Project exact component conflicts onto each physical routing deck."""
    ClaimPositions = frozenset((
        *KeepoutClaims.WireCells,
        *KeepoutClaims.SupportCells,
        *KeepoutClaims.RequiredAirCells,
        *KeepoutClaims.ElectricalCells,
    ))
    if not ClaimPositions:
        return {
            Layer: frozenset()
            for Layer in range(max(1, int(LayerCount)))
        }
    MinimumX = min(Position[0] for Position in ClaimPositions) - 1
    MaximumX = max(Position[0] for Position in ClaimPositions) + 1
    MinimumZ = min(Position[2] for Position in ClaimPositions) - 1
    MaximumZ = max(Position[2] for Position in ClaimPositions) + 1
    Result: dict[int, frozenset[tuple[int, int]]] = {}
    CheckedCellCount = 0
    for Layer in range(max(1, int(LayerCount))):
        RoutingY = ResourceGraph.Technology.RoutingY(
            MinimumPlacementY,
            Layer,
        )
        BlockedCells = set()
        for X in range(MinimumX, MaximumX + 1):
            for Z in range(MinimumZ, MaximumZ + 1):
                CheckedCellCount += 1
                if WorkCheck is not None and CheckedCellCount % 256 == 0:
                    WorkCheck({
                        "Stage": "physical-component-layer-keepout",
                        "Layer": Layer,
                        "CheckedCellCount": CheckedCellCount,
                        "BlockedCellCount": len(BlockedCells),
                    })
                GuideClaims = ResourceGraph.BuildRouteClaims((
                    (X, RoutingY, Z),
                ))
                if FindClaimConflicts({
                    "Component": KeepoutClaims,
                    "GlobalGuide": GuideClaims,
                }):
                    BlockedCells.add((X, Z))
        Result[Layer] = frozenset(BlockedCells)
    return Result

def PreparePhysicalComponentFeedthroughEndpointDomain(
    Signal: str,
    Layer: int,
    *,
    FabricNodes: frozenset[tuple[int, int, int]],
    FabricEdges: frozenset[
        tuple[tuple[int, int, int], tuple[int, int, int]]
    ],
    FabricIngressNodes: frozenset[tuple[int, int, int]],
    FabricFingerprint: str,
    ResourceGraph: Any,
    MinimumPlacementY: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PreparedPhysicalComponentFeedthroughEndpointDomain:
    """Enumerate the complete port-independent interior passage domain.

    A boundary assignment may block a candidate later, but it may not cause
    the component fabric to be searched again.  On the component fabric used
    by this pipeline every connected ingress pair has one deterministic path;
    retaining every such path makes an empty post-port domain authoritative.
    """
    RoutingY = ResourceGraph.Technology.RoutingY(
        MinimumPlacementY,
        int(Layer),
    )
    LayerNodes = frozenset(
        Node for Node in FabricNodes if int(Node[1]) == int(RoutingY)
    )
    CandidateNodes = frozenset(
        Node for Node in FabricIngressNodes if Node in LayerNodes
    )
    LayerAdjacency: dict[
        tuple[int, int, int], set[tuple[int, int, int]]
    ] = defaultdict(set)
    for First, Second in FabricEdges:
        if First not in LayerNodes or Second not in LayerNodes:
            continue
        if ResourceGraph.BuildPrimitive(First, Second) is None:
            continue
        LayerAdjacency[First].add(Second)
        LayerAdjacency[Second].add(First)
    # One retained path per ingress pair is complete only on a forest.  The
    # production component fabric is a parallel tree; explicitly reject an
    # unexpected cyclic backend instead of mistaking one BFS witness for the
    # complete claim-distinct path domain.
    RemainingLayerNodes = set(LayerNodes)
    LayerFabricIsForest = True
    while RemainingLayerNodes:
        Start = min(RemainingLayerNodes)
        Pending = [Start]
        Reached = {Start}
        RemainingLayerNodes.remove(Start)
        DegreeSum = 0
        while Pending:
            Current = Pending.pop()
            Neighbors = LayerAdjacency.get(Current, set())
            DegreeSum += len(Neighbors)
            for Neighbor in Neighbors:
                if Neighbor in Reached:
                    continue
                Reached.add(Neighbor)
                RemainingLayerNodes.discard(Neighbor)
                Pending.append(Neighbor)
        if DegreeSum // 2 != max(0, len(Reached) - 1):
            LayerFabricIsForest = False
            break
    CandidatesByFingerprint = {}
    SearchCount = 0
    for Entry in sorted(CandidateNodes):
        Pending = deque((Entry,))
        Previous: dict[
            tuple[int, int, int], tuple[int, int, int] | None
        ] = {Entry: None}
        while Pending:
            Current = Pending.popleft()
            SearchCount += 1
            if WorkCheck is not None and SearchCount % 1024 == 0:
                WorkCheck({
                    "Stage": "physical-component-feedthrough-domain",
                    "Signal": Signal,
                    "VisitedNodeCount": SearchCount,
                })
            for Neighbor in sorted(LayerAdjacency.get(Current, ())):
                if Neighbor in Previous:
                    continue
                Previous[Neighbor] = Current
                Pending.append(Neighbor)
        for Exit in sorted(CandidateNodes):
            if Exit <= Entry or Exit not in Previous:
                continue
            Path = [Exit]
            while Previous[Path[-1]] is not None:
                Parent = Previous[Path[-1]]
                assert Parent is not None
                Path.append(Parent)
            Path.reverse()
            Forward = tuple(Path)
            Reverse = tuple(reversed(Path))
            ReservedPathNodes = min(Forward, Reverse)
            # This is the complete candidate domain for one fixed placement,
            # so translated lanes remain distinct physical choices.  Relative
            # topology is a template-cache identity, not a license to merge
            # absolute endpoint reservations before port claims are applied.
            CandidateFingerprint = BuildStableFingerprint((
                "physical-component-feedthrough-endpoint-candidate-v2",
                int(Layer),
                ReservedPathNodes,
            ))
            CandidatesByFingerprint.setdefault(
                CandidateFingerprint,
                PhysicalComponentFeedthroughEndpointCandidate(
                    CandidateFingerprint=CandidateFingerprint,
                    Layer=int(Layer),
                    Entry=ReservedPathNodes[0],
                    Exit=ReservedPathNodes[-1],
                    ReservedPathNodes=ReservedPathNodes,
                    Claims=ResourceGraph.BuildRouteClaims(
                        ReservedPathNodes
                    ),
                ),
            )
    Candidates = tuple(
        CandidatesByFingerprint[Fingerprint]
        for Fingerprint in sorted(CandidatesByFingerprint)
    )
    ResourceGraphFingerprint = BuildStableFingerprint((
        getattr(ResourceGraph, "GraphVersion", ""),
        len(getattr(ResourceGraph, "Nodes", ())),
        len(getattr(ResourceGraph, "Edges", ())),
    ))
    DomainFingerprint = BuildStableFingerprint((
        "physical-component-feedthrough-endpoint-domain-v1",
        int(Layer),
        str(FabricFingerprint),
        ResourceGraphFingerprint,
        tuple(Value.CandidateFingerprint for Value in Candidates),
    ))
    return PreparedPhysicalComponentFeedthroughEndpointDomain(
        DomainFingerprint=DomainFingerprint,
        Signal=str(Signal),
        Layer=int(Layer),
        FabricFingerprint=str(FabricFingerprint),
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        Candidates=Candidates,
        Complete=LayerFabricIsForest,
    )

def BuildExplicitPhysicalComponentFeedthrough(
    Signal: str,
    Layer: int,
    Guide: frozenset[tuple[int, int]],
    *,
    ComponentKeepoutGuideCells: frozenset[tuple[int, int]],
    ReservedPortAccessGuideCells: frozenset[tuple[int, int]],
    FabricNodes: frozenset[tuple[int, int, int]],
    FabricEdges: frozenset[
        tuple[tuple[int, int, int], tuple[int, int, int]]
    ],
    FabricIngressNodes: frozenset[tuple[int, int, int]],
    ResourceGraph: Any,
    MinimumPlacementY: int,
    PreparedEndpointDomain: (
        PreparedPhysicalComponentFeedthroughEndpointDomain | None
    ) = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[ComponentFeedthroughContract, frozenset[tuple[int, int]]]:
    """Reserve one exact capacity-one lane through a closed component.

    Ordinary global nets are first required to route around the component.
    This constructor is used only after that complete exterior search proves
    the guide's required exterior pieces cannot be joined.  It chooses two
    physical fabric boundary nodes on the signal's authoritative layer,
    freezes the shortest deterministic fabric path between them, and joins
    only those declared passage cells back to the exterior guide.  No foreign
    component domain is inferred from incidental overlap.
    """
    OutsideGuide = frozenset(
        Value
        for Value in Guide
        if (
            Value not in ComponentKeepoutGuideCells
            and Value not in ReservedPortAccessGuideCells
        )
    )

    def Components(
        Nodes: frozenset[tuple[int, int]],
    ) -> tuple[frozenset[tuple[int, int]], ...]:
        Remaining = set(Nodes)
        Result = []
        while Remaining:
            Start = min(Remaining)
            Pending = deque((Start,))
            Reached = {Start}
            Remaining.remove(Start)
            while Pending:
                X, Z = Pending.popleft()
                for Neighbor in (
                    (X - 1, Z),
                    (X + 1, Z),
                    (X, Z - 1),
                    (X, Z + 1),
                ):
                    if Neighbor not in Remaining:
                        continue
                    Remaining.remove(Neighbor)
                    Reached.add(Neighbor)
                    Pending.append(Neighbor)
            Result.append(frozenset(Reached))
        return tuple(sorted(Result, key=lambda Value: tuple(sorted(Value))))

    ExteriorComponents = Components(OutsideGuide)
    if len(ExteriorComponents) < 2:
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
            ),
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=(Signal,),
            Detail=(
                "an explicit capacity-one component feedthrough requires "
                "at least two exterior guide components"
            ),
            Diagnostics={
                "Signal": Signal,
                "ExteriorGuideComponentCount": len(ExteriorComponents),
                "DeclaredFeedthroughCapacity": 1,
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    RoutingY = ResourceGraph.Technology.RoutingY(
        MinimumPlacementY,
        int(Layer),
    )
    LayerNodes = frozenset(
        Node for Node in FabricNodes if int(Node[1]) == int(RoutingY)
    )
    EndpointDomain = PreparedEndpointDomain
    if EndpointDomain is None:
        EndpointDomain = PreparePhysicalComponentFeedthroughEndpointDomain(
            Signal,
            Layer,
            FabricNodes=FabricNodes,
            FabricEdges=FabricEdges,
            FabricIngressNodes=FabricIngressNodes,
            FabricFingerprint=BuildStableFingerprint((
                tuple(sorted(FabricNodes)),
                tuple(sorted(FabricEdges)),
            )),
            ResourceGraph=ResourceGraph,
            MinimumPlacementY=MinimumPlacementY,
            WorkCheck=WorkCheck,
        )
    CurrentResourceGraphFingerprint = BuildStableFingerprint((
        getattr(ResourceGraph, "GraphVersion", ""),
        len(getattr(ResourceGraph, "Nodes", ())),
        len(getattr(ResourceGraph, "Edges", ())),
    ))
    if not EndpointDomain.Complete:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete,
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=(Signal,),
            Detail=(
                "the feedthrough endpoint domain requires claim-distinct "
                "path enumeration for a cyclic component fabric"
            ),
            Diagnostics={
                "Signal": Signal,
                "FeedthroughEndpointDomainFingerprint": (
                    EndpointDomain.DomainFingerprint
                ),
                "FeedthroughEndpointDomainComplete": False,
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    if (
        EndpointDomain.Signal != str(Signal)
        or EndpointDomain.Layer != int(Layer)
        or EndpointDomain.ResourceGraphFingerprint
        != CurrentResourceGraphFingerprint
    ):
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch,
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=(Signal,),
            Detail=(
                "the prepared feedthrough endpoint domain identity does "
                "not match the fixed physical assembly"
            ),
            Diagnostics={
                "Signal": Signal,
                "FeedthroughEndpointDomainFingerprint": (
                    EndpointDomain.DomainFingerprint
                ),
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    EndpointCandidates = tuple(
        Candidate
        for Candidate in EndpointDomain.Candidates
        if not any(
            (Node[0], Node[2]) in ReservedPortAccessGuideCells
            for Node in Candidate.ReservedPathNodes
        )
    )
    EndpointPrescreenRejectedCandidateCount = (
        len(EndpointDomain.Candidates) - len(EndpointCandidates)
    )
    CandidateNodes = frozenset(
        Node
        for Candidate in EndpointCandidates
        for Node in (Candidate.Entry, Candidate.Exit)
    )

    def DistanceToComponent(
        Node: tuple[int, int, int],
        Component: frozenset[tuple[int, int]],
    ) -> int:
        return min(
            abs(Node[0] - X) + abs(Node[2] - Z)
            for X, Z in Component
        )

    def JoinExterior(
        Component: frozenset[tuple[int, int]],
        Target: tuple[int, int],
    ) -> tuple[tuple[int, int], ...]:
        """Find the exact exterior stem for one candidate fabric endpoint."""
        Pending = deque(sorted(Component))
        Previous: dict[
            tuple[int, int], tuple[int, int] | None
        ] = {Value: None for Value in Component}
        Reached = Target if Target in Previous else None
        # The search box is a finite exact domain for this fixed physical
        # contract.  Include every obstacle extent; bounding only the guide
        # and selected lane could incorrectly hide the route around a port
        # access halo that extends beyond both.
        # The exterior seam owns exactly one attachment, not the entire
        # interior feedthrough path.
        AllowedInterior = frozenset((Target,))
        SearchExtent = frozenset((
            *OutsideGuide,
            *AllowedInterior,
            *ComponentKeepoutGuideCells,
            *ReservedPortAccessGuideCells,
        ))
        MinimumX = min(Value[0] for Value in SearchExtent) - 2
        MaximumX = max(Value[0] for Value in SearchExtent) + 2
        MinimumZ = min(Value[1] for Value in SearchExtent) - 2
        MaximumZ = max(Value[1] for Value in SearchExtent) + 2
        while Pending and Reached is None:
            Current = Pending.popleft()
            X, Z = Current
            for Neighbor in (
                (X - 1, Z),
                (X + 1, Z),
                (X, Z - 1),
                (X, Z + 1),
            ):
                if (
                    Neighbor in Previous
                    or not (
                        MinimumX <= Neighbor[0] <= MaximumX
                        and MinimumZ <= Neighbor[1] <= MaximumZ
                    )
                    or Neighbor in ReservedPortAccessGuideCells
                    or (
                        Neighbor in ComponentKeepoutGuideCells
                        and Neighbor not in AllowedInterior
                    )
                ):
                    continue
                Previous[Neighbor] = Current
                if Neighbor == Target:
                    Reached = Neighbor
                    break
                Pending.append(Neighbor)
        if Reached is None:
            return ()
        Path = [Reached]
        while Previous[Path[-1]] is not None:
            Parent = Previous[Path[-1]]
            assert Parent is not None
            Path.append(Parent)
        return tuple(reversed(Path))

    Best: tuple[
        tuple[object, ...],
        tuple[tuple[int, int, int], ...],
        tuple[tuple[int, int], ...],
        tuple[tuple[int, int], ...],
        str,
    ] | None = None
    SearchCount = 0
    FabricPathCandidateCount = 2 * len(EndpointCandidates)
    ExteriorJoinRejectedCandidateCount = 0
    if len(ExteriorComponents) > 2:
        PathByEndpoints = {
            frozenset((Candidate.Entry, Candidate.Exit)): (
                tuple(Candidate.ReservedPathNodes),
                Candidate.CandidateFingerprint,
            )
            for Candidate in EndpointCandidates
        }
        JoinsByComponent = tuple(
            {
                Node: JoinExterior(Component, (Node[0], Node[2]))
                for Node in sorted(CandidateNodes)
            }
            for Component in ExteriorComponents
        )
        NodeDomains = tuple(
            tuple(
                Node for Node in sorted(CandidateNodes) if Joins.get(Node)
            )
            for Joins in JoinsByComponent
        )
        MultiBest: tuple[
            tuple[object, ...],
            tuple[tuple[int, int, int], ...],
            tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...],
            tuple[tuple[int, int], ...],
            str,
        ] | None = None
        CombinationCount = prod(len(Domain) for Domain in NodeDomains)
        for CombinationIndex, SelectedNodes in enumerate(
            product(*NodeDomains),
            start=1,
        ):
            if WorkCheck is not None and CombinationIndex % 1024 == 0:
                WorkCheck({
                    "Stage": "physical-component-feedthrough-tree-domain",
                    "Signal": Signal,
                    "CombinationIndex": CombinationIndex,
                    "CombinationCount": CombinationCount,
                    "ExteriorGuideComponentCount": len(ExteriorComponents),
                })
            Root = SelectedNodes[0]
            ReservedNodes = {Root}
            EndpointPairs = []
            CandidateFingerprints = []
            CompleteTree = True
            for Endpoint in SelectedNodes[1:]:
                if Endpoint == Root:
                    continue
                PathValue = PathByEndpoints.get(frozenset((Root, Endpoint)))
                if PathValue is None:
                    CompleteTree = False
                    break
                Path, CandidateFingerprint = PathValue
                ReservedNodes.update(Path)
                EndpointPairs.append((Root, Endpoint))
                CandidateFingerprints.append(CandidateFingerprint)
            if not CompleteTree:
                continue
            Claims = ResourceGraph.BuildRouteClaims(ReservedNodes)
            if FindSelfClaimConflicts({Signal: Claims}):
                continue
            ExteriorJoins = tuple(
                Position
                for ComponentIndex, Node in enumerate(SelectedNodes)
                for Position in JoinsByComponent[ComponentIndex][Node]
            )
            Score = (
                len(ReservedNodes) + len(ExteriorJoins),
                tuple(SelectedNodes),
                tuple(sorted(ReservedNodes)),
            )
            Candidate = (
                Score,
                tuple(sorted(ReservedNodes)),
                tuple(EndpointPairs),
                ExteriorJoins,
                BuildStableFingerprint(tuple(sorted(
                    CandidateFingerprints
                ))),
            )
            if MultiBest is None or Candidate < MultiBest:
                MultiBest = Candidate
        if MultiBest is None:
            raise RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
                ),
                Stage="PhysicalComponentAssemblyPlanning",
                AffectedNets=(Signal,),
                Detail=(
                    "the complete component feedthrough tree domain cannot "
                    "connect every exterior guide component"
                ),
                Diagnostics={
                    "Signal": Signal,
                    "ExteriorGuideComponentCount": len(ExteriorComponents),
                    "FeedthroughTreeCombinationCount": CombinationCount,
                    "FeedthroughCandidateDomainComplete": True,
                    "ComponentFabricConstructionComplete": True,
                    "OwnershipSearchComplete": True,
                    "ImplicitForeignTransitDomainCount": 0,
                },
            ))
        ReservedPathNodes = MultiBest[1]
        EndpointPairs = MultiBest[2]
        ExteriorJoins = MultiBest[3]
        Claims = ResourceGraph.BuildRouteClaims(ReservedPathNodes)
        ReservationFingerprint = BuildStableFingerprint((
            "physical-component-feedthrough-tree-v1",
            int(Layer),
            EndpointPairs,
            tuple(sorted(map(str, Claims.ResourceIds))),
        ))
        Contract = ComponentFeedthroughContract(
            Signal=Signal,
            EndpointPairs=EndpointPairs,
            Capacity=1,
            ReservedPathNodes=ReservedPathNodes,
            Claims=Claims,
            ReservationFingerprint=ReservationFingerprint,
            EndpointDomainFingerprint=EndpointDomain.DomainFingerprint,
            EndpointCandidateFingerprint=MultiBest[4],
            EndpointCandidateCount=CombinationCount,
            EndpointPrescreenRetainedCandidateCount=len(EndpointCandidates),
            EndpointPrescreenRejectedCandidateCount=(
                EndpointPrescreenRejectedCandidateCount
            ),
        )
        return Contract, frozenset((
            *OutsideGuide,
            *((Node[0], Node[2]) for Node in ReservedPathNodes),
            *ExteriorJoins,
        ))

    FirstComponent, SecondComponent = ExteriorComponents
    FirstExteriorJoins = {
        Node: JoinExterior(FirstComponent, (Node[0], Node[2]))
        for Node in sorted(CandidateNodes)
    }
    SecondExteriorJoins = {
        Node: JoinExterior(SecondComponent, (Node[0], Node[2]))
        for Node in sorted(CandidateNodes)
    }
    for EndpointCandidate in EndpointCandidates:
        Path = list(EndpointCandidate.ReservedPathNodes)
        for Entry, Exit, OrientedPath in (
            (Path[0], Path[-1], tuple(Path)),
            (Path[-1], Path[0], tuple(reversed(Path))),
        ):
            ForwardScore = (
                DistanceToComponent(Entry, FirstComponent)
                + DistanceToComponent(Exit, SecondComponent),
                len(OrientedPath),
                Entry,
                Exit,
            )
            if FirstExteriorJoins[Entry] and SecondExteriorJoins[Exit]:
                Candidate = (
                    ForwardScore,
                    OrientedPath,
                    FirstExteriorJoins[Entry],
                    SecondExteriorJoins[Exit],
                    EndpointCandidate.CandidateFingerprint,
                )
                if Best is None or Candidate < Best:
                    Best = Candidate
            else:
                ExteriorJoinRejectedCandidateCount += 1
    if Best is None:
        HasFabricPaths = FabricPathCandidateCount > 0
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
            ),
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=(Signal,),
            Detail=(
                (
                    "the complete explicit feedthrough endpoint domain "
                    "cannot reach both exterior guide components without "
                    "crossing a port claim"
                )
                if HasFabricPaths
                else (
                    "the component fabric has no layer-exact path for an "
                    "explicit foreign feedthrough"
                )
            ),
            Diagnostics={
                "Signal": Signal,
                "Layer": int(Layer),
                "RoutingY": int(RoutingY),
                "FabricLayerNodeCount": len(LayerNodes),
                "FabricBoundaryNodeCount": len(CandidateNodes),
                "FabricPathCandidateCount": FabricPathCandidateCount,
                "ExteriorJoinRejectedCandidateCount": (
                    ExteriorJoinRejectedCandidateCount
                ),
                "FeedthroughEndpointDomainFingerprint": (
                    EndpointDomain.DomainFingerprint
                ),
                "FeedthroughEndpointCandidateCount": len(
                    EndpointDomain.Candidates
                ),
                "FeedthroughEndpointPrescreenRejectedCandidateCount": (
                    EndpointPrescreenRejectedCandidateCount
                ),
                "FeedthroughEndpointPrescreenComplete": True,
                "FeedthroughCandidateDomainComplete": True,
                "ComponentFabricConstructionComplete": True,
                "OwnershipSearchComplete": True,
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    ReservedPathNodes = Best[1]
    ReservedGuideCells = frozenset(
        (Node[0], Node[2]) for Node in ReservedPathNodes
    )

    FirstJoin = Best[2]
    SecondJoin = Best[3]
    Claims = ResourceGraph.BuildRouteClaims(ReservedPathNodes)
    ReservationFingerprint = BuildStableFingerprint((
        "physical-component-feedthrough-v1",
        int(Layer),
        tuple(
            (
                Node[0] - ReservedPathNodes[0][0],
                Node[1] - ReservedPathNodes[0][1],
                Node[2] - ReservedPathNodes[0][2],
            )
            for Node in ReservedPathNodes
        ),
        tuple(sorted(map(str, Claims.ResourceIds))),
    ))
    Contract = ComponentFeedthroughContract(
        Signal=Signal,
        EndpointPairs=((ReservedPathNodes[0], ReservedPathNodes[-1]),),
        Capacity=1,
        ReservedPathNodes=ReservedPathNodes,
        Claims=Claims,
        ReservationFingerprint=ReservationFingerprint,
        EndpointDomainFingerprint=EndpointDomain.DomainFingerprint,
        EndpointCandidateFingerprint=Best[4],
        EndpointCandidateCount=len(EndpointDomain.Candidates),
        EndpointPrescreenRetainedCandidateCount=len(EndpointCandidates),
        EndpointPrescreenRejectedCandidateCount=(
            EndpointPrescreenRejectedCandidateCount
        ),
    )
    UpdatedGuide = frozenset((
        *OutsideGuide,
        *FirstJoin,
        *ReservedGuideCells,
        *SecondJoin,
    ))
    return Contract, UpdatedGuide

def BuildComponentKeepoutAvoidingGlobalGuides(
    CoarsePlan: Any,
    *,
    ComponentPortSignals: frozenset[str],
    EnvelopeMinimum: tuple[int, int, int],
    EnvelopeMaximum: tuple[int, int, int],
    TrackPitch: int,
    ReservedPortGuideCells: frozenset[tuple[int, int]] = frozenset(),
    ComponentKeepoutGuideCells: (
        frozenset[tuple[int, int]] | None
    ) = None,
    ComponentKeepoutGuideCellsByLayer: (
        Mapping[int, frozenset[tuple[int, int]]] | None
    ) = None,
    DeclaredFeedthroughSignals: frozenset[str] = frozenset(),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[Any, tuple[str, ...]]:
    """Reserve connected exterior corridors for ordinary global nets."""
    if CoarsePlan is None:
        return CoarsePlan, ()
    Guides = dict(CoarsePlan.Guides)
    Layers = dict(CoarsePlan.Layers)
    SlotsByLayer: Counter[int] = Counter()
    DetouredSignals = []

    def InsideEnvelope(Position: tuple[int, int]) -> bool:
        X, Z = Position
        return bool(
            EnvelopeMinimum[0] <= X <= EnvelopeMaximum[0]
            and EnvelopeMinimum[2] <= Z <= EnvelopeMaximum[2]
        )

    if (
        ComponentKeepoutGuideCells is None
        and ComponentKeepoutGuideCellsByLayer is None
    ):
        DefaultComponentKeepoutCore = frozenset(
            (X, Z)
            for X in range(
                EnvelopeMinimum[0],
                EnvelopeMaximum[0] + 1,
            )
            for Z in range(
                EnvelopeMinimum[2],
                EnvelopeMaximum[2] + 1,
            )
        )
    else:
        DefaultComponentKeepoutCore = (
            ComponentKeepoutGuideCells or frozenset()
        )
    KeepoutClearance = max(1, int(TrackPitch))
    ComponentKeepoutHaloByLayer: dict[
        int, frozenset[tuple[int, int]]
    ] = {}

    def ComponentKeepoutHaloForLayer(
        Layer: int,
    ) -> frozenset[tuple[int, int]]:
        Cached = ComponentKeepoutHaloByLayer.get(Layer)
        if Cached is not None:
            return Cached
        Core = (
            ComponentKeepoutGuideCellsByLayer.get(Layer, frozenset())
            if ComponentKeepoutGuideCellsByLayer is not None
            else DefaultComponentKeepoutCore
        )
        Result = frozenset(
            (X + DeltaX, Z + DeltaZ)
            for X, Z in Core
            for DeltaX in range(-KeepoutClearance, KeepoutClearance + 1)
            for DeltaZ in range(-KeepoutClearance, KeepoutClearance + 1)
            if abs(DeltaX) + abs(DeltaZ) <= KeepoutClearance
        )
        ComponentKeepoutHaloByLayer[Layer] = Result
        return Result

    PortClearance = max(1, int(TrackPitch))
    ReservedPortAccessHaloCache: dict[
        int, frozenset[tuple[int, int]]
    ] = {}

    def ReservedPortAccessHaloForLayer(
        Layer: int,
    ) -> frozenset[tuple[int, int]]:
        Cached = ReservedPortAccessHaloCache.get(Layer)
        if Cached is not None:
            return Cached
        Core = ReservedPortGuideCells
        Result = frozenset(
            (X + DeltaX, Z + DeltaZ)
            for X, Z in Core
            for DeltaX in range(-PortClearance, PortClearance + 1)
            for DeltaZ in range(-PortClearance, PortClearance + 1)
            if abs(DeltaX) + abs(DeltaZ) <= PortClearance
        )
        ReservedPortAccessHaloCache[Layer] = Result
        return Result

    def Components(
        Nodes: frozenset[tuple[int, int]],
    ) -> tuple[frozenset[tuple[int, int]], ...]:
        Remaining = set(Nodes)
        Result = []
        while Remaining:
            Start = min(Remaining)
            Pending = deque((Start,))
            Reached = {Start}
            Remaining.remove(Start)
            while Pending:
                X, Z = Pending.popleft()
                for Neighbor in (
                    (X - 1, Z),
                    (X + 1, Z),
                    (X, Z - 1),
                    (X, Z + 1),
                ):
                    if Neighbor in Remaining:
                        Remaining.remove(Neighbor)
                        Reached.add(Neighbor)
                        Pending.append(Neighbor)
            Result.append(frozenset(Reached))
        return tuple(Result)

    for SignalIndex, Signal in enumerate(sorted(Guides), start=1):
        if WorkCheck is not None:
            WorkCheck({
                "Stage": "physical-global-channel-detour",
                "Signal": Signal,
                "ProcessedSignalCount": SignalIndex - 1,
                "SignalCount": len(Guides),
                "DetouredSignalCount": len(DetouredSignals),
            })
        if Signal in ComponentPortSignals:
            continue
        if Signal in DeclaredFeedthroughSignals:
            continue
        Guide = frozenset(Guides[Signal])
        Layer = int(Layers.get(Signal, 0))
        ComponentKeepoutHaloCells = ComponentKeepoutHaloForLayer(Layer)
        CurrentPortAccessHalo = ReservedPortAccessHaloForLayer(Layer)
        ReservedPortAccessHaloCells = (
            ReservedPortAccessHaloForLayer(Layer)
        )
        if not (
            bool(Guide & ComponentKeepoutHaloCells)
            or bool(Guide & ReservedPortAccessHaloCells)
        ):
            continue
        OutsideGuide = frozenset(
            Value
            for Value in Guide
            if (
                Value not in ComponentKeepoutHaloCells
                and Value not in ReservedPortAccessHaloCells
            )
        )
        if not OutsideGuide:
            raise RoutingStageError(RoutingFailure(
                Reason=(
                    RoutingFailureReason
                    .ComponentChannelCapacityUnsatisfiable
                ),
                Stage="PhysicalComponentAssemblyPlanning",
                AffectedNets=(Signal,),
                Detail=(
                    "a foreign global guide has no exterior segment after "
                    "applying component port and keepout reservations"
                ),
                Diagnostics={
                    "Signal": Signal,
                    "GuideCellCount": len(Guide),
                    "ReservedPortAccessCellCount": len(
                        ReservedPortAccessHaloCells
                    ),
                    "ImplicitForeignTransitDomainCount": 0,
                },
            ))
        Slot = SlotsByLayer[Layer]
        SlotsByLayer[Layer] += 1
        Margin = max(1, int(TrackPitch)) * (Slot + 1)
        ReservedMinimumX = min(
            (Value[0] for Value in ReservedPortAccessHaloCells),
            default=EnvelopeMinimum[0],
        )
        ReservedMaximumX = max(
            (Value[0] for Value in ReservedPortAccessHaloCells),
            default=EnvelopeMaximum[0],
        )
        ReservedMinimumZ = min(
            (Value[1] for Value in ReservedPortAccessHaloCells),
            default=EnvelopeMinimum[2],
        )
        ReservedMaximumZ = max(
            (Value[1] for Value in ReservedPortAccessHaloCells),
            default=EnvelopeMaximum[2],
        )
        # The authoritative obstacle is the projected physical keepout, not
        # merely the logical component envelope used to seed it.  Electrical
        # exclusions can extend beyond that envelope; bounding the exterior
        # search only from the logical box can therefore place the obstacle
        # directly on the search boundary and falsely prove that two exterior
        # guide components cannot be joined.  Include the exact layer-owned
        # keepout extent so the global router always has the declared margin
        # in which to route around a closed component.
        KeepoutMinimumX = min(
            (Value[0] for Value in ComponentKeepoutHaloCells),
            default=EnvelopeMinimum[0],
        )
        KeepoutMaximumX = max(
            (Value[0] for Value in ComponentKeepoutHaloCells),
            default=EnvelopeMaximum[0],
        )
        KeepoutMinimumZ = min(
            (Value[1] for Value in ComponentKeepoutHaloCells),
            default=EnvelopeMinimum[2],
        )
        KeepoutMaximumZ = max(
            (Value[1] for Value in ComponentKeepoutHaloCells),
            default=EnvelopeMaximum[2],
        )
        MinimumX = min(
            min(Value[0] for Value in OutsideGuide),
            EnvelopeMinimum[0] - Margin,
            ReservedMinimumX - Margin,
            KeepoutMinimumX - Margin,
        ) - 1
        MaximumX = max(
            max(Value[0] for Value in OutsideGuide),
            EnvelopeMaximum[0] + Margin,
            ReservedMaximumX + Margin,
            KeepoutMaximumX + Margin,
        ) + 1
        MinimumZ = min(
            min(Value[1] for Value in OutsideGuide),
            EnvelopeMinimum[2] - Margin,
            ReservedMinimumZ - Margin,
            KeepoutMinimumZ - Margin,
        ) - 1
        MaximumZ = max(
            max(Value[1] for Value in OutsideGuide),
            EnvelopeMaximum[2] + Margin,
            ReservedMaximumZ + Margin,
            KeepoutMaximumZ + Margin,
        ) + 1
        GuideComponents = Components(OutsideGuide)
        SourcePath = tuple(getattr(
            CoarsePlan,
            "SourceAccessTransitions",
            {},
        ).get(Signal, ()))
        TargetPaths = tuple(getattr(
            CoarsePlan,
            "TargetAccessTransitions",
            {},
        ).get(Signal, {}).values())
        RequiredAccessPaths = tuple(
            Path for Path in (SourcePath, *TargetPaths) if Path
        )
        if RequiredAccessPaths:
            RequiredComponentIndices = set()
            for AccessPath in RequiredAccessPaths:
                # Access-transition order terminates at the authoritative
                # portal/guide handoff.  Intermediate access cells may cross
                # stale coarse-guide islands and must not make those islands
                # globally owned anchors.
                AccessCells = frozenset(((
                    AccessPath[-1][0],
                    AccessPath[-1][2],
                ),))
                Intersecting = tuple(
                    Index
                    for Index, Component in enumerate(GuideComponents)
                    if Component & AccessCells
                )
                if Intersecting:
                    RequiredComponentIndices.add(Intersecting[0])
                    continue
                RequiredComponentIndices.add(min(
                    range(len(GuideComponents)),
                    key=lambda Index: (
                        min(
                            abs(GuideCell[0] - AccessCell[0])
                            + abs(GuideCell[1] - AccessCell[1])
                            for GuideCell in GuideComponents[Index]
                            for AccessCell in AccessCells
                        ),
                        Index,
                    ),
                ))
            GuideComponents = tuple(
                Component
                for Index, Component in enumerate(GuideComponents)
                if Index in RequiredComponentIndices
            )
            OutsideGuide = frozenset(
                Cell
                for Component in GuideComponents
                for Cell in Component
            )
        Connected = set(GuideComponents[0])
        DetourNodes: set[tuple[int, int]] = set()
        DetourComplete = True
        for TargetComponent in GuideComponents[1:]:
            TargetMinimumX = min(Value[0] for Value in TargetComponent)
            TargetMaximumX = max(Value[0] for Value in TargetComponent)
            TargetMinimumZ = min(Value[1] for Value in TargetComponent)
            TargetMaximumZ = max(Value[1] for Value in TargetComponent)

            def TargetDistance(Value: tuple[int, int]) -> int:
                X, Z = Value
                return (
                    max(0, TargetMinimumX - X, X - TargetMaximumX)
                    + max(0, TargetMinimumZ - Z, Z - TargetMaximumZ)
                )

            Pending: list[tuple[int, int, int, int]] = []
            Distance: dict[tuple[int, int], int] = {}
            Previous: dict[
                tuple[int, int], tuple[int, int] | None
            ] = {}
            for Value in sorted(Connected):
                Distance[Value] = 0
                Previous[Value] = None
                heappush(Pending, (
                    TargetDistance(Value),
                    0,
                    Value[0],
                    Value[1],
                ))
            Reached = next(
                (
                    Value
                    for Value in sorted(Connected)
                    if Value in TargetComponent
                ),
                None,
            )
            while Pending and Reached is None:
                _Estimate, CurrentDistance, X, Z = heappop(Pending)
                Current = (X, Z)
                if Distance.get(Current) != CurrentDistance:
                    continue
                if WorkCheck is not None and len(Previous) % 1024 == 0:
                    WorkCheck({
                        "Stage": "physical-global-channel-detour-search",
                        "Signal": Signal,
                        "VisitedNodeCount": len(Previous),
                        "ExteriorGuideComponentCount": len(GuideComponents),
                    })
                NeighborValues = (
                    (X - 1, Z),
                    (X + 1, Z),
                    (X, Z - 1),
                    (X, Z + 1),
                )
                for Neighbor in NeighborValues:
                    NeighborDistance = CurrentDistance + 1
                    if (
                        NeighborDistance
                        >= Distance.get(Neighbor, NeighborDistance + 1)
                        or not (
                            MinimumX <= Neighbor[0] <= MaximumX
                            and MinimumZ <= Neighbor[1] <= MaximumZ
                        )
                        or Neighbor in ComponentKeepoutHaloCells
                        or Neighbor in ReservedPortAccessHaloCells
                    ):
                        continue
                    Distance[Neighbor] = NeighborDistance
                    Previous[Neighbor] = Current
                    if Neighbor in TargetComponent:
                        Reached = Neighbor
                        break
                    heappush(Pending, (
                        NeighborDistance + TargetDistance(Neighbor),
                        NeighborDistance,
                        Neighbor[0],
                        Neighbor[1],
                    ))
            if Reached is None:
                DetourComplete = False
                break
            Path = [Reached]
            while Previous[Path[-1]] is not None:
                Parent = Previous[Path[-1]]
                assert Parent is not None
                Path.append(Parent)
            DetourNodes.update(Path)
            Connected.update(TargetComponent)
            Connected.update(Path)
        if DetourComplete:
            Guides[Signal] = frozenset((
                *OutsideGuide,
                *DetourNodes,
            ))
            DetouredSignals.append(Signal)
            continue
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason
                .ComponentChannelCapacityUnsatisfiable
            ),
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=(Signal,),
            Detail=(
                "a foreign global guide cannot bypass the reserved "
                "component port-access domain"
            ),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "ConflictFingerprint": BuildStableFingerprint((
                    "physical-global-guide-detour-unsat-v1",
                    Signal,
                    int(Layers.get(Signal, 0)),
                    tuple(tuple(sorted(Value)) for Value in GuideComponents),
                    tuple(sorted(ComponentKeepoutHaloCells)),
                    tuple(sorted(ReservedPortAccessHaloCells)),
                    (MinimumX, MaximumX, MinimumZ, MaximumZ),
                )),
                "ConflictGraph": {
                    "Classification": (
                        "physical-component-global-capacity-cut"
                    ),
                    "ConflictSignals": [Signal],
                    "NoCandidateSignals": [Signal],
                    "RelocationSignals": [Signal],
                    "PriorityRelocationSignals": [Signal],
                    "CompleteAssignmentCutProof": True,
                },
                "Signal": Signal,
                "ExteriorGuideComponentCount": len(GuideComponents),
                "ReservedPortAccessCellCount": len(
                    ReservedPortAccessHaloCells
                ),
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))

    Usage = Counter(
        (
            int(Layers.get(Signal, 0)),
            X,
            Z,
        )
        for Signal, Guide in Guides.items()
        for X, Z in Guide
    )
    Overflow = {
        Position: Count - 1
        for Position, Count in Usage.items()
        if Count > 1
    }
    PlanFields = getattr(CoarsePlan, "__dataclass_fields__", {})
    Changes: dict[str, object] = {
        "Guides": Guides,
        "Layers": Layers,
    }
    if "Usage" in PlanFields:
        Changes["Usage"] = dict(Usage)
    if "Overflow" in PlanFields:
        Changes["Overflow"] = Overflow
    if "CorridorUsage" in PlanFields:
        Changes["CorridorUsage"] = dict(Counter(
            Position
            for Guide in Guides.values()
            for Position in Guide
        ))
    return replace(CoarsePlan, **Changes), tuple(DetouredSignals)

def RemoveClosedComponentInternalGuides(
    CoarsePlan: Any,
    ComponentInternalSignals: frozenset[str],
) -> Any:
    """Transfer internal-net ownership out of the frozen global plan."""
    if CoarsePlan is None or not ComponentInternalSignals:
        return CoarsePlan
    PlanFields = getattr(CoarsePlan, "__dataclass_fields__", {})
    Guides = {
        Signal: Guide
        for Signal, Guide in CoarsePlan.Guides.items()
        if Signal not in ComponentInternalSignals
    }
    Changes: dict[str, object] = {"Guides": Guides}
    for FieldName in (
        "Layers",
        "Axes",
        "Lanes",
        "Profiles",
        "ResourceClaimsBySignal",
        "SourceAccessTransitions",
        "TargetAccessTransitions",
    ):
        if FieldName not in PlanFields:
            continue
        Values = getattr(CoarsePlan, FieldName)
        Changes[FieldName] = {
            Signal: Value
            for Signal, Value in Values.items()
            if Signal not in ComponentInternalSignals
        }
    if "SignalOrder" in PlanFields:
        Changes["SignalOrder"] = tuple(
            Signal
            for Signal in CoarsePlan.SignalOrder
            if Signal not in ComponentInternalSignals
        )
    if "TrunkSignals" in PlanFields:
        Changes["TrunkSignals"] = frozenset(
            set(CoarsePlan.TrunkSignals) - ComponentInternalSignals
        )
    if "LocalSignals" in PlanFields:
        Changes["LocalSignals"] = frozenset(
            set(CoarsePlan.LocalSignals) - ComponentInternalSignals
        )
    Layers = Changes.get("Layers", getattr(CoarsePlan, "Layers", {}))
    Usage = Counter(
        (int(Layers.get(Signal, 0)), X, Z)
        for Signal, Guide in Guides.items()
        for X, Z in Guide
    )
    if "Usage" in PlanFields:
        Changes["Usage"] = dict(Usage)
    if "Overflow" in PlanFields:
        Changes["Overflow"] = {
            Position: Count - 1
            for Position, Count in Usage.items()
            if Count > 1
        }
    if "CorridorUsage" in PlanFields:
        Changes["CorridorUsage"] = dict(Counter(
            Position
            for Guide in Guides.values()
            for Position in Guide
        ))
    if "ResourceClaimsBySignal" in Changes:
        ResourceUsage = Counter(
            Resource
            for Claims in Changes["ResourceClaimsBySignal"].values()
            for Resource in Claims
        )
        if "ResourceUsage" in PlanFields:
            Changes["ResourceUsage"] = dict(ResourceUsage)
        if "ResourceOverflow" in PlanFields:
            Capacity = max(1, int(getattr(
                CoarsePlan,
                "CorridorCapacity",
                1,
            )))
            Changes["ResourceOverflow"] = {
                Resource: Count - Capacity
                for Resource, Count in ResourceUsage.items()
                if Count > Capacity
            }
    return replace(CoarsePlan, **Changes)

def SelectPhysicalPortBankWitnesses(
    PortDomains: Iterable[Any],
    *,
    RequiredLayerBySignal: dict[str, int],
    ForeignGuideCells: frozenset[tuple[int, int]],
    ResourceGraph: Any,
    TrackPitch: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, Any]:
    """Choose one immutable, jointly compatible physical witness per port."""
    Pitch = max(1, int(TrackPitch))
    CandidateClaims: dict[str, RoutingResourceClaims] = {}
    CandidateGuideCells: dict[str, frozenset[tuple[int, int]]] = {}

    def ClaimsFor(Candidate: Any) -> RoutingResourceClaims:
        Cached = CandidateClaims.get(Candidate.CandidateFingerprint)
        if Cached is not None:
            return Cached
        LocalPath = tuple(Candidate.LocalPath)
        OutwardPath = ()
        if len(LocalPath) >= 2:
            Direction = tuple(
                LocalPath[-1][Index] - LocalPath[-2][Index]
                for Index in range(3)
            )
            OutwardPath = tuple(
                tuple(
                    LocalPath[-1][Index] + Distance * Direction[Index]
                    for Index in range(3)
                )
                for Distance in range(1, Pitch + 1)
            )
        Result = ResourceGraph.BuildRouteClaims(frozenset((
            *LocalPath,
            *OutwardPath,
        )))
        CandidateClaims[Candidate.CandidateFingerprint] = Result
        CandidateGuideCells[Candidate.CandidateFingerprint] = frozenset(
            (Position[0], Position[2])
            for Position in (
                *Result.WireCells,
                *Result.SupportCells,
                *Result.RequiredAirCells,
                *Result.ElectricalCells,
            )
        )
        return Result

    DomainsBySignal = {}
    for Domain in PortDomains:
        RequiredLayer = RequiredLayerBySignal.get(Domain.Signal)
        Candidates = tuple(
            Candidate
            for Candidate in Domain.Candidates
            if (
                RequiredLayer is None
                or int(Candidate.Layer) == int(RequiredLayer)
            )
        )
        if not Candidates:
            raise RoutingStageError(RoutingFailure(
                Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
                Stage="PhysicalComponentAssemblyPlanning",
                AffectedNets=(Domain.Signal,),
                Detail=(
                    "the complete local port domain has no witness on the "
                    "authoritative global layer"
                ),
                Diagnostics={
                    "Signal": Domain.Signal,
                    "RequiredLayer": RequiredLayer,
                    "ImplicitForeignTransitDomainCount": 0,
                },
            ))
        for Candidate in Candidates:
            ClaimsFor(Candidate)
        DomainsBySignal[Domain.Signal] = tuple(sorted(
            Candidates,
            key=lambda Candidate: (
                len(
                    CandidateGuideCells[Candidate.CandidateFingerprint]
                    & ForeignGuideCells
                ),
                len(CandidateClaims[
                    Candidate.CandidateFingerprint
                ].ResourceIds),
                Candidate.CandidateFingerprint,
            ),
        ))

    SignalOrder = tuple(sorted(
        DomainsBySignal,
        key=lambda Signal: (len(DomainsBySignal[Signal]), Signal),
    ))
    Selected: dict[str, Any] = {}
    SelectedClaims: dict[str, RoutingResourceClaims] = {}
    ExpansionCount = 0

    def Search(Index: int) -> bool:
        nonlocal ExpansionCount
        if Index >= len(SignalOrder):
            return True
        Signal = SignalOrder[Index]
        for Candidate in DomainsBySignal[Signal]:
            ExpansionCount += 1
            if WorkCheck is not None and ExpansionCount % 256 == 0:
                WorkCheck({
                    "Stage": "physical-port-bank-assignment",
                    "Signal": Signal,
                    "ExpansionCount": ExpansionCount,
                })
            Claims = CandidateClaims[Candidate.CandidateFingerprint]
            if FindClaimConflicts({**SelectedClaims, Signal: Claims}):
                continue
            Selected[Signal] = Candidate
            SelectedClaims[Signal] = Claims
            if Search(Index + 1):
                return True
            Selected.pop(Signal, None)
            SelectedClaims.pop(Signal, None)
        return False

    if not Search(0):
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            Stage="PhysicalComponentAssemblyPlanning",
            AffectedNets=SignalOrder,
            Detail=(
                "complete physical port-bank assignment proves no jointly "
                "compatible fixed witness exists"
            ),
            Diagnostics={
                "ExpansionCount": ExpansionCount,
                "PortDomainSizes": {
                    Signal: len(Values)
                    for Signal, Values in sorted(DomainsBySignal.items())
                },
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    return Selected

def DecomposePhysicalPortLaneFactors(
    LaneFactorsBySignal: Mapping[
        str, tuple[PhysicalPortLaneFactor, ...]
    ],
    ChannelReservations: Iterable[PhysicalComponentChannelReservation],
    ResourceGraph: Any,
    *,
    FabricOrigin: tuple[int, int, int],
) -> tuple[
    tuple[tuple[str, tuple[PhysicalPortLocalAccessFactor, ...]], ...],
    tuple[tuple[str, tuple[PhysicalPortApertureOptionFactor, ...]], ...],
    tuple[tuple[str, tuple[PhysicalPortLocalApertureSupport, ...]], ...],
]:
    """Split certified composite seams without inventing Cartesian support.

    The preparation stage historically carried a local path and its global
    aperture as one lane value.  This projection makes both variables
    explicit while retaining an edge only when the original certified seam
    proves that exact pair legal.
    """
    ChannelBySignal = {
        Value.Signal: Value for Value in ChannelReservations
    }
    LocalBySignal: dict[
        str, dict[str, PhysicalPortLocalAccessFactor]
    ] = defaultdict(dict)
    ApertureBySignal: dict[
        str, dict[str, PhysicalPortApertureOptionFactor]
    ] = defaultdict(dict)
    SupportBySignal: dict[
        str, dict[
            tuple[str, str], PhysicalPortLocalApertureSupport
        ]
    ] = defaultdict(dict)

    def RelativePath(
        Path: Iterable[tuple[int, int, int]],
    ) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            tuple(
                int(Position[Index]) - int(FabricOrigin[Index])
                for Index in range(3)
            )
            for Position in Path
        )

    for Signal, LaneFactors in sorted(LaneFactorsBySignal.items()):
        Channel = ChannelBySignal.get(Signal)
        ChannelFingerprint = BuildStableFingerprint((
            "physical-port-channel-contract-v1",
            int(getattr(Channel, "Layer", 0)),
            tuple(sorted(getattr(Channel, "GuideCells", ()))),
            int(getattr(Channel, "Capacity", 1)),
            tuple(sorted(getattr(
                Channel,
                "FeedthroughComponentIds",
                (),
            ))),
        ))
        for LaneFactor in sorted(
            LaneFactors,
            key=lambda Value: (
                Value.FabricDomainFingerprint,
                Value.OwnedTerminals,
            ),
        ):
            for Seam in sorted(
                LaneFactor.Seams,
                key=lambda Value: Value.SeamFingerprint,
            ):
                OwnedCandidateFingerprints = frozenset(
                    Seam.OwnedCandidateFingerprints
                )
                OwnedAccessCandidates = tuple(
                    Candidate
                    for CandidateDomain in LaneFactor.CandidateDomains
                    for Candidate in CandidateDomain
                    if Candidate.CandidateFingerprint
                    in OwnedCandidateFingerprints
                )
                LocalClaims = ResourceGraph.BuildRouteClaims(
                    frozenset(Seam.LocalPath)
                )
                GlobalClaims = ResourceGraph.BuildRouteClaims(
                    frozenset(Seam.GlobalPath)
                )
                ReservationFingerprint = BuildStableFingerprint((
                    LaneFactor.Direction,
                    LaneFactor.FabricDomainFingerprint,
                    RelativePath(Seam.LocalPath),
                    RelativePath(Seam.GlobalPath),
                    LaneFactor.Capacity,
                ))
                Port = PhysicalComponentPortReservation(
                    Signal=Signal,
                    Direction=LaneFactor.Direction,
                    OwnedTerminals=LaneFactor.OwnedTerminals,
                    OwnedTerminalFingerprints=tuple(
                        Domain.TerminalFingerprint
                        for Domain in LaneFactor.Domains
                    ),
                    OwnedCandidateFingerprints=(
                        Seam.OwnedCandidateFingerprints
                    ),
                    FabricDomainFingerprint=(
                        LaneFactor.FabricDomainFingerprint
                    ),
                    FabricAttachment=Seam.FabricAttachment,
                    Attachment=Seam.Attachment,
                    LocalPath=Seam.LocalPath,
                    GlobalPath=Seam.GlobalPath,
                    Claims=Seam.Claims,
                    LocalClaims=LocalClaims,
                    GlobalClaims=GlobalClaims,
                    OwnedAccessCandidates=OwnedAccessCandidates,
                    Capacity=LaneFactor.Capacity,
                    ReservationFingerprint=ReservationFingerprint,
                )
                LocalContractFingerprint = (
                    BuildPhysicalPortLocalContractFingerprint(Port)
                )
                GlobalContractFingerprint = (
                    BuildPhysicalPortGlobalContractFingerprint(Port)
                )
                ApertureContractFingerprint = (
                    BuildPhysicalPortApertureContractFingerprint(Port)
                )
                LocalAccessFingerprint = BuildStableFingerprint((
                    "physical-port-local-access-v1",
                    LocalContractFingerprint,
                ))
                ApertureOptionFingerprint = BuildStableFingerprint((
                    "physical-port-aperture-option-v1",
                    GlobalContractFingerprint,
                    ApertureContractFingerprint,
                    ChannelFingerprint,
                ))
                LocalFactor = PhysicalPortLocalAccessFactor(
                    Signal=Signal,
                    Direction=LaneFactor.Direction,
                    Capacity=LaneFactor.Capacity,
                    OwnedTerminals=LaneFactor.OwnedTerminals,
                    OwnedTerminalFingerprints=tuple(
                        Domain.TerminalFingerprint
                        for Domain in LaneFactor.Domains
                    ),
                    OwnedAccessCandidates=OwnedAccessCandidates,
                    FabricDomainFingerprint=(
                        LaneFactor.FabricDomainFingerprint
                    ),
                    FabricAttachment=Seam.FabricAttachment,
                    LocalPath=Seam.LocalPath,
                    LocalClaims=LocalClaims,
                    OwnedCandidateFingerprints=(
                        Seam.OwnedCandidateFingerprints
                    ),
                    LocalContractFingerprint=LocalContractFingerprint,
                    LocalAccessFingerprint=LocalAccessFingerprint,
                )
                LocalFactor = replace(
                    LocalFactor,
                    SeamContractFingerprint=(
                        BuildPhysicalPortSeamContractFingerprint(
                            LocalFactor
                        )
                    ),
                )
                ApertureFactor = PhysicalPortApertureOptionFactor(
                    Signal=Signal,
                    Direction=LaneFactor.Direction,
                    Capacity=LaneFactor.Capacity,
                    Attachment=Seam.Attachment,
                    GlobalPath=Seam.GlobalPath,
                    GlobalClaims=GlobalClaims,
                    ChannelContractFingerprint=ChannelFingerprint,
                    GlobalContractFingerprint=GlobalContractFingerprint,
                    ApertureContractFingerprint=(
                        ApertureContractFingerprint
                    ),
                    ApertureOptionFingerprint=ApertureOptionFingerprint,
                )
                ExistingLocal = LocalBySignal[Signal].get(
                    LocalAccessFingerprint
                )
                if ExistingLocal is not None and ExistingLocal != LocalFactor:
                    raise ValueError("physical local-access identity collision")
                LocalBySignal[Signal][LocalAccessFingerprint] = LocalFactor
                ExistingAperture = ApertureBySignal[Signal].get(
                    ApertureOptionFingerprint
                )
                if (
                    ExistingAperture is not None
                    and ExistingAperture != ApertureFactor
                ):
                    raise ValueError("physical aperture identity collision")
                ApertureBySignal[Signal][
                    ApertureOptionFingerprint
                ] = ApertureFactor
                SupportFingerprint = BuildStableFingerprint((
                    "physical-port-local-aperture-support-v1",
                    LocalAccessFingerprint,
                    ApertureOptionFingerprint,
                ))
                SupportKey = (
                    LocalAccessFingerprint,
                    ApertureOptionFingerprint,
                )
                ExistingSupport = SupportBySignal[Signal].get(SupportKey)
                RetainExistingSupport = bool(
                    ExistingSupport is not None
                    and ExistingSupport.SourceSeamFingerprint
                    <= Seam.SeamFingerprint
                )
                Support = PhysicalPortLocalApertureSupport(
                    Signal=Signal,
                    LocalAccessFingerprint=LocalAccessFingerprint,
                    ApertureOptionFingerprint=ApertureOptionFingerprint,
                    SourceSeamFingerprint=(
                        ExistingSupport.SourceSeamFingerprint
                        if RetainExistingSupport
                        else Seam.SeamFingerprint
                    ),
                    ReservationFingerprint=(
                        ExistingSupport.ReservationFingerprint
                        if RetainExistingSupport
                        else ReservationFingerprint
                    ),
                    SupportFingerprint=SupportFingerprint,
                )
                SupportBySignal[Signal][SupportKey] = Support

    Signals = tuple(sorted(LaneFactorsBySignal))
    return (
        tuple(
            (Signal, tuple(
                LocalBySignal[Signal][Fingerprint]
                for Fingerprint in sorted(LocalBySignal[Signal])
            ))
            for Signal in Signals
        ),
        tuple(
            (Signal, tuple(
                ApertureBySignal[Signal][Fingerprint]
                for Fingerprint in sorted(ApertureBySignal[Signal])
            ))
            for Signal in Signals
        ),
        tuple(
            (Signal, tuple(
                SupportBySignal[Signal][Key]
                for Key in sorted(SupportBySignal[Signal])
            ))
            for Signal in Signals
        ),
    )

def PreparePhysicalSignalLocalFactorDomain(
    Problem: ComponentRoutingProblem,
    AccessCertificate: ComponentCutAccessFeasibilityCertificate,
    Signal: str,
    ResourceGraph: Any,
    *,
    LocalAccessFactors: Iterable[PhysicalPortLocalAccessFactor],
    TechnologyFingerprint: str = "",
) -> PreparedPhysicalSignalLocalFactorDomain:
    """Freeze the exact local inputs that may survive a placement repair.

    This intentionally accepts no guide, aperture, or exterior-path input.
    Those facts are reconstructed by the caller for every placement.
    """
    Signal = str(Signal)
    Factors = tuple(sorted(
        LocalAccessFactors,
        key=lambda Value: str(Value.LocalAccessFingerprint),
    ))
    CertificatePortDomains = tuple(sorted(
        (
            Domain
            for Domain in AccessCertificate.PortDomains
            if str(Domain.Signal) == Signal
        ),
        key=lambda Value: (str(Value.Direction), str(Value.Signal)),
    ))
    TerminalDomains = tuple(sorted(
        (
            Domain
            for Domain in getattr(Problem, "OwnedTerminalDomains", ())
            if str(Domain.Signal) == Signal
        ),
        key=lambda Value: (
            str(Value.TerminalFingerprint),
            str(Value.TerminalRole),
            tuple(Value.Terminal),
        ),
    ))
    TerminalContract = tuple(
        (
            str(Domain.TerminalFingerprint),
            tuple(map(int, Domain.Terminal)),
            str(Domain.TerminalRole),
            tuple(sorted(
                str(Candidate.CandidateFingerprint)
                for Candidate in Domain.Candidates
            )),
        )
        for Domain in TerminalDomains
    )
    LocalGeometry = tuple(
        (
            str(Factor.FabricDomainFingerprint),
            tuple(map(int, Factor.FabricAttachment)),
            tuple(tuple(map(int, Node)) for Node in Factor.LocalPath),
            tuple(sorted(map(str, Factor.OwnedCandidateFingerprints))),
        )
        for Factor in Factors
    )
    LocalClaims = tuple(
        sorted(
            (
                str(Factor.LocalAccessFingerprint),
                tuple(sorted(map(str, Factor.LocalClaims.ResourceIds))),
                tuple(sorted(map(str, Factor.LocalClaims.WireCells))),
                tuple(sorted(map(str, Factor.LocalClaims.SupportCells))),
                tuple(sorted(map(str, Factor.LocalClaims.RequiredAirCells))),
                tuple(sorted(map(str, Factor.LocalClaims.ElectricalCells))),
            )
            for Factor in Factors
        )
    )
    CertifiedLocalWitnesses = tuple(
        (
            str(Domain.Direction),
            tuple(sorted(
                (
                    str(Candidate.CandidateFingerprint),
                    str(Candidate.FabricDomainFingerprint),
                    tuple(map(int, Candidate.FabricAttachment)),
                    tuple(tuple(map(int, Node)) for Node in Candidate.LocalPath),
                    tuple(sorted(map(str, Candidate.OwnedCandidateFingerprints))),
                    tuple(sorted(map(str, Candidate.Claims.ResourceIds))),
                )
                for Candidate in Domain.Candidates
            )),
        )
        for Domain in CertificatePortDomains
    )
    ComponentTopologyFingerprint = str(getattr(
        Problem.Fabric,
        "FabricFingerprint",
        "",
    ))
    TerminalContractFingerprint = BuildStableFingerprint(TerminalContract)
    LocalGeometryFingerprint = BuildStableFingerprint(LocalGeometry)
    LocalClaimsFingerprint = BuildStableFingerprint(LocalClaims)
    EffectiveTechnologyFingerprint = (
        str(TechnologyFingerprint)
        or BuildStableFingerprint(repr(getattr(ResourceGraph, "Technology", None)))
    )
    ResourceGraphFingerprint = BuildStableFingerprint((
        getattr(ResourceGraph, "GraphVersion", ""),
        len(getattr(ResourceGraph, "Nodes", ())),
        len(getattr(ResourceGraph, "Edges", ())),
    ))
    LocalIdentityFingerprint = BuildStableFingerprint((
        "physical-signal-local-factor-domain-v1",
        Signal,
        ComponentTopologyFingerprint,
        TerminalContractFingerprint,
        LocalGeometryFingerprint,
        LocalClaimsFingerprint,
        CertifiedLocalWitnesses,
        EffectiveTechnologyFingerprint,
        ResourceGraphFingerprint,
    ))
    return PreparedPhysicalSignalLocalFactorDomain(
        Signal=Signal,
        LocalIdentityFingerprint=LocalIdentityFingerprint,
        ComponentTopologyFingerprint=ComponentTopologyFingerprint,
        TerminalContractFingerprint=TerminalContractFingerprint,
        LocalGeometryFingerprint=LocalGeometryFingerprint,
        LocalClaimsFingerprint=LocalClaimsFingerprint,
        TechnologyFingerprint=EffectiveTechnologyFingerprint,
        Complete=bool(AccessCertificate.Complete),
        Feasible=bool(Factors),
        LocalAccessFactors=Factors,
        LocalSupportFacts=tuple(
            str(Factor.SeamContractFingerprint) for Factor in Factors
        ),
    )

def CertifyPhysicalPortExteriorFixedClaims(
    Problem: ComponentRoutingProblem,
    Profiles: Mapping[str, Any],
    ApertureFactorsBySignal: Mapping[
        str, tuple[PhysicalPortApertureOptionFactor, ...]
    ],
    ResourceGraph: Any,
    FrozenComponentClaims: Iterable[LocalRouteClaim],
    *,
    TechnologyFingerprint: str,
    ResourceGraphIdentityFingerprint: str = "",
) -> tuple[PhysicalPortExteriorFixedClaimCertificate, ...]:
    """Certify claims inherited by every route through each aperture."""
    FrozenComponentClaims = tuple(FrozenComponentClaims)
    ResourceGraphFingerprint = (
        ResourceGraphIdentityFingerprint
        or BuildStableFingerprint((
            getattr(ResourceGraph, "GraphVersion", ""),
            len(getattr(ResourceGraph, "Nodes", ())),
            len(getattr(ResourceGraph, "Edges", ())),
        ))
    )
    FrozenClaimsFingerprint = BuildStableFingerprint(tuple(sorted(
        (
            str(Claim.Signal),
            int(getattr(Claim, "ClusterId", 0)),
            tuple(sorted(map(str, Claim.Claims.ResourceIds))),
        )
        for Claim in FrozenComponentClaims
    )))
    OwnedTerminalsBySignal: dict[str, frozenset[Position3]] = {}
    for Domain in Problem.OwnedTerminalDomains:
        OwnedTerminalsBySignal[Domain.Signal] = frozenset((
            *OwnedTerminalsBySignal.get(Domain.Signal, frozenset()),
            Domain.Terminal,
        ))
    InterfaceFingerprint = str(getattr(
        Problem.Interface,
        "InterfaceFingerprint",
        "",
    ))
    Certificates = []
    for Signal, Factors in sorted(ApertureFactorsBySignal.items()):
        Profile = Profiles.get(Signal)
        Covered = OwnedTerminalsBySignal.get(Signal, frozenset())
        for Factor in sorted(
            Factors,
            key=lambda Value: Value.ApertureOptionFingerprint,
        ):
            Complete = bool(Profile is not None and InterfaceFingerprint)
            FixedNodes: frozenset[Position3] = frozenset()
            if Complete:
                OutsideTargets = tuple(
                    Target
                    for Target in Profile.Targets
                    if Target not in Covered
                )
                if Profile.Root in Covered:
                    AccessPaths = (
                        tuple(Factor.GlobalPath),
                        *(
                            tuple(Profile.TargetAccessPaths[Target])
                            for Target in OutsideTargets
                        ),
                    )
                else:
                    AccessPaths = (
                        tuple(Profile.SourceAccessPath),
                        *(
                            tuple(Profile.TargetAccessPaths[Target])
                            for Target in OutsideTargets
                        ),
                        tuple(Factor.GlobalPath),
                    )
                FixedNodes = frozenset(
                    Position
                    for Path in AccessPaths
                    for Position in Path
                )
            FixedClaims = ResourceGraph.BuildRouteClaims(FixedNodes)
            SelfConflicts = (
                tuple(sorted(map(
                    str,
                    FindSelfClaimConflicts({Signal: FixedClaims}),
                )))
                if Complete
                else ()
            )
            FrozenBlockers = (
                PortalTupleConflictsWithFrozenComponentClaims(
                    Signal,
                    FixedClaims,
                    FrozenComponentClaims,
                )
                if Complete
                else ()
            )
            FixedClaimsFingerprint = BuildStableFingerprint((
                tuple(sorted(FixedNodes)),
                tuple(sorted(map(str, FixedClaims.ResourceIds))),
            ))
            Feasible = bool(
                not Complete or (not SelfConflicts and not FrozenBlockers)
            )
            CertificateFingerprint = BuildStableFingerprint((
                "physical-port-exterior-fixed-claim-certificate-v1",
                Problem.PlacementFingerprint,
                InterfaceFingerprint,
                ResourceGraphFingerprint,
                TechnologyFingerprint,
                Signal,
                Factor.ApertureOptionFingerprint,
                Factor.ApertureContractFingerprint,
                FixedClaimsFingerprint,
                FrozenClaimsFingerprint,
                Complete,
                SelfConflicts,
                FrozenBlockers,
            ))
            Certificates.append(
                PhysicalPortExteriorFixedClaimCertificate(
                    CertificateFingerprint=CertificateFingerprint,
                    Signal=Signal,
                    ApertureOptionFingerprint=(
                        Factor.ApertureOptionFingerprint
                    ),
                    ApertureContractFingerprint=(
                        Factor.ApertureContractFingerprint
                    ),
                    PlacementFingerprint=Problem.PlacementFingerprint,
                    InterfaceFingerprint=InterfaceFingerprint,
                    ResourceGraphFingerprint=ResourceGraphFingerprint,
                    TechnologyFingerprint=TechnologyFingerprint,
                    FixedClaimsFingerprint=FixedClaimsFingerprint,
                    FrozenClaimsFingerprint=FrozenClaimsFingerprint,
                    Complete=Complete,
                    Feasible=Feasible,
                    SelfConflictResources=SelfConflicts,
                    FrozenConflictSignals=FrozenBlockers,
                )
            )
    return tuple(Certificates)

def BuildPhysicalExteriorResourceGraphFingerprint(
    ResourceGraph: Any,
    RegionFingerprint: str,
    Region: Any | None,
) -> str:
    """Build the one region-bound resource identity used across handoff."""
    return BuildStableFingerprint((
        getattr(ResourceGraph, "GraphVersion", ""),
        str(RegionFingerprint) if Region is not None else "",
        tuple(getattr(Region, "Bounds", ())) if Region is not None else (),
        len(getattr(Region, "Nodes", ())) if Region is not None else 0,
        len(getattr(Region, "Edges", ())) if Region is not None else 0,
    ))

def MaterializeSupportedPhysicalPortReservation(
    LocalFactor: PhysicalPortLocalAccessFactor,
    ApertureFactor: PhysicalPortApertureOptionFactor,
    Support: PhysicalPortLocalApertureSupport,
    ResourceGraph: Any,
) -> PhysicalComponentPortReservation:
    """Materialize only an explicitly certified local/aperture support edge."""
    if (
        LocalFactor.Signal != ApertureFactor.Signal
        or Support.Signal != LocalFactor.Signal
        or Support.LocalAccessFingerprint
        != LocalFactor.LocalAccessFingerprint
        or Support.ApertureOptionFingerprint
        != ApertureFactor.ApertureOptionFingerprint
        or LocalFactor.Direction != ApertureFactor.Direction
        or LocalFactor.Capacity != ApertureFactor.Capacity
    ):
        raise ValueError("unsupported physical local/aperture factor pair")
    # Claims are a function of the complete physical node set, not an
    # additive property of independently compiled path fragments.  In
    # particular, BuildRouteClaims can discover technology primitives between
    # spatially adjacent local/global cells that neither fragment contains by
    # itself.  Recompile the joined seam so the immutable contract exactly
    # describes what will be frozen into the resource graph.
    Claims = ResourceGraph.BuildRouteClaims(frozenset((
        *LocalFactor.LocalPath,
        *ApertureFactor.GlobalPath,
    )))
    Port = PhysicalComponentPortReservation(
        Signal=LocalFactor.Signal,
        Direction=LocalFactor.Direction,
        OwnedTerminals=LocalFactor.OwnedTerminals,
        OwnedTerminalFingerprints=(
            LocalFactor.OwnedTerminalFingerprints
        ),
        OwnedCandidateFingerprints=(
            LocalFactor.OwnedCandidateFingerprints
        ),
        FabricDomainFingerprint=(
            LocalFactor.FabricDomainFingerprint
        ),
        FabricAttachment=LocalFactor.FabricAttachment,
        Attachment=ApertureFactor.Attachment,
        LocalPath=LocalFactor.LocalPath,
        GlobalPath=ApertureFactor.GlobalPath,
        Claims=Claims,
        LocalClaims=LocalFactor.LocalClaims,
        GlobalClaims=ApertureFactor.GlobalClaims,
        OwnedAccessCandidates=LocalFactor.OwnedAccessCandidates,
        Capacity=LocalFactor.Capacity,
        ReservationFingerprint=Support.ReservationFingerprint,
    )
    if (
        BuildPhysicalPortLocalContractFingerprint(Port)
        != LocalFactor.LocalContractFingerprint
        or BuildPhysicalPortGlobalContractFingerprint(Port)
        != ApertureFactor.GlobalContractFingerprint
        or BuildPhysicalPortApertureContractFingerprint(Port)
        != ApertureFactor.ApertureContractFingerprint
    ):
        raise ValueError("physical factor materialization identity mismatch")
    return Port

def MaterializePhysicalPortFactorPair(
    LocalFactor: PhysicalPortLocalAccessFactor,
    ApertureFactor: PhysicalPortApertureOptionFactor,
    Supports: Iterable[PhysicalPortLocalApertureSupport],
    ResourceGraph: Any,
) -> PhysicalComponentPortReservation:
    """Resolve one pair through the explicit support relation or reject it."""
    Support = next((
        Value
        for Value in Supports
        if Value.Signal == LocalFactor.Signal
        and Value.LocalAccessFingerprint
        == LocalFactor.LocalAccessFingerprint
        and Value.ApertureOptionFingerprint
        == ApertureFactor.ApertureOptionFingerprint
    ), None)
    if Support is None:
        raise ValueError("unsupported physical local/aperture factor pair")
    return MaterializeSupportedPhysicalPortReservation(
        LocalFactor,
        ApertureFactor,
        Support,
        ResourceGraph,
    )

def BuildPhysicalComponentBoundaryPortReservation(
    ApertureFactor: PhysicalPortApertureOptionFactor,
) -> PhysicalComponentBoundaryPortReservation:
    """Publish one global-only aperture contract before local compilation."""
    Attachment = tuple(ApertureFactor.Attachment)
    RelativeGlobalPath = tuple(
        tuple(
            int(Position[Index]) - int(Attachment[Index])
            for Index in range(3)
        )
        for Position in ApertureFactor.GlobalPath
    )
    ReservationFingerprint = BuildStableFingerprint((
        "physical-component-boundary-port-reservation-v1",
        ApertureFactor.Direction,
        RelativeGlobalPath,
        int(ApertureFactor.Capacity),
    ))
    return PhysicalComponentBoundaryPortReservation(
        Signal=ApertureFactor.Signal,
        Direction=ApertureFactor.Direction,
        Attachment=Attachment,
        GlobalPath=tuple(ApertureFactor.GlobalPath),
        GlobalClaims=ApertureFactor.GlobalClaims,
        Capacity=ApertureFactor.Capacity,
        ChannelContractFingerprint=(
            ApertureFactor.ChannelContractFingerprint
        ),
        GlobalContractFingerprint=(
            ApertureFactor.GlobalContractFingerprint
        ),
        ApertureContractFingerprint=(
            ApertureFactor.ApertureContractFingerprint
        ),
        ReservationFingerprint=ReservationFingerprint,
    )

def BuildPhysicalExteriorApertureFabric(
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    CompleteCoarseGuideCellsBySignal: Mapping[
        str, Iterable[Position2]
    ],
    DeclaredPortalIngressNodesBySignal: Mapping[
        str, Iterable[Position3]
    ],
    *,
    Technology: object,
    MinimumPlacementY: int,
    Layer: int,
    KeepoutColumns: Iterable[Position2] = (),
    KeepoutNodes: Iterable[Position3] = (),
    RegionNodes: Iterable[Position3] | None = None,
    RegionEdges: Iterable[tuple[Position3, Position3]] | None = None,
    RegionFingerprint: str = "",
    ResourceGraphFingerprint: str = "",
    Complete: bool | None = None,
) -> PhysicalExteriorApertureFabric:
    """Freeze one edge-exact exterior slice of the authoritative Region.

    An explicit Region supplies every allowed exterior node and edge; guide
    and ingress geometry binds targets without narrowing that complete graph.
    Geometry-only callers receive an incomplete ring/guide fixture.  Component
    envelope nodes remain locally owned and explicit keepouts always win.
    """
    EnvelopeMinimum = tuple(map(int, EnvelopeMinimum))
    EnvelopeMaximum = tuple(map(int, EnvelopeMaximum))
    if any(
        EnvelopeMinimum[Index] > EnvelopeMaximum[Index]
        for Index in range(3)
    ):
        raise ValueError("component envelope minimum exceeds maximum")
    Layer = int(Layer)
    RoutingY = int(Technology.RoutingY(
        int(MinimumPlacementY),
        Layer,
    ))

    Signals = frozenset((
        *CompleteCoarseGuideCellsBySignal.keys(),
        *DeclaredPortalIngressNodesBySignal.keys(),
    ))
    SignalBindings = []
    for Signal in sorted(Signals):
        GuideColumns = tuple(sorted({
            (int(Column[0]), int(Column[1]))
            for Column in CompleteCoarseGuideCellsBySignal.get(Signal, ())
        }))
        IngressNodes = tuple(sorted({
            tuple(map(int, Node))
            for Node in DeclaredPortalIngressNodesBySignal.get(Signal, ())
        }))
        SignalBindings.append((str(Signal), GuideColumns, IngressNodes))
    CanonicalSignalBindings = tuple(SignalBindings)
    CanonicalSignalGeometry = tuple(sorted(
        (GuideColumns, IngressNodes)
        for _Signal, GuideColumns, IngressNodes
        in CanonicalSignalBindings
    ))
    DeclaredIngressNodes = frozenset(
        Node
        for _GuideColumns, IngressNodes in CanonicalSignalGeometry
        for Node in IngressNodes
    )
    MinimumX, MaximumX = EnvelopeMinimum[0], EnvelopeMaximum[0]
    MinimumZ, MaximumZ = EnvelopeMinimum[2], EnvelopeMaximum[2]
    ExteriorPerimeterColumns = frozenset((
        *((X, MinimumZ - 1) for X in range(MinimumX - 1, MaximumX + 2)),
        *((X, MaximumZ + 1) for X in range(MinimumX - 1, MaximumX + 2)),
        *((MinimumX - 1, Z) for Z in range(MinimumZ, MaximumZ + 1)),
        *((MaximumX + 1, Z) for Z in range(MinimumZ, MaximumZ + 1)),
    ))
    InvalidIngressNodes = tuple(sorted(
        Node
        for Node in DeclaredIngressNodes
        if (
            Node[1] != RoutingY
            or (
                MinimumX <= Node[0] <= MaximumX
                and MinimumZ <= Node[2] <= MaximumZ
            )
        )
    ))
    if InvalidIngressNodes:
        raise ValueError(
            "declared portal ingress must lie outside the closed envelope "
            f"at routing Y {RoutingY}: {InvalidIngressNodes}"
        )

    StableKeepoutNodes = frozenset(
        tuple(map(int, Node)) for Node in KeepoutNodes
    )
    StableKeepoutColumns = frozenset((
        *((int(Column[0]), int(Column[1])) for Column in KeepoutColumns),
        *(
            (Node[0], Node[2])
            for Node in StableKeepoutNodes
            if Node[1] == RoutingY
        ),
    ))
    ConflictingIngressNodes = tuple(sorted(
        Node
        for Node in DeclaredIngressNodes
        if (
            Node in StableKeepoutNodes
            or (Node[0], Node[2]) in StableKeepoutColumns
        )
    ))
    if ConflictingIngressNodes:
        raise ValueError(
            "declared portal ingress intersects an immutable keepout: "
            f"{ConflictingIngressNodes}"
        )

    def IsInsideEnvelope(Column: Position2) -> bool:
        return bool(
            MinimumX <= Column[0] <= MaximumX
            and MinimumZ <= Column[1] <= MaximumZ
        )

    CompleteGuideColumns = frozenset(
        Column
        for GuideColumns, _IngressNodes in CanonicalSignalGeometry
        for Column in GuideColumns
    )
    ExteriorOwnedColumns = frozenset(
        Column
        for Column in (*ExteriorPerimeterColumns, *CompleteGuideColumns)
        if not IsInsideEnvelope(Column)
        and Column not in StableKeepoutColumns
    )
    ExplicitRegion = RegionNodes is not None and RegionEdges is not None
    if (RegionNodes is None) != (RegionEdges is None):
        raise ValueError(
            "exterior fabric requires region nodes and edges together"
        )
    if ExplicitRegion:
        StableRegionNodes = frozenset(
            tuple(map(int, Node)) for Node in RegionNodes or ()
        )
        StableRegionEdges = frozenset(
            tuple(sorted((
                tuple(map(int, First)),
                tuple(map(int, Second)),
            )))
            for First, Second in RegionEdges or ()
        )
        # The complete routing Region is authoritative.  Guide and ingress
        # geometry bind targets and identities; they must not narrow away an
        # intermediate exterior node needed to connect them.
        AllowedNodes = frozenset(
            Node
            for Node in StableRegionNodes
            if Node[1] == RoutingY
            and not IsInsideEnvelope((Node[0], Node[2]))
            and Node not in StableKeepoutNodes
            and (Node[0], Node[2]) not in StableKeepoutColumns
        )
        MissingIngressNodes = tuple(sorted(
            DeclaredIngressNodes - AllowedNodes
        ))
        if MissingIngressNodes:
            raise ValueError(
                "explicit exterior region omits declared ingress nodes: "
                f"{MissingIngressNodes}"
            )
        AllowedEdges = frozenset(
            (First, Second)
            for First, Second in StableRegionEdges
            if First in AllowedNodes and Second in AllowedNodes
        )
        if not RegionFingerprint:
            RegionFingerprint = BuildStableFingerprint((
                "physical-exterior-source-region-v1",
                tuple(sorted(StableRegionNodes)),
                tuple(sorted(StableRegionEdges)),
            ))
    else:
        # Compatibility construction for geometry-only fixtures.  It is
        # deliberately incomplete and may not support an authoritative UNSAT
        # result until a concrete Region is supplied.
        AllowedNodes = frozenset((
            *((X, RoutingY, Z) for X, Z in ExteriorOwnedColumns),
            *DeclaredIngressNodes,
        ))
        AllowedEdges = frozenset(
            tuple(sorted((First, Second)))
            for First in AllowedNodes
            for Second in (
                (First[0] + 1, First[1], First[2]),
                (First[0], First[1], First[2] + 1),
            )
            if Second in AllowedNodes
        )
        if not RegionFingerprint:
            RegionFingerprint = BuildStableFingerprint((
                "physical-exterior-implicit-fixture-region-v1",
                tuple(sorted(AllowedNodes)),
                tuple(sorted(AllowedEdges)),
            ))
    AllowedColumns = frozenset(
        (Node[0], Node[2]) for Node in AllowedNodes
    )
    AdjacencyByNode: dict[Position3, set[Position3]] = {
        Node: set() for Node in AllowedNodes
    }
    for First, Second in AllowedEdges:
        AdjacencyByNode[First].add(Second)
        AdjacencyByNode[Second].add(First)
    Adjacency = tuple(
        (Node, tuple(sorted(Neighbors)))
        for Node, Neighbors in sorted(AdjacencyByNode.items())
    )

    TechnologyFingerprint = BuildStableFingerprint((
        "physical-exterior-aperture-technology-v1",
        str(getattr(Technology, "TechnologyVersion", "")),
        int(getattr(Technology, "RoutingLayerPitch", 0)),
        int(getattr(Technology, "TrackPitch", 0)),
        int(getattr(Technology, "AccessLength", 0)),
    ))
    GuideIdentityFingerprint = BuildStableFingerprint((
        "physical-exterior-aperture-guide-identity-v1",
        Layer,
        tuple(sorted(
            GuideColumns
            for _Signal, GuideColumns, _IngressNodes
            in CanonicalSignalBindings
        )),
    ))
    SignalBindingFingerprint = BuildStableFingerprint((
        "physical-exterior-aperture-signal-binding-v1",
        Layer,
        CanonicalSignalBindings,
    ))
    IsComplete = bool(
        Complete
        if Complete is not None
        else ExplicitRegion and ResourceGraphFingerprint
    )
    if IsComplete and not ExplicitRegion:
        raise ValueError(
            "complete exterior fabric requires an explicit routing region"
        )
    if IsComplete and not ResourceGraphFingerprint:
        raise ValueError(
            "complete exterior fabric requires a resource graph identity"
        )
    FabricFingerprint = BuildStableFingerprint((
        "physical-exterior-aperture-fabric-v2",
        EnvelopeMinimum,
        EnvelopeMaximum,
        Layer,
        RoutingY,
        CanonicalSignalGeometry,
        tuple(sorted(ExteriorPerimeterColumns)),
        tuple(sorted(StableKeepoutColumns)),
        tuple(sorted(StableKeepoutNodes)),
        tuple(sorted(AllowedColumns)),
        tuple(sorted(AllowedNodes)),
        tuple(sorted(AllowedEdges)),
        bool(IsComplete),
        str(RegionFingerprint),
        str(ResourceGraphFingerprint),
        TechnologyFingerprint,
        GuideIdentityFingerprint,
    ))
    return PhysicalExteriorApertureFabric(
        EnvelopeMinimum=EnvelopeMinimum,
        EnvelopeMaximum=EnvelopeMaximum,
        Layer=Layer,
        RoutingY=RoutingY,
        ExteriorPerimeterColumns=ExteriorPerimeterColumns,
        SignalGuideIngressGeometry=CanonicalSignalGeometry,
        SignalGuideIngressBindings=CanonicalSignalBindings,
        DeclaredPortalIngressNodes=DeclaredIngressNodes,
        KeepoutColumns=StableKeepoutColumns,
        KeepoutNodes=StableKeepoutNodes,
        AllowedColumns=AllowedColumns,
        AllowedNodes=AllowedNodes,
        AllowedEdges=AllowedEdges,
        Adjacency=Adjacency,
        Complete=IsComplete,
        RegionFingerprint=str(RegionFingerprint),
        ResourceGraphFingerprint=str(ResourceGraphFingerprint),
        TechnologyFingerprint=TechnologyFingerprint,
        GuideIdentityFingerprint=GuideIdentityFingerprint,
        SignalBindingFingerprint=SignalBindingFingerprint,
        FabricFingerprint=FabricFingerprint,
    )

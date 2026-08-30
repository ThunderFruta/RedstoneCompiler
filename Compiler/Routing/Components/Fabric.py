"""Component fabric, terminal access, problem construction, and feedthrough domains."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import combinations, islice, product
from math import prod as ProductIntegers
from time import monotonic
from typing import Any, Callable, Iterable, Mapping


from ..Contracts.Component import (
    ClosedComponentInterface,
    ComponentFeedthroughContract,
    ComponentForeignTransitDomain,
    ComponentInterfacePort,
    ComponentRoutingFabric,
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from ..Contracts.Core import Position2, Position3
from ..Interfaces.PhysicalClaims import (
    _MergeClaims,
    ComponentClaimsCompatibleForOwners,
    ComponentClaimsConflict,
)
from ..ResourceGraph import (
    FindSelfClaimConflicts,
    LocalRouteClaim,
    PinAccessPortal,
    RoutingEdge,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
    RoutingResourceClaims,
)
from ..Technology import (
    DefaultRedstoneRoutingTechnology,
    RepeaterInputFacingForStep,
)

try:
    from ...RustRouting import (
        BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
    )
    from ...RustRouting import BuildRouteClaimsBatch as _BuildRouteClaimsBatch
    from ...RustRouting import (
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
    )
    from ...RustRouting import GetRoutingThreadCount as _GetRoutingThreadCount
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import (
            BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
        )
        from RedstoneCompiler.RustRouting import (
            BuildRouteClaimsBatch as _BuildRouteClaimsBatch,
        )
        from RedstoneCompiler.RustRouting import (
            BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
        )
        from RedstoneCompiler.RustRouting import (
            GetRoutingThreadCount as _GetRoutingThreadCount,
        )
    except ImportError:
        _BuildFabricSubtreesBatchWithTelemetry = None
        _BuildRouteClaimsBatch = None
        _BuildRouteClaimsBatchWithTelemetry = None
        _GetRoutingThreadCount = None

from .Core import (
    _ClaimsFingerprint,
    _NormalizedEdge,
    _RelativeGeometry,
    _StableFingerprint,
)
def SelectComponentIncidentSignals(
    BoundaryRequests: Iterable[Any],
    SelectedClusters: Iterable[int],
    ProfileSignals: Iterable[str],
) -> frozenset[str]:
    """Select every routed net with a terminal owned by the component."""
    SelectedClusterSet = frozenset(
        int(Value) for Value in SelectedClusters
    )
    Profiles = frozenset(str(Value) for Value in ProfileSignals)
    return frozenset(
        str(Request.Signal)
        for Request in BoundaryRequests
        if (
            str(Request.Signal) in Profiles
            and (
                int(Request.SourceCluster) in SelectedClusterSet
                or int(Request.TargetCluster) in SelectedClusterSet
            )
        )
    )


def ApplyRoutedComponentGlobalProfiles(
    Placed: Any,
    Profiles: dict[str, Any],
) -> dict[str, Any]:
    """Replace covered component terminals with exported global ports."""
    Result = dict(Profiles)
    for Template in (
        getattr(Placed, "RoutedComponentTemplates", ()) or ()
    ):
        for Net in Template.Nets:
            Profile = Result.get(Net.Signal)
            if Profile is None:
                continue
            Covered = frozenset(Net.CoveredTerminals)
            ExportedPorts = tuple(sorted(Net.ExportedPorts))
            if not ExportedPorts:
                if (
                    Profile.Root in Covered
                    and all(
                        Target in Covered
                        for Target in Profile.Targets
                    )
                ):
                    Result.pop(Net.Signal, None)
                continue
            ExportedPort = ExportedPorts[0]
            RootIsCovered = Profile.Root in Covered
            OutsideTargets = tuple(
                Target
                for Target in Profile.Targets
                if Target not in Covered
            )
            if RootIsCovered:
                if not OutsideTargets:
                    Result.pop(Net.Signal, None)
                    continue
                Root = ExportedPort
                Targets = OutsideTargets
                SourceAccessPath = (ExportedPort,)
                TargetAccessPaths = {
                    Target: Profile.TargetAccessPaths[Target]
                    for Target in Targets
                }
            else:
                Root = Profile.Root
                Targets = tuple(dict.fromkeys((
                    *OutsideTargets,
                    ExportedPort,
                )))
                SourceAccessPath = Profile.SourceAccessPath
                TargetAccessPaths = {
                    **{
                        Target: Profile.TargetAccessPaths[Target]
                        for Target in OutsideTargets
                    },
                    ExportedPort: (ExportedPort,),
                }
            Result[Net.Signal] = replace(
                Profile,
                Root=Root,
                Targets=Targets,
                Span=max(
                    (
                        abs(Target[0] - Root[0])
                        + abs(Target[2] - Root[2])
                    )
                    for Target in Targets
                ),
                Fanout=len(Targets),
                SourceAccessPath=SourceAccessPath,
                TargetAccessPaths=TargetAccessPaths,
            )
    # Passive source witnesses retire the original producer portal exactly as
    # passive target witnesses retire their covered target.  Export their
    # boundary node as the global root so cached portal domains and the
    # transformed profile describe the same terminal set.
    for Claim in (
        getattr(Placed, "LocalRouteClaims", ()) or ()
    ):
        if int(getattr(Claim, "ClusterId", -1)) != -2:
            continue
        Profile = Result.get(Claim.Signal)
        if (
            Profile is None
            or Profile.Root != Claim.Root
        ):
            if (
                Profile is None
                or Claim.Root not in Profile.Targets
                or not Claim.BoundaryNodes
            ):
                continue
            Boundary = min(Claim.BoundaryNodes)
            Targets = tuple(dict.fromkeys(
                Boundary if Target == Claim.Root else Target
                for Target in Profile.Targets
            ))
            TargetAccessPaths = {
                (
                    Boundary if Target == Claim.Root else Target
                ): (
                    (Boundary,)
                    if Target == Claim.Root
                    else Profile.TargetAccessPaths[Target]
                )
                for Target in Profile.Targets
            }
            Result[Claim.Signal] = replace(
                Profile,
                Targets=Targets,
                Span=max(
                    (
                        abs(Target[0] - Profile.Root[0])
                        + abs(Target[2] - Profile.Root[2])
                    )
                    for Target in Targets
                ),
                Fanout=len(Targets),
                TargetAccessPaths=TargetAccessPaths,
            )
            continue
        if not Claim.BoundaryNodes:
            continue
        Root = min(Claim.BoundaryNodes)
        Result[Claim.Signal] = replace(
            Profile,
            Root=Root,
            Span=max(
                (
                    abs(Target[0] - Root[0])
                    + abs(Target[2] - Root[2])
                )
                for Target in Profile.Targets
            ),
            SourceAccessPath=(Root,),
        )
    return Result


def BuildComponentEgressPaths(
    Attachment: Position3,
    TargetY: int | None = None,
    *,
    EnvelopeMinimum: Position3 | None = None,
    EnvelopeMaximum: Position3 | None = None,
    Directions: Iterable[Position2] | None = None,
) -> tuple[tuple[Position3, ...], ...]:
    """Enumerate straight component-to-global-layer perimeter contracts."""
    if (EnvelopeMinimum is None) != (EnvelopeMaximum is None):
        raise ValueError(
            "component egress requires both envelope bounds or neither"
        )
    EffectiveTargetY = (
        Attachment[1]
        - DefaultRedstoneRoutingTechnology.RoutingLayerPitch
        if TargetY is None
        else int(TargetY)
    )
    VerticalDirection = (
        1 if EffectiveTargetY > Attachment[1] else -1
    )
    VerticalDistance = abs(EffectiveTargetY - Attachment[1])

    def RequiredHorizontalDistance(DeltaX: int, DeltaZ: int) -> int:
        BaseDistance = (
            VerticalDistance
            + DefaultRedstoneRoutingTechnology.TrackPitch
        )
        if EnvelopeMinimum is None or EnvelopeMaximum is None:
            return BaseDistance
        if DeltaX < 0:
            PerimeterDistance = Attachment[0] - EnvelopeMinimum[0] + 1
        elif DeltaX > 0:
            PerimeterDistance = EnvelopeMaximum[0] - Attachment[0] + 1
        elif DeltaZ < 0:
            PerimeterDistance = Attachment[2] - EnvelopeMinimum[2] + 1
        else:
            PerimeterDistance = EnvelopeMaximum[2] - Attachment[2] + 1
        return max(BaseDistance, PerimeterDistance)

    CardinalDirections = (
        (-1, 0),
        (0, -1),
        (0, 1),
        (1, 0),
    )
    EffectiveDirections = (
        CardinalDirections
        if Directions is None
        else tuple(dict.fromkeys(tuple(Direction) for Direction in Directions))
    )
    if any(Direction not in CardinalDirections for Direction in EffectiveDirections):
        raise ValueError("component egress directions must be cardinal")
    return tuple(
        (
            Attachment,
            *(
                (
                    Attachment[0] + Distance * DeltaX,
                    Attachment[1]
                    + VerticalDirection
                    * min(Distance, VerticalDistance),
                    Attachment[2] + Distance * DeltaZ,
                )
                for Distance in range(
                    1,
                    RequiredHorizontalDistance(DeltaX, DeltaZ) + 1,
                )
            ),
        )
        for DeltaX, DeltaZ in EffectiveDirections
    )


def SelectGuideFacingComponentEgressDirections(
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    GuideCells: Iterable[Position2],
) -> tuple[Position2, ...]:
    """Return the perimeter sides selected by exterior coarse-guide cells."""
    Selected: set[Position2] = set()
    for X, Z in GuideCells:
        if X < EnvelopeMinimum[0]:
            Selected.add((-1, 0))
        if X > EnvelopeMaximum[0]:
            Selected.add((1, 0))
        if Z < EnvelopeMinimum[2]:
            Selected.add((0, -1))
        if Z > EnvelopeMaximum[2]:
            Selected.add((0, 1))
    return tuple(
        Direction
        for Direction in ((-1, 0), (0, -1), (0, 1), (1, 0))
        if Direction in Selected
    )


def BuildComponentRoutingFabric(
    Channel: Any,
) -> ComponentRoutingFabric:
    """Validate the first backend as an acyclic tree/forest fabric."""
    Nodes = frozenset(
        tuple(Cell)
        for Lane in getattr(Channel, "Lanes", ())
        for Cell in Lane.Cells
    )
    Edges = frozenset(
        _NormalizedEdge(tuple(First), tuple(Second))
        for Lane in getattr(Channel, "Lanes", ())
        for First, Second in zip(Lane.Cells, Lane.Cells[1:])
        if sum(abs(First[Index] - Second[Index]) for Index in range(3)) == 1
    )
    IngressNodes = tuple(sorted({
        tuple(Value)
        for Lane in getattr(Channel, "Lanes", ())
        for Value in Lane.IngressNodes
        if tuple(Value) in Nodes
    }))
    Adjacency: dict[Position3, set[Position3]] = {
        Node: set() for Node in Nodes
    }
    for First, Second in Edges:
        Adjacency[First].add(Second)
        Adjacency[Second].add(First)
    ComponentCount = 0
    Visited: set[Position3] = set()
    for Start in sorted(Nodes):
        if Start in Visited:
            continue
        ComponentCount += 1
        Pending = [Start]
        Visited.add(Start)
        while Pending:
            Current = Pending.pop()
            for Neighbor in Adjacency[Current]:
                if Neighbor not in Visited:
                    Visited.add(Neighbor)
                    Pending.append(Neighbor)
    HasCycle = bool(Edges) and len(Edges) != len(Nodes) - ComponentCount
    Complete = bool(Nodes and IngressNodes and not HasCycle)
    IncompleteReason = (
        "empty-fabric"
        if not Nodes
        else "no-ingress"
        if not IngressNodes
        else "unsupported-cyclic-fabric"
        if HasCycle
        else ""
    )
    StructuralNodes = _RelativeGeometry(Nodes)
    Origin = (
        min(Value[0] for Value in Nodes),
        min(Value[1] for Value in Nodes),
        min(Value[2] for Value in Nodes),
    ) if Nodes else (0, 0, 0)
    StructuralEdges = tuple(sorted(
        (
            (
                First[0] - Origin[0],
                First[1] - Origin[1],
                First[2] - Origin[2],
            ),
            (
                Second[0] - Origin[0],
                Second[1] - Origin[1],
                Second[2] - Origin[2],
            ),
        )
        for First, Second in Edges
    ))
    return ComponentRoutingFabric(
        FabricFingerprint=_StableFingerprint((
            getattr(Channel, "PhysicalModel", ""),
            StructuralNodes,
            StructuralEdges,
            len(IngressNodes),
        )),
        Nodes=tuple(sorted(Nodes)),
        Edges=tuple(sorted(Edges)),
        IngressNodes=IngressNodes,
        TopologyKind=(
            "tree" if ComponentCount == 1 else "tree-forest"
        ),
        Complete=Complete,
        IncompleteReason=IncompleteReason,
    )


def AugmentComponentRoutingFabric(
    Fabric: ComponentRoutingFabric,
    Attachments: Iterable[Position3],
    ResourceGraph: Any,
    ProtectedAccessNodes: frozenset[Position3] = frozenset(),
) -> ComponentRoutingFabric:
    """Grow the existing lane forest to declared port domains.

    Parallel component lanes are independent capacity domains.  Joining them
    here makes every terminal theoretically reachable but creates an
    artificial shared electrical bottleneck for every routed signal.  Each
    attachment is therefore connected to exactly one existing lane component;
    physical assembly planning is responsible for choosing one common
    component for all terminals of a signal.
    """
    Requested = tuple(sorted(set(map(tuple, Attachments))))
    if ResourceGraph is None or not Requested:
        return Fabric
    Nodes = set(Fabric.Nodes)
    Edges = set(Fabric.Edges)
    ProtectedAccessClaims = (
        ResourceGraph.BuildRouteClaims(ProtectedAccessNodes)
        if ProtectedAccessNodes
        else RoutingResourceClaims()
    )
    if ProtectedAccessNodes:
        ProtectedIncompatibleNodes = {
            Node
            for Node in Nodes
            if FindSelfClaimConflicts({
                "component-access-existing-fabric": _MergeClaims((
                    ProtectedAccessClaims,
                    ResourceGraph.BuildRouteClaims((Node,)),
                )),
            })
        }
        Nodes.difference_update(ProtectedIncompatibleNodes)
        Edges = {
            Edge
            for Edge in Edges
            if Edge[0] in Nodes and Edge[1] in Nodes
        }
    Anchors = tuple((*Nodes, *Requested))
    MinimumX = min(Value[0] for Value in Anchors) - 2
    MaximumX = max(Value[0] for Value in Anchors) + 2
    MinimumY = min(Value[1] for Value in Anchors) - 1
    MaximumY = max(Value[1] for Value in Anchors) + 1
    MinimumZ = min(Value[2] for Value in Anchors) - 2
    MaximumZ = max(Value[2] for Value in Anchors) + 2
    Missing = []
    for Attachment in Requested:
        if Attachment in Nodes:
            continue
        Pending = deque((Attachment,))
        Previous: dict[Position3, Position3 | None] = {
            Attachment: None
        }
        Reached: Position3 | None = None
        while Pending and Reached is None:
            Current = Pending.popleft()
            for Neighbor in sorted(
                ResourceGraph.Technology.NeighborPositions(Current)
            ):
                if (
                    Neighbor in Previous
                    or not (
                        MinimumX <= Neighbor[0] <= MaximumX
                        and MinimumY <= Neighbor[1] <= MaximumY
                        and MinimumZ <= Neighbor[2] <= MaximumZ
                    )
                    or ResourceGraph.BuildPrimitive(
                        Current,
                        Neighbor,
                    )
                    is None
                ):
                    continue
                if ProtectedAccessNodes:
                    Prefix = [Neighbor, Current]
                    while Prefix[-1] != Attachment:
                        Parent = Previous[Prefix[-1]]
                        assert Parent is not None
                        Prefix.append(Parent)
                    PrefixClaims = ResourceGraph.BuildRouteClaims(
                        frozenset(Prefix)
                    )
                    if FindSelfClaimConflicts({
                        "component-access-augmentation": _MergeClaims((
                            ProtectedAccessClaims,
                            PrefixClaims,
                        )),
                    }):
                        continue
                Previous[Neighbor] = Current
                if Neighbor in Nodes:
                    Reached = Neighbor
                    break
                Pending.append(Neighbor)
        if Reached is None:
            Missing.append(Attachment)
            continue
        Path = [Reached]
        while Path[-1] != Attachment:
            Parent = Previous[Path[-1]]
            assert Parent is not None
            Path.append(Parent)
        Path.reverse()
        for First, Second in zip(Path, Path[1:]):
            Edges.add(_NormalizedEdge(First, Second))
        Nodes.update(Path)
    Complete = bool(Fabric.Complete and not Missing)
    Origin = (
        min(Value[0] for Value in Nodes),
        min(Value[1] for Value in Nodes),
        min(Value[2] for Value in Nodes),
    )
    return ComponentRoutingFabric(
        FabricFingerprint=_StableFingerprint((
            "closed-component-port-forest-v3",
            tuple(sorted(
                tuple(
                    Value[Index] - Origin[Index]
                    for Index in range(3)
                )
                for Value in Nodes
            )),
            tuple(sorted(
                (
                    tuple(
                        First[Index] - Origin[Index]
                        for Index in range(3)
                    ),
                    tuple(
                        Second[Index] - Origin[Index]
                        for Index in range(3)
                    ),
                )
                for First, Second in Edges
            )),
            tuple(sorted(
                tuple(
                    Value[Index] - Origin[Index]
                    for Index in range(3)
                )
                for Value in Requested
            )),
        )),
        Nodes=tuple(sorted(Nodes)),
        Edges=tuple(sorted(Edges)),
        IngressNodes=tuple(sorted(
            Value
            for Value in Fabric.IngressNodes
            if Value in Nodes
        )),
        TopologyKind="closed-component-port-forest-v3",
        Complete=Complete,
        IncompleteReason=(
            "unreachable-declared-port-domain"
            if Missing
            else ""
        ),
    )


def BridgeDisconnectedOwnedSignalFabric(
    Fabric: ComponentRoutingFabric,
    Domains: Iterable[ComponentTerminalAccessDomain],
    ResourceGraph: Any,
    *,
    ProtectedAccessNodes: frozenset[Position3] = frozenset(),
) -> ComponentRoutingFabric:
    """Add a legal channel bridge when one owned net spans forest islands.

    A closed component may own both terminals of a net while its generated
    channel consists of independent lane trees.  That is not a placement
    conflict: without a bridge the net has no local tree at all.  Connect the
    nearest pair of its terminal attachment islands through authoritative
    routing primitives, retaining a forest by never traversing an existing
    island before the target is reached.
    """
    if ResourceGraph is None or not Fabric.Nodes:
        return Fabric
    Nodes = set(Fabric.Nodes)
    Edges = set(Fabric.Edges)

    def Components() -> dict[Position3, int]:
        Adjacency = _BuildAdjacency(Edges)
        Result: dict[Position3, int] = {}
        for Start in sorted(Nodes):
            if Start in Result:
                continue
            Index = len(set(Result.values()))
            Pending = [Start]
            Result[Start] = Index
            while Pending:
                Current = Pending.pop()
                for Neighbor in Adjacency.get(Current, ()):
                    if Neighbor not in Result:
                        Result[Neighbor] = Index
                        Pending.append(Neighbor)
        return Result

    BySignal: dict[str, list[ComponentTerminalAccessDomain]] = defaultdict(list)
    for Domain in Domains:
        BySignal[str(Domain.Signal)].append(Domain)
    BridgedSignals: list[str] = []
    for Signal, SignalDomains in sorted(BySignal.items()):
        if len(SignalDomains) < 2:
            continue
        ComponentByNode = Components()
        AttachmentsByDomain = [
            tuple(sorted({
                Candidate.Attachment for Candidate in Domain.Candidates
                if Candidate.Attachment in ComponentByNode
            }))
            for Domain in SignalDomains
        ]
        if any(not Values for Values in AttachmentsByDomain):
            continue
        Common = set(ComponentByNode[Value] for Value in AttachmentsByDomain[0])
        for Values in AttachmentsByDomain[1:]:
            Common.intersection_update(ComponentByNode[Value] for Value in Values)
        if Common:
            continue
        Start = AttachmentsByDomain[0][0]
        Target = min(
            (
                Value
                for Values in AttachmentsByDomain[1:]
                for Value in Values
                if ComponentByNode[Value] != ComponentByNode[Start]
            ),
            key=lambda Value: (
                abs(Start[0] - Value[0]) + abs(Start[1] - Value[1])
                + abs(Start[2] - Value[2]),
                Value,
            ),
            default=None,
        )
        if Target is None:
            continue
        TargetComponent = ComponentByNode[Target]
        TargetNodes = frozenset(
            Node
            for Node, ComponentIndex in ComponentByNode.items()
            if ComponentIndex == TargetComponent
        )
        SourceComponent = ComponentByNode[Start]
        SourceNodes = frozenset(
            Node
            for Node, ComponentIndex in ComponentByNode.items()
            if ComponentIndex == SourceComponent
        )
        Minimum = tuple(min(Value[Index] for Value in (*Nodes, Start, Target)) - 2 for Index in range(3))
        Maximum = tuple(max(Value[Index] for Value in (*Nodes, Start, Target)) + 2 for Index in range(3))
        # Access domains for different signals are alternatives that the
        # exact component solver still has to select.  Treating every
        # protected candidate as one same-signal route can manufacture a
        # self-conflict before that CSP runs and prevent an otherwise legal
        # bridge.  The bridge itself only has to coexist with the access
        # candidates of the owned signal whose disconnected islands it joins.
        SignalAccessNodes = frozenset(
            Position
            for Domain in SignalDomains
            for Candidate in Domain.Candidates
            for Position in Candidate.Path
            if (
                not ProtectedAccessNodes
                or Position in ProtectedAccessNodes
            )
        )
        # The source and target fabric components are finite option spaces,
        # not wires that are all occupied together.  Folding every fabric
        # node into the bridge claim rejects legal paths whenever unrelated
        # branches would self-connect.  Exact subtree materialization later
        # selects and validates the actually energized fabric nodes.
        BaseClaims = ResourceGraph.BuildRouteClaims(
            SignalAccessNodes
        )
        if FindSelfClaimConflicts({Signal: BaseClaims}):
            continue
        Pending = deque((Start,))
        Paths: dict[Position3, tuple[Position3, ...]] = {Start: (Start,)}
        ClaimsByNode: dict[Position3, RoutingResourceClaims] = {
            Start: BaseClaims,
        }
        Reached: Position3 | None = None
        while Pending and Reached is None:
            Current = Pending.popleft()
            for Neighbor in sorted(ResourceGraph.Technology.NeighborPositions(Current)):
                Primitive = ResourceGraph.BuildPrimitive(Current, Neighbor)
                if (
                    Neighbor in Paths
                    or any(Neighbor[Index] < Minimum[Index] or Neighbor[Index] > Maximum[Index] for Index in range(3))
                    or (Neighbor in Nodes and Neighbor not in TargetNodes)
                    or Primitive is None
                ):
                    continue
                CandidatePath = (*Paths[Current], Neighbor)
                AdjacentPrimitiveClaims = tuple(
                    AdjacentPrimitive.Claims
                    for Adjacent in ResourceGraph.Technology.NeighborPositions(Neighbor)
                    if Adjacent in ClaimsByNode[Current].WireCells
                    if (
                        AdjacentPrimitive := ResourceGraph.BuildPrimitive(
                            Adjacent,
                            Neighbor,
                        )
                    ) is not None
                )
                CandidateClaims = _MergeClaims((
                    ClaimsByNode[Current],
                    *AdjacentPrimitiveClaims,
                ))
                if FindSelfClaimConflicts({Signal: CandidateClaims}):
                    continue
                Paths[Neighbor] = CandidatePath
                ClaimsByNode[Neighbor] = CandidateClaims
                if Neighbor in TargetNodes:
                    Reached = Neighbor
                    break
                Pending.append(Neighbor)
        if Reached is None:
            continue
        Path = Paths[Reached]
        Nodes.update(Path)
        Edges.update(
            _NormalizedEdge(First, Second)
            for First, Second in zip(Path, Path[1:])
        )
        BridgedSignals.append(Signal)
    if not BridgedSignals:
        return Fabric
    Origin = tuple(min(Value[Index] for Value in Nodes) for Index in range(3))
    return ComponentRoutingFabric(
        FabricFingerprint=_StableFingerprint((
            "closed-component-bridged-forest-v1",
            Fabric.FabricFingerprint,
            tuple(BridgedSignals),
            tuple(sorted(tuple(Value[Index] - Origin[Index] for Index in range(3)) for Value in Nodes)),
            tuple(sorted(Edges)),
        )),
        Nodes=tuple(sorted(Nodes)),
        Edges=tuple(sorted(Edges)),
        IngressNodes=Fabric.IngressNodes,
        TopologyKind="closed-component-bridged-forest-v1",
        Complete=Fabric.Complete,
        IncompleteReason=Fabric.IncompleteReason,
    )


def _BuildAccessCandidate(
    Portal: PinAccessPortal,
) -> ComponentTerminalAccessCandidate:
    return ComponentTerminalAccessCandidate(
        CandidateFingerprint=_StableFingerprint((
            _RelativeGeometry(Portal.Path),
            _ClaimsFingerprint(Portal.Claims),
            Portal.Layer,
        )),
        Attachment=Portal.Path[-1],
        Path=tuple(Portal.Path),
        Claims=Portal.Claims,
        Layer=int(Portal.Layer),
        Cost=int(Portal.Cost),
    )


def PruneDominatedComponentAccessCandidates(
    Candidates: Iterable[ComponentTerminalAccessCandidate],
) -> tuple[ComponentTerminalAccessCandidate, ...]:
    """Remove strictly larger access paths with the same fabric attachment."""
    Retained: list[ComponentTerminalAccessCandidate] = []
    for Candidate in sorted(
        Candidates,
        key=lambda Value: (
            len(Value.Claims.ResourceIds),
            Value.Cost,
            len(Value.Path),
            Value.CandidateFingerprint,
        ),
    ):
        Dominated = any(
            Existing.Attachment == Candidate.Attachment
            and Existing.Claims.WireCells
            <= Candidate.Claims.WireCells
            and Existing.Claims.SupportCells
            <= Candidate.Claims.SupportCells
            and Existing.Claims.RequiredAirCells
            <= Candidate.Claims.RequiredAirCells
            and Existing.Claims.ElectricalCells
            <= Candidate.Claims.ElectricalCells
            for Existing in Retained
        )
        if not Dominated:
            Retained.append(Candidate)
    return tuple(sorted(
        Retained,
        key=lambda Value: Value.CandidateFingerprint,
    ))


def BuildCoalescedComponentAccessCandidates(
    Candidate: ComponentTerminalAccessCandidate,
    Trunks: tuple[ComponentTerminalAccessCandidate, ...],
    *,
    ResourceGraph: Any,
    ExistingNodes: frozenset[Position3] = frozenset(),
    MaximumCandidates: int = 16,
) -> tuple[ComponentTerminalAccessCandidate, ...]:
    """Join one same-net terminal to an already selected access trunk.

    Independent pin portals can form mutually impossible parallel staircases
    for adjacent fanout terminals.  A closed component owns those terminals
    jointly, so it may merge a terminal into a peer path before the peer's
    immutable fabric attachment.  Every returned branch is revalidated with
    the authoritative resource graph; this does not invent routing geometry
    or an alternative boundary port.
    """
    if ResourceGraph is None or not Candidate.Path or not Trunks:
        return ()
    Terminal = tuple(Candidate.Path[0])
    # Preserve part of the bounded portfolio for non-planar branches.  Without
    # this, two X/Z orderings from the first flat merge can consume the whole
    # cap and make the 3D repair path unreachable.
    MaximumFlatCandidates = max(1, MaximumCandidates // 2)

    def HorizontalPath(
        Target: Position3,
        *,
        XFirst: bool,
    ) -> tuple[Position3, ...]:
        if Target[1] != Terminal[1]:
            return ()
        Values = [Terminal]
        CurrentX, CurrentY, CurrentZ = Terminal
        Axes = ("X", "Z") if XFirst else ("Z", "X")
        for Axis in Axes:
            TargetValue = Target[0] if Axis == "X" else Target[2]
            CurrentValue = CurrentX if Axis == "X" else CurrentZ
            Step = 1 if TargetValue > CurrentValue else -1
            while CurrentValue != TargetValue:
                CurrentValue += Step
                if Axis == "X":
                    CurrentX = CurrentValue
                else:
                    CurrentZ = CurrentValue
                Values.append((CurrentX, CurrentY, CurrentZ))
        return tuple(Values)

    Results: dict[str, ComponentTerminalAccessCandidate] = {}
    RankedMerges = sorted(
        (
            abs(Terminal[0] - Merge[0])
            + abs(Terminal[2] - Merge[2]),
            Trunk.CandidateFingerprint,
            MergeIndex,
            TrunkIndex,
            Trunk,
            Merge,
        )
        for TrunkIndex, Trunk in enumerate(Trunks)
        for MergeIndex, Merge in enumerate(Trunk.Path)
        if Merge[1] == Terminal[1]
    )
    for _Distance, _Fingerprint, MergeIndex, _TrunkIndex, Trunk, Merge in RankedMerges:
        if len(Results) >= MaximumFlatCandidates:
            break
        for XFirst in (True, False):
            Branch = HorizontalPath(Merge, XFirst=XFirst)
            if not Branch:
                continue
            Path = tuple(dict.fromkeys((
                *Branch,
                *Trunk.Path[MergeIndex + 1 :],
            )))
            if (
                Path[-1] != Trunk.Attachment
                or any(
                    ResourceGraph.BuildPrimitive(First, Second) is None
                    for First, Second in zip(Path, Path[1:])
                )
            ):
                continue
            Claims = ResourceGraph.BuildRouteClaims(frozenset(Path))
            if FindSelfClaimConflicts({
                "component-coalesced-access": ResourceGraph.BuildRouteClaims(
                    ExistingNodes | frozenset(Path)
                )
            }):
                continue
            Fingerprint = _StableFingerprint((
                "coalesced-component-access-v1",
                _RelativeGeometry(Path),
                _ClaimsFingerprint(Claims),
                Candidate.Layer,
            ))
            Results.setdefault(
                Fingerprint,
                ComponentTerminalAccessCandidate(
                    CandidateFingerprint=Fingerprint,
                    Attachment=Trunk.Attachment,
                    Path=Path,
                    Claims=Claims,
                    Layer=Candidate.Layer,
                    Cost=len(Path) - 1,
                ),
            )
            if len(Results) >= MaximumFlatCandidates:
                break
    # The normal portal deck often changes elevation before reaching its
    # channel attachment.  A same-height Manhattan branch can be individually
    # legal yet electrically incompatible with its peer staircase.  Add a
    # bounded graph-derived branch portfolio as well; the exact frontier DP
    # still selects the canonical minimum legal geometry.
    Technology = getattr(ResourceGraph, "Technology", None)
    NeighborPositions = getattr(Technology, "NeighborPositions", None)
    if len(Results) >= MaximumCandidates or not callable(NeighborPositions):
        return tuple(Results.values())

    def BuildBoundedBranch(
        Merge: Position3,
        Trunk: ComponentTerminalAccessCandidate,
        FirstStep: Position3 | None = None,
    ) -> tuple[Position3, ...]:
        Padding = 2
        Minimum = tuple(
            min(Terminal[Index], Merge[Index]) - Padding
            for Index in range(3)
        )
        Maximum = tuple(
            max(Terminal[Index], Merge[Index]) + Padding
            for Index in range(3)
        )
        Pending = deque((Terminal,))
        Previous: dict[Position3, Position3 | None] = {Terminal: None}
        # A branch may enter its peer path only at the nominated merge.  This
        # avoids a hidden earlier join followed by a non-contiguous suffix.
        BlockedTrunkNodes = frozenset(Trunk.Path) - frozenset((Merge,))
        if FirstStep is not None:
            if (
                FirstStep in BlockedTrunkNodes
                or any(
                    FirstStep[Index] < Minimum[Index]
                    or FirstStep[Index] > Maximum[Index]
                    for Index in range(3)
                )
                or ResourceGraph.BuildPrimitive(Terminal, FirstStep) is None
            ):
                return ()
            Previous[FirstStep] = Terminal
            Pending = deque((FirstStep,))
        while Pending:
            Current = Pending.popleft()
            if Current == Merge:
                Path = [Current]
                while Path[-1] != Terminal:
                    Parent = Previous[Path[-1]]
                    assert Parent is not None
                    Path.append(Parent)
                return tuple(reversed(Path))
            for Neighbor in sorted(NeighborPositions(Current)):
                if (
                    Neighbor in Previous
                    or any(
                        Neighbor[Index] < Minimum[Index]
                        or Neighbor[Index] > Maximum[Index]
                        for Index in range(3)
                    )
                    or Neighbor in BlockedTrunkNodes
                    or ResourceGraph.BuildPrimitive(Current, Neighbor) is None
                ):
                    continue
                Previous[Neighbor] = Current
                Pending.append(Neighbor)
        return ()

    AllMerges = sorted(
        (
            sum(
                abs(Terminal[Index] - Merge[Index])
                for Index in range(3)
            ),
            Trunk.CandidateFingerprint,
            MergeIndex,
            TrunkIndex,
            Trunk,
            Merge,
        )
        for TrunkIndex, Trunk in enumerate(Trunks)
        for MergeIndex, Merge in enumerate(Trunk.Path)
    )
    for _Distance, _Fingerprint, MergeIndex, _TrunkIndex, Trunk, Merge in AllMerges:
        FirstSteps = (None, *sorted(NeighborPositions(Terminal)))
        for FirstStep in FirstSteps:
            Branch = BuildBoundedBranch(Merge, Trunk, FirstStep)
            if not Branch:
                continue
            Path = tuple(dict.fromkeys((
                *Branch,
                *Trunk.Path[MergeIndex + 1 :],
            )))
            if Path[-1] != Trunk.Attachment:
                continue
            if any(
                ResourceGraph.BuildPrimitive(First, Second) is None
                for First, Second in zip(Path, Path[1:])
            ):
                continue
            Claims = ResourceGraph.BuildRouteClaims(frozenset(Path))
            if FindSelfClaimConflicts({
                "component-coalesced-access": ResourceGraph.BuildRouteClaims(
                    ExistingNodes | frozenset(Path)
                )
            }):
                continue
            Fingerprint = _StableFingerprint((
                "coalesced-component-access-bounded-v1",
                _RelativeGeometry(Path),
                _ClaimsFingerprint(Claims),
                Candidate.Layer,
            ))
            Results.setdefault(Fingerprint, ComponentTerminalAccessCandidate(
                CandidateFingerprint=Fingerprint,
                Attachment=Trunk.Attachment,
                Path=Path,
                Claims=Claims,
                Layer=Candidate.Layer,
                Cost=len(Path) - 1,
            ))
            if len(Results) >= MaximumCandidates:
                break
        if len(Results) >= MaximumCandidates:
            break
    return tuple(Results.values())


def CoalesceOwnedSignalAccessDomains(
    Domains: Iterable[ComponentTerminalAccessDomain],
    *,
    ResourceGraph: Any,
    MaximumAdditionalCandidatesPerDomain: int = 2,
) -> tuple[ComponentTerminalAccessDomain, ...]:
    """Add bounded same-net access branches for jointly owned terminals.

    Raw pin portals are generated independently.  Two legal staircases can
    nevertheless claim mutually exclusive redstone support when a component
    owns both terminals of the same net.  A coalesced branch joins one
    terminal to a peer's already legal trunk before its fabric attachment;
    the tree-frontier solver can then select one shared electrical geometry.
    """
    BySignal: dict[str, list[ComponentTerminalAccessDomain]] = defaultdict(list)
    for Domain in Domains:
        BySignal[str(Domain.Signal)].append(Domain)
    Result: list[ComponentTerminalAccessDomain] = []
    for Signal in sorted(BySignal):
        SignalDomains = sorted(
            BySignal[Signal],
            key=lambda Value: (
                Value.TerminalRole,
                Value.Terminal,
                Value.TerminalFingerprint,
            ),
        )
        # Coalescing is a repair for a locally owned driver and sink that
        # independently chose incompatible portal staircases.  Input-only
        # fanout terminals have no local driver to share and expanding them
        # turns a compact symbolic frontier into an unnecessary cross product.
        HasLocalSource = any(
            Domain.TerminalRole == "source" for Domain in SignalDomains
        )
        HasLocalTarget = any(
            Domain.TerminalRole == "target" for Domain in SignalDomains
        )
        for Domain in SignalDomains:
            Original = tuple(sorted(
                Domain.Candidates,
                key=lambda Value: Value.CandidateFingerprint,
            ))
            PeerTrunks = tuple(
                Candidate
                for Peer in SignalDomains
                if Peer.Terminal != Domain.Terminal
                for Candidate in sorted(
                    Peer.Candidates,
                    key=lambda Value: Value.CandidateFingerprint,
                )
            )
            Added: list[ComponentTerminalAccessCandidate] = []
            if (
                ResourceGraph is not None
                and PeerTrunks
                and HasLocalSource
                and HasLocalTarget
            ):
                for Candidate in Original:
                    Remaining = (
                        MaximumAdditionalCandidatesPerDomain - len(Added)
                    )
                    if Remaining <= 0:
                        break
                    Added.extend(BuildCoalescedComponentAccessCandidates(
                        Candidate,
                        PeerTrunks,
                        ResourceGraph=ResourceGraph,
                        MaximumCandidates=Remaining,
                    ))
            CandidatesByFingerprint = {
                Candidate.CandidateFingerprint: Candidate
                for Candidate in (*Original, *Added)
            }
            Candidates = PruneDominatedComponentAccessCandidates(
                CandidatesByFingerprint[Fingerprint]
                for Fingerprint in sorted(CandidatesByFingerprint)
            )
            Result.append(ComponentTerminalAccessDomain(
                Signal=Domain.Signal,
                Terminal=Domain.Terminal,
                TerminalRole=Domain.TerminalRole,
                TerminalFingerprint=_StableFingerprint((
                    "coalesced-owned-component-access-domain-v1",
                    Domain.TerminalRole,
                    len(Candidates),
                    tuple(
                        Candidate.CandidateFingerprint
                        for Candidate in Candidates
                    ),
                )),
                Candidates=Candidates,
                Complete=Domain.Complete,
            ))
    return tuple(Result)


def BuildClosedComponentInterface(
    *,
    Channel: Any,
    Fabric: ComponentRoutingFabric,
    Profiles: dict[str, Any],
    ComponentSignals: Iterable[str],
    ComponentPairs: Iterable[tuple[str, Position3]],
) -> ClosedComponentInterface:
    """Freeze owned terminals, exported ports, and explicit feedthroughs."""
    PairSet = frozenset(
        (str(Signal), tuple(Terminal))
        for Signal, Terminal in ComponentPairs
    )
    OwnedSignals = tuple(sorted(set(map(str, ComponentSignals))))
    Ports = []
    for Signal in OwnedSignals:
        Profile = Profiles.get(Signal)
        if Profile is None:
            continue
        AllTerminals = tuple(dict.fromkeys((
            Profile.Root,
            *Profile.Targets,
        )))
        OwnedTerminals = tuple(sorted(
            Terminal
            for Terminal in AllTerminals
            if (Signal, Terminal) in PairSet
        ))
        ExternalTerminals = tuple(
            Terminal
            for Terminal in AllTerminals
            if (Signal, Terminal) not in PairSet
        )
        if not ExternalTerminals:
            continue
        RootOwned = (Signal, Profile.Root) in PairSet
        Ports.append(ComponentInterfacePort(
            Signal=Signal,
            Direction="output" if RootOwned else "input",
            OwnedTerminals=OwnedTerminals,
            ExternalTerminalCount=len(ExternalTerminals),
            Capacity=max(1, len(ExternalTerminals)),
        ))

    LaneEndpointPairs = tuple(sorted({
        (
            tuple(Lane.Cells[0]),
            tuple(Lane.Cells[-1]),
        )
        for Lane in getattr(Channel, "Lanes", ())
        if len(getattr(Lane, "Cells", ())) >= 2
        and Lane.Cells[0] != Lane.Cells[-1]
    }))
    Feedthroughs = tuple(
        ComponentFeedthroughContract(
            Signal=str(Signal),
            EndpointPairs=LaneEndpointPairs,
            Capacity=1,
        )
        for Signal in sorted(set(getattr(
            Channel,
            "DeclaredFeedthroughSignals",
            (),
        )))
    )
    StructuralIdentity = (
        getattr(Channel, "InterfaceFingerprint", ""),
        tuple(sorted(
            (
                Port.Direction,
                len(Port.OwnedTerminals),
                Port.ExternalTerminalCount,
                Port.Capacity,
            )
            for Port in Ports
        )),
        tuple(
            (
                len(Value.EndpointPairs),
                Value.Capacity,
                tuple(
                    (
                        _RelativeGeometry((Entry, Exit))
                    )
                    for Entry, Exit in Value.EndpointPairs
                ),
            )
            for Value in Feedthroughs
        ),
        Fabric.FabricFingerprint,
    )
    return ClosedComponentInterface(
        InterfaceFingerprint=_StableFingerprint(StructuralIdentity),
        ComponentId=getattr(Channel, "ComponentId", None),
        OwnedSignals=OwnedSignals,
        Ports=tuple(sorted(
            Ports,
            key=lambda Value: (
                Value.Direction,
                Value.Signal,
            ),
        )),
        Feedthroughs=Feedthroughs,
        Complete=bool(
            Fabric.Complete
            and OwnedSignals
            and all(
                any(
                    CandidateSignal == Signal
                    for CandidateSignal, _Terminal in PairSet
                )
                for Signal in OwnedSignals
            )
            and all(Value.EndpointPairs for Value in Feedthroughs)
        ),
    )


def SelectClosedComponentOwnedTerminalPairs(
    Placed: Any,
    Profiles: Mapping[str, Any],
) -> frozenset[tuple[str, Position3]]:
    """Select terminals owned by the placed closed component.

    This topology-only selection deliberately has no portal dependency.  It
    lets physical planning prepare the exact owned-terminal portal slice and
    prove its local frontier before spending work on unrelated global nets.
    """
    Channel = getattr(Placed, "InterClusterRoutingChannel", None)
    SelectedClusters = tuple(sorted(
        int(Value)
        for Value in getattr(Channel, "AffectedClusters", ())
    ))
    SelectedClusterSet = frozenset(SelectedClusters)
    BoundaryRequests = tuple(
        getattr(Placed, "ClusterBoundaryLeaseRequests", ()) or ()
    )
    IncidentSignals = {
        str(Signal)
        for Signal in getattr(Channel, "AffectedSignals", ())
        if str(Signal) in Profiles
    }
    IncidentSignals.update(SelectComponentIncidentSignals(
        BoundaryRequests,
        SelectedClusters,
        dict(Profiles),
    ))
    ComponentGraph = getattr(Placed, "ComponentGraph", None)
    ComponentId = getattr(Channel, "ComponentId", None)
    TopologyComponent = next(
        (
            Value
            for Value in getattr(ComponentGraph, "Components", ())
            if Value.ComponentId == ComponentId
        ),
        None,
    )
    ComponentGateNames = frozenset((
        *getattr(TopologyComponent, "GateNames", ()),
        *(
            Name
            for Cluster in (
                getattr(Placed, "PackedClusters", ()) or ()
            )
            if int(getattr(Cluster, "ClusterId", -1))
            in SelectedClusterSet
            for Name in getattr(Cluster, "MemberNands", ())
        ),
    ))
    ComponentPairs: set[tuple[str, Position3]] = set()
    for Request in BoundaryRequests:
        Signal = str(Request.Signal)
        if Signal not in IncidentSignals:
            continue
        Profile = Profiles[Signal]
        ProfileTerminals = frozenset((
            tuple(Profile.Root),
            *(tuple(Terminal) for Terminal in Profile.Targets),
        ))
        SourceIsSelected = (
            int(Request.SourceCluster) in SelectedClusterSet
        )
        TargetIsSelected = (
            int(Request.TargetCluster) in SelectedClusterSet
        )
        if not (SourceIsSelected or TargetIsSelected):
            continue
        if (
            SourceIsSelected
            and Request.SourceTerminal is not None
            and tuple(Request.SourceTerminal) in ProfileTerminals
        ):
            ComponentPairs.add((
                Signal,
                tuple(Request.SourceTerminal),
            ))
        if TargetIsSelected:
            ComponentPairs.update(
                (Signal, tuple(Terminal))
                for Terminal in Request.TargetTerminals
                if tuple(Terminal) in ProfileTerminals
            )
    if ComponentGateNames:
        for Gate in getattr(Placed, "PlacedGates", ()):
            if Gate.Name not in ComponentGateNames:
                continue
            OutputPin = getattr(Gate, "OutputPin", None)
            if OutputPin is not None:
                for Signal, Profile in Profiles.items():
                    if (
                        Signal in IncidentSignals
                        and tuple(Profile.Root) == tuple(OutputPin)
                    ):
                        ComponentPairs.add((
                            str(Signal),
                            tuple(OutputPin),
                        ))
            InputPins = frozenset(map(
                tuple,
                getattr(Gate, "InputPins", ()),
            ))
            if InputPins:
                for Signal, Profile in Profiles.items():
                    if Signal not in IncidentSignals:
                        continue
                    ComponentPairs.update(
                        (str(Signal), tuple(Terminal))
                        for Terminal in Profile.Targets
                        if tuple(Terminal) in InputPins
                    )
    return frozenset(ComponentPairs)

def _BuildAdjacency(
    Edges: Iterable[RoutingEdge],
) -> dict[Position3, set[Position3]]:
    Adjacency: dict[Position3, set[Position3]] = defaultdict(set)
    for First, Second in Edges:
        Adjacency[First].add(Second)
        Adjacency[Second].add(First)
    return Adjacency


def _UniqueFabricSubtree(
    Fabric: ComponentRoutingFabric,
    Attachments: Iterable[Position3],
    *,
    Adjacency: dict[Position3, set[Position3]] | None = None,
    ParentCache: dict[
        Position3,
        dict[Position3, Position3 | None],
    ] | None = None,
) -> tuple[frozenset[Position3], frozenset[RoutingEdge]] | None:
    Required = tuple(sorted(set(Attachments)))
    if not Required:
        return None
    Adjacency = (
        _BuildAdjacency(Fabric.Edges)
        if Adjacency is None
        else Adjacency
    )
    Root = Required[0]
    Parents = (
        ParentCache.get(Root)
        if ParentCache is not None
        else None
    )
    if Parents is None:
        Parents = {Root: None}
        Pending = deque([Root])
        while Pending:
            Current = Pending.popleft()
            for Neighbor in sorted(Adjacency.get(Current, ())):
                if Neighbor not in Parents:
                    Parents[Neighbor] = Current
                    Pending.append(Neighbor)
        if ParentCache is not None:
            ParentCache[Root] = Parents
    if any(Value not in Parents for Value in Required):
        return None
    Nodes = {Root}
    Edges: set[RoutingEdge] = set()
    for Target in Required[1:]:
        Current = Target
        while Current != Root:
            Parent = Parents[Current]
            assert Parent is not None
            Nodes.update((Current, Parent))
            Edges.add(_NormalizedEdge(Current, Parent))
            Current = Parent
    return frozenset(Nodes), frozenset(Edges)


def BuildUniqueComponentFabricSubtree(
    Fabric: ComponentRoutingFabric,
    Attachments: Iterable[Position3],
) -> tuple[frozenset[Position3], frozenset[RoutingEdge]] | None:
    """Return the deterministic lane-local subtree for a fixed port contract."""
    return _UniqueFabricSubtree(Fabric, Attachments)


def BuildClaimsAwareComponentFabricSubtree(
    Fabric: ComponentRoutingFabric,
    Attachments: Iterable[Position3],
    ResourceGraph: Any,
    *,
    FixedNodes: Iterable[Position3] = (),
) -> tuple[frozenset[Position3], frozenset[RoutingEdge]] | None:
    """Return the first deterministic subtree with self-compatible claims."""
    Required = tuple(sorted(set(Attachments)))
    if not Required or ResourceGraph is None:
        return None
    Adjacency = _BuildAdjacency(Fabric.Edges)
    if any(Attachment not in Adjacency for Attachment in Required):
        return None
    TreeNodes: set[Position3] = {Required[0]}
    TreeEdges: set[RoutingEdge] = set()
    FixedNodeSet = frozenset((*map(tuple, FixedNodes), Required[0]))
    TreeClaims = ResourceGraph.BuildRouteClaims(FixedNodeSet)
    if FindSelfClaimConflicts({"__ComponentFabric__": TreeClaims}):
        return None
    for Target in Required[1:]:
        if Target in TreeNodes:
            continue
        Pending = deque(sorted(TreeNodes))
        Paths = {Node: (Node,) for Node in TreeNodes}
        ClaimsByNode = {Node: TreeClaims for Node in TreeNodes}
        Reached: Position3 | None = None
        while Pending and Reached is None:
            Current = Pending.popleft()
            for Neighbor in sorted(Adjacency.get(Current, ())):
                if Neighbor in Paths:
                    continue
                AdjacentPrimitiveClaims = tuple(
                    Primitive.Claims
                    for Adjacent in ResourceGraph.Technology.NeighborPositions(Neighbor)
                    if Adjacent in ClaimsByNode[Current].WireCells
                    if (Primitive := ResourceGraph.BuildPrimitive(Adjacent, Neighbor)) is not None
                )
                if not AdjacentPrimitiveClaims:
                    continue
                CandidateClaims = _MergeClaims((
                    ClaimsByNode[Current],
                    *AdjacentPrimitiveClaims,
                ))
                if FindSelfClaimConflicts({"__ComponentFabric__": CandidateClaims}):
                    continue
                Paths[Neighbor] = (*Paths[Current], Neighbor)
                ClaimsByNode[Neighbor] = CandidateClaims
                if Neighbor == Target:
                    Reached = Neighbor
                    break
                Pending.append(Neighbor)
        if Reached is None:
            return None
        Path = Paths[Reached]
        TreeNodes.update(Path)
        TreeEdges.update(
            _NormalizedEdge(First, Second)
            for First, Second in zip(Path, Path[1:])
        )
        TreeClaims = ClaimsByNode[Reached]
    return frozenset(TreeNodes), frozenset(TreeEdges)


def BuildComponentFabricAdjacency(
    Fabric: ComponentRoutingFabric,
) -> dict[Position3, set[Position3]]:
    """Build one reusable adjacency index for component eligibility work."""
    return _BuildAdjacency(Fabric.Edges)


MaximumExternalSourcePoweredSeamEligibilityCacheEntries = 32_768


def FilterExternalSourcePoweredSeamCandidateDomains(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    CandidateDomains: tuple[
        tuple[ComponentTerminalAccessCandidate, ...], ...
    ],
    LocalPath: tuple[Position3, ...],
    *,
    FabricAdjacency: dict[
        Position3, set[Position3]
    ] | None = None,
    FabricParentCache: dict[
        Position3, dict[Position3, Position3 | None]
    ] | None = None,
    RouteClaimsCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    TreeRepeaterSubproblemCache: dict[
        tuple[int, int, str],
        tuple[tuple[Position3, str], ...] | None,
    ] | None = None,
    TreeRepeaterCacheStatistics: dict[str, int] | None = None,
    CandidateEligibilityCache: dict[str, bool] | None = None,
    CandidateEligibilityCacheStatistics: dict[str, int] | None = None,
    CandidateEligibilityDiagnostics: dict[str, object] | None = None,
) -> tuple[tuple[ComponentTerminalAccessCandidate, ...], ...]:
    """Keep terminal candidates individually power-reachable from a seam.

    This is a necessary, factorized eligibility check for component inputs.
    It never joins candidates across terminal domains and therefore does not
    replace authoritative closed-component compilation.
    """
    HasExternalSource = any(
        ExternalSignal == Signal and Role == "source"
        for ExternalSignal, _Terminal, Role
        in Problem.ExternalContinuationTerminals
    )
    if not HasExternalSource or not LocalPath:
        return CandidateDomains

    def RecordEligibilityResult(Key: str) -> None:
        if CandidateEligibilityDiagnostics is None:
            return
        CandidateEligibilityDiagnostics[Key] = (
            int(CandidateEligibilityDiagnostics.get(Key, 0)) + 1
        )

    def RecordSelfConflictResources(
        Conflicts: dict[RoutingResourceId, tuple[str, ...]],
        Root: Position3,
    ) -> None:
        if CandidateEligibilityDiagnostics is None:
            return
        Counts = CandidateEligibilityDiagnostics.setdefault(
            "SelfClaimConflictResourceCounts",
            {},
        )
        Samples = CandidateEligibilityDiagnostics.setdefault(
            "SelfClaimConflictResourceSamples",
            [],
        )
        assert isinstance(Counts, dict)
        assert isinstance(Samples, list)
        for Resource in sorted(
            Conflicts,
            key=lambda Value: (Value.Kind.value, Value.Position),
        ):
            Kind = Resource.Kind.value
            Counts[Kind] = int(Counts.get(Kind, 0)) + 1
            Sample = {
                "Kind": Kind,
                "RelativePosition": [
                    Resource.Position[Index] - Root[Index]
                    for Index in range(3)
                ],
            }
            if Sample not in Samples and len(Samples) < 16:
                Samples.append(Sample)

    FabricAttachment = LocalPath[0]
    Root = LocalPath[-1]
    LocalClaims = tuple(
        Claim
        for Claim in Problem.LocalClaims
        if Claim.Signal == Signal
    )
    Result = []
    for Domain, Candidates in zip(Domains, CandidateDomains):
        Retained = []
        for Candidate in Candidates:
            Subtree = _UniqueFabricSubtree(
                Problem.Fabric,
                (Candidate.Attachment, FabricAttachment),
                Adjacency=FabricAdjacency,
                ParentCache=FabricParentCache,
            )
            if Subtree is None:
                RecordEligibilityResult("DisconnectedFabricSubtreeCount")
                continue
            FabricNodes, FabricEdges = Subtree
            Nodes = set(FabricNodes)
            Edges = set(FabricEdges)
            Nodes.update(Candidate.Path)
            Edges.update(
                _NormalizedEdge(First, Second)
                for First, Second in zip(
                    Candidate.Path,
                    Candidate.Path[1:],
                )
            )
            Nodes.update(LocalPath)
            Edges.update(
                _NormalizedEdge(First, Second)
                for First, Second in zip(LocalPath, LocalPath[1:])
            )
            for Claim in LocalClaims:
                Nodes.update(Claim.Nodes)
                Edges.update(
                    _NormalizedEdge(*Edge) for Edge in Claim.Edges
                )
            FrozenNodes = frozenset(Nodes)
            Relative = lambda Position: (
                Position[0] - Root[0],
                Position[1] - Root[1],
                Position[2] - Root[2],
            )
            Technology = getattr(Problem.ResourceGraph, "Technology", None)
            CandidateEligibilityFingerprint = _StableFingerprint((
                "external-source-powered-seam-candidate-eligibility-v1",
                type(Technology).__qualname__,
                repr(Technology),
                int(Problem.MaximumPowerDistance),
                tuple(sorted(Relative(Position) for Position in FrozenNodes)),
                tuple(sorted(
                    _NormalizedEdge(Relative(First), Relative(Second))
                    for First, Second in Edges
                )),
            ))
            if (
                CandidateEligibilityCache is not None
                and CandidateEligibilityFingerprint
                in CandidateEligibilityCache
            ):
                if CandidateEligibilityCacheStatistics is not None:
                    CandidateEligibilityCacheStatistics["HitCount"] = (
                        CandidateEligibilityCacheStatistics.get(
                            "HitCount",
                            0,
                        )
                        + 1
                    )
                if CandidateEligibilityCache[CandidateEligibilityFingerprint]:
                    RecordEligibilityResult("CachedFeasibleCount")
                    Retained.append(Candidate)
                else:
                    RecordEligibilityResult("CachedInfeasibleCount")
                continue
            if CandidateEligibilityCacheStatistics is not None:
                CandidateEligibilityCacheStatistics["MissCount"] = (
                    CandidateEligibilityCacheStatistics.get("MissCount", 0)
                    + 1
                )
            Claims = (
                RouteClaimsCache.get(FrozenNodes)
                if RouteClaimsCache is not None
                else None
            )
            if Claims is None:
                Claims = Problem.ResourceGraph.BuildRouteClaims(
                    FrozenNodes
                )
                if RouteClaimsCache is not None:
                    RouteClaimsCache[FrozenNodes] = Claims
            SelfConflicts = FindSelfClaimConflicts({Signal: Claims})
            RepeaterPlan = (
                None
                if SelfConflicts
                else _PlanTreeRepeaters(
                    FrozenNodes,
                    frozenset(Edges),
                    Root,
                    Problem.MaximumPowerDistance,
                    SubproblemCache=TreeRepeaterSubproblemCache,
                    CacheStatistics=TreeRepeaterCacheStatistics,
                )
            )
            Feasible = bool(not SelfConflicts and RepeaterPlan is not None)
            if SelfConflicts:
                RecordEligibilityResult("SelfClaimConflictCount")
                RecordSelfConflictResources(SelfConflicts, Root)
            elif RepeaterPlan is None:
                RecordEligibilityResult("RepeaterPlanInfeasibleCount")
            else:
                RecordEligibilityResult("FeasibleCount")
            if CandidateEligibilityCache is not None:
                CandidateEligibilityCache[CandidateEligibilityFingerprint] = (
                    Feasible
                )
                if CandidateEligibilityCacheStatistics is not None:
                    CandidateEligibilityCacheStatistics["StoreCount"] = (
                        CandidateEligibilityCacheStatistics.get(
                            "StoreCount",
                            0,
                        )
                        + 1
                    )
                while len(CandidateEligibilityCache) > (
                    MaximumExternalSourcePoweredSeamEligibilityCacheEntries
                ):
                    CandidateEligibilityCache.pop(
                        next(iter(CandidateEligibilityCache))
                    )
            if Feasible:
                Retained.append(Candidate)
        Result.append(tuple(Retained))
    return tuple(Result)

def _BuildTreeEdges(
    Nodes: Iterable[Position3],
    Edges: Iterable[RoutingEdge],
    Root: Position3,
) -> tuple[dict[Position3, Position3 | None], dict[Position3, list[Position3]]] | None:
    Adjacency = _BuildAdjacency(Edges)
    Parents: dict[Position3, Position3 | None] = {Root: None}
    Children: dict[Position3, list[Position3]] = defaultdict(list)
    Pending = deque([Root])
    while Pending:
        Current = Pending.popleft()
        for Neighbor in sorted(Adjacency.get(Current, ())):
            if Neighbor in Parents:
                continue
            Parents[Neighbor] = Current
            Children[Current].append(Neighbor)
            Pending.append(Neighbor)
    if set(Nodes) - set(Parents):
        return None
    return Parents, Children


def _RepeaterInputFacing(
    Current: Position3,
    Next: Position3,
) -> str | None:
    try:
        return RepeaterInputFacingForStep(Current, Next)
    except ValueError:
        return None


def _PlanTreeRepeaters(
    Nodes: frozenset[Position3],
    Edges: frozenset[RoutingEdge],
    Root: Position3,
    MaximumDistance: int,
    SubproblemCache: dict[
        tuple[int, int, str],
        tuple[tuple[Position3, str], ...] | None,
    ] | None = None,
    CacheStatistics: dict[str, int] | None = None,
) -> tuple[tuple[Position3, str], ...] | None:
    """Use deterministic tree DP to minimize legal refresh elements."""
    Tree = _BuildTreeEdges(Nodes, Edges, Root)
    if Tree is None:
        return None
    Parents, Children = Tree
    SubtreeFingerprintByNode: dict[Position3, str] = {}
    TreeOrder = tuple(Parents)
    for Node in reversed(TreeOrder):
        SubtreeFingerprintByNode[Node] = _StableFingerprint((
            Node,
            Parents[Node],
            tuple(
                SubtreeFingerprintByNode[Child]
                for Child in Children.get(Node, ())
            ),
        ))

    Memo: dict[
        tuple[Position3, int],
        tuple[tuple[Position3, str], ...] | None,
    ] = {}
    Pending: list[tuple[Position3, int, bool]] = [(Root, 0, False)]
    InProgress: set[tuple[Position3, int]] = set()
    while Pending:
        Node, Distance, Finalize = Pending.pop()
        Key = Node, Distance
        if Key in Memo:
            continue
        SharedKey = (
            MaximumDistance,
            Distance,
            SubtreeFingerprintByNode[Node],
        )
        if not Finalize:
            if (
                SubproblemCache is not None
                and SharedKey in SubproblemCache
            ):
                if CacheStatistics is not None:
                    CacheStatistics["HitCount"] = (
                        CacheStatistics.get("HitCount", 0) + 1
                    )
                Memo[Key] = SubproblemCache[SharedKey]
                continue
            if Key in InProgress:
                continue
            InProgress.add(Key)
            if CacheStatistics is not None:
                CacheStatistics["MissCount"] = (
                    CacheStatistics.get("MissCount", 0) + 1
                )
            Pending.append((Node, Distance, True))
            Dependencies: list[tuple[Position3, int, bool]] = []
            if Distance <= MaximumDistance:
                Dependencies.extend(
                    (Child, Distance + 1, False)
                    for Child in Children.get(Node, ())
                )
            Parent = Parents[Node]
            ChildValues = Children.get(Node, ())
            if Parent is not None and len(ChildValues) == 1:
                Child = ChildValues[0]
                if Parent[1] == Node[1] == Child[1]:
                    Incoming = (
                        Node[0] - Parent[0],
                        Node[2] - Parent[2],
                    )
                    Outgoing = (
                        Child[0] - Node[0],
                        Child[2] - Node[2],
                    )
                    if Incoming == Outgoing and _RepeaterInputFacing(Node, Child) is not None:
                        Dependencies.append((Child, 1, False))
            Pending.extend(reversed(Dependencies))
            continue
        Options: list[tuple[tuple[Position3, str], ...]] = []
        if Distance <= MaximumDistance:
            ChildPlans = []
            for Child in Children.get(Node, ()):
                Plan = Memo[(Child, Distance + 1)]
                if Plan is None:
                    break
                ChildPlans.append(Plan)
            else:
                Options.append(tuple(sorted(
                    Value for Plan in ChildPlans for Value in Plan
                )))
        Parent = Parents[Node]
        ChildValues = Children.get(Node, ())
        if Parent is not None and len(ChildValues) == 1:
            Child = ChildValues[0]
            if Parent[1] == Node[1] == Child[1]:
                Incoming = (
                    Node[0] - Parent[0],
                    Node[2] - Parent[2],
                )
                Outgoing = (
                    Child[0] - Node[0],
                    Child[2] - Node[2],
                )
                Facing = (
                    _RepeaterInputFacing(Node, Child)
                    if Incoming == Outgoing
                    else None
                )
                if Facing is not None:
                    ChildPlan = Memo[(Child, 1)]
                    if ChildPlan is not None:
                        Options.append(tuple(sorted((
                            (Node, Facing),
                            *ChildPlan,
                        ))))
        Result = (
            min(Options, key=lambda Value: (len(Value), Value))
            if Options
            else None
        )
        Memo[Key] = Result
        InProgress.discard(Key)
        if SubproblemCache is not None:
            SubproblemCache[SharedKey] = Result

    return Memo[(Root, 0)]

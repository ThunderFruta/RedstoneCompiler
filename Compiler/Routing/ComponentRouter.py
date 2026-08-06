"""Exact bounded routing for reusable hierarchical routed components."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import combinations, islice, product
from math import prod as ProductIntegers
from time import monotonic
from typing import Any, Callable, Iterable, Mapping


from .Models import (
    ClosedComponentInterface,
    ComponentFeedthroughContract,
    ComponentForeignTransitDomain,
    ComponentInterfacePort,
    ComponentRoutingFabric,
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    Position3,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from .ResourceGraph import (
    FindSelfClaimConflicts,
    LocalRouteClaim,
    PinAccessPortal,
    RoutingEdge,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
    RoutingResourceClaims,
)
from .Technology import DefaultRedstoneRoutingTechnology

try:
    from ..RustRouting import (
        BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
    )
    from ..RustRouting import BuildRouteClaimsBatch as _BuildRouteClaimsBatch
    from ..RustRouting import (
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
    )
    from ..RustRouting import GetRoutingThreadCount as _GetRoutingThreadCount
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


def _StableFingerprint(Value: object) -> str:
    return sha256(repr(Value).encode("utf-8")).hexdigest()[:16]


def _NormalizedEdge(
    First: Position3,
    Second: Position3,
) -> RoutingEdge:
    return (First, Second) if First <= Second else (Second, First)


def _RelativeGeometry(
    Positions: Iterable[Position3],
) -> tuple[Position3, ...]:
    Values = tuple(sorted(set(Positions)))
    if not Values:
        return ()
    MinimumX = min(Value[0] for Value in Values)
    MinimumY = min(Value[1] for Value in Values)
    MinimumZ = min(Value[2] for Value in Values)
    return tuple(
        (
            Value[0] - MinimumX,
            Value[1] - MinimumY,
            Value[2] - MinimumZ,
        )
        for Value in Values
    )


def _ClaimsFingerprint(Claims: RoutingResourceClaims) -> str:
    return _StableFingerprint((
        _RelativeGeometry(Claims.WireCells),
        _RelativeGeometry(Claims.SupportCells),
        _RelativeGeometry(Claims.RequiredAirCells),
        _RelativeGeometry(Claims.ElectricalCells),
    ))


def _TranslatePosition(
    Position: Position3,
    Delta: Position3,
) -> Position3:
    return (
        Position[0] + Delta[0],
        Position[1] + Delta[1],
        Position[2] + Delta[2],
    )


def _TranslateClaims(
    Claims: RoutingResourceClaims,
    Delta: Position3,
) -> RoutingResourceClaims:
    return RoutingResourceClaims(
        WireCells=frozenset(
            _TranslatePosition(Value, Delta)
            for Value in Claims.WireCells
        ),
        SupportCells=frozenset(
            _TranslatePosition(Value, Delta)
            for Value in Claims.SupportCells
        ),
        RequiredAirCells=frozenset(
            _TranslatePosition(Value, Delta)
            for Value in Claims.RequiredAirCells
        ),
        ElectricalCells=frozenset(
            _TranslatePosition(Value, Delta)
            for Value in Claims.ElectricalCells
        ),
    )


def _NormalizePosition(
    Position: Position3,
    Origin: Position3,
) -> Position3:
    return _TranslatePosition(
        Position,
        (-Origin[0], -Origin[1], -Origin[2]),
    )


def _NormalizeClaims(
    Claims: RoutingResourceClaims,
    Origin: Position3,
) -> tuple[tuple[Position3, ...], ...]:
    return (
        tuple(sorted(
            _NormalizePosition(Value, Origin)
            for Value in Claims.WireCells
        )),
        tuple(sorted(
            _NormalizePosition(Value, Origin)
            for Value in Claims.SupportCells
        )),
        tuple(sorted(
            _NormalizePosition(Value, Origin)
            for Value in Claims.RequiredAirCells
        )),
        tuple(sorted(
            _NormalizePosition(Value, Origin)
            for Value in Claims.ElectricalCells
        )),
    )


def _ComponentOrigin(
    Problem: ComponentRoutingProblem,
) -> Position3:
    Positions = tuple(Problem.Fabric.Nodes)
    if not Positions:
        Positions = tuple(
            Domain.Terminal
            for Domain in Problem.OwnedTerminalDomains
        )
    if not Positions:
        return (0, 0, 0)
    return (
        min(Value[0] for Value in Positions),
        min(Value[1] for Value in Positions),
        min(Value[2] for Value in Positions),
    )


def _ComponentNetPortfolioStaticStructuralFingerprint(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Origin: Position3,
) -> str:
    """Identify the signal-static portion of a finite net portfolio."""
    ComponentSignals = frozenset(Problem.ComponentSignals)

    def OwnerRole(Value: str) -> str:
        if Value == Signal:
            return "self"
        if Value in ComponentSignals:
            return "component-peer"
        return "foreign"

    def CandidateIdentity(
        Candidate: ComponentTerminalAccessCandidate,
    ) -> tuple[object, ...]:
        return (
            _NormalizePosition(Candidate.Attachment, Origin),
            tuple(
                _NormalizePosition(Value, Origin)
                for Value in Candidate.Path
            ),
            _NormalizeClaims(Candidate.Claims, Origin),
            Candidate.Layer,
            Candidate.Cost,
        )

    def DomainIdentity(
        Domain: ComponentTerminalAccessDomain,
    ) -> tuple[object, ...]:
        return (
            Domain.TerminalRole,
            _NormalizePosition(Domain.Terminal, Origin),
            tuple(sorted(
                CandidateIdentity(Candidate)
                for Candidate in Domain.Candidates
            )),
            Domain.Complete,
        )

    def ClaimIdentity(Claim: Any) -> tuple[object, ...]:
        Claims = Claim.Claims
        return (
            OwnerRole(str(Claim.Signal)),
            (
                _NormalizePosition(Claim.Root, Origin)
                if hasattr(Claim, "Root")
                else None
            ),
            tuple(sorted(
                _NormalizePosition(Value, Origin)
                for Value in getattr(
                    Claim,
                    "ConnectedTargets",
                    (),
                )
            )),
            tuple(sorted(
                _NormalizePosition(Value, Origin)
                for Value in getattr(Claim, "BoundaryNodes", ())
            )),
            tuple(sorted(
                _NormalizePosition(Value, Origin)
                for Value in getattr(
                    Claim,
                    "Nodes",
                    Claims.WireCells,
                )
            )),
            tuple(sorted(
                _NormalizedEdge(
                    _NormalizePosition(First, Origin),
                    _NormalizePosition(Second, Origin),
                )
                for First, Second in getattr(Claim, "Edges", ())
            )),
            _NormalizeClaims(Claims, Origin),
        )

    ContinuationDomains = tuple(
        DomainIdentity(Domain)
        for Domain in Problem.ExternalContinuationDomains
        if Domain.Signal == Signal
    )
    ResourceGraph = Problem.ResourceGraph
    Technology = getattr(ResourceGraph, "Technology", None)
    ResourceCompletenessIdentity = (
        None
        if ResourceGraph is None
        else (
            getattr(ResourceGraph, "GraphVersion", None),
            type(Technology).__qualname__,
            getattr(Technology, "TechnologyVersion", None),
            repr(Technology),
            tuple(sorted(
                _NormalizePosition(Value, Origin)
                for Value in getattr(ResourceGraph, "ActualBlocks", ())
            )),
            tuple(sorted(
                _NormalizePosition(Value, Origin)
                for Value in getattr(ResourceGraph, "ElectricalBlocks", ())
            )),
            tuple(sorted(
                _NormalizePosition(Value, Origin)
                for Value in getattr(ResourceGraph, "SolidBlocks", ())
            )),
        )
    )
    return _StableFingerprint((
        "component-net-static-translation-v1",
        tuple(sorted(
            _NormalizePosition(Value, Origin)
            for Value in Problem.Fabric.Nodes
        )),
        tuple(sorted(
            _NormalizedEdge(
                _NormalizePosition(First, Origin),
                _NormalizePosition(Second, Origin),
            )
            for First, Second in Problem.Fabric.Edges
        )),
        tuple(sorted(DomainIdentity(Domain) for Domain in Domains)),
        tuple(sorted(
            ClaimIdentity(Claim)
            for Claim in (
                *Problem.LocalClaims,
                *Problem.ImmutableClaims,
            )
            if Claim.Signal == Signal
        )),
        tuple(sorted(
            (
                Role,
                _NormalizePosition(Terminal, Origin),
            )
            for ExternalSignal, Terminal, Role
            in Problem.ExternalContinuationTerminals
            if ExternalSignal == Signal
        )),
        tuple(sorted(ContinuationDomains)),
        Problem.MaximumPowerDistance,
        ResourceCompletenessIdentity,
    ))


@dataclass(frozen=True)
class CompleteComponentNetPortfolioStaticContext:
    """Invocation-scoped static identity shared by exact port contracts."""

    Signal: str
    Origin: Position3
    StaticStructuralFingerprint: str


def BuildCompleteComponentNetPortfolioStaticContext(
    Problem: ComponentRoutingProblem,
    Signal: str,
) -> CompleteComponentNetPortfolioStaticContext:
    """Hash signal-static routing structure once for a contract portfolio."""
    Signal = str(Signal)
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    if Signal not in Problem.ComponentSignals or not Domains:
        raise ValueError("static portfolio context requires an owned net")
    Origin = _ComponentOrigin(Problem)
    return CompleteComponentNetPortfolioStaticContext(
        Signal=Signal,
        Origin=Origin,
        StaticStructuralFingerprint=(
            _ComponentNetPortfolioStaticStructuralFingerprint(
                Problem,
                Signal,
                Domains,
                Origin,
            )
        ),
    )


def _ComponentNetPortfolioStructuralFingerprint(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Origin: Position3,
    StaticContext: CompleteComponentNetPortfolioStaticContext | None = None,
) -> str:
    """Combine static structure with one exact physical-port contract."""
    if StaticContext is None:
        StaticContext = BuildCompleteComponentNetPortfolioStaticContext(
            Problem,
            Signal,
        )
    if StaticContext.Signal != Signal or StaticContext.Origin != Origin:
        raise ValueError("net portfolio static context identity mismatch")
    PhysicalPort = next(
        (
            Port
            for Port in (
                Problem.Interface.PhysicalPortReservations
                if Problem.Interface is not None
                else ()
            )
            if Port.Signal == Signal
        ),
        None,
    )
    ExactPortContract = (
        None
        if PhysicalPort is None
        else _PhysicalPortLocalContractFingerprint(PhysicalPort)
    )
    return _StableFingerprint((
        "component-net-translation-v4",
        StaticContext.StaticStructuralFingerprint,
        ExactPortContract,
    ))


def _TranslateAndValidateNetPortfolio(
    Variants: tuple[RoutedComponentNet, ...],
    *,
    SourceOrigin: Position3,
    TargetOrigin: Position3,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Problem: ComponentRoutingProblem,
) -> tuple[RoutedComponentNet, ...] | None:
    """Instantiate a translation-equivalent complete portfolio safely."""
    Delta = (
        TargetOrigin[0] - SourceOrigin[0],
        TargetOrigin[1] - SourceOrigin[1],
        TargetOrigin[2] - SourceOrigin[2],
    )
    ExpectedCoveredTerminals = tuple(sorted(
        Domain.Terminal for Domain in Domains
    ))
    ImmutableForeignClaims = tuple(
        Claim.Claims
        for Claim in (
            *Problem.LocalClaims,
            *Problem.ImmutableClaims,
        )
        if Claim.Signal not in Problem.ComponentSignals
    ) + tuple(
        Claims
        for ReservedSignal, Claims
        in Problem.ReservedGlobalClaimsBySignal
        if ReservedSignal != Signal
    )
    PhysicalPort = next(
        (
            Port
            for Port in (
                Problem.Interface.PhysicalPortReservations
                if Problem.Interface is not None
                else ()
            )
            if Port.Signal == Signal
        ),
        None,
    )
    Result: list[RoutedComponentNet] = []
    for Value in Variants:
        Nodes = frozenset(
            _TranslatePosition(Position, Delta)
            for Position in Value.Nodes
        )
        Edges = frozenset(
            _NormalizedEdge(
                _TranslatePosition(First, Delta),
                _TranslatePosition(Second, Delta),
            )
            for First, Second in Value.Edges
        )
        Claims = _TranslateClaims(Value.Claims, Delta)
        Repeaters = tuple(
            (_TranslatePosition(Position, Delta), Facing)
            for Position, Facing in Value.Repeaters
        )
        CoveredTerminals = tuple(sorted(
            _TranslatePosition(Position, Delta)
            for Position in Value.CoveredTerminals
        ))
        if CoveredTerminals != ExpectedCoveredTerminals:
            return None
        if (
            PhysicalPort is not None
            and not frozenset(PhysicalPort.LocalPath) <= Nodes
        ):
            return None
        if FindSelfClaimConflicts({Signal: Claims}):
            return None
        if any(
            ComponentClaimsConflict(Claims, ImmutableClaims)
            for ImmutableClaims in ImmutableForeignClaims
        ):
            return None
        if Problem.ResourceGraph is not None:
            if any(
                Problem.ResourceGraph.BuildPrimitive(First, Second)
                is None
                for First, Second in Edges
            ):
                return None
            if Problem.ResourceGraph.BuildRouteClaims(Nodes) != Claims:
                return None
        ExportedPorts = tuple(
            _TranslatePosition(Position, Delta)
            for Position in Value.ExportedPorts
        )
        NetFingerprint = _StableFingerprint((
            tuple(sorted(Nodes)),
            tuple(sorted(Edges)),
            tuple(Position for Position, _Facing in Repeaters),
            tuple(sorted(ExportedPorts)),
            tuple(sorted(Claims.WireCells)),
            tuple(sorted(Claims.SupportCells)),
            tuple(sorted(Claims.RequiredAirCells)),
            tuple(sorted(Claims.ElectricalCells)),
        ))
        Result.append(RoutedComponentNet(
            Signal=Signal,
            Root=_TranslatePosition(Value.Root, Delta),
            Nodes=Nodes,
            Edges=Edges,
            WireCells=Claims.WireCells - frozenset(
                Position for Position, _Facing in Repeaters
            ),
            SupportCells=Claims.SupportCells,
            Repeaters=Repeaters,
            Claims=Claims,
            CoveredTerminals=CoveredTerminals,
            ExportedPorts=ExportedPorts,
            NetFingerprint=NetFingerprint,
        ))
    return tuple(Result)


def _MergeClaims(
    Values: Iterable[RoutingResourceClaims],
) -> RoutingResourceClaims:
    Items = tuple(Values)
    return RoutingResourceClaims(
        WireCells=frozenset(
            Position for Value in Items for Position in Value.WireCells
        ),
        SupportCells=frozenset(
            Position for Value in Items for Position in Value.SupportCells
        ),
        RequiredAirCells=frozenset(
            Position
            for Value in Items
            for Position in Value.RequiredAirCells
        ),
        ElectricalCells=frozenset(
            Position
            for Value in Items
            for Position in Value.ElectricalCells
        ),
    )


def ComponentClaimsConflict(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> bool:
    """Return exact capacity, electrical, support, or air incompatibility."""
    # This predicate is the inner loop of exact component arc consistency.
    # Keep every ownership rule explicit: constructing temporary unions here
    # multiplies large fabric claim sets for every option pair and can consume
    # an entire component-stage deadline before the CSP starts.  Separate
    # intersections are logically identical and short-circuit without
    # allocating merged sets.
    return bool(
        First.WireCells & Second.WireCells
        or First.SupportCells & Second.WireCells
        or First.SupportCells & Second.RequiredAirCells
        or Second.SupportCells & First.WireCells
        or Second.SupportCells & First.RequiredAirCells
        or First.RequiredAirCells & Second.WireCells
        or Second.RequiredAirCells & First.WireCells
        or First.ElectricalCells & Second.WireCells
        or Second.ElectricalCells & First.WireCells
    )


def ComponentClaimsCompatibleForOwners(
    FirstOwner: str,
    First: RoutingResourceClaims,
    SecondOwner: str,
    Second: RoutingResourceClaims,
) -> bool:
    """Apply electrical rules without exempting same-net physical collisions."""
    if FirstOwner != SecondOwner:
        return not ComponentClaimsConflict(First, Second)
    return not FindSelfClaimConflicts({
        FirstOwner: _MergeClaims((First, Second)),
    })


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
) -> tuple[tuple[Position3, ...], ...]:
    """Enumerate legal component-to-assigned-global-layer port paths."""
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
                    VerticalDistance
                    + 1
                    + DefaultRedstoneRoutingTechnology.TrackPitch,
                )
            ),
        )
        for DeltaX, DeltaZ in (
            (-1, 0),
            (0, -1),
            (0, 1),
            (1, 0),
        )
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
        Minimum = tuple(min(Value[Index] for Value in (*Nodes, Start, Target)) - 2 for Index in range(3))
        Maximum = tuple(max(Value[Index] for Value in (*Nodes, Start, Target)) + 2 for Index in range(3))
        Pending = deque((Start,))
        Previous: dict[Position3, Position3 | None] = {Start: None}
        Reached: Position3 | None = None
        while Pending and Reached is None:
            Current = Pending.popleft()
            for Neighbor in sorted(ResourceGraph.Technology.NeighborPositions(Current)):
                if (
                    Neighbor in Previous
                    or any(Neighbor[Index] < Minimum[Index] or Neighbor[Index] > Maximum[Index] for Index in range(3))
                    or (Neighbor in Nodes and Neighbor not in TargetNodes)
                    or ResourceGraph.BuildPrimitive(Current, Neighbor) is None
                ):
                    continue
                Previous[Neighbor] = Current
                if Neighbor in TargetNodes:
                    Reached = Neighbor
                    break
                Pending.append(Neighbor)
        if Reached is None:
            continue
        Path = [Reached]
        while Path[-1] != Start:
            Parent = Previous[Path[-1]]
            assert Parent is not None
            Path.append(Parent)
        Path.reverse()
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
            Trunk,
            Merge,
        )
        for Trunk in Trunks
        for MergeIndex, Merge in enumerate(Trunk.Path)
        if Merge[1] == Terminal[1]
    )
    for _Distance, _Fingerprint, MergeIndex, Trunk, Merge in RankedMerges:
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
            Trunk,
            Merge,
        )
        for Trunk in Trunks
        for MergeIndex, Merge in enumerate(Trunk.Path)
    )
    for _Distance, _Fingerprint, MergeIndex, Trunk, Merge in AllMerges:
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
    ComponentGateNames = frozenset(
        getattr(TopologyComponent, "GateNames", ())
    )
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


def BuildComponentRoutingProblem(
    *,
    Placed: Any,
    Profiles: dict[str, Any],
    RawPortals: dict[
        tuple[str, Position3, int],
        tuple[PinAccessPortal, ...],
    ],
    PlacementFingerprint: str,
    LocalTemplateFingerprint: str,
    ResourceGraph: Any = None,
    MaximumWork: int = 250_000,
) -> ComponentRoutingProblem:
    """Promote complete raw portal geometry into a generic finite problem."""
    Channel = getattr(Placed, "InterClusterRoutingChannel", None)
    Fabric = BuildComponentRoutingFabric(Channel)
    SelectedClusters = tuple(sorted(
        int(Value)
        for Value in getattr(Channel, "AffectedClusters", ())
    ))
    SelectedClusterSet = frozenset(SelectedClusters)
    BoundaryRequests = tuple(
        getattr(Placed, "ClusterBoundaryLeaseRequests", ()) or ()
    )
    # A closed component owns every terminal inside its selected clusters.
    # The channel's internal signals are only the seed; all topological cut
    # crossings become explicit interface ports instead of remaining porous
    # implicit continuations.
    IncidentSignals = {
        str(Signal)
        for Signal in getattr(Channel, "AffectedSignals", ())
        if str(Signal) in Profiles
    }
    IncidentSignals.update(SelectComponentIncidentSignals(
        BoundaryRequests,
        SelectedClusters,
        Profiles,
    ))
    ComponentPairs = SelectClosedComponentOwnedTerminalPairs(
        Placed,
        Profiles,
    )
    EffectiveComponentPairs = set(ComponentPairs)
    RoutableComponentSignals = tuple(sorted(
        Signal
        for Signal in IncidentSignals
        if (
            Signal in Profiles
        )
    ))
    AllLocalClaims = tuple(
        getattr(Placed, "LocalRouteClaims", ()) or ()
    )
    ComponentSignals = RoutableComponentSignals
    Fabric = AugmentComponentRoutingFabric(
        Fabric,
        (
            Portal.Path[-1]
            for (
                Signal,
                Terminal,
                _Layer,
            ), Portals in RawPortals.items()
            if (Signal, Terminal) in ComponentPairs
            for Portal in Portals
            if Portal.Path
        ),
        ResourceGraph,
    )
    FabricNodes = frozenset(Fabric.Nodes)
    OwnedDomains = []
    AllTerminalKeys = {
        (Signal, Terminal)
        for Signal, Terminal, _Layer in RawPortals
    }
    for Signal, Terminal in sorted(AllTerminalKeys):
        Values = tuple(
            Portal
            for (CandidateSignal, CandidateTerminal, _Layer), Portals
            in sorted(RawPortals.items())
            if (
                CandidateSignal == Signal
                and CandidateTerminal == Terminal
            )
            for Portal in Portals
        )
        # Cluster ownership is topological. A foreign terminal does not
        # become component-owned merely because one of its global portal
        # paths can geometrically touch the shared fabric. Such terminals
        # remain external continuations and receive exported component ports.
        IsOwned = (Signal, Terminal) in ComponentPairs
        if not IsOwned:
            continue
        Values = tuple(
            Portal
            for Portal in Values
            if Portal.Path and Portal.Path[-1] in FabricNodes
        )
        CandidatesByFingerprint = {
            Candidate.CandidateFingerprint: Candidate
            for Candidate in map(_BuildAccessCandidate, Values)
        }
        Candidates = PruneDominatedComponentAccessCandidates(
            CandidatesByFingerprint[Fingerprint]
            for Fingerprint in sorted(CandidatesByFingerprint)
        )
        Profile = Profiles.get(Signal)
        Role = (
            "source"
            if Profile is not None and Terminal == Profile.Root
            else "target"
        )
        Domain = ComponentTerminalAccessDomain(
            Signal=Signal,
            Terminal=Terminal,
            TerminalRole=Role,
            TerminalFingerprint=_StableFingerprint((
                Role,
                len(Candidates),
                tuple(
                    Candidate.CandidateFingerprint
                    for Candidate in Candidates
                ),
            )),
            Candidates=Candidates,
            Complete=True,
        )
        OwnedDomains.append(Domain)
    OwnedDomains = list(CoalesceOwnedSignalAccessDomains(
        OwnedDomains,
        ResourceGraph=ResourceGraph,
    ))
    Fabric = BridgeDisconnectedOwnedSignalFabric(
        Fabric,
        OwnedDomains,
        ResourceGraph,
    )
    ExternalContinuationTerminals = tuple(sorted(
        (
            Signal,
            Terminal,
            (
                "source"
                if Terminal == Profiles[Signal].Root
                else "target"
            ),
        )
        for Signal in RoutableComponentSignals
        for Terminal in (
            Profiles[Signal].Root,
            *Profiles[Signal].Targets,
        )
        if (Signal, Terminal) not in EffectiveComponentPairs
    ))
    LocalClaims = tuple(
        Claim
        for Claim in AllLocalClaims
        if int(getattr(Claim, "ClusterId", -1)) in SelectedClusterSet
    )
    ImmutableClaims = tuple(
        Claim
        for Claim in AllLocalClaims
        if int(getattr(Claim, "ClusterId", -1)) not in SelectedClusterSet
    )
    Interface = BuildClosedComponentInterface(
        Channel=Channel,
        Fabric=Fabric,
        Profiles=Profiles,
        ComponentSignals=RoutableComponentSignals,
        ComponentPairs=ComponentPairs,
    )
    DomainComplete = bool(
        Interface.Complete
        and OwnedDomains
        and all(Domain.Candidates for Domain in OwnedDomains)
    )
    StructuralDomainSignature = tuple(sorted(
        (
            Domain.TerminalRole,
            len(Domain.Candidates),
            tuple(
                Candidate.CandidateFingerprint
                for Candidate in Domain.Candidates
            ),
        )
        for Domain in OwnedDomains
    ))
    GateStructure = tuple(sorted(
        (
            str(getattr(Gate, "Kind", "")),
            len(getattr(Gate, "Inputs", ())),
            len(getattr(Gate, "Outputs", ())),
        )
        for Gate in getattr(
            getattr(Placed, "Module", None),
            "Gates",
            (),
        )
    ))
    ProblemFingerprint = _StableFingerprint((
        GateStructure,
        len(SelectedClusters),
        len(ComponentSignals),
        Fabric.FabricFingerprint,
        Interface.InterfaceFingerprint,
        StructuralDomainSignature,
        len(ExternalContinuationTerminals),
        LocalTemplateFingerprint,
    ))
    BaseProblem = ComponentRoutingProblem(
        ProblemFingerprint=ProblemFingerprint,
        PlacementFingerprint=PlacementFingerprint,
        LocalTemplateFingerprint=LocalTemplateFingerprint,
        SelectedClusters=SelectedClusters,
        ComponentSignals=ComponentSignals,
        LocalClaims=LocalClaims,
        Fabric=Fabric,
        OwnedTerminalDomains=tuple(OwnedDomains),
        ExternalContinuationTerminals=ExternalContinuationTerminals,
        ForeignEscapeDomains=(),
        MaximumPowerDistance=(
            DefaultRedstoneRoutingTechnology
            .MaximumUnrefreshedDustLength
        ),
        DomainComplete=DomainComplete,
        ResourceGraph=ResourceGraph,
        MaximumWork=MaximumWork,
        ImmutableClaims=ImmutableClaims,
        ExternalContinuationDomains=(),
        Interface=Interface,
    )
    DeclaredFeedthroughSignals = (
        Interface.DeclaredFeedthroughSignals
    )
    ForeignTransitDomains = (
        BuildDeclaredComponentFeedthroughDomains(
            BaseProblem,
            Interface.Feedthroughs,
        )
        if DeclaredFeedthroughSignals
        else ()
    )
    TransitSignature = tuple(
        (
            Domain.PartitionAxis,
            Domain.PartitionFingerprint,
            len(Domain.Candidates),
            tuple(
                (
                    _RelativeGeometry(Candidate.Nodes),
                    _RelativeGeometry(
                        Position
                        for Position, _Facing
                        in Candidate.Repeaters
                    ),
                )
                for Candidate in Domain.Candidates
            ),
        )
        for Domain in ForeignTransitDomains
    )
    return replace(
        BaseProblem,
        ProblemFingerprint=_StableFingerprint((
            ProblemFingerprint,
            TransitSignature,
        )),
        ForeignTransitDomains=ForeignTransitDomains,
        DomainComplete=bool(
            BaseProblem.DomainComplete
            and frozenset(
                Domain.Signal for Domain in ForeignTransitDomains
            ) == DeclaredFeedthroughSignals
            and all(
                Domain.Complete and Domain.Candidates
                for Domain in ForeignTransitDomains
            )
        ),
    )


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


def BuildComponentFabricAdjacency(
    Fabric: ComponentRoutingFabric,
) -> dict[Position3, set[Position3]]:
    """Build one reusable adjacency index for component eligibility work."""
    return _BuildAdjacency(Fabric.Edges)


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
            if FindSelfClaimConflicts({Signal: Claims}):
                continue
            if _PlanTreeRepeaters(
                FrozenNodes,
                frozenset(Edges),
                Root,
                Problem.MaximumPowerDistance,
                SubproblemCache=TreeRepeaterSubproblemCache,
                CacheStatistics=TreeRepeaterCacheStatistics,
            ) is not None:
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


def _RepeaterFacing(
    Current: Position3,
    Next: Position3,
) -> str | None:
    Delta = (Next[0] - Current[0], Next[2] - Current[2])
    return {
        (1, 0): "west",
        (-1, 0): "east",
        (0, 1): "north",
        (0, -1): "south",
    }.get(Delta)


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

    def SubtreeFingerprint(Node: Position3) -> str:
        Cached = SubtreeFingerprintByNode.get(Node)
        if Cached is not None:
            return Cached
        Result = _StableFingerprint((
            Node,
            Parents[Node],
            tuple(
                SubtreeFingerprint(Child)
                for Child in Children.get(Node, ())
            ),
        ))
        SubtreeFingerprintByNode[Node] = Result
        return Result

    Memo: dict[
        tuple[Position3, int],
        tuple[tuple[Position3, str], ...] | None,
    ] = {}

    def Solve(
        Node: Position3,
        Distance: int,
    ) -> tuple[tuple[Position3, str], ...] | None:
        Key = Node, Distance
        if Key in Memo:
            return Memo[Key]
        SharedKey = (
            MaximumDistance,
            Distance,
            SubtreeFingerprint(Node),
        )
        if (
            SubproblemCache is not None
            and SharedKey in SubproblemCache
        ):
            if CacheStatistics is not None:
                CacheStatistics["HitCount"] = (
                    CacheStatistics.get("HitCount", 0) + 1
                )
            Result = SubproblemCache[SharedKey]
            Memo[Key] = Result
            return Result
        if CacheStatistics is not None:
            CacheStatistics["MissCount"] = (
                CacheStatistics.get("MissCount", 0) + 1
            )
        Options: list[tuple[tuple[Position3, str], ...]] = []
        if Distance <= MaximumDistance:
            ChildPlans = []
            for Child in Children.get(Node, ()):
                Plan = Solve(Child, Distance + 1)
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
                    _RepeaterFacing(Node, Child)
                    if Incoming == Outgoing
                    else None
                )
                if Facing is not None:
                    ChildPlan = Solve(Child, 1)
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
        if SubproblemCache is not None:
            SubproblemCache[SharedKey] = Result
        return Result

    return Solve(Root, 0)


def _BuildNetVariant(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Candidates: tuple[ComponentTerminalAccessCandidate, ...],
    ExternalEgressPath: tuple[Position3, ...] = (),
    RejectionCounts: dict[str, int] | None = None,
    ImmutableConflictSignals: set[str] | None = None,
    FabricAdjacency: dict[
        Position3, set[Position3]
    ] | None = None,
    FabricParentCache: dict[
        Position3,
        dict[Position3, Position3 | None],
    ] | None = None,
    ImmutableAccessConflictCache: dict[
        tuple[str, str, tuple[Position3, ...]],
        frozenset[str],
    ] | None = None,
    LocalClaimsBySignal: dict[
        str, tuple[Any, ...]
    ] | None = None,
    NetVariantTopologyCache: dict[
        tuple[
            str,
            frozenset[Position3],
            frozenset[RoutingEdge],
            tuple[Position3, ...],
        ],
        RoutedComponentNet | None,
    ] | None = None,
    RouteClaimsCache: dict[
        frozenset[Position3],
        RoutingResourceClaims,
    ] | None = None,
    TreeRepeaterSubproblemCache: dict[
        tuple[int, int, str],
        tuple[tuple[Position3, str], ...] | None,
    ] | None = None,
    TreeRepeaterCacheStatistics: dict[str, int] | None = None,
    PrecomputedFabricSubtree: tuple[
        frozenset[Position3],
        frozenset[RoutingEdge],
    ] | None = None,
    ReservedPortContractConflictSignals: set[str] | None = None,
    ReservedGlobalRouteConflictSignals: set[str] | None = None,
) -> RoutedComponentNet | None:
    def Reject(Reason: str) -> None:
        if RejectionCounts is not None:
            RejectionCounts[Reason] = (
                RejectionCounts.get(Reason, 0) + 1
            )

    ImmutableForeignClaims = tuple(
        (Claim.Signal, Claim.Claims)
        for Claim in Problem.ImmutableClaims
        if Claim.Signal not in Problem.ComponentSignals
    ) + tuple(
        (ReservedSignal, Claims)
        for ReservedSignal, Claims
        in Problem.ReservedGlobalClaimsBySignal
        if ReservedSignal != Signal
    )
    AccessClaimsContextFingerprint = _StableFingerprint(tuple(
        sorted(
            (
                str(ClaimSignal),
                tuple(sorted(map(str, Claims.ResourceIds))),
            )
            for ClaimSignal, Claims in ImmutableForeignClaims
        )
    ))
    ReservedClaimsBySignal = {
        str(ReservedSignal): Claims
        for ReservedSignal, Claims
        in Problem.ReservedGlobalClaimsBySignal
        if str(ReservedSignal) != Signal
    }
    PhysicalPortClaimsBySignal = {
        str(Port.Signal): Port.Claims
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
    }

    def RecordReservedBlockerProvenance(
        Claims: RoutingResourceClaims,
        Blockers: Iterable[str],
    ) -> None:
        for Blocker in Blockers:
            if Blocker not in ReservedClaimsBySignal:
                continue
            PortClaims = PhysicalPortClaimsBySignal.get(Blocker)
            if (
                PortClaims is not None
                and ComponentClaimsConflict(Claims, PortClaims)
            ):
                if ReservedPortContractConflictSignals is not None:
                    ReservedPortContractConflictSignals.add(Blocker)
            elif ReservedGlobalRouteConflictSignals is not None:
                ReservedGlobalRouteConflictSignals.add(Blocker)
    BlockingImmutableSignals: set[str] = set()
    for Candidate in Candidates:
        CacheKey = (
            AccessClaimsContextFingerprint,
            Signal,
            Candidate.Path,
        )
        CandidateBlockers = (
            ImmutableAccessConflictCache.get(CacheKey)
            if ImmutableAccessConflictCache is not None
            else None
        )
        if CandidateBlockers is None:
            CandidateBlockers = frozenset(
                ClaimSignal
                for ClaimSignal, Claims in ImmutableForeignClaims
                if ComponentClaimsConflict(
                    Candidate.Claims,
                    Claims,
                )
            )
            if ImmutableAccessConflictCache is not None:
                ImmutableAccessConflictCache[
                    CacheKey
                ] = CandidateBlockers
        BlockingImmutableSignals.update(CandidateBlockers)
        RecordReservedBlockerProvenance(
            Candidate.Claims,
            CandidateBlockers,
        )
    if BlockingImmutableSignals:
        Reject("immutable-local-access-conflict")
        if ImmutableConflictSignals is not None:
            ImmutableConflictSignals.update(
                BlockingImmutableSignals
            )
        return None
    Subtree = (
        PrecomputedFabricSubtree
        if PrecomputedFabricSubtree is not None
        else _UniqueFabricSubtree(
            Problem.Fabric,
            (
                *(
                    Candidate.Attachment
                    for Candidate in Candidates
                ),
                *((ExternalEgressPath[0],)
                  if ExternalEgressPath else ()),
            ),
            Adjacency=FabricAdjacency,
            ParentCache=FabricParentCache,
        )
    )
    if Subtree is None:
        Reject("disconnected-fabric-attachments")
        return None
    FabricNodes, FabricEdges = Subtree
    Nodes = set(FabricNodes)
    Edges = set(FabricEdges)
    for Candidate in Candidates:
        Nodes.update(Candidate.Path)
        Edges.update(
            _NormalizedEdge(First, Second)
            for First, Second in zip(
                Candidate.Path,
                Candidate.Path[1:],
            )
        )
    LocalClaims = (
        LocalClaimsBySignal.get(Signal, ())
        if LocalClaimsBySignal is not None
        else tuple(
            Claim
            for Claim in Problem.LocalClaims
            if Claim.Signal == Signal
        )
    )
    for Claim in LocalClaims:
        Nodes.update(Claim.Nodes)
        Edges.update(
            _NormalizedEdge(*Edge) for Edge in Claim.Edges
        )
    External = tuple(
        Value
        for Value in Problem.ExternalContinuationTerminals
        if Value[0] == Signal
    )
    ExportedPorts: tuple[Position3, ...] = ()
    if External:
        if not ExternalEgressPath:
            Reject("missing-external-egress")
            return None
        Nodes.update(ExternalEgressPath)
        Edges.update(
            _NormalizedEdge(First, Second)
            for First, Second in zip(
                ExternalEgressPath,
                ExternalEgressPath[1:],
            )
        )
        ExportedPorts = (ExternalEgressPath[-1],)
    TopologyCacheKey = (
        Signal,
        frozenset(Nodes),
        frozenset(Edges),
        tuple(ExportedPorts),
    )
    if (
        NetVariantTopologyCache is not None
        and TopologyCacheKey in NetVariantTopologyCache
    ):
        CachedNet = NetVariantTopologyCache[TopologyCacheKey]
        if CachedNet is None:
            return None
        CachedBlockers = frozenset(
            ClaimSignal
            for ClaimSignal, ImmutableClaims
            in ImmutableForeignClaims
            if ComponentClaimsConflict(
                CachedNet.Claims,
                ImmutableClaims,
            )
        )
        if CachedBlockers:
            RecordReservedBlockerProvenance(
                CachedNet.Claims,
                CachedBlockers,
            )
            Reject("immutable-route-conflict")
            if ImmutableConflictSignals is not None:
                ImmutableConflictSignals.update(CachedBlockers)
            return None
        return CachedNet
    SourceIndexes = tuple(
        Index
        for Index, Domain in enumerate(Domains)
        if Domain.TerminalRole == "source"
    )
    RootIndex = SourceIndexes[0] if SourceIndexes else 0
    Root = Domains[RootIndex].Terminal
    if Root not in Nodes:
        Root = Candidates[RootIndex].Path[0]
    if (
        ExportedPorts
        and any(Role == "source" for _Signal, _Terminal, Role in External)
    ):
        Root = ExportedPorts[0]
    Repeaters = _PlanTreeRepeaters(
        frozenset(Nodes),
        frozenset(Edges),
        Root,
        Problem.MaximumPowerDistance,
        SubproblemCache=TreeRepeaterSubproblemCache,
        CacheStatistics=TreeRepeaterCacheStatistics,
    )
    if Repeaters is None:
        Reject("power-or-tree-connectivity")
        if NetVariantTopologyCache is not None:
            NetVariantTopologyCache[TopologyCacheKey] = None
        return None
    RepeatersByPosition = dict(Repeaters)
    WireCells = frozenset(Nodes) - frozenset(RepeatersByPosition)
    Supports = frozenset(
        (X, Y - 1, Z) for X, Y, Z in Nodes
    )
    FrozenNodes = frozenset(Nodes)
    Claims = (
        RouteClaimsCache.get(FrozenNodes)
        if RouteClaimsCache is not None
        else None
    )
    if Claims is None:
        Claims = (
            Problem.ResourceGraph.BuildRouteClaims(FrozenNodes)
            if Problem.ResourceGraph is not None
            else RoutingResourceClaims(
            WireCells=frozenset(Nodes),
            SupportCells=Supports,
            RequiredAirCells=frozenset(),
            ElectricalCells=frozenset(
                DefaultRedstoneRoutingTechnology
                .BuildElectricalExclusions(set(Nodes))
            ),
            )
        )
        if RouteClaimsCache is not None:
            RouteClaimsCache[FrozenNodes] = Claims
    if FindSelfClaimConflicts({Signal: Claims}):
        Reject("self-claim-conflict")
        return None
    RouteBlockers = frozenset(
        ClaimSignal
        for ClaimSignal, ImmutableClaims
        in ImmutableForeignClaims
        if ComponentClaimsConflict(Claims, ImmutableClaims)
    )
    if RouteBlockers:
        RecordReservedBlockerProvenance(Claims, RouteBlockers)
        Reject("immutable-route-conflict")
        if ImmutableConflictSignals is not None:
            ImmutableConflictSignals.update(RouteBlockers)
        return None
    if (
        Problem.ResourceGraph is not None
        and any(
            Problem.ResourceGraph.BuildPrimitive(First, Second) is None
            for First, Second in Edges
        )
    ):
        Reject("illegal-routing-primitive")
        return None
    Supports = Claims.SupportCells
    WireCells = Claims.WireCells - frozenset(RepeatersByPosition)
    CoveredTerminals = tuple(sorted(
        Domain.Terminal for Domain in Domains
    ))
    NetFingerprint = _StableFingerprint((
        tuple(sorted(Nodes)),
        tuple(sorted(Edges)),
        tuple(Position for Position, _Facing in Repeaters),
        tuple(sorted(ExportedPorts)),
        tuple(sorted(Claims.WireCells)),
        tuple(sorted(Claims.SupportCells)),
        tuple(sorted(Claims.RequiredAirCells)),
        tuple(sorted(Claims.ElectricalCells)),
    ))
    Result = RoutedComponentNet(
        Signal=Signal,
        Root=Root,
        Nodes=frozenset(Nodes),
        Edges=frozenset(Edges),
        WireCells=WireCells,
        SupportCells=Supports,
        Repeaters=Repeaters,
        Claims=Claims,
        CoveredTerminals=CoveredTerminals,
        ExportedPorts=ExportedPorts,
        NetFingerprint=NetFingerprint,
    )
    if NetVariantTopologyCache is not None:
        NetVariantTopologyCache[TopologyCacheKey] = Result
    return Result


def _BuildCanonicalAccessCombinationKey(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Candidates: tuple[ComponentTerminalAccessCandidate, ...],
    ExternalEgressPath: tuple[Position3, ...],
    FabricComponentIndex: int,
    FabricSubtree: tuple[
        frozenset[Position3],
        frozenset[RoutingEdge],
    ],
    LocalClaims: tuple[Any, ...] = (),
) -> tuple[object, ...]:
    """Identify one power-relevant physical access state before building it."""
    FabricNodes, FabricEdges = FabricSubtree
    Nodes = set(FabricNodes)
    Edges = set(FabricEdges)
    for Candidate in Candidates:
        Nodes.update(Candidate.Path)
        Edges.update(
            _NormalizedEdge(First, Second)
            for First, Second in zip(
                Candidate.Path,
                Candidate.Path[1:],
            )
        )
    for Claim in LocalClaims:
        Nodes.update(Claim.Nodes)
        Edges.update(
            _NormalizedEdge(*Edge) for Edge in Claim.Edges
        )
    External = tuple(
        Value
        for Value in Problem.ExternalContinuationTerminals
        if Value[0] == Signal
    )
    ExportedPorts: tuple[Position3, ...] = ()
    if ExternalEgressPath:
        Nodes.update(ExternalEgressPath)
        Edges.update(
            _NormalizedEdge(First, Second)
            for First, Second in zip(
                ExternalEgressPath,
                ExternalEgressPath[1:],
            )
        )
        if External:
            ExportedPorts = (ExternalEgressPath[-1],)
    SourceIndexes = tuple(
        Index
        for Index, Domain in enumerate(Domains)
        if Domain.TerminalRole == "source"
    )
    RootIndex = SourceIndexes[0] if SourceIndexes else 0
    Root = Domains[RootIndex].Terminal
    if Root not in Nodes:
        Root = Candidates[RootIndex].Path[0]
    if (
        ExportedPorts
        and any(Role == "source" for _Signal, _Terminal, Role in External)
    ):
        Root = ExportedPorts[0]
    return (
        "canonical-component-access-state-v1",
        Problem.Fabric.FabricFingerprint,
        int(FabricComponentIndex),
        frozenset(FabricNodes),
        frozenset(FabricEdges),
        tuple(
            _ClaimsFingerprint(Candidate.Claims)
            for Candidate in Candidates
        ),
        frozenset(Nodes),
        frozenset(Edges),
        Root,
        int(Problem.MaximumPowerDistance),
        tuple(ExternalEgressPath),
        ExportedPorts,
    )


@dataclass(frozen=True)
class ExactComponentPortRealizabilityResult:
    """Powered single-port proof for one immutable physical contract."""

    Realizable: bool
    ContractFingerprint: str
    NetFingerprint: str = ""
    Detail: str = ""
    Diagnostics: dict[str, object] | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Realizable": self.Realizable,
            "ContractFingerprint": self.ContractFingerprint,
            "NetFingerprint": self.NetFingerprint,
            "Detail": self.Detail,
            "Diagnostics": dict(self.Diagnostics or {}),
        }


@dataclass(frozen=True)
class CompleteOpposingNetAccessPairResult:
    """Typed feasibility certificate for one exact local-contract pair."""

    Status: str
    Complete: bool
    Feasible: bool | None
    DomainFingerprint: str
    ProofFingerprint: str
    CurrentSignal: str
    CompleteSignal: str
    CurrentLocalContractFingerprint: str
    CompleteLocalContractFingerprint: str
    SupportingCompleteVariantFingerprints: tuple[str, ...] = ()
    ExpansionCount: int = 0
    Detail: str = ""
    Diagnostics: dict[str, object] | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Status": self.Status,
            "Complete": self.Complete,
            "Feasible": self.Feasible,
            "DomainFingerprint": self.DomainFingerprint,
            "ProofFingerprint": self.ProofFingerprint,
            "CurrentSignal": self.CurrentSignal,
            "CompleteSignal": self.CompleteSignal,
            "CurrentLocalContractFingerprint": (
                self.CurrentLocalContractFingerprint
            ),
            "CompleteLocalContractFingerprint": (
                self.CompleteLocalContractFingerprint
            ),
            "SupportingCompleteVariantFingerprints": list(
                self.SupportingCompleteVariantFingerprints
            ),
            "ExpansionCount": self.ExpansionCount,
            "Detail": self.Detail,
            "Diagnostics": dict(self.Diagnostics or {}),
        }


@dataclass(frozen=True)
class CompleteOpposingNetAccessRowContext:
    """Invariant fabric cuts for one complete opposing-net portfolio."""

    FabricFingerprint: str
    CompleteVariantFingerprints: tuple[str, ...]
    ComponentByNodeByVariant: tuple[
        tuple[str, tuple[tuple[Position3, int], ...]], ...
    ]
    ComponentMapByVariant: dict[str, dict[Position3, int]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    CurrentSignal: str = ""
    CompleteSignal: str = ""
    CurrentAccessDomainFingerprint: str = ""
    CompatibleComponentByCandidateFingerprintByVariant: dict[
        str, dict[str, int]
    ] = field(default_factory=dict, compare=False, repr=False)


def _OpposingRowCurrentAccessDomainFingerprint(
    Problem: ComponentRoutingProblem,
    Signal: str,
) -> str:
    Signal = str(Signal)
    if not Signal:
        return ""
    Origin = _ComponentOrigin(Problem)
    ForeignClaims = tuple(
        Claim.Claims
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if Claim.Signal not in Problem.ComponentSignals
    )
    return _StableFingerprint((
        "opposing-current-access-domain-v1",
        Signal,
        tuple(sorted(
            (
                Domain.TerminalRole,
                Domain.TerminalFingerprint,
                bool(getattr(Domain, "Complete", True)),
                tuple(sorted(
                    (
                        Candidate.CandidateFingerprint,
                        _NormalizePosition(Candidate.Attachment, Origin),
                        _NormalizeClaims(Candidate.Claims, Origin),
                    )
                    for Candidate in Domain.Candidates
                    if not any(
                        ComponentClaimsConflict(Candidate.Claims, Claims)
                        for Claims in ForeignClaims
                    )
                )),
            )
            for Domain in Problem.OwnedTerminalDomains
            if Domain.Signal == Signal
        )),
    ))


def BuildCompleteOpposingNetAccessRowContext(
    Problem: ComponentRoutingProblem,
    CompleteVariants: tuple[RoutedComponentNet, ...],
    *,
    CurrentSignal: str = "",
    CompleteSignal: str = "",
) -> CompleteOpposingNetAccessRowContext:
    """Precompute each opposing variant's surviving fabric components."""
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    if bool(CurrentSignal) != bool(CompleteSignal) or (
        CurrentSignal and CurrentSignal == CompleteSignal
    ):
        raise ValueError("row support index requires two distinct signals")
    FabricAdjacency = BuildComponentFabricAdjacency(Problem.Fabric)
    SingletonFabricClaims = {
        Node: (
            Problem.ResourceGraph.BuildRouteClaims(frozenset((Node,)))
            if Problem.ResourceGraph is not None
            else RoutingResourceClaims(
                WireCells=frozenset((Node,)),
                SupportCells=frozenset(((Node[0], Node[1] - 1, Node[2]),)),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions({Node})
                ),
            )
        )
        for Node in Problem.Fabric.Nodes
    }
    CurrentAccessDomainFingerprint = (
        _OpposingRowCurrentAccessDomainFingerprint(
            Problem,
            CurrentSignal,
        )
    )
    ComponentMaps = []
    for Variant in sorted(
        CompleteVariants,
        key=lambda Value: Value.NetFingerprint,
    ):
        BlockedNodes = frozenset(
            Node
            for Node, Claims in SingletonFabricClaims.items()
            if ComponentClaimsConflict(Claims, Variant.Claims)
        )
        AllowedNodes = frozenset(Problem.Fabric.Nodes) - BlockedNodes
        ComponentByNode: dict[Position3, int] = {}
        for Start in sorted(AllowedNodes):
            if Start in ComponentByNode:
                continue
            ComponentIndex = len(set(ComponentByNode.values()))
            Pending = [Start]
            ComponentByNode[Start] = ComponentIndex
            while Pending:
                Node = Pending.pop()
                for Neighbor in FabricAdjacency.get(Node, ()):
                    if (
                        Neighbor in AllowedNodes
                        and Neighbor not in ComponentByNode
                    ):
                        ComponentByNode[Neighbor] = ComponentIndex
                        Pending.append(Neighbor)
        ComponentMaps.append((
            Variant.NetFingerprint,
            tuple(sorted(ComponentByNode.items())),
        ))
    return CompleteOpposingNetAccessRowContext(
        FabricFingerprint=Problem.Fabric.FabricFingerprint,
        CompleteVariantFingerprints=tuple(
            Variant.NetFingerprint
            for Variant in sorted(
                CompleteVariants,
                key=lambda Value: Value.NetFingerprint,
            )
        ),
        ComponentByNodeByVariant=tuple(ComponentMaps),
        ComponentMapByVariant={
            Fingerprint: dict(Values)
            for Fingerprint, Values in ComponentMaps
        },
        CurrentSignal=CurrentSignal,
        CompleteSignal=CompleteSignal,
        CurrentAccessDomainFingerprint=CurrentAccessDomainFingerprint,
        CompatibleComponentByCandidateFingerprintByVariant={},
    )


@dataclass(frozen=True)
class CompleteOpposingNetAccessContractRowResult:
    """Exact pair results computed by one shared opposing-variant scan."""

    ResultsByCurrentContract: tuple[
        tuple[str, CompleteOpposingNetAccessPairResult], ...
    ]
    AccessSignatureCount: int
    VariantScanCount: int
    SignaturePairCheckCount: int
    AccessPreparationSeconds: float = 0.0
    VariantScanSeconds: float = 0.0

    @property
    def Results(self) -> dict[str, CompleteOpposingNetAccessPairResult]:
        return dict(self.ResultsByCurrentContract)


@dataclass(frozen=True)
class CompleteOpposingNetAccessContractDomain:
    """Current-side access facts shared by every opposing contract row."""

    CurrentSignal: str
    FabricFingerprint: str
    ResourceIdentityFingerprint: str
    CurrentAccessDomainFingerprint: str
    CurrentContractDomainFingerprint: str
    DomainIndexFingerprint: str
    CurrentContractFingerprints: tuple[str, ...]
    PortObjectIdentities: tuple[tuple[str, int], ...]
    SelectionKeysBySignatureIndex: tuple[tuple[str, ...], ...]
    CanonicalAccessSignatures: tuple[
        tuple[str, tuple[object, ...]], ...
    ]
    SignatureIndexByCurrentContract: tuple[
        tuple[str, int], ...
    ]
    CandidateDomainsByCurrentContract: tuple[
        tuple[
            str,
            tuple[tuple[ComponentTerminalAccessCandidate, ...], ...],
        ], ...
    ]
    ValidatedProblemIdentityObjects: list[tuple[object, ...]] = field(
        default_factory=list,
        compare=False,
        repr=False,
    )

    @property
    def SignaturesByCurrentContract(
        self,
    ) -> tuple[tuple[str, tuple[object, ...]], ...]:
        """Expand the compact index only for the row evaluator.

        The large effective-access signature is stored once per distinct
        candidate selection.  Exact local contracts retain separate entries
        and proof identities while sharing that immutable access fact.
        """
        Signatures = tuple(
            Signature for _Fingerprint, Signature
            in self.CanonicalAccessSignatures
        )
        return tuple(
            (Contract, Signatures[Index])
            for Contract, Index in self.SignatureIndexByCurrentContract
        )


def _OpposingNetResourceIdentityFingerprint(
    Problem: ComponentRoutingProblem,
) -> str:
    Origin = _ComponentOrigin(Problem)
    ResourceGraph = Problem.ResourceGraph
    Technology = getattr(ResourceGraph, "Technology", None)
    return _StableFingerprint((
        "opposing-net-resource-domain-v1",
        Problem.Fabric.FabricFingerprint,
        getattr(ResourceGraph, "GraphVersion", None),
        type(Technology).__qualname__,
        getattr(Technology, "TechnologyVersion", None),
        repr(Technology),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(ResourceGraph, "ActualBlocks", ())
        )),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(ResourceGraph, "ElectricalBlocks", ())
        )),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(ResourceGraph, "SolidBlocks", ())
        )),
    ))


def _EffectiveOpposingNetAccessCandidateDomains(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Port: Any,
) -> tuple[tuple[ComponentTerminalAccessCandidate, ...], ...]:
    CandidateFingerprints = frozenset(
        getattr(Port, "OwnedCandidateFingerprints", ())
    )
    ImmutableForeignClaims = tuple(
        Claim.Claims
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if Claim.Signal not in Problem.ComponentSignals
    )
    return tuple(
        tuple(
            Candidate
            for Candidate in Domain.Candidates
            if (
                (
                    not CandidateFingerprints
                    or Candidate.CandidateFingerprint
                    in CandidateFingerprints
                )
                and not any(
                    ComponentClaimsConflict(Candidate.Claims, Claims)
                    for Claims in ImmutableForeignClaims
                )
            )
        )
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )


def BuildOpposingNetEffectiveAccessSignature(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Port: Any,
) -> tuple[object, ...]:
    """Return exactly the current-side facts consumed by the pair predicate.

    A seam's LocalPath remains in its exact local-contract and proof-domain
    identity.  The relaxed predicate itself consumes only terminal identity,
    candidate attachment, and all four physical claim sets, so contracts that
    differ only outside those facts may safely share computation but not proof.
    """
    CandidateDomains = _EffectiveOpposingNetAccessCandidateDomains(
        Problem,
        str(Signal),
        Port,
    )
    return _BuildOpposingNetEffectiveAccessSignatureFromDomains(
        Problem,
        str(Signal),
        CandidateDomains,
    )


def _BuildOpposingNetEffectiveAccessSignatureFromDomains(
    Problem: ComponentRoutingProblem,
    Signal: str,
    CandidateDomains: tuple[
        tuple[ComponentTerminalAccessCandidate, ...], ...
    ],
) -> tuple[object, ...]:
    """Fingerprint one already-filtered effective access domain."""
    Origin = _ComponentOrigin(Problem)
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == str(Signal)
    )
    return tuple(
        (
            Domain.TerminalRole,
            Domain.TerminalFingerprint,
            bool(getattr(Domain, "Complete", True)),
            tuple(sorted({
                (
                    _NormalizePosition(Candidate.Attachment, Origin),
                    _NormalizeClaims(Candidate.Claims, Origin),
                )
                for Candidate in Candidates
            })),
        )
        for Domain, Candidates in zip(Domains, CandidateDomains)
    )


def _CanonicalOpposingNetAccessSignatureFingerprint(
    Signature: tuple[object, ...],
) -> str:
    """Return a full digest for a shared effective-access signature."""
    return sha256(repr((
        "opposing-effective-access-signature-v1",
        Signature,
    )).encode("utf-8")).hexdigest()


def _BuildOpposingNetCurrentContractDomainFingerprint(
    CurrentSignal: str,
    OrderedCurrentPorts: tuple[tuple[str, Any], ...],
    SelectionKeysBySignatureIndex: tuple[tuple[str, ...], ...],
    CanonicalAccessSignatures: tuple[
        tuple[str, tuple[object, ...]], ...
    ],
    SignatureIndexByCurrentContract: tuple[tuple[str, int], ...],
    Origin: Position3,
) -> str:
    """Hash each large access signature once and exact contracts separately."""
    SignatureIndexByContract = dict(SignatureIndexByCurrentContract)
    return _StableFingerprint((
        "opposing-current-contract-domain-v2",
        str(CurrentSignal),
        tuple(
            (
                Index,
                SelectionKeysBySignatureIndex[Index],
                SignatureFingerprint,
            )
            for Index, (SignatureFingerprint, _Signature)
            in enumerate(CanonicalAccessSignatures)
        ),
        tuple(
            (
                Contract,
                getattr(Port, "Direction", ""),
                getattr(Port, "FabricDomainFingerprint", ""),
                tuple(sorted(getattr(
                    Port,
                    "OwnedTerminalFingerprints",
                    (),
                ))),
                tuple(sorted(getattr(
                    Port,
                    "OwnedCandidateFingerprints",
                    (),
                ))),
                tuple(
                    _NormalizePosition(Position, Origin)
                    for Position in getattr(Port, "LocalPath", ())
                ),
                SignatureIndexByContract[Contract],
                CanonicalAccessSignatures[
                    SignatureIndexByContract[Contract]
                ][0],
            )
            for Contract, Port in OrderedCurrentPorts
        ),
    ))


def BuildCompleteOpposingNetAccessContractDomain(
    Problem: ComponentRoutingProblem,
    CurrentSignal: str,
    CurrentPortsByContract: Mapping[str, Any],
) -> CompleteOpposingNetAccessContractDomain:
    """Precompute the invariant current-side domain for a row portfolio."""
    CurrentSignal = str(CurrentSignal)
    OrderedCurrentPorts = tuple(sorted(CurrentPortsByContract.items()))
    if not CurrentSignal or not OrderedCurrentPorts:
        raise ValueError("opposing-net access contract domain is empty")
    for Contract, Port in OrderedCurrentPorts:
        if (
            str(getattr(Port, "Signal", "")) != CurrentSignal
            or _PhysicalPortLocalContractFingerprint(Port) != Contract
        ):
            raise ValueError(
                "opposing-net access contract domain identity mismatch"
            )
    CandidateDomainsBySelection = {}
    SignatureBySelection = {}
    SelectionKeyByContract = {}
    CandidateDomains = []
    for Contract, Port in OrderedCurrentPorts:
        SelectionKey = tuple(sorted(
            getattr(Port, "OwnedCandidateFingerprints", ())
        ))
        SelectionKeyByContract[Contract] = SelectionKey
        SelectedDomains = CandidateDomainsBySelection.get(SelectionKey)
        if SelectedDomains is None:
            SelectedDomains = _EffectiveOpposingNetAccessCandidateDomains(
                Problem,
                CurrentSignal,
                Port,
            )
            CandidateDomainsBySelection[SelectionKey] = SelectedDomains
        CandidateDomains.append((Contract, SelectedDomains))
        Signature = SignatureBySelection.get(SelectionKey)
        if Signature is None:
            Signature = (
                _BuildOpposingNetEffectiveAccessSignatureFromDomains(
                    Problem,
                    CurrentSignal,
                    SelectedDomains,
                )
            )
            SignatureBySelection[SelectionKey] = Signature
    CandidateDomains = tuple(CandidateDomains)
    CandidateDomainsByContract = dict(CandidateDomains)
    SelectionKeysBySignatureIndex = tuple(sorted(SignatureBySelection))
    SignatureIndexBySelection = {
        SelectionKey: Index
        for Index, SelectionKey
        in enumerate(SelectionKeysBySignatureIndex)
    }
    CanonicalAccessSignatures = tuple(
        (
            _CanonicalOpposingNetAccessSignatureFingerprint(
                SignatureBySelection[SelectionKey]
            ),
            SignatureBySelection[SelectionKey],
        )
        for SelectionKey in SelectionKeysBySignatureIndex
    )
    SignatureIndexByCurrentContract = tuple(
        (
            Contract,
            SignatureIndexBySelection[SelectionKeyByContract[Contract]],
        )
        for Contract, _Port in OrderedCurrentPorts
    )
    Origin = _ComponentOrigin(Problem)
    CurrentContractDomainFingerprint = (
        _BuildOpposingNetCurrentContractDomainFingerprint(
            CurrentSignal,
            OrderedCurrentPorts,
            SelectionKeysBySignatureIndex,
            CanonicalAccessSignatures,
            SignatureIndexByCurrentContract,
            Origin,
        )
    )
    FabricFingerprint = Problem.Fabric.FabricFingerprint
    ResourceIdentityFingerprint = _OpposingNetResourceIdentityFingerprint(
        Problem
    )
    CurrentAccessDomainFingerprint = (
        _OpposingRowCurrentAccessDomainFingerprint(
            Problem,
            CurrentSignal,
        )
    )
    DomainIndexFingerprint = _StableFingerprint((
        "opposing-current-access-domain-index-v1",
        FabricFingerprint,
        ResourceIdentityFingerprint,
        CurrentAccessDomainFingerprint,
        CurrentContractDomainFingerprint,
    ))
    return CompleteOpposingNetAccessContractDomain(
        CurrentSignal=CurrentSignal,
        FabricFingerprint=FabricFingerprint,
        ResourceIdentityFingerprint=ResourceIdentityFingerprint,
        CurrentAccessDomainFingerprint=CurrentAccessDomainFingerprint,
        CurrentContractDomainFingerprint=CurrentContractDomainFingerprint,
        DomainIndexFingerprint=DomainIndexFingerprint,
        CurrentContractFingerprints=tuple(
            Contract for Contract, _Port in OrderedCurrentPorts
        ),
        PortObjectIdentities=tuple(
            (Contract, id(Port)) for Contract, Port in OrderedCurrentPorts
        ),
        SelectionKeysBySignatureIndex=SelectionKeysBySignatureIndex,
        CanonicalAccessSignatures=CanonicalAccessSignatures,
        SignatureIndexByCurrentContract=(
            SignatureIndexByCurrentContract
        ),
        CandidateDomainsByCurrentContract=tuple(
            (
                Contract,
                CandidateDomainsByContract[Contract],
            )
            for Contract, _Port in OrderedCurrentPorts
        ),
    )


def EvaluateCompleteOpposingNetAccessContractRow(
    Problem: ComponentRoutingProblem,
    *,
    CurrentSignal: str,
    CompleteSignal: str,
    CurrentPortsByContract: Mapping[str, Any],
    CompleteLocalContractFingerprint: str,
    CompleteVariants: tuple[RoutedComponentNet, ...],
    CompleteVariantDomainComplete: bool,
    DeadlineSeconds: float | None,
    DomainFingerprintsByCurrentContract: Mapping[str, str],
    ContractDomain: CompleteOpposingNetAccessContractDomain | None = None,
    RowContext: CompleteOpposingNetAccessRowContext | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> CompleteOpposingNetAccessContractRowResult:
    """Evaluate one complete-contract row with one outer variant scan."""
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    if (
        not CurrentSignal
        or not CompleteSignal
        or CurrentSignal == CompleteSignal
        or not CurrentPortsByContract
    ):
        raise ValueError(
            "opposing-net access row requires two signals and current ports"
        )
    PortsBySignal = {
        str(Port.Signal): Port
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
    }
    CompletePort = PortsBySignal.get(CompleteSignal)
    if (
        CompletePort is None
        or _PhysicalPortLocalContractFingerprint(CompletePort)
        != CompleteLocalContractFingerprint
    ):
        raise ValueError(
            "opposing-net access row complete contract fingerprint mismatch"
        )
    OrderedCurrentPorts = tuple(sorted(CurrentPortsByContract.items()))
    if set(DomainFingerprintsByCurrentContract) != {
        Contract for Contract, _Port in OrderedCurrentPorts
    }:
        raise ValueError("opposing-net access row proof domains are incomplete")
    if ContractDomain is None:
        for Contract, Port in OrderedCurrentPorts:
            if (
                str(getattr(Port, "Signal", "")) != CurrentSignal
                or _PhysicalPortLocalContractFingerprint(Port) != Contract
            ):
                raise ValueError(
                    "opposing-net access row current contract fingerprint "
                    "mismatch"
                )

    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == CurrentSignal
    )
    InputDomainComplete = bool(
        Problem.DomainComplete
        and Domains
        and all(getattr(Domain, "Complete", True) for Domain in Domains)
        and CompleteVariantDomainComplete
    )
    AccessPreparationStartedAt = monotonic()
    if ContractDomain is None:
        ContractDomain = BuildCompleteOpposingNetAccessContractDomain(
            Problem,
            CurrentSignal,
            dict(OrderedCurrentPorts),
        )
    ExpectedCurrentContracts = tuple(
        Contract for Contract, _Port in OrderedCurrentPorts
    )
    OrderedPortsByContract = dict(OrderedCurrentPorts)
    CandidateDomainsByContract = dict(
        ContractDomain.CandidateDomainsByCurrentContract
    )
    SignatureIndexByContract = dict(
        ContractDomain.SignatureIndexByCurrentContract
    )
    try:
        CanonicalSignaturesAreValid = (
            ContractDomain.SelectionKeysBySignatureIndex
            == tuple(sorted(
                ContractDomain.SelectionKeysBySignatureIndex
            ))
            and len(set(
                ContractDomain.SelectionKeysBySignatureIndex
            )) == len(ContractDomain.SelectionKeysBySignatureIndex)
            and len(ContractDomain.SelectionKeysBySignatureIndex)
            == len(ContractDomain.CanonicalAccessSignatures)
            and tuple(
                Contract for Contract, _Index
                in ContractDomain.SignatureIndexByCurrentContract
            ) == ExpectedCurrentContracts
            and all(
                0 <= Index
                < len(ContractDomain.CanonicalAccessSignatures)
                and tuple(sorted(getattr(
                    OrderedPortsByContract[Contract],
                    "OwnedCandidateFingerprints",
                    (),
                )))
                == ContractDomain.SelectionKeysBySignatureIndex[Index]
                for Contract, Index
                in ContractDomain.SignatureIndexByCurrentContract
            )
            and ContractDomain.CurrentContractDomainFingerprint
            == _BuildOpposingNetCurrentContractDomainFingerprint(
                CurrentSignal,
                OrderedCurrentPorts,
                ContractDomain.SelectionKeysBySignatureIndex,
                ContractDomain.CanonicalAccessSignatures,
                ContractDomain.SignatureIndexByCurrentContract,
                _ComponentOrigin(Problem),
            )
        )
    except (IndexError, KeyError, TypeError, ValueError):
        CanonicalSignaturesAreValid = False
    ProblemIdentityObjects = (
        Problem.Fabric,
        Problem.ResourceGraph,
        Problem.OwnedTerminalDomains,
        Problem.LocalClaims,
        Problem.ImmutableClaims,
        Problem.ComponentSignals,
        *(Port for _Contract, Port in OrderedCurrentPorts),
    )
    ExpensiveIdentityIsValidated = any(
        len(CachedObjects) == len(ProblemIdentityObjects)
        and all(
            Cached is Current
            for Cached, Current
            in zip(CachedObjects, ProblemIdentityObjects)
        )
        for CachedObjects
        in ContractDomain.ValidatedProblemIdentityObjects
    )
    if (
        ContractDomain.CurrentSignal != CurrentSignal
        or ContractDomain.FabricFingerprint
        != Problem.Fabric.FabricFingerprint
        or ContractDomain.CurrentContractFingerprints
        != ExpectedCurrentContracts
        or ContractDomain.PortObjectIdentities
        != tuple(
            (Contract, id(Port))
            for Contract, Port in OrderedCurrentPorts
        )
        or not CanonicalSignaturesAreValid
        or ContractDomain.DomainIndexFingerprint
        != _StableFingerprint((
            "opposing-current-access-domain-index-v1",
            ContractDomain.FabricFingerprint,
            ContractDomain.ResourceIdentityFingerprint,
            ContractDomain.CurrentAccessDomainFingerprint,
            ContractDomain.CurrentContractDomainFingerprint,
        ))
    ):
        raise ValueError(
            "opposing-net access contract domain identity mismatch"
        )
    if (
        tuple(sorted(CandidateDomainsByContract))
        != ExpectedCurrentContracts
    ):
        raise ValueError(
            "opposing-net access contract domain is incomplete"
        )
    if not ExpensiveIdentityIsValidated:
        ExpensiveIdentityIsValid = (
            ContractDomain.ResourceIdentityFingerprint
            == _OpposingNetResourceIdentityFingerprint(Problem)
            and ContractDomain.CurrentAccessDomainFingerprint
            == _OpposingRowCurrentAccessDomainFingerprint(
                Problem,
                CurrentSignal,
            )
            and all(
                SignatureFingerprint
                == _CanonicalOpposingNetAccessSignatureFingerprint(
                    Signature
                )
                for SignatureFingerprint, Signature
                in ContractDomain.CanonicalAccessSignatures
            )
        )
        for SignatureIndex in range(
            len(ContractDomain.CanonicalAccessSignatures)
        ):
            Contracts = tuple(
                Contract for Contract in ExpectedCurrentContracts
                if SignatureIndexByContract[Contract] == SignatureIndex
            )
            if not Contracts:
                ExpensiveIdentityIsValid = False
                break
            RepresentativeDomains = CandidateDomainsByContract[Contracts[0]]
            if (
                any(
                    CandidateDomainsByContract[Contract]
                    is not RepresentativeDomains
                    for Contract in Contracts[1:]
                )
                or _BuildOpposingNetEffectiveAccessSignatureFromDomains(
                    Problem,
                    CurrentSignal,
                    RepresentativeDomains,
                ) != ContractDomain.CanonicalAccessSignatures[
                    SignatureIndex
                ][1]
            ):
                ExpensiveIdentityIsValid = False
                break
        if not ExpensiveIdentityIsValid:
            raise ValueError(
                "opposing-net access contract domain identity mismatch"
            )
        ContractDomain.ValidatedProblemIdentityObjects.append(
            ProblemIdentityObjects
        )
    ContractsBySignatureIndex: dict[int, list[str]] = defaultdict(list)
    RepresentativeCandidateDomainsBySignatureIndex: dict[
        int,
        tuple[tuple[ComponentTerminalAccessCandidate, ...], ...],
    ] = {}
    for Contract in ExpectedCurrentContracts:
        SignatureIndex = SignatureIndexByContract[Contract]
        ContractsBySignatureIndex[SignatureIndex].append(Contract)
        RepresentativeCandidateDomainsBySignatureIndex.setdefault(
            SignatureIndex,
            CandidateDomainsByContract[Contract],
        )
    AccessPreparationSeconds = monotonic() - AccessPreparationStartedAt

    ExpectedVariantFingerprints = tuple(
        Variant.NetFingerprint
        for Variant in sorted(
            CompleteVariants,
            key=lambda Value: Value.NetFingerprint,
        )
    )
    if RowContext is None and CompleteVariants:
        RowContext = BuildCompleteOpposingNetAccessRowContext(
            Problem,
            CompleteVariants,
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
        )
    if RowContext is not None and (
        RowContext.FabricFingerprint != Problem.Fabric.FabricFingerprint
        or RowContext.CompleteVariantFingerprints
        != ExpectedVariantFingerprints
        or RowContext.CurrentSignal not in ("", CurrentSignal)
        or RowContext.CompleteSignal not in ("", CompleteSignal)
        or (
            RowContext.CurrentAccessDomainFingerprint
            and RowContext.CurrentAccessDomainFingerprint
            != ContractDomain.CurrentAccessDomainFingerprint
        )
    ):
        raise ValueError("opposing-net access row context identity mismatch")
    ComponentMapByVariant = (
        {}
        if RowContext is None
        else RowContext.ComponentMapByVariant
        if RowContext.ComponentMapByVariant
        else {
            Fingerprint: dict(Values)
            for Fingerprint, Values in RowContext.ComponentByNodeByVariant
        }
    )
    CompatibleByVariant = (
        {}
        if RowContext is None
        else RowContext.CompatibleComponentByCandidateFingerprintByVariant
    )

    StartedAt = monotonic()
    VariantScanCount = 0
    SignaturePairCheckCount = 0
    SupportingVariantBySignatureIndex: dict[int, str] = {}
    ExpansionCountBySignatureIndex = {
        SignatureIndex: 0
        for SignatureIndex in ContractsBySignatureIndex
    }
    EmptySignatureIndexes = {
        SignatureIndex
        for SignatureIndex, CandidateDomains
        in RepresentativeCandidateDomainsBySignatureIndex.items()
        if any(not Candidates for Candidates in CandidateDomains)
    }
    UnresolvedSignatureIndexes = (
        set(ContractsBySignatureIndex) - EmptySignatureIndexes
    )
    InitialDeadlineExpired = bool(
        CompleteVariants
        and DeadlineSeconds is not None
        and DeadlineSeconds <= 0
    )
    DeadlineExpired = InitialDeadlineExpired
    VariantScanStartedAt = monotonic()
    if InputDomainComplete and CompleteVariants and not DeadlineExpired:
        for Variant in sorted(
            CompleteVariants,
            key=lambda Value: Value.NetFingerprint,
        ):
            if (
                DeadlineSeconds is not None
                and monotonic() - StartedAt >= DeadlineSeconds
            ):
                DeadlineExpired = True
                break
            if not UnresolvedSignatureIndexes:
                break
            VariantScanCount += 1
            if WorkCheck is not None:
                WorkCheck({
                    "Stage": "complete-opposing-net-access-contract-row",
                    "VariantScanCount": VariantScanCount,
                    "CompleteVariantCount": len(CompleteVariants),
                    "UnresolvedAccessSignatureCount": len(
                        UnresolvedSignatureIndexes
                    ),
                    "CurrentSignal": CurrentSignal,
                    "CompleteSignal": CompleteSignal,
                })
            ComponentByNode = ComponentMapByVariant[Variant.NetFingerprint]
            CandidateComponentIndex = CompatibleByVariant.get(
                Variant.NetFingerprint,
                {},
            )
            for SignatureIndex in tuple(sorted(
                UnresolvedSignatureIndexes
            )):
                SignaturePairCheckCount += 1
                ExpansionCountBySignatureIndex[SignatureIndex] += 1
                CommonComponents: set[int] | None = None
                for Candidates in (
                    RepresentativeCandidateDomainsBySignatureIndex[
                        SignatureIndex
                    ]
                ):
                    CandidateComponents = {
                        (
                            CandidateComponentIndex[
                                Candidate.CandidateFingerprint
                            ]
                            if Candidate.CandidateFingerprint
                            in CandidateComponentIndex
                            else ComponentByNode[Candidate.Attachment]
                        )
                        for Candidate in Candidates
                        if (
                            Candidate.Attachment in ComponentByNode
                            and (
                                Candidate.CandidateFingerprint
                                in CandidateComponentIndex
                                or (
                                    not CompatibleByVariant
                                    and ComponentClaimsCompatibleForOwners(
                                        CurrentSignal,
                                        Candidate.Claims,
                                        CompleteSignal,
                                        Variant.Claims,
                                    )
                                )
                            )
                        )
                    }
                    if CommonComponents is None:
                        CommonComponents = set(CandidateComponents)
                    else:
                        CommonComponents.intersection_update(
                            CandidateComponents
                        )
                    if not CommonComponents:
                        break
                if CommonComponents:
                    SupportingVariantBySignatureIndex[SignatureIndex] = (
                        Variant.NetFingerprint
                    )
                    UnresolvedSignatureIndexes.remove(SignatureIndex)
    VariantScanSeconds = monotonic() - VariantScanStartedAt

    def ExactResult(
        Contract: str,
        SignatureIndex: int,
    ) -> CompleteOpposingNetAccessPairResult:
        DomainFingerprint = str(
            DomainFingerprintsByCurrentContract[Contract]
        )
        SupportingVariant = SupportingVariantBySignatureIndex.get(
            SignatureIndex
        )
        ExpansionCount = ExpansionCountBySignatureIndex[SignatureIndex]
        IncompleteDetail = (
            "pair access input domain is incomplete"
            if not InputDomainComplete
            else "pair access deadline expired"
            if (
                InitialDeadlineExpired
                or DeadlineExpired
                and SignatureIndex in UnresolvedSignatureIndexes
            )
            else ""
        )
        if IncompleteDetail:
            return CompleteOpposingNetAccessPairResult(
                Status="incomplete",
                Complete=False,
                Feasible=None,
                DomainFingerprint=DomainFingerprint,
                ProofFingerprint="",
                CurrentSignal=CurrentSignal,
                CompleteSignal=CompleteSignal,
                CurrentLocalContractFingerprint=Contract,
                CompleteLocalContractFingerprint=(
                    CompleteLocalContractFingerprint
                ),
                ExpansionCount=ExpansionCount,
                Detail=IncompleteDetail,
            )
        Feasible = SupportingVariant is not None
        SupportingVariants = (
            (SupportingVariant,) if SupportingVariant is not None else ()
        )
        EmptyCurrentDomain = SignatureIndex in EmptySignatureIndexes
        EmptyCompleteDomain = not CompleteVariants
        ProofValue: object = (
            "empty-current-access-domain"
            if EmptyCurrentDomain
            else "empty-complete-variant-domain"
            if EmptyCompleteDomain
            else SupportingVariants
        )
        return CompleteOpposingNetAccessPairResult(
            Status=("feasible" if Feasible else "architectural-unsatisfiable"),
            Complete=True,
            Feasible=Feasible,
            DomainFingerprint=DomainFingerprint,
            ProofFingerprint=_StableFingerprint((
                "complete-opposing-net-access-pair-proof-v1",
                DomainFingerprint,
                ProofValue,
            )),
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
            CurrentLocalContractFingerprint=Contract,
            CompleteLocalContractFingerprint=CompleteLocalContractFingerprint,
            SupportingCompleteVariantFingerprints=SupportingVariants,
            ExpansionCount=ExpansionCount,
            Detail=(
                "one exact current access domain has no legal candidate"
                if EmptyCurrentDomain
                else "complete opposing-net variant domain is empty"
                if EmptyCompleteDomain
                else "a complete opposing-net variant supports every access domain"
                if Feasible
                else "no complete opposing-net variant supports every access domain"
            ),
            Diagnostics={
                "CompleteVariantDomainComplete": True,
                "ReservedGlobalClaimsIgnored": True,
                "BulkAccessSignatureShared": bool(
                    len(ContractsBySignatureIndex[SignatureIndex]) > 1
                ),
            },
        )

    return CompleteOpposingNetAccessContractRowResult(
        ResultsByCurrentContract=tuple(
            (
                Contract,
                ExactResult(
                    Contract,
                    SignatureIndexByContract[Contract],
                ),
            )
            for Contract, _Port in OrderedCurrentPorts
        ),
        AccessSignatureCount=len(ContractsBySignatureIndex),
        VariantScanCount=VariantScanCount,
        SignaturePairCheckCount=SignaturePairCheckCount,
        AccessPreparationSeconds=AccessPreparationSeconds,
        VariantScanSeconds=VariantScanSeconds,
    )


@dataclass(frozen=True)
class CompleteComponentNetVariantPortfolioResult:
    """A cache-backed complete per-net portfolio, or typed incompleteness."""

    Status: str
    Complete: bool
    Variants: tuple[RoutedComponentNet, ...]
    DomainFingerprint: str
    Detail: str = ""
    ExpansionCount: int = 0
    Diagnostics: dict[str, object] | None = None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Status": self.Status,
            "Complete": self.Complete,
            "VariantCount": len(self.Variants),
            "VariantFingerprints": [
                Variant.NetFingerprint for Variant in self.Variants
            ],
            "DomainFingerprint": self.DomainFingerprint,
            "Detail": self.Detail,
            "ExpansionCount": self.ExpansionCount,
            "Diagnostics": dict(self.Diagnostics or {}),
        }


@dataclass(frozen=True)
class CompleteComponentNetMultiPortfolioResult:
    """One finite complete-net discovery projected onto exact port contracts."""

    Complete: bool
    PortfoliosByContract: tuple[
        tuple[str, CompleteComponentNetVariantPortfolioResult], ...
    ]
    DomainFingerprint: str
    CanonicalStateCount: int
    NetVariantBuildCount: int
    Detail: str = ""
    Diagnostics: dict[str, object] | None = None

    @property
    def Portfolios(self) -> dict[
        str, CompleteComponentNetVariantPortfolioResult
    ]:
        return dict(self.PortfoliosByContract)


def _PhysicalPortSeamContractFingerprint(Port: Any) -> str:
    """Mirror the pipeline's witness-free local seam identity."""
    Origin = Port.FabricAttachment

    def RelativePath(Path: Any) -> tuple[Position3, ...]:
        return tuple(
            (
                int(Position[0]) - int(Origin[0]),
                int(Position[1]) - int(Origin[1]),
                int(Position[2]) - int(Origin[2]),
            )
            for Position in Path
        )

    return "local-seam-contract-v1:" + _StableFingerprint((
        getattr(Port, "Direction", ""),
        getattr(Port, "FabricDomainFingerprint", ""),
        tuple(sorted(RelativePath(
            getattr(Port, "OwnedTerminals", ())
        ))),
        RelativePath(getattr(Port, "LocalPath", ())),
        int(getattr(Port, "Capacity", 1)),
    ))


def _PhysicalPortLocalContractFingerprint(Port: Any) -> str:
    """Mirror the pipeline's translation-stable local contract identity."""
    CertifiedFingerprint = str(getattr(
        Port,
        "CertifiedLocalContractFingerprint",
        "",
    ))
    CertifiedSeamFingerprint = str(getattr(
        Port,
        "CertifiedSeamContractFingerprint",
        "",
    ))
    if (
        CertifiedFingerprint
        and CertifiedSeamFingerprint
        and not getattr(Port, "OwnedCandidateFingerprints", ())
        and not getattr(Port, "OwnedAccessCandidates", ())
        and _PhysicalPortSeamContractFingerprint(Port)
        == CertifiedSeamFingerprint
    ):
        return CertifiedFingerprint
    Origin = Port.FabricAttachment

    def RelativePath(Path: Any) -> tuple[Position3, ...]:
        return tuple(
            (
                int(Position[0]) - int(Origin[0]),
                int(Position[1]) - int(Origin[1]),
                int(Position[2]) - int(Origin[2]),
            )
            for Position in Path
        )

    CandidateContracts = tuple(sorted(
        (
            RelativePath(Candidate.Path),
            int(Candidate.Layer),
        )
        for Candidate in getattr(Port, "OwnedAccessCandidates", ())
    ))
    return "local-contract-v1:" + _StableFingerprint((
        getattr(Port, "Direction", ""),
        getattr(Port, "FabricDomainFingerprint", ""),
        tuple(sorted(RelativePath(
            getattr(Port, "OwnedTerminals", ())
        ))),
        RelativePath(getattr(Port, "LocalPath", ())),
        CandidateContracts,
        int(getattr(Port, "Capacity", 1)),
    ))


def _BuildLocalOnlyCompleteNetProblem(
    Problem: ComponentRoutingProblem,
    Signal: str,
    Port: Any,
    StructuralFingerprint: str,
) -> ComponentRoutingProblem:
    """Close one signal around one exact local port, excluding global claims."""
    OriginalComponentSignals = frozenset(Problem.ComponentSignals)
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    LocalInterface = Problem.Interface
    if LocalInterface is not None:
        LocalInterface = replace(
            LocalInterface,
            OwnedSignals=(Signal,),
            Ports=tuple(
                Value for Value in LocalInterface.Ports
                if Value.Signal == Signal
            ),
            Feedthroughs=(),
            PhysicalPortReservations=(Port,),
        )
    LocalAssemblyPlan = Problem.PhysicalAssemblyPlan
    if LocalAssemblyPlan is not None:
        LocalAssemblyPlan = replace(
            LocalAssemblyPlan,
            Ports=(Port,),
            Feedthroughs=(),
        )
    return replace(
        Problem,
        ProblemFingerprint=_StableFingerprint((
            "local-only-complete-net-portfolio-problem-v1",
            StructuralFingerprint,
            Signal,
        )),
        ComponentSignals=(Signal,),
        LocalClaims=tuple(
            Claim for Claim in Problem.LocalClaims
            if Claim.Signal == Signal
        ),
        ImmutableClaims=tuple(
            Claim for Claim in Problem.ImmutableClaims
            if Claim.Signal not in OriginalComponentSignals
        ),
        OwnedTerminalDomains=Domains,
        ExternalContinuationTerminals=tuple(
            Value for Value in Problem.ExternalContinuationTerminals
            if Value[0] == Signal
        ),
        ExternalContinuationDomains=tuple(
            Domain for Domain in Problem.ExternalContinuationDomains
            if Domain.Signal == Signal
        ),
        ForeignEscapeDomains=(),
        ForeignTransitDomains=(),
        Interface=LocalInterface,
        PhysicalAssemblyPlan=LocalAssemblyPlan,
        ReservedGlobalClaimsBySignal=(),
    )


def GetCachedCompleteComponentNetVariantPortfolio(
    Problem: ComponentRoutingProblem,
    Signal: str,
    VariantPortfolioCache: dict[Any, Any],
    *,
    StaticContext: CompleteComponentNetPortfolioStaticContext | None = None,
) -> CompleteComponentNetVariantPortfolioResult:
    """Read only portfolios written after exhaustive net discovery.

    The component solver never writes ``VariantPortfolioCache`` when variant
    discovery stops at a limit.  Cache presence is therefore a completeness
    certificate, but structural reuse is still translated and revalidated
    against the exact local problem before being returned.
    """
    Signal = str(Signal)
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    Origin = _ComponentOrigin(Problem)
    StructuralFingerprint = _ComponentNetPortfolioStructuralFingerprint(
        Problem,
        Signal,
        Domains,
        Origin,
        StaticContext,
    )
    ExactKey = (Problem.ProblemFingerprint, Signal)
    StructuralKey = (
        "component-net-translation-v1",
        StructuralFingerprint,
    )
    Cached = VariantPortfolioCache.get(ExactKey)
    CacheKind = "exact"
    if Cached is None:
        Cached = VariantPortfolioCache.get(StructuralKey)
        CacheKind = "structural"
    DomainFingerprint = _StableFingerprint((
        "complete-component-net-variant-portfolio-v1",
        StructuralFingerprint,
        Signal,
    ))
    if Cached is None:
        return CompleteComponentNetVariantPortfolioResult(
            Status="incomplete",
            Complete=False,
            Variants=(),
            DomainFingerprint=DomainFingerprint,
            Detail="complete net variant portfolio is not cached",
        )
    (
        CachedVariants,
        _CombinationCount,
        _CachedRejections,
        CachedImmutableConflicts,
        CachedOrigin,
    ) = Cached
    if CacheKind == "structural" and CachedImmutableConflicts:
        return CompleteComponentNetVariantPortfolioResult(
            Status="incomplete",
            Complete=False,
            Variants=(),
            DomainFingerprint=DomainFingerprint,
            Detail="structural portfolio has context-specific conflicts",
        )
    Variants = _TranslateAndValidateNetPortfolio(
        tuple(CachedVariants),
        SourceOrigin=CachedOrigin,
        TargetOrigin=Origin,
        Signal=Signal,
        Domains=Domains,
        Problem=Problem,
    )
    if Variants is None:
        return CompleteComponentNetVariantPortfolioResult(
            Status="incomplete",
            Complete=False,
            Variants=(),
            DomainFingerprint=DomainFingerprint,
            Detail="cached net portfolio failed exact local validation",
        )
    return CompleteComponentNetVariantPortfolioResult(
        Status="complete",
        Complete=True,
        Variants=Variants,
        DomainFingerprint=DomainFingerprint,
        Detail="complete net variant portfolio reused",
    )


def CompileCompleteComponentNetVariantPortfolio(
    Problem: ComponentRoutingProblem,
    Signal: str,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    StaticContext: CompleteComponentNetPortfolioStaticContext | None = None,
) -> CompleteComponentNetVariantPortfolioResult:
    """Exhaust one exact local net domain without solving a component template.

    Partial enumeration is retained only in ``NetVariantDiscoveryStateCache``
    so a later call can resume it.  ``VariantPortfolioCache`` is populated by
    the shared discovery implementation only after the finite domain has been
    exhausted; incomplete work is therefore never admitted as a proof cache.
    Reserved global routes are deliberately removed from this local-only
    compilation stage.
    """
    Signal = str(Signal)
    if Signal not in Problem.ComponentSignals:
        raise ValueError("net portfolio signal is not owned by the component")
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    if not Domains:
        raise ValueError("net portfolio signal has no owned terminal domain")
    if VariantPortfolioCache is None:
        VariantPortfolioCache = {}
    if NetVariantConstructionCache is None:
        NetVariantConstructionCache = {}
    if RouteClaimsConstructionCache is None:
        RouteClaimsConstructionCache = {}
    if NetVariantDiscoveryStateCache is None:
        NetVariantDiscoveryStateCache = {}

    Origin = _ComponentOrigin(Problem)
    StructuralFingerprint = _ComponentNetPortfolioStructuralFingerprint(
        Problem,
        Signal,
        Domains,
        Origin,
        StaticContext,
    )
    OriginalComponentSignals = frozenset(Problem.ComponentSignals)
    PhysicalPortReservations = tuple(
        Port
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
        if Port.Signal == Signal
    )
    LocalInterface = Problem.Interface
    if LocalInterface is not None:
        LocalInterface = replace(
            LocalInterface,
            OwnedSignals=(Signal,),
            Ports=tuple(
                Port for Port in LocalInterface.Ports
                if Port.Signal == Signal
            ),
            Feedthroughs=(),
            PhysicalPortReservations=PhysicalPortReservations,
        )
    LocalAssemblyPlan = Problem.PhysicalAssemblyPlan
    if LocalAssemblyPlan is not None:
        LocalAssemblyPlan = replace(
            LocalAssemblyPlan,
            Ports=PhysicalPortReservations,
            Feedthroughs=(),
        )
    LocalProblem = replace(
        Problem,
        ProblemFingerprint=_StableFingerprint((
            "local-only-complete-net-portfolio-problem-v1",
            StructuralFingerprint,
            Signal,
        )),
        ComponentSignals=(Signal,),
        LocalClaims=tuple(
            Claim for Claim in Problem.LocalClaims
            if Claim.Signal == Signal
        ),
        ImmutableClaims=tuple(
            Claim for Claim in Problem.ImmutableClaims
            if Claim.Signal not in OriginalComponentSignals
        ),
        OwnedTerminalDomains=Domains,
        ExternalContinuationTerminals=tuple(
            Value for Value in Problem.ExternalContinuationTerminals
            if Value[0] == Signal
        ),
        ExternalContinuationDomains=tuple(
            Domain for Domain in Problem.ExternalContinuationDomains
            if Domain.Signal == Signal
        ),
        ForeignEscapeDomains=(),
        ForeignTransitDomains=(),
        Interface=LocalInterface,
        PhysicalAssemblyPlan=LocalAssemblyPlan,
        ReservedGlobalClaimsBySignal=(),
    )
    Cached = GetCachedCompleteComponentNetVariantPortfolio(
        LocalProblem,
        Signal,
        VariantPortfolioCache,
        StaticContext=StaticContext,
    )
    if Cached.Complete:
        return replace(
            Cached,
            Status="complete-cached",
            Diagnostics={
                "PortfolioCacheHit": True,
                "LocalOnly": True,
                "TemplateSearchEntered": False,
            },
        )

    Solve = SolveComponentRoutingProblem(
        LocalProblem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
        DiscoveryVariantLimit=None,
        StopAfterCompleteNetVariantPortfolioSignal=Signal,
        StaticPortfolioContextsBySignal=(
            {Signal: StaticContext}
            if StaticContext is not None
            else None
        ),
    )
    Portfolio = GetCachedCompleteComponentNetVariantPortfolio(
        LocalProblem,
        Signal,
        VariantPortfolioCache,
        StaticContext=StaticContext,
    )
    if Solve.Status == "complete-net-variant-portfolio" and Portfolio.Complete:
        return replace(
            Portfolio,
            Status="complete",
            Detail="complete local-only net variant portfolio compiled",
            ExpansionCount=Solve.ExpansionCount,
            Diagnostics={
                **dict(Solve.Diagnostics or {}),
                "PortfolioCacheHit": False,
                "LocalOnly": True,
                "TemplateSearchEntered": False,
            },
        )
    return CompleteComponentNetVariantPortfolioResult(
        Status="incomplete",
        Complete=False,
        Variants=(),
        DomainFingerprint=Portfolio.DomainFingerprint,
        Detail=Solve.Detail or "local-only net portfolio compilation incomplete",
        ExpansionCount=Solve.ExpansionCount,
        Diagnostics={
            **dict(Solve.Diagnostics or {}),
            "UnderlyingStatus": Solve.Status,
            "PortfolioCacheHit": False,
            "LocalOnly": True,
            "TemplateSearchEntered": False,
        },
    )


def CompileCompleteComponentNetVariantPortfolios(
    Problem: ComponentRoutingProblem,
    Signal: str,
    PortsByContract: Mapping[str, Any],
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    StaticContext: CompleteComponentNetPortfolioStaticContext | None = None,
) -> CompleteComponentNetMultiPortfolioResult:
    """Exhaust one shared net domain and project it onto exact local ports.

    Candidate tuples are admitted to a contract only when every selected
    terminal candidate belongs to that contract.  External egress is partitioned
    by the exact ``LocalPath``.  Consequently the projected finite domain for
    each contract is identical to an independent exact-port compilation, while
    common fabric/tree construction is performed once.
    """
    StartedAt = monotonic()
    Signal = str(Signal)
    OrderedPorts = tuple(sorted(
        (str(Contract), Port)
        for Contract, Port in PortsByContract.items()
    ))
    if Signal not in Problem.ComponentSignals or not OrderedPorts:
        raise ValueError("multi-portfolio requires an owned signal and ports")
    if any(
        str(getattr(Port, "Signal", "")) != Signal
        or _PhysicalPortLocalContractFingerprint(Port) != Contract
        for Contract, Port in OrderedPorts
    ):
        raise ValueError("multi-portfolio local contract identity mismatch")
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == Signal
    )
    if not Domains:
        raise ValueError("multi-portfolio signal has no owned terminal domain")
    if StaticContext is None:
        StaticContext = BuildCompleteComponentNetPortfolioStaticContext(
            Problem,
            Signal,
        )
    if VariantPortfolioCache is None:
        VariantPortfolioCache = {}
    if NetVariantConstructionCache is None:
        NetVariantConstructionCache = {}
    if RouteClaimsConstructionCache is None:
        RouteClaimsConstructionCache = {}
    if NetVariantDiscoveryStateCache is None:
        NetVariantDiscoveryStateCache = {}

    Origin = _ComponentOrigin(Problem)

    def RelativePath(Path: Iterable[Position3]) -> tuple[Position3, ...]:
        return tuple(_NormalizePosition(Value, Origin) for Value in Path)

    CandidateByFingerprint = {
        Candidate.CandidateFingerprint: Candidate
        for Domain in Domains
        for Candidate in Domain.Candidates
    }
    ContractIdentity = tuple(
        (
            Contract,
            RelativePath(getattr(Port, "LocalPath", ())),
            tuple(sorted(
                (
                    Fingerprint,
                    _NormalizePosition(Candidate.Attachment, Origin),
                    RelativePath(Candidate.Path),
                    _NormalizeClaims(Candidate.Claims, Origin),
                    Candidate.Layer,
                    Candidate.Cost,
                )
                for Fingerprint in getattr(
                    Port,
                    "OwnedCandidateFingerprints",
                    (),
                )
                for Candidate in (CandidateByFingerprint.get(Fingerprint),)
                if Candidate is not None
            )),
        )
        for Contract, Port in OrderedPorts
    )
    DomainFingerprint = _StableFingerprint((
        "complete-net-multi-contract-domain-v1",
        StaticContext.StaticStructuralFingerprint,
        ContractIdentity,
    ))
    LocalProblems = {}
    StructuralFingerprints = {}
    CompletedPortfolios = {}
    MissingContracts = []
    for Contract, Port in OrderedPorts:
        StructuralFingerprint = _ComponentNetPortfolioStructuralFingerprint(
            replace(
                Problem,
                Interface=replace(
                    Problem.Interface,
                    PhysicalPortReservations=(Port,),
                ) if Problem.Interface is not None else None,
            ),
            Signal,
            Domains,
            Origin,
            StaticContext,
        )
        LocalProblem = _BuildLocalOnlyCompleteNetProblem(
            Problem,
            Signal,
            Port,
            StructuralFingerprint,
        )
        StructuralFingerprints[Contract] = StructuralFingerprint
        LocalProblems[Contract] = LocalProblem
        Cached = GetCachedCompleteComponentNetVariantPortfolio(
            LocalProblem,
            Signal,
            VariantPortfolioCache,
            StaticContext=StaticContext,
        )
        if Cached.Complete:
            CompletedPortfolios[Contract] = replace(
                Cached,
                Status="complete-cached",
                Diagnostics={
                    "PortfolioCacheHit": True,
                    "LocalOnly": True,
                    "MultiContract": True,
                    "TemplateSearchEntered": False,
                },
            )
        else:
            MissingContracts.append(Contract)

    StateKey = (
        "complete-net-multi-contract-discovery-v1",
        DomainFingerprint,
    )
    PriorState = NetVariantDiscoveryStateCache.get(StateKey, {})
    VariantsByContract = {
        Contract: dict(Values)
        for Contract, Values in PriorState.get(
            "VariantsByContract",
            {},
        ).items()
        if Contract in MissingContracts
    }
    for Contract in MissingContracts:
        VariantsByContract.setdefault(Contract, {})
    RejectionsByContract = {
        Contract: dict(Values)
        for Contract, Values in PriorState.get(
            "RejectionsByContract",
            {},
        ).items()
        if Contract in MissingContracts
    }
    for Contract in MissingContracts:
        RejectionsByContract.setdefault(Contract, {})
    ImmutableConflictsByContract = {
        Contract: set(Values)
        for Contract, Values in PriorState.get(
            "ImmutableConflictsByContract",
            {},
        ).items()
        if Contract in MissingContracts
    }
    for Contract in MissingContracts:
        ImmutableConflictsByContract.setdefault(Contract, set())
    ProcessedStates = set(PriorState.get("ProcessedStates", ()))
    CombinationKeysByContract = {
        Contract: set(Values)
        for Contract, Values in PriorState.get(
            "CombinationKeysByContract",
            {},
        ).items()
        if Contract in MissingContracts
    }
    for Contract in MissingContracts:
        CombinationKeysByContract.setdefault(Contract, set())

    CandidateContracts = {}
    for Domain in Domains:
        for Candidate in Domain.Candidates:
            CandidateContracts.setdefault(
                Candidate.CandidateFingerprint,
                set(),
            ).update(
                Contract
                for Contract, Port in OrderedPorts
                if Contract in MissingContracts
                and (
                    not getattr(Port, "OwnedCandidateFingerprints", ())
                    or Candidate.CandidateFingerprint in frozenset(
                        Port.OwnedCandidateFingerprints
                    )
                )
            )
    HasExternalContinuation = any(
        Value[0] == Signal
        for Value in Problem.ExternalContinuationTerminals
    )
    ContractsByEgress = defaultdict(set)
    for Contract, Port in OrderedPorts:
        if Contract in MissingContracts:
            ContractsByEgress[
                tuple(Port.LocalPath) if HasExternalContinuation else ()
            ].add(Contract)

    FabricAdjacency = _BuildAdjacency(Problem.Fabric.Edges)
    FabricParentCache = {}
    FabricComponentByNode = {}
    ComponentIndex = 0
    for Start in sorted(Problem.Fabric.Nodes):
        if Start in FabricComponentByNode:
            continue
        Pending = [Start]
        FabricComponentByNode[Start] = ComponentIndex
        while Pending:
            Node = Pending.pop()
            for Neighbor in FabricAdjacency.get(Node, ()):
                if Neighbor not in FabricComponentByNode:
                    FabricComponentByNode[Neighbor] = ComponentIndex
                    Pending.append(Neighbor)
        ComponentIndex += 1
    CandidatesByDomainByComponent = tuple(
        {
            Index: tuple(
                Candidate
                for Candidate in Domain.Candidates
                if FabricComponentByNode.get(Candidate.Attachment) == Index
            )
            for Index in set(
                FabricComponentByNode.get(Candidate.Attachment)
                for Candidate in Domain.Candidates
                if Candidate.Attachment in FabricComponentByNode
            )
        }
        for Domain in Domains
    )
    CommonComponents = (
        set(CandidatesByDomainByComponent[0])
        if CandidatesByDomainByComponent else set()
    )
    for Values in CandidatesByDomainByComponent[1:]:
        CommonComponents.intersection_update(Values)

    CanonicalStateCount = len(ProcessedStates)
    NetVariantBuildCount = 0
    ImmutableAccessConflictCache = {}
    LocalClaimsBySignal = {
        Signal: tuple(
            Claim for Claim in Problem.LocalClaims
            if Claim.Signal == Signal
        )
    }
    TreeRepeaterSubproblemCache = {}
    TreeRepeaterCacheStatistics = {}

    def SavePartial() -> None:
        NetVariantDiscoveryStateCache[StateKey] = {
            "VariantsByContract": {
                Contract: dict(Values)
                for Contract, Values in VariantsByContract.items()
            },
            "RejectionsByContract": {
                Contract: dict(Values)
                for Contract, Values in RejectionsByContract.items()
            },
            "ImmutableConflictsByContract": {
                Contract: frozenset(Values)
                for Contract, Values in ImmutableConflictsByContract.items()
            },
            "ProcessedStates": frozenset(ProcessedStates),
            "CombinationKeysByContract": {
                Contract: frozenset(Values)
                for Contract, Values in CombinationKeysByContract.items()
            },
        }

    def IncompleteResult(Detail: str) -> CompleteComponentNetMultiPortfolioResult:
        SavePartial()
        Results = dict(CompletedPortfolios)
        for Contract in MissingContracts:
            Results[Contract] = CompleteComponentNetVariantPortfolioResult(
                Status="incomplete",
                Complete=False,
                Variants=(),
                DomainFingerprint=_StableFingerprint((
                    "complete-component-net-variant-portfolio-v1",
                    StructuralFingerprints[Contract],
                    Signal,
                )),
                Detail=Detail,
                Diagnostics={
                    "MultiContract": True,
                    "SharedDomainComplete": False,
                    "TemplateSearchEntered": False,
                },
            )
        return CompleteComponentNetMultiPortfolioResult(
            Complete=False,
            PortfoliosByContract=tuple(sorted(Results.items())),
            DomainFingerprint=DomainFingerprint,
            CanonicalStateCount=CanonicalStateCount,
            NetVariantBuildCount=NetVariantBuildCount,
            Detail=Detail,
            Diagnostics={
                "SolverCallCount": 1,
                "MissingContractCount": len(MissingContracts),
                "ResumedCanonicalStateCount": len(
                    PriorState.get("ProcessedStates", ())
                ),
            },
        )

    if MissingContracts:
        for FabricComponentIndex in sorted(CommonComponents):
            CandidateDomains = tuple(
                Values[FabricComponentIndex]
                for Values in CandidatesByDomainByComponent
            )
            for Candidates in product(*CandidateDomains):
                CombinationKey = tuple(
                    Candidate.CandidateFingerprint for Candidate in Candidates
                )
                AdmittedContracts = set(MissingContracts)
                for Candidate in Candidates:
                    AdmittedContracts.intersection_update(
                        CandidateContracts.get(
                            Candidate.CandidateFingerprint,
                            (),
                        )
                    )
                if not AdmittedContracts:
                    continue
                for EgressPath, PathContracts in sorted(
                    ContractsByEgress.items()
                ):
                    StateContracts = AdmittedContracts & PathContracts
                    if not StateContracts:
                        continue
                    StateKeyValue = (
                        FabricComponentIndex,
                        CombinationKey,
                        tuple(EgressPath),
                    )
                    if StateKeyValue in ProcessedStates:
                        continue
                    if (
                        DeadlineSeconds is not None
                        and monotonic() - StartedAt >= DeadlineSeconds
                    ):
                        return IncompleteResult(
                            "multi-contract portfolio deadline expired"
                        )
                    if WorkCheck is not None:
                        try:
                            WorkCheck({
                                "Stage": "complete-net-multi-contract-portfolio",
                                "CanonicalStateCount": CanonicalStateCount,
                                "ContractCount": len(OrderedPorts),
                            })
                        except BaseException:
                            SavePartial()
                            raise
                    FabricSubtree = _UniqueFabricSubtree(
                        Problem.Fabric,
                        (
                            *(Candidate.Attachment for Candidate in Candidates),
                            *((EgressPath[0],) if EgressPath else ()),
                        ),
                        Adjacency=FabricAdjacency,
                        ParentCache=FabricParentCache,
                    )
                    TemporaryRejections = {}
                    TemporaryConflicts = set()
                    RepresentativeContract = min(StateContracts)
                    Variant = _BuildNetVariant(
                        LocalProblems[RepresentativeContract],
                        Signal,
                        Domains,
                        tuple(Candidates),
                        tuple(EgressPath),
                        TemporaryRejections,
                        TemporaryConflicts,
                        FabricAdjacency,
                        FabricParentCache,
                        ImmutableAccessConflictCache,
                        LocalClaimsBySignal,
                        NetVariantConstructionCache,
                        RouteClaimsConstructionCache,
                        TreeRepeaterSubproblemCache,
                        TreeRepeaterCacheStatistics,
                        PrecomputedFabricSubtree=FabricSubtree,
                    )
                    NetVariantBuildCount += 1
                    CanonicalStateCount += 1
                    ProcessedStates.add(StateKeyValue)
                    for Contract in StateContracts:
                        CombinationKeysByContract[Contract].add(CombinationKey)
                        for Reason, Count in TemporaryRejections.items():
                            RejectionsByContract[Contract][Reason] = (
                                RejectionsByContract[Contract].get(Reason, 0)
                                + Count
                            )
                        ImmutableConflictsByContract[Contract].update(
                            TemporaryConflicts
                        )
                        if Variant is not None:
                            VariantsByContract[Contract].setdefault(
                                Variant.NetFingerprint,
                                Variant,
                            )

    NetVariantDiscoveryStateCache.pop(StateKey, None)
    Results = dict(CompletedPortfolios)
    for Contract in MissingContracts:
        LocalProblem = LocalProblems[Contract]
        EnumeratedVariants = tuple(
            VariantsByContract[Contract][Fingerprint]
            for Fingerprint in sorted(VariantsByContract[Contract])
        )
        CachedValue = (
            EnumeratedVariants,
            len(CombinationKeysByContract[Contract]),
            dict(RejectionsByContract[Contract]),
            frozenset(ImmutableConflictsByContract[Contract]),
            Origin,
        )
        VariantPortfolioCache[(LocalProblem.ProblemFingerprint, Signal)] = (
            CachedValue
        )
        if not ImmutableConflictsByContract[Contract]:
            VariantPortfolioCache[(
                "component-net-translation-v1",
                StructuralFingerprints[Contract],
            )] = CachedValue
        Results[Contract] = CompleteComponentNetVariantPortfolioResult(
            Status="complete",
            Complete=True,
            Variants=EnumeratedVariants,
            DomainFingerprint=_StableFingerprint((
                "complete-component-net-variant-portfolio-v1",
                StructuralFingerprints[Contract],
                Signal,
            )),
            Detail="complete shared multi-contract net portfolio compiled",
            Diagnostics={
                "PortfolioCacheHit": False,
                "LocalOnly": True,
                "MultiContract": True,
                "SharedDomainComplete": True,
                "AccessCombinationCount": len(
                    CombinationKeysByContract[Contract]
                ),
                "TemplateSearchEntered": False,
            },
        )
    return CompleteComponentNetMultiPortfolioResult(
        Complete=True,
        PortfoliosByContract=tuple(sorted(Results.items())),
        DomainFingerprint=DomainFingerprint,
        CanonicalStateCount=CanonicalStateCount,
        NetVariantBuildCount=NetVariantBuildCount,
        Detail="complete shared multi-contract portfolio domain exhausted",
        Diagnostics={
            "SolverCallCount": 1,
            "ContractCount": len(OrderedPorts),
            "PreviouslyCachedContractCount": len(CompletedPortfolios),
            "SharedDomainComplete": True,
            "ResumedCanonicalStateCount": len(
                PriorState.get("ProcessedStates", ())
            ),
        },
    )


def EvaluateCompleteOpposingNetAccessPair(
    Problem: ComponentRoutingProblem,
    *,
    CurrentSignal: str,
    CompleteSignal: str,
    CurrentLocalContractFingerprint: str,
    CompleteLocalContractFingerprint: str,
    CompleteVariants: tuple[RoutedComponentNet, ...],
    CompleteVariantDomainComplete: bool,
    DeadlineSeconds: float | None,
    DomainFingerprint: str | None = None,
    RowContext: CompleteOpposingNetAccessRowContext | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ProofCache: dict[str, CompleteOpposingNetAccessPairResult] | None = None,
) -> CompleteOpposingNetAccessPairResult:
    """Decide the access predicate without global routing or template search.

    ``CompleteVariants`` must be the complete net portfolio for the selected
    ``CompleteSignal`` local contract.  The oracle checks whether at least one
    such net leaves mutually connected, claim-compatible terminal access for
    the exact ``CurrentSignal`` contract.  Reserved global corridors are
    intentionally absent from both the predicate and its identity.
    """
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    if (
        not CurrentSignal
        or not CompleteSignal
        or CurrentSignal == CompleteSignal
    ):
        raise ValueError("opposing-net access requires two distinct signals")
    PortsBySignal = {
        str(Port.Signal): Port
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
    }
    CurrentPort = PortsBySignal.get(CurrentSignal)
    CompletePort = PortsBySignal.get(CompleteSignal)
    if CurrentPort is None or CompletePort is None:
        raise ValueError("opposing-net access requires exact physical ports")
    ExpectedCurrentContractFingerprint = (
        _PhysicalPortLocalContractFingerprint(CurrentPort)
    )
    ExpectedCompleteContractFingerprint = (
        _PhysicalPortLocalContractFingerprint(CompletePort)
    )
    if (
        CurrentLocalContractFingerprint
        != ExpectedCurrentContractFingerprint
        or CompleteLocalContractFingerprint
        != ExpectedCompleteContractFingerprint
    ):
        raise ValueError(
            "opposing-net access local contract fingerprint mismatch"
        )
    CurrentLocalContractFingerprint = ExpectedCurrentContractFingerprint
    CompleteLocalContractFingerprint = ExpectedCompleteContractFingerprint
    if (
        _PhysicalPortLocalContractFingerprint(CurrentPort)
        != CurrentLocalContractFingerprint
        or _PhysicalPortLocalContractFingerprint(CompletePort)
        != CompleteLocalContractFingerprint
    ):
        raise ValueError(
            "opposing-net access local contract identity mismatch"
        )
    Domains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if Domain.Signal == CurrentSignal
    )
    Origin = _ComponentOrigin(Problem)
    CurrentCandidateFingerprints = frozenset(
        getattr(CurrentPort, "OwnedCandidateFingerprints", ())
    )
    ImmutableForeignClaims = tuple(
        (str(Claim.Signal), Claim.Claims)
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if Claim.Signal not in Problem.ComponentSignals
    )

    def CandidateIdentity(
        Candidate: ComponentTerminalAccessCandidate,
    ) -> tuple[object, ...]:
        return (
            Candidate.CandidateFingerprint,
            _NormalizePosition(Candidate.Attachment, Origin),
            tuple(
                _NormalizePosition(Position, Origin)
                for Position in Candidate.Path
            ),
            _NormalizeClaims(Candidate.Claims, Origin),
            Candidate.Layer,
            Candidate.Cost,
        )

    DomainFingerprint = str(DomainFingerprint or "") or _StableFingerprint((
        "complete-opposing-net-access-pair-domain-v1",
        Problem.Fabric.FabricFingerprint,
        tuple(sorted(
            _NormalizePosition(Node, Origin)
            for Node in Problem.Fabric.Nodes
        )),
        tuple(sorted(
            _NormalizedEdge(
                _NormalizePosition(First, Origin),
                _NormalizePosition(Second, Origin),
            )
            for First, Second in Problem.Fabric.Edges
        )),
        CurrentSignal,
        CurrentLocalContractFingerprint,
        CompleteSignal,
        CompleteLocalContractFingerprint,
        tuple(sorted(
            (
                Domain.TerminalRole,
                Domain.TerminalFingerprint,
                bool(getattr(Domain, "Complete", True)),
                tuple(sorted(
                    CandidateIdentity(Candidate)
                    for Candidate in Domain.Candidates
                    if (
                        not CurrentCandidateFingerprints
                        or Candidate.CandidateFingerprint
                        in CurrentCandidateFingerprints
                    )
                )),
            )
            for Domain in Domains
        )),
        tuple(sorted(
            (
                Signal,
                _NormalizeClaims(Claims, Origin),
            )
            for Signal, Claims in ImmutableForeignClaims
        )),
        tuple(
            (
                Variant.NetFingerprint,
                tuple(sorted(
                    _NormalizePosition(Node, Origin)
                    for Node in Variant.Nodes
                )),
                _NormalizeClaims(Variant.Claims, Origin),
            )
            for Variant in sorted(
                CompleteVariants,
                key=lambda Value: Value.NetFingerprint,
            )
        ),
        bool(CompleteVariantDomainComplete),
        Problem.MaximumPowerDistance,
        getattr(Problem.ResourceGraph, "GraphVersion", None),
        type(getattr(Problem.ResourceGraph, "Technology", None)).__qualname__,
        getattr(
            getattr(Problem.ResourceGraph, "Technology", None),
            "TechnologyVersion",
            None,
        ),
        repr(getattr(Problem.ResourceGraph, "Technology", None)),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(Problem.ResourceGraph, "ActualBlocks", ())
        )),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(
                Problem.ResourceGraph,
                "ElectricalBlocks",
                (),
            )
        )),
        tuple(sorted(
            _NormalizePosition(Position, Origin)
            for Position in getattr(Problem.ResourceGraph, "SolidBlocks", ())
        )),
    ))
    if ProofCache is not None and DomainFingerprint in ProofCache:
        return ProofCache[DomainFingerprint]

    StartedAt = monotonic()
    ExpansionCount = 0

    def Incomplete(Detail: str) -> CompleteOpposingNetAccessPairResult:
        return CompleteOpposingNetAccessPairResult(
            Status="incomplete",
            Complete=False,
            Feasible=None,
            DomainFingerprint=DomainFingerprint,
            ProofFingerprint="",
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
            CurrentLocalContractFingerprint=(
                CurrentLocalContractFingerprint
            ),
            CompleteLocalContractFingerprint=(
                CompleteLocalContractFingerprint
            ),
            ExpansionCount=ExpansionCount,
            Detail=Detail,
        )

    if (
        not Problem.DomainComplete
        or not Domains
        or any(not getattr(Domain, "Complete", True) for Domain in Domains)
        or not CompleteVariantDomainComplete
    ):
        return Incomplete("pair access input domain is incomplete")
    if not CompleteVariants:
        Result = CompleteOpposingNetAccessPairResult(
            Status="architectural-unsatisfiable",
            Complete=True,
            Feasible=False,
            DomainFingerprint=DomainFingerprint,
            ProofFingerprint=_StableFingerprint((
                "complete-opposing-net-access-pair-proof-v1",
                DomainFingerprint,
                "empty-complete-variant-domain",
            )),
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
            CurrentLocalContractFingerprint=(
                CurrentLocalContractFingerprint
            ),
            CompleteLocalContractFingerprint=(
                CompleteLocalContractFingerprint
            ),
            ExpansionCount=ExpansionCount,
            Detail="complete opposing-net variant domain is empty",
        )
        if ProofCache is not None:
            ProofCache[DomainFingerprint] = Result
        return Result
    if DeadlineSeconds is not None and DeadlineSeconds <= 0:
        return Incomplete("pair access deadline expired")

    CandidateDomains = tuple(
        tuple(
            Candidate
            for Candidate in Domain.Candidates
            if (
                (
                    not CurrentCandidateFingerprints
                    or Candidate.CandidateFingerprint
                    in CurrentCandidateFingerprints
                )
                and not any(
                    ComponentClaimsConflict(Candidate.Claims, Claims)
                    for _Signal, Claims in ImmutableForeignClaims
                )
            )
        )
        for Domain in Domains
    )
    if any(not Candidates for Candidates in CandidateDomains):
        Result = CompleteOpposingNetAccessPairResult(
            Status="architectural-unsatisfiable",
            Complete=True,
            Feasible=False,
            DomainFingerprint=DomainFingerprint,
            ProofFingerprint=_StableFingerprint((
                "complete-opposing-net-access-pair-proof-v1",
                DomainFingerprint,
                "empty-current-access-domain",
            )),
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
            CurrentLocalContractFingerprint=(
                CurrentLocalContractFingerprint
            ),
            CompleteLocalContractFingerprint=(
                CompleteLocalContractFingerprint
            ),
            ExpansionCount=ExpansionCount,
            Detail="one exact current access domain has no legal candidate",
        )
        if ProofCache is not None:
            ProofCache[DomainFingerprint] = Result
        return Result

    ExpectedVariantFingerprints = tuple(
        Variant.NetFingerprint
        for Variant in sorted(
            CompleteVariants,
            key=lambda Value: Value.NetFingerprint,
        )
    )
    if RowContext is None:
        RowContext = BuildCompleteOpposingNetAccessRowContext(
            Problem,
            CompleteVariants,
            CurrentSignal=CurrentSignal,
            CompleteSignal=CompleteSignal,
        )
    if (
        RowContext.FabricFingerprint != Problem.Fabric.FabricFingerprint
        or RowContext.CompleteVariantFingerprints
        != ExpectedVariantFingerprints
        or (
            RowContext.CurrentSignal
            and RowContext.CurrentSignal != CurrentSignal
        )
        or (
            RowContext.CompleteSignal
            and RowContext.CompleteSignal != CompleteSignal
        )
        or (
            RowContext.CurrentAccessDomainFingerprint
            and RowContext.CurrentAccessDomainFingerprint
            != _OpposingRowCurrentAccessDomainFingerprint(
                Problem,
                CurrentSignal,
            )
        )
    ):
        raise ValueError("opposing-net access row context identity mismatch")
    ComponentMapByVariant = (
        RowContext.ComponentMapByVariant
        if RowContext.ComponentMapByVariant
        else {
            Fingerprint: dict(Values)
            for Fingerprint, Values in RowContext.ComponentByNodeByVariant
        }
    )
    CompatibleComponentsByVariant = (
        RowContext.CompatibleComponentByCandidateFingerprintByVariant
        if (
            RowContext.CurrentSignal == CurrentSignal
            and RowContext.CompleteSignal == CompleteSignal
        )
        else {}
    )
    SupportingVariants = []
    for Variant in sorted(
        CompleteVariants,
        key=lambda Value: Value.NetFingerprint,
    ):
        if (
            DeadlineSeconds is not None
            and monotonic() - StartedAt >= DeadlineSeconds
        ):
            return Incomplete("pair access deadline expired")
        ExpansionCount += 1
        if WorkCheck is not None:
            WorkCheck({
                "Stage": "complete-opposing-net-access-pair",
                "ExpansionCount": ExpansionCount,
                "CompleteVariantCount": len(CompleteVariants),
                "CurrentSignal": CurrentSignal,
                "CompleteSignal": CompleteSignal,
            })
        ComponentByNode = ComponentMapByVariant[Variant.NetFingerprint]
        CompatibleComponents = CompatibleComponentsByVariant.get(
            Variant.NetFingerprint
        )
        CommonComponents: set[int] | None = None
        for Candidates in CandidateDomains:
            CandidateComponents = (
                {
                    CompatibleComponents[
                        Candidate.CandidateFingerprint
                    ]
                    for Candidate in Candidates
                    if Candidate.CandidateFingerprint
                    in CompatibleComponents
                }
                if CompatibleComponents is not None
                else {
                    ComponentByNode[Candidate.Attachment]
                    for Candidate in Candidates
                    if (
                        Candidate.Attachment in ComponentByNode
                        and ComponentClaimsCompatibleForOwners(
                            CurrentSignal,
                            Candidate.Claims,
                            CompleteSignal,
                            Variant.Claims,
                        )
                    )
                }
            )
            if CommonComponents is None:
                CommonComponents = set(CandidateComponents)
            else:
                CommonComponents.intersection_update(CandidateComponents)
            if not CommonComponents:
                break
        if CommonComponents:
            SupportingVariants.append(Variant.NetFingerprint)
            break

    Feasible = bool(SupportingVariants)
    Result = CompleteOpposingNetAccessPairResult(
        Status="feasible" if Feasible else "architectural-unsatisfiable",
        Complete=True,
        Feasible=Feasible,
        DomainFingerprint=DomainFingerprint,
        ProofFingerprint=_StableFingerprint((
            "complete-opposing-net-access-pair-proof-v1",
            DomainFingerprint,
            tuple(SupportingVariants),
        )),
        CurrentSignal=CurrentSignal,
        CompleteSignal=CompleteSignal,
        CurrentLocalContractFingerprint=CurrentLocalContractFingerprint,
        CompleteLocalContractFingerprint=CompleteLocalContractFingerprint,
        SupportingCompleteVariantFingerprints=tuple(SupportingVariants),
        ExpansionCount=ExpansionCount,
        Detail=(
            "a complete opposing-net variant supports every access domain"
            if Feasible
            else "no complete opposing-net variant supports every access domain"
        ),
        Diagnostics={
            "CurrentAccessDomainSizes": [
                len(Values) for Values in CandidateDomains
            ],
            "CompleteVariantDomainComplete": True,
            "ReservedGlobalClaimsIgnored": True,
        },
    )
    if ProofCache is not None:
        ProofCache[DomainFingerprint] = Result
    return Result


def EvaluateCachedCompleteOpposingNetAccessPair(
    Problem: ComponentRoutingProblem,
    *,
    CurrentSignal: str,
    CompleteSignal: str,
    CurrentLocalContractFingerprint: str,
    CompleteLocalContractFingerprint: str,
    VariantPortfolioCache: dict[Any, Any],
    DeadlineSeconds: float | None,
    DomainFingerprint: str | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ProofCache: dict[str, CompleteOpposingNetAccessPairResult] | None = None,
) -> CompleteOpposingNetAccessPairResult:
    """Evaluate a pair using only an exhaustively cached complete portfolio."""
    Portfolio = GetCachedCompleteComponentNetVariantPortfolio(
        Problem,
        CompleteSignal,
        VariantPortfolioCache,
    )
    return EvaluateCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal=CurrentSignal,
        CompleteSignal=CompleteSignal,
        CurrentLocalContractFingerprint=CurrentLocalContractFingerprint,
        CompleteLocalContractFingerprint=CompleteLocalContractFingerprint,
        CompleteVariants=Portfolio.Variants,
        CompleteVariantDomainComplete=Portfolio.Complete,
        DeadlineSeconds=DeadlineSeconds,
        DomainFingerprint=DomainFingerprint,
        WorkCheck=WorkCheck,
        ProofCache=ProofCache,
    )


@dataclass(frozen=True)
class ExactComponentPortRealizabilityContext:
    """Signal-static identity and blockers shared by exact port probes."""

    Origin: Position3
    FabricStructuralFingerprint: str
    ImmutableIdentity: tuple[Any, ...]
    ImmutableClaims: tuple[tuple[str, RoutingResourceClaims], ...]
    StaticContractFingerprint: str
    CandidateIdentityCache: dict[
        tuple[
            tuple[
                ComponentTerminalAccessDomain,
                ComponentTerminalAccessCandidate,
            ],
            ...,
        ],
        str,
    ] = field(default_factory=dict, compare=False, repr=False)
    LocalPathIdentityCache: dict[
        tuple[Position3, ...], str
    ] = field(default_factory=dict, compare=False, repr=False)


MaximumStructuralPortRealizabilityCacheEntries = 65_536
_StructuralPortRealizabilityCache: dict[
    str, ExactComponentPortRealizabilityResult
] = {}


def ClearStructuralPortRealizabilityCache() -> None:
    """Clear translation-normalized exact port predicates for tests."""
    _StructuralPortRealizabilityCache.clear()


def BuildExactComponentPortRealizabilityContext(
    Problem: ComponentRoutingProblem,
    *,
    Signal: str,
    ReservedClaimsBySignal: tuple[
        tuple[str, RoutingResourceClaims], ...
    ] = (),
) -> ExactComponentPortRealizabilityContext:
    """Precompute the immutable half of a signal's port proof domain."""
    Origin = _ComponentOrigin(Problem)
    FabricStructuralFingerprint = _StableFingerprint((
        getattr(Problem.Fabric, "TopologyKind", ""),
        tuple(sorted(
            _NormalizePosition(Node, Origin)
            for Node in Problem.Fabric.Nodes
        )),
        tuple(sorted(
            tuple(sorted((
                _NormalizePosition(First, Origin),
                _NormalizePosition(Second, Origin),
            )))
            for First, Second in Problem.Fabric.Edges
        )),
    ))
    ComponentSignals = frozenset(Problem.ComponentSignals)
    RelevantClaimInputs = (
        *((str(Claim.Signal), Claim.Claims) for Claim in Problem.LocalClaims),
        *(
            (str(Claim.Signal), Claim.Claims)
            for Claim in Problem.ImmutableClaims
            if str(Claim.Signal) != Signal
        ),
        *(
            (Owner, Claims)
            for Owner, Claims in (
                *Problem.ReservedGlobalClaimsBySignal,
                *ReservedClaimsBySignal,
            )
            if Owner != Signal
        ),
    )
    ImmutableIdentity = tuple(sorted(
        (
            (
                "self"
                if Owner == Signal
                else "component-peer"
                if Owner in ComponentSignals
                else "foreign"
            ),
            _NormalizeClaims(Claims, Origin),
        )
        for Owner, Claims in RelevantClaimInputs
    ))
    ImmutableClaims = tuple(
        (str(Claim.Signal), Claim.Claims)
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if str(Claim.Signal) != Signal
    ) + tuple(
        (Owner, Claims)
        for Owner, Claims in (
            *Problem.ReservedGlobalClaimsBySignal,
            *ReservedClaimsBySignal,
        )
        if Owner != Signal
    )
    return ExactComponentPortRealizabilityContext(
        Origin=Origin,
        FabricStructuralFingerprint=FabricStructuralFingerprint,
        ImmutableIdentity=ImmutableIdentity,
        ImmutableClaims=ImmutableClaims,
        StaticContractFingerprint=_StableFingerprint((
            "exact-component-port-static-v2",
            FabricStructuralFingerprint,
            ImmutableIdentity,
            Problem.MaximumPowerDistance,
            getattr(Problem.ResourceGraph, "GraphVersion", None),
            type(getattr(
                Problem.ResourceGraph,
                "Technology",
                None,
            )).__qualname__,
        )),
    )


def BuildExactComponentPortRealizabilityFingerprint(
    Problem: ComponentRoutingProblem,
    *,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Candidates: tuple[ComponentTerminalAccessCandidate, ...],
    LocalPath: tuple[Position3, ...],
    ReservedClaimsBySignal: tuple[
        tuple[str, RoutingResourceClaims], ...
    ] = (),
    Context: ExactComponentPortRealizabilityContext | None = None,
) -> str:
    """Identify translation-equivalent exact single-port proof inputs."""
    Context = Context or BuildExactComponentPortRealizabilityContext(
        Problem,
        Signal=Signal,
        ReservedClaimsBySignal=ReservedClaimsBySignal,
    )
    Origin = Context.Origin

    def ClaimIdentity(
        Claims: RoutingResourceClaims,
    ) -> tuple[tuple[Position3, ...], ...]:
        return _NormalizeClaims(Claims, Origin)

    CandidateCacheKey = tuple(
        (Domain, Candidate)
        for Domain, Candidate in zip(Domains, Candidates)
    )
    CandidateIdentityFingerprint = Context.CandidateIdentityCache.get(
        CandidateCacheKey
    )
    if CandidateIdentityFingerprint is None:
        CandidateIdentity = tuple(
            (
                Domain.TerminalRole,
                _NormalizePosition(Domain.Terminal, Origin),
                _NormalizePosition(Candidate.Attachment, Origin),
                tuple(
                    _NormalizePosition(Value, Origin)
                    for Value in Candidate.Path
                ),
                ClaimIdentity(Candidate.Claims),
                Candidate.Layer,
            )
            for Domain, Candidate in zip(Domains, Candidates)
        )
        CandidateIdentityFingerprint = _StableFingerprint((
            "exact-component-port-candidates-v2",
            len(Domains),
            len(Candidates),
            CandidateIdentity,
        ))
        Context.CandidateIdentityCache[
            CandidateCacheKey
        ] = CandidateIdentityFingerprint
    LocalPathKey = tuple(LocalPath)
    LocalPathIdentityFingerprint = Context.LocalPathIdentityCache.get(
        LocalPathKey
    )
    if LocalPathIdentityFingerprint is None:
        LocalPathIdentity = tuple(
            _NormalizePosition(Value, Origin)
            for Value in LocalPathKey
        )
        LocalPathIdentityFingerprint = _StableFingerprint((
            "exact-component-port-local-path-v2",
            LocalPathIdentity,
        ))
        Context.LocalPathIdentityCache[
            LocalPathKey
        ] = LocalPathIdentityFingerprint
    return _StableFingerprint((
        "exact-component-port-realizability-v2",
        Context.StaticContractFingerprint,
        CandidateIdentityFingerprint,
        LocalPathIdentityFingerprint,
    ))


def EvaluateExactComponentPortRealizability(
    Problem: ComponentRoutingProblem,
    *,
    Signal: str,
    Domains: tuple[ComponentTerminalAccessDomain, ...],
    Candidates: tuple[ComponentTerminalAccessCandidate, ...],
    LocalPath: tuple[Position3, ...],
    ReservedClaimsBySignal: tuple[
        tuple[str, RoutingResourceClaims], ...
    ] = (),
    RealizabilityCache: dict[
        str, ExactComponentPortRealizabilityResult
    ] | None = None,
    FabricAdjacency: dict[
        Position3, set[Position3]
    ] | None = None,
    FabricParentCache: dict[
        Position3,
        dict[Position3, Position3 | None],
    ] | None = None,
    ImmutableAccessConflictCache: dict[
        tuple[str, str, tuple[Position3, ...]],
        frozenset[str],
    ] | None = None,
    LocalClaimsBySignal: dict[
        str, tuple[Any, ...]
    ] | None = None,
    NetVariantTopologyCache: dict[
        tuple[
            str,
            frozenset[Position3],
            frozenset[RoutingEdge],
            tuple[Position3, ...],
        ],
        RoutedComponentNet | None,
    ] | None = None,
    RouteClaimsCache: dict[
        frozenset[Position3],
        RoutingResourceClaims,
    ] | None = None,
    TreeRepeaterSubproblemCache: dict[
        tuple[int, int, str],
        tuple[tuple[Position3, str], ...] | None,
    ] | None = None,
    TreeRepeaterCacheStatistics: dict[str, int] | None = None,
    Context: ExactComponentPortRealizabilityContext | None = None,
    UseStructuralCache: bool = False,
) -> ExactComponentPortRealizabilityResult:
    """Prove one exact access/seam contract without a multi-net solve."""
    ContractFingerprint = (
        BuildExactComponentPortRealizabilityFingerprint(
            Problem,
            Signal=Signal,
            Domains=Domains,
            Candidates=Candidates,
            LocalPath=LocalPath,
            ReservedClaimsBySignal=ReservedClaimsBySignal,
            Context=Context,
        )
    )
    Cached = (
        RealizabilityCache.get(ContractFingerprint)
        if RealizabilityCache is not None
        else None
    )
    CacheScope = "local"
    if Cached is None and UseStructuralCache:
        Cached = _StructuralPortRealizabilityCache.get(
            ContractFingerprint
        )
        CacheScope = "structural"
    if Cached is not None:
        if RealizabilityCache is not None:
            RealizabilityCache[ContractFingerprint] = Cached
        return replace(
            Cached,
            Diagnostics={
                **(Cached.Diagnostics or {}),
                "CacheHit": True,
                "CacheScope": CacheScope,
            },
        )
    FabricNodes = frozenset(Problem.Fabric.Nodes)

    def CandidateMatchesDomain(
        Domain: ComponentTerminalAccessDomain,
        Candidate: ComponentTerminalAccessCandidate,
    ) -> bool:
        if Candidate in Domain.Candidates:
            return True
        if (
            Problem.ResourceGraph is None
            or not Candidate.Path
            or Candidate.Path[0] != Domain.Terminal
            or Candidate.Attachment not in FabricNodes
            or Candidate.Path[-1] != Candidate.Attachment
            or any(
                Problem.ResourceGraph.BuildPrimitive(First, Second) is None
                for First, Second in zip(
                    Candidate.Path,
                    Candidate.Path[1:],
                )
            )
        ):
            return False
        return (
            Problem.ResourceGraph.BuildRouteClaims(
                frozenset(Candidate.Path)
            )
            == Candidate.Claims
        )

    if (
        len(Domains) != len(Candidates)
        or not Domains
        or not LocalPath
        or any(
            Domain.Signal != Signal
            or not CandidateMatchesDomain(Domain, Candidate)
            for Domain, Candidate in zip(Domains, Candidates)
        )
    ):
        Result = ExactComponentPortRealizabilityResult(
            Realizable=False,
            ContractFingerprint=ContractFingerprint,
            Detail="exact port contract is incomplete or inconsistent",
            Diagnostics={
                "CacheHit": False,
                "RejectionCounts": {
                    "invalid-exact-port-contract": 1,
                },
                "ImmutableConflictSignals": [],
            },
        )
    else:
        RejectionCounts: dict[str, int] = {}
        ImmutableConflictSignals: set[str] = set()
        ProbeProblem = replace(
            Problem,
            ReservedGlobalClaimsBySignal=tuple((
                *Problem.ReservedGlobalClaimsBySignal,
                *ReservedClaimsBySignal,
            )),
        )
        Net = _BuildNetVariant(
            ProbeProblem,
            Signal,
            Domains,
            Candidates,
            LocalPath,
            RejectionCounts,
            ImmutableConflictSignals,
            FabricAdjacency=FabricAdjacency,
            FabricParentCache=FabricParentCache,
            ImmutableAccessConflictCache=(
                ImmutableAccessConflictCache
            ),
            LocalClaimsBySignal=LocalClaimsBySignal,
            NetVariantTopologyCache=NetVariantTopologyCache,
            RouteClaimsCache=RouteClaimsCache,
            TreeRepeaterSubproblemCache=(
                TreeRepeaterSubproblemCache
            ),
            TreeRepeaterCacheStatistics=(
                TreeRepeaterCacheStatistics
            ),
        )
        Context = Context or BuildExactComponentPortRealizabilityContext(
            Problem,
            Signal=Signal,
            ReservedClaimsBySignal=ReservedClaimsBySignal,
        )
        ImmutableClaims = Context.ImmutableClaims
        RouteBlockers = tuple(sorted(
            Owner
            for Owner, Claims in ImmutableClaims
            if (
                Net is not None
                and not ComponentClaimsCompatibleForOwners(
                    Signal,
                    Net.Claims,
                    Owner,
                    Claims,
                )
            )
        ))
        if Net is not None and RouteBlockers:
            RejectionCounts["immutable-route-conflict"] = (
                RejectionCounts.get(
                    "immutable-route-conflict",
                    0,
                )
                + 1
            )
            ImmutableConflictSignals.update(RouteBlockers)
            Net = None
        Result = ExactComponentPortRealizabilityResult(
            Realizable=Net is not None,
            ContractFingerprint=ContractFingerprint,
            NetFingerprint=(
                Net.NetFingerprint if Net is not None else ""
            ),
            Detail=(
                ""
                if Net is not None
                else "exact port contract has no powered legal subtree"
            ),
            Diagnostics={
                "CacheHit": False,
                "RejectionCounts": dict(sorted(
                    RejectionCounts.items()
                )),
                "ImmutableConflictSignals": sorted(
                    ImmutableConflictSignals
                ),
                "FabricFingerprint": (
                    Problem.Fabric.FabricFingerprint
                ),
                "CandidateCount": len(Candidates),
                "LocalPathLength": len(LocalPath),
            },
        )
    if RealizabilityCache is not None:
        RealizabilityCache[ContractFingerprint] = Result
    if UseStructuralCache:
        _StructuralPortRealizabilityCache[ContractFingerprint] = Result
        while (
            len(_StructuralPortRealizabilityCache)
            > MaximumStructuralPortRealizabilityCacheEntries
        ):
            _StructuralPortRealizabilityCache.pop(
                next(iter(_StructuralPortRealizabilityCache))
            )
    return Result


def BuildComponentForeignTransitDomains(
    Problem: ComponentRoutingProblem,
    Profiles: dict[str, Any],
    *,
    MaximumCandidatesPerSignal: int = 8,
) -> tuple[ComponentForeignTransitDomain, ...]:
    """Build finite through-component witnesses for geometrically split nets.

    A foreign net qualifies only when its terminals lie on both opposite
    sides of the routed component and their orthogonal span intersects the
    component.  The witness uses the existing finite component fabric and
    fixed egress macros; it is not a whole-design detour search.
    """
    if MaximumCandidatesPerSignal < 1:
        raise ValueError(
            "MaximumCandidatesPerSignal must be positive"
        )
    FabricNodes = frozenset(Problem.Fabric.Nodes)
    if not FabricNodes or not Problem.Fabric.Complete:
        return ()
    MinimumX = min(Position[0] for Position in FabricNodes)
    MaximumX = max(Position[0] for Position in FabricNodes)
    MinimumZ = min(Position[2] for Position in FabricNodes)
    MaximumZ = max(Position[2] for Position in FabricNodes)
    Adjacency = _BuildAdjacency(Problem.Fabric.Edges)
    Remaining = set(FabricNodes)
    FabricComponents: list[frozenset[Position3]] = []
    while Remaining:
        Start = min(Remaining)
        Visited = {Start}
        Pending = deque((Start,))
        while Pending:
            Current = Pending.popleft()
            for Neighbor in sorted(Adjacency.get(Current, ())):
                if Neighbor not in Visited:
                    Visited.add(Neighbor)
                    Pending.append(Neighbor)
        Component = frozenset(Visited)
        FabricComponents.append(Component)
        Remaining.difference_update(Component)

    def CandidateForPath(
        PathFromTerminal: tuple[Position3, ...],
    ) -> ComponentTerminalAccessCandidate:
        Claims = (
            Problem.ResourceGraph.BuildRouteClaims(PathFromTerminal)
            if Problem.ResourceGraph is not None
            else RoutingResourceClaims(
                WireCells=frozenset(PathFromTerminal),
                SupportCells=frozenset(
                    (X, Y - 1, Z)
                    for X, Y, Z in PathFromTerminal
                ),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions(set(
                        PathFromTerminal
                    ))
                ),
            )
        )
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=_StableFingerprint((
                _RelativeGeometry(PathFromTerminal),
                _ClaimsFingerprint(Claims),
            )),
            Attachment=PathFromTerminal[-1],
            Path=PathFromTerminal,
            Claims=Claims,
            Layer=3,
            Cost=len(PathFromTerminal),
        )

    Result = []
    ComponentSignals = frozenset(Problem.ComponentSignals)
    for Signal, Profile in sorted(Profiles.items()):
        if Signal in ComponentSignals:
            continue
        Terminals = tuple(dict.fromkeys((
            tuple(Profile.Root),
            *(tuple(Value) for Value in Profile.Targets),
        )))
        if len(Terminals) < 2:
            continue
        MinimumTerminalX = min(Value[0] for Value in Terminals)
        MaximumTerminalX = max(Value[0] for Value in Terminals)
        MinimumTerminalZ = min(Value[2] for Value in Terminals)
        MaximumTerminalZ = max(Value[2] for Value in Terminals)
        CenterX = (MinimumX + MaximumX) / 2.0
        CenterZ = (MinimumZ + MaximumZ) / 2.0
        AxisScores = []
        if (
            MinimumTerminalX < CenterX
            and MaximumTerminalX > CenterX
            and MinimumTerminalZ <= MaximumZ
            and MaximumTerminalZ >= MinimumZ
        ):
            AxisScores.append((
                -(
                    CenterX - MinimumTerminalX
                    + MaximumTerminalX - CenterX
                ),
                "X",
            ))
        if (
            MinimumTerminalZ < CenterZ
            and MaximumTerminalZ > CenterZ
            and MinimumTerminalX <= MaximumX
            and MaximumTerminalX >= MinimumX
        ):
            AxisScores.append((
                -(
                    CenterZ - MinimumTerminalZ
                    + MaximumTerminalZ - CenterZ
                ),
                "Z",
            ))
        CrossesComponentPartition = bool(AxisScores)
        if not AxisScores:
            ProjectedSpanX = max(
                0,
                min(MaximumTerminalX, MaximumX)
                - max(MinimumTerminalX, MinimumX),
            )
            ProjectedSpanZ = max(
                0,
                min(MaximumTerminalZ, MaximumZ)
                - max(MinimumTerminalZ, MinimumZ),
            )
            AxisScores.append((
                -max(ProjectedSpanX, ProjectedSpanZ),
                "X"
                if ProjectedSpanX >= ProjectedSpanZ
                else "Z",
            ))
        _Score, Axis = min(AxisScores)
        AxisIndex = 0 if Axis == "X" else 2
        GlobalMinimum = MinimumX if Axis == "X" else MinimumZ
        GlobalMaximum = MaximumX if Axis == "X" else MaximumZ
        RootCoordinate = Profile.Root[AxisIndex]
        SourceSide = (
            "minimum"
            if RootCoordinate
            <= ((GlobalMinimum + GlobalMaximum) / 2.0)
            else "maximum"
        )
        Variants = []
        RejectionCounts: dict[str, int] = {}
        ImmutableConflictSignals: set[str] = set()
        AttemptedCandidateCount = 0
        for FabricComponent in sorted(
            FabricComponents,
            key=lambda Value: (
                len(Value),
                tuple(sorted(Value)),
            ),
        ):
            ComponentIngressNodes = tuple(
                Value
                for Value in Problem.Fabric.IngressNodes
                if Value in FabricComponent
            )
            if len(ComponentIngressNodes) < 2:
                continue
            ComponentMinimum = min(
                Value[AxisIndex] for Value in ComponentIngressNodes
            )
            ComponentMaximum = max(
                Value[AxisIndex] for Value in ComponentIngressNodes
            )
            TerminalMinimum = (
                MinimumTerminalX
                if Axis == "X"
                else MinimumTerminalZ
            )
            TerminalMaximum = (
                MaximumTerminalX
                if Axis == "X"
                else MaximumTerminalZ
            )
            DesiredMinimum = max(
                ComponentMinimum,
                min(ComponentMaximum, TerminalMinimum),
            )
            DesiredMaximum = max(
                ComponentMinimum,
                min(ComponentMaximum, TerminalMaximum),
            )
            MinimumAttachmentCoordinate = (
                ComponentMinimum
                if CrossesComponentPartition
                else min(
                    (
                        abs(Value[AxisIndex] - DesiredMinimum),
                        Value[AxisIndex],
                    )
                    for Value in ComponentIngressNodes
                )[1]
            )
            MaximumAttachmentCoordinate = (
                ComponentMaximum
                if CrossesComponentPartition
                else min(
                    (
                        abs(Value[AxisIndex] - DesiredMaximum),
                        Value[AxisIndex],
                    )
                    for Value in ComponentIngressNodes
                )[1]
            )
            if (
                MinimumAttachmentCoordinate
                == MaximumAttachmentCoordinate
            ):
                continue
            if ComponentMinimum == ComponentMaximum:
                continue
            MinimumAttachments = tuple(
                Value
                for Value in ComponentIngressNodes
                if (
                    Value[AxisIndex]
                    == MinimumAttachmentCoordinate
                )
            )
            MaximumAttachments = tuple(
                Value
                for Value in ComponentIngressNodes
                if (
                    Value[AxisIndex]
                    == MaximumAttachmentCoordinate
                )
            )
            for MinimumAttachment in MinimumAttachments:
                MinimumPaths = tuple(
                    tuple(reversed(Path))
                    for Path in BuildComponentEgressPaths(
                        MinimumAttachment
                    )
                    if Path[-1][AxisIndex] < MinimumAttachment[AxisIndex]
                )
                for MaximumAttachment in MaximumAttachments:
                    MaximumPaths = tuple(
                        tuple(reversed(Path))
                        for Path in BuildComponentEgressPaths(
                            MaximumAttachment
                        )
                        if (
                            Path[-1][AxisIndex]
                            > MaximumAttachment[AxisIndex]
                        )
                    )
                    for MinimumPath in MinimumPaths:
                        for MaximumPath in MaximumPaths:
                            MinimumCandidate = CandidateForPath(
                                MinimumPath
                            )
                            MaximumCandidate = CandidateForPath(
                                MaximumPath
                            )
                            Domains = (
                                ComponentTerminalAccessDomain(
                                    Signal=Signal,
                                    Terminal=MinimumPath[0],
                                    TerminalRole=(
                                        "source"
                                        if SourceSide == "minimum"
                                        else "target"
                                    ),
                                    TerminalFingerprint=_StableFingerprint((
                                        "transit-minimum",
                                        _RelativeGeometry(MinimumPath),
                                    )),
                                    Candidates=(MinimumCandidate,),
                                ),
                                ComponentTerminalAccessDomain(
                                    Signal=Signal,
                                    Terminal=MaximumPath[0],
                                    TerminalRole=(
                                        "source"
                                        if SourceSide == "maximum"
                                        else "target"
                                    ),
                                    TerminalFingerprint=_StableFingerprint((
                                        "transit-maximum",
                                        _RelativeGeometry(MaximumPath),
                                    )),
                                    Candidates=(MaximumCandidate,),
                                ),
                            )
                            TransitProblem = replace(
                                Problem,
                                ComponentSignals=tuple(sorted((
                                    *Problem.ComponentSignals,
                                    Signal,
                                ))),
                            )
                            AttemptedCandidateCount += 1
                            Variant = _BuildNetVariant(
                                TransitProblem,
                                Signal,
                                Domains,
                                (
                                    MinimumCandidate,
                                    MaximumCandidate,
                                ),
                                RejectionCounts=RejectionCounts,
                                ImmutableConflictSignals=(
                                    ImmutableConflictSignals
                                ),
                            )
                            if Variant is not None:
                                Variants.append(Variant)
                            if (
                                len(Variants)
                                >= MaximumCandidatesPerSignal
                            ):
                                break
                        if len(Variants) >= MaximumCandidatesPerSignal:
                            break
                    if len(Variants) >= MaximumCandidatesPerSignal:
                        break
                if len(Variants) >= MaximumCandidatesPerSignal:
                    break
            if len(Variants) >= MaximumCandidatesPerSignal:
                break
        Candidates = PruneDominatedComponentNetVariants(Variants)
        PartitionFingerprint = _StableFingerprint((
            Axis,
            tuple(sorted(
                (
                    -1
                    if Value[AxisIndex] < GlobalMinimum
                    else 1
                    if Value[AxisIndex] > GlobalMaximum
                    else 0,
                    Value[
                        2 if Axis == "X" else 0
                    ] - (
                        MinimumZ if Axis == "X" else MinimumX
                    ),
                )
                for Value in Terminals
            )),
            tuple(
                _RelativeGeometry(Candidate.Nodes)
                for Candidate in Candidates
            ),
        ))
        Result.append(ComponentForeignTransitDomain(
            Signal=Signal,
            PartitionAxis=Axis,
            PartitionFingerprint=PartitionFingerprint,
            Candidates=Candidates,
            Complete=True,
            Diagnostics={
                "Mode": (
                    "through-component"
                    if CrossesComponentPartition
                    else "boundary-parallel"
                ),
                "AttemptedCandidateCount": AttemptedCandidateCount,
                "RejectionCounts": dict(sorted(
                    RejectionCounts.items()
                )),
                "ImmutableConflictSignals": sorted(
                    ImmutableConflictSignals
                ),
            },
        ))
    return tuple(sorted(
        Result,
        key=lambda Value: (
            Value.PartitionFingerprint,
            Value.PartitionAxis,
        ),
    ))


def BuildDeclaredComponentFeedthroughDomains(
    Problem: ComponentRoutingProblem,
    Feedthroughs: Iterable[ComponentFeedthroughContract],
) -> tuple[ComponentForeignTransitDomain, ...]:
    """Compile only the endpoint pairs in an explicit feedthrough contract."""
    FabricNodes = frozenset(Problem.Fabric.Nodes)
    Result = []
    for Feedthrough in sorted(
        Feedthroughs,
        key=lambda Value: (
            tuple(sorted(Value.EndpointPairs)),
            Value.Capacity,
        ),
    ):
        Candidates = []
        RejectionCounts: dict[str, int] = {}
        ImmutableConflictSignals: set[str] = set()
        ReservedPathNodes = tuple(Feedthrough.ReservedPathNodes)
        ReservedPathNodeSet = frozenset(ReservedPathNodes)
        ReservedPathEdges = frozenset(
            _NormalizedEdge(First, Second)
            for First, Second in zip(
                ReservedPathNodes,
                ReservedPathNodes[1:],
            )
        )
        ReservedPathValid = bool(
            not ReservedPathNodes
            or (
                len(ReservedPathNodes) >= 2
                and len(ReservedPathNodeSet) == len(ReservedPathNodes)
                and ReservedPathNodeSet <= FabricNodes
                and all(
                    Edge in Problem.Fabric.Edges
                    for Edge in ReservedPathEdges
                )
                and (
                    Problem.ResourceGraph is None
                    or all(
                        Problem.ResourceGraph.BuildPrimitive(
                            First,
                            Second,
                        ) is not None
                        for First, Second in zip(
                            ReservedPathNodes,
                            ReservedPathNodes[1:],
                        )
                    )
                )
            )
        )
        for Entry, Exit in sorted(set(Feedthrough.EndpointPairs)):
            if (
                Entry == Exit
                or Entry not in FabricNodes
                or Exit not in FabricNodes
                or not ReservedPathValid
                or (
                    ReservedPathNodes
                    and (
                        Entry != ReservedPathNodes[0]
                        or Exit != ReservedPathNodes[-1]
                    )
                )
            ):
                RejectionCounts["invalid-declared-endpoints"] = (
                    RejectionCounts.get(
                        "invalid-declared-endpoints",
                        0,
                    )
                    + 1
                )
                continue

            def AccessCandidate(
                Position: Position3,
            ) -> ComponentTerminalAccessCandidate:
                Claims = (
                    Problem.ResourceGraph.BuildRouteClaims((Position,))
                    if Problem.ResourceGraph is not None
                    else RoutingResourceClaims(
                        WireCells=frozenset((Position,)),
                        SupportCells=frozenset(((
                            Position[0],
                            Position[1] - 1,
                            Position[2],
                        ),)),
                        ElectricalCells=frozenset(
                            DefaultRedstoneRoutingTechnology
                            .BuildElectricalExclusions({Position})
                        ),
                    )
                )
                return ComponentTerminalAccessCandidate(
                    CandidateFingerprint=_StableFingerprint((
                        "declared-feedthrough-endpoint",
                        _RelativeGeometry((Position,)),
                        _ClaimsFingerprint(Claims),
                    )),
                    Attachment=Position,
                    Path=(Position,),
                    Claims=Claims,
                    Layer=3,
                    Cost=1,
                )

            EntryCandidate = AccessCandidate(Entry)
            ExitCandidate = AccessCandidate(Exit)
            Domains = (
                ComponentTerminalAccessDomain(
                    Signal=Feedthrough.Signal,
                    Terminal=Entry,
                    TerminalRole="feedthrough-entry",
                    TerminalFingerprint=_StableFingerprint((
                        "feedthrough-entry",
                        _RelativeGeometry((Entry, Exit)),
                    )),
                    Candidates=(EntryCandidate,),
                ),
                ComponentTerminalAccessDomain(
                    Signal=Feedthrough.Signal,
                    Terminal=Exit,
                    TerminalRole="feedthrough-exit",
                    TerminalFingerprint=_StableFingerprint((
                        "feedthrough-exit",
                        _RelativeGeometry((Entry, Exit)),
                    )),
                    Candidates=(ExitCandidate,),
                ),
            )
            TransitProblem = replace(
                Problem,
                ComponentSignals=tuple(sorted((
                    *Problem.ComponentSignals,
                    Feedthrough.Signal,
                ))),
            )
            Candidate = _BuildNetVariant(
                TransitProblem,
                Feedthrough.Signal,
                Domains,
                (EntryCandidate, ExitCandidate),
                RejectionCounts=RejectionCounts,
                ImmutableConflictSignals=ImmutableConflictSignals,
                PrecomputedFabricSubtree=(
                    (ReservedPathNodeSet, ReservedPathEdges)
                    if ReservedPathNodes
                    else None
                ),
            )
            if (
                Candidate is not None
                and (
                    not ReservedPathNodes
                    or Candidate.Nodes == ReservedPathNodeSet
                )
                and (
                    Feedthrough.Claims is None
                    or Candidate.Claims == Feedthrough.Claims
                )
            ):
                Candidates.append(Candidate)
            elif Candidate is not None:
                RejectionCounts["reserved-path-identity-mismatch"] = (
                    RejectionCounts.get(
                        "reserved-path-identity-mismatch",
                        0,
                    )
                    + 1
                )
        DeltaX = max(
            (
                abs(Exit[0] - Entry[0])
                for Entry, Exit in Feedthrough.EndpointPairs
            ),
            default=0,
        )
        DeltaZ = max(
            (
                abs(Exit[2] - Entry[2])
                for Entry, Exit in Feedthrough.EndpointPairs
            ),
            default=0,
        )
        Axis = "X" if DeltaX >= DeltaZ else "Z"
        Retained = PruneDominatedComponentNetVariants(Candidates)
        Result.append(ComponentForeignTransitDomain(
            Signal=Feedthrough.Signal,
            PartitionAxis=Axis,
            PartitionFingerprint=_StableFingerprint((
                "declared-feedthrough-v1",
                tuple(sorted(
                    _RelativeGeometry((Entry, Exit))
                    for Entry, Exit in Feedthrough.EndpointPairs
                )),
                Feedthrough.Capacity,
                tuple(
                    _RelativeGeometry(Value.Nodes)
                    for Value in Retained
                ),
            )),
            Candidates=Retained,
            Complete=bool(Feedthrough.EndpointPairs),
            Diagnostics={
                "Mode": "declared-feedthrough",
                "DeclaredEndpointPairCount": len(
                    Feedthrough.EndpointPairs
                ),
                "CandidateCount": len(Retained),
                "Capacity": Feedthrough.Capacity,
                "RejectionCounts": dict(sorted(
                    RejectionCounts.items()
                )),
                "ImmutableConflictSignals": sorted(
                    ImmutableConflictSignals
                ),
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    return tuple(sorted(
        Result,
        key=lambda Value: (
            Value.PartitionFingerprint,
            Value.PartitionAxis,
        ),
    ))


def PruneDominatedComponentNetVariants(
    Variants: Iterable[RoutedComponentNet],
) -> tuple[RoutedComponentNet, ...]:
    """Remove physically larger variants with identical external semantics."""
    Retained: list[RoutedComponentNet] = []
    for Candidate in sorted(
        Variants,
        key=lambda Value: (
            len(Value.Claims.ResourceIds),
            len(Value.Nodes),
            len(Value.Repeaters),
            Value.NetFingerprint,
        ),
    ):
        Dominated = any(
            Existing.ExportedPorts == Candidate.ExportedPorts
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
        key=lambda Value: Value.NetFingerprint,
    ))


def FindCompleteComponentNetUnsatSubset(
    VariantsBySignal: dict[str, tuple[RoutedComponentNet, ...]],
    *,
    Advance: Callable[[], bool] | None = None,
) -> tuple[str, ...] | None:
    """Prove that a complete subset of component nets cannot coexist.

    Returning a signal tuple is an exact monotone proof: adding the remaining
    component variables cannot make an already-unsatisfiable complete subset
    feasible. ``()`` means the subset is feasible, while ``None`` means the
    bounded proof was interrupted.
    """
    Ordered = tuple(sorted(
        VariantsBySignal.items(),
        key=lambda Value: (
            len(Value[1]),
            tuple(
                Variant.NetFingerprint
                for Variant in Value[1]
            ),
        ),
    ))
    if any(not Variants for _Signal, Variants in Ordered):
        return tuple(
            Signal for Signal, Variants in Ordered if not Variants
        )
    CompatibilityCache: dict[
        tuple[int, int, int],
        frozenset[int],
    ] = {}

    def CompatibleIndexes(
        FirstIndex: int,
        OptionIndex: int,
        SecondIndex: int,
    ) -> frozenset[int]:
        Key = (FirstIndex, OptionIndex, SecondIndex)
        Cached = CompatibilityCache.get(Key)
        if Cached is not None:
            return Cached
        FirstSignal, FirstVariants = Ordered[FirstIndex]
        SecondSignal, SecondVariants = Ordered[SecondIndex]
        FirstClaims = FirstVariants[OptionIndex].Claims
        Result = frozenset(
            SecondOptionIndex
            for SecondOptionIndex, SecondVariant
            in enumerate(SecondVariants)
            if ComponentClaimsCompatibleForOwners(
                FirstSignal,
                FirstClaims,
                SecondSignal,
                SecondVariant.Claims,
            )
        )
        CompatibilityCache[Key] = Result
        return Result

    Interrupted = object()

    def Enforce(
        Domains: dict[int, tuple[int, ...]],
    ) -> dict[int, tuple[int, ...]] | object:
        Result = dict(Domains)
        Queue = deque(
            (FirstIndex, SecondIndex)
            for FirstIndex in Result
            for SecondIndex in Result
            if FirstIndex != SecondIndex
        )
        while Queue:
            FirstIndex, SecondIndex = Queue.popleft()
            SecondDomain = frozenset(Result[SecondIndex])
            Retained = []
            for OptionIndex in Result[FirstIndex]:
                if Advance is not None and not Advance():
                    return Interrupted
                if SecondDomain.intersection(CompatibleIndexes(
                    FirstIndex,
                    OptionIndex,
                    SecondIndex,
                )):
                    Retained.append(OptionIndex)
            RetainedDomain = tuple(Retained)
            if RetainedDomain == Result[FirstIndex]:
                continue
            Result[FirstIndex] = RetainedDomain
            if not RetainedDomain:
                return Result
            Queue.extend(
                (OtherIndex, FirstIndex)
                for OtherIndex in Result
                if (
                    OtherIndex != FirstIndex
                    and OtherIndex != SecondIndex
                )
            )
        return Result

    def Search(
        Domains: dict[int, tuple[int, ...]],
    ) -> bool | object:
        Consistent = Enforce(Domains)
        if Consistent is Interrupted:
            return Interrupted
        assert isinstance(Consistent, dict)
        if any(not Domain for Domain in Consistent.values()):
            return False
        Unassigned = tuple(
            (len(Domain), VariableIndex, Domain)
            for VariableIndex, Domain in Consistent.items()
            if len(Domain) > 1
        )
        if not Unassigned:
            return True
        _Size, SelectedIndex, SelectedDomain = min(Unassigned)
        for OptionIndex in SelectedDomain:
            NextDomains = dict(Consistent)
            NextDomains[SelectedIndex] = (OptionIndex,)
            Result = Search(NextDomains)
            if Result is Interrupted or Result:
                return Result
        return False

    Result = Search({
        VariableIndex: tuple(range(len(Variants)))
        for VariableIndex, (_Signal, Variants)
        in enumerate(Ordered)
    })
    if Result is Interrupted:
        return None
    if Result:
        return ()
    return tuple(Signal for Signal, _Variants in Ordered)


def _SolveComponentRoutingProblemLegacy(
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ForbiddenAssignmentFingerprints: frozenset[str] = frozenset(),
    ForbiddenExportPortsBySignal: dict[
        str,
        tuple[Position3, ...],
    ] | None = None,
    ForbiddenForeignCandidateFingerprintsBySignal: dict[
        str,
        frozenset[str],
    ] | None = None,
    ForbiddenForeignAssignmentPairs: tuple[
        frozenset[tuple[str, Position3, str]], ...
    ] = (),
    VariantPortfolioCache: dict[
        tuple[str, str],
        tuple[
            tuple[RoutedComponentNet, ...],
            int,
            dict[str, int],
            frozenset[str],
            Position3,
        ],
    ] | None = None,
    NetVariantConstructionCache: dict[
        tuple[
            str,
            frozenset[Position3],
            frozenset[RoutingEdge],
            tuple[Position3, ...],
        ],
        RoutedComponentNet | None,
    ] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3],
        RoutingResourceClaims,
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[
        tuple[str, str],
        dict[str, object],
    ] | None = None,
    DiscoveryVariantLimit: int | None = 8,
    DiscoveryVariantLimitsBySignal: dict[
        str, int | None
    ] | None = None,
    RequiredForeignTransitSignals: frozenset[str] = frozenset(),
    StopAfterCompleteNetVariantPortfolioSignal: str | None = None,
    StaticPortfolioContextsBySignal: dict[
        str, CompleteComponentNetPortfolioStaticContext
    ] | None = None,
) -> ComponentRoutingSolveResult:
    """Legacy oracle that enumerates complete per-net tree portfolios."""
    Started = monotonic()
    ForbiddenExportPortsBySignal = (
        ForbiddenExportPortsBySignal or {}
    )
    ForbiddenForeignCandidateFingerprintsBySignal = (
        ForbiddenForeignCandidateFingerprintsBySignal or {}
    )
    if VariantPortfolioCache is None:
        VariantPortfolioCache = {}
    if NetVariantConstructionCache is None:
        NetVariantConstructionCache = {}
    if RouteClaimsConstructionCache is None:
        RouteClaimsConstructionCache = {}
    if NetVariantDiscoveryStateCache is None:
        NetVariantDiscoveryStateCache = {}
    if DiscoveryVariantLimitsBySignal is None:
        DiscoveryVariantLimitsBySignal = {}
    if StaticPortfolioContextsBySignal is None:
        StaticPortfolioContextsBySignal = {}
    ExpansionCount = 0
    DeclaredFeedthroughSignals = (
        Problem.Interface.DeclaredFeedthroughSignals
        if Problem.Interface is not None
        else frozenset()
    )
    ForeignTransitSignals = frozenset(
        Domain.Signal for Domain in Problem.ForeignTransitDomains
    )
    ImplicitForeignTransitSignals = tuple(sorted(
        ForeignTransitSignals - DeclaredFeedthroughSignals
        if Problem.Interface is not None
        else ()
    ))
    SolverDiagnostics: dict[str, object] = {
        "SolverKind": "complete-tree-portfolio-v1",
        "ExploredStateCount": 0,
        "PeakFrontierStateCount": 0,
        "DominatedStateCount": 0,
        "CompleteTreesMaterialized": 0,
        "ProblemFingerprint": Problem.ProblemFingerprint,
        "FabricFingerprint": Problem.Fabric.FabricFingerprint,
        "FabricTopologyKind": Problem.Fabric.TopologyKind,
        "FabricNodeCount": len(Problem.Fabric.Nodes),
        "FabricEdgeCount": len(Problem.Fabric.Edges),
        "ComponentSignalCount": len(Problem.ComponentSignals),
        "OwnedTerminalDomainCount": len(
            Problem.OwnedTerminalDomains
        ),
        "ForeignEscapeDomainCount": len(
            Problem.ForeignEscapeDomains
        ),
        "ForeignTransitDomainCount": len(
            Problem.ForeignTransitDomains
        ),
        "InterfaceFingerprint": (
            Problem.Interface.InterfaceFingerprint
            if Problem.Interface is not None
            else ""
        ),
        "DeclaredFeedthroughCount": len(
            DeclaredFeedthroughSignals
        ),
        "ImplicitForeignTransitDomainCount": len(
            ImplicitForeignTransitSignals
        ),
        "ImplicitForeignTransitSignals": list(
            ImplicitForeignTransitSignals
        ),
        "RequiredForeignTransitSignals": sorted(
            RequiredForeignTransitSignals
        ),
        "ExternalContinuationDomainCount": len(
            Problem.ExternalContinuationDomains
        ),
        "ForbiddenForeignAssignmentPairCount": len(
            ForbiddenForeignAssignmentPairs
        ),
        "NetVariantConstructionCacheInitialCount": len(
            NetVariantConstructionCache
        ),
        "RouteClaimsConstructionCacheInitialCount": len(
            RouteClaimsConstructionCache
        ),
        "NetVariantDiscoveryStateCacheInitialCount": len(
            NetVariantDiscoveryStateCache
        ),
    }

    def Advance(Phase: str) -> bool:
        nonlocal ExpansionCount
        ExpansionCount += 1
        if WorkCheck is not None and (
            StopAfterCompleteNetVariantPortfolioSignal is not None
            or ExpansionCount % 128 == 0
        ):
            WorkCheck({
                "Phase": Phase,
                "ExpansionCount": ExpansionCount,
            })
        return not (
            ExpansionCount > Problem.MaximumWork
            or (
                DeadlineSeconds is not None
                and monotonic() - Started >= DeadlineSeconds
            )
        )

    if ImplicitForeignTransitSignals or (
        Problem.Interface is not None
        and not RequiredForeignTransitSignals.issubset(
            DeclaredFeedthroughSignals
        )
    ):
        return ComponentRoutingSolveResult(
            Status="architectural-unsatisfiable",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "undeclared-foreign-transit",
                ImplicitForeignTransitSignals,
                tuple(sorted(RequiredForeignTransitSignals)),
            )),
            Detail=(
                "closed component contains undeclared foreign transit"
            ),
            Diagnostics=SolverDiagnostics,
        )
    if Problem.Interface is not None and not Problem.Interface.Complete:
        return ComponentRoutingSolveResult(
            Status="incomplete",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "incomplete-closed-interface",
            )),
            Detail="closed component interface is incomplete",
            Diagnostics=SolverDiagnostics,
        )
    if not Problem.Fabric.Complete:
        return ComponentRoutingSolveResult(
            Status="incomplete",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                Problem.Fabric.IncompleteReason,
            )),
            Detail=Problem.Fabric.IncompleteReason,
            Diagnostics=SolverDiagnostics,
        )
    if not Problem.DomainComplete:
        return ComponentRoutingSolveResult(
            Status="incomplete",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "incomplete-domain",
            )),
            Detail="one or more terminal domains are incomplete or empty",
            Diagnostics=SolverDiagnostics,
        )
    TransitDomainsBySignal = {
        Domain.Signal: Domain
        for Domain in Problem.ForeignTransitDomains
    }
    RequiredTransitDomains = tuple(
        TransitDomainsBySignal.get(Signal)
        for Signal in sorted(RequiredForeignTransitSignals)
    )
    MissingRequiredTransitSignals = tuple(
        Signal
        for Signal, Domain in zip(
            sorted(RequiredForeignTransitSignals),
            RequiredTransitDomains,
        )
        if Domain is None
    )
    if MissingRequiredTransitSignals:
        SolverDiagnostics["RequiredTransitPrecheck"] = {
            "Complete": True,
            "MissingSignals": list(
                MissingRequiredTransitSignals
            ),
            "PairCompatibility": [],
        }
        return ComponentRoutingSolveResult(
            Status="architectural-unsatisfiable",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "missing-required-transit-domain",
                len(MissingRequiredTransitSignals),
            )),
            Detail=(
                "required foreign transit has no finite component-fabric "
                "domain"
            ),
            Diagnostics=SolverDiagnostics,
        )
    RequiredTransitDomains = tuple(
        Domain
        for Domain in RequiredTransitDomains
        if Domain is not None
    )
    ImmutableTransitClaims = tuple((
        (Claim.Signal, Claim.Claims)
        for Claim in (
            *Problem.LocalClaims,
            *Problem.ImmutableClaims,
        )
        if Claim.Signal not in Problem.ComponentSignals
    ))
    TransitOptions = {
        Domain.Signal: tuple(
            Candidate
            for Candidate in Domain.Candidates
            if all(
                ComponentClaimsCompatibleForOwners(
                    Domain.Signal,
                    Candidate.Claims,
                    ImmutableOwner,
                    ImmutableClaims,
                )
                for ImmutableOwner, ImmutableClaims
                in ImmutableTransitClaims
            )
        )
        for Domain in RequiredTransitDomains
    }
    TransitPairCompatibility = []
    TransitPrecheckUnsatisfiable = any(
        not TransitOptions[Domain.Signal]
        for Domain in RequiredTransitDomains
    )
    for FirstOffset, FirstDomain in enumerate(
        RequiredTransitDomains
    ):
        for SecondDomain in RequiredTransitDomains[
            FirstOffset + 1:
        ]:
            CompatiblePairCount = sum(
                ComponentClaimsCompatibleForOwners(
                    FirstDomain.Signal,
                    First.Claims,
                    SecondDomain.Signal,
                    Second.Claims,
                )
                for First in TransitOptions[FirstDomain.Signal]
                for Second in TransitOptions[SecondDomain.Signal]
            )
            TransitPairCompatibility.append({
                "FirstSignal": FirstDomain.Signal,
                "SecondSignal": SecondDomain.Signal,
                "FirstCandidateCount": len(
                    TransitOptions[FirstDomain.Signal]
                ),
                "SecondCandidateCount": len(
                    TransitOptions[SecondDomain.Signal]
                ),
                "CompatiblePairCount": CompatiblePairCount,
                "StructuralPairFingerprint": _StableFingerprint((
                    tuple(
                        (
                            _RelativeGeometry(Candidate.Nodes),
                            _RelativeGeometry(
                                Position
                                for Position, _Facing
                                in Candidate.Repeaters
                            ),
                            _ClaimsFingerprint(Candidate.Claims),
                        )
                        for Candidate
                        in TransitOptions[FirstDomain.Signal]
                    ),
                    tuple(
                        (
                            _RelativeGeometry(Candidate.Nodes),
                            _RelativeGeometry(
                                Position
                                for Position, _Facing
                                in Candidate.Repeaters
                            ),
                            _ClaimsFingerprint(Candidate.Claims),
                        )
                        for Candidate
                        in TransitOptions[SecondDomain.Signal]
                    ),
                    CompatiblePairCount,
                )),
            })
            TransitPrecheckUnsatisfiable = bool(
                TransitPrecheckUnsatisfiable
                or CompatiblePairCount == 0
            )
    SolverDiagnostics["RequiredTransitPrecheck"] = {
        "Complete": True,
        "CandidateCounts": {
            Domain.Signal: len(TransitOptions[Domain.Signal])
            for Domain in RequiredTransitDomains
        },
        "PairCompatibility": TransitPairCompatibility,
    }
    if TransitPrecheckUnsatisfiable:
        return ComponentRoutingSolveResult(
            Status="architectural-unsatisfiable",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "required-transit-capacity-unsatisfiable",
                tuple(
                    Entry["StructuralPairFingerprint"]
                    for Entry in TransitPairCompatibility
                ),
                tuple(sorted(
                    len(TransitOptions[Domain.Signal])
                    for Domain in RequiredTransitDomains
                )),
            )),
            Detail=(
                "required foreign transits have no capacity-compatible "
                "component-fabric assignment"
            ),
            Diagnostics=SolverDiagnostics,
        )
    DomainsBySignal = {
        Signal: tuple(
            Domain
            for Domain in Problem.OwnedTerminalDomains
            if Domain.Signal == Signal
        )
        for Signal in Problem.ComponentSignals
    }

    if StopAfterCompleteNetVariantPortfolioSignal is not None:
        PortfolioSignal = str(StopAfterCompleteNetVariantPortfolioSignal)
        if PortfolioSignal not in DomainsBySignal:
            raise ValueError(
                "requested net portfolio signal is not owned by component"
            )
        DomainsBySignal = {
            PortfolioSignal: DomainsBySignal[PortfolioSignal]
        }
    VariantsBySignal: dict[str, tuple[RoutedComponentNet, ...]] = {}
    VariantDiagnosticsBySignal: dict[str, dict[str, object]] = {}
    DiscoveryIncompleteSignals: set[str] = set()
    FabricAdjacency = _BuildAdjacency(Problem.Fabric.Edges)
    FabricComponentByNode: dict[Position3, int] = {}
    for ComponentIndex, Start in enumerate(
        sorted(Problem.Fabric.Nodes)
    ):
        if Start in FabricComponentByNode:
            continue
        Pending = [Start]
        FabricComponentByNode[Start] = ComponentIndex
        while Pending:
            Current = Pending.pop()
            for Neighbor in FabricAdjacency.get(Current, ()):
                if Neighbor in FabricComponentByNode:
                    continue
                FabricComponentByNode[Neighbor] = ComponentIndex
                Pending.append(Neighbor)
    FabricParentCache: dict[
        Position3,
        dict[Position3, Position3 | None],
    ] = {}
    ImmutableForeignClaimsForAccess = tuple(
        (Claim.Signal, Claim.Claims)
        for Claim in Problem.ImmutableClaims
        if Claim.Signal not in Problem.ComponentSignals
    )

    def AccessClaimsContextFingerprint(Signal: str) -> str:
        ClaimsBySignal = (
            *ImmutableForeignClaimsForAccess,
            *(
                (str(ReservedSignal), Claims)
                for ReservedSignal, Claims
                in Problem.ReservedGlobalClaimsBySignal
                if str(ReservedSignal) != Signal
            ),
        )
        return _StableFingerprint(tuple(sorted(
            (
                str(ClaimSignal),
                tuple(sorted(map(str, Claims.ResourceIds))),
            )
            for ClaimSignal, Claims in ClaimsBySignal
        )))

    def AccessCandidateBlockers(
        Signal: str,
        Candidate: ComponentTerminalAccessCandidate,
    ) -> frozenset[str]:
        CacheKey = (
            AccessClaimsContextFingerprint(Signal),
            Signal,
            Candidate.Path,
        )
        Cached = ImmutableAccessConflictCache.get(CacheKey)
        if Cached is not None:
            return Cached
        Result = frozenset(
            ClaimSignal
            for ClaimSignal, Claims
            in (
                *ImmutableForeignClaimsForAccess,
                *(
                    (str(ReservedSignal), Claims)
                    for ReservedSignal, Claims
                    in Problem.ReservedGlobalClaimsBySignal
                    if str(ReservedSignal) != Signal
                ),
            )
            if ComponentClaimsConflict(Candidate.Claims, Claims)
        )
        ImmutableAccessConflictCache[CacheKey] = Result
        return Result
    ImmutableAccessConflictCache: dict[
        tuple[str, str, tuple[Position3, ...]],
        frozenset[str],
    ] = {}
    ReservedClaimsBySignal = {
        str(Signal): Claims
        for Signal, Claims in Problem.ReservedGlobalClaimsBySignal
    }
    PhysicalPortClaimsBySignal = {
        str(Port.Signal): Port.Claims
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
    }

    def ClassifyReservedBlockers(
        Claims: RoutingResourceClaims,
        Blockers: Iterable[str],
        PortContractBlockers: set[str],
        GlobalRouteBlockers: set[str],
    ) -> None:
        """Separate immutable seam conflicts from selected channel conflicts."""
        for BlockerValue in Blockers:
            Blocker = str(BlockerValue)
            if Blocker not in ReservedClaimsBySignal:
                continue
            PortClaims = PhysicalPortClaimsBySignal.get(Blocker)
            if (
                PortClaims is not None
                and ComponentClaimsConflict(Claims, PortClaims)
            ):
                PortContractBlockers.add(Blocker)
            else:
                GlobalRouteBlockers.add(Blocker)
    LocalClaimsBySignal = {
        Signal: tuple(
            Claim
            for Claim in Problem.LocalClaims
            if Claim.Signal == Signal
        )
        for Signal in Problem.ComponentSignals
    }
    NetVariantTopologyCache = NetVariantConstructionCache
    RouteClaimsCache = RouteClaimsConstructionCache
    def ConnectedAccessCombinationEstimate(
        Domains: tuple[ComponentTerminalAccessDomain, ...],
    ) -> int:
        CountsByDomain = tuple(
            {
                ComponentIndex: sum(
                    FabricComponentByNode.get(
                        Candidate.Attachment
                    ) == ComponentIndex
                    for Candidate in Domain.Candidates
                )
                for ComponentIndex in set(
                    FabricComponentByNode.get(
                        Candidate.Attachment
                    )
                    for Candidate in Domain.Candidates
                    if Candidate.Attachment in FabricComponentByNode
                )
            }
            for Domain in Domains
        )
        CommonComponents = (
            set(CountsByDomain[0])
            if CountsByDomain
            else set()
        )
        for Counts in CountsByDomain[1:]:
            CommonComponents.intersection_update(Counts)
        return sum(
            ProductIntegers(
                Counts[ComponentIndex]
                for Counts in CountsByDomain
            )
            for ComponentIndex in CommonComponents
        )

    OrderedDomainItems = tuple(sorted(
        DomainsBySignal.items(),
        key=lambda Value: (
            ConnectedAccessCombinationEstimate(Value[1]),
            tuple(sorted(
                (
                    Domain.TerminalRole,
                    Domain.TerminalFingerprint,
                    len(Domain.Candidates),
                )
                for Domain in Value[1]
            )),
        ),
    ))
    CompleteProofVariants: dict[
        str, tuple[RoutedComponentNet, ...]
    ] = {}
    CompleteTreeMaterializationCount = 0
    for Signal, Domains in OrderedDomainItems:
        ReservedPortContractConflictSignals: set[str] = set()
        ReservedGlobalRouteConflictSignals: set[str] = set()
        PhysicalPort = next(
            (
                Value
                for Value in (
                    Problem.Interface.PhysicalPortReservations
                    if Problem.Interface is not None
                    else ()
                )
                if Value.Signal == Signal
            ),
            None,
        )
        CertifiedCandidateFingerprints = frozenset(
            getattr(
                PhysicalPort,
                "OwnedCandidateFingerprints",
                (),
            )
        )
        CanonicalAccessStateCount = 0
        DuplicateCanonicalAccessStateCount = 0
        NetVariantBuildCount = 0
        EffectiveDiscoveryVariantLimit = (
            DiscoveryVariantLimitsBySignal.get(
                Signal,
                DiscoveryVariantLimit,
            )
        )
        ComponentOrigin = _ComponentOrigin(Problem)
        StructuralPortfolioFingerprint = (
            _ComponentNetPortfolioStructuralFingerprint(
                Problem,
                Signal,
                Domains,
                ComponentOrigin,
                StaticPortfolioContextsBySignal.get(Signal),
            )
        )
        StructuralCacheKey = (
            "component-net-translation-v1",
            StructuralPortfolioFingerprint,
        )
        ExactCacheKey = (Problem.ProblemFingerprint, Signal)
        CachedPortfolio = VariantPortfolioCache.get(ExactCacheKey)
        PortfolioCacheKind = "exact"
        if CachedPortfolio is None:
            CachedPortfolio = VariantPortfolioCache.get(
                StructuralCacheKey
            )
            PortfolioCacheKind = "structural"
        PortfolioTranslationDelta = (0, 0, 0)
        PortfolioTranslationValidated = False
        if CachedPortfolio is not None:
            (
                CachedVariants,
                CombinationCount,
                CachedRejections,
                CachedImmutableConflicts,
                CachedOrigin,
            ) = CachedPortfolio
            CurrentImmutableConflicts = frozenset(
                Blocker
                for Domain in Domains
                for Candidate in Domain.Candidates
                for Blocker in AccessCandidateBlockers(
                    Signal,
                    Candidate,
                )
            )
            for Domain in Domains:
                for Candidate in Domain.Candidates:
                    CandidateBlockers = AccessCandidateBlockers(
                        Signal,
                        Candidate,
                    )
                    ClassifyReservedBlockers(
                        Candidate.Claims,
                        CandidateBlockers,
                        ReservedPortContractConflictSignals,
                        ReservedGlobalRouteConflictSignals,
                    )
            if (
                PortfolioCacheKind == "structural"
                and (
                    CachedImmutableConflicts
                    or CurrentImmutableConflicts
                )
            ):
                CachedPortfolio = None
            PortfolioTranslationDelta = (
                ComponentOrigin[0] - CachedOrigin[0],
                ComponentOrigin[1] - CachedOrigin[1],
                ComponentOrigin[2] - CachedOrigin[2],
            )
            if CachedPortfolio is not None:
                TranslatedVariants = _TranslateAndValidateNetPortfolio(
                    CachedVariants,
                    SourceOrigin=CachedOrigin,
                    TargetOrigin=ComponentOrigin,
                    Signal=Signal,
                    Domains=Domains,
                    Problem=Problem,
                )
                if TranslatedVariants is None:
                    CachedPortfolio = None
                else:
                    EnumeratedVariants = TranslatedVariants
                    RejectionCounts = dict(CachedRejections)
                    ImmutableConflictSignals = set(
                        CachedImmutableConflicts
                    )
                    PortfolioTranslationValidated = True
        if CachedPortfolio is None:
            DiscoveryStateKey = (
                Problem.ProblemFingerprint,
                Signal,
            )
            CompleteProofContextFingerprint = _StableFingerprint(tuple(
                sorted(
                    (
                        CompleteSignal,
                        tuple(
                            Variant.NetFingerprint
                            for Variant in CompleteVariants
                        ),
                    )
                    for CompleteSignal, CompleteVariants
                    in CompleteProofVariants.items()
                )
            ))
            PriorDiscoveryState = (
                NetVariantDiscoveryStateCache.get(
                    DiscoveryStateKey
                )
            )
            if (
                PriorDiscoveryState is not None
                and PriorDiscoveryState.get(
                    "CompleteProofContextFingerprint",
                    "",
                )
                != CompleteProofContextFingerprint
            ):
                PriorDiscoveryState = None
            VariantsByFingerprint = dict(
                (
                    PriorDiscoveryState.get("Variants", {})
                    if PriorDiscoveryState is not None
                    else {}
                )
            )
            RejectionCounts = dict(
                (
                    PriorDiscoveryState.get(
                        "RejectionCounts",
                        {},
                    )
                    if PriorDiscoveryState is not None
                    else {}
                )
            )
            ImmutableConflictSignals = set(
                (
                    PriorDiscoveryState.get(
                        "ImmutableConflictSignals",
                        (),
                    )
                    if PriorDiscoveryState is not None
                    else ()
                )
            )
            ResumeEgressStateCount = int(
                (
                    PriorDiscoveryState.get(
                        "ProcessedEgressStateCount",
                        0,
                    )
                    if PriorDiscoveryState is not None
                    else 0
                )
            )
            ProcessedEgressStateCount = 0
            CombinationCount = 0
            StopDiscovery = False
            ImmutableFilteredDomainCandidates = []
            for Domain in Domains:
                FilteredCandidates = []
                for Candidate in Domain.Candidates:
                    Blockers = AccessCandidateBlockers(
                        Signal,
                        Candidate,
                    )
                    if Blockers:
                        ImmutableConflictSignals.update(Blockers)
                        ClassifyReservedBlockers(
                            Candidate.Claims,
                            Blockers,
                            ReservedPortContractConflictSignals,
                            ReservedGlobalRouteConflictSignals,
                        )
                        continue
                    FilteredCandidates.append(Candidate)
                ImmutableFilteredDomainCandidates.append(
                    tuple(FilteredCandidates)
                )
            ViableCompleteVariantsBySignal = {}
            SingletonFabricClaims = {
                Node: (
                    Problem.ResourceGraph.BuildRouteClaims(
                        frozenset((Node,))
                    )
                    if Problem.ResourceGraph is not None
                    else RoutingResourceClaims(
                        WireCells=frozenset((Node,)),
                        SupportCells=frozenset(((
                            Node[0],
                            Node[1] - 1,
                            Node[2],
                        ),)),
                        ElectricalCells=frozenset(
                            DefaultRedstoneRoutingTechnology
                            .BuildElectricalExclusions({Node})
                        ),
                    )
                )
                for Node in Problem.Fabric.Nodes
            }
            for CompleteSignal, CompleteVariants in (
                CompleteProofVariants.items()
            ):
                def CompleteVariantSupportsDomains(
                    CompleteVariant: RoutedComponentNet,
                ) -> bool:
                    CertainlyBlockedNodes = frozenset(
                        Node
                        for Node, NodeClaims
                        in SingletonFabricClaims.items()
                        if ComponentClaimsConflict(
                            NodeClaims,
                            CompleteVariant.Claims,
                        )
                    )
                    AllowedNodes = (
                        frozenset(Problem.Fabric.Nodes)
                        - CertainlyBlockedNodes
                    )
                    AllowedComponentByNode: dict[
                        Position3, int
                    ] = {}
                    for AllowedNode in sorted(AllowedNodes):
                        if AllowedNode in AllowedComponentByNode:
                            continue
                        ComponentIndex = len(set(
                            AllowedComponentByNode.values()
                        ))
                        PendingNodes = [AllowedNode]
                        AllowedComponentByNode[
                            AllowedNode
                        ] = ComponentIndex
                        while PendingNodes:
                            CurrentNode = PendingNodes.pop()
                            for Neighbor in FabricAdjacency.get(
                                CurrentNode,
                                (),
                            ):
                                if (
                                    Neighbor not in AllowedNodes
                                    or Neighbor
                                    in AllowedComponentByNode
                                ):
                                    continue
                                AllowedComponentByNode[
                                    Neighbor
                                ] = ComponentIndex
                                PendingNodes.append(Neighbor)
                    CommonComponentIndexes: set[int] | None = None
                    for DomainCandidates in (
                        ImmutableFilteredDomainCandidates
                    ):
                        DomainComponentIndexes = {
                            AllowedComponentByNode[
                                Candidate.Attachment
                            ]
                            for Candidate in DomainCandidates
                            if (
                                Candidate.Attachment
                                in AllowedComponentByNode
                                and ComponentClaimsCompatibleForOwners(
                                    Signal,
                                    Candidate.Claims,
                                    CompleteSignal,
                                    CompleteVariant.Claims,
                                )
                            )
                        }
                        if not DomainComponentIndexes:
                            return False
                        if CommonComponentIndexes is None:
                            CommonComponentIndexes = set(
                                DomainComponentIndexes
                            )
                        else:
                            CommonComponentIndexes.intersection_update(
                                DomainComponentIndexes
                            )
                        if not CommonComponentIndexes:
                            return False
                    return bool(CommonComponentIndexes)

                ViableCompleteVariants = tuple(
                    CompleteVariant
                    for CompleteVariant in CompleteVariants
                    if CompleteVariantSupportsDomains(
                        CompleteVariant
                    )
                )
                if not ViableCompleteVariants:
                    CompleteVariantDiagnostics = dict(
                        VariantDiagnosticsBySignal.get(
                            CompleteSignal,
                            {},
                        )
                    )
                    CompletePortContractBlockers = frozenset(map(
                        str,
                        CompleteVariantDiagnostics.get(
                            "ReservedPortContractConflictSignals",
                            (),
                        ),
                    ))
                    CompleteGlobalRouteBlockers = frozenset(map(
                        str,
                        CompleteVariantDiagnostics.get(
                            "ReservedGlobalRouteConflictSignals",
                            (),
                        ),
                    ))
                    PortContractBlockers = frozenset({
                        *ReservedPortContractConflictSignals,
                        *CompletePortContractBlockers,
                    })
                    GlobalRouteBlockers = frozenset({
                        *ReservedGlobalRouteConflictSignals,
                        *CompleteGlobalRouteBlockers,
                    })
                    LocalCoreSignals = tuple(sorted({
                        Signal,
                        CompleteSignal,
                        *PortContractBlockers,
                    }))
                    SolverDiagnostics[
                        "VariantDiagnosticsBySignal"
                    ] = VariantDiagnosticsBySignal
                    SolverDiagnostics[
                        "CompleteNetAccessUnsatSignals"
                    ] = sorted((Signal, CompleteSignal))
                    SolverDiagnostics["LocalUnsatCoreComplete"] = True
                    SolverDiagnostics["LocalUnsatCoreSignals"] = list(
                        LocalCoreSignals
                    )
                    SolverDiagnostics["LocalUnsatCoreKind"] = (
                        "complete-opposing-net-access-pair"
                    )
                    SolverDiagnostics[
                        "LocalUnsatCoreCurrentSignal"
                    ] = Signal
                    SolverDiagnostics[
                        "LocalUnsatCoreCompleteSignal"
                    ] = CompleteSignal
                    SolverDiagnostics[
                        "LocalUnsatCorePortContractBlockers"
                    ] = sorted(PortContractBlockers)
                    SolverDiagnostics[
                        "LocalUnsatCoreGlobalRouteBlockers"
                    ] = sorted(GlobalRouteBlockers)
                    SolverDiagnostics[
                        "CompleteNetAccessUnsatDomainSizes"
                    ] = [
                        len(Values)
                        for Values
                        in ImmutableFilteredDomainCandidates
                    ]
                    SolverDiagnostics[
                        "LocalUnsatCoreFingerprint"
                    ] = _StableFingerprint((
                        "complete-opposing-net-access-pair-v2",
                        LocalCoreSignals,
                        tuple(sorted(PortContractBlockers)),
                        tuple(sorted(GlobalRouteBlockers)),
                        StructuralPortfolioFingerprint,
                        tuple(
                            Variant.NetFingerprint
                            for Variant in CompleteVariants
                        ),
                        tuple(
                            Domain.TerminalFingerprint
                            for Domain in Domains
                        ),
                    ))
                    return ComponentRoutingSolveResult(
                        Status="architectural-unsatisfiable",
                        ProofFingerprint=_StableFingerprint((
                            Problem.ProblemFingerprint,
                            "complete-net-access-domain-unsatisfiable",
                            LocalCoreSignals,
                            tuple(
                                Variant.NetFingerprint
                                for Variant in CompleteVariants
                            ),
                            tuple(
                                Domain.TerminalFingerprint
                                for Domain in Domains
                            ),
                        )),
                        ExpansionCount=ExpansionCount,
                        Detail=(
                            "no complete opposing-net variant supports "
                            "every terminal access domain"
                        ),
                        Diagnostics=SolverDiagnostics,
                    )
                ViableCompleteVariantsBySignal[
                    CompleteSignal
                ] = ViableCompleteVariants
            FilteredDomainCandidatesValues = []
            for DomainCandidates in ImmutableFilteredDomainCandidates:
                FilteredCandidates = []
                for Candidate in DomainCandidates:
                    if (
                        CertifiedCandidateFingerprints
                        and Candidate.CandidateFingerprint
                        not in CertifiedCandidateFingerprints
                    ):
                        RejectionCounts[
                            "outside-certified-port-access-domain"
                        ] = RejectionCounts.get(
                            "outside-certified-port-access-domain",
                            0,
                        ) + 1
                        continue
                    CompleteNetBlockers = frozenset(
                        CompleteSignal
                        for CompleteSignal, CompleteVariants
                        in ViableCompleteVariantsBySignal.items()
                        if all(
                            not ComponentClaimsCompatibleForOwners(
                                Signal,
                                Candidate.Claims,
                                CompleteSignal,
                                CompleteVariant.Claims,
                            )
                            for CompleteVariant in CompleteVariants
                        )
                    )
                    if CompleteNetBlockers:
                        RejectionCounts[
                            "complete-net-access-capacity"
                        ] = (
                            RejectionCounts.get(
                                "complete-net-access-capacity",
                                0,
                            )
                            + 1
                        )
                        ImmutableConflictSignals.update(
                            CompleteNetBlockers
                        )
                        continue
                    FilteredCandidates.append(Candidate)
                FilteredDomainCandidatesValues.append(
                    tuple(FilteredCandidates)
                )
            FilteredDomainCandidates = tuple(
                FilteredDomainCandidatesValues
            )
            CandidateDomainsByComponent = tuple(
                {
                    ComponentIndex: tuple(
                        Candidate
                        for Candidate in DomainCandidates
                        if FabricComponentByNode.get(
                            Candidate.Attachment
                        ) == ComponentIndex
                    )
                    for ComponentIndex in sorted(set(
                        FabricComponentByNode.get(
                            Candidate.Attachment
                        )
                        for Candidate in DomainCandidates
                        if Candidate.Attachment
                        in FabricComponentByNode
                    ))
                }
                for DomainCandidates in FilteredDomainCandidates
            )
            CommonComponentIndexes = (
                set(CandidateDomainsByComponent[0])
                if CandidateDomainsByComponent
                else set()
            )
            for Values in CandidateDomainsByComponent[1:]:
                CommonComponentIndexes.intersection_update(Values)
            GuidedCombinationCount = 0
            SeenCanonicalAccessStates: set[
                tuple[object, ...]
            ] = set(
                PriorDiscoveryState.get(
                    "CanonicalAccessStates",
                    (),
                )
                if PriorDiscoveryState is not None
                else ()
            )
            CanonicalAccessStateCount = len(
                SeenCanonicalAccessStates
            )

            def OrderedCandidateCombinations(
                ComponentIndex: int,
            ) -> Iterable[
                tuple[ComponentTerminalAccessCandidate, ...]
            ]:
                nonlocal GuidedCombinationCount
                SeenCombinationFingerprints: set[
                    tuple[str, ...]
                ] = set()
                if ViableCompleteVariantsBySignal:
                    CompleteVariantGroups = tuple(
                        ViableCompleteVariantsBySignal[CompleteSignal]
                        for CompleteSignal in sorted(
                            ViableCompleteVariantsBySignal
                        )
                    )
                    for CompleteVariantSelection in product(
                        *CompleteVariantGroups
                    ):
                        GuidedDomains = tuple(
                            tuple(
                                Candidate
                                for Candidate in Values[ComponentIndex]
                                if all(
                                    ComponentClaimsCompatibleForOwners(
                                        Signal,
                                        Candidate.Claims,
                                        CompleteVariant.Signal,
                                        CompleteVariant.Claims,
                                    )
                                    for CompleteVariant
                                    in CompleteVariantSelection
                                )
                            )
                            for Values in CandidateDomainsByComponent
                        )
                        if any(not Values for Values in GuidedDomains):
                            continue
                        for Candidates in islice(
                            product(*GuidedDomains),
                            4,
                        ):
                            Fingerprint = tuple(
                                Candidate.CandidateFingerprint
                                for Candidate in Candidates
                            )
                            if Fingerprint in SeenCombinationFingerprints:
                                continue
                            SeenCombinationFingerprints.add(Fingerprint)
                            GuidedCombinationCount += 1
                            yield Candidates
                            if GuidedCombinationCount >= 96:
                                break
                        if GuidedCombinationCount >= 96:
                            break
                for Candidates in product(*(
                    Values[ComponentIndex]
                    for Values in CandidateDomainsByComponent
                )):
                    Fingerprint = tuple(
                        Candidate.CandidateFingerprint
                        for Candidate in Candidates
                    )
                    if Fingerprint in SeenCombinationFingerprints:
                        continue
                    SeenCombinationFingerprints.add(Fingerprint)
                    yield Candidates

            for ComponentIndex in sorted(CommonComponentIndexes):
                for Candidates in OrderedCandidateCombinations(
                    ComponentIndex
                ):
                    CombinationCount += 1
                    HasExternalContinuation = any(
                        Value[0] == Signal
                        for Value
                        in Problem.ExternalContinuationTerminals
                    )
                    if HasExternalContinuation:
                        EgressPaths = (
                            (PhysicalPort.LocalPath,)
                            if PhysicalPort is not None
                            else tuple(
                                EgressPath
                                for Attachment in sorted({
                                    Candidate.Attachment
                                    for Candidate in Candidates
                                })
                                for EgressPath
                                in BuildComponentEgressPaths(
                                    Attachment
                                )
                            )
                        )
                    else:
                        EgressPaths = ((),)
                    AdvancedCombination = False
                    for EgressPath in EgressPaths:
                        ProcessedEgressStateCount += 1
                        if (
                            ProcessedEgressStateCount
                            <= ResumeEgressStateCount
                        ):
                            continue
                        FabricSubtree = _UniqueFabricSubtree(
                            Problem.Fabric,
                            (
                                *(
                                    Candidate.Attachment
                                    for Candidate in Candidates
                                ),
                                *((EgressPath[0],)
                                  if EgressPath else ()),
                            ),
                            Adjacency=FabricAdjacency,
                            ParentCache=FabricParentCache,
                        )
                        CanonicalAccessState = (
                            _BuildCanonicalAccessCombinationKey(
                                Problem,
                                Signal,
                                Domains,
                                tuple(Candidates),
                                EgressPath,
                                ComponentIndex,
                                FabricSubtree,
                                LocalClaimsBySignal.get(Signal, ()),
                            )
                            if FabricSubtree is not None
                            else None
                        )
                        if (
                            CanonicalAccessState is not None
                            and CanonicalAccessState
                            in SeenCanonicalAccessStates
                        ):
                            DuplicateCanonicalAccessStateCount += 1
                            continue
                        if CanonicalAccessState is not None:
                            SeenCanonicalAccessStates.add(
                                CanonicalAccessState
                            )
                            CanonicalAccessStateCount += 1
                        if not AdvancedCombination:
                            if not Advance("net-variant"):
                                NetVariantDiscoveryStateCache[
                                    DiscoveryStateKey
                                ] = {
                                    "Variants": dict(
                                        VariantsByFingerprint
                                    ),
                                    "RejectionCounts": dict(
                                        RejectionCounts
                                    ),
                                    "ImmutableConflictSignals": (
                                        frozenset(
                                            ImmutableConflictSignals
                                        )
                                    ),
                                    "ProcessedEgressStateCount": (
                                        ProcessedEgressStateCount - 1
                                    ),
                                    "CombinationCount": (
                                        CombinationCount
                                    ),
                                    "CanonicalAccessStates": frozenset(
                                        SeenCanonicalAccessStates
                                    ),
                                    "CompleteProofContextFingerprint": (
                                        CompleteProofContextFingerprint
                                    ),
                                }
                                SolverDiagnostics[
                                    "VariantDiagnosticsBySignal"
                                ] = VariantDiagnosticsBySignal
                                SolverDiagnostics[
                                    "FabricParentCacheRootCount"
                                ] = len(FabricParentCache)
                                return ComponentRoutingSolveResult(
                                    Status="incomplete",
                                    ProofFingerprint=(
                                        _StableFingerprint((
                                            Problem.ProblemFingerprint,
                                            "work-or-deadline",
                                            ExpansionCount,
                                        ))
                                    ),
                                    ExpansionCount=ExpansionCount,
                                    Detail=(
                                        "component state work or "
                                        "deadline cap reached"
                                    ),
                                    Diagnostics=SolverDiagnostics,
                                )
                            AdvancedCombination = True
                        NetVariantBuildCount += 1
                        Variant = _BuildNetVariant(
                            Problem,
                            Signal,
                            Domains,
                            tuple(Candidates),
                            EgressPath,
                            RejectionCounts,
                            ImmutableConflictSignals,
                            FabricAdjacency,
                            FabricParentCache,
                            ImmutableAccessConflictCache,
                            LocalClaimsBySignal,
                            NetVariantTopologyCache,
                            RouteClaimsCache,
                            PrecomputedFabricSubtree=FabricSubtree,
                            ReservedPortContractConflictSignals=(
                                ReservedPortContractConflictSignals
                            ),
                            ReservedGlobalRouteConflictSignals=(
                                ReservedGlobalRouteConflictSignals
                            ),
                        )
                        if Variant is not None:
                            CompleteTreeMaterializationCount += 1
                            SolverDiagnostics[
                                "CompleteTreesMaterialized"
                            ] = CompleteTreeMaterializationCount
                            CompleteNetRouteBlockers = frozenset(
                                CompleteSignal
                                for CompleteSignal, CompleteVariants
                                in CompleteProofVariants.items()
                                if all(
                                    not ComponentClaimsCompatibleForOwners(
                                        Signal,
                                        Variant.Claims,
                                        CompleteSignal,
                                        CompleteVariant.Claims,
                                    )
                                    for CompleteVariant in CompleteVariants
                                )
                            )
                            if CompleteNetRouteBlockers:
                                RejectionCounts[
                                    "complete-net-route-capacity"
                                ] = (
                                    RejectionCounts.get(
                                        "complete-net-route-capacity",
                                        0,
                                    )
                                    + 1
                                )
                                ImmutableConflictSignals.update(
                                    CompleteNetRouteBlockers
                                )
                                continue
                            VariantsByFingerprint.setdefault(
                                Variant.NetFingerprint,
                                Variant,
                            )
                            if (
                                EffectiveDiscoveryVariantLimit is not None
                                and len(VariantsByFingerprint)
                                >= EffectiveDiscoveryVariantLimit
                            ):
                                StopDiscovery = True
                                break
                    if StopDiscovery:
                        break
                if StopDiscovery:
                    break
            EnumeratedVariants = tuple(
                VariantsByFingerprint[Fingerprint]
                for Fingerprint in sorted(VariantsByFingerprint)
            )
            if StopDiscovery:
                DiscoveryIncompleteSignals.add(Signal)
                NetVariantDiscoveryStateCache[
                    DiscoveryStateKey
                ] = {
                    "Variants": dict(VariantsByFingerprint),
                    "RejectionCounts": dict(RejectionCounts),
                    "ImmutableConflictSignals": frozenset(
                        ImmutableConflictSignals
                    ),
                    "ProcessedEgressStateCount": (
                        ProcessedEgressStateCount
                    ),
                    "CombinationCount": CombinationCount,
                    "CanonicalAccessStates": frozenset(
                        SeenCanonicalAccessStates
                    ),
                    "CompleteProofContextFingerprint": (
                        CompleteProofContextFingerprint
                    ),
                }
            else:
                NetVariantDiscoveryStateCache.pop(
                    DiscoveryStateKey,
                    None,
                )
                CachedValue = (
                    EnumeratedVariants,
                    CombinationCount,
                    dict(RejectionCounts),
                    frozenset(ImmutableConflictSignals),
                    ComponentOrigin,
                )
                VariantPortfolioCache[ExactCacheKey] = CachedValue
                if not ImmutableConflictSignals:
                    VariantPortfolioCache[
                        StructuralCacheKey
                    ] = CachedValue
        ExternalPositions = tuple(
            Terminal
            for ExternalSignal, Terminal, _Role
            in Problem.ExternalContinuationTerminals
            if ExternalSignal == Signal
        )

        def ContinuationCost(
            Value: RoutedComponentNet,
        ) -> int:
            if not ExternalPositions or not Value.ExportedPorts:
                return 0
            return sum(
                min(
                    abs(Port[0] - Terminal[0])
                    + abs(Port[1] - Terminal[1])
                    + abs(Port[2] - Terminal[2])
                    for Port in Value.ExportedPorts
                )
                for Terminal in ExternalPositions
            )

        RankedVariants = tuple(sorted(
            PruneDominatedComponentNetVariants(EnumeratedVariants),
            key=lambda Value: (
                ContinuationCost(Value),
                Value.NetFingerprint,
            ),
        ))
        ForbiddenPorts = ForbiddenExportPortsBySignal.get(Signal)
        VariantsBySignal[Signal] = tuple(
            Value
            for Value in RankedVariants
            if (
                ForbiddenPorts is None
                or Value.ExportedPorts != ForbiddenPorts
            )
        )
        VariantDiagnosticsBySignal[Signal] = {
            "PortfolioCacheHit": CachedPortfolio is not None,
            "PortfolioCacheKind": (
                PortfolioCacheKind
                if CachedPortfolio is not None
                else "miss"
            ),
            "StructuralPortfolioFingerprint": (
                StructuralPortfolioFingerprint
            ),
            "PortfolioTranslationDelta": list(
                PortfolioTranslationDelta
            ),
            "PortfolioTranslationValidated": (
                PortfolioTranslationValidated
            ),
            "ResumedEgressStateCount": (
                ResumeEgressStateCount
                if CachedPortfolio is None
                else 0
            ),
            "TerminalDomainSizes": [
                len(Domain.Candidates) for Domain in Domains
            ],
            "AccessCombinationCount": CombinationCount,
            "GuidedCombinationCount": (
                GuidedCombinationCount
                if CachedPortfolio is None
                else 0
            ),
            "CanonicalAccessStateCount": CanonicalAccessStateCount,
            "DuplicateCanonicalAccessStateCount": (
                DuplicateCanonicalAccessStateCount
            ),
            "NetVariantBuildCount": NetVariantBuildCount,
            "RoutedVariantCount": len(
                VariantsBySignal[Signal]
            ),
            "EnumeratedPhysicalVariantCount": len(
                EnumeratedVariants
            ),
            "DominatedVariantCount": (
                len(EnumeratedVariants)
                - len(RankedVariants)
            ),
            "ForbiddenExportPortVariantCount": (
                len(RankedVariants)
                - len(VariantsBySignal[Signal])
            ),
            "ContinuationCostRange": (
                [
                    min(map(
                        ContinuationCost,
                        VariantsBySignal[Signal],
                    )),
                    max(map(
                        ContinuationCost,
                        VariantsBySignal[Signal],
                    )),
                ]
                if VariantsBySignal[Signal]
                else []
            ),
            "DiscoveryPortfolioComplete": (
                Signal not in DiscoveryIncompleteSignals
            ),
            "DiscoveryVariantLimit": (
                EffectiveDiscoveryVariantLimit
            ),
            "RejectionCounts": dict(sorted(
                RejectionCounts.items()
            )),
            "ImmutableConflictSignals": sorted(
                ImmutableConflictSignals
            ),
            "ReservedPortContractConflictSignals": sorted(
                ReservedPortContractConflictSignals
            ),
            "ReservedGlobalRouteConflictSignals": sorted(
                ReservedGlobalRouteConflictSignals
            ),
        }
        if StopAfterCompleteNetVariantPortfolioSignal == Signal:
            SolverDiagnostics["VariantDiagnosticsBySignal"] = (
                VariantDiagnosticsBySignal
            )
            SolverDiagnostics["NetVariantPortfolioSignal"] = Signal
            SolverDiagnostics["NetVariantPortfolioComplete"] = bool(
                Signal not in DiscoveryIncompleteSignals
            )
            SolverDiagnostics["NetVariantPortfolioVariantCount"] = len(
                EnumeratedVariants
            )
            SolverDiagnostics["TemplateSearchEntered"] = False
            if Signal in DiscoveryIncompleteSignals:
                return ComponentRoutingSolveResult(
                    Status="incomplete",
                    ProofFingerprint=_StableFingerprint((
                        Problem.ProblemFingerprint,
                        Signal,
                        "net-variant-portfolio-incomplete",
                    )),
                    ExpansionCount=ExpansionCount,
                    Detail="net variant portfolio discovery is incomplete",
                    Diagnostics=SolverDiagnostics,
                )
            return ComponentRoutingSolveResult(
                Status="complete-net-variant-portfolio",
                ProofFingerprint=_StableFingerprint((
                    Problem.ProblemFingerprint,
                    Signal,
                    "complete-net-variant-portfolio",
                    tuple(
                        Variant.NetFingerprint
                        for Variant in EnumeratedVariants
                    ),
                )),
                ExpansionCount=ExpansionCount,
                Detail="complete net variant portfolio compiled",
                Diagnostics=SolverDiagnostics,
            )
        if not VariantsBySignal[Signal]:
            LocalUnsatCoreSignals = tuple(sorted({
                Signal,
                *ImmutableConflictSignals,
            }))
            LocalUnsatCoreComplete = (
                Signal not in DiscoveryIncompleteSignals
            )
            LocalUnsatCoreFingerprint = _StableFingerprint((
                "local-no-powered-variant-core-v1",
                StructuralPortfolioFingerprint,
                CompleteProofContextFingerprint,
                tuple(sorted(RejectionCounts.items())),
                LocalUnsatCoreSignals,
            ))
            SolverDiagnostics["VariantDiagnosticsBySignal"] = (
                VariantDiagnosticsBySignal
            )
            SolverDiagnostics["StructuralVariantCounts"] = sorted(
                len(Values)
                for Values in VariantsBySignal.values()
            )
            SolverDiagnostics["LocalUnsatSignal"] = Signal
            SolverDiagnostics["LocalUnsatCoreSignals"] = list(
                LocalUnsatCoreSignals
            )
            SolverDiagnostics["LocalUnsatCoreComplete"] = (
                LocalUnsatCoreComplete
            )
            SolverDiagnostics["LocalUnsatCoreFingerprint"] = (
                LocalUnsatCoreFingerprint
            )
            return ComponentRoutingSolveResult(
                Status="architectural-unsatisfiable",
                ProofFingerprint=_StableFingerprint((
                    Problem.ProblemFingerprint,
                    "no-powered-net-variant",
                    len(Domains),
                    LocalUnsatCoreFingerprint,
                )),
                ExpansionCount=ExpansionCount,
                Detail="a component net has no powered fabric tree",
                Diagnostics=SolverDiagnostics,
            )
        if Signal not in DiscoveryIncompleteSignals:
            CompleteProofVariants[Signal] = VariantsBySignal[Signal]
        if 2 <= len(CompleteProofVariants) <= 4:
            UnsatSubset = FindCompleteComponentNetUnsatSubset(
                CompleteProofVariants,
                Advance=lambda: Advance(
                    "incremental-complete-net-capacity-proof"
                ),
            )
            if UnsatSubset is None:
                SolverDiagnostics["VariantDiagnosticsBySignal"] = (
                    VariantDiagnosticsBySignal
                )
                SolverDiagnostics[
                    "IncrementalNetCapacityProofComplete"
                ] = False
                return ComponentRoutingSolveResult(
                    Status="incomplete",
                    ProofFingerprint=_StableFingerprint((
                        Problem.ProblemFingerprint,
                        "incremental-net-capacity-proof-incomplete",
                        ExpansionCount,
                    )),
                    ExpansionCount=ExpansionCount,
                    Detail=(
                        "component state work or deadline cap reached"
                    ),
                    Diagnostics=SolverDiagnostics,
                )
            if UnsatSubset:
                StructuralCoreFingerprint = _StableFingerprint(tuple(
                    sorted(
                        tuple(
                            Variant.NetFingerprint
                            for Variant
                            in CompleteProofVariants[CoreSignal]
                        )
                        for CoreSignal in UnsatSubset
                    )
                ))
                SolverDiagnostics["VariantDiagnosticsBySignal"] = (
                    VariantDiagnosticsBySignal
                )
                SolverDiagnostics[
                    "IncrementalNetCapacityProofComplete"
                ] = True
                SolverDiagnostics[
                    "IncrementalNetCapacityUnsatSignals"
                ] = sorted(UnsatSubset)
                SolverDiagnostics[
                    "IncrementalNetCapacityCoreFingerprint"
                ] = StructuralCoreFingerprint
                return ComponentRoutingSolveResult(
                    Status="architectural-unsatisfiable",
                    ProofFingerprint=_StableFingerprint((
                        Problem.ProblemFingerprint,
                        "complete-net-capacity-subset-unsatisfiable",
                        StructuralCoreFingerprint,
                    )),
                    ExpansionCount=ExpansionCount,
                    Detail=(
                        "a complete subset of component net portfolios "
                        "cannot share the component fabric"
                    ),
                    Diagnostics=SolverDiagnostics,
                )
    SolverDiagnostics["VariantDiagnosticsBySignal"] = (
        VariantDiagnosticsBySignal
    )
    SolverDiagnostics["FabricParentCacheRootCount"] = len(
        FabricParentCache
    )
    SolverDiagnostics["NetVariantTopologyCacheCount"] = len(
        NetVariantTopologyCache
    )
    SolverDiagnostics["RouteClaimsCacheCount"] = len(
        RouteClaimsCache
    )
    SolverDiagnostics["StructuralVariantCounts"] = sorted(
        len(Values) for Values in VariantsBySignal.values()
    )
    SolverDiagnostics["DiscoveryIncompleteSignals"] = sorted(
        DiscoveryIncompleteSignals
    )
    # Access, routes, and passive escapes are one CSP. Solving all component
    # nets first and foreign witnesses afterward recreates the ownership-first
    # failure mode: the search explores many component trees that a single
    # passive terminal immediately disproves. Dynamic MRV and forward checking
    # keep the solve simultaneous without changing its finite state space.
    Variables: list[
        tuple[
            str,
            str,
            str,
            tuple[Any, ...],
            Callable[[Any], RoutingResourceClaims],
            tuple[object, ...],
        ]
    ] = []
    for Signal in Problem.ComponentSignals:
        Options = VariantsBySignal[Signal]
        Variables.append((
            "net",
            Signal,
            Signal,
            Options,
            lambda Value: Value.Claims,
            tuple(Value.NetFingerprint for Value in Options),
        ))
    for DomainIndex, Domain in enumerate(
        Problem.ExternalContinuationDomains
    ):
        Variables.append((
            "continuation",
            str(DomainIndex),
            Domain.Signal,
            Domain.Candidates,
            lambda Value: Value.Claims,
            tuple(
                Value.CandidateFingerprint
                for Value in Domain.Candidates
            ),
        ))
    for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains):
        ForbiddenForeignFingerprints = (
            ForbiddenForeignCandidateFingerprintsBySignal.get(
                Domain.Signal,
                frozenset(),
            )
        )
        Options = tuple(
            Candidate
            for Candidate in Domain.Candidates
            if (
                Candidate.CandidateFingerprint
                not in ForbiddenForeignFingerprints
            )
        )
        Variables.append((
            "foreign",
            str(DomainIndex),
            Domain.Signal,
            Options,
            lambda Value: Value.Claims,
            tuple(
                Value.CandidateFingerprint for Value in Options
            ),
        ))
    for DomainIndex, Domain in enumerate(
        Problem.ForeignTransitDomains
    ):
        if Domain.Signal not in RequiredForeignTransitSignals:
            continue
        Variables.append((
            "transit",
            str(DomainIndex),
            Domain.Signal,
            Domain.Candidates,
            lambda Value: Value.Claims,
            tuple(
                Value.NetFingerprint
                for Value in Domain.Candidates
            ),
        ))
    SelectedValues: dict[tuple[str, str], Any] = {}
    SelectedClaims: list[
        tuple[str, RoutingResourceClaims]
    ] = [
        (Claim.Signal, Claim.Claims)
        for Claim in Problem.LocalClaims
        if Claim.Signal not in Problem.ComponentSignals
    ] + [
        (Claim.Signal, Claim.Claims)
        for Claim in Problem.ImmutableClaims
    ]
    CapacityEmptyDomainCounts: dict[str, int] = {}
    CapacityEmptyDomainWitnesses: dict[
        str, tuple[str, ...]
    ] = {}
    OptionClaims = tuple(
        tuple(ClaimsFor(Option) for Option in Options)
        for (
            _Kind,
            _Identity,
            _Owner,
            Options,
            ClaimsFor,
            _Structural,
        ) in Variables
    )
    CompatibilityCache: dict[
        tuple[int, int, int],
        tuple[int, ...],
    ] = {}
    ArcConsistencyRevisionCount = 0
    ArcConsistencyRemovedOptionCount = 0

    def CompatibleOptionIndexes(
        VariableIndex: int,
        OptionIndex: int,
        OtherIndex: int,
    ) -> tuple[int, ...]:
        Key = (VariableIndex, OptionIndex, OtherIndex)
        Cached = CompatibilityCache.get(Key)
        if Cached is not None:
            return Cached
        Owner = Variables[VariableIndex][2]
        OtherOwner = Variables[OtherIndex][2]
        Claims = OptionClaims[VariableIndex][OptionIndex]
        Result = tuple(
            Index
            for Index, OtherClaims
            in enumerate(OptionClaims[OtherIndex])
            if ComponentClaimsCompatibleForOwners(
                Owner,
                Claims,
                OtherOwner,
                OtherClaims,
            )
        )
        CompatibilityCache[Key] = Result
        return Result

    def EnforceArcConsistency(
        Domains: dict[int, tuple[int, ...]],
    ) -> dict[int, tuple[int, ...]] | None:
        """Enforce exact binary claim support across all remaining domains."""
        nonlocal ArcConsistencyRevisionCount
        nonlocal ArcConsistencyRemovedOptionCount
        Result = dict(Domains)
        Queue = deque(
            (FirstIndex, SecondIndex)
            for FirstIndex in Result
            for SecondIndex in Result
            if FirstIndex != SecondIndex
        )
        while Queue:
            FirstIndex, SecondIndex = Queue.popleft()
            FirstDomain = Result[FirstIndex]
            SecondDomainSet = frozenset(Result[SecondIndex])
            Retained = []
            for OptionOffset, OptionIndex in enumerate(FirstDomain):
                ArcConsistencyRevisionCount += 1
                if (
                    ArcConsistencyRevisionCount % 128 == 0
                    and not Advance("component-arc-consistency")
                ):
                    return None
                if SecondDomainSet.intersection(
                    CompatibleOptionIndexes(
                        FirstIndex,
                        OptionIndex,
                        SecondIndex,
                    )
                ):
                    Retained.append(OptionIndex)
            RetainedDomain = tuple(Retained)
            if RetainedDomain == FirstDomain:
                continue
            ArcConsistencyRemovedOptionCount += (
                len(FirstDomain) - len(RetainedDomain)
            )
            if not RetainedDomain:
                Result[FirstIndex] = ()
                FirstKind, FirstIdentity = (
                    Variables[FirstIndex][0],
                    Variables[FirstIndex][1],
                )
                SecondKind, SecondIdentity = (
                    Variables[SecondIndex][0],
                    Variables[SecondIndex][1],
                )
                EmptyKey = f"{FirstKind}:{FirstIdentity}"
                CapacityEmptyDomainCounts[EmptyKey] = (
                    CapacityEmptyDomainCounts.get(EmptyKey, 0) + 1
                )
                CapacityEmptyDomainWitnesses.setdefault(
                    EmptyKey,
                    (f"{SecondKind}:{SecondIdentity}",),
                )
                return Result
            Result[FirstIndex] = RetainedDomain
            Queue.extend(
                (OtherIndex, FirstIndex)
                for OtherIndex in Result
                if (
                    OtherIndex != FirstIndex
                    and OtherIndex != SecondIndex
                )
            )
        return Result

    InitialDomains: dict[int, tuple[int, ...]] = {}
    for VariableIndex, Variable in enumerate(Variables):
        Owner = Variable[2]
        InitialDomains[VariableIndex] = tuple(
            OptionIndex
            for OptionIndex, Claims
            in enumerate(OptionClaims[VariableIndex])
            if not any(
                not ComponentClaimsCompatibleForOwners(
                    Owner,
                    Claims,
                    ImmutableOwner,
                    ImmutableClaims,
                )
                for ImmutableOwner, ImmutableClaims in SelectedClaims
            )
        )
    ArcConsistentInitialDomains = EnforceArcConsistency(
        InitialDomains
    )
    if ArcConsistentInitialDomains is None:
        SolverDiagnostics["ArcConsistencyComplete"] = False
        SolverDiagnostics["ArcConsistencyRevisionCount"] = (
            ArcConsistencyRevisionCount
        )
        return ComponentRoutingSolveResult(
            Status="incomplete",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "initial-arc-consistency-incomplete",
                ExpansionCount,
            )),
            ExpansionCount=ExpansionCount,
            Detail="component state work or deadline cap reached",
            Diagnostics=SolverDiagnostics,
        )
    InitialDomains = ArcConsistentInitialDomains
    FailedDomainStates: set[
        tuple[tuple[int, tuple[int, ...]], ...]
    ] = set()
    ForbiddenPairForeignVariableIdentities = frozenset(
        str(DomainIndex)
        for DomainIndex, Domain
        in enumerate(Problem.ForeignEscapeDomains)
        if any(
            any(
                Signal == Domain.Signal
                and Terminal == Domain.Terminal
                for Signal, Terminal, _Fingerprint
                in ForbiddenPair
            )
            for ForbiddenPair
            in ForbiddenForeignAssignmentPairs
        )
    )
    FeedbackConstrainedForeignVariableIdentities = frozenset({
        *ForbiddenPairForeignVariableIdentities,
        *(
            str(DomainIndex)
            for DomainIndex, Domain
            in enumerate(Problem.ForeignEscapeDomains)
            if ForbiddenForeignCandidateFingerprintsBySignal.get(
                Domain.Signal,
                frozenset(),
            )
        ),
    })

    def SelectedForeignAssignments() -> frozenset[
        tuple[str, Position3, str]
    ]:
        return frozenset(
            (
                Domain.Signal,
                Domain.Terminal,
                Value.CandidateFingerprint,
            )
            for DomainIndex, Domain
            in enumerate(Problem.ForeignEscapeDomains)
            if (
                Value := SelectedValues.get(
                    ("foreign", str(DomainIndex))
                )
            ) is not None
        )

    def ViolatesForbiddenForeignPair() -> bool:
        Selected = SelectedForeignAssignments()
        return any(
            ForbiddenPair <= Selected
            for ForbiddenPair in ForbiddenForeignAssignmentPairs
        )

    def SelectVariables(
        RemainingDomains: dict[int, tuple[int, ...]],
    ) -> bool:
        if not RemainingDomains:
            if ViolatesForbiddenForeignPair():
                SolverDiagnostics[
                    "RejectedForeignAssignmentPairCount"
                ] = int(SolverDiagnostics.get(
                    "RejectedForeignAssignmentPairCount",
                    0,
                )) + 1
                return False
            AssignmentFingerprint = _StableFingerprint(tuple(sorted(
                (
                    Kind,
                    Identity,
                    (
                        Value.NetFingerprint
                        if Kind in {"net", "transit"}
                        else Value.CandidateFingerprint
                    ),
                )
                for (Kind, Identity), Value
                in SelectedValues.items()
            )))
            if (
                AssignmentFingerprint
                in ForbiddenAssignmentFingerprints
            ):
                SolverDiagnostics[
                    "RejectedAssignmentFingerprintCount"
                ] = int(SolverDiagnostics.get(
                    "RejectedAssignmentFingerprintCount",
                    0,
                )) + 1
                return False
            SolverDiagnostics["SelectedAssignmentFingerprint"] = (
                AssignmentFingerprint
            )
            return True
        DomainState = tuple(sorted(RemainingDomains.items()))
        if DomainState in FailedDomainStates:
            return False
        Ranked = []
        for VariableIndex, Domain in RemainingDomains.items():
            if not Domain:
                (
                    Kind,
                    Identity,
                    _Owner,
                    _Values,
                    _ClaimsFor,
                    _Structural,
                ) = (
                    Variables[VariableIndex]
                )
                Key = f"{Kind}:{Identity}"
                CapacityEmptyDomainCounts[Key] = (
                    CapacityEmptyDomainCounts.get(Key, 0) + 1
                )
                CapacityEmptyDomainWitnesses.setdefault(
                    Key,
                    tuple(sorted(
                        f"{SelectedKind}:{SelectedIdentity}"
                        for SelectedKind, SelectedIdentity
                        in SelectedValues
                    )),
                )
                return False
            (
                Kind,
                _Identity,
                _Owner,
                _Values,
                _ClaimsFor,
                Structural,
            ) = (
                Variables[VariableIndex]
            )
            Ranked.append((
                # Route trees are normally the high-impact decisions. Exact
                # foreign feedback is more constraining still: commit those
                # variables first so neither a rejected witness nor a
                # rejected witness pair expands every unrelated component
                # net before selecting a replacement escape.
                (
                    0
                    if (
                        Kind == "foreign"
                        and _Identity
                        in FeedbackConstrainedForeignVariableIdentities
                    )
                    else 1
                    if Kind in {"net", "transit"}
                    else 2
                    if Kind == "continuation"
                    else 3
                ),
                len(Domain),
                tuple(Structural[Index] for Index in Domain),
                VariableIndex,
                Domain,
            ))
        (
            _KindOrder,
            _OptionCount,
            _Structural,
            SelectedIndex,
            Domain,
        ) = min(Ranked)
        (
            Kind,
            Identity,
            Owner,
            _Values,
            ClaimsFor,
            _Fingerprints,
        ) = (
            Variables[SelectedIndex]
        )
        for OptionIndex in Domain:
            Option = Variables[SelectedIndex][3][OptionIndex]
            if not Advance("simultaneous-component-capacity"):
                return False
            NextDomains: dict[int, tuple[int, ...]] = {}
            ForwardLegal = True
            for OtherIndex, OtherDomain in RemainingDomains.items():
                if OtherIndex == SelectedIndex:
                    continue
                Compatible = frozenset(CompatibleOptionIndexes(
                    SelectedIndex,
                    OptionIndex,
                    OtherIndex,
                ))
                Filtered = tuple(
                    Index
                    for Index in OtherDomain
                    if Index in Compatible
                )
                if not Filtered:
                    OtherKind, OtherIdentity = (
                        Variables[OtherIndex][0],
                        Variables[OtherIndex][1],
                    )
                    Key = f"{OtherKind}:{OtherIdentity}"
                    CapacityEmptyDomainCounts[Key] = (
                        CapacityEmptyDomainCounts.get(Key, 0) + 1
                    )
                    CapacityEmptyDomainWitnesses.setdefault(
                        Key,
                        tuple(sorted((
                            *(
                                f"{SelectedKind}:{SelectedIdentity}"
                                for SelectedKind, SelectedIdentity
                                in SelectedValues
                            ),
                            f"{Kind}:{Identity}",
                        ))),
                    )
                    ForwardLegal = False
                    break
                NextDomains[OtherIndex] = Filtered
            if not ForwardLegal:
                continue
            ArcConsistentDomains = EnforceArcConsistency(
                NextDomains
            )
            if ArcConsistentDomains is None:
                return False
            if not ArcConsistentDomains and NextDomains:
                continue
            NextDomains = ArcConsistentDomains
            SelectedValues[(Kind, Identity)] = Option
            if ViolatesForbiddenForeignPair():
                SolverDiagnostics[
                    "RejectedForeignAssignmentPairCount"
                ] = int(SolverDiagnostics.get(
                    "RejectedForeignAssignmentPairCount",
                    0,
                )) + 1
                del SelectedValues[(Kind, Identity)]
                continue
            SelectedClaims.append((Owner, ClaimsFor(Option)))
            if SelectVariables(NextDomains):
                return True
            SelectedClaims.pop()
            del SelectedValues[(Kind, Identity)]
        FailedDomainStates.add(DomainState)
        return False

    Feasible = SelectVariables(InitialDomains)
    HitLimit = bool(
        ExpansionCount > Problem.MaximumWork
        or (
            DeadlineSeconds is not None
            and monotonic() - Started >= DeadlineSeconds
        )
    )
    SolverDiagnostics["CapacityEmptyDomainCounts"] = dict(
        sorted(CapacityEmptyDomainCounts.items())
    )
    SolverDiagnostics["CapacityEmptyDomainWitnesses"] = dict(
        sorted(CapacityEmptyDomainWitnesses.items())
    )
    SolverDiagnostics["FailedDomainStateCount"] = len(
        FailedDomainStates
    )
    SolverDiagnostics["ArcConsistencyComplete"] = True
    SolverDiagnostics["ArcConsistencyRevisionCount"] = (
        ArcConsistencyRevisionCount
    )
    SolverDiagnostics["ArcConsistencyRemovedOptionCount"] = (
        ArcConsistencyRemovedOptionCount
    )
    SolverDiagnostics["ExpansionCount"] = ExpansionCount
    if not Feasible:
        if DiscoveryIncompleteSignals and not HitLimit:
            return ComponentRoutingSolveResult(
                Status="incomplete",
                ProofFingerprint=_StableFingerprint((
                    Problem.ProblemFingerprint,
                    "discovery-needs-exhaustive-retry",
                    tuple(sorted(DiscoveryIncompleteSignals)),
                )),
                ExpansionCount=ExpansionCount,
                Detail=(
                    "bounded discovery portfolio needs exhaustive retry"
                ),
                Diagnostics={
                    **SolverDiagnostics,
                    "DiscoveryNeedsExhaustiveRetry": True,
                },
            )
        Status = (
            "incomplete" if HitLimit else "architectural-unsatisfiable"
        )
        return ComponentRoutingSolveResult(
            Status=Status,
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                Status,
                ExpansionCount,
                tuple(
                    len(VariantsBySignal[Signal])
                    for Signal in Problem.ComponentSignals
                ),
            )),
            ExpansionCount=ExpansionCount,
            Detail=(
                "component state work or deadline cap reached"
                if HitLimit
                else "complete finite component state space exhausted"
            ),
            Diagnostics=SolverDiagnostics,
        )
    Nets = tuple(sorted(
        (
            SelectedValues[("net", Signal)]
            for Signal in Problem.ComponentSignals
        ),
        key=lambda Value: Value.NetFingerprint,
    ))
    Foreign = tuple(sorted(
        (
            (
                Domain.Signal,
                Domain.Terminal,
                SelectedValues[("foreign", str(DomainIndex))],
            )
            for DomainIndex, Domain
            in enumerate(Problem.ForeignEscapeDomains)
        ),
        key=lambda Value: (
            Value[2].CandidateFingerprint,
            Value[1],
        ),
    ))
    ExternalContinuations = tuple(sorted(
        (
            (
                Domain.Signal,
                Domain.Terminal,
                SelectedValues[("continuation", str(DomainIndex))],
            )
            for DomainIndex, Domain
            in enumerate(Problem.ExternalContinuationDomains)
        ),
        key=lambda Value: (
            Value[0],
            Value[1],
            Value[2].CandidateFingerprint,
        ),
    ))
    ForeignTransits = tuple(sorted(
        (
            SelectedValues[("transit", str(DomainIndex))]
            for DomainIndex, _Domain
            in enumerate(Problem.ForeignTransitDomains)
            if ("transit", str(DomainIndex)) in SelectedValues
        ),
        key=lambda Value: Value.NetFingerprint,
    ))
    Claims = _MergeClaims((
        *(Value.Claims for Value in Nets),
        *(Value[2].Claims for Value in ExternalContinuations),
        *(Value[2].Claims for Value in Foreign),
        *(Value.Claims for Value in ForeignTransits),
    ))
    ExportedPorts = tuple(sorted(
        (
            Net.Signal,
            Position,
        )
        for Net in Nets
        for Position in Net.ExportedPorts
    ))
    ExportedPortFingerprint = _StableFingerprint(tuple(
        _RelativeGeometry(Position for _Signal, Position in ExportedPorts)
    ))
    ClaimsFingerprint = _ClaimsFingerprint(Claims)
    RoutedTemplateFingerprint = _StableFingerprint((
        Problem.ProblemFingerprint,
        tuple(Net.NetFingerprint for Net in Nets),
        tuple(
            Value[2].CandidateFingerprint for Value in Foreign
        ),
        tuple(
            Value[2].CandidateFingerprint
            for Value in ExternalContinuations
        ),
        tuple(
            Value.NetFingerprint for Value in ForeignTransits
        ),
        ExportedPortFingerprint,
        ClaimsFingerprint,
    ))
    ProofFingerprint = _StableFingerprint((
        RoutedTemplateFingerprint,
        ExpansionCount,
        "feasible",
    ))
    Template = RoutedComponentTemplate(
        ProblemFingerprint=Problem.ProblemFingerprint,
        PlacementFingerprint=Problem.PlacementFingerprint,
        LocalTemplateFingerprint=Problem.LocalTemplateFingerprint,
        FabricFingerprint=Problem.Fabric.FabricFingerprint,
        RoutedTemplateFingerprint=RoutedTemplateFingerprint,
        Nets=Nets,
        ForeignEscapeReservations=Foreign,
        ExportedPorts=ExportedPorts,
        Claims=Claims,
        ExportedPortFingerprint=ExportedPortFingerprint,
        ClaimsFingerprint=ClaimsFingerprint,
        ProofFingerprint=ProofFingerprint,
        ExpansionCount=ExpansionCount,
        Diagnostics=SolverDiagnostics,
        ExternalContinuationReservations=ExternalContinuations,
        ForeignTransitReservations=ForeignTransits,
        InterfaceFingerprint=(
            Problem.Interface.InterfaceFingerprint
            if Problem.Interface is not None
            else ""
        ),
    )
    return ComponentRoutingSolveResult(
        Status="feasible",
        Template=Template,
        ProofFingerprint=ProofFingerprint,
        ExpansionCount=ExpansionCount,
        Diagnostics=SolverDiagnostics,
    )


@dataclass(frozen=True)
class ComponentTreeDpNetState:
    """Canonical exact state for one fully covered component signal."""

    Signal: str
    Candidates: tuple[ComponentTerminalAccessCandidate, ...]
    EgressPath: tuple[Position3, ...]
    Nodes: frozenset[Position3]
    Edges: frozenset[RoutingEdge]
    Claims: RoutingResourceClaims
    Root: Position3
    Repeaters: tuple[tuple[Position3, str], ...]
    CoveredTerminals: tuple[Position3, ...]
    ExportedPorts: tuple[Position3, ...]
    NetFingerprint: str


def SelectComponentSymbolicPhysicalPort(
    Problem: ComponentRoutingProblem,
    Signal: str,
) -> Any | None:
    return next((
        Port
        for Port in (
            Problem.Interface.PhysicalPortReservations
            if Problem.Interface is not None
            else ()
        )
        if Port.Signal == Signal
    ), None)


def BuildComponentSymbolicNetStateCacheKey(
    Problem: ComponentRoutingProblem,
    Signal: str,
    ForbiddenExportPortsBySignal: dict[
        str, tuple[Position3, ...]
    ] | None = None,
    *,
    PreparedContextFingerprint: str | None = None,
) -> str:
    """Identify one signal-local exact tree-frontier compilation."""
    PhysicalPort = SelectComponentSymbolicPhysicalPort(Problem, Signal)
    ContextFingerprint = (
        str(PreparedContextFingerprint)
        if PreparedContextFingerprint is not None
        else _BuildPreparedComponentSymbolicNetStateContextFingerprint(
            Problem,
            Signal,
        )
    )
    return _StableFingerprint((
        # v3: the prepared context is the signal-local identity; it omits
        # placement-wide exterior geometry by construction.
        "component-symbolic-net-state-v3-local-domain",
        ContextFingerprint,
        Signal,
        tuple(getattr(PhysicalPort, "LocalPath", ())),
        tuple(getattr(
            PhysicalPort,
            "OwnedCandidateFingerprints",
            (),
        )),
        tuple((ForbiddenExportPortsBySignal or {}).get(Signal, ())),
    ))


def _BuildPreparedComponentSymbolicNetStateContextIdentity(
    Problem: ComponentRoutingProblem,
    Signal: str,
) -> dict[str, object]:
    """Build a stable semantic identity for one prepared symbolic context."""
    OwnedDomains = tuple(
        Domain
        for Domain in Problem.OwnedTerminalDomains
        if str(Domain.Signal) == str(Signal)
    )
    return {
        "FabricFingerprint": Problem.Fabric.FabricFingerprint,
        # Placement-wide identity is deliberately excluded.  This context is
        # consumed only by the closed-component unary tree compiler; its
        # remaining fields below cover every local geometry, terminal,
        # obstacle, claim, power, resource-graph, and technology input it
        # reads.  Exterior placement changes must rebuild seams and capacity,
        # but need not invalidate an exact local tree proof.
        "ResourceGraphFingerprint": str(
            getattr(
                Problem.PhysicalAssemblyPlan,
                "ResourceGraphFingerprint",
                "",
            )
        ),
        "TechnologyFingerprint": str(
            getattr(
                Problem.PhysicalAssemblyPlan,
                "TechnologyFingerprint",
                "",
            )
        ),
        "Signal": str(Signal),
        "OwnedDomains": tuple(
            (
                Domain.TerminalRole,
                Domain.TerminalFingerprint,
                tuple(sorted(
                    (
                        Candidate.CandidateFingerprint,
                        Candidate.Attachment,
                        Candidate.Path,
                        _ClaimsFingerprint(Candidate.Claims),
                        Candidate.Layer,
                        Candidate.Cost,
                    )
                    for Candidate in Domain.Candidates
                )),
                Domain.Complete,
            )
            for Domain in sorted(
                OwnedDomains,
                key=lambda Value: (
                    Value.TerminalFingerprint,
                    Value.TerminalRole,
                    Value.Terminal,
                ),
            )
        ),
        "LocalClaims": tuple(
            (
                str(Claim.Signal),
                tuple(sorted(
                    Value for Value in Claim.Nodes
                )),
                tuple(
                    _NormalizedEdge(First, Second)
                    for First, Second in sorted(Claim.Edges)
                ),
            )
            for Claim in sorted(
                (
                    Claim
                    for Claim in Problem.LocalClaims
                    if str(Claim.Signal) == str(Signal)
                ),
                key=lambda Value: (
                    str(Value.Signal),
                    Value.Root,
                ),
            )
        ),
        "ImmutableClaims": tuple(
            (
                str(Claim.Signal),
                _ClaimsFingerprint(Claim.Claims),
            )
            for Claim in sorted(
                (
                    Claim
                    for Claim in Problem.ImmutableClaims
                    if str(Claim.Signal) not in Problem.ComponentSignals
                ),
                key=lambda Value: str(Value.Signal),
            )
        ),
        "ReservedGlobalClaims": tuple(
            (
                str(ReservedSignal),
                _ClaimsFingerprint(Claims),
            )
            for ReservedSignal, Claims in sorted(
                Problem.ReservedGlobalClaimsBySignal,
                key=lambda Value: str(Value[0]),
            )
            if str(ReservedSignal) != str(Signal)
        ),
        "ExternalContinuations": tuple(
            Value
            for Value in sorted(
                Problem.ExternalContinuationTerminals,
                key=lambda Value: (
                    str(Value[0]),
                    Value[1],
                    Value[2],
                ),
            )
            if str(Value[0]) == str(Signal)
        ),
        "MaximumPowerDistance": Problem.MaximumPowerDistance,
        "ResourceGraphVersion": getattr(
            Problem.ResourceGraph,
            "GraphVersion",
            "",
        ),
    }


def _BuildPreparedComponentSymbolicNetStateContextFingerprint(
    Problem: ComponentRoutingProblem,
    Signal: str,
) -> str:
    """Identify every port-independent input to one signal tree compiler."""
    Identity = _BuildPreparedComponentSymbolicNetStateContextIdentity(
        Problem,
        Signal,
    )
    return _StableFingerprint((
        "prepared-component-symbolic-net-state-context-v1",
        tuple(sorted(Identity.items())),
    ))


@dataclass
class PreparedComponentSymbolicTerminalFrontier:
    """Complete owned-terminal frontier independent of one physical egress."""

    FilteredByDomain: tuple[
        tuple[ComponentTerminalAccessCandidate, ...], ...
    ]
    Frontier: tuple[
        tuple[
            int,
            tuple[tuple[int, ComponentTerminalAccessCandidate], ...],
            frozenset[Position3],
            frozenset[RoutingEdge],
            RoutingResourceClaims,
        ], ...
    ]
    ImmutableRejectedCandidateCount: int
    CertifiedRejectedCandidateCount: int
    CandidateFilterEmpty: bool


@dataclass
class PreparedComponentSymbolicNetStateContext:
    """Reusable port-independent tree data for one component signal."""

    ContextFingerprint: str
    Signal: str
    FabricAdjacency: dict[Position3, set[Position3]]
    FabricParentCache: dict[
        Position3, dict[Position3, Position3 | None]
    ]
    FabricComponentByNode: dict[Position3, int]
    Domains: tuple[ComponentTerminalAccessDomain, ...]
    ImmutableEligibleCandidateFingerprintsByDomain: tuple[
        frozenset[str], ...
    ]
    LocalClaims: tuple[LocalRouteClaim, ...]
    ImmutableClaimsBySignal: tuple[
        tuple[str, RoutingResourceClaims], ...
    ]
    BlockingImmutableClaims: tuple[
        tuple[str, RoutingResourceClaims], ...
    ]
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ]
    TerminalFrontierCache: dict[
        frozenset[str], PreparedComponentSymbolicTerminalFrontier
    ]
    TerminalFrontierBuildCount: int
    TerminalFrontierCacheHitCount: int
    TreeRepeaterSubproblemCache: dict[
        tuple[int, int, str],
        tuple[tuple[Position3, str], ...] | None,
    ]
    TreeRepeaterCacheStatistics: dict[str, int]


def PrepareComponentSymbolicNetStateContext(
    Problem: ComponentRoutingProblem,
    Signal: str,
    *,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
) -> PreparedComponentSymbolicNetStateContext:
    """Prepare the static fabric and claim context once for many port accesses."""
    Signal = str(Signal)
    if Signal not in Problem.ComponentSignals:
        raise ValueError(
            "prepared symbolic net-state signal is outside the component"
        )
    FabricAdjacency = _BuildAdjacency(Problem.Fabric.Edges)
    FabricComponentByNode: dict[Position3, int] = {}
    ComponentIndex = 0
    for Start in sorted(Problem.Fabric.Nodes):
        if Start in FabricComponentByNode:
            continue
        FabricComponentByNode[Start] = ComponentIndex
        Pending = [Start]
        while Pending:
            Current = Pending.pop()
            for Neighbor in sorted(FabricAdjacency.get(Current, ())):
                if Neighbor in FabricComponentByNode:
                    continue
                FabricComponentByNode[Neighbor] = ComponentIndex
                Pending.append(Neighbor)
        ComponentIndex += 1
    ImmutableClaimsBySignal = tuple(
        (str(Claim.Signal), Claim.Claims)
        for Claim in Problem.ImmutableClaims
        if Claim.Signal not in Problem.ComponentSignals
    )
    BlockingImmutableClaims = (
        *ImmutableClaimsBySignal,
        *tuple(
            (str(ReservedSignal), Claims)
            for ReservedSignal, Claims
            in Problem.ReservedGlobalClaimsBySignal
            if str(ReservedSignal) != Signal
        ),
    )
    Domains = tuple(
        Domain for Domain in Problem.OwnedTerminalDomains
        if str(Domain.Signal) == Signal
    )
    ImmutableEligibleCandidateFingerprintsByDomain = tuple(
        frozenset(
            Candidate.CandidateFingerprint
            for Candidate in Domain.Candidates
            if Candidate.Attachment in FabricComponentByNode
            and not any(
                ComponentClaimsConflict(
                    Candidate.Claims,
                    ImmutableClaims,
                )
                for _Owner, ImmutableClaims in BlockingImmutableClaims
            )
        )
        for Domain in Domains
    )
    return PreparedComponentSymbolicNetStateContext(
        ContextFingerprint=(
            _BuildPreparedComponentSymbolicNetStateContextFingerprint(
                Problem,
                Signal,
            )
        ),
        Signal=Signal,
        FabricAdjacency=FabricAdjacency,
        FabricParentCache={},
        FabricComponentByNode=FabricComponentByNode,
        Domains=Domains,
        ImmutableEligibleCandidateFingerprintsByDomain=(
            ImmutableEligibleCandidateFingerprintsByDomain
        ),
        LocalClaims=tuple(
            Claim for Claim in Problem.LocalClaims
            if str(Claim.Signal) == Signal
        ),
        ImmutableClaimsBySignal=ImmutableClaimsBySignal,
        BlockingImmutableClaims=BlockingImmutableClaims,
        RouteClaimsConstructionCache=(
            RouteClaimsConstructionCache
            if RouteClaimsConstructionCache is not None
            else {}
        ),
        TerminalFrontierCache={},
        TerminalFrontierBuildCount=0,
        TerminalFrontierCacheHitCount=0,
        TreeRepeaterSubproblemCache={},
        TreeRepeaterCacheStatistics={
            "HitCount": 0,
            "MissCount": 0,
        },
    )


@dataclass(frozen=True)
class PreparedComponentSymbolicNetStateCompilation:
    """One exact access-bound state domain compiled from a prepared context."""

    CacheKey: str
    States: tuple[ComponentTreeDpNetState, ...] | None
    Complete: bool
    CacheHit: bool
    ExpansionCount: int
    Diagnostics: dict[str, object]


def SolveComponentRoutingProblemDynamic(
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ForbiddenAssignmentFingerprints: frozenset[str] = frozenset(),
    ForbiddenExportPortsBySignal: dict[
        str, tuple[Position3, ...]
    ] | None = None,
    ForbiddenForeignCandidateFingerprintsBySignal: dict[
        str, frozenset[str]
    ] | None = None,
    ForbiddenForeignAssignmentPairs: tuple[
        frozenset[tuple[str, Position3, str]], ...
    ] = (),
    RequiredForeignTransitSignals: frozenset[str] = frozenset(),
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    SymbolicNetStateCache: dict[str, Any] | None = None,
    RequestedSymbolicStateSignals: frozenset[str] | None = None,
    PreparedSymbolicNetStateContext: (
        PreparedComponentSymbolicNetStateContext | None
    ) = None,
    PreparedPhysicalPortVariants: tuple[Any, ...] = (),
    StopAfterOwnedSignalFrontierProof: bool = False,
    StopAfterSymbolicCapacityProof: bool = False,
) -> ComponentRoutingSolveResult:
    """Solve a complete tree fabric through canonical frontier states.

    Access domains are folded one terminal at a time.  Because the component
    fabric is a forest, every attachment set has one unique connecting
    subtree.  States with the same covered terminals, component, nodes, and
    edges are therefore equivalent and may be merged before any complete
    routed-net object is materialized.
    """
    Started = monotonic()
    ForbiddenExportPortsBySignal = ForbiddenExportPortsBySignal or {}
    ForbiddenForeignCandidateFingerprintsBySignal = (
        ForbiddenForeignCandidateFingerprintsBySignal or {}
    )
    RouteClaimsCache = (
        PreparedSymbolicNetStateContext.RouteClaimsConstructionCache
        if PreparedSymbolicNetStateContext is not None
        else RouteClaimsConstructionCache
        if RouteClaimsConstructionCache is not None
        else {}
    )
    NetStateCache = (
        SymbolicNetStateCache
        if SymbolicNetStateCache is not None
        else {}
    )
    ExpansionCount = 0
    ExploredStateCount = 0
    PeakFrontierStateCount = 0
    DominatedStateCount = 0
    IncrementalPhysicalEgressMaterializationCount = 0
    HitLimit = False
    SignalDiagnostics: dict[str, dict[str, object]] = {}
    SolverDiagnostics: dict[str, object] = {
        "SolverKind": "tree-frontier-dp-v1",
        "ProblemFingerprint": Problem.ProblemFingerprint,
        "FabricFingerprint": Problem.Fabric.FabricFingerprint,
        "FabricTopologyKind": Problem.Fabric.TopologyKind,
        "FabricNodeCount": len(Problem.Fabric.Nodes),
        "FabricEdgeCount": len(Problem.Fabric.Edges),
        "ComponentSignalCount": len(Problem.ComponentSignals),
        "OwnedTerminalDomainCount": len(Problem.OwnedTerminalDomains),
        "CompleteTreesMaterialized": 0,
        "SelectedTreesMaterialized": 0,
        "SymbolicNetStateCacheHitCount": 0,
        "SymbolicNetStateCacheStoreCount": 0,
    }

    def Advance(Phase: str) -> bool:
        nonlocal ExpansionCount, ExploredStateCount, HitLimit
        ExpansionCount += 1
        ExploredStateCount += 1
        if WorkCheck is not None and ExpansionCount % 128 == 0:
            WorkCheck({
                "Phase": Phase,
                "SolverKind": "tree-frontier-dp-v1",
                "ExpansionCount": ExpansionCount,
                "ExploredStateCount": ExploredStateCount,
                "PeakFrontierStateCount": PeakFrontierStateCount,
                "DominatedStateCount": DominatedStateCount,
                "CompleteTreesMaterialized": 0,
            })
        HitLimit = bool(
            ExpansionCount > Problem.MaximumWork
            or (
                DeadlineSeconds is not None
                and monotonic() - Started >= DeadlineSeconds
            )
        )
        return not HitLimit

    def FinishDiagnostics() -> dict[str, object]:
        SolverDiagnostics.update({
            "ExpansionCount": ExpansionCount,
            "ExploredStateCount": ExploredStateCount,
            "PeakFrontierStateCount": PeakFrontierStateCount,
            "DominatedStateCount": DominatedStateCount,
            "IncrementalPhysicalEgressMaterializationCount": (
                IncrementalPhysicalEgressMaterializationCount
            ),
            "CompleteTreesMaterialized": 0,
            "SignalDiagnostics": SignalDiagnostics,
            "RuntimeSeconds": monotonic() - Started,
        })
        return SolverDiagnostics

    DeclaredFeedthroughSignals = (
        Problem.Interface.DeclaredFeedthroughSignals
        if Problem.Interface is not None
        else frozenset()
    )
    ForeignTransitSignals = frozenset(
        Domain.Signal for Domain in Problem.ForeignTransitDomains
    )
    ImplicitForeignTransitSignals = tuple(sorted(
        ForeignTransitSignals - DeclaredFeedthroughSignals
        if Problem.Interface is not None
        else ()
    ))
    SolverDiagnostics["ImplicitForeignTransitDomainCount"] = len(
        ImplicitForeignTransitSignals
    )
    SolverDiagnostics["ImplicitForeignTransitSignals"] = list(
        ImplicitForeignTransitSignals
    )
    if ImplicitForeignTransitSignals or not RequiredForeignTransitSignals <= (
        DeclaredFeedthroughSignals
    ):
        return ComponentRoutingSolveResult(
            Status="architectural-unsatisfiable",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "undeclared-foreign-transit",
                ImplicitForeignTransitSignals,
                tuple(sorted(RequiredForeignTransitSignals)),
            )),
            Detail="closed component contains undeclared foreign transit",
            Diagnostics=FinishDiagnostics(),
        )
    if Problem.Interface is not None and not Problem.Interface.Complete:
        return ComponentRoutingSolveResult(
            Status="incomplete",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "incomplete-closed-interface",
            )),
            Detail="closed component interface is incomplete",
            Diagnostics=FinishDiagnostics(),
        )
    if not Problem.Fabric.Complete:
        return ComponentRoutingSolveResult(
            Status="incomplete",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                Problem.Fabric.IncompleteReason,
            )),
            Detail=Problem.Fabric.IncompleteReason,
            Diagnostics=FinishDiagnostics(),
        )
    if not Problem.DomainComplete:
        return ComponentRoutingSolveResult(
            Status="incomplete",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "incomplete-domain",
            )),
            Detail="one or more terminal domains are incomplete or empty",
            Diagnostics=FinishDiagnostics(),
        )

    if PreparedSymbolicNetStateContext is not None:
        PreparedSignal = PreparedSymbolicNetStateContext.Signal
        if (
            _BuildPreparedComponentSymbolicNetStateContextFingerprint(
                Problem,
                PreparedSignal,
            )
            != PreparedSymbolicNetStateContext.ContextFingerprint
        ):
            raise ValueError(
                "prepared symbolic net-state context identity mismatch"
            )
        FabricAdjacency = (
            PreparedSymbolicNetStateContext.FabricAdjacency
        )
        FabricParentCache = (
            PreparedSymbolicNetStateContext.FabricParentCache
        )
        FabricComponentByNode = (
            PreparedSymbolicNetStateContext.FabricComponentByNode
        )
        LocalClaimsBySignal = {
            PreparedSignal: PreparedSymbolicNetStateContext.LocalClaims
        }
        ImmutableClaimsBySignal = (
            PreparedSymbolicNetStateContext.ImmutableClaimsBySignal
        )
        DomainsBySignal = {
            PreparedSignal: PreparedSymbolicNetStateContext.Domains
        }
    else:
        FabricAdjacency = _BuildAdjacency(Problem.Fabric.Edges)
        FabricParentCache: dict[
            Position3, dict[Position3, Position3 | None]
        ] = {}
        FabricComponentByNode: dict[Position3, int] = {}
        for Start in sorted(Problem.Fabric.Nodes):
            if Start in FabricComponentByNode:
                continue
            ComponentIndex = len(set(FabricComponentByNode.values()))
            FabricComponentByNode[Start] = ComponentIndex
            Pending = [Start]
            while Pending:
                Current = Pending.pop()
                for Neighbor in sorted(FabricAdjacency.get(Current, ())):
                    if Neighbor in FabricComponentByNode:
                        continue
                    FabricComponentByNode[Neighbor] = ComponentIndex
                    Pending.append(Neighbor)

        LocalClaimsBySignal = {
            Signal: tuple(
                Claim
                for Claim in Problem.LocalClaims
                if Claim.Signal == Signal
            )
            for Signal in Problem.ComponentSignals
        }
        ImmutableClaimsBySignal = tuple(
            (str(Claim.Signal), Claim.Claims)
            for Claim in Problem.ImmutableClaims
            if Claim.Signal not in Problem.ComponentSignals
        )
        DomainsBySignal = {
            Signal: tuple(
                Domain
                for Domain in Problem.OwnedTerminalDomains
                if Domain.Signal == Signal
            )
            for Signal in Problem.ComponentSignals
        }

    def ClaimsForNodes(
        Nodes: frozenset[Position3],
    ) -> RoutingResourceClaims:
        Claims = RouteClaimsCache.get(Nodes)
        if Claims is not None:
            return Claims
        Claims = (
            Problem.ResourceGraph.BuildRouteClaims(Nodes)
            if Problem.ResourceGraph is not None
            else RoutingResourceClaims(
                WireCells=Nodes,
                SupportCells=frozenset(
                    (X, Y - 1, Z) for X, Y, Z in Nodes
                ),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions(set(Nodes))
                ),
            )
        )
        RouteClaimsCache[Nodes] = Claims
        return Claims

    def ClaimsForNodeBatch(
        NodeSets: Iterable[frozenset[Position3]],
    ) -> dict[frozenset[Position3], RoutingResourceClaims]:
        """Materialize independent claim sets through the bounded native pool.

        The tree frontier retains ownership, ordering, and dominance in this
        process.  Only the pure physical expansion (wire/support/air/electrical
        cells) is batched, so a Rayon worker never observes mutable router
        state.  Non-default technologies keep the existing authoritative
        Python implementation until they have an equivalent native contract.
        """
        UniqueNodes = tuple(sorted(set(NodeSets), key=repr))
        Missing = tuple(
            Nodes for Nodes in UniqueNodes
            if Nodes not in RouteClaimsCache
        )
        if not Missing:
            return {
                Nodes: RouteClaimsCache[Nodes]
                for Nodes in UniqueNodes
            }
        ResourceGraph = Problem.ResourceGraph
        Technology = getattr(ResourceGraph, "Technology", None)
        NativeCompatible = bool(
            _BuildRouteClaimsBatchWithTelemetry is not None
            and (
                ResourceGraph is None
                or Technology == DefaultRedstoneRoutingTechnology
            )
        )
        if NativeCompatible and len(Missing) > 1:
            NativeClaims, ActiveWorkerCount = _BuildRouteClaimsBatchWithTelemetry(
                [tuple(sorted(Nodes)) for Nodes in Missing],
                tuple(sorted(getattr(ResourceGraph, "ActualBlocks", ()))),
                tuple(sorted(getattr(ResourceGraph, "SolidBlocks", ()))),
            )
            for Nodes, (Wire, Support, Air, Electrical) in zip(
                Missing,
                NativeClaims,
                strict=True,
            ):
                RouteClaimsCache[Nodes] = RoutingResourceClaims(
                    WireCells=frozenset(Wire),
                    SupportCells=frozenset(Support),
                    RequiredAirCells=frozenset(Air),
                    ElectricalCells=frozenset(Electrical),
                )
            SolverDiagnostics["NativeClaimBatchCount"] = int(
                SolverDiagnostics.get("NativeClaimBatchCount", 0)
            ) + 1
            SolverDiagnostics["NativeClaimBatchWorkItems"] = int(
                SolverDiagnostics.get("NativeClaimBatchWorkItems", 0)
            ) + len(Missing)
            SolverDiagnostics["NativeClaimBatchWorkerCount"] = (
                int(_GetRoutingThreadCount())
                if _GetRoutingThreadCount is not None
                else 0
            )
            SolverDiagnostics["NativeClaimBatchActiveWorkerCount"] = int(
                ActiveWorkerCount
            )
        else:
            for Nodes in Missing:
                ClaimsForNodes(Nodes)
        return {
            Nodes: RouteClaimsCache[Nodes]
            for Nodes in UniqueNodes
        }

    def BlockingImmutableClaims(
        Signal: str,
    ) -> tuple[tuple[str, RoutingResourceClaims], ...]:
        if (
            PreparedSymbolicNetStateContext is not None
            and str(Signal) == PreparedSymbolicNetStateContext.Signal
        ):
            return PreparedSymbolicNetStateContext.BlockingImmutableClaims
        return (
            *ImmutableClaimsBySignal,
            *tuple(
                (str(ReservedSignal), Claims)
                for ReservedSignal, Claims
                in Problem.ReservedGlobalClaimsBySignal
                if str(ReservedSignal) != Signal
            ),
        )

    ReservedGlobalGeometryBlockerSetsBySignal: dict[
        str, list[frozenset[str]]
    ] = defaultdict(list)
    ReservedGlobalCandidateBlockerSetsBySignalDomain: dict[
        tuple[str, int], list[frozenset[str]]
    ] = defaultdict(list)
    ReservedGlobalClaimSignals = frozenset(
        str(Signal)
        for Signal, _Claims in Problem.ReservedGlobalClaimsBySignal
    )
    ReservedGlobalWireCellsBySignal: dict[
        str, frozenset[Position3]
    ] = {
        Signal: frozenset().union(*(
            Claims.WireCells
            for ReservedSignal, Claims
            in Problem.ReservedGlobalClaimsBySignal
            if str(ReservedSignal) == Signal
        ))
        for Signal in ReservedGlobalClaimSignals
    }

    def HasBlockingClaimConflict(
        Signal: str,
        Claims: RoutingResourceClaims,
    ) -> bool:
        ConflictingOwners = frozenset(
            str(Owner)
            for Owner, ImmutableClaims in BlockingImmutableClaims(Signal)
            if ComponentClaimsConflict(Claims, ImmutableClaims)
        )
        if not ConflictingOwners:
            return False
        ImmutableBlockers = ConflictingOwners - ReservedGlobalClaimSignals
        ReservedBlockers = ConflictingOwners & ReservedGlobalClaimSignals
        # A geometry that also conflicts with an immutable claim is
        # intrinsically unavailable and needs no exterior literal.  Every
        # otherwise legal geometry rejected only by selected global routes
        # contributes one exact blocker set to the cut proof.
        if not ImmutableBlockers and ReservedBlockers:
            ReservedGlobalGeometryBlockerSetsBySignal[Signal].append(
                ReservedBlockers
            )
        return True

    def HasSameSignalReservedSelfConflict(
        Signal: str,
        Nodes: frozenset[Position3],
        CombinedClaims: RoutingResourceClaims | None = None,
    ) -> bool:
        SameSignalReservedNodes = (
            ReservedGlobalWireCellsBySignal.get(
                Signal,
                frozenset(),
            )
        )
        if not SameSignalReservedNodes:
            return False
        CombinedClaims = (
            ClaimsForNodes(frozenset((
                *Nodes,
                *SameSignalReservedNodes,
            )))
            if CombinedClaims is None
            else CombinedClaims
        )
        if not FindSelfClaimConflicts({Signal: CombinedClaims}):
            return False
        ReservedGlobalGeometryBlockerSetsBySignal[Signal].append(
            frozenset((Signal,))
        )
        return True

    def BuildGeometryStructure(
        Signal: str,
        Candidates: tuple[ComponentTerminalAccessCandidate, ...],
        EgressPath: tuple[Position3, ...],
        FabricSubtree: (
            tuple[frozenset[Position3], frozenset[RoutingEdge]] | None
        ) = None,
    ) -> tuple[frozenset[Position3], frozenset[RoutingEdge]] | None:
        Attachments = tuple(
            Candidate.Attachment for Candidate in Candidates
        ) + ((EgressPath[0],) if EgressPath else ())
        Subtree = (
            FabricSubtree
            if FabricSubtree is not None
            else _UniqueFabricSubtree(
                Problem.Fabric,
                Attachments,
                Adjacency=FabricAdjacency,
                ParentCache=FabricParentCache,
            )
        )
        if Subtree is None:
            return None
        Nodes = set(Subtree[0])
        Edges = set(Subtree[1])
        for Candidate in Candidates:
            Nodes.update(Candidate.Path)
            Edges.update(
                _NormalizedEdge(First, Second)
                for First, Second in zip(
                    Candidate.Path,
                    Candidate.Path[1:],
                )
            )
        for Claim in LocalClaimsBySignal.get(Signal, ()):
            Nodes.update(Claim.Nodes)
            Edges.update(
                _NormalizedEdge(*Edge) for Edge in Claim.Edges
            )
        if EgressPath:
            Nodes.update(EgressPath)
            Edges.update(
                _NormalizedEdge(First, Second)
                for First, Second in zip(
                    EgressPath,
                    EgressPath[1:],
                )
            )
        FrozenNodes = frozenset(Nodes)
        FrozenEdges = frozenset(Edges)
        return FrozenNodes, FrozenEdges

    def BuildFabricSubtreeBatch(
        AttachmentSets: Iterable[tuple[Position3, ...]],
    ) -> tuple[
        tuple[
            tuple[frozenset[Position3], frozenset[RoutingEdge]] | None,
            ...,
        ],
        int,
    ]:
        Values = tuple(AttachmentSets)
        if (
            _BuildFabricSubtreesBatchWithTelemetry is None
            or len(Values) < 2
        ):
            return (
                tuple(
                    _UniqueFabricSubtree(
                        Problem.Fabric,
                        Attachments,
                        Adjacency=FabricAdjacency,
                        ParentCache=FabricParentCache,
                    )
                    for Attachments in Values
                ),
                0,
            )
        NativeSubtrees, ActiveWorkerCount = (
            _BuildFabricSubtreesBatchWithTelemetry(
                tuple(sorted(Problem.Fabric.Nodes)),
                tuple(sorted(Problem.Fabric.Edges)),
                Values,
            )
        )
        Result = tuple(
            None if Subtree is None else (
                frozenset(Subtree[0]),
                frozenset(
                    _NormalizedEdge(First, Second)
                    for First, Second in Subtree[1]
                ),
            )
            for Subtree in NativeSubtrees
        )
        SolverDiagnostics["NativeFabricSubtreeBatchCount"] = int(
            SolverDiagnostics.get("NativeFabricSubtreeBatchCount", 0)
        ) + 1
        SolverDiagnostics["NativeFabricSubtreeBatchWorkItems"] = int(
            SolverDiagnostics.get("NativeFabricSubtreeBatchWorkItems", 0)
        ) + len(Values)
        SolverDiagnostics["NativeFabricSubtreeBatchActiveWorkerCount"] = int(
            ActiveWorkerCount
        )
        return Result, int(ActiveWorkerCount)

    def IsGeometryClaimsEligible(
        Signal: str,
        Nodes: frozenset[Position3],
        Claims: RoutingResourceClaims,
        SameSignalCombinedClaims: RoutingResourceClaims | None = None,
    ) -> bool:
        if FindSelfClaimConflicts({Signal: Claims}):
            return False
        if HasSameSignalReservedSelfConflict(
            Signal,
            Nodes,
            SameSignalCombinedClaims,
        ):
            return False
        if HasBlockingClaimConflict(Signal, Claims):
            return False
        return True

    def BuildGeometry(
        Signal: str,
        Candidates: tuple[ComponentTerminalAccessCandidate, ...],
        EgressPath: tuple[Position3, ...],
    ) -> tuple[
        frozenset[Position3],
        frozenset[RoutingEdge],
        RoutingResourceClaims,
    ] | None:
        Structure = BuildGeometryStructure(Signal, Candidates, EgressPath)
        if Structure is None:
            return None
        Nodes, Edges = Structure
        Claims = ClaimsForNodes(Nodes)
        if not IsGeometryClaimsEligible(Signal, Nodes, Claims):
            return None
        return Nodes, Edges, Claims

    def BuildSignalStates(
        Signal: str,
    ) -> tuple[ComponentTreeDpNetState, ...] | None:
        nonlocal PeakFrontierStateCount, DominatedStateCount
        nonlocal IncrementalPhysicalEgressMaterializationCount
        Domains = DomainsBySignal[Signal]
        DefaultPhysicalPort = SelectComponentSymbolicPhysicalPort(
            Problem,
            Signal,
        )
        PhysicalPorts = (
            tuple(PreparedPhysicalPortVariants)
            if PreparedPhysicalPortVariants
            else (DefaultPhysicalPort,)
            if DefaultPhysicalPort is not None
            else ()
        )
        if any(str(Port.Signal) != Signal for Port in PhysicalPorts):
            raise ValueError(
                "prepared physical port variants contain another signal"
            )
        CertifiedDomains = {
            frozenset(getattr(
                Port,
                "OwnedCandidateFingerprints",
                (),
            ))
            for Port in PhysicalPorts
        }
        if len(CertifiedDomains) > 1:
            raise ValueError(
                "prepared physical port variants require one exact owned "
                "candidate domain"
            )
        Certified = (
            next(iter(CertifiedDomains))
            if CertifiedDomains
            else frozenset()
        )
        CachedTerminalFrontier = (
            PreparedSymbolicNetStateContext.TerminalFrontierCache.get(
                Certified
            )
            if PreparedSymbolicNetStateContext is not None
            else None
        )
        TerminalFrontierCacheHit = CachedTerminalFrontier is not None
        if CachedTerminalFrontier is not None:
            PreparedSymbolicNetStateContext.TerminalFrontierCacheHitCount += 1
            FilteredByDomain = {
                Index: Values for Index, Values in enumerate(
                    CachedTerminalFrontier.FilteredByDomain
                )
            }
            Frontier = CachedTerminalFrontier.Frontier
            ImmutableRejected = (
                CachedTerminalFrontier.ImmutableRejectedCandidateCount
            )
            CertifiedRejected = (
                CachedTerminalFrontier.CertifiedRejectedCandidateCount
            )
            CandidateFilterEmpty = (
                CachedTerminalFrontier.CandidateFilterEmpty
            )
            PeakFrontierStateCount = max(
                PeakFrontierStateCount,
                len(Frontier),
            )
        else:
            FilteredByDomain: dict[int, tuple[
                ComponentTerminalAccessCandidate, ...
            ]] = {}
            ImmutableRejected = 0
            CertifiedRejected = 0
            for DomainIndex, Domain in enumerate(Domains):
                Retained = []
                PreparedEligibleCandidateFingerprints = (
                    PreparedSymbolicNetStateContext
                    .ImmutableEligibleCandidateFingerprintsByDomain[
                        DomainIndex
                    ]
                    if PreparedSymbolicNetStateContext is not None
                    else None
                )
                for Candidate in sorted(
                    Domain.Candidates,
                    key=lambda Value: Value.CandidateFingerprint,
                ):
                    if (
                        Certified
                        and Candidate.CandidateFingerprint not in Certified
                    ):
                        CertifiedRejected += 1
                        continue
                    if PreparedEligibleCandidateFingerprints is not None:
                        CandidateEligible = bool(
                            Candidate.CandidateFingerprint
                            in PreparedEligibleCandidateFingerprints
                        )
                    else:
                        CandidateEligible = bool(
                            Candidate.Attachment in FabricComponentByNode
                        )
                        if CandidateEligible:
                            ConflictingOwners = frozenset(
                                str(Owner)
                                for Owner, ImmutableClaims
                                in BlockingImmutableClaims(Signal)
                                if ComponentClaimsConflict(
                                    Candidate.Claims,
                                    ImmutableClaims,
                                )
                            )
                            if ConflictingOwners:
                                ImmutableBlockers = (
                                    ConflictingOwners
                                    - ReservedGlobalClaimSignals
                                )
                                ReservedBlockers = (
                                    ConflictingOwners
                                    & ReservedGlobalClaimSignals
                                )
                                if (
                                    not ImmutableBlockers
                                    and ReservedBlockers
                                ):
                                    (
                                        ReservedGlobalCandidateBlockerSetsBySignalDomain[
                                            (Signal, DomainIndex)
                                        ]
                                        .append(ReservedBlockers)
                                    )
                                CandidateEligible = False
                    if not CandidateEligible:
                        ImmutableRejected += 1
                        continue
                    Retained.append(Candidate)
                FilteredByDomain[DomainIndex] = tuple(Retained)
            CandidateFilterEmpty = any(
                not Values for Values in FilteredByDomain.values()
            )
            Frontier = ()
            if CandidateFilterEmpty:
                if PreparedSymbolicNetStateContext is not None:
                    PreparedSymbolicNetStateContext.TerminalFrontierCache[
                        Certified
                    ] = PreparedComponentSymbolicTerminalFrontier(
                        FilteredByDomain=tuple(
                            FilteredByDomain[Index]
                            for Index in range(len(Domains))
                        ),
                        Frontier=(),
                        ImmutableRejectedCandidateCount=ImmutableRejected,
                        CertifiedRejectedCandidateCount=CertifiedRejected,
                        CandidateFilterEmpty=True,
                    )
                    PreparedSymbolicNetStateContext.TerminalFrontierBuildCount += 1
        if CandidateFilterEmpty:
            MinimumCandidateGlobalRouteCore: tuple[str, ...] = ()
            CandidateBlockerSets: tuple[frozenset[str], ...] = ()
            for DomainIndex in sorted(FilteredByDomain):
                if FilteredByDomain[DomainIndex]:
                    continue
                DomainBlockerSets = tuple(
                    ReservedGlobalCandidateBlockerSetsBySignalDomain.get(
                        (Signal, DomainIndex),
                        (),
                    )
                )
                if not DomainBlockerSets:
                    continue
                BlockerUniverse = tuple(sorted(set().union(
                    *DomainBlockerSets
                )))
                DomainCore: tuple[str, ...] = ()
                for CoreSize in range(1, len(BlockerUniverse) + 1):
                    DomainCore = next((
                        CandidateCore
                        for CandidateCore in combinations(
                            BlockerUniverse,
                            CoreSize,
                        )
                        if all(
                            set(CandidateCore) & Blockers
                            for Blockers in DomainBlockerSets
                        )
                    ), ())
                    if DomainCore:
                        break
                if DomainCore and (
                    not MinimumCandidateGlobalRouteCore
                    or (
                        len(DomainCore),
                        DomainCore,
                    ) < (
                        len(MinimumCandidateGlobalRouteCore),
                        MinimumCandidateGlobalRouteCore,
                    )
                ):
                    MinimumCandidateGlobalRouteCore = DomainCore
                    CandidateBlockerSets = DomainBlockerSets
            SignalDiagnostics[Signal] = {
                "TerminalDomainSizes": [
                    len(FilteredByDomain[Index])
                    for Index in range(len(Domains))
                ],
                "ImmutableRejectedCandidateCount": ImmutableRejected,
                "CertifiedRejectedCandidateCount": CertifiedRejected,
                "EmptyPhase": "candidate-filter",
                "OwnedSignalDomainContractIndependent": False,
                "ReservedGlobalRouteBlockerSetCount": len(
                    CandidateBlockerSets
                ),
                "ReservedGlobalRouteUnsatCoreSignals": list(
                    MinimumCandidateGlobalRouteCore
                ),
                "ReservedGlobalRouteUnsatCoreComplete": bool(
                    MinimumCandidateGlobalRouteCore
                ),
                "ReservedGlobalRouteUnsatCoreFingerprint": (
                    _StableFingerprint((
                        "reserved-global-candidate-filter-core-v1",
                        Problem.ProblemFingerprint,
                        Signal,
                        tuple(sorted(CandidateBlockerSets, key=repr)),
                        MinimumCandidateGlobalRouteCore,
                    ))
                    if MinimumCandidateGlobalRouteCore
                    else ""
                ),
                "Complete": True,
                "StateCount": 0,
                "TerminalFrontierCacheHit": TerminalFrontierCacheHit,
            }
            return ()
        OrderedDomainIndexes = tuple(sorted(
            range(len(Domains)),
            key=lambda Index: (
                len(FilteredByDomain[Index]),
                Domains[Index].TerminalRole,
                Domains[Index].TerminalFingerprint,
            ),
        ))
        # (component, selected domain/candidate pairs, nodes, edges, claims)
        for Depth, DomainIndex in (
            enumerate(OrderedDomainIndexes)
            if not TerminalFrontierCacheHit
            else ()
        ):
            NextByKey: dict[
                tuple[object, ...],
                tuple[
                    int,
                    tuple[
                        tuple[int, ComponentTerminalAccessCandidate], ...
                    ],
                    frozenset[Position3],
                    frozenset[RoutingEdge],
                    RoutingResourceClaims,
                ],
            ] = {}
            Sources = Frontier or tuple(
                (
                    FabricComponentByNode[Candidate.Attachment],
                    (),
                    frozenset(),
                    frozenset(),
                    RoutingResourceClaims(),
                )
                for Candidate in FilteredByDomain[DomainIndex]
            )
            Bootstrap = not Frontier
            PendingCandidateTransitions: list[
                tuple[
                    int,
                    tuple[
                        tuple[int, ComponentTerminalAccessCandidate], ...
                    ],
                ]
            ] = []
            for SourceOffset, Source in enumerate(Sources):
                CandidateValues = (
                    (FilteredByDomain[DomainIndex][SourceOffset],)
                    if Bootstrap
                    else FilteredByDomain[DomainIndex]
                )
                for Candidate in CandidateValues:
                    if not Advance("tree-frontier-terminal"):
                        return None
                    ComponentIndex = FabricComponentByNode[
                        Candidate.Attachment
                    ]
                    if ComponentIndex != Source[0]:
                        continue
                    Selections = tuple(sorted((
                        *Source[1], (DomainIndex, Candidate),
                    )))
                    PendingCandidateTransitions.append((
                        ComponentIndex,
                        Selections,
                    ))
            FabricSubtrees, _ActiveFabricWorkers = BuildFabricSubtreeBatch(
                tuple(
                    Candidate.Attachment
                    for _DomainIndex, Candidate in Selections
                )
                for _ComponentIndex, Selections in PendingCandidateTransitions
            )
            PendingTransitions: list[
                tuple[
                    int,
                    tuple[
                        tuple[int, ComponentTerminalAccessCandidate], ...
                    ],
                    frozenset[Position3],
                    frozenset[RoutingEdge],
                ]
            ] = []
            for (
                (ComponentIndex, Selections),
                FabricSubtree,
            ) in zip(
                PendingCandidateTransitions,
                FabricSubtrees,
                strict=True,
            ):
                if FabricSubtree is None:
                    continue
                OrderedCandidates = tuple(
                    Value for _Index, Value in Selections
                )
                Structure = BuildGeometryStructure(
                    Signal,
                    OrderedCandidates,
                    (),
                    FabricSubtree,
                )
                if Structure is None:
                    continue
                Nodes, Edges = Structure
                PendingTransitions.append((
                    ComponentIndex,
                    Selections,
                    Nodes,
                    Edges,
                ))
            SameSignalReservedNodes = (
                ReservedGlobalWireCellsBySignal.get(Signal, frozenset())
            )
            ClaimNodeSets = [
                Nodes for _ComponentIndex, _Selections, Nodes, _Edges
                in PendingTransitions
            ]
            if SameSignalReservedNodes:
                ClaimNodeSets.extend(
                    frozenset((*Nodes, *SameSignalReservedNodes))
                    for _ComponentIndex, _Selections, Nodes, _Edges
                    in PendingTransitions
                )
            ClaimsByNodes = ClaimsForNodeBatch(ClaimNodeSets)
            for ComponentIndex, Selections, Nodes, Edges in PendingTransitions:
                Claims = ClaimsByNodes[Nodes]
                SameSignalCombinedClaims = (
                    ClaimsByNodes[frozenset((
                        *Nodes,
                        *SameSignalReservedNodes,
                    ))]
                    if SameSignalReservedNodes
                    else None
                )
                if not IsGeometryClaimsEligible(
                    Signal,
                    Nodes,
                    Claims,
                    SameSignalCombinedClaims,
                ):
                    continue
                Key = (Depth + 1, ComponentIndex, Nodes, Edges)
                Value = (
                    ComponentIndex, Selections, Nodes, Edges, Claims,
                )
                Existing = NextByKey.get(Key)
                if Existing is not None:
                    DominatedStateCount += 1
                    ExistingIds = tuple(
                        CandidateValue.CandidateFingerprint
                        for _Index, CandidateValue in Existing[1]
                    )
                    ValueIds = tuple(
                        CandidateValue.CandidateFingerprint
                        for _Index, CandidateValue in Value[1]
                    )
                    if ExistingIds <= ValueIds:
                        continue
                NextByKey[Key] = Value
            Frontier = tuple(
                NextByKey[Key] for Key in sorted(
                    NextByKey,
                    key=repr,
                )
            )
            PeakFrontierStateCount = max(
                PeakFrontierStateCount,
                len(Frontier),
            )
            if not Frontier:
                break

        if (
            not TerminalFrontierCacheHit
            and PreparedSymbolicNetStateContext is not None
        ):
            PreparedSymbolicNetStateContext.TerminalFrontierCache[
                Certified
            ] = PreparedComponentSymbolicTerminalFrontier(
                FilteredByDomain=tuple(
                    FilteredByDomain[Index]
                    for Index in range(len(Domains))
                ),
                Frontier=Frontier,
                ImmutableRejectedCandidateCount=ImmutableRejected,
                CertifiedRejectedCandidateCount=CertifiedRejected,
                CandidateFilterEmpty=False,
            )
            PreparedSymbolicNetStateContext.TerminalFrontierBuildCount += 1

        if not Frontier:
            BlockerSets = tuple(
                ReservedGlobalGeometryBlockerSetsBySignal.get(
                    Signal,
                    (),
                )
            )
            MinimumGlobalRouteCore: tuple[str, ...] = ()
            if BlockerSets:
                BlockerUniverse = tuple(sorted(set().union(*BlockerSets)))
                for CoreSize in range(1, len(BlockerUniverse) + 1):
                    MinimumGlobalRouteCore = next((
                        CandidateCore
                        for CandidateCore in combinations(
                            BlockerUniverse,
                            CoreSize,
                        )
                        if all(
                            set(CandidateCore) & Blockers
                            for Blockers in BlockerSets
                        )
                    ), ())
                    if MinimumGlobalRouteCore:
                        break
            ContractIndependent = bool(
                not CertifiedRejected
                and not Problem.ReservedGlobalClaimsBySignal
            )
            OwnedDomainProjectionFingerprint = _StableFingerprint((
                "tree-frontier-owned-signal-domain-v1",
                Problem.Fabric.FabricFingerprint,
                Signal,
                tuple(
                    (
                        Domain.TerminalRole,
                        Domain.TerminalFingerprint,
                        tuple(
                            (
                                Candidate.CandidateFingerprint,
                                Candidate.Attachment,
                                Candidate.Path,
                            )
                            for Candidate in Domain.Candidates
                        ),
                    )
                    for Domain in Domains
                ),
                tuple(
                    (
                        Claim.Nodes,
                        Claim.Edges,
                    )
                    for Claim in LocalClaimsBySignal.get(Signal, ())
                ),
                tuple(
                    (Owner, _ClaimsFingerprint(Claims))
                    for Owner, Claims in ImmutableClaimsBySignal
                ),
                Problem.MaximumPowerDistance,
                getattr(Problem.ResourceGraph, "GraphVersion", ""),
            ))
            SignalDiagnostics[Signal] = {
                "TerminalDomainSizes": [
                    len(FilteredByDomain[Index])
                    for Index in range(len(Domains))
                ],
                "TerminalCoverageCount": len(Domains),
                "ImmutableRejectedCandidateCount": ImmutableRejected,
                "CertifiedRejectedCandidateCount": CertifiedRejected,
                "FinalStateCount": 0,
                "EmptyPhase": "owned-terminal-frontier",
                "OwnedSignalDomainContractIndependent": (
                    ContractIndependent
                ),
                "OwnedSignalDomainProjectionFingerprint": (
                    OwnedDomainProjectionFingerprint
                ),
                "ReservedGlobalRouteBlockerSetCount": len(BlockerSets),
                "ReservedGlobalRouteUnsatCoreSignals": list(
                    MinimumGlobalRouteCore
                ),
                "ReservedGlobalRouteUnsatCoreComplete": bool(
                    MinimumGlobalRouteCore
                ),
                "ReservedGlobalRouteUnsatCoreFingerprint": (
                    _StableFingerprint((
                        "reserved-global-route-frontier-core-v1",
                        Problem.ProblemFingerprint,
                        Signal,
                        tuple(sorted(BlockerSets, key=repr)),
                        MinimumGlobalRouteCore,
                    ))
                    if MinimumGlobalRouteCore
                    else ""
                ),
                "Complete": True,
                "TerminalFrontierCacheHit": TerminalFrontierCacheHit,
            }
            return ()

        FinalByFingerprint: dict[str, ComponentTreeDpNetState] = {}
        External = tuple(
            Value
            for Value in Problem.ExternalContinuationTerminals
            if Value[0] == Signal
        )
        for (
            _ComponentIndex,
            Selections,
            PartialNodes,
            PartialEdges,
            PartialClaims,
        ) in Frontier:
            CandidatesByDomain = dict(Selections)
            Candidates = tuple(
                CandidatesByDomain[Index]
                for Index in range(len(Domains))
            )
            if External and PhysicalPorts:
                EgressVariants = tuple(
                    (tuple(Port.LocalPath), Port)
                    for Port in PhysicalPorts
                )
            elif External:
                EgressVariants = tuple(
                    (EgressPath, None)
                    for Attachment in sorted({
                        Candidate.Attachment
                        for Candidate in Candidates
                    })
                    for EgressPath in BuildComponentEgressPaths(
                        Attachment
                    )
                )
            else:
                EgressVariants = (((), None),)
            for EgressPath, ActivePhysicalPort in EgressVariants:
                if not Advance("tree-frontier-egress"):
                    return None
                EgressPath = tuple(EgressPath)
                PhysicalLocalClaims = getattr(
                    ActivePhysicalPort,
                    "LocalClaims",
                    None,
                )
                UseIncrementalPhysicalEgress = bool(
                    ActivePhysicalPort is not None
                    and EgressPath
                    and EgressPath
                    == tuple(ActivePhysicalPort.LocalPath)
                    and isinstance(
                        PhysicalLocalClaims,
                        RoutingResourceClaims,
                    )
                )
                if UseIncrementalPhysicalEgress:
                    IncrementalPhysicalEgressMaterializationCount += 1
                    ConnectorSubtree = _UniqueFabricSubtree(
                        Problem.Fabric,
                        tuple(
                            Candidate.Attachment
                            for Candidate in Candidates
                        ) + (EgressPath[0],),
                        Adjacency=FabricAdjacency,
                        ParentCache=FabricParentCache,
                    )
                    if ConnectorSubtree is None:
                        Geometry = None
                    else:
                        ConnectorNodes, ConnectorEdges = ConnectorSubtree
                        DeltaNodes = frozenset((
                            *(ConnectorNodes - PartialNodes),
                            *EgressPath,
                        ))
                        Nodes = frozenset((
                            *PartialNodes,
                            *ConnectorNodes,
                            *EgressPath,
                        ))
                        Edges = frozenset((
                            *PartialEdges,
                            *ConnectorEdges,
                            *(
                                _NormalizedEdge(First, Second)
                                for First, Second in zip(
                                    EgressPath,
                                    EgressPath[1:],
                                )
                            ),
                        ))
                        Claims = _MergeClaims((
                            PartialClaims,
                            ClaimsForNodes(DeltaNodes),
                        ))
                        Geometry = (
                            None
                            if FindSelfClaimConflicts({Signal: Claims})
                            or HasSameSignalReservedSelfConflict(
                                Signal,
                                Nodes,
                            )
                            or HasBlockingClaimConflict(Signal, Claims)
                            else (Nodes, Edges, Claims)
                        )
                else:
                    Geometry = BuildGeometry(
                        Signal,
                        Candidates,
                        EgressPath,
                    )
                if Geometry is None:
                    continue
                Nodes, Edges, Claims = Geometry
                if ActivePhysicalPort is not None and (
                    (
                        frozenset(getattr(
                            ActivePhysicalPort,
                            "GlobalPath",
                            (),
                        ))
                        - frozenset((getattr(
                            ActivePhysicalPort,
                            "Attachment",
                            None,
                        ),))
                    )
                    & Nodes
                ):
                    # The seam attachment is shared by the two ownership
                    # halves, but every later exterior node is globally
                    # owned. Reject the geometry during frontier
                    # compilation instead of discovering the violation only
                    # when validating an otherwise feasible template.
                    continue
                ExportedPorts = (
                    (tuple(EgressPath[-1]),)
                    if External and EgressPath
                    else ()
                )
                if (
                    Signal in ForbiddenExportPortsBySignal
                    and ExportedPorts
                    == ForbiddenExportPortsBySignal[Signal]
                ):
                    continue
                SourceIndexes = tuple(
                    Index
                    for Index, Domain in enumerate(Domains)
                    if Domain.TerminalRole == "source"
                )
                RootIndex = SourceIndexes[0] if SourceIndexes else 0
                Root = Domains[RootIndex].Terminal
                if Root not in Nodes:
                    Root = Candidates[RootIndex].Path[0]
                if ExportedPorts and any(
                    Role == "source"
                    for _Signal, _Terminal, Role in External
                ):
                    Root = ExportedPorts[0]
                Repeaters = _PlanTreeRepeaters(
                    Nodes,
                    Edges,
                    Root,
                    Problem.MaximumPowerDistance,
                    SubproblemCache=(
                        PreparedSymbolicNetStateContext
                        .TreeRepeaterSubproblemCache
                        if PreparedSymbolicNetStateContext is not None
                        else None
                    ),
                    CacheStatistics=(
                        PreparedSymbolicNetStateContext
                        .TreeRepeaterCacheStatistics
                        if PreparedSymbolicNetStateContext is not None
                        else None
                    ),
                )
                if Repeaters is None:
                    continue
                if Problem.ResourceGraph is not None and any(
                    Problem.ResourceGraph.BuildPrimitive(First, Second)
                    is None
                    for First, Second in Edges
                ):
                    continue
                NetFingerprint = _StableFingerprint((
                    tuple(sorted(Nodes)),
                    tuple(sorted(Edges)),
                    tuple(Position for Position, _Facing in Repeaters),
                    tuple(sorted(ExportedPorts)),
                    tuple(sorted(Claims.WireCells)),
                    tuple(sorted(Claims.SupportCells)),
                    tuple(sorted(Claims.RequiredAirCells)),
                    tuple(sorted(Claims.ElectricalCells)),
                ))
                FinalByFingerprint.setdefault(
                    NetFingerprint,
                    ComponentTreeDpNetState(
                        Signal=Signal,
                        Candidates=Candidates,
                        EgressPath=tuple(EgressPath),
                        Nodes=Nodes,
                        Edges=Edges,
                        Claims=Claims,
                        Root=Root,
                        Repeaters=Repeaters,
                        CoveredTerminals=tuple(sorted(
                            Domain.Terminal for Domain in Domains
                        )),
                        ExportedPorts=ExportedPorts,
                        NetFingerprint=NetFingerprint,
                    ),
                )
        Result = tuple(
            FinalByFingerprint[Fingerprint]
            for Fingerprint in sorted(FinalByFingerprint)
        )
        EgressBlockerSets = tuple(
            ReservedGlobalGeometryBlockerSetsBySignal.get(
                Signal,
                (),
            )
        )
        MinimumEgressGlobalRouteCore: tuple[str, ...] = ()
        CompleteExteriorBlockerSets = EgressBlockerSets
        if not Result:
            CandidateDomainCores = []
            for DomainIndex in range(len(Domains)):
                DomainBlockerSets = tuple(
                    ReservedGlobalCandidateBlockerSetsBySignalDomain.get(
                        (Signal, DomainIndex),
                        (),
                    )
                )
                if not DomainBlockerSets:
                    continue
                BlockerUniverse = tuple(sorted(set().union(
                    *DomainBlockerSets
                )))
                DomainCore: tuple[str, ...] = ()
                for CoreSize in range(1, len(BlockerUniverse) + 1):
                    DomainCore = next((
                        CandidateCore
                        for CandidateCore in combinations(
                            BlockerUniverse,
                            CoreSize,
                        )
                        if all(
                            set(CandidateCore) & Blockers
                            for Blockers in DomainBlockerSets
                        )
                    ), ())
                    if DomainCore:
                        break
                if DomainCore:
                    CandidateDomainCores.append((
                        DomainCore,
                        DomainBlockerSets,
                    ))
            if CandidateDomainCores:
                (
                    MinimumEgressGlobalRouteCore,
                    CompleteExteriorBlockerSets,
                ) = min(
                    CandidateDomainCores,
                    key=lambda Value: (
                        len(Value[0]),
                        Value[0],
                    ),
                )
            elif EgressBlockerSets:
                BlockerUniverse = tuple(sorted(set().union(
                    *EgressBlockerSets
                )))
                for CoreSize in range(1, len(BlockerUniverse) + 1):
                    MinimumEgressGlobalRouteCore = next((
                        CandidateCore
                        for CandidateCore in combinations(
                            BlockerUniverse,
                            CoreSize,
                        )
                        if all(
                            set(CandidateCore) & Blockers
                            for Blockers in EgressBlockerSets
                        )
                    ), ())
                    if MinimumEgressGlobalRouteCore:
                        break
        SignalDiagnostics[Signal] = {
            "TerminalDomainSizes": [
                len(FilteredByDomain[Index])
                for Index in range(len(Domains))
            ],
            "TerminalCoverageCount": len(Domains),
            "ImmutableRejectedCandidateCount": ImmutableRejected,
            "CertifiedRejectedCandidateCount": CertifiedRejected,
            "FinalStateCount": len(Result),
            "EmptyPhase": (
                "fixed-egress-or-power" if not Result else ""
            ),
            "OwnedSignalDomainContractIndependent": False,
            "ReservedGlobalRouteBlockerSetCount": len(
                CompleteExteriorBlockerSets
            ),
            "ReservedGlobalRouteUnsatCoreSignals": list(
                MinimumEgressGlobalRouteCore
            ),
            "ReservedGlobalRouteUnsatCoreComplete": bool(
                MinimumEgressGlobalRouteCore
            ),
            "ReservedGlobalRouteUnsatCoreFingerprint": (
                _StableFingerprint((
                    "reserved-global-route-egress-core-v1",
                    Problem.ProblemFingerprint,
                    Signal,
                    tuple(sorted(
                        CompleteExteriorBlockerSets,
                        key=repr,
                    )),
                    MinimumEgressGlobalRouteCore,
                ))
                if MinimumEgressGlobalRouteCore
                else ""
            ),
            "Complete": True,
            "TerminalFrontierCacheHit": TerminalFrontierCacheHit,
        }
        return Result

    RequestedSignals = (
        frozenset(Problem.ComponentSignals)
        if RequestedSymbolicStateSignals is None
        else frozenset(map(str, RequestedSymbolicStateSignals))
    )
    UnknownRequestedSignals = RequestedSignals.difference(
        Problem.ComponentSignals
    )
    if UnknownRequestedSignals:
        raise ValueError(
            "requested symbolic state signals are outside the component: "
            + ", ".join(sorted(UnknownRequestedSignals))
        )
    if (
        PreparedSymbolicNetStateContext is not None
        and RequestedSignals
        != frozenset((PreparedSymbolicNetStateContext.Signal,))
    ):
        raise ValueError(
            "prepared symbolic net-state context requires its exact signal"
        )
    NetStatesBySignal: dict[
        str, tuple[ComponentTreeDpNetState, ...]
    ] = {}
    for Signal in sorted(
        RequestedSignals,
        key=lambda Value: (
            sum(
                len(Domain.Candidates)
                for Domain in DomainsBySignal[Value]
            ),
            Value,
        ),
    ):
        CacheKey = BuildComponentSymbolicNetStateCacheKey(
            Problem,
            Signal,
            ForbiddenExportPortsBySignal,
        )
        CachedNetState = NetStateCache.get(CacheKey)
        if CachedNetState is not None:
            States, CachedDiagnostics = CachedNetState
            SignalDiagnostics[Signal] = dict(CachedDiagnostics)
            SignalDiagnostics[Signal]["SymbolicNetStateCacheHit"] = True
            SolverDiagnostics["SymbolicNetStateCacheHitCount"] = int(
                SolverDiagnostics["SymbolicNetStateCacheHitCount"]
            ) + 1
        else:
            States = BuildSignalStates(Signal)
            if States is not None:
                CachedDiagnostics = dict(
                    SignalDiagnostics.get(Signal, {})
                )
                CachedDiagnostics["SymbolicNetStateCacheHit"] = False
                NetStateCache[CacheKey] = (
                    States,
                    CachedDiagnostics,
                )
                SolverDiagnostics["SymbolicNetStateCacheStoreCount"] = int(
                    SolverDiagnostics["SymbolicNetStateCacheStoreCount"]
                ) + 1
        if States is None:
            return ComponentRoutingSolveResult(
                Status="incomplete",
                ProofFingerprint=_StableFingerprint((
                    Problem.ProblemFingerprint,
                    "tree-frontier-limit",
                    ExpansionCount,
                )),
                ExpansionCount=ExpansionCount,
                Detail="component state work or deadline cap reached",
                Diagnostics=FinishDiagnostics(),
            )
        if not States:
            EmptyDiagnostics = SignalDiagnostics.get(Signal, {})
            ContractIndependentOwnedDomain = bool(
                EmptyDiagnostics.get("EmptyPhase")
                == "owned-terminal-frontier"
                and EmptyDiagnostics.get(
                    "OwnedSignalDomainContractIndependent",
                    False,
                )
            )
            CoreKind = (
                "tree-frontier-empty-owned-signal-domain"
                if ContractIndependentOwnedDomain
                else "tree-frontier-empty-signal"
            )
            return ComponentRoutingSolveResult(
                Status="architectural-unsatisfiable",
                ProofFingerprint=_StableFingerprint((
                    Problem.ProblemFingerprint,
                    Signal,
                    CoreKind,
                )),
                ExpansionCount=ExpansionCount,
                Detail="a component net has no powered frontier state",
                Diagnostics={
                    **FinishDiagnostics(),
                    "LocalUnsatCoreComplete": True,
                    "LocalUnsatCoreSignals": [Signal],
                    "LocalUnsatCoreKind": CoreKind,
                    "LocalUnsatCoreVariableKinds": ["net"],
                    "LocalUnsatCoreProjectionFingerprint": str(
                        EmptyDiagnostics.get(
                            "OwnedSignalDomainProjectionFingerprint",
                            "",
                        )
                    ),
                    "LocalUnsatCoreFingerprint": _StableFingerprint((
                        Problem.ProblemFingerprint,
                        Signal,
                        CoreKind,
                    )),
                    "SymbolicCapacityProofComplete": bool(
                        StopAfterSymbolicCapacityProof
                    ),
                    "SymbolicCapacityFeasible": False,
                },
            )
        NetStatesBySignal[Signal] = States

    if StopAfterOwnedSignalFrontierProof:
        ProofFingerprint = _StableFingerprint((
            Problem.ProblemFingerprint,
            "owned-signal-frontier-feasible",
            tuple(
                (Signal, len(States))
                for Signal, States in sorted(NetStatesBySignal.items())
            ),
        ))
        return ComponentRoutingSolveResult(
            Status="frontier-feasible",
            ProofFingerprint=ProofFingerprint,
            ExpansionCount=ExpansionCount,
            Detail=(
                "every owned component signal has a powered frontier state"
            ),
            Diagnostics={
                **FinishDiagnostics(),
                "OwnedSignalFrontierProofComplete": True,
                "OwnedSignalFrontierFeasible": True,
                "OwnedSignalFrontierStateCounts": {
                    Signal: len(States)
                    for Signal, States in sorted(NetStatesBySignal.items())
                },
            },
        )

    # Solve symbolic net states and passive interface witnesses in one exact
    # capacity CSP.  Only the selected symbolic nets are materialized below.
    Variables: list[
        tuple[str, str, str, tuple[Any, ...], Callable[[Any], Any]]
    ] = []
    for Signal, States in NetStatesBySignal.items():
        Variables.append((
            "net",
            Signal,
            Signal,
            States,
            lambda Value: Value.Claims,
        ))
    for DomainIndex, Domain in enumerate(
        Problem.ExternalContinuationDomains
    ):
        Variables.append((
            "continuation",
            str(DomainIndex),
            Domain.Signal,
            Domain.Candidates,
            lambda Value: Value.Claims,
        ))
    for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains):
        Forbidden = ForbiddenForeignCandidateFingerprintsBySignal.get(
            Domain.Signal,
            frozenset(),
        )
        Variables.append((
            "foreign",
            str(DomainIndex),
            Domain.Signal,
            tuple(
                Candidate
                for Candidate in Domain.Candidates
                if Candidate.CandidateFingerprint not in Forbidden
            ),
            lambda Value: Value.Claims,
        ))
    TransitBySignal = {
        Domain.Signal: (Index, Domain)
        for Index, Domain in enumerate(Problem.ForeignTransitDomains)
    }
    MissingTransitSignals = tuple(sorted(
        RequiredForeignTransitSignals - TransitBySignal.keys()
    ))
    if MissingTransitSignals:
        return ComponentRoutingSolveResult(
            Status="architectural-unsatisfiable",
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                "missing-required-transit",
                MissingTransitSignals,
            )),
            ExpansionCount=ExpansionCount,
            Detail="required foreign transit has no finite domain",
            Diagnostics=FinishDiagnostics(),
        )
    for Signal in sorted(RequiredForeignTransitSignals):
        DomainIndex, Domain = TransitBySignal[Signal]
        Variables.append((
            "transit",
            str(DomainIndex),
            Signal,
            Domain.Candidates,
            lambda Value: Value.Claims,
        ))

    StaticClaims = tuple(
        (str(Claim.Signal), Claim.Claims)
        for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        if Claim.Signal not in Problem.ComponentSignals
    )
    Domains: dict[int, tuple[int, ...]] = {}
    for VariableIndex, Variable in enumerate(Variables):
        _Kind, _Identity, Owner, Options, ClaimsFor = Variable
        Domains[VariableIndex] = tuple(
            OptionIndex
            for OptionIndex, Option in enumerate(Options)
            if all(
                ComponentClaimsCompatibleForOwners(
                    Owner,
                    ClaimsFor(Option),
                    StaticOwner,
                    StaticValue,
                )
                for StaticOwner, StaticValue in StaticClaims
            )
        )
    if any(not Domain for Domain in Domains.values()):
        EmptyIndexes = tuple(sorted(
            Index for Index, Domain in Domains.items() if not Domain
        ))
        EmptySignals = tuple(sorted({
            Variables[Index][2] for Index in EmptyIndexes
        }))
        ProjectionFingerprint = _StableFingerprint((
            "complete-symbolic-empty-capacity-domain-v1",
            Problem.ProblemFingerprint,
            tuple(
                (
                    Variables[Index][0],
                    Variables[Index][1],
                    Variables[Index][2],
                )
                for Index in EmptyIndexes
            ),
            tuple(
                (
                    Owner,
                    _ClaimsFingerprint(Claims),
                )
                for Owner, Claims in StaticClaims
            ),
        ))
        return ComponentRoutingSolveResult(
            Status="architectural-unsatisfiable",
            ProofFingerprint=ProjectionFingerprint,
            ExpansionCount=ExpansionCount,
            Detail="a complete symbolic capacity domain is empty",
            Diagnostics={
                **FinishDiagnostics(),
                "SymbolicCapacityProofComplete": True,
                "SymbolicCapacityFeasible": False,
                "LocalUnsatCoreComplete": True,
                "LocalUnsatCoreKind": (
                    "complete-symbolic-empty-capacity-domain"
                ),
                "LocalUnsatCoreSignals": list(EmptySignals),
                "LocalUnsatCoreProjectionFingerprint": (
                    ProjectionFingerprint
                ),
                "LocalUnsatCoreFingerprint": ProjectionFingerprint,
                "LocalUnsatCoreVariableCount": len(EmptyIndexes),
                "LocalUnsatCoreVariableKinds": [
                    Variables[Index][0] for Index in EmptyIndexes
                ],
            },
        )

    def CapacityOptionFingerprint(Kind: str, Value: Any) -> str:
        return str(
            Value.NetFingerprint
            if Kind in {"net", "transit"}
            else Value.CandidateFingerprint
        )

    # Detect a complete binary support contradiction before recursive CSP
    # search.  Every domain above has already been filtered against immutable
    # claims, so an option-pair Cartesian product with zero compatible pairs
    # is a self-contained local-contract core.  Publishing that exact core
    # lets the post-handoff factor portfolio reject the family instead of
    # learning one whole port tuple per globally routed plan.
    for FirstIndex, SecondIndex in combinations(sorted(Domains), 2):
        (
            FirstKind,
            FirstIdentity,
            FirstOwner,
            FirstOptions,
            FirstClaimsFor,
        ) = Variables[FirstIndex]
        (
            SecondKind,
            SecondIdentity,
            SecondOwner,
            SecondOptions,
            SecondClaimsFor,
        ) = Variables[SecondIndex]
        if any(
            ComponentClaimsCompatibleForOwners(
                FirstOwner,
                FirstClaimsFor(FirstOptions[FirstOptionIndex]),
                SecondOwner,
                SecondClaimsFor(SecondOptions[SecondOptionIndex]),
            )
            for FirstOptionIndex in Domains[FirstIndex]
            for SecondOptionIndex in Domains[SecondIndex]
        ):
            continue
        CoreSignals = tuple(sorted({FirstOwner, SecondOwner}))
        ProjectionFingerprint = _StableFingerprint((
            "complete-symbolic-capacity-pair-v1",
            Problem.ProblemFingerprint,
            (
                FirstKind,
                FirstIdentity,
                FirstOwner,
                tuple(
                    CapacityOptionFingerprint(
                        FirstKind,
                        FirstOptions[OptionIndex],
                    )
                    for OptionIndex in Domains[FirstIndex]
                ),
            ),
            (
                SecondKind,
                SecondIdentity,
                SecondOwner,
                tuple(
                    CapacityOptionFingerprint(
                        SecondKind,
                        SecondOptions[OptionIndex],
                    )
                    for OptionIndex in Domains[SecondIndex]
                ),
            ),
        ))
        return ComponentRoutingSolveResult(
            Status="architectural-unsatisfiable",
            ProofFingerprint=ProjectionFingerprint,
            ExpansionCount=ExpansionCount,
            Detail=(
                "two complete symbolic capacity domains have no "
                "compatible option pair"
            ),
            Diagnostics={
                **FinishDiagnostics(),
                "SymbolicCapacityProofComplete": True,
                "SymbolicCapacityFeasible": False,
                "LocalUnsatCoreComplete": True,
                "LocalUnsatCoreKind": (
                    "complete-symbolic-capacity-pair"
                ),
                "LocalUnsatCoreSignals": list(CoreSignals),
                "LocalUnsatCoreCurrentSignal": FirstOwner,
                "LocalUnsatCoreCompleteSignal": SecondOwner,
                "LocalUnsatCoreProjectionFingerprint": (
                    ProjectionFingerprint
                ),
                "LocalUnsatCoreFingerprint": ProjectionFingerprint,
                "LocalUnsatCoreVariableKinds": [
                    FirstKind,
                    SecondKind,
                ],
            },
        )

    Selected: dict[tuple[str, str], Any] = {}
    SelectedClaims: list[tuple[str, RoutingResourceClaims]] = list(
        StaticClaims
    )

    def SelectedForeignAssignments() -> frozenset[
        tuple[str, Position3, str]
    ]:
        return frozenset(
            (
                Domain.Signal,
                Domain.Terminal,
                Candidate.CandidateFingerprint,
            )
            for DomainIndex, Domain in enumerate(
                Problem.ForeignEscapeDomains
            )
            if (
                Candidate := Selected.get((
                    "foreign",
                    str(DomainIndex),
                ))
            ) is not None
        )

    def ViolatesForbiddenForeignPair() -> bool:
        Current = SelectedForeignAssignments()
        return any(
            ForbiddenPair <= Current
            for ForbiddenPair in ForbiddenForeignAssignmentPairs
        )

    def OptionFingerprint(Kind: str, Value: Any) -> str:
        return CapacityOptionFingerprint(Kind, Value)

    def Search(
        Remaining: dict[int, tuple[int, ...]],
    ) -> bool:
        if not Remaining:
            if ViolatesForbiddenForeignPair():
                return False
            AssignmentFingerprint = _StableFingerprint(tuple(sorted(
                (
                    Kind,
                    Identity,
                    OptionFingerprint(Kind, Value),
                )
                for (Kind, Identity), Value in Selected.items()
            )))
            if AssignmentFingerprint in ForbiddenAssignmentFingerprints:
                return False
            SolverDiagnostics["SelectedAssignmentFingerprint"] = (
                AssignmentFingerprint
            )
            return True
        SelectedIndex = min(
            Remaining,
            key=lambda Index: (
                len(Remaining[Index]),
                0 if Variables[Index][0] == "net" else 1,
                Variables[Index][0],
                Variables[Index][1],
            ),
        )
        Kind, Identity, Owner, Options, ClaimsFor = Variables[
            SelectedIndex
        ]
        for OptionIndex in Remaining[SelectedIndex]:
            if not Advance("tree-frontier-capacity"):
                return False
            Option = Options[OptionIndex]
            Claims = ClaimsFor(Option)
            if any(
                not ComponentClaimsCompatibleForOwners(
                    Owner,
                    Claims,
                    SelectedOwner,
                    SelectedValue,
                )
                for SelectedOwner, SelectedValue in SelectedClaims
            ):
                continue
            Next: dict[int, tuple[int, ...]] = {}
            ForwardLegal = True
            for OtherIndex, OtherDomain in Remaining.items():
                if OtherIndex == SelectedIndex:
                    continue
                OtherOwner = Variables[OtherIndex][2]
                OtherOptions = Variables[OtherIndex][3]
                OtherClaimsFor = Variables[OtherIndex][4]
                Filtered = tuple(
                    OtherOptionIndex
                    for OtherOptionIndex in OtherDomain
                    if ComponentClaimsCompatibleForOwners(
                        Owner,
                        Claims,
                        OtherOwner,
                        OtherClaimsFor(
                            OtherOptions[OtherOptionIndex]
                        ),
                    )
                )
                if not Filtered:
                    ForwardLegal = False
                    break
                Next[OtherIndex] = Filtered
            if not ForwardLegal:
                continue
            Selected[(Kind, Identity)] = Option
            SelectedClaims.append((Owner, Claims))
            if not ViolatesForbiddenForeignPair() and Search(Next):
                return True
            SelectedClaims.pop()
            del Selected[(Kind, Identity)]
        return False

    Feasible = Search(Domains)
    if not Feasible:
        Status = "incomplete" if HitLimit else "architectural-unsatisfiable"
        if (
            not HitLimit
            and not ForbiddenAssignmentFingerprints
            and not ForbiddenForeignAssignmentPairs
        ):
            def CapacitySubsetHasSupport(
                VariableIndexes: tuple[int, ...],
            ) -> bool:
                SelectedSubsetClaims = list(StaticClaims)

                def SearchSubset(RemainingIndexes: tuple[int, ...]) -> bool:
                    if not RemainingIndexes:
                        return True
                    SelectedIndex = min(
                        RemainingIndexes,
                        key=lambda Index: (
                            len(Domains[Index]),
                            Variables[Index][0],
                            Variables[Index][1],
                        ),
                    )
                    (
                        _Kind,
                        _Identity,
                        Owner,
                        Options,
                        ClaimsFor,
                    ) = Variables[SelectedIndex]
                    NextIndexes = tuple(
                        Index for Index in RemainingIndexes
                        if Index != SelectedIndex
                    )
                    for OptionIndex in Domains[SelectedIndex]:
                        Claims = ClaimsFor(Options[OptionIndex])
                        if any(
                            not ComponentClaimsCompatibleForOwners(
                                Owner,
                                Claims,
                                SelectedOwner,
                                SelectedClaims,
                            )
                            for SelectedOwner, SelectedClaims
                            in SelectedSubsetClaims
                        ):
                            continue
                        SelectedSubsetClaims.append((Owner, Claims))
                        if SearchSubset(NextIndexes):
                            return True
                        SelectedSubsetClaims.pop()
                    return False

                return SearchSubset(VariableIndexes)

            CoreIndexes = tuple(sorted(Domains))
            if not CapacitySubsetHasSupport(CoreIndexes):
                for VariableIndex in tuple(CoreIndexes):
                    CandidateCore = tuple(
                        Index for Index in CoreIndexes
                        if Index != VariableIndex
                    )
                    if (
                        CandidateCore
                        and not CapacitySubsetHasSupport(CandidateCore)
                    ):
                        CoreIndexes = CandidateCore
                CoreSignals = tuple(sorted({
                    Variables[Index][2] for Index in CoreIndexes
                }))
                CoreProjectionFingerprint = _StableFingerprint((
                    "complete-symbolic-capacity-core-v1",
                    Problem.ProblemFingerprint,
                    tuple(
                        (
                            Variables[Index][0],
                            Variables[Index][1],
                            Variables[Index][2],
                            tuple(
                                CapacityOptionFingerprint(
                                    Variables[Index][0],
                                    Variables[Index][3][OptionIndex],
                                )
                                for OptionIndex in Domains[Index]
                            ),
                        )
                        for Index in CoreIndexes
                    ),
                ))
                return ComponentRoutingSolveResult(
                    Status="architectural-unsatisfiable",
                    ProofFingerprint=CoreProjectionFingerprint,
                    ExpansionCount=ExpansionCount,
                    Detail=(
                        "a deletion-minimal complete symbolic capacity "
                        "core has no compatible assignment"
                    ),
                    Diagnostics={
                        **FinishDiagnostics(),
                        "SymbolicCapacityProofComplete": True,
                        "SymbolicCapacityFeasible": False,
                        "LocalUnsatCoreComplete": True,
                        "LocalUnsatCoreKind": (
                            "complete-symbolic-capacity-core"
                        ),
                        "LocalUnsatCoreSignals": list(CoreSignals),
                        "LocalUnsatCoreProjectionFingerprint": (
                            CoreProjectionFingerprint
                        ),
                        "LocalUnsatCoreFingerprint": (
                            CoreProjectionFingerprint
                        ),
                        "LocalUnsatCoreVariableCount": len(CoreIndexes),
                        "LocalUnsatCoreVariableKinds": [
                            Variables[Index][0] for Index in CoreIndexes
                        ],
                    },
                )
        return ComponentRoutingSolveResult(
            Status=Status,
            ProofFingerprint=_StableFingerprint((
                Problem.ProblemFingerprint,
                Status,
                ExpansionCount,
                "tree-frontier-dp-v1",
            )),
            ExpansionCount=ExpansionCount,
            Detail=(
                "component state work or deadline cap reached"
                if HitLimit
                else "complete symbolic component state space exhausted"
            ),
            Diagnostics={
                **FinishDiagnostics(),
                "SymbolicCapacityProofComplete": not HitLimit,
                "SymbolicCapacityFeasible": False,
            },
        )

    if StopAfterSymbolicCapacityProof:
        ProofFingerprint = _StableFingerprint((
            Problem.ProblemFingerprint,
            "symbolic-capacity-feasible",
            SolverDiagnostics.get("SelectedAssignmentFingerprint", ""),
        ))
        return ComponentRoutingSolveResult(
            Status="capacity-feasible",
            ProofFingerprint=ProofFingerprint,
            ExpansionCount=ExpansionCount,
            Detail=(
                "the closed component symbolic capacity CSP is feasible"
            ),
            Diagnostics={
                **FinishDiagnostics(),
                "SymbolicCapacityProofComplete": True,
                "SymbolicCapacityFeasible": True,
            },
        )

    Nets = tuple(sorted(
        (
            RoutedComponentNet(
                Signal=State.Signal,
                Root=State.Root,
                Nodes=State.Nodes,
                Edges=State.Edges,
                WireCells=(
                    State.Claims.WireCells
                    - frozenset(
                        Position for Position, _Facing in State.Repeaters
                    )
                ),
                SupportCells=State.Claims.SupportCells,
                Repeaters=State.Repeaters,
                Claims=State.Claims,
                CoveredTerminals=State.CoveredTerminals,
                ExportedPorts=State.ExportedPorts,
                NetFingerprint=State.NetFingerprint,
            )
            for Signal in Problem.ComponentSignals
            for State in (Selected[("net", Signal)],)
        ),
        key=lambda Value: Value.NetFingerprint,
    ))
    Foreign = tuple(sorted(
        (
            (
                Domain.Signal,
                Domain.Terminal,
                Selected[("foreign", str(DomainIndex))],
            )
            for DomainIndex, Domain in enumerate(
                Problem.ForeignEscapeDomains
            )
        ),
        key=lambda Value: (Value[2].CandidateFingerprint, Value[1]),
    ))
    ExternalContinuations = tuple(sorted(
        (
            (
                Domain.Signal,
                Domain.Terminal,
                Selected[("continuation", str(DomainIndex))],
            )
            for DomainIndex, Domain in enumerate(
                Problem.ExternalContinuationDomains
            )
        ),
        key=lambda Value: (
            Value[0],
            Value[1],
            Value[2].CandidateFingerprint,
        ),
    ))
    ForeignTransits = tuple(sorted(
        (
            Selected[("transit", str(DomainIndex))]
            for DomainIndex, Domain in enumerate(
                Problem.ForeignTransitDomains
            )
            if Domain.Signal in RequiredForeignTransitSignals
        ),
        key=lambda Value: Value.NetFingerprint,
    ))
    Claims = _MergeClaims((
        *(Value.Claims for Value in Nets),
        *(Value[2].Claims for Value in ExternalContinuations),
        *(Value[2].Claims for Value in Foreign),
        *(Value.Claims for Value in ForeignTransits),
    ))
    ExportedPorts = tuple(sorted(
        (Net.Signal, Position)
        for Net in Nets
        for Position in Net.ExportedPorts
    ))
    ExportedPortFingerprint = _StableFingerprint(tuple(
        _RelativeGeometry(Position for _Signal, Position in ExportedPorts)
    ))
    ClaimsFingerprint = _ClaimsFingerprint(Claims)
    RoutedTemplateFingerprint = _StableFingerprint((
        Problem.ProblemFingerprint,
        tuple(Net.NetFingerprint for Net in Nets),
        tuple(Value[2].CandidateFingerprint for Value in Foreign),
        tuple(
            Value[2].CandidateFingerprint
            for Value in ExternalContinuations
        ),
        tuple(Value.NetFingerprint for Value in ForeignTransits),
        ExportedPortFingerprint,
        ClaimsFingerprint,
    ))
    ProofFingerprint = _StableFingerprint((
        RoutedTemplateFingerprint,
        ExpansionCount,
        "feasible",
        "tree-frontier-dp-v1",
    ))
    SolverDiagnostics["SelectedTreesMaterialized"] = len(Nets)
    Template = RoutedComponentTemplate(
        ProblemFingerprint=Problem.ProblemFingerprint,
        PlacementFingerprint=Problem.PlacementFingerprint,
        LocalTemplateFingerprint=Problem.LocalTemplateFingerprint,
        FabricFingerprint=Problem.Fabric.FabricFingerprint,
        RoutedTemplateFingerprint=RoutedTemplateFingerprint,
        Nets=Nets,
        ForeignEscapeReservations=Foreign,
        ExportedPorts=ExportedPorts,
        Claims=Claims,
        ExportedPortFingerprint=ExportedPortFingerprint,
        ClaimsFingerprint=ClaimsFingerprint,
        ProofFingerprint=ProofFingerprint,
        ExpansionCount=ExpansionCount,
        Diagnostics=FinishDiagnostics(),
        ExternalContinuationReservations=ExternalContinuations,
        ForeignTransitReservations=ForeignTransits,
        InterfaceFingerprint=(
            Problem.Interface.InterfaceFingerprint
            if Problem.Interface is not None
            else ""
        ),
    )
    return ComponentRoutingSolveResult(
        Status="feasible",
        Template=Template,
        ProofFingerprint=ProofFingerprint,
        ExpansionCount=ExpansionCount,
        Diagnostics=Template.Diagnostics,
    )


def CompilePreparedComponentSymbolicNetStates(
    Context: PreparedComponentSymbolicNetStateContext,
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    SymbolicNetStateCache: dict[str, Any] | None = None,
    ForbiddenExportPorts: tuple[Position3, ...] = (),
) -> PreparedComponentSymbolicNetStateCompilation:
    """Compile one access-bound state domain using reusable static tree data."""
    Signal = Context.Signal
    if (
        _BuildPreparedComponentSymbolicNetStateContextFingerprint(
            Problem,
            Signal,
        )
        != Context.ContextFingerprint
    ):
        raise ValueError(
            "prepared symbolic net-state compilation identity mismatch"
        )
    Forbidden = (
        {Signal: tuple(ForbiddenExportPorts)}
        if ForbiddenExportPorts
        else {}
    )
    Cache = (
        SymbolicNetStateCache
        if SymbolicNetStateCache is not None
        else {}
    )
    CacheKey = BuildComponentSymbolicNetStateCacheKey(
        Problem,
        Signal,
        Forbidden,
    )
    Cached = Cache.get(CacheKey)
    if Cached is not None:
        States, Diagnostics = Cached
        return PreparedComponentSymbolicNetStateCompilation(
            CacheKey=CacheKey,
            States=tuple(States),
            Complete=True,
            CacheHit=True,
            ExpansionCount=0,
            Diagnostics={
                **dict(Diagnostics),
                "SymbolicNetStateCacheHit": True,
                "PreparedSymbolicNetStateContextFingerprint": (
                    Context.ContextFingerprint
                ),
            },
        )
    Result = SolveComponentRoutingProblemDynamic(
        Problem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        ForbiddenExportPortsBySignal=Forbidden,
        RouteClaimsConstructionCache=(
            Context.RouteClaimsConstructionCache
        ),
        SymbolicNetStateCache=Cache,
        RequestedSymbolicStateSignals=frozenset((Signal,)),
        PreparedSymbolicNetStateContext=Context,
        StopAfterOwnedSignalFrontierProof=True,
    )
    Cached = Cache.get(CacheKey)
    Complete = bool(Result.Status != "incomplete" and Cached is not None)
    States = tuple(Cached[0]) if Cached is not None else None
    Diagnostics = dict(Cached[1]) if Cached is not None else {}
    Diagnostics.update({
        "SymbolicNetStateCacheHit": False,
        "PreparedSymbolicNetStateContextFingerprint": (
            Context.ContextFingerprint
        ),
        "PreparedSymbolicNetStateResultStatus": Result.Status,
    })
    return PreparedComponentSymbolicNetStateCompilation(
        CacheKey=CacheKey,
        States=States,
        Complete=Complete,
        CacheHit=False,
        ExpansionCount=Result.ExpansionCount,
        Diagnostics=Diagnostics,
    )


def CompilePreparedComponentPhysicalFactorStateBatch(
    Context: PreparedComponentSymbolicNetStateContext,
    ProblemsByAccess: Mapping[str, ComponentRoutingProblem],
    *,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    SymbolicNetStateCache: dict[str, Any] | None = None,
) -> dict[str, PreparedComponentSymbolicNetStateCompilation]:
    """Compile exact physical egress factors in shared frontier batches.

    Factors with the same certified owned-candidate domain share one dynamic
    solver invocation.  Results are then partitioned by the immutable local
    egress path and stored under the unchanged per-factor cache identities.
    """
    Cache = (
        SymbolicNetStateCache
        if SymbolicNetStateCache is not None
        else {}
    )
    StartedAt = monotonic()
    Results: dict[str, PreparedComponentSymbolicNetStateCompilation] = {}
    MissesByCertifiedDomain: dict[
        frozenset[str],
        list[tuple[str, ComponentRoutingProblem, str, Any]],
    ] = defaultdict(list)
    RepresentativeProblem = next(iter(ProblemsByAccess.values()), None)
    ReferenceIdentity: dict[str, object] = {}
    if RepresentativeProblem is not None:
        ReferenceIdentity = (
            _BuildPreparedComponentSymbolicNetStateContextIdentity(
                RepresentativeProblem,
                Context.Signal,
            )
        )
    if RepresentativeProblem is not None:
        if (
            _BuildPreparedComponentSymbolicNetStateContextFingerprint(
                RepresentativeProblem,
                Context.Signal,
            )
            != Context.ContextFingerprint
        ):
            raise ValueError(
                "prepared physical factor batch identity mismatch: "
                "representative context does not match prepared context"
            )

    def BuildContextFieldDiffs(
        Problem: ComponentRoutingProblem,
    ) -> tuple[str, ...]:
        ProblemIdentity = (
            _BuildPreparedComponentSymbolicNetStateContextIdentity(
                Problem,
                Context.Signal,
            )
        )
        if ProblemIdentity == ReferenceIdentity:
            return ()
        return tuple(
            Name
            for Name in sorted(ReferenceIdentity)
            if ReferenceIdentity.get(Name) != ProblemIdentity.get(Name)
        )

    def SharesPreparedContextInputs(
        Problem: ComponentRoutingProblem,
    ) -> tuple[str, ...]:
        if RepresentativeProblem is None:
            return ()
        if (
            _BuildPreparedComponentSymbolicNetStateContextFingerprint(
                Problem,
                Context.Signal,
            )
            != Context.ContextFingerprint
        ):
            return ("context-fingerprint",)
        return BuildContextFieldDiffs(Problem)

    for AccessFingerprint, Problem in sorted(ProblemsByAccess.items()):
        Mismatches = SharesPreparedContextInputs(Problem)
        if Mismatches:
            raise ValueError(
                "prepared physical factor batch identity mismatch: "
                f"semantic context mismatch ({', '.join(Mismatches)})"
            )
        Port = SelectComponentSymbolicPhysicalPort(
            Problem,
            Context.Signal,
        )
        if Port is None:
            raise ValueError(
                "prepared physical factor batch requires a fixed port"
            )
        CacheKey = BuildComponentSymbolicNetStateCacheKey(
            Problem,
            Context.Signal,
            {},
            PreparedContextFingerprint=Context.ContextFingerprint,
        )
        Cached = Cache.get(CacheKey)
        if Cached is not None:
            States, Diagnostics = Cached
            Results[str(AccessFingerprint)] = (
                PreparedComponentSymbolicNetStateCompilation(
                    CacheKey=CacheKey,
                    States=tuple(States),
                    Complete=True,
                    CacheHit=True,
                    ExpansionCount=0,
                    Diagnostics={
                        **dict(Diagnostics),
                        "SymbolicNetStateCacheHit": True,
                        "PhysicalFactorBatchCacheHit": True,
                    },
                )
            )
            continue
        Certified = frozenset(getattr(
            Port,
            "OwnedCandidateFingerprints",
            (),
        ))
        MissesByCertifiedDomain[Certified].append((
            str(AccessFingerprint),
            Problem,
            CacheKey,
            Port,
        ))

    for Certified, Members in sorted(
        MissesByCertifiedDomain.items(),
        key=lambda Value: tuple(sorted(Value[0])),
    ):
        # A certified physical access domain is a hard intersection with
        # every owned terminal domain.  Detect an empty intersection before
        # entering the tree DP: the dynamic solver would reach the same
        # complete empty frontier, but doing so once per distinct rejected
        # access certificate dominated CLA-sized unary/pair compilation.
        CandidateFilterEmpty = bool(
            Certified
            and any(
                not Certified.intersection(Eligible)
                for Eligible in (
                    Context
                    .ImmutableEligibleCandidateFingerprintsByDomain
                )
            )
        )
        if CandidateFilterEmpty:
            Diagnostics = {
                "SymbolicNetStateCacheHit": False,
                "PhysicalFactorBatchCacheHit": False,
                "PhysicalFactorBatchSize": len(Members),
                "PhysicalFactorBatchCertifiedCandidateCount": len(
                    Certified
                ),
                "PhysicalFactorBatchCandidateFilterEmpty": True,
                "PreparedSymbolicNetStateResultStatus": (
                    "architectural-unsatisfiable"
                ),
            }
            for AccessFingerprint, _Problem, CacheKey, _Port in Members:
                Cache[CacheKey] = ((), Diagnostics)
                Results[AccessFingerprint] = (
                    PreparedComponentSymbolicNetStateCompilation(
                        CacheKey=CacheKey,
                        States=(),
                        Complete=True,
                        CacheHit=False,
                        ExpansionCount=0,
                        Diagnostics=Diagnostics,
                    )
                )
            continue
        RemainingDeadline = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        RepresentativeProblem = Members[0][1]
        BatchCache: dict[str, Any] = {}
        Result = SolveComponentRoutingProblemDynamic(
            RepresentativeProblem,
            DeadlineSeconds=RemainingDeadline,
            WorkCheck=WorkCheck,
            RouteClaimsConstructionCache=(
                Context.RouteClaimsConstructionCache
            ),
            SymbolicNetStateCache=BatchCache,
            RequestedSymbolicStateSignals=frozenset((Context.Signal,)),
            PreparedSymbolicNetStateContext=Context,
            PreparedPhysicalPortVariants=tuple(
                Member[3] for Member in Members
            ),
            StopAfterOwnedSignalFrontierProof=True,
        )
        RepresentativeCacheKey = BuildComponentSymbolicNetStateCacheKey(
            RepresentativeProblem,
            Context.Signal,
            {},
            PreparedContextFingerprint=Context.ContextFingerprint,
        )
        CachedBatch = BatchCache.get(RepresentativeCacheKey)
        BatchComplete = bool(
            Result.Status != "incomplete" and CachedBatch is not None
        )
        BatchStates = (
            tuple(CachedBatch[0]) if CachedBatch is not None else ()
        )
        BatchDiagnostics = (
            dict(CachedBatch[1]) if CachedBatch is not None else {}
        )
        for AccessFingerprint, _Problem, CacheKey, Port in Members:
            if BatchComplete:
                LocalPath = tuple(Port.LocalPath)
                States = tuple(
                    State for State in BatchStates
                    if tuple(State.EgressPath) == LocalPath
                )
                Diagnostics = {
                    **BatchDiagnostics,
                    "SymbolicNetStateCacheHit": False,
                    "PhysicalFactorBatchCacheHit": False,
                    "PhysicalFactorBatchSize": len(Members),
                    "PhysicalFactorBatchCertifiedCandidateCount": len(
                        Certified
                    ),
                    "PreparedSymbolicNetStateResultStatus": Result.Status,
                }
                Cache[CacheKey] = (States, Diagnostics)
            else:
                States = None
                Diagnostics = {
                    "PhysicalFactorBatchCacheHit": False,
                    "PhysicalFactorBatchSize": len(Members),
                    "PreparedSymbolicNetStateResultStatus": Result.Status,
                }
            Results[AccessFingerprint] = (
                PreparedComponentSymbolicNetStateCompilation(
                    CacheKey=CacheKey,
                    States=States,
                    Complete=BatchComplete,
                    CacheHit=False,
                    ExpansionCount=Result.ExpansionCount,
                    Diagnostics=Diagnostics,
                )
            )
        if not BatchComplete:
            break

    for AccessFingerprint, Problem in sorted(ProblemsByAccess.items()):
        if str(AccessFingerprint) in Results:
            continue
        CacheKey = BuildComponentSymbolicNetStateCacheKey(
            Problem,
            Context.Signal,
            {},
            PreparedContextFingerprint=Context.ContextFingerprint,
        )
        Results[str(AccessFingerprint)] = (
            PreparedComponentSymbolicNetStateCompilation(
                CacheKey=CacheKey,
                States=None,
                Complete=False,
                CacheHit=False,
                ExpansionCount=0,
                Diagnostics={
                    "PhysicalFactorBatchDeferredAfterIncompleteGroup": True,
                },
            )
        )
    return Results


def SolveComponentRoutingProblem(
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ForbiddenAssignmentFingerprints: frozenset[str] = frozenset(),
    ForbiddenExportPortsBySignal: dict[
        str, tuple[Position3, ...]
    ] | None = None,
    ForbiddenForeignCandidateFingerprintsBySignal: dict[
        str, frozenset[str]
    ] | None = None,
    ForbiddenForeignAssignmentPairs: tuple[
        frozenset[tuple[str, Position3, str]], ...
    ] = (),
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    SymbolicNetStateCache: dict[str, Any] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    DiscoveryVariantLimit: int | None = 8,
    DiscoveryVariantLimitsBySignal: dict[
        str, int | None
    ] | None = None,
    RequiredForeignTransitSignals: frozenset[str] = frozenset(),
    StopAfterCompleteNetVariantPortfolioSignal: str | None = None,
    StaticPortfolioContextsBySignal: dict[
        str, CompleteComponentNetPortfolioStaticContext
    ] | None = None,
    StopAfterOwnedSignalFrontierProof: bool = False,
    StopAfterSymbolicCapacityProof: bool = False,
) -> ComponentRoutingSolveResult:
    """Dispatch physical tree fabrics to DP and retain the legacy oracle."""
    UseDynamicSolver = bool(
        StopAfterCompleteNetVariantPortfolioSignal is None
        and Problem.Interface is not None
        and (
            (
                Problem.PhysicalAssemblyPlan is not None
                and Problem.Interface.PhysicalPortReservations
            )
            or (
                StopAfterOwnedSignalFrontierProof
                and Problem.PhysicalAssemblyPlan is None
                and not Problem.Interface.PhysicalPortReservations
                and not Problem.ReservedGlobalClaimsBySignal
            )
            or (
                StopAfterSymbolicCapacityProof
                and Problem.PhysicalAssemblyPlan is None
                and Problem.Interface.PhysicalPortReservations
                and not Problem.ReservedGlobalClaimsBySignal
            )
        )
        and Problem.Fabric.TopologyKind in {
            "tree",
            "tree-forest",
            "closed-component-port-forest-v3",
            "closed-component-bridged-forest-v1",
        }
    )
    if UseDynamicSolver:
        return SolveComponentRoutingProblemDynamic(
            Problem,
            DeadlineSeconds=DeadlineSeconds,
            WorkCheck=WorkCheck,
            ForbiddenAssignmentFingerprints=(
                ForbiddenAssignmentFingerprints
            ),
            ForbiddenExportPortsBySignal=ForbiddenExportPortsBySignal,
            ForbiddenForeignCandidateFingerprintsBySignal=(
                ForbiddenForeignCandidateFingerprintsBySignal
            ),
            ForbiddenForeignAssignmentPairs=(
                ForbiddenForeignAssignmentPairs
            ),
            RequiredForeignTransitSignals=RequiredForeignTransitSignals,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
            SymbolicNetStateCache=SymbolicNetStateCache,
            StopAfterOwnedSignalFrontierProof=(
                StopAfterOwnedSignalFrontierProof
            ),
            StopAfterSymbolicCapacityProof=(
                StopAfterSymbolicCapacityProof
            ),
        )
    return _SolveComponentRoutingProblemLegacy(
        Problem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        ForbiddenAssignmentFingerprints=ForbiddenAssignmentFingerprints,
        ForbiddenExportPortsBySignal=ForbiddenExportPortsBySignal,
        ForbiddenForeignCandidateFingerprintsBySignal=(
            ForbiddenForeignCandidateFingerprintsBySignal
        ),
        ForbiddenForeignAssignmentPairs=ForbiddenForeignAssignmentPairs,
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
        DiscoveryVariantLimit=DiscoveryVariantLimit,
        DiscoveryVariantLimitsBySignal=DiscoveryVariantLimitsBySignal,
        RequiredForeignTransitSignals=RequiredForeignTransitSignals,
        StopAfterCompleteNetVariantPortfolioSignal=(
            StopAfterCompleteNetVariantPortfolioSignal
        ),
        StaticPortfolioContextsBySignal=StaticPortfolioContextsBySignal,
    )


def MaterializeRoutedComponentTemplate(
    Placed: Any,
    Template: RoutedComponentTemplate,
) -> Any:
    """Freeze component trees and every proved continuation escape corridor."""
    ExistingClaims = tuple(getattr(Placed, "LocalRouteClaims", ()) or ())
    ComponentSignals = frozenset(Net.Signal for Net in Template.Nets)
    RetainedClaims = tuple(
        Claim for Claim in ExistingClaims if Claim.Signal not in ComponentSignals
    )
    ComponentClaims = tuple(
        LocalRouteClaim(
            Signal=Net.Signal,
            ClusterId=-1,
            Root=Net.Root,
            ConnectedTargets=Net.CoveredTerminals,
            BoundaryNodes=Net.ExportedPorts or tuple(Net.Nodes),
            Nodes=Net.Nodes,
            Edges=Net.Edges,
            Claims=Net.Claims,
            ExactRouteSignalBlocks=len(Net.WireCells),
            ExactRouteRefreshBlocks=len(Net.Repeaters),
            ExactRouteSupportBlocks=len(Net.SupportCells),
        )
        for Net in Template.Nets
    )
    ForeignEscapeClaims = tuple(
        LocalRouteClaim(
            Signal=Signal,
            ClusterId=-2,
            Root=Terminal,
            # A passive target witness already connects its gate terminal to
            # the exported boundary node.  Source terminals are harmless in
            # this set because they are not present in a profile's targets.
            ConnectedTargets=(Terminal,),
            BoundaryNodes=(Candidate.Path[-1],),
            Nodes=frozenset(Candidate.Path),
            Edges=frozenset(
                _NormalizedEdge(First, Second)
                for First, Second in zip(
                    Candidate.Path,
                    Candidate.Path[1:],
                )
            ),
            Claims=Candidate.Claims,
            ExactRouteSignalBlocks=len(Candidate.Claims.WireCells),
            ExactRouteSupportBlocks=len(Candidate.Claims.SupportCells),
        )
        for Signal, Terminal, Candidate in (
            Template.ForeignEscapeReservations
        )
        if Candidate.Path
    )
    ExternalContinuationClaims = tuple(
        LocalRouteClaim(
            Signal=Signal,
            ClusterId=-3,
            Root=Terminal,
            ConnectedTargets=(Terminal,),
            BoundaryNodes=(Candidate.Path[-1],),
            Nodes=frozenset(Candidate.Path),
            Edges=frozenset(
                _NormalizedEdge(First, Second)
                for First, Second in zip(
                    Candidate.Path,
                    Candidate.Path[1:],
                )
            ),
            Claims=Candidate.Claims,
            ExactRouteSignalBlocks=len(Candidate.Claims.WireCells),
            ExactRouteSupportBlocks=len(Candidate.Claims.SupportCells),
        )
        for Signal, Terminal, Candidate in (
            Template.ExternalContinuationReservations
        )
        if Candidate.Path
    )
    ForeignTransitClaims = tuple(
        LocalRouteClaim(
            Signal=Net.Signal,
            ClusterId=-4,
            Root=Net.Root,
            ConnectedTargets=tuple(
                Position
                for Position in Net.CoveredTerminals
                if Position != Net.Root
            ),
            BoundaryNodes=tuple(Net.CoveredTerminals),
            Nodes=Net.Nodes,
            Edges=Net.Edges,
            Claims=Net.Claims,
            RepeaterReservations=tuple(
                RoutingReservation(
                    Signal=Net.Signal,
                    Resource=RoutingResourceId(
                        RoutingResourceKind.Wire,
                        Position,
                    ),
                    Position=Position,
                    Purpose="Repeater",
                    Facing=Facing,
                )
                for Position, Facing in Net.Repeaters
            ),
            ExactRouteSignalBlocks=len(Net.WireCells),
            ExactRouteRefreshBlocks=len(Net.Repeaters),
            ExactRouteSupportBlocks=len(Net.SupportCells),
        )
        for Net in Template.ForeignTransitReservations
    )
    Diagnostics = dict(getattr(Placed, "LocalRouteDiagnostics", {}) or {})
    Diagnostics["__RoutedComponentTemplate__"] = Template.ToDictionary()
    Diagnostics["__RoutedComponentGlobalHandoff__"] = {
        "RetiredClusterBoundaryLeaseRequestCount": len(
            getattr(Placed, "ClusterBoundaryLeaseRequests", ()) or ()
        ),
        "GlobalAccessPolicy": (
            "authoritative-route-assignment-with-frozen-component-obstacles"
        ),
        "FrozenForeignEscapeClaimCount": len(ForeignEscapeClaims),
        "FrozenForeignEscapeSignals": sorted({
            Claim.Signal for Claim in ForeignEscapeClaims
        }),
        "FrozenExternalContinuationClaimCount": len(
            ExternalContinuationClaims
        ),
        "FrozenForeignTransitClaimCount": len(
            ForeignTransitClaims
        ),
        "FrozenForeignTransitSignals": sorted({
            Claim.Signal for Claim in ForeignTransitClaims
        }),
        "FabricFingerprint": Template.FabricFingerprint,
        "ArchivedChannelFingerprint": str(getattr(
            getattr(Placed, "InterClusterRoutingChannel", None),
            "ChannelFingerprint",
            "",
        )),
        "InterfaceFingerprint": Template.InterfaceFingerprint,
        "ImplicitForeignTransitDomainCount": int(
            Template.Diagnostics.get(
                "ImplicitForeignTransitDomainCount",
                0,
            )
        ),
    }
    ActiveChannel = getattr(
        Placed,
        "InterClusterRoutingChannel",
        None,
    )
    return replace(
        Placed,
        LocalRouteClaims=(
            *RetainedClaims,
            *ComponentClaims,
            *ExternalContinuationClaims,
            *ForeignEscapeClaims,
            *ForeignTransitClaims,
        ),
        LocalRouteDiagnostics=Diagnostics,
        RoutedComponentTemplates=(
            *(getattr(Placed, "RoutedComponentTemplates", ()) or ()),
            Template,
        ),
        RoutedComponentRoutingChannels=(
            *(
                getattr(
                    Placed,
                    "RoutedComponentRoutingChannels",
                    (),
                )
                or ()
            ),
            *((ActiveChannel,) if ActiveChannel is not None else ()),
        ),
        # The complete component template replaces the dense boundary-lease
        # pre-solver.  Remaining global nets are assigned by the ordinary
        # authoritative router against immutable component claims, with the
        # proved passive witnesses retained in their portal domains.
        ClusterBoundaryLeaseRequests=(),
        CompleteClusterInterfaceAccess=False,
        InterClusterRoutingChannel=None,
    )


def ValidateRoutedComponentHandoff(
    Placed: Any,
    Template: RoutedComponentTemplate,
    *,
    PlacementFingerprint: str,
    LocalTemplateFingerprint: str,
) -> dict[str, object]:
    """Validate every immutable identity before ordinary global routing."""
    Channel = getattr(Placed, "InterClusterRoutingChannel", None)
    if Channel is None:
        ArchivedChannels = tuple(
            getattr(
                Placed,
                "RoutedComponentRoutingChannels",
                (),
            )
            or ()
        )
        Channel = ArchivedChannels[-1] if ArchivedChannels else None
    ChannelFingerprint = str(
        getattr(Channel, "ChannelFingerprint", "")
    )
    if Template.PlacementFingerprint != PlacementFingerprint:
        raise ValueError("routed component placement fingerprint mismatch")
    if Template.LocalTemplateFingerprint != LocalTemplateFingerprint:
        raise ValueError("routed component local-template fingerprint mismatch")
    if Template.FabricFingerprint == "" or ChannelFingerprint == "":
        raise ValueError("routed component fabric identity is missing")
    InterfaceDiagnostic = (
        (getattr(Placed, "LocalRouteDiagnostics", {}) or {})
        .get("__RoutedComponentGlobalHandoff__", {})
    )
    if (
        InterfaceDiagnostic.get("FabricFingerprint")
        != Template.FabricFingerprint
    ):
        raise ValueError("routed component fabric fingerprint mismatch")
    if (
        InterfaceDiagnostic.get("ArchivedChannelFingerprint")
        != ChannelFingerprint
    ):
        raise ValueError(
            "routed component archived-channel fingerprint mismatch"
        )
    ArchivedFabricFingerprint = (
        BuildComponentRoutingFabric(Channel).FabricFingerprint
    )
    if (
        Template.InterfaceFingerprint
        and InterfaceDiagnostic.get("InterfaceFingerprint")
        != Template.InterfaceFingerprint
    ):
        raise ValueError("routed component interface fingerprint mismatch")
    if int(InterfaceDiagnostic.get(
        "ImplicitForeignTransitDomainCount",
        0,
    )) != 0:
        raise ValueError(
            "routed component handoff contains implicit foreign transit"
        )
    PlacedTemplates = tuple(
        getattr(Placed, "RoutedComponentTemplates", ()) or ()
    )
    if not any(
        Value.RoutedTemplateFingerprint
        == Template.RoutedTemplateFingerprint
        and Value.ExportedPortFingerprint
        == Template.ExportedPortFingerprint
        and Value.ClaimsFingerprint == Template.ClaimsFingerprint
        for Value in PlacedTemplates
    ):
        raise ValueError("routed component template identity was not frozen")
    LocalClaims = tuple(
        getattr(Placed, "LocalRouteClaims", ()) or ()
    )
    for Net in Template.Nets:
        if not any(
            Claim.Signal == Net.Signal
            and Claim.Root == Net.Root
            and Claim.Nodes == Net.Nodes
            and Claim.Claims == Net.Claims
            and tuple(Claim.BoundaryNodes)
            == (Net.ExportedPorts or tuple(Net.Nodes))
            for Claim in LocalClaims
        ):
            raise ValueError(
                "routed component net claim was not frozen exactly"
            )
    for Net in Template.ForeignTransitReservations:
        if not any(
            int(getattr(Claim, "ClusterId", 0)) == -4
            and Claim.Signal == Net.Signal
            and Claim.Root == Net.Root
            and Claim.Nodes == Net.Nodes
            and Claim.Claims == Net.Claims
            and tuple(Claim.BoundaryNodes)
            == tuple(Net.CoveredTerminals)
            for Claim in LocalClaims
        ):
            raise ValueError(
                "routed component foreign transit was not frozen exactly"
            )
    return {
        "PlacementFingerprint": PlacementFingerprint,
        "LocalTemplateFingerprint": LocalTemplateFingerprint,
        "FabricFingerprint": Template.FabricFingerprint,
        "ArchivedChannelFingerprint": ChannelFingerprint,
        "ArchivedFabricFingerprint": ArchivedFabricFingerprint,
        "FabricAugmentedForExactAccess": (
            ArchivedFabricFingerprint
            != Template.FabricFingerprint
        ),
        "RoutedTemplateFingerprint": (
            Template.RoutedTemplateFingerprint
        ),
        "ExportedPortFingerprint": (
            Template.ExportedPortFingerprint
        ),
        "ClaimsFingerprint": Template.ClaimsFingerprint,
        "InterfaceFingerprint": Template.InterfaceFingerprint,
        "Valid": True,
    }


def PreserveRoutedComponentForeignEscapes(
    Placed: Any,
    RawPortals: dict[
        tuple[str, Position3, int],
        tuple[PinAccessPortal, ...],
    ],
) -> tuple[
    dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    dict[str, object],
]:
    """Preserve witnesses unless their corridor is already a frozen claim."""
    Result = dict(RawPortals)
    PreservedCount = 0
    FrozenClaimCount = 0
    ExportedSourcePortCount = 0
    RequiredCount = 0
    ContinuationRequiredCount = 0
    ContinuationPreservedCount = 0
    ContinuationMissingCount = 0
    FrozenWitnesses = frozenset(
        (Claim.Signal, Claim.Root)
        for Claim in (
            getattr(Placed, "LocalRouteClaims", ()) or ()
        )
        if int(getattr(Claim, "ClusterId", -1)) == -2
    )
    ProducerTerminals = frozenset(
        tuple(Gate.OutputPin)
        for Gate in getattr(Placed, "PlacedGates", ())
        if getattr(Gate, "OutputPin", None) is not None
    )
    for Template in (
        getattr(Placed, "RoutedComponentTemplates", ()) or ()
    ):
        for Signal, Terminal, Candidate in getattr(
            Template,
            "ExternalContinuationReservations",
            (),
        ):
            ContinuationRequiredCount += 1
            MatchingKeys = tuple(
                Key
                for Key in Result
                if Key[0] == Signal and Key[1] == Terminal
            )
            SelectedValues = tuple(
                Portal
                for Key in MatchingKeys
                for Portal in Result.get(Key, ())
                if (
                    tuple(Portal.Path) == Candidate.Path
                    and Portal.Claims == Candidate.Claims
                )
            )
            for Key in MatchingKeys:
                Result.pop(Key, None)
            if not SelectedValues:
                ContinuationMissingCount += 1
                continue
            for Portal in SelectedValues:
                Key = (Signal, Terminal, int(Portal.Layer))
                Result[Key] = (
                    *Result.get(Key, ()),
                    Portal,
                )
            ContinuationPreservedCount += 1
        for Signal, Terminal, Candidate in (
            Template.ForeignEscapeReservations
        ):
            RequiredCount += 1
            if (Signal, Terminal) in FrozenWitnesses:
                # The terminal has been replaced by a same-net continuation
                # claim.  Keeping its pre-materialization portal would make
                # the global matcher look up a target access path that no
                # longer exists in the transformed profile.
                for Key in tuple(Result):
                    if Key[0] == Signal and Key[1] == Terminal:
                        Result.pop(Key)
                if Terminal in ProducerTerminals:
                    Port = Candidate.Path[-1]
                    PortClaims = RoutingResourceClaims(
                        WireCells=frozenset((Port,)),
                        SupportCells=frozenset((
                            (Port[0], Port[1] - 1, Port[2]),
                        )),
                        ElectricalCells=frozenset(
                            DefaultRedstoneRoutingTechnology
                            .BuildElectricalExclusions({Port})
                        ),
                    )
                    Portal = PinAccessPortal(
                        PortalId=(
                            "routed-component-foreign-source-"
                            f"{Candidate.CandidateFingerprint}"
                        ),
                        Signal=Signal,
                        Terminal=Port,
                        Layer=int(Candidate.Layer),
                        Path=(Port,),
                        Edges=frozenset(),
                        Claims=PortClaims,
                        Length=1,
                        BendCount=0,
                        ViaCount=0,
                        Cost=0,
                    )
                    Result[
                        (Signal, Port, int(Candidate.Layer))
                    ] = (Portal,)
                    ExportedSourcePortCount += 1
                FrozenClaimCount += 1
                continue
            MatchingKeys = tuple(
                Key
                for Key in Result
                if Key[0] == Signal and Key[1] == Terminal
            )
            Matched = False
            Witness = None
            WitnessKey = None
            for Key in MatchingKeys:
                Values = Result[Key]
                Witnesses = tuple(
                    Value
                    for Value in Values
                    if (
                        tuple(Value.Path) == Candidate.Path
                        and Value.Claims == Candidate.Claims
                    )
                )
                if not Witnesses:
                    continue
                Witness = min(
                    Witnesses,
                    key=lambda Value: (
                        Value.Cost,
                        Value.PortalId,
                    ),
                )
                Matched = True
                WitnessKey = Key
                break
            if not Matched:
                PathEdges = frozenset(
                    _NormalizedEdge(First, Second)
                    for First, Second in zip(
                        Candidate.Path,
                        Candidate.Path[1:],
                    )
                )
                BendCount = sum(
                    (
                        First[0] - Previous[0],
                        First[1] - Previous[1],
                        First[2] - Previous[2],
                    )
                    != (
                        Second[0] - First[0],
                        Second[1] - First[1],
                        Second[2] - First[2],
                    )
                    for Previous, First, Second
                    in zip(
                        Candidate.Path,
                        Candidate.Path[1:],
                        Candidate.Path[2:],
                    )
                )
                ViaCount = sum(
                    First[1] != Second[1]
                    for First, Second in zip(
                        Candidate.Path,
                        Candidate.Path[1:],
                    )
                )
                Witness = PinAccessPortal(
                    PortalId=(
                        "routed-component-foreign-"
                        f"{Candidate.CandidateFingerprint}"
                    ),
                    Signal=Signal,
                    Terminal=Terminal,
                    Layer=int(Candidate.Layer),
                    Path=Candidate.Path,
                    Edges=PathEdges,
                    Claims=Candidate.Claims,
                    Length=len(Candidate.Path),
                    BendCount=BendCount,
                    ViaCount=ViaCount,
                    Cost=int(Candidate.Cost),
                )
            assert Witness is not None
            Key = WitnessKey or (
                Signal, Terminal, int(Candidate.Layer)
            )
            Result[Key] = (
                Witness,
                *(
                    Value
                    for Value in Result.get(Key, ())
                    if Value is not Witness
                ),
            )
            PreservedCount += 1
    return Result, {
        "RequiredWitnessCount": RequiredCount,
        "PreservedWitnessCount": PreservedCount,
        "ConsumedByFrozenClaimCount": FrozenClaimCount,
        "ExportedSourcePortCount": ExportedSourcePortCount,
        "ContinuationRequiredCount": ContinuationRequiredCount,
        "ContinuationPreservedCount": ContinuationPreservedCount,
        "ContinuationMissingCount": ContinuationMissingCount,
        "Complete": (
            PreservedCount + FrozenClaimCount == RequiredCount
            and ContinuationMissingCount == 0
        ),
    }

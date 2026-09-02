"""Prepared symbolic component net-state contracts and cache identities."""

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
from ..Contracts.Core import Position3
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
from ..Technology import DefaultRedstoneRoutingTechnology

try:
    from RedstoneCompiler.RustRouting import (
        BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
    )
    from RedstoneCompiler.RustRouting import BuildRouteClaimsBatch as _BuildRouteClaimsBatch
    from RedstoneCompiler.RustRouting import (
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
    )
    from RedstoneCompiler.RustRouting import GetRoutingThreadCount as _GetRoutingThreadCount
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

from .Core import _ClaimsFingerprint, _NormalizedEdge, _RelativeGeometry, _StableFingerprint
from .Fabric import BuildComponentEgressPaths, _BuildAdjacency, _PlanTreeRepeaters, _UniqueFabricSubtree
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
    RepeaterInputFacings: tuple[tuple[Position3, str], ...]
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

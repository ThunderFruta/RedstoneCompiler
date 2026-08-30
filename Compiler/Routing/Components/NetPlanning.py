"""Exact component net tree and repeater variant construction."""

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

from .Core import _ClaimsFingerprint, _NormalizedEdge, _StableFingerprint
from .Fabric import _BuildAdjacency, _PlanTreeRepeaters, _UniqueFabricSubtree
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
        RepeaterInputFacings=Repeaters,
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

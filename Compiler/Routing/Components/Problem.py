"""Closed component-routing problem and physical-interface construction."""

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

from .Fabric import (
    AugmentComponentRoutingFabric,
    BridgeDisconnectedOwnedSignalFabric,
    BuildClosedComponentInterface,
    BuildCoalescedComponentAccessCandidates,
    BuildComponentRoutingFabric,
    CoalesceOwnedSignalAccessDomains,
    PruneDominatedComponentAccessCandidates,
    SelectClosedComponentOwnedTerminalPairs,
    SelectComponentIncidentSignals,
    _BuildAccessCandidate,
)
from .Core import _RelativeGeometry, _StableFingerprint
from .Feedthroughs import BuildDeclaredComponentFeedthroughDomains
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
    ProtectedAccessNodes = frozenset(
        Position
        for (
            Signal,
            Terminal,
            _Layer,
        ), Portals in RawPortals.items()
        if (Signal, Terminal) in ComponentPairs
        for Portal in Portals
        for Position in Portal.Path
    )
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
        ProtectedAccessNodes=ProtectedAccessNodes,
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
        ProtectedAccessNodes=ProtectedAccessNodes,
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
                        in Candidate.RepeaterInputFacings
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

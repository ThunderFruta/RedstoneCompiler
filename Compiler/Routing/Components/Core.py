"""Stable component-routing geometry, fingerprints, and portfolio contexts."""

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
            for Position, Facing in Value.RepeaterInputFacings
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
            RepeaterInputFacings=Repeaters,
            Claims=Claims,
            CoveredTerminals=CoveredTerminals,
            ExportedPorts=ExportedPorts,
            NetFingerprint=NetFingerprint,
        ))
    return tuple(Result)

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

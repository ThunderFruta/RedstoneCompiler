"""Exact local-factor projections, certificates, and directional portfolios."""

from __future__ import annotations

from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import product
from math import prod
import multiprocessing
import os
from time import monotonic
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping
from ....Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from ....Contracts.Component import ComponentRoutingProblem, ComponentRoutingSolveResult, PhysicalComponentAssemblyPlan, PhysicalComponentChannelReservation, PhysicalComponentPortReservation, PhysicalComponentSelectedLocalPortSupport, RoutedComponentNet, RoutedComponentTemplate
from ....Contracts.Core import Position3
from ....Contracts.PhysicalInterface import PhysicalComponentLocalFactorProjection, PhysicalComponentLocalFactorProjectionComparison, PhysicalComponentLocalFactorUnsatCertificate, PhysicalLocalPortPairProofRecord, PhysicalLocalPortPairSupportCertificate, PhysicalComponentSymbolicHigherOrderCertificate, PhysicalComponentSymbolicPortPairCertificate, PhysicalPortCorridorDomain, PhysicalPortCorridorFactor, PreparedPhysicalComponentAssembly, PreparedPhysicalComponentPortFactorDomain
from ....Constraints import BoundaryRelations
from ....Constraints.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint, ProjectPhysicalComponentSignalGlobalProfile
from ....Constraints.PhysicalClaims import ComponentClaimsConflict
from ....Resources.ResourceGraph import RoutingResourceClaims
from ....Runtime.Reliability import BuildStableFingerprint
from ..Planning.InterfacePlanning import BuildComponentCapacityGuide, ComponentCapacityGuide, ComponentCapacityGuideOption, ComponentInterfaceContract, ComponentPlanningResult, ComponentPlanningStatus, IterClosedComponentContracts, PlanClosedComponent, SolveComponentInterfaceCsp

from ..Core import BuildCompleteComponentNetPortfolioStaticContext
from ..Symbolic.SymbolicState import _BuildPreparedComponentSymbolicNetStateContextFingerprint, BuildComponentSymbolicNetStateCacheKey, PrepareComponentSymbolicNetStateContext
from ..Symbolic.SymbolicWorkers import CompilePreparedComponentPhysicalFactorStateBatch, CompilePreparedComponentSymbolicNetStates
from ..Planning.Portfolios import BuildCompleteOpposingNetAccessContractDomain, BuildCompleteOpposingNetAccessRowContext, CompileCompleteComponentNetVariantPortfolio, CompileCompleteComponentNetVariantPortfolios, EvaluateCompleteOpposingNetAccessContractRow
from ..Solving.Solver import MaterializeRoutedComponentTemplate, SolveComponentRoutingProblem, ValidateRoutedComponentHandoff

from ..Planning.PhysicalPlanning import BuildPhysicalComponentPortSolverCacheKey, MaterializePreparedPhysicalPortOptionDomains
from .Validation import (
    BuildPhysicalLocalAccessDomainFingerprint,
    BuildPhysicalPortLocalContractFingerprint,
    BuildPhysicalPortSeamContractFingerprint,
    _Fingerprint,
    _Normalize,
    _NormalizedClaimsIdentity,
    _Origin,
    _SignalStructuralIdentities,
)
def BuildGlobalRelaxedLocalProofDomainFingerprint(
    Problem: ComponentRoutingProblem,
) -> str:
    """Identify the bound local domain after removing global corridors."""
    Plan = Problem.PhysicalAssemblyPlan
    if Plan is None:
        raise ValueError("global-relaxed proof requires a physical plan")
    Origin = _Origin(Problem)
    SignalIdentities = dict(_SignalStructuralIdentities(Problem))

    def SignalIdentity(Signal: str) -> str:
        return SignalIdentities.get(Signal, "foreign:" + str(Signal))

    def TerminalDomainIdentity(Domain: Any) -> tuple[object, ...]:
        return (
            SignalIdentity(str(Domain.Signal)),
            Domain.TerminalRole,
            _Normalize(Domain.Terminal, Origin),
            Domain.TerminalFingerprint,
            bool(getattr(Domain, "Complete", True)),
            tuple(sorted(
                (
                    Candidate.CandidateFingerprint,
                    _Normalize(Candidate.Attachment, Origin),
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Candidate.Path
                    ),
                    _NormalizedClaimsIdentity(Candidate.Claims, Origin),
                    Candidate.Layer,
                    Candidate.Cost,
                )
                for Candidate in Domain.Candidates
            )),
        )

    def TransitDomainIdentity(Domain: Any) -> tuple[object, ...]:
        return (
            SignalIdentity(str(Domain.Signal)),
            Domain.PartitionAxis,
            Domain.PartitionFingerprint,
            Domain.Complete,
            tuple(sorted(
                (
                    Candidate.NetFingerprint,
                    tuple(sorted(
                        _Normalize(Value, Origin)
                        for Value in Candidate.Nodes
                    )),
                    tuple(sorted(
                        tuple(sorted((
                            _Normalize(First, Origin),
                            _Normalize(Second, Origin),
                        )))
                        for First, Second in Candidate.Edges
                    )),
                    _NormalizedClaimsIdentity(Candidate.Claims, Origin),
                    tuple(
                        (_Normalize(Position, Origin), Facing)
                        for Position, Facing in Candidate.RepeaterInputFacings
                    ),
                    tuple(sorted(
                        _Normalize(Value, Origin)
                        for Value in Candidate.ExportedPorts
                    )),
                )
                for Candidate in Domain.Candidates
            )),
        )

    ResourceGraph = Problem.ResourceGraph
    Technology = getattr(ResourceGraph, "Technology", None)
    return _Fingerprint((
        "global-relaxed-local-proof-domain-v2",
        Problem.Fabric.FabricFingerprint,
        BuildPhysicalLocalAccessDomainFingerprint(Problem),
        tuple(sorted(SignalIdentities.items())),
        tuple(sorted(
            (
                "component"
                if Claim.Signal in Problem.ComponentSignals
                else "foreign",
                _NormalizedClaimsIdentity(Claim.Claims, Origin),
            )
            for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims)
        )),
        tuple(sorted(
            (
                Port.Signal,
                Port.Direction,
                tuple(
                    _Normalize(Value, Origin)
                    for Value in Port.OwnedTerminals
                ),
                tuple(Port.OwnedTerminalFingerprints),
                tuple(Port.OwnedCandidateFingerprints),
                _Normalize(Port.FabricAttachment, Origin),
                _Normalize(Port.Attachment, Origin),
                tuple(_Normalize(Value, Origin) for Value in Port.LocalPath),
                Port.Capacity,
            )
            for Port in Plan.Ports
        )),
        tuple(sorted(
            (
                Feedthrough.Signal,
                tuple(sorted(
                    (
                        _Normalize(Entry, Origin),
                        _Normalize(Exit, Origin),
                    )
                    for Entry, Exit in Feedthrough.EndpointPairs
                )),
                Feedthrough.Capacity,
                tuple(
                    _Normalize(Value, Origin)
                    for Value in Feedthrough.ReservedPathNodes
                ),
                (
                    _NormalizedClaimsIdentity(Feedthrough.Claims, Origin)
                    if Feedthrough.Claims is not None
                    else ()
                ),
                Feedthrough.ReservationFingerprint,
            )
            for Feedthrough in Plan.Feedthroughs
        )),
        tuple(sorted(
            TerminalDomainIdentity(Domain)
            for Domain in Problem.ExternalContinuationDomains
        )),
        tuple(sorted(
            TerminalDomainIdentity(Domain)
            for Domain in Problem.ForeignEscapeDomains
        )),
        tuple(sorted(
            TransitDomainIdentity(Domain)
            for Domain in Problem.ForeignTransitDomains
        )),
        tuple(sorted(
            (
                SignalIdentity(str(Signal)),
                _Normalize(Terminal, Origin),
                Role,
            )
            for Signal, Terminal, Role
            in Problem.ExternalContinuationTerminals
        )),
        Problem.MaximumPowerDistance,
        getattr(ResourceGraph, "GraphVersion", None),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in getattr(ResourceGraph, "ActualBlocks", ())
        )),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in getattr(ResourceGraph, "ElectricalBlocks", ())
        )),
        tuple(sorted(
            _Normalize(Value, Origin)
            for Value in getattr(ResourceGraph, "SolidBlocks", ())
        )),
        type(Technology).__qualname__,
        getattr(Technology, "TechnologyVersion", None),
        repr(Technology),
    ))


def BuildPhysicalLocalPairProofContextFingerprint(
    Problem: ComponentRoutingProblem,
    Preparation: PreparedPhysicalComponentPortFactorDomain,
) -> str:
    """Bind pair-support proofs to the complete global-independent context.

    Exact global channels and selected ports are intentionally removed.  All
    component-local geometry, immutable obstacles, declared feedthroughs, and
    finite terminal domains remain in the relaxed-domain identity.  Comparing
    the bound problem with the preparation problem prevents a post-planning
    mutation from being laundered into a reusable pre-assignment certificate.
    """
    if (
        Preparation is None
        or not Preparation.Complete
        or not Preparation.Feasible
        or not Preparation.DomainFingerprint
    ):
        raise ValueError(
            "local pair proof context requires a complete feasible preparation"
        )

    def CanonicalFingerprint(Value: ComponentRoutingProblem) -> str:
        Interface = Value.Interface
        if (
            Interface is None
            or not bool(getattr(Interface, "Complete", True))
            or not Value.DomainComplete
            or not bool(getattr(Value.Fabric, "Complete", True))
            or any(
                not bool(getattr(Domain, "Complete", True))
                for Domain in Value.OwnedTerminalDomains
            )
        ):
            raise ValueError("local pair proof input domain is incomplete")
        Feedthroughs = tuple(getattr(Interface, "Feedthroughs", ()))
        DeclaredFeedthroughSignals = frozenset(
            Value.Signal for Value in Feedthroughs
        )
        ForeignTransitSignals = frozenset(
            Domain.Signal for Domain in Value.ForeignTransitDomains
        )
        if not ForeignTransitSignals.issubset(DeclaredFeedthroughSignals):
            raise ValueError(
                "local pair proof contains undeclared foreign transit"
            )
        CanonicalInterface = replace(
            Interface,
            PhysicalPortReservations=(),
        )
        CanonicalProblem = replace(
            Value,
            Interface=CanonicalInterface,
            PhysicalAssemblyPlan=SimpleNamespace(
                Ports=(),
                Feedthroughs=Feedthroughs,
            ),
            ReservedGlobalClaimsBySignal=(),
        )
        return BuildGlobalRelaxedLocalProofDomainFingerprint(
            CanonicalProblem
        )

    PreparedContext = CanonicalFingerprint(Preparation.Problem)
    CurrentContext = CanonicalFingerprint(Problem)
    if PreparedContext != CurrentContext:
        raise ValueError(
            "local pair proof context differs from its prepared domain"
        )
    AccessCertificate = Preparation.AccessCertificate
    if (
        not bool(getattr(AccessCertificate, "Complete", False))
        or not bool(getattr(AccessCertificate, "Feasible", False))
        or not getattr(AccessCertificate, "CertificateFingerprint", "")
    ):
        raise ValueError(
            "local pair proof requires a complete feasible access certificate"
        )
    return _Fingerprint((
        "physical-local-pair-proof-context-v1",
        Preparation.DomainFingerprint,
        Preparation.PlacementFingerprint,
        Preparation.ComponentGraphFingerprint,
        Preparation.ResourceGraphFingerprint,
        Preparation.AccessCertificateFingerprint,
        PreparedContext,
    ))


def _BuildPhysicalComponentLocalFactorProjectionParts(
    Problem: ComponentRoutingProblem,
    LocalFactorsBySignal: Any,
) -> tuple[
    str,
    str,
    tuple[tuple[str, tuple[str, ...]], ...],
    dict[str, tuple[str, tuple[str, ...]]],
]:
    """Build name-free normalized identities shared by proofs and candidates."""
    Origin = _Origin(Problem)

    def ClaimsIdentity(Claims: Any) -> tuple[tuple[Position3, ...], ...]:
        if Claims is None:
            return ((), (), (), ())
        return tuple(
            tuple(sorted(
                _Normalize(Value, Origin)
                for Value in getattr(Claims, Attribute, ())
            ))
            for Attribute in (
                "WireCells",
                "SupportCells",
                "RequiredAirCells",
                "ElectricalCells",
            )
        )

    FactorsBySignal = {
        str(Signal): tuple(Factors)
        for Signal, Factors in LocalFactorsBySignal
    }
    Interface = Problem.Interface
    LogicalPortsBySignal = {
        str(Port.Signal): Port
        for Port in getattr(Interface, "Ports", ())
    }
    PhysicalPortsBySignal = {
        str(Port.Signal): Port
        for Port in getattr(
            Interface,
            "PhysicalPortReservations",
            (),
        )
    }
    Signals = tuple(sorted(FactorsBySignal))
    SignalRecordsByName: dict[
        str, tuple[str, tuple[str, ...]]
    ] = {}
    for Signal in Signals:
        LogicalPort = LogicalPortsBySignal.get(Signal)
        PhysicalPort = PhysicalPortsBySignal.get(Signal)
        OwnedDomainIdentity = tuple(sorted(
            (
                str(Domain.TerminalRole),
                _Normalize(Domain.Terminal, Origin),
                bool(getattr(Domain, "Complete", True)),
                tuple(sorted(
                    (
                        _Normalize(Candidate.Attachment, Origin),
                        tuple(
                            _Normalize(Value, Origin)
                            for Value in Candidate.Path
                        ),
                        ClaimsIdentity(Candidate.Claims),
                        int(Candidate.Layer),
                    )
                    for Candidate in Domain.Candidates
                )),
            )
            for Domain in Problem.OwnedTerminalDomains
            if str(Domain.Signal) == Signal
        ))
        SignalIdentity = _Fingerprint((
            "physical-component-local-factor-signal-v1",
            OwnedDomainIdentity,
            (
                str(LogicalPort.Direction),
                tuple(sorted(
                    _Normalize(Value, Origin)
                    for Value in LogicalPort.OwnedTerminals
                )),
                int(LogicalPort.ExternalTerminalCount),
                int(LogicalPort.Capacity),
            ) if LogicalPort is not None else None,
            (
                str(PhysicalPort.Direction),
                tuple(sorted(
                    _Normalize(Value, Origin)
                    for Value in PhysicalPort.OwnedTerminals
                )),
                _Normalize(PhysicalPort.FabricAttachment, Origin),
                int(PhysicalPort.Capacity),
            ) if PhysicalPort is not None else None,
        ))
        FactorIdentities = tuple(sorted(
            _Fingerprint((
                "physical-component-local-access-factor-v1",
                str(Factor.Direction),
                int(Factor.Capacity),
                tuple(sorted(
                    _Normalize(Value, Origin)
                    for Value in Factor.OwnedTerminals
                )),
                _Normalize(Factor.FabricAttachment, Origin),
                tuple(
                    _Normalize(Value, Origin)
                    for Value in Factor.LocalPath
                ),
                ClaimsIdentity(Factor.LocalClaims),
                tuple(sorted(
                    (
                        _Normalize(Candidate.Attachment, Origin),
                        tuple(
                            _Normalize(Value, Origin)
                            for Value in Candidate.Path
                        ),
                        ClaimsIdentity(Candidate.Claims),
                        int(Candidate.Layer),
                    )
                    for Candidate in Factor.OwnedAccessCandidates
                )),
            ))
            for Factor in FactorsBySignal[Signal]
        ))
        SignalRecordsByName[Signal] = (
            SignalIdentity,
            FactorIdentities,
        )

    SignalFactorIdentities = tuple(sorted(
        SignalRecordsByName.values()
    ))
    Fabric = Problem.Fabric
    ComponentTopologyFingerprint = _Fingerprint((
        "physical-component-local-topology-v1",
        tuple(sorted(
            _Normalize(Value, Origin) for Value in Fabric.Nodes
        )),
        tuple(sorted(
            tuple(sorted((
                _Normalize(First, Origin),
                _Normalize(Second, Origin),
            )))
            for First, Second in Fabric.Edges
        )),
        tuple(sorted(
            _Normalize(Value, Origin) for Value in Fabric.IngressNodes
        )),
        str(Fabric.TopologyKind),
        int(Problem.MaximumPowerDistance),
        tuple(sorted(
            len(Record[1])
            for Record in SignalFactorIdentities
        )),
    ))
    FeedthroughIdentity = tuple(sorted(
        (
            tuple(sorted(
                (
                    _Normalize(Entry, Origin),
                    _Normalize(Exit, Origin),
                )
                for Entry, Exit in Feedthrough.EndpointPairs
            )),
            int(Feedthrough.Capacity),
            tuple(
                _Normalize(Value, Origin)
                for Value in Feedthrough.ReservedPathNodes
            ),
            ClaimsIdentity(Feedthrough.Claims),
        )
        for Feedthrough in getattr(Interface, "Feedthroughs", ())
    ))
    InterfaceContractFingerprint = _Fingerprint((
        "physical-component-local-interface-v1",
        bool(getattr(Interface, "Complete", False)),
        tuple(sorted(
            SignalIdentity
            for SignalIdentity, _ in SignalFactorIdentities
        )),
        FeedthroughIdentity,
    ))
    return (
        ComponentTopologyFingerprint,
        InterfaceContractFingerprint,
        SignalFactorIdentities,
        SignalRecordsByName,
    )


def BuildPhysicalComponentLocalFactorProjection(
    Problem: ComponentRoutingProblem,
    LocalFactorsBySignal: Any,
    *,
    ResourceGraphFingerprint: str = "",
    TechnologyFingerprint: str = "",
    Complete: bool = True,
) -> PhysicalComponentLocalFactorProjection:
    """Freeze the cheap local part of a retained placement before apertures."""
    (
        ComponentTopologyFingerprint,
        InterfaceContractFingerprint,
        SignalFactorIdentities,
        _,
    ) = _BuildPhysicalComponentLocalFactorProjectionParts(
        Problem,
        LocalFactorsBySignal,
    )
    Plan = Problem.PhysicalAssemblyPlan
    ResourceIdentity = str(
        ResourceGraphFingerprint
        or getattr(Plan, "ResourceGraphFingerprint", "")
    )
    TechnologyIdentity = str(
        TechnologyFingerprint
        or getattr(Plan, "TechnologyFingerprint", "")
    )
    DomainComplete = bool(
        Complete
        and ResourceIdentity
        and TechnologyIdentity
        and Problem.DomainComplete
        and Problem.Fabric.Complete
        and Problem.Interface is not None
        and Problem.Interface.Complete
        and SignalFactorIdentities
        and all(Factors for _, Factors in SignalFactorIdentities)
        and all(
            bool(getattr(Domain, "Complete", False))
            for Domain in Problem.OwnedTerminalDomains
        )
    )
    LocalFactorDomainFingerprint = _Fingerprint((
        "physical-component-local-factor-domain-v1",
        ComponentTopologyFingerprint,
        InterfaceContractFingerprint,
        SignalFactorIdentities,
    ))
    ProjectionFingerprint = _Fingerprint((
        "physical-component-local-factor-projection-v1",
        ComponentTopologyFingerprint,
        ResourceIdentity,
        TechnologyIdentity,
        InterfaceContractFingerprint,
        LocalFactorDomainFingerprint,
        DomainComplete,
    ))
    return PhysicalComponentLocalFactorProjection(
        ProjectionFingerprint=ProjectionFingerprint,
        ComponentTopologyFingerprint=ComponentTopologyFingerprint,
        ResourceGraphFingerprint=ResourceIdentity,
        TechnologyFingerprint=TechnologyIdentity,
        InterfaceContractFingerprint=InterfaceContractFingerprint,
        LocalFactorDomainFingerprint=LocalFactorDomainFingerprint,
        SignalFactorIdentities=SignalFactorIdentities,
        Complete=DomainComplete,
    )


def BuildPhysicalComponentLocalFactorUnsatCertificate(
    Problem: ComponentRoutingProblem,
    LocalFactorsBySignal: Any,
    Proof: dict[str, object],
    *,
    ResourceGraphFingerprint: str = "",
    TechnologyFingerprint: str = "",
) -> PhysicalComponentLocalFactorUnsatCertificate:
    """Bind one complete architectural-UNSAT proof to its cheap projection."""
    Projection = BuildPhysicalComponentLocalFactorProjection(
        Problem,
        LocalFactorsBySignal,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        TechnologyFingerprint=TechnologyFingerprint,
    )
    _, _, _, SignalRecordsByName = (
        _BuildPhysicalComponentLocalFactorProjectionParts(
            Problem,
            LocalFactorsBySignal,
        )
    )
    CoreSignals = tuple(sorted({
        str(Value)
        for Value in Proof.get(
            "GlobalRelaxedLocalUnsatCoreSignals",
            (),
        ) or ()
    }))
    CoreSignalFactorIdentities = tuple(sorted(
        SignalRecordsByName[Signal]
        for Signal in CoreSignals
        if Signal in SignalRecordsByName
    ))
    SourceProofFingerprint = str(Proof.get(
        "GlobalRelaxedLocalProofFingerprint",
        "",
    ))
    ProofKind = str(Proof.get(
        "GlobalRelaxedLocalUnsatCoreKind",
        "",
    ))
    Complete = bool(
        Projection.Complete
        and Proof.get("GlobalRelaxedLocalProofComplete", False)
        and Proof.get("GlobalRelaxedLocalCoreComplete", False)
        and Proof.get("GlobalRelaxedLocalProofStatus", "")
        == "architectural-unsatisfiable"
        and SourceProofFingerprint
        and ProofKind
        and CoreSignals
        and len(CoreSignalFactorIdentities) == len(CoreSignals)
    )
    ProofFingerprint = _Fingerprint((
        "physical-component-normalized-local-factor-proof-v1",
        Projection.ProjectionFingerprint,
        CoreSignalFactorIdentities,
        ProofKind,
        Complete,
    ))
    CertificateFingerprint = _Fingerprint((
        "physical-component-local-factor-unsat-certificate-v1",
        Projection.ProjectionFingerprint,
        CoreSignalFactorIdentities,
        ProofFingerprint,
        ProofKind,
        Complete,
    ))
    return PhysicalComponentLocalFactorUnsatCertificate(
        CertificateFingerprint=CertificateFingerprint,
        ProjectionFingerprint=Projection.ProjectionFingerprint,
        ComponentTopologyFingerprint=(
            Projection.ComponentTopologyFingerprint
        ),
        ResourceGraphFingerprint=Projection.ResourceGraphFingerprint,
        TechnologyFingerprint=Projection.TechnologyFingerprint,
        InterfaceContractFingerprint=(
            Projection.InterfaceContractFingerprint
        ),
        LocalFactorDomainFingerprint=(
            Projection.LocalFactorDomainFingerprint
        ),
        CoreSignalFactorIdentities=CoreSignalFactorIdentities,
        ProofFingerprint=ProofFingerprint,
        ProofKind=ProofKind,
        Complete=Complete,
    )


def ComparePhysicalComponentLocalFactorProjection(
    Certificate: PhysicalComponentLocalFactorUnsatCertificate,
    Projection: PhysicalComponentLocalFactorProjection,
) -> PhysicalComponentLocalFactorProjectionComparison:
    """Compare a retained placement without treating similarity as proof."""
    Reason = ""
    if not Certificate.Complete:
        Reason = "incomplete-certificate"
    elif not Projection.Complete:
        Reason = "incomplete-projection"
    elif (
        Certificate.ResourceGraphFingerprint
        != Projection.ResourceGraphFingerprint
    ):
        Reason = "resource-graph-mismatch"
    elif (
        Certificate.TechnologyFingerprint
        != Projection.TechnologyFingerprint
    ):
        Reason = "technology-mismatch"
    elif (
        Certificate.ComponentTopologyFingerprint
        != Projection.ComponentTopologyFingerprint
    ):
        Reason = "component-topology-mismatch"
    elif (
        Certificate.InterfaceContractFingerprint
        != Projection.InterfaceContractFingerprint
    ):
        Reason = "interface-contract-mismatch"
    IdentityCompatible = not Reason
    CoreCounts: dict[tuple[str, tuple[str, ...]], int] = {}
    for Record in Projection.SignalFactorIdentities:
        CoreCounts[Record] = CoreCounts.get(Record, 0) + 1
    CoreFactorMatchCount = 0
    for Record in Certificate.CoreSignalFactorIdentities:
        if CoreCounts.get(Record, 0) > 0:
            CoreFactorMatchCount += 1
            CoreCounts[Record] -= 1
    ExactDomainMatch = bool(
        IdentityCompatible
        and Certificate.LocalFactorDomainFingerprint
        == Projection.LocalFactorDomainFingerprint
    )
    if IdentityCompatible and not ExactDomainMatch:
        Reason = "local-contract-domain-mismatch"
    CanPrune = bool(ExactDomainMatch and Certificate.Complete)
    ComparisonFingerprint = _Fingerprint((
        "physical-component-local-factor-comparison-v1",
        Certificate.CertificateFingerprint,
        Projection.ProjectionFingerprint,
        IdentityCompatible,
        ExactDomainMatch,
        CoreFactorMatchCount,
        len(Certificate.CoreSignalFactorIdentities),
        CanPrune,
        Reason,
    ))
    return PhysicalComponentLocalFactorProjectionComparison(
        ComparisonFingerprint=ComparisonFingerprint,
        IdentityCompatible=IdentityCompatible,
        ExactDomainMatch=ExactDomainMatch,
        CoreFactorMatchCount=CoreFactorMatchCount,
        CoreFactorCount=len(Certificate.CoreSignalFactorIdentities),
        CanPrune=CanPrune,
        RejectionReason=Reason,
    )


def ProveGlobalRelaxedLocalUnsatisfiability(
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
) -> dict[str, object]:
    """Run one non-recursive local proof without reserved global claims."""
    DomainFingerprint = BuildGlobalRelaxedLocalProofDomainFingerprint(
        Problem
    )
    if DeadlineSeconds is not None and DeadlineSeconds <= 0:
        return {
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalCoreComplete": False,
            "GlobalRelaxedLocalProofStatus": "incomplete",
            "GlobalRelaxedLocalProofFingerprint": "",
            "GlobalRelaxedLocalDomainFingerprint": DomainFingerprint,
            "GlobalRelaxedLocalUnsatCoreSignals": [],
            "GlobalRelaxedLocalUnsatCoreKind": "",
        }
    RelaxedProblem = replace(
        Problem,
        ProblemFingerprint=_Fingerprint((
            "global-relaxed-local-proof-problem-v1",
            DomainFingerprint,
        )),
        ReservedGlobalClaimsBySignal=(),
    )
    RelaxedSolve = SolveComponentRoutingProblem(
        RelaxedProblem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        DiscoveryVariantLimit=None,
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
    )
    Complete = RelaxedSolve.Status == "architectural-unsatisfiable"
    RelaxedDiagnostics = dict(RelaxedSolve.Diagnostics or {})
    CoreComplete = bool(
        RelaxedDiagnostics.get("LocalUnsatCoreComplete", False)
    )
    return {
        "GlobalRelaxedLocalProofComplete": Complete,
        "GlobalRelaxedLocalCoreComplete": CoreComplete,
        "GlobalRelaxedLocalProofStatus": RelaxedSolve.Status,
        "GlobalRelaxedLocalProofFingerprint": (
            RelaxedSolve.ProofFingerprint if Complete else ""
        ),
        "GlobalRelaxedLocalDomainFingerprint": DomainFingerprint,
        "GlobalRelaxedLocalUnsatCoreSignals": list(
            RelaxedDiagnostics.get("LocalUnsatCoreSignals", ()) or ()
        ),
        "GlobalRelaxedLocalUnsatCoreKind": str(
            RelaxedDiagnostics.get("LocalUnsatCoreKind", "")
        ),
        "LocalUnsatCoreProjectionFingerprint": str(
            RelaxedDiagnostics.get(
                "LocalUnsatCoreProjectionFingerprint",
                "",
            )
        ),
        "GlobalRelaxedLocalCurrentSignal": str(
            RelaxedDiagnostics.get("LocalUnsatCoreCurrentSignal", "")
        ),
        "GlobalRelaxedLocalCompleteSignal": str(
            RelaxedDiagnostics.get("LocalUnsatCoreCompleteSignal", "")
        ),
        "GlobalRelaxedLocalProofExpansionCount": (
            RelaxedSolve.ExpansionCount
        ),
    }


def ProveClosedComponentOwnedSignalFrontiers(
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    CompletedProofCache: dict[
        str, ComponentRoutingSolveResult
    ] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
) -> ComponentRoutingSolveResult:
    """Certify per-signal local frontier existence before global routing.

    This eligibility proof stops before the net-capacity CSP and before any
    routed component template is materialized.  It may therefore reject a
    placement whose owned signal domain is structurally empty without
    violating the port-first global-before-local compilation boundary.
    """
    Plan = Problem.PhysicalAssemblyPlan
    DomainFingerprint = (
        BuildGlobalRelaxedLocalProofDomainFingerprint(Problem)
        if Plan is not None
        else _Fingerprint((
            "closed-component-owned-frontier-domain-v1",
            Problem.ProblemFingerprint,
            Problem.Fabric.FabricFingerprint,
            getattr(Problem.Interface, "InterfaceFingerprint", ""),
            getattr(Problem.ResourceGraph, "GraphVersion", ""),
            getattr(
                getattr(Problem.ResourceGraph, "Technology", None),
                "TechnologyVersion",
                "",
            ),
            Problem.MaximumPowerDistance,
        ))
    )
    Cached = (
        CompletedProofCache.get(DomainFingerprint)
        if CompletedProofCache is not None
        else None
    )
    if Cached is not None:
        if Cached.Template is not None:
            raise ValueError(
                "cached owned-signal frontier proof materialized a template"
            )
        return replace(
            Cached,
            Diagnostics={
                **dict(Cached.Diagnostics or {}),
                "OwnedSignalFrontierProofCacheHit": True,
                "OwnedSignalFrontierProofDomainFingerprint": (
                    DomainFingerprint
                ),
            },
        )
    Result = SolveComponentRoutingProblem(
        Problem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        StopAfterOwnedSignalFrontierProof=True,
    )
    if Result.Template is not None:
        raise ValueError(
            "owned-signal frontier eligibility materialized a template"
        )
    if Result.Exhaustive and CompletedProofCache is not None:
        CompletedProofCache[DomainFingerprint] = replace(
            Result,
            Diagnostics={
                **dict(Result.Diagnostics or {}),
                "OwnedSignalFrontierProofCacheHit": False,
                "OwnedSignalFrontierProofDomainFingerprint": (
                    DomainFingerprint
                ),
            },
        )
    return Result


def _SelectPhysicalComponentSymbolicPortPairFactors(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Signals: tuple[str, ...],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], str]]:
    """Return the complete supported local-access and seam relation."""
    LocalFactorsBySignal = dict(FactorDomain.LocalAccessFactorsBySignal)
    SupportedLocalAccessFingerprintsBySignal = {
        str(Signal): frozenset(
            str(Support.LocalAccessFingerprint)
            for Support in Supports
        )
        for Signal, Supports
        in FactorDomain.LocalApertureSupportBySignal
    }
    FactorsBySignal: dict[str, dict[str, Any]] = {}
    SeamFingerprintByLocalAccess: dict[tuple[str, str], str] = {}
    for Signal in Signals:
        Supported = SupportedLocalAccessFingerprintsBySignal.get(
            Signal,
            frozenset(),
        )
        ByLocalAccess = {}
        for Factor in LocalFactorsBySignal.get(Signal, ()):
            LocalAccessFingerprint = str(Factor.LocalAccessFingerprint)
            if LocalAccessFingerprint not in Supported:
                continue
            Existing = ByLocalAccess.get(LocalAccessFingerprint)
            if Existing is not None and Existing != Factor:
                raise ValueError(
                    "symbolic port pair local-access identity collision"
                )
            ByLocalAccess[LocalAccessFingerprint] = Factor
            SeamFingerprintByLocalAccess[(
                Signal,
                LocalAccessFingerprint,
            )] = (
                str(getattr(Factor, "SeamContractFingerprint", ""))
                or BuildPhysicalPortSeamContractFingerprint(Factor)
            )
        if not ByLocalAccess:
            raise ValueError(
                f"symbolic port pair has no complete seam domain for {Signal}"
            )
        FactorsBySignal[Signal] = ByLocalAccess
    return FactorsBySignal, SeamFingerprintByLocalAccess


def _BuildPhysicalComponentSymbolicPortPairContext(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Signals: tuple[str, ...],
    FactorsBySignal: dict[str, dict[str, Any]],
    SeamFingerprintByLocalAccess: dict[tuple[str, str], str],
) -> dict[str, str]:
    """Build every immutable identity on which a pair proof depends."""
    PlacementFingerprint = str(Problem.PlacementFingerprint)
    PreparedDomainFingerprint = str(FactorDomain.DomainFingerprint)
    ComponentGraphFingerprint = str(FactorDomain.ComponentGraphFingerprint)
    FabricFingerprint = str(Problem.Fabric.FabricFingerprint)
    ResourceGraphFingerprint = str(FactorDomain.ResourceGraphFingerprint)
    TechnologyFingerprint = str(getattr(
        FactorDomain.AccessCertificate,
        "TechnologyFingerprint",
        "",
    ))
    AccessCertificateFingerprint = str(getattr(
        FactorDomain.AccessCertificate,
        "CertificateFingerprint",
        "",
    ))
    # Pair support is compiled over the complete prepared local-access/seam
    # domain, not over one subsequently selected global aperture tuple. Bind
    # cache identity to that immutable preparation interface so equivalent
    # CSP contracts reuse the same exact signal-local state certificates.
    InterfaceFingerprint = str(getattr(
        FactorDomain.Problem.Interface,
        "InterfaceFingerprint",
        "",
    ))
    SupportIdentity = tuple(sorted(
        (
            str(Signal),
            str(Support.LocalAccessFingerprint),
            str(Support.ApertureOptionFingerprint),
            str(Support.SupportFingerprint),
        )
        for Signal, Supports
        in FactorDomain.LocalApertureSupportBySignal
        if str(Signal) in Signals
        for Support in Supports
    ))
    LocalAccessIdentity = tuple(
        (
            Signal,
            tuple(
                (
                    LocalAccessFingerprint,
                    str(Factor.LocalContractFingerprint),
                    str(Factor.FabricDomainFingerprint),
                    tuple(Factor.OwnedTerminalFingerprints),
                    tuple(Factor.OwnedCandidateFingerprints),
                    tuple(Factor.LocalPath),
                    tuple(sorted(map(str, Factor.LocalClaims.ResourceIds))),
                )
                for LocalAccessFingerprint, Factor in sorted(
                    FactorsBySignal[Signal].items()
                )
            ),
        )
        for Signal in Signals
    )
    LocalAccessDomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-local-access-domain-v1",
        LocalAccessIdentity,
        SupportIdentity,
    ))
    SeamDomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-seam-domain-v1",
        tuple(sorted(
            (
                Signal,
                LocalAccessFingerprint,
                SeamFingerprint,
            )
            for (Signal, LocalAccessFingerprint), SeamFingerprint
            in SeamFingerprintByLocalAccess.items()
        )),
    ))
    DomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-domain-v2",
        PreparedDomainFingerprint,
        PlacementFingerprint,
        ComponentGraphFingerprint,
        FabricFingerprint,
        ResourceGraphFingerprint,
        TechnologyFingerprint,
        AccessCertificateFingerprint,
        InterfaceFingerprint,
        LocalAccessDomainFingerprint,
        SeamDomainFingerprint,
        Signals,
    ))
    return {
        "DomainFingerprint": DomainFingerprint,
        "PreparedDomainFingerprint": PreparedDomainFingerprint,
        "PlacementFingerprint": PlacementFingerprint,
        "ComponentGraphFingerprint": ComponentGraphFingerprint,
        "FabricFingerprint": FabricFingerprint,
        "ResourceGraphFingerprint": ResourceGraphFingerprint,
        "TechnologyFingerprint": TechnologyFingerprint,
        "AccessCertificateFingerprint": AccessCertificateFingerprint,
        "InterfaceFingerprint": InterfaceFingerprint,
        "LocalAccessDomainFingerprint": LocalAccessDomainFingerprint,
        "SeamDomainFingerprint": SeamDomainFingerprint,
    }


def _BuildPhysicalComponentSymbolicPortPairVariantProblem(
    Problem: ComponentRoutingProblem,
    Signal: str,
    LocalAccessFingerprint: str,
    Factor: Any,
) -> ComponentRoutingProblem:
    """Bind one exact local access while leaving global ownership relaxed."""
    if Problem.Interface is None:
        raise ValueError("symbolic port pair requires a closed interface")
    BasePort = next((
        Port
        for Port in Problem.Interface.PhysicalPortReservations
        if str(Port.Signal) == Signal
    ), None)
    SyntheticBasePort = BasePort is None
    if BasePort is None:
        Attachment = (
            tuple(Factor.LocalPath[-1])
            if Factor.LocalPath
            else tuple(Factor.FabricAttachment)
        )
        BasePort = PhysicalComponentPortReservation(
            Signal=Signal,
            Direction=Factor.Direction,
            OwnedTerminals=Factor.OwnedTerminals,
            OwnedTerminalFingerprints=(
                Factor.OwnedTerminalFingerprints
            ),
            OwnedCandidateFingerprints=(
                Factor.OwnedCandidateFingerprints
            ),
            FabricDomainFingerprint=Factor.FabricDomainFingerprint,
            FabricAttachment=Factor.FabricAttachment,
            Attachment=Attachment,
            LocalPath=Factor.LocalPath,
            GlobalPath=(Attachment,),
            Claims=Factor.LocalClaims,
            LocalClaims=Factor.LocalClaims,
            GlobalClaims=RoutingResourceClaims(),
            OwnedAccessCandidates=Factor.OwnedAccessCandidates,
            Capacity=Factor.Capacity,
        )
    SeamFingerprint = BuildPhysicalPortSeamContractFingerprint(Factor)
    VariantPort = replace(
        BasePort,
        Direction=Factor.Direction,
        OwnedTerminals=Factor.OwnedTerminals,
        OwnedTerminalFingerprints=Factor.OwnedTerminalFingerprints,
        OwnedCandidateFingerprints=Factor.OwnedCandidateFingerprints,
        FabricDomainFingerprint=Factor.FabricDomainFingerprint,
        FabricAttachment=Factor.FabricAttachment,
        LocalPath=Factor.LocalPath,
        Claims=Factor.LocalClaims,
        LocalClaims=Factor.LocalClaims,
        OwnedAccessCandidates=Factor.OwnedAccessCandidates,
        Capacity=Factor.Capacity,
        ReservationFingerprint=_Fingerprint((
            "symbolic-port-domain-reservation-v1",
            Signal,
            SeamFingerprint,
            BasePort.Attachment,
            BasePort.GlobalPath,
        )),
        CertifiedLocalContractFingerprint=(
            Factor.LocalContractFingerprint
        ),
        CertifiedSeamContractFingerprint=SeamFingerprint,
        CertifiedSupportReservationFingerprint="",
    )
    VariantPorts = tuple(
        VariantPort if str(Port.Signal) == Signal else Port
        for Port in Problem.Interface.PhysicalPortReservations
    )
    if SyntheticBasePort:
        VariantPorts = (*VariantPorts, VariantPort)
    return replace(
        Problem,
        ProblemFingerprint=_Fingerprint((
            "symbolic-port-domain-net-state-v1",
            Problem.ProblemFingerprint,
            Signal,
            LocalAccessFingerprint,
        )),
        Interface=replace(
            Problem.Interface,
            PhysicalPortReservations=VariantPorts,
            PhysicalAssemblyPlanFingerprint="",
        ),
        ReservedGlobalClaimsBySignal=(),
    )


def _BuildPhysicalComponentSymbolicNetStateFingerprint(
    States: Iterable[Any],
) -> str:
    """Bind a certificate to exact state contents, not only its cache key."""
    return _Fingerprint((
        "physical-symbolic-net-state-domain-v1",
        tuple(sorted(
            (
                str(State.Signal),
                str(State.NetFingerprint),
                tuple(sorted(State.Nodes)),
                tuple(sorted(State.Edges)),
                tuple(sorted(map(str, State.Claims.ResourceIds))),
                tuple(State.CoveredTerminals),
                tuple(State.ExportedPorts),
                tuple(State.RepeaterInputFacings),
            )
            for State in States
        )),
    ))


def ValidatePhysicalComponentSymbolicPortPairCertificate(
    Certificate: PhysicalComponentSymbolicPortPairCertificate,
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalPair: Iterable[str],
    *,
    NetStateCache: dict[str, Any] | None = None,
) -> None:
    """Reject cached pair proofs unless every dependency matches exactly."""
    Signals = tuple(sorted(frozenset(map(str, SignalPair))))
    if len(Signals) != 2:
        raise ValueError("symbolic port pair validation requires two signals")
    FactorsBySignal, SeamFingerprintByLocalAccess = (
        _SelectPhysicalComponentSymbolicPortPairFactors(
            FactorDomain,
            Signals,
        )
    )
    Context = _BuildPhysicalComponentSymbolicPortPairContext(
        Problem,
        FactorDomain,
        Signals,
        FactorsBySignal,
        SeamFingerprintByLocalAccess,
    )
    FieldValues = {
        "DomainFingerprint": Certificate.DomainFingerprint,
        "PreparedDomainFingerprint": Certificate.PreparedDomainFingerprint,
        "PlacementFingerprint": Certificate.PlacementFingerprint,
        "ComponentGraphFingerprint": Certificate.ComponentGraphFingerprint,
        "FabricFingerprint": Certificate.FabricFingerprint,
        "ResourceGraphFingerprint": Certificate.ResourceGraphFingerprint,
        "TechnologyFingerprint": Certificate.TechnologyFingerprint,
        "AccessCertificateFingerprint": (
            Certificate.AccessCertificateFingerprint
        ),
        "InterfaceFingerprint": Certificate.InterfaceFingerprint,
        "LocalAccessDomainFingerprint": (
            Certificate.LocalAccessDomainFingerprint
        ),
        "SeamDomainFingerprint": Certificate.SeamDomainFingerprint,
    }
    Mismatches = tuple(
        FieldName
        for FieldName, Expected in Context.items()
        if str(FieldValues.get(FieldName, "")) != str(Expected)
    )
    if Certificate.SignalPair != Signals:
        Mismatches = (*Mismatches, "SignalPair")
    ExpectedAccesses = tuple(
        (
            Signal,
            tuple(sorted(FactorsBySignal[Signal])),
        )
        for Signal in Signals
    )
    ExpectedSeams = tuple(sorted(
        (
            Signal,
            LocalAccessFingerprint,
            SeamFingerprint,
        )
        for (Signal, LocalAccessFingerprint), SeamFingerprint
        in SeamFingerprintByLocalAccess.items()
    ))
    if Certificate.LocalAccessFingerprintsBySignal != ExpectedAccesses:
        Mismatches = (*Mismatches, "LocalAccessFingerprintsBySignal")
    if Certificate.SeamFingerprintByLocalAccess != ExpectedSeams:
        Mismatches = (*Mismatches, "SeamFingerprintByLocalAccess")
    BindingKeys = tuple(
        (Signal, LocalAccessFingerprint, CacheKey)
        for Signal, LocalAccessFingerprint, CacheKey, _StateFingerprint
        in Certificate.NetStateBindings
    )
    if BindingKeys != Certificate.NetStateCacheKeys:
        Mismatches = (*Mismatches, "NetStateCacheKeys")
    RelaxedProblem = replace(
        FactorDomain.Problem,
        ReservedGlobalClaimsBySignal=(),
    )
    ExpectedCacheKeys = []
    for Signal in Signals:
        PreparedContextFingerprint = (
            _BuildPreparedComponentSymbolicNetStateContextFingerprint(
                RelaxedProblem,
                Signal,
            )
        )
        for LocalAccessFingerprint, Factor in sorted(
            FactorsBySignal[Signal].items()
        ):
            VariantProblem = (
                _BuildPhysicalComponentSymbolicPortPairVariantProblem(
                    RelaxedProblem,
                    Signal,
                    LocalAccessFingerprint,
                    Factor,
                )
            )
            ExpectedCacheKeys.append((
                Signal,
                LocalAccessFingerprint,
                BuildComponentSymbolicNetStateCacheKey(
                    VariantProblem,
                    Signal,
                    PreparedContextFingerprint=(
                        PreparedContextFingerprint
                    ),
                ),
            ))
    ExpectedCacheKeySet = frozenset(ExpectedCacheKeys)
    ActualCacheKeySet = frozenset(Certificate.NetStateCacheKeys)
    if (
        (Certificate.Complete and ActualCacheKeySet != ExpectedCacheKeySet)
        or not ActualCacheKeySet <= ExpectedCacheKeySet
        or len(ActualCacheKeySet) != len(Certificate.NetStateCacheKeys)
    ):
        Mismatches = (*Mismatches, "NetStateCacheDomain")
    ExpectedStateDomainFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-state-bindings-v1",
        Certificate.NetStateBindings,
    ))
    if (
        Certificate.NetStateDomainFingerprint
        != ExpectedStateDomainFingerprint
    ):
        Mismatches = (*Mismatches, "NetStateDomainFingerprint")
    if NetStateCache is not None:
        for (
            _Signal,
            _LocalAccessFingerprint,
            CacheKey,
            StateFingerprint,
        ) in Certificate.NetStateBindings:
            Cached = NetStateCache.get(CacheKey)
            if Cached is None:
                continue
            States, _Diagnostics = Cached
            if (
                _BuildPhysicalComponentSymbolicNetStateFingerprint(States)
                != StateFingerprint
            ):
                Mismatches = (*Mismatches, "NetStateCacheContents")
                break
    ExpectedProofFingerprint = _Fingerprint((
        "physical-symbolic-port-pair-proof-v2",
        Certificate.DomainFingerprint,
        Certificate.LocalAccessFingerprintsBySignal,
        Certificate.UnsupportedUnaryLocalAccess,
        Certificate.UnsupportedLocalAccessPairs,
        Certificate.UnsupportedUnarySeams,
        Certificate.UnsupportedSeamPairs,
        Certificate.NetStateDomainFingerprint,
        Certificate.Complete,
    ))
    if Certificate.ProofFingerprint != ExpectedProofFingerprint:
        Mismatches = (*Mismatches, "ProofFingerprint")
    if Mismatches:
        raise ValueError(
            "physical symbolic port-pair certificate identity mismatch: "
            + ", ".join(dict.fromkeys(Mismatches))
        )

def SelectContractIndependentOwnedSignalFrontierUnsatCore(
    Problem: ComponentRoutingProblem,
    Result: ComponentRoutingSolveResult,
) -> tuple[str, ...]:
    """Select only an unbound, port-independent owned-frontier proof.

    This is deliberately one-way: a feasible unbound frontier does not prove
    that any later fixed port contract is feasible.  It only lets placement
    eligibility reject a terminal-domain contradiction that binding cannot
    repair.
    """
    Interface = getattr(Problem, "Interface", None)
    if (
        tuple(getattr(Interface, "PhysicalPortReservations", ()))
        or bool(getattr(Problem, "ReservedGlobalClaimsBySignal", {}))
        or Result.Template is not None
        or Result.Status != "architectural-unsatisfiable"
    ):
        return ()
    Diagnostics = dict(Result.Diagnostics or {})
    CoreSignals = tuple(sorted(map(str, (
        Diagnostics.get("LocalUnsatCoreSignals", ()) or ()
    ))))
    SignalDiagnostics = Diagnostics.get("SignalDiagnostics", {})
    if (
        not CoreSignals
        or not Diagnostics.get("LocalUnsatCoreComplete", False)
        or Diagnostics.get("LocalUnsatCoreKind", "")
        != "tree-frontier-empty-owned-signal-domain"
        or not Diagnostics.get(
            "LocalUnsatCoreProjectionFingerprint",
            "",
        )
        or not isinstance(SignalDiagnostics, dict)
    ):
        return ()
    # Every signal admitted below is independently empty before a port is
    # assigned.  Return one deterministic singleton rather than the union of
    # all empty signals: the singleton is the deletion-minimal placement core
    # and can therefore drive a proof-qualified geometry change without
    # pretending that unrelated signals participated in the contradiction.
    for Signal in CoreSignals:
        SignalProof = SignalDiagnostics.get(Signal, {})
        if not isinstance(SignalProof, dict) or not (
            SignalProof.get("Complete", False)
            and SignalProof.get("EmptyPhase", "")
            == "owned-terminal-frontier"
            and SignalProof.get(
                "OwnedSignalDomainContractIndependent",
                False,
            )
            and int(SignalProof.get(
                "CertifiedRejectedCandidateCount",
                -1,
            )) == 0
        ):
            return ()
    return (CoreSignals[0],)


def PromoteCoveredLocalContractNoGoods(
    Plan: PhysicalComponentAssemblyPlan,
    CoreSignals: Any,
    Resources: Any,
) -> tuple[frozenset[tuple[str, str]], ...]:
    """Lift a fully covered exact local pair to its fabric-domain pair.

    The port option cache contains a complete finite domain.  Promotion is
    allowed only when proof-qualified local-contract no-goods cover the full
    Cartesian product for the current two fabric domains.  This adds no
    speculative local solve before global planning; it is clause resolution
    over completed proofs learned after authoritative local compilation.
    """
    Signals = tuple(sorted({str(Signal) for Signal in CoreSignals}))
    if len(Signals) != 2:
        return ()
    PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
    if any(Signal not in PortsBySignal for Signal in Signals):
        return ()
    FirstSignal, SecondSignal = Signals
    FirstFabric = PortsBySignal[FirstSignal].FabricDomainFingerprint
    SecondFabric = PortsBySignal[SecondSignal].FabricDomainFingerprint
    RejectedSets = (
        Resources.RejectedPhysicalComponentPortReservationSets
    )
    CurrentFirstContract = BuildPhysicalPortLocalContractFingerprint(
        PortsBySignal[FirstSignal]
    )
    CurrentSecondContract = BuildPhysicalPortLocalContractFingerprint(
        PortsBySignal[SecondSignal]
    )
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    try:
        Domains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            Signals,
        )
    except ValueError:
        return ()
    DomainCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        str(getattr(Preparation, "DomainFingerprint", ""))
    )
    FirstContracts = frozenset(
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in Domains.get(FirstSignal, ())
        if Option.FabricDomainFingerprint == FirstFabric
    )
    SecondContracts = frozenset(
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in Domains.get(SecondSignal, ())
        if Option.FabricDomainFingerprint == SecondFabric
    )
    if not FirstContracts or not SecondContracts:
        return ()
    if (
        CurrentFirstContract not in FirstContracts
        or CurrentSecondContract not in SecondContracts
    ):
        return ()
    Promotion = frozenset((
            (
                FirstSignal,
                "local-factor-domain:"
                + str(DomainCacheKey)
                + ":"
                + FirstFabric,
            ),
            (
                SecondSignal,
                "local-factor-domain:"
                + str(DomainCacheKey)
                + ":"
                + SecondFabric,
            ),
        ))
    if Promotion in RejectedSets:
        return (Promotion,)

    def PairIsRejected(
        FirstContract: str,
        SecondContract: str,
    ) -> bool:
        PairKeys = frozenset((
                (FirstSignal, FirstContract),
                (SecondSignal, SecondContract),
                (
                    FirstSignal,
                    "local-signal-domain:" + str(DomainCacheKey),
                ),
                (
                    SecondSignal,
                    "local-signal-domain:" + str(DomainCacheKey),
                ),
            ))
        return any(
            RejectedSet.issubset(PairKeys)
            for RejectedSet in RejectedSets
        )

    if all(
        PairIsRejected(FirstContract, SecondContract)
        for FirstContract in FirstContracts
        for SecondContract in SecondContracts
    ):
        RejectedSets.add(Promotion)
        RejectedSets.difference_update({
                frozenset((
                    (FirstSignal, FirstContract),
                    (SecondSignal, SecondContract),
                ))
                for FirstContract in FirstContracts
                for SecondContract in SecondContracts
        })
        return (Promotion,)
    return ()


def BuildDirectionalLocalFactorNoGoods(
    Plan: PhysicalComponentAssemblyPlan,
    CurrentSignal: str,
    CompleteSignal: str,
    Resources: Any,
) -> tuple[frozenset[tuple[str, str]], ...]:
    """Resolve covered exact pairs over one complete current factor domain.

    A ``complete-opposing-net-access-pair`` proof is bound to both selected
    local port contracts.  It does not by itself cover alternative seams for
    ``CurrentSignal``.  Promote the current side to its prepared-domain key
    only after proof-qualified exact no-goods cover every current contract
    against the same completed-side contract.
    """
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    if not CurrentSignal or not CompleteSignal or CurrentSignal == CompleteSignal:
        return ()
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    DomainFingerprint = str(
        getattr(Preparation, "DomainFingerprint", "")
    )
    if not DomainFingerprint:
        return ()
    PortSolverCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    try:
        Domains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            (CurrentSignal, CompleteSignal),
        )
    except ValueError:
        return ()
    if (
        CurrentSignal not in Domains
        or CompleteSignal not in Domains
        or not Domains[CurrentSignal]
        or not Domains[CompleteSignal]
    ):
        return ()
    PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
    if CurrentSignal not in PortsBySignal or CompleteSignal not in PortsBySignal:
        return ()
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        PortsBySignal[CurrentSignal]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        PortsBySignal[CompleteSignal]
    )
    CurrentDomainContracts = frozenset(
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in Domains[CurrentSignal]
    )
    CompleteDomainContracts = frozenset(
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in Domains[CompleteSignal]
    )
    if (
        CurrentContract not in CurrentDomainContracts
        or CompleteContract not in CompleteDomainContracts
    ):
        return ()
    RejectedSets = getattr(
        Resources,
        "RejectedPhysicalComponentPortReservationSets",
        set(),
    )

    def ExactPairIsRejected(CurrentValue: str) -> bool:
        PairKeys = frozenset((
            (CurrentSignal, CurrentValue),
            (CompleteSignal, CompleteContract),
        ))
        return any(
            RejectedSet.issubset(PairKeys)
            for RejectedSet in RejectedSets
        )

    if not all(
        ExactPairIsRejected(CurrentValue)
        for CurrentValue in CurrentDomainContracts
    ):
        return ()
    return (frozenset((
        (
            CurrentSignal,
            "local-signal-domain:" + PortSolverCacheKey,
        ),
        (CompleteSignal, CompleteContract),
    )),)


def BuildPhysicalLocalPortPairSupportCertificate(
    Preparation: Any,
    PortSolverCacheKey: str,
    RowSignal: str,
    RowContract: str,
    ColumnSignal: str,
    ColumnContracts: tuple[str, ...],
    LocalProofContextFingerprint: str,
    PairProofRecords: tuple[PhysicalLocalPortPairProofRecord, ...],
) -> PhysicalLocalPortPairSupportCertificate:
    """Freeze one completely disproven local-support row for port AC-3."""
    if (
        Preparation is None
        or not bool(getattr(Preparation, "Complete", False))
        or not bool(getattr(Preparation, "Feasible", False))
    ):
        raise ValueError(
            "local pair support requires complete feasible preparation"
        )
    PreparedDomainFingerprint = str(getattr(
        Preparation,
        "DomainFingerprint",
        "",
    ))
    Problem = getattr(Preparation, "Problem", None)
    Fabric = getattr(Problem, "Fabric", None)
    ComponentGraphFingerprint = str(getattr(
        Preparation,
        "ComponentGraphFingerprint",
        "",
    ))
    FabricFingerprint = str(getattr(Fabric, "FabricFingerprint", ""))
    ResourceGraphFingerprint = str(getattr(
        Preparation,
        "ResourceGraphFingerprint",
        "",
    ))
    TechnologyFingerprint = str(getattr(
        getattr(Preparation, "AccessCertificate", None),
        "TechnologyFingerprint",
        "",
    ))
    RowSignal = str(RowSignal)
    RowContract = str(RowContract)
    ColumnSignal = str(ColumnSignal)
    ColumnContracts = tuple(sorted({
        str(Value) for Value in ColumnContracts if str(Value)
    }))
    LocalProofContextFingerprint = str(LocalProofContextFingerprint)
    if any(
        not isinstance(Value, PhysicalLocalPortPairProofRecord)
        for Value in PairProofRecords
    ):
        raise ValueError("local pair support row is incomplete")
    PairProofRecords = tuple(sorted(
        PairProofRecords,
        key=lambda Value: (
            Value.CurrentContract,
            Value.ProofDomainFingerprint,
            Value.ProofFingerprint,
        ),
    ))
    ExpectedRecordKeys = frozenset(
        (ColumnContract, RowContract)
        for ColumnContract in ColumnContracts
    )
    ActualRecordKeys = frozenset(
        (Value.CurrentContract, Value.CompleteContract)
        for Value in PairProofRecords
    )
    RecordsAreExact = bool(
        len(PairProofRecords) == len(ColumnContracts)
        and ActualRecordKeys == ExpectedRecordKeys
        and all(
            isinstance(Value, PhysicalLocalPortPairProofRecord)
            and Value.CurrentSignal == ColumnSignal
            and Value.CompleteSignal == RowSignal
            and Value.CompleteContract == RowContract
            and Value.CurrentContract in ColumnContracts
            and Value.ProofDomainFingerprint
            and Value.ProofFingerprint
            and Value.Status == "architectural-unsatisfiable"
            and Value.Complete
            and Value.Feasible is False
            for Value in PairProofRecords
        )
    )
    if (
        not PreparedDomainFingerprint
        or not PortSolverCacheKey
        or not ComponentGraphFingerprint
        or not FabricFingerprint
        or not ResourceGraphFingerprint
        or not TechnologyFingerprint
        or not RowSignal
        or not RowContract
        or not ColumnSignal
        or RowSignal == ColumnSignal
        or not ColumnContracts
        or not LocalProofContextFingerprint
        or not RecordsAreExact
    ):
        raise ValueError("local pair support row is incomplete")
    CertificateFingerprint = _Fingerprint((
        "physical-local-port-pair-support-row-v1",
        PreparedDomainFingerprint,
        PortSolverCacheKey,
        ComponentGraphFingerprint,
        FabricFingerprint,
        ResourceGraphFingerprint,
        TechnologyFingerprint,
        RowSignal,
        RowContract,
        ColumnSignal,
        ColumnContracts,
        LocalProofContextFingerprint,
        PairProofRecords,
    ))
    return PhysicalLocalPortPairSupportCertificate(
        CertificateFingerprint=CertificateFingerprint,
        PreparedDomainFingerprint=PreparedDomainFingerprint,
        PortSolverCacheKey=str(PortSolverCacheKey),
        ComponentGraphFingerprint=ComponentGraphFingerprint,
        FabricFingerprint=FabricFingerprint,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        TechnologyFingerprint=TechnologyFingerprint,
        RowSignal=RowSignal,
        RowContract=RowContract,
        ColumnSignal=ColumnSignal,
        ColumnContracts=ColumnContracts,
        LocalProofContextFingerprint=LocalProofContextFingerprint,
        PairProofRecords=PairProofRecords,
        Complete=True,
    )


def ValidatePhysicalLocalPortPairSupportCertificate(
    Certificate: PhysicalLocalPortPairSupportCertificate,
    Preparation: Any,
    PortSolverCacheKey: str,
) -> bool:
    """Require a certificate to be the canonical row for this preparation."""
    if (
        not isinstance(
            Certificate,
            PhysicalLocalPortPairSupportCertificate,
        )
        or not Certificate.Complete
        or Certificate.PortSolverCacheKey != PortSolverCacheKey
    ):
        return False
    try:
        ExpectedProofContextFingerprint = (
            BuildPhysicalLocalPairProofContextFingerprint(
                Preparation.Problem,
                Preparation,
            )
        )
    except (AttributeError, TypeError, ValueError):
        return False
    if (
        Certificate.LocalProofContextFingerprint
        != ExpectedProofContextFingerprint
    ):
        return False
    LocalFactorsBySignal = dict(getattr(
        Preparation,
        "LocalAccessFactorsBySignal",
        (),
    ))
    RowContracts = frozenset(
        str(Value.LocalContractFingerprint)
        for Value in LocalFactorsBySignal.get(Certificate.RowSignal, ())
    )
    ColumnContracts = frozenset(
        str(Value.LocalContractFingerprint)
        for Value in LocalFactorsBySignal.get(Certificate.ColumnSignal, ())
    )
    if (
        not RowContracts
        or not ColumnContracts
        or Certificate.RowContract not in RowContracts
        or frozenset(Certificate.ColumnContracts) != ColumnContracts
    ):
        return False
    try:
        Rebuilt = BuildPhysicalLocalPortPairSupportCertificate(
            Preparation,
            PortSolverCacheKey,
            Certificate.RowSignal,
            Certificate.RowContract,
            Certificate.ColumnSignal,
            Certificate.ColumnContracts,
            Certificate.LocalProofContextFingerprint,
            Certificate.PairProofRecords,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return Rebuilt == Certificate


def CertifyDirectionalLocalContractPortfolio(
    Plan: PhysicalComponentAssemblyPlan,
    CurrentSignal: str,
    CompleteSignal: str,
    Resources: Any,
    *,
    BuildProofDomainFingerprint: Callable[[Any, Any], str],
    EvaluatePair: Callable[[Any, Any, str], dict[str, object]],
    LocalProofContextFingerprint: str,
    MaximumCompletedRows: int | None = None,
) -> dict[str, object]:
    """Certify exact local-contract pairs before resolving factor clauses.

    The selected fixed-plan proof admits this stage but covers only its exact
    pair. Every other pair must either already have a proof-qualified no-good
    or complete the same global-relaxed local feasibility proof here. A
    directional clause is emitted only after all current-side contracts are
    covered for one exact complete-side contract.
    """
    CurrentSignal = str(CurrentSignal)
    CompleteSignal = str(CompleteSignal)
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    DomainFingerprint = str(
        getattr(Preparation, "DomainFingerprint", "")
    )
    if (
        not CurrentSignal
        or not CompleteSignal
        or CurrentSignal == CompleteSignal
        or not DomainFingerprint
        or not LocalProofContextFingerprint
    ):
        return {
            "Complete": False,
            "Reason": "missing-directional-portfolio-domain",
        }
    PortSolverCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    try:
        Domains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            (CurrentSignal, CompleteSignal),
        )
    except ValueError:
        return {
            "Complete": False,
            "Reason": "missing-complete-port-factor-domain",
            "PortSolverCacheKey": PortSolverCacheKey,
        }
    if any(
        Signal not in Domains or not Domains[Signal]
        for Signal in (CurrentSignal, CompleteSignal)
    ):
        return {
            "Complete": False,
            "Reason": "empty-directional-port-option-domain",
            "PortSolverCacheKey": PortSolverCacheKey,
        }

    def UniqueOptions(Signal: str) -> dict[str, Any]:
        Result = {}
        for Option in Domains[Signal]:
            Contract = BuildPhysicalPortLocalContractFingerprint(Option)
            Result.setdefault(Contract, Option)
        return Result

    CurrentOptions = UniqueOptions(CurrentSignal)
    CompleteOptions = UniqueOptions(CompleteSignal)
    RejectedSets = (
        Resources.RejectedPhysicalComponentPortReservationSets
    )
    ProofCache = getattr(
        Resources,
        "PhysicalComponentLocalInterfaceFactorProofCache",
        None,
    )
    if ProofCache is None:
        ProofCache = {}
        Resources.PhysicalComponentLocalInterfaceFactorProofCache = ProofCache
    SupportCertificateCache = getattr(
        Resources,
        "PhysicalLocalPortPairSupportCertificateCache",
        None,
    )
    if SupportCertificateCache is None:
        SupportCertificateCache = {}
        Resources.PhysicalLocalPortPairSupportCertificateCache = (
            SupportCertificateCache
        )
    EvaluatedPairCount = 0
    CachedPairCount = 0
    PreviouslyCoveredPairCount = 0
    CertifiedPairCount = 0
    IncompletePairs = []
    DeferredIncompleteRows = []
    ProofDomainFingerprints = set()
    DirectionalNoGoods = set()
    SupportCertificates = []
    FeasibleWitness = None
    FeasibleWitnessOptions = None
    ProcessedCompleteContractCount = 0
    CompletedRowCount = 0
    CompletedRowLimitReached = False

    def ExactPairNoGood(
        CurrentContract: str,
        CompleteContract: str,
    ) -> frozenset[tuple[str, str]]:
        return frozenset((
            (CurrentSignal, CurrentContract),
            (CompleteSignal, CompleteContract),
        ))

    def PairCovered(NoGood: frozenset[tuple[str, str]]) -> bool:
        ProofKeys = set(NoGood)
        for Signal, Contract in NoGood:
            Option = (
                CurrentOptions.get(Contract)
                if Signal == CurrentSignal
                else CompleteOptions.get(Contract)
                if Signal == CompleteSignal
                else None
            )
            if Option is None:
                continue
            ProofKeys.add((
                Signal,
                "local-signal-domain:" + PortSolverCacheKey,
            ))
            ProofKeys.add((
                Signal,
                "local-factor-domain:"
                + PortSolverCacheKey
                + ":"
                + str(Option.FabricDomainFingerprint),
            ))
        ExpandedNoGood = frozenset(ProofKeys)
        return any(
            RejectedSet.issubset(ExpandedNoGood)
            for RejectedSet in RejectedSets
        )

    SelectedCompleteOption = next(
        Port for Port in Plan.Ports if Port.Signal == CompleteSignal
    )
    SelectedCompleteContract = BuildPhysicalPortLocalContractFingerprint(
        SelectedCompleteOption
    )
    OrderedCompleteOptions = tuple(sorted(
        CompleteOptions.items(),
        key=lambda Value: (
            Value[0] != SelectedCompleteContract,
            Value[0],
        ),
    ))
    for CompleteContract, CompleteOption in OrderedCompleteOptions:
        ProcessedCompleteContractCount += 1
        ResolvedRowClause = frozenset((
            (
                CurrentSignal,
                "local-signal-domain:" + PortSolverCacheKey,
            ),
            (CompleteSignal, CompleteContract),
        ))
        if ResolvedRowClause in RejectedSets:
            PreviouslyCoveredPairCount += len(CurrentOptions)
            continue
        CompleteContractCovered = True
        RowProofRecords = []
        for CurrentContract, CurrentOption in sorted(CurrentOptions.items()):
            ExactNoGood = ExactPairNoGood(
                CurrentContract,
                CompleteContract,
            )
            if PairCovered(ExactNoGood):
                PreviouslyCoveredPairCount += 1
                continue
            ProofDomainFingerprint = str(BuildProofDomainFingerprint(
                CurrentOption,
                CompleteOption,
            ))
            if not ProofDomainFingerprint:
                CompleteContractCovered = False
                IncompletePairs.append((CurrentContract, CompleteContract))
                DeferredIncompleteRows.append(CompleteContract)
                break
            ProofDomainFingerprints.add(ProofDomainFingerprint)
            CacheKey = (
                PortSolverCacheKey,
                CurrentSignal,
                CompleteContract,
                ProofDomainFingerprint,
            )
            Proof = ProofCache.get(CacheKey)
            if Proof is None:
                Proof = dict(EvaluatePair(
                    CurrentOption,
                    CompleteOption,
                    ProofDomainFingerprint,
                ))
                EvaluatedPairCount += 1
            else:
                Proof = dict(Proof)
                CachedPairCount += 1
            ProofSignals = frozenset(map(str, (
                Proof.get("GlobalRelaxedLocalUnsatCoreSignals", ()) or ()
            )))
            Certified = bool(
                Proof.get("GlobalRelaxedLocalProofComplete", False)
                and Proof.get("GlobalRelaxedLocalCoreComplete", False)
                and Proof.get("GlobalRelaxedLocalUnsatCoreKind", "")
                == "complete-opposing-net-access-pair"
                and Proof.get("GlobalRelaxedLocalCurrentSignal", "")
                == CurrentSignal
                and Proof.get("GlobalRelaxedLocalCompleteSignal", "")
                == CompleteSignal
                and ProofSignals
                == frozenset((CurrentSignal, CompleteSignal))
                and Proof.get("GlobalRelaxedLocalDomainFingerprint", "")
                == ProofDomainFingerprint
                and bool(Proof.get(
                    "GlobalRelaxedLocalProofFingerprint",
                    "",
                ))
            )
            if Certified:
                # Only complete proof-qualified UNSAT results may populate
                # the reusable proof cache. Deadline/incomplete results carry
                # no monotonic information and must be recomputed.
                ProofCache[CacheKey] = Proof
                RejectedSets.add(ExactNoGood)
                CertifiedPairCount += 1
                RowProofRecords.append(
                    PhysicalLocalPortPairProofRecord(
                        CurrentSignal=CurrentSignal,
                        CurrentContract=CurrentContract,
                        CompleteSignal=CompleteSignal,
                        CompleteContract=CompleteContract,
                        ProofDomainFingerprint=ProofDomainFingerprint,
                        ProofFingerprint=str(Proof[
                            "GlobalRelaxedLocalProofFingerprint"
                        ]),
                        Status="architectural-unsatisfiable",
                        Complete=True,
                        Feasible=False,
                    )
                )
            else:
                CompleteContractCovered = False
                if (
                    Proof.get("GlobalRelaxedLocalProofStatus") == "feasible"
                    and Proof.get(
                        "GlobalRelaxedLocalFeasibleWitnessComplete",
                        False,
                    )
                ):
                    # A concrete route is a complete positive witness for this
                    # exact pair and is safe to retain.
                    ProofCache[CacheKey] = Proof
                    FeasibleWitness = (CurrentContract, CompleteContract)
                    FeasibleWitnessOptions = (CurrentOption, CompleteOption)
                    break
                IncompletePairs.append((CurrentContract, CompleteContract))
                # Incompleteness carries no authoritative support fact.  Stop
                # this row after its first unknown pair so later rows receive
                # a fair opportunity within the same admitted proof domain.
                # A future call may resume the underlying portfolio discovery
                # and reevaluate this exact pair without caching speculation.
                DeferredIncompleteRows.append(CompleteContract)
                break
        if FeasibleWitness is not None:
            break
        if CompleteContractCovered:
            CompletedRowCount += 1
            if (
                bool(getattr(Preparation, "Complete", False))
                and len(RowProofRecords) == len(CurrentOptions)
            ):
                Certificate = BuildPhysicalLocalPortPairSupportCertificate(
                    Preparation,
                    PortSolverCacheKey,
                    CompleteSignal,
                    CompleteContract,
                    CurrentSignal,
                    tuple(sorted(CurrentOptions)),
                    LocalProofContextFingerprint,
                    tuple(RowProofRecords),
                )
                SupportCertificateCache[
                    Certificate.CertificateFingerprint
                ] = Certificate
                SupportCertificates.append(Certificate)
            PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
            PortsBySignal[CompleteSignal] = CompleteOption
            PortfolioPorts = tuple(
                PortsBySignal.get(Port.Signal, Port)
                for Port in Plan.Ports
            )
            try:
                PortfolioPlan = replace(Plan, Ports=PortfolioPorts)
            except TypeError:
                PortfolioPlan = type("PortfolioPlan", (), {
                    **vars(Plan),
                    "Ports": PortfolioPorts,
                })()
            Clauses = BuildDirectionalLocalFactorNoGoods(
                PortfolioPlan,
                CurrentSignal,
                CompleteSignal,
                Resources,
            )
            RejectedSets.update(Clauses)
            DirectionalNoGoods.update(Clauses)
            if Clauses:
                # Clause resolution has replaced the full exact row. Keeping
                # every antecedent makes the persistent CSP epoch and binary
                # propagation grow quadratically without adding information.
                for CurrentContract in CurrentOptions:
                    RejectedSets.discard(ExactPairNoGood(
                        CurrentContract,
                        CompleteContract,
                    ))
            if (
                MaximumCompletedRows is not None
                and CompletedRowCount >= MaximumCompletedRows
            ):
                CompletedRowLimitReached = True
                break

    if FeasibleWitnessOptions is not None:
        CurrentOption, CompleteOption = FeasibleWitnessOptions
        PreferredReservations = getattr(
            Resources,
            "PreferredPhysicalComponentPortReservationsBySignal",
            None,
        )
        if PreferredReservations is None:
            PreferredReservations = {}
            Resources.PreferredPhysicalComponentPortReservationsBySignal = (
                PreferredReservations
            )
        PreferredGlobalContracts = getattr(
            Resources,
            "PreferredPhysicalComponentGlobalContractsBySignal",
            None,
        )
        if PreferredGlobalContracts is None:
            PreferredGlobalContracts = {}
            Resources.PreferredPhysicalComponentGlobalContractsBySignal = (
                PreferredGlobalContracts
            )
        if all(
            getattr(Option, "ReservationFingerprint", "")
            and hasattr(Option, "GlobalPath")
            for Option in (CurrentOption, CompleteOption)
        ):
            PreferredReservations.update({
                CurrentSignal: CurrentOption.ReservationFingerprint,
                CompleteSignal: CompleteOption.ReservationFingerprint,
            })
            PreferredGlobalContracts.update({
                CurrentSignal: BuildPhysicalPortGlobalContractFingerprint(
                    CurrentOption
                ),
                CompleteSignal: BuildPhysicalPortGlobalContractFingerprint(
                    CompleteOption
                ),
            })

    DeferredRowCount = max(
        0,
        len(OrderedCompleteOptions) - ProcessedCompleteContractCount,
    )
    Complete = bool(
        not IncompletePairs
        and FeasibleWitness is None
        and DeferredRowCount == 0
    )
    PromotedFabricNoGoods = (
        PromoteCoveredLocalContractNoGoods(
            Plan,
            (CurrentSignal, CompleteSignal),
            Resources,
        )
        if Complete
        else ()
    )
    return {
        "Complete": Complete,
        "Status": (
            "complete-unsatisfiable-portfolio"
            if Complete
            else "partial-complete-rows"
            if CompletedRowLimitReached
            else "feasible-witness"
            if FeasibleWitness is not None
            else "incomplete"
        ),
        "Reason": (
            "complete-cartesian-local-contract-coverage"
            if Complete
            else "completed-row-frontier-yield"
            if CompletedRowLimitReached
            else "exact-local-contract-pair-is-feasible"
            if FeasibleWitness is not None
            else "local-interface-factor-proof-incomplete"
        ),
        "Stage": "local-interface-factor-portfolio-certification",
        "PortSolverCacheKey": PortSolverCacheKey,
        "CurrentSignal": CurrentSignal,
        "CompleteSignal": CompleteSignal,
        "CurrentContractCount": len(CurrentOptions),
        "CompleteContractCount": len(CompleteOptions),
        "PairDomainCount": len(CurrentOptions) * len(CompleteOptions),
        "EvaluatedPairCount": EvaluatedPairCount,
        "CachedPairCount": CachedPairCount,
        "PreviouslyCoveredPairCount": PreviouslyCoveredPairCount,
        "CertifiedPairCount": CertifiedPairCount,
        "IncompletePairs": [list(Value) for Value in IncompletePairs],
        "IncompletePairCount": len(IncompletePairs),
        "DeferredIncompleteRows": list(DeferredIncompleteRows),
        "DeferredIncompleteRowCount": len(DeferredIncompleteRows),
        "ProcessedCompleteContractCount": ProcessedCompleteContractCount,
        "CompletedRowCount": CompletedRowCount,
        "DeferredRowCount": DeferredRowCount,
        "CompletedRowLimitReached": CompletedRowLimitReached,
        "FeasibleWitness": (
            list(FeasibleWitness) if FeasibleWitness is not None else None
        ),
        "FeasibleWitnessCount": int(FeasibleWitness is not None),
        "ProofDomainFingerprints": sorted(ProofDomainFingerprints),
        "DirectionalNoGoodCount": len(DirectionalNoGoods),
        "PromotedFabricNoGoodCount": len(PromotedFabricNoGoods),
        "LocalPairSupportCertificateCount": len(SupportCertificates),
        "LocalPairSupportCertificateFingerprints": [
            Value.CertificateFingerprint for Value in SupportCertificates
        ],
        "DirectionalNoGoodKeys": [
            [list(Key) for Key in sorted(NoGood)]
            for NoGood in sorted(
                DirectionalNoGoods,
                key=lambda Value: tuple(sorted(Value)),
            )
        ],
        "PromotedFabricNoGoodKeys": [
            [list(Key) for Key in sorted(NoGood)]
            for NoGood in PromotedFabricNoGoods
        ],
    }


def CertifyLocalInterfaceFactorPortfolio(
    Problem: ComponentRoutingProblem,
    Plan: PhysicalComponentAssemblyPlan,
    CurrentSignal: str,
    CompleteSignal: str,
    Resources: Any,
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
) -> dict[str, object]:
    """Compile a certified local interface-factor portfolio after admission."""
    StartedAt = monotonic()
    Variants: dict[tuple[str, str], ComponentRoutingProblem] = {}
    if VariantPortfolioCache is None:
        VariantPortfolioCache = {}
    if NetVariantConstructionCache is None:
        NetVariantConstructionCache = {}
    if RouteClaimsConstructionCache is None:
        RouteClaimsConstructionCache = {}
    if NetVariantDiscoveryStateCache is None:
        NetVariantDiscoveryStateCache = {}
    # The full closed local domain is invariant across this Cartesian port
    # portfolio. Hash it once, then compose each pair from the exact normalized
    # replacement-port identities. Re-hashing every terminal candidate and
    # claim for every pair made identity construction dominate the solve.
    PortfolioBaseDomainFingerprint = (
        BuildGlobalRelaxedLocalProofDomainFingerprint(Problem)
    )
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    PreparedDomainFingerprint = str(getattr(
        Preparation,
        "DomainFingerprint",
        "",
    ))
    LocalProofContextFingerprint = ""
    if (
        isinstance(
            Preparation,
            PreparedPhysicalComponentPortFactorDomain,
        )
        and isinstance(Preparation.Problem, ComponentRoutingProblem)
        and Preparation.Complete
        and Preparation.Feasible
    ):
        LocalProofContextFingerprint = (
            BuildPhysicalLocalPairProofContextFingerprint(
                Problem,
                Preparation,
            )
        )
    PortSolverCacheKey = (
        BuildPhysicalComponentPortSolverCacheKey(
            PreparedDomainFingerprint
        )
        if PreparedDomainFingerprint
        else ""
    )
    ContextCache = getattr(
        Resources,
        "PhysicalComponentLocalPortfolioContextCache",
        None,
    )
    if ContextCache is None:
        ContextCache = {}
        Resources.PhysicalComponentLocalPortfolioContextCache = ContextCache
    ContextKey = (
        PortSolverCacheKey,
        PortfolioBaseDomainFingerprint,
        str(CurrentSignal),
        str(CompleteSignal),
    )
    PersistentContextReused = ContextKey in ContextCache
    PersistentContext = ContextCache.setdefault(ContextKey, {
        "CompletePortfolioStaticContext": None,
        "CompletePortfoliosByContract": {},
        "OpposingRowContextsByContract": {},
        "CurrentAccessContractDomain": None,
    })
    PersistentContext.setdefault("CurrentAccessContractDomain", None)
    CompletePortfolioStaticContext = PersistentContext[
        "CompletePortfolioStaticContext"
    ]
    CompletePortfoliosByContract = PersistentContext[
        "CompletePortfoliosByContract"
    ]
    OpposingRowContextsByContract = PersistentContext[
        "OpposingRowContextsByContract"
    ]
    BulkPairResultsByCompleteContract = {}
    BulkAccessSignatureCount = 0
    BulkVariantScanCount = 0
    BulkSignaturePairCheckCount = 0
    CurrentAccessContractDomain = PersistentContext[
        "CurrentAccessContractDomain"
    ]
    CurrentAccessContractDomainReused = (
        CurrentAccessContractDomain is not None
    )
    PortfolioCompilationSeconds = 0.0
    RowContextConstructionSeconds = 0.0
    BulkAccessPreparationSeconds = 0.0
    BulkVariantScanSeconds = 0.0
    RejectedReservationSets = getattr(
        Resources,
        "RejectedPhysicalComponentPortReservationSets",
        None,
    )
    if RejectedReservationSets is None:
        RejectedReservationSets = set()
        Resources.RejectedPhysicalComponentPortReservationSets = (
            RejectedReservationSets
        )
    PortfolioOrigin = _Origin(Problem)

    def PortProofIdentity(Port: Any) -> tuple[object, ...]:
        return (
            Port.Signal,
            Port.Direction,
            tuple(
                _Normalize(Value, PortfolioOrigin)
                for Value in Port.OwnedTerminals
            ),
            tuple(Port.OwnedTerminalFingerprints),
            tuple(Port.OwnedCandidateFingerprints),
            _Normalize(Port.FabricAttachment, PortfolioOrigin),
            _Normalize(Port.Attachment, PortfolioOrigin),
            tuple(
                _Normalize(Value, PortfolioOrigin)
                for Value in Port.LocalPath
            ),
            Port.Capacity,
        )

    def BuildVariant(CurrentOption: Any, CompleteOption: Any):
        Key = (
            BuildPhysicalPortLocalContractFingerprint(CurrentOption),
            BuildPhysicalPortLocalContractFingerprint(CompleteOption),
        )
        Cached = Variants.get(Key)
        if Cached is not None:
            return Cached
        Ports = tuple(
            CurrentOption if Port.Signal == CurrentSignal
            else CompleteOption if Port.Signal == CompleteSignal
            else Port
            for Port in Plan.Ports
        )
        PlanFingerprint = _Fingerprint((
            "local-interface-factor-portfolio-plan-v1",
            Plan.PlanFingerprint,
            Key,
        ))
        VariantPlan = replace(
            Plan,
            Ports=Ports,
            PlanFingerprint=PlanFingerprint,
            PortAssignmentFingerprint=_Fingerprint(Key),
        )
        if Problem.Interface is None:
            raise ValueError("portfolio certification requires closed interface")
        VariantInterface = replace(
            Problem.Interface,
            PhysicalPortReservations=Ports,
            PhysicalAssemblyPlanFingerprint=PlanFingerprint,
        )
        VariantProblem = replace(
            Problem,
            ProblemFingerprint=_Fingerprint((
                "local-interface-factor-portfolio-problem-v1",
                Problem.ProblemFingerprint,
                PlanFingerprint,
            )),
            Interface=VariantInterface,
            PhysicalAssemblyPlan=VariantPlan,
            ReservedGlobalClaimsBySignal=(),
        )
        Variants[Key] = VariantProblem
        return VariantProblem

    def ProofDomain(CurrentOption: Any, CompleteOption: Any) -> str:
        return _Fingerprint((
            "global-relaxed-local-interface-portfolio-domain-v1",
            PortfolioBaseDomainFingerprint,
            PortProofIdentity(CurrentOption),
            PortProofIdentity(CompleteOption),
        ))

    try:
        CachedPortDomains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            (CurrentSignal, CompleteSignal),
        )
    except ValueError:
        CachedPortDomains = {}
    CompleteOptionsByContract = {}
    for Option in CachedPortDomains.get(CompleteSignal, ()):
        CompleteOptionsByContract.setdefault(
            BuildPhysicalPortLocalContractFingerprint(Option),
            Option,
        )
    MultiPortfolioResult = None
    if CompleteOptionsByContract:
        if CompletePortfolioStaticContext is None:
            CompletePortfolioStaticContext = (
                BuildCompleteComponentNetPortfolioStaticContext(
                    Problem,
                    CompleteSignal,
                )
            )
            PersistentContext["CompletePortfolioStaticContext"] = (
                CompletePortfolioStaticContext
            )
        Remaining = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        PortfolioCompilationStartedAt = monotonic()
        MultiPortfolioResult = CompileCompleteComponentNetVariantPortfolios(
            Problem,
            CompleteSignal,
            CompleteOptionsByContract,
            DeadlineSeconds=Remaining,
            WorkCheck=WorkCheck,
            VariantPortfolioCache=VariantPortfolioCache,
            NetVariantConstructionCache=NetVariantConstructionCache,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
            NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
            StaticContext=CompletePortfolioStaticContext,
        )
        PortfolioCompilationSeconds += (
            monotonic() - PortfolioCompilationStartedAt
        )
        for Contract, Portfolio in MultiPortfolioResult.Portfolios.items():
            if Portfolio.Complete:
                CompletePortfoliosByContract[Contract] = Portfolio

    def Evaluate(
        CurrentOption: Any,
        CompleteOption: Any,
        ProofDomainFingerprint: str,
    ) -> dict[str, object]:
        nonlocal CompletePortfolioStaticContext
        nonlocal BulkAccessSignatureCount
        nonlocal BulkVariantScanCount
        nonlocal BulkSignaturePairCheckCount
        nonlocal CurrentAccessContractDomain
        nonlocal PortfolioCompilationSeconds
        nonlocal RowContextConstructionSeconds
        nonlocal BulkAccessPreparationSeconds
        nonlocal BulkVariantScanSeconds
        Remaining = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        CurrentContract = BuildPhysicalPortLocalContractFingerprint(
            CurrentOption
        )
        CompleteContract = BuildPhysicalPortLocalContractFingerprint(
            CompleteOption
        )
        VariantProblem = BuildVariant(CurrentOption, CompleteOption)
        Portfolio = CompletePortfoliosByContract.get(CompleteContract)
        if Portfolio is None:
            Portfolio = (
                MultiPortfolioResult.Portfolios.get(CompleteContract)
                if MultiPortfolioResult is not None
                else None
            )
        if Portfolio is None:
            raise ValueError(
                "shared complete-net portfolio omitted an exact contract"
            )
        RowContext = OpposingRowContextsByContract.get(CompleteContract)
        if RowContext is None and Portfolio.Complete:
            RowContextConstructionStartedAt = monotonic()
            RowContext = BuildCompleteOpposingNetAccessRowContext(
                VariantProblem,
                Portfolio.Variants,
                CurrentSignal=CurrentSignal,
                CompleteSignal=CompleteSignal,
            )
            RowContextConstructionSeconds += (
                monotonic() - RowContextConstructionStartedAt
            )
            OpposingRowContextsByContract[CompleteContract] = RowContext
        Remaining = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        RowResults = BulkPairResultsByCompleteContract.get(CompleteContract)
        if RowResults is None:
            CurrentOptionsByContract = {
                BuildPhysicalPortLocalContractFingerprint(Option): Option
                for Option in CachedPortDomains.get(CurrentSignal, ())
            }
            if (
                not CurrentOptionsByContract
                or CurrentContract not in CurrentOptionsByContract
            ):
                raise ValueError(
                    "bulk opposing-net access is missing the exact current "
                    "option domain"
                )
            if CurrentAccessContractDomain is None:
                CurrentAccessContractDomain = (
                    BuildCompleteOpposingNetAccessContractDomain(
                        Problem,
                        CurrentSignal,
                        CurrentOptionsByContract,
                    )
                )
                PersistentContext["CurrentAccessContractDomain"] = (
                    CurrentAccessContractDomain
                )
            Bulk = EvaluateCompleteOpposingNetAccessContractRow(
                VariantProblem,
                CurrentSignal=CurrentSignal,
                CompleteSignal=CompleteSignal,
                CurrentPortsByContract=CurrentOptionsByContract,
                CompleteLocalContractFingerprint=CompleteContract,
                CompleteVariants=Portfolio.Variants,
                CompleteVariantDomainComplete=Portfolio.Complete,
                DeadlineSeconds=Remaining,
                DomainFingerprintsByCurrentContract={
                    Contract: ProofDomain(Option, CompleteOption)
                    for Contract, Option
                    in CurrentOptionsByContract.items()
                },
                ContractDomain=CurrentAccessContractDomain,
                RowContext=RowContext,
                WorkCheck=WorkCheck,
            )
            RowResults = Bulk.Results
            BulkAccessSignatureCount += Bulk.AccessSignatureCount
            BulkVariantScanCount += Bulk.VariantScanCount
            BulkSignaturePairCheckCount += Bulk.SignaturePairCheckCount
            BulkAccessPreparationSeconds += float(getattr(
                Bulk,
                "AccessPreparationSeconds",
                0.0,
            ))
            BulkVariantScanSeconds += float(getattr(
                Bulk,
                "VariantScanSeconds",
                0.0,
            ))
            if (
                Portfolio.Complete
                and all(Result.Complete for Result in RowResults.values())
            ):
                BulkPairResultsByCompleteContract[
                    CompleteContract
                ] = RowResults
        OracleResult = RowResults[CurrentContract]
        ExactIdentity = bool(
            OracleResult.CurrentSignal == CurrentSignal
            and OracleResult.CompleteSignal == CompleteSignal
            and OracleResult.CurrentLocalContractFingerprint
            == CurrentContract
            and OracleResult.CompleteLocalContractFingerprint
            == CompleteContract
        )
        CertifiedUnsatisfiable = bool(
            ExactIdentity
            and OracleResult.Complete
            and OracleResult.Status == "architectural-unsatisfiable"
            and OracleResult.Feasible is False
        )
        CertifiedFeasible = bool(
            ExactIdentity
            and OracleResult.Complete
            and OracleResult.Status == "feasible"
            and OracleResult.Feasible is True
        )
        return {
            "GlobalRelaxedLocalProofComplete": CertifiedUnsatisfiable,
            "GlobalRelaxedLocalCoreComplete": CertifiedUnsatisfiable,
            "GlobalRelaxedLocalProofStatus": (
                "architectural-unsatisfiable"
                if CertifiedUnsatisfiable
                else "feasible"
                if CertifiedFeasible
                else "incomplete"
            ),
            "GlobalRelaxedLocalProofFingerprint": (
                OracleResult.ProofFingerprint
                if CertifiedUnsatisfiable or CertifiedFeasible
                else ""
            ),
            "GlobalRelaxedLocalFeasibleWitnessComplete": (
                CertifiedFeasible
            ),
            "GlobalRelaxedLocalDomainFingerprint": ProofDomainFingerprint,
            "GlobalRelaxedLocalUnsatCoreSignals": (
                [CurrentSignal, CompleteSignal]
                if CertifiedUnsatisfiable
                else []
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
                if CertifiedUnsatisfiable
                else ""
            ),
            "GlobalRelaxedLocalCurrentSignal": (
                CurrentSignal if CertifiedUnsatisfiable else ""
            ),
            "GlobalRelaxedLocalCompleteSignal": (
                CompleteSignal if CertifiedUnsatisfiable else ""
            ),
            "PairAccessOracleStatus": OracleResult.Status,
            "PairAccessOracleComplete": OracleResult.Complete,
            "PairAccessOracleFeasible": OracleResult.Feasible,
            "PairAccessOracleDomainFingerprint": (
                OracleResult.DomainFingerprint
            ),
            "PairAccessOracleProofFingerprint": (
                OracleResult.ProofFingerprint
            ),
            "PairAccessOracleExpansionCount": OracleResult.ExpansionCount,
            "PairAccessOracleDetail": OracleResult.Detail,
            "CompleteNetPortfolioStatus": Portfolio.Status,
            "CompleteNetPortfolioComplete": Portfolio.Complete,
            "CompleteNetPortfolioVariantCount": len(Portfolio.Variants),
            "CompleteNetPortfolioDomainFingerprint": (
                Portfolio.DomainFingerprint
            ),
            "CompleteNetPortfolioExpansionCount": Portfolio.ExpansionCount,
            "CompleteNetPortfolioDiagnostics": dict(
                Portfolio.Diagnostics or {}
            ),
        }

    CertificationPasses = []
    while True:
        ProofFrontierBefore = (
            len(getattr(
                Resources,
                "PhysicalComponentLocalInterfaceFactorProofCache",
                {},
            )),
            len(CompletePortfoliosByContract),
            len(OpposingRowContextsByContract),
        )
        Diagnostics = CertifyDirectionalLocalContractPortfolio(
            Plan,
            CurrentSignal,
            CompleteSignal,
            Resources,
            BuildProofDomainFingerprint=ProofDomain,
            EvaluatePair=Evaluate,
            LocalProofContextFingerprint=(
                LocalProofContextFingerprint
            ),
            MaximumCompletedRows=None,
        )
        CertificationPasses.append({
            "Complete": bool(Diagnostics.get("Complete", False)),
            "Status": str(Diagnostics.get("Status", "")),
            "CertifiedPairCount": int(
                Diagnostics.get("CertifiedPairCount", 0)
            ),
            "IncompletePairCount": int(
                Diagnostics.get("IncompletePairCount", 0)
            ),
            "CompletedRowCount": int(
                Diagnostics.get("CompletedRowCount", 0)
            ),
        })
        ProofFrontierAfter = (
            len(getattr(
                Resources,
                "PhysicalComponentLocalInterfaceFactorProofCache",
                {},
            )),
            len(CompletePortfoliosByContract),
            len(OpposingRowContextsByContract),
        )
        if (
            Diagnostics.get("Complete", False)
            or Diagnostics.get("FeasibleWitnessCount", 0)
            or ProofFrontierAfter <= ProofFrontierBefore
        ):
            break
    return {
        **Diagnostics,
        "CertificationPassCount": len(CertificationPasses),
        "CertificationPasses": CertificationPasses,
        "PersistentPortfolioContextReused": PersistentContextReused,
        "CurrentAccessContractDomainReused": (
            CurrentAccessContractDomainReused
        ),
        "CurrentAccessContractDomainFingerprint": str(getattr(
            CurrentAccessContractDomain,
            "DomainIndexFingerprint",
            "",
        )),
        "CachedCompletePortfolioCount": len(
            CompletePortfoliosByContract
        ),
        "CachedOpposingRowContextCount": len(
            OpposingRowContextsByContract
        ),
        "BulkEvaluatedRowCount": len(BulkPairResultsByCompleteContract),
        "BulkAccessSignatureCount": BulkAccessSignatureCount,
        "BulkVariantScanCount": BulkVariantScanCount,
        "BulkSignaturePairCheckCount": BulkSignaturePairCheckCount,
        "PortfolioCompilationSeconds": PortfolioCompilationSeconds,
        "RowContextConstructionSeconds": RowContextConstructionSeconds,
        "BulkAccessPreparationSeconds": BulkAccessPreparationSeconds,
        "BulkVariantScanSeconds": BulkVariantScanSeconds,
        "MultiContractPortfolioComplete": bool(
            MultiPortfolioResult is not None
            and MultiPortfolioResult.Complete
        ),
        "MultiContractCanonicalStateCount": (
            0 if MultiPortfolioResult is None
            else MultiPortfolioResult.CanonicalStateCount
        ),
        "MultiContractNetVariantBuildCount": (
            0 if MultiPortfolioResult is None
            else MultiPortfolioResult.NetVariantBuildCount
        ),
        "MultiContractPortfolioDiagnostics": (
            {} if MultiPortfolioResult is None
            else dict(MultiPortfolioResult.Diagnostics or {})
        ),
    }

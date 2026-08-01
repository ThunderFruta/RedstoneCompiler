"""Guide-independent local access certification for placed components."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Iterable

from .ComponentRouter import (
    BuildComponentEgressPaths,
)
from .Models import (
    ComponentCutAccessFeasibilityCertificate,
    ComponentPerimeterPortCandidate,
    ComponentPortBankDomain,
    ComponentRoutingProblem,
    Position3,
)
from .Reliability import BuildStableFingerprint
from .ResourceGraph import (
    FindSelfClaimConflicts,
    RoutingResourceClaims,
)


def _RelativePath(
    Path: Iterable[Position3],
    Origin: Position3,
) -> tuple[Position3, ...]:
    return tuple(
        tuple(
            Position[Index] - Origin[Index]
            for Index in range(3)
        )
        for Position in Path
    )


def _GraphFingerprint(ResourceGraph: Any) -> str:
    return BuildStableFingerprint((
        getattr(ResourceGraph, "GraphVersion", ""),
        len(getattr(ResourceGraph, "Nodes", ())),
        len(getattr(ResourceGraph, "Edges", ())),
    ))


def _TechnologyFingerprint(ResourceGraph: Any) -> str:
    return BuildStableFingerprint(
        repr(getattr(ResourceGraph, "Technology", None))
    )


def _FabricComponents(
    Problem: ComponentRoutingProblem,
) -> tuple[
    dict[Position3, int],
    dict[int, frozenset[Position3]],
]:
    Adjacency: dict[Position3, set[Position3]] = defaultdict(set)
    for First, Second in Problem.Fabric.Edges:
        Adjacency[tuple(First)].add(tuple(Second))
        Adjacency[tuple(Second)].add(tuple(First))
    ComponentByNode: dict[Position3, int] = {}
    NodesByComponent: dict[int, frozenset[Position3]] = {}
    for Start in sorted(Problem.Fabric.Nodes):
        if Start in ComponentByNode:
            continue
        ComponentIndex = len(NodesByComponent)
        Pending = [tuple(Start)]
        ComponentNodes = {tuple(Start)}
        ComponentByNode[tuple(Start)] = ComponentIndex
        while Pending:
            Current = Pending.pop()
            for Neighbor in sorted(Adjacency.get(Current, ())):
                if Neighbor in ComponentByNode:
                    continue
                ComponentByNode[Neighbor] = ComponentIndex
                ComponentNodes.add(Neighbor)
                Pending.append(Neighbor)
        NodesByComponent[ComponentIndex] = frozenset(ComponentNodes)
    return ComponentByNode, NodesByComponent


def _Envelope(
    Problem: ComponentRoutingProblem,
) -> tuple[Position3, Position3, frozenset[Position3]]:
    Nodes = frozenset((
        *Problem.Fabric.Nodes,
        *(
            Position
            for Claim in Problem.LocalClaims
            for Position in Claim.Nodes
        ),
    ))
    if not Nodes:
        return (0, 0, 0), (0, 0, 0), Nodes
    return (
        tuple(min(Value[Index] for Value in Nodes) for Index in range(3)),
        tuple(max(Value[Index] for Value in Nodes) for Index in range(3)),
        Nodes,
    )


def _ExitsEnvelope(
    Path: tuple[Position3, ...],
    Minimum: Position3,
    Maximum: Position3,
) -> bool:
    if len(Path) < 2:
        return False
    Delta = (
        Path[1][0] - Path[0][0],
        Path[1][2] - Path[0][2],
    )
    Endpoint = Path[-1]
    return bool(
        (Delta == (-1, 0) and Endpoint[0] < Minimum[0])
        or (Delta == (1, 0) and Endpoint[0] > Maximum[0])
        or (Delta == (0, -1) and Endpoint[2] < Minimum[2])
        or (Delta == (0, 1) and Endpoint[2] > Maximum[2])
    )


def _Certificate(
    *,
    Problem: ComponentRoutingProblem,
    ResourceGraph: Any,
    EnvelopeMinimum: Position3,
    EnvelopeMaximum: Position3,
    StemContractFingerprint: str,
    PortDomains: tuple[ComponentPortBankDomain, ...],
    Complete: bool,
    Feasible: bool,
    ProofKind: str,
    AffectedSignals: tuple[str, ...],
    Diagnostics: dict[str, object],
) -> ComponentCutAccessFeasibilityCertificate:
    ComponentGraphFingerprint = str(
        Diagnostics.get("ComponentGraphFingerprint", "")
    )
    ResourceGraphFingerprint = _GraphFingerprint(ResourceGraph)
    TechnologyFingerprint = _TechnologyFingerprint(ResourceGraph)
    StructuralIdentity = (
        TechnologyFingerprint,
        StemContractFingerprint,
        tuple(
            (
                Domain.Direction,
                tuple(
                    Candidate.CandidateFingerprint
                    for Candidate in Domain.Candidates
                ),
                Domain.Complete,
            )
            for Domain in PortDomains
        ),
        Complete,
        Feasible,
        ProofKind,
    )
    StructuralFingerprint = BuildStableFingerprint(StructuralIdentity)
    Identity = (
        Problem.PlacementFingerprint,
        ComponentGraphFingerprint,
        ResourceGraphFingerprint,
        getattr(Problem.Interface, "ComponentId", None),
        StructuralFingerprint,
    )
    return ComponentCutAccessFeasibilityCertificate(
        CertificateFingerprint=BuildStableFingerprint(Identity),
        StructuralFingerprint=StructuralFingerprint,
        PlacementFingerprint=Problem.PlacementFingerprint,
        ComponentGraphFingerprint=ComponentGraphFingerprint,
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        TechnologyFingerprint=TechnologyFingerprint,
        ComponentId=getattr(Problem.Interface, "ComponentId", None),
        EnvelopeMinimum=EnvelopeMinimum,
        EnvelopeMaximum=EnvelopeMaximum,
        BoundedStemContractFingerprint=StemContractFingerprint,
        PortDomains=PortDomains,
        Complete=Complete,
        Feasible=Feasible,
        ProofKind=ProofKind,
        AffectedSignals=AffectedSignals,
        Diagnostics=Diagnostics,
    )


def BuildComponentCutAccessFeasibilityCertificate(
    Problem: ComponentRoutingProblem,
    ResourceGraph: Any,
    *,
    LayerCount: int,
    MinimumPlacementY: int,
    ComponentGraphFingerprint: str = "",
    RequiredLayerBySignal: dict[str, int] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> ComponentCutAccessFeasibilityCertificate:
    """Prove each exported port has a self-legal local access and seam.

    The feasible result retains one deterministic witness per port.  An
    infeasible result is emitted only after the corresponding finite access
    and seam domain has been exhausted.  Joint global-corridor ownership is
    intentionally left to physical assembly planning.
    """
    if Problem.Interface is None or not Problem.Interface.Complete:
        return _Certificate(
            Problem=Problem,
            ResourceGraph=ResourceGraph,
            EnvelopeMinimum=(0, 0, 0),
            EnvelopeMaximum=(0, 0, 0),
            StemContractFingerprint="",
            PortDomains=(),
            Complete=False,
            Feasible=False,
            ProofKind="incomplete-interface",
            AffectedSignals=tuple(Problem.ComponentSignals),
            Diagnostics={
                "ComponentGraphFingerprint": ComponentGraphFingerprint,
                "Reason": "component interface is incomplete",
            },
        )
    EffectiveLayerCount = max(1, int(LayerCount))
    EnvelopeMinimum, EnvelopeMaximum, _KeepoutNodes = _Envelope(Problem)
    Technology = ResourceGraph.Technology
    RequiredLayerBySignal = dict(RequiredLayerBySignal or {})
    StemContractFingerprint = BuildStableFingerprint((
        "component-local-egress-v1",
        EffectiveLayerCount,
        repr(Technology),
        tuple(
            EnvelopeMaximum[Index] - EnvelopeMinimum[Index]
            for Index in range(3)
        ),
        tuple(sorted(RequiredLayerBySignal.items())),
    ))
    FabricOrigin = (
        tuple(
            min(Value[Index] for Value in Problem.Fabric.Nodes)
            for Index in range(3)
        )
        if Problem.Fabric.Nodes
        else (0, 0, 0)
    )
    ComponentByNode, NodesByComponent = _FabricComponents(Problem)
    PortDomains: list[ComponentPortBankDomain] = []
    AccessExpansionCount = 0
    AccessSelfConflictCount = 0
    CompleteAccessTupleCount = 0
    SeamExpansionCount = 0
    SeamsByComponent: dict[
        tuple[int, int | None],
        tuple[tuple[int, Position3, tuple[Position3, ...]], ...],
    ] = {}

    def SeamsForComponent(
        ComponentIndex: int,
        Signal: str,
    ) -> tuple[tuple[int, Position3, tuple[Position3, ...]], ...]:
        nonlocal SeamExpansionCount
        RequiredLayer = RequiredLayerBySignal.get(Signal)
        CacheKey = (ComponentIndex, RequiredLayer)
        Cached = SeamsByComponent.get(CacheKey)
        if Cached is not None:
            return Cached
        Values = []
        Layers = (
            (RequiredLayer,)
            if RequiredLayer is not None
            else range(EffectiveLayerCount)
        )
        for Layer in Layers:
            if Layer < 0 or Layer >= EffectiveLayerCount:
                continue
            TargetY = Technology.RoutingY(MinimumPlacementY, Layer)
            for FabricAttachment in sorted(
                NodesByComponent[ComponentIndex]
            ):
                for LocalPath in BuildComponentEgressPaths(
                    FabricAttachment,
                    TargetY=TargetY,
                ):
                    SeamExpansionCount += 1
                    if (
                        WorkCheck is not None
                        and SeamExpansionCount % 256 == 0
                    ):
                        WorkCheck({
                            "Stage": "component-perimeter-seam",
                            "Signal": Signal,
                            "AccessExpansionCount": (
                                AccessExpansionCount
                            ),
                            "SeamExpansionCount": SeamExpansionCount,
                        })
                    LocalPath = tuple(LocalPath)
                    if not _ExitsEnvelope(
                        LocalPath,
                        EnvelopeMinimum,
                        EnvelopeMaximum,
                    ):
                        continue
                    if any(
                        ResourceGraph.BuildPrimitive(First, Second)
                        is None
                        for First, Second in zip(
                            LocalPath,
                            LocalPath[1:],
                        )
                    ):
                        continue
                    Values.append((
                        Layer,
                        FabricAttachment,
                        LocalPath,
                    ))
        Result = tuple(Values)
        SeamsByComponent[CacheKey] = Result
        return Result

    for PortIndex, Port in enumerate(sorted(
        Problem.Interface.Ports,
        key=lambda Value: Value.Signal,
    )):
        if WorkCheck is not None:
            WorkCheck({
                "Stage": "component-access-certification",
                "PortIndex": PortIndex,
                "PortCount": len(Problem.Interface.Ports),
                "Signal": Port.Signal,
                "AccessExpansionCount": AccessExpansionCount,
                "SeamExpansionCount": SeamExpansionCount,
            })
        Domains = tuple(sorted(
            (
                Domain
                for Domain in Problem.OwnedTerminalDomains
                if Domain.Signal == Port.Signal
            ),
            key=lambda Value: Value.Terminal,
        ))
        if not Domains or any(
            not Domain.Complete or not Domain.Candidates
            for Domain in Domains
        ):
            return _Certificate(
                Problem=Problem,
                ResourceGraph=ResourceGraph,
                EnvelopeMinimum=EnvelopeMinimum,
                EnvelopeMaximum=EnvelopeMaximum,
                StemContractFingerprint=StemContractFingerprint,
                PortDomains=tuple(PortDomains),
                Complete=all(Domain.Complete for Domain in Domains),
                Feasible=False,
                ProofKind=(
                    "terminal-access-empty"
                    if Domains and all(Domain.Complete for Domain in Domains)
                    else "terminal-access-incomplete"
                ),
                AffectedSignals=(Port.Signal,),
                Diagnostics={
                    "ComponentGraphFingerprint": ComponentGraphFingerprint,
                    "Signal": Port.Signal,
                    "TerminalDomainCount": len(Domains),
                    "EmptyTerminalCount": sum(
                        not Domain.Candidates for Domain in Domains
                    ),
                },
            )
        CandidateComponentsByDomain = tuple(
            frozenset(
                ComponentByNode[Candidate.Attachment]
                for Candidate in Domain.Candidates
                if Candidate.Attachment in ComponentByNode
            )
            for Domain in Domains
        )
        CommonComponents = (
            set.intersection(*map(set, CandidateComponentsByDomain))
            if CandidateComponentsByDomain
            else set()
        )
        Witnesses: list[ComponentPerimeterPortCandidate] = []
        WitnessFingerprints: set[str] = set()
        for ComponentIndex in sorted(CommonComponents):
            CandidateDomains = tuple(
                tuple(
                    Candidate
                    for Candidate in Domain.Candidates
                    if ComponentByNode.get(Candidate.Attachment)
                    == ComponentIndex
                    and not FindSelfClaimConflicts({
                        Port.Signal: Candidate.Claims,
                    })
                )
                for Domain in Domains
            )
            AccessExpansionCount += sum(map(len, CandidateDomains))
            if any(not Values for Values in CandidateDomains):
                AccessSelfConflictCount += sum(
                    not Values for Values in CandidateDomains
                )
                continue
            CompleteAccessTupleCount += 1
            ComponentNodes = NodesByComponent[ComponentIndex]
            FabricDomainFingerprint = BuildStableFingerprint(tuple(sorted(
                _RelativePath(ComponentNodes, FabricOrigin)
            )))
            for Layer, FabricAttachment, LocalPath in (
                SeamsForComponent(ComponentIndex, Port.Signal)
            ):
                Claims = ResourceGraph.BuildRouteClaims(
                    frozenset(LocalPath)
                )
                if FindSelfClaimConflicts({Port.Signal: Claims}):
                    continue
                CandidateFingerprint = BuildStableFingerprint((
                    Port.Direction,
                    FabricDomainFingerprint,
                    _RelativePath(LocalPath, FabricOrigin),
                    Layer,
                    Port.Capacity,
                ))
                if CandidateFingerprint in WitnessFingerprints:
                    continue
                WitnessFingerprints.add(CandidateFingerprint)
                Witnesses.append(ComponentPerimeterPortCandidate(
                    CandidateFingerprint=CandidateFingerprint,
                    Signal=Port.Signal,
                    Direction=Port.Direction,
                    FabricDomainFingerprint=FabricDomainFingerprint,
                    OwnedTerminals=tuple(
                        Domain.Terminal for Domain in Domains
                    ),
                    OwnedCandidateFingerprints=(),
                    FabricAttachment=FabricAttachment,
                    Attachment=LocalPath[-1],
                    LocalPath=LocalPath,
                    Claims=Claims,
                    Layer=Layer,
                    Capacity=Port.Capacity,
                ))
        if not Witnesses:
            ProofKind = (
                "terminal-access-self-conflict"
                if CompleteAccessTupleCount == 0
                else "perimeter-seam-empty"
            )
            return _Certificate(
                Problem=Problem,
                ResourceGraph=ResourceGraph,
                EnvelopeMinimum=EnvelopeMinimum,
                EnvelopeMaximum=EnvelopeMaximum,
                StemContractFingerprint=StemContractFingerprint,
                PortDomains=tuple(PortDomains),
                Complete=True,
                Feasible=False,
                ProofKind=ProofKind,
                AffectedSignals=(Port.Signal,),
                Diagnostics={
                    "ComponentGraphFingerprint": ComponentGraphFingerprint,
                    "Signal": Port.Signal,
                    "AccessExpansionCount": AccessExpansionCount,
                    "AccessSelfConflictCount": AccessSelfConflictCount,
                    "CompleteAccessTupleCount": CompleteAccessTupleCount,
                    "SeamExpansionCount": SeamExpansionCount,
                    "PoweredSubtreeChecksDeferred": True,
                    "ImplicitForeignTransitDomainCount": 0,
                },
            )
        PortDomains.append(ComponentPortBankDomain(
            Signal=Port.Signal,
            Direction=Port.Direction,
            Candidates=tuple(Witnesses),
            Complete=True,
        ))
    return _Certificate(
        Problem=Problem,
        ResourceGraph=ResourceGraph,
        EnvelopeMinimum=EnvelopeMinimum,
        EnvelopeMaximum=EnvelopeMaximum,
        StemContractFingerprint=StemContractFingerprint,
        PortDomains=tuple(PortDomains),
        Complete=True,
        Feasible=True,
        ProofKind="factorized-local-access-domain",
        AffectedSignals=(),
        Diagnostics={
            "ComponentGraphFingerprint": ComponentGraphFingerprint,
            "AccessExpansionCount": AccessExpansionCount,
            "AccessSelfConflictCount": AccessSelfConflictCount,
            "CompleteAccessTupleCount": CompleteAccessTupleCount,
            "SeamExpansionCount": SeamExpansionCount,
            "PoweredSubtreeChecksDeferred": True,
            "FactorizedPortDomains": True,
            "JointPortBankCapacityDeferred": True,
            "ImplicitForeignTransitDomainCount": 0,
        },
    )


def ValidateComponentAccessCertificateIdentity(
    Certificate: ComponentCutAccessFeasibilityCertificate,
    Problem: ComponentRoutingProblem,
    ResourceGraph: Any,
    *,
    ComponentGraphFingerprint: str,
) -> None:
    """Reject stale certificates before physical assembly consumes them."""
    Expected = {
        "PlacementFingerprint": Problem.PlacementFingerprint,
        "ComponentGraphFingerprint": ComponentGraphFingerprint,
        "ResourceGraphFingerprint": _GraphFingerprint(ResourceGraph),
        "TechnologyFingerprint": _TechnologyFingerprint(ResourceGraph),
        "ComponentId": getattr(Problem.Interface, "ComponentId", None),
    }
    Actual = {
        "PlacementFingerprint": Certificate.PlacementFingerprint,
        "ComponentGraphFingerprint": (
            Certificate.ComponentGraphFingerprint
        ),
        "ResourceGraphFingerprint": Certificate.ResourceGraphFingerprint,
        "TechnologyFingerprint": Certificate.TechnologyFingerprint,
        "ComponentId": Certificate.ComponentId,
    }
    if Actual != Expected:
        raise ValueError(
            "component access certificate identity mismatch: "
            f"expected {Expected}, got {Actual}"
        )

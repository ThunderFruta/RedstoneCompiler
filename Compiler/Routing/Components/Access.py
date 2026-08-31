"""Guide-independent local access certification for placed components."""

from __future__ import annotations

from collections import defaultdict
from itertools import chain
from typing import Any, Callable, Iterable, Mapping

from .Fabric import (
    BuildComponentEgressPaths,
    SelectGuideFacingComponentEgressDirections,
)
from ..Contracts.Component import (
    ComponentCutAccessFeasibilityCertificate,
    ComponentPerimeterPortCandidate,
    ComponentPortBankDomain,
    ComponentRoutingProblem,
)
from ..Contracts.Core import Position2, Position3
from ..Reliability import BuildStableFingerprint
from ..ResourceGraph import (
    FindSelfClaimConflicts,
    RoutingResourceClaims,
)
from ..Technology import DefaultRedstoneRoutingTechnology

try:
    from ...RustRouting import (
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
    )
    from ...RustRouting import GetRoutingThreadCount as _GetRoutingThreadCount
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import (
            BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
        )
        from RedstoneCompiler.RustRouting import (
            GetRoutingThreadCount as _GetRoutingThreadCount,
        )
    except ImportError:
        _BuildRouteClaimsBatchWithTelemetry = None
        _GetRoutingThreadCount = None


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


def BuildComponentAccessGuideTargetColumns(
    Problem: ComponentRoutingProblem,
    GuideCellsBySignal: Mapping[str, Iterable[Position2]],
) -> dict[str, frozenset[Position2]]:
    """Conjoin coarse guides with authoritative external continuation targets."""
    TargetsBySignal = {
        str(Signal): set(map(tuple, Cells))
        for Signal, Cells in GuideCellsBySignal.items()
    }
    for Signal, Terminal, _Role in Problem.ExternalContinuationTerminals:
        TargetsBySignal.setdefault(str(Signal), set()).add((
            int(Terminal[0]),
            int(Terminal[2]),
        ))
    return {
        Signal: frozenset(Targets)
        for Signal, Targets in sorted(TargetsBySignal.items())
    }


def SelectStraightContinuationEgressDirections(
    OwnedTerminals: Iterable[Position3],
    ExternalTerminals: Iterable[Position3],
) -> tuple[Position2, ...]:
    """Point an enclosed component seam toward its external continuation."""
    Owned = tuple(map(tuple, OwnedTerminals))
    External = tuple(map(tuple, ExternalTerminals))
    if not Owned or not External:
        return ()
    OriginX = sum(Position[0] for Position in Owned) / len(Owned)
    OriginZ = sum(Position[2] for Position in Owned) / len(Owned)
    Directions: set[Position2] = set()
    for Terminal in External:
        DeltaX = Terminal[0] - OriginX
        DeltaZ = Terminal[2] - OriginZ
        if DeltaX and abs(DeltaX) >= abs(DeltaZ):
            Directions.add((1 if DeltaX > 0 else -1, 0))
        if DeltaZ and abs(DeltaZ) >= abs(DeltaX):
            Directions.add((0, 1 if DeltaZ > 0 else -1))
    return tuple(
        Direction
        for Direction in ((-1, 0), (0, -1), (0, 1), (1, 0))
        if Direction in Directions
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
) -> tuple[Position3, Position3]:
    Positions = chain(
        Problem.Fabric.Nodes,
        (
            Position
            for Claim in Problem.LocalClaims
            for Position in Claim.Nodes
        ),
    )
    First = next(Positions, None)
    if First is None:
        return (0, 0, 0), (0, 0, 0)
    Minimum = list(First)
    Maximum = list(First)
    for Position in Positions:
        for Index in range(3):
            Minimum[Index] = min(Minimum[Index], Position[Index])
            Maximum[Index] = max(Maximum[Index], Position[Index])
    return tuple(Minimum), tuple(Maximum)


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
    RequiredGuideCellsBySignal: Mapping[
        str,
        Iterable[Position2],
    ] | None = None,
    PrioritySignals: Iterable[str] = (),
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
    EnvelopeMinimum, EnvelopeMaximum = _Envelope(Problem)
    if WorkCheck is not None:
        WorkCheck({
            "Stage": "component-access-envelope",
            "FabricNodeCount": len(Problem.Fabric.Nodes),
            "LocalClaimCount": len(Problem.LocalClaims),
        })
    Technology = ResourceGraph.Technology
    RequiredLayerBySignal = dict(RequiredLayerBySignal or {})
    RequiredGuideCells = (
        {
            str(Signal): frozenset(tuple(Cell) for Cell in Cells)
            for Signal, Cells in RequiredGuideCellsBySignal.items()
        }
        if RequiredGuideCellsBySignal is not None
        else None
    )
    GuideFacingDirectionsBySignal: dict[
        str,
        tuple[Position2, ...],
    ] = {}
    ExteriorGuideTargetCountBySignal: dict[str, int] = {}
    StraightContinuationDirectionsBySignal: dict[
        str,
        tuple[Position2, ...],
    ] = {}
    if RequiredGuideCells is not None:
        for Port in Problem.Interface.Ports:
            GuideCells = RequiredGuideCells.get(Port.Signal, frozenset())
            ExteriorGuideTargetCountBySignal[Port.Signal] = sum(
                1
                for X, Z in GuideCells
                if (
                    X < EnvelopeMinimum[0]
                    or X > EnvelopeMaximum[0]
                    or Z < EnvelopeMinimum[2]
                    or Z > EnvelopeMaximum[2]
                )
            )
            GuideFacingDirections = (
                SelectGuideFacingComponentEgressDirections(
                    EnvelopeMinimum,
                    EnvelopeMaximum,
                    GuideCells,
                )
            )
            StraightContinuationDirections = (
                SelectStraightContinuationEgressDirections(
                    Port.OwnedTerminals,
                    (
                        Terminal
                        for Signal, Terminal, _Role
                        in Problem.ExternalContinuationTerminals
                        if Signal == Port.Signal
                    ),
                )
                if not GuideFacingDirections
                else ()
            )
            StraightContinuationDirectionsBySignal[Port.Signal] = (
                StraightContinuationDirections
            )
            GuideFacingDirectionsBySignal[Port.Signal] = (
                GuideFacingDirections
                or StraightContinuationDirections
            )
    PrioritySignalNames = frozenset(map(str, PrioritySignals))
    StemContractFingerprint = BuildStableFingerprint((
        "component-local-egress-v1",
        EffectiveLayerCount,
        repr(Technology),
        tuple(
            EnvelopeMaximum[Index] - EnvelopeMinimum[Index]
            for Index in range(3)
        ),
        tuple(sorted(RequiredLayerBySignal.items())),
        (
            tuple(
                (Signal, tuple(sorted(Cells)))
                for Signal, Cells in sorted(RequiredGuideCells.items())
            )
            if RequiredGuideCells is not None
            else None
        ),
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
    if RequiredGuideCells is not None:
        for Port in Problem.Interface.Ports:
            ComponentGuideFacingDirections = tuple(sorted({
                Direction
                for ComponentNodes in NodesByComponent.values()
                for Direction in SelectGuideFacingComponentEgressDirections(
                    tuple(
                        min(Node[Index] for Node in ComponentNodes)
                        for Index in range(3)
                    ),
                    tuple(
                        max(Node[Index] for Node in ComponentNodes)
                        for Index in range(3)
                    ),
                    RequiredGuideCells.get(Port.Signal, frozenset()),
                )
            }))
            GuideFacingDirectionsBySignal[Port.Signal] = (
                ComponentGuideFacingDirections
                or StraightContinuationDirectionsBySignal.get(
                    Port.Signal,
                    (),
                )
            )
    if WorkCheck is not None:
        WorkCheck({
            "Stage": "component-access-fabric-components",
            "FabricNodeCount": len(Problem.Fabric.Nodes),
            "FabricEdgeCount": len(Problem.Fabric.Edges),
            "FabricComponentCount": len(NodesByComponent),
        })
    PortDomains: list[ComponentPortBankDomain] = []
    AccessExpansionCount = 0
    AccessSelfConflictCount = 0
    CompleteAccessTupleCount = 0
    SeamExpansionCount = 0
    SeamsByComponent: dict[
        tuple[int, int | None, tuple[Position2, ...] | None],
        tuple[
            tuple[
                int,
                Position3,
                tuple[Position3, ...],
                RoutingResourceClaims,
            ], ...
        ],
    ] = {}
    SeamDiagnosticsByComponent: dict[
        tuple[int, int | None, tuple[Position2, ...] | None],
        dict[str, object],
    ] = {}
    FabricFingerprintByComponent: dict[int, str] = {}
    RelativePathByLocalPath: dict[
        tuple[Position3, ...],
        tuple[Position3, ...],
    ] = {}
    CandidateFingerprintByIdentity: dict[tuple[object, ...], str] = {}
    NativeClaimBatchCount = 0
    NativeClaimBatchWorkItems = 0
    NativeClaimBatchActiveWorkerCount = 0

    def BuildSeamClaims(
        Paths: tuple[tuple[Position3, ...], ...],
    ) -> tuple[RoutingResourceClaims, ...]:
        """Build one immutable claim batch without changing seam membership."""
        nonlocal NativeClaimBatchCount
        nonlocal NativeClaimBatchWorkItems
        nonlocal NativeClaimBatchActiveWorkerCount
        NativeCompatible = bool(
            _BuildRouteClaimsBatchWithTelemetry is not None
            and ResourceGraph.Technology
            == DefaultRedstoneRoutingTechnology
            and hasattr(ResourceGraph, "ActualBlocks")
            and hasattr(ResourceGraph, "SolidBlocks")
        )
        if NativeCompatible and len(Paths) > 1:
            NativeClaims, ActiveWorkerCount = (
                _BuildRouteClaimsBatchWithTelemetry(
                    [tuple(sorted(Path)) for Path in Paths],
                    tuple(sorted(ResourceGraph.ActualBlocks)),
                    tuple(sorted(ResourceGraph.SolidBlocks)),
                )
            )
            NativeClaimBatchCount += 1
            NativeClaimBatchWorkItems += len(Paths)
            NativeClaimBatchActiveWorkerCount = max(
                NativeClaimBatchActiveWorkerCount,
                int(ActiveWorkerCount),
            )
            return tuple(
                RoutingResourceClaims(
                    WireCells=frozenset(Wire),
                    SupportCells=frozenset(Support),
                    RequiredAirCells=frozenset(Air),
                    ElectricalCells=frozenset(Electrical),
                )
                for Wire, Support, Air, Electrical in NativeClaims
            )
        return tuple(
            ResourceGraph.BuildRouteClaims(frozenset(Path))
            for Path in Paths
        )

    def SeamsForComponent(
        ComponentIndex: int,
        Signal: str,
    ) -> tuple[
        tuple[
            int,
            Position3,
            tuple[Position3, ...],
            RoutingResourceClaims,
        ], ...
    ]:
        nonlocal SeamExpansionCount
        RequiredLayer = RequiredLayerBySignal.get(Signal)
        ComponentNodes = NodesByComponent[ComponentIndex]
        ComponentEnvelopeMinimum = tuple(
            min(Node[Index] for Node in ComponentNodes)
            for Index in range(3)
        )
        ComponentEnvelopeMaximum = tuple(
            max(Node[Index] for Node in ComponentNodes)
            for Index in range(3)
        )
        GuideFacingDirections = (
            SelectGuideFacingComponentEgressDirections(
                ComponentEnvelopeMinimum,
                ComponentEnvelopeMaximum,
                RequiredGuideCells.get(Signal, frozenset()),
            )
            if RequiredGuideCells is not None
            else None
        )
        if GuideFacingDirections == ():
            GuideFacingDirections = (
                StraightContinuationDirectionsBySignal.get(Signal, ())
            )
        CacheKey = (
            ComponentIndex,
            RequiredLayer,
            GuideFacingDirections,
        )
        Cached = SeamsByComponent.get(CacheKey)
        if Cached is not None:
            return Cached
        Values = []
        GeneratedDirectionCounts: dict[str, int] = defaultdict(int)
        ExitingDirectionCounts: dict[str, int] = defaultdict(int)
        IllegalPrimitiveDirectionCounts: dict[str, int] = defaultdict(int)
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
                    EnvelopeMinimum=ComponentEnvelopeMinimum,
                    EnvelopeMaximum=ComponentEnvelopeMaximum,
                    Directions=GuideFacingDirections,
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
                    Direction = tuple(
                        LocalPath[1][Index] - LocalPath[0][Index]
                        for Index in range(3)
                    )
                    DirectionKey = ",".join(map(str, Direction))
                    GeneratedDirectionCounts[DirectionKey] += 1
                    if not _ExitsEnvelope(
                        LocalPath,
                        ComponentEnvelopeMinimum,
                        ComponentEnvelopeMaximum,
                    ):
                        continue
                    ExitingDirectionCounts[DirectionKey] += 1
                    if any(
                        ResourceGraph.BuildPrimitive(First, Second)
                        is None
                        for First, Second in zip(
                            LocalPath,
                            LocalPath[1:],
                        )
                    ):
                        IllegalPrimitiveDirectionCounts[DirectionKey] += 1
                        continue
                    Values.append((
                        Layer,
                        FabricAttachment,
                        LocalPath,
                    ))
        Paths = tuple(Value[2] for Value in Values)
        Claims = BuildSeamClaims(Paths)
        ResultValues = []
        SelfConflictDirectionCounts: dict[str, int] = defaultdict(int)
        LegalDirectionCounts: dict[str, int] = defaultdict(int)
        for Value, Claim in zip(Values, Claims, strict=True):
            LocalPath = Value[2]
            Direction = tuple(
                LocalPath[1][Index] - LocalPath[0][Index]
                for Index in range(3)
            )
            DirectionKey = ",".join(map(str, Direction))
            if FindSelfClaimConflicts({Signal: Claim}):
                SelfConflictDirectionCounts[DirectionKey] += 1
                continue
            LegalDirectionCounts[DirectionKey] += 1
            ResultValues.append((*Value, Claim))
        Result = tuple(ResultValues)
        SeamDiagnosticsByComponent[CacheKey] = {
            "ComponentIndex": ComponentIndex,
            "RequiredLayer": RequiredLayer,
            "GuideFacingDirections": (
                [list(Direction) for Direction in GuideFacingDirections]
                if GuideFacingDirections is not None
                else None
            ),
            "FabricNodeCount": len(ComponentNodes),
            "FabricMinimum": list(ComponentEnvelopeMinimum),
            "FabricMaximum": list(ComponentEnvelopeMaximum),
            "EgressEnvelopeScope": "connected-fabric-component",
            "GeneratedPathCount": sum(GeneratedDirectionCounts.values()),
            "EnvelopeExitingPathCount": sum(ExitingDirectionCounts.values()),
            "IllegalPrimitivePathCount": sum(
                IllegalPrimitiveDirectionCounts.values()
            ),
            "SelfConflictPathCount": sum(
                SelfConflictDirectionCounts.values()
            ),
            "LegalSeamCount": len(Result),
            "GeneratedDirectionCounts": dict(sorted(
                GeneratedDirectionCounts.items()
            )),
            "EnvelopeExitingDirectionCounts": dict(sorted(
                ExitingDirectionCounts.items()
            )),
            "IllegalPrimitiveDirectionCounts": dict(sorted(
                IllegalPrimitiveDirectionCounts.items()
            )),
            "SelfConflictDirectionCounts": dict(sorted(
                SelfConflictDirectionCounts.items()
            )),
            "LegalDirectionCounts": dict(sorted(
                LegalDirectionCounts.items()
            )),
        }
        SeamsByComponent[CacheKey] = Result
        return Result

    for PortIndex, Port in enumerate(sorted(
        Problem.Interface.Ports,
        key=lambda Value: (
            0 if Value.Signal in PrioritySignalNames else 1,
            Value.Signal,
        ),
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
        if RequiredGuideCells is not None:
            GuideCells = RequiredGuideCells.get(Port.Signal, frozenset())
            StraightContinuationDirections = (
                StraightContinuationDirectionsBySignal.get(
                    Port.Signal,
                    (),
                )
            )
            if not GuideFacingDirectionsBySignal.get(Port.Signal, ()):
                return _Certificate(
                    Problem=Problem,
                    ResourceGraph=ResourceGraph,
                    EnvelopeMinimum=EnvelopeMinimum,
                    EnvelopeMaximum=EnvelopeMaximum,
                    StemContractFingerprint=StemContractFingerprint,
                    PortDomains=tuple(PortDomains),
                    Complete=True,
                    Feasible=False,
                    ProofKind=(
                        "perimeter-seam-exterior-guide-target-empty"
                    ),
                    AffectedSignals=(Port.Signal,),
                    Diagnostics={
                        "ComponentGraphFingerprint": (
                            ComponentGraphFingerprint
                        ),
                        "Signal": Port.Signal,
                        "GuideCellCount": len(GuideCells),
                        "ExteriorGuideTargetCount": (
                            ExteriorGuideTargetCountBySignal.get(
                                Port.Signal,
                                0,
                            )
                        ),
                        "StraightContinuationDirections": [],
                        "StraightContinuationContractApplied": False,
                        "EnvelopeMinimum": list(EnvelopeMinimum),
                        "EnvelopeMaximum": list(EnvelopeMaximum),
                        "PrioritySignal": (
                            Port.Signal in PrioritySignalNames
                        ),
                        "GuideDomainComplete": True,
                    },
                )
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
        PortCompleteAccessTupleCount = 0
        PortAccessSelfConflictCount = 0
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
                RejectedDomainCount = sum(
                    not Values for Values in CandidateDomains
                )
                PortAccessSelfConflictCount += RejectedDomainCount
                AccessSelfConflictCount += RejectedDomainCount
                continue
            PortCompleteAccessTupleCount += 1
            CompleteAccessTupleCount += 1
            ComponentNodes = NodesByComponent[ComponentIndex]
            FabricDomainFingerprint = FabricFingerprintByComponent.get(
                ComponentIndex
            )
            if FabricDomainFingerprint is None:
                FabricDomainFingerprint = BuildStableFingerprint(tuple(sorted(
                    _RelativePath(ComponentNodes, FabricOrigin)
                )))
                FabricFingerprintByComponent[ComponentIndex] = (
                    FabricDomainFingerprint
                )
            for Layer, FabricAttachment, LocalPath, Claims in (
                SeamsForComponent(ComponentIndex, Port.Signal)
            ):
                RelativeLocalPath = RelativePathByLocalPath.get(LocalPath)
                if RelativeLocalPath is None:
                    RelativeLocalPath = _RelativePath(
                        LocalPath,
                        FabricOrigin,
                    )
                    RelativePathByLocalPath[LocalPath] = RelativeLocalPath
                CandidateIdentity = (
                    Port.Direction,
                    FabricDomainFingerprint,
                    RelativeLocalPath,
                    Layer,
                    Port.Capacity,
                )
                CandidateFingerprint = CandidateFingerprintByIdentity.get(
                    CandidateIdentity
                )
                if CandidateFingerprint is None:
                    CandidateFingerprint = BuildStableFingerprint(
                        CandidateIdentity
                    )
                    CandidateFingerprintByIdentity[CandidateIdentity] = (
                        CandidateFingerprint
                    )
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
            FailureGuideFacingDirections = (
                SelectGuideFacingComponentEgressDirections(
                    EnvelopeMinimum,
                    EnvelopeMaximum,
                    RequiredGuideCells.get(Port.Signal, frozenset()),
                )
                if RequiredGuideCells is not None
                else None
            )
            ProofKind = (
                "terminal-access-fabric-component-disconnected"
                if not CommonComponents
                else (
                    "terminal-access-self-conflict"
                    if PortCompleteAccessTupleCount == 0
                    else "perimeter-seam-empty"
                )
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
                    "PortAccessSelfConflictCount": (
                        PortAccessSelfConflictCount
                    ),
                    "PortCompleteAccessTupleCount": (
                        PortCompleteAccessTupleCount
                    ),
                    "SeamExpansionCount": SeamExpansionCount,
                    "OwnedTerminals": [
                        list(Domain.Terminal) for Domain in Domains
                    ],
                    "TerminalCandidateCounts": [
                        len(Domain.Candidates) for Domain in Domains
                    ],
                    "TerminalCandidateComponents": [
                        sorted(Components)
                        for Components in CandidateComponentsByDomain
                    ],
                    "CommonFabricComponents": sorted(CommonComponents),
                    "PerimeterSeamDiagnostics": [
                        SeamDiagnosticsByComponent[
                            (
                                ComponentIndex,
                                RequiredLayerBySignal.get(Port.Signal),
                                FailureGuideFacingDirections,
                            )
                        ]
                        for ComponentIndex in sorted(CommonComponents)
                        if (
                            ComponentIndex,
                            RequiredLayerBySignal.get(Port.Signal),
                            FailureGuideFacingDirections,
                        ) in SeamDiagnosticsByComponent
                    ],
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
        PortDomains=tuple(sorted(
            PortDomains,
            key=lambda Domain: Domain.Signal,
        )),
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
            "NativeClaimBatchCount": NativeClaimBatchCount,
            "NativeClaimBatchWorkItems": NativeClaimBatchWorkItems,
            "NativeClaimBatchWorkerCount": (
                int(_GetRoutingThreadCount())
                if _GetRoutingThreadCount is not None
                else 0
            ),
            "NativeClaimBatchActiveWorkerCount": (
                NativeClaimBatchActiveWorkerCount
            ),
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

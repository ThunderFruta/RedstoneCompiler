"""Dynamic-programming component-routing search."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import combinations, islice, product
from math import prod as ProductIntegers
from time import monotonic
from typing import Any, Callable, Iterable, Mapping


from ....Contracts.Component import ClosedComponentInterface, ComponentFeedthroughContract, ComponentForeignTransitDomain, ComponentInterfacePort, ComponentRoutingFabric, ComponentRoutingProblem, ComponentRoutingSolveResult, ComponentTerminalAccessCandidate, ComponentTerminalAccessDomain, RoutedComponentNet, RoutedComponentTemplate
from ....Contracts.Core import Position3
from ....Interfaces.PhysicalClaims import _MergeClaims, ComponentClaimsCompatibleForOwners, ComponentClaimsConflict
from ....Resources.ResourceGraph import FindSelfClaimConflicts, LocalRouteClaim, PinAccessPortal, RoutingEdge, RoutingReservation, RoutingResourceId, RoutingResourceKind, RoutingResourceClaims
from ....Redstone.Technology import DefaultRedstoneRoutingTechnology

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

from ..Core import _ClaimsFingerprint, _NormalizedEdge, _RelativeGeometry, _StableFingerprint
from ..Interfaces.Fabric import BuildClaimsAwareComponentFabricSubtree, BuildComponentEgressPaths, _BuildAdjacency, _PlanTreeRepeaters, _UniqueFabricSubtree
from ..Symbolic.SymbolicState import BuildComponentSymbolicNetStateCacheKey, ComponentTreeDpNetState, PreparedComponentSymbolicNetStateContext, PreparedComponentSymbolicTerminalFrontier, SelectComponentSymbolicPhysicalPort, _BuildPreparedComponentSymbolicNetStateContextFingerprint
def SolveComponentRoutingProblemDynamic(Problem: ComponentRoutingProblem, *, DeadlineSeconds: float | None=None, WorkCheck: Callable[[dict[str, object]], None] | None=None, ForbiddenAssignmentFingerprints: frozenset[str]=frozenset(), ForbiddenExportPortsBySignal: dict[str, tuple[Position3, ...]] | None=None, ForbiddenForeignCandidateFingerprintsBySignal: dict[str, frozenset[str]] | None=None, ForbiddenForeignAssignmentPairs: tuple[frozenset[tuple[str, Position3, str]], ...]=(), RequiredForeignTransitSignals: frozenset[str]=frozenset(), RouteClaimsConstructionCache: dict[frozenset[Position3], RoutingResourceClaims] | None=None, SymbolicNetStateCache: dict[str, Any] | None=None, RequestedSymbolicStateSignals: frozenset[str] | None=None, PreparedSymbolicNetStateContext: PreparedComponentSymbolicNetStateContext | None=None, PreparedPhysicalPortVariants: tuple[Any, ...]=(), StopAfterOwnedSignalFrontierProof: bool=False, StopAfterSymbolicCapacityProof: bool=False) -> ComponentRoutingSolveResult:
    """Solve a complete tree fabric through canonical frontier states.

    Access domains are folded one terminal at a time.  Because the component
    fabric is a forest, every attachment set has one unique connecting
    subtree.  States with the same covered terminals, component, nodes, and
    edges are therefore equivalent and may be merged before any complete
    routed-net object is materialized.
    """
    Started = monotonic()
    ForbiddenExportPortsBySignal = ForbiddenExportPortsBySignal or {}
    ForbiddenForeignCandidateFingerprintsBySignal = ForbiddenForeignCandidateFingerprintsBySignal or {}
    RouteClaimsCache = PreparedSymbolicNetStateContext.RouteClaimsConstructionCache if PreparedSymbolicNetStateContext is not None else RouteClaimsConstructionCache if RouteClaimsConstructionCache is not None else {}
    NetStateCache = SymbolicNetStateCache if SymbolicNetStateCache is not None else {}
    ExpansionCount = 0
    ExploredStateCount = 0
    PeakFrontierStateCount = 0
    DominatedStateCount = 0
    IncrementalPhysicalEgressMaterializationCount = 0
    HitLimit = False
    SignalDiagnostics: dict[str, dict[str, object]] = {}
    SolverDiagnostics: dict[str, object] = {'SolverKind': 'tree-frontier-dp-v1', 'ProblemFingerprint': Problem.ProblemFingerprint, 'FabricFingerprint': Problem.Fabric.FabricFingerprint, 'FabricTopologyKind': Problem.Fabric.TopologyKind, 'FabricNodeCount': len(Problem.Fabric.Nodes), 'FabricEdgeCount': len(Problem.Fabric.Edges), 'ComponentSignalCount': len(Problem.ComponentSignals), 'OwnedTerminalDomainCount': len(Problem.OwnedTerminalDomains), 'CompleteTreesMaterialized': 0, 'SelectedTreesMaterialized': 0, 'SymbolicNetStateCacheHitCount': 0, 'SymbolicNetStateCacheStoreCount': 0}

    def Advance(Phase: str) -> bool:
        nonlocal ExpansionCount, ExploredStateCount, HitLimit
        ExpansionCount += 1
        ExploredStateCount += 1
        if WorkCheck is not None and ExpansionCount % 128 == 0:
            WorkCheck({'Phase': Phase, 'SolverKind': 'tree-frontier-dp-v1', 'ExpansionCount': ExpansionCount, 'ExploredStateCount': ExploredStateCount, 'PeakFrontierStateCount': PeakFrontierStateCount, 'DominatedStateCount': DominatedStateCount, 'CompleteTreesMaterialized': 0})
        HitLimit = bool(ExpansionCount > Problem.MaximumWork or (DeadlineSeconds is not None and monotonic() - Started >= DeadlineSeconds))
        return not HitLimit

    def FinishDiagnostics() -> dict[str, object]:
        SolverDiagnostics.update({'ExpansionCount': ExpansionCount, 'ExploredStateCount': ExploredStateCount, 'PeakFrontierStateCount': PeakFrontierStateCount, 'DominatedStateCount': DominatedStateCount, 'IncrementalPhysicalEgressMaterializationCount': IncrementalPhysicalEgressMaterializationCount, 'CompleteTreesMaterialized': 0, 'SignalDiagnostics': SignalDiagnostics, 'RuntimeSeconds': monotonic() - Started})
        return SolverDiagnostics
    DeclaredFeedthroughSignals = Problem.Interface.DeclaredFeedthroughSignals if Problem.Interface is not None else frozenset()
    ForeignTransitSignals = frozenset((Domain.Signal for Domain in Problem.ForeignTransitDomains))
    ImplicitForeignTransitSignals = tuple(sorted(ForeignTransitSignals - DeclaredFeedthroughSignals if Problem.Interface is not None else ()))
    SolverDiagnostics['ImplicitForeignTransitDomainCount'] = len(ImplicitForeignTransitSignals)
    SolverDiagnostics['ImplicitForeignTransitSignals'] = list(ImplicitForeignTransitSignals)
    if ImplicitForeignTransitSignals or not RequiredForeignTransitSignals <= DeclaredFeedthroughSignals:
        return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'undeclared-foreign-transit', ImplicitForeignTransitSignals, tuple(sorted(RequiredForeignTransitSignals)))), Detail='closed component contains undeclared foreign transit', Diagnostics=FinishDiagnostics())
    if Problem.Interface is not None and (not Problem.Interface.Complete):
        return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'incomplete-closed-interface')), Detail='closed component interface is incomplete', Diagnostics=FinishDiagnostics())
    if not Problem.Fabric.Complete:
        return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, Problem.Fabric.IncompleteReason)), Detail=Problem.Fabric.IncompleteReason, Diagnostics=FinishDiagnostics())
    if not Problem.DomainComplete:
        return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'incomplete-domain')), Detail='one or more terminal domains are incomplete or empty', Diagnostics=FinishDiagnostics())
    if PreparedSymbolicNetStateContext is not None:
        PreparedSignal = PreparedSymbolicNetStateContext.Signal
        if _BuildPreparedComponentSymbolicNetStateContextFingerprint(Problem, PreparedSignal) != PreparedSymbolicNetStateContext.ContextFingerprint:
            raise ValueError('prepared symbolic net-state context identity mismatch')
        FabricAdjacency = PreparedSymbolicNetStateContext.FabricAdjacency
        FabricParentCache = PreparedSymbolicNetStateContext.FabricParentCache
        FabricComponentByNode = PreparedSymbolicNetStateContext.FabricComponentByNode
        LocalClaimsBySignal = {PreparedSignal: PreparedSymbolicNetStateContext.LocalClaims}
        ImmutableClaimsBySignal = PreparedSymbolicNetStateContext.ImmutableClaimsBySignal
        DomainsBySignal = {PreparedSignal: PreparedSymbolicNetStateContext.Domains}
    else:
        FabricAdjacency = _BuildAdjacency(Problem.Fabric.Edges)
        FabricParentCache: dict[Position3, dict[Position3, Position3 | None]] = {}
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
        LocalClaimsBySignal = {Signal: tuple((Claim for Claim in Problem.LocalClaims if Claim.Signal == Signal)) for Signal in Problem.ComponentSignals}
        ImmutableClaimsBySignal = tuple(((str(Claim.Signal), Claim.Claims) for Claim in Problem.ImmutableClaims if Claim.Signal not in Problem.ComponentSignals))
        DomainsBySignal = {Signal: tuple((Domain for Domain in Problem.OwnedTerminalDomains if Domain.Signal == Signal)) for Signal in Problem.ComponentSignals}

    def ClaimsForNodes(Nodes: frozenset[Position3]) -> RoutingResourceClaims:
        Claims = RouteClaimsCache.get(Nodes)
        if Claims is not None:
            return Claims
        Claims = Problem.ResourceGraph.BuildRouteClaims(Nodes) if Problem.ResourceGraph is not None else RoutingResourceClaims(WireCells=Nodes, SupportCells=frozenset(((X, Y - 1, Z) for X, Y, Z in Nodes)), ElectricalCells=frozenset(DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(set(Nodes))))
        RouteClaimsCache[Nodes] = Claims
        return Claims

    def ClaimsForNodeBatch(NodeSets: Iterable[frozenset[Position3]]) -> dict[frozenset[Position3], RoutingResourceClaims]:
        """Materialize independent claim sets through the bounded native pool.

        The tree frontier retains ownership, ordering, and dominance in this
        process.  Only the pure physical expansion (wire/support/air/electrical
        cells) is batched, so a Rayon worker never observes mutable router
        state.  Non-default technologies keep the existing authoritative
        Python implementation until they have an equivalent native contract.
        """
        UniqueNodes = tuple(sorted(set(NodeSets), key=repr))
        Missing = tuple((Nodes for Nodes in UniqueNodes if Nodes not in RouteClaimsCache))
        if not Missing:
            return {Nodes: RouteClaimsCache[Nodes] for Nodes in UniqueNodes}
        ResourceGraph = Problem.ResourceGraph
        Technology = getattr(ResourceGraph, 'Technology', None)
        NativeCompatible = bool(_BuildRouteClaimsBatchWithTelemetry is not None and (ResourceGraph is None or Technology == DefaultRedstoneRoutingTechnology))
        if NativeCompatible and len(Missing) > 1:
            NativeClaims, ActiveWorkerCount = _BuildRouteClaimsBatchWithTelemetry([tuple(sorted(Nodes)) for Nodes in Missing], tuple(sorted(getattr(ResourceGraph, 'ActualBlocks', ()))), tuple(sorted(getattr(ResourceGraph, 'SolidBlocks', ()))))
            for Nodes, (Wire, Support, Air, Electrical) in zip(Missing, NativeClaims, strict=True):
                RouteClaimsCache[Nodes] = RoutingResourceClaims(WireCells=frozenset(Wire), SupportCells=frozenset(Support), RequiredAirCells=frozenset(Air), ElectricalCells=frozenset(Electrical))
            SolverDiagnostics['NativeClaimBatchCount'] = int(SolverDiagnostics.get('NativeClaimBatchCount', 0)) + 1
            SolverDiagnostics['NativeClaimBatchWorkItems'] = int(SolverDiagnostics.get('NativeClaimBatchWorkItems', 0)) + len(Missing)
            SolverDiagnostics['NativeClaimBatchWorkerCount'] = int(_GetRoutingThreadCount()) if _GetRoutingThreadCount is not None else 0
            SolverDiagnostics['NativeClaimBatchActiveWorkerCount'] = int(ActiveWorkerCount)
        else:
            for Nodes in Missing:
                ClaimsForNodes(Nodes)
        return {Nodes: RouteClaimsCache[Nodes] for Nodes in UniqueNodes}

    def BlockingImmutableClaims(Signal: str) -> tuple[tuple[str, RoutingResourceClaims], ...]:
        if PreparedSymbolicNetStateContext is not None and str(Signal) == PreparedSymbolicNetStateContext.Signal:
            return PreparedSymbolicNetStateContext.BlockingImmutableClaims
        return (*ImmutableClaimsBySignal, *tuple(((str(ReservedSignal), Claims) for ReservedSignal, Claims in Problem.ReservedGlobalClaimsBySignal if str(ReservedSignal) != Signal)))
    ReservedGlobalGeometryBlockerSetsBySignal: dict[str, list[frozenset[str]]] = defaultdict(list)
    ReservedGlobalCandidateBlockerSetsBySignalDomain: dict[tuple[str, int], list[frozenset[str]]] = defaultdict(list)
    GeometryClaimRejectionReasonsBySignal: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    GeometrySelfConflictResourcesBySignal: dict[str, set[str]] = defaultdict(set)
    ReservedGlobalClaimSignals = frozenset((str(Signal) for Signal, _Claims in Problem.ReservedGlobalClaimsBySignal))
    ReservedGlobalWireCellsBySignal: dict[str, frozenset[Position3]] = {Signal: frozenset().union(*(Claims.WireCells for ReservedSignal, Claims in Problem.ReservedGlobalClaimsBySignal if str(ReservedSignal) == Signal)) for Signal in ReservedGlobalClaimSignals}

    def HasBlockingClaimConflict(Signal: str, Claims: RoutingResourceClaims) -> bool:
        ConflictingOwners = frozenset((str(Owner) for Owner, ImmutableClaims in BlockingImmutableClaims(Signal) if ComponentClaimsConflict(Claims, ImmutableClaims)))
        if not ConflictingOwners:
            return False
        ImmutableBlockers = ConflictingOwners - ReservedGlobalClaimSignals
        ReservedBlockers = ConflictingOwners & ReservedGlobalClaimSignals
        if not ImmutableBlockers and ReservedBlockers:
            ReservedGlobalGeometryBlockerSetsBySignal[Signal].append(ReservedBlockers)
        return True

    def HasSameSignalReservedSelfConflict(Signal: str, Nodes: frozenset[Position3], CombinedClaims: RoutingResourceClaims | None=None) -> bool:
        SameSignalReservedNodes = ReservedGlobalWireCellsBySignal.get(Signal, frozenset())
        if not SameSignalReservedNodes:
            return False
        CombinedClaims = ClaimsForNodes(frozenset((*Nodes, *SameSignalReservedNodes))) if CombinedClaims is None else CombinedClaims
        if not FindSelfClaimConflicts({Signal: CombinedClaims}):
            return False
        ReservedGlobalGeometryBlockerSetsBySignal[Signal].append(frozenset((Signal,)))
        return True

    def BuildGeometryStructure(Signal: str, Candidates: tuple[ComponentTerminalAccessCandidate, ...], EgressPath: tuple[Position3, ...], FabricSubtree: tuple[frozenset[Position3], frozenset[RoutingEdge]] | None=None) -> tuple[frozenset[Position3], frozenset[RoutingEdge]] | None:
        Attachments = tuple((Candidate.Attachment for Candidate in Candidates)) + ((EgressPath[0],) if EgressPath else ())
        Subtree = FabricSubtree if FabricSubtree is not None else _UniqueFabricSubtree(Problem.Fabric, Attachments, Adjacency=FabricAdjacency, ParentCache=FabricParentCache)
        if Subtree is None:
            return None
        Nodes = set(Subtree[0])
        Edges = set(Subtree[1])
        for Candidate in Candidates:
            Nodes.update(Candidate.Path)
            Edges.update((_NormalizedEdge(First, Second) for First, Second in zip(Candidate.Path, Candidate.Path[1:])))
        for Claim in LocalClaimsBySignal.get(Signal, ()):
            Nodes.update(Claim.Nodes)
            Edges.update((_NormalizedEdge(*Edge) for Edge in Claim.Edges))
        if EgressPath:
            Nodes.update(EgressPath)
            Edges.update((_NormalizedEdge(First, Second) for First, Second in zip(EgressPath, EgressPath[1:])))
        FrozenNodes = frozenset(Nodes)
        FrozenEdges = frozenset(Edges)
        return (FrozenNodes, FrozenEdges)

    def BuildFabricSubtreeBatch(AttachmentSets: Iterable[tuple[Position3, ...]]) -> tuple[tuple[tuple[frozenset[Position3], frozenset[RoutingEdge]] | None, ...], int]:
        Values = tuple(AttachmentSets)
        if _BuildFabricSubtreesBatchWithTelemetry is None or len(Values) < 2:
            return (tuple((_UniqueFabricSubtree(Problem.Fabric, Attachments, Adjacency=FabricAdjacency, ParentCache=FabricParentCache) for Attachments in Values)), 0)
        NativeSubtrees, ActiveWorkerCount = _BuildFabricSubtreesBatchWithTelemetry(tuple(sorted(Problem.Fabric.Nodes)), tuple(sorted(Problem.Fabric.Edges)), Values)
        Result = tuple((None if Subtree is None else (frozenset(Subtree[0]), frozenset((_NormalizedEdge(First, Second) for First, Second in Subtree[1]))) for Subtree in NativeSubtrees))
        SolverDiagnostics['NativeFabricSubtreeBatchCount'] = int(SolverDiagnostics.get('NativeFabricSubtreeBatchCount', 0)) + 1
        SolverDiagnostics['NativeFabricSubtreeBatchWorkItems'] = int(SolverDiagnostics.get('NativeFabricSubtreeBatchWorkItems', 0)) + len(Values)
        SolverDiagnostics['NativeFabricSubtreeBatchActiveWorkerCount'] = int(ActiveWorkerCount)
        return (Result, int(ActiveWorkerCount))

    def IsGeometryClaimsEligible(Signal: str, Nodes: frozenset[Position3], Claims: RoutingResourceClaims, SameSignalCombinedClaims: RoutingResourceClaims | None=None) -> bool:
        SelfConflicts = FindSelfClaimConflicts({Signal: Claims})
        if SelfConflicts:
            GeometryClaimRejectionReasonsBySignal[Signal]['self-geometry'] += 1
            GeometrySelfConflictResourcesBySignal[Signal].update(
                map(str, SelfConflicts)
            )
            return False
        if HasSameSignalReservedSelfConflict(Signal, Nodes, SameSignalCombinedClaims):
            GeometryClaimRejectionReasonsBySignal[Signal]['same-signal-reserved-route'] += 1
            return False
        if HasBlockingClaimConflict(Signal, Claims):
            GeometryClaimRejectionReasonsBySignal[Signal]['immutable-owner'] += 1
            return False
        return True

    def BuildGeometry(Signal: str, Candidates: tuple[ComponentTerminalAccessCandidate, ...], EgressPath: tuple[Position3, ...]) -> tuple[frozenset[Position3], frozenset[RoutingEdge], RoutingResourceClaims] | None:
        Structure = BuildGeometryStructure(Signal, Candidates, EgressPath)
        if Structure is None:
            return None
        Nodes, Edges = Structure
        Claims = ClaimsForNodes(Nodes)
        if not IsGeometryClaimsEligible(Signal, Nodes, Claims):
            return None
        return (Nodes, Edges, Claims)

    def BuildSignalStates(Signal: str) -> tuple[ComponentTreeDpNetState, ...] | None:
        nonlocal PeakFrontierStateCount, DominatedStateCount
        nonlocal IncrementalPhysicalEgressMaterializationCount
        Domains = DomainsBySignal[Signal]
        FrontierComponentMismatchCount = 0
        FrontierMissingSubtreeCount = 0
        FrontierClaimRejectionCount = 0
        FrontierStateCountsByDepth: list[int] = []
        DefaultPhysicalPort = SelectComponentSymbolicPhysicalPort(Problem, Signal)
        PhysicalPorts = tuple(PreparedPhysicalPortVariants) if PreparedPhysicalPortVariants else (DefaultPhysicalPort,) if DefaultPhysicalPort is not None else ()
        if any((str(Port.Signal) != Signal for Port in PhysicalPorts)):
            raise ValueError('prepared physical port variants contain another signal')
        CertifiedDomains = {frozenset(getattr(Port, 'OwnedCandidateFingerprints', ())) for Port in PhysicalPorts}
        if len(CertifiedDomains) > 1:
            raise ValueError('prepared physical port variants require one exact owned candidate domain')
        Certified = next(iter(CertifiedDomains)) if CertifiedDomains else frozenset()
        CachedTerminalFrontier = PreparedSymbolicNetStateContext.TerminalFrontierCache.get(Certified) if PreparedSymbolicNetStateContext is not None else None
        TerminalFrontierCacheHit = CachedTerminalFrontier is not None
        if CachedTerminalFrontier is not None:
            PreparedSymbolicNetStateContext.TerminalFrontierCacheHitCount += 1
            FilteredByDomain = {Index: Values for Index, Values in enumerate(CachedTerminalFrontier.FilteredByDomain)}
            Frontier = CachedTerminalFrontier.Frontier
            ImmutableRejected = CachedTerminalFrontier.ImmutableRejectedCandidateCount
            CertifiedRejected = CachedTerminalFrontier.CertifiedRejectedCandidateCount
            CandidateFilterEmpty = CachedTerminalFrontier.CandidateFilterEmpty
            PeakFrontierStateCount = max(PeakFrontierStateCount, len(Frontier))
        else:
            FilteredByDomain: dict[int, tuple[ComponentTerminalAccessCandidate, ...]] = {}
            ImmutableRejected = 0
            CertifiedRejected = 0
            for DomainIndex, Domain in enumerate(Domains):
                Retained = []
                PreparedEligibleCandidateFingerprints = PreparedSymbolicNetStateContext.ImmutableEligibleCandidateFingerprintsByDomain[DomainIndex] if PreparedSymbolicNetStateContext is not None else None
                for Candidate in sorted(Domain.Candidates, key=lambda Value: Value.CandidateFingerprint):
                    if Certified and Candidate.CandidateFingerprint not in Certified:
                        CertifiedRejected += 1
                        continue
                    if PreparedEligibleCandidateFingerprints is not None:
                        CandidateEligible = bool(Candidate.CandidateFingerprint in PreparedEligibleCandidateFingerprints)
                    else:
                        CandidateEligible = bool(Candidate.Attachment in FabricComponentByNode)
                        if CandidateEligible:
                            ConflictingOwners = frozenset((str(Owner) for Owner, ImmutableClaims in BlockingImmutableClaims(Signal) if ComponentClaimsConflict(Candidate.Claims, ImmutableClaims)))
                            if ConflictingOwners:
                                ImmutableBlockers = ConflictingOwners - ReservedGlobalClaimSignals
                                ReservedBlockers = ConflictingOwners & ReservedGlobalClaimSignals
                                if not ImmutableBlockers and ReservedBlockers:
                                    ReservedGlobalCandidateBlockerSetsBySignalDomain[Signal, DomainIndex].append(ReservedBlockers)
                                CandidateEligible = False
                    if not CandidateEligible:
                        ImmutableRejected += 1
                        continue
                    Retained.append(Candidate)
                FilteredByDomain[DomainIndex] = tuple(Retained)
            CandidateFilterEmpty = any((not Values for Values in FilteredByDomain.values()))
            Frontier = ()
            if CandidateFilterEmpty:
                if PreparedSymbolicNetStateContext is not None:
                    PreparedSymbolicNetStateContext.TerminalFrontierCache[Certified] = PreparedComponentSymbolicTerminalFrontier(FilteredByDomain=tuple((FilteredByDomain[Index] for Index in range(len(Domains)))), Frontier=(), ImmutableRejectedCandidateCount=ImmutableRejected, CertifiedRejectedCandidateCount=CertifiedRejected, CandidateFilterEmpty=True)
                    PreparedSymbolicNetStateContext.TerminalFrontierBuildCount += 1
        if CandidateFilterEmpty:
            MinimumCandidateGlobalRouteCore: tuple[str, ...] = ()
            CandidateBlockerSets: tuple[frozenset[str], ...] = ()
            for DomainIndex in sorted(FilteredByDomain):
                if FilteredByDomain[DomainIndex]:
                    continue
                DomainBlockerSets = tuple(ReservedGlobalCandidateBlockerSetsBySignalDomain.get((Signal, DomainIndex), ()))
                if not DomainBlockerSets:
                    continue
                BlockerUniverse = tuple(sorted(set().union(*DomainBlockerSets)))
                DomainCore: tuple[str, ...] = ()
                for CoreSize in range(1, len(BlockerUniverse) + 1):
                    DomainCore = next((CandidateCore for CandidateCore in combinations(BlockerUniverse, CoreSize) if all((set(CandidateCore) & Blockers for Blockers in DomainBlockerSets))), ())
                    if DomainCore:
                        break
                if DomainCore and (not MinimumCandidateGlobalRouteCore or (len(DomainCore), DomainCore) < (len(MinimumCandidateGlobalRouteCore), MinimumCandidateGlobalRouteCore)):
                    MinimumCandidateGlobalRouteCore = DomainCore
                    CandidateBlockerSets = DomainBlockerSets
            SignalDiagnostics[Signal] = {'TerminalDomainSizes': [len(FilteredByDomain[Index]) for Index in range(len(Domains))], 'ImmutableRejectedCandidateCount': ImmutableRejected, 'CertifiedRejectedCandidateCount': CertifiedRejected, 'EmptyPhase': 'candidate-filter', 'OwnedSignalDomainContractIndependent': False, 'ReservedGlobalRouteBlockerSetCount': len(CandidateBlockerSets), 'ReservedGlobalRouteUnsatCoreSignals': list(MinimumCandidateGlobalRouteCore), 'ReservedGlobalRouteUnsatCoreComplete': bool(MinimumCandidateGlobalRouteCore), 'ReservedGlobalRouteUnsatCoreFingerprint': _StableFingerprint(('reserved-global-candidate-filter-core-v1', Problem.ProblemFingerprint, Signal, tuple(sorted(CandidateBlockerSets, key=repr)), MinimumCandidateGlobalRouteCore)) if MinimumCandidateGlobalRouteCore else '', 'Complete': True, 'StateCount': 0, 'TerminalFrontierCacheHit': TerminalFrontierCacheHit}
            return ()
        OrderedDomainIndexes = tuple(sorted(range(len(Domains)), key=lambda Index: (len(FilteredByDomain[Index]), Domains[Index].TerminalRole, Domains[Index].TerminalFingerprint)))
        for Depth, DomainIndex in enumerate(OrderedDomainIndexes) if not TerminalFrontierCacheHit else ():
            NextByKey: dict[tuple[object, ...], tuple[int, tuple[tuple[int, ComponentTerminalAccessCandidate], ...], frozenset[Position3], frozenset[RoutingEdge], RoutingResourceClaims]] = {}
            Sources = Frontier or tuple(((FabricComponentByNode[Candidate.Attachment], (), frozenset(), frozenset(), RoutingResourceClaims()) for Candidate in FilteredByDomain[DomainIndex]))
            Bootstrap = not Frontier
            PendingCandidateTransitions: list[tuple[int, tuple[tuple[int, ComponentTerminalAccessCandidate], ...]]] = []
            for SourceOffset, Source in enumerate(Sources):
                CandidateValues = (FilteredByDomain[DomainIndex][SourceOffset],) if Bootstrap else FilteredByDomain[DomainIndex]
                for Candidate in CandidateValues:
                    if not Advance('tree-frontier-terminal'):
                        return None
                    ComponentIndex = FabricComponentByNode[Candidate.Attachment]
                    if ComponentIndex != Source[0]:
                        FrontierComponentMismatchCount += 1
                        continue
                    Selections = tuple(sorted((*Source[1], (DomainIndex, Candidate))))
                    PendingCandidateTransitions.append((ComponentIndex, Selections))
            FabricSubtrees, _ActiveFabricWorkers = BuildFabricSubtreeBatch((tuple((Candidate.Attachment for _DomainIndex, Candidate in Selections)) for _ComponentIndex, Selections in PendingCandidateTransitions))
            PendingTransitions: list[tuple[int, tuple[tuple[int, ComponentTerminalAccessCandidate], ...], frozenset[Position3], frozenset[RoutingEdge]]] = []
            for (ComponentIndex, Selections), FabricSubtree in zip(PendingCandidateTransitions, FabricSubtrees, strict=True):
                if FabricSubtree is None:
                    FrontierMissingSubtreeCount += 1
                    continue
                OrderedCandidates = tuple((Value for _Index, Value in Selections))
                Structure = BuildGeometryStructure(Signal, OrderedCandidates, (), FabricSubtree)
                if Structure is None:
                    continue
                Nodes, Edges = Structure
                PendingTransitions.append((ComponentIndex, Selections, Nodes, Edges))
            SameSignalReservedNodes = ReservedGlobalWireCellsBySignal.get(Signal, frozenset())
            ClaimNodeSets = [Nodes for _ComponentIndex, _Selections, Nodes, _Edges in PendingTransitions]
            if SameSignalReservedNodes:
                ClaimNodeSets.extend((frozenset((*Nodes, *SameSignalReservedNodes)) for _ComponentIndex, _Selections, Nodes, _Edges in PendingTransitions))
            ClaimsByNodes = ClaimsForNodeBatch(ClaimNodeSets)
            for ComponentIndex, Selections, Nodes, Edges in PendingTransitions:
                Claims = ClaimsByNodes[Nodes]
                SameSignalCombinedClaims = ClaimsByNodes[frozenset((*Nodes, *SameSignalReservedNodes))] if SameSignalReservedNodes else None
                if FindSelfClaimConflicts({Signal: Claims}) and Problem.ResourceGraph is not None:
                    OrderedCandidates = tuple((Value for _Index, Value in Selections))
                    FixedNodes = frozenset((
                        *(Node for Candidate in OrderedCandidates for Node in Candidate.Path),
                        *(Node for Claim in LocalClaimsBySignal.get(Signal, ()) for Node in Claim.Nodes),
                    ))
                    ClaimsAwareSubtree = BuildClaimsAwareComponentFabricSubtree(
                        Problem.Fabric,
                        tuple((Candidate.Attachment for Candidate in OrderedCandidates)),
                        Problem.ResourceGraph,
                        FixedNodes=FixedNodes,
                    )
                    ClaimsAwareStructure = BuildGeometryStructure(
                        Signal,
                        OrderedCandidates,
                        (),
                        ClaimsAwareSubtree,
                    ) if ClaimsAwareSubtree is not None else None
                    if ClaimsAwareStructure is not None:
                        Nodes, Edges = ClaimsAwareStructure
                        Claims = ClaimsForNodes(Nodes)
                        SameSignalCombinedClaims = ClaimsForNodes(
                            frozenset((*Nodes, *SameSignalReservedNodes))
                        ) if SameSignalReservedNodes else None
                        SolverDiagnostics['ClaimsAwareFabricSubtreeFallbackCount'] = int(SolverDiagnostics.get('ClaimsAwareFabricSubtreeFallbackCount', 0)) + 1
                if not IsGeometryClaimsEligible(Signal, Nodes, Claims, SameSignalCombinedClaims):
                    FrontierClaimRejectionCount += 1
                    continue
                Key = (Depth + 1, ComponentIndex, Nodes, Edges)
                Value = (ComponentIndex, Selections, Nodes, Edges, Claims)
                Existing = NextByKey.get(Key)
                if Existing is not None:
                    DominatedStateCount += 1
                    ExistingIds = tuple((CandidateValue.CandidateFingerprint for _Index, CandidateValue in Existing[1]))
                    ValueIds = tuple((CandidateValue.CandidateFingerprint for _Index, CandidateValue in Value[1]))
                    if ExistingIds <= ValueIds:
                        continue
                NextByKey[Key] = Value
            Frontier = tuple((NextByKey[Key] for Key in sorted(NextByKey, key=repr)))
            FrontierStateCountsByDepth.append(len(Frontier))
            PeakFrontierStateCount = max(PeakFrontierStateCount, len(Frontier))
            if not Frontier:
                break
        if not TerminalFrontierCacheHit and PreparedSymbolicNetStateContext is not None:
            PreparedSymbolicNetStateContext.TerminalFrontierCache[Certified] = PreparedComponentSymbolicTerminalFrontier(FilteredByDomain=tuple((FilteredByDomain[Index] for Index in range(len(Domains)))), Frontier=Frontier, ImmutableRejectedCandidateCount=ImmutableRejected, CertifiedRejectedCandidateCount=CertifiedRejected, CandidateFilterEmpty=False)
            PreparedSymbolicNetStateContext.TerminalFrontierBuildCount += 1
        if not Frontier:
            BlockerSets = tuple(ReservedGlobalGeometryBlockerSetsBySignal.get(Signal, ()))
            MinimumGlobalRouteCore: tuple[str, ...] = ()
            if BlockerSets:
                BlockerUniverse = tuple(sorted(set().union(*BlockerSets)))
                for CoreSize in range(1, len(BlockerUniverse) + 1):
                    MinimumGlobalRouteCore = next((CandidateCore for CandidateCore in combinations(BlockerUniverse, CoreSize) if all((set(CandidateCore) & Blockers for Blockers in BlockerSets))), ())
                    if MinimumGlobalRouteCore:
                        break
            ContractIndependent = bool(not CertifiedRejected and (not Problem.ReservedGlobalClaimsBySignal))
            OwnedDomainProjectionFingerprint = _StableFingerprint(('tree-frontier-owned-signal-domain-v1', Problem.Fabric.FabricFingerprint, Signal, tuple(((Domain.TerminalRole, Domain.TerminalFingerprint, tuple(((Candidate.CandidateFingerprint, Candidate.Attachment, Candidate.Path) for Candidate in Domain.Candidates))) for Domain in Domains)), tuple(((Claim.Nodes, Claim.Edges) for Claim in LocalClaimsBySignal.get(Signal, ()))), tuple(((Owner, _ClaimsFingerprint(Claims)) for Owner, Claims in ImmutableClaimsBySignal)), Problem.MaximumPowerDistance, getattr(Problem.ResourceGraph, 'GraphVersion', '')))
            SignalDiagnostics[Signal] = {'TerminalDomainSizes': [len(FilteredByDomain[Index]) for Index in range(len(Domains))], 'TerminalCoverageCount': len(Domains), 'TerminalAttachmentComponents': [[FabricComponentByNode.get(Candidate.Attachment, -1) for Candidate in FilteredByDomain[Index]] for Index in range(len(Domains))], 'TerminalAccessCandidates': [[{'CandidateFingerprint': Candidate.CandidateFingerprint, 'Attachment': list(Candidate.Attachment), 'Path': [list(Node) for Node in Candidate.Path]} for Candidate in FilteredByDomain[Index][:4]] for Index in range(len(Domains))], 'FrontierStateCountsByDepth': FrontierStateCountsByDepth, 'FrontierComponentMismatchCount': FrontierComponentMismatchCount, 'FrontierMissingSubtreeCount': FrontierMissingSubtreeCount, 'FrontierClaimRejectionCount': FrontierClaimRejectionCount, 'FrontierClaimRejectionReasons': dict(sorted(GeometryClaimRejectionReasonsBySignal[Signal].items())), 'FrontierSelfConflictResources': sorted(GeometrySelfConflictResourcesBySignal[Signal]), 'ImmutableRejectedCandidateCount': ImmutableRejected, 'CertifiedRejectedCandidateCount': CertifiedRejected, 'FinalStateCount': 0, 'EmptyPhase': 'owned-terminal-frontier', 'OwnedSignalDomainContractIndependent': ContractIndependent, 'OwnedSignalDomainProjectionFingerprint': OwnedDomainProjectionFingerprint, 'ReservedGlobalRouteBlockerSetCount': len(BlockerSets), 'ReservedGlobalRouteUnsatCoreSignals': list(MinimumGlobalRouteCore), 'ReservedGlobalRouteUnsatCoreComplete': bool(MinimumGlobalRouteCore), 'ReservedGlobalRouteUnsatCoreFingerprint': _StableFingerprint(('reserved-global-route-frontier-core-v1', Problem.ProblemFingerprint, Signal, tuple(sorted(BlockerSets, key=repr)), MinimumGlobalRouteCore)) if MinimumGlobalRouteCore else '', 'Complete': True, 'TerminalFrontierCacheHit': TerminalFrontierCacheHit}
            return ()
        FinalByFingerprint: dict[str, ComponentTreeDpNetState] = {}
        External = tuple((Value for Value in Problem.ExternalContinuationTerminals if Value[0] == Signal))
        for _ComponentIndex, Selections, PartialNodes, PartialEdges, PartialClaims in Frontier:
            CandidatesByDomain = dict(Selections)
            Candidates = tuple((CandidatesByDomain[Index] for Index in range(len(Domains))))
            if External and PhysicalPorts:
                EgressVariants = tuple(((tuple(Port.LocalPath), Port) for Port in PhysicalPorts))
            elif External:
                EgressVariants = tuple(((EgressPath, None) for Attachment in sorted({Candidate.Attachment for Candidate in Candidates}) for EgressPath in BuildComponentEgressPaths(Attachment)))
            else:
                EgressVariants = (((), None),)
            for EgressPath, ActivePhysicalPort in EgressVariants:
                if not Advance('tree-frontier-egress'):
                    return None
                EgressPath = tuple(EgressPath)
                PhysicalLocalClaims = getattr(ActivePhysicalPort, 'LocalClaims', None)
                UseIncrementalPhysicalEgress = bool(ActivePhysicalPort is not None and EgressPath and (EgressPath == tuple(ActivePhysicalPort.LocalPath)) and isinstance(PhysicalLocalClaims, RoutingResourceClaims))
                if UseIncrementalPhysicalEgress:
                    IncrementalPhysicalEgressMaterializationCount += 1
                    ConnectorSubtree = _UniqueFabricSubtree(Problem.Fabric, tuple((Candidate.Attachment for Candidate in Candidates)) + (EgressPath[0],), Adjacency=FabricAdjacency, ParentCache=FabricParentCache)
                    if ConnectorSubtree is None:
                        Geometry = None
                    else:
                        ConnectorNodes, ConnectorEdges = ConnectorSubtree
                        DeltaNodes = frozenset((*ConnectorNodes - PartialNodes, *EgressPath))
                        Nodes = frozenset((*PartialNodes, *ConnectorNodes, *EgressPath))
                        Edges = frozenset((*PartialEdges, *ConnectorEdges, *(_NormalizedEdge(First, Second) for First, Second in zip(EgressPath, EgressPath[1:]))))
                        Claims = _MergeClaims((PartialClaims, ClaimsForNodes(DeltaNodes)))
                        Geometry = None if FindSelfClaimConflicts({Signal: Claims}) or HasSameSignalReservedSelfConflict(Signal, Nodes) or HasBlockingClaimConflict(Signal, Claims) else (Nodes, Edges, Claims)
                else:
                    Geometry = BuildGeometry(Signal, Candidates, EgressPath)
                if Geometry is None:
                    continue
                Nodes, Edges, Claims = Geometry
                if ActivePhysicalPort is not None and frozenset(getattr(ActivePhysicalPort, 'GlobalPath', ())) - frozenset((getattr(ActivePhysicalPort, 'Attachment', None),)) & Nodes:
                    continue
                ExportedPorts = (tuple(EgressPath[-1]),) if External and EgressPath else ()
                if Signal in ForbiddenExportPortsBySignal and ExportedPorts == ForbiddenExportPortsBySignal[Signal]:
                    continue
                SourceIndexes = tuple((Index for Index, Domain in enumerate(Domains) if Domain.TerminalRole == 'source'))
                RootIndex = SourceIndexes[0] if SourceIndexes else 0
                Root = Domains[RootIndex].Terminal
                if Root not in Nodes:
                    Root = Candidates[RootIndex].Path[0]
                if ExportedPorts and any((Role == 'source' for _Signal, _Terminal, Role in External)):
                    Root = ExportedPorts[0]
                Repeaters = _PlanTreeRepeaters(Nodes, Edges, Root, Problem.MaximumPowerDistance, SubproblemCache=PreparedSymbolicNetStateContext.TreeRepeaterSubproblemCache if PreparedSymbolicNetStateContext is not None else None, CacheStatistics=PreparedSymbolicNetStateContext.TreeRepeaterCacheStatistics if PreparedSymbolicNetStateContext is not None else None)
                if Repeaters is None:
                    continue
                if Problem.ResourceGraph is not None and any((Problem.ResourceGraph.BuildPrimitive(First, Second) is None for First, Second in Edges)):
                    continue
                NetFingerprint = _StableFingerprint((tuple(sorted(Nodes)), tuple(sorted(Edges)), tuple((Position for Position, _Facing in Repeaters)), tuple(sorted(ExportedPorts)), tuple(sorted(Claims.WireCells)), tuple(sorted(Claims.SupportCells)), tuple(sorted(Claims.RequiredAirCells)), tuple(sorted(Claims.ElectricalCells))))
                FinalByFingerprint.setdefault(NetFingerprint, ComponentTreeDpNetState(Signal=Signal, Candidates=Candidates, EgressPath=tuple(EgressPath), Nodes=Nodes, Edges=Edges, Claims=Claims, Root=Root, RepeaterInputFacings=Repeaters, CoveredTerminals=tuple(sorted((Domain.Terminal for Domain in Domains))), ExportedPorts=ExportedPorts, NetFingerprint=NetFingerprint))
        Result = tuple((FinalByFingerprint[Fingerprint] for Fingerprint in sorted(FinalByFingerprint)))
        EgressBlockerSets = tuple(ReservedGlobalGeometryBlockerSetsBySignal.get(Signal, ()))
        MinimumEgressGlobalRouteCore: tuple[str, ...] = ()
        CompleteExteriorBlockerSets = EgressBlockerSets
        if not Result:
            CandidateDomainCores = []
            for DomainIndex in range(len(Domains)):
                DomainBlockerSets = tuple(ReservedGlobalCandidateBlockerSetsBySignalDomain.get((Signal, DomainIndex), ()))
                if not DomainBlockerSets:
                    continue
                BlockerUniverse = tuple(sorted(set().union(*DomainBlockerSets)))
                DomainCore: tuple[str, ...] = ()
                for CoreSize in range(1, len(BlockerUniverse) + 1):
                    DomainCore = next((CandidateCore for CandidateCore in combinations(BlockerUniverse, CoreSize) if all((set(CandidateCore) & Blockers for Blockers in DomainBlockerSets))), ())
                    if DomainCore:
                        break
                if DomainCore:
                    CandidateDomainCores.append((DomainCore, DomainBlockerSets))
            if CandidateDomainCores:
                MinimumEgressGlobalRouteCore, CompleteExteriorBlockerSets = min(CandidateDomainCores, key=lambda Value: (len(Value[0]), Value[0]))
            elif EgressBlockerSets:
                BlockerUniverse = tuple(sorted(set().union(*EgressBlockerSets)))
                for CoreSize in range(1, len(BlockerUniverse) + 1):
                    MinimumEgressGlobalRouteCore = next((CandidateCore for CandidateCore in combinations(BlockerUniverse, CoreSize) if all((set(CandidateCore) & Blockers for Blockers in EgressBlockerSets))), ())
                    if MinimumEgressGlobalRouteCore:
                        break
        SignalDiagnostics[Signal] = {'TerminalDomainSizes': [len(FilteredByDomain[Index]) for Index in range(len(Domains))], 'TerminalCoverageCount': len(Domains), 'ImmutableRejectedCandidateCount': ImmutableRejected, 'CertifiedRejectedCandidateCount': CertifiedRejected, 'FinalStateCount': len(Result), 'EmptyPhase': 'fixed-egress-or-power' if not Result else '', 'OwnedSignalDomainContractIndependent': False, 'ReservedGlobalRouteBlockerSetCount': len(CompleteExteriorBlockerSets), 'ReservedGlobalRouteUnsatCoreSignals': list(MinimumEgressGlobalRouteCore), 'ReservedGlobalRouteUnsatCoreComplete': bool(MinimumEgressGlobalRouteCore), 'ReservedGlobalRouteUnsatCoreFingerprint': _StableFingerprint(('reserved-global-route-egress-core-v1', Problem.ProblemFingerprint, Signal, tuple(sorted(CompleteExteriorBlockerSets, key=repr)), MinimumEgressGlobalRouteCore)) if MinimumEgressGlobalRouteCore else '', 'Complete': True, 'TerminalFrontierCacheHit': TerminalFrontierCacheHit}
        return Result
    RequestedSignals = frozenset(Problem.ComponentSignals) if RequestedSymbolicStateSignals is None else frozenset(map(str, RequestedSymbolicStateSignals))
    UnknownRequestedSignals = RequestedSignals.difference(Problem.ComponentSignals)
    if UnknownRequestedSignals:
        raise ValueError('requested symbolic state signals are outside the component: ' + ', '.join(sorted(UnknownRequestedSignals)))
    if PreparedSymbolicNetStateContext is not None and RequestedSignals != frozenset((PreparedSymbolicNetStateContext.Signal,)):
        raise ValueError('prepared symbolic net-state context requires its exact signal')
    NetStatesBySignal: dict[str, tuple[ComponentTreeDpNetState, ...]] = {}
    for Signal in sorted(RequestedSignals, key=lambda Value: (sum((len(Domain.Candidates) for Domain in DomainsBySignal[Value])), Value)):
        CacheKey = BuildComponentSymbolicNetStateCacheKey(Problem, Signal, ForbiddenExportPortsBySignal)
        CachedNetState = NetStateCache.get(CacheKey)
        if CachedNetState is not None:
            States, CachedDiagnostics = CachedNetState
            SignalDiagnostics[Signal] = dict(CachedDiagnostics)
            SignalDiagnostics[Signal]['SymbolicNetStateCacheHit'] = True
            SolverDiagnostics['SymbolicNetStateCacheHitCount'] = int(SolverDiagnostics['SymbolicNetStateCacheHitCount']) + 1
        else:
            States = BuildSignalStates(Signal)
            if States is not None:
                CachedDiagnostics = dict(SignalDiagnostics.get(Signal, {}))
                CachedDiagnostics['SymbolicNetStateCacheHit'] = False
                NetStateCache[CacheKey] = (States, CachedDiagnostics)
                SolverDiagnostics['SymbolicNetStateCacheStoreCount'] = int(SolverDiagnostics['SymbolicNetStateCacheStoreCount']) + 1
        if States is None:
            return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'tree-frontier-limit', ExpansionCount)), ExpansionCount=ExpansionCount, Detail='component state work or deadline cap reached', Diagnostics=FinishDiagnostics())
        if not States:
            EmptyDiagnostics = SignalDiagnostics.get(Signal, {})
            ContractIndependentOwnedDomain = bool(EmptyDiagnostics.get('EmptyPhase') == 'owned-terminal-frontier' and EmptyDiagnostics.get('OwnedSignalDomainContractIndependent', False))
            CoreKind = 'tree-frontier-empty-owned-signal-domain' if ContractIndependentOwnedDomain else 'tree-frontier-empty-signal'
            return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, Signal, CoreKind)), ExpansionCount=ExpansionCount, Detail='a component net has no powered frontier state', Diagnostics={**FinishDiagnostics(), 'LocalUnsatCoreComplete': True, 'LocalUnsatCoreSignals': [Signal], 'LocalUnsatCoreKind': CoreKind, 'LocalUnsatCoreVariableKinds': ['net'], 'LocalUnsatCoreProjectionFingerprint': str(EmptyDiagnostics.get('OwnedSignalDomainProjectionFingerprint', '')), 'LocalUnsatCoreFingerprint': _StableFingerprint((Problem.ProblemFingerprint, Signal, CoreKind)), 'SymbolicCapacityProofComplete': bool(StopAfterSymbolicCapacityProof), 'SymbolicCapacityFeasible': False})
        NetStatesBySignal[Signal] = States
    if StopAfterOwnedSignalFrontierProof:
        ProofFingerprint = _StableFingerprint((Problem.ProblemFingerprint, 'owned-signal-frontier-feasible', tuple(((Signal, len(States)) for Signal, States in sorted(NetStatesBySignal.items())))))
        return ComponentRoutingSolveResult(Status='frontier-feasible', ProofFingerprint=ProofFingerprint, ExpansionCount=ExpansionCount, Detail='every owned component signal has a powered frontier state', Diagnostics={**FinishDiagnostics(), 'OwnedSignalFrontierProofComplete': True, 'OwnedSignalFrontierFeasible': True, 'OwnedSignalFrontierStateCounts': {Signal: len(States) for Signal, States in sorted(NetStatesBySignal.items())}})
    Variables: list[tuple[str, str, str, tuple[Any, ...], Callable[[Any], Any]]] = []
    for Signal, States in NetStatesBySignal.items():
        Variables.append(('net', Signal, Signal, States, lambda Value: Value.Claims))
    for DomainIndex, Domain in enumerate(Problem.ExternalContinuationDomains):
        Variables.append(('continuation', str(DomainIndex), Domain.Signal, Domain.Candidates, lambda Value: Value.Claims))
    for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains):
        Forbidden = ForbiddenForeignCandidateFingerprintsBySignal.get(Domain.Signal, frozenset())
        Variables.append(('foreign', str(DomainIndex), Domain.Signal, tuple((Candidate for Candidate in Domain.Candidates if Candidate.CandidateFingerprint not in Forbidden)), lambda Value: Value.Claims))
    TransitBySignal = {Domain.Signal: (Index, Domain) for Index, Domain in enumerate(Problem.ForeignTransitDomains)}
    MissingTransitSignals = tuple(sorted(RequiredForeignTransitSignals - TransitBySignal.keys()))
    if MissingTransitSignals:
        return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'missing-required-transit', MissingTransitSignals)), ExpansionCount=ExpansionCount, Detail='required foreign transit has no finite domain', Diagnostics=FinishDiagnostics())
    for Signal in sorted(RequiredForeignTransitSignals):
        DomainIndex, Domain = TransitBySignal[Signal]
        Variables.append(('transit', str(DomainIndex), Signal, Domain.Candidates, lambda Value: Value.Claims))
    StaticClaims = tuple(((str(Claim.Signal), Claim.Claims) for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims) if Claim.Signal not in Problem.ComponentSignals))
    Domains: dict[int, tuple[int, ...]] = {}
    for VariableIndex, Variable in enumerate(Variables):
        _Kind, _Identity, Owner, Options, ClaimsFor = Variable
        Domains[VariableIndex] = tuple((OptionIndex for OptionIndex, Option in enumerate(Options) if all((ComponentClaimsCompatibleForOwners(Owner, ClaimsFor(Option), StaticOwner, StaticValue) for StaticOwner, StaticValue in StaticClaims))))
    if any((not Domain for Domain in Domains.values())):
        EmptyIndexes = tuple(sorted((Index for Index, Domain in Domains.items() if not Domain)))
        EmptySignals = tuple(sorted({Variables[Index][2] for Index in EmptyIndexes}))
        ProjectionFingerprint = _StableFingerprint(('complete-symbolic-empty-capacity-domain-v1', Problem.ProblemFingerprint, tuple(((Variables[Index][0], Variables[Index][1], Variables[Index][2]) for Index in EmptyIndexes)), tuple(((Owner, _ClaimsFingerprint(Claims)) for Owner, Claims in StaticClaims))))
        return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=ProjectionFingerprint, ExpansionCount=ExpansionCount, Detail='a complete symbolic capacity domain is empty', Diagnostics={**FinishDiagnostics(), 'SymbolicCapacityProofComplete': True, 'SymbolicCapacityFeasible': False, 'LocalUnsatCoreComplete': True, 'LocalUnsatCoreKind': 'complete-symbolic-empty-capacity-domain', 'LocalUnsatCoreSignals': list(EmptySignals), 'LocalUnsatCoreProjectionFingerprint': ProjectionFingerprint, 'LocalUnsatCoreFingerprint': ProjectionFingerprint, 'LocalUnsatCoreVariableCount': len(EmptyIndexes), 'LocalUnsatCoreVariableKinds': [Variables[Index][0] for Index in EmptyIndexes]})

    def CapacityOptionFingerprint(Kind: str, Value: Any) -> str:
        return str(Value.NetFingerprint if Kind in {'net', 'transit'} else Value.CandidateFingerprint)

    for FirstIndex, SecondIndex in combinations(sorted(Domains), 2):
        FirstKind, FirstIdentity, FirstOwner, FirstOptions, FirstClaimsFor = Variables[FirstIndex]
        SecondKind, SecondIdentity, SecondOwner, SecondOptions, SecondClaimsFor = Variables[SecondIndex]
        if any((ComponentClaimsCompatibleForOwners(FirstOwner, FirstClaimsFor(FirstOptions[FirstOptionIndex]), SecondOwner, SecondClaimsFor(SecondOptions[SecondOptionIndex])) for FirstOptionIndex in Domains[FirstIndex] for SecondOptionIndex in Domains[SecondIndex])):
            continue
        CoreSignals = tuple(sorted({FirstOwner, SecondOwner}))
        ProjectionFingerprint = _StableFingerprint(('complete-symbolic-capacity-pair-v1', Problem.ProblemFingerprint, (FirstKind, FirstIdentity, FirstOwner, tuple((CapacityOptionFingerprint(FirstKind, FirstOptions[OptionIndex]) for OptionIndex in Domains[FirstIndex]))), (SecondKind, SecondIdentity, SecondOwner, tuple((CapacityOptionFingerprint(SecondKind, SecondOptions[OptionIndex]) for OptionIndex in Domains[SecondIndex])))))
        return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=ProjectionFingerprint, ExpansionCount=ExpansionCount, Detail='two complete symbolic capacity domains have no compatible option pair', Diagnostics={**FinishDiagnostics(), 'SymbolicCapacityProofComplete': True, 'SymbolicCapacityFeasible': False, 'LocalUnsatCoreComplete': True, 'LocalUnsatCoreKind': 'complete-symbolic-capacity-pair', 'LocalUnsatCoreSignals': list(CoreSignals), 'LocalUnsatCoreCurrentSignal': FirstOwner, 'LocalUnsatCoreCompleteSignal': SecondOwner, 'LocalUnsatCoreProjectionFingerprint': ProjectionFingerprint, 'LocalUnsatCoreFingerprint': ProjectionFingerprint, 'LocalUnsatCoreVariableKinds': [FirstKind, SecondKind]})
    Selected: dict[tuple[str, str], Any] = {}
    SelectedClaims: list[tuple[str, RoutingResourceClaims]] = list(StaticClaims)

    def SelectedForeignAssignments() -> frozenset[tuple[str, Position3, str]]:
        return frozenset(((Domain.Signal, Domain.Terminal, Candidate.CandidateFingerprint) for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains) if (Candidate := Selected.get(('foreign', str(DomainIndex)))) is not None))

    def ViolatesForbiddenForeignPair() -> bool:
        Current = SelectedForeignAssignments()
        return any((ForbiddenPair <= Current for ForbiddenPair in ForbiddenForeignAssignmentPairs))

    def OptionFingerprint(Kind: str, Value: Any) -> str:
        return CapacityOptionFingerprint(Kind, Value)

    def Search(Remaining: dict[int, tuple[int, ...]]) -> bool:
        if not Remaining:
            if ViolatesForbiddenForeignPair():
                return False
            AssignmentFingerprint = _StableFingerprint(tuple(sorted(((Kind, Identity, OptionFingerprint(Kind, Value)) for (Kind, Identity), Value in Selected.items()))))
            if AssignmentFingerprint in ForbiddenAssignmentFingerprints:
                return False
            SolverDiagnostics['SelectedAssignmentFingerprint'] = AssignmentFingerprint
            return True
        SelectedIndex = min(Remaining, key=lambda Index: (len(Remaining[Index]), 0 if Variables[Index][0] == 'net' else 1, Variables[Index][0], Variables[Index][1]))
        Kind, Identity, Owner, Options, ClaimsFor = Variables[SelectedIndex]
        for OptionIndex in Remaining[SelectedIndex]:
            if not Advance('tree-frontier-capacity'):
                return False
            Option = Options[OptionIndex]
            Claims = ClaimsFor(Option)
            if any((not ComponentClaimsCompatibleForOwners(Owner, Claims, SelectedOwner, SelectedValue) for SelectedOwner, SelectedValue in SelectedClaims)):
                continue
            Next: dict[int, tuple[int, ...]] = {}
            ForwardLegal = True
            for OtherIndex, OtherDomain in Remaining.items():
                if OtherIndex == SelectedIndex:
                    continue
                OtherOwner = Variables[OtherIndex][2]
                OtherOptions = Variables[OtherIndex][3]
                OtherClaimsFor = Variables[OtherIndex][4]
                Filtered = tuple((OtherOptionIndex for OtherOptionIndex in OtherDomain if ComponentClaimsCompatibleForOwners(Owner, Claims, OtherOwner, OtherClaimsFor(OtherOptions[OtherOptionIndex]))))
                if not Filtered:
                    ForwardLegal = False
                    break
                Next[OtherIndex] = Filtered
            if not ForwardLegal:
                continue
            Selected[Kind, Identity] = Option
            SelectedClaims.append((Owner, Claims))
            if not ViolatesForbiddenForeignPair() and Search(Next):
                return True
            SelectedClaims.pop()
            del Selected[Kind, Identity]
        return False
    Feasible = Search(Domains)
    if not Feasible:
        Status = 'incomplete' if HitLimit else 'architectural-unsatisfiable'
        if not HitLimit and (not ForbiddenAssignmentFingerprints) and (not ForbiddenForeignAssignmentPairs):

            def CapacitySubsetHasSupport(VariableIndexes: tuple[int, ...]) -> bool:
                SelectedSubsetClaims = list(StaticClaims)

                def SearchSubset(RemainingIndexes: tuple[int, ...]) -> bool:
                    if not RemainingIndexes:
                        return True
                    SelectedIndex = min(RemainingIndexes, key=lambda Index: (len(Domains[Index]), Variables[Index][0], Variables[Index][1]))
                    _Kind, _Identity, Owner, Options, ClaimsFor = Variables[SelectedIndex]
                    NextIndexes = tuple((Index for Index in RemainingIndexes if Index != SelectedIndex))
                    for OptionIndex in Domains[SelectedIndex]:
                        Claims = ClaimsFor(Options[OptionIndex])
                        if any((not ComponentClaimsCompatibleForOwners(Owner, Claims, SelectedOwner, SelectedClaims) for SelectedOwner, SelectedClaims in SelectedSubsetClaims)):
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
                    CandidateCore = tuple((Index for Index in CoreIndexes if Index != VariableIndex))
                    if CandidateCore and (not CapacitySubsetHasSupport(CandidateCore)):
                        CoreIndexes = CandidateCore
                CoreSignals = tuple(sorted({Variables[Index][2] for Index in CoreIndexes}))
                CoreProjectionFingerprint = _StableFingerprint(('complete-symbolic-capacity-core-v1', Problem.ProblemFingerprint, tuple(((Variables[Index][0], Variables[Index][1], Variables[Index][2], tuple((CapacityOptionFingerprint(Variables[Index][0], Variables[Index][3][OptionIndex]) for OptionIndex in Domains[Index]))) for Index in CoreIndexes))))
                return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=CoreProjectionFingerprint, ExpansionCount=ExpansionCount, Detail='a deletion-minimal complete symbolic capacity core has no compatible assignment', Diagnostics={**FinishDiagnostics(), 'SymbolicCapacityProofComplete': True, 'SymbolicCapacityFeasible': False, 'LocalUnsatCoreComplete': True, 'LocalUnsatCoreKind': 'complete-symbolic-capacity-core', 'LocalUnsatCoreSignals': list(CoreSignals), 'LocalUnsatCoreProjectionFingerprint': CoreProjectionFingerprint, 'LocalUnsatCoreFingerprint': CoreProjectionFingerprint, 'LocalUnsatCoreVariableCount': len(CoreIndexes), 'LocalUnsatCoreVariableKinds': [Variables[Index][0] for Index in CoreIndexes]})
        return ComponentRoutingSolveResult(Status=Status, ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, Status, ExpansionCount, 'tree-frontier-dp-v1')), ExpansionCount=ExpansionCount, Detail='component state work or deadline cap reached' if HitLimit else 'complete symbolic component state space exhausted', Diagnostics={**FinishDiagnostics(), 'SymbolicCapacityProofComplete': not HitLimit, 'SymbolicCapacityFeasible': False})
    if StopAfterSymbolicCapacityProof:
        ProofFingerprint = _StableFingerprint((Problem.ProblemFingerprint, 'symbolic-capacity-feasible', SolverDiagnostics.get('SelectedAssignmentFingerprint', '')))
        return ComponentRoutingSolveResult(Status='capacity-feasible', ProofFingerprint=ProofFingerprint, ExpansionCount=ExpansionCount, Detail='the closed component symbolic capacity CSP is feasible', Diagnostics={**FinishDiagnostics(), 'SymbolicCapacityProofComplete': True, 'SymbolicCapacityFeasible': True})
    Nets = tuple(sorted((RoutedComponentNet(Signal=State.Signal, Root=State.Root, Nodes=State.Nodes, Edges=State.Edges, WireCells=State.Claims.WireCells - frozenset((Position for Position, _InputFacing in State.RepeaterInputFacings)), SupportCells=State.Claims.SupportCells, RepeaterInputFacings=State.RepeaterInputFacings, Claims=State.Claims, CoveredTerminals=State.CoveredTerminals, ExportedPorts=State.ExportedPorts, NetFingerprint=State.NetFingerprint) for Signal in Problem.ComponentSignals for State in (Selected['net', Signal],)), key=lambda Value: Value.NetFingerprint))
    Foreign = tuple(sorted(((Domain.Signal, Domain.Terminal, Selected['foreign', str(DomainIndex)]) for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains)), key=lambda Value: (Value[2].CandidateFingerprint, Value[1])))
    ExternalContinuations = tuple(sorted(((Domain.Signal, Domain.Terminal, Selected['continuation', str(DomainIndex)]) for DomainIndex, Domain in enumerate(Problem.ExternalContinuationDomains)), key=lambda Value: (Value[0], Value[1], Value[2].CandidateFingerprint)))
    ForeignTransits = tuple(sorted((Selected['transit', str(DomainIndex)] for DomainIndex, Domain in enumerate(Problem.ForeignTransitDomains) if Domain.Signal in RequiredForeignTransitSignals), key=lambda Value: Value.NetFingerprint))
    Claims = _MergeClaims((*(Value.Claims for Value in Nets), *(Value[2].Claims for Value in ExternalContinuations), *(Value[2].Claims for Value in Foreign), *(Value.Claims for Value in ForeignTransits)))
    ExportedPorts = tuple(sorted(((Net.Signal, Position) for Net in Nets for Position in Net.ExportedPorts)))
    ExportedPortFingerprint = _StableFingerprint(tuple(_RelativeGeometry((Position for _Signal, Position in ExportedPorts))))
    ClaimsFingerprint = _ClaimsFingerprint(Claims)
    RoutedTemplateFingerprint = _StableFingerprint((Problem.ProblemFingerprint, tuple((Net.NetFingerprint for Net in Nets)), tuple((Value[2].CandidateFingerprint for Value in Foreign)), tuple((Value[2].CandidateFingerprint for Value in ExternalContinuations)), tuple((Value.NetFingerprint for Value in ForeignTransits)), ExportedPortFingerprint, ClaimsFingerprint))
    ProofFingerprint = _StableFingerprint((RoutedTemplateFingerprint, ExpansionCount, 'feasible', 'tree-frontier-dp-v1'))
    SolverDiagnostics['SelectedTreesMaterialized'] = len(Nets)
    Template = RoutedComponentTemplate(ProblemFingerprint=Problem.ProblemFingerprint, PlacementFingerprint=Problem.PlacementFingerprint, LocalTemplateFingerprint=Problem.LocalTemplateFingerprint, FabricFingerprint=Problem.Fabric.FabricFingerprint, RoutedTemplateFingerprint=RoutedTemplateFingerprint, Nets=Nets, ForeignEscapeReservations=Foreign, ExportedPorts=ExportedPorts, Claims=Claims, ExportedPortFingerprint=ExportedPortFingerprint, ClaimsFingerprint=ClaimsFingerprint, ProofFingerprint=ProofFingerprint, ExpansionCount=ExpansionCount, Diagnostics=FinishDiagnostics(), ExternalContinuationReservations=ExternalContinuations, ForeignTransitReservations=ForeignTransits, InterfaceFingerprint=Problem.Interface.InterfaceFingerprint if Problem.Interface is not None else '')
    return ComponentRoutingSolveResult(Status='feasible', Template=Template, ProofFingerprint=ProofFingerprint, ExpansionCount=ExpansionCount, Diagnostics=Template.Diagnostics)

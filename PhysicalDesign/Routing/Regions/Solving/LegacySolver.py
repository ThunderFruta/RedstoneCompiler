"""Legacy exact component-routing search retained as an independent oracle."""

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
from ....Constraints.PhysicalClaims import _MergeClaims, ComponentClaimsCompatibleForOwners, ComponentClaimsConflict
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

from ..Core import CompleteComponentNetPortfolioStaticContext, _ClaimsFingerprint, _ComponentNetPortfolioStructuralFingerprint, _ComponentOrigin, _RelativeGeometry, _StableFingerprint, _TranslateAndValidateNetPortfolio
from ..Domains import FindCompleteComponentNetUnsatSubset, PruneDominatedComponentNetVariants
from ..Boundaries.Fabric import BuildComponentEgressPaths, _BuildAdjacency, _UniqueFabricSubtree
from ..Planning.NetPlanning import _BuildCanonicalAccessCombinationKey, _BuildNetVariant
def _SolveComponentRoutingProblemLegacy(Problem: ComponentRoutingProblem, *, DeadlineSeconds: float | None=None, WorkCheck: Callable[[dict[str, object]], None] | None=None, ForbiddenAssignmentFingerprints: frozenset[str]=frozenset(), ForbiddenExportPortsBySignal: dict[str, tuple[Position3, ...]] | None=None, ForbiddenForeignCandidateFingerprintsBySignal: dict[str, frozenset[str]] | None=None, ForbiddenForeignAssignmentPairs: tuple[frozenset[tuple[str, Position3, str]], ...]=(), VariantPortfolioCache: dict[tuple[str, str], tuple[tuple[RoutedComponentNet, ...], int, dict[str, int], frozenset[str], Position3]] | None=None, NetVariantConstructionCache: dict[tuple[str, frozenset[Position3], frozenset[RoutingEdge], tuple[Position3, ...]], RoutedComponentNet | None] | None=None, RouteClaimsConstructionCache: dict[frozenset[Position3], RoutingResourceClaims] | None=None, NetVariantDiscoveryStateCache: dict[tuple[str, str], dict[str, object]] | None=None, DiscoveryVariantLimit: int | None=8, DiscoveryVariantLimitsBySignal: dict[str, int | None] | None=None, RequiredForeignTransitSignals: frozenset[str]=frozenset(), StopAfterCompleteNetVariantPortfolioSignal: str | None=None, StaticPortfolioContextsBySignal: dict[str, CompleteComponentNetPortfolioStaticContext] | None=None) -> ComponentRoutingSolveResult:
    """Legacy oracle that enumerates complete per-net tree portfolios."""
    Started = monotonic()
    ForbiddenExportPortsBySignal = ForbiddenExportPortsBySignal or {}
    ForbiddenForeignCandidateFingerprintsBySignal = ForbiddenForeignCandidateFingerprintsBySignal or {}
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
    DeclaredFeedthroughSignals = Problem.Interface.DeclaredFeedthroughSignals if Problem.Interface is not None else frozenset()
    ForeignTransitSignals = frozenset((Domain.Signal for Domain in Problem.ForeignTransitDomains))
    ImplicitForeignTransitSignals = tuple(sorted(ForeignTransitSignals - DeclaredFeedthroughSignals if Problem.Interface is not None else ()))
    SolverDiagnostics: dict[str, object] = {'SolverKind': 'complete-tree-portfolio-v1', 'ExploredStateCount': 0, 'PeakFrontierStateCount': 0, 'DominatedStateCount': 0, 'CompleteTreesMaterialized': 0, 'ProblemFingerprint': Problem.ProblemFingerprint, 'FabricFingerprint': Problem.Fabric.FabricFingerprint, 'FabricTopologyKind': Problem.Fabric.TopologyKind, 'FabricNodeCount': len(Problem.Fabric.Nodes), 'FabricEdgeCount': len(Problem.Fabric.Edges), 'ComponentSignalCount': len(Problem.ComponentSignals), 'OwnedTerminalDomainCount': len(Problem.OwnedTerminalDomains), 'ForeignEscapeDomainCount': len(Problem.ForeignEscapeDomains), 'ForeignTransitDomainCount': len(Problem.ForeignTransitDomains), 'InterfaceFingerprint': Problem.Interface.InterfaceFingerprint if Problem.Interface is not None else '', 'DeclaredFeedthroughCount': len(DeclaredFeedthroughSignals), 'ImplicitForeignTransitDomainCount': len(ImplicitForeignTransitSignals), 'ImplicitForeignTransitSignals': list(ImplicitForeignTransitSignals), 'RequiredForeignTransitSignals': sorted(RequiredForeignTransitSignals), 'ExternalContinuationDomainCount': len(Problem.ExternalContinuationDomains), 'ForbiddenForeignAssignmentPairCount': len(ForbiddenForeignAssignmentPairs), 'NetVariantConstructionCacheInitialCount': len(NetVariantConstructionCache), 'RouteClaimsConstructionCacheInitialCount': len(RouteClaimsConstructionCache), 'NetVariantDiscoveryStateCacheInitialCount': len(NetVariantDiscoveryStateCache)}

    def Advance(Phase: str) -> bool:
        nonlocal ExpansionCount
        ExpansionCount += 1
        if WorkCheck is not None and (StopAfterCompleteNetVariantPortfolioSignal is not None or ExpansionCount % 128 == 0):
            WorkCheck({'Phase': Phase, 'ExpansionCount': ExpansionCount})
        return not (ExpansionCount > Problem.MaximumWork or (DeadlineSeconds is not None and monotonic() - Started >= DeadlineSeconds))
    if ImplicitForeignTransitSignals or (Problem.Interface is not None and (not RequiredForeignTransitSignals.issubset(DeclaredFeedthroughSignals))):
        return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'undeclared-foreign-transit', ImplicitForeignTransitSignals, tuple(sorted(RequiredForeignTransitSignals)))), Detail='closed component contains undeclared foreign transit', Diagnostics=SolverDiagnostics)
    if Problem.Interface is not None and (not Problem.Interface.Complete):
        return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'incomplete-closed-interface')), Detail='closed component interface is incomplete', Diagnostics=SolverDiagnostics)
    if not Problem.Fabric.Complete:
        return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, Problem.Fabric.IncompleteReason)), Detail=Problem.Fabric.IncompleteReason, Diagnostics=SolverDiagnostics)
    if not Problem.DomainComplete:
        return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'incomplete-domain')), Detail='one or more terminal domains are incomplete or empty', Diagnostics=SolverDiagnostics)
    TransitDomainsBySignal = {Domain.Signal: Domain for Domain in Problem.ForeignTransitDomains}
    RequiredTransitDomains = tuple((TransitDomainsBySignal.get(Signal) for Signal in sorted(RequiredForeignTransitSignals)))
    MissingRequiredTransitSignals = tuple((Signal for Signal, Domain in zip(sorted(RequiredForeignTransitSignals), RequiredTransitDomains) if Domain is None))
    if MissingRequiredTransitSignals:
        SolverDiagnostics['RequiredTransitPrecheck'] = {'Complete': True, 'MissingSignals': list(MissingRequiredTransitSignals), 'PairCompatibility': []}
        return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'missing-required-transit-domain', len(MissingRequiredTransitSignals))), Detail='required foreign transit has no finite component-fabric domain', Diagnostics=SolverDiagnostics)
    RequiredTransitDomains = tuple((Domain for Domain in RequiredTransitDomains if Domain is not None))
    ImmutableTransitClaims = tuple(((Claim.Signal, Claim.Claims) for Claim in (*Problem.LocalClaims, *Problem.ImmutableClaims) if Claim.Signal not in Problem.ComponentSignals))
    TransitOptions = {Domain.Signal: tuple((Candidate for Candidate in Domain.Candidates if all((ComponentClaimsCompatibleForOwners(Domain.Signal, Candidate.Claims, ImmutableOwner, ImmutableClaims) for ImmutableOwner, ImmutableClaims in ImmutableTransitClaims)))) for Domain in RequiredTransitDomains}
    TransitPairCompatibility = []
    TransitPrecheckUnsatisfiable = any((not TransitOptions[Domain.Signal] for Domain in RequiredTransitDomains))
    for FirstOffset, FirstDomain in enumerate(RequiredTransitDomains):
        for SecondDomain in RequiredTransitDomains[FirstOffset + 1:]:
            CompatiblePairCount = sum((ComponentClaimsCompatibleForOwners(FirstDomain.Signal, First.Claims, SecondDomain.Signal, Second.Claims) for First in TransitOptions[FirstDomain.Signal] for Second in TransitOptions[SecondDomain.Signal]))
            TransitPairCompatibility.append({'FirstSignal': FirstDomain.Signal, 'SecondSignal': SecondDomain.Signal, 'FirstCandidateCount': len(TransitOptions[FirstDomain.Signal]), 'SecondCandidateCount': len(TransitOptions[SecondDomain.Signal]), 'CompatiblePairCount': CompatiblePairCount, 'StructuralPairFingerprint': _StableFingerprint((tuple(((_RelativeGeometry(Candidate.Nodes), _RelativeGeometry((Position for Position, _InputFacing in Candidate.RepeaterInputFacings)), _ClaimsFingerprint(Candidate.Claims)) for Candidate in TransitOptions[FirstDomain.Signal])), tuple(((_RelativeGeometry(Candidate.Nodes), _RelativeGeometry((Position for Position, _InputFacing in Candidate.RepeaterInputFacings)), _ClaimsFingerprint(Candidate.Claims)) for Candidate in TransitOptions[SecondDomain.Signal])), CompatiblePairCount))})
            TransitPrecheckUnsatisfiable = bool(TransitPrecheckUnsatisfiable or CompatiblePairCount == 0)
    SolverDiagnostics['RequiredTransitPrecheck'] = {'Complete': True, 'CandidateCounts': {Domain.Signal: len(TransitOptions[Domain.Signal]) for Domain in RequiredTransitDomains}, 'PairCompatibility': TransitPairCompatibility}
    if TransitPrecheckUnsatisfiable:
        return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'required-transit-capacity-unsatisfiable', tuple((Entry['StructuralPairFingerprint'] for Entry in TransitPairCompatibility)), tuple(sorted((len(TransitOptions[Domain.Signal]) for Domain in RequiredTransitDomains))))), Detail='required foreign transits have no capacity-compatible component-fabric assignment', Diagnostics=SolverDiagnostics)
    DomainsBySignal = {Signal: tuple((Domain for Domain in Problem.OwnedTerminalDomains if Domain.Signal == Signal)) for Signal in Problem.ComponentSignals}
    if StopAfterCompleteNetVariantPortfolioSignal is not None:
        PortfolioSignal = str(StopAfterCompleteNetVariantPortfolioSignal)
        if PortfolioSignal not in DomainsBySignal:
            raise ValueError('requested net portfolio signal is not owned by component')
        DomainsBySignal = {PortfolioSignal: DomainsBySignal[PortfolioSignal]}
    VariantsBySignal: dict[str, tuple[RoutedComponentNet, ...]] = {}
    VariantDiagnosticsBySignal: dict[str, dict[str, object]] = {}
    DiscoveryIncompleteSignals: set[str] = set()
    FabricAdjacency = _BuildAdjacency(Problem.Fabric.Edges)
    FabricComponentByNode: dict[Position3, int] = {}
    for ComponentIndex, Start in enumerate(sorted(Problem.Fabric.Nodes)):
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
    FabricParentCache: dict[Position3, dict[Position3, Position3 | None]] = {}
    ImmutableForeignClaimsForAccess = tuple(((Claim.Signal, Claim.Claims) for Claim in Problem.ImmutableClaims if Claim.Signal not in Problem.ComponentSignals))

    def AccessClaimsContextFingerprint(Signal: str) -> str:
        ClaimsBySignal = (*ImmutableForeignClaimsForAccess, *((str(ReservedSignal), Claims) for ReservedSignal, Claims in Problem.ReservedGlobalClaimsBySignal if str(ReservedSignal) != Signal))
        return _StableFingerprint(tuple(sorted(((str(ClaimSignal), tuple(sorted(map(str, Claims.ResourceIds)))) for ClaimSignal, Claims in ClaimsBySignal))))

    def AccessCandidateBlockers(Signal: str, Candidate: ComponentTerminalAccessCandidate) -> frozenset[str]:
        CacheKey = (AccessClaimsContextFingerprint(Signal), Signal, Candidate.Path)
        Cached = ImmutableAccessConflictCache.get(CacheKey)
        if Cached is not None:
            return Cached
        Result = frozenset((ClaimSignal for ClaimSignal, Claims in (*ImmutableForeignClaimsForAccess, *((str(ReservedSignal), Claims) for ReservedSignal, Claims in Problem.ReservedGlobalClaimsBySignal if str(ReservedSignal) != Signal)) if ComponentClaimsConflict(Candidate.Claims, Claims)))
        ImmutableAccessConflictCache[CacheKey] = Result
        return Result
    ImmutableAccessConflictCache: dict[tuple[str, str, tuple[Position3, ...]], frozenset[str]] = {}
    ReservedClaimsBySignal = {str(Signal): Claims for Signal, Claims in Problem.ReservedGlobalClaimsBySignal}
    PhysicalPortClaimsBySignal = {str(Port.Signal): Port.Claims for Port in (Problem.Interface.PhysicalPortReservations if Problem.Interface is not None else ())}

    def ClassifyReservedBlockers(Claims: RoutingResourceClaims, Blockers: Iterable[str], PortContractBlockers: set[str], GlobalRouteBlockers: set[str]) -> None:
        """Separate immutable seam conflicts from selected channel conflicts."""
        for BlockerValue in Blockers:
            Blocker = str(BlockerValue)
            if Blocker not in ReservedClaimsBySignal:
                continue
            PortClaims = PhysicalPortClaimsBySignal.get(Blocker)
            if PortClaims is not None and ComponentClaimsConflict(Claims, PortClaims):
                PortContractBlockers.add(Blocker)
            else:
                GlobalRouteBlockers.add(Blocker)
    LocalClaimsBySignal = {Signal: tuple((Claim for Claim in Problem.LocalClaims if Claim.Signal == Signal)) for Signal in Problem.ComponentSignals}
    NetVariantTopologyCache = NetVariantConstructionCache
    RouteClaimsCache = RouteClaimsConstructionCache

    def ConnectedAccessCombinationEstimate(Domains: tuple[ComponentTerminalAccessDomain, ...]) -> int:
        CountsByDomain = tuple(({ComponentIndex: sum((FabricComponentByNode.get(Candidate.Attachment) == ComponentIndex for Candidate in Domain.Candidates)) for ComponentIndex in set((FabricComponentByNode.get(Candidate.Attachment) for Candidate in Domain.Candidates if Candidate.Attachment in FabricComponentByNode))} for Domain in Domains))
        CommonComponents = set(CountsByDomain[0]) if CountsByDomain else set()
        for Counts in CountsByDomain[1:]:
            CommonComponents.intersection_update(Counts)
        return sum((ProductIntegers((Counts[ComponentIndex] for Counts in CountsByDomain)) for ComponentIndex in CommonComponents))
    OrderedDomainItems = tuple(sorted(DomainsBySignal.items(), key=lambda Value: (ConnectedAccessCombinationEstimate(Value[1]), tuple(sorted(((Domain.TerminalRole, Domain.TerminalFingerprint, len(Domain.Candidates)) for Domain in Value[1]))))))
    CompleteProofVariants: dict[str, tuple[RoutedComponentNet, ...]] = {}
    CompleteTreeMaterializationCount = 0
    for Signal, Domains in OrderedDomainItems:
        ReservedPortContractConflictSignals: set[str] = set()
        ReservedGlobalRouteConflictSignals: set[str] = set()
        PhysicalPort = next((Value for Value in (Problem.Interface.PhysicalPortReservations if Problem.Interface is not None else ()) if Value.Signal == Signal), None)
        CertifiedCandidateFingerprints = frozenset(getattr(PhysicalPort, 'OwnedCandidateFingerprints', ()))
        CanonicalAccessStateCount = 0
        DuplicateCanonicalAccessStateCount = 0
        NetVariantBuildCount = 0
        EffectiveDiscoveryVariantLimit = DiscoveryVariantLimitsBySignal.get(Signal, DiscoveryVariantLimit)
        ComponentOrigin = _ComponentOrigin(Problem)
        StructuralPortfolioFingerprint = _ComponentNetPortfolioStructuralFingerprint(Problem, Signal, Domains, ComponentOrigin, StaticPortfolioContextsBySignal.get(Signal))
        StructuralCacheKey = ('component-net-translation-v1', StructuralPortfolioFingerprint)
        ExactCacheKey = (Problem.ProblemFingerprint, Signal)
        CachedPortfolio = VariantPortfolioCache.get(ExactCacheKey)
        PortfolioCacheKind = 'exact'
        if CachedPortfolio is None:
            CachedPortfolio = VariantPortfolioCache.get(StructuralCacheKey)
            PortfolioCacheKind = 'structural'
        PortfolioTranslationDelta = (0, 0, 0)
        PortfolioTranslationValidated = False
        if CachedPortfolio is not None:
            CachedVariants, CombinationCount, CachedRejections, CachedImmutableConflicts, CachedOrigin = CachedPortfolio
            CurrentImmutableConflicts = frozenset((Blocker for Domain in Domains for Candidate in Domain.Candidates for Blocker in AccessCandidateBlockers(Signal, Candidate)))
            for Domain in Domains:
                for Candidate in Domain.Candidates:
                    CandidateBlockers = AccessCandidateBlockers(Signal, Candidate)
                    ClassifyReservedBlockers(Candidate.Claims, CandidateBlockers, ReservedPortContractConflictSignals, ReservedGlobalRouteConflictSignals)
            if PortfolioCacheKind == 'structural' and (CachedImmutableConflicts or CurrentImmutableConflicts):
                CachedPortfolio = None
            PortfolioTranslationDelta = (ComponentOrigin[0] - CachedOrigin[0], ComponentOrigin[1] - CachedOrigin[1], ComponentOrigin[2] - CachedOrigin[2])
            if CachedPortfolio is not None:
                TranslatedVariants = _TranslateAndValidateNetPortfolio(CachedVariants, SourceOrigin=CachedOrigin, TargetOrigin=ComponentOrigin, Signal=Signal, Domains=Domains, Problem=Problem)
                if TranslatedVariants is None:
                    CachedPortfolio = None
                else:
                    EnumeratedVariants = TranslatedVariants
                    RejectionCounts = dict(CachedRejections)
                    ImmutableConflictSignals = set(CachedImmutableConflicts)
                    PortfolioTranslationValidated = True
        if CachedPortfolio is None:
            DiscoveryStateKey = (Problem.ProblemFingerprint, Signal)
            CompleteProofContextFingerprint = _StableFingerprint(tuple(sorted(((CompleteSignal, tuple((Variant.NetFingerprint for Variant in CompleteVariants))) for CompleteSignal, CompleteVariants in CompleteProofVariants.items()))))
            PriorDiscoveryState = NetVariantDiscoveryStateCache.get(DiscoveryStateKey)
            if PriorDiscoveryState is not None and PriorDiscoveryState.get('CompleteProofContextFingerprint', '') != CompleteProofContextFingerprint:
                PriorDiscoveryState = None
            VariantsByFingerprint = dict(PriorDiscoveryState.get('Variants', {}) if PriorDiscoveryState is not None else {})
            RejectionCounts = dict(PriorDiscoveryState.get('RejectionCounts', {}) if PriorDiscoveryState is not None else {})
            ImmutableConflictSignals = set(PriorDiscoveryState.get('ImmutableConflictSignals', ()) if PriorDiscoveryState is not None else ())
            ResumeEgressStateCount = int(PriorDiscoveryState.get('ProcessedEgressStateCount', 0) if PriorDiscoveryState is not None else 0)
            ProcessedEgressStateCount = 0
            CombinationCount = 0
            StopDiscovery = False
            ImmutableFilteredDomainCandidates = []
            for Domain in Domains:
                FilteredCandidates = []
                for Candidate in Domain.Candidates:
                    Blockers = AccessCandidateBlockers(Signal, Candidate)
                    if Blockers:
                        ImmutableConflictSignals.update(Blockers)
                        ClassifyReservedBlockers(Candidate.Claims, Blockers, ReservedPortContractConflictSignals, ReservedGlobalRouteConflictSignals)
                        continue
                    FilteredCandidates.append(Candidate)
                ImmutableFilteredDomainCandidates.append(tuple(FilteredCandidates))
            ViableCompleteVariantsBySignal = {}
            SingletonFabricClaims = {Node: Problem.ResourceGraph.BuildRouteClaims(frozenset((Node,))) if Problem.ResourceGraph is not None else RoutingResourceClaims(WireCells=frozenset((Node,)), SupportCells=frozenset(((Node[0], Node[1] - 1, Node[2]),)), ElectricalCells=frozenset(DefaultRedstoneRoutingTechnology.BuildElectricalExclusions({Node}))) for Node in Problem.Fabric.Nodes}
            for CompleteSignal, CompleteVariants in CompleteProofVariants.items():

                def CompleteVariantSupportsDomains(CompleteVariant: RoutedComponentNet) -> bool:
                    CertainlyBlockedNodes = frozenset((Node for Node, NodeClaims in SingletonFabricClaims.items() if ComponentClaimsConflict(NodeClaims, CompleteVariant.Claims)))
                    AllowedNodes = frozenset(Problem.Fabric.Nodes) - CertainlyBlockedNodes
                    AllowedComponentByNode: dict[Position3, int] = {}
                    for AllowedNode in sorted(AllowedNodes):
                        if AllowedNode in AllowedComponentByNode:
                            continue
                        ComponentIndex = len(set(AllowedComponentByNode.values()))
                        PendingNodes = [AllowedNode]
                        AllowedComponentByNode[AllowedNode] = ComponentIndex
                        while PendingNodes:
                            CurrentNode = PendingNodes.pop()
                            for Neighbor in FabricAdjacency.get(CurrentNode, ()):
                                if Neighbor not in AllowedNodes or Neighbor in AllowedComponentByNode:
                                    continue
                                AllowedComponentByNode[Neighbor] = ComponentIndex
                                PendingNodes.append(Neighbor)
                    CommonComponentIndexes: set[int] | None = None
                    for DomainCandidates in ImmutableFilteredDomainCandidates:
                        DomainComponentIndexes = {AllowedComponentByNode[Candidate.Attachment] for Candidate in DomainCandidates if Candidate.Attachment in AllowedComponentByNode and ComponentClaimsCompatibleForOwners(Signal, Candidate.Claims, CompleteSignal, CompleteVariant.Claims)}
                        if not DomainComponentIndexes:
                            return False
                        if CommonComponentIndexes is None:
                            CommonComponentIndexes = set(DomainComponentIndexes)
                        else:
                            CommonComponentIndexes.intersection_update(DomainComponentIndexes)
                        if not CommonComponentIndexes:
                            return False
                    return bool(CommonComponentIndexes)
                ViableCompleteVariants = tuple((CompleteVariant for CompleteVariant in CompleteVariants if CompleteVariantSupportsDomains(CompleteVariant)))
                if not ViableCompleteVariants:
                    CompleteVariantDiagnostics = dict(VariantDiagnosticsBySignal.get(CompleteSignal, {}))
                    CompletePortContractBlockers = frozenset(map(str, CompleteVariantDiagnostics.get('ReservedPortContractConflictSignals', ())))
                    CompleteGlobalRouteBlockers = frozenset(map(str, CompleteVariantDiagnostics.get('ReservedGlobalRouteConflictSignals', ())))
                    PortContractBlockers = frozenset({*ReservedPortContractConflictSignals, *CompletePortContractBlockers})
                    GlobalRouteBlockers = frozenset({*ReservedGlobalRouteConflictSignals, *CompleteGlobalRouteBlockers})
                    LocalCoreSignals = tuple(sorted({Signal, CompleteSignal, *PortContractBlockers}))
                    SolverDiagnostics['VariantDiagnosticsBySignal'] = VariantDiagnosticsBySignal
                    SolverDiagnostics['CompleteNetAccessUnsatSignals'] = sorted((Signal, CompleteSignal))
                    SolverDiagnostics['LocalUnsatCoreComplete'] = True
                    SolverDiagnostics['LocalUnsatCoreSignals'] = list(LocalCoreSignals)
                    SolverDiagnostics['LocalUnsatCoreKind'] = 'complete-opposing-net-access-pair'
                    SolverDiagnostics['LocalUnsatCoreCurrentSignal'] = Signal
                    SolverDiagnostics['LocalUnsatCoreCompleteSignal'] = CompleteSignal
                    SolverDiagnostics['LocalUnsatCorePortContractBlockers'] = sorted(PortContractBlockers)
                    SolverDiagnostics['LocalUnsatCoreGlobalRouteBlockers'] = sorted(GlobalRouteBlockers)
                    SolverDiagnostics['CompleteNetAccessUnsatDomainSizes'] = [len(Values) for Values in ImmutableFilteredDomainCandidates]
                    SolverDiagnostics['LocalUnsatCoreFingerprint'] = _StableFingerprint(('complete-opposing-net-access-pair-v2', LocalCoreSignals, tuple(sorted(PortContractBlockers)), tuple(sorted(GlobalRouteBlockers)), StructuralPortfolioFingerprint, tuple((Variant.NetFingerprint for Variant in CompleteVariants)), tuple((Domain.TerminalFingerprint for Domain in Domains))))
                    return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'complete-net-access-domain-unsatisfiable', LocalCoreSignals, tuple((Variant.NetFingerprint for Variant in CompleteVariants)), tuple((Domain.TerminalFingerprint for Domain in Domains)))), ExpansionCount=ExpansionCount, Detail='no complete opposing-net variant supports every terminal access domain', Diagnostics=SolverDiagnostics)
                ViableCompleteVariantsBySignal[CompleteSignal] = ViableCompleteVariants
            FilteredDomainCandidatesValues = []
            for DomainCandidates in ImmutableFilteredDomainCandidates:
                FilteredCandidates = []
                for Candidate in DomainCandidates:
                    if CertifiedCandidateFingerprints and Candidate.CandidateFingerprint not in CertifiedCandidateFingerprints:
                        RejectionCounts['outside-certified-port-access-domain'] = RejectionCounts.get('outside-certified-port-access-domain', 0) + 1
                        continue
                    CompleteNetBlockers = frozenset((CompleteSignal for CompleteSignal, CompleteVariants in ViableCompleteVariantsBySignal.items() if all((not ComponentClaimsCompatibleForOwners(Signal, Candidate.Claims, CompleteSignal, CompleteVariant.Claims) for CompleteVariant in CompleteVariants))))
                    if CompleteNetBlockers:
                        RejectionCounts['complete-net-access-capacity'] = RejectionCounts.get('complete-net-access-capacity', 0) + 1
                        ImmutableConflictSignals.update(CompleteNetBlockers)
                        continue
                    FilteredCandidates.append(Candidate)
                FilteredDomainCandidatesValues.append(tuple(FilteredCandidates))
            FilteredDomainCandidates = tuple(FilteredDomainCandidatesValues)
            CandidateDomainsByComponent = tuple(({ComponentIndex: tuple((Candidate for Candidate in DomainCandidates if FabricComponentByNode.get(Candidate.Attachment) == ComponentIndex)) for ComponentIndex in sorted(set((FabricComponentByNode.get(Candidate.Attachment) for Candidate in DomainCandidates if Candidate.Attachment in FabricComponentByNode)))} for DomainCandidates in FilteredDomainCandidates))
            CommonComponentIndexes = set(CandidateDomainsByComponent[0]) if CandidateDomainsByComponent else set()
            for Values in CandidateDomainsByComponent[1:]:
                CommonComponentIndexes.intersection_update(Values)
            GuidedCombinationCount = 0
            SeenCanonicalAccessStates: set[tuple[object, ...]] = set(PriorDiscoveryState.get('CanonicalAccessStates', ()) if PriorDiscoveryState is not None else ())
            CanonicalAccessStateCount = len(SeenCanonicalAccessStates)

            def OrderedCandidateCombinations(ComponentIndex: int) -> Iterable[tuple[ComponentTerminalAccessCandidate, ...]]:
                nonlocal GuidedCombinationCount
                SeenCombinationFingerprints: set[tuple[str, ...]] = set()
                if ViableCompleteVariantsBySignal:
                    CompleteVariantGroups = tuple((ViableCompleteVariantsBySignal[CompleteSignal] for CompleteSignal in sorted(ViableCompleteVariantsBySignal)))
                    for CompleteVariantSelection in product(*CompleteVariantGroups):
                        GuidedDomains = tuple((tuple((Candidate for Candidate in Values[ComponentIndex] if all((ComponentClaimsCompatibleForOwners(Signal, Candidate.Claims, CompleteVariant.Signal, CompleteVariant.Claims) for CompleteVariant in CompleteVariantSelection)))) for Values in CandidateDomainsByComponent))
                        if any((not Values for Values in GuidedDomains)):
                            continue
                        for Candidates in islice(product(*GuidedDomains), 4):
                            Fingerprint = tuple((Candidate.CandidateFingerprint for Candidate in Candidates))
                            if Fingerprint in SeenCombinationFingerprints:
                                continue
                            SeenCombinationFingerprints.add(Fingerprint)
                            GuidedCombinationCount += 1
                            yield Candidates
                            if GuidedCombinationCount >= 96:
                                break
                        if GuidedCombinationCount >= 96:
                            break
                for Candidates in product(*(Values[ComponentIndex] for Values in CandidateDomainsByComponent)):
                    Fingerprint = tuple((Candidate.CandidateFingerprint for Candidate in Candidates))
                    if Fingerprint in SeenCombinationFingerprints:
                        continue
                    SeenCombinationFingerprints.add(Fingerprint)
                    yield Candidates
            for ComponentIndex in sorted(CommonComponentIndexes):
                for Candidates in OrderedCandidateCombinations(ComponentIndex):
                    CombinationCount += 1
                    HasExternalContinuation = any((Value[0] == Signal for Value in Problem.ExternalContinuationTerminals))
                    if HasExternalContinuation:
                        EgressPaths = (PhysicalPort.LocalPath,) if PhysicalPort is not None else tuple((EgressPath for Attachment in sorted({Candidate.Attachment for Candidate in Candidates}) for EgressPath in BuildComponentEgressPaths(Attachment)))
                    else:
                        EgressPaths = ((),)
                    AdvancedCombination = False
                    for EgressPath in EgressPaths:
                        ProcessedEgressStateCount += 1
                        if ProcessedEgressStateCount <= ResumeEgressStateCount:
                            continue
                        FabricSubtree = _UniqueFabricSubtree(Problem.Fabric, (*(Candidate.Attachment for Candidate in Candidates), *((EgressPath[0],) if EgressPath else ())), Adjacency=FabricAdjacency, ParentCache=FabricParentCache)
                        CanonicalAccessState = _BuildCanonicalAccessCombinationKey(Problem, Signal, Domains, tuple(Candidates), EgressPath, ComponentIndex, FabricSubtree, LocalClaimsBySignal.get(Signal, ())) if FabricSubtree is not None else None
                        if CanonicalAccessState is not None and CanonicalAccessState in SeenCanonicalAccessStates:
                            DuplicateCanonicalAccessStateCount += 1
                            continue
                        if CanonicalAccessState is not None:
                            SeenCanonicalAccessStates.add(CanonicalAccessState)
                            CanonicalAccessStateCount += 1
                        if not AdvancedCombination:
                            if not Advance('net-variant'):
                                NetVariantDiscoveryStateCache[DiscoveryStateKey] = {'Variants': dict(VariantsByFingerprint), 'RejectionCounts': dict(RejectionCounts), 'ImmutableConflictSignals': frozenset(ImmutableConflictSignals), 'ProcessedEgressStateCount': ProcessedEgressStateCount - 1, 'CombinationCount': CombinationCount, 'CanonicalAccessStates': frozenset(SeenCanonicalAccessStates), 'CompleteProofContextFingerprint': CompleteProofContextFingerprint}
                                SolverDiagnostics['VariantDiagnosticsBySignal'] = VariantDiagnosticsBySignal
                                SolverDiagnostics['FabricParentCacheRootCount'] = len(FabricParentCache)
                                return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'work-or-deadline', ExpansionCount)), ExpansionCount=ExpansionCount, Detail='component state work or deadline cap reached', Diagnostics=SolverDiagnostics)
                            AdvancedCombination = True
                        NetVariantBuildCount += 1
                        Variant = _BuildNetVariant(Problem, Signal, Domains, tuple(Candidates), EgressPath, RejectionCounts, ImmutableConflictSignals, FabricAdjacency, FabricParentCache, ImmutableAccessConflictCache, LocalClaimsBySignal, NetVariantTopologyCache, RouteClaimsCache, PrecomputedFabricSubtree=FabricSubtree, ReservedPortContractConflictSignals=ReservedPortContractConflictSignals, ReservedGlobalRouteConflictSignals=ReservedGlobalRouteConflictSignals)
                        if Variant is not None:
                            CompleteTreeMaterializationCount += 1
                            SolverDiagnostics['CompleteTreesMaterialized'] = CompleteTreeMaterializationCount
                            CompleteNetRouteBlockers = frozenset((CompleteSignal for CompleteSignal, CompleteVariants in CompleteProofVariants.items() if all((not ComponentClaimsCompatibleForOwners(Signal, Variant.Claims, CompleteSignal, CompleteVariant.Claims) for CompleteVariant in CompleteVariants))))
                            if CompleteNetRouteBlockers:
                                RejectionCounts['complete-net-route-capacity'] = RejectionCounts.get('complete-net-route-capacity', 0) + 1
                                ImmutableConflictSignals.update(CompleteNetRouteBlockers)
                                continue
                            VariantsByFingerprint.setdefault(Variant.NetFingerprint, Variant)
                            if EffectiveDiscoveryVariantLimit is not None and len(VariantsByFingerprint) >= EffectiveDiscoveryVariantLimit:
                                StopDiscovery = True
                                break
                    if StopDiscovery:
                        break
                if StopDiscovery:
                    break
            EnumeratedVariants = tuple((VariantsByFingerprint[Fingerprint] for Fingerprint in sorted(VariantsByFingerprint)))
            if StopDiscovery:
                DiscoveryIncompleteSignals.add(Signal)
                NetVariantDiscoveryStateCache[DiscoveryStateKey] = {'Variants': dict(VariantsByFingerprint), 'RejectionCounts': dict(RejectionCounts), 'ImmutableConflictSignals': frozenset(ImmutableConflictSignals), 'ProcessedEgressStateCount': ProcessedEgressStateCount, 'CombinationCount': CombinationCount, 'CanonicalAccessStates': frozenset(SeenCanonicalAccessStates), 'CompleteProofContextFingerprint': CompleteProofContextFingerprint}
            else:
                NetVariantDiscoveryStateCache.pop(DiscoveryStateKey, None)
                CachedValue = (EnumeratedVariants, CombinationCount, dict(RejectionCounts), frozenset(ImmutableConflictSignals), ComponentOrigin)
                VariantPortfolioCache[ExactCacheKey] = CachedValue
                if not ImmutableConflictSignals:
                    VariantPortfolioCache[StructuralCacheKey] = CachedValue
        ExternalPositions = tuple((Terminal for ExternalSignal, Terminal, _Role in Problem.ExternalContinuationTerminals if ExternalSignal == Signal))

        def ContinuationCost(Value: RoutedComponentNet) -> int:
            if not ExternalPositions or not Value.ExportedPorts:
                return 0
            return sum((min((abs(Port[0] - Terminal[0]) + abs(Port[1] - Terminal[1]) + abs(Port[2] - Terminal[2]) for Port in Value.ExportedPorts)) for Terminal in ExternalPositions))
        RankedVariants = tuple(sorted(PruneDominatedComponentNetVariants(EnumeratedVariants), key=lambda Value: (ContinuationCost(Value), Value.NetFingerprint)))
        ForbiddenPorts = ForbiddenExportPortsBySignal.get(Signal)
        VariantsBySignal[Signal] = tuple((Value for Value in RankedVariants if ForbiddenPorts is None or Value.ExportedPorts != ForbiddenPorts))
        VariantDiagnosticsBySignal[Signal] = {'PortfolioCacheHit': CachedPortfolio is not None, 'PortfolioCacheKind': PortfolioCacheKind if CachedPortfolio is not None else 'miss', 'StructuralPortfolioFingerprint': StructuralPortfolioFingerprint, 'PortfolioTranslationDelta': list(PortfolioTranslationDelta), 'PortfolioTranslationValidated': PortfolioTranslationValidated, 'ResumedEgressStateCount': ResumeEgressStateCount if CachedPortfolio is None else 0, 'TerminalDomainSizes': [len(Domain.Candidates) for Domain in Domains], 'AccessCombinationCount': CombinationCount, 'GuidedCombinationCount': GuidedCombinationCount if CachedPortfolio is None else 0, 'CanonicalAccessStateCount': CanonicalAccessStateCount, 'DuplicateCanonicalAccessStateCount': DuplicateCanonicalAccessStateCount, 'NetVariantBuildCount': NetVariantBuildCount, 'RoutedVariantCount': len(VariantsBySignal[Signal]), 'EnumeratedPhysicalVariantCount': len(EnumeratedVariants), 'DominatedVariantCount': len(EnumeratedVariants) - len(RankedVariants), 'ForbiddenExportPortVariantCount': len(RankedVariants) - len(VariantsBySignal[Signal]), 'ContinuationCostRange': [min(map(ContinuationCost, VariantsBySignal[Signal])), max(map(ContinuationCost, VariantsBySignal[Signal]))] if VariantsBySignal[Signal] else [], 'DiscoveryPortfolioComplete': Signal not in DiscoveryIncompleteSignals, 'DiscoveryVariantLimit': EffectiveDiscoveryVariantLimit, 'RejectionCounts': dict(sorted(RejectionCounts.items())), 'ImmutableConflictSignals': sorted(ImmutableConflictSignals), 'ReservedPortContractConflictSignals': sorted(ReservedPortContractConflictSignals), 'ReservedGlobalRouteConflictSignals': sorted(ReservedGlobalRouteConflictSignals)}
        if StopAfterCompleteNetVariantPortfolioSignal == Signal:
            SolverDiagnostics['VariantDiagnosticsBySignal'] = VariantDiagnosticsBySignal
            SolverDiagnostics['NetVariantPortfolioSignal'] = Signal
            SolverDiagnostics['NetVariantPortfolioComplete'] = bool(Signal not in DiscoveryIncompleteSignals)
            SolverDiagnostics['NetVariantPortfolioVariantCount'] = len(EnumeratedVariants)
            SolverDiagnostics['TemplateSearchEntered'] = False
            if Signal in DiscoveryIncompleteSignals:
                return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, Signal, 'net-variant-portfolio-incomplete')), ExpansionCount=ExpansionCount, Detail='net variant portfolio discovery is incomplete', Diagnostics=SolverDiagnostics)
            return ComponentRoutingSolveResult(Status='complete-net-variant-portfolio', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, Signal, 'complete-net-variant-portfolio', tuple((Variant.NetFingerprint for Variant in EnumeratedVariants)))), ExpansionCount=ExpansionCount, Detail='complete net variant portfolio compiled', Diagnostics=SolverDiagnostics)
        if not VariantsBySignal[Signal]:
            LocalUnsatCoreSignals = tuple(sorted({Signal, *ImmutableConflictSignals}))
            LocalUnsatCoreComplete = Signal not in DiscoveryIncompleteSignals
            LocalUnsatCoreFingerprint = _StableFingerprint(('local-no-powered-variant-core-v1', StructuralPortfolioFingerprint, CompleteProofContextFingerprint, tuple(sorted(RejectionCounts.items())), LocalUnsatCoreSignals))
            SolverDiagnostics['VariantDiagnosticsBySignal'] = VariantDiagnosticsBySignal
            SolverDiagnostics['StructuralVariantCounts'] = sorted((len(Values) for Values in VariantsBySignal.values()))
            SolverDiagnostics['LocalUnsatSignal'] = Signal
            SolverDiagnostics['LocalUnsatCoreSignals'] = list(LocalUnsatCoreSignals)
            SolverDiagnostics['LocalUnsatCoreComplete'] = LocalUnsatCoreComplete
            SolverDiagnostics['LocalUnsatCoreFingerprint'] = LocalUnsatCoreFingerprint
            return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'no-powered-net-variant', len(Domains), LocalUnsatCoreFingerprint)), ExpansionCount=ExpansionCount, Detail='a component net has no powered fabric tree', Diagnostics=SolverDiagnostics)
        if Signal not in DiscoveryIncompleteSignals:
            CompleteProofVariants[Signal] = VariantsBySignal[Signal]
        if 2 <= len(CompleteProofVariants) <= 4:
            UnsatSubset = FindCompleteComponentNetUnsatSubset(CompleteProofVariants, Advance=lambda: Advance('incremental-complete-net-capacity-proof'))
            if UnsatSubset is None:
                SolverDiagnostics['VariantDiagnosticsBySignal'] = VariantDiagnosticsBySignal
                SolverDiagnostics['IncrementalNetCapacityProofComplete'] = False
                return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'incremental-net-capacity-proof-incomplete', ExpansionCount)), ExpansionCount=ExpansionCount, Detail='component state work or deadline cap reached', Diagnostics=SolverDiagnostics)
            if UnsatSubset:
                StructuralCoreFingerprint = _StableFingerprint(tuple(sorted((tuple((Variant.NetFingerprint for Variant in CompleteProofVariants[CoreSignal])) for CoreSignal in UnsatSubset))))
                SolverDiagnostics['VariantDiagnosticsBySignal'] = VariantDiagnosticsBySignal
                SolverDiagnostics['IncrementalNetCapacityProofComplete'] = True
                SolverDiagnostics['IncrementalNetCapacityUnsatSignals'] = sorted(UnsatSubset)
                SolverDiagnostics['IncrementalNetCapacityCoreFingerprint'] = StructuralCoreFingerprint
                return ComponentRoutingSolveResult(Status='architectural-unsatisfiable', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'complete-net-capacity-subset-unsatisfiable', StructuralCoreFingerprint)), ExpansionCount=ExpansionCount, Detail='a complete subset of component net portfolios cannot share the component fabric', Diagnostics=SolverDiagnostics)
    SolverDiagnostics['VariantDiagnosticsBySignal'] = VariantDiagnosticsBySignal
    SolverDiagnostics['FabricParentCacheRootCount'] = len(FabricParentCache)
    SolverDiagnostics['NetVariantTopologyCacheCount'] = len(NetVariantTopologyCache)
    SolverDiagnostics['RouteClaimsCacheCount'] = len(RouteClaimsCache)
    SolverDiagnostics['StructuralVariantCounts'] = sorted((len(Values) for Values in VariantsBySignal.values()))
    SolverDiagnostics['DiscoveryIncompleteSignals'] = sorted(DiscoveryIncompleteSignals)
    Variables: list[tuple[str, str, str, tuple[Any, ...], Callable[[Any], RoutingResourceClaims], tuple[object, ...]]] = []
    for Signal in Problem.ComponentSignals:
        Options = VariantsBySignal[Signal]
        Variables.append(('net', Signal, Signal, Options, lambda Value: Value.Claims, tuple((Value.NetFingerprint for Value in Options))))
    for DomainIndex, Domain in enumerate(Problem.ExternalContinuationDomains):
        Variables.append(('continuation', str(DomainIndex), Domain.Signal, Domain.Candidates, lambda Value: Value.Claims, tuple((Value.CandidateFingerprint for Value in Domain.Candidates))))
    for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains):
        ForbiddenForeignFingerprints = ForbiddenForeignCandidateFingerprintsBySignal.get(Domain.Signal, frozenset())
        Options = tuple((Candidate for Candidate in Domain.Candidates if Candidate.CandidateFingerprint not in ForbiddenForeignFingerprints))
        Variables.append(('foreign', str(DomainIndex), Domain.Signal, Options, lambda Value: Value.Claims, tuple((Value.CandidateFingerprint for Value in Options))))
    for DomainIndex, Domain in enumerate(Problem.ForeignTransitDomains):
        if Domain.Signal not in RequiredForeignTransitSignals:
            continue
        Variables.append(('transit', str(DomainIndex), Domain.Signal, Domain.Candidates, lambda Value: Value.Claims, tuple((Value.NetFingerprint for Value in Domain.Candidates))))
    SelectedValues: dict[tuple[str, str], Any] = {}
    SelectedClaims: list[tuple[str, RoutingResourceClaims]] = [(Claim.Signal, Claim.Claims) for Claim in Problem.LocalClaims if Claim.Signal not in Problem.ComponentSignals] + [(Claim.Signal, Claim.Claims) for Claim in Problem.ImmutableClaims]
    CapacityEmptyDomainCounts: dict[str, int] = {}
    CapacityEmptyDomainWitnesses: dict[str, tuple[str, ...]] = {}
    OptionClaims = tuple((tuple((ClaimsFor(Option) for Option in Options)) for _Kind, _Identity, _Owner, Options, ClaimsFor, _Structural in Variables))
    CompatibilityCache: dict[tuple[int, int, int], tuple[int, ...]] = {}
    ArcConsistencyRevisionCount = 0
    ArcConsistencyRemovedOptionCount = 0

    def CompatibleOptionIndexes(VariableIndex: int, OptionIndex: int, OtherIndex: int) -> tuple[int, ...]:
        Key = (VariableIndex, OptionIndex, OtherIndex)
        Cached = CompatibilityCache.get(Key)
        if Cached is not None:
            return Cached
        Owner = Variables[VariableIndex][2]
        OtherOwner = Variables[OtherIndex][2]
        Claims = OptionClaims[VariableIndex][OptionIndex]
        Result = tuple((Index for Index, OtherClaims in enumerate(OptionClaims[OtherIndex]) if ComponentClaimsCompatibleForOwners(Owner, Claims, OtherOwner, OtherClaims)))
        CompatibilityCache[Key] = Result
        return Result

    def EnforceArcConsistency(Domains: dict[int, tuple[int, ...]]) -> dict[int, tuple[int, ...]] | None:
        """Enforce exact binary claim support across all remaining domains."""
        nonlocal ArcConsistencyRevisionCount
        nonlocal ArcConsistencyRemovedOptionCount
        Result = dict(Domains)
        Queue = deque(((FirstIndex, SecondIndex) for FirstIndex in Result for SecondIndex in Result if FirstIndex != SecondIndex))
        while Queue:
            FirstIndex, SecondIndex = Queue.popleft()
            FirstDomain = Result[FirstIndex]
            SecondDomainSet = frozenset(Result[SecondIndex])
            Retained = []
            for OptionOffset, OptionIndex in enumerate(FirstDomain):
                ArcConsistencyRevisionCount += 1
                if ArcConsistencyRevisionCount % 128 == 0 and (not Advance('component-arc-consistency')):
                    return None
                if SecondDomainSet.intersection(CompatibleOptionIndexes(FirstIndex, OptionIndex, SecondIndex)):
                    Retained.append(OptionIndex)
            RetainedDomain = tuple(Retained)
            if RetainedDomain == FirstDomain:
                continue
            ArcConsistencyRemovedOptionCount += len(FirstDomain) - len(RetainedDomain)
            if not RetainedDomain:
                Result[FirstIndex] = ()
                FirstKind, FirstIdentity = (Variables[FirstIndex][0], Variables[FirstIndex][1])
                SecondKind, SecondIdentity = (Variables[SecondIndex][0], Variables[SecondIndex][1])
                EmptyKey = f'{FirstKind}:{FirstIdentity}'
                CapacityEmptyDomainCounts[EmptyKey] = CapacityEmptyDomainCounts.get(EmptyKey, 0) + 1
                CapacityEmptyDomainWitnesses.setdefault(EmptyKey, (f'{SecondKind}:{SecondIdentity}',))
                return Result
            Result[FirstIndex] = RetainedDomain
            Queue.extend(((OtherIndex, FirstIndex) for OtherIndex in Result if OtherIndex != FirstIndex and OtherIndex != SecondIndex))
        return Result
    InitialDomains: dict[int, tuple[int, ...]] = {}
    for VariableIndex, Variable in enumerate(Variables):
        Owner = Variable[2]
        InitialDomains[VariableIndex] = tuple((OptionIndex for OptionIndex, Claims in enumerate(OptionClaims[VariableIndex]) if not any((not ComponentClaimsCompatibleForOwners(Owner, Claims, ImmutableOwner, ImmutableClaims) for ImmutableOwner, ImmutableClaims in SelectedClaims))))
    ArcConsistentInitialDomains = EnforceArcConsistency(InitialDomains)
    if ArcConsistentInitialDomains is None:
        SolverDiagnostics['ArcConsistencyComplete'] = False
        SolverDiagnostics['ArcConsistencyRevisionCount'] = ArcConsistencyRevisionCount
        return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'initial-arc-consistency-incomplete', ExpansionCount)), ExpansionCount=ExpansionCount, Detail='component state work or deadline cap reached', Diagnostics=SolverDiagnostics)
    InitialDomains = ArcConsistentInitialDomains
    FailedDomainStates: set[tuple[tuple[int, tuple[int, ...]], ...]] = set()
    ForbiddenPairForeignVariableIdentities = frozenset((str(DomainIndex) for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains) if any((any((Signal == Domain.Signal and Terminal == Domain.Terminal for Signal, Terminal, _Fingerprint in ForbiddenPair)) for ForbiddenPair in ForbiddenForeignAssignmentPairs))))
    FeedbackConstrainedForeignVariableIdentities = frozenset({*ForbiddenPairForeignVariableIdentities, *(str(DomainIndex) for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains) if ForbiddenForeignCandidateFingerprintsBySignal.get(Domain.Signal, frozenset()))})

    def SelectedForeignAssignments() -> frozenset[tuple[str, Position3, str]]:
        return frozenset(((Domain.Signal, Domain.Terminal, Value.CandidateFingerprint) for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains) if (Value := SelectedValues.get(('foreign', str(DomainIndex)))) is not None))

    def ViolatesForbiddenForeignPair() -> bool:
        Selected = SelectedForeignAssignments()
        return any((ForbiddenPair <= Selected for ForbiddenPair in ForbiddenForeignAssignmentPairs))

    def SelectVariables(RemainingDomains: dict[int, tuple[int, ...]]) -> bool:
        if not RemainingDomains:
            if ViolatesForbiddenForeignPair():
                SolverDiagnostics['RejectedForeignAssignmentPairCount'] = int(SolverDiagnostics.get('RejectedForeignAssignmentPairCount', 0)) + 1
                return False
            AssignmentFingerprint = _StableFingerprint(tuple(sorted(((Kind, Identity, Value.NetFingerprint if Kind in {'net', 'transit'} else Value.CandidateFingerprint) for (Kind, Identity), Value in SelectedValues.items()))))
            if AssignmentFingerprint in ForbiddenAssignmentFingerprints:
                SolverDiagnostics['RejectedAssignmentFingerprintCount'] = int(SolverDiagnostics.get('RejectedAssignmentFingerprintCount', 0)) + 1
                return False
            SolverDiagnostics['SelectedAssignmentFingerprint'] = AssignmentFingerprint
            return True
        DomainState = tuple(sorted(RemainingDomains.items()))
        if DomainState in FailedDomainStates:
            return False
        Ranked = []
        for VariableIndex, Domain in RemainingDomains.items():
            if not Domain:
                Kind, Identity, _Owner, _Values, _ClaimsFor, _Structural = Variables[VariableIndex]
                Key = f'{Kind}:{Identity}'
                CapacityEmptyDomainCounts[Key] = CapacityEmptyDomainCounts.get(Key, 0) + 1
                CapacityEmptyDomainWitnesses.setdefault(Key, tuple(sorted((f'{SelectedKind}:{SelectedIdentity}' for SelectedKind, SelectedIdentity in SelectedValues))))
                return False
            Kind, _Identity, _Owner, _Values, _ClaimsFor, Structural = Variables[VariableIndex]
            Ranked.append((0 if Kind == 'foreign' and _Identity in FeedbackConstrainedForeignVariableIdentities else 1 if Kind in {'net', 'transit'} else 2 if Kind == 'continuation' else 3, len(Domain), tuple((Structural[Index] for Index in Domain)), VariableIndex, Domain))
        _KindOrder, _OptionCount, _Structural, SelectedIndex, Domain = min(Ranked)
        Kind, Identity, Owner, _Values, ClaimsFor, _Fingerprints = Variables[SelectedIndex]
        for OptionIndex in Domain:
            Option = Variables[SelectedIndex][3][OptionIndex]
            if not Advance('simultaneous-component-capacity'):
                return False
            NextDomains: dict[int, tuple[int, ...]] = {}
            ForwardLegal = True
            for OtherIndex, OtherDomain in RemainingDomains.items():
                if OtherIndex == SelectedIndex:
                    continue
                Compatible = frozenset(CompatibleOptionIndexes(SelectedIndex, OptionIndex, OtherIndex))
                Filtered = tuple((Index for Index in OtherDomain if Index in Compatible))
                if not Filtered:
                    OtherKind, OtherIdentity = (Variables[OtherIndex][0], Variables[OtherIndex][1])
                    Key = f'{OtherKind}:{OtherIdentity}'
                    CapacityEmptyDomainCounts[Key] = CapacityEmptyDomainCounts.get(Key, 0) + 1
                    CapacityEmptyDomainWitnesses.setdefault(Key, tuple(sorted((*(f'{SelectedKind}:{SelectedIdentity}' for SelectedKind, SelectedIdentity in SelectedValues), f'{Kind}:{Identity}'))))
                    ForwardLegal = False
                    break
                NextDomains[OtherIndex] = Filtered
            if not ForwardLegal:
                continue
            ArcConsistentDomains = EnforceArcConsistency(NextDomains)
            if ArcConsistentDomains is None:
                return False
            if not ArcConsistentDomains and NextDomains:
                continue
            NextDomains = ArcConsistentDomains
            SelectedValues[Kind, Identity] = Option
            if ViolatesForbiddenForeignPair():
                SolverDiagnostics['RejectedForeignAssignmentPairCount'] = int(SolverDiagnostics.get('RejectedForeignAssignmentPairCount', 0)) + 1
                del SelectedValues[Kind, Identity]
                continue
            SelectedClaims.append((Owner, ClaimsFor(Option)))
            if SelectVariables(NextDomains):
                return True
            SelectedClaims.pop()
            del SelectedValues[Kind, Identity]
        FailedDomainStates.add(DomainState)
        return False
    Feasible = SelectVariables(InitialDomains)
    HitLimit = bool(ExpansionCount > Problem.MaximumWork or (DeadlineSeconds is not None and monotonic() - Started >= DeadlineSeconds))
    SolverDiagnostics['CapacityEmptyDomainCounts'] = dict(sorted(CapacityEmptyDomainCounts.items()))
    SolverDiagnostics['CapacityEmptyDomainWitnesses'] = dict(sorted(CapacityEmptyDomainWitnesses.items()))
    SolverDiagnostics['FailedDomainStateCount'] = len(FailedDomainStates)
    SolverDiagnostics['ArcConsistencyComplete'] = True
    SolverDiagnostics['ArcConsistencyRevisionCount'] = ArcConsistencyRevisionCount
    SolverDiagnostics['ArcConsistencyRemovedOptionCount'] = ArcConsistencyRemovedOptionCount
    SolverDiagnostics['ExpansionCount'] = ExpansionCount
    if not Feasible:
        if DiscoveryIncompleteSignals and (not HitLimit):
            return ComponentRoutingSolveResult(Status='incomplete', ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, 'discovery-needs-exhaustive-retry', tuple(sorted(DiscoveryIncompleteSignals)))), ExpansionCount=ExpansionCount, Detail='bounded discovery portfolio needs exhaustive retry', Diagnostics={**SolverDiagnostics, 'DiscoveryNeedsExhaustiveRetry': True})
        Status = 'incomplete' if HitLimit else 'architectural-unsatisfiable'
        return ComponentRoutingSolveResult(Status=Status, ProofFingerprint=_StableFingerprint((Problem.ProblemFingerprint, Status, ExpansionCount, tuple((len(VariantsBySignal[Signal]) for Signal in Problem.ComponentSignals)))), ExpansionCount=ExpansionCount, Detail='component state work or deadline cap reached' if HitLimit else 'complete finite component state space exhausted', Diagnostics=SolverDiagnostics)
    Nets = tuple(sorted((SelectedValues['net', Signal] for Signal in Problem.ComponentSignals), key=lambda Value: Value.NetFingerprint))
    Foreign = tuple(sorted(((Domain.Signal, Domain.Terminal, SelectedValues['foreign', str(DomainIndex)]) for DomainIndex, Domain in enumerate(Problem.ForeignEscapeDomains)), key=lambda Value: (Value[2].CandidateFingerprint, Value[1])))
    ExternalContinuations = tuple(sorted(((Domain.Signal, Domain.Terminal, SelectedValues['continuation', str(DomainIndex)]) for DomainIndex, Domain in enumerate(Problem.ExternalContinuationDomains)), key=lambda Value: (Value[0], Value[1], Value[2].CandidateFingerprint)))
    ForeignTransits = tuple(sorted((SelectedValues['transit', str(DomainIndex)] for DomainIndex, _Domain in enumerate(Problem.ForeignTransitDomains) if ('transit', str(DomainIndex)) in SelectedValues), key=lambda Value: Value.NetFingerprint))
    Claims = _MergeClaims((*(Value.Claims for Value in Nets), *(Value[2].Claims for Value in ExternalContinuations), *(Value[2].Claims for Value in Foreign), *(Value.Claims for Value in ForeignTransits)))
    ExportedPorts = tuple(sorted(((Net.Signal, Position) for Net in Nets for Position in Net.ExportedPorts)))
    ExportedPortFingerprint = _StableFingerprint(tuple(_RelativeGeometry((Position for _Signal, Position in ExportedPorts))))
    ClaimsFingerprint = _ClaimsFingerprint(Claims)
    RoutedTemplateFingerprint = _StableFingerprint((Problem.ProblemFingerprint, tuple((Net.NetFingerprint for Net in Nets)), tuple((Value[2].CandidateFingerprint for Value in Foreign)), tuple((Value[2].CandidateFingerprint for Value in ExternalContinuations)), tuple((Value.NetFingerprint for Value in ForeignTransits)), ExportedPortFingerprint, ClaimsFingerprint))
    ProofFingerprint = _StableFingerprint((RoutedTemplateFingerprint, ExpansionCount, 'feasible'))
    Template = RoutedComponentTemplate(ProblemFingerprint=Problem.ProblemFingerprint, PlacementFingerprint=Problem.PlacementFingerprint, LocalTemplateFingerprint=Problem.LocalTemplateFingerprint, FabricFingerprint=Problem.Fabric.FabricFingerprint, RoutedTemplateFingerprint=RoutedTemplateFingerprint, Nets=Nets, ForeignEscapeReservations=Foreign, ExportedPorts=ExportedPorts, Claims=Claims, ExportedPortFingerprint=ExportedPortFingerprint, ClaimsFingerprint=ClaimsFingerprint, ProofFingerprint=ProofFingerprint, ExpansionCount=ExpansionCount, Diagnostics=SolverDiagnostics, ExternalContinuationReservations=ExternalContinuations, ForeignTransitReservations=ForeignTransits, InterfaceFingerprint=Problem.Interface.InterfaceFingerprint if Problem.Interface is not None else '')
    return ComponentRoutingSolveResult(Status='feasible', Template=Template, ProofFingerprint=ProofFingerprint, ExpansionCount=ExpansionCount, Diagnostics=SolverDiagnostics)

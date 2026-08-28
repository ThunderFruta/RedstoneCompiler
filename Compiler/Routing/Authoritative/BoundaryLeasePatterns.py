"""Cohesive preparation and finalization phases for one domain."""

from __future__ import annotations

from ..Contracts.Placement import ClusterInterfaceAssignment
from ..Failures import RoutingFailure
from ..Failures import RoutingFailureReason
from ..Failures import RoutingStageError
from ..ResourceGraph import FindClaimConflicts
from ..ResourceGraph import FindSelfClaimConflicts
from .AssignmentState import _ClaimsConflict
from .CandidateCache import BuildClusterInterfaceAccessDomainFingerprint
from functools import partial
from .BoundaryLeaseState import BoundaryLeaseReturn, BoundaryLeaseState, SetBoundaryLeaseState
from .BoundaryLeaseHelpers import AccessPatternFingerprint, AccessPatternIsAdmissible, AddJointCutPatterns, BuildConflictComponents, BuildZeroDomainConflictGraph, CandidateOffset, ClusterInterfaceAccessPattern, Compatible, CompatiblePatternIndices, CompleteForwardDomain, CompleteOrderedValues, CompleteSelectionAdmissible, ComponentSatisfiable, DiverseTerminalDomain, LegacyCompatible, MergePatternClaims, PatternSelectionFingerprint, Search, SearchBundleComponent, SearchCompleteDomains, SearchLegacy, SelectLayerSignatureDiverseIndices
from ..Contracts.Core import Position3
from ..ResourceGraph import PinAccessPortal
from ..ResourceGraph import PortalReservation
from ..ResourceGraph import RoutingResourceClaims
from .CandidateCache import BuildClusterInterfaceAccessDomainFingerprint, BuildClusterInterfaceReservationAssignmentFingerprint

def BuildBoundaryLeasePatterns(Context):
    Context.PatternsBySignal: dict[str, tuple[ClusterInterfaceAccessPattern, ...]] = {}
    Context.SelfConflictPatternRejectionCounts: dict[str, int] = {}
    Context.BundleConflictEdges: set[tuple[str, str]] = set()
    for Context.Signal in Context.BundleSignals:
        Context.Terminals = tuple((Key for Key in Context.Order if Key[0] == Context.Signal))
        Context.CandidateDomains = tuple((DiverseTerminalDomain(Context, Key) for Key in Context.Terminals))
        Context.CandidateIndexBeam: list[tuple[int, ...]] = [()]
        Context.CandidateIndexBeamWidth = max(Context.MaximumPatternsPerSignal, min(256, Context.MaximumPatternsPerSignal * 4))
        for Context.Values in Context.CandidateDomains:
            Context.ExpandedCandidateIndices = [(*Prefix, CandidateIndex) for Prefix in Context.CandidateIndexBeam for CandidateIndex in range(len(Context.Values))]
            Context.SelfLegalCandidateIndices = []
            for Context.Indices in Context.ExpandedCandidateIndices:
                Context.PartialValues = tuple((Context.CandidateDomains[Index][CandidateIndex] for Index, CandidateIndex in enumerate(Context.Indices)))
                if FindSelfClaimConflicts({Context.Signal: MergePatternClaims(Context, Context.PartialValues)}):
                    Context.SelfConflictPatternRejectionCounts[Context.Signal] = Context.SelfConflictPatternRejectionCounts.get(Context.Signal, 0) + 1
                    continue
                Context.SelfLegalCandidateIndices.append(Context.Indices)
            Context.CandidateIndexBeam = SelectLayerSignatureDiverseIndices(Context, Context.SelfLegalCandidateIndices, Context.CandidateIndexBeamWidth)
            if not Context.CandidateIndexBeam:
                break
        Context.CandidateIndices = SelectLayerSignatureDiverseIndices(Context, Context.CandidateIndexBeam, Context.MaximumPatternsPerSignal)
        Context.Patterns = []
        for Context.Indices in Context.CandidateIndices:
            Context.Values = tuple((Context.CandidateDomains[Index][CandidateIndex] for Index, CandidateIndex in enumerate(Context.Indices)))
            Context.Claims = MergePatternClaims(Context, Context.Values)
            Context.Patterns.append(ClusterInterfaceAccessPattern(Signal=Context.Signal, Selections=tuple(zip(Context.Terminals, Context.Values)), Claims=Context.Claims, Score=(len({Value[1] for Value in Context.Values}) - 1, max((Value[1] for Value in Context.Values)) - min((Value[1] for Value in Context.Values)) if Context.Values else 0, sum(((Value[1] - Context.PreferredLayerByTerminal[Context.Terminals[Index]]) % max(1, Context.LayerCount) for Index, Value in enumerate(Context.Values))) if Context.DiversifyLayers else 0, sum((Value[0] for Value in Context.Values)), tuple((Value[1] for Value in Context.Values)), tuple((Value[2].PortalId for Value in Context.Values)))))
        Context.RequiredSignalReservations = Context.RequiredReservationsBySignal.get(Context.Signal, ())
        if Context.RequiredSignalReservations:
            Context.RequiredSelections = []
            for Context.Reservation in Context.RequiredSignalReservations:
                Context.Key = (Context.Signal, Context.Reservation.Terminal)
                Context.MatchingValues = tuple((Value for Value in Context.Domains.get(Context.Key, ()) if Value[1] == Context.Reservation.Layer and tuple(Value[2].Path) == tuple(Context.Reservation.FirstSegment) and (Value[3] == Context.Reservation.Claims)))
                if not Context.MatchingValues:
                    Context.RequiredSelections = []
                    break
                Context.RequiredSelections.append((Context.Key, min(Context.MatchingValues, key=lambda Value: (Value[0], Value[1], Value[2].PortalId))))
            if Context.RequiredSelections:
                Context.RequiredValues = tuple((Value for _Key, Value in Context.RequiredSelections))
                Context.RequiredPattern = ClusterInterfaceAccessPattern(Signal=Context.Signal, Selections=tuple(Context.RequiredSelections), Claims=MergePatternClaims(Context, Context.RequiredValues), Score=(-1, 0, 0, sum((Value[0] for Value in Context.RequiredValues)), tuple((Value[1] for Value in Context.RequiredValues)), tuple((Value[2].PortalId for Value in Context.RequiredValues))))
                Context.RequiredFingerprint = AccessPatternFingerprint(Context, Context.RequiredPattern.Selections)
                if all((AccessPatternFingerprint(Context, Pattern.Selections) != Context.RequiredFingerprint for Pattern in Context.Patterns)):
                    Context.Patterns.append(Context.RequiredPattern)
        Context.Offset = CandidateOffset(Context, Context.Signal, len(Context.Patterns))
        Context.OrderedPatterns = tuple(sorted(Context.Patterns, key=lambda Value: Value.Score))
        if not Context.OrderedPatterns:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.BoundaryEscapeInfeasible, Stage='ClusterBoundaryLease', AffectedNets=(Context.Signal,), Detail='cluster interface has no complete pin-access pattern', Diagnostics={'ReservationPurpose': 'cluster-boundary-lease', 'Signal': Context.Signal, 'TerminalCount': len(Context.Terminals), 'TerminalDomainSizes': [len(Values) for Values in Context.CandidateDomains], 'CandidateCounts': {Context.Signal: 0}, 'ConflictGraph': BuildZeroDomainConflictGraph(Context, Context.Signal)}))
        Context.OrderedPatterns = tuple((Pattern for Pattern in Context.OrderedPatterns if AccessPatternIsAdmissible(Context, Context.Signal, Pattern.Selections)))
        if not Context.OrderedPatterns:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.BoundaryEscapeInfeasible, Stage='ClusterBoundaryLease', AffectedNets=(Context.Signal,), Detail='candidate-realizability nogoods exhausted every bounded cluster-interface access pattern', Diagnostics={'ReservationPurpose': 'cluster-boundary-lease', 'Signal': Context.Signal, 'CandidateRealizabilityNogoodCount': len(Context.NogoodPatternFingerprintsBySignal.get(Context.Signal, frozenset())), 'RequiredPatternFingerprint': Context.RequiredPatternFingerprintsBySignal.get(Context.Signal, ''), 'ConflictGraph': BuildZeroDomainConflictGraph(Context, Context.Signal)}))
        Context.Offset = CandidateOffset(Context, Context.Signal, len(Context.OrderedPatterns))
        Context.PatternsBySignal[Context.Signal] = (*Context.OrderedPatterns[Context.Offset:], *Context.OrderedPatterns[:Context.Offset])
    Context.SelectedPatterns: dict[str, ClusterInterfaceAccessPattern] = {}
    Context.PatternIndexByIdentity = {Signal: {id(Pattern): Index for Index, Pattern in enumerate(Patterns)} for Signal, Patterns in Context.PatternsBySignal.items()}
    Context.CompatiblePatternIndexCache: dict[tuple[str, int, str], frozenset[int]] = {}
    Context.JointCutSearchDiagnostics: list[dict[str, object]] = []
    Context.ExpandedJointCutFingerprints: set[tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = set()
    Context.ConservativeAdjacency = {Signal: set() for Signal in Context.BundleSignals}
    Context.DeferredInitialFrontierEdges: set[tuple[str, str]] = set()
    for Context.SignalIndex, Context.FirstSignal in enumerate(Context.BundleSignals):
        Context.FirstPattern = Context.PatternsBySignal[Context.FirstSignal][0]
        for Context.SecondSignal in Context.BundleSignals[Context.SignalIndex + 1:]:
            Context.SecondPattern = Context.PatternsBySignal[Context.SecondSignal][0]
            if not _ClaimsConflict(Context.FirstSignal, Context.FirstPattern.Claims, Context.SecondSignal, Context.SecondPattern.Claims):
                continue
            if bool(Context.PriorityInterfaceCutSignals) and (Context.FirstSignal in Context.PriorityInterfaceCutSignals) != (Context.SecondSignal in Context.PriorityInterfaceCutSignals):
                Context.DeferredInitialFrontierEdges.add(tuple(sorted((Context.FirstSignal, Context.SecondSignal))))
                continue
            Context.ConservativeAdjacency[Context.FirstSignal].add(Context.SecondSignal)
            Context.ConservativeAdjacency[Context.SecondSignal].add(Context.FirstSignal)
    for Context.FirstSignal in Context.PriorityInterfaceCutSignals:
        for Context.SecondSignal in Context.PriorityInterfaceCutSignals:
            if Context.FirstSignal >= Context.SecondSignal:
                continue
            Context.ConservativeAdjacency[Context.FirstSignal].add(Context.SecondSignal)
            Context.ConservativeAdjacency[Context.SecondSignal].add(Context.FirstSignal)
    Context.Components = BuildConflictComponents(Context)

def SearchBoundaryLeasePatterns(Context):
    Context.BundlePatternAssignmentComplete = False
    Context.FailedComponentSignals: tuple[str, ...] = ()
    for Context._Refinement in range(max(1, len(Context.BundleSignals))):
        Context.Components = BuildConflictComponents(Context)
        Context.SelectedPatterns.clear()
        Context.ComponentSearchComplete = True
        for Context.ComponentIndex, Context.Component in enumerate(Context.Components):
            if SearchBundleComponent(Context, {Signal: Context.PatternsBySignal[Signal] for Signal in Context.Component}, Context.ComponentIndex, set()):
                continue
            Context.ComponentSearchComplete = False
            Context.FailedComponentSignals = Context.Component
            break
        if not Context.ComponentSearchComplete:
            Context.ProvisionalPairEdges = tuple(sorted((tuple(sorted((FirstSignal, SecondSignal))) for SignalIndex, FirstSignal in enumerate(Context.FailedComponentSignals) for SecondSignal in Context.FailedComponentSignals[SignalIndex + 1:] if all((not CompatiblePatternIndices(Context, Pattern, SecondSignal if Pattern.Signal == FirstSignal else FirstSignal) for Pattern in Context.PatternsBySignal[FirstSignal])))))
            Context.FixedAccessEdgeSet = set(Context.FixedAccessIncompatibleEdges)
            Context.PortalOnlyPairEdges = tuple((Edge for Edge in Context.ProvisionalPairEdges if Edge not in Context.FixedAccessEdgeSet))
            Context.PortalOnlyCutSignals = tuple(sorted({Signal for Edge in Context.PortalOnlyPairEdges for Signal in Edge}))
            Context.FixedAccessSignals = {Signal for Edge in Context.FixedAccessIncompatibleEdges for Signal in Edge}
            Context.HigherOrderPortalOnlyCut = not Context.ProvisionalPairEdges and 2 < len(Context.FailedComponentSignals) <= 12 and (not set(Context.FailedComponentSignals) & Context.FixedAccessSignals)
            Context.ExactCutSignals = Context.PortalOnlyCutSignals if Context.PortalOnlyPairEdges else Context.FailedComponentSignals if Context.HigherOrderPortalOnlyCut else ()
            if Context.ExactCutSignals and AddJointCutPatterns(Context, Context.ExactCutSignals, Context.PortalOnlyPairEdges):
                continue
            break
        Context.ActualConflictEdges = {tuple(sorted((FirstSignal, SecondSignal))) for SignalIndex, FirstSignal in enumerate(Context.BundleSignals) for SecondSignal in Context.BundleSignals[SignalIndex + 1:] if _ClaimsConflict(FirstSignal, Context.SelectedPatterns[FirstSignal].Claims, SecondSignal, Context.SelectedPatterns[SecondSignal].Claims)}
        if not Context.ActualConflictEdges:
            Context.BundlePatternAssignmentComplete = True
            break
        Context.BundleConflictEdges.update(Context.ActualConflictEdges)
        Context.NewEdgeCount = 0
        for Context.FirstSignal, Context.SecondSignal in Context.ActualConflictEdges:
            if Context.SecondSignal in Context.ConservativeAdjacency[Context.FirstSignal]:
                continue
            Context.ConservativeAdjacency[Context.FirstSignal].add(Context.SecondSignal)
            Context.ConservativeAdjacency[Context.SecondSignal].add(Context.FirstSignal)
            Context.NewEdgeCount += 1
        if Context.NewEdgeCount == 0:
            break
    if Context.BundlePatternAssignmentComplete:
        Context.Selected = {Key: Value for Pattern in Context.SelectedPatterns.values() for Key, Value in Pattern.Selections}
    else:
        Context.PairScanSignals = Context.FailedComponentSignals if Context.FailedComponentSignals else Context.BundleSignals
        Context.PairScanEdges = {tuple(sorted((FirstSignal, SecondSignal))) for SignalIndex, FirstSignal in enumerate(Context.PairScanSignals) for SecondSignal in Context.PairScanSignals[SignalIndex + 1:]}
        Context.UnavoidableEdges = tuple(sorted((Edge for Edge in Context.PairScanEdges if all((not CompatiblePatternIndices(Context, Pattern, Edge[1] if Pattern.Signal == Edge[0] else Edge[0]) for Pattern in Context.PatternsBySignal[Edge[0]])))))
        Context.UnavoidablePairWitnesses = []
        for Context.FirstSignal, Context.SecondSignal in Context.UnavoidableEdges:
            Context.BestWitness = None
            Context.BestOrder = None
            for Context.FirstPattern in Context.PatternsBySignal[Context.FirstSignal]:
                for Context.SecondPattern in Context.PatternsBySignal[Context.SecondSignal]:
                    Context.Conflicts = FindClaimConflicts({Context.FirstSignal: Context.FirstPattern.Claims, Context.SecondSignal: Context.SecondPattern.Claims})
                    Context.ConflictResourceIds = tuple(sorted(Context.Conflicts, key=str))
                    Context.ConflictResources = tuple(map(str, Context.ConflictResourceIds))
                    Context.WitnessOrder = (len(Context.ConflictResources), Context.ConflictResources, Context.FirstPattern.Score, Context.SecondPattern.Score)
                    if Context.BestOrder is not None and Context.WitnessOrder >= Context.BestOrder:
                        continue
                    Context.BestOrder = Context.WitnessOrder
                    Context.BestWitness = {'Signals': [Context.FirstSignal, Context.SecondSignal], 'ConflictResourceCount': len(Context.ConflictResources), 'ConflictResources': list(Context.ConflictResources[:16]), 'ConflictingTerminals': sorted({Terminal for Pattern in (Context.FirstPattern, Context.SecondPattern) for (_Signal, Terminal), Value in Pattern.Selections if Value[3].ResourceIds & frozenset(Context.ConflictResourceIds)}), 'Selections': {Pattern.Signal: [{'Terminal': list(Terminal), 'Layer': Value[1], 'PortalId': Value[2].PortalId, 'Path': [list(Position) for Position in Value[2].Path]} for (_Signal, Terminal), Value in Pattern.Selections] for Pattern in (Context.FirstPattern, Context.SecondPattern)}}
            if Context.BestWitness is not None:
                Context.UnavoidablePairWitnesses.append(Context.BestWitness)
        Context.PriorityRelocationTerminals = tuple(sorted({tuple(map(int, Terminal)) for Witness in Context.UnavoidablePairWitnesses for Terminal in Witness['ConflictingTerminals']}))
        Context.ProvenPairSignals = {Signal for Edge in Context.UnavoidableEdges for Signal in Edge}
        Context.ReducedHigherOrderCore = tuple(Context.PairScanSignals)
        Context.CoreShrinkExpansionCount = 0
        Context.CoreShrinkBudgetExhausted = False
        Context.PriorityCoreProvenUnsatisfiable = False
        Context.PriorityCore = tuple((Signal for Signal in Context.ReducedHigherOrderCore if Signal in Context.PriorityInterfaceCutSignals))
        if not Context.UnavoidableEdges and 2 <= len(Context.PriorityCore) < len(Context.ReducedHigherOrderCore) and (Context.ExpansionCount < Context.MaximumExpansions):
            Context.PriorityCoreSatisfiable = ComponentSatisfiable(Context, Context.PriorityCore)
            if Context.PriorityCoreSatisfiable is False:
                Context.ReducedHigherOrderCore = Context.PriorityCore
                Context.PriorityCoreProvenUnsatisfiable = True
        if not Context.UnavoidableEdges and 2 < len(Context.ReducedHigherOrderCore) and (Context.ExpansionCount < Context.MaximumExpansions):
            for Context.RemovedSignal in tuple(Context.ReducedHigherOrderCore):
                Context.TrialCore = tuple((Signal for Signal in Context.ReducedHigherOrderCore if Signal != Context.RemovedSignal))
                if len(Context.TrialCore) < 2:
                    break
                Context.TrialSatisfiable = ComponentSatisfiable(Context, Context.TrialCore)
                if Context.TrialSatisfiable is None:
                    break
                if not Context.TrialSatisfiable:
                    Context.ReducedHigherOrderCore = Context.TrialCore
        Context.Affected = tuple(sorted(Context.ProvenPairSignals or Context.ReducedHigherOrderCore or Context.FailedCut))
        Context.CutAccessDomainFingerprint = BuildClusterInterfaceAccessDomainFingerprint(Context.Domains, frozenset(Context.Affected))
        Context.SearchConflictSignals = tuple(sorted({*Context.FailedCut, *(Signal for Edge in Context.UnavoidableEdges for Signal in Edge)}))
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.BoundaryEscapeInfeasible, Stage='ClusterBoundaryLease', AffectedNets=Context.Affected, RepairActions=('RelocateAffectedClusters',), Detail='no complete cluster-interface access pattern satisfies capacity-one ownership', Diagnostics={'ReservationPurpose': 'cluster-boundary-lease', 'AuthoritativeAccessDomainFingerprint': Context.AccessDomainFingerprint, 'InterfaceAssignment': ClusterInterfaceAssignment(Problem=Context.InterfaceProblem, Feasible=False, UnsatisfiedTerminalCount=len(Context.Affected)).ToDictionary(), 'AuthoritativeCutAccessDomainFingerprint': Context.CutAccessDomainFingerprint, 'ExpansionCount': Context.ExpansionCount, 'MaximumExpansions': Context.MaximumExpansions, 'LeaseTerminalCount': len(Context.Order), 'LayerDiversification': Context.DiversifyLayers, 'ClusterInterfacePatternSearch': {'Applied': True, 'Complete': False, 'SignalCount': len(Context.BundleSignals), 'ConflictComponentSizes': [len(Component) for Component in Context.Components], 'PatternCounts': {Signal: len(Patterns) for Signal, Patterns in sorted(Context.PatternsBySignal.items())}, 'SelfConflictPatternRejectionCounts': dict(sorted(Context.SelfConflictPatternRejectionCounts.items())), 'MaximumPatternsPerSignal': Context.MaximumPatternsPerSignal, 'ConflictEdges': [list(Edge) for Edge in sorted(Context.BundleConflictEdges)], 'UnavoidablePairEdges': [list(Edge) for Edge in Context.UnavoidableEdges], 'UnavoidablePairWitnesses': Context.UnavoidablePairWitnesses, 'FixedAccessIncompatibleEdges': [list(Edge) for Edge in Context.FixedAccessIncompatibleEdges], 'FixedAccessConflictResourceCount': len(Context.FixedAccessConflicts), 'ReducedHigherOrderCore': list(Context.ReducedHigherOrderCore), 'CoreShrinkExpansionCount': Context.CoreShrinkExpansionCount, 'CoreShrinkComplete': bool(not Context.CoreShrinkBudgetExhausted), 'PriorityInterfaceCutSignals': sorted(Context.PriorityInterfaceCutSignals), 'DeferredInitialFrontierEdges': [list(Edge) for Edge in sorted(Context.DeferredInitialFrontierEdges)], 'PriorityCoreProvenUnsatisfiable': Context.PriorityCoreProvenUnsatisfiable, 'SearchConflictSignals': list(Context.SearchConflictSignals), 'AuthoritativeAccessDomainFingerprint': Context.AccessDomainFingerprint, 'AuthoritativeCutAccessDomainFingerprint': Context.CutAccessDomainFingerprint, 'CutLocalJointSearches': Context.JointCutSearchDiagnostics}, 'ConflictGraph': {'Classification': 'saturated-boundary-cut', 'ConflictSignals': list(Context.Affected), 'RelocationSignals': list(Context.Affected), 'PriorityRelocationSignals': list(Context.Affected), 'PairwiseIncompatibleEdges': [list(Edge) for Edge in Context.UnavoidableEdges], 'FixedAccessIncompatibleEdges': [list(Edge) for Edge in Context.FixedAccessIncompatibleEdges], 'ObservedPatternConflictEdges': [list(Edge) for Edge in sorted(Context.BundleConflictEdges)], 'PriorityRelocationTerminals': [list(Terminal) for Terminal in Context.PriorityRelocationTerminals]}}))

def RecoverBoundaryLeaseAssignment(Context):
    Context.GreedySelected: dict[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]] = {}
    for Context.Signal, Context.Terminal in () if Context.Selected else Context.Order:
        Context.Values = Context.Domains[Context.Signal, Context.Terminal]
        Context.OrderedValues = tuple(sorted(Context.Values, key=lambda Value: ((Value[1] - Context.PreferredLayerByTerminal[Context.Signal, Context.Terminal]) % max(1, Context.LayerCount) if Context.DiversifyLayers else 0, Value[0], Value[1], Value[2].PortalId)))
        Context.Offset = CandidateOffset(Context, Context.Signal, len(Context.OrderedValues))
        for Context.Value in (*Context.OrderedValues[Context.Offset:], *Context.OrderedValues[:Context.Offset]):
            if not Compatible(Context, Context.Signal, Context.Value[3], Context.GreedySelected):
                Context.GreedySelected[Context.Signal, Context.Terminal] = Context.Value
                break
        if (Context.Signal, Context.Terminal) not in Context.GreedySelected:
            break
    if len(Context.GreedySelected) == len(Context.Order):
        Context.Selected = Context.GreedySelected
        Context.ExpansionCount = len(Context.Order)
    else:
        Context.GreedySelected.clear()
    if len(Context.Selected) != len(Context.Order) and (not Search(Context, 0)):
        Context.Affected = tuple(sorted(Context.FailedCut or {Key[0] for Key in Context.Order}))
        Context.CutAccessDomainFingerprint = BuildClusterInterfaceAccessDomainFingerprint(Context.Domains, frozenset(Context.Affected))
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.BoundaryEscapeInfeasible, Stage='ClusterBoundaryLease', AffectedNets=Context.Affected, Detail=f'no capacity-one access-plus-stem lease assignment within {Context.MaximumExpansions} deterministic expansions', Diagnostics={'ReservationPurpose': 'cluster-boundary-lease', 'AuthoritativeAccessDomainFingerprint': Context.AccessDomainFingerprint, 'AuthoritativeCutAccessDomainFingerprint': Context.CutAccessDomainFingerprint, 'ExpansionCount': Context.ExpansionCount, 'MaximumExpansions': Context.MaximumExpansions, 'LeaseTerminalCount': len(Context.Order), 'LayerDiversification': Context.DiversifyLayers, 'ClusterInterfacePatternSearch': {'Applied': True, 'Complete': Context.BundlePatternAssignmentComplete, 'SignalCount': len(Context.BundleSignals), 'ConflictComponentSizes': [len(Component) for Component in Context.Components], 'PatternCounts': {Signal: len(Patterns) for Signal, Patterns in sorted(Context.PatternsBySignal.items())}, 'SelfConflictPatternRejectionCounts': dict(sorted(Context.SelfConflictPatternRejectionCounts.items())), 'MaximumPatternsPerSignal': Context.MaximumPatternsPerSignal, 'ConflictEdges': [list(Edge) for Edge in sorted(Context.BundleConflictEdges)], 'AuthoritativeAccessDomainFingerprint': Context.AccessDomainFingerprint, 'AuthoritativeCutAccessDomainFingerprint': Context.CutAccessDomainFingerprint}, 'ConflictGraph': {'Classification': 'saturated-boundary-cut', 'ConflictSignals': list(Context.Affected), 'RelocationSignals': list(Context.Affected)}}))

def FinalizeBoundaryLeaseAssignment(Context):
    Context.Filtered = {Key: () for Key in Context.Portals}
    Context.Reservations = []
    for Context.SlotIndex, ((Context.Signal, Context.Terminal), (Context._Cost, Context.Layer, Context.Portal, Context.Claims)) in enumerate(sorted(Context.Selected.items())):
        Context.Filtered[Context.Signal, Context.Terminal, Context.Layer] = (Context.Portal,)
        Context.FirstSegment = Context.Portal.Path
        Context.Reservations.append(PortalReservation(Signal=Context.Signal, Terminal=Context.Terminal, Layer=Context.Layer, SlotIndex=Context.SlotIndex, PortalId=Context.Portal.PortalId, Claims=Context.Claims, Purpose='cluster-boundary-lease', FirstSegment=Context.FirstSegment))
    Context.AssignmentFingerprint = BuildClusterInterfaceReservationAssignmentFingerprint(Context.Reservations)
    Context.Resources.PreparedClusterInterfaceAssignment = ClusterInterfaceAssignment(Problem=Context.InterfaceProblem, Feasible=True, AssignmentFingerprint=Context.AssignmentFingerprint, OwnershipAssignmentFingerprint=Context.AssignmentFingerprint, Objective=(0, sum((len(Values) == 1 for Values in Context.Domains.values())), sum((Value[0] for Value in Context.Selected.values())), Context.AssignmentFingerprint))
    raise BoundaryLeaseReturn((Context.Filtered, tuple(Context.Reservations)))

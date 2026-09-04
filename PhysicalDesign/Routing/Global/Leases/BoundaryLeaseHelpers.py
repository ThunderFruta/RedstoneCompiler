"""Importable helpers for exact cluster boundary lease assignment."""

from __future__ import annotations

from ....Contracts.Core import Position3
from ....Runtime.Reliability import BuildStableFingerprint
from ....Resources.ResourceGraph import FindSelfClaimConflicts
from ....Resources.ResourceGraph import PinAccessPortal
from ....Resources.ResourceGraph import PortalReservation
from ....Resources.ResourceGraph import RoutingResourceClaims
from dataclasses import dataclass
from ..Assignment.AssignmentState import _ClaimsConflict
from ..Candidates.CandidateCache import BuildClusterInterfaceReservationAssignmentFingerprint
from functools import partial

from .BoundaryLeaseState import (
    BoundaryLeaseState,
    SetBoundaryLeaseState,
)


@dataclass(frozen=True)
class ClusterInterfaceAccessPattern:
    """One complete, immutable boundary-access choice for a signal."""
    Signal: str
    Selections: tuple[tuple[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]], ...]
    Claims: RoutingResourceClaims
    Score: tuple[object, ...]


def CandidateOffset(Context, Signal: str, Count: int) -> int:
    """Rotate only cut endpoints; all other lease domains stay stable."""
    return (Context.ReservationVariant + Context.SignalCandidateDomainOffsets.get(Signal, 0)) % max(1, Count)


def BuildZeroDomainConflictGraph(Context, Signal: str) -> dict[str, object]:
    """Publish an exact empty lease domain as placement repair evidence."""
    return {'Classification': 'candidate-starvation-placement-conflict', 'ConflictSignals': [Signal], 'NoCandidateSignals': [Signal], 'RelocationSignals': [Signal], 'PriorityRelocationSignals': [Signal], 'CandidateCounts': {Signal: 0}}


def AccessPatternFingerprint(Context, Selections: tuple[tuple[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]], ...]) -> str:
    return BuildStableFingerprint(tuple(sorted(((Terminal, Value[1], tuple(Value[2].Path), tuple(sorted(Value[3].WireCells)), tuple(sorted(Value[3].SupportCells)), tuple(sorted(Value[3].RequiredAirCells)), tuple(sorted(Value[3].ElectricalCells))) for (_Signal, Terminal), Value in Selections))))


def AccessPatternIsAdmissible(Context, Signal: str, Selections: tuple[tuple[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]], ...]) -> bool:
    """Apply frozen-pattern and realizability constraints uniformly.

        Complete patterns can originate in the ordinary per-signal domain or
        in the later exact joint-cut refinement.  Both are part of the same
        CSP domain and therefore must obey the same no-goods.  Applying these
        constraints only to the initial domain allowed joint refinement to
        reinsert a rejected pattern and repeat a forbidden assignment.
        """
    Fingerprint = AccessPatternFingerprint(Context, Selections)
    return bool(Fingerprint not in Context.NogoodPatternFingerprintsBySignal.get(Signal, frozenset()) and (Signal not in Context.RequiredPatternFingerprintsBySignal or Fingerprint == Context.RequiredPatternFingerprintsBySignal[Signal]))


def LegacyCompatible(Context, Signal: str, Claims: RoutingResourceClaims) -> set[str]:
    return {OtherSignal for (OtherSignal, _Terminal), Value in Context.LegacySelected.items() if OtherSignal != Signal and _ClaimsConflict(Signal, Claims, OtherSignal, Value[3])}


def SearchLegacy(Context, Depth: int) -> bool:
    if Depth == len(Context.Order):
        return True
    Signal, Terminal = Context.Order[Depth]
    Values = Context.Domains[Signal, Terminal]
    Offset = CandidateOffset(Context, Signal, len(Values))
    for Value in (*Values[Offset:], *Values[:Offset]):
        Context.LegacyExpansionCount += 1
        if Context.WorkCheck is not None and Context.LegacyExpansionCount % 16 == 0:
            Context.WorkCheck({'Phase': 'cluster-boundary-lease-assignment', 'ExpansionCount': Context.LegacyExpansionCount, 'LeaseTerminalCount': len(Context.Order), 'CompleteClusterInterfaceAccess': False})
        if Context.LegacyExpansionCount > Context.MaximumExpansions:
            Context.LegacyFailedCut.add(Signal)
            return False
        Blockers = LegacyCompatible(Context, Signal, Value[3])
        Context.LegacyFailedCut.update(Blockers)
        if Blockers:
            continue
        Context.LegacySelected[Signal, Terminal] = Value
        if SearchLegacy(Context, Depth + 1):
            return True
        Context.LegacySelected.pop((Signal, Terminal), None)
    Context.LegacyFailedCut.add(Signal)
    return False


def DiverseTerminalDomain(Context, Key: tuple[str, Position3]) -> tuple[tuple[int, int, PinAccessPortal, RoutingResourceClaims], ...]:
    Values = Context.Domains[Key]
    PreferredLayer = Context.PreferredLayerByTerminal[Key]
    Ranked = tuple(sorted(Values, key=lambda Value: ((Value[1] - PreferredLayer) % max(1, Context.LayerCount) if Context.DiversifyLayers else 0, Value[0], Value[1], Value[2].PortalId)))
    DomainOffset = CandidateOffset(Context, Key[0], len(Ranked))
    Ranked = (*Ranked[DomainOffset:], *Ranked[:DomainOffset])
    SelectedValues = []
    SeenLayers = set()
    for Value in Ranked:
        if Value[1] in SeenLayers:
            continue
        SelectedValues.append(Value)
        SeenLayers.add(Value[1])
        if len(SelectedValues) == Context.MaximumPortalChoicesPerTerminal:
            return tuple(SelectedValues)
    for Value in Ranked:
        if Value in SelectedValues:
            continue
        SelectedValues.append(Value)
        if len(SelectedValues) == Context.MaximumPortalChoicesPerTerminal:
            break
    return tuple(SelectedValues)


def MergePatternClaims(Context, Values: tuple[tuple[int, int, PinAccessPortal, RoutingResourceClaims], ...]) -> RoutingResourceClaims:
    return RoutingResourceClaims(WireCells=frozenset((Position for Value in Values for Position in Value[3].WireCells)), SupportCells=frozenset((Position for Value in Values for Position in Value[3].SupportCells)), RequiredAirCells=frozenset((Position for Value in Values for Position in Value[3].RequiredAirCells)), ElectricalCells=frozenset((Position for Value in Values for Position in Value[3].ElectricalCells)))


def CompleteOrderedValues(Context, Key: tuple[str, Position3]) -> tuple[tuple[int, int, PinAccessPortal, RoutingResourceClaims], ...]:
    Values = tuple(sorted(Context.Domains[Key], key=lambda Value: ((Value[1] - Context.PreferredLayerByTerminal[Key]) % max(1, Context.LayerCount) if Context.DiversifyLayers else 0, Value[0], Value[1], Value[2].PortalId)))
    Offset = CandidateOffset(Context, Key[0], len(Values))
    return (*Values[Offset:], *Values[:Offset])


def CompleteSelectionAdmissible(Context, Key: tuple[str, Position3], Value: tuple[int, int, PinAccessPortal, RoutingResourceClaims]) -> bool:
    Signal = Key[0]
    SameSignalValues = tuple((Value if OtherKey == Key else Context.CompleteSelected[OtherKey] for OtherKey in Context.TerminalKeysBySignal[Signal] if OtherKey == Key or OtherKey in Context.CompleteSelected))
    if FindSelfClaimConflicts({Signal: MergePatternClaims(Context, SameSignalValues)}):
        return False
    for OtherKey, OtherValue in Context.CompleteSelected.items():
        if OtherKey[0] == Signal:
            continue
        if _ClaimsConflict(Signal, Value[3], OtherKey[0], OtherValue[3]):
            Context.CompleteFailedSignals.update((Signal, OtherKey[0]))
            return False
    RemainingSignalKeys = tuple((OtherKey for OtherKey in Context.TerminalKeysBySignal[Signal] if OtherKey != Key and OtherKey not in Context.CompleteSelected))
    if RemainingSignalKeys:
        return True
    Selections = tuple(((OtherKey, Value if OtherKey == Key else Context.CompleteSelected[OtherKey]) for OtherKey in Context.TerminalKeysBySignal[Signal]))
    return AccessPatternIsAdmissible(Context, Signal, Selections)


def CompleteForwardDomain(Context, Key: tuple[str, Position3]) -> tuple[tuple[int, int, PinAccessPortal, RoutingResourceClaims], ...]:
    return tuple((Value for Value in Context.CompleteDomains[Key] if CompleteSelectionAdmissible(Context, Key, Value)))


def SearchCompleteDomains(Context, RemainingKeys: tuple[tuple[str, Position3], ...]) -> bool:
    if not RemainingKeys:
        CompleteReservations = tuple((PortalReservation(Signal=Signal, Terminal=Terminal, Layer=Value[1], SlotIndex=SlotIndex, PortalId=Value[2].PortalId, Claims=Value[3], Purpose='cluster-boundary-lease', FirstSegment=Value[2].Path) for SlotIndex, ((Signal, Terminal), Value) in enumerate(sorted(Context.CompleteSelected.items()))))
        return BuildClusterInterfaceReservationAssignmentFingerprint(CompleteReservations) not in Context.ForbiddenOwnershipAssignmentFingerprints
    ForwardDomains = {Key: CompleteForwardDomain(Context, Key) for Key in RemainingKeys}
    EmptyKeys = tuple((Key for Key, Values in ForwardDomains.items() if not Values))
    if EmptyKeys:
        Context.CompleteFailedSignals.update((Key[0] for Key in EmptyKeys))
        return False
    Key = min(RemainingKeys, key=lambda CandidateKey: (len(ForwardDomains[CandidateKey]), CandidateKey[1], CandidateKey[0]))
    NextKeys = tuple((CandidateKey for CandidateKey in RemainingKeys if CandidateKey != Key))
    for Value in ForwardDomains[Key]:
        Context.CompleteExpansionCount += 1
        if Context.WorkCheck is not None and Context.CompleteExpansionCount % 16 == 0:
            Context.WorkCheck({'Phase': 'complete-cluster-interface-domain-search', 'ExpansionCount': Context.CompleteExpansionCount, 'MaximumExpansions': Context.MaximumExpansions, 'RemainingTerminalCount': len(RemainingKeys), 'TerminalCount': len(Context.Order)})
        if Context.CompleteExpansionCount > Context.MaximumExpansions:
            Context.CompleteWorkExhausted = True
            return False
        Context.CompleteSelected[Key] = Value
        if SearchCompleteDomains(Context, NextKeys):
            return True
        Context.CompleteSelected.pop(Key, None)
        if Context.CompleteWorkExhausted:
            return False
    Context.CompleteFailedSignals.add(Key[0])
    return False


def SelectLayerSignatureDiverseIndices(Context, Values: list[tuple[int, ...]], Limit: int) -> list[tuple[int, ...]]:
    Ordered = sorted(Values, key=lambda Indices: (len({Context.CandidateDomains[Index][Value][1] for Index, Value in enumerate(Indices)}) - 1, max((Context.CandidateDomains[Index][Value][1] for Index, Value in enumerate(Indices))) - min((Context.CandidateDomains[Index][Value][1] for Index, Value in enumerate(Indices))) if Indices else 0, max(Indices, default=0), sum(Indices), Indices))
    SelectedIndices: list[tuple[int, ...]] = []
    SeenLayerSignatures: set[tuple[int, ...]] = set()
    for Indices in Ordered:
        LayerSignature = tuple((Context.CandidateDomains[Index][Value][1] for Index, Value in enumerate(Indices)))
        if LayerSignature in SeenLayerSignatures:
            continue
        SelectedIndices.append(Indices)
        SeenLayerSignatures.add(LayerSignature)
        if len(SelectedIndices) >= Limit:
            return SelectedIndices
    for Indices in Ordered:
        if Indices in SelectedIndices:
            continue
        SelectedIndices.append(Indices)
        if len(SelectedIndices) >= Limit:
            break
    return SelectedIndices


def CompatiblePatternIndices(Context, Pattern: ClusterInterfaceAccessPattern, OtherSignal: str) -> frozenset[int]:
    PatternIndex = Context.PatternIndexByIdentity[Pattern.Signal][id(Pattern)]
    Key = (Pattern.Signal, PatternIndex, OtherSignal)
    Cached = Context.CompatiblePatternIndexCache.get(Key)
    if Cached is not None:
        return Cached
    Cached = frozenset((OtherIndex for OtherIndex, OtherPattern in enumerate(Context.PatternsBySignal[OtherSignal]) if not _ClaimsConflict(Pattern.Signal, Pattern.Claims, OtherSignal, OtherPattern.Claims)))
    Context.CompatiblePatternIndexCache[Key] = Cached
    return Cached


def PatternSelectionFingerprint(Context, Pattern: ClusterInterfaceAccessPattern) -> tuple[tuple[Position3, int, str], ...]:
    """Deduplicate the existing bounded joint-pattern search."""
    return tuple(((Terminal, Value[1], Value[2].PortalId) for (_Signal, Terminal), Value in Pattern.Selections))


def AddJointCutPatterns(Context, CutSignals: tuple[str, ...], CutEdges: tuple[tuple[str, str], ...]) -> bool:
    """Recover templates hidden by independent per-signal truncation.

        An incompatible pair in the bounded macro-pattern domains is not proof
        that the underlying terminal portal domains are incompatible. Search
        only the connected exact cut at endpoint granularity, using the same
        global expansion budget, and feed a small set of jointly compatible
        complete templates back into the authoritative macro assignment.
        """
    CutFingerprint = (tuple(sorted(CutSignals)), tuple(sorted(CutEdges)))
    if CutFingerprint in Context.ExpandedJointCutFingerprints:
        return False
    Context.ExpandedJointCutFingerprints.add(CutFingerprint)
    CutKeys = tuple((Key for Key in Context.Order if Key[0] in CutSignals))
    MaximumJointSolutions = min(16, max(2, Context.MaximumPatternsPerSignal // 4))
    JointSelections: dict[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]] = {}
    JointSolutions: list[dict[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]]] = []
    SeenSolutionFingerprints: set[tuple[tuple[Position3, int, str], ...]] = set()
    SearchStartExpansionCount = Context.ExpansionCount
    BudgetExhausted = False
    CutKeyIndices = {Key: Index for Index, Key in enumerate(CutKeys)}
    CompatibleValueIndexCache: dict[tuple[tuple[str, Position3], int, tuple[str, Position3]], frozenset[int]] = {}
    FailedJointStates: set[tuple[tuple[tuple[str, Position3], tuple[int, ...]], ...]] = set()
    JointCompatibilityCheckCount = 0

    def CompatibleValueIndices(Key: tuple[str, Position3], ValueIndex: int, OtherKey: tuple[str, Position3]) -> frozenset[int]:
        """Cache exact endpoint compatibility for incremental propagation."""
        nonlocal JointCompatibilityCheckCount
        CacheKey = (Key, ValueIndex, OtherKey)
        Cached = CompatibleValueIndexCache.get(CacheKey)
        if Cached is not None:
            return Cached
        Value = Context.Domains[Key][ValueIndex]
        Compatible = []
        for OtherValueIndex, OtherValue in enumerate(Context.Domains[OtherKey]):
            if Key[0] == OtherKey[0]:
                if not FindSelfClaimConflicts({Key[0]: MergePatternClaims(Context, (Value, OtherValue))}):
                    Compatible.append(OtherValueIndex)
                continue
            else:
                JointCompatibilityCheckCount += 1
                if Context.WorkCheck is not None and JointCompatibilityCheckCount % 256 == 0:
                    Context.WorkCheck({'Phase': 'cluster-interface-cut-compatibility', 'CompatibilityCheckCount': JointCompatibilityCheckCount, 'CutSignalCount': len(CutSignals), 'CacheEntryCount': len(CompatibleValueIndexCache)})
                if not _ClaimsConflict(Key[0], Value[3], OtherKey[0], OtherValue[3]):
                    Compatible.append(OtherValueIndex)
        Cached = frozenset(Compatible)
        CompatibleValueIndexCache[CacheKey] = Cached
        return Cached

    def RotatedValueIndices(Key: tuple[str, Position3], SearchVariant: int) -> tuple[int, ...]:
        ValueCount = len(Context.Domains[Key])
        Offset = (CandidateOffset(Context, Key[0], ValueCount) + SearchVariant * (CutKeyIndices[Key] * 2 + 1)) % max(1, ValueCount)
        return tuple((*range(Offset, ValueCount), *range(0, Offset)))

    def SearchJointCut(ActiveDomains: dict[tuple[str, Position3], tuple[int, ...]], SolutionTarget: int) -> None:
        nonlocal BudgetExhausted
        if len(JointSolutions) >= SolutionTarget or len(JointSolutions) >= MaximumJointSolutions:
            return
        if not ActiveDomains:
            Fingerprint = tuple(sorted(((Terminal, Value[1], Value[2].PortalId) for (_Signal, Terminal), Value in JointSelections.items())))
            if Fingerprint not in SeenSolutionFingerprints:
                SeenSolutionFingerprints.add(Fingerprint)
                JointSolutions.append(dict(JointSelections))
            return
        StateKey = tuple(sorted(((Key, ValueIndices) for Key, ValueIndices in ActiveDomains.items())))
        if StateKey in FailedJointStates:
            return
        SolutionCountBefore = len(JointSolutions)
        Key = min(ActiveDomains, key=lambda CandidateKey: (len(ActiveDomains[CandidateKey]), CandidateKey[1], CandidateKey[0]))
        ValueIndices = ActiveDomains[Key]
        if not ValueIndices:
            FailedJointStates.add(StateKey)
            return
        for ValueIndex in ValueIndices:
            if Context.ExpansionCount >= Context.MaximumExpansions:
                BudgetExhausted = True
                return
            Context.ExpansionCount += 1
            if Context.WorkCheck is not None and Context.ExpansionCount % 16 == 0:
                Context.WorkCheck({'Phase': 'cluster-interface-cut-joint-search', 'ExpansionCount': Context.ExpansionCount, 'CutSignalCount': len(CutSignals), 'RemainingTerminalCount': len(ActiveDomains) - 1, 'SolutionCount': len(JointSolutions), 'FailedStateCount': len(FailedJointStates), 'CompatibilityCacheEntryCount': len(CompatibleValueIndexCache)})
            NextDomains: dict[tuple[str, Position3], tuple[int, ...]] = {}
            ForwardLegal = True
            for OtherKey, OtherValueIndices in ActiveDomains.items():
                if OtherKey == Key:
                    continue
                CompatibleIndices = CompatibleValueIndices(Key, ValueIndex, OtherKey)
                FilteredIndices = tuple((OtherValueIndex for OtherValueIndex in OtherValueIndices if OtherValueIndex in CompatibleIndices))
                if not FilteredIndices:
                    ForwardLegal = False
                    break
                NextDomains[OtherKey] = FilteredIndices
            if not ForwardLegal:
                continue
            Value = Context.Domains[Key][ValueIndex]
            JointSelections[Key] = Value
            SearchJointCut(NextDomains, SolutionTarget)
            JointSelections.pop(Key, None)
            if BudgetExhausted or len(JointSolutions) >= SolutionTarget or len(JointSolutions) >= MaximumJointSolutions:
                return
        if not BudgetExhausted and len(JointSolutions) == SolutionCountBefore:
            FailedJointStates.add(StateKey)
    CompletedSearchVariants = 0
    for JointSearchVariantValue in range(MaximumJointSolutions):
        CompletedSearchVariants += 1
        SearchJointCut({Key: RotatedValueIndices(Key, JointSearchVariantValue) for Key in CutKeys}, min(MaximumJointSolutions, len(JointSolutions) + 1))
        JointSelections.clear()
        if BudgetExhausted or len(JointSolutions) >= MaximumJointSolutions:
            break
    ExistingFingerprints = {Signal: {PatternSelectionFingerprint(Context, Pattern) for Pattern in Context.PatternsBySignal[Signal]} for Signal in CutSignals}
    AddedPatternCounts = {Signal: 0 for Signal in CutSignals}
    for Solution in JointSolutions:
        for Signal in CutSignals:
            Terminals = tuple((Key for Key in CutKeys if Key[0] == Signal))
            Values = tuple((Solution[Key] for Key in Terminals))
            PatternClaims = MergePatternClaims(Context, Values)
            if FindSelfClaimConflicts({Signal: PatternClaims}):
                continue
            Pattern = ClusterInterfaceAccessPattern(Signal=Signal, Selections=tuple(zip(Terminals, Values)), Claims=PatternClaims, Score=(len({Value[1] for Value in Values}) - 1, max((Value[1] for Value in Values)) - min((Value[1] for Value in Values)) if Values else 0, sum((Value[0] for Value in Values)), tuple((Value[1] for Value in Values)), tuple((Value[2].PortalId for Value in Values))))
            if not AccessPatternIsAdmissible(Context, Signal, Pattern.Selections):
                continue
            Fingerprint = PatternSelectionFingerprint(Context, Pattern)
            if Fingerprint in ExistingFingerprints[Signal]:
                continue
            ExistingFingerprints[Signal].add(Fingerprint)
            Context.PatternsBySignal[Signal] = (*Context.PatternsBySignal[Signal], Pattern)
            AddedPatternCounts[Signal] += 1
    PatternsAdded = sum(AddedPatternCounts.values())
    Context.JointCutSearchDiagnostics.append({'CutSignals': list(CutSignals), 'CutEdges': [list(Edge) for Edge in CutEdges], 'TerminalCount': len(CutKeys), 'SolutionCount': len(JointSolutions), 'AddedPatternCounts': AddedPatternCounts, 'ExpansionCount': Context.ExpansionCount - SearchStartExpansionCount, 'SearchVariantCount': CompletedSearchVariants, 'BudgetExhausted': BudgetExhausted, 'CompatibilityCheckCount': JointCompatibilityCheckCount, 'CompatibilityCacheEntryCount': len(CompatibleValueIndexCache), 'FailedStateCount': len(FailedJointStates)})
    if not PatternsAdded:
        return False
    Context.PatternIndexByIdentity = {Signal: {id(Pattern): Index for Index, Pattern in enumerate(Patterns)} for Signal, Patterns in Context.PatternsBySignal.items()}
    Context.CompatiblePatternIndexCache = {}
    return True


def BuildConflictComponents(Context) -> list[tuple[str, ...]]:
    Components = []
    UnvisitedSignals = set(Context.BundleSignals)
    while UnvisitedSignals:
        Start = min(UnvisitedSignals, key=lambda Signal: (Context.Profiles[Signal].Root, tuple(Context.Profiles[Signal].Targets)))
        Pending = [Start]
        Component = set()
        while Pending:
            Signal = Pending.pop()
            if Signal in Component:
                continue
            Component.add(Signal)
            Pending.extend(Context.ConservativeAdjacency[Signal] - Component)
        UnvisitedSignals.difference_update(Component)
        Components.append(tuple(sorted(Component, key=lambda Signal: (len(Context.PatternsBySignal[Signal]), Context.Profiles[Signal].Root, tuple(Context.Profiles[Signal].Targets)))))
    Components.sort(key=lambda Component: (0 if Context.PriorityInterfaceCutSignals.intersection(Component) else 1, -len(Component), tuple(((Context.Profiles[Signal].Root, tuple(Context.Profiles[Signal].Targets)) for Signal in Component))))
    return Components


def SearchBundleComponent(Context, ActiveDomains: dict[str, tuple[ClusterInterfaceAccessPattern, ...]], ComponentIndex: int, FailedStates: set[tuple[object, ...]]) -> bool:
    if not ActiveDomains:
        return True
    StateKey = tuple(sorted(((Signal, tuple((Context.PatternIndexByIdentity[Signal][id(Pattern)] for Pattern in Patterns))) for Signal, Patterns in ActiveDomains.items())))
    if StateKey in FailedStates:
        return False
    Signal = min(ActiveDomains, key=lambda Value: (0 if Value in Context.PriorityInterfaceCutSignals else 1, len(ActiveDomains[Value]), len(Context.PatternsBySignal[Value]), Context.Profiles[Value].Root, tuple(Context.Profiles[Value].Targets)))
    ViablePatterns = ActiveDomains[Signal]
    if not ViablePatterns:
        Context.FailedCut.add(Signal)
        FailedStates.add(StateKey)
        return False
    for Pattern in ViablePatterns:
        Context.ExpansionCount += 1
        if Context.WorkCheck is not None and Context.ExpansionCount % 16 == 0:
            Context.WorkCheck({'Phase': 'cluster-interface-pattern-assignment', 'ExpansionCount': Context.ExpansionCount, 'ComponentIndex': ComponentIndex, 'ComponentCount': len(Context.Components), 'RemainingSignalCount': len(ActiveDomains), 'SignalCount': len(Context.BundleSignals), 'PatternCount': len(Context.PatternsBySignal[Signal])})
        if Context.ExpansionCount > Context.MaximumExpansions:
            Context.FailedCut.add(Signal)
            return False
        NextDomains = {}
        ForwardLegal = True
        for OtherSignal, OtherPatterns in ActiveDomains.items():
            if OtherSignal == Signal:
                continue
            CompatibleIndices = CompatiblePatternIndices(Context, Pattern, OtherSignal)
            FilteredPatterns = tuple((OtherPattern for OtherPattern in OtherPatterns if Context.PatternIndexByIdentity[OtherSignal][id(OtherPattern)] in CompatibleIndices))
            if not FilteredPatterns:
                ForwardLegal = False
                Context.FailedCut.add(OtherSignal)
                Context.BundleConflictEdges.add(tuple(sorted((Signal, OtherSignal))))
                break
            NextDomains[OtherSignal] = FilteredPatterns
        if not ForwardLegal:
            continue
        Context.SelectedPatterns[Signal] = Pattern
        if SearchBundleComponent(Context, NextDomains, ComponentIndex, FailedStates):
            return True
        Context.SelectedPatterns.pop(Signal, None)
    Context.FailedCut.add(Signal)
    FailedStates.add(StateKey)
    return False


def ComponentSatisfiable(Context, Signals: tuple[str, ...]) -> bool | None:
    """Decide one reduced component inside the existing CSP budget."""
    FailedStates: set[tuple[object, ...]] = set()

    def SearchReduced(ActiveDomains: dict[str, tuple[ClusterInterfaceAccessPattern, ...]]) -> bool | None:
        if not ActiveDomains:
            return True
        StateKey = tuple(sorted(((Signal, tuple((Context.PatternIndexByIdentity[Signal][id(Pattern)] for Pattern in Patterns))) for Signal, Patterns in ActiveDomains.items())))
        if StateKey in FailedStates:
            return False
        Signal = min(ActiveDomains, key=lambda Value: (len(ActiveDomains[Value]), len(Context.PatternsBySignal[Value]), Context.Profiles[Value].Root, tuple(Context.Profiles[Value].Targets)))
        for Pattern in ActiveDomains[Signal]:
            if Context.ExpansionCount >= Context.MaximumExpansions:
                Context.CoreShrinkBudgetExhausted = True
                return None
            Context.ExpansionCount += 1
            Context.CoreShrinkExpansionCount += 1
            if Context.WorkCheck is not None and Context.CoreShrinkExpansionCount % 16 == 0:
                Context.WorkCheck({'Phase': 'cluster-interface-unsat-core-shrink', 'ExpansionCount': Context.ExpansionCount, 'CoreShrinkExpansionCount': Context.CoreShrinkExpansionCount, 'CoreSignalCount': len(Signals)})
            NextDomains = {}
            ForwardLegal = True
            for OtherSignal, OtherPatterns in ActiveDomains.items():
                if OtherSignal == Signal:
                    continue
                CompatibleIndices = CompatiblePatternIndices(Context, Pattern, OtherSignal)
                FilteredPatterns = tuple((OtherPattern for OtherPattern in OtherPatterns if Context.PatternIndexByIdentity[OtherSignal][id(OtherPattern)] in CompatibleIndices))
                if not FilteredPatterns:
                    ForwardLegal = False
                    break
                NextDomains[OtherSignal] = FilteredPatterns
            if not ForwardLegal:
                continue
            Result = SearchReduced(NextDomains)
            if Result is not False:
                return Result
        FailedStates.add(StateKey)
        return False
    return SearchReduced({Signal: Context.PatternsBySignal[Signal] for Signal in Signals})


_DefaultBoundaryLeaseSelections = object()


def Compatible(
    Context,
    Signal: str,
    Claims: RoutingResourceClaims,
    Selections: dict[
        tuple[str, Position3],
        tuple[int, int, PinAccessPortal, RoutingResourceClaims],
    ] | object = _DefaultBoundaryLeaseSelections,
) -> set[str]:
    if Selections is _DefaultBoundaryLeaseSelections:
        Selections = Context.Selected
    return {OtherSignal for (OtherSignal, _Terminal), Value in Selections.items() if OtherSignal != Signal and _ClaimsConflict(Signal, Claims, OtherSignal, Value[3])}


def Search(Context, Depth: int) -> bool:
    if Depth == len(Context.Order):
        return True
    Signal, Terminal = Context.Order[Depth]
    Values = Context.Domains[Signal, Terminal]
    OrderedValues = tuple(sorted(Values, key=lambda Value: ((Value[1] - Context.PreferredLayerByTerminal[Signal, Terminal]) % max(1, Context.LayerCount) if Context.DiversifyLayers else 0, Value[0], Value[1], Value[2].PortalId)))
    Offset = CandidateOffset(Context, Signal, len(OrderedValues))
    for Value in (*OrderedValues[Offset:], *OrderedValues[:Offset]):
        Context.ExpansionCount += 1
        if Context.WorkCheck is not None and Context.ExpansionCount % 16 == 0:
            Context.WorkCheck({'Phase': 'cluster-boundary-lease-assignment', 'ExpansionCount': Context.ExpansionCount, 'LeaseTerminalCount': len(Context.Order)})
        if Context.ExpansionCount > Context.MaximumExpansions:
            Context.FailedCut.add(Signal)
            return False
        Blockers = Compatible(Context, Signal, Value[3])
        Context.FailedCut.update(Blockers)
        if Blockers:
            continue
        Context.Selected[Signal, Terminal] = Value
        if Search(Context, Depth + 1):
            return True
        Context.Selected.pop((Signal, Terminal), None)
    Context.FailedCut.add(Signal)
    return False

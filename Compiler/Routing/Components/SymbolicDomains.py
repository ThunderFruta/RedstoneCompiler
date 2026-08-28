"""Symbolic pair, higher-order, unary, and foreign-portal proof domains."""

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
from ..Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from ..Contracts.Component import (
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    PhysicalComponentAssemblyPlan,
    PhysicalComponentChannelReservation,
    PhysicalComponentPortReservation,
    PhysicalComponentSelectedLocalPortSupport,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from ..Contracts.Core import Position3
from ..Contracts.PhysicalInterface import (
    PhysicalComponentLocalFactorProjection,
    PhysicalComponentLocalFactorProjectionComparison,
    PhysicalComponentLocalFactorUnsatCertificate,
    PhysicalLocalPortPairProofRecord,
    PhysicalLocalPortPairSupportCertificate,
    PhysicalComponentSymbolicHigherOrderCertificate,
    PhysicalComponentSymbolicPortPairCertificate,
    PhysicalPortCorridorDomain,
    PhysicalPortCorridorFactor,
    PreparedPhysicalComponentAssembly,
    PreparedPhysicalComponentPortFactorDomain,
)
from ..Interfaces import BoundaryRelations
from ..Interfaces.BoundaryRelations import (
    BuildPhysicalPortGlobalContractFingerprint,
    ProjectPhysicalComponentSignalGlobalProfile,
)
from ..Interfaces.PhysicalClaims import ComponentClaimsConflict
from ..ResourceGraph import RoutingResourceClaims
from ..Reliability import BuildStableFingerprint
from .InterfacePlanning import (
    BuildComponentCapacityGuide,
    ComponentCapacityGuide,
    ComponentCapacityGuideOption,
    ComponentInterfaceContract,
    ComponentPlanningResult,
    ComponentPlanningStatus,
    IterClosedComponentContracts,
    PlanClosedComponent,
    SolveComponentInterfaceCsp,
)

from .Core import BuildCompleteComponentNetPortfolioStaticContext
from .SymbolicState import (
    _BuildPreparedComponentSymbolicNetStateContextFingerprint,
    BuildComponentSymbolicNetStateCacheKey,
    PrepareComponentSymbolicNetStateContext,
)
from .SymbolicWorkers import (
    CompilePreparedComponentPhysicalFactorStateBatch,
    CompilePreparedComponentSymbolicNetStates,
)
from .Portfolios import (
    BuildCompleteOpposingNetAccessContractDomain,
    BuildCompleteOpposingNetAccessRowContext,
    CompileCompleteComponentNetVariantPortfolio,
    CompileCompleteComponentNetVariantPortfolios,
    EvaluateCompleteOpposingNetAccessContractRow,
)
from .Solver import (
    MaterializeRoutedComponentTemplate,
    SolveComponentRoutingProblem,
    ValidateRoutedComponentHandoff,
)

from .Certification import (
    BuildGlobalRelaxedLocalProofDomainFingerprint,
    ValidatePhysicalComponentSymbolicPortPairCertificate,
    _BuildPhysicalComponentSymbolicNetStateFingerprint,
    _BuildPhysicalComponentSymbolicPortPairContext,
    _BuildPhysicalComponentSymbolicPortPairVariantProblem,
    _SelectPhysicalComponentSymbolicPortPairFactors,
)
from .Validation import (
    BuildPhysicalPortApertureContractFingerprint,
    BuildPhysicalPortSeamContractFingerprint,
    _Fingerprint,
)
def CompilePhysicalComponentSymbolicPortPairDomain(Problem: ComponentRoutingProblem, FactorDomain: PreparedPhysicalComponentPortFactorDomain, SignalPair: Iterable[str], *, DeadlineSeconds: float | None, WorkCheck: Callable[[dict[str, object]], None] | None=None, NetStateCache: dict[str, Any] | None=None, CompletedCertificateCache: dict[str, PhysicalComponentSymbolicPortPairCertificate] | None=None, CompleteCompatibilityIndexCache: dict[str, Any] | None=None, RouteClaimsConstructionCache: dict[frozenset[Position3], RoutingResourceClaims] | None=None) -> PhysicalComponentSymbolicPortPairCertificate:
    """Compile exact unary/binary support across two complete seam domains."""
    if not FactorDomain.Complete:
        raise ValueError('symbolic port pair compilation requires a complete factor domain')
    if not FactorDomain.Feasible:
        raise ValueError('symbolic port pair compilation requires a feasible factor domain')
    if Problem.PlacementFingerprint != FactorDomain.PlacementFingerprint:
        raise ValueError('symbolic port pair placement identity mismatch')
    Signals = tuple(sorted(frozenset(map(str, SignalPair))))
    if len(Signals) != 2:
        raise ValueError('symbolic port pair compilation requires two signals')
    FactorsBySignal, SeamFingerprintByLocalAccess = _SelectPhysicalComponentSymbolicPortPairFactors(FactorDomain, Signals)
    Context = _BuildPhysicalComponentSymbolicPortPairContext(Problem, FactorDomain, Signals, FactorsBySignal, SeamFingerprintByLocalAccess)
    DomainFingerprint = Context['DomainFingerprint']
    EffectiveNetStateCache = NetStateCache if NetStateCache is not None else {}
    Cached = CompletedCertificateCache.get(DomainFingerprint) if CompletedCertificateCache is not None else None
    if Cached is not None:
        ValidatePhysicalComponentSymbolicPortPairCertificate(Cached, Problem, FactorDomain, Signals, NetStateCache=EffectiveNetStateCache)
        return Cached
    StartedAt = monotonic()
    RelaxedProblem = replace(FactorDomain.Problem, ReservedGlobalClaimsBySignal=())
    PreparedNetStateContexts = {Signal: PrepareComponentSymbolicNetStateContext(RelaxedProblem, Signal, RouteClaimsConstructionCache=RouteClaimsConstructionCache) for Signal in Signals}
    StatesBySignalAndLocalAccess: dict[tuple[str, str], tuple[Any, ...]] = {}
    NetStateCacheKeys = []
    NetStateBindings = []
    Complete = True
    for Signal in Signals:
        VariantProblemsByAccess = {LocalAccessFingerprint: _BuildPhysicalComponentSymbolicPortPairVariantProblem(RelaxedProblem, Signal, LocalAccessFingerprint, Factor) for LocalAccessFingerprint, Factor in sorted(FactorsBySignal[Signal].items())}
        RemainingDeadline = None if DeadlineSeconds is None else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        CompilationsByAccess = CompilePreparedComponentPhysicalFactorStateBatch(PreparedNetStateContexts[Signal], VariantProblemsByAccess, DeadlineSeconds=RemainingDeadline, WorkCheck=WorkCheck, SymbolicNetStateCache=EffectiveNetStateCache)
        for LocalAccessFingerprint in sorted(VariantProblemsByAccess):
            Compilation = CompilationsByAccess[LocalAccessFingerprint]
            CacheKey = Compilation.CacheKey
            if not Compilation.Complete or Compilation.States is None:
                Complete = False
                break
            States = Compilation.States
            StatesBySignalAndLocalAccess[Signal, LocalAccessFingerprint] = tuple(States)
            NetStateCacheKeys.append((Signal, LocalAccessFingerprint, CacheKey))
            NetStateBindings.append((Signal, LocalAccessFingerprint, CacheKey, _BuildPhysicalComponentSymbolicNetStateFingerprint(States)))
        if not Complete:
            break
    MandatoryStateDomains: list[tuple[Any, ...]] = []
    MandatoryStateDomainsBySignal: dict[str, tuple[Any, ...]] = {}
    AllLocalFactorsBySignal = dict(FactorDomain.LocalAccessFactorsBySignal)
    SupportedAccessesBySignal = {str(Signal): frozenset((str(Support.LocalAccessFingerprint) for Support in Supports)) for Signal, Supports in FactorDomain.LocalApertureSupportBySignal}
    if Complete:
        for OtherSignal in (Signal for Signal in sorted(Problem.ComponentSignals) if str(Signal) not in Signals):
            OtherFactors = {str(Factor.LocalAccessFingerprint): Factor for Factor in AllLocalFactorsBySignal.get(OtherSignal, ()) if str(Factor.LocalAccessFingerprint) in SupportedAccessesBySignal.get(OtherSignal, frozenset())}
            OtherContext = PrepareComponentSymbolicNetStateContext(RelaxedProblem, OtherSignal, RouteClaimsConstructionCache=RouteClaimsConstructionCache)
            RemainingDeadline = None if DeadlineSeconds is None else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
            if OtherFactors:
                OtherProblems = {LocalAccessFingerprint: _BuildPhysicalComponentSymbolicPortPairVariantProblem(RelaxedProblem, OtherSignal, LocalAccessFingerprint, Factor) for LocalAccessFingerprint, Factor in sorted(OtherFactors.items())}
                OtherCompilations = CompilePreparedComponentPhysicalFactorStateBatch(OtherContext, OtherProblems, DeadlineSeconds=RemainingDeadline, WorkCheck=WorkCheck, SymbolicNetStateCache={})
                OtherStates = []
                for LocalAccessFingerprint in sorted(OtherProblems):
                    Compilation = OtherCompilations[LocalAccessFingerprint]
                    if not Compilation.Complete or Compilation.States is None:
                        Complete = False
                        break
                    OtherStates.extend(Compilation.States)
            else:
                Compilation = CompilePreparedComponentSymbolicNetStates(OtherContext, RelaxedProblem, DeadlineSeconds=RemainingDeadline, WorkCheck=WorkCheck, SymbolicNetStateCache={})
                if not Compilation.Complete or Compilation.States is None:
                    Complete = False
                    break
                OtherStates = list(Compilation.States)
            if not Complete or not OtherStates:
                Complete = False
                break
            OtherStateDomain = tuple(OtherStates)
            MandatoryStateDomains.append(OtherStateDomain)
            MandatoryStateDomainsBySignal[OtherSignal] = OtherStateDomain
    UnsupportedUnaryLocalAccess = tuple(sorted(((Signal, LocalAccessFingerprint) for (Signal, LocalAccessFingerprint), States in StatesBySignalAndLocalAccess.items() if not States)))
    UnsupportedLocalAccessPairs = []
    CompatibilityCheckCount = 0
    ConflictIndexCache: dict[tuple[str, ...], tuple[dict[str, dict[Position3, int]], int]] = {}

    def BuildConflictIndexes(States: tuple[Any, ...]) -> tuple[dict[str, dict[Position3, int]], int]:
        Key = tuple((State.NetFingerprint for State in States))
        CachedIndexes = ConflictIndexCache.get(Key)
        if CachedIndexes is not None:
            return CachedIndexes
        Mutable = {'Wire': {}, 'Support': {}, 'Air': {}, 'Electrical': {}}
        for Index, State in enumerate(States):
            Bit = 1 << Index
            for Name, Cells in (('Wire', State.Claims.WireCells), ('Support', State.Claims.SupportCells), ('Air', State.Claims.RequiredAirCells), ('Electrical', State.Claims.ElectricalCells)):
                IndexByCell = Mutable[Name]
                for Cell in Cells:
                    IndexByCell[Cell] = IndexByCell.get(Cell, 0) | Bit
        Result = (Mutable, (1 << len(States)) - 1)
        ConflictIndexCache[Key] = Result
        return Result

    def BuildFirstStateConflictMask(FirstState: Any, SecondIndexes: dict[str, dict[Position3, int]], AllSecondStatesMask: int) -> int:
        ConflictMask = 0

        def Add(Cells: Iterable[Position3], Names: tuple[str, ...]) -> None:
            nonlocal ConflictMask
            for Cell in Cells:
                for Name in Names:
                    ConflictMask |= SecondIndexes[Name].get(Cell, 0)
                if ConflictMask == AllSecondStatesMask:
                    return
        Add(FirstState.Claims.WireCells, ('Wire', 'Support', 'Air', 'Electrical'))
        if ConflictMask != AllSecondStatesMask:
            Add(FirstState.Claims.SupportCells, ('Wire', 'Air'))
        if ConflictMask != AllSecondStatesMask:
            Add(FirstState.Claims.RequiredAirCells, ('Wire', 'Support'))
        if ConflictMask != AllSecondStatesMask:
            Add(FirstState.Claims.ElectricalCells, ('Wire',))
        return ConflictMask
    if Complete and (not MandatoryStateDomains):
        FirstSignal, SecondSignal = Signals
        FlattenedSecondStates = tuple((State for SecondAccess in sorted(FactorsBySignal[SecondSignal]) for State in StatesBySignalAndLocalAccess[SecondSignal, SecondAccess]))
        SecondAccessMasks = {}
        SecondStateOffset = 0
        for SecondAccess in sorted(FactorsBySignal[SecondSignal]):
            StateCount = len(StatesBySignalAndLocalAccess[SecondSignal, SecondAccess])
            SecondAccessMasks[SecondAccess] = (1 << StateCount) - 1 << SecondStateOffset if StateCount else 0
            SecondStateOffset += StateCount
        SecondIndexes, AllSecondStatesMask = BuildConflictIndexes(FlattenedSecondStates)
        for FirstAccess in sorted(FactorsBySignal[FirstSignal]):
            FirstStates = StatesBySignalAndLocalAccess[FirstSignal, FirstAccess]
            SupportedSecondStateMask = 0
            for FirstState in FirstStates:
                CompatibilityCheckCount += len(FlattenedSecondStates)
                if WorkCheck is not None and CompatibilityCheckCount % 128 < len(FlattenedSecondStates):
                    WorkCheck({'Stage': 'physical-symbolic-port-pair-compatibility', 'SignalPair': list(Signals), 'CompatibilityCheckCount': CompatibilityCheckCount, 'CompatibilityIndexKind': 'flattened-claim-cell-bitset-v2'})
                ConflictMask = BuildFirstStateConflictMask(FirstState, SecondIndexes, AllSecondStatesMask)
                SupportedSecondStateMask |= AllSecondStatesMask & ~ConflictMask
                if SupportedSecondStateMask == AllSecondStatesMask:
                    break
            for SecondAccess in sorted(FactorsBySignal[SecondSignal]):
                if SupportedSecondStateMask & SecondAccessMasks[SecondAccess]:
                    continue
                UnsupportedLocalAccessPairs.append(((FirstSignal, FirstAccess), (SecondSignal, SecondAccess)))
    elif Complete:
        FirstSignal, SecondSignal = Signals

        def NormalizeStateDomain(States: Iterable[Any]) -> tuple[Any, ...]:
            StateByFingerprint = {}
            for State in States:
                Fingerprint = str(State.NetFingerprint)
                Existing = StateByFingerprint.get(Fingerprint)
                if Existing is not None and Existing != State:
                    raise ValueError('port-pair symbolic net-state identity collision')
                StateByFingerprint[Fingerprint] = State
            return tuple((StateByFingerprint[Fingerprint] for Fingerprint in sorted(StateByFingerprint)))
        CanonicalSignals = tuple(sorted(Problem.ComponentSignals))
        DomainsToSearch = tuple((NormalizeStateDomain((State for Access in sorted(FactorsBySignal[Signal]) for State in StatesBySignalAndLocalAccess[Signal, Access]) if Signal in Signals else MandatoryStateDomainsBySignal[Signal]) for Signal in CanonicalSignals))
        StateIndexByFingerprint = tuple(({str(State.NetFingerprint): Index for Index, State in enumerate(Domain)} for Domain in DomainsToSearch))
        AccessMasks = {}
        SignalIndexByName = {Signal: Index for Index, Signal in enumerate(CanonicalSignals)}
        for Signal in Signals:
            SignalIndex = SignalIndexByName[Signal]
            for Access in sorted(FactorsBySignal[Signal]):
                Mask = 0
                for State in StatesBySignalAndLocalAccess[Signal, Access]:
                    Mask |= 1 << StateIndexByFingerprint[SignalIndex][str(State.NetFingerprint)]
                AccessMasks[Signal, Access] = Mask
        CompatibilityIndexFingerprint = _Fingerprint(('complete-component-compatibility-index-v2', Problem.PlacementFingerprint, FactorDomain.DomainFingerprint, tuple(((Signal, tuple((State.NetFingerprint for State in Domain))) for Signal, Domain in zip(CanonicalSignals, DomainsToSearch)))))
        CachedCompatibilityIndex = CompleteCompatibilityIndexCache.get(CompatibilityIndexFingerprint) if CompleteCompatibilityIndexCache is not None else None
        if CachedCompatibilityIndex is None:
            PairCompatibleStateMasks: dict[tuple[int, int, int], int] = {}
            for FirstDomainIndex in range(len(DomainsToSearch)):
                FirstDomain = DomainsToSearch[FirstDomainIndex]
                for SecondDomainIndex in range(FirstDomainIndex + 1, len(DomainsToSearch)):
                    SecondDomain = DomainsToSearch[SecondDomainIndex]
                    SecondIndexes, AllSecondStatesMask = BuildConflictIndexes(SecondDomain)
                    for FirstStateIndex, State in enumerate(FirstDomain):
                        ConflictMask = BuildFirstStateConflictMask(State, SecondIndexes, AllSecondStatesMask)
                        CompatibleMask = AllSecondStatesMask & ~ConflictMask
                        PairCompatibleStateMasks[FirstDomainIndex, FirstStateIndex, SecondDomainIndex] = CompatibleMask
                        CompatibilityCheckCount += len(SecondDomain)
                        if WorkCheck is not None and CompatibilityCheckCount % 16384 < len(SecondDomain):
                            WorkCheck({'Stage': 'physical-symbolic-port-pair-complete-component-compatibility', 'SignalPair': list(Signals), 'MandatorySignalDomainCount': len(MandatoryStateDomains), 'CompatibilityCheckCount': CompatibilityCheckCount, 'CompatibilityIndexKind': 'normalized-claim-cell-bitset-v2'})
                    FirstIndexes, AllFirstStatesMask = BuildConflictIndexes(FirstDomain)
                    for SecondStateIndex, State in enumerate(SecondDomain):
                        ConflictMask = BuildFirstStateConflictMask(State, FirstIndexes, AllFirstStatesMask)
                        PairCompatibleStateMasks[SecondDomainIndex, SecondStateIndex, FirstDomainIndex] = AllFirstStatesMask & ~ConflictMask
                        CompatibilityCheckCount += len(FirstDomain)
                        if WorkCheck is not None and CompatibilityCheckCount % 16384 < len(FirstDomain):
                            WorkCheck({'Stage': 'physical-symbolic-port-pair-complete-component-compatibility', 'SignalPair': list(Signals), 'MandatorySignalDomainCount': len(MandatoryStateDomains), 'CompatibilityCheckCount': CompatibilityCheckCount, 'CompatibilityIndexKind': 'normalized-claim-cell-bitset-v2'})
            FailedCompatibilityResiduals: set[tuple[int, tuple[int, ...]]] = set()
            CompleteCompatibilityWitnesses: list[tuple[int, ...]] = []
            UnsupportedRestrictionMasks: dict[tuple[int, int], list[tuple[int, int]]] = {}
            ArcConsistencyCache: dict[tuple[int, tuple[int, ...]], tuple[int, ...] | None] = {}
            CachedCompatibilityIndex = {'PairCompatibleStateMasks': PairCompatibleStateMasks, 'FailedCompatibilityResiduals': FailedCompatibilityResiduals, 'CompleteCompatibilityWitnesses': CompleteCompatibilityWitnesses, 'UnsupportedRestrictionMasks': UnsupportedRestrictionMasks, 'ArcConsistencyCache': ArcConsistencyCache}
            if CompleteCompatibilityIndexCache is not None:
                CompleteCompatibilityIndexCache[CompatibilityIndexFingerprint] = CachedCompatibilityIndex
            CompatibilityIndexCacheHit = False
        else:
            PairCompatibleStateMasks = CachedCompatibilityIndex['PairCompatibleStateMasks']
            FailedCompatibilityResiduals = CachedCompatibilityIndex['FailedCompatibilityResiduals']
            CompleteCompatibilityWitnesses = CachedCompatibilityIndex['CompleteCompatibilityWitnesses']
            UnsupportedRestrictionMasks = CachedCompatibilityIndex['UnsupportedRestrictionMasks']
            ArcConsistencyCache = CachedCompatibilityIndex.setdefault('ArcConsistencyCache', {})
            CompatibilityIndexCacheHit = True
        if WorkCheck is not None:
            WorkCheck({'Stage': 'physical-symbolic-port-pair-complete-component-compatibility-index-complete', 'SignalPair': list(Signals), 'MandatorySignalDomainCount': len(MandatoryStateDomains), 'NormalizedStateDomainSizes': [len(Domain) for Domain in DomainsToSearch], 'CompatibilityCheckCount': CompatibilityCheckCount, 'CompatibilityIndexKind': 'normalized-claim-cell-bitset-v2', 'CompatibilityIndexCacheHit': CompatibilityIndexCacheHit})

        def HasCompleteComponentSupport(FirstAccess: str, SecondAccess: str) -> bool:
            InitialMasksList = [(1 << len(Domain)) - 1 for Domain in DomainsToSearch]
            InitialMasksList[SignalIndexByName[FirstSignal]] = AccessMasks[FirstSignal, FirstAccess]
            InitialMasksList[SignalIndexByName[SecondSignal]] = AccessMasks[SecondSignal, SecondAccess]
            InitialMasks = tuple(InitialMasksList)
            FirstSignalIndex = SignalIndexByName[FirstSignal]
            SecondSignalIndex = SignalIndexByName[SecondSignal]
            if any((Witness[FirstSignalIndex] & InitialMasks[FirstSignalIndex] and Witness[SecondSignalIndex] & InitialMasks[SecondSignalIndex] for Witness in CompleteCompatibilityWitnesses)):
                return True
            RestrictionKey = (FirstSignalIndex, SecondSignalIndex)
            if any((not (InitialMasks[FirstSignalIndex] & ~FirstMask or InitialMasks[SecondSignalIndex] & ~SecondMask) for FirstMask, SecondMask in UnsupportedRestrictionMasks.get(RestrictionKey, ()))):
                return False
            SelectedStateBits = [0] * len(DomainsToSearch)

            def PropagateArcConsistency(RemainingDomains: int, AllowedMasks: tuple[int, ...]) -> tuple[int, ...] | None:
                """Remove every state lacking support in a live domain.

                Exact complete-component support is a binary-constraint CSP
                over immutable symbolic net states.  Forward checking only
                against the most recently selected state leaves large
                mutually incompatible residual products intact.  Bitset arc
                consistency reaches the required fixed point before search
                and is shared by every aperture-pair restriction.
                """
                CacheKey = (RemainingDomains, AllowedMasks)
                if CacheKey in ArcConsistencyCache:
                    return ArcConsistencyCache[CacheKey]
                MutableMasks = list(AllowedMasks)
                RemainingIndexes = tuple((Index for Index in range(len(DomainsToSearch)) if RemainingDomains & 1 << Index))
                Changed = True
                while Changed:
                    Changed = False
                    for SourceIndex in RemainingIndexes:
                        SourceMask = MutableMasks[SourceIndex]
                        SupportedSourceMask = 0
                        CandidateMask = SourceMask
                        while CandidateMask:
                            CandidateBit = CandidateMask & -CandidateMask
                            CandidateMask ^= CandidateBit
                            CandidateStateIndex = CandidateBit.bit_length() - 1
                            if all((TargetIndex == SourceIndex or bool(PairCompatibleStateMasks[SourceIndex, CandidateStateIndex, TargetIndex] & MutableMasks[TargetIndex]) for TargetIndex in RemainingIndexes)):
                                SupportedSourceMask |= CandidateBit
                        if not SupportedSourceMask:
                            ArcConsistencyCache[CacheKey] = None
                            return None
                        if SupportedSourceMask != SourceMask:
                            MutableMasks[SourceIndex] = SupportedSourceMask
                            Changed = True
                Result = tuple(MutableMasks)
                ArcConsistencyCache[CacheKey] = Result
                return Result

            def Search(RemainingDomains: int, AllowedMasks: tuple[int, ...]) -> bool:
                if not RemainingDomains:
                    return True
                PropagatedMasks = PropagateArcConsistency(RemainingDomains, AllowedMasks)
                if PropagatedMasks is None:
                    return False
                AllowedMasks = PropagatedMasks
                ResidualKey = (RemainingDomains, AllowedMasks)
                if ResidualKey in FailedCompatibilityResiduals:
                    return False
                RemainingIndexes = tuple((Index for Index in range(len(DomainsToSearch)) if RemainingDomains & 1 << Index))
                SelectedIndex = min(RemainingIndexes, key=lambda Index: (AllowedMasks[Index].bit_count(), Index))
                CandidateMask = AllowedMasks[SelectedIndex]
                NextRemainingDomains = RemainingDomains & ~(1 << SelectedIndex)
                while CandidateMask:
                    CandidateBit = CandidateMask & -CandidateMask
                    CandidateMask ^= CandidateBit
                    CandidateStateIndex = CandidateBit.bit_length() - 1
                    SelectedStateBits[SelectedIndex] = CandidateBit
                    NextMasks = list(AllowedMasks)
                    NextMasks[SelectedIndex] = 0
                    Viable = True
                    for TargetIndex in RemainingIndexes:
                        if TargetIndex == SelectedIndex:
                            continue
                        NextMasks[TargetIndex] &= PairCompatibleStateMasks[SelectedIndex, CandidateStateIndex, TargetIndex]
                        if not NextMasks[TargetIndex]:
                            Viable = False
                            break
                    if Viable and Search(NextRemainingDomains, tuple(NextMasks)):
                        return True
                    SelectedStateBits[SelectedIndex] = 0
                FailedCompatibilityResiduals.add(ResidualKey)
                return False
            if any((not Mask for Mask in InitialMasks)):
                return False
            Supported = Search((1 << len(DomainsToSearch)) - 1, InitialMasks)
            if Supported:
                CompleteCompatibilityWitnesses.append(tuple(SelectedStateBits))
                return True
            UnsupportedRestrictionMasks.setdefault(RestrictionKey, []).append((InitialMasks[FirstSignalIndex], InitialMasks[SecondSignalIndex]))
            return False
        for FirstAccess in sorted(FactorsBySignal[FirstSignal]):
            for SecondAccess in sorted(FactorsBySignal[SecondSignal]):
                if WorkCheck is not None:
                    WorkCheck({'Stage': 'physical-symbolic-port-pair-complete-component-compatibility', 'SignalPair': list(Signals), 'MandatorySignalDomainCount': len(MandatoryStateDomains), 'CompatibilityCheckCount': CompatibilityCheckCount})
                if HasCompleteComponentSupport(FirstAccess, SecondAccess):
                    continue
                UnsupportedLocalAccessPairs.append(((FirstSignal, FirstAccess), (SecondSignal, SecondAccess)))
    UnsupportedUnaryLocalAccessSet = frozenset(UnsupportedUnaryLocalAccess)
    UnsupportedLocalAccessPairSet = frozenset((frozenset(Value) for Value in UnsupportedLocalAccessPairs))
    AccessesBySignalAndSeam: dict[tuple[str, str], set[str]] = {}
    for (Signal, LocalAccessFingerprint), SeamFingerprint in SeamFingerprintByLocalAccess.items():
        AccessesBySignalAndSeam.setdefault((Signal, SeamFingerprint), set()).add(LocalAccessFingerprint)
    UnsupportedUnarySeams = tuple(sorted(((Signal, SeamFingerprint) for (Signal, SeamFingerprint), Accesses in AccessesBySignalAndSeam.items() if all(((Signal, Access) in UnsupportedUnaryLocalAccessSet for Access in Accesses)))))
    UnsupportedSeamPairs = []
    if Complete:
        FirstSignal, SecondSignal = Signals
        FirstSeams = tuple(sorted((Seam for Signal, Seam in AccessesBySignalAndSeam if Signal == FirstSignal)))
        SecondSeams = tuple(sorted((Seam for Signal, Seam in AccessesBySignalAndSeam if Signal == SecondSignal)))
        for FirstSeam in FirstSeams:
            for SecondSeam in SecondSeams:
                if all(((FirstSignal, FirstAccess) in UnsupportedUnaryLocalAccessSet or (SecondSignal, SecondAccess) in UnsupportedUnaryLocalAccessSet or frozenset(((FirstSignal, FirstAccess), (SecondSignal, SecondAccess))) in UnsupportedLocalAccessPairSet for FirstAccess in AccessesBySignalAndSeam[FirstSignal, FirstSeam] for SecondAccess in AccessesBySignalAndSeam[SecondSignal, SecondSeam])):
                    UnsupportedSeamPairs.append(((FirstSignal, FirstSeam), (SecondSignal, SecondSeam)))
    NetStateBindingsTuple = tuple(sorted(NetStateBindings))
    NetStateDomainFingerprint = _Fingerprint(('physical-symbolic-port-pair-state-bindings-v1', NetStateBindingsTuple))
    LocalAccessFingerprintsBySignal = tuple(((Signal, tuple(sorted(FactorsBySignal[Signal]))) for Signal in Signals))
    ProofFingerprint = _Fingerprint(('physical-symbolic-port-pair-proof-v2', DomainFingerprint, LocalAccessFingerprintsBySignal, UnsupportedUnaryLocalAccess, tuple(UnsupportedLocalAccessPairs), UnsupportedUnarySeams, tuple(UnsupportedSeamPairs), NetStateDomainFingerprint, Complete))
    Certificate = PhysicalComponentSymbolicPortPairCertificate(DomainFingerprint=DomainFingerprint, PreparedDomainFingerprint=Context['PreparedDomainFingerprint'], PlacementFingerprint=Context['PlacementFingerprint'], ComponentGraphFingerprint=Context['ComponentGraphFingerprint'], FabricFingerprint=Context['FabricFingerprint'], ResourceGraphFingerprint=Context['ResourceGraphFingerprint'], TechnologyFingerprint=Context['TechnologyFingerprint'], AccessCertificateFingerprint=Context['AccessCertificateFingerprint'], InterfaceFingerprint=Context['InterfaceFingerprint'], LocalAccessDomainFingerprint=Context['LocalAccessDomainFingerprint'], SeamDomainFingerprint=Context['SeamDomainFingerprint'], SignalPair=Signals, LocalAccessFingerprintsBySignal=LocalAccessFingerprintsBySignal, SeamFingerprintByLocalAccess=tuple(sorted(((Signal, LocalAccessFingerprint, SeamFingerprint) for (Signal, LocalAccessFingerprint), SeamFingerprint in SeamFingerprintByLocalAccess.items()))), SeamFingerprintsBySignal=tuple(((Signal, tuple(sorted({SeamFingerprintByLocalAccess[Signal, LocalAccessFingerprint] for LocalAccessFingerprint in FactorsBySignal[Signal]}))) for Signal in Signals)), UnsupportedUnaryLocalAccess=UnsupportedUnaryLocalAccess, UnsupportedLocalAccessPairs=tuple(UnsupportedLocalAccessPairs), UnsupportedUnarySeams=UnsupportedUnarySeams, UnsupportedSeamPairs=tuple(UnsupportedSeamPairs), NetStateCacheKeys=tuple(sorted(NetStateCacheKeys)), NetStateBindings=NetStateBindingsTuple, NetStateDomainFingerprint=NetStateDomainFingerprint, ProofFingerprint=ProofFingerprint, Complete=Complete)
    ValidatePhysicalComponentSymbolicPortPairCertificate(Certificate, Problem, FactorDomain, Signals, NetStateCache=EffectiveNetStateCache)
    if Complete and CompletedCertificateCache is not None:
        CompletedCertificateCache[DomainFingerprint] = Certificate
    return Certificate


def _BuildPhysicalComponentSymbolicHigherOrderContext(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Signals: tuple[str, ...],
    FactorsBySignal: dict[str, dict[str, Any]],
    SeamFingerprintByLocalAccess: dict[tuple[str, str], str],
) -> dict[str, str]:
    """Build immutable identities for a complete higher-order proof."""
    PairContext = _BuildPhysicalComponentSymbolicPortPairContext(
        Problem,
        FactorDomain,
        Signals,
        FactorsBySignal,
        SeamFingerprintByLocalAccess,
    )
    Result = dict(PairContext)
    Result["DomainFingerprint"] = _Fingerprint((
        "physical-symbolic-higher-order-domain-v1",
        Signals,
        tuple(
            (Name, Value)
            for Name, Value in sorted(PairContext.items())
            if Name != "DomainFingerprint"
        ),
    ))
    return Result


def ValidatePhysicalComponentSymbolicHigherOrderCertificate(
    Certificate: PhysicalComponentSymbolicHigherOrderCertificate,
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalDomain: Iterable[str],
    *,
    NetStateCache: dict[str, Any] | None = None,
) -> None:
    """Reject a higher-order proof if any structural identity has drifted."""
    Signals = tuple(sorted(frozenset(map(str, SignalDomain))))
    if len(Signals) < 3:
        raise ValueError(
            "symbolic higher-order validation requires at least three signals"
        )
    FactorsBySignal, SeamFingerprintByLocalAccess = (
        _SelectPhysicalComponentSymbolicPortPairFactors(
            FactorDomain,
            Signals,
        )
    )
    Context = _BuildPhysicalComponentSymbolicHigherOrderContext(
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
    Mismatches = [
        Name
        for Name, Expected in Context.items()
        if str(FieldValues.get(Name, "")) != str(Expected)
    ]
    ExpectedAccesses = tuple(
        (Signal, tuple(sorted(FactorsBySignal[Signal])))
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
    if Certificate.SignalDomain != Signals:
        Mismatches.append("SignalDomain")
    if Certificate.LocalAccessFingerprintsBySignal != ExpectedAccesses:
        Mismatches.append("LocalAccessFingerprintsBySignal")
    if Certificate.SeamFingerprintByLocalAccess != ExpectedSeams:
        Mismatches.append("SeamFingerprintByLocalAccess")
    BindingKeys = tuple(
        (Signal, LocalAccessFingerprint, CacheKey)
        for Signal, LocalAccessFingerprint, CacheKey, _StateFingerprint
        in Certificate.NetStateBindings
    )
    if BindingKeys != Certificate.NetStateCacheKeys:
        Mismatches.append("NetStateCacheKeys")
    ExpectedBindingFingerprint = _Fingerprint((
        "physical-symbolic-higher-order-state-bindings-v1",
        Certificate.NetStateBindings,
    ))
    if Certificate.NetStateDomainFingerprint != ExpectedBindingFingerprint:
        Mismatches.append("NetStateDomainFingerprint")
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
                Mismatches.append("NetStateCacheContents")
                break
    ExpectedProofFingerprint = _Fingerprint((
        "physical-symbolic-higher-order-proof-v1",
        Certificate.DomainFingerprint,
        Certificate.LocalAccessFingerprintsBySignal,
        Certificate.SupportedLocalAccessTuples,
        Certificate.SupportedSeamTuples,
        Certificate.NetStateDomainFingerprint,
        Certificate.CompatibilityCheckCount,
        Certificate.Complete,
    ))
    if Certificate.ProofFingerprint != ExpectedProofFingerprint:
        Mismatches.append("ProofFingerprint")
    if Mismatches:
        raise ValueError(
            "physical symbolic higher-order certificate identity mismatch: "
            + ", ".join(dict.fromkeys(Mismatches))
        )


def CompilePhysicalComponentSymbolicHigherOrderDomain(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalDomain: Iterable[str],
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    NetStateCache: dict[str, Any] | None = None,
    CompletedCertificateCache: dict[
        str, PhysicalComponentSymbolicHigherOrderCertificate
    ] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
) -> PhysicalComponentSymbolicHigherOrderCertificate:
    """Compile the exact joint local-state relation for 3+ signal seams."""
    if not FactorDomain.Complete or not FactorDomain.Feasible:
        raise ValueError(
            "higher-order compilation requires a complete feasible factor domain"
        )
    if Problem.PlacementFingerprint != FactorDomain.PlacementFingerprint:
        raise ValueError("higher-order placement identity mismatch")
    Signals = tuple(sorted(frozenset(map(str, SignalDomain))))
    if len(Signals) < 3:
        raise ValueError(
            "symbolic higher-order compilation requires at least three signals"
        )
    FactorsBySignal, SeamFingerprintByLocalAccess = (
        _SelectPhysicalComponentSymbolicPortPairFactors(
            FactorDomain,
            Signals,
        )
    )
    Context = _BuildPhysicalComponentSymbolicHigherOrderContext(
        Problem,
        FactorDomain,
        Signals,
        FactorsBySignal,
        SeamFingerprintByLocalAccess,
    )
    DomainFingerprint = Context["DomainFingerprint"]
    EffectiveNetStateCache = (
        NetStateCache if NetStateCache is not None else {}
    )
    Cached = (
        CompletedCertificateCache.get(DomainFingerprint)
        if CompletedCertificateCache is not None
        else None
    )
    if Cached is not None:
        ValidatePhysicalComponentSymbolicHigherOrderCertificate(
            Cached,
            Problem,
            FactorDomain,
            Signals,
            NetStateCache=EffectiveNetStateCache,
        )
        return Cached

    StartedAt = monotonic()
    RelaxedProblem = replace(Problem, ReservedGlobalClaimsBySignal=())
    PreparedNetStateContexts = {
        Signal: PrepareComponentSymbolicNetStateContext(
            RelaxedProblem,
            Signal,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        )
        for Signal in Signals
    }
    StatesBySignalAndLocalAccess: dict[
        tuple[str, str], tuple[Any, ...]
    ] = {}
    NetStateCacheKeys: list[tuple[str, str, str]] = []
    NetStateBindings: list[tuple[str, str, str, str]] = []
    Complete = True
    for Signal in Signals:
        VariantProblemsByAccess = {
            LocalAccessFingerprint: (
                _BuildPhysicalComponentSymbolicPortPairVariantProblem(
                    RelaxedProblem,
                    Signal,
                    LocalAccessFingerprint,
                    Factor,
                )
            )
            for LocalAccessFingerprint, Factor
            in sorted(FactorsBySignal[Signal].items())
        }
        RemainingDeadline = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        CompilationsByAccess = (
            CompilePreparedComponentPhysicalFactorStateBatch(
                PreparedNetStateContexts[Signal],
                VariantProblemsByAccess,
                DeadlineSeconds=RemainingDeadline,
                WorkCheck=WorkCheck,
                SymbolicNetStateCache=EffectiveNetStateCache,
            )
        )
        for LocalAccessFingerprint in sorted(VariantProblemsByAccess):
            Compilation = CompilationsByAccess[LocalAccessFingerprint]
            if not Compilation.Complete or Compilation.States is None:
                Complete = False
                break
            States = tuple(Compilation.States)
            StatesBySignalAndLocalAccess[
                (Signal, LocalAccessFingerprint)
            ] = States
            NetStateCacheKeys.append((
                Signal,
                LocalAccessFingerprint,
                Compilation.CacheKey,
            ))
            NetStateBindings.append((
                Signal,
                LocalAccessFingerprint,
                Compilation.CacheKey,
                _BuildPhysicalComponentSymbolicNetStateFingerprint(States),
            ))
        if not Complete:
            break

    SupportedLocalAccessTuples: set[
        tuple[tuple[str, str], ...]
    ] = set()
    CompatibilityCheckCount = 0
    CompatibilitySearchStateCount = 0

    # Compile the higher-order relation once over deduplicated symbolic net
    # states.  The earlier access-tuple implementation repeated Python set
    # conflict checks for every local-factor product.  These immutable
    # bitsets turn the same exact problem into a k-partite compatibility CSP
    # whose state relation is reusable across every access and seam tuple.
    UniqueStatesBySignal: dict[str, tuple[Any, ...]] = {}
    AccessStateMasks: dict[tuple[str, str], int] = {}
    PairCompatibleStateMasks: dict[tuple[int, int, int], int] = {}
    if Complete:
        for Signal in Signals:
            StateByFingerprint: dict[str, Any] = {}
            for Access in sorted(FactorsBySignal[Signal]):
                for State in StatesBySignalAndLocalAccess[(Signal, Access)]:
                    Fingerprint = str(State.NetFingerprint)
                    Existing = StateByFingerprint.get(Fingerprint)
                    if Existing is not None and Existing != State:
                        raise ValueError(
                            "higher-order symbolic net-state identity "
                            "collision"
                        )
                    StateByFingerprint[Fingerprint] = State
            States = tuple(
                StateByFingerprint[Fingerprint]
                for Fingerprint in sorted(StateByFingerprint)
            )
            UniqueStatesBySignal[Signal] = States
            StateIndexByFingerprint = {
                str(State.NetFingerprint): Index
                for Index, State in enumerate(States)
            }
            for Access in sorted(FactorsBySignal[Signal]):
                Mask = 0
                for State in StatesBySignalAndLocalAccess[(Signal, Access)]:
                    Mask |= 1 << StateIndexByFingerprint[
                        str(State.NetFingerprint)
                    ]
                AccessStateMasks[(Signal, Access)] = Mask

        for FirstSignalIndex in range(len(Signals)):
            FirstSignal = Signals[FirstSignalIndex]
            FirstStates = UniqueStatesBySignal[FirstSignal]
            for SecondSignalIndex in range(
                FirstSignalIndex + 1,
                len(Signals),
            ):
                SecondSignal = Signals[SecondSignalIndex]
                SecondStates = UniqueStatesBySignal[SecondSignal]
                ReverseMasks = [0] * len(SecondStates)
                SecondClaimIndexes: dict[
                    str, dict[Position3, int]
                ] = {
                    "Wire": {},
                    "Support": {},
                    "Air": {},
                    "Electrical": {},
                }
                for SecondStateIndex, SecondState in enumerate(
                    SecondStates
                ):
                    StateBit = 1 << SecondStateIndex
                    for Name, Cells in (
                        ("Wire", SecondState.Claims.WireCells),
                        ("Support", SecondState.Claims.SupportCells),
                        ("Air", SecondState.Claims.RequiredAirCells),
                        (
                            "Electrical",
                            SecondState.Claims.ElectricalCells,
                        ),
                    ):
                        IndexByCell = SecondClaimIndexes[Name]
                        for Cell in Cells:
                            IndexByCell[Cell] = (
                                IndexByCell.get(Cell, 0) | StateBit
                            )
                AllSecondStatesMask = (1 << len(SecondStates)) - 1
                for FirstStateIndex, FirstState in enumerate(FirstStates):
                    PriorCheckCount = CompatibilityCheckCount
                    CompatibilityCheckCount += len(SecondStates)
                    if (
                        WorkCheck is not None
                        and PriorCheckCount // 1024
                        != CompatibilityCheckCount // 1024
                    ):
                        WorkCheck({
                            "Stage": (
                                "physical-symbolic-higher-order-"
                                "compatibility-index"
                            ),
                            "SignalDomain": list(Signals),
                            "CompatibilityCheckCount": (
                                CompatibilityCheckCount
                            ),
                        })
                    ConflictMask = 0

                    def AddConflicts(
                        Cells: Iterable[Position3],
                        Names: tuple[str, ...],
                    ) -> None:
                        nonlocal ConflictMask
                        for Cell in Cells:
                            for Name in Names:
                                ConflictMask |= (
                                    SecondClaimIndexes[Name].get(Cell, 0)
                                )
                            if ConflictMask == AllSecondStatesMask:
                                return

                    AddConflicts(
                        FirstState.Claims.WireCells,
                        ("Wire", "Support", "Air", "Electrical"),
                    )
                    if ConflictMask != AllSecondStatesMask:
                        AddConflicts(
                            FirstState.Claims.SupportCells,
                            ("Wire", "Air"),
                        )
                    if ConflictMask != AllSecondStatesMask:
                        AddConflicts(
                            FirstState.Claims.RequiredAirCells,
                            ("Wire", "Support"),
                        )
                    if ConflictMask != AllSecondStatesMask:
                        AddConflicts(
                            FirstState.Claims.ElectricalCells,
                            ("Wire",),
                        )
                    CompatibleMask = (
                        AllSecondStatesMask & ~ConflictMask
                    )
                    CompatibleStateMask = CompatibleMask
                    while CompatibleStateMask:
                        StateBit = (
                            CompatibleStateMask & -CompatibleStateMask
                        )
                        CompatibleStateMask ^= StateBit
                        SecondStateIndex = StateBit.bit_length() - 1
                        ReverseMasks[SecondStateIndex] |= (
                            1 << FirstStateIndex
                        )
                    PairCompatibleStateMasks[(
                        FirstSignalIndex,
                        FirstStateIndex,
                        SecondSignalIndex,
                    )] = CompatibleMask
                for SecondStateIndex, ReverseMask in enumerate(
                    ReverseMasks
                ):
                    PairCompatibleStateMasks[(
                        SecondSignalIndex,
                        SecondStateIndex,
                        FirstSignalIndex,
                    )] = ReverseMask

    CompatibilitySearchCache: dict[
        tuple[tuple[int, ...], tuple[int, ...]], bool
    ] = {}

    def HasCompatibleStateTuple(
        AccessTuple: tuple[tuple[str, str], ...],
    ) -> bool:
        nonlocal CompatibilitySearchStateCount
        AllowedMasks = tuple(
            AccessStateMasks[Value] for Value in AccessTuple
        )
        if any(not Mask for Mask in AllowedMasks):
            return False

        def Search(
            RemainingIndexes: tuple[int, ...],
            CurrentMasks: tuple[int, ...],
        ) -> bool:
            nonlocal CompatibilitySearchStateCount
            if not RemainingIndexes:
                return True
            CacheKey = (RemainingIndexes, CurrentMasks)
            Cached = CompatibilitySearchCache.get(CacheKey)
            if Cached is not None:
                return Cached
            CompatibilitySearchStateCount += 1
            if (
                WorkCheck is not None
                and CompatibilitySearchStateCount % 1024 == 0
            ):
                WorkCheck({
                    "Stage": (
                        "physical-symbolic-higher-order-compatibility-csp"
                    ),
                    "SignalDomain": list(Signals),
                    "CompatibilityCheckCount": CompatibilityCheckCount,
                    "CompatibilitySearchStateCount": (
                        CompatibilitySearchStateCount
                    ),
                })
            SelectedIndex = min(
                RemainingIndexes,
                key=lambda Index: (
                    CurrentMasks[Index].bit_count(),
                    Index,
                ),
            )
            NextRemaining = tuple(
                Index for Index in RemainingIndexes
                if Index != SelectedIndex
            )
            CandidateMask = CurrentMasks[SelectedIndex]
            while CandidateMask:
                StateBit = CandidateMask & -CandidateMask
                CandidateMask ^= StateBit
                StateIndex = StateBit.bit_length() - 1
                NextMasks = list(CurrentMasks)
                NextMasks[SelectedIndex] = 0
                Feasible = True
                for OtherIndex in NextRemaining:
                    NextMasks[OtherIndex] &= (
                        PairCompatibleStateMasks.get(
                            (SelectedIndex, StateIndex, OtherIndex),
                            0,
                        )
                    )
                    if not NextMasks[OtherIndex]:
                        Feasible = False
                        break
                if Feasible and Search(
                    NextRemaining,
                    tuple(NextMasks),
                ):
                    CompatibilitySearchCache[CacheKey] = True
                    return True
            CompatibilitySearchCache[CacheKey] = False
            return False

        return Search(tuple(range(len(Signals))), AllowedMasks)

    if Complete:
        AccessDomains = tuple(
            tuple(sorted(FactorsBySignal[Signal]))
            for Signal in Signals
        )
        for AccessTupleIndex, AccessValues in enumerate(
            product(*AccessDomains)
        ):
            AccessTuple = tuple(zip(Signals, AccessValues))
            if (
                WorkCheck is not None
                and AccessTupleIndex % 128 == 0
            ):
                WorkCheck({
                    "Stage": "physical-symbolic-higher-order-compatibility",
                    "SignalDomain": list(Signals),
                    "CompatibilityCheckCount": CompatibilityCheckCount,
                    "CompatibilitySearchStateCount": (
                        CompatibilitySearchStateCount
                    ),
                    "AccessTupleCount": AccessTupleIndex,
            })
            if HasCompatibleStateTuple(AccessTuple):
                SupportedLocalAccessTuples.add(AccessTuple)

    AccessesBySignalAndSeam: dict[tuple[str, str], tuple[str, ...]] = {}
    for Signal in Signals:
        for Seam in sorted({
            SeamFingerprintByLocalAccess[(Signal, Access)]
            for Access in FactorsBySignal[Signal]
        }):
            AccessesBySignalAndSeam[(Signal, Seam)] = tuple(sorted(
                Access
                for Access in FactorsBySignal[Signal]
                if SeamFingerprintByLocalAccess[(Signal, Access)] == Seam
            ))
    SupportedSeamTuples: frozenset[
        tuple[tuple[str, str], ...]
    ] = frozenset()
    if Complete:
        SupportedSeamTuples = frozenset(
            tuple(
                (
                    Signal,
                    SeamFingerprintByLocalAccess[(Signal, Access)],
                )
                for Signal, Access in AccessTuple
            )
            for AccessTuple in SupportedLocalAccessTuples
        )

    NetStateBindingsTuple = tuple(sorted(NetStateBindings))
    NetStateDomainFingerprint = _Fingerprint((
        "physical-symbolic-higher-order-state-bindings-v1",
        NetStateBindingsTuple,
    ))
    LocalAccessFingerprintsBySignal = tuple(
        (Signal, tuple(sorted(FactorsBySignal[Signal])))
        for Signal in Signals
    )
    SupportedLocalAccessTuplesTuple = tuple(sorted(
        SupportedLocalAccessTuples
    ))
    SupportedSeamTuplesTuple = tuple(sorted(
        SupportedSeamTuples
    ))
    ProofFingerprint = _Fingerprint((
        "physical-symbolic-higher-order-proof-v1",
        DomainFingerprint,
        LocalAccessFingerprintsBySignal,
        SupportedLocalAccessTuplesTuple,
        SupportedSeamTuplesTuple,
        NetStateDomainFingerprint,
        CompatibilityCheckCount,
        Complete,
    ))
    Certificate = PhysicalComponentSymbolicHigherOrderCertificate(
        DomainFingerprint=DomainFingerprint,
        PreparedDomainFingerprint=Context["PreparedDomainFingerprint"],
        PlacementFingerprint=Context["PlacementFingerprint"],
        ComponentGraphFingerprint=Context["ComponentGraphFingerprint"],
        FabricFingerprint=Context["FabricFingerprint"],
        ResourceGraphFingerprint=Context["ResourceGraphFingerprint"],
        TechnologyFingerprint=Context["TechnologyFingerprint"],
        AccessCertificateFingerprint=(
            Context["AccessCertificateFingerprint"]
        ),
        InterfaceFingerprint=Context["InterfaceFingerprint"],
        LocalAccessDomainFingerprint=(
            Context["LocalAccessDomainFingerprint"]
        ),
        SeamDomainFingerprint=Context["SeamDomainFingerprint"],
        SignalDomain=Signals,
        LocalAccessFingerprintsBySignal=LocalAccessFingerprintsBySignal,
        SeamFingerprintByLocalAccess=tuple(sorted(
            (
                Signal,
                Access,
                Seam,
            )
            for (Signal, Access), Seam
            in SeamFingerprintByLocalAccess.items()
        )),
        SeamFingerprintsBySignal=tuple(
            (
                Signal,
                tuple(sorted(
                    Seam
                    for DomainSignal, Seam in AccessesBySignalAndSeam
                    if DomainSignal == Signal
                )),
            )
            for Signal in Signals
        ),
        SupportedLocalAccessTuples=(
            SupportedLocalAccessTuplesTuple
        ),
        SupportedSeamTuples=SupportedSeamTuplesTuple,
        NetStateCacheKeys=tuple(sorted(NetStateCacheKeys)),
        NetStateBindings=NetStateBindingsTuple,
        NetStateDomainFingerprint=NetStateDomainFingerprint,
        ProofFingerprint=ProofFingerprint,
        CompatibilityCheckCount=CompatibilityCheckCount,
        Complete=Complete,
    )
    ValidatePhysicalComponentSymbolicHigherOrderCertificate(
        Certificate,
        Problem,
        FactorDomain,
        Signals,
        NetStateCache=EffectiveNetStateCache,
    )
    if Complete and CompletedCertificateCache is not None:
        CompletedCertificateCache[DomainFingerprint] = Certificate
    return Certificate


def CompilePhysicalComponentSymbolicUnaryApertureSignalWorker(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Signal: str,
    DeadlineAt: float | None,
) -> tuple[
    str,
    frozenset[frozenset[tuple[str, str]]],
    dict[str, Any],
    dict[str, Any],
]:
    """Compile one immutable unary signal snapshot in a child process."""
    # The parent owns the eight-way signal fan-out.  Keep each child at one
    # native routing worker so six unary processes do not each create an
    # additional eight-thread Rayon pool.
    os.environ["RC_ROUTING_THREADS"] = "1"
    RemainingDeadline = (
        None
        if DeadlineAt is None
        else max(0.0, DeadlineAt - monotonic())
    )
    SymbolicNetStateCache: dict[str, Any] = {}
    Clauses, Diagnostics = CompilePhysicalComponentSymbolicUnaryApertureDomain(
        Problem,
        FactorDomain,
        (Signal,),
        DeadlineSeconds=RemainingDeadline,
        NetStateCache=SymbolicNetStateCache,
        AllowParallelSignalCompilation=False,
    )
    return str(Signal), Clauses, Diagnostics, SymbolicNetStateCache


def CompilePhysicalComponentSymbolicUnaryApertureDomain(
    Problem: ComponentRoutingProblem,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    SignalDomain: Iterable[str],
    *,
    DeadlineSeconds: float | None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    NetStateCache: dict[str, Any] | None = None,
    CompletedClauseCache: dict[
        str,
        tuple[
            frozenset[frozenset[tuple[str, str]]],
            dict[str, Any],
        ],
    ] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    AllowParallelSignalCompilation: bool = True,
) -> tuple[
    frozenset[frozenset[tuple[str, str]]],
    dict[str, Any],
]:
    """Compile a requested signal domain and project complete unary cuts."""
    if not FactorDomain.Complete or not FactorDomain.Feasible:
        raise ValueError(
            "symbolic unary compilation requires a complete feasible domain"
        )
    if Problem.PlacementFingerprint != FactorDomain.PlacementFingerprint:
        raise ValueError("symbolic unary placement identity mismatch")
    Signals = tuple(sorted(frozenset(map(str, SignalDomain))))
    AvailableSignals = frozenset(
        str(Signal)
        for Signal, _Values in FactorDomain.LocalAccessFactorsBySignal
    )
    if not Signals or not frozenset(Signals) <= AvailableSignals:
        raise ValueError(
            "symbolic unary compilation requires available signals"
        )
    CacheKey = _Fingerprint((
        "physical-symbolic-unary-aperture-domain-v7",
        FactorDomain.DomainFingerprint,
        Problem.Fabric.FabricFingerprint,
        Signals,
    ))
    Cached = (
        CompletedClauseCache.get(CacheKey)
        if CompletedClauseCache is not None
        else None
    )
    if Cached is not None:
        Clauses, Diagnostics = Cached
        return Clauses, {**Diagnostics, "UnaryCertificateCacheHit": True}

    EffectiveNetStateCache = (
        NetStateCache if NetStateCache is not None else {}
    )

    # Each signal's local access factors are immutable after physical factor
    # preparation.  Compile them in separate CPython processes so the
    # Python-heavy frontier DP can use real cores despite the GIL.  The parent
    # remains the only writer of the shared caches and merges sorted results
    # deterministically.
    WorkerCount = min(8, len(Signals))
    # The parent owns the persistent local proof cache.  A spawned worker
    # receives a copy, so using it after a repair would discard the exact
    # local-state hits we prepared for this process.  The serial path below
    # consults that cache directly; a cold domain still uses all cores.
    if (
        AllowParallelSignalCompilation
        and WorkerCount > 1
        and not NetStateCache
    ):
        DeadlineAt = (
            None
            if DeadlineSeconds is None
            else monotonic() + max(0.0, DeadlineSeconds)
        )
        StartedParallelAt = monotonic()
        try:
            Context = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(
                max_workers=WorkerCount,
                mp_context=Context,
            ) as Executor:
                FuturesBySignal = {
                    Signal: Executor.submit(
                        CompilePhysicalComponentSymbolicUnaryApertureSignalWorker,
                        Problem,
                        FactorDomain,
                        Signal,
                        DeadlineAt,
                    )
                    for Signal in Signals
                }
                ResultsBySignal = {}
                for Signal in Signals:
                    Remaining = (
                        None
                        if DeadlineAt is None
                        else max(0.0, DeadlineAt - monotonic())
                    )
                    if Remaining is not None and Remaining <= 0.0:
                        return frozenset(), {
                            "Complete": False,
                            "Signal": Signal,
                            "CompiledAccessCount": 0,
                            "UnaryCertificateCacheHit": False,
                            "UnarySignalProcessWorkerCount": WorkerCount,
                            "UnarySignalProcessStatus": "deadline-expired",
                        }
                    ResultsBySignal[Signal] = FuturesBySignal[Signal].result(
                        timeout=Remaining,
                    )
        except TimeoutError:
            return frozenset(), {
                "Complete": False,
                "CompiledAccessCount": 0,
                "UnarySignalProcessWorkerCount": WorkerCount,
                "UnarySignalProcessStatus": "deadline-expired",
            }
        except Exception:
            # Spawn/pickle failures are environment-specific.  Preserve the
            # existing exact compiler rather than rejecting a legal design.
            ResultsBySignal = {}
        if ResultsBySignal:
            OrderedResults = tuple(
                ResultsBySignal[Signal]
                for Signal in Signals
            )
            Incomplete = next((
                (Signal, Diagnostics)
                for Signal, _Clauses, Diagnostics, _WorkerCache in OrderedResults
                if not Diagnostics.get("Complete", False)
            ), None)
            if Incomplete is not None:
                Signal, Diagnostics = Incomplete
                return frozenset(), {
                    **Diagnostics,
                    "Complete": False,
                    "Signal": Signal,
                    "UnarySignalProcessWorkerCount": WorkerCount,
                    "UnarySignalProcessStatus": "incomplete-worker-result",
                }
            Result = frozenset(
                Clause
                for _Signal, Clauses, _Diagnostics, _WorkerCache in OrderedResults
                for Clause in Clauses
            )
            for _Signal, _Clauses, _Diagnostics, WorkerCache in OrderedResults:
                for WorkerCacheKey, WorkerCacheValue in WorkerCache.items():
                    EffectiveNetStateCache.setdefault(
                        WorkerCacheKey,
                        WorkerCacheValue,
                    )
            Diagnostics = {
                "Complete": True,
                "SignalCount": len(Signals),
                "CompiledAccessCount": sum(
                    int(Diagnostics.get("CompiledAccessCount", 0))
                    for _Signal, _Clauses, Diagnostics, _WorkerCache in OrderedResults
                ),
                "UnsupportedLocalAccessCount": sum(
                    int(Diagnostics.get("UnsupportedLocalAccessCount", 0))
                    for _Signal, _Clauses, Diagnostics, _WorkerCache in OrderedResults
                ),
                "UnsupportedApertureOptionCount": sum(
                    int(Diagnostics.get("UnsupportedApertureOptionCount", 0))
                    for _Signal, _Clauses, Diagnostics, _WorkerCache in OrderedResults
                ),
                "UnsupportedLocalApertureSupportCount": sum(
                    int(Diagnostics.get(
                        "UnsupportedLocalApertureSupportCount",
                        0,
                    ))
                    for _Signal, _Clauses, Diagnostics, _WorkerCache in OrderedResults
                ),
                "UnaryLocalAccessClauseCount": sum(
                    int(Diagnostics.get("UnaryLocalAccessClauseCount", 0))
                    for _Signal, _Clauses, Diagnostics, _WorkerCache in OrderedResults
                ),
                "UnarySeamClauseCount": sum(
                    int(Diagnostics.get("UnarySeamClauseCount", 0))
                    for _Signal, _Clauses, Diagnostics, _WorkerCache in OrderedResults
                ),
                "UnaryApertureClauseCount": len(Result),
                "UnaryCertificateCacheHit": False,
                "DomainFingerprint": CacheKey,
                "UnarySignalProcessWorkerCount": WorkerCount,
                "UnarySignalProcessStatus": "complete",
                "UnarySignalProcessElapsedSeconds": (
                    monotonic() - StartedParallelAt
                ),
            }
            if CompletedClauseCache is not None:
                CompletedClauseCache[CacheKey] = (Result, Diagnostics)
            return Result, Diagnostics

    LocalFactorsBySignal = dict(FactorDomain.LocalAccessFactorsBySignal)
    SupportedAccessesBySignal = {
        str(Signal): frozenset(
            str(Support.LocalAccessFingerprint) for Support in Supports
        )
        for Signal, Supports
        in FactorDomain.LocalApertureSupportBySignal
    }
    RelaxedProblem = replace(Problem, ReservedGlobalClaimsBySignal=())
    StartedAt = monotonic()
    UnsupportedAccesses: set[tuple[str, str]] = set()
    CompiledStatesByAccess: dict[
        tuple[str, str], tuple[Any, ...]
    ] = {}
    CompiledAccessCount = 0
    for Signal in Signals:
        Factors = {
            str(Factor.LocalAccessFingerprint): Factor
            for Factor in LocalFactorsBySignal.get(Signal, ())
            if str(Factor.LocalAccessFingerprint)
            in SupportedAccessesBySignal.get(Signal, frozenset())
        }
        PreparedContext = PrepareComponentSymbolicNetStateContext(
            RelaxedProblem,
            Signal,
            RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        )
        VariantProblemsByAccess = {
            LocalAccessFingerprint: (
                _BuildPhysicalComponentSymbolicPortPairVariantProblem(
                    RelaxedProblem,
                    Signal,
                    LocalAccessFingerprint,
                    Factor,
                )
            )
            for LocalAccessFingerprint, Factor in sorted(Factors.items())
        }
        RemainingDeadline = (
            None
            if DeadlineSeconds is None
            else max(
                0.0,
                DeadlineSeconds - (monotonic() - StartedAt),
            )
        )
        CompilationsByAccess = (
            CompilePreparedComponentPhysicalFactorStateBatch(
                PreparedContext,
                VariantProblemsByAccess,
                DeadlineSeconds=RemainingDeadline,
                WorkCheck=WorkCheck,
                SymbolicNetStateCache=EffectiveNetStateCache,
            )
        )
        for LocalAccessFingerprint in sorted(VariantProblemsByAccess):
            Compilation = CompilationsByAccess[LocalAccessFingerprint]
            if not Compilation.Complete or Compilation.States is None:
                return frozenset(), {
                    "Complete": False,
                    "Signal": Signal,
                    "CompiledAccessCount": CompiledAccessCount,
                    "UnaryCertificateCacheHit": False,
                }
            CompiledAccessCount += 1
            CompiledStatesByAccess[(
                Signal,
                LocalAccessFingerprint,
            )] = tuple(Compilation.States)
            if not Compilation.States:
                UnsupportedAccesses.add((Signal, LocalAccessFingerprint))

    ApertureFactorsBySignal = dict(FactorDomain.ApertureFactorsBySignal)
    SupportsByOption = dict(FactorDomain.LocalApertureSupportsByOption)
    Clauses: set[frozenset[tuple[str, str]]] = set()
    UnsupportedLocalContracts: set[tuple[str, str]] = set()
    UnsupportedApertureOptions: set[tuple[str, str]] = set()
    UnsupportedLocalApertureSupports: set[tuple[str, str]] = set()

    for Signal in Signals:
        FactorsByAccess = {
            str(Factor.LocalAccessFingerprint): Factor
            for Factor in LocalFactorsBySignal.get(Signal, ())
        }
        for LocalAccessFingerprint in sorted(
            Access
            for CandidateSignal, Access in UnsupportedAccesses
            if CandidateSignal == Signal
        ):
            # The physical port CSP represents an option through its stable
            # local contract, seam contract, and aperture contract.  The
            # factor-local access fingerprint is a compilation cache key and
            # is deliberately not part of ``BuildPhysicalPortNoGoodKeys``.
            # Project the complete unary proof onto the solver-visible local
            # contract instead of publishing an inert cache identity.
            LocalFactor = FactorsByAccess[LocalAccessFingerprint]
            LocalContractKey = (
                Signal,
                str(LocalFactor.LocalContractFingerprint),
            )
            UnsupportedLocalContracts.add(LocalContractKey)
            Clauses.add(frozenset((LocalContractKey,)))
        AccessesBySeam: dict[str, set[str]] = {}
        for LocalAccessFingerprint, Factor in FactorsByAccess.items():
            SeamFingerprint = (
                str(getattr(Factor, "SeamContractFingerprint", ""))
                or BuildPhysicalPortSeamContractFingerprint(Factor)
            )
            AccessesBySeam.setdefault(SeamFingerprint, set()).add(
                LocalAccessFingerprint
            )
        for SeamFingerprint, LocalAccessFingerprints in (
            AccessesBySeam.items()
        ):
            if LocalAccessFingerprints and all(
                (Signal, LocalAccessFingerprint) in UnsupportedAccesses
                for LocalAccessFingerprint in LocalAccessFingerprints
            ):
                Clauses.add(frozenset(((Signal, SeamFingerprint),)))
        OptionsByContract: dict[str, list[Any]] = {}
        for Aperture in ApertureFactorsBySignal.get(Signal, ()):
            OptionsByContract.setdefault(
                str(Aperture.ApertureContractFingerprint),
                [],
            ).append(Aperture)
            OptionSupports = tuple(
                SupportsByOption.get((
                    Signal,
                    str(Aperture.ApertureOptionFingerprint),
                ), ())
            )
            ForbiddenGlobalNodes = (
                frozenset(Aperture.GlobalPath)
                - frozenset((Aperture.Attachment,))
            )
            SupportedEdgeCount = 0
            for Support in OptionSupports:
                EdgeSupported = any(
                    not (ForbiddenGlobalNodes & State.Nodes)
                    for State in CompiledStatesByAccess.get((
                        Signal,
                        str(Support.LocalAccessFingerprint),
                    ), ())
                )
                if EdgeSupported:
                    SupportedEdgeCount += 1
                    continue
                UnsupportedLocalApertureSupports.add((
                    Signal,
                    str(Support.SupportFingerprint),
                ))
                Clauses.add(frozenset(((
                    Signal,
                    str(Support.SupportFingerprint),
                ),)))
            if not SupportedEdgeCount:
                UnsupportedApertureOptions.add((
                    Signal,
                    str(Aperture.ApertureOptionFingerprint),
                ))
        for ApertureContract, Apertures in OptionsByContract.items():
            if Apertures and all(
                (
                    Signal,
                    str(Aperture.ApertureOptionFingerprint),
                ) in UnsupportedApertureOptions
                for Aperture in Apertures
            ):
                Clauses.add(frozenset(((Signal, ApertureContract),)))
    Result = frozenset(Clauses)
    Diagnostics = {
        "Complete": True,
        "SignalCount": len(Signals),
        "CompiledAccessCount": CompiledAccessCount,
        "UnsupportedLocalAccessCount": len(UnsupportedAccesses),
        "UnsupportedApertureOptionCount": len(
            UnsupportedApertureOptions
        ),
        "UnsupportedLocalApertureSupportCount": len(
            UnsupportedLocalApertureSupports
        ),
        "UnaryLocalAccessClauseCount": sum(
            1
            for Clause in Result
            if bool(Clause & UnsupportedLocalContracts)
        ),
        "UnarySeamClauseCount": sum(
            1
            for Clause in Result
            if any(
                str(Fingerprint).startswith("local-seam-contract-v1:")
                for _Signal, Fingerprint in Clause
            )
        ),
        "UnaryApertureClauseCount": len(Result),
        "UnaryCertificateCacheHit": False,
        "DomainFingerprint": CacheKey,
    }
    if CompletedClauseCache is not None:
        CompletedClauseCache[CacheKey] = (Result, Diagnostics)
    return Result, Diagnostics


def SelectPhysicalComponentResourceRelevantSignalPairs(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
) -> tuple[tuple[str, str], ...]:
    """Select only signal pairs whose exact local claims can intersect."""
    ResourceIdsBySignal = {
        str(Signal): frozenset(
            Resource
            for Factor in Factors
            for Resource in Factor.LocalClaims.ResourceIds
        )
        for Signal, Factors in FactorDomain.LocalAccessFactorsBySignal
    }
    Signals = tuple(sorted(ResourceIdsBySignal))
    return tuple(
        (FirstSignal, SecondSignal)
        for FirstIndex, FirstSignal in enumerate(Signals)
        for SecondSignal in Signals[FirstIndex + 1:]
        if ResourceIdsBySignal[FirstSignal].intersection(
            ResourceIdsBySignal[SecondSignal]
        )
    )


def CompilePhysicalComponentForeignPortalUnaryApertureClauses(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    RawPortalCache: Any,
    ResourceGraph: Any,
) -> tuple[
    frozenset[frozenset[tuple[str, str]]],
    dict[str, object],
]:
    """Reject apertures whose fixed claims erase a foreign terminal domain.

    The whole-design portal cache is complete before component CSP solving.
    Intersecting every portal path for one terminal recovers its immutable
    access stem; the union of those stems is mandatory for that ordinary net.
    If one component aperture conflicts with every portal alternative for a
    terminal, the resulting unary clause is exact and independent of any
    exterior route candidate later materialized for that aperture.
    """
    if RawPortalCache is None or ResourceGraph is None:
        return frozenset(), {
            "Complete": False,
            "Reason": "missing-portal-cache-or-resource-graph",
        }
    RawPortals = RawPortalCache.BuildPortalDictionary()
    CompleteKeys = frozenset(
        getattr(RawPortalCache, "CompletePortalDomainKeys", ())
    )
    LayerCount = int(getattr(RawPortalCache, "LayerCount", 0))
    ComponentSignals = frozenset(
        str(Signal)
        for Signal, _Options in (
            FactorDomain.BoundaryPortReservationsBySignal
        )
    )
    TerminalsBySignal: dict[str, set[Position3]] = {}
    for Signal, Terminal, _Layer in CompleteKeys:
        if str(Signal) in ComponentSignals:
            continue
        TerminalsBySignal.setdefault(str(Signal), set()).add(Terminal)
    ForeignDomains: list[
        tuple[str, Position3, tuple[RoutingResourceClaims, ...]]
    ] = []
    IncompleteTerminalCount = 0
    for Signal in sorted(TerminalsBySignal):
        PortalsByTerminal: dict[
            Position3, tuple[Any, ...]
        ] = {}
        MandatoryNodes: set[Position3] = set()
        for Terminal in sorted(TerminalsBySignal[Signal]):
            Keys = tuple(
                (Signal, Terminal, Layer)
                for Layer in range(LayerCount)
            )
            if not all(Key in CompleteKeys for Key in Keys):
                IncompleteTerminalCount += 1
                continue
            PortalsById = {
                Portal.PortalId: Portal
                for Key in Keys
                for Portal in RawPortals.get(Key, ())
            }
            Portals = tuple(sorted(
                PortalsById.values(),
                key=lambda Value: Value.PortalId,
            ))
            if not Portals:
                continue
            PortalsByTerminal[Terminal] = Portals
            CommonNodes = set(Portals[0].Path)
            for Portal in Portals[1:]:
                CommonNodes.intersection_update(Portal.Path)
            MandatoryNodes.update(CommonNodes)
        FrozenMandatoryNodes = frozenset(MandatoryNodes)
        for Terminal, Portals in sorted(PortalsByTerminal.items()):
            ForeignDomains.append((
                Signal,
                Terminal,
                tuple(
                    ResourceGraph.BuildRouteClaims(
                        FrozenMandatoryNodes | frozenset(Portal.Path)
                    )
                    for Portal in Portals
                ),
            ))
    Clauses: set[frozenset[tuple[str, str]]] = set()
    RejectedCountsBySignal: dict[str, int] = {}
    AperturePortalSlackBySignal: dict[
        str, dict[str, tuple[int, int]]
    ] = {}
    CompatibilityCheckCount = 0
    for Signal, Options in (
        FactorDomain.BoundaryPortReservationsBySignal
    ):
        for Option in Options:
            Unsupported = False
            MinimumRemainingAlternativeCount: int | None = None
            TotalRemainingAlternativeCount = 0
            for _ForeignSignal, _Terminal, ClaimsDomain in ForeignDomains:
                CompatibilityCheckCount += len(ClaimsDomain)
                ConflictCount = sum(
                    ComponentClaimsConflict(
                        Option.GlobalClaims,
                        Claims,
                    )
                    for Claims in ClaimsDomain
                )
                RemainingAlternativeCount = (
                    len(ClaimsDomain) - ConflictCount
                )
                MinimumRemainingAlternativeCount = min(
                    RemainingAlternativeCount,
                    (
                        MinimumRemainingAlternativeCount
                        if MinimumRemainingAlternativeCount is not None
                        else RemainingAlternativeCount
                    ),
                )
                TotalRemainingAlternativeCount += (
                    RemainingAlternativeCount
                )
                if ClaimsDomain and RemainingAlternativeCount == 0:
                    Unsupported = True
                    break
            StoredFingerprint = str(
                Option.ApertureContractFingerprint
            )
            CanonicalFingerprint = (
                BuildPhysicalPortApertureContractFingerprint(Option)
            )
            if not Unsupported:
                for Fingerprint in {
                    StoredFingerprint,
                    CanonicalFingerprint,
                }:
                    if Fingerprint:
                        AperturePortalSlackBySignal.setdefault(
                            str(Signal),
                            {},
                        )[Fingerprint] = (
                            int(MinimumRemainingAlternativeCount or 0),
                            int(TotalRemainingAlternativeCount),
                        )
                continue
            for Fingerprint in {
                StoredFingerprint,
                CanonicalFingerprint,
            }:
                if Fingerprint:
                    Clauses.add(frozenset(((
                        str(Signal),
                        Fingerprint,
                    ),)))
            RejectedCountsBySignal[str(Signal)] = (
                RejectedCountsBySignal.get(str(Signal), 0) + 1
            )
    return frozenset(Clauses), {
        "Complete": IncompleteTerminalCount == 0,
        "ForeignTerminalDomainCount": len(ForeignDomains),
        "IncompleteForeignTerminalDomainCount": IncompleteTerminalCount,
        "ComponentSignalCount": len(ComponentSignals),
        "ApertureOptionCount": sum(
            len(Options)
            for _Signal, Options in (
                FactorDomain.BoundaryPortReservationsBySignal
            )
        ),
        "RejectedApertureCount": len(Clauses),
        "RejectedApertureCountsBySignal": dict(sorted(
            RejectedCountsBySignal.items()
        )),
        "AperturePortalSlackBySignal": {
            Signal: dict(sorted(Values.items()))
            for Signal, Values in sorted(
                AperturePortalSlackBySignal.items()
            )
        },
        "CompatibilityCheckCount": CompatibilityCheckCount,
    }


def ProveClosedComponentSymbolicCapacityEligibility(
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
    SymbolicNetStateCache: dict[str, Any] | None = None,
) -> ComponentRoutingSolveResult:
    """Certify one selected local port tuple before routing its corridors.

    The proof deliberately removes reserved global-route claims.  It is a
    necessary local-capacity admission certificate for an already selected
    physical boundary, not local template compilation and not authority to
    choose a global boundary.
    """
    DomainFingerprint = BuildGlobalRelaxedLocalProofDomainFingerprint(
        Problem
    )
    Cached = (
        CompletedProofCache.get(DomainFingerprint)
        if CompletedProofCache is not None
        else None
    )
    if Cached is not None:
        return replace(
            Cached,
            Diagnostics={
                **dict(Cached.Diagnostics or {}),
                "SymbolicCapacityAdmissionDomainFingerprint": (
                    DomainFingerprint
                ),
                "SymbolicCapacityAdmissionCacheHit": True,
            },
        )
    RelaxedProblem = replace(
        Problem,
        ProblemFingerprint=_Fingerprint((
            "pre-global-symbolic-capacity-admission-v1",
            DomainFingerprint,
        )),
        ReservedGlobalClaimsBySignal=(),
    )
    Result = SolveComponentRoutingProblem(
        RelaxedProblem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        SymbolicNetStateCache=SymbolicNetStateCache,
        StopAfterSymbolicCapacityProof=True,
    )
    if Result.Template is not None:
        raise ValueError(
            "symbolic capacity eligibility materialized a template"
        )
    Result = replace(
        Result,
        Diagnostics={
            **dict(Result.Diagnostics or {}),
            "SymbolicCapacityAdmissionDomainFingerprint": (
                DomainFingerprint
            ),
            "SymbolicCapacityAdmissionCacheHit": False,
            "ReservedGlobalClaimsRemoved": True,
        },
    )
    Complete = bool(
        Result.Status == "capacity-feasible"
        or (
            Result.Status == "architectural-unsatisfiable"
            and Result.Diagnostics.get(
                "SymbolicCapacityProofComplete",
                False,
            )
        )
    )
    if CompletedProofCache is not None and Complete:
        CompletedProofCache[DomainFingerprint] = Result
    return Result


def ProjectCompletePhysicalPortPairCertificateToApertureClauses(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Certificate: PhysicalComponentSymbolicPortPairCertificate,
) -> tuple[
    frozenset[frozenset[tuple[str, str]]],
    dict[str, object],
]:
    """Project one complete local seam relation onto absolute apertures.

    Portable reservation fingerprints deliberately do not participate in this
    projection: they are translation-normalized and may alias distinct
    physical apertures.  A cut is sound only when every local-access factor in
    both certificate domains maps through the prepared support relation to an
    authoritative ``ApertureContractFingerprint``.  Any missing or ambiguous
    edge therefore suppresses the entire projection rather than publishing a
    partial global no-good.
    """
    Signals = tuple(map(str, Certificate.SignalPair))
    Diagnostics: dict[str, object] = {
        "PortPairCompatibilityComplete": bool(Certificate.Complete),
        "ApertureProjectionComplete": False,
        "ApertureProjectionSignals": list(Signals),
        "ApertureProjectionFailureReason": "",
    }
    if not Certificate.Complete or len(Signals) != 2 or len(set(Signals)) != 2:
        Diagnostics["ApertureProjectionFailureReason"] = (
            "pair-certificate-incomplete-or-invalid"
        )
        return frozenset(), Diagnostics

    ExpectedLocalAccessBySignal = {
        str(Signal): frozenset(map(str, LocalAccessFingerprints))
        for Signal, LocalAccessFingerprints
        in Certificate.LocalAccessFingerprintsBySignal
        if str(Signal) in Signals
    }
    if (
        set(ExpectedLocalAccessBySignal) != set(Signals)
        or any(
            not ExpectedLocalAccessBySignal.get(Signal)
            for Signal in Signals
        )
    ):
        Diagnostics["ApertureProjectionFailureReason"] = (
            "certificate-local-access-domain-incomplete"
        )
        return frozenset(), Diagnostics

    SeamByLocalAccess: dict[tuple[str, str], str] = {}
    for Signal, LocalAccessFingerprint, SeamFingerprint in (
        Certificate.SeamFingerprintByLocalAccess
    ):
        Key = (str(Signal), str(LocalAccessFingerprint))
        Seam = str(SeamFingerprint)
        Existing = SeamByLocalAccess.get(Key)
        if (
            Key[0] not in Signals
            or not Seam
            or (Existing is not None and Existing != Seam)
        ):
            Diagnostics["ApertureProjectionFailureReason"] = (
                "certificate-seam-map-incomplete-or-ambiguous"
            )
            return frozenset(), Diagnostics
        SeamByLocalAccess[Key] = Seam
    if any(
        set(
            LocalAccess
            for (CandidateSignal, LocalAccess) in SeamByLocalAccess
            if CandidateSignal == Signal
        ) != set(ExpectedLocalAccessBySignal[Signal])
        for Signal in Signals
    ):
        Diagnostics["ApertureProjectionFailureReason"] = (
            "certificate-seam-map-does-not-cover-local-domain"
        )
        return frozenset(), Diagnostics

    ApertureContractByOption: dict[tuple[str, str], str] = {}
    for Signal, Factors in FactorDomain.ApertureFactorsBySignal:
        Signal = str(Signal)
        if Signal not in Signals:
            continue
        for Factor in Factors:
            Key = (
                Signal,
                str(Factor.ApertureOptionFingerprint),
            )
            Contract = str(Factor.ApertureContractFingerprint)
            Existing = ApertureContractByOption.get(Key)
            if (
                not Key[1]
                or not Contract
                or (Existing is not None and Existing != Contract)
            ):
                Diagnostics["ApertureProjectionFailureReason"] = (
                    "aperture-option-contract-incomplete-or-ambiguous"
                )
                return frozenset(), Diagnostics
            ApertureContractByOption[Key] = Contract
    if any(
        not any(Key[0] == Signal for Key in ApertureContractByOption)
        for Signal in Signals
    ):
        Diagnostics["ApertureProjectionFailureReason"] = (
            "aperture-option-domain-incomplete"
        )
        return frozenset(), Diagnostics

    SupportsByOption: dict[tuple[str, str], list[str]] = {
        Key: [] for Key in ApertureContractByOption
    }
    MappedLocalAccessBySignal: dict[str, set[str]] = {
        Signal: set() for Signal in Signals
    }
    for Signal, Supports in FactorDomain.LocalApertureSupportBySignal:
        Signal = str(Signal)
        if Signal not in Signals:
            continue
        for Support in Supports:
            Key = (
                Signal,
                str(Support.ApertureOptionFingerprint),
            )
            LocalAccess = str(Support.LocalAccessFingerprint)
            if (
                Key not in ApertureContractByOption
                or LocalAccess not in ExpectedLocalAccessBySignal[Signal]
                or (Signal, LocalAccess) not in SeamByLocalAccess
            ):
                Diagnostics["ApertureProjectionFailureReason"] = (
                    "prepared-support-edge-unresolved"
                )
                return frozenset(), Diagnostics
            SupportsByOption[Key].append(LocalAccess)
            MappedLocalAccessBySignal[Signal].add(LocalAccess)
    if (
        any(not Values for Values in SupportsByOption.values())
        or any(
            MappedLocalAccessBySignal[Signal]
            != set(ExpectedLocalAccessBySignal[Signal])
            for Signal in Signals
        )
    ):
        Diagnostics["ApertureProjectionFailureReason"] = (
            "prepared-support-domain-incomplete"
        )
        return frozenset(), Diagnostics

    SeamsByApertureContract: dict[tuple[str, str], set[str]] = {}
    for OptionKey, LocalAccessFingerprints in SupportsByOption.items():
        ApertureKey = (
            OptionKey[0],
            ApertureContractByOption[OptionKey],
        )
        SeamsByApertureContract.setdefault(ApertureKey, set()).update(
            SeamByLocalAccess[(OptionKey[0], LocalAccessFingerprint)]
            for LocalAccessFingerprint in LocalAccessFingerprints
        )

    UnsupportedUnarySeams = frozenset(
        (str(Signal), str(Seam))
        for Signal, Seam in Certificate.UnsupportedUnarySeams
    )
    UnsupportedSeamPairs = frozenset(
        frozenset((
            (str(First[0]), str(First[1])),
            (str(Second[0]), str(Second[1])),
        ))
        for First, Second in Certificate.UnsupportedSeamPairs
    )
    Clauses: set[frozenset[tuple[str, str]]] = set()
    for ApertureKey, Seams in SeamsByApertureContract.items():
        if Seams and all(
            (ApertureKey[0], Seam) in UnsupportedUnarySeams
            for Seam in Seams
        ):
            Clauses.add(frozenset((ApertureKey,)))

    FirstSignal, SecondSignal = Signals
    FirstApertures = tuple(
        (Key, Seams)
        for Key, Seams in SeamsByApertureContract.items()
        if Key[0] == FirstSignal
    )
    SecondApertures = tuple(
        (Key, Seams)
        for Key, Seams in SeamsByApertureContract.items()
        if Key[0] == SecondSignal
    )
    for FirstKey, FirstSeams in FirstApertures:
        for SecondKey, SecondSeams in SecondApertures:
            if FirstSeams and SecondSeams and all(
                (FirstSignal, FirstSeam) in UnsupportedUnarySeams
                or (SecondSignal, SecondSeam) in UnsupportedUnarySeams
                or frozenset((
                    (FirstSignal, FirstSeam),
                    (SecondSignal, SecondSeam),
                )) in UnsupportedSeamPairs
                for FirstSeam in FirstSeams
                for SecondSeam in SecondSeams
            ):
                Clauses.add(frozenset((FirstKey, SecondKey)))

    Diagnostics.update({
        "ApertureProjectionComplete": True,
        "ApertureProjectionFailureReason": "",
        "ApertureProjectionOptionCount": len(
            ApertureContractByOption
        ),
        "ApertureProjectionClauseCount": len(Clauses),
    })
    return frozenset(Clauses), Diagnostics


def ProjectCompletePhysicalHigherOrderCertificateToApertureClauses(
    FactorDomain: PreparedPhysicalComponentPortFactorDomain,
    Certificate: PhysicalComponentSymbolicHigherOrderCertificate,
    *,
    RestrictedApertureContractsBySignal: (
        Mapping[str, str | frozenset[str]] | None
    ) = None,
) -> tuple[
    frozenset[frozenset[tuple[str, str]]],
    dict[str, object],
]:
    """Project one complete 3+ signal seam relation onto exact apertures.

    The projection is universal: an aperture tuple is rejected only when
    every local seam tuple supported behind it is disproven by the complete
    certificate.  Production callers may restrict the absolute contracts to
    the current physical plan so proof compilation stays core-driven instead
    of eagerly enumerating the whole global aperture product.
    """
    Signals = tuple(map(str, Certificate.SignalDomain))
    Diagnostics: dict[str, object] = {
        "HigherOrderCompatibilityComplete": bool(Certificate.Complete),
        "HigherOrderApertureProjectionComplete": False,
        "HigherOrderApertureProjectionSignals": list(Signals),
        "HigherOrderApertureProjectionFailureReason": "",
    }

    def Incomplete(Reason: str) -> tuple[
        frozenset[frozenset[tuple[str, str]]],
        dict[str, object],
    ]:
        Diagnostics["HigherOrderApertureProjectionFailureReason"] = Reason
        return frozenset(), Diagnostics

    if (
        not Certificate.Complete
        or len(Signals) < 3
        or len(set(Signals)) != len(Signals)
    ):
        return Incomplete("higher-order-certificate-incomplete-or-invalid")
    if (
        not FactorDomain.Complete
        or not FactorDomain.Feasible
        or str(Certificate.PreparedDomainFingerprint)
        != str(FactorDomain.DomainFingerprint)
    ):
        return Incomplete("prepared-domain-identity-mismatch")

    ExpectedAccessesBySignal: dict[str, frozenset[str]] = {}
    for Signal, Accesses in Certificate.LocalAccessFingerprintsBySignal:
        Signal = str(Signal)
        Values = frozenset(map(str, Accesses))
        if Signal in ExpectedAccessesBySignal:
            return Incomplete(
                "certificate-local-access-domain-incomplete-or-ambiguous"
            )
        ExpectedAccessesBySignal[Signal] = Values
    if (
        set(ExpectedAccessesBySignal) != set(Signals)
        or any(not ExpectedAccessesBySignal[Signal] for Signal in Signals)
    ):
        return Incomplete("certificate-local-access-domain-incomplete")

    SeamByAccess: dict[tuple[str, str], str] = {}
    for Signal, Access, Seam in Certificate.SeamFingerprintByLocalAccess:
        Key = (str(Signal), str(Access))
        Seam = str(Seam)
        Existing = SeamByAccess.get(Key)
        if (
            Key[0] not in Signals
            or not Seam
            or (Existing is not None and Existing != Seam)
        ):
            return Incomplete(
                "certificate-access-seam-map-incomplete-or-ambiguous"
            )
        SeamByAccess[Key] = Seam
    if any(
        frozenset(
            Access
            for CandidateSignal, Access in SeamByAccess
            if CandidateSignal == Signal
        ) != ExpectedAccessesBySignal[Signal]
        for Signal in Signals
    ):
        return Incomplete("certificate-access-seam-map-does-not-cover-domain")

    PreparedSeamByAccess: dict[tuple[str, str], str] = {}
    for Signal, Factors in FactorDomain.LocalAccessFactorsBySignal:
        Signal = str(Signal)
        if Signal not in Signals:
            continue
        for Factor in Factors:
            Access = str(Factor.LocalAccessFingerprint)
            Key = (Signal, Access)
            Seam = (
                str(getattr(Factor, "SeamContractFingerprint", ""))
                or BuildPhysicalPortSeamContractFingerprint(Factor)
            )
            Existing = PreparedSeamByAccess.get(Key)
            if Existing is not None and Existing != Seam:
                return Incomplete("prepared-access-seam-map-ambiguous")
            PreparedSeamByAccess[Key] = Seam
    if any(
        PreparedSeamByAccess.get((Signal, Access))
        != SeamByAccess.get((Signal, Access))
        for Signal in Signals
        for Access in ExpectedAccessesBySignal[Signal]
    ):
        return Incomplete("prepared-access-seam-identity-mismatch")

    ContractByOption: dict[tuple[str, str], str] = {}
    for Signal, Apertures in FactorDomain.ApertureFactorsBySignal:
        Signal = str(Signal)
        if Signal not in Signals:
            continue
        for Aperture in Apertures:
            Key = (Signal, str(Aperture.ApertureOptionFingerprint))
            Contract = str(Aperture.ApertureContractFingerprint)
            Existing = ContractByOption.get(Key)
            if (
                not Key[1]
                or not Contract
                or (Existing is not None and Existing != Contract)
            ):
                return Incomplete(
                    "aperture-option-contract-incomplete-or-ambiguous"
                )
            ContractByOption[Key] = Contract

    SupportsByOption = {
        (str(Key[0]), str(Key[1])): tuple(Supports)
        for Key, Supports in FactorDomain.LocalApertureSupportsByOption
        if str(Key[0]) in Signals
    }
    if any(Key not in ContractByOption for Key in SupportsByOption):
        return Incomplete("prepared-support-option-unresolved")
    SeamsByContract: dict[tuple[str, str], set[str]] = {}
    MappedAccessesBySignal: dict[str, set[str]] = {
        Signal: set() for Signal in Signals
    }
    for OptionKey, Contract in ContractByOption.items():
        Supports = SupportsByOption.get(OptionKey)
        if not Supports:
            return Incomplete("aperture-option-has-no-local-support")
        for Support in Supports:
            Access = str(Support.LocalAccessFingerprint)
            if (
                str(Support.ApertureOptionFingerprint) != OptionKey[1]
                or Access not in ExpectedAccessesBySignal[OptionKey[0]]
            ):
                return Incomplete("prepared-support-access-unresolved")
            Seam = SeamByAccess.get((OptionKey[0], Access))
            if Seam is None:
                return Incomplete("prepared-support-seam-unresolved")
            MappedAccessesBySignal[OptionKey[0]].add(Access)
            SeamsByContract.setdefault(
                (OptionKey[0], Contract), set()
            ).add(Seam)
    if any(
        MappedAccessesBySignal[Signal]
        != set(ExpectedAccessesBySignal[Signal])
        for Signal in Signals
    ):
        return Incomplete("prepared-support-domain-incomplete")

    Restriction = {}
    for Signal, Contracts in (
        RestrictedApertureContractsBySignal or {}
    ).items():
        Restriction[str(Signal)] = (
            frozenset((str(Contracts),))
            if isinstance(Contracts, str)
            else frozenset(map(str, Contracts))
        )
    if Restriction and not set(Signals) <= set(Restriction):
        return Incomplete("restricted-aperture-contract-domain-incomplete")
    ContractDomains = []
    for Signal in Signals:
        Contracts = tuple(sorted(
            Contract
            for CandidateSignal, Contract in SeamsByContract
            if CandidateSignal == Signal
            and (
                Signal not in Restriction
                or Contract in Restriction[Signal]
            )
        ))
        if not Contracts:
            return Incomplete("aperture-contract-domain-empty")
        if Signal in Restriction and frozenset(Contracts) != Restriction[Signal]:
            return Incomplete("restricted-aperture-contract-unresolved")
        ContractDomains.append(Contracts)

    CertifiedSeamsBySignal = {
        str(Signal): frozenset(map(str, Seams))
        for Signal, Seams in Certificate.SeamFingerprintsBySignal
    }
    if (
        set(CertifiedSeamsBySignal) != set(Signals)
        or any(
            not CertifiedSeamsBySignal[Signal]
            or CertifiedSeamsBySignal[Signal] != frozenset(
                Seam
                for (CandidateSignal, _Access), Seam
                in SeamByAccess.items()
                if CandidateSignal == Signal
            )
            for Signal in Signals
        )
    ):
        return Incomplete("certificate-seam-domain-incomplete-or-ambiguous")
    MutableSupportedSeamTuples = set()
    for TupleValue in Certificate.SupportedSeamTuples:
        BySignal = {
            str(Signal): str(Seam) for Signal, Seam in TupleValue
        }
        if (
            len(BySignal) != len(TupleValue)
            or set(BySignal) != set(Signals)
            or any(
                BySignal[Signal] not in CertifiedSeamsBySignal[Signal]
                for Signal in Signals
            )
        ):
            return Incomplete("certificate-supported-seam-tuple-invalid")
        MutableSupportedSeamTuples.add(tuple(
            (Signal, BySignal[Signal]) for Signal in Signals
        ))
    SupportedSeamTuples = frozenset(MutableSupportedSeamTuples)
    Clauses: set[frozenset[tuple[str, str]]] = set()
    SeamTupleCheckCount = 0
    for ContractValues in product(*ContractDomains):
        ContractTuple = tuple(zip(Signals, ContractValues))
        SeamDomains = tuple(
            tuple(sorted(SeamsByContract[(Signal, Contract)]))
            for Signal, Contract in ContractTuple
        )
        if any(not Seams for Seams in SeamDomains):
            return Incomplete("aperture-contract-seam-domain-empty")
        UniversallyUnsupported = True
        SeamDomainsBySignal = {
            Signal: frozenset(Seams)
            for Signal, Seams in zip(Signals, SeamDomains)
        }
        for SupportedTuple in SupportedSeamTuples:
            SeamTupleCheckCount += 1
            if all(
                Seam in SeamDomainsBySignal[Signal]
                for Signal, Seam in SupportedTuple
            ):
                UniversallyUnsupported = False
                break
        if UniversallyUnsupported:
            Clauses.add(frozenset(ContractTuple))

    Diagnostics.update({
        "HigherOrderApertureProjectionComplete": True,
        "HigherOrderApertureProjectionFailureReason": "",
        "HigherOrderApertureProjectionRestricted": bool(Restriction),
        "HigherOrderApertureProjectionContractTupleCount": int(
            prod(map(len, ContractDomains))
        ),
        "HigherOrderApertureProjectionSeamTupleCheckCount": (
            SeamTupleCheckCount
        ),
        "HigherOrderApertureProjectionClauseCount": len(Clauses),
    })
    return frozenset(Clauses), Diagnostics

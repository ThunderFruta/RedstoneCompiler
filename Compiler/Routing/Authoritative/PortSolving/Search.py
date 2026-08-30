"""Exact bounded physical component port-domain search."""

from __future__ import annotations

from ...Components.PhysicalPlanning import BuildPhysicalComponentAssemblyChoiceFingerprint

from ...Components.PhysicalPlanning import BuildPhysicalComponentAssemblyPlanDomainFingerprint

from ...Components.PhysicalPlanning import BuildPhysicalComponentPortSolverCacheKey

from ...Components.Validation import BuildPhysicalLocalAccessDomainFingerprint

from ...Components.Validation import BuildPhysicalPortApertureContractFingerprint

from ...Components.Validation import BuildPhysicalPortSeamContractFingerprint

from ...Components.InterfacePlanning import ComponentInterfaceContract

from ...Components.InterfacePlanning import IterClosedComponentContracts

from ...Contracts.Component import PhysicalComponentAssemblyPlan

from ...Contracts.Component import PhysicalComponentBoundaryPortReservation

from ...Contracts.Component import PhysicalComponentChannelReservation

from ...Contracts.Component import PhysicalComponentPortReservation

from ...Contracts.Component import PreparedPhysicalComponentFeedthroughEndpointDomain

from ...Contracts.PhysicalInterface import PhysicalPortLaneFactor

from ...Contracts.PhysicalInterface import PhysicalPortSeamFactor

from ...Contracts.PhysicalInterface import PreparedPhysicalComponentAssembly

from ...Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain

from ...Contracts.Results import RoutingResources

from ...Failures import RoutingFailure

from ...Failures import RoutingFailureReason

from ...Failures import RoutingStageError

from ...Interfaces.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint

from ...Interfaces.PhysicalClaims import ComponentClaimsConflict

from ...Reliability import BuildStableFingerprint

from ...Reliability import RoutingDeadline

from ...ResourceGraph import BuildRoutingEnvelope

from ...ResourceGraph import FindSelfClaimConflicts

from ...ResourceGraph import RoutingResourceClaims

from ...Technology import DefaultRedstoneRoutingTechnology

from collections import Counter

from collections import defaultdict

from dataclasses import replace

from itertools import combinations

from math import prod

from types import SimpleNamespace

from typing import Any

from typing import Callable

from typing import Iterable

from typing import Mapping

from typing import Sequence

from ..AssignmentState import (
    BuildPhysicalLocalPortPairUnsupportedIndex,
    BuildPhysicalPortNoGoodKeys,
    FindProofQualifiedCompleteDomainNoGoodCore,
    FindProofQualifiedUniversalNoGoodCore,
    GetPersistentPhysicalComponentPortCspState,
    OrderPhysicalPortOptionsByPreferences,
    PropagateExactNoGoodClauses,
    SelectBinaryExactNoGoodClauses,
    SelectExactNoGoodCspBranch,
)

from ..CandidateGuides import (
    PropagateLaneFactorArcConsistency,
)

from ..ExteriorConnectors import (
    BuildPhysicalBoundaryPortAssignmentFingerprint,
    IterPhysicalBoundaryPortAssignments,
    SelectPhysicalFactorBranchSignal,
)

from ..PhysicalGuides import (
    BuildComponentKeepoutAvoidingGlobalGuides,
    BuildExplicitPhysicalComponentFeedthrough,
    FindSignalClaimConflicts,
    MaterializeSupportedPhysicalPortReservation,
    PreparePhysicalComponentFeedthroughEndpointDomain,
)

from ..TrackPortfolio import (
    BuildSeamOnlyPhysicalComponentPortReservation,
    InterleavePhysicalPortSeamsByEgressClass,
)

from .Finalization import FinalizePreparedPhysicalComponentAssembly
from .Validation import (
    ValidatePreparedPhysicalComponentPortFactorDomain,
)


def IterPreparedPhysicalBoundaryAssignments(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    Resources: RoutingResources,
    BoundaryPortDomainsBySignal: dict[
        str, tuple[PhysicalComponentBoundaryPortReservation, ...]
    ],
    CertifiedLocalPairNoGoodClauses: tuple[frozenset[tuple[str, str]], ...],
    LocalSeamNoGoodClauses: tuple[frozenset[tuple[str, str]], ...],
    PortSolverCacheKey: str,
    ResourceGraph: Any,
    CheckComponentPlannerWork: Callable[[dict[str, object]], None],
    Deadline: RoutingDeadline | None,
    DeferLocalCompositeSelection: bool = False,
) -> Iterable[object]:
    """Prefer coarse contracts, then complete the global-only boundary."""
    IncludeLocalCompositeFactors = not DeferLocalCompositeSelection

    class LiveBoundaryNoGoodClauses:
        """Expose every proof-qualified boundary/local cut as one live view."""

        def __iter__(Self):
            yield from Resources.RejectedPhysicalComponentPortReservationSets
            yield from CertifiedLocalPairNoGoodClauses
            yield from LocalSeamNoGoodClauses

    LiveNoGoods = LiveBoundaryNoGoodClauses()
    yield from IterClosedComponentContracts(Preparation, TrackPitch=max(1, int(getattr(ResourceGraph.Technology, 'TrackPitch', DefaultRedstoneRoutingTechnology.TrackPitch))), RejectedClauses=LiveNoGoods, RejectedApertureContractFingerprintsBySignal=Resources.RejectedPhysicalComponentPortReservationsBySignal, RejectedAssignmentFingerprints=Resources.RejectedPhysicalComponentBoundaryAssignmentFingerprints, WorkCheck=CheckComponentPlannerWork, IncludeLocalCompositeFactors=IncludeLocalCompositeFactors, PreferredGlobalContractsBySignal=Resources.PreferredPhysicalComponentGlobalContractsBySignal, PreferredApertureContractsBySignal=Resources.PreferredPhysicalComponentApertureContractsBySignal, PreferredPortReservationsBySignal=Resources.PreferredPhysicalComponentPortReservationsBySignal, AperturePortalSlackBySignal=Resources.PhysicalComponentAperturePortalSlackBySignal, MaximumRuntimeSeconds=Deadline.RemainingSeconds if Deadline is not None else None)
    # A capacity-guide local lease can under-approximate a legal shared
    # fabric.  The complete global-only iterator freezes the boundary while
    # the exact port CSP remains responsible for the local assignment proof.
    yield from IterPhysicalBoundaryPortAssignments(
        BoundaryPortDomainsBySignal,
        LocalAccessFactorsBySignal=dict(
            Preparation.LocalAccessFactorsBySignal
        ),
        ApertureFactorsBySignal=dict(
            Preparation.ApertureFactorsBySignal
        ),
        LocalApertureSupportBySignal=dict(
            Preparation.LocalApertureSupportBySignal
        ),
        CertifiedLocalNoGoodClauses=(),
        LearnedLocalSeamNoGoodClauses=LocalSeamNoGoodClauses,
        PortSolverCacheKey=PortSolverCacheKey,
        PreferredGlobalContractsBySignal=Resources.PreferredPhysicalComponentGlobalContractsBySignal,
        RejectedGlobalApertureClauses=LiveNoGoods,
        RejectedGlobalApertureFingerprintsBySignal=Resources.RejectedPhysicalComponentPortReservationsBySignal,
        CertifiedNoGoodProjectionOnly=DeferLocalCompositeSelection,
        PersistentPairSupportCache=getattr(
            Resources,
            "PhysicalBoundaryPairSupportCache",
            None,
        ),
        WorkCheck=CheckComponentPlannerWork,
    )


def SelectAdjacentScarcityBoundaryPairCoreCandidates(
    SignalOrder: Sequence[str],
    DomainSizesBySignal: Mapping[str, int] | None = None,
    MaximumCandidates: int = 8,
) -> tuple[tuple[str, str], ...]:
    """Probe adjacent complete domains before the bounded triple fallback."""
    if MaximumCandidates <= 0:
        return ()
    Ordered = tuple(map(str, SignalOrder))
    Pairs = tuple(
        (Ordered[Index], Ordered[Index + 1])
        for Index in range(max(0, len(Ordered) - 1))
    )
    DomainSizes = DomainSizesBySignal or {}
    return tuple(sorted(
        Pairs,
        key=lambda Pair: (
            abs(
                int(DomainSizes.get(Pair[0], 0))
                - int(DomainSizes.get(Pair[1], 0))
            ),
            int(DomainSizes.get(Pair[0], 0))
            + int(DomainSizes.get(Pair[1], 0)),
            Ordered.index(Pair[0]),
        ),
    )[:MaximumCandidates])


def SelectRevalidatablePriorPortAssignmentCore(
    PreferredSignals: Iterable[str],
    OrderedSignals: Iterable[str],
    MaximumSignals: int = 3,
) -> tuple[str, ...]:
    """Select a small prior core only as a fresh factor-search hint."""
    OrderedSignalSet = frozenset(map(str, OrderedSignals))
    Preferred = tuple(sorted(set(map(str, PreferredSignals))))
    if (
        not Preferred
        or len(Preferred) > max(0, int(MaximumSignals))
        or not set(Preferred) <= OrderedSignalSet
    ):
        return ()
    return Preferred


def BuildCapacityRepairSeamRestrictionPasses(
    BaseRestrictions: Mapping[str, str],
    PreferredRestrictions: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    """Try a proved repair witness first without making it a completeness cut."""
    Base = {str(Signal): str(Seam) for Signal, Seam in BaseRestrictions.items()}
    Preferred = {
        str(Signal): str(Seam)
        for Signal, Seam in PreferredRestrictions.items()
    }
    if not Preferred:
        return (Base,)
    if any(
        Signal in Base and Base[Signal] != Seam
        for Signal, Seam in Preferred.items()
    ):
        return (Base,)
    Guided = {**Base, **Preferred}
    if Guided == Base:
        return (Base,)
    return (Guided, Base)


def SelectCapacityRepairBoundaryPreferences(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    SelectedSeamsBySignal: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Project a selected local seam witness to canonical boundary hints."""
    LocalFactorsBySignal = dict(Preparation.LocalAccessFactorsBySignal)
    ApertureFactorsBySignal = dict(Preparation.ApertureFactorsBySignal)
    SupportsBySignal = dict(Preparation.LocalApertureSupportBySignal)
    PreferredGlobalContracts: dict[str, str] = {}
    PreferredApertureContracts: dict[str, str] = {}
    for Signal, SelectedSeam in sorted(SelectedSeamsBySignal.items()):
        LocalAccessFingerprints = frozenset(
            str(Factor.LocalAccessFingerprint)
            for Factor in LocalFactorsBySignal.get(Signal, ())
            if (
                str(Factor.SeamContractFingerprint)
                == str(SelectedSeam)
            )
        )
        AperturesByFingerprint = {
            str(Factor.ApertureOptionFingerprint): Factor
            for Factor in ApertureFactorsBySignal.get(Signal, ())
        }
        Candidates = tuple(sorted(
            (
                AperturesByFingerprint[str(Support.ApertureOptionFingerprint)]
                for Support in SupportsBySignal.get(Signal, ())
                if (
                    str(Support.LocalAccessFingerprint)
                    in LocalAccessFingerprints
                    and str(Support.ApertureOptionFingerprint)
                    in AperturesByFingerprint
                )
            ),
            key=lambda Value: (
                str(Value.GlobalContractFingerprint),
                str(Value.ApertureContractFingerprint),
                str(Value.ApertureOptionFingerprint),
            ),
        ))
        if not Candidates:
            continue
        PreferredGlobalContracts[str(Signal)] = str(
            Candidates[0].GlobalContractFingerprint
        )
        PreferredApertureContracts[str(Signal)] = str(
            Candidates[0].ApertureContractFingerprint
        )
    return PreferredGlobalContracts, PreferredApertureContracts


def ApplyCapacityRepairBoundaryPreferences(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    Resources: RoutingResources,
    OrderedSignals: Sequence[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Publish transient boundary hints derived from the active seam witness."""
    OrderedSignalSet = frozenset(map(str, OrderedSignals))
    SelectedSeamsBySignal = {
        str(Signal): str(Seam)
        for Signal, Seam in dict(getattr(
            Resources,
            'PreferredPhysicalComponentSeamContractsBySignal',
            {},
        )).items()
        if str(Signal) in OrderedSignalSet
    }
    PreferredGlobal, PreferredAperture = (
        SelectCapacityRepairBoundaryPreferences(
            Preparation,
            SelectedSeamsBySignal,
        )
    )
    Resources.PreferredPhysicalComponentGlobalContractsBySignal.update(
        PreferredGlobal
    )
    Resources.PreferredPhysicalComponentApertureContractsBySignal.update(
        PreferredAperture
    )
    return SelectedSeamsBySignal, PreferredAperture


def FindCompleteBoundaryAssignmentUnsatCore(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    Resources: RoutingResources,
    BoundaryPortDomainsBySignal: dict[
        str,
        tuple[PhysicalComponentBoundaryPortReservation, ...],
    ],
    OrderedSignals: tuple[str, ...],
    CertifiedLocalPairNoGoodClauses: tuple[
        frozenset[tuple[str, str]],
        ...,
    ],
    LocalSeamNoGoodClauses: Iterable[frozenset[tuple[str, str]]],
    PortSolverCacheKey: str,
    CheckComponentPlannerWork: Callable[[dict[str, object]], None],
    DeferLocalCompositeSelection: bool,
) -> tuple[tuple[str, ...], int, tuple[str, ...], int]:
    """Return the first inclusion-minimal complete pair or triple cut."""
    PairCore: tuple[str, ...] = ()
    PairCheckCount = 0
    TripleCore: tuple[str, ...] = ()
    TripleCheckCount = 0

    def FindSubsetAssignment(Signals: tuple[str, ...]):
        SubsetDomains = {
            Signal: BoundaryPortDomainsBySignal[Signal]
            for Signal in Signals
        }
        return next(IterPhysicalBoundaryPortAssignments(
            SubsetDomains,
            LocalAccessFactorsBySignal=dict(
                Preparation.LocalAccessFactorsBySignal
            ),
            ApertureFactorsBySignal=dict(
                Preparation.ApertureFactorsBySignal
            ),
            LocalApertureSupportBySignal=dict(
                Preparation.LocalApertureSupportBySignal
            ),
            CertifiedLocalNoGoodClauses=(
                CertifiedLocalPairNoGoodClauses
            ),
            LearnedLocalSeamNoGoodClauses=LocalSeamNoGoodClauses,
            PortSolverCacheKey=PortSolverCacheKey,
            PreferredGlobalContractsBySignal=(
                Resources.PreferredPhysicalComponentGlobalContractsBySignal
            ),
            RejectedGlobalApertureClauses=(
                Resources.RejectedPhysicalComponentPortReservationSets
            ),
            RejectedGlobalApertureFingerprintsBySignal=(
                Resources.RejectedPhysicalComponentPortReservationsBySignal
            ),
            CertifiedNoGoodProjectionOnly=(
                DeferLocalCompositeSelection
            ),
            PersistentPairSupportCache=getattr(
                Resources,
                "PhysicalBoundaryPairSupportCache",
                None,
            ),
            WorkCheck=CheckComponentPlannerWork,
        ), None)

    SignalOrder = tuple(sorted(
        OrderedSignals,
        key=lambda Signal: (
            len(BoundaryPortDomainsBySignal[Signal]),
            Signal,
        ),
    ))
    CheckedFeasiblePairs: set[tuple[str, str]] = set()
    for SignalPair in SelectAdjacentScarcityBoundaryPairCoreCandidates(
        SignalOrder,
        {
            Signal: len(BoundaryPortDomainsBySignal[Signal])
            for Signal in SignalOrder
        },
    ):
        PairCheckCount += 1
        CheckComponentPlannerWork({
            'Stage': 'physical-port-boundary-scarcity-pair-core-extraction',
            'BoundaryTripleUnsatCoreCheckCount': TripleCheckCount,
            'BoundaryPairUnsatCoreCheckCount': PairCheckCount,
        })
        if FindSubsetAssignment(SignalPair) is None:
            return SignalPair, PairCheckCount, (), TripleCheckCount
        CheckedFeasiblePairs.add(SignalPair)
    for SignalTriple in combinations(SignalOrder, 3):
        TripleCheckCount += 1
        CheckComponentPlannerWork({
            'Stage': 'physical-port-boundary-core-extraction',
            'BoundaryTripleUnsatCoreCheckCount': TripleCheckCount,
            'BoundaryPairUnsatCoreCheckCount': PairCheckCount,
        })
        if FindSubsetAssignment(SignalTriple) is not None:
            continue
        for SignalPair in combinations(SignalTriple, 2):
            if SignalPair in CheckedFeasiblePairs:
                continue
            PairCheckCount += 1
            CheckComponentPlannerWork({
                'Stage': 'physical-port-boundary-core-minimality',
                'BoundaryTripleUnsatCoreCheckCount': TripleCheckCount,
                'BoundaryPairUnsatCoreCheckCount': PairCheckCount,
            })
            if FindSubsetAssignment(SignalPair) is None:
                PairCore = SignalPair
                break
        if not PairCore:
            TripleCore = SignalTriple
        break
    return PairCore, PairCheckCount, TripleCore, TripleCheckCount


def BuildFeedthroughEndpointPrescreenNoGood(
    Failure: RoutingFailure,
    BoundaryPorts: tuple[PhysicalComponentBoundaryPortReservation, ...],
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    ResourceGraph: Any,
) -> tuple[frozenset[tuple[str, str]], int]:
    """Prove a minimal aperture core blocking every feedthrough path."""
    Diagnostics = dict(Failure.Diagnostics or {})
    Signal = str(Diagnostics.get('Signal', ''))
    CandidateCount = int(Diagnostics.get(
        'FeedthroughEndpointCandidateCount',
        0,
    ))
    RejectedCount = int(Diagnostics.get(
        'FeedthroughEndpointPrescreenRejectedCandidateCount',
        0,
    ))
    if (
        not Signal
        or not Diagnostics.get(
            'FeedthroughEndpointPrescreenComplete',
            False,
        )
        or CandidateCount <= 0
        or RejectedCount != CandidateCount
        or not BoundaryPorts
    ):
        return (frozenset(), 0)
    EndpointDomain = next((
        Domain
        for Domain in Preparation.FeedthroughEndpointDomains
        if str(Domain.Signal) == Signal
    ), None)
    if (
        EndpointDomain is None
        or not EndpointDomain.Complete
        or len(EndpointDomain.Candidates) != CandidateCount
    ):
        return (frozenset(), 0)
    Clearance = max(1, int(getattr(
        ResourceGraph.Technology,
        'TrackPitch',
        DefaultRedstoneRoutingTechnology.TrackPitch,
    )))
    HaloBySignal = {
        str(Port.Signal): frozenset((
            (X + DeltaX, Z + DeltaZ)
            for X, _Y, Z in Port.GlobalPath
            for DeltaX in range(-Clearance, Clearance + 1)
            for DeltaZ in range(-Clearance, Clearance + 1)
            if abs(DeltaX) + abs(DeltaZ) <= Clearance
        ))
        for Port in BoundaryPorts
    }
    BlockerSets = tuple(
        frozenset(
            PortSignal
            for PortSignal, Halo in HaloBySignal.items()
            if any(
                (Node[0], Node[2]) in Halo
                for Node in Candidate.ReservedPathNodes
            )
        )
        for Candidate in EndpointDomain.Candidates
    )
    if any(not Blockers for Blockers in BlockerSets):
        return (frozenset(), 0)
    Signals = tuple(sorted(HaloBySignal))
    CheckCount = 0
    Core: tuple[str, ...] = ()
    for CoreSize in range(1, len(Signals) + 1):
        for CandidateCore in combinations(Signals, CoreSize):
            CheckCount += 1
            if all(set(CandidateCore) & Blockers for Blockers in BlockerSets):
                Core = CandidateCore
                break
        if Core:
            break
    if not Core:
        return (frozenset(), CheckCount)
    PortsBySignal = {
        str(Port.Signal): Port
        for Port in BoundaryPorts
    }
    return (
        frozenset((
            (
                SignalValue,
                str(PortsBySignal[
                    SignalValue
                ].ApertureContractFingerprint),
            )
            for SignalValue in Core
        )),
        CheckCount,
    )


def MinimizeExplicitFeedthroughNoGood(
    Failure: RoutingFailure,
    BoundaryPorts: tuple[PhysicalComponentBoundaryPortReservation, ...],
    Context: Any,
) -> tuple[frozenset[tuple[str, str]], int]:
    """Delete apertures while preserving one complete feedthrough cut."""
    Preparation = Context.Preparation
    OrderedSignals = Context.OrderedSignals
    Problem = Context.Problem
    ResourceGraph = Context.ResourceGraph
    WorkCheck = Context.WorkCheck
    GetPreparedFeedthroughEndpointDomain = Context.GetPreparedFeedthroughEndpointDomain
    ExplicitFeedthroughMinimizationDiagnostics = Context.ExplicitFeedthroughMinimizationDiagnostics
    ExplicitFeedthroughFeasibilityCache = Context.ExplicitFeedthroughFeasibilityCache
    CompiledExplicitFeedthroughBinaryRows = Context.CompiledExplicitFeedthroughBinaryRows
    LocalSeamNoGoodClauses = Context.LocalSeamNoGoodClauses
    ExpectedDetail = str(Failure.Detail)
    ExpectedSignal = str((Failure.Diagnostics or {}).get('Signal', ''))
    if ExpectedDetail not in {'the complete explicit feedthrough endpoint domain cannot reach both exterior guide components without crossing a port claim', 'the complete component feedthrough tree domain cannot connect every exterior guide component'} or not ExpectedSignal or (not BoundaryPorts):
        return (frozenset(), 0)
    Layer = int(Preparation.CoarsePlan.Layers.get(ExpectedSignal, (Failure.Diagnostics or {}).get('Layer', 0)))
    EndpointDomain = GetPreparedFeedthroughEndpointDomain(ExpectedSignal, Layer)
    if not EndpointDomain.Complete:
        return (frozenset(), 0)
    Guide = frozenset(Preparation.CoarsePlan.Guides.get(ExpectedSignal, ()))
    if not Guide:
        return (frozenset(), 0)
    TrackPitch = max(1, int(getattr(ResourceGraph.Technology, 'TrackPitch', DefaultRedstoneRoutingTechnology.TrackPitch)))
    KeepoutCore = dict(Preparation.ComponentKeepoutGuideCellsByLayer).get(Layer, frozenset())
    KeepoutHalo = frozenset(((X + DeltaX, Z + DeltaZ) for X, Z in KeepoutCore for DeltaX, DeltaZ in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))))
    CheckCount = 0

    def HasSameCompleteFailure(Ports: tuple[PhysicalComponentBoundaryPortReservation, ...]) -> bool:
        nonlocal CheckCount
        CacheKey = (ExpectedSignal, ExpectedDetail, tuple(sorted(((str(Port.Signal), str(Port.ApertureContractFingerprint)) for Port in Ports))))
        CachedFailure = ExplicitFeedthroughFeasibilityCache.get(CacheKey)
        if CachedFailure is not None:
            return CachedFailure
        CheckCount += 1
        ReservedCells = frozenset(((Position[0], Position[2]) for Port in Ports for Position in Port.GlobalPath))
        SingleSignalPlan = replace(Preparation.CoarsePlan, Guides={ExpectedSignal: Guide}, Layers={ExpectedSignal: Layer})
        try:
            BuildComponentKeepoutAvoidingGlobalGuides(SingleSignalPlan, ComponentPortSignals=frozenset(OrderedSignals), EnvelopeMinimum=Preparation.ComponentEnvelopeMinimum, EnvelopeMaximum=Preparation.ComponentEnvelopeMaximum, TrackPitch=TrackPitch, ReservedPortGuideCells=ReservedCells, ComponentKeepoutGuideCellsByLayer=dict(Preparation.ComponentKeepoutGuideCellsByLayer), DeclaredFeedthroughSignals=frozenset())
            ExplicitFeedthroughMinimizationDiagnostics.update({'GuideStatus': 'feasible', 'PortCount': len(Ports)})
            ExplicitFeedthroughFeasibilityCache[CacheKey] = False
            return False
        except RoutingStageError as GuideError:
            GuideFailure = GuideError.Failure
            ExplicitFeedthroughMinimizationDiagnostics.update({'GuideStatus': 'failed', 'GuideFailureDetail': str(GuideFailure.Detail), 'GuideFailureDiagnostics': dict(GuideFailure.Diagnostics or {}), 'PortCount': len(Ports)})
            if GuideFailure.Reason != RoutingFailureReason.ComponentChannelCapacityUnsatisfiable or str((GuideFailure.Diagnostics or {}).get('Signal', '')) != ExpectedSignal or int((GuideFailure.Diagnostics or {}).get('ExteriorGuideComponentCount', 0)) < 2:
                ExplicitFeedthroughFeasibilityCache[CacheKey] = False
                return False
        ReservedHalo = frozenset(((X + DeltaX, Z + DeltaZ) for X, Z in ReservedCells for DeltaX in range(-TrackPitch, TrackPitch + 1) for DeltaZ in range(-TrackPitch, TrackPitch + 1) if abs(DeltaX) + abs(DeltaZ) <= TrackPitch))
        try:
            BuildExplicitPhysicalComponentFeedthrough(ExpectedSignal, Layer, Guide, ComponentKeepoutGuideCells=KeepoutHalo, ReservedPortAccessGuideCells=ReservedHalo, FabricNodes=frozenset(Problem.Fabric.Nodes), FabricEdges=frozenset(Problem.Fabric.Edges), FabricIngressNodes=frozenset(Problem.Fabric.IngressNodes), ResourceGraph=ResourceGraph, MinimumPlacementY=Preparation.MinimumPlacementY, PreparedEndpointDomain=EndpointDomain)
        except RoutingStageError as CandidateError:
            ExplicitFeedthroughMinimizationDiagnostics.update({'FeedthroughStatus': 'failed', 'FeedthroughFailureDetail': str(CandidateError.Failure.Detail), 'FeedthroughFailureDiagnostics': dict(CandidateError.Failure.Diagnostics or {})})
            Result = bool(CandidateError.Failure.Reason == RoutingFailureReason.ComponentChannelCapacityUnsatisfiable and str(CandidateError.Failure.Detail) == ExpectedDetail)
            ExplicitFeedthroughFeasibilityCache[CacheKey] = Result
            return Result
        ExplicitFeedthroughFeasibilityCache[CacheKey] = False
        return False
    Core = list(sorted(BoundaryPorts, key=lambda Value: Value.Signal))
    if not HasSameCompleteFailure(tuple(Core)):
        return (frozenset(), CheckCount)
    for Port in tuple(Core):
        CandidateCore = tuple((Value for Value in Core if Value.Signal != Port.Signal))
        if CandidateCore and HasSameCompleteFailure(CandidateCore):
            Core = list(CandidateCore)
    if len(Core) == 2:
        BoundaryDomains = {str(Signal): tuple(Values) for Signal, Values in Preparation.BoundaryPortReservationsBySignal}
        UniqueDomains = {}
        for Signal in (str(Core[0].Signal), str(Core[1].Signal)):
            ByContract = {}
            for Value in BoundaryDomains.get(Signal, ()):
                ByContract.setdefault(str(Value.ApertureContractFingerprint), Value)
            UniqueDomains[Signal] = tuple((ByContract[Fingerprint] for Fingerprint in sorted(ByContract)))
        FixedPort, VariablePort = sorted(Core, key=lambda Value: (len(UniqueDomains.get(str(Value.Signal), ())), str(Value.Signal)))
        FixedSignal = str(FixedPort.Signal)
        VariableSignal = str(VariablePort.Signal)
        RowKey = (ExpectedSignal, FixedSignal, str(FixedPort.ApertureContractFingerprint), VariableSignal)
        VariableDomain = UniqueDomains.get(VariableSignal, ())
        if RowKey not in CompiledExplicitFeedthroughBinaryRows and VariableDomain:
            IncompatibleCount = 0
            for AlternativeIndex, Alternative in enumerate(VariableDomain, start=1):
                if WorkCheck is not None and (AlternativeIndex == 1 or AlternativeIndex % 16 == 0):
                    WorkCheck({'Stage': 'physical-feedthrough-binary-support-row', 'FixedSignal': FixedSignal, 'VariableSignal': VariableSignal, 'AlternativeIndex': AlternativeIndex, 'AlternativeCount': len(VariableDomain), 'IncompatibleCount': IncompatibleCount})
                if not HasSameCompleteFailure(tuple(sorted((FixedPort, Alternative), key=lambda Value: str(Value.Signal)))):
                    continue
                IncompatibleCount += 1
                LocalSeamNoGoodClauses.add(frozenset(((FixedSignal, str(FixedPort.ApertureContractFingerprint)), (VariableSignal, str(Alternative.ApertureContractFingerprint)))))
            if IncompatibleCount == len(VariableDomain):
                LocalSeamNoGoodClauses.add(frozenset(((FixedSignal, str(FixedPort.ApertureContractFingerprint)),)))
            CompiledExplicitFeedthroughBinaryRows.add(RowKey)
            ExplicitFeedthroughMinimizationDiagnostics.update({'CompiledBinaryRowFixedSignal': FixedSignal, 'CompiledBinaryRowVariableSignal': VariableSignal, 'CompiledBinaryRowAlternativeCount': len(VariableDomain), 'CompiledBinaryRowIncompatibleCount': IncompatibleCount, 'CompiledBinaryRowPromotedUnary': bool(IncompatibleCount == len(VariableDomain))})
    return (frozenset(((str(Port.Signal), str(Port.ApertureContractFingerprint)) for Port in Core)), CheckCount)


def BuildExplicitFeedthroughMinimizationContext(
    Values: Mapping[str, Any],
) -> SimpleNamespace:
    """Freeze the small set of state used by feedthrough cut minimization."""
    Names = (
        'Preparation', 'OrderedSignals', 'Problem', 'ResourceGraph',
        'WorkCheck', 'GetPreparedFeedthroughEndpointDomain',
        'ExplicitFeedthroughMinimizationDiagnostics',
        'ExplicitFeedthroughFeasibilityCache',
        'CompiledExplicitFeedthroughBinaryRows', 'LocalSeamNoGoodClauses',
    )
    return SimpleNamespace(**{Name: Values[Name] for Name in Names})


def _SolvePreparedPhysicalComponentPortFactorDomain(Preparation: PreparedPhysicalComponentPortFactorDomain, Resources: RoutingResources, *, WorkCheck: Callable[[dict[str, object]], None] | None=None, Deadline: RoutingDeadline | None=None, DeferLocalCompositeSelection: bool=False, RequiredBoundaryPorts: tuple[PhysicalComponentBoundaryPortReservation, ...] | None=None) -> PreparedPhysicalComponentAssembly:
    """Solve one complete frozen physical port factor domain."""
    Inputs = ValidatePreparedPhysicalComponentPortFactorDomain(Preparation, Resources)
    Problem, CoarsePlan, AccessCertificate, ResourceGraph, LocalSeamNoGoodClauses, CurrentResourceGraphFingerprint, CurrentGuideFingerprint, LaneFactorsBySignal, CurrentLocalAccessFactorsBySignal, CurrentApertureFactorsBySignal, CurrentLocalApertureSupportBySignal, CurrentDomainFingerprint = Inputs
    ChannelReservations = list(Preparation.ChannelReservations)
    ChannelClaimsBySignal = {Value.Signal: Value.Claims for Value in ChannelReservations}
    FabricOrigin = Preparation.FabricOrigin
    MinimumPlacementY = Preparation.MinimumPlacementY
    ComponentEnvelopeMinimum = Preparation.ComponentEnvelopeMinimum
    ComponentEnvelopeMaximum = Preparation.ComponentEnvelopeMaximum
    FabricAdjacency = defaultdict(set)
    for Node, Neighbors in Preparation.FabricAdjacency:
        FabricAdjacency[Node].update(Neighbors)
    ComponentKeepoutNodes = Preparation.ComponentKeepoutNodes
    ComponentGraphFingerprint = Preparation.ComponentGraphFingerprint
    LaneFactorExpansionCount = Preparation.LaneFactorExpansionCount; AccessFactorExpansionCount = Preparation.AccessFactorExpansionCount; SeamFactorExpansionCount = Preparation.SeamFactorExpansionCount; GlobalConnectorSearchCount = Preparation.GlobalConnectorSearchCount
    GlobalConnectorCacheHitCount = Preparation.GlobalConnectorCacheHitCount; GlobalConnectorPortableCacheHitCount = Preparation.GlobalConnectorPortableCacheHitCount; GlobalConnectorPortableCacheValidationRejectCount = Preparation.GlobalConnectorPortableCacheValidationRejectCount; GlobalConnectorPortableCacheStoreCount = Preparation.GlobalConnectorPortableCacheStoreCount
    GlobalConnectorExpansionCount = Preparation.GlobalConnectorExpansionCount; GlobalGuideFieldBuildCount = Preparation.GlobalGuideFieldBuildCount; GlobalGuideFieldExpansionCount = Preparation.GlobalGuideFieldExpansionCount; GlobalGuideFieldHitCount = Preparation.GlobalGuideFieldHitCount
    GlobalGuideFieldCanonicalPathCount = Preparation.GlobalGuideFieldCanonicalPathCount; GlobalGuideFieldFallbackCount = Preparation.GlobalGuideFieldFallbackCount
    OrderedSignals = tuple(sorted(LaneFactorsBySignal))
    (PreferredCapacityRepairSeamsBySignal, PreferredCapacityRepairApertureContractsBySignal) = ApplyCapacityRepairBoundaryPreferences(
        Preparation, Resources, OrderedSignals,
    )
    PortSolverCacheKey = BuildPhysicalComponentPortSolverCacheKey(Preparation.DomainFingerprint)
    CertifiedLocalPairNoGoodClauses = tuple(sorted(BuildPhysicalLocalPortPairUnsupportedIndex(getattr(Resources, 'PhysicalLocalPortPairSupportCertificateCache', {}).values(), Preparation, PortSolverCacheKey), key=lambda Clause: tuple(sorted(Clause))))
    PhysicalLocalNoGoodClauses = CertifiedLocalPairNoGoodClauses
    SelectedPorts: tuple[PhysicalComponentPortReservation, ...] | None = None
    PortAssignmentExpansionCount = 0
    PersistentPortCspState, PersistentPortCspStateReused = GetPersistentPhysicalComponentPortCspState(Resources, PortSolverCacheKey, Preparation.DomainFingerprint)
    FailedPortAssignmentStates = PersistentPortCspState.FailedAssignmentStates
    FailedFactorizedPortAssignmentStates: set[tuple[object, ...]] = set()
    PortClaimCompatibilityCache: dict[tuple[str, str], bool] = PersistentPortCspState.PortClaimCompatibilityCache
    PersistentPortClaimCompatibilityEntryCountAtStart = len(PortClaimCompatibilityCache)
    GeneratedPortOptionsBySeam: dict[tuple[str, str, str], list[PhysicalComponentPortReservation]] = {(Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint): [] for Signal in OrderedSignals for LaneFactor in LaneFactorsBySignal[Signal] for Seam in LaneFactor.Seams}
    RealizablePortClaimsBySeam: dict[tuple[str, str, str], set[RoutingResourceClaims]] = {Key: set() for Key in GeneratedPortOptionsBySeam}
    GeneratedPortFingerprintsBySignal: dict[str, set[str]] = {Signal: set() for Signal in OrderedSignals}
    ExhaustedPortSeamKeys: set[tuple[str, str, str]] = set()
    PortOptionGenerationCountBySignal: Counter[str] = Counter()
    PortOptionSelfClaimPruneCount = 0
    DominatedPortReservationPruneCount = 0
    AccessFactorExpansionCountBySignal: Counter[str] = Counter()
    AccessAssignmentSelfConflictCountBySignal: Counter[str] = Counter()
    AccessAssignmentSelfConflictSamplesBySignal: dict[str, list[dict[str, object]]] = {}
    CompleteAccessAssignmentCountBySignal: Counter[str] = Counter()
    LocalRealizabilityCheckCountBySignal: Counter[str] = Counter()
    LocalRealizabilityRejectedSeamCountBySignal: Counter[str] = Counter()
    LocalRealizabilityRejectionCountsBySignal: dict[str, Counter[str]] = defaultdict(Counter)

    def RelativePath(Path: Iterable[tuple[int, int, int]]) -> tuple[tuple[int, int, int], ...]:
        return tuple((tuple((Position[Index] - FabricOrigin[Index] for Index in range(3))) for Position in Path))

    def IterGeneratedSeamOptions(Signal: str, LaneFactor: PhysicalPortLaneFactor, Seam: PhysicalPortSeamFactor) -> Iterable[PhysicalComponentPortReservation]:
        nonlocal SeamFactorExpansionCount
        nonlocal AccessFactorExpansionCount
        nonlocal DominatedPortReservationPruneCount
        nonlocal PortOptionSelfClaimPruneCount
        ForeignCorridorClaims: dict[str, RoutingResourceClaims] = {}
        for _PhysicalSeam in (Seam,):
            SeamFactorExpansionCount += 1
            PortOptionGenerationCountBySignal[Signal] += 1
            ReservedNodes = frozenset(Seam.LocalPath) | frozenset(Seam.GlobalPath)
            Claims = ResourceGraph.BuildRouteClaims(ReservedNodes)
            LocalClaims = ResourceGraph.BuildRouteClaims(frozenset(Seam.LocalPath))
            GlobalClaims = ResourceGraph.BuildRouteClaims(frozenset(Seam.GlobalPath))
            if FindSelfClaimConflicts({Signal: Claims}):
                PortOptionSelfClaimPruneCount += 1
                continue
            if FindSignalClaimConflicts({**ForeignCorridorClaims, Signal: Claims}, Signal):
                continue
            SeamKey = (Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint)
            if Claims in RealizablePortClaimsBySeam[SeamKey]:
                DominatedPortReservationPruneCount += 1
                continue
            ReservationIdentity = (LaneFactor.Direction, LaneFactor.FabricDomainFingerprint, RelativePath(Seam.LocalPath), RelativePath(Seam.GlobalPath), LaneFactor.Capacity)
            Fingerprint = BuildStableFingerprint(ReservationIdentity)
            if Fingerprint in GeneratedPortFingerprintsBySignal[Signal]:
                continue
            GeneratedPortFingerprintsBySignal[Signal].add(Fingerprint)
            RealizablePortClaimsBySeam[SeamKey].add(Claims)
            yield PhysicalComponentPortReservation(Signal=Signal, Direction=LaneFactor.Direction, OwnedTerminals=LaneFactor.OwnedTerminals, OwnedTerminalFingerprints=tuple((Domain.TerminalFingerprint for Domain in LaneFactor.Domains)), OwnedCandidateFingerprints=Seam.OwnedCandidateFingerprints, FabricDomainFingerprint=LaneFactor.FabricDomainFingerprint, FabricAttachment=Seam.FabricAttachment, Attachment=Seam.Attachment, LocalPath=Seam.LocalPath, GlobalPath=Seam.GlobalPath, Claims=Claims, LocalClaims=LocalClaims, GlobalClaims=GlobalClaims, OwnedAccessCandidates=tuple((Candidate for DomainCandidates in LaneFactor.CandidateDomains for Candidate in DomainCandidates if Candidate.CandidateFingerprint in frozenset(Seam.OwnedCandidateFingerprints))), Capacity=LaneFactor.Capacity, ReservationFingerprint=Fingerprint)
            return
    PortOptionIteratorsBySeam = {(Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint): iter(IterGeneratedSeamOptions(Signal, LaneFactor, Seam)) for Signal in OrderedSignals for LaneFactor in LaneFactorsBySignal[Signal] for Seam in LaneFactor.Seams}

    def ClaimsCompatible(FirstSignal: str, FirstFingerprint: str, FirstClaims: RoutingResourceClaims, SecondSignal: str, SecondFingerprint: str, SecondClaims: RoutingResourceClaims) -> bool:
        CompatibilityKey = tuple(sorted((FirstFingerprint, SecondFingerprint)))
        Compatible = PortClaimCompatibilityCache.get(CompatibilityKey)
        if Compatible is None:
            Compatible = not ComponentClaimsConflict(FirstClaims, SecondClaims)
            PortClaimCompatibilityCache[CompatibilityKey] = Compatible
        return Compatible
    ExternalClaimsCache: dict[tuple[str, tuple[tuple[int, int, int], ...]], RoutingResourceClaims] = {}
    PhysicalPortContractClaimsCache: dict[tuple[str, tuple[str, ...]], RoutingResourceClaims] = PersistentPortCspState.PortContractClaimsCache
    PhysicalPortNoGoodKeyCache: dict[str, frozenset[tuple[str, str]]] = PersistentPortCspState.PortNoGoodKeyCache
    PersistentPortContractClaimsEntryCountAtStart = len(PhysicalPortContractClaimsCache)
    PersistentPortNoGoodKeyEntryCountAtStart = len(PhysicalPortNoGoodKeyCache)

    def PhysicalPortNoGoodKeys(Port: PhysicalComponentPortReservation) -> frozenset[tuple[str, str]]:
        """Return every proof-level identity represented by one option."""
        Cached = PhysicalPortNoGoodKeyCache.get(Port.ReservationFingerprint)
        if Cached is not None:
            return Cached
        Cached = BuildPhysicalPortNoGoodKeys(Port, PortSolverCacheKey)
        PhysicalPortNoGoodKeyCache[Port.ReservationFingerprint] = Cached
        return Cached

    def PhysicalPortContractClaims(Port: PhysicalComponentPortReservation) -> RoutingResourceClaims:
        """Return ownership appropriate to the current stage boundary."""
        CacheKey = (Port.ReservationFingerprint, ('seam-only',) if DeferLocalCompositeSelection else tuple(sorted(Port.OwnedCandidateFingerprints)))
        Cached = PhysicalPortContractClaimsCache.get(CacheKey)
        if Cached is None:
            Cached = ResourceGraph.BuildRouteClaims(frozenset((*Port.LocalPath, *Port.GlobalPath, *(Position for Candidate in Port.OwnedAccessCandidates for Position in Candidate.Path if not DeferLocalCompositeSelection))))
            PhysicalPortContractClaimsCache[CacheKey] = Cached
        return Cached

    def ExternalPortClaims(Signal: str, Value: Any) -> RoutingResourceClaims:
        Path = tuple(Value.GlobalPath)
        CacheKey = (Signal, Path)
        Cached = ExternalClaimsCache.get(CacheKey)
        if Cached is None:
            Cached = ResourceGraph.BuildRouteClaims(frozenset(Path))
            ExternalClaimsCache[CacheKey] = Cached
        return Cached

    def IterPortOptions(Signal: str, Selected: tuple[PhysicalComponentPortReservation, ...], AllowedLaneFingerprints: frozenset[str] | None=None) -> Iterable[PhysicalComponentPortReservation]:
        SelectedReservationKeys = frozenset((Key for Value in Selected for Key in PhysicalPortNoGoodKeys(Value)))
        for LaneFactor in sorted(LaneFactorsBySignal[Signal], key=lambda Value: (Value.FabricDomainFingerprint, Value.OwnedTerminals)):
            if AllowedLaneFingerprints is not None and LaneFactor.FabricDomainFingerprint not in AllowedLaneFingerprints:
                continue
            LaneReservationKeys = SelectedReservationKeys | frozenset(((Signal, 'fabric-domain:' + LaneFactor.FabricDomainFingerprint), (Signal, 'local-factor-domain:' + PortSolverCacheKey + ':' + LaneFactor.FabricDomainFingerprint), (Signal, 'local-signal-domain:' + PortSolverCacheKey)))
            if ReservationKeysContainRejectedPortClause(LaneReservationKeys):
                continue
            for Seam in InterleavePhysicalPortSeamsByEgressClass(LaneFactor.Seams, BaseKey=lambda Value: (min((abs(Value.Attachment[0] - GuideX) + abs(Value.Attachment[2] - GuideZ) for GuideX, GuideZ in LaneFactor.GuideCells), default=0), sum((abs(Value.Attachment[0] - Terminal[0]) + abs(Value.Attachment[1] - Terminal[1]) + abs(Value.Attachment[2] - Terminal[2]) for Terminal in LaneFactor.ExternalTerminals)), Value.SeamFingerprint)):
                SeamExternalClaims = ExternalPortClaims(Signal, Seam)
                if any((not ClaimsCompatible(Signal, Seam.SeamFingerprint, SeamExternalClaims, Value.Signal, Value.ReservationFingerprint, ExternalPortClaims(Value.Signal, Value)) for Value in Selected)):
                    continue
                SeamKey = (Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint)
                YieldedCount = 0
                while True:
                    CachedOptions = GeneratedPortOptionsBySeam[SeamKey]
                    while YieldedCount < len(CachedOptions):
                        yield CachedOptions[YieldedCount]
                        YieldedCount += 1
                    try:
                        Option = next(PortOptionIteratorsBySeam[SeamKey])
                    except StopIteration:
                        ExhaustedPortSeamKeys.add(SeamKey)
                        break
                    CachedOptions.append(Option)
    RejectedPortClauses = tuple((*Resources.RejectedPhysicalComponentPortReservationSets, *LocalSeamNoGoodClauses, *CertifiedLocalPairNoGoodClauses))
    RejectedUnaryPortKeys = frozenset((next(iter(Clause)) for Clause in RejectedPortClauses if len(Clause) == 1))
    MutableRejectedBinaryPortKeysByKey: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for Clause in RejectedPortClauses:
        if len(Clause) != 2:
            continue
        First, Second = tuple(Clause)
        MutableRejectedBinaryPortKeysByKey[First].add(Second)
        MutableRejectedBinaryPortKeysByKey[Second].add(First)
    RejectedBinaryPortKeysByKey = {Key: frozenset(Values) for Key, Values in MutableRejectedBinaryPortKeysByKey.items()}
    RejectedHigherOrderPortClauses = tuple((Clause for Clause in RejectedPortClauses if len(Clause) > 2))

    def ReservationKeysContainRejectedPortClause(Keys: frozenset[tuple[str, str]]) -> bool:
        if Keys.intersection(RejectedUnaryPortKeys):
            return True
        if any((Keys.intersection(RejectedBinaryPortKeysByKey.get(Key, frozenset())) for Key in Keys)):
            return True
        return any((Clause.issubset(Keys) for Clause in RejectedHigherOrderPortClauses))

    def CompatibleWithSelected(Option: PhysicalComponentPortReservation, Selected: tuple[PhysicalComponentPortReservation, ...]) -> bool:
        RejectedSignalKeys = Resources.RejectedPhysicalComponentPortReservationsBySignal.get(Option.Signal, set())
        if any((Fingerprint in RejectedSignalKeys for Signal, Fingerprint in PhysicalPortNoGoodKeys(Option) if Signal == Option.Signal)):
            return False
        SelectedReservationKeys = frozenset((Key for Value in (*Selected, Option) for Key in PhysicalPortNoGoodKeys(Value)))
        if ReservationKeysContainRejectedPortClause(SelectedReservationKeys):
            return False
        for Value in Selected:
            if not ClaimsCompatible(Value.Signal, Value.ReservationFingerprint, PhysicalPortContractClaims(Value), Option.Signal, Option.ReservationFingerprint, PhysicalPortContractClaims(Option)):
                return False
        return True
    CachedPortOptionDomains = Resources.PhysicalComponentPortOptionDomainCache.get(PortSolverCacheKey)
    CompletePortOptionDomains: dict[str, tuple[PhysicalComponentPortReservation, ...]] | None = dict(CachedPortOptionDomains) if CachedPortOptionDomains is not None else None
    CachedPortOptionArcSupport = Resources.PhysicalComponentPortOptionArcSupportCache.get(PortSolverCacheKey)
    PortOptionArcSupportIndex: dict[tuple[str, str, str], frozenset[str]] | None = dict(CachedPortOptionArcSupport) if CachedPortOptionArcSupport is not None else None
    PortOptionDomainPropagationCache: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], dict[str, tuple[PhysicalComponentPortReservation, ...]] | None] = PersistentPortCspState.OptionDomainPropagationCache
    LatestPortOptionDomainsByState = PersistentPortCspState.LatestOptionDomainsByState
    PersistentPortOptionPropagationStateCountAtStart = len(LatestPortOptionDomainsByState)
    PortOptionDomainMaterializationCount = 0
    PortOptionArcConsistencyCheckCount = 0
    PortOptionArcConsistencyIntersectionCount = 0
    PortOptionDomainPropagationCount = 0
    PortOptionDomainPruneCount = 0
    PersistentPortOptionPropagationCacheHitCount = 0
    IncrementalPortOptionPropagationReuseCount = 0
    ActiveApertureContractRestrictionsBySignal: dict[str, tuple[str, str]] = {}

    def GetCompletePortOptionDomains() -> dict[str, tuple[PhysicalComponentPortReservation, ...]]:
        """Materialize the finite global seam variables exactly once."""
        nonlocal CompletePortOptionDomains
        nonlocal PortOptionDomainMaterializationCount
        if CompletePortOptionDomains is None:
            CompletePortOptionDomains = {}
            for Signal in OrderedSignals:
                Options = tuple(IterPortOptions(Signal, ()))
                CompletePortOptionDomains[Signal] = Options
                PortOptionDomainMaterializationCount += len(Options)
                if WorkCheck is not None:
                    WorkCheck({'Stage': 'physical-port-option-domain', 'Signal': Signal, 'PortOptionDomainMaterializationCount': PortOptionDomainMaterializationCount, 'PortOptionDomainSizes': {Name: len(Values) for Name, Values in CompletePortOptionDomains.items()}})
            Resources.PhysicalComponentPortOptionDomainCache[PortSolverCacheKey] = tuple(sorted(CompletePortOptionDomains.items()))
        return CompletePortOptionDomains

    def GetPortOptionArcSupportIndex() -> dict[tuple[str, str, str], frozenset[str]]:
        """Compile exact seam compatibility into reusable binary supports."""
        nonlocal PortOptionArcSupportIndex
        nonlocal PortOptionArcConsistencyCheckCount
        if PortOptionArcSupportIndex is not None:
            return PortOptionArcSupportIndex
        Domains = GetCompletePortOptionDomains()
        MutableSupport: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for FirstIndex, FirstSignal in enumerate(OrderedSignals):
            for SecondSignal in OrderedSignals[FirstIndex + 1:]:
                for First in Domains[FirstSignal]:
                    for Second in Domains[SecondSignal]:
                        PortOptionArcConsistencyCheckCount += 1
                        if WorkCheck is not None and PortOptionArcConsistencyCheckCount % 128 == 0:
                            WorkCheck({'Stage': 'physical-port-option-support-index', 'PortOptionArcConsistencyCheckCount': PortOptionArcConsistencyCheckCount, 'PortOptionDomainMaterializationCount': PortOptionDomainMaterializationCount})
                        if not ClaimsCompatible(First.Signal, First.ReservationFingerprint, PhysicalPortContractClaims(First), Second.Signal, Second.ReservationFingerprint, PhysicalPortContractClaims(Second)):
                            continue
                        MutableSupport[FirstSignal, First.ReservationFingerprint, SecondSignal].add(Second.ReservationFingerprint)
                        MutableSupport[SecondSignal, Second.ReservationFingerprint, FirstSignal].add(First.ReservationFingerprint)
        PortOptionArcSupportIndex = {Key: frozenset(Values) for Key, Values in MutableSupport.items()}
        Resources.PhysicalComponentPortOptionArcSupportCache[PortSolverCacheKey] = tuple(sorted(PortOptionArcSupportIndex.items()))
        return PortOptionArcSupportIndex

    def PropagatePortOptionDomains(Remaining: tuple[str, ...], Selected: tuple[PhysicalComponentPortReservation, ...]) -> dict[str, tuple[PhysicalComponentPortReservation, ...]] | None:
        """Enforce exact seam arc consistency after selected assignments."""
        nonlocal PortOptionDomainPropagationCount
        nonlocal PortOptionArcConsistencyIntersectionCount
        nonlocal PortOptionDomainPruneCount
        nonlocal PersistentPortOptionPropagationCacheHitCount
        nonlocal IncrementalPortOptionPropagationReuseCount
        NoGoodEpoch = CurrentPortNoGoodEpoch()
        PreferredGlobalContracts = Resources.PreferredPhysicalComponentGlobalContractsBySignal
        PreferredApertureContracts = Resources.PreferredPhysicalComponentApertureContractsBySignal
        PreferredReservations = Resources.PreferredPhysicalComponentPortReservationsBySignal
        PreferenceEpoch = (tuple(sorted(PreferredGlobalContracts.items())), tuple(sorted(PreferredApertureContracts.items())), tuple(sorted(PreferredReservations.items())), tuple(sorted(ActiveApertureContractRestrictionsBySignal.items())))
        StateKey = (tuple(sorted(ActiveApertureContractRestrictionsBySignal.items())), tuple(sorted(Remaining)), SelectedState(Selected))
        CacheKey = (*StateKey, NoGoodEpoch, PreferenceEpoch)
        if CacheKey in PortOptionDomainPropagationCache:
            PersistentPortOptionPropagationCacheHitCount += 1
            return PortOptionDomainPropagationCache[CacheKey]
        PortOptionDomainPropagationCount += 1
        CompleteDomains = GetCompletePortOptionDomains()
        PriorEntry = LatestPortOptionDomainsByState.get(StateKey)
        PriorDomains = None
        PriorNoGoodEpoch = ()
        if PriorEntry is not None:
            PriorNoGoodEpoch, PriorDomains = PriorEntry
            if not frozenset(PriorNoGoodEpoch).issubset(frozenset(NoGoodEpoch)):
                PriorDomains = None
        if PriorEntry is not None and PriorDomains is None:
            if frozenset(PriorNoGoodEpoch).issubset(frozenset(NoGoodEpoch)):
                PersistentPortOptionPropagationCacheHitCount += 1
                PortOptionDomainPropagationCache[CacheKey] = None
                LatestPortOptionDomainsByState[StateKey] = (NoGoodEpoch, None)
                return None
        StartingDomains = PriorDomains if PriorDomains is not None else CompleteDomains
        if PriorDomains is not None:
            IncrementalPortOptionPropagationReuseCount += 1
        FilteredDomains = {Signal: OrderPhysicalPortOptionsByPreferences(Signal, (Option for Option in StartingDomains[Signal] if CompatibleWithSelected(Option, Selected) and (Signal not in ActiveApertureContractRestrictionsBySignal or (BuildPhysicalPortGlobalContractFingerprint(Option), BuildPhysicalPortApertureContractFingerprint(Option)) == ActiveApertureContractRestrictionsBySignal[Signal])), PreferredGlobalContracts, PreferredReservations) for Signal in Remaining}
        IncrementalMembershipChanged = bool(PriorDomains is not None and any((len(FilteredDomains[Signal]) != len(PriorDomains[Signal]) for Signal in Remaining)))
        if any((not Values for Values in FilteredDomains.values())):
            PortOptionDomainPropagationCache[CacheKey] = None
            LatestPortOptionDomainsByState[StateKey] = (NoGoodEpoch, None)
            return None
        SupportIndex = GetPortOptionArcSupportIndex()
        MutableDomains = {Signal: list(Values) for Signal, Values in FilteredDomains.items()}
        SelectedNoGoodKeysBySignal = {Value.Signal: PhysicalPortNoGoodKeys(Value) for Value in Selected}
        BinaryRejectedSets = SelectBinaryExactNoGoodClauses((*Resources.RejectedPhysicalComponentPortReservationSets, *LocalSeamNoGoodClauses, *CertifiedLocalPairNoGoodClauses))
        LearnedPairCompatibilityCache: dict[tuple[str, str], bool] = {}
        AddedNoGoodClauses = frozenset(NoGoodEpoch) - frozenset(PriorNoGoodEpoch)
        AddedBinaryNoGoodClauses = SelectBinaryExactNoGoodClauses((frozenset(Clause) for Clause in AddedNoGoodClauses))

        def LearnedPairCompatible(First: PhysicalComponentPortReservation, Second: PhysicalComponentPortReservation) -> bool:
            Key = tuple(sorted((First.ReservationFingerprint, Second.ReservationFingerprint)))
            Cached = LearnedPairCompatibilityCache.get(Key)
            if Cached is None:
                PairKeys = PhysicalPortNoGoodKeys(First) | PhysicalPortNoGoodKeys(Second)
                Cached = not ReservationKeysContainRejectedPortClause(PairKeys)
                LearnedPairCompatibilityCache[Key] = Cached
            return Cached
        Changed = True
        while Changed:
            Changed = False
            ClausePropagated = PropagateExactNoGoodClauses({Signal: tuple(Values) for Signal, Values in MutableDomains.items()}, SelectedNoGoodKeysBySignal, RejectedHigherOrderPortClauses, PhysicalPortNoGoodKeys)
            if ClausePropagated is None:
                PortOptionDomainPropagationCache[CacheKey] = None
                LatestPortOptionDomainsByState[StateKey] = (NoGoodEpoch, None)
                return None
            for Signal, Values in ClausePropagated.items():
                if len(Values) != len(MutableDomains[Signal]):
                    MutableDomains[Signal] = list(Values)
                    Changed = True
            if PriorDomains is not None and (not Changed) and (not IncrementalMembershipChanged) and (not AddedBinaryNoGoodClauses):
                break
            for Signal in sorted(Remaining):
                Retained = []
                for Option in MutableDomains[Signal]:
                    SupportedEverywhere = True
                    for OtherSignal in sorted(Remaining):
                        if OtherSignal == Signal:
                            continue
                        SupportedFingerprints = SupportIndex.get((Signal, Option.ReservationFingerprint, OtherSignal), frozenset())
                        PortOptionArcConsistencyIntersectionCount += 1
                        if not any((Other.ReservationFingerprint in SupportedFingerprints and LearnedPairCompatible(Option, Other) for Other in MutableDomains[OtherSignal])):
                            SupportedEverywhere = False
                            break
                    if SupportedEverywhere:
                        Retained.append(Option)
                if len(Retained) != len(MutableDomains[Signal]):
                    MutableDomains[Signal] = Retained
                    Changed = True
                    if not Retained:
                        PortOptionDomainPropagationCache[CacheKey] = None
                        LatestPortOptionDomainsByState[StateKey] = (NoGoodEpoch, None)
                        return None
        Result = {Signal: tuple(Values) for Signal, Values in MutableDomains.items()}
        PortOptionDomainPruneCount += sum((len(CompleteDomains[Signal]) - len(Result[Signal]) for Signal in Remaining))
        PortOptionDomainPropagationCache[CacheKey] = Result
        LatestPortOptionDomainsByState[StateKey] = (NoGoodEpoch, Result)
        return Result
    FactorDomainPropagationCount = 0
    FactorDomainPruneCount = 0
    ForwardSupportCheckCount = 0
    ForwardSupportCacheHitCount = 0
    ForwardSupportWitnessHitCount = 0
    LaneArcConsistencyCheckCount = 0
    LaneArcConsistencyCacheHitCount = 0
    ActivePropagationAssignedPortCount = 0
    FactorDomainPropagationCache: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...], str], dict[str, tuple[PhysicalPortLaneFactor, ...]] | None] = {}
    ForwardSupportCache: dict[tuple[str, tuple[tuple[str, tuple[str, str]], ...], tuple[tuple[str, str], ...], tuple[str, ...]], PhysicalComponentPortReservation | None] = {}
    CachedLaneArcSupport = Resources.PhysicalComponentPortLaneArcSupportCache.get(PortSolverCacheKey)
    LaneArcSupportIndex: dict[tuple[str, str, str], frozenset[str]] | None = dict(CachedLaneArcSupport) if CachedLaneArcSupport is not None else None
    ArcConsistentFactorDomainCache: dict[tuple[tuple[str, tuple[str, ...]], ...], dict[str, tuple[PhysicalPortLaneFactor, ...]] | None] = {}
    FactorArcClosureCount = 0
    FactorArcClosureCacheHitCount = 0
    LaneArcSupportIntersectionCount = 0

    def SelectedState(Selected: tuple[PhysicalComponentPortReservation, ...]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(((Value.Signal, Value.ReservationFingerprint) for Value in Selected)))

    def CurrentPortNoGoodEpoch() -> tuple[tuple[tuple[str, str], ...], ...]:
        """Identity of proof-qualified constraints learned since preparation."""
        return tuple(sorted((tuple(sorted(((str(Signal), str(Fingerprint)) for Signal, Fingerprint in RejectedSet))) for RejectedSet in (*Resources.RejectedPhysicalComponentPortReservationSets, *LocalSeamNoGoodClauses, *CertifiedLocalPairNoGoodClauses, *(frozenset(((Signal, Fingerprint),)) for Signal, Fingerprints in Resources.RejectedPhysicalComponentPortReservationsBySignal.items() for Fingerprint in Fingerprints)))))
    FactorPortNoGoodEpochFingerprint = BuildStableFingerprint(('physical-port-factor-no-good-epoch-v1', CurrentPortNoGoodEpoch()))

    def LaneAllowedWithSelected(Signal: str, LaneFactor: PhysicalPortLaneFactor, Selected: tuple[PhysicalComponentPortReservation, ...]) -> bool:
        ReservationKeys = frozenset((Key for Value in Selected for Key in PhysicalPortNoGoodKeys(Value))) | frozenset(((Signal, 'fabric-domain:' + LaneFactor.FabricDomainFingerprint), (Signal, 'local-factor-domain:' + PortSolverCacheKey + ':' + LaneFactor.FabricDomainFingerprint), (Signal, 'local-signal-domain:' + PortSolverCacheKey)))
        return not ReservationKeysContainRejectedPortClause(ReservationKeys)

    def LaneSupportsSelected(Signal: str, LaneFactor: PhysicalPortLaneFactor, Selected: tuple[PhysicalComponentPortReservation, ...]) -> bool:
        return any((all((ClaimsCompatible(Signal, Seam.SeamFingerprint, ExternalPortClaims(Signal, Seam), Value.Signal, Value.ReservationFingerprint, ExternalPortClaims(Value.Signal, Value)) for Value in Selected)) for Seam in LaneFactor.Seams))

    def GetLaneArcSupportIndex() -> dict[tuple[str, str, str], frozenset[str]]:
        """Compile immutable lane-pair claims into a reusable support index."""
        nonlocal LaneArcSupportIndex
        nonlocal LaneArcConsistencyCheckCount
        if LaneArcSupportIndex is not None:
            return LaneArcSupportIndex
        MutableSupport: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for FirstIndex, FirstSignal in enumerate(OrderedSignals):
            for SecondSignal in OrderedSignals[FirstIndex + 1:]:
                for First in LaneFactorsBySignal[FirstSignal]:
                    for Second in LaneFactorsBySignal[SecondSignal]:
                        LaneArcConsistencyCheckCount += 1
                        if WorkCheck is not None and LaneArcConsistencyCheckCount % 128 == 0:
                            WorkCheck({'Stage': 'physical-port-lane-support-index', 'LaneArcConsistencyCheckCount': LaneArcConsistencyCheckCount, 'PortCount': len(OrderedSignals)})
                        if not any((ClaimsCompatible(FirstSignal, FirstSeam.SeamFingerprint, ExternalPortClaims(FirstSignal, FirstSeam), SecondSignal, SecondSeam.SeamFingerprint, ExternalPortClaims(SecondSignal, SecondSeam)) for FirstSeam in First.Seams for SecondSeam in Second.Seams)):
                            continue
                        MutableSupport[FirstSignal, First.FabricDomainFingerprint, SecondSignal].add(Second.FabricDomainFingerprint)
                        MutableSupport[SecondSignal, Second.FabricDomainFingerprint, FirstSignal].add(First.FabricDomainFingerprint)
        LaneArcSupportIndex = {Key: frozenset(Values) for Key, Values in MutableSupport.items()}
        Resources.PhysicalComponentPortLaneArcSupportCache[PortSolverCacheKey] = tuple(sorted(LaneArcSupportIndex.items()))
        return LaneArcSupportIndex

    def PropagateFactorDomains(Remaining: tuple[str, ...], Selected: tuple[PhysicalComponentPortReservation, ...]) -> dict[str, tuple[PhysicalPortLaneFactor, ...]] | None:
        nonlocal FactorDomainPropagationCount, FactorDomainPruneCount
        nonlocal FactorArcClosureCount
        nonlocal FactorArcClosureCacheHitCount
        nonlocal LaneArcSupportIntersectionCount
        nonlocal ActivePropagationAssignedPortCount
        ActivePropagationAssignedPortCount = len(Selected)
        CacheKey = (tuple(sorted(Remaining)), SelectedState(Selected), FactorPortNoGoodEpochFingerprint)
        if CacheKey in FactorDomainPropagationCache:
            return FactorDomainPropagationCache[CacheKey]
        FactorDomainPropagationCount += 1
        Domains = {Signal: tuple((LaneFactor for LaneFactor in LaneFactorsBySignal[Signal] if LaneAllowedWithSelected(Signal, LaneFactor, Selected) and LaneSupportsSelected(Signal, LaneFactor, Selected))) for Signal in Remaining}
        if any((not Domain for Domain in Domains.values())):
            FactorDomainPropagationCache[CacheKey] = None
            return None
        FactorDomainPruneCount += sum((len(LaneFactorsBySignal[Signal]) - len(Domains[Signal]) for Signal in Remaining))
        InitialDomainState = tuple(((Signal, tuple((Value.FabricDomainFingerprint for Value in Domains[Signal]))) for Signal in sorted(Remaining)))
        if InitialDomainState in ArcConsistentFactorDomainCache:
            FactorArcClosureCacheHitCount += 1
            Result = ArcConsistentFactorDomainCache[InitialDomainState]
            FactorDomainPropagationCache[CacheKey] = Result
            return Result
        FactorArcClosureCount += 1
        ArcSupportIndex = GetLaneArcSupportIndex()
        FingerprintDomains = {Signal: tuple((Value.FabricDomainFingerprint for Value in Domain)) for Signal, Domain in Domains.items()}
        PropagatedFingerprints, IntersectionCount = PropagateLaneFactorArcConsistency(FingerprintDomains, ArcSupportIndex)
        LaneArcSupportIntersectionCount += IntersectionCount
        if PropagatedFingerprints is None:
            Result = None
        else:
            Result = {Signal: tuple((Value for Value in Domains[Signal] if Value.FabricDomainFingerprint in frozenset(PropagatedFingerprints[Signal]))) for Signal in Domains}
            FactorDomainPruneCount += sum((len(Domains[Signal]) - len(Result[Signal]) for Signal in Domains))
        ArcConsistentFactorDomainCache[InitialDomainState] = Result
        FactorDomainPropagationCache[CacheKey] = Result
        return Result

    def FindForwardSupport(Signal: str, Selected: tuple[PhysicalComponentPortReservation, ...], LaneDomain: tuple[PhysicalPortLaneFactor, ...]) -> PhysicalComponentPortReservation | None:
        nonlocal ForwardSupportCheckCount
        nonlocal ForwardSupportCacheHitCount
        LaneFingerprints = tuple(sorted((Value.FabricDomainFingerprint for Value in LaneDomain)))
        CacheKey = (Signal, tuple(sorted(ActiveApertureContractRestrictionsBySignal.items())), SelectedState(Selected), LaneFingerprints)
        if CacheKey in ForwardSupportCache:
            ForwardSupportCacheHitCount += 1
            return ForwardSupportCache[CacheKey]
        ForwardSupportCheckCount += 1
        Result = next((Option for Option in IterPortOptions(Signal, Selected, frozenset(LaneFingerprints)) if OptionMatchesActiveApertureContract(Option) and CompatibleWithSelected(Option, Selected)), None)
        ForwardSupportCache[CacheKey] = Result
        return Result

    def SelectPorts(Remaining: tuple[str, ...], Selected: tuple[PhysicalComponentPortReservation, ...], ForwardWitnesses: dict[str, PhysicalComponentPortReservation] | None=None) -> bool:
        nonlocal SelectedPorts, PortAssignmentExpansionCount
        nonlocal ForwardSupportWitnessHitCount
        PortAssignmentExpansionCount += 1
        SelectedReservations = SelectedState(Selected)
        State = (tuple(sorted(ActiveApertureContractRestrictionsBySignal.items())), tuple(sorted(Remaining)), SelectedReservations)
        if WorkCheck is not None and (PortAssignmentExpansionCount == 1 or PortAssignmentExpansionCount % 128 == 0):
            WorkCheck({'Stage': 'physical-port-capacity', 'AssignedPortCount': len(Selected), 'PortCount': len(OrderedSignals), 'ExpansionCount': PortAssignmentExpansionCount, 'LaneFactorExpansionCount': LaneFactorExpansionCount, 'AccessFactorExpansionCount': AccessFactorExpansionCount, 'SeamFactorExpansionCount': SeamFactorExpansionCount, 'GlobalConnectorSearchCount': GlobalConnectorSearchCount, 'GlobalConnectorCacheHitCount': GlobalConnectorCacheHitCount, 'GlobalConnectorPortableCacheHitCount': GlobalConnectorPortableCacheHitCount, 'GlobalConnectorPortableCacheValidationRejectCount': GlobalConnectorPortableCacheValidationRejectCount, 'GlobalConnectorPortableCacheStoreCount': GlobalConnectorPortableCacheStoreCount, 'GlobalConnectorExpansionCount': GlobalConnectorExpansionCount, 'GlobalGuideFieldBuildCount': GlobalGuideFieldBuildCount, 'GlobalGuideFieldExpansionCount': GlobalGuideFieldExpansionCount, 'GlobalGuideFieldHitCount': GlobalGuideFieldHitCount, 'GlobalGuideFieldCanonicalPathCount': GlobalGuideFieldCanonicalPathCount, 'GlobalGuideFieldFallbackCount': GlobalGuideFieldFallbackCount, 'FactorDomainPropagationCount': FactorDomainPropagationCount, 'FactorDomainPruneCount': FactorDomainPruneCount, 'ForwardSupportCheckCount': ForwardSupportCheckCount, 'ForwardSupportCacheHitCount': ForwardSupportCacheHitCount, 'ForwardSupportWitnessHitCount': ForwardSupportWitnessHitCount, 'LaneArcConsistencyCheckCount': LaneArcConsistencyCheckCount, 'LaneArcConsistencyCacheHitCount': LaneArcConsistencyCacheHitCount, 'FactorArcClosureCount': FactorArcClosureCount, 'FactorArcClosureCacheHitCount': FactorArcClosureCacheHitCount, 'LaneArcSupportIntersectionCount': LaneArcSupportIntersectionCount, 'GeneratedPortOptionCountBySignal': dict(PortOptionGenerationCountBySignal), 'PortOptionDomainMaterializationCount': PortOptionDomainMaterializationCount, 'PortOptionArcConsistencyCheckCount': PortOptionArcConsistencyCheckCount, 'PortOptionArcConsistencyIntersectionCount': PortOptionArcConsistencyIntersectionCount, 'PortOptionDomainPropagationCount': PortOptionDomainPropagationCount, 'PortOptionDomainPruneCount': PortOptionDomainPruneCount, 'PersistentPortOptionPropagationCacheHitCount': PersistentPortOptionPropagationCacheHitCount, 'IncrementalPortOptionPropagationReuseCount': IncrementalPortOptionPropagationReuseCount, 'PersistentPortOptionPropagationStateCountAtStart': PersistentPortOptionPropagationStateCountAtStart, 'LocalRealizabilityCheckCountBySignal': dict(LocalRealizabilityCheckCountBySignal), 'LocalRealizabilityRejectedSeamCountBySignal': dict(LocalRealizabilityRejectedSeamCountBySignal)})
        if not Remaining:
            PortAssignmentFingerprint = BuildStableFingerprint(tuple(((Value.Signal, Value.ReservationFingerprint) for Value in sorted(Selected, key=lambda Candidate: Candidate.Signal))))
            if PortAssignmentFingerprint in Resources.RejectedPhysicalComponentPortAssignmentFingerprints or PortAssignmentFingerprint in getattr(Resources, 'DeferredPhysicalComponentPortAssignmentFingerprints', set()):
                FailedPortAssignmentStates.add(State)
                return False
            SelectedPorts = Selected
            return True
        if State in FailedPortAssignmentStates:
            return False
        PropagatedOptionDomains = PropagatePortOptionDomains(Remaining, Selected)
        if PropagatedOptionDomains is None:
            FailedPortAssignmentStates.add(State)
            return False
        SelectedNoGoodKeysBySignal = {Value.Signal: PhysicalPortNoGoodKeys(Value) for Value in Selected}
        Signal, OrderedOptions = SelectExactNoGoodCspBranch(PropagatedOptionDomains, SelectedNoGoodKeysBySignal, (*Resources.RejectedPhysicalComponentPortReservationSets, *LocalSeamNoGoodClauses, *CertifiedLocalPairNoGoodClauses), PhysicalPortNoGoodKeys)
        NextRemaining = tuple((Value for Value in Remaining if Value != Signal))
        for Option in OrderedOptions:
            NextSelected = (*Selected, Option)
            NextDomains = PropagatePortOptionDomains(NextRemaining, NextSelected)
            if NextRemaining:
                if NextDomains is None:
                    continue
            if SelectPorts(NextRemaining, NextSelected, None):
                return True
        FailedPortAssignmentStates.add(State)
        return False
    ApertureSelectionExpansionCount = 0
    FailedApertureRestrictionStates = set() if RequiredBoundaryPorts is not None else PersistentPortCspState.FailedApertureRestrictionStates
    BoundaryPortDomainsBySignal = {Signal: tuple((Value for Value in Values if BuildPhysicalPortApertureContractFingerprint(Value) not in Resources.RejectedPhysicalComponentPortReservationsBySignal.get(Signal, set()))) for Signal, Values in Preparation.BoundaryPortReservationsBySignal}
    RejectedBoundaryAssignmentFingerprints = getattr(Resources, 'RejectedPhysicalComponentBoundaryAssignmentFingerprints', None)
    if RejectedBoundaryAssignmentFingerprints is None:
        RejectedBoundaryAssignmentFingerprints = set()
        setattr(Resources, 'RejectedPhysicalComponentBoundaryAssignmentFingerprints', RejectedBoundaryAssignmentFingerprints)
    SelectedBoundaryPorts: tuple[PhysicalComponentBoundaryPortReservation, ...] | None = None
    SelectedBoundaryAssignmentFingerprint = ''
    BoundaryIteratorCache = getattr(Resources, 'PhysicalComponentBoundaryAssignmentIteratorCache', None)
    if BoundaryIteratorCache is None:
        BoundaryIteratorCache = {}
        setattr(Resources, 'PhysicalComponentBoundaryAssignmentIteratorCache', BoundaryIteratorCache)
    BoundaryIteratorCacheKey = BuildPhysicalComponentAssemblyPlanDomainFingerprint(Preparation.DomainFingerprint, DeferLocalCompositeSelection)
    Resources.PhysicalComponentAssemblyPlanDomainFingerprint = BoundaryIteratorCacheKey
    Resources.PhysicalComponentDeferLocalCompositeSelection = bool(DeferLocalCompositeSelection)
    BoundaryAssignmentIterator = None
    BoundaryAssignmentIteratorReused = False
    ComponentPlannerLiveDiagnostics: dict[str, object] = {
        "DeferLocalCompositeSelection": bool(
            DeferLocalCompositeSelection
        ),
        "BoundaryPortDomainCountBySignal": {
            Signal: len(Values)
            for Signal, Values
            in sorted(BoundaryPortDomainsBySignal.items())
        },
        "LaneFactorCountBySignal": {
            Signal: len(Values)
            for Signal, Values in sorted(LaneFactorsBySignal.items())
        },
        "SeamFactorCountBySignal": {
            Signal: sum(len(Value.Seams) for Value in Values)
            for Signal, Values in sorted(LaneFactorsBySignal.items())
        },
        "LocalAccessFactorCountBySignal": {
            Signal: len(Values)
            for Signal, Values
            in sorted(dict(CurrentLocalAccessFactorsBySignal).items())
        },
        "ApertureFactorCountBySignal": {
            Signal: len(Values)
            for Signal, Values
            in sorted(dict(CurrentApertureFactorsBySignal).items())
        },
        "LocalApertureSupportCountBySignal": {
            Signal: len(Values)
            for Signal, Values
            in sorted(dict(CurrentLocalApertureSupportBySignal).items())
        },
        "CapacityRepairBoundaryPreferenceSignals": sorted(
            PreferredCapacityRepairApertureContractsBySignal
        ),
    }

    def CheckComponentPlannerWork(Diagnostics: dict[str, object]) -> None:
        if WorkCheck is not None:
            WorkCheck({**Diagnostics, **ComponentPlannerLiveDiagnostics})
    IsRequiredBoundaryFixed = RequiredBoundaryPorts is not None
    if IsRequiredBoundaryFixed:
        RequiredBoundaryPorts = tuple(sorted(RequiredBoundaryPorts, key=lambda Value: Value.Signal))
        if tuple((Value.Signal for Value in RequiredBoundaryPorts)) != OrderedSignals:
            raise ValueError('required physical boundary does not match the prepared component interface')
        BoundaryAssignmentIterator = iter((RequiredBoundaryPorts,))
    else:
        BoundaryAssignmentIterator = BoundaryIteratorCache.get(BoundaryIteratorCacheKey)
        BoundaryAssignmentIteratorReused = BoundaryAssignmentIterator is not None
    if BoundaryAssignmentIterator is None:
        BoundaryAssignmentIterator = iter(IterPreparedPhysicalBoundaryAssignments(Preparation, Resources, BoundaryPortDomainsBySignal, CertifiedLocalPairNoGoodClauses, LocalSeamNoGoodClauses, PortSolverCacheKey, ResourceGraph, CheckComponentPlannerWork, Deadline, DeferLocalCompositeSelection=DeferLocalCompositeSelection))  # PreferredApertureContractsBySignal
        BoundaryIteratorCache[BoundaryIteratorCacheKey] = BoundaryAssignmentIterator

    def OptionMatchesActiveApertureContract(Option: PhysicalComponentPortReservation) -> bool:
        Restriction = ActiveApertureContractRestrictionsBySignal.get(Option.Signal)
        return bool(Restriction is None or Restriction == (BuildPhysicalPortGlobalContractFingerprint(Option), BuildPhysicalPortApertureContractFingerprint(Option)))

    def SelectFactorizedPorts(Remaining: tuple[str, ...], Selected: tuple[PhysicalComponentPortReservation, ...]) -> bool:
        """Solve fixed apertures without materializing the option product."""
        nonlocal SelectedPorts, PortAssignmentExpansionCount
        nonlocal ForwardSupportWitnessHitCount
        PortAssignmentExpansionCount += 1
        State = (tuple(sorted(ActiveApertureContractRestrictionsBySignal.items())), tuple(sorted(Remaining)), SelectedState(Selected))
        if State in FailedFactorizedPortAssignmentStates:
            return False
        if WorkCheck is not None and (PortAssignmentExpansionCount == 1 or PortAssignmentExpansionCount % 128 == 0):
            WorkCheck({'Stage': 'physical-port-capacity', 'FactorizedPortSearch': True, 'AssignedPortCount': len(Selected), 'PortCount': len(OrderedSignals), 'ExpansionCount': PortAssignmentExpansionCount, 'LaneFactorExpansionCount': LaneFactorExpansionCount, 'AccessFactorExpansionCount': AccessFactorExpansionCount, 'SeamFactorExpansionCount': SeamFactorExpansionCount, 'FactorDomainPropagationCount': FactorDomainPropagationCount, 'ForwardSupportCheckCount': ForwardSupportCheckCount, 'ForwardSupportWitnessHitCount': ForwardSupportWitnessHitCount, 'LaneArcConsistencyCheckCount': LaneArcConsistencyCheckCount, 'FactorArcClosureCount': FactorArcClosureCount, 'FactorArcClosureCacheHitCount': FactorArcClosureCacheHitCount, 'LaneArcSupportIntersectionCount': LaneArcSupportIntersectionCount, 'GeneratedPortOptionCountBySignal': dict(PortOptionGenerationCountBySignal)})
        if not Remaining:
            PortAssignmentFingerprint = BuildStableFingerprint(tuple(((Value.Signal, Value.ReservationFingerprint) for Value in sorted(Selected, key=lambda Candidate: Candidate.Signal))))
            if PortAssignmentFingerprint in Resources.RejectedPhysicalComponentPortAssignmentFingerprints or PortAssignmentFingerprint in getattr(Resources, 'DeferredPhysicalComponentPortAssignmentFingerprints', set()):
                FailedFactorizedPortAssignmentStates.add(State)
                return False
            SelectedPorts = Selected
            return True
        FactorDomains = PropagateFactorDomains(Remaining, Selected)
        if FactorDomains is None:
            FailedFactorizedPortAssignmentStates.add(State)
            return False
        Signal = SelectPhysicalFactorBranchSignal({Value: len(FactorDomains[Value]) for Value in Remaining}, (*Resources.RejectedPhysicalComponentPortReservationSets, *LocalSeamNoGoodClauses, *CertifiedLocalPairNoGoodClauses))
        NextRemaining = tuple((Value for Value in Remaining if Value != Signal))
        AllowedLaneFingerprints = frozenset((Value.FabricDomainFingerprint for Value in FactorDomains[Signal]))
        for Option in IterPortOptions(Signal, Selected, AllowedLaneFingerprints):
            if not OptionMatchesActiveApertureContract(Option) or not CompatibleWithSelected(Option, Selected):
                continue
            NextSelected = (*Selected, Option)
            if NextRemaining:
                NextDomains = PropagateFactorDomains(NextRemaining, NextSelected)
                if NextDomains is None:
                    continue
                ForwardSupported = True
                for OtherSignal in NextRemaining:
                    Witness = FindForwardSupport(OtherSignal, NextSelected, NextDomains[OtherSignal])
                    if Witness is None:
                        ForwardSupported = False
                        break
                    ForwardSupportWitnessHitCount += 1
                if not ForwardSupported:
                    continue
            if SelectFactorizedPorts(NextRemaining, NextSelected):
                return True
        FailedFactorizedPortAssignmentStates.add(State)
        return False
    SeamOnlyPortDomainsBySignal: dict[tuple[str, tuple[str, str] | None, str | None, str | None, str | None], tuple[PhysicalComponentPortReservation, ...]] = {}
    PreparedLocalFactorsBySignal = {str(Signal): {str(Value.LocalAccessFingerprint): Value for Value in Values} for Signal, Values in Preparation.LocalAccessFactorsBySignal}
    PreparedApertureFactorsBySignal = {str(Signal): {str(Value.ApertureOptionFingerprint): Value for Value in Values} for Signal, Values in Preparation.ApertureFactorsBySignal}
    PreparedLocalApertureSupportsByOption = {(str(Key[0]), str(Key[1])): tuple(Values) for Key, Values in Preparation.LocalApertureSupportsByOption}
    ActiveLocalAccessRestrictionsBySignal: dict[str, str] = {}
    ActiveSeamRestrictionsBySignal: dict[str, str] = {}
    ActiveSupportRestrictionsBySignal: dict[str, str] = {}

    def BuildHigherOrderSeamSupportRelation(Certificate: Any) -> tuple[frozenset[str], frozenset[frozenset[tuple[str, str]]]]:
        SignalDomain = tuple(map(str, Certificate.SignalDomain))
        SupportedProjections = set()
        for TupleValue in Certificate.SupportedSeamTuples:
            SeamBySignal = {str(Signal): str(Seam) for Signal, Seam in TupleValue}
            for ProjectionMask in range(1, 1 << len(SignalDomain)):
                SupportedProjections.add(frozenset(((Signal, SeamBySignal[Signal]) for Index, Signal in enumerate(SignalDomain) if ProjectionMask & 1 << Index)))
        return (frozenset(SignalDomain), frozenset(SupportedProjections))
    HigherOrderSeamSupportRelations = tuple((BuildHigherOrderSeamSupportRelation(Certificate) for Certificate in sorted(getattr(Resources, 'PhysicalComponentSymbolicHigherOrderCertificateCache', {}).values(), key=lambda Value: str(Value.DomainFingerprint)) if Certificate.Complete and str(Certificate.PreparedDomainFingerprint) == str(Preparation.DomainFingerprint)))
    def HasHigherOrderSeamSupport(Selected: tuple[PhysicalComponentPortReservation, ...]) -> bool:
        """Keep a partial seam tuple only if one certified tuple extends it."""
        SelectedSeams = frozenset(((str(Port.Signal), str(getattr(Port, 'CertifiedSeamContractFingerprint', '')) or BuildPhysicalPortSeamContractFingerprint(Port)) for Port in Selected))
        for SignalDomain, SupportedProjections in HigherOrderSeamSupportRelations:
            RestrictedTuple = frozenset(((Signal, Seam) for Signal, Seam in SelectedSeams if Signal in SignalDomain))
            if not RestrictedTuple:
                continue
            if RestrictedTuple in SupportedProjections:
                continue
            return False
        return True

    def BuildSeamOnlyPortDomainKey(Signal: str) -> tuple[str, tuple[str, str] | None, str | None, str | None, str | None]:
        """Identify every restriction that changes a seam-only domain."""
        return (Signal, ActiveApertureContractRestrictionsBySignal.get(Signal), ActiveLocalAccessRestrictionsBySignal.get(Signal), ActiveSeamRestrictionsBySignal.get(Signal), ActiveSupportRestrictionsBySignal.get(Signal))

    def SelectSeamOnlyPorts() -> bool:
        """Select exact seams without performing the local capacity proof.

        The frozen boundary and learned seam clauses own identity selection.
        Joint local claim compatibility belongs to the subsequent exact
        symbolic-capacity admission stage; solving it here would multiply the
        same local CSP across the global boundary product.
        """
        nonlocal SelectedPorts, PortAssignmentExpansionCount
        for Signal in OrderedSignals:
            Restriction = ActiveApertureContractRestrictionsBySignal.get(Signal)
            DomainKey = BuildSeamOnlyPortDomainKey(Signal)
            if DomainKey in SeamOnlyPortDomainsBySignal:
                continue
            ByFingerprint = {}
            LocalFactors = PreparedLocalFactorsBySignal.get(Signal, {})
            ApertureFactors = PreparedApertureFactorsBySignal.get(Signal, {})
            SeenSeamFingerprints: set[tuple[str, str]] = set()
            ConsideredSupportCount = 0
            MatchingApertureFingerprints = tuple((Fingerprint for Fingerprint, ApertureFactor in ApertureFactors.items() if Restriction is None or Restriction == (ApertureFactor.GlobalContractFingerprint, ApertureFactor.ApertureContractFingerprint)))
            SupportsForRestriction = tuple((Support for ApertureFingerprint in MatchingApertureFingerprints for Support in PreparedLocalApertureSupportsByOption.get((Signal, ApertureFingerprint), ())))
            for Support in SupportsForRestriction:
                ConsideredSupportCount += 1
                if ActiveLocalAccessRestrictionsBySignal.get(Signal) not in (None, str(Support.LocalAccessFingerprint)) or ActiveSupportRestrictionsBySignal.get(Signal) not in (None, str(Support.SupportFingerprint)):
                    continue
                LocalFactor = LocalFactors.get(str(Support.LocalAccessFingerprint))
                ApertureFactor = ApertureFactors.get(str(Support.ApertureOptionFingerprint))
                if LocalFactor is None or ApertureFactor is None:
                    continue
                if Restriction is not None and Restriction != (ApertureFactor.GlobalContractFingerprint, ApertureFactor.ApertureContractFingerprint):
                    continue
                SeamFingerprint = BuildPhysicalPortSeamContractFingerprint(LocalFactor)
                ActiveSeamFingerprint = (SeamFingerprint, str(Support.SupportFingerprint))
                SeamRestriction = ActiveSeamRestrictionsBySignal.get(Signal)
                if SeamRestriction not in (None, SeamFingerprint, ActiveSeamFingerprint):
                    continue
                if ActiveSeamFingerprint in SeenSeamFingerprints:
                    continue
                SeenSeamFingerprints.add(ActiveSeamFingerprint)
                Option = MaterializeSupportedPhysicalPortReservation(LocalFactor, ApertureFactor, Support, ResourceGraph)
                SeamOnly = BuildSeamOnlyPhysicalComponentPortReservation(Option, ResourceGraph)
                ByFingerprint.setdefault(SeamOnly.ReservationFingerprint, SeamOnly)
                if WorkCheck is not None and (len(ByFingerprint) == 1 or len(ByFingerprint) % 128 == 0):
                    WorkCheck({'Stage': 'physical-port-seam-domain-materialization', 'Signal': Signal, 'ConsideredSupportCount': ConsideredSupportCount, 'DistinctSeamCount': len(ByFingerprint), 'SeamOnly': True, 'ImplicitForeignTransitDomainCount': 0})
            if WorkCheck is not None:
                WorkCheck({'Stage': 'physical-port-seam-domain-complete', 'Signal': Signal, 'ConsideredSupportCount': ConsideredSupportCount, 'DistinctSeamCount': len(ByFingerprint), 'Restriction': list(Restriction or ()), 'SeamOnly': True, 'ImplicitForeignTransitDomainCount': 0})
            SeamOnlyPortDomainsBySignal[DomainKey] = tuple((ByFingerprint[Fingerprint] for Fingerprint in sorted(ByFingerprint)))
        Domains = {Signal: tuple((Option for Option in SeamOnlyPortDomainsBySignal[BuildSeamOnlyPortDomainKey(Signal)])) for Signal in OrderedSignals}
        if any((not Values for Values in Domains.values())):
            return False
        RejectedClauses = tuple((*Resources.RejectedPhysicalComponentPortReservationSets, *LocalSeamNoGoodClauses))

        def Search(Remaining: tuple[str, ...], Selected: tuple[PhysicalComponentPortReservation, ...], SelectedKeys: frozenset[tuple[str, str]]) -> bool:
            nonlocal SelectedPorts, PortAssignmentExpansionCount
            PortAssignmentExpansionCount += 1
            if WorkCheck is not None and (PortAssignmentExpansionCount == 1 or PortAssignmentExpansionCount % 128 == 0):
                WorkCheck({'Stage': 'physical-port-seam-selection', 'AssignedPortCount': len(Selected), 'PortCount': len(OrderedSignals), 'ExpansionCount': PortAssignmentExpansionCount, 'SeamOnly': True, 'ImplicitForeignTransitDomainCount': 0})
            if ReservationKeysContainRejectedPortClause(SelectedKeys):
                return False
            if not HasHigherOrderSeamSupport(Selected):
                return False
            if not Remaining:
                PortAssignmentFingerprint = BuildStableFingerprint(tuple(((Value.Signal, Value.ReservationFingerprint) for Value in sorted(Selected, key=lambda Candidate: Candidate.Signal))))
                if PortAssignmentFingerprint in Resources.RejectedPhysicalComponentPortAssignmentFingerprints or PortAssignmentFingerprint in getattr(Resources, 'DeferredPhysicalComponentPortAssignmentFingerprints', set()):
                    return False
                SelectedPorts = Selected
                return True
            CompatibleDomains = {Signal: tuple((Option for Option in Domains[Signal] if not ReservationKeysContainRejectedPortClause(SelectedKeys | PhysicalPortNoGoodKeys(Option)))) for Signal in Remaining}
            if any((not Values for Values in CompatibleDomains.values())):
                return False
            Signal = SelectPhysicalFactorBranchSignal({Value: len(CompatibleDomains[Value]) for Value in Remaining}, RejectedClauses)
            NextRemaining = tuple((Value for Value in Remaining if Value != Signal))
            for Option in CompatibleDomains[Signal]:
                if Search(NextRemaining, (*Selected, Option), SelectedKeys | PhysicalPortNoGoodKeys(Option)):
                    return True
            return False
        return Search(tuple(OrderedSignals), (), frozenset())

    def SelectNextGloballyPlannedBoundary() -> bool:
        """Freeze one global tuple before any joint local compilation."""
        nonlocal ActiveApertureContractRestrictionsBySignal
        nonlocal ActiveLocalAccessRestrictionsBySignal
        nonlocal ActiveSeamRestrictionsBySignal
        nonlocal ActiveSupportRestrictionsBySignal
        nonlocal ApertureSelectionExpansionCount
        nonlocal SelectedBoundaryPorts, SelectedPorts
        nonlocal SelectedBoundaryAssignmentFingerprint
        BoundaryClauseSkipCount = 0
        while True:
            BoundarySelection = next(BoundaryAssignmentIterator, None)
            if BoundarySelection is None:
                ActiveApertureContractRestrictionsBySignal = {}
                ActiveLocalAccessRestrictionsBySignal = {}
                ActiveSeamRestrictionsBySignal = {}
                ActiveSupportRestrictionsBySignal = {}
                SelectedBoundaryPorts = None
                SelectedPorts = None
                return False
            if isinstance(BoundarySelection, ComponentInterfaceContract):
                BoundaryPorts = BoundarySelection.SelectedBoundaryPorts
                ActiveLocalAccessRestrictionsBySignal = dict(BoundarySelection.SelectedLocalAccessFingerprints)
                BaseSeamRestrictionsBySignal = dict(BoundarySelection.SelectedSeamContractFingerprints)
                ActiveSupportRestrictionsBySignal = dict(BoundarySelection.SelectedLocalSupportFingerprints)
            else:
                BoundaryPorts = BoundarySelection
                ActiveLocalAccessRestrictionsBySignal = {}
                BaseSeamRestrictionsBySignal = {}
                ActiveSupportRestrictionsBySignal = {}
            ApertureSelectionExpansionCount += 1
            SelectedBoundaryPorts = BoundaryPorts
            ActiveApertureContractRestrictionsBySignal = {Value.Signal: (Value.GlobalContractFingerprint, Value.ApertureContractFingerprint) for Value in BoundaryPorts}
            BoundaryAssignmentFingerprint = BuildPhysicalBoundaryPortAssignmentFingerprint(BoundaryPorts)
            SelectedBoundaryAssignmentFingerprint = BoundaryAssignmentFingerprint
            BoundaryKeys = frozenset((*((Value.Signal, Value.GlobalContractFingerprint) for Value in BoundaryPorts), *((Value.Signal, Value.ApertureContractFingerprint) for Value in BoundaryPorts), *((Signal, 'local-signal-domain:' + PortSolverCacheKey) for Signal in OrderedSignals)))
            RejectedByGlobalClause = any((Clause and Clause <= BoundaryKeys for Clause in Resources.RejectedPhysicalComponentPortReservationSets))
            RejectedBySingleAperture = any((Value.ApertureContractFingerprint in Resources.RejectedPhysicalComponentPortReservationsBySignal.get(Value.Signal, ()) for Value in BoundaryPorts))
            if BoundaryAssignmentFingerprint in RejectedBoundaryAssignmentFingerprints or RejectedByGlobalClause or RejectedBySingleAperture:
                BoundaryClauseSkipCount += 1
                continue
            SeamRestrictionPasses = BuildCapacityRepairSeamRestrictionPasses(
                BaseSeamRestrictionsBySignal,
                PreferredCapacityRepairSeamsBySignal,
            )
            for RestrictionPassIndex, SeamRestrictions in enumerate(SeamRestrictionPasses):
                ActiveSeamRestrictionsBySignal = SeamRestrictions
                RestrictionState = (tuple(sorted(ActiveApertureContractRestrictionsBySignal.items())), tuple(sorted(ActiveLocalAccessRestrictionsBySignal.items())), tuple(sorted(ActiveSeamRestrictionsBySignal.items())), tuple(sorted(ActiveSupportRestrictionsBySignal.items())))
                if WorkCheck is not None:
                    WorkCheck({'Stage': 'physical-port-global-boundary-selected', 'BoundaryAssignmentFingerprint': BoundaryAssignmentFingerprint, 'BoundaryAssignmentCount': ApertureSelectionExpansionCount, 'BoundaryClauseSkipCount': BoundaryClauseSkipCount, 'PersistentBoundaryFrontierReused': BoundaryAssignmentIteratorReused, 'BoundaryFrontierFingerprint': BoundaryIteratorCacheKey, 'SelectedApertureContracts': {Signal: list(Value) for Signal, Value in sorted(ActiveApertureContractRestrictionsBySignal.items())}, 'SelectedLocalAccessContracts': dict(sorted(ActiveLocalAccessRestrictionsBySignal.items())), 'SelectedSeamContracts': dict(sorted(ActiveSeamRestrictionsBySignal.items())), 'CapacityRepairSeamWitnessGuided': bool(RestrictionPassIndex == 0 and len(SeamRestrictionPasses) > 1), 'CapacityRepairSeamWitnessFallback': bool(RestrictionPassIndex > 0), 'GlobalBoundaryPlanningComplete': True, 'LocalCompositePlanningStarted': False, 'ImplicitForeignTransitDomainCount': 0})
                SelectedPorts = None
                if DeferLocalCompositeSelection:
                    while RestrictionState not in FailedApertureRestrictionStates and SelectSeamOnlyPorts():
                        assert SelectedPorts is not None
                        SelectedPorts = tuple(sorted(SelectedPorts, key=lambda Value: Value.Signal))
                        return True
                    SelectedPorts = None
                elif RestrictionState not in FailedApertureRestrictionStates and (SelectFactorizedPorts(OrderedSignals, ()) or SelectPorts(OrderedSignals, ())):
                    return True
                FailedApertureRestrictionStates.add(RestrictionState)
            RejectedBoundaryAssignmentFingerprints.add(BoundaryAssignmentFingerprint)
    ActiveApertureContractRestrictionsBySignal = {}
    if not SelectNextGloballyPlannedBoundary():
        DeferredAssignments = frozenset(getattr(Resources, 'DeferredPhysicalComponentPortAssignmentFingerprints', set()))
        if DeferredAssignments:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalPortAssignmentDeferredDomainExhausted', AffectedNets=tuple(OrderedSignals), Detail='all currently eligible physical port assignments are retained on the proof-neutral global-plan frontier', Diagnostics={'DeferredPortAssignmentDomainExhausted': True, 'DeferredPortAssignmentFingerprints': sorted(DeferredAssignments), 'GlobalPlanDomainComplete': False, 'CompleteAssignmentCutProof': False, 'PortAssignmentProofComplete': False, 'OwnershipSearchComplete': False, 'ImplicitForeignTransitDomainCount': 0}))
        RejectedNoGoodClauses = (*Resources.RejectedPhysicalComponentPortReservationSets, *LocalSeamNoGoodClauses, *CertifiedLocalPairNoGoodClauses)
        UniversalFactorKeysBySignal = {}
        for Signal in OrderedSignals:
            FabricFingerprints = frozenset((Factor.FabricDomainFingerprint for Factor in LaneFactorsBySignal[Signal]))
            Keys = {(Signal, 'local-signal-domain:' + PortSolverCacheKey)}
            if len(FabricFingerprints) == 1:
                FabricFingerprint = next(iter(FabricFingerprints))
                Keys.update(((Signal, 'fabric-domain:' + FabricFingerprint), (Signal, 'local-factor-domain:' + PortSolverCacheKey + ':' + FabricFingerprint)))
            UniversalFactorKeysBySignal[Signal] = frozenset(Keys)
        FactorDirectUnsatCore = FindProofQualifiedUniversalNoGoodCore(UniversalFactorKeysBySignal, RejectedNoGoodClauses)
        MaterializedDirectUnsatCore = FindProofQualifiedCompleteDomainNoGoodCore(CompletePortOptionDomains, RejectedNoGoodClauses, PhysicalPortNoGoodKeys) if CompletePortOptionDomains is not None else None
        DirectUnsatCore = FactorDirectUnsatCore or MaterializedDirectUnsatCore
        DirectUnsatCoreBasis = 'complete-factor-domain-no-good' if FactorDirectUnsatCore is not None else 'complete-option-domain-no-good' if MaterializedDirectUnsatCore is not None else ''
        DirectUnsatCoreReused = bool(DirectUnsatCore is not None and len(DirectUnsatCore[0]) <= 2)
        DirectUnsatCoreClause = DirectUnsatCore[1] if DirectUnsatCoreReused else frozenset()
        BoundaryPairUnsatCore: tuple[str, ...] = ()
        BoundaryPairUnsatCoreCheckCount = 0
        BoundaryTripleUnsatCore: tuple[str, ...] = ()
        BoundaryTripleUnsatCoreCheckCount = 0
        if ApertureSelectionExpansionCount == 0:
            (
                BoundaryPairUnsatCore,
                BoundaryPairUnsatCoreCheckCount,
                BoundaryTripleUnsatCore,
                BoundaryTripleUnsatCoreCheckCount,
            ) = FindCompleteBoundaryAssignmentUnsatCore(
                Preparation,
                Resources,
                BoundaryPortDomainsBySignal,
                OrderedSignals,
                CertifiedLocalPairNoGoodClauses,
                LocalSeamNoGoodClauses,
                PortSolverCacheKey,
                CheckComponentPlannerWork,
                DeferLocalCompositeSelection,
            )
        BoundaryUnsatCore = BoundaryPairUnsatCore or BoundaryTripleUnsatCore
        PortAssignmentUnsatCore = list(BoundaryUnsatCore or (DirectUnsatCore[0] if DirectUnsatCoreReused else OrderedSignals))
        PortAssignmentUnsatCoreCheckCount = BoundaryPairUnsatCoreCheckCount + BoundaryTripleUnsatCoreCheckCount
        PortAssignmentDeletionCoreComplete = False
        PriorCoreRevalidated = False
        MaximumDeletionCoreSignals = 12
        PriorCore = SelectRevalidatablePriorPortAssignmentCore(
            getattr(
                Resources,
                'PreferredPhysicalComponentPortUnsatCoreSignals',
                (),
            ),
            OrderedSignals,
        )
        if (
            not BoundaryUnsatCore
            and not DirectUnsatCoreReused
            and PriorCore
        ):
            PortAssignmentUnsatCoreCheckCount += 1
            CheckComponentPlannerWork({
                'Stage': 'physical-port-prior-factor-core-revalidation',
                'PortAssignmentUnsatCoreCheckCount': (
                    PortAssignmentUnsatCoreCheckCount
                ),
                'CandidateCoreSize': len(PriorCore),
            })
            SelectedPorts = None
            ActiveApertureContractRestrictionsBySignal = {}
            if not SelectFactorizedPorts(PriorCore, ()):
                PortAssignmentUnsatCore = list(PriorCore)
                PriorCoreRevalidated = True
        if not BoundaryUnsatCore and not DirectUnsatCoreReused and len(PortAssignmentUnsatCore) <= MaximumDeletionCoreSignals:
            for Signal in tuple(PortAssignmentUnsatCore):
                CandidateCore = tuple((Value for Value in PortAssignmentUnsatCore if Value != Signal))
                if not CandidateCore:
                    continue
                PortAssignmentUnsatCoreCheckCount += 1
                CheckComponentPlannerWork({
                    'Stage': 'physical-port-factor-core-extraction',
                    'PortAssignmentUnsatCoreCheckCount': PortAssignmentUnsatCoreCheckCount,
                    'CandidateCoreSize': len(CandidateCore),
                    'RemovedSignal': Signal,
                })
                SelectedPorts = None
                ActiveApertureContractRestrictionsBySignal = {}
                if not SelectFactorizedPorts(CandidateCore, ()):
                    PortAssignmentUnsatCore = list(CandidateCore)
            PortAssignmentDeletionCoreComplete = True
        PortAssignmentUnsatCoreSignals = tuple(sorted(PortAssignmentUnsatCore))
        PortAssignmentUnsatCoreFingerprint = BuildStableFingerprint(('physical-port-assignment-unsat-core', PortAssignmentUnsatCoreSignals))
        PortDomainGenerationCompleteBySignal = {Signal: all(((Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint) in ExhaustedPortSeamKeys for LaneFactor in LaneFactorsBySignal[Signal] for Seam in LaneFactor.Seams)) for Signal in OrderedSignals}
        PortOptionMaterializationComplete = all(PortDomainGenerationCompleteBySignal.values())
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalComponentAssemblyPlanning', AffectedNets=PortAssignmentUnsatCoreSignals, Detail='complete boundary and factor search proves that no capacity-one component port assignment exists', Diagnostics={'BoundaryPortDomainSizes': {Signal: len(BoundaryPortDomainsBySignal[Signal]) for Signal in OrderedSignals}, 'BoundaryAssignmentExpansionCount': ApertureSelectionExpansionCount, 'BoundaryPairUnsatCoreCheckCount': BoundaryPairUnsatCoreCheckCount, 'BoundaryTripleUnsatCoreCheckCount': BoundaryTripleUnsatCoreCheckCount, 'PortDomainSizes': {Signal: sum((len(GeneratedPortOptionsBySeam[Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint]) for LaneFactor in LaneFactorsBySignal[Signal] for Seam in LaneFactor.Seams)) for Signal in OrderedSignals}, 'PortDomainGenerationComplete': PortDomainGenerationCompleteBySignal, 'PortDomainGenerationStatus': {Signal: 'complete' if all(((Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint) in ExhaustedPortSeamKeys for LaneFactor in LaneFactorsBySignal[Signal] for Seam in LaneFactor.Seams)) else 'partial' if any(((Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint) in ExhaustedPortSeamKeys or GeneratedPortOptionsBySeam[Signal, LaneFactor.FabricDomainFingerprint, Seam.SeamFingerprint] for LaneFactor in LaneFactorsBySignal[Signal] for Seam in LaneFactor.Seams)) else 'unvisited' for Signal in OrderedSignals}, 'PortFactorDomainSizes': {Signal: sum((len(LaneFactor.Seams) * prod((len(Candidates) for Candidates in LaneFactor.CandidateDomains)) for LaneFactor in LaneFactorsBySignal[Signal])) for Signal in OrderedSignals}, 'PortOptionGenerationCounts': dict(PortOptionGenerationCountBySignal), 'PortOptionSelfClaimPruneCount': PortOptionSelfClaimPruneCount, 'LaneFactorExpansionCount': LaneFactorExpansionCount, 'AccessFactorExpansionCount': AccessFactorExpansionCount, 'AccessFactorExpansionCountBySignal': dict(AccessFactorExpansionCountBySignal), 'AccessAssignmentSelfConflictCountBySignal': dict(AccessAssignmentSelfConflictCountBySignal), 'AccessAssignmentSelfConflictSamplesBySignal': AccessAssignmentSelfConflictSamplesBySignal, 'CompleteAccessAssignmentCountBySignal': dict(CompleteAccessAssignmentCountBySignal), 'LocalRealizabilityCheckCountBySignal': dict(LocalRealizabilityCheckCountBySignal), 'LocalRealizabilityRejectedSeamCountBySignal': dict(LocalRealizabilityRejectedSeamCountBySignal), 'LocalRealizabilityRejectionCountsBySignal': {Signal: dict(Counts) for Signal, Counts in sorted(LocalRealizabilityRejectionCountsBySignal.items())}, 'SeamFactorExpansionCount': SeamFactorExpansionCount, 'FactorDomainPropagationCount': FactorDomainPropagationCount, 'FactorDomainPruneCount': FactorDomainPruneCount, 'ForwardSupportCheckCount': ForwardSupportCheckCount, 'ForwardSupportCacheHitCount': ForwardSupportCacheHitCount, 'ForwardSupportWitnessHitCount': ForwardSupportWitnessHitCount, 'LaneArcConsistencyCheckCount': LaneArcConsistencyCheckCount, 'LaneArcConsistencyCacheHitCount': LaneArcConsistencyCacheHitCount, 'FactorArcClosureCount': FactorArcClosureCount, 'FactorArcClosureCacheHitCount': FactorArcClosureCacheHitCount, 'LaneArcSupportIntersectionCount': LaneArcSupportIntersectionCount, 'SpeculativeLocalCompilationCheckCount': 0, 'GuideLayerBySignal': {Signal: int(CoarsePlan.Layers.get(Signal, 0)) for Signal in OrderedSignals}, 'PortAssignmentExpansionCount': PortAssignmentExpansionCount, 'FactorizedPortSearch': True, 'PreparedApertureFactorDomainReused': True, 'PortAssignmentUnsatCoreSignals': list(PortAssignmentUnsatCoreSignals), 'PortAssignmentUnsatCoreFingerprint': PortAssignmentUnsatCoreFingerprint, 'PortAssignmentUnsatCoreCheckCount': PortAssignmentUnsatCoreCheckCount, 'PortAssignmentUnsatCoreMinimal': bool(BoundaryUnsatCore) or DirectUnsatCoreReused or PortAssignmentDeletionCoreComplete, 'PortAssignmentUnsatCoreProofBasis': 'complete-boundary-pair-domain' if BoundaryPairUnsatCore else 'complete-boundary-triple-domain' if BoundaryTripleUnsatCore else DirectUnsatCoreBasis if DirectUnsatCoreReused else 'deletion-check' if PortAssignmentDeletionCoreComplete else 'complete-signal-domain', 'PortAssignmentUnsatCoreDirectReuse': DirectUnsatCoreReused, 'PortAssignmentPriorCoreSignals': list(PriorCore), 'PortAssignmentPriorCoreRevalidated': PriorCoreRevalidated, 'PortAssignmentUnsatCoreNoGoodKeys': [list(Key) for Key in sorted(DirectUnsatCoreClause)], 'FailedPortAssignmentStateCount': len(FailedPortAssignmentStates), 'PersistentPortCspStateReused': PersistentPortCspStateReused, 'ComponentFabricConstructionComplete': True, 'PortOptionMaterializationComplete': PortOptionMaterializationComplete, 'PortAssignmentProofComplete': True, 'PortAssignmentUnsatProofBasis': 'complete-boundary-pair-domain' if BoundaryPairUnsatCore else 'complete-boundary-triple-domain' if BoundaryTripleUnsatCore else 'exhaustive-option-domain' if PortOptionMaterializationComplete else 'complete-factor-search' if ApertureSelectionExpansionCount else 'complete-boundary-domain', 'OwnershipSearchComplete': True, 'ImplicitForeignTransitDomainCount': 0}))

    def FinalizePortFirstGlobalChannels(Ports: tuple[PhysicalComponentPortReservation, ...]) -> tuple[Any, tuple[PhysicalComponentPortReservation, ...], tuple[PhysicalComponentChannelReservation, ...], ComponentRoutingProblem]:
        Ports = tuple((BuildSeamOnlyPhysicalComponentPortReservation(Port, ResourceGraph) for Port in Ports))
        CandidateProblem = Problem
        if WorkCheck is not None:
            WorkCheck({'Stage': 'physical-port-plan-selected', 'PortCount': len(Ports), 'FactorizedPortSearch': True, 'PreparedApertureFactorDomainReused': True, 'ApertureSelectionExpansionCount': ApertureSelectionExpansionCount, 'SelectedApertureContracts': {Signal: list(Value) for Signal, Value in sorted(ActiveApertureContractRestrictionsBySignal.items())}, 'PortAssignmentExpansionCount': PortAssignmentExpansionCount, 'SeamFactorExpansionCount': SeamFactorExpansionCount, 'PortOptionGenerationCounts': dict(PortOptionGenerationCountBySignal), 'AccessFactorExpansionCount': AccessFactorExpansionCount, 'LocalRealizabilityCheckCountBySignal': dict(LocalRealizabilityCheckCountBySignal), 'LocalRealizabilityRejectedSeamCountBySignal': dict(LocalRealizabilityRejectedSeamCountBySignal), 'SpeculativeLocalCompilationCheckCount': 0, 'PortSolverCacheKey': PortSolverCacheKey, 'CertifiedLocalPortPairNoGoodCount': len(CertifiedLocalPairNoGoodClauses), 'PersistentPortCspStateReused': PersistentPortCspStateReused, 'FailedPortAssignmentStateCount': len(FailedPortAssignmentStates), 'CompletePortOptionDomainCacheHit': bool(CachedPortOptionDomains is not None), 'PortOptionArcSupportCacheHit': bool(CachedPortOptionArcSupport is not None), 'LaneArcSupportCacheHit': bool(CachedLaneArcSupport is not None), 'PortClaimCompatibilityCacheEntryCount': len(PortClaimCompatibilityCache), 'PersistentPortClaimCompatibilityEntryCountAtStart': PersistentPortClaimCompatibilityEntryCountAtStart, 'PhysicalPortContractClaimsCacheEntryCount': len(PhysicalPortContractClaimsCache), 'PersistentPortContractClaimsEntryCountAtStart': PersistentPortContractClaimsEntryCountAtStart, 'PhysicalPortNoGoodKeyCacheEntryCount': len(PhysicalPortNoGoodKeyCache), 'PersistentPortNoGoodKeyEntryCountAtStart': PersistentPortNoGoodKeyEntryCountAtStart, 'PortOptionDomainMaterializationCount': PortOptionDomainMaterializationCount, 'PortOptionArcConsistencyCheckCount': PortOptionArcConsistencyCheckCount, 'PortOptionArcConsistencyIntersectionCount': PortOptionArcConsistencyIntersectionCount, 'PortOptionDomainPropagationCount': PortOptionDomainPropagationCount, 'PortOptionDomainPruneCount': PortOptionDomainPruneCount, 'PersistentPortOptionPropagationCacheHitCount': PersistentPortOptionPropagationCacheHitCount, 'IncrementalPortOptionPropagationReuseCount': IncrementalPortOptionPropagationReuseCount, 'PersistentPortOptionPropagationStateCountAtStart': PersistentPortOptionPropagationStateCountAtStart, 'FactorDomainPropagationCount': FactorDomainPropagationCount, 'FactorDomainPruneCount': FactorDomainPruneCount, 'FactorArcClosureCount': FactorArcClosureCount, 'FactorArcClosureCacheHitCount': FactorArcClosureCacheHitCount, 'LaneArcSupportIntersectionCount': LaneArcSupportIntersectionCount, 'ForwardSupportCheckCount': ForwardSupportCheckCount, 'ForwardSupportCacheHitCount': ForwardSupportCacheHitCount})
        CandidateCoarsePlan = Preparation.CoarsePlan
        PortSignals = frozenset((Port.Signal for Port in Ports))
        KeepoutClaims = ResourceGraph.BuildRouteClaims(ComponentKeepoutNodes)
        KeepoutGuideCellsByLayer = dict(Preparation.ComponentKeepoutGuideCellsByLayer)
        ReservedPortGuideCells = frozenset(((Position[0], Position[2]) for Port in Ports for Position in Port.GlobalPath))
        PortClearance = max(1, int(getattr(ResourceGraph.Technology, 'TrackPitch', DefaultRedstoneRoutingTechnology.TrackPitch)))
        ReservedPortAccessGuideCells = frozenset(((X + DeltaX, Z + DeltaZ) for X, Z in ReservedPortGuideCells for DeltaX in range(-PortClearance, PortClearance + 1) for DeltaZ in range(-PortClearance, PortClearance + 1) if abs(DeltaX) + abs(DeltaZ) <= PortClearance))
        PreparedFeedthroughDomainsBySignal = {Domain.Signal: Domain for Domain in Preparation.FeedthroughEndpointDomains}
        if PreparedFeedthroughDomainsBySignal:
            RetainedFeedthroughs = tuple((Value for Value in CandidateProblem.Interface.Feedthroughs if Value.Signal not in PreparedFeedthroughDomainsBySignal))
            CandidateProblem = replace(CandidateProblem, Interface=replace(CandidateProblem.Interface, Feedthroughs=RetainedFeedthroughs), ForeignTransitDomains=tuple((Domain for Domain in CandidateProblem.ForeignTransitDomains if Domain.Signal not in PreparedFeedthroughDomainsBySignal)))
            for Signal, EndpointDomain in sorted(PreparedFeedthroughDomainsBySignal.items()):
                RetainedEndpointCandidates = tuple((Candidate for Candidate in EndpointDomain.Candidates if not any(((Node[0], Node[2]) in ReservedPortAccessGuideCells for Node in Candidate.ReservedPathNodes))))
                if RetainedEndpointCandidates:
                    continue
                raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable, Stage='PhysicalComponentAssemblyPlanning', AffectedNets=(Signal,), Detail='the selected physical ports block every candidate in the complete feedthrough endpoint domain', Diagnostics={'Signal': Signal, 'FeedthroughEndpointDomainFingerprint': EndpointDomain.DomainFingerprint, 'FeedthroughEndpointCandidateCount': len(EndpointDomain.Candidates), 'FeedthroughEndpointPrescreenRejectedCandidateCount': len(EndpointDomain.Candidates), 'FeedthroughEndpointPrescreenComplete': True, 'ComponentFabricConstructionComplete': True, 'OwnershipSearchComplete': True, 'ImplicitForeignTransitDomainCount': 0}))
        CandidateChannels = []
        for Channel in Preparation.ChannelReservations:
            Layer = int(CandidateCoarsePlan.Layers.get(Channel.Signal, Channel.Layer))
            GuideCells = tuple(sorted(CandidateCoarsePlan.Guides.get(Channel.Signal, Channel.GuideCells)))
            RoutingY = ResourceGraph.Technology.RoutingY(Preparation.MinimumPlacementY, Layer)
            GuideNodes = frozenset(((int(X), RoutingY, int(Z)) for X, Z in GuideCells if Channel.Signal not in PortSignals and (int(X), int(Z)) not in KeepoutGuideCellsByLayer.get(Layer, frozenset())))
            Claims = ResourceGraph.BuildRouteClaims(GuideNodes)
            ResourceIds = tuple(map(str, sorted(Claims.ResourceIds, key=str)))
            CandidateChannels.append(replace(Channel, Layer=Layer, GuideCells=GuideCells, ResourceIds=ResourceIds, Claims=Claims, ReservationFingerprint=BuildStableFingerprint(('authoritative-assembly-channel-v1', Channel.Signal, Layer, GuideCells, ResourceIds, Channel.Capacity, Channel.FeedthroughComponentIds))))
        CandidateChannels = tuple(CandidateChannels)
        return (CandidateCoarsePlan, Ports, CandidateChannels, CandidateProblem)
    ChannelPlanRejectionCount = 0
    ExactAssemblyChoiceRejectionCount = 0
    ExplicitFeedthroughEndpointDomainCache = {str(Domain.Signal): Domain for Domain in Preparation.FeedthroughEndpointDomains}

    def GetPreparedFeedthroughEndpointDomain(Signal: str, Layer: int) -> PreparedPhysicalComponentFeedthroughEndpointDomain:
        Cached = ExplicitFeedthroughEndpointDomainCache.get(str(Signal))
        if Cached is not None:
            return Cached
        Cached = PreparePhysicalComponentFeedthroughEndpointDomain(str(Signal), int(Layer), FabricNodes=frozenset(Problem.Fabric.Nodes), FabricEdges=frozenset(Problem.Fabric.Edges), FabricIngressNodes=frozenset(Problem.Fabric.IngressNodes), FabricFingerprint=BuildStableFingerprint((tuple(sorted(Problem.Fabric.Nodes)), tuple(sorted(Problem.Fabric.Edges)))), ResourceGraph=ResourceGraph, MinimumPlacementY=Preparation.MinimumPlacementY, WorkCheck=WorkCheck)
        ExplicitFeedthroughEndpointDomainCache[str(Signal)] = Cached
        return Cached

    def MinimizeExteriorGuideDetourNoGood(Failure: RoutingFailure, BoundaryPorts: tuple[PhysicalComponentBoundaryPortReservation, ...]) -> tuple[frozenset[tuple[str, str]], int]:
        """Return the smallest complete aperture cut for a guide detour.

        The exterior guide checker is monotone in reserved port-access cells:
        adding an aperture can only remove detour nodes.  Deletion therefore
        produces a complete irreducible core without enumerating assignments.
        A two-component cut remains repairable by the explicit feedthrough
        stage and is not a no-good witness here.
        """
        if not BoundaryPorts:
            return (frozenset(), 0)
        ExpectedSignal = str(Failure.Diagnostics.get('Signal', ''))
        ExpectedDetail = str(Failure.Detail)
        CheckCount = 0

        def HasSameCompleteFailure(Ports: tuple[PhysicalComponentBoundaryPortReservation, ...]) -> bool:
            nonlocal CheckCount
            CheckCount += 1
            ReservedCells = frozenset(((Position[0], Position[2]) for Port in Ports for Position in Port.GlobalPath))
            try:
                BuildComponentKeepoutAvoidingGlobalGuides(Preparation.CoarsePlan, ComponentPortSignals=frozenset(OrderedSignals), EnvelopeMinimum=Preparation.ComponentEnvelopeMinimum, EnvelopeMaximum=Preparation.ComponentEnvelopeMaximum, TrackPitch=max(1, int(getattr(ResourceGraph.Technology, 'TrackPitch', DefaultRedstoneRoutingTechnology.TrackPitch))), ReservedPortGuideCells=ReservedCells, ComponentKeepoutGuideCellsByLayer=dict(Preparation.ComponentKeepoutGuideCellsByLayer), DeclaredFeedthroughSignals=Preparation.Problem.Interface.DeclaredFeedthroughSignals, WorkCheck=WorkCheck)
            except RoutingStageError as CandidateError:
                CandidateFailure = CandidateError.Failure
                CandidateSignal = str(CandidateFailure.Diagnostics.get('Signal', ''))
                RepairableExteriorCut = bool(int(CandidateFailure.Diagnostics.get('ExteriorGuideComponentCount', 0)) >= 2 and CandidateSignal and (CandidateSignal not in Preparation.Problem.Interface.DeclaredFeedthroughSignals))
                return bool(not RepairableExteriorCut and (not ExpectedSignal or CandidateSignal == ExpectedSignal) and (not ExpectedDetail or str(CandidateFailure.Detail) == ExpectedDetail))
            return False
        Core = list(sorted(BoundaryPorts, key=lambda Value: Value.Signal))
        if not HasSameCompleteFailure(tuple(Core)):
            return (frozenset(), CheckCount)
        for Port in tuple(Core):
            CandidateCore = tuple((Value for Value in Core if Value.Signal != Port.Signal))
            if CandidateCore and HasSameCompleteFailure(CandidateCore):
                Core = list(CandidateCore)
        return (frozenset(((str(Port.Signal), str(Port.ApertureContractFingerprint)) for Port in Core)), CheckCount)

    ExplicitFeedthroughMinimizationDiagnostics: dict[str, object] = {}
    ExplicitFeedthroughFeasibilityCache: dict[tuple[str, str, tuple[tuple[str, str], ...]], bool] = {}
    CompiledExplicitFeedthroughBinaryRows: set[tuple[str, str, str, str]] = set()

    ExplicitFeedthroughMinimizationContext = (
        BuildExplicitFeedthroughMinimizationContext(locals())
    )
    while True:
        assert SelectedPorts is not None
        SelectedPorts = tuple(sorted(SelectedPorts, key=lambda Value: Value.Signal))
        try:
            CoarsePlan, SelectedPorts, ChannelReservations, CandidateProblem = FinalizePortFirstGlobalChannels(SelectedPorts)
            AssemblyChoiceFingerprint = BuildPhysicalComponentAssemblyChoiceFingerprint(SimpleNamespace(PlacementFingerprint=CandidateProblem.PlacementFingerprint, ComponentGraphFingerprint=Preparation.ComponentGraphFingerprint, ResourceGraphFingerprint=Preparation.ResourceGraphFingerprint, TechnologyFingerprint=BuildStableFingerprint(repr(getattr(ResourceGraph, 'Technology', None))), Ports=SelectedPorts, Feedthroughs=CandidateProblem.Interface.Feedthroughs))
            if AssemblyChoiceFingerprint in getattr(Resources, 'RejectedPhysicalComponentAssemblyChoiceFingerprints', set()):
                ExactAssemblyChoiceRejectionCount += 1
                if SelectedBoundaryPorts is not None and (not IsRequiredBoundaryFixed):
                    RejectedBoundaryAssignmentFingerprints.add(BuildPhysicalBoundaryPortAssignmentFingerprint(SelectedBoundaryPorts))
                SelectedPorts = None
                ActiveApertureContractRestrictionsBySignal = {}
                if not SelectNextGloballyPlannedBoundary():
                    raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable, Stage='PhysicalComponentAssemblyPlanning', AffectedNets=tuple(OrderedSignals), Detail='the complete physical assembly-choice domain is rejected by authoritative global proofs', Diagnostics={'GlobalPlanDomainComplete': True, 'CompleteAssignmentCutProof': True, 'PortAssignmentProofComplete': True, 'ComponentFabricConstructionComplete': True, 'OwnershipSearchComplete': True, 'ExactAssemblyChoiceRejectionCount': ExactAssemblyChoiceRejectionCount, 'RejectedAssemblyChoiceFingerprint': AssemblyChoiceFingerprint, 'ImplicitForeignTransitDomainCount': 0}))
                continue
            Problem = CandidateProblem
            break
        except RoutingStageError as Error:
            if Error.Failure.Reason != RoutingFailureReason.ComponentChannelCapacityUnsatisfiable:
                raise
            ChannelPlanRejectionCount += 1
            ExteriorGuideDetourNoGood = frozenset()
            ExteriorGuideDetourCoreCheckCount = 0
            if SelectedBoundaryPorts is not None and str(Error.Failure.Detail) in {'a foreign global guide cannot bypass the reserved component port-access domain', 'a foreign global guide has no exterior segment after applying component port and keepout reservations'}:
                ExteriorGuideDetourNoGood, ExteriorGuideDetourCoreCheckCount = MinimizeExteriorGuideDetourNoGood(Error.Failure, SelectedBoundaryPorts)
                if ExteriorGuideDetourNoGood:
                    LocalSeamNoGoodClauses.add(ExteriorGuideDetourNoGood)
            if SelectedBoundaryPorts is not None and (not ExteriorGuideDetourNoGood):
                ExteriorGuideDetourNoGood, ExteriorGuideDetourCoreCheckCount = BuildFeedthroughEndpointPrescreenNoGood(Error.Failure, SelectedBoundaryPorts, Preparation, ResourceGraph)
                if ExteriorGuideDetourNoGood:
                    LocalSeamNoGoodClauses.add(ExteriorGuideDetourNoGood)
            if SelectedBoundaryPorts is not None and (not ExteriorGuideDetourNoGood):
                ExteriorGuideDetourNoGood, ExteriorGuideDetourCoreCheckCount = MinimizeExplicitFeedthroughNoGood(
                    Error.Failure,
                    SelectedBoundaryPorts,
                    ExplicitFeedthroughMinimizationContext,
                )
                if ExteriorGuideDetourNoGood:
                    LocalSeamNoGoodClauses.add(ExteriorGuideDetourNoGood)
            ComponentPlannerLiveDiagnostics.update({'PriorChannelPlanRejectionCount': ChannelPlanRejectionCount, 'PriorChannelPlanFailureDetail': str(Error.Failure.Detail), 'PriorChannelPlanFailureAffectedNets': list(Error.Failure.AffectedNets), 'PriorChannelPlanFailureDiagnostics': dict(Error.Failure.Diagnostics or {}), 'PriorChannelPlanLearnedCore': [list(Key) for Key in sorted(ExteriorGuideDetourNoGood)], 'PriorChannelPlanCoreCheckCount': ExteriorGuideDetourCoreCheckCount, 'PriorChannelPlanCoreCheckDiagnostics': dict(ExplicitFeedthroughMinimizationDiagnostics)})
            RejectedFingerprint = BuildStableFingerprint(tuple(((Port.Signal, Port.ReservationFingerprint) for Port in SelectedPorts)))
            Resources.RejectedPhysicalComponentPortAssignmentFingerprints.add(RejectedFingerprint)
            LocalSeamNoGoodClauses.add(frozenset(((Port.Signal, Port.ReservationFingerprint) for Port in SelectedPorts)))
            if SelectedBoundaryPorts is not None and (not IsRequiredBoundaryFixed):
                RejectedBoundaryAssignmentFingerprints.add(BuildPhysicalBoundaryPortAssignmentFingerprint(SelectedBoundaryPorts))
            SelectedPorts = None
            ActiveApertureContractRestrictionsBySignal = {}
            if not SelectNextGloballyPlannedBoundary():
                CutSignals = tuple(sorted({*OrderedSignals, *Error.Failure.AffectedNets}))
                Diagnostics = dict(Error.Failure.Diagnostics or {})
                CutFingerprint = BuildStableFingerprint(('physical-feedthrough-port-domain-cut-v1', CutSignals, tuple(sorted(RejectedBoundaryAssignmentFingerprints)), tuple(sorted(Resources.RejectedPhysicalComponentPortAssignmentFingerprints))))
                raise RoutingStageError(replace(Error.Failure, AffectedNets=CutSignals, Detail='the complete physical port and feedthrough channel domain is capacity-unsatisfiable', Diagnostics={**Diagnostics, 'GlobalPlanDomainComplete': True, 'CompleteAssignmentCutProof': True, 'PortAssignmentProofComplete': True, 'ComponentFabricConstructionComplete': True, 'OwnershipSearchComplete': True, 'ChannelPlanRejectionCount': ChannelPlanRejectionCount, 'ExteriorGuideDetourNoGood': [list(Key) for Key in sorted(ExteriorGuideDetourNoGood)], 'ExteriorGuideDetourCoreCheckCount': ExteriorGuideDetourCoreCheckCount, 'ExteriorGuideDetourCoreMinimal': bool(ExteriorGuideDetourNoGood), 'PortAssignmentUnsatCoreSignals': list(CutSignals), 'PortAssignmentUnsatCoreFingerprint': CutFingerprint, 'ConflictFingerprint': CutFingerprint, 'ConflictGraph': {'Classification': 'physical-feedthrough-capacity-cut', 'ConflictSignals': list(CutSignals), 'RelocationSignals': list(CutSignals), 'PriorityRelocationSignals': list(CutSignals), 'CompleteAssignmentCutProof': True}, 'ImplicitForeignTransitDomainCount': 0})) from Error
    return FinalizePreparedPhysicalComponentAssembly(Preparation, Resources, Problem=Problem, CoarsePlan=CoarsePlan, AccessCertificate=AccessCertificate, ResourceGraph=ResourceGraph, ComponentGraphFingerprint=ComponentGraphFingerprint, ComponentKeepoutNodes=ComponentKeepoutNodes, MinimumPlacementY=MinimumPlacementY, ChannelReservations=ChannelReservations, SelectedPorts=SelectedPorts, SelectedBoundaryPorts=SelectedBoundaryPorts, AssemblyChoiceFingerprint=AssemblyChoiceFingerprint, BoundaryIteratorCacheKey=BoundaryIteratorCacheKey)

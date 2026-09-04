"""Bounded candidate-domain controls and proof gates."""

from __future__ import annotations

from ....Contracts.Core import Position3

from ....Contracts.Failures import RoutingFailure

from ....Contracts.Failures import RoutingFailureReason

from ....Contracts.Failures import RoutingStageError

from ....Constraints.BoundaryRelations import RawPortalGeometryCache

from ....Constraints.PhysicalClaims import ClaimConflictPositions

from ....Constraints.PhysicalClaims import MandatoryClaimsConflict

from ....Policy import PhysicalDesignPolicy

from ....Runtime.Reliability import BuildStableFingerprint

from ....Resources.ResourceGraph import LocalRouteClaim

from ....Resources.ResourceGraph import NetRouteCandidate

from ....Resources.ResourceGraph import PortalReservation

from ....Resources.ResourceGraph import RoutingResourceClaims

from collections import deque

from dataclasses import dataclass

from dataclasses import replace

from time import monotonic

from typing import Any

from typing import Callable

from typing import Iterable

from ..Orchestration.RunModels import ClusterLeaseCandidateRealizabilityNogood, MandatoryPortalTupleSelfConflictEvidence, OptionalPortalSeedSliceExpired

def BuildClusterLeaseSignalPatternFingerprint(
    Reservations: tuple[PortalReservation, ...] | list[PortalReservation],
    Signal: str,
) -> str:
    """Identify one selected signal access template within fixed geometry."""
    return BuildStableFingerprint(tuple(sorted(
        (
            Reservation.Terminal,
            Reservation.Layer,
            tuple(Reservation.FirstSegment),
            tuple(sorted(Reservation.Claims.WireCells)),
            tuple(sorted(Reservation.Claims.SupportCells)),
            tuple(sorted(Reservation.Claims.RequiredAirCells)),
            tuple(sorted(Reservation.Claims.ElectricalCells)),
        )
        for Reservation in Reservations
        if (
            Reservation.Purpose == "cluster-boundary-lease"
            and Reservation.Signal == Signal
        )
    )))

def SelectTransactionalLeasePrescreenSignals(
    PlacementRecipeDiagnostics: object,
) -> frozenset[str]:
    """Select one exact transactional pair for authoritative lease screening."""
    if not isinstance(PlacementRecipeDiagnostics, dict):
        return frozenset()
    if not bool(
        PlacementRecipeDiagnostics.get(
            "TransactionalClusterEndpointRepair",
            False,
        )
    ):
        return frozenset()
    Signals = frozenset(map(
        str,
        PlacementRecipeDiagnostics.get(
            "InternalPinBankGeometryRepairSignals",
            (),
        ),
    ))
    return Signals if len(Signals) == 2 else frozenset()

def ShouldRefineCandidateRealizabilityLeaseNogood(
    TopologyRequiresJointPortfolio: bool,
    CompleteClusterInterfaceAccess: bool,
    HasClusterBoundaryLeaseReservations: bool,
    ReservationVariant: int,
    MaximumPortalReservationAlternatives: int,
    SkipStrictPortalReservation: bool,
    CurrentPatternFingerprint: str,
    PriorNogoods: (
        tuple[ClusterLeaseCandidateRealizabilityNogood, ...]
        | list[ClusterLeaseCandidateRealizabilityNogood]
    ),
    RemainingSeconds: float,
    Signal: str = "",
    MaximumRefinementsPerSignal: int = 2,
    MaximumTotalRefinements: int = 2,
    MinimumRetrySeconds: float = 5.0,
) -> bool:
    """Refine one endpoint without letting another consume its whole budget."""
    MatchingSignalNogoods = tuple(
        Nogood
        for Nogood in PriorNogoods
        if not Signal or Nogood.Signal == Signal
    )
    return (
        TopologyRequiresJointPortfolio
        and CompleteClusterInterfaceAccess
        and HasClusterBoundaryLeaseReservations
        and 0 <= ReservationVariant < MaximumPortalReservationAlternatives
        and MaximumPortalReservationAlternatives > 0
        and not SkipStrictPortalReservation
        and bool(CurrentPatternFingerprint)
        and MaximumRefinementsPerSignal > 0
        and MaximumTotalRefinements > 0
        and len(MatchingSignalNogoods) < MaximumRefinementsPerSignal
        and len(PriorNogoods) < MaximumTotalRefinements
        and all(
            Nogood.PatternFingerprint != CurrentPatternFingerprint
            for Nogood in MatchingSignalNogoods
        )
        and RemainingSeconds >= MinimumRetrySeconds
    )

def SelectCandidateRealizabilityProbeSliceSeconds(
    RemainingSeconds: float,
    MinimumProbeSeconds: float = 5.0,
    MaximumSliceSeconds: float = 10.0,
    EndgameReserveSeconds: float = 12.0,
) -> float:
    """Bound one lease-state route proof while retaining geometry-repair time."""
    if (
        MinimumProbeSeconds <= 0
        or MaximumSliceSeconds < MinimumProbeSeconds
        or EndgameReserveSeconds < 0
    ):
        raise ValueError("invalid candidate-realizability probe policy")
    Available = max(
        0.0,
        RemainingSeconds - EndgameReserveSeconds,
    )
    if Available < MinimumProbeSeconds:
        return 0.0
    return min(MaximumSliceSeconds, Available)

def ShouldContinueUniqueAccessDistinctCandidateRealizabilityProof(
    TopologyRequiresJointPortfolio: bool,
    ContinuationDiagnostics: object,
    RemainingSeconds: float,
    MinimumContinuationSeconds: float = 5.0,
    PublicationReserveSeconds: float = 2.0,
) -> bool:
    """Spend the reserved endgame once on the sole cut-distinct geometry."""
    if (
        MinimumContinuationSeconds <= 0
        or PublicationReserveSeconds < 0
    ):
        raise ValueError("invalid candidate-realizability continuation policy")
    return bool(
        TopologyRequiresJointPortfolio
        and isinstance(ContinuationDiagnostics, dict)
        and ContinuationDiagnostics.get("Eligible", False)
        and int(
            ContinuationDiagnostics.get(
                "CutInterfaceDifference",
                0,
            )
        ) > 0
        and int(
            ContinuationDiagnostics.get(
                "AccessDistinctCandidateCount",
                0,
            )
        ) == 1
        and RemainingSeconds
        >= MinimumContinuationSeconds + PublicationReserveSeconds
    )

def ShouldHandoffContinuedCandidateRealizabilityCut(
    EscalationHistory: tuple[dict[str, object], ...],
    ConflictGraph: object,
    BudgetExhausted: bool,
) -> bool:
    """Return a proved cut without repeating unchanged routing controls."""
    return bool(
        not BudgetExhausted
        and isinstance(ConflictGraph, dict)
        and ConflictGraph.get("PriorityRelocationSignals")
        and any(
            isinstance(Entry, dict)
            and Entry.get("Action")
            == (
                "continue-unique-access-distinct-"
                "candidate-realizability-proof"
            )
            for Entry in EscalationHistory
        )
    )

def SelectOptionalPortalSeedSliceSeconds(
    RemainingSeconds: float,
    MaximumSliceSeconds: float = 0.5,
) -> float:
    """Reserve most remaining routing time for authoritative candidate work."""
    if MaximumSliceSeconds <= 0:
        raise ValueError("MaximumSliceSeconds must be positive")
    return min(
        MaximumSliceSeconds,
        max(0.0, RemainingSeconds) * 0.25,
    )

def SelectCandidateDomainPairScanSliceSeconds(
    RemainingSeconds: float,
    MaximumSliceSeconds: float = 0.5,
) -> float:
    """Bound optional pair diagnosis independently of authoritative routing."""
    if MaximumSliceSeconds <= 0:
        raise ValueError("MaximumSliceSeconds must be positive")
    return min(
        MaximumSliceSeconds,
        max(0.0, RemainingSeconds) * 0.05,
    )

def ShouldPrepareOptionalPortalSeed(
    UnreservedPortalMode: bool,
    ProfileSignals: tuple[str, ...] | list[str],
    SeedReservationPrepared: bool,
) -> bool:
    """Return whether the optional multi-signal seed hint is applicable."""
    return (
        UnreservedPortalMode
        and len(ProfileSignals) > 8
        and not SeedReservationPrepared
    )

def BuildOptionalPortalSeedWorkCheck(
    ExpiresAt: float,
    SharedWorkCheck: Callable[[dict[str, object]], None],
) -> Callable[[dict[str, object]], None]:
    """Bound an optional hint without replacing the shared routing deadline."""
    def Check(Details: dict[str, object]) -> None:
        SharedWorkCheck(Details)
        if monotonic() >= ExpiresAt:
            raise OptionalPortalSeedSliceExpired

    return Check

def CountExactLegalRetainedJointStates(
    LocalRouteDiagnostics: dict[str, object],
) -> int:
    """Count access-screened joint states available to the placement flow."""
    JointDiagnostics = LocalRouteDiagnostics.get(
        "__JointClusterPlacement__",
        {},
    )
    if not isinstance(JointDiagnostics, dict):
        return 0
    RemainingStateCount = JointDiagnostics.get(
        "RemainingExactLegalRetainedStateCount"
    )
    if (
        isinstance(RemainingStateCount, int)
        and not isinstance(RemainingStateCount, bool)
        and RemainingStateCount > 0
    ):
        return RemainingStateCount
    ExactStates = JointDiagnostics.get("ExactLegalRetainedStates")
    if isinstance(ExactStates, tuple | list):
        return len(ExactStates)
    RetainedStates = JointDiagnostics.get("RetainedStates", ())
    if not isinstance(RetainedStates, tuple | list):
        return 0
    return sum(
        bool(State.get("ExactLegal"))
        for State in RetainedStates
        if isinstance(State, dict)
    )

def CountJointAssignmentConstraintKinds(
    LocalRouteDiagnostics: dict[str, object],
) -> tuple[int, int]:
    """Count higher-order sets and pair edges retained by joint placement."""
    JointDiagnostics = LocalRouteDiagnostics.get(
        "__JointClusterPlacement__",
        {},
    )
    if not isinstance(JointDiagnostics, dict):
        return (0, 0)
    AssignmentConstraints = JointDiagnostics.get(
        "ActiveAssignmentConstraints",
        JointDiagnostics.get("AssignmentConstraints", {}),
    )
    if not isinstance(AssignmentConstraints, dict):
        return (0, 0)
    HigherOrderSignalSets = AssignmentConstraints.get(
        "HigherOrderSignalSets",
        (),
    )
    PairwiseConflictEdges = AssignmentConstraints.get(
        "PairwiseConflictEdges",
        (),
    )
    HigherOrderCount = (
        sum(
            isinstance(Signals, tuple | list) and bool(Signals)
            for Signals in HigherOrderSignalSets
        )
        if isinstance(HigherOrderSignalSets, tuple | list)
        else 0
    )
    PairwiseCount = (
        sum(
            isinstance(Edge, tuple | list) and len(Edge) == 2
            for Edge in PairwiseConflictEdges
        )
        if isinstance(PairwiseConflictEdges, tuple | list)
        else 0
    )
    return (HigherOrderCount, PairwiseCount)

def SelectJointHigherOrderConstraintSignals(
    LocalRouteDiagnostics: dict[str, object],
) -> frozenset[str]:
    """Return the name-independent signal set from cumulative higher cuts."""
    JointDiagnostics = LocalRouteDiagnostics.get(
        "__JointClusterPlacement__",
        {},
    )
    AssignmentConstraints = (
        JointDiagnostics.get(
            "ActiveAssignmentConstraints",
            JointDiagnostics.get("AssignmentConstraints", {}),
        )
        if isinstance(JointDiagnostics, dict)
        else {}
    )
    HigherOrderSignalSets = (
        AssignmentConstraints.get("HigherOrderSignalSets", ())
        if isinstance(AssignmentConstraints, dict)
        else ()
    )
    if not isinstance(HigherOrderSignalSets, tuple | list):
        return frozenset()
    return frozenset(
        str(Signal)
        for Signals in HigherOrderSignalSets
        if isinstance(Signals, tuple | list)
        for Signal in Signals
    )

def SelectJointPairwiseConstraintSignals(
    LocalRouteDiagnostics: dict[str, object],
) -> frozenset[str]:
    """Return endpoints of cumulative pair cuts retained by placement."""
    JointDiagnostics = LocalRouteDiagnostics.get(
        "__JointClusterPlacement__",
        {},
    )
    AssignmentConstraints = (
        JointDiagnostics.get(
            "ActiveAssignmentConstraints",
            JointDiagnostics.get("AssignmentConstraints", {}),
        )
        if isinstance(JointDiagnostics, dict)
        else {}
    )
    PairwiseConflictEdges = (
        AssignmentConstraints.get("PairwiseConflictEdges", ())
        if isinstance(AssignmentConstraints, dict)
        else ()
    )
    if not isinstance(PairwiseConflictEdges, tuple | list):
        return frozenset()
    return frozenset(
        str(Signal)
        for Edge in PairwiseConflictEdges
        if isinstance(Edge, tuple | list) and len(Edge) == 2
        for Signal in Edge
    )

def HasCumulativeJointAssignmentConstraintMaturity(
    LocalRouteDiagnostics: dict[str, object],
) -> bool:
    """Return whether placement has both higher-order and pairwise cut memory."""
    HigherOrderCount, PairwiseCount = (
        CountJointAssignmentConstraintKinds(LocalRouteDiagnostics)
    )
    return HigherOrderCount > 0 and PairwiseCount > 0

def ShouldCapMatureCumulativeJointPortfolio(
    PlacementWasRelocated: bool,
    ExactLegalRetainedJointStateCount: int,
    HasCumulativeAssignmentConstraints: bool,
) -> bool:
    """Return whether bounded mature-portfolio search caps are applicable."""
    return (
        PlacementWasRelocated
        and ExactLegalRetainedJointStateCount > 0
        and HasCumulativeAssignmentConstraints
    )

def ShouldStageTopologyPressureJointPortfolio(
    ExactLegalRetainedJointStateCount: int,
    RequiresJointPortfolio: bool,
) -> bool:
    """Stage a seed domain for every topology-pressure geometry.

    A broad fallback placement can still carry the same reconvergent logical
    demand even when it has no retained packed sibling.  Its complete request
    metadata remains available to exact-cut completion; staging merely avoids
    eagerly materializing unrelated alternatives before the capacity-one
    planner reports the actual deficient domain.
    """
    del ExactLegalRetainedJointStateCount
    return RequiresJointPortfolio

def SelectMaturePortfolioPortalLimit(
    RequestedPortalLimit: int,
    ApplyMaturePortfolioCap: bool,
    MaximumMaturePortfolioPortalLimit: int = 6,
) -> int:
    """Cap only mature cumulative portfolios without growing smaller domains."""
    if RequestedPortalLimit < 1:
        raise ValueError("RequestedPortalLimit must be positive")
    if MaximumMaturePortfolioPortalLimit < 1:
        raise ValueError(
            "MaximumMaturePortfolioPortalLimit must be positive"
        )
    return (
        min(
            RequestedPortalLimit,
            MaximumMaturePortfolioPortalLimit,
        )
        if ApplyMaturePortfolioCap
        else RequestedPortalLimit
    )

def SelectMaturePortfolioExactInitialRequestFloor(
    ConfiguredRequestFloor: int,
    ApplyMaturePortfolioCap: bool,
    MaximumMaturePortfolioRequestFloor: int = 8,
) -> int:
    """Preserve smaller user floors while bounding mature portfolio work."""
    if ConfiguredRequestFloor < 0:
        raise ValueError("ConfiguredRequestFloor cannot be negative")
    if MaximumMaturePortfolioRequestFloor < 1:
        raise ValueError(
            "MaximumMaturePortfolioRequestFloor must be positive"
        )
    return (
        min(
            ConfiguredRequestFloor,
            MaximumMaturePortfolioRequestFloor,
        )
        if ApplyMaturePortfolioCap
        else ConfiguredRequestFloor
    )

def SelectCoordinatedInitialRequestWindowLimit(
    BaseRequestLimit: int,
    AvailableRequestCount: int,
    CandidateGrowthFactor: int,
    CoordinatedCandidateDiversityLevel: int,
    ApplyCoordinatedProfile: bool,
) -> int:
    """Grow only a reported cut signal's bounded initial request window."""
    if BaseRequestLimit < 1:
        raise ValueError("BaseRequestLimit must be positive")
    if AvailableRequestCount < 0:
        raise ValueError("AvailableRequestCount cannot be negative")
    if CandidateGrowthFactor < 1:
        raise ValueError("CandidateGrowthFactor must be positive")
    if CoordinatedCandidateDiversityLevel < 0:
        raise ValueError(
            "CoordinatedCandidateDiversityLevel cannot be negative"
        )
    RequestedLimit = BaseRequestLimit
    if ApplyCoordinatedProfile:
        RequestedLimit *= (
            CandidateGrowthFactor
            ** CoordinatedCandidateDiversityLevel
        )
    return min(AvailableRequestCount, RequestedLimit)

def SelectCoordinatedContinuationRequestWindowLimit(
    CurrentRequestLimit: int,
    AvailableRequestCount: int,
    BaseRequestLimit: int,
    CandidateGrowthFactor: int,
    CoordinatedCandidateDiversityLevel: int,
    MaximumCandidateDiversityEscalations: int,
    ApplyCoordinatedContinuation: bool,
) -> int:
    """Extend one reported signal to the next unseen bounded request tranche."""
    if CurrentRequestLimit < 0:
        raise ValueError("CurrentRequestLimit cannot be negative")
    if AvailableRequestCount < 0:
        raise ValueError("AvailableRequestCount cannot be negative")
    if CurrentRequestLimit > AvailableRequestCount:
        raise ValueError(
            "CurrentRequestLimit cannot exceed AvailableRequestCount"
        )
    if BaseRequestLimit < 1:
        raise ValueError("BaseRequestLimit must be positive")
    if CandidateGrowthFactor < 1:
        raise ValueError("CandidateGrowthFactor must be positive")
    if CoordinatedCandidateDiversityLevel < 0:
        raise ValueError(
            "CoordinatedCandidateDiversityLevel cannot be negative"
        )
    if MaximumCandidateDiversityEscalations < 1:
        raise ValueError(
            "MaximumCandidateDiversityEscalations must be positive"
        )
    if (
        not ApplyCoordinatedContinuation
        or CoordinatedCandidateDiversityLevel + 1
        >= MaximumCandidateDiversityEscalations
    ):
        return CurrentRequestLimit
    ContinuationLimit = SelectCoordinatedInitialRequestWindowLimit(
        BaseRequestLimit,
        AvailableRequestCount,
        CandidateGrowthFactor,
        CoordinatedCandidateDiversityLevel + 1,
        True,
    )
    return max(CurrentRequestLimit, ContinuationLimit)

def SelectCoordinatedCandidateExpansionLimit(
    BaseExpansionLimit: int,
    StrictMaximumExpansions: int,
    CandidateGrowthFactor: int,
    CoordinatedCandidateDiversityLevel: int,
    ApplyCoordinatedProfile: bool,
) -> int:
    """Grow search depth only for one repeatedly starved reported signal."""
    if BaseExpansionLimit < 1:
        raise ValueError("BaseExpansionLimit must be positive")
    if StrictMaximumExpansions < 1:
        raise ValueError("StrictMaximumExpansions must be positive")
    if BaseExpansionLimit > StrictMaximumExpansions:
        raise ValueError(
            "BaseExpansionLimit cannot exceed StrictMaximumExpansions"
        )
    if CandidateGrowthFactor < 1:
        raise ValueError("CandidateGrowthFactor must be positive")
    if CoordinatedCandidateDiversityLevel < 0:
        raise ValueError(
            "CoordinatedCandidateDiversityLevel cannot be negative"
        )
    if not ApplyCoordinatedProfile:
        return BaseExpansionLimit
    return min(
        StrictMaximumExpansions,
        BaseExpansionLimit
        * CandidateGrowthFactor
        ** CoordinatedCandidateDiversityLevel,
    )

def SelectEffectiveCoordinatedCandidateDiversityLevel(
    CandidateDiversityLevel: int,
    ConfiguredCoordinatedBoost: int,
    MaximumCandidateDiversityEscalations: int,
    ApplyCoordinatedProfile: bool,
) -> int:
    """Keep one reported cut ahead without raising the global search limit."""
    if CandidateDiversityLevel < 0:
        raise ValueError("CandidateDiversityLevel cannot be negative")
    if ConfiguredCoordinatedBoost < 0:
        raise ValueError("ConfiguredCoordinatedBoost cannot be negative")
    if MaximumCandidateDiversityEscalations < 1:
        raise ValueError(
            "MaximumCandidateDiversityEscalations must be positive"
        )
    if (
        not ApplyCoordinatedProfile
        or ConfiguredCoordinatedBoost == 0
    ):
        return 0
    return min(
        MaximumCandidateDiversityEscalations - 1,
        CandidateDiversityLevel + ConfiguredCoordinatedBoost,
    )

def ShouldPrepareMandatoryPortalTuples(
    HasMaterializedCandidates: bool,
    HasRetainedCandidates: bool,
    RegenerateSignal: bool,
) -> bool:
    """Skip immutable retained domains during offender-only prescreening."""
    return not (
        not RegenerateSignal
        and (
            HasMaterializedCandidates
            or HasRetainedCandidates
        )
    )

def SelectClusterLeaseOwnershipSignals(
    ProfileSignals: Iterable[str],
    BoundaryLeaseSignals: Iterable[str],
    CompleteClusterInterfaceAccess: bool,
    DenseComponentSignals: Iterable[str] = (),
) -> frozenset[str]:
    """Select complete ownership only for the measured dense component."""
    Profiles = frozenset(str(Value) for Value in ProfileSignals)
    BoundarySignals = frozenset(str(Value) for Value in BoundaryLeaseSignals)
    ComponentSignals = frozenset(
        str(Value) for Value in DenseComponentSignals
    )
    if (
        CompleteClusterInterfaceAccess
        and BoundarySignals
        and ComponentSignals
    ):
        return Profiles.intersection(
            BoundarySignals,
            ComponentSignals,
        )
    return BoundarySignals

def ShouldScanCandidateDomainPairCut(
    AdaptiveRoutingEnabled: bool,
    PlacementWasRelocated: bool,
    ExactLegalRetainedJointStateCount: int,
    JointHigherOrderConstraintCount: int,
    StarvedSignal: str,
    JointHigherOrderConstraintSignals: frozenset[str],
    CandidateDiversityLevel: int,
    ReservationVariant: int,
    LaneDiversityLevel: int,
    SkipStrictPortalReservation: bool,
    MaximumCandidateDiversityEscalations: int,
) -> bool:
    """Restrict optional pair diagnosis to an initial post-cut portfolio."""
    return (
        AdaptiveRoutingEnabled
        and PlacementWasRelocated
        and ExactLegalRetainedJointStateCount > 1
        and JointHigherOrderConstraintCount > 0
        and StarvedSignal in JointHigherOrderConstraintSignals
        and CandidateDiversityLevel == 0
        and ReservationVariant == 0
        and LaneDiversityLevel == 0
        and not SkipStrictPortalReservation
        and CandidateDiversityLevel + 1
        < MaximumCandidateDiversityEscalations
    )

def SelectCoordinatedPortalVariantCount(
    BaselineVariantCount: int,
    DemandDerivedPortalLimit: int,
    ApplyCoordinatedDiversification: bool,
    MaximumCoordinatedPortalVariants: int = 6,
) -> int:
    """Widen only a repeated-cut signal within existing portal limits."""
    if BaselineVariantCount < 1:
        raise ValueError("BaselineVariantCount must be positive")
    if DemandDerivedPortalLimit < 1:
        raise ValueError("DemandDerivedPortalLimit must be positive")
    if MaximumCoordinatedPortalVariants < 1:
        raise ValueError(
            "MaximumCoordinatedPortalVariants must be positive"
        )
    if not ApplyCoordinatedDiversification:
        return BaselineVariantCount
    return max(
        BaselineVariantCount,
        min(
            DemandDerivedPortalLimit,
            MaximumCoordinatedPortalVariants,
        ),
    )

def ShouldLimitRetainedPortfolioPortalDomain(
    AdaptiveRoutingEnabled: bool,
    ApplyStagedPortfolioProof: bool,
    ExactLegalRetainedJointStateCount: int,
    RawPortalCachePresent: bool,
    RemainingSeconds: float,
    PortalLimit: int,
    MaximumSliceSeconds: float = 24.0,
) -> bool:
    """Limit portal breadth only for an actual retained-placement slice."""
    if MaximumSliceSeconds <= 0:
        raise ValueError("MaximumSliceSeconds must be positive")
    return (
        AdaptiveRoutingEnabled
        and ApplyStagedPortfolioProof
        and ExactLegalRetainedJointStateCount > 1
        and not RawPortalCachePresent
        and RemainingSeconds <= MaximumSliceSeconds
        and PortalLimit > 2
    )

def RawPortalProfileMatchesRequestedControls(
    Cache: RawPortalGeometryCache | None,
    PortalLimit: int,
    PortalVariantCounts: dict[str, int],
) -> bool:
    """Treat cached portal work as a hint, never as retry control authority."""
    return (
        Cache is not None
        and Cache.PortalLimit == PortalLimit
        and Cache.PortalVariantCounts
        == tuple(sorted(PortalVariantCounts.items()))
    )

def ShouldRetainBoundedPortfolioPortalProfile(
    ApplyStagedPortfolioProof: bool,
    ExactLegalRetainedJointStateCount: int,
    Cache: RawPortalGeometryCache | None,
) -> bool:
    """Keep one sibling probe bounded without constraining the sole survivor."""
    return (
        ApplyStagedPortfolioProof
        and ExactLegalRetainedJointStateCount > 1
        and Cache is not None
        and Cache.RetainedPortfolioSliceLimited
    )

def ShouldUseMatureStagedInitialCandidateScheduler(
    ApplyMaturePortfolioSearchCaps: bool,
    CandidateDiversityLevel: int,
    ReservationVariant: int,
    LaneDiversityLevel: int,
    SkipStrictPortalReservation: bool,
    RetainedCandidateCachePresent: bool,
    PriorCandidateCachePresent: bool,
    AllowPriorCandidateCache: bool = False,
    ForcePhysicalAssemblyPlanning: bool = False,
) -> bool:
    """Stage the initial window while retaining valid topology seed domains."""
    return (
        (ApplyMaturePortfolioSearchCaps or ForcePhysicalAssemblyPlanning)
        and CandidateDiversityLevel == 0
        and ReservationVariant == 0
        and LaneDiversityLevel == 0
        and not SkipStrictPortalReservation
        and not RetainedCandidateCachePresent
        and (AllowPriorCandidateCache or not PriorCandidateCachePresent)
    )

def MayAdvanceStagedCandidateOnExhaustion(
    ApplyMaturePortfolioSearchCaps: bool,
    ExactLegalRetainedJointStateCount: int,
    Signal: str,
    JointHigherOrderConstraintSignals: frozenset[str],
) -> bool:
    """Advance only from a mature feedback-derived empty domain proof.

    The topology-pressure portfolio uses staging to publish a viable seed pool
    early, but its first exact candidate still owns the ordinary offender
    escalation path.  Treating that initial window as a placement proof would
    discard a candidate before its deferred requests and adaptive
    diversification have run.
    """
    return (
        ApplyMaturePortfolioSearchCaps
        and ExactLegalRetainedJointStateCount > 1
        and Signal not in JointHigherOrderConstraintSignals
    )

@dataclass(frozen=True)
class StagedInitialRouteTreeResult:
    """Canonical route-tree results from a bounded staged initial window."""

    RouteTrees: tuple[object | None, ...]
    ExhaustedSignals: tuple[str, ...]
    ExecutedRequestCount: int
    PlannedRequestCount: int
    BatchCount: int
    FullPoolGenerated: bool
    EverySignalHasTree: bool
    ExecutedRequestCountsBySignal: tuple[tuple[str, int], ...]
    FirstSuccessfulRequestIndicesBySignal: tuple[tuple[str, int], ...]

def GenerateStagedInitialRouteTrees(
    SignalOrder: tuple[str, ...] | list[str],
    RequestsBySignal: dict[str, list[tuple[Any, ...]]],
    GenerateBatch: Callable[[list[tuple[Any, ...]]], list[Any]],
    MayStopOnExhaustedSignal: Callable[[str], bool],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    StopAfterEverySignalHasTree: bool = False,
) -> StagedInitialRouteTreeResult:
    """Probe canonical request rounds and preserve exact issued-prefix state.

    A signal is proven empty only after every request in its exact initial
    window returned no native tree. If no eligible empty signal is proven,
    ordinary callers generate every deferred request. Seed-pool callers keep
    a sparse issued prefix when an ineligible higher-order signal exhausts;
    the unissued suffix remains deferred for offender-only continuation.
    Results always retain signal-major shape so issued-prefix counts can
    distinguish unissued entries from completed no-tree results.
    """
    OrderedSignals = tuple(
        Signal for Signal in SignalOrder if Signal in RequestsBySignal
    )
    PlannedRequestCount = sum(
        len(RequestsBySignal[Signal]) for Signal in OrderedSignals
    )
    ResultsBySignal: dict[str, list[object]] = {
        Signal: [None] * len(RequestsBySignal[Signal])
        for Signal in OrderedSignals
    }
    IssuedBySignal: dict[str, list[bool]] = {
        Signal: [False] * len(RequestsBySignal[Signal])
        for Signal in OrderedSignals
    }
    SignalsWithTree: set[str] = set()
    ExecutedRequestCount = 0
    BatchCount = 0

    def Check(Phase: str, **Details: object) -> None:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": Phase,
                "ExecutedRequestCount": ExecutedRequestCount,
                "PlannedRequestCount": PlannedRequestCount,
                "BatchCount": BatchCount,
                **Details,
            })

    MaximumWindow = max(
        (len(RequestsBySignal[Signal]) for Signal in OrderedSignals),
        default=0,
    )
    for RequestIndex in range(MaximumWindow):
        RoundEntries = [
            (Signal, RequestIndex, RequestsBySignal[Signal][RequestIndex])
            for Signal in OrderedSignals
            if (
                Signal not in SignalsWithTree
                and RequestIndex < len(RequestsBySignal[Signal])
            )
        ]
        if not RoundEntries:
            break
        Check(
            "round",
            RequestIndex=RequestIndex,
            SignalCount=len(RoundEntries),
        )
        RoundResults = GenerateBatch([
            Request for _Signal, _Index, Request in RoundEntries
        ])
        if len(RoundResults) != len(RoundEntries):
            raise ValueError(
                "GenerateBatch must return one result per staged request"
            )
        BatchCount += 1
        ExecutedRequestCount += len(RoundEntries)
        for (Signal, Index, _Request), Result in zip(
            RoundEntries,
            RoundResults,
        ):
            ResultsBySignal[Signal][Index] = Result
            IssuedBySignal[Signal][Index] = True
            if Result is not None:
                SignalsWithTree.add(Signal)
        ExhaustedSignals = tuple(
            Signal
            for Signal in OrderedSignals
            if (
                Signal not in SignalsWithTree
                and bool(RequestsBySignal[Signal])
                and all(IssuedBySignal[Signal])
                and MayStopOnExhaustedSignal(Signal)
            )
        )
        if ExhaustedSignals:
            Check(
                "exhausted",
                ExhaustedSignals=list(ExhaustedSignals),
            )
            return StagedInitialRouteTreeResult(
                RouteTrees=tuple(),
                ExhaustedSignals=ExhaustedSignals,
                ExecutedRequestCount=ExecutedRequestCount,
                PlannedRequestCount=PlannedRequestCount,
                BatchCount=BatchCount,
                FullPoolGenerated=False,
                EverySignalHasTree=False,
                ExecutedRequestCountsBySignal=tuple(
                    (
                        Signal,
                        sum(IssuedBySignal[Signal]),
                    )
                    for Signal in OrderedSignals
                ),
                FirstSuccessfulRequestIndicesBySignal=tuple(
                    (
                        Signal,
                        next(
                            Index
                            for Index, Result
                            in enumerate(ResultsBySignal[Signal])
                            if Result is not None
                        ),
                    )
                    for Signal in OrderedSignals
                    if Signal in SignalsWithTree
                ),
            )
        EverySignalHasTree = all(
            not RequestsBySignal[Signal]
            or Signal in SignalsWithTree
            for Signal in OrderedSignals
        )
        if StopAfterEverySignalHasTree and EverySignalHasTree:
            Check("seed-pool-complete")
            return StagedInitialRouteTreeResult(
                RouteTrees=tuple(
                    Result
                    for Signal in OrderedSignals
                    for Result in ResultsBySignal[Signal]
                ),
                ExhaustedSignals=tuple(),
                ExecutedRequestCount=ExecutedRequestCount,
                PlannedRequestCount=PlannedRequestCount,
                BatchCount=BatchCount,
                FullPoolGenerated=False,
                EverySignalHasTree=True,
                ExecutedRequestCountsBySignal=tuple(
                    (
                        Signal,
                        sum(IssuedBySignal[Signal]),
                    )
                    for Signal in OrderedSignals
                ),
                FirstSuccessfulRequestIndicesBySignal=tuple(
                    (
                        Signal,
                        next(
                            Index
                            for Index, Result
                            in enumerate(ResultsBySignal[Signal])
                            if Result is not None
                        ),
                    )
                    for Signal in OrderedSignals
                    if Signal in SignalsWithTree
                ),
            )

    IncompleteSignals = tuple(
        Signal
        for Signal in OrderedSignals
        if RequestsBySignal[Signal] and Signal not in SignalsWithTree
    )
    if StopAfterEverySignalHasTree and IncompleteSignals:
        # Reaching this point means every incomplete signal exhausted its
        # issued window but was deliberately ineligible for placement
        # advancement (for example, an active higher-order cut endpoint).
        # Preserve successful seed prefixes and let the existing typed
        # offender-only path widen only those incomplete domains.
        Check(
            "seed-pool-incomplete",
            IncompleteSignals=list(IncompleteSignals),
            UnissuedRequestCount=(
                PlannedRequestCount - ExecutedRequestCount
            ),
        )
        return StagedInitialRouteTreeResult(
            RouteTrees=tuple(
                Result
                for Signal in OrderedSignals
                for Result in ResultsBySignal[Signal]
            ),
            ExhaustedSignals=tuple(),
            ExecutedRequestCount=ExecutedRequestCount,
            PlannedRequestCount=PlannedRequestCount,
            BatchCount=BatchCount,
            FullPoolGenerated=False,
            EverySignalHasTree=False,
            ExecutedRequestCountsBySignal=tuple(
                (
                    Signal,
                    sum(IssuedBySignal[Signal]),
                )
                for Signal in OrderedSignals
            ),
            FirstSuccessfulRequestIndicesBySignal=tuple(
                (
                    Signal,
                    next(
                        Index
                        for Index, Result
                        in enumerate(ResultsBySignal[Signal])
                        if Result is not None
                    ),
                )
                for Signal in OrderedSignals
                if Signal in SignalsWithTree
            ),
        )

    RemainingEntries = [
        (Signal, Index, Request)
        for Signal in OrderedSignals
        for Index, Request in enumerate(RequestsBySignal[Signal])
        if not IssuedBySignal[Signal][Index]
    ]
    if RemainingEntries:
        Check("full-fallback", RemainingRequestCount=len(RemainingEntries))
        RemainingResults = GenerateBatch([
            Request for _Signal, _Index, Request in RemainingEntries
        ])
        if len(RemainingResults) != len(RemainingEntries):
            raise ValueError(
                "GenerateBatch must return one result per fallback request"
            )
        BatchCount += 1
        ExecutedRequestCount += len(RemainingEntries)
        for (Signal, Index, _Request), Result in zip(
            RemainingEntries,
            RemainingResults,
        ):
            ResultsBySignal[Signal][Index] = Result
            IssuedBySignal[Signal][Index] = True
    Check("complete")
    return StagedInitialRouteTreeResult(
        RouteTrees=tuple(
            Result
            for Signal in OrderedSignals
            for Result in ResultsBySignal[Signal]
        ),
        ExhaustedSignals=tuple(),
        ExecutedRequestCount=ExecutedRequestCount,
        PlannedRequestCount=PlannedRequestCount,
        BatchCount=BatchCount,
        FullPoolGenerated=True,
        EverySignalHasTree=all(
            not RequestsBySignal[Signal]
            or Signal in SignalsWithTree
            for Signal in OrderedSignals
        ),
        ExecutedRequestCountsBySignal=tuple(
            (
                Signal,
                sum(IssuedBySignal[Signal]),
            )
            for Signal in OrderedSignals
        ),
        FirstSuccessfulRequestIndicesBySignal=tuple(
            (
                Signal,
                next(
                    Index
                    for Index, Result in enumerate(ResultsBySignal[Signal])
                    if Result is not None
                ),
            )
            for Signal in OrderedSignals
            if Signal in SignalsWithTree
        ),
    )

def FindPriorCandidateDomainPairExpansion(
    EscalationHistory: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    Signal: str,
    CandidateFailureFingerprint: str | None = None,
) -> dict[str, Any] | None:
    """Return a prior matching pair expansion for one starved domain.

    Endpoint membership alone is not structural repeat evidence: a later
    access topology may starve the same signal for a different reason.  When
    supplied, the candidate failure fingerprint makes the caller advance a
    retained portfolio only after the exact empty-domain state recurs.
    """
    return next(
        (
            Entry
            for Entry in reversed(EscalationHistory)
            if (
                isinstance(Entry, dict)
                and bool(Entry.get("CandidateDomainPairExpansion"))
                and Signal in Entry.get("AffectedSignals", ())
                and (
                    CandidateFailureFingerprint is None
                    or Entry.get("CandidateFailureFingerprint")
                    == CandidateFailureFingerprint
                )
            )
        ),
        None,
    )

def ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
    PlacementWasRelocated: bool,
    ExactLegalRetainedJointStateCount: int,
    HasCumulativeAssignmentConstraints: bool,
    CandidateDiversityLevel: int,
    ReservationVariant: int,
    LaneDiversityLevel: int,
    SkipStrictPortalReservation: bool,
    RoutedTreeCount: int,
    MaterializedCandidateCount: int,
    AllRoutedTreesRejectedByFixedLegality: bool = False,
    RepeatedCandidateStarvationClass: bool = False,
) -> bool:
    """Prefer another access topology after one physically empty window."""
    return (
        PlacementWasRelocated
        and ExactLegalRetainedJointStateCount > 1
        and HasCumulativeAssignmentConstraints
        and ReservationVariant == 0
        and LaneDiversityLevel == 0
        and not SkipStrictPortalReservation
        and MaterializedCandidateCount == 0
        and (
            (
                CandidateDiversityLevel == 0
                and RoutedTreeCount == 0
            )
            or (
                AllRoutedTreesRejectedByFixedLegality
                and RepeatedCandidateStarvationClass
            )
        )
    )

def ShouldAdvanceTopologyCutEpochOnCandidateStarvation(
    *,
    PlacementWasRelocated: bool,
    TopologyRequiresJointPortfolio: bool,
    HasTopologyCutConstraintEvidence: bool,
    CandidateDiversityLevel: int,
    ReservationVariant: int,
    LaneDiversityLevel: int,
    SkipStrictPortalReservation: bool,
    RoutedTreeCount: int,
    MaterializedCandidateCount: int,
) -> bool:
    """Return a proved empty topology domain to geometry before controls."""
    return (
        PlacementWasRelocated
        and TopologyRequiresJointPortfolio
        and HasTopologyCutConstraintEvidence
        and CandidateDiversityLevel == 0
        and ReservationVariant == 0
        and LaneDiversityLevel == 0
        and not SkipStrictPortalReservation
        and RoutedTreeCount == 0
        and MaterializedCandidateCount == 0
    )

def ShouldContinueCutScopedFixedLegalityWindow(
    *,
    PlacementWasRelocated: bool,
    ExactLegalRetainedJointStateCount: int,
    HasCumulativeAssignmentConstraints: bool,
    CandidateDiversityLevel: int,
    ReservationVariant: int,
    LaneDiversityLevel: int,
    SkipStrictPortalReservation: bool,
    Signal: str,
    JointAssignmentConstraintSignals: frozenset[str],
    RoutedTreeCount: int,
    MaterializedCandidateCount: int,
    AllRoutedTreesRejectedByFixedLegality: bool,
    DeferredRequestCount: int,
    HasCompleteClusterBoundaryLease: bool = False,
) -> bool:
    """Allow one unseen request tranche before abandoning exact geometry.

    A complete boundary lease makes every signal's selected access template
    part of the same authoritative macro state. A native tree rejected by
    fixed legality is therefore not a successful staged signal merely because
    that signal was outside the cut which selected the macro state.
    """
    return (
        PlacementWasRelocated
        and ExactLegalRetainedJointStateCount > 1
        and HasCumulativeAssignmentConstraints
        and CandidateDiversityLevel == 0
        and ReservationVariant == 0
        and LaneDiversityLevel == 0
        and not SkipStrictPortalReservation
        and (
            Signal in JointAssignmentConstraintSignals
            or HasCompleteClusterBoundaryLease
        )
        and RoutedTreeCount > 0
        and MaterializedCandidateCount == 0
        and AllRoutedTreesRejectedByFixedLegality
        and DeferredRequestCount > 0
    )

def BuildTelemetryRoutingStageError(
    Failure: RoutingFailure,
    Diagnostics: dict[str, object],
) -> RoutingStageError:
    """Return one typed failure carrying the complete measured work record."""
    return RoutingStageError(replace(
        Failure,
        Diagnostics=dict(Diagnostics),
    ))

def ShouldUseNegotiatedRouting(
    Policy: PhysicalDesignPolicy,
    SignalCount: int,
) -> bool:
    """Use negotiation only when the remaining global domain is large.

    Structural packing can freeze most nets locally.  Larger fan-in/fanout
    domains retain negotiation's bounded-memory behavior, while compact
    domains keep deterministic strict assignment.
    """
    # Selection is based on domain size and policy only; no circuit identity.
    return Policy.NegotiatedRouting.Enabled and SignalCount > 64

def ShouldRetryRelocatedCandidateStarvation(
    PlacementWasRelocated: bool,
    SourceGenerator: str | None,
    RelocationVariant: int,
    RoutingSpacing: int,
    UseNegotiatedRouting: bool,
    SignalCount: int,
    PrioritySignalCount: int,
) -> bool:
    """Return whether one relocated candidate domain may still be widened."""
    if not PlacementWasRelocated:
        return False
    if SourceGenerator != "row-beam-conflict-relocation":
        return True
    if RelocationVariant >= 3 or (
        RelocationVariant >= 2 and RoutingSpacing >= 10
    ):
        return True
    return (
        not UseNegotiatedRouting
        and 33 <= SignalCount <= 64
    )

def ShouldContinueSoleRetainedCutCandidateStarvation(
    PlacementWasRelocated: bool,
    ExactLegalRetainedJointStateCount: int,
    HasCumulativeAssignmentConstraints: bool,
    Signal: str,
    JointAssignmentConstraintSignals: frozenset[str],
) -> bool:
    """Use one existing deferred window for the sole cut-scoped survivor."""
    return (
        PlacementWasRelocated
        and ExactLegalRetainedJointStateCount == 1
        and HasCumulativeAssignmentConstraints
        and Signal in JointAssignmentConstraintSignals
    )

def ShouldExpandNegotiatedOffenderHalo(
    Failure: RoutingFailure,
    AdaptiveRoutingEnabled: bool,
    LaneDiversityLevel: int,
    MaximumLaneDiversityEscalations: int,
    TopologyRequiresJointPortfolio: bool = False,
) -> bool:
    """Use a same-placement halo only when topology repair is unavailable."""
    return (
        AdaptiveRoutingEnabled
        and not TopologyRequiresJointPortfolio
        and Failure.Reason
        == RoutingFailureReason.RepeaterAccessInfeasible
        and Failure.Stage == "NegotiatedDetailedRouting"
        and "ExpandOffenderHalo" in Failure.RepairActions
        and LaneDiversityLevel + 1
        < MaximumLaneDiversityEscalations
    )

def SelectNegotiatedOffenderHaloLaneDiversityLevel(
    LaneDiversityLevel: int,
    MaximumLaneDiversityEscalations: int,
) -> int:
    """Select one maximal bounded sparse-region repair state."""
    if LaneDiversityLevel < 0:
        raise ValueError("lane diversity level must be non-negative")
    if MaximumLaneDiversityEscalations <= 0:
        raise ValueError(
            "maximum lane diversity escalations must be positive"
        )
    if LaneDiversityLevel + 1 >= MaximumLaneDiversityEscalations:
        raise ValueError("no negotiated offender-halo state remains")
    return MaximumLaneDiversityEscalations - 1

def BuildNegotiatedOffenderHaloEscalation(
    Failure: RoutingFailure,
    FromLaneDiversityLevel: int,
    ToLaneDiversityLevel: int,
) -> dict[str, object]:
    """Record one non-recursive sparse-region repair diagnostic."""
    Diagnostics = dict(Failure.Diagnostics or {})
    InitialDetailedBatch = Diagnostics.get("InitialDetailedBatch", {})
    CompactInitialDetailedBatch = (
        {
            Key: InitialDetailedBatch[Key]
            for Key in (
                "Enabled",
                "RequestCount",
                "ScheduledRequestCount",
                "BatchCount",
                "CompletedWork",
                "DeadlineExceeded",
                "WorkerCount",
                "PreflightRejectedRequestCount",
                "MaterializationCacheHits",
                "MaterializationCacheMisses",
            )
            if Key in InitialDetailedBatch
        }
        if isinstance(InitialDetailedBatch, dict)
        else {}
    )
    CompactDiagnostics = {
        Key: Diagnostics[Key]
        for Key in (
            "ConflictGraph",
            "RequestCount",
            "AttemptedRequestCount",
            "Rejections",
            "Iteration",
            "Deadline",
            "StageTimingsSeconds",
            "EffectivePortalLimit",
            "PortalCacheMode",
            "PortalCacheHit",
            "PortalRequestCount",
            "PortalTargetCount",
        )
        if Key in Diagnostics
    }
    if CompactInitialDetailedBatch:
        CompactDiagnostics["InitialDetailedBatch"] = (
            CompactInitialDetailedBatch
        )
    return {
        "Stage": "NegotiatedDetailedRouting",
        "Action": "expand-offender-halo",
        "AffectedSignals": list(Failure.AffectedNets),
        "FromLaneDiversityLevel": FromLaneDiversityLevel,
        "ToLaneDiversityLevel": ToLaneDiversityLevel,
        "Failure": {
            "Reason": Failure.Reason.value,
            "Stage": Failure.Stage,
            "AffectedNets": list(Failure.AffectedNets),
            "RepairActions": list(Failure.RepairActions),
            "Detail": Failure.Detail,
            "Diagnostics": CompactDiagnostics,
        },
    }

def SelectNegotiatedExpandedRequestMinimumExpansionCount(
    SearchLimitedBeforeExpansion: bool,
    NativeRepeaterCutBeforeExpansion: bool,
    StrictBaseExpansions: int,
) -> int | None:
    """Keep a proved strict search floor after one sparse-region expansion."""
    if StrictBaseExpansions <= 0:
        raise ValueError("strict base expansions must be positive")
    if SearchLimitedBeforeExpansion or NativeRepeaterCutBeforeExpansion:
        return StrictBaseExpansions
    return None

def ExactAssignmentCompletionSignalOrderKey(
    CandidateSignal: str,
    MissingSignals: frozenset[str],
    PriorAttemptCount: int,
    ConflictFrequency: int,
    BlockedFrequency: int,
    CandidateCount: int,
) -> tuple[int, int, int, int, int, str]:
    """Rank absent and scarce exact domains before widening broad domains."""
    return (
        0 if CandidateSignal in MissingSignals else 1,
        CandidateCount,
        PriorAttemptCount,
        -ConflictFrequency,
        -BlockedFrequency,
        CandidateSignal,
    )

def SelectExactAssignmentCompletionRequestBatch(
    RankedRequestIndices: list[int],
    DomainConflictScores: dict[int, tuple[int, int, int]],
    BatchSize: int,
    StrictConflictFreeProofEnabled: bool,
    QuickDiscoveryEnabled: bool = True,
) -> tuple[tuple[int, ...], str]:
    """Keep quick discovery separate from a longer conflict-free proof."""
    if BatchSize < 1:
        raise ValueError("exact completion batch size must be positive")
    StrictProofRequests = [
        RequestIndex
        for RequestIndex in RankedRequestIndices
        if DomainConflictScores[RequestIndex][0] == 0
    ]
    if (
        StrictConflictFreeProofEnabled
        and QuickDiscoveryEnabled
        and RankedRequestIndices
    ):
        return (
            tuple(RankedRequestIndices[:BatchSize]),
            "quick-discovery",
        )
    if StrictConflictFreeProofEnabled:
        return (
            tuple(StrictProofRequests[:BatchSize]),
            "strict-proof",
        )
    return (
        tuple(RankedRequestIndices[:BatchSize]),
        "bounded-discovery",
    )

def SelectExactAssignmentCompletionCutWideRequests(
    RankedSignals: list[str] | tuple[str, ...],
    RankedRequestIndicesBySignal: dict[
        str,
        list[int] | tuple[int, ...],
    ],
    BatchSize: int,
    CandidateCountsBySignal: dict[str, int] | None = None,
) -> tuple[tuple[str, int], ...]:
    """Share a bounded cohort, then favor the scarcer exact domain."""
    if BatchSize < 1:
        raise ValueError("exact completion batch size must be positive")
    SignalOrder = tuple(dict.fromkeys(RankedSignals))
    RequestQueues = {
        Signal: deque(RankedRequestIndicesBySignal.get(Signal, ()))
        for Signal in SignalOrder
    }
    Selected: list[tuple[str, int]] = []
    if CandidateCountsBySignal is not None:
        ActiveSignals = tuple(
            Signal for Signal in SignalOrder if RequestQueues[Signal]
        )
        MinimumQuota = (
            2 if len(ActiveSignals) * 2 <= BatchSize else 1
        )
        for _QuotaIndex in range(MinimumQuota):
            for Signal in ActiveSignals:
                Requests = RequestQueues[Signal]
                if not Requests or len(Selected) >= BatchSize:
                    continue
                Selected.append((Signal, Requests.popleft()))
        SignalRank = {Signal: Index for Index, Signal in enumerate(SignalOrder)}
        ScarcityOrder = tuple(sorted(
            SignalOrder,
            key=lambda Signal: (
                max(0, int(CandidateCountsBySignal.get(Signal, 0))),
                SignalRank[Signal],
            ),
        ))
        while len(Selected) < BatchSize:
            NextSignal = next(
                (
                    Signal
                    for Signal in ScarcityOrder
                    if RequestQueues[Signal]
                ),
                None,
            )
            if NextSignal is None:
                break
            Selected.append((NextSignal, RequestQueues[NextSignal].popleft()))
        return tuple(Selected)
    while len(Selected) < BatchSize:
        Advanced = False
        for Signal in SignalOrder:
            Requests = RequestQueues[Signal]
            if not Requests:
                continue
            Selected.append((Signal, Requests.popleft()))
            Advanced = True
            if len(Selected) >= BatchSize:
                break
        if not Advanced:
            break
    return tuple(Selected)

def SelectPendingExactAssignmentCompletionRequestIndices(
    CandidateSignal: str,
    RequestCount: int,
    AttemptTier: str,
    CompletionAttempts: set[tuple[str, int, str]],
) -> set[int]:
    """Do not retry cut-independent route searches when the exact cut changes."""
    if RequestCount < 0:
        raise ValueError("request count cannot be negative")
    return {
        RequestIndex
        for RequestIndex in range(RequestCount)
        if (
            CandidateSignal,
            RequestIndex,
            AttemptTier,
        ) not in CompletionAttempts
    }

def SelectExactAssignmentCompletionReserveMilliseconds(
    AdvancePlacementOnExhaustedExactCut: bool,
    TerminalCount: int,
    HasValidatedLocalClaims: bool,
    MaximumRuntimeSeconds: float,
) -> int:
    """Reserve only the work that can follow exact-cut completion."""
    if TerminalCount < 0:
        raise ValueError("terminal count cannot be negative")
    if MaximumRuntimeSeconds <= 0:
        raise ValueError("maximum runtime must be positive")
    if AdvancePlacementOnExhaustedExactCut:
        # This path raises placement feedback immediately when completion
        # remains impossible. A successful capacity-one assignment has zero
        # overflow and returns after bounded envelope/layer selection, so a
        # broad negotiated-routing reserve would be unreachable either way.
        return 1_000
    if TerminalCount <= 256 and HasValidatedLocalClaims:
        return min(
            90_000,
            max(5_000, int(MaximumRuntimeSeconds * 250)),
        )
    return 15_000

def ShouldContinueDistinctExactCutFrontier(
    AdvancePlacementOnExhaustedExactCut: bool,
    PriorCutKeys: tuple[tuple[str, ...], ...],
    BaseRoundLimit: int,
) -> bool:
    """Spend one bounded continuation on a strictly advancing exact frontier."""
    return (
        AdvancePlacementOnExhaustedExactCut
        and BaseRoundLimit > 0
        and len(PriorCutKeys) == BaseRoundLimit
        and len(set(PriorCutKeys)) == len(PriorCutKeys)
    )

def RetainNegotiatedInitialCandidateOption(
    InitialCandidateOptions: dict[str, dict[str, NetRouteCandidate]],
    Signal: str,
    Candidate: NetRouteCandidate,
    PassIndex: int,
) -> bool:
    """Publish a pass-zero repair tree into the exact assignment domain."""
    if PassIndex != 0:
        return False
    InitialCandidateOptions.setdefault(Signal, {})[
        Candidate.CandidateId
    ] = Candidate
    return True

def CountPriorCandidateFailureFingerprint(
    EscalationHistory: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    CandidateFailureFingerprint: str,
) -> int:
    """Count completed candidate retries with the exact same work result."""
    return sum(
        1
        for Entry in EscalationHistory
        if (
            isinstance(Entry, dict)
            and Entry.get("Stage") == "CandidateGeneration"
            and Entry.get("CandidateFailureFingerprint")
            == CandidateFailureFingerprint
        )
    )

def CountPriorCandidateRequestDomainFingerprint(
    EscalationHistory: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    CandidateRequestDomainFingerprint: str,
) -> int:
    """Count retries which evaluated the identical native request domain."""
    return sum(
        1
        for Entry in EscalationHistory
        if (
            isinstance(Entry, dict)
            and Entry.get("Stage") == "CandidateGeneration"
            and Entry.get("CandidateRequestDomainFingerprint")
            == CandidateRequestDomainFingerprint
        )
    )

def BuildCandidateStarvationClassFingerprint(
    Signal: str,
    Diagnostics: dict[str, object],
) -> str:
    """Fingerprint a zero-result physical domain independent of window size."""
    return BuildStableFingerprint({
        "Signal": Signal,
        "Materialized": int(Diagnostics.get("Materialized", 0)),
        "RoutedTrees": int(Diagnostics.get("RoutedTrees", 0)),
        "Rejections": Diagnostics.get("Rejections", {}),
        "SourcePortals": int(Diagnostics.get("SourcePortals", 0)),
        "TargetPortals": int(Diagnostics.get("TargetPortals", 0)),
        "ForeignBlockedNodes": int(
            Diagnostics.get("ForeignBlockedNodes", 0)
        ),
        "SeedNodes": int(Diagnostics.get("SeedNodes", 0)),
    })

def CountPriorCandidateStarvationClassFingerprint(
    EscalationHistory: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    CandidateStarvationClassFingerprint: str,
) -> int:
    """Count equivalent empty physical domains across bounded windows."""
    return sum(
        1
        for Entry in EscalationHistory
        if (
            isinstance(Entry, dict)
            and Entry.get("Stage") == "CandidateGeneration"
            and Entry.get("CandidateStarvationClassFingerprint")
            == CandidateStarvationClassFingerprint
        )
    )

def ShouldRetainUnaffectedCandidatesForControl(Action: str) -> bool:
    """Retain exact unaffected routes across signal-scoped controls.

    Portal and reservation controls may change the candidate domain for the
    reported offender, but an already materialized route for another signal
    remains a valid physical candidate.  The authoritative assignment still
    checks its claims against every new candidate, so discarding sixty-one
    unrelated domains only repeats native tree search without adding proof.
    """
    return Action in {
        "regenerate-affected-candidates",
        "increase-guide-lane-diversity",
        "add-routing-layer",
        "alternate-portal-slots",
        "diversify-repeated-candidate-cut",
        "try-bounded-unreserved-portals",
        "final-bounded-unreserved-portals",
        "alternate-complete-cluster-interface-lease",
        "exclude-candidate-unrealizable-cluster-lease-template",
    }

def ShouldRetryCompleteClusterLeaseStateBeforePlacement(
    AdaptiveRoutingEnabled: bool,
    TopologyRequiresJointPortfolio: bool,
    CompleteClusterInterfaceAccess: bool,
    HasClusterBoundaryLeaseReservations: bool,
    ReservationVariant: int,
    SkipStrictPortalReservation: bool,
    MaximumPortalReservationAlternatives: int,
) -> bool:
    """Use the configured access states before changing geometry."""
    MaximumCompleteLeaseStates = MaximumPortalReservationAlternatives
    return (
        AdaptiveRoutingEnabled
        and TopologyRequiresJointPortfolio
        and CompleteClusterInterfaceAccess
        and HasClusterBoundaryLeaseReservations
        and ReservationVariant + 1 < MaximumCompleteLeaseStates
        and not SkipStrictPortalReservation
        and MaximumCompleteLeaseStates > 1
    )

def ShouldDiversifyStarvedCompleteClusterLeaseEndpoint(
    TopologyRequiresJointPortfolio: bool,
    CompleteClusterInterfaceAccess: bool,
    HasClusterBoundaryLeaseReservations: bool,
    ReservationVariant: int,
    SkipStrictPortalReservation: bool,
    MaximumPortalReservationAlternatives: int,
    HasCutScopedEndpointDiversification: bool,
) -> bool:
    """Open one exact cut-scoped state after ordinary lease variants."""
    return (
        TopologyRequiresJointPortfolio
        and CompleteClusterInterfaceAccess
        and HasClusterBoundaryLeaseReservations
        and MaximumPortalReservationAlternatives > 0
        and ReservationVariant + 1
        >= MaximumPortalReservationAlternatives
        and not SkipStrictPortalReservation
        and not HasCutScopedEndpointDiversification
    )

def ShouldAdvanceAfterCompleteClusterLeasePortfolio(
    TopologyRequiresJointPortfolio: bool,
    CompleteClusterInterfaceAccess: bool,
    HasClusterBoundaryLeaseReservations: bool,
    ReservationVariant: int,
    SkipStrictPortalReservation: bool,
    MaximumPortalReservationAlternatives: int,
    HasCutScopedEndpointDiversification: bool,
) -> bool:
    """Do not replace a proven capacity-one lease with a duplicate fallback."""
    return (
        TopologyRequiresJointPortfolio
        and CompleteClusterInterfaceAccess
        and HasClusterBoundaryLeaseReservations
        and MaximumPortalReservationAlternatives > 0
        and 0 <= ReservationVariant < MaximumPortalReservationAlternatives
        and not SkipStrictPortalReservation
        and HasCutScopedEndpointDiversification
    )

def HasRepeatedExactPairCut(
    EscalationHistory: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ConflictGraph: dict[str, Any],
) -> bool:
    """Detect one exact pair cut after its endpoints were regenerated once."""
    if (
        ConflictGraph.get("Classification")
        != "portal-coverage-pair-conflict"
    ):
        return False

    def NormalizeEdges(RawEdges: object) -> tuple[tuple[str, str], ...]:
        if not isinstance(RawEdges, (list, tuple)):
            return ()
        Edges: set[tuple[str, str]] = set()
        for RawEdge in RawEdges:
            if not isinstance(RawEdge, (list, tuple)) or len(RawEdge) != 2:
                continue
            First, Second = sorted((str(RawEdge[0]), str(RawEdge[1])))
            Edges.add((First, Second))
        return tuple(sorted(Edges))

    CurrentEdges = NormalizeEdges(
        ConflictGraph.get("PairwiseIncompatibleEdges", ())
    )
    if not CurrentEdges:
        return False
    return any(
        isinstance(Entry, dict)
        and Entry.get("Stage") == "TrackAssignment"
        and Entry.get("Action") == "regenerate-affected-candidates"
        and (
            Entry.get("ConflictClassification")
            == "portal-coverage-pair-conflict"
            or bool(Entry.get("CandidateDomainPairExpansion"))
        )
        and NormalizeEdges(
            Entry.get("PairwiseIncompatibleEdges", ())
        )
        == CurrentEdges
        for Entry in EscalationHistory
    )

def HasCoveredPairCutAfterEndpointExpansion(
    EscalationHistory: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ConflictGraph: dict[str, Any],
) -> bool:
    """Detect a pair cut whose every endpoint was already diversified.

    Candidate regeneration can contract a proved pairwise cut as newly
    materialized alternatives remove some edges.  Repeating the same control
    for the surviving subset is redundant only when the earlier pass was an
    explicit endpoint-only expansion and covered every endpoint still
    reported by the authoritative capacity-one planner.
    """
    if (
        ConflictGraph.get("Classification")
        != "portal-coverage-pair-conflict"
    ):
        return False

    def NormalizeEdges(RawEdges: object) -> frozenset[tuple[str, str]]:
        if not isinstance(RawEdges, (list, tuple)):
            return frozenset()
        return frozenset(
            tuple(sorted((str(RawEdge[0]), str(RawEdge[1]))))
            for RawEdge in RawEdges
            if isinstance(RawEdge, (list, tuple))
            and len(RawEdge) == 2
        )

    CurrentEdges = NormalizeEdges(
        ConflictGraph.get("PairwiseIncompatibleEdges", ())
    )
    if not CurrentEdges:
        return False
    CurrentEndpoints = frozenset(
        Signal for Edge in CurrentEdges for Signal in Edge
    )
    return any(
        isinstance(Entry, dict)
        and Entry.get("Stage") == "TrackAssignment"
        and Entry.get("Action") == "regenerate-affected-candidates"
        and bool(Entry.get("ExactPairEndpointExpansion"))
        and (
            Entry.get("ConflictClassification")
            == "portal-coverage-pair-conflict"
            or bool(Entry.get("CandidateDomainPairExpansion"))
        )
        and bool(PriorEdges := NormalizeEdges(
            Entry.get("PairwiseIncompatibleEdges", ())
        ))
        and CurrentEdges <= PriorEdges
        and CurrentEndpoints <= frozenset(
            str(Signal)
            for Signal in Entry.get("AffectedSignals", ())
        )
        for Entry in EscalationHistory
    )

def ShouldRejectRoutedComponentForeignEscape(
    *,
    HasRoutedComponentTemplate: bool,
    IsSelectedForeignEscape: bool,
    CandidateDiversityLevel: int,
    CandidateCount: int,
) -> bool:
    """Bound lazy global realizability feedback for one passive escape.

    A routed-component foreign witness is a selected architectural decision,
    not an ordinary unconstrained terminal.  Once the ordinary global router
    has tried its initial window and one distinct candidate window without
    producing any legal tree, the witness must return to the exact component
    CSP as a no-good.  Continuing through every global diversity level merely
    retries a component decision while starving the remaining placement
    states of their shared interface budget.
    """
    return bool(
        HasRoutedComponentTemplate
        and IsSelectedForeignEscape
        and CandidateCount == 0
        and CandidateDiversityLevel >= 1
    )

def ImmutableRoutingClaimsBlockedWireNodes(
    Claims: Iterable[RoutingResourceClaims],
) -> frozenset[Position3]:
    """Project immutable physical claims onto forbidden route-wire nodes."""
    ClaimValues = tuple(Claims)
    return frozenset({
        *(
            Position
            for Claim in ClaimValues
            for Position in (
                Claim.WireCells
                | Claim.SupportCells
                | Claim.RequiredAirCells
                | Claim.ElectricalCells
            )
        ),
        *(
            (X, Y + 1, Z)
            for Claim in ClaimValues
            for X, Y, Z in (
                Claim.WireCells
                | Claim.RequiredAirCells
            )
        ),
    })

def FrozenComponentBlockedWireNodes(
    Signal: str,
    FrozenComponentClaims: Iterable[LocalRouteClaim],
) -> frozenset[Position3]:
    """Return wire positions that cannot coexist with frozen component claims.

    Detailed routing searches wire nodes, while final legality also owns
    support and required-air cells.  Project support conflicts back onto the
    candidate wire plane so the native search cannot construct a tree that is
    guaranteed to fail immutable-component validation afterward.
    """
    return ImmutableRoutingClaimsBlockedWireNodes(
        Claim.Claims
        for Claim in FrozenComponentClaims
        if Claim.Signal != Signal
    )

def CountRoutedComponentGlobalNoTreeAttempts(
    EscalationHistory: Iterable[dict[str, object]],
    Signal: str | None = None,
) -> int:
    """Count completed no-tree windows for one net or the component state."""
    return sum(
        1
        for Entry in EscalationHistory
        if (
            Signal is None
            or Signal
            in tuple(Entry.get("AffectedSignals", ()) or ())
        )
        and isinstance(Entry.get("Diagnostics"), dict)
        and int(Entry["Diagnostics"].get("Requests", 0)) > 0
        and int(Entry["Diagnostics"].get("RoutedTrees", 0)) == 0
    )

def FindUnavoidableMandatoryClaimCut(
    MandatoryClaimsBySignal: dict[
        str,
        tuple[RoutingResourceClaims, ...],
    ],
) -> tuple[tuple[str, str], frozenset[Position3]] | None:
    """Find a signal pair whose every fixed-access alternative conflicts."""
    Cuts = FindAllUnavoidableMandatoryClaimCuts(MandatoryClaimsBySignal)
    return Cuts[0] if Cuts else None

def FindAllUnavoidableMandatoryClaimCuts(
    MandatoryClaimsBySignal: dict[
        str,
        tuple[RoutingResourceClaims, ...],
    ],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[tuple[tuple[str, str], frozenset[Position3]], ...]:
    """Find every signal pair whose fixed-access domains cannot coexist."""
    Signals = tuple(sorted(
        Signal
        for Signal, Claims in MandatoryClaimsBySignal.items()
        if Claims
    ))
    Cuts = []
    AlternativePairChecks = 0
    for FirstIndex, FirstSignal in enumerate(Signals):
        for SecondSignal in Signals[FirstIndex + 1:]:
            Unavoidable = True
            for FirstClaims in MandatoryClaimsBySignal[FirstSignal]:
                for SecondClaims in MandatoryClaimsBySignal[SecondSignal]:
                    AlternativePairChecks += 1
                    if (
                        WorkCheck is not None
                        and AlternativePairChecks % 64 == 0
                    ):
                        WorkCheck({
                            "Phase": "mandatory-claim-cut-proof",
                            "FirstSignal": FirstSignal,
                            "SecondSignal": SecondSignal,
                            "AlternativePairChecks": AlternativePairChecks,
                        })
                    if not MandatoryClaimsConflict(
                        FirstClaims,
                        SecondClaims,
                    ):
                        Unavoidable = False
                        break
                if not Unavoidable:
                    break
            if Unavoidable:
                ConflictPositions = {
                    Position
                    for FirstClaims in MandatoryClaimsBySignal[FirstSignal]
                    for SecondClaims in MandatoryClaimsBySignal[SecondSignal]
                    for Position in ClaimConflictPositions(
                        FirstClaims,
                        SecondClaims,
                    )
                }
                Cuts.append((
                    (FirstSignal, SecondSignal),
                    frozenset(ConflictPositions),
                ))
    return tuple(Cuts)

def BuildCompleteMandatoryClaimCutCoverage(
    MandatoryClaimsBySignal: dict[
        str,
        tuple[RoutingResourceClaims, ...],
    ],
    HasMandatoryPortalCut: bool,
) -> dict[str, object] | None:
    """Describe every unavoidable fixed-access pair in one repair batch."""
    Cuts = FindAllUnavoidableMandatoryClaimCuts(
        MandatoryClaimsBySignal
    )
    if not Cuts:
        return None
    CutSignals = tuple(sorted({
        Signal
        for Pair, _Positions in Cuts
        for Signal in Pair
    }))
    CutPositions = frozenset({
        Position
        for _Pair, Positions in Cuts
        for Position in Positions
    })
    return {
        "Classification": (
            "mandatory-boundary-capacity-cut"
            if HasMandatoryPortalCut
            else "portal-coverage-pair-conflict"
        ),
        "ConflictSignals": list(CutSignals),
        "CongestionCutSignals": list(CutSignals),
        "PairwiseIncompatibleEdges": [
            list(Pair) for Pair, _Positions in Cuts
        ],
        "MandatoryAlternativeCounts": {
            Signal: len(MandatoryClaimsBySignal[Signal])
            for Signal in CutSignals
        },
        "MandatoryConflictPositions": [
            list(Position)
            for Position in sorted(CutPositions)[:32]
        ],
        "CandidateCoverageRepairPairs": [
            {
                "Signals": list(Pair),
                "CompatibleAlternatives": 0,
                "TotalAlternatives": (
                    len(MandatoryClaimsBySignal[Pair[0]])
                    * len(MandatoryClaimsBySignal[Pair[1]])
                ),
            }
            for Pair, _Positions in Cuts
        ],
        "CandidateCoverageRepairSignals": list(CutSignals),
    }

def BuildUnavoidableMandatoryClaimCutFailure(
    Cuts: tuple[
        tuple[tuple[str, str], frozenset[Position3]],
        ...,
    ],
    StageTimings: dict[str, float] | None = None,
    *,
    PairwiseNoGoodEdges: Iterable[tuple[str, str]] = (),
) -> RoutingFailure:
    """Preserve the complete exact portal-cut batch for placement repair."""
    if not Cuts:
        raise ValueError("Cuts must contain at least one unavoidable pair")
    PairwiseEdges = tuple(sorted({
        tuple(sorted((str(First), str(Second))))
        for (First, Second), _Positions in Cuts
        if str(First) != str(Second)
    }))
    ConflictSignals = tuple(sorted({
        Signal
        for Edge in PairwiseEdges
        for Signal in Edge
    }))
    ConflictPositions = frozenset(
        Position
        for _Edge, Positions in Cuts
        for Position in Positions
    )
    MandatoryAccessProof = BuildGeneratedFixedPortalDomainExhaustionProof(
        ConflictPositions,
        len(PairwiseEdges),
    )
    CertifiedPairwiseEdges = tuple(sorted({
        tuple(sorted(map(str, Edge)))
        for Edge in PairwiseNoGoodEdges
        if len(tuple(Edge)) == 2 and str(tuple(Edge)[0]) != str(tuple(Edge)[1])
    }))
    return RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="InitialCandidateAssignment",
        AffectedNets=ConflictSignals,
        Locations=tuple(sorted(ConflictPositions))[:32],
        RepairActions=("RelocateAffectedClusters",),
        Detail=(
            "every generated fixed portal/access alternative conflicts "
            "before native route-tree generation"
        ),
        Diagnostics={
            "MandatoryConflictPairCount": len(PairwiseEdges),
            "MandatoryConflictPositionCount": len(ConflictPositions),
            "MandatoryAccessProof": MandatoryAccessProof,
            "PairwisePortReservationNoGoodProofComplete": bool(
                CertifiedPairwiseEdges
            ),
            "PairwisePortReservationNoGoodEdges": [
                list(Edge) for Edge in CertifiedPairwiseEdges
            ],
            "StageTimingsSeconds": dict(StageTimings or {}),
            "ConflictGraph": {
                "Classification": "mandatory-boundary-capacity-cut",
                "ConflictSignals": list(ConflictSignals),
                "CongestionCutSignals": list(ConflictSignals),
                "RelocationSignals": list(ConflictSignals),
                "PriorityRelocationSignals": list(ConflictSignals),
                "PairwiseIncompatibleEdges": [
                    list(Edge) for Edge in PairwiseEdges
                ],
            },
        },
    )

def BuildBoundedPortfolioPortalSliceAdvanceFailure(
    Cuts: tuple[
        tuple[tuple[str, str], frozenset[Position3]],
        ...,
    ],
    StageTimings: dict[str, float] | None = None,
) -> RoutingFailure:
    """Advance a sibling without promoting an intentionally incomplete cut."""
    if not Cuts:
        raise ValueError("Cuts must contain at least one bounded pair")
    ConflictSignals = tuple(sorted({
        Signal
        for Pair, _Positions in Cuts
        for Signal in Pair
    }))
    ConflictPositions = frozenset(
        Position
        for _Pair, Positions in Cuts
        for Position in Positions
    )
    return RoutingFailure(
        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
        Stage="InitialCandidateAssignment",
        AffectedNets=ConflictSignals,
        Locations=tuple(sorted(ConflictPositions))[:32],
        RepairActions=("AdvancePlacementCandidate",),
        Detail=(
            "the bounded retained-candidate portal slice produced no "
            "compatible mandatory-access tuple; the incomplete portal "
            "domain is not assignment-cut evidence"
        ),
        Diagnostics={
            "Action": "advance-placement-after-bounded-portal-slice",
            "BoundedPortalSlice": True,
            "CompleteAssignmentCutProof": False,
            "ObservedConflictPairCount": len(Cuts),
            "ObservedConflictPositionCount": len(ConflictPositions),
            "StageTimingsSeconds": dict(StageTimings or {}),
        },
    )

def BuildMandatoryPortalTupleSelfConflictFailure(
    Evidence: tuple[MandatoryPortalTupleSelfConflictEvidence, ...],
    *,
    StageTimings: dict[str, float] | None = None,
) -> RoutingFailure:
    """Publish complete empty net-wide portal domains as one placement cut."""
    if not Evidence:
        raise ValueError("Evidence must contain at least one exact proof")
    Signals = tuple(sorted({
        Value.Signal for Value in Evidence
    }))
    ConflictResources = tuple(sorted({
        Resource
        for Value in Evidence
        for Resource in Value.ConflictResources
    }, key=str))
    ConflictPositions = tuple(sorted({
        Resource.Position
        for Resource in ConflictResources
    }))
    AnonymousRecords = tuple(sorted(
        (
            BuildStableFingerprint(Value.AnonymousRecord()),
            Value.AnonymousRecord(),
        )
        for Value in Evidence
    ))
    ConflictFingerprint = BuildStableFingerprint({
        "Kind": "generated-net-wide-portal-tuple-domain-exhausted",
        "SignalProofs": AnonymousRecords,
    })
    Proof = {
        "Kind": "generated-net-wide-portal-tuple-domain-exhausted",
        "Complete": True,
        "BudgetExhausted": False,
        "DeadlineExceeded": False,
        "SignalCount": len(Signals),
        "SignalProofs": [
            {
                "Signal": Value.Signal,
                "CompletePortalTupleCount": (
                    Value.CompletePortalTupleCount
                ),
                "EvaluatedPortalTupleCount": (
                    Value.EvaluatedPortalTupleCount
                ),
                "TerminalPortalDomainCounts": list(
                    Value.TerminalPortalDomainCounts
                ),
                "ConflictResourceCount": len(
                    Value.ConflictResources
                ),
                "PortalDomainCertificateFingerprint": (
                    Value.PortalDomainCertificateFingerprint
                ),
                "PhysicalAssemblyPlanFingerprint": (
                    Value.PhysicalAssemblyPlanFingerprint
                ),
                "ResourceGraphFingerprint": (
                    Value.ResourceGraphFingerprint
                ),
                "TechnologyFingerprint": Value.TechnologyFingerprint,
                "PlacementFingerprint": Value.PlacementFingerprint,
                "InterfaceFingerprint": Value.InterfaceFingerprint,
                "SeamFingerprint": Value.SeamFingerprint,
                "PortalRequestDomainFingerprint": (
                    Value.PortalRequestDomainFingerprint
                ),
                "ExactAttachmentValidationFingerprint": (
                    Value.ExactAttachmentValidationFingerprint
                ),
                "AnonymousFingerprint": BuildStableFingerprint(
                    Value.AnonymousRecord()
                ),
            }
            for Value in sorted(Evidence, key=lambda Item: Item.Signal)
        ],
        "ConflictResourceCount": len(ConflictResources),
        "ConflictPositionCount": len(ConflictPositions),
        "ConflictFingerprint": ConflictFingerprint,
    }
    CompletePhysicalAssemblyPortalProof = all(
        Value.PortalDomainCertificateFingerprint
        and Value.PhysicalAssemblyPlanFingerprint
        and Value.ResourceGraphFingerprint
        and Value.TechnologyFingerprint
        and Value.PlacementFingerprint
        and Value.InterfaceFingerprint
        and Value.SeamFingerprint
        and Value.PortalRequestDomainFingerprint
        and Value.ExactAttachmentValidationFingerprint
        for Value in Evidence
    )
    return RoutingFailure(
        Reason=RoutingFailureReason.NoPinAccessPattern,
        Stage="InitialCandidateAssignment",
        AffectedNets=Signals,
        Locations=ConflictPositions[:32],
        Resources=tuple(
            str(Resource)
            for Resource in ConflictResources[:32]
        ),
        RepairActions=("RelocateAffectedClusters",),
        Detail=(
            "every generated net-wide source/target portal tuple has an "
            "internal wire, support, or headroom ownership conflict"
        ),
        Diagnostics={
            "Action": "relocate-empty-net-wide-portal-tuple-domain",
            "ConflictFingerprint": ConflictFingerprint,
            "MandatoryAccessProof": Proof,
            "GlobalPlanDomainComplete": (
                CompletePhysicalAssemblyPortalProof
            ),
            "CompleteAssignmentCutProof": (
                CompletePhysicalAssemblyPortalProof
            ),
            "IndependentEmptyCandidateDomainSignals": (
                list(Signals)
                if CompletePhysicalAssemblyPortalProof
                else []
            ),
            "StageTimingsSeconds": dict(StageTimings or {}),
            "ConflictGraph": {
                "Classification": "mandatory-access-self-conflict",
                "ConflictSignals": list(Signals),
                "NoCandidateSignals": list(Signals),
                "RelocationSignals": list(Signals),
                "PriorityRelocationSignals": list(Signals),
                "CandidateCounts": {
                    Signal: 0 for Signal in Signals
                },
            },
        },
    )

def BuildGeneratedFixedPortalDomainExhaustionProof(
    ConflictPositions: Iterable[Position3],
    ConflictPairCount: int,
) -> dict[str, object]:
    """Fingerprint one exhaustively generated portal-domain cut anonymously."""
    Positions = tuple(
        tuple(int(Coordinate) for Coordinate in Position)
        for Position in ConflictPositions
    )
    MinimumX = min((Position[0] for Position in Positions), default=0)
    MinimumY = min((Position[1] for Position in Positions), default=0)
    MinimumZ = min((Position[2] for Position in Positions), default=0)
    NormalizedPositions = tuple(sorted({
        (
            Position[0] - MinimumX,
            Position[1] - MinimumY,
            Position[2] - MinimumZ,
        )
        for Position in Positions
    }))
    return {
        "Kind": "generated-fixed-portal-domain-exhausted",
        "Complete": True,
        "PortalTupleDomainComplete": True,
        "ProofScope": "complete-portal-tuple-domain",
        "BudgetExhausted": False,
        "DeadlineExceeded": False,
        "ConflictPairCount": int(ConflictPairCount),
        "ConflictPositionCount": len(Positions),
        "ConflictFingerprint": BuildStableFingerprint({
            "NormalizedConflictPositions": NormalizedPositions,
            "ConflictPairCount": int(ConflictPairCount),
        }),
    }

def PortalTupleFeasibilityDomainIsComplete(
    FeasibilityValues: Iterable[dict[str, object]],
    *,
    ExpectedLayers: Iterable[int] | None = None,
) -> bool:
    """Return whether every prepared layer exhausted its portal product."""
    Values = tuple(FeasibilityValues)
    ExpectedLayerSet = (
        frozenset(int(Layer) for Layer in ExpectedLayers)
        if ExpectedLayers is not None
        else None
    )
    return bool(
        Values
        and (
            ExpectedLayerSet is None
            or frozenset(int(Value["Layer"]) for Value in Values)
            == ExpectedLayerSet
        )
        and all(
            bool(Value["PortalTupleDomainComplete"])
            if "PortalTupleDomainComplete" in Value
            else (
                int(Value["CompletePortalTupleCount"]) > 0
                and int(Value["EvaluatedPortalTupleCount"])
                >= int(Value["CompletePortalTupleCount"])
            )
            for Value in Values
        )
    )

def PortalTupleEmptyProofDomainIsComplete(
    FeasibilityValues: Iterable[dict[str, object]],
    *,
    ExpectedLayers: Iterable[int],
) -> bool:
    """Require exhaustive emptiness on every eligible trunk layer."""
    Values = tuple(FeasibilityValues)
    ExpectedLayerSet = frozenset(int(Layer) for Layer in ExpectedLayers)
    return bool(
        Values
        and frozenset(int(Value["Layer"]) for Value in Values)
        == ExpectedLayerSet
        and all(
            bool(Value.get("PortalTupleEmptyProofComplete", False))
            and int(Value.get("LegalPortalTupleCount", 0)) == 0
            for Value in Values
        )
    )

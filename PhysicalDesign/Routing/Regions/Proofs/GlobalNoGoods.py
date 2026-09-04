"""Authoritative global-plan no-goods learned at the component boundary."""

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

from ..Planning.PhysicalPlanning import BuildPhysicalComponentAssemblyChoiceFingerprint, BuildPhysicalComponentPortSolverCacheKey, BuildPhysicalGlobalPlanDependencyFingerprint, BuildPhysicalPortApertureContractFingerprint, BuildPhysicalRequestAperturePortNoGood, PreservePhysicalComponentAssemblyPlanDomainContinuation
from .Validation import SelectPhysicalComponentGlobalContractRecommendation
def RecordPhysicalComponentGlobalPlanNoGood(
    Failure: RoutingFailure,
    Plan: PhysicalComponentAssemblyPlan,
    Resources: Any,
    *,
    ShouldStop: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Record the smallest port contract justified by a complete cut.

    Global planning happens before local compilation, so its complete proof
    may reject only physical choices on which that proof depends.  Explicit
    feedthroughs remain part of the exact assembly identity unless the proof
    independently certifies that they cannot affect the cut.
    """
    Diagnostics = dict(Failure.Diagnostics or {})
    if not bool(
        Diagnostics.get("GlobalPlanDomainComplete", False)
        and Diagnostics.get("CompleteAssignmentCutProof", False)
    ):
        raise ValueError(
            "physical global-plan no-good requires a complete domain proof"
        )
    DependencySignals = frozenset(str(Signal) for Signal in (
        Diagnostics.get("AssemblyPlanDependentPortSignals", ()) or ()
    ))
    AssemblyDependencySignals = frozenset(str(Signal) for Signal in (
        Diagnostics.get("AssemblyPlanDependencySignals", ()) or ()
    ))
    Feedthroughs = tuple(getattr(Plan, "Feedthroughs", ()))
    FeedthroughIndependenceProved = bool(Diagnostics.get(
        "AssemblyPlanFeedthroughIndependentProofComplete",
        False,
    ))
    RequiresExactAssemblyChoice = bool(
        Feedthroughs and not FeedthroughIndependenceProved
    )
    DependencyPorts = tuple(
        Port for Port in Plan.Ports if Port.Signal in DependencySignals
    )
    if frozenset(Port.Signal for Port in DependencyPorts) != DependencySignals:
        raise ValueError(
            "physical global-plan proof names an undeclared component port"
        )
    DeclaredDependencyFingerprint = str(
        Diagnostics.get("GlobalPlanDependencyFingerprint", "")
    )
    DependencyProjectionProofComplete = bool(
        Diagnostics.get("CompleteAssignmentCutProof", False)
        and Diagnostics.get(
            "AssemblyPlanDependencyIdentityComplete",
            False,
        )
        and AssemblyDependencySignals
        and AssemblyDependencySignals == DependencySignals
        and DeclaredDependencyFingerprint
        and DeclaredDependencyFingerprint
        == BuildPhysicalGlobalPlanDependencyFingerprint(
            Plan,
            AssemblyDependencySignals,
        )
        and Diagnostics.get("GlobalPlanCutFamilyFingerprint", "")
        and Diagnostics.get("GlobalPlanProofFingerprint", "")
    )
    RequestApertureFactorNoGood = frozenset(
        (str(Key[0]), str(Key[1]))
        for Key in (
            Diagnostics.get("RequestApertureFactorNoGood", ()) or ()
        )
        if isinstance(Key, (tuple, list)) and len(Key) == 2
    )
    RequestApertureProofSignals = frozenset(
        Signal for Signal, _FingerprintValue
        in RequestApertureFactorNoGood
    )
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    PortSolverDomainFingerprint = str(
        getattr(Preparation, "DomainFingerprint", "")
    )
    DeclaredRequestAperturePortNoGood = frozenset(
        (str(Key[0]), str(Key[1]))
        for Key in (
            Diagnostics.get("RequestAperturePortNoGood", ()) or ()
        )
        if isinstance(Key, (tuple, list)) and len(Key) == 2
    )
    ExpectedRequestAperturePortNoGood = (
        BuildPhysicalRequestAperturePortNoGood(
            Plan,
            RequestApertureFactorNoGood,
            SignalLocalRequestFactorProofComplete=bool(
                Diagnostics.get(
                    "SignalLocalRequestFactorProofComplete",
                    False,
                )
            ),
            PortSolverCacheKey=BuildPhysicalComponentPortSolverCacheKey(
                PortSolverDomainFingerprint
            ),
        )
        if RequestApertureFactorNoGood and PortSolverDomainFingerprint
        else frozenset()
    )
    if (
        DeclaredRequestAperturePortNoGood
        and DeclaredRequestAperturePortNoGood
        != ExpectedRequestAperturePortNoGood
    ):
        raise ValueError(
            "request/aperture port no-good identity mismatch"
        )
    RequestApertureProofComplete = bool(
        Diagnostics.get("RequestApertureFactorProofComplete", False)
        and RequestApertureFactorNoGood
        and RequestApertureProofSignals == DependencySignals
        and PortSolverDomainFingerprint
    )
    IndependentEmptyDomainSignals = frozenset(
        str(Signal)
        for Signal in (
            Diagnostics.get(
                "IndependentEmptyCandidateDomainSignals",
                (),
            )
            or ()
        )
    )
    if not IndependentEmptyDomainSignals <= DependencySignals:
        raise ValueError(
            "independent empty route-domain proof names an unrelated signal"
        )
    # A complete assignment cut carries its exact dependency closure.  When
    # every dependency is an assembly port and its plan-bound identity
    # fingerprint validates, unrelated ports cannot affect that proof and
    # must not inflate the learned clause.  Request/aperture starvation keeps
    # its stronger prepared-domain scoped representation below.  Feedthroughs
    # still require exact assembly-choice identity unless their independence
    # is separately certified.
    Ports = (
        tuple(
            Port for Port in Plan.Ports
            if Port.Signal in IndependentEmptyDomainSignals
        )
        if IndependentEmptyDomainSignals
        else DependencyPorts
        if RequestApertureProofComplete
        else DependencyPorts
        if DependencyProjectionProofComplete
        else tuple(Plan.Ports)
    )
    ReservationKeys = frozenset(
        (
            Port.Signal,
            BuildPhysicalPortApertureContractFingerprint(Port),
        )
        for Port in Ports
    )
    PortSolverScopeKey = (
        (
            min(DependencySignals),
            "local-signal-domain:"
            + BuildPhysicalComponentPortSolverCacheKey(
                PortSolverDomainFingerprint
            ),
        )
        if RequestApertureProofComplete
        else None
    )
    RequestGlobalDeterminantKeys = (
        frozenset(
            (
                Port.Signal,
                BuildPhysicalPortGlobalContractFingerprint(Port),
            )
            for Port in Plan.Ports
        )
        if RequestApertureProofComplete
        else frozenset()
    )
    RejectedRequestApertureSet = frozenset((
        *(
            DeclaredRequestAperturePortNoGood
            or frozenset((
                *RequestGlobalDeterminantKeys,
                *ReservationKeys,
                *((
                    (PortSolverScopeKey,)
                    if PortSolverScopeKey is not None
                    else ()
                )),
            ))
        ),
    ))
    ReservationKeyBySignal = {
        Signal: (Signal, Fingerprint)
        for Signal, Fingerprint in ReservationKeys
    }
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    PairwiseEdges = tuple(
        tuple(map(str, Edge))
        for Edge in (
            Diagnostics.get(
                "PairwisePortReservationNoGoodEdges",
                (),
            )
            or (
                ConflictGraph.get("PairwiseIncompatibleEdges", ())
                if isinstance(ConflictGraph, dict)
                else ()
            )
        )
        if (
            isinstance(Edge, (list, tuple))
            and len(Edge) == 2
            and str(Edge[0]) in ReservationKeyBySignal
            and str(Edge[1]) in ReservationKeyBySignal
        )
    )
    PairwiseProofComplete = bool(Diagnostics.get(
        "PairwisePortReservationNoGoodProofComplete",
        False,
    ))
    PairwiseReservationSets = (
        frozenset(
            frozenset((
                ReservationKeyBySignal[First],
                ReservationKeyBySignal[Second],
            ))
            for First, Second in PairwiseEdges
            if (
                First != Second
                and First in ReservationKeyBySignal
                and Second in ReservationKeyBySignal
            )
        )
        if PairwiseProofComplete
        else frozenset()
    )
    CompiledPairRelationDiagnostics: list[dict[str, object]] = []
    PreparedFactorCache = getattr(
        Resources,
        "PhysicalBoundaryMandatoryPortalFactorDomainCache",
        {},
    )
    PairwiseEdgeSignals = frozenset(
        Signal for Edge in PairwiseEdges for Signal in Edge
    )
    PreparedPairFactorDomains = tuple(
        Value
        for Key, Value in PreparedFactorCache.items()
        if (
            isinstance(Key, tuple)
            and len(Key) == 3
            and Key[0] == PortSolverDomainFingerprint
            and str(Key[1]) in PairwiseEdgeSignals
        )
    )
    HasPreparedPairFactorArchitecture = bool(
        PreparedPairFactorDomains
        and {
            str(getattr(Value, "Signal", ""))
            for Value in PreparedPairFactorDomains
        } == PairwiseEdgeSignals
        and all(
            bool(getattr(Value, "Complete", False))
            for Value in PreparedPairFactorDomains
        )
    )
    PreparedPairFactorArchitectureDiagnostics = {
        "Available": HasPreparedPairFactorArchitecture,
        "ExpectedSignals": sorted(PairwiseEdgeSignals),
        "PreparedSignalCount": len({
            str(getattr(Value, "Signal", ""))
            for Value in PreparedPairFactorDomains
        }),
        "FactorDomainCount": len(PreparedPairFactorDomains),
        "CompleteFactorDomainCount": sum(
            int(bool(getattr(Value, "Complete", False)))
            for Value in PreparedPairFactorDomains
        ),
        "IncompleteSignals": sorted({
            str(getattr(Value, "Signal", ""))
            for Value in PreparedPairFactorDomains
            if not bool(getattr(Value, "Complete", False))
        }),
        "OtherPreparedDomainFactorCount": sum(
            1
            for Key in PreparedFactorCache
            if (
                isinstance(Key, tuple)
                and len(Key) == 3
                and Key[0] != PortSolverDomainFingerprint
            )
        ),
    }
    PreparedPairOptionCounts = {
        Signal: len({
            str(getattr(Value, "ApertureContractFingerprint", ""))
            for Value in PreparedPairFactorDomains
            if str(getattr(Value, "Signal", "")) == Signal
        })
        for Signal in PairwiseEdgeSignals
    }
    PreparedPairOptionProduct = 1
    for Signal in sorted(PreparedPairOptionCounts):
        PreparedPairOptionProduct *= PreparedPairOptionCounts[Signal]
    # The relation compiler uses a shared symbolic frontier quotient for
    # larger products, so the gate bounds published certificate size rather
    # than forcing the exterior router to rediscover each incompatible pair
    # one contract at a time.
    MaximumEagerPreparedPairOptionProduct = 65_536
    CompletePreparedPairSignals = frozenset(
        Signal
        for Signal in PairwiseEdgeSignals
        if (
            PreparedPairOptionCounts.get(Signal, 0) > 0
            and all(
                bool(getattr(Value, "Complete", False))
                for Value in PreparedPairFactorDomains
                if str(getattr(Value, "Signal", "")) == Signal
            )
        )
    )
    PreparedPairEdges = tuple(sorted({
        tuple(sorted((str(First), str(Second))))
        for First, Second in PairwiseEdges
        if str(First) != str(Second)
    }))
    EligiblePreparedPairRelations = tuple(sorted(
        (
            (
                PreparedPairOptionCounts.get(Pair[0], 0)
                * PreparedPairOptionCounts.get(Pair[1], 0)
            ),
            Pair,
        )
        for Pair in PreparedPairEdges
        if (
            frozenset(Pair) <= CompletePreparedPairSignals
            and 0
            < (
                PreparedPairOptionCounts.get(Pair[0], 0)
                * PreparedPairOptionCounts.get(Pair[1], 0)
            )
            <= MaximumEagerPreparedPairOptionProduct
        )
    ))
    ShouldCompilePreparedPairRelation = bool(
        PairwiseProofComplete
        and EligiblePreparedPairRelations
    )
    PreparedPairFactorArchitectureDiagnostics.update({
        "OptionCountsBySignal": PreparedPairOptionCounts,
        "OptionProduct": PreparedPairOptionProduct,
        "MaximumEagerOptionProduct": (
            MaximumEagerPreparedPairOptionProduct
        ),
        "EagerCompilationSelected": (
            ShouldCompilePreparedPairRelation
        ),
    })
    if ShouldCompilePreparedPairRelation:
        CompiledPairwiseReservationSets = set(PairwiseReservationSets)
        # Compile one smallest eligible pair per exterior failure.  The exact
        # relation itself runs to completion under the shared typed deadline;
        # imposing a second certificate-count quantum here would repeatedly
        # revisit the same cached state index without ever publishing its
        # complete binary clauses.
        _SelectedPairOptionProduct, Pair = (
            EligiblePreparedPairRelations[0]
        )
        for Pair in (Pair,):
            Relation = (
                BoundaryRelations
                .CompilePhysicalBoundaryMandatoryPortalPairRelation(
                Preparation,
                Pair,
                Resources,
                ShouldStop=ShouldStop,
                MaximumNewCertificates=None,
                PreferredApertureContractsBySignal={
                    Port.Signal: (
                        BuildPhysicalPortApertureContractFingerprint(Port)
                    )
                    for Port in Plan.Ports
                    if Port.Signal in Pair
                },
                )
            )
            CompiledPairRelationDiagnostics.append({
                "RelationFingerprint": Relation.RelationFingerprint,
                "Signals": list(Relation.Signals),
                "ExpectedOptionPairCount": (
                    Relation.ExpectedOptionPairCount
                ),
                "CertificateCount": len(Relation.Certificates),
                "UnsatisfiableClauseCount": len(
                    Relation.UnsatisfiableApertureClauses
                ),
                "ForeignDependencyCertificateCount": (
                    Relation.ForeignDependencyCertificateCount
                ),
                "FactorCertificateCount": getattr(
                    Relation, "FactorCertificateCount", 0
                ),
                "FactorStateCount": getattr(
                    Relation, "FactorStateCount", 0
                ),
                "UniqueClaimStateCountsBySignal": dict(getattr(
                    Relation,
                    "UniqueClaimStateCountsBySignal",
                    (),
                )),
                "FactorExpansionCount": getattr(
                    Relation, "FactorExpansionCount", 0
                ),
                "CompatibilityIndexStatePairUpperBound": getattr(
                    Relation,
                    "CompatibilityIndexStatePairUpperBound",
                    0,
                ),
                "Complete": Relation.Complete,
            })
            CompiledPairwiseReservationSets.update(
                Relation.UnsatisfiableApertureClauses
            )
        PairwiseReservationSets = frozenset(
            CompiledPairwiseReservationSets
        )
    Scope = "none"
    RejectedAssemblyChoiceFingerprint = ""
    if RequiresExactAssemblyChoice:
        ComputedAssemblyChoiceFingerprint = (
            BuildPhysicalComponentAssemblyChoiceFingerprint(Plan)
        )
        DeclaredAssemblyChoiceFingerprint = str(getattr(
            Plan,
            "AssemblyChoiceFingerprint",
            "",
        ))
        if (
            DeclaredAssemblyChoiceFingerprint
            and DeclaredAssemblyChoiceFingerprint
            != ComputedAssemblyChoiceFingerprint
        ):
            raise ValueError(
                "physical assembly choice fingerprint identity mismatch"
            )
        RejectedAssemblyChoiceFingerprint = (
            DeclaredAssemblyChoiceFingerprint
            or ComputedAssemblyChoiceFingerprint
        )
        RejectedChoices = getattr(
            Resources,
            "RejectedPhysicalComponentAssemblyChoiceFingerprints",
            None,
        )
        if RejectedChoices is None:
            RejectedChoices = set()
            Resources.RejectedPhysicalComponentAssemblyChoiceFingerprints = (
                RejectedChoices
            )
        RejectedChoices.add(RejectedAssemblyChoiceFingerprint)
        Scope = "exact-assembly-port-feedthrough-choice"
    elif IndependentEmptyDomainSignals:
        for Signal, ReservationFingerprint in ReservationKeys:
            (
                Resources
                .RejectedPhysicalComponentPortReservationsBySignal
                .setdefault(Signal, set())
                .add(ReservationFingerprint)
            )
        Scope = "independent-empty-global-route-domain"
    elif RequestApertureProofComplete:
        (
            Resources
            .RejectedPhysicalComponentPortReservationSets
            .add(RejectedRequestApertureSet)
        )
        Scope = "request-aperture-factor-port-set"
    elif len(ReservationKeys) == 1:
        Signal, ReservationFingerprint = next(iter(ReservationKeys))
        (
            Resources
            .RejectedPhysicalComponentPortReservationsBySignal
            .setdefault(Signal, set())
            .add(ReservationFingerprint)
        )
        Scope = "single-port-aperture-reservation"
    elif PairwiseReservationSets:
        (
            Resources
            .RejectedPhysicalComponentPortReservationSets
            .update(PairwiseReservationSets)
        )
        Scope = "pairwise-port-aperture-reservation-sets"
    elif ReservationKeys:
        (
            Resources
            .RejectedPhysicalComponentPortReservationSets
            .add(ReservationKeys)
        )
        Scope = "exact-assembly-port-aperture-set"
    CorridorCache = getattr(
        Resources,
        "PhysicalPortCorridorDomainCache",
        {},
    )
    Recommendation = (
        SelectPhysicalComponentGlobalContractRecommendation(
            CorridorCache.values(),
            (Port.Signal for Port in Plan.Ports),
            RejectedSets=(
                Resources.RejectedPhysicalComponentPortReservationSets
            ),
            CompatibilityCache=getattr(
                Resources,
                "PhysicalGlobalAssignmentArcCompatibilityCache",
                None,
            ),
            ResourceGraphFingerprint=str(getattr(
                Plan,
                "ResourceGraphFingerprint",
                "",
            )),
            TechnologyFingerprint=str(getattr(
                Plan,
                "TechnologyFingerprint",
                "",
            )),
        )
        if CorridorCache
        else None
    )
    RecommendedContracts = (
        {
            Signal: Factor.PortGlobalContractFingerprint
            for Signal, Factor in Recommendation.items()
        }
        if Recommendation is not None
        else {}
    )
    MinimumDeltaPivotSignal = ""
    MinimumDeltaPivotDomainCounts: dict[str, int] = {}
    MinimumDeltaCertifiedExteriorDomainCounts: dict[str, int] = {}
    MinimumDeltaRetainedContracts: dict[str, str] = {}
    MinimumDeltaRetainedApertures: dict[str, str] = {}
    MinimumDeltaRetainedReservations: dict[str, str] = {}
    if (
        not RecommendedContracts
        and Scope in {
            "exact-assembly-port-aperture-set",
            "request-aperture-factor-port-set",
            "independent-empty-global-route-domain",
        }
    ):
        # A complete higher-order cut rejects the exact tuple, not each of
        # its literals.  Preserve every non-pivot global contract as a soft
        # preference so the next CSP solution changes the smallest useful
        # part of the assembly and can reuse completed exterior domains.
        # Universal conflict hubs are the strongest deterministic pivot;
        # otherwise use the reported failure net or the first dependency.
        RequestAperturePivotSignals = tuple(sorted(
            Signal
            for Signal, Fingerprint in RequestApertureFactorNoGood
            if Fingerprint.startswith("aperture-factor:")
            and Signal in DependencySignals
        ))
        UniversalConflictHubs = (
            ConflictGraph.get("UniversalConflictHubs", {})
            if isinstance(ConflictGraph, dict)
            else {}
        )
        HubSignals = tuple(sorted(
            (
                str(Signal),
                int(
                    Details.get("PairDegree", 0)
                    if isinstance(Details, dict)
                    else 0
                ),
            )
            for Signal, Details in (
                UniversalConflictHubs.items()
                if isinstance(UniversalConflictHubs, dict)
                else ()
            )
            if str(Signal) in DependencySignals
        ))
        RemainingApertureDomains = {
            str(Signal): frozenset(
                BuildPhysicalPortApertureContractFingerprint(Option)
                for Option in Options
                if BuildPhysicalPortApertureContractFingerprint(Option)
                not in (
                    Resources
                    .RejectedPhysicalComponentPortReservationsBySignal
                    .get(str(Signal), set())
                )
            )
            for Signal, Options in (
                getattr(
                    Preparation,
                    "BoundaryPortReservationsBySignal",
                    (),
                )
                if Preparation is not None
                else ()
            )
            if str(Signal) in DependencySignals
        }
        MinimumDeltaPivotDomainCounts = {
            Signal: len(Fingerprints)
            for Signal, Fingerprints
            in sorted(RemainingApertureDomains.items())
        }
        CertifiedExteriorCoreCandidateCounts = {
            str(Signal): int(Count)
            for Signal, Count in dict(
                Diagnostics.get(
                    "HigherOrderPortReservationNoGoodCandidateCounts",
                    {},
                )
                or {}
            ).items()
            if (
                str(Signal) in DependencySignals
                and int(Count) > 0
            )
        }
        MinimumDeltaCertifiedExteriorDomainCounts = dict(
            CertifiedExteriorCoreCandidateCounts
        )
        SmallestCertifiedExteriorDomainSignals = tuple(sorted(
            CertifiedExteriorCoreCandidateCounts,
            key=lambda Signal: (
                CertifiedExteriorCoreCandidateCounts[Signal],
                Signal,
            ),
        ))
        SmallestRemainingDomainSignals = tuple(sorted(
            RemainingApertureDomains,
            key=lambda Signal: (
                len(RemainingApertureDomains[Signal]),
                Signal,
                BuildPhysicalPortApertureContractFingerprint(
                    next(
                        Port for Port in Plan.Ports
                        if Port.Signal == Signal
                    )
                ),
            ),
        ))
        if IndependentEmptyDomainSignals:
            MinimumDeltaPivotSignal = min(
                IndependentEmptyDomainSignals
            )
        elif RequestAperturePivotSignals:
            MinimumDeltaPivotSignal = RequestAperturePivotSignals[0]
        elif SmallestCertifiedExteriorDomainSignals:
            MinimumDeltaPivotSignal = (
                SmallestCertifiedExteriorDomainSignals[0]
            )
        elif SmallestRemainingDomainSignals:
            # Keep the exact proof intact, but enumerate its cheapest useful
            # delta first.  This is the same MRV rule used by the interface
            # CSP and avoids retrying a wide port domain while a smaller
            # dependency domain can disprove the retained context.
            MinimumDeltaPivotSignal = SmallestRemainingDomainSignals[0]
        elif HubSignals:
            MinimumDeltaPivotSignal = max(
                HubSignals,
                key=lambda Value: (Value[1], Value[0]),
            )[0]
        else:
            ReportedFailureNet = str(
                ConflictGraph.get("FailureNet", "")
                if isinstance(ConflictGraph, dict)
                else ""
            )
            MinimumDeltaPivotSignal = (
                ReportedFailureNet
                if ReportedFailureNet in DependencySignals
                else min(DependencySignals)
                if DependencySignals
                else ""
            )
        MinimumDeltaRetainedContracts = {
            Port.Signal: BuildPhysicalPortGlobalContractFingerprint(Port)
            for Port in Plan.Ports
            if Port.Signal != MinimumDeltaPivotSignal
        }
        MinimumDeltaRetainedReservations = {
            Port.Signal: str(getattr(
                Port,
                "ReservationFingerprint",
                "",
            ))
            for Port in Plan.Ports
            if (
                Port.Signal != MinimumDeltaPivotSignal
                and str(getattr(
                    Port,
                    "ReservationFingerprint",
                    "",
                ))
            )
        }
        MinimumDeltaRetainedApertures = {
            Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
            for Port in Plan.Ports
            if Port.Signal != MinimumDeltaPivotSignal
        }
        RecommendedContracts = dict(MinimumDeltaRetainedContracts)
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
    else:
        PreferredGlobalContracts.clear()
    PreferredGlobalContracts.update(RecommendedContracts)
    PreferredApertureContracts = getattr(
        Resources,
        "PreferredPhysicalComponentApertureContractsBySignal",
        None,
    )
    if PreferredApertureContracts is None:
        PreferredApertureContracts = {}
        Resources.PreferredPhysicalComponentApertureContractsBySignal = (
            PreferredApertureContracts
        )
    else:
        PreferredApertureContracts.clear()
    PreferredApertureContracts.update(MinimumDeltaRetainedApertures)
    PreferredPortReservations = getattr(
        Resources,
        "PreferredPhysicalComponentPortReservationsBySignal",
        None,
    )
    if PreferredPortReservations is None:
        PreferredPortReservations = {}
        Resources.PreferredPhysicalComponentPortReservationsBySignal = (
            PreferredPortReservations
        )
    else:
        PreferredPortReservations.clear()
    PreferredPortReservations.update(
        MinimumDeltaRetainedReservations
    )
    PrunedRetainedGlobalPlanCount = 0
    if CompiledPairRelationDiagnostics:
        Frontier = getattr(
            Resources,
            "RetainedPhysicalGlobalPlanFrontier",
            {},
        )
        RejectedSets = tuple(
            Resources.RejectedPhysicalComponentPortReservationSets
        )
        Retained = {}
        for Fingerprint, Entry in Frontier.items():
            EntryKeys = frozenset(
                (
                    Port.Signal,
                    BuildPhysicalPortApertureContractFingerprint(Port),
                )
                for Port in Entry.Assembly.Plan.Ports
            )
            if any(Clause <= EntryKeys for Clause in RejectedSets):
                PrunedRetainedGlobalPlanCount += 1
                continue
            Retained[Fingerprint] = Entry
        Resources.RetainedPhysicalGlobalPlanFrontier = Retained
    TraversalDiagnostics = (
        PreservePhysicalComponentAssemblyPlanDomainContinuation(
            Resources,
        )
    )
    return {
        "NoGoodScope": Scope,
        "NoGoodSignals": sorted(
            IndependentEmptyDomainSignals or DependencySignals
        ),
        "NoGoodReservationKeys": [
            [Signal, ReservationFingerprint]
            for Signal, ReservationFingerprint in sorted(
                ()
                if RequiresExactAssemblyChoice
                else RejectedRequestApertureSet
                if RequestApertureProofComplete
                else ReservationKeys
            )
        ],
        "NoGoodConstraintArity": (
            1
            if (
                RequiresExactAssemblyChoice
                or IndependentEmptyDomainSignals
                or len(ReservationKeys) == 1
            )
            else 2
            if PairwiseReservationSets
            else len(
                RejectedRequestApertureSet
                if RequestApertureProofComplete
                else ReservationKeys
            )
        ),
        "AssemblyPortCount": len(tuple(Plan.Ports)),
        "NoGoodReservationSets": [
            [list(Key) for Key in sorted(ReservationSet)]
            for ReservationSet in sorted(
                PairwiseReservationSets,
                key=lambda Value: tuple(sorted(Value)),
            )
        ],
        "CachedCorridorContractRecommendation": dict(
            sorted(RecommendedContracts.items())
        ),
        "CachedCorridorContractRecommendationComplete": bool(
            Recommendation is not None
        ),
        "MinimumDeltaReplanPivotSignal": MinimumDeltaPivotSignal,
        "MinimumDeltaPivotDomainCounts": dict(sorted(
            MinimumDeltaPivotDomainCounts.items()
        )),
        "MinimumDeltaCertifiedExteriorDomainCounts": dict(sorted(
            MinimumDeltaCertifiedExteriorDomainCounts.items()
        )),
        "MinimumDeltaRetainedGlobalContracts": dict(sorted(
            MinimumDeltaRetainedContracts.items()
        )),
        "MinimumDeltaRetainedApertureContracts": dict(sorted(
            MinimumDeltaRetainedApertures.items()
        )),
        "MinimumDeltaRetainedPortReservations": dict(sorted(
            MinimumDeltaRetainedReservations.items()
        )),
        **TraversalDiagnostics,
        "RejectedPortAssignmentFingerprint": (
            Plan.PortAssignmentFingerprint
        ),
        "GlobalPlanDependencyFingerprint": str(
            Diagnostics.get("GlobalPlanDependencyFingerprint", "")
        ),
        "GlobalPlanCutFamilyFingerprint": str(
            Diagnostics.get("GlobalPlanCutFamilyFingerprint", "")
        ),
        "GlobalPlanProofFingerprint": str(
            Diagnostics.get("GlobalPlanProofFingerprint", "")
        ),
        "PairwisePortReservationNoGoodProofComplete": (
            PairwiseProofComplete
        ),
        "CompiledMandatoryPortalPairRelations": (
            CompiledPairRelationDiagnostics
        ),
        "PreparedMandatoryPortalPairFactorStatus": (
            PreparedPairFactorArchitectureDiagnostics
        ),
        "CompiledMandatoryPortalPairClauseCount": sum(
            int(Value["UnsatisfiableClauseCount"])
            for Value in CompiledPairRelationDiagnostics
        ),
        "PrunedRetainedGlobalPlanCount": PrunedRetainedGlobalPlanCount,
        "RejectedAssemblyChoiceFingerprint": (
            RejectedAssemblyChoiceFingerprint
        ),
        "AssemblyPlanFeedthroughIndependentProofComplete": (
            FeedthroughIndependenceProved
        ),
        "AssemblyPlanDependencyProjectionProofComplete": (
            DependencyProjectionProofComplete
        ),
        "AssemblyPlanDependencyProjectionSignals": sorted(
            DependencySignals if DependencyProjectionProofComplete else ()
        ),
    }

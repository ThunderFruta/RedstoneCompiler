"""Symbolic-capacity and detailed local-compilation no-goods."""

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
from ....Interfaces import BoundaryRelations
from ....Interfaces.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint, ProjectPhysicalComponentSignalGlobalProfile
from ....Interfaces.PhysicalClaims import ComponentClaimsConflict
from ....Resources.ResourceGraph import RoutingResourceClaims
from ....Execution.Reliability import BuildStableFingerprint
from ..Planning.InterfacePlanning import BuildComponentCapacityGuide, ComponentCapacityGuide, ComponentCapacityGuideOption, ComponentInterfaceContract, ComponentPlanningResult, ComponentPlanningStatus, IterClosedComponentContracts, PlanClosedComponent, SolveComponentInterfaceCsp

from ..Core import BuildCompleteComponentNetPortfolioStaticContext
from ..Symbolic.SymbolicState import _BuildPreparedComponentSymbolicNetStateContextFingerprint, BuildComponentSymbolicNetStateCacheKey, PrepareComponentSymbolicNetStateContext
from ..Symbolic.SymbolicWorkers import CompilePreparedComponentPhysicalFactorStateBatch, CompilePreparedComponentSymbolicNetStates
from ..Planning.Portfolios import BuildCompleteOpposingNetAccessContractDomain, BuildCompleteOpposingNetAccessRowContext, CompileCompleteComponentNetVariantPortfolio, CompileCompleteComponentNetVariantPortfolios, EvaluateCompleteOpposingNetAccessContractRow
from ..Solving.Solver import MaterializeRoutedComponentTemplate, SolveComponentRoutingProblem, ValidateRoutedComponentHandoff

from .Certification import (
    BuildDirectionalLocalFactorNoGoods,
    BuildGlobalRelaxedLocalProofDomainFingerprint,
    PromoteCoveredLocalContractNoGoods,
)
from ..Planning.PhysicalPlanning import BuildPhysicalComponentPortSolverCacheKey, MaterializePreparedPhysicalPortOptionDomains, PreservePhysicalComponentAssemblyPlanDomainContinuation, SelectPhysicalAssemblyGlobalBoundaryPorts
from .Validation import (
    BuildPhysicalPortLocalContractFingerprint,
    BuildPhysicalPortSeamContractFingerprint,
)
def RecordPhysicalComponentSymbolicCapacityEligibilityNoGood(
    Proof: ComponentRoutingSolveResult,
    Plan: PhysicalComponentAssemblyPlan,
    Resources: Any,
    FactorDomain: PreparedPhysicalComponentPortFactorDomain | None = None,
) -> dict[str, object]:
    """Reject one exact port tuple disproven before global reservation."""
    Diagnostics = dict(Proof.Diagnostics or {})
    if (
        Proof.Status != "architectural-unsatisfiable"
        or not Diagnostics.get("SymbolicCapacityProofComplete", False)
    ):
        raise ValueError(
            "symbolic capacity no-good requires a complete local proof"
        )
    Resources.RejectedPhysicalComponentPortAssignmentFingerprints.add(
        Plan.PortAssignmentFingerprint
    )
    Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
        Plan.PlanFingerprint
    )
    AssemblyChoiceFingerprint = str(getattr(
        Plan,
        "AssemblyChoiceFingerprint",
        "",
    ))
    if AssemblyChoiceFingerprint:
        Resources.RejectedPhysicalComponentAssemblyChoiceFingerprints.add(
            AssemblyChoiceFingerprint
        )
    CoreSignals = tuple(sorted({
        str(Signal)
        for Signal in Diagnostics.get("LocalUnsatCoreSignals", ())
        if str(Signal)
    }))
    PortsBySignal = {
        str(Port.Signal): Port for Port in Plan.Ports
    }
    PortCoreSignals = tuple(
        Signal for Signal in CoreSignals if Signal in PortsBySignal
    )
    LocalSeamNoGoodClauses = getattr(
        Resources,
        "RejectedPhysicalComponentLocalSeamReservationSets",
        None,
    )
    if LocalSeamNoGoodClauses is None:
        LocalSeamNoGoodClauses = set()
        setattr(
            Resources,
            "RejectedPhysicalComponentLocalSeamReservationSets",
            LocalSeamNoGoodClauses,
        )
    LocalCoreClause = frozenset()
    if (
        Diagnostics.get("LocalUnsatCoreComplete", False)
        and PortCoreSignals
    ):
        LocalCoreClause = frozenset(
            (
                Signal,
                BuildPhysicalPortSeamContractFingerprint(
                    PortsBySignal[Signal]
                ),
            )
            for Signal in PortCoreSignals
        )
        LocalSeamNoGoodClauses.add(LocalCoreClause)
        # Publish complete seam clauses to the staged CSP's canonical live
        # no-good set. Keep the legacy seam set mirrored until its remaining
        # consumers are removed after physical parity.
        Resources.RejectedPhysicalComponentPortReservationSets.add(
            LocalCoreClause
        )
    PromotedApertureClauses: set[
        frozenset[tuple[str, str]]
    ] = set()
    PromotedApertureSignals: set[str] = set()
    CoreSeamDomainSizes: dict[str, int] = {}
    if FactorDomain is not None and LocalCoreClause:
        BoundaryBySignal = {
            str(Port.Signal): Port
            for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
        }
        LocalFactorsBySignal = dict(
            FactorDomain.LocalAccessFactorsBySignal
        )
        ApertureFactorsBySignal = dict(
            FactorDomain.ApertureFactorsBySignal
        )
        SupportsByOption = dict(
            FactorDomain.LocalApertureSupportsByOption
        )
        SeamFingerprintsBySignal: dict[str, frozenset[str]] = {}
        for Signal in PortCoreSignals:
            Boundary = BoundaryBySignal.get(Signal)
            if Boundary is None:
                continue
            ApertureOptionFingerprints = frozenset(
                str(Aperture.ApertureOptionFingerprint)
                for Aperture in ApertureFactorsBySignal.get(Signal, ())
                if Aperture.GlobalContractFingerprint
                == Boundary.GlobalContractFingerprint
                and Aperture.ApertureContractFingerprint
                == Boundary.ApertureContractFingerprint
            )
            SupportedLocalAccessFingerprints = frozenset(
                str(Support.LocalAccessFingerprint)
                for ApertureOptionFingerprint
                in ApertureOptionFingerprints
                for Support in SupportsByOption.get(
                    (Signal, ApertureOptionFingerprint),
                    (),
                )
            )
            SeamFingerprints = frozenset(
                BuildPhysicalPortSeamContractFingerprint(LocalFactor)
                for LocalFactor in LocalFactorsBySignal.get(Signal, ())
                if str(LocalFactor.LocalAccessFingerprint)
                in SupportedLocalAccessFingerprints
            )
            if SeamFingerprints:
                SeamFingerprintsBySignal[Signal] = SeamFingerprints
                CoreSeamDomainSizes[Signal] = len(SeamFingerprints)

        RejectedLocalSeamClauses = tuple(
            Clause
            for Clause in LocalSeamNoGoodClauses
            if Clause and all(
                str(Fingerprint).startswith(
                    "local-seam-contract-v1:"
                )
                for _Signal, Fingerprint in Clause
            )
        )
        RejectedUnarySeamKeys = frozenset(
            next(iter(Clause))
            for Clause in RejectedLocalSeamClauses
            if len(Clause) == 1
        )
        MutableRejectedSeamPartners: dict[
            tuple[str, str], set[tuple[str, str]]
        ] = {}
        RejectedHigherOrderSeamClauses = []
        for Clause in RejectedLocalSeamClauses:
            if len(Clause) == 2:
                First, Second = tuple(Clause)
                MutableRejectedSeamPartners.setdefault(
                    First, set()
                ).add(Second)
                MutableRejectedSeamPartners.setdefault(
                    Second, set()
                ).add(First)
            elif len(Clause) > 2:
                RejectedHigherOrderSeamClauses.append(Clause)
        RejectedSeamPartners = {
            Key: frozenset(Values)
            for Key, Values in MutableRejectedSeamPartners.items()
        }

        def SeamTupleIsRejected(
            Keys: frozenset[tuple[str, str]],
        ) -> bool:
            return bool(
                Keys & RejectedUnarySeamKeys
                or any(
                    RejectedSeamPartners.get(Key, frozenset()) & Keys
                    for Key in Keys
                )
                or any(
                    Clause <= Keys
                    for Clause in RejectedHigherOrderSeamClauses
                )
            )

        def HasSupportedSeamTuple(
            RemainingSignals: tuple[str, ...],
            Keys: frozenset[tuple[str, str]],
        ) -> bool:
            if SeamTupleIsRejected(Keys):
                return False
            if not RemainingSignals:
                return True
            Signal = min(
                RemainingSignals,
                key=lambda Value: (
                    len(SeamFingerprintsBySignal[Value]),
                    Value,
                ),
            )
            NextRemaining = tuple(
                Value for Value in RemainingSignals if Value != Signal
            )
            return any(
                HasSupportedSeamTuple(
                    NextRemaining,
                    Keys | frozenset(((Signal, Seam),)),
                )
                for Seam in sorted(SeamFingerprintsBySignal[Signal])
            )

        for Signal, SeamFingerprints in (
            SeamFingerprintsBySignal.items()
        ):
            if all(
                SeamTupleIsRejected(frozenset(((Signal, Seam),)))
                for Seam in SeamFingerprints
            ):
                Boundary = BoundaryBySignal[Signal]
                (
                    Resources
                    .RejectedPhysicalComponentPortReservationsBySignal
                    .setdefault(Signal, set())
                    .add(Boundary.ApertureContractFingerprint)
                )
                PromotedApertureSignals.add(Signal)
        if len(PortCoreSignals) >= 2 and all(
            Signal in SeamFingerprintsBySignal
            and Signal in BoundaryBySignal
            for Signal in PortCoreSignals
        ):
            if not HasSupportedSeamTuple(PortCoreSignals, frozenset()):
                ApertureClause = frozenset(
                    (
                        Signal,
                        BoundaryBySignal[Signal]
                        .ApertureContractFingerprint,
                    )
                    for Signal in PortCoreSignals
                )
                Resources.RejectedPhysicalComponentPortReservationSets.add(
                    ApertureClause
                )
                PromotedApertureClauses.add(ApertureClause)
    # The boundary generator reads the learned-clause set dynamically.  Keep
    # its suspended DFS cursor and invocation-local support memo alive so the
    # next plan is the next member of the same complete domain, rather than a
    # replay of the domain under a rotated branch order.  A global/detailed
    # failure may still request an explicit traversal change; a complete
    # pre-global local core only shrinks this retained frontier.
    TraversalDiagnostics = {
        "BoundaryTraversalEpoch": int(getattr(
            Resources,
            "PhysicalComponentBoundaryTraversalEpoch",
            0,
        )),
        "BoundaryTraversalPrioritySignals": list(getattr(
            Resources,
            "PhysicalComponentBoundaryTraversalPrioritySignals",
            (),
        )),
        "BoundaryTraversalFocusSignal": "",
        "BoundaryIteratorCacheCleared": False,
        "BoundaryIteratorContinuationPreserved": True,
    }
    return {
        "NoGoodScope": "pre-global-symbolic-capacity-port-assignment",
        "RejectedPortAssignmentFingerprint": (
            Plan.PortAssignmentFingerprint
        ),
        "RejectedPhysicalAssemblyPlanFingerprint": Plan.PlanFingerprint,
        "RejectedAssemblyChoiceFingerprint": AssemblyChoiceFingerprint,
        "LocalCapacityCoreSignals": list(CoreSignals),
        "LocalCapacityProjectedInterfaceCoreSignals": list(
            PortCoreSignals
        ),
        "LocalCapacityCoreClause": [
            list(Value) for Value in sorted(LocalCoreClause)
        ],
        "LocalCapacityCorePromoted": bool(LocalCoreClause),
        "LocalCapacityApertureSignalsPromoted": sorted(
            PromotedApertureSignals
        ),
        "LocalCapacityApertureClausesPromoted": [
            [list(Key) for Key in sorted(Clause)]
            for Clause in sorted(
                PromotedApertureClauses,
                key=lambda Value: tuple(sorted(Value)),
            )
        ],
        "LocalCapacityCoreSeamDomainSizes": dict(sorted(
            CoreSeamDomainSizes.items()
        )),
        "SymbolicNetStateCacheHitCount": int(Diagnostics.get(
            "SymbolicNetStateCacheHitCount",
            0,
        )),
        "SymbolicNetStateCacheStoreCount": int(Diagnostics.get(
            "SymbolicNetStateCacheStoreCount",
            0,
        )),
        "SymbolicCapacityProofComplete": True,
        "SymbolicCapacityProofFingerprint": Proof.ProofFingerprint,
        "LocalCompilationEntered": False,
        "GlobalPlanningEntered": False,
        "PreferredRetainedGlobalContracts": dict(sorted(
            Resources.PreferredPhysicalComponentGlobalContractsBySignal.items()
        )),
        **TraversalDiagnostics,
        "ImplicitForeignTransitDomainCount": 0,
    }

def BuildUniversalPromotedFabricPortAssignmentFailure(
    Plan: PhysicalComponentAssemblyPlan,
    Resources: Any,
    PortfolioDiagnostics: dict[str, object] | None,
) -> RoutingFailure | None:
    """Build direct UNSAT only when a promoted clause covers full domains."""
    if (
        not PortfolioDiagnostics
        or not PortfolioDiagnostics.get("Complete", False)
        or int(PortfolioDiagnostics.get("PromotedFabricNoGoodCount", 0)) <= 0
    ):
        return None
    Preparation = getattr(
        Resources,
        "PreparedPhysicalComponentPortFactorDomain",
        None,
    )
    DomainFingerprint = str(getattr(
        Preparation,
        "DomainFingerprint",
        "",
    ))
    if (
        Preparation is None
        or not getattr(Preparation, "Complete", False)
        or not DomainFingerprint
    ):
        return None
    PortSolverCacheKey = BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    if str(PortfolioDiagnostics.get("PortSolverCacheKey", "")) != (
        PortSolverCacheKey
    ):
        return None
    try:
        Domains = MaterializePreparedPhysicalPortOptionDomains(
            Preparation,
            Resources,
            tuple(Port.Signal for Port in Plan.Ports),
        )
    except ValueError:
        return None
    PromotedClauses = tuple(
        frozenset(
            (str(Signal), str(Fingerprint))
            for Signal, Fingerprint in Clause
        )
        for Clause in PortfolioDiagnostics.get(
            "PromotedFabricNoGoodKeys",
            (),
        )
        if Clause
    )
    RejectedSets = getattr(
        Resources,
        "RejectedPhysicalComponentPortReservationSets",
        set(),
    )
    PortsBySignal = {str(Port.Signal): Port for Port in Plan.Ports}
    Candidates = []
    for Clause in PromotedClauses:
        Signals = tuple(sorted({Signal for Signal, _ in Clause}))
        if (
            Clause not in RejectedSets
            or not Signals
            or any(Signal not in PortsBySignal for Signal in Signals)
            or any(
                Signal not in Domains or not Domains[Signal]
                for Signal in Signals
            )
        ):
            continue
        UniversalKeysBySignal = {}
        for Signal in Signals:
            KeySets = tuple(
                frozenset(((
                    Signal,
                    "local-factor-domain:"
                    + PortSolverCacheKey
                    + ":"
                    + str(Option.FabricDomainFingerprint),
                ),))
                for Option in Domains[Signal]
            )
            UniversalKeysBySignal[Signal] = frozenset.intersection(*KeySets)
        if all(
            Literal in UniversalKeysBySignal.get(
                Literal[0],
                frozenset(),
            )
            for Literal in Clause
        ):
            Candidates.append((Signals, Clause))
    if not Candidates:
        return None
    CoreSignals, CoreClause = min(
        Candidates,
        key=lambda Value: (
            len(Value[0]),
            Value[0],
            tuple(sorted(Value[1])),
        ),
    )
    return RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="LocalInterfaceFactorPortfolioUnsatisfiable",
        AffectedNets=CoreSignals,
        Detail=(
            "a complete local interface portfolio promoted a fabric clause "
            "that is universal over the active complete port domains"
        ),
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "CompleteAssignmentCutProof": True,
            "OwnershipSearchComplete": True,
            "PortAssignmentUnsatProofBasis": (
                "complete-local-interface-factor-domain-no-good"
            ),
            "PortAssignmentUnsatCoreSignals": list(CoreSignals),
            "PortAssignmentUnsatCoreClause": [
                list(Value) for Value in sorted(CoreClause)
            ],
            "PortSolverCacheKey": PortSolverCacheKey,
            "CompletePortDomainSizes": {
                Signal: len(Domains[Signal]) for Signal in CoreSignals
            },
            "LocalInterfaceFactorPortfolio": PortfolioDiagnostics,
            "PhysicalAssemblyPlanFingerprint": Plan.PlanFingerprint,
            "GlobalReplanEntered": False,
            "LocalTemplateReopened": False,
            "BroadFallbackAllowed": False,
            "ExecutableLegacyRepairCascade": False,
            "ImplicitForeignTransitDomainCount": 0,
        },
    )


def RecordPhysicalComponentDetailedRoutingNoGood(
    Plan: PhysicalComponentAssemblyPlan,
    GlobalChannelDesign: Any,
    Resources: Any,
) -> dict[str, object]:
    """Reject only the exact bound channels after detailed-route failure."""
    Assignment = getattr(GlobalChannelDesign, "RoutingAssignment", None)
    if Assignment is None:
        raise ValueError(
            "detailed routing no-good requires a bound global assignment"
        )
    CandidateSet = frozenset(
        (str(Signal), str(Candidate.CandidateId))
        for Signal, Candidate in Assignment.SelectedCandidates.items()
    )
    BoundCandidateSet = frozenset(
        (str(Channel.Signal), str(Channel.RouteCandidateId))
        for Channel in Plan.Channels
    )
    if not CandidateSet or CandidateSet != BoundCandidateSet:
        raise ValueError(
            "detailed routing no-good global assignment identity mismatch"
        )
    Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(
        CandidateSet
    )
    Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
        Plan.PlanFingerprint
    )
    return {
        "NoGoodScope": "exact-physical-global-candidate-set",
        "ForbiddenGlobalCandidateSet": [
            [Signal, CandidateId]
            for Signal, CandidateId in sorted(CandidateSet)
        ],
        "RejectedPhysicalAssemblyPlanFingerprint": (
            Plan.PlanFingerprint
        ),
        "PortAssignmentRejected": False,
    }


def RecordPhysicalComponentLocalCompilationNoGood(
    Solve: ComponentRoutingSolveResult,
    Plan: PhysicalComponentAssemblyPlan,
    GlobalChannelDesign: Any,
    Resources: Any,
    *,
    Problem: ComponentRoutingProblem | None = None,
) -> dict[str, object]:
    """Record the narrowest proof-qualified local compilation no-good."""
    Diagnostics = dict(Solve.Diagnostics or {})
    if (
        Solve.Status != "architectural-unsatisfiable"
        or not bool(Diagnostics.get("LocalUnsatCoreComplete", False))
    ):
        raise ValueError(
            "local component no-good requires a complete local proof"
        )
    CoreSignals = frozenset(map(str, (
        Diagnostics.get("LocalUnsatCoreSignals", ()) or ()
    )))
    if not CoreSignals:
        raise ValueError("local component proof has an empty core")
    GlobalRelaxedProofComplete = bool(
        Diagnostics.get("GlobalRelaxedLocalProofComplete", False)
    )
    RelaxedProofFingerprint = str(
        Diagnostics.get("GlobalRelaxedLocalProofFingerprint", "")
    )
    RelaxedDomainFingerprint = str(
        Diagnostics.get("GlobalRelaxedLocalDomainFingerprint", "")
    )
    RelaxedCoreKind = str(
        Diagnostics.get("GlobalRelaxedLocalUnsatCoreKind", "")
    )
    CertifiedCoreKinds = frozenset((
        "complete-opposing-net-access-pair",
        "complete-symbolic-capacity-pair",
        "complete-symbolic-capacity-core",
        "complete-symbolic-empty-capacity-domain",
        "tree-frontier-empty-owned-signal-domain",
        "tree-frontier-empty-signal",
    ))
    if GlobalRelaxedProofComplete:
        if not RelaxedProofFingerprint or not RelaxedDomainFingerprint:
            raise ValueError(
                "global-relaxed local proof is missing identity fingerprints"
            )
        if (
            Problem is None
            or Problem.PhysicalAssemblyPlan is None
            or Problem.PhysicalAssemblyPlan.PlanFingerprint
            != Plan.PlanFingerprint
        ):
            raise ValueError(
                "global-relaxed local proof problem identity mismatch"
            )
        if (
            BuildGlobalRelaxedLocalProofDomainFingerprint(Problem)
            != RelaxedDomainFingerprint
        ):
            raise ValueError(
                "global-relaxed local proof domain fingerprint mismatch"
            )
    RelaxedCoreSignals = frozenset(map(str, (
        Diagnostics.get("GlobalRelaxedLocalUnsatCoreSignals", ()) or ()
    )))
    RelaxedCurrentSignal = str(
        Diagnostics.get("GlobalRelaxedLocalCurrentSignal", "")
    )
    RelaxedCompleteSignal = str(
        Diagnostics.get("GlobalRelaxedLocalCompleteSignal", "")
    )
    if GlobalRelaxedProofComplete:
        PortsBySignal = {Port.Signal: Port for Port in Plan.Ports}
        if (
            Diagnostics.get("GlobalRelaxedLocalCoreComplete", False)
            and RelaxedCoreKind in CertifiedCoreKinds
            and RelaxedCoreSignals
            and RelaxedCoreSignals <= PortsBySignal.keys()
        ):
            PreparedPortDomain = getattr(
                Resources,
                "PreparedPhysicalComponentPortFactorDomain",
                None,
            )
            OwnedSignalFamilyProof = bool(
                RelaxedCoreKind
                == "tree-frontier-empty-owned-signal-domain"
                and len(RelaxedCoreSignals) == 1
                and Diagnostics.get(
                    "LocalUnsatCoreProjectionFingerprint",
                    "",
                )
                and PreparedPortDomain is not None
                and bool(getattr(PreparedPortDomain, "Complete", False))
                and bool(getattr(PreparedPortDomain, "Feasible", False))
                and getattr(PreparedPortDomain, "DomainFingerprint", "")
            )
            if OwnedSignalFamilyProof:
                PortSolverCacheKey = (
                    BuildPhysicalComponentPortSolverCacheKey(str(
                        PreparedPortDomain.DomainFingerprint
                    ))
                )
                CoreReservationKeys = frozenset(
                    (
                        Signal,
                        "local-signal-domain:" + PortSolverCacheKey,
                    )
                    for Signal in RelaxedCoreSignals
                )
            else:
                CoreReservationKeys = frozenset(
                    (
                        Signal,
                        BuildPhysicalPortLocalContractFingerprint(
                            PortsBySignal[Signal]
                        ),
                    )
                    for Signal in RelaxedCoreSignals
                )
            Resources.RejectedPhysicalComponentPortReservationSets.add(
                CoreReservationKeys
            )
            DirectionalLocalFactorNoGoods = ()
            if not OwnedSignalFamilyProof and RelaxedCoreSignals == frozenset((
                RelaxedCurrentSignal,
                RelaxedCompleteSignal,
            )):
                DirectionalLocalFactorNoGoods = (
                    BuildDirectionalLocalFactorNoGoods(
                        Plan,
                        RelaxedCurrentSignal,
                        RelaxedCompleteSignal,
                        Resources,
                    )
                )
                Resources.RejectedPhysicalComponentPortReservationSets.update(
                    DirectionalLocalFactorNoGoods
                )
            PromotedFabricNoGoods = (
                ()
                if OwnedSignalFamilyProof
                else PromoteCoveredLocalContractNoGoods(
                    Plan,
                    RelaxedCoreSignals,
                    Resources,
                )
            )
            Scope = (
                "global-relaxed-owned-signal-domain"
                if OwnedSignalFamilyProof
                else "global-relaxed-local-port-core"
            )
        else:
            Resources.RejectedPhysicalComponentPortAssignmentFingerprints.add(
                Plan.PortAssignmentFingerprint
            )
            CoreReservationKeys = frozenset()
            PromotedFabricNoGoods = ()
            Scope = "global-relaxed-port-assignment"
        Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
            Plan.PlanFingerprint
        )
        # The relaxed proof removed exterior corridors, so the completed
        # global contracts are known-good context rather than part of the
        # local failure.  Retain them as deterministic CSP preferences while
        # selecting a different local contract; this avoids needlessly
        # reopening unrelated global geometry without forbidding alternatives.
        Resources.PreferredPhysicalComponentGlobalContractsBySignal = {
            Port.Signal: BuildPhysicalPortGlobalContractFingerprint(Port)
            for Port in Plan.Ports
        }
        TraversalDiagnostics = (
            PreservePhysicalComponentAssemblyPlanDomainContinuation(
                Resources,
            )
        )
        Result = {
            "NoGoodScope": Scope,
            "NoGoodSignals": sorted(RelaxedCoreSignals),
            "GlobalRelaxedLocalUnsatCoreSignals": sorted(
                RelaxedCoreSignals
            ),
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": bool(
                Diagnostics.get(
                    "GlobalRelaxedLocalCoreComplete",
                    False,
                )
            ),
            "GlobalRelaxedLocalProofFingerprint": str(
                RelaxedProofFingerprint
            ),
            "GlobalRelaxedLocalDomainFingerprint": str(
                RelaxedDomainFingerprint
            ),
            "GlobalRelaxedLocalUnsatCoreKind": RelaxedCoreKind,
            "RejectedPhysicalAssemblyPlanFingerprint": (
                Plan.PlanFingerprint
            ),
            "PreferredRetainedGlobalContracts": dict(sorted(
                Resources
                .PreferredPhysicalComponentGlobalContractsBySignal.items()
            )),
            **TraversalDiagnostics,
        }
        if CoreReservationKeys:
            Result["NoGoodReservationKeys"] = [
                [Signal, Fingerprint]
                for Signal, Fingerprint in sorted(CoreReservationKeys)
            ]
            Result["PromotedFabricNoGoodKeys"] = [
                [list(Key) for Key in sorted(Promotion)]
                for Promotion in PromotedFabricNoGoods
            ]
            Result["DirectionalLocalFactorNoGoodKeys"] = [
                [list(Key) for Key in sorted(NoGood)]
                for NoGood in DirectionalLocalFactorNoGoods
            ]
        else:
            Result["RejectedPortAssignmentFingerprint"] = (
                Plan.PortAssignmentFingerprint
            )
        return Result
    Assignment = getattr(GlobalChannelDesign, "RoutingAssignment", None)
    if Assignment is None:
        raise ValueError(
            "local component no-good requires a bound global assignment"
        )
    CandidateSet = frozenset(
        (str(Signal), str(Candidate.CandidateId))
        for Signal, Candidate in Assignment.SelectedCandidates.items()
    )
    BoundCandidateSet = frozenset(
        (str(Channel.Signal), str(Channel.RouteCandidateId))
        for Channel in Plan.Channels
    )
    if not CandidateSet or CandidateSet != BoundCandidateSet:
        raise ValueError(
            "local component no-good global assignment identity mismatch"
        )
    SignalDiagnostics = Diagnostics.get("SignalDiagnostics", {})
    ProvenExteriorCoreSignals: set[str] = set()
    ExteriorCoreComplete = bool(
        isinstance(SignalDiagnostics, dict)
        and CoreSignals
    )
    for Signal in sorted(CoreSignals):
        PerSignalDiagnostics = SignalDiagnostics.get(Signal, {})
        if not (
            isinstance(PerSignalDiagnostics, dict)
            and PerSignalDiagnostics.get(
                "ReservedGlobalRouteUnsatCoreComplete",
                False,
            )
        ):
            ExteriorCoreComplete = False
            break
        ProvenExteriorCoreSignals.update(map(str, (
            PerSignalDiagnostics.get(
                "ReservedGlobalRouteUnsatCoreSignals",
                (),
            )
        )))
    CandidateNoGood = frozenset(
        (Signal, CandidateId)
        for Signal, CandidateId in CandidateSet
        if Signal in ProvenExteriorCoreSignals
    )
    if not (
        ExteriorCoreComplete
        and CandidateNoGood
        and len(CandidateNoGood) <= 2
        and len(CandidateNoGood) == len(ProvenExteriorCoreSignals)
    ):
        CandidateNoGood = CandidateSet
        ExteriorCoreComplete = False
    Resources.ForbiddenPhysicalComponentGlobalCandidateSets.add(
        CandidateNoGood
    )
    Resources.RejectedPhysicalComponentAssemblyPlanFingerprints.add(
        Plan.PlanFingerprint
    )
    TraversalDiagnostics = (
        PreservePhysicalComponentAssemblyPlanDomainContinuation(
            Resources,
        )
    )
    return {
        "NoGoodScope": "exact-physical-global-candidate-set",
        "NoGoodSignals": sorted(CoreSignals),
        "ForbiddenGlobalCandidateSet": [
            [Signal, CandidateId]
            for Signal, CandidateId in sorted(CandidateNoGood)
        ],
        "ExteriorCandidateCoreSignals": sorted(
            ProvenExteriorCoreSignals
        ),
        "ExteriorCandidateCoreComplete": ExteriorCoreComplete,
        "NoGoodConstraintArity": len(CandidateNoGood),
        "LocalUnsatCoreFingerprint": str(
            Diagnostics.get("LocalUnsatCoreFingerprint", "")
        ),
        "RejectedPhysicalAssemblyPlanFingerprint": Plan.PlanFingerprint,
        "GlobalRelaxedLocalProofComplete": False,
        "GlobalRelaxedLocalCoreComplete": False,
        **TraversalDiagnostics,
        "GlobalRelaxedLocalProofStatus": str(
            (
                "uncertified-core-kind"
                if GlobalRelaxedProofComplete
                else Diagnostics.get(
                    "GlobalRelaxedLocalProofStatus",
                    "not-run",
                )
            )
        ),
    }

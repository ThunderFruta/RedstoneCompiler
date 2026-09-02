"""Shared fixtures and imports for split component pipeline tests."""

from dataclasses import replace

from types import SimpleNamespace

import pytest

from Compiler.Placement.Core.Repair import (
    SelectTransactionalRepairClusterSelections,
)

from Compiler.Placement.Flow.Candidates import (
    BuildPhysicalGlobalPlanResumeCursorFromDiagnostics,
    ClassifyPhysicalGlobalPlanRetentionAdmission,
)

from Compiler.Placement.Flow.Preparation import SummarizePreRouteAccessFabric

from Compiler.Placement.Flow.Results import (
    BuildComponentRoutabilityCore,
    BuildCapacityRepairEndpointClosureClusters,
    BuildCapacityRepairGeometryFingerprint,
    BuildPhysicalLocalFactorDiversificationCore,
    BuildPhysicalOwnedFrontierTopologyRepairCore,
    BuildPhysicalInterfaceRepairCore,
    ComposePhysicalInterfaceRepairCores,
    BuildSymbolicCapacityRepairEvidence,
    PreparedEligibilityHasDisjointCapacitySeams,
    BuildPhysicalComponentPlacementFeedback,
    IsClusterInterfaceStateIncomplete,
    IsCompletePhysicalAssemblyUnsatisfiable,
)

from Compiler.Placement.Flow.Runner import _PlaceAndRoutePcbWithPolicy

import Compiler.Placement.Flow.CandidateRouting as PlacementCandidateRouting

import Compiler.Placement.Flow.Feedback as PlacementFeedback

import Compiler.Placement.Flow.PhysicalAssembly as PlacementPhysicalAssembly

import Compiler.Placement.Flow.PhysicalFlow as PlacementPhysicalFlow

import Compiler.Placement.Flow.PlacementAttempts as PlacementAttempts

import Compiler.Placement.Flow.Portfolios as PlacementPortfolios

import Compiler.Placement.Flow.Results as PlacementPublication

import Compiler.Placement.Flow.RoutingAttempts as PlacementRoutingAttempts

import Compiler.Placement.Flow.Setup as PlacementSetup

import Compiler.Routing.Authoritative.CandidateDomains as AuthoritativeCandidateDomains

import Compiler.Routing.Authoritative.CandidateGuides as AuthoritativeCandidateGuides

import Compiler.Routing.Authoritative.Flow as AuthoritativeFlow

import Compiler.Routing.Authoritative.FlowPhases.AssignmentPreparation as AuthoritativeAssignmentPreparation

import Compiler.Routing.Authoritative.FlowPhases.GuidePlanning as AuthoritativeGuidePlanning

import Compiler.Routing.Authoritative.FlowPhases.PortalPreparation as AuthoritativePortalPreparation

import Compiler.Routing.Authoritative.PortPreparation as AuthoritativePortPreparation

import Compiler.Routing.Authoritative.RunModels as AuthoritativeRunModels

import Compiler.Routing.Authoritative.ExteriorConnectors as AuthoritativeExteriorConnectors

import Compiler.Routing.Authoritative.PortPreparationHelpers as AuthoritativePortPreparationHelpers

import Compiler.Routing.Authoritative.PortPreparation as PhysicalPortPreparation

import Compiler.Routing.Authoritative.PortSolving as PhysicalPortSolving

import Compiler.Routing.Authoritative.PortSolving.Search as PhysicalPortSearch

import Compiler.Routing.Components.Fabric as ComponentFabric

import Compiler.Routing.Components.Problem as ComponentProblem

import Compiler.Routing.Pcb as Pcb

from Compiler.Routing.Interfaces import BoundaryRelations

from Compiler.Routing.Components.PhysicalPlanning import (
    BuildPhysicalComponentAssemblyChoiceFingerprint,
    BuildPhysicalComponentAssemblyPlanDomainFingerprint,
    BuildPhysicalAssemblyGlobalReuseFingerprint,
    BuildPhysicalGlobalPlanCutFamilyFingerprint,
    BuildPhysicalGlobalPlanDependencyFingerprint,
    BuildPhysicalRequestAperturePortNoGood,
    ClassifyPhysicalComponentGlobalPlanningFailure,
    PreservePhysicalComponentAssemblyPlanDomainContinuation,
    PhysicalAssemblyGlobalRouteCanBeRebound,
    PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses,
    SelectPhysicalComponentExactGlobalChannelSignals,
)

from Compiler.Routing.Components.Validation import (
    BuildPhysicalPortApertureContractFingerprint,
    BuildPhysicalPortLocalContractFingerprint,
    BuildPhysicalPortSeamContractFingerprint,
    SelectPhysicalComponentGlobalContractRecommendation,
)

from Compiler.Routing.Components.GlobalNoGoods import (
    RecordPhysicalComponentGlobalPlanNoGood,
)

from Compiler.Routing.Components.NoGoods import (
    RecordPhysicalComponentDetailedRoutingNoGood,
    RecordPhysicalComponentSymbolicCapacityEligibilityNoGood,
)

from Compiler.Routing.Components.SymbolicDomains import (
    ProjectCompletePhysicalPortPairCertificateToApertureClauses,
)

from Compiler.Routing.Components.Certification import (
    SelectContractIndependentOwnedSignalFrontierUnsatCore,
)

from Compiler.Routing.Interfaces.BoundaryRelations import (
    BuildPhysicalPortGlobalContractFingerprint,
)

import Compiler.Routing.Pcb as RoutingPcb

from Compiler.Routing.Pcb import (
    ReplanPhysicalComponentAssembly,
    SolvePreparedPhysicalComponentEligibility,
)

from Compiler.Routing.Failures import (
    RoutingAssignmentCut,
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)

from Compiler.Routing.ResourceGraph import RoutingResourceClaims

from Compiler.Routing.Contracts.Component import ComponentRoutingSolveResult

from Compiler.Routing.Contracts.PhysicalInterface import (
    PhysicalGlobalPlanResumeCursor,
    PhysicalPortCorridorDomain,
    PhysicalPortCorridorFactor,
)

from Compiler.Routing.Contracts.Placement import (
    TrackAssignmentPreparation,
    TrackAssignmentPrepared,
)

from Compiler.Routing.Policy import DefaultPhysicalDesignPolicy

from Compiler.Routing.Reliability import RoutingDeadline

def _PhysicalPairApertureProjectionFixture(*, CompleteSupports=True):
    Apertures = (
        (
            "Alpha",
            (
                SimpleNamespace(
                    ApertureOptionFingerprint="alpha-left-option",
                    ApertureContractFingerprint="alpha-absolute-left",
                    ReservationFingerprint="alpha-portable-reservation",
                ),
                SimpleNamespace(
                    ApertureOptionFingerprint="alpha-right-option",
                    ApertureContractFingerprint="alpha-absolute-right",
                    ReservationFingerprint="alpha-portable-reservation",
                ),
            ),
        ),
        (
            "Beta",
            (SimpleNamespace(
                ApertureOptionFingerprint="beta-option",
                ApertureContractFingerprint="beta-absolute",
                ReservationFingerprint="beta-portable-reservation",
            ),),
        ),
    )
    Supports = [
        SimpleNamespace(
            ApertureOptionFingerprint="alpha-left-option",
            LocalAccessFingerprint="alpha-left-access",
        ),
    ]
    if CompleteSupports:
        Supports.append(SimpleNamespace(
            ApertureOptionFingerprint="alpha-right-option",
            LocalAccessFingerprint="alpha-right-access",
        ))
    FactorDomain = SimpleNamespace(
        ApertureFactorsBySignal=Apertures,
        LocalApertureSupportBySignal=(
            ("Alpha", tuple(Supports)),
            ("Beta", (SimpleNamespace(
                ApertureOptionFingerprint="beta-option",
                LocalAccessFingerprint="beta-access",
            ),)),
        ),
    )
    Certificate = SimpleNamespace(
        Complete=True,
        SignalPair=("Alpha", "Beta"),
        LocalAccessFingerprintsBySignal=(
            ("Alpha", (
                "alpha-left-access",
                "alpha-right-access",
            )),
            ("Beta", ("beta-access",)),
        ),
        SeamFingerprintByLocalAccess=(
            ("Alpha", "alpha-left-access", "alpha-left-seam"),
            ("Alpha", "alpha-right-access", "alpha-right-seam"),
            ("Beta", "beta-access", "beta-seam"),
        ),
        UnsupportedUnarySeams=(
            ("Alpha", "alpha-left-seam"),
            ("Alpha", "alpha-right-seam"),
        ),
        UnsupportedSeamPairs=(),
    )
    return FactorDomain, Certificate

def _MixedPhysicalCorridorDomains():
    def Factor(Signal, Suffix, Node):
        Claims = RoutingResourceClaims(
            WireCells=frozenset((Node,)),
            ElectricalCells=frozenset((Node,)),
        )
        return PhysicalPortCorridorFactor(
            Signal=Signal,
            PortReservationFingerprint=(
                f"reservation-{Signal.lower()}-{Suffix}"
            ),
            PortGlobalContractFingerprint=(
                f"global-{Signal.lower()}-{Suffix}"
            ),
            RequestDependencyFingerprint=(
                f"request-{Signal.lower()}-{Suffix}"
            ),
            RouteCandidateId=f"route-{Signal.lower()}-{Suffix}",
            RouteCandidateFingerprint=(
                f"route-fingerprint-{Signal.lower()}-{Suffix}"
            ),
            NormalizedIdentityFingerprint=(
                f"normalized-{Signal.lower()}-{Suffix}"
            ),
            Layer=0,
            Nodes=frozenset((Node,)),
            Claims=Claims,
            Candidate=SimpleNamespace(
                CandidateId=f"route-{Signal.lower()}-{Suffix}",
                Claims=Claims,
            ),
        )

    def Domain(FactorValue):
        return PhysicalPortCorridorDomain(
            DomainFingerprint=(
                "domain-" + FactorValue.NormalizedIdentityFingerprint
            ),
            Signal=FactorValue.Signal,
            PortReservationFingerprint=(
                FactorValue.PortReservationFingerprint
            ),
            PortGlobalContractFingerprint=(
                FactorValue.PortGlobalContractFingerprint
            ),
            RequestDependencyFingerprint=(
                FactorValue.RequestDependencyFingerprint
            ),
            ResourceGraphFingerprint="resource-graph",
            TechnologyFingerprint="technology",
            Factors=(FactorValue,),
            Complete=True,
        )

    # The two original plan tuples conflict on their shared exact node:
    # (A1, B1) at node 0 and (A2, B2) at node 10.  Cross-plan tuples are
    # compatible, allowing the recommendation to reuse cached exact factors
    # without pretending either failed complete tuple was feasible.
    return tuple(map(Domain, (
        Factor("A", "1", (0, 1, 0)),
        Factor("B", "1", (0, 1, 0)),
        Factor("A", "2", (10, 1, 0)),
        Factor("B", "2", (10, 1, 0)),
    )))

def _DescriptorProgressDiagnostics(
    Completed,
    *,
    PreSibling="pre-sibling-a",
    RequestDomain="request-a",
    Universe="universe-a",
    DescriptorCount=3,
    StoredRouteResults=0,
):
    return {
        "PhysicalSignalRouteDomainDescriptorProgress": {
            "SignalA": {
                "PreSiblingDomainFingerprint": PreSibling,
                "RequestDomainFingerprint": RequestDomain,
                "DescriptorUniverseFingerprint": Universe,
                "DescriptorCount": DescriptorCount,
                "CompletedDescriptorCount": len(Completed),
                "CompletedDescriptorFingerprints": list(Completed),
            },
        },
        "PhysicalGlobalRouteTreeResultCache": {
            "StoredResultCount": StoredRouteResults,
            "StoredResultCountAfterDeadlineRetention": (
                StoredRouteResults
            ),
        },
        "RouteTreeCompletedWork": StoredRouteResults,
    }

def _DescriptorContinuation(Completed, **DiagnosticOverrides):
    Cursor, CompletedWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            _DescriptorProgressDiagnostics(
                Completed,
                **DiagnosticOverrides,
            ),
        )
    )
    assert Cursor is not None
    Plan = SimpleNamespace(PlanFingerprint="plan-a", Ports=())
    return AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
        Plan,
        {"SignalA": "request-a"},
        {"SignalA": 3 - len(Completed)},
        (),
        ("aperture-a",),
        CompletedWork=CompletedWork,
        ResumeCursor=Cursor,
    )

__all__ = tuple(Name for Name in globals() if not Name.startswith("__"))

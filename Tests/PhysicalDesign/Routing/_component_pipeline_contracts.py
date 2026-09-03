"""Shared fixtures and imports for split component pipeline tests."""

from dataclasses import replace

from types import SimpleNamespace

import pytest

from PhysicalDesign.Placement.Core.Repair import SelectTransactionalRepairClusterSelections

from PhysicalDesign.Flow.Candidates import BuildPhysicalGlobalPlanResumeCursorFromDiagnostics, ClassifyPhysicalGlobalPlanRetentionAdmission

from PhysicalDesign.Flow.Preparation import SummarizePreRouteAccessFabric

from PhysicalDesign.Flow.Results import BuildComponentRoutabilityCore, BuildCapacityRepairEndpointClosureClusters, BuildCapacityRepairGeometryFingerprint, BuildPhysicalLocalFactorDiversificationCore, BuildPhysicalOwnedFrontierTopologyRepairCore, BuildPhysicalInterfaceRepairCore, ComposePhysicalInterfaceRepairCores, BuildSymbolicCapacityRepairEvidence, PreparedEligibilityHasDisjointCapacitySeams, BuildPhysicalComponentPlacementFeedback, IsClusterInterfaceStateIncomplete, IsCompletePhysicalAssemblyUnsatisfiable

from PhysicalDesign.Flow.Runner import _PlaceAndRoutePcbWithPolicy

import PhysicalDesign.Flow.CandidateRouting as PlacementCandidateRouting

import PhysicalDesign.Flow.Feedback as PlacementFeedback

import PhysicalDesign.Flow.PhysicalAssembly as PlacementPhysicalAssembly

import PhysicalDesign.Flow.PhysicalFlow as PlacementPhysicalFlow

import PhysicalDesign.Flow.PlacementAttempts as PlacementAttempts

import PhysicalDesign.Flow.Portfolios as PlacementPortfolios

import PhysicalDesign.Flow.Results as PlacementPublication

import PhysicalDesign.Flow.RoutingAttempts as PlacementRoutingAttempts

import PhysicalDesign.Flow.Setup as PlacementSetup

import PhysicalDesign.Routing.Global.Candidates.CandidateDomains as AuthoritativeCandidateDomains

import PhysicalDesign.Routing.Global.Candidates.CandidateGuides as AuthoritativeCandidateGuides

import PhysicalDesign.Routing.Global.Flow.Flow as AuthoritativeFlow

import PhysicalDesign.Routing.Global.Flow.Phases.AssignmentPreparation as AuthoritativeAssignmentPreparation

import PhysicalDesign.Routing.Global.Flow.Phases.GuidePlanning as AuthoritativeGuidePlanning

import PhysicalDesign.Routing.Global.Flow.Phases.PortalPreparation as AuthoritativePortalPreparation

import PhysicalDesign.Routing.Global.Ports.PortPreparation as AuthoritativePortPreparation

import PhysicalDesign.Routing.Global.Flow.RunModels as AuthoritativeRunModels

import PhysicalDesign.Routing.Global.Ports.ExteriorConnectors as AuthoritativeExteriorConnectors

import PhysicalDesign.Routing.Global.Ports.PortPreparationHelpers as AuthoritativePortPreparationHelpers

import PhysicalDesign.Routing.Global.Ports.PortPreparation as PhysicalPortPreparation

import PhysicalDesign.Routing.Global.Ports.Solving as PhysicalPortSolving

import PhysicalDesign.Routing.Global.Ports.Solving.Search as PhysicalPortSearch

import PhysicalDesign.Routing.Regions.Interfaces.Fabric as ComponentFabric

import PhysicalDesign.Routing.Regions.Interfaces.Problem as ComponentProblem

import PhysicalDesign.Routing.Pcb as Pcb

from PhysicalDesign.Interfaces import BoundaryRelations

from PhysicalDesign.Routing.Regions.Planning.PhysicalPlanning import BuildPhysicalComponentAssemblyChoiceFingerprint, BuildPhysicalComponentAssemblyPlanDomainFingerprint, BuildPhysicalAssemblyGlobalReuseFingerprint, BuildPhysicalGlobalPlanCutFamilyFingerprint, BuildPhysicalGlobalPlanDependencyFingerprint, BuildPhysicalRequestAperturePortNoGood, ClassifyPhysicalComponentGlobalPlanningFailure, PreservePhysicalComponentAssemblyPlanDomainContinuation, PhysicalAssemblyGlobalRouteCanBeRebound, PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses, SelectPhysicalComponentExactGlobalChannelSignals

from PhysicalDesign.Routing.Regions.Proofs.Validation import BuildPhysicalPortApertureContractFingerprint, BuildPhysicalPortLocalContractFingerprint, BuildPhysicalPortSeamContractFingerprint, SelectPhysicalComponentGlobalContractRecommendation

from PhysicalDesign.Routing.Regions.Proofs.GlobalNoGoods import RecordPhysicalComponentGlobalPlanNoGood

from PhysicalDesign.Routing.Regions.Proofs.NoGoods import RecordPhysicalComponentDetailedRoutingNoGood, RecordPhysicalComponentSymbolicCapacityEligibilityNoGood

from PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains import ProjectCompletePhysicalPortPairCertificateToApertureClauses

from PhysicalDesign.Routing.Regions.Proofs.Certification import SelectContractIndependentOwnedSignalFrontierUnsatCore

from PhysicalDesign.Interfaces.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint

import PhysicalDesign.Routing.Pcb as RoutingPcb

from PhysicalDesign.Routing.Pcb import ReplanPhysicalComponentAssembly, SolvePreparedPhysicalComponentEligibility

from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingFailure, RoutingFailureReason, RoutingStageError

from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims

from PhysicalDesign.Contracts.Component import ComponentRoutingSolveResult

from PhysicalDesign.Contracts.PhysicalInterface import PhysicalGlobalPlanResumeCursor, PhysicalPortCorridorDomain, PhysicalPortCorridorFactor

from PhysicalDesign.Contracts.Placement import TrackAssignmentPreparation, TrackAssignmentPrepared

from PhysicalDesign.Policy import DefaultPhysicalDesignPolicy

from PhysicalDesign.Execution.Reliability import RoutingDeadline

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

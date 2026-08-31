import inspect
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


def _Sources(*Functions):
    """Join concrete owners in their runtime phase order for source contracts."""
    return "\n".join(inspect.getsource(Function) for Function in Functions)


def test_port_preparation_queries_native_connector_capability_from_its_owner():
    HelperSource = inspect.getsource(
        AuthoritativePortPreparationHelpers.BuildGlobalPathToGuide
    )

    assert "NativeExteriorConnectorSearchAvailable()" in HelperSource
    assert "_SearchExteriorConnectorsBatchWithTelemetry" not in HelperSource
    assert isinstance(
        AuthoritativeExteriorConnectors.NativeExteriorConnectorSearchAvailable(),
        bool,
    )


def test_complete_native_connector_no_path_does_not_repeat_python_search():
    HelperSource = inspect.getsource(
        AuthoritativePortPreparationHelpers.BuildGlobalPathToGuide
    )

    NativeEmpty = HelperSource.index(
        "if NativePathResult is not None and not NativePathResult.Path:"
    )
    ExactFallback = HelperSource.index(
        "PathResult = SelectPhysicalExteriorConnectorPath("
    )

    assert NativeEmpty < ExactFallback


def test_fixed_connector_stem_claims_are_rejected_before_materialized_acceptance():
    HelperSource = inspect.getsource(
        AuthoritativePortPreparationHelpers.BuildGlobalPathToGuide
    )

    StemClaims = HelperSource.index(
        "and not ConnectorClaimsAreLegal(StemPath)"
    )
    NativeAcceptance = HelperSource.index(
        "if NativePathResult is not None and NativePathResult.Path:"
    )

    assert StemClaims < NativeAcceptance
    assert "This prepass freezes geometry-only native work" in HelperSource


def test_capacity_repair_precheck_runs_before_remaining_port_banks():
    Source = inspect.getsource(
        AuthoritativePortPreparation.PreparePhysicalComponentPortFactorDomain
    )

    CapacityPrecheck = Source.index(
        "CapacityWitness = SelectPriorityDisjointCapacitySeams("
    )
    RemainingBanks = Source.index(
        "RemainingSignals = PortSignals - PrioritySignals"
    )

    assert CapacityPrecheck < RemainingBanks


def test_channelized_capacity_repair_preserves_dequeued_priority_core():
    Source = inspect.getsource(
        PlacementPhysicalFlow.RunPhysicalComponentFlow
    )
    PrepareEligibility = Source.index(
        "if Context.InterfaceWorkPhase == 'prepare-eligibility':"
    )
    CapacityHandoff = Source.index(
        "PhysicalComponentCapacityRepairConstraint = Context.CapacityRepairConstraint",
        PrepareEligibility,
    )
    PreparationCall = Source.index(
        "Context.PreparedEligibility = PreparePhysicalComponentEligibility(",
        CapacityHandoff,
    )

    assert CapacityHandoff < PreparationCall
    assert (
        "CapacityRepairConstraintByPlacementFingerprint.get"
        not in Source[CapacityHandoff:PreparationCall]
    )
    assert (
        "PreferredSignals=Context.CapacityRepairPreferredSignals"
        in Source
    )
    assert Source.index(
        "MissingCapacityRepairChannelSignals"
    ) < PreparationCall


def test_capacity_child_core_can_generate_one_refined_geometry_before_sibling():
    AssemblySource = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement
    )
    FlowSource = inspect.getsource(
        PlacementPhysicalFlow.RunPhysicalComponentFlow
    )

    assert "InheritedCapacityRepairRefinement" in AssemblySource
    assert "(not InheritedCapacityRepairRefinement)" in AssemblySource
    assert (
        "MaximumProofGuidedSymbolicCapacityPairPlacements = 3"
        in FlowSource
    )


def test_closed_region_portals_replace_discovery_domain_before_consumers():
    Source = inspect.getsource(
        AuthoritativePortalPreparation.RunPortalPreparation
    )
    Publish = Source.index(
        "RawPortalEntries = State.EffectiveRawPortalCache.PortalEntries"
    )
    Dictionary = Source.index(
        "RawPortals = State.EffectiveRawPortalCache.BuildPortalDictionary()",
        Publish,
    )
    Consumer = Source.index(
        "if State.PrepareComponentRoutingProblemOnly:",
        Dictionary,
    )

    assert Publish < Dictionary < Consumer


def test_single_component_selection_freezes_raw_tracks_before_route():
    """A compact portfolio has one raw selector and no post-selection solve."""
    Source = _Sources(
        PlacementRoutingAttempts.SolvePrePlacementCapacityProblem,
        PlacementRoutingAttempts.MaterializeRawTemplate,
        PlacementSetup.PreparePlacementRouting,
        PlacementCandidateRouting.RoutePlacementCandidates,
    )
    RawDomain = Source.index(
        "RawDomain = PrepareRawTrackAssignmentDomain("
    )
    Selection = Source.index(
        "SolveRawTrackAssignmentPortfolioWithContext(",
        RawDomain,
    )
    FrozenPreparation = Source.index(
        "Context.SelectedTrackPreparation = Context.RawTrackAssignmentResult.Preparation",
        Selection,
    )
    MultiComponentPreparation = Source.index(
        "Preparation = PrepareTrackAssignment("
    )
    LegacyPreparation = Source.index(
        "Context.SelectedTrackPreparation = PrepareTrackAssignment(",
        FrozenPreparation,
    )
    FirstRoute = Source.index("Context.Services.RoutePcbDesign(", LegacyPreparation)

    # Each fixed candidate exports its raw native values, then one aggregate
    # selector supplies the selected frozen witness.  The remaining ordinary
    # preparation belongs only to the legacy multi-component compatibility
    # path; it cannot run on the single packed component path.  The first
    # call prepares a legacy candidate before selection, and the second is a
    # defensive fallback guarded by a missing frozen witness.
    assert Source.count("PrepareRawTrackAssignmentDomain(") == 1
    assert Source.count("SolveRawTrackAssignmentPortfolioWithContext(") == 1
    assert Source.count("PrepareTrackAssignment(") == 2
    assert RawDomain < Selection < FrozenPreparation < LegacyPreparation
    assert MultiComponentPreparation < Selection
    assert "if Context.SelectedTrackPreparation is None:" in Source[
        FrozenPreparation:LegacyPreparation
    ]
    assert LegacyPreparation < FirstRoute


def test_success_publishes_authoritative_selection_fingerprint():
    Source = inspect.getsource(PlacementPublication.PublishPlacementFlowResult)
    RawFingerprint = Source.index(
        "Context.RawTrackAssignmentResult.SelectionFingerprint"
    )
    InterfaceFingerprint = Source.index(
        "else Context.PreRouteInterfaceResult.SelectionFingerprint",
        RawFingerprint,
    )
    Publication = Source.index(
        "RoutingControlEffectiveness['CandidateFingerprint']",
        InterfaceFingerprint,
    )

    assert RawFingerprint < InterfaceFingerprint < Publication


def test_multi_component_missing_access_assignment_uses_frozen_track_witness():
    """Legacy components must not be mislabeled missing small-design fabric."""
    Source = _Sources(
        PlacementRoutingAttempts.SolvePrePlacementCapacityProblem,
        PlacementSetup.PreparePlacementRouting,
    )
    MissingAssignment = Source.index("if AccessAssignment is None:")
    Preparation = Source.index(
        "Preparation = PrepareTrackAssignment(",
        MissingAssignment,
    )
    StoredWitness = Source.index(
        "Context.PrePlacementTrackPreparationWitnesses[",
        Preparation,
    )
    PublishedWitness = Source.index(
        "Context.PrePlacementTrackPreparationWitnesses.get(",
        StoredWitness,
    )
    SelectedWitness = Source.index(
        "Context.SelectedTrackPreparation = Context.PrePlacementTrackPreparationWitnesses.get(",
        PublishedWitness,
    )
    DefensiveFallback = Source.index(
        "if Context.SelectedTrackPreparation is None:",
        SelectedWitness,
    )

    assert "missing-access-assignment" not in Source[
        MissingAssignment:Preparation
    ]
    assert Preparation < StoredWitness < PublishedWitness < SelectedWitness
    assert SelectedWitness < DefensiveFallback


def test_single_component_defers_derived_fabric_until_raw_materialization():
    """A declared shell ranks first; its escape search is not eager work."""
    Source = _Sources(
        PlacementSetup.GeneratePlacementCandidates,
        PlacementRoutingAttempts.MaterializeRawTemplate,
    )
    Shell = Source.index("Context.Shell = BuildDerivedPerimeterFabricShell(")
    Descriptor = Source.index("Context.PreRouteFabricDescriptorsByCandidateId[")
    DeferredCandidate = Source.index(
        "Context.FabricCandidateRecords.append(Context.DescriptorCandidate)",
    )
    Materializer = Source.index("def MaterializeRawTemplate(")
    Fabric = Source.index("Fabric = BuildPlacementAccessFabric(", Materializer)
    RawDomain = Source.index("RawDomain = PrepareRawTrackAssignmentDomain(")
    Attached = Source.index(
        "AttachedPlacement = AttachPlacementAccessFabric(",
        Materializer,
    )

    assert Shell < Descriptor < DeferredCandidate < Materializer
    assert Materializer < Fabric < Attached < RawDomain
    assert "Shell=FabricDescriptor.Shell" in Source[Fabric:RawDomain]


def test_single_component_selected_contract_cannot_reenter_legacy_portfolio():
    """One selected packed contract owns the whole remaining route attempt."""
    Source = _Sources(
        PlacementSetup.PreparePlacementRouting,
        PlacementCandidateRouting.RoutePlacementCandidates,
    )
    ExactGate = Source.index("Context.ExactClusterInterfaceSolveEnabled =")
    DeferredAlternatives = Source.index(
        "Context.HasRemainingPlacementAlternative =",
        ExactGate,
    )
    SingleAttemptSlots = Source.index(
        "Context.PlannedRoutingSlots =",
        DeferredAlternatives,
    )
    Route = Source.index("Context.Services.RoutePcbDesign(", SingleAttemptSlots)

    ExactGateSource = Source[ExactGate:DeferredAlternatives]
    RouteBudgetSource = Source[DeferredAlternatives:Route]
    assert "not Context.SinglePackedComponent" in ExactGateSource
    assert (
        "Context.HasRemainingPlacementAlternative = False "
        "if Context.SinglePackedComponent"
    ) in RouteBudgetSource
    assert (
        "Context.PlannedRoutingSlots = 1 "
        "if Context.SinglePackedComponent"
    ) in RouteBudgetSource


def test_pre_route_fabric_summary_exposes_frontier_not_all_stub_claims():
    """Failure artifacts keep the proof frontier without duplicating stubs."""
    IncompleteDomain = SimpleNamespace(
        Signal="Signal",
        Terminal=(1, 2, 3),
        EscapeStubs=(object(), object()),
        Complete=False,
        IncompleteReason="no-legal-fabric-escape",
    )
    CompleteDomain = SimpleNamespace(
        Signal="Other",
        Terminal=(4, 5, 6),
        EscapeStubs=(object(),),
        Complete=True,
        IncompleteReason="",
    )
    Fabric = SimpleNamespace(
        FabricFingerprint="fabric",
        TopologyKind="derived-perimeter-access-v1",
        Complete=False,
        IncompleteReason="no-legal-fabric-escape",
        AccessRingTrackCount=1,
        AccessRingFingerprint="ring",
        OuterBounds=(-2, -3, 8, 9),
        ActiveFaces=("north", "south"),
        Nodes=((0, 1, 0),),
        Edges=(),
        CapacityResourceIds=("resource",),
        TerminalDomains=(IncompleteDomain, CompleteDomain),
        LegalEscapeExpansionCount=41,
        LegalEscapeExpansionLimit=64,
        LegalEscapeWorkLimitKind="derived-direction-state-v1",
        LegalEscapeDirectionStateUpperBound=128,
        PhysicalClaims=SimpleNamespace(
            WireCells=frozenset({(0, 1, 0)}),
            SupportCells=frozenset(),
            RequiredAirCells=frozenset(),
            ElectricalCells=frozenset(),
        ),
    )

    Summary = SummarizePreRouteAccessFabric(Fabric)

    assert Summary is not None
    assert Summary["TerminalDomainCount"] == 2
    assert Summary["CompleteTerminalDomainCount"] == 1
    assert Summary["LegalEscapeExpansionLimit"] == 64
    assert Summary["LegalEscapeWorkLimitKind"] == "derived-direction-state-v1"
    assert Summary["LegalEscapeDirectionStateUpperBound"] == 128
    assert Summary["IncompleteTerminalDomains"] == [{
        "Signal": "Signal",
        "Terminal": [1, 2, 3],
        "EscapeStubCount": 2,
        "IncompleteReason": "no-legal-fabric-escape",
    }]
    assert "TerminalDomains" not in Summary
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


def test_prepare_track_assignment_stops_before_route_tree_construction(
    monkeypatch: pytest.MonkeyPatch,
):
    Expected = TrackAssignmentPreparation(
        Success=True,
        SelectedCandidateIds=(("Signal", "candidate"),),
        CandidateCounts=(("Signal", 1),),
        ConflictSignals=(),
        ConflictResourceIndices=(),
        ExpansionCount=1,
        Complete=True,
    )
    Calls: list[dict[str, object]] = []

    def Prepare(*_Arguments: object, **KeywordArguments: object) -> None:
        Calls.append(dict(KeywordArguments))
        raise TrackAssignmentPrepared(Expected)

    monkeypatch.setattr(Pcb, "BuildPcbRoutingConfigurations", lambda _Value: (object(),))
    monkeypatch.setattr(Pcb, "RoutePcbAttempt", Prepare)

    Actual = Pcb.PrepareTrackAssignment(
        SimpleNamespace(),
        Resources=SimpleNamespace(),
        Policy=DefaultPhysicalDesignPolicy,
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert Actual is Expected
    assert len(Calls) == 1
    assert Calls[0]["Policy"] is DefaultPhysicalDesignPolicy
    assert Calls[0]["PrepareTrackAssignmentOnly"] is True
    assert Calls[0][
        "DeferClusterBoundaryLeaseUntilCapacityPrecheck"
    ] is False
    assert isinstance(Calls[0]["Deadline"], RoutingDeadline)

    Pcb.PrepareTrackAssignment(
        SimpleNamespace(),
        Resources=SimpleNamespace(),
        Policy=DefaultPhysicalDesignPolicy,
        Deadline=RoutingDeadline.Start(1.0),
        DeferClusterBoundaryLeaseUntilCapacityPrecheck=True,
    )
    assert Calls[1][
        "DeferClusterBoundaryLeaseUntilCapacityPrecheck"
    ] is True


def test_prepare_raw_track_assignment_domain_stops_before_assignment(
    monkeypatch: pytest.MonkeyPatch,
):
    """The portfolio bridge exports values, not a second native solve."""
    Position = (1, 1, 1)
    Expected = AuthoritativeRunModels.RawTrackAssignmentDomain(
        ResourcePositions=(Position,),
        Values=(AuthoritativeRunModels.RawTrackAssignmentValue(
            Signal="Signal",
            CandidateId="candidate",
            Claims=RoutingResourceClaims(WireCells=frozenset({Position})),
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
        ),),
        BaseClaims=(),
        CandidateCounts=(("Signal", 1),),
        CandidateDomainFingerprint="candidate-domain",
        LocalClaimDomainFingerprint="local-domain",
        PlacementFingerprint="placement",
        ResourceGraphFingerprint="resources",
        PortalDomainFingerprint="portals",
        Complete=True,
        MaximumAssignmentExpansions=16,
    )
    Calls: list[dict[str, object]] = []

    def Prepare(*_Arguments: object, **KeywordArguments: object) -> None:
        Calls.append(dict(KeywordArguments))
        raise AuthoritativeRunModels.RawTrackAssignmentDomainPrepared(
            Expected
        )

    monkeypatch.setattr(
        Pcb,
        "BuildPcbRoutingConfigurations",
        lambda _Value: (object(),),
    )
    monkeypatch.setattr(Pcb, "RoutePcbAttempt", Prepare)

    Actual = Pcb.PrepareRawTrackAssignmentDomain(
        SimpleNamespace(),
        Resources=SimpleNamespace(),
        Policy=DefaultPhysicalDesignPolicy,
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert Actual is Expected
    assert len(Calls) == 1
    assert Calls[0]["Policy"] is DefaultPhysicalDesignPolicy
    assert Calls[0]["PrepareRawTrackAssignmentDomainOnly"] is True
    assert "PrepareTrackAssignmentOnly" not in Calls[0]
    assert isinstance(Calls[0]["Deadline"], RoutingDeadline)


def test_pre_global_symbolic_capacity_proof_rejects_only_exact_port_tuple():
    Port = SimpleNamespace(
        Signal="Alpha",
        Direction="input",
        FabricDomainFingerprint="fabric",
        FabricAttachment=(0, 2, 0),
        Attachment=(2, 2, 0),
        LocalPath=((0, 2, 0), (1, 2, 0), (2, 2, 0)),
        GlobalPath=((2, 2, 0), (2, 2, -1)),
        OwnedTerminals=((0, 2, 0),),
        OwnedAccessCandidates=(),
        Capacity=1,
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="port-assignment",
        Ports=(Port,),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
        RejectedPhysicalComponentAssemblyChoiceFingerprints=set(),
        RejectedPhysicalComponentPortReservationSets=set(),
        PreferredPhysicalComponentGlobalContractsBySignal={},
        PhysicalComponentBoundaryTraversalEpoch=0,
        PhysicalComponentBoundaryAssignmentIteratorCache={"stale": object()},
    )
    Proof = ComponentRoutingSolveResult(
        Status="architectural-unsatisfiable",
        ProofFingerprint="capacity-proof",
        Diagnostics={
            "SymbolicCapacityProofComplete": True,
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreSignals": ["Alpha", "InternalNet"],
        },
    )

    Diagnostics = RecordPhysicalComponentSymbolicCapacityEligibilityNoGood(
        Proof,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "pre-global-symbolic-capacity-port-assignment"
    )
    assert Resources.RejectedPhysicalComponentPortAssignmentFingerprints == {
        "port-assignment"
    }
    assert Resources.RejectedPhysicalComponentAssemblyPlanFingerprints == {
        "physical-plan"
    }
    assert Resources.PreferredPhysicalComponentGlobalContractsBySignal == {}
    assert set(Resources.PhysicalComponentBoundaryAssignmentIteratorCache) == {
        "stale"
    }
    assert Diagnostics["BoundaryIteratorContinuationPreserved"] is True
    assert Diagnostics["BoundaryIteratorCacheCleared"] is False
    assert Resources.PhysicalComponentBoundaryTraversalEpoch == 0
    assert Diagnostics["GlobalPlanningEntered"] is False
    assert Diagnostics["LocalCompilationEntered"] is False
    assert Diagnostics["LocalCapacityCorePromoted"] is True
    assert Diagnostics["LocalCapacityCoreSignals"] == [
        "Alpha",
        "InternalNet",
    ]
    assert Diagnostics["LocalCapacityProjectedInterfaceCoreSignals"] == [
        "Alpha"
    ]
    ExpectedClause = frozenset(((
        "Alpha",
        BuildPhysicalPortSeamContractFingerprint(Port),
    ),))
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        ExpectedClause
    }
    assert Resources.RejectedPhysicalComponentLocalSeamReservationSets == {
        ExpectedClause
    }


def test_seam_domain_cache_identity_includes_every_composite_restriction():
    Source = inspect.getsource(
        PhysicalPortSearch._SolvePreparedPhysicalComponentPortFactorDomain
    )

    HelperStart = Source.index("def BuildSeamOnlyPortDomainKey")
    HelperEnd = Source.index("def SelectSeamOnlyPorts", HelperStart)
    HelperSource = Source[HelperStart:HelperEnd]
    assert "ActiveApertureContractRestrictionsBySignal" in HelperSource
    assert "ActiveLocalAccessRestrictionsBySignal" in HelperSource
    assert "ActiveSeamRestrictionsBySignal" in HelperSource
    assert "ActiveSupportRestrictionsBySignal" in HelperSource
    assert Source.count("BuildSeamOnlyPortDomainKey(Signal)") >= 2


def test_deferred_local_selection_keeps_boundary_csp_port_first():
    Source = inspect.getsource(
        PhysicalPortSearch.IterPreparedPhysicalBoundaryAssignments
    )

    assert "IncludeLocalCompositeFactors = not DeferLocalCompositeSelection" in Source
    assert "CertifiedNoGoodProjectionOnly=DeferLocalCompositeSelection" in Source
    assert "PreferredApertureContractsBySignal" in Source


def test_complete_local_pair_domain_promotes_to_aperture_cut():
    def LocalPort(Signal, Offset):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric:" + Signal,
            FabricAttachment=(Offset, 2, 0),
            Attachment=(Offset + 2, 2, 0),
            LocalPath=((Offset, 2, 0), (Offset + 1, 2, 0)),
            GlobalPath=((Offset + 2, 2, 0),),
            OwnedTerminals=((Offset, 2, 0),),
            OwnedAccessCandidates=(),
            LocalAccessFingerprint="access:" + Signal,
            Capacity=1,
        )

    Ports = (LocalPort("Alpha", 0), LocalPort("Beta", 10))
    Boundaries = tuple(
        SimpleNamespace(
            Signal=Port.Signal,
            GlobalContractFingerprint="global:" + Port.Signal,
            ApertureContractFingerprint="aperture:" + Port.Signal,
        )
        for Port in Ports
    )
    Apertures = tuple(
        SimpleNamespace(
            GlobalContractFingerprint=Boundary.GlobalContractFingerprint,
            ApertureContractFingerprint=(
                Boundary.ApertureContractFingerprint
            ),
            ApertureOptionFingerprint="option:" + Boundary.Signal,
        )
        for Boundary in Boundaries
    )
    FactorDomain = SimpleNamespace(
        LocalAccessFactorsBySignal=tuple(
            (Port.Signal, (Port,)) for Port in Ports
        ),
        ApertureFactorsBySignal=tuple(
            (Boundary.Signal, (Aperture,))
            for Boundary, Aperture in zip(Boundaries, Apertures)
        ),
        LocalApertureSupportsByOption=tuple(
            (
                (Boundary.Signal, Aperture.ApertureOptionFingerprint),
                (SimpleNamespace(
                    LocalAccessFingerprint="access:" + Boundary.Signal,
                ),),
            )
            for Boundary, Aperture in zip(Boundaries, Apertures)
        ),
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="port-assignment",
        Ports=Ports,
        GlobalBoundaryPorts=Boundaries,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
        RejectedPhysicalComponentAssemblyChoiceFingerprints=set(),
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortReservationsBySignal={},
        PreferredPhysicalComponentGlobalContractsBySignal={},
        PhysicalComponentBoundaryTraversalEpoch=0,
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )
    Proof = ComponentRoutingSolveResult(
        Status="architectural-unsatisfiable",
        ProofFingerprint="capacity-proof",
        Diagnostics={
            "SymbolicCapacityProofComplete": True,
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreSignals": ["Alpha", "Beta"],
        },
    )

    Diagnostics = RecordPhysicalComponentSymbolicCapacityEligibilityNoGood(
        Proof,
        Plan,
        Resources,
        FactorDomain,
    )

    ApertureClause = frozenset((
        ("Alpha", "aperture:Alpha"),
        ("Beta", "aperture:Beta"),
    ))
    assert ApertureClause in (
        Resources.RejectedPhysicalComponentPortReservationSets
    )
    assert Diagnostics["LocalCapacityApertureClausesPromoted"] == [[
        ["Alpha", "aperture:Alpha"],
        ["Beta", "aperture:Beta"],
    ]]
    assert Diagnostics["BoundaryIteratorContinuationPreserved"] is True


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


def test_translated_port_options_with_portable_alias_project_distinct_apertures():
    FactorDomain, Certificate = _PhysicalPairApertureProjectionFixture()

    Clauses, Diagnostics = (
        ProjectCompletePhysicalPortPairCertificateToApertureClauses(
            FactorDomain,
            Certificate,
        )
    )

    assert frozenset((("Alpha", "alpha-absolute-left"),)) in Clauses
    assert frozenset((("Alpha", "alpha-absolute-right"),)) in Clauses
    assert all(
        "portable-reservation" not in Fingerprint
        for Clause in Clauses
        for _Signal, Fingerprint in Clause
    )
    assert Diagnostics["ApertureProjectionComplete"] is True
    assert Diagnostics["ApertureProjectionOptionCount"] == 3


def test_incomplete_support_mapping_does_not_promote_aperture_cuts():
    FactorDomain, Certificate = _PhysicalPairApertureProjectionFixture(
        CompleteSupports=False,
    )

    Clauses, Diagnostics = (
        ProjectCompletePhysicalPortPairCertificateToApertureClauses(
            FactorDomain,
            Certificate,
        )
    )

    assert Clauses == frozenset()
    assert Diagnostics["ApertureProjectionComplete"] is False
    assert Diagnostics["ApertureProjectionFailureReason"] == (
        "prepared-support-domain-incomplete"
    )


def test_mandatory_singleton_seam_collapses_pair_cuts_to_unary_apertures():
    FactorDomain = SimpleNamespace(
        ApertureFactorsBySignal=(
            ('Alpha', (
                SimpleNamespace(
                    ApertureOptionFingerprint='alpha-left-option',
                    ApertureContractFingerprint='alpha-left-aperture',
                ),
                SimpleNamespace(
                    ApertureOptionFingerprint='alpha-right-option',
                    ApertureContractFingerprint='alpha-right-aperture',
                ),
            )),
            ('Beta', (
                SimpleNamespace(
                    ApertureOptionFingerprint='beta-near-option',
                    ApertureContractFingerprint='beta-near-aperture',
                ),
                SimpleNamespace(
                    ApertureOptionFingerprint='beta-far-option',
                    ApertureContractFingerprint='beta-far-aperture',
                ),
            )),
        ),
        LocalApertureSupportBySignal=(
            ('Alpha', (
                SimpleNamespace(
                    ApertureOptionFingerprint='alpha-left-option',
                    LocalAccessFingerprint='alpha-left-access',
                ),
                SimpleNamespace(
                    ApertureOptionFingerprint='alpha-right-option',
                    LocalAccessFingerprint='alpha-right-access',
                ),
            )),
            ('Beta', (
                SimpleNamespace(
                    ApertureOptionFingerprint='beta-near-option',
                    LocalAccessFingerprint='beta-access',
                ),
                SimpleNamespace(
                    ApertureOptionFingerprint='beta-far-option',
                    LocalAccessFingerprint='beta-access',
                ),
            )),
        ),
    )
    Certificate = SimpleNamespace(
        Complete=True,
        SignalPair=('Alpha', 'Beta'),
        LocalAccessFingerprintsBySignal=(
            ('Alpha', ('alpha-left-access', 'alpha-right-access')),
            ('Beta', ('beta-access',)),
        ),
        SeamFingerprintByLocalAccess=(
            ('Alpha', 'alpha-left-access', 'alpha-left-seam'),
            ('Alpha', 'alpha-right-access', 'alpha-right-seam'),
            ('Beta', 'beta-access', 'beta-seam'),
        ),
        UnsupportedUnarySeams=(),
        UnsupportedSeamPairs=((
            ('Alpha', 'alpha-left-seam'),
            ('Beta', 'beta-seam'),
        ),),
    )

    Clauses, Diagnostics = (
        ProjectCompletePhysicalPortPairCertificateToApertureClauses(
            FactorDomain,
            Certificate,
        )
    )

    assert Clauses == frozenset((
        frozenset((('Alpha', 'alpha-left-aperture'),)),
    ))
    assert Diagnostics['ApertureProjectionClauseCount'] == 1
    assert Diagnostics['ApertureProjectionUnaryClauseCount'] == 1
    assert Diagnostics['ApertureProjectionBinaryClauseCount'] == 0
    assert Diagnostics['ApertureProjectionEmptySignals'] == []


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


def test_local_unsat_rejects_only_the_complete_assembly_plan():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Start = Source.index("ComponentSolve = CompileClosedComponent(")
    End = Source.index("assert Context.ComponentSolve.Template is not None", Start)
    LocalCompilation = Source[Start:End]

    assert "RecordPhysicalComponentLocalCompilationNoGood" in (
        LocalCompilation
    )
    assert "GlobalChannelDesign" in LocalCompilation
    assert "RejectedPhysicalComponentPortAssignmentFingerprints" not in (
        LocalCompilation
    )
    assert "RejectedPhysicalComponentPortReservationsBySignal" not in (
        LocalCompilation
    )
    assert "RejectedPhysicalComponentPortReservationSets" not in (
        LocalCompilation
    )
    assert "local-unsat-reject-complete-assembly-plan" in LocalCompilation
    assert "'PerSignalReservationFeedbackUsed': False" in LocalCompilation
    assert "ReplanPhysicalAssemblyWithTiming(" in LocalCompilation


def test_detailed_failure_rejects_exact_channels_not_port_assignment():
    Plan = SimpleNamespace(
        PlanFingerprint="plan",
        PortAssignmentFingerprint="ports",
        Channels=(
            SimpleNamespace(Signal="A", RouteCandidateId="route-a"),
            SimpleNamespace(Signal="B", RouteCandidateId="route-b"),
        ),
    )
    Design = SimpleNamespace(
        RoutingAssignment=SimpleNamespace(SelectedCandidates={
            "A": SimpleNamespace(CandidateId="route-a"),
            "B": SimpleNamespace(CandidateId="route-b"),
        }),
    )
    Resources = SimpleNamespace(
        ForbiddenPhysicalComponentGlobalCandidateSets=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )

    Diagnostics = RecordPhysicalComponentDetailedRoutingNoGood(
        Plan,
        Design,
        Resources,
    )

    assert Resources.ForbiddenPhysicalComponentGlobalCandidateSets == {
        frozenset((("A", "route-a"), ("B", "route-b")))
    }
    assert Resources.RejectedPhysicalComponentAssemblyPlanFingerprints == {
        "plan"
    }
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Diagnostics["ForbiddenGlobalCandidateSet"] == [
        ["A", "route-a"],
        ["B", "route-b"],
    ]
    assert Diagnostics["RejectedPhysicalAssemblyPlanFingerprint"] == "plan"
    assert Diagnostics["PortAssignmentRejected"] is False
    with pytest.raises(ValueError, match="assignment identity mismatch"):
        RecordPhysicalComponentDetailedRoutingNoGood(
            SimpleNamespace(
                PlanFingerprint="different-plan",
                PortAssignmentFingerprint="ports",
                Channels=(SimpleNamespace(
                    Signal="A",
                    RouteCandidateId="different",
                ),),
            ),
            Design,
            Resources,
        )


def test_detailed_failure_orchestration_has_no_broad_port_rejection():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Start = Source.index(
        "'detailed-failure-reject-physical-plan'",
    )
    End = Source.index(
        "Context.PreparedAssembly =",
        Start,
    )
    Rejection = Source[Start:End]

    assert "RecordPhysicalComponentDetailedRoutingNoGood(" in Source[:Start]
    assert "RejectedPhysicalComponentPortAssignmentFingerprints" not in (
        Rejection
    )
    assert "RejectedPortAssignmentFingerprint" not in Rejection


def test_local_compilation_requires_explicit_admission_without_floor():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Start = Source.index(
        "Context.ActiveComponentDeadline = Context.SharedInterfaceDeadline"
    )
    Compile = Source.index("ComponentSolve = CompileClosedComponent(", Start)
    End = Source.index("if not Context.ComponentSolve.Feasible:", Compile)
    Admission = Source[Start:Compile]
    Invocation = Source[Compile:End]

    assert "BuildLocalComponentCompilationAdmissionFailure(" in Admission
    assert "ActiveComponentRemainingSeconds <= 0" in Admission
    assert "DeadlineSeconds=Context.ActiveComponentRemainingSeconds" in Invocation
    assert "ActiveComponentRemainingSeconds" in Invocation
    assert "max(" not in Invocation


def test_admitted_local_compilation_is_not_reclassified_by_planning_clock():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Compile = Source.index("ComponentSolve = CompileClosedComponent(")
    Result = Source.index("if not Context.ComponentSolve.Feasible:", Compile)
    Template = Source.index(
        "assert Context.ComponentSolve.Template is not None",
        Result,
    )
    Classification = Source[Result:Template]

    assert "InterfaceDeadline.IsExpired()" not in Classification
    assert "Stage='ClosedComponentCompilationIncomplete'" in Classification
    assert "RecordPhysicalComponentLocalCompilationNoGood(" in Classification


def test_physical_planning_uses_planning_clock_until_bound_handoff():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Schedule = Source.index("BuildClusterInterfaceStageSchedule(")
    PlanningDeadline = Source.index(
        "SharedInterfacePlanningDeadline = RoutingDeadline(",
        Schedule,
    )
    StateDeadline = Source.index(
        "Context.InterfaceDeadline = Context.SharedInterfacePlanningDeadline",
        PlanningDeadline,
    )
    ProofGuidedDeadline = Source.index(
        "Context.InterfaceDeadline = Context.ProofGuidedInterfacePlanningDeadline",
        StateDeadline,
    )
    Admission = Source.index(
        "if Context.InterfaceDeadline.IsExpired():",
        StateDeadline,
    )
    Preparation = Source.index(
        "PreparePhysicalComponentEligibility(",
        Admission,
    )

    assert (
        Schedule
        < PlanningDeadline
        < StateDeadline
        < ProofGuidedDeadline
        < Admission
        < Preparation
    )
    Selection = Source[StateDeadline:Admission]
    assert (
        "Context.InterfaceDeadline = Context.SharedInterfacePlanningDeadline"
        in Selection
    )
    assert "Context.InterfaceDeadline = Context.Deadline" not in Selection
    assert "if Context.RetainedPlacementFingerprint in" in Selection
    assert "Context.GeneratedProofGuidedPlacementFingerprints" in Selection
    assert (
        "Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint"
        in Selection
    )
    assert (
        "Context.InterfaceDeadline = "
        "Context.AccessRepairInterfaceDeadline"
        in Selection
    )


def test_capacity_repair_local_proof_preserves_global_routing_reserve():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)

    assert (
        "min(Context.InterfaceDeadline.ExpiresAt, "
        "Context.Deadline.ExpiresAt - 2.0)"
        in Source
    )
    assert (
        "Context.Deadline if Context.CapacityRepairConstraint is not None "
        "else Context.InterfaceDeadline"
        not in Source
    )
    assert (
        "Context.InterfaceDeadline = Context.AccessRepairInterfaceDeadline "
        "if Context.CapacityRepairConstraint is not None"
        in Source
    )
    assert (
        "Context.CapacityRepairConstraint is not None or ("
        in Source
    )

    PortalSource = inspect.getsource(
        AuthoritativePortalPreparation.RunPortalPreparation
    )
    assert "'TelemetryDisposition': 'compact-success-summary'" in PortalSource
    assert "PreparedAccessCertificate.ToDictionary()" in PortalSource


def test_stage_specific_incomplete_failures_preserve_handoff_identity():
    Source = _Sources(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels,
        PlacementPhysicalFlow.RunPhysicalComponentFlow,
    )
    Compile = Source.index("ComponentSolve = CompileClosedComponent(")
    BeforeCompile = Source[:Compile]
    AfterCompile = Source[Compile:]

    assert BeforeCompile.count(
        "BuildPhysicalAssemblyPlanningIncompleteFailure("
    ) == 3
    assert BeforeCompile.count(
        "BuildLocalComponentCompilationAdmissionFailure("
    ) == 1
    assert "BuildClosedComponentExecutionIncompleteFailure(" not in (
        BeforeCompile
    )
    assert "BuildClosedComponentExecutionIncompleteFailure(" not in (
        AfterCompile
    )
    assert "'ClosedComponentCompilationIncomplete'" in AfterCompile
    assert "PhysicalAssemblyPlan.PlanFingerprint" in AfterCompile


def test_unbound_owned_frontier_core_requires_complete_independence():
    Problem = SimpleNamespace(
        Interface=SimpleNamespace(PhysicalPortReservations=()),
        ReservedGlobalClaimsBySignal={},
    )
    SignalProof = {
        "Complete": True,
        "EmptyPhase": "owned-terminal-frontier",
        "OwnedSignalDomainContractIndependent": True,
        "CertifiedRejectedCandidateCount": 0,
    }
    Result = SimpleNamespace(
        Status="architectural-unsatisfiable",
        Template=None,
        Diagnostics={
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreKind": (
                "tree-frontier-empty-owned-signal-domain"
            ),
            "LocalUnsatCoreSignals": ["NandLike"],
            "LocalUnsatCoreProjectionFingerprint": "projection",
            "SignalDiagnostics": {"NandLike": SignalProof},
        },
    )

    assert SelectContractIndependentOwnedSignalFrontierUnsatCore(
        Problem,
        Result,
    ) == ("NandLike",)
    MultipleEmptySignals = SimpleNamespace(
        **{
            **Result.__dict__,
            "Diagnostics": {
                **Result.Diagnostics,
                "LocalUnsatCoreSignals": ["Zulu", "Alpha"],
                "SignalDiagnostics": {
                    "Zulu": SignalProof,
                    "Alpha": SignalProof,
                },
            },
        }
    )
    assert SelectContractIndependentOwnedSignalFrontierUnsatCore(
        Problem,
        MultipleEmptySignals,
    ) == ("Alpha",)
    Incomplete = SimpleNamespace(
        **{**Result.__dict__, "Status": "incomplete"}
    )
    assert SelectContractIndependentOwnedSignalFrontierUnsatCore(
        Problem,
        Incomplete,
    ) == ()
    Dependent = SimpleNamespace(
        **{
            **Result.__dict__,
            "Diagnostics": {
                **Result.Diagnostics,
                "SignalDiagnostics": {
                    "NandLike": {
                        **SignalProof,
                        "OwnedSignalDomainContractIndependent": False,
                    }
                },
            },
        }
    )
    assert SelectContractIndependentOwnedSignalFrontierUnsatCore(
        Problem,
        Dependent,
    ) == ()
    BoundProblem = SimpleNamespace(
        Interface=SimpleNamespace(
            PhysicalPortReservations=(SimpleNamespace(),),
        ),
        ReservedGlobalClaimsBySignal={},
    )
    assert SelectContractIndependentOwnedSignalFrontierUnsatCore(
        BoundProblem,
        Result,
    ) == ()


def test_unbound_frontier_callback_precedes_port_factor_preparation():
    Source = inspect.getsource(
        AuthoritativePortalPreparation.RunPortalPreparation
    )
    Problem = Source.index(
        "PreparedAccessProblem = Services.BuildComponentRoutingProblem("
    )
    Callback = Source.index(
        "State.UnboundOwnedSignalFrontierProofCallback(",
        Problem,
    )
    Access = Source.index(
        "Services.BuildComponentCutAccessFeasibilityCertificate(",
        Callback,
    )
    Factors = Source.index(
        "Preparation = Services.PreparePhysicalComponentPortFactorDomain(",
        Callback,
    )

    assert Problem < Callback < Access < Factors
    assert "RequiredGuideCellsBySignal=" in Source[Access:Factors]
    assert "BuildComponentAccessGuideTargetColumns(" in Source[
        Callback:Access
    ]
    assert "ComponentAccessGuideTargetHandoff" in Source[Callback:Access]
    assert "ActiveAccessCertificateSignals = tuple(getattr(" in Source[
        Callback:Access
    ]
    assert "PrioritySignals=ActiveAccessCertificateSignals" in Source[
        Access:Factors
    ]


def test_unbound_frontier_failure_exports_minimal_placement_core():
    CallbackSource = inspect.getsource(
        PlacementPhysicalAssembly.ProveUnboundOwnedSignalFrontier
    )

    assert "'PortAssignmentUnsatCoreMinimal': True" in CallbackSource
    assert "'PortAssignmentUnsatCoreSignals': list(" in CallbackSource
    assert "'PortAssignmentUnsatCoreFingerprint':" in CallbackSource


def test_proof_guided_siblings_precede_stale_placement_backlog():
    Source = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement
    )
    Selection = Source.index(
        "SelectFreshProofGuidedPlacementCandidate("
    )
    SiblingDeferral = Source.index(
        "proof-guided-backlog-deferred-for-retained-sibling"
    )
    PendingMaterialization = Source.index(
        "JointState = Context.PendingJointPlacementStates.pop(0)"
    )

    assert Selection < SiblingDeferral < PendingMaterialization


def test_queued_generated_proof_candidate_precedes_another_generation():
    Source = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement
    )

    assert "QueuedGeneratedProofBlockers" in Source
    assert (
        "AssignmentCut is not None or QueuedGeneratedProofBlockers"
        in Source
    )

    FlowSource = inspect.getsource(
        PlacementPhysicalFlow.RunPhysicalComponentFlow
    )
    assert "Context.PendingRequiresRetainedDrain" in FlowSource
    assert "Context.PendingBlockerPlacementFingerprints" in FlowSource
    assert "Context.QueuedEligibilityPlacementFingerprints" in FlowSource
    assert "Context.CompletedEligibilityPlacementFingerprints" in FlowSource
    assert "Context.PendingUnresolvedBlockerPlacementFingerprints" in FlowSource
    assert "Context.PendingCapacityRepair" in FlowSource
    assert "'proof-guided-pending-admission'" in FlowSource
    assert "proof-guided-pending-capacity-repair-preserved" in Source
    assert "CompletedProofGuidedPlacementFingerprints" in Source
    assert (
        "QueuedGeneratedProofBlockers -= "
        "CompletedProofGuidedPlacementFingerprints"
        in Source
    )
    assert "if SelectedFreshProofGeneration:" in Source
    assert (
        "Context.GeneratedProofGuidedPlacementFingerprints.add("
        in Source
    )


def test_proof_guided_descendant_inherits_owned_frontier_topology_kind():
    Context = SimpleNamespace(
        OwnedFrontierTopologyRepairKindByPlacementFingerprint={
            "source": "relocate-endpoint-cluster",
        },
        OwnedFrontierTopologyRepairSignalsByPlacementFingerprint={
            "source": ("A0", "A1", "B0", "B1"),
        },
    )

    Kind = (
        PlacementPhysicalAssembly.InheritOwnedFrontierTopologyRepairKind(
            Context,
            "source",
            "descendant",
        )
    )

    assert Kind == "relocate-endpoint-cluster"
    assert Context.OwnedFrontierTopologyRepairKindByPlacementFingerprint == {
        "source": "relocate-endpoint-cluster",
        "descendant": "relocate-endpoint-cluster",
    }
    assert Context.OwnedFrontierTopologyRepairSignalsByPlacementFingerprint == {
        "source": ("A0", "A1", "B0", "B1"),
        "descendant": ("A0", "A1", "B0", "B1"),
    }


def test_symbolic_capacity_core_composes_with_prior_topology_core():
    CumulativeSignals = {"B1"}

    Combined = (
        PlacementPhysicalAssembly.AccumulateProofGuidedRelocationSignals(
            CumulativeSignals,
            ("Generate1",),
            Reset=False,
        )
    )

    assert Combined == frozenset(("B1", "Generate1"))
    assert CumulativeSignals == {"B1", "Generate1"}


def test_topology_repaired_descendant_uses_alternate_joint_branch():
    Select = (
        PlacementPhysicalAssembly
        .SelectInheritedTopologyJointPlacementCandidateIndex
    )

    assert Select("split-interface-cut") == 1
    assert Select("relocate-endpoint-cluster") == 1
    assert Select(
        "relocate-endpoint-cluster",
        ComposedSignalCount=3,
    ) == 0
    assert Select("") == 0


def test_proof_guided_sibling_preserves_access_repair_routing_reserve():
    Source = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement
    )
    PendingMaterialization = Source.index(
        "JointState = Context.PendingJointPlacementStates.pop(0)"
    )
    SiblingMaterialization = Source[PendingMaterialization:]

    assert Source.count("PlacementGenerationNotAfter=") == 2
    assert Source.count("UseCompletePlacementGenerationBudget=True") == 2
    assert Source.count("AllowCapacityPairRepair=CapacityRepairActive") >= 2
    assert (
        "PlacementGenerationNotAfter="
        "Context.AccessRepairInterfacePlanningDeadline.ExpiresAt"
        in SiblingMaterialization
    )


def test_owned_terminal_portals_precede_unbound_frontier_and_global_portals():
    Source = inspect.getsource(
        AuthoritativePortalPreparation.RunPortalPreparation
    )
    OwnedBatch = Source.index(
        "'PhysicalOwnedTerminalPortalEligibility'",
    )
    OwnedProblem = Source.index(
        "PreparedAccessProblem = Services.BuildComponentRoutingProblem(",
        OwnedBatch,
    )
    Callback = Source.index(
        "State.UnboundOwnedSignalFrontierProofCallback(",
        OwnedProblem,
    )
    Defer = Source.index(
        "PortalRequests = []",
        Callback,
    )
    GlobalBatch = Source.index(
        "GeneratePortalRequestBatch(PortalRequests,",
        Defer,
    )
    RawCache = Source.index(
        "State.EffectiveRawPortalCache = Services.RawPortalGeometryCache(",
        GlobalBatch,
    )

    assert OwnedBatch < OwnedProblem < Callback < Defer < GlobalBatch < RawCache
    assert "'Selective': True" in Source[Callback:Defer]


def test_unbound_frontier_preparation_never_scans_unowned_signals():
    Source = inspect.getsource(
        AuthoritativePortalPreparation.RunPortalPreparation
    )
    Selective = Source.index("SelectiveOwnedPortalPreparation = bool(")
    OwnedSignals = Source.index(
        "OwnedPortalPreparationSignals = frozenset(",
        Selective,
    )
    PreparedSignals = Source.index(
        "PortalPreparationSignals = (",
        OwnedSignals,
    )
    OrderedSignals = Source.index(
        "OrderedPortalSignals = tuple(sorted(",
        PreparedSignals,
    )
    PortalLoop = Source.index(
        "for SignalIndex, Signal in enumerate(OrderedPortalSignals):",
        OrderedSignals,
    )
    TerminalFilter = Source.index(
        "if SelectiveOwnedPortalPreparation:",
        PortalLoop,
    )
    TypedHandoff = Source.index(
        "and TypedStraightAccessPortals:",
        TerminalFilter,
    )
    GenericGeometry = Source.index(
        "AccessColumns =",
        TypedHandoff,
    )

    Selection = Source[Selective:OrderedSignals]
    assert "State.ClosedComponentOwnedTerminalPairs" in Selection
    assert "UnboundOwnedSignalFrontierProofCallback" not in Selection
    assert "if SelectiveOwnedPortalPreparation" in Selection
    assert Selective < OwnedSignals < PreparedSignals < OrderedSignals < PortalLoop
    assert PortalLoop < TerminalFilter < TypedHandoff < GenericGeometry


def test_typed_straight_access_claims_use_one_native_batch():
    Source = inspect.getsource(
        AuthoritativePortalPreparation.RunPortalPreparation
    )
    Rows = Source.index("TypedStraightAccessRows = []")
    Batch = Source.index(
        "Services.BuildRouteClaimsBatchWithTelemetry(",
        Rows,
    )
    Claims = Source.index(
        "TypedStraightClaims = tuple(",
        Batch,
    )
    Portal = Source.index(
        "Portal = Services.PinAccessPortal(",
        Claims,
    )

    assert Rows < Batch < Claims < Portal
    assert "'ActiveWorkerCount': int(ActiveWorkerCount)" in Source[Batch:Portal + 2000]


def test_typed_straight_access_native_batch_requires_bulk_work():
    Select = (
        AuthoritativePortalPreparation
        .ShouldUseNativeTypedStraightClaimBatch
    )

    assert not Select(
        NativeAvailable=True,
        WorkItemCount=18,
        WirePositionCount=54,
    )
    assert Select(
        NativeAvailable=True,
        WorkItemCount=64,
        WirePositionCount=512,
    )
    assert not Select(
        NativeAvailable=False,
        WorkItemCount=64,
        WirePositionCount=512,
    )


def test_typed_straight_access_defers_generic_portal_infrastructure():
    Source = inspect.getsource(
        AuthoritativePortalPreparation.RunPortalPreparation
    )
    EnsureDefinition = Source.index(
        "def EnsureGenericPortalInfrastructure()"
    )
    TypedHandoff = Source.index(
        "and TypedStraightAccessPortals:",
        EnsureDefinition,
    )
    TypedContinue = Source.index("continue", TypedHandoff)
    EnsureCall = Source.index(
        "EnsureGenericPortalInfrastructure()",
        TypedContinue,
    )
    CachePublication = Source.index(
        "AssignmentIndexed=State.Resources.ResourceGraph.BuildIndexedGraph",
        EnsureCall,
    )

    assert EnsureDefinition < TypedHandoff < TypedContinue < EnsureCall
    assert "if State.Context is not None else None" in Source[
        CachePublication:CachePublication + 300
    ]


def test_component_fabric_augmentation_protects_full_access_prefixes():
    ProblemSource = inspect.getsource(
        ComponentProblem.BuildComponentRoutingProblem
    )
    FabricSource = inspect.getsource(
        ComponentFabric.AugmentComponentRoutingFabric
    )

    assert "ProtectedAccessNodes=" in ProblemSource
    assert "ProtectedAccessClaims" in FabricSource
    assert "FindSelfClaimConflicts" in FabricSource


def test_component_only_guide_uses_owned_signal_slice():
    Source = inspect.getsource(
        AuthoritativeGuidePlanning.RunGuidePlanning
    )
    OwnedPairs = Source.index(
        "State.ClosedComponentOwnedTerminalPairs ="
    )
    GuideProfiles = Source.index(
        "GuidePlanningProfiles = (",
        OwnedPairs,
    )
    GuideFingerprint = Source.index(
        "BuildCapacityAwareGuideInputFingerprint(GuidePlanningProfiles,",
        GuideProfiles,
    )
    GuidePlan = Source.index(
        "BuildCapacityAwareGuidePlan(GuidePlanningProfiles,",
        GuideFingerprint,
    )
    RegionColumns = Source.index(
        "for Signal, Profile in GuidePlanningProfiles.items():",
        GuidePlan,
    )

    assert OwnedPairs < GuideProfiles < GuideFingerprint < GuidePlan < RegionColumns
    assert "SelectiveComponentGuidePlanning" in Source[OwnedPairs:GuideFingerprint]
    PairSelection = Source[OwnedPairs:GuideProfiles]
    assert "UnboundOwnedSignalFrontierProofCallback" not in PairSelection


@pytest.mark.parametrize(
    "Reason, ProofKey",
    (
        (
            RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            "PortAssignmentProofComplete",
        ),
        (
            RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
            "GlobalPlanDomainComplete",
        ),
    ),
)
def test_complete_physical_assembly_proof_survives_deadline_expiry(
    Reason,
    ProofKey,
):
    CompleteProof = IsCompletePhysicalAssemblyUnsatisfiable(
        Reason,
        {ProofKey: True},
    )

    assert CompleteProof
    assert not IsClusterInterfaceStateIncomplete(
        FailureReason=Reason,
        InterfaceDeadlineExpired=True,
        ComponentSolveStatus="",
        ExplicitCompleteUnsatProof=CompleteProof,
    )


def test_deadline_expiry_is_incomplete_without_complete_proof():
    Reason = RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    CompleteProof = IsCompletePhysicalAssemblyUnsatisfiable(Reason, {})

    assert not CompleteProof
    assert IsClusterInterfaceStateIncomplete(
        FailureReason=Reason,
        InterfaceDeadlineExpired=True,
        ComponentSolveStatus="",
        ExplicitCompleteUnsatProof=CompleteProof,
    )


def test_component_materialization_unsat_does_not_exhaust_sibling_selections():
    assert not IsClusterInterfaceStateIncomplete(
        FailureReason=(
            RoutingFailureReason.ClusterInterfaceArchitectureUnsatisfiable
        ),
        InterfaceDeadlineExpired=False,
        ComponentSolveStatus="",
        ExplicitCompleteUnsatProof=False,
    )
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    assert "duplicate-component-selection-proof-reused" in Source
    assert "and Proof.Exhaustive" in Source
    assert "ExpectedComponentStateFingerprints=tuple(sorted(" in Source
    assert (
        "PlacementPortfolioDomainComplete="
        "Context.PlacementPortfolioDomainComplete"
    ) in Source
    PortfolioGuard = Source.index(
        "'ClusterInterfacePlacementPortfolioIncomplete'",
    )
    DomainGuard = Source.index(
        "'ClusterInterfaceComponentStateDomainIncomplete'",
        PortfolioGuard,
    )
    assert PortfolioGuard < DomainGuard
    assert "'ArchitecturalUnsatisfiabilityProven': False" in Source


def test_explicit_complete_proof_overrides_stale_incomplete_status():
    assert not IsClusterInterfaceStateIncomplete(
        FailureReason=(
            RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        ),
        InterfaceDeadlineExpired=True,
        ComponentSolveStatus="incomplete",
        ExplicitCompleteUnsatProof=True,
    )


def test_complete_port_assignment_core_advances_placement_after_deadline():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Exhaustive = Source.index(
        "if Context.StateExhaustive and (not Context.StateIncomplete)"
    )
    Advance = Source.index(
        "if Context.ComponentAccessCoreSignals:",
        Exhaustive,
    )
    Reorder = Source.index(
        "ReorderRemainingPlacementsForAccessCore(",
        Advance,
    )

    assert "InterfaceDeadline.IsExpired()" not in Source[Advance:Reorder]


def test_access_core_replay_prioritizes_untried_singleton_local_eco(monkeypatch):
    Current = SimpleNamespace(PlacementFingerprint="current")
    Broad = SimpleNamespace(PlacementFingerprint="broad")
    LocalEco = SimpleNamespace(PlacementFingerprint="local-eco")
    Context = SimpleNamespace(
        InterfaceCandidateQueue=[
            ("prepare-eligibility", 0, Broad, 0, 0),
            ("prepare-eligibility", 1, Current, 0, 0),
            ("prepare-eligibility", 2, LocalEco, 0, 0),
        ],
        ActiveComponentCutSignals={"A1"},
        LocalFactorDiversificationCandidateByPlacementFingerprint={
            "local-eco": LocalEco,
        },
    )
    monkeypatch.setattr(
        PlacementPhysicalAssembly,
        "BuildComponentAccessFeedbackPlacementScore",
        lambda Candidate, _Signals: (
            (0, 0, 0, 0, 0)
            if Candidate.PlacementFingerprint == "broad"
            else (9, 9, 9, 9, 9)
        ),
    )

    PlacementPhysicalAssembly.ReorderRemainingPlacementsForAccessCore(
        Context,
        "current",
    )

    assert [
        Entry[2].PlacementFingerprint
        for Entry in Context.InterfaceCandidateQueue
    ] == ["local-eco", "broad", "current"]


def test_exact_capacity_repair_does_not_compose_unrelated_topology_signals():
    Source = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement
    )

    assert (
        "InheritedOwnedFrontierTopologyRepairSignals\n"
        "        and CapacityRepairConstraint is None"
    ) in Source
    assert "Reset=PressureGuidance or CapacityRepairActive" in Source
    FlowSource = inspect.getsource(
        PlacementPhysicalFlow.RunPhysicalComponentFlow
    )
    assert "SelectedComponentClusters" in FlowSource
    assert (
        "RequiredComponentGateNames="
        "Context.CapacityRepairRequiredComponentGateNames"
    ) in FlowSource
    assert "Context.SelectedComponentClusters = ()" in FlowSource
    assert (
        "Context.FailureDiagnostics.setdefault(\n"
        "                        'SelectedComponentClusters'"
    ) in FlowSource
    assert "QueuedCapacityRepairSolveFingerprints" in FlowSource
    assert (
        "proof-guided-pending-capacity-solve-prioritized"
        in FlowSource
    )
    AssemblySource = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement
    )
    assert "proof-guided-pending-capacity-repair-preserved" in AssemblySource
    assert "IncomingCapacityRepair" in AssemblySource
    assert "FreshCapacityRepairRefinement" in AssemblySource
    assert "MergedGateNames" in AssemblySource
    assert "CapacityRepairGeometryConstraint" in AssemblySource
    assert "CapacityRepairFocusSignals" in AssemblySource
    assert "GeometryFocusProofFingerprint" in AssemblySource
    DiversificationSource = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueSingletonLocalFactorDiversification
    )
    assert "InheritedCapacityRepairConstraint" in DiversificationSource
    assert "local-factor-diversification" in DiversificationSource
    assert "CapacityRepairRefinementReady" in FlowSource
    assert "capacity-repair-refinement-prioritized" in FlowSource


def test_composed_capacity_repair_requires_the_complete_signal_domain():
    Composed = SimpleNamespace(
        RepairLevel="local-assembly",
        ProofKind="composed-complete-capacity-core",
        Signals=("CarryIn", "Generate1", "B1"),
    )
    Fresh = SimpleNamespace(
        RepairLevel="local-assembly",
        ProofKind="complete-capacity-core",
        Signals=("Generate1", "B1"),
    )

    assert PlacementPhysicalAssembly.SelectCapacityRepairGenerationSignals(
        Composed,
        ("Generate1", "B1"),
    ) == frozenset(("CarryIn", "Generate1", "B1"))
    assert PlacementPhysicalAssembly.SelectCapacityRepairGenerationSignals(
        Fresh,
        ("Generate1", "B1"),
    ) == frozenset(("Generate1", "B1"))
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        Composed,
        ("Generate1", "B1"),
    ) == frozenset(("CarryIn", "Generate1", "B1"))
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        Fresh,
        ("Generate1", "B1"),
    ) == frozenset(("Generate1", "B1"))


def test_transactional_capacity_repair_rejects_incomplete_or_broad_domains():
    UnrelatedFocus = SimpleNamespace(
        RepairLevel="local-assembly",
        ProofKind="composed-complete-capacity-core",
        Signals=("CarryIn", "Generate1", "B1"),
    )
    Broad = SimpleNamespace(
        RepairLevel="local-assembly",
        ProofKind="composed-complete-capacity-core",
        Signals=("CarryIn", "Generate1", "B1", "B2"),
    )
    Channel = SimpleNamespace(
        RepairLevel="channel-capacity",
        ProofKind="composed-complete-capacity-core",
        Signals=("CarryIn", "Generate1", "B1"),
    )

    assert not PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        UnrelatedFocus,
        ("Generate0", "B0"),
    )
    assert not PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        Broad,
        ("Generate1", "B1"),
    )
    assert not PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        Channel,
        ("Generate1", "B1"),
    )


def test_complete_three_signal_repair_composes_one_bounded_descendant():
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalVariants(
        0,
        ("B1", "CarryIn", "Generate1"),
    ) == (0, 1)
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalVariants(
        1,
        ("B1", "CarryIn", "Generate1"),
    ) == (1,)
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalVariants(
        0,
        ("CarryIn", "Generate1"),
    ) == (0,)
    assert not PlacementPhysicalAssembly.SelectCapacityRepairTransactionalVariants(
        2,
        ("B1", "CarryIn", "Generate1"),
    )


def test_complete_three_signal_repair_prefetches_only_one_internal_signal():
    Select = (
        PlacementPhysicalAssembly
        .SelectCapacityRepairCumulativeSingletonPrefetchSignal
    )

    assert Select(
        ("B1", "CarryIn", "Generate1"),
        ("B1", "CarryIn", "Sum0"),
    ) == "Generate1"
    assert not Select(
        ("B1", "Generate1", "Propagate1"),
        ("B1", "CarryIn", "Sum0"),
    )
    assert not Select(
        ("B1", "CarryIn"),
        ("B1", "CarryIn", "Sum0"),
    )
    assert not Select(
        ("A1", "B1", "CarryIn"),
        ("A1", "B1", "CarryIn", "Sum0"),
    )


def test_singleton_repair_transition_key_requires_exact_geometry_identity():
    Build = (
        PlacementPhysicalAssembly
        .BuildSingletonLocalFactorRepairTransitionKey
    )

    First = Build(
        "NandNet4",
        1,
        {
            "SelectedClusterIndices": [3],
            "InvalidatedSignals": [
                "Propagate1",
                "NandNet4",
                "NandNet5",
            ],
        },
    )

    assert First == Build(
        "NandNet4",
        1,
        {
            "SelectedClusterIndices": [3],
            "InvalidatedSignals": [
                "NandNet5",
                "NandNet4",
                "Propagate1",
            ],
        },
    )
    assert First != Build(
        "NandNet4",
        0,
        {
            "SelectedClusterIndices": [2],
            "InvalidatedSignals": [
                "A1",
                "NandNet3",
                "NandNet4",
            ],
        },
    )
    assert not Build("NandNet4", 1, {})


def test_learned_transition_replay_requires_one_advancing_signal():
    Select = (
        PlacementPhysicalAssembly
        .SelectLearnedAdvancingSingletonRepairTransition
    )
    Build = (
        PlacementPhysicalAssembly
        .BuildSingletonLocalFactorRepairTransitionKey
    )
    NandNet4 = Build(
        "NandNet4",
        1,
        {
            "SelectedClusterIndices": [3],
            "InvalidatedSignals": [
                "NandNet4",
                "NandNet5",
                "Propagate1",
            ],
        },
    )
    NandNet5 = Build(
        "NandNet5",
        1,
        {
            "SelectedClusterIndices": [3],
            "InvalidatedSignals": [
                "NandNet4",
                "NandNet5",
                "Propagate1",
            ],
        },
    )

    assert Select({
        NandNet4: "NandNet5",
        NandNet5: "NandNet5",
    }) == (NandNet4, "NandNet5")
    assert not Select({NandNet4: "<ambiguous>"})
    assert not Select({
        NandNet4: "NandNet5",
        Build(
            "Generate1",
            1,
            {
                "SelectedClusterIndices": [5],
                "InvalidatedSignals": [
                    "Generate1",
                    "NandNet19",
                    "Propagate2",
                ],
            },
        ): "NandNet4",
    })


def test_proof_closed_learned_transition_sorts_behind_fresh_sibling():
    Classify = (
        PlacementPhysicalAssembly
        .ClassifyLearnedTransitionCandidatePriority
    )
    Prefetched = frozenset(("prefetched",))
    Closed = frozenset(("closed",))

    assert tuple(sorted(
        ("closed", "fresh", "prefetched"),
        key=lambda Fingerprint: Classify(
            Fingerprint,
            Prefetched,
            Closed,
        ),
    )) == ("prefetched", "fresh", "closed")
    assert Classify("prefetched", Prefetched, Closed) == 0
    assert Classify("fresh", Prefetched, Closed) == 1
    assert Classify("closed", Prefetched, Closed) == 2


def test_learned_binary_transition_prefetches_only_alternate_sibling():
    Select = (
        PlacementPhysicalAssembly
        .SelectAlternateBinarySingletonRepairVariant
    )

    assert Select(0) == 1
    assert Select(1) == 0
    assert Select(-1) is None
    assert Select(2) is None


def test_complete_typed_access_proof_scans_exact_beam_but_incomplete_proofs_remain_binary():
    Select = (
        PlacementPhysicalAssembly
        .SelectSingletonLocalFactorRepairVariants
    )

    assert Select(0, False, 64) == (0, 1)
    assert Select(1, False, 64) == (1,)
    assert Select(2, False, 64) == ()
    assert Select(0, True, 64) == tuple(range(16))
    assert Select(14, True, 64) == (14, 15)
    assert Select(16, True, 64) == ()


def test_transactional_complete_core_expands_only_when_two_owners_are_incomplete():
    ThreeOwners = (
        (0, ("Gate0",), frozenset(("B1",))),
        (1, ("Gate1",), frozenset(("Generate1",))),
        (2, ("Gate2",), frozenset(("CarryIn",))),
    )
    TwoOwnerClosure = (
        (0, ("Gate0",), frozenset(("B1", "Generate1"))),
        (1, ("Gate1",), frozenset(("CarryIn",))),
        (2, ("Gate2",), frozenset(("B1",))),
    )
    CompleteSignals = frozenset(("B1", "Generate1", "CarryIn"))

    assert SelectTransactionalRepairClusterSelections(
        ThreeOwners,
        2,
        CompleteSignals,
    ) == ((0, 1, 2),)
    assert SelectTransactionalRepairClusterSelections(
        TwoOwnerClosure,
        2,
        CompleteSignals,
    ) == ((0, 1),)


def test_minimal_physical_port_core_builds_explicit_placement_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        AffectedNets=("UnusedBroadSignal",),
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreSignals": ["Beta", "Alpha", "Beta"],
            "PortAssignmentUnsatCoreFingerprint": "physical-core",
            "PhysicalAssemblyPlanFingerprint": "plan",
            "DomainFingerprint": "domain",
        },
    )

    Feedback = BuildPhysicalComponentPlacementFeedback(Failure)

    assert Feedback is not None
    assert Feedback.ProofFingerprint == "physical-core"
    assert Feedback.RelocationSignals == ("Alpha", "Beta")
    assert Feedback.SourcePlanFingerprint == "plan"
    assert Feedback.DomainFingerprint == "domain"


def test_complete_singleton_access_core_builds_placement_feedback():
    Feedback = BuildPhysicalComponentPlacementFeedback(RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="ComponentAccessCertification",
        AffectedNets=("NandNet",),
        Diagnostics={
            "Complete": True,
            "Feasible": False,
            "AffectedSignals": ["NandNet"],
            "CertificateFingerprint": "access-core",
            "DomainFingerprint": "access-domain",
        },
    ))

    assert Feedback is not None
    assert Feedback.ProofFingerprint == "access-core"
    assert Feedback.RelocationSignals == ("NandNet",)
    assert Feedback.DomainFingerprint == "access-domain"


def test_complete_ownership_core_is_serialized_and_drives_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalEligibilitySolveAfterUnarySupport",
        AffectedNets=("NandNet26", "CarryIn"),
        Resources=("portal-a",),
        Locations=((3, 7, 2),),
        Diagnostics={
            "OwnershipUnsatCoreFingerprint": "44136fa355b3678a",
        },
    )
    Core = BuildComponentRoutabilityCore(
        Failure,
        PlacementStateFingerprint="placement",
        ComponentStateFingerprint="component",
        DomainFingerprint="domain",
        CoreFingerprint="fallback",
        Complete=True,
    )

    assert Core is not None
    assert Core.Signals == ("CarryIn", "NandNet26")
    assert Core.BlockingResources == ("portal-a",)
    assert Core.BlockingPorts == ((3, 7, 2),)
    Feedback = BuildPhysicalComponentPlacementFeedback(replace(
        Failure,
        Diagnostics={"ComponentRoutabilityCore": Core.ToDictionary()},
    ))
    assert Feedback is not None
    assert Feedback.ProofFingerprint == "44136fa355b3678a"
    assert Feedback.RelocationSignals == ("CarryIn", "NandNet26")


def test_complete_capacity_pressure_core_overrides_broad_ownership_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        AffectedNets=("CarryIn", "NandNet26", "NandNet28", "NandNet29"),
        Diagnostics={
            "OwnershipUnsatCoreFingerprint": "44136fa355b3678a",
            "PlacementInterfacePressureSignals": [
                "NandNet29",
                "NandNet28",
            ],
        },
    )

    Core = BuildComponentRoutabilityCore(
        Failure,
        PlacementStateFingerprint="placement",
        ComponentStateFingerprint="component",
        DomainFingerprint="domain",
        CoreFingerprint="fallback",
        Complete=True,
    )

    assert Core is not None
    assert Core.CoreFingerprint != "44136fa355b3678a"
    assert Core.Signals == ("NandNet28", "NandNet29")
    Feedback = BuildPhysicalComponentPlacementFeedback(replace(
        Failure,
        Diagnostics={"ComponentRoutabilityCore": Core.ToDictionary()},
    ))
    assert Feedback is not None
    assert Feedback.RelocationSignals == ("NandNet28", "NandNet29")


def test_complete_capacity_pair_builds_disjoint_seam_constraint():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "proof",
            "PortAssignmentUnsatCoreSignals": ["Beta", "Alpha"],
            "PortAssignmentUnsatCoreClause": [
                ["Alpha", "seam-alpha"],
                ["Beta", "seam-beta"],
            ],
        },
    )

    Constraint = BuildPhysicalInterfaceRepairCore(Failure, Candidate)

    assert Constraint is not None
    assert Constraint.Signals == ("Alpha", "Beta")
    assert Constraint.ForcedSeamClasses == (
        ("Alpha", "seam-alpha"),
        ("Beta", "seam-beta"),
    )
    Preparation = SimpleNamespace(LocalAccessFactorsBySignal=(
        ("Alpha", (SimpleNamespace(
            LocalClaims=SimpleNamespace(ResourceIds=frozenset(("a",))),
            SeamContractFingerprint="new-alpha",
        ),)),
        ("Beta", (SimpleNamespace(
            LocalClaims=SimpleNamespace(ResourceIds=frozenset(("b",))),
            SeamContractFingerprint="new-beta",
        ),)),
    ))
    assert PreparedEligibilityHasDisjointCapacitySeams(
        Preparation, Constraint,
    )[0] is True
    Overlapping = SimpleNamespace(LocalAccessFactorsBySignal=(
        ("Alpha", (SimpleNamespace(
            LocalClaims=SimpleNamespace(ResourceIds=frozenset(("shared",))),
            SeamContractFingerprint="alpha",
        ),)),
        ("Beta", (SimpleNamespace(
            LocalClaims=SimpleNamespace(ResourceIds=frozenset(("shared",))),
            SeamContractFingerprint="beta",
        ),)),
    ))
    assert PreparedEligibilityHasDisjointCapacitySeams(
        Overlapping, Constraint,
    )[0] is False
    assert AuthoritativePortPreparation.SelectDisjointCapacitySeams(
        Preparation.LocalAccessFactorsBySignal,
        Constraint,
    ) == PreparedEligibilityHasDisjointCapacitySeams(
        Preparation,
        Constraint,
    )
    assert AuthoritativePortPreparation.SelectDisjointCapacitySeams(
        Overlapping.LocalAccessFactorsBySignal,
        Constraint,
    ) == PreparedEligibilityHasDisjointCapacitySeams(
        Overlapping,
        Constraint,
    )


def test_proven_capacity_repair_chain_composes_parent_and_child_cores():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(
            Placed=SimpleNamespace(
                LocalRouteClaims=(),
                PlacedGates=(
                    SimpleNamespace(
                        Name="Gate62",
                        Inputs=("Carry", "Net36"),
                        InputPins=((1, 1, 1), (1, 1, 2)),
                        Outputs=("Net38",),
                        OutputPin=(1, 1, 3),
                    ),
                    SimpleNamespace(
                        Name="Gate63",
                        Inputs=("Net37", "Net38"),
                        InputPins=((2, 1, 1), (2, 1, 2)),
                        Outputs=("Sum",),
                        OutputPin=(2, 1, 3),
                    ),
                ),
            ),
            Clusters=((), ("Gate62",), ("Gate63",)),
        ),
    )
    Parent = BuildPhysicalInterfaceRepairCore(
        RoutingFailure(
            Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            Stage="PhysicalSymbolicCapacityPlacementFeedback",
            Diagnostics={
                "SymbolicCapacityPlacementFeedback": True,
                "SymbolicCapacityProofComplete": True,
                "SymbolicCapacityProofFingerprint": "parent-proof",
                "PlacementInterfacePressureSignals": ["Carry", "Net36"],
                "LocalCapacityCoreClause": [
                    ["Carry", "carry-seam"],
                    ["Net36", "net36-seam"],
                ],
                "SelectedComponentClusters": [1, 2],
            },
        ),
        Candidate,
    )
    Child = BuildPhysicalInterfaceRepairCore(
        RoutingFailure(
            Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            Stage="PhysicalComponentAssemblyPlanning",
            Diagnostics={
                "PortAssignmentProofComplete": True,
                "PortAssignmentUnsatCoreMinimal": True,
                "PortAssignmentUnsatCoreFingerprint": "child-proof",
                "PortAssignmentUnsatCoreSignals": ["Net36", "Net37"],
                "SelectedComponentClusters": [1, 2],
            },
        ),
        Candidate,
    )
    assert Parent is not None
    assert Child is not None

    First = ComposePhysicalInterfaceRepairCores(Parent, Child, Candidate)
    Second = ComposePhysicalInterfaceRepairCores(Child, Parent, Candidate)

    assert First == Second
    assert First.Signals == ("Carry", "Net36", "Net37")
    assert First.ClusterIds == (1, 2)
    assert First.ComponentGateNames == ("Gate62", "Gate63")
    assert First.ProofKind == "composed-complete-capacity-core"
    ClosurePlacement = SimpleNamespace(
        Clusters=(("Producer",), ("Other",), ("Gate62", "Gate63")),
        ClusterBoundaryLeaseRequests=(
            SimpleNamespace(
                Signal="Carry",
                SourceCluster=0,
                TargetCluster=2,
            ),
            SimpleNamespace(
                Signal="Net36",
                SourceCluster=1,
                TargetCluster=2,
            ),
        ),
        Placed=SimpleNamespace(ClusterBoundaryLeaseRequests=()),
    )
    assert BuildCapacityRepairEndpointClosureClusters(
        ClosurePlacement,
        First,
    ) == (0, 1, 2)
    assert BuildCapacityRepairEndpointClosureClusters(
        ClosurePlacement,
        Parent,
    ) == ()


def test_complete_capacity_core_uses_lexicographic_multi_signal_matching():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "triple-proof",
            "PortAssignmentUnsatCoreSignals": ["Gamma", "Alpha", "Beta"],
            "PortAssignmentUnsatCoreClause": [
                ["Alpha", "seam-alpha"],
                ["Beta", "seam-beta"],
                ["Gamma", "seam-gamma"],
            ],
        },
    )
    Constraint = BuildPhysicalInterfaceRepairCore(Failure, Candidate)

    assert Constraint is not None
    assert Constraint.Signals == ("Alpha", "Beta", "Gamma")
    Preparation = SimpleNamespace(LocalAccessFactorsBySignal=(
        ("Alpha", (SimpleNamespace(
            LocalClaims=SimpleNamespace(ResourceIds=frozenset(("a",))),
            SeamContractFingerprint="alpha",
        ),)),
        ("Beta", (SimpleNamespace(
            LocalClaims=SimpleNamespace(ResourceIds=frozenset(("b",))),
            SeamContractFingerprint="beta",
        ),)),
        ("Gamma", (SimpleNamespace(
            LocalClaims=SimpleNamespace(ResourceIds=frozenset(("c",))),
            SeamContractFingerprint="gamma",
        ),)),
    ))
    First = PreparedEligibilityHasDisjointCapacitySeams(Preparation, Constraint)
    Second = PreparedEligibilityHasDisjointCapacitySeams(Preparation, Constraint)

    assert First == Second
    assert First[0] is True
    assert First[2] == (
        ("Alpha", "alpha"),
        ("Beta", "beta"),
        ("Gamma", "gamma"),
    )


def test_complete_capacity_core_rejects_higher_order_claim_conflict():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "triple-conflict",
            "PortAssignmentUnsatCoreSignals": ["Alpha", "Beta", "Gamma"],
            "PortAssignmentUnsatCoreClause": [
                ["Alpha", "a"], ["Beta", "b"], ["Gamma", "c"],
            ],
        },
    )
    Constraint = BuildPhysicalInterfaceRepairCore(Failure, Candidate)
    assert Constraint is not None
    Preparation = SimpleNamespace(LocalAccessFactorsBySignal=tuple(
        (Signal, (SimpleNamespace(
            LocalClaims=SimpleNamespace(ResourceIds=frozenset(("shared",))),
            SeamContractFingerprint=Signal,
        ),))
        for Signal in Constraint.Signals
    ))

    assert PreparedEligibilityHasDisjointCapacitySeams(
        Preparation, Constraint,
    ) == (
        False,
        "",
        (),
        (
            ("Alpha", ("Alpha",)),
            ("Beta", ("Beta",)),
            ("Gamma", ("Gamma",)),
        ),
    )


def test_complete_single_signal_capacity_core_does_not_admit_geometry_repair():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "single-proof",
            "PortAssignmentUnsatCoreSignals": ["Alpha"],
            "PortAssignmentUnsatCoreClause": [["Alpha", "seam-alpha"]],
        },
    )
    Constraint = BuildPhysicalInterfaceRepairCore(Failure, Candidate)
    assert Constraint is None


def test_complete_singleton_assembly_core_admits_local_factor_diversification():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "singleton-proof",
            "PortAssignmentUnsatCoreSignals": ["Alpha"],
            "DomainFingerprint": "factor-domain",
        },
    )

    First = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)
    Second = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)

    assert First == Second
    assert First is not None
    assert First.Signal == "Alpha"
    assert First.SourceProofFingerprint == "singleton-proof"


def test_incomplete_singleton_assembly_core_cannot_diversify_local_factor():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "singleton-proof",
            "PortAssignmentUnsatCoreSignals": ["Alpha"],
            "DomainFingerprint": "factor-domain",
        },
    )

    assert BuildPhysicalLocalFactorDiversificationCore(
        Failure,
        Candidate,
    ) is None


def test_complete_singleton_typed_seam_failure_admits_endpoint_diversification():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPerimeterSeamUnsatisfiable,
        Stage="ComponentAccessCertification",
        AffectedNets=("Alpha",),
        Diagnostics={
            "AffectedSignals": ["Alpha"],
            "CertificateFingerprint": "access-proof",
            "Complete": True,
            "StructuralFingerprint": "access-domain",
        },
    )

    Core = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)

    assert Core is not None
    assert Core.Signal == "Alpha"
    assert Core.SourceProofFingerprint == "access-proof"
    assert Core.LocalFactorIdentityFingerprint


def test_complete_singleton_symbolic_capacity_proof_admits_local_factor_diversification():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage='PhysicalSymbolicCapacityPlacementFeedback',
        AffectedNets=('NandNet4',),
        Diagnostics={
            'SymbolicCapacityPlacementFeedback': True,
            'SymbolicCapacityProofComplete': True,
            'SymbolicCapacityProofFingerprint': 'symbolic-singleton-proof',
            'PlacementInterfacePressureSignals': ['NandNet4'],
            'LocalCapacityCoreClause': [[
                'NandNet4',
                'nand-net-4-seam',
            ]],
        },
    )

    Core = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)

    assert Core is not None
    assert Core.Signal == 'NandNet4'
    assert Core.SourceProofFingerprint == 'symbolic-singleton-proof'
    assert Core.LocalFactorIdentityFingerprint


def test_complete_singleton_physical_eligibility_empty_bank_admits_diversification():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentEligibility",
        AffectedNets=("B1",),
        Diagnostics={
            "Complete": True,
            "Feasible": False,
            "ComponentFabricConstructionComplete": True,
            "OwnershipSearchComplete": True,
            "ImplicitForeignTransitDomainCount": 0,
            "PriorityPreparationSignals": ["B1", "CarryIn"],
            "DomainDiagnosticsBySignal": {
                "B1": {
                    "Reason": (
                        "complete-certified-domain-empty-after-physical-projection"
                    ),
                    "RequiredPortLayer": 2,
                    "CertifiedGuideDisconnectedCount": 204,
                },
                "CarryIn": {
                    "Reason": "available-certified",
                    "RequiredPortLayer": 1,
                },
            },
        },
    )

    First = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)
    Second = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)

    assert First == Second
    assert First is not None
    assert First.Signal == "B1"
    assert First.SourceProofFingerprint
    assert First.LocalFactorIdentityFingerprint


def test_complete_physical_eligibility_recovers_exact_repair_terminals():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage='PhysicalComponentEligibility',
        AffectedNets=('B1',),
        Diagnostics={
            'Complete': True,
            'Feasible': False,
            'ComponentFabricConstructionComplete': True,
            'OwnershipSearchComplete': True,
            'DomainDiagnosticsBySignal': {
                'B1': {
                    'Reason': (
                        'complete-certified-domain-empty-after-physical-'
                        'projection'
                    ),
                    'CandidateCountByTerminal': [
                        {'Terminal': [11, 1, 37], 'CandidateCount': 1},
                        {'Terminal': [18, 1, 39], 'CandidateCount': 1},
                    ],
                },
            },
        },
    )

    assert (
        PlacementPhysicalAssembly
        .SelectCompletePhysicalEligibilityRepairTerminalPositions(
            Failure,
            'B1',
        )
        == frozenset(((11, 1, 37), (18, 1, 39)))
    )
    assert not (
        PlacementPhysicalAssembly
        .SelectCompletePhysicalEligibilityRepairTerminalPositions(
            Failure,
            'CarryIn',
        )
    )

    ChannelizedPlacement = SimpleNamespace(Placed=SimpleNamespace(
        PlacedGates=(
            SimpleNamespace(
                Name='B1Owner',
                Outputs=('B1',),
                OutputPin=(11, 1, 37),
                Inputs=('NandNet4',),
                InputPins=((9, 1, 37),),
            ),
            SimpleNamespace(
                Name='B1Consumer',
                Outputs=('NandNet8',),
                OutputPin=(20, 1, 39),
                Inputs=('B1',),
                InputPins=((18, 1, 39),),
            ),
        ),
    ))
    assert (
        PlacementPhysicalAssembly
        .SelectCompletePhysicalEligibilityRepairEndpointGateNames(
            Failure,
            'B1',
            ChannelizedPlacement,
        )
        == frozenset(('B1Owner', 'B1Consumer'))
    )


def test_complete_physical_repair_keeps_pending_local_sibling_first():
    LocalFirst = SimpleNamespace(PlacementFingerprint='local-first')
    UnrelatedLocal = SimpleNamespace(PlacementFingerprint='unrelated-local')
    Fallback = SimpleNamespace(PlacementFingerprint='fallback')
    Queue = [
        ('prepare-eligibility', 1, LocalFirst, 0, 0),
        ('prepare-eligibility', 2, UnrelatedLocal, 0, 0),
        ('prepare-eligibility', 3, Fallback, 0, 0),
    ]

    assert (
        PlacementPhysicalAssembly
        .SelectLocalFactorCandidateQueueInsertionIndex(
            Queue,
            {
                'local-first': 'B1',
                'unrelated-local': 'NandNet4',
            },
            'B1',
        )
        == 1
    )
    assert (
        PlacementPhysicalAssembly
        .SelectLocalFactorCandidateQueueInsertionIndex(
            Queue,
            {},
            'B1',
        )
        == 0
    )


def test_cycle_repair_keeps_only_immediate_sibling_group_first():
    Select = (
        PlacementPhysicalAssembly
        .SelectLocalFactorCycleSiblingQueueInsertionIndex
    )
    FirstSibling = SimpleNamespace(PlacementFingerprint='first-sibling')
    StaleSibling = SimpleNamespace(PlacementFingerprint='stale-sibling')
    Fallback = SimpleNamespace(PlacementFingerprint='fallback')
    Queue = [
        ('prepare-eligibility', 1, FirstSibling, 0, 0),
        ('prepare-eligibility', 2, StaleSibling, 0, 0),
        ('prepare-eligibility', 3, Fallback, 0, 0),
    ]
    Groups = {
        'first-sibling': 'current-group',
        'stale-sibling': 'stale-group',
    }

    assert Select(Queue, Groups, 'current-group') == 1
    assert Select(Queue, Groups, 'stale-group') == 0
    assert Select(Queue, Groups, '') == 0

    PersistentSibling = SimpleNamespace(
        PlacementFingerprint='persistent-sibling',
        JointPortfolioIdentityFingerprint='portfolio-1',
    )
    assert Select(
        [('prepare-eligibility', 1, PersistentSibling, 0, 0)],
        {},
        '',
        'portfolio-1',
    ) == 1


def test_cycle_repair_promotes_exact_sibling_after_access_core_rescore():
    TargetSibling = SimpleNamespace(
        PlacementFingerprint='target-sibling',
        JointPortfolioIdentityFingerprint='target-portfolio',
    )
    StaleSibling = SimpleNamespace(
        PlacementFingerprint='stale-sibling',
        JointPortfolioIdentityFingerprint='stale-portfolio',
    )
    Fallback = SimpleNamespace(
        PlacementFingerprint='fallback',
        JointPortfolioIdentityFingerprint='',
    )
    Queue = [
        ('prepare-eligibility', 1, StaleSibling, 0, 0),
        ('prepare-eligibility', 2, Fallback, 0, 0),
        ('prepare-eligibility', 3, TargetSibling, 0, 0),
    ]

    Count = PlacementPhysicalAssembly.PrioritizeLocalFactorCycleSiblings(
        Queue,
        {'target-sibling': 'target-group'},
        'target-group',
        'target-portfolio',
    )

    assert Count == 1
    assert [Entry[2].PlacementFingerprint for Entry in Queue] == [
        'target-sibling',
        'stale-sibling',
        'fallback',
    ]


def test_local_factor_lineage_falls_back_to_persistent_repair_recipe():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(
            Placed=SimpleNamespace(LocalRouteDiagnostics={
                '__PlacementRecipe__': {
                    'TransactionalRepairSignalHistory': [
                        ['B1', 'Generate1'],
                        ['Generate1'],
                        ['NandNet37'],
                    ],
                },
            }),
        ),
    )
    Select = PlacementPhysicalAssembly.SelectLocalFactorRepairSignalLineage

    assert Select(Candidate, ()) == ('Generate1', 'NandNet37')
    assert Select(
        Candidate,
        ('Generate1', 'Generate1', 'NandNet37'),
    ) == ('Generate1', 'Generate1', 'NandNet37')


def test_transactional_repair_footprint_measures_exact_changed_clusters():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(
            Placed=SimpleNamespace(LocalRouteDiagnostics={
                '__TransactionalClusterEndpointRepair__': {
                    'Clusters': {
                        '3': {'FinalWidth': 11, 'FinalDepth': 16},
                        '12': {'FinalWidth': 3, 'FinalDepth': 4},
                    },
                },
            }),
        ),
    )

    assert (
        PlacementPhysicalAssembly
        .MeasureTransactionalRepairClusterFootprint(Candidate)
        == (188, 2)
    )


def test_least_footprint_repair_is_bounded_to_transition_and_first_repeat():
    Select = (
        PlacementPhysicalAssembly.ShouldPreferLeastFootprintLocalRepair
    )

    assert not Select(False, ('Generate1',), 'NandNet37')
    assert not Select(True, (), 'NandNet37')
    assert not Select(True, ('Generate1',), 'Generate1')
    assert Select(True, ('Generate1',), 'NandNet37')
    assert Select(True, ('Generate1', 'NandNet37'), 'NandNet37')
    assert not Select(
        True,
        ('Generate1', 'NandNet37', 'NandNet37'),
        'NandNet37',
    )


def test_boundary_pair_core_candidates_probe_adjacent_scarcity_domains():
    Select = (
        PhysicalPortSearch.SelectAdjacentScarcityBoundaryPairCoreCandidates
    )

    assert Select(
        ('A1', 'B1', 'Generate1', 'NandNet4'),
        {'A1': 3, 'B1': 20, 'Generate1': 21, 'NandNet4': 21},
        2,
    ) == (
        ('Generate1', 'NandNet4'),
        ('B1', 'Generate1'),
    )
    assert Select(('A1',), {}, 8) == ()
    assert Select(('A1', 'B1'), {}, 0) == ()


def test_prior_port_assignment_core_hint_requires_small_live_signal_subset():
    Select = PhysicalPortSearch.SelectRevalidatablePriorPortAssignmentCore

    assert Select(
        ('Generate1', 'B1'),
        ('A1', 'B1', 'Generate1'),
    ) == ('B1', 'Generate1')
    assert Select(('Missing', 'B1'), ('A1', 'B1')) == ()
    assert Select(('A1', 'B1', 'C1', 'D1'), ('A1', 'B1', 'C1', 'D1')) == ()




def test_local_factor_back_edge_requires_return_to_earlier_distinct_signal():
    IsBackEdge = PlacementPhysicalAssembly.IsLocalFactorRepairBackEdge

    assert not IsBackEdge((), 'Generate1')
    assert not IsBackEdge(('Generate1',), 'Generate1')
    assert not IsBackEdge(('Generate1', 'Generate1'), 'Generate1')
    assert not IsBackEdge(('Generate1', 'Generate1'), 'NandNet37')
    assert IsBackEdge(
        ('Generate1', 'Generate1', 'NandNet37'),
        'Generate1',
    )


def test_complete_physical_empty_bank_prefers_access_distinct_sibling():
    Priorities = {
        Fingerprint: (
            PlacementPhysicalAssembly
            .ClassifyCompletePhysicalEligibilityCandidatePriority(
                Fingerprint,
                {
                    'default': (
                        'singleton-local-factor-repair-transition-v1',
                        'B1',
                        0,
                        (2,),
                        ('B1',),
                    ),
                    'distinct': (
                        'singleton-local-factor-repair-transition-v1',
                        'B1',
                        3,
                        (2,),
                        ('B1',),
                    ),
                },
            )
        )
        for Fingerprint in ('default', 'distinct')
    }

    assert Priorities['distinct'] < Priorities['default']


def test_transactional_repair_queue_preserves_external_terminals_first():
    def Candidate(Fingerprint, InvalidatedSignals):
        return SimpleNamespace(
            PlacementFingerprint=Fingerprint,
            Placement=SimpleNamespace(
                Placed=SimpleNamespace(
                    LocalRouteDiagnostics={
                        '__TransactionalClusterEndpointRepair__': {
                            'InvalidatedSignals': InvalidatedSignals,
                        },
                    },
                ),
            ),
        )

    ExternalRepair = Candidate('external', ['A1', 'NandNet4'])
    InternalRepair = Candidate('internal', ['NandNet4', 'NandNet5'])

    Ordered = sorted(
        (ExternalRepair, InternalRepair),
        key=lambda Value: (
            PlacementPhysicalAssembly.BuildTransactionalRepairRoutingPriority(
                Value,
                ('A1', 'B1'),
            )
        ),
    )

    assert [Value.PlacementFingerprint for Value in Ordered] == [
        'internal',
        'external',
    ]


def test_complete_contract_independent_owned_frontier_lifts_topology_core():
    Producer = SimpleNamespace(
        Name="Producer",
        Outputs=("Alpha",),
        Inputs=(),
        OutputPin=(0, 1, 0),
        InputPins=(),
    )
    Consumer = SimpleNamespace(
        Name="Consumer",
        Outputs=(),
        Inputs=("Alpha",),
        OutputPin=None,
        InputPins=((4, 1, 0),),
    )
    Candidate = SimpleNamespace(
        InterfaceTopologyFingerprint="source-topology",
        Placement=SimpleNamespace(
            Clusters=(("Producer", "Consumer"),),
            Placed=SimpleNamespace(PlacedGates=(Producer, Consumer)),
        ),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentLocalEligibility",
        Diagnostics={
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreKind": "tree-frontier-empty-owned-signal-domain",
            "LocalUnsatCoreFingerprint": "owned-frontier-proof",
            "LocalUnsatCoreSignals": ["Alpha"],
            "SignalDiagnostics": {
                "Alpha": {
                    "Complete": True,
                    "OwnedSignalDomainContractIndependent": True,
                },
            },
        },
    )

    First = BuildPhysicalOwnedFrontierTopologyRepairCore(Failure, Candidate)
    Second = BuildPhysicalOwnedFrontierTopologyRepairCore(Failure, Candidate)

    assert First == Second
    assert First is not None
    assert First.Signals == ("Alpha",)
    assert First.ProducerGateNames == ("Producer",)
    assert First.ConsumerGateNames == ("Consumer",)
    assert First.ClusterIds == (0,)


def test_port_dependent_or_incomplete_owned_frontier_cannot_lift_topology_core():
    Candidate = SimpleNamespace(
        InterfaceTopologyFingerprint="source-topology",
        Placement=SimpleNamespace(
            Clusters=(("Producer", "Consumer"),),
            Placed=SimpleNamespace(PlacedGates=()),
        ),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentLocalEligibility",
        Diagnostics={
            "LocalUnsatCoreComplete": False,
            "LocalUnsatCoreKind": "tree-frontier-empty-owned-signal-domain",
            "LocalUnsatCoreFingerprint": "incomplete",
            "LocalUnsatCoreSignals": ["Alpha"],
            "SignalDiagnostics": {"Alpha": {"Complete": False}},
        },
    )

    assert BuildPhysicalOwnedFrontierTopologyRepairCore(
        Failure,
        Candidate,
    ) is None


def test_complete_channel_capacity_core_lifts_deterministically():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
        Stage="PhysicalComponentGlobalChannelUnsatisfiable",
        AffectedNets=("Gamma", "Alpha", "Beta"),
        Diagnostics={
            "GlobalPlanDomainComplete": True,
            "CompleteAssignmentCutProof": True,
            "GlobalPlanDependencyFingerprint": "channel-proof",
        },
    )

    First = BuildPhysicalInterfaceRepairCore(Failure, Candidate)
    Second = BuildPhysicalInterfaceRepairCore(Failure, Candidate)

    assert First == Second
    assert First is not None
    assert First.RepairLevel == "channel-capacity"
    assert First.ProofKind == "complete-channel-capacity-core"
    assert First.Signals == ("Alpha", "Beta", "Gamma")


def test_complete_feedthrough_endpoint_domain_lifts_channel_repair_core():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        AffectedNets=("Transit",),
        Diagnostics={
            "FeedthroughCandidateDomainComplete": True,
            "ComponentFabricConstructionComplete": True,
            "OwnershipSearchComplete": True,
            "FeedthroughEndpointPrescreenComplete": True,
            "FeedthroughEndpointDomainFingerprint": "feedthrough-proof",
        },
    )

    Core = BuildPhysicalInterfaceRepairCore(Failure, Candidate)

    assert Core is not None
    assert Core.RepairLevel == "channel-capacity"
    assert Core.ProofKind == "complete-feedthrough-endpoint-domain"
    assert Core.Signals == ("Transit",)


def test_component_factor_preparation_defers_dense_boundary_lease():
    FlowSource = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    PrepareCall = FlowSource.index(
        "PreparedEligibility = PreparePhysicalComponentEligibility("
    )
    PrepareSource = FlowSource[PrepareCall:PrepareCall + 1200]
    PlannerSource = inspect.getsource(
        AuthoritativePortalPreparation.RunPortalPreparation
    )
    PcbSource = inspect.getsource(Pcb.PreparePhysicalComponentEligibility)

    assert (
        "DeferClusterBoundaryLeaseUntilCapacityPrecheck=True"
        in PrepareSource
    )
    assert "not State.DeferClusterBoundaryLeaseUntilCapacityPrecheck" in (
        PlannerSource
    )
    assert "deferred-for-capacity-repair-precheck" in PlannerSource
    DeferredLease = PlannerSource.index(
        "'deferred-for-capacity-repair-precheck'"
    )
    assert "State.PortalReservations = ()" in PlannerSource[
        DeferredLease:DeferredLease + 500
    ]
    assert "DeferClusterBoundaryLeaseUntilCapacityPrecheck=(" in PcbSource


def test_dense_global_handoff_owns_one_repeater_ready_retry():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    GlobalFailure = Source.index(
        "except RoutingStageError as GlobalError:"
    )
    SelectProof = Source.index(
        "SelectExhaustedRepeaterAccessCutSignals(",
        GlobalFailure,
    )
    ApplyRepair = Source.index(
        "ApplyCoordinatedCandidateDiversificationProfile(",
        SelectProof,
    )
    Retry = Source.index(
        "'repeater-ready-global-route-retry'",
        ApplyRepair,
    )
    RecordNoGood = Source.index(
        "RecordPhysicalComponentDetailedRoutingNoGood(",
        Retry,
    )

    assert GlobalFailure < SelectProof < ApplyRepair < Retry < RecordNoGood
    assert "EnableRepeaterReadyPortalRepair=True" in Source[
        ApplyRepair:Retry
    ]
    assert "'ReusedPhysicalAssemblyPlan': True" in Source[
        Retry:RecordNoGood
    ]


def test_dense_eligibility_requeues_one_repeater_ready_identity():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    OuterFailure = Source.index(
        "except RoutingStageError as Error:",
        Source.index("while Context.InterfaceCandidateQueue"),
    )
    SelectProof = Source.index(
        "SelectExhaustedRepeaterAccessCutSignals(Error.Failure)",
        OuterFailure,
    )
    ApplyRepair = Source.index(
        "ApplyCoordinatedCandidateDiversificationProfile(",
        SelectProof,
    )
    Requeue = Source.index(
        "Context.InterfaceCandidateQueue.insert(0, (",
        ApplyRepair,
    )
    CapacityRepair = Source.index(
        "if Context.CapacityRepairConstraint is not None:",
        Requeue,
    )

    assert OuterFailure < SelectProof < ApplyRepair < Requeue < CapacityRepair
    assert "Context.InterfaceWorkPhase == 'prepare-eligibility'" in Source[
        SelectProof:ApplyRepair
    ]
    assert "EnableRepeaterReadyPortalRepair=True" in Source[
        ApplyRepair:Requeue
    ]


def test_dense_materialization_work_check_captures_context_candidate():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Materialize = Source.index(
        "MaterializeSelectedJointPlacementLocalRouting("
    )
    Preparation = Source.index(
        "PreparePhysicalComponentEligibility(",
        Materialize,
    )

    assert (
        "Candidate=Context.InterfaceCandidate"
        in Source[Materialize:Preparation]
    )
    assert "Candidate=InterfaceCandidate" not in Source




def test_complete_capacity_pair_evidence_survives_feedback_escalation():
    Evidence = BuildSymbolicCapacityRepairEvidence(
        {
            "SymbolicCapacityProofFingerprint": "complete-pair-proof",
            "LocalCapacityCoreClause": [
                ["Beta", "seam-beta"],
                ["Alpha", "seam-alpha"],
            ],
        },
        ("Beta", "Alpha"),
    )
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(
            Placed=SimpleNamespace(
                LocalRouteClaims=(),
                PlacedGates=(
                    SimpleNamespace(
                        Name="GateA",
                        Inputs=("Alpha",),
                        InputPins=((1, 1, 1),),
                        Outputs=("OtherA",),
                        OutputPin=(1, 1, 2),
                    ),
                    SimpleNamespace(
                        Name="GateB",
                        Inputs=("Beta",),
                        InputPins=((2, 1, 1),),
                        Outputs=("OtherB",),
                        OutputPin=(2, 1, 2),
                    ),
                ),
            ),
            Clusters=(("Outside",), (), ("GateA",), (), ("GateB",)),
        ),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalSymbolicCapacityPlacementFeedback",
        Diagnostics={
            "SymbolicCapacityPlacementFeedback": True,
            "PlacementInterfacePressureSignals": ["Alpha", "Beta"],
            "SelectedComponentClusters": [4, 2],
            **Evidence,
        },
    )

    Constraint = BuildPhysicalInterfaceRepairCore(Failure, Candidate)

    assert Evidence == {
        "SymbolicCapacityProofComplete": True,
        "SymbolicCapacityProofFingerprint": "complete-pair-proof",
        "LocalCapacityCoreClause": [
            ["Alpha", "seam-alpha"],
            ["Beta", "seam-beta"],
        ],
    }
    assert Constraint is not None
    assert Constraint.RepairLevel == "local-assembly"
    assert Constraint.ProofKind == "complete-symbolic-capacity-core"
    assert Constraint.Signals == ("Alpha", "Beta")
    assert Constraint.ClusterIds == (2, 4)
    assert Constraint.ComponentGateNames == ("GateA", "GateB")
    assert Constraint.ForcedSeamClasses == (
        ("Alpha", "seam-alpha"),
        ("Beta", "seam-beta"),
    )
    assert BuildSymbolicCapacityRepairEvidence(
        {"SymbolicCapacityProofFingerprint": "incomplete"},
        ("Alpha", "Beta"),
    ) == {}


def test_capacity_repair_geometry_includes_pair_pin_positions():
    def CandidateAt(X):
        Gate = SimpleNamespace(
            Name="Producer",
            Outputs=("Alpha",),
            OutputPin=(X, 7, 0),
            Inputs=("Beta",),
            InputPins=((X, 7, 1),),
        )
        return SimpleNamespace(
            Placement=SimpleNamespace(Placed=SimpleNamespace(
                LocalRouteClaims=(),
                PlacedGates=(Gate,),
            )),
        )

    First = BuildCapacityRepairGeometryFingerprint(
        CandidateAt(1), ("Alpha", "Beta"),
    )
    Second = BuildCapacityRepairGeometryFingerprint(
        CandidateAt(2), ("Alpha", "Beta"),
    )

    assert First != Second


def test_proof_guided_placement_rejects_unchanged_access_geometry():
    Source = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement
    )

    assert "SourceRelocationGeometryFingerprint" in Source
    assert "CandidateRelocationGeometryFingerprint" in Source
    assert (
        "'proof-guided-access-geometry-equivalent-rejected'"
        in Source
    )
    assert "ExistingPlacementFingerprints.add(" in Source


def test_complete_component_core_uses_immediate_relocation_generator():
    Source = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement
    )

    assert "CompleteRoutabilityFeedback" in Source
    assert "ExactRoutabilityCoreSignals = RelocationSignals" in Source
    assert "SelectTopologyEquivalentRepairSignals(" not in Source
    assert "ComposedPriorProofSignals" in Source
    assert "CapacityRepairActive or ImmediatePhysicalGeometryFeedback" in Source
    assert "SourceGenerator='row-beam-conflict-relocation'" in Source
    assert "Context.AccessRepairInterfacePlanningDeadline.ExpiresAt" in Source
    assert "PlacementGenerationProofCore" in Source
    assert (
        "ProofGuidedPlacementGenerationCountByCore.get("
        "PlacementGenerationProofCore"
    ) in Source
    assert "PlacementGenerationProofCoreSignals" in Source
    assert "InheritedCapacityRepairConstraint is not None" in Source
    assert "InheritedCapacityRepairRefinement" in Source


def test_physical_proof_core_drives_focused_joint_refinement():
    Source = inspect.getsource(PlacementAttempts._TryPlacement)
    Replay = inspect.getsource(
        PlacementRoutingAttempts.MaterializeSelectedJointPlacementLocalRouting
    )

    assert "PhysicalProofCoreFocusedPlacement = bool(" in Source
    assert "FixedPhysicalProofCoreSignals" in Source
    assert (
        "CutDrivenClusterRefinementSignals="
        "CutDrivenClusterRefinementSignals"
    ) in Source
    assert "FocusedCutEpochPlacement=FocusedCutEpochPlacement" in Source
    assert "State.PhysicalProofCoreSignals" in Replay
    assert (
        "CutDrivenClusterRefinementSignals="
        "CutDrivenClusterRefinementSignals"
    ) in Replay
    assert "FocusedCutEpochPlacement=FocusedCutEpochPlacement" in Replay


def test_derived_signal_local_factor_domains_are_not_eagerly_published():
    Source = inspect.getsource(
        AuthoritativePortPreparation.CachePhysicalPortLocalFactors
    )

    assert "PreparePhysicalSignalLocalFactorDomain(" not in Source
    assert "Context.LocalFactorDomainsBySignal = {}" in Source
    assert "Context.LocalAccessFactorsBySignal" in Source
    assert "Context.FactorDomainFingerprint" in Source

    FlowSource = inspect.getsource(
        PlacementPhysicalFlow.RunPhysicalComponentFlow
    )
    assert "Context.MaximumProofGuidedGeneratedPlacements = 4" in FlowSource


def test_channelized_duplicate_reuses_exhaustive_physical_state_proof():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Channelized = Source.index("Context.ChannelizedPlacementFingerprint =")
    Proof = Source.index("Context.ChannelizedEquivalentProof = next(", Channelized)
    Reuse = Source.index("'duplicate-channelized-state-proof-reused'", Proof)
    Resources = Source.index(
        "ReuseRetainedPlacementRoutingResources(",
        Reuse,
    )

    Selection = Source[Proof:Reuse]
    assert "Proof.PlacementStateFingerprint" in Selection
    assert "Proof.ComponentSelectionFingerprint" in Selection
    assert "Proof.ComponentVariant" in Selection
    assert "Proof.Exhaustive" in Selection
    assert "ProofGuidedGenerationSourceByPlacementFingerprint.pop(" in Selection
    assert "EnqueueProofGuidedPhysicalPlacement(" in Selection
    assert "'PlacementAdvanced':" in Selection
    assert Channelized < Proof < Reuse < Resources


def test_generated_channel_architecture_failure_advances_same_repair_core():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Attempt = Source.index("Context.InterfaceAttemptDiagnostics.append({")
    Advance = Source.index(
        "'generated-channel-architecture-rejected-advance-repair'",
        Attempt,
    )
    Selection = Source[Attempt:Advance]

    assert "InterClusterRoutingChannelMaterialization" in Selection
    assert "ProofGuidedGenerationSourceByPlacementFingerprint.pop(" in Selection
    assert "EnqueueProofGuidedPhysicalPlacement(" in Selection
    assert "GeneratedArchitecturePlacementAdvanced" in Selection


def test_complete_capacity_feedback_advances_queued_repair_placement():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    FeedbackStart = Source.index(
        "CompleteSymbolicCapacityPlacementFeedback = bool("
    )
    FeedbackEnd = Source.index(
        "LatestInterfaceProofByComponentState",
        FeedbackStart,
    )
    Feedback = Source[FeedbackStart:FeedbackEnd]

    assert "EnqueueOwnedFrontierTopologyRepair(" in Feedback
    assert "or EnqueueProofGuidedPhysicalPlacement(" in Feedback
    assert "or Context.CompleteSymbolicCapacityPlacementFeedback" in Feedback
    assert "and Context.PlacementAdvanced" in Feedback
    assert "not Context.InterfaceDeadline.IsExpired()" in Feedback
    assert "not Context.AccessRepairInterfacePlanningDeadline.IsExpired()" in Feedback
    assert "GlobalHandoffPlacementAdvanced" in Source
    assert "'SymbolicCapacityPlacementFeedback'" in Source
    assert "Context.PendingCapacityRepairReady" in Source
    assert "'pending-capacity-repair-prioritized'" in Source

    DeferredRequestSource = _Sources(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement,
        PlacementAttempts._TryPlacement,
        PlacementAttempts._TakeNextDeferredRequest,
    )
    assert "AllowCapacityPairRepair=CapacityRepairActive" in DeferredRequestSource
    assert "CapacityRepairActive" in DeferredRequestSource
    assert "AllowCapacityPairRepair: bool=False" in DeferredRequestSource
    assert "not AllowCapacityPairRepair" in DeferredRequestSource
    assert "AllowCapacityPairRepair or" in DeferredRequestSource
    assert "PlacementGenerationNotAfter=Context.AccessRepairInterfacePlanningDeadline.ExpiresAt" in DeferredRequestSource
    assert "if CapacityRepairActive or ImmediatePhysicalGeometryFeedback" in (
        DeferredRequestSource
    )
    assert "SourceGenerator='row-beam-conflict-relocation'" in (
        DeferredRequestSource
    )


def test_complete_access_proof_prunes_only_stale_portfolio_siblings():
    Source = inspect.getsource(PlacementAttempts._TryPlacement)

    Promotion = Source.index(
        "PortfolioEvaluation.ShouldPromote and IdentityStillCurrent"
    )
    Prune = Source.index(
        "pruned-stale-siblings-after-access-proof-promotion",
        Promotion,
    )
    ConstraintIdentity = Source.index(
        "BuildPendingJointPlacementPortfolioIdentity(State)",
        Promotion,
    )

    assert Promotion < ConstraintIdentity < Prune
    assert "CompleteEvidenceCandidateIndices" in Source[Promotion:Prune + 600]


def test_owned_frontier_topology_repair_regenerates_before_eligibility():
    Repair = inspect.getsource(
        PlacementPhysicalAssembly.EnqueueOwnedFrontierTopologyRepair
    )
    Source = _Sources(
        PlacementPhysicalAssembly.EnqueueOwnedFrontierTopologyRepair,
        PlacementPhysicalFlow.RunPhysicalComponentFlow,
    )

    assert "PlacePcbGraph(" in Repair
    assert "CutDrivenClusterRefinementSignals=RepairSignals" in Repair
    assert "TopologyRepairPackingPolicy = replace(" in Repair
    assert "RetainedJointPlacementCandidates=1" in Repair
    assert "PackingPolicy=TopologyRepairPackingPolicy" in Repair
    assert "SelectTopologyEquivalentRepairSignals(" in Repair
    assert "BuildOwnedFrontierTopologyRepairDomainFingerprint(" in Repair
    assert "AttemptCountByDomainFingerprint" in Repair
    assert "AttemptCountByProofFingerprint" not in Repair
    assert "BuildRoutingResources(" not in Repair
    assert "'RoutingResourceConstructionDeferred': True" in Repair
    assert "Context.Services.BuildRoutingResources(" in Source
    assert "BuildTransactionalClusterEndpointRepair(" not in Repair
    assert "'prepare-eligibility'" in Repair
    assert "JointPlacementCandidateIndex=TopologyCandidateIndex" in Repair
    assert "TopologyCandidateBaseIndex" in Repair
    assert "TopologyCandidateBaseIndex + TopologyCandidateOffset" in Repair
    assert "owned-frontier-topology-retained-domain-exhausted" in Repair
    assert "RetainedJointPlacementCandidates * 2" in Repair
    assert (
        "PendingRepairVariants = RepairVariants[AttemptCount:AttemptCount + 1]"
        in Repair
    )
    assert "EffectiveComponentVariant" in Source
    assert "'relocate-endpoint-cluster'" in Source
    assert "JointPlacementCandidateIndex=Variant" not in Repair
    assert "GlobalComponentStateDomainExhausted" in Source


def test_owned_frontier_repair_expands_bounded_topology_equivalent_signals():
    Fingerprints = {
        "A1": "symmetric-input-bit-one",
        "B1": "symmetric-input-bit-one",
        "CarryIn": "carry-input",
    }

    assert PlacementPhysicalAssembly.SelectTopologyEquivalentRepairSignals(
        ("B1",),
        Fingerprints,
    ) == frozenset(("A1", "B1"))
    assert PlacementPhysicalAssembly.SelectTopologyEquivalentRepairSignals(
        ("B1",),
        Fingerprints,
        MaximumSignals=1,
    ) == frozenset(("B1",))


def test_owned_frontier_repair_domain_is_stable_across_symmetric_signals():
    Fingerprints = {
        "A1": "symmetric-input-bit-one",
        "B1": "symmetric-input-bit-one",
        "CarryIn": "carry-input",
    }

    First = (
        PlacementPhysicalAssembly
        .BuildOwnedFrontierTopologyRepairDomainFingerprint(
            ("A1",),
            Fingerprints,
        )
    )
    Second = (
        PlacementPhysicalAssembly
        .BuildOwnedFrontierTopologyRepairDomainFingerprint(
            ("B1",),
            Fingerprints,
        )
    )
    Carry = (
        PlacementPhysicalAssembly
        .BuildOwnedFrontierTopologyRepairDomainFingerprint(
            ("CarryIn",),
            Fingerprints,
        )
    )

    assert First == Second
    assert First != Carry


def test_local_incidence_color_ignores_downstream_signal_names():
    InputKind = SimpleNamespace(value="INPUT")
    NandKind = SimpleNamespace(value="NAND")
    Module = SimpleNamespace(
        Inputs=("A", "B"),
        Outputs=(),
        Gates=(
            SimpleNamespace(
                Kind=InputKind,
                Inputs=(),
                Outputs=("A",),
            ),
            SimpleNamespace(
                Kind=InputKind,
                Inputs=(),
                Outputs=("B",),
            ),
            SimpleNamespace(
                Kind=NandKind,
                Inputs=("A", "X"),
                Outputs=("DifferentA",),
            ),
            SimpleNamespace(
                Kind=NandKind,
                Inputs=("B", "Y"),
                Outputs=("DifferentB",),
            ),
        ),
    )

    Colors = PlacementFeedback.BuildSignalLocalIncidenceFingerprints(Module)

    assert Colors["A"] == Colors["B"]


def test_capacity_repair_requeue_counts_dequeued_channelized_placement():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    DeferredRequestSource = _Sources(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement,
        PlacementPortfolios.AddMandatoryAccessPortfolioPairwiseConstraints,
        PlacementPhysicalFlow.RunPhysicalComponentFlow,
    )
    RequeueStart = Source.index("AttemptedRepairPlacementFingerprints = {")
    RequeueEnd = Source.index("UnattemptedCapacityRepairCandidates", RequeueStart)
    Requeue = Source[RequeueStart:RequeueEnd]

    assert "capacity-pair-repair-dequeued" in Requeue
    assert "bounded-proof-driven-repair-candidate-failed" in Requeue
    assert "DequeuedCapacityRepairPlacementFingerprints" in Source
    assert "capacity-pair-repair-duplicate-dequeue-suppressed" in Source
    assert "Context.InterfaceWorkPhase == 'prepare-eligibility'" in Source
    assert (
        "Context.DequeuedCapacityRepairPlacementFingerprints.add("
        in Source
    )
    assert "if CapacityRepairConstraint is not None:" in (
        DeferredRequestSource
    )
    assert "Context.CapacityRepairPlacementState =" in DeferredRequestSource
    assert "not Context.CapacityRepairPlacementState" in DeferredRequestSource
    assert "PairwiseConflictEdges=(" in DeferredRequestSource
    assert "CapacityRepairConstraint.Signals" in DeferredRequestSource
    assert (
        "CapacityRepairConstraint is None and "
        "InheritedCapacityRepairConstraint is not None"
    ) in DeferredRequestSource
    assert "ClusterInterfacePlacementMaterialization" in (
        DeferredRequestSource
    )
    assert "Context.AccessRepairInterfacePlanningDeadline" in (
        DeferredRequestSource
    )
    assert "in Context.CapacityRepairConstraintByPlacementFingerprint" in (
        DeferredRequestSource
    )


def test_interface_repair_preserves_broad_work_and_records_outcomes():
    Source = _Sources(
        PlacementPhysicalAssembly.EnqueueProofGuidedPhysicalPlacement,
        PlacementPhysicalFlow.RunPhysicalComponentFlow,
    )

    assert "'interface-repair-epoch-started'" in Source
    assert "PreemptedCandidateIds" in Source
    assert "'PreemptedCandidateIds': []" in Source
    assert "'capacity-pair-repair-generated'" in Source
    assert "'capacity-pair-repair-dequeued'" in Source
    assert "'capacity-pair-repair-local-materialized'" in Source
    assert "'capacity-pair-repair-rejected-overlapping-seams'" in Source
    assert "'capacity-repair-witness-reserved'" in Source
    assert "'capacity-repair-csp-admitted'" in Source
    assert "'bounded-proof-driven-repair-candidate-failed'" in Source
    assert "'bounded-proof-driven-repair-exhausted'" in Source
    assert "'PhysicalCapacityRepairPortfolio'" in Source
    assert "'capacity-repair-portfolio-prefetched'" in Source
    assert "'capacity-repair-geometry-portfolio-exhausted'" in Source
    assert "RequireCurrentGeneration=CapacityRepairActive" in Source
    assert "CapacityRepairGeometryConstraintByPlacementFingerprint" in Source
    assert "CapacityRepairGeometryFocusByPlacementFingerprint" in Source
    assert "SelectCapacityRepairGeometryConstraint(" in Source
    assert "SelectCapacityRepairGeometryFocus(" in Source
    assert "CurrentProofPendingStateExists = any(" in Source
    assert "capacity-repair-generation-limit-retained-sibling-admitted" in Source
    assert "'split-relocate'" in Source
    assert "'widen-channel-deck'" in Source
    assert "'split-channel-endpoints'" in Source
    assert "IndexedRepairQueue" not in Source
    assert "ProofKind.startswith('composed-')" in Source
    assert "RepairAttempt == (1 if PreferSplitFirst else 0)" in Source
    assert "if CapacityRepairActive and RepairAttempt == 0:" in Source
    assert (
        "'GeometryKinds': [CapacityRepairGeometryKind, "
        "CapacityRepairFallbackGeometryKind]"
    ) in Source


def test_incomplete_capacity_pair_cannot_build_repair_constraint():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalSymbolicCapacityPlacementFeedback",
        Diagnostics={
            "SymbolicCapacityPlacementFeedback": True,
            "PlacementInterfacePressureSignals": ["Alpha", "Beta"],
            "LocalCapacityCoreClause": [
                ["Alpha", "seam-alpha"], ["Beta", "seam-beta"],
            ],
        },
    )

    assert BuildPhysicalInterfaceRepairCore(Failure, Candidate) is None


def test_incomplete_ownership_core_cannot_drive_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
        Stage="PhysicalEligibilitySolveAfterUnarySupport",
        AffectedNets=("NandNet26",),
        Diagnostics={"OwnershipUnsatCoreFingerprint": "core"},
    )

    assert BuildComponentRoutabilityCore(
        Failure,
        PlacementStateFingerprint="placement",
        ComponentStateFingerprint="component",
        DomainFingerprint="domain",
        CoreFingerprint="core",
        Complete=False,
    ) is None


@pytest.mark.parametrize(
    "Diagnostics",
    (
        {},
        {
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": False,
            "PortAssignmentUnsatCoreSignals": ["Alpha"],
        },
        {
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreSignals": [],
        },
    ),
)
def test_incomplete_or_nonminimal_port_core_cannot_drive_placement(
    Diagnostics,
):
    assert BuildPhysicalComponentPlacementFeedback(RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics=Diagnostics,
    )) is None


def test_two_assembly_plans_reuse_one_prepared_factor_domain(monkeypatch):
    Preparation = SimpleNamespace(
        DomainFingerprint="stable-factor-domain",
        Problem=object(),
        CoarsePlan=object(),
        AccessCertificate=object(),
    )
    FirstAssembly = SimpleNamespace(
        Problem=object(),
        Plan=SimpleNamespace(PlanFingerprint="plan-a"),
        GlobalGuidePlan=Preparation.CoarsePlan,
    )
    SecondAssembly = SimpleNamespace(
        Problem=object(),
        Plan=SimpleNamespace(PlanFingerprint="plan-b"),
        GlobalGuidePlan=Preparation.CoarsePlan,
    )
    Calls = []

    def Solve(
        Value,
        Resources,
        *,
        WorkCheck=None,
        Deadline=None,
        DeferLocalCompositeSelection=False,
        RequiredBoundaryPorts=None,
    ):
        assert Deadline is not None
        assert DeferLocalCompositeSelection
        assert RequiredBoundaryPorts is None
        Calls.append((Value, Value.DomainFingerprint))
        return (FirstAssembly, SecondAssembly)[len(Calls) - 1]

    monkeypatch.setattr(
        PhysicalPortSolving,
        "SolvePreparedPhysicalComponentPortFactorDomain",
        Solve,
    )
    Resources = SimpleNamespace(
        PreparedComponentRoutingProblem=None,
        PreparedPhysicalComponentAssembly=None,
        FrozenPhysicalComponentAssemblyPlan=None,
        FrozenPhysicalComponentGlobalGuidePlan=None,
    )
    Deadline = SimpleNamespace(RaiseIfExpired=lambda *_Args: None)

    First = SolvePreparedPhysicalComponentEligibility(
        Preparation,
        Resources=Resources,
        Deadline=Deadline,
    )
    Second = SolvePreparedPhysicalComponentEligibility(
        Preparation,
        Resources=Resources,
        Deadline=Deadline,
    )

    assert First.Plan.PlanFingerprint == "plan-a"
    assert Second.Plan.PlanFingerprint == "plan-b"
    assert Calls == [
        (Preparation, "stable-factor-domain"),
        (Preparation, "stable-factor-domain"),
    ]
    assert Resources.FrozenPhysicalComponentGlobalGuidePlan is (
        Preparation.CoarsePlan
    )


def test_retained_placement_exhausts_prepared_domain_before_advancing():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    QueueStart = Source.index("'prepare-eligibility',")
    PhaseOrder = Source.index(
        "if Context.InterfaceWorkPhase == 'prepare-eligibility':",
        QueueStart,
    )
    SolveMarker = Source.index(
        "Context.InterfaceCandidateQueue.insert(0, "
        "('solve-prepared-eligibility',",
        PhaseOrder,
    )
    SolveCall = Source.index(
        "SolvePreparedPhysicalComponentEligibility(",
        SolveMarker,
    )

    assert QueueStart < PhaseOrder < SolveMarker < SolveCall
    assert "Context.PreparedEligibilityByState[" in Source[PhaseOrder:SolveMarker]


def test_complete_global_plan_failure_replans_before_local_compilation():
    Reservation = inspect.getsource(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels
    )

    Classify = Reservation.index(
        "ClassifyPhysicalComponentGlobalPlanningFailure("
    )
    RecordNoGood = Reservation.index(
        "RecordPhysicalComponentGlobalPlanNoGood(",
        Classify,
    )
    Reject = Reservation.index(
        "global-planning-reject-physical-plan",
        RecordNoGood,
    )
    Replan = Reservation.index(
        "SelectFreshOrRetainedAssembly()",
        Reject,
    )
    Continue = Reservation.index("continue", Replan)

    assert Classify < RecordNoGood < Reject < Replan < Continue
    assert "RejectedPhysicalComponentPortAssignmentFingerprints" not in (
        Reservation[Classify:Replan]
    )
    assert "'LocalCompilationEntered': False" in Reservation
    assert "'LocalTemplateReopened': False" in Reservation


def test_incomplete_global_plan_is_retained_without_recording_a_no_good():
    Reservation = inspect.getsource(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels
    )

    GlobalFailureStart = Reservation.index(
        "except RoutingStageError as GlobalPlanningError:"
    )
    Incomplete = Reservation.index(
        "RoutingFailureReason.PhysicalComponentAssemblyIncomplete",
        GlobalFailureStart,
    )
    Retain = Reservation.index(
        "RetainIncompleteGlobalPlan(",
        Incomplete,
    )
    Defer = Reservation.index(
        "incomplete-plan-retained",
        Retain,
    )
    Replan = Reservation.index(
        "SelectFreshOrRetainedAssembly()",
        Defer,
    )

    assert Incomplete < Retain < Defer < Replan
    assert "'NoGoodRecorded': False" in Reservation[Retain:Replan]
    assert "'CursorResumeAvailable': bool(" in Reservation[Retain:Replan]
    assert "RecordPhysicalComponentGlobalPlanNoGood(" not in (
        Reservation[Incomplete:Replan]
    )


def test_incomplete_global_plan_timing_closes_before_next_plan_selection():
    Reservation = inspect.getsource(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels
    )
    Incomplete = Reservation.index(
        "RoutingFailureReason.PhysicalComponentAssemblyIncomplete"
    )
    Retained = Reservation.index(
        "GlobalPlanningAttemptResult = 'incomplete-plan-retained'",
        Incomplete,
    )
    Record = Reservation.index(
        "RecordPhysicalComponentStageTiming(",
        Retained,
    )
    MarkRecorded = Reservation.index(
        "GlobalPlanningAttemptRecorded = True",
        Record,
    )
    SelectNext = Reservation.index(
        "SelectFreshOrRetainedAssembly()",
        MarkRecorded,
    )

    assert Retained < Record < MarkRecorded < SelectNext


def test_local_structural_caches_span_retained_placement_candidate_loop():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    CandidateLoop = Source.index(
        "while Context.InterfaceCandidateQueue or "
        "Context.PendingProofGuidedPlacementByComponentVariant:"
    )
    CacheDeclarations = (
        "Context.ComponentVariantPortfolioCache: dict[Any, Any] = {}",
        "Context.ComponentNetVariantConstructionCache: dict[Any, Any] = {}",
        "Context.ComponentRouteClaimsConstructionCache: dict[Any, Any] = {}",
        "Context.ComponentNetVariantDiscoveryStateCache: dict[Any, Any] = {}",
    )

    for Declaration in CacheDeclarations:
        assert Source.count(Declaration) == 1
        assert Source.index(Declaration) < CandidateLoop

    CandidateBody = Source[CandidateLoop:]
    assert all(
        Declaration not in CandidateBody
        for Declaration in CacheDeclarations
    )
    assert (
        "Context.RoutingResourcesByRetainedPlacementFingerprint"
        in Source[:CandidateLoop]
    )


def test_frontier_retention_requires_complete_aperture_and_progress():
    CompleteAperture = {
        "DomainFingerprint": "aperture-a",
        "Complete": True,
    }

    Plan = SimpleNamespace(PlanFingerprint="plan-a", Ports=())
    WithoutCursor = (
        AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            Plan, {}, {}, (), ("aperture-a",), CompletedWork=8,
        )
    )
    assert not ClassifyPhysicalGlobalPlanRetentionAdmission(
        {**CompleteAperture, "Complete": False},
        Continuation=WithoutCursor,
    )["Retained"]
    NonResumable = ClassifyPhysicalGlobalPlanRetentionAdmission(
        CompleteAperture,
        Continuation=WithoutCursor,
    )
    assert NonResumable["Retained"] is False
    assert NonResumable["Reason"] == "resume-cursor-unavailable"
    with pytest.raises(ValueError, match="requires a resumable cursor"):
        AuthoritativeCandidateGuides.RetainIncompletePhysicalGlobalPlan(
            {},
            SimpleNamespace(Plan=Plan),
            WithoutCursor,
            EnqueuedSequence=0,
        )

    with pytest.raises(ValueError, match="no resumable progress"):
        AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            Plan, {}, {}, (), ("aperture-a",),
            CompletedWork=8,
            ResumeCursor=PhysicalGlobalPlanResumeCursor(
                "cursor-empty", "plan-a", "aperture-a", 8, None,
            ),
        )

    Cursor = PhysicalGlobalPlanResumeCursor(
        CursorFingerprint="cursor-a",
        PlanFingerprint="plan-a",
        ApertureDomainFingerprint="aperture-a",
        CompletedWork=8,
        State=object(),
    )
    Resumable = AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
        Plan, {}, {}, (), ("aperture-a",),
        CompletedWork=8,
        ResumeCursor=Cursor,
    )
    Positive = ClassifyPhysicalGlobalPlanRetentionAdmission(
        CompleteAperture,
        Continuation=Resumable,
    )
    assert Positive["Retained"] is True
    assert Positive["Reason"] == "typed-resumable-progress"


def test_candidate_stage_portal_progress_builds_resumable_cursor():
    Cursor, CompletedWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            {
                "UnderlyingFailure": {
                    "Diagnostics": {
                        "PortalCompletedWork": 28,
                        "PortalRequestCount": 40,
                        "PortalCacheMode": "partial-signal",
                        "RawPortalResourceCacheSelected": True,
                    },
                },
            },
        )
    )

    assert CompletedWork == 28
    assert Cursor is not None
    assert Cursor.PlanFingerprint == "plan-a"
    assert Cursor.ApertureDomainFingerprint == "aperture-a"
    assert Cursor.CompletedWork == 28


def test_uncached_portal_failure_does_not_build_resume_cursor():
    Cursor, CompletedWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            {
                "PortalCompletedWork": 28,
                "PortalRequestCount": 40,
                "PortalCacheMode": "disabled",
                "RawPortalResourceCacheSelected": False,
            },
        )
    )

    assert CompletedWork == 28
    assert Cursor is None


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


def test_descriptor_retention_admits_only_a_strict_completed_set_superset():
    First = _DescriptorContinuation(("descriptor-0",))
    Existing = SimpleNamespace(Continuation=First)
    StrictSuperset = _DescriptorContinuation((
        "descriptor-0",
        "descriptor-2",
    ))

    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=StrictSuperset,
        ExistingEntry=Existing,
    )

    assert Admission["Retained"] is True
    assert Admission["DescriptorCompletedSetSuperset"] is True
    assert Admission["DescriptorStrictAddition"] is True


def test_descriptor_retention_keeps_full_two_signal_universe_across_rollover():
    def Continuation(AlphaCompleted, BetaCompleted):
        Diagnostics = {
            "PhysicalSignalRouteDomainDescriptorProgress": {
                "Alpha": {
                    "PreSiblingDomainFingerprint": "pre-alpha",
                    "RequestDomainFingerprint": "request-alpha",
                    "DescriptorUniverseFingerprint": "universe-alpha",
                    "DescriptorCount": 2,
                    "CompletedDescriptorCount": len(AlphaCompleted),
                    "CompletedDescriptorFingerprints": list(
                        AlphaCompleted
                    ),
                },
                "Beta": {
                    "PreSiblingDomainFingerprint": "pre-beta",
                    "RequestDomainFingerprint": "request-beta",
                    "DescriptorUniverseFingerprint": "universe-beta",
                    "DescriptorCount": 2,
                    "CompletedDescriptorCount": len(BetaCompleted),
                    "CompletedDescriptorFingerprints": list(
                        BetaCompleted
                    ),
                },
            },
        }
        Cursor, CompletedWork = (
            BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
                "plan-a",
                "aperture-a",
                Diagnostics,
            )
        )
        assert Cursor is not None
        return AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            SimpleNamespace(PlanFingerprint="plan-a", Ports=()),
            {
                "Alpha": "request-alpha",
                "Beta": "request-beta",
            },
            {
                "Alpha": 2 - len(AlphaCompleted),
                "Beta": 2 - len(BetaCompleted),
            },
            (),
            ("aperture-a",),
            CompletedWork=CompletedWork,
            ResumeCursor=Cursor,
        )

    First = Continuation(("alpha-0",), ())
    Second = Continuation(("alpha-0",), ("beta-1",))
    assert (
        First.ResumeCursor.State.UniverseIdentities
        == Second.ResumeCursor.State.UniverseIdentities
    )

    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=Second,
        ExistingEntry=SimpleNamespace(Continuation=First),
    )

    assert Admission["Retained"] is True
    assert Admission["DescriptorCompletedSetSuperset"] is True
    assert Admission["DescriptorStrictAddition"] is True


def test_portable_conversion_publishes_exact_full_universe_before_retry():
    Cache = {}
    PortableCandidate = SimpleNamespace(
        CandidateId="portable-alpha",
        Payload="translated",
    )
    Alpha, _Advanced = (
        AuthoritativeCandidateGuides
        .RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint="pre-alpha",
            Signal="Alpha",
            RequestDomainFingerprint="request-alpha",
            RequestDescriptorFingerprints=("alpha-0", "alpha-1"),
            CompletedDescriptorFingerprints=("alpha-0", "alpha-1"),
            Candidates=(PortableCandidate,),
            CandidateMetadata={
                "portable-alpha": ("X", 0, 0, 2),
            },
        )
    )
    Beta, _Advanced = (
        AuthoritativeCandidateGuides
        .RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint="pre-beta",
            Signal="Beta",
            RequestDomainFingerprint="request-beta",
            RequestDescriptorFingerprints=("beta-0", "beta-1"),
            CompletedDescriptorFingerprints=(),
            Candidates=(),
            CandidateMetadata={},
        )
    )
    FirstDiagnostics = {
        "PhysicalSignalRouteDomainDescriptorProgress": {
            "Alpha": {
                **Alpha.ToProgressDictionary(),
                "PortableReplayProvenance": True,
            },
            "Beta": Beta.ToProgressDictionary(),
        },
    }

    ReplayedAlpha = (
        AuthoritativeCandidateGuides
        .SelectReplayablePhysicalSignalRouteDomainContinuation(
            Cache,
            "pre-alpha",
            "Alpha",
            "request-alpha",
            ("alpha-0", "alpha-1"),
        )
    )
    assert ReplayedAlpha is Alpha
    BetaAdvanced, StrictlyAdvanced = (
        AuthoritativeCandidateGuides
        .RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint="pre-beta",
            Signal="Beta",
            RequestDomainFingerprint="request-beta",
            RequestDescriptorFingerprints=("beta-0", "beta-1"),
            CompletedDescriptorFingerprints=("beta-1",),
            Candidates=(),
            CandidateMetadata={},
        )
    )
    assert StrictlyAdvanced
    SecondDiagnostics = {
        "PhysicalSignalRouteDomainDescriptorProgress": {
            "Alpha": {
                **ReplayedAlpha.ToProgressDictionary(),
                "PortableReplayProvenance": False,
            },
            "Beta": BetaAdvanced.ToProgressDictionary(),
        },
    }

    def Continuation(Diagnostics):
        Cursor, CompletedWork = (
            BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
                "plan-a",
                "aperture-a",
                Diagnostics,
            )
        )
        assert Cursor is not None
        return AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            SimpleNamespace(PlanFingerprint="plan-a", Ports=()),
            {
                "Alpha": "request-alpha",
                "Beta": "request-beta",
            },
            {
                "Alpha": 0,
                "Beta": (
                    2
                    - len(BetaAdvanced.CompletedDescriptorFingerprints)
                    if Diagnostics is SecondDiagnostics
                    else 2
                ),
            },
            (),
            ("aperture-a",),
            CompletedWork=CompletedWork,
            ResumeCursor=Cursor,
        )

    First = Continuation(FirstDiagnostics)
    Second = Continuation(SecondDiagnostics)
    assert (
        First.ResumeCursor.State.UniverseIdentities
        == Second.ResumeCursor.State.UniverseIdentities
    )
    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=Second,
        ExistingEntry=SimpleNamespace(Continuation=First),
    )
    assert Admission["Retained"] is True
    assert Admission["DescriptorStrictAddition"] is True


def test_descriptor_retention_rejects_equal_cardinality_different_sets():
    First = _DescriptorContinuation(("descriptor-0",))
    Existing = SimpleNamespace(Continuation=First)
    DifferentSet = _DescriptorContinuation(("descriptor-1",))

    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=DifferentSet,
        ExistingEntry=Existing,
    )

    assert Admission["Retained"] is False
    assert Admission["Reason"] == "descriptor-completion-is-not-a-superset"


@pytest.mark.parametrize(
    "ChangedIdentity",
    (
        {"PreSibling": "pre-sibling-b"},
        {"RequestDomain": "request-b"},
        {"Universe": "universe-b"},
        {"DescriptorCount": 4},
    ),
)
def test_descriptor_retention_rejects_universe_or_identity_mismatch(
    ChangedIdentity,
):
    First = _DescriptorContinuation(("descriptor-0",))
    Existing = SimpleNamespace(Continuation=First)
    Changed = _DescriptorContinuation(
        ("descriptor-0", "descriptor-1"),
        **ChangedIdentity,
    )

    Admission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        {"DomainFingerprint": "aperture-a", "Complete": True},
        Continuation=Changed,
        ExistingEntry=Existing,
    )

    assert Admission["Retained"] is False
    assert Admission["Reason"] == (
        "descriptor-universe-or-identity-mismatch"
    )


def test_raw_route_lru_growth_is_not_descriptor_progress():
    Cursor, CompletedWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            {
                "RouteTreeCompletedWork": 23,
                "PhysicalComponentGlobalCandidateContinuations": [{
                    "Signal": "SignalA",
                    "ExecutedRequestCount": 23,
                    "RemainingRequestCount": 40,
                    "MaterializedCandidateCount": 2,
                }],
                "PhysicalGlobalRouteTreeResultCache": {
                    "DescriptorCount": 63,
                    "StoredResultCount": 23,
                    "StoredResultCountAfterDeadlineRetention": 23,
                },
            },
        )
    )

    assert Cursor is None
    assert CompletedWork == 0


def test_descriptor_progress_ignores_raw_route_lru_growth():
    FirstCursor, FirstWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            _DescriptorProgressDiagnostics(
                ("descriptor-0",),
                StoredRouteResults=1,
            ),
        )
    )
    GrownCursor, GrownWork = (
        BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
            "plan-a",
            "aperture-a",
            _DescriptorProgressDiagnostics(
                ("descriptor-0",),
                StoredRouteResults=99,
            ),
        )
    )

    assert FirstCursor is not None and GrownCursor is not None
    assert FirstWork == GrownWork == 1
    assert FirstCursor.CursorFingerprint == GrownCursor.CursorFingerprint


def test_descriptor_retention_two_signal_rollover_requires_zero_seeded_universe():
    def Diagnostics(*, IncludeSignalB, CompletedB=()):
        Progress = {
            "SignalA": {
                "PreSiblingDomainFingerprint": "pre-sibling-a",
                "RequestDomainFingerprint": "request-a",
                "DescriptorUniverseFingerprint": "universe-a",
                "DescriptorCount": 2,
                "CompletedDescriptorCount": 1,
                "CompletedDescriptorFingerprints": ["a-0"],
            },
        }
        if IncludeSignalB:
            Progress["SignalB"] = {
                "PreSiblingDomainFingerprint": "pre-sibling-b",
                "RequestDomainFingerprint": "request-b",
                "DescriptorUniverseFingerprint": "universe-b",
                "DescriptorCount": 2,
                "CompletedDescriptorCount": len(CompletedB),
                "CompletedDescriptorFingerprints": list(CompletedB),
            }
        return {
            "PhysicalSignalRouteDomainDescriptorProgress": Progress,
        }

    def Continuation(DiagnosticsValue):
        Cursor, CompletedWork = (
            BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(
                "plan-a",
                "aperture-a",
                DiagnosticsValue,
            )
        )
        assert Cursor is not None
        return AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            SimpleNamespace(PlanFingerprint="plan-a", Ports=()),
            {"SignalA": "request-a", "SignalB": "request-b"},
            {"SignalA": 1, "SignalB": 1},
            (),
            ("aperture-a",),
            CompletedWork=CompletedWork,
            ResumeCursor=Cursor,
        )

    ZeroSeededFirst = Continuation(Diagnostics(
        IncludeSignalB=True,
    ))
    Second = Continuation(Diagnostics(
        IncludeSignalB=True,
        CompletedB=("b-1",),
    ))
    Aperture = {"DomainFingerprint": "aperture-a", "Complete": True}

    Admitted = ClassifyPhysicalGlobalPlanRetentionAdmission(
        Aperture,
        Continuation=Second,
        ExistingEntry=SimpleNamespace(Continuation=ZeroSeededFirst),
    )

    assert Admitted["Retained"] is True
    assert Admitted["DescriptorStrictAddition"] is True

    OmittedSignalFirst = Continuation(Diagnostics(
        IncludeSignalB=False,
    ))
    Rejected = ClassifyPhysicalGlobalPlanRetentionAdmission(
        Aperture,
        Continuation=Second,
        ExistingEntry=SimpleNamespace(Continuation=OmittedSignalFirst),
    )

    assert Rejected["Retained"] is False
    assert Rejected["Reason"] == (
        "descriptor-universe-or-identity-mismatch"
    )


def test_retained_plan_resume_preserves_aperture_and_fairness_state():
    Plan = SimpleNamespace(
        PlanFingerprint="plan-a",
        PortAssignmentFingerprint="ports-a",
        Ports=(),
    )
    Assembly = SimpleNamespace(Plan=Plan)
    Aperture = {
        "DomainFingerprint": "aperture-a",
        "Complete": True,
    }
    FirstCursor = PhysicalGlobalPlanResumeCursor(
        "cursor-6", "plan-a", "aperture-a", 6, object(),
    )
    FirstContinuation = (
        AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
            Plan,
            {"Signal": "request-a"},
            {"Signal": 4},
            (),
            ("access-certificate", "aperture-a"),
            CompletedWork=6,
            ResumeCursor=FirstCursor,
        )
    )
    FreshAdmission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        Aperture,
        Continuation=FirstContinuation,
    )
    assert FreshAdmission["Retained"] is True
    Frontier = AuthoritativeCandidateGuides.RetainIncompletePhysicalGlobalPlan(
        {},
        Assembly,
        FirstContinuation,
        EnqueuedSequence=0,
    )
    assert AuthoritativeCandidateGuides.ShouldScheduleRetainedPhysicalGlobalPlan(
        Frontier,
        PreviousPlanWasRetained=False,
    )
    Resumed, Frontier = (
        AuthoritativeCandidateGuides.SelectNextRetainedPhysicalGlobalPlan(
            Frontier,
            ScheduleSequence=1,
        )
    )
    assert Resumed.Assembly is Assembly
    RefreshCursor = PhysicalGlobalPlanResumeCursor(
        "cursor-8", "plan-a", "aperture-a", 8, object(),
    )
    Refresh = AuthoritativeCandidateGuides.BuildPhysicalGlobalPlanContinuationState(
        Plan,
        {"Signal": "request-a"},
        {"Signal": 2},
        (),
        ("access-certificate", "aperture-a"),
        CompletedWork=2,
        ResumeCursor=RefreshCursor,
    )
    RefreshAdmission = ClassifyPhysicalGlobalPlanRetentionAdmission(
        Aperture,
        Continuation=Refresh,
        ExistingEntry=Resumed,
    )
    assert RefreshAdmission["Retained"] is True
    Frontier = AuthoritativeCandidateGuides.RetainIncompletePhysicalGlobalPlan(
        Frontier,
        Assembly,
        Refresh,
        EnqueuedSequence=2,
    )

    Entry = Frontier["plan-a"]
    assert Entry.ScheduleCount == 1
    assert Entry.AccumulatedCompletedWork == 8
    assert Entry.Continuation.RemainingRequestCounts == (("Signal", 2),)
    assert "aperture-a" in Entry.Continuation.CertificateFingerprints
    assert AuthoritativeCandidateGuides.ShouldScheduleRetainedPhysicalGlobalPlan(
        Frontier,
        PreviousPlanWasRetained=True,
    )


def test_retained_global_plans_are_serviced_before_another_fresh_plan():
    Source = inspect.getsource(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels
    )
    Start = Source.index("def SelectFreshOrRetainedAssembly(")
    End = Source.index("CurrentAssembly = Assembly", Start)
    Selector = Source[Start:End]

    Fairness = Selector.index(
        "ShouldScheduleRetainedPhysicalGlobalPlan("
    )
    Retained = Selector.index(
        "SelectRetainedAssembly(",
        Fairness,
    )
    Fresh = Selector.index(
        "ReplanPhysicalAssemblyWithTiming(Context)",
        Retained,
    )

    assert Fairness < Retained < Fresh
    assert "PreviousPlanWasRetained" in Selector


def test_physical_component_pipeline_records_explicit_stage_durations():
    Source = _Sources(
        PlacementPhysicalAssembly.RecordPhysicalComponentStageTiming,
        PlacementPhysicalAssembly.ReplanPhysicalAssemblyWithTiming,
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels,
        PlacementPhysicalFlow.RunPhysicalComponentFlow,
    )

    assert "'DurationSeconds'" in Source
    assert "'ElapsedSinceRoutingStartSeconds'" in Source
    assert "'PhysicalComponentStageTimings'" in Source
    for Stage in (
        "PhysicalEligibilityPreparation",
        "PhysicalEligibilitySolveAfterUnarySupport",
        "AuthoritativeGlobalReserve",
        "PhysicalAssemblyReplan",
        "BoundLocalCompilation",
    ):
        assert f"'{Stage}'" in Source


def test_complete_local_capacity_proof_crosses_frozen_interface_as_typed_placement_feedback():
    Context = SimpleNamespace(
        CumulativeSymbolicCapacityPressureSignals={'Generate1', 'CarryIn'},
        LatestSymbolicCapacityRepairEvidence={
            'SymbolicCapacityProofComplete': True,
            'SymbolicCapacityProofFingerprint': 'capacity-proof',
            'LocalCapacityCoreClause': [
                ['CarryIn', 'carry-seam'],
                ['Generate1', 'generate-seam'],
            ],
        },
        PreRouteInterfaceResult=SimpleNamespace(
            ToDictionary=lambda: {'SelectionFingerprint': 'frozen-interface'},
        ),
        SymbolicCapacityAssemblyReplanCount=0,
    )

    with pytest.raises(RoutingStageError) as Raised:
        PlacementPhysicalAssembly.ReplanPhysicalAssemblyWithTiming(Context)

    Failure = Raised.value.Failure
    assert Failure.Reason == RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    assert Failure.Stage == 'PhysicalSymbolicCapacityPlacementFeedback'
    assert Failure.AffectedNets == ('CarryIn', 'Generate1')
    assert Failure.Diagnostics['AutomaticReplanDisabled'] is True
    assert Failure.Diagnostics['SymbolicCapacityPlacementFeedback'] is True
    assert Failure.Diagnostics['PlacementInterfacePressureSignals'] == [
        'CarryIn',
        'Generate1',
    ]
    assert Failure.Diagnostics['GlobalPlanningEntered'] is False
    assert Failure.Diagnostics['LocalCompilationEntered'] is False


def test_physical_component_pipeline_compiles_symbolic_unary_support():
    PipelineSource = "\n".join((
        inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow),
        inspect.getsource(SolvePreparedPhysicalComponentEligibility),
    ))
    EligibilitySource = inspect.getsource(
        SolvePreparedPhysicalComponentEligibility
    )

    assert (
        "CompilePhysicalComponentSymbolicUnaryApertureClauses"
        not in PipelineSource
    )
    assert "PhysicalComponentUnarySupportCompilation" in PipelineSource
    assert (
        "PhysicalComponentSymbolicUnaryApertureClauseCache"
        in PipelineSource
    )
    assert (
        "CompilePhysicalComponentSymbolicUnaryApertureDomain"
        not in EligibilitySource
    )
    assert (
        "CompilePhysicalComponentSymbolicPortPairDomain"
        not in EligibilitySource
    )
    assert (
        "PhysicalComponentSymbolicPairDomainPrecompileFingerprint"
        not in EligibilitySource
    )


def test_successful_global_plan_returns_its_frontier_source_identity():
    Reservation = inspect.getsource(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels
    )

    assert "Context.SuccessfulGlobalPlanWasRetained =" in Reservation
    assert "PreviousGlobalPlanWasRetained" in Reservation
    assert "Context.SuccessfulGlobalPlanWasRetained" in Reservation


def test_boundary_iterator_identity_excludes_branch_and_preference_hints():
    Source = inspect.getsource(
        PhysicalPortSearch._SolvePreparedPhysicalComponentPortFactorDomain
    )
    IdentityStart = Source.index("BoundaryIteratorCacheKey = ")
    IdentityEnd = Source.index(
        "BoundaryAssignmentIterator = None",
        IdentityStart,
    )
    Identity = Source[IdentityStart:IdentityEnd]

    assert "BuildPhysicalComponentAssemblyPlanDomainFingerprint(" in Identity
    assert "Preparation.DomainFingerprint" in Identity
    assert "DeferLocalCompositeSelection" in Identity
    assert "PhysicalComponentBoundaryTraversalEpoch" not in Identity
    assert "PhysicalComponentBoundaryTraversalPrioritySignals" not in Identity
    assert "PreferredPhysicalComponentGlobalContractsBySignal" not in Identity


def test_capacity_repair_seam_witness_is_guidance_with_exact_fallback():
    assert PhysicalPortSearch.BuildCapacityRepairSeamRestrictionPasses(
        {},
        {"Alpha": "seam-a", "Beta": "seam-b"},
    ) == (
        {"Alpha": "seam-a", "Beta": "seam-b"},
        {},
    )
    assert PhysicalPortSearch.BuildCapacityRepairSeamRestrictionPasses(
        {"Alpha": "seam-a"},
        {"Alpha": "seam-a", "Beta": "seam-b"},
    ) == (
        {"Alpha": "seam-a", "Beta": "seam-b"},
        {"Alpha": "seam-a"},
    )


def test_capacity_repair_seam_witness_does_not_override_boundary_contract():
    assert PhysicalPortSearch.BuildCapacityRepairSeamRestrictionPasses(
        {"Alpha": "boundary-seam"},
        {"Alpha": "repair-seam"},
    ) == ({"Alpha": "boundary-seam"},)


def test_capacity_repair_seam_witness_survives_the_scheduled_solve_phase():
    Source = inspect.getsource(
        PlacementPhysicalFlow.RunPhysicalComponentFlow
    )
    PreparePhase = Source.index(
        "if Context.InterfaceWorkPhase == 'prepare-eligibility':"
    )
    ClearWitness = Source.index(
        "PreferredPhysicalComponentSeamContractsBySignal = {}"
    )
    PublishWitness = Source.index(
        "PreferredPhysicalComponentSeamContractsBySignal = dict("
    )
    ScheduleSolve = Source.index(
        "Context.InterfaceCandidateQueue.insert(0, "
        "('solve-prepared-eligibility'"
    )

    assert PreparePhase < ClearWitness < PublishWitness < ScheduleSolve
    assert Source.count(
        "PreferredPhysicalComponentSeamContractsBySignal = {}"
    ) == 1


def test_capacity_repair_seam_witness_projects_to_boundary_preferences():
    Preparation = SimpleNamespace(
        LocalAccessFactorsBySignal=(("Alpha", (
            SimpleNamespace(
                LocalAccessFingerprint="local-a",
                SeamContractFingerprint="seam-a",
            ),
            SimpleNamespace(
                LocalAccessFingerprint="local-other",
                SeamContractFingerprint="seam-other",
            ),
        )),),
        ApertureFactorsBySignal=(("Alpha", (
            SimpleNamespace(
                ApertureOptionFingerprint="option-z",
                GlobalContractFingerprint="global-z",
                ApertureContractFingerprint="aperture-z",
            ),
            SimpleNamespace(
                ApertureOptionFingerprint="option-a",
                GlobalContractFingerprint="global-a",
                ApertureContractFingerprint="aperture-a",
            ),
        )),),
        LocalApertureSupportBySignal=(("Alpha", (
            SimpleNamespace(
                LocalAccessFingerprint="local-a",
                ApertureOptionFingerprint="option-z",
            ),
            SimpleNamespace(
                LocalAccessFingerprint="local-a",
                ApertureOptionFingerprint="option-a",
            ),
            SimpleNamespace(
                LocalAccessFingerprint="local-other",
                ApertureOptionFingerprint="option-z",
            ),
        )),),
    )

    Global, Aperture = (
        PhysicalPortSearch.SelectCapacityRepairBoundaryPreferences(
            Preparation,
            {"Alpha": "seam-a", "Missing": "seam-missing"},
        )
    )

    assert Global == {"Alpha": "global-a"}
    assert Aperture == {"Alpha": "aperture-a"}


@pytest.mark.parametrize(
    "DeferLocalCompositeSelection",
    (False, True),
)
def test_deferred_boundary_selection_preserves_global_only_stage_ownership(
    monkeypatch,
    DeferLocalCompositeSelection,
):
    Preparation = SimpleNamespace(
        LocalAccessFactorsBySignal=(("Signal", ("local",)),),
        ApertureFactorsBySignal=(("Signal", ("aperture",)),),
        LocalApertureSupportBySignal=(("Signal", ("support",)),),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentBoundaryAssignmentFingerprints=set(),
        PreferredPhysicalComponentGlobalContractsBySignal={},
        PreferredPhysicalComponentApertureContractsBySignal={},
        PreferredPhysicalComponentPortReservationsBySignal={},
        PhysicalComponentAperturePortalSlackBySignal={},
        PhysicalBoundaryPairSupportCache={},
    )
    Calls = []

    def IterClosed(_Preparation, **Keywords):
        Calls.append(("closed", Keywords))
        return iter(())

    def IterGlobal(_Domains, **Keywords):
        Calls.append(("global", Keywords))
        return iter(())

    monkeypatch.setattr(
        PhysicalPortSearch,
        "IterClosedComponentContracts",
        IterClosed,
    )
    monkeypatch.setattr(
        PhysicalPortSearch,
        "IterPhysicalBoundaryPortAssignments",
        IterGlobal,
    )

    assert tuple(PhysicalPortSearch.IterPreparedPhysicalBoundaryAssignments(
        Preparation,
        Resources,
        {"Signal": ()},
        (),
        (),
        "solver-domain",
        SimpleNamespace(Technology=SimpleNamespace(TrackPitch=3)),
        lambda _Diagnostics: None,
        None,
        DeferLocalCompositeSelection=DeferLocalCompositeSelection,
    )) == ()

    assert Calls[0][1]["IncludeLocalCompositeFactors"] is (
        not DeferLocalCompositeSelection
    )
    for Key in (
        "LocalAccessFactorsBySignal",
        "ApertureFactorsBySignal",
        "LocalApertureSupportBySignal",
    ):
        assert Calls[1][1][Key] is not None
    assert Calls[1][1]["CertifiedNoGoodProjectionOnly"] is (
        DeferLocalCompositeSelection
    )
    assert Calls[1][1]["PersistentPairSupportCache"] is (
        Resources.PhysicalBoundaryPairSupportCache
    )


def test_priority_port_preparation_stops_on_complete_empty_bank(monkeypatch):
    Calls = []
    Problem = SimpleNamespace(
        Interface=SimpleNamespace(
            Ports=(
                SimpleNamespace(Signal="Alpha"),
                SimpleNamespace(Signal="Beta"),
            ),
        ),
    )
    Resources = SimpleNamespace(
        PhysicalComponentBoundaryTraversalPrioritySignals=("Beta",),
    )
    AccessCertificate = SimpleNamespace(Complete=True)

    def Validate(Context):
        Context.CertifiedPortDomainBySignal = {
            "Alpha": SimpleNamespace(Complete=True),
            "Beta": SimpleNamespace(Complete=True),
        }

    def PrepareConnectors(Context, Signals=None, *, Initialize=True):
        Calls.append(("connectors", Signals, Initialize))
        if Initialize:
            Context.LaneFactorsBySignal = {}
            Context.LaneFactorDiagnosticsBySignal = {}
            Context.NativeConnectorBatchWorkItems = 0
            Context.NativeConnectorBatchActiveWorkerCount = 0
            Context.ExteriorFactorPreparationStartedAt = (
                PhysicalPortPreparation.monotonic()
            )

    def BuildFactors(Context, Signals=None):
        Calls.append(("factors", Signals))
        for Signal in Signals or ("Alpha", "Beta"):
            Context.LaneFactorsBySignal[Signal] = (
                () if Signal == "Beta" else (object(),)
            )
            Context.LaneFactorDiagnosticsBySignal[Signal] = {
                "Reason": (
                    "complete-certified-domain-empty-after-physical-projection"
                    if Signal == "Beta"
                    else "available-certified"
                ),
            }

    monkeypatch.setattr(
        PhysicalPortPreparation,
        "ValidatePhysicalPortPreparation",
        Validate,
    )
    monkeypatch.setattr(
        PhysicalPortPreparation,
        "BuildPhysicalPortChannelReservations",
        lambda _Context: None,
    )
    monkeypatch.setattr(
        PhysicalPortPreparation,
        "BuildPhysicalPortExteriorFabrics",
        lambda _Context: None,
    )
    monkeypatch.setattr(
        PhysicalPortPreparation,
        "PreparePhysicalPortConnectorSearch",
        PrepareConnectors,
    )
    monkeypatch.setattr(
        PhysicalPortPreparation,
        "BuildPhysicalPortLaneFactors",
        BuildFactors,
    )

    with pytest.raises(RoutingStageError) as Raised:
        PhysicalPortPreparation.PreparePhysicalComponentPortFactorDomain(
            object(),
            Problem,
            object(),
            Resources,
            AccessCertificate=AccessCertificate,
        )

    Failure = Raised.value.Failure
    assert Failure.AffectedNets == ("Beta",)
    assert Failure.Diagnostics["PriorityPreparation"] is True
    assert Calls == [
        ("connectors", frozenset(("Beta",)), True),
        ("factors", frozenset(("Beta",))),
    ]


def test_assembly_domain_clause_epoch_is_monotone_and_order_stable():
    DomainFingerprint = BuildPhysicalComponentAssemblyPlanDomainFingerprint(
        "prepared-domain",
        True,
    )

    def Resources(Clauses):
        return SimpleNamespace(
            PhysicalComponentAssemblyPlanDomainFingerprint=(
                DomainFingerprint
            ),
            PhysicalComponentAssemblyPlanClauseStateByDomain={},
            RejectedPhysicalComponentPortReservationSets=set(Clauses),
            RejectedPhysicalComponentPortReservationsBySignal={},
            RejectedPhysicalComponentPortAssignmentFingerprints=set(),
            RejectedPhysicalComponentAssemblyChoiceFingerprints=set(),
            RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
            ForbiddenPhysicalComponentGlobalCandidateSets=set(),
            PhysicalComponentBoundaryTraversalEpoch=0,
            PhysicalComponentBoundaryTraversalPrioritySignals=(),
            PhysicalComponentBoundaryAssignmentIteratorCache={
                DomainFingerprint: object(),
            },
        )

    FirstClause = frozenset((("Alpha", "alpha-0"),))
    SecondClause = frozenset((
        ("Alpha", "alpha-1"),
        ("Beta", "beta-0"),
    ))
    Forward = Resources((FirstClause, SecondClause))
    Reverse = Resources((SecondClause, FirstClause))
    ForwardFirst = PreservePhysicalComponentAssemblyPlanDomainContinuation(
        Forward
    )
    ReverseFirst = PreservePhysicalComponentAssemblyPlanDomainContinuation(
        Reverse
    )

    assert ForwardFirst["AssemblyPlanDomainClauseEpoch"] == 1
    assert (
        ForwardFirst["AssemblyPlanDomainClauseFingerprint"]
        == ReverseFirst["AssemblyPlanDomainClauseFingerprint"]
    )
    assert PreservePhysicalComponentAssemblyPlanDomainContinuation(
        Forward
    )["AssemblyPlanDomainClauseEpoch"] == 1
    Forward.RejectedPhysicalComponentPortReservationSets.add(
        frozenset((("Beta", "beta-1"),))
    )
    Advanced = PreservePhysicalComponentAssemblyPlanDomainContinuation(
        Forward
    )
    assert Advanced["AssemblyPlanDomainClauseEpoch"] == 2
    assert Advanced["BoundaryIteratorContinuationPreserved"] is True
    assert Advanced["BoundaryIteratorCacheCleared"] is False
    assert set(Forward.PhysicalComponentBoundaryAssignmentIteratorCache) == {
        DomainFingerprint,
    }


def test_single_port_global_proof_records_only_targeted_reservation_no_good():
    FrozenHandoff = {
        "Applied": True,
        "PreparationDomainFingerprint": "prepared-domain",
        "PhysicalAssemblyPlanFingerprint": "physical-plan",
        "ExteriorRegionFingerprint": "closed-region",
        "AssignedColumnCount": 41,
        "ReservedAccessCount": 7,
        "PortalEntryCount": 23,
        "PortableProofUsed": False,
    }
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=(
            SimpleNamespace(
                Signal="PortA",
                ReservationFingerprint="reservation-a",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
            SimpleNamespace(
                Signal="PortB",
                ReservationFingerprint="reservation-b",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA",),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "FrozenPostClosurePortalHandoff": FrozenHandoff,
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    assert Failure.Diagnostics["FrozenPostClosurePortalHandoff"] == (
        FrozenHandoff
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={
            "stale-frontier": object(),
        },
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "exact-assembly-port-aperture-set"
    )
    assert Diagnostics["NoGoodSignals"] == ["PortA"]
    ApertureKeys = {
        Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", ApertureKeys["PortA"]),
            ("PortB", ApertureKeys["PortB"]),
        )),
    }
    assert not Resources.RejectedPhysicalComponentPortReservationsBySignal
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Diagnostics["BoundaryTraversalFocusSignal"] == ""
    assert Diagnostics["BoundaryTraversalPrioritySignals"] == []
    assert Diagnostics["BoundaryTraversalEpoch"] == 0
    assert set(Resources.PhysicalComponentBoundaryAssignmentIteratorCache) == {
        "stale-frontier",
    }
    assert Diagnostics["BoundaryIteratorContinuationPreserved"] is True
    assert Diagnostics["BoundaryIteratorCacheCleared"] is False
    assert Diagnostics["AssemblyPlanDomainClauseEpoch"] == 1
    assert Diagnostics["MinimumDeltaReplanPivotSignal"] == "PortA"
    assert Diagnostics["MinimumDeltaRetainedGlobalContracts"] == {
        "PortB": BuildPhysicalPortGlobalContractFingerprint(Plan.Ports[1]),
    }
    assert (
        Resources.PreferredPhysicalComponentGlobalContractsBySignal
        == Diagnostics["MinimumDeltaRetainedGlobalContracts"]
    )


def test_feedthrough_global_proof_records_consumable_exact_assembly_choice():
    Port = SimpleNamespace(
        Signal="PortA",
        Direction="input",
        Attachment=(0, 2, 0),
        GlobalPath=((0, 2, 0), (0, 2, -1)),
        Capacity=1,
        ReservationFingerprint="reservation-a",
        GlobalClaims=SimpleNamespace(
            ResourceIds=frozenset(("wire:port-a",)),
        ),
    )
    Feedthrough = SimpleNamespace(
        Signal="Foreign",
        ReservationFingerprint="feedthrough-reservation",
        EndpointDomainFingerprint="feedthrough-domain",
        EndpointCandidateFingerprint="feedthrough-candidate",
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        PlacementFingerprint="placement",
        ComponentGraphFingerprint="component-graph",
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        InterfaceFingerprint="interface",
        Ports=(Port,),
        Feedthroughs=(Feedthrough,),
        AssemblyChoiceFingerprint="",
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA",),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        RejectedPhysicalComponentAssemblyChoiceFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    ChoiceFingerprint = BuildPhysicalComponentAssemblyChoiceFingerprint(
        Plan
    )
    assert Diagnostics["NoGoodScope"] == (
        "exact-assembly-port-feedthrough-choice"
    )
    assert Diagnostics["RejectedAssemblyChoiceFingerprint"] == (
        ChoiceFingerprint
    )
    assert Resources.RejectedPhysicalComponentAssemblyChoiceFingerprints == {
        ChoiceFingerprint
    }
    assert not Resources.RejectedPhysicalComponentPortReservationSets
    assert not Resources.RejectedPhysicalComponentPortReservationsBySignal
    assert Diagnostics["NoGoodReservationKeys"] == []


def test_feedthrough_independence_proof_allows_port_only_global_no_good():
    Port = SimpleNamespace(
        Signal="PortA",
        ReservationFingerprint="reservation-a",
        GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=(Port,),
        Feedthroughs=(SimpleNamespace(
            Signal="Foreign",
            ReservationFingerprint="feedthrough-reservation",
        ),),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA",),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "AssemblyPlanFeedthroughIndependentProofComplete": True,
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        RejectedPhysicalComponentAssemblyChoiceFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "single-port-aperture-reservation"
    )
    assert not Resources.RejectedPhysicalComponentAssemblyChoiceFingerprints
    assert Resources.RejectedPhysicalComponentPortReservationsBySignal


def test_generated_empty_portal_domain_needs_exact_assembly_certificate():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        PlacementFingerprint="placement",
        InterfaceFingerprint="interface",
        Ports=(SimpleNamespace(
            Signal="CarryLike",
            Direction="input",
            Attachment=(0, 2, 0),
            GlobalPath=((0, 2, 0),),
            Capacity=1,
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )
    GeneratedOnly = AuthoritativeCandidateDomains.BuildMandatoryPortalTupleSelfConflictFailure((
        AuthoritativeRunModels.MandatoryPortalTupleSelfConflictEvidence(
            Signal="CarryLike",
            CompletePortalTupleCount=16,
            EvaluatedPortalTupleCount=16,
            TerminalPortalDomainCounts=(1, 4, 4),
            ConflictResources=(),
        ),
    ))

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        GeneratedOnly,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Reason == (
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete
    )
    assert Classified.Diagnostics["GlobalPlanDomainComplete"] is False


def test_certified_empty_portal_domain_is_complete_exact_plan_unsat():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        PlacementFingerprint="placement",
        InterfaceFingerprint="interface",
        Ports=(SimpleNamespace(
            Signal="CarryLike",
            Direction="input",
            Attachment=(0, 2, 0),
            GlobalPath=((0, 2, 0),),
            Capacity=1,
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ), SimpleNamespace(
            Signal="UnrelatedSibling",
            Direction="input",
            Attachment=(4, 2, 0),
            GlobalPath=((4, 2, 0),),
            Capacity=1,
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        )),
    )
    Certified = AuthoritativeCandidateDomains.BuildMandatoryPortalTupleSelfConflictFailure((
        AuthoritativeRunModels.MandatoryPortalTupleSelfConflictEvidence(
            Signal="CarryLike",
            CompletePortalTupleCount=64,
            EvaluatedPortalTupleCount=64,
            TerminalPortalDomainCounts=(1, 4, 4),
            ConflictResources=(),
            PortalDomainCertificateFingerprint="portal-certificate",
            PhysicalAssemblyPlanFingerprint="physical-plan",
            ResourceGraphFingerprint="resource-graph",
            TechnologyFingerprint="technology",
            PlacementFingerprint="placement",
            InterfaceFingerprint="interface",
            SeamFingerprint="seam",
            PortalRequestDomainFingerprint="request-domain",
            ExactAttachmentValidationFingerprint="attachment-validation",
        ),
    ))

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Certified,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Classified.Diagnostics["GlobalPlanDomainComplete"] is True
    assert Classified.Diagnostics["CompleteAssignmentCutProof"] is True
    assert Classified.Diagnostics["AssemblyPlanDependencySignals"] == [
        "CarryLike"
    ]
    assert Classified.Diagnostics["AssemblyPlanDependentPortSignals"] == [
        "CarryLike"
    ]
    assert Classified.Diagnostics[
        "IndependentEmptyCandidateDomainSignals"
    ] == ["CarryLike"]

    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        RejectedPhysicalComponentAssemblyChoiceFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )
    NoGood = RecordPhysicalComponentGlobalPlanNoGood(
        Classified,
        Plan,
        Resources,
    )

    assert NoGood["NoGoodScope"] == (
        "independent-empty-global-route-domain"
    )
    assert NoGood["NoGoodConstraintArity"] == 1
    assert set(
        Resources.RejectedPhysicalComponentPortReservationsBySignal
    ) == {"CarryLike"}
    assert not Resources.RejectedPhysicalComponentPortReservationSets


def test_joint_port_global_proof_records_only_targeted_reservation_tuple():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=(
            SimpleNamespace(
                Signal="PortA",
                ReservationFingerprint="reservation-a",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
            SimpleNamespace(
                Signal="PortB",
                ReservationFingerprint="reservation-b",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
            SimpleNamespace(
                Signal="PortC",
                ReservationFingerprint="reservation-c",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ),
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA", "PortB"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "exact-assembly-port-aperture-set"
    )
    assert Diagnostics["NoGoodSignals"] == ["PortA", "PortB"]
    GlobalKeys = {
        Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", GlobalKeys["PortA"]),
            ("PortB", GlobalKeys["PortB"]),
            ("PortC", GlobalKeys["PortC"]),
        )),
    }
    assert not Resources.RejectedPhysicalComponentPortReservationsBySignal
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Diagnostics["BoundaryTraversalFocusSignal"] == ""
    assert Diagnostics["BoundaryTraversalPrioritySignals"] == []
    assert Diagnostics["BoundaryIteratorContinuationPreserved"] is True
    assert Diagnostics["BoundaryIteratorCacheCleared"] is False
    assert Diagnostics["AssemblyPlanDomainClauseEpoch"] == 1

    Rotated = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )
    assert Rotated["BoundaryTraversalFocusSignal"] == ""
    assert Rotated["BoundaryTraversalPrioritySignals"] == []
    assert Rotated["BoundaryTraversalEpoch"] == 0
    assert Rotated["AssemblyPlanDomainClauseEpoch"] == 1


def test_complete_dependency_cut_ignores_unrelated_port_variation():
    DependencySignals = (
        "CarryIn",
        "CarryOut",
        "NandNet28",
        "NandNet29",
        "NandNet31",
    )

    def Port(Signal, Index, *, UnrelatedOffset=0):
        X = Index + UnrelatedOffset
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricDomainFingerprint=f"fabric-{Signal}",
            FabricAttachment=(X, 2, 1),
            Attachment=(X, 2, 0),
            OwnedTerminals=((X, 2, 2),),
            LocalPath=((X, 2, 1), (X, 2, 0)),
            GlobalPath=((X, 2, 0), (X, 2, -1)),
            Capacity=1,
            ReservationFingerprint=f"reservation-{Signal}-{X}",
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        )

    def Plan(UnrelatedOffset):
        return SimpleNamespace(
            PlanFingerprint=f"physical-plan-{UnrelatedOffset}",
            PortAssignmentFingerprint=f"assignment-{UnrelatedOffset}",
            Ports=tuple((
                *(Port(Signal, Index) for Index, Signal in enumerate(
                    DependencySignals
                )),
                Port("NandNet26", 20, UnrelatedOffset=UnrelatedOffset),
            )),
            Feedthroughs=(),
        )

    FirstPlan = Plan(0)
    FirstFailure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=DependencySignals,
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "MandatoryAccessProof": {"Complete": True},
                "PairwisePortReservationNoGoodProofComplete": True,
                "PairwisePortReservationNoGoodEdges": [
                    ["CarryIn", "CarryOut"],
                    ["CarryOut", "NandNet29"],
                    ["NandNet28", "NandNet31"],
                ],
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": list(DependencySignals),
                    "CongestionCutSignals": list(DependencySignals),
                    "PairwiseIncompatibleEdges": [
                        ["CarryIn", "CarryOut"],
                        ["CarryOut", "NandNet29"],
                        ["NandNet28", "NandNet31"],
                    ],
                },
            },
        ),
        FirstPlan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )
    FirstDiagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        FirstFailure,
        FirstPlan,
        Resources,
    )
    ExpectedClauses = set(
        Resources.RejectedPhysicalComponentPortReservationSets
    )

    SecondPlan = Plan(100)
    SecondFailure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=DependencySignals,
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "MandatoryAccessProof": {"Complete": True},
                "PairwisePortReservationNoGoodProofComplete": True,
                "PairwisePortReservationNoGoodEdges": [
                    ["CarryIn", "CarryOut"],
                    ["CarryOut", "NandNet29"],
                    ["NandNet28", "NandNet31"],
                ],
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": list(DependencySignals),
                    "CongestionCutSignals": list(DependencySignals),
                    "PairwiseIncompatibleEdges": [
                        ["CarryIn", "CarryOut"],
                        ["CarryOut", "NandNet29"],
                        ["NandNet28", "NandNet31"],
                    ],
                },
            },
        ),
        SecondPlan,
        DeadlineExpired=False,
    )
    SecondDiagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        SecondFailure,
        SecondPlan,
        Resources,
    )

    assert len(ExpectedClauses) == 3
    assert all(len(Clause) == 2 for Clause in ExpectedClauses)
    assert Resources.RejectedPhysicalComponentPortReservationSets == (
        ExpectedClauses
    )
    assert FirstDiagnostics["NoGoodConstraintArity"] == 2
    assert SecondDiagnostics["NoGoodConstraintArity"] == 2
    assert len(FirstDiagnostics["NoGoodReservationSets"]) == 3
    assert len(SecondDiagnostics["NoGoodReservationSets"]) == 3
    assert FirstDiagnostics[
        "AssemblyPlanDependencyProjectionProofComplete"
    ] is True
    assert SecondDiagnostics[
        "AssemblyPlanDependencyProjectionProofComplete"
    ] is True


def test_complete_higher_order_exterior_core_projects_exact_port_subset():
    def Port(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Attachment=(X, 2, 0),
            GlobalPath=((X, 2, 0), (X, 2, -1)),
            Capacity=1,
            ReservationFingerprint=f"reservation-{Signal}",
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        )

    CoreSignals = ("CarryIn", "CarryOut", "NandNet29")
    ReportedDeadEndSignals = (*CoreSignals, "DeadEndWitness")
    Plan = SimpleNamespace(
        PlanFingerprint="higher-order-plan",
        PortAssignmentFingerprint="higher-order-assignment",
        Ports=tuple(Port(Signal, Index) for Index, Signal in enumerate((
            *ReportedDeadEndSignals,
            "Unrelated",
        ))),
        Feedthroughs=(),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=ReportedDeadEndSignals,
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "HigherOrderPortReservationNoGoodProofComplete": True,
                "HigherOrderPortReservationNoGoodSignals": list(
                    CoreSignals
                ),
                "HigherOrderPortReservationNoGoodCandidateCounts": {
                    "CarryIn": 25,
                    "CarryOut": 20,
                    "NandNet29": 32,
                },
                "ConflictGraph": {
                    "Classification": "higher-order-placement-conflict",
                    "ConflictSignals": list(ReportedDeadEndSignals),
                },
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Failure.Diagnostics[
        "HigherOrderPortReservationNoGoodProofComplete"
    ] is True
    assert Failure.Diagnostics[
        "AssemblyPlanDependencyIdentityComplete"
    ] is True
    assert Diagnostics[
        "AssemblyPlanDependencyProjectionProofComplete"
    ] is True
    assert Diagnostics["NoGoodConstraintArity"] == 3
    assert Diagnostics["MinimumDeltaReplanPivotSignal"] == "CarryOut"
    assert Diagnostics[
        "MinimumDeltaCertifiedExteriorDomainCounts"
    ] == {
        "CarryIn": 25,
        "CarryOut": 20,
        "NandNet29": 32,
    }
    Clause = next(iter(
        Resources.RejectedPhysicalComponentPortReservationSets
    ))
    assert {Signal for Signal, _Fingerprint in Clause} == set(CoreSignals)


def test_complete_independent_empty_route_domains_reject_exact_ports():
    Ports = tuple(
        SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Attachment=(Index, 2, 0),
            GlobalPath=((Index, 2, 0), (Index, 2, -1)),
            Capacity=1,
            ReservationFingerprint=f"reservation-{Signal.lower()}",
            Claims=SimpleNamespace(
                ResourceIds=frozenset((f"wire:{Signal}",)),
            ),
            GlobalClaims=SimpleNamespace(
                ResourceIds=frozenset((f"global-wire:{Signal}",)),
            ),
        )
        for Index, Signal in enumerate(("EmptyA", "EmptyB", "Sibling"))
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=Ports,
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=(
                RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
            ),
            Stage="PhysicalComponentGlobalCandidateDomain",
            AffectedNets=("EmptyA", "EmptyB", "Sibling"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "IndependentEmptyCandidateDomainSignals": [
                    "EmptyA",
                    "EmptyB",
                ],
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        PreparedPhysicalComponentPortFactorDomain=SimpleNamespace(
            DomainFingerprint="prepared-port-domain",
        ),
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "independent-empty-global-route-domain"
    )
    assert set(
        Resources.RejectedPhysicalComponentPortReservationsBySignal
    ) == {"EmptyA", "EmptyB"}
    assert "Sibling" not in (
        Resources.RejectedPhysicalComponentPortReservationsBySignal
    )
    assert not Resources.RejectedPhysicalComponentPortReservationSets


def test_request_aperture_proof_retains_global_determinants_and_scope():
    Ports = tuple(
        SimpleNamespace(
            Signal=Signal,
            Direction="input",
            Attachment=(Index, 2, 0),
            GlobalPath=((Index, 2, 0), (Index, 2, -1)),
            Capacity=1,
            ReservationFingerprint=f"reservation-{Signal.lower()}",
            Claims=SimpleNamespace(
                ResourceIds=frozenset((f"wire:{Signal}",)),
            ),
            GlobalClaims=SimpleNamespace(
                ResourceIds=frozenset((f"global-wire:{Signal}",)),
            ),
        )
        for Index, Signal in enumerate((
            "Victim",
            "Blocker",
            *(f"Unrelated{Index}" for Index in range(16)),
        ))
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=Ports,
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalCandidateDomain",
            AffectedNets=("Victim", "Blocker"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "RequestApertureFactorProofComplete": True,
                "RequestApertureFactorNoGood": [
                    ["Victim", "request-factor:victim"],
                    ["Blocker", "aperture-factor:blocker"],
                ],
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        PreparedPhysicalComponentPortFactorDomain=SimpleNamespace(
            DomainFingerprint="prepared-port-domain",
        ),
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "request-aperture-factor-port-set"
    )
    assert Diagnostics["AssemblyPortCount"] == 18
    assert Diagnostics["NoGoodConstraintArity"] == 21
    assert len(Resources.RejectedPhysicalComponentPortReservationSets) == 1
    NoGood = next(iter(
        Resources.RejectedPhysicalComponentPortReservationSets
    ))
    assert {Port.Signal for Port in Ports} == {
        Signal for Signal, _Fingerprint in NoGood
    }
    assert {
        (Port.Signal, BuildPhysicalPortGlobalContractFingerprint(Port))
        for Port in Ports
    } <= NoGood
    assert any(
        Fingerprint.startswith("local-signal-domain:")
        for _Signal, Fingerprint in NoGood
    )
    ExpectedRetainedContracts = {
        Port.Signal: BuildPhysicalPortGlobalContractFingerprint(Port)
        for Port in Ports
        if Port.Signal != "Blocker"
    }
    assert Diagnostics["MinimumDeltaReplanPivotSignal"] == "Blocker"
    assert Diagnostics["BoundaryTraversalFocusSignal"] == ""
    assert Diagnostics["BoundaryIteratorContinuationPreserved"] is True
    assert Diagnostics["BoundaryIteratorCacheCleared"] is False
    assert Diagnostics["MinimumDeltaRetainedGlobalContracts"] == (
        ExpectedRetainedContracts
    )
    assert (
        Resources.PreferredPhysicalComponentGlobalContractsBySignal
        == ExpectedRetainedContracts
    )


def test_global_plan_dependency_identity_tracks_only_fixed_cut_contracts():
    Plan = SimpleNamespace(Ports=(
        SimpleNamespace(
            Signal="PortA",
            Direction="input",
            Attachment=(1, 2, 3),
            GlobalPath=((1, 2, 3), (0, 2, 3)),
            Capacity=1,
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),
        SimpleNamespace(
            Signal="PortB",
            Direction="output",
            Attachment=(9, 2, 3),
            GlobalPath=((9, 2, 3), (10, 2, 3)),
            Capacity=1,
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),
    ))

    First = BuildPhysicalGlobalPlanDependencyFingerprint(
        Plan,
        ("Foreign", "PortA"),
    )
    Reordered = BuildPhysicalGlobalPlanDependencyFingerprint(
        Plan,
        ("PortA", "Foreign", "PortA"),
    )
    UnrelatedPortChanged = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(
            Plan.Ports[0],
            SimpleNamespace(
                **{
                    **vars(Plan.Ports[1]),
                    "Attachment": (11, 2, 3),
                }
            ),
        )),
        ("Foreign", "PortA"),
    )
    DependentPortChanged = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(
            SimpleNamespace(
                **{
                    **vars(Plan.Ports[0]),
                    "Attachment": (2, 2, 3),
                }
            ),
            Plan.Ports[1],
        )),
        ("Foreign", "PortA"),
    )

    assert First == Reordered == UnrelatedPortChanged
    assert First != DependentPortChanged


def test_global_dependency_identity_includes_exact_aperture_claims():
    BasePort = SimpleNamespace(
        Signal="PortA",
        Direction="input",
        Attachment=(1, 2, 3),
        GlobalPath=((1, 2, 3), (0, 2, 3)),
        Capacity=1,
        ReservationFingerprint="reservation-a",
        Claims=SimpleNamespace(ResourceIds=frozenset(("wire:1",))),
        GlobalClaims=SimpleNamespace(
            ResourceIds=frozenset(("global-wire:1",))
        ),
    )
    Base = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(BasePort,)),
        ("PortA",),
    )
    ChangedClaims = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(SimpleNamespace(
            **{
                **vars(BasePort),
                "GlobalClaims": SimpleNamespace(
                    ResourceIds=frozenset(("global-wire:2",)),
                ),
            }
        ),)),
        ("PortA",),
    )
    ChangedLocalReservation = BuildPhysicalGlobalPlanDependencyFingerprint(
        SimpleNamespace(Ports=(SimpleNamespace(
            **{
                **vars(BasePort),
                "ReservationFingerprint": "reservation-b",
            }
        ),)),
        ("PortA",),
    )

    assert Base != ChangedClaims
    assert Base == ChangedLocalReservation


def test_global_aperture_identity_ignores_local_seam_claims():
    GlobalClaims = SimpleNamespace(
        ResourceIds=frozenset(("global-wire",)),
    )
    BasePort = SimpleNamespace(
        Signal="PortA",
        Direction="input",
        Attachment=(1, 2, 3),
        GlobalPath=((1, 2, 3), (0, 2, 3)),
        Capacity=1,
        ReservationFingerprint="local-reservation-a",
        Claims=SimpleNamespace(ResourceIds=frozenset(("local-a",))),
        GlobalClaims=GlobalClaims,
    )
    ChangedLocal = SimpleNamespace(
        **{
            **vars(BasePort),
            "ReservationFingerprint": "local-reservation-b",
            "Claims": SimpleNamespace(
                ResourceIds=frozenset(("local-b",)),
            ),
        }
    )

    assert BuildPhysicalPortApertureContractFingerprint(
        BasePort
    ) == BuildPhysicalPortApertureContractFingerprint(ChangedLocal)


def test_request_aperture_proof_projects_only_required_local_claims():
    Ports = tuple(
        SimpleNamespace(
            Signal=Signal,
            Direction="input",
            Attachment=(Index, 2, 0),
            GlobalPath=((Index, 2, 0), (Index, 2, -1)),
            Capacity=1,
            ReservationFingerprint=f"reservation-{Signal.lower()}",
            Claims=SimpleNamespace(
                ResourceIds=frozenset((f"wire:{Signal}",)),
            ),
            GlobalClaims=SimpleNamespace(
                ResourceIds=frozenset((f"global-wire:{Signal}",)),
            ),
        )
        for Index, Signal in enumerate(("Alpha", "Beta", "Gamma"))
    )
    NoGood = BuildPhysicalRequestAperturePortNoGood(
        SimpleNamespace(Ports=Ports),
        frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
        )),
    )

    assert {
        (Port.Signal, BuildPhysicalPortGlobalContractFingerprint(Port))
        for Port in Ports
    } <= NoGood
    assert (
        "Alpha",
        BuildPhysicalPortApertureContractFingerprint(Ports[0]),
    ) in NoGood
    assert (
        "Beta",
        BuildPhysicalPortApertureContractFingerprint(Ports[1]),
    ) in NoGood
    assert all(
        not (
            Signal == "Gamma"
            and Fingerprint.startswith("aperture-contract-v2:")
        )
        for Signal, Fingerprint in NoGood
    )


def test_certified_signal_local_request_aperture_proof_is_domain_scoped():
    Ports = tuple(
        SimpleNamespace(
            Signal=Signal,
            Direction="input",
            Attachment=(Index, 2, 0),
            GlobalPath=((Index, 2, 0), (Index, 2, -1)),
            Capacity=1,
            ReservationFingerprint=f"reservation-{Signal.lower()}",
            Claims=SimpleNamespace(
                ResourceIds=frozenset((f"wire:{Signal}",)),
            ),
            GlobalClaims=SimpleNamespace(
                ResourceIds=frozenset((f"global-wire:{Signal}",)),
            ),
        )
        for Index, Signal in enumerate(("Alpha", "Beta", "Gamma"))
    )
    NoGood = BuildPhysicalRequestAperturePortNoGood(
        SimpleNamespace(Ports=Ports),
        frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
        )),
        SignalLocalRequestFactorProofComplete=True,
        PortSolverCacheKey="solver-domain",
    )

    assert (
        "Alpha",
        BuildPhysicalPortGlobalContractFingerprint(Ports[0]),
    ) in NoGood
    assert NoGood == frozenset((
        (
            "Alpha",
            BuildPhysicalPortGlobalContractFingerprint(Ports[0]),
        ),
        (
            "Beta",
            BuildPhysicalPortApertureContractFingerprint(Ports[1]),
        ),
    ))
    assert (
        "Gamma",
        BuildPhysicalPortApertureContractFingerprint(Ports[2]),
    ) not in NoGood

    Unscoped = BuildPhysicalRequestAperturePortNoGood(
        SimpleNamespace(Ports=Ports),
        frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
        )),
        SignalLocalRequestFactorProofComplete=True,
    )
    assert {
        (Port.Signal, BuildPhysicalPortGlobalContractFingerprint(Port))
        for Port in Ports
    } <= Unscoped


def test_global_route_reuse_ignores_local_port_contract_only():
    def Plan(LocalPath, GlobalPath=((2, 2, 0), (2, 2, -1))):
        Port = SimpleNamespace(
            Signal="Alpha",
            Direction="input",
            Attachment=(2, 2, 0),
            GlobalPath=GlobalPath,
            LocalPath=LocalPath,
            Capacity=1,
        )
        Channel = SimpleNamespace(
            Signal="Alpha",
            Layer=0,
            GuideCells=((2, 0), (2, -1)),
            Capacity=1,
            FeedthroughComponentIds=(),
            ReservationFingerprint="channel-alpha",
        )
        return SimpleNamespace(
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="resource",
            TechnologyFingerprint="technology",
            EnvelopeMinimum=(0, 0, 0),
            EnvelopeMaximum=(4, 4, 4),
            GlobalKeepoutFingerprint="keepout",
            Ports=(Port,),
            PlanningChannels=(Channel,),
            Feedthroughs=(),
        )

    First = Plan(((0, 2, 0), (2, 2, 0)))
    LocalChanged = Plan(((0, 3, 0), (2, 2, 0)))
    GlobalChanged = Plan(
        ((0, 2, 0), (2, 2, 0)),
        ((2, 2, 0), (3, 2, 0)),
    )

    assert PhysicalAssemblyGlobalRouteCanBeRebound(First, LocalChanged)
    assert not PhysicalAssemblyGlobalRouteCanBeRebound(
        First,
        GlobalChanged,
    )
    assert BuildPhysicalAssemblyGlobalReuseFingerprint(First).startswith(
        "global-assembly-reuse-v1:"
    )


def test_global_cut_family_identity_ignores_candidate_variant_identity():
    First = {
        "Classification": "multi-pair-placement-conflict",
        "FailureNet": "PortC",
        "ConflictSignals": ["PortC", "PortA", "PortB"],
        "PairwiseIncompatibleEdges": [
            ["PortA", "PortC"],
            ["PortB", "PortC"],
        ],
        "CandidateCounts": {"PortA": 48, "PortC": 47},
        "PortalReservations": [{"PortalId": "variant-a"}],
    }
    Second = {
        **First,
        "ConflictSignals": ["PortB", "PortA", "PortC"],
        "PairwiseIncompatibleEdges": [
            ["PortC", "PortB"],
            ["PortC", "PortA"],
        ],
        "CandidateCounts": {"PortA": 72, "PortC": 72},
        "PortalReservations": [{"PortalId": "variant-b"}],
    }

    assert BuildPhysicalGlobalPlanCutFamilyFingerprint(First) == (
        BuildPhysicalGlobalPlanCutFamilyFingerprint(Second)
    )


def test_global_planning_classifier_exposes_proof_dependency_identities():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(
            Signal="PortA",
            Direction="input",
            Attachment=(1, 2, 3),
                GlobalPath=((1, 2, 3),),
                Capacity=1,
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="PhysicalComponentGlobalAssignmentDomain",
        AffectedNets=("PortA", "Foreign"),
        Diagnostics={
            "GlobalPlanDomainComplete": True,
            "CompleteAssignmentCutProof": True,
            "CandidateFingerprint": "candidate-domain",
            "ConflictFingerprint": "conflict-domain",
            "ConflictGraph": {
                "Classification": "pairwise-incompatibility",
                "ConflictSignals": ["Foreign", "PortA"],
                "PairwiseIncompatibleEdges": [["PortA", "Foreign"]],
            },
        },
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    Diagnostics = Classified.Diagnostics
    assert Diagnostics["GlobalPlanDependencyFingerprint"].startswith(
        "global-dependency-v2:"
    )
    assert Diagnostics["GlobalPlanCutFamilyFingerprint"].startswith(
        "global-cut-family-v1:"
    )
    assert Diagnostics["GlobalPlanProofFingerprint"].startswith(
        "global-proof-v1:"
    )


def test_complete_pairwise_global_cut_records_each_exact_reservation_edge():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=tuple(
            SimpleNamespace(
                Signal=Signal,
                ReservationFingerprint=f"reservation-{Signal.lower()}",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            )
            for Signal in ("PortA", "PortB", "PortC")
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA", "PortB", "PortC"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "PairwisePortReservationNoGoodProofComplete": True,
                "ConflictGraph": {
                    "PairwiseIncompatibleEdges": [
                        ["PortA", "PortB"],
                        ["PortA", "PortC"],
                    ],
                },
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        PreparedPhysicalComponentPortFactorDomain=SimpleNamespace(
            DomainFingerprint="prepared-domain",
        ),
        PhysicalBoundaryMandatoryPortalFactorDomainCache={
            ("prepared-domain", Signal, f"aperture-{Signal}"):
            SimpleNamespace(Signal=Signal, Complete=False)
            for Signal in ("PortA", "PortB", "PortC")
        },
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "pairwise-port-aperture-reservation-sets"
    )
    assert Diagnostics["NoGoodConstraintArity"] == 2
    GlobalKeys = {
        Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", GlobalKeys["PortA"]),
            ("PortB", GlobalKeys["PortB"]),
        )),
        frozenset((
            ("PortA", GlobalKeys["PortA"]),
            ("PortC", GlobalKeys["PortC"]),
        )),
    }
    assert len(Diagnostics["NoGoodReservationSets"]) == 2
    assert Diagnostics["PreparedMandatoryPortalPairFactorStatus"] == {
        "Available": False,
        "ExpectedSignals": ["PortA", "PortB", "PortC"],
        "PreparedSignalCount": 3,
        "FactorDomainCount": 3,
        "CompleteFactorDomainCount": 0,
        "IncompleteSignals": ["PortA", "PortB", "PortC"],
        "OtherPreparedDomainFactorCount": 0,
        "OptionCountsBySignal": {
            "PortA": 1,
            "PortB": 1,
            "PortC": 1,
        },
        "OptionProduct": 1,
        "MaximumEagerOptionProduct": 65_536,
        "EagerCompilationSelected": False,
    }


def test_pair_relation_deadline_retains_only_current_exact_pair_clause(
    monkeypatch,
):
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=tuple(
            SimpleNamespace(
                Signal=Signal,
                ReservationFingerprint=f"reservation-{Signal.lower()}",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            )
            for Signal in ("PortA", "PortB")
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA", "PortB"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "PairwisePortReservationNoGoodProofComplete": True,
                "PairwisePortReservationNoGoodEdges": [
                    ["PortA", "PortB"],
                ],
                "MandatoryAccessProof": {
                    "Kind": "generated-fixed-portal-domain-exhausted",
                    "Complete": True,
                    "PortalTupleDomainComplete": True,
                    "ProofScope": "complete-portal-tuple-domain",
                    "BudgetExhausted": False,
                    "DeadlineExceeded": False,
                },
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": ["PortA", "PortB"],
                    "CongestionCutSignals": ["PortA", "PortB"],
                    "PairwiseIncompatibleEdges": [["PortA", "PortB"]],
                },
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Preparation = SimpleNamespace(DomainFingerprint="prepared-domain")
    Resources = SimpleNamespace(
        PreparedPhysicalComponentPortFactorDomain=Preparation,
        PhysicalBoundaryMandatoryPortalFactorDomainCache={
            ("prepared-domain", "PortA", "aperture-a"):
            SimpleNamespace(Signal="PortA", Complete=True),
            ("prepared-domain", "PortB", "aperture-b"):
            SimpleNamespace(Signal="PortB", Complete=True),
        },
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )
    DeadlineChecks = []

    def Compile(
        _Preparation,
        Signals,
        _Resources,
        ShouldStop=None,
        **_Options,
    ):
        DeadlineChecks.append(ShouldStop())
        return SimpleNamespace(
            RelationFingerprint="pair-relation",
            Signals=tuple(sorted(Signals)),
            ExpectedOptionPairCount=4,
            Certificates=(),
            UnsatisfiableApertureClauses=(),
            ForeignDependencyCertificateCount=0,
            Complete=False,
        )

    monkeypatch.setattr(
        BoundaryRelations,
        "CompilePhysicalBoundaryMandatoryPortalPairRelation",
        Compile,
    )

    NoGood = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
        ShouldStop=lambda: True,
    )

    assert DeadlineChecks == [True]
    assert NoGood["NoGoodConstraintArity"] == 2
    assert len(Resources.RejectedPhysicalComponentPortReservationSets) == 1
    assert NoGood[
        "CompiledMandatoryPortalPairRelations"
    ][0]["Complete"] is False


def test_foreign_dependent_mandatory_pair_cannot_project_port_subset():
    def Port(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Capacity=1,
            Attachment=(X, 2, 0),
            GlobalPath=((X, 2, 0), (X, 2, -1)),
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            ReservationFingerprint=f"reservation-{Signal}",
        )

    Plan = SimpleNamespace(
        PlanFingerprint="foreign-dependent-plan",
        PortAssignmentFingerprint="foreign-dependent-assignment",
        Ports=(Port("PortA", 0), Port("PortB", 2), Port("Unrelated", 4)),
        Feedthroughs=(),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="InitialCandidateAssignment",
            AffectedNets=("PortA", "PortB"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                "MandatoryAccessProof": {
                    "Kind": "generated-fixed-portal-domain-exhausted",
                    "Complete": True,
                    "PortalTupleDomainComplete": True,
                    "ProofScope": "complete-portal-tuple-domain",
                    "BudgetExhausted": False,
                    "DeadlineExceeded": False,
                },
                # The observed edge exists, but its exact certificate also
                # depended on a frozen foreign owner and was not promoted.
                "PairwisePortReservationNoGoodProofComplete": False,
                "PairwisePortReservationNoGoodEdges": [],
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": ["PortA", "PortB"],
                    "CongestionCutSignals": ["PortA", "PortB"],
                    "PairwiseIncompatibleEdges": [["PortA", "PortB"]],
                },
            },
        ),
        Plan,
        DeadlineExpired=False,
    )

    assert not Failure.Diagnostics[
        "MandatoryPairDependencyIdentityComplete"
    ]
    assert not Failure.Diagnostics[
        "AssemblyPlanDependencyIdentityComplete"
    ]
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        PhysicalComponentBoundaryAssignmentIteratorCache={},
    )
    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    Clause = next(iter(
        Resources.RejectedPhysicalComponentPortReservationSets
    ))
    assert {Signal for Signal, _Fingerprint in Clause} == {
        "PortA",
        "PortB",
        "Unrelated",
    }
    assert Diagnostics["NoGoodScope"] == (
        "exact-assembly-port-aperture-set"
    )
    assert not Diagnostics[
        "AssemblyPlanDependencyProjectionProofComplete"
    ]


def test_new_aperture_clause_purges_matching_retained_global_plan():
    def Port(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Capacity=1,
            Attachment=(X, 2, 0),
            GlobalPath=((X, 2, 0), (X, 2, -1)),
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        )

    RejectedPlan = SimpleNamespace(
        PlanFingerprint="rejected-plan",
        Ports=(Port("PortA", 0), Port("PortB", 2)),
    )
    RetainedPlan = SimpleNamespace(
        PlanFingerprint="retained-plan",
        Ports=(Port("PortA", 8), Port("PortB", 2)),
    )
    Clause = frozenset(
        (
            PortValue.Signal,
            BuildPhysicalPortApertureContractFingerprint(PortValue),
        )
        for PortValue in RejectedPlan.Ports
    )
    Frontier = {
        "rejected-plan": SimpleNamespace(
            Assembly=SimpleNamespace(Plan=RejectedPlan)
        ),
        "retained-plan": SimpleNamespace(
            Assembly=SimpleNamespace(Plan=RetainedPlan)
        ),
    }

    Pruned, RejectedFingerprints = (
        PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses(
            Frontier,
            (Clause,),
        )
    )

    assert tuple(Pruned) == ("retained-plan",)
    assert RejectedFingerprints == ("rejected-plan",)


def test_retained_global_scheduler_prunes_live_clauses_before_selection():
    Source = inspect.getsource(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels
    )
    Rebuild = Source.index("def RebuildFrontierDeferrals()")
    Prune = Source.index(
        "PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses(",
        Rebuild,
    )
    Select = Source.index(
        "SelectNextRetainedPhysicalGlobalPlan(",
        Prune,
    )
    FreshOrRetained = Source.index(
        "def SelectFreshOrRetainedAssembly()",
        Select,
    )
    Recheck = Source.index(
        "RebuildFrontierDeferrals()",
        FreshOrRetained,
    )

    assert Rebuild < Prune < Select < FreshOrRetained < Recheck


def test_global_contract_recommendation_rejects_uncertified_mixed_factors():
    Recommendation = SelectPhysicalComponentGlobalContractRecommendation(
        _MixedPhysicalCorridorDomains(),
        ("A", "B"),
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
    )

    assert Recommendation is None


def test_global_contract_recommendation_mixes_one_certified_family():
    Domains = tuple(
        replace(
            Domain,
            PortableRequestFamilyFingerprint="portable-family",
        )
        for Domain in _MixedPhysicalCorridorDomains()
    )
    Recommendation = SelectPhysicalComponentGlobalContractRecommendation(
        Domains,
        ("A", "B"),
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        PortableRequestFamilyFingerprint="portable-family",
    )

    assert Recommendation is not None
    assert {
        Signal: Factor.PortGlobalContractFingerprint
        for Signal, Factor in Recommendation.items()
    } == {
        "A": "global-a-1",
        "B": "global-b-2",
    }


def test_global_contract_recommendation_rejects_forbidden_mixed_tuple():
    Domains = tuple(
        replace(
            Domain,
            PortableRequestFamilyFingerprint="portable-family",
        )
        for Domain in _MixedPhysicalCorridorDomains()
    )
    Recommendation = SelectPhysicalComponentGlobalContractRecommendation(
        Domains,
        ("A", "B"),
        RejectedSets=(frozenset((
            ("A", "global-a-1"),
            ("B", "global-b-2"),
        )),),
        CompatibilityCache={},
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        PortableRequestFamilyFingerprint="portable-family",
    )

    assert Recommendation is not None
    assert {
        Signal: Factor.PortGlobalContractFingerprint
        for Signal, Factor in Recommendation.items()
    } == {
        "A": "global-a-2",
        "B": "global-b-1",
    }


def test_complete_global_cut_without_pair_dependency_proof_records_joint_tuple():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="whole-assignment",
        Ports=tuple(
            SimpleNamespace(
                Signal=Signal,
                ReservationFingerprint=f"reservation-{Signal.lower()}",
                GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
            )
            for Signal in ("PortA", "PortB", "PortC")
        ),
    )
    Failure = ClassifyPhysicalComponentGlobalPlanningFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="PhysicalComponentGlobalAssignmentDomain",
            AffectedNets=("PortA", "PortB", "PortC"),
            Diagnostics={
                "GlobalPlanDomainComplete": True,
                "CompleteAssignmentCutProof": True,
                # These are route-candidate conflicts under the full plan,
                # not a proof that either port pair is independently
                # infeasible under every assignment of the third port.
                "ConflictGraph": {
                    "PairwiseIncompatibleEdges": [
                        ["PortA", "PortB"],
                        ["PortA", "PortC"],
                    ],
                },
            },
        ),
        Plan,
        DeadlineExpired=False,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )

    Diagnostics = RecordPhysicalComponentGlobalPlanNoGood(
        Failure,
        Plan,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "exact-assembly-port-aperture-set"
    )
    assert not Diagnostics[
        "PairwisePortReservationNoGoodProofComplete"
    ]
    GlobalKeys = {
        Port.Signal: BuildPhysicalPortApertureContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", GlobalKeys["PortA"]),
            ("PortB", GlobalKeys["PortB"]),
            ("PortC", GlobalKeys["PortC"]),
        )),
    }
    assert Diagnostics["NoGoodReservationSets"] == []


def test_global_port_replans_route_and_bind_each_exact_corridor_contract():
    Reservation = inspect.getsource(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels
    )

    assert "PreparePhysicalComponentGlobalPlanningPlacement(" in Reservation
    assert (
        "Context.InterfaceResources.PreparingPhysicalComponentGlobalChannels = True"
        in Reservation
    )
    assert "Context.Services.RoutePcbDesign(" in Reservation
    assert "BindPhysicalComponentAssemblyGlobalChannels(" in Reservation
    assert "CurrentAssembly" in Reservation


def test_authoritative_global_reservation_precedes_closed_component_compile():
    Source = _Sources(
        PlacementPhysicalAssembly.AdmitSymbolicLocalCapacity,
        PlacementPhysicalFlow.RunPhysicalComponentFlow,
    )
    ReserveCall = Source.index(
        "ReserveAuthoritativeGlobalChannels(Context, Context.PreparedAssembly)"
    )
    Compile = Source.index(
        "ComponentSolve = CompileClosedComponent(",
        ReserveCall,
    )

    assert "def ReserveAuthoritativeGlobalChannels(" in inspect.getsource(
        PlacementPhysicalAssembly.ReserveAuthoritativeGlobalChannels
    )
    assert ReserveCall < Compile
    CapacityProof = Source.index(
        "ProveClosedComponentSymbolicCapacityEligibility("
    )
    assert CapacityProof < ReserveCall < Compile
    assert "if Proof.Status == 'capacity-feasible'" in Source


def test_foreign_portal_certificates_cover_preencoded_assignments():
    Source = inspect.getsource(
        AuthoritativeAssignmentPreparation.RunAssignmentPreparation
    )
    Assignment = Source.index("def PlanAssignment(")
    Publish = Source.index(
        "PublishPhysicalGlobalForeignPortalCandidateNoGoods(State.CandidatesBySignal)",
        Assignment,
    )
    ValuesBranch = Source.index("if Values is None:", Assignment)

    assert Assignment < Publish < ValuesBranch
    assert "PhysicalForeignPortalCertifiedCandidateIds" in Source
    assert (
        "PhysicalForeignPortalCandidateCertificatesCompiled"
        not in Source
    )
    NativeAssignment = Source.index(
        "def PlanNative(",
        ValuesBranch,
    )
    ExactEmptyReturn = Source.index(
        "['NativeAssignmentSkipped'] = True",
        Publish,
    )
    assert Publish < ExactEmptyReturn < NativeAssignment


def test_component_global_domains_close_before_native_assignment():
    Source = inspect.getsource(
        AuthoritativeAssignmentPreparation.RunAssignmentPreparation
    )
    Completion = Source.index(
        "['PhysicalGlobalPreAssignmentDomainCompletion']"
    )
    AssignmentStart = Source.index(
        'State.AssignmentStarted = Services.monotonic()',
        Completion,
    )
    InitialAssignment = Source.index(
        "State.Result = State.PlanAssignment(",
        AssignmentStart,
    )

    assert Completion < AssignmentStart < InitialAssignment


def test_foreign_portal_unary_empty_core_precedes_binary_certificates():
    Source = inspect.getsource(
        AuthoritativeAssignmentPreparation.RunAssignmentPreparation
    )
    EmptyCore = Source.index(
        "if not IndependentEmptyCandidateDomainSignals:"
    )
    BinaryLoop = Source.index(
        "for FirstIndex, First in enumerate(OrderedCandidates):",
        EmptyCore,
    )
    Telemetry = Source.index(
        "'BinaryCompilationSkippedForUnaryEmptyCore'",
        BinaryLoop,
    )

    assert EmptyCore < BinaryLoop < Telemetry


def test_pair_certificate_edges_are_scoped_to_component_ports():
    Source = inspect.getsource(
        RecordPhysicalComponentGlobalPlanNoGood
    )

    assert "str(Edge[0]) in ReservationKeyBySignal" in Source
    assert "str(Edge[1]) in ReservationKeyBySignal" in Source


def test_local_unsat_rejects_exact_plan_before_distinct_global_replan():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Compile = Source.index("CompileClosedComponent(")
    RecordNoGood = Source.index(
        "RecordPhysicalComponentLocalCompilationNoGood(", Compile
    )
    Replan = Source.index(
        "ReplanPhysicalAssemblyWithTiming(Context)", RecordNoGood
    )
    Reserve = Source.index(
        "ReserveAuthoritativeGlobalChannels(", Replan
    )
    Continue = Source.index("continue", Reserve)
    LocalFailure = Source[Compile:Continue]

    assert Compile < RecordNoGood < Replan < Reserve < Continue
    assert (
        "RejectedPhysicalComponentAssemblyPlanFingerprints"
        in LocalFailure
    )
    assert "'PerSignalReservationFeedbackUsed': False" in LocalFailure
    assert "ProveGlobalRelaxedLocalUnsatisfiability(" not in LocalFailure
    assert "CertifyLocalInterfaceFactorPortfolio(" not in LocalFailure
    assert "PhysicalAssemblyGlobalRouteCanBeRebound(" not in LocalFailure


def test_bound_local_compiles_once_per_physical_assembly_plan():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Guard = Source.index(
        "Context.CompiledPhysicalAssemblyPlanFingerprints: set[str] = set()"
    )
    Duplicate = Source.index(
        "Stage='DuplicateClosedComponentCompilation'",
        Guard,
    )
    Add = Source.index(
        "Context.CompiledPhysicalAssemblyPlanFingerprints.add(",
        Duplicate,
    )
    Compile = Source.index(
        "ComponentSolve = CompileClosedComponent(",
        Add,
    )

    assert Guard < Duplicate < Add < Compile
    assert "ActiveComponentRemainingSeconds" in Source[Add:Compile + 500]


def test_incomplete_local_compile_stops_before_exact_plan_replan():
    Source = inspect.getsource(PlacementPhysicalFlow.RunPhysicalComponentFlow)
    Compile = Source.index("CompileClosedComponent(")
    Incomplete = Source.index(
        "'ClosedComponentCompilationIncomplete'", Compile
    )
    RecordNoGood = Source.index(
        "RecordPhysicalComponentLocalCompilationNoGood(", Incomplete
    )
    Replan = Source.index(
        "ReplanPhysicalAssemblyWithTiming(Context)", RecordNoGood
    )
    Guard = Source[Incomplete:RecordNoGood]

    assert Compile < Incomplete < RecordNoGood < Replan
    assert "raise RoutingStageError(RoutingFailure(" in Guard
    assert "'Complete': False" in Guard
    assert "ReplanPhysicalAssemblyWithTiming(" not in Guard


def test_physical_guide_overlay_preserves_complete_ordinary_plan_coverage():
    Source = inspect.getsource(
        AuthoritativeGuidePlanning.RunGuidePlanning
    )
    OverlayStart = Source.index(
        "FrozenAxes = dict("
    )
    OverlayEnd = Source.index(
        "State.WorkTelemetry['GlobalGuidePlanCacheHit']",
        OverlayStart,
    )
    Overlay = Source[OverlayStart:OverlayEnd]

    OrdinaryGuides = Overlay.index("**dict(State.CoarsePlan.Guides)")
    PhysicalGuides = Overlay.index(
        "Channel.Signal: frozenset(Channel.GuideCells)",
        OrdinaryGuides,
    )
    OrdinaryLayers = Overlay.index("**dict(State.CoarsePlan.Layers)")
    PhysicalLayers = Overlay.index(
        "Channel.Signal: int(Channel.Layer)",
        OrdinaryLayers,
    )

    assert OrdinaryGuides < PhysicalGuides
    assert OrdinaryLayers < PhysicalLayers
    assert Overlay.count(
        "if Channel.Signal in PhysicalAssemblyPortSignalsForGuide"
    ) == 2
    assert "State.CoarsePlan = FrozenPhysicalComponentGuidePlan" not in Overlay


def test_prepared_solve_preserves_typed_deadline_and_domain(monkeypatch):
    Preparation = SimpleNamespace(
        DomainFingerprint="prepared-domain",
    )

    def Expire(*_Args, **_KeywordArgs):
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.RuntimeBudgetExceeded,
            Stage="PhysicalComponentAssembly",
            Detail="shared routing deadline expired",
            Diagnostics={"PortAssignmentExpansionCount": 164210},
        ))

    monkeypatch.setattr(
        PhysicalPortSolving,
        "SolvePreparedPhysicalComponentPortFactorDomain",
        Expire,
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
    )
    Deadline = SimpleNamespace(RaiseIfExpired=lambda *_Args: None)

    with pytest.raises(RoutingStageError) as Raised:
        SolvePreparedPhysicalComponentEligibility(
            Preparation,
            Resources=Resources,
            Deadline=Deadline,
        )

    Failure = Raised.value.Failure
    assert Failure.Reason == (
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete
    )
    assert Failure.Stage == "PhysicalComponentAssemblyIncomplete"
    assert Failure.Diagnostics["DomainFingerprint"] == "prepared-domain"
    assert Failure.Diagnostics["PreparedFactorDomainReused"] is True
    assert Failure.Diagnostics[
        "PhysicalComponentAssemblyClassification"
    ]["Operation"] == "solve-prepared-eligibility"


def test_replan_reuses_retained_factor_domain(monkeypatch):
    Preparation = SimpleNamespace(DomainFingerprint="same-domain")
    Resources = SimpleNamespace(
        PreparedPhysicalComponentPortFactorDomain=Preparation,
        PreparedComponentRoutingProblem=None,
        PreparedPhysicalComponentAssembly=None,
        FrozenPhysicalComponentAssemblyPlan=None,
    )
    Assembly = SimpleNamespace(
        Problem=object(),
        Plan=SimpleNamespace(PlanFingerprint="next-plan"),
    )
    Seen = []

    def Solve(
        Value,
        *,
        Resources,
        Deadline,
        DeferLocalCompositeSelection=True,
        RequiredBoundaryPorts=None,
    ):
        assert DeferLocalCompositeSelection
        assert RequiredBoundaryPorts is None
        Seen.append(Value)
        return Assembly

    monkeypatch.setattr(
        RoutingPcb,
        "SolvePreparedPhysicalComponentEligibility",
        Solve,
    )
    Result = ReplanPhysicalComponentAssembly(
        SimpleNamespace(),
        Resources=Resources,
        Deadline=SimpleNamespace(),
    )

    assert Result is Assembly
    assert Seen == [Preparation]
    assert Resources.PreparedPhysicalComponentPortFactorDomain is Preparation
    assert Resources.FrozenPhysicalComponentAssemblyPlan is Assembly.Plan


def test_global_planning_classifier_preserves_explicit_domain_proof():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="PhysicalComponentGlobalAssignmentDomain",
        AffectedNets=("A", "B"),
        Diagnostics={
            "GlobalPlanDomainComplete": True,
            "CompleteAssignmentCutProof": True,
            "EscalationHistory": (),
        },
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        SimpleNamespace(PlanFingerprint="physical-plan"),
        DeadlineExpired=False,
    )

    assert Classified.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Classified.Diagnostics["GlobalPlanDomainComplete"] is True
    assert Classified.Diagnostics["UnderlyingEscalationHistory"] == []
    assert Classified.Diagnostics["ExecutableLegacyRepairCascade"] is False
    Cut = RoutingAssignmentCut.FromFailure(Classified)
    assert Cut is not None
    assert Cut.CompleteAssignmentCutProof is True
    assert Cut.ConflictSignals == ("A", "B")


def test_global_mandatory_cut_skips_unrelated_port_reassignment():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="GeneratedPortalCapacityCertification",
        AffectedNets=("ForeignA", "ForeignB"),
        Diagnostics={
            "MandatoryAccessProof": {
                "Kind": "generated-fixed-portal-domain-exhausted",
                "Complete": True,
                "PortalTupleDomainComplete": True,
                "ProofScope": "complete-portal-tuple-domain",
                "BudgetExhausted": False,
                "DeadlineExceeded": False,
            },
            "ConflictGraph": {
                "Classification": "mandatory-boundary-capacity-cut",
                "ConflictSignals": ["ForeignA", "ForeignB"],
                "PairwiseIncompatibleEdges": [
                    ["ForeignA", "ForeignB"],
                ],
            },
        },
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(
            Signal="ComponentPort",
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Diagnostics["PlanIndependentMandatoryCut"] is True
    assert Classified.Diagnostics[
        "AssemblyPlanReassignmentAllowed"
    ] is False


def test_complete_non_port_global_cut_skips_port_reassignment():
    Failure = RoutingFailure(
        Reason=(
            RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
        ),
        Stage="PhysicalComponentGlobalPlanning",
        AffectedNets=("OrdinaryA", "OrdinaryB"),
        Diagnostics={"GlobalPlanDomainComplete": True},
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(
            Signal="ComponentPort",
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Diagnostics["PlanIndependentGlobalCut"] is True
    assert Classified.Diagnostics[
        "AssemblyPlanReassignmentAllowed"
    ] is False


def test_global_mandatory_cut_includes_graph_dependency_signals():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="GeneratedPortalCapacityCertification",
        AffectedNets=("ForeignA",),
        Diagnostics={
            "MandatoryAccessProof": {
                "Kind": "generated-fixed-portal-domain-exhausted",
                "Complete": True,
                "PortalTupleDomainComplete": True,
                "ProofScope": "complete-portal-tuple-domain",
                "BudgetExhausted": False,
                "DeadlineExceeded": False,
            },
            "ConflictGraph": {
                "Classification": "mandatory-boundary-capacity-cut",
                "ConflictSignals": ["ForeignB"],
                "CongestionCutSignals": ["ComponentPort"],
            },
        },
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(
            Signal="ComponentPort",
            GlobalClaims=SimpleNamespace(ResourceIds=frozenset()),
        ),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.AffectedNets == (
        "ComponentPort",
        "ForeignA",
        "ForeignB",
    )
    assert Classified.Diagnostics[
        "AssemblyPlanDependencySignals"
    ] == ["ComponentPort", "ForeignA", "ForeignB"]
    assert Classified.Diagnostics["ConflictGraph"][
        "ConflictSignals"
    ] == ["ComponentPort", "ForeignA", "ForeignB"]
    assert Classified.Diagnostics["PlanIndependentMandatoryCut"] is False
    assert Classified.Diagnostics[
        "AssemblyPlanReassignmentAllowed"
    ] is True


def test_bounded_fixed_portal_sample_cannot_claim_complete_global_domain():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="InitialCandidateAssignment",
        AffectedNets=("OrdinaryA", "OrdinaryB"),
        Diagnostics={
            "GlobalPlanDomainComplete": True,
            "CompleteAssignmentCutProof": True,
            "MandatoryAccessProof": {
                "Kind": "generated-fixed-portal-domain-exhausted",
                "Complete": True,
                "BudgetExhausted": False,
                "DeadlineExceeded": False,
            },
        },
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(Signal="ComponentPort"),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Reason == (
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete
    )
    assert Classified.Diagnostics["GlobalPlanDomainComplete"] is False
    assert Classified.Diagnostics[
        "AmbiguousFixedPortalProofRejected"
    ] is True


def test_exact_global_cut_contains_only_ports_and_feedthroughs():
    def Claims(Wires):
        return RoutingResourceClaims(
            WireCells=frozenset(Wires),
            ElectricalCells=frozenset(Wires),
        )

    def Channel(Signal, Wires, Feedthroughs=()):
        return SimpleNamespace(
            Signal=Signal,
            Claims=Claims(Wires),
            FeedthroughComponentIds=Feedthroughs,
        )

    Channels = (
        Channel("Port", {(0, 1, 0)}),
        Channel("Feed", {(5, 1, 0)}, ("component",)),
        Channel("Declared", {(7, 1, 0)}),
        Channel("ConflictA", {(10, 1, 0)}),
        Channel("ConflictB", {(10, 1, 0)}),
        Channel("Ordinary", {(20, 1, 0)}),
    )
    Plan = SimpleNamespace(
        Ports=(SimpleNamespace(Signal="Port"),),
        Channels=(),
        Corridors=Channels,
        PlanningChannels=Channels,
        Feedthroughs=(SimpleNamespace(Signal="Declared"),),
        DeclaredFeedthroughSignals=frozenset(("Declared",)),
    )

    assert SelectPhysicalComponentExactGlobalChannelSignals(Plan) == {
        "Feed",
        "Declared",
        "Port",
    }


def test_exact_global_preparation_excludes_unowned_corridors_from_base_claims():
    Source = inspect.getsource(
        AuthoritativeAssignmentPreparation.RunAssignmentPreparation
    )
    IndexStart = Source.index(
        "def EnsurePhysicalAssignmentIndexComplete()"
    )
    IndexEnd = Source.index(
        "EnsurePhysicalAssignmentIndexComplete()",
        IndexStart + len(
            "def EnsurePhysicalAssignmentIndexComplete()"
        ),
    )
    IndexPreparation = Source[IndexStart:IndexEnd]
    BaseStart = Source.index("if BaseValues is None:", IndexEnd)
    BaseEnd = Source.index("if BaseValues:", BaseStart)
    BasePreparation = Source[BaseStart:BaseEnd]
    Assignment = Source[BaseEnd:Source.index(
        "def RaiseForNativeAssignmentDeadline",
        BaseEnd,
    )]

    assert "State.PhysicalAssemblyPlan.PlanningChannels" not in IndexPreparation
    assert "Channel.Claims" not in IndexPreparation
    assert "State.AssignmentIndexed.EncodeClaims(Channel.Claims)" not in (
        BasePreparation
    )
    assert "ExactPhysicalSignals" not in BasePreparation
    assert "PlanAuthoritativeRoutesWithBaseBounded" in Assignment
    assert "PlanAuthoritativeRoutesWithBase(" in Assignment


def test_global_complete_nonmandatory_proof_without_port_skips_reassignment():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.TrackAssignmentConflict,
        Stage="InitialCandidateAssignment",
        AffectedNets=("ForeignA", "ForeignB"),
        Diagnostics={
            "MandatoryAccessProof": {
                "Kind": "ordinary-route-domain-exhausted",
                "Complete": True,
                "BudgetExhausted": False,
                "DeadlineExceeded": False,
            },
            "ConflictGraph": {
                "Classification": "detailed-congestion-cut",
                "ConflictSignals": ["ForeignA", "ForeignB"],
            },
        },
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        Ports=(SimpleNamespace(Signal="ComponentPort"),),
    )

    Classified = ClassifyPhysicalComponentGlobalPlanningFailure(
        Failure,
        Plan,
        DeadlineExpired=False,
    )

    assert Classified.Diagnostics["GlobalPlanDomainComplete"] is True
    assert Classified.Diagnostics["PlanIndependentMandatoryCut"] is False
    assert Classified.Diagnostics[
        "AssemblyPlanReassignmentAllowed"
    ] is False

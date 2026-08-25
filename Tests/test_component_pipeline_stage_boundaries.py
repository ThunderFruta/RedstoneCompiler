import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest

from Compiler.Placement.PcbFlow import (
    BuildPhysicalGlobalPlanResumeCursorFromDiagnostics,
    BuildComponentRoutabilityCore,
    BuildCapacityRepairGeometryFingerprint,
    BuildPhysicalLocalFactorDiversificationCore,
    BuildPhysicalOwnedFrontierTopologyRepairCore,
    BuildPhysicalInterfaceRepairCore,
    BuildSymbolicCapacityRepairEvidence,
    PreparedEligibilityHasDisjointCapacitySeams,
    BuildPhysicalComponentPlacementFeedback,
    ClassifyPhysicalGlobalPlanRetentionAdmission,
    IsClusterInterfaceStateIncomplete,
    IsCompletePhysicalAssemblyUnsatisfiable,
    SummarizePreRouteAccessFabric,
    _PlaceAndRoutePcbWithPolicy,
)
import Compiler.Routing.AuthoritativePlanner as AuthoritativePlanner
import Compiler.Routing.Pcb as Pcb
from Compiler.Routing.ComponentPipeline import (
    BuildPhysicalComponentAssemblyChoiceFingerprint,
    BuildPhysicalComponentAssemblyPlanDomainFingerprint,
    BuildPhysicalAssemblyGlobalReuseFingerprint,
    BuildPhysicalGlobalPlanCutFamilyFingerprint,
    BuildPhysicalGlobalPlanDependencyFingerprint,
    BuildPhysicalPortApertureContractFingerprint,
    BuildPhysicalPortGlobalContractFingerprint,
    BuildPhysicalPortLocalContractFingerprint,
    BuildPhysicalPortSeamContractFingerprint,
    BuildPhysicalRequestAperturePortNoGood,
    ClassifyPhysicalComponentGlobalPlanningFailure,
    RecordPhysicalComponentGlobalPlanNoGood,
    RecordPhysicalComponentSymbolicCapacityEligibilityNoGood,
    PreservePhysicalComponentAssemblyPlanDomainContinuation,
    ProjectCompletePhysicalPortPairCertificateToApertureClauses,
    PhysicalAssemblyGlobalRouteCanBeRebound,
    PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses,
    RecordPhysicalComponentDetailedRoutingNoGood,
    SelectContractIndependentOwnedSignalFrontierUnsatCore,
    SelectPhysicalComponentGlobalContractRecommendation,
    SelectPhysicalComponentExactGlobalChannelSignals,
)


def test_closed_region_portals_replace_discovery_domain_before_consumers():
    Source = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    Publish = Source.index(
        "RawPortalEntries = EffectiveRawPortalCache.PortalEntries"
    )
    Dictionary = Source.index(
        "RawPortals = EffectiveRawPortalCache.BuildPortalDictionary()",
        Publish,
    )
    Consumer = Source.index(
        "if PrepareComponentRoutingProblemOnly:",
        Dictionary,
    )

    assert Publish < Dictionary < Consumer


def test_single_component_selection_freezes_raw_tracks_before_route():
    """A compact portfolio has one raw selector and no post-selection solve."""
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    RawDomain = Source.index(
        "RawDomain = PrepareRawTrackAssignmentDomain("
    )
    Selection = Source.index(
        "SolveRawTrackAssignmentPortfolioWithContext(",
        RawDomain,
    )
    FrozenPreparation = Source.index(
        "SelectedTrackPreparation = RawTrackAssignmentResult.Preparation",
        Selection,
    )
    MultiComponentPreparation = Source.index(
        "Preparation = PrepareTrackAssignment("
    )
    LegacyPreparation = Source.index(
        "SelectedTrackPreparation = PrepareTrackAssignment(",
        FrozenPreparation,
    )
    FirstRoute = Source.index("RoutePcbDesign(", LegacyPreparation)

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
    assert "if SelectedTrackPreparation is None:" in Source[
        FrozenPreparation:LegacyPreparation
    ]
    assert LegacyPreparation < FirstRoute


def test_success_publishes_authoritative_selection_fingerprint():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    RawFingerprint = Source.index(
        "RawTrackAssignmentResult.SelectionFingerprint"
    )
    InterfaceFingerprint = Source.index(
        "else PreRouteInterfaceResult.SelectionFingerprint",
        RawFingerprint,
    )
    Publication = Source.index(
        'RoutingControlEffectiveness["CandidateFingerprint"]',
        InterfaceFingerprint,
    )

    assert RawFingerprint < InterfaceFingerprint < Publication


def test_multi_component_missing_access_assignment_uses_frozen_track_witness():
    """Legacy components must not be mislabeled missing small-design fabric."""
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    MissingAssignment = Source.index("if AccessAssignment is None:")
    Preparation = Source.index(
        "Preparation = PrepareTrackAssignment(",
        MissingAssignment,
    )
    StoredWitness = Source.index(
        "PrePlacementTrackPreparationWitnesses[",
        Preparation,
    )
    PublishedWitness = Source.index(
        "PrePlacementTrackPreparationWitnesses.get(",
        StoredWitness,
    )
    SelectedWitness = Source.index(
        "SelectedTrackPreparation = (",
        PublishedWitness,
    )
    DefensiveFallback = Source.index(
        "if SelectedTrackPreparation is None:",
        SelectedWitness,
    )

    assert "missing-access-assignment" not in Source[
        MissingAssignment:Preparation
    ]
    assert Preparation < StoredWitness < PublishedWitness < SelectedWitness
    assert SelectedWitness < DefensiveFallback


def test_single_component_defers_derived_fabric_until_raw_materialization():
    """A declared shell ranks first; its escape search is not eager work."""
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Shell = Source.index("Shell = BuildDerivedPerimeterFabricShell(")
    Descriptor = Source.index("PreRouteFabricDescriptorsByCandidateId[")
    DeferredCandidate = Source.index(
        "FabricCandidateRecords.append(DescriptorCandidate)",
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ExactGate = Source.index("ExactClusterInterfaceSolveEnabled = (")
    DeferredAlternatives = Source.index(
        "HasRemainingPlacementAlternative = (",
        ExactGate,
    )
    SingleAttemptSlots = Source.index(
        "PlannedRoutingSlots = (",
        DeferredAlternatives,
    )
    Route = Source.index("RoutePcbDesign(", SingleAttemptSlots)

    ExactGateSource = Source[ExactGate:DeferredAlternatives]
    RouteBudgetSource = Source[DeferredAlternatives:Route]
    assert "not SinglePackedComponent" in ExactGateSource
    assert "False\n            if SinglePackedComponent" in RouteBudgetSource
    assert "1\n            if SinglePackedComponent" in RouteBudgetSource


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
from Compiler.Routing.Models import (
    ComponentRoutingSolveResult,
    PhysicalGlobalPlanResumeCursor,
    PhysicalPortCorridorDomain,
    PhysicalPortCorridorFactor,
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
    assert isinstance(Calls[0]["Deadline"], RoutingDeadline)


def test_prepare_raw_track_assignment_domain_stops_before_assignment(
    monkeypatch: pytest.MonkeyPatch,
):
    """The portfolio bridge exports values, not a second native solve."""
    Position = (1, 1, 1)
    Expected = AuthoritativePlanner.RawTrackAssignmentDomain(
        ResourcePositions=(Position,),
        Values=(AuthoritativePlanner.RawTrackAssignmentValue(
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
        raise AuthoritativePlanner.RawTrackAssignmentDomainPrepared(
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
        AuthoritativePlanner.SolvePreparedPhysicalComponentPortFactorDomain
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
        AuthoritativePlanner.SolvePreparedPhysicalComponentPortFactorDomain
    )

    assert "IncludeLocalCompositeFactors=True" in Source
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Start = Source.index("ComponentSolve = CompileClosedComponent(")
    End = Source.index("assert ComponentSolve.Template is not None", Start)
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
    assert "PerSignalReservationFeedbackUsed\": False" in LocalCompilation
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Start = Source.index(
        '"detailed-failure-reject-physical-plan"',
    )
    End = Source.index(
        "PreparedAssembly = (",
        Start,
    )
    Rejection = Source[Start:End]

    assert "RecordPhysicalComponentDetailedRoutingNoGood(" in Source[:Start]
    assert "RejectedPhysicalComponentPortAssignmentFingerprints" not in (
        Rejection
    )
    assert "RejectedPortAssignmentFingerprint" not in Rejection


def test_local_compilation_requires_explicit_admission_without_floor():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Start = Source.index("ActiveComponentDeadline = SharedInterfaceDeadline")
    Compile = Source.index("ComponentSolve = CompileClosedComponent(", Start)
    End = Source.index("if not ComponentSolve.Feasible:", Compile)
    Admission = Source[Start:Compile]
    Invocation = Source[Compile:End]

    assert "BuildLocalComponentCompilationAdmissionFailure(" in Admission
    assert "ActiveComponentRemainingSeconds <= 0" in Admission
    assert "DeadlineSeconds=(" in Invocation
    assert "ActiveComponentRemainingSeconds" in Invocation
    assert "max(" not in Invocation


def test_admitted_local_compilation_is_not_reclassified_by_planning_clock():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Compile = Source.index("ComponentSolve = CompileClosedComponent(")
    Result = Source.index("if not ComponentSolve.Feasible:", Compile)
    Template = Source.index(
        "assert ComponentSolve.Template is not None",
        Result,
    )
    Classification = Source[Result:Template]

    assert "InterfaceDeadline.IsExpired()" not in Classification
    assert 'Stage=(\n                                    "ClosedComponentCompilationIncomplete"' in Classification
    assert "RecordPhysicalComponentLocalCompilationNoGood(" in Classification


def test_physical_planning_uses_planning_clock_until_bound_handoff():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Schedule = Source.index("BuildClusterInterfaceStageSchedule(")
    PlanningDeadline = Source.index(
        "SharedInterfacePlanningDeadline = RoutingDeadline(",
        Schedule,
    )
    StateDeadline = Source.index(
        "InterfaceDeadline = SharedInterfacePlanningDeadline",
        PlanningDeadline,
    )
    Admission = Source.index(
        "if InterfaceDeadline.IsExpired():",
        StateDeadline,
    )
    Preparation = Source.index(
        "PreparePhysicalComponentEligibility(",
        Admission,
    )

    assert Schedule < PlanningDeadline < StateDeadline < Admission < Preparation
    Selection = Source[StateDeadline:Admission]
    assert "InterfaceDeadline = SharedInterfacePlanningDeadline" in Selection
    assert "SharedInterfaceDeadline" not in Selection
    assert "if RetainedPlacementFingerprint in (" in Selection


def test_stage_specific_incomplete_failures_preserve_handoff_identity():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
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
    assert '"ClosedComponentCompilationIncomplete"' in AfterCompile
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
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    Problem = Source.index("PreparedAccessProblem = BuildComponentRoutingProblem(")
    Callback = Source.index(
        "UnboundOwnedSignalFrontierProofCallback(",
        Problem,
    )
    Access = Source.index(
        "BuildComponentCutAccessFeasibilityCertificate(",
        Callback,
    )
    Factors = Source.index(
        "Preparation = PreparePhysicalComponentPortFactorDomain(",
        Callback,
    )

    assert Problem < Callback < Access < Factors


def test_unbound_frontier_failure_exports_minimal_placement_core():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Callback = Source.index("def ProveUnboundOwnedSignalFrontier(")
    Preparation = Source.index(
        "PreparedEligibility = PreparePhysicalComponentEligibility(",
        Callback,
    )
    CallbackSource = Source[Callback:Preparation]

    assert '"PortAssignmentUnsatCoreMinimal": True' in CallbackSource
    assert '"PortAssignmentUnsatCoreSignals": list(' in CallbackSource
    assert '"PortAssignmentUnsatCoreFingerprint": (' in CallbackSource


def test_owned_terminal_portals_precede_unbound_frontier_and_global_portals():
    Source = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    OwnedBatch = Source.index(
        '"PhysicalOwnedTerminalPortalEligibility"',
    )
    OwnedProblem = Source.index(
        "PreparedAccessProblem = BuildComponentRoutingProblem(",
        OwnedBatch,
    )
    Callback = Source.index(
        "UnboundOwnedSignalFrontierProofCallback(",
        OwnedProblem,
    )
    GlobalBatch = Source.index(
        "GeneratePortalRequestBatch(\n                PortalRequests,",
        Callback,
    )
    RawCache = Source.index(
        "EffectiveRawPortalCache = RawPortalGeometryCache(",
        GlobalBatch,
    )

    assert OwnedBatch < OwnedProblem < Callback < GlobalBatch < RawCache


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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    assert "duplicate-component-selection-proof-reused" in Source
    assert "and Proof.Exhaustive" in Source
    assert "ExpectedComponentStateFingerprints=tuple(sorted(" in Source
    assert "PlacementPortfolioDomainComplete=(" in Source
    PortfolioGuard = Source.index(
        '"ClusterInterfacePlacementPortfolioIncomplete"',
    )
    DomainGuard = Source.index(
        '"ClusterInterfaceComponentStateDomainIncomplete"',
        PortfolioGuard,
    )
    assert PortfolioGuard < DomainGuard
    assert '"ArchitecturalUnsatisfiabilityProven": False' in Source


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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Exhaustive = Source.index(
        "if (\n"
        "                    StateExhaustive and not StateIncomplete\n"
        "                )"
    )
    Advance = Source.index(
        "if ComponentAccessCoreSignals:",
        Exhaustive,
    )
    Reorder = Source.index(
        "ReorderRemainingPlacementsForAccessCore(",
        Advance,
    )

    assert "InterfaceDeadline.IsExpired()" not in Source[Advance:Reorder]


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


def test_capacity_repair_precheck_defers_dense_boundary_lease_only():
    FlowSource = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    PrepareCall = FlowSource.index(
        "PreparedEligibility = PreparePhysicalComponentEligibility("
    )
    PrepareSource = FlowSource[PrepareCall:PrepareCall + 1200]
    PlannerSource = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    PcbSource = inspect.getsource(Pcb.PreparePhysicalComponentEligibility)

    assert "DeferClusterBoundaryLeaseUntilCapacityPrecheck=(" in PrepareSource
    assert "CapacityRepairConstraint is not None" in PrepareSource
    assert "and not DeferClusterBoundaryLeaseUntilCapacityPrecheck" in (
        PlannerSource
    )
    assert "deferred-for-capacity-repair-precheck" in PlannerSource
    DeferredLease = PlannerSource.index(
        '"deferred-for-capacity-repair-precheck"'
    )
    assert "PortalReservations = ()" in PlannerSource[
        DeferredLease:DeferredLease + 500
    ]
    assert "DeferClusterBoundaryLeaseUntilCapacityPrecheck=(" in PcbSource


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
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalSymbolicCapacityPlacementFeedback",
        Diagnostics={
            "SymbolicCapacityPlacementFeedback": True,
            "PlacementInterfacePressureSignals": ["Alpha", "Beta"],
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
    # Symbolic pair evidence may guide local replanning, but it is not yet
    # an exact assembly/channel repair core.
    assert Constraint is None
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


def test_complete_capacity_feedback_advances_queued_repair_placement():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
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
    assert "or CompleteSymbolicCapacityPlacementFeedback" in Feedback
    assert "and PlacementAdvanced" in Feedback
    assert "and not (\n                            Deadline" in Feedback
    assert "GlobalHandoffPlacementAdvanced" in Source
    assert '"SymbolicCapacityPlacementFeedback"' in Source

    DeferredRequestSource = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    assert "AllowCapacityPairRepair=(" in DeferredRequestSource
    assert "CapacityRepairActive" in DeferredRequestSource
    assert "AllowCapacityPairRepair: bool = False" in DeferredRequestSource
    assert "and not AllowCapacityPairRepair" in DeferredRequestSource
    assert "AllowCapacityPairRepair\n                or" in DeferredRequestSource
    assert "AllowCapacityPairRepair=(" in DeferredRequestSource
    assert "PlacementGenerationNotAfter=(" in DeferredRequestSource
    assert "Deadline.ExpiresAt\n                            if" in (
        DeferredRequestSource
    )
    assert 'SourceGenerator="row-beam-conflict-relocation"' in (
        DeferredRequestSource
    )


def test_owned_frontier_topology_repair_regenerates_before_eligibility():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    DeferredRequestSource = Source
    RepairStart = Source.index("def EnqueueOwnedFrontierTopologyRepair(")
    RepairEnd = Source.index("while (", RepairStart)
    Repair = Source[RepairStart:RepairEnd]

    assert "PlacePcbGraph(" in Repair
    assert "CutDrivenClusterRefinementSignals=frozenset(Core.Signals)" in Repair
    assert "BuildRoutingResources(" in Repair
    assert "BuildTransactionalClusterEndpointRepair(" not in Repair
    assert '"prepare-eligibility"' in Repair
    assert "JointPlacementCandidateIndex=TopologyCandidateIndex" in Repair
    assert "TopologyCandidateBaseIndex" in Repair
    assert "TopologyCandidateBaseIndex + TopologyCandidateOffset" in Repair
    assert "owned-frontier-topology-retained-domain-exhausted" in Repair
    assert "RetainedJointPlacementCandidates * 2" in Repair
    assert "EffectiveComponentVariant" in Source
    assert '"relocate-endpoint-cluster"' in Source
    assert "JointPlacementCandidateIndex=Variant" not in Repair
    assert "GlobalComponentStateDomainExhausted" in Source


def test_capacity_repair_requeue_counts_dequeued_channelized_placement():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    DeferredRequestSource = Source
    RequeueStart = Source.index("AttemptedRepairPlacementFingerprints = {")
    RequeueEnd = Source.index("UnattemptedCapacityRepairCandidates", RequeueStart)
    Requeue = Source[RequeueStart:RequeueEnd]

    assert "capacity-pair-repair-dequeued" in Requeue
    assert "bounded-proof-driven-repair-candidate-failed" in Requeue
    assert "DequeuedCapacityRepairPlacementFingerprints" in Source
    assert "capacity-pair-repair-duplicate-dequeue-suppressed" in Source
    assert 'InterfaceWorkPhase == "prepare-eligibility"' in Source
    assert "serial solve work item intentionally reuses this" in Source
    assert "if CapacityRepairConstraint is not None:" in (
        DeferredRequestSource
    )
    assert "CapacityRepairPlacementState = (" in DeferredRequestSource
    assert "and not CapacityRepairPlacementState" in DeferredRequestSource
    assert "PairwiseConflictEdges=(" in DeferredRequestSource
    assert "CapacityRepairConstraint.Signals" in DeferredRequestSource
    assert "CapacityRepairConstraint is None\n                and InheritedCapacityRepairConstraint is not None" in Source
    assert "ClusterInterfacePlacementMaterialization" in (
        DeferredRequestSource
    )
    assert "if CapacityRepairConstraint is not None\n                            else InterfaceDeadline" in (
        DeferredRequestSource
    )
    assert "in CapacityRepairConstraintByPlacementFingerprint" in (
        DeferredRequestSource
    )


def test_interface_repair_preserves_broad_work_and_records_outcomes():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)

    assert '"interface-repair-epoch-started"' in Source
    assert "PreemptedCandidateIds" in Source
    assert "PreemptedCandidateIds\": []" in Source
    assert '"capacity-pair-repair-generated"' in Source
    assert '"capacity-pair-repair-dequeued"' in Source
    assert '"capacity-pair-repair-local-materialized"' in Source
    assert '"capacity-pair-repair-rejected-overlapping-seams"' in Source
    assert '"capacity-repair-witness-reserved"' in Source
    assert '"capacity-repair-csp-admitted"' in Source
    assert '"bounded-proof-driven-repair-candidate-failed"' in Source
    assert '"bounded-proof-driven-repair-exhausted"' in Source
    assert '"PhysicalCapacityRepairPortfolio"' in Source
    assert '"capacity-repair-portfolio-prefetched"' in Source
    assert '"split-relocate"' in Source
    assert '"widen-channel-deck"' in Source
    assert '"split-channel-endpoints"' in Source


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
        AuthoritativePlanner,
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    QueueStart = Source.index('"prepare-eligibility",')
    PhaseOrder = Source.index(
        'if Entry[1][0] == "prepare-eligibility"',
        QueueStart,
    )
    SolveMarker = Source.index(
        'InterfaceCandidateQueue.insert(0, (\n'
        '                        "solve-prepared-eligibility",',
        PhaseOrder,
    )
    SolveCall = Source.index(
        "SolvePreparedPhysicalComponentEligibility(",
        SolveMarker,
    )

    assert QueueStart < PhaseOrder < SolveMarker < SolveCall
    assert "PreparedEligibilityByState[" in Source[PhaseOrder:SolveMarker]


def test_complete_global_plan_failure_replans_before_local_compilation():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index(
        "def ReserveAuthoritativeGlobalChannels("
    )
    ReserveEnd = Source.index(
        "PreparedAssembly, GlobalChannelDesign = (",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]

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
    assert "LocalCompilationEntered\": False" in Reservation
    assert "LocalTemplateReopened\": False" in Reservation


def test_incomplete_global_plan_is_retained_without_recording_a_no_good():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index(
        "def ReserveAuthoritativeGlobalChannels("
    )
    ReserveEnd = Source.index(
        "PreparedAssembly, GlobalChannelDesign = (",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]

    GlobalFailureStart = Reservation.index(
        "except RoutingStageError as GlobalPlanningError:"
    )
    Incomplete = Reservation.index(
        ".PhysicalComponentAssemblyIncomplete",
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
    assert '"NoGoodRecorded": False' in Reservation[Retain:Replan]
    assert '"CursorResumeAvailable": bool(' in Reservation[Retain:Replan]
    assert "RecordPhysicalComponentGlobalPlanNoGood(" not in (
        Reservation[Incomplete:Replan]
    )


def test_incomplete_global_plan_timing_closes_before_next_plan_selection():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index(
        "def ReserveAuthoritativeGlobalChannels("
    )
    ReserveEnd = Source.index(
        "PreparedAssembly, GlobalChannelDesign = (",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]
    Incomplete = Reservation.index(
        ".PhysicalComponentAssemblyIncomplete"
    )
    Retained = Reservation.index(
        'GlobalPlanningAttemptResult = (\n'
        '                                    "incomplete-plan-retained"',
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    CandidateLoop = Source.index(
        "while (\n"
        "            InterfaceCandidateQueue\n"
        "            or PendingProofGuidedPlacementByComponentVariant\n"
        "        ):"
    )
    CacheDeclarations = (
        "ComponentVariantPortfolioCache: dict[Any, Any] = {}",
        "ComponentNetVariantConstructionCache: dict[Any, Any] = {}",
        "ComponentRouteClaimsConstructionCache: dict[Any, Any] = {}",
        "ComponentNetVariantDiscoveryStateCache: dict[Any, Any] = {}",
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
        "RoutingResourcesByRetainedPlacementFingerprint"
        in Source[:CandidateLoop]
    )


def test_frontier_retention_requires_complete_aperture_and_progress():
    CompleteAperture = {
        "DomainFingerprint": "aperture-a",
        "Complete": True,
    }

    Plan = SimpleNamespace(PlanFingerprint="plan-a", Ports=())
    WithoutCursor = (
        AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
        AuthoritativePlanner.RetainIncompletePhysicalGlobalPlan(
            {},
            SimpleNamespace(Plan=Plan),
            WithoutCursor,
            EnqueuedSequence=0,
        )

    with pytest.raises(ValueError, match="no resumable progress"):
        AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
    Resumable = AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
    return AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
        return AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
        AuthoritativePlanner
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
        AuthoritativePlanner
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
        AuthoritativePlanner
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
        AuthoritativePlanner
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
        return AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
        return AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
        AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
    Frontier = AuthoritativePlanner.RetainIncompletePhysicalGlobalPlan(
        {},
        Assembly,
        FirstContinuation,
        EnqueuedSequence=0,
    )
    assert AuthoritativePlanner.ShouldScheduleRetainedPhysicalGlobalPlan(
        Frontier,
        PreviousPlanWasRetained=False,
    )
    Resumed, Frontier = (
        AuthoritativePlanner.SelectNextRetainedPhysicalGlobalPlan(
            Frontier,
            ScheduleSequence=1,
        )
    )
    assert Resumed.Assembly is Assembly
    RefreshCursor = PhysicalGlobalPlanResumeCursor(
        "cursor-8", "plan-a", "aperture-a", 8, object(),
    )
    Refresh = AuthoritativePlanner.BuildPhysicalGlobalPlanContinuationState(
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
    Frontier = AuthoritativePlanner.RetainIncompletePhysicalGlobalPlan(
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
    assert AuthoritativePlanner.ShouldScheduleRetainedPhysicalGlobalPlan(
        Frontier,
        PreviousPlanWasRetained=True,
    )


def test_retained_global_plans_are_serviced_before_another_fresh_plan():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
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
        "ReplanPhysicalAssemblyWithTiming()",
        Retained,
    )

    assert Fairness < Retained < Fresh
    assert "PreviousPlanWasRetained" in Selector


def test_physical_component_pipeline_records_explicit_stage_durations():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)

    assert '"DurationSeconds"' in Source
    assert '"ElapsedSinceRoutingStartSeconds"' in Source
    assert '"PhysicalComponentStageTimings"' in Source
    for Stage in (
        "PhysicalEligibilityPreparation",
        "PhysicalEligibilitySolveAfterUnarySupport",
        "AuthoritativeGlobalReserve",
        "PhysicalAssemblyReplan",
        "BoundLocalCompilation",
    ):
        assert f'"{Stage}"' in Source


def test_physical_component_pipeline_compiles_symbolic_unary_support():
    PipelineSource = "\n".join((
        inspect.getsource(_PlaceAndRoutePcbWithPolicy),
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index("def ReserveAuthoritativeGlobalChannels(")
    ReserveEnd = Source.index(
        "PhysicalAssemblyPlan = PreparedAssembly.Plan",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]

    assert "SuccessfulGlobalPlanWasRetained = (" in Reservation
    assert "PreviousGlobalPlanWasRetained" in Reservation
    assert "if SuccessfulGlobalPlanWasRetained" in Source[ReserveStart:]


def test_boundary_iterator_identity_excludes_branch_and_preference_hints():
    Source = inspect.getsource(
        AuthoritativePlanner.SolvePreparedPhysicalComponentPortFactorDomain
    )
    IdentityStart = Source.index("BoundaryIteratorCacheKey = (")
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
    GeneratedOnly = AuthoritativePlanner.BuildMandatoryPortalTupleSelfConflictFailure((
        AuthoritativePlanner.MandatoryPortalTupleSelfConflictEvidence(
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
    Certified = AuthoritativePlanner.BuildMandatoryPortalTupleSelfConflictFailure((
        AuthoritativePlanner.MandatoryPortalTupleSelfConflictEvidence(
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
        AuthoritativePlanner,
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveStart = Source.index(
        "def ReserveAuthoritativeGlobalChannels(",
    )
    ReserveEnd = Source.index(
        "PreparedAssembly, GlobalChannelDesign = (",
        ReserveStart,
    )
    Reservation = Source[ReserveStart:ReserveEnd]

    assert "PreparePhysicalComponentGlobalPlanningPlacement(" in Reservation
    assert "PreparingPhysicalComponentGlobalChannels = True" in Reservation
    assert "RoutePcbDesign(" in Reservation
    assert "BindPhysicalComponentAssemblyGlobalChannels(" in Reservation
    assert "CurrentAssembly" in Reservation


def test_authoritative_global_reservation_precedes_closed_component_compile():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ReserveDefinition = Source.index(
        "def ReserveAuthoritativeGlobalChannels(",
    )
    ReserveCall = Source.index(
        "ReserveAuthoritativeGlobalChannels(\n"
        "                        PreparedAssembly",
        ReserveDefinition,
    )
    Compile = Source.index(
        "ComponentSolve = CompileClosedComponent(",
        ReserveCall,
    )

    assert ReserveDefinition < ReserveCall < Compile
    CapacityProof = Source.index(
        "ProveClosedComponentSymbolicCapacityEligibility("
    )
    assert CapacityProof < ReserveCall < Compile
    assert 'if Proof.Status == "capacity-feasible"' in Source


def test_foreign_portal_certificates_cover_preencoded_assignments():
    Source = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    Assignment = Source.index("def PlanAssignment(")
    Publish = Source.index(
        "PublishPhysicalGlobalForeignPortalCandidateNoGoods(\n"
        "                CandidatesBySignal",
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
        '"NativeAssignmentSkipped"] = True',
        Publish,
    )
    assert Publish < ExactEmptyReturn < NativeAssignment


def test_component_global_domains_close_before_native_assignment():
    Source = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    Completion = Source.index(
        '"PhysicalGlobalPreAssignmentDomainCompletion"'
    )
    AssignmentStart = Source.index(
        'AssignmentStarted = monotonic()',
        Completion,
    )
    InitialAssignment = Source.index(
        "Result = PlanAssignment(",
        AssignmentStart,
    )

    assert Completion < AssignmentStart < InitialAssignment


def test_foreign_portal_unary_empty_core_precedes_binary_certificates():
    Source = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    EmptyCore = Source.index(
        "if not IndependentEmptyCandidateDomainSignals:"
    )
    BinaryLoop = Source.index(
        "for FirstIndex, First in enumerate(OrderedCandidates):",
        EmptyCore,
    )
    Telemetry = Source.index(
        '"BinaryCompilationSkippedForUnaryEmptyCore"',
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
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Compile = Source.index("CompileClosedComponent(")
    RecordNoGood = Source.index(
        "RecordPhysicalComponentLocalCompilationNoGood(", Compile
    )
    Replan = Source.index(
        "ReplanPhysicalAssemblyWithTiming()", RecordNoGood
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
    assert '"PerSignalReservationFeedbackUsed": False' in LocalFailure
    assert "ProveGlobalRelaxedLocalUnsatisfiability(" not in LocalFailure
    assert "CertifyLocalInterfaceFactorPortfolio(" not in LocalFailure
    assert "PhysicalAssemblyGlobalRouteCanBeRebound(" not in LocalFailure


def test_bound_local_compiles_once_per_physical_assembly_plan():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Guard = Source.index(
        "CompiledPhysicalAssemblyPlanFingerprints: set[str] = set()"
    )
    Duplicate = Source.index(
        'Stage="DuplicateClosedComponentCompilation"',
        Guard,
    )
    Add = Source.index(
        "CompiledPhysicalAssemblyPlanFingerprints.add(",
        Duplicate,
    )
    Compile = Source.index(
        "ComponentSolve = CompileClosedComponent(",
        Add,
    )

    assert Guard < Duplicate < Add < Compile
    assert "ActiveComponentRemainingSeconds" in Source[Add:Compile + 500]


def test_incomplete_local_compile_stops_before_exact_plan_replan():
    Source = inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    Compile = Source.index("CompileClosedComponent(")
    Incomplete = Source.index(
        '"ClosedComponentCompilationIncomplete"', Compile
    )
    RecordNoGood = Source.index(
        "RecordPhysicalComponentLocalCompilationNoGood(", Incomplete
    )
    Replan = Source.index(
        "ReplanPhysicalAssemblyWithTiming()", RecordNoGood
    )
    Guard = Source[Incomplete:RecordNoGood]

    assert Compile < Incomplete < RecordNoGood < Replan
    assert "raise RoutingStageError(RoutingFailure(" in Guard
    assert '"Complete": False' in Guard
    assert "ReplanPhysicalAssemblyWithTiming(" not in Guard


def test_physical_guide_overlay_preserves_complete_ordinary_plan_coverage():
    Source = inspect.getsource(
        AuthoritativePlanner.RouteAuthoritativeResources
    )
    OverlayStart = Source.index(
        "# Rebuild ordinary whole-design guides against the current profile"
    )
    OverlayEnd = Source.index(
        'WorkTelemetry["GlobalGuidePlanCacheHit"]',
        OverlayStart,
    )
    Overlay = Source[OverlayStart:OverlayEnd]

    OrdinaryGuides = Overlay.index("**dict(CoarsePlan.Guides)")
    PhysicalGuides = Overlay.index(
        "Channel.Signal: frozenset(Channel.GuideCells)",
        OrdinaryGuides,
    )
    OrdinaryLayers = Overlay.index("**dict(CoarsePlan.Layers)")
    PhysicalLayers = Overlay.index(
        "Channel.Signal: int(Channel.Layer)",
        OrdinaryLayers,
    )

    assert OrdinaryGuides < PhysicalGuides
    assert OrdinaryLayers < PhysicalLayers
    assert Overlay.count(
        "if Channel.Signal in PhysicalAssemblyPortSignalsForGuide"
    ) == 2
    assert "CoarsePlan = FrozenPhysicalComponentGuidePlan" not in Overlay


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
        AuthoritativePlanner,
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
        AuthoritativePlanner.RouteAuthoritativeResources
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

    assert "PhysicalAssemblyPlan.PlanningChannels" not in IndexPreparation
    assert "Channel.Claims" not in IndexPreparation
    assert "AssignmentIndexed.EncodeClaims(Channel.Claims)" not in (
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

"""Proof Scheduling contracts for component pipeline."""

from ._component_pipeline_contracts import *


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

def test_explicit_complete_proof_overrides_stale_incomplete_status():
    assert not IsClusterInterfaceStateIncomplete(
        FailureReason=(
            RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        ),
        InterfaceDeadlineExpired=True,
        ComponentSolveStatus="incomplete",
        ExplicitCompleteUnsatProof=True,
    )

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

def test_global_contract_recommendation_rejects_uncertified_mixed_factors():
    Recommendation = SelectPhysicalComponentGlobalContractRecommendation(
        _MixedPhysicalCorridorDomains(),
        ("A", "B"),
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
    )

    assert Recommendation is None

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

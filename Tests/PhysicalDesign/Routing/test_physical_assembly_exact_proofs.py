"""Exact Proofs contracts for physical assembly."""

from ._physical_assembly_contracts import *


def test_factor_branching_prioritizes_learned_pair_constraints():
    Selected = SelectPhysicalFactorBranchSignal(
        {"Small": 1, "Left": 12, "Right": 8},
        (
            frozenset((
                ("Left", "local:left"),
                ("Right", "local:right"),
            )),
        ),
    )

    assert Selected == "Right"

def test_physical_factor_deadline_preserves_typed_stage_and_diagnostics():
    Original = RoutingStageError(RoutingFailure(
        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
        Stage="PhysicalComponentAssembly",
        Detail="deadline",
        RepairActions=("RetrySignal",),
        Diagnostics={
            "Stage": "physical-port-capacity",
            "AssignedPortCount": 3,
            "PortCount": 9,
            "ExpansionCount": 388864,
        },
    ))
    Classified = ClassifyPhysicalComponentAssemblyFailure(
        Original,
        Operation="prepare",
        Resources=SimpleNamespace(
            RejectedPhysicalComponentPortReservationsBySignal={
                "Carry3": {"reservation-b", "reservation-a"},
            },
            RejectedPhysicalComponentPortAssignmentFingerprints={
                "assignment-b",
                "assignment-a",
            },
        ),
    )
    Failure = Classified.Failure
    assert Failure.Reason == (
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete
    )
    assert Failure.Stage == "PhysicalComponentAssemblyIncomplete"
    assert Failure.RepairActions == ()
    assert Failure.Diagnostics["Stage"] == "physical-port-capacity"
    Classification = Failure.Diagnostics[
        "PhysicalComponentAssemblyClassification"
    ]
    assert Classification == {
        "Operation": "prepare",
        "ActiveFactorStage": "physical-port-capacity",
        "Complete": False,
        "FactorDiagnostics": {
            "AssignedPortCount": 3,
            "PortCount": 9,
            "ExpansionCount": 388864,
            "RejectedSignalReservationFingerprintsBySignal": {
                "Carry3": [
                    "reservation-a",
                    "reservation-b",
                ],
            },
            "RejectedSignalReservationCount": 2,
            "RejectedPortAssignmentFingerprints": [
                "assignment-a",
                "assignment-b",
            ],
        },
        "ExecutableRetryAllowed": False,
        "FlatFallbackAllowed": False,
        "SignalLevelFallbackAllowed": False,
    }

def test_physical_port_factor_preparation_is_complete_and_retained():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )

    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    assert Preparation.Complete
    assert Preparation.Feasible
    assert Preparation.DomainFingerprint
    assert Preparation.LaneFactorsBySignal
    assert (
        Resources.PreparedPhysicalComponentPortFactorDomain
        is Preparation
    )

def test_local_proof_materializes_only_requested_prepared_factor_domain():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    First = MaterializePreparedPhysicalPortOptionDomains(
        Preparation,
        Resources,
        ("Alpha",),
    )
    Second = MaterializePreparedPhysicalPortOptionDomains(
        Preparation,
        Resources,
        ("Alpha",),
    )

    assert First["Alpha"]
    assert Second["Alpha"] is First["Alpha"]
    assert all(Option.Signal == "Alpha" for Option in First["Alpha"])
    LocalFactors = dict(Preparation.LocalAccessFactorsBySignal)["Alpha"]
    ExpectedLocalContracts = {
        Value.LocalContractFingerprint for Value in LocalFactors
    }
    MaterializedLocalContracts = [
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in First["Alpha"]
    ]
    assert set(MaterializedLocalContracts) == ExpectedLocalContracts
    assert len(MaterializedLocalContracts) == len(
        ExpectedLocalContracts
    )
    LocalAccessFingerprintByContract = {
        Value.LocalContractFingerprint: Value.LocalAccessFingerprint
        for Value in LocalFactors
    }
    Supports = dict(Preparation.LocalApertureSupportBySignal)["Alpha"]
    ExpectedRepresentativeByContract = {
        Contract: min(
            (
                Value.ReservationFingerprint,
                Value.SupportFingerprint,
            )
            for Value in Supports
            if Value.LocalAccessFingerprint == LocalAccessFingerprint
        )[0]
        for Contract, LocalAccessFingerprint
        in LocalAccessFingerprintByContract.items()
    }
    assert {
        BuildPhysicalPortLocalContractFingerprint(Option): (
            Option.ReservationFingerprint
        )
        for Option in First["Alpha"]
    } == ExpectedRepresentativeByContract
    assert not Resources.PhysicalComponentPortOptionDomainCache
    assert len(Resources.PhysicalComponentFactorPortOptionDomainCache) == 1

def test_selected_plan_local_proof_core_has_complete_prepared_factor_domain(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    monkeypatch.setattr(
        "PhysicalDesign.Routing.Regions.Interfaces.Reservations."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )
    Assembly = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
    )
    Signals = tuple(Port.Signal for Port in Assembly.Plan.Ports)

    Domains = MaterializePreparedPhysicalPortOptionDomains(
        Preparation,
        Resources,
        Signals,
    )

    assert set(Domains) == set(Signals)
    assert all(Domains.values())
    assert all(
        BuildPhysicalPortLocalContractFingerprint(Port)
        in {
            BuildPhysicalPortLocalContractFingerprint(Option)
            for Option in Domains[Port.Signal]
        }
        for Port in Assembly.Plan.Ports
    )
    assert not Resources.PhysicalComponentPortOptionDomainCache
    assert len(Resources.PhysicalComponentFactorPortOptionDomainCache) == len(
        Signals
    )

def test_prepared_physical_port_factor_rejects_component_graph_mismatch():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    with pytest.raises(RoutingStageError) as Raised:
        SolvePreparedPhysicalComponentPortFactorDomain(
            replace(
                Preparation,
                ComponentGraphFingerprint="changed-component-graph",
            ),
            Resources,
        )

    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentAssemblyIdentityMismatch
    )
    assert "access-certificate-component-graph" in (
        Raised.value.Failure.Diagnostics["IdentityMismatches"]
    )

def test_prepared_physical_port_factor_resume_does_not_rebuild_lanes(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Events = []
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
        WorkCheck=Events.append,
    )
    LaneEventCount = sum(
        Event.get("Stage") == "physical-port-lane-assignment"
        for Event in Events
    )
    monkeypatch.setattr(
        "PhysicalDesign.Routing.Regions.Interfaces.Reservations."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )

    Assembly = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=Events.append,
    )

    assert Assembly.Plan.Complete
    assert sum(
        Event.get("Stage") == "physical-port-lane-assignment"
        for Event in Events
    ) == LaneEventCount

def test_prepared_physical_port_replan_reuses_factorized_domain(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    monkeypatch.setattr(
        "PhysicalDesign.Routing.Regions.Interfaces.Reservations."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )
    FirstEvents = []
    First = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=FirstEvents.append,
    )
    assert not Resources.PhysicalComponentPortOptionDomainCache
    RejectedPlanClause = frozenset(
        (
            Port.Signal,
            BuildPhysicalPortApertureContractFingerprint(Port),
        )
        for Port in First.Plan.Ports
    )
    Resources.RejectedPhysicalComponentPortReservationSets.add(
        RejectedPlanClause
    )

    SecondEvents = []
    Second = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=SecondEvents.append,
    )

    assert (
        Second.Plan.PortAssignmentFingerprint
        != First.Plan.PortAssignmentFingerprint
    )
    assert not any(
        Event.get("Stage") == "physical-port-option-domain"
        for Event in FirstEvents
    )
    assert not any(
        Event.get("Stage") == "physical-port-option-domain"
        for Event in SecondEvents
    )
    assert not any(
        Event.get("Stage") == "physical-port-option-domain-published"
        for Event in (*FirstEvents, *SecondEvents)
    )
    SelectedEvent = next(
        Event
        for Event in SecondEvents
        if Event.get("Stage") == "physical-port-plan-selected"
    )
    assert SelectedEvent["FactorizedPortSearch"] is True
    assert SelectedEvent["PreparedApertureFactorDomainReused"] is True
    assert SelectedEvent["PersistentPortCspStateReused"] is True

    ColdResources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    ColdResources.RejectedPhysicalComponentPortReservationSets.add(
        RejectedPlanClause
    )
    Cold = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        ColdResources,
    )

    assert Cold.Plan.PortAssignmentFingerprint == (
        Second.Plan.PortAssignmentFingerprint
    )

def test_proof_neutral_port_assignment_deferral_selects_a_distinct_plan(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    monkeypatch.setattr(
        "PhysicalDesign.Routing.Regions.Interfaces.Reservations."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )
    First = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
    )
    (
        Resources
        .DeferredPhysicalComponentPortAssignmentFingerprints
        .add(First.Plan.PortAssignmentFingerprint)
    )

    Second = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
    )

    assert Second.Plan.PortAssignmentFingerprint != (
        First.Plan.PortAssignmentFingerprint
    )
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Resources.DeferredPhysicalComponentPortAssignmentFingerprints == {
        First.Plan.PortAssignmentFingerprint
    }

def test_pending_joint_placement_sibling_requires_same_physical_proof():
    Constraints = SimpleNamespace(Fingerprint="constraints")
    Cut = SimpleNamespace(ConflictFingerprint="cut")
    State = SimpleNamespace(
        PhysicalProofCoreSignals=frozenset(("B1",)),
        RelocationSignals=frozenset(("B1",)),
        RelocationPrioritySignals=frozenset(("B1",)),
        RequiredRelocationSignals=frozenset(("B1",)),
        PhysicalProofFingerprint="proof",
        AssignmentCut=Cut,
        AssignmentConstraints=Constraints,
    )

    assert PendingJointPlacementStateMatchesPhysicalProof(
        State,
        ("B1",),
        "proof",
        Cut,
        Constraints,
    )
    assert not PendingJointPlacementStateMatchesPhysicalProof(
        State,
        ("NandNet19", "NandNet42", "NandNet7"),
        "proof",
        Cut,
        Constraints,
    )
    assert not PendingJointPlacementStateMatchesPhysicalProof(
        State,
        ("B1",),
        "proof",
        SimpleNamespace(ConflictFingerprint="new-cut"),
        Constraints,
    )
    assert not PendingJointPlacementStateMatchesPhysicalProof(
        State,
        ("B1",),
        "new-proof",
        Cut,
        Constraints,
    )

def test_capacity_repair_geometry_focus_prefers_smallest_exact_proof():
    Pair = SimpleNamespace(
        ProofFingerprint="pair",
        RelocationSignals=("Beta", "Gamma"),
    )
    Broad = SimpleNamespace(
        ProofFingerprint="broad",
        RelocationSignals=("Alpha", "Beta", "Gamma"),
    )
    Singleton = SimpleNamespace(
        ProofFingerprint="singleton",
        RelocationSignals=("Alpha",),
    )

    assert SelectCapacityRepairGeometryFocus(Broad, Pair, Broad) is Pair
    assert SelectCapacityRepairGeometryFocus(Singleton, Pair, Broad) is Singleton
    assert SelectCapacityRepairGeometryFocus(None, Pair, Broad) is Pair

def test_rejected_physical_port_assignment_advances_without_reopening_it():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    First = _Assembly(Problem, Resources)
    Resources.RejectedPhysicalComponentPortAssignmentFingerprints.add(
        First.Plan.PortAssignmentFingerprint
    )

    Second = _Assembly(Problem, Resources)

    assert (
        Second.Plan.PortAssignmentFingerprint
        != First.Plan.PortAssignmentFingerprint
    )
    assert Second.Plan.PlanFingerprint != First.Plan.PlanFingerprint

def test_rejected_signal_port_reservation_prunes_equivalent_plans():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    First = _Assembly(Problem, Resources)
    FirstPort = First.Plan.Ports[0]
    Resources.RejectedPhysicalComponentPortReservationsBySignal.setdefault(
        FirstPort.Signal,
        set(),
    ).add(FirstPort.ReservationFingerprint)

    Second = _Assembly(Problem, Resources)

    assert (
        Second.Plan.Ports[0].ReservationFingerprint
        != FirstPort.ReservationFingerprint
    )

def test_incomplete_local_core_cannot_prune_a_port_contract():
    Solve = ComponentRoutingSolveResult(
        Status="incomplete",
        ProofFingerprint="proof",
        Diagnostics={
            "LocalUnsatCoreComplete": False,
            "LocalUnsatCoreSignals": ["PortA"],
        },
    )
    with pytest.raises(ValueError, match="complete local proof"):
        RecordPhysicalComponentLocalCompilationNoGood(
            Solve,
            SimpleNamespace(
                PortAssignmentFingerprint="assignment",
                Ports=(),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

def test_local_interface_factor_hashes_full_proof_domain_once_per_portfolio(
    monkeypatch,
):
    Assembly = _Assembly(_Problem())
    FullDomainCalls = []

    def FullDomain(_ProblemValue):
        FullDomainCalls.append(True)
        return "full-local-domain"

    def Option(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            OwnedTerminals=((X, 7, 0),),
            OwnedTerminalFingerprints=(f"terminal-{X}",),
            OwnedCandidateFingerprints=(f"candidate-{X}",),
            FabricAttachment=(X, 7, 0),
            Attachment=(X, 7, 0),
            LocalPath=((X, 7, 0),),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 0), Option("Current", 1))
    CompleteOptions = (Option("Complete", 2), Option("Complete", 3))

    def CartesianPortfolio(
        _Plan,
        _CurrentSignal,
        _CompleteSignal,
        _Resources,
        *,
        BuildProofDomainFingerprint,
        EvaluatePair,
        **_Keywords,
    ):
        assert _Keywords["MaximumCompletedRows"] is None
        Domains = [
            BuildProofDomainFingerprint(Current, Complete)
            for Current in CurrentOptions
            for Complete in CompleteOptions
        ]
        assert len(Domains) == 4
        assert len(set(Domains)) == 4
        return {"Complete": False, "ProofDomainFingerprints": Domains}

    monkeypatch.setattr(
        ComponentCertification,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        FullDomain,
    )
    monkeypatch.setattr(
        ComponentCertification,
        "CertifyDirectionalLocalContractPortfolio",
        CartesianPortfolio,
    )

    Diagnostics = ComponentCertification.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        SimpleNamespace(),
        DeadlineSeconds=1.0,
    )

    assert Diagnostics["Complete"] is False
    assert len(FullDomainCalls) == 1

def test_local_interface_factor_reaches_monotonic_proof_fixed_point(
    monkeypatch,
):
    Assembly = _Assembly(_Problem())
    Calls = []

    def Portfolio(
        _Plan,
        _CurrentSignal,
        _CompleteSignal,
        Resources,
        **_Keywords,
    ):
        assert _Keywords["MaximumCompletedRows"] is None
        Calls.append(True)
        ProofCache = getattr(
            Resources,
            "PhysicalComponentLocalInterfaceFactorProofCache",
            None,
        )
        if ProofCache is None:
            ProofCache = {}
            Resources.PhysicalComponentLocalInterfaceFactorProofCache = (
                ProofCache
            )
        if len(Calls) == 1:
            ProofCache["new-proof"] = object()
            return {"Complete": False, "Status": "incomplete"}
        return {"Complete": True, "Status": "complete"}

    monkeypatch.setattr(
        ComponentCertification,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _ProblemValue: "full-local-domain",
    )
    monkeypatch.setattr(
        ComponentCertification,
        "CertifyDirectionalLocalContractPortfolio",
        Portfolio,
    )
    Diagnostics = ComponentCertification.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        SimpleNamespace(
            RejectedPhysicalComponentPortReservationSets=set(),
        ),
        DeadlineSeconds=1.0,
    )

    assert Diagnostics["Complete"] is True
    assert Diagnostics["CertificationPassCount"] == 2

def test_local_interface_factor_compiles_each_complete_contract_once(
    monkeypatch,
):
    Assembly = _Assembly(_Problem())
    StaticContext = object()
    StaticContextCalls = []
    CompileCalls = []
    ContractDomainBuildCalls = []
    BuildContractDomain = (
        ComponentPortfolios.BuildCompleteOpposingNetAccessContractDomain
    )

    def Option(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricDomainFingerprint=f"domain-{X}",
            OwnedTerminals=((X, 7, 0),),
            OwnedTerminalFingerprints=(f"terminal-{X}",),
            OwnedCandidateFingerprints=(f"candidate-{X}",),
            OwnedAccessCandidates=(),
            FabricAttachment=(X, 7, 0),
            Attachment=(X, 7, 0),
            LocalPath=((X, 7, 0),),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 0), Option("Current", 1))
    CompleteOptions = (Option("Complete", 2), Option("Complete", 3))

    def CartesianPortfolio(
        _Plan,
        _CurrentSignal,
        _CompleteSignal,
        _Resources,
        *,
        BuildProofDomainFingerprint,
        EvaluatePair,
        **_Keywords,
    ):
        Results = [
            EvaluatePair(
                Current,
                Complete,
                BuildProofDomainFingerprint(Current, Complete),
            )
            for Complete in CompleteOptions
            for Current in CurrentOptions
        ]
        return {"Complete": False, "Results": Results}

    def BuildStaticContext(_ProblemValue, Signal):
        StaticContextCalls.append(Signal)
        return StaticContext

    def Compile(_ProblemValue, Signal, PortsByContract, **Keywords):
        CompileCalls.append((Signal, Keywords.get("StaticContext")))
        return SimpleNamespace(
            Complete=True,
            Portfolios={
                Contract: SimpleNamespace(
                    Complete=True,
                    Variants=(),
                    DomainFingerprint=(
                        f"portfolio-{len(CompileCalls)}-{Contract}"
                    ),
                    Status="complete",
                    ExpansionCount=0,
                    Diagnostics={},
                )
                for Contract in PortsByContract
            },
            CanonicalStateCount=0,
            NetVariantBuildCount=0,
            Diagnostics={},
        )

    def OracleRow(
        _ProblemValue,
        *,
        CurrentSignal,
        CompleteSignal,
        CurrentPortsByContract,
        CompleteLocalContractFingerprint,
        DomainFingerprintsByCurrentContract,
        **_Keywords,
    ):
        return SimpleNamespace(
            Results={
                Contract: SimpleNamespace(
                    CurrentSignal=CurrentSignal,
                    CompleteSignal=CompleteSignal,
                    CurrentLocalContractFingerprint=Contract,
                    CompleteLocalContractFingerprint=(
                        CompleteLocalContractFingerprint
                    ),
                    Complete=True,
                    Status="feasible",
                    Feasible=True,
                    ProofFingerprint="witness",
                    DomainFingerprint=(
                        DomainFingerprintsByCurrentContract[Contract]
                    ),
                    ExpansionCount=0,
                    Detail="feasible",
                    Diagnostics={},
                )
                for Contract in CurrentPortsByContract
            },
            AccessSignatureCount=len(CurrentPortsByContract),
            VariantScanCount=0,
            SignaturePairCheckCount=0,
        )

    ProofDomain = ["full-local-domain"]
    monkeypatch.setattr(
        ComponentCertification,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _ProblemValue: ProofDomain[0],
    )
    monkeypatch.setattr(
        ComponentCertification,
        "BuildCompleteComponentNetPortfolioStaticContext",
        BuildStaticContext,
    )
    monkeypatch.setattr(
        ComponentCertification,
        "CompileCompleteComponentNetVariantPortfolios",
        Compile,
    )
    monkeypatch.setattr(
        ComponentCertification,
        "BuildCompleteOpposingNetAccessContractDomain",
        lambda *Arguments, **Keywords: (
            ContractDomainBuildCalls.append(True),
            BuildContractDomain(*Arguments, **Keywords),
        )[1],
    )
    monkeypatch.setattr(
        ComponentCertification,
        "EvaluateCompleteOpposingNetAccessContractRow",
        OracleRow,
    )
    monkeypatch.setattr(
        ComponentCertification,
        "CertifyDirectionalLocalContractPortfolio",
        CartesianPortfolio,
    )

    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            "portfolio-factor-domain",
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    First = ComponentCertification.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        Resources,
        DeadlineSeconds=1.0,
    )
    Second = ComponentCertification.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        Resources,
        DeadlineSeconds=1.0,
    )
    ProofDomain[0] = "changed-full-local-domain"
    Third = ComponentCertification.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        Resources,
        DeadlineSeconds=1.0,
    )

    assert StaticContextCalls == ["Complete", "Complete"]
    assert CompileCalls == [
        ("Complete", StaticContext),
        ("Complete", StaticContext),
        ("Complete", StaticContext),
    ]
    assert First["PersistentPortfolioContextReused"] is False
    assert Second["PersistentPortfolioContextReused"] is True
    assert Third["PersistentPortfolioContextReused"] is False
    assert First["CachedCompletePortfolioCount"] == 2
    assert Second["CachedCompletePortfolioCount"] == 2
    assert First["CachedOpposingRowContextCount"] == 2
    assert Second["CachedOpposingRowContextCount"] == 2
    assert len(ContractDomainBuildCalls) == 2
    assert First["CurrentAccessContractDomainReused"] is False
    assert Second["CurrentAccessContractDomainReused"] is True

def test_local_interface_factor_reevaluates_incomplete_bulk_row(monkeypatch):
    Assembly = _Assembly(_Problem())
    def Option(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricDomainFingerprint=f"domain-{X}",
            OwnedTerminals=((X, 7, 0),),
            OwnedTerminalFingerprints=(f"terminal-{X}",),
            OwnedCandidateFingerprints=(f"candidate-{X}",),
            OwnedAccessCandidates=(),
            FabricAttachment=(X, 7, 0),
            Attachment=(X, 7, 0),
            LocalPath=((X, 7, 0),),
            Capacity=1,
        )

    Current = Option("Current", 0)
    Complete = Option("Complete", 1)
    BulkCalls = []

    monkeypatch.setattr(
        ComponentCertification,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _ProblemValue: "full-local-domain",
    )
    monkeypatch.setattr(
        ComponentCertification,
        "BuildCompleteComponentNetPortfolioStaticContext",
        lambda *_Arguments: object(),
    )
    monkeypatch.setattr(
        ComponentCertification,
        "CompileCompleteComponentNetVariantPortfolios",
        lambda *_Arguments, **_Keywords: SimpleNamespace(
            Complete=True,
            Portfolios={
                Contract: SimpleNamespace(
                    Complete=True,
                    Variants=(),
                    DomainFingerprint="complete-portfolio",
                    Status="complete",
                    ExpansionCount=0,
                    Diagnostics={},
                )
                for Contract in _Arguments[2]
            },
            CanonicalStateCount=0,
            NetVariantBuildCount=0,
            Diagnostics={},
        ),
    )

    def Bulk(
        _ProblemValue,
        *,
        CurrentSignal,
        CompleteSignal,
        CurrentPortsByContract,
        CompleteLocalContractFingerprint,
        DomainFingerprintsByCurrentContract,
        **_Keywords,
    ):
        BulkCalls.append(True)
        IsComplete = len(BulkCalls) > 1
        return SimpleNamespace(
            Results={
                Contract: SimpleNamespace(
                    CurrentSignal=CurrentSignal,
                    CompleteSignal=CompleteSignal,
                    CurrentLocalContractFingerprint=Contract,
                    CompleteLocalContractFingerprint=(
                        CompleteLocalContractFingerprint
                    ),
                    Complete=IsComplete,
                    Status=(
                        "architectural-unsatisfiable"
                        if IsComplete
                        else "incomplete"
                    ),
                    Feasible=False if IsComplete else None,
                    ProofFingerprint="proof" if IsComplete else "",
                    DomainFingerprint=(
                        DomainFingerprintsByCurrentContract[Contract]
                    ),
                    ExpansionCount=0,
                    Detail="",
                    Diagnostics={},
                )
                for Contract in CurrentPortsByContract
            },
            AccessSignatureCount=1,
            VariantScanCount=0,
            SignaturePairCheckCount=0,
        )

    monkeypatch.setattr(
        ComponentCertification,
        "EvaluateCompleteOpposingNetAccessContractRow",
        Bulk,
    )
    PortfolioCalls = []

    def Portfolio(
        _Plan,
        _CurrentSignal,
        _CompleteSignal,
        Resources,
        *,
        BuildProofDomainFingerprint,
        EvaluatePair,
        **_Keywords,
    ):
        PortfolioCalls.append(True)
        Proof = EvaluatePair(
            Current,
            Complete,
            BuildProofDomainFingerprint(Current, Complete),
        )
        if len(PortfolioCalls) == 1:
            Resources.PhysicalComponentLocalInterfaceFactorProofCache = {
                "frontier": object()
            }
            return {"Complete": False, "Status": "incomplete"}
        assert Proof["GlobalRelaxedLocalProofComplete"] is True
        return {"Complete": True, "Status": "complete"}

    monkeypatch.setattr(
        ComponentCertification,
        "CertifyDirectionalLocalContractPortfolio",
        Portfolio,
    )
    Diagnostics = ComponentCertification.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        SimpleNamespace(
            **_PreparedFactorDomainFixture(
                "incomplete-row-factor-domain",
                Current=(Current,),
                Complete=(Complete,),
            ),
            RejectedPhysicalComponentPortReservationSets=set(),
        ),
        DeadlineSeconds=1.0,
    )

    assert Diagnostics["Complete"] is True
    assert len(BulkCalls) == 2

def test_directional_local_factor_no_good_requires_complete_pair_coverage():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    CompleteOptions = (Option("Complete", 3), Option("Complete", 4))
    Plan = SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0]))
    DomainFingerprint = "prepared-domain"
    CacheKey = BuildStableFingerprint((
        "physical-component-port-solver-cache-v2",
        DomainFingerprint,
    ))
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )

    # One selected pair proof cannot stand for the other Current contract.
    SelectedPair = frozenset((
        (
            "Current",
            BuildPhysicalPortLocalContractFingerprint(CurrentOptions[0]),
        ),
        (
            "Complete",
            BuildPhysicalPortLocalContractFingerprint(CompleteOptions[0]),
        ),
    ))
    Resources.RejectedPhysicalComponentPortReservationSets.add(SelectedPair)
    assert BuildDirectionalLocalFactorNoGoods(
        Plan,
        "Current",
        "Complete",
        Resources,
    ) == ()

    # Exact proofs covering the full cached Current domain permit resolution
    # to the prepared-domain key while retaining the exact Complete contract.
    for CurrentOption in CurrentOptions:
        Resources.RejectedPhysicalComponentPortReservationSets.add(
            frozenset((
                (
                    "Current",
                    BuildPhysicalPortLocalContractFingerprint(CurrentOption),
                ),
                (
                    "Complete",
                    BuildPhysicalPortLocalContractFingerprint(
                        CompleteOptions[0]
                    ),
                ),
            ))
        )
    NoGoods = BuildDirectionalLocalFactorNoGoods(
        Plan,
        "Current",
        "Complete",
        Resources,
    )
    Expected = frozenset((
        ("Current", "local-signal-domain:" + CacheKey),
        (
            "Complete",
            BuildPhysicalPortLocalContractFingerprint(CompleteOptions[0]),
        ),
    ))
    assert NoGoods == (Expected,)
    assert BuildDirectionalLocalFactorNoGoods(
        Plan,
        "Complete",
        "Current",
        Resources,
    ) == ()

    Resources.PreparedPhysicalComponentPortFactorDomain = SimpleNamespace(
        DomainFingerprint="different-prepared-domain",
    )
    assert BuildDirectionalLocalFactorNoGoods(
        Plan,
        "Current",
        "Complete",
        Resources,
    ) == ()

def test_local_interface_factor_portfolio_batches_and_reuses_exact_pairs():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    CompleteOptions = (Option("Complete", 3), Option("Complete", 4))
    Plan = SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0]))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    Evaluated = []

    def Domain(Current, Complete):
        return "proof:" + ":".join((
            BuildPhysicalPortLocalContractFingerprint(Current),
            BuildPhysicalPortLocalContractFingerprint(Complete),
        ))

    def Evaluate(Current, Complete, ProofDomain):
        Evaluated.append(ProofDomain)
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": (
                "proof-result:" + ProofDomain
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalUnsatCoreSignals": [
                "Current",
                "Complete",
            ],
            "GlobalRelaxedLocalDomainFingerprint": ProofDomain,
        }

    First = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=Domain,
        EvaluatePair=Evaluate,
    )

    assert First["Complete"]
    assert First["PairDomainCount"] == 4
    assert First["EvaluatedPairCount"] == 4
    assert First["CertifiedPairCount"] == 4
    assert First["DirectionalNoGoodCount"] == 2
    assert First["PromotedFabricNoGoodCount"] == 1

    Second = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=Domain,
        EvaluatePair=lambda *_Arguments: pytest.fail(
            "certified pair should come from the proof cache"
        ),
    )

    assert Second["Complete"]
    assert Second["EvaluatedPairCount"] == 0
    assert Second["PreviouslyCoveredPairCount"] == 4
    assert len(Evaluated) == 4

def test_local_interface_factor_portfolio_does_not_lift_incomplete_coverage():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    Complete = Option("Complete", 3)
    Plan = SimpleNamespace(Ports=(CurrentOptions[0], Complete))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=(Complete,),
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    FirstContract = BuildPhysicalPortLocalContractFingerprint(
        CurrentOptions[0]
    )

    def Domain(Current, _Complete):
        return BuildPhysicalPortLocalContractFingerprint(Current)

    def Evaluate(Current, _Complete, ProofDomain):
        if ProofDomain != FirstContract:
            return {
                "GlobalRelaxedLocalProofComplete": False,
                "GlobalRelaxedLocalCoreComplete": False,
                "GlobalRelaxedLocalDomainFingerprint": ProofDomain,
            }
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": (
                "proof-result:" + ProofDomain
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalUnsatCoreSignals": [
                "Current",
                "Complete",
            ],
            "GlobalRelaxedLocalDomainFingerprint": ProofDomain,
        }

    Result = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=Domain,
        EvaluatePair=Evaluate,
    )

    assert not Result["Complete"]
    assert Result["CertifiedPairCount"] == 0
    assert Result["EvaluatedPairCount"] == 1
    assert len(Result["IncompletePairs"]) == 1
    assert Result["DeferredIncompleteRowCount"] == 1
    assert Result["DirectionalNoGoodCount"] == 0
    assert Result["PromotedFabricNoGoodCount"] == 0

    RetryEvaluations = []

    def EvaluateRetry(_Current, _Complete, ProofDomain):
        RetryEvaluations.append(ProofDomain)
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": (
                "proof-result:" + ProofDomain
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalUnsatCoreSignals": [
                "Current",
                "Complete",
            ],
            "GlobalRelaxedLocalDomainFingerprint": ProofDomain,
        }

    Retried = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=Domain,
        EvaluatePair=EvaluateRetry,
    )

    assert Retried["Complete"]
    assert Retried["PreviouslyCoveredPairCount"] == 0
    assert Retried["EvaluatedPairCount"] == 2
    assert len(RetryEvaluations) == 2
    assert Retried["DirectionalNoGoodCount"] == 1

def test_local_interface_factor_portfolio_requires_complete_cartesian_proof():
    def Option(Signal, LocalX, Fabric):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint=Fabric,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (
        Option("Current", 1, "fabric-current"),
        Option("Current", 2, "fabric-current"),
    )
    CompleteOptions = (
        Option("Complete", 3, "fabric-complete"),
        Option("Complete", 4, "fabric-complete"),
    )
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    SeedPair = frozenset((
        (
            "Current",
            BuildPhysicalPortLocalContractFingerprint(CurrentOptions[0]),
        ),
        (
            "Complete",
            BuildPhysicalPortLocalContractFingerprint(CompleteOptions[0]),
        ),
    ))
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        RejectedPhysicalComponentPortReservationSets={SeedPair},
    )
    Plan = SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0]))
    Evaluated = []

    def ProofDomain(Current, Complete):
        return "proof:" + BuildPhysicalPortLocalContractFingerprint(
            Current
        ) + ":" + BuildPhysicalPortLocalContractFingerprint(Complete)

    def Evaluate(Current, Complete, Domain):
        Evaluated.append(Domain)
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofStatus": "architectural-unsatisfiable",
            "GlobalRelaxedLocalProofFingerprint": (
                "proof-result:" + Domain
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalUnsatCoreSignals": ["Current", "Complete"],
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Diagnostics = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Evaluate,
    )

    assert Diagnostics["Complete"] is True
    assert Diagnostics["PairDomainCount"] == 4
    assert Diagnostics["PreviouslyCoveredPairCount"] == 1
    assert Diagnostics["EvaluatedPairCount"] == 3
    assert Diagnostics["CertifiedPairCount"] == 3
    assert Diagnostics["DirectionalNoGoodCount"] == 2
    assert Diagnostics["PromotedFabricNoGoodCount"] == 1
    assert len(Evaluated) == 3

    Resources.RejectedPhysicalComponentPortReservationSets = {SeedPair}
    CachedDiagnostics = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=lambda *_Args: pytest.fail(
            "identical local proof domains must reuse the portfolio cache"
        ),
    )
    assert CachedDiagnostics["Complete"] is True
    assert CachedDiagnostics["CachedPairCount"] == 3
    assert CachedDiagnostics["EvaluatedPairCount"] == 0

    Resources.RejectedPhysicalComponentPortReservationSets = {SeedPair}
    ChangedDomainEvaluations = []

    def ChangedDomain(Current, Complete):
        return "changed:" + ProofDomain(Current, Complete)

    def EvaluateChanged(Current, Complete, Domain):
        ChangedDomainEvaluations.append(Domain)
        return Evaluate(Current, Complete, Domain)

    ChangedDiagnostics = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ChangedDomain,
        EvaluatePair=EvaluateChanged,
    )
    assert ChangedDiagnostics["EvaluatedPairCount"] == 3
    assert ChangedDiagnostics["CachedPairCount"] == 0
    assert len(ChangedDomainEvaluations) == 3

def test_local_interface_factor_portfolio_stops_at_feasible_pair():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    CompleteOptions = (Option("Complete", 3), Option("Complete", 4))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    Evaluated = []

    def ProofDomain(Current, Complete):
        return "proof:" + BuildPhysicalPortLocalContractFingerprint(
            Current
        ) + ":" + BuildPhysicalPortLocalContractFingerprint(Complete)

    def Feasible(_Current, _Complete, Domain):
        Evaluated.append(Domain)
        return {
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalCoreComplete": False,
            "GlobalRelaxedLocalProofStatus": "feasible",
            "GlobalRelaxedLocalFeasibleWitnessComplete": True,
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Diagnostics = CertifyDirectionalLocalContractPortfolio(
        SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0])),
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Feasible,
    )

    assert Diagnostics["Complete"] is False
    assert Diagnostics["Status"] == "feasible-witness"
    assert Diagnostics["Reason"] == "exact-local-contract-pair-is-feasible"
    assert Diagnostics["FeasibleWitnessCount"] == 1
    assert Diagnostics["FeasibleWitness"] is not None
    assert Diagnostics["EvaluatedPairCount"] == 1
    assert Diagnostics["DirectionalNoGoodCount"] == 0
    assert Diagnostics["PromotedFabricNoGoodCount"] == 0
    assert len(Resources.RejectedPhysicalComponentPortReservationSets) == 0

def test_local_interface_factor_portfolio_yields_after_one_complete_row():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    CompleteOptions = (Option("Complete", 3), Option("Complete", 4))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        RejectedPhysicalComponentPortReservationSets=set(),
    )

    def ProofDomain(Current, Complete):
        return "proof:" + BuildPhysicalPortLocalContractFingerprint(
            Current
        ) + ":" + BuildPhysicalPortLocalContractFingerprint(Complete)

    def Unsatisfiable(_Current, _Complete, Domain):
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofStatus": "architectural-unsatisfiable",
            "GlobalRelaxedLocalProofFingerprint": "proof-result:" + Domain,
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalUnsatCoreSignals": ["Current", "Complete"],
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Diagnostics = CertifyDirectionalLocalContractPortfolio(
        SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0])),
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Unsatisfiable,
        MaximumCompletedRows=1,
    )

    assert Diagnostics["Status"] == "partial-complete-rows"
    assert Diagnostics["CompletedRowLimitReached"] is True
    assert Diagnostics["CompletedRowCount"] == 1
    assert Diagnostics["ProcessedCompleteContractCount"] == 1
    assert Diagnostics["DeferredRowCount"] == 1
    assert Diagnostics["EvaluatedPairCount"] == 2

    # A global replan over the same frozen preparation must retain the
    # proof-qualified directional row and visit only the deferred row.  The
    # completed row is a monotonic local fact, not an assembly-plan variant.
    Resumed = CertifyDirectionalLocalContractPortfolio(
        SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[1])),
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Unsatisfiable,
    )

    assert Resumed["Complete"] is True
    assert Resumed["PreviouslyCoveredPairCount"] == 2
    assert Resumed["EvaluatedPairCount"] == 2
    assert Resumed["CertifiedPairCount"] == 2
    assert Resumed["CompletedRowCount"] == 1

def test_local_interface_factor_portfolio_does_not_cache_incomplete_proof():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    Current = Option("Current", 1)
    Complete = Option("Complete", 2)
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=(Current,),
            Complete=(Complete,),
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        PhysicalLocalPortPairSupportCertificateCache={},
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    EvaluationCount = 0

    def Incomplete(_Current, _Complete, Domain):
        nonlocal EvaluationCount
        EvaluationCount += 1
        return {
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalCoreComplete": False,
            "GlobalRelaxedLocalProofStatus": "incomplete",
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Arguments = dict(
        Plan=SimpleNamespace(Ports=(Current, Complete)),
        CurrentSignal="Current",
        CompleteSignal="Complete",
        Resources=Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=lambda *_Options: "proof-domain",
        EvaluatePair=Incomplete,
    )
    First = CertifyDirectionalLocalContractPortfolio(**Arguments)
    Second = CertifyDirectionalLocalContractPortfolio(**Arguments)

    assert First["Status"] == Second["Status"] == "incomplete"
    assert First["IncompletePairCount"] == 1
    assert First["DeferredIncompleteRowCount"] == 1
    assert Second["CachedPairCount"] == 0
    assert EvaluationCount == 2
    assert Resources.PhysicalComponentLocalInterfaceFactorProofCache == {}

def test_local_interface_factor_defers_incomplete_rows_and_finds_later_witness():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (
        Option("Current", 1),
        Option("Current", 2),
        Option("Current", 3),
    )
    CompleteOptions = (
        Option("Complete", 10),
        Option("Complete", 11),
        Option("Complete", 12),
    )
    CompleteByContract = {
        BuildPhysicalPortLocalContractFingerprint(Value): Value
        for Value in CompleteOptions
    }
    SelectedContract = BuildPhysicalPortLocalContractFingerprint(
        CompleteOptions[0]
    )
    OrderedCompleteContracts = tuple(sorted(
        CompleteByContract,
        key=lambda Value: (Value != SelectedContract, Value),
    ))
    FeasibleContract = OrderedCompleteContracts[-1]
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        PhysicalLocalPortPairSupportCertificateCache={},
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    EvaluationCountByCompleteContract = {}

    def ProofDomain(Current, Complete):
        return "proof:" + BuildPhysicalPortLocalContractFingerprint(
            Current
        ) + ":" + BuildPhysicalPortLocalContractFingerprint(Complete)

    def Evaluate(_Current, Complete, Domain):
        CompleteContract = BuildPhysicalPortLocalContractFingerprint(
            Complete
        )
        EvaluationCountByCompleteContract[CompleteContract] = (
            EvaluationCountByCompleteContract.get(CompleteContract, 0) + 1
        )
        if CompleteContract == FeasibleContract:
            return {
                "GlobalRelaxedLocalProofComplete": False,
                "GlobalRelaxedLocalCoreComplete": False,
                "GlobalRelaxedLocalProofStatus": "feasible",
                "GlobalRelaxedLocalFeasibleWitnessComplete": True,
                "GlobalRelaxedLocalProofFingerprint": "witness:" + Domain,
                "GlobalRelaxedLocalDomainFingerprint": Domain,
            }
        return {
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalCoreComplete": False,
            "GlobalRelaxedLocalProofStatus": "incomplete",
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Diagnostics = CertifyDirectionalLocalContractPortfolio(
        SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0])),
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Evaluate,
        MaximumCompletedRows=None,
    )

    assert Diagnostics["Status"] == "feasible-witness"
    assert Diagnostics["FeasibleWitnessCount"] == 1
    assert Diagnostics["FeasibleWitness"] is not None
    assert Diagnostics["IncompletePairCount"] == 2
    assert Diagnostics["DeferredIncompleteRowCount"] == 2
    assert len(set(Diagnostics["DeferredIncompleteRows"])) == 2
    assert Diagnostics["EvaluatedPairCount"] == 3
    assert set(EvaluationCountByCompleteContract) == set(
        OrderedCompleteContracts
    )
    assert set(EvaluationCountByCompleteContract.values()) == {1}
    assert Diagnostics["CertifiedPairCount"] == 0
    assert len(
        Resources.PhysicalComponentLocalInterfaceFactorProofCache
    ) == 1
    assert Resources.RejectedPhysicalComponentPortReservationSets == set()

def test_factor_unsat_proof_does_not_claim_full_option_materialization():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    First = _Assembly(Problem, Resources)
    Port = First.Plan.Ports[0]
    Resources.RejectedPhysicalComponentPortReservationSets.add(
        frozenset(((
            Port.Signal,
            "fabric-domain:" + Port.FabricDomainFingerprint,
        ),))
    )

    with pytest.raises(RoutingStageError) as Context:
        _Assembly(Problem, Resources)

    Failure = Context.value.Failure
    assert Failure.Reason == (
        RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    )
    assert Failure.Diagnostics["PortAssignmentProofComplete"]
    assert not Failure.Diagnostics[
        "PortOptionMaterializationComplete"
    ]
    assert Failure.Diagnostics["PortAssignmentUnsatProofBasis"] == (
        "complete-factor-search"
    )
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreSignals"
    ] == ["Alpha"]
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreMinimal"
    ]
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreDirectReuse"
    ]
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreProofBasis"
    ] == "complete-factor-domain-no-good"
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreCheckCount"
    ] == 0
    assert set(
        Failure.Diagnostics["PortDomainGenerationStatus"].values()
    ) == {"unvisited"}

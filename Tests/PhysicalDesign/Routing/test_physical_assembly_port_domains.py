"""Port Domains contracts for physical assembly."""

from ._physical_assembly_contracts import *


def test_local_contract_fingerprint_is_translation_stable_and_geometry_exact():
    def Port(Delta=(0, 0, 0), LocalZ=0):
        def Move(Position):
            return tuple(
                Position[Index] + Delta[Index]
                for Index in range(3)
            )

        LocalPath = tuple(map(
            Move,
            ((0, 7, 0), (1, 7, LocalZ)),
        ))
        GlobalPath = tuple(map(
            Move,
            ((1, 7, LocalZ), (2, 7, LocalZ)),
        ))
        return SimpleNamespace(
            Direction="output",
            FabricDomainFingerprint="domain",
            FabricAttachment=Move((0, 7, 0)),
            Attachment=Move((1, 7, 0)),
            OwnedTerminals=(Move((0, 7, 0)),),
            LocalPath=LocalPath,
            GlobalPath=GlobalPath,
            Claims=_Claims((*LocalPath, *GlobalPath)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    Base = BuildPhysicalPortLocalContractFingerprint(Port())
    Translated = BuildPhysicalPortLocalContractFingerprint(
        Port((30, 4, 12))
    )
    DifferentLocalSeam = BuildPhysicalPortLocalContractFingerprint(
        Port(LocalZ=1)
    )

    assert Base == Translated
    assert Base != DifferentLocalSeam

def test_persistent_boundary_iteration_restarts_after_new_unary_cut():
    Alpha = tuple(_BoundaryPort("Alpha", X) for X in (0, 10))
    Beta = tuple(_BoundaryPort("Beta", X) for X in (100, 110, 120))
    RejectedBySignal = {}
    Frontier = iter(IterPhysicalBoundaryPortAssignments(
        {"Alpha": Alpha, "Beta": Beta},
        RejectedGlobalApertureFingerprintsBySignal=RejectedBySignal,
    ))

    First = next(Frontier)
    FirstBySignal = {Value.Signal: Value for Value in First}
    RejectedBySignal["Alpha"] = {
        FirstBySignal["Alpha"].ApertureContractFingerprint,
    }
    Second = next(Frontier)
    SecondBySignal = {Value.Signal: Value for Value in Second}

    assert SecondBySignal["Alpha"].ApertureContractFingerprint not in (
        RejectedBySignal["Alpha"]
    )

def test_priority_innermost_boundary_iteration_changes_requested_axis():
    Assignments = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": tuple(
                _BoundaryPort("Alpha", X) for X in (20, 10)
            ),
            "Beta": tuple(
                _BoundaryPort("Beta", X) for X in (120, 110)
            ),
        },
        PriorityInnermostSignals=("Alpha",),
    ))

    Indices = _BoundaryAttachmentIndexSequence(Assignments)
    assert Indices[:2] == (
        (0, 0),
        (1, 0),
    )

def test_priority_innermost_boundary_iteration_is_rename_order_invariant():
    First = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": tuple(
                _BoundaryPort("Alpha", X) for X in (30, 20, 10)
            ),
            "Beta": tuple(
                _BoundaryPort("Beta", X) for X in (120, 110)
            ),
        },
        PriorityInnermostSignals=("Alpha",),
    ))
    RenamedAndReordered = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Zulu": tuple(
                _BoundaryPort("Zulu", X) for X in (110, 120)
            ),
            "Able": tuple(
                _BoundaryPort("Able", X) for X in (10, 30, 20)
            ),
        },
        PriorityInnermostSignals=("Able",),
    ))

    assert _BoundaryAttachmentIndexSequence(First) == (
        _BoundaryAttachmentIndexSequence(RenamedAndReordered)
    )

def test_priority_innermost_boundary_iteration_preserves_full_domain():
    Assignments = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": tuple(
                _BoundaryPort("Alpha", X) for X in (10, 20)
            ),
            "Beta": tuple(
                _BoundaryPort("Beta", X) for X in (110, 120, 130)
            ),
        },
        PriorityInnermostSignals=("Alpha",),
    ))

    Indices = _BoundaryAttachmentIndexSequence(Assignments)
    assert len(Indices) == 6
    assert len(set(Indices)) == 6
    assert set(Indices) == {
        (AlphaIndex, BetaIndex)
        for AlphaIndex in range(2)
        for BetaIndex in range(3)
    }

def test_priority_innermost_boundary_iteration_orders_multiple_hints():
    Assignments = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": tuple(
                _BoundaryPort("Alpha", X) for X in (10, 20)
            ),
            "Beta": tuple(
                _BoundaryPort("Beta", X) for X in (110, 120)
            ),
            "Gamma": tuple(
                _BoundaryPort("Gamma", X) for X in (210, 220)
            ),
        },
        PriorityInnermostSignals=("Alpha", "Beta"),
    ))

    assert _BoundaryAttachmentIndexSequence(Assignments)[:2] == (
        (0, 0, 0),
        (0, 1, 0),
    )

def test_component_keepout_projection_is_owned_by_physical_layer():
    ResourceGraph = _ResourceGraph()
    LayerZeroY = ResourceGraph.Technology.RoutingY(0, 0)
    CellsByLayer = BuildComponentKeepoutGuideCellsByLayer(
        ResourceGraph.BuildRouteClaims(((3, LayerZeroY, 5),)),
        ResourceGraph,
        MinimumPlacementY=0,
        LayerCount=2,
    )

    assert (3, 5) in CellsByLayer[0]
    assert (3, 5) not in CellsByLayer[1]

def test_component_egress_contract_reaches_each_envelope_side():
    Paths = BuildComponentEgressPaths(
        (5, 1, 5),
        TargetY=3,
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(10, 5, 10),
    )

    assert {Path[-1] for Path in Paths} == {
        (-1, 3, 5),
        (5, 3, -1),
        (5, 3, 11),
        (11, 3, 5),
    }
    assert all(Path[0] == (5, 1, 5) for Path in Paths)

def test_component_egress_contract_requires_complete_envelope_bounds():
    with pytest.raises(ValueError, match="both envelope bounds"):
        BuildComponentEgressPaths(
            (5, 1, 5),
            EnvelopeMinimum=(0, 0, 0),
        )

def test_component_egress_contract_rejects_noncardinal_direction():
    with pytest.raises(ValueError, match="must be cardinal"):
        BuildComponentEgressPaths((5, 1, 5), Directions=((1, 1),))

def test_sparse_component_keepout_projection_matches_exhaustive_fallback():
    ResourceGraph = RoutingResourceGraph(
        ActualBlocks=frozenset(),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
    )
    ExhaustiveGraph = RoutingResourceGraph(
        ActualBlocks=frozenset(),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
        Technology=replace(
            DefaultRedstoneRoutingTechnology,
            TechnologyVersion="test-exhaustive-equivalent-v1",
        ),
    )
    KeepoutClaims = ResourceGraph.BuildRouteClaims(
        ((3, 1, 5), (4, 2, 5))
    )
    Sparse = BuildComponentKeepoutGuideCellsByLayer(
        KeepoutClaims,
        ResourceGraph,
        MinimumPlacementY=0,
        LayerCount=2,
    )
    Exhaustive = BuildComponentKeepoutGuideCellsByLayer(
        KeepoutClaims,
        ExhaustiveGraph,
        MinimumPlacementY=0,
        LayerCount=2,
    )

    assert Sparse == Exhaustive

def test_placement_domain_is_exhausted_before_selecting_another_component():
    assert BuildRetainedComponentPlacementSearchDomain(
        ("placement-a", "placement-b"),
        MaximumComponentSelections=3,
    ) == (
        (0, 0, "placement-a"),
        (0, 1, "placement-b"),
        (1, 0, "placement-a"),
        (1, 1, "placement-b"),
        (2, 0, "placement-a"),
        (2, 1, "placement-b"),
    )

def test_retained_placement_resources_reuse_identity_across_components():
    Cache = {}
    BuildCount = 0

    def Build():
        nonlocal BuildCount
        BuildCount += 1
        return SimpleNamespace(
            ResourceGraph=object(),
            RawPortalGeometryCaches=("whole-design",),
        )

    First, FirstHit = ReuseRetainedPlacementRoutingResources(
        Cache,
        "placement",
        Build,
    )
    Second, SecondHit = ReuseRetainedPlacementRoutingResources(
        Cache,
        "placement",
        Build,
    )
    Other, OtherHit = ReuseRetainedPlacementRoutingResources(
        Cache,
        "other-placement",
        Build,
    )

    assert not FirstHit
    assert SecondHit
    assert not OtherHit
    assert First is Second
    assert First.ResourceGraph is Second.ResourceGraph
    assert Other is not First
    assert BuildCount == 2

def test_complete_ordinary_portal_cut_advances_component_placement():
    Plan = SimpleNamespace(
        Ports=(SimpleNamespace(Signal="ComponentPort"),),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.NoPinAccessPattern,
        Stage="NegotiatedDetailedRouting",
        AffectedNets=("OrdinaryGlobal",),
        Detail="no legal portal-aware route tree was found",
        Diagnostics={
            "RequestCount": 0,
            "AttemptedRequestCount": 0,
            "MandatoryPortalClaimPreScreen": {
                "Scope": "complete-design",
                "PreparedSignalCount": 17,
                "EmptyNetWidePortalTupleSignals": ["OrdinaryGlobal"],
            },
            "RoutedComponentGlobalHandoff": {"Enabled": True},
        },
    )

    assert IsComponentKeepoutGlobalFailure(Failure, Plan)
    assert not IsComponentKeepoutGlobalFailure(
        replace(Failure, AffectedNets=("ComponentPort",)),
        Plan,
    )
    WrappedFailure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentDetailedRoutingFailed,
        Stage="ComponentGlobalKeepoutAdmission",
        AffectedNets=("OrdinaryGlobal",),
        Diagnostics={
            "PhysicalAssemblyPlanFingerprint": "assembly-plan",
            "UnderlyingFailure": Failure.ToDictionary(),
        },
    )
    Feedback = BuildPhysicalComponentPlacementFeedback(WrappedFailure)
    assert Feedback is not None
    assert Feedback.RelocationSignals == ("OrdinaryGlobal",)
    assert Feedback.SourcePlanFingerprint == "assembly-plan"
    assert not IsComponentKeepoutGlobalFailure(
        replace(
            Failure,
            Diagnostics={
                **Failure.Diagnostics,
                "AttemptedRequestCount": 1,
            },
        ),
        Plan,
    )

def test_port_solver_does_not_run_local_realizability_before_plan():
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
    Events = []

    Assembly = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=Events.append,
    )

    assert Assembly.Plan.Ports
    SelectedEvent = next(
        Event
        for Event in Events
        if Event.get("Stage") == "physical-port-plan-selected"
    )
    assert SelectedEvent[
        "LocalRealizabilityCheckCountBySignal"
    ] == {}

def test_promoted_signal_domain_prunes_distinct_local_contracts():
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
    Options = MaterializePreparedPhysicalPortOptionDomains(
        Preparation,
        Resources,
        ("Alpha",),
    )["Alpha"]
    assert len(Options) >= 2
    First, Second = Options[:2]
    assert BuildPhysicalPortLocalContractFingerprint(First) != (
        BuildPhysicalPortLocalContractFingerprint(Second)
    )

    PortSolverCacheKey = (
        ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
            Preparation.DomainFingerprint,
        )
    )
    SignalDomainKey = (
        "Alpha",
        "local-signal-domain:" + PortSolverCacheKey,
    )
    PromotedClause = frozenset((SignalDomainKey,))
    assert PromotedClause <= BuildPhysicalPortNoGoodKeys(
        First,
        PortSolverCacheKey,
    )
    assert PromotedClause <= BuildPhysicalPortNoGoodKeys(
        Second,
        PortSolverCacheKey,
    )
    Resources.RejectedPhysicalComponentPortReservationSets.add(
        PromotedClause
    )

    with pytest.raises(RoutingStageError) as Raised:
        SolvePreparedPhysicalComponentPortFactorDomain(
            Preparation,
            Resources,
        )

    Failure = Raised.value.Failure
    assert Failure.Reason == (
        RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    )
    assert Failure.Diagnostics["PortAssignmentUnsatCoreDirectReuse"] is True
    assert Failure.Diagnostics["PortAssignmentUnsatCoreProofBasis"] == (
        "complete-factor-domain-no-good"
    )
    assert Failure.Diagnostics["PortAssignmentUnsatCoreNoGoodKeys"] == [
        list(SignalDomainKey)
    ]
    assert Failure.Diagnostics["PortOptionMaterializationComplete"] is False
    assert Failure.Diagnostics["SeamFactorExpansionCount"] == 0

def test_physical_plan_binds_exact_port_before_local_compilation():
    Assembly = _Assembly(_Problem())

    assert Assembly.Plan.Complete
    assert Assembly.Problem.Interface is not None
    assert (
        Assembly.Problem.Interface.PhysicalAssemblyPlanFingerprint
        == Assembly.Plan.PlanFingerprint
    )
    assert len(Assembly.Plan.Ports) == 1
    assert Assembly.Plan.Ports[0].Attachment == (
        Assembly.Plan.Ports[0].LocalPath[-1]
    )
    assert Assembly.Plan.AccessCertificateFingerprint
    assert Assembly.Plan.StageOrder == (
        "PhysicalBoundaryPlanning",
        "AuthoritativeGlobalReserve",
        "LocalSupportBinding",
        "ClosedComponentCompilation",
        "AuthoritativeDetailedRouting",
    )
    assert all(
        len(Domain.Candidates) == 1
        for Domain in Assembly.Problem.OwnedTerminalDomains
    )
    assert (
        Assembly.Plan.ToDictionary()[
            "ImplicitForeignTransitDomainCount"
        ]
        == 0
    )
    Assembly = _BindAssemblyForLocalCompilation(Assembly)
    Result = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )
    assert Result.Feasible and Result.Template is not None
    assert Result.Template.ExportedPorts == ((
        "Alpha",
        Assembly.Plan.Ports[0].Attachment,
    ),)

def test_capacity_repair_geometry_focus_survives_non_refining_failure():
    Authoritative = SimpleNamespace(Signals=("Alpha", "Beta", "Gamma"))
    ExactChild = SimpleNamespace(Signals=("Beta", "Gamma"))
    FreshChild = SimpleNamespace(Signals=("Alpha", "Gamma"))

    assert SelectCapacityRepairGeometryConstraint(
        None,
        ExactChild,
        Authoritative,
    ) is ExactChild
    assert SelectCapacityRepairGeometryConstraint(
        FreshChild,
        ExactChild,
        Authoritative,
    ) is FreshChild
    assert SelectCapacityRepairGeometryConstraint(
        None,
        None,
        Authoritative,
    ) is Authoritative

def test_capacity_repair_geometry_focus_rejects_unrelated_inherited_signal():
    Pair = SimpleNamespace(
        ProofFingerprint="pair",
        RelocationSignals=("Carry1", "NandNet36"),
    )
    Unrelated = SimpleNamespace(
        ProofFingerprint="unrelated",
        RelocationSignals=("A0",),
    )

    assert SelectCapacityRepairGeometryFocus(
        Unrelated,
        None,
        Pair,
        Pair.RelocationSignals,
    ) is Pair
    assert SelectCapacityRepairGeometryFocus(
        None,
        Unrelated,
        Pair,
        Pair.RelocationSignals,
    ) is Pair

def test_physical_plan_defers_local_realizability_to_closed_compiler():
    Problem = _Problem()
    SourceDomain, TargetDomain = Problem.OwnedTerminalDomains
    Rejected = replace(
        SourceDomain.Candidates[0],
        CandidateFingerprint="00-local-unrealizable",
    )
    Accepted = replace(
        SourceDomain.Candidates[0],
        CandidateFingerprint="10-local-realizable",
        Attachment=Problem.Fabric.Nodes[1],
        Path=(
            SourceDomain.Terminal,
            Problem.Fabric.Nodes[1],
        ),
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                SourceDomain,
                Candidates=(Rejected, Accepted),
            ),
            TargetDomain,
        ),
    )

    Assembly = _Assembly(Problem)

    assert Assembly.Plan.Ports[0].OwnedCandidateFingerprints == ()
    assert tuple(
        Candidate.CandidateFingerprint
        for Candidate in Assembly.Problem.OwnedTerminalDomains[0].Candidates
    ) == ("00-local-unrealizable", "10-local-realizable")

def test_physical_port_selection_is_rename_and_translation_invariant():
    Original = _Assembly(_Problem("Alpha"))
    Renamed = _Assembly(_Problem("Renamed"))
    Delta = (31, 0, 13)
    Translated = _Assembly(_Problem("Alpha", Delta))

    assert (
        Original.Plan.Ports[0].ReservationFingerprint
        == Renamed.Plan.Ports[0].ReservationFingerprint
        == Translated.Plan.Ports[0].ReservationFingerprint
    )
    assert tuple(
        Translated.Plan.Ports[0].Attachment[Index] - Delta[Index]
        for Index in range(3)
    ) == Original.Plan.Ports[0].Attachment
    assert (
        Translated.Plan.InterfaceFingerprint
        == Original.Plan.InterfaceFingerprint
    )

def test_component_compile_rejects_changed_physical_identity():
    Assembly = _Assembly(_Problem())
    Changed = replace(
        Assembly.Plan,
        PlanFingerprint="changed-plan",
    )

    with pytest.raises(
        ValueError,
        match="physical assembly identities differ",
    ):
        CompileClosedComponent(
            Assembly.Problem,
            AssemblyPlan=Changed,
            DiscoveryVariantLimit=None,
        )

def test_component_compile_rejects_local_feedback_no_goods():
    Assembly = _BindAssemblyForLocalCompilation(_Assembly(_Problem()))

    with pytest.raises(
        ValueError,
        match="cannot reopen its immutable assembly plan",
    ):
        CompileClosedComponent(
            Assembly.Problem,
            AssemblyPlan=Assembly.Plan,
            ForbiddenExportPortsBySignal={
                "Alpha": (
                    Assembly.Plan.Ports[0].Attachment,
                ),
            },
            DiscoveryVariantLimit=None,
        )

def test_completed_physical_template_cache_reuses_renamed_translation():
    ComponentCache._CompletedComponentTemplateCache.clear()
    Original = _BindAssemblyForLocalCompilation(
        _Assembly(_Problem("Original"))
    )
    First = CompileClosedComponent(
        Original.Problem,
        AssemblyPlan=Original.Plan,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible and First.Template is not None
    assert not First.Diagnostics["CompletedTemplateCacheHit"]

    Delta = (31, 0, 13)
    Renamed = _BindAssemblyForLocalCompilation(
        _Assembly(_Problem("Renamed", Delta))
    )
    # The assembly/interface fingerprint is an opaque exact-plan identity.
    # Structural template reuse is governed by the normalized physical port
    # contract, so an equivalent translated plan must not depend on this ID.
    RenamedPlan = replace(
        Renamed.Plan,
        InterfaceFingerprint="translated-opaque-interface",
    )
    RenamedInterface = replace(
        Renamed.Problem.Interface,
        InterfaceFingerprint="translated-opaque-interface",
    )
    Renamed = replace(
        Renamed,
        Plan=RenamedPlan,
        Problem=replace(
            Renamed.Problem,
            Interface=RenamedInterface,
            PhysicalAssemblyPlan=RenamedPlan,
        ),
    )
    Second = CompileClosedComponent(
        Renamed.Problem,
        AssemblyPlan=Renamed.Plan,
        DiscoveryVariantLimit=None,
    )

    assert Second.Feasible and Second.Template is not None
    assert Second.Diagnostics["CompletedTemplateCacheHit"]
    assert (
        Second.Diagnostics["CompletedTemplateTranslationDelta"]
        == list(Delta)
    )
    assert {Net.Signal for Net in Second.Template.Nets} == {
        "Renamed"
    }
    assert Second.Template.ExportedPorts == ((
        "Renamed",
        Renamed.Plan.Ports[0].Attachment,
    ),)

def test_local_only_net_portfolio_compiles_exhaustively_without_template_search():
    Problem = _Assembly(_Problem()).Problem
    PortfolioCache = {}
    DiscoveryCache = {}
    WorkEvents = []

    Compiled = CompileCompleteComponentNetVariantPortfolio(
        Problem,
        "Alpha",
        DeadlineSeconds=1.0,
        WorkCheck=WorkEvents.append,
        VariantPortfolioCache=PortfolioCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )

    assert Compiled.Complete
    assert Compiled.Status == "complete"
    assert Compiled.Variants
    assert Compiled.Diagnostics["LocalOnly"] is True
    assert Compiled.Diagnostics["TemplateSearchEntered"] is False
    assert WorkEvents
    assert DiscoveryCache == {}
    assert PortfolioCache

    Reused = CompileCompleteComponentNetVariantPortfolio(
        Problem,
        "Alpha",
        DeadlineSeconds=0.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )
    assert Reused.Complete
    assert Reused.Status == "complete-cached"
    assert Reused.ExpansionCount == 0
    assert Reused.Diagnostics["PortfolioCacheHit"] is True

def test_local_only_net_portfolio_resumes_but_does_not_cache_partial_domain():
    Problem = _Assembly(_Problem()).Problem
    PortfolioCache = {}
    DiscoveryCache = {}
    ConstructionCache = {}
    ClaimsCache = {}

    Incomplete = CompileCompleteComponentNetVariantPortfolio(
        replace(Problem, MaximumWork=0),
        "Alpha",
        DeadlineSeconds=1.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantConstructionCache=ConstructionCache,
        RouteClaimsConstructionCache=ClaimsCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )

    assert not Incomplete.Complete
    assert Incomplete.Status == "incomplete"
    assert PortfolioCache == {}
    assert DiscoveryCache

    Completed = CompileCompleteComponentNetVariantPortfolio(
        replace(Problem, MaximumWork=250_000),
        "Alpha",
        DeadlineSeconds=1.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantConstructionCache=ConstructionCache,
        RouteClaimsConstructionCache=ClaimsCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )

    assert Completed.Complete
    assert Completed.Status == "complete"
    assert PortfolioCache
    assert DiscoveryCache == {}

def test_multi_contract_portfolios_equal_exact_subset_and_superset_domains():
    Problem, OriginalPort, AlternatePort, SupersetPort = (
        _MultiPortfolioFixture()
    )
    Ports = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (OriginalPort, AlternatePort, SupersetPort)
    }

    Shared = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=1.0,
    )

    assert Shared.Complete
    assert Shared.Diagnostics["SolverCallCount"] == 1
    ExactByContract = {
        Contract: _ExactPortfolioForPort(Problem, Port)
        for Contract, Port in Ports.items()
    }
    ExactBuildCount = sum(
        Portfolio.Diagnostics["VariantDiagnosticsBySignal"]["Alpha"][
            "NetVariantBuildCount"
        ]
        for Portfolio in ExactByContract.values()
    )
    assert Shared.NetVariantBuildCount == 4
    assert Shared.NetVariantBuildCount < ExactBuildCount == 6
    for Contract, Port in Ports.items():
        Exact = ExactByContract[Contract]
        assert Shared.Portfolios[Contract].Complete
        assert Shared.Portfolios[Contract].Variants == Exact.Variants
    OriginalContract = BuildPhysicalPortLocalContractFingerprint(OriginalPort)
    AlternateContract = BuildPhysicalPortLocalContractFingerprint(AlternatePort)
    SupersetContract = BuildPhysicalPortLocalContractFingerprint(SupersetPort)
    assert Shared.Portfolios[OriginalContract].Diagnostics[
        "AccessCombinationCount"
    ] == 1
    assert Shared.Portfolios[AlternateContract].Diagnostics[
        "AccessCombinationCount"
    ] == 1
    assert Shared.Portfolios[SupersetContract].Diagnostics[
        "AccessCombinationCount"
    ] == 4

def test_multi_contract_portfolios_keep_exact_local_paths_separate():
    Problem, OriginalPort, _AlternatePort, _SupersetPort = (
        _MultiPortfolioFixture()
    )
    OtherPathPort = replace(
        OriginalPort,
        LocalPath=((2, 7, 0), (1, 7, 0), (0, 7, 0)),
        ReservationFingerprint="other-path",
    )
    Ports = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (OriginalPort, OtherPathPort)
    }

    Shared = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=1.0,
    )

    assert Shared.Complete
    for Contract, Port in Ports.items():
        assert Shared.Portfolios[Contract].Variants == (
            _ExactPortfolioForPort(Problem, Port).Variants
        )
        assert all(
            frozenset(Port.LocalPath) <= Variant.Nodes
            for Variant in Shared.Portfolios[Contract].Variants
        )

def test_multi_contract_empty_domain_is_complete_and_does_not_leak_union():
    Problem, OriginalPort, _AlternatePort, _SupersetPort = (
        _MultiPortfolioFixture()
    )
    EmptyPort = replace(
        OriginalPort,
        FabricDomainFingerprint="empty-domain",
        OwnedCandidateFingerprints=("missing-candidate",),
        OwnedAccessCandidates=(),
        ReservationFingerprint="empty",
    )
    Ports = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (OriginalPort, EmptyPort)
    }

    Shared = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=1.0,
    )

    EmptyContract = BuildPhysicalPortLocalContractFingerprint(EmptyPort)
    assert Shared.Complete
    assert Shared.Portfolios[EmptyContract].Complete
    assert Shared.Portfolios[EmptyContract].Variants == ()

def test_multi_contract_interruption_resumes_without_publishing_partial_cache():
    Problem, OriginalPort, AlternatePort, _SupersetPort = (
        _MultiPortfolioFixture()
    )
    Ports = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (OriginalPort, AlternatePort)
    }
    PortfolioCache = {}
    DiscoveryCache = {}

    Incomplete = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=0.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )
    assert not Incomplete.Complete
    assert PortfolioCache == {}
    assert DiscoveryCache

    Completed = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        dict(reversed(tuple(Ports.items()))),
        DeadlineSeconds=1.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )
    Fresh = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=1.0,
    )
    assert Completed.Complete
    assert Completed.DomainFingerprint == Fresh.DomainFingerprint
    assert Completed.PortfoliosByContract == Fresh.PortfoliosByContract
    assert PortfolioCache
    assert DiscoveryCache == {}

def test_net_portfolio_static_context_excludes_exact_port_contract():
    Problem = _Assembly(_Problem()).Problem
    Context = BuildCompleteComponentNetPortfolioStaticContext(
        Problem,
        "Alpha",
    )
    Port = Problem.Interface.PhysicalPortReservations[0]
    ChangedPort = replace(
        Port,
        LocalPath=(*Port.LocalPath, (9, 7, 0)),
    )
    ChangedProblem = replace(
        Problem,
        Interface=replace(
            Problem.Interface,
            PhysicalPortReservations=(ChangedPort,),
        ),
    )
    ChangedContext = BuildCompleteComponentNetPortfolioStaticContext(
        ChangedProblem,
        "Alpha",
    )

    assert (
        Context.StaticStructuralFingerprint
        == ChangedContext.StaticStructuralFingerprint
    )
    BasePortfolio = GetCachedCompleteComponentNetVariantPortfolio(
        Problem,
        "Alpha",
        {},
        StaticContext=Context,
    )
    ChangedPortfolio = GetCachedCompleteComponentNetVariantPortfolio(
        ChangedProblem,
        "Alpha",
        {},
        StaticContext=Context,
    )
    assert BasePortfolio.DomainFingerprint != (
        ChangedPortfolio.DomainFingerprint
    )

def test_relaxed_complete_two_port_core_prunes_exact_reservation_pair(
    monkeypatch,
):
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="assignment",
        Channels=(
            SimpleNamespace(Signal="PortA", RouteCandidateId="route-a"),
            SimpleNamespace(Signal="PortB", RouteCandidateId="route-b"),
        ),
        Ports=(
            SimpleNamespace(
                Signal="PortA",
                Direction="input",
                OwnedTerminals=((0, 1, 0),),
                OwnedAccessCandidates=(),
                Capacity=1,
                ReservationFingerprint="reservation-a",
                FabricDomainFingerprint="fabric-a",
                FabricAttachment=(0, 1, 0),
                Attachment=(1, 1, 0),
                LocalPath=((0, 1, 0), (1, 1, 0)),
                OwnedCandidateFingerprints=("access-a",),
            ),
            SimpleNamespace(
                Signal="PortB",
                Direction="input",
                OwnedTerminals=((0, 1, 2),),
                OwnedAccessCandidates=(),
                Capacity=1,
                ReservationFingerprint="reservation-b",
                FabricDomainFingerprint="fabric-b",
                FabricAttachment=(0, 1, 2),
                Attachment=(1, 1, 2),
                LocalPath=((0, 1, 2), (1, 1, 2)),
                OwnedCandidateFingerprints=("access-b",),
            ),
            SimpleNamespace(
                Signal="PortC",
                Direction="input",
                OwnedTerminals=((0, 1, 4),),
                OwnedAccessCandidates=(),
                Capacity=1,
                ReservationFingerprint="reservation-c",
                FabricDomainFingerprint="fabric-c",
                FabricAttachment=(0, 1, 4),
                Attachment=(1, 1, 4),
                LocalPath=((0, 1, 4), (1, 1, 4)),
                OwnedCandidateFingerprints=("access-c",),
            ),
        ),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        ForbiddenPhysicalComponentGlobalCandidateSets=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
    )
    Solve = ComponentRoutingSolveResult(
        Status="architectural-unsatisfiable",
        ProofFingerprint="proof",
        Diagnostics={
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreSignals": ["PortA", "PortB"],
            "LocalUnsatCoreFingerprint": "core",
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": "relaxed-proof",
            "GlobalRelaxedLocalDomainFingerprint": "relaxed-domain",
            "GlobalRelaxedLocalUnsatCoreSignals": ["PortA", "PortB"],
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
        },
    )
    monkeypatch.setattr(
        ComponentNoGoods,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _Problem: "relaxed-domain",
    )

    Diagnostics = RecordPhysicalComponentLocalCompilationNoGood(
        Solve,
        Plan,
        SimpleNamespace(),
        Resources,
        Problem=SimpleNamespace(PhysicalAssemblyPlan=Plan),
    )

    assert Diagnostics["GlobalRelaxedLocalProofComplete"] is True
    assert Diagnostics["NoGoodScope"] == (
        "global-relaxed-local-port-core"
    )
    assert Diagnostics["NoGoodSignals"] == ["PortA", "PortB"]
    assert Diagnostics["GlobalRelaxedLocalUnsatCoreSignals"] == [
        "PortA",
        "PortB",
    ]
    assert Diagnostics["GlobalRelaxedLocalCoreComplete"] is True
    assert Diagnostics["GlobalRelaxedLocalProofFingerprint"] == (
        "relaxed-proof"
    )
    assert Diagnostics["GlobalRelaxedLocalDomainFingerprint"] == (
        "relaxed-domain"
    )
    ExpectedReservationKeys = {
        Port.Signal: (
            ComponentValidation.BuildPhysicalPortLocalContractFingerprint(Port)
        )
        for Port in Plan.Ports
    }
    assert Diagnostics["NoGoodReservationKeys"] == [
        ["PortA", ExpectedReservationKeys["PortA"]],
        ["PortB", ExpectedReservationKeys["PortB"]],
    ]
    assert Diagnostics["RejectedPhysicalAssemblyPlanFingerprint"] == (
        "physical-plan"
    )
    assert Diagnostics["PreferredRetainedGlobalContracts"] == {
        Port.Signal: BuildPhysicalPortGlobalContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert (
        Resources.PreferredPhysicalComponentGlobalContractsBySignal
        == Diagnostics["PreferredRetainedGlobalContracts"]
    )
    assert "RejectedPortAssignmentFingerprint" not in Diagnostics
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", ExpectedReservationKeys["PortA"]),
            ("PortB", ExpectedReservationKeys["PortB"]),
        )),
    }
    assert not Resources.RejectedPhysicalComponentPortReservationsBySignal
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert not Resources.ForbiddenPhysicalComponentGlobalCandidateSets
    assert Resources.RejectedPhysicalComponentAssemblyPlanFingerprints == {
        "physical-plan"
    }
    assert all(
        Signal != "PortC"
        for RejectedSet
        in Resources.RejectedPhysicalComponentPortReservationSets
        for Signal, _Fingerprint in RejectedSet
    )

def test_relaxed_owned_tree_frontier_prunes_complete_signal_domain(
    monkeypatch,
):
    Port = SimpleNamespace(
        Signal="PortA",
        Direction="output",
        OwnedTerminals=((0, 1, 0),),
        OwnedAccessCandidates=(),
        Capacity=1,
        ReservationFingerprint="reservation-a",
        FabricDomainFingerprint="fabric-a",
        FabricAttachment=(0, 1, 0),
        Attachment=(1, 1, 0),
        LocalPath=((0, 1, 0), (1, 1, 0)),
        GlobalPath=((1, 1, 0), (2, 1, 0)),
        OwnedCandidateFingerprints=("access-a",),
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="assignment",
        Channels=(),
        Ports=(Port,),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        ForbiddenPhysicalComponentGlobalCandidateSets=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
        PreparedPhysicalComponentPortFactorDomain=SimpleNamespace(
            Complete=True,
            Feasible=True,
            DomainFingerprint="prepared-domain",
        ),
    )
    Solve = ComponentRoutingSolveResult(
        Status="architectural-unsatisfiable",
        ProofFingerprint="proof",
        Diagnostics={
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreSignals": ["PortA"],
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": "relaxed-proof",
            "GlobalRelaxedLocalDomainFingerprint": "relaxed-domain",
            "GlobalRelaxedLocalUnsatCoreSignals": ["PortA"],
            "GlobalRelaxedLocalUnsatCoreKind": (
                "tree-frontier-empty-owned-signal-domain"
            ),
            "LocalUnsatCoreProjectionFingerprint": "owned-domain",
        },
    )
    monkeypatch.setattr(
        ComponentNoGoods,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _Problem: "relaxed-domain",
    )

    Diagnostics = RecordPhysicalComponentLocalCompilationNoGood(
        Solve,
        Plan,
        SimpleNamespace(),
        Resources,
        Problem=SimpleNamespace(PhysicalAssemblyPlan=Plan),
    )

    PortSolverCacheKey = (
        ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
            "prepared-domain"
        )
    )
    SignalDomainKey = "local-signal-domain:" + PortSolverCacheKey
    assert Diagnostics["NoGoodScope"] == (
        "global-relaxed-owned-signal-domain"
    )
    assert Diagnostics["NoGoodReservationKeys"] == [
        ["PortA", SignalDomainKey]
    ]
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((("PortA", SignalDomainKey),))
    }
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints

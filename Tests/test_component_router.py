from dataclasses import replace
from types import SimpleNamespace
import unittest

from Compiler.Routing.ComponentRouter import (
    ApplyRoutedComponentGlobalProfiles,
    BuildCoalescedComponentAccessCandidates,
    BuildClosedComponentInterface,
    BuildDeclaredComponentFeedthroughDomains,
    BuildComponentForeignTransitDomains,
    BuildComponentRoutingProblem,
    BuildComponentRoutingFabric,
    BuildExactComponentPortRealizabilityContext,
    BuildExactComponentPortRealizabilityFingerprint,
    ClearStructuralPortRealizabilityCache,
    ComponentClaimsCompatibleForOwners,
    ComponentClaimsConflict,
    EvaluateExactComponentPortRealizability,
    FindCompleteComponentNetUnsatSubset,
    MaterializeRoutedComponentTemplate,
    PreserveRoutedComponentForeignEscapes,
    PruneDominatedComponentAccessCandidates,
    SelectComponentIncidentSignals,
    SolveComponentRoutingProblem,
    ValidateRoutedComponentHandoff,
    _PlanTreeRepeaters,
)
from Compiler.Routing.ComponentPipeline import CompileClosedComponent
from Compiler.Routing.Models import (
    ClosedComponentInterface,
    ComponentFeedthroughContract,
    ComponentForeignTransitDomain,
    ComponentInterfacePort,
    ComponentRoutingProblem,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    PhysicalComponentPortReservation,
    RoutedComponentNet,
)
from Compiler.Routing.ResourceGraph import (
    PinAccessPortal,
    RoutingResourceClaims,
)
from Compiler.Routing.ChannelPlanner import NetRoutingProfile
from Compiler.Placement.Geometry import PlacedDesign


def _Claims(*Nodes):
    Nodes = frozenset(Nodes)
    return RoutingResourceClaims(
        WireCells=Nodes,
        SupportCells=frozenset(
            (X, Y - 1, Z) for X, Y, Z in Nodes
        ),
        ElectricalCells=Nodes,
    )


def _Channel(*Lanes):
    return SimpleNamespace(
        PhysicalModel="test-tree-fabric",
        AffectedClusters=(0, 1),
        AffectedSignals=("Alpha",),
        Lanes=tuple(
            SimpleNamespace(
                Cells=tuple(Cells),
                IngressNodes=(Cells[0], Cells[-1]),
            )
            for Cells in Lanes
        ),
    )


def _Candidate(Path):
    return ComponentTerminalAccessCandidate(
        CandidateFingerprint=str(tuple(Path)),
        Attachment=Path[-1],
        Path=tuple(Path),
        Claims=_Claims(*Path),
    )


def test_tree_repeater_dp_reuses_unchanged_subtrees():
    Root = (0, 7, 0)
    SharedBranch = tuple((0, 7, Z) for Z in range(4))
    FirstBranch = tuple((X, 7, 0) for X in range(4))
    ExtendedBranch = (*FirstBranch, (4, 7, 0))

    def Tree(Branch):
        Nodes = frozenset((*SharedBranch, *Branch))
        Edges = frozenset(
            tuple(sorted((First, Second)))
            for Path in (SharedBranch, Branch)
            for First, Second in zip(Path, Path[1:])
        )
        return Nodes, Edges

    Cache = {}
    Statistics = {}
    FirstNodes, FirstEdges = Tree(FirstBranch)
    _PlanTreeRepeaters(
        FirstNodes,
        FirstEdges,
        Root,
        15,
        SubproblemCache=Cache,
        CacheStatistics=Statistics,
    )
    InitialHits = Statistics.get("HitCount", 0)
    SecondNodes, SecondEdges = Tree(ExtendedBranch)
    Cached = _PlanTreeRepeaters(
        SecondNodes,
        SecondEdges,
        Root,
        15,
        SubproblemCache=Cache,
        CacheStatistics=Statistics,
    )
    Uncached = _PlanTreeRepeaters(
        SecondNodes,
        SecondEdges,
        Root,
        15,
    )

    assert Cached == Uncached
    assert Statistics.get("HitCount", 0) > InitialHits


def _Portal(Signal, Terminal, Path, PortalId):
    Path = tuple(Path)
    return PinAccessPortal(
        PortalId=PortalId,
        Signal=Signal,
        Terminal=Terminal,
        Layer=0,
        Path=Path,
        Edges=frozenset(
            (First, Second) if First <= Second else (Second, First)
            for First, Second in zip(Path, Path[1:])
        ),
        Claims=_Claims(*Path),
        Length=len(Path),
        BendCount=0,
        ViaCount=0,
        Cost=0,
    )


def _Domain(Signal, Terminal, Role, *Candidates):
    return ComponentTerminalAccessDomain(
        Signal=Signal,
        Terminal=Terminal,
        TerminalRole=Role,
        TerminalFingerprint=f"{Role}-{Terminal}",
        Candidates=tuple(Candidates),
    )


def _Problem(
    Signal="Alpha",
    *,
    Fabric=None,
    Foreign=(),
    External=(),
    MaximumPowerDistance=15,
):
    Fabric = Fabric or BuildComponentRoutingFabric(
        _Channel(((0, 7, 0), (1, 7, 0), (2, 7, 0)))
    )
    Source = _Domain(
        Signal,
        (0, 7, 0),
        "source",
        _Candidate(((0, 7, 0),)),
    )
    Target = _Domain(
        Signal,
        (2, 7, 0),
        "target",
        _Candidate(((2, 7, 0),)),
    )
    return ComponentRoutingProblem(
        ProblemFingerprint="structural-problem",
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
        SelectedClusters=(0, 1),
        ComponentSignals=(Signal,),
        LocalClaims=(),
        Fabric=Fabric,
        OwnedTerminalDomains=(Source, Target),
        ExternalContinuationTerminals=tuple(External),
        ForeignEscapeDomains=tuple(Foreign),
        MaximumPowerDistance=MaximumPowerDistance,
        DomainComplete=True,
        MaximumWork=10_000,
    )


def _Net(Signal, Position):
    Claims = _Claims(Position)
    return RoutedComponentNet(
        Signal=Signal,
        Root=Position,
        Nodes=frozenset((Position,)),
        Edges=frozenset(),
        WireCells=frozenset((Position,)),
        SupportCells=Claims.SupportCells,
        Repeaters=(),
        Claims=Claims,
        CoveredTerminals=(Position,),
        ExportedPorts=(),
        NetFingerprint=f"{Signal}-{Position}",
    )


def test_complete_net_subset_proves_monotone_capacity_unsat():
    First = _Net("First", (0, 7, 0))
    Second = _Net("Second", (0, 7, 0))

    Core = FindCompleteComponentNetUnsatSubset({
        "First": (First,),
        "Second": (Second,),
    })

    assert set(Core) == {"First", "Second"}
    assert FindCompleteComponentNetUnsatSubset({
        "First": (First,),
        "Second": (_Net("Second", (20, 7, 20)),),
    }) == ()
    assert FindCompleteComponentNetUnsatSubset(
        {"First": (First,), "Second": (Second,)},
        Advance=lambda: False,
    ) is None


def test_exact_port_realizability_uses_powered_local_net_primitive():
    Problem = _Problem(
        External=(("Alpha", (10, 7, 0), "target"),),
    )
    Domains = Problem.OwnedTerminalDomains
    Candidates = tuple(
        Domain.Candidates[0] for Domain in Domains
    )
    LocalPath = ((2, 7, 0), (3, 7, 0))
    Cache = {}

    First = EvaluateExactComponentPortRealizability(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=Candidates,
        LocalPath=LocalPath,
        RealizabilityCache=Cache,
    )
    Renamed = _Problem(
        "Renamed",
        External=(("Renamed", (10, 7, 0), "target"),),
    )
    RenamedDomains = Renamed.OwnedTerminalDomains
    Second = EvaluateExactComponentPortRealizability(
        Renamed,
        Signal="Renamed",
        Domains=RenamedDomains,
        Candidates=tuple(
            Domain.Candidates[0] for Domain in RenamedDomains
        ),
        LocalPath=LocalPath,
        RealizabilityCache=Cache,
    )

    assert First.Realizable
    assert First.NetFingerprint
    assert not First.Diagnostics["CacheHit"]
    assert Second.Realizable
    assert Second.ContractFingerprint == First.ContractFingerprint
    assert Second.Diagnostics["CacheHit"]
    assert (
        BuildExactComponentPortRealizabilityFingerprint(
            Problem,
            Signal="Alpha",
            Domains=Domains,
            Candidates=Candidates,
            LocalPath=LocalPath,
        )
        == First.ContractFingerprint
    )
    OwnChannelFingerprint = (
        BuildExactComponentPortRealizabilityFingerprint(
            Problem,
            Signal="Alpha",
            Domains=Domains,
            Candidates=Candidates,
            LocalPath=LocalPath,
            ReservedClaimsBySignal=((
                "Alpha",
                _Claims((30, 7, 0)),
            ),),
        )
    )
    ForeignChannelFingerprint = (
        BuildExactComponentPortRealizabilityFingerprint(
            Problem,
            Signal="Alpha",
            Domains=Domains,
            Candidates=Candidates,
            LocalPath=LocalPath,
            ReservedClaimsBySignal=((
                "Foreign",
                _Claims((30, 7, 0)),
            ),),
        )
    )
    assert OwnChannelFingerprint == First.ContractFingerprint
    assert ForeignChannelFingerprint != First.ContractFingerprint


def test_exact_port_realizability_reuses_structural_predicate_across_rename():
    ClearStructuralPortRealizabilityCache()
    FirstProblem = _Problem(
        External=(("Alpha", (10, 7, 0), "target"),),
    )
    FirstDomains = FirstProblem.OwnedTerminalDomains
    First = EvaluateExactComponentPortRealizability(
        FirstProblem,
        Signal="Alpha",
        Domains=FirstDomains,
        Candidates=tuple(
            Domain.Candidates[0] for Domain in FirstDomains
        ),
        LocalPath=((2, 7, 0), (3, 7, 0)),
        UseStructuralCache=True,
    )
    RenamedProblem = _Problem(
        "Renamed",
        External=(("Renamed", (10, 7, 0), "target"),),
    )
    RenamedDomains = RenamedProblem.OwnedTerminalDomains
    Second = EvaluateExactComponentPortRealizability(
        RenamedProblem,
        Signal="Renamed",
        Domains=RenamedDomains,
        Candidates=tuple(
            Domain.Candidates[0] for Domain in RenamedDomains
        ),
        LocalPath=((2, 7, 0), (3, 7, 0)),
        UseStructuralCache=True,
    )

    assert First.Realizable
    assert not First.Diagnostics["CacheHit"]
    assert Second.Realizable
    assert Second.ContractFingerprint == First.ContractFingerprint
    assert Second.Diagnostics["CacheHit"]
    assert Second.Diagnostics["CacheScope"] == "structural"
    ClearStructuralPortRealizabilityCache()


def test_exact_port_context_factorizes_candidate_identity_across_seams():
    Problem = _Problem(
        External=(("Alpha", (10, 7, 0), "target"),),
    )
    Domains = Problem.OwnedTerminalDomains
    Candidates = tuple(
        Domain.Candidates[0] for Domain in Domains
    )
    Context = BuildExactComponentPortRealizabilityContext(
        Problem,
        Signal="Alpha",
    )

    First = BuildExactComponentPortRealizabilityFingerprint(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=Candidates,
        LocalPath=((2, 7, 0), (3, 7, 0)),
        Context=Context,
    )
    Repeated = BuildExactComponentPortRealizabilityFingerprint(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=Candidates,
        LocalPath=((2, 7, 0), (3, 7, 0)),
        Context=Context,
    )
    OtherSeam = BuildExactComponentPortRealizabilityFingerprint(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=Candidates,
        LocalPath=((2, 7, 0), (2, 7, 1)),
        Context=Context,
    )

    assert First == Repeated
    assert OtherSeam != First
    assert len(Context.CandidateIdentityCache) == 1
    assert len(Context.LocalPathIdentityCache) == 2


def test_component_access_coalesces_adjacent_terminal_into_shared_trunk():
    class HorizontalResourceGraph:
        def BuildPrimitive(self, First, Second):
            return (
                object()
                if sum(
                    abs(First[Index] - Second[Index])
                    for Index in range(3)
                ) == 1
                else None
            )

        def BuildRouteClaims(self, Nodes):
            return _Claims(*Nodes)

    Trunk = _Candidate((
        (0, 1, 0),
        (0, 1, 1),
        (0, 1, 2),
    ))
    Independent = _Candidate((
        (2, 1, 0),
        (2, 1, 1),
        (2, 1, 2),
        (1, 1, 2),
        (0, 1, 2),
    ))

    Values = BuildCoalescedComponentAccessCandidates(
        Independent,
        (Trunk,),
        ResourceGraph=HorizontalResourceGraph(),
        ExistingNodes=frozenset(Trunk.Path),
    )

    assert Values
    assert Values[0].Path[:3] == (
        (2, 1, 0),
        (1, 1, 0),
        (0, 1, 0),
    )
    assert Values[0].Path[-1] == Trunk.Attachment
    assert Values[0].Attachment == Trunk.Attachment


def test_exact_port_realizability_reports_self_claim_conflict():
    class SelfConflictingResourceGraph:
        GraphVersion = "self-conflict-test"
        Technology = SimpleNamespace()

        def BuildRouteClaims(self, Nodes):
            Nodes = frozenset(Nodes)
            Claims = _Claims(*Nodes)
            if len(Nodes) < 3:
                return Claims
            First = min(Nodes)
            return replace(
                Claims,
                SupportCells=Claims.SupportCells | frozenset((First,)),
            )

        def BuildPrimitive(self, _First, _Second):
            return object()

    Problem = replace(
        _Problem(
            External=(("Alpha", (10, 7, 0), "target"),),
        ),
        ResourceGraph=SelfConflictingResourceGraph(),
    )
    Domains = Problem.OwnedTerminalDomains
    Result = EvaluateExactComponentPortRealizability(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=tuple(
            Domain.Candidates[0] for Domain in Domains
        ),
        LocalPath=((2, 7, 0), (3, 7, 0)),
    )

    assert not Result.Realizable
    assert Result.Diagnostics["RejectionCounts"] == {
        "self-claim-conflict": 1,
    }
    assert not Result.Diagnostics["ImmutableConflictSignals"]


def test_exact_port_predicate_matches_single_net_local_compilation():
    Problem = _Problem(
        External=(("Alpha", (10, 7, 0), "target"),),
    )
    Domains = Problem.OwnedTerminalDomains
    Candidates = tuple(
        Domain.Candidates[0] for Domain in Domains
    )
    LocalPath = ((2, 7, 0), (3, 7, 0))
    Port = PhysicalComponentPortReservation(
        Signal="Alpha",
        Direction="output",
        OwnedTerminals=tuple(
            Domain.Terminal for Domain in Domains
        ),
        OwnedTerminalFingerprints=tuple(
            Domain.TerminalFingerprint for Domain in Domains
        ),
        OwnedCandidateFingerprints=tuple(
            Candidate.CandidateFingerprint
            for Candidate in Candidates
        ),
        FabricDomainFingerprint="single-fabric",
        FabricAttachment=LocalPath[0],
        Attachment=LocalPath[-1],
        LocalPath=LocalPath,
        GlobalPath=(LocalPath[-1], (4, 7, 0)),
        Claims=_Claims(
            *(
                Position
                for Candidate in Candidates
                for Position in Candidate.Path
            ),
            *LocalPath,
            (4, 7, 0),
        ),
        ReservationFingerprint="exact-port",
    )
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="exact-single-net-interface",
        ComponentId=0,
        OwnedSignals=("Alpha",),
        Ports=(
            ComponentInterfacePort(
                Signal="Alpha",
                Direction="output",
                OwnedTerminals=Port.OwnedTerminals,
                ExternalTerminalCount=1,
            ),
        ),
        PhysicalPortReservations=(Port,),
    )
    ExactProblem = replace(
        Problem,
        Interface=Interface,
        OwnedTerminalDomains=tuple(
            replace(Domain, Candidates=(Candidate,))
            for Domain, Candidate in zip(Domains, Candidates)
        ),
    )

    Predicate = EvaluateExactComponentPortRealizability(
        ExactProblem,
        Signal="Alpha",
        Domains=ExactProblem.OwnedTerminalDomains,
        Candidates=Candidates,
        LocalPath=LocalPath,
    )
    Compilation = SolveComponentRoutingProblem(
        ExactProblem,
        DiscoveryVariantLimit=None,
    )

    assert Predicate.Realizable == Compilation.Feasible
    assert Predicate.Realizable
    assert Compilation.Template is not None
    assert (
        Predicate.NetFingerprint
        == Compilation.Template.Nets[0].NetFingerprint
    )
    PowerUnsatisfiable = replace(
        ExactProblem,
        ProblemFingerprint="exact-single-net-power-unsatisfiable",
        MaximumPowerDistance=0,
    )
    UnsatisfiablePredicate = (
        EvaluateExactComponentPortRealizability(
            PowerUnsatisfiable,
            Signal="Alpha",
            Domains=PowerUnsatisfiable.OwnedTerminalDomains,
            Candidates=Candidates,
            LocalPath=LocalPath,
        )
    )
    UnsatisfiableCompilation = SolveComponentRoutingProblem(
        PowerUnsatisfiable,
        DiscoveryVariantLimit=None,
    )

    assert (
        UnsatisfiablePredicate.Realizable
        == UnsatisfiableCompilation.Feasible
        == False
    )
    assert (
        UnsatisfiablePredicate.Diagnostics["RejectionCounts"]
        == {"power-or-tree-connectivity": 1}
    )


def test_tree_fabric_is_deterministic_and_cycle_is_incomplete():
    First = BuildComponentRoutingFabric(
        _Channel(((0, 7, 0), (1, 7, 0), (2, 7, 0)))
    )
    Reordered = BuildComponentRoutingFabric(
        _Channel(((2, 7, 0), (1, 7, 0), (0, 7, 0)))
    )
    assert First.Complete
    assert First.FabricFingerprint == Reordered.FabricFingerprint

    Cyclic = BuildComponentRoutingFabric(_Channel((
        (0, 7, 0),
        (1, 7, 0),
        (1, 7, 1),
        (0, 7, 1),
        (0, 7, 0),
    )))
    assert not Cyclic.Complete
    assert Cyclic.IncompleteReason == "unsupported-cyclic-fabric"


def test_foreign_transit_selection_is_structural_and_rename_invariant():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(7)),
        tuple((X, 7, 6) for X in range(7)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Profile = NetRoutingProfile(
        Signal="Foreign",
        Root=(-10, 1, 3),
        Targets=((16, 1, 3),),
        Span=26,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((-10, 1, 3),),
        TargetAccessPaths={(16, 1, 3): ((16, 1, 3),)},
    )

    First = BuildComponentForeignTransitDomains(
        Problem,
        {"Foreign": Profile},
    )
    Renamed = BuildComponentForeignTransitDomains(
        Problem,
        {
            "Renamed": replace(
                Profile,
                Signal="Renamed",
            ),
        },
    )

    assert len(First) == 1
    assert First[0].PartitionAxis == "X"
    assert First[0].Candidates
    assert (
        First[0].PartitionFingerprint
        == Renamed[0].PartitionFingerprint
    )


def test_production_component_problem_has_closed_ownership():
    Channel = _Channel(
        ((0, 7, 0), (1, 7, 0), (2, 7, 0)),
    )
    Request = SimpleNamespace(
        Signal="Alpha",
        SourceCluster=0,
        TargetCluster=1,
        SourceTerminal=(0, 7, 0),
        TargetTerminals=((2, 7, 0),),
    )
    Placed = SimpleNamespace(
        InterClusterRoutingChannel=Channel,
        ClusterBoundaryLeaseRequests=(Request,),
        LocalRouteClaims=(),
        Module=SimpleNamespace(Gates=()),
    )
    Profiles = {
        "Alpha": NetRoutingProfile(
            Signal="Alpha",
            Root=(0, 7, 0),
            Targets=((2, 7, 0),),
            Span=2,
            Fanout=1,
            RetryCount=0,
            Criticality=0,
            IsTrunk=False,
            SourceAccessPath=((0, 7, 0),),
            TargetAccessPaths={(2, 7, 0): ((2, 7, 0),)},
        ),
        "Foreign": NetRoutingProfile(
            Signal="Foreign",
            Root=(1, 7, -2),
            Targets=((1, 7, 2),),
            Span=4,
            Fanout=1,
            RetryCount=0,
            Criticality=0,
            IsTrunk=False,
            SourceAccessPath=((1, 7, -2),),
            TargetAccessPaths={(1, 7, 2): ((1, 7, 2),)},
        ),
    }
    RawPortals = {
        ("Alpha", (0, 7, 0), 0): (
            _Portal(
                "Alpha",
                (0, 7, 0),
                ((0, 7, 0),),
                "alpha-source",
            ),
        ),
        ("Alpha", (2, 7, 0), 0): (
            _Portal(
                "Alpha",
                (2, 7, 0),
                ((2, 7, 0),),
                "alpha-target",
            ),
        ),
        ("Foreign", (1, 7, -2), 0): (
            _Portal(
                "Foreign",
                (1, 7, -2),
                ((1, 7, -2), (1, 7, 0)),
                "foreign-source",
            ),
        ),
    }

    Problem = BuildComponentRoutingProblem(
        Placed=Placed,
        Profiles=Profiles,
        RawPortals=RawPortals,
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
    )

    assert Problem.Interface is not None
    assert Problem.Interface.OwnedSignals == ("Alpha",)
    assert not Problem.ForeignEscapeDomains
    assert not Problem.ExternalContinuationDomains
    assert not Problem.ForeignTransitDomains
    assert {
        Domain.Signal for Domain in Problem.OwnedTerminalDomains
    } == {"Alpha"}


def test_closed_component_rejects_undeclared_foreign_transit():
    Problem = _Problem()
    Foreign = _Net("Foreign", (1, 7, 0))
    Transit = ComponentForeignTransitDomain(
        Signal="Foreign",
        PartitionAxis="X",
        PartitionFingerprint="partition",
        Candidates=(Foreign,),
    )
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="closed-interface",
        ComponentId=0,
        OwnedSignals=("Alpha",),
        Ports=(),
    )

    Result = SolveComponentRoutingProblem(replace(
        Problem,
        Interface=Interface,
        ForeignTransitDomains=(Transit,),
    ))

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Diagnostics["ImplicitForeignTransitDomainCount"] == 1
    assert "undeclared foreign transit" in Result.Detail


def test_explicit_feedthrough_contract_records_endpoints_and_capacity():
    Channel = _Channel(
        ((0, 7, 0), (1, 7, 0), (2, 7, 0)),
    )
    Channel.DeclaredFeedthroughSignals = ("Foreign",)
    Channel.ComponentId = 4
    Channel.InterfaceFingerprint = "logical-interface"
    Fabric = BuildComponentRoutingFabric(Channel)
    Profile = NetRoutingProfile(
        Signal="Alpha",
        Root=(0, 7, 0),
        Targets=((2, 7, 0),),
        Span=2,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((0, 7, 0),),
        TargetAccessPaths={(2, 7, 0): ((2, 7, 0),)},
    )

    Interface = BuildClosedComponentInterface(
        Channel=Channel,
        Fabric=Fabric,
        Profiles={"Alpha": Profile},
        ComponentSignals=("Alpha",),
        ComponentPairs=(
            ("Alpha", (0, 7, 0)),
            ("Alpha", (2, 7, 0)),
        ),
    )

    assert Interface.ComponentId == 4
    assert Interface.DeclaredFeedthroughSignals == {"Foreign"}
    assert Interface.Feedthroughs == (
        ComponentFeedthroughContract(
            Signal="Foreign",
            EndpointPairs=(((0, 7, 0), (2, 7, 0)),),
            Capacity=1,
        ),
    )


def test_declared_feedthrough_compiles_only_its_exact_endpoints():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(7)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Contract = ComponentFeedthroughContract(
        Signal="Foreign",
        EndpointPairs=(((0, 7, 0), (6, 7, 0)),),
        Capacity=1,
    )

    Domains = BuildDeclaredComponentFeedthroughDomains(
        Problem,
        (Contract,),
    )

    assert len(Domains) == 1
    assert Domains[0].Complete
    assert Domains[0].Diagnostics["Mode"] == "declared-feedthrough"
    assert Domains[0].Candidates
    assert all(
        Candidate.Nodes <= frozenset(Fabric.Nodes)
        and (0, 7, 0) in Candidate.Nodes
        and (6, 7, 0) in Candidate.Nodes
        for Candidate in Domains[0].Candidates
    )


def test_foreign_transit_competes_for_capacity_and_freezes_as_seed():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(7)),
        tuple((X, 7, 6) for X in range(7)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Profile = NetRoutingProfile(
        Signal="Foreign",
        Root=(-10, 1, 3),
        Targets=((16, 1, 3),),
        Span=26,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((-10, 1, 3),),
        TargetAccessPaths={(16, 1, 3): ((16, 1, 3),)},
    )
    TransitDomains = BuildComponentForeignTransitDomains(
        Problem,
        {"Foreign": Profile},
    )
    OptionalResult = SolveComponentRoutingProblem(replace(
        Problem,
        ForeignTransitDomains=TransitDomains,
    ))
    assert OptionalResult.Feasible
    assert OptionalResult.Template is not None
    assert not OptionalResult.Template.ForeignTransitReservations
    Result = SolveComponentRoutingProblem(
        replace(
            Problem,
            ForeignTransitDomains=TransitDomains,
        ),
        RequiredForeignTransitSignals=frozenset(("Foreign",)),
    )

    assert Result.Feasible
    assert Result.Template is not None
    assert len(Result.Template.ForeignTransitReservations) == 1
    Placed = PlacedDesign(
        Module=SimpleNamespace(),
        PlacedGates=[],
        LocalRouteClaims=(),
        LocalRouteDiagnostics={},
        RoutedComponentTemplates=(),
    )
    Materialized = MaterializeRoutedComponentTemplate(
        Placed,
        Result.Template,
    )
    TransitClaims = tuple(
        Claim
        for Claim in Materialized.LocalRouteClaims
        if Claim.ClusterId == -4
    )
    assert len(TransitClaims) == 1
    assert TransitClaims[0].Signal == "Foreign"
    assert len(TransitClaims[0].BoundaryNodes) == 2


def test_required_foreign_transit_pair_is_prechecked_before_net_search():
    First = _Net("FirstTransit", (20, 7, 20))
    Second = _Net("SecondTransit", (20, 7, 20))
    Domains = (
        ComponentForeignTransitDomain(
            Signal=First.Signal,
            PartitionAxis="X",
            PartitionFingerprint="first",
            Candidates=(First,),
        ),
        ComponentForeignTransitDomain(
            Signal=Second.Signal,
            PartitionAxis="Z",
            PartitionFingerprint="second",
            Candidates=(Second,),
        ),
    )
    Result = SolveComponentRoutingProblem(
        replace(
            _Problem(),
            ForeignTransitDomains=Domains,
        ),
        RequiredForeignTransitSignals=frozenset((
            First.Signal,
            Second.Signal,
        )),
    )

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.ExpansionCount == 0
    Precheck = Result.Diagnostics["RequiredTransitPrecheck"]
    assert Precheck["Complete"]
    assert (
        Precheck["PairCompatibility"][0][
            "CompatiblePairCount"
        ]
        == 0
    )


def test_foreign_boundary_parallel_continuation_uses_dominant_span():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(7)),
        tuple((0, 7, Z) for Z in range(1, 7)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Profile = NetRoutingProfile(
        Signal="BoundaryForeign",
        Root=(-8, 1, -10),
        Targets=((-7, 1, 16),),
        Span=27,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((-8, 1, -10),),
        TargetAccessPaths={
            (-7, 1, 16): ((-7, 1, 16),),
        },
    )

    Domains = BuildComponentForeignTransitDomains(
        Problem,
        {"BoundaryForeign": Profile},
    )

    assert len(Domains) == 1
    assert Domains[0].PartitionAxis == "Z"
    assert Domains[0].Candidates


def test_access_candidate_dominance_preserves_distinct_attachments():
    Compact = _Candidate(((0, 7, 0),))
    Larger = ComponentTerminalAccessCandidate(
        CandidateFingerprint="larger",
        Attachment=Compact.Attachment,
        Path=((0, 7, 0), (0, 7, 1)),
        Claims=_Claims((0, 7, 0), (0, 7, 1)),
    )
    Distinct = _Candidate(((1, 7, 0),))

    Retained = PruneDominatedComponentAccessCandidates((
        Larger,
        Distinct,
        Compact,
    ))

    assert Compact in Retained
    assert Distinct in Retained
    assert Larger not in Retained


def test_component_signal_selection_uses_cluster_incidence():
    Requests = (
        SimpleNamespace(
            Signal="Internal",
            SourceCluster=1,
            TargetCluster=2,
        ),
        SimpleNamespace(
            Signal="Entering",
            SourceCluster=8,
            TargetCluster=2,
        ),
        SimpleNamespace(
            Signal="Leaving",
            SourceCluster=1,
            TargetCluster=9,
        ),
        SimpleNamespace(
            Signal="Unrelated",
            SourceCluster=8,
            TargetCluster=9,
        ),
    )
    assert SelectComponentIncidentSignals(
        Requests,
        (1, 2),
        ("Internal", "Entering", "Leaving", "Unrelated"),
    ) == frozenset({"Internal", "Entering", "Leaving"})
    Renamed = tuple(
        SimpleNamespace(
            Signal=f"Signal{Index}",
            SourceCluster=Request.SourceCluster,
            TargetCluster=Request.TargetCluster,
        )
        for Index, Request in enumerate(reversed(Requests))
    )
    assert len(SelectComponentIncidentSignals(
        Renamed,
        (2, 1),
        tuple(Request.Signal for Request in Renamed),
    )) == 3


def test_unique_subtree_routes_and_detects_exact_capacity_conflict():
    Result = SolveComponentRoutingProblem(_Problem())
    assert Result.Feasible
    assert Result.Template is not None
    assert Result.Template.Nets[0].Nodes == frozenset({
        (0, 7, 0),
        (1, 7, 0),
        (2, 7, 0),
    })
    assert ComponentClaimsConflict(
        _Claims((0, 7, 0)),
        _Claims((0, 7, 0)),
    )
    assert not ComponentClaimsConflict(
        RoutingResourceClaims(
            SupportCells=frozenset({(0, 6, 0)}),
        ),
        RoutingResourceClaims(
            SupportCells=frozenset({(0, 6, 0)}),
        ),
    )
    assert ComponentClaimsConflict(
        RoutingResourceClaims(
            SupportCells=frozenset({(0, 6, 0)}),
        ),
        RoutingResourceClaims(
            WireCells=frozenset({(0, 6, 0)}),
        ),
    )


def test_unique_subtree_conflict_fixture_is_exhaustive():
    Base = _Problem()
    Conflicting = ComponentRoutingProblem(
        **{
            **Base.__dict__,
            "ComponentSignals": ("Alpha", "Beta"),
            "OwnedTerminalDomains": (
                *Base.OwnedTerminalDomains,
                _Domain(
                    "Beta",
                    (0, 7, 0),
                    "source",
                    _Candidate(((0, 7, 0),)),
                ),
                _Domain(
                    "Beta",
                    (2, 7, 0),
                    "target",
                    _Candidate(((2, 7, 0),)),
                ),
            ),
        }
    )
    Bounded = SolveComponentRoutingProblem(
        Conflicting,
        DiscoveryVariantLimit=1,
    )
    assert Bounded.Status == "incomplete"
    assert Bounded.Diagnostics["CapacityEmptyDomainWitnesses"]
    Result = SolveComponentRoutingProblem(Conflicting)
    assert Result.Exhaustive


def test_physically_identical_access_derivations_share_one_net_variant():
    Base = _Problem()
    Source = Base.OwnedTerminalDomains[0]
    Candidate = Source.Candidates[0]
    Duplicate = ComponentTerminalAccessCandidate(
        CandidateFingerprint="different-enumeration-identity",
        Attachment=Candidate.Attachment,
        Path=Candidate.Path,
        Claims=Candidate.Claims,
        Layer=Candidate.Layer,
        Cost=Candidate.Cost + 1,
    )
    Problem = ComponentRoutingProblem(
        **{
            **Base.__dict__,
            "OwnedTerminalDomains": (
                ComponentTerminalAccessDomain(
                    **{
                        **Source.__dict__,
                        "Candidates": (Candidate, Duplicate),
                    },
                ),
                Base.OwnedTerminalDomains[1],
            ),
        },
    )
    Result = SolveComponentRoutingProblem(Problem)
    assert Result.Feasible
    Diagnostics = (
        Result.Diagnostics["VariantDiagnosticsBySignal"]["Alpha"]
    )
    assert Diagnostics["AccessCombinationCount"] == 2
    assert Diagnostics["RoutedVariantCount"] == 1


def test_tree_power_dp_places_repeaters_on_long_trunk():
    Nodes = tuple((Index, 7, 0) for Index in range(10))
    Fabric = BuildComponentRoutingFabric(_Channel(Nodes))
    Problem = _Problem(
        Fabric=Fabric,
        MaximumPowerDistance=3,
    )
    Problem = ComponentRoutingProblem(
        **{
            **Problem.__dict__,
            "OwnedTerminalDomains": (
                _Domain(
                    "Alpha",
                    Nodes[0],
                    "source",
                    _Candidate((Nodes[0],)),
                ),
                _Domain(
                    "Alpha",
                    Nodes[-1],
                    "target",
                    _Candidate((Nodes[-1],)),
                ),
            ),
        }
    )
    Result = SolveComponentRoutingProblem(Problem)
    assert Result.Feasible
    assert Result.Template is not None
    assert len(Result.Template.Nets[0].Repeaters) >= 2


def test_foreign_escape_must_respect_immutable_outside_local_claims():
    ForeignCandidate = _Candidate(((10, 3, 10), (11, 3, 10)))
    Problem = _Problem(
        Foreign=(
            _Domain(
                "Foreign",
                (10, 3, 10),
                "foreign-target",
                ForeignCandidate,
            ),
        ),
    )
    Problem = ComponentRoutingProblem(
        **{
            **Problem.__dict__,
            "ImmutableClaims": (
                SimpleNamespace(
                    Signal="OutsideLocal",
                    Claims=_Claims((11, 3, 10)),
                ),
            ),
        }
    )
    Result = SolveComponentRoutingProblem(Problem)
    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Template is None


def test_same_signal_claim_fragments_still_obey_physical_self_conflicts():
    Existing = _Claims((10, 2, 10))
    Colliding = _Claims((10, 3, 10))
    assert ComponentClaimsCompatibleForOwners(
        "Shared",
        Existing,
        "Shared",
        Existing,
    )
    assert not ComponentClaimsCompatibleForOwners(
        "Shared",
        Existing,
        "Shared",
        Colliding,
    )


def test_frozen_foreign_source_exports_boundary_as_global_root():
    Claim = SimpleNamespace(
        Signal="ForeignSource",
        ClusterId=-2,
        Root=(4, 1, 4),
        BoundaryNodes=((8, 4, 8),),
    )
    Profile = NetRoutingProfile(
        Signal="ForeignSource",
        Root=(4, 1, 4),
        Targets=((20, 1, 20),),
        Span=32,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((4, 1, 4),),
        TargetAccessPaths={(20, 1, 20): ((20, 1, 20),)},
    )
    Result = ApplyRoutedComponentGlobalProfiles(
        SimpleNamespace(
            RoutedComponentTemplates=(),
            LocalRouteClaims=(Claim,),
        ),
        {"ForeignSource": Profile},
    )
    assert Result["ForeignSource"].Root == (8, 4, 8)
    assert Result["ForeignSource"].SourceAccessPath == ((8, 4, 8),)


def test_frozen_foreign_source_replaces_original_portal_with_export():
    Candidate = _Candidate(((4, 1, 4), (5, 2, 5)))
    Template = SimpleNamespace(
        ForeignEscapeReservations=(
            ("ForeignSource", (4, 1, 4), Candidate),
        ),
    )
    Placed = SimpleNamespace(
        RoutedComponentTemplates=(Template,),
        PlacedGates=(
            SimpleNamespace(OutputPin=(4, 1, 4)),
        ),
        LocalRouteClaims=(
            SimpleNamespace(
                Signal="ForeignSource",
                Root=(4, 1, 4),
                ClusterId=-2,
            ),
        ),
    )
    Portals, Diagnostics = PreserveRoutedComponentForeignEscapes(
        Placed,
        {
            ("ForeignSource", (4, 1, 4), 0): (
                SimpleNamespace(),
            ),
        },
    )
    assert ("ForeignSource", (4, 1, 4), 0) not in Portals
    ExportKey = ("ForeignSource", (5, 2, 5), 0)
    assert ExportKey in Portals
    assert Portals[ExportKey][0].Path == ((5, 2, 5),)
    assert Diagnostics["ExportedSourcePortCount"] == 1


def test_frozen_foreign_target_replaces_pin_with_boundary_endpoint():
    Claim = SimpleNamespace(
        Signal="ForeignTarget",
        ClusterId=-2,
        Root=(20, 1, 20),
        BoundaryNodes=((18, 4, 20),),
    )
    Profile = NetRoutingProfile(
        Signal="ForeignTarget",
        Root=(4, 1, 4),
        Targets=((20, 1, 20), (24, 1, 24)),
        Span=40,
        Fanout=2,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((4, 1, 4),),
        TargetAccessPaths={
            (20, 1, 20): ((20, 1, 20),),
            (24, 1, 24): ((24, 1, 24),),
        },
    )

    Result = ApplyRoutedComponentGlobalProfiles(
        SimpleNamespace(
            RoutedComponentTemplates=(),
            LocalRouteClaims=(Claim,),
        ),
        {"ForeignTarget": Profile},
    )

    assert Result["ForeignTarget"].Targets == (
        (18, 4, 20),
        (24, 1, 24),
    )
    assert Result["ForeignTarget"].TargetAccessPaths[
        (18, 4, 20)
    ] == ((18, 4, 20),)
    assert (20, 1, 20) not in (
        Result["ForeignTarget"].TargetAccessPaths
    )


def test_external_port_and_foreign_escape_survive_materialization():
    ForeignCandidate = _Candidate(((10, 3, 10), (11, 3, 10)))
    Foreign = _Domain(
        "Foreign",
        (10, 3, 10),
        "foreign-target",
        ForeignCandidate,
    )
    Problem = _Problem(
        Foreign=(Foreign,),
        External=(("Alpha", (9, 3, 9), "target"),),
    )
    Result = SolveComponentRoutingProblem(Problem)
    assert Result.Feasible
    assert Result.Template is not None
    assert Result.Template.ExportedPorts
    assert Result.Template.ForeignEscapeReservations[0][2] == ForeignCandidate

    Channel = SimpleNamespace(
        PhysicalModel="test-tree-fabric",
        ChannelFingerprint="channel",
        Lanes=(
            SimpleNamespace(
                Cells=((0, 7, 0), (1, 7, 0), (2, 7, 0)),
                IngressNodes=((0, 7, 0), (2, 7, 0)),
            ),
        ),
    )
    Placed = PlacedDesign(
        Module=SimpleNamespace(),
        PlacedGates=[],
        LocalRouteClaims=(),
        LocalRouteDiagnostics={},
        RoutedComponentTemplates=(),
        ClusterBoundaryLeaseRequests=(SimpleNamespace(Signal="Foreign"),),
        InterClusterRoutingChannel=Channel,
    )
    Materialized = MaterializeRoutedComponentTemplate(
        Placed,
        Result.Template,
    )
    assert Materialized.RoutedComponentTemplates == (Result.Template,)
    assert Materialized.ClusterBoundaryLeaseRequests == ()
    ForeignClaims = tuple(
        Claim
        for Claim in Materialized.LocalRouteClaims
        if Claim.ClusterId == -2
    )
    assert len(ForeignClaims) == 1
    assert ForeignClaims[0].Signal == "Foreign"
    assert ForeignClaims[0].Root == Foreign.Terminal
    assert ForeignClaims[0].ConnectedTargets == (Foreign.Terminal,)
    assert ForeignClaims[0].BoundaryNodes == (
        ForeignCandidate.Path[-1],
    )
    assert ForeignClaims[0].Nodes == frozenset(ForeignCandidate.Path)
    assert (
        Materialized.LocalRouteDiagnostics[
            "__RoutedComponentGlobalHandoff__"
        ]["FrozenForeignEscapeClaimCount"]
        == 1
    )
    assert (
        Materialized.LocalRouteDiagnostics[
            "__RoutedComponentGlobalHandoff__"
        ]["RetiredClusterBoundaryLeaseRequestCount"]
        == 1
    )
    assert Materialized.LocalRouteClaims[0].BoundaryNodes
    assert (
        Materialized.LocalRouteDiagnostics[
            "__RoutedComponentTemplate__"
        ]["ExportedPortFingerprint"]
        == Result.Template.ExportedPortFingerprint
    )
    assert ValidateRoutedComponentHandoff(
        Materialized,
        Result.Template,
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
    )["Valid"]
    AugmentedTemplate = replace(
        Result.Template,
        FabricFingerprint="augmented-exact-access-fabric",
    )
    AugmentedMaterialized = MaterializeRoutedComponentTemplate(
        Placed,
        AugmentedTemplate,
    )
    AugmentedHandoff = ValidateRoutedComponentHandoff(
        AugmentedMaterialized,
        AugmentedTemplate,
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
    )
    assert AugmentedHandoff["Valid"]
    assert AugmentedHandoff["FabricAugmentedForExactAccess"]
    assert (
        AugmentedHandoff["ArchivedFabricFingerprint"]
        != AugmentedHandoff["FabricFingerprint"]
    )
    Portal = SimpleNamespace(
        Path=ForeignCandidate.Path,
        Claims=ForeignCandidate.Claims,
        Cost=0,
        PortalId="foreign",
    )
    AlternativePortal = SimpleNamespace(
        Path=((10, 5, 10), (11, 5, 10)),
        Claims=_Claims((10, 5, 10), (11, 5, 10)),
        Cost=1,
        PortalId="foreign-alternative",
    )
    Portals, Diagnostics = PreserveRoutedComponentForeignEscapes(
        Materialized,
        {
            ("Foreign", (10, 3, 10), 1): (Portal,),
            ("Foreign", (10, 3, 10), 2): (AlternativePortal,),
        },
    )
    assert ("Foreign", (10, 3, 10), 1) not in Portals
    assert ("Foreign", (10, 3, 10), 2) not in Portals
    assert Diagnostics["ConsumedByFrozenClaimCount"] == 1
    assert Diagnostics["Complete"]

    Entering = SolveComponentRoutingProblem(_Problem(
        External=(("Alpha", (-5, 7, 0), "source"),),
    ))
    assert Entering.Feasible
    assert Entering.Template is not None
    EnteringNet = Entering.Template.Nets[0]
    assert EnteringNet.Root in EnteringNet.ExportedPorts


def test_external_continuation_access_is_solved_and_frozen():
    SelectedCandidate = _Candidate(((20, 3, 20), (21, 3, 20)))
    Continuation = _Domain(
        "Alpha",
        (20, 3, 20),
        "continuation-target",
        SelectedCandidate,
    )
    Problem = replace(
        _Problem(
            External=(("Alpha", Continuation.Terminal, "target"),),
        ),
        ExternalContinuationDomains=(Continuation,),
    )

    Result = SolveComponentRoutingProblem(Problem)

    assert Result.Feasible
    assert Result.Template is not None
    assert Result.Template.ExternalContinuationReservations == (
        ("Alpha", Continuation.Terminal, SelectedCandidate),
    )
    Placed = PlacedDesign(
        Module=SimpleNamespace(),
        PlacedGates=[],
        LocalRouteClaims=(),
        LocalRouteDiagnostics={},
        RoutedComponentTemplates=(),
    )
    Materialized = MaterializeRoutedComponentTemplate(
        Placed,
        Result.Template,
    )
    ContinuationClaims = tuple(
        Claim
        for Claim in Materialized.LocalRouteClaims
        if Claim.ClusterId == -3
    )
    assert len(ContinuationClaims) == 1
    assert ContinuationClaims[0].Signal == "Alpha"
    assert ContinuationClaims[0].Root == Continuation.Terminal
    assert ContinuationClaims[0].Nodes == frozenset(
        SelectedCandidate.Path
    )


def test_external_continuation_handoff_retains_only_selected_portal():
    SelectedPortal = _Portal(
        "Alpha",
        (20, 3, 20),
        ((20, 3, 20), (21, 3, 20)),
        "selected",
    )
    OtherPortal = _Portal(
        "Alpha",
        (20, 3, 20),
        ((20, 3, 20), (20, 3, 21)),
        "other",
    )
    Candidate = _Candidate(SelectedPortal.Path)
    Template = SimpleNamespace(
        ExternalContinuationReservations=(
            ("Alpha", (20, 3, 20), Candidate),
        ),
        ForeignEscapeReservations=(),
    )

    Portals, Diagnostics = PreserveRoutedComponentForeignEscapes(
        SimpleNamespace(
            RoutedComponentTemplates=(Template,),
            LocalRouteClaims=(),
            PlacedGates=(),
        ),
        {
            ("Alpha", (20, 3, 20), 0): (
                OtherPortal,
                SelectedPortal,
            ),
        },
    )

    assert Portals[("Alpha", (20, 3, 20), 0)] == (
        SelectedPortal,
    )
    assert Diagnostics["ContinuationRequiredCount"] == 1
    assert Diagnostics["ContinuationPreservedCount"] == 1
    assert Diagnostics["ContinuationMissingCount"] == 0
    assert Diagnostics["Complete"]


def test_cut_derived_export_port_nogood_selects_distinct_variant():
    Problem = _Problem(
        External=(("Alpha", (9, 3, 9), "target"),),
    )
    PortfolioCache = {}
    First = SolveComponentRoutingProblem(
        Problem,
        VariantPortfolioCache=PortfolioCache,
    )
    assert First.Feasible and First.Template is not None
    FirstNet = First.Template.Nets[0]
    Second = SolveComponentRoutingProblem(
        Problem,
        ForbiddenExportPortsBySignal={
            "Alpha": FirstNet.ExportedPorts,
        },
        VariantPortfolioCache=PortfolioCache,
    )
    assert Second.Feasible and Second.Template is not None
    assert (
        Second.Template.Nets[0].ExportedPorts
        != FirstNet.ExportedPorts
    )
    assert (
        Second.Diagnostics["VariantDiagnosticsBySignal"]["Alpha"][
            "PortfolioCacheHit"
        ]
    )


def test_complete_net_portfolio_cache_reuses_rigid_translation_and_rename():
    PortfolioCache = {}
    First = SolveComponentRoutingProblem(
        _Problem("Alpha"),
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible and First.Template is not None

    Delta = (20, 0, 11)

    def Move(Position):
        return tuple(
            Position[Index] + Delta[Index]
            for Index in range(3)
        )

    def MoveClaims(Claims):
        return RoutingResourceClaims(
            WireCells=frozenset(map(Move, Claims.WireCells)),
            SupportCells=frozenset(map(Move, Claims.SupportCells)),
            RequiredAirCells=frozenset(
                map(Move, Claims.RequiredAirCells)
            ),
            ElectricalCells=frozenset(
                map(Move, Claims.ElectricalCells)
            ),
        )

    def MoveCandidate(Candidate):
        return replace(
            Candidate,
            CandidateFingerprint=(
                f"renamed-{Candidate.CandidateFingerprint}"
            ),
            Attachment=Move(Candidate.Attachment),
            Path=tuple(map(Move, Candidate.Path)),
            Claims=MoveClaims(Candidate.Claims),
        )

    Base = _Problem("Alpha")
    Translated = replace(
        Base,
        ProblemFingerprint="translated-and-renamed-problem",
        PlacementFingerprint="translated-placement",
        ComponentSignals=("Omega",),
        Fabric=replace(
            Base.Fabric,
            FabricFingerprint="translated-fabric",
            Nodes=tuple(map(Move, Base.Fabric.Nodes)),
            Edges=tuple(
                (Move(FirstNode), Move(SecondNode))
                for FirstNode, SecondNode in Base.Fabric.Edges
            ),
            IngressNodes=tuple(map(Move, Base.Fabric.IngressNodes)),
        ),
        OwnedTerminalDomains=tuple(
            replace(
                Domain,
                Signal="Omega",
                Terminal=Move(Domain.Terminal),
                TerminalFingerprint=(
                    f"renamed-{Domain.TerminalRole}"
                ),
                Candidates=tuple(
                    MoveCandidate(Candidate)
                    for Candidate in Domain.Candidates
                ),
            )
            for Domain in Base.OwnedTerminalDomains
        ),
    )
    Second = SolveComponentRoutingProblem(
        Translated,
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )

    assert Second.Feasible and Second.Template is not None
    Diagnostics = Second.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Omega"]
    assert Diagnostics["PortfolioCacheHit"]
    assert Diagnostics["PortfolioTranslationValidated"]
    assert Diagnostics["PortfolioTranslationDelta"] == list(Delta)
    assert Second.Template.Nets[0].Signal == "Omega"
    assert Second.Template.Nets[0].Nodes == frozenset(
        Move(Position)
        for Position in First.Template.Nets[0].Nodes
    )


def test_completed_component_template_cache_reuses_translation():
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="completed-cache-interface",
        ComponentId=7,
        OwnedSignals=("Alpha",),
        Ports=(),
    )
    FirstProblem = replace(_Problem(), Interface=Interface)
    First = CompileClosedComponent(
        FirstProblem,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible and First.Template is not None
    assert not First.Diagnostics["CompletedTemplateCacheHit"]

    Delta = (31, 0, 13)

    def Move(Position):
        return tuple(
            Position[Index] + Delta[Index]
            for Index in range(3)
        )

    def MoveClaims(Claims):
        return RoutingResourceClaims(
            WireCells=frozenset(map(Move, Claims.WireCells)),
            SupportCells=frozenset(map(Move, Claims.SupportCells)),
            RequiredAirCells=frozenset(
                map(Move, Claims.RequiredAirCells)
            ),
            ElectricalCells=frozenset(
                map(Move, Claims.ElectricalCells)
            ),
        )

    Translated = replace(
        FirstProblem,
        ProblemFingerprint="completed-cache-translated",
        PlacementFingerprint="translated-placement",
        Fabric=BuildComponentRoutingFabric(_Channel(tuple(
            Move(Position)
            for Position in ((0, 7, 0), (1, 7, 0), (2, 7, 0))
        ))),
        OwnedTerminalDomains=tuple(
            replace(
                Domain,
                Terminal=Move(Domain.Terminal),
                Candidates=tuple(
                    replace(
                        Candidate,
                        Attachment=Move(Candidate.Attachment),
                        Path=tuple(map(Move, Candidate.Path)),
                        Claims=MoveClaims(Candidate.Claims),
                    )
                    for Candidate in Domain.Candidates
                ),
            )
            for Domain in FirstProblem.OwnedTerminalDomains
        ),
    )
    Second = CompileClosedComponent(
        Translated,
        DiscoveryVariantLimit=None,
    )

    assert Second.Feasible and Second.Template is not None
    assert Second.Diagnostics["CompletedTemplateCacheHit"]
    assert (
        Second.Diagnostics["CompletedTemplateTranslationDelta"]
        == list(Delta)
    )
    assert Second.Template.Nets[0].Nodes == frozenset(
        Move(Position)
        for Position in First.Template.Nets[0].Nodes
    )


def test_complete_net_portfolio_cache_rejects_changed_fabric_topology():
    PortfolioCache = {}
    SolveComponentRoutingProblem(
        _Problem(),
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )
    Changed = replace(_Problem(
        Fabric=BuildComponentRoutingFabric(_Channel(
            ((0, 7, 0), (1, 7, 0), (2, 7, 0)),
            ((1, 7, 0), (1, 7, 1)),
        )),
    ), ProblemFingerprint="changed-topology-problem")
    Result = SolveComponentRoutingProblem(
        Changed,
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )

    assert Result.Feasible
    assert not Result.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Alpha"]["PortfolioCacheHit"]


def test_progressive_discovery_reuses_net_construction_work():
    NetCache = {}
    ClaimsCache = {}
    DiscoveryCache = {}
    First = SolveComponentRoutingProblem(
        _Problem(),
        DiscoveryVariantLimit=1,
        NetVariantConstructionCache=NetCache,
        RouteClaimsConstructionCache=ClaimsCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )
    assert First.Feasible
    assert NetCache
    assert ClaimsCache
    assert DiscoveryCache

    Second = SolveComponentRoutingProblem(
        _Problem(),
        DiscoveryVariantLimit=2,
        NetVariantConstructionCache=NetCache,
        RouteClaimsConstructionCache=ClaimsCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )

    assert Second.Feasible
    assert (
        Second.Diagnostics[
            "NetVariantConstructionCacheInitialCount"
        ] > 0
    )
    assert (
        Second.Diagnostics[
            "RouteClaimsConstructionCacheInitialCount"
        ] > 0
    )
    assert (
        Second.Diagnostics[
            "NetVariantDiscoveryStateCacheInitialCount"
        ] > 0
    )


def test_signal_scoped_discovery_limit_overrides_global_limit():
    Result = SolveComponentRoutingProblem(
        _Problem(),
        DiscoveryVariantLimit=1,
        DiscoveryVariantLimitsBySignal={"Alpha": None},
    )

    assert Result.Feasible
    Alpha = Result.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Alpha"]
    assert Alpha["DiscoveryVariantLimit"] is None
    assert Alpha["DiscoveryPortfolioComplete"]


def test_cut_derived_foreign_witness_nogood_selects_distinct_candidate():
    FirstCandidate = _Candidate(((10, 3, 10), (11, 3, 10)))
    SecondCandidate = _Candidate(((10, 5, 10), (11, 5, 10)))
    Problem = _Problem(Foreign=(
        _Domain(
            "Foreign",
            (10, 3, 10),
            "foreign-target",
            FirstCandidate,
            SecondCandidate,
        ),
    ))
    First = SolveComponentRoutingProblem(Problem)
    assert First.Feasible and First.Template is not None
    Selected = First.Template.ForeignEscapeReservations[0][2]
    Second = SolveComponentRoutingProblem(
        Problem,
        ForbiddenForeignCandidateFingerprintsBySignal={
            "Foreign": frozenset((
                Selected.CandidateFingerprint,
            )),
        },
    )
    assert Second.Feasible and Second.Template is not None
    assert (
        Second.Template.ForeignEscapeReservations[0][
            2
        ].CandidateFingerprint
        != Selected.CandidateFingerprint
    )


def test_foreign_pair_nogood_changes_only_conflicting_combination():
    FirstDomain = _Domain(
        "ForeignA",
        (10, 3, 10),
        "foreign-target",
        _Candidate(((10, 3, 10), (11, 3, 10))),
        _Candidate(((10, 5, 10), (11, 5, 10))),
    )
    SecondDomain = _Domain(
        "ForeignB",
        (20, 3, 20),
        "foreign-target",
        _Candidate(((20, 3, 20), (21, 3, 20))),
        _Candidate(((20, 5, 20), (21, 5, 20))),
    )
    Problem = _Problem(Foreign=(FirstDomain, SecondDomain))
    First = SolveComponentRoutingProblem(Problem)
    assert First.Feasible and First.Template is not None
    SelectedPair = frozenset(
        (
            Signal,
            Terminal,
            Candidate.CandidateFingerprint,
        )
        for Signal, Terminal, Candidate
        in First.Template.ForeignEscapeReservations
    )

    Second = SolveComponentRoutingProblem(
        Problem,
        ForbiddenForeignAssignmentPairs=(SelectedPair,),
    )

    assert Second.Feasible and Second.Template is not None
    SecondPair = frozenset(
        (
            Signal,
            Terminal,
            Candidate.CandidateFingerprint,
        )
        for Signal, Terminal, Candidate
        in Second.Template.ForeignEscapeReservations
    )
    assert SecondPair != SelectedPair
    assert any(
        FirstValue in SecondPair
        for FirstValue in SelectedPair
    )


def test_incomplete_classification_and_rename_invariance():
    IncompleteFabric = BuildComponentRoutingFabric(
        _Channel((
            (0, 7, 0),
            (1, 7, 0),
            (1, 7, 1),
            (0, 7, 1),
            (0, 7, 0),
        ))
    )
    Incomplete = SolveComponentRoutingProblem(
        _Problem(Fabric=IncompleteFabric)
    )
    assert Incomplete.Status == "incomplete"
    assert not Incomplete.Exhaustive

    First = SolveComponentRoutingProblem(_Problem("Alpha"))
    Renamed = SolveComponentRoutingProblem(_Problem("Renamed"))
    assert First.Feasible and Renamed.Feasible
    assert First.Template is not None and Renamed.Template is not None
    assert (
        First.Template.RoutedTemplateFingerprint
        == Renamed.Template.RoutedTemplateFingerprint
    )


def load_tests(_Loader, _Tests, _Pattern):
    """Expose compact function tests to the repository's unittest runner."""
    return unittest.TestSuite(
        unittest.FunctionTestCase(Value)
        for Name, Value in sorted(globals().items())
        if Name.startswith("test_") and callable(Value)
    )

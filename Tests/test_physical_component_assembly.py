import ast
from dataclasses import replace
import inspect
import textwrap
from types import SimpleNamespace

import pytest

import Compiler.Routing.ComponentPipeline as ComponentPipeline
from Compiler.Placement.Geometry import PlacedDesign
from Compiler.Placement.PcbFlow import (
    BuildRetainedComponentPlacementSearchDomain,
    IsComponentKeepoutGlobalFailure,
    ReuseRetainedPlacementRoutingResources,
    _PlaceAndRoutePcbWithPolicy,
)
from Compiler.Routing.AuthoritativePlanner import (
    BuildComponentKeepoutAvoidingGlobalGuides,
    BuildComponentKeepoutGuideCellsByLayer,
    BuildPhysicalComponentAssemblyPlan,
    FindSignalClaimConflicts,
    PropagateLaneFactorArcConsistency,
    PreparePhysicalComponentPortFactorDomain,
    RemoveClosedComponentInternalGuides,
    SolvePreparedPhysicalComponentPortFactorDomain,
)
from Compiler.Routing.ChannelPlanner import ChannelPlan
from Compiler.Routing.LocalFirst import CoarseGuidePlan
from Compiler.Routing.ComponentPipeline import (
    CompileClosedComponent,
    FinalizePhysicalComponentChannelReservations,
)
from Compiler.Routing.ComponentAccess import (
    BuildComponentCutAccessFeasibilityCertificate,
    ValidateComponentAccessCertificateIdentity,
)
from Compiler.Routing.ComponentRouter import BuildComponentRoutingFabric
from Compiler.Routing.ComponentRouter import AugmentComponentRoutingFabric
from Compiler.Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.Models import (
    ClosedComponentInterface,
    ComponentInterfacePort,
    ComponentRoutingProblem,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    PhysicalComponentChannelReservation,
    RoutingResources,
)
from Compiler.Routing.ResourceGraph import (
    RoutingResourceClaims,
    RoutingResourceId,
    RoutingResourceKind,
)
from Compiler.Routing.Pcb import (
    ClassifyPhysicalComponentAssemblyFailure,
    ReplanPhysicalComponentAssembly,
)
from Compiler.Routing.Reliability import RoutingDeadline
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
)


def _Claims(Nodes):
    Nodes = frozenset(Nodes)
    return RoutingResourceClaims(
        WireCells=Nodes,
        SupportCells=frozenset(
            (X, Y - 1, Z) for X, Y, Z in Nodes
        ),
        ElectricalCells=Nodes,
    )


def test_port_seam_ignores_conflicts_owned_only_by_foreign_corridors():
    ForeignNode = (0, 7, 0)
    PortNode = (10, 7, 0)
    Claims = {
        "ForeignA": _Claims((ForeignNode,)),
        "ForeignB": _Claims((ForeignNode,)),
        "Port": _Claims((PortNode,)),
    }

    assert not FindSignalClaimConflicts(Claims, "Port")
    assert FindSignalClaimConflicts(
        {
            **Claims,
            "Port": _Claims((ForeignNode,)),
        },
        "Port",
    )


def test_global_guide_detours_around_component_keepout_without_feedthrough():
    OriginalForeignGuide = frozenset(
        (X, Z)
        for X in range(-8, 9)
        for Z in range(-1, 2)
    )
    ComponentGuide = frozenset(
        (X, 1) for X in range(-1, 2)
    )
    Plan = CoarseGuidePlan(
        Guides={
            "Foreign": OriginalForeignGuide,
            "Port": ComponentGuide,
        },
        Layers={"Foreign": 0, "Port": 0},
        Axes={"Foreign": "X", "Port": "X"},
        Lanes={"Foreign": 0, "Port": 1},
        Usage={},
        Overflow={},
        LocalSignals=frozenset(),
        Iterations=(),
    )

    Result, Detoured = BuildComponentKeepoutAvoidingGlobalGuides(
        Plan,
        ComponentPortSignals=frozenset(("Port",)),
        EnvelopeMinimum=(-1, 0, -1),
        EnvelopeMaximum=(1, 8, 1),
        TrackPitch=3,
        ReservedPortGuideCells=frozenset(((-2, 0),)),
    )

    assert Detoured == ("Foreign",)
    assert Result.Guides["Port"] == ComponentGuide
    assert not any(
        -2 <= X <= 2 and -2 <= Z <= 2
        for X, Z in Result.Guides["Foreign"]
    )
    assert not any(
        abs(X + 2) + abs(Z) <= 3
        for X, Z in Result.Guides["Foreign"]
    )
    Pending = [min(Result.Guides["Foreign"])]
    Reached = {Pending[0]}
    while Pending:
        X, Z = Pending.pop()
        for Neighbor in (
            (X - 1, Z),
            (X + 1, Z),
            (X, Z - 1),
            (X, Z + 1),
        ):
            if (
                Neighbor in Result.Guides["Foreign"]
                and Neighbor not in Reached
            ):
                Reached.add(Neighbor)
                Pending.append(Neighbor)
    assert Reached == set(Result.Guides["Foreign"])


def test_global_guide_detours_around_exterior_port_access_halo():
    Guide = frozenset((X, 6) for X in range(4, 11))
    Plan = CoarseGuidePlan(
        Guides={"Foreign": Guide},
        Layers={"Foreign": 0},
        Axes={"Foreign": "X"},
        Lanes={"Foreign": 6},
        Usage={},
        Overflow={},
        LocalSignals=frozenset(),
        Iterations=(),
    )

    Result, Detoured = BuildComponentKeepoutAvoidingGlobalGuides(
        Plan,
        ComponentPortSignals=frozenset(),
        EnvelopeMinimum=(-1, 0, -1),
        EnvelopeMaximum=(1, 8, 1),
        TrackPitch=3,
        ReservedPortGuideCells=frozenset(((7, 5),)),
    )

    assert Detoured == ("Foreign",)
    assert not any(
        abs(X - 7) + abs(Z - 5) <= 3
        for X, Z in Result.Guides["Foreign"]
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


def test_global_guide_rejects_a_foreign_corridor_consumed_by_keepout():
    Plan = CoarseGuidePlan(
        Guides={"Foreign": frozenset(((0, 0), (1, 0)))},
        Layers={"Foreign": 0},
        Axes={"Foreign": "X"},
        Lanes={"Foreign": 0},
        Usage={},
        Overflow={},
        LocalSignals=frozenset(),
        Iterations=(),
    )

    with pytest.raises(RoutingStageError) as Captured:
        BuildComponentKeepoutAvoidingGlobalGuides(
            Plan,
            ComponentPortSignals=frozenset(),
            EnvelopeMinimum=(-1, 0, -1),
            EnvelopeMaximum=(1, 8, 1),
            TrackPitch=3,
        )

    assert Captured.value.Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert (
        Captured.value.Failure.Diagnostics[
            "ImplicitForeignTransitDomainCount"
        ]
        == 0
    )


def test_closed_component_internal_guides_leave_global_plan():
    Plan = CoarseGuidePlan(
        Guides={
            "Internal": frozenset(((0, 0), (1, 0))),
            "Port": frozenset(((2, 0), (3, 0))),
            "Foreign": frozenset(((4, 0), (5, 0))),
        },
        Layers={"Internal": 0, "Port": 1, "Foreign": 2},
        Axes={"Internal": "X", "Port": "X", "Foreign": "X"},
        Lanes={"Internal": 0, "Port": 1, "Foreign": 2},
        Usage={(0, 0, 0): 1, (1, 2, 0): 1, (2, 4, 0): 1},
        Overflow={(0, 0, 0): 1},
        LocalSignals=frozenset(("Internal", "Port")),
        Iterations=(),
    )

    Result = RemoveClosedComponentInternalGuides(
        Plan,
        frozenset(("Internal",)),
    )

    assert set(Result.Guides) == {"Port", "Foreign"}
    assert set(Result.Layers) == {"Port", "Foreign"}
    assert Result.LocalSignals == frozenset(("Port",))
    assert not Result.Overflow
    assert all(Position[0] != 0 for Position in Result.Usage)


def test_hierarchical_pipeline_has_no_local_portfolio_or_recursive_fallback():
    FunctionTree = ast.parse(textwrap.dedent(
        inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ))
    Calls = tuple(
        Node
        for Node in ast.walk(FunctionTree)
        if isinstance(Node, ast.Call)
    )
    LocalCompileCalls = tuple(
        Call
        for Call in Calls
        if isinstance(Call.func, ast.Name)
        and Call.func.id == "CompileClosedComponent"
    )
    assert len(LocalCompileCalls) == 1
    assert {
        Keyword.arg
        for Keyword in LocalCompileCalls[0].keywords
    } == {
        "AssemblyPlan",
        "DeadlineSeconds",
        "WorkCheck",
    }
    assert not any(
        isinstance(Call.func, ast.Name)
        and Call.func.id == "_PlaceAndRoutePcbWithPolicy"
        for Call in Calls
    )
    DeckCalls = tuple(
        Call
        for Call in Calls
        if isinstance(Call.func, ast.Name)
        and Call.func.id == "BuildBoundedInterClusterRoutingDeck"
    )
    assert len(DeckCalls) == 2
    for DeckCall in DeckCalls:
        ComponentVariant = next(
            Keyword.value
            for Keyword in DeckCall.keywords
            if Keyword.arg == "ComponentVariant"
        )
        assert isinstance(ComponentVariant, ast.Name)
        assert ComponentVariant.id == "ComponentVariantForState"
    assert any(
        Keyword.arg == "ForcedAffectedClusters"
        for Keyword in DeckCalls[1].keywords
    )


def test_physical_port_certificate_filter_uses_each_ports_guide_layer():
    FunctionTree = ast.parse(textwrap.dedent(
        inspect.getsource(PreparePhysicalComponentPortFactorDomain)
    ))
    PortLoop = next(
        Node
        for Node in ast.walk(FunctionTree)
        if isinstance(Node, ast.For)
        and isinstance(Node.target, ast.Name)
        and Node.target.id == "Port"
        and any(
            isinstance(Child, ast.Name)
            and Child.id == "CertifiedCandidate"
            for Child in ast.walk(Node)
        )
    )
    PortLayerAssignments = tuple(
        Node
        for Node in ast.walk(PortLoop)
        if isinstance(Node, ast.Assign)
        and any(
            isinstance(Target, ast.Name)
            and Target.id == "PortLayer"
            for Target in Node.targets
        )
    )
    assert len(PortLayerAssignments) == 1
    CertifiedLayerComparisons = tuple(
        Node
        for Node in ast.walk(PortLoop)
        if isinstance(Node, ast.Compare)
        and isinstance(Node.left, ast.Attribute)
        and Node.left.attr == "Layer"
        and any(
            isinstance(Value, ast.Name) and Value.id == "PortLayer"
            for Value in Node.comparators
        )
    )
    assert CertifiedLayerComparisons
    RoutingYCalls = tuple(
        Node
        for Node in ast.walk(PortLoop)
        if isinstance(Node, ast.Call)
        and isinstance(Node.func, ast.Attribute)
        and Node.func.attr == "RoutingY"
    )
    assert RoutingYCalls
    assert all(
        any(
            isinstance(Argument, ast.Name)
            and Argument.id == "PortLayer"
            for Argument in Call.args
        )
        for Call in RoutingYCalls
    )


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


def test_ordinary_global_starvation_rejects_component_keepout_not_ports():
    Plan = SimpleNamespace(
        Ports=(SimpleNamespace(Signal="ComponentPort"),),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.DetailedSearchExhausted,
        Stage="Candidate",
        AffectedNets=("OrdinaryGlobal",),
        Detail=(
            "the immutable routed-component state blocked a complete "
            "bounded global candidate window"
        ),
        Diagnostics={
            "Action": "advance-routed-component-global-starvation",
        },
    )

    assert IsComponentKeepoutGlobalFailure(Failure, Plan)
    assert not IsComponentKeepoutGlobalFailure(
        replace(Failure, AffectedNets=("ComponentPort",)),
        Plan,
    )
    assert not IsComponentKeepoutGlobalFailure(
        replace(
            Failure,
            Diagnostics={"Action": "regenerate-affected-candidates"},
        ),
        Plan,
    )


class _ResourceGraph:
    GraphVersion = "test-resource-graph"
    Technology = DefaultRedstoneRoutingTechnology
    Nodes = ()
    Edges = ()

    def BuildRouteClaims(self, Nodes):
        return _Claims(Nodes)

    def BuildPrimitive(self, _First, _Second):
        return object()


def _Problem(Signal="Alpha", Delta=(0, 0, 0)):
    def Move(Position):
        return tuple(
            Position[Index] + Delta[Index] for Index in range(3)
        )

    Cells = tuple(map(Move, (
        (0, 7, 0),
        (1, 7, 0),
        (2, 7, 0),
    )))
    Channel = SimpleNamespace(
        PhysicalModel="test-tree",
        ComponentId=3,
        InterfaceFingerprint="logical-interface",
        DeclaredFeedthroughSignals=(),
        AffectedClusters=(0,),
        AffectedSignals=(Signal,),
        Lanes=(
            SimpleNamespace(
                Cells=Cells,
                IngressNodes=(Cells[0], Cells[-1]),
            ),
        ),
    )
    Fabric = BuildComponentRoutingFabric(Channel)

    def Candidate(Terminal):
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=f"{Signal}:{Terminal}",
            Attachment=Terminal,
            Path=(Terminal,),
            Claims=_Claims((Terminal,)),
        )

    Source = Cells[0]
    Target = Cells[-1]
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="translation-stable-interface",
        ComponentId=3,
        OwnedSignals=(Signal,),
        Ports=(
            ComponentInterfacePort(
                Signal=Signal,
                Direction="output",
                OwnedTerminals=(Source, Target),
                ExternalTerminalCount=1,
            ),
        ),
    )
    return ComponentRoutingProblem(
        ProblemFingerprint="problem",
        PlacementFingerprint=f"placement:{Delta}",
        LocalTemplateFingerprint="local",
        SelectedClusters=(0,),
        ComponentSignals=(Signal,),
        LocalClaims=(),
        Fabric=Fabric,
        OwnedTerminalDomains=(
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Source,
                TerminalRole="source",
                TerminalFingerprint="source",
                Candidates=(Candidate(Source),),
            ),
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Target,
                TerminalRole="target",
                TerminalFingerprint="target",
                Candidates=(Candidate(Target),),
            ),
        ),
        ExternalContinuationTerminals=(
            (Signal, Move((12, 7, 0)), "target"),
        ),
        ForeignEscapeDomains=(),
        MaximumPowerDistance=15,
        DomainComplete=True,
        ResourceGraph=_ResourceGraph(),
        Interface=Interface,
    )


def _Guide(Problem):
    Signal = Problem.ComponentSignals[0]
    Position = Problem.Fabric.Nodes[-1]
    Resource = RoutingResourceId(
        RoutingResourceKind.Wire,
        Position,
    )
    return ChannelPlan(
        Profiles={},
        SignalOrder=(Signal,),
        TrunkSignals=frozenset(),
        Guides={Signal: frozenset(
            (X, Position[2])
            for X in range(Position[0], Position[0] + 21)
        )},
        CorridorUsage={},
        CorridorCosts={},
        CorridorCapacity=1,
        Layers={Signal: 0},
        ResourceUsage={Resource: 1},
        ResourceOverflow={},
        ResourceClaimsBySignal={Signal: frozenset((Resource,))},
        SourceAccessTransitions={},
        TargetAccessTransitions={},
    )


def _Placed(Problem):
    Signal = Problem.ComponentSignals[0]
    return SimpleNamespace(
        ComponentGraph=SimpleNamespace(
            StructuralFingerprint="component-graph",
            Channels=(
                SimpleNamespace(
                    Signal=Signal,
                    FeedthroughComponentIds=(),
                ),
            ),
        ),
    )


def _AccessCertificate(Problem, Placed, Resources):
    MinimumPlacementY = min(
        Value[1] for Value in Problem.Fabric.Nodes
    ) - 7
    return BuildComponentCutAccessFeasibilityCertificate(
        Problem,
        Resources.ResourceGraph,
        LayerCount=1,
        MinimumPlacementY=MinimumPlacementY,
        ComponentGraphFingerprint=(
            Placed.ComponentGraph.StructuralFingerprint
        ),
    )


def _Assembly(Problem, Resources=None):
    Resources = Resources or RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Placed = _Placed(Problem)
    Certificate = _AccessCertificate(Problem, Placed, Resources)
    return BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=Certificate,
    )


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
        "Compiler.Routing.AuthoritativePlanner."
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


def test_physical_channel_finalization_requires_an_exterior_guide():
    Assembly = _Assembly(_Problem())
    Port = Assembly.Plan.Ports[0]
    Channel = next(
        Value
        for Value in Assembly.Plan.Channels
        if Value.Signal == Port.Signal
    )
    Channel = replace(
        Channel,
        GuideCells=((
            Assembly.Plan.EnvelopeMinimum[0],
            Assembly.Plan.EnvelopeMinimum[2],
        ),),
    )

    with pytest.raises(RoutingStageError) as Raised:
        FinalizePhysicalComponentChannelReservations(
            (Channel,),
            (Port,),
            _ResourceGraph(),
            MinimumPlacementY=0,
            EnvelopeMinimum=Assembly.Plan.EnvelopeMinimum,
            EnvelopeMaximum=Assembly.Plan.EnvelopeMaximum,
        )
    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Raised.value.Failure.AffectedNets == (Port.Signal,)
    assert "does not intersect" in Raised.value.Failure.Detail
    assert Raised.value.Failure.Diagnostics[
        "PortReservationFingerprint"
    ] == Port.ReservationFingerprint


def test_physical_channel_finalization_freezes_connected_port_and_guide():
    Assembly = _Assembly(_Problem())
    Port = Assembly.Plan.Ports[0]
    Source = next(
        Value
        for Value in Assembly.Plan.Channels
        if Value.Signal == Port.Signal
    )
    GuideCells = tuple(
        (X, Port.GlobalPath[-1][2])
        for X in range(
            Port.GlobalPath[0][0],
            Port.GlobalPath[-1][0] + 3,
        )
    )
    Channel = replace(Source, GuideCells=GuideCells)

    (Finalized,) = FinalizePhysicalComponentChannelReservations(
        (Channel,),
        (Port,),
        _ResourceGraph(),
        MinimumPlacementY=0,
        EnvelopeMinimum=Assembly.Plan.EnvelopeMinimum,
        EnvelopeMaximum=Assembly.Plan.EnvelopeMaximum,
    )

    assert frozenset(Port.GlobalPath) <= Finalized.Claims.WireCells
    assert (
        GuideCells[-1][0],
        Port.GlobalPath[-1][1],
        GuideCells[-1][1],
    ) in Finalized.Claims.WireCells
    assert Finalized.ResourceIds


def test_physical_channel_finalization_excludes_ordinary_keepout_nodes():
    Channel = PhysicalComponentChannelReservation(
        Signal="GlobalOnly",
        Layer=0,
        GuideCells=((-2, 0), (-1, 0), (0, 0)),
        ResourceIds=(),
        Claims=RoutingResourceClaims(),
        Capacity=1,
    )

    (Finalized,) = FinalizePhysicalComponentChannelReservations(
        (Channel,),
        (),
        _ResourceGraph(),
        MinimumPlacementY=0,
        EnvelopeMinimum=(0, 0, -1),
        EnvelopeMaximum=(2, 8, 1),
    )

    assert Finalized.Claims.WireCells == frozenset((
        (-2, 1, 0),
        (-1, 1, 0),
    ))


def test_physical_channel_finalization_rejects_joint_capacity_conflict():
    First = PhysicalComponentChannelReservation(
        Signal="First",
        Layer=0,
        GuideCells=((-2, 0), (-1, 0)),
        ResourceIds=(),
        Claims=RoutingResourceClaims(),
        Capacity=1,
    )
    Second = replace(First, Signal="Second")

    with pytest.raises(RoutingStageError) as Raised:
        FinalizePhysicalComponentChannelReservations(
            (First, Second),
            (),
            _ResourceGraph(),
            MinimumPlacementY=0,
            EnvelopeMinimum=(0, 0, -1),
            EnvelopeMaximum=(2, 8, 1),
        )

    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Raised.value.Failure.AffectedNets == ("First", "Second")
    assert Raised.value.Failure.Diagnostics["ConflictPairs"] == [
        ["First", "Second"]
    ]


def test_physical_assembly_reserves_noncomponent_global_guides():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Placed = _Placed(Problem)
    Guide = _Guide(Problem)
    Guide = replace(
        Guide,
        SignalOrder=(*Guide.SignalOrder, "GlobalOnly"),
        Guides={
            **Guide.Guides,
            "GlobalOnly": frozenset(((100, 0), (101, 0))),
        },
        Layers={**Guide.Layers, "GlobalOnly": 0},
    )

    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    GlobalChannel = next(
        Value
        for Value in Assembly.Plan.Channels
        if Value.Signal == "GlobalOnly"
    )

    assert GlobalChannel.Claims.ResourceIds
    assert dict(
        Assembly.Problem.ReservedGlobalClaimsBySignal
    )["GlobalOnly"] == GlobalChannel.Claims
    CompileResult = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )
    assert CompileResult.Feasible


def test_complete_assembly_rejects_unrelated_foreign_corridor_conflict():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Guide = _Guide(Problem)
    SharedForeignGuide = frozenset(((100, 0), (101, 0)))
    Guide = replace(
        Guide,
        SignalOrder=(
            *Guide.SignalOrder,
            "ForeignA",
            "ForeignB",
        ),
        Guides={
            **Guide.Guides,
            "ForeignA": SharedForeignGuide,
            "ForeignB": SharedForeignGuide,
        },
        Layers={
            **Guide.Layers,
            "ForeignA": 0,
            "ForeignB": 0,
        },
    )

    with pytest.raises(RoutingStageError) as Raised:
        BuildPhysicalComponentAssemblyPlan(
            Placed,
            Problem,
            Guide,
            Resources,
            AccessCertificate=_AccessCertificate(
                Problem,
                Placed,
                Resources,
            ),
        )

    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Raised.value.Failure.AffectedNets == (
        "ForeignA",
        "ForeignB",
    )
    assert Raised.value.Failure.Diagnostics["ConflictPairs"] == [
        ["ForeignA", "ForeignB"]
    ]


def test_physical_replan_preserves_access_certificate_identity(monkeypatch):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Certificate = _AccessCertificate(Problem, Placed, Resources)
    ExpectedAssembly = _Assembly(Problem, Resources)
    Guide = _Guide(Problem)
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=Certificate,
    )
    Resources.PreparedPhysicalComponentUnboundProblem = Problem
    Resources.FrozenPhysicalComponentGlobalGuidePlan = Guide
    Resources.PreparedComponentAccessCertificate = Certificate
    Resources.PreparedPhysicalComponentPortFactorDomain = Preparation
    assert Preparation.AccessCertificate is Certificate

    def Solve(Value, _ResourcesValue, *, WorkCheck=None):
        assert Value is Preparation
        assert Value.AccessCertificate is Certificate
        return ExpectedAssembly

    monkeypatch.setattr(
        "Compiler.Routing.AuthoritativePlanner."
        "SolvePreparedPhysicalComponentPortFactorDomain",
        Solve,
    )

    Result = ReplanPhysicalComponentAssembly(
        SimpleNamespace(Placed=Placed),
        Resources=Resources,
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert Result is ExpectedAssembly
    assert Resources.PreparedPhysicalComponentPortFactorDomain is Preparation
    assert (
        Resources.FrozenPhysicalComponentAssemblyPlan
        is ExpectedAssembly.Plan
    )


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
    assert Assembly.Plan.StageOrder[0] == (
        "ComponentAccessCertification"
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


def test_component_access_certificate_is_rename_and_translation_stable():
    Certificates = []
    for Signal, Delta in (
        ("Alpha", (0, 0, 0)),
        ("Renamed", (0, 0, 0)),
        ("Alpha", (30, 4, 12)),
    ):
        Problem = _Problem(Signal, Delta)
        Placed = _Placed(Problem)
        Resources = RoutingResources(
            StaticGeometry=SimpleNamespace(),
            ResourceGraph=Problem.ResourceGraph,
        )
        Certificates.append(
            _AccessCertificate(Problem, Placed, Resources)
        )
    Base, Renamed, Translated = Certificates

    assert Base.StructuralFingerprint == Renamed.StructuralFingerprint
    assert Base.StructuralFingerprint == Translated.StructuralFingerprint
    assert Base.CertificateFingerprint == Renamed.CertificateFingerprint
    assert Base.CertificateFingerprint != Translated.CertificateFingerprint


def test_component_access_certificate_rejects_identity_mismatch():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Certificate = _AccessCertificate(Problem, Placed, Resources)

    with pytest.raises(ValueError, match="identity mismatch"):
        ValidateComponentAccessCertificateIdentity(
            Certificate,
            replace(Problem, PlacementFingerprint="changed-placement"),
            Resources.ResourceGraph,
            ComponentGraphFingerprint=(
                Placed.ComponentGraph.StructuralFingerprint
            ),
        )


def test_component_access_certificate_proves_empty_seam_domain():
    class NoEgressResourceGraph(_ResourceGraph):
        def BuildPrimitive(self, _First, _Second):
            return None

    Problem = replace(
        _Problem(),
        ResourceGraph=NoEgressResourceGraph(),
    )
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )

    Certificate = _AccessCertificate(Problem, Placed, Resources)

    assert Certificate.Complete
    assert not Certificate.Feasible
    assert Certificate.ProofKind == "perimeter-seam-empty"
    assert Certificate.AffectedSignals == ("Alpha",)


def test_physical_plan_binds_one_access_candidate_per_owned_terminal():
    Problem = _Problem()
    SourceDomain, TargetDomain = Problem.OwnedTerminalDomains
    AlternateAttachment = Problem.Fabric.Nodes[1]
    Alternate = ComponentTerminalAccessCandidate(
        CandidateFingerprint="alternate-source-access",
        Attachment=AlternateAttachment,
        Path=(SourceDomain.Terminal, AlternateAttachment),
        Claims=_Claims((
            SourceDomain.Terminal,
            AlternateAttachment,
        )),
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                SourceDomain,
                Candidates=(
                    *SourceDomain.Candidates,
                    Alternate,
                ),
            ),
            TargetDomain,
        ),
    )

    Assembly = _Assembly(Problem)
    Port = Assembly.Plan.Ports[0]
    CandidateFingerprintByTerminal = dict(zip(
        Port.OwnedTerminals,
        Port.OwnedCandidateFingerprints,
    ))

    assert len(Port.OwnedCandidateFingerprints) == len(
        Port.OwnedTerminals
    )
    assert all(
        len(Domain.Candidates) == 1
        and Domain.Candidates[0].CandidateFingerprint
        == CandidateFingerprintByTerminal[Domain.Terminal]
        for Domain in Assembly.Problem.OwnedTerminalDomains
    )
    assert all(
        Position in Port.Claims.WireCells
        for Domain in Assembly.Problem.OwnedTerminalDomains
        for Position in Domain.Candidates[0].Path
    )


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

    assert (
        Assembly.Plan.Ports[0].OwnedCandidateFingerprints[0]
        == "00-local-unrealizable"
    )
    assert (
        Assembly.Problem.OwnedTerminalDomains[0]
        .Candidates[0].CandidateFingerprint
        == "00-local-unrealizable"
    )


def test_distinct_ports_may_share_one_fabric_component():
    Signals = ("Alpha", "Beta")
    Cells = tuple((X, 7, 0) for X in range(31))
    Fabric = BuildComponentRoutingFabric(SimpleNamespace(
        PhysicalModel="test-shared-tree",
        ComponentId=3,
        ChannelFingerprint="shared-tree",
        Lanes=(SimpleNamespace(
            Cells=Cells,
            IngressNodes=(Cells[0], Cells[-1]),
        ),),
    ))

    def Candidate(Signal, Terminal):
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=f"{Signal}:{Terminal}",
            Attachment=Terminal,
            Path=(Terminal,),
            Claims=_Claims((Terminal,)),
        )

    TerminalPairs = {
        "Alpha": (Cells[0], Cells[3]),
        "Beta": (Cells[-4], Cells[-1]),
    }
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="shared-fabric-interface",
        ComponentId=3,
        OwnedSignals=Signals,
        Ports=tuple(
            ComponentInterfacePort(
                Signal=Signal,
                Direction="output",
                OwnedTerminals=TerminalPairs[Signal],
                ExternalTerminalCount=1,
            )
            for Signal in Signals
        ),
    )
    Problem = ComponentRoutingProblem(
        ProblemFingerprint="shared-fabric-problem",
        PlacementFingerprint="shared-fabric-placement",
        LocalTemplateFingerprint="shared-fabric-local",
        SelectedClusters=(0,),
        ComponentSignals=Signals,
        LocalClaims=(),
        Fabric=Fabric,
        OwnedTerminalDomains=tuple(
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Terminal,
                TerminalRole=(
                    "source" if Terminal == Terminals[0] else "target"
                ),
                TerminalFingerprint=f"{Signal}:{Terminal}:terminal",
                Candidates=(Candidate(Signal, Terminal),),
            )
            for Signal, Terminals in TerminalPairs.items()
            for Terminal in Terminals
        ),
        ExternalContinuationTerminals=(
            ("Alpha", (-12, 7, 0), "target"),
            ("Beta", (42, 7, 0), "target"),
        ),
        ForeignEscapeDomains=(),
        MaximumPowerDistance=15,
        DomainComplete=True,
        ResourceGraph=_ResourceGraph(),
        Interface=Interface,
    )
    Guide = ChannelPlan(
        Profiles={},
        SignalOrder=Signals,
        TrunkSignals=frozenset(),
        Guides={
            "Alpha": frozenset(((-3, 0), (-2, 0))),
            "Beta": frozenset(((32, 0), (33, 0))),
        },
        CorridorUsage={},
        CorridorCosts={},
        CorridorCapacity=1,
        Layers={"Alpha": 0, "Beta": 0},
        ResourceUsage={},
        ResourceOverflow={},
        ResourceClaimsBySignal={},
        SourceAccessTransitions={},
        TargetAccessTransitions={},
    )
    Placed = SimpleNamespace(
        ComponentGraph=SimpleNamespace(
            StructuralFingerprint="shared-fabric-graph",
            Channels=tuple(
                SimpleNamespace(
                    Signal=Signal,
                    FeedthroughComponentIds=(),
                )
                for Signal in Signals
            ),
        ),
    )
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )

    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    assert len(Assembly.Plan.Ports) == 2
    assert len({
        Port.FabricDomainFingerprint
        for Port in Assembly.Plan.Ports
    }) == 1
    Result = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )
    assert not Result.Feasible
    assert Result.Status == "architectural-unsatisfiable"


def test_seam_domain_uses_actual_egress_reach_not_fixed_perimeter_band():
    Signal = "Alpha"
    Cells = tuple((X, 7, 0) for X in range(15))
    Fabric = BuildComponentRoutingFabric(SimpleNamespace(
        PhysicalModel="deep-egress-test",
        ComponentId=3,
        ChannelFingerprint="deep-egress",
        Lanes=(SimpleNamespace(
            Cells=Cells,
            IngressNodes=(Cells[0], Cells[-1]),
        ),),
    ))
    Terminals = (Cells[7], Cells[8])

    def Candidate(Terminal):
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=f"access:{Terminal}",
            Attachment=Terminal,
            Path=(Terminal,),
            Claims=_Claims((Terminal,)),
        )

    Problem = replace(
        _Problem(),
        Fabric=Fabric,
        OwnedTerminalDomains=tuple(
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Terminal,
                TerminalRole=(
                    "source" if Terminal == Terminals[0] else "target"
                ),
                TerminalFingerprint=f"terminal:{Terminal}",
                Candidates=(Candidate(Terminal),),
            )
            for Terminal in Terminals
        ),
        Interface=ClosedComponentInterface(
            InterfaceFingerprint="deep-egress-interface",
            ComponentId=3,
            OwnedSignals=(Signal,),
            Ports=(ComponentInterfacePort(
                Signal=Signal,
                Direction="output",
                OwnedTerminals=Terminals,
                ExternalTerminalCount=1,
            ),),
        ),
        ExternalContinuationTerminals=(
            (Signal, (-12, 7, 0), "target"),
        ),
    )
    Guide = replace(
        _Guide(Problem),
        Guides={
            Signal: frozenset(((-2, 0), (-1, 0))),
        },
    )

    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    Port = Assembly.Plan.Ports[0]

    assert Port.FabricAttachment == Cells[7]
    assert Port.LocalPath[-1][0] < min(
        Position[0] for Position in Problem.Fabric.Nodes
    )


def test_joint_access_self_conflict_is_proved_by_assembly_factor_join():
    Problem = _Problem()
    ConflictNodes = frozenset(
        Domain.Terminal
        for Domain in Problem.OwnedTerminalDomains
    )

    class AccessConflictingResourceGraph(_ResourceGraph):
        def BuildRouteClaims(self, Nodes):
            Nodes = frozenset(Nodes)
            Claims = _Claims(Nodes)
            if not ConflictNodes <= Nodes:
                return Claims
            return replace(
                Claims,
                SupportCells=(
                    Claims.SupportCells
                    | frozenset((min(Nodes),))
                ),
            )

    Problem = replace(
        Problem,
        ResourceGraph=AccessConflictingResourceGraph(),
    )
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )

    with pytest.raises(RoutingStageError) as Error:
        _Assembly(Problem, Resources)

    Diagnostics = Error.value.Failure.Diagnostics
    assert Error.value.Failure.Reason == (
        RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    )
    assert Diagnostics[
        "AccessAssignmentSelfConflictCountBySignal"
    ]["Alpha"] > 0
    assert Diagnostics["PortAssignmentProofComplete"]


def test_factorized_certificate_preserves_bound_access_seam_correlation():
    Problem = _Problem()
    SourceDomain, TargetDomain = Problem.OwnedTerminalDomains
    Alternatives = tuple(
        replace(
            SourceDomain.Candidates[0],
            CandidateFingerprint=f"source-option-{Index:02d}",
        )
        for Index in range(16)
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(SourceDomain, Candidates=Alternatives),
            TargetDomain,
        ),
    )
    Events = []

    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Assembly = BuildPhysicalComponentAssemblyPlan(
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

    AccessEvents = tuple(
        Event
        for Event in Events
        if Event["Stage"]
        == "physical-terminal-access-assignment"
    )
    assert Assembly.Plan.Complete
    assert AccessEvents == ()
    assert all(
        Port.OwnedCandidateFingerprints
        for Port in Assembly.Plan.Ports
    )
    assert len(AccessEvents) < (
        len(Alternatives)
        * len(Problem.Fabric.Nodes)
        * 2
    )
    CapacityEvent = next(
        Event
        for Event in Events
        if Event["Stage"] == "physical-port-capacity"
    )
    assert {
        "LaneFactorExpansionCount",
        "AccessFactorExpansionCount",
        "SeamFactorExpansionCount",
        "FactorDomainPropagationCount",
        "ForwardSupportCheckCount",
        "ForwardSupportWitnessHitCount",
        "LaneArcConsistencyCheckCount",
        "FactorArcClosureCount",
        "FactorArcClosureCacheHitCount",
        "LaneArcSupportIntersectionCount",
    }.issubset(CapacityEvent)


def test_lane_factor_arc_propagation_reuses_compiled_support_relations():
    Domains = {
        "Alpha": ("a1", "a2"),
        "Beta": ("b1", "b2"),
        "Gamma": ("c1",),
    }
    Support = {
        ("Alpha", "a1", "Beta"): frozenset(("b1",)),
        ("Alpha", "a2", "Beta"): frozenset(("b2",)),
        ("Beta", "b1", "Alpha"): frozenset(("a1",)),
        ("Beta", "b2", "Alpha"): frozenset(("a2",)),
        ("Beta", "b1", "Gamma"): frozenset(("c1",)),
        ("Gamma", "c1", "Beta"): frozenset(("b1",)),
        ("Alpha", "a1", "Gamma"): frozenset(("c1",)),
        ("Alpha", "a2", "Gamma"): frozenset(("c1",)),
        ("Gamma", "c1", "Alpha"): frozenset(("a1", "a2")),
    }

    Propagated, IntersectionCount = (
        PropagateLaneFactorArcConsistency(Domains, Support)
    )

    assert Propagated == {
        "Alpha": ("a1",),
        "Beta": ("b1",),
        "Gamma": ("c1",),
    }
    assert IntersectionCount > 0
    Unsatisfiable, _ = PropagateLaneFactorArcConsistency(
        {"Alpha": ("a2",), "Beta": ("b1",)},
        Support,
    )
    assert Unsatisfiable is None


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


def test_component_compile_rejects_seam_inside_keepout_envelope():
    Assembly = _Assembly(_Problem())
    OriginalPort = Assembly.Plan.Ports[0]
    InteriorAttachment = OriginalPort.FabricAttachment
    ChangedPort = replace(
        OriginalPort,
        Attachment=InteriorAttachment,
        LocalPath=(InteriorAttachment,),
        GlobalPath=(
            InteriorAttachment,
            (
                InteriorAttachment[0] + 1,
                InteriorAttachment[1],
                InteriorAttachment[2],
            ),
        ),
    )
    ChangedPlan = replace(
        Assembly.Plan,
        Ports=(ChangedPort,),
    )
    ChangedProblem = replace(
        Assembly.Problem,
        PhysicalAssemblyPlan=ChangedPlan,
        Interface=replace(
            Assembly.Problem.Interface,
            PhysicalPortReservations=(ChangedPort,),
        ),
    )

    with pytest.raises(
        ValueError,
        match="seam ownership is malformed",
    ):
        CompileClosedComponent(
            ChangedProblem,
            AssemblyPlan=ChangedPlan,
            DiscoveryVariantLimit=None,
        )


def test_component_handoff_identity_error_is_typed(monkeypatch):
    Assembly = _Assembly(_Problem())
    Result = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )
    assert Result.Template is not None
    Channel = SimpleNamespace(
        PhysicalModel="test-tree",
        ChannelFingerprint="archived-channel",
        Lanes=(SimpleNamespace(
            Cells=((0, 7, 0), (1, 7, 0), (2, 7, 0)),
            IngressNodes=((0, 7, 0), (2, 7, 0)),
        ),),
    )
    Placed = PlacedDesign(
        Module=SimpleNamespace(),
        PlacedGates=[],
        LocalRouteClaims=(),
        LocalRouteDiagnostics={},
        RoutedComponentTemplates=(),
        InterClusterRoutingChannel=Channel,
    )

    def RejectHandoff(*_Arguments, **_Keywords):
        raise ValueError("test fabric identity mismatch")

    monkeypatch.setattr(
        ComponentPipeline,
        "ValidateRoutedComponentHandoff",
        RejectHandoff,
    )
    with pytest.raises(RoutingStageError) as Error:
        ComponentPipeline.AssembleClosedComponentForGlobalRouting(
            Placed,
            Result.Template,
            PhysicalAssemblyPlan=Assembly.Plan,
            PlacementFingerprint=(
                Assembly.Problem.PlacementFingerprint
            ),
            LocalTemplateFingerprint=(
                Assembly.Problem.LocalTemplateFingerprint
            ),
        )

    assert Error.value.Failure.Reason == (
        RoutingFailureReason.ComponentAssemblyIdentityMismatch
    )
    assert Error.value.Failure.Stage == (
        "ComponentAssemblyIdentityValidation"
    )
    assert "test fabric identity mismatch" in (
        Error.value.Failure.Detail
    )


def test_component_compile_rejects_reopened_terminal_access_domain():
    Assembly = _Assembly(_Problem())
    Domain = Assembly.Problem.OwnedTerminalDomains[0]
    Reopened = replace(
        Domain.Candidates[0],
        CandidateFingerprint="reopened-access",
    )
    Problem = replace(
        Assembly.Problem,
        OwnedTerminalDomains=(
            replace(
                Domain,
                Candidates=(
                    Domain.Candidates[0],
                    Reopened,
                ),
            ),
            *Assembly.Problem.OwnedTerminalDomains[1:],
        ),
    )

    with pytest.raises(
        ValueError,
        match="terminal access is not bound exactly",
    ):
        CompileClosedComponent(
            Problem,
            AssemblyPlan=Assembly.Plan,
            DiscoveryVariantLimit=None,
        )


def test_component_compile_rejects_local_feedback_no_goods():
    Assembly = _Assembly(_Problem())

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
    ComponentPipeline._CompletedComponentTemplateCache.clear()
    Original = _Assembly(_Problem("Original"))
    First = CompileClosedComponent(
        Original.Problem,
        AssemblyPlan=Original.Plan,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible and First.Template is not None
    assert not First.Diagnostics["CompletedTemplateCacheHit"]

    Delta = (31, 0, 13)
    Renamed = _Assembly(_Problem("Renamed", Delta))
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
    assert set(
        Failure.Diagnostics["PortDomainGenerationStatus"].values()
    ) == {"unvisited"}


def test_component_fabric_preserves_parallel_lane_capacity_domains():
    First = tuple((X, 7, 0) for X in range(3))
    Second = tuple((X, 7, 6) for X in range(3))
    Fabric = BuildComponentRoutingFabric(SimpleNamespace(
        PhysicalModel="parallel-test",
        Lanes=(
            SimpleNamespace(
                Cells=First,
                IngressNodes=(First[0], First[-1]),
            ),
            SimpleNamespace(
                Cells=Second,
                IngressNodes=(Second[0], Second[-1]),
            ),
        ),
    ))

    Connected = AugmentComponentRoutingFabric(
        Fabric,
        (First[0], Second[0]),
        _ResourceGraph(),
    )

    assert Connected.Complete
    assert Connected.TopologyKind == "closed-component-port-forest-v3"
    assert len(Connected.Edges) == len(Connected.Nodes) - 2

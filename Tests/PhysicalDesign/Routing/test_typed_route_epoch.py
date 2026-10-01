"""Independent ownership and physical-behavior checks for frozen route epochs."""

from dataclasses import FrozenInstanceError, dataclass, replace
from types import MappingProxyType, SimpleNamespace

import pytest

from PhysicalDesign.Policy import PhysicalDesignPolicy
from PhysicalDesign.Redstone.Technology import RedstoneRoutingTechnology
from PhysicalDesign.Resources.ResourceGraph import (
    LocalRouteClaim,
    PinAccessPortal,
    RoutingGraphRegion,
    RoutingResourceClaims,
    RoutingResourceGraph,
    RoutingResourceId,
    RoutingResourceKind,
)
from PhysicalDesign.Routing.Global.Orchestration.TypedRouteEpoch import (
    BuildTypedRouteMaterializationEpoch,
)
from PhysicalDesign.Routing.Global.Ports.Portals import _MaterializeCandidate
from PhysicalDesign.Routing.Planning.ChannelPlanner import (
    NetRoutingProfile,
    SignalRouteSeed,
)
from PhysicalDesign.Routing.Planning.LocalFirst import CoarseGuidePlan, DerivedRoutingBudget


def Inputs():
    Technology = RedstoneRoutingTechnology()
    Nodes = ((0, 1, 0), (1, 1, 0), (2, 1, 0))
    Edges = frozenset(((Nodes[0], Nodes[1]), (Nodes[1], Nodes[2])))
    Graph = RoutingResourceGraph(frozenset(), frozenset(), frozenset(), Technology)
    Claims = RoutingResourceClaims(WireCells=frozenset((Nodes[0],)))
    LocalClaim = LocalRouteClaim(
        "signal", -1, Nodes[0], (), (Nodes[0],), frozenset((Nodes[0],)),
        frozenset(), Claims,
    )
    Seed = SignalRouteSeed(
        "signal", Nodes[0], (LocalClaim,), (), (Nodes[2],), (Nodes[0],),
        frozenset((RoutingResourceId(RoutingResourceKind.Wire, Nodes[0]),)),
    )
    Profile = NetRoutingProfile(
        "signal", Nodes[0], (Nodes[2],), 2, 1, 0, 0, False,
        (Nodes[0],), {Nodes[2]: (Nodes[2],)}, Seed,
    )
    Source = PinAccessPortal(
        "selected-source", "signal", Nodes[0], 0, (Nodes[0],), frozenset(),
        Claims, 1, 0, 0, 1,
    )
    Target = PinAccessPortal(
        "selected-target", "signal", Nodes[2], 0, (Nodes[2],), frozenset(),
        RoutingResourceClaims(WireCells=frozenset((Nodes[2],))), 1, 0, 0, 1,
    )
    Guide = frozenset((Position[0], Position[2]) for Position in Nodes)
    Metadata = ((Source, (Target,), Guide, 0, "X", 0, 0),)
    State = SimpleNamespace(
        Profiles={"signal": Profile},
        LayerCount=1,
        UnreservedPortalMode=False,
        Region=RoutingGraphRegion((0, 2, 1, 1, 0, 0), frozenset(Nodes), Edges),
        Resources=SimpleNamespace(
            ResourceGraph=Graph, PreparingPhysicalComponentGlobalChannels=False,
            OpaqueUnrelatedState=object(),
        ),
        Technology=Technology,
        Policy=PhysicalDesignPolicy(),
        CoarsePlan=CoarseGuidePlan(
            {"signal": Guide}, {"signal": 0}, {"signal": "X"}, {"signal": 0},
            {}, {}, frozenset(), (),
        ),
        AdaptiveBudget=DerivedRoutingBudget(8, 1, 1, 1, 2, 32, 32, 0, 1, 10.0, ("fixture",)),
        ForeignSelectedPinAccessClaimsBySignal={"signal": (("foreign", Claims),)},
        FrozenComponentClaims=(LocalClaim,),
        AssemblySpecificSiblingAperturesBySignal={"signal": (("sibling", Claims),)},
        OpaqueUnrelatedState=object(),
    )
    return State, Metadata, Nodes


def test_epoch_owns_nested_profiles_metadata_guides_claims_and_controls():
    State, Metadata, Nodes = Inputs()
    MutablePath = [Nodes[2]]
    State.Profiles["signal"].TargetAccessPaths[Nodes[2]] = MutablePath
    MutableClaims = {Nodes[0]}
    State.ForeignSelectedPinAccessClaimsBySignal["signal"] = [
        ("foreign", RoutingResourceClaims(WireCells=MutableClaims))
    ]
    Epoch = BuildTypedRouteMaterializationEpoch(State, Metadata)

    MutablePath.clear()
    MutableClaims.clear()
    State.Profiles.clear()
    State.CoarsePlan.Guides.clear()
    State.ForeignSelectedPinAccessClaimsBySignal.clear()
    State.AssemblySpecificSiblingAperturesBySignal.clear()
    State.FrozenComponentClaims = ()
    State.Policy = replace(State.Policy, Seed=99)
    State.AdaptiveBudget = replace(State.AdaptiveBudget, CandidatesPerNet=20)
    State.Resources.PreparingPhysicalComponentGlobalChannels = True
    State.LayerCount = 7
    State.UnreservedPortalMode = True

    assert Epoch.Profiles["signal"].TargetAccessPaths[Nodes[2]] == (Nodes[2],)
    assert Epoch.ForeignSelectedPinAccessClaimsBySignal["signal"][0][1].WireCells == {Nodes[0]}
    assert Epoch.AssemblySpecificSiblingAperturesBySignal["signal"][0][0] == "sibling"
    assert Epoch.FrozenComponentClaims[0].Signal == "signal"
    assert Epoch.CoarsePlan.Guides["signal"] == Metadata[0][2]
    assert Epoch.Policy.Seed == 0
    assert Epoch.AdaptiveBudget.CandidatesPerNet == 2
    assert Epoch.Resources.PreparingPhysicalComponentGlobalChannels is False
    assert Epoch.LayerCount == 1
    assert Epoch.UnreservedPortalMode is False
    assert Epoch.Metadata == Metadata
    assert Epoch.Metadata[0][0] is not Metadata[0][0]
    with pytest.raises(TypeError):
        Epoch.Profiles["signal"].TargetAccessPaths[Nodes[2]] = ()
    with pytest.raises(TypeError):
        Epoch.CoarsePlan.Layers["signal"] = 2
    with pytest.raises(FrozenInstanceError):
        Epoch.Metadata[0][0].PortalId = "changed"
    with pytest.raises(FrozenInstanceError):
        Epoch.Resources.PreparingPhysicalComponentGlobalChannels = True
    assert not hasattr(Epoch, "OpaqueUnrelatedState")
    assert not hasattr(Epoch.Resources, "OpaqueUnrelatedState")


def test_epoch_preserves_exact_frozen_producer_types_and_methods():
    State, Metadata, Nodes = Inputs()
    Epoch = BuildTypedRouteMaterializationEpoch(State, Metadata)
    for Name in ("Policy", "Technology", "Region", "CoarsePlan", "AdaptiveBudget"):
        assert type(getattr(Epoch, Name)) is type(getattr(State, Name))
        assert getattr(Epoch, Name) is not getattr(State, Name)
    assert type(Epoch.Profiles["signal"]) is NetRoutingProfile
    assert type(Epoch.Profiles["signal"].Seed) is SignalRouteSeed
    assert type(Epoch.Metadata[0][0]) is PinAccessPortal
    assert Epoch.Policy.ToDictionary() == State.Policy.ToDictionary()
    assert Epoch.AdaptiveBudget.ToDictionary() == State.AdaptiveBudget.ToDictionary()
    assert Epoch.CoarsePlan.ToDictionary() == State.CoarsePlan.ToDictionary()
    assert Epoch.Region.ContainsEdge(Nodes[0], Nodes[1]) is True
    assert Epoch.Technology.NeighborPositions(Nodes[0]) == State.Technology.NeighborPositions(Nodes[0])
    assert Epoch.Profiles["signal"].Seed.PreOwnedResources == {
        RoutingResourceId(RoutingResourceKind.Wire, Nodes[0])
    }


def test_epoch_rebuilds_resource_authority_without_inheriting_cached_answers():
    State, Metadata, Nodes = Inputs()
    Source = State.Resources.ResourceGraph
    Source._RouteClaimsCache[frozenset(Nodes)] = RoutingResourceClaims()
    Source._RouteClaimsCacheOrder.append(frozenset(Nodes))
    Source.__dict__["StaticKeepOut"] = frozenset((Nodes[0],))
    Epoch = BuildTypedRouteMaterializationEpoch(State, Metadata)
    Graph = Epoch.Resources.ResourceGraph
    assert isinstance(Graph, RoutingResourceGraph)
    assert Graph is not Source
    assert Graph._RouteClaimsCache == {}
    assert Graph._RegionCache == {}
    assert "StaticKeepOut" not in Graph.__dict__
    assert Graph.IsLegalNode(Nodes[0]) is True
    assert Source.IsLegalNode(Nodes[0]) is False
    assert Graph.BuildRouteClaims(Nodes).WireCells == frozenset(Nodes)
    assert Graph._RouteClaimsCache is not Source._RouteClaimsCache
    assert Graph._RouteClaimsCache
    assert Source.BuildRouteClaims(Nodes).WireCells == frozenset()


@pytest.mark.parametrize("Name", (
    "ActualBlocks", "ElectricalBlocks", "SolidBlocks", "StaticKeepOutBlocks",
    "Technology", "GraphVersion", "BlockStates", "_FrozenBlockStates", "StaticKeepOut",
))
def test_epoch_graph_rejects_authority_rebinding_or_deletion(Name):
    State, Metadata, _Nodes = Inputs()
    Graph = BuildTypedRouteMaterializationEpoch(State, Metadata).Resources.ResourceGraph
    with pytest.raises(AttributeError, match="authority is immutable"):
        setattr(Graph, Name, None)
    with pytest.raises(AttributeError, match="authority is immutable"):
        delattr(Graph, Name)


def test_epoch_graph_closes_over_all_current_authoritative_inputs():
    State, Metadata, Nodes = Inputs()
    State.Resources.ResourceGraph = RoutingResourceGraph(
        ActualBlocks={Nodes[0]}, ElectricalBlocks={(4, 1, 0)}, SolidBlocks={(6, 0, 0)},
        Technology=replace(State.Technology, TrackPitch=5), GraphVersion="epoch-fixture-v1",
        StaticKeepOutBlocks={(8, 1, 0)},
        BlockStates={(6, 0, 0): {"Name": "minecraft:stone", "Properties": {"nested": [1, 2]}}},
    )
    Source = State.Resources.ResourceGraph
    Graph = BuildTypedRouteMaterializationEpoch(State, Metadata).Resources.ResourceGraph
    Source.ActualBlocks.clear()
    Source.ElectricalBlocks.clear()
    Source.SolidBlocks.clear()
    Source.StaticKeepOutBlocks.clear()
    Source.Technology = State.Technology
    assert Graph.ActualBlocks == {Nodes[0]}
    assert Graph.ElectricalBlocks == {(4, 1, 0)}
    assert Graph.SolidBlocks == {(6, 0, 0)}
    assert Graph.StaticKeepOutBlocks == {(8, 1, 0)}
    assert Graph.Technology.TrackPitch == 5
    assert Graph.GraphVersion == "epoch-fixture-v1"
    assert Graph.BlockStates == Source.BlockStates
    assert Graph.BlockStates is not Source.BlockStates
    assert Graph.BlockStates[(6, 0, 0)] is not Source.BlockStates[(6, 0, 0)]
    with pytest.raises(TypeError):
        Graph.BlockStates[(6, 0, 0)]["Properties"]["nested"][0] = 3


def test_new_epoch_observes_current_values_instead_of_mutable_object_identity():
    State, Metadata, Nodes = Inputs()
    First = BuildTypedRouteMaterializationEpoch(State, Metadata)
    State.Profiles["signal"].TargetAccessPaths[Nodes[2]] = (Nodes[1], Nodes[2])
    State.CoarsePlan.Lanes["signal"] = 5
    State.Resources.ResourceGraph.ActualBlocks = frozenset((Nodes[0],))
    Second = BuildTypedRouteMaterializationEpoch(State, Metadata)
    assert First.Profiles["signal"].TargetAccessPaths != Second.Profiles["signal"].TargetAccessPaths
    assert First.CoarsePlan.Lanes["signal"] == 0
    assert Second.CoarsePlan.Lanes["signal"] == 5
    assert First.Resources.ResourceGraph.IsLegalNode(Nodes[0]) is True
    assert Second.Resources.ResourceGraph.IsLegalNode(Nodes[0]) is False


def test_frozen_epoch_preserves_real_selected_portal_materialization():
    State, Metadata, Nodes = Inputs()
    Epoch = BuildTypedRouteMaterializationEpoch(State, Metadata)

    def Materialize(InputsValue, MetadataValue):
        Source, Targets, Guide, Layer, Axis, Lane, Variant = MetadataValue
        return _MaterializeCandidate(
            "signal", InputsValue.Profiles["signal"], Source, Targets, Guide,
            Layer, Axis, Lane, Variant, Nodes, InputsValue.Region,
            InputsValue.Resources, InputsValue.Technology, 1,
        )

    Original = Materialize(State, Metadata[0])
    Frozen = Materialize(Epoch, Epoch.Metadata[0])
    assert Original is not None
    assert Frozen == Original
    assert Frozen.Nodes == frozenset(Nodes)
    assert Frozen.SourcePortalId == "selected-source"
    assert Frozen.TargetPortalIds == {Nodes[2]: "selected-target"}


@pytest.mark.parametrize("Value", (object(), SimpleNamespace(Value=1), float("nan")))
def test_epoch_rejects_opaque_or_nonfinite_field_trees(Value):
    State, Metadata, _Nodes = Inputs()
    State.Profiles["unsupported"] = Value
    with pytest.raises(TypeError):
        BuildTypedRouteMaterializationEpoch(State, Metadata)


def test_epoch_rejects_cycles_and_nonfrozen_dataclasses_without_copy_hooks():
    State, Metadata, _Nodes = Inputs()
    Cyclic = []
    Cyclic.append(Cyclic)
    State.Profiles["unsupported"] = Cyclic
    with pytest.raises(TypeError, match="cyclic"):
        BuildTypedRouteMaterializationEpoch(State, Metadata)

    @dataclass
    class MutableRecord:
        Value: int

        def __deepcopy__(self, Memo):
            pytest.fail("opaque copy hook was called")

    State.Profiles["unsupported"] = MutableRecord(1)
    with pytest.raises(TypeError, match="frozen dataclass"):
        BuildTypedRouteMaterializationEpoch(State, Metadata)


@pytest.mark.parametrize("Name,Value", (("LayerCount", True), ("LayerCount", 0), ("UnreservedPortalMode", 1)))
def test_epoch_requires_exact_candidate_pool_controls(Name, Value):
    State, Metadata, _Nodes = Inputs()
    setattr(State, Name, Value)
    with pytest.raises(TypeError, match=Name):
        BuildTypedRouteMaterializationEpoch(State, Metadata)


def test_epoch_shares_only_certified_builtin_forests_and_reowns_mutable_descendants():
    State, Metadata, Nodes = Inputs()
    Mutable = [Nodes[2]]
    State.Profiles["signal"].TargetAccessPaths[Nodes[2]] = (Mutable,)
    Epoch = BuildTypedRouteMaterializationEpoch(State, Metadata)
    assert Epoch.Region is not State.Region
    assert Epoch.Region.Nodes is State.Region.Nodes
    assert Epoch.Region.Edges is State.Region.Edges
    assert Epoch.Metadata[0][0] is not Metadata[0][0]
    assert Epoch.Profiles["signal"].TargetAccessPaths is not State.Profiles["signal"].TargetAccessPaths
    Mutable.clear()
    assert Epoch.Profiles["signal"].TargetAccessPaths[Nodes[2]] == ((Nodes[2],),)
    State.Resources.ResourceGraph.ActualBlocks = frozenset((Nodes[0],))
    assert Epoch.Resources.ResourceGraph.ActualBlocks == frozenset()
    assert Epoch.Resources.ResourceGraph.IsLegalNode(Nodes[0]) is True


def test_reused_certificate_cache_recaptures_mutable_descendants_and_dataclasses():
    State, Metadata, Nodes = Inputs()
    Cache = {}
    MutablePath = [Nodes[2]]
    BackingPaths = {Nodes[2]: MutablePath}
    Profile = replace(
        State.Profiles["signal"], TargetAccessPaths=MappingProxyType(BackingPaths),
    )
    State.Profiles["signal"] = Profile
    MutableCells = {Nodes[0]}
    Claims = RoutingResourceClaims(WireCells=MutableCells)
    State.ForeignSelectedPinAccessClaimsBySignal["signal"] = (("foreign", Claims),)
    MutableTargets = list(Metadata[0][1])
    Metadata = ((Metadata[0][0], MutableTargets, *Metadata[0][2:]),)
    First = BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)

    MutablePath.insert(0, Nodes[1])
    BackingPaths[Nodes[1]] = [Nodes[0], Nodes[1]]
    MutableCells.add(Nodes[1])
    MutableTargets.append(replace(MutableTargets[0], PortalId="new-target"))
    Second = BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)

    assert First.Profiles["signal"].TargetAccessPaths == {Nodes[2]: (Nodes[2],)}
    assert Second.Profiles["signal"].TargetAccessPaths == {
        Nodes[2]: (Nodes[1], Nodes[2]), Nodes[1]: (Nodes[0], Nodes[1]),
    }
    assert First.ForeignSelectedPinAccessClaimsBySignal["signal"][0][1].WireCells == {Nodes[0]}
    assert Second.ForeignSelectedPinAccessClaimsBySignal["signal"][0][1].WireCells == {
        Nodes[0], Nodes[1],
    }
    assert tuple(Target.PortalId for Target in First.Metadata[0][1]) == ("selected-target",)
    assert tuple(Target.PortalId for Target in Second.Metadata[0][1]) == (
        "selected-target", "new-target",
    )
    for Epoch in (First, Second):
        assert Epoch.Profiles["signal"] is not Profile
        assert Epoch.Profiles["signal"].Seed is not Profile.Seed
        assert Epoch.Profiles["signal"].TargetAccessPaths is not Profile.TargetAccessPaths
        assert Epoch.ForeignSelectedPinAccessClaimsBySignal["signal"][0][1] is not Claims
        assert Epoch.Metadata[0][0] is not Metadata[0][0]
    assert First.Profiles["signal"] is not Second.Profiles["signal"]
    assert First.Metadata[0][0] is not Second.Metadata[0][0]
    MutablePath.clear()
    BackingPaths.clear()
    MutableCells.clear()
    MutableTargets.clear()
    assert Second.Profiles["signal"].TargetAccessPaths[Nodes[2]] == (Nodes[1], Nodes[2])
    assert Second.ForeignSelectedPinAccessClaimsBySignal["signal"][0][1].WireCells == {
        Nodes[0], Nodes[1],
    }
    assert len(Second.Metadata[0][1]) == 2


def test_reused_certificate_cache_observes_replaced_region_and_graph_authority():
    State, Metadata, Nodes = Inputs()
    Cache = {}
    First = BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)
    assert First.Resources.ResourceGraph.BuildRouteClaims(Nodes).WireCells == frozenset(Nodes)
    State.Region = RoutingGraphRegion(
        (1, 2, 1, 1, 0, 0), frozenset(Nodes[1:]), frozenset(((Nodes[1], Nodes[2]),)),
    )
    State.Resources.ResourceGraph = RoutingResourceGraph(
        frozenset((Nodes[0],)), frozenset(), frozenset(), State.Technology,
        GraphVersion="replacement-graph",
    )
    State.Resources.ResourceGraph._RouteClaimsCache[frozenset(Nodes)] = RoutingResourceClaims()
    Second = BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)

    assert First.Region.Nodes == frozenset(Nodes)
    assert First.Region.ContainsEdge(Nodes[0], Nodes[1]) is True
    assert Second.Region.Nodes == frozenset(Nodes[1:])
    assert Second.Region.Bounds == (1, 2, 1, 1, 0, 0)
    assert Second.Region.ContainsEdge(Nodes[0], Nodes[1]) is False
    assert Second.Region.ContainsEdge(Nodes[1], Nodes[2]) is True
    assert Second.Region is not State.Region
    assert First.Resources.ResourceGraph.IsLegalNode(Nodes[0]) is True
    assert Second.Resources.ResourceGraph.IsLegalNode(Nodes[0]) is False
    assert Second.Resources.ResourceGraph.GraphVersion == "replacement-graph"
    assert Second.Resources.ResourceGraph._RouteClaimsCache == {}
    assert Second.Resources.ResourceGraph._RouteClaimsCache is not First.Resources.ResourceGraph._RouteClaimsCache
    assert Second.Resources.ResourceGraph.BuildRouteClaims(Nodes[1:]).WireCells == frozenset(Nodes[1:])


def test_reused_certificate_cache_retains_exact_immutable_forests_but_reowns_records():
    @dataclass(frozen=True)
    class FrozenRecord:
        Value: object

    State, Metadata, _Nodes = Inputs()
    Forest = (
        None, True, 7, "primitive", 0.25,
        frozenset((None, False, 2, "nested", 0.5)),
        (("leaf",), frozenset(("a", "b", "c", "d"))),
    )
    Record = FrozenRecord(Forest)
    State.Profiles["fixture"] = Record
    Cache = {}
    First = BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)
    Second = BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)

    assert First.Profiles["fixture"].Value is Forest
    assert Second.Profiles["fixture"].Value is Forest
    assert First.Profiles["fixture"] is not Record
    assert Second.Profiles["fixture"] is not Record
    assert First.Profiles["fixture"] is not Second.Profiles["fixture"]
    assert Cache[id(Forest)] is Forest
    assert all(Key == id(Value) for Key, Value in Cache.items())


@pytest.fixture
def SaturatedImmutableCache():
    State, Metadata, _Nodes = Inputs()
    Guides = {
        str(Index): frozenset((Index, Offset) for Offset in range(4))
        for Index in range(2050)
    }
    State.CoarsePlan = replace(State.CoarsePlan, Guides=Guides)
    Cache = {}
    Epoch = BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)
    assert Epoch.CoarsePlan.Guides == Guides
    assert len(Cache) == 2048
    assert all(Key == id(Value) for Key, Value in Cache.items())
    return Cache


def test_saturated_certificate_cache_preserves_current_values_and_owned_snapshots(SaturatedImmutableCache):
    State, Metadata, Nodes = Inputs()
    MutablePath = [Nodes[2]]
    State.Profiles["signal"].TargetAccessPaths[Nodes[2]] = (MutablePath,)
    First = BuildTypedRouteMaterializationEpoch(
        State, Metadata, _ImmutableForestCache=SaturatedImmutableCache,
    )
    MutablePath.insert(0, Nodes[1])
    Second = BuildTypedRouteMaterializationEpoch(
        State, Metadata, _ImmutableForestCache=SaturatedImmutableCache,
    )
    Fresh = BuildTypedRouteMaterializationEpoch(State, Metadata)

    assert First.Profiles["signal"].TargetAccessPaths[Nodes[2]] == ((Nodes[2],),)
    assert Second.Profiles["signal"].TargetAccessPaths[Nodes[2]] == ((Nodes[1], Nodes[2]),)
    assert Second.Profiles == Fresh.Profiles
    assert Second.Metadata == Fresh.Metadata
    assert Second.Region == Fresh.Region
    assert Second.Region.Nodes is State.Region.Nodes
    assert Second.Region.Edges is State.Region.Edges
    assert len(SaturatedImmutableCache) == 2048


@pytest.mark.parametrize("InvalidKind,Error", (
    ("opaque", "unsupported materialization value"),
    ("nan", "finite floats"),
    ("inf", "finite floats"),
    ("negative-inf", "finite floats"),
    ("cycle", "cyclic"),
    ("tuple-subclass", "unsupported materialization value"),
    ("frozenset-subclass", "unsupported materialization value"),
    ("int-subclass", "unsupported materialization value"),
    ("str-subclass", "unsupported materialization value"),
    ("float-subclass", "unsupported materialization value"),
))
def test_saturated_certificate_cache_preserves_rejections(SaturatedImmutableCache, InvalidKind, Error):
    State, Metadata, Nodes = Inputs()
    Values = {
        "opaque": object(), "nan": float("nan"), "inf": float("inf"),
        "negative-inf": -float("inf"),
        "tuple-subclass": type("TupleSubclass", (tuple,), {})((1, 2, 3, 4)),
        "frozenset-subclass": type("FrozenSetSubclass", (frozenset,), {})((1, 2, 3, 4)),
        "int-subclass": type("IntSubclass", (int,), {})(1),
        "str-subclass": type("StrSubclass", (str,), {})("value"),
        "float-subclass": type("FloatSubclass", (float,), {})(0.5),
    }
    if InvalidKind == "cycle":
        Value = []
        Value.append(Value)
    else:
        Value = Values[InvalidKind]
    State.Profiles["signal"].TargetAccessPaths[Nodes[2]] = (Value,)

    with pytest.raises(TypeError, match=Error) as Fresh:
        BuildTypedRouteMaterializationEpoch(State, Metadata)
    with pytest.raises(TypeError, match=Error) as Saturated:
        BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=SaturatedImmutableCache)
    assert str(Saturated.value) == str(Fresh.value)
    assert len(SaturatedImmutableCache) == 2048


@pytest.mark.parametrize("Container", ("direct", "tuple", "frozenset"))
def test_warm_certificate_cache_rejects_metaclass_builtin_equality_spoofing(Container):
    Comparisons = []

    class BuiltinSpoofingMeta(type):
        __hash__ = type.__hash__

        def __eq__(self, Other):
            Comparisons.append(Other)
            return True

    class OpaqueMutableValue(metaclass=BuiltinSpoofingMeta):
        def __init__(self, Payload):
            self.Payload = Payload

    State, Metadata, Nodes = Inputs()
    Guide = frozenset(((0, 0), (1, 0), (2, 0), (3, 0)))
    State.CoarsePlan.Guides["signal"] = Guide
    Cache = {}
    First = BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)
    assert Cache
    PreviousCertificates = dict(Cache)
    Value = OpaqueMutableValue([Nodes[2]])
    Wrapped = Value if Container == "direct" else (
        (Value,) if Container == "tuple" else frozenset((Value,))
    )
    State.Profiles["signal"].TargetAccessPaths[Nodes[2]] = Wrapped

    with pytest.raises(TypeError, match="unsupported materialization value"):
        BuildTypedRouteMaterializationEpoch(State, Metadata, _ImmutableForestCache=Cache)

    Value.Payload.clear()
    assert Comparisons == []
    assert Cache.keys() == PreviousCertificates.keys()
    assert all(Cache[Key] is Certificate for Key, Certificate in PreviousCertificates.items())
    assert First.Profiles["signal"].TargetAccessPaths == {Nodes[2]: (Nodes[2],)}
    assert First.CoarsePlan.Guides["signal"] == Guide

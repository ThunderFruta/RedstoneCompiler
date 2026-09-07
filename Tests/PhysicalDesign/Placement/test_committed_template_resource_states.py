"""Observable local-routing consumption of placed-template resource states."""

from dataclasses import replace
from types import SimpleNamespace

from Compilation.Ir.Models import Gate, GateKind
from Formats.Litematic.Codec import CellTemplate
from PhysicalDesign.Geometry.Placement import BuildPlacedGate
from PhysicalDesign.Geometry.Rotation import TransformBlockState, TransformLocalPosition
from PhysicalDesign.Placement.Engine.Construction.CommitRouting import (
    RouteCommittedClusterTemplates,
)
from PhysicalDesign.Policy import LocalFirstPhysicalDesignPolicy
from PhysicalDesign.Redstone.Rules import Geometry
from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology


def _BuildLocalRoutingContext(Placed, Technology, WorkChecks):
    return SimpleNamespace(
        PackedMode=True,
        Placed=Placed,
        PlacedGates=Placed.PlacedGates,
        WorkCheck=lambda Diagnostics: WorkChecks.append(Diagnostics),
        Technology=Technology,
        GapPlan=SimpleNamespace(ToDictionary=lambda: {}),
        ClusterRefinementProfile=None,
        JointPlacementDiagnostics={},
        PackedAccessRepairByCluster={},
        Clusters=(("Only",),),
        PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
        EnableClusterLocalRouteReuse=False,
        PlacementScoringOnly=True,
        RelocationSignals=frozenset(),
        RelocationPrioritySignals=frozenset(),
        RequiredRelocationSignals=frozenset(),
        RelocationVariant=0,
        AssignmentCut=None,
        PhysicallyRelocatedClusters=frozenset(),
        MirroredRelocationClusters=frozenset(),
        CoordinatedCandidateDiversificationSignals=frozenset(),
        EnableInternalPinBankGeometryRepair=False,
        InternalPinBankGeometrySignals=frozenset(),
    )


def test_committed_local_graph_uses_public_template_states_with_global_semantics(
    monkeypatch,
):
    """The real local commit consumes the public state-aware Physical graph."""
    Template = CellTemplate(
        Size=(2, 1, 3),
        Blocks={
            (0, 0, 0): {"Name": "minecraft:air"},
            (1, 0, 1): {
                "Name": "minecraft:redstone_wire",
                "Properties": {"east": "side", "north": "up"},
            },
            (1, 0, 2): {"Name": "minecraft:redstone_torch"},
        },
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Technology = replace(DefaultRedstoneRoutingTechnology, TrackPitch=5)
    GateValue = BuildPlacedGate(
        Gate("Only", GateKind.NAND, ["Y"], ["A", "B"]),
        10,
        4,
        20,
        90,
        True,
    )
    FrozenPosition = (99, 4, 99)
    Placed = SimpleNamespace(
        PlacedGates=[GateValue],
        FrozenNetWires={"PreviouslyFrozen": (FrozenPosition,)},
    )
    WorkChecks = []
    Context = _BuildLocalRoutingContext(Placed, Technology, WorkChecks)

    GlobalGraph = BuildRoutingResources(Placed, Technology=Technology).ResourceGraph
    RouteCommittedClusterTemplates(Context)

    LocalWire = TransformLocalPosition((1, 0, 1), (2, 3), 90, True)
    WirePosition = (10 + LocalWire[0], 4 + LocalWire[1], 20 + LocalWire[2])
    ExpectedWireState = TransformBlockState(Template.Blocks[(1, 0, 1)], 90, True)
    LocalAir = TransformLocalPosition((0, 0, 0), (2, 3), 90, True)
    AirPosition = (10 + LocalAir[0], 4 + LocalAir[1], 20 + LocalAir[2])

    assert Context.LocalResourceGraph.Technology is Technology
    assert any(Check["Phase"] == "routing-resources-start" for Check in WorkChecks)
    assert Context.LocalResourceGraph.BlockStates[WirePosition] == ExpectedWireState
    assert GlobalGraph.BlockStates[WirePosition] == ExpectedWireState
    assert AirPosition in Context.LocalResourceGraph.BlockStates
    assert AirPosition not in Context.LocalResourceGraph.ActualBlocks
    assert AirPosition not in GlobalGraph.ActualBlocks
    assert WirePosition in Context.LocalResourceGraph.ActualBlocks
    assert WirePosition in Context.LocalResourceGraph.ElectricalBlocks
    assert FrozenPosition in Context.LocalResourceGraph.ElectricalBlocks
    assert FrozenPosition in GlobalGraph.ElectricalBlocks
    assert Context.LocalResourceGraph.StaticKeepOutBlocks == GlobalGraph.StaticKeepOutBlocks

"""Independent observable coverage for placed-template resource states."""

from types import SimpleNamespace

import pytest

from Compilation.Ir.Models import Gate, GateKind
from Formats.Litematic.Codec import CellTemplate
from PhysicalDesign.Geometry.Placement import BuildPlacedGate
from PhysicalDesign.Geometry.Rotation import TransformBlockState, TransformLocalPosition
from PhysicalDesign.Redstone.Rules import Geometry
from PhysicalDesign.Redstone.Rules.Geometry import BuildPlacedTemplateRoutingStateSnapshot, BuildRoutingResources
from PhysicalDesign.Redstone.Rules import (
    BuildPlacedTemplateRoutingStateSnapshot as FacadeSnapshot,
    PlacedTemplateRoutingStateSnapshot,
)


def _Placed(Gates):
    return SimpleNamespace(PlacedGates=list(Gates), FrozenNetWires={})


def test_real_template_rotation_and_mirror_match_public_transform_oracle():
    GateValue = BuildPlacedGate(
        Gate("Nand", GateKind.NAND, ["Y"], ["A", "B"]),
        20, 3, 30, 90, True,
    )
    Template = Geometry.LoadRoutingTemplates()["NAND"]
    LocalPosition, State = next(iter(Template.Blocks.items()))
    Rotated = TransformLocalPosition(
        LocalPosition, (Template.Size[0], Template.Size[2]), 90, True,
    )
    ExpectedPosition = (20 + Rotated[0], 3 + Rotated[1], 30 + Rotated[2])
    ExpectedState = TransformBlockState(State, 90, True)

    Snapshot = BuildPlacedTemplateRoutingStateSnapshot(_Placed((GateValue,)))

    assert Snapshot.BlockStates[ExpectedPosition] == ExpectedState
    assert ExpectedPosition in Snapshot.ActualBlocks


def test_explicit_air_is_state_only_and_template_order_is_deterministic(monkeypatch):
    Template = CellTemplate(
        Size=(2, 1, 1),
        Blocks={
            (1, 0, 0): {"Name": "minecraft:redstone_wire", "Properties": {"east": "side", "west": "none"}},
            (0, 0, 0): {"Name": "minecraft:air"},
        },
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    GateValue = BuildPlacedGate(
        Gate("Nand", GateKind.NAND, ["Y"], ["A", "B"]), 0, 0, 0, 0, False,
    )

    Snapshot = BuildPlacedTemplateRoutingStateSnapshot(_Placed((GateValue,)))
    Resources = BuildRoutingResources(_Placed((GateValue,)))

    assert Snapshot.BlockStates[(0, 0, 0)]["Name"] == "minecraft:air"
    assert (0, 0, 0) not in Snapshot.ActualBlocks
    assert (0, 0, 0) not in Resources.ResourceGraph.ActualBlocks
    assert (0, 0, 0) not in Resources.ResourceGraph.ElectricalBlocks
    assert Snapshot.BlockStates[(1, 0, 0)]["Properties"]["east"] == "side"


def test_cached_placed_geometry_shape_needs_only_template_transform_fields(monkeypatch):
    Template = CellTemplate(Size=(1, 1, 1), Blocks={(0, 0, 0): {"Name": "minecraft:stone"}})
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Cached = type("CachedPlacedGate", (), {
        "Name": "Cached", "Kind": "NAND", "X": 4, "Y": 2, "Z": 6,
        "Rotation": 0, "MirrorX": False,
    })()

    Snapshot = BuildPlacedTemplateRoutingStateSnapshot(_Placed((Cached,)))

    assert Snapshot.ActualBlocks == frozenset({(4, 2, 6)})


def test_duplicate_gate_identity_rejects_before_template_publication(monkeypatch):
    Template = CellTemplate(Size=(1, 1, 1), Blocks={(0, 0, 0): {"Name": "minecraft:stone"}})
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: {"NAND": Template})
    First = type("Placed", (), {"Name": "Repeated", "Kind": "NAND", "X": 0, "Y": 0, "Z": 0, "Rotation": 0, "MirrorX": False})()
    Second = type("Placed", (), {"Name": "Repeated", "Kind": "NAND", "X": 4, "Y": 0, "Z": 0, "Rotation": 0, "MirrorX": False})()

    try:
        BuildPlacedTemplateRoutingStateSnapshot(_Placed((First, Second)))
    except ValueError as Error:
        assert "repeats a gate identity" in str(Error)
    else:
        raise AssertionError("duplicate gate identity must reject before snapshot")


def test_public_facade_and_nested_state_are_immutable(monkeypatch):
    Template = CellTemplate(Size=(1, 1, 1), Blocks={(0, 0, 0): {"Name": "minecraft:redstone_wire", "Properties": {"east": "side"}}})
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    GateValue = BuildPlacedGate(Gate("Nand", GateKind.NAND, ["Y"], ["A", "B"]), 0, 0, 0, 0, False)
    Snapshot = FacadeSnapshot(_Placed((GateValue,)))

    assert isinstance(Snapshot, PlacedTemplateRoutingStateSnapshot)
    try:
        Snapshot.BlockStates[(0, 0, 0)]["Properties"]["east"] = "none"
    except TypeError:
        pass
    else:
        raise AssertionError("nested transformed state must be immutable")


def _TemplateGate(Name, Kind, X):
    return type("CapturedPlacedGate", (), {
        "Name": Name, "Kind": Kind, "X": X, "Y": 0, "Z": 0,
        "Rotation": 0, "MirrorX": False,
    })()


def test_two_nand_size_mutation_during_workcheck_cannot_publish_resources(monkeypatch):
    Template = CellTemplate(
        Size=(1, 1, 1), Blocks={(0, 0, 0): {"Name": "minecraft:stone"}},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("First", "NAND", 0), _TemplateGate("Second", "NAND", 3)))

    def MutateSize(Diagnostics):
        if Diagnostics.get("Phase") == "placed-template-state-gate" and Diagnostics["CompletedGates"] == 1:
            Template.Size = (2, 1, 1)

    with pytest.raises(ValueError, match="inputs changed"):
        BuildRoutingResources(Placed, WorkCheck=MutateSize)


def test_template_mapping_replacement_during_workcheck_cannot_publish_resources(monkeypatch):
    Template = CellTemplate(
        Size=(1, 1, 1), Blocks={(0, 0, 0): {"Name": "minecraft:stone"}},
    )
    Holder = {"Templates": {"NAND": Template}}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Holder["Templates"])
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    def ReplaceMapping(Diagnostics):
        if Diagnostics.get("Phase") == "placed-template-state-gate":
            Holder["Templates"] = dict(Holder["Templates"])

    with pytest.raises(ValueError, match="inputs changed"):
        BuildRoutingResources(Placed, WorkCheck=ReplaceMapping)


def test_early_nand_mutation_during_later_nor_work_rejects_snapshot(monkeypatch):
    Nand = CellTemplate(
        Size=(1, 1, 1), Blocks={(0, 0, 0): {"Name": "minecraft:stone"}},
    )
    Nor = CellTemplate(
        Size=(1, 1, 1), Blocks={(0, 0, 0): {"Name": "minecraft:stone"}},
    )
    Templates = {"NAND": Nand, "NOR": Nor}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("A-Nand", "NAND", 0), _TemplateGate("B-Nor", "NOR", 3)))

    def MutateEarlyTemplate(Diagnostics):
        if Diagnostics.get("Phase") == "placed-template-state-gate" and Diagnostics["GateName"] == "B-Nor":
            Nand.Blocks[(0, 0, 0)]["Name"] = "minecraft:air"

    with pytest.raises(ValueError, match="inputs changed"):
        BuildPlacedTemplateRoutingStateSnapshot(Placed, WorkCheck=MutateEarlyTemplate)


def test_replaced_gate_collection_during_workcheck_cannot_publish_resources(monkeypatch):
    Template = CellTemplate(
        Size=(1, 1, 1), Blocks={(0, 0, 0): {"Name": "minecraft:stone"}},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    def ReplaceCollection(Diagnostics):
        if Diagnostics.get("Phase") == "placed-template-state-gate":
            Placed.PlacedGates = list(Placed.PlacedGates)

    with pytest.raises(ValueError, match="inputs changed"):
        BuildRoutingResources(Placed, WorkCheck=ReplaceCollection)


def test_valid_template_state_mutation_cannot_publish_snapshot(monkeypatch):
    Template = CellTemplate(
        Size=(1, 1, 1),
        Blocks={(0, 0, 0): {"Name": "minecraft:stone", "Properties": {"powered": "true"}}},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    def ChangePropertyValue(Diagnostics):
        if Diagnostics.get("Phase") == "placed-template-state-gate":
            Template.Blocks[(0, 0, 0)]["Properties"]["powered"] = "false"

    with pytest.raises(ValueError, match="inputs changed"):
        BuildPlacedTemplateRoutingStateSnapshot(Placed, WorkCheck=ChangePropertyValue)


@pytest.mark.parametrize("InvalidProperties", (
    float("nan"), float("inf"), float("-inf"), {0: "not-a-string-key"},
    iter(("one-shot",)),
))
def test_snapshot_and_resource_publication_reject_invalid_block_state_values(monkeypatch, InvalidProperties):
    Template = CellTemplate(
        Size=(1, 1, 1),
        Blocks={(0, 0, 0): {"Name": "minecraft:stone", "Properties": InvalidProperties}},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    with pytest.raises(TypeError):
        BuildPlacedTemplateRoutingStateSnapshot(Placed)
    with pytest.raises(TypeError):
        BuildRoutingResources(Placed)


@pytest.mark.parametrize("State", (
    {
        "Name": "minecraft:stone", "Properties": {"axis": "x"},
        "Extra": {"x": 1},
    },
    {"Name": "minecraft:stone", "Properties": {}},
))
def test_snapshot_and_resource_reject_transform_lossy_top_level_state(monkeypatch, State):
    Template = CellTemplate(
        Size=(1, 1, 1),
        Blocks={(0, 0, 0): State},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    with pytest.raises(ValueError, match="cannot preserve"):
        BuildPlacedTemplateRoutingStateSnapshot(Placed)
    with pytest.raises(ValueError, match="cannot preserve"):
        BuildRoutingResources(Placed)


@pytest.mark.parametrize("Size", (
    (0, 1, 1), (1, 0, 1), (1, 1, 0),
    (-1, 1, 1), (1, -1, 1), (1, 1, -1),
))
def test_snapshot_and_resource_reject_nonpositive_template_dimensions(monkeypatch, Size):
    Template = CellTemplate(
        Size=Size, Blocks={(0, 0, 0): {"Name": "minecraft:stone"}},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    with pytest.raises(ValueError, match="positive"):
        BuildPlacedTemplateRoutingStateSnapshot(Placed)
    with pytest.raises(ValueError, match="positive"):
        BuildRoutingResources(Placed)


@pytest.mark.parametrize("LocalPosition", (
    (-1, 0, 0), (2, 0, 0),
    (0, -1, 0), (0, 2, 0),
    (0, 0, -1), (0, 0, 2),
))
def test_snapshot_and_resource_reject_local_positions_outside_each_template_axis(monkeypatch, LocalPosition):
    Template = CellTemplate(
        Size=(2, 2, 2),
        Blocks={LocalPosition: {"Name": "minecraft:stone"}},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    with pytest.raises(ValueError, match="outside template size"):
        BuildPlacedTemplateRoutingStateSnapshot(Placed)
    with pytest.raises(ValueError, match="outside template size"):
        BuildRoutingResources(Placed)


def test_valid_boundary_state_is_losslessly_transformed_and_published(monkeypatch):
    State = {
        "Name": "minecraft:redstone_wire",
        "Properties": {"east": "side", "west": "none", "north": "up"},
    }
    Template = CellTemplate(Size=(2, 3, 4), Blocks={(1, 2, 3): State})
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    GateValue = _TemplateGate("Boundary", "NAND", 10)
    GateValue.Rotation = 90
    GateValue.MirrorX = True
    Placed = _Placed((GateValue,))
    ExpectedPosition = TransformLocalPosition((1, 2, 3), (2, 4), 90, True)
    ExpectedPosition = (10 + ExpectedPosition[0], 2, ExpectedPosition[2])
    ExpectedState = TransformBlockState(State, 90, True)

    Snapshot = BuildPlacedTemplateRoutingStateSnapshot(Placed)
    Resources = BuildRoutingResources(Placed)

    assert Snapshot.BlockStates[ExpectedPosition] == ExpectedState
    assert Resources.ResourceGraph.BlockStates[ExpectedPosition] == ExpectedState
    assert ExpectedPosition in Snapshot.ActualBlocks
    assert ExpectedPosition in Resources.ResourceGraph.ActualBlocks


@pytest.mark.parametrize("Properties", (
    {"value": True}, {"value": 1}, {"value": 1.0}, {"value": None},
    {"value": ["x"]}, {"value": {"x": "y"}}, {1: "value"},
))
def test_snapshot_and_resource_reject_nonstring_template_properties(monkeypatch, Properties):
    Template = CellTemplate(
        Size=(1, 1, 1),
        Blocks={(0, 0, 0): {"Name": "minecraft:stone", "Properties": Properties}},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    with pytest.raises(TypeError, match="exact strings"):
        BuildPlacedTemplateRoutingStateSnapshot(Placed)
    with pytest.raises(TypeError, match="exact strings"):
        BuildRoutingResources(Placed)


@pytest.mark.parametrize("RotationValue", ("01", "-1", "16", "1.5", "north", ""))
def test_snapshot_and_resource_reject_noncanonical_template_rotation_property(monkeypatch, RotationValue):
    Template = CellTemplate(
        Size=(1, 1, 1),
        Blocks={(0, 0, 0): {
            "Name": "minecraft:oak_sign", "Properties": {"rotation": RotationValue},
        }},
    )
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    Placed = _Placed((_TemplateGate("Only", "NAND", 0),))

    with pytest.raises(ValueError, match="canonical 0 through 15"):
        BuildPlacedTemplateRoutingStateSnapshot(Placed)
    with pytest.raises(ValueError, match="canonical 0 through 15"):
        BuildRoutingResources(Placed)


@pytest.mark.parametrize("RotationValue, GateRotation, MirrorX", (
    ("0", 0, False), ("15", 90, True), ("11", 270, False),
))
def test_canonical_template_rotation_property_matches_public_transform(monkeypatch, RotationValue, GateRotation, MirrorX):
    State = {
        "Name": "minecraft:oak_sign", "Properties": {"rotation": RotationValue},
    }
    Template = CellTemplate(Size=(1, 1, 1), Blocks={(0, 0, 0): State})
    Templates = {"NAND": Template}
    monkeypatch.setattr(Geometry, "LoadRoutingTemplates", lambda: Templates)
    GateValue = _TemplateGate("Only", "NAND", 0)
    GateValue.Rotation = GateRotation
    GateValue.MirrorX = MirrorX
    ExpectedState = TransformBlockState(State, GateRotation, MirrorX)

    Snapshot = BuildPlacedTemplateRoutingStateSnapshot(_Placed((GateValue,)))
    Resources = BuildRoutingResources(_Placed((GateValue,)))

    assert Snapshot.BlockStates[(0, 0, 0)] == ExpectedState
    assert Resources.ResourceGraph.BlockStates[(0, 0, 0)] == ExpectedState

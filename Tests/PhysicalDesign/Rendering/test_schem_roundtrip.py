"""Real litematic writer/reader round-trip coverage."""

from pathlib import Path

from Validation.Fabric.SchemImport import ReadLitematicIoLabels
from PhysicalDesign.Rendering.SchemWriter import LoadTemplate, WriteObservedLitematic


def test_observed_litematic_round_trip_preserves_states_and_labels(
    tmp_path: Path,
) -> None:
    Blocks = {
        (-2, 1, -1): {
            "Name": "minecraft:lever",
            "Properties": {
                "face": "wall",
                "facing": "west",
                "powered": "true",
            },
        },
        (-1, 1, -1): {
            "Name": "minecraft:redstone_wire",
            "Properties": {"east": "side", "power": "15"},
        },
        (0, 1, -1): {
            "Name": "minecraft:repeater",
            "Properties": {
                "delay": "2",
                "facing": "east",
                "locked": "false",
                "powered": "true",
            },
        },
        (1, 1, -1): {
            "Name": "minecraft:redstone_wall_torch",
            "Properties": {"facing": "west", "lit": "false"},
        },
        (2, 1, -1): {
            "Name": "minecraft:redstone_lamp",
            "Properties": {"lit": "true"},
        },
        (-2, 1, 0): {
            "Name": "minecraft:oak_sign",
            "Properties": {"rotation": "0", "waterlogged": "false"},
        },
        (2, 1, 0): {
            "Name": "minecraft:oak_sign",
            "Properties": {"rotation": "0", "waterlogged": "false"},
        },
    }
    OutputPath = tmp_path / "Observed.litematic"

    WriteObservedLitematic(
        Blocks,
        OutputPath,
        Bounds=((-2, 0, -1), (2, 1, 1)),
        Signs=[((-2, 1, 0), "IN a"), ((2, 1, 0), "OUT y")],
    )
    Template = LoadTemplate(OutputPath)

    assert Template.Size == (5, 2, 3)
    for Position, State in Blocks.items():
        Normalized = (Position[0] + 2, Position[1], Position[2] + 1)
        assert Template.Blocks[Normalized] == State
    assert ReadLitematicIoLabels(OutputPath) == [
        ((0, 1, 1), "IN", "a"),
        ((4, 1, 1), "OUT", "y"),
    ]

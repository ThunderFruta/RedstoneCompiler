import gzip
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from Compiler.FabricServer import BuildFabricFixtureFromSchem
from Compiler.FabricServer.SchemImport import ReadLitematicIoLabels
from PhysicalDesign.Rendering import SchemWriter
from PhysicalDesign.Rendering.SchemWriter import CellTemplate, EncodePayload, EncodeString, NbtValue


def _WriteSpongeSchem(PathValue: Path, *, BlockData: bytes, BlockEntities: list[dict[str, NbtValue]] | None = None) -> None:
    Root = {
        "Version": NbtValue(3, 2),
        "DataVersion": NbtValue(3, 4903),
        "Width": NbtValue(2, 2),
        "Height": NbtValue(2, 1),
        "Length": NbtValue(2, 2),
        "Palette": NbtValue(10, {
            "minecraft:air": NbtValue(3, 0),
            "minecraft:stone": NbtValue(3, 1),
            "minecraft:lever[face=wall,facing=north,powered=false]": NbtValue(3, 2),
        }),
        "BlockData": NbtValue(7, BlockData),
        "BlockEntities": NbtValue(9, (10, BlockEntities or [])),
        "Entities": NbtValue(9, (10, [])),
    }
    PathValue.write_bytes(gzip.compress(bytes([10]) + EncodeString("") + EncodePayload(10, Root)))


def _WriteLitematicRoot(PathValue: Path, Root: dict[str, NbtValue]) -> None:
    PathValue.write_bytes(
        gzip.compress(bytes([10]) + EncodeString("") + EncodePayload(10, Root)),
    )


class SpongeSchemImportTests(unittest.TestCase):
    def testReadsV2PaletteAndXzyBlockOrder(self) -> None:
        with tempfile.TemporaryDirectory() as TemporaryDirectory:
            PathValue = Path(TemporaryDirectory) / "layout.schem"
            # x=0,z=0 air; x=1,z=0 stone; x=0,z=1 lever; x=1,z=1 air.
            _WriteSpongeSchem(PathValue, BlockData=bytes([0, 1, 2, 0]))
            Fixture = BuildFabricFixtureFromSchem(
                PathValue,
                Origin=(10, 70, -4),
                ResetBeforeLoad=True,
            )

        self.assertEqual(Fixture["Arena"]["Origin"], [10, 70, -4])
        self.assertTrue(Fixture["Arena"]["ResetBeforeLoad"])
        self.assertEqual(Fixture["Arena"]["Bounds"]["Maximum"], [1, 0, 1])
        self.assertEqual(Fixture["Blocks"], [
            {"Position": [1, 0, 0], "State": {"Name": "minecraft:stone"}},
            {"Position": [0, 0, 1], "State": {
                "Name": "minecraft:lever",
                "Properties": {"face": "wall", "facing": "north", "powered": "false"},
            }},
        ])

    def testRejectsUnsupportedBlockEntitiesRatherThanDroppingThem(self) -> None:
        with tempfile.TemporaryDirectory() as TemporaryDirectory:
            PathValue = Path(TemporaryDirectory) / "entity.schem"
            _WriteSpongeSchem(
                PathValue,
                BlockData=bytes([0, 0, 0, 0]),
                BlockEntities=[{"id": NbtValue(8, "minecraft:chest")}],
            )
            with self.assertRaisesRegex(ValueError, "BlockEntities"):
                BuildFabricFixtureFromSchem(PathValue)

    def testReadsCompilerLitematicForLiveLoading(self) -> None:
        PathValue = SchemWriter.LitematicTemplates["Input"]
        Template = SchemWriter.LoadTemplate(PathValue)

        Fixture = BuildFabricFixtureFromSchem(
            PathValue,
            Origin=(12, 70, -8),
            ResetBeforeLoad=True,
        )

        self.assertEqual(Fixture["Arena"]["Origin"], [12, 70, -8])
        self.assertEqual(
            Fixture["Arena"]["Bounds"]["Maximum"],
            [Value - 1 for Value in Template.Size],
        )
        self.assertEqual(len(Fixture["Blocks"]), len(Template.Blocks))

    def testCompilerLitematicPortsAndDynamicStatesAreRecoveredForTesting(self) -> None:
        Template = CellTemplate(
            Size=(4, 2, 4),
            Blocks={
                (0, 1, 0): {
                    "Name": "minecraft:lever",
                    "Properties": {"powered": "true", "face": "wall", "facing": "west"},
                },
                (1, 1, 0): {
                    "Name": "minecraft:redstone_wire",
                    "Properties": {"power": "15", "east": "side"},
                },
                (2, 1, 0): {
                    "Name": "minecraft:redstone_wall_torch",
                    "Properties": {"lit": "true", "facing": "south"},
                },
                (3, 1, 0): {
                    "Name": "minecraft:redstone_lamp",
                    "Properties": {"lit": "true"},
                },
            },
        )
        with tempfile.TemporaryDirectory() as TemporaryDirectory:
            PathValue = Path(TemporaryDirectory) / "Top.litematic"
            with patch(
                "Compiler.FabricServer.SchemImport.LoadTemplate",
                return_value=Template,
            ), patch(
                "Compiler.FabricServer.SchemImport.ReadLitematicIoLabels",
                return_value=[
                    ((0, 1, 1), "IN", "a"),
                    ((3, 1, 1), "OUT", "y"),
                ],
            ):
                Fixture = BuildFabricFixtureFromSchem(PathValue)

        States = {
            tuple(Block["Position"]): Block["State"]
            for Block in Fixture["Blocks"]
        }
        self.assertEqual(
            Fixture["Inputs"],
            [{"Name": "a", "LeverPosition": [0, 1, 0]}],
        )
        self.assertEqual(
            Fixture["Outputs"],
            [{"Name": "y", "LampPosition": [3, 1, 0]}],
        )
        self.assertEqual(States[(0, 1, 0)]["Properties"]["powered"], "false")
        self.assertEqual(States[(1, 1, 0)]["Properties"]["power"], "0")
        self.assertEqual(States[(2, 1, 0)]["Properties"]["lit"], "false")
        self.assertEqual(States[(3, 1, 0)]["Properties"]["lit"], "false")
        self.assertEqual(Fixture["Signs"], [
            {
                "Position": [0, 1, 1],
                "FrontText": ["IN a", "", "", ""],
                "BackText": ["IN a", "", "", ""],
            },
            {
                "Position": [3, 1, 1],
                "FrontText": ["OUT y", "", "", ""],
                "BackText": ["OUT y", "", "", ""],
            },
        ])

    def testLitematicPortReaderRejectsMultipleRegionsRatherThanTestingOnlyOne(self) -> None:
        with tempfile.TemporaryDirectory() as TemporaryDirectory:
            PathValue = Path(TemporaryDirectory) / "MultiRegion.litematic"
            _WriteLitematicRoot(PathValue, {
                "Regions": NbtValue(10, {
                    "First": NbtValue(10, {}),
                    "Second": NbtValue(10, {}),
                }),
            })
            with self.assertRaisesRegex(ValueError, "multi-region"):
                ReadLitematicIoLabels(PathValue)


if __name__ == "__main__":
    unittest.main()

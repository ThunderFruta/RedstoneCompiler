import gzip
from pathlib import Path
import tempfile
import unittest

from Compiler.FabricServer import BuildFabricFixtureFromSchem
from SchemEncoder import SchemWriter
from SchemEncoder.SchemWriter import EncodePayload, EncodeString, NbtValue


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


if __name__ == "__main__":
    unittest.main()

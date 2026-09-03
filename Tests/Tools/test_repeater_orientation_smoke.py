import tempfile
from pathlib import Path
import unittest

from Tools.Fabric.BuildRepeaterOrientationSmoke import BuildSmokeLitematic
from SchemEncoder.SchemWriter import LoadTemplate


class RepeaterOrientationSmokeTests(unittest.TestCase):
    def testFourDirectionSmokeArtifactPassesReadback(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "smoke.litematic"
            Report = BuildSmokeLitematic(OutputPath)
            Blocks = LoadTemplate(OutputPath).Blocks

            self.assertTrue(Report["AutomatedSubsetValidationPassed"])
            self.assertEqual(Report["LaneCount"], 4)
            self.assertEqual(
                {
                    State["Properties"]["facing"]
                    for State in Blocks.values()
                    if State["Name"] == "minecraft:repeater"
                },
                {"north", "south", "east", "west"},
            )
            self.assertTrue(
                Report["RepeaterOrientation"]["ReadbackPassed"]
            )


if __name__ == "__main__":
    unittest.main()

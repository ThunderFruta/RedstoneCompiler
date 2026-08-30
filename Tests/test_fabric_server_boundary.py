import unittest

from Compiler.FabricServer import FabricServerValidationResult
from SchemEncoder.Writer262 import NeutralDynamicState


class FabricServerBoundaryTests(unittest.TestCase):
    def testNotRunResultCannotMasqueradeAsServerAcceptance(self) -> None:
        Result = FabricServerValidationResult.NotRun()

        self.assertEqual(Result.Status, "not-run")
        self.assertIsNone(Result.Backend)
        self.assertEqual(Result.RuntimeSeconds, 0.0)
        self.assertEqual(
            Result.Diagnostics["Reason"],
            "fabric-server-integration-not-configured",
        )

    def testDynamicBlocksAreEmittedWithoutPredictedPower(self) -> None:
        Cases = (
            (
                {
                    "Name": "minecraft:repeater",
                    "Properties": {"powered": "true", "facing": "north"},
                },
                "powered",
                "false",
            ),
            (
                {
                    "Name": "minecraft:redstone_wall_torch",
                    "Properties": {"lit": "true", "facing": "south"},
                },
                "lit",
                "false",
            ),
            (
                {
                    "Name": "minecraft:redstone_wire",
                    "Properties": {"power": "15", "north": "side"},
                },
                "power",
                "0",
            ),
        )

        for State, Property, Expected in Cases:
            with self.subTest(State=State["Name"]):
                Neutral = NeutralDynamicState(State)
                self.assertEqual(Neutral["Properties"][Property], Expected)


if __name__ == "__main__":
    unittest.main()

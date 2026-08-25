import unittest

from Compiler.Simulation.Redstone import (
    _BuildMinecraftWireAdjacency,
    ShouldSimulateRenderedMinecraftTruthTable,
    SimulateMinecraftRedstoneBlockMap,
)


class MinecraftJavaSubsetTests(unittest.TestCase):
    def testRenderedSimulationUsesParityProvenRowBound(self) -> None:
        Small = type("SmallModule", (), {"Inputs": ("A", "B", "C")})()
        Large = type(
            "LargeModule",
            (),
            {"Inputs": ("A", "B", "C", "D")},
        )()

        self.assertTrue(ShouldSimulateRenderedMinecraftTruthTable(Small))
        self.assertFalse(ShouldSimulateRenderedMinecraftTruthTable(Large))

    def testTorchPowersOpaqueBlockAndDustAboveIt(self) -> None:
        Blocks = {
            (0, 0, 0): {
                "Name": "minecraft:redstone_torch",
                "Properties": {"lit": "true"},
            },
            (0, 1, 0): {"Name": "minecraft:red_concrete"},
            (0, 2, 0): {
                "Name": "minecraft:redstone_wire",
                "Properties": {
                    "north": "none",
                    "south": "none",
                    "east": "none",
                    "west": "none",
                },
            },
        }

        Result = SimulateMinecraftRedstoneBlockMap(Blocks, {})

        self.assertTrue(Result.Stable)
        self.assertEqual(Result.DustPower[(0, 2, 0)], 15)

    def testRepeaterOnlyPowersItsFront(self) -> None:
        Blocks = {
            (0, 0, 0): {
                "Name": "minecraft:repeater",
                "Properties": {
                    "facing": "east",
                    "powered": "false",
                    "delay": "1",
                },
            },
            (1, 0, 0): {"Name": "minecraft:redstone_lamp"},
            (-2, 0, 0): {"Name": "minecraft:lever"},
        }

        Result = SimulateMinecraftRedstoneBlockMap(
            Blocks,
            {(-2, 0, 0): True},
        )

        self.assertTrue(Result.Stable)
        self.assertTrue(Result.LampLit[(1, 0, 0)])

    def testConsecutiveRepeatersPropagateThroughDirectRearInput(self) -> None:
        Blocks = {
            (0, 0, 0): {
                "Name": "minecraft:repeater",
                "Properties": {
                    "facing": "east",
                    "powered": "false",
                    "delay": "1",
                },
            },
            (1, 0, 0): {
                "Name": "minecraft:repeater",
                "Properties": {
                    "facing": "east",
                    "powered": "false",
                    "delay": "1",
                },
            },
            (2, 0, 0): {"Name": "minecraft:redstone_lamp"},
            (-2, 0, 0): {"Name": "minecraft:lever"},
        }

        Result = SimulateMinecraftRedstoneBlockMap(
            Blocks,
            {(-2, 0, 0): True},
        )

        self.assertTrue(Result.Stable)
        self.assertTrue(Result.RepeaterPowered[(0, 0, 0)])
        self.assertTrue(Result.RepeaterPowered[(1, 0, 0)])
        self.assertTrue(Result.LampLit[(2, 0, 0)])

    def testRepeaterDelayCannotBeMisreportedAsStable(self) -> None:
        Blocks = {
            (-1, 0, 0): {"Name": "minecraft:lever"},
            (0, 0, 0): {
                "Name": "minecraft:repeater",
                "Properties": {
                    "facing": "east",
                    "powered": "true",
                    "delay": "4",
                },
            },
            (1, 0, 0): {"Name": "minecraft:redstone_lamp"},
        }

        Result = SimulateMinecraftRedstoneBlockMap(Blocks, {})

        self.assertTrue(Result.Stable)
        self.assertFalse(Result.RepeaterPowered[(0, 0, 0)])
        self.assertFalse(Result.LampLit[(1, 0, 0)])

    def testLeverBesideRepeaterDoesNotLockIt(self) -> None:
        Blocks = {
            (0, 0, 0): {
                "Name": "minecraft:repeater",
                "Properties": {
                    "facing": "east",
                    "powered": "true",
                    "delay": "1",
                },
            },
            (0, 0, 1): {"Name": "minecraft:lever"},
        }

        Result = SimulateMinecraftRedstoneBlockMap(
            Blocks,
            {(0, 0, 1): True},
        )

        self.assertTrue(Result.Stable)
        self.assertFalse(Result.RepeaterPowered[(0, 0, 0)])

    def testDustStairConnectionConductsInBothDirections(self) -> None:
        Lower = (0, 0, 0)
        Upper = (1, 1, 0)
        Blocks = {
            (-1, 0, 0): {"Name": "minecraft:lever"},
            Lower: {
                "Name": "minecraft:redstone_wire",
                "Properties": {
                    "north": "none",
                    "south": "none",
                    "east": "up",
                    "west": "side",
                },
            },
            Upper: {
                "Name": "minecraft:redstone_wire",
                "Properties": {
                    "north": "none",
                    "south": "none",
                    "east": "none",
                    "west": "side",
                },
            },
        }

        Adjacency = _BuildMinecraftWireAdjacency(Blocks)
        Result = SimulateMinecraftRedstoneBlockMap(
            Blocks,
            {(-1, 0, 0): True},
        )

        self.assertIn(Upper, Adjacency[Lower])
        self.assertIn(Lower, Adjacency[Upper])
        self.assertGreater(Result.DustPower[Upper], 0)


if __name__ == "__main__":
    unittest.main()

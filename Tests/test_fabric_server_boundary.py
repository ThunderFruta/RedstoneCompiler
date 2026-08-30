import unittest
from types import SimpleNamespace

from Compiler.FabricServer import (
    BuildExpectedVectors,
    BuildFabricFixture,
    BuildValidationVectors,
    FabricServerConfiguration,
    FabricServerSupervisor,
    FabricServerValidationResult,
)
from Compiler.Ir.Models import Gate, GateKind, ModuleIR
from SchemEncoder.SchemWriter import BuildLitematicBlockMap, NeutralDynamicState


class FabricServerBoundaryTests(unittest.TestCase):
    def testMissingServerIsAnInfrastructureFailure(self) -> None:
        Result = FabricServerSupervisor(
            FabricServerConfiguration(Root=None),
        ).Validate(
            Fixture=SimpleNamespace(Path=None, Sha256="", BlockCount=0),
            Vectors=[],
        )

        self.assertEqual(Result.Status, "infrastructure-failure")
        self.assertEqual(Result.Diagnostics["Reason"], "server-root-not-configured")

    def testVectorPolicyIsExhaustiveThenDeterministic(self) -> None:
        self.assertEqual(len(BuildValidationVectors(("a", "b"))), 4)
        First = BuildValidationVectors(tuple(f"a{Index}" for Index in range(17)))
        Second = BuildValidationVectors(tuple(f"a{Index}" for Index in range(17)))
        self.assertEqual(len(First), 2 + 34 + 4096)
        self.assertEqual(First, Second)

    def testFixtureUsesIOTemplateBlocksRatherThanSigns(self) -> None:
        Routed = SimpleNamespace(PlacedGates=[
            SimpleNamespace(Name="InputA", Kind="INPUT", Outputs=["a"], X=0, Y=0, Z=0, Rotation=0, MirrorX=False),
            SimpleNamespace(Name="OutputY", Kind="OUTPUT", Outputs=["y$Output"], X=4, Y=0, Z=0, Rotation=0, MirrorX=False),
        ])
        Rendered = SimpleNamespace(Blocks={
            (0, 0, 0): {"Name": "minecraft:lever", "Properties": {"powered": "false"}},
            (4, 0, 1): {"Name": "minecraft:redstone_lamp", "Properties": {"lit": "false"}},
        })
        Fixture = BuildFabricFixture(
            RoutedDesign=Routed,
            Rendered=Rendered,
            Module=ModuleIR(Name="Top"),
        )

        self.assertEqual(Fixture["Inputs"][0]["LeverPosition"], [0, 0, 0])
        self.assertEqual(Fixture["Outputs"][0]["LampPosition"], [4, 0, 1])

    def testExpectedVectorsUseLogicOnlyAsAnOracle(self) -> None:
        Module = ModuleIR(
            Name="Top",
            Inputs=["a"],
            Outputs=["y$Output"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["a"]),
                Gate("OutputY", GateKind.OUTPUT, ["y$Output"], ["a"]),
            ],
        )
        Vectors = BuildExpectedVectors(Module, ["a"], ["y$Output"])
        self.assertEqual(Vectors[0]["Expected"], {"y$Output": False})
        self.assertEqual(Vectors[1]["Expected"], {"y$Output": True})

    def testWallMountedInputLeverHasPhysicalBackingBlock(self) -> None:
        Routed = SimpleNamespace(
            PlacedGates=[SimpleNamespace(
                Name="InputA",
                Kind="INPUT",
                Outputs=["a"],
                OutputPin=(0, 0, 2),
                OutputDirection=(0, 0, 1),
                X=0,
                Y=0,
                Z=0,
                Rotation=0,
                MirrorX=False,
            )],
            NetWires={},
            Supports=[],
            Repeaters={},
            RepeaterInputFacings={},
        )
        Rendered = BuildLitematicBlockMap(Routed)

        self.assertEqual(
            Rendered.Blocks[(0, 0, -1)]["Name"],
            "minecraft:light_gray_concrete",
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

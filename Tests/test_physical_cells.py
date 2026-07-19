import unittest

from Compiler.Cells.Library import CellMacros
from Compiler.Ir.Models import Gate, GateKind
from Compiler.Placement.Geometry import BuildPlacedGate
from Compiler.Routing.Core import (
    AreConnected,
    BuildElectricalExclusions,
    BuildPhysicalGraphs,
    NeighborPositions,
    RustRoutingContext,
)
from SchemEncoder.Writer262 import BuildWireState, LoadTemplate
from Templates import LitematicTemplates


class PhysicalCellTests(unittest.TestCase):
    def testMacroSizesMatchTemplates(self) -> None:
        for Name, PathValue in LitematicTemplates.items():
            with self.subTest(Name=Name):
                Template = LoadTemplate(PathValue)
                Macro = CellMacros[Name.upper()]
                self.assertEqual(
                    (Macro.Width, Macro.Height, Macro.Depth),
                    Template.Size,
                )

    def testMirroringSwapsNandInputs(self) -> None:
        GateValue = Gate(
            Name="NandGate",
            Kind=GateKind.NAND,
            Outputs=["Y"],
            Inputs=["A", "B"],
        )
        Normal = BuildPlacedGate(GateValue, 0, 1, 0, 0, False)
        Mirrored = BuildPlacedGate(GateValue, 0, 1, 0, 0, True)
        self.assertEqual(Mirrored.InputPins, list(reversed(Normal.InputPins)))
        self.assertEqual(Mirrored.OutputPin, Normal.OutputPin)

    def testElectricalExclusionsMatchRedstoneConnectivity(self) -> None:
        Origin = (4, 3, 8)
        Exclusions = BuildElectricalExclusions({Origin})
        self.assertEqual(Exclusions, {Origin, *NeighborPositions(Origin)})
        self.assertTrue(
            all(
                Position == Origin or AreConnected(Origin, Position)
                for Position in Exclusions
            )
        )

    def testWallTorchDoesNotBlockDustStairConnection(self) -> None:
        Lower = (0, 0, 0)
        Upper = (1, 1, 0)
        UpperSupport = (1, 0, 0)
        WallTorch = (0, 1, 0)
        NetWires = {"Signal": {Lower, Upper}}
        Graph = BuildPhysicalGraphs(
            NetWires,
            ActualBlocks={UpperSupport, WallTorch},
            Supports={(0, -1, 0), UpperSupport},
            SolidBlocks={UpperSupport},
        )
        self.assertIn(Upper, Graph["Signal"][Lower])
        self.assertIn(Lower, Graph["Signal"][Upper])

        Blocks = {
            UpperSupport: {"Name": "minecraft:smooth_stone"},
            WallTorch: {"Name": "minecraft:redstone_wall_torch"},
        }
        LowerState = BuildWireState(Lower, NetWires["Signal"], Blocks, 15)
        UpperState = BuildWireState(Upper, NetWires["Signal"], Blocks, 15)
        self.assertEqual(LowerState["Properties"]["east"], "up")
        self.assertEqual(UpperState["Properties"]["west"], "side")

    @unittest.skipIf(
        RustRoutingContext is None
        or not hasattr(RustRoutingContext, "FindPathOnResourceGraph"),
        "Graph Rust routing extension is unavailable",
    )
    def testRustSearchCannotLeaveAuthoritativeGraph(self) -> None:
        Nodes = [
            *((X, 0, 0) for X in range(4)),
            *((3, 0, Z) for Z in range(1, 4)),
        ]
        Edges = [
            *((((X, 0, 0), (X + 1, 0, 0))) for X in range(3)),
            *((((3, 0, Z), (3, 0, Z + 1))) for Z in range(3)),
        ]
        Context = RustRoutingContext(
            (-2, 5, 0, 2, -2, 5),
            (0, 3, 0, 3),
            Nodes,
            Edges,
        )
        Path = Context.FindPathOnResourceGraph(
            [(0, 0, 0)],
            (3, 0, 3),
            0,
            [],
            [],
            [],
            10,
            8,
            2,
            20_000,
        )

        self.assertIsNotNone(Path)
        self.assertTrue(all(Position in Nodes for Position in Path))


if __name__ == "__main__":
    unittest.main()

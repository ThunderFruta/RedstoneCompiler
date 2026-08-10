import unittest
from itertools import product
from types import SimpleNamespace

from Compiler.Cells.Library import CellMacros
from Compiler.Ir.Models import Gate, GateKind
from Compiler.Placement.Geometry import BuildPlacedGate
from Compiler.Placement.Pcb import PcbGatesConflict
from Compiler.Routing.Actions import (
    AreConnected,
    BuildElectricalExclusions,
    BuildPhysicalGraphs,
    NeighborPositions,
)
from Compiler.Routing.Actions.Repeaters import PruneRedundantRepeaterReservations
from Compiler.Routing.Actions.Geometry import ValidatePlacedCellElectricalIsolation
from Compiler.Routing.Workers.DetailedRouting import RustRoutingContext
from Compiler.Simulation.Redstone import SimulateMinecraftRedstoneBlockMap
from SchemEncoder.Writer262 import (
    ApplyTemplateRepeaterPinDirection,
    BlockProvenance,
    BuildLitematicBlockMap,
    BuildWireState,
    LoadTemplate,
    MinecraftRepeaterFacingForReservation,
    TemplateRepeaterPinRoles,
    OrientCellState,
    PoweredCellState,
    _PlaceIoSigns,
)
from Templates import LitematicTemplates


class PhysicalCellTests(unittest.TestCase):
    def testNandTemplateExecutesItsPhysicalTruthTable(self) -> None:
        """Prove the real template, rather than painted gate state, is NAND."""
        Template = LoadTemplate(LitematicTemplates["Nand"])
        for Inputs in product((False, True), repeat=2):
            Blocks = {
                Position: ApplyTemplateRepeaterPinDirection(
                    "NAND", Position, State
                )
                for Position, State in Template.Blocks.items()
            }
            for Position, State in tuple(Blocks.items()):
                if State["Name"] in (
                    "minecraft:repeater",
                    "minecraft:redstone_wire",
                ):
                    Blocks[(Position[0], Position[1] - 1, Position[2])] = {
                        "Name": "minecraft:smooth_stone"
                    }
            Blocks[(1, 0, 4)] = {"Name": "minecraft:redstone_lamp"}
            LeverPower = {
                (0, 0, -2): Inputs[0],
                (2, 0, -2): Inputs[1],
            }
            Blocks.update({
                Position: {"Name": "minecraft:lever"}
                for Position in LeverPower
            })
            Result = SimulateMinecraftRedstoneBlockMap(Blocks, LeverPower)
            self.assertTrue(Result.Stable)
            self.assertEqual(
                Result.LampLit[(1, 0, 4)],
                not all(Inputs),
            )

    def testOutputTemplateDrivesItsLampFromItsDeclaredInput(self) -> None:
        Template = LoadTemplate(LitematicTemplates["Output"])
        for Value in (False, True):
            Blocks = {
                Position: ApplyTemplateRepeaterPinDirection(
                    "OUTPUT", Position, State
                )
                for Position, State in Template.Blocks.items()
            }
            Blocks[(0, -1, 0)] = {"Name": "minecraft:smooth_stone"}
            Blocks[(0, 0, -1)] = {"Name": "minecraft:redstone_wire"}
            Blocks[(0, -1, -1)] = {"Name": "minecraft:smooth_stone"}
            LeverPosition = (0, 0, -2)
            Blocks[LeverPosition] = {"Name": "minecraft:lever"}
            Result = SimulateMinecraftRedstoneBlockMap(
                Blocks,
                {LeverPosition: Value},
            )
            self.assertTrue(Result.Stable)
            self.assertEqual(Result.LampLit[(0, 0, 1)], Value)

    def testInputTemplateRetainsItsLampIndicator(self) -> None:
        State = PoweredCellState(
            {"Name": "minecraft:redstone_lamp", "Properties": {"lit": "false"}},
            SimpleNamespace(Kind="INPUT", Outputs=["A"], Inputs=[]),
            (0, 0, 1),
            {"A": True},
        )
        self.assertEqual(State["Name"], "minecraft:redstone_lamp")
        self.assertEqual(State["Properties"]["lit"], "true")

    def testPackedCellsRejectActualTemplateElectricalAdjacency(self) -> None:
        Input = BuildPlacedGate(
            Gate("InputA", GateKind.INPUT, ["A"], []),
            8,
            1,
            -1,
            0,
        )
        Nand = BuildPlacedGate(
            Gate("NandGate0", GateKind.NAND, ["Y"], ["A", "B"]),
            5,
            1,
            0,
            0,
        )
        self.assertTrue(PcbGatesConflict(Input, Nand))
        with self.assertRaisesRegex(ValueError, "violate electrical isolation"):
            ValidatePlacedCellElectricalIsolation(
                SimpleNamespace(PlacedGates=[Input, Nand])
            )

    def testIoSignPlacementEscapesCompletelyOccupiedLocalArea(self) -> None:
        Gate = SimpleNamespace(
            Name="InputA",
            Kind="INPUT",
            X=0,
            Y=1,
            Z=0,
            Rotation=0,
            Outputs=["A"],
            Inputs=[],
            OutputPin=(0, 1, 0),
            OutputDirection=(1, 0, 0),
            InputPins=[],
            InputDirections=[],
        )
        Blocks = {
            (X, 1, Z): {"Name": "minecraft:redstone_wire"}
            for X in range(-8, 9)
            for Z in range(-8, 9)
        }
        Provenance = {
            Position: BlockProvenance.RouteSignal for Position in Blocks
        }
        Signs = _PlaceIoSigns(
            SimpleNamespace(PlacedGates=[Gate]),
            Blocks,
            Provenance,
            {"Name": "minecraft:smooth_stone"},
            {},
            {},
        )
        self.assertEqual(len(Signs), 1)
        Position, Text = Signs[0]
        self.assertEqual(Text, "IN A")
        self.assertEqual(Blocks[Position]["Name"], "minecraft:oak_sign")
        self.assertEqual(Provenance[Position], BlockProvenance.Annotation)
        self.assertIn((Position[0], Position[1] - 1, Position[2]), Blocks)

    def testIoSignPlacementPrefersExistingPhysicalEnvelope(self) -> None:
        Gate = SimpleNamespace(
            Name="InputA",
            Kind="INPUT",
            X=0,
            Y=1,
            Z=0,
            Rotation=0,
            Outputs=["A"],
            Inputs=[],
            OutputPin=(1, 1, 1),
            OutputDirection=(1, 0, 0),
            InputPins=[],
            InputDirections=[],
        )
        Blocks = {
            (0, 0, 0): {"Name": "minecraft:smooth_stone"},
            (1, 0, 0): {"Name": "minecraft:smooth_stone"},
            (0, 0, 2): {"Name": "minecraft:smooth_stone"},
            (1, 0, 2): {"Name": "minecraft:smooth_stone"},
        }
        Provenance = {
            Position: BlockProvenance.RouteSupport for Position in Blocks
        }
        Signs = _PlaceIoSigns(
            SimpleNamespace(PlacedGates=[Gate]),
            Blocks,
            Provenance,
            {"Name": "minecraft:smooth_stone"},
            {},
            {"A": "minecraft:light_gray_concrete"},
        )

        self.assertEqual(Signs, [((1, 1, 0), "IN A")])
        self.assertEqual(max(Position[0] for Position in Blocks), 1)
        self.assertEqual(max(Position[2] for Position in Blocks), 2)

    def testIoSignPlacementUsesExistingVerticalDeckBeforeExpanding(self) -> None:
        Gate = SimpleNamespace(
            Name="InputA",
            Kind="INPUT",
            X=100,
            Y=1,
            Z=100,
            Rotation=0,
            Outputs=["A"],
            Inputs=[],
            OutputPin=(1, 1, 1),
            OutputDirection=(1, 0, 0),
            InputPins=[],
            InputDirections=[],
        )
        Blocks = {
            (X, 1, Z): {"Name": "minecraft:redstone_wire"}
            for X in range(-7, 10)
            for Z in range(-7, 10)
        }
        # The primary-direction slot is occupied at the pin layer but has a
        # valid support directly below the next existing routing deck.
        Blocks[(2, 1, 1)] = {"Name": "minecraft:smooth_stone"}
        Blocks[(0, 0, 0)] = {"Name": "minecraft:smooth_stone"}
        Blocks[(0, 3, 0)] = {"Name": "minecraft:smooth_stone"}
        Provenance = {
            Position: BlockProvenance.RouteSupport for Position in Blocks
        }
        OriginalBounds = (
            min(Position[0] for Position in Blocks),
            max(Position[0] for Position in Blocks),
            min(Position[1] for Position in Blocks),
            max(Position[1] for Position in Blocks),
            min(Position[2] for Position in Blocks),
            max(Position[2] for Position in Blocks),
        )

        Signs = _PlaceIoSigns(
            SimpleNamespace(PlacedGates=[Gate]),
            Blocks,
            Provenance,
            {"Name": "minecraft:smooth_stone"},
            {},
            {"A": "minecraft:light_gray_concrete"},
        )

        self.assertEqual(Signs, [((2, 2, 1), "IN A")])
        self.assertEqual(
            (
                min(Position[0] for Position in Blocks),
                max(Position[0] for Position in Blocks),
                min(Position[1] for Position in Blocks),
                max(Position[1] for Position in Blocks),
                min(Position[2] for Position in Blocks),
                max(Position[2] for Position in Blocks),
            ),
            OriginalBounds,
        )

    def testRepeaterPruningKeepsOnlyStrengthRequiredRefreshers(self) -> None:
        Nodes = [(X, 0, 0) for X in range(29)]
        Graph = {
            Node: [
                Neighbor
                for Neighbor in Nodes
                if abs(Neighbor[0] - Node[0]) == 1
            ]
            for Node in Nodes
        }
        Reservations = tuple(
            SimpleNamespace(Position=(X, 0, 0), Facing="west")
            for X in (10, 14, 20)
        )
        Retained = PruneRedundantRepeaterReservations(
            (0, 0, 0),
            ((28, 0, 0),),
            Graph,
            Reservations,
        )
        self.assertEqual([Value.Position for Value in Retained], [(14, 0, 0)])

    def testTemplateRepeatersAllBridgeDeclaredMacroPins(self) -> None:
        for Name, PathValue in LitematicTemplates.items():
            with self.subTest(Name=Name):
                Template = LoadTemplate(PathValue)
                Repeaters = {
                    Position
                    for Position, State in Template.Blocks.items()
                    if State["Name"] == "minecraft:repeater"
                }
                self.assertEqual(
                    Repeaters,
                    set(TemplateRepeaterPinRoles(Name)),
                )

    def testRenderedPinRepeatersFollowRotatedMacroDirection(self) -> None:
        ExpectedByRotation = {
            0: "south",
            90: "west",
            180: "north",
            270: "east",
        }
        Template = LoadTemplate(LitematicTemplates["Input"])
        State = Template.Blocks[(0, 0, 2)]
        for Rotation, ExpectedFacing in ExpectedByRotation.items():
            with self.subTest(Rotation=Rotation):
                Canonical = ApplyTemplateRepeaterPinDirection(
                    "INPUT",
                    (0, 0, 2),
                    State,
                )
                Rendered = OrientCellState(Canonical, Rotation)
                self.assertEqual(
                    Rendered["Properties"]["facing"],
                    ExpectedFacing,
                )

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

    def testRouteSupportsUsePerSignalConcreteColors(self) -> None:
        RoutedDesign = SimpleNamespace(
            Module=SimpleNamespace(Gates=()),
            PlacedGates=(),
            Wires=[(0, 1, 0), (2, 1, 0)],
            Supports=[(0, 0, 0), (2, 0, 0)],
            Repeaters={},
            NetWires={
                "A": [(0, 1, 0)],
                "B": [(2, 1, 0)],
            },
            SupportBlock="minecraft:light_gray_concrete",
        )
        Build = BuildLitematicBlockMap(RoutedDesign)
        self.assertEqual(
            Build.Blocks[(0, 0, 0)]["Name"],
            "minecraft:light_gray_concrete",
        )
        self.assertEqual(Build.Blocks[(2, 0, 0)]["Name"], "minecraft:yellow_concrete")

    def testRenderedRouteRepeaterUsesMinecraftOutputFacing(self) -> None:
        RoutedDesign = SimpleNamespace(
            Module=SimpleNamespace(Gates=()),
            PlacedGates=(),
            Wires=[(0, 1, 0), (1, 1, 0), (2, 1, 0)],
            Supports=[(0, 0, 0), (1, 0, 0), (2, 0, 0)],
            Repeaters={(1, 1, 0): "west"},
            NetWires={"Signal": [(0, 1, 0), (1, 1, 0), (2, 1, 0)]},
            SupportBlock="minecraft:light_gray_concrete",
        )
        Build = BuildLitematicBlockMap(RoutedDesign)
        self.assertEqual(
            MinecraftRepeaterFacingForReservation("west"),
            "east",
        )
        self.assertEqual(
            Build.Blocks[(1, 1, 0)]["Properties"]["facing"],
            "east",
        )

    def testRouteSupportsUseCustomTracePalette(self) -> None:
        RoutedDesign = SimpleNamespace(
            Module=SimpleNamespace(Gates=()),
            PlacedGates=(),
            Wires=[(0, 1, 0), (2, 1, 0)],
            Supports=[(0, 0, 0), (2, 0, 0)],
            Repeaters={},
            NetWires={
                "A": [(0, 1, 0)],
                "B": [(2, 1, 0)],
            },
            SupportBlock="minecraft:light_gray_concrete",
        )
        Build = BuildLitematicBlockMap(
            RoutedDesign,
            TraceSupportBlocks=("minecraft:andesite", "minecraft:blue_concrete"),
        )
        self.assertEqual(Build.Blocks[(0, 0, 0)]["Name"], "minecraft:andesite")
        self.assertEqual(
            Build.Blocks[(2, 0, 0)]["Name"],
            "minecraft:blue_concrete",
        )

    def testTracePaletteCyclesBeyondSevenSignals(self) -> None:
        RoutedDesign = SimpleNamespace(
            Module=SimpleNamespace(Gates=()),
            PlacedGates=(),
            Wires=[(Index * 2, 1, 0) for Index in range(9)],
            Supports=[(Index * 2, 0, 0) for Index in range(9)],
            Repeaters={},
            NetWires={f"Signal{Index}": [(Index * 2, 1, 0)] for Index in range(9)},
            SupportBlock="minecraft:light_gray_concrete",
        )
        Build = BuildLitematicBlockMap(RoutedDesign)
        self.assertEqual(
            Build.Blocks[(14, 0, 0)]["Name"],
            "minecraft:light_gray_concrete",
        )
        self.assertEqual(Build.Blocks[(16, 0, 0)]["Name"], "minecraft:yellow_concrete")

    def testSupportBlockCompositionUsesRouteSupportProvenance(self) -> None:
        RoutedDesign = SimpleNamespace(
            Module=SimpleNamespace(Gates=()),
            PlacedGates=(),
            Wires=[(0, 1, 0), (2, 1, 0)],
            Supports=[(0, 0, 0), (2, 0, 0)],
            Repeaters={},
            NetWires={
                "A": [(0, 1, 0)],
                "B": [(2, 1, 0)],
            },
            SupportBlock="minecraft:light_gray_concrete",
        )
        Build = BuildLitematicBlockMap(RoutedDesign)
        self.assertEqual(Build.Composition.SupportBlocks, 2)

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

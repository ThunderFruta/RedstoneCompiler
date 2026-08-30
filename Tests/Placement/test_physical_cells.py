import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace

from Compiler.Cells.Library import CellMacros
from Compiler.Ir.Models import Gate, GateKind
from Compiler.Placement.Geometry import (
    BuildPlacedGate,
    BuildPlacementPinAccessWitness,
)
from Compiler.Placement.Core.Clustering import PcbGatesConflict
from Compiler.Routing.Actions import (
    AreConnected,
    BuildElectricalExclusions,
    BuildPhysicalGraphs,
    NeighborPositions,
)
from Compiler.Routing.Actions.Repeaters import PruneRedundantRepeaterReservations
from Compiler.Routing.Actions.Geometry import ValidatePlacedCellElectricalIsolation
from Compiler.Routing.Workers.DetailedRouting import RustRoutingContext
from Compiler.Routing.ChannelPlanner import BuildNetRoutingProfiles
from Compiler.Routing.Technology import OppositeHorizontalFacing
from SchemEncoder.SchemWriter import (
    BlockProvenance,
    BuildLitematicBlockMap,
    BuildWireState,
    LoadTemplate,
    TemplateRepeaterPinRoles,
    ValidateSerializedRepeaterOrientations,
    WriteLitematic,
    _PlaceIoSigns,
)
from Templates import LitematicTemplates


class PhysicalCellTests(unittest.TestCase):
    def testStraightPinAccessWitnessIsCatalogMatchedAndDeterministic(self) -> None:
        Gates = (
            BuildPlacedGate(
                Gate("InputA", GateKind.INPUT, ["A"], []),
                0,
                1,
                0,
                90,
            ),
            BuildPlacedGate(
                Gate("Nand0", GateKind.NAND, ["Y"], ["A", "B"]),
                10,
                1,
                10,
                90,
                True,
            ),
            BuildPlacedGate(
                Gate("OutputY", GateKind.OUTPUT, [], ["Y"]),
                20,
                1,
                20,
                270,
            ),
        )

        First = BuildPlacementPinAccessWitness(
            Gates,
            AccessLength=3,
        )
        Second = BuildPlacementPinAccessWitness(
            reversed(Gates),
            AccessLength=3,
        )

        self.assertTrue(First.Complete)
        self.assertTrue(First.CatalogMatched)
        self.assertEqual(First.WitnessFingerprint, Second.WitnessFingerprint)
        self.assertEqual(First.ToDictionary(), Second.ToDictionary())
        self.assertEqual(len(First.Selections), 5)
        self.assertEqual(
            {Value.PatternId for Value in First.Selections},
            {"Input0Straight", "Input1Straight", "Output0Straight"},
        )
        Profiles = BuildNetRoutingProfiles(
            SimpleNamespace(PlacedGates=list(Gates)),
            AccessLength=1,
            AccessWitness=First,
        )
        self.assertEqual(set(Profiles), {"A", "Y"})
        self.assertTrue(all(
            len(Profile.SourceAccessPath) == 1
            and all(
                len(Path) == 1
                for Path in Profile.TargetAccessPaths.values()
            )
            for Profile in Profiles.values()
        ))

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
            SimpleNamespace(Position=(X, 0, 0), InputFacing="west")
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

    def testBuilderAppliesInputFacingToEveryTemplateRoleAfterTransforms(
        self,
    ) -> None:
        DirectionNames = {
            (0, 0, -1): "north",
            (0, 0, 1): "south",
            (1, 0, 0): "east",
            (-1, 0, 0): "west",
        }
        Gates = (
            Gate("InputA", GateKind.INPUT, ["A"], []),
            Gate("InputB", GateKind.INPUT, ["B"], []),
            Gate("NandY", GateKind.NAND, ["Y"], ["A", "B"]),
            Gate("OutputY", GateKind.OUTPUT, [], ["Y"]),
        )
        # OUTPUT cells consume the NAND signal but do not produce an IR value;
        # the default-state evaluator only needs the producers here.
        Module = SimpleNamespace(Gates=Gates[:3])
        for Rotation in (0, 90, 180, 270):
            for MirrorX in (False, True):
                with self.subTest(Rotation=Rotation, MirrorX=MirrorX):
                    PlacedGates = tuple(
                        BuildPlacedGate(
                            GateValue,
                            Index * 30,
                            1,
                            Index * 30,
                            Rotation,
                            MirrorX,
                        )
                        for Index, GateValue in enumerate(Gates)
                    )
                    Build = BuildLitematicBlockMap(SimpleNamespace(
                        Module=Module,
                        PlacedGates=PlacedGates,
                        Wires=(),
                        Supports=(),
                        RepeaterInputFacings={},
                        NetWires={},
                    ))
                    PlacedByName = {
                        GateValue.Name: GateValue
                        for GateValue in PlacedGates
                    }
                    for Record in Build.RepeaterOrientation["Records"]:
                        self.assertEqual(Record["Source"], "Template")
                        GateValue = PlacedByName[Record["Gate"]]
                        Role = Record["Role"]
                        if Role == "OutputPinBridge":
                            Direction = tuple(
                                -Value for Value in GateValue.OutputDirection
                            )
                        else:
                            InputIndex = int(
                                Role[len("InputPin") : -len("Bridge")]
                            )
                            Direction = GateValue.InputDirections[InputIndex]
                        self.assertEqual(
                            Record["InputFacing"],
                            DirectionNames[Direction],
                        )
                        self.assertEqual(
                            Record["OutputFacing"],
                            OppositeHorizontalFacing(
                                Record["InputFacing"]
                            ),
                        )

    def testRouteRepeaterSerializesInputFacingWithoutConversion(self) -> None:
        RoutedDesign = SimpleNamespace(
            Module=SimpleNamespace(Gates=()),
            PlacedGates=(),
            Wires=((0, 1, 0),),
            Supports=((0, 0, 0),),
            RepeaterInputFacings={(0, 1, 0): "west"},
            NetWires={"A": ((0, 1, 0),)},
        )
        Build = BuildLitematicBlockMap(RoutedDesign)

        self.assertEqual(
            Build.Blocks[(0, 1, 0)]["Properties"]["facing"],
            "west",
        )
        self.assertEqual(Build.RepeaterOrientation["RouteCount"], 1)
        self.assertEqual(
            Build.RepeaterOrientation["Records"][0]["OutputFacing"],
            "east",
        )

        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "route.litematic"
            WriteLitematic(RoutedDesign, OutputPath, Build=Build)
            self.assertTrue(Build.RepeaterOrientation["ReadbackPassed"])
            self.assertEqual(
                LoadTemplate(OutputPath).Blocks[(0, 1, 0)]["Properties"][
                    "facing"
                ],
                "west",
            )

    def testNbtReadbackRejectsOneMutatedRepeaterFacing(self) -> None:
        RoutedDesign = SimpleNamespace(
            Module=SimpleNamespace(Gates=()),
            PlacedGates=(),
            Wires=((0, 1, 0),),
            Supports=((0, 0, 0),),
            RepeaterInputFacings={(0, 1, 0): "west"},
            NetWires={"A": ((0, 1, 0),)},
        )
        Build = BuildLitematicBlockMap(RoutedDesign)
        Build.Blocks[(0, 1, 0)]["Properties"]["facing"] = "east"

        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "mutated.litematic"
            with self.assertRaisesRegex(
                ValueError,
                "Serialized repeater orientation audit failed",
            ):
                WriteLitematic(RoutedDesign, OutputPath, Build=Build)

            with self.assertRaisesRegex(ValueError, "audit failed"):
                ValidateSerializedRepeaterOrientations(OutputPath, Build)

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
            RepeaterInputFacings={},
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

    def testRouteSupportsUseCustomTracePalette(self) -> None:
        RoutedDesign = SimpleNamespace(
            Module=SimpleNamespace(Gates=()),
            PlacedGates=(),
            Wires=[(0, 1, 0), (2, 1, 0)],
            Supports=[(0, 0, 0), (2, 0, 0)],
            RepeaterInputFacings={},
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
            RepeaterInputFacings={},
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
            RepeaterInputFacings={},
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

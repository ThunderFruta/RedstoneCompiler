from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    RustRoutingContext = None

from SVDecoder.Sv import ParseSvToNetlist
import Compiler.Placement.PcbFlow as PcbFlow
from Compiler.Placement.PcbFlow import PlaceAndRoutePcb
from Compiler.Routing.Policy import LocalFirstPhysicalDesignPolicy
from Compiler.Simulation.Redstone import (
    _BuildMinecraftWireAdjacency,
    SimulateMinecraftRedstoneBlockMap,
    SimulateRoutedTruthTable,
    SimulateRoutedTruthTablePython,
)
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly


class RedstoneSimulationTests(unittest.TestCase):
    def testTorchPowersOpaqueBlockAndDustAboveIt(self) -> None:
        """A torch-under-block stack is a real cross-net power source."""
        Blocks = {
            (0, 0, 0): {
                "Name": "minecraft:redstone_torch",
                "Properties": {"lit": "true"},
            },
            (0, 1, 0): {"Name": "minecraft:red_concrete"},
            (0, 2, 0): {
                "Name": "minecraft:redstone_wire",
                "Properties": {
                    "north": "none", "south": "none",
                    "east": "none", "west": "none",
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
                "Properties": {"facing": "east", "powered": "false", "delay": "1"},
            },
            (1, 0, 0): {"Name": "minecraft:redstone_lamp"},
            (-2, 0, 0): {"Name": "minecraft:lever"},
        }
        Result = SimulateMinecraftRedstoneBlockMap(Blocks, {(-2, 0, 0): True})
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
        self.assertIn(Upper, Adjacency[Lower])
        self.assertIn(Lower, Adjacency[Upper])
        Result = SimulateMinecraftRedstoneBlockMap(
            Blocks,
            {(-1, 0, 0): True},
        )
        self.assertGreater(Result.DustPower[Upper], 0)

    def testFullAdderPassesEveryPhysicalTruthTableRow(self) -> None:
        if RustRoutingContext is None:
            self.skipTest("authoritative routing requires Rust router")

        StageCalls: list[str] = []
        RealSolveIntegratedNativeCatalog = (
            PcbFlow
            .SolvePlacementAccessNativeEscapeGuideFactorCatalogBounded
        )
        RealPrepareTrackAssignment = PcbFlow.PrepareTrackAssignment
        RealRoutePcbDesign = PcbFlow.RoutePcbDesign

        def SelectIntegratedNativeCatalog(*Arguments, **KeywordArguments):
            StageCalls.append("integrated-native-catalog-selection")
            return RealSolveIntegratedNativeCatalog(
                *Arguments,
                **KeywordArguments,
            )

        def PrepareSelectedTrackAssignment(*Arguments, **KeywordArguments):
            StageCalls.append("selected-track-preparation")
            return RealPrepareTrackAssignment(*Arguments, **KeywordArguments)

        def RouteSelectedPlacement(*Arguments, **KeywordArguments):
            StageCalls.append("route")
            return RealRoutePcbDesign(*Arguments, **KeywordArguments)

        with tempfile.TemporaryDirectory() as Workdir:
            Netlist = ParseSvToNetlist(
                InputPath=Path("Examples/FullAdder.sv"),
                TopModule=None,
                Workdir=Path(Workdir),
            )
            Optimized = OptimizeLogic(Netlist)
            NandNetlist = ToNandOnly(Optimized)
            ProgressEvents = []
            with (
                patch.object(
                    PcbFlow,
                    "SolvePlacementAccessNativeEscapeGuideFactorCatalogBounded",
                    side_effect=SelectIntegratedNativeCatalog,
                ) as SelectInterface,
                patch.object(
                    PcbFlow,
                    "PrepareTrackAssignment",
                    side_effect=PrepareSelectedTrackAssignment,
                ) as PrepareTracks,
                patch.object(
                    PcbFlow,
                    "RoutePcbDesign",
                    side_effect=RouteSelectedPlacement,
                ) as RouteDesign,
            ):
                Physical = PlaceAndRoutePcb(
                    NandNetlist,
                    ProgressCallback=ProgressEvents.append,
                )
            Report = SimulateRoutedTruthTable(
                Physical.Routed,
                ReferenceModule=Optimized.Modules[Optimized.Top],
            )
            PythonReport = SimulateRoutedTruthTablePython(
                Physical.Routed,
                ReferenceModule=Optimized.Modules[Optimized.Top],
            )

        self.assertEqual(len(Report.Rows), 8)
        self.assertEqual(Report.Rows, PythonReport.Rows)
        self.assertEqual(Report.Backend, "native-parallel")
        self.assertEqual(PythonReport.Backend, "python")
        self.assertTrue(Report.Passed)
        self.assertFalse(Physical.FallbackUsed)
        self.assertTrue(Physical.Routed.ZeroResourceConflicts)
        self.assertEqual(Physical.Routed.AssignmentExpansionCount, 0)
        self.assertNotIn(
            "AdaptiveEscalationHistory",
            Physical.Routed.RoutingControlEffectiveness,
        )
        self.assertNotIn(
            "RoutingEscalationState",
            Physical.Routed.RoutingControlEffectiveness,
        )
        self.assertIn(
            "FixedRoutingControls",
            Physical.Routed.RoutingControlEffectiveness,
        )
        CapacitySelection = Physical.Routed.RoutingControlEffectiveness[
            "PrePlacementCapacitySelection"
        ]
        # FullAdder must expose the fixed incumbent plus at least one compact
        # geometry alternative to the single pre-route selection.  A lone
        # incumbent would be an accidental regression to the old direct path.
        self.assertGreaterEqual(CapacitySelection["GeometryDomainSize"], 2)
        self.assertLessEqual(
            CapacitySelection["GeometryDomainSize"],
            LocalFirstPhysicalDesignPolicy
            .NandPacking.RetainedPlacementCandidates,
        )
        self.assertGreaterEqual(
            CapacitySelection["EnvelopeDomainSize"],
            CapacitySelection["GeometryDomainSize"],
        )
        # The small-design domain has multiple geometry/layer members.  One
        # integrated native access/guide catalog operation freezes the winner
        # before exactly one route.
        self.assertEqual(SelectInterface.call_count, 1)
        self.assertEqual(PrepareTracks.call_count, 0)
        self.assertEqual(RouteDesign.call_count, 1)
        self.assertEqual(
            StageCalls,
            ["integrated-native-catalog-selection", "route"],
        )
        self.assertEqual(CapacitySelection["CapacitySolveCount"], 1)
        self.assertEqual(CapacitySelection["RouteAttemptCount"], 1)
        self.assertEqual(
            CapacitySelection["PreSelectionDetailedDomainBuildCount"],
            0,
        )
        self.assertEqual(
            CapacitySelection["PostSelectionDetailedDomainBuildCount"],
            1,
        )
        self.assertEqual(
            CapacitySelection["NativeTemplateSelectionCallCount"],
            1,
        )
        self.assertFalse(CapacitySelection["FallbackOccurred"])
        self.assertFalse(
            CapacitySelection["SecondAssignmentInvocationOccurred"]
        )
        self.assertFalse(
            CapacitySelection["SecondRoutingInvocationOccurred"]
        )
        InterfaceSelection = Physical.Routed.RoutingControlEffectiveness[
            "PreRouteInterfaceSelection"
        ]
        self.assertTrue(InterfaceSelection["Success"])
        self.assertTrue(InterfaceSelection["Complete"])
        self.assertFalse(InterfaceSelection["Unsatisfiable"])
        self.assertEqual(len(InterfaceSelection["SelectedTemplateIds"]), 1)
        self.assertTrue(InterfaceSelection["SelectionFingerprint"])
        self.assertEqual(
            Physical.Routed.RoutingControlEffectiveness[
                "LayerCappedAssignmentAttempts"
            ],
            [],
        )
        self.assertEqual(
            Physical.Routed.RoutingControlEffectiveness[
                "LocalizedRepairPasses"
            ],
            0,
        )
        self.assertEqual(
            Physical.Routed.RoutingControlEffectiveness[
                "LocalizedReroutedNetCount"
            ],
            0,
        )
        self.assertTrue(
            any(
                0 < Progress.Completed < Progress.Total
                for Progress in ProgressEvents
            )
        )
        self.assertTrue(
            any(
                "conflicts" in Progress.Stage
                for Progress in ProgressEvents
            )
        )


if __name__ == "__main__":
    unittest.main()

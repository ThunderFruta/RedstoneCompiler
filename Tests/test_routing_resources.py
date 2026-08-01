from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from Compiler.Routing.Actions import (
    BuildPhysicalGraphs,
    BuildRoutingResources,
    FindFlatRouteConflicts,
    MaterializeReservedRepeaters,
    ValidatePhysicalRoutes,
    ValidateTemplateIsolation,
)
from Compiler.Routing.ResourceGraph import (
    BuildRoutingEnvelope,
    FindClaimConflicts,
    RoutingResourceClaims,
    RoutingResourceGraph,
)
from Compiler.Routing.Pcb import BuildPcbRoutingConfigurations
from SVDecoder.Sv import ParseSvToNetlist
from Compiler.Placement.Pcb import PlacePcbGraph
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly


class RoutingResourceTests(unittest.TestCase):
    def testRoutingEnvelopeUsesAllAxesAndExactMaterialCounts(self) -> None:
        Envelope = BuildRoutingEnvelope(
            ((4, 3, 9), (7, 5, 12)),
            ((4, 2, 9),),
            ((7, 5, 12),),
        )
        self.assertEqual((Envelope.Width, Envelope.Depth, Envelope.Height), (4, 4, 4))
        self.assertEqual(Envelope.ToDictionary()["Footprint"], 16)
        self.assertEqual(Envelope.RouteBlockCount, 2)
        self.assertEqual(Envelope.SupportBlockCount, 1)
        self.assertEqual(Envelope.RepeaterCount, 1)

    def testPhysicalGraphConstructionCanBeStoppedInsideCellLoop(self) -> None:
        Cells = {(Index, 1, 0) for Index in range(600)}
        Observed = []

        def StopDuringCells(Diagnostics):
            Observed.append(Diagnostics)
            if Diagnostics["Phase"] == "cells":
                raise RuntimeError("physical graph deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "physical graph deadline expired",
        ):
            BuildPhysicalGraphs(
                {"A": Cells},
                ActualBlocks=set(),
                Supports={(Index, 0, 0) for Index in range(600)},
                WorkCheck=StopDuringCells,
            )

        self.assertEqual(Observed[-1]["ProcessedCells"], 256)

    def testPhysicalValidationCanBeStoppedInsideConnectivityWalk(self) -> None:
        Nodes = [(Index, 1, 0) for Index in range(600)]
        Graph = {
            Node: [
                Neighbor
                for Neighbor in (
                    (Node[0] - 1, 1, 0),
                    (Node[0] + 1, 1, 0),
                )
                if 0 <= Neighbor[0] < len(Nodes)
            ]
            for Node in Nodes
        }
        Observed = []

        def StopDuringConnectivity(Diagnostics):
            Observed.append(Diagnostics)
            if Diagnostics["Phase"] == "connectivity":
                raise RuntimeError("physical validation deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "physical validation deadline expired",
        ):
            ValidatePhysicalRoutes(
                {"A": Graph},
                {"A": SimpleNamespace(OutputPin=Nodes[0])},
                {"A": [Nodes[-1]]},
                WorkCheck=StopDuringConnectivity,
            )

        self.assertEqual(Observed[-1]["ExpandedNodes"], 256)

    def testTemplateIsolationCanBeStoppedWhileExpandingKeepout(self) -> None:
        Observed = []

        def StopDuringKeepOut(Diagnostics):
            Observed.append(Diagnostics)
            if Diagnostics["Phase"] == "template-keepout":
                raise RuntimeError("template isolation deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "template isolation deadline expired",
        ):
            ValidateTemplateIsolation(
                {},
                set(),
                {(Index, 1, 0) for Index in range(600)},
                set(),
                {},
                {},
                WorkCheck=StopDuringKeepOut,
            )

        self.assertEqual(Observed[-1]["ProcessedPositions"], 256)

    def testRoutingResourceConstructionCanStopInsideFrozenWireLoop(self) -> None:
        Observed = []

        def StopDuringFrozenWires(Diagnostics):
            Observed.append(Diagnostics)
            if Diagnostics["Phase"] == "routing-resources-frozen-position":
                raise RuntimeError("resource construction deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "resource construction deadline expired",
        ):
            BuildRoutingResources(
                SimpleNamespace(
                    PlacedGates=[],
                    FrozenNetWires={
                        "A": {(Index, 1, 0) for Index in range(600)},
                    },
                ),
                WorkCheck=StopDuringFrozenWires,
            )

        self.assertEqual(Observed[-1]["ProcessedPositions"], 256)

    def testRouteClaimConstructionAndConflictPairsAreStoppable(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        ObservedClaims = []

        def StopDuringClaims(Diagnostics):
            ObservedClaims.append(Diagnostics)
            if Diagnostics["Phase"] == "collect-wire-cells":
                raise RuntimeError("claim rebuild deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "claim rebuild deadline expired",
        ):
            Graph.BuildRouteClaims(
                ((Index, 1, 0) for Index in range(600)),
                WorkCheck=StopDuringClaims,
            )
        self.assertEqual(ObservedClaims[-1]["ProcessedPositions"], 256)

        Claims = {
            f"Signal{Index}": RoutingResourceClaims(
                WireCells=frozenset({(Index * 4, 1, 0)}),
                ElectricalCells=frozenset({(Index * 4, 1, 0)}),
            )
            for Index in range(12)
        }
        ObservedPairs = []

        def StopDuringPairs(Diagnostics):
            ObservedPairs.append(Diagnostics)
            if Diagnostics["Phase"] == "signal-pairs":
                raise RuntimeError("claim conflict deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "claim conflict deadline expired",
        ):
            FindClaimConflicts(Claims, WorkCheck=StopDuringPairs)
        self.assertEqual(ObservedPairs[-1]["SignalPairChecks"], 64)

    def testFlatConflictRebuildCanStopInsideSignalPairs(self) -> None:
        NetWires = {
            f"Signal{Index}": {(Index * 4, 1, 0)}
            for Index in range(12)
        }
        Observed = []

        def StopDuringPairs(Diagnostics):
            Observed.append(Diagnostics)
            if Diagnostics["Phase"] == "signal-pairs":
                raise RuntimeError("flat conflict deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "flat conflict deadline expired",
        ):
            FindFlatRouteConflicts(NetWires, WorkCheck=StopDuringPairs)

        self.assertEqual(Observed[-1]["SignalPairChecks"], 64)

    def testRepeaterMaterializationCanStopInsideFallbackPathSearch(self) -> None:
        Nodes = [(Index, 1, 0) for Index in range(600)]
        Graph = {
            Node: [
                Neighbor
                for Neighbor in (
                    (Node[0] - 1, 1, 0),
                    (Node[0] + 1, 1, 0),
                )
                if 0 <= Neighbor[0] < len(Nodes)
            ]
            for Node in Nodes
        }
        Observed = []

        def StopDuringFallback(Diagnostics):
            Observed.append(Diagnostics)
            if Diagnostics["Phase"] == "fallback-path-search":
                raise RuntimeError("repeater deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "repeater deadline expired",
        ):
            MaterializeReservedRepeaters(
                {"A": set(Nodes)},
                {"A": SimpleNamespace(OutputPin=Nodes[0])},
                {"A": [Nodes[-1]]},
                {"A": Graph},
                {"A": SimpleNamespace(RepeaterReservations=())},
                WorkCheck=StopDuringFallback,
            )

        self.assertEqual(Observed[-1]["ExpandedNodes"], 256)

    def testMaterializedRepeaterPruningRetainsOnlyRequiredBranches(self) -> None:
        Nodes = {
            *((Index, 1, 0) for Index in range(29)),
            *((10, 1, Index) for Index in range(1, 19)),
        }
        Graph = {
            Node: sorted(
                Neighbor
                for Neighbor in Nodes
                if sum(
                    abs(Node[Axis] - Neighbor[Axis])
                    for Axis in range(3)
                ) == 1
            )
            for Node in Nodes
        }
        Root = (0, 1, 0)
        Targets = ((28, 1, 0), (10, 1, 18))
        Reservations = tuple(
            SimpleNamespace(Position=Position, Facing=Facing)
            for Position, Facing in (
                ((8, 1, 0), "west"),
                ((14, 1, 0), "west"),
                ((10, 1, 4), "north"),
                ((10, 1, 12), "north"),
            )
        )
        Diagnostics = {}
        Repeaters = MaterializeReservedRepeaters(
            {"A": set(Nodes)},
            {"A": SimpleNamespace(OutputPin=Root)},
            {"A": list(Targets)},
            {"A": Graph},
            {"A": SimpleNamespace(RepeaterReservations=Reservations)},
            PruningDiagnostics=Diagnostics,
        )

        self.assertEqual(
            Repeaters,
            {
                (14, 1, 0): "west",
                (10, 1, 4): "north",
            },
        )
        self.assertEqual(Diagnostics["A"]["InitialCount"], 4)
        self.assertEqual(
            Diagnostics["A"]["RemovedPositions"],
            [[8, 1, 0], [10, 1, 12]],
        )
        self.assertTrue(Diagnostics["A"]["PowerValidated"])

    def testFrozenRoutesAreObstaclesButNotTemplateElectricalBlocks(self) -> None:
        FrozenPosition = (1, 1, 0)
        Resources = BuildRoutingResources(SimpleNamespace(
            PlacedGates=[],
            FrozenNetWires={"A": (FrozenPosition,)},
        ))

        self.assertIn(
            FrozenPosition,
            Resources.StaticGeometry.ElectricalBlocks,
        )
        self.assertNotIn(
            FrozenPosition,
            Resources.StaticGeometry.TemplateElectricalBlocks,
        )

        Arguments = (
            {"A": {(0, 1, 0), FrozenPosition}},
            set(),
            set(Resources.StaticGeometry.TemplateElectricalBlocks),
            set(),
            {"A": SimpleNamespace(OutputPin=(0, 1, 0))},
            {"A": []},
        )
        ValidateTemplateIsolation(*Arguments)
        with self.assertRaisesRegex(ValueError, "template electrical clearance"):
            ValidateTemplateIsolation(
                Arguments[0],
                Arguments[1],
                set(Resources.StaticGeometry.ElectricalBlocks),
                *Arguments[3:],
            )

    def testSupportBlocksAreNegotiatedConflictResources(self) -> None:
        ConflictCells, ConflictCounts = FindFlatRouteConflicts(
            {
                "Upper": {(0, 1, 0)},
                "Lower": {(0, 0, 0)},
            }
        )

        self.assertIn((0, 0, 0), ConflictCells)
        self.assertGreater(ConflictCounts["Upper"], 0)
        self.assertGreater(ConflictCounts["Lower"], 0)

    def testRoutingUsesOneAuthoritativeStrictAttempt(self) -> None:
        Placement = SimpleNamespace()
        Configurations = BuildPcbRoutingConfigurations(Placement)

        self.assertEqual(len(Configurations), 1)
        self.assertEqual(Configurations[0].AttemptId, "Authoritative")
        self.assertEqual(Configurations[0].SearchMargin, 20)
        self.assertEqual(Configurations[0].GuidePenalty, 6)
        self.assertEqual(Configurations[0].MaximumIterations, 4)
        self.assertEqual(Configurations[0].OrderMode, "Natural")

    def testTerminalBanksPreserveDeclaredPortOrder(self) -> None:
        with TemporaryDirectory() as Workdir:
            Netlist = ParseSvToNetlist(
                InputPath=Path("Examples/RippleCarryAdder4.sv"),
                Workdir=Path(Workdir),
            )
            Placement = PlacePcbGraph(
                ToNandOnly(OptimizeLogic(Netlist)),
                RoutingSpacing=6,
            )

        Inputs = sorted(
            (
                Gate.X,
                Gate.Outputs[0],
            )
            for Gate in Placement.Placed.PlacedGates
            if Gate.Kind == "INPUT"
        )
        Outputs = sorted(
            (
                Gate.X,
                Gate.Inputs[0],
            )
            for Gate in Placement.Placed.PlacedGates
            if Gate.Kind == "OUTPUT"
        )
        self.assertEqual(
            [Signal for _X, Signal in Inputs],
            list(Netlist.Modules[Netlist.Top].Inputs),
        )
        self.assertEqual(
            [Signal for _X, Signal in Outputs],
            list(Netlist.Modules[Netlist.Top].Outputs),
        )


if __name__ == "__main__":
    unittest.main()

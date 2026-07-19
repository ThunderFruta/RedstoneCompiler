from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from Compiler.Routing.Core import (
    FindFlatRouteConflicts,
)
from Compiler.Routing.Pcb import BuildPcbRoutingConfigurations
from SVDecoder.Sv import ParseSvToNetlist
from Compiler.Placement.Pcb import PlacePcbGraph
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly


class RoutingResourceTests(unittest.TestCase):
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

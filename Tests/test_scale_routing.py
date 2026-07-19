import os
from pathlib import Path
import tempfile
import unittest

from SVDecoder.Sv import ParseSvToNetlist
from Compiler.Placement.PcbFlow import PlaceAndRoutePcb
from Compiler.Simulation.Redstone import SimulateRoutedTruthTable
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly
from Compiler.Routing.Policy import RoutingStrategy


RUN_SCALE_TESTS = os.environ.get("RC_RUN_SCALE_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


@unittest.skipUnless(
    RUN_SCALE_TESTS,
    "set RC_RUN_SCALE_TESTS=1 to run the routed 4-bit acceptance tests",
)
class ScaleRoutingTests(unittest.TestCase):
    def AssertExampleRoutesAndSimulates(self, ExampleName: str) -> None:
        with tempfile.TemporaryDirectory() as Workdir:
            Netlist = ParseSvToNetlist(
                InputPath=Path("Examples") / ExampleName,
                TopModule=None,
                Workdir=Path(Workdir),
            )
            Optimized = OptimizeLogic(Netlist)
            Physical = PlaceAndRoutePcb(
                ToNandOnly(Optimized),
                Strategy=RoutingStrategy.NewRouterFirst,
            )
            Report = SimulateRoutedTruthTable(
                Physical.Routed,
                ReferenceModule=Optimized.Modules[Optimized.Top],
            )

        self.assertIsNotNone(Physical.Routed.GlobalPlan)
        self.assertFalse(Physical.Routed.GlobalPlan.ResourceOverflow)
        self.assertEqual(len(Report.Rows), 512)
        self.assertTrue(Report.Passed)

    def testRippleCarryAdder4RoutesAndSimulates(self) -> None:
        self.AssertExampleRoutesAndSimulates("RippleCarryAdder4.sv")

    def testCarryLookaheadAdder4RoutesAndSimulates(self) -> None:
        self.AssertExampleRoutesAndSimulates("CarryLookaheadAdder4.sv")


if __name__ == "__main__":
    unittest.main()

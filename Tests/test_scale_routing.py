import os
from pathlib import Path
import tempfile
import unittest

from SVDecoder.Sv import ParseSvToNetlist
from Compiler.Placement.Flow.Runner import PlaceAndRoutePcb
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
    def AssertExampleRoutesAndSimulates(
        self,
        ExampleName: str,
        ExpectedTruthTableRows: int = 512,
    ):
        with tempfile.TemporaryDirectory() as Workdir:
            Netlist = ParseSvToNetlist(
                InputPath=Path("Examples") / ExampleName,
                TopModule=None,
                Workdir=Path(Workdir),
            )
            Optimized = OptimizeLogic(Netlist)
            Physical = PlaceAndRoutePcb(
                ToNandOnly(Optimized),
                Strategy=RoutingStrategy.Default,
            )
            Report = SimulateRoutedTruthTable(
                Physical.Routed,
                ReferenceModule=Optimized.Modules[Optimized.Top],
            )

        self.assertIsNotNone(Physical.Routed.GlobalPlan)
        self.assertFalse(Physical.Routed.GlobalPlan.ResourceOverflow)
        self.assertEqual(len(Report.Rows), ExpectedTruthTableRows)
        self.assertTrue(Report.Passed)
        Handoff = Physical.Routed.RoutingControlEffectiveness[
            "PrePlacementTrackAssignmentHandoff"
        ]
        self.assertTrue(Handoff["Applied"])
        self.assertEqual(Handoff["NativeAssignmentExpansionCount"], 0)
        return Physical

    def testRippleCarryAdder4RoutesAndSimulates(self) -> None:
        self.AssertExampleRoutesAndSimulates("RippleCarryAdder4.sv")

    def testCarryLookaheadAdder4RoutesAndSimulates(self) -> None:
        self.AssertExampleRoutesAndSimulates("CarryLookaheadAdder4.sv")

    def testRippleCarryAdder8SelectsFixedGeometryBeforeRouting(self) -> None:
        Physical = self.AssertExampleRoutesAndSimulates(
            "RippleCarryAdder8.sv",
            ExpectedTruthTableRows=131072,
        )

        Selection = Physical.Routed.RoutingControlEffectiveness[
            "PrePlacementCapacitySelection"
        ]
        self.assertEqual(Selection["GeometryDomainSize"], 2)
        self.assertEqual(Selection["CapacitySolveCount"], 1)
        self.assertEqual(Selection["RouteAttemptCount"], 1)
        self.assertEqual(
            Selection["CandidateResults"][0]["IncompleteReason"],
            "immutable-local-claim-conflict",
        )
        self.assertTrue(Selection["CandidateResults"][1]["Success"])


if __name__ == "__main__":
    unittest.main()

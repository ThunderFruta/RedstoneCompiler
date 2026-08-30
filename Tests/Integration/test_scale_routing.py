import os
from pathlib import Path
import tempfile
import unittest

from SVDecoder.Sv import ParseSvToNetlist
from Compiler.Placement.Flow.Runner import PlaceAndRoutePcb
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
    def AssertExampleRoutes(
        self,
        ExampleName: str,
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

        self.assertIsNotNone(Physical.Routed.GlobalPlan)
        self.assertFalse(Physical.Routed.GlobalPlan.ResourceOverflow)
        Handoff = Physical.Routed.RoutingControlEffectiveness[
            "PrePlacementTrackAssignmentHandoff"
        ]
        self.assertTrue(Handoff["Applied"])
        self.assertEqual(Handoff["NativeAssignmentExpansionCount"], 0)
        PinAccessWitness = Handoff["PlacementPinAccessWitness"]
        self.assertTrue(PinAccessWitness["Complete"])
        self.assertTrue(PinAccessWitness["CatalogMatched"])
        self.assertTrue(PinAccessWitness["WitnessFingerprint"])
        return Physical

    def testRippleCarryAdder4Routes(self) -> None:
        self.AssertExampleRoutes("RippleCarryAdder4.sv")

    def testCarryLookaheadAdder4Routes(self) -> None:
        self.AssertExampleRoutes("CarryLookaheadAdder4.sv")

    def testRippleCarryAdder8SelectsFixedGeometryBeforeRouting(self) -> None:
        Physical = self.AssertExampleRoutes("RippleCarryAdder8.sv")

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

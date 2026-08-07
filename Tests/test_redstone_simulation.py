from pathlib import Path
import tempfile
import unittest

try:
    from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    RustRoutingContext = None

from SVDecoder.Sv import ParseSvToNetlist
from Compiler.Placement.PcbFlow import PlaceAndRoutePcb
from Compiler.Simulation.Redstone import (
    SimulateRoutedTruthTable,
    SimulateRoutedTruthTablePython,
)
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly


class RedstoneSimulationTests(unittest.TestCase):
    def testFullAdderPassesEveryPhysicalTruthTableRow(self) -> None:
        if RustRoutingContext is None:
            self.skipTest("authoritative routing requires Rust router")

        with tempfile.TemporaryDirectory() as Workdir:
            Netlist = ParseSvToNetlist(
                InputPath=Path("Examples/FullAdder.sv"),
                TopModule=None,
                Workdir=Path(Workdir),
            )
            Optimized = OptimizeLogic(Netlist)
            NandNetlist = ToNandOnly(Optimized)
            ProgressEvents = []
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
        self.assertEqual(CapacitySelection["GeometryDomainSize"], 1)
        self.assertEqual(CapacitySelection["CapacitySolveCount"], 1)
        self.assertEqual(CapacitySelection["RouteAttemptCount"], 1)
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

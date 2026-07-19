from pathlib import Path
import tempfile
import unittest

try:
    from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    RustRoutingContext = None

from SVDecoder.Sv import ParseSvToNetlist
from Compiler.Placement.PcbFlow import PlaceAndRoutePcb
from Compiler.Simulation.Redstone import SimulateRoutedTruthTable
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

        self.assertEqual(len(Report.Rows), 8)
        self.assertTrue(Report.Passed)
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

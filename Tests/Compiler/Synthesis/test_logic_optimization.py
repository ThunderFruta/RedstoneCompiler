from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from Compiler.Frontend.Sv import ParseSvToNetlist
from Compiler.Synthesis.LogicOptimization import (
    CountNands,
    EvaluateModuleOutputs,
    OptimizeLogic,
)


class LogicOptimizationTests(unittest.TestCase):
    def AssertEquivalent(self, First, Second) -> None:
        FirstModule = First.Modules[First.Top]
        SecondModule = Second.Modules[Second.Top]
        self.assertEqual(FirstModule.Inputs, SecondModule.Inputs)
        for Assignment in range(1 << len(FirstModule.Inputs)):
            self.assertEqual(
                EvaluateModuleOutputs(FirstModule, Assignment),
                EvaluateModuleOutputs(SecondModule, Assignment),
            )

    def testRedundantEquationUsesNoNands(self) -> None:
        Source = """
module Redundant(input A, input B, output Y);
    wire NotB;
    assign NotB = ~B;
    assign Y = (A & B) | (A & NotB);
endmodule
"""
        with TemporaryDirectory() as Directory:
            SourcePath = Path(Directory) / "Redundant.sv"
            SourcePath.write_text(Source)
            Netlist = ParseSvToNetlist(InputPath=SourcePath)
            Optimized = OptimizeLogic(Netlist)

        self.AssertEquivalent(Netlist, Optimized)
        self.assertEqual(CountNands(Optimized), 0)
        self.assertLess(CountNands(Optimized), CountNands(Netlist))

    def testExampleOptimizationNeverIncreasesNands(self) -> None:
        for SourcePath in Path("Examples").glob("*.sv"):
            with self.subTest(SourcePath=SourcePath):
                Netlist = ParseSvToNetlist(InputPath=SourcePath)
                Optimized = OptimizeLogic(Netlist)
                self.AssertEquivalent(Netlist, Optimized)
                self.assertLessEqual(
                    CountNands(Optimized),
                    CountNands(Netlist),
                )


if __name__ == "__main__":
    unittest.main()

"""Coverage for the embedded MCHPRS physical-validation backend."""

from __future__ import annotations

from pathlib import Path
import unittest

from Compiler.Ir.Models import Gate, GateKind, ModuleIR
from ValidationServerHarness.Mchprs import MchprsValidator
from Compiler.PhysicalValidation import (
    BuildFabricCanaryVectors,
    BuildValidationAssignments,
    PhysicalFixtureArtifact,
)


RepositoryRoot = Path(__file__).resolve().parents[1]


class MchprsValidationTests(unittest.TestCase):
    def testValidationPolicyIsExhaustiveThroughTwentyInputs(self) -> None:
        self.assertEqual(
            len(BuildValidationAssignments(("a", "b", "c"))),
            8,
        )
        WideNames = tuple(f"i{Index}" for Index in range(21))
        First = BuildValidationAssignments(WideNames)
        Second = BuildValidationAssignments(WideNames)
        self.assertEqual(len(First), 2 + 2 * len(WideNames) + 4096)
        self.assertEqual(First, Second)

    def testFabricCanariesAreExtremesOneHotAndOneCold(self) -> None:
        Module = ModuleIR(
            Name="CanaryPolicy",
            Inputs=["a", "b", "c"],
            Outputs=["y"],
            Gates=[
                Gate(
                    Name="Nand",
                    Kind=GateKind.NAND,
                    Inputs=["a", "b"],
                    Outputs=["n"],
                ),
                Gate(
                    Name="Output",
                    Kind=GateKind.OUTPUT,
                    Inputs=["n"],
                    Outputs=["y"],
                ),
            ],
            SourcePath=Path("CanaryPolicy.sv"),
        )
        Vectors = BuildFabricCanaryVectors(
            Module,
            Module.Inputs,
            Module.Outputs,
        )
        self.assertEqual(len(Vectors), 8)
        self.assertEqual(
            {tuple(sorted(Vector["Inputs"].items())) for Vector in Vectors},
            {
                tuple(sorted(Assignment.items()))
                for Assignment in BuildValidationAssignments(Module.Inputs)
            },
        )

    @unittest.skipUnless(
        (RepositoryRoot / "Output" / "FullAdder" / "FullAdder.FabricFixture.json").is_file(),
        "installed FullAdder physical fixture is unavailable",
    )
    def testFullAdderPassesEveryMchprsVector(self) -> None:
        FixturePath = (
            RepositoryRoot / "Output" / "FullAdder" / "FullAdder.FabricFixture.json"
        )
        FixtureDocument = FixturePath.read_bytes()
        Result = MchprsValidator().Validate(
            Fixture=PhysicalFixtureArtifact(
                Path=FixturePath,
                Sha256="test-fixture",
                BlockCount=0,
                InputCount=3,
                OutputCount=2,
            ),
            LogicPath=RepositoryRoot / "Output" / "FullAdder" / "FullAdder.Nand.json",
        )
        self.assertTrue(FixtureDocument)
        self.assertEqual(Result.Status, "passed")
        self.assertEqual(Result.Diagnostics["TestedVectors"], 8)

    @unittest.skipUnless(
        (RepositoryRoot / "Output" / "RippleCarryAdder8" / "RippleCarryAdder8.FabricFixture.json").is_file(),
        "installed RCA8 physical fixture is unavailable",
    )
    def testRca8PassesAllOneHundredThirtyOneThousandVectors(self) -> None:
        FixturePath = (
            RepositoryRoot
            / "Output"
            / "RippleCarryAdder8"
            / "RippleCarryAdder8.FabricFixture.json"
        )
        Result = MchprsValidator().Validate(
            Fixture=PhysicalFixtureArtifact(
                Path=FixturePath,
                Sha256="test-fixture",
                BlockCount=0,
                InputCount=17,
                OutputCount=9,
            ),
            LogicPath=(
                RepositoryRoot
                / "Output"
                / "RippleCarryAdder8"
                / "RippleCarryAdder8.Nand.json"
            ),
        )
        self.assertEqual(Result.Status, "passed")
        self.assertTrue(Result.Diagnostics["Exhaustive"])
        self.assertEqual(Result.Diagnostics["TestedVectors"], 131_072)


if __name__ == "__main__":
    unittest.main()

"""Coverage for the embedded MCHPRS physical-validation backend."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from Compilation.Ir.Models import Gate, GateKind, ModuleIR
from Validation.Mchprs import MchprsValidator
from Validation.Physical import BuildFabricCanaryVectors, BuildValidationAssignments, ExhaustiveInputLimit, PhysicalFixtureArtifact


RepositoryRoot = Path(__file__).resolve().parents[3]
FixtureRoot = RepositoryRoot / "Tests/Fixtures/Mchprs"


def LoadFixtureCase(Name: str) -> tuple[dict[str, object], Path, Path]:
    """Load one hash-bound tracked MCHPRS fixture pair."""
    Manifest = json.loads((FixtureRoot / "Manifest.json").read_text())
    if Manifest.get("SchemaVersion") != "mchprs-test-fixtures-v1":
        raise ValueError("unsupported MCHPRS test fixture manifest")
    Case = Manifest["Circuits"][Name]
    FixturePath = FixtureRoot / Case["PhysicalFixture"]["Path"]
    LogicPath = FixtureRoot / Case["NandLogic"]["Path"]
    for PathValue, Definition in (
        (FixturePath, Case["PhysicalFixture"]),
        (LogicPath, Case["NandLogic"]),
    ):
        ActualSha256 = sha256(PathValue.read_bytes()).hexdigest()
        if ActualSha256 != Definition["Sha256"]:
            raise ValueError(f"MCHPRS test fixture hash mismatch: {PathValue}")
    return Case, FixturePath, LogicPath


class MchprsValidationTests(unittest.TestCase):
    def testValidationPolicyIsExhaustiveThroughTwentyInputs(self) -> None:
        self.assertEqual(ExhaustiveInputLimit, 20)
        self.assertEqual(
            len(BuildValidationAssignments(("a", "b", "c"))),
            8,
        )
        WideNames = tuple(f"i{Index}" for Index in range(21))
        First = BuildValidationAssignments(WideNames)
        Second = BuildValidationAssignments(WideNames)
        self.assertEqual(len(First), 2 + 2 * len(WideNames) + 4096)
        self.assertEqual(First, Second)

    def testValidationAssignmentBoundaryIsExactAndDeterministic(self) -> None:
        for ExhaustiveLimit in (2, 3, 4):
            with self.subTest(ExhaustiveLimit=ExhaustiveLimit):
                BoundaryNames = tuple(
                    f"i{Index}" for Index in range(ExhaustiveLimit)
                )
                WideNames = tuple(f"i{Index}" for Index in range(13))
                self.assertEqual(
                    len(BuildValidationAssignments(
                        BoundaryNames,
                        ExhaustiveLimit=ExhaustiveLimit,
                    )),
                    1 << ExhaustiveLimit,
                )
                First = BuildValidationAssignments(
                    WideNames,
                    ExhaustiveLimit=ExhaustiveLimit,
                )
                Second = BuildValidationAssignments(
                    tuple(reversed(WideNames)),
                    ExhaustiveLimit=ExhaustiveLimit,
                )
                self.assertEqual(
                    len(First),
                    2 + 2 * len(WideNames) + 4096,
                )
                self.assertEqual(First, Second)

    def testFabricCanaryCountStaysLinearForExhaustiveMchprsDesigns(
        self,
    ) -> None:
        for InputCount, ExpectedCanaries in ((9, 20), (17, 36)):
            Inputs = [f"i{Index}" for Index in range(InputCount)]
            Module = ModuleIR(
                Name=f"Canary{InputCount}",
                Inputs=Inputs,
                Outputs=["y"],
                Gates=[
                    Gate(
                        Name="Nand",
                        Kind=GateKind.NAND,
                        Inputs=Inputs[:2],
                        Outputs=["y"],
                    ),
                ],
                SourcePath=Path(f"Canary{InputCount}.sv"),
            )
            with self.subTest(InputCount=InputCount):
                self.assertEqual(
                    len(BuildFabricCanaryVectors(
                        Module,
                        Module.Inputs,
                        Module.Outputs,
                    )),
                    ExpectedCanaries,
                )

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

    def testFullAdderPassesEveryMchprsVector(self) -> None:
        Case, FixturePath, LogicPath = LoadFixtureCase("FullAdder")
        FixtureDocument = json.loads(FixturePath.read_text())
        Result = MchprsValidator().Validate(
            Fixture=PhysicalFixtureArtifact(
                Path=FixturePath,
                Sha256=Case["PhysicalFixture"]["Sha256"],
                BlockCount=len(FixtureDocument["Blocks"]),
                InputCount=len(FixtureDocument["Inputs"]),
                OutputCount=len(FixtureDocument["Outputs"]),
            ),
            LogicPath=LogicPath,
        )
        self.assertEqual(Result.Status, "passed")
        self.assertEqual(
            Result.Diagnostics["TestedVectors"],
            Case["ExpectedVectors"],
        )

    def testRca8PassesAllOneHundredThirtyOneThousandVectors(self) -> None:
        Case, FixturePath, LogicPath = LoadFixtureCase("RippleCarryAdder8")
        FixtureDocument = json.loads(FixturePath.read_text())
        Result = MchprsValidator().Validate(
            Fixture=PhysicalFixtureArtifact(
                Path=FixturePath,
                Sha256=Case["PhysicalFixture"]["Sha256"],
                BlockCount=len(FixtureDocument["Blocks"]),
                InputCount=len(FixtureDocument["Inputs"]),
                OutputCount=len(FixtureDocument["Outputs"]),
            ),
            LogicPath=LogicPath,
        )
        self.assertEqual(Result.Status, "passed")
        self.assertTrue(Result.Diagnostics["Exhaustive"])
        self.assertEqual(
            Result.Diagnostics["TestedVectors"],
            Case["ExpectedVectors"],
        )


if __name__ == "__main__":
    unittest.main()

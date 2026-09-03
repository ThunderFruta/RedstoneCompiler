"""Exhaustive arithmetic-oracle coverage for the bundled adder examples."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from Compiler.Ir.Models import GateKind, ModuleIR
from Compiler.Synthesis.LogicEvaluation import EvaluateLogicModule
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly
from Compiler.Synthesis.Validation import ValidateNandOnlyDesign
from Compiler.Frontend.Sv import ParseSvToNetlist


@dataclass(frozen=True)
class AdderOracleCase:
    """One exhaustive scalar-adder oracle configuration."""

    ModuleName: str
    Width: int
    ExpectedRows: int
    ExpectedDigest: str

    @property
    def SourcePath(self) -> Path:
        return Path("Examples") / f"{self.ModuleName}.sv"


FullAdderCase = AdderOracleCase(
    ModuleName="FullAdder",
    Width=1,
    ExpectedRows=8,
    ExpectedDigest=(
        "db3a85c6851c53d5d2c74587ad636846"
        "43d4b94ac48197ba17c55c89ce56c16c"
    ),
)
RippleCarryAdder4Case = AdderOracleCase(
    ModuleName="RippleCarryAdder4",
    Width=4,
    ExpectedRows=512,
    ExpectedDigest=(
        "231006e91741e994c318a56931dd2c99"
        "4be7849b989c45ec73dd3c3d7689f262"
    ),
)
RippleCarryAdder8Case = AdderOracleCase(
    ModuleName="RippleCarryAdder8",
    Width=8,
    ExpectedRows=131_072,
    ExpectedDigest=(
        "302104d45f6eb340bbcf1f267c10da1a"
        "65233a875cac5c4d9d3f116caa3c1269"
    ),
)
CarryLookaheadAdder4Case = AdderOracleCase(
    ModuleName="CarryLookaheadAdder4",
    Width=4,
    ExpectedRows=512,
    ExpectedDigest=RippleCarryAdder4Case.ExpectedDigest,
)

IdentifierPattern = re.compile(r"\b[A-Za-z_][A-Za-z0-9_$]*\b")
AssignmentPattern = re.compile(
    r"\bassign\s+([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(.*?);",
    flags=re.DOTALL,
)


def BuildAdderAssignment(
    Case: AdderOracleCase,
    Left: int,
    Right: int,
    CarryIn: int,
) -> dict[str, bool]:
    """Map integer operands to the scalar input names used by one example."""
    if Case.Width == 1:
        return {
            "A": bool(Left),
            "B": bool(Right),
            "CarryIn": bool(CarryIn),
        }
    Assignment = {
        f"A{Index}": bool((Left >> Index) & 1)
        for Index in range(Case.Width)
    }
    Assignment.update({
        f"B{Index}": bool((Right >> Index) & 1)
        for Index in range(Case.Width)
    })
    Assignment["CarryIn"] = bool(CarryIn)
    return Assignment


def DecodeAdderResult(
    Case: AdderOracleCase,
    Values: dict[str, bool],
) -> int:
    """Decode scalar sum/carry outputs into one unsigned result."""
    if Case.Width == 1:
        Sum = int(Values["Sum"])
    else:
        Sum = sum(
            int(Values[f"Sum{Index}"]) << Index
            for Index in range(Case.Width)
        )
    return Sum | (int(Values["CarryOut"]) << Case.Width)


def RenameIdentifiers(Text: str, Renames: dict[str, str]) -> str:
    """Apply exact-token identifier renames to a SystemVerilog fragment."""
    return IdentifierPattern.sub(
        lambda Match: Renames.get(Match.group(0), Match.group(0)),
        Text,
    )


def BuildMetamorphicCla4Source(
    Module: ModuleIR,
    Source: str,
) -> str:
    """Rename internals and reorder independent CLA4 declarations/assignments."""
    Assignments = [
        (Match.group(1), " ".join(Match.group(2).split()))
        for Match in AssignmentPattern.finditer(Source)
    ]
    AssignedNames = {Target for Target, _Expression in Assignments}
    InternalNames = sorted(AssignedNames - set(Module.Outputs))
    Renames = {
        Name: f"AuxiliarySignal{Index}"
        for Index, Name in enumerate(reversed(InternalNames))
    }

    Dependencies = {
        Target: set(IdentifierPattern.findall(Expression)) & AssignedNames
        for Target, Expression in Assignments
    }
    Expressions = dict(Assignments)
    OrderedTargets: list[str] = []
    Completed: set[str] = set()
    while len(OrderedTargets) < len(Assignments):
        Ready = sorted(
            (
                Target
                for Target, _Expression in Assignments
                if Target not in Completed
                and Dependencies[Target] <= Completed
            ),
            reverse=True,
        )
        if not Ready:
            raise ValueError("CLA4 assignments contain a combinational cycle")
        OrderedTargets.extend(Ready)
        Completed.update(Ready)

    PortLines = [
        *(f"    input {Name}" for Name in reversed(Module.Inputs)),
        *(f"    output {Name}" for Name in reversed(Module.Outputs)),
    ]
    Header = ",\n".join(PortLines)
    Declarations = "\n".join(
        f"    wire {Renames[Name]};"
        for Name in reversed(InternalNames)
    )
    ReorderedAssignments = "\n".join(
        "    assign "
        f"{RenameIdentifiers(Target, Renames)} = "
        f"{RenameIdentifiers(Expressions[Target], Renames)};"
        for Target in OrderedTargets
    )
    return (
        "module ArithmeticMetamorph (\n"
        f"{Header}\n"
        ");\n"
        f"{Declarations}\n\n"
        f"{ReorderedAssignments}\n"
        "endmodule\n"
    )


def BuildNameIndependentNandSignature(Module: ModuleIR) -> tuple[object, ...]:
    """Describe NAND topology without using generated gate/net identifiers."""
    NandProducers = {
        Gate.Output: Gate
        for Gate in Module.Gates
        if Gate.Kind == GateKind.NAND
    }
    Memo: dict[str, tuple[object, ...]] = {}

    def DescribeSignal(Signal: str) -> tuple[object, ...]:
        if Signal in Memo:
            return Memo[Signal]
        if Signal in Module.Inputs:
            Result: tuple[object, ...] = ("INPUT", Signal)
        else:
            Producer = NandProducers[Signal]
            Inputs = sorted(
                (DescribeSignal(Input) for Input in Producer.Inputs),
                key=repr,
            )
            Result = ("NAND", *Inputs)
        Memo[Signal] = Result
        return Result

    OutputRoots = {
        Gate.Output.removesuffix("$Output"): Gate.Inputs[0]
        for Gate in Module.Gates
        if Gate.Kind == GateKind.OUTPUT
    }
    OutputSignatures = tuple(
        (Output, DescribeSignal(OutputRoots[Output]))
        for Output in sorted(Module.Outputs)
    )
    NodeSignatures = Counter(
        DescribeSignal(Gate.Output)
        for Gate in Module.Gates
        if Gate.Kind == GateKind.NAND
    )
    return (
        OutputSignatures,
        tuple(sorted(NodeSignatures.items(), key=repr)),
    )


class AdderArithmeticOracleTests(unittest.TestCase):
    """Verify parsed, optimized, and NAND-only adders exhaustively."""

    def AssertModuleMatchesArithmeticOracle(
        self,
        Module: ModuleIR,
        Case: AdderOracleCase,
        Stage: str,
    ) -> None:
        ExpectedInputs = set(
            BuildAdderAssignment(Case, Left=0, Right=0, CarryIn=0)
        )
        self.assertEqual(set(Module.Inputs), ExpectedInputs)

        Digest = sha256()
        RowCount = 0
        ResultByteCount = (Case.Width + 8) // 8
        for Left in range(1 << Case.Width):
            for Right in range(1 << Case.Width):
                for CarryIn in (0, 1):
                    Assignment = BuildAdderAssignment(
                        Case,
                        Left,
                        Right,
                        CarryIn,
                    )
                    Values = EvaluateLogicModule(Module, Assignment)
                    Actual = DecodeAdderResult(Case, Values)
                    Expected = Left + Right + CarryIn
                    if Actual != Expected:
                        self.fail(
                            f"{Case.ModuleName} {Stage} arithmetic mismatch: "
                            f"{Left} + {Right} + {CarryIn} produced {Actual}, "
                            f"expected {Expected}"
                        )
                    Digest.update(
                        Actual.to_bytes(ResultByteCount, byteorder="little")
                    )
                    RowCount += 1

        self.assertEqual(RowCount, Case.ExpectedRows)
        self.assertEqual(Digest.hexdigest(), Case.ExpectedDigest)

    def AssertAdderPipelineMatchesArithmeticOracle(
        self,
        Case: AdderOracleCase,
    ) -> None:
        Parsed = ParseSvToNetlist(
            InputPath=Case.SourcePath,
            TopModule=Case.ModuleName,
        )
        Optimized = OptimizeLogic(Parsed)
        NandOnly = ToNandOnly(Optimized)
        ValidateNandOnlyDesign(NandOnly)

        for Stage, Netlist in (
            ("parsed", Parsed),
            ("optimized", Optimized),
            ("NAND-only", NandOnly),
        ):
            with self.subTest(Module=Case.ModuleName, Stage=Stage):
                self.AssertModuleMatchesArithmeticOracle(
                    Netlist.Modules[Netlist.Top],
                    Case,
                    Stage,
                )

    def testFullAdderMatchesArithmeticOracle(self) -> None:
        self.AssertAdderPipelineMatchesArithmeticOracle(FullAdderCase)

    def testRippleCarryAdder4MatchesArithmeticOracle(self) -> None:
        self.AssertAdderPipelineMatchesArithmeticOracle(
            RippleCarryAdder4Case
        )

    def testRippleCarryAdder8MatchesArithmeticOracle(self) -> None:
        self.AssertAdderPipelineMatchesArithmeticOracle(
            RippleCarryAdder8Case
        )

    def testCarryLookaheadAdder4MatchesOracleAndNandCheckpoint(self) -> None:
        self.AssertAdderPipelineMatchesArithmeticOracle(
            CarryLookaheadAdder4Case
        )
        Parsed = ParseSvToNetlist(
            InputPath=CarryLookaheadAdder4Case.SourcePath,
            TopModule=CarryLookaheadAdder4Case.ModuleName,
        )
        NandOnly = ToNandOnly(OptimizeLogic(Parsed))
        NandCount = sum(
            Gate.Kind == GateKind.NAND
            for Gate in NandOnly.Modules[NandOnly.Top].Gates
        )
        self.assertEqual(NandCount, 72)

    def testCla4LogicAndTopologyIgnoreNamesAndSafeOrdering(self) -> None:
        Parsed = ParseSvToNetlist(
            InputPath=CarryLookaheadAdder4Case.SourcePath,
            TopModule=CarryLookaheadAdder4Case.ModuleName,
        )
        OriginalModule = Parsed.Modules[Parsed.Top]
        MetamorphicSource = BuildMetamorphicCla4Source(
            OriginalModule,
            CarryLookaheadAdder4Case.SourcePath.read_text(encoding="utf-8"),
        )

        with TemporaryDirectory() as Directory:
            SourcePath = Path(Directory) / "ArithmeticMetamorph.sv"
            SourcePath.write_text(MetamorphicSource, encoding="utf-8")
            Metamorphic = ParseSvToNetlist(
                InputPath=SourcePath,
                TopModule="ArithmeticMetamorph",
            )

        MetamorphicModule = Metamorphic.Modules[Metamorphic.Top]
        self.assertEqual(
            MetamorphicModule.Inputs,
            list(reversed(OriginalModule.Inputs)),
        )
        self.assertEqual(
            MetamorphicModule.Outputs,
            list(reversed(OriginalModule.Outputs)),
        )
        self.assertNotIn("Propagate0", MetamorphicModule.Nets)

        OriginalNandOnly = ToNandOnly(OptimizeLogic(Parsed))
        MetamorphicOptimized = OptimizeLogic(Metamorphic)
        MetamorphicNandOnly = ToNandOnly(MetamorphicOptimized)
        ValidateNandOnlyDesign(MetamorphicNandOnly)
        for Stage, Module in (
            ("metamorphic-parsed", MetamorphicModule),
            (
                "metamorphic-optimized",
                MetamorphicOptimized.Modules[MetamorphicOptimized.Top],
            ),
            (
                "metamorphic-NAND-only",
                MetamorphicNandOnly.Modules[MetamorphicNandOnly.Top],
            ),
        ):
            self.AssertModuleMatchesArithmeticOracle(
                Module,
                CarryLookaheadAdder4Case,
                Stage,
            )

        MetamorphicNandCount = sum(
            Gate.Kind == GateKind.NAND
            for Gate in MetamorphicNandOnly.Modules[
                MetamorphicNandOnly.Top
            ].Gates
        )
        self.assertEqual(MetamorphicNandCount, 72)
        self.assertEqual(
            BuildNameIndependentNandSignature(
                OriginalNandOnly.Modules[OriginalNandOnly.Top]
            ),
            BuildNameIndependentNandSignature(
                MetamorphicNandOnly.Modules[MetamorphicNandOnly.Top]
            ),
        )


if __name__ == "__main__":
    unittest.main()

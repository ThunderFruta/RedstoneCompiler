"""Deterministic randomized truth-equivalence checks for NAND lowering."""

from itertools import product
from pathlib import Path
from random import Random

from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from Compilation.Synthesis.LogicEvaluation import EvaluateLogicModule
from Compilation.Synthesis.NandTransform import ToNandOnly


def BuildRandomAcyclicModule(Seed: int) -> ModuleIR:
    """Build a small name-agnostic acyclic Boolean network."""
    Generator = Random(Seed)
    Inputs = [f"Input{Index}" for Index in range(4)]
    Available = list(Inputs)
    Gates = []
    BinaryKinds = (GateKind.NAND, GateKind.AND, GateKind.OR, GateKind.XOR)
    for Index in range(12):
        Kind = Generator.choice((*BinaryKinds, GateKind.NOT, GateKind.BUFFER))
        InputCount = 1 if Kind in {GateKind.NOT, GateKind.BUFFER} else 2
        GateInputs = [Generator.choice(Available) for _ in range(InputCount)]
        Output = f"Net{Index}"
        Gates.append(Gate(
            Name=f"Gate{Index}",
            Kind=Kind,
            Inputs=GateInputs,
            Outputs=[Output],
        ))
        Available.append(Output)
    return ModuleIR(
        Name=f"Random{Seed}",
        Inputs=Inputs,
        Outputs=[Available[-1]],
        Gates=Gates,
        SourcePath=Path(f"Random{Seed}.sv"),
    )


def test_random_acyclic_logic_matches_nand_lowering() -> None:
    for Seed in range(32):
        SourceModule = BuildRandomAcyclicModule(Seed)
        NandModule = ToNandOnly(NetlistIR(
            Top=SourceModule.Name,
            Modules={SourceModule.Name: SourceModule},
        )).Modules[SourceModule.Name]
        assert {
            GateValue.Kind for GateValue in NandModule.Gates
        } <= {GateKind.INPUT, GateKind.NAND, GateKind.OUTPUT}
        for Values in product((False, True), repeat=len(SourceModule.Inputs)):
            Assignment = dict(zip(SourceModule.Inputs, Values))
            Expected = EvaluateLogicModule(SourceModule, Assignment)[
                SourceModule.Outputs[0]
            ]
            Actual = EvaluateLogicModule(NandModule, Assignment)[
                f"{SourceModule.Outputs[0]}$Output"
            ]
            assert Actual is Expected, (Seed, Assignment)

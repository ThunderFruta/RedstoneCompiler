"""Reference evaluation for synthesized combinational logic."""

from __future__ import annotations

from typing import Any


def EvaluateLogicModule(
    Module: Any,
    Assignment: dict[str, bool],
) -> dict[str, bool]:
    """Evaluate an ideal combinational IR module for synthesis tests."""
    Values = dict(Assignment)
    Pending = list(Module.Gates)
    while Pending:
        Progress = False
        Remaining = []
        for Gate in Pending:
            Kind = Gate.Kind.value
            if Kind == "INPUT":
                for Output in Gate.Outputs:
                    Values[Output] = bool(Assignment[Output])
                Progress = True
                continue
            if any(Input not in Values for Input in Gate.Inputs):
                Remaining.append(Gate)
                continue
            Inputs = [Values[Input] for Input in Gate.Inputs]
            if Kind == "NAND":
                Result = not all(Inputs)
            elif Kind == "AND":
                Result = all(Inputs)
            elif Kind == "OR":
                Result = any(Inputs)
            elif Kind == "XOR":
                Result = sum(Inputs) % 2 == 1
            elif Kind == "NOT":
                Result = not Inputs[0]
            elif Kind in ("BUFFER", "OUTPUT"):
                Result = Inputs[0]
            else:
                raise ValueError(f"Unsupported combinational gate kind {Kind}")
            for Output in Gate.Outputs:
                Values[Output] = Result
            Progress = True
        if not Progress:
            Names = ", ".join(Gate.Name for Gate in Remaining)
            raise ValueError(
                "Combinational evaluation could not resolve gates: " + Names
            )
        Pending = Remaining
    return Values

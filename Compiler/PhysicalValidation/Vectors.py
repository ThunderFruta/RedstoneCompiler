"""Truth-table and Fabric-canary policies shared by physical backends."""

from __future__ import annotations

from hashlib import sha256
import itertools
from typing import Any, Iterable

from Compiler.Synthesis.LogicEvaluation import EvaluateLogicModule


ExhaustiveInputLimit = 20
WideInputSampleCount = 4096


def BuildValidationAssignments(
    InputNames: Iterable[str],
    *,
    ExhaustiveLimit: int = ExhaustiveInputLimit,
) -> list[dict[str, bool]]:
    """Return exhaustive assignments through the configured input limit."""
    Names = tuple(sorted(str(Name) for Name in InputNames))
    if len(Names) <= ExhaustiveLimit:
        return [
            dict(zip(Names, Values))
            for Values in itertools.product((False, True), repeat=len(Names))
        ]
    Values = {
        tuple(False for _ in Names),
        tuple(True for _ in Names),
    }
    for Index in range(len(Names)):
        Values.add(tuple(Index == Other for Other in range(len(Names))))
        Values.add(tuple(Index != Other for Other in range(len(Names))))
    Counter = 0
    TargetCount = 2 + 2 * len(Names) + WideInputSampleCount
    while len(Values) < TargetCount:
        Digest = sha256(f"{Names}:{Counter}".encode("utf-8")).digest()
        Values.add(tuple(
            bool(Digest[Index // 8] & (1 << (Index % 8)))
            for Index in range(len(Names))
        ))
        Counter += 1
    return [dict(zip(Names, Value)) for Value in sorted(Values)]


def BuildExpectedVectors(
    Module: Any,
    InputNames: Iterable[str],
    OutputNames: Iterable[str],
    *,
    IncludeTraceValues: bool = False,
    ExhaustiveLimit: int = ExhaustiveInputLimit,
) -> list[dict[str, object]]:
    """Pair each assignment with outputs from the semantic logic oracle."""
    InputNames = tuple(str(Name) for Name in InputNames)
    OutputNames = tuple(str(Name) for Name in OutputNames)
    Vectors = []
    for Assignment in BuildValidationAssignments(
        InputNames,
        ExhaustiveLimit=ExhaustiveLimit,
    ):
        Values = EvaluateLogicModule(Module, Assignment)
        Vector: dict[str, object] = {
            "Inputs": Assignment,
            "Expected": {Name: bool(Values[Name]) for Name in OutputNames},
        }
        if IncludeTraceValues:
            Vector["ExpectedSignals"] = {
                str(Name): bool(Value)
                for Name, Value in sorted(Values.items())
            }
        Vectors.append(Vector)
    return Vectors


def BuildFabricCanaryVectors(
    Module: Any,
    InputNames: Iterable[str],
    OutputNames: Iterable[str],
) -> list[dict[str, object]]:
    """Build zero, one, one-hot, and one-cold final Fabric checks."""
    Names = tuple(sorted(str(Name) for Name in InputNames))
    Assignments = {
        tuple(False for _ in Names),
        tuple(True for _ in Names),
    }
    for Index in range(len(Names)):
        Assignments.add(tuple(Index == Other for Other in range(len(Names))))
        Assignments.add(tuple(Index != Other for Other in range(len(Names))))
    OutputNames = tuple(str(Name) for Name in OutputNames)
    Result = []
    for Values in sorted(Assignments):
        Assignment = dict(zip(Names, Values))
        Evaluated = EvaluateLogicModule(Module, Assignment)
        Result.append({
            "Inputs": Assignment,
            "Expected": {Name: bool(Evaluated[Name]) for Name in OutputNames},
            "ExpectedSignals": {
                str(Name): bool(Value)
                for Name, Value in sorted(Evaluated.items())
            },
        })
    return Result


def PackAssignment(InputNames: Iterable[str], Assignment: dict[str, bool]) -> int:
    """Pack one named assignment into the native validator's canonical mask."""
    Mask = 0
    for Index, Name in enumerate(sorted(str(Value) for Value in InputNames)):
        if Assignment[Name]:
            Mask |= 1 << Index
    return Mask

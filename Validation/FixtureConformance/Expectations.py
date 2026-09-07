"""Strict expectations parsing, separate from fixture and execution inputs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


class FixtureConfigurationError(ValueError):
    """A fixture case is malformed, rather than physically illegal."""


def UniqueObject(Pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    Result = {}
    for Key, Value in Pairs:
        if Key in Result:
            raise FixtureConfigurationError(f"duplicate-json-key: {Key}")
        Result[Key] = Value
    return Result


def ReadJson(PathValue: Path) -> Any:
    try:
        return json.loads(PathValue.read_text(), object_pairs_hook=UniqueObject,
                          parse_constant=lambda Value: (_ for _ in ()).throw(FixtureConfigurationError(f"nonfinite-json: {Value}")))
    except (OSError, json.JSONDecodeError) as Error:
        raise FixtureConfigurationError(str(Error)) from Error


def Fields(Value: Any, Required: set[str], Optional: set[str] = frozenset()) -> None:
    if not isinstance(Value, dict) or not Required <= set(Value) or set(Value) - Required - Optional:
        raise FixtureConfigurationError(f"invalid-fields: required={sorted(Required)}, optional={sorted(Optional)}")


def Vector(Value: Any, Names: set[str]) -> dict[str, bool]:
    if not isinstance(Value, dict) or set(Value) != Names or any(type(Bit) is not bool for Bit in Value.values()):
        raise FixtureConfigurationError("complete-boolean-vector-required")
    return dict(sorted(Value.items()))


def TickCount(Value: Any, Minimum: int = 0) -> int:
    if type(Value) is not int or not Minimum <= Value <= 100000:
        raise FixtureConfigurationError("tick-count-out-of-range")
    return Value


@dataclass(frozen=True)
class CaseExpectation:
    Id: str
    InitialInputs: dict[str, bool]
    AppliedInputs: dict[str, bool]
    ExpectedOutputs: dict[str, bool]
    SettlementTicks: int
    StabilityWindowTicks: int
    Physical: dict[str, Any] | None
    CheckerStatus: str | None


def PredictionPosition(Value: Any) -> None:
    if not isinstance(Value, list) or len(Value) != 3 or any(type(Axis) is not int for Axis in Value):
        raise FixtureConfigurationError("prediction-position-must-be-three-integers")


def ValidatePhysicalPredictions(Physical: dict[str, Any]) -> None:
    for Field, Values in Physical.items():
        if not isinstance(Values, list):
            raise FixtureConfigurationError("physical-predictions-must-be-lists")
        for Value in Values:
            if Field == "DustConnections":
                if not isinstance(Value, list) or len(Value) != 2:
                    raise FixtureConfigurationError("dust-edge-must-have-two-positions")
                for P in Value:
                    PredictionPosition(P)
            elif Field == "RepeaterDirections":
                Fields(Value, {"Position", "InputFacing", "InputPosition", "OutputPosition"})
                if Value["InputFacing"] not in {"north", "south", "east", "west"}:
                    raise FixtureConfigurationError("invalid-repeater-expectation-facing")
                for Key in ("Position", "InputPosition", "OutputPosition"):
                    PredictionPosition(Value[Key])
            else:
                PredictionPosition(Value)


def ParseExpectations(PathValue: Path, InputNames: set[str], OutputNames: set[str]) -> tuple[CaseExpectation, ...]:
    Document = ReadJson(PathValue)
    Fields(Document, {"schemaVersion", "kind", "cases"})
    if Document["schemaVersion"] != "physical-rules-expectations-v1" or Document["kind"] != "combinational":
        raise FixtureConfigurationError("unsupported-expectation-schema-or-stateful-sequence")
    if not isinstance(Document["cases"], list) or not Document["cases"]:
        raise FixtureConfigurationError("nonempty-cases-required")
    Results = []
    Seen = set()
    PhysicalFields = {"DustConnections", "SupportPositions", "MissingSupport", "HeadroomPositions", "BlockedHeadroom", "RepeaterDirections", "ConflictPositions"}
    for Case in Document["cases"]:
        Fields(Case, {"id", "initialInputs", "appliedInputs", "expectedOutputs", "settlementTicks", "stabilityWindowTicks"}, {"physical", "checkerStatus"})
        if not isinstance(Case["id"], str) or not Case["id"] or Case["id"] in Seen:
            raise FixtureConfigurationError("case-id-must-be-unique-nonempty-string")
        Seen.add(Case["id"])
        Physical = Case.get("physical")
        if "physical" in Case:
            Fields(Physical, set(), PhysicalFields)
            ValidatePhysicalPredictions(Physical)
        Status = Case.get("checkerStatus")
        if "checkerStatus" in Case and Status not in {"Legal", "Illegal", "Unknown"}:
            raise FixtureConfigurationError("invalid-checker-status")
        TickCount(TickCount(Case["settlementTicks"]) + TickCount(Case["stabilityWindowTicks"], 1))
        Results.append(CaseExpectation(
            Case["id"], Vector(Case["initialInputs"], InputNames),
            Vector(Case["appliedInputs"], InputNames), Vector(Case["expectedOutputs"], OutputNames),
            TickCount(Case["settlementTicks"]), TickCount(Case["stabilityWindowTicks"], 1),
            Physical, Status,
        ))
    return tuple(Results)

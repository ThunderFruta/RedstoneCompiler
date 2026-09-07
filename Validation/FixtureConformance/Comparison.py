"""Independent complete-trace settlement and model comparison."""

from __future__ import annotations

from typing import Any

from .Expectations import CaseExpectation


def EvaluateSettlement(
    Samples: list[dict[str, Any]], Expected: dict[str, bool],
    SettlementTicks: int, StabilityWindowTicks: int,
) -> dict[str, Any]:
    """Require the final correct suffix through L+W, with suffix start <= L.

    W counts elapsed intervals, so W+1 equal samples are necessary. All H+1
    observations are required even when an earlier window appeared to pass.
    """
    Horizon = SettlementTicks + StabilityWindowTicks
    if len(Samples) != Horizon + 1:
        return {"Status": "unobserved", "Reason": "incomplete-trace", "ActualSettlementTicks": None}
    Values = []
    for Tick, Sample in enumerate(Samples):
        Outputs = Sample.get("Outputs")
        if type(Sample.get("Tick")) is not int or Sample["Tick"] != Tick or not isinstance(Outputs, dict) or set(Outputs) != set(Expected) or any(type(Bit) is not bool for Bit in Outputs.values()):
            return {"Status": "unobserved", "Reason": "missing-or-invalid-output-sample", "ActualSettlementTicks": None}
        Values.append(Outputs)
    LastChange = max((Index for Index in range(1, len(Values)) if Values[Index] != Values[Index - 1]), default=0)
    FinalCorrect = Values[-1] == Expected
    Actual = LastChange if FinalCorrect else None
    if FinalCorrect:
        Status = "passed" if Actual <= SettlementTicks else "late"
    elif any(Value == Expected for Value in Values):
        Status = "transient"
    elif len({tuple(sorted(Value.items())) for Value in Values}) > 1:
        Status = "oscillating"
    else:
        Status = "wrong"
    return {"Status": Status, "ActualSettlementTicks": Actual, "ObservedThroughTick": Horizon,
            "SampleCount": len(Samples), "StableIntervals": Horizon - LastChange}


def CompareCase(
    Case: CaseExpectation, Prediction: dict[str, Any], Observation: dict[str, Any],
    *, FixtureSha256: str, ExpectedModel: dict[str, Any],
) -> dict[str, Any]:
    if Prediction.get("FixtureSha256") != FixtureSha256 or Observation.get("FixtureSha256") != FixtureSha256 or Prediction.get("Model") != ExpectedModel:
        return {"Status": "identity-mismatch", "Settlement": {"Status": "unobserved", "ActualSettlementTicks": None}, "Mismatches": [], "PhysicalExpectationsSupplied": Case.Physical is not None}
    def ExactInputs(Value: Any, Expected: dict[str, bool]) -> bool:
        return isinstance(Value, dict) and set(Value) == set(Expected) and all(type(Bit) is bool for Bit in Value.values()) and Value == Expected
    Mismatches = []
    if Case.CheckerStatus is not None and Case.CheckerStatus != Prediction["Status"]:
        Mismatches.append({"Field": "Status", "Expected": Case.CheckerStatus, "Actual": Prediction["Status"]})
    for Field, Expected in (Case.Physical or {}).items():
        Actual = Prediction["Physical"].get(Field)
        if Actual != Expected:
            Mismatches.append({"Field": Field, "Expected": Expected, "Actual": Actual})
    if Observation.get("Status") != "observed":
        Settlement = {"Status": Observation.get("Status", "backend-error"), "ActualSettlementTicks": None}
    elif Observation.get("FreshWorld") is not True or Observation.get("FreshCompiler") is not True:
        Settlement = {"Status": "unobserved", "Reason": "fresh-case-receipt-missing", "ActualSettlementTicks": None}
    elif not ExactInputs(Observation.get("InitialInputs"), Case.InitialInputs) or not ExactInputs(Observation.get("AppliedInputs"), Case.AppliedInputs):
        Settlement = {"Status": "unobserved", "Reason": "input-readback-mismatch", "ActualSettlementTicks": None}
    else:
        Settlement = EvaluateSettlement(Observation.get("Samples", []), Case.ExpectedOutputs, Case.SettlementTicks, Case.StabilityWindowTicks)
    RequiredRoots = Prediction["Electrical"].get("RequiredRootPower")
    if Settlement["Status"] == "passed":
        Start = Settlement["ActualSettlementTicks"]
        for Sample in Observation["Samples"]:
            if not ExactInputs(Sample.get("Inputs"), Case.AppliedInputs):
                Settlement = {"Status": "unobserved", "Reason": "per-tick-input-readback-mismatch", "ActualSettlementTicks": None}
                break
            if RequiredRoots is not None and Sample["Tick"] >= Start:
                ActualRoots = Sample.get("RootPower")
                if not isinstance(ActualRoots, dict) or set(ActualRoots) != set(RequiredRoots) or any(type(Power) is not int for Power in ActualRoots.values()):
                    Settlement = {"Status": "unobserved", "Reason": "missing-root-observation", "ActualSettlementTicks": None}
                    break
                if ActualRoots != RequiredRoots:
                    Settlement = {"Status": "assumption-mismatch", "Reason": "observed-root-power-disagrees", "ActualSettlementTicks": None}
                    break
    Electrical = Prediction["Electrical"]["Outputs"]
    if Electrical.get("Status") == "Available" and not ExactInputs(Electrical.get("Values"), Case.ExpectedOutputs):
        Mismatches.append({"Field": "Electrical.Outputs", "Expected": Case.ExpectedOutputs, "Actual": Electrical["Values"]})
    if Mismatches:
        Status = "mismatch"
    elif Settlement["Status"] != "passed":
        Status = Settlement["Status"]
    elif Prediction["Status"] == "Unknown" or Electrical.get("Status") != "Available":
        Status = "unknown"
    elif Prediction["Status"] == "Illegal":
        Status = "illegal"
    else:
        Status = "passed"
    return {"Status": Status, "Settlement": Settlement, "Mismatches": Mismatches,
            "PhysicalExpectationsSupplied": Case.Physical is not None}

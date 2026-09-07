"""Independent configuration and timing challenges for declarative fixtures."""

from copy import deepcopy
import json

import pytest

from Validation.FixtureConformance.Comparison import CompareCase, EvaluateSettlement
from Validation.FixtureConformance.Expectations import CaseExpectation, FixtureConfigurationError, ParseExpectations


def WriteCases(TmpPath, **Updates):
    Case = {"id": "mixed", "initialInputs": {"A": True, "B": False},
            "appliedInputs": {"A": False, "B": True}, "expectedOutputs": {"Y": False},
            "settlementTicks": 2, "stabilityWindowTicks": 2}
    Case.update(Updates)
    P = TmpPath / "Expectations.json"
    P.write_text(json.dumps({"schemaVersion": "physical-rules-expectations-v1", "kind": "combinational", "cases": [Case]}))
    return P


@pytest.mark.parametrize("Field", ["initialInputs", "appliedInputs"])
@pytest.mark.parametrize("Vector", [{"A": True}, {"A": True, "B": False, "C": False}, {"A": 1, "B": False}, {}])
def test_each_control_vector_requires_exact_boolean_domain(tmp_path, Field, Vector):
    with pytest.raises(FixtureConfigurationError):
        ParseExpectations(WriteCases(tmp_path, **{Field: Vector}), {"A", "B"}, {"Y"})


@pytest.mark.parametrize("Field,Value", [("settlementTicks", True), ("settlementTicks", -1), ("settlementTicks", 1.0), ("stabilityWindowTicks", False), ("stabilityWindowTicks", 0)])
def test_timing_counts_intervals_without_coercion(tmp_path, Field, Value):
    with pytest.raises(FixtureConfigurationError):
        ParseExpectations(WriteCases(tmp_path, **{Field: Value}), {"A", "B"}, {"Y"})


def test_omitted_and_explicit_empty_physical_assertions_are_distinct(tmp_path):
    Omitted = ParseExpectations(WriteCases(tmp_path), {"A", "B"}, {"Y"})[0]
    Empty = ParseExpectations(WriteCases(tmp_path, physical={"DustConnections": []}), {"A", "B"}, {"Y"})[0]
    assert Omitted.Physical is None
    assert Empty.Physical == {"DustConnections": []}
    with pytest.raises(FixtureConfigurationError):
        ParseExpectations(WriteCases(tmp_path, physical=None), {"A", "B"}, {"Y"})


def test_duplicate_keys_and_stateful_cases_are_configuration_errors(tmp_path):
    P = WriteCases(tmp_path)
    P.write_text(P.read_text().replace('"settlementTicks": 2', '"settlementTicks": 2, "settlementTicks": 5'))
    with pytest.raises(FixtureConfigurationError):
        ParseExpectations(P, {"A", "B"}, {"Y"})
    P = WriteCases(tmp_path)
    P.write_text(P.read_text().replace('"combinational"', '"stateful"'))
    with pytest.raises(FixtureConfigurationError):
        ParseExpectations(P, {"A", "B"}, {"Y"})


@pytest.mark.parametrize("Values,Status,Actual", [
    ([True, True, True, True, True], "passed", 0),
    ([False, False, True, True, True], "passed", 2),
    ([False, False, False, True, True], "late", 3),
    ([True, True, True, False, True], "late", 4),
    ([True, True, True, False, False], "transient", None),
    ([False, False, False, False, False], "wrong", None),
])
def test_final_suffix_controls_settlement_not_first_match(Values, Status, Actual):
    Result = EvaluateSettlement([{"Tick": I, "Outputs": {"Y": V}} for I, V in enumerate(Values)], {"Y": True}, 2, 2)
    assert (Result["Status"], Result["ActualSettlementTicks"]) == (Status, Actual)


def test_missing_duplicate_and_nonboolean_observations_cannot_pass():
    Trace = [{"Tick": I, "Outputs": {"Y": True}} for I in range(3)]
    for Mutation in (Trace[:-1], [Trace[0], Trace[0], Trace[2]], [{"Tick": I, "Outputs": {"Y": 1}} for I in range(3)]):
        assert EvaluateSettlement(Mutation, {"Y": True}, 0, 2)["Status"] == "unobserved"
    Oscillation = [{"Tick": I, "Outputs": {"Y": bool(I % 2), "Z": False}} for I in range(5)]
    assert EvaluateSettlement(Oscillation, {"Y": True, "Z": True}, 2, 2)["Status"] == "oscillating"


def test_unknown_prediction_and_missing_root_proof_cannot_be_native_success():
    Case = CaseExpectation("case", {"A": False}, {"A": True}, {"Y": True}, 0, 2, None, None)
    Prediction = {"FixtureSha256": "fixture-a", "Model": {"Fingerprint": "model-a"}, "Status": "Unknown", "Physical": {}, "Electrical": {"Outputs": {"Status": "Unknown"}}}
    Observation = {"FixtureSha256": "fixture-a", "Status": "observed", "FreshWorld": True, "FreshCompiler": True, "InitialInputs": {"A": False}, "AppliedInputs": {"A": True},
                   "Samples": [{"Tick": I, "Outputs": {"Y": True}, "Inputs": {"A": True}} for I in range(3)]}
    assert CompareCase(Case, Prediction, Observation, FixtureSha256="fixture-a", ExpectedModel={"Fingerprint": "model-a"})["Status"] == "unknown"
    assert CompareCase(Case, Prediction, {"Status": "unsupported", "FixtureSha256": "fixture-a"}, FixtureSha256="fixture-a", ExpectedModel={"Fingerprint": "model-a"})["Status"] == "unsupported"
    Prediction.update(Status="Legal", Electrical={"Outputs": {"Status": "Available", "Values": {"Y": True}}, "RequiredRootPower": {"A": 15}})
    assert CompareCase(Case, Prediction, Observation, FixtureSha256="fixture-a", ExpectedModel={"Fingerprint": "model-a"})["Status"] == "unobserved"
    WrongRoot = deepcopy(Observation)
    for S in WrongRoot["Samples"]:
        S["RootPower"] = {"A": 0}
    assert CompareCase(Case, Prediction, WrongRoot, FixtureSha256="fixture-a", ExpectedModel={"Fingerprint": "model-a"})["Status"] == "assumption-mismatch"


@pytest.mark.parametrize("Mutation", ["prediction-fixture", "observation-fixture", "model", "initial-int", "applied-int", "tick-input-int"])
def test_comparator_rejects_switched_identity_and_integer_readback(Mutation):
    Case = CaseExpectation("case", {"A": False}, {"A": True}, {"Y": True}, 0, 1, None, None)
    Prediction = {"FixtureSha256": "fixture-a", "Model": {"Fingerprint": "model-a"}, "Status": "Legal", "Physical": {}, "Electrical": {"Outputs": {"Status": "Available", "Values": {"Y": True}}}}
    Observation = {"FixtureSha256": "fixture-a", "Status": "observed", "FreshWorld": True, "FreshCompiler": True, "InitialInputs": {"A": False}, "AppliedInputs": {"A": True}, "Samples": [{"Tick": I, "Inputs": {"A": True}, "Outputs": {"Y": True}} for I in range(2)]}
    if Mutation == "prediction-fixture":
        Prediction["FixtureSha256"] = "fixture-b"
    elif Mutation == "observation-fixture":
        Observation["FixtureSha256"] = "fixture-b"
    elif Mutation == "model":
        Prediction["Model"]["Fingerprint"] = "model-b"
    elif Mutation == "initial-int":
        Observation["InitialInputs"]["A"] = 0
    elif Mutation == "applied-int":
        Observation["AppliedInputs"]["A"] = 1
    else:
        Observation["Samples"][1]["Inputs"]["A"] = 1
    Result = CompareCase(Case, Prediction, Observation, FixtureSha256="fixture-a", ExpectedModel={"Fingerprint": "model-a"})
    assert Result["Status"] == ("identity-mismatch" if Mutation in {"prediction-fixture", "observation-fixture", "model"} else "unobserved")

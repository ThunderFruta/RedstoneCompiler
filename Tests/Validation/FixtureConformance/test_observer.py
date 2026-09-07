"""Real native traces and expectation-mutation challenges; no fake adapter."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import RedstoneCompiler.RustRouting as Native

from PhysicalDesign.Redstone.FixtureValidation import CheckPhysicalFixture, FixtureIdentity, ModelIdentity
from Validation.FixtureConformance.Comparison import CompareCase
from Validation.FixtureConformance.Expectations import ParseExpectations
from Validation.FixtureConformance.Loaders import LoadFixture, LoadedFixture
from Validation.FixtureConformance.Observation import ObserveCase


Root = Path(__file__).resolve().parents[2] / "Fixtures" / "PhysicalRulesBatch1"


def test_mixed_baselines_and_reversed_order_have_identical_raw_traces():
    Fixture = LoadFixture(Root / "Directional" / "Fixture.json")
    Cases = ParseExpectations(Root / "Directional" / "Expectations.json", {"A", "B"}, {"YA", "YB"})
    Seen = {}
    for Case in (*Cases, *reversed(Cases)):
        Observation = ObserveCase(Fixture, Case.InitialInputs, Case.AppliedInputs, Case.SettlementTicks + Case.StabilityWindowTicks)
        assert Observation["Status"] == "observed", Observation
        assert Observation["FreshWorld"] is True and Observation["FreshCompiler"] is True
        assert Observation["InitialInputs"] == Case.InitialInputs
        assert Observation["AppliedInputs"] == Case.AppliedInputs
        assert Observation["CompilerOptions"] == {"io_only": False, "optimize": False}
        Prediction = CheckPhysicalFixture(Fixture.Document, ExpectedFixtureSha256=Fixture.Sha256, InputVector=Case.AppliedInputs)
        assert CompareCase(Case, Prediction, Observation, FixtureSha256=Fixture.Sha256, ExpectedModel=ModelIdentity())["Status"] == "passed", Observation
        if Case.Id in Seen:
            assert Seen[Case.Id] == Observation["Samples"]
        Seen[Case.Id] = Observation["Samples"]


def test_expectation_only_mutation_changes_comparison_not_executor_results():
    Fixture = LoadFixture(Root / "Identity" / "Fixture.json")
    Case = ParseExpectations(Root / "Identity" / "Expectations.json", {"A"}, {"Y"})[0]
    Mutated = replace(Case, ExpectedOutputs={"Y": False}, Physical={"DustConnections": []})
    Results = []
    for Current in (Case, Mutated):
        Prediction = CheckPhysicalFixture(Fixture.Document, ExpectedFixtureSha256=Fixture.Sha256, InputVector=Current.AppliedInputs)
        Observation = ObserveCase(Fixture, Current.InitialInputs, Current.AppliedInputs, 7)
        Results.append((Prediction, Observation, CompareCase(Current, Prediction, Observation, FixtureSha256=Fixture.Sha256, ExpectedModel=ModelIdentity())))
    assert Results[0][0] == Results[1][0]
    assert Results[0][1]["Samples"] == Results[1][1]["Samples"]
    assert Results[0][2]["Status"] == "passed"
    assert Results[1][2]["Status"] == "mismatch"


def test_supported_stair_cases_keep_native_trace_separate_from_expectations():
    """Exercise the exact clear stair with the real native observer per case."""
    Fixture = LoadFixture(Root / "SupportedStair" / "Fixture.json")
    Cases = ParseExpectations(Root / "SupportedStair" / "Expectations.json", {"A"}, {"Y"})
    Results = {}
    for Case in Cases:
        Prediction = CheckPhysicalFixture(
            Fixture.Document,
            ExpectedFixtureSha256=Fixture.Sha256,
            InputVector=Case.AppliedInputs,
        )
        Observation = ObserveCase(
            Fixture,
            Case.InitialInputs,
            Case.AppliedInputs,
            Case.SettlementTicks + Case.StabilityWindowTicks,
        )
        Result = CompareCase(
            Case,
            Prediction,
            Observation,
            FixtureSha256=Fixture.Sha256,
            ExpectedModel=ModelIdentity(),
        )
        assert Observation["Status"] == "observed", Observation
        assert Observation["FreshWorld"] is True
        assert Observation["FreshCompiler"] is True
        assert Observation["CompilerOptions"] == {"io_only": False, "optimize": False}
        assert Observation["InitialInputs"] == Case.InitialInputs
        assert Observation["AppliedInputs"] == Case.AppliedInputs
        assert Observation["HorizonTicks"] == 7
        assert [Sample["Tick"] for Sample in Observation["Samples"]] == list(range(8))
        assert all(Sample["Inputs"] == Case.AppliedInputs for Sample in Observation["Samples"])
        assert all(Sample["RootPower"] == {"A": 15 if Case.AppliedInputs["A"] else 0} for Sample in Observation["Samples"])
        assert Result["Status"] == "passed", {"Observation": Observation, "Result": Result}
        Results[Case.Id] = (Prediction, Observation, Result)

    Case = Cases[0]
    Mutated = replace(
        Case,
        ExpectedOutputs={"Y": False},
        Physical={"DustConnections": []},
    )
    Prediction = CheckPhysicalFixture(
        Fixture.Document,
        ExpectedFixtureSha256=Fixture.Sha256,
        InputVector=Mutated.AppliedInputs,
    )
    Observation = ObserveCase(
        Fixture,
        Mutated.InitialInputs,
        Mutated.AppliedInputs,
        Mutated.SettlementTicks + Mutated.StabilityWindowTicks,
    )
    assert Prediction == Results[Case.Id][0]
    assert Observation["Samples"] == Results[Case.Id][1]["Samples"]
    assert CompareCase(
        Mutated,
        Prediction,
        Observation,
        FixtureSha256=Fixture.Sha256,
        ExpectedModel=ModelIdentity(),
    )["Status"] == "mismatch"


def test_blocked_headroom_stair_cases_are_real_all_stone_native_receipts():
    """The blocked edge remains absent while native Y stays false for both vectors."""
    Fixture = LoadFixture(Root / "BlockedHeadroomStair" / "Fixture.json")
    Cases = ParseExpectations(Root / "BlockedHeadroomStair" / "Expectations.json", {"A"}, {"Y"})
    Results = {}
    for Case in Cases:
        Prediction = CheckPhysicalFixture(
            Fixture.Document,
            ExpectedFixtureSha256=Fixture.Sha256,
            InputVector=Case.AppliedInputs,
        )
        Observation = ObserveCase(
            Fixture,
            Case.InitialInputs,
            Case.AppliedInputs,
            Case.SettlementTicks + Case.StabilityWindowTicks,
        )
        Comparison = CompareCase(
            Case,
            Prediction,
            Observation,
            FixtureSha256=Fixture.Sha256,
            ExpectedModel=ModelIdentity(),
        )
        assert Observation["Status"] == "observed", Observation
        assert Observation["Backend"] == "mchprs-redpiler-fe217210"
        assert Observation["FreshWorld"] is True
        assert Observation["FreshCompiler"] is True
        assert Observation["CompilerOptions"] == {"io_only": False, "optimize": False}
        assert Observation["InitialInputs"] == Case.InitialInputs
        assert Observation["AppliedInputs"] == Case.AppliedInputs
        assert Observation["HorizonTicks"] == 7
        assert [Sample["Tick"] for Sample in Observation["Samples"]] == list(range(8))
        assert all(Sample["Inputs"] == Case.AppliedInputs for Sample in Observation["Samples"])
        assert all(Sample["Outputs"] == {"Y": False} for Sample in Observation["Samples"])
        assert all(Sample["RootPower"] == {"A": 15 if Case.AppliedInputs["A"] else 0} for Sample in Observation["Samples"])
        assert all("UpperPower" not in Sample and "UpperAnalogPower" not in Sample for Sample in Observation["Samples"])
        assert Observation["FixtureSha256"] == Fixture.Sha256
        NativePath = Path(Native.__file__).resolve()
        ExpectedNativeSha256 = sha256(NativePath.read_bytes()).hexdigest()
        ReportedNative = Observation["Native"]
        assert Path(ReportedNative["Path"]).resolve() == NativePath, ReportedNative
        assert ReportedNative["Sha256"] == ExpectedNativeSha256, ReportedNative
        assert Prediction["Status"] == "Legal"
        assert Prediction["Physical"]["DustConnections"] == []
        assert Comparison["Status"] == "passed", {"Observation": Observation, "Comparison": Comparison}
        Results[Case.Id] = (Prediction, Observation)

    Case = Cases[0]
    Mutated = replace(Case, ExpectedOutputs={"Y": True})
    Prediction = CheckPhysicalFixture(
        Fixture.Document,
        ExpectedFixtureSha256=Fixture.Sha256,
        InputVector=Mutated.AppliedInputs,
    )
    Observation = ObserveCase(
        Fixture,
        Mutated.InitialInputs,
        Mutated.AppliedInputs,
        Mutated.SettlementTicks + Mutated.StabilityWindowTicks,
    )
    assert Prediction == Results[Case.Id][0]
    assert Observation == Results[Case.Id][1]
    assert CompareCase(
        Mutated,
        Prediction,
        Observation,
        FixtureSha256=Fixture.Sha256,
        ExpectedModel=ModelIdentity(),
    )["Status"] == "mismatch"


def test_wrong_excitation_and_unsupported_observation_do_not_pass():
    Fixture = LoadFixture(Root / "Identity" / "Fixture.json")
    Case = ParseExpectations(Root / "Identity" / "Expectations.json", {"A"}, {"Y"})[0]
    Wrong = deepcopy(Fixture.Document)
    # Dust one step farther along the route receives 14, not the declared root 15.
    Wrong["RouteInputs"]["A"] = [2, 1, 0]
    WrongFixture = LoadedFixture(Wrong, FixtureIdentity(Wrong), Fixture.SourceHashes)
    Prediction = CheckPhysicalFixture(Wrong, ExpectedFixtureSha256=WrongFixture.Sha256, InputVector=Case.AppliedInputs)
    Observation = ObserveCase(WrongFixture, Case.InitialInputs, Case.AppliedInputs, 7)
    assert Observation["Status"] == "observed"
    assert Observation["Samples"][-1]["Outputs"] == {"Y": True}
    assert CompareCase(Case, Prediction, Observation, FixtureSha256=WrongFixture.Sha256, ExpectedModel=ModelIdentity())["Status"] == "assumption-mismatch"
    Unsupported = deepcopy(Fixture.Document)
    Unsupported["Outputs"][0]["Position"] = [2, 0, 0]
    UnsupportedFixture = LoadedFixture(Unsupported, FixtureIdentity(Unsupported), Fixture.SourceHashes)
    assert ObserveCase(UnsupportedFixture, Case.InitialInputs, Case.AppliedInputs, 7)["Status"] == "unsupported"


def test_native_rejects_invalid_repeater_delays_even_when_outputs_are_off():
    Fixture = LoadFixture(Root / "Directional" / "Fixture.json")
    for Delay in ("0", "5", "255"):
        Document = deepcopy(Fixture.Document)
        next(B for B in Document["Blocks"] if B["State"]["Name"] == "minecraft:repeater")["State"]["Properties"]["delay"] = Delay
        Changed = LoadedFixture(Document, FixtureIdentity(Document), Fixture.SourceHashes)
        Observation = ObserveCase(Changed, {"A": False, "B": False}, {"A": False, "B": False}, 2)
        assert Observation["Status"] == "unsupported", Observation
        assert "Samples" not in Observation


def test_dust_signal_strength_boundary_cases_are_raw_native_receipts_and_expectation_isolated():
    """Run the literal F15/F16 pair without passing expected values to either executor."""
    ExpectedCases = {
        ("DustSignalStrengthAtLimit", "off-to-on"): (True, 15),
        ("DustSignalStrengthAtLimit", "on-to-off"): (False, 0),
        ("DustSignalStrengthBeyondLimit", "off-to-on"): (False, 15),
        ("DustSignalStrengthBeyondLimit", "on-to-off"): (False, 0),
    }
    Results = {}
    for FixtureName, Endpoint in (("DustSignalStrengthAtLimit", 15), ("DustSignalStrengthBeyondLimit", 16)):
        Fixture = LoadFixture(Root / FixtureName / "Fixture.json")
        Cases = ParseExpectations(Root / FixtureName / "Expectations.json", {"A"}, {"Y"})
        assert {Case.Id for Case in Cases} == {"off-to-on", "on-to-off"}
        for Case in Cases:
            ExpectedOutput, ExpectedRootPower = ExpectedCases[(FixtureName, Case.Id)]
            Prediction = CheckPhysicalFixture(
                Fixture.Document,
                ExpectedFixtureSha256=Fixture.Sha256,
                InputVector=Case.AppliedInputs,
            )
            Observation = ObserveCase(
                Fixture,
                Case.InitialInputs,
                Case.AppliedInputs,
                Case.SettlementTicks + Case.StabilityWindowTicks,
            )
            Comparison = CompareCase(
                Case,
                Prediction,
                Observation,
                FixtureSha256=Fixture.Sha256,
                ExpectedModel=ModelIdentity(),
            )
            assert Observation["Status"] == "observed", Observation
            assert Observation["FreshWorld"] is True and Observation["FreshCompiler"] is True
            assert Observation["CompilerOptions"] == {"io_only": False, "optimize": False}
            assert Observation["InitialInputs"] == Case.InitialInputs
            assert Observation["AppliedInputs"] == Case.AppliedInputs
            assert Observation["HorizonTicks"] == 7
            assert [Sample["Tick"] for Sample in Observation["Samples"]] == list(range(8))
            assert all(Sample["Inputs"] == Case.AppliedInputs for Sample in Observation["Samples"])
            assert all(Sample["Outputs"] == {"Y": ExpectedOutput} for Sample in Observation["Samples"])
            assert all(Sample["RootPower"] == {"A": ExpectedRootPower} for Sample in Observation["Samples"])
            assert all("UpperPower" not in Sample and "UpperAnalogPower" not in Sample for Sample in Observation["Samples"])
            NativePath = Path(Native.__file__).resolve()
            ExpectedNativeSha256 = sha256(NativePath.read_bytes()).hexdigest()
            assert Observation["Native"] == {"Path": str(NativePath), "Sha256": ExpectedNativeSha256}
            assert Prediction["Status"] == "Legal"
            assert Prediction["Electrical"]["RequiredRootPower"] == {"A": ExpectedRootPower}
            assert len(Prediction["Physical"]["DustConnections"]) == 2 * (Endpoint - 1)
            assert Comparison["Status"] == "passed", {"Observation": Observation, "Comparison": Comparison}
            Results[(FixtureName, Case.Id)] = (Fixture, Case, Prediction, Observation)

    for FixtureName, CaseId in (("DustSignalStrengthAtLimit", "off-to-on"), ("DustSignalStrengthBeyondLimit", "off-to-on")):
        Fixture, Case, Prediction, Observation = Results[(FixtureName, CaseId)]
        Mutated = replace(Case, ExpectedOutputs={"Y": not Case.ExpectedOutputs["Y"]}, Physical={"DustConnections": []})
        MutatedPrediction = CheckPhysicalFixture(
            Fixture.Document,
            ExpectedFixtureSha256=Fixture.Sha256,
            InputVector=Mutated.AppliedInputs,
        )
        MutatedObservation = ObserveCase(
            Fixture,
            Mutated.InitialInputs,
            Mutated.AppliedInputs,
            Mutated.SettlementTicks + Mutated.StabilityWindowTicks,
        )
        assert MutatedPrediction == Prediction
        assert MutatedObservation["Samples"] == Observation["Samples"]
        assert CompareCase(
            Mutated,
            MutatedPrediction,
            MutatedObservation,
            FixtureSha256=Fixture.Sha256,
            ExpectedModel=ModelIdentity(),
        )["Status"] == "mismatch"

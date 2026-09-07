"""Public checker identity, supported direction, and missing-rule challenges."""

from copy import deepcopy
from collections import Counter
from pathlib import Path

import pytest

from PhysicalDesign.Redstone.FixtureValidation import CheckPhysicalFixture, FixtureIdentity
from Validation.FixtureConformance.Loaders import LoadFixture


FixtureRoot = Path(__file__).resolve().parents[2] / "Fixtures" / "PhysicalRulesBatch1"


def test_public_checker_proves_opposite_route_directions_with_complete_identity():
    Fixture = LoadFixture(FixtureRoot / "Directional" / "Fixture.json")
    Result = CheckPhysicalFixture(Fixture.Document, ExpectedFixtureSha256=Fixture.Sha256, InputVector={"A": True, "B": True})
    assert Result["Status"] == "Legal"
    assert Result["Electrical"]["Outputs"]["Values"] == {"YA": True, "YB": False}
    assert Result["Electrical"]["RequiredRootPower"] == {"A": 15, "B": 15}
    assert Result["Electrical"]["SettlementTicks"]["Status"] == "Unknown"
    assert Result["FixtureSha256"] == Fixture.Sha256
    assert Result["Model"]["TechnologyVersion"] == "redstone-routing-v1"
    assert [2, 0, 0] in Result["CheckedPositions"] or (2, 0, 0) in Result["CheckedPositions"]


def test_stale_blocks_and_incomplete_vector_reject_before_prediction():
    Fixture = LoadFixture(FixtureRoot / "Identity" / "Fixture.json")
    Changed = deepcopy(Fixture.Document)
    Changed["Blocks"].pop()
    with pytest.raises(ValueError, match="stale-fixture"):
        CheckPhysicalFixture(Changed, ExpectedFixtureSha256=Fixture.Sha256, InputVector={"A": True})
    with pytest.raises(ValueError, match="input-vector"):
        CheckPhysicalFixture(Fixture.Document, ExpectedFixtureSha256=Fixture.Sha256, InputVector={})


@pytest.mark.parametrize(
    ("InputVector", "ExpectedOutput", "ExpectedRootPower"),
    [
        ({"A": False}, False, 0),
        ({"A": True}, True, 15),
    ],
)
def test_supported_stair_fixture_is_legal_with_literal_clear_route_claims(
    InputVector, ExpectedOutput, ExpectedRootPower,
):
    """Check the literal +X/+Y stair without loading its expected outcomes."""
    Fixture = LoadFixture(FixtureRoot / "SupportedStair" / "Fixture.json")
    States = {tuple(Block["Position"]): Block["State"] for Block in Fixture.Document["Blocks"]}
    assert set(States) == {
        (0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 0), (2, 1, 0), (2, 2, 0),
    }
    assert (1, 2, 0) not in States
    assert States[(1, 1, 0)] == {
        "Name": "minecraft:redstone_wire",
        "Properties": {"power": "0", "east": "up", "west": "side", "north": "none", "south": "none"},
    }
    assert States[(2, 2, 0)] == {
        "Name": "minecraft:redstone_wire",
        "Properties": {"power": "0", "east": "none", "west": "side", "north": "none", "south": "none"},
    }

    Result = CheckPhysicalFixture(
        Fixture.Document,
        ExpectedFixtureSha256=Fixture.Sha256,
        InputVector=InputVector,
    )

    assert Result["Status"] == "Legal"
    assert Result["Electrical"]["Outputs"]["Values"] == {"Y": ExpectedOutput}
    assert Result["Electrical"]["RequiredRootPower"] == {"A": ExpectedRootPower}
    assert Result["Physical"]["DustConnections"] == [
        [[1, 1, 0], [2, 2, 0]],
        [[2, 2, 0], [1, 1, 0]],
    ]
    assert Result["Physical"]["SupportPositions"] == [[1, 0, 0], [2, 1, 0]]
    assert Result["Physical"]["HeadroomPositions"] == [[1, 2, 0]]
    assert Result["Physical"]["MissingSupport"] == []
    assert Result["Physical"]["BlockedHeadroom"] == []
    assert Result["Physical"]["ConflictPositions"] == []
    assert Result["Physical"]["RepeaterDirections"] == []


@pytest.mark.parametrize(
    ("InputVector", "ExpectedRootPower"),
    [
        ({"A": False}, 0),
        ({"A": True}, 15),
    ],
)
def test_blocked_headroom_stair_is_staticly_legal_but_has_no_route_claim(
    InputVector, ExpectedRootPower,
):
    """Prove the literal all-stone N0 boundary without reading expectations."""
    Fixture = LoadFixture(FixtureRoot / "BlockedHeadroomStair" / "Fixture.json")
    assert Counter(Block["State"]["Name"] for Block in Fixture.Document["Blocks"]) == {
        "minecraft:stone": 4,
        "minecraft:lever": 1,
        "minecraft:redstone_wire": 2,
    }
    assert {
        tuple(Block["Position"]): Block["State"]
        for Block in Fixture.Document["Blocks"]
    } == {
        (0, 0, 0): {"Name": "minecraft:stone"},
        (0, 1, 0): {
            "Name": "minecraft:lever",
            "Properties": {"face": "floor", "facing": "north", "powered": "false"},
        },
        (1, 0, 0): {"Name": "minecraft:stone"},
        (1, 1, 0): {
            "Name": "minecraft:redstone_wire",
            "Properties": {"power": "0", "east": "none", "west": "side", "north": "none", "south": "none"},
        },
        (1, 2, 0): {"Name": "minecraft:stone"},
        (2, 1, 0): {"Name": "minecraft:stone"},
        (2, 2, 0): {
            "Name": "minecraft:redstone_wire",
            "Properties": {"power": "0", "east": "none", "west": "none", "north": "none", "south": "none"},
        },
    }

    Result = CheckPhysicalFixture(
        Fixture.Document,
        ExpectedFixtureSha256=Fixture.Sha256,
        InputVector=InputVector,
    )

    assert Result["Status"] == "Legal"
    assert Result["Electrical"]["InputVector"] == InputVector
    assert Result["Electrical"]["Outputs"] == {
        "Status": "Available",
        "Values": {"Y": False},
        "Scope": "conditional-powered-route-transfer",
    }
    assert Result["Electrical"]["RequiredRootPower"] == {"A": ExpectedRootPower}
    assert Result["Physical"]["DustConnections"] == []
    assert Result["Physical"]["SupportPositions"] == [[1, 0, 0], [2, 1, 0]]
    assert Result["Physical"]["HeadroomPositions"] == [[1, 2, 0]]
    assert Result["Physical"]["BlockedHeadroom"] == [[1, 2, 0]]
    assert Result["Physical"]["MissingSupport"] == []
    assert Result["Physical"]["ConflictPositions"] == []
    assert Result["Physical"]["RepeaterDirections"] == []
    Decisions = [
        Decision
        for Decision in Result["Physical"]["DustStairDecisions"]
        if Decision["LowerPosition"] == [1, 1, 0]
        and Decision["UpperPosition"] == [2, 2, 0]
    ]
    assert len(Decisions) == 1
    Decision = Decisions[0]
    assert {
        Key: Decision[Key]
        for Key in (
            "QueryVersion", "DecisionVersion", "LowerPosition", "UpperPosition",
            "LowerSupportPosition", "UpperSupportPosition", "HeadroomPosition",
            "GeometryStatus", "HeadroomClassification", "RouteClaimStatus",
            "ReasonCode", "ClaimPositions",
        )
    } == {
        "QueryVersion": "dust-stair-query-v1",
        "DecisionVersion": "dust-stair-decision-v1",
        "LowerPosition": [1, 1, 0],
        "UpperPosition": [2, 2, 0],
        "LowerSupportPosition": [1, 0, 0],
        "UpperSupportPosition": [2, 1, 0],
        "HeadroomPosition": [1, 2, 0],
        "GeometryStatus": "Blocked",
        "HeadroomClassification": "Solid",
        "RouteClaimStatus": "Illegal",
        "ReasonCode": "solid-headroom",
        "ClaimPositions": None,
    }
    Normalized = Decision["NormalizedInput"]
    assert Normalized["Context"] == {
        "ElectricalCompatibility": None,
        "HeadroomOwnerSignal": None,
        "RouteSignal": None,
        "SameNetIdentity": None,
        "SupportMode": "Existing",
    }
    assert Normalized["BlockStates"]["1,2,0"] == {
        "Present": True, "Valid": True, "RawState": {"Name": "minecraft:stone"},
    }
    assert Normalized["Membership"]["1,2,0"] == {
        "Actual": True, "Dust": False, "Electrical": False, "Solid": True, "Support": True,
    }
    Rechecked = CheckPhysicalFixture(
        Fixture.Document,
        ExpectedFixtureSha256=Fixture.Sha256,
        InputVector=InputVector,
    )
    RecheckedDecision = next(
        Candidate
        for Candidate in Rechecked["Physical"]["DustStairDecisions"]
        if Candidate["LowerPosition"] == [1, 1, 0]
        and Candidate["UpperPosition"] == [2, 2, 0]
    )
    assert Decision["InputIdentity"] == RecheckedDecision["InputIdentity"]
    assert Normalized == RecheckedDecision["NormalizedInput"]


def test_missing_support_is_illegal_and_torch_truth_is_explicitly_unknown():
    Fixture = LoadFixture(FixtureRoot / "Identity" / "Fixture.json")
    Missing = deepcopy(Fixture.Document)
    Missing["Blocks"] = [B for B in Missing["Blocks"] if B["Position"] != [1, 0, 0]]
    Result = CheckPhysicalFixture(Missing, ExpectedFixtureSha256=FixtureIdentity(Missing), InputVector={"A": True})
    assert Result["Status"] == "Illegal"
    assert Result["Physical"]["MissingSupport"] == [[1, 0, 0]]
    Torch = deepcopy(Fixture.Document)
    Torch["Blocks"].append({"Position": [8, 1, 0], "State": {"Name": "minecraft:redstone_wall_torch", "Properties": {"facing": "east", "lit": "true"}}})
    Result = CheckPhysicalFixture(Torch, ExpectedFixtureSha256=FixtureIdentity(Torch), InputVector={"A": True})
    assert Result["Status"] == "Unknown"
    assert Result["Electrical"]["Outputs"]["Status"] == "Unknown"
    assert any(R["Code"] == "device-behavior-unavailable" for R in Result["Reasons"])


@pytest.mark.parametrize(
    ("FixtureName", "Endpoint", "ExpectedOn", "TemplateSha256", "FixtureSha256"),
    [
        ("DustSignalStrengthAtLimit", 15, True, "4039e38e0d6c121690f368b3e3c17211e3dadb0dac7e08cc63a0dc80db8eacd3", "33253a1d9af01c293b54f9a68021e2c3b4ca9194f24b2dc9d1ae180acc388c89"),
        ("DustSignalStrengthBeyondLimit", 16, False, "3f91c7cc2694a8e2154a6e668e0b8edc328446366f40c61313a1e2ab631540e9", "22252f5768eeb33e6dd0d607f744e7fda7c41d02120d21a2bc80f2cf755028c1"),
    ],
)
def test_straight_dust_signal_strength_boundary_uses_literal_layout_and_arithmetic_oracle(
    FixtureName, Endpoint, ExpectedOn, TemplateSha256, FixtureSha256,
):
    """Check literal F15/F16 geometry without reading either Expectations.json."""
    FixtureDirectory = FixtureRoot / FixtureName
    Fixture = LoadFixture(FixtureDirectory / "Fixture.json")
    TemplatePath = str((FixtureDirectory / "Template.json").resolve())
    assert Fixture.SourceHashes == {
        str((FixtureDirectory / "Fixture.json").resolve()): FixtureSha256,
        TemplatePath: TemplateSha256,
    }
    assert Fixture.Document["Inputs"] == [{"Name": "A", "Position": [0, 1, 0]}]
    assert Fixture.Document["Outputs"] == [{"Name": "Y", "Position": [Endpoint, 1, 0]}]
    assert Fixture.Document["RouteInputs"] == {"A": [1, 1, 0]}

    States = {tuple(Block["Position"]): Block["State"] for Block in Fixture.Document["Blocks"]}
    assert len(States) == 2 * Endpoint + 2
    assert Counter(Block["State"]["Name"] for Block in Fixture.Document["Blocks"]) == {
        "minecraft:stone": Endpoint + 1,
        "minecraft:lever": 1,
        "minecraft:redstone_wire": Endpoint,
    }
    assert {Position for Position, State in States.items() if State == {"Name": "minecraft:stone"}} == {
        (X, 0, 0) for X in range(Endpoint + 1)
    }
    assert States[(0, 1, 0)] == {
        "Name": "minecraft:lever",
        "Properties": {"face": "floor", "facing": "north", "powered": "false"},
    }
    Interior = {"Name": "minecraft:redstone_wire", "Properties": {
        "power": "0", "east": "side", "west": "side", "north": "none", "south": "none",
    }}
    End = {"Name": "minecraft:redstone_wire", "Properties": {
        "power": "0", "east": "none", "west": "side", "north": "none", "south": "none",
    }}
    assert all(States[(X, 1, 0)] == Interior for X in range(1, Endpoint))
    assert States[(Endpoint, 1, 0)] == End
    assert all((X, 0, 0) in States for X in range(1, Endpoint + 1))
    assert not any(Position[2] != 0 or Position[1] > 1 or Position[1] < 0 for Position in States)

    ExpectedEdges = [
        [[X, 1, 0], [X + 1, 1, 0]]
        for X in range(1, Endpoint)
        for _ in (0,)
    ]
    ExpectedEdges = [Edge for X in range(1, Endpoint) for Edge in (
        [[X, 1, 0], [X + 1, 1, 0]], [[X + 1, 1, 0], [X, 1, 0]],
    )]
    assert len(ExpectedEdges) == 2 * (Endpoint - 1)
    assert len({tuple(map(tuple, Edge)) for Edge in ExpectedEdges}) == len(ExpectedEdges)
    for Applied in (False, True):
        RootPower = 15 if Applied else 0
        # This is the independent oracle: one unit per dust edge, and zero is off.
        ExpectedOutput = RootPower - (Endpoint - 1) > 0
        assert ExpectedOutput is (ExpectedOn if Applied else False)
        Result = CheckPhysicalFixture(
            Fixture.Document,
            ExpectedFixtureSha256=Fixture.Sha256,
            InputVector={"A": Applied},
        )
        assert Result["Status"] == "Legal"
        assert Result["Electrical"]["InputVector"] == {"A": Applied}
        assert Result["Electrical"]["Outputs"] == {
            "Status": "Available",
            "Values": {"Y": ExpectedOutput},
            "Scope": "conditional-powered-route-transfer",
        }
        assert Result["Electrical"]["RequiredRootPower"] == {"A": RootPower}
        assert Result["Physical"]["DustConnections"] == ExpectedEdges
        assert Result["Physical"]["SupportPositions"] == [[X, 0, 0] for X in range(1, Endpoint + 1)]
        assert Result["Physical"]["HeadroomPositions"] == []
        assert Result["Physical"]["BlockedHeadroom"] == []
        assert Result["Physical"]["MissingSupport"] == []
        assert Result["Physical"]["ConflictPositions"] == []
        assert Result["Physical"]["RepeaterDirections"] == []


def test_w1_fixture_exposes_geometric_connection_and_route_uncertainty():
    Fixture = {
        "SchemaVersion": "physical-rules-w1-observation-input-v1",
        "Id": "wall-torch-dust-stair-east-backed",
        "Blocks": [
            {"Position": [0, -1, 0], "State": {"Name": "minecraft:smooth_stone"}},
            {"Position": [0, 0, 0], "State": {"Name": "minecraft:redstone_wire", "Properties": {"power": "0"}}},
            {"Position": [1, 0, 0], "State": {"Name": "minecraft:smooth_stone"}},
            {"Position": [1, 1, 0], "State": {"Name": "minecraft:redstone_wire", "Properties": {"power": "0"}}},
            {"Position": [0, 1, 0], "State": {"Name": "minecraft:redstone_wall_torch", "Properties": {"facing": "east", "lit": "true"}}},
            {"Position": [-1, 1, 0], "State": {"Name": "minecraft:smooth_stone"}},
        ],
    }
    Result = CheckPhysicalFixture(
        Fixture,
        ExpectedFixtureSha256=FixtureIdentity(Fixture),
        InputVector={},
    )

    assert Result["Status"] == "Unknown"
    assert Result["Physical"]["BlockedHeadroom"] == []
    assert [
        [[0, 0, 0], [1, 1, 0]],
        [[1, 1, 0], [0, 0, 0]],
    ] == Result["Physical"]["DustConnections"]
    assert len(Result["Physical"]["DustStairDecisions"]) == 1
    Decision = Result["Physical"]["DustStairDecisions"][0]
    assert Decision["GeometryStatus"] == "Connected"
    assert Decision["HeadroomClassification"] == "NonSolidElectrical"
    assert Decision["RouteClaimStatus"] == "Unknown"
    assert Decision["ReasonCode"] == "electrical-headroom-ownership-unavailable"
    assert Decision["ClaimPositions"] is None
    assert Result["Model"]["DustStairDecisionVersion"] == "dust-stair-decision-v1"
    assert any(
        Reason["Code"] == "electrical-headroom-ownership-unavailable"
        and Reason["DustStairInputIdentity"] == Decision["InputIdentity"]
        for Reason in Result["Reasons"]
    )

    WithExternalOracleData = deepcopy(Fixture)
    WithExternalOracleData["Expected"] = {"RouteClaimStatus": "Legal"}
    WithExternalOracleData["FabricResult"] = {
        "LowerPower": 15,
        "UpperPower": 15,
    }
    OracleResult = CheckPhysicalFixture(
        WithExternalOracleData,
        ExpectedFixtureSha256=FixtureIdentity(WithExternalOracleData),
        InputVector={},
    )
    assert (
        OracleResult["Physical"]["DustStairDecisions"][0]["InputIdentity"]
        == Decision["InputIdentity"]
    )
    assert OracleResult["Physical"]["DustStairDecisions"][0][
        "RouteClaimStatus"
    ] == "Unknown"

    ControlB = deepcopy(Fixture)
    ControlB["Blocks"] = [
        Block for Block in ControlB["Blocks"] if Block["Position"] != [0, 1, 0]
    ]
    ControlResult = CheckPhysicalFixture(
        ControlB,
        ExpectedFixtureSha256=FixtureIdentity(ControlB),
        InputVector={},
    )
    assert ControlResult["Status"] == "Legal"
    ControlDecision = ControlResult["Physical"]["DustStairDecisions"][0]
    assert ControlDecision["GeometryStatus"] == "Connected"
    assert ControlDecision["HeadroomClassification"] == "Air"
    assert ControlDecision["RouteClaimStatus"] == "Legal"

"""Outcome-first contract tests for the public dust-stair decision."""

from copy import deepcopy
from types import MappingProxyType

import pytest

from PhysicalDesign.Redstone.Rules import (
    DustStairDecisionVersion,
    DustStairQueryVersion,
    GeometryStatus,
    HeadroomClassification,
    QueryDustStair,
    RouteClaimStatus,
)


Lower = (0, 0, 0)
Upper = (1, 1, 0)
LowerSupport = (0, -1, 0)
UpperSupport = (1, 0, 0)
Headroom = (0, 1, 0)
Backing = (-1, 1, 0)


def _BaseContext():
    return {
        "ActualBlocks": {LowerSupport, UpperSupport},
        "SolidBlocks": {LowerSupport, UpperSupport},
        "SupportPositions": {LowerSupport, UpperSupport},
        "DustPositions": {Lower, Upper},
        "BlockStates": {
            LowerSupport: {"Name": "minecraft:smooth_stone"},
            UpperSupport: {"Name": "minecraft:smooth_stone"},
        },
    }


def _W1Context():
    Context = _BaseContext()
    Context["ActualBlocks"] = {
        LowerSupport,
        UpperSupport,
        Headroom,
        Backing,
    }
    Context["ElectricalBlocks"] = {Headroom}
    Context["SolidBlocks"] = {LowerSupport, UpperSupport, Backing}
    Context["SupportPositions"] = {LowerSupport, UpperSupport, Backing}
    Context["BlockStates"] = {
        **Context["BlockStates"],
        Headroom: {
            "Name": "minecraft:redstone_wall_torch",
            "Properties": {"facing": "east", "lit": "true"},
        },
        Backing: {"Name": "minecraft:smooth_stone"},
    }
    return Context


def test_public_decision_separates_p0_n0_and_w1_axes():
    P0 = QueryDustStair(Lower, Upper, **_BaseContext())
    assert P0.QueryVersion == DustStairQueryVersion
    assert P0.DecisionVersion == DustStairDecisionVersion
    assert P0.GeometryStatus is GeometryStatus.Connected
    assert P0.HeadroomClassification is HeadroomClassification.Air
    assert P0.RouteClaimStatus is RouteClaimStatus.Legal
    assert P0.ReasonCode == "supported-clear-air"
    assert P0.ClaimPositions is not None
    assert P0.ClaimPositions.WirePositions == (Lower, Upper)
    assert P0.ClaimPositions.SupportPositions == (LowerSupport, UpperSupport)
    assert P0.ClaimPositions.RequiredAirPositions == (Headroom,)

    N0Context = _BaseContext()
    N0Context["ActualBlocks"] = {*N0Context["ActualBlocks"], Headroom}
    N0Context["SolidBlocks"] = {*N0Context["SolidBlocks"], Headroom}
    N0Context["BlockStates"] = {
        **N0Context["BlockStates"],
        Headroom: {"Name": "minecraft:smooth_stone"},
    }
    N0 = QueryDustStair(Lower, Upper, **N0Context)
    assert N0.GeometryStatus is GeometryStatus.Blocked
    assert N0.HeadroomClassification is HeadroomClassification.Solid
    assert N0.RouteClaimStatus is RouteClaimStatus.Illegal
    assert N0.ReasonCode == "solid-headroom"
    assert N0.ClaimPositions is None

    W1 = QueryDustStair(Lower, Upper, **_W1Context())
    assert W1.GeometryStatus is GeometryStatus.Connected
    assert (
        W1.HeadroomClassification
        is HeadroomClassification.NonSolidElectrical
    )
    assert W1.RouteClaimStatus is RouteClaimStatus.Unknown
    assert W1.ReasonCode == "electrical-headroom-ownership-unavailable"
    assert W1.ClaimPositions is None


def test_w1_is_translation_stable_but_not_promoted_by_ownership_hints():
    Context = _W1Context()
    AbsentOwnership = QueryDustStair(Lower, Upper, **Context)
    SuppliedOwnership = QueryDustStair(
        Lower,
        Upper,
        **Context,
        RouteSignal="Signal",
        HeadroomOwnerSignal="Signal",
        SameNetIdentity=True,
        ElectricalCompatibility=True,
    )
    assert SuppliedOwnership.InputIdentity != AbsentOwnership.InputIdentity
    assert SuppliedOwnership.RouteClaimStatus is RouteClaimStatus.Unknown
    assert SuppliedOwnership.ClaimPositions is None

    Offset = (17, 4, -9)

    def Move(Position):
        return tuple(Position[Axis] + Offset[Axis] for Axis in range(3))

    Translated = {
        Key: (
            {Move(Position) for Position in Value}
            if Key.endswith("Blocks") or Key.endswith("Positions")
            else {Move(Position): State for Position, State in Value.items()}
        )
        for Key, Value in Context.items()
    }
    Decision = QueryDustStair(Move(Lower), Move(Upper), **Translated)
    assert Decision.GeometryStatus is GeometryStatus.Connected
    assert Decision.RouteClaimStatus is RouteClaimStatus.Unknown
    assert Decision.ReasonCode == "electrical-headroom-ownership-unavailable"


def test_missing_support_and_unqualified_headroom_states_fail_closed():
    MissingSupport = _BaseContext()
    MissingSupport["SupportPositions"] = {LowerSupport}
    MissingSupport["SolidBlocks"] = {LowerSupport}
    MissingSupport["ActualBlocks"] = {LowerSupport}
    MissingSupport["BlockStates"] = {
        LowerSupport: {"Name": "minecraft:smooth_stone"}
    }
    Missing = QueryDustStair(Lower, Upper, **MissingSupport)
    assert Missing.GeometryStatus is GeometryStatus.Blocked
    assert Missing.RouteClaimStatus is RouteClaimStatus.Illegal
    assert Missing.ReasonCode == "missing-support"
    assert Missing.ClaimPositions is None

    Inert = _BaseContext()
    Inert["ActualBlocks"] = {*Inert["ActualBlocks"], Headroom}
    Inert["BlockStates"] = {
        **Inert["BlockStates"],
        Headroom: {"Name": "minecraft:water"},
    }
    InertDecision = QueryDustStair(Lower, Upper, **Inert)
    assert (
        InertDecision.HeadroomClassification
        is HeadroomClassification.NonSolidInert
    )
    assert InertDecision.GeometryStatus is GeometryStatus.Unknown
    assert InertDecision.RouteClaimStatus is RouteClaimStatus.Unknown
    assert InertDecision.ClaimPositions is None

    NovelElectrical = _W1Context()
    NovelElectrical["BlockStates"] = {
        **NovelElectrical["BlockStates"],
        Headroom: {
            "Name": "minecraft:redstone_wall_torch",
            "Properties": {"facing": "west", "lit": "true"},
        },
    }
    Novel = QueryDustStair(Lower, Upper, **NovelElectrical)
    assert Novel.GeometryStatus is GeometryStatus.Unknown
    assert Novel.RouteClaimStatus is RouteClaimStatus.Unknown
    assert Novel.ReasonCode == "unqualified-electrical-headroom"

    MalformedElectrical = _W1Context()
    MalformedElectrical["BlockStates"] = {
        **MalformedElectrical["BlockStates"],
        Headroom: {
            "Name": "minecraft:redstone_wall_torch",
            "Properties": {"facing": "east", "lit": True},
        },
    }
    Malformed = QueryDustStair(Lower, Upper, **MalformedElectrical)
    assert Malformed.HeadroomClassification is HeadroomClassification.Unknown
    assert Malformed.GeometryStatus is GeometryStatus.Unknown
    assert Malformed.RouteClaimStatus is RouteClaimStatus.Unknown
    assert Malformed.ClaimPositions is None

    Insufficient = _BaseContext()
    Insufficient["ActualBlocks"] = {*Insufficient["ActualBlocks"], Headroom}
    InsufficientDecision = QueryDustStair(Lower, Upper, **Insufficient)
    assert (
        InsufficientDecision.HeadroomClassification
        is HeadroomClassification.Unknown
    )
    assert InsufficientDecision.GeometryStatus is GeometryStatus.Unknown
    assert InsufficientDecision.RouteClaimStatus is RouteClaimStatus.Unknown


def test_normalized_identity_is_deterministic_and_oracle_free():
    First = QueryDustStair(Lower, Upper, **_W1Context())
    Reordered = _W1Context()
    Reordered["ActualBlocks"] = tuple(reversed(sorted(Reordered["ActualBlocks"])))
    Reordered["BlockStates"] = dict(
        reversed(tuple(Reordered["BlockStates"].items()))
    )
    Second = QueryDustStair(Upper, Lower, **Reordered)
    assert Second.InputIdentity == First.InputIdentity
    assert Second.ToDictionary()["NormalizedInput"] == First.ToDictionary()[
        "NormalizedInput"
    ]

    Changed = _W1Context()
    Changed["BlockStates"] = deepcopy(Changed["BlockStates"])
    Changed["BlockStates"][Headroom]["Properties"]["lit"] = "false"
    assert QueryDustStair(Lower, Upper, **Changed).InputIdentity != First.InputIdentity

    FixtureEnvelope = {
        "SemanticContext": _W1Context(),
        "Expected": {"RouteClaimStatus": "Legal"},
        "FabricResult": {"lowerPower": 15, "upperPower": 15},
    }
    Before = QueryDustStair(
        Lower,
        Upper,
        **FixtureEnvelope["SemanticContext"],
    )
    FixtureEnvelope["Expected"] = {"RouteClaimStatus": "Illegal"}
    FixtureEnvelope["FabricResult"] = {"lowerPower": 0, "upperPower": 0}
    After = QueryDustStair(
        Lower,
        Upper,
        **FixtureEnvelope["SemanticContext"],
    )
    assert After == Before


def test_identity_includes_complete_malformed_state_and_validity():
    Exact = QueryDustStair(Lower, Upper, **_W1Context())
    Variants = []

    ExtraField = _W1Context()
    ExtraField["BlockStates"] = deepcopy(ExtraField["BlockStates"])
    ExtraField["BlockStates"][Headroom]["Extra"] = "x"
    Variants.append(QueryDustStair(Lower, Upper, **ExtraField))

    MissingField = _W1Context()
    MissingField["BlockStates"] = deepcopy(MissingField["BlockStates"])
    del MissingField["BlockStates"][Headroom]["Properties"]["lit"]
    Variants.append(QueryDustStair(Lower, Upper, **MissingField))

    MalformedProperty = _W1Context()
    MalformedProperty["BlockStates"] = deepcopy(
        MalformedProperty["BlockStates"]
    )
    MalformedProperty["BlockStates"][Headroom]["Properties"]["lit"] = True
    Variants.append(QueryDustStair(Lower, Upper, **MalformedProperty))

    ExactObservable = (
        Exact.GeometryStatus,
        Exact.HeadroomClassification,
        Exact.RouteClaimStatus,
        Exact.ReasonCode,
    )
    for Variant in Variants:
        VariantObservable = (
            Variant.GeometryStatus,
            Variant.HeadroomClassification,
            Variant.RouteClaimStatus,
            Variant.ReasonCode,
        )
        assert VariantObservable != ExactObservable
        assert Variant.InputIdentity != Exact.InputIdentity

    Reordered = _W1Context()
    Reordered["BlockStates"] = dict(
        reversed(tuple(Reordered["BlockStates"].items()))
    )
    assert QueryDustStair(
        Upper,
        Lower,
        **Reordered,
    ).InputIdentity == Exact.InputIdentity


def test_identity_rejects_lossy_subclasses_and_noncanonical_values():
    class TaggedString(str):
        pass

    class TaggedMapping(dict):
        pass

    class TaggedList(list):
        pass

    class TaggedInt(int):
        pass

    for Value in (TaggedString("true"), TaggedString("false")):
        States = deepcopy(_W1Context()["BlockStates"])
        States[Headroom]["Properties"]["lit"] = Value
        with pytest.raises(TypeError, match="TaggedString"):
            QueryDustStair(
                Lower,
                Upper,
                **{
                    **_W1Context(),
                    "BlockStates": States,
                },
            )

    Context = _W1Context()
    with pytest.raises(TypeError, match="BlockStates must be"):
        QueryDustStair(
            Lower,
            Upper,
            **{
                **Context,
                "BlockStates": TaggedMapping(Context["BlockStates"]),
            },
        )

    Challenges = (
        ("custom-container", TaggedList(["x"]), "TaggedList"),
        ("custom-scalar", TaggedInt(1), "TaggedInt"),
        ("set", {"x"}, "builtins.set"),
        ("nonfinite", float("nan"), "floats must be finite"),
    )
    for Key, Value, Pattern in Challenges:
        States = deepcopy(Context["BlockStates"])
        States[Headroom][Key] = Value
        with pytest.raises((TypeError, ValueError), match=Pattern):
            QueryDustStair(
                Lower,
                Upper,
                **{
                    **Context,
                    "BlockStates": States,
                },
            )

    NonStringKey = deepcopy(Context["BlockStates"])
    NonStringKey[Headroom][7] = "x"
    with pytest.raises(TypeError, match="keys must be exact strings"):
        QueryDustStair(
            Lower,
            Upper,
            **{
                **Context,
                "BlockStates": NonStringKey,
            },
        )

    def Freeze(Value):
        if type(Value) is dict:
            return MappingProxyType({
                Key: Freeze(Item) for Key, Item in Value.items()
            })
        if type(Value) is list:
            return tuple(Freeze(Item) for Item in Value)
        return Value

    Exact = QueryDustStair(Lower, Upper, **Context)
    FrozenStates = MappingProxyType({
        Position: Freeze(State)
        for Position, State in Context["BlockStates"].items()
    })
    Frozen = QueryDustStair(
        Upper,
        Lower,
        **{
            **Context,
            "BlockStates": FrozenStates,
        },
    )
    assert Frozen.InputIdentity == Exact.InputIdentity
    assert Frozen.ToDictionary()["NormalizedInput"] == Exact.ToDictionary()[
        "NormalizedInput"
    ]

"""Versioned, state-aware dust-stair geometry and route-claim decisions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Literal

from ...Contracts.Core import Position3


DustStairQueryVersion = "dust-stair-query-v1"
DustStairDecisionVersion = "dust-stair-decision-v1"


class GeometryStatus(str, Enum):
    """Whether the two dust positions have a qualified Minecraft arm."""

    Connected = "Connected"
    Blocked = "Blocked"
    Unknown = "Unknown"


class HeadroomClassification(str, Enum):
    """Normalized occupancy/electrical class at the lower dust headroom."""

    Air = "Air"
    Solid = "Solid"
    NonSolidInert = "NonSolidInert"
    NonSolidElectrical = "NonSolidElectrical"
    Unknown = "Unknown"


class RouteClaimStatus(str, Enum):
    """Whether the stair may create routing claims."""

    Legal = "Legal"
    Illegal = "Illegal"
    Unknown = "Unknown"


@dataclass(frozen=True)
class DustStairClaimPositions:
    """Semantic claim positions supplied only for a legal stair decision."""

    WirePositions: tuple[Position3, Position3]
    SupportPositions: tuple[Position3, Position3]
    RequiredAirPositions: tuple[Position3, ...]

    def ToDictionary(self) -> dict[str, object]:
        return {
            "WirePositions": [list(Position) for Position in self.WirePositions],
            "SupportPositions": [
                list(Position) for Position in self.SupportPositions
            ],
            "RequiredAirPositions": [
                list(Position) for Position in self.RequiredAirPositions
            ],
        }


@dataclass(frozen=True)
class DustStairDecision:
    """One immutable result shared by routing, validation, and rendering."""

    QueryVersion: str
    DecisionVersion: str
    InputIdentity: str
    NormalizedInputJson: str
    LowerPosition: Position3
    UpperPosition: Position3
    LowerSupportPosition: Position3
    UpperSupportPosition: Position3
    HeadroomPosition: Position3
    GeometryStatus: GeometryStatus
    HeadroomClassification: HeadroomClassification
    RouteClaimStatus: RouteClaimStatus
    ReasonCode: str
    ClaimPositions: DustStairClaimPositions | None

    def ToDictionary(self) -> dict[str, object]:
        return {
            "QueryVersion": self.QueryVersion,
            "DecisionVersion": self.DecisionVersion,
            "InputIdentity": self.InputIdentity,
            "NormalizedInput": json.loads(self.NormalizedInputJson),
            "LowerPosition": list(self.LowerPosition),
            "UpperPosition": list(self.UpperPosition),
            "LowerSupportPosition": list(self.LowerSupportPosition),
            "UpperSupportPosition": list(self.UpperSupportPosition),
            "HeadroomPosition": list(self.HeadroomPosition),
            "GeometryStatus": self.GeometryStatus.value,
            "HeadroomClassification": self.HeadroomClassification.value,
            "RouteClaimStatus": self.RouteClaimStatus.value,
            "ReasonCode": self.ReasonCode,
            "ClaimPositions": (
                None
                if self.ClaimPositions is None
                else self.ClaimPositions.ToDictionary()
            ),
        }


ElectricalBlockNames = frozenset({
    "minecraft:comparator",
    "minecraft:lever",
    "minecraft:redstone_torch",
    "minecraft:redstone_wall_torch",
    "minecraft:redstone_wire",
    "minecraft:repeater",
})


def _NormalizePosition(Position: Position3) -> Position3:
    if (
        type(Position) is not tuple
        or len(Position) != 3
        or any(type(Value) is not int for Value in Position)
    ):
        raise TypeError("dust-stair positions must be integer XYZ tuples")
    return Position


def _CanonicalValue(Value: Any) -> Any:
    if Value is None or type(Value) in (bool, int, str):
        return Value
    if type(Value) is float:
        if not isfinite(Value):
            raise ValueError(
                "dust-stair state floats must be finite"
            )
        return Value
    if type(Value) in (dict, MappingProxyType):
        if any(type(Key) is not str for Key in Value):
            raise TypeError(
                "dust-stair state mapping keys must be exact strings"
            )
        return {
            Key: _CanonicalValue(Item)
            for Key, Item in sorted(Value.items())
        }
    if type(Value) in (tuple, list):
        return [_CanonicalValue(Item) for Item in Value]
    raise TypeError(
        "unsupported dust-stair state value type: "
        f"{type(Value).__module__}.{type(Value).__qualname__}"
    )


def _NormalizeState(
    State: Any,
    *,
    Present: bool,
) -> tuple[dict[str, Any], bool]:
    if not Present:
        return {"Present": False, "Valid": True}, True
    if type(State) not in (dict, MappingProxyType):
        raise TypeError(
            "dust-stair block states must be exact dict or mappingproxy values"
        )
    Name = State.get("Name")
    Properties = State.get("Properties", {})
    Valid = (
        type(Name) is str
        and bool(Name)
        and type(Properties) in (dict, MappingProxyType)
        and all(type(Key) is str for Key in Properties)
        and all(type(Value) is str for Value in Properties.values())
        and set(State).issubset({"Name", "Properties"})
    )
    return {
        "Present": True,
        "Valid": Valid,
        "RawState": _CanonicalValue(State),
    }, Valid


def _StateName(State: Any) -> str | None:
    if type(State) not in (dict, MappingProxyType):
        return None
    Name = State.get("Name")
    return Name if type(Name) is str else None


def _Decision(
    *,
    NormalizedInput: dict[str, object],
    Lower: Position3,
    Upper: Position3,
    LowerSupport: Position3,
    UpperSupport: Position3,
    Headroom: Position3,
    Geometry: GeometryStatus,
    HeadroomKind: HeadroomClassification,
    Route: RouteClaimStatus,
    Reason: str,
    Claims: DustStairClaimPositions | None = None,
) -> DustStairDecision:
    NormalizedJson = json.dumps(
        NormalizedInput,
        sort_keys=True,
        separators=(",", ":"),
    )
    Identity = sha256((NormalizedJson + "\n").encode()).hexdigest()
    if Route is not RouteClaimStatus.Legal:
        Claims = None
    return DustStairDecision(
        QueryVersion=DustStairQueryVersion,
        DecisionVersion=DustStairDecisionVersion,
        InputIdentity=Identity,
        NormalizedInputJson=NormalizedJson,
        LowerPosition=Lower,
        UpperPosition=Upper,
        LowerSupportPosition=LowerSupport,
        UpperSupportPosition=UpperSupport,
        HeadroomPosition=Headroom,
        GeometryStatus=Geometry,
        HeadroomClassification=HeadroomKind,
        RouteClaimStatus=Route,
        ReasonCode=Reason,
        ClaimPositions=Claims,
    )


def QueryDustStair(
    First: Position3,
    Second: Position3,
    *,
    ActualBlocks: Iterable[Position3] = (),
    ElectricalBlocks: Iterable[Position3] = (),
    SolidBlocks: Iterable[Position3] = (),
    SupportPositions: Iterable[Position3] = (),
    DustPositions: Iterable[Position3] = (),
    BlockStates: Mapping[Position3, Any] | None = None,
    SupportMode: Literal["Existing", "Claimable"] = "Existing",
    RouteSignal: str | None = None,
    HeadroomOwnerSignal: str | None = None,
    SameNetIdentity: bool | None = None,
    ElectricalCompatibility: bool | None = None,
) -> DustStairDecision:
    """Classify one dust stair without consulting expectations or observations.

    ``Existing`` support mode is used by final physical validation and
    rendering. ``Claimable`` mode is used by the resource graph, where an
    empty support cell can be reserved and filled by a route claim.
    """
    First = _NormalizePosition(First)
    Second = _NormalizePosition(Second)
    Actual = frozenset(map(_NormalizePosition, ActualBlocks))
    Electrical = frozenset(map(_NormalizePosition, ElectricalBlocks))
    Solid = frozenset(map(_NormalizePosition, SolidBlocks))
    Supports = frozenset(map(_NormalizePosition, SupportPositions))
    Dust = frozenset(map(_NormalizePosition, DustPositions))
    if BlockStates is None:
        States = {}
    elif type(BlockStates) in (dict, MappingProxyType):
        States = dict(BlockStates)
    else:
        raise TypeError(
            "dust-stair BlockStates must be an exact dict or mappingproxy"
        )

    if First[1] < Second[1]:
        Lower, Upper = First, Second
    elif Second[1] < First[1]:
        Lower, Upper = Second, First
    else:
        Lower, Upper = sorted((First, Second))
    LowerSupport = (Lower[0], Lower[1] - 1, Lower[2])
    UpperSupport = (Upper[0], Upper[1] - 1, Upper[2])
    Headroom = (Lower[0], Lower[1] + 1, Lower[2])
    Backing = (Headroom[0] - 1, Headroom[1], Headroom[2])
    RelevantPositions = (
        Lower,
        Upper,
        LowerSupport,
        UpperSupport,
        Headroom,
        Backing,
    )
    NormalizedStates: dict[str, object] = {}
    StateValidity: dict[Position3, bool] = {}
    for Position in RelevantPositions:
        NormalizedState, Valid = _NormalizeState(
            States.get(Position),
            Present=Position in States,
        )
        NormalizedStates[",".join(map(str, Position))] = NormalizedState
        StateValidity[Position] = Valid

    NormalizedInput: dict[str, object] = {
        "QueryVersion": DustStairQueryVersion,
        "LowerPosition": list(Lower),
        "UpperPosition": list(Upper),
        "LowerSupportPosition": list(LowerSupport),
        "UpperSupportPosition": list(UpperSupport),
        "HeadroomPosition": list(Headroom),
        "WallTorchBackingPosition": list(Backing),
        "Membership": {
            ",".join(map(str, Position)): {
                "Actual": Position in Actual,
                "Electrical": Position in Electrical,
                "Solid": Position in Solid,
                "Support": Position in Supports,
                "Dust": Position in Dust,
            }
            for Position in RelevantPositions
        },
        "BlockStates": NormalizedStates,
        "Context": {
            "SupportMode": SupportMode,
            "RouteSignal": RouteSignal,
            "HeadroomOwnerSignal": HeadroomOwnerSignal,
            "SameNetIdentity": SameNetIdentity,
            "ElectricalCompatibility": ElectricalCompatibility,
        },
    }

    Delta = (
        Upper[0] - Lower[0],
        Upper[1] - Lower[1],
        Upper[2] - Lower[2],
    )
    IsSupportedDelta = (
        Delta[1] == 1
        and abs(Delta[0]) + abs(Delta[2]) == 1
    )
    HeadroomState = States.get(Headroom)
    HeadroomName = _StateName(HeadroomState)
    HeadroomOccupied = (
        Headroom in Actual
        or (HeadroomName is not None and HeadroomName != "minecraft:air")
    )
    HeadroomElectrical = (
        Headroom in Electrical or HeadroomName in ElectricalBlockNames
    )
    if not StateValidity[Headroom]:
        HeadroomKind = HeadroomClassification.Unknown
    elif Headroom in Solid:
        HeadroomKind = HeadroomClassification.Solid
    elif HeadroomName == "minecraft:air" and HeadroomOccupied:
        HeadroomKind = HeadroomClassification.Unknown
    elif not HeadroomOccupied:
        HeadroomKind = HeadroomClassification.Air
    elif HeadroomElectrical:
        HeadroomKind = HeadroomClassification.NonSolidElectrical
    elif HeadroomState is None:
        HeadroomKind = HeadroomClassification.Unknown
    else:
        HeadroomKind = HeadroomClassification.NonSolidInert

    if not IsSupportedDelta:
        return _Decision(
            NormalizedInput=NormalizedInput,
            Lower=Lower,
            Upper=Upper,
            LowerSupport=LowerSupport,
            UpperSupport=UpperSupport,
            Headroom=Headroom,
            Geometry=GeometryStatus.Unknown,
            HeadroomKind=HeadroomKind,
            Route=RouteClaimStatus.Unknown,
            Reason="unsupported-dust-stair-delta",
        )
    if SupportMode not in ("Existing", "Claimable"):
        return _Decision(
            NormalizedInput=NormalizedInput,
            Lower=Lower,
            Upper=Upper,
            LowerSupport=LowerSupport,
            UpperSupport=UpperSupport,
            Headroom=Headroom,
            Geometry=GeometryStatus.Unknown,
            HeadroomKind=HeadroomKind,
            Route=RouteClaimStatus.Unknown,
            Reason="unsupported-support-context",
        )

    def HasSupport(Position: Position3) -> bool:
        if Position in Dust:
            return False
        if SupportMode == "Existing":
            return Position in Supports or Position in Solid
        return Position not in Actual or Position in Solid

    if not HasSupport(LowerSupport) or not HasSupport(UpperSupport):
        return _Decision(
            NormalizedInput=NormalizedInput,
            Lower=Lower,
            Upper=Upper,
            LowerSupport=LowerSupport,
            UpperSupport=UpperSupport,
            Headroom=Headroom,
            Geometry=GeometryStatus.Blocked,
            HeadroomKind=HeadroomKind,
            Route=RouteClaimStatus.Illegal,
            Reason="missing-support",
        )
    if HeadroomKind is HeadroomClassification.Solid:
        return _Decision(
            NormalizedInput=NormalizedInput,
            Lower=Lower,
            Upper=Upper,
            LowerSupport=LowerSupport,
            UpperSupport=UpperSupport,
            Headroom=Headroom,
            Geometry=GeometryStatus.Blocked,
            HeadroomKind=HeadroomKind,
            Route=RouteClaimStatus.Illegal,
            Reason="solid-headroom",
        )
    if HeadroomKind is HeadroomClassification.Air:
        Claims = DustStairClaimPositions(
            WirePositions=(Lower, Upper),
            SupportPositions=(LowerSupport, UpperSupport),
            RequiredAirPositions=(Headroom,),
        )
        return _Decision(
            NormalizedInput=NormalizedInput,
            Lower=Lower,
            Upper=Upper,
            LowerSupport=LowerSupport,
            UpperSupport=UpperSupport,
            Headroom=Headroom,
            Geometry=GeometryStatus.Connected,
            HeadroomKind=HeadroomKind,
            Route=RouteClaimStatus.Legal,
            Reason="supported-clear-air",
            Claims=Claims,
        )

    HeadroomProperties = (
        HeadroomState.get("Properties", {})
        if type(HeadroomState) in (dict, MappingProxyType)
        else {}
    )
    IsExactQualifiedW1 = (
        HeadroomKind is HeadroomClassification.NonSolidElectrical
        and StateValidity[Headroom]
        and HeadroomName == "minecraft:redstone_wall_torch"
        and HeadroomProperties == {"facing": "east", "lit": "true"}
        and Delta == (1, 1, 0)
        and Backing in Solid
        and StateValidity[Backing]
        and _StateName(States.get(Backing)) not in (None, "minecraft:air")
    )
    if IsExactQualifiedW1:
        return _Decision(
            NormalizedInput=NormalizedInput,
            Lower=Lower,
            Upper=Upper,
            LowerSupport=LowerSupport,
            UpperSupport=UpperSupport,
            Headroom=Headroom,
            Geometry=GeometryStatus.Connected,
            HeadroomKind=HeadroomKind,
            Route=RouteClaimStatus.Unknown,
            Reason="electrical-headroom-ownership-unavailable",
        )

    if HeadroomKind is HeadroomClassification.Unknown:
        Reason = "headroom-state-or-occupancy-unavailable"
    elif HeadroomKind is HeadroomClassification.NonSolidElectrical:
        Reason = "unqualified-electrical-headroom"
    else:
        Reason = "unqualified-nonsolid-headroom"
    return _Decision(
        NormalizedInput=NormalizedInput,
        Lower=Lower,
        Upper=Upper,
        LowerSupport=LowerSupport,
        UpperSupport=UpperSupport,
        Headroom=Headroom,
        Geometry=GeometryStatus.Unknown,
        HeadroomKind=HeadroomKind,
        Route=RouteClaimStatus.Unknown,
        Reason=Reason,
    )

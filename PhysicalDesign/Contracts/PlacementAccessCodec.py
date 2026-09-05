"""Strict JSON decoding for immutable pin-access records and their evidence."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from functools import lru_cache
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

from ..Resources.ResourceGraph import RoutingReservation, RoutingResourceClaims, RoutingResourceId, RoutingResourceKind


def _SameJson(Actual: object, Expected: object) -> bool:
    """Compare JSON without Python's bool/int coercion or list reordering."""
    if type(Actual) is not type(Expected):
        return False
    if isinstance(Expected, dict):
        return Actual.keys() == Expected.keys() and all(
            _SameJson(Actual[Key], Value) for Key, Value in Expected.items()
        )
    if isinstance(Expected, list):
        return len(Actual) == len(Expected) and all(
            _SameJson(First, Second) for First, Second in zip(Actual, Expected)
        )
    return Actual == Expected


@lru_cache(maxsize=None)
def _Hints(Contract: type) -> dict[str, object]:
    return get_type_hints(Contract)


def _ReadValue(Hint: object, Value: object) -> object:
    Origin = get_origin(Hint)
    Arguments = get_args(Hint)
    if Hint is type(None):
        if Value is not None:
            raise ValueError("expected null")
        return None
    if Origin in (UnionType, Union):
        Failures = []
        for Alternative in Arguments:
            try:
                return _ReadValue(Alternative, Value)
            except (ValueError, TypeError) as Error:
                Failures.append(str(Error))
        raise ValueError("value does not match contract union: " + "; ".join(Failures))
    if Origin is Literal:
        if not any(type(Value) is type(Item) and Value == Item for Item in Arguments):
            raise ValueError("invalid literal in access contract")
        return Value
    if Origin in (tuple, frozenset):
        if type(Value) is not list:
            raise ValueError("contract sequence must be a JSON array")
        if Origin is frozenset or (len(Arguments) == 2 and Arguments[1] is Ellipsis):
            Items = tuple(_ReadValue(Arguments[0], Item) for Item in Value)
        else:
            if len(Value) != len(Arguments):
                raise ValueError("contract tuple has incorrect length")
            Items = tuple(_ReadValue(Type, Item) for Type, Item in zip(Arguments, Value))
        if Origin is frozenset:
            if Items != tuple(sorted(set(Items))):
                raise ValueError("claim coordinates must be sorted and unique")
            return frozenset(Items)
        return Items
    if isinstance(Hint, type) and issubclass(Hint, Enum):
        if type(Value) is not str:
            raise ValueError("contract enum must be a string")
        return Hint(Value)
    if is_dataclass(Hint):
        Reader = getattr(Hint, "FromDictionary", None)
        return Reader(Value) if Reader else ReadContract(Hint, Value)
    if type(Value) is not Hint:
        raise ValueError(f"expected {Hint}, received {type(Value)}")
    return Value


def ReadContract(Contract: type, Value: object) -> object:
    """Rebuild typed values and verify every serialized field, including hashes.

    Decoding establishes internal consistency, not physical legality or current
    snapshot authority. Those require the coordinator's current-domain checks.
    """
    if type(Value) is not dict or any(type(Key) is not str for Key in Value):
        raise ValueError("access contract must be a JSON object")
    Hints = _Hints(Contract)
    Arguments = {}
    try:
        for Field in fields(Contract):
            Item = Value[Field.name]
            if Field.name == "ClaimsBySignal":
                if type(Item) is not dict:
                    raise ValueError("claims by signal must be an object")
                Item = [[Signal, Claims] for Signal, Claims in sorted(Item.items())]
            elif Field.name == "BlockRoles":
                if type(Item) is not list or any(type(Role) is not dict or set(Role) != {"Position", "Role"} for Role in Item):
                    raise ValueError("block roles must contain exact position/role objects")
                Item = [[Role["Position"], Role["Role"]] for Role in Item]
            elif Contract is RoutingReservation and Field.name == "Resource":
                Kind, Coordinates = Item.split(":")
                Parts = Coordinates.split(",")
                if len(Parts) != 3:
                    raise ValueError("invalid reservation resource coordinate")
                Arguments[Field.name] = RoutingResourceId(RoutingResourceKind(Kind), tuple(int(Part) for Part in Parts))
                continue
            Arguments[Field.name] = _ReadValue(Hints[Field.name], Item)
        Result = Contract(**Arguments)
        if Contract is RoutingReservation:
            Expected = {"Signal": Result.Signal, "Resource": str(Result.Resource), "Position": list(Result.Position), "Purpose": Result.Purpose, "InputFacing": Result.InputFacing}
        elif Contract is RoutingResourceClaims:
            Expected = {Field.name: [list(Position) for Position in sorted(getattr(Result, Field.name))] for Field in fields(Result)}
        else:
            Expected = Result.ToDictionary()
        if not _SameJson(Value, Expected):
            raise ValueError(f"{Contract.__name__} contains missing, unknown, or inconsistent fields")
        return Result
    except (KeyError, TypeError, AttributeError, OverflowError) as Error:
        raise ValueError(f"malformed {Contract.__name__}: {Error}") from Error

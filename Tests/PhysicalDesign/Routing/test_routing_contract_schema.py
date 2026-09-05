"""Structural parity checks for the neutral routing contract packages."""

from __future__ import annotations

import dataclasses
from hashlib import sha256
import inspect
import json

from PhysicalDesign.Contracts import Component, Core, PhysicalInterface, Placement, Results


ExpectedRoutingContractSchemaHash = (
    "542b7f2210ab6eec76a8db31ef8dca457a98893ee5d96cfa73347256260baed8"
)
ContractFamilies = {
    "Component": Component,
    "Core": Core,
    "PhysicalInterface": PhysicalInterface,
    "Placement": Placement,
    "Results": Results,
}


def _StableValue(Value: object) -> str:
    if Value is dataclasses.MISSING:
        return "MISSING"
    return repr(Value)


def _StableFactory(Value: object) -> str:
    if Value is dataclasses.MISSING:
        return "MISSING"
    Module = getattr(Value, "__module__", "")
    Name = getattr(Value, "__qualname__", repr(Value))
    return f"{Module}.{Name}" if Module else Name


def _CaptureClassSchema(
    Family: str,
    Value: type[object],
) -> dict[str, object]:
    Entry: dict[str, object] = {
        "Family": Family,
        "Bases": [Base.__name__ for Base in Value.__bases__],
        "Signature": str(inspect.signature(Value)),
    }
    if dataclasses.is_dataclass(Value):
        Parameters = Value.__dataclass_params__
        Entry["Dataclass"] = {
            "Eq": Parameters.eq,
            "Frozen": Parameters.frozen,
            "Init": Parameters.init,
            "Order": Parameters.order,
            "Repr": Parameters.repr,
            "UnsafeHash": Parameters.unsafe_hash,
        }
        Entry["Fields"] = [
            {
                "Compare": FieldValue.compare,
                "Default": _StableValue(FieldValue.default),
                "DefaultFactory": _StableFactory(
                    FieldValue.default_factory
                ),
                "Hash": FieldValue.hash,
                "Init": FieldValue.init,
                "KwOnly": FieldValue.kw_only,
                "Name": FieldValue.name,
                "Repr": FieldValue.repr,
                "Type": str(FieldValue.type),
            }
            for FieldValue in dataclasses.fields(Value)
        ]
    Entry["Methods"] = {
        MemberName: str(inspect.signature(Member))
        for MemberName, Member in vars(Value).items()
        if inspect.isfunction(Member) and not MemberName.startswith("__")
    }
    Entry["Properties"] = {
        MemberName: str(inspect.signature(Member.fget))
        for MemberName, Member in vars(Value).items()
        if isinstance(Member, property) and Member.fget is not None
    }
    return Entry


def _CaptureRoutingContractSchema() -> dict[str, object]:
    Classes: dict[str, object] = {}
    for Family, Module in ContractFamilies.items():
        for Name, Value in vars(Module).items():
            if not inspect.isclass(Value):
                continue
            if Value.__module__ != Module.__name__:
                continue
            assert Name not in Classes, Name
            Classes[Name] = _CaptureClassSchema(Family, Value)
    return {
        "Aliases": {
            "Position2": str(Core.Position2),
            "Position3": str(Core.Position3),
        },
        "Classes": Classes,
    }


def test_routing_contract_schema_matches_s1_access_handoff() -> None:
    """Schema matches the explicit S1 pin-access identity handoff."""

    Payload = json.dumps(
        _CaptureRoutingContractSchema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert len(_CaptureRoutingContractSchema()["Classes"]) == 81
    assert sha256(Payload).hexdigest() == ExpectedRoutingContractSchemaHash


def test_routing_contract_dependencies_are_one_way() -> None:
    """Neutral contracts cannot import routing or placement orchestrators."""

    ForbiddenPrefixes = (
        "PhysicalDesign.Orchestration",
        "PhysicalDesign.Routing.Global",
        "PhysicalDesign.Routing.Regions",
    )
    for Module in ContractFamilies.values():
        for Value in vars(Module).values():
            ImportedModule = getattr(Value, "__module__", "")
            assert not ImportedModule.startswith(ForbiddenPrefixes)

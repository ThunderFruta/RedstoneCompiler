"""Load source-bound physical fixtures without reading expectations."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from Formats.Litematic.Codec import LoadTemplate, ReadNbt
from PhysicalDesign.Redstone.FixtureValidation import FixtureIdentity, ValidateFixtureBlockState
from .Expectations import Fields, FixtureConfigurationError, ReadJson


def Position(Value: Any) -> list[int]:
    if not isinstance(Value, list) or len(Value) != 3 or any(type(Axis) is not int or abs(Axis) > 1000000 for Axis in Value):
        raise FixtureConfigurationError("position-must-be-three-bounded-integers")
    return Value


@dataclass(frozen=True)
class LoadedFixture:
    Document: dict[str, Any]
    Sha256: str
    SourceHashes: dict[str, str]


def LoadFixture(PathValue: Path) -> LoadedFixture:
    """Read a template JSON or single-region litematic plus explicit port map.

    Paths are relative to Fixture.json. Template JSON uses the existing physical
    fixture block-state shape. Multi-region litematics are rejected because the
    current template codec reads only one region.
    """
    Source = ReadJson(PathValue)
    Fields(Source, {"schemaVersion", "source", "inputs", "outputs"}, {"routeInputs"})
    if Source["schemaVersion"] != "physical-rules-fixture-v1":
        raise FixtureConfigurationError("unsupported-fixture-schema")
    Fields(Source["source"], {"kind", "path", "sha256"})
    FixturePath = (PathValue.parent / Source["source"]["path"]).resolve()
    Content = FixturePath.read_bytes()
    SourceDigest = sha256(Content).hexdigest()
    if SourceDigest != Source["source"]["sha256"]:
        raise FixtureConfigurationError("stale-fixture-source")
    Kind = Source["source"]["kind"]
    if Kind == "litematic":
        if len(ReadNbt(FixturePath)["Regions"].Value) != 1:
            raise FixtureConfigurationError("multi-region-litematic-unsupported")
        Template = LoadTemplate(FixturePath)
        Blocks = [{"Position": list(P), "State": S} for P, S in sorted(Template.Blocks.items())]
    elif Kind == "template":
        Template = ReadJson(FixturePath)
        Fields(Template, {"Blocks"})
        Blocks = Template["Blocks"]
    else:
        raise FixtureConfigurationError("unsupported-fixture-source-kind")
    if not isinstance(Blocks, list) or not Blocks:
        raise FixtureConfigurationError("nonempty-block-list-required")
    Occupied = {}
    for Block in Blocks:
        Fields(Block, {"Position", "State"})
        Key = tuple(Position(Block["Position"]))
        if Key in Occupied:
            raise FixtureConfigurationError("duplicate-block-position")
        Fields(Block["State"], {"Name"}, {"Properties"})
        State = Block["State"]
        if not isinstance(State["Name"], str) or not State["Name"].startswith("minecraft:"):
            raise FixtureConfigurationError("invalid-block-name")
        Properties = State.get("Properties", {})
        if not isinstance(Properties, dict) or any(not isinstance(K, str) or not isinstance(V, str) for K, V in Properties.items()):
            raise FixtureConfigurationError("block-properties-must-be-strings")
        try:
            ValidateFixtureBlockState(State)
        except ValueError as Error:
            raise FixtureConfigurationError(str(Error)) from Error
        Occupied[Key] = State
    Ports = {}
    for SourceKey, DestinationKey in (("inputs", "Inputs"), ("outputs", "Outputs")):
        if not isinstance(Source[SourceKey], list):
            raise FixtureConfigurationError("ports-must-be-lists")
        Names = set()
        Positions = set()
        Ports[DestinationKey] = []
        for Port in Source[SourceKey]:
            Fields(Port, {"name", "position"})
            Name = Port["name"]
            P = tuple(Position(Port["position"]))
            if not isinstance(Name, str) or not Name or Name in Names or P in Positions:
                raise FixtureConfigurationError("duplicate-or-invalid-port")
            if P not in Occupied:
                raise FixtureConfigurationError("port-position-missing")
            if SourceKey == "inputs" and Occupied[P]["Name"] != "minecraft:lever":
                raise FixtureConfigurationError("only-lever-controls-supported")
            Names.add(Name)
            Positions.add(P)
            Ports[DestinationKey].append({"Name": Name, "Position": list(P)})
        Ports[DestinationKey].sort(key=lambda Port: Port["Name"])
    RouteInputs = Source.get("routeInputs", {})
    if not isinstance(RouteInputs, dict) or (RouteInputs and set(RouteInputs) != {P["Name"] for P in Ports["Inputs"]}):
        raise FixtureConfigurationError("route-input-map-must-cover-all-controls")
    for Root in RouteInputs.values():
        P = tuple(Position(Root))
        if Occupied.get(P, {}).get("Name") != "minecraft:redstone_wire":
            raise FixtureConfigurationError("route-input-root-must-be-dust")
    Document = {"Blocks": sorted(Blocks, key=lambda B: B["Position"]), **Ports, "RouteInputs": RouteInputs}
    return LoadedFixture(Document, FixtureIdentity(Document), {
        str(PathValue.resolve()): sha256(PathValue.read_bytes()).hexdigest(),
        str(FixturePath): SourceDigest,
    })

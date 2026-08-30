"""Canonical fixtures consumed by the dedicated Fabric validation server."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from SchemEncoder import SchemWriter


FixtureSchemaVersion = 1


def _Position(Value: tuple[int, int, int]) -> list[int]:
    return [int(Axis) for Axis in Value]


def _TemplateBlockPosition(Gate: Any, TemplateName: str, BlockName: str) -> tuple[int, int, int]:
    """Locate an I/O template block after the exact writer transform."""
    Template = SchemWriter.LoadTemplate(SchemWriter.LitematicTemplates[TemplateName])
    LocalPosition = next(
        Position
        for Position, State in Template.Blocks.items()
        if State["Name"] == BlockName
    )
    X, Y, Z = SchemWriter.TransformLocalPosition(
        LocalPosition,
        (Template.Size[0], Template.Size[2]),
        Gate.Rotation,
        Gate.MirrorX,
    )
    return (Gate.X + X, Gate.Y + Y, Gate.Z + Z)


def BuildFabricFixture(
    *,
    RoutedDesign: Any,
    Rendered: Any,
    Module: Any,
) -> dict[str, object]:
    """Build the server fixture from the same block map used for litematic output.

    The port locations are template-derived, not recovered from signs.  Input
    levers and output lamps are part of the existing I/O cells, so the server
    can drive and sample an unmodified exported circuit.
    """
    Inputs = []
    Outputs = []
    for Gate in sorted(RoutedDesign.PlacedGates, key=lambda Value: Value.Name):
        if Gate.Kind == "INPUT":
            Inputs.append({
                "Name": str(Gate.Outputs[0]),
                "LeverPosition": _Position(_TemplateBlockPosition(
                    Gate, "Input", "minecraft:lever",
                )),
            })
        elif Gate.Kind == "OUTPUT":
            Outputs.append({
                "Name": str(Gate.Outputs[0]),
                "LampPosition": _Position(_TemplateBlockPosition(
                    Gate, "Output", "minecraft:redstone_lamp",
                )),
            })
    if len({Value["Name"] for Value in Inputs}) != len(Inputs):
        raise ValueError("Fabric fixture input names must be unique")
    if len({Value["Name"] for Value in Outputs}) != len(Outputs):
        raise ValueError("Fabric fixture output names must be unique")

    Blocks = [
        {
            "Position": _Position(Position),
            "State": State,
        }
        for Position, State in sorted(Rendered.Blocks.items())
    ]
    return {
        "SchemaVersion": FixtureSchemaVersion,
        "TopModule": str(Module.Name),
        "Blocks": Blocks,
        "Inputs": Inputs,
        "Outputs": Outputs,
        "Arena": {
            "Origin": [0, 64, 0],
            "ResetBeforeLoad": True,
        },
    }


def CanonicalFixtureBytes(Fixture: dict[str, object]) -> bytes:
    """Encode fixtures deterministically for cross-process digest validation."""
    return (json.dumps(
        Fixture,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n").encode("utf-8")


@dataclass(frozen=True)
class FabricFixtureArtifact:
    Path: Path
    Sha256: str
    BlockCount: int
    InputCount: int
    OutputCount: int


def WriteFabricFixture(PathValue: Path, Fixture: dict[str, object]) -> FabricFixtureArtifact:
    """Write one fixture and return the identity the harness must verify."""
    Encoded = CanonicalFixtureBytes(Fixture)
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    PathValue.write_bytes(Encoded)
    return FabricFixtureArtifact(
        Path=PathValue,
        Sha256=sha256(Encoded).hexdigest(),
        BlockCount=len(Fixture["Blocks"]),
        InputCount=len(Fixture["Inputs"]),
        OutputCount=len(Fixture["Outputs"]),
    )

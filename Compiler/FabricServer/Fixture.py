"""Canonical fixtures consumed by the dedicated Fabric validation server."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from SchemEncoder import SchemWriter


FixtureSchemaVersion = 2
TraceSchemaVersion = 1


DynamicTraceBlocks = frozenset({
    "minecraft:comparator",
    "minecraft:lever",
    "minecraft:redstone_lamp",
    "minecraft:redstone_torch",
    "minecraft:redstone_wall_torch",
    "minecraft:redstone_wire",
    "minecraft:repeater",
})


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


def _GateBlockPositions(Gate: Any) -> list[tuple[int, int, int]]:
    """Return every rendered template position owned by one placed gate."""
    Template = SchemWriter.LoadTemplate(SchemWriter.LitematicTemplates[Gate.Kind.title()])
    Positions = []
    for LocalPosition in Template.Blocks:
        X, Y, Z = SchemWriter.TransformLocalPosition(
            LocalPosition,
            (Template.Size[0], Template.Size[2]),
            Gate.Rotation,
            Gate.MirrorX,
        )
        Positions.append((Gate.X + X, Gate.Y + Y, Gate.Z + Z))
    return sorted(Positions)


def _OrderedSignalPositions(
    Positions: list[tuple[int, int, int]] | tuple[tuple[int, int, int], ...],
    Start: tuple[int, int, int] | None,
) -> list[tuple[int, int, int]]:
    """Order a routed net from its producer while retaining disconnected evidence."""
    Remaining = {tuple(Position) for Position in Positions}
    if not Remaining:
        return []
    Seed = Start if Start in Remaining else min(Remaining)
    Queue = [Seed]
    Ordered = []
    Remaining.remove(Seed)
    while Queue:
        Position = Queue.pop(0)
        Ordered.append(Position)
        X, Y, Z = Position
        Neighbors = sorted({
            (X + DeltaX, Y + DeltaY, Z + DeltaZ)
            for DeltaX, DeltaZ in ((-1, 0), (1, 0), (0, -1), (0, 1))
            for DeltaY in (-1, 0, 1)
        } & Remaining)
        for Neighbor in Neighbors:
            Remaining.remove(Neighbor)
            Queue.append(Neighbor)
    return Ordered + sorted(Remaining)


def _BuildTraceMap(
    *,
    RoutedDesign: Any,
    Rendered: Any,
    Module: Any,
) -> dict[str, object]:
    """Describe the flattened circuit hierarchy and exact physical trace probes."""
    GatesByOutput = {
        str(Signal): Gate
        for Gate in RoutedDesign.PlacedGates
        for Signal in getattr(Gate, "Outputs", ())
    }
    ConsumersBySignal: dict[str, list[str]] = {}
    for Gate in RoutedDesign.PlacedGates:
        for Signal in getattr(Gate, "Inputs", ()):
            ConsumersBySignal.setdefault(str(Signal), []).append(str(Gate.Name))

    Gates = []
    ProbePositions: set[tuple[int, int, int]] = set()
    for Gate in sorted(RoutedDesign.PlacedGates, key=lambda Value: Value.Name):
        BlockPositions = _GateBlockPositions(Gate)
        GateProbes = [
            Position
            for Position in BlockPositions
            if Rendered.Blocks.get(Position, {}).get("Name") in DynamicTraceBlocks
        ]
        ProbePositions.update(GateProbes)
        OutputProbe = getattr(Gate, "OutputPin", None)
        if Gate.Kind == "OUTPUT":
            OutputProbe = _TemplateBlockPosition(
                Gate,
                "Output",
                "minecraft:redstone_lamp",
            )
        if OutputProbe not in GateProbes:
            OutputProbe = None
        Gates.append({
            "Name": str(Gate.Name),
            "Kind": str(Gate.Kind),
            "CircuitPath": [str(Module.Name), str(Gate.Name)],
            "Inputs": [str(Signal) for Signal in getattr(Gate, "Inputs", ())],
            "Outputs": [str(Signal) for Signal in getattr(Gate, "Outputs", ())],
            "BlockPositions": [_Position(Position) for Position in BlockPositions],
            "ProbePositions": [_Position(Position) for Position in GateProbes],
            "OutputProbePosition": (
                _Position(OutputProbe) if OutputProbe is not None else None
            ),
        })

    Signals = []
    for Signal, RawPositions in sorted(getattr(RoutedDesign, "NetWires", {}).items()):
        Producer = GatesByOutput.get(str(Signal))
        OrderedPositions = _OrderedSignalPositions(
            RawPositions,
            getattr(Producer, "OutputPin", None),
        )
        SignalProbes = [
            Position
            for Position in OrderedPositions
            if Rendered.Blocks.get(Position, {}).get("Name") in DynamicTraceBlocks
        ]
        ProbePositions.update(SignalProbes)
        Signals.append({
            "Name": str(Signal),
            "ProducerGate": str(Producer.Name) if Producer is not None else None,
            "ConsumerGates": sorted(ConsumersBySignal.get(str(Signal), [])),
            "BlockPositions": [_Position(Position) for Position in OrderedPositions],
            "ProbePositions": [_Position(Position) for Position in SignalProbes],
        })

    return {
        "SchemaVersion": TraceSchemaVersion,
        "Circuit": str(Module.Name),
        "Gates": Gates,
        "Signals": Signals,
        "ProbePositions": [_Position(Position) for Position in sorted(ProbePositions)],
    }


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
        "Trace": _BuildTraceMap(
            RoutedDesign=RoutedDesign,
            Rendered=Rendered,
            Module=Module,
        ),
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

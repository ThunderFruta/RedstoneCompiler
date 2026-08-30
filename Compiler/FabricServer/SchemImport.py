"""Sponge ``.schem`` import for the local Fabric server arena.

The compiler still exports clean ``.litematic`` artifacts.  This module is an
explicit bridge for loading a user-provided Sponge schematic into the local
server through the authenticated harness control plane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from SchemEncoder.SchemWriter import LoadTemplate, NbtValue, ReadNbt


_AirBlocks = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}


def _Tag(Root: dict[str, NbtValue], Name: str) -> Any:
    try:
        return Root[Name].Value
    except KeyError as Error:
        raise ValueError(f"Sponge schematic is missing {Name}") from Error


def _BlockState(Value: str) -> dict[str, object]:
    Name, Separator, PropertiesText = Value.partition("[")
    if not Name or (Separator and not PropertiesText.endswith("]")):
        raise ValueError(f"invalid Sponge palette block state: {Value}")
    State: dict[str, object] = {"Name": Name}
    if Separator:
        Properties: dict[str, str] = {}
        for Item in PropertiesText[:-1].split(","):
            Key, Equals, PropertyValue = Item.partition("=")
            if not Key or not Equals or not PropertyValue:
                raise ValueError(f"invalid Sponge palette property: {Value}")
            Properties[Key] = PropertyValue
        State["Properties"] = Properties
    return State


def _DecodeVarints(Data: bytes, Count: int) -> list[int]:
    Values: list[int] = []
    Offset = 0
    while len(Values) < Count:
        Value = 0
        Shift = 0
        while True:
            if Offset >= len(Data):
                raise ValueError("Sponge BlockData ended before all blocks were decoded")
            Byte = Data[Offset]
            Offset += 1
            Value |= (Byte & 0x7F) << Shift
            if not Byte & 0x80:
                break
            Shift += 7
            if Shift > 28:
                raise ValueError("Sponge BlockData contains an oversized varint")
        Values.append(Value)
    if Offset != len(Data):
        raise ValueError("Sponge BlockData has trailing palette data")
    return Values


def BuildFabricFixtureFromSchem(
    SchemPath: Path,
    *,
    Origin: tuple[int, int, int] = (0, 64, 0),
    ResetBeforeLoad: bool = False,
) -> dict[str, object]:
    """Read a Sponge v2/v3 `.schem` or compiler `.litematic` fixture.

    Sponge block data is ordered X, then Z, then Y.  Tile entities and normal
    entities are deliberately rejected: the validation harness has no safe
    generic NBT entity application contract, so silently dropping them would
    make a Sponge import misleading. Litematic block entities are not loaded;
    compiler circuits use their rendered block states and do not depend on
    sign text for their I/O contract.
    """
    PathValue = Path(SchemPath).expanduser().resolve()
    if PathValue.suffix.lower() == ".litematic":
        Template = LoadTemplate(PathValue)
        return {
            "SchemaVersion": 1,
            "TopModule": PathValue.stem,
            "Blocks": [
                {"Position": list(Position), "State": State}
                for Position, State in sorted(Template.Blocks.items())
            ],
            "Inputs": [],
            "Outputs": [],
            "Arena": {
                "Origin": [int(Value) for Value in Origin],
                "ResetBeforeLoad": bool(ResetBeforeLoad),
                "Bounds": {
                    "Minimum": [0, 0, 0],
                    "Maximum": [Value - 1 for Value in Template.Size],
                },
            },
        }
    if PathValue.suffix.lower() != ".schem":
        raise ValueError(f"expected a .schem or .litematic file: {PathValue}")
    Root = ReadNbt(PathValue)
    Version = int(_Tag(Root, "Version"))
    if Version not in (2, 3):
        raise ValueError(f"unsupported Sponge schematic Version: {Version}")
    Width, Height, Length = (int(_Tag(Root, Axis)) for Axis in ("Width", "Height", "Length"))
    if min(Width, Height, Length) <= 0:
        raise ValueError("Sponge schematic dimensions must be positive")
    PaletteTag = _Tag(Root, "Palette")
    if not isinstance(PaletteTag, dict):
        raise ValueError("Sponge Palette is not a compound")
    Palette = {int(Item.Value): _BlockState(Name) for Name, Item in PaletteTag.items()}
    if len(Palette) != len(PaletteTag) or set(Palette) != set(range(len(Palette))):
        raise ValueError("Sponge Palette indexes must be a contiguous range from zero")
    BlockData = _Tag(Root, "BlockData")
    if not isinstance(BlockData, bytes):
        raise ValueError("Sponge BlockData is not a byte array")
    for Field in ("BlockEntities", "Entities"):
        if Field in Root:
            Tag = Root[Field].Value
            if isinstance(Tag, tuple) and Tag[1]:
                raise ValueError(f"Sponge {Field} are not supported by the Fabric arena importer")
    States = _DecodeVarints(BlockData, Width * Height * Length)
    Blocks = []
    for Index, PaletteIndex in enumerate(States):
        try:
            State = Palette[PaletteIndex]
        except KeyError as Error:
            raise ValueError(f"Sponge BlockData references missing palette index {PaletteIndex}") from Error
        if State["Name"] in _AirBlocks:
            continue
        X = Index % Width
        Z = (Index // Width) % Length
        Y = Index // (Width * Length)
        Blocks.append({"Position": [X, Y, Z], "State": State})
    return {
        "SchemaVersion": 1,
        "TopModule": PathValue.stem,
        "Blocks": Blocks,
        "Inputs": [],
        "Outputs": [],
        "Arena": {
            "Origin": [int(Value) for Value in Origin],
            "ResetBeforeLoad": bool(ResetBeforeLoad),
            "Bounds": {
                "Minimum": [0, 0, 0],
                "Maximum": [Width - 1, Height - 1, Length - 1],
            },
        },
    }

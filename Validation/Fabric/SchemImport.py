"""Sponge ``.schem`` import for the local Fabric server arena.

The compiler still exports clean ``.litematic`` artifacts.  This module is an
explicit bridge for loading a user-provided Sponge schematic into the local
server through the authenticated harness control plane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PhysicalDesign.Rendering.SchemWriter import CellTemplate, LoadTemplate, NbtValue, NeutralDynamicState, ReadNbt


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


def ReadLitematicIoLabels(
    PathValue: Path,
) -> list[tuple[tuple[int, int, int], str, str]]:
    """Read compiler I/O annotations without loading sign block entities.

    These labels are only an import-time bridge for a compiler-emitted
    litematic.  The normal compiler fixture path remains template-derived.
    """
    Root = ReadNbt(PathValue)
    Regions = _Tag(Root, "Regions")
    if not isinstance(Regions, dict) or not Regions:
        raise ValueError("litematic has no regions")
    if len(Regions) != 1:
        raise ValueError("multi-region litematic files are not supported by the Fabric importer")
    Region = next(iter(Regions.values())).Value
    if not isinstance(Region, dict):
        raise ValueError("litematic region is not a compound")
    Size = _Tag(Region, "Size")
    if not isinstance(Size, dict):
        raise ValueError("litematic region has no compound Size")
    SignedSize = tuple(int(_Tag(Size, Axis)) for Axis in ("x", "y", "z"))
    AbsoluteSize = tuple(abs(Value) for Value in SignedSize)
    TileEntities = Region.get("TileEntities")
    if TileEntities is None:
        return []
    if not isinstance(TileEntities, NbtValue):
        raise ValueError("litematic TileEntities is not an NBT value")
    ListType, Values = TileEntities.Value
    if not isinstance(Values, list):
        raise ValueError("litematic TileEntities is not a compound list")
    if not Values:
        return []
    if ListType != 10:
        raise ValueError("litematic TileEntities is not a compound list")
    Labels: list[tuple[tuple[int, int, int], str, str]] = []
    for Entity in Values:
        if not isinstance(Entity, dict):
            raise ValueError("litematic TileEntities contains a non-compound value")
        if _Tag(Entity, "id") != "minecraft:sign":
            continue
        FrontText = _Tag(Entity, "front_text")
        if not isinstance(FrontText, dict):
            raise ValueError("litematic sign front_text is not a compound")
        Messages = _Tag(FrontText, "messages")
        if (
            not isinstance(Messages, tuple)
            or len(Messages) != 2
            or Messages[0] != 8
            or not isinstance(Messages[1], list)
            or not Messages[1]
            or not isinstance(Messages[1][0], str)
        ):
            raise ValueError("litematic sign has invalid front_text messages")
        Text = Messages[1][0].strip()
        Prefix, Separator, Name = Text.partition(" ")
        if Prefix not in {"IN", "OUT"}:
            continue
        if not Separator or not Name.strip():
            raise ValueError(f"litematic I/O label has no signal name: {Text!r}")
        RawPosition = tuple(int(_Tag(Entity, Axis)) for Axis in ("x", "y", "z"))
        Position = tuple(
            RawPosition[Index]
            if SignedSize[Index] > 0
            else AbsoluteSize[Index] - 1 - RawPosition[Index]
            for Index in range(3)
        )
        Labels.append((Position, Prefix, Name.strip()))
    return Labels


def InferLitematicPorts(
    Template: CellTemplate,
    Labels: list[tuple[tuple[int, int, int], str, str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Pair unambiguous compiler I/O labels with the nearest I/O blocks."""
    Candidates = {
        "IN": sorted(
            Position
            for Position, State in Template.Blocks.items()
            if State["Name"] == "minecraft:lever"
        ),
        "OUT": sorted(
            Position
            for Position, State in Template.Blocks.items()
            if State["Name"] == "minecraft:redstone_lamp"
        ),
    }
    Ports: dict[str, list[dict[str, object]]] = {"IN": [], "OUT": []}
    UsedPositions: set[tuple[int, int, int]] = set()
    SeenNames: set[tuple[str, str]] = set()
    for LabelPosition, Prefix, Name in sorted(Labels, key=lambda Value: (Value[1], Value[2], Value[0])):
        Key = (Prefix, Name)
        if Key in SeenNames:
            raise ValueError(f"litematic has duplicate {Prefix} label for {Name}")
        SeenNames.add(Key)
        Distances = sorted(
            (
                sum(abs(Left - Right) for Left, Right in zip(LabelPosition, Position)),
                Position,
            )
            for Position in Candidates[Prefix]
            if Position not in UsedPositions
        )
        if not Distances:
            raise ValueError(f"litematic has no available {Prefix} block for {Name}")
        Distance, Position = Distances[0]
        if len(Distances) > 1 and Distances[1][0] == Distance:
            raise ValueError(f"litematic {Prefix} label for {Name} is ambiguous")
        UsedPositions.add(Position)
        Ports[Prefix].append({
            "Name": Name,
            "LeverPosition" if Prefix == "IN" else "LampPosition": list(Position),
        })
    return (
        sorted(Ports["IN"], key=lambda Value: str(Value["Name"])),
        sorted(Ports["OUT"], key=lambda Value: str(Value["Name"])),
    )


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
    make a Sponge import misleading. For compiler-created litematics, the
    narrow sign-text contract is retained while ``IN`` and ``OUT`` annotations
    also recover the physical test ports. Generic imports without those
    annotations remain loadable but are not testable.
    """
    PathValue = Path(SchemPath).expanduser().resolve()
    if PathValue.suffix.lower() == ".litematic":
        Template = LoadTemplate(PathValue)
        Labels = ReadLitematicIoLabels(PathValue)
        Inputs, Outputs = InferLitematicPorts(
            Template,
            Labels,
        )
        return {
            "SchemaVersion": 1,
            "TopModule": PathValue.stem,
            "Blocks": [
                {
                    "Position": list(Position),
                    "State": NeutralDynamicState(State),
                }
                for Position, State in sorted(Template.Blocks.items())
            ],
            "Signs": [
                {
                    "Position": list(Position),
                    "FrontText": [f"{Prefix} {Name}", "", "", ""],
                    "BackText": [f"{Prefix} {Name}", "", "", ""],
                }
                for Position, Prefix, Name in sorted(Labels)
            ],
            "Inputs": Inputs,
            "Outputs": Outputs,
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
        Blocks.append({
            "Position": [X, Y, Z],
            "State": NeutralDynamicState(State),
        })
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

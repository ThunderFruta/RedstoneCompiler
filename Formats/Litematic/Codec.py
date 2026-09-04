"""Litematica NBT, template, and file codecs."""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import math
from pathlib import Path
import struct
import time
from typing import Any

from PhysicalDesign.Redstone.Technology import ValidateRepeaterInputFacing

LITEMATIC_VERSION = 7
LITEMATIC_SUBVERSION = 1
MINECRAFT_DATA_VERSION = 4903

@dataclass(frozen=True)
class NbtValue:
    TagType: int
    Value: Any


@dataclass
class CellTemplate:
    Size: tuple[int, int, int]
    Blocks: dict[tuple[int, int, int], dict[str, Any]]


def ReadString(Data: bytes, Offset: int) -> tuple[str, int]:
    Length = struct.unpack_from(">H", Data, Offset)[0]
    Offset += 2
    return Data[Offset : Offset + Length].decode("utf-8"), Offset + Length


def ReadPayload(TagType: int, Data: bytes, Offset: int) -> tuple[Any, int]:
    ScalarFormats = {1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}
    if TagType in ScalarFormats:
        Format = ">" + ScalarFormats[TagType]
        return struct.unpack_from(Format, Data, Offset)[0], Offset + struct.calcsize(Format)
    if TagType == 7:
        Length = struct.unpack_from(">i", Data, Offset)[0]
        Offset += 4
        return Data[Offset : Offset + Length], Offset + Length
    if TagType == 8:
        return ReadString(Data, Offset)
    if TagType == 9:
        ChildType = Data[Offset]
        Length = struct.unpack_from(">i", Data, Offset + 1)[0]
        Offset += 5
        Values = []
        for _ in range(Length):
            Value, Offset = ReadPayload(ChildType, Data, Offset)
            Values.append(Value)
        return (ChildType, Values), Offset
    if TagType == 10:
        Values: dict[str, NbtValue] = {}
        while Data[Offset] != 0:
            ChildType = Data[Offset]
            Name, Offset = ReadString(Data, Offset + 1)
            Value, Offset = ReadPayload(ChildType, Data, Offset)
            Values[Name] = NbtValue(ChildType, Value)
        return Values, Offset + 1
    if TagType in (11, 12):
        Length = struct.unpack_from(">i", Data, Offset)[0]
        Offset += 4
        Format = "i" if TagType == 11 else "q"
        Width = 4 if TagType == 11 else 8
        Values = list(struct.unpack_from(f">{Length}{Format}", Data, Offset))
        return Values, Offset + Length * Width
    raise ValueError(f"Unsupported NBT tag type: {TagType}")


def ReadNbt(PathValue: Path) -> dict[str, NbtValue]:
    Data = gzip.decompress(PathValue.read_bytes())
    if Data[0] != 10:
        raise ValueError(f"Litematic root is not an NBT compound: {PathValue}")
    _, Offset = ReadString(Data, 1)
    Root, Offset = ReadPayload(10, Data, Offset)
    if Offset != len(Data):
        raise ValueError(f"Trailing bytes in litematic template: {PathValue}")
    return Root


def CompoundValue(Value: dict[str, NbtValue]) -> dict[str, Any]:
    Result: dict[str, Any] = {}
    for Name, Item in Value.items():
        if Item.TagType == 10:
            Result[Name] = CompoundValue(Item.Value)
        else:
            Result[Name] = Item.Value
    return Result


def UnpackStates(Packed: list[int], Count: int, PaletteSize: int) -> list[int]:
    Bits = max(2, (PaletteSize - 1).bit_length())
    Mask = (1 << Bits) - 1
    Unsigned = [Value & ((1 << 64) - 1) for Value in Packed]
    States = []
    for Index in range(Count):
        BitIndex = Index * Bits
        LongIndex = BitIndex // 64
        StartBit = BitIndex % 64
        Value = Unsigned[LongIndex] >> StartBit
        if StartBit + Bits > 64:
            Value |= Unsigned[LongIndex + 1] << (64 - StartBit)
        States.append(Value & Mask)
    return States


def MirrorTemplateState(
    State: dict[str, Any],
    SignedSize: tuple[int, int, int],
) -> dict[str, Any]:
    """Mirror directional block states with a negative template axis."""
    Properties = dict(State.get("Properties", {}))
    Facing = Properties.get("facing")
    if SignedSize[0] < 0 and Facing in ("east", "west"):
        Properties["facing"] = "west" if Facing == "east" else "east"
    if SignedSize[2] < 0 and Facing in ("north", "south"):
        Properties["facing"] = "south" if Facing == "north" else "north"

    if State["Name"] == "minecraft:redstone_wire":
        if SignedSize[0] < 0:
            Properties["east"], Properties["west"] = (
                Properties.get("west", "none"),
                Properties.get("east", "none"),
            )
        if SignedSize[2] < 0:
            Properties["north"], Properties["south"] = (
                Properties.get("south", "none"),
                Properties.get("north", "none"),
            )

    Result = {"Name": State["Name"]}
    if Properties:
        Result["Properties"] = Properties
    return Result


def LoadTemplate(PathValue: Path) -> CellTemplate:
    Root = ReadNbt(PathValue)
    Regions = Root["Regions"].Value
    Region = next(iter(Regions.values())).Value
    SizeTag = Region["Size"].Value
    SignedSize = tuple(int(SizeTag[Axis].Value) for Axis in ("x", "y", "z"))
    Size = tuple(abs(Value) for Value in SignedSize)
    PaletteTag = Region["BlockStatePalette"].Value
    Palette = [CompoundValue(Value) for Value in PaletteTag[1]]
    Count = Size[0] * Size[1] * Size[2]
    States = UnpackStates(Region["BlockStates"].Value, Count, len(Palette))
    Blocks: dict[tuple[int, int, int], dict[str, Any]] = {}

    for Y in range(Size[1]):
        for Z in range(Size[2]):
            for X in range(Size[0]):
                Index = Y * Size[2] * Size[0] + Z * Size[0] + X
                State = Palette[States[Index]]
                if State["Name"] == "minecraft:air":
                    continue
                NormalX = X if SignedSize[0] > 0 else Size[0] - 1 - X
                NormalY = Y if SignedSize[1] > 0 else Size[1] - 1 - Y
                NormalZ = Z if SignedSize[2] > 0 else Size[2] - 1 - Z
                Blocks[(NormalX, NormalY, NormalZ)] = MirrorTemplateState(
                    State,
                    SignedSize,
                )
    return CellTemplate(Size=Size, Blocks=Blocks)


def CanonicalState(State: dict[str, Any]) -> tuple[Any, ...]:
    Properties = tuple(sorted(State.get("Properties", {}).items()))
    return (State["Name"], Properties)


def NeutralDynamicState(
    State: dict[str, Any],
) -> dict[str, Any]:
    """Emit dynamic redstone blocks without predicting server state."""
    Name = State["Name"]
    Properties = dict(State.get("Properties", {}))
    if Name in ("minecraft:lever", "minecraft:repeater"):
        Properties["powered"] = "false"
    elif Name in (
        "minecraft:redstone_lamp",
        "minecraft:redstone_torch",
        "minecraft:redstone_wall_torch",
    ):
        Properties["lit"] = "false"
    elif Name == "minecraft:redstone_wire":
        Properties["power"] = "0"

    Result = {"Name": Name}
    if Properties:
        Result["Properties"] = Properties
    return Result


def SignText(Text: str) -> dict[str, NbtValue]:
    Messages = [Text, "", "", ""]
    return {
        "color": NbtValue(8, "black"),
        "has_glowing_text": NbtValue(1, 0),
        "messages": NbtValue(9, (8, Messages)),
        "filtered_messages": NbtValue(9, (8, Messages)),
    }


def SignBlockEntity(
    Position: tuple[int, int, int],
    Text: str,
) -> dict[str, NbtValue]:
    X, Y, Z = Position
    return {
        "id": NbtValue(8, "minecraft:sign"),
        "x": NbtValue(3, X),
        "y": NbtValue(3, Y),
        "z": NbtValue(3, Z),
        "front_text": NbtValue(10, SignText(Text)),
        "back_text": NbtValue(10, SignText(Text)),
        "is_waxed": NbtValue(1, 0),
    }


def PackStates(States: list[int], PaletteSize: int) -> list[int]:
    Bits = max(2, (PaletteSize - 1).bit_length())
    Packed = [0] * math.ceil(len(States) * Bits / 64)
    Mask64 = (1 << 64) - 1
    for Index, State in enumerate(States):
        BitIndex = Index * Bits
        LongIndex = BitIndex // 64
        StartBit = BitIndex % 64
        Packed[LongIndex] |= State << StartBit
        Packed[LongIndex] &= Mask64
        if StartBit + Bits > 64:
            Packed[LongIndex + 1] |= State >> (64 - StartBit)
            Packed[LongIndex + 1] &= Mask64
    return [Value if Value < (1 << 63) else Value - (1 << 64) for Value in Packed]


def EncodeString(Value: str) -> bytes:
    Data = Value.encode("utf-8")
    return struct.pack(">H", len(Data)) + Data


def EncodePayload(TagType: int, Value: Any) -> bytes:
    ScalarFormats = {1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}
    if TagType in ScalarFormats:
        return struct.pack(">" + ScalarFormats[TagType], Value)
    if TagType == 7:
        return struct.pack(">i", len(Value)) + bytes(Value)
    if TagType == 8:
        return EncodeString(Value)
    if TagType == 9:
        ChildType, Values = Value
        return bytes([ChildType]) + struct.pack(">i", len(Values)) + b"".join(
            EncodePayload(ChildType, Item) for Item in Values
        )
    if TagType == 10:
        Parts = []
        for Name, Item in Value.items():
            Parts.append(bytes([Item.TagType]) + EncodeString(Name) + EncodePayload(Item.TagType, Item.Value))
        return b"".join(Parts) + b"\x00"
    if TagType in (11, 12):
        Format = "i" if TagType == 11 else "q"
        return struct.pack(">i", len(Value)) + struct.pack(f">{len(Value)}{Format}", *Value)
    raise ValueError(f"Unsupported NBT tag type: {TagType}")


def StateTag(State: dict[str, Any]) -> dict[str, NbtValue]:
    Result = {"Name": NbtValue(8, State["Name"])}
    if State.get("Properties"):
        Result["Properties"] = NbtValue(
            10,
            {
                Name: NbtValue(8, Value)
                for Name, Value in State["Properties"].items()
            },
        )
    return Result


def ValidateSerializedRepeaterOrientations(
    PathValue: Path,
    Build: LitematicBuild,
) -> dict[str, Any]:
    """Read back one litematic and require its repeater states to match intent."""
    Expected = {
        tuple(Record["SerializedPosition"]): Record["InputFacing"]
        for Record in Build.RepeaterOrientation.get("Records", ())
    }
    Serialized = LoadTemplate(PathValue)
    Actual = {
        Position: ValidateRepeaterInputFacing(
            str(State.get("Properties", {}).get("facing", ""))
        )
        for Position, State in Serialized.Blocks.items()
        if State["Name"] == "minecraft:repeater"
    }
    if Actual != Expected:
        Mismatches = [
            {
                "Position": list(Position),
                "Expected": Expected.get(Position),
                "Actual": Actual.get(Position),
            }
            for Position in sorted(set(Expected) | set(Actual))
            if Expected.get(Position) != Actual.get(Position)
        ]
        raise ValueError(
            "Serialized repeater orientation audit failed: "
            f"{Mismatches[:8]}"
        )
    Build.RepeaterOrientation.update({
        "ReadbackPassed": True,
        "ReadbackCount": len(Actual),
        "ReadbackMismatchCount": 0,
    })
    return Build.RepeaterOrientation

def WriteLitematic(
    RoutedDesign: Any,
    OutputPath: Path,
    Build: Any | None = None,
) -> LitematicBuild:
    """Serialize one canonical rendered block map to a litematic region."""
    if Build is None:
        from PhysicalDesign.Rendering.Renderer import BuildLitematicBlockMap
        Build = BuildLitematicBlockMap(RoutedDesign)
    Blocks = Build.Blocks
    Signs = Build.Signs

    if not Blocks:
        raise ValueError("Cannot write an empty litematic")
    MinX = min(Position[0] for Position in Blocks)
    MinY = min(Position[1] for Position in Blocks)
    MinZ = min(Position[2] for Position in Blocks)
    MaxX = max(Position[0] for Position in Blocks)
    MaxY = max(Position[1] for Position in Blocks)
    MaxZ = max(Position[2] for Position in Blocks)
    SizeX, SizeY, SizeZ = MaxX - MinX + 1, MaxY - MinY + 1, MaxZ - MinZ + 1

    AirState = {"Name": "minecraft:air"}
    Palette = [AirState]
    PaletteIndexes = {CanonicalState(AirState): 0}
    for State in Blocks.values():
        Key = CanonicalState(State)
        if Key not in PaletteIndexes:
            PaletteIndexes[Key] = len(Palette)
            Palette.append(State)

    States = [0] * (SizeX * SizeY * SizeZ)
    for (X, Y, Z), State in Blocks.items():
        Index = (Y - MinY) * SizeZ * SizeX + (Z - MinZ) * SizeX + (X - MinX)
        States[Index] = PaletteIndexes[CanonicalState(State)]

    EmptyCompoundList = NbtValue(9, (10, []))
    TileEntities = [
        SignBlockEntity(
            (
                Position[0] - MinX,
                Position[1] - MinY,
                Position[2] - MinZ,
            ),
            Text,
        )
        for Position, Text in Signs
    ]
    Region = {
        "Size": NbtValue(
            10,
            {
                "x": NbtValue(3, SizeX),
                "y": NbtValue(3, SizeY),
                "z": NbtValue(3, SizeZ),
            },
        ),
        "Position": NbtValue(
            10,
            {"x": NbtValue(3, 0), "y": NbtValue(3, 0), "z": NbtValue(3, 0)},
        ),
        "BlockStatePalette": NbtValue(9, (10, [StateTag(State) for State in Palette])),
        "BlockStates": NbtValue(12, PackStates(States, len(Palette))),
        "Entities": EmptyCompoundList,
        "TileEntities": NbtValue(9, (10, TileEntities)),
        "PendingBlockTicks": EmptyCompoundList,
        "PendingFluidTicks": EmptyCompoundList,
    }
    Timestamp = int(time.time() * 1000)
    Name = OutputPath.stem
    Root = {
        "Regions": NbtValue(10, {"RedstoneCompiler": NbtValue(10, Region)}),
        "SubVersion": NbtValue(3, LITEMATIC_SUBVERSION),
        "Metadata": NbtValue(
            10,
            {
                "Description": NbtValue(8, "Generated by RedstoneCompiler"),
                "TimeModified": NbtValue(4, Timestamp),
                "TimeCreated": NbtValue(4, Timestamp),
                "TotalVolume": NbtValue(3, SizeX * SizeY * SizeZ),
                "Name": NbtValue(8, Name),
                "Author": NbtValue(8, "RedstoneCompiler"),
                "TotalBlocks": NbtValue(3, len(Blocks)),
                "EnclosingSize": NbtValue(
                    10,
                    {
                        "x": NbtValue(3, SizeX),
                        "y": NbtValue(3, SizeY),
                        "z": NbtValue(3, SizeZ),
                    },
                ),
                "RegionCount": NbtValue(3, 1),
            },
        ),
        "Version": NbtValue(3, LITEMATIC_VERSION),
        "MinecraftDataVersion": NbtValue(3, MINECRAFT_DATA_VERSION),
    }
    OutputPath.parent.mkdir(parents=True, exist_ok=True)
    Data = bytes([10]) + EncodeString("") + EncodePayload(10, Root)
    OutputPath.write_bytes(gzip.compress(Data))
    ValidateSerializedRepeaterOrientations(OutputPath, Build)
    return Build


def WriteObservedLitematic(
    Blocks: dict[tuple[int, int, int], dict[str, Any]],
    OutputPath: Path,
    *,
    Bounds: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None,
    Signs: list[tuple[tuple[int, int, int], str]] | None = None,
) -> None:
    """Write the block states observed in a live Minecraft world.

    This is intentionally separate from :func:`WriteLitematic`.  The normal
    compiler writer proves the repeater-orientation contract against its
    routed design.  A server snapshot is authoritative for dynamic block
    properties such as redstone power and lamp state, so it must preserve
    those values without reasserting compile-time repeater expectations.

    ``Bounds`` preserves the fixture's full rectangular arena even when a
    block update removed an outermost block.  Positions are local fixture
    positions and are normalized to a litematic region during serialization.
    """
    AirNames = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
    NormalizedBlocks: dict[tuple[int, int, int], dict[str, Any]] = {}
    for RawPosition, RawState in Blocks.items():
        if (
            not isinstance(RawPosition, tuple)
            or len(RawPosition) != 3
            or not all(isinstance(Value, int) for Value in RawPosition)
        ):
            raise ValueError(f"observed litematic block has invalid position: {RawPosition!r}")
        if not isinstance(RawState, dict):
            raise ValueError(f"observed litematic block at {RawPosition} has no state compound")
        Name = RawState.get("Name")
        if not isinstance(Name, str) or not Name:
            raise ValueError(f"observed litematic block at {RawPosition} has invalid Name")
        RawProperties = RawState.get("Properties", {})
        if not isinstance(RawProperties, dict) or not all(
            isinstance(Key, str) and isinstance(Value, str)
            for Key, Value in RawProperties.items()
        ):
            raise ValueError(
                f"observed litematic block at {RawPosition} has invalid Properties",
            )
        if Name in AirNames:
            continue
        State: dict[str, Any] = {"Name": Name}
        if RawProperties:
            State["Properties"] = dict(sorted(RawProperties.items()))
        NormalizedBlocks[RawPosition] = State

    if Bounds is None:
        if not NormalizedBlocks:
            raise ValueError("Cannot write an empty observed litematic without bounds")
        Minimum = tuple(
            min(Position[Axis] for Position in NormalizedBlocks)
            for Axis in range(3)
        )
        Maximum = tuple(
            max(Position[Axis] for Position in NormalizedBlocks)
            for Axis in range(3)
        )
    else:
        if len(Bounds) != 2:
            raise ValueError("observed litematic bounds must contain minimum and maximum positions")
        Minimum, Maximum = Bounds
        if (
            len(Minimum) != 3
            or len(Maximum) != 3
            or not all(isinstance(Value, int) for Value in (*Minimum, *Maximum))
            or any(Minimum[Axis] > Maximum[Axis] for Axis in range(3))
        ):
            raise ValueError(f"observed litematic bounds are invalid: {Bounds!r}")
        for Position in NormalizedBlocks:
            if any(
                Position[Axis] < Minimum[Axis] or Position[Axis] > Maximum[Axis]
                for Axis in range(3)
            ):
                raise ValueError(
                    f"observed litematic block {Position} lies outside bounds {Bounds}",
                )

    MinX, MinY, MinZ = Minimum
    MaxX, MaxY, MaxZ = Maximum
    SizeX, SizeY, SizeZ = MaxX - MinX + 1, MaxY - MinY + 1, MaxZ - MinZ + 1
    AirState = {"Name": "minecraft:air"}
    Palette = [AirState]
    PaletteIndexes = {CanonicalState(AirState): 0}
    for Position in sorted(NormalizedBlocks):
        State = NormalizedBlocks[Position]
        Key = CanonicalState(State)
        if Key not in PaletteIndexes:
            PaletteIndexes[Key] = len(Palette)
            Palette.append(State)

    States = [0] * (SizeX * SizeY * SizeZ)
    for (X, Y, Z), State in NormalizedBlocks.items():
        Index = (Y - MinY) * SizeZ * SizeX + (Z - MinZ) * SizeX + (X - MinX)
        States[Index] = PaletteIndexes[CanonicalState(State)]

    TileEntities = []
    for Position, Text in sorted(Signs or []):
        if (
            not isinstance(Position, tuple)
            or len(Position) != 3
            or not all(isinstance(Value, int) for Value in Position)
            or not isinstance(Text, str)
        ):
            raise ValueError(f"observed litematic sign is invalid: {(Position, Text)!r}")
        if any(
            Position[Axis] < Minimum[Axis] or Position[Axis] > Maximum[Axis]
            for Axis in range(3)
        ):
            raise ValueError(f"observed litematic sign {Position} lies outside bounds {Bounds}")
        State = NormalizedBlocks.get(Position)
        if State is None or not str(State["Name"]).endswith("_sign"):
            continue
        TileEntities.append(SignBlockEntity(
            (
                Position[0] - MinX,
                Position[1] - MinY,
                Position[2] - MinZ,
            ),
            Text,
        ))

    EmptyCompoundList = NbtValue(9, (10, []))
    Region = {
        "Size": NbtValue(
            10,
            {
                "x": NbtValue(3, SizeX),
                "y": NbtValue(3, SizeY),
                "z": NbtValue(3, SizeZ),
            },
        ),
        "Position": NbtValue(
            10,
            {"x": NbtValue(3, 0), "y": NbtValue(3, 0), "z": NbtValue(3, 0)},
        ),
        "BlockStatePalette": NbtValue(9, (10, [StateTag(State) for State in Palette])),
        "BlockStates": NbtValue(12, PackStates(States, len(Palette))),
        "Entities": EmptyCompoundList,
        "TileEntities": NbtValue(9, (10, TileEntities)),
        "PendingBlockTicks": EmptyCompoundList,
        "PendingFluidTicks": EmptyCompoundList,
    }
    Timestamp = int(time.time() * 1000)
    OutputPath = Path(OutputPath)
    Root = {
        "Regions": NbtValue(10, {"RedstoneCompilerServerSnapshot": NbtValue(10, Region)}),
        "SubVersion": NbtValue(3, LITEMATIC_SUBVERSION),
        "Metadata": NbtValue(
            10,
            {
                "Description": NbtValue(
                    8,
                    "Captured from the RedstoneCompiler Fabric server after block updates",
                ),
                "TimeModified": NbtValue(4, Timestamp),
                "TimeCreated": NbtValue(4, Timestamp),
                "TotalVolume": NbtValue(3, SizeX * SizeY * SizeZ),
                "Name": NbtValue(8, OutputPath.stem),
                "Author": NbtValue(8, "RedstoneCompiler Fabric server"),
                "TotalBlocks": NbtValue(3, len(NormalizedBlocks)),
                "EnclosingSize": NbtValue(
                    10,
                    {
                        "x": NbtValue(3, SizeX),
                        "y": NbtValue(3, SizeY),
                        "z": NbtValue(3, SizeZ),
                    },
                ),
                "RegionCount": NbtValue(3, 1),
            },
        ),
        "Version": NbtValue(3, LITEMATIC_VERSION),
        "MinecraftDataVersion": NbtValue(3, MINECRAFT_DATA_VERSION),
    }
    OutputPath.parent.mkdir(parents=True, exist_ok=True)
    Data = bytes([10]) + EncodeString("") + EncodePayload(10, Root)
    OutputPath.write_bytes(gzip.compress(Data))


def WriteSchem(RoutedDesign: Any, OutputPath: Path) -> None:
    """Compatibility wrapper for the original writer name."""
    WriteLitematic(RoutedDesign, OutputPath)

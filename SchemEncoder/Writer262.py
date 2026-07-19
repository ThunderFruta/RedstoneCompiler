"""Self-contained Litematica NBT reader and writer."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from enum import Enum
import gzip
import math
from pathlib import Path
import struct
import time
from typing import Any

from Compiler.Placement.Rotation import (
    RotatedCellSize,
    TransformBlockState,
    TransformLocalPosition,
)
from Templates import LitematicTemplates


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


class BlockProvenance(str, Enum):
    """Stable ownership class for every emitted non-air block."""

    NandCell = "NandCell"
    IoCell = "IoCell"
    RouteSignal = "RouteSignal"
    RouteRefresh = "RouteRefresh"
    RouteSupport = "RouteSupport"
    Annotation = "Annotation"


@dataclass(frozen=True)
class BlockCompositionMetrics:
    """Exact material and ownership counts from the final emitted block map."""

    NonAirBlocks: int
    FunctionalBlocks: int
    ComponentOwnedFunctionalBlocks: int
    RoutingOwnedFunctionalBlocks: int
    SupportBlocks: int
    AnnotationBlocks: int
    RawDustBlocks: int
    ComponentFunctionalShare: float
    RoutingFunctionalShare: float
    RawDustFunctionalShare: float
    RawDustAllBlockShare: float
    Width: int
    Height: int
    Depth: int
    Footprint: int
    MaterialCounts: dict[str, int]
    ProvenanceCounts: dict[str, int]

    def ToDictionary(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LitematicBuild:
    """Canonical rendered blocks shared by diagnostics and serialization."""

    Blocks: dict[tuple[int, int, int], dict[str, Any]]
    Provenance: dict[tuple[int, int, int], BlockProvenance]
    Signs: list[tuple[tuple[int, int, int], str]]
    Composition: BlockCompositionMetrics


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


def OrientCellState(
    State: dict[str, Any],
    Rotation: int,
    MirrorX: bool = False,
) -> dict[str, Any]:
    """Mirror and rotate a template block state with its containing cell."""
    return TransformBlockState(State, Rotation, MirrorX)


def SimulateDefaultSignals(RoutedDesign: Any) -> dict[str, bool]:
    """Evaluate the NAND IR with every input lever in its default off state."""
    SignalValues: dict[str, bool] = {}
    for Gate in RoutedDesign.Module.Gates:
        if Gate.Kind.value == "INPUT":
            SignalValues[Gate.Output] = False
        elif Gate.Kind.value == "NAND":
            SignalValues[Gate.Output] = not all(
                SignalValues[Signal] for Signal in Gate.Inputs
            )
        elif Gate.Kind.value == "OUTPUT":
            SignalValues[Gate.Output] = SignalValues[Gate.Inputs[0]]
    return SignalValues


def PoweredCellState(
    State: dict[str, Any],
    Gate: Any,
    LocalPosition: tuple[int, int, int],
    SignalValues: dict[str, bool],
) -> dict[str, Any]:
    """Apply the simulated initial logic state to one template block."""
    Name = State["Name"]
    Properties = dict(State.get("Properties", {}))
    X, _, Z = LocalPosition

    if Gate.Kind == "INPUT":
        Value = SignalValues[Gate.Outputs[0]]
        if Name in ("minecraft:lever", "minecraft:repeater"):
            Properties["powered"] = str(Value).lower()
        elif Name == "minecraft:redstone_lamp":
            Properties["lit"] = str(Value).lower()
    elif Gate.Kind == "OUTPUT":
        Value = SignalValues[Gate.Inputs[0]]
        if Name == "minecraft:repeater":
            Properties["powered"] = str(Value).lower()
        elif Name == "minecraft:redstone_lamp":
            Properties["lit"] = str(Value).lower()
    elif Gate.Kind == "NAND":
        OutputValue = SignalValues[Gate.Outputs[0]]
        if Name == "minecraft:repeater":
            if Z == 0:
                InputIndex = 0 if X == 0 else 1
                Properties["powered"] = str(
                    SignalValues[Gate.Inputs[InputIndex]]
                ).lower()
            else:
                Properties["powered"] = str(OutputValue).lower()
        elif Name == "minecraft:redstone_wall_torch":
            InputIndex = 0 if X == 0 else 1
            Properties["lit"] = str(
                not SignalValues[Gate.Inputs[InputIndex]]
            ).lower()
        elif Name == "minecraft:redstone_wire":
            Properties["power"] = "15" if OutputValue else "0"

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


def BuildWireState(
    Position: tuple[int, int, int],
    NetCells: set[tuple[int, int, int]],
    Blocks: dict[tuple[int, int, int], dict[str, Any]],
    Power: int,
) -> dict[str, Any]:
    """Build the exact redstone-wire shape required at one routed position."""
    X, Y, Z = Position
    Connections = {}
    NonSolidNames = {
        None,
        "minecraft:air",
        "minecraft:comparator",
        "minecraft:lever",
        "minecraft:redstone_torch",
        "minecraft:redstone_wall_torch",
        "minecraft:redstone_wire",
        "minecraft:repeater",
    }
    for Direction, DeltaX, DeltaZ in (
        ("north", 0, -1),
        ("south", 0, 1),
        ("east", 1, 0),
        ("west", -1, 0),
    ):
        SameLevel = (X + DeltaX, Y, Z + DeltaZ)
        UpperLevel = (X + DeltaX, Y + 1, Z + DeltaZ)
        LowerLevel = (X + DeltaX, Y - 1, Z + DeltaZ)
        NeighborName = Blocks.get(SameLevel, {}).get("Name")
        HeadName = Blocks.get((X, Y + 1, Z), {}).get("Name")
        CanClimb = (
            NeighborName not in NonSolidNames
            and HeadName not in (
                "minecraft:redstone_wire",
                "minecraft:repeater",
            )
            and HeadName in NonSolidNames
        )
        if UpperLevel in NetCells and CanClimb:
            Connections[Direction] = "up"
        elif (
            SameLevel in NetCells
            or LowerLevel in NetCells
            or NeighborName == "minecraft:repeater"
        ):
            Connections[Direction] = "side"
        else:
            Connections[Direction] = "none"

    return {
        "Name": "minecraft:redstone_wire",
        "Properties": {
            "power": str(Power),
            **Connections,
        },
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


def BuildLitematicBlockMap(RoutedDesign: Any) -> LitematicBuild:
    """Render the exact final block map and retain ownership provenance."""
    Templates = {
        Name.upper(): LoadTemplate(PathValue)
        for Name, PathValue in LitematicTemplates.items()
    }
    Blocks: dict[tuple[int, int, int], dict[str, Any]] = {}
    Provenance: dict[tuple[int, int, int], BlockProvenance] = {}
    Signs: list[tuple[tuple[int, int, int], str]] = []
    RoutedPositions = set(RoutedDesign.Wires)
    SignalValues = SimulateDefaultSignals(RoutedDesign)

    for Gate in RoutedDesign.PlacedGates:
        Template = Templates[Gate.Kind]
        for (X, Y, Z), State in Template.Blocks.items():
            State = PoweredCellState(
                State,
                Gate,
                (X, Y, Z),
                SignalValues,
            )
            LocalPosition = TransformLocalPosition(
                (X, Y, Z),
                (Template.Size[0], Template.Size[2]),
                Gate.Rotation,
                Gate.MirrorX,
            )
            State = OrientCellState(State, Gate.Rotation, Gate.MirrorX)
            Position = (
                Gate.X + LocalPosition[0],
                Gate.Y + LocalPosition[1],
                Gate.Z + LocalPosition[2],
            )
            Blocks[Position] = State
            Provenance[Position] = (
                BlockProvenance.NandCell
                if Gate.Kind == "NAND"
                else BlockProvenance.IoCell
            )
        # Raised I/O signs are intentionally omitted: flat layouts guarantee
        # one electrical layer above one support floor.
    def IsOccupiedByGate(Position: tuple[int, int, int]) -> bool:
        X, _, Z = Position
        return any(
            Gate.X <= X < Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            and Gate.Z <= Z < Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in RoutedDesign.PlacedGates
        )

    for Gate in RoutedDesign.PlacedGates:
        if Gate.Kind not in ("INPUT", "OUTPUT"):
            continue
        if not Gate.Outputs:
            continue
        SignalName = (
            Gate.Outputs[0]
            if Gate.Kind == "INPUT"
            else Gate.Inputs[0]
        )
        Prefix = "IN" if Gate.Kind == "INPUT" else "OUT"
        if Gate.Kind == "INPUT":
            if Gate.OutputPin is None or Gate.OutputDirection is None:
                continue
            Pin = Gate.OutputPin
            PrimaryDirection = Gate.OutputDirection
        else:
            if not Gate.InputPins or not Gate.InputDirections:
                continue
            Pin = Gate.InputPins[0]
            PrimaryDirection = (
                -Gate.InputDirections[0][0],
                -Gate.InputDirections[0][1],
                -Gate.InputDirections[0][2],
            )
        PinX, PinY, PinZ = Pin
        SearchOffsets = [
            (PrimaryDirection[0], PrimaryDirection[2]),
            (0, 0),
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1, 1),
            (1, -1),
            (-1, 1),
            (-1, -1),
        ]
        SignPosition = None
        for Radius in range(4):
            for OffsetX, OffsetZ in SearchOffsets:
                Candidate = (
                    PinX + OffsetX * (Radius + 1),
                    PinY,
                    PinZ + OffsetZ * (Radius + 1),
                )
                if Candidate in Blocks:
                    continue
                if Candidate in RoutedPositions:
                    continue
                if IsOccupiedByGate(Candidate):
                    continue
                SignPosition = Candidate
                break
            if SignPosition is not None:
                break
        if SignPosition is not None:
            Signs.append((SignPosition, f"{Prefix} {SignalName}"))

    SupportBlock = getattr(
        RoutedDesign,
        "SupportBlock",
        "minecraft:smooth_stone",
    )
    if not SupportBlock:
        SupportBlock = "minecraft:smooth_stone"
    SupportState = {"Name": SupportBlock}

    # Support only components that require a floor block. Solid template
    # blocks and wall-mounted components do not need a full rectangular slab.
    for Gate in RoutedDesign.PlacedGates:
        Template = Templates[Gate.Kind]
        for (X, Y, Z), State in Template.Blocks.items():
            if State["Name"] in (
                "minecraft:repeater",
                "minecraft:redstone_wire",
            ):
                LocalPosition = TransformLocalPosition(
                    (X, Y, Z),
                    (Template.Size[0], Template.Size[2]),
                    Gate.Rotation,
                    Gate.MirrorX,
                )
                SupportPosition = (
                    Gate.X + LocalPosition[0],
                    Gate.Y + LocalPosition[1] - 1,
                    Gate.Z + LocalPosition[2],
                )
                if SupportPosition not in Blocks:
                    Blocks[SupportPosition] = SupportState
                    Provenance[SupportPosition] = BlockProvenance.RouteSupport

    for Position in RoutedDesign.Supports:
        if Position not in Blocks:
            Blocks[Position] = SupportState
            Provenance[Position] = BlockProvenance.RouteSupport

    for Position, _ in Signs:
        SupportPosition = (Position[0], Position[1] - 1, Position[2])
        if SupportPosition not in Blocks:
            Blocks[SupportPosition] = SupportState
            Provenance[SupportPosition] = BlockProvenance.RouteSupport
        if Position not in Blocks:
            Blocks[Position] = {"Name": "minecraft:oak_sign"}
            Provenance[Position] = BlockProvenance.Annotation

    for Signal, Positions in RoutedDesign.NetWires.items():
        NetCells = set(Positions)
        Power = 15 if SignalValues[Signal] else 0
        for Position in NetCells:
            if Position in RoutedDesign.Repeaters:
                continue
            Blocks[Position] = BuildWireState(
                Position,
                NetCells,
                Blocks,
                Power,
            )
            Provenance.setdefault(Position, BlockProvenance.RouteSignal)

    for Position, Facing in RoutedDesign.Repeaters.items():
        Signal = next(
            Name
            for Name, Positions in RoutedDesign.NetWires.items()
            if Position in Positions
        )
        Blocks[Position] = {
            "Name": "minecraft:repeater",
            "Properties": {
                "powered": str(SignalValues[Signal]).lower(),
                "facing": Facing,
                "locked": "false",
                "delay": "1",
            },
        }
        Provenance.setdefault(Position, BlockProvenance.RouteRefresh)

    if not Blocks:
        raise ValueError("Cannot build an empty litematic")
    MinimumX = min(Position[0] for Position in Blocks)
    MinimumY = min(Position[1] for Position in Blocks)
    MinimumZ = min(Position[2] for Position in Blocks)
    MaximumX = max(Position[0] for Position in Blocks)
    MaximumY = max(Position[1] for Position in Blocks)
    MaximumZ = max(Position[2] for Position in Blocks)
    Width = MaximumX - MinimumX + 1
    Height = MaximumY - MinimumY + 1
    Depth = MaximumZ - MinimumZ + 1
    Materials = Counter(State["Name"] for State in Blocks.values())
    Ownership = Counter(Value.value for Value in Provenance.values())
    AnnotationBlocks = Ownership[BlockProvenance.Annotation.value]
    SupportBlocks = sum(
        1
        for Position, State in Blocks.items()
        if State["Name"] == SupportBlock
        and Provenance[Position] != BlockProvenance.Annotation
    )
    FunctionalPositions = {
        Position
        for Position, State in Blocks.items()
        if State["Name"] != SupportBlock
        and Provenance[Position] != BlockProvenance.Annotation
    }
    ComponentProvenance = {
        BlockProvenance.NandCell,
        BlockProvenance.IoCell,
    }
    RoutingProvenance = {
        BlockProvenance.RouteSignal,
        BlockProvenance.RouteRefresh,
    }
    ComponentBlocks = sum(
        Provenance[Position] in ComponentProvenance
        for Position in FunctionalPositions
    )
    RoutingBlocks = sum(
        Provenance[Position] in RoutingProvenance
        for Position in FunctionalPositions
    )
    FunctionalBlocks = len(FunctionalPositions)
    RawDustBlocks = Materials["minecraft:redstone_wire"]
    Composition = BlockCompositionMetrics(
        NonAirBlocks=len(Blocks),
        FunctionalBlocks=FunctionalBlocks,
        ComponentOwnedFunctionalBlocks=ComponentBlocks,
        RoutingOwnedFunctionalBlocks=RoutingBlocks,
        SupportBlocks=SupportBlocks,
        AnnotationBlocks=AnnotationBlocks,
        RawDustBlocks=RawDustBlocks,
        ComponentFunctionalShare=(
            ComponentBlocks / FunctionalBlocks if FunctionalBlocks else 0.0
        ),
        RoutingFunctionalShare=(
            RoutingBlocks / FunctionalBlocks if FunctionalBlocks else 0.0
        ),
        RawDustFunctionalShare=(
            RawDustBlocks / FunctionalBlocks if FunctionalBlocks else 0.0
        ),
        RawDustAllBlockShare=RawDustBlocks / len(Blocks),
        Width=Width,
        Height=Height,
        Depth=Depth,
        Footprint=Width * Depth,
        MaterialCounts=dict(sorted(Materials.items())),
        ProvenanceCounts=dict(sorted(Ownership.items())),
    )
    return LitematicBuild(
        Blocks=Blocks,
        Provenance=Provenance,
        Signs=Signs,
        Composition=Composition,
    )


def WriteLitematic(
    RoutedDesign: Any,
    OutputPath: Path,
    Build: LitematicBuild | None = None,
) -> LitematicBuild:
    """Serialize one canonical rendered block map to a litematic region."""
    Build = Build or BuildLitematicBlockMap(RoutedDesign)
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
    return Build


def WriteSchem(RoutedDesign: Any, OutputPath: Path) -> None:
    """Compatibility wrapper for the original writer name."""
    WriteLitematic(RoutedDesign, OutputPath)

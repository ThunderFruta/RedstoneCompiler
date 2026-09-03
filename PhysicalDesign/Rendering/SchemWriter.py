"""Self-contained Litematica NBT reader and writer."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
import gzip
import math
from pathlib import Path
import struct
import time
from typing import Any

from PhysicalDesign.Cells.Library import CellMacros
from PhysicalDesign.Geometry.Rotation import RotatedCellSize, TransformBlockState, TransformLocalPosition
from PhysicalDesign.Redstone.Technology import OppositeHorizontalFacing, ValidateRepeaterInputFacing
from Assets.Templates import LitematicTemplates


LITEMATIC_VERSION = 7
LITEMATIC_SUBVERSION = 1
MINECRAFT_DATA_VERSION = 4903
TRACE_PALETTE = (
    "minecraft:light_gray_concrete",
    "minecraft:yellow_concrete",
    "minecraft:lime_concrete",
    "minecraft:light_blue_concrete",
    "minecraft:red_concrete",
    "minecraft:orange_concrete",
    "minecraft:magenta_concrete",
)


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
    XYFootprint: int
    Footprint: int
    FullFootprint: int
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
    RepeaterOptimization: dict[str, Any] = field(default_factory=dict)
    RepeaterOrientation: dict[str, Any] = field(default_factory=dict)


def _PlaceIoSigns(
    RoutedDesign: Any,
    Blocks: dict[tuple[int, int, int], dict[str, Any]],
    Provenance: dict[tuple[int, int, int], BlockProvenance],
    SupportState: dict[str, Any],
    SupportSignalByPosition: dict[tuple[int, int, int], str],
    NetSignalColors: dict[str, str],
) -> list[tuple[tuple[int, int, int], str]]:
    """Place one supported, non-overwritten sign for every routed I/O cell."""
    Signs: list[tuple[tuple[int, int, int], str]] = []
    NonSupportingBlocks = {
        "minecraft:air",
        "minecraft:lever",
        "minecraft:oak_sign",
        "minecraft:redstone_torch",
        "minecraft:redstone_wall_torch",
        "minecraft:redstone_wire",
        "minecraft:repeater",
    }
    # Capture the physical design envelope before annotations.  I/O labels
    # are useful metadata, but they should not widen or deepen an otherwise
    # compact routed build when a legal in-envelope position exists.
    MinimumX = min(Position[0] for Position in Blocks)
    MaximumX = max(Position[0] for Position in Blocks)
    MinimumY = min(Position[1] for Position in Blocks)
    MaximumY = max(Position[1] for Position in Blocks)
    MinimumZ = min(Position[2] for Position in Blocks)
    MaximumZ = max(Position[2] for Position in Blocks)
    BaseFootprint = (MaximumX - MinimumX + 1) * (MaximumZ - MinimumZ + 1)

    def IsOccupiedByGate(Position: tuple[int, int, int]) -> bool:
        X, _Y, Z = Position
        return any(
            Gate.X <= X < Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            and Gate.Z <= Z < Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in RoutedDesign.PlacedGates
        )

    def CanPlace(Position: tuple[int, int, int]) -> bool:
        if Position in Blocks or IsOccupiedByGate(Position):
            return False
        SupportPosition = (Position[0], Position[1] - 1, Position[2])
        Support = Blocks.get(SupportPosition)
        return Support is None or Support["Name"] not in NonSupportingBlocks

    def LocalCandidatePositions(
        Pin: tuple[int, int, int],
        PrimaryDirection: tuple[int, int, int],
    ):
        PinX, PinY, PinZ = Pin
        Preferred = (PrimaryDirection[0], PrimaryDirection[2])
        for Radius in range(1, 9):
            Ring = [
                (DeltaX, DeltaZ)
                for DeltaX in range(-Radius, Radius + 1)
                for DeltaZ in range(-Radius, Radius + 1)
                if max(abs(DeltaX), abs(DeltaZ)) == Radius
            ]
            Ring.sort(
                key=lambda Offset: (
                    Offset != (Preferred[0] * Radius, Preferred[1] * Radius),
                    abs(Offset[0] - Preferred[0] * Radius)
                    + abs(Offset[1] - Preferred[1] * Radius),
                    Offset,
                )
            )
            for DeltaX, DeltaZ in Ring:
                yield (PinX + DeltaX, PinY, PinZ + DeltaZ)

    def ExteriorCandidatePositions(PinY: int):
        # The bounded local search keeps normal layouts compact. The exterior
        # search is deliberately unbounded so an annotation can never vanish
        # merely because routing filled the nearby cells.
        Distance = 1
        while True:
            for X in range(MinimumX - Distance, MaximumX + Distance + 1):
                yield (X, PinY, MinimumZ - Distance)
            for X in range(MinimumX - Distance, MaximumX + Distance + 1):
                yield (X, PinY, MaximumZ + Distance)
            for Z in range(
                MinimumZ - (Distance - 1),
                MaximumZ + (Distance - 1) + 1,
            ):
                yield (MinimumX - Distance, PinY, Z)
            for Z in range(
                MinimumZ - (Distance - 1),
                MaximumZ + (Distance - 1) + 1,
            ):
                yield (MaximumX + Distance, PinY, Z)
            Distance += 1

    def FitsExistingEnvelope(Position: tuple[int, int, int]) -> bool:
        """Return whether both a sign and its support fit the routed envelope."""
        X, Y, Z = Position
        return (
            MinimumX <= X <= MaximumX
            and MinimumY <= Y - 1 <= MaximumY
            and MinimumY <= Y <= MaximumY
            and MinimumZ <= Z <= MaximumZ
        )

    def ExistingDeckCandidatePositions(
        Pin: tuple[int, int, int],
        PrimaryDirection: tuple[int, int, int],
    ):
        """Yield nearby label positions on existing vertical routing decks.

        A pin-level ring can be completely occupied in a dense stacked design
        even though another already-emitted layer has a legal supported slot.
        Restrict this fallback to the pre-annotation envelope so using a deck
        can only avoid X/Z or height growth.
        """
        PinX, PinY, PinZ = Pin
        CandidateYs = sorted(
            (
                Y
                for Y in range(MinimumY + 1, MaximumY + 1)
                if Y != PinY
            ),
            key=lambda Y: (abs(Y - PinY), Y),
        )
        for CandidateY in CandidateYs:
            for Position in LocalCandidatePositions(
                (PinX, CandidateY, PinZ),
                PrimaryDirection,
            ):
                if FitsExistingEnvelope(Position):
                    yield Position

    def EnvelopeGrowth(
        Position: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        """Score annotation placement by exact physical envelope growth."""
        X, Y, Z = Position
        ExpandedWidth = max(MaximumX, X) - min(MinimumX, X) + 1
        ExpandedDepth = max(MaximumZ, Z) - min(MinimumZ, Z) + 1
        ExpandedHeight = max(MaximumY, Y) - min(MinimumY, Y - 1) + 1
        ExpandedFootprint = ExpandedWidth * ExpandedDepth
        return (
            ExpandedFootprint - BaseFootprint,
            ExpandedHeight - (MaximumY - MinimumY + 1),
            ExpandedFootprint,
        )

    for Gate in RoutedDesign.PlacedGates:
        if Gate.Kind not in ("INPUT", "OUTPUT") or not Gate.Outputs:
            continue
        if Gate.Kind == "INPUT":
            if Gate.OutputPin is None or Gate.OutputDirection is None:
                raise ValueError(f"INPUT cell {Gate.Name} has no routed output pin")
            SignalName = Gate.Outputs[0]
            Prefix = "IN"
            Pin = Gate.OutputPin
            PrimaryDirection = Gate.OutputDirection
        else:
            if not Gate.InputPins or not Gate.InputDirections:
                raise ValueError(f"OUTPUT cell {Gate.Name} has no routed input pin")
            SignalName = Gate.Inputs[0]
            Prefix = "OUT"
            Pin = Gate.InputPins[0]
            PrimaryDirection = tuple(-Value for Value in Gate.InputDirections[0])

        LocalChoices = [
            (EnvelopeGrowth(Position), 0, 0, CandidateIndex, Position)
            for CandidateIndex, Position in enumerate(
                LocalCandidatePositions(Pin, PrimaryDirection)
            )
            if CanPlace(Position)
        ]
        ExistingDeckChoices = [
            (
                EnvelopeGrowth(Position),
                1,
                abs(Position[1] - Pin[1]),
                CandidateIndex,
                Position,
            )
            for CandidateIndex, Position in enumerate(
                ExistingDeckCandidatePositions(Pin, PrimaryDirection)
            )
            if CanPlace(Position)
        ]
        CandidateChoices = [*LocalChoices, *ExistingDeckChoices]
        if CandidateChoices:
            _Growth, _DeckPenalty, _DeckDistance, _CandidateIndex, SignPosition = min(
                CandidateChoices
            )
        else:
            SignPosition = next(
                Position
                for Position in ExteriorCandidatePositions(Pin[1])
                if CanPlace(Position)
            )
        SupportPosition = (SignPosition[0], SignPosition[1] - 1, SignPosition[2])
        if SupportPosition not in Blocks:
            Signal = SupportSignalByPosition.get(SupportPosition)
            Blocks[SupportPosition] = (
                {"Name": NetSignalColors[Signal]}
                if Signal is not None
                else SupportState
            )
            Provenance[SupportPosition] = BlockProvenance.RouteSupport
        Blocks[SignPosition] = {"Name": "minecraft:oak_sign"}
        Provenance[SignPosition] = BlockProvenance.Annotation
        Signs.append((SignPosition, f"{Prefix} {SignalName}"))
    return Signs


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


def _NormalizeTracePalette(
    TraceSupportBlocks: tuple[str, ...] | list[str] | None = None,
) -> tuple[str, ...]:
    if TraceSupportBlocks is None:
        return TRACE_PALETTE
    if isinstance(TraceSupportBlocks, (tuple, list)):
        Normalized = tuple(
            str(Block).strip()
            for Block in TraceSupportBlocks
            if str(Block).strip()
        )
        return Normalized or TRACE_PALETTE
    raise TypeError(
        "Trace support blocks must be a tuple/list of block IDs or omitted",
    )


def TemplateRepeaterPinRoles(CellKind: str) -> dict[tuple[int, int, int], str]:
    """Return template repeaters that bridge an externally declared macro pin.

    A macro repeater on one of these positions cannot be removed by the route
    cleanup pass: it is part of the cell's input isolation or output-driving
    contract.  Template repeaters outside this map remain protected until a
    macro-equivalence check is available; the audit makes that distinction
    explicit instead of silently treating template blocks as route material.
    """
    Macro = CellMacros[CellKind.upper()]
    Roles: dict[tuple[int, int, int], str] = {}
    for Index, (Pin, Direction) in enumerate(
        zip(Macro.InputPins, Macro.InputDirections)
    ):
        Roles[tuple(Pin[Axis] - Direction[Axis] for Axis in range(3))] = (
            f"InputPin{Index}Bridge"
        )
    if Macro.OutputPin is not None and Macro.OutputDirection is not None:
        Roles[
            tuple(
                Macro.OutputPin[Axis] - Macro.OutputDirection[Axis]
                for Axis in range(3)
            )
        ] = "OutputPinBridge"
    return Roles


def TemplateRepeaterPinInputFacings(
    CellKind: str,
) -> dict[tuple[int, int, int], tuple[int, int, int]]:
    """Return the local Java input-side facing of each pin repeater.

    Java repeater ``facing`` points from the output side toward the input
    side.  An input bridge receives power from the macro's outward input
    direction, while an output bridge receives power from inside the macro.
    """
    Macro = CellMacros[CellKind.upper()]
    Directions = {
        tuple(Pin[Axis] - Direction[Axis] for Axis in range(3)): tuple(Direction)
        for Pin, Direction in zip(Macro.InputPins, Macro.InputDirections)
    }
    if Macro.OutputPin is not None and Macro.OutputDirection is not None:
        Directions[
            tuple(
                Macro.OutputPin[Axis] - Macro.OutputDirection[Axis]
                for Axis in range(3)
            )
        ] = tuple(-Value for Value in Macro.OutputDirection)
    return Directions


def ApplyTemplateRepeaterPinInputFacing(
    CellKind: str,
    LocalPosition: tuple[int, int, int],
    State: dict[str, Any],
) -> dict[str, Any]:
    """Apply an unrotated macro bridge input side to one template state."""
    if State["Name"] != "minecraft:repeater":
        return State
    Direction = TemplateRepeaterPinInputFacings(CellKind).get(LocalPosition)
    if Direction is None:
        return State
    FacingByDirection = {
        (0, 0, -1): "north",
        (1, 0, 0): "east",
        (0, 0, 1): "south",
        (-1, 0, 0): "west",
    }
    Facing = FacingByDirection.get(Direction)
    if Facing is None:
        raise ValueError(
            f"Template repeater pin direction must be horizontal: {Direction}"
        )
    Result = {"Name": State["Name"]}
    Properties = dict(State.get("Properties", {}))
    Properties["facing"] = Facing
    Result["Properties"] = Properties
    return Result


def _RepeaterOrientationSignal(Gate: Any, Role: str) -> str | None:
    if Role.startswith("InputPin") and Role.endswith("Bridge"):
        IndexText = Role[len("InputPin") : -len("Bridge")]
        if IndexText.isdigit() and int(IndexText) < len(Gate.Inputs):
            return str(Gate.Inputs[int(IndexText)])
    if Role == "OutputPinBridge" and Gate.Outputs:
        return str(Gate.Outputs[0])
    return None


def BuildRepeaterOrientationAudit(
    Blocks: dict[tuple[int, int, int], dict[str, Any]],
    ExpectedByPosition: dict[tuple[int, int, int], dict[str, Any]],
) -> dict[str, Any]:
    """Validate every rendered repeater against its independent owner intent."""
    ActualPositions = {
        Position
        for Position, State in Blocks.items()
        if State["Name"] == "minecraft:repeater"
    }
    ExpectedPositions = set(ExpectedByPosition)
    if ActualPositions != ExpectedPositions:
        Missing = sorted(ExpectedPositions - ActualPositions)
        Unexpected = sorted(ActualPositions - ExpectedPositions)
        raise ValueError(
            "Rendered repeater ownership mismatch: "
            f"missing={Missing[:8]} unexpected={Unexpected[:8]}"
        )
    Minimum = tuple(
        min(Position[Axis] for Position in Blocks)
        for Axis in range(3)
    )
    Records = []
    DirectionCounts: Counter[str] = Counter()
    SourceCounts: Counter[str] = Counter()
    for Position in sorted(ExpectedPositions):
        State = Blocks[Position]
        ActualInputFacing = ValidateRepeaterInputFacing(
            str(State.get("Properties", {}).get("facing", ""))
        )
        Expected = ExpectedByPosition[Position]
        ExpectedInputFacing = ValidateRepeaterInputFacing(
            str(Expected["InputFacing"])
        )
        if ActualInputFacing != ExpectedInputFacing:
            raise ValueError(
                "Rendered repeater input-facing mismatch at "
                f"{Position}: {ActualInputFacing} != {ExpectedInputFacing}"
            )
        Source = str(Expected["Source"])
        DirectionCounts[ActualInputFacing] += 1
        SourceCounts[Source] += 1
        Records.append({
            **Expected,
            "Position": list(Position),
            "SerializedPosition": [
                Position[Axis] - Minimum[Axis]
                for Axis in range(3)
            ],
            "InputFacing": ActualInputFacing,
            "OutputFacing": OppositeHorizontalFacing(ActualInputFacing),
        })
    return {
        "SchemaVersion": "repeater-orientation-v1",
        "Contract": "minecraft-java-facing-is-input-side",
        "Passed": True,
        "MismatchCount": 0,
        "ExpectedCount": len(ExpectedPositions),
        "RenderedCount": len(ActualPositions),
        "RouteCount": SourceCounts["Route"],
        "TemplateCount": SourceCounts["Template"],
        "InputFacingCounts": dict(sorted(DirectionCounts.items())),
        "OriginTranslation": [-Value for Value in Minimum],
        "ReadbackRequired": True,
        "ReadbackPassed": False,
        "Records": Records,
    }


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


def BuildLitematicBlockMap(
    RoutedDesign: Any,
    TraceSupportBlocks: tuple[str, ...] | list[str] | None = None,
) -> LitematicBuild:
    """Render the exact final block map and retain ownership provenance."""
    Templates = {
        Name.upper(): LoadTemplate(PathValue)
        for Name, PathValue in LitematicTemplates.items()
    }
    Blocks: dict[tuple[int, int, int], dict[str, Any]] = {}
    Provenance: dict[tuple[int, int, int], BlockProvenance] = {}
    ExpectedRepeaterOrientations: dict[
        tuple[int, int, int],
        dict[str, Any],
    ] = {}
    Signs: list[tuple[tuple[int, int, int], str]] = []
    SignalList = sorted(RoutedDesign.NetWires)
    TracePalette = _NormalizeTracePalette(
        TraceSupportBlocks
        or getattr(RoutedDesign, "TraceSupportBlocks", None)
    )
    NetSignalColors = {
        Signal: TracePalette[SignalIndex % len(TracePalette)]
        for SignalIndex, Signal in enumerate(SignalList)
    }
    SupportSignalByPosition: dict[tuple[int, int, int], str] = {}
    TemplateRepeaterAudit: dict[str, Any] = {
        "Scanned": 0,
        "Removed": 0,
        "RetainedRequired": 0,
        "RetainedWithoutMacroEquivalenceProof": 0,
        "Roles": {},
    }
    for Signal in SignalList:
        for X, Y, Z in RoutedDesign.NetWires[Signal]:
            SupportSignalByPosition[(X, Y - 1, Z)] = Signal

    for Gate in RoutedDesign.PlacedGates:
        Template = Templates[Gate.Kind]
        RepeaterRoles = TemplateRepeaterPinRoles(Gate.Kind)
        for (X, Y, Z), State in Template.Blocks.items():
            Role: str | None = None
            if State["Name"] == "minecraft:repeater":
                Role = RepeaterRoles.get((X, Y, Z))
                TemplateRepeaterAudit["Scanned"] += 1
                if Role is None:
                    TemplateRepeaterAudit[
                        "RetainedWithoutMacroEquivalenceProof"
                    ] += 1
                    Role = "InternalProtected"
                else:
                    TemplateRepeaterAudit["RetainedRequired"] += 1
                TemplateRepeaterAudit["Roles"][Role] = (
                    TemplateRepeaterAudit["Roles"].get(Role, 0) + 1
                )
            State = ApplyTemplateRepeaterPinInputFacing(
                Gate.Kind,
                (X, Y, Z),
                State,
            )
            State = NeutralDynamicState(State)
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
            if State["Name"] == "minecraft:repeater":
                if Position in ExpectedRepeaterOrientations:
                    raise ValueError(
                        f"Multiple repeaters occupy rendered position {Position}"
                    )
                InputFacing = ValidateRepeaterInputFacing(
                    str(State.get("Properties", {}).get("facing", ""))
                )
                ExpectedRepeaterOrientations[Position] = {
                    "Source": "Template",
                    "Provenance": (
                        BlockProvenance.NandCell.value
                        if Gate.Kind == "NAND"
                        else BlockProvenance.IoCell.value
                    ),
                    "Gate": str(Gate.Name),
                    "Role": str(Role),
                    "Signal": _RepeaterOrientationSignal(
                        Gate,
                        str(Role),
                    ),
                    "InputFacing": InputFacing,
                }
            Blocks[Position] = State
            Provenance[Position] = (
                BlockProvenance.NandCell
                if Gate.Kind == "NAND"
                else BlockProvenance.IoCell
            )

    SupportBlock = getattr(
        RoutedDesign,
        "SupportBlock",
        "minecraft:light_gray_concrete",
    )
    if not SupportBlock:
        SupportBlock = "minecraft:light_gray_concrete"
    SupportState = {"Name": SupportBlock}

    # A wall lever's ``facing`` property points away from its backing block.
    # The Input template already places its redstone lamp on that backing side;
    # do not add a second, floating support block on the visible lever side.
    # Keep this general check for any future wall-mounted template components
    # whose real backing block is absent.
    FacingOffsets = {
        "north": (0, 0, -1),
        "south": (0, 0, 1),
        "east": (1, 0, 0),
        "west": (-1, 0, 0),
    }
    for Position, State in tuple(Blocks.items()):
        if (
            State.get("Name") == "minecraft:lever"
            and State.get("Properties", {}).get("face") == "wall"
        ):
            Facing = State["Properties"].get("facing")
            Offset = FacingOffsets.get(Facing)
            if Offset is None:
                raise ValueError(f"wall lever has unsupported facing: {Facing}")
            SupportPosition = tuple(
                Position[Axis] - Offset[Axis]
                for Axis in range(3)
            )
            if SupportPosition not in Blocks:
                Blocks[SupportPosition] = SupportState
                Provenance[SupportPosition] = BlockProvenance.RouteSupport

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
                    Signal = SupportSignalByPosition.get(SupportPosition)
                    Blocks[SupportPosition] = (
                        {"Name": NetSignalColors[Signal]}
                        if Signal is not None
                        else SupportState
                    )
                    Provenance[SupportPosition] = BlockProvenance.RouteSupport

    for Position in RoutedDesign.Supports:
        if Position not in Blocks:
            Signal = SupportSignalByPosition.get(Position)
            Blocks[Position] = (
                {"Name": NetSignalColors[Signal]}
                if Signal is not None
                else SupportState
            )
            Provenance[Position] = BlockProvenance.RouteSupport

    for Signal, Positions in RoutedDesign.NetWires.items():
        NetCells = set(Positions)
        for Position in NetCells:
            if Position in RoutedDesign.RepeaterInputFacings:
                continue
            Blocks[Position] = BuildWireState(
                Position,
                NetCells,
                Blocks,
                0,
            )
            Provenance.setdefault(Position, BlockProvenance.RouteSignal)

    for Position, InputFacingValue in (
        RoutedDesign.RepeaterInputFacings.items()
    ):
        if Position in ExpectedRepeaterOrientations:
            raise ValueError(
                f"Route repeater overlaps template repeater at {Position}"
            )
        InputFacing = ValidateRepeaterInputFacing(InputFacingValue)
        Signal = next(
            Name
            for Name, Positions in RoutedDesign.NetWires.items()
            if Position in Positions
        )
        Blocks[Position] = {
            "Name": "minecraft:repeater",
            "Properties": {
                # Routed refreshers are dynamic circuit state.  Seeding them
                # from an ideal default logic evaluation can pre-charge a
                # same-signal feedback path that the physical producer does
                # not currently drive.  Emit the neutral blockstate and let
                # the exact redstone network settle every repeater from its
                # rear input.
                "powered": "false",
                "facing": InputFacing,
                "locked": "false",
                "delay": "1",
            },
        }
        Provenance[Position] = BlockProvenance.RouteRefresh
        ExpectedRepeaterOrientations[Position] = {
            "Source": "Route",
            "Provenance": BlockProvenance.RouteRefresh.value,
            "Gate": None,
            "Role": "RouteRefresh",
            "Signal": str(Signal),
            "InputFacing": InputFacing,
        }

    Signs = _PlaceIoSigns(
        RoutedDesign,
        Blocks,
        Provenance,
        SupportState,
        SupportSignalByPosition,
        NetSignalColors,
    )

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
        for Position in Blocks
        if Provenance[Position] == BlockProvenance.RouteSupport
    )
    FunctionalPositions = {
        Position
        for Position, State in Blocks.items()
        if Provenance[Position]
        not in {BlockProvenance.RouteSupport, BlockProvenance.Annotation}
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
        XYFootprint=Width * Height,
        Footprint=Width * Depth,
        FullFootprint=Width * Height * Depth,
        MaterialCounts=dict(sorted(Materials.items())),
        ProvenanceCounts=dict(sorted(Ownership.items())),
    )
    RepeaterOrientation = BuildRepeaterOrientationAudit(
        Blocks,
        ExpectedRepeaterOrientations,
    )
    return LitematicBuild(
        Blocks=Blocks,
        Provenance=Provenance,
        Signs=Signs,
        Composition=Composition,
        RepeaterOptimization={
            "Route": dict(
                getattr(RoutedDesign, "RepeaterOptimizationDiagnostics", {})
            ),
            "Templates": {
                **TemplateRepeaterAudit,
                "Roles": dict(sorted(TemplateRepeaterAudit["Roles"].items())),
            },
        },
        RepeaterOrientation=RepeaterOrientation,
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

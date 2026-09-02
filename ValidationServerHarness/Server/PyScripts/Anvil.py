"""Read persisted Anvil blocks without mutating the canonical Fabric world.

The server manager uses this module to discover the *actual* non-air blocks in
saved chunks before asking the already-running harness to remove them.  It
deliberately does not write region files: Minecraft remains the sole owner of
world storage and of the resulting block updates.
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
from pathlib import Path
import re
import struct
from typing import Any, Iterator
import zlib


AirBlockNames = frozenset({
    "minecraft:air",
    "minecraft:cave_air",
    "minecraft:void_air",
})
ChunkEdgeLength = 16
ChunkSectionBlockCount = ChunkEdgeLength ** 3
RegionHeaderLength = 4096
RegionSectorLength = 4096
MaximumNbtCollectionLength = 10_000_000
RegionFilenamePattern = re.compile(r"r\.(-?\d+)\.(-?\d+)\.mca\Z")


@dataclass(frozen=True)
class RegionChunkBlocks:
    """The non-air positions found in one saved chunk."""

    ChunkX: int
    ChunkZ: int
    Positions: tuple[tuple[int, int, int], ...]


def RequireBytes(Data: bytes, Offset: int, Length: int, Context: str) -> None:
    """Reject a truncated NBT payload before slicing or unpacking it."""
    if Offset < 0 or Length < 0 or Offset + Length > len(Data):
        raise RuntimeError(f"truncated NBT {Context}")


def ReadNbtString(Data: bytes, Offset: int) -> tuple[str, int]:
    """Read one standard big-endian NBT string."""
    RequireBytes(Data, Offset, 2, "string length")
    Length = struct.unpack_from(">H", Data, Offset)[0]
    Offset += 2
    RequireBytes(Data, Offset, Length, "string value")
    try:
        Value = Data[Offset:Offset + Length].decode("utf-8")
    except UnicodeDecodeError as Error:
        raise RuntimeError("invalid UTF-8 NBT string") from Error
    return Value, Offset + Length


def ReadNbtLength(Data: bytes, Offset: int, Context: str) -> tuple[int, int]:
    """Read a bounded non-negative NBT collection length."""
    RequireBytes(Data, Offset, 4, f"{Context} length")
    Length = struct.unpack_from(">i", Data, Offset)[0]
    if Length < 0 or Length > MaximumNbtCollectionLength:
        raise RuntimeError(f"invalid NBT {Context} length: {Length}")
    return Length, Offset + 4


def ReadNbtPayload(TagType: int, Data: bytes, Offset: int) -> tuple[Any, int]:
    """Decode the standard NBT tags needed by modern Anvil chunks."""
    ScalarFormats = {1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}
    if TagType in ScalarFormats:
        Format = ">" + ScalarFormats[TagType]
        Width = struct.calcsize(Format)
        RequireBytes(Data, Offset, Width, "scalar")
        return struct.unpack_from(Format, Data, Offset)[0], Offset + Width
    if TagType == 7:
        Length, Offset = ReadNbtLength(Data, Offset, "byte array")
        RequireBytes(Data, Offset, Length, "byte array")
        return Data[Offset:Offset + Length], Offset + Length
    if TagType == 8:
        return ReadNbtString(Data, Offset)
    if TagType == 9:
        RequireBytes(Data, Offset, 1, "list element type")
        ChildType = Data[Offset]
        if ChildType > 12:
            raise RuntimeError(f"unsupported NBT list element type: {ChildType}")
        Length, Offset = ReadNbtLength(Data, Offset + 1, "list")
        if ChildType == 0 and Length:
            raise RuntimeError("NBT end-tag list has values")
        Values = []
        for _ in range(Length):
            Value, Offset = ReadNbtPayload(ChildType, Data, Offset)
            Values.append(Value)
        return Values, Offset
    if TagType == 10:
        Values: dict[str, Any] = {}
        while True:
            RequireBytes(Data, Offset, 1, "compound tag type")
            ChildType = Data[Offset]
            Offset += 1
            if ChildType == 0:
                return Values, Offset
            if ChildType > 12:
                raise RuntimeError(f"unsupported NBT compound tag type: {ChildType}")
            Name, Offset = ReadNbtString(Data, Offset)
            if Name in Values:
                raise RuntimeError(f"duplicate NBT compound name: {Name!r}")
            Value, Offset = ReadNbtPayload(ChildType, Data, Offset)
            Values[Name] = Value
    if TagType in (11, 12):
        Context = "int array" if TagType == 11 else "long array"
        Length, Offset = ReadNbtLength(Data, Offset, Context)
        Format = "i" if TagType == 11 else "q"
        Width = 4 if TagType == 11 else 8
        RequireBytes(Data, Offset, Length * Width, Context)
        return list(struct.unpack_from(f">{Length}{Format}", Data, Offset)), Offset + Length * Width
    raise RuntimeError(f"unsupported NBT tag type: {TagType}")


def ReadNbtRoot(Data: bytes) -> dict[str, Any]:
    """Decode one complete uncompressed chunk NBT compound."""
    RequireBytes(Data, 0, 1, "root tag type")
    if Data[0] != 10:
        raise RuntimeError("Anvil chunk root is not an NBT compound")
    _RootName, Offset = ReadNbtString(Data, 1)
    Root, Offset = ReadNbtPayload(10, Data, Offset)
    if Offset != len(Data):
        raise RuntimeError("trailing bytes in Anvil chunk NBT")
    if not isinstance(Root, dict):
        raise RuntimeError("invalid Anvil chunk root compound")
    return Root


def DecompressChunkPayload(CompressionType: int, Data: bytes) -> bytes:
    """Decode supported standard Anvil compression types without writing data."""
    if CompressionType == 1:
        try:
            return gzip.decompress(Data)
        except (OSError, EOFError) as Error:
            raise RuntimeError("invalid gzip-compressed Anvil chunk") from Error
    if CompressionType == 2:
        try:
            return zlib.decompress(Data)
        except zlib.error as Error:
            raise RuntimeError("invalid zlib-compressed Anvil chunk") from Error
    if CompressionType == 3:
        return Data
    raise RuntimeError(
        f"unsupported Anvil chunk compression type: {CompressionType}",
    )


def ReadPackedPaletteIndex(
    Packed: list[int],
    Index: int,
    BitsPerValue: int,
) -> int:
    """Read an entry from Minecraft's padded ``SimpleBitStorage`` layout."""
    ValuesPerLong = 64 // BitsPerValue
    LongIndex = Index // ValuesPerLong
    Shift = (Index % ValuesPerLong) * BitsPerValue
    Mask = (1 << BitsPerValue) - 1
    return ((Packed[LongIndex] & ((1 << 64) - 1)) >> Shift) & Mask


def ReadSectionPaletteNames(Section: dict[str, Any]) -> tuple[int, list[str], list[int] | None]:
    """Validate one modern chunk section's block-state palette."""
    SectionY = Section.get("Y")
    if type(SectionY) is not int:
        raise RuntimeError("Anvil chunk section has no integer Y")
    BlockStates = Section.get("block_states")
    if not isinstance(BlockStates, dict):
        raise RuntimeError("Anvil chunk section has no block_states compound")
    Palette = BlockStates.get("palette")
    if not isinstance(Palette, list) or not Palette:
        raise RuntimeError("Anvil chunk block_states has no palette")
    Names: list[str] = []
    for PaletteIndex, State in enumerate(Palette):
        if not isinstance(State, dict):
            raise RuntimeError(
                f"Anvil chunk palette entry {PaletteIndex} is not a compound",
            )
        Name = State.get("Name")
        if not isinstance(Name, str) or not Name:
            raise RuntimeError(
                f"Anvil chunk palette entry {PaletteIndex} has no block name",
            )
        Names.append(Name)
    Packed = BlockStates.get("data")
    if Packed is None:
        return SectionY, Names, None
    if not isinstance(Packed, list) or not all(type(Value) is int for Value in Packed):
        raise RuntimeError("Anvil chunk block_states data is not a long array")
    return SectionY, Names, Packed


def ReadSectionNonAirPositions(
    Section: dict[str, Any],
    ChunkX: int,
    ChunkZ: int,
) -> tuple[tuple[int, int, int], ...]:
    """Return every non-air block coordinate in one 16x16x16 chunk section."""
    SectionY, PaletteNames, Packed = ReadSectionPaletteNames(Section)
    NonAirPaletteIndexes = {
        PaletteIndex
        for PaletteIndex, Name in enumerate(PaletteNames)
        if Name not in AirBlockNames
    }
    if not NonAirPaletteIndexes:
        return ()
    if len(PaletteNames) == 1:
        if Packed not in (None, []):
            raise RuntimeError("single-state Anvil palette unexpectedly has packed data")
        StateIndexes = (0 for _ in range(ChunkSectionBlockCount))
    else:
        if Packed is None:
            raise RuntimeError("multi-state Anvil palette has no packed data")
        BitsPerValue = max(4, (len(PaletteNames) - 1).bit_length())
        ValuesPerLong = 64 // BitsPerValue
        ExpectedLongCount = (
            ChunkSectionBlockCount + ValuesPerLong - 1
        ) // ValuesPerLong
        if len(Packed) != ExpectedLongCount:
            raise RuntimeError(
                "Anvil chunk block_states data has an unexpected packed-long count: "
                f"{len(Packed)} != {ExpectedLongCount}",
            )
        StateIndexes = (
            ReadPackedPaletteIndex(Packed, Index, BitsPerValue)
            for Index in range(ChunkSectionBlockCount)
        )

    Positions = []
    for Index, PaletteIndex in enumerate(StateIndexes):
        if PaletteIndex >= len(PaletteNames):
            raise RuntimeError(
                "Anvil chunk block_states references palette index "
                f"{PaletteIndex} outside {len(PaletteNames)} entries",
            )
        if PaletteIndex not in NonAirPaletteIndexes:
            continue
        LocalX = Index & 15
        LocalZ = (Index >> 4) & 15
        LocalY = Index >> 8
        Positions.append((
            ChunkX * ChunkEdgeLength + LocalX,
            SectionY * ChunkEdgeLength + LocalY,
            ChunkZ * ChunkEdgeLength + LocalZ,
        ))
    return tuple(Positions)


def ReadChunkNonAirBlocks(
    ChunkData: bytes,
    ExpectedChunkX: int,
    ExpectedChunkZ: int,
) -> RegionChunkBlocks:
    """Parse one decoded chunk and validate its location before returning blocks."""
    Root = ReadNbtRoot(ChunkData)
    ChunkX = Root.get("xPos")
    ChunkZ = Root.get("zPos")
    if type(ChunkX) is not int or type(ChunkZ) is not int:
        raise RuntimeError("Anvil chunk has no integer xPos/zPos")
    if (ChunkX, ChunkZ) != (ExpectedChunkX, ExpectedChunkZ):
        raise RuntimeError(
            "Anvil chunk location does not match its region header: "
            f"header=({ExpectedChunkX},{ExpectedChunkZ}) "
            f"NBT=({ChunkX},{ChunkZ})",
        )
    Sections = Root.get("sections")
    if not isinstance(Sections, list):
        raise RuntimeError("Anvil chunk has no sections list")
    Positions = []
    for Section in Sections:
        if not isinstance(Section, dict):
            raise RuntimeError("Anvil chunk sections contains a non-compound entry")
        Positions.extend(ReadSectionNonAirPositions(Section, ChunkX, ChunkZ))
    return RegionChunkBlocks(
        ChunkX=ChunkX,
        ChunkZ=ChunkZ,
        Positions=tuple(Positions),
    )


def ReadRegionNonAirBlocks(RegionPath: Path) -> Iterator[RegionChunkBlocks]:
    """Yield every saved chunk's non-air positions from one standard region file."""
    Match = RegionFilenamePattern.fullmatch(RegionPath.name)
    if Match is None:
        raise RuntimeError(f"invalid Anvil region filename: {RegionPath}")
    if RegionPath.is_symlink():
        raise RuntimeError(f"refusing to inspect linked world storage: {RegionPath}")
    try:
        with RegionPath.open("rb") as RegionFile:
            Header = RegionFile.read(RegionHeaderLength)
            if len(Header) != RegionHeaderLength:
                raise RuntimeError(f"invalid Anvil region header: {RegionPath}")
            RegionX, RegionZ = (int(Value) for Value in Match.groups())
            RegionFileSize = RegionPath.stat().st_size
            for Index in range(1024):
                Location = int.from_bytes(Header[Index * 4:(Index + 1) * 4], "big")
                SectorOffset = Location >> 8
                SectorCount = Location & 0xFF
                if SectorOffset == 0:
                    if SectorCount:
                        raise RuntimeError(
                            f"invalid empty Anvil location entry in {RegionPath}",
                        )
                    continue
                if SectorOffset < 2 or SectorCount == 0:
                    raise RuntimeError(f"invalid Anvil location entry in {RegionPath}")
                ByteOffset = SectorOffset * RegionSectorLength
                MaximumChunkBytes = SectorCount * RegionSectorLength
                if ByteOffset + 5 > RegionFileSize:
                    raise RuntimeError(f"Anvil chunk starts past region file: {RegionPath}")
                RegionFile.seek(ByteOffset)
                Prefix = RegionFile.read(5)
                if len(Prefix) != 5:
                    raise RuntimeError(f"truncated Anvil chunk prefix: {RegionPath}")
                ChunkLength = int.from_bytes(Prefix[:4], "big")
                if ChunkLength < 1 or ChunkLength + 4 > MaximumChunkBytes:
                    raise RuntimeError(f"invalid Anvil chunk length: {RegionPath}")
                if ByteOffset + ChunkLength + 4 > RegionFileSize:
                    raise RuntimeError(f"Anvil chunk extends past region file: {RegionPath}")
                CompressionByte = Prefix[4]
                if CompressionByte & 0x80:
                    raise RuntimeError(
                        "external Anvil chunk storage is unsupported for a safe "
                        f"live clear: {RegionPath}",
                    )
                Compressed = RegionFile.read(ChunkLength - 1)
                if len(Compressed) != ChunkLength - 1:
                    raise RuntimeError(f"truncated Anvil chunk payload: {RegionPath}")
                ChunkX = RegionX * 32 + Index % 32
                ChunkZ = RegionZ * 32 + Index // 32
                yield ReadChunkNonAirBlocks(
                    DecompressChunkPayload(CompressionByte, Compressed),
                    ChunkX,
                    ChunkZ,
                )
    except (OSError, ValueError, struct.error) as Error:
        raise RuntimeError(f"could not read Anvil region {RegionPath}: {Error}") from Error

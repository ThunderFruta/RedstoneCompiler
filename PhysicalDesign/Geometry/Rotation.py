"""Shared horizontal rotation helpers for placed redstone cells."""

from __future__ import annotations

from typing import Any

from ..Cells.Library import CellMacros


BaseCellSizes = {
    Name: Macro.Footprint
    for Name, Macro in CellMacros.items()
}

HorizontalDirections = ("north", "east", "south", "west")


def NormalizeRotation(Rotation: int) -> int:
    """Return a supported clockwise quarter-turn rotation."""
    Normalized = Rotation % 360
    if Normalized not in (0, 90, 180, 270):
        raise ValueError("Cell rotation must be 0, 90, 180, or 270 degrees")
    return Normalized


def RotatedCellSize(CellKind: str, Rotation: int) -> tuple[int, int]:
    """Return the X/Z footprint of a horizontally rotated cell."""
    Width, Depth = BaseCellSizes[CellKind]
    if NormalizeRotation(Rotation) in (90, 270):
        return Depth, Width
    return Width, Depth


def RotateLocalPosition(
    Position: tuple[int, int, int],
    Size: tuple[int, int],
    Rotation: int,
) -> tuple[int, int, int]:
    """Rotate a local coordinate clockwise around the cell origin."""
    X, Y, Z = Position
    Width, Depth = Size
    Rotation = NormalizeRotation(Rotation)
    if Rotation == 90:
        return Depth - 1 - Z, Y, X
    if Rotation == 180:
        return Width - 1 - X, Y, Depth - 1 - Z
    if Rotation == 270:
        return Z, Y, Width - 1 - X
    return Position


def RotateDirection(
    Direction: tuple[int, int, int],
    Rotation: int,
) -> tuple[int, int, int]:
    """Rotate a direction vector clockwise around the Y axis."""
    X, Y, Z = Direction
    Rotation = NormalizeRotation(Rotation)
    if Rotation == 90:
        return -Z, Y, X
    if Rotation == 180:
        return -X, Y, -Z
    if Rotation == 270:
        return Z, Y, -X
    return Direction


def MirrorLocalPosition(
    Position: tuple[int, int, int],
    Size: tuple[int, int],
    MirrorX: bool,
) -> tuple[int, int, int]:
    """Mirror a local position across the cell's X centerline."""
    if not MirrorX:
        return Position
    X, Y, Z = Position
    Width, _ = Size
    return Width - 1 - X, Y, Z


def MirrorDirection(
    Direction: tuple[int, int, int],
    MirrorX: bool,
) -> tuple[int, int, int]:
    """Mirror a direction vector across the cell's X centerline."""
    if not MirrorX:
        return Direction
    X, Y, Z = Direction
    return -X, Y, Z


def TransformLocalPosition(
    Position: tuple[int, int, int],
    Size: tuple[int, int],
    Rotation: int,
    MirrorX: bool = False,
) -> tuple[int, int, int]:
    """Mirror, then rotate, a local cell coordinate."""
    return RotateLocalPosition(
        MirrorLocalPosition(Position, Size, MirrorX),
        Size,
        Rotation,
    )


def TransformDirection(
    Direction: tuple[int, int, int],
    Rotation: int,
    MirrorX: bool = False,
) -> tuple[int, int, int]:
    """Mirror, then rotate, a local direction vector."""
    return RotateDirection(
        MirrorDirection(Direction, MirrorX),
        Rotation,
    )


def RotateHorizontalName(Direction: str, Rotation: int) -> str:
    """Rotate a Minecraft horizontal direction name."""
    if Direction not in HorizontalDirections:
        return Direction
    Steps = NormalizeRotation(Rotation) // 90
    Index = HorizontalDirections.index(Direction)
    return HorizontalDirections[(Index + Steps) % 4]


def RotateBlockState(
    State: dict[str, Any],
    Rotation: int,
) -> dict[str, Any]:
    """Rotate all directional properties belonging to one template block."""
    Rotation = NormalizeRotation(Rotation)
    Properties = dict(State.get("Properties", {}))
    Facing = Properties.get("facing")
    if Facing in HorizontalDirections:
        Properties["facing"] = RotateHorizontalName(Facing, Rotation)

    if State["Name"] == "minecraft:redstone_wire":
        OriginalConnections = {
            Direction: Properties.get(Direction, "none")
            for Direction in HorizontalDirections
        }
        for Direction, Connection in OriginalConnections.items():
            Properties[RotateHorizontalName(Direction, Rotation)] = Connection

    if Rotation in (90, 270) and Properties.get("axis") in ("x", "z"):
        Properties["axis"] = "z" if Properties["axis"] == "x" else "x"

    if "rotation" in Properties:
        try:
            Properties["rotation"] = str(
                (int(Properties["rotation"]) + (Rotation // 90) * 4) % 16
            )
        except (TypeError, ValueError):
            pass

    Result = {"Name": State["Name"]}
    if Properties:
        Result["Properties"] = Properties
    return Result


def MirrorBlockState(
    State: dict[str, Any],
    MirrorX: bool,
) -> dict[str, Any]:
    """Mirror directional block properties across the local X axis."""
    if not MirrorX:
        return {
            "Name": State["Name"],
            **(
                {"Properties": dict(State["Properties"])}
                if State.get("Properties")
                else {}
            ),
        }

    Properties = dict(State.get("Properties", {}))
    Facing = Properties.get("facing")
    if Facing in ("east", "west"):
        Properties["facing"] = "west" if Facing == "east" else "east"

    if State["Name"] == "minecraft:redstone_wire":
        Properties["east"], Properties["west"] = (
            Properties.get("west", "none"),
            Properties.get("east", "none"),
        )

    if "rotation" in Properties:
        try:
            Properties["rotation"] = str((-int(Properties["rotation"])) % 16)
        except (TypeError, ValueError):
            pass

    Result = {"Name": State["Name"]}
    if Properties:
        Result["Properties"] = Properties
    return Result


def TransformBlockState(
    State: dict[str, Any],
    Rotation: int,
    MirrorX: bool = False,
) -> dict[str, Any]:
    """Mirror, then rotate, a template block state."""
    return RotateBlockState(
        MirrorBlockState(State, MirrorX),
        Rotation,
    )

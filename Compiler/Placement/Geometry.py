"""Shared placed-cell geometry for the PCB backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..Cells.Library import GetCellMacro
from .Rotation import (
    NormalizeRotation,
    RotatedCellSize,
    TransformDirection,
    TransformLocalPosition,
)


@dataclass
class PlacedGate:
    Name: str
    Kind: str
    X: int
    Y: int
    Z: int
    Outputs: list[str]
    Inputs: list[str]
    Attrs: dict[str, Any]
    InputPins: list[tuple[int, int, int]]
    OutputPin: tuple[int, int, int] | None
    Rotation: int
    MirrorX: bool
    InputDirections: list[tuple[int, int, int]]
    OutputDirection: tuple[int, int, int] | None


@dataclass
class PlacedDesign:
    Module: Any
    PlacedGates: list[PlacedGate]
    RouteGuides: dict[str, frozenset[tuple[int, int]]] | None = None
    RouteLayers: dict[str, int] | None = None
    FrozenNetWires: dict[str, tuple[tuple[int, int, int], ...]] | None = None
    LocalNetBranches: dict[str, tuple[tuple[int, int, int], ...]] | None = None
    LocalNetTargets: dict[str, tuple[tuple[int, int, int], ...]] | None = None
    LocalRouteClaims: tuple[Any, ...] = ()
    LocalRouteDiagnostics: dict[str, Any] | None = None
    ClusterBoundaryLeaseRequests: tuple[Any, ...] = ()
    CompleteClusterInterfaceAccess: bool = False
    InterClusterRoutingChannel: Any | None = None
    PlacementAccessFabric: Any | None = None
    PlacementAccessAssignment: Any | None = None
    # A derived packed-core placement freezes terminal face ownership before
    # access-fabric construction.  It remains optional so ordinary placement
    # backends preserve their historical terminal behavior.
    DerivedPerimeterSlotDomain: Any | None = None
    DerivedPerimeterSlotAssignment: Any | None = None
    # Complete placement-local trees offered as immutable alternatives to
    # ordinary portal/track candidates in the pre-route capacity problem.
    # They are deliberately distinct from ``LocalRouteClaims``, which are
    # already-selected base ownership.
    DerivedLocalRouteClaims: tuple[Any, ...] = ()
    RoutedComponentTemplates: tuple[Any, ...] = ()
    RoutedComponentRoutingChannels: tuple[Any, ...] = ()
    PackedClusters: tuple[Any, ...] = ()
    ComponentGraph: Any | None = None


def ValidatePlacedGateContract(Gate: PlacedGate) -> None:
    """Reject logical-to-physical pin aliasing before placement can route it."""
    if len(Gate.Inputs) != len(Gate.InputPins):
        raise ValueError(
            f"Cell {Gate.Name} ({Gate.Kind}) has {len(Gate.Inputs)} logical "
            f"inputs but {len(Gate.InputPins)} physical input pins"
        )
    if len(Gate.Inputs) != len(Gate.InputDirections):
        raise ValueError(
            f"Cell {Gate.Name} ({Gate.Kind}) has {len(Gate.Inputs)} logical "
            f"inputs but {len(Gate.InputDirections)} input directions"
        )
    if Gate.Kind != "OUTPUT" and Gate.Outputs and (
        Gate.OutputPin is None or Gate.OutputDirection is None
    ):
        raise ValueError(
            f"Cell {Gate.Name} ({Gate.Kind}) has logical outputs without a "
            "physical output pin and direction"
        )


def GetGateInputAccess(
    Gate: PlacedGate,
    InputIndex: int,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Return one exact logical input pin and direction after validation."""
    ValidatePlacedGateContract(Gate)
    if InputIndex < 0 or InputIndex >= len(Gate.Inputs):
        raise ValueError(
            f"Input index {InputIndex} is invalid for cell {Gate.Name}"
        )
    return Gate.InputPins[InputIndex], Gate.InputDirections[InputIndex]


def BuildPlacedGate(
    Gate: Any,
    X: int,
    Y: int,
    Z: int,
    Rotation: int,
    MirrorX: bool = False,
) -> PlacedGate:
    """Build rotation- and mirror-aware geometry for one placed gate."""
    Rotation = NormalizeRotation(Rotation)
    Macro = GetCellMacro(Gate.Kind.value)
    MirrorX = bool(MirrorX and Macro.AllowMirror)
    BaseSize = Macro.Footprint
    InputPins = []
    for Pin in Macro.InputPins:
        LocalPin = TransformLocalPosition(Pin, BaseSize, Rotation, MirrorX)
        InputPins.append(
            (X + LocalPin[0], Y + LocalPin[1], Z + LocalPin[2])
        )

    if Macro.OutputPin is not None:
        LocalOutputPin = TransformLocalPosition(
            Macro.OutputPin,
            BaseSize,
            Rotation,
            MirrorX,
        )
        OutputPin = (
            X + LocalOutputPin[0],
            Y + LocalOutputPin[1],
            Z + LocalOutputPin[2],
        )
    else:
        OutputPin = None

    InputDirections = [
        TransformDirection(Direction, Rotation, MirrorX)
        for Direction in Macro.InputDirections
    ]
    OutputDirection = (
        TransformDirection(Macro.OutputDirection, Rotation, MirrorX)
        if Macro.OutputDirection is not None
        else None
    )

    Placed = PlacedGate(
        Name=Gate.Name,
        Kind=Gate.Kind.value,
        X=X,
        Y=Y,
        Z=Z,
        Outputs=list(Gate.Outputs),
        Inputs=list(Gate.Inputs),
        Attrs=Gate.Attrs,
        InputPins=InputPins,
        OutputPin=OutputPin,
        Rotation=Rotation,
        MirrorX=MirrorX,
        InputDirections=InputDirections,
        OutputDirection=OutputDirection,
    )
    ValidatePlacedGateContract(Placed)
    return Placed


def RectanglesOverlap(
    First: PlacedGate,
    Second: PlacedGate,
) -> bool:
    """Return whether two rotated cell footprints occupy the same column."""
    # Vertically separated placement decks may intentionally share X/Z
    # columns. Deck pitch and exact electrical geometry are validated by the
    # PCB legalizer before routing.
    if First.Y != Second.Y:
        return False
    FirstWidth, FirstDepth = RotatedCellSize(First.Kind, First.Rotation)
    SecondWidth, SecondDepth = RotatedCellSize(Second.Kind, Second.Rotation)
    return not (
        First.X + FirstWidth <= Second.X
        or Second.X + SecondWidth <= First.X
        or First.Z + FirstDepth <= Second.Z
        or Second.Z + SecondDepth <= First.Z
    )


def GateAccessPositions(
    Gate: PlacedGate,
) -> set[tuple[int, int, int]]:
    """Return the pin and two outward routing cells reserved by a gate."""
    Positions: set[tuple[int, int, int]] = set()
    if Gate.OutputPin is not None and Gate.OutputDirection is not None:
        X, Y, Z = Gate.OutputPin
        DeltaX, DeltaY, DeltaZ = Gate.OutputDirection
        Positions.update(
            (
                X + DeltaX * Offset,
                Y + DeltaY * Offset,
                Z + DeltaZ * Offset,
            )
            for Offset in range(3)
        )
    ValidatePlacedGateContract(Gate)
    for Pin, Direction in zip(Gate.InputPins, Gate.InputDirections):
        X, Y, Z = Pin
        DeltaX, DeltaY, DeltaZ = Direction
        Positions.update(
            (
                X + DeltaX * Offset,
                Y + DeltaY * Offset,
                Z + DeltaZ * Offset,
            )
            for Offset in range(3)
        )
    return Positions

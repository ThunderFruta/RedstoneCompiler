"""Shared placed-cell geometry for the PCB backend."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from PhysicalDesign.Contracts.PlacementAccess import (
        PlacementAccessSolveResult,
        SelectedPlacementPinAccessWitness,
    )

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


@dataclass(frozen=True)
class PlacementPinAccessSelection:
    """One catalog-named physical pin path after placement transforms."""

    Signal: str
    GateName: str
    GateKind: str
    Role: str
    PinId: str
    PatternId: str
    Terminal: tuple[int, int, int]
    ApproachDirection: tuple[int, int, int]
    Path: tuple[tuple[int, int, int], ...]
    CatalogAccessLength: int
    CatalogMatched: bool

    def __post_init__(self) -> None:
        if self.Role not in {"Source", "Target"}:
            raise ValueError("pin-access selection role is invalid")
        if not self.Signal or not self.GateName or not self.PatternId:
            raise ValueError("pin-access selection requires stable identities")
        if not self.Path or self.Path[0] != self.Terminal:
            raise ValueError("pin-access path must begin at its terminal")
        if len(self.Path) > self.CatalogAccessLength:
            raise ValueError("pin-access path exceeds its catalog pattern")
        ExpectedPath = tuple(
            (
                self.Terminal[0] + self.ApproachDirection[0] * Offset,
                self.Terminal[1] + self.ApproachDirection[1] * Offset,
                self.Terminal[2] + self.ApproachDirection[2] * Offset,
            )
            for Offset in range(len(self.Path))
        )
        if self.Path != ExpectedPath:
            raise ValueError("pin-access path is not the selected straight ray")

    def StructuralIdentity(self) -> tuple[object, ...]:
        return (
            self.Signal,
            self.GateName,
            self.GateKind,
            self.Role,
            self.PinId,
            self.PatternId,
            self.Terminal,
            self.ApproachDirection,
            self.Path,
            self.CatalogAccessLength,
            self.CatalogMatched,
        )

    @property
    def SelectionFingerprint(self) -> str:
        return sha256(
            repr((
                "placement-pin-access-selection-v1",
                self.StructuralIdentity(),
            )).encode("utf-8")
        ).hexdigest()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "GateName": self.GateName,
            "GateKind": self.GateKind,
            "Role": self.Role,
            "PinId": self.PinId,
            "PatternId": self.PatternId,
            "Terminal": list(self.Terminal),
            "ApproachDirection": list(self.ApproachDirection),
            "Path": [list(Position) for Position in self.Path],
            "CatalogAccessLength": self.CatalogAccessLength,
            "CatalogMatched": self.CatalogMatched,
            "SelectionFingerprint": self.SelectionFingerprint,
        }


@dataclass(frozen=True)
class PlacementPinAccessWitness:
    """Complete straight-only access identity for one placed gate set."""

    AccessLength: int
    Selections: tuple[PlacementPinAccessSelection, ...]
    Complete: bool
    IncompleteReason: str = ""
    SchemaVersion: str = "placement-pin-access-witness-v1"

    def __post_init__(self) -> None:
        if self.AccessLength < 1:
            raise ValueError("pin-access witness requires positive access length")
        if self.Complete == bool(self.IncompleteReason):
            raise ValueError(
                "complete pin-access witness and incomplete reason disagree"
            )
        Ordered = tuple(sorted(
            self.Selections,
            key=lambda Value: Value.StructuralIdentity(),
        ))
        if self.Selections != Ordered:
            raise ValueError("pin-access witness selections must be sorted")
        Identities = tuple(
            (
                Value.Signal,
                Value.GateName,
                Value.Role,
                Value.PinId,
            )
            for Value in self.Selections
        )
        if len(Identities) != len(set(Identities)):
            raise ValueError("pin-access witness repeats a terminal identity")
        if any(len(Value.Path) != self.AccessLength for Value in self.Selections):
            raise ValueError("pin-access witness contains a truncated selection")

    @property
    def CatalogMatched(self) -> bool:
        return all(Value.CatalogMatched for Value in self.Selections)

    @property
    def WitnessFingerprint(self) -> str:
        return sha256(
            repr((
                self.SchemaVersion,
                self.AccessLength,
                tuple(
                    Value.StructuralIdentity()
                    for Value in self.Selections
                ),
                self.Complete,
                self.IncompleteReason,
            )).encode("utf-8")
        ).hexdigest()

    def FindSelection(
        self,
        Signal: str,
        GateName: str,
        Role: str,
        PinId: str,
    ) -> PlacementPinAccessSelection:
        Match = next(
            (
                Value
                for Value in self.Selections
                if (
                    Value.Signal,
                    Value.GateName,
                    Value.Role,
                    Value.PinId,
                ) == (Signal, GateName, Role, PinId)
            ),
            None,
        )
        if Match is None:
            raise ValueError(
                "pin-access witness is missing "
                f"{Signal}:{GateName}:{Role}:{PinId}"
            )
        return Match

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "WitnessFingerprint": self.WitnessFingerprint,
            "AccessLength": self.AccessLength,
            "SelectionCount": len(self.Selections),
            "SignalCount": len({
                Value.Signal for Value in self.Selections
            }),
            "CatalogMatched": self.CatalogMatched,
            "Selections": [
                Value.ToDictionary() for Value in self.Selections
            ],
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }


def BuildPlacementPinAccessWitness(
    PlacedGates: Any,
    *,
    AccessLength: int,
    RequireCatalogMatch: bool = True,
) -> PlacementPinAccessWitness:
    """Transform the cell catalog's straight patterns into placed paths."""
    if AccessLength < 1:
        raise ValueError("pin-access witness requires positive access length")
    Selections = []
    for Gate in PlacedGates:
        Macro = GetCellMacro(Gate.Kind)
        Rotation = int(getattr(Gate, "Rotation", 0))
        MirrorX = bool(getattr(Gate, "MirrorX", False))
        PatternsByPinId = {
            Pattern.PinId: Pattern
            for Pattern in Macro.PinAccessPatterns
        }

        def BuildSelection(
            Signal: str,
            Role: str,
            PinId: str,
            PhysicalTerminal: tuple[int, int, int],
            PhysicalDirection: tuple[int, int, int],
        ) -> PlacementPinAccessSelection:
            Pattern = PatternsByPinId.get(PinId)
            if Pattern is None:
                raise ValueError(
                    f"cell {Gate.Name} has no catalog pattern for {PinId}"
                )
            LocalTerminal = TransformLocalPosition(
                Pattern.ConnectionPosition,
                Macro.Footprint,
                Rotation,
                MirrorX,
            )
            CatalogTerminal = (
                Gate.X + LocalTerminal[0],
                Gate.Y + LocalTerminal[1],
                Gate.Z + LocalTerminal[2],
            )
            CatalogDirection = TransformDirection(
                Pattern.ApproachDirection,
                Rotation,
                MirrorX,
            )
            CatalogMatched = (
                CatalogTerminal == tuple(PhysicalTerminal)
                and CatalogDirection == tuple(PhysicalDirection)
                and AccessLength <= Pattern.AccessLength
            )
            if RequireCatalogMatch and not CatalogMatched:
                raise ValueError(
                    f"placed pin {Gate.Name}:{PinId} does not match its "
                    "catalog access pattern"
                )
            Terminal = (
                CatalogTerminal if CatalogMatched else tuple(PhysicalTerminal)
            )
            Direction = (
                CatalogDirection if CatalogMatched else tuple(PhysicalDirection)
            )
            return PlacementPinAccessSelection(
                Signal=str(Signal),
                GateName=str(Gate.Name),
                GateKind=str(Gate.Kind),
                Role=Role,
                PinId=PinId,
                PatternId=Pattern.PatternId,
                Terminal=Terminal,
                ApproachDirection=Direction,
                Path=tuple(
                    (
                        Terminal[0] + Direction[0] * Offset,
                        Terminal[1] + Direction[1] * Offset,
                        Terminal[2] + Direction[2] * Offset,
                    )
                    for Offset in range(AccessLength)
                ),
                CatalogAccessLength=Pattern.AccessLength,
                CatalogMatched=CatalogMatched,
            )

        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            Selections.extend(
                BuildSelection(
                    Signal,
                    "Source",
                    "Output0",
                    Gate.OutputPin,
                    Gate.OutputDirection,
                )
                for Signal in Gate.Outputs
            )
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            Selections.append(BuildSelection(
                Signal,
                "Target",
                f"Input{InputIndex}",
                Pin,
                Direction,
            ))
    Ordered = tuple(sorted(
        Selections,
        key=lambda Value: Value.StructuralIdentity(),
    ))
    CatalogMatched = all(Value.CatalogMatched for Value in Ordered)
    return PlacementPinAccessWitness(
        AccessLength=AccessLength,
        Selections=Ordered,
        Complete=CatalogMatched or not RequireCatalogMatch,
        IncompleteReason=(
            "" if CatalogMatched or not RequireCatalogMatch
            else "catalog-geometry-mismatch"
        ),
    )


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
    SelectedPinAccessWitness: SelectedPlacementPinAccessWitness | None = None
    PlacementAccessSolve: PlacementAccessSolveResult | None = None
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

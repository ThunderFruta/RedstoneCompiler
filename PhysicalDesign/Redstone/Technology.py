"""Authoritative Minecraft routing technology rules and primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Position3 = tuple[int, int, int]
RepeaterInputFacing = Literal["north", "south", "east", "west"]

HorizontalFacingDeltas: dict[RepeaterInputFacing, Position3] = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "east": (1, 0, 0),
    "west": (-1, 0, 0),
}
OppositeHorizontalFacings: dict[
    RepeaterInputFacing,
    RepeaterInputFacing,
] = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
}


def ValidateRepeaterInputFacing(Facing: str) -> RepeaterInputFacing:
    """Validate one Java repeater input-side ``facing`` blockstate."""
    if Facing not in HorizontalFacingDeltas:
        raise ValueError(
            f"Repeater input facing must be horizontal: {Facing!r}"
        )
    return Facing  # type: ignore[return-value]


def OppositeHorizontalFacing(
    Facing: RepeaterInputFacing | str,
) -> RepeaterInputFacing:
    """Return the opposite cardinal side after strict validation."""
    return OppositeHorizontalFacings[ValidateRepeaterInputFacing(Facing)]


def RepeaterInputDelta(
    InputFacing: RepeaterInputFacing | str,
) -> Position3:
    """Return the offset from a repeater to its Java input/rear side."""
    return HorizontalFacingDeltas[
        ValidateRepeaterInputFacing(InputFacing)
    ]


def RepeaterOutputDelta(
    InputFacing: RepeaterInputFacing | str,
) -> Position3:
    """Return the offset from a repeater to its driven output/front side."""
    return HorizontalFacingDeltas[
        OppositeHorizontalFacing(InputFacing)
    ]


def RepeaterInputFacingForStep(
    Current: Position3,
    Next: Position3,
) -> RepeaterInputFacing:
    """Return the Java input-facing state for power flowing to ``Next``."""
    Delta = (
        Next[0] - Current[0],
        Next[1] - Current[1],
        Next[2] - Current[2],
    )
    InputFacingByOutputDelta = {
        RepeaterOutputDelta(InputFacing): InputFacing
        for InputFacing in HorizontalFacingDeltas
    }
    try:
        return InputFacingByOutputDelta[Delta]
    except KeyError as Error:
        raise ValueError(
            "A routing repeater must lie on a flat straight run"
        ) from Error


@dataclass(frozen=True)
class TrackPrimitive:
    """One concrete electrically isolated redstone routing track."""

    PrimitiveId: str
    Direction: str
    Pitch: int
    RequiresSupport: bool
    RequiresHeadroom: bool


@dataclass(frozen=True)
class RedstoneRoutingTechnology:
    """Single owner for physical rules shared by placement and routing."""

    TechnologyVersion: str = "redstone-routing-v1"
    MaximumUnrefreshedDustLength: int = 15
    PreferredRepeaterInterval: int = 14
    ReservedRepeaterInterval: int = 8
    TrackPitch: int = 3
    AccessLength: int = 3
    DefaultSupportBlock: str = "minecraft:light_gray_concrete"
    RoutingLayerPitch: int = 2
    # A routing deck is physically legal as soon as it can be represented by
    # the technology's support/headroom and electrical rules.  The historic
    # three-deck value below remains the conservative legacy floor for paths
    # that have not selected an explicit pre-route envelope.  Small derived
    # placement domains may prove that one or two decks suffice.
    MinimumPhysicalRoutingLayerCount: int = 1
    MinimumRoutingLayerCount: int = 3
    MaximumRoutableLayerCount: int = 8

    @property
    def TrackPrimitives(self) -> tuple[TrackPrimitive, ...]:
        return (
            TrackPrimitive("DustX", "X", self.TrackPitch, True, True),
            TrackPrimitive("DustZ", "Z", self.TrackPitch, True, True),
        )

    def NeighborPositions(self, Position: Position3) -> tuple[Position3, ...]:
        """Enumerate every position to which dust can directly connect."""
        X, Y, Z = Position
        Result = [
            (X + 1, Y, Z),
            (X - 1, Y, Z),
            (X, Y, Z + 1),
            (X, Y, Z - 1),
        ]
        for DeltaX, DeltaZ in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            Result.append((X + DeltaX, Y + 1, Z + DeltaZ))
            Result.append((X + DeltaX, Y - 1, Z + DeltaZ))
        return tuple(Result)

    def AreConnected(self, First: Position3, Second: Position3) -> bool:
        """Return whether dust at two coordinates can connect."""
        return Second in self.NeighborPositions(First)

    def BuildElectricalExclusions(
        self,
        Positions: set[Position3],
    ) -> set[Position3]:
        """Expand occupied electrical cells by the connectivity rule."""
        Result = set(Positions)
        for Position in Positions:
            Result.update(self.NeighborPositions(Position))
        return Result

    def TorchPoweredDustKeepOut(self, Position: Position3) -> Position3:
        """Return dust above the solid block directly powered by a torch.

        Both standing and wall redstone torches power a solid block placed
        immediately above them. Dust on top of that block is therefore part
        of the torch's electrical domain even though it is two Y cells away.
        """
        return (Position[0], Position[1] + 2, Position[2])

    def RoutingY(self, BaseY: int, Layer: int) -> int:
        """Map a logical routing layer to its concrete dust elevation."""
        if Layer < 0:
            raise ValueError("Routing layer cannot be negative")
        return BaseY + 1 + self.RoutingLayerPitch * Layer

    def AccessLanding(self, Path: tuple[Position3, ...]) -> Position3:
        """Return the first support-safe routing cell beyond a pin escape."""
        if len(Path) < 2:
            raise ValueError("Pin access paths require at least two cells")
        Previous = Path[-2]
        Last = Path[-1]
        return (
            Last[0] + Last[0] - Previous[0],
            Last[1] + Last[1] - Previous[1],
            Last[2] + Last[2] - Previous[2],
        )


DefaultRedstoneRoutingTechnology = RedstoneRoutingTechnology()

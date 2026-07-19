"""Authoritative Minecraft routing technology rules and primitives."""

from __future__ import annotations

from dataclasses import dataclass


Position3 = tuple[int, int, int]


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
    PreferredRepeaterInterval: int = 12
    ReservedRepeaterInterval: int = 8
    TrackPitch: int = 3
    AccessLength: int = 3
    DefaultSupportBlock: str = "minecraft:smooth_stone"
    RoutingLayerPitch: int = 2
    MinimumRoutingLayerCount: int = 3
    MaximumRoutableLayerCount: int = 6

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

"""Authoritative physical macro definitions for redstone standard cells."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PinAccessPattern:
    """One named legal straight escape primitive for a logical cell pin."""

    PatternId: str
    PinId: str
    ConnectionPosition: tuple[int, int, int]
    ApproachDirection: tuple[int, int, int]
    AccessLength: int = 3
    AllowedRoutingLayers: tuple[int, ...] = ()


@dataclass(frozen=True)
class CellMacro:
    """Fixed geometry and pin contract for one placeable redstone cell."""

    Name: str
    Width: int
    Height: int
    Depth: int
    InputPins: tuple[tuple[int, int, int], ...]
    OutputPin: tuple[int, int, int] | None
    InputDirections: tuple[tuple[int, int, int], ...]
    OutputDirection: tuple[int, int, int] | None
    EstimatedBlocks: int
    AllowMirror: bool = False

    @property
    def PinAccessPatterns(self) -> tuple[PinAccessPattern, ...]:
        """Expose named pin escapes instead of recreating offsets downstream."""
        Inputs = tuple(
            PinAccessPattern(
                PatternId=f"Input{Index}Straight",
                PinId=f"Input{Index}",
                ConnectionPosition=Pin,
                ApproachDirection=self.InputDirections[Index],
            )
            for Index, Pin in enumerate(self.InputPins)
        )
        Output = (
            (
                PinAccessPattern(
                    PatternId="Output0Straight",
                    PinId="Output0",
                    ConnectionPosition=self.OutputPin,
                    ApproachDirection=self.OutputDirection,
                ),
            )
            if self.OutputPin is not None and self.OutputDirection is not None
            else ()
        )
        return Inputs + Output

    @property
    def Footprint(self) -> tuple[int, int]:
        return self.Width, self.Depth


CellMacros = {
    "INPUT": CellMacro(
        Name="INPUT",
        Width=1,
        Height=1,
        Depth=3,
        InputPins=(),
        OutputPin=(0, 0, 3),
        InputDirections=(),
        OutputDirection=(0, 0, 1),
        EstimatedBlocks=7,
    ),
    "NAND": CellMacro(
        Name="NAND2",
        Width=3,
        Height=1,
        Depth=4,
        InputPins=((0, 0, -1), (2, 0, -1)),
        OutputPin=(1, 0, 4),
        InputDirections=((0, 0, -1), (0, 0, -1)),
        OutputDirection=(0, 0, 1),
        EstimatedBlocks=11,
        AllowMirror=True,
    ),
    "OUTPUT": CellMacro(
        Name="OUTPUT",
        Width=1,
        Height=1,
        Depth=2,
        InputPins=((0, 0, -1),),
        OutputPin=None,
        InputDirections=((0, 0, -1),),
        OutputDirection=None,
        EstimatedBlocks=5,
    ),
}


def GetCellMacro(CellKind: str) -> CellMacro:
    """Return the standard macro for a placed IR gate kind."""
    try:
        return CellMacros[CellKind.upper()]
    except KeyError as Error:
        raise ValueError(f"No physical cell macro is defined for {CellKind}") from Error

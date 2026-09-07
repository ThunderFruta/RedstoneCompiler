"""Authoritative physical macro definitions for redstone standard cells."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PinAccessPattern:
    """One finite cell-local seed for a logical pin-access pattern."""

    PatternId: str
    PinId: str
    ConnectionPosition: tuple[int, int, int]
    ApproachDirection: tuple[int, int, int]
    PatternFamily: str = "straight"
    TangentialSign: int = 0
    AccessLength: int = 3
    AllowedRoutingLayers: tuple[int, ...] = (0,)

    def __post_init__(self) -> None:
        if self.PatternFamily not in {"straight", "planar-jog"}:
            raise ValueError("pin-access pattern family is unsupported")
        ExpectedSigns = (
            {0} if self.PatternFamily == "straight" else {-1, 1}
        )
        if self.TangentialSign not in ExpectedSigns:
            raise ValueError("pin-access pattern tangential sign is invalid")
        if self.AccessLength < 1:
            raise ValueError("pin-access pattern length must be positive")
        if (
            self.ApproachDirection[1] != 0
            or sum(abs(Value) for Value in self.ApproachDirection) != 1
        ):
            raise ValueError(
                "pin-access pattern approach must be horizontal cardinal"
            )
        if self.AllowedRoutingLayers != (0,):
            raise ValueError(
                "the initial pin-access catalog supports only layer zero"
            )


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
    StaticSignalRoles: tuple[
        tuple[tuple[int, int, int], str], ...
    ] = ()

    def __post_init__(self) -> None:
        if self.StaticSignalRoles != tuple(sorted(set(self.StaticSignalRoles))):
            raise ValueError(
                "cell static signal roles must be sorted and unique"
            )
        for _Position, Role in self.StaticSignalRoles:
            if Role == "Output0":
                if self.OutputPin is None:
                    raise ValueError(
                        "cell static output role requires an output pin"
                    )
                continue
            if not Role.startswith("Input") or not Role[5:].isdigit():
                raise ValueError("cell static signal role is invalid")
            if int(Role[5:]) >= len(self.InputPins):
                raise ValueError(
                    "cell static input role is outside the pin domain"
                )

    @property
    def PinAccessPatterns(self) -> tuple[PinAccessPattern, ...]:
        """Expose the legacy straight-only seed for each physical pin.

        The v16 placement witness consumes this property and intentionally
        remains singleton.  The routing-aware strategy consumes
        ``RoutingAwarePinAccessPatterns`` instead.
        """
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
    def RoutingAwarePinAccessPatterns(self) -> tuple[PinAccessPattern, ...]:
        """Return straight and symmetric planar-jog seeds for every pin."""
        Results = []
        for Straight in self.PinAccessPatterns:
            Results.extend((
                Straight,
                PinAccessPattern(
                    PatternId=f"{Straight.PinId}PlanarJogNegative",
                    PinId=Straight.PinId,
                    ConnectionPosition=Straight.ConnectionPosition,
                    ApproachDirection=Straight.ApproachDirection,
                    PatternFamily="planar-jog",
                    TangentialSign=-1,
                    AccessLength=Straight.AccessLength,
                ),
                PinAccessPattern(
                    PatternId=f"{Straight.PinId}PlanarJogPositive",
                    PinId=Straight.PinId,
                    ConnectionPosition=Straight.ConnectionPosition,
                    ApproachDirection=Straight.ApproachDirection,
                    PatternFamily="planar-jog",
                    TangentialSign=1,
                    AccessLength=Straight.AccessLength,
                ),
            ))
        return tuple(Results)

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
        StaticSignalRoles=(
            ((0, 0, 0), "Output0"),
            ((0, 0, 1), "Output0"),
            ((0, 0, 2), "Output0"),
        ),
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
        StaticSignalRoles=(
            ((0, 0, 0), "Input0"),
            ((0, 0, 1), "Input0"),
            ((0, 0, 2), "Output0"),
            ((1, 0, 2), "Output0"),
            ((1, 0, 3), "Output0"),
            ((2, 0, 0), "Input1"),
            ((2, 0, 1), "Input1"),
            ((2, 0, 2), "Output0"),
        ),
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
        StaticSignalRoles=(
            ((0, 0, 0), "Input0"),
            ((0, 0, 1), "Input0"),
        ),
    ),
}


def GetCellMacro(CellKind: str) -> CellMacro:
    """Return the standard macro for a placed IR gate kind."""
    try:
        return CellMacros[CellKind.upper()]
    except KeyError as Error:
        raise ValueError(f"No physical cell macro is defined for {CellKind}") from Error

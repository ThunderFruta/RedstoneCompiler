"""NAND cell geometry and pin convention."""

from dataclasses import dataclass

from .Library import GetCellMacro


@dataclass(frozen=True)
class NandCellSpec:
    Name: str = "NAND2"
    Width: int = 3
    Height: int = 1
    Depth: int = 4

    # Pin offsets from cell origin (x, y, z)
    AIn: tuple[int, int, int] = (0, 0, -1)
    BIn: tuple[int, int, int] = (2, 0, -1)
    Out: tuple[int, int, int] = (1, 0, 4)


StandardNandCell = GetCellMacro("NAND")

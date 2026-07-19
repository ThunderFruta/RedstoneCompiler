"""IR model for synthesized logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any


class GateKind(str, Enum):
    NAND = "NAND"
    NOT = "NOT"
    AND = "AND"
    OR = "OR"
    XOR = "XOR"
    BUFFER = "BUFFER"
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


@dataclass
class NetIR:
    """Represents a named signal in the IR."""

    Name: str
    Width: int = 1
    IsConstant: bool = False
    Value: int | None = None


@dataclass
class Gate:
    """A logic gate node."""

    Name: str
    Kind: GateKind
    Outputs: list[str]
    Inputs: list[str] = field(default_factory=list)
    Attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def Output(self) -> str:
        if not self.Outputs:
            raise ValueError(f"gate {self.Name} has no outputs")
        return self.Outputs[0]


@dataclass
class ModuleIR:
    """Single-module flattened IR."""

    Name: str
    Ports: dict[str, int] = field(default_factory=dict)
    Inputs: list[str] = field(default_factory=list)
    Outputs: list[str] = field(default_factory=list)
    Nets: dict[str, NetIR] = field(default_factory=dict)
    Gates: list[Gate] = field(default_factory=list)
    SourcePath: Path | None = None


@dataclass
class NetlistIR:
    """Top-level design payload."""

    Top: str
    Modules: dict[str, ModuleIR] = field(default_factory=dict)

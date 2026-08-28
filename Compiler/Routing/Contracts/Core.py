"""Resource primitives shared by routing contract families."""

from __future__ import annotations

from dataclasses import dataclass

Position2 = tuple[int, int]
Position3 = tuple[int, int, int]

@dataclass(frozen=True)
class RoutingStaticGeometry:
    ActualBlocks: frozenset[Position3]
    ElectricalBlocks: frozenset[Position3]
    SolidBlocks: frozenset[Position3] = frozenset()
    TemplateElectricalBlocks: frozenset[Position3] = frozenset()

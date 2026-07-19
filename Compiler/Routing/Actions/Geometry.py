"""Static cell geometry and redstone-neighborhood actions."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from ...Placement.Rotation import TransformLocalPosition
from SchemEncoder.Writer262 import LoadTemplate
from Templates import LitematicTemplates
from ..Models import Position3, RoutingResources, RoutingStaticGeometry
from ..ResourceGraph import RoutingResourceGraph
from ..Technology import DefaultRedstoneRoutingTechnology

ElectricalBlockNames = {
    "minecraft:comparator",
    "minecraft:lever",
    "minecraft:redstone_torch",
    "minecraft:redstone_wall_torch",
    "minecraft:redstone_wire",
    "minecraft:repeater",
}
NonSolidBlockNames = ElectricalBlockNames | {"minecraft:air"}


@lru_cache(maxsize=1)
def LoadRoutingTemplates() -> dict[str, Any]:
    """Load exact template occupancy once per routing worker."""
    return {
        Name.upper(): LoadTemplate(PathValue)
        for Name, PathValue in LitematicTemplates.items()
    }


def BuildPlacedCellGeometry(
    Placed: Any,
) -> tuple[set[Position3], set[Position3], set[Position3]]:
    """Return occupied, electrical, and solid template positions."""
    Templates = LoadRoutingTemplates()
    OccupiedOwners: dict[Position3, str] = {}
    ElectricalBlocks: set[Position3] = set()
    SolidBlocks: set[Position3] = set()

    for Gate in Placed.PlacedGates:
        Template = Templates[Gate.Kind]
        for LocalPosition, State in Template.Blocks.items():
            Rotated = TransformLocalPosition(
                LocalPosition,
                (Template.Size[0], Template.Size[2]),
                Gate.Rotation,
                Gate.MirrorX,
            )
            Position = (
                Gate.X + Rotated[0],
                Gate.Y + Rotated[1],
                Gate.Z + Rotated[2],
            )
            ExistingOwner = OccupiedOwners.get(Position)
            if ExistingOwner is not None and ExistingOwner != Gate.Name:
                raise ValueError(
                    f"Placed cells overlap at {Position}: "
                    f"{ExistingOwner} and {Gate.Name}"
                )
            OccupiedOwners[Position] = Gate.Name
            if State["Name"] in ElectricalBlockNames:
                ElectricalBlocks.add(Position)
            if State["Name"] not in NonSolidBlockNames:
                SolidBlocks.add(Position)
    return set(OccupiedOwners), ElectricalBlocks, SolidBlocks


def BuildRoutingResources(Placed: Any) -> RoutingResources:
    """Build placement geometry once for reuse across routing retries."""
    ActualBlocks, ElectricalBlocks, SolidBlocks = BuildPlacedCellGeometry(Placed)
    # Complete local nets are immutable obstacles to every remaining signal.
    # Partial claims are carried inside their signal's route candidates.
    ElectricalBlocks.update(
        Position
        for Positions in (getattr(Placed, "FrozenNetWires", None) or {}).values()
        for Position in Positions
    )
    StaticGeometry = RoutingStaticGeometry(
        ActualBlocks=frozenset(ActualBlocks),
        ElectricalBlocks=frozenset(ElectricalBlocks),
        SolidBlocks=frozenset(SolidBlocks),
    )
    return RoutingResources(
        StaticGeometry=StaticGeometry,
        ResourceGraph=RoutingResourceGraph(
            ActualBlocks=StaticGeometry.ActualBlocks,
            ElectricalBlocks=StaticGeometry.ElectricalBlocks,
            SolidBlocks=StaticGeometry.SolidBlocks,
        ),
    )


def AreConnected(First: Position3, Second: Position3) -> bool:
    """Return whether redstone dust at two coordinates can connect."""
    return DefaultRedstoneRoutingTechnology.AreConnected(First, Second)


def BuildElectricalExclusions(Positions: set[Position3]) -> set[Position3]:
    """Expand positions into their direct redstone connection neighborhood."""
    return DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(Positions)


def NeighborPositions(Position: Position3) -> list[Position3]:
    return list(DefaultRedstoneRoutingTechnology.NeighborPositions(Position))

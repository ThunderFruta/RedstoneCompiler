"""Static cell geometry and redstone-neighborhood actions."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

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
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[set[Position3], set[Position3], set[Position3]]:
    """Return occupied, electrical, and solid template positions."""
    Templates = LoadRoutingTemplates()
    OccupiedOwners: dict[Position3, str] = {}
    ElectricalBlocks: set[Position3] = set()
    SolidBlocks: set[Position3] = set()

    Gates = list(Placed.PlacedGates)
    for GateIndex, Gate in enumerate(Gates):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "placed-cell-geometry-gate",
                "CompletedGates": GateIndex,
                "TotalGates": len(Gates),
                "GateName": Gate.Name,
            })
        Template = Templates[Gate.Kind]
        for BlockIndex, (LocalPosition, State) in enumerate(
            Template.Blocks.items()
        ):
            if WorkCheck is not None and BlockIndex % 64 == 0:
                WorkCheck({
                    "Phase": "placed-cell-geometry-block",
                    "GateName": Gate.Name,
                    "CompletedBlocks": BlockIndex,
                    "TotalBlocks": len(Template.Blocks),
                })
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


def ValidatePlacedCellElectricalIsolation(
    Placed: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> None:
    """Reject template adjacency that can create stateful redstone feedback."""
    Geometry = []
    Gates = list(Placed.PlacedGates)
    for GateIndex, Gate in enumerate(Gates):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "electrical-isolation-geometry",
                "CompletedGates": GateIndex,
                "TotalGates": len(Gates),
                "GateName": Gate.Name,
            })
        Actual, Electrical, _Solid = BuildPlacedCellGeometry(
            type("SingleCellPlacement", (), {"PlacedGates": [Gate]})(),
            WorkCheck=WorkCheck,
        )
        Geometry.append((Gate.Name, Actual, Electrical))
    Technology = DefaultRedstoneRoutingTechnology
    for Index, (FirstName, FirstActual, FirstElectrical) in enumerate(Geometry):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "electrical-isolation-pairs",
                "CompletedFirstGates": Index,
                "TotalGates": len(Geometry),
                "GateName": FirstName,
            })
        FirstKeepOut = Technology.BuildElectricalExclusions(set(FirstElectrical))
        for PairIndex, (
            SecondName,
            SecondActual,
            SecondElectrical,
        ) in enumerate(Geometry[Index + 1 :]):
            if WorkCheck is not None and PairIndex % 32 == 0:
                WorkCheck({
                    "Phase": "electrical-isolation-pair",
                    "FirstGateName": FirstName,
                    "SecondGateName": SecondName,
                    "CompletedPairsForGate": PairIndex,
                })
            Conflicts = (FirstKeepOut & set(SecondActual)) | (
                Technology.BuildElectricalExclusions(set(SecondElectrical))
                & set(FirstActual)
            )
            if Conflicts:
                raise ValueError(
                    "Placed templates violate electrical isolation: "
                    f"{FirstName},{SecondName} at {sorted(Conflicts)[:8]}"
                )


def BuildRoutingResources(
    Placed: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> RoutingResources:
    """Build placement geometry once for reuse across routing retries."""
    if WorkCheck is not None:
        WorkCheck({"Phase": "routing-resources-start"})
    ValidatePlacedCellElectricalIsolation(Placed, WorkCheck=WorkCheck)
    ActualBlocks, ElectricalBlocks, SolidBlocks = BuildPlacedCellGeometry(
        Placed,
        WorkCheck=WorkCheck,
    )
    TemplateElectricalBlocks = frozenset(ElectricalBlocks)
    # Complete local nets are immutable obstacles to every remaining signal.
    # Partial claims are carried inside their signal's route candidates.
    FrozenNetWires = getattr(Placed, "FrozenNetWires", None) or {}
    FrozenPositionCount = 0
    for SignalIndex, (Signal, Positions) in enumerate(
        sorted(FrozenNetWires.items())
    ):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "routing-resources-frozen-net",
                "CompletedSignals": SignalIndex,
                "TotalSignals": len(FrozenNetWires),
                "Signal": Signal,
            })
        for Position in Positions:
            ElectricalBlocks.add(Position)
            FrozenPositionCount += 1
            if WorkCheck is not None and FrozenPositionCount % 256 == 0:
                WorkCheck({
                    "Phase": "routing-resources-frozen-position",
                    "Signal": Signal,
                    "CompletedSignals": SignalIndex,
                    "ProcessedPositions": FrozenPositionCount,
                })
    StaticGeometry = RoutingStaticGeometry(
        ActualBlocks=frozenset(ActualBlocks),
        ElectricalBlocks=frozenset(ElectricalBlocks),
        SolidBlocks=frozenset(SolidBlocks),
        TemplateElectricalBlocks=TemplateElectricalBlocks,
    )
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "routing-resources-complete",
            "FrozenPositionCount": FrozenPositionCount,
        })
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

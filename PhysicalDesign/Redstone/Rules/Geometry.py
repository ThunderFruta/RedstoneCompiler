"""Static cell geometry and redstone-neighborhood actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Callable

from ...Geometry.Rotation import TransformBlockState, TransformLocalPosition
from Formats.Litematic.Codec import LoadTemplate
from Assets.Templates import LitematicTemplates
from ...Contracts.Core import Position3, RoutingStaticGeometry
from ...Contracts.Results import RoutingResources
from ...Resources.ResourceGraph import RoutingResourceGraph
from ...Resources.ResourceGraph import FreezeRoutingResourceState
from ..Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)

ElectricalBlockNames = {
    "minecraft:comparator",
    "minecraft:lever",
    "minecraft:redstone_torch",
    "minecraft:redstone_wall_torch",
    "minecraft:redstone_wire",
    "minecraft:repeater",
}
NonSolidBlockNames = ElectricalBlockNames | {"minecraft:air"}
TorchBlockNames = {
    "minecraft:redstone_torch",
    "minecraft:redstone_wall_torch",
}


@dataclass(frozen=True)
class PlacedTemplateRoutingStateSnapshot:
    """One re-attestable complete explicit template-state observation."""

    ActualBlocks: frozenset[Position3]
    ElectricalBlocks: frozenset[Position3]
    SolidBlocks: frozenset[Position3]
    ElectricalKeepOutBlocks: frozenset[Position3]
    BlockStates: MappingProxyType
    IsolationGeometry: tuple[tuple[str, frozenset[Position3], frozenset[Position3], frozenset[Position3]], ...]
    SourceObservations: tuple[tuple[object, ...], ...]
    _SourceCapture: "_PlacedTemplateSourceCapture"


@dataclass(frozen=True)
class _CapturedRoutingTemplate:
    """One complete, locally-owned template observation used for derivation."""

    Key: str
    Template: Any
    TemplateClass: type
    Size: tuple[int, int, int]
    Blocks: dict[Position3, dict[str, Any]]
    Entries: tuple[tuple[Position3, Any, tuple[object, ...]], ...]


@dataclass(frozen=True)
class _PlacedTemplateSourceCapture:
    """Non-portable source identities kept only to reject publication drift."""

    GateCollection: Any
    GateCollectionClass: type
    Gates: tuple[Any, ...]
    GateObservations: tuple[tuple[object, ...], ...]
    Templates: dict[str, Any]
    TemplateDomain: tuple[str, ...]
    UsedTemplateKeys: tuple[str, ...]
    TemplatesByKey: tuple[_CapturedRoutingTemplate, ...]
    FrozenNetWireMapping: Any
    FrozenNetWireMappingClass: type
    FrozenNetWireEntries: tuple[tuple[str, tuple[Position3, ...]], ...]


def _PlacedTemplateGateObservation(Gate: Any) -> tuple[object, ...]:
    Values = (
        Gate.Name, Gate.Kind, Gate.X, Gate.Y, Gate.Z, Gate.Rotation, Gate.MirrorX,
    )
    if type(Gate.Name) is not str or type(Gate.Kind) is not str:
        raise TypeError("placed template gate identity must be exact strings")
    if any(type(Value) is not int for Value in (Gate.X, Gate.Y, Gate.Z, Gate.Rotation)):
        raise TypeError("placed template gate geometry must use exact integers")
    if type(Gate.MirrorX) is not bool:
        raise TypeError("placed template gate mirror must be an exact bool")
    return Values


def _TypeSensitiveStateObservation(Value: Any, Seen: set[int] | None = None) -> tuple[object, ...]:
    """Retain exact input scalar/container types for live-source attestation."""
    Active = Seen if Seen is not None else set()
    if Value is None:
        return ("none",)
    if type(Value) in (bool, int, str):
        return (type(Value).__name__, Value)
    if type(Value) is float:
        return ("float", Value.hex())
    if type(Value) is dict:
        if id(Value) in Active:
            raise TypeError("placed template states cannot be cyclic")
        if any(type(Key) is not str for Key in Value):
            raise TypeError("placed template state keys must be exact strings")
        Active.add(id(Value))
        try:
            return ("dict", tuple(
                (Key, _TypeSensitiveStateObservation(Item, Active))
                for Key, Item in Value.items()
            ))
        finally:
            Active.remove(id(Value))
    if type(Value) in (tuple, list):
        if id(Value) in Active:
            raise TypeError("placed template states cannot be cyclic")
        Active.add(id(Value))
        try:
            return (type(Value).__name__, tuple(
                _TypeSensitiveStateObservation(Item, Active)
                for Item in Value
            ))
        finally:
            Active.remove(id(Value))
    raise TypeError("placed template state is not losslessly re-attestable")


def _ThawRoutingResourceState(Value: Any) -> Any:
    if type(Value) is MappingProxyType:
        return {Key: _ThawRoutingResourceState(Item) for Key, Item in Value.items()}
    if type(Value) is tuple:
        return tuple(_ThawRoutingResourceState(Item) for Item in Value)
    return Value


def _CaptureRoutingTemplate(Key: str, Template: Any) -> _CapturedRoutingTemplate:
    Size = Template.Size
    Blocks = Template.Blocks
    if (
        type(Size) is not tuple or len(Size) != 3
        or any(type(Value) is not int for Value in Size)
        or type(Blocks) is not dict
    ):
        raise TypeError("routing template shape is malformed")
    if any(Value <= 0 for Value in Size):
        raise ValueError("routing template size must contain three positive exact integers")
    Entries = []
    for LocalPosition, State in Blocks.items():
        if (
            type(LocalPosition) is not tuple or len(LocalPosition) != 3
            or any(type(Value) is not int for Value in LocalPosition)
            or type(State) is not dict or type(State.get("Name")) is not str
        ):
            raise TypeError("placed template state entry is malformed")
        if any(
            LocalPosition[Axis] < 0 or LocalPosition[Axis] >= Size[Axis]
            for Axis in range(3)
        ):
            raise ValueError("placed template local position is outside template size")
        if set(State) - {"Name", "Properties"}:
            raise ValueError("placed template state has fields the transform cannot preserve")
        if "Properties" in State and type(State["Properties"]) is not dict:
            raise TypeError("placed template state Properties must be an exact dict")
        if "Properties" in State and not State["Properties"]:
            raise ValueError("placed template state has fields the transform cannot preserve")
        if "Properties" in State and any(
            type(PropertyName) is not str or type(PropertyValue) is not str
            for PropertyName, PropertyValue in State["Properties"].items()
        ):
            raise TypeError("placed template state Properties must use exact strings")
        if (
            "Properties" in State
            and "rotation" in State["Properties"]
            and State["Properties"]["rotation"] not in {
                str(Value) for Value in range(16)
            }
        ):
            raise ValueError("placed template rotation property must be canonical 0 through 15")
        FrozenState = FreezeRoutingResourceState(State)
        Entries.append((
            LocalPosition,
            FrozenState,
            _TypeSensitiveStateObservation(State),
        ))
    if len({Position for Position, _State, _Observation in Entries}) != len(Entries):
        raise ValueError("placed template repeats a local position")
    return _CapturedRoutingTemplate(
        Key=Key,
        Template=Template,
        TemplateClass=type(Template),
        Size=Size,
        Blocks=Blocks,
        Entries=tuple(Entries),
    )


def _CaptureFrozenNetWireSources(
    Placed: Any,
) -> tuple[Any, type, tuple[tuple[str, tuple[Position3, ...]], ...]]:
    Source = getattr(Placed, "FrozenNetWires", None)
    if Source is None:
        return None, type(None), ()
    if not isinstance(Source, Mapping):
        raise TypeError("placed frozen wires must be a mapping or None")
    RawEntries = tuple(Source.items())
    Signals = tuple(Signal for Signal, _Positions in RawEntries)
    if any(type(Signal) is not str for Signal in Signals):
        raise TypeError("placed frozen-wire signals must be exact strings")
    if len(Signals) != len(set(Signals)):
        raise ValueError("placed frozen wires repeat a signal")
    CapturedEntries = []
    for Signal, Positions in RawEntries:
        Materialized = tuple(Positions)
        if any(
            type(Position) not in (tuple, list)
            or len(Position) != 3
            or any(type(Component) is not int for Component in Position)
            for Position in Materialized
        ):
            raise TypeError(
                "placed frozen-wire positions must be exact three-integer positions"
            )
        Normalized = tuple(tuple(Position) for Position in Materialized)
        if len(Normalized) != len(set(Normalized)):
            raise ValueError("placed frozen wires repeat a position")
        CapturedEntries.append((Signal, tuple(sorted(Normalized))))
    return Source, type(Source), tuple(sorted(CapturedEntries))


def _CapturePlacedTemplateSources(Placed: Any) -> _PlacedTemplateSourceCapture:
    GateCollection = Placed.PlacedGates
    if type(GateCollection) not in (list, tuple):
        raise TypeError("placed template gates must be an exact list or tuple")
    Gates = tuple(GateCollection)
    GateObservations = tuple(_PlacedTemplateGateObservation(Gate) for Gate in Gates)
    Identities = tuple(Observation[0] for Observation in GateObservations)
    if len(Identities) != len(set(Identities)):
        raise ValueError("placed template snapshot repeats a gate identity")
    FrozenNetWireMapping, FrozenNetWireMappingClass, FrozenNetWireEntries = (
        _CaptureFrozenNetWireSources(Placed)
    )
    Templates = LoadRoutingTemplates()
    if type(Templates) is not dict:
        raise TypeError("routing templates must be an exact re-attestable dict")
    if any(type(Key) is not str for Key in Templates):
        raise TypeError("routing template keys must be exact strings")
    UsedTemplateKeys = tuple(sorted({Observation[1].upper() for Observation in GateObservations}))
    Captured = []
    for Key in UsedTemplateKeys:
        if Key not in Templates:
            raise ValueError("routing templates omit placed gate kind " + Key)
        Captured.append(_CaptureRoutingTemplate(Key, Templates[Key]))
    return _PlacedTemplateSourceCapture(
        GateCollection=GateCollection,
        GateCollectionClass=type(GateCollection),
        Gates=Gates,
        GateObservations=GateObservations,
        Templates=Templates,
        TemplateDomain=tuple(sorted(Templates)),
        UsedTemplateKeys=UsedTemplateKeys,
        TemplatesByKey=tuple(Captured),
        FrozenNetWireMapping=FrozenNetWireMapping,
        FrozenNetWireMappingClass=FrozenNetWireMappingClass,
        FrozenNetWireEntries=FrozenNetWireEntries,
    )


def _ReattestPlacedTemplateSources(
    Placed: Any,
    Capture: _PlacedTemplateSourceCapture,
) -> None:
    """Reject collection, template, or deep block drift before publication."""
    Current = _CapturePlacedTemplateSources(Placed)
    if (
        Current.FrozenNetWireMapping is not Capture.FrozenNetWireMapping
        or Current.FrozenNetWireMappingClass is not Capture.FrozenNetWireMappingClass
        or Current.FrozenNetWireEntries != Capture.FrozenNetWireEntries
    ):
        raise ValueError("placed frozen-wire inputs changed before publication")
    SourceIdentityChanged = (
        Current.GateCollection is not Capture.GateCollection
        or Current.GateCollectionClass is not Capture.GateCollectionClass
        or tuple(map(id, Current.Gates)) != tuple(map(id, Capture.Gates))
        or Current.Templates is not Capture.Templates
        or Current.TemplateDomain != Capture.TemplateDomain
        or Current.UsedTemplateKeys != Capture.UsedTemplateKeys
        or len(Current.TemplatesByKey) != len(Capture.TemplatesByKey)
        or any(
            CurrentTemplate.Key != CapturedTemplate.Key
            or CurrentTemplate.Template is not CapturedTemplate.Template
            or CurrentTemplate.TemplateClass is not CapturedTemplate.TemplateClass
            or CurrentTemplate.Blocks is not CapturedTemplate.Blocks
            for CurrentTemplate, CapturedTemplate in zip(
                Current.TemplatesByKey, Capture.TemplatesByKey
            )
        )
    )
    if (
        SourceIdentityChanged
        or Current.GateObservations != Capture.GateObservations
        or tuple(
            (Template.Size, Template.Entries)
            for Template in Current.TemplatesByKey
        ) != tuple(
            (Template.Size, Template.Entries)
            for Template in Capture.TemplatesByKey
        )
    ):
        raise ValueError("placed template inputs changed before publication")


def BuildPlacedTemplateRoutingStateSnapshot(
    Placed: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> PlacedTemplateRoutingStateSnapshot:
    """Transform every explicit template state once without last-write-wins.

    Explicit air stays observable in BlockStates but is not occupancy. Template
    loading currently filters file-air, so this is not a full-volume air claim.
    """
    Capture = _CapturePlacedTemplateSources(Placed)
    TemplatesByKey = {Template.Key: Template for Template in Capture.TemplatesByKey}
    OrderedGateRecords = tuple(sorted(
        zip(Capture.Gates, Capture.GateObservations),
        key=lambda Value: Value[1][0],
    ))
    States: dict[Position3, Any] = {}
    Owners: dict[Position3, str] = {}
    ElectricalBlocks: set[Position3] = set()
    SolidBlocks: set[Position3] = set()
    KeepOut: set[Position3] = set()
    IsolationGeometry = []
    for GateIndex, (_Gate, GateObservation) in enumerate(OrderedGateRecords):
        GateName, GateKind, GateX, GateY, GateZ, GateRotation, GateMirrorX = GateObservation
        Template = TemplatesByKey[GateKind.upper()]
        LocalEntries = Template.Entries
        if WorkCheck is not None:
            WorkCheck({"Phase": "placed-template-state-gate", "CompletedGates": GateIndex, "TotalGates": len(OrderedGateRecords), "GateName": GateName})
        GateActual: set[Position3] = set()
        GateElectrical: set[Position3] = set()
        GateKeepOut: set[Position3] = set()
        for BlockIndex, (LocalPosition, FrozenState, _StateObservation) in enumerate(LocalEntries):
            if WorkCheck is not None and BlockIndex % 64 == 0:
                WorkCheck({"Phase": "placed-template-state-block", "GateName": GateName, "CompletedBlocks": BlockIndex, "TotalBlocks": len(LocalEntries)})
            Rotated = TransformLocalPosition(
                LocalPosition,
                (Template.Size[0], Template.Size[2]),
                GateRotation,
                GateMirrorX,
            )
            Position = (GateX + Rotated[0], GateY + Rotated[1], GateZ + Rotated[2])
            if Position in Owners:
                raise ValueError("placed template states overlap at " + str(Position))
            Transformed = FreezeRoutingResourceState(TransformBlockState(
                _ThawRoutingResourceState(FrozenState),
                GateRotation,
                GateMirrorX,
            ))
            Owners[Position] = GateName
            States[Position] = Transformed
            if Transformed["Name"] == "minecraft:air":
                continue
            GateActual.add(Position)
            if Transformed["Name"] in ElectricalBlockNames:
                ElectricalBlocks.add(Position)
                GateElectrical.add(Position)
            if Transformed["Name"] in TorchBlockNames:
                KeepOut.add(Technology.TorchPoweredDustKeepOut(Position))
                GateKeepOut.add(Technology.TorchPoweredDustKeepOut(Position))
            if Transformed["Name"] not in NonSolidBlockNames:
                SolidBlocks.add(Position)
        IsolationGeometry.append((GateName, frozenset(GateActual), frozenset(GateElectrical), frozenset(GateKeepOut)))
    _ReattestPlacedTemplateSources(Placed, Capture)
    ActualBlocks = frozenset(
        Position for Position, State in States.items()
        if State["Name"] != "minecraft:air"
    )
    return PlacedTemplateRoutingStateSnapshot(
        ActualBlocks=ActualBlocks,
        ElectricalBlocks=frozenset(ElectricalBlocks),
        SolidBlocks=frozenset(SolidBlocks),
        ElectricalKeepOutBlocks=frozenset(KeepOut),
        BlockStates=MappingProxyType({
            Position: States[Position] for Position in sorted(States)
        }),
        IsolationGeometry=tuple(IsolationGeometry),
        SourceObservations=Capture.GateObservations,
        _SourceCapture=Capture,
    )


def _ValidateSnapshotElectricalIsolation(
    Snapshot: PlacedTemplateRoutingStateSnapshot,
    Technology: RedstoneRoutingTechnology,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> None:
    for Index, (FirstName, FirstActual, FirstElectrical, FirstKeepOut) in enumerate(Snapshot.IsolationGeometry):
        FirstExclusions = Technology.BuildElectricalExclusions(set(FirstElectrical)) | set(FirstKeepOut)
        for SecondName, SecondActual, SecondElectrical, SecondKeepOut in Snapshot.IsolationGeometry[Index + 1:]:
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "placed-template-state-isolation",
                    "FirstGateName": FirstName,
                    "SecondGateName": SecondName,
                })
            Conflicts = (FirstExclusions & set(SecondActual)) | ((Technology.BuildElectricalExclusions(set(SecondElectrical)) | set(SecondKeepOut)) & set(FirstActual))
            if Conflicts:
                raise ValueError(f"Placed templates violate electrical isolation: {FirstName},{SecondName} at {sorted(Conflicts)[:8]}")


@lru_cache(maxsize=1)
def LoadRoutingTemplates() -> dict[str, Any]:
    """Load exact template occupancy once per routing worker."""
    return {
        Name.upper(): LoadTemplate(PathValue)
        for Name, PathValue in LitematicTemplates.items()
    }


def BuildPlacedCellGeometryWithKeepOut(
    Placed: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> tuple[
    set[Position3],
    set[Position3],
    set[Position3],
    set[Position3],
]:
    """Return template occupancy plus block-aware electrical keep-outs."""
    Snapshot = BuildPlacedTemplateRoutingStateSnapshot(
        Placed, WorkCheck=WorkCheck, Technology=Technology,
    )
    return (
        set(Snapshot.ActualBlocks),
        set(Snapshot.ElectricalBlocks),
        set(Snapshot.SolidBlocks),
        set(Snapshot.ElectricalKeepOutBlocks),
    )


def BuildPlacedCellGeometry(
    Placed: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> tuple[set[Position3], set[Position3], set[Position3]]:
    """Return occupied, electrical, and solid template positions."""
    Actual, Electrical, Solid, _KeepOut = BuildPlacedCellGeometryWithKeepOut(
        Placed,
        WorkCheck=WorkCheck,
        Technology=Technology,
    )
    return Actual, Electrical, Solid


def ValidatePlacedCellElectricalIsolation(
    Placed: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> None:
    """Reject template adjacency that can create stateful redstone feedback."""
    Snapshot = BuildPlacedTemplateRoutingStateSnapshot(
        Placed,
        WorkCheck=WorkCheck,
        Technology=Technology,
    )
    _ValidateSnapshotElectricalIsolation(Snapshot, Technology, WorkCheck)
    _ReattestPlacedTemplateSources(Placed, Snapshot._SourceCapture)


def BuildRoutingResources(
    Placed: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> RoutingResources:
    """Build placement geometry once for reuse across routing retries."""
    Snapshot = BuildPlacedTemplateRoutingStateSnapshot(
        Placed,
        WorkCheck=WorkCheck,
        Technology=Technology,
    )
    if WorkCheck is not None:
        WorkCheck({"Phase": "routing-resources-start"})
    _ValidateSnapshotElectricalIsolation(Snapshot, Technology, WorkCheck)
    ActualBlocks = set(Snapshot.ActualBlocks)
    ElectricalBlocks = set(Snapshot.ElectricalBlocks)
    SolidBlocks = set(Snapshot.SolidBlocks)
    TemplateElectricalKeepOutBlocks = set(Snapshot.ElectricalKeepOutBlocks)
    TemplateElectricalBlocks = frozenset(ElectricalBlocks)
    # Complete local nets are immutable obstacles to every remaining signal.
    # Partial claims are carried inside their signal's route candidates.
    FrozenNetWires = Snapshot._SourceCapture.FrozenNetWireEntries
    FrozenPositionCount = 0
    for SignalIndex, (Signal, Positions) in enumerate(FrozenNetWires):
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
    _ReattestPlacedTemplateSources(Placed, Snapshot._SourceCapture)
    return RoutingResources(
        StaticGeometry=StaticGeometry,
        ResourceGraph=RoutingResourceGraph(
            ActualBlocks=StaticGeometry.ActualBlocks,
            ElectricalBlocks=StaticGeometry.ElectricalBlocks,
            SolidBlocks=StaticGeometry.SolidBlocks,
            StaticKeepOutBlocks=frozenset(
                TemplateElectricalKeepOutBlocks
            ),
            Technology=Technology,
            BlockStates=dict(Snapshot.BlockStates),
        ),
    )


def ForkRoutingResourcesWithSharedStaticGeometry(
    Source: RoutingResources,
) -> RoutingResources:
    """Create an isolated routing context over immutable placed geometry.

    Sibling pre-route envelopes for one placed geometry may share static
    occupancy and the resource graph's pure region/claim memoization.  They
    must *not* share portal caches, prepared assignment state, native routing
    contexts, or any proof result: those carry layer and envelope identity.
    ``RoutingResources`` defaults create fresh values for all of that mutable
    state, leaving only the immutable geometry/legality substrate shared.
    """
    if Source.ResourceGraph is None:
        raise ValueError(
            "routing-resource fork requires a static resource graph"
        )
    return RoutingResources(
        StaticGeometry=Source.StaticGeometry,
        ResourceGraph=Source.ResourceGraph,
    )


def AreConnected(First: Position3, Second: Position3) -> bool:
    """Return whether redstone dust at two coordinates can connect."""
    return DefaultRedstoneRoutingTechnology.AreConnected(First, Second)


def BuildElectricalExclusions(Positions: set[Position3]) -> set[Position3]:
    """Expand positions into their direct redstone connection neighborhood."""
    return DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(Positions)


def NeighborPositions(Position: Position3) -> list[Position3]:
    return list(DefaultRedstoneRoutingTechnology.NeighborPositions(Position))

"""Owned immutable inputs for one typed route materialization epoch."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from math import isfinite
from types import MappingProxyType

from PhysicalDesign.Resources.ResourceGraph import (
    RoutingResourceGraph,
    RoutingResourceKind,
)


def _IsExactImmutableMaterializationForest(Value: object, Cache: dict) -> bool:
    """Certify only concrete immutable builtin trees, retaining strong keys.

    A frozen dataclass, enum, mapping proxy or custom producer is not such a
    tree. Those inputs still receive owned copies; mutable descendants cannot
    become shareable merely because their outer tuple/frozenset is immutable.
    """
    Kind = type(Value)
    if Value is None or Kind in (bool, int, str):
        return True
    if Kind is float:
        return isfinite(Value)
    if Kind not in (tuple, frozenset):
        return False
    Cached = Cache.get(id(Value))
    if Cached is Value:
        return True
    if Kind is tuple and all(type(Item) is int for Item in Value):
        return True
    if not all(_IsExactImmutableMaterializationForest(Item, Cache) for Item in Value):
        return False
    if len(Value) > 3:
        Cache[id(Value)] = Value
    return True


def _FreezeMaterializationValue(Value: object, Name: str, Active: set[int], ImmutableCache: dict) -> object:
    """Copy concrete value trees without calling opaque copy/projection hooks."""
    Kind = type(Value)
    if Value is None or Kind in (bool, int, str):
        return Value
    if Kind is float:
        if not isfinite(Value):
            raise TypeError(f"{Name} must contain finite floats")
        return Value
    if Kind is RoutingResourceKind:
        # These named primitive resource tags are the only enum atoms consumed
        # by profiles and claims. Do not admit arbitrary enum payloads.
        return Value
    if Kind in (tuple, frozenset) and _IsExactImmutableMaterializationForest(Value, ImmutableCache):
        return Value
    if id(Value) in Active:
        raise TypeError(f"{Name} cannot contain a cyclic materialization value")
    Active.add(id(Value))
    try:
        if Kind in (tuple, list):
            return tuple(
                _FreezeMaterializationValue(Item, f"{Name}[{Index}]", Active, ImmutableCache)
                for Index, Item in enumerate(Value)
            )
        if Kind in (set, frozenset):
            return frozenset(
                _FreezeMaterializationValue(Item, f"{Name} member", Active, ImmutableCache)
                for Item in Value
            )
        if Kind in (dict, MappingProxyType):
            return MappingProxyType({
                _FreezeMaterializationValue(Key, f"{Name} key", Active, ImmutableCache):
                _FreezeMaterializationValue(Item, f"{Name}[{Key!r}]", Active, ImmutableCache)
                for Key, Item in Value.items()
            })
        if is_dataclass(Value) and not isinstance(Value, type):
            if not Value.__dataclass_params__.frozen:
                raise TypeError(f"{Name} requires a frozen dataclass value")
            # Retain the producer's exact type and methods, but only its declared
            # fields. Constructors, cached properties, and custom copy hooks can
            # introduce mutable aliases or change an already validated value.
            Frozen = object.__new__(Kind)
            for Field in fields(Value):
                object.__setattr__(Frozen, Field.name, _FreezeMaterializationValue(
                    getattr(Value, Field.name), f"{Name}.{Field.name}", Active, ImmutableCache,
                ))
            return Frozen
        raise TypeError(
            f"{Name} has unsupported materialization value type {Kind.__name__}"
        )
    finally:
        Active.remove(id(Value))


class _FrozenTypedRouteResourceGraph(RoutingResourceGraph):
    """Fresh physical graph with sealed inputs and private mutable query caches.

    This graph is only for materialization. Selected-access producers that
    require the exact public graph type continue to use the current live graph.
    """

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "_EpochAuthoritySealed", True)

    def __setattr__(self, Name: str, Value: object) -> None:
        if self.__dict__.get("_EpochAuthoritySealed", False):
            raise AttributeError("typed materialization resource authority is immutable")
        super().__setattr__(Name, Value)

    def __delattr__(self, Name: str) -> None:
        raise AttributeError("typed materialization resource authority is immutable")


@dataclass(frozen=True, slots=True)
class FrozenTypedRouteMaterializationResources:
    """The two resource inputs used by physical candidate materialization."""

    ResourceGraph: RoutingResourceGraph
    PreparingPhysicalComponentGlobalChannels: bool


@dataclass(frozen=True, slots=True)
class FrozenTypedRouteMaterializationInputs:
    """Closed physical inputs captured before one native request batch."""

    Profiles: object
    Metadata: tuple[tuple[object, ...], ...]
    Region: object
    Resources: FrozenTypedRouteMaterializationResources
    Technology: object
    Policy: object
    CoarsePlan: object
    AdaptiveBudget: object
    LayerCount: int
    UnreservedPortalMode: bool
    ForeignSelectedPinAccessClaimsBySignal: object
    FrozenComponentClaims: object
    AssemblySpecificSiblingAperturesBySignal: object


def BuildTypedRouteMaterializationEpoch(
    State: object,
    OriginMetadata: tuple[tuple[object, ...], ...],
) -> FrozenTypedRouteMaterializationInputs:
    """Capture only physical consumers' inputs; never clone routing state.

    All ownership is local to this capture. A later capture observes current
    values again, with no mutable-object identity cache or copied graph caches.
    The caller separately binds and validates current selected-access authority.
    """
    if type(OriginMetadata) is not tuple or any(
        type(Metadata) is not tuple or len(Metadata) != 7
        for Metadata in OriginMetadata
    ):
        raise TypeError("OriginMetadata must contain exact seven-field tuples")
    Graph = State.Resources.ResourceGraph
    if type(Graph) is not RoutingResourceGraph:
        raise TypeError("materialization capture requires the exact current resource graph")
    Preparing = State.Resources.PreparingPhysicalComponentGlobalChannels
    if type(Preparing) is not bool:
        raise TypeError("PreparingPhysicalComponentGlobalChannels must be an exact bool")

    if type(State.LayerCount) is not int or State.LayerCount < 1:
        raise TypeError("LayerCount must be a positive exact int")
    if type(State.UnreservedPortalMode) is not bool:
        raise TypeError("UnreservedPortalMode must be an exact bool")

    ImmutableCache = {}

    def Freeze(Value: object, Name: str) -> object:
        return _FreezeMaterializationValue(Value, Name, set(), ImmutableCache)

    FrozenGraph = _FrozenTypedRouteResourceGraph(
        ActualBlocks=Freeze(Graph.ActualBlocks, "ResourceGraph.ActualBlocks"),
        ElectricalBlocks=Freeze(Graph.ElectricalBlocks, "ResourceGraph.ElectricalBlocks"),
        SolidBlocks=Freeze(Graph.SolidBlocks, "ResourceGraph.SolidBlocks"),
        Technology=Freeze(Graph.Technology, "ResourceGraph.Technology"),
        GraphVersion=Freeze(Graph.GraphVersion, "ResourceGraph.GraphVersion"),
        StaticKeepOutBlocks=Freeze(Graph.StaticKeepOutBlocks, "ResourceGraph.StaticKeepOutBlocks"),
        BlockStates=Freeze(Graph.BlockStates, "ResourceGraph.BlockStates"),
    )
    return FrozenTypedRouteMaterializationInputs(
        Profiles=Freeze(State.Profiles, "Profiles"),
        Metadata=Freeze(OriginMetadata, "OriginMetadata"),
        Region=Freeze(State.Region, "Region"),
        Resources=FrozenTypedRouteMaterializationResources(FrozenGraph, Preparing),
        Technology=Freeze(State.Technology, "Technology"),
        Policy=Freeze(State.Policy, "Policy"),
        CoarsePlan=Freeze(State.CoarsePlan, "CoarsePlan"),
        AdaptiveBudget=Freeze(State.AdaptiveBudget, "AdaptiveBudget"),
        LayerCount=State.LayerCount,
        UnreservedPortalMode=State.UnreservedPortalMode,
        ForeignSelectedPinAccessClaimsBySignal=Freeze(
            State.ForeignSelectedPinAccessClaimsBySignal,
            "ForeignSelectedPinAccessClaimsBySignal",
        ),
        FrozenComponentClaims=Freeze(State.FrozenComponentClaims, "FrozenComponentClaims"),
        AssemblySpecificSiblingAperturesBySignal=Freeze(
            State.AssemblySpecificSiblingAperturesBySignal,
            "AssemblySpecificSiblingAperturesBySignal",
        ),
    )

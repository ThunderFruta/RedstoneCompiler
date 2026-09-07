"""Contract coverage for nonempty static routing-geometry sharing."""

from dataclasses import fields
from types import SimpleNamespace

from Assets.Templates import LitematicTemplates
from Compilation.Ir.Models import Gate, GateKind
from Formats.Litematic.Codec import LoadTemplate
from PhysicalDesign.Contracts.Results import RoutingResources
from PhysicalDesign.Geometry.Placement import BuildPlacedGate
from PhysicalDesign.Redstone.Rules import (
    BuildRoutingResources,
    ForkRoutingResourcesWithSharedStaticGeometry,
)
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology


ElectricalBlockNames = frozenset({
    "minecraft:comparator",
    "minecraft:lever",
    "minecraft:redstone_torch",
    "minecraft:redstone_wall_torch",
    "minecraft:redstone_wire",
    "minecraft:repeater",
})

SharedForkFields = frozenset({"StaticGeometry", "ResourceGraph"})
RepresentativeNonContainerFields = (
    "RawPortalGeometryCaches",
    "PreparedPortalDomainCaches",
    "FrozenPreparedPortalDomainCache",
    "FrozenInterfaceGlobalCandidateCache",
    "PreparedClusterInterfaceAssignment",
    "PreparedComponentRoutingProblem",
    "PreparedPhysicalComponentAssembly",
    "FrozenRoutedComponentTemplate",
)


def BuildNonemptyPlacement(
    *,
    Anchor: tuple[int, int, int] = (10, 1, 10),
    FrozenPosition: tuple[int, int, int] = (20, 3, 20),
) -> SimpleNamespace:
    """Build one legal, rotated NAND placement without routing it."""
    return SimpleNamespace(
        PlacedGates=[
            BuildPlacedGate(
                Gate("Nand0", GateKind.NAND, ["Y"], ["A", "B"]),
                *Anchor,
                90,
            ),
        ],
        FrozenNetWires={"Frozen": (FrozenPosition,)},
    )


def ExpectedNandGeometry(
    Anchor: tuple[int, int, int],
) -> tuple[frozenset[tuple[int, int, int]], ...]:
    """Independently rotate the public NAND template 90 degrees clockwise.

    The public template is 3 by 1 by 4.  For a local ``(x, y, z)`` position,
    a clockwise quarter turn is ``(depth - 1 - z, y, x)``; this is deliberately
    written here rather than delegated to routing-geometry construction.
    """
    Template = LoadTemplate(LitematicTemplates["Nand"])
    assert Template.Size == (3, 1, 4)
    Actual = set()
    Electrical = set()
    Solid = set()
    for (X, Y, Z), State in Template.Blocks.items():
        Rotated = (Template.Size[2] - 1 - Z, Y, X)
        Position = (
            Anchor[0] + Rotated[0],
            Anchor[1] + Rotated[1],
            Anchor[2] + Rotated[2],
        )
        Actual.add(Position)
        if State["Name"] in ElectricalBlockNames:
            Electrical.add(Position)
        if State["Name"] not in ElectricalBlockNames | {"minecraft:air"}:
            Solid.add(Position)

    # Keep the public template fixture itself explicit: the transform below is
    # an independent geometry oracle, not a second call into routing setup.
    assert Actual == {
        (10, 1, 11),
        (11, 1, 10),
        (11, 1, 11),
        (11, 1, 12),
        (12, 1, 10),
        (12, 1, 12),
        (13, 1, 10),
        (13, 1, 12),
    }
    assert Electrical == {
        (10, 1, 11),
        (11, 1, 10),
        (11, 1, 11),
        (11, 1, 12),
        (13, 1, 10),
        (13, 1, 12),
    }
    assert Solid == {(12, 1, 10), (12, 1, 12)}
    return frozenset(Actual), frozenset(Electrical), frozenset(Solid)


def MutableContainerFields(Resources: RoutingResources) -> tuple[str, ...]:
    """Return every public routing-resource field with a mutable container."""
    return tuple(
        Field.name
        for Field in fields(RoutingResources)
        if isinstance(getattr(Resources, Field.name), (dict, list, set))
    )


def AddSentinel(Container: object, Sentinel: object) -> None:
    """Make a public mutable cache observably non-default without routing."""
    if isinstance(Container, dict):
        Container[Sentinel] = Sentinel
    elif isinstance(Container, list):
        Container.append(Sentinel)
    elif isinstance(Container, set):
        Container.add(Sentinel)
    else:
        raise AssertionError(f"{Sentinel} is not a mutable-container sentinel")


def ContainsSentinel(Container: object, Sentinel: object) -> bool:
    """Return whether a container still carries one exact stale-state marker."""
    if isinstance(Container, dict):
        return Sentinel in Container or Sentinel in Container.values()
    if isinstance(Container, (list, set)):
        return Sentinel in Container
    raise AssertionError(f"{Sentinel} is not a mutable-container sentinel")


def AssertFreshForkDefaults(
    Source: RoutingResources,
    Fork: RoutingResources,
    FreshDefaults: RoutingResources,
) -> None:
    """Require every fork-local public field to reset to its dataclass default."""
    for Field in fields(RoutingResources):
        SourceValue = getattr(Source, Field.name)
        ForkValue = getattr(Fork, Field.name)
        DefaultValue = getattr(FreshDefaults, Field.name)
        if Field.name in SharedForkFields:
            assert ForkValue is SourceValue, Field.name
            continue
        assert ForkValue == DefaultValue, Field.name


def test_nonempty_static_geometry_is_shared_but_attempt_state_is_isolated() -> None:
    """Forks preserve one nonempty static lineage and isolate attempt state."""
    Anchor = (10, 1, 10)
    FrozenPosition = (20, 3, 20)
    ExpectedActual, ExpectedElectrical, ExpectedSolid = ExpectedNandGeometry(
        Anchor
    )
    Source = BuildRoutingResources(
        BuildNonemptyPlacement(Anchor=Anchor, FrozenPosition=FrozenPosition),
    )
    assert Source.StaticGeometry.ActualBlocks == ExpectedActual
    assert Source.StaticGeometry.ElectricalBlocks == (
        ExpectedElectrical | {FrozenPosition}
    )
    assert Source.StaticGeometry.SolidBlocks == ExpectedSolid
    assert Source.StaticGeometry.TemplateElectricalBlocks == ExpectedElectrical
    assert FrozenPosition not in Source.StaticGeometry.TemplateElectricalBlocks

    # Build a public default reference before source mutation.  The two shared
    # fields are the entire sharing allowlist; every other top-level field must
    # equal a fresh RoutingResources default after a fork.
    FreshDefaults = RoutingResources(
        StaticGeometry=Source.StaticGeometry,
        ResourceGraph=Source.ResourceGraph,
    )
    MutableSentinels = {}
    for FieldName in MutableContainerFields(Source):
        Sentinel = ("source-mutable-sentinel", FieldName)
        AddSentinel(getattr(Source, FieldName), Sentinel)
        MutableSentinels[FieldName] = Sentinel
        assert ContainsSentinel(getattr(Source, FieldName), Sentinel)

    # Tuples and Any/object state are not discoverable as mutable containers,
    # so exercise representative portal, proof/result, candidate, assignment,
    # and prepared-component fields with non-default source values as well.
    NonContainerSentinels = {
        FieldName: ("source-non-container-sentinel", FieldName)
        for FieldName in RepresentativeNonContainerFields
    }
    for FieldName, Sentinel in NonContainerSentinels.items():
        setattr(Source, FieldName, Sentinel)
    First = ForkRoutingResourcesWithSharedStaticGeometry(Source)
    Second = ForkRoutingResourcesWithSharedStaticGeometry(Source)

    AssertFreshForkDefaults(Source, First, FreshDefaults)
    AssertFreshForkDefaults(Source, Second, FreshDefaults)
    assert Source.ResourceGraph.Technology is DefaultRedstoneRoutingTechnology
    assert First.ResourceGraph.Technology is DefaultRedstoneRoutingTechnology

    for FieldName, Sentinel in MutableSentinels.items():
        SourceValue = getattr(Source, FieldName)
        FirstValue = getattr(First, FieldName)
        SecondValue = getattr(Second, FieldName)
        assert FirstValue is not SourceValue, FieldName
        assert SecondValue is not SourceValue, FieldName
        assert FirstValue is not SecondValue, FieldName
        assert not ContainsSentinel(FirstValue, Sentinel), FieldName
        assert not ContainsSentinel(SecondValue, Sentinel), FieldName
        FirstSentinel = ("first-sibling-only", FieldName)
        AddSentinel(FirstValue, FirstSentinel)
        assert ContainsSentinel(SourceValue, Sentinel), FieldName
        assert not ContainsSentinel(SecondValue, FirstSentinel), FieldName

    for FieldName, Sentinel in NonContainerSentinels.items():
        assert getattr(Source, FieldName) == Sentinel
        assert getattr(First, FieldName) == getattr(FreshDefaults, FieldName)
        assert getattr(Second, FieldName) == getattr(FreshDefaults, FieldName)

    # The same current-lineage graph is intentionally shared. Its private pure
    # region/claim memoization is graph-owned, not fork-local attempt state.
    # The frozen wire excludes its coordinate from a region, while a separate
    # sloped primitive has independently predictable support/headroom claims.
    Region = Source.ResourceGraph.BuildRegion((19, 21, 1, 4, 19, 21))
    assert FrozenPosition not in Region.Nodes
    assert Region.Nodes
    assert Region.Edges
    assert First.ResourceGraph.BuildRegion((19, 21, 1, 4, 19, 21)) is Region
    assert Second.ResourceGraph.BuildRegion((19, 21, 1, 4, 19, 21)) is Region

    WireCells = frozenset({(24, 3, 20), (25, 4, 20)})
    ExpectedElectricalClaims = set(WireCells)
    for Position in WireCells:
        ExpectedElectricalClaims.update(
            DefaultRedstoneRoutingTechnology.NeighborPositions(Position)
        )
    Claims = Source.ResourceGraph.BuildRouteClaims(WireCells)
    assert Claims.WireCells == WireCells
    assert Claims.SupportCells == {(24, 2, 20), (25, 3, 20)}
    assert Claims.RequiredAirCells == {(24, 4, 20)}
    assert Claims.ElectricalCells == ExpectedElectricalClaims
    assert First.ResourceGraph.BuildRouteClaims(WireCells) is Claims
    assert Second.ResourceGraph.BuildRouteClaims(WireCells) is Claims


def test_changed_inputs_use_new_eager_static_geometry_lineages() -> None:
    """Changed placement or frozen-wire inputs are never represented as forks."""
    Source = BuildRoutingResources(BuildNonemptyPlacement())
    ChangedPlacement = BuildRoutingResources(
        BuildNonemptyPlacement(Anchor=(30, 1, 10))
    )
    ChangedFrozenWire = BuildRoutingResources(
        BuildNonemptyPlacement(FrozenPosition=(30, 3, 20))
    )

    assert ChangedPlacement.StaticGeometry is not Source.StaticGeometry
    assert ChangedPlacement.ResourceGraph is not Source.ResourceGraph
    assert ChangedPlacement.StaticGeometry.ActualBlocks != (
        Source.StaticGeometry.ActualBlocks
    )
    assert ChangedFrozenWire.StaticGeometry is not Source.StaticGeometry
    assert ChangedFrozenWire.ResourceGraph is not Source.ResourceGraph
    assert (30, 3, 20) in ChangedFrozenWire.StaticGeometry.ElectricalBlocks
    assert (30, 3, 20) not in Source.StaticGeometry.ElectricalBlocks

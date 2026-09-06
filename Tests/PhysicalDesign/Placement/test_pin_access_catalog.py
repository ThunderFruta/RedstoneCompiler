from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from Compilation.Ir.Models import Gate, GateKind
from PhysicalDesign.Cells.Library import CellMacros
from PhysicalDesign.Contracts.PlacementAccess import (
    FrozenPhysicalPlacementContract,
    PlacementAccessCellTransform,
    PlacementAccessEnvelope,
    PlacementAccessPinMapping,
    PlacementAccessSolveStatus,
)
from PhysicalDesign.Geometry.Placement import (
    BuildPlacedGate,
    BuildPlacementPinAccessWitness,
    PlacedDesign,
)
from PhysicalDesign.Geometry.Rotation import TransformLocalPosition
from PhysicalDesign.Placement.Access.Capacity import (
    FixedPlacementPinAccessStatus,
    SolveFixedPlacementPinAccessDomains,
    SolvePlacedPinAccessOptionDomains,
)
from PhysicalDesign.Placement.Access.Catalog import (
    BuildPhysicalPinAccessCatalog,
    BuildPlacedPinAccessModelFingerprint,
    EnumeratePlacedPinAccessOptionDomains,
    FreezeSelectedPlacementPinAccessWitness,
)
from PhysicalDesign.Placement.Access.Fabric import BuildPlacementAccessFabric
from PhysicalDesign.Placement.Engine.Clusters import PcbPlacement
from PhysicalDesign.Redstone.Rules.Geometry import (
    BuildRoutingResources,
    LoadRoutingTemplates,
)
from PhysicalDesign.Redstone.Rules.Repeaters import PropagateRoutePower
from PhysicalDesign.Redstone.Technology import (
    DefaultRedstoneRoutingTechnology,
    RepeaterOutputDelta,
)
from PhysicalDesign.Rendering.SchemWriter import (
    BuildLitematicBlockMap,
    WriteLitematic,
)
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceGraph


Technology = DefaultRedstoneRoutingTechnology
FixturePath = (
    Path(__file__).parents[2]
    / "Fixtures"
    / "PlacementAccess"
    / "Cla4PlacementAccessV17.json"
)


def _Placement(Gates, FrozenNetWires=None):
    return type("Placement", (), {
        "PlacedGates": list(Gates),
        "FrozenNetWires": FrozenNetWires or {},
    })()


def _Resources(Gates):
    return BuildRoutingResources(_Placement(Gates)).ResourceGraph


def _Nand(
    Name="Nand0",
    Output="Y",
    Inputs=("A", "B"),
    Origin=(10, 1, 10),
    Rotation=0,
    MirrorX=False,
):
    return BuildPlacedGate(
        Gate(Name, GateKind.NAND, [Output], list(Inputs)),
        *Origin,
        Rotation,
        MirrorX,
    )


def _SelectedFirstOptions(Domains):
    return {
        Domain.DomainId: Domain.Options[0].SelectionFingerprint
        for Domain in Domains
    }


def _SelectedStraightAccessFixture():
    Gates = (
        BuildPlacedGate(
            Gate("Source", GateKind.INPUT, ["A"], []),
            0,
            1,
            0,
            0,
            False,
        ),
        BuildPlacedGate(
            Gate("Target", GateKind.OUTPUT, [], ["A"]),
            10,
            1,
            10,
            0,
            False,
        ),
    )
    Placed = PlacedDesign(
        Module=None,
        PlacedGates=list(Gates),
        FrozenNetWires={},
    )
    Placement = PcbPlacement(
        Placed=Placed,
        Clusters=(),
        SignalOrder=("A",),
        LayerCount=1,
    )
    Resources = BuildRoutingResources(Placed, Technology=Technology)
    Domains = EnumeratePlacedPinAccessOptionDomains(
        Gates,
        ResourceGraph=Resources.ResourceGraph,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal={},
    )
    Solve = SolvePlacedPinAccessOptionDomains(
        Domains,
        ResourceGraph=Resources.ResourceGraph,
        MaximumExpansions=100,
    )
    assert Solve.Status is PlacementAccessSolveStatus.Feasible
    assert Solve.SelectedWitness is not None
    return Placement, Resources, Domains, Solve


def testExplicitPlacementAccessFabricAcceptsCurrentSelectedWitness() -> None:
    Placement, Resources, _Domains, Solve = (
        _SelectedStraightAccessFixture()
    )
    Witness = Solve.SelectedWitness

    Fabric = BuildPlacementAccessFabric(
        Placement,
        Resources=Resources,
        Technology=Technology,
        PinAccessWitness=Witness,
        FixedPinAccessSolve=Solve,
        RequireSelectedPinAccessWitness=True,
    )

    assert Fabric.Complete is True
    assert Fabric.PinAccessWitnessFingerprint == Witness.WitnessFingerprint


def testExplicitPlacementAccessFabricRejectsCurrentForeignOwnership() -> None:
    Placement, _Resources, _Domains, Solve = (
        _SelectedStraightAccessFixture()
    )
    Witness = Solve.SelectedWitness
    ForeignPosition = (1, 1, 4)
    FrozenNetWires = {"Foreign": (ForeignPosition,)}
    CurrentPlaced = replace(
        Placement.Placed,
        FrozenNetWires=FrozenNetWires,
    )
    CurrentPlacement = replace(Placement, Placed=CurrentPlaced)
    CurrentResources = BuildRoutingResources(
        CurrentPlaced,
        Technology=Technology,
    )
    CurrentDomains = EnumeratePlacedPinAccessOptionDomains(
        CurrentPlaced.PlacedGates,
        ResourceGraph=CurrentResources.ResourceGraph,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal=FrozenNetWires,
    )
    CurrentSource = next(
        Domain for Domain in CurrentDomains if Domain.Role == "Source"
    )
    SelectedSource = next(
        Selection
        for Selection in Witness.Selections
        if Selection.Role == "Source"
    )

    assert SelectedSource.FirstLegNodes == (
        (0, 1, 3),
        (0, 1, 4),
        (0, 1, 5),
    )
    assert ForeignPosition == (1, 1, 4)
    assert CurrentSource.Complete is True
    assert CurrentSource.Options == ()
    assert CurrentSource.RejectedOptionCount == 1
    assert Witness.ResourceModelFingerprint != (
        BuildPlacedPinAccessModelFingerprint(
            CurrentPlaced.PlacedGates,
            ResourceGraph=CurrentResources.ResourceGraph,
            PreOwnedNodesBySignal=FrozenNetWires,
        )
    )
    with pytest.raises(ValueError):
        BuildPlacementAccessFabric(
            CurrentPlacement,
            Resources=CurrentResources,
            Technology=Technology,
            PinAccessWitness=Witness,
            FixedPinAccessSolve=Solve,
            RequireSelectedPinAccessWitness=True,
        )


def testExplicitPlacementAccessFabricRejectsCurrentTechnology() -> None:
    Placement, _Resources, _Domains, Solve = (
        _SelectedStraightAccessFixture()
    )
    Witness = Solve.SelectedWitness
    ChangedTechnology = replace(
        Technology,
        TechnologyVersion="redstone-routing-current-v2",
    )
    CurrentResources = BuildRoutingResources(
        Placement.Placed,
        Technology=ChangedTechnology,
    )

    assert ChangedTechnology.TechnologyVersion != Technology.TechnologyVersion
    with pytest.raises(ValueError):
        BuildPlacementAccessFabric(
            Placement,
            Resources=CurrentResources,
            Technology=ChangedTechnology,
            PinAccessWitness=Witness,
            FixedPinAccessSolve=Solve,
            RequireSelectedPinAccessWitness=True,
        )


def _OracleTransformNandPosition(
    Position,
    *,
    SourceOrigin,
    DestinationOrigin,
    Rotation,
    MirrorX,
):
    """Apply the documented mirror-then-clockwise-rotation contract."""
    X = Position[0] - SourceOrigin[0]
    Y = Position[1] - SourceOrigin[1]
    Z = Position[2] - SourceOrigin[2]
    if MirrorX:
        X = 2 - X
    if Rotation == 90:
        X, Z = 3 - Z, X
    elif Rotation == 180:
        X, Z = 2 - X, 3 - Z
    elif Rotation == 270:
        X, Z = Z, 2 - X
    return (
        DestinationOrigin[0] + X,
        DestinationOrigin[1] + Y,
        DestinationOrigin[2] + Z,
    )


def _OracleTransformDirection(Direction, *, Rotation, MirrorX):
    X, Y, Z = Direction
    if MirrorX:
        X = -X
    if Rotation == 90:
        return -Z, Y, X
    if Rotation == 180:
        return -X, Y, -Z
    if Rotation == 270:
        return Z, Y, -X
    return X, Y, Z


def testDirectionOracleMirrorsEastAndWestWithoutRotation() -> None:
    """Validate oracle math, not production east/west access capability."""
    assert _OracleTransformDirection(
        (1, 0, 0),
        Rotation=0,
        MirrorX=True,
    ) == (-1, 0, 0)
    assert _OracleTransformDirection(
        (-1, 0, 0),
        Rotation=0,
        MirrorX=True,
    ) == (1, 0, 0)


def testCatalogCompilesTheThreeApprovedLocalShapes() -> None:
    Catalog = BuildPhysicalPinAccessCatalog(Technology=Technology)
    Values = {
        Value.TemplateId: Value
        for Value in Catalog
        if Value.CellKind == "NAND" and Value.PinId == "Output0"
    }

    assert tuple(Values) == (
        "NAND:Output0Straight",
        "NAND:Output0PlanarJogNegative",
        "NAND:Output0PlanarJogPositive",
    )
    Straight = Values["NAND:Output0Straight"]
    Negative = Values["NAND:Output0PlanarJogNegative"]
    Positive = Values["NAND:Output0PlanarJogPositive"]
    assert Straight.FirstLegNodes == (
        (1, 0, 4),
        (1, 0, 5),
        (1, 0, 6),
    )
    assert Straight.FirstTrackNode == (1, 0, 7)
    assert Straight.RepeaterPathIndex == 1
    assert Negative.FirstLegNodes == (
        (1, 0, 4),
        (1, 0, 5),
        (0, 0, 5),
    )
    assert Negative.FirstTrackNode == (-1, 0, 5)
    assert Positive.FirstLegNodes == (
        (1, 0, 4),
        (1, 0, 5),
        (2, 0, 5),
    )
    assert Positive.FirstTrackNode == (3, 0, 5)
    assert Negative.RepeaterPathIndex == Positive.RepeaterPathIndex == 0
    assert all(Value.AllowedRoutingLayers == (0,) for Value in Values.values())


def testLegacyWitnessRemainsStraightOnlyAndByteStableByInputOrder() -> None:
    Gates = (
        _Nand(Rotation=90, MirrorX=True),
        BuildPlacedGate(
            Gate("InputA", GateKind.INPUT, ["A"], []),
            0,
            1,
            0,
            90,
        ),
    )
    First = BuildPlacementPinAccessWitness(Gates, AccessLength=3)
    Second = BuildPlacementPinAccessWitness(reversed(Gates), AccessLength=3)

    assert First.ToDictionary() == Second.ToDictionary()
    assert all("Straight" in Value.PatternId for Value in First.Selections)
    assert all(
        Value.Path == tuple(
            (
                Value.Terminal[0] + Value.ApproachDirection[0] * Offset,
                Value.Terminal[1] + Value.ApproachDirection[1] * Offset,
                Value.Terminal[2] + Value.ApproachDirection[2] * Offset,
            )
            for Offset in range(3)
        )
        for Value in First.Selections
    )


@pytest.mark.parametrize("Rotation", (0, 90, 180, 270))
@pytest.mark.parametrize("MirrorX", (False, True))
def testPlacedCatalogTransformsClaimsAndCanonicalChirality(
    Rotation: int,
    MirrorX: bool,
) -> None:
    GateValue = _Nand(Rotation=Rotation, MirrorX=MirrorX)
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=_Resources((GateValue,)),
        Technology=Technology,
    )

    assert len(Domains) == 3
    assert all(Domain.Complete and len(Domain.Options) == 3 for Domain in Domains)
    for Domain in Domains:
        assert Domain.Options[0].PatternFamily == "straight"
        for Option in Domain.Options:
            assert Option.Claims.WireCells == frozenset(Option.FirstLegNodes)
            assert len(Option.RepeaterReservations) == 1
            assert (
                Option.RepeaterReservations[0].Position
                in Option.Claims.WireCells
            )
            assert Option.VerticalTransitionCount == 0
            assert RepeaterOutputDelta(
                Option.RepeaterReservations[0].InputFacing
            ) == (
                Option.Face
                if Option.Role == "Source"
                else tuple(-Value for Value in Option.Face)
            )
    Output = next(Value for Value in Domains if Value.Role == "Source")
    Positive = next(
        Value
        for Value in Output.Options
        if Value.TemplateId.endswith("PlanarJogPositive")
    )
    LocalPositive = next(
        Value
        for Value in BuildPhysicalPinAccessCatalog(Technology=Technology)
        if Value.TemplateId == "NAND:Output0PlanarJogPositive"
    )
    Expected = tuple(
        (
            GateValue.X + Local[0],
            GateValue.Y + Local[1],
            GateValue.Z + Local[2],
        )
        for Local in (
            TransformLocalPosition(
                Value,
                (3, 4),
                Rotation,
                MirrorX,
            )
            for Value in LocalPositive.FirstLegNodes
        )
    )
    assert Positive.FirstLegNodes == Expected
    assert Positive.TemplateId == "NAND:Output0PlanarJogPositive"


@pytest.mark.parametrize("Rotation", (0, 90, 180, 270))
@pytest.mark.parametrize("MirrorX", (False, True))
def testPlacedCatalogTransformsNonemptyClaimsAndPreservesEmptyRequiredAir(
    Rotation: int,
    MirrorX: bool,
) -> None:
    SourceOrigin = (10, 1, 10)
    DestinationOrigin = (30, 5, -20)
    SourceGate = _Nand(Origin=SourceOrigin)
    DestinationGate = _Nand(
        Origin=DestinationOrigin,
        Rotation=Rotation,
        MirrorX=MirrorX,
    )
    SourceDomains = EnumeratePlacedPinAccessOptionDomains(
        (SourceGate,),
        ResourceGraph=_Resources((SourceGate,)),
        Technology=Technology,
    )
    DestinationDomains = EnumeratePlacedPinAccessOptionDomains(
        (DestinationGate,),
        ResourceGraph=_Resources((DestinationGate,)),
        Technology=Technology,
    )
    SourceOptions = {
        (Domain.Role, Domain.PinId, Option.TemplateId): Option
        for Domain in SourceDomains
        for Option in Domain.Options
    }
    DestinationOptions = {
        (Domain.Role, Domain.PinId, Option.TemplateId): Option
        for Domain in DestinationDomains
        for Option in Domain.Options
    }
    FacingVectors = {
        "north": (0, 0, -1),
        "south": (0, 0, 1),
        "east": (1, 0, 0),
        "west": (-1, 0, 0),
    }

    assert SourceOptions
    assert SourceOptions.keys() == DestinationOptions.keys()
    for Identity, Source in SourceOptions.items():
        Destination = DestinationOptions[Identity]

        def Transform(Position):
            return _OracleTransformNandPosition(
                Position,
                SourceOrigin=SourceOrigin,
                DestinationOrigin=DestinationOrigin,
                Rotation=Rotation,
                MirrorX=MirrorX,
            )

        assert Destination.Terminal == Transform(Source.Terminal)
        assert Destination.FirstLegNodes == tuple(
            Transform(Value) for Value in Source.FirstLegNodes
        )
        assert Destination.FirstTrackNode == Transform(Source.FirstTrackNode)
        assert Destination.BlockRoles == tuple(
            (Transform(Position), Role)
            for Position, Role in Source.BlockRoles
        )
        for ClaimName in (
            "WireCells",
            "SupportCells",
            "ElectricalCells",
        ):
            assert getattr(Source.Claims, ClaimName)
            assert getattr(Destination.Claims, ClaimName) == frozenset(
                Transform(Value)
                for Value in getattr(Source.Claims, ClaimName)
            )
        assert not Source.Claims.RequiredAirCells
        assert not Destination.Claims.RequiredAirCells
        assert Destination.Face == _OracleTransformDirection(
            Source.Face,
            Rotation=Rotation,
            MirrorX=MirrorX,
        )
        SourceRepeater = Source.RepeaterReservations[0]
        DestinationRepeater = Destination.RepeaterReservations[0]
        assert DestinationRepeater.Position == Transform(
            SourceRepeater.Position
        )
        assert FacingVectors[DestinationRepeater.InputFacing] == (
            _OracleTransformDirection(
                FacingVectors[SourceRepeater.InputFacing],
                Rotation=Rotation,
                MirrorX=MirrorX,
            )
        )


@pytest.mark.parametrize("Rotation", (0, 90, 180, 270))
@pytest.mark.parametrize("MirrorX", (False, True))
def testEveryNandPinPatternPowersAndRoundTripsItsRepeater(
    Rotation: int,
    MirrorX: bool,
    tmp_path: Path,
) -> None:
    GateValue = _Nand(Rotation=Rotation, MirrorX=MirrorX)
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=_Resources((GateValue,)),
        Technology=Technology,
    )

    for Domain in Domains:
        for Option in Domain.Options:
            Bridge = tuple(
                Option.Terminal[Index] - Option.Face[Index]
                for Index in range(3)
            )
            OrderedNodes = (
                (Bridge, *Option.FirstLegNodes, Option.FirstTrackNode)
                if Option.Role == "Source"
                else (Option.FirstTrackNode, *reversed(Option.FirstLegNodes), Bridge)
            )
            Graph = {Value: [] for Value in OrderedNodes}
            for First, Second in zip(OrderedNodes, OrderedNodes[1:]):
                Graph[First].append(Second)
                Graph[Second].append(First)
            Powers = PropagateRoutePower(
                OrderedNodes[0],
                Graph,
                {
                    Value.Position: Value.InputFacing
                    for Value in Option.RepeaterReservations
                },
            )
            assert Powers[OrderedNodes[-1]] > 0

            Routed = SimpleNamespace(
                Module=SimpleNamespace(Gates=()),
                PlacedGates=(GateValue,),
                Wires=Option.FirstLegNodes,
                Supports=tuple(Option.Claims.SupportCells),
                RepeaterInputFacings={
                    Value.Position: Value.InputFacing
                    for Value in Option.RepeaterReservations
                },
                NetWires={Option.Signal: Option.FirstLegNodes},
            )
            Build = BuildLitematicBlockMap(Routed)
            OutputPath = tmp_path / (
                f"{Domain.PinId}-{Option.TemplateId.split(':')[-1]}.litematic"
            )
            WriteLitematic(Routed, OutputPath, Build=Build)
            assert Build.RepeaterOrientation["ReadbackPassed"] is True
            assert Build.RepeaterOrientation["ReadbackMismatchCount"] == 0


def testAnonymousGeometryIdentityIgnoresSignalAndGateRenaming() -> None:
    FirstGate = _Nand(Name="FirstName", Output="FirstOutput")
    SecondGate = _Nand(Name="SecondName", Output="SecondOutput")
    FirstDomains = EnumeratePlacedPinAccessOptionDomains(
        (FirstGate,),
        ResourceGraph=_Resources((FirstGate,)),
        Technology=Technology,
    )
    SecondDomains = EnumeratePlacedPinAccessOptionDomains(
        (SecondGate,),
        ResourceGraph=_Resources((SecondGate,)),
        Technology=Technology,
    )
    FirstOptions = sorted(
        Value.AnonymousGeometryFingerprint
        for Domain in FirstDomains
        for Value in Domain.Options
    )
    SecondOptions = sorted(
        Value.AnonymousGeometryFingerprint
        for Domain in SecondDomains
        for Value in Domain.Options
    )

    assert FirstOptions == SecondOptions
    assert {
        Value.PlacedBindingFingerprint
        for Domain in FirstDomains
        for Value in Domain.Options
    } != {
        Value.PlacedBindingFingerprint
        for Domain in SecondDomains
        for Value in Domain.Options
    }


def testAnonymousGeometryIdentityIsTranslationInvariant() -> None:
    FirstGate = _Nand(Origin=(10, 1, 10))
    SecondGate = _Nand(Origin=(30, 5, -20))
    FirstDomains = EnumeratePlacedPinAccessOptionDomains(
        (FirstGate,),
        ResourceGraph=_Resources((FirstGate,)),
        Technology=Technology,
    )
    SecondDomains = EnumeratePlacedPinAccessOptionDomains(
        (SecondGate,),
        ResourceGraph=_Resources((SecondGate,)),
        Technology=Technology,
    )

    def AnonymousByLogicalPattern(Domains):
        return {
            (
                Domain.Role,
                Domain.PinId,
                Option.TemplateId,
            ): Option.AnonymousGeometryFingerprint
            for Domain in Domains
            for Option in Domain.Options
        }

    assert AnonymousByLogicalPattern(FirstDomains) == (
        AnonymousByLogicalPattern(SecondDomains)
    )
    assert {
        Value.PlacedBindingFingerprint
        for Domain in FirstDomains
        for Value in Domain.Options
    } != {
        Value.PlacedBindingFingerprint
        for Domain in SecondDomains
        for Value in Domain.Options
    }


def testAccessIdentityChangesWithTechnologyAndCatalogVersions() -> None:
    GateValue = _Nand()
    ChangedTechnology = replace(
        Technology,
        TechnologyVersion="redstone-routing-conformance-v2",
    )

    def OptionsByIdentity(TechnologyValue, CatalogVersion):
        ResourceGraph = BuildRoutingResources(
            _Placement((GateValue,)),
            Technology=TechnologyValue,
        ).ResourceGraph
        Domains = EnumeratePlacedPinAccessOptionDomains(
            (GateValue,),
            ResourceGraph=ResourceGraph,
            Technology=TechnologyValue,
            CatalogVersion=CatalogVersion,
        )
        return {
            (Domain.Role, Domain.PinId, Option.TemplateId): Option
            for Domain in Domains
            for Option in Domain.Options
        }

    Original = OptionsByIdentity(Technology, "physical-access-catalog-a")
    NewTechnology = OptionsByIdentity(
        ChangedTechnology,
        "physical-access-catalog-a",
    )
    NewCatalog = OptionsByIdentity(Technology, "physical-access-catalog-b")

    assert Original.keys() == NewTechnology.keys() == NewCatalog.keys()
    for Identity, OriginalOption in Original.items():
        TechnologyOption = NewTechnology[Identity]
        CatalogOption = NewCatalog[Identity]
        assert TechnologyOption.FirstLegNodes == OriginalOption.FirstLegNodes
        assert CatalogOption.FirstLegNodes == OriginalOption.FirstLegNodes
        assert (
            TechnologyOption.TechnologyFingerprint
            != OriginalOption.TechnologyFingerprint
        )
        assert (
            TechnologyOption.TemplateProofFingerprint
            != OriginalOption.TemplateProofFingerprint
        )
        assert (
            TechnologyOption.AnonymousGeometryFingerprint
            != OriginalOption.AnonymousGeometryFingerprint
        )
        assert TechnologyOption.ResourceModelFingerprint != (
            OriginalOption.ResourceModelFingerprint
        )
        assert TechnologyOption.PlacedBindingFingerprint != (
            OriginalOption.PlacedBindingFingerprint
        )
        assert CatalogOption.TechnologyFingerprint == (
            OriginalOption.TechnologyFingerprint
        )
        assert CatalogOption.ResourceModelFingerprint == (
            OriginalOption.ResourceModelFingerprint
        )
        assert CatalogOption.TemplateProofFingerprint == (
            OriginalOption.TemplateProofFingerprint
        )
        assert CatalogOption.TemplateFingerprint != (
            OriginalOption.TemplateFingerprint
        )
        assert CatalogOption.AnonymousGeometryFingerprint != (
            OriginalOption.AnonymousGeometryFingerprint
        )
        assert CatalogOption.PlacedBindingFingerprint != (
            OriginalOption.PlacedBindingFingerprint
        )


def testDomainGenerationCapIsIncompleteRatherThanEmptyUnsat() -> None:
    GateValue = _Nand()
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=_Resources((GateValue,)),
        Technology=Technology,
        MaximumGenerationWork=1,
    )

    assert len(Domains) == 3
    assert all(not Value.Complete for Value in Domains)
    assert {
        Value.IncompleteReason for Value in Domains
    } == {"catalog-domain-generation-work-cap"}


def testStaticObstructionRejectsOnlyAffectedCatalogOption() -> None:
    GateValue = _Nand()
    Base = _Resources((GateValue,))
    Obstruction = (13, 1, 15)
    Blocked = RoutingResourceGraph(
        ActualBlocks=Base.ActualBlocks | {Obstruction},
        ElectricalBlocks=Base.ElectricalBlocks,
        SolidBlocks=Base.SolidBlocks | {Obstruction},
        Technology=Base.Technology,
        GraphVersion=Base.GraphVersion,
        StaticKeepOutBlocks=Base.StaticKeepOutBlocks,
    )
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=Blocked,
        Technology=Technology,
    )
    Output = next(Value for Value in Domains if Value.Role == "Source")

    assert len(Output.Options) == 2
    assert Output.RejectedOptionCount == 1
    assert all(
        Obstruction not in Value.FirstLegNodes
        for Value in Output.Options
    )


def testStaticSignalRolesCoverEveryTemplateBlock() -> None:
    Templates = LoadRoutingTemplates()

    assert {
        Name: {Position for Position, _Role in Macro.StaticSignalRoles}
        for Name, Macro in CellMacros.items()
    } == {
        Name: set(Template.Blocks)
        for Name, Template in Templates.items()
    }


def testStraightS1AllowsOnlySignalOwnedPreExistingContacts() -> None:
    GateValue = _Nand()
    Base = _Resources((GateValue,))
    Unblocked = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=Base,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    Output = next(Value for Value in Unblocked if Value.Role == "Source")
    AccessNode = Output.Options[0].FirstLegNodes[1]
    Obstruction = (AccessNode[0] + 1, AccessNode[1], AccessNode[2])
    Blocked = RoutingResourceGraph(
        ActualBlocks=Base.ActualBlocks,
        ElectricalBlocks=Base.ElectricalBlocks | {Obstruction},
        SolidBlocks=Base.SolidBlocks,
        Technology=Base.Technology,
        GraphVersion=Base.GraphVersion,
        StaticKeepOutBlocks=Base.StaticKeepOutBlocks,
    )

    StrictDomains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=Blocked,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    SameSignalDomains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=Blocked,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal={"Y": (Obstruction,)},
    )
    ForeignSignalDomains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=Blocked,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal={"Other": (Obstruction,)},
    )

    assert not next(
        Value for Value in StrictDomains if Value.Role == "Source"
    ).Options
    assert len(next(
        Value for Value in SameSignalDomains if Value.Role == "Source"
    ).Options) == 1
    assert not next(
        Value for Value in ForeignSignalDomains if Value.Role == "Source"
    ).Options


def testStraightS1RejectsSameSignalPreOwnedSupportWireConflict() -> None:
    GateValue = _Nand()
    Base = _Resources((GateValue,))
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=Base,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    Output = next(Value for Value in Domains if Value.Role == "Source")
    AccessNode = Output.Options[0].FirstLegNodes[1]
    ElevatedPreOwnedNode = (
        AccessNode[0],
        AccessNode[1] + 1,
        AccessNode[2],
    )
    WithPreOwnedRoute = RoutingResourceGraph(
        ActualBlocks=Base.ActualBlocks,
        ElectricalBlocks=(
            Base.ElectricalBlocks | {ElevatedPreOwnedNode}
        ),
        SolidBlocks=Base.SolidBlocks,
        Technology=Base.Technology,
        GraphVersion=Base.GraphVersion,
        StaticKeepOutBlocks=Base.StaticKeepOutBlocks,
    )
    ConflictingDomains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=WithPreOwnedRoute,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal={"Y": (ElevatedPreOwnedNode,)},
    )

    assert not next(
        Value for Value in ConflictingDomains if Value.Role == "Source"
    ).Options


def testStraightS1UsesCellRoleOwnershipForInputSourceContact() -> None:
    InputB = BuildPlacedGate(
        Gate("InputB", GateKind.INPUT, ["B"], []),
        11,
        1,
        -1,
        270,
    )
    Nand = _Nand(
        Name="Nand0",
        Output="Y",
        Inputs=("A", "B"),
        Origin=(8, 1, 0),
    )
    Gates = (InputB, Nand)
    Domains = EnumeratePlacedPinAccessOptionDomains(
        Gates,
        ResourceGraph=_Resources(Gates),
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    BInput = next(
        Value
        for Value in Domains
        if Value.GateName == "Nand0" and Value.PinId == "Input1"
    )

    ForeignInputB = BuildPlacedGate(
        Gate("InputB", GateKind.INPUT, ["Other"], []),
        11,
        1,
        -1,
        270,
    )
    ForeignGates = (ForeignInputB, Nand)
    ForeignDomains = EnumeratePlacedPinAccessOptionDomains(
        ForeignGates,
        ResourceGraph=_Resources(ForeignGates),
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    ForeignBInput = next(
        Value
        for Value in ForeignDomains
        if Value.GateName == "Nand0" and Value.PinId == "Input1"
    )

    assert len(BInput.Options) == 1
    assert not ForeignBInput.Options


def testStraightS1UsesNandOutputOwnershipForTorchContact() -> None:
    Producer = _Nand(
        Name="Producer",
        Output="Shared",
        Origin=(4, 1, 15),
        MirrorX=True,
    )
    Consumer = _Nand(
        Name="Consumer",
        Output="Y",
        Inputs=("Other", "Shared"),
        Origin=(7, 1, 20),
        MirrorX=True,
    )
    Gates = (Producer, Consumer)
    Domains = EnumeratePlacedPinAccessOptionDomains(
        Gates,
        ResourceGraph=_Resources(Gates),
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    SharedInput = next(
        Value
        for Value in Domains
        if Value.GateName == "Consumer" and Value.PinId == "Input1"
    )

    ForeignProducer = _Nand(
        Name="Producer",
        Output="Foreign",
        Origin=(4, 1, 15),
        MirrorX=True,
    )
    ForeignGates = (ForeignProducer, Consumer)
    ForeignDomains = EnumeratePlacedPinAccessOptionDomains(
        ForeignGates,
        ResourceGraph=_Resources(ForeignGates),
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    ForeignInput = next(
        Value
        for Value in ForeignDomains
        if Value.GateName == "Consumer" and Value.PinId == "Input1"
    )

    assert len(SharedInput.Options) == 1
    assert not ForeignInput.Options


def testStraightS1DoesNotClaimThePreferredFirstTrackNode() -> None:
    GateValue = _Nand()
    Base = _Resources((GateValue,))
    BaseDomains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=Base,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    Output = next(Value for Value in BaseDomains if Value.Role == "Source")
    FirstTrackNeighbor = (
        Output.Options[0].FirstTrackNode[0] + 1,
        Output.Options[0].FirstTrackNode[1],
        Output.Options[0].FirstTrackNode[2],
    )
    Blocked = RoutingResourceGraph(
        ActualBlocks=Base.ActualBlocks,
        ElectricalBlocks=Base.ElectricalBlocks | {FirstTrackNeighbor},
        SolidBlocks=Base.SolidBlocks,
        Technology=Base.Technology,
        GraphVersion=Base.GraphVersion,
        StaticKeepOutBlocks=Base.StaticKeepOutBlocks,
    )
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=Blocked,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal={"Foreign": (FirstTrackNeighbor,)},
    )

    assert len(next(Value for Value in Domains if Value.Role == "Source").Options) == 1


def testFullAdderProductionTransformHasCompleteStrictStraightDomains() -> None:
    LogicalGates = (
        Gate("NandGate0", GateKind.NAND, ["NandNet0"], ["A", "B"]),
        Gate(
            "NandGate1",
            GateKind.NAND,
            ["NandNet1"],
            ["A", "NandNet0"],
        ),
        Gate(
            "NandGate2",
            GateKind.NAND,
            ["NandNet2"],
            ["B", "NandNet0"],
        ),
        Gate(
            "NandGate3",
            GateKind.NAND,
            ["Propagate"],
            ["NandNet1", "NandNet2"],
        ),
        Gate(
            "NandGate4",
            GateKind.NAND,
            ["NandNet3"],
            ["Propagate", "CarryIn"],
        ),
        Gate(
            "NandGate5",
            GateKind.NAND,
            ["NandNet4"],
            ["Propagate", "NandNet3"],
        ),
        Gate(
            "NandGate6",
            GateKind.NAND,
            ["NandNet5"],
            ["CarryIn", "NandNet3"],
        ),
        Gate(
            "NandGate7",
            GateKind.NAND,
            ["Sum"],
            ["NandNet4", "NandNet5"],
        ),
        Gate(
            "NandGate8",
            GateKind.NAND,
            ["CarryOut"],
            ["NandNet0", "NandNet3"],
        ),
        Gate("InputA", GateKind.INPUT, ["A"], []),
        Gate("InputB", GateKind.INPUT, ["B"], []),
        Gate("InputCarryIn", GateKind.INPUT, ["CarryIn"], []),
        Gate("OutputSum", GateKind.OUTPUT, [], ["Sum"]),
        Gate("OutputCarryOut", GateKind.OUTPUT, [], ["CarryOut"]),
    )
    Transforms = (
        (8, 1, 0, 0, False),
        (4, 1, 5, 0, False),
        (12, 1, 5, 0, True),
        (8, 1, 10, 0, False),
        (4, 1, 15, 0, True),
        (7, 1, 20, 0, True),
        (0, 1, 20, 0, False),
        (4, 1, 25, 0, True),
        (12, 1, 20, 0, False),
        (1, 1, -1, 270, False),
        (11, 1, -1, 270, False),
        (-1, 1, 13, 0, False),
        (3, 1, 29, 90, False),
        (11, 1, 29, 90, False),
    )
    Gates = tuple(
        BuildPlacedGate(GateValue, *Transform)
        for GateValue, Transform in zip(LogicalGates, Transforms)
    )
    FrozenNetWires = {
        "NandNet4": ((6, 1, 24), (7, 1, 24), (8, 1, 24)),
    }
    Placement = _Placement(Gates, FrozenNetWires)
    ResourceGraph = BuildRoutingResources(Placement).ResourceGraph

    Domains = EnumeratePlacedPinAccessOptionDomains(
        Gates,
        ResourceGraph=ResourceGraph,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal=FrozenNetWires,
    )
    Solve = SolvePlacedPinAccessOptionDomains(
        Domains,
        ResourceGraph=ResourceGraph,
        MaximumExpansions=100_000,
    )

    assert len(Domains) == 32
    assert all(
        Domain.Complete and len(Domain.Options) == 1
        for Domain in Domains
    )
    assert Solve.Status is PlacementAccessSolveStatus.Feasible
    assert Solve.SearchComplete is True
    assert Solve.SelectedWitness is not None
    assert len(Solve.SelectedWitness.Selections) == len(Domains)
    assert {
        Selection.PatternFamily
        for Selection in Solve.SelectedWitness.Selections
    } == {"straight"}


def testFreezeBuildsDeeplyImmutableSelectedWitnessAndContract() -> None:
    GateValue = _Nand()
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=_Resources((GateValue,)),
        Technology=Technology,
    )
    Witness = FreezeSelectedPlacementPinAccessWitness(
        Domains,
        _SelectedFirstOptions(Domains),
    )
    Contract = FrozenPhysicalPlacementContract(
        ModuleFingerprint="module",
        PolicyVersion="physical-design-v17-routing-aware-placement-access",
        CatalogVersion=Witness.CatalogVersion,
        TechnologyFingerprint=Witness.TechnologyFingerprint,
        ResourceModelFingerprint=Witness.ResourceModelFingerprint,
        ProblemFingerprint="problem",
        ProofFingerprint="proof",
        CellTransforms=(PlacementAccessCellTransform(
            GateName=GateValue.Name,
            GateKind=GateValue.Kind,
            Origin=(GateValue.X, GateValue.Y, GateValue.Z),
            Rotation=GateValue.Rotation,
            MirrorX=GateValue.MirrorX,
        ),),
        PinMappings=tuple(sorted((
            PlacementAccessPinMapping(
                GateName=Value.GateName,
                Signal=Value.Signal,
                Role=Value.Role,
                LogicalPinId=Value.PinId,
                PhysicalPinId=Value.PinId,
            )
            for Value in Witness.Selections
        ), key=lambda Value: Value.StructuralIdentity())),
        SelectedPinAccessWitness=Witness,
        BoundaryLeases=(),
        ChannelReservations=(),
        Envelope=PlacementAccessEnvelope((10, 1, 9), (13, 1, 17)),
        DomainComplete=True,
        SearchComplete=False,
        OptimalityProven=False,
    )

    assert Contract.ContractFingerprint
    assert Contract.ToDictionary()["SelectedPinAccessWitness"][
        "WitnessFingerprint"
    ] == Witness.WitnessFingerprint
    with pytest.raises(FrozenInstanceError):
        Witness.Complete = False
    with pytest.raises(ValueError, match="identities disagree"):
        replace(Contract, TechnologyFingerprint="stale")


def testSelectedWitnessRejectsMissingAndTruncatedExplicitAccess() -> None:
    GateValue = _Nand()
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=_Resources((GateValue,)),
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    Selected = _SelectedFirstOptions(Domains)
    MissingDomain = dict(Selected)
    MissingDomain.pop(next(iter(sorted(MissingDomain))))

    with pytest.raises(ValueError):
        FreezeSelectedPlacementPinAccessWitness(Domains, MissingDomain)

    Witness = FreezeSelectedPlacementPinAccessWitness(Domains, Selected)
    TruncatedDocument = json.loads(json.dumps(Witness.ToDictionary()))
    TruncatedSelection = TruncatedDocument["Selections"][0]
    TruncatedSelection["FirstLegNodes"] = (
        TruncatedSelection["FirstLegNodes"][:-1]
    )

    with pytest.raises(ValueError):
        type(Witness).FromDictionary(TruncatedDocument)

    MissingProofDocument = json.loads(
        json.dumps(Witness.Selections[0].ToDictionary())
    )
    MissingProofDocument["Template"] = None
    with pytest.raises(ValueError):
        type(Witness.Selections[0]).FromDictionary(MissingProofDocument)


def testCla4OpposingStraightRaysBecomeFeasibleWithAPlanarJog() -> None:
    Fixture = json.loads(FixturePath.read_text(encoding="utf-8"))
    Gates = tuple(
        _Nand(
            Name=Value["Name"],
            Output=Value["OutputSignal"],
            Inputs=tuple(Value["InputSignals"]),
            Origin=tuple(Value["Origin"]),
            Rotation=Value["Rotation"],
            MirrorX=Value["MirrorX"],
        )
        for Value in Fixture["Gates"]
    )
    for GateValue, Expected in zip(Gates, Fixture["Gates"], strict=True):
        assert GateValue.OutputPin == tuple(Expected["ExpectedOutputTerminal"])
        assert GateValue.OutputDirection == tuple(
            Expected["ExpectedOutputDirection"]
        )
    ResourceGraph = _Resources(Gates)
    RichDomains = tuple(
        Value
        for Value in EnumeratePlacedPinAccessOptionDomains(
            Gates,
            ResourceGraph=ResourceGraph,
            Technology=Technology,
        )
        if Value.Role == "Source"
    )

    StraightDomains = tuple(
        replace(
            Value,
            Options=tuple(
                Option
                for Option in Value.Options
                if Option.PatternFamily == "straight"
            ),
            GeneratedOptionCount=1,
        )
        for Value in RichDomains
    )

    Straight = SolveFixedPlacementPinAccessDomains(
        StraightDomains,
        ResourceGraph=ResourceGraph,
    )
    Rich = SolveFixedPlacementPinAccessDomains(
        RichDomains,
        ResourceGraph=ResourceGraph,
    )

    assert Straight.Status.value == Fixture["StraightOnly"]["ExpectedStatus"]
    assert Straight.Status is FixedPlacementPinAccessStatus.Unsatisfiable
    assert Straight.UnsatisfiableCore is not None
    assert {
        Resource
        for Conflict in Straight.UnsatisfiableCore.Conflicts
        for Resource in Conflict.ResourceIds
    } == set(Fixture["StraightOnly"]["ExpectedConflictingResources"])
    assert Rich.Status.value == Fixture["ThreeOptionCatalog"]["ExpectedStatus"]
    assert Rich.Status is FixedPlacementPinAccessStatus.Feasible
    Selected = dict(Rich.SelectedOptionFingerprints)
    assert any(
        Option.PatternFamily == "planar-jog"
        for Domain in RichDomains
        for Option in Domain.Options
        if Selected.get(Domain.DomainId) == Option.SelectionFingerprint
    )


def testPlacementAccessSolveFreezesTheExactStraightWitness() -> None:
    GateValue = _Nand()
    ResourceGraph = _Resources((GateValue,))
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=ResourceGraph,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )

    Result = SolvePlacedPinAccessOptionDomains(
        Domains,
        ResourceGraph=ResourceGraph,
        MaximumExpansions=100,
    )

    assert Result.Status is PlacementAccessSolveStatus.Feasible
    assert Result.SearchComplete is True
    assert Result.OptimalityProven is False
    assert Result.SelectedWitness is not None
    assert all(
        Value.PatternFamily == "straight"
        for Value in Result.SelectedWitness.Selections
    )
    assert Result.AssignmentFingerprint == (
        Result.SelectedWitness.WitnessFingerprint
    )


def testPlacementAccessSolvePreservesIncompleteDomainClassification() -> None:
    GateValue = _Nand()
    ResourceGraph = _Resources((GateValue,))
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (GateValue,),
        ResourceGraph=ResourceGraph,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        MaximumGenerationWork=1,
    )

    Result = SolvePlacedPinAccessOptionDomains(
        Domains,
        ResourceGraph=ResourceGraph,
        MaximumExpansions=100,
    )

    assert Result.Status is PlacementAccessSolveStatus.Incomplete
    assert Result.SearchComplete is False
    assert Result.ConflictCore is None
    assert Result.IncompleteReason == "catalog-domain-generation-work-cap"

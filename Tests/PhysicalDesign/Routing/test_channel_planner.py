import unittest
from types import SimpleNamespace

from Compilation.Ir.Models import Gate, GateKind
from PhysicalDesign.Geometry.Placement import (
    BuildPlacedGate,
    PlacementPinAccessSelection,
    PlacementPinAccessWitness,
)
from PhysicalDesign.Placement.Access.Catalog import (
    EnumeratePlacedPinAccessOptionDomains,
    FreezeSelectedPlacementPinAccessWitness,
)
from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources
from PhysicalDesign.Redstone.Technology import (
    DefaultRedstoneRoutingTechnology,
)
from PhysicalDesign.Routing.Planning.ChannelPlanner import BuildNetRoutingProfiles, MeasureRoutingStage


def BuildGate(
    Name,
    X,
    Z,
    *,
    Inputs=(),
    Outputs=(),
    InputPins=(),
    InputDirections=(),
    OutputPin=None,
    Kind="NAND",
):
    return SimpleNamespace(
        Name=Name,
        Kind=Kind,
        X=X,
        Y=0,
        Z=Z,
        Inputs=list(Inputs),
        Outputs=list(Outputs),
        InputPins=list(InputPins),
        InputDirections=(
            list(InputDirections)
            if InputDirections
            else [(0, 0, -1) for _ in Inputs]
        ),
        OutputPin=OutputPin,
        OutputDirection=((0, 0, 1) if OutputPin is not None else None),
    )


class ChannelPlannerTests(unittest.TestCase):
    def BuildRealCatalogSelectedWitnessFixture(self):
        SourcePath = ((0, 1, 3), (0, 1, 4), (0, 1, 5))
        TargetPath = ((10, 1, 9), (10, 1, 8), (10, 1, 7))
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
        Placed = SimpleNamespace(
            PlacedGates=list(Gates),
            FrozenNetWires={},
        )
        ResourceGraph = BuildRoutingResources(Placed).ResourceGraph
        Domains = EnumeratePlacedPinAccessOptionDomains(
            Gates,
            ResourceGraph=ResourceGraph,
            Technology=DefaultRedstoneRoutingTechnology,
            EnabledPatternFamilies=("straight",),
        )
        Witness = FreezeSelectedPlacementPinAccessWitness(
            Domains,
            {
                Domain.DomainId: Domain.Options[0].SelectionFingerprint
                for Domain in Domains
            },
        )
        return Placed, Gates, Witness, SourcePath, TargetPath

    def BuildExplicitAccessFixture(self):
        Placed = SimpleNamespace(PlacedGates=[
            BuildGate(
                "Source",
                0,
                0,
                Outputs=("A",),
                OutputPin=(0, 0, 0),
            ),
            BuildGate(
                "Target",
                12,
                0,
                Inputs=("A",),
                InputPins=((12, 0, 0),),
            ),
        ])
        SourcePath = ((0, 0, 0), (0, 0, 1), (0, 0, 2))
        TargetPath = ((12, 0, 0), (11, 0, 0), (10, 0, 0))
        Selections = (
            PlacementPinAccessSelection(
                Signal="A",
                GateName="Source",
                GateKind="NAND",
                Role="Source",
                PinId="Output0",
                PatternId="fixture-source-straight",
                Terminal=SourcePath[0],
                ApproachDirection=(0, 0, 1),
                Path=SourcePath,
                CatalogAccessLength=3,
                CatalogMatched=False,
            ),
            PlacementPinAccessSelection(
                Signal="A",
                GateName="Target",
                GateKind="NAND",
                Role="Target",
                PinId="Input0",
                PatternId="fixture-target-straight",
                Terminal=TargetPath[0],
                ApproachDirection=(-1, 0, 0),
                Path=TargetPath,
                CatalogAccessLength=3,
                CatalogMatched=False,
            ),
        )
        Witness = PlacementPinAccessWitness(
            AccessLength=3,
            Selections=tuple(sorted(
                Selections,
                key=lambda Value: Value.StructuralIdentity(),
            )),
            Complete=True,
        )
        return Placed, Witness, SourcePath, TargetPath

    def BuildPlaced(self):
        Gates = [
            BuildGate("SourceA", 0, 0, Outputs=("A",), OutputPin=(0, 0, 0)),
            BuildGate("SinkA1", 12, 0, Inputs=("A",), InputPins=((12, 0, 0),)),
            BuildGate("SinkA2", 12, 6, Inputs=("A",), InputPins=((12, 0, 6),)),
            BuildGate("SourceB", 0, 1, Outputs=("B",), OutputPin=(0, 0, 1)),
            BuildGate("SinkB", 10, 1, Inputs=("B",), InputPins=((10, 0, 1),)),
            BuildGate("SourceC", 2, 20, Outputs=("C",), OutputPin=(2, 0, 20)),
            BuildGate("SinkC", 3, 20, Inputs=("C",), InputPins=((3, 0, 20),)),
        ]
        return SimpleNamespace(PlacedGates=Gates)

    def testCriticalTrunksRouteFirstDeterministically(self) -> None:
        Placed = self.BuildPlaced()
        Profiles = BuildNetRoutingProfiles(Placed, {"B": 2})

        self.assertTrue(Profiles["A"].IsTrunk)
        self.assertGreater(Profiles["B"].Criticality, Profiles["A"].Criticality)

    def testNetProfilesEncodeTerminalFanout(self) -> None:
        Profiles = BuildNetRoutingProfiles(self.BuildPlaced(), None)

        self.assertEqual(Profiles["A"].Fanout, 2)
        self.assertEqual(Profiles["B"].Fanout, 1)
        self.assertEqual(Profiles["C"].Span, 1)

    def testDefaultProfilesRetainOrdinaryAccessPrefixBehavior(self) -> None:
        Placed, Witness, SourcePath, TargetPath = (
            self.BuildExplicitAccessFixture()
        )

        Profile = BuildNetRoutingProfiles(
            Placed,
            AccessLength=2,
            AccessWitness=Witness,
        )["A"]

        self.assertEqual(Profile.SourceAccessPath, SourcePath[:2])
        self.assertEqual(
            Profile.TargetAccessPaths[TargetPath[0]],
            TargetPath[:2],
        )

    def testExplicitProfilesRejectMissingSelectedAccessWitness(self) -> None:
        Placed, _Witness, _SourcePath, _TargetPath = (
            self.BuildExplicitAccessFixture()
        )

        with self.assertRaises(ValueError):
            BuildNetRoutingProfiles(
                Placed,
                AccessLength=3,
                RequireExplicitAccessWitness=True,
            )

    def testExplicitProfilesRejectShorterRequestedAccessLength(self) -> None:
        Placed, _Gates, Witness, _SourcePath, _TargetPath = (
            self.BuildRealCatalogSelectedWitnessFixture()
        )

        with self.assertRaises(ValueError):
            BuildNetRoutingProfiles(
                Placed,
                AccessLength=2,
                AccessWitness=Witness,
                RequireExplicitAccessWitness=True,
            )

    def testExplicitProfilesRejectLongerRequestedAccessLength(self) -> None:
        Placed, _Gates, Witness, _SourcePath, _TargetPath = (
            self.BuildRealCatalogSelectedWitnessFixture()
        )

        with self.assertRaises(ValueError):
            BuildNetRoutingProfiles(
                Placed,
                AccessLength=4,
                AccessWitness=Witness,
                RequireExplicitAccessWitness=True,
            )

    def testExplicitProfilesConsumeRealCatalogSelectedWitness(self) -> None:
        Placed, _Gates, Witness, SourcePath, TargetPath = (
            self.BuildRealCatalogSelectedWitnessFixture()
        )
        SelectionsByRole = {
            Selection.Role: Selection
            for Selection in Witness.Selections
        }

        self.assertEqual(Witness.AccessLength, 3)
        self.assertEqual(SelectionsByRole["Source"].Path, SourcePath)
        self.assertEqual(SelectionsByRole["Target"].Path, TargetPath)
        self.assertNotIn(
            SelectionsByRole["Source"].FirstTrackNode,
            SourcePath,
        )
        self.assertNotIn(
            SelectionsByRole["Target"].FirstTrackNode,
            TargetPath,
        )

        Profile = BuildNetRoutingProfiles(
            Placed,
            AccessLength=3,
            AccessWitness=Witness,
            RequireExplicitAccessWitness=True,
        )["A"]

        self.assertEqual(Profile.SourceAccessPath, SourcePath)
        self.assertEqual(
            Profile.TargetAccessPaths[TargetPath[0]],
            TargetPath,
        )

    def testExplicitProfilesRejectWitnessFromMovedSameNameGate(self) -> None:
        _Placed, Gates, Witness, _SourcePath, TargetPath = (
            self.BuildRealCatalogSelectedWitnessFixture()
        )
        MovedTarget = BuildPlacedGate(
            Gate("Target", GateKind.OUTPUT, [], ["A"]),
            10,
            1,
            20,
            0,
            False,
        )
        CurrentPlaced = SimpleNamespace(
            PlacedGates=[Gates[0], MovedTarget],
            FrozenNetWires={},
        )

        self.assertEqual(TargetPath[0], (10, 1, 9))
        self.assertEqual(MovedTarget.InputPins, [(10, 1, 19)])
        with self.assertRaises(ValueError):
            BuildNetRoutingProfiles(
                CurrentPlaced,
                AccessLength=3,
                AccessWitness=Witness,
                RequireExplicitAccessWitness=True,
            )

    def testExplicitProfilesRejectStaleFaceAtCurrentTerminal(self) -> None:
        _Placed, Gates, Witness, _SourcePath, TargetPath = (
            self.BuildRealCatalogSelectedWitnessFixture()
        )
        RotatedTarget = BuildPlacedGate(
            Gate("Target", GateKind.OUTPUT, [], ["A"]),
            8,
            1,
            9,
            90,
            False,
        )
        CurrentPlaced = SimpleNamespace(
            PlacedGates=[Gates[0], RotatedTarget],
            FrozenNetWires={},
        )
        StaleTarget = next(
            Selection
            for Selection in Witness.Selections
            if Selection.Role == "Target"
        )

        self.assertEqual(TargetPath[0], (10, 1, 9))
        self.assertEqual(RotatedTarget.InputPins, [(10, 1, 9)])
        self.assertEqual(StaleTarget.Face, (0, 0, -1))
        self.assertEqual(RotatedTarget.InputDirections, [(1, 0, 0)])
        with self.assertRaises(ValueError):
            BuildNetRoutingProfiles(
                CurrentPlaced,
                AccessLength=3,
                AccessWitness=Witness,
                RequireExplicitAccessWitness=True,
            )

    def testRoutingMetricsReportShape(self) -> None:
        Metrics = MeasureRoutingStage(
            "Strict + cleanup",
            {
                "A": {(0, 0, 0), (1, 0, 0), (1, 0, 1)},
                "B": {(0, 1, 0), (1, 1, 0)},
            },
            Plan=SimpleNamespace(CorridorCapacity=2),
            ReroutedNets=1,
        )

        self.assertEqual(Metrics.Stage, "Strict + cleanup")
        self.assertEqual(Metrics.TotalLength, 5)
        self.assertGreaterEqual(Metrics.BendCount, 1)
        self.assertEqual(Metrics.ReroutedNets, 1)
        self.assertEqual(Metrics.CorridorOverflowPeak, 0)


if __name__ == "__main__":
    unittest.main()

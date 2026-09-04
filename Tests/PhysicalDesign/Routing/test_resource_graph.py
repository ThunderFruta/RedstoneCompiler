import unittest

from PhysicalDesign.Geometry.Placement import PlacedGate
from PhysicalDesign.Resources.ResourceGraph import FindClaimConflicts, FindClaimConflictsByResourceIndex, LocalRouteClaim, NormalizeRoutingEdge, RoutingResourceGraph, RoutingResourceKind, ValidateLocalRouteClaims
from PhysicalDesign.Placement.Engine.Channels import LocalClusterRouteCandidate, SelectJointLocalClusterCandidates
from PhysicalDesign.Placement.Engine.MandatoryAccess import FindMandatoryAccessConflictSignals, MeasureMandatoryAccessConflictProfile


class RoutingResourceGraphTests(unittest.TestCase):
    def BuildGraph(self, *, Actual=(), Electrical=(), Solid=()):
        return RoutingResourceGraph(
            ActualBlocks=frozenset(Actual),
            ElectricalBlocks=frozenset(Electrical),
            SolidBlocks=frozenset(Solid),
        )

    def BuildMandatoryOutput(
        self,
        Name,
        Signal,
        Pin,
    ):
        return PlacedGate(
            Name=Name,
            Kind="NAND",
            X=Pin[0],
            Y=Pin[1],
            Z=Pin[2],
            Outputs=[Signal],
            Inputs=[],
            Attrs={},
            InputPins=[],
            OutputPin=Pin,
            Rotation=0,
            MirrorX=False,
            InputDirections=[],
            OutputDirection=(1, 0, 0),
        )

    def testMandatoryAccessProfileIsRenameAndTranslationIndependent(self) -> None:
        First = (
            self.BuildMandatoryOutput("First", "Alpha", (0, 1, 0)),
            self.BuildMandatoryOutput("Second", "Beta", (0, 1, 0)),
        )
        RenamedTranslated = (
            self.BuildMandatoryOutput("RenamedSecond", "Y", (11, 4, 7)),
            self.BuildMandatoryOutput("RenamedFirst", "X", (11, 4, 7)),
        )

        Baseline = MeasureMandatoryAccessConflictProfile(
            First,
            ("Alpha", "Beta"),
        )
        Candidate = MeasureMandatoryAccessConflictProfile(
            RenamedTranslated,
            ("X", "Y"),
        )

        self.assertTrue(Baseline.HasConflicts)
        self.assertEqual(
            Baseline.OwnershipFingerprint,
            Candidate.OwnershipFingerprint,
        )
        self.assertEqual(
            Baseline.ConflictFingerprint,
            Candidate.ConflictFingerprint,
        )
        self.assertEqual(
            FindMandatoryAccessConflictSignals(
                First,
                ("Alpha", "Beta"),
            ),
            dict(Baseline.CrossConflicts),
        )
        self.assertEqual(
            Baseline.ToDictionary()["ExactConflictCount"],
            Baseline.ExactConflictCount,
        )

    def testMandatoryAccessOwnershipFingerprintChangesWithTopology(self) -> None:
        Shared = MeasureMandatoryAccessConflictProfile(
            (
                self.BuildMandatoryOutput("First", "Alpha", (0, 1, 0)),
                self.BuildMandatoryOutput("Second", "Beta", (0, 1, 0)),
            ),
            ("Alpha", "Beta"),
        )
        Separated = MeasureMandatoryAccessConflictProfile(
            (
                self.BuildMandatoryOutput("First", "Alpha", (0, 1, 0)),
                self.BuildMandatoryOutput("Second", "Beta", (20, 1, 0)),
            ),
            ("Alpha", "Beta"),
        )

        self.assertNotEqual(
            Shared.OwnershipFingerprint,
            Separated.OwnershipFingerprint,
        )
        self.assertFalse(Separated.HasConflicts)

    def testLocalClaimsMergeForOneSignalAndRejectForeignAdjacency(self) -> None:
        Graph = self.BuildGraph()

        def Claim(Signal, Nodes, Root, Target):
            Ordered = tuple(Nodes)
            NodeSet = frozenset(Ordered)
            return LocalRouteClaim(
                Signal=Signal,
                ClusterId=0,
                Root=Root,
                ConnectedTargets=(Target,),
                BoundaryNodes=tuple(sorted(NodeSet)),
                Nodes=NodeSet,
                Edges=frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for First, Second in zip(Ordered, Ordered[1:])
                ),
                Claims=Graph.BuildRouteClaims(NodeSet),
            )

        First = Claim("A", ((0, 1, 0), (1, 1, 0)), (0, 1, 0), (1, 1, 0))
        Second = Claim("A", ((1, 1, 0), (2, 1, 0)), (1, 1, 0), (2, 1, 0))
        Merged = ValidateLocalRouteClaims(Graph, (First, Second))
        self.assertEqual(
            Merged["A"].WireCells,
            frozenset({(0, 1, 0), (1, 1, 0), (2, 1, 0)}),
        )
        Foreign = Claim("B", ((2, 1, 1), (3, 1, 1)), (2, 1, 1), (3, 1, 1))
        with self.assertRaisesRegex(ValueError, "claims conflict"):
            ValidateLocalRouteClaims(Graph, (First, Second, Foreign))

    def testFlatAndStairPrimitivesCarryPhysicalClaims(self) -> None:
        Graph = self.BuildGraph()
        Flat = Graph.BuildPrimitive((0, 1, 0), (1, 1, 0))
        Stair = Graph.BuildPrimitive((0, 1, 0), (1, 2, 0))

        self.assertIsNotNone(Flat)
        self.assertFalse(Flat.IsVerticalTransition)
        self.assertEqual(Flat.Claims.RequiredAirCells, frozenset())
        self.assertIn((1, 0, 0), Flat.Claims.SupportCells)
        self.assertIsNotNone(Stair)
        self.assertTrue(Stair.IsVerticalTransition)
        self.assertIn((0, 2, 0), Stair.Claims.RequiredAirCells)

    def testBlockedHeadroomRemovesStairTransition(self) -> None:
        Graph = self.BuildGraph(Actual={(0, 2, 0)}, Solid={(0, 2, 0)})

        self.assertIsNone(Graph.BuildPrimitive((0, 1, 0), (1, 2, 0)))

    def testStairRequiresSolidUpperSupportWhenCellIsOccupied(self) -> None:
        OccupiedSupport = (1, 1, 0)
        NonSolid = self.BuildGraph(Actual={OccupiedSupport})
        Solid = self.BuildGraph(
            Actual={OccupiedSupport},
            Solid={OccupiedSupport},
        )

        self.assertIsNone(NonSolid.BuildPrimitive((0, 1, 0), (1, 2, 0)))
        self.assertIsNotNone(Solid.BuildPrimitive((0, 1, 0), (1, 2, 0)))

    def testRegionContainsOnlyAuthoritativeLegalEdges(self) -> None:
        Graph = self.BuildGraph()
        Region = Graph.BuildRegion(
            (0, 2, 1, 2, 0, 1),
            AllowedColumns=frozenset({(0, 0), (1, 0), (2, 0)}),
        )

        self.assertTrue(Region.ContainsEdge((0, 1, 0), (1, 1, 0)))
        self.assertNotIn((0, 1, 1), Region.Nodes)
        self.assertEqual(
            NormalizeRoutingEdge((1, 1, 0), (0, 1, 0)),
            ((0, 1, 0), (1, 1, 0)),
        )
        self.assertEqual(Graph.CachedNodeCount, len(Region.Nodes))
        self.assertEqual(Graph.CachedEdgeCount, len(Region.Edges))

    def testRegionConstructionCanBeStoppedBeforePublishingPartialCache(self) -> None:
        Graph = self.BuildGraph()
        Phases = []

        def StopDuringEdges(Diagnostics):
            Phases.append(Diagnostics["Phase"])
            if Diagnostics["Phase"] == "edges":
                raise RuntimeError("adaptive slice expired")

        with self.assertRaisesRegex(RuntimeError, "adaptive slice expired"):
            Graph.BuildRegion(
                (0, 20, 1, 3, 0, 20),
                WorkCheck=StopDuringEdges,
            )

        self.assertIn("nodes", Phases)
        self.assertIn("edges", Phases)
        self.assertEqual(Graph.CachedNodeCount, 0)
        self.assertEqual(Graph.CachedEdgeCount, 0)

    def testEscalatedRegionReusesPriorColumnsAndLayers(self) -> None:
        Graph = self.BuildGraph()
        FirstColumns = frozenset({(0, 0), (1, 0)})
        First = Graph.BuildRegion(
            (0, 2, 1, 2, 0, 0),
            AllowedColumns=FirstColumns,
        )
        Diagnostics = []
        Second = Graph.BuildRegion(
            (0, 2, 1, 3, 0, 0),
            AllowedColumns=frozenset({(0, 0), (1, 0), (2, 0)}),
            WorkCheck=Diagnostics.append,
        )

        Complete = Diagnostics[-1]
        self.assertTrue(Complete["ReusedRegion"])
        self.assertGreater(Complete["ReusedNodeCount"], 0)
        self.assertLess(Complete["BuiltNodeCount"], len(Second.Nodes))
        self.assertTrue(First.Nodes.issubset(Second.Nodes))
        self.assertIs(
            Graph.BuildRegion(
                (0, 2, 1, 3, 0, 0),
                AllowedColumns=frozenset({(0, 0), (1, 0), (2, 0)}),
            ),
            Second,
        )

    def testEscalatedRegionReusesMonotonicAccessExtensionExactly(self) -> None:
        Bounds = (0, 2, 1, 2, 0, 0)
        Columns = frozenset({(0, 0), (1, 0), (2, 0)})
        ExtendedAccess = frozenset({(1, 1, 0)})
        Graph = self.BuildGraph(Actual={(1, 0, 0)})
        Graph.BuildRegion(Bounds, AllowedColumns=Columns)
        Diagnostics = []

        Reused = Graph.BuildRegion(
            Bounds,
            AllowedColumns=Columns,
            AllowedAccess=ExtendedAccess,
            WorkCheck=Diagnostics.append,
        )
        Cold = self.BuildGraph(Actual={(1, 0, 0)}).BuildRegion(
            Bounds,
            AllowedColumns=Columns,
            AllowedAccess=ExtendedAccess,
        )

        self.assertTrue(Diagnostics[-1]["ReusedRegion"])
        self.assertGreater(Diagnostics[-1]["ReusedNodeCount"], 0)
        self.assertIn((1, 1, 0), Reused.Nodes)
        self.assertEqual(Reused, Cold)

    def testRegionDoesNotReuseRemovedAccessNodes(self) -> None:
        Bounds = (0, 2, 1, 2, 0, 0)
        Columns = frozenset({(0, 0), (1, 0), (2, 0)})
        Graph = self.BuildGraph(Actual={(1, 0, 0), (2, 0, 0)})
        Graph.BuildRegion(
            Bounds,
            AllowedColumns=Columns,
            AllowedAccess=frozenset({(1, 1, 0)}),
        )
        Diagnostics = []

        Region = Graph.BuildRegion(
            Bounds,
            AllowedColumns=Columns,
            AllowedAccess=frozenset({(2, 1, 0)}),
            WorkCheck=Diagnostics.append,
        )

        self.assertFalse(Diagnostics[-1]["ReusedRegion"])
        self.assertNotIn((1, 1, 0), Region.Nodes)
        self.assertIn((2, 1, 0), Region.Nodes)

    def testForeignElectricalClaimsConflictButSameNetClaimsDoNot(self) -> None:
        Graph = self.BuildGraph()
        First = Graph.BuildRouteClaims({(0, 1, 0), (1, 1, 0)})
        Second = Graph.BuildRouteClaims({(1, 1, 1), (2, 1, 1)})

        self.assertEqual(FindClaimConflicts({"A": First}), {})
        Conflicts = FindClaimConflicts({"A": First, "B": Second})
        self.assertTrue(Conflicts)
        self.assertTrue(
            any(Resource.Kind == RoutingResourceKind.Electrical for Resource in Conflicts)
        )

    def testRouteClaimsReuseTheExactPositionSet(self) -> None:
        Graph = self.BuildGraph()
        Positions = frozenset({(0, 1, 0), (1, 1, 0)})

        First = Graph.BuildRouteClaims(Positions)
        Second = Graph.BuildRouteClaims(Positions)

        self.assertIs(First, Second)

    def testForeignSupportCannotOccupyAnotherSignalsWire(self) -> None:
        Graph = self.BuildGraph()
        Upper = Graph.BuildRouteClaims({(0, 1, 0)})
        Lower = Graph.BuildRouteClaims({(0, 0, 0)})

        Conflicts = FindClaimConflicts({"Upper": Upper, "Lower": Lower})

        self.assertTrue(any(
            Resource.Kind == RoutingResourceKind.Support
            and Resource.Position == (0, 0, 0)
            for Resource in Conflicts
        ))

    def testIndexedClaimConflictsMatchPairwiseConflicts(self) -> None:
        Graph = self.BuildGraph()
        Claims = {
            "A": Graph.BuildRouteClaims({
                (0, 1, 0),
                (1, 1, 0),
                (2, 2, 0),
            }),
            "B": Graph.BuildRouteClaims({
                (1, 1, 1),
                (2, 1, 1),
                (2, 2, 1),
            }),
            "C": Graph.BuildRouteClaims({
                (0, 0, 0),
                (1, 0, 0),
                (3, 1, 1),
            }),
            "D": Graph.BuildRouteClaims({
                (4, 1, 0),
                (4, 2, 0),
            }),
        }

        self.assertEqual(
            FindClaimConflictsByResourceIndex(Claims),
            FindClaimConflicts(Claims),
        )

    def testJointClusterSelectionRejectsConflictingCandidateClaims(self) -> None:
        Graph = self.BuildGraph()

        def Claim(Signal, Nodes):
            Ordered = tuple(Nodes)
            return LocalRouteClaim(
                Signal=Signal,
                ClusterId=0,
                Root=Ordered[0],
                ConnectedTargets=(Ordered[-1],),
                BoundaryNodes=(),
                Nodes=frozenset(Ordered),
                Edges=frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for First, Second in zip(Ordered, Ordered[1:])
                ),
                Claims=Graph.BuildRouteClaims(Ordered),
                ExactRouteSignalBlocks=len(Ordered),
                ExactRouteSupportBlocks=len(Ordered),
            )

        First = LocalClusterRouteCandidate(
            "cluster0:A:direct:0", Claim("A", ((0, 1, 0), (1, 1, 0)))
        )
        # This tree uses A's electrical clearance and must be rejected.
        Conflicting = LocalClusterRouteCandidate(
            "cluster0:B:direct:0", Claim("B", ((1, 1, 1), (2, 1, 1)))
        )
        Independent = LocalClusterRouteCandidate(
            "cluster0:B:direct:1", Claim("B", ((4, 1, 0), (5, 1, 0)))
        )

        Selection = SelectJointLocalClusterCandidates(
            Graph,
            (),
            {"A": (First,), "B": (Conflicting, Independent)},
            64,
        )

        self.assertEqual(
            tuple(Candidate.CandidateId for Candidate in Selection.Candidates),
            ("cluster0:A:direct:0", "cluster0:B:direct:1"),
        )
        self.assertTrue(Selection.RejectionCounts)


if __name__ == "__main__":
    unittest.main()

import unittest

from PhysicalDesign.Geometry.Placement import PlacedGate
from PhysicalDesign.Redstone.Rules import BuildPhysicalGraphs
from PhysicalDesign.Rendering.SchemWriter import BuildWireState
from PhysicalDesign.Resources.ResourceGraph import FindClaimConflicts, FindClaimConflictsByResourceIndex, FreezeRoutingResourceState, LocalRouteClaim, NormalizeRoutingEdge, RoutingResourceGraph, RoutingResourceGraphVersion, RoutingResourceKind, ValidateLocalRouteClaims
from PhysicalDesign.Placement.Engine.Channels import LocalClusterRouteCandidate, SelectJointLocalClusterCandidates
from PhysicalDesign.Placement.Engine.MandatoryAccess import FindMandatoryAccessConflictSignals, MeasureMandatoryAccessConflictProfile


class RoutingResourceGraphTests(unittest.TestCase):
    def BuildGraph(self, *, Actual=(), Electrical=(), Solid=(), BlockStates=None):
        return RoutingResourceGraph(
            ActualBlocks=frozenset(Actual),
            ElectricalBlocks=frozenset(Electrical),
            SolidBlocks=frozenset(Solid),
            BlockStates=BlockStates or {},
        )

    def test_graph_version_defaults_to_v3_is_immutable_and_allows_explicit_custom(self):
        Graph = self.BuildGraph()
        self.assertEqual(Graph.GraphVersion, RoutingResourceGraphVersion)
        with self.assertRaises(AttributeError):
            Graph.GraphVersion = "routing-resource-graph-v4"
        Custom = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
            GraphVersion="abstract-cache-test-v1",
        )
        self.assertEqual(Custom.GraphVersion, "abstract-cache-test-v1")
        with self.assertRaises(TypeError):
            RoutingResourceGraph(
                ActualBlocks=frozenset(), ElectricalBlocks=frozenset(),
                SolidBlocks=frozenset(), GraphVersion="",
            )

    def test_public_state_freezer_is_finite_canonical_and_rejects_lossy_inputs(self):
        Frozen = FreezeRoutingResourceState({
            "z": [True, 2, 3.5], "a": {"nested": None},
        })
        self.assertEqual(tuple(Frozen), ("a", "z"))
        self.assertEqual(Frozen["z"], (True, 2, 3.5))
        with self.assertRaises(TypeError):
            Frozen["z"] = ()
        Cyclic = {}
        Cyclic["self"] = Cyclic
        for Invalid in (
            float("nan"), float("inf"), float("-inf"),
            {0: "not-a-string-key"}, iter(("one-shot",)), Cyclic,
        ):
            with self.assertRaises(TypeError):
                FreezeRoutingResourceState(Invalid)

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

    def testSupportedStairRuleAgreesAcrossPhysicalConsumers(self) -> None:
        Lower = (0, 1, 0)
        Upper = (1, 2, 0)
        LowerSupport = (0, 0, 0)
        UpperSupport = (1, 1, 0)
        Headroom = (0, 2, 0)
        Nodes = frozenset({Lower, Upper})
        Edge = NormalizeRoutingEdge(Lower, Upper)
        ClearGraph = self.BuildGraph(
            Actual={UpperSupport},
            Solid={UpperSupport},
        )

        Primitive = ClearGraph.BuildPrimitive(Lower, Upper)
        self.assertIsNotNone(Primitive)
        self.assertEqual(Primitive.Claims.WireCells, Nodes)
        self.assertEqual(
            Primitive.Claims.SupportCells,
            frozenset({LowerSupport, UpperSupport}),
        )
        self.assertEqual(
            Primitive.Claims.RequiredAirCells,
            frozenset({Headroom}),
        )

        Claims = ClearGraph.BuildRouteClaims(Nodes)
        self.assertEqual(Claims.WireCells, Nodes)
        self.assertEqual(
            Claims.SupportCells,
            frozenset({LowerSupport, UpperSupport}),
        )
        self.assertEqual(Claims.RequiredAirCells, frozenset({Headroom}))
        LocalClaim = LocalRouteClaim(
            Signal="Signal",
            ClusterId=0,
            Root=Lower,
            ConnectedTargets=(Upper,),
            BoundaryNodes=(),
            Nodes=Nodes,
            Edges=frozenset({Edge}),
            Claims=Claims,
        )
        self.assertEqual(
            ValidateLocalRouteClaims(ClearGraph, (LocalClaim,))["Signal"],
            Claims,
        )

        ClearPhysical = BuildPhysicalGraphs(
            {"Signal": set(Nodes)},
            ActualBlocks={UpperSupport},
            Supports={LowerSupport, UpperSupport},
            SolidBlocks={UpperSupport},
        )
        self.assertIn(Upper, ClearPhysical["Signal"][Lower])
        self.assertIn(Lower, ClearPhysical["Signal"][Upper])
        ClearBlocks = {
            LowerSupport: {"Name": "minecraft:smooth_stone"},
            UpperSupport: {"Name": "minecraft:smooth_stone"},
        }
        self.assertEqual(
            BuildWireState(Lower, set(Nodes), ClearBlocks, 0)["Properties"]["east"],
            "up",
        )
        self.assertEqual(
            BuildWireState(Upper, set(Nodes), ClearBlocks, 0)["Properties"]["west"],
            "side",
        )

        BlockedGraph = self.BuildGraph(
            Actual={UpperSupport, Headroom},
            Solid={UpperSupport, Headroom},
        )
        self.assertIsNone(BlockedGraph.BuildPrimitive(Lower, Upper))
        with self.assertRaises(ValueError):
            ValidateLocalRouteClaims(BlockedGraph, (LocalClaim,))
        BlockedPhysical = BuildPhysicalGraphs(
            {"Signal": set(Nodes)},
            ActualBlocks={UpperSupport, Headroom},
            Supports={LowerSupport, UpperSupport},
            SolidBlocks={UpperSupport, Headroom},
        )
        self.assertNotIn(Upper, BlockedPhysical["Signal"][Lower])
        self.assertNotIn(Lower, BlockedPhysical["Signal"][Upper])
        BlockedBlocks = {
            **ClearBlocks,
            Headroom: {"Name": "minecraft:smooth_stone"},
        }
        self.assertEqual(
            BuildWireState(Lower, set(Nodes), BlockedBlocks, 0)["Properties"]["east"],
            "none",
        )

    def testWallTorchStairSeparatesGeometricConsumersFromRouteClaims(self) -> None:
        Lower = (0, 0, 0)
        Upper = (1, 1, 0)
        LowerSupport = (0, -1, 0)
        UpperSupport = (1, 0, 0)
        Headroom = (0, 1, 0)
        Backing = (-1, 1, 0)
        Nodes = frozenset({Lower, Upper})
        Edge = NormalizeRoutingEdge(Lower, Upper)
        BlockStates = {
            LowerSupport: {"Name": "minecraft:smooth_stone"},
            UpperSupport: {"Name": "minecraft:smooth_stone"},
            Headroom: {
                "Name": "minecraft:redstone_wall_torch",
                "Properties": {"facing": "east", "lit": "true"},
            },
            Backing: {"Name": "minecraft:smooth_stone"},
        }
        Graph = self.BuildGraph(
            Actual={LowerSupport, UpperSupport, Headroom, Backing},
            Electrical={Headroom},
            Solid={LowerSupport, UpperSupport, Backing},
            BlockStates=BlockStates,
        )

        Decision = Graph.QueryDustStairDecision(Lower, Upper)
        self.assertEqual(Decision.GeometryStatus.value, "Connected")
        self.assertEqual(Decision.RouteClaimStatus.value, "Unknown")
        self.assertEqual(
            Decision.ReasonCode,
            "electrical-headroom-ownership-unavailable",
        )
        self.assertIsNone(Decision.ClaimPositions)
        self.assertIsNone(Graph.BuildPrimitive(Lower, Upper))
        with self.assertRaisesRegex(ValueError, "stair-status-is-not-legal"):
            Graph.BuildRouteClaims(Nodes)

        Region = Graph.BuildRegion(
            (-1, 1, 0, 1, 0, 0),
            AllowedAccess=Nodes,
        )
        self.assertFalse(Region.ContainsEdge(Lower, Upper))
        self.assertNotIn(Edge, Region.Edges)

        Physical = BuildPhysicalGraphs(
            {"Signal": set(Nodes)},
            ActualBlocks={LowerSupport, UpperSupport, Headroom, Backing},
            Supports={LowerSupport, UpperSupport, Backing},
            SolidBlocks={LowerSupport, UpperSupport, Backing},
            BlockStates=BlockStates,
        )
        self.assertIn(Upper, Physical["Signal"][Lower])
        self.assertIn(Lower, Physical["Signal"][Upper])

        MalformedStates = {
            **BlockStates,
            Headroom: {
                "Name": "minecraft:redstone_wall_torch",
                "Properties": {"facing": "east"},
            },
        }
        MalformedPhysical = BuildPhysicalGraphs(
            {"Signal": set(Nodes)},
            ActualBlocks={LowerSupport, UpperSupport, Headroom, Backing},
            Supports={LowerSupport, UpperSupport, Backing},
            SolidBlocks={LowerSupport, UpperSupport, Backing},
            BlockStates=MalformedStates,
        )
        self.assertNotIn(Upper, MalformedPhysical["Signal"][Lower])
        self.assertNotIn(Lower, MalformedPhysical["Signal"][Upper])

    def testGraphCachesUseFrozenBlockStateSemantics(self) -> None:
        Lower = (0, 0, 0)
        Upper = (1, 1, 0)
        LowerSupport = (0, -1, 0)
        UpperSupport = (1, 0, 0)
        Headroom = (0, 1, 0)
        Backing = (-1, 1, 0)
        Nodes = frozenset({Lower, Upper})
        Edge = NormalizeRoutingEdge(Lower, Upper)
        CallerStates = {
            LowerSupport: {"Name": "minecraft:smooth_stone"},
            UpperSupport: {"Name": "minecraft:smooth_stone"},
            Headroom: {
                "Name": "minecraft:air",
                "Properties": {},
            },
            Backing: {"Name": "minecraft:smooth_stone"},
        }
        Graph = self.BuildGraph(
            Actual={LowerSupport, UpperSupport, Backing},
            Solid={LowerSupport, UpperSupport, Backing},
            BlockStates=CallerStates,
        )
        Before = Graph.QueryDustStairDecision(Lower, Upper)
        Region = Graph.BuildRegion(
            (-1, 1, 0, 1, 0, 0),
            AllowedAccess=Nodes,
        )
        Claims = Graph.BuildRouteClaims(Nodes)
        self.assertEqual(Before.RouteClaimStatus.value, "Legal")
        self.assertIn(Edge, Region.Edges)
        self.assertEqual(Claims.RequiredAirCells, frozenset({Headroom}))

        CallerStates[Headroom]["Name"] = "minecraft:redstone_wall_torch"
        CallerStates[Headroom]["Properties"].update({
            "facing": "east",
            "lit": "true",
        })
        FreshGraph = self.BuildGraph(
            Actual={LowerSupport, UpperSupport, Backing},
            Solid={LowerSupport, UpperSupport, Backing},
            BlockStates=CallerStates,
        )
        self.assertEqual(
            FreshGraph.QueryDustStairDecision(
                Lower,
                Upper,
            ).RouteClaimStatus.value,
            "Unknown",
        )

        After = Graph.QueryDustStairDecision(Lower, Upper)
        self.assertEqual(After, Before)
        self.assertEqual(Graph.BlockStates[Headroom]["Name"], "minecraft:air")
        self.assertIs(Graph.BuildRegion(
            (-1, 1, 0, 1, 0, 0),
            AllowedAccess=Nodes,
        ), Region)
        self.assertIs(Graph.BuildRouteClaims(Nodes), Claims)
        self.assertIn(Edge, Region.Edges)
        self.assertEqual(Claims.RequiredAirCells, frozenset({Headroom}))
        with self.assertRaisesRegex(AttributeError, "semantics are immutable"):
            Graph.BlockStates = CallerStates

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

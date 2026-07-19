import unittest

from Compiler.Routing.ResourceGraph import (
    FindClaimConflicts,
    LocalRouteClaim,
    NormalizeRoutingEdge,
    RoutingResourceGraph,
    RoutingResourceKind,
    ValidateLocalRouteClaims,
)


class RoutingResourceGraphTests(unittest.TestCase):
    def BuildGraph(self, *, Actual=(), Electrical=(), Solid=()):
        return RoutingResourceGraph(
            ActualBlocks=frozenset(Actual),
            ElectricalBlocks=frozenset(Electrical),
            SolidBlocks=frozenset(Solid),
        )

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


if __name__ == "__main__":
    unittest.main()

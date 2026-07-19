import unittest

from Compiler.Routing.AuthoritativePlanner import _ReserveRepeaters
from Compiler.Routing.ResourceGraph import RoutingResourceGraph
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology
from RedstoneCompiler.RustRouting import GenerateRectilinearTopology, RoutingContext


class AuthoritativePlannerTests(unittest.TestCase):
    def BuildGraph(self):
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Region = Graph.BuildRegion((0, 4, 1, 2, 0, 0))
        Context = RoutingContext(
            (0, 4, 1, 2, 0, 0),
            (0, 4, 0, 0),
            sorted(Region.Nodes),
            sorted(Region.Edges),
        )
        return Graph, Region, Context

    def testIndexedResourcesAreDeterministic(self) -> None:
        Graph, Region, _Context = self.BuildGraph()

        First = Graph.BuildIndexedGraph(Region)
        Second = Graph.BuildIndexedGraph(Region)

        self.assertEqual(First.ResourcePositions, Second.ResourcePositions)
        self.assertEqual(First.PositionIndices, Second.PositionIndices)

    def testRustPortalClaimsMatchPythonPathClaims(self) -> None:
        Graph, Region, Context = self.BuildGraph()
        Values = Context.GeneratePortalCandidates(
            [(0, 1, 0)],
            [(3, 1, 0)],
            sorted(Region.Nodes),
            1,
            8,
            1_000,
        )

        self.assertEqual(len(Values), 1)
        Portal = Values[0]
        Claims = Graph.BuildRouteClaims(Portal.Path)
        self.assertEqual(set(Portal.WireClaims), set(Claims.WireCells))
        self.assertEqual(set(Portal.SupportClaims), set(Claims.SupportCells))
        self.assertEqual(set(Portal.AirClaims), set(Claims.RequiredAirCells))
        self.assertEqual(set(Portal.ElectricalClaims), set(Claims.ElectricalCells))

    def testRustAssignmentSelectsDisjointCandidate(self) -> None:
        _Graph, _Region, Context = self.BuildGraph()
        Result = Context.PlanAuthoritativeRoutes(
            [
                ("A", "A0", [1], [], [], [0, 1, 2], 1, 1, 1, 0, 0),
                ("B", "B0", [2], [], [], [1, 2, 3], 1, 1, 1, 0, 0),
                ("B", "B1", [6], [], [], [5, 6, 7], 2, 2, 2, 0, 0),
            ],
            8,
            100,
        )

        self.assertTrue(Result.Success)
        self.assertEqual(
            dict(Result.SelectedCandidateIds),
            {"A": "A0", "B": "B1"},
        )
        self.assertGreater(Result.ExpansionCount, 0)

    def testRustAssignmentRespectsCompleteLocalBaseOwner(self) -> None:
        _Graph, _Region, Context = self.BuildGraph()
        Result = Context.PlanAuthoritativeRoutesWithBase(
            [
                ("B", "blocked", [2], [], [], [1, 2, 3], 1, 1, 1, 0, 0),
                ("B", "clear", [6], [], [], [5, 6, 7], 2, 2, 2, 0, 0),
            ],
            [("A", [1], [], [], [0, 1, 2])],
            8,
            100,
        )

        self.assertTrue(Result.Success)
        self.assertEqual(dict(Result.SelectedCandidateIds), {"B": "clear"})

    def testMultiSourceFanoutReusesOneSharedTrunk(self) -> None:
        _Graph, Region, Context = self.BuildGraph()
        Tree = Context.GenerateRouteTree(
            [(0, 1, 0), (1, 1, 0), (2, 1, 0)],
            [[(4, 1, 0)], [(3, 1, 0)]],
            sorted(Region.Nodes),
            [(Index, 0) for Index in range(5)],
            1,
            0,
            0,
            0,
            1_000,
        )

        self.assertIsNotNone(Tree)
        self.assertEqual(set(Tree), {(Index, 1, 0) for Index in range(5)})
        IndependentPathBlocks = 5 + 4
        self.assertLess(len(Tree), IndependentPathBlocks)

    def testNativeTopologyIsDeterministicAndRectilinear(self) -> None:
        First = GenerateRectilinearTopology([(4, 4), (0, 0), (4, 0)])
        Second = GenerateRectilinearTopology([(4, 0), (4, 4), (0, 0)])

        self.assertEqual(First, Second)
        self.assertTrue(
            all(A[0] == B[0] or A[1] == B[1] for A, B in First)
        )

    def testFifteenEdgeRunRequiresRefreshBeforePowerReachesZero(self) -> None:
        Path = tuple((Index, 1, 0) for Index in range(16))
        Graph = {
            Position: [
                Neighbor
                for Neighbor in Path
                if abs(Neighbor[0] - Position[0]) == 1
            ]
            for Position in Path
        }

        Reservations, Paths = _ReserveRepeaters(
            "A",
            Path[0],
            (Path[-1],),
            Graph,
            DefaultRedstoneRoutingTechnology,
        )

        self.assertEqual(Paths[Path[-1]], Path)
        self.assertEqual(len(Reservations), 1)
        self.assertLess(
            Reservations[0].Position[0],
            DefaultRedstoneRoutingTechnology.MaximumUnrefreshedDustLength,
        )


if __name__ == "__main__":
    unittest.main()

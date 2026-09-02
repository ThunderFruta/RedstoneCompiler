"""PhysicalExteriorConnectorDistanceFieldTests contracts."""

from ._authoritative_planner_contracts import *


class PhysicalExteriorConnectorDistanceFieldTests(unittest.TestCase):
    class GridResourceGraph:
        def __init__(self, ForbiddenEdges=()):
            self.ForbiddenEdges = frozenset(ForbiddenEdges)

        def BuildPrimitive(self, First, Second):
            if (
                First[1] != Second[1]
                or sum(
                    abs(First[Index] - Second[Index])
                    for Index in range(3)
                ) != 1
                or (First, Second) in self.ForbiddenEdges
            ):
                return None
            return object()

    @staticmethod
    def BuildField(
        *,
        Targets=frozenset(((4, 0, 0),)),
        BlockedGuideCells=frozenset(),
        EdgeIsLegal=lambda _First, _Second: True,
        ResourceGraph=None,
        ResourceGraphFingerprint="",
        ForeignClaimsFingerprint="",
    ):
        return BuildPhysicalExteriorConnectorDistanceField(
            ResourceGraph or (
                PhysicalExteriorConnectorDistanceFieldTests
                .GridResourceGraph()
            ),
            Targets,
            EnvelopeMinimum=(10, 0, 10),
            EnvelopeMaximum=(10, 0, 10),
            BlockedGuideCells=BlockedGuideCells,
            Margin=1,
            Bounds=(-1, 5, -2, 2),
            EdgeIsLegal=EdgeIsLegal,
            ResourceGraphFingerprint=ResourceGraphFingerprint,
            ForeignClaimsFingerprint=ForeignClaimsFingerprint,
        )

    def testOneFieldServesMultipleSeams(self):
        Graph = self.GridResourceGraph()
        Field = self.BuildField(ResourceGraph=Graph)
        Results = tuple(
            SelectPhysicalExteriorConnectorPath(
                Field,
                Graph,
                Start,
                BlockedLocalNodes=frozenset(),
                EdgeIsLegal=lambda _First, _Second: True,
                ValidateCandidate=lambda _Path: True,
            )
            for Start in ((0, 0, 0), (0, 0, 1))
        )
        self.assertGreater(Field.BuildExpansionCount, 0)
        self.assertTrue(all(Result.UsedCanonicalField for Result in Results))
        self.assertTrue(all(not Result.UsedFallback for Result in Results))
        self.assertEqual(
            tuple(Result.Path[-1] for Result in Results),
            ((4, 0, 0), (4, 0, 0)),
        )

    def testFrozenNativeBatchMatchesExactStaticSelector(self):
        Graph = self.GridResourceGraph()
        Field = self.BuildField(ResourceGraph=Graph)
        Nodes = frozenset(
            (X, 0, Z)
            for X in range(-1, 6)
            for Z in range(-2, 3)
        )
        Edges = frozenset(
            tuple(sorted((Position, Neighbor)))
            for Position in Nodes
            for Neighbor in (
                (Position[0] + 1, 0, Position[2]),
                (Position[0], 0, Position[2] + 1),
            )
            if Neighbor in Nodes
        )
        FrozenField = replace(
            Field,
            AllowedNodes=Nodes,
            AllowedEdges=Edges,
            Complete=True,
        )
        Starts = ((0, 0, -1), (0, 0, 0), (0, 0, 1), (1, 0, 1),
                  (2, 0, -1), (3, 0, 1), (0, 0, 2), (1, 0, -2))
        Expected = tuple(
            SelectPhysicalExteriorConnectorPath(
                FrozenField,
                Graph,
                Start,
                BlockedLocalNodes=frozenset(),
                EdgeIsLegal=lambda _First, _Second: True,
                ValidateCandidate=lambda _Path: True,
            )
            for Start in Starts
        )
        Actual, ActiveWorkers = SearchFrozenPhysicalExteriorConnectorBatch(
            FrozenPhysicalExteriorConnectorSearchRequest(
                FrozenField,
                Start,
                frozenset(),
            )
            for Start in Starts
        )
        self.assertEqual(Actual, Expected)
        self.assertEqual(ActiveWorkers, 8)

    def testFieldExcludesKeepoutAndForeignIllegalEdges(self):
        ForbiddenEdge = frozenset(((1, 0, 0), (1, 0, -1)))
        EdgeIsLegal = lambda First, Second: frozenset((
            First,
            Second,
        )) != ForbiddenEdge
        Graph = self.GridResourceGraph()
        Field = self.BuildField(
            BlockedGuideCells=frozenset(((2, 0),)),
            EdgeIsLegal=EdgeIsLegal,
            ResourceGraph=Graph,
        )
        Result = SelectPhysicalExteriorConnectorPath(
            Field,
            Graph,
            (0, 0, 0),
            BlockedLocalNodes=frozenset(),
            EdgeIsLegal=EdgeIsLegal,
            ValidateCandidate=lambda _Path: True,
        )
        self.assertTrue(Result.Path)
        self.assertNotIn((2, 0, 0), Result.Path)
        self.assertTrue(all(
            frozenset((First, Second)) != ForbiddenEdge
            for First, Second in zip(Result.Path, Result.Path[1:])
        ))

    def testFieldIdentityIsOrderAndExternalSignalNameInvariant(self):
        Graph = self.GridResourceGraph()
        First = self.BuildField(
            Targets=frozenset(((4, 0, 0), (4, 0, 1))),
            ResourceGraph=Graph,
        )
        Second = self.BuildField(
            Targets=frozenset(reversed(((4, 0, 0), (4, 0, 1)))),
            ResourceGraph=Graph,
        )
        FieldsBySignal = {"RenamedSignal": Second}
        self.assertEqual(First.FieldFingerprint, Second.FieldFingerprint)
        self.assertEqual(
            First.FieldFingerprint,
            FieldsBySignal["RenamedSignal"].FieldFingerprint,
        )
        self.assertEqual(First.NextNodeByNode, Second.NextNodeByNode)

    def testFieldIdentityIncludesResourceAndForeignClaims(self):
        Graph = self.GridResourceGraph()
        Baseline = self.BuildField(
            ResourceGraph=Graph,
            ResourceGraphFingerprint="resource-a",
            ForeignClaimsFingerprint="claims-a",
        )
        OtherResource = self.BuildField(
            ResourceGraph=Graph,
            ResourceGraphFingerprint="resource-b",
            ForeignClaimsFingerprint="claims-a",
        )
        OtherClaims = self.BuildField(
            ResourceGraph=Graph,
            ResourceGraphFingerprint="resource-a",
            ForeignClaimsFingerprint="claims-b",
        )
        self.assertNotEqual(
            Baseline.FieldFingerprint,
            OtherResource.FieldFingerprint,
        )
        self.assertNotEqual(
            Baseline.FieldFingerprint,
            OtherClaims.FieldFingerprint,
        )

    def testBlockedCanonicalPathUsesExactFallback(self):
        Graph = self.GridResourceGraph()
        Field = self.BuildField(
            Targets=frozenset(((2, 0, 0),)),
            ResourceGraph=Graph,
        )
        Result = SelectPhysicalExteriorConnectorPath(
            Field,
            Graph,
            (0, 0, 0),
            BlockedLocalNodes=frozenset(((1, 0, 0),)),
            EdgeIsLegal=lambda _First, _Second: True,
            ValidateCandidate=lambda _Path: True,
        )
        self.assertTrue(Result.UsedFallback)
        self.assertFalse(Result.UsedCanonicalField)
        self.assertGreater(Result.FallbackExpansionCount, 0)
        self.assertNotIn((1, 0, 0), Result.Path)
        self.assertEqual(Result.Path[-1], (2, 0, 0))

    def testForeignClaimBlockedCanonicalEdgeUsesExactFallback(self):
        Graph = self.GridResourceGraph()
        Field = self.BuildField(
            Targets=frozenset(((2, 0, 0),)),
            ResourceGraph=Graph,
        )
        BlockedEdge = frozenset(((0, 0, 0), (1, 0, 0)))

        Result = SelectPhysicalExteriorConnectorPath(
            Field,
            Graph,
            (0, 0, 0),
            BlockedLocalNodes=frozenset(),
            EdgeIsLegal=lambda First, Second: frozenset((
                First,
                Second,
            )) != BlockedEdge,
            ValidateCandidate=lambda _Path: True,
        )

        self.assertTrue(Result.UsedFallback)
        self.assertFalse(Result.UsedCanonicalField)
        self.assertTrue(Result.Path)
        self.assertEqual(Result.Path[-1], (2, 0, 0))
        self.assertTrue(all(
            frozenset((First, Second)) != BlockedEdge
            for First, Second in zip(Result.Path, Result.Path[1:])
        ))

    def testDisconnectedStartReturnsCompleteUnreachableResult(self):
        Graph = self.GridResourceGraph()
        Field = self.BuildField(
            Targets=frozenset(((4, 0, 0),)),
            BlockedGuideCells=frozenset(
                (1, Z) for Z in range(-2, 3)
            ),
            ResourceGraph=Graph,
        )

        Result = SelectPhysicalExteriorConnectorPath(
            Field,
            Graph,
            (0, 0, 0),
            BlockedLocalNodes=frozenset(),
            EdgeIsLegal=lambda _First, _Second: True,
            ValidateCandidate=lambda _Path: True,
        )

        self.assertEqual(Result.Path, ())
        self.assertFalse(Result.UsedCanonicalField)
        self.assertTrue(Result.UsedFallback)
        self.assertGreater(Result.FallbackExpansionCount, 0)

    def testSharedFieldMatchesTinyBruteBfsReachability(self):
        Graph = self.GridResourceGraph()
        BlockedGuideCells = frozenset(((2, 1),))
        Field = self.BuildField(
            Targets=frozenset(((4, 0, 0), (4, 0, 1))),
            BlockedGuideCells=BlockedGuideCells,
            ResourceGraph=Graph,
        )

        def BruteDistance(Start, BlockedLocalNodes):
            MinimumX, MaximumX, MinimumZ, MaximumZ = Field.Bounds
            Pending = deque(((Start, 0),))
            Seen = {Start}
            while Pending:
                Current, Distance = Pending.popleft()
                if Current in Field.Targets:
                    return Distance
                X, Y, Z = Current
                for Neighbor in (
                    (X - 1, Y, Z),
                    (X + 1, Y, Z),
                    (X, Y, Z - 1),
                    (X, Y, Z + 1),
                ):
                    if (
                        Neighbor in Seen
                        or Neighbor in BlockedLocalNodes
                        or not (MinimumX <= Neighbor[0] <= MaximumX)
                        or not (MinimumZ <= Neighbor[2] <= MaximumZ)
                        or (Neighbor[0], Neighbor[2])
                        in Field.BlockedGuideCells
                        or Graph.BuildPrimitive(Current, Neighbor) is None
                    ):
                        continue
                    Seen.add(Neighbor)
                    Pending.append((Neighbor, Distance + 1))
            return None

        for Start, Blocked in (
            ((0, 0, 0), frozenset()),
            ((0, 0, 1), frozenset(((1, 0, 1),))),
            ((5, 0, 2), frozenset()),
        ):
            Result = SelectPhysicalExteriorConnectorPath(
                Field,
                Graph,
                Start,
                BlockedLocalNodes=Blocked,
                EdgeIsLegal=lambda _First, _Second: True,
                ValidateCandidate=lambda _Path: True,
            )
            ExpectedDistance = BruteDistance(Start, Blocked)
            self.assertEqual(bool(Result.Path), ExpectedDistance is not None)
            if Result.Path:
                self.assertEqual(len(Result.Path) - 1, ExpectedDistance)

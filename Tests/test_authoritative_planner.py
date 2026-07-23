import unittest
from collections import Counter
from dataclasses import replace
from time import monotonic, sleep
from unittest.mock import patch

from Compiler.Routing.AuthoritativePlanner import (
    BuildNegotiatedInitialColumns,
    BuildNegotiatedInitialTiles,
    BuildNegotiatedFallbackGuideColumns,
    BuildNegotiatedRouteTreeState,
    BuildRoutingConflictGraph,
    CandidatePortalShapeRank,
    CandidateRequestWindowOffset,
    ChooseRepeatedWorkTransition,
    ExpandNegotiatedTiles,
    FindNegotiatedBoundaryTouches,
    GrowAssignmentExpansionLimit,
    PlanNegotiatedRouteTrees,
    NegotiatedColumnsForTiles,
    RawPortalGeometryCache,
    RequiredRoutingLayerCountForAccess,
    ReserveBoundaryPortals,
    ReserveNegotiatedBoundaryEscapes,
    SelectEscalatedRoutingLayerCount,
    SelectAuthoritativeBaseClaims,
    SelectGraphAccessStarts,
    SelectInitialRoutingLayerCount,
    ShouldGrowAssignmentBudget,
    ShouldRunShapeOptimization,
    _BuildTargetPortalBranches,
    _MaterializeCandidate,
    _ReserveRepeaters,
)
from Compiler.Routing.ChannelPlanner import NetRoutingProfile
from Compiler.Routing.Failures import RoutingFailureReason, RoutingStageError
from Compiler.Routing.Models import RoutingResources, RoutingStaticGeometry
from Compiler.Routing.Policy import DefaultPhysicalDesignPolicy
from Compiler.Routing.Reliability import RoutingDeadline
from Compiler.Routing.ResourceGraph import (
    IndexedRoutingResourceGraph,
    LocalRouteClaim,
    NetRouteCandidate,
    PinAccessPortal,
    RoutingResourceClaims,
    RoutingResourceId,
    RoutingResourceKind,
    RoutingResourceGraph,
    RoutingGraphRegion,
)
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology
from RedstoneCompiler.RustRouting import GenerateRectilinearTopology, RoutingContext


class AuthoritativePlannerTests(unittest.TestCase):
    def BuildPortal(self, Signal, Terminal, Position, Layer=0):
        Claims = RoutingResourceClaims(WireCells=frozenset((Position,)))
        return PinAccessPortal(
            PortalId=f"{Signal}:{Position}", Signal=Signal, Terminal=Terminal,
            Layer=Layer, Path=(Position,), Edges=frozenset(), Claims=Claims,
            Length=0, BendCount=0, ViaCount=0, Cost=0,
        )

    def BuildCandidate(self, Signal, CandidateId, Position):
        Claims = RoutingResourceClaims(WireCells=frozenset((Position,)))
        return NetRouteCandidate(
            CandidateId=CandidateId, Signal=Signal, SourcePortalId="source",
            TargetPortalIds={}, Nodes=frozenset((Position,)), Edges=frozenset(),
            Claims=Claims, Layer=0, Guide=frozenset(), RepeaterWaypoints=(),
            MaterialCost=1, FootprintGrowth=1, Length=1, BendCount=0, ViaCount=0,
        )

    def testCandidateRetriesStartAtUnseenPortalShapes(self) -> None:
        self.assertEqual(CandidateRequestWindowOffset(8, 2, 8, 0), 0)
        self.assertEqual(CandidateRequestWindowOffset(8, 2, 8, 1), 1)
        self.assertEqual(CandidateRequestWindowOffset(8, 2, 8, 2), 3)

        OrderedVariants = sorted(
            range(6),
            key=lambda Variant: CandidatePortalShapeRank(
                Variant,
                AxisIndex=0,
                LaneIndex=0,
                LayerIndex=0,
                PortalVariantCount=6,
                LaneCount=1,
                RequestWindowOffset=3,
            ),
        )
        self.assertEqual(OrderedVariants, [3, 4, 5, 0, 1, 2])

    def testCandidatePortalShapesUseEveryStartBeforeAlternateAxis(self) -> None:
        OrderedShapes = sorted(
            (
                (Variant, AxisIndex)
                for AxisIndex in range(2)
                for Variant in range(3)
            ),
            key=lambda Value: CandidatePortalShapeRank(
                Value[0],
                Value[1],
                LaneIndex=0,
                LayerIndex=0,
                PortalVariantCount=3,
                LaneCount=1,
                RequestWindowOffset=0,
            ),
        )
        self.assertEqual(
            OrderedShapes,
            [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)],
        )

        RankZeroShapesByLayer = []
        for LayerIndex in range(6):
            RankZeroShapesByLayer.append(next(
                (Variant, AxisIndex)
                for AxisIndex in range(2)
                for Variant in range(3)
                if CandidatePortalShapeRank(
                    Variant,
                    AxisIndex,
                    LaneIndex=0,
                    LayerIndex=LayerIndex,
                    PortalVariantCount=3,
                    LaneCount=1,
                    RequestWindowOffset=0,
                ) == 0
            ))
        self.assertEqual(
            RankZeroShapesByLayer,
            [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)],
        )

        OrderedLaneShapes = sorted(
            (
                (Variant, LaneIndex)
                for LaneIndex in range(2)
                for Variant in range(3)
            ),
            key=lambda Value: CandidatePortalShapeRank(
                Value[0],
                AxisIndex=0,
                LaneIndex=Value[1],
                LayerIndex=0,
                PortalVariantCount=3,
                LaneCount=2,
                RequestWindowOffset=0,
            ),
        )
        self.assertEqual(
            OrderedLaneShapes,
            [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)],
        )

    def testBoundaryPortalReservationUsesDisjointForeignSlots(self) -> None:
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0))
        Second = self.BuildPortal("A", (0, 1, 0), (2, 1, 0))
        Foreign = self.BuildPortal("B", (4, 1, 0), (1, 1, 0))
        ForeignClear = self.BuildPortal("B", (4, 1, 0), (3, 1, 0))
        Reserved, Reservations = ReserveBoundaryPortals({
            ("A", (0, 1, 0), 0): (First, Second),
            ("B", (4, 1, 0), 0): (Foreign, ForeignClear),
        })
        self.assertEqual(Reservations[0].PortalId, First.PortalId)
        self.assertEqual(Reservations[1].PortalId, ForeignClear.PortalId)
        self.assertEqual(Reserved[("B", (4, 1, 0), 0)][0], ForeignClear)

    def testBoundaryPortalReservationSolvesScarceEscapeBeforeCheapEscape(self) -> None:
        # A naive signal-order allocator picks A's cheap first stem and makes
        # B impossible.  The escape allocator instead selects B's one legal
        # stem first, then moves A to its remaining compatible stem.
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0))
        Alternate = self.BuildPortal("A", (0, 1, 0), (2, 1, 0))
        Scarce = self.BuildPortal("B", (4, 1, 0), (1, 1, 0))
        Reserved, _Reservations = ReserveBoundaryPortals({
            ("A", (0, 1, 0), 0): (First, Alternate),
            ("B", (4, 1, 0), 0): (Scarce,),
        })
        self.assertEqual(Reserved[("A", (0, 1, 0), 0)], (Alternate,))
        self.assertEqual(Reserved[("B", (4, 1, 0), 0)], (Scarce,))

    def testBoundaryPortalReservationDoesNotCrossReserveLayers(self) -> None:
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0), Layer=0)
        Second = self.BuildPortal("B", (4, 1, 0), (1, 1, 0), Layer=1)
        Reserved, _Reservations = ReserveBoundaryPortals({
            ("A", (0, 1, 0), 0): (First,),
            ("B", (4, 1, 0), 1): (Second,),
        })
        self.assertEqual(Reserved[("A", (0, 1, 0), 0)], (First,))
        self.assertEqual(Reserved[("B", (4, 1, 0), 1)], (Second,))

    def testBoundaryPortalReservationAllowsLayerSpecificInaccessibility(self) -> None:
        Terminal = (0, 7, 0)
        Reachable = self.BuildPortal("A", Terminal, (2, 4, 0), Layer=1)

        Reserved, Reservations = ReserveBoundaryPortals({
            ("A", Terminal, 0): (),
            ("A", Terminal, 1): (Reachable,),
        })

        self.assertEqual(Reserved[("A", Terminal, 0)], ())
        self.assertEqual(Reserved[("A", Terminal, 1)], (Reachable,))
        self.assertEqual(tuple(Value.Layer for Value in Reservations), (1,))

    def testBoundaryPortalReservationRejectsTerminalWithoutAnyLayer(self) -> None:
        Terminal = (0, 7, 0)

        with self.assertRaisesRegex(
            RoutingStageError,
            "no boundary-portal geometry available on any layer",
        ) as Context:
            ReserveBoundaryPortals({
                ("A", Terminal, 0): (),
                ("A", Terminal, 1): (),
            })

        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.NoBoundaryEscape,
        )
        self.assertEqual(Context.exception.Failure.AffectedNets, ("A",))
        self.assertEqual(
            Context.exception.Failure.Diagnostics["Layers"],
            [0, 1],
        )

    def testNegotiatedReservationSelectsNetWideSelfLegalPortalTuple(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Signal = "A"
        Root = (10, 1, 0)
        Target = (20, 1, 0)

        def Portal(Label, Terminal, Position):
            return PinAccessPortal(
                PortalId=Label,
                Signal=Signal,
                Terminal=Terminal,
                Layer=0,
                Path=(Position,),
                Edges=frozenset(),
                Claims=Graph.BuildRouteClaims((Position,)),
                Length=0,
                BendCount=0,
                ViaCount=0,
                Cost=0,
            )

        Source = Portal("source", Root, (0, 2, 0))
        ConflictingTarget = Portal("target-conflict", Target, (0, 1, 0))
        LegalTarget = Portal("target-legal", Target, (3, 1, 0))
        Profile = NetRoutingProfile(
            Signal=Signal,
            Root=Root,
            Targets=(Target,),
            Span=10,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
        )

        Reserved, Reservations = ReserveNegotiatedBoundaryEscapes(
            {
                (Signal, Root, 0): (Source,),
                (Signal, Target, 0): (
                    ConflictingTarget,
                    LegalTarget,
                ),
            },
            {Signal: Profile},
            Resources,
        )

        self.assertEqual(
            Reserved[(Signal, Target, 0)],
            (LegalTarget,),
        )
        self.assertEqual(
            {Reservation.PortalId for Reservation in Reservations},
            {"source", "target-legal"},
        )

    def testStackedAccessRaisesOnlyTheNecessaryRoutingLayerFloor(self) -> None:
        Technology = DefaultRedstoneRoutingTechnology

        self.assertEqual(
            RequiredRoutingLayerCountForAccess(
                1,
                frozenset({(0, 19, 0)}),
                GuideExpansion=3,
                Technology=Technology,
            ),
            8,
        )
        self.assertEqual(
            RequiredRoutingLayerCountForAccess(
                1,
                frozenset({(0, 3, 0)}),
                GuideExpansion=3,
                Technology=Technology,
            ),
            Technology.MinimumRoutingLayerCount,
        )

    def testRelocatedPlacementCanClimbTheLayerLadder(self) -> None:
        Arguments = {
            "MinimumLayerCount": 3,
            "EffectiveMaximumLayerCount": 8,
            "RequiredAccessLayerCount": 3,
            "AdaptiveLayerCount": 3,
            "AdaptiveLayerFloor": 0,
            "NegotiatedLayerFloor": 2,
            "ExistingRouteLayerCount": 1,
            "PlacementWasRelocated": True,
        }
        self.assertEqual(
            SelectInitialRoutingLayerCount(
                **Arguments,
                ForceMaximumAfterPlacementRelocation=True,
            ),
            8,
        )
        self.assertEqual(
            SelectInitialRoutingLayerCount(
                **Arguments,
                ForceMaximumAfterPlacementRelocation=False,
            ),
            3,
        )
        self.assertEqual(
            SelectEscalatedRoutingLayerCount(
                LayerCount=3,
                EffectiveMaximumLayerCount=8,
                ConflictClassification="relocated-pairwise-incompatibility",
                ForceMaximumAfterPlacementRelocation=True,
            ),
            8,
        )
        self.assertEqual(
            SelectEscalatedRoutingLayerCount(
                LayerCount=3,
                EffectiveMaximumLayerCount=8,
                ConflictClassification="relocated-pairwise-incompatibility",
                ForceMaximumAfterPlacementRelocation=False,
            ),
            4,
        )

    def testGreedyBoundaryPortalReservationVariantChangesPhysicalSlot(self) -> None:
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0))
        Second = self.BuildPortal("A", (0, 1, 0), (2, 1, 0))
        Portals = {("A", (0, 1, 0), 0): (First, Second)}

        Initial, _InitialReservations = ReserveBoundaryPortals(
            Portals,
            ReservationVariant=0,
            RequireConflictFree=False,
        )
        Alternate, _AlternateReservations = ReserveBoundaryPortals(
            Portals,
            ReservationVariant=1,
            RequireConflictFree=False,
        )

        self.assertEqual(Initial[("A", (0, 1, 0), 0)], (First,))
        self.assertEqual(Alternate[("A", (0, 1, 0), 0)], (Second,))

    def testRawPortalCacheMatchesOnlyIdenticalGeometryControls(self) -> None:
        Placed = object()
        Resources = object()
        Region = object()
        Context = object()
        Cache = RawPortalGeometryCache(
            PlacedIdentity=id(Placed),
            ResourcesIdentity=id(Resources),
            Region=Region,
            LayerCount=3,
            PortalLimit=9,
            PortalVariantCounts=(("A", 9),),
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            Context=Context,
            PortalEntries=(),
            RequestCount=3,
            TargetCount=9,
            StarvationCount=0,
        )

        self.assertTrue(Cache.Matches(
            Placed, Resources, Region, 3, 9, {"A": 9}, 3, 100
        ))
        self.assertFalse(Cache.Matches(
            Placed, Resources, Region, 4, 9, {"A": 9}, 3, 100
        ))
        self.assertFalse(Cache.Matches(
            object(), Resources, Region, 3, 9, {"A": 9}, 3, 100
        ))

        BaseRegion = RoutingGraphRegion(
            (0, 1, 0, 1, 0, 0),
            frozenset({(0, 0, 0)}),
            frozenset(),
        )
        ExpandedRegion = RoutingGraphRegion(
            BaseRegion.Bounds,
            frozenset({(0, 0, 0), (1, 0, 0)}),
            frozenset({((0, 0, 0), (1, 0, 0))}),
        )
        GrowingCache = replace(Cache, Region=BaseRegion)
        self.assertTrue(GrowingCache.Matches(
            Placed, Resources, ExpandedRegion, 3, 9, {"A": 9}, 3, 100
        ))

    def testReservedFilteringDoesNotMutateRawPortalCache(self) -> None:
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0))
        Second = self.BuildPortal("A", (0, 1, 0), (2, 1, 0))
        Key = ("A", (0, 1, 0), 0)
        Cache = RawPortalGeometryCache(
            PlacedIdentity=1,
            ResourcesIdentity=2,
            Region=object(),
            LayerCount=1,
            PortalLimit=2,
            PortalVariantCounts=(("A", 2),),
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            Context=object(),
            PortalEntries=((Key, (First, Second)),),
            RequestCount=1,
            TargetCount=2,
            StarvationCount=0,
        )

        Reserved, _Reservations = ReserveBoundaryPortals(
            Cache.BuildPortalDictionary()
        )

        self.assertEqual(len(Reserved[Key]), 1)
        self.assertEqual(Cache.BuildPortalDictionary()[Key], (First, Second))

    def testConflictGraphClassifiesPairwiseIncompatibility(self) -> None:
        Result = type("Result", (), {
            "BudgetExhausted": False,
            "FailureNet": "B",
            "ConflictSignals": ["B", "A"],
            "ExpansionCount": 7,
            "ConflictResourceIndices": [0],
        })()
        Graph = BuildRoutingConflictGraph(
            {
                "A": [self.BuildCandidate("A", "A0", (0, 1, 0))],
                "B": [self.BuildCandidate("B", "B0", (0, 1, 0))],
            },
            Result,
            ((0, 1, 0),),
            (),
        )
        self.assertEqual(Graph["Classification"], "pairwise-incompatibility")
        self.assertEqual(Graph["PairwiseIncompatibleEdges"], [["A", "B"]])
        self.assertEqual(Graph["NativeConflictSignals"], ["A", "B"])
        self.assertEqual(Graph["ConflictSignals"], ["A", "B"])

    def testConflictGraphIncludesSupportVersusWireConflicts(self) -> None:
        SupportPosition = (2, 1, 0)
        SupportCandidate = replace(
            self.BuildCandidate("A", "A0", (0, 1, 0)),
            Claims=RoutingResourceClaims(
                SupportCells=frozenset({SupportPosition})
            ),
        )
        WireCandidate = self.BuildCandidate("B", "B0", SupportPosition)
        Result = type("Result", (), {
            "BudgetExhausted": False,
            "FailureNet": "B",
            "ExpansionCount": 1,
            "ConflictResourceIndices": [],
        })()

        Graph = BuildRoutingConflictGraph(
            {"A": [SupportCandidate], "B": [WireCandidate]},
            Result,
            (),
            (),
        )

        self.assertEqual(Graph["Classification"], "pairwise-incompatibility")
        self.assertEqual(Graph["PairwiseIncompatibleEdges"], [["A", "B"]])

    def testConflictGraphUsesTypedHigherOrderAssignmentOffenders(self) -> None:
        Result = type("Result", (), {
            "BudgetExhausted": False,
            "FailureNet": "C",
            "ConflictSignals": ["C", "A", "B", "A"],
            "ExpansionCount": 2,
            "ConflictResourceIndices": [3],
        })()
        Graph = BuildRoutingConflictGraph(
            {
                "A": [self.BuildCandidate("A", "A0", (0, 1, 0))],
                "B": [self.BuildCandidate("B", "B0", (3, 1, 0))],
                "C": [self.BuildCandidate("C", "C0", (6, 1, 0))],
            },
            Result,
            tuple((Index, 1, 0) for Index in range(8)),
            (),
        )

        self.assertEqual(
            Graph["Classification"],
            "higher-order-placement-conflict",
        )
        self.assertEqual(Graph["PairwiseIncompatibleEdges"], [])
        self.assertEqual(Graph["NativeConflictSignals"], ["A", "B", "C"])
        self.assertEqual(Graph["ConflictSignals"], ["A", "B", "C"])
        self.assertEqual(Graph["ResourceHotspots"], [[3, 1, 0]])

    def testConflictGraphClassificationCanBeStoppedDuringCandidatePairs(self) -> None:
        Result = type("Result", (), {
            "BudgetExhausted": False,
            "FailureNet": "B",
            "ConflictSignals": ["A", "B"],
            "ExpansionCount": 1,
            "ConflictResourceIndices": [],
        })()
        Candidates = {
            Signal: [
                self.BuildCandidate(
                    Signal,
                    f"{Signal}{Index}",
                    (0, 1, 0),
                )
                for Index in range(9)
            ]
            for Signal in ("A", "B")
        }
        Observed = []

        def StopDuringCandidatePairs(Diagnostics):
            Observed.append(Diagnostics)
            if (
                Diagnostics["Phase"] == "candidate-pairs"
                and Diagnostics["CandidatePairChecks"] >= 64
            ):
                raise RuntimeError("classification deadline expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "classification deadline expired",
        ):
            BuildRoutingConflictGraph(
                Candidates,
                Result,
                (),
                (),
                WorkCheck=StopDuringCandidatePairs,
            )

        self.assertEqual(Observed[-1]["CandidatePairChecks"], 64)
        self.assertEqual(Observed[-1]["CompletedSignalPairs"], 0)

    def testAssignmentBudgetGrowthRequiresExplicitRustExhaustion(self) -> None:
        Exhausted = type("Result", (), {"BudgetExhausted": True})()
        Incompatible = type("Result", (), {"BudgetExhausted": False})()
        Legacy = type("Result", (), {"ExpansionCount": 128})()
        self.assertTrue(ShouldGrowAssignmentBudget(Exhausted))
        self.assertFalse(ShouldGrowAssignmentBudget(Incompatible))
        self.assertFalse(ShouldGrowAssignmentBudget(Legacy))

    def testAssignmentExpansionGrowthIsSmoothAndBounded(self) -> None:
        self.assertEqual(GrowAssignmentExpansionLimit(128, 50_000, 2), 256)
        self.assertEqual(GrowAssignmentExpansionLimit(32_768, 50_000, 2), 50_000)
        self.assertEqual(GrowAssignmentExpansionLimit(50_000, 50_000, 2), 50_000)
        with self.assertRaises(ValueError):
            GrowAssignmentExpansionLimit(128, 50_000, 1)

    def testFirstLegalSkipsResultOnlyShapeOptimization(self) -> None:
        self.assertFalse(ShouldRunShapeOptimization("first-legal"))
        self.assertTrue(ShouldRunShapeOptimization("best-quality"))

    def testRepeatedReservedWorkTransitionsOnceToUnreservedOnSameDeadline(self) -> None:
        Deadline = RoutingDeadline(StartedAt=1.0, ExpiresAt=2.0)

        Reserved = ChooseRepeatedWorkTransition(False, Deadline)
        Unreserved = ChooseRepeatedWorkTransition(
            Reserved.SkipStrictPortalReservation,
            Reserved.Deadline,
        )

        self.assertEqual(Reserved.Action, "TryUnreservedPortals")
        self.assertTrue(Reserved.SkipStrictPortalReservation)
        self.assertIs(Reserved.Deadline, Deadline)
        self.assertEqual(Unreserved.Action, "Terminate")
        self.assertIs(Unreserved.Deadline, Deadline)

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

    def testCandidateWithSupportUnderItsOwnWireIsRejected(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Region = Graph.BuildRegion((10, 14, 1, 4, -2, 4))
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Root = (14, 1, 2)
        Target = (10, 1, -1)
        Profile = NetRoutingProfile(
            Signal="B",
            Root=Root,
            Targets=(Target,),
            Span=6,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root, (14, 1, 3), (14, 1, 4)),
            TargetAccessPaths={Target: (Target,)},
        )
        SourcePortal = self.BuildPortal("B", Root, (14, 2, 3))
        TargetPortal = self.BuildPortal("B", Target, Target)
        Rejections = Counter()

        Candidate = _MaterializeCandidate(
            "B",
            Profile,
            SourcePortal,
            (TargetPortal,),
            frozenset(),
            1,
            "X",
            2,
            0,
            [
                Root,
                (14, 1, 3),
                (14, 1, 4),
                (14, 2, 3),
                (13, 3, 2),
                (12, 3, 1),
                (11, 2, 0),
                Target,
            ],
            Region,
            Resources,
            DefaultRedstoneRoutingTechnology,
            1,
            RejectionCounts=Rejections,
        )

        self.assertIsNone(Candidate)
        self.assertEqual(Rejections["SelfClaimConflict"], 1)

    def testPortalStartsRemainAnchoredToGraphAccessAndReachRoutingLayer(self) -> None:
        Graph, Region, Context = self.BuildGraph()
        AccessPath = ((-1, 1, 0), (0, 1, 0))
        Starts = SelectGraphAccessStarts(AccessPath, Region.Nodes)
        RoutingTarget = (1, 2, 0)

        self.assertEqual(Starts, ((0, 1, 0),))
        self.assertNotIn((4, 2, 0), Starts)

        Values = Context.GeneratePortalCandidates(
            list(Starts),
            [RoutingTarget],
            sorted(Region.Nodes),
            2,
            8,
            1_000,
        )

        self.assertEqual(len(Values), 1)
        PortalPath = tuple(Values[0].Path)
        self.assertEqual(PortalPath[0], Starts[0])
        self.assertEqual(PortalPath[-1], RoutingTarget)
        self.assertTrue(all(
            Graph.BuildPrimitive(First, Second) is not None
            for First, Second in zip(PortalPath, PortalPath[1:])
        ))

        TargetPortal = PinAccessPortal(
            PortalId="A:access-to-layer",
            Signal="A",
            Terminal=AccessPath[0],
            Layer=0,
            Path=PortalPath,
            Edges=frozenset(),
            Claims=RoutingResourceClaims(),
            Length=len(PortalPath),
            BendCount=0,
            ViaCount=1,
            Cost=len(PortalPath),
        )
        TargetChain = (
            *_BuildTargetPortalBranches((TargetPortal,))[0],
            *reversed(AccessPath[:-1]),
        )
        self.assertTrue(all(
            Graph.BuildPrimitive(First, Second) is not None
            for First, Second in zip(TargetChain, TargetChain[1:])
        ))

    def testBatchedPortalGenerationPreservesRequestOrder(self) -> None:
        _Graph, Region, Context = self.BuildGraph()
        AllowedNodes = sorted(Region.Nodes)
        Requests = [
            ([(0, 1, 0)], [Target], AllowedNodes, 1, 8, 1_000)
            for Target in ((3, 1, 0), (2, 1, 0), (1, 1, 0))
        ]
        First = Context.GeneratePortalCandidateBatches(Requests)
        Second = Context.GeneratePortalCandidateBatches(Requests)
        self.assertEqual(
            [Batch[0].Target for Batch in First],
            [(3, 1, 0), (2, 1, 0), (1, 1, 0)],
        )
        self.assertEqual(
            [[Value.Path for Value in Batch] for Batch in First],
            [[Value.Path for Value in Batch] for Batch in Second],
        )

    def testBatchedRouteTreesPreserveRequestOrder(self) -> None:
        _Graph, _Region, Context = self.BuildGraph()
        Columns = [(X, 0) for X in range(5)]
        Requests = [
            (
                [(0, 1, 0)],
                [[Target]],
                Columns,
                [(0, 1, 0), Target],
                [],
                [],
                1,
                0,
                0,
                0,
                1_000,
            )
            for Target in ((4, 1, 0), (2, 1, 0), (3, 1, 0))
        ]
        First = Context.GenerateRouteTrees(Requests)
        Second = Context.GenerateRouteTrees(Requests)
        self.assertEqual(First, Second)
        self.assertTrue(all(
            Target in Tree
            for Target, Tree in zip(
                ((4, 1, 0), (2, 1, 0), (3, 1, 0)),
                First,
            )
        ))

    def testRouteTreeTargetsSelectedPortalOuterEndpoint(self) -> None:
        _Graph, Region, Context = self.BuildGraph()
        Portal = PinAccessPortal(
            PortalId="A:target-portal",
            Signal="A",
            Terminal=(1, 1, 0),
            Layer=0,
            Path=((3, 1, 0), (4, 1, 0)),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(),
            Length=2,
            BendCount=0,
            ViaCount=0,
            Cost=2,
        )
        Branches = _BuildTargetPortalBranches((Portal,))
        RawTargetAccessBranches = [[(1, 1, 0), (2, 1, 0)]]
        CompleteBranches = _BuildTargetPortalBranches(
            (Portal,),
            (((1, 1, 0), (2, 1, 0)),),
        )

        self.assertEqual(Branches, [[(4, 1, 0), (3, 1, 0)]])
        self.assertEqual(
            CompleteBranches,
            [[(4, 1, 0), (3, 1, 0), (2, 1, 0), (1, 1, 0)]],
        )
        self.assertEqual(Branches[0][0], Portal.Path[-1])
        self.assertNotEqual(Branches, RawTargetAccessBranches)

        Tree = Context.GenerateRouteTree(
            [(0, 1, 0)],
            Branches,
            sorted(Region.Nodes),
            [(Index, 0) for Index in range(5)],
            1,
            0,
            0,
            0,
            1_000,
        )
        RawTree = Context.GenerateRouteTree(
            [(0, 1, 0)],
            RawTargetAccessBranches,
            sorted(Region.Nodes),
            [(Index, 0) for Index in range(5)],
            1,
            0,
            0,
            0,
            1_000,
        )
        self.assertIn(Portal.Path[-1], Tree)
        self.assertNotIn(Portal.Path[-1], RawTree)

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

    def testBoundedRustAssignmentStartsDeadlineBeforePayloadConversion(self) -> None:
        class SlowLargeIndexSequence:
            def __init__(self) -> None:
                self.AccessCount = 0

            def __len__(self) -> int:
                return 128

            def __getitem__(self, Index: int) -> int:
                if Index >= len(self):
                    raise IndexError(Index)
                self.AccessCount += 1
                sleep(0.01)
                return Index

        _Graph, _Region, Context = self.BuildGraph()
        SlowWireClaims = SlowLargeIndexSequence()
        CandidateValues = [(
            "A",
            "large-payload",
            SlowWireClaims,
            [],
            [],
            [],
            1,
            1,
            1,
            0,
            0,
        )]

        Started = monotonic()
        Result = Context.PlanAuthoritativeRoutesBounded(
            CandidateValues,
            128,
            64,
            0,
        )
        Elapsed = monotonic() - Started

        self.assertFalse(Result.Success)
        self.assertTrue(Result.DeadlineExceeded)
        self.assertFalse(Result.BudgetExhausted)
        self.assertEqual(Result.CompletedWork, 0)
        self.assertEqual(SlowWireClaims.AccessCount, 0)
        self.assertLess(Elapsed, 1.0)

        InterruptibleSlowClaims = SlowLargeIndexSequence()
        CandidateValues[0] = (
            *CandidateValues[0][:2],
            InterruptibleSlowClaims,
            *CandidateValues[0][3:],
        )
        Started = monotonic()
        Result = Context.PlanAuthoritativeRoutesBounded(
            CandidateValues,
            128,
            64,
            1,
        )
        Elapsed = monotonic() - Started

        self.assertTrue(Result.DeadlineExceeded)
        self.assertLess(InterruptibleSlowClaims.AccessCount, 128)
        self.assertLess(Elapsed, 1.0)

    def testPartialLocalBaseOwnerAffectsRustAssignment(self) -> None:
        _Graph, _Region, Context = self.BuildGraph()
        PartialClaims = RoutingResourceClaims(
            WireCells=frozenset({(1, 1, 0)}),
            ElectricalCells=frozenset({
                (0, 1, 0),
                (1, 1, 0),
                (2, 1, 0),
            }),
        )
        PartialClaim = LocalRouteClaim(
            Signal="A",
            ClusterId=0,
            Root=(1, 1, 0),
            ConnectedTargets=(),
            BoundaryNodes=((1, 1, 0),),
            Nodes=frozenset({(1, 1, 0)}),
            Edges=frozenset(),
            Claims=PartialClaims,
        )
        BaseClaims = SelectAuthoritativeBaseClaims((PartialClaim,), False)
        ResourcePositions = tuple(
            (Index, 1, 0) for Index in range(8)
        )
        Indexed = IndexedRoutingResourceGraph(
            ResourcePositions=ResourcePositions,
            PositionIndices={
                Position: Index
                for Index, Position in enumerate(ResourcePositions)
            },
        )
        Wire, Support, Air, Electrical = Indexed.EncodeClaims(
            BaseClaims[0].Claims
        )

        self.assertEqual(BaseClaims, (PartialClaim,))
        self.assertEqual(SelectAuthoritativeBaseClaims((PartialClaim,), True), ())

        Result = Context.PlanAuthoritativeRoutesWithBase(
            [
                ("B", "blocked", [2], [], [], [1, 2, 3], 1, 1, 1, 0, 0),
                ("B", "clear", [6], [], [], [5, 6, 7], 2, 2, 2, 0, 0),
            ],
            [(
                PartialClaim.Signal,
                list(Wire),
                list(Support),
                list(Air),
                list(Electrical),
            )],
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

    def testRustContextAddsSparseRegionsWithoutRebuildingExistingGraph(self) -> None:
        Context = RoutingContext(
            (0, 3, 1, 1, 0, 0),
            (0, 3, 0, 0),
            [(0, 1, 0), (1, 1, 0)],
            [((0, 1, 0), (1, 1, 0))],
        )

        Counts = Context.AddRegion(
            [(1, 1, 0), (2, 1, 0), (3, 1, 0)],
            [
                ((1, 1, 0), (2, 1, 0)),
                ((2, 1, 0), (3, 1, 0)),
            ],
        )
        RepeatedCounts = Context.AddRegion(
            [(2, 1, 0), (3, 1, 0)],
            [((2, 1, 0), (3, 1, 0))],
        )

        self.assertEqual(Counts, (4, 3))
        self.assertEqual(RepeatedCounts, Counts)

    def testNegotiatedNodeCostMovesTreeOffPresentCongestion(self) -> None:
        Start = (0, 1, 0)
        Direct = (1, 1, 0)
        Target = (2, 1, 0)
        Detour = ((0, 1, 1), (1, 1, 1), (2, 1, 1))
        Nodes = [Start, Direct, Target, *Detour]
        Edges = [
            (Start, Direct),
            (Direct, Target),
            (Start, Detour[0]),
            (Detour[0], Detour[1]),
            (Detour[1], Detour[2]),
            (Detour[2], Target),
        ]
        Context = RoutingContext(
            (0, 2, 1, 1, 0, 1),
            (0, 2, 0, 1),
            Nodes,
            Edges,
        )

        Tree = Context.GenerateRouteTreeWithCostsBounded(
            [Start],
            [[Target]],
            Nodes,
            [],
            [],
            [(Direct, 100)],
            1,
            0,
            0,
            0,
            1_000,
            1_000,
        )

        self.assertIsNotNone(Tree)
        self.assertNotIn(Direct, Tree)
        self.assertTrue(set(Detour).issubset(Tree))

    def testNegotiatedInitialRegionUsesOneFullTechnologyTileHalo(self) -> None:
        Bounds = (0, 47, 1, 5, 0, 47)
        TileSize = 4 * DefaultRedstoneRoutingTechnology.TrackPitch
        Tiles = BuildNegotiatedInitialTiles({(18, 18)}, Bounds, TileSize)
        Columns = NegotiatedColumnsForTiles(Tiles, Bounds, TileSize)
        ExactColumns = BuildNegotiatedInitialColumns(
            {(18, 18)}, Bounds, TileSize
        )

        self.assertEqual(TileSize, 12)
        self.assertEqual(
            Tiles,
            frozenset((X, Z) for X in range(3) for Z in range(3)),
        )
        self.assertEqual(min(X for X, _Z in Columns), 0)
        self.assertEqual(max(X for X, _Z in Columns), 35)
        self.assertEqual(min(X for X, _Z in ExactColumns), 6)
        self.assertEqual(max(X for X, _Z in ExactColumns), 30)
        self.assertEqual(min(Z for _X, Z in ExactColumns), 6)
        self.assertEqual(max(Z for _X, Z in ExactColumns), 30)

    def testBuildNegotiatedFallbackGuideColumnsUsesProfileGeometry(self) -> None:
        Profile = NetRoutingProfile(
            Signal="Signal",
            Root=(11, 1, 11),
            Targets=((14, 1, 14),),
            Span=3,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=((10, 1, 10), (11, 1, 10), (11, 1, 11)),
            TargetAccessPaths={
                (14, 1, 14): ((14, 1, 14), (13, 1, 14), (12, 1, 14)),
            },
            Seed=None,
        )
        Columns = BuildNegotiatedFallbackGuideColumns(
            Profile,
            (0, 20, 1, 5, 0, 20),
            [],
        )
        self.assertEqual(
            Columns,
            frozenset({
                (10, 10),
                (11, 10),
                (11, 11),
                (12, 14),
                (13, 14),
                (14, 14),
            }),
        )

    def testNegotiatedRegionExpandsOneSideAndThenIsIdempotent(self) -> None:
        Bounds = (0, 47, 1, 5, 0, 35)
        TileSize = 12
        Initial = frozenset((X, Z) for X in range(3) for Z in range(3))
        Expanded = ExpandNegotiatedTiles(
            Initial,
            "MaximumX",
            Bounds,
            TileSize,
        )
        Repeated = ExpandNegotiatedTiles(
            Expanded,
            "MaximumX",
            Bounds,
            TileSize,
        )

        self.assertEqual(Expanded - Initial, {(3, 0), (3, 1), (3, 2)})
        self.assertEqual(Repeated, Expanded)
        Touches = FindNegotiatedBoundaryTouches(
            {(35, 1, 18)},
            Initial,
            Bounds,
            TileSize,
        )
        self.assertEqual(Touches, {"MaximumX": ((35, 1, 18),)})

    def testNegotiatedBranchRepairRetainsOnlyCleanTargetPath(self) -> None:
        Conflict = RoutingResourceId(
            RoutingResourceKind.Electrical,
            (2, 1, 0),
        )
        Candidate = self.BuildCandidate("Signal", "candidate", (0, 1, 0))
        Candidate = replace(
            Candidate,
            TargetPaths={
                (4, 1, 0): tuple((X, 1, 0) for X in range(5)),
                (0, 1, 4): tuple((0, 1, Z) for Z in range(5)),
            },
            BranchClaims={
                (4, 1, 0): RoutingResourceClaims(
                    ElectricalCells=frozenset({(2, 1, 0)})
                ),
                (0, 1, 4): RoutingResourceClaims(
                    ElectricalCells=frozenset({(0, 1, 2)})
                ),
            },
        )

        State = BuildNegotiatedRouteTreeState(Candidate, {Conflict})

        self.assertEqual(State.PrunedTargets, ((4, 1, 0),))
        self.assertEqual(State.RetainedTargets, ((0, 1, 4),))

    def testNegotiatedBranchRepairPrunesOnlyTailToNearestBranchpoint(self) -> None:
        Conflict = RoutingResourceId(
            RoutingResourceKind.Wire,
            (4, 1, 0),
        )
        Candidate = self.BuildCandidate("Signal", "candidate", (0, 1, 0))
        Candidate = replace(
            Candidate,
            TargetPaths={
                (4, 1, 4): (
                    (0, 1, 0),
                    (1, 1, 0),
                    (2, 1, 0),
                    (3, 1, 0),
                    (4, 1, 0),
                    (4, 1, 1),
                    (4, 1, 2),
                    (4, 1, 3),
                    (4, 1, 4),
                ),
                (4, 1, -4): (
                    (0, 1, 0),
                    (1, 1, 0),
                    (2, 1, 0),
                    (3, 1, 0),
                    (4, 1, 0),
                    (4, 1, -1),
                    (4, 1, -2),
                    (4, 1, -3),
                    (4, 1, -4),
                ),
            },
            BranchClaims={
                (4, 1, 4): RoutingResourceClaims(
                    WireCells=frozenset({
                        (0, 1, 0),
                        (1, 1, 0),
                        (2, 1, 0),
                        (3, 1, 0),
                        (4, 1, 0),
                        (4, 1, 1),
                        (4, 1, 2),
                        (4, 1, 3),
                        (4, 1, 4),
                    })
                ),
                (4, 1, -4): RoutingResourceClaims(
                    WireCells=frozenset({
                        (0, 1, 0),
                        (1, 1, 0),
                        (2, 1, 0),
                        (3, 1, 0),
                        (4, 1, 0),
                        (4, 1, -1),
                        (4, 1, -2),
                        (4, 1, -3),
                        (4, 1, -4),
                    })
                ),
            },
        )

        State = BuildNegotiatedRouteTreeState(Candidate, {Conflict})

        self.assertEqual(State.PrunedTargets, ((4, 1, -4), (4, 1, 4)))
        self.assertEqual(State.RetainedTargets, ())
        self.assertEqual(
            State.PrunedBranchPaths,
            (
                (
                    (0, 1, 0),
                    (1, 1, 0),
                    (2, 1, 0),
                    (3, 1, 0),
                    (4, 1, 0),
                    (4, 1, -1),
                    (4, 1, -2),
                    (4, 1, -3),
                    (4, 1, -4),
                ),
                (
                    (0, 1, 0),
                    (1, 1, 0),
                    (2, 1, 0),
                    (3, 1, 0),
                    (4, 1, 0),
                    (4, 1, 1),
                    (4, 1, 2),
                    (4, 1, 3),
                    (4, 1, 4),
                ),
            ),
        )
        self.assertEqual(
            State.PrunedBranchTailPaths,
            (
                ((4, 1, -1), (4, 1, -2), (4, 1, -3), (4, 1, -4)),
                ((4, 1, 1), (4, 1, 2), (4, 1, 3), (4, 1, 4)),
            ),
        )
        self.assertEqual(
            State.SharedTrunkNodes,
            ((0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0), (4, 1, 0)),
        )

    def testPlanNegotiatedRouteTreesPreservesSeededSignalWhenNotRegenerating(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Region = Graph.BuildRegion((0, 40, 1, 2, 0, 40))
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Context = RoutingContext(
            (0, 40, 1, 2, 0, 40),
            (0, 40, 0, 0),
            sorted(Region.Nodes),
            sorted(Region.Edges),
        )
        Profile = NetRoutingProfile(
            Signal="S1",
            Root=(1, 1, 1),
            Targets=((6, 1, 1),),
            Span=6,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=((1, 1, 1),),
            TargetAccessPaths={(6, 1, 1): ((6, 1, 1),)},
            Seed=None,
        )
        SeedCandidate = self.BuildCandidate("S1", "seed", (1, 1, 1))
        Plan = PlanNegotiatedRouteTrees(
            Context=Context,
            Profiles={"S1": Profile},
            RouteRequestsBySignal={},
            RouteMetadataBySignal={},
            Region=Region,
            ReservedAccess=frozenset(),
            Resources=Resources,
            Technology=DefaultRedstoneRoutingTechnology,
            Policy=DefaultPhysicalDesignPolicy,
            Deadline=RoutingDeadline(
                StartedAt=monotonic(),
                ExpiresAt=monotonic() + 5.0,
            ),
            AdaptiveExpiresAt=monotonic() + 4.0,
            CheckRuntimeBudget=lambda _Name, _Diagnostics: None,
            RegenerateSignals=frozenset(),
            SeedCandidatesBySignal={"S1": (SeedCandidate,)},
        )
        self.assertEqual(Plan.SelectedCandidates["S1"].CandidateId, "seed")

    def testPlanNegotiatedRouteTreesRegenerateForcesReplanWhenNoRequests(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Region = Graph.BuildRegion((0, 40, 1, 2, 0, 40))
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Context = RoutingContext(
            (0, 40, 1, 2, 0, 40),
            (0, 40, 0, 0),
            sorted(Region.Nodes),
            sorted(Region.Edges),
        )
        Profile = NetRoutingProfile(
            Signal="S1",
            Root=(1, 1, 1),
            Targets=((6, 1, 1),),
            Span=6,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=((1, 1, 1),),
            TargetAccessPaths={(6, 1, 1): ((6, 1, 1),)},
            Seed=None,
        )
        SeedCandidate = self.BuildCandidate("S1", "seed", (1, 1, 1))
        with self.assertRaisesRegex(
            RoutingStageError,
            "no legal portal-aware route tree",
        ) as ContextManager:
            PlanNegotiatedRouteTrees(
                Context=Context,
                Profiles={"S1": Profile},
                RouteRequestsBySignal={},
                RouteMetadataBySignal={},
                Region=Region,
                ReservedAccess=frozenset(),
                Resources=Resources,
                Technology=DefaultRedstoneRoutingTechnology,
                Policy=DefaultPhysicalDesignPolicy,
                Deadline=RoutingDeadline(
                    StartedAt=monotonic(),
                    ExpiresAt=monotonic() + 5.0,
                ),
                AdaptiveExpiresAt=monotonic() + 4.0,
                CheckRuntimeBudget=lambda _Name, _Diagnostics: None,
                RegenerateSignals=frozenset({"S1"}),
                SeedCandidatesBySignal={"S1": (SeedCandidate,)},
            )
        self.assertEqual(
            ContextManager.exception.Failure.Reason,
            RoutingFailureReason.NoPinAccessPattern,
        )

    def testNegotiatedPassZeroDetailedSearchUsesStableNativeBatches(self) -> None:
        class DetailedBatchResult:
            def __init__(self, SearchResults):
                self.SearchResults = SearchResults
                self.DeadlineExceeded = False
                self.CompletedWork = len(SearchResults)
                self.TotalWork = len(SearchResults)

        class BatchContext:
            def __init__(self, Context):
                self.Context = Context
                self.BatchCalls = []

            def __getattr__(self, Name):
                return getattr(self.Context, Name)

            def GenerateRouteTreeDetailedBatchBounded(
                self,
                Requests,
                MaximumRuntimeMilliseconds,
            ):
                self.BatchCalls.append((
                    len(Requests),
                    MaximumRuntimeMilliseconds,
                ))
                return DetailedBatchResult([
                    self.Context.GenerateRouteTreeDetailedBounded(
                        *Request,
                        MaximumRuntimeMilliseconds,
                    )
                    for Request in Requests
                ])

        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Region = Graph.BuildRegion((0, 8, 1, 1, 0, 0))
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Root = (1, 1, 0)
        Target = (6, 1, 0)
        Profile = NetRoutingProfile(
            Signal="S1",
            Root=Root,
            Targets=(Target,),
            Span=5,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
            Seed=None,
        )
        SourcePortal = PinAccessPortal(
            "source",
            "S1",
            Root,
            0,
            (Root,),
            frozenset(),
            Graph.BuildRouteClaims((Root,)),
            0,
            0,
            0,
            0,
        )
        TargetPortal = PinAccessPortal(
            "target",
            "S1",
            Target,
            0,
            (Target,),
            frozenset(),
            Graph.BuildRouteClaims((Target,)),
            0,
            0,
            0,
            0,
        )
        Guide = [(X, 0) for X in range(9)]
        Request = (
            [Root],
            [[Target]],
            Guide,
            [Root, Target],
            [],
            Guide,
            1,
            0,
            0,
            0,
            # The initial global candidate budget is intentionally too small
            # for this path. The negotiated planner must retry only this
            # search at the strict per-net limit before declaring a cut.
            1,
        )
        Metadata = (
            SourcePortal,
            (TargetPortal,),
            frozenset(Guide),
            0,
            "X",
            0,
            0,
        )
        Context = BatchContext(RoutingContext(
            (0, 8, 1, 1, 0, 0),
            (0, 8, 0, 0),
            sorted(Region.Nodes),
            sorted(Region.Edges),
        ))
        with patch(
            "Compiler.Routing.AuthoritativePlanner.GetRustRoutingThreadCount",
            return_value=1,
        ):
            Plan = PlanNegotiatedRouteTrees(
                Context=Context,
                Profiles={"S1": Profile},
                RouteRequestsBySignal={"S1": [Request, Request]},
                RouteMetadataBySignal={
                    "S1": [Metadata, (*Metadata[:-1], 1)]
                },
                Region=Region,
                ReservedAccess=frozenset(),
                Resources=Resources,
                Technology=DefaultRedstoneRoutingTechnology,
                Policy=DefaultPhysicalDesignPolicy,
                Deadline=RoutingDeadline(
                    StartedAt=monotonic(),
                    ExpiresAt=monotonic() + 5.0,
                ),
                AdaptiveExpiresAt=monotonic() + 4.0,
                CheckRuntimeBudget=lambda _Name, _Diagnostics: None,
            )

        self.assertEqual([Count for Count, _Time in Context.BatchCalls], [1, 1])
        self.assertTrue(all(
            Time <= DefaultPhysicalDesignPolicy.NegotiatedRouting
            .MaximumRouteTreeRequestMilliseconds
            for _Count, Time in Context.BatchCalls
        ))
        self.assertEqual(
            Plan.Diagnostics["InitialDetailedBatch"]["CompletedWork"],
            2,
        )
        self.assertEqual(
            Plan.Diagnostics["InitialDetailedBatch"]["WorkerCount"],
            1,
        )
        self.assertEqual(
            Plan.Diagnostics["SearchExpansionEscalations"],
            {"S1": DefaultPhysicalDesignPolicy.DetailedRouting
             .StrictBaseExpansions},
        )
        self.assertTrue(any(
            Value["MaximumExpansionCount"]
            == DefaultPhysicalDesignPolicy.DetailedRouting.StrictBaseExpansions
            for Value in Plan.Diagnostics["NativeSearch"]["S1"]
        ))
        self.assertIn("S1", Plan.SelectedCandidates)

    def testDetailedNativeTreeReturnsRepeaterAwareResult(self) -> None:
        Nodes = [(X, 1, 0) for X in range(31)]
        Edges = list(zip(Nodes, Nodes[1:]))
        Context = RoutingContext(
            (0, 30, 1, 1, 0, 0),
            (0, 30, 0, 0),
            Nodes,
            Edges,
        )

        Result = Context.GenerateRouteTreeDetailedBounded(
            [Nodes[0]],
            [[Nodes[-1]]],
            Nodes,
            [],
            [],
            [],
            1,
            0,
            0,
            0,
            True,
            10_000,
            1_000,
        )

        self.assertEqual(Result.Status, "Routed")
        self.assertEqual(len(Result.TargetPaths), 1)
        self.assertEqual(len(Result.RepeaterReservations), 2)
        self.assertEqual(Result.RepeaterRejectedCount, 0)

    def testDetailedNativeTreeTypesImpossibleRepeaterGeometry(self) -> None:
        Nodes = [(0, 1, 0)]
        for Index in range(1, 20):
            Previous = Nodes[-1]
            Nodes.append((
                Previous[0] + (1 if Index % 2 else 0),
                1,
                Previous[2] + (0 if Index % 2 else 1),
            ))
        Context = RoutingContext(
            (0, 10, 1, 1, 0, 10),
            (0, 10, 0, 10),
            Nodes,
            list(zip(Nodes, Nodes[1:])),
        )

        Result = Context.GenerateRouteTreeDetailedBounded(
            [Nodes[0]],
            [[Nodes[-1]]],
            Nodes,
            [],
            [],
            [],
            1,
            0,
            0,
            0,
            True,
            10_000,
            1_000,
        )

        self.assertEqual(Result.Status, "NoPath")
        self.assertGreater(Result.RepeaterRejectedCount, 0)
        self.assertEqual(Result.NoPathReason, "NoRepeater")

    def testDetailedNativeTreeKeepsStrengthAcrossRetainedStarts(self) -> None:
        Nodes = [(X, 1, 0) for X in range(32)]
        Context = RoutingContext(
            (0, 31, 1, 1, 0, 0),
            (0, 31, 0, 0),
            Nodes,
            list(zip(Nodes, Nodes[1:])),
        )

        Result = Context.GenerateRouteTreeDetailedBounded(
            Nodes[:15],
            [[Nodes[-1]]],
            Nodes,
            [],
            [],
            [],
            1,
            0,
            0,
            0,
            True,
            10_000,
            1_000,
        )

        self.assertEqual(Result.Status, "Routed")
        self.assertEqual(Result.TargetPaths[0][1], Nodes)
        self.assertEqual(len(Result.RepeaterReservations), 2)

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

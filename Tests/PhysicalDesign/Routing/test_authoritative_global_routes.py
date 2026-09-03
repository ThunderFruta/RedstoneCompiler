"""Global Routes contracts for authoritative routing."""

from ._authoritative_planner_contracts import *


class AuthoritativeGlobalRoutesTests(AuthoritativePlannerTestBase):
    def testRoutedComponentForeignEscapeFeedbackIsStructurallyScoped(
        self,
    ) -> None:
        for HasTemplate, IsEscape, CandidateCount in (
            (False, True, 0),
            (True, False, 0),
            (True, True, 1),
        ):
            with self.subTest(
                HasTemplate=HasTemplate,
                IsEscape=IsEscape,
                CandidateCount=CandidateCount,
            ):
                self.assertFalse(
                    ShouldRejectRoutedComponentForeignEscape(
                        HasRoutedComponentTemplate=HasTemplate,
                        IsSelectedForeignEscape=IsEscape,
                        CandidateDiversityLevel=8,
                        CandidateCount=CandidateCount,
                    )
                )

    def testFrozenComponentClaimsBecomeExactGlobalWireObstacles(
        self,
    ) -> None:
        Claim = LocalRouteClaim(
            Signal="ComponentNet",
            ClusterId=-1,
            Root=(5, 3, 5),
            ConnectedTargets=(),
            BoundaryNodes=(),
            Nodes=frozenset(((5, 3, 5),)),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(
                WireCells=frozenset(((5, 3, 5),)),
                SupportCells=frozenset(((5, 2, 5),)),
                RequiredAirCells=frozenset(((6, 3, 5),)),
                ElectricalCells=frozenset(((4, 3, 5),)),
            ),
        )

        Obstacles = FrozenComponentBlockedWireNodes(
            "ForeignNet",
            (Claim,),
        )

        self.assertTrue({
            (4, 3, 5),
            (5, 2, 5),
            (5, 3, 5),
            (5, 4, 5),
            (6, 3, 5),
            (6, 4, 5),
        }.issubset(Obstacles))
        self.assertEqual(
            FrozenComponentBlockedWireNodes(
                "ComponentNet",
                (Claim,),
            ),
            frozenset(),
        )
        self.assertEqual(
            ImmutableRoutingClaimsBlockedWireNodes((Claim.Claims,)),
            Obstacles,
        )

    def testPhysicalPortPathsShareOnlyTheirDeclaredAttachment(self) -> None:
        self.assertTrue(PhysicalPortPathsOwnExclusiveSeam(
            ((0, 2, 0), (1, 2, 0), (2, 2, 0)),
            ((2, 2, 0), (3, 2, 0), (4, 2, 0)),
        ))
        self.assertFalse(PhysicalPortPathsOwnExclusiveSeam(
            ((0, 2, 0), (3, 2, 0), (2, 2, 0)),
            ((2, 2, 0), (3, 2, 0), (4, 2, 0)),
        ))
        self.assertFalse(PhysicalPortPathsOwnExclusiveSeam(
            ((0, 2, 0), (1, 2, 0)),
            ((2, 2, 0), (3, 2, 0)),
        ))

    def testSeamOnlyPortLeavesTerminalAccessToLocalCompiler(self) -> None:
        class ResourceGraph:
            @staticmethod
            def BuildRouteClaims(Nodes):
                return RoutingResourceClaims(WireCells=frozenset(Nodes))

        Candidate = SimpleNamespace(
            CandidateFingerprint="candidate",
            Path=((0, 0, 0),),
            Layer=0,
        )
        Port = PhysicalComponentPortReservation(
            Signal="sum",
            Direction="output",
            OwnedTerminals=((0, 0, 0),),
            OwnedTerminalFingerprints=("terminal",),
            OwnedCandidateFingerprints=("candidate",),
            FabricDomainFingerprint="fabric",
            FabricAttachment=(0, 0, 0),
            Attachment=(2, 0, 0),
            LocalPath=((0, 0, 0), (1, 0, 0), (2, 0, 0)),
            GlobalPath=((2, 0, 0), (3, 0, 0)),
            Claims=RoutingResourceClaims(),
            LocalClaims=RoutingResourceClaims(),
            GlobalClaims=RoutingResourceClaims(),
            OwnedAccessCandidates=(Candidate,),
            ReservationFingerprint="candidate-bound",
        )

        SeamOnly = BuildSeamOnlyPhysicalComponentPortReservation(
            Port,
            ResourceGraph(),
        )

        self.assertEqual(SeamOnly.OwnedCandidateFingerprints, ())
        self.assertEqual(SeamOnly.OwnedAccessCandidates, ())
        # Removing the terminal witness must not recreate speculative claims
        # from path geometry; only the already certified seam claims survive.
        self.assertEqual(SeamOnly.LocalClaims, Port.LocalClaims)
        self.assertEqual(SeamOnly.GlobalClaims, Port.GlobalClaims)
        self.assertEqual(SeamOnly.Claims, Port.Claims)
        self.assertNotEqual(
            SeamOnly.ReservationFingerprint,
            Port.ReservationFingerprint,
        )

    def testRoutedComponentNoTreeEvidenceRequiresCompletedWindows(
        self,
    ) -> None:
        History = (
            {
                "AffectedSignals": ["Alpha"],
                "Diagnostics": {
                    "Requests": 8,
                    "RoutedTrees": 0,
                },
            },
            {
                "AffectedSignals": ["Alpha"],
                "Diagnostics": {
                    "Requests": 8,
                    "RoutedTrees": 1,
                },
            },
            {
                "AffectedSignals": ["Beta"],
                "Diagnostics": {
                    "Requests": 8,
                    "RoutedTrees": 0,
                },
            },
            {
                "AffectedSignals": ["Alpha"],
                "Diagnostics": None,
            },
        )

        self.assertEqual(
            CountRoutedComponentGlobalNoTreeAttempts(
                History,
                "Alpha",
            ),
            1,
        )
        self.assertEqual(
            CountRoutedComponentGlobalNoTreeAttempts(
                History,
                "Beta",
            ),
            1,
        )
        self.assertEqual(
            CountRoutedComponentGlobalNoTreeAttempts(History),
            2,
        )

    def testDetachedLocalClaimComponentsBecomeJoinAnchors(
        self,
    ) -> None:
        Profile = SimpleNamespace(
            Root=(0, 1, 0),
            SourceAccessPath=((0, 1, 0),),
            Seed=SimpleNamespace(LocalClaims=(
                SimpleNamespace(
                    Nodes=frozenset({
                        (0, 1, 0),
                        (1, 1, 0),
                    }),
                    BoundaryNodes=((1, 1, 0),),
                ),
                SimpleNamespace(
                    Nodes=frozenset({
                        (5, 1, 0),
                        (6, 1, 0),
                    }),
                    BoundaryNodes=((6, 1, 0),),
                ),
            )),
        )
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Starts, Anchors = PartitionLocalClaimSeedComponents(
            Profile,
            Graph,
        )
        self.assertEqual(
            Starts,
            ((0, 1, 0), (1, 1, 0)),
        )
        self.assertEqual(Anchors, ((6, 1, 0),))

    def testDetachedLocalClaimResourcesBecomeSearchObstacles(
        self,
    ) -> None:
        RootClaim = SimpleNamespace(
            Nodes=frozenset({(0, 1, 0), (1, 1, 0)}),
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(0, 1, 0), (1, 1, 0)}),
            ),
        )
        DetachedClaim = SimpleNamespace(
            Nodes=frozenset({(5, 1, 0), (6, 1, 0)}),
            BoundaryNodes=((6, 1, 0),),
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(5, 1, 0), (6, 1, 0)}),
                SupportCells=frozenset({(5, 0, 0), (6, 0, 0)}),
                RequiredAirCells=frozenset({(5, 2, 0)}),
                ElectricalCells=frozenset({
                    (4, 1, 0),
                    (5, 1, 0),
                    (6, 1, 0),
                    (7, 1, 0),
                }),
            ),
        )
        Profile = SimpleNamespace(
            Seed=SimpleNamespace(
                LocalClaims=(RootClaim, DetachedClaim),
            ),
        )
        self.assertEqual(
            BuildDetachedLocalClaimObstacleNodes(
                Profile,
                RootClaim.Nodes,
                RoutingResourceGraph(
                    ActualBlocks=frozenset(),
                    ElectricalBlocks=frozenset(),
                    SolidBlocks=frozenset(),
                ),
            ),
            frozenset({
                (4, 1, 0),
                (5, 0, 0),
                (6, 0, 0),
                (5, 2, 0),
            }),
        )

    def testInvariantRouteRequestNodePayloadMatchesEagerConstruction(
        self,
    ) -> None:
        FixedRequiredNodes = frozenset({
            (3, 1, 4),
            (1, 0, 0),
            (2, 0, 0),
        })
        PortalNodes = frozenset({
            (2, 0, 0),
            (5, 2, 1),
        })
        BlockedNodeBase = frozenset({
            (0, 0, 0),
            (1, 0, 0),
            (3, 1, 4),
            (4, 1, 0),
            (6, 2, 1),
        })

        Payload = BuildInvariantRouteRequestNodePayload(
            FixedRequiredNodes,
            PortalNodes,
            tuple(sorted(BlockedNodeBase)),
        )
        ExpectedRequiredNodes = FixedRequiredNodes | PortalNodes

        self.assertEqual(
            Payload.RequiredNodeSet,
            ExpectedRequiredNodes,
        )
        self.assertEqual(
            Payload.RequiredNodes,
            tuple(sorted(ExpectedRequiredNodes)),
        )
        self.assertEqual(
            Payload.BlockedNodes,
            tuple(sorted(
                BlockedNodeBase - ExpectedRequiredNodes
            )),
        )

    def testInvariantRouteRequestNodePayloadIsDeterministicAndImmutable(
        self,
    ) -> None:
        Arguments = (
            frozenset({(9, 0, 1), (1, 0, 1)}),
            frozenset({(5, 2, 3), (1, 0, 1)}),
            tuple(sorted({
                (1, 0, 1),
                (2, 0, 1),
                (5, 2, 3),
                (8, 1, 2),
            })),
        )

        First = BuildInvariantRouteRequestNodePayload(*Arguments)
        Second = BuildInvariantRouteRequestNodePayload(*Arguments)

        self.assertEqual(First, Second)
        self.assertIsNot(First, Second)
        FirstNativeBlockedNodes = list(First.BlockedNodes)
        SecondNativeBlockedNodes = list(Second.BlockedNodes)
        FirstNativeBlockedNodes.clear()
        self.assertEqual(
            SecondNativeBlockedNodes,
            list(Second.BlockedNodes),
        )

    def testInvariantRouteRequestGuidePayloadMatchesEagerExpansion(
        self,
    ) -> None:
        Guide = frozenset({
            (1, 1),
            (2, 1),
            (2, 2),
        })
        GuideExpansion = 2
        ExpectedColumns = {
            (GuideX + DeltaX, GuideZ + DeltaZ)
            for GuideX, GuideZ in Guide
            for DeltaX in range(
                -GuideExpansion,
                GuideExpansion + 1,
            )
            for DeltaZ in range(
                -GuideExpansion,
                GuideExpansion + 1,
            )
            if abs(DeltaX) + abs(DeltaZ) <= GuideExpansion
        }

        First = BuildInvariantRouteRequestGuidePayload(
            Guide,
            GuideExpansion,
        )
        Second = BuildInvariantRouteRequestGuidePayload(
            Guide,
            GuideExpansion,
        )

        self.assertEqual(
            First,
            (
                tuple(sorted(ExpectedColumns)),
                tuple(sorted(Guide)),
            ),
        )
        self.assertEqual(First, Second)
        self.assertIsNot(First, Second)

    def testIncrementalPhysicalArcIndexComparesOnlyNewPairs(self) -> None:
        Index = IncrementalPhysicalCandidateArcIndex()
        A1 = SimpleNamespace(Signal="A", CandidateId="a1")
        A2 = SimpleNamespace(Signal="A", CandidateId="a2")
        B1 = SimpleNamespace(Signal="B", CandidateId="b1")
        B2 = SimpleNamespace(Signal="B", CandidateId="b2")
        Checks = []

        def Compatible(First, Second):
            Checks.append((First.CandidateId, Second.CandidateId))
            return True

        self.assertEqual(
            Index.Extend({"A": (A1, A2), "B": (B1,)}, Compatible),
            2,
        )
        self.assertEqual(
            Index.Extend({"A": (A1, A2), "B": (B1, B2)}, Compatible),
            2,
        )
        self.assertEqual(len(Checks), 4)
        self.assertEqual(len(set(Checks)), 4)

    def testPhysicalGlobalArcIndexPersistsOnlyForAssemblyPlanning(
        self,
    ) -> None:
        Resources = SimpleNamespace(
            PhysicalGlobalAssignmentArcIndex=None,
        )

        First = GetPhysicalGlobalAssignmentArcIndex(
            Resources,
            Persistent=True,
        )
        Second = GetPhysicalGlobalAssignmentArcIndex(
            Resources,
            Persistent=True,
        )
        Flat = GetPhysicalGlobalAssignmentArcIndex(
            Resources,
            Persistent=False,
        )

        self.assertIs(First, Second)
        self.assertIsNot(First, Flat)

    def testPersistentPhysicalArcIndexComparesNewCrossPlanPair(
        self,
    ) -> None:
        Index = IncrementalPhysicalCandidateArcIndex()
        A1 = SimpleNamespace(Signal="A", CandidateId="a1")
        A2 = SimpleNamespace(Signal="A", CandidateId="a2")
        B1 = SimpleNamespace(Signal="B", CandidateId="b1")
        B2 = SimpleNamespace(Signal="B", CandidateId="b2")
        Compared = []

        def Compatible(First, Second):
            Compared.append((First.CandidateId, Second.CandidateId))
            return True

        Index.Extend({"A": (A1,), "B": (B1,)}, Compatible)
        Index.Extend({"A": (A2,), "B": (B2,)}, Compatible)
        self.assertEqual(
            Index.Extend({"A": (A1,), "B": (B2,)}, Compatible),
            1,
        )
        self.assertIn(("a1", "b2"), Compared)

    def testIncrementalPhysicalArcIndexPropagatesSupportClosure(
        self,
    ) -> None:
        Index = IncrementalPhysicalCandidateArcIndex()
        Candidates = {
            "A": tuple(
                SimpleNamespace(Signal="A", CandidateId=Value)
                for Value in ("a1", "a2")
            ),
            "B": tuple(
                SimpleNamespace(Signal="B", CandidateId=Value)
                for Value in ("b1", "b2")
            ),
            "C": (
                SimpleNamespace(Signal="C", CandidateId="c1"),
            ),
        }
        CompatiblePairs = {
            frozenset(("a1", "b1")),
            frozenset(("a2", "b2")),
            frozenset(("a1", "c1")),
            frozenset(("a2", "c1")),
            frozenset(("b1", "c1")),
        }
        Index.Extend(
            Candidates,
            lambda First, Second: frozenset((
                First.CandidateId,
                Second.CandidateId,
            )) in CompatiblePairs,
        )

        Retained, PruneCount = Index.Propagate(Candidates)

        self.assertEqual(
            {
                Signal: [Value.CandidateId for Value in Values]
                for Signal, Values in Retained.items()
            },
            {"A": ["a1"], "B": ["b1"], "C": ["c1"]},
        )
        self.assertEqual(PruneCount, 2)

    def testPhysicalRouteRequestConsumesReservedCorridor(self) -> None:
        ReservedGuide = frozenset({
            (0, 0),
            (0, 4),
            (7, 4),
            (7, 0),
        })

        Selected = SelectAuthoritativeRouteRequestGuide(
            ((0, 0), (7, 0)),
            "X",
            0,
            ReservedPhysicalGuide=ReservedGuide,
        )

        self.assertEqual(Selected, ReservedGuide)
        self.assertIn((0, 4), Selected)
        self.assertNotEqual(
            Selected,
            frozenset((
                (0, 0),
                (1, 0),
                (2, 0),
                (3, 0),
                (4, 0),
                (5, 0),
                (6, 0),
                (7, 0),
            )),
        )

    def testPhysicalSeamOrderAlternatesOutwardBanks(self) -> None:
        Seams = (
            SimpleNamespace(Name="south-0", GlobalPath=((0, 0, 0), (0, 0, -1)), Cost=0),
            SimpleNamespace(Name="south-1", GlobalPath=((1, 0, 0), (1, 0, -1)), Cost=1),
            SimpleNamespace(Name="south-2", GlobalPath=((2, 0, 0), (2, 0, -1)), Cost=2),
            SimpleNamespace(Name="east-0", GlobalPath=((0, 0, 0), (1, 0, 0)), Cost=3),
            SimpleNamespace(Name="north-0", GlobalPath=((0, 0, 0), (0, 0, 1)), Cost=4),
        )

        Ordered = InterleavePhysicalPortSeamsByEgressClass(
            Seams,
            BaseKey=lambda Value: (Value.Cost,),
        )

        self.assertEqual(
            tuple(Value.Name for Value in Ordered),
            (
                "south-0",
                "east-0",
                "north-0",
                "south-1",
                "south-2",
            ),
        )

    def testTopologyCutEpochAdvancesWithoutMaterializedSibling(
        self,
    ) -> None:
        Arguments = {
            "PlacementWasRelocated": True,
            "TopologyRequiresJointPortfolio": True,
            "HasTopologyCutConstraintEvidence": True,
            "CandidateDiversityLevel": 0,
            "ReservationVariant": 0,
            "LaneDiversityLevel": 0,
            "SkipStrictPortalReservation": False,
            "RoutedTreeCount": 0,
            "MaterializedCandidateCount": 0,
        }
        self.assertTrue(
            ShouldAdvanceTopologyCutEpochOnCandidateStarvation(
                **Arguments
            )
        )
        for Override in (
            {"PlacementWasRelocated": False},
            {"TopologyRequiresJointPortfolio": False},
            {"HasTopologyCutConstraintEvidence": False},
            {"CandidateDiversityLevel": 1},
            {"ReservationVariant": 1},
            {"LaneDiversityLevel": 1},
            {"SkipStrictPortalReservation": True},
            {"RoutedTreeCount": 1},
            {"MaterializedCandidateCount": 1},
        ):
            self.assertFalse(
                ShouldAdvanceTopologyCutEpochOnCandidateStarvation(
                    **{
                        **Arguments,
                        **Override,
                    }
                )
            )

    def testCumulativeJointConstraintMaturityRequiresBothCutKinds(
        self,
    ) -> None:
        def Diagnostics(
            HigherOrderSignalSets,
            PairwiseConflictEdges,
        ):
            return {
                "__JointClusterPlacement__": {
                    "AssignmentConstraints": {
                        "HigherOrderSignalSets": HigherOrderSignalSets,
                        "PairwiseConflictEdges": PairwiseConflictEdges,
                    },
                },
            }

        HigherOnly = Diagnostics(
            [["A", "B", "Carry"]],
            [],
        )
        PairOnly = Diagnostics(
            [],
            [["A", "B"]],
        )
        Both = Diagnostics(
            [["A", "B", "Carry"]],
            [["A", "B"]],
        )

        self.assertEqual(
            CountJointAssignmentConstraintKinds(HigherOnly),
            (1, 0),
        )
        self.assertEqual(
            CountJointAssignmentConstraintKinds(PairOnly),
            (0, 1),
        )
        self.assertFalse(
            HasCumulativeJointAssignmentConstraintMaturity(HigherOnly)
        )
        self.assertFalse(
            HasCumulativeJointAssignmentConstraintMaturity(PairOnly)
        )
        self.assertTrue(
            HasCumulativeJointAssignmentConstraintMaturity(Both)
        )
        ActiveOverridesGenerated = {
            "__JointClusterPlacement__": {
                "AssignmentConstraints": {
                    "HigherOrderSignalSets": [["Generated", "Only"]],
                    "PairwiseConflictEdges": [],
                },
                "ActiveAssignmentConstraints": {
                    "HigherOrderSignalSets": [["Live", "Higher"]],
                    "PairwiseConflictEdges": [["Live", "Pair"]],
                },
            },
        }
        self.assertEqual(
            CountJointAssignmentConstraintKinds(
                ActiveOverridesGenerated
            ),
            (1, 1),
        )
        self.assertEqual(
            SelectJointHigherOrderConstraintSignals(
                ActiveOverridesGenerated
            ),
            frozenset({"Live", "Higher"}),
        )
        self.assertEqual(
            SelectJointPairwiseConstraintSignals(
                ActiveOverridesGenerated
            ),
            frozenset({"Live", "Pair"}),
        )

    def testCutScopedFixedLegalityContinuationIsExactPortfolioOnly(
        self,
    ) -> None:
        Arguments = {
            "PlacementWasRelocated": True,
            "ExactLegalRetainedJointStateCount": 5,
            "HasCumulativeAssignmentConstraints": True,
            "CandidateDiversityLevel": 0,
            "ReservationVariant": 0,
            "LaneDiversityLevel": 0,
            "SkipStrictPortalReservation": False,
            "Signal": "CutSignal",
            "JointAssignmentConstraintSignals": frozenset({
                "CutSignal",
                "OtherSignal",
            }),
            "RoutedTreeCount": 8,
            "MaterializedCandidateCount": 0,
            "AllRoutedTreesRejectedByFixedLegality": True,
            "DeferredRequestCount": 72,
        }
        self.assertTrue(
            ShouldContinueCutScopedFixedLegalityWindow(**Arguments)
        )
        for Override in (
            {"PlacementWasRelocated": False},
            {"ExactLegalRetainedJointStateCount": 1},
            {"HasCumulativeAssignmentConstraints": False},
            {"CandidateDiversityLevel": 1},
            {"ReservationVariant": 1},
            {"LaneDiversityLevel": 1},
            {"SkipStrictPortalReservation": True},
            {"Signal": "UnreportedSignal"},
            {"RoutedTreeCount": 0},
            {"MaterializedCandidateCount": 1},
            {"AllRoutedTreesRejectedByFixedLegality": False},
            {"DeferredRequestCount": 0},
        ):
            with self.subTest(Override=Override):
                self.assertFalse(
                    ShouldContinueCutScopedFixedLegalityWindow(
                        **{
                            **Arguments,
                            **Override,
                        }
                    )
                )
        self.assertTrue(
            ShouldContinueCutScopedFixedLegalityWindow(
                **{
                    **Arguments,
                    "Signal": "UnreportedSignal",
                    "HasCompleteClusterBoundaryLease": True,
                }
            )
        )

    def testCumulativeJointConstraintMaturityIsRenameAndOrderIndependent(
        self,
    ) -> None:
        Original = {
            "__JointClusterPlacement__": {
                "AssignmentConstraints": {
                    "HigherOrderSignalSets": [
                        ["A", "B", "Carry"],
                        ["Generate", "Propagate", "Carry"],
                    ],
                    "PairwiseConflictEdges": [
                        ["A", "B"],
                        ["Generate", "Propagate"],
                    ],
                },
            },
        }
        RenamedAndReordered = {
            "__JointClusterPlacement__": {
                "AssignmentConstraints": {
                    "HigherOrderSignalSets": [
                        ["Signal91", "Signal17", "Signal4"],
                        ["Signal63", "Signal22", "Signal5"],
                    ],
                    "PairwiseConflictEdges": [
                        ["Signal63", "Signal22"],
                        ["Signal91", "Signal17"],
                    ],
                },
            },
        }

        self.assertEqual(
            CountJointAssignmentConstraintKinds(Original),
            CountJointAssignmentConstraintKinds(RenamedAndReordered),
        )
        self.assertEqual(
            HasCumulativeJointAssignmentConstraintMaturity(Original),
            HasCumulativeJointAssignmentConstraintMaturity(
                RenamedAndReordered
            ),
        )
        self.assertEqual(
            SelectJointHigherOrderConstraintSignals(
                RenamedAndReordered
            ),
            frozenset({
                "Signal4",
                "Signal5",
                "Signal17",
                "Signal22",
                "Signal63",
                "Signal91",
            }),
        )
        self.assertTrue(
            HasCumulativeJointAssignmentConstraintMaturity(
                RenamedAndReordered
            )
        )
        self.assertEqual(
            ShouldCapMatureCumulativeJointPortfolio(
                True,
                6,
                HasCumulativeJointAssignmentConstraintMaturity(Original),
            ),
            ShouldCapMatureCumulativeJointPortfolio(
                True,
                6,
                HasCumulativeJointAssignmentConstraintMaturity(
                    RenamedAndReordered
                ),
            ),
        )

    def testMaturePortfolioCapsRequireEveryStructuralGate(
        self,
    ) -> None:
        self.assertTrue(
            ShouldCapMatureCumulativeJointPortfolio(
                True,
                2,
                True,
            )
        )
        self.assertFalse(
            ShouldCapMatureCumulativeJointPortfolio(
                False,
                2,
                True,
            )
        )
        self.assertTrue(
            ShouldCapMatureCumulativeJointPortfolio(
                True,
                1,
                True,
            )
        )
        self.assertFalse(
            ShouldCapMatureCumulativeJointPortfolio(
                True,
                2,
                False,
            )
        )

    def testTopologyPressurePortfolioStagingIncludesFinalGeometry(
        self,
    ) -> None:
        self.assertTrue(
            ShouldStageTopologyPressureJointPortfolio(6, True)
        )
        self.assertTrue(
            ShouldStageTopologyPressureJointPortfolio(1, True)
        )
        self.assertTrue(
            ShouldStageTopologyPressureJointPortfolio(0, True)
        )
        self.assertFalse(
            ShouldStageTopologyPressureJointPortfolio(6, False)
        )

    def testPhysicalAssemblyPlanningForcesSparseInitialScheduler(
        self,
    ) -> None:
        self.assertTrue(
            ShouldUseMatureStagedInitialCandidateScheduler(
                ApplyMaturePortfolioSearchCaps=False,
                CandidateDiversityLevel=0,
                ReservationVariant=0,
                LaneDiversityLevel=0,
                SkipStrictPortalReservation=False,
                RetainedCandidateCachePresent=False,
                PriorCandidateCachePresent=False,
                ForcePhysicalAssemblyPlanning=True,
            )
        )

    def testMaturePortfolioExactFloorCapPreservesSmallerUserFloor(
        self,
    ) -> None:
        self.assertEqual(
            SelectMaturePortfolioExactInitialRequestFloor(32, True),
            8,
        )
        self.assertEqual(
            SelectMaturePortfolioExactInitialRequestFloor(16, True),
            8,
        )
        self.assertEqual(
            SelectMaturePortfolioExactInitialRequestFloor(8, True),
            8,
        )
        self.assertEqual(
            SelectMaturePortfolioExactInitialRequestFloor(0, True),
            0,
        )
        self.assertEqual(
            SelectMaturePortfolioExactInitialRequestFloor(32, False),
            32,
        )
        with self.assertRaises(ValueError):
            SelectMaturePortfolioExactInitialRequestFloor(-1, True)
        with self.assertRaises(ValueError):
            SelectMaturePortfolioExactInitialRequestFloor(
                32,
                True,
                MaximumMaturePortfolioRequestFloor=0,
            )

    def testMatureStagedSchedulerRequiresFreshInitialPortfolio(
        self,
    ) -> None:
        Arguments = {
            "ApplyMaturePortfolioSearchCaps": True,
            "CandidateDiversityLevel": 0,
            "ReservationVariant": 0,
            "LaneDiversityLevel": 0,
            "SkipStrictPortalReservation": False,
            "RetainedCandidateCachePresent": False,
            "PriorCandidateCachePresent": False,
        }
        self.assertTrue(
            ShouldUseMatureStagedInitialCandidateScheduler(**Arguments)
        )
        for Key, Value in (
            ("ApplyMaturePortfolioSearchCaps", False),
            ("CandidateDiversityLevel", 1),
            ("ReservationVariant", 1),
            ("LaneDiversityLevel", 1),
            ("SkipStrictPortalReservation", True),
            ("RetainedCandidateCachePresent", True),
            ("PriorCandidateCachePresent", True),
        ):
            with self.subTest(Key=Key):
                self.assertFalse(
                    ShouldUseMatureStagedInitialCandidateScheduler(
                        **{
                            **Arguments,
                            Key: Value,
                        }
                    )
                )

    def testNegotiatedOffenderHaloUsesExistingLaneLadder(self) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.RepeaterAccessInfeasible,
            Stage="NegotiatedDetailedRouting",
            AffectedNets=("SignalA",),
            RepairActions=(
                "RelocateProducerConsumerClusters",
                "ExpandOffenderHalo",
            ),
        )
        Arguments = {
            "Failure": Failure,
            "AdaptiveRoutingEnabled": True,
            "LaneDiversityLevel": 0,
            "MaximumLaneDiversityEscalations": 4,
        }
        self.assertTrue(
            ShouldExpandNegotiatedOffenderHalo(**Arguments)
        )
        self.assertFalse(
            ShouldExpandNegotiatedOffenderHalo(
                **Arguments,
                TopologyRequiresJointPortfolio=True,
            )
        )
        for Key, Value in (
            ("AdaptiveRoutingEnabled", False),
            ("LaneDiversityLevel", 3),
        ):
            with self.subTest(Key=Key):
                self.assertFalse(
                    ShouldExpandNegotiatedOffenderHalo(
                        **{
                            **Arguments,
                            Key: Value,
                        }
                    )
                )
        self.assertFalse(ShouldExpandNegotiatedOffenderHalo(
            replace(Failure, RepairActions=("RelocateAffectedClusters",)),
            True,
            0,
            4,
        ))

    def testNegotiatedOffenderHaloEscalationIsNonRecursive(self) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.RepeaterAccessInfeasible,
            Stage="NegotiatedDetailedRouting",
            AffectedNets=("SignalA",),
            RepairActions=("ExpandOffenderHalo",),
            Detail="bounded sparse-region failure",
            Diagnostics={
                "ConflictGraph": {"ConflictSignals": ["SignalA"]},
                "RequestCount": 12,
                "PortalCacheGeneratedSignals": ["SignalA", "SignalB"],
                "EscalationHistory": [{"Diagnostics": {"Large": "payload"}}],
                "InitialDetailedBatch": {
                    "Enabled": True,
                    "RequestCount": 4,
                    "CompletedWork": 3,
                    "PerSignalRuntimeMilliseconds": {"SignalA": 500},
                },
            },
        )

        Escalation = BuildNegotiatedOffenderHaloEscalation(
            Failure,
            0,
            3,
        )

        self.assertEqual(Escalation["FromLaneDiversityLevel"], 0)
        self.assertEqual(Escalation["ToLaneDiversityLevel"], 3)
        Snapshot = Escalation["Failure"]
        self.assertEqual(Snapshot["Reason"], "RepeaterAccessInfeasible")
        SnapshotDiagnostics = Snapshot["Diagnostics"]
        self.assertNotIn("EscalationHistory", SnapshotDiagnostics)
        self.assertNotIn(
            "PortalCacheGeneratedSignals",
            SnapshotDiagnostics,
        )
        self.assertEqual(
            SnapshotDiagnostics["InitialDetailedBatch"],
            {
                "Enabled": True,
                "RequestCount": 4,
                "CompletedWork": 3,
            },
        )

    def testNegotiatedExpandedRequestsPreserveProvedStrictFloor(
        self,
    ) -> None:
        self.assertIsNone(
            SelectNegotiatedExpandedRequestMinimumExpansionCount(
                False,
                False,
                90_000,
            )
        )
        self.assertEqual(
            SelectNegotiatedExpandedRequestMinimumExpansionCount(
                True,
                False,
                90_000,
            ),
            90_000,
        )
        self.assertEqual(
            SelectNegotiatedExpandedRequestMinimumExpansionCount(
                False,
                True,
                90_000,
            ),
            90_000,
        )

    def testExactCompletionSeparatesQuickDiscoveryFromStrictProof(
        self,
    ) -> None:
        Requests, Mode = SelectExactAssignmentCompletionRequestBatch(
            [0, 1, 2, 3],
            {
                0: (0, 0, 0),
                1: (1, 2, 1),
                2: (0, 0, 2),
                3: (2, 4, 3),
            },
            8,
            True,
        )

        self.assertEqual(Requests, (0, 1, 2, 3))
        self.assertEqual(Mode, "quick-discovery")

    def testExactCompletionRunsStrictProofAfterSameCutDiscovery(
        self,
    ) -> None:
        Requests, Mode = SelectExactAssignmentCompletionRequestBatch(
            [0, 1, 2, 3],
            {
                0: (0, 0, 0),
                1: (1, 2, 1),
                2: (0, 0, 2),
                3: (2, 4, 3),
            },
            2,
            True,
            QuickDiscoveryEnabled=False,
        )

        self.assertEqual(Requests, (0, 2))
        self.assertEqual(Mode, "strict-proof")

    def testExactCompletionUsesOnlyReturnPathReserveForPortfolioCut(
        self,
    ) -> None:
        self.assertEqual(
            SelectExactAssignmentCompletionReserveMilliseconds(
                True,
                162,
                False,
                60.0,
            ),
            1_000,
        )

    def testExactCompletionContinuesOnlyDistinctProgressingFrontier(
        self,
    ) -> None:
        DistinctCuts = (
            ("A", "Anchor"),
            ("B", "Anchor"),
            ("C", "D"),
            ("E", "F"),
        )
        self.assertTrue(
            ShouldContinueDistinctExactCutFrontier(
                True,
                DistinctCuts,
                4,
            )
        )
        self.assertFalse(
            ShouldContinueDistinctExactCutFrontier(
                True,
                (*DistinctCuts[:3], DistinctCuts[0]),
                4,
            )
        )
        self.assertFalse(
            ShouldContinueDistinctExactCutFrontier(
                False,
                DistinctCuts,
                4,
            )
        )

    def testExactCompletionSharesOneBatchAcrossPairCut(
        self,
    ) -> None:
        self.assertEqual(
            SelectExactAssignmentCompletionCutWideRequests(
                ["Scarce", "Anchor"],
                {
                    "Scarce": list(range(8)),
                    "Anchor": list(range(8)),
                },
                8,
            ),
            (
                ("Scarce", 0),
                ("Anchor", 0),
                ("Scarce", 1),
                ("Anchor", 1),
                ("Scarce", 2),
                ("Anchor", 2),
                ("Scarce", 3),
                ("Anchor", 3),
            ),
        )

    def testExactCompletionRedistributesUnusedCutQuota(
        self,
    ) -> None:
        self.assertEqual(
            SelectExactAssignmentCompletionCutWideRequests(
                ["Short", "Long"],
                {
                    "Short": [0, 1],
                    "Long": list(range(10, 18)),
                },
                8,
            ),
            (
                ("Short", 0),
                ("Long", 10),
                ("Short", 1),
                ("Long", 11),
                ("Long", 12),
                ("Long", 13),
                ("Long", 14),
                ("Long", 15),
            ),
        )

    def testExactCompletionBoundsHigherOrderCut(
        self,
    ) -> None:
        Signals = [f"Signal{Index}" for Index in range(9)]
        Selected = SelectExactAssignmentCompletionCutWideRequests(
            Signals,
            {
                Signal: [Index, Index + 100]
                for Index, Signal in enumerate(Signals)
            },
            8,
        )

        self.assertEqual(len(Selected), 8)
        self.assertEqual(
            tuple(Signal for Signal, _RequestIndex in Selected),
            tuple(Signals[:8]),
        )

    def testExactCompletionDoesNotRetryRequestWhenCutChanges(
        self,
    ) -> None:
        Attempts = {
            ("Anchor", 0, "quick-discovery"),
            ("Anchor", 1, "quick-discovery"),
            ("Anchor", 0, "strict-proof"),
        }

        self.assertEqual(
            SelectPendingExactAssignmentCompletionRequestIndices(
                "Anchor",
                4,
                "quick-discovery",
                Attempts,
            ),
            {2, 3},
        )
        self.assertEqual(
            SelectPendingExactAssignmentCompletionRequestIndices(
                "Anchor",
                4,
                "strict-proof",
                Attempts,
            ),
            {1, 2, 3},
        )

    def testExactCompletionDoesNotMixDiscoveryAndStrictProof(
        self,
    ) -> None:
        Scores = {
            0: (0, 0, 0),
            1: (1, 2, 1),
            2: (0, 0, 2),
        }
        DiscoveryRequests, DiscoveryMode = (
            SelectExactAssignmentCompletionRequestBatch(
                [0, 1, 2],
                Scores,
                8,
                True,
                QuickDiscoveryEnabled=True,
            )
        )
        StrictRequests, StrictMode = (
            SelectExactAssignmentCompletionRequestBatch(
                [0, 1, 2],
                Scores,
                8,
                True,
                QuickDiscoveryEnabled=False,
            )
        )

        self.assertEqual(
            (DiscoveryRequests, DiscoveryMode),
            ((0, 1, 2), "quick-discovery"),
        )
        self.assertEqual(
            (StrictRequests, StrictMode),
            ((0, 2), "strict-proof"),
        )

    def testOnlyMatureFeedbackCanAdvanceAnExhaustedStagedCandidate(
        self,
    ) -> None:
        self.assertFalse(
            MayAdvanceStagedCandidateOnExhaustion(
                False,
                2,
                "CarryOut",
                frozenset(),
            )
        )
        self.assertTrue(
            MayAdvanceStagedCandidateOnExhaustion(
                True,
                2,
                "CarryOut",
                frozenset({"CarryIn"}),
            )
        )
        self.assertFalse(
            MayAdvanceStagedCandidateOnExhaustion(
                True,
                2,
                "CarryIn",
                frozenset({"CarryIn"}),
            )
        )
        self.assertFalse(
            MayAdvanceStagedCandidateOnExhaustion(
                True,
                1,
                "CarryOut",
                frozenset({"CarryIn"}),
            )
        )

    def testMatureStagedSchedulerProvesExactWindowBeforeStarvation(
        self,
    ) -> None:
        Requests = {
            "A": [("A", 0), ("A", 1), ("A", 2)],
            "B": [("B", 0), ("B", 1), ("B", 2)],
        }
        Calls = []

        def GenerateBatch(Batch):
            Calls.append(list(Batch))
            return [
                "tree-B0" if Request == ("B", 0) else None
                for Request in Batch
            ]

        Result = GenerateStagedInitialRouteTrees(
            ("A", "B"),
            Requests,
            GenerateBatch,
            lambda Signal: Signal == "A",
        )

        self.assertFalse(Result.FullPoolGenerated)
        self.assertEqual(Result.ExhaustedSignals, ("A",))
        self.assertEqual(Result.ExecutedRequestCount, 4)
        self.assertEqual(Result.PlannedRequestCount, 6)
        self.assertEqual(Result.BatchCount, 3)
        self.assertEqual(Result.RouteTrees, ())
        self.assertEqual(
            dict(Result.ExecutedRequestCountsBySignal),
            {"A": 3, "B": 1},
        )
        self.assertEqual(
            dict(Result.FirstSuccessfulRequestIndicesBySignal),
            {"B": 0},
        )
        self.assertEqual(
            Calls,
            [
                [("A", 0), ("B", 0)],
                [("A", 1)],
                [("A", 2)],
            ],
        )

    def testCoordinatedCandidateExpansionIsSignalScopedAndStrictlyCapped(
        self,
    ) -> None:
        self.assertEqual(
            SelectCoordinatedCandidateExpansionLimit(
                12_000,
                90_000,
                2,
                1,
                True,
            ),
            24_000,
        )
        self.assertEqual(
            SelectCoordinatedCandidateExpansionLimit(
                60_000,
                90_000,
                2,
                1,
                True,
            ),
            90_000,
        )

    def testCoordinatedDiversityStaysOneLevelAheadWithinPolicyCap(
        self,
    ) -> None:
        self.assertEqual(
            SelectEffectiveCoordinatedCandidateDiversityLevel(
                0,
                1,
                7,
                True,
            ),
            1,
        )
        self.assertEqual(
            SelectEffectiveCoordinatedCandidateDiversityLevel(
                3,
                1,
                7,
                True,
            ),
            4,
        )
        self.assertEqual(
            SelectEffectiveCoordinatedCandidateDiversityLevel(
                6,
                1,
                7,
                True,
            ),
            6,
        )
        self.assertEqual(
            SelectEffectiveCoordinatedCandidateDiversityLevel(
                3,
                1,
                7,
                False,
            ),
            0,
        )
        self.assertEqual(
            SelectCoordinatedCandidateExpansionLimit(
                12_000,
                90_000,
                2,
                3,
                False,
            ),
            12_000,
        )

    def testMatureStagedSchedulerRecoversOnlyInContinuationTranche(
        self,
    ) -> None:
        Requests = {
            "Target": [
                ("Target", 0),
                ("Target", 1),
                ("Target", 2),
                ("Target", 3),
            ],
            "Other": [
                ("Other", 0),
                ("Other", 1),
            ],
        }
        Calls = []

        def GenerateBatch(Batch):
            Calls.append(list(Batch))
            return [
                (
                    "tree-Target2"
                    if Request == ("Target", 2)
                    else "tree-Other0"
                    if Request == ("Other", 0)
                    else None
                )
                for Request in Batch
            ]

        Result = GenerateStagedInitialRouteTrees(
            ("Target", "Other"),
            Requests,
            GenerateBatch,
            lambda _Signal: True,
        )

        self.assertTrue(Result.FullPoolGenerated)
        self.assertEqual(Result.ExhaustedSignals, ())
        self.assertEqual(
            dict(Result.FirstSuccessfulRequestIndicesBySignal),
            {"Target": 2, "Other": 0},
        )
        self.assertEqual(
            Result.RouteTrees,
            (
                None,
                None,
                "tree-Target2",
                None,
                "tree-Other0",
                None,
            ),
        )
        self.assertEqual(
            Calls,
            [
                [("Target", 0), ("Other", 0)],
                [("Target", 1)],
                [("Target", 2)],
                [
                    ("Target", 3),
                    ("Other", 1),
                ],
            ],
        )
        self.assertEqual(
            Counter(
                Request
                for Batch in Calls
                for Request in Batch
            ),
            Counter({
                ("Target", 0): 1,
                ("Target", 1): 1,
                ("Target", 2): 1,
                ("Target", 3): 1,
                ("Other", 0): 1,
                ("Other", 1): 1,
            }),
        )

    def testMatureStagedSchedulerPublishesCoordinatedSeedPoolEarly(
        self,
    ) -> None:
        Requests = {
            "Target": [
                ("Target", 0),
                ("Target", 1),
                ("Target", 2),
                ("Target", 3),
            ],
            "Other": [
                ("Other", 0),
                ("Other", 1),
            ],
        }
        Calls = []

        def GenerateBatch(Batch):
            Calls.append(list(Batch))
            return [
                (
                    "tree-Target2"
                    if Request == ("Target", 2)
                    else "tree-Other0"
                    if Request == ("Other", 0)
                    else None
                )
                for Request in Batch
            ]

        Result = GenerateStagedInitialRouteTrees(
            ("Target", "Other"),
            Requests,
            GenerateBatch,
            lambda _Signal: True,
            StopAfterEverySignalHasTree=True,
        )

        self.assertFalse(Result.FullPoolGenerated)
        self.assertTrue(Result.EverySignalHasTree)
        self.assertEqual(Result.ExhaustedSignals, ())
        self.assertEqual(Result.ExecutedRequestCount, 4)
        self.assertEqual(
            Result.RouteTrees,
            (
                None,
                None,
                "tree-Target2",
                None,
                "tree-Other0",
                None,
            ),
        )
        self.assertEqual(
            Calls,
            [
                [("Target", 0), ("Other", 0)],
                [("Target", 1)],
                [("Target", 2)],
            ],
        )

    def testMatureStagedSchedulerRestoresFullSignalMajorPool(
        self,
    ) -> None:
        Requests = {
            "A": [("A", 0), ("A", 1), ("A", 2)],
            "B": [("B", 0), ("B", 1), ("B", 2)],
        }
        Values = {
            Request: f"tree-{Request[0]}{Request[1]}"
            for SignalRequests in Requests.values()
            for Request in SignalRequests
        }
        Calls = []

        def GenerateBatch(Batch):
            Calls.append(list(Batch))
            return [Values[Request] for Request in Batch]

        Result = GenerateStagedInitialRouteTrees(
            ("A", "B"),
            Requests,
            GenerateBatch,
            lambda _Signal: True,
        )
        CanonicalRequests = [
            *Requests["A"],
            *Requests["B"],
        ]

        self.assertTrue(Result.FullPoolGenerated)
        self.assertEqual(Result.ExhaustedSignals, ())
        self.assertEqual(Result.ExecutedRequestCount, 6)
        self.assertEqual(Result.PlannedRequestCount, 6)
        self.assertEqual(
            Result.RouteTrees,
            tuple(Values[Request] for Request in CanonicalRequests),
        )
        self.assertEqual(
            Calls,
            [
                [("A", 0), ("B", 0)],
                [
                    ("A", 1),
                    ("A", 2),
                    ("B", 1),
                    ("B", 2),
                ],
            ],
        )

    def testMatureStagedSchedulerHandlesEmptyAndUnevenWindows(
        self,
    ) -> None:
        Requests = {
            "Empty": [],
            "Short": [("Short", 0), ("Short", 1)],
            "Long": [("Long", 0), ("Long", 1), ("Long", 2)],
        }
        Calls = []

        def GenerateBatch(Batch):
            Calls.append(list(Batch))
            return [
                "tree-Long0" if Request == ("Long", 0) else None
                for Request in Batch
            ]

        Result = GenerateStagedInitialRouteTrees(
            ("Empty", "Short", "Long"),
            Requests,
            GenerateBatch,
            lambda _Signal: True,
        )

        self.assertFalse(Result.FullPoolGenerated)
        self.assertEqual(Result.ExhaustedSignals, ("Short",))
        self.assertEqual(Result.ExecutedRequestCount, 3)
        self.assertEqual(Result.PlannedRequestCount, 5)
        self.assertEqual(
            Calls,
            [
                [("Short", 0), ("Long", 0)],
                [("Short", 1)],
            ],
        )

    def testMatureStagedSchedulerPreservesHigherOrderStarvationPath(
        self,
    ) -> None:
        Requests = {
            "HigherOrder": [
                ("HigherOrder", 0),
                ("HigherOrder", 1),
            ],
            "Other": [("Other", 0), ("Other", 1)],
        }
        Calls = []

        def GenerateBatch(Batch):
            Calls.append(list(Batch))
            return [
                "tree-Other0" if Request == ("Other", 0) else None
                for Request in Batch
            ]

        Result = GenerateStagedInitialRouteTrees(
            ("HigherOrder", "Other"),
            Requests,
            GenerateBatch,
            lambda Signal: Signal != "HigherOrder",
        )

        self.assertTrue(Result.FullPoolGenerated)
        self.assertEqual(Result.ExhaustedSignals, ())
        self.assertEqual(
            Result.RouteTrees,
            (None, None, "tree-Other0", None),
        )
        self.assertEqual(
            Calls,
            [
                [("HigherOrder", 0), ("Other", 0)],
                [("HigherOrder", 1)],
                [("Other", 1)],
            ],
        )

    def testMatureStagedSeedDefersSuccessfulSuffixForHigherOrderRetry(
        self,
    ) -> None:
        Requests = {
            "HigherOrder": [
                ("HigherOrder", 0),
                ("HigherOrder", 1),
            ],
            "Other": [
                ("Other", 0),
                ("Other", 1),
                ("Other", 2),
            ],
        }
        Calls = []

        def GenerateBatch(Batch):
            Calls.append(list(Batch))
            return [
                "tree-Other0" if Request == ("Other", 0) else None
                for Request in Batch
            ]

        Result = GenerateStagedInitialRouteTrees(
            ("HigherOrder", "Other"),
            Requests,
            GenerateBatch,
            lambda Signal: Signal != "HigherOrder",
            StopAfterEverySignalHasTree=True,
        )

        self.assertFalse(Result.FullPoolGenerated)
        self.assertFalse(Result.EverySignalHasTree)
        self.assertEqual(Result.ExhaustedSignals, ())
        self.assertEqual(Result.ExecutedRequestCount, 3)
        self.assertEqual(Result.PlannedRequestCount, 5)
        self.assertEqual(
            dict(Result.ExecutedRequestCountsBySignal),
            {"HigherOrder": 2, "Other": 1},
        )
        self.assertEqual(
            Result.RouteTrees,
            (None, None, "tree-Other0", None, None),
        )
        self.assertEqual(
            Calls,
            [
                [("HigherOrder", 0), ("Other", 0)],
                [("HigherOrder", 1)],
            ],
        )

    def testUnrelatedStarvationBypassesPairScanAndAdvancesPortfolio(
        self,
    ) -> None:
        self.assertFalse(ShouldScanCandidateDomainPairCut(
            AdaptiveRoutingEnabled=True,
            PlacementWasRelocated=True,
            ExactLegalRetainedJointStateCount=6,
            JointHigherOrderConstraintCount=1,
            StarvedSignal="NandNet0",
            JointHigherOrderConstraintSignals=frozenset({
                "A0",
                "A1",
                "Propagate1",
            }),
            CandidateDiversityLevel=0,
            ReservationVariant=0,
            LaneDiversityLevel=0,
            SkipStrictPortalReservation=False,
            MaximumCandidateDiversityEscalations=4,
        ))
        self.assertTrue(
            ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
                PlacementWasRelocated=True,
                ExactLegalRetainedJointStateCount=6,
                HasCumulativeAssignmentConstraints=True,
                CandidateDiversityLevel=0,
                ReservationVariant=0,
                LaneDiversityLevel=0,
                SkipStrictPortalReservation=False,
                RoutedTreeCount=0,
                MaterializedCandidateCount=0,
            )
        )

    def testCandidateStarvationClassIgnoresOnlyWindowAccounting(
        self,
    ) -> None:
        First = {
            "Materialized": 0,
            "RoutedTrees": 0,
            "Requests": 32,
            "DeferredRequests": 672,
            "Rejections": {},
            "SourcePortals": 85,
            "TargetPortals": 245,
            "ForeignBlockedNodes": 2012,
            "SeedNodes": 0,
        }
        Second = {
            **First,
            "Requests": 16,
            "DeferredRequests": 688,
            "ForeignPortalOverlapRequests": 14,
        }
        Fingerprint = BuildCandidateStarvationClassFingerprint(
            "Generate0",
            First,
        )
        self.assertEqual(
            Fingerprint,
            BuildCandidateStarvationClassFingerprint(
                "Generate0",
                Second,
            ),
        )
        self.assertNotEqual(
            Fingerprint,
            BuildCandidateStarvationClassFingerprint(
                "Generate0",
                {**Second, "TargetPortals": 244},
            ),
        )
        self.assertEqual(
            CountPriorCandidateStarvationClassFingerprint(
                (
                    {
                        "Stage": "CandidateGeneration",
                        "CandidateStarvationClassFingerprint": Fingerprint,
                    },
                    {
                        "Stage": "TrackAssignment",
                        "CandidateStarvationClassFingerprint": Fingerprint,
                    },
                ),
                Fingerprint,
            ),
            1,
        )

    def testUnindexedCandidateClaimsAreIdentified(self) -> None:
        Indexed = IndexedRoutingResourceGraph(
            ResourcePositions=((0, 1, 0), (0, 0, 0)),
            PositionIndices={
                (0, 1, 0): 0,
                (0, 0, 0): 1,
            },
        )
        Claims = RoutingResourceClaims(
            WireCells=frozenset({(0, 1, 0)}),
            SupportCells=frozenset({(0, 0, 0)}),
            RequiredAirCells=frozenset({(1, 1, 0)}),
            ElectricalCells=frozenset({(0, 1, 0), (0, 1, 1)}),
        )

        self.assertEqual(
            FindUnindexedClaimPositions(Indexed, Claims),
            frozenset({(1, 1, 0), (0, 1, 1)}),
        )

    def testCandidateRegenerationUsesEveryExactPairEndpoint(self) -> None:
        self.assertEqual(
            SelectCandidateRegenerationSignals({
                "CandidateCounts": {
                    "B1": 10,
                    "NandNet3": 15,
                    "CarryIn": 16,
                    "Propagate0": 8,
                },
                "PairwiseIncompatibleEdges": [
                    ["B1", "NandNet3"],
                    ["CarryIn", "Propagate0"],
                ],
            }),
            ["Propagate0", "B1", "NandNet3", "CarryIn"],
        )

    def testCandidateRegenerationIncludesCompletePriorityCut(self) -> None:
        self.assertEqual(
            SelectCandidateRegenerationSignals({
                "CandidateCounts": {
                    "PairA": 8,
                    "PairB": 9,
                    "Native": 2,
                    "Failure": 4,
                },
                "PairwiseIncompatibleEdges": [["PairA", "PairB"]],
                "CandidateCoverageRepairSignals": ["PairA", "PairB"],
                "PriorityRelocationSignals": [
                    "Native",
                    "Failure",
                    "PairA",
                    "PairB",
                ],
            }),
            ["Native", "Failure", "PairA", "PairB"],
        )

    def testOrdinaryExactRegenerationBatchesFreshEndpoints(self) -> None:
        ConflictGraph = {
            "CandidateCounts": {
                "A": 4,
                "B": 3,
                "C": 2,
                "D": 5,
            },
            "PairwiseIncompatibleEdges": [
                ["A", "B"],
                ["C", "D"],
            ],
        }
        self.assertEqual(
            SelectCandidateRegenerationCoverSignals(ConflictGraph),
            ["C", "B", "A", "D"],
        )
        self.assertEqual(
            SelectCandidateRegenerationCoverSignals(
                ConflictGraph,
                frozenset({"B", "C"}),
            ),
            ["A", "D"],
        )

    def testHigherOrderCoverIncludesFreshNonEdgeContributors(self) -> None:
        self.assertEqual(
            SelectCandidateRegenerationCoverSignals(
                {
                    "Classification": "relocated-higher-order-conflict",
                    "CandidateCounts": {
                        "A": 4,
                        "B": 3,
                        "C": 2,
                    },
                    "ConflictSignals": ["A", "B", "C"],
                    "PairwiseIncompatibleEdges": [["A", "B"]],
                },
                frozenset({"B"}),
                frozenset({"A", "B", "C", "D"}),
            ),
            ["D", "C", "A"],
        )

    def testCandidatePairAvoidanceIsScopedAwayFromStarvedSignal(
        self,
    ) -> None:
        Positions = frozenset({(1, 2, 3), (4, 5, 6)})

        Scoped = MergeSignalScopedAvoidancePositions(
            {"Existing": frozenset({(9, 9, 9)})},
            frozenset({"PairA", "PairB"}),
            Positions,
        )

        self.assertEqual(Scoped["PairA"], Positions)
        self.assertEqual(Scoped["PairB"], Positions)
        self.assertEqual(Scoped["Existing"], frozenset({(9, 9, 9)}))
        self.assertNotIn("Starved", Scoped)

    def testRepeatedExactPairCutAdvancesAfterOneEndpointExpansion(
        self,
    ) -> None:
        ConflictGraph = {
            "Classification": "portal-coverage-pair-conflict",
            "PairwiseIncompatibleEdges": [["B1", "NandNet3"]],
        }
        self.assertFalse(HasRepeatedExactPairCut((), ConflictGraph))
        self.assertTrue(HasRepeatedExactPairCut(
            (
                {
                    "Stage": "TrackAssignment",
                    "Action": "regenerate-affected-candidates",
                    "ConflictClassification": (
                        "portal-coverage-pair-conflict"
                    ),
                    "PairwiseIncompatibleEdges": [
                        ["NandNet3", "B1"],
                    ],
                },
            ),
            ConflictGraph,
        ))
        self.assertFalse(HasRepeatedExactPairCut(
            (
                {
                    "Stage": "TrackAssignment",
                    "Action": "increase-guide-lane-diversity",
                    "ConflictClassification": (
                        "portal-coverage-pair-conflict"
                    ),
                    "PairwiseIncompatibleEdges": [
                        ["B1", "NandNet3"],
                    ],
                },
            ),
            ConflictGraph,
        ))
        self.assertTrue(HasRepeatedExactPairCut(
            (
                {
                    "Stage": "TrackAssignment",
                    "Action": "regenerate-affected-candidates",
                    "ConflictClassification": (
                        "candidate-domain-pair-conflict"
                    ),
                    "CandidateDomainPairExpansion": True,
                    "PairwiseIncompatibleEdges": [
                        ["NandNet3", "B1"],
                    ],
                },
            ),
            ConflictGraph,
        ))

    def testCoveredContractingPairCutAdvancesOnlyAfterEndpointExpansion(
        self,
    ) -> None:
        ConflictGraph = {
            "Classification": "portal-coverage-pair-conflict",
            "PairwiseIncompatibleEdges": [
                ["A", "B"],
                ["B", "C"],
            ],
        }
        CoveredExpansion = {
            "Stage": "TrackAssignment",
            "Action": "regenerate-affected-candidates",
            "ConflictClassification": "portal-coverage-pair-conflict",
            "ExactPairEndpointExpansion": True,
            "AffectedSignals": ["A", "B", "C", "D"],
            "PairwiseIncompatibleEdges": [
                ["B", "A"],
                ["C", "B"],
                ["C", "D"],
            ],
        }
        self.assertTrue(HasCoveredPairCutAfterEndpointExpansion(
            (CoveredExpansion,),
            ConflictGraph,
        ))
        self.assertTrue(HasCoveredPairCutAfterEndpointExpansion(
            (CoveredExpansion,),
            {
                **ConflictGraph,
                "PairwiseIncompatibleEdges": [["A", "B"]],
            },
        ))
        self.assertFalse(HasCoveredPairCutAfterEndpointExpansion(
            ({
                **CoveredExpansion,
                "ExactPairEndpointExpansion": False,
            },),
            ConflictGraph,
        ))
        self.assertFalse(HasCoveredPairCutAfterEndpointExpansion(
            ({
                **CoveredExpansion,
                "AffectedSignals": ["A", "B", "D"],
            },),
            ConflictGraph,
        ))
        self.assertFalse(HasCoveredPairCutAfterEndpointExpansion(
            (CoveredExpansion,),
            {
                **ConflictGraph,
                "PairwiseIncompatibleEdges": [
                    ["A", "B"],
                    ["B", "C"],
                    ["C", "E"],
                ],
            },
        ))
        self.assertFalse(HasCoveredPairCutAfterEndpointExpansion(
            (CoveredExpansion,),
            {
                **ConflictGraph,
                "Classification": "relocated-pairwise-incompatibility",
            },
        ))

    def testNewExactConflictEndpointsPermitSameLevelRepair(self) -> None:
        self.assertTrue(ShouldRegenerateNewExactConflictSignals(
            "relocated-multi-pair-conflict",
            61,
            frozenset({"A0", "B0"}),
            frozenset({"A1", "NandNet3"}),
        ))
        self.assertFalse(ShouldRegenerateNewExactConflictSignals(
            "relocated-multi-pair-conflict",
            61,
            frozenset({"A1", "NandNet3"}),
            frozenset({"A1", "NandNet3"}),
        ))
        self.assertFalse(ShouldRegenerateNewExactConflictSignals(
            "relocated-multi-pair-conflict",
            65,
            frozenset(),
            frozenset({"A1", "NandNet3"}),
        ))
        self.assertTrue(ShouldRegenerateNewExactConflictSignals(
            "relocated-higher-order-conflict",
            61,
            frozenset({"A1"}),
            frozenset({"CarryIn", "Generate0", "Propagate0"}),
        ))
        self.assertTrue(ShouldRegenerateNewExactConflictSignals(
            "relocated-larger-matching-failure",
            61,
            frozenset({"Carry3"}),
            frozenset({"Carry3", "NandNet22", "NandNet23"}),
        ))
        self.assertTrue(ShouldRegenerateNewExactConflictSignals(
            "relocated-pairwise-incompatibility",
            61,
            frozenset({"CarryIn"}),
            frozenset({"CarryIn", "Propagate0"}),
        ))

    def testBroadCompactRelocationRetriesCandidateStarvation(self) -> None:
        self.assertTrue(
            ShouldRetryRelocatedCandidateStarvation(
                True,
                "row-beam-conflict-relocation",
                2,
                8,
                False,
                63,
                3,
            )
        )
        self.assertFalse(
            ShouldRetryRelocatedCandidateStarvation(
                True,
                "row-beam-conflict-relocation",
                2,
                8,
                True,
                63,
                3,
            )
        )
        self.assertTrue(
            ShouldRetryRelocatedCandidateStarvation(
                True,
                "row-beam-conflict-relocation",
                2,
                8,
                False,
                63,
                1,
            )
        )
        self.assertTrue(
            ShouldRetryRelocatedCandidateStarvation(
                True,
                "row-beam-conflict-relocation",
                3,
                8,
                True,
                100,
                1,
            )
        )
        self.assertFalse(
            ShouldRetryRelocatedCandidateStarvation(
                False,
                "row-beam-conflict-relocation",
                3,
                10,
                False,
                63,
                10,
            )
        )

    def testUniqueAccessDistinctCandidateContinuesReservedProofOnce(
        self,
    ) -> None:
        Diagnostics = {
            "Eligible": True,
            "CutInterfaceDifference": 917,
            "AccessDistinctCandidateCount": 1,
        }
        self.assertTrue(
            ShouldContinueUniqueAccessDistinctCandidateRealizabilityProof(
                True,
                Diagnostics,
                11.5,
            )
        )
        self.assertFalse(
            ShouldContinueUniqueAccessDistinctCandidateRealizabilityProof(
                False,
                Diagnostics,
                11.5,
            )
        )
        self.assertFalse(
            ShouldContinueUniqueAccessDistinctCandidateRealizabilityProof(
                True,
                {
                    **Diagnostics,
                    "AccessDistinctCandidateCount": 2,
                },
                11.5,
            )
        )
        self.assertFalse(
            ShouldContinueUniqueAccessDistinctCandidateRealizabilityProof(
                True,
                Diagnostics,
                6.9,
            )
        )

    def testContinuedCandidateHandsOffCompleteCutBeforeRepeat(
        self,
    ) -> None:
        History = ({
            "Action": (
                "continue-unique-access-distinct-"
                "candidate-realizability-proof"
            ),
        },)
        Conflict = {
            "PriorityRelocationSignals": ["AnonymousFirst", "AnonymousSecond"],
        }
        self.assertTrue(
            ShouldHandoffContinuedCandidateRealizabilityCut(
                History,
                Conflict,
                False,
            )
        )
        self.assertFalse(
            ShouldHandoffContinuedCandidateRealizabilityCut(
                History,
                Conflict,
                True,
            )
        )
        self.assertFalse(
            ShouldHandoffContinuedCandidateRealizabilityCut(
                (),
                Conflict,
                False,
            )
        )
        self.assertFalse(
            ShouldHandoffContinuedCandidateRealizabilityCut(
                History,
                {},
                False,
            )
        )

    def testCompleteProofSelectsAnonymousMinimumFailurePair(
        self,
    ) -> None:
        Graph = {
            "FailureNet": "Failed",
            "CandidateCounts": {
                "Failed": 1,
                "First": 1,
                "Second": 1,
            },
            "PairwiseIncompatibleEdges": [
                ["Failed", "First"],
                ["Failed", "Second"],
            ],
        }
        self.assertEqual(
            SelectAnonymousMinimumFailurePairRelocationSignals(
                Graph,
                {
                    "Failed": "20",
                    "First": "30",
                    "Second": "10",
                },
            ),
            ["Failed", "Second"],
        )
        RenamedGraph = {
            "FailureNet": "OpaqueZ",
            "CandidateCounts": {
                "OpaqueZ": 1,
                "OpaqueA": 1,
                "OpaqueB": 1,
            },
            "PairwiseIncompatibleEdges": [
                ["OpaqueZ", "OpaqueA"],
                ["OpaqueZ", "OpaqueB"],
            ],
        }
        self.assertEqual(
            SelectAnonymousMinimumFailurePairRelocationSignals(
                RenamedGraph,
                {
                    "OpaqueZ": "20",
                    "OpaqueA": "30",
                    "OpaqueB": "10",
                },
            ),
            ["OpaqueB", "OpaqueZ"],
        )

    def testClaimConflictPositionsExcludeNonConflictingClaims(self) -> None:
        First = RoutingResourceClaims(
            WireCells=frozenset({(0, 1, 0), (5, 1, 0)}),
            SupportCells=frozenset({(1, 0, 0)}),
            RequiredAirCells=frozenset({(2, 1, 0)}),
            ElectricalCells=frozenset({(3, 1, 0)}),
        )
        Second = RoutingResourceClaims(
            WireCells=frozenset({(1, 0, 0), (2, 1, 0), (3, 1, 0)}),
            SupportCells=frozenset({(0, 1, 0)}),
            RequiredAirCells=frozenset({(9, 1, 0)}),
            ElectricalCells=frozenset({(5, 1, 0)}),
        )

        self.assertEqual(
            ClaimConflictPositions(First, Second),
            frozenset({
                (0, 1, 0),
                (1, 0, 0),
                (2, 1, 0),
                (3, 1, 0),
                (5, 1, 0),
            }),
        )
        self.assertTrue(MandatoryClaimsConflict(First, Second))
        self.assertFalse(MandatoryClaimsConflict(
            First,
            RoutingResourceClaims(
                WireCells=frozenset({(20, 1, 0)}),
                ElectricalCells=frozenset({(20, 1, 0)}),
            ),
        ))

    def testFindUnavoidableMandatoryClaimCutRequiresEveryAlternative(self) -> None:
        FixedElectrical = RoutingResourceClaims(
            ElectricalCells=frozenset({(1, 1, 0), (2, 1, 0)}),
        )
        FirstOptions = (
            RoutingResourceClaims(WireCells=frozenset({(1, 1, 0)})),
            RoutingResourceClaims(WireCells=frozenset({(2, 1, 0)})),
        )

        Cut = FindUnavoidableMandatoryClaimCut({
            "First": FirstOptions,
            "Second": (FixedElectrical,),
        })

        self.assertEqual(
            Cut,
            (
                ("First", "Second"),
                frozenset({(1, 1, 0), (2, 1, 0)}),
            ),
        )
        self.assertIsNone(FindUnavoidableMandatoryClaimCut({
            "First": (
                *FirstOptions,
                RoutingResourceClaims(
                    WireCells=frozenset({(9, 1, 0)}),
                ),
            ),
            "Second": (FixedElectrical,),
        }))

    def testFindAllUnavoidableMandatoryClaimCutsReturnsWholeRepairBatch(
        self,
    ) -> None:
        SharedElectrical = RoutingResourceClaims(
            ElectricalCells=frozenset({(1, 1, 0), (2, 1, 0)}),
        )
        Cuts = FindAllUnavoidableMandatoryClaimCuts({
            "A": (
                RoutingResourceClaims(WireCells=frozenset({(1, 1, 0)})),
            ),
            "B": (SharedElectrical,),
            "C": (
                RoutingResourceClaims(WireCells=frozenset({(2, 1, 0)})),
            ),
        })

        self.assertEqual(
            Cuts,
            (
                (("A", "B"), frozenset({(1, 1, 0)})),
                (("B", "C"), frozenset({(2, 1, 0)})),
            ),
        )

    def testCompleteMandatoryClaimCoveragePreservesEveryRepairPair(
        self,
    ) -> None:
        SharedElectrical = RoutingResourceClaims(
            ElectricalCells=frozenset({(1, 1, 0), (2, 1, 0)}),
        )
        Coverage = BuildCompleteMandatoryClaimCutCoverage(
            {
                "A": (
                    RoutingResourceClaims(
                        WireCells=frozenset({(1, 1, 0)}),
                    ),
                ),
                "B": (SharedElectrical,),
                "C": (
                    RoutingResourceClaims(
                        WireCells=frozenset({(2, 1, 0)}),
                    ),
                ),
            },
            False,
        )

        self.assertIsNotNone(Coverage)
        assert Coverage is not None
        self.assertEqual(
            Coverage["Classification"],
            "portal-coverage-pair-conflict",
        )
        self.assertEqual(
            Coverage["PairwiseIncompatibleEdges"],
            [["A", "B"], ["B", "C"]],
        )
        self.assertEqual(
            Coverage["CandidateCoverageRepairSignals"],
            ["A", "B", "C"],
        )
        self.assertEqual(
            Coverage["MandatoryConflictPositions"],
            [[1, 1, 0], [2, 1, 0]],
        )

    def testForeignElectricalExclusionProjectionMatchesNaiveUnion(
        self,
    ) -> None:
        class InflatingTechnology:
            @staticmethod
            def BuildElectricalExclusions(Nodes):
                return {
                    (X + Delta, Y, Z)
                    for X, Y, Z in Nodes
                    for Delta in (-1, 0, 1)
                }

        Protected = {
            "A": frozenset({(0, 1, 0), (1, 1, 0)}),
            "B": frozenset({(1, 1, 0), (3, 1, 0)}),
            "C": frozenset({(5, 1, 0)}),
        }
        Projected = BuildForeignElectricalExclusionsBySignal(
            Protected,
            InflatingTechnology(),
        )
        Naive = {
            Signal: frozenset(
                InflatingTechnology.BuildElectricalExclusions(set().union(*(
                    Nodes
                    for OtherSignal, Nodes in Protected.items()
                    if OtherSignal != Signal
                )))
                - Protected[Signal]
            )
            for Signal in Protected
        }

        self.assertEqual(Projected, Naive)

        Factored = BuildForeignElectricalExclusionsBySignal(
            Protected,
            InflatingTechnology(),
            DeferredPairwiseSignals=frozenset(("A", "B")),
        )
        StaticCExclusions = frozenset(
            InflatingTechnology.BuildElectricalExclusions(
                set(Protected["C"])
            )
        )
        self.assertEqual(
            Factored["A"],
            StaticCExclusions - Protected["A"],
        )
        self.assertEqual(
            Factored["B"],
            StaticCExclusions - Protected["B"],
        )
        self.assertEqual(Factored["C"], frozenset())

    def testCandidateStarvationPairScanAcceptsCompatibleAlternative(
        self,
    ) -> None:
        SharedElectrical = RoutingResourceClaims(
            ElectricalCells=frozenset({(1, 1, 0), (2, 1, 0)}),
        )
        FirstOptions = [
            self.BuildCandidate("A", "A0", (1, 1, 0)),
            self.BuildCandidate("A", "A1", (9, 1, 0)),
        ]
        SecondOptions = [
            replace(
                self.BuildCandidate("B", "B0", (2, 1, 0)),
                Claims=SharedElectrical,
            ),
        ]

        self.assertIsNone(
            FindFirstUnavoidableCandidateDomainPairCut({
                "A": FirstOptions,
                "B": SecondOptions,
                "Starved": [],
            })
        )

    def testCandidateStarvationPairScanRequiresValidatorAcceptance(
        self,
    ) -> None:
        First = self.BuildCandidate("A", "A0", (1, 1, 0))
        Second = self.BuildCandidate("B", "B0", (1, 1, 0))
        Work = []

        self.assertIsNone(
            FindFirstUnavoidableCandidateDomainPairCut(
                {
                    "A": [First],
                    "B": [Second],
                },
                WorkCheck=lambda Diagnostics: Work.append(Diagnostics),
                CutValidator=lambda _Cut: False,
            )
        )
        self.assertIn(
            "rejected-cut",
            [Entry["Phase"] for Entry in Work],
        )

    def testCandidateStarvationPairScanHonorsPrivateCheckLimit(
        self,
    ) -> None:
        Work = []
        self.assertIsNone(
            FindFirstUnavoidableCandidateDomainPairCut(
                {
                    "A": [
                        self.BuildCandidate("A", "A0", (1, 1, 0)),
                        self.BuildCandidate("A", "A1", (2, 1, 0)),
                    ],
                    "B": [
                        self.BuildCandidate("B", "B0", (1, 1, 0)),
                        self.BuildCandidate("B", "B1", (2, 1, 0)),
                    ],
                },
                WorkCheck=lambda Diagnostics: Work.append(Diagnostics),
                MaximumCandidatePairChecks=1,
            )
        )
        self.assertEqual(Work[-1]["Phase"], "limit")
        self.assertEqual(Work[-1]["CandidatePairChecks"], 1)
        with self.assertRaises(ValueError):
            FindFirstUnavoidableCandidateDomainPairCut(
                {},
                MaximumCandidatePairChecks=0,
            )

    def testCandidateStarvationPairScanIsRenameAndOrderIndependent(
        self,
    ) -> None:
        def Candidate(
            Signal: str,
            CandidateId: str,
            Claims: RoutingResourceClaims,
        ) -> NetRouteCandidate:
            return replace(
                self.BuildCandidate(
                    Signal,
                    CandidateId,
                    (0, 1, 0),
                ),
                Claims=Claims,
            )

        FirstClaims = RoutingResourceClaims(
            WireCells=frozenset({(4, 1, 0)}),
        )
        SecondClaims = RoutingResourceClaims(
            ElectricalCells=frozenset({(4, 1, 0)}),
        )
        Original = FindFirstUnavoidableCandidateDomainPairCut({
            "A": [Candidate("A", "A0", FirstClaims)],
            "B": [Candidate("B", "B0", SecondClaims)],
        })
        Renamed = FindFirstUnavoidableCandidateDomainPairCut({
            "Signal91": [
                Candidate("Signal91", "Signal91-0", SecondClaims),
            ],
            "Signal17": [
                Candidate("Signal17", "Signal17-0", FirstClaims),
            ],
        })

        self.assertIsNotNone(Original)
        self.assertIsNotNone(Renamed)
        self.assertEqual(Original.Signals, ("A", "B"))
        self.assertEqual(
            frozenset(Renamed.Signals),
            frozenset({"Signal17", "Signal91"}),
        )
        self.assertEqual(
            Original.ConflictPositions,
            Renamed.ConflictPositions,
        )

    def testCandidatePairPrioritySurvivesMultipleCutsAndRename(
        self,
    ) -> None:
        def Candidate(
            Signal: str,
            CandidateId: str,
            Position: tuple[int, int, int],
        ) -> NetRouteCandidate:
            return self.BuildCandidate(
                Signal,
                CandidateId,
                Position,
            )

        Original = {
            "A": [Candidate("A", "A0", (1, 1, 0))],
            "B": [Candidate("B", "B0", (1, 1, 0))],
            "C": [Candidate("C", "C0", (2, 1, 0))],
            "D": [Candidate("D", "D0", (2, 1, 0))],
        }
        Renamed = {
            "Signal91": [
                Candidate("Signal91", "Signal91-0", (1, 1, 0)),
            ],
            "Signal17": [
                Candidate("Signal17", "Signal17-0", (1, 1, 0)),
            ],
            "Signal63": [
                Candidate("Signal63", "Signal63-0", (2, 1, 0)),
            ],
            "Signal22": [
                Candidate("Signal22", "Signal22-0", (2, 1, 0)),
            ],
        }

        OriginalCut = FindFirstUnavoidableCandidateDomainPairCut(
            Original,
            OrderedSignals=("A", "B", "C", "D"),
            PrioritySignals=frozenset({"C", "D"}),
            MaximumCandidatePairChecks=1,
        )
        RenamedCut = FindFirstUnavoidableCandidateDomainPairCut(
            Renamed,
            OrderedSignals=(
                "Signal91",
                "Signal17",
                "Signal63",
                "Signal22",
            ),
            PrioritySignals=frozenset({
                "Signal63",
                "Signal22",
            }),
            MaximumCandidatePairChecks=1,
        )

        self.assertIsNotNone(OriginalCut)
        self.assertEqual(OriginalCut.Signals, ("C", "D"))
        self.assertIsNotNone(RenamedCut)
        self.assertEqual(
            frozenset(RenamedCut.Signals),
            frozenset({"Signal22", "Signal63"}),
        )
        self.assertEqual(
            OriginalCut.ConflictPositions,
            RenamedCut.ConflictPositions,
        )

    def testExactCoordinatedOffenderMaterializesCurrentBroadShapes(
        self,
    ) -> None:
        Arguments = {
            "UnreservedPortalMode": True,
            "UseSparseCandidateBootstrap": True,
            "SparseBootstrapRanks": (3, 4, 5),
            "PortalShapeRank": 5,
            "UnreservedPerLayerRequestLimit": 2,
        }
        self.assertTrue(
            ShouldDeferUnreservedCandidateRequestShape(
                **Arguments,
                CompleteCoordinatedSignalWindow=False,
            )
        )
        self.assertFalse(
            ShouldDeferUnreservedCandidateRequestShape(
                **Arguments,
                CompleteCoordinatedSignalWindow=True,
            )
        )

    def testPhysicalGlobalPlanningEagerlyCompletesOnlyFixedPortWindows(
        self,
    ) -> None:
        Arguments = {
            "UseSparseCandidateBootstrap": True,
            "SparseBootstrapRanks": (3, 4, 5),
            "PortalShapeRank": 5,
            "UnreservedPerLayerRequestLimit": 2,
        }
        self.assertTrue(
            ShouldCompletePhysicalCandidateRequestWindow(
                True,
                False,
                0,
                0,
                True,
            )
        )
        self.assertFalse(
            ShouldCompletePhysicalCandidateRequestWindow(
                True,
                False,
                0,
                0,
                False,
            )
        )
        self.assertFalse(
            ShouldCompletePhysicalCandidateRequestWindow(
                False,
                False,
                0,
                0,
            )
        )
        self.assertFalse(
            ShouldDeferUnreservedCandidateRequestShape(
                **{
                    **Arguments,
                    "UnreservedPortalMode": False,
                },
                CompleteCoordinatedSignalWindow=False,
            )
        )

    def testLazyPhysicalCandidateRequestMaterializesExactlyOnce(self) -> None:
        Calls = []
        Shape = CandidateRequestShapeDescriptor(
            SourcePortal=SimpleNamespace(PortalId="source"),
            TargetPortals=(SimpleNamespace(PortalId="target"),),
            Guide=frozenset({(1, 2)}),
            Layer=1,
            Axis="X",
            Lane=2,
            Variant=3,
            PortalShapeRank=4,
            RoutingY=5,
            GuideExpansion=6,
            InitiallyDeferred=True,
            Priority=(0, 4, 0, 0, 0, "X", 2),
        )

        def BuildRequest() -> tuple[object, ...]:
            Calls.append("materialized")
            return ("request",)

        Request = LazyCandidateRouteRequest(Shape, BuildRequest)
        StableIdentity = str(Request)

        self.assertEqual(Calls, [])
        self.assertEqual(Request.Materialize(), ("request",))
        self.assertEqual(Request.Materialize(), ("request",))
        self.assertEqual(Calls, ["materialized"])
        self.assertEqual(str(Request), StableIdentity)

    def testPhysicalRequestDependencyIgnoresSearchOrderingOnly(self) -> None:
        Source = SimpleNamespace(
            PortalId="source",
            Path=((0, 2, 0), (1, 2, 0)),
        )
        Target = SimpleNamespace(
            PortalId="target",
            Path=((8, 2, 0), (7, 2, 0)),
        )
        Base = CandidateRequestShapeDescriptor(
            SourcePortal=Source,
            TargetPortals=(Target,),
            Guide=frozenset(((1, 0), (2, 0))),
            Layer=1,
            Axis="X",
            Lane=2,
            Variant=3,
            PortalShapeRank=4,
            RoutingY=5,
            GuideExpansion=6,
            InitiallyDeferred=False,
            Priority=(0, 4, 0, 0, 0, "X", 2),
        )
        Reordered = replace(
            Base,
            Axis="Z",
            Lane=99,
            Variant=8,
            PortalShapeRank=12,
            InitiallyDeferred=True,
            Priority=(9,),
        )
        ChangedPath = replace(
            Base,
            SourcePortal=SimpleNamespace(
                PortalId="source",
                Path=((0, 2, 0), (0, 2, 1)),
            ),
        )

        self.assertEqual(
            BuildPhysicalCandidateRequestShapeDependencyIdentity(Base),
            BuildPhysicalCandidateRequestShapeDependencyIdentity(Reordered),
        )
        self.assertNotEqual(
            BuildPhysicalCandidateRequestShapeDependencyIdentity(Base),
            BuildPhysicalCandidateRequestShapeDependencyIdentity(ChangedPath),
        )

    def testNativePairCutStartsWithSmallestOpenPair(self) -> None:
        Result = SimpleNamespace(
            PairwiseCompatibilityComplete=True,
            PairwiseIncompatibleSignals=(
                ("LargeA", "LargeB"),
                ("SmallA", "SmallB"),
            ),
        )

        self.assertEqual(
            SelectPhysicalGlobalNativePairCutSuffixSignals(
                Result,
                {
                    "LargeA": 20,
                    "LargeB": 30,
                    "SmallA": 4,
                    "SmallB": 5,
                },
            ),
            ("SmallA", "SmallB"),
        )

    def testFrozenPhysicalPortGuidesRequireCompleteCutCoverage(
        self,
    ) -> None:
        Frozen = SimpleNamespace(
            Guides={"A": frozenset(((0, 0),)), "B": frozenset(((1, 0),))},
            Layers={"A": 0, "B": 1},
        )

        self.assertTrue(CanReuseFrozenPhysicalPortGuidePlan(
            ("A", "B"),
            ("A", "B", "C"),
            Frozen,
        ))
        self.assertFalse(CanReuseFrozenPhysicalPortGuidePlan(
            ("A", "C"),
            ("A", "B", "C"),
            Frozen,
        ))

    def testExactNoGoodClauseUnitPropagatesAcrossKeyClasses(self) -> None:
        def Option(Signal, Value):
            return SimpleNamespace(Signal=Signal, Value=Value)

        def Keys(Value):
            return frozenset(((Value.Signal, Value.Value),))

        Domains = {
            Signal: (Option(Signal, "bad"),)
            for Signal in ("A", "B", "C", "D", "E")
        }
        Domains["F"] = (
            Option("F", "bad"),
            Option("F", "escape"),
        )
        Domains["Unrelated"] = (Option("Unrelated", "only"),)
        Clause = frozenset(
            (Signal, "bad") for Signal in ("A", "B", "C", "D", "E", "F")
        )

        Result = PropagateExactNoGoodClauses(
            Domains,
            {},
            (Clause,),
            Keys,
        )

        self.assertIsNotNone(Result)
        assert Result is not None
        self.assertEqual(tuple(Value.Value for Value in Result["F"]), ("escape",))
        self.assertEqual(len(Result["Unrelated"]), 1)

    def testExactNoGoodClauseDetectsForcedContradiction(self) -> None:
        Domains = {
            Signal: (SimpleNamespace(Signal=Signal, Value="bad"),)
            for Signal in ("A", "B", "C")
        }
        Clause = frozenset((Signal, "bad") for Signal in Domains)

        self.assertIsNone(PropagateExactNoGoodClauses(
            Domains,
            {},
            (Clause,),
            lambda Value: frozenset(((Value.Signal, Value.Value),)),
        ))

    def testRepeatedExactNoGoodCoreChangesSharedPortFirst(self) -> None:
        Domains = {
            "SharedA": ("A0", "A1", "A2"),
            "SharedB": ("B0", "B1", "B2"),
            "Varying": ("V0", "V1"),
        }
        Rejected = (
            frozenset((
                ("SharedA", "A0"),
                ("SharedB", "B0"),
                ("Varying", "V0"),
            )),
            frozenset((
                ("SharedA", "A0"),
                ("SharedB", "B0"),
                ("Varying", "V1"),
            )),
        )
        Signal, Options = SelectExactNoGoodCspBranch(
            Domains,
            {},
            Rejected,
            lambda Option: frozenset((
                (
                    "SharedA" if Option.startswith("A") else
                    "SharedB" if Option.startswith("B") else
                    "Varying",
                    Option,
                ),
            )),
        )

        # MRV alone would revisit Varying. Repeated-clause activity changes a
        # shared literal first, while retaining every option in the domain.
        self.assertEqual(Signal, "SharedA")
        self.assertEqual(Options, ("A1", "A2", "A0"))
        self.assertEqual(set(Options), set(Domains[Signal]))

    def testContradictedExactNoGoodCoreDoesNotBiasBranching(self) -> None:
        Domains = {
            "SharedB": ("B0", "B1", "B2"),
            "Varying": ("V0", "V1"),
        }
        Rejected = tuple(
            frozenset((
                ("SharedA", "A0"),
                ("SharedB", "B0"),
                ("Varying", Value),
            ))
            for Value in ("V0", "V1")
        )
        Signal, Options = SelectExactNoGoodCspBranch(
            Domains,
            {"SharedA": frozenset((("SharedA", "A1"),))},
            Rejected,
            lambda Option: frozenset((
                (
                    "SharedB" if Option.startswith("B") else "Varying",
                    Option,
                ),
            )),
        )

        self.assertEqual(Signal, "Varying")
        self.assertEqual(Options, Domains[Signal])

    def testSingleExactNoGoodCoreBranchesOnEscapingLiteralFirst(self) -> None:
        Domains = {
            "Unrelated": ("U0",),
            "PortA": ("A0", "A1"),
            "PortB": ("B0", "B1"),
        }
        Rejected = (frozenset((("PortA", "A0"), ("PortB", "B0"))),)

        Signal, Options = SelectExactNoGoodCspBranch(
            Domains,
            {},
            Rejected,
            lambda Option: frozenset(((
                {"U": "Unrelated", "A": "PortA", "B": "PortB"}[
                    Option[0]
                ],
                Option,
            ),)),
        )

        self.assertEqual(Signal, "PortA")
        self.assertEqual(Options, ("A1", "A0"))

    def testBinaryArcPassExcludesHigherOrderExactClauses(self) -> None:
        Unary = frozenset((("A", "a0"),))
        Binary = frozenset((("A", "a0"), ("B", "b0")))
        HigherOrder = frozenset((
            ("A", "a0"),
            ("B", "b0"),
            ("C", "c0"),
        ))

        self.assertEqual(
            SelectBinaryExactNoGoodClauses((HigherOrder, Binary, Unary)),
            (Binary, Unary),
        )

    def testPhysicalPortExactPreferencePreservesDistinctOptions(self) -> None:
        def Port(Fingerprint, LocalX, AttachmentX):
            return PhysicalComponentPortReservation(
                Signal="sum",
                Direction="output",
                OwnedTerminals=((0, 0, 0),),
                OwnedTerminalFingerprints=("terminal",),
                OwnedCandidateFingerprints=(),
                FabricDomainFingerprint="fabric",
                FabricAttachment=(0, 0, 0),
                Attachment=(AttachmentX, 0, 0),
                LocalPath=((0, 0, 0), (LocalX, 0, 0)),
                GlobalPath=((AttachmentX, 0, 0), (AttachmentX + 1, 0, 0)),
                Claims=None,
                ReservationFingerprint=Fingerprint,
            )

        First = Port("reservation-a", 1, 4)
        Preferred = Port("reservation-b", 2, 4)
        OtherContract = Port("reservation-c", 3, 8)
        Options = (OtherContract, First, Preferred)

        Ordered = OrderPhysicalPortOptionsByPreferences(
            "sum",
            Options,
            {},
            {"sum": Preferred.ReservationFingerprint},
        )

        self.assertEqual(
            tuple(Value.ReservationFingerprint for Value in Ordered),
            ("reservation-b", "reservation-c", "reservation-a"),
        )
        self.assertCountEqual(Ordered, Options)
        self.assertEqual({id(Value) for Value in Ordered}, {
            id(Value) for Value in Options
        })

    def testPhysicalPortGlobalPreferenceOutranksExactPreference(self) -> None:
        def Port(Fingerprint, LocalX, AttachmentX):
            return PhysicalComponentPortReservation(
                Signal="carry",
                Direction="output",
                OwnedTerminals=((0, 0, 0),),
                OwnedTerminalFingerprints=("terminal",),
                OwnedCandidateFingerprints=(),
                FabricDomainFingerprint="fabric",
                FabricAttachment=(0, 0, 0),
                Attachment=(AttachmentX, 0, 0),
                LocalPath=((0, 0, 0), (LocalX, 0, 0)),
                GlobalPath=((AttachmentX, 0, 0), (AttachmentX + 1, 0, 0)),
                Claims=None,
                ReservationFingerprint=Fingerprint,
            )

        FirstGlobal = Port("reservation-a", 1, 4)
        SameGlobal = Port("reservation-b", 2, 4)
        ExactButDifferentGlobal = Port("reservation-c", 3, 8)

        Ordered = OrderPhysicalPortOptionsByPreferences(
            "carry",
            (ExactButDifferentGlobal, SameGlobal, FirstGlobal),
            {
                "carry": BuildPhysicalPortGlobalContractFingerprint(
                    FirstGlobal
                )
            },
            {"carry": ExactButDifferentGlobal.ReservationFingerprint},
        )

        self.assertEqual(
            tuple(Value.ReservationFingerprint for Value in Ordered),
            ("reservation-b", "reservation-a", "reservation-c"),
        )
        self.assertEqual(
            BuildPhysicalPortGlobalContractFingerprint(Ordered[0]),
            BuildPhysicalPortGlobalContractFingerprint(Ordered[1]),
        )

    def testPhysicalPortDecompositionDoesNotInventCartesianSupport(self) -> None:
        def Claims(Nodes):
            Values = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=Values,
                ElectricalCells=Values,
            )

        SeamSpecifications = (
            (1, 4, "l1-a1"),
            (2, 4, "l2-a1"),
            (2, 8, "l2-a2"),
        )
        LaneFactors = {"carry": tuple(
            PhysicalPortLaneFactor(
                Signal="carry",
                Direction="output",
                Capacity=1,
                OwnedTerminals=((0, 1, 0),),
                Domains=(),
                CandidateDomains=(),
                FabricDomainFingerprint="fabric",
                Seams=(PhysicalPortSeamFactor(
                    FabricAttachment=(0, 1, 0),
                    Attachment=(ApertureX, 1, 0),
                    LocalPath=((0, 1, 0), (LocalX, 1, 0)),
                    GlobalPath=(
                        (ApertureX, 1, 0),
                        (ApertureX + 1, 1, 0),
                    ),
                    Claims=Claims((
                        (0, 1, 0),
                        (LocalX, 1, 0),
                        (ApertureX, 1, 0),
                        (ApertureX + 1, 1, 0),
                    )),
                    SeamFingerprint=Name,
                ),),
                GuideCells=frozenset(),
                ExternalTerminals=(),
            )
            for LocalX, ApertureX, Name in SeamSpecifications
        )}
        Channel = PhysicalComponentChannelReservation(
            Signal="carry",
            Layer=0,
            GuideCells=(),
            ResourceIds=(),
            Claims=Claims(()),
            ReservationFingerprint="channel-carry",
        )
        ResourceGraph = SimpleNamespace(
            BuildRouteClaims=lambda Nodes: Claims(Nodes)
        )

        Local, Apertures, Supports = DecomposePhysicalPortLaneFactors(
            LaneFactors,
            (Channel,),
            ResourceGraph,
            FabricOrigin=(0, 1, 0),
        )
        ActualPairs = {
            (
                Value.LocalAccessFingerprint,
                Value.ApertureOptionFingerprint,
            )
            for Value in Supports[0][1]
        }

        self.assertEqual(len(Local[0][1]), 2)
        self.assertEqual(len(Apertures[0][1]), 2)
        self.assertEqual(len(ActualPairs), 3)
        self.assertLess(
            len(ActualPairs),
            len(Local[0][1]) * len(Apertures[0][1]),
        )
        LocalOne = next(
            Value for Value in Local[0][1]
            if Value.LocalPath[-1][0] == 1
        )
        ApertureTwo = next(
            Value for Value in Apertures[0][1]
            if Value.Attachment[0] == 8
        )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            MaterializePhysicalPortFactorPair(
                LocalOne,
                ApertureTwo,
                Supports[0][1],
                ResourceGraph,
            )

    def testPhysicalPortOptionOrderWithoutPreferencePreservesGeometryOrder(
        self,
    ) -> None:
        def Port(Fingerprint):
            return PhysicalComponentPortReservation(
                Signal="sum",
                Direction="output",
                OwnedTerminals=((0, 0, 0),),
                OwnedTerminalFingerprints=("terminal",),
                OwnedCandidateFingerprints=(),
                FabricDomainFingerprint="fabric",
                FabricAttachment=(0, 0, 0),
                Attachment=(4, 0, 0),
                LocalPath=((0, 0, 0),),
                GlobalPath=((4, 0, 0),),
                Claims=None,
                ReservationFingerprint=Fingerprint,
            )

        First, Second, Third = (
            Port("reservation-a"),
            Port("reservation-b"),
            Port("reservation-c"),
        )
        Forward = OrderPhysicalPortOptionsByPreferences(
            "sum", (Third, First, Second), {}, {}
        )
        Reverse = OrderPhysicalPortOptionsByPreferences(
            "sum", (Second, Third, First), {}, {}
        )

        self.assertEqual(Forward, (Third, First, Second))
        self.assertEqual(Reverse, (Second, Third, First))

    @patch(
        "PhysicalDesign.Routing.Regions.Proofs.Certification."
        "BuildPhysicalLocalPairProofContextFingerprint",
        return_value="local-proof-context",
    )
    def testLocalPairSupportIndexCompactsLargeRowWithoutChangingRejection(
        self,
        _ProofContext,
    ) -> None:
        ColumnContracts = tuple(
            f"local-column-{Index:03d}" for Index in range(144)
        )
        Preparation = SimpleNamespace(
            Complete=True,
            Feasible=True,
            DomainFingerprint="prepared-large-row",
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="resource",
            Problem=SimpleNamespace(
                Fabric=SimpleNamespace(FabricFingerprint="fabric"),
            ),
            AccessCertificate=SimpleNamespace(
                TechnologyFingerprint="technology",
            ),
            LocalAccessFactorsBySignal=(
                ("Row", (
                    SimpleNamespace(
                        LocalContractFingerprint="local-row"
                    ),
                )),
                ("Column", tuple(
                    SimpleNamespace(LocalContractFingerprint=Contract)
                    for Contract in ColumnContracts
                )),
            ),
        )
        Certificate = BuildPhysicalLocalPortPairSupportCertificate(
            Preparation,
            "solver-large-row",
            "Row",
            "local-row",
            "Column",
            ColumnContracts,
            "local-proof-context",
            _LocalPairProofRecords(
                "Column", ColumnContracts, "Row", "local-row"
            ),
        )

        Clauses = BuildPhysicalLocalPortPairUnsupportedIndex(
            (Certificate,),
            Preparation,
            "solver-large-row",
        )
        ExpectedClause = frozenset((
            ("Row", "local-row"),
            (
                "Column",
                "local-signal-domain:solver-large-row",
            ),
        ))

        self.assertEqual(Clauses, frozenset((ExpectedClause,)))
        self.assertEqual(len(Clauses), 1)
        for ColumnContract in ColumnContracts:
            ExactPairKeys = frozenset((
                ("Row", "local-row"),
                ("Column", ColumnContract),
                (
                    "Column",
                    "local-signal-domain:solver-large-row",
                ),
            ))
            self.assertTrue(ExpectedClause.issubset(ExactPairKeys))

        DifferentRowKeys = frozenset((
            ("Row", "local-other-row"),
            ("Column", ColumnContracts[0]),
            (
                "Column",
                "local-signal-domain:solver-large-row",
            ),
        ))
        self.assertFalse(ExpectedClause.issubset(DifferentRowKeys))

    def testCorridorCaptureRequiresClosedExactRequestCursor(self) -> None:
        Port = SimpleNamespace(
            Signal="PortA",
            Direction="output",
            Capacity=1,
            Attachment=(0, 2, 0),
            GlobalPath=((0, 2, 0),),
            ReservationFingerprint="reservation-a",
        )
        Nodes = frozenset(((0, 2, 0), (1, 2, 0)))
        Candidate = NetRouteCandidate(
            CandidateId="candidate-a",
            Signal="PortA",
            SourcePortalId="source",
            TargetPortalIds={},
            Nodes=Nodes,
            Edges=frozenset((((0, 2, 0), (1, 2, 0)),)),
            Claims=RoutingResourceClaims(WireCells=Nodes),
            Layer=0,
            Guide=frozenset(),
            RepeaterWaypoints=(),
            MaterialCost=2,
            FootprintGrowth=2,
            Length=2,
            BendCount=0,
            ViaCount=0,
        )
        Plan = SimpleNamespace(
            Ports=(Port,),
            ResourceGraphFingerprint="resource-graph",
            TechnologyFingerprint="technology",
        )
        Resources = SimpleNamespace(PhysicalPortCorridorDomainCache={})

        self.assertEqual(
            CaptureCompletePhysicalPortCorridorDomains(
                Plan,
                {"PortA": (Candidate,)},
                {"PortA": "request-domain"},
                {"PortA": 1},
                Resources,
            ),
            (),
        )
        Captured = CaptureCompletePhysicalPortCorridorDomains(
            Plan,
            {"PortA": (Candidate,)},
            {"PortA": "request-domain"},
            {"PortA": 0},
            Resources,
        )
        self.assertEqual(len(Captured), 1)
        self.assertTrue(Captured[0].Complete)
        self.assertIn(
            Captured[0].DomainFingerprint,
            Resources.PhysicalPortCorridorDomainCache,
        )

    def testPhysicalPortCorridorArcConsistencyUsesExactClaims(self) -> None:
        def Port(Signal, Attachment):
            return SimpleNamespace(
                Signal=Signal,
                Direction="output",
                Capacity=1,
                Attachment=Attachment,
                GlobalPath=(Attachment,),
                ReservationFingerprint=f"reservation-{Signal}",
            )

        def Candidate(Signal, CandidateId, Attachment, Extra):
            Nodes = frozenset((Attachment, Extra))
            return NetRouteCandidate(
                CandidateId=CandidateId,
                Signal=Signal,
                SourcePortalId="source",
                TargetPortalIds={},
                Nodes=Nodes,
                Edges=frozenset(((Attachment, Extra),)),
                Claims=RoutingResourceClaims(WireCells=Nodes),
                Layer=0,
                Guide=frozenset(),
                RepeaterWaypoints=(),
                MaterialCost=2,
                FootprintGrowth=2,
                Length=2,
                BendCount=0,
                ViaCount=0,
            )

        PortA = Port("PortA", (0, 2, 0))
        PortB = Port("PortB", (10, 2, 0))
        Domains = {
            "PortA": BuildPhysicalPortCorridorDomain(
                PortA,
                (
                    Candidate("PortA", "a-conflict", PortA.Attachment, (5, 2, 0)),
                    Candidate("PortA", "a-supported", PortA.Attachment, (6, 2, 0)),
                ),
                "request-a",
                "resource-graph",
                "technology",
                Complete=True,
            ),
            "PortB": BuildPhysicalPortCorridorDomain(
                PortB,
                (Candidate("PortB", "b-only", PortB.Attachment, (5, 2, 0)),),
                "request-b",
                "resource-graph",
                "technology",
                Complete=True,
            ),
        }

        Support = BuildPhysicalPortCorridorArcSupportIndex(Domains)
        Propagated, CheckCount, ProofComplete = (
            PropagatePhysicalPortCorridorArcConsistency(Domains)
        )

        self.assertTrue(Support)
        self.assertIsNotNone(Propagated)
        assert Propagated is not None
        self.assertEqual(
            [Value.RouteCandidateId for Value in Propagated["PortA"]],
            ["a-supported"],
        )
        self.assertGreater(CheckCount, 0)
        self.assertTrue(ProofComplete)
        Incomplete = {
            **Domains,
            "PortB": replace(Domains["PortB"], Complete=False),
        }
        _Values, _Checks, IncompleteProof = (
            PropagatePhysicalPortCorridorArcConsistency(Incomplete)
        )
        self.assertFalse(IncompleteProof)

    def testPhysicalPortCorridorReuseRequiresCompleteExactDependencies(
        self,
    ) -> None:
        def Port(ReservationFingerprint):
            return SimpleNamespace(
                Signal="A",
                Direction="output",
                Capacity=1,
                Attachment=(0, 2, 0),
                GlobalPath=((0, 2, 0),),
                ReservationFingerprint=ReservationFingerprint,
            )

        Candidate = NetRouteCandidate(
            CandidateId="candidate-a",
            Signal="A",
            SourcePortalId="source",
            TargetPortalIds={},
            Nodes=frozenset(((0, 2, 0), (1, 2, 0))),
            Edges=frozenset((((0, 2, 0), (1, 2, 0)),)),
            Claims=RoutingResourceClaims(
                WireCells=frozenset(((0, 2, 0), (1, 2, 0)))
            ),
            Layer=0,
            Guide=frozenset(),
            RepeaterWaypoints=(),
            MaterialCost=2,
            FootprintGrowth=2,
            Length=2,
            BendCount=0,
            ViaCount=0,
        )
        Domain = BuildPhysicalPortCorridorDomain(
            Port("old-local"),
            (Candidate,),
            "request-a",
            "graph-a",
            "technology-a",
            Complete=True,
        )
        Cache = {Domain.DomainFingerprint: Domain}
        CurrentShapes = {"A": (SimpleNamespace(
            Layer=0,
            SourcePortal=SimpleNamespace(
                PortalId="current-source",
                Path=((0, 2, 0),),
            ),
            TargetPortals=(),
        ),)}

        Reused = SelectReusablePhysicalPortCorridorCandidates(
            Cache,
            {"A": Port("new-local")},
            {"A": "request-a"},
            "graph-a",
            "technology-a",
            CurrentShapes,
        )
        self.assertEqual(tuple(Reused), ("A",))
        self.assertEqual(
            Reused["A"][0].SourcePortalId,
            "current-source",
        )
        self.assertEqual(Reused["A"][0].Nodes, Candidate.Nodes)
        for ChangedRequests, Graph, Technology, Cached in (
            ({"A": "request-b"}, "graph-a", "technology-a", Cache),
            ({"A": "request-a"}, "graph-b", "technology-a", Cache),
            ({"A": "request-a"}, "graph-a", "technology-b", Cache),
            (
                {"A": "request-a"},
                "graph-a",
                "technology-a",
                {
                    Domain.DomainFingerprint: replace(
                        Domain,
                        Complete=False,
                    )
                },
            ),
        ):
            self.assertEqual(
                SelectReusablePhysicalPortCorridorCandidates(
                    Cached,
                    {"A": Port("new-local")},
                    ChangedRequests,
                    Graph,
                    Technology,
                    CurrentShapes,
                ),
                {},
            )

    def testCoordinatedInitialWindowGrowsOnlyReportedSignal(
        self,
    ) -> None:
        self.assertEqual(
            SelectCoordinatedInitialRequestWindowLimit(
                8,
                128,
                2,
                1,
                False,
            ),
            8,
        )
        self.assertEqual(
            SelectCoordinatedInitialRequestWindowLimit(
                8,
                128,
                2,
                1,
                True,
            ),
            16,
        )
        self.assertEqual(
            SelectCoordinatedInitialRequestWindowLimit(
                8,
                12,
                2,
                2,
                True,
            ),
            12,
        )
        for Arguments in (
            (0, 1, 2, 1, True),
            (1, -1, 2, 1, True),
            (1, 1, 0, 1, True),
            (1, 1, 2, -1, True),
        ):
            with self.assertRaises(ValueError):
                SelectCoordinatedInitialRequestWindowLimit(*Arguments)

    def testClusterInterfacePatternAllowsMixedEndpointLayers(self) -> None:
        Signal = "MixedLayer"
        Root = (0, 1, 0)
        Target = (6, 1, 0)
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=RoutingResourceGraph(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
                SolidBlocks=frozenset(),
            ),
        )
        Source = self.BuildPortal(
            Signal,
            Root,
            (1, 1, 0),
            Layer=0,
        )
        Destination = self.BuildPortal(
            Signal,
            Target,
            (5, 3, 0),
            Layer=1,
        )
        Profile = NetRoutingProfile(
            Signal=Signal,
            Root=Root,
            Targets=(Target,),
            Span=6,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
        )

        _Reserved, Reservations = ReserveClusterBoundaryLeases(
            {
                (Signal, Root, 0): (Source,),
                (Signal, Target, 1): (Destination,),
            },
            {Signal: Profile},
            Resources,
        )

        self.assertEqual(
            {
                Reservation.Terminal: Reservation.Layer
                for Reservation in Reservations
            },
            {Root: 0, Target: 1},
        )

    def testCandidateRealizabilityNogoodSelectsAnotherAccessTemplate(
        self,
    ) -> None:
        Signal = "Crossing"
        Root = (0, 1, 0)
        First = (1, 1, 0)
        Second = (2, 1, 0)
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=RoutingResourceGraph(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
                SolidBlocks=frozenset(),
            ),
        )
        Profile = NetRoutingProfile(
            Signal=Signal,
            Root=Root,
            Targets=(),
            Span=0,
            Fanout=0,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={},
        )
        Portals = {
            (Signal, Root, 0): (
                self.BuildPortal(Signal, Root, First),
                self.BuildPortal(Signal, Root, Second),
            ),
        }
        _FirstReserved, FirstReservations = (
            ReserveClusterBoundaryLeases(
                Portals,
                {Signal: Profile},
                Resources,
            )
        )
        FirstFingerprint = BuildClusterLeaseSignalPatternFingerprint(
            FirstReservations,
            Signal,
        )
        _SecondReserved, SecondReservations = (
            ReserveClusterBoundaryLeases(
                Portals,
                {Signal: Profile},
                Resources,
                CandidateRealizabilityNogoods=(
                    ClusterLeaseCandidateRealizabilityNogood(
                        Signal=Signal,
                        PatternFingerprint=FirstFingerprint,
                        CandidateFailureFingerprint="candidate-empty",
                    ),
                ),
            )
        )

        self.assertNotEqual(
            BuildClusterLeaseSignalPatternFingerprint(
                SecondReservations,
                Signal,
            ),
            FirstFingerprint,
        )
        self.assertNotEqual(
            FirstReservations[0].FirstSegment,
            SecondReservations[0].FirstSegment,
        )

    def testCompleteInterfaceSkipsForbiddenOwnershipCombination(
        self,
    ) -> None:
        Signal = "Boundary"
        Root = (0, 1, 0)
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=RoutingResourceGraph(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
                SolidBlocks=frozenset(),
            ),
        )
        Profile = NetRoutingProfile(
            Signal=Signal,
            Root=Root,
            Targets=(),
            Span=0,
            Fanout=0,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={},
        )
        Portals = {
            (Signal, Root, 0): (
                self.BuildPortal(Signal, Root, (1, 1, 0)),
                self.BuildPortal(Signal, Root, (0, 1, 1)),
            ),
        }

        _FirstPortals, FirstReservations = (
            ReserveClusterBoundaryLeases(
                Portals,
                {Signal: Profile},
                Resources,
                RequireCompleteClusterInterfaceDomain=True,
            )
        )
        FirstFingerprint = (
            Resources.PreparedClusterInterfaceAssignment
            .OwnershipAssignmentFingerprint
        )
        _SecondPortals, SecondReservations = (
            ReserveClusterBoundaryLeases(
                Portals,
                {Signal: Profile},
                Resources,
                RequireCompleteClusterInterfaceDomain=True,
                ForbiddenOwnershipAssignmentFingerprints=frozenset((
                    FirstFingerprint,
                )),
            )
        )
        SecondFingerprint = (
            Resources.PreparedClusterInterfaceAssignment
            .OwnershipAssignmentFingerprint
        )

        self.assertNotEqual(FirstFingerprint, SecondFingerprint)
        self.assertNotEqual(
            FirstReservations[0].PortalId,
            SecondReservations[0].PortalId,
        )

    def testClusterInterfaceFailureReportsOnlyUnavoidablePair(
        self,
    ) -> None:
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=RoutingResourceGraph(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
                SolidBlocks=frozenset(),
            ),
        )
        Terminals = {
            "First": (10, 1, 0),
            "Second": (20, 1, 0),
            "Independent": (30, 1, 0),
        }
        Positions = {
            "First": (0, 1, 0),
            "Second": (0, 1, 0),
            "Independent": (8, 1, 0),
        }

        def Profile(Signal):
            Root = Terminals[Signal]
            return NetRoutingProfile(
                Signal=Signal,
                Root=Root,
                Targets=(),
                Span=0,
                Fanout=0,
                RetryCount=0,
                Criticality=1,
                IsTrunk=False,
                SourceAccessPath=(Root,),
                TargetAccessPaths={},
            )

        with self.assertRaises(RoutingStageError) as Context:
            ReserveClusterBoundaryLeases(
                {
                    (Signal, Terminal, 0): (
                        self.BuildPortal(
                            Signal,
                            Terminal,
                            Positions[Signal],
                        ),
                    )
                    for Signal, Terminal in Terminals.items()
                },
                {
                    Signal: Profile(Signal)
                    for Signal in Terminals
                },
                Resources,
            )

        self.assertEqual(
            Context.exception.Failure.AffectedNets,
            ("First", "Second"),
        )
        self.assertEqual(
            Context.exception.Failure.Diagnostics["ConflictGraph"][
                "PairwiseIncompatibleEdges"
            ],
            [["First", "Second"]],
        )

    def testClusterInterfaceFailureShrinksHigherOrderUnsatCore(
        self,
    ) -> None:
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=RoutingResourceGraph(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
                SolidBlocks=frozenset(),
            ),
        )
        Signals = ("First", "Second", "Third", "Independent")
        Terminals = {
            Signal: (10 * (Index + 1), 1, 0)
            for Index, Signal in enumerate(Signals)
        }

        def Profile(Signal):
            Root = Terminals[Signal]
            return NetRoutingProfile(
                Signal=Signal,
                Root=Root,
                Targets=(),
                Span=0,
                Fanout=0,
                RetryCount=0,
                Criticality=1,
                IsTrunk=False,
                SourceAccessPath=(Root,),
                TargetAccessPaths={},
            )

        with self.assertRaises(RoutingStageError) as Context:
            ReserveClusterBoundaryLeases(
                {
                    (Signal, Terminals[Signal], 0): tuple(
                        self.BuildPortal(
                            Signal,
                            Terminals[Signal],
                            Position,
                        )
                        for Position in (
                            ((0, 1, 0), (4, 1, 0))
                            if Signal != "Independent"
                            # Its first deterministic pattern conflicts with
                            # the known cut, while its second remains legal.
                            # Cut-first solving must defer that arbitrary
                            # frontier edge until selected-pattern validation.
                            else ((0, 1, 0), (8, 1, 0))
                        )
                    )
                    for Signal in Signals
                },
                {
                    Signal: Profile(Signal)
                    for Signal in Signals
                },
                Resources,
                MaximumExpansions=1_000,
                PriorityInterfaceCutSignals=frozenset((
                    "First",
                    "Second",
                    "Third",
                )),
            )

        Failure = Context.exception.Failure
        self.assertEqual(
            frozenset(Failure.AffectedNets),
            frozenset(("First", "Second", "Third")),
        )
        Search = Failure.Diagnostics["ClusterInterfacePatternSearch"]
        self.assertEqual(Search["UnavoidablePairEdges"], [])
        self.assertEqual(
            frozenset(Search["ReducedHigherOrderCore"]),
            frozenset(("First", "Second", "Third")),
        )
        self.assertTrue(Search["CoreShrinkComplete"])
        self.assertGreater(Search["CoreShrinkExpansionCount"], 0)
        self.assertEqual(
            Search["PriorityInterfaceCutSignals"],
            ["First", "Second", "Third"],
        )
        self.assertTrue(Search["DeferredInitialFrontierEdges"])

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

    def testPhysicalAssemblyPlanningCorridorsRaiseTheLayerFloor(self) -> None:
        Corridors = (
            SimpleNamespace(Signal="PortA", Layer=1),
            SimpleNamespace(
                Signal="ForeignFeedthrough",
                Layer=5,
                FeedthroughComponentIds=(7,),
            ),
        )
        Plan = SimpleNamespace(
            PlanFingerprint="assembly-a",
            Channels=(),
            Corridors=Corridors,
            PlanningChannels=Corridors,
        )

        self.assertEqual(
            RequiredPhysicalAssemblyRoutingLayerCount(Plan),
            6,
        )
        RequiredLayerCount = RequiredPhysicalAssemblyRoutingLayerCount(Plan)
        ValidatePhysicalAssemblyRoutingLayerLimit(
            Plan,
            RequiredLayerCount=RequiredLayerCount,
            EffectiveMaximumLayerCount=6,
            PolicyMaximumLayerCount=8,
            TechnologyMaximumLayerCount=8,
        )
        self.assertEqual(
            SelectInitialRoutingLayerCount(
                MinimumLayerCount=3,
                EffectiveMaximumLayerCount=max(3, RequiredLayerCount),
                RequiredAccessLayerCount=max(3, RequiredLayerCount),
                AdaptiveLayerCount=3,
                AdaptiveLayerFloor=0,
                NegotiatedLayerFloor=0,
                ExistingRouteLayerCount=1,
                PlacementWasRelocated=False,
                ForceMaximumAfterPlacementRelocation=False,
            ),
            RequiredLayerCount,
        )

    def testRouteTreeBatchCompletionMaskPreservesNonPrefixWork(
        self,
    ) -> None:
        Batch = SimpleNamespace(
            CompletionMask=(False, True, False, True),
            CompletedWork=2,
            DeadlineExceeded=True,
        )

        self.assertEqual(
            ReadRouteTreeBatchCompletionMask(Batch, 4),
            (False, True, False, True),
        )

    def testPhysicalAssemblyLayerOutsidePolicyFailsTypedAndEarly(self) -> None:
        Corridors = (SimpleNamespace(Signal="PortA", Layer=4),)
        Plan = SimpleNamespace(
            PlanFingerprint="assembly-b",
            Channels=(),
            Corridors=Corridors,
            PlanningChannels=Corridors,
        )

        with self.assertRaises(RoutingStageError) as Context:
            ValidatePhysicalAssemblyRoutingLayerLimit(
                Plan,
                RequiredLayerCount=(
                    RequiredPhysicalAssemblyRoutingLayerCount(Plan)
                ),
                EffectiveMaximumLayerCount=4,
                PolicyMaximumLayerCount=4,
                TechnologyMaximumLayerCount=8,
            )

        Failure = Context.exception.Failure
        self.assertEqual(
            Failure.Reason,
            RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
        )
        self.assertEqual(Failure.Stage, "PhysicalComponentGlobalPlanning")
        self.assertEqual(Failure.AffectedNets, ("PortA",))
        self.assertEqual(
            Failure.Diagnostics["RequiredPhysicalAssemblyLayerCount"],
            5,
        )
        self.assertTrue(
            Failure.Diagnostics["GlobalPlanDomainComplete"]
        )

    def testExplicitInterfaceDeckExtendsOnlyTheHierarchicalMaximum(
        self,
    ) -> None:
        Corridors = (SimpleNamespace(Signal="PortA", Layer=3),)
        Plan = SimpleNamespace(
            PlanFingerprint="assembly-deck",
            Channels=(),
            Corridors=Corridors,
            PlanningChannels=Corridors,
        )

        self.assertEqual(
            SelectHierarchicalRoutingMaximumLayerCount(
                PolicyLayerLimit=3,
                TechnologyMaximumLayerCount=8,
                InterfaceDeckLayer=None,
                Plan=None,
            ),
            3,
        )
        AuthorizedMaximum = SelectHierarchicalRoutingMaximumLayerCount(
            PolicyLayerLimit=3,
            TechnologyMaximumLayerCount=8,
            InterfaceDeckLayer=3,
            Plan=Plan,
        )
        self.assertEqual(AuthorizedMaximum, 4)
        self.assertEqual(
            SelectHierarchicalRoutingMaximumLayerCount(
                PolicyLayerLimit=3,
                TechnologyMaximumLayerCount=8,
                InterfaceDeckLayer=None,
                Plan=Plan,
            ),
            4,
        )
        ValidatePhysicalAssemblyRoutingLayerLimit(
            Plan,
            RequiredLayerCount=4,
            EffectiveMaximumLayerCount=AuthorizedMaximum,
            PolicyMaximumLayerCount=3,
            TechnologyMaximumLayerCount=8,
        )

        with self.assertRaises(RoutingStageError) as Context:
            SelectHierarchicalRoutingMaximumLayerCount(
                PolicyLayerLimit=3,
                TechnologyMaximumLayerCount=4,
                InterfaceDeckLayer=4,
                Plan=SimpleNamespace(
                    PlanFingerprint="assembly-deck",
                    Channels=(),
                    Corridors=(
                        SimpleNamespace(Signal="PortA", Layer=4),
                    ),
                    PlanningChannels=(
                        SimpleNamespace(Signal="PortA", Layer=4),
                    ),
                ),
            )
        Failure = Context.exception.Failure
        self.assertEqual(
            Failure.Reason,
            RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
        )
        self.assertEqual(Failure.AffectedNets, ("PortA",))
        self.assertEqual(
            Failure.Diagnostics["InterfaceDeckAuthorization"],
            "rejected-by-technology",
        )

    def testPhysicalAssemblyExactAttachmentMustBeGloballyVisible(self) -> None:
        Attachment = (4, 3, 7)
        GlobalPath = (Attachment, (5, 3, 7))
        Port = SimpleNamespace(
            Signal="PortA",
            Attachment=Attachment,
            GlobalPath=GlobalPath,
            ReservationFingerprint="port-a",
        )
        Plan = SimpleNamespace(
            PlanFingerprint="assembly-c",
            Ports=(Port,),
            Channels=(),
            Corridors=(SimpleNamespace(Signal="PortA", Layer=1),),
            PlanningChannels=(
                SimpleNamespace(Signal="PortA", Layer=1),
            ),
        )
        Profile = SimpleNamespace(Root=Attachment, Targets=((9, 3, 7),))
        ExactPortal = replace(
            self.BuildPortal("PortA", Attachment, Attachment, Layer=1),
            PortalId=BuildPhysicalComponentGlobalPortalId(Port, 1),
            Path=GlobalPath,
        )

        Diagnostics = ValidatePhysicalComponentExactAttachmentPortals(
            {"PortA": Profile},
            {("PortA", Attachment, 1): (ExactPortal,)},
            Plan,
            LayerCount=2,
        )
        self.assertTrue(
            Diagnostics["AllDeclaredExactAttachmentsVisible"]
        )
        self.assertTrue(
            Diagnostics["ExactAttachmentValidationFingerprint"]
        )

        with self.assertRaises(RoutingStageError) as Context:
            ValidatePhysicalComponentExactAttachmentPortals(
                {"PortA": Profile},
                {},
                Plan,
                LayerCount=2,
            )
        Failure = Context.exception.Failure
        self.assertEqual(
            Failure.Reason,
            RoutingFailureReason.ComponentAssemblyIdentityMismatch,
        )
        self.assertEqual(
            Failure.Diagnostics["VisibleExactAttachmentCount"],
            0,
        )
        self.assertEqual(
            Failure.Diagnostics["MissingExactAttachments"][0]["Problems"],
            ["exact-portal-not-visible"],
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

    def testPhysicalAssemblyGuideContractIgnoresLocalOnlyPortState(
        self,
    ) -> None:
        def Port(LocalFingerprint, Attachment=(4, 2, 6)):
            return SimpleNamespace(
                Signal="A",
                Direction="output",
                Attachment=Attachment,
                GlobalPath=(Attachment, (Attachment[0] + 1, 2, 6)),
                Capacity=1,
                ReservationFingerprint=LocalFingerprint,
                LocalPath=((0, 2, 0), Attachment),
                OwnedCandidateFingerprints=(LocalFingerprint,),
            )

        def Plan(PortValue, ChannelFingerprint="channel-a"):
            return SimpleNamespace(
                GlobalKeepoutFingerprint="keepout",
                Ports=(PortValue,),
                PlanningChannels=(SimpleNamespace(
                    Signal="A",
                    ReservationFingerprint=ChannelFingerprint,
                ),),
                PlanFingerprint=PortValue.ReservationFingerprint,
            )

        First = BuildPhysicalAssemblyGuideContractFingerprint(
            Plan(Port("local-a"))
        )
        LocalOnlyChange = BuildPhysicalAssemblyGuideContractFingerprint(
            Plan(Port("local-b"))
        )
        GlobalPortChange = BuildPhysicalAssemblyGuideContractFingerprint(
            Plan(Port("local-c", Attachment=(5, 2, 6)))
        )
        ChannelChange = BuildPhysicalAssemblyGuideContractFingerprint(
            Plan(Port("local-a"), ChannelFingerprint="channel-b")
        )

        self.assertEqual(First, LocalOnlyChange)
        self.assertNotEqual(First, GlobalPortChange)
        self.assertNotEqual(First, ChannelChange)

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

    def testFirstLegalSkipsResultOnlyShapeOptimization(self) -> None:
        self.assertFalse(ShouldRunShapeOptimization("first-legal"))
        self.assertTrue(ShouldRunShapeOptimization("best-quality"))

    def testIndexedResourcesAreDeterministic(self) -> None:
        Graph, Region, _Context = self.BuildGraph()

        First = Graph.BuildIndexedGraph(Region)
        Second = Graph.BuildIndexedGraph(Region)

        self.assertEqual(First.ResourcePositions, Second.ResourcePositions)
        self.assertEqual(First.PositionIndices, Second.PositionIndices)

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

    def testNativeRepeaterReservationsBypassPythonPathHeuristic(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Nodes = tuple((X, 1, 0) for X in range(21))
        Region = RoutingGraphRegion(
            (0, 20, 1, 1, 0, 0),
            frozenset(Nodes),
            frozenset(),
        )
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Root, Target = Nodes[0], Nodes[-1]
        Profile = NetRoutingProfile(
            Signal="N",
            Root=Root,
            Targets=(Target,),
            Span=20,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
        )
        Diagnostics = {}
        with patch(
            'PhysicalDesign.Routing.Global.Ports.Portals._ReserveRepeaters',
            return_value=((), {}),
        ):
            Candidate = _MaterializeCandidate(
                "N",
                Profile,
                self.BuildPortal("N", Root, Root),
                (self.BuildPortal("N", Target, Target),),
                frozenset(),
                0,
                "X",
                0,
                0,
                list(Nodes),
                Region,
                Resources,
                DefaultRedstoneRoutingTechnology,
                1,
                NativeRepeaterReservations=(((13, 1, 0), "west"),),
                MaterializationDiagnostics=Diagnostics,
            )

        self.assertIsNotNone(Candidate)
        self.assertEqual(Diagnostics["Status"], "accepted")
        self.assertFalse(Diagnostics["FallbackUsed"])
        self.assertEqual(Diagnostics["PoweredTargetCount"], 1)

    def testInvalidNativeRepeaterFallsBackToPhysicalReservation(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Nodes = tuple((X, 1, 0) for X in range(21))
        Region = RoutingGraphRegion(
            (0, 20, 1, 1, 0, 0),
            frozenset(Nodes),
            frozenset(),
        )
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Root, Target = Nodes[0], Nodes[-1]
        Profile = NetRoutingProfile(
            Signal="N",
            Root=Root,
            Targets=(Target,),
            Span=20,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
        )
        Diagnostics = {}
        Candidate = _MaterializeCandidate(
            "N",
            Profile,
            self.BuildPortal("N", Root, Root),
            (self.BuildPortal("N", Target, Target),),
            frozenset(),
            0,
            "X",
            0,
            0,
            list(Nodes),
            Region,
            Resources,
            DefaultRedstoneRoutingTechnology,
            1,
            NativeRepeaterReservations=(((13, 1, 0), "north"),),
            MaterializationDiagnostics=Diagnostics,
        )

        self.assertIsNotNone(Candidate)
        self.assertEqual(Diagnostics["Status"], "accepted")
        self.assertTrue(Diagnostics["FallbackUsed"])
        self.assertFalse(Diagnostics["NativeGeometryValid"])

    def testPoweredNativeSubsetIgnoresRedundantInvalidReservation(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Nodes = tuple((X, 1, 0) for X in range(21))
        Region = RoutingGraphRegion(
            (0, 20, 1, 1, 0, 0),
            frozenset(Nodes),
            frozenset(),
        )
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Root, Target = Nodes[0], Nodes[-1]
        Profile = NetRoutingProfile(
            Signal="N",
            Root=Root,
            Targets=(Target,),
            Span=20,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
        )
        Diagnostics = {}
        with patch(
            'PhysicalDesign.Routing.Global.Ports.Portals._ReserveRepeaters',
            return_value=((), {}),
        ):
            Candidate = _MaterializeCandidate(
                "N",
                Profile,
                self.BuildPortal("N", Root, Root),
                (self.BuildPortal("N", Target, Target),),
                frozenset(),
                0,
                "X",
                0,
                0,
                list(Nodes),
                Region,
                Resources,
                DefaultRedstoneRoutingTechnology,
                1,
                NativeRepeaterReservations=(
                    ((10, 1, 0), "north"),
                    ((13, 1, 0), "west"),
                ),
                MaterializationDiagnostics=Diagnostics,
            )

        self.assertIsNotNone(Candidate)
        self.assertFalse(Diagnostics["NativeGeometryValid"])
        self.assertTrue(Diagnostics["NativePowerValid"])
        self.assertFalse(Diagnostics["FallbackUsed"])

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

    def testSourceConnectedTargetBranchIsOmittedFromNativePayload(self) -> None:
        Graph, _Region, _Context = self.BuildGraph()
        Root = (0, 1, 0)
        ConnectedBranch = (
            (2, 1, 0),
            (1, 1, 0),
            Root,
        )
        DisconnectedBranch = ((4, 1, 0),)

        Actual = FilterSourceConnectedTargetBranches(
            Root,
            (Root, (1, 1, 0), (2, 1, 0)),
            (ConnectedBranch, DisconnectedBranch),
            Graph,
        )

        self.assertEqual(Actual, (DisconnectedBranch,))

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
            'PhysicalDesign.Routing.Global.Negotiation.NegotiatedTrees.GetRustRoutingThreadCount',
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

    def testFallbackRepeatersDoNotReplaceBranchingDust(self) -> None:
        Trunk = tuple((Index, 1, 0) for Index in range(31))
        Branch = tuple((13, 1, Index) for Index in range(1, 21))
        Nodes = set((*Trunk, *Branch))
        Graph = {
            Position: sorted(
                Neighbor
                for Neighbor in Nodes
                if (
                    abs(Neighbor[0] - Position[0])
                    + abs(Neighbor[1] - Position[1])
                    + abs(Neighbor[2] - Position[2])
                    == 1
                )
            )
            for Position in Nodes
        }
        Targets = (Trunk[-1], Branch[-1])

        Reservations, _Paths = _ReserveRepeaters(
            "A",
            Trunk[0],
            Targets,
            Graph,
            DefaultRedstoneRoutingTechnology,
        )
        Repeaters = {
            Reservation.Position: Reservation.InputFacing
            for Reservation in Reservations
        }
        Powers = PropagateRoutePower(Trunk[0], Graph, Repeaters)

        self.assertNotIn((13, 1, 0), Repeaters)
        self.assertTrue(all(Powers.get(Target, 0) > 0 for Target in Targets))

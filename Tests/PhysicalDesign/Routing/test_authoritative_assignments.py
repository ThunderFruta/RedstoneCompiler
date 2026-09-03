"""Assignments contracts for authoritative routing."""

from ._authoritative_planner_contracts import *


class AuthoritativeAssignmentsTests(AuthoritativePlannerTestBase):
    def testPhysicalRouteFactorPrunesOnlyCertifiedDisconnectedGuides(
        self,
    ) -> None:
        Adjacency = {
            (0, 2, 0): ((1, 2, 0),),
            (1, 2, 0): ((0, 2, 0), (2, 2, 0)),
            (2, 2, 0): ((1, 2, 0),),
        }
        Nodes = frozenset(Adjacency)
        Required = frozenset(((0, 2, 0), (2, 2, 0)))
        self.assertTrue(
            PhysicalRouteRequestFactorHasNecessaryConnectivity(
                Adjacency,
                Nodes,
                Required,
                frozenset(),
                frozenset(((1, 0),)),
            )
        )
        self.assertFalse(
            PhysicalRouteRequestFactorHasNecessaryConnectivity(
                Adjacency,
                Nodes,
                Required,
                frozenset(((1, 2, 0),)),
                frozenset(((1, 0),)),
            )
        )
        self.assertTrue(
            PhysicalRouteRequestFactorHasNecessaryConnectivity(
                Adjacency,
                Nodes,
                frozenset((*Required, (9, 2, 9))),
                frozenset(),
                frozenset(),
            )
        )

    def testPhysicalAssignmentArcPassDropsStaleConflictWitnesses(
        self,
    ) -> None:
        Telemetry = {
            "EmptySignals": ["Old"],
            "BlockerSignalsByEmptySignal": {"Old": ["Peer"]},
            "EncodingRemovedSignal": True,
            "CompatibilityCheckCount": 17,
        }

        BeginPhysicalAssignmentArcPass(Telemetry)

        self.assertEqual(Telemetry, {"CompatibilityCheckCount": 17})

    def testTopologyPressureStagingCanReusePriorCandidateDomains(
        self,
    ) -> None:
        Arguments = {
            "ApplyMaturePortfolioSearchCaps": True,
            "CandidateDiversityLevel": 0,
            "ReservationVariant": 0,
            "LaneDiversityLevel": 0,
            "SkipStrictPortalReservation": False,
            "RetainedCandidateCachePresent": False,
            "PriorCandidateCachePresent": True,
        }

        self.assertFalse(
            ShouldUseMatureStagedInitialCandidateScheduler(
                **Arguments,
            )
        )
        self.assertTrue(
            ShouldUseMatureStagedInitialCandidateScheduler(
                **Arguments,
                AllowPriorCandidateCache=True,
            )
        )

    def testExactCompletionRanksMissingDomainBeforeBlockedDomain(
        self,
    ) -> None:
        MissingKey = ExactAssignmentCompletionSignalOrderKey(
            "Missing",
            frozenset({"Missing"}),
            0,
            81,
            0,
            0,
        )
        BlockedKey = ExactAssignmentCompletionSignalOrderKey(
            "Blocked",
            frozenset({"Missing"}),
            0,
            81,
            12,
            2,
        )

        self.assertLess(MissingKey, BlockedKey)

    def testExactCompletionRanksScarceDomainBeforeFrequentWideDomain(
        self,
    ) -> None:
        ScarceKey = ExactAssignmentCompletionSignalOrderKey(
            "Scarce",
            frozenset(),
            8,
            1,
            0,
            1,
        )
        FrequentWideKey = ExactAssignmentCompletionSignalOrderKey(
            "FrequentWide",
            frozenset(),
            0,
            81,
            12,
            4,
        )

        self.assertLess(ScarceKey, FrequentWideKey)

    def testExactCompletionFavorsScarceCutDomainAfterMinimumProbe(
        self,
    ) -> None:
        self.assertEqual(
            SelectExactAssignmentCompletionCutWideRequests(
                ["Scarce", "Wide"],
                {
                    "Scarce": list(range(8)),
                    "Wide": list(range(10, 18)),
                },
                8,
                {"Scarce": 1, "Wide": 8},
            ),
            (
                ("Scarce", 0),
                ("Wide", 10),
                ("Scarce", 1),
                ("Wide", 11),
                ("Scarce", 2),
                ("Scarce", 3),
                ("Scarce", 4),
                ("Scarce", 5),
            ),
        )

    def testExpandedPassZeroCandidateEntersExactAssignmentDomain(
        self,
    ) -> None:
        Candidate = self.BuildCandidate(
            "SignalA",
            "expanded-candidate",
            (1, 1, 1),
        )
        CandidateOptions = {}

        self.assertTrue(RetainNegotiatedInitialCandidateOption(
            CandidateOptions,
            "SignalA",
            Candidate,
            0,
        ))
        self.assertIs(
            CandidateOptions["SignalA"]["expanded-candidate"],
            Candidate,
        )
        self.assertFalse(RetainNegotiatedInitialCandidateOption(
            CandidateOptions,
            "SignalA",
            self.BuildCandidate(
                "SignalA",
                "later-candidate",
                (2, 1, 1),
            ),
            1,
        ))
        self.assertNotIn("later-candidate", CandidateOptions["SignalA"])

    def testCompleteClusterLeaseOwnsOnlyDenseComponentSignals(
        self,
    ) -> None:
        self.assertEqual(
            SelectClusterLeaseOwnershipSignals(
                ("Boundary", "InternalGlobal", "Output"),
                ("Boundary", "Output"),
                True,
                ("Boundary", "Output"),
            ),
            frozenset(("Boundary", "Output")),
        )
        self.assertEqual(
            SelectClusterLeaseOwnershipSignals(
                ("Boundary", "InternalGlobal"),
                ("Boundary",),
                False,
                ("Boundary",),
            ),
            frozenset(("Boundary",)),
        )
        self.assertEqual(
            SelectClusterLeaseOwnershipSignals(
                ("Boundary", "InternalGlobal"),
                ("Boundary",),
                True,
                (),
            ),
            frozenset(("Boundary",)),
        )
        self.assertEqual(
            SelectClusterLeaseOwnershipSignals(
                ("Ordinary",),
                (),
                True,
                ("Ordinary",),
            ),
            frozenset(),
        )

    def testCandidateDomainPairScanRequiresPostHigherOrderInitialState(
        self,
    ) -> None:
        Arguments = {
            "AdaptiveRoutingEnabled": True,
            "PlacementWasRelocated": True,
            "ExactLegalRetainedJointStateCount": 2,
            "JointHigherOrderConstraintCount": 1,
            "StarvedSignal": "Propagate1",
            "JointHigherOrderConstraintSignals": frozenset({
                "A0",
                "Propagate1",
            }),
            "CandidateDiversityLevel": 0,
            "ReservationVariant": 0,
            "LaneDiversityLevel": 0,
            "SkipStrictPortalReservation": False,
            "MaximumCandidateDiversityEscalations": 4,
        }
        self.assertTrue(
            ShouldScanCandidateDomainPairCut(**Arguments)
        )
        for Key, Value in (
            ("AdaptiveRoutingEnabled", False),
            ("PlacementWasRelocated", False),
            ("ExactLegalRetainedJointStateCount", 1),
            ("JointHigherOrderConstraintCount", 0),
            ("StarvedSignal", "NandNet0"),
            ("JointHigherOrderConstraintSignals", frozenset({"A0"})),
            ("CandidateDiversityLevel", 1),
            ("ReservationVariant", 1),
            ("LaneDiversityLevel", 1),
            ("SkipStrictPortalReservation", True),
            ("MaximumCandidateDiversityEscalations", 1),
        ):
            with self.subTest(Key=Key):
                self.assertFalse(
                    ShouldScanCandidateDomainPairCut(
                        **{
                            **Arguments,
                            Key: Value,
                        }
                    )
                )

    def testCandidateDomainPairScanMembershipIsRenameAndOrderIndependent(
        self,
    ) -> None:
        Common = {
            "AdaptiveRoutingEnabled": True,
            "PlacementWasRelocated": True,
            "ExactLegalRetainedJointStateCount": 6,
            "JointHigherOrderConstraintCount": 1,
            "CandidateDiversityLevel": 0,
            "ReservationVariant": 0,
            "LaneDiversityLevel": 0,
            "SkipStrictPortalReservation": False,
            "MaximumCandidateDiversityEscalations": 4,
        }
        self.assertTrue(ShouldScanCandidateDomainPairCut(
            **Common,
            StarvedSignal="Propagate1",
            JointHigherOrderConstraintSignals=frozenset((
                "A0",
                "Propagate1",
                "CarryOut",
            )),
        ))
        self.assertTrue(ShouldScanCandidateDomainPairCut(
            **Common,
            StarvedSignal="Signal17",
            JointHigherOrderConstraintSignals=frozenset((
                "Signal91",
                "Signal63",
                "Signal17",
            )),
        ))
        self.assertFalse(ShouldScanCandidateDomainPairCut(
            **Common,
            StarvedSignal="Signal22",
            JointHigherOrderConstraintSignals=frozenset((
                "Signal17",
                "Signal63",
                "Signal91",
            )),
        ))

    def testCandidateDomainPairExpansionLookupIsSignalScoped(
        self,
    ) -> None:
        History = (
            {
                "CandidateDomainPairExpansion": True,
                "AffectedSignals": ["A", "B", "Starved"],
                "CandidateFailureFingerprint": "first-failure",
                "Marker": "first",
            },
            {
                "CandidateDomainPairExpansion": True,
                "AffectedSignals": ["C", "Other"],
                "Marker": "latest-other",
            },
        )

        self.assertEqual(
            FindPriorCandidateDomainPairExpansion(
                History,
                "Starved",
            )["Marker"],
            "first",
        )
        self.assertIsNone(
            FindPriorCandidateDomainPairExpansion(
                tuple(reversed(History)),
                "Missing",
            )
        )
        self.assertIsNone(
            FindPriorCandidateDomainPairExpansion(
                History,
                "Starved",
                "different-failure",
            )
        )
        self.assertEqual(
            FindPriorCandidateDomainPairExpansion(
                History,
                "Starved",
                "first-failure",
            )["Marker"],
            "first",
        )

    def testCandidateDomainPairScanSliceIsPrivateAndRemainingAware(
        self,
    ) -> None:
        self.assertEqual(
            SelectCandidateDomainPairScanSliceSeconds(20.0),
            0.5,
        )
        self.assertEqual(
            SelectCandidateDomainPairScanSliceSeconds(4.0),
            0.2,
        )
        self.assertEqual(
            SelectCandidateDomainPairScanSliceSeconds(0.0),
            0.0,
        )
        with self.assertRaises(ValueError):
            SelectCandidateDomainPairScanSliceSeconds(
                8.0,
                MaximumSliceSeconds=0.0,
            )

    def testPhysicalAssignmentIndexExtendsEveryClaimCategory(self) -> None:
        Existing = (4, 1, 4)
        Wire = (3, 1, 4)
        Support = (3, 0, 4)
        RequiredAir = (3, 2, 4)
        Electrical = (2, 1, 4)
        Indexed = IndexedRoutingResourceGraph(
            ResourcePositions=(Existing,),
            PositionIndices={Existing: 0},
        )

        Extended = ExtendIndexedRoutingResourceGraph(
            Indexed,
            (RoutingResourceClaims(
                WireCells=frozenset({Wire}),
                SupportCells=frozenset({Support}),
                RequiredAirCells=frozenset({RequiredAir}),
                ElectricalCells=frozenset({Electrical}),
            ),),
        )

        Expected = tuple(sorted({
            Existing,
            Wire,
            Support,
            RequiredAir,
            Electrical,
        }))
        self.assertEqual(Extended.ResourcePositions, Expected)
        self.assertEqual(
            Extended.PositionIndices,
            {
                Position: Index
                for Index, Position in enumerate(Expected)
            },
        )

    def testPhysicalAssignmentIndexExtensionIsClaimOrderInvariant(
        self,
    ) -> None:
        Indexed = IndexedRoutingResourceGraph(
            ResourcePositions=((9, 1, 0),),
            PositionIndices={(9, 1, 0): 0},
        )
        Alpha = RoutingResourceClaims(
            WireCells=frozenset({(2, 1, 0)}),
            ElectricalCells=frozenset({(1, 1, 0)}),
        )
        Beta = RoutingResourceClaims(
            SupportCells=frozenset({(7, 0, 0)}),
            RequiredAirCells=frozenset({(7, 2, 0)}),
        )

        Forward = ExtendIndexedRoutingResourceGraph(
            Indexed,
            (Alpha, Beta),
        )
        Reversed = ExtendIndexedRoutingResourceGraph(
            Indexed,
            (Beta, Alpha),
        )

        self.assertEqual(Forward, Reversed)
        self.assertEqual(
            Forward.ResourcePositions,
            tuple(sorted(Forward.ResourcePositions)),
        )

    def testPhysicalAssignmentIndexExtensionReusesCompleteIndex(
        self,
    ) -> None:
        Positions = (
            (0, 0, 0),
            (0, 1, 0),
            (0, 2, 0),
            (1, 1, 0),
        )
        Indexed = IndexedRoutingResourceGraph(
            ResourcePositions=Positions,
            PositionIndices={
                Position: Index
                for Index, Position in enumerate(Positions)
            },
        )
        Claims = RoutingResourceClaims(
            WireCells=frozenset({(0, 1, 0)}),
            SupportCells=frozenset({(0, 0, 0)}),
            RequiredAirCells=frozenset({(0, 2, 0)}),
            ElectricalCells=frozenset({(1, 1, 0)}),
        )

        Extended = ExtendIndexedRoutingResourceGraph(Indexed, (Claims,))

        self.assertIs(Extended, Indexed)

    def testPriorityRelocationIncludesFailureMinimumDomainNeighbors(
        self,
    ) -> None:
        ConflictGraph = {
            "Classification": "relocated-multi-pair-conflict",
            "FailureNet": "Failure",
            "CandidateCounts": {
                "Failure": 22,
                "FixedA": 1,
                "FixedB": 1,
                "Wide": 12,
                "UnrelatedFixed": 1,
            },
            "PairwiseIncompatibleEdges": [
                ["Failure", "FixedA"],
                ["Failure", "Wide"],
                ["FixedB", "Failure"],
                ["Wide", "UnrelatedFixed"],
            ],
            "ConflictSignals": [
                "Failure",
                "FixedA",
                "FixedB",
                "Wide",
                "UnrelatedFixed",
            ],
        }
        self.assertEqual(
            SelectPriorityPlacementRelocationSignals(ConflictGraph),
            ["Failure", "FixedA", "FixedB"],
        )
        RenamedAndReordered = {
            **ConflictGraph,
            "FailureNet": "RenamedFailure",
            "CandidateCounts": {
                "RenamedWide": 12,
                "RenamedFixedB": 1,
                "RenamedFailure": 22,
                "RenamedFixedA": 1,
            },
            "PairwiseIncompatibleEdges": [
                ["RenamedFixedB", "RenamedFailure"],
                ["RenamedWide", "RenamedFailure"],
                ["RenamedFailure", "RenamedFixedA"],
            ],
        }
        self.assertEqual(
            set(SelectPriorityPlacementRelocationSignals(
                RenamedAndReordered
            )),
            {
                "RenamedFailure",
                "RenamedFixedA",
                "RenamedFixedB",
            },
        )

    def testHigherOrderRetriesUseNativeOffendersAfterDomainCoverage(self) -> None:
        self.assertEqual(
            SelectCandidateRegenerationCoverSignals(
                {
                    "Classification": "relocated-higher-order-conflict",
                    "CandidateCounts": {
                        "A": 12,
                        "B": 8,
                        "C": 20,
                    },
                    "NativeConflictSignals": ["A", "B"],
                    "ConflictSignals": ["A", "B", "C"],
                    "PairwiseIncompatibleEdges": [],
                },
                frozenset({"A", "B", "C"}),
                frozenset({"A", "B", "C"}),
            ),
            ["B", "A"],
        )

    def testPartialAssignmentAvoidanceExcludesRegeneratedSignals(self) -> None:
        First = NetRouteCandidate(
            Signal="A",
            CandidateId="A0",
            SourcePortalId="A-source",
            TargetPortalIds={},
            Layer=0,
            Guide=frozenset(),
            Nodes=frozenset({(1, 1, 1)}),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(1, 1, 1)}),
            ),
            RepeaterWaypoints=(),
            Length=1,
            BendCount=0,
            ViaCount=0,
            MaterialCost=1,
            FootprintGrowth=0,
        )
        Second = replace(
            First,
            Signal="B",
            CandidateId="B0",
            Nodes=frozenset({(2, 1, 2)}),
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(2, 1, 2)}),
            ),
        )
        self.assertEqual(
            SelectPartialAssignmentAvoidancePositions(
                [("A", "A0"), ("B", "B0")],
                {"A": [First], "B": [Second]},
                frozenset({"B"}),
            ),
            frozenset({(1, 1, 1)}),
        )

    def testPartialAssignmentBlockersCoverDistinctCandidateCuts(self) -> None:
        SelectedA = NetRouteCandidate(
            Signal="A",
            CandidateId="A0",
            SourcePortalId="A-source",
            TargetPortalIds={},
            Layer=0,
            Guide=frozenset(),
            Nodes=frozenset({(1, 1, 1)}),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(1, 1, 1)}),
            ),
            RepeaterWaypoints=(),
            Length=1,
            BendCount=0,
            ViaCount=0,
            MaterialCost=1,
            FootprintGrowth=0,
        )
        SelectedB = replace(
            SelectedA,
            Signal="B",
            CandidateId="B0",
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(2, 1, 2)}),
            ),
        )
        SelectedD = replace(
            SelectedA,
            Signal="D",
            CandidateId="D0",
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(3, 1, 3)}),
            ),
        )
        Omitted = replace(
            SelectedA,
            Signal="C",
            CandidateId="C0",
            Claims=RoutingResourceClaims(
                ElectricalCells=frozenset({(1, 1, 1)}),
            ),
        )
        OmittedAlternate = replace(
            Omitted,
            CandidateId="C1",
            Claims=RoutingResourceClaims(
                ElectricalCells=frozenset({(2, 1, 2)}),
            ),
        )
        OmittedThird = replace(
            Omitted,
            CandidateId="C2",
            Claims=RoutingResourceClaims(
                ElectricalCells=frozenset({(3, 1, 3)}),
            ),
        )
        self.assertEqual(
            SelectPartialAssignmentBlockerSignals(
                [("A", "A0"), ("B", "B0"), ("D", "D0")],
                {
                    "A": [SelectedA],
                    "B": [SelectedB],
                    "C": [Omitted, OmittedAlternate, OmittedThird],
                    "D": [SelectedD],
                },
                frozenset({"C"}),
            ),
            frozenset({"A"}),
        )

    def testNegotiatedRoutingStartsAboveMeasuredExactDomain(self) -> None:
        self.assertFalse(
            ShouldUseNegotiatedRouting(
                LocalFirstPhysicalDesignPolicy,
                32,
            )
        )
        self.assertFalse(
            ShouldUseNegotiatedRouting(
                LocalFirstPhysicalDesignPolicy,
                64,
            )
        )
        self.assertTrue(
            ShouldUseNegotiatedRouting(
                LocalFirstPhysicalDesignPolicy,
                65,
            )
        )
        self.assertFalse(
            ShouldUseNegotiatedRouting(
                DefaultPhysicalDesignPolicy,
                100,
            )
        )

    def testCompleteClusterLeaseRetriesConfiguredPortfolioOnly(
        self,
    ) -> None:
        self.assertTrue(
            ShouldRetryCompleteClusterLeaseStateBeforePlacement(
                True,
                True,
                True,
                True,
                0,
                False,
                4,
            )
        )
        self.assertTrue(
            ShouldRetryCompleteClusterLeaseStateBeforePlacement(
                True,
                True,
                True,
                True,
                1,
                False,
                4,
            )
        )
        for Overrides in (
            {"TopologyRequiresJointPortfolio": False},
            {"CompleteClusterInterfaceAccess": False},
            {"HasClusterBoundaryLeaseReservations": False},
            {"ReservationVariant": 3},
            {"SkipStrictPortalReservation": True},
            {"MaximumPortalReservationAlternatives": 1},
        ):
            Values = {
                "AdaptiveRoutingEnabled": True,
                "TopologyRequiresJointPortfolio": True,
                "CompleteClusterInterfaceAccess": True,
                "HasClusterBoundaryLeaseReservations": True,
                "ReservationVariant": 0,
                "SkipStrictPortalReservation": False,
                "MaximumPortalReservationAlternatives": 4,
                **Overrides,
            }
            with self.subTest(Overrides=Overrides):
                self.assertFalse(
                    ShouldRetryCompleteClusterLeaseStateBeforePlacement(
                        **Values
                    )
                )

    def testCandidateStarvationFindsUnavoidableNonemptyDomainPair(
        self,
    ) -> None:
        FirstOptions = [
            replace(
                self.BuildCandidate("A", "A0", (1, 1, 0)),
                Claims=RoutingResourceClaims(
                    WireCells=frozenset({(1, 1, 0)}),
                ),
            ),
            replace(
                self.BuildCandidate("A", "A1", (2, 1, 0)),
                Claims=RoutingResourceClaims(
                    WireCells=frozenset({(2, 1, 0)}),
                ),
            ),
        ]
        SecondOptions = [
            replace(
                self.BuildCandidate("B", "B0", (3, 1, 0)),
                Claims=RoutingResourceClaims(
                    ElectricalCells=frozenset({
                        (1, 1, 0),
                        (2, 1, 0),
                    }),
                ),
            ),
        ]
        Work = []

        Cut = FindFirstUnavoidableCandidateDomainPairCut(
            {
                "Starved": [],
                "B": SecondOptions,
                "A": FirstOptions,
                "Compatible": [
                    self.BuildCandidate(
                        "Compatible",
                        "Compatible0",
                        (9, 1, 0),
                    ),
                ],
            },
            WorkCheck=lambda Diagnostics: Work.append(Diagnostics),
        )

        self.assertIsNotNone(Cut)
        self.assertEqual(Cut.Signals, ("A", "B"))
        self.assertEqual(
            Cut.ConflictPositions,
            frozenset({(1, 1, 0), (2, 1, 0)}),
        )
        self.assertEqual(Work[0]["Phase"], "start")
        self.assertEqual(Work[-1]["Phase"], "cut")

    def testAccessAwareLocalClaimReleaseSelectsOneClaim(self) -> None:
        Claim = self.BuildLocalClaim("LocalA", (1, 1, 0))
        Selection = SelectAccessAwareLocalClaimReleases(
            {
                "Signal": (
                    RoutingResourceClaims(WireCells=frozenset({(1, 1, 0)})),
                ),
            },
            (Claim,),
        )
        self.assertEqual(Selection.ReleasedSignals, frozenset({"LocalA"}))
        self.assertEqual(Selection.SelectorScore[:2], (1, 1))

    def testAccessAwareLocalClaimReleaseSelectsMinimalMultiClaimCut(self) -> None:
        First = self.BuildLocalClaim("LocalA", (1, 1, 0))
        Second = self.BuildLocalClaim("LocalB", (5, 1, 0), NodeCount=3)
        Selection = SelectAccessAwareLocalClaimReleases(
            {
                "First": (
                    RoutingResourceClaims(WireCells=frozenset({(1, 1, 0)})),
                ),
                "Second": (
                    RoutingResourceClaims(WireCells=frozenset({(5, 1, 0)})),
                ),
            },
            (First, Second),
        )
        self.assertEqual(
            Selection.ReleasedSignals,
            frozenset({"LocalA", "LocalB"}),
        )
        self.assertEqual(Selection.SelectorScore[:2], (2, 4))

    def testAccessAwareLocalClaimReleaseLeavesCandidateOnlyAccessUntouched(self) -> None:
        Claim = self.BuildLocalClaim("LocalA", (1, 1, 0))
        Selection = SelectAccessAwareLocalClaimReleases(
            {
                "Signal": (
                    RoutingResourceClaims(WireCells=frozenset({(20, 1, 0)})),
                ),
            },
            (Claim,),
        )
        self.assertEqual(Selection.ReleasedSignals, frozenset())
        self.assertEqual(Selection.CandidateOnlySignals, frozenset({"Signal"}))

    def testAccessAwareLocalClaimReleaseNeverReleasesOwnClaim(self) -> None:
        Claim = self.BuildLocalClaim("Signal", (1, 1, 0))
        Selection = SelectAccessAwareLocalClaimReleases(
            {
                "Signal": (
                    RoutingResourceClaims(WireCells=frozenset({(1, 1, 0)})),
                ),
            },
            (Claim,),
        )

        self.assertEqual(Selection.ReleasedSignals, frozenset())
        self.assertEqual(Selection.CandidateOnlySignals, frozenset({"Signal"}))

    def testRejectedTrailingDescriptorStillCompletesFiniteDomain(self) -> None:
        Calls = []
        Shape = CandidateRequestShapeDescriptor(
            SourcePortal=SimpleNamespace(PortalId="source"),
            TargetPortals=(SimpleNamespace(PortalId="target"),),
            Guide=frozenset(),
            Layer=0,
            Axis="Z",
            Lane=0,
            Variant=0,
            PortalShapeRank=7,
            RoutingY=1,
            GuideExpansion=1,
            InitiallyDeferred=True,
            Priority=(0, 7, 0, 0, 0, "Z", 0),
        )

        def RejectRequest() -> None:
            Calls.append("rejected")
            return None

        Request = LazyCandidateRouteRequest(Shape, RejectRequest)
        self.assertIsNone(Request.Materialize())
        self.assertIsNone(Request.Materialize())
        self.assertEqual(Calls, ["rejected"])
        self.assertTrue(IsPhysicalCandidateRequestDomainComplete(0, False))
        self.assertFalse(IsPhysicalCandidateRequestDomainComplete(1, False))
        self.assertTrue(IsPhysicalCandidateRequestDomainComplete(0, True))

    def testPhysicalGlobalAssignmentSuffixUsesNativeCutDeterministically(
        self,
    ) -> None:
        Selection = SelectPhysicalGlobalAssignmentSuffixSignals(
            ("Zulu", "Alpha", "Beta"),
            (("Alpha", "candidate-a"),),
            ("Zulu", "Alpha", "Unknown"),
            {"Alpha": 4, "Beta": 9, "Zulu": 2},
        )

        self.assertEqual(Selection, ("Alpha", "Zulu"))

    def testPhysicalGlobalAssignmentSuffixFallsBackToMissingSignals(
        self,
    ) -> None:
        Selection = SelectPhysicalGlobalAssignmentSuffixSignals(
            ("Zulu", "Alpha", "Beta"),
            (("Alpha", "candidate-a"),),
            (),
            {"Alpha": 4, "Beta": 0, "Zulu": 2},
        )

        self.assertEqual(Selection, ("Zulu",))

    def testNativePairCutCompletesOneSmallestAdjacentDomain(self) -> None:
        Result = SimpleNamespace(
            PairwiseCompatibilityComplete=True,
            PairwiseIncompatibleSignals=(
                ("Closed", "LargeOpen"),
                ("Closed", "SmallOpen"),
                ("FirstOpen", "SecondOpen"),
            ),
        )
        Remaining = {
            "Closed": 0,
            "LargeOpen": 11,
            "SmallOpen": 3,
            "FirstOpen": 1,
            "SecondOpen": 1,
        }

        self.assertEqual(
            SelectPhysicalGlobalNativePairCutSuffixSignals(
                Result,
                Remaining,
            ),
            ("SmallOpen",),
        )
        self.assertEqual(
            SelectCompletedPhysicalGlobalPairNoGoodEdges(
                Result,
                Remaining,
            ),
            (),
        )
        Remaining["SmallOpen"] = 0
        self.assertEqual(
            SelectCompletedPhysicalGlobalPairNoGoodEdges(
                Result,
                Remaining,
            ),
            (("Closed", "SmallOpen"),),
        )

    def testPhysicalGlobalAssignmentCompletesOnlyAfterRelevantCursors(
        self,
    ) -> None:
        Remaining = {"Conflict": 0, "Unrelated": 12}
        self.assertTrue(
            PhysicalGlobalAssignmentDomainIsComplete(
                ("Conflict",),
                Remaining,
                AssignmentBudgetExhausted=False,
                DeadlineExpired=False,
            )
        )
        self.assertFalse(
            PhysicalGlobalAssignmentDomainIsComplete(
                ("Unrelated",),
                Remaining,
                AssignmentBudgetExhausted=False,
                DeadlineExpired=False,
            )
        )
        self.assertFalse(
            PhysicalGlobalAssignmentDomainIsComplete(
                ("Conflict",),
                Remaining,
                AssignmentBudgetExhausted=True,
                DeadlineExpired=False,
            )
        )
        self.assertFalse(
            PhysicalGlobalAssignmentDomainIsComplete(
                (),
                Remaining,
                AssignmentBudgetExhausted=False,
                DeadlineExpired=False,
            )
        )

    def testPhysicalGlobalProofSelectsEveryOpenCandidateDomain(
        self,
    ) -> None:
        self.assertEqual(
            SelectOpenPhysicalGlobalCandidateDomainSignals({
                "Closed": 0,
                "Zulu": 4,
                "Alpha": 1,
            }),
            ("Alpha", "Zulu"),
        )

    def testPhysicalGlobalAssignmentBranchesAroundExactNoGood(self) -> None:
        Calls = []

        def PlanNative(Values):
            Calls.append(tuple((Value[0], Value[1]) for Value in Values))
            Selected = []
            for Signal in sorted({Value[0] for Value in Values}):
                CandidateId = min(
                    Value[1] for Value in Values if Value[0] == Signal
                )
                Selected.append((Signal, CandidateId))
            return SimpleNamespace(
                Success=True,
                SelectedCandidateIds=tuple(Selected),
                ExpansionCount=1,
                CompletedWork=1,
                BudgetExhausted=False,
                DeadlineExceeded=False,
                ConflictSignals=(),
            )

        Result = PlanPhysicalGlobalAssignmentAvoidingExactNoGoods(
            (("A", "A0"), ("A", "A1"), ("B", "B0"), ("B", "B1")),
            (frozenset((("A", "A0"), ("B", "B0"))),),
            PlanNative,
        )

        self.assertEqual(
            dict(Result.SelectedCandidateIds),
            {"A": "A1", "B": "B0"},
        )
        self.assertEqual(len(Calls), 2)
        self.assertNotIn(("A", "A0"), Calls[1])

    def testPhysicalGlobalAssignmentBranchesAroundUnaryCore(self) -> None:
        Calls = []

        def PlanNative(Values):
            Calls.append(tuple((Value[0], Value[1]) for Value in Values))
            Selected = tuple(
                (
                    Signal,
                    min(Value[1] for Value in Values if Value[0] == Signal),
                )
                for Signal in sorted({Value[0] for Value in Values})
            )
            return SimpleNamespace(
                Success=True,
                SelectedCandidateIds=Selected,
                ExpansionCount=1,
                CompletedWork=1,
                BudgetExhausted=False,
                DeadlineExceeded=False,
                ConflictSignals=(),
            )

        Result = PlanPhysicalGlobalAssignmentAvoidingExactNoGoods(
            (("A", "A0"), ("A", "A1"), ("B", "B0")),
            (frozenset((("A", "A0"),)),),
            PlanNative,
        )

        self.assertEqual(
            dict(Result.SelectedCandidateIds),
            {"A": "A1", "B": "B0"},
        )
        self.assertEqual(len(Calls), 1)
        self.assertNotIn(("A", "A0"), Calls[0])

    def testCompleteDomainNoGoodProducesDirectProofCore(self) -> None:
        Domains = {
            "First": ("F0", "F1"),
            "Second": ("S0", "S1"),
            "Unrelated": ("U0",),
        }

        def Keys(Option):
            Signal = {
                "F": "First",
                "S": "Second",
                "U": "Unrelated",
            }[Option[0]]
            return frozenset((
                (Signal, "signal-domain"),
                (Signal, Option),
            ))

        Clause = frozenset((
            ("First", "signal-domain"),
            ("Second", "signal-domain"),
        ))
        Result = FindProofQualifiedCompleteDomainNoGoodCore(
            Domains,
            (Clause,),
            Keys,
        )

        self.assertEqual(Result, (("First", "Second"), Clause))

    def testSelectedOptionNoGoodIsNotACompleteDomainProof(self) -> None:
        Domains = {"First": ("F0", "F1")}

        Result = FindProofQualifiedCompleteDomainNoGoodCore(
            Domains,
            (frozenset((("First", "F0"),)),),
            lambda Option: frozenset((("First", Option),)),
        )

        self.assertIsNone(Result)

    def testUniversalFactorNoGoodProducesCoreWithoutOptionMaterialization(
        self,
    ) -> None:
        Clause = frozenset((
            ("First", "local-factor-domain:solver:fabric-first"),
            ("Second", "local-factor-domain:solver:fabric-second"),
        ))
        Result = FindProofQualifiedUniversalNoGoodCore(
            {
                "First": frozenset((
                    (
                        "First",
                        "local-factor-domain:solver:fabric-first",
                    ),
                )),
                "Second": frozenset((
                    (
                        "Second",
                        "local-factor-domain:solver:fabric-second",
                    ),
                )),
            },
            (Clause,),
        )

        self.assertEqual(Result, (("First", "Second"), Clause))

    def testPersistentPortCspStateReusesOnlyMonotonicConstraints(self) -> None:
        Resources = SimpleNamespace(
            RejectedPhysicalComponentPortReservationsBySignal={},
            RejectedPhysicalComponentPortReservationSets=set(),
            RejectedPhysicalComponentPortAssignmentFingerprints=set(),
            DeferredPhysicalComponentPortAssignmentFingerprints=set(),
            PhysicalComponentPortCspStateCache={},
            PhysicalGlobalRouteTreeResultCache={},
        )
        Initial, Reused = GetPersistentPhysicalComponentPortCspState(
            Resources,
            "solver",
            "domain",
        )
        self.assertFalse(Reused)
        Initial.FailedAssignmentStates.add(("failed-prefix",))

        # Native route-tree completion and replay are downstream of physical
        # assembly selection.  Populating that cache must not create a new
        # port-CSP epoch or invalidate its monotonic failed-prefix state.
        Resources.PhysicalGlobalRouteTreeResultCache["request"] = None
        MaskIndependent, Reused = GetPersistentPhysicalComponentPortCspState(
            Resources,
            "solver",
            "domain",
        )
        self.assertTrue(Reused)
        self.assertIs(MaskIndependent, Initial)

        Resources.RejectedPhysicalComponentPortReservationSets.add(
            frozenset((("sum", "reservation-a"),))
        )
        Extended, Reused = GetPersistentPhysicalComponentPortCspState(
            Resources,
            "solver",
            "domain",
        )
        self.assertTrue(Reused)
        self.assertIs(Extended, Initial)
        self.assertIn(("failed-prefix",), Extended.FailedAssignmentStates)

        Resources.DeferredPhysicalComponentPortAssignmentFingerprints.add(
            "deferred-plan"
        )
        Deferred, Reused = GetPersistentPhysicalComponentPortCspState(
            Resources,
            "solver",
            "domain",
        )
        self.assertTrue(Reused)
        Resources.DeferredPhysicalComponentPortAssignmentFingerprints.clear()
        Restarted, Reused = GetPersistentPhysicalComponentPortCspState(
            Resources,
            "solver",
            "domain",
        )
        self.assertFalse(Reused)
        self.assertIsNot(Restarted, Deferred)
        self.assertFalse(Restarted.FailedAssignmentStates)

    def testPortSolverScopedNoGoodCannotMatchAnotherDomain(self) -> None:
        Port = PhysicalComponentPortReservation(
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
            GlobalClaims=RoutingResourceClaims(),
            ReservationFingerprint="reservation",
        )
        FirstKeys = BuildPhysicalPortNoGoodKeys(Port, "solver-a")
        OtherKeys = BuildPhysicalPortNoGoodKeys(Port, "solver-b")
        ScopedClause = frozenset((
            (
                Port.Signal,
                BuildPhysicalPortApertureContractFingerprint(Port),
            ),
            (Port.Signal, "local-signal-domain:solver-a"),
        ))

        self.assertTrue(ScopedClause.issubset(FirstKeys))
        self.assertFalse(ScopedClause.issubset(OtherKeys))

    def testPhysicalGlobalAssignmentAvoidsMultipleExactNoGoods(self) -> None:
        def PlanNative(Values):
            Selected = tuple(
                (
                    Signal,
                    min(Value[1] for Value in Values if Value[0] == Signal),
                )
                for Signal in sorted({Value[0] for Value in Values})
            )
            return SimpleNamespace(
                Success=True,
                SelectedCandidateIds=Selected,
                ExpansionCount=1,
                CompletedWork=1,
                BudgetExhausted=False,
                DeadlineExceeded=False,
                ConflictSignals=(),
            )

        Result = PlanPhysicalGlobalAssignmentAvoidingExactNoGoods(
            (("A", "A0"), ("A", "A1"), ("B", "B0"), ("B", "B1")),
            (
                frozenset((("A", "A0"), ("B", "B0"))),
                frozenset((("A", "A1"), ("B", "B0"))),
            ),
            PlanNative,
        )

        self.assertTrue(Result.Success)
        self.assertEqual(
            dict(Result.SelectedCandidateIds),
            {"A": "A1", "B": "B1"},
        )

    def testNegotiatedReservationPublishesHardLeaseFirstSegments(self) -> None:
        Signal = "Crossing"
        Root = (0, 1, 0)
        Target = (4, 1, 0)
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
        Source = self.BuildPortal(Signal, Root, (1, 1, 0))
        Destination = self.BuildPortal(Signal, Target, (3, 1, 0))
        Profile = NetRoutingProfile(
            Signal=Signal,
            Root=Root,
            Targets=(Target,),
            Span=4,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
        )

        _Reserved, Reservations = ReserveClusterBoundaryLeases(
            {(Signal, Root, 0): (Source,), (Signal, Target, 0): (Destination,)},
            {Signal: Profile},
            Resources,
        )

        self.assertEqual(
            {Value.Purpose for Value in Reservations},
            {"cluster-boundary-lease"},
        )
        self.assertTrue(all(Value.FirstSegment for Value in Reservations))
        self.assertEqual(
            Reservations[0].ToDictionary()["Purpose"],
            "cluster-boundary-lease",
        )
        self.assertIsNotNone(
            Resources.PreparedClusterInterfaceAssignment
        )
        Assignment = Resources.PreparedClusterInterfaceAssignment
        assert Assignment is not None
        self.assertTrue(Assignment.Feasible)
        self.assertEqual(
            Assignment.Problem.TerminalDomainSizes,
            (1, 1),
        )
        self.assertTrue(Assignment.AssignmentFingerprint)

    def testClusterInterfacePatternSearchBacktracksWholeSignalBundle(
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
        FirstTerminal = (10, 1, 0)
        SecondTerminal = (20, 1, 0)
        Shared = (0, 1, 0)
        Alternative = (4, 1, 0)
        FirstShared = self.BuildPortal(
            "First", FirstTerminal, Shared,
        )
        FirstAlternative = self.BuildPortal(
            "First", FirstTerminal, Alternative,
        )
        SecondShared = self.BuildPortal(
            "Second", SecondTerminal, Shared,
        )

        def Profile(Signal, Root):
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

        _Reserved, Reservations = ReserveClusterBoundaryLeases(
            {
                ("First", FirstTerminal, 0): (
                    FirstShared,
                    FirstAlternative,
                ),
                ("Second", SecondTerminal, 0): (SecondShared,),
            },
            {
                "First": Profile("First", FirstTerminal),
                "Second": Profile("Second", SecondTerminal),
            },
            Resources,
            MaximumExpansions=3,
        )

        self.assertEqual(
            {
                Reservation.Signal: Reservation.FirstSegment
                for Reservation in Reservations
            },
            {
                "First": (Alternative,),
                "Second": (Shared,),
            },
        )

    def testCompleteInterfaceDomainUsesCandidatesBeyondCompactBeam(
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
        FirstSignal = "First"
        SecondSignal = "Second"
        FirstTerminal = (0, 1, 0)
        SecondTerminal = (20, 1, 0)
        SharedPosition = (4, 1, 0)
        LegalPosition = (40, 1, 0)

        def Profile(Signal, Terminal):
            return NetRoutingProfile(
                Signal=Signal,
                Root=Terminal,
                Targets=(),
                Span=0,
                Fanout=0,
                RetryCount=0,
                Criticality=1,
                IsTrunk=False,
                SourceAccessPath=(Terminal,),
                TargetAccessPaths={},
            )

        BlockingPortal = PinAccessPortal(
            PortalId="blocking",
            Signal=SecondSignal,
            Terminal=SecondTerminal,
            Layer=0,
            Path=(SharedPosition,),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(
                WireCells=frozenset((SharedPosition,)),
            ),
            Length=0,
            BendCount=0,
            ViaCount=0,
            Cost=0,
        )
        FirstPortals = tuple(
            PinAccessPortal(
                PortalId=f"first-{Index}",
                Signal=FirstSignal,
                Terminal=FirstTerminal,
                Layer=0,
                Path=(Position,),
                Edges=frozenset(),
                Claims=RoutingResourceClaims(
                    WireCells=frozenset((Position,)),
                ),
                Length=0,
                BendCount=0,
                ViaCount=0,
                Cost=Index,
            )
            for Index, Position in enumerate((
                *(SharedPosition for _Index in range(6)),
                LegalPosition,
            ))
        )
        _Portals, Reservations = ReserveClusterBoundaryLeases(
            {
                (FirstSignal, FirstTerminal, 0): FirstPortals,
                (SecondSignal, SecondTerminal, 0): (
                    BlockingPortal,
                ),
            },
            {
                FirstSignal: Profile(
                    FirstSignal,
                    FirstTerminal,
                ),
                SecondSignal: Profile(
                    SecondSignal,
                    SecondTerminal,
                ),
            },
            Resources,
            RequireCompleteClusterInterfaceDomain=True,
        )

        FirstReservation = next(
            Reservation
            for Reservation in Reservations
            if Reservation.Signal == FirstSignal
        )
        self.assertEqual(
            FirstReservation.FirstSegment,
            (LegalPosition,),
        )
        self.assertTrue(
            Resources.PreparedClusterInterfaceAssignment
            .Problem.DomainComplete
        )

    def testCompleteInterfaceEmptyTerminalDomainIsExhaustive(
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

        with self.assertRaises(RoutingStageError) as Context:
            ReserveClusterBoundaryLeases(
                {(Signal, Root, 3): ()},
                {Signal: Profile},
                Resources,
                RequiredInterfaceLayer=3,
                RequireCompleteClusterInterfaceDomain=True,
            )

        self.assertTrue(
            Context.exception.Failure.Diagnostics[
                "ClusterInterfaceDomainComplete"
            ]
        )
        self.assertTrue(
            Context.exception.Failure.Diagnostics[
                "OwnershipSearchComplete"
            ]
        )

    def testTransactionalLeasePrescreenSelectsOnlyExactRepairPairs(
        self,
    ) -> None:
        PairRecipe = {
            "TransactionalClusterEndpointRepair": True,
            "InternalPinBankGeometryRepairSignals": [
                "Right",
                "Left",
            ],
        }
        self.assertEqual(
            SelectTransactionalLeasePrescreenSignals(PairRecipe),
            frozenset({"Left", "Right"}),
        )
        for Recipe in (
            {},
            {
                **PairRecipe,
                "TransactionalClusterEndpointRepair": False,
            },
            {
                **PairRecipe,
                "InternalPinBankGeometryRepairSignals": ["Only"],
            },
            {
                **PairRecipe,
                "InternalPinBankGeometryRepairSignals": [
                    "One",
                    "Two",
                    "Three",
                ],
            },
        ):
            self.assertFalse(
                SelectTransactionalLeasePrescreenSignals(Recipe)
            )

    def testClusterLeaseRejectsSelfConflictingMergedSignalPattern(
        self,
    ) -> None:
        Signal = "Crossing"
        Root = (10, 1, 0)
        Target = (20, 1, 0)
        BadRoot = self.BuildPortal(
            Signal,
            Root,
            (0, 1, 0),
        )
        SafeRoot = replace(
            self.BuildPortal(
                Signal,
                Root,
                (4, 1, 0),
            ),
            Cost=1,
        )
        TargetPortal = self.BuildPortal(
            Signal,
            Target,
            (0, 2, 0),
        )
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
            Targets=(Target,),
            Span=0,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(),
            TargetAccessPaths={Target: ()},
        )

        _Reserved, Reservations = ReserveClusterBoundaryLeases(
            {
                (Signal, Root, 0): (BadRoot, SafeRoot),
                (Signal, Target, 0): (TargetPortal,),
            },
            {Signal: Profile},
            Resources,
        )

        self.assertEqual(
            {
                Reservation.Terminal: Reservation.FirstSegment
                for Reservation in Reservations
            },
            {
                Root: ((4, 1, 0),),
                Target: ((0, 2, 0),),
            },
        )

    def testClusterInterfaceEmptyTerminalDomainPublishesStarvationCut(
        self,
    ) -> None:
        Signal = "Starved"
        Terminal = (0, 1, 0)
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
            Root=Terminal,
            Targets=(),
            Span=0,
            Fanout=0,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Terminal,),
            TargetAccessPaths={},
        )

        with self.assertRaises(RoutingStageError) as Context:
            ReserveClusterBoundaryLeases(
                {
                    (Signal, Terminal, 0): (
                        self.BuildPortal(
                            Signal,
                            Terminal,
                            (0, 2, 0),
                        ),
                    ),
                },
                {Signal: Profile},
                Resources,
            )

        Failure = Context.exception.Failure
        self.assertEqual(Failure.AffectedNets, (Signal,))
        self.assertEqual(
            Failure.Diagnostics["ConflictGraph"],
            {
                "Classification": (
                    "candidate-starvation-placement-conflict"
                ),
                "ConflictSignals": [Signal],
                "NoCandidateSignals": [Signal],
                "RelocationSignals": [Signal],
                "PriorityRelocationSignals": [Signal],
                "CandidateCounts": {Signal: 0},
            },
        )
        Cut = RoutingAssignmentCut.FromFailure(Failure)
        self.assertIsNotNone(Cut)
        assert Cut is not None
        self.assertEqual(
            Cut.Classification.value,
            "candidate-starvation-placement-conflict",
        )
        self.assertEqual(Cut.NoCandidateSignals, (Signal,))
        self.assertEqual(Cut.CandidateCounts, ((Signal, 0),))
        self.assertTrue(Cut.ConflictFingerprint)

    def testClusterInterfaceVariantsSampleDifferentTerminalDomains(
        self,
    ) -> None:
        Signal = "Interface"
        Terminal = (20, 1, 0)
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
        Portals = tuple(
            self.BuildPortal(
                Signal,
                Terminal,
                (Index * 3, 1, 0),
            )
            for Index in range(8)
        )
        Profile = NetRoutingProfile(
            Signal=Signal,
            Root=Terminal,
            Targets=(),
            Span=0,
            Fanout=0,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Terminal,),
            TargetAccessPaths={},
        )

        _FirstPortals, FirstReservations = (
            ReserveClusterBoundaryLeases(
                {(Signal, Terminal, 0): Portals},
                {Signal: Profile},
                Resources,
                ReservationVariant=0,
            )
        )
        _SecondPortals, SecondReservations = (
            ReserveClusterBoundaryLeases(
                {(Signal, Terminal, 0): Portals},
                {Signal: Profile},
                Resources,
                ReservationVariant=1,
            )
        )

        self.assertNotEqual(
            FirstReservations[0].PortalId,
            SecondReservations[0].PortalId,
        )

    def testRouteDomainContinuationRestoresExactCursorAndCandidates(
        self,
    ) -> None:
        Candidate = SimpleNamespace(CandidateId="candidate-a")
        Continuation = PhysicalSignalRouteDomainContinuation(
            PreSiblingDomainFingerprint="stable-domain",
            Signal="Alpha",
            RequestDomainFingerprint="request-domain",
            RequestDescriptorFingerprints=("shape-0", "shape-1"),
            NextDescriptorCursor=1,
            Candidates=(Candidate,),
            CandidateMetadata=(("candidate-a", ("X", 3, 1, 0)),),
            Complete=False,
        )
        Cache = {"stable-domain": Continuation}

        Restored = SelectReplayablePhysicalSignalRouteDomainContinuation(
            Cache,
            "stable-domain",
            "Alpha",
            "request-domain",
            ("shape-0", "shape-1"),
        )

        self.assertIs(Restored, Continuation)
        self.assertEqual(Restored.NextDescriptorCursor, 1)
        self.assertEqual(Restored.Candidates, (Candidate,))
        self.assertIsNone(
            SelectReplayablePhysicalSignalRouteDomainContinuation(
                Cache,
                "stable-domain",
                "Alpha",
                "request-domain",
                ("shape-0", "changed-shape"),
            )
        )

    def testPreSiblingContinuationPublishesOnlyClosedDomains(self) -> None:
        Cache = {}
        Identity = SimpleNamespace(
            StableDomainFingerprint="stable-alpha"
        )
        Candidate = SimpleNamespace(CandidateId="candidate-a")

        self.assertEqual(
            RetainCompletePhysicalSignalRouteDomainContinuations(
                Cache,
                {"Alpha": Identity},
                {"Alpha": ("shape-0", "shape-1")},
                {"Alpha": "request-domain"},
                {"Alpha": 1},
                {"Alpha": (Candidate,)},
                {"Alpha": {"candidate-a": ("X", 1, 0, 0)}},
            ),
            (),
        )
        self.assertFalse(Cache)

        self.assertEqual(
            RetainCompletePhysicalSignalRouteDomainContinuations(
                Cache,
                {"Alpha": Identity},
                {"Alpha": ("shape-0", "shape-1")},
                {"Alpha": "request-domain"},
                {"Alpha": 0},
                {"Alpha": (Candidate,)},
                {"Alpha": {}},
            ),
            (),
        )
        self.assertFalse(Cache)

        RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint="stable-alpha",
            Signal="Alpha",
            RequestDomainFingerprint="request-domain",
            RequestDescriptorFingerprints=("shape-0", "shape-1"),
            CompletedDescriptorFingerprints=("shape-0", "shape-1"),
            Candidates=(Candidate,),
            CandidateMetadata={"candidate-a": ("X", 1, 0, 0)},
        )
        Retained = RetainCompletePhysicalSignalRouteDomainContinuations(
            Cache,
            {"Alpha": Identity},
            {"Alpha": ("shape-0", "shape-1")},
            {"Alpha": "request-domain"},
            {"Alpha": 0},
            {"Alpha": (Candidate,)},
            {"Alpha": {"candidate-a": ("X", 1, 0, 0)}},
        )
        self.assertEqual(len(Retained), 1)
        self.assertTrue(Retained[0].Complete)
        self.assertEqual(Retained[0].NextDescriptorCursor, 2)
        self.assertIs(Cache["stable-alpha"], Retained[0])

    def testSignalLocalCandidateRequestCertificateBindsExactFactors(
        self,
    ) -> None:
        ApertureDomain = SimpleNamespace(
            Complete=True,
            CrossingSignals=("Alpha",),
            StableKeepoutCoreFingerprint="keepout",
            Factors=(SimpleNamespace(
                Signal="Alpha",
                PortGlobalContractFingerprint="global-alpha",
                ChannelReservationFingerprint="channel-alpha",
            ),),
        )
        Components = {
            "GlobalContractFingerprint": "global-alpha",
            "ChannelFingerprint": "channel-alpha",
            "GuideFactorFingerprint": "",
            "GlobalKeepoutFingerprint": "keepout",
            "BlockedNodesFingerprint": "blocked",
            "DescriptorDomainFingerprint": "descriptors",
            "DescriptorCount": 2,
        }

        self.assertTrue(
            PhysicalSignalLocalCandidateRequestFactorProofComplete(
                "Alpha",
                Components,
                ("Alpha",),
                {"Alpha": frozenset(((1, 2),))},
                ApertureDomain,
            )
        )
        self.assertTrue(
            PhysicalSignalLocalCandidateRequestFactorProofComplete(
                "Alpha",
                {**Components, "GuideFactorFingerprint": "guide-alpha"},
                ("Alpha",),
                {"Alpha": frozenset(((1, 2),))},
                ApertureDomain,
            )
        )
        self.assertFalse(
            PhysicalSignalLocalCandidateRequestFactorProofComplete(
                "Alpha",
                {**Components, "ChannelFingerprint": "changed"},
                ("Alpha",),
                {"Alpha": frozenset(((1, 2),))},
                ApertureDomain,
            )
        )
        self.assertFalse(
            PhysicalSignalLocalCandidateRequestFactorProofComplete(
                "Alpha",
                Components,
                ("Alpha",),
                {},
                ApertureDomain,
            )
        )

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

    def testUnassignedPlacementAccessFabricDomainReachesTrackPreparation(
        self,
    ) -> None:
        """Track preparation receives every retained fabric alternative."""
        Module = ModuleIR(
            Name="AccessFabricPortalPreparation",
            Inputs=["A", "B"],
            Outputs=["Z"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["A"]),
                Gate("InputB", GateKind.INPUT, ["B"]),
                Gate("Nand", GateKind.NAND, ["Z"], ["A", "B"]),
                Gate("OutputZ", GateKind.OUTPUT, [], ["Z"]),
            ],
        )
        Netlist = NetlistIR(
            Top=Module.Name,
            Modules={Module.Name: Module},
        )
        Placement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=0,
            PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
            PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
        )
        BuiltFabric = BuildPlacementAccessFabric(
            Placement,
            TopologyKind="derived-perimeter-access-v1",
            AccessRingTrackCount=1,
        )
        Domain = next(
            Value for Value in BuiltFabric.TerminalDomains
            if Value.Signal == "Z"
        )
        # Keep a compact two-layer domain small enough that exact preparation
        # can demonstrate every choice rather than a policy-sized slice.
        Fabric = replace(
            BuiltFabric,
            TerminalDomains=(replace(
                Domain,
                EscapeStubs=Domain.EscapeStubs[:3],
            ),),
        )
        AttachedPlacement = AttachPlacementAccessFabric(Placement, Fabric)
        Resources = BuildRoutingResources(AttachedPlacement.Placed)
        MinimumY = min(
            GateValue.Y
            for GateValue in AttachedPlacement.Placed.PlacedGates
        )
        ExpectedPortalIds = {
            Portal.PortalId
            for Values in ApplyPlacementAccessFabricPortalDomains(
                {},
                Fabric,
                Resources.ResourceGraph,
                DefaultRedstoneRoutingTechnology,
                MinimumY,
                AttachedPlacement.LayerCount,
            ).values()
            for Portal in Values
        }
        SeenPortalIds: set[str] = set()
        OriginalIdentity = (
            TrackPortfolio.BuildCandidateRequestGeometryIdentity
        )

        def RecordCandidateRequestIdentity(
            SourcePortalId: str,
            TargetPortalIds: tuple[str, ...],
            *Arguments: object,
            **KeywordArguments: object,
        ) -> tuple[object, ...]:
            SeenPortalIds.add(SourcePortalId)
            SeenPortalIds.update(TargetPortalIds)
            return OriginalIdentity(
                SourcePortalId,
                TargetPortalIds,
                *Arguments,
                **KeywordArguments,
            )

        with patch.object(
            TrackPortfolio,
            "BuildCandidateRequestGeometryIdentity",
            RecordCandidateRequestIdentity,
        ):
            Preparation = PrepareTrackAssignment(
                AttachedPlacement,
                Resources=Resources,
                Policy=LocalFirstPhysicalDesignPolicy,
                Deadline=RoutingDeadline.Start(5.0),
            )

        self.assertTrue(Preparation.Success)
        self.assertTrue(Preparation.Complete)
        self.assertTrue(ExpectedPortalIds)
        self.assertEqual(SeenPortalIds, ExpectedPortalIds)
        self.assertTrue(all(
            "AccessFabricDomain:" in PortalId
            for PortalId in SeenPortalIds
        ))
        self.assertTrue(all(
            Count > 0
            for _Signal, Count in Preparation.CandidateCounts
        ))

    def testRawTrackAssignmentDomainStopsBeforeNativeAssignment(
        self,
    ) -> None:
        """One frozen envelope exports the same values without solving them."""
        from PhysicalDesign.Flow.Preparation import BuildDerivedRoutingEnvelopeDomain, BuildFrozenEnvelopeRoutingPolicy, BuildPlacementAccessDemand

        Module = ModuleIR(
            Name="RawTrackAssignmentDomain",
            Inputs=["A", "B"],
            Outputs=["Z"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["A"]),
                Gate("InputB", GateKind.INPUT, ["B"]),
                Gate("Nand", GateKind.NAND, ["Z"], ["A", "B"]),
                Gate("OutputZ", GateKind.OUTPUT, [], ["Z"]),
            ],
        )
        Netlist = NetlistIR(
            Top=Module.Name,
            Modules={Module.Name: Module},
        )
        Placement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=0,
            PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
            PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
        )
        Demand = BuildPlacementAccessDemand(
            Placement,
            0,
            DefaultRedstoneRoutingTechnology,
        )
        Envelope = BuildDerivedRoutingEnvelopeDomain(
            Demand,
            Placement,
        )[0]
        Policy = BuildFrozenEnvelopeRoutingPolicy(
            LocalFirstPhysicalDesignPolicy,
            Envelope,
        )
        NativeContext = Flow.RustRoutingContext

        class RefuseAssignmentContext:
            def __init__(self, *Arguments) -> None:
                self.Inner = NativeContext(*Arguments)

            def __getattr__(self, Name):
                return getattr(self.Inner, Name)

            def PlanAuthoritativeRoutesBounded(self, *_Arguments):
                raise AssertionError(
                    "raw-domain preparation must not run assignment"
                )

            def PlanAuthoritativeRoutesWithBaseBounded(
                self,
                *_Arguments,
            ):
                raise AssertionError(
                    "raw-domain preparation must not run assignment"
                )

        with patch.object(
            Flow,
            "RustRoutingContext",
            RefuseAssignmentContext,
        ):
            Domain = PrepareRawTrackAssignmentDomain(
                Placement,
                Resources=BuildRoutingResources(Placement.Placed),
                Policy=Policy,
                Deadline=RoutingDeadline.Start(5.0),
            )

        self.assertTrue(Domain.Complete)
        self.assertFalse(Domain.IncompleteReason)
        self.assertGreater(len(Domain.Values), 0)
        self.assertTrue(Domain.CandidateDomainFingerprint)
        self.assertIsNotNone(Domain.NativeAssignmentContext)
        self.assertTrue(all(
            Count > 0
            for _Signal, Count in Domain.CandidateCounts
        ))

    def testRawTemplateSelectionFreezesTheOnlyRouteAssignment(
        self,
    ) -> None:
        """The selected raw witness reaches routing without a second solve."""
        from PhysicalDesign.Flow.Preparation import BuildDerivedRoutingEnvelopeDomain, BuildFrozenEnvelopeRoutingPolicy, BuildPlacementAccessDemand
        from PhysicalDesign.Routing.Assignment.TemplateAssignment import RawTrackAssignmentProblem, RawTrackAssignmentTemplate, SolveRawTrackAssignmentProblemWithContext

        Module = ModuleIR(
            Name="RawTemplateFrozenHandoff",
            Inputs=["A", "B"],
            Outputs=["Z"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["A"]),
                Gate("InputB", GateKind.INPUT, ["B"]),
                Gate("Nand", GateKind.NAND, ["Z"], ["A", "B"]),
                Gate("OutputZ", GateKind.OUTPUT, [], ["Z"]),
            ],
        )
        Netlist = NetlistIR(
            Top=Module.Name,
            Modules={Module.Name: Module},
        )
        Placement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=0,
            PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
            PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
        )
        Demand = BuildPlacementAccessDemand(
            Placement,
            0,
            DefaultRedstoneRoutingTechnology,
        )
        Envelope = BuildDerivedRoutingEnvelopeDomain(
            Demand,
            Placement,
        )[0]
        Policy = BuildFrozenEnvelopeRoutingPolicy(
            LocalFirstPhysicalDesignPolicy,
            Envelope,
        )
        Placement = replace(
            Placement,
            LayerCount=Envelope.RoutingLayerCount,
        )
        Resources = BuildRoutingResources(Placement.Placed)
        Fabric = BuildPlacementAccessFabric(
            Placement,
            Resources=Resources,
            Technology=DefaultRedstoneRoutingTechnology,
            AccessLength=Envelope.AccessLength,
            TopologyKind="derived-perimeter-access-v1",
            AccessRingTrackCount=Envelope.AccessRingTrackCount,
            DeriveLegalEscapeWorkLimit=True,
        )
        # The raw authoritative selector owns all portal/stub choices.  Do
        # not attach a local capacity assignment here: that would collapse
        # the finite fabric domain to a preselected terminal witness.
        Placement = AttachPlacementAccessFabric(Placement, Fabric)
        Resources = BuildRoutingResources(Placement.Placed)
        Domain = PrepareRawTrackAssignmentDomain(
            Placement,
            Resources=Resources,
            Policy=Policy,
            Deadline=RoutingDeadline.Start(5.0),
        )
        Selection = SolveRawTrackAssignmentProblemWithContext(
            RawTrackAssignmentProblem(
                Templates=(RawTrackAssignmentTemplate(
                    TemplateId="only",
                    Objective=(1,),
                    Domain=Domain,
                ),),
                MaximumAssignmentExpansions=(
                    Domain.MaximumAssignmentExpansions
                ),
            ),
            Deadline=RoutingDeadline.Start(5.0),
        )

        self.assertTrue(Selection.Success)
        self.assertTrue(Selection.Complete)
        self.assertIsNotNone(Selection.Preparation)
        self.assertTrue(Domain.Complete)
        self.assertTrue(dict(Domain.Diagnostics)[
            "ExcludedConfiguredRequestCounts"
        ])
        Routed = RoutePcbDesign(
            Placement,
            Resources=Resources,
            Policy=Policy,
            Deadline=RoutingDeadline.Start(5.0),
            FrozenTrackAssignmentPreparation=Selection.Preparation,
        )

        self.assertTrue(Routed.ZeroResourceConflicts)
        self.assertEqual(Routed.AssignmentExpansionCount, 0)

    def testPreRouteLocalClaimChoiceUsesOneNativeAssignment(
        self,
    ) -> None:
        """A complete local tree is one value in the frozen capacity solve.

        The controlled tree materialization gives ``B`` one cheap ordinary
        route which conflicts with ``A``'s complete local tree and one
        compatible ordinary route.  The native assignment must choose the
        local tree plus the compatible route in its single bounded call;
        it must not release a claim or schedule another assignment attempt.
        """
        Module = ModuleIR(
            Name="PreRouteLocalClaimChoice",
            Inputs=["A", "B"],
            Outputs=["Z"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["A"]),
                Gate("InputB", GateKind.INPUT, ["B"]),
                Gate("Nand", GateKind.NAND, ["Z"], ["A", "B"]),
                Gate("OutputZ", GateKind.OUTPUT, [], ["Z"]),
            ],
        )
        Netlist = NetlistIR(
            Top=Module.Name,
            Modules={Module.Name: Module},
        )
        InitialPlacement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=0,
            PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
            PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
        )
        ExistingLocalClaim = next(
            Claim
            for Claim in InitialPlacement.Placed.LocalRouteClaims
            if Claim.Signal == "A"
        )
        LocalClaim = replace(
            ExistingLocalClaim,
            BoundaryNodes=(ExistingLocalClaim.ConnectedTargets[0],),
        )
        ConflictPosition = min(LocalClaim.Nodes)
        Placed = replace(
            InitialPlacement.Placed,
            # Make A and B ordinary profiles again.  The claim below is
            # deliberately derived-only, so it is an optional value rather
            # than a pre-owned base obstacle.
            LocalRouteClaims=(),
            LocalNetTargets={},
            FrozenNetWires={},
            DerivedLocalRouteClaims=(LocalClaim,),
        )
        Placement = replace(InitialPlacement, Placed=Placed)
        Resources = BuildRoutingResources(Placed)
        MaterializedBySignal = Counter()
        NativeAssignmentCalls: list[tuple[object, ...]] = []

        def MaterializeControlledCandidate(
            Signal,
            Profile,
            SourcePortal,
            TargetPortals,
            Guide,
            Layer,
            Axis,
            Lane,
            Variant,
            RoutedTree,
            Region,
            CandidateResources,
            *Arguments,
            **KeywordArguments,
        ):
            del Axis, Variant, RoutedTree, Region, Arguments, KeywordArguments
            MaterializedBySignal[Signal] += 1
            if Signal == "A":
                CandidateId = "A:ordinary"
                Position = (30, 1, 30)
                MaterialCost = 100
            elif Signal == "B" and MaterializedBySignal[Signal] == 1:
                CandidateId = "B:conflicts-local"
                Position = ConflictPosition
                MaterialCost = 1
            elif Signal == "B":
                CandidateId = "B:compatible"
                Position = (40, 1, 40)
                MaterialCost = 2
            else:
                CandidateId = f"{Signal}:ordinary"
                Position = (50, 1, 50)
                MaterialCost = 1
            return NetRouteCandidate(
                CandidateId=CandidateId,
                Signal=Signal,
                SourcePortalId=SourcePortal.PortalId,
                TargetPortalIds={
                    Target: Portal.PortalId
                    for Target, Portal in zip(
                        Profile.Targets,
                        TargetPortals,
                    )
                },
                Nodes=frozenset((Position,)),
                Edges=frozenset(),
                Claims=CandidateResources.ResourceGraph.BuildRouteClaims(
                    (Position,)
                ),
                Layer=0,
                Guide=frozenset(Guide),
                RepeaterWaypoints=(),
                MaterialCost=MaterialCost,
                FootprintGrowth=1,
                Length=1,
                BendCount=0,
                ViaCount=0,
            )

        NativeRoutingContext = Flow.RustRoutingContext

        class RecordingRoutingContext:
            def __init__(self, *Arguments) -> None:
                self.Inner = NativeRoutingContext(*Arguments)

            def __getattr__(self, Name):
                return getattr(self.Inner, Name)

            def PlanAuthoritativeRoutesBounded(self, *Arguments):
                NativeAssignmentCalls.append(Arguments)
                return self.Inner.PlanAuthoritativeRoutesBounded(*Arguments)

        # Candidate trees are deliberately controlled, but the assignment
        # call remains the real Rust solver and is instrumented below.
        with patch.object(
            Portals,
            "_MaterializeCandidate",
            MaterializeControlledCandidate,
        ), patch.object(
            Flow,
            "RustRoutingContext",
            RecordingRoutingContext,
        ):
            Preparation = PrepareTrackAssignment(
                Placement,
                Resources=Resources,
                Policy=LocalFirstPhysicalDesignPolicy,
                Deadline=RoutingDeadline.Start(5.0),
            )

        self.assertTrue(Preparation.Success)
        self.assertTrue(Preparation.Complete)
        self.assertEqual(len(NativeAssignmentCalls), 1)
        self.assertEqual(
            dict(Preparation.SelectedCandidateIds),
            {
                "B": "B:compatible",
                "Z": "Z:ordinary",
            },
        )
        self.assertEqual(len(Preparation.SelectedLocalClaimChoiceIds), 1)
        SelectedSignal, SelectedChoiceId = (
            Preparation.SelectedLocalClaimChoiceIds[0]
        )
        self.assertEqual(SelectedSignal, "A")
        self.assertTrue(SelectedChoiceId.startswith("A:DerivedLocal:"))
        self.assertTrue(Preparation.LocalClaimDomainFingerprint)
        # The preparation is also the resource-bearing witness exported to
        # the pre-route interface selector.  It must retain claims from both
        # the selected local-tree value and the selected ordinary candidates;
        # otherwise component/template selection would see a false empty
        # capacity contract.
        SelectedCapacityResources = set(
            Preparation.SelectedCapacityResourceIds
        )
        self.assertTrue({
            str(ResourceId) for ResourceId in LocalClaim.Claims.ResourceIds
        }.issubset(SelectedCapacityResources))
        self.assertTrue({
            str(ResourceId)
            for ResourceId in Resources.ResourceGraph.BuildRouteClaims(
                ((40, 1, 40),)
            ).ResourceIds
        }.issubset(SelectedCapacityResources))
        EncodedValues = NativeAssignmentCalls[0][0]
        ConflictingCandidateClaims = Resources.ResourceGraph.BuildRouteClaims(
            (ConflictPosition,)
        )
        self.assertTrue(
            MandatoryClaimsConflict(
                LocalClaim.Claims,
                ConflictingCandidateClaims,
            )
        )
        self.assertIn(
            ("A", SelectedChoiceId),
            {(str(Value[0]), str(Value[1])) for Value in EncodedValues},
        )
        self.assertIn(
            ("B", "B:conflicts-local"),
            {(str(Value[0]), str(Value[1])) for Value in EncodedValues},
        )
        self.assertIn(
            ("B", "B:compatible"),
            {(str(Value[0]), str(Value[1])) for Value in EncodedValues},
        )

    def testFrozenTrackAssignmentRejectsSameIdWithMutatedClaims(
        self,
    ) -> None:
        """The frozen capacity witness owns its physical value domain.

        Candidate IDs are stable routing labels, not proof identities.  A
        regenerated candidate which keeps an ID but gains a physical claim
        must therefore be rejected before the authoritative route starts.
        """
        Module = ModuleIR(
            Name="FrozenTrackAssignmentCandidateDomain",
            Inputs=["A", "B"],
            Outputs=["Z"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["A"]),
                Gate("InputB", GateKind.INPUT, ["B"]),
                Gate("Nand", GateKind.NAND, ["Z"], ["A", "B"]),
                Gate("OutputZ", GateKind.OUTPUT, [], ["Z"]),
            ],
        )
        Netlist = NetlistIR(
            Top=Module.Name,
            Modules={Module.Name: Module},
        )
        Placement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=0,
            PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
            PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
        )
        Resources = BuildRoutingResources(Placement.Placed)
        Preparation = PrepareTrackAssignment(
            Placement,
            Resources=Resources,
            Policy=LocalFirstPhysicalDesignPolicy,
            Deadline=RoutingDeadline.Start(5.0),
        )

        self.assertTrue(Preparation.Success)
        self.assertTrue(Preparation.Complete)
        self.assertTrue(Preparation.CandidateDomainFingerprint)
        PinAccessWitnessFingerprint = str(dict(Preparation.Diagnostics).get(
            "PlacementPinAccessWitnessFingerprint",
            "",
        ))
        self.assertTrue(PinAccessWitnessFingerprint)
        self.assertEqual(
            Preparation.ToDictionary()["CandidateDomainFingerprint"],
            Preparation.CandidateDomainFingerprint,
        )

        OriginalMaterialize = Portals._MaterializeCandidate

        def MaterializeWithMutatedClaims(*Arguments, **KeywordArguments):
            Candidate = OriginalMaterialize(*Arguments, **KeywordArguments)
            Extra = (999, 1, 999)
            return replace(
                Candidate,
                Claims=RoutingResourceClaims(
                    WireCells=Candidate.Claims.WireCells | {Extra},
                    SupportCells=Candidate.Claims.SupportCells,
                    RequiredAirCells=Candidate.Claims.RequiredAirCells,
                    ElectricalCells=(
                        Candidate.Claims.ElectricalCells | {Extra}
                    ),
                ),
            )

        with patch.object(
            Portals,
            "_MaterializeCandidate",
            MaterializeWithMutatedClaims,
        ), self.assertRaises(RoutingStageError) as Raised:
            RoutePcbDesign(
                Placement,
                Resources=Resources,
                Policy=LocalFirstPhysicalDesignPolicy,
                Deadline=RoutingDeadline.Start(5.0),
                FrozenTrackAssignmentPreparation=Preparation,
            )

        self.assertEqual(
            Raised.exception.Failure.Stage,
            "FrozenTrackAssignmentHandoff",
        )
        Diagnostics = dict(Raised.exception.Failure.Diagnostics or {})
        self.assertEqual(
            Diagnostics["FrozenCandidateDomainFingerprint"],
            Preparation.CandidateDomainFingerprint,
        )
        self.assertNotEqual(
            Diagnostics["CurrentCandidateDomainFingerprint"],
            Preparation.CandidateDomainFingerprint,
        )

        MutatedPreparation = replace(
            Preparation,
            Diagnostics=tuple(
                (Key, Value)
                for Key, Value in Preparation.Diagnostics
                if Key != "PlacementPinAccessWitnessFingerprint"
            ) + (("PlacementPinAccessWitnessFingerprint", "mutated"),),
        )
        with self.assertRaises(RoutingStageError) as WitnessRaised:
            RoutePcbDesign(
                Placement,
                Resources=Resources,
                Policy=LocalFirstPhysicalDesignPolicy,
                Deadline=RoutingDeadline.Start(5.0),
                FrozenTrackAssignmentPreparation=MutatedPreparation,
            )
        self.assertEqual(
            WitnessRaised.exception.Failure.Stage,
            "FrozenTrackAssignmentHandoff",
        )
        WitnessDiagnostics = dict(
            WitnessRaised.exception.Failure.Diagnostics or {}
        )
        self.assertEqual(
            WitnessDiagnostics["FrozenPinAccessWitnessFingerprint"],
            "mutated",
        )
        self.assertEqual(
            WitnessDiagnostics["CurrentPinAccessWitnessFingerprint"],
            PinAccessWitnessFingerprint,
        )

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

    def testCompleteSeedDomainRetriesExactOnlyAfterDiscovery(self) -> None:
        self.assertFalse(
            ShouldRetryNegotiatedExactAssignment(0, True, True, True)
        )
        self.assertFalse(
            ShouldRetryNegotiatedExactAssignment(1, True, True, False)
        )
        self.assertTrue(
            ShouldRetryNegotiatedExactAssignment(1, True, True, True)
        )
        self.assertTrue(
            ShouldRetryNegotiatedExactAssignment(1, True, False, False)
        )

    def testFinalDiversityRetryReleasesFrozenPartialAssignment(self) -> None:
        self.assertFalse(
            ShouldReleaseFrozenPartialAssignment(False, 11, 11)
        )
        self.assertFalse(
            ShouldReleaseFrozenPartialAssignment(True, 9, 11)
        )
        self.assertFalse(
            ShouldReleaseFrozenPartialAssignment(True, 10, 11)
        )
        self.assertTrue(
            ShouldReleaseFrozenPartialAssignment(True, 11, 11)
        )

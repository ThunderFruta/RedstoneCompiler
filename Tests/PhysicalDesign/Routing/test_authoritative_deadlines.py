"""Deadlines contracts for authoritative routing."""

from ._authoritative_planner_contracts import *


class AuthoritativeDeadlinesTests(AuthoritativePlannerTestBase):
    def testRoutedComponentForeignEscapeUsesOneBoundedAlternateWindow(
        self,
    ) -> None:
        self.assertFalse(
            ShouldRejectRoutedComponentForeignEscape(
                HasRoutedComponentTemplate=True,
                IsSelectedForeignEscape=True,
                CandidateDiversityLevel=0,
                CandidateCount=0,
            )
        )
        self.assertTrue(
            ShouldRejectRoutedComponentForeignEscape(
                HasRoutedComponentTemplate=True,
                IsSelectedForeignEscape=True,
                CandidateDiversityLevel=1,
                CandidateCount=0,
            )
        )

    def testNegotiatedOffenderHaloUsesOneMaximalBoundedState(self) -> None:
        self.assertEqual(
            SelectNegotiatedOffenderHaloLaneDiversityLevel(0, 4),
            3,
        )
        self.assertEqual(
            SelectNegotiatedOffenderHaloLaneDiversityLevel(1, 4),
            3,
        )
        with self.assertRaisesRegex(
            ValueError,
            "no negotiated offender-halo state remains",
        ):
            SelectNegotiatedOffenderHaloLaneDiversityLevel(3, 4)

    def testBoundedPortfolioPortalProfileIsNotCutEvidence(self) -> None:
        Cache = replace(
            self.BuildRawPortalCache(
                object(),
                object(),
                {"Alpha": 2, "Beta": 2},
            ),
            RetainedPortfolioSliceLimited=True,
        )
        self.assertTrue(
            ShouldRetainBoundedPortfolioPortalProfile(True, 2, Cache)
        )
        self.assertFalse(
            ShouldRetainBoundedPortfolioPortalProfile(True, 1, Cache)
        )
        self.assertFalse(
            ShouldRetainBoundedPortfolioPortalProfile(False, 2, Cache)
        )
        self.assertFalse(
            ShouldRetainBoundedPortfolioPortalProfile(True, 2, None)
        )

        Failure = BuildBoundedPortfolioPortalSliceAdvanceFailure(
            ((("Alpha", "Beta"), frozenset({(1, 2, 3)})),),
            {"PortalGeneration": 0.25},
        )
        self.assertEqual(
            Failure.Reason,
            RoutingFailureReason.RuntimeBudgetExceeded,
        )
        self.assertIn("AdvancePlacementCandidate", Failure.RepairActions)
        self.assertFalse(
            Failure.Diagnostics["CompleteAssignmentCutProof"]
        )
        self.assertNotIn("ConflictGraph", Failure.Diagnostics)

    def testCoordinatedContinuationSelectsOneBoundedUnseenTranche(
        self,
    ) -> None:
        Arguments = {
            "CurrentRequestLimit": 16,
            "AvailableRequestCount": 120,
            "BaseRequestLimit": 8,
            "CandidateGrowthFactor": 2,
            "CoordinatedCandidateDiversityLevel": 1,
            "MaximumCandidateDiversityEscalations": 7,
            "ApplyCoordinatedContinuation": True,
        }
        self.assertEqual(
            SelectCoordinatedContinuationRequestWindowLimit(
                **Arguments,
            ),
            32,
        )
        self.assertEqual(
            SelectCoordinatedContinuationRequestWindowLimit(
                **{
                    **Arguments,
                    "AvailableRequestCount": 24,
                },
            ),
            24,
        )
        self.assertEqual(
            SelectCoordinatedContinuationRequestWindowLimit(
                **{
                    **Arguments,
                    "ApplyCoordinatedContinuation": False,
                },
            ),
            16,
        )
        self.assertEqual(
            SelectCoordinatedContinuationRequestWindowLimit(
                **{
                    **Arguments,
                    "CoordinatedCandidateDiversityLevel": 6,
                },
            ),
            16,
        )

    def testConflictAvoidancePositionsAreBoundedAndValidated(self) -> None:
        self.assertEqual(
            SelectConflictAvoidancePositions(
                {
                    "ResourceHotspots": [
                        [1, 2, 3],
                        ["4", "5", "6"],
                        [7, 8],
                        "invalid",
                    ],
                },
                MaximumPositions=2,
            ),
            frozenset({(1, 2, 3), (4, 5, 6)}),
        )

    def testCandidateRealizabilityNogoodAdmissionIsBoundedAndContained(
        self,
    ) -> None:
        Prior = (
            ClusterLeaseCandidateRealizabilityNogood(
                Signal="First",
                PatternFingerprint="bad-pattern",
                CandidateFailureFingerprint="failure",
            ),
        )
        self.assertTrue(
            ShouldRefineCandidateRealizabilityLeaseNogood(
                True,
                True,
                True,
                1,
                2,
                False,
                "new-pattern",
                Prior,
                8.0,
            )
        )
        for Overrides in (
            {"TopologyRequiresJointPortfolio": False},
            {"CompleteClusterInterfaceAccess": False},
            {"HasClusterBoundaryLeaseReservations": False},
            {"ReservationVariant": 2},
            {"SkipStrictPortalReservation": True},
            {"CurrentPatternFingerprint": "bad-pattern"},
            {"PriorNogoods": (*Prior, Prior[0])},
            {"RemainingSeconds": 4.9},
        ):
            Values = {
                "TopologyRequiresJointPortfolio": True,
                "CompleteClusterInterfaceAccess": True,
                "HasClusterBoundaryLeaseReservations": True,
                "ReservationVariant": 1,
                "MaximumPortalReservationAlternatives": 2,
                "SkipStrictPortalReservation": False,
                "CurrentPatternFingerprint": "new-pattern",
                "PriorNogoods": Prior,
                "RemainingSeconds": 8.0,
                **Overrides,
            }
            with self.subTest(Overrides=Overrides):
                self.assertFalse(
                    ShouldRefineCandidateRealizabilityLeaseNogood(
                        **Values
                    )
                )
        OtherSignalPrior = (
            Prior[0],
            ClusterLeaseCandidateRealizabilityNogood(
                Signal="Second",
                PatternFingerprint="second-pattern",
                CandidateFailureFingerprint="second-failure",
            ),
        )
        self.assertFalse(
            ShouldRefineCandidateRealizabilityLeaseNogood(
                True,
                True,
                True,
                1,
                2,
                False,
                "target-pattern",
                OtherSignalPrior,
                8.0,
                Signal="Target",
            )
        )
        self.assertFalse(
            ShouldRefineCandidateRealizabilityLeaseNogood(
                True,
                True,
                True,
                1,
                2,
                False,
                "target-pattern",
                (
                    *OtherSignalPrior,
                    ClusterLeaseCandidateRealizabilityNogood(
                        Signal="Third",
                        PatternFingerprint="third-pattern",
                        CandidateFailureFingerprint="third-failure",
                    ),
                ),
                8.0,
                Signal="Target",
            )
        )

    def testPortalTupleCompletenessRejectsBoundedDiagonalSample(self) -> None:
        self.assertFalse(PortalTupleFeasibilityDomainIsComplete((
            {
                "CompletePortalTupleCount": 759,
                "EvaluatedPortalTupleCount": 13,
            },
            {
                "CompletePortalTupleCount": 736,
                "EvaluatedPortalTupleCount": 13,
            },
        )))
        self.assertTrue(PortalTupleFeasibilityDomainIsComplete((
            {
                "CompletePortalTupleCount": 12,
                "EvaluatedPortalTupleCount": 12,
            },
            {
                "CompletePortalTupleCount": 16,
                "EvaluatedPortalTupleCount": 16,
            },
        )))

    def testIncompleteMandatoryPortalPairFactorIsNotPromoted(self) -> None:
        Graph, Fixed, Domains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0),)
        )
        Certificate = SolveMandatoryPortalPairFeasibility(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="incomplete-domain",
            ShouldStop=lambda: True,
        )

        self.assertFalse(Certificate.Complete)
        self.assertIsNone(Certificate.Feasible)
        self.assertEqual(SelectCertifiedMandatoryPortalPairCuts((Certificate,)), ())

    def testBoundaryMandatoryPortalPairRelationIncompletePromotesNothing(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
            ShouldStop=lambda: True,
        )

        self.assertFalse(Relation.Complete)
        self.assertEqual(Relation.UnsatisfiableApertureClauses, ())
        self.assertNotIn(
            Relation.RelationFingerprint,
            Resources.PhysicalBoundaryMandatoryPortalPairRelationCache,
        )

    def testPhysicalGlobalPairSupportClosesIncompletePartnerDomain(
        self,
    ) -> None:
        def Candidate(Node):
            Claims = SimpleNamespace(
                WireCells=frozenset((Node,)),
                SupportCells=frozenset(),
                RequiredAirCells=frozenset(),
                ElectricalCells=frozenset((Node,)),
            )
            return SimpleNamespace(Claims=Claims)

        Candidates = {
            "Complete": (Candidate((0, 2, 0)),),
            "BlockedOpen": (Candidate((0, 2, 0)),),
            "SupportedOpen": (Candidate((4, 2, 0)),),
        }

        self.assertEqual(
            SelectPhysicalGlobalPairSupportSuffixSignals(
                Candidates,
                ("Complete",),
                {
                    "Complete": 0,
                    "BlockedOpen": 8,
                    "SupportedOpen": 8,
                },
            ),
            ("BlockedOpen",),
        )

    def testIncompleteNativePairClassificationCannotCloseClause(self) -> None:
        Result = SimpleNamespace(
            PairwiseCompatibilityComplete=False,
            PairwiseIncompatibleSignals=(("Alpha", "Beta"),),
        )
        Remaining = {"Alpha": 0, "Beta": 0}

        self.assertEqual(
            SelectPhysicalGlobalNativePairCutSuffixSignals(
                Result,
                Remaining,
            ),
            (),
        )
        self.assertEqual(
            SelectCompletedPhysicalGlobalPairNoGoodEdges(
                Result,
                Remaining,
            ),
            (),
        )

    def testPhysicalGlobalPlanYieldDeadlineServesAdmittedFrontiers(self) -> None:
        StartedAt = monotonic()
        Shared = RoutingDeadline(
            StartedAt=StartedAt,
            ExpiresAt=StartedAt + 100.0,
        )

        First = BuildPhysicalGlobalPlanYieldDeadline(Shared, 0)
        WithRetained = BuildPhysicalGlobalPlanYieldDeadline(Shared, 2)
        SelectedRetained = BuildPhysicalGlobalPlanYieldDeadline(
            Shared,
            1,
            CurrentPlanWasRetained=True,
        )

        self.assertAlmostEqual(First.ExpiresAt, Shared.ExpiresAt, delta=0.05)
        self.assertLess(WithRetained.ExpiresAt, First.ExpiresAt)
        self.assertAlmostEqual(
            SelectedRetained.ExpiresAt,
            Shared.ExpiresAt,
            delta=0.05,
        )
        self.assertGreater(First.RemainingSeconds(), 90.0)
        self.assertGreater(WithRetained.RemainingSeconds(), 20.0)
        with self.assertRaises(ValueError):
            BuildPhysicalGlobalPlanYieldDeadline(
                Shared,
                0,
                CurrentPlanWasRetained=True,
            )

    def testPhysicalGlobalNoGoodBranchPreservesNativeDeadline(self) -> None:
        Calls = 0

        def PlanNative(Values):
            nonlocal Calls
            Calls += 1
            if Calls == 1:
                return SimpleNamespace(
                    Success=True,
                    SelectedCandidateIds=(("A", "A0"), ("B", "B0")),
                    ExpansionCount=1,
                    CompletedWork=1,
                    BudgetExhausted=False,
                    DeadlineExceeded=False,
                    ConflictSignals=(),
                )
            return SimpleNamespace(
                Success=False,
                SelectedCandidateIds=(),
                ExpansionCount=2,
                CompletedWork=2,
                BudgetExhausted=False,
                DeadlineExceeded=True,
                ConflictSignals=("A",),
            )

        Result = PlanPhysicalGlobalAssignmentAvoidingExactNoGoods(
            (("A", "A0"), ("A", "A1"), ("B", "B0"), ("B", "B1")),
            (frozenset((("A", "A0"), ("B", "B0"))),),
            PlanNative,
        )

        self.assertTrue(Result.DeadlineExceeded)
        self.assertFalse(Result.Success)
        self.assertEqual(Calls, 2)

    def testCompleteInterfaceWorkLimitIsIncompleteNotUnsatisfiable(
        self,
    ) -> None:
        Signal = "TwoTerminal"
        Root = (0, 1, 0)
        Target = (10, 1, 0)
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
            Span=10,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
        )

        with self.assertRaises(RoutingStageError) as Context:
            ReserveClusterBoundaryLeases(
                {
                    (Signal, Root, 0): (
                        self.BuildPortal(
                            Signal,
                            Root,
                            (1, 1, 0),
                        ),
                    ),
                    (Signal, Target, 0): (
                        self.BuildPortal(
                            Signal,
                            Target,
                            (9, 1, 0),
                        ),
                    ),
                },
                {Signal: Profile},
                Resources,
                MaximumExpansions=1,
                RequireCompleteClusterInterfaceDomain=True,
            )

        Failure = Context.exception.Failure
        self.assertEqual(
            Failure.Reason,
            RoutingFailureReason.ClusterInterfaceSolveIncomplete,
        )
        self.assertFalse(
            Failure.Diagnostics["ClusterInterfaceDomainComplete"]
        )
        self.assertFalse(
            Failure.Diagnostics["OwnershipSearchComplete"]
        )

    def testLegacyDeadlineBatchDoesNotInventCompletedPrefix(self) -> None:
        LegacyBatch = SimpleNamespace(
            CompletedWork=2,
            DeadlineExceeded=True,
        )

        self.assertEqual(
            ReadRouteTreeBatchCompletionMask(LegacyBatch, 4),
            (False, False, False, False),
        )
        with self.assertRaisesRegex(ValueError, "disagrees"):
            ReadRouteTreeBatchCompletionMask(
                SimpleNamespace(
                    CompletionMask=(True, False),
                    CompletedWork=2,
                    DeadlineExceeded=True,
                ),
                2,
            )

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

    def testRepeaterReadyPortalDomainAddsBoundedStraightLanding(
        self,
    ) -> None:
        Graph, Region, _Context = self.BuildGraph()
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Portal = PinAccessPortal(
            PortalId="Signal:portal",
            Signal="Signal",
            Terminal=(0, 1, 0),
            Layer=0,
            Path=((0, 1, 0), (1, 1, 0)),
            Edges=frozenset({
                ((0, 1, 0), (1, 1, 0)),
            }),
            Claims=Graph.BuildRouteClaims(
                ((0, 1, 0), (1, 1, 0))
            ),
            Length=2,
            BendCount=0,
            ViaCount=0,
            Cost=2,
        )
        Other = replace(
            Portal,
            PortalId="Other:portal",
            Signal="Other",
        )
        Domains, Diagnostics = BuildRepeaterReadyPortalDomains(
            {
                ("Signal", (0, 1, 0), 0): (Portal,),
                ("Other", (0, 1, 0), 0): (Other,),
            },
            frozenset(("Signal",)),
            Region,
            Resources,
            ExtensionLength=3,
            MaximumExtensionsPerPortal=2,
        )

        Extended = Domains[("Signal", (0, 1, 0), 0)][0]
        self.assertIn(":repeater-ready:", Extended.PortalId)
        self.assertEqual(
            Extended.Path[-3:],
            ((2, 1, 0), (3, 1, 0), (4, 1, 0)),
        )
        self.assertEqual(
            Domains[("Other", (0, 1, 0), 0)],
            (Other,),
        )
        self.assertEqual(Diagnostics["ExtendedPortalCount"], 1)
        self.assertEqual(Diagnostics["ExtendedSignals"], ["Signal"])

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

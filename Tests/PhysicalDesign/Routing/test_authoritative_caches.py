"""Caches contracts for authoritative routing."""

from ._authoritative_planner_contracts import *


class AuthoritativeCachesTests(AuthoritativePlannerTestBase):
    def testEmptyPhysicalCandidateDomainClassificationSeparatesProofFromIdentity(
        self,
    ) -> None:
        CompleteEmpty, IdentityMismatch = (
            ClassifyEmptyPhysicalCandidateDomains(
                {
                    "CompleteEmpty": (),
                    "StaleOnly": (),
                    "Retained": (SimpleNamespace(CandidateId="route"),),
                },
                {
                    "StaleOnly": ("stale-route",),
                    "Retained": ("unrelated-stale-route",),
                },
            )
        )

        self.assertEqual(CompleteEmpty, ("CompleteEmpty",))
        self.assertEqual(IdentityMismatch, ("StaleOnly",))

        CompleteEmpty, IdentityMismatch = (
            ClassifyEmptyPhysicalCandidateDomains(
                {"CertifiedCurrent": ()},
                {"CertifiedCurrent": ("older-portal-route",)},
                CertifiedCurrentEmptyDomainSignals=(
                    "CertifiedCurrent",
                ),
            )
        )
        self.assertEqual(CompleteEmpty, ("CertifiedCurrent",))
        self.assertEqual(IdentityMismatch, ())

    def testPhysicalCandidatePortalFilterRejectsStaleRetainedIdentity(
        self,
    ) -> None:
        Current = SimpleNamespace(
            CandidateId="current",
            SourcePortalId="source-current",
            TargetPortalIds={(9, 1, 0): "target-current"},
        )
        StaleSource = SimpleNamespace(
            CandidateId="stale-source",
            SourcePortalId="source-old",
            TargetPortalIds={(9, 1, 0): "target-current"},
        )
        StaleTarget = SimpleNamespace(
            CandidateId="stale-target",
            SourcePortalId="source-current",
            TargetPortalIds={(9, 1, 0): "target-old"},
        )
        Portals = {
            (0, 1, 0): (SimpleNamespace(PortalId="source-current"),),
            (9, 1, 0): (SimpleNamespace(PortalId="target-current"),),
        }

        Filtered, Removed = FilterPhysicalCandidatesToCurrentPortalDomain(
            {"NandNet26": (StaleSource, Current, StaleTarget)},
            Portals,
        )

        self.assertEqual(Filtered, {"NandNet26": (Current,)})
        self.assertEqual(
            Removed,
            {"NandNet26": ("stale-source", "stale-target")},
        )

    def testFlatRouteRequestRetainsGeneratedLane(self) -> None:
        Selected = SelectAuthoritativeRouteRequestGuide(
            ((0, 0), (3, 2)),
            "X",
            1,
        )

        self.assertEqual(
            Selected,
            frozenset({
                (0, 0),
                (0, 1),
                (1, 1),
                (2, 1),
                (3, 1),
                (3, 2),
            }),
        )

    def testPhysicalRequestIdentityCollapsesAxisLaneAliases(self) -> None:
        Arguments = (
            "source",
            ("target",),
            frozenset({(0, 0), (1, 0)}),
            1,
        )

        First = BuildCandidateRequestGeometryIdentity(
            *Arguments,
            "X",
            3,
            ImmutablePhysicalGuide=True,
        )
        Second = BuildCandidateRequestGeometryIdentity(
            *Arguments,
            "Z",
            27,
            ImmutablePhysicalGuide=True,
        )

        self.assertEqual(First, Second)

    def testFlatRequestIdentityPreservesAxisLaneDiversity(self) -> None:
        Arguments = (
            "source",
            ("target",),
            frozenset({(0, 0), (1, 0)}),
            1,
        )

        First = BuildCandidateRequestGeometryIdentity(
            *Arguments,
            "X",
            3,
            ImmutablePhysicalGuide=False,
        )
        Second = BuildCandidateRequestGeometryIdentity(
            *Arguments,
            "Z",
            27,
            ImmutablePhysicalGuide=False,
        )

        self.assertNotEqual(First, Second)

    def testRetainedJointPortfolioAdvancesAfterInitialStarvation(
        self,
    ) -> None:
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
        for Overrides in (
            {"PlacementWasRelocated": False},
            {"ExactLegalRetainedJointStateCount": 1},
            {"HasCumulativeAssignmentConstraints": False},
            {"CandidateDiversityLevel": 1},
            {"ReservationVariant": 1},
            {"LaneDiversityLevel": 1},
            {"SkipStrictPortalReservation": True},
            {"RoutedTreeCount": 1},
            {"MaterializedCandidateCount": 1},
        ):
            Arguments = {
                "PlacementWasRelocated": True,
                "ExactLegalRetainedJointStateCount": 6,
                "HasCumulativeAssignmentConstraints": True,
                "CandidateDiversityLevel": 0,
                "ReservationVariant": 0,
                "LaneDiversityLevel": 0,
                "SkipStrictPortalReservation": False,
                "RoutedTreeCount": 0,
                "MaterializedCandidateCount": 0,
                **Overrides,
            }
            self.assertFalse(
                ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
                    **Arguments,
                )
            )

    def testRetainedJointPortfolioAdvancesWhenEveryTreeHitsFixedSeed(
        self,
    ) -> None:
        Arguments = {
            "PlacementWasRelocated": True,
            "ExactLegalRetainedJointStateCount": 6,
            "HasCumulativeAssignmentConstraints": True,
            "CandidateDiversityLevel": 0,
            "ReservationVariant": 0,
            "LaneDiversityLevel": 0,
            "SkipStrictPortalReservation": False,
            "RoutedTreeCount": 8,
            "MaterializedCandidateCount": 0,
            "AllRoutedTreesRejectedByFixedLegality": True,
            "RepeatedCandidateStarvationClass": True,
        }
        self.assertTrue(
            ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
                **Arguments,
            )
        )
        self.assertFalse(
            ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
                **{
                    **Arguments,
                    "AllRoutedTreesRejectedByFixedLegality": False,
                },
            )
        )
        self.assertFalse(
            ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
                **{
                    **Arguments,
                    "MaterializedCandidateCount": 1,
                },
            )
        )
        self.assertFalse(
            ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
                **{
                    **Arguments,
                    "RepeatedCandidateStarvationClass": False,
                },
            )
        )

    def testRetainedJointPortfolioCountIsRenameAndOrderIndependent(
        self,
    ) -> None:
        OriginalStates = [
            {
                "CandidateIndex": Index,
                "ExactLegal": True,
                "MandatoryAccessOwnershipFingerprint": f"access-{Index}",
                "Signals": ["Generate0", f"NandNet{Index}"],
            }
            for Index in range(6)
        ]
        RenamedStates = [
            {
                **State,
                "CandidateIndex": Index + 100,
                "Signals": [
                    f"Arbitrary{Index * 17}",
                    f"Renamed{Index * 31}",
                ],
            }
            for Index, State in enumerate(reversed(OriginalStates))
        ]

        OriginalCount = CountExactLegalRetainedJointStates({
            "__JointClusterPlacement__": {
                "ExactLegalRetainedStates": OriginalStates,
            },
        })
        RenamedCount = CountExactLegalRetainedJointStates({
            "__JointClusterPlacement__": {
                "ExactLegalRetainedStates": RenamedStates,
            },
        })
        self.assertEqual(OriginalCount, 6)
        self.assertEqual(RenamedCount, OriginalCount)
        self.assertEqual(
            ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
                True,
                OriginalCount,
                True,
                0,
                0,
                0,
                False,
                0,
                0,
            ),
            ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation(
                True,
                RenamedCount,
                True,
                0,
                0,
                0,
                False,
                0,
                0,
            ),
        )

    def testRetainedJointPortfolioCountFallsBackToExactLegalStates(
        self,
    ) -> None:
        self.assertEqual(
            CountExactLegalRetainedJointStates({
                "__JointClusterPlacement__": {
                    "RetainedStates": [
                        {"CandidateIndex": 0, "ExactLegal": True},
                        {"CandidateIndex": 1, "ExactLegal": False},
                        {"CandidateIndex": 2, "ExactLegal": True},
                    ],
                },
            }),
            2,
        )
        self.assertEqual(
            CountExactLegalRetainedJointStates({
                "__JointClusterPlacement__": {
                    "RemainingExactLegalRetainedStateCount": 1,
                    "ExactLegalRetainedStates": [
                        {"CandidateIndex": 0},
                        {"CandidateIndex": 1},
                    ],
                },
            }),
            1,
        )

    def testSoleRetainedCutCandidateCanUseDeferredWindow(self) -> None:
        Arguments = {
            "PlacementWasRelocated": True,
            "ExactLegalRetainedJointStateCount": 1,
            "HasCumulativeAssignmentConstraints": True,
            "Signal": "CutSignal",
            "JointAssignmentConstraintSignals": frozenset({
                "CutSignal",
                "PeerSignal",
            }),
        }
        self.assertTrue(
            ShouldContinueSoleRetainedCutCandidateStarvation(
                **Arguments
            )
        )
        for Key, Value in (
            ("PlacementWasRelocated", False),
            ("ExactLegalRetainedJointStateCount", 0),
            ("ExactLegalRetainedJointStateCount", 2),
            ("HasCumulativeAssignmentConstraints", False),
            ("Signal", "UnreportedSignal"),
        ):
            with self.subTest(Key=Key, Value=Value):
                self.assertFalse(
                    ShouldContinueSoleRetainedCutCandidateStarvation(
                        **{
                            **Arguments,
                            Key: Value,
                        }
                    )
                )

    def testExactCompletionRetainsStrictProofWhenItIsOnlyTier(
        self,
    ) -> None:
        Requests, Mode = SelectExactAssignmentCompletionRequestBatch(
            [0, 2],
            {
                0: (0, 0, 0),
                2: (0, 0, 2),
            },
            1,
            True,
            QuickDiscoveryEnabled=False,
        )

        self.assertEqual(Requests, (0,))
        self.assertEqual(Mode, "strict-proof")

    def testExactCompletionRetainsNegotiationReserveOutsidePortfolioCut(
        self,
    ) -> None:
        self.assertEqual(
            SelectExactAssignmentCompletionReserveMilliseconds(
                False,
                162,
                True,
                60.0,
            ),
            15_000,
        )
        self.assertEqual(
            SelectExactAssignmentCompletionReserveMilliseconds(
                False,
                300,
                False,
                60.0,
            ),
            15_000,
        )

    def testPortalSliceLimitRequiresRetainedTopologyPortfolio(self) -> None:
        Arguments = {
            "AdaptiveRoutingEnabled": True,
            "ApplyStagedPortfolioProof": True,
            "ExactLegalRetainedJointStateCount": 2,
            "RawPortalCachePresent": False,
            "RemainingSeconds": 8.0,
            "PortalLimit": 10,
        }
        self.assertTrue(
            ShouldLimitRetainedPortfolioPortalDomain(**Arguments)
        )
        for Key, Value in (
            ("AdaptiveRoutingEnabled", False),
            ("ApplyStagedPortfolioProof", False),
            ("ExactLegalRetainedJointStateCount", 1),
            ("RawPortalCachePresent", True),
            ("RemainingSeconds", 25.0),
            ("PortalLimit", 2),
        ):
            with self.subTest(Key=Key):
                self.assertFalse(
                    ShouldLimitRetainedPortfolioPortalDomain(
                        **{
                            **Arguments,
                            Key: Value,
                        }
                    )
                )
        with self.assertRaises(ValueError):
            ShouldLimitRetainedPortfolioPortalDomain(
                **Arguments,
                MaximumSliceSeconds=0,
            )

    def testMandatoryPortalPrescreenSkipsOnlyImmutableRetainedDomains(
        self,
    ) -> None:
        self.assertFalse(
            ShouldPrepareMandatoryPortalTuples(
                HasMaterializedCandidates=False,
                HasRetainedCandidates=True,
                RegenerateSignal=False,
            )
        )
        self.assertTrue(
            ShouldPrepareMandatoryPortalTuples(
                HasMaterializedCandidates=False,
                HasRetainedCandidates=True,
                RegenerateSignal=True,
            )
        )
        self.assertTrue(
            ShouldPrepareMandatoryPortalTuples(
                HasMaterializedCandidates=False,
                HasRetainedCandidates=False,
                RegenerateSignal=False,
            )
        )

    def testStructuredFailureRetainsCompleteMeasuredWorkTelemetry(
        self,
    ) -> None:
        ConflictGraph = {
            "Classification": "higher-order-placement-conflict",
        }
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            Diagnostics={"ConflictGraph": ConflictGraph},
        )
        Error = BuildTelemetryRoutingStageError(
            Failure,
            {
                "ConflictGraph": ConflictGraph,
                "GlobalGuidePlanCacheHit": True,
                "ResourceGraphCacheHit": True,
                "PortalCacheHit": True,
                "CandidateRequestConstructionSeconds": 1.25,
                "InitialNativeCandidateBatchSeconds": 2.5,
                "StageTimingsSeconds": {
                    "GlobalGuidePlanning": 0.0,
                    "ResourceGraph": 0.0,
                    "PortalGeneration": 0.0,
                    "CandidateGeneration": 3.75,
                    "Assignment": 0.125,
                },
            },
        )

        self.assertIsInstance(Error, RoutingStageError)
        self.assertEqual(Error.Failure.Stage, "TrackAssignment")
        self.assertEqual(
            Error.Failure.Diagnostics["ConflictGraph"],
            ConflictGraph,
        )
        self.assertTrue(
            Error.Failure.Diagnostics["GlobalGuidePlanCacheHit"]
        )
        self.assertTrue(
            Error.Failure.Diagnostics["ResourceGraphCacheHit"]
        )
        self.assertTrue(Error.Failure.Diagnostics["PortalCacheHit"])
        self.assertEqual(
            Error.Failure.Diagnostics[
                "InitialNativeCandidateBatchSeconds"
            ],
            2.5,
        )
        self.assertEqual(
            Error.Failure.Diagnostics["StageTimingsSeconds"]["Assignment"],
            0.125,
        )

    def testCandidateFailureFingerprintCountsOnlyExactCandidateRetries(
        self,
    ) -> None:
        History = (
            {
                "Stage": "CandidateGeneration",
                "CandidateFailureFingerprint": "same",
            },
            {
                "Stage": "TrackAssignment",
                "CandidateFailureFingerprint": "same",
            },
            {
                "Stage": "CandidateGeneration",
                "CandidateFailureFingerprint": "different",
            },
        )

        self.assertEqual(
            CountPriorCandidateFailureFingerprint(History, "same"),
            1,
        )
        self.assertEqual(
            CountPriorCandidateFailureFingerprint(History, "missing"),
            0,
        )

    def testCandidateRequestDomainFingerprintCountsOnlyExactDomains(
        self,
    ) -> None:
        History = (
            {
                "Stage": "CandidateGeneration",
                "CandidateRequestDomainFingerprint": "same-domain",
            },
            {
                "Stage": "CandidateGeneration",
                "CandidateRequestDomainFingerprint": "other-domain",
            },
            {
                "Stage": "TrackAssignment",
                "CandidateRequestDomainFingerprint": "same-domain",
            },
        )

        self.assertEqual(
            CountPriorCandidateRequestDomainFingerprint(
                History,
                "same-domain",
            ),
            1,
        )
        self.assertEqual(
            CountPriorCandidateRequestDomainFingerprint(
                History,
                "missing-domain",
            ),
            0,
        )

    def testPartialAssignmentCacheFreezesSelectedNonOffenders(self) -> None:
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
        Alternate = replace(First, CandidateId="A1")
        Offender = replace(First, Signal="B", CandidateId="B0")
        Candidates, Metadata = RetainPartialAssignmentCandidateCache(
            {"A": [First, Alternate], "B": [Offender]},
            {
                "A": {"A0": ("X", 1), "A1": ("Z", 2)},
                "B": {"B0": ("X", 3)},
            },
            [("A", "A1"), ("B", "B0")],
            frozenset({"B"}),
        )
        self.assertEqual(
            tuple(Candidate.CandidateId for Candidate in Candidates["A"]),
            ("A1",),
        )
        self.assertEqual(Metadata, {"A": {"A1": ("Z", 2)}})
        self.assertNotIn("B", Candidates)

    def testCutOnlyCandidateControlsRetainUnaffectedDomains(self) -> None:
        for Action in (
            "regenerate-affected-candidates",
            "increase-guide-lane-diversity",
            "add-routing-layer",
            "alternate-portal-slots",
            "diversify-repeated-candidate-cut",
            "try-bounded-unreserved-portals",
            "final-bounded-unreserved-portals",
            "alternate-complete-cluster-interface-lease",
            "exclude-candidate-unrealizable-cluster-lease-template",
        ):
            with self.subTest(Action=Action):
                self.assertTrue(
                    ShouldRetainUnaffectedCandidatesForControl(Action)
                )
        for Action in (
            "replace-complete-candidate-domain",
        ):
            with self.subTest(Action=Action):
                self.assertFalse(
                    ShouldRetainUnaffectedCandidatesForControl(Action)
                )

    def testCompleteClusterLeasePortfolioAdvancesWithoutUnreservedReplay(
        self,
    ) -> None:
        self.assertTrue(
            ShouldDiversifyStarvedCompleteClusterLeaseEndpoint(
                True,
                True,
                True,
                1,
                False,
                2,
                False,
            )
        )
        self.assertFalse(
            ShouldDiversifyStarvedCompleteClusterLeaseEndpoint(
                True,
                True,
                True,
                1,
                False,
                2,
                True,
            )
        )
        self.assertTrue(
            ShouldAdvanceAfterCompleteClusterLeasePortfolio(
                True,
                True,
                True,
                3,
                False,
                4,
                True,
            )
        )
        self.assertFalse(
            ShouldAdvanceAfterCompleteClusterLeasePortfolio(
                True,
                True,
                True,
                2,
                False,
                4,
                False,
            )
        )
        self.assertFalse(
            ShouldAdvanceAfterCompleteClusterLeasePortfolio(
                False,
                True,
                True,
                2,
                False,
                4,
                True,
                )
            )

    def testCandidateRealizabilityProbeRetainsEndgameReserve(
        self,
    ) -> None:
        self.assertEqual(
            SelectCandidateRealizabilityProbeSliceSeconds(60.0),
            10.0,
        )
        self.assertEqual(
            SelectCandidateRealizabilityProbeSliceSeconds(20.0),
            8.0,
        )
        self.assertEqual(
            SelectCandidateRealizabilityProbeSliceSeconds(16.9),
            0.0,
        )
        with self.assertRaises(ValueError):
            SelectCandidateRealizabilityProbeSliceSeconds(
                60.0,
                MinimumProbeSeconds=0.0,
            )

    def testAnonymousCandidateDomainFingerprintOrdersResourceIds(
        self,
    ) -> None:
        Candidate = SimpleNamespace(
            Claims=SimpleNamespace(
                ResourceIds=frozenset({
                    RoutingResourceId(
                        RoutingResourceKind.Wire,
                        (2, 1, 0),
                    ),
                    RoutingResourceId(
                        RoutingResourceKind.Support,
                        (1, 0, 0),
                    ),
                }),
            ),
        )
        self.assertEqual(
            BuildAnonymousCandidateDomainFingerprint([Candidate]),
            BuildAnonymousCandidateDomainFingerprint([Candidate]),
        )

    def testRetainedPortalWitnessCapIsNotACompleteCutDomain(self) -> None:
        TruncatedWitnessLayer = {
            "Layer": 0,
            "CompletePortalTupleCount": 64,
            "EvaluatedPortalTupleCount": 64,
            "PortalTupleDomainComplete": False,
            "PortalTupleExhaustiveSearchComplete": True,
            "PortalTupleEmptyProofComplete": False,
            "RetainedLegalWitnessDomainComplete": False,
            "DiscoveredLegalPortalTupleCount": 24,
            "LegalPortalTupleCount": 16,
        }
        self.assertFalse(PortalTupleFeasibilityDomainIsComplete(
            (TruncatedWitnessLayer,),
            ExpectedLayers=(0,),
        ))
        self.assertFalse(PortalTupleEmptyProofDomainIsComplete(
            (TruncatedWitnessLayer,),
            ExpectedLayers=(0,),
        ))

    def testNonPrefixPortalCompletionPublishesAndReplaysExactKeys(
        self,
    ) -> None:
        Metadata = tuple(
            ("SignalA", (Index, 0, 0), 0)
            for Index in range(4)
        )
        Results = ("zero", "one", "two", "three")
        self.assertEqual(
            SelectMatchingPartialPortalReplaySignals(
                ("SignalA", "ChangedSignal"),
                {
                    "SignalA": "same-domain",
                    "ChangedSignal": "new-domain",
                },
                {
                    "SignalA": "same-domain",
                    "ChangedSignal": "old-domain",
                },
                False,
            ),
            frozenset(("SignalA",)),
        )
        self.assertEqual(
            SelectMatchingPartialPortalReplaySignals(
                ("SignalA",),
                {"SignalA": "same-domain"},
                {"SignalA": "same-domain"},
                True,
            ),
            frozenset(),
        )
        CompletedEntries = SelectCompletedPortalBatchEntries(
            Metadata,
            Results,
            (False, True, False, True),
        )
        self.assertEqual(
            CompletedEntries,
            (
                (Metadata[1], "one"),
                (Metadata[3], "three"),
            ),
        )

        CachedKey = ("SignalA", (-1, 0, 0), 0)
        PublishedEntries, PublishedKeys = MergePartialRawPortalBatchWork(
            ((CachedKey, ()),),
            tuple((Key, ()) for Key, _Value in CompletedEntries),
            (CachedKey,),
            tuple(Key for Key, _Value in CompletedEntries),
            ("SignalA",),
            True,
        )
        self.assertEqual(
            {Key for Key, _Values in PublishedEntries},
            {CachedKey, Metadata[1], Metadata[3]},
        )
        self.assertEqual(
            set(PublishedKeys),
            {CachedKey, Metadata[1], Metadata[3]},
        )

        ReplayEntries, ReplayKeys = MergePartialRawPortalBatchWork(
            PublishedEntries,
            ((Metadata[2], ()),),
            PublishedKeys,
            (Metadata[2],),
            ("SignalA",),
            True,
        )
        self.assertEqual(
            {Key for Key, _Values in ReplayEntries},
            {CachedKey, Metadata[1], Metadata[2], Metadata[3]},
        )
        self.assertEqual(
            set(ReplayKeys),
            {CachedKey, Metadata[1], Metadata[2], Metadata[3]},
        )

    def testDescriptorProgressDeadlineReplayExceedsLruAndFinishesTwoSlices(
        self,
    ) -> None:
        Descriptors = tuple(f"descriptor-{Index:04d}" for Index in range(700))
        FirstSlice = frozenset(Descriptors[::2])
        SecondSlice = frozenset(Descriptors[1::2])
        CandidateA = SimpleNamespace(CandidateId="candidate-a", Payload="a")
        CandidateB = SimpleNamespace(CandidateId="candidate-b", Payload="b")

        ProgressCache = {}
        First, FirstAdvanced = (
            RetainPhysicalSignalRouteDomainDescriptorProgress(
                ProgressCache,
                PreSiblingDomainFingerprint="stable-domain",
                Signal="Alpha",
                RequestDomainFingerprint="request-domain",
                RequestDescriptorFingerprints=Descriptors,
                CompletedDescriptorFingerprints=FirstSlice,
                Candidates=(CandidateA,),
                CandidateMetadata={"candidate-a": ("X", 1, 0, 3)},
            )
        )
        self.assertTrue(FirstAdvanced)
        self.assertFalse(First.Complete)
        self.assertEqual(
            First.RemainingDescriptorFingerprints,
            SecondSlice,
        )

        RawLru = {}
        RetainPhysicalGlobalRouteTreeResults(
            RawLru,
            ((Descriptor, None) for Descriptor in Descriptors),
        )
        self.assertEqual(len(RawLru), 512)
        self.assertGreater(
            len(First.CompletedDescriptorFingerprints),
            len(RawLru) // 2,
        )

        Replayed = SelectReplayablePhysicalSignalRouteDomainContinuation(
            ProgressCache,
            "stable-domain",
            "Alpha",
            "request-domain",
            Descriptors,
        )
        self.assertIs(Replayed, First)

        ReorderedRows = SelectPendingPhysicalRouteDescriptorRows(
            tuple(reversed(Descriptors)),
            tuple(reversed(Descriptors)),
            tuple(reversed(Descriptors)),
            First.CompletedDescriptorFingerprints,
        )
        self.assertEqual(
            tuple(Row[2] for Row in ReorderedRows),
            tuple(reversed(Descriptors[1::2])),
        )
        self.assertFalse(
            First.CompletedDescriptorFingerprints
            & frozenset(Row[2] for Row in ReorderedRows)
        )

        Second, SecondAdvanced = (
            RetainPhysicalSignalRouteDomainDescriptorProgress(
                ProgressCache,
                PreSiblingDomainFingerprint="stable-domain",
                Signal="Alpha",
                RequestDomainFingerprint="request-domain",
                RequestDescriptorFingerprints=Descriptors,
                CompletedDescriptorFingerprints=SecondSlice,
                Candidates=(CandidateA, CandidateB),
                CandidateMetadata={
                    "candidate-a": ("X", 1, 0, 3),
                    "candidate-b": ("Z", 2, 1, 4),
                },
            )
        )
        self.assertTrue(SecondAdvanced)
        self.assertTrue(Second.Complete)
        self.assertEqual(
            Second.CompletedDescriptorFingerprints,
            frozenset(Descriptors),
        )
        self.assertFalse(Second.RemainingDescriptorFingerprints)
        self.assertEqual(
            tuple(Value.CandidateId for Value in Second.Candidates),
            ("candidate-a", "candidate-b"),
        )
        self.assertEqual(len(Second.CandidateMetadata), 2)
        Summary = Second.ToProgressDictionary()
        self.assertEqual(Summary["DescriptorCount"], 700)
        self.assertEqual(Summary["CompletedDescriptorCount"], 700)
        self.assertEqual(Summary["RemainingDescriptorCount"], 0)
        self.assertEqual(Summary["SemanticCandidateCount"], 2)
        self.assertTrue(Summary["CandidateMetadataClosed"])
        self.assertTrue(Summary["ProgressFingerprint"])
        self.assertFalse(Summary["RawResultCacheAuthoritative"])
        with self.assertRaises(ValueError):
            RetainPhysicalSignalRouteDomainDescriptorProgress(
                ProgressCache,
                PreSiblingDomainFingerprint="stable-domain",
                Signal="Alpha",
                RequestDomainFingerprint="request-domain",
                RequestDescriptorFingerprints=Descriptors,
                CompletedDescriptorFingerprints=(),
                Candidates=(SimpleNamespace(
                    CandidateId="candidate-a",
                    Payload="changed",
                ),),
                CandidateMetadata={
                    "candidate-a": ("X", 1, 0, 3),
                },
            )

    def testExactPortalCertificateIdentityReportsEachStrictMismatch(
        self,
    ) -> None:
        Fabric = SimpleNamespace(
            Complete=True,
            ResourceGraphFingerprint="region-resource",
            RegionFingerprint="region",
        )
        Plan = SimpleNamespace(
            PlanFingerprint="plan",
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="region-resource",
            TechnologyFingerprint="technology",
            InterfaceFingerprint="interface",
            ExteriorRegionFingerprint="region",
            ExteriorFabricSetFingerprint="fabric-set",
            ExteriorFabrics=(Fabric,),
        )
        Problem = SimpleNamespace(
            PhysicalAssemblyPlan=Plan,
            PlacementFingerprint="placement",
            Interface=SimpleNamespace(
                InterfaceFingerprint="interface",
                PhysicalAssemblyPlanFingerprint="plan",
            ),
        )
        Preparation = SimpleNamespace(
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="region-resource",
            ExteriorRegionFingerprint="region",
            ExteriorFabricSetFingerprint="fabric-set",
            ExteriorFabrics=(Fabric,),
        )

        Baseline = BuildExactPhysicalPortalCertificateIdentityConditions(
            Plan,
            Problem,
            Preparation,
            "region-resource",
            "region",
            "technology",
        )
        self.assertTrue(all(Baseline.values()), Baseline)

        Cases = (
            (
                "ProblemPlanIdentityMatch",
                Plan,
                SimpleNamespace(
                    PhysicalAssemblyPlan=SimpleNamespace(
                        PlanFingerprint="other-plan"
                    ),
                    PlacementFingerprint="placement",
                    Interface=Problem.Interface,
                ),
                Preparation,
                "region-resource",
                "region",
                "technology",
            ),
            (
                "PlacementIdentityMatch",
                Plan,
                Problem,
                SimpleNamespace(
                    **{
                        **vars(Preparation),
                        "PlacementFingerprint": "other-placement",
                    }
                ),
                "region-resource",
                "region",
                "technology",
            ),
            (
                "ResourceGraphCurrentIdentityMatch",
                Plan,
                Problem,
                Preparation,
                "other-resource",
                "region",
                "technology",
            ),
            (
                "ExteriorFabricPreparationIdentityMatch",
                Plan,
                Problem,
                SimpleNamespace(
                    **{
                        **vars(Preparation),
                        "ExteriorFabricSetFingerprint": (
                            "other-fabric-set"
                        ),
                    }
                ),
                "region-resource",
                "region",
                "technology",
            ),
            (
                "ResourceGraphPreparationIdentityMatch",
                Plan,
                Problem,
                SimpleNamespace(
                    **{
                        **vars(Preparation),
                        "ResourceGraphFingerprint": "other-resource",
                    }
                ),
                "region-resource",
                "region",
                "technology",
            ),
            (
                "ExteriorRegionCurrentIdentityMatch",
                Plan,
                Problem,
                Preparation,
                "region-resource",
                "other-region",
                "technology",
            ),
            (
                "TechnologyIdentityMatch",
                Plan,
                Problem,
                Preparation,
                "region-resource",
                "region",
                "other-technology",
            ),
            (
                "InterfaceIdentityMatch",
                Plan,
                SimpleNamespace(
                    PhysicalAssemblyPlan=Plan,
                    PlacementFingerprint="placement",
                    Interface=SimpleNamespace(
                        InterfaceFingerprint="other-interface",
                        PhysicalAssemblyPlanFingerprint="plan",
                    ),
                ),
                Preparation,
                "region-resource",
                "region",
                "technology",
            ),
        )
        for ExpectedMismatch, *Arguments in Cases:
            with self.subTest(ExpectedMismatch=ExpectedMismatch):
                Conditions = (
                    BuildExactPhysicalPortalCertificateIdentityConditions(
                        *Arguments
                    )
                )
                self.assertFalse(Conditions[ExpectedMismatch])
                self.assertFalse(all(Conditions.values()))

    def testExteriorResourceFingerprintUsesAuthoritativeRegionIdentity(
        self,
    ) -> None:
        Graph = SimpleNamespace(
            GraphVersion="resource-graph-v1",
            # These whole-graph attributes intentionally disagree with the
            # authoritative region and must not affect this identity.
            Nodes=tuple(range(99)),
            Edges=tuple(range(101)),
        )
        Region = SimpleNamespace(
            Bounds=(0, 20, 1, 8, -4, 16),
            Nodes=((0, 2, 0), (1, 2, 0)),
            Edges=(((0, 2, 0), (1, 2, 0)),),
        )
        Baseline = BuildPhysicalExteriorResourceGraphFingerprint(
            Graph,
            "authoritative-region-a",
            Region,
        )
        SameRegionDifferentWholeGraph = (
            BuildPhysicalExteriorResourceGraphFingerprint(
                SimpleNamespace(
                    **{
                        **vars(Graph),
                        "Nodes": (),
                        "Edges": (),
                    }
                ),
                "authoritative-region-a",
                Region,
            )
        )

        self.assertEqual(Baseline, SameRegionDifferentWholeGraph)
        for ChangedFingerprint, ChangedRegion in (
            ("authoritative-region-b", Region),
            (
                "authoritative-region-a",
                SimpleNamespace(
                    **{
                        **vars(Region),
                        "Bounds": (0, 21, 1, 8, -4, 16),
                    }
                ),
            ),
            (
                "authoritative-region-a",
                SimpleNamespace(
                    **{
                        **vars(Region),
                        "Nodes": (*Region.Nodes, (2, 2, 0)),
                    }
                ),
            ),
            (
                "authoritative-region-a",
                SimpleNamespace(
                    **{
                        **vars(Region),
                        "Edges": (
                            *Region.Edges,
                            ((1, 2, 0), (2, 2, 0)),
                        ),
                    }
                ),
            ),
        ):
            self.assertNotEqual(
                Baseline,
                BuildPhysicalExteriorResourceGraphFingerprint(
                    Graph,
                    ChangedFingerprint,
                    ChangedRegion,
                ),
            )

    def testFrozenPostClosurePortalHandoffRejectsPlanIdentityMismatch(
        self,
    ) -> None:
        Region = SimpleNamespace(Bounds=(0, 1, 0, 1, 0, 1), Nodes=(), Edges=())
        Graph = SimpleNamespace(GraphVersion="resource-graph-v1")
        RegionFingerprint = "exterior-region"
        ResourceFingerprint = BuildPhysicalExteriorResourceGraphFingerprint(
            Graph,
            RegionFingerprint,
            Region,
        )
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset()),
            ResourceGraph=Graph,
        )
        Cache = RawPortalGeometryCache(
            PlacementGeometryFingerprint="placement-geometry",
            ResourceGeometryFingerprint="resource-geometry",
            PlacedReference=object(),
            ResourcesReference=Resources,
            Region=Region,
            LayerCount=1,
            PortalLimit=1,
            PortalVariantCounts=(),
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            Context=object(),
            AssignmentIndexed=IndexedRoutingResourceGraph(
                ResourcePositions=(),
                PositionIndices={},
            ),
            PortalEntries=(),
            RequestCount=0,
            TargetCount=0,
            StarvationCount=0,
            ExteriorRegionFingerprint=RegionFingerprint,
            AuthoritativeResourceGraphFingerprint=ResourceFingerprint,
        )
        Preparation = SimpleNamespace(
            DomainFingerprint="prepared-domain",
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component-graph",
            ResourceGraphFingerprint=ResourceFingerprint,
            ExteriorRegionFingerprint=RegionFingerprint,
            Complete=True,
        )
        Resources.FrozenPhysicalComponentPostClosurePortalHandoff = (
            FrozenPhysicalComponentPostClosurePortalHandoff(
                PreparationDomainFingerprint="prepared-domain",
                PlacementFingerprint="placement",
                ComponentGraphFingerprint="component-graph",
                ResourceGraphFingerprint=ResourceFingerprint,
                ExteriorRegionFingerprint=RegionFingerprint,
                RawPortalGeometryCache=Cache,
            )
        )
        MismatchedPlan = SimpleNamespace(
            PlanFingerprint="assembly-plan",
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component-graph",
            ResourceGraphFingerprint="different-resource",
            ExteriorRegionFingerprint=RegionFingerprint,
        )

        with self.assertRaises(RoutingStageError) as Captured:
            ValidateFrozenPhysicalComponentPostClosurePortalHandoff(
                Resources,
                Preparation,
                MismatchedPlan,
            )

        self.assertEqual(
            Captured.exception.Failure.Reason,
            RoutingFailureReason.ComponentAssemblyIdentityMismatch,
        )
        self.assertIn(
            "PlanResourceGraphFingerprint",
            Captured.exception.Failure.Diagnostics["IdentityMismatches"],
        )

    def testLargeBoundaryPairRelationResumesCompleteCertifiedPrefix(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Resources.ResourceGraph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        BaseBySignal = {
            Signal: next(
                Value for Value in Resources
                .PhysicalBoundaryMandatoryPortalFactorDomainCache.values()
                if Value.Signal == Signal
            )
            for Signal in ("Alpha", "Beta")
        }
        ExpandedDomains = []
        for Signal in ("Alpha", "Beta"):
            Base = BaseBySignal[Signal]
            BasePortal = Base.PortalDomains[0][0]
            for Index in range(17):
                Suffix = str(Index)
                Aperture = f"aperture:{Signal}:{Suffix}"
                PortalValue = replace(
                    BasePortal,
                    PortalId=f"{Signal}:{Suffix}",
                )
                ExpandedDomains.append(replace(
                    Base,
                    DomainFingerprint=f"domain:{Signal}:{Suffix}",
                    ApertureOptionFingerprint=f"option:{Signal}:{Suffix}",
                    ApertureContractFingerprint=Aperture,
                    GlobalContractFingerprint=f"global:{Signal}:{Suffix}",
                    PortalDomains=((PortalValue,),),
                ))
        Preparation.BoundaryPortReservationsBySignal = tuple(
            (
                Signal,
                tuple(
                    SimpleNamespace(
                        ApertureContractFingerprint=(
                            f"aperture:{Signal}:{Index}"
                        )
                    )
                    for Index in range(17)
                ),
            )
            for Signal in ("Alpha", "Beta")
        )
        Resources.PhysicalBoundaryMandatoryPortalFactorDomainCache = {
            (
                Value.PreparedDomainFingerprint,
                Value.Signal,
                Value.ApertureContractFingerprint,
            ): Value
            for Value in ExpandedDomains
        }
        Partial = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
            MaximumNewCertificates=37,
            PreferredApertureContractsBySignal={
                "Alpha": "aperture:Alpha:16",
                "Beta": "aperture:Beta:15",
            },
        )

        self.assertFalse(Partial.Complete)
        self.assertGreater(len(Partial.Certificates), 0)
        self.assertLess(
            len(Partial.Certificates),
            Partial.ExpectedOptionPairCount,
        )
        # The targeted direct compiler completes both current-option rows
        # (17 + 17 - their shared current pair) before constructing the full
        # quotient on a later call.
        self.assertEqual(len(Partial.Certificates), 33)
        self.assertEqual(
            (
                Partial.Certificates[0]
                .FirstApertureContractFingerprint,
                Partial.Certificates[0]
                .SecondApertureContractFingerprint,
            ),
            ("aperture:Alpha:16", "aperture:Beta:15"),
        )
        self.assertIs(
            Resources.PhysicalBoundaryMandatoryPortalPairRelationCache[
                Partial.RelationFingerprint
            ],
            Partial,
        )

        Complete = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Complete.Complete)
        self.assertEqual(
            len(Complete.Certificates),
            Complete.ExpectedOptionPairCount,
        )
        self.assertEqual(
            Complete.Certificates[:len(Partial.Certificates)],
            Partial.Certificates,
        )

    def testBoundaryMandatoryPortalPairRelationValidatesCompleteCache(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        Cached = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Beta", "Alpha"),
            Resources,
            ShouldStop=lambda: True,
        )

        self.assertIs(Cached, Relation)

    def testBoundaryMandatoryPortalPairRelationRejectsCorruptCompleteCache(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )
        FirstCertificate = Relation.Certificates[0]
        Corruptions = (
            replace(
                Relation,
                PreparedDomainFingerprint="different-prepared-domain",
            ),
            replace(
                Relation,
                OptionDomainFingerprintsBySignal=(("Alpha", ()),),
            ),
            replace(
                Relation,
                Certificates=(
                    replace(
                        FirstCertificate,
                        Certificate=replace(
                            FirstCertificate.Certificate,
                            DomainFingerprint="different-pair-domain",
                        ),
                    ),
                    *Relation.Certificates[1:],
                ),
            ),
            replace(Relation, UnsatisfiableApertureClauses=()),
        )

        for Corrupt in Corruptions:
            with self.subTest(Corruption=Corrupt):
                Resources.PhysicalBoundaryMandatoryPortalPairRelationCache[
                    Relation.RelationFingerprint
                ] = Corrupt
                with self.assertRaisesRegex(
                    ValueError,
                    "cached mandatory portal pair relation identity mismatch",
                ):
                    CompilePhysicalBoundaryMandatoryPortalPairRelation(
                        Preparation,
                        ("Alpha", "Beta"),
                        Resources,
                    )

    def testBoundaryMandatoryPortalPairRelationRejectsCorruptFactorStateCache(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )
        FactorFingerprint, FactorCertificate = next(
            (Fingerprint, Certificate)
            for Fingerprint, Certificate in Resources
            .PhysicalBoundaryMandatoryPortalFactorCertificateCache.items()
            if not Fingerprint.startswith("generic:")
        )
        Resources.PhysicalBoundaryMandatoryPortalFactorCertificateCache[
            FactorFingerprint
        ] = replace(FactorCertificate, States=())
        Resources.PhysicalBoundaryMandatoryPortalPairRelationCache.pop(
            Relation.RelationFingerprint
        )

        with self.assertRaisesRegex(
            ValueError,
            "cached mandatory portal pair state index identity mismatch",
        ):
            CompilePhysicalBoundaryMandatoryPortalPairRelation(
                Preparation,
                ("Alpha", "Beta"),
                Resources,
            )

    def testBoundaryMandatoryPortalPairRelationRejectsIdentityMismatch(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Key = next(
            Key for Key in Resources
            .PhysicalBoundaryMandatoryPortalFactorDomainCache
            if Key[1] == "Beta"
        )
        Resources.PhysicalBoundaryMandatoryPortalFactorDomainCache[Key] = (
            replace(
                Resources
                .PhysicalBoundaryMandatoryPortalFactorDomainCache[Key],
                ExteriorRegionFingerprint="different-region",
            )
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertFalse(Relation.Complete)
        self.assertEqual(Relation.UnsatisfiableApertureClauses, ())

    def testBoundaryMandatoryPortalPairRelationRejectsCachedOptionSubset(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Key = next(
            Key for Key in Resources
            .PhysicalBoundaryMandatoryPortalFactorDomainCache
            if Key[1] == "Beta" and Key[2].endswith(":1")
        )
        del Resources.PhysicalBoundaryMandatoryPortalFactorDomainCache[Key]
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertEqual(Relation.ExpectedOptionPairCount, 4)
        self.assertFalse(Relation.Complete)
        self.assertEqual(Relation.UnsatisfiableApertureClauses, ())

    def testBoundaryPortalExactGlobalPathChangesRelationAndFingerprint(
        self,
    ) -> None:
        ConflictPreparation, ConflictResources, ConflictDomains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((0, 1, 0), (2, 1, 0)),
            )
        )
        ClearPreparation, ClearResources, ClearDomains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((0, 1, 0), (0, 1, 2)),
            )
        )
        Conflict = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            ConflictPreparation,
            ("Alpha", "Beta"),
            ConflictResources,
        )
        Clear = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            ClearPreparation,
            ("Alpha", "Beta"),
            ClearResources,
        )

        self.assertNotEqual(
            next(Value for Value in ConflictDomains if Value.Signal == "Alpha")
            .DomainFingerprint,
            next(Value for Value in ClearDomains if Value.Signal == "Alpha")
            .DomainFingerprint,
        )
        self.assertFalse(Conflict.Certificates[0].Certificate.Feasible)
        self.assertTrue(Clear.Certificates[0].Certificate.Feasible)

    def testClusterInterfaceAccessDomainFingerprintIsPhysicalAndAnonymous(
        self,
    ) -> None:
        def BuildDomains(
            FirstSignal,
            SecondSignal,
            Delta=(0, 0, 0),
            ChangeSecond=False,
        ):
            def Translate(Position):
                return tuple(
                    Position[Index] + Delta[Index]
                    for Index in range(3)
                )

            def Value(Signal, Terminal, Path, Layer):
                TranslatedTerminal = Translate(Terminal)
                TranslatedPath = tuple(map(Translate, Path))
                Claims = RoutingResourceClaims(
                    WireCells=frozenset(TranslatedPath),
                    SupportCells=frozenset(
                        (X, Y - 1, Z)
                        for X, Y, Z in TranslatedPath
                    ),
                    RequiredAirCells=frozenset(
                        (X, Y + 1, Z)
                        for X, Y, Z in TranslatedPath
                    ),
                    ElectricalCells=frozenset(TranslatedPath),
                )
                Portal = PinAccessPortal(
                    PortalId=f"{Signal}:{TranslatedPath}",
                    Signal=Signal,
                    Terminal=TranslatedTerminal,
                    Layer=Layer,
                    Path=TranslatedPath,
                    Edges=frozenset(),
                    Claims=Claims,
                    Length=len(TranslatedPath),
                    BendCount=0,
                    ViaCount=0,
                    Cost=7,
                )
                return 7, Layer, Portal, Claims

            FirstTerminal = (10, 1, 0)
            SecondTerminal = (20, 1, 0)
            SecondPath = (
                (19, 1, 0),
                (18, 3 if ChangeSecond else 1, 0),
            )
            # Deliberately reverse insertion order after rename/translation.
            return {
                (SecondSignal, Translate(SecondTerminal)): (
                    Value(
                        SecondSignal,
                        SecondTerminal,
                        SecondPath,
                        1,
                    ),
                ),
                (FirstSignal, Translate(FirstTerminal)): (
                    Value(
                        FirstSignal,
                        FirstTerminal,
                        ((11, 1, 0), (12, 1, 0)),
                        0,
                    ),
                    Value(
                        FirstSignal,
                        FirstTerminal,
                        ((11, 1, 1), (12, 1, 1)),
                        1,
                    ),
                ),
            }

        Original = BuildDomains("Alpha", "Beta")
        RenamedTranslated = BuildDomains(
            "Net91",
            "Net17",
            Delta=(37, 4, -22),
        )
        Changed = BuildDomains(
            "Alpha",
            "Beta",
            ChangeSecond=True,
        )

        self.assertEqual(
            BuildClusterInterfaceAccessDomainFingerprint(Original),
            BuildClusterInterfaceAccessDomainFingerprint(
                RenamedTranslated
            ),
        )
        self.assertNotEqual(
            BuildClusterInterfaceAccessDomainFingerprint(Original),
            BuildClusterInterfaceAccessDomainFingerprint(Changed),
        )
        self.assertEqual(
            BuildClusterInterfaceAccessDomainFingerprint(
                Original,
                frozenset({"Alpha"}),
            ),
            BuildClusterInterfaceAccessDomainFingerprint(
                Changed,
                frozenset({"Alpha"}),
            ),
        )
        OriginalProblem = BuildClusterInterfaceProblem(
            Original,
            PlacementVariantFingerprint="placement",
            OwnershipFingerprint="ownership",
        )
        RenamedProblem = BuildClusterInterfaceProblem(
            RenamedTranslated,
            PlacementVariantFingerprint="placement",
            OwnershipFingerprint="ownership",
        )
        RenamedProblemWithoutTranslation = BuildClusterInterfaceProblem(
            BuildDomains("Net91", "Net17"),
            PlacementVariantFingerprint="placement",
            OwnershipFingerprint="ownership",
        )
        self.assertEqual(
            OriginalProblem.ComponentFingerprint,
            RenamedProblem.ComponentFingerprint,
        )
        self.assertEqual(
            OriginalProblem.TerminalDomainSizes,
            (1, 2),
        )
        self.assertEqual(OriginalProblem.MaximumClusterVariants, 6)
        self.assertEqual(OriginalProblem.MaximumRepairClusters, 3)
        self.assertEqual(
            OriginalProblem.TerminalDomains,
            RenamedProblemWithoutTranslation.TerminalDomains,
        )
        self.assertEqual(
            tuple(
                Component.ComponentFingerprint
                for Component in OriginalProblem.ConflictComponents
            ),
            tuple(
                Component.ComponentFingerprint
                for Component
                in RenamedProblemWithoutTranslation.ConflictComponents
            ),
        )
        self.assertEqual(len(OriginalProblem.TerminalDomains), 2)
        self.assertEqual(len(OriginalProblem.ConflictComponents), 2)

    def testMultiPairConflictRetainsExactBinaryPortProofs(self) -> None:
        self.assertTrue(
            ConflictClassificationSupportsPhysicalPortPairNoGoods(
                "pairwise-incompatibility"
            )
        )
        self.assertTrue(
            ConflictClassificationSupportsPhysicalPortPairNoGoods(
                "multi-pair-placement-conflict"
            )
        )
        self.assertFalse(
            ConflictClassificationSupportsPhysicalPortPairNoGoods(
                "higher-order-placement-conflict"
            )
        )

    def testPhysicalSignalLocalFactorIdentityExcludesExteriorAperture(
        self,
    ) -> None:
        """The reuse key changes for local inputs, never a guide-only move."""
        Claims = RoutingResourceClaims(
            WireCells=frozenset(((0, 1, 0), (1, 1, 0))),
            ElectricalCells=frozenset(((0, 1, 0), (1, 1, 0))),
        )
        Factor = SimpleNamespace(
            LocalAccessFingerprint="local-access",
            FabricDomainFingerprint="fabric-domain",
            FabricAttachment=(0, 1, 0),
            LocalPath=((0, 1, 0), (1, 1, 0)),
            OwnedCandidateFingerprints=frozenset(("candidate",)),
            LocalClaims=Claims,
            SeamContractFingerprint="local-seam",
        )
        Certificate = SimpleNamespace(
            Complete=True,
            PortDomains=(SimpleNamespace(
                Signal="sum",
                Direction="output",
                Candidates=(),
            ),),
        )
        Problem = SimpleNamespace(
            Fabric=SimpleNamespace(FabricFingerprint="fabric-topology"),
            OwnedTerminalDomains=(SimpleNamespace(
                Signal="sum",
                TerminalFingerprint="terminal",
                TerminalRole="source",
                Terminal=(0, 1, 0),
                Candidates=(SimpleNamespace(CandidateFingerprint="candidate"),),
            ),),
        )
        ResourceGraph = SimpleNamespace(
            GraphVersion="graph-v1",
            Nodes=((0, 1, 0),),
            Edges=(),
            Technology="technology-v1",
        )
        First = PreparePhysicalSignalLocalFactorDomain(
            Problem, Certificate, "sum", ResourceGraph,
            LocalAccessFactors=(Factor,),
        )
        Second = PreparePhysicalSignalLocalFactorDomain(
            Problem, Certificate, "sum", ResourceGraph,
            LocalAccessFactors=(Factor,),
        )
        ChangedFactor = SimpleNamespace(
            **{
                **vars(Factor),
                "OwnedCandidateFingerprints": frozenset(("changed",)),
            }
        )
        Changed = PreparePhysicalSignalLocalFactorDomain(
            Problem, Certificate, "sum", ResourceGraph,
            LocalAccessFactors=(ChangedFactor,),
        )

        self.assertEqual(First, Second)
        self.assertEqual(First.LocalAccessFactors, (Factor,))
        self.assertNotEqual(
            First.LocalIdentityFingerprint,
            Changed.LocalIdentityFingerprint,
        )

    @patch(
        "PhysicalDesign.Routing.Regions.Proofs.Certification."
        "BuildPhysicalLocalPairProofContextFingerprint",
        return_value="local-proof-context",
    )
    def testLocalPairSupportIndexRequiresCompleteMatchingIdentity(
        self,
        _ProofContext,
    ) -> None:
        Preparation = SimpleNamespace(
            DomainFingerprint="prepared",
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="resource",
            Problem=SimpleNamespace(
                Fabric=SimpleNamespace(FabricFingerprint="fabric"),
            ),
            AccessCertificate=SimpleNamespace(
                TechnologyFingerprint="technology",
            ),
        )
        CompletePreparation = SimpleNamespace(
            **{
                **vars(Preparation),
                "Complete": True,
                "Feasible": True,
                "LocalAccessFactorsBySignal": (
                    ("CarryA", (
                        SimpleNamespace(
                            LocalContractFingerprint="local-a"
                        ),
                    )),
                    ("CarryB", (
                        SimpleNamespace(
                            LocalContractFingerprint="local-b0"
                        ),
                        SimpleNamespace(
                            LocalContractFingerprint="local-b1"
                        ),
                    )),
                ),
            },
        )
        Certificate = BuildPhysicalLocalPortPairSupportCertificate(
            CompletePreparation,
            "solver",
            "CarryA",
            "local-a",
            "CarryB",
            ("local-b0", "local-b1"),
            "local-proof-context",
            _LocalPairProofRecords(
                "CarryB",
                ("local-b0", "local-b1"),
                "CarryA",
                "local-a",
            ),
        )
        Expected = frozenset((frozenset((
            ("CarryA", "local-a"),
            ("CarryB", "local-signal-domain:solver"),
        )),))

        self.assertEqual(
            BuildPhysicalLocalPortPairUnsupportedIndex(
                (Certificate,), CompletePreparation, "solver"
            ),
            Expected,
        )
        for Invalid in (
            replace(Certificate, Complete=False),
            replace(Certificate, PortSolverCacheKey="other-solver"),
            replace(Certificate, FabricFingerprint="other-fabric"),
            replace(Certificate, ResourceGraphFingerprint="other-resource"),
            replace(Certificate, TechnologyFingerprint="other-technology"),
            replace(Certificate, ComponentGraphFingerprint="other-component"),
            replace(Certificate, PreparedDomainFingerprint="other-prepared"),
            replace(Certificate, CertificateFingerprint="other-certificate"),
            replace(Certificate, PairProofRecords=()),
            replace(
                Certificate,
                PairProofRecords=Certificate.PairProofRecords[:1],
            ),
        ):
            self.assertFalse(
                BuildPhysicalLocalPortPairUnsupportedIndex(
                    (Invalid,), CompletePreparation, "solver"
                )
            )
        SubsetCertificate = BuildPhysicalLocalPortPairSupportCertificate(
            CompletePreparation,
            "solver",
            "CarryA",
            "local-a",
            "CarryB",
            ("local-b0",),
            "local-proof-context",
            _LocalPairProofRecords(
                "CarryB", ("local-b0",), "CarryA", "local-a"
            ),
        )
        ForeignRowCertificate = BuildPhysicalLocalPortPairSupportCertificate(
            CompletePreparation,
            "solver",
            "CarryA",
            "local-foreign",
            "CarryB",
            ("local-b0", "local-b1"),
            "local-proof-context",
            _LocalPairProofRecords(
                "CarryB",
                ("local-b0", "local-b1"),
                "CarryA",
                "local-foreign",
            ),
        )
        self.assertFalse(BuildPhysicalLocalPortPairUnsupportedIndex(
            (SubsetCertificate,), CompletePreparation, "solver"
        ))
        self.assertFalse(BuildPhysicalLocalPortPairUnsupportedIndex(
            (ForeignRowCertificate,), CompletePreparation, "solver"
        ))

    def testPhysicalPortCorridorIdentityIsTranslationNormalized(self) -> None:
        def Port(Offset, Fingerprint):
            return SimpleNamespace(
                Signal="PortA",
                Direction="output",
                Capacity=1,
                Attachment=(Offset, 2, 0),
                GlobalPath=((Offset, 2, 0),),
                ReservationFingerprint=Fingerprint,
            )

        def Candidate(Offset, CandidateId):
            Nodes = frozenset(((Offset, 2, 0), (Offset + 1, 2, 0)))
            return NetRouteCandidate(
                CandidateId=CandidateId,
                Signal="PortA",
                SourcePortalId="source",
                TargetPortalIds={},
                Nodes=Nodes,
                Edges=frozenset((((Offset, 2, 0), (Offset + 1, 2, 0)),)),
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

        First = BuildPhysicalPortCorridorFactor(
            Port(0, "reservation-a"),
            Candidate(0, "candidate-a"),
            "request-domain",
        )
        Translated = BuildPhysicalPortCorridorFactor(
            Port(10, "reservation-b"),
            Candidate(10, "candidate-b"),
            "request-domain",
        )

        self.assertEqual(
            First.NormalizedIdentityFingerprint,
            Translated.NormalizedIdentityFingerprint,
        )
        self.assertNotEqual(
            First.RouteCandidateFingerprint,
            Translated.RouteCandidateFingerprint,
        )
        Domain = BuildPhysicalPortCorridorDomain(
            Port(0, "reservation-a"),
            (Candidate(0, "candidate-b"), Candidate(0, "candidate-a")),
            "request-domain",
            "resource-graph",
            "technology",
            Complete=True,
        )
        self.assertEqual(len(Domain.Factors), 1)
        self.assertEqual(Domain.Factors[0].RouteCandidateId, "candidate-a")
        self.assertTrue(Domain.Complete)
        SameGlobalContract = BuildPhysicalPortCorridorDomain(
            Port(0, "reservation-local-variant"),
            (Candidate(0, "candidate-a"),),
            "request-domain",
            "resource-graph",
            "technology",
            Complete=True,
        )
        self.assertEqual(
            Domain.DomainFingerprint,
            SameGlobalContract.DomainFingerprint,
        )
        self.assertNotEqual(
            Domain.PortReservationFingerprint,
            SameGlobalContract.PortReservationFingerprint,
        )

    def testRetainedPhysicalGlobalPlanFrontierSchedulesFairly(self) -> None:
        def Assembly(Name):
            return SimpleNamespace(Plan=SimpleNamespace(
                PlanFingerprint=f"plan-{Name}",
                PortAssignmentFingerprint=f"ports-{Name}",
                Ports=(),
            ))

        Frontier = {}
        for Sequence, Name in enumerate(("a", "b", "c")):
            CurrentAssembly = Assembly(Name)
            Continuation = BuildPhysicalGlobalPlanContinuationState(
                CurrentAssembly.Plan,
                {"Signal": f"request-{Name}"},
                {"Signal": 3 - Sequence},
                (),
                (f"aperture-{Name}",),
                CompletedWork=Sequence + 1,
                ResumeCursor=PhysicalGlobalPlanResumeCursor(
                    CursorFingerprint=f"cursor-{Name}",
                    PlanFingerprint=f"plan-{Name}",
                    ApertureDomainFingerprint=f"aperture-{Name}",
                    CompletedWork=Sequence + 1,
                    State={"owner": Name},
                ),
            )
            Frontier = RetainIncompletePhysicalGlobalPlan(
                Frontier,
                CurrentAssembly,
                Continuation,
                EnqueuedSequence=Sequence,
            )

        ScheduledNames = []
        for Sequence in range(4):
            Entry, Frontier = SelectNextRetainedPhysicalGlobalPlan(
                Frontier,
                ScheduleSequence=Sequence,
            )
            ScheduledNames.append(Entry.PlanFingerprint)

        self.assertEqual(
            ScheduledNames,
            ["plan-a", "plan-b", "plan-c", "plan-a"],
        )
        self.assertFalse(any(
            Entry.Continuation.Complete for Entry in Frontier.values()
        ))

    def testRetainedPhysicalGlobalPlansFinishBeforeFreshExploration(
        self,
    ) -> None:
        Frontier = {
            "plan-a": SimpleNamespace(),
            "plan-b": SimpleNamespace(),
        }

        self.assertTrue(ShouldScheduleRetainedPhysicalGlobalPlan(
            Frontier,
            PreviousPlanWasRetained=False,
        ))
        self.assertTrue(ShouldScheduleRetainedPhysicalGlobalPlan(
            Frontier,
            PreviousPlanWasRetained=True,
        ))
        self.assertFalse(ShouldScheduleRetainedPhysicalGlobalPlan(
            {},
            PreviousPlanWasRetained=False,
        ))

    def testRetainedPhysicalGlobalPlanRefreshPreservesFairnessState(self) -> None:
        Assembly = SimpleNamespace(Plan=SimpleNamespace(
            PlanFingerprint="plan-a",
            PortAssignmentFingerprint="ports-a",
            Ports=(),
        ))
        First = BuildPhysicalGlobalPlanContinuationState(
            Assembly.Plan,
            {"Signal": "request-domain"},
            {"Signal": 4},
            (),
            ("aperture",),
            CompletedWork=3,
            ResumeCursor=PhysicalGlobalPlanResumeCursor(
                "cursor-3", "plan-a", "aperture", 3, object(),
            ),
        )
        Frontier = RetainIncompletePhysicalGlobalPlan(
            {}, Assembly, First, EnqueuedSequence=2,
        )
        _Entry, Frontier = SelectNextRetainedPhysicalGlobalPlan(
            Frontier,
            ScheduleSequence=5,
        )
        Continued = BuildPhysicalGlobalPlanContinuationState(
            Assembly.Plan,
            {"Signal": "request-domain"},
            {"Signal": 2},
            (),
            ("aperture",),
            CompletedWork=7,
            ResumeCursor=PhysicalGlobalPlanResumeCursor(
                "cursor-10", "plan-a", "aperture", 10, object(),
            ),
        )
        Frontier = RetainIncompletePhysicalGlobalPlan(
            Frontier, Assembly, Continued, EnqueuedSequence=9,
        )
        Entry = Frontier["plan-a"]

        self.assertEqual(Entry.EnqueuedSequence, 2)
        self.assertEqual(Entry.LastScheduledSequence, 5)
        self.assertEqual(Entry.ScheduleCount, 1)
        self.assertEqual(Entry.AccumulatedCompletedWork, 10)
        self.assertEqual(Entry.Continuation.RemainingRequestCounts, (
            ("Signal", 2),
        ))
        Stale = BuildPhysicalGlobalPlanContinuationState(
            Assembly.Plan,
            {"Signal": "request-domain"},
            {"Signal": 4},
            (),
            ("aperture",),
            CompletedWork=1,
            ResumeCursor=PhysicalGlobalPlanResumeCursor(
                "cursor-9", "plan-a", "aperture", 9, object(),
            ),
        )
        Preserved = RetainIncompletePhysicalGlobalPlan(
            Frontier, Assembly, Stale, EnqueuedSequence=10,
        )["plan-a"]
        self.assertEqual(
            Preserved.Continuation.StateFingerprint,
            Entry.Continuation.StateFingerprint,
        )
        self.assertEqual(Preserved.AccumulatedCompletedWork, 10)
        OverlappingPublication = BuildPhysicalGlobalPlanContinuationState(
            Assembly.Plan,
            {"Signal": "request-domain"},
            {"Signal": 1},
            (),
            ("aperture",),
            CompletedWork=9,
            ResumeCursor=PhysicalGlobalPlanResumeCursor(
                "cursor-12", "plan-a", "aperture", 12, object(),
            ),
        )
        Advanced = RetainIncompletePhysicalGlobalPlan(
            Frontier,
            Assembly,
            OverlappingPublication,
            EnqueuedSequence=11,
        )["plan-a"]
        self.assertEqual(Advanced.AccumulatedCompletedWork, 12)
        with self.assertRaises(ValueError):
            RetainIncompletePhysicalGlobalPlan(
                Frontier,
                Assembly,
                replace(Continued, Complete=True),
                EnqueuedSequence=10,
            )

    def testExactRealizabilityNogoodAndAssignmentIdentityAreStructural(
        self,
    ) -> None:
        Signal = "Original"
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
                self.BuildPortal(Signal, Root, (2, 1, 0)),
            ),
        }
        _FirstPortals, FirstReservations = (
            ReserveClusterBoundaryLeases(
                Portals,
                {Signal: Profile},
                Resources,
            )
        )
        FirstPattern = BuildClusterLeaseSignalPatternFingerprint(
            FirstReservations,
            Signal,
        )
        FirstAssignment = (
            BuildClusterInterfaceReservationAssignmentFingerprint(
                FirstReservations
            )
        )
        self.assertEqual(
            FirstAssignment,
            BuildClusterInterfaceReservationAssignmentFingerprint(
                tuple(
                    replace(
                        Reservation,
                        PortalId="renamed-portal",
                        SlotIndex=99,
                    )
                    for Reservation in reversed(FirstReservations)
                )
            ),
        )
        Nogood = ClusterInterfaceRealizabilityNogood(
            PlacementStateFingerprint="state",
            ComponentFingerprint="component",
            Signal=Signal,
            TerminalPatternFingerprint=FirstPattern,
            CandidateDomainFingerprint="domain",
            RouteFailureFingerprint="failure",
            RejectedAssignmentFingerprint="assignment",
        )
        _SecondPortals, SecondReservations = (
            ReserveClusterBoundaryLeases(
                Portals,
                {Signal: Profile},
                Resources,
                CandidateRealizabilityNogoods=(Nogood,),
            )
        )
        self.assertNotEqual(
            BuildClusterLeaseSignalPatternFingerprint(
                SecondReservations,
                Signal,
            ),
            FirstPattern,
        )
        RenamedNogood = replace(Nogood, Signal="Renamed")
        self.assertEqual(
            Nogood.StructuralIdentity(),
            RenamedNogood.StructuralIdentity(),
        )
        self.assertEqual(
            ClusterInterfaceStateProof(
                PlacementStateFingerprint="state",
                Status="realizability-unsatisfiable",
                RealizabilityNogoods=(Nogood,),
                Exhaustive=True,
            ).StructuralIdentity(),
            ClusterInterfaceStateProof(
                PlacementStateFingerprint="state",
                Status="realizability-unsatisfiable",
                RealizabilityNogoods=(RenamedNogood,),
                Exhaustive=True,
            ).StructuralIdentity(),
        )

    def testPhysicalGlobalRouteTreeCacheReusesExactRequestAcrossPlans(
        self,
    ) -> None:
        FirstPlan = SimpleNamespace(PlanFingerprint="assembly-a")
        SecondPlan = SimpleNamespace(PlanFingerprint="assembly-b")
        Request = (
            "PortA",
            ((1, 2, 3),),
            frozenset({(4, 2, 3)}),
            ((1, 0), (2, 0)),
        )

        FirstKey = BuildPhysicalGlobalRouteTreeResultCacheKey(
            Request,
            "resource-graph",
            "technology",
        )
        SecondKey = BuildPhysicalGlobalRouteTreeResultCacheKey(
            Request,
            "resource-graph",
            "technology",
        )

        self.assertNotEqual(
            FirstPlan.PlanFingerprint,
            SecondPlan.PlanFingerprint,
        )
        self.assertEqual(FirstKey, SecondKey)
        Cache: dict[str, object] = {}
        FirstResult = object()
        RetainPhysicalGlobalRouteTreeResults(
            Cache,
            ((FirstKey, FirstResult),),
        )
        self.assertIs(
            TouchPhysicalGlobalRouteTreeResult(Cache, SecondKey),
            FirstResult,
        )

    def testPhysicalGlobalRouteTreeCacheMissesChangedRequestGeometry(
        self,
    ) -> None:
        BaseRequest = (
            "PortA",
            ((1, 2, 3),),
            frozenset({(4, 2, 3)}),
            ((1, 0), (2, 0)),
        )
        ChangedBlockedRequest = (
            "PortA",
            ((1, 2, 3),),
            frozenset({(5, 2, 3)}),
            ((1, 0), (2, 0)),
        )

        BaseKey = BuildPhysicalGlobalRouteTreeResultCacheKey(
            BaseRequest,
            "resource-graph",
            "technology",
        )

        self.assertNotEqual(
            BaseKey,
            BuildPhysicalGlobalRouteTreeResultCacheKey(
                ChangedBlockedRequest,
                "resource-graph",
                "technology",
            ),
        )
        self.assertNotEqual(
            BaseKey,
            BuildPhysicalGlobalRouteTreeResultCacheKey(
                BaseRequest,
                "changed-resource-graph",
                "technology",
            ),
        )

    def testPhysicalGlobalRouteTreeCacheRetentionIsBoundedLru(self) -> None:
        Cache = {"a": 1, "b": 2, "c": 3}

        self.assertEqual(
            TouchPhysicalGlobalRouteTreeResult(Cache, "a"),
            1,
        )
        EvictedCount = RetainPhysicalGlobalRouteTreeResults(
            Cache,
            (("d", 4), ("e", 5)),
            MaximumEntries=3,
        )

        self.assertEqual(EvictedCount, 2)
        self.assertEqual(Cache, {"a": 1, "d": 4, "e": 5})

    def testPhysicalGlobalPortalIdentityFollowsOnlyGlobalSeam(self) -> None:
        Attachment = (4, 3, 7)
        GlobalPath = (Attachment, (5, 3, 7))

        def Port(ReservationFingerprint, LocalPath, *, Path=GlobalPath):
            return SimpleNamespace(
                Signal="PortA",
                Direction="output",
                Attachment=Path[0],
                GlobalPath=Path,
                LocalPath=LocalPath,
                Capacity=1,
                ReservationFingerprint=ReservationFingerprint,
            )

        First = Port("local-reservation-a", ((2, 3, 7), Attachment))
        LocalChanged = Port(
            "local-reservation-b",
            ((1, 3, 7), (2, 3, 7), Attachment),
        )
        GlobalChangedPath = ((4, 3, 8), (5, 3, 8))
        GlobalChanged = Port(
            "local-reservation-c",
            ((2, 3, 8), GlobalChangedPath[0]),
            Path=GlobalChangedPath,
        )

        self.assertEqual(
            BuildPhysicalComponentGlobalPortalId(First, 1),
            BuildPhysicalComponentGlobalPortalId(LocalChanged, 1),
        )
        self.assertNotEqual(
            BuildPhysicalComponentGlobalPortalId(First, 1),
            BuildPhysicalComponentGlobalPortalId(GlobalChanged, 1),
        )

    def testRawPortalCacheMatchesOnlyIdenticalGeometryControls(self) -> None:
        Placed = object()
        Resources = object()
        Region = object()
        Context = object()
        Cache = RawPortalGeometryCache(
            PlacementGeometryFingerprint=(
                BuildRawPortalPlacementGeometryFingerprint(Placed)
            ),
            ResourceGeometryFingerprint=(
                BuildRawPortalResourceGeometryFingerprint(Resources)
            ),
            PlacedReference=Placed,
            ResourcesReference=Resources,
            Region=Region,
            LayerCount=3,
            PortalLimit=9,
            PortalVariantCounts=(("A", 9),),
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            Context=Context,
            AssignmentIndexed=IndexedRoutingResourceGraph(
                ResourcePositions=(),
                PositionIndices={},
            ),
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

    def testRawPortalCacheOpaqueIdentityCannotAliasAnotherObject(
        self,
    ) -> None:
        Placed = object()
        Resources = object()
        Cache = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2},
        )

        self.assertTrue(Cache.MatchesPlacementResources(Placed, Resources))
        self.assertFalse(Cache.MatchesPlacementResources(
            object(),
            Resources,
        ))
        self.assertFalse(Cache.MatchesPlacementResources(
            Placed,
            object(),
        ))

    def testRawPortalCacheMatchesEquivalentStructuralGeometry(
        self,
    ) -> None:
        def BuildPlaced(X):
            return SimpleNamespace(PlacedGates=[SimpleNamespace(
                Name="A",
                Kind="NAND",
                X=X,
                Y=0,
                Z=0,
                Rotation=0,
                MirrorX=False,
                InputPins=[(0, 0, 0), (0, 0, 1)],
                OutputPin=(1, 0, 0),
                InputDirections=[(-1, 0, 0), (-1, 0, 0)],
                OutputDirection=(1, 0, 0),
                Inputs=["I0", "I1"],
                Outputs=["O"],
            )])

        FirstPlaced = BuildPlaced(3)
        EquivalentPlaced = BuildPlaced(3)
        DifferentPlaced = BuildPlaced(4)
        FirstResources = RoutingResources(RoutingStaticGeometry(
            frozenset({(3, 0, 0)}),
            frozenset({(3, 0, 1)}),
        ))
        EquivalentResources = RoutingResources(RoutingStaticGeometry(
            frozenset({(3, 0, 0)}),
            frozenset({(3, 0, 1)}),
        ))
        Cache = self.BuildRawPortalCache(
            FirstPlaced,
            FirstResources,
            {"Alpha": 2},
        )

        self.assertTrue(Cache.MatchesPlacementResources(
            EquivalentPlaced,
            EquivalentResources,
        ))
        self.assertFalse(Cache.MatchesPlacementResources(
            DifferentPlaced,
            EquivalentResources,
        ))

    def testRawPortalCacheReusesGuidePlanOnlyForSameGeometryAndLayer(
        self,
    ) -> None:
        Placed = object()
        Resources = object()
        GuidePlan = object()
        Cache = RawPortalGeometryCache(
            PlacementGeometryFingerprint=(
                BuildRawPortalPlacementGeometryFingerprint(Placed)
            ),
            ResourceGeometryFingerprint=(
                BuildRawPortalResourceGeometryFingerprint(Resources)
            ),
            PlacedReference=Placed,
            ResourcesReference=Resources,
            Region=object(),
            LayerCount=3,
            PortalLimit=9,
            PortalVariantCounts=(("A", 9),),
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            Context=object(),
            AssignmentIndexed=IndexedRoutingResourceGraph(
                ResourcePositions=(),
                PositionIndices={},
            ),
            PortalEntries=(),
            RequestCount=3,
            TargetCount=9,
            StarvationCount=0,
            GuidePlanPrepared=True,
            GuidePlan=GuidePlan,
        )

        self.assertTrue(
            Cache.MatchesGuidePlan(Placed, Resources, 3)
        )
        self.assertIs(Cache.GuidePlan, GuidePlan)
        self.assertFalse(
            Cache.MatchesGuidePlan(object(), Resources, 3)
        )
        self.assertFalse(
            Cache.MatchesGuidePlan(Placed, object(), 3)
        )
        self.assertFalse(
            Cache.MatchesGuidePlan(Placed, Resources, 4)
        )
        self.assertFalse(
            replace(
                Cache,
                GuidePlanPrepared=False,
            ).MatchesGuidePlan(Placed, Resources, 3)
        )

        PortableCache = replace(
            Cache,
            GuideInputFingerprint="same-guide-input",
        )
        self.assertTrue(
            PortableCache.MatchesGuidePlan(
                object(),
                object(),
                3,
                "same-guide-input",
            )
        )
        self.assertFalse(
            PortableCache.MatchesGuidePlan(
                object(),
                object(),
                3,
                "different-guide-input",
            )
        )

    def testCapacityAwareGuideInputFingerprintIsOrderInvariantAndExact(
        self,
    ) -> None:
        def Profile(
            Root: tuple[int, int, int],
            Target: tuple[int, int, int],
            Criticality: int,
        ) -> SimpleNamespace:
            return SimpleNamespace(
                SourceAccessPath=(Root,),
                TargetAccessPaths={Target: (Target,)},
                Span=abs(Target[0] - Root[0]),
                Criticality=Criticality,
                Fanout=1,
            )

        Alpha = Profile((0, 1, 0), (6, 1, 3), 4)
        Beta = Profile((9, 1, 3), (12, 1, 6), 2)
        Arguments = (
            3,
            0,
            0,
            DefaultPhysicalDesignPolicy.GlobalRouting,
            DefaultRedstoneRoutingTechnology,
            DefaultPhysicalDesignPolicy.Placement.LocalFanoutDistance,
        )

        Fingerprint = BuildCapacityAwareGuideInputFingerprint(
            {"Alpha": Alpha, "Beta": Beta},
            *Arguments,
        )
        self.assertEqual(
            Fingerprint,
            BuildCapacityAwareGuideInputFingerprint(
                {"Beta": Beta, "Alpha": Alpha},
                *Arguments,
            ),
        )
        self.assertNotEqual(
            Fingerprint,
            BuildCapacityAwareGuideInputFingerprint(
                {
                    "Alpha": SimpleNamespace(
                        **{
                            **vars(Alpha),
                            "Criticality": Alpha.Criticality + 1,
                        }
                    ),
                    "Beta": Beta,
                },
                *Arguments,
            ),
        )

    def testFactorizedPhysicalGuideIdentityChangesOnlyEditedSignal(
        self,
    ) -> None:
        Base = SimpleNamespace(
            Guides={"A": {(0, 0), (1, 0)}, "B": {(0, 2), (1, 2)}},
            Layers={"A": 0, "B": 1},
            Axes={"A": "X", "B": "X"},
            Lanes={"A": 0, "B": 2},
            Overflow={},
        )
        Changed = SimpleNamespace(
            Guides={"A": {(0, 0), (1, 0)}, "B": {(0, 3), (1, 3)}},
            Layers={"A": 0, "B": 1},
            Axes={"A": "X", "B": "X"},
            Lanes={"A": 0, "B": 3},
            Overflow={},
        )
        Inputs = {"A": "input-a", "B": "input-b"}

        First = BuildFactorizedPhysicalGuideIdentity(Base, Inputs)
        Second = BuildFactorizedPhysicalGuideIdentity(Changed, Inputs)

        self.assertEqual(
            First.FactorFingerprintBySignal()["A"],
            Second.FactorFingerprintBySignal()["A"],
        )
        self.assertNotEqual(
            First.FactorFingerprintBySignal()["B"],
            Second.FactorFingerprintBySignal()["B"],
        )
        self.assertNotEqual(
            First.JointCapacityAssignmentFingerprint,
            Second.JointCapacityAssignmentFingerprint,
        )

    def testExteriorRouteDomainRetainsGlobalPortContractIdentity(self) -> None:
        def Identity(Path):
            NodeSet = frozenset(Path)
            Port = SimpleNamespace(
                Signal="Alpha",
                Direction="output",
                Attachment=Path[0],
                GlobalPath=tuple(Path),
                Capacity=1,
                ReservationFingerprint="port",
                Claims=RoutingResourceClaims(
                    WireCells=NodeSet,
                    ElectricalCells=NodeSet,
                ),
                GlobalClaims=RoutingResourceClaims(
                    WireCells=NodeSet,
                    ElectricalCells=NodeSet,
                ),
            )
            Plan = SimpleNamespace(
                Ports=(Port,),
                PlanningChannels=(SimpleNamespace(
                    Signal="Alpha",
                    ReservationFingerprint="channel",
                ),),
                GlobalKeepoutNodes=frozenset({
                    (0, 2, 0), (1, 2, 0), (2, 2, 0),
                }),
                ComponentGraphFingerprint="component",
                ResourceGraphFingerprint="resource",
                TechnologyFingerprint="technology",
            )
            return BuildPhysicalSignalApertureCandidateDomainIdentity(
                BuildCertifiedPhysicalComponentApertureDomain(
                    Plan,
                    Complete=True,
                ),
                "Alpha",
                "authoritative-request-domain",
                ((2, 2, 0),),
                CoverageCursor=3,
                Complete=False,
            )

        First = Identity(((0, 2, 0), (1, 2, 0)))
        ChangedGlobalPath = Identity(((0, 2, 0), (0, 2, 1)))

        self.assertNotEqual(
            First.PortGlobalContractFingerprint,
            ChangedGlobalPath.PortGlobalContractFingerprint,
        )
        self.assertNotEqual(
            First.StableDomainFingerprint,
            ChangedGlobalPath.StableDomainFingerprint,
        )

    def testCompleteRouteDomainReplayBindsOrderedDescriptorUniverse(self) -> None:
        Continuation = PhysicalSignalRouteDomainContinuation(
            PreSiblingDomainFingerprint="stable-domain",
            Signal="Alpha",
            RequestDomainFingerprint="request-domain",
            RequestDescriptorFingerprints=("late", "early"),
            NextDescriptorCursor=2,
            Candidates=(),
            CompletedDescriptorFingerprints=frozenset(("late", "early")),
            Complete=True,
        )

        Restored = SelectReplayablePhysicalSignalRouteDomainContinuation(
            {"stable-domain": Continuation},
            "stable-domain",
            "Alpha",
            "request-domain",
            ("early", "late", "schedule-alias"),
        )

        self.assertIsNone(Restored)
        self.assertIsNone(
            SelectReplayablePhysicalSignalRouteDomainContinuation(
                {"stable-domain": Continuation},
                "stable-domain",
                "Alpha",
                "changed-request-domain",
                ("late", "early"),
            )
        )

    def testCompleteEmptyRouteDomainIsAnExactReplayProof(self) -> None:
        Empty = PhysicalSignalRouteDomainContinuation(
            PreSiblingDomainFingerprint="stable-domain",
            Signal="Alpha",
            RequestDomainFingerprint="request-domain",
            RequestDescriptorFingerprints=("shape-0", "shape-1"),
            NextDescriptorCursor=2,
            Candidates=(),
            CompletedDescriptorFingerprints=frozenset((
                "shape-0",
                "shape-1",
            )),
            Complete=True,
        )

        self.assertTrue(PhysicalSignalRouteDomainIsCertifiedEmpty(
            Empty,
            Signal="Alpha",
            PreSiblingDomainFingerprint="stable-domain",
            RequestDomainFingerprint="request-domain",
        ))
        self.assertFalse(PhysicalSignalRouteDomainIsCertifiedEmpty(
            replace(
                Empty,
                Candidates=(SimpleNamespace(CandidateId="candidate"),),
                CandidateMetadata=(("candidate", ()),),
            ),
            Signal="Alpha",
            PreSiblingDomainFingerprint="stable-domain",
            RequestDomainFingerprint="request-domain",
        ))
        self.assertFalse(PhysicalSignalRouteDomainIsCertifiedEmpty(
            Empty,
            Signal="Alpha",
            PreSiblingDomainFingerprint="different-domain",
            RequestDomainFingerprint="request-domain",
        ))

    def testPortableStructuralBucketDefersFullCanonicalization(
        self,
    ) -> None:
        self.assertEqual(
            SelectPortableReplayTelemetryReason({
                "PortableReplayReason": "hit",
                "Reason": "stale-fallback",
            }),
            "hit",
        )
        self.assertEqual(
            SelectPortableReplayTelemetryReason({
                "Reason": "structural-bucket-miss",
            }),
            "structural-bucket-miss",
        )
        Source = PinAccessPortal(
            PortalId="source-a",
            Signal="Alpha",
            Terminal=(0, 2, 0),
            Layer=0,
            Path=((0, 2, 0), (1, 2, 0)),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(),
            Length=2,
            BendCount=0,
            ViaCount=0,
            Cost=2,
        )
        Target = replace(
            Source,
            PortalId="target-a",
            Terminal=(4, 2, 0),
            Path=((4, 2, 0), (3, 2, 0)),
        )
        Descriptor = CandidateRequestShapeDescriptor(
            SourcePortal=Source,
            TargetPortals=(Target,),
            Guide=frozenset(((1, 0), (2, 0))),
            Layer=0,
            Axis="X",
            Lane=0,
            Variant=0,
            PortalShapeRank=0,
            RoutingY=2,
            GuideExpansion=2,
            InitiallyDeferred=False,
            Priority=(),
        )
        Plan = SimpleNamespace(
            Ports=(SimpleNamespace(
                Signal="Alpha",
                Direction="output",
                Attachment=(0, 2, 0),
                Capacity=1,
            ),),
            GlobalBoundaryPorts=(),
            PlanningChannels=(SimpleNamespace(
                Signal="Alpha",
                Layer=0,
                Capacity=1,
                FeedthroughComponentIds=(),
            ),),
            ComponentGraphFingerprint="component-a",
            TechnologyFingerprint="technology-a",
        )
        Preparation = PreparePortablePhysicalSignalRouteDomain(
            Plan,
            "Alpha",
            (Descriptor,),
            ((0, 2, 0),),
            ((8, 2, 0),),
            ((1, 2, 0),),
            (),
        )
        Cache = {}
        Canonicalizer = (
            'PhysicalDesign.Routing.Global.Candidates.CandidateGuides.BuildPortablePhysicalSignalRouteDomainIdentity'
        )
        with patch(Canonicalizer, wraps=(
            BuildPortablePhysicalSignalRouteDomainIdentity
        )) as BuildFull:
            Restored, Reason = (
                SelectPreparedPortablePhysicalSignalRouteDomainContinuation(
                    Cache,
                    Preparation,
                )
            )
            self.assertIsNone(Restored)
            self.assertEqual(Reason, "structural-bucket-miss")
            BuildFull.assert_not_called()

            self.assertEqual(
                RetainCompletePortablePhysicalSignalRouteDomains(
                    Cache,
                    {"Alpha": Preparation},
                    {"Alpha": 1},
                    {"Alpha": ()},
                    {"Alpha": {}},
                ),
                (),
            )
            BuildFull.assert_not_called()

            Published = RetainCompletePortablePhysicalSignalRouteDomains(
                Cache,
                {"Alpha": Preparation},
                {"Alpha": 0},
                {"Alpha": ()},
                {"Alpha": {}},
            )
            self.assertEqual(len(Published), 1)
            self.assertEqual(BuildFull.call_count, 1)

            Restored, Reason = (
                SelectPreparedPortablePhysicalSignalRouteDomainContinuation(
                    Cache,
                    Preparation,
                )
            )
            # A portable empty domain is never replayed: negative evidence
            # and completeness remain exact-plan-only.
            self.assertIsNone(Restored)
            self.assertEqual(Reason, "portal-rebind-mismatch")
            self.assertEqual(BuildFull.call_count, 2)

            Bucket = next(
                Value for Key, Value in Cache.items()
                if Key.startswith("portable-route-domain-bucket:")
            )
            FullKey = next(iter(Bucket))
            OriginalEntry = Bucket[FullKey]
            OriginalPortalId, OriginalGeometry = (
                OriginalEntry.PortalGeometryById[0]
            )
            Bucket[FullKey] = replace(
                OriginalEntry,
                PortalGeometryById=((
                    OriginalPortalId,
                    (
                        OriginalGeometry[0],
                        (99, 99, 99),
                        OriginalGeometry[2],
                    ),
                ),),
            )
            Restored, Reason = (
                SelectPreparedPortablePhysicalSignalRouteDomainContinuation(
                    Cache,
                    Preparation,
                )
            )
            self.assertIsNone(Restored)
            self.assertEqual(Reason, "portal-rebind-mismatch")
            Bucket[FullKey] = OriginalEntry

            ChangedGeometry = replace(
                Preparation,
                BlockedNodes=((9, 2, 0),),
            )
            Restored, Reason = (
                SelectPreparedPortablePhysicalSignalRouteDomainContinuation(
                    Cache,
                    ChangedGeometry,
                )
            )
            self.assertIsNone(Restored)
            self.assertEqual(Reason, "full-identity-mismatch")
            self.assertEqual(BuildFull.call_count, 4)

    def testPortableCompleteRouteDomainRebindsTranslatedPortalIds(
        self,
    ) -> None:
        def BuildFixture(Transform="Identity", Translation=(0, 0, 0)):
            def Position(Value):
                return TransformPlanarRoutingPosition(
                    Value, Transform, Translation
                )

            SourcePath = tuple(map(Position, ((0, 2, 0), (1, 2, 0))))
            TargetPath = tuple(map(Position, ((4, 2, 0), (3, 2, 0))))
            Source = PinAccessPortal(
                PortalId="source:" + str(Translation) + Transform,
                Signal="Alpha",
                Terminal=SourcePath[0],
                Layer=0,
                Path=SourcePath,
                Edges=frozenset(),
                Claims=RoutingResourceClaims(
                    WireCells=frozenset(SourcePath)
                ),
                Length=2,
                BendCount=0,
                ViaCount=0,
                Cost=2,
            )
            Target = replace(
                Source,
                PortalId="target:" + str(Translation) + Transform,
                Terminal=TargetPath[0],
                Path=TargetPath,
                Claims=RoutingResourceClaims(
                    WireCells=frozenset(TargetPath)
                ),
            )
            Descriptor = CandidateRequestShapeDescriptor(
                SourcePortal=Source,
                TargetPortals=(Target,),
                Guide=frozenset(
                    (Value[0], Value[2])
                    for Value in map(Position, ((1, 2, 0), (2, 2, 0)))
                ),
                Layer=0,
                Axis="X",
                Lane=0,
                Variant=0,
                PortalShapeRank=0,
                RoutingY=Position((0, 2, 0))[1],
                GuideExpansion=2,
                InitiallyDeferred=False,
                Priority=(),
            )
            Port = SimpleNamespace(
                Signal="Alpha",
                Direction="output",
                Attachment=Source.Terminal,
                GlobalPath=Source.Path,
                Capacity=1,
            )
            Channel = SimpleNamespace(
                Signal="Alpha",
                Layer=0,
                Capacity=1,
                FeedthroughComponentIds=(),
            )
            Plan = SimpleNamespace(
                Ports=(Port,),
                GlobalBoundaryPorts=(),
                PlanningChannels=(Channel,),
                ComponentGraphFingerprint="component-a",
                TechnologyFingerprint="technology-a",
            )
            Identity = BuildPortablePhysicalSignalRouteDomainIdentity(
                Plan,
                "Alpha",
                (Descriptor,),
                SourcePath,
                tuple(map(Position, ((7, 2, 1),))),
                (SourcePath[-1],),
                (),
            )
            Nodes = frozenset(map(Position, (
                (0, 2, 0), (1, 2, 0), (2, 2, 0),
                (3, 2, 0), (4, 2, 0),
            )))
            Candidate = NetRouteCandidate(
                CandidateId="candidate:" + str(Translation) + Transform,
                Signal="Alpha",
                SourcePortalId=Source.PortalId,
                TargetPortalIds={Target.Terminal: Target.PortalId},
                Nodes=Nodes,
                Edges=frozenset(
                    (First, Second)
                    for First, Second in zip(
                        sorted(Nodes), sorted(Nodes)[1:]
                    )
                ),
                Claims=RoutingResourceClaims(WireCells=Nodes),
                Layer=0,
                Guide=Descriptor.Guide,
                RepeaterWaypoints=(),
                MaterialCost=5,
                FootprintGrowth=5,
                Length=5,
                BendCount=0,
                ViaCount=0,
            )
            return Plan, Identity, Source, Target, Candidate

        _OldPlan, OldIdentity, _OldSource, _OldTarget, Candidate = (
            BuildFixture()
        )
        Cache = {}
        Retained = RetainPortablePhysicalSignalRouteDomainContinuation(
            Cache,
            OldIdentity[0],
            OldIdentity[1],
            "Alpha",
            OldIdentity[2],
            OldIdentity[3],
            OldIdentity[4],
            (Candidate,),
            {Candidate.CandidateId: ("X", 0, 0, 0)},
            Complete=True,
        )
        self.assertIsNotNone(Retained)

        _NewPlan, NewIdentity, NewSource, NewTarget, _NewCandidate = (
            BuildFixture("Rotate90", (20, 0, 10))
        )
        self.assertEqual(OldIdentity[0], NewIdentity[0])
        Restored = SelectPortablePhysicalSignalRouteDomainContinuation(
            Cache,
            NewIdentity[0],
            NewIdentity[1],
            "Alpha",
            NewIdentity[2],
            NewIdentity[3],
            NewIdentity[4],
        )

        self.assertIsNotNone(Restored)
        assert Restored is not None
        self.assertFalse(Restored.Complete)
        self.assertEqual(Restored.CompletedDescriptorFingerprints, frozenset())
        ExactPlanCache = {}
        Progress, _Advanced = RetainPhysicalSignalRouteDomainDescriptorProgress(
            ExactPlanCache,
            PreSiblingDomainFingerprint="current-stable-domain",
            Signal="Alpha",
            RequestDomainFingerprint="current-request-domain",
            RequestDescriptorFingerprints=("current-descriptor",),
            CompletedDescriptorFingerprints=(),
            Candidates=Restored.Candidates,
            CandidateMetadata=dict(Restored.CandidateMetadata),
        )
        self.assertFalse(Progress.Complete)
        self.assertEqual(
            Progress.RemainingDescriptorFingerprints,
            frozenset(("current-descriptor",)),
        )
        self.assertEqual(Restored.RequestDescriptorFingerprints, ())
        self.assertEqual(
            Restored.Candidates[0].SourcePortalId,
            NewSource.PortalId,
        )
        self.assertEqual(
            set(Restored.Candidates[0].TargetPortalIds.values()),
            {NewTarget.PortalId},
        )
        self.assertIn(NewSource.Terminal, Restored.Candidates[0].Nodes)
        self.assertEqual(
            dict(Restored.CandidateMetadata)[
                Restored.Candidates[0].CandidateId
            ][:2],
            ("Z", 20),
        )
        ConflictingNode = next(iter(
            Restored.Candidates[0].Claims.WireCells
        ))
        CurrentSiblingClaims = RoutingResourceClaims(
            WireCells=frozenset((ConflictingNode,)),
            ElectricalCells=frozenset((ConflictingNode,)),
        )
        self.assertEqual(
            FilterPhysicalCandidatesAgainstSiblingApertures(
                Restored.Candidates,
                (("CurrentSibling", CurrentSiblingClaims),),
            ),
            (),
        )

        _MirrorPlan, MirrorIdentity, _MirrorSource, _MirrorTarget, _ = (
            BuildFixture("MirrorX", (30, 0, 5))
        )
        Mirrored = SelectPortablePhysicalSignalRouteDomainContinuation(
            Cache,
            MirrorIdentity[0],
            MirrorIdentity[1],
            "Alpha",
            MirrorIdentity[2],
            MirrorIdentity[3],
            MirrorIdentity[4],
        )
        self.assertIsNotNone(Mirrored)
        assert Mirrored is not None
        self.assertEqual(
            dict(Mirrored.CandidateMetadata)[
                Mirrored.Candidates[0].CandidateId
            ][:2],
            ("X", 5),
        )

    def testPortableRouteDomainRejectsIdentityMismatchAndOpenDomain(
        self,
    ) -> None:
        Candidate = SimpleNamespace(CandidateId="candidate-a")
        Cache = {}
        self.assertIsNone(
            RetainPortablePhysicalSignalRouteDomainContinuation(
                Cache,
                "portable-a",
                "identity-a",
                "Alpha",
                (0, 2, 0),
                "Identity",
                (("portal-a", (0, (0, 0, 0), ((0, 0, 0),))),),
                (Candidate,),
                {"candidate-a": ("X", 0, 0, 0)},
                Complete=False,
            )
        )
        self.assertFalse(Cache)

        # A complete entry is still unusable under a different structural or
        # technology identity, even if its portable geometry key is supplied.
        from PhysicalDesign.Routing.Global.Candidates.CandidateGuides import PortablePhysicalSignalRouteDomainContinuation
        Cache["portable-route-domain:portable-a"] = (
            PortablePhysicalSignalRouteDomainContinuation(
            PortableDomainFingerprint="portable-a",
            IdentityFingerprint="identity-a",
            Signal="Alpha",
            Attachment=(0, 2, 0),
            CanonicalTransform="Identity",
            PortalGeometryById=(
                ("portal-a", (0, (0, 0, 0), ((0, 0, 0),))),
            ),
            Candidates=(),
                CandidateMetadata=(),
            )
        )
        self.assertIsNone(
            SelectPortablePhysicalSignalRouteDomainContinuation(
                Cache,
                "portable-a",
                "changed-technology-or-graph",
                "Alpha",
                (5, 2, 0),
                "Identity",
                (("portal-b", (0, (0, 0, 0), ((0, 0, 0),))),),
            )
        )

    def testChangedExteriorRegionDependencyPreventsDomainReplay(self) -> None:
        ApertureDomain = SimpleNamespace(
            Complete=True,
            Factors=(SimpleNamespace(
                Signal="Alpha",
                ApertureFingerprint="aperture-alpha",
                PortGlobalContractFingerprint="global-alpha",
                ChannelReservationFingerprint="channel-alpha",
            ),),
            StableKeepoutCoreFingerprint="stable-core",
            ResourceGraphFingerprint="resource",
            TechnologyFingerprint="technology",
        )

        def Identity(ExteriorRegionFingerprint):
            return BuildPhysicalSignalApertureCandidateDomainIdentity(
                ApertureDomain,
                "Alpha",
                ExteriorRegionFingerprint,
                ((2, 2, 0),),
                CoverageCursor=0,
                Complete=False,
            )

        First = Identity("exterior-region-a")
        ChangedRegion = Identity("exterior-region-b")
        Continuation = PhysicalSignalRouteDomainContinuation(
            PreSiblingDomainFingerprint=First.StableDomainFingerprint,
            Signal="Alpha",
            RequestDomainFingerprint="request-domain-a",
            RequestDescriptorFingerprints=("shape-0",),
            NextDescriptorCursor=1,
            Candidates=(),
            CompletedDescriptorFingerprints=frozenset(("shape-0",)),
            Complete=True,
        )

        self.assertNotEqual(
            First.StableDomainFingerprint,
            ChangedRegion.StableDomainFingerprint,
        )
        self.assertIsNone(
            SelectReplayablePhysicalSignalRouteDomainContinuation(
                {First.StableDomainFingerprint: Continuation},
                ChangedRegion.StableDomainFingerprint,
                "Alpha",
                "request-domain-b",
                ("shape-0",),
            )
        )

    def testPriorCandidateReplayReappliesCurrentSiblingApertures(self) -> None:
        Shared = (2, 2, 0)
        Conflicting = SimpleNamespace(
            CandidateId="conflicting",
            Claims=RoutingResourceClaims(
                WireCells=frozenset((Shared,)),
                ElectricalCells=frozenset((Shared,)),
            ),
        )
        Clear = SimpleNamespace(
            CandidateId="clear",
            Claims=RoutingResourceClaims(
                WireCells=frozenset(((8, 2, 0),)),
                ElectricalCells=frozenset(((8, 2, 0),)),
            ),
        )
        SiblingClaims = RoutingResourceClaims(
            WireCells=frozenset((Shared,)),
            ElectricalCells=frozenset((Shared,)),
        )

        Retained = FilterPhysicalCandidatesAgainstSiblingApertures(
            (Conflicting, Clear),
            (("Beta", SiblingClaims),),
        )

        self.assertEqual(
            tuple(Value.CandidateId for Value in Retained),
            ("clear",),
        )

    def testReplayedSiblingProjectionRetainsApertureProofWitnesses(
        self,
    ) -> None:
        FirstShared = (2, 2, 0)
        SecondShared = (3, 2, 0)

        def Claims(*Nodes):
            Values = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=Values,
                ElectricalCells=Values,
            )

        Candidates = (
            SimpleNamespace(
                CandidateId="first",
                Claims=Claims(FirstShared),
            ),
            SimpleNamespace(
                CandidateId="second",
                Claims=Claims(SecondShared),
            ),
        )
        SiblingClaims = Claims(FirstShared, SecondShared)
        ConflictSets = []

        def Classify(CandidateClaims):
            Conflicts = (
                ("Beta",)
                if CandidateClaims.WireCells & SiblingClaims.WireCells
                else ()
            )
            if Conflicts:
                ConflictSets.append(frozenset(Conflicts))
            return Conflicts

        Retained = FilterPhysicalCandidatesAgainstSiblingApertures(
            Candidates,
            (("Beta", SiblingClaims),),
            ConflictClassifier=Classify,
        )
        NoGood = BuildMinimalPhysicalRequestApertureNoGood(
            "Alpha",
            "request-domain",
            ConflictSets,
            {"Beta": "beta-aperture"},
        )

        self.assertEqual(Retained, ())
        self.assertEqual(
            ConflictSets,
            [frozenset(("Beta",)), frozenset(("Beta",))],
        )
        self.assertEqual(NoGood, frozenset((
            ("Alpha", "request-factor:request-domain"),
            ("Beta", "aperture-factor:beta-aperture"),
        )))

    def testRequestApertureNoGoodRetainsRequiredHigherOrderCut(self) -> None:
        NoGood = BuildMinimalPhysicalRequestApertureNoGood(
            "Alpha",
            "request-alpha",
            (("Beta",), ("Gamma",)),
            {
                "Beta": "aperture-beta",
                "Gamma": "aperture-gamma",
            },
        )

        self.assertEqual(NoGood, frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
            ("Gamma", "aperture-factor:aperture-gamma"),
        )))
        self.assertFalse(BuildMinimalPhysicalRequestApertureNoGood(
            "Alpha",
            "request-alpha",
            (("Missing",),),
            {"Beta": "aperture-beta"},
        ))

    def testPhysicalPortCorridorDomainIdentityRejectsDependencyMismatch(
        self,
    ) -> None:
        Port = SimpleNamespace(
            Signal="A",
            Direction="output",
            Capacity=1,
            Attachment=(0, 1, 0),
            GlobalPath=((0, 1, 0),),
            ReservationFingerprint="port-a",
        )
        Claims = RoutingResourceClaims(
            WireCells=frozenset({(0, 1, 0), (1, 1, 0)}),
            SupportCells=frozenset({(0, 0, 0), (1, 0, 0)}),
            RequiredAirCells=frozenset(),
            ElectricalCells=frozenset({(0, 1, 0), (1, 1, 0)}),
        )
        Candidate = SimpleNamespace(
            Signal="A",
            CandidateId="route-a",
            Layer=0,
            Nodes=frozenset({(0, 1, 0), (1, 1, 0)}),
            Edges=frozenset({((0, 1, 0), (1, 1, 0))}),
            Claims=Claims,
            RepeaterWaypoints=(),
        )

        First = BuildPhysicalPortCorridorDomain(
            Port, (Candidate,), "requests-a", "graph", "technology",
            Complete=True,
        )
        Equivalent = BuildPhysicalPortCorridorDomain(
            Port, (Candidate,), "requests-a", "graph", "technology",
            Complete=True,
        )
        Changed = BuildPhysicalPortCorridorDomain(
            Port, (Candidate,), "requests-b", "graph", "technology",
            Complete=True,
        )

        self.assertEqual(First, Equivalent)
        self.assertTrue(First.Complete)
        self.assertEqual(len(First.Factors), 1)
        self.assertNotEqual(
            First.DomainFingerprint,
            Changed.DomainFingerprint,
        )

    def testReservedFilteringDoesNotMutateRawPortalCache(self) -> None:
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0))
        Second = self.BuildPortal("A", (0, 1, 0), (2, 1, 0))
        Key = ("A", (0, 1, 0), 0)
        Cache = RawPortalGeometryCache(
            PlacementGeometryFingerprint="opaque-placement",
            ResourceGeometryFingerprint="opaque-resources",
            PlacedReference=object(),
            ResourcesReference=object(),
            Region=object(),
            LayerCount=1,
            PortalLimit=2,
            PortalVariantCounts=(("A", 2),),
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            Context=object(),
            AssignmentIndexed=IndexedRoutingResourceGraph(
                ResourcePositions=(),
                PositionIndices={},
            ),
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

    def testRawPortalResourceCacheSelectsExactBeforeCoordinatedDelta(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        Base = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2, "Beta": 2},
        )
        Exact = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2, "Beta": 6},
        )
        Arguments = {
            "Placed": Placed,
            "Resources": Resources,
            "LayerCount": 2,
            "PortalLimit": 6,
            "PortalVariantCounts": {"Alpha": 2, "Beta": 6},
            "GuideExpansion": 3,
            "StrictMaximumExpansions": 100,
            "AccessGeometryFingerprint": ("access",),
            "CoordinatedSignals": frozenset({"Beta"}),
        }

        ExactPlan = SelectRawPortalGeometryReusePlan(
            (Exact, Base),
            **Arguments,
        )
        self.assertIsNotNone(ExactPlan)
        self.assertIs(ExactPlan.Cache, Exact)
        self.assertTrue(ExactPlan.ExactMatch)
        self.assertEqual(
            ExactPlan.ReusedSignals,
            frozenset({"Alpha", "Beta"}),
        )
        self.assertEqual(ExactPlan.GeneratedSignals, frozenset())

        PartialPlan = SelectRawPortalGeometryReusePlan(
            (Base,),
            **Arguments,
        )
        self.assertIsNotNone(PartialPlan)
        self.assertIs(PartialPlan.Cache, Base)
        self.assertFalse(PartialPlan.ExactMatch)
        self.assertEqual(
            PartialPlan.ReusedSignals,
            frozenset({"Alpha"}),
        )
        self.assertEqual(
            PartialPlan.GeneratedSignals,
            frozenset({"Beta"}),
        )

    def testRawPortalResourceCacheRejectsGeometryAndUnreportedDeltas(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        Cache = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2, "Beta": 2},
        )
        Arguments = {
            "Caches": (Cache,),
            "Placed": Placed,
            "Resources": Resources,
            "LayerCount": 2,
            "PortalLimit": 6,
            "PortalVariantCounts": {"Alpha": 2, "Beta": 6},
            "GuideExpansion": 3,
            "StrictMaximumExpansions": 100,
            "AccessGeometryFingerprint": ("access",),
            "CoordinatedSignals": frozenset({"Beta"}),
        }
        for Overrides in (
            {"Placed": object()},
            {
                "Resources": RoutingResources(
                    RoutingStaticGeometry(
                        frozenset({(9, 0, 9)}),
                        frozenset(),
                    )
                )
            },
            {"LayerCount": 3},
            {"PortalLimit": 5},
            {"GuideExpansion": 4},
            {"StrictMaximumExpansions": 101},
            {"AccessGeometryFingerprint": ("other-access",)},
            {"CoordinatedSignals": frozenset({"Alpha"})},
        ):
            with self.subTest(Overrides=Overrides):
                self.assertIsNone(SelectRawPortalGeometryReusePlan(
                    **{**Arguments, **Overrides}
                ))
        UnpreparedGuideCache = replace(
            Cache,
            GuidePlanPrepared=False,
        )
        self.assertIsNone(SelectRawPortalGeometryReusePlan(
            **{
                **Arguments,
                "Caches": (UnpreparedGuideCache,),
            }
        ))

    def testRawPortalResourceCachePortablePlanReusesTranslatedSignals(
        self,
    ) -> None:
        OldPlaced = object()
        OldResources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        AlphaGeometry = (
            "Alpha",
            (0, 1, 0),
            (),
            (),
        )
        OldBetaGeometry = (
            "Beta",
            (1, 1, 0),
            (),
            (),
        )
        NewBetaGeometry = (
            "Beta",
            (9, 1, 0),
            (),
            (),
        )
        Cache = self.BuildRawPortalCache(
            OldPlaced,
            OldResources,
            {"Alpha": 2, "Beta": 2},
            AccessGeometryFingerprint=(
                AlphaGeometry,
                OldBetaGeometry,
                ("packed-boundary-lease-v1", ("Alpha", "Beta")),
            ),
        )
        Plan = SelectRawPortalGeometryReusePlan(
            (Cache,),
            Placed=object(),
            Resources=RoutingResources(
                RoutingStaticGeometry(frozenset(), frozenset())
            ),
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts={"Alpha": 2, "Beta": 2},
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            AccessGeometryFingerprint=(
                AlphaGeometry,
                NewBetaGeometry,
                ("packed-boundary-lease-v1", ("Alpha", "Beta")),
            ),
            CoordinatedSignals=frozenset(),
            AllowPortableSignalReuse=True,
        )

        self.assertIsNotNone(Plan)
        self.assertTrue(Plan.PortableAcrossPlacement)
        self.assertFalse(Plan.ExactMatch)
        self.assertEqual(
            Plan.ReusedSignals,
            frozenset({"Alpha", "Beta"}),
        )
        self.assertEqual(Plan.GeneratedSignals, frozenset())
        self.assertEqual(
            dict(Plan.SignalTranslations),
            {"Alpha": (0, 0, 0), "Beta": (8, 0, 0)},
        )

    def testTranslatedPortablePortalIdentityDoesNotRetainLineage(
        self,
    ) -> None:
        Arguments = {
            "Signal": "Alpha",
            "Terminal": (9, 1, 0),
            "Layer": 1,
            "Path": ((9, 1, 0), (9, 2, 0)),
            "Length": 2,
            "BendCount": 0,
            "ViaCount": 1,
            "Cost": 7,
        }

        First = BuildTranslatedPortablePortalId(**Arguments)
        Second = BuildTranslatedPortablePortalId(**Arguments)

        self.assertEqual(First, Second)
        self.assertEqual(First.count("translated:"), 1)
        self.assertLess(len(First), 80)
        self.assertNotIn("Portal:", First)

    def testPortablePortalPositiveWitnessRevalidatesCurrentGeometry(
        self,
    ) -> None:
        Graph, Region, _Context = self.BuildGraph()
        SourcePath = ((0, 1, 0), (1, 1, 0))
        Portal = PinAccessPortal(
            PortalId="Alpha:source",
            Signal="Alpha",
            Terminal=(0, 1, 0),
            Layer=0,
            Path=SourcePath,
            Edges=frozenset((((0, 1, 0), (1, 1, 0)),)),
            Claims=Graph.BuildRouteClaims(SourcePath),
            Length=2,
            BendCount=0,
            ViaCount=0,
            Cost=2,
        )
        Materialized = (
            MaterializeValidatedPortablePortalPositiveWitness(
                Portal,
                Signal="Alpha",
                Terminal=(2, 1, 0),
                Layer=0,
                Transform="Identity",
                Translation=(2, 0, 0),
                ResourceGraph=Graph,
                RegionNodes=frozenset(Region.Nodes),
                RegionEdges=frozenset(Region.Edges),
            )
        )

        self.assertIsNotNone(Materialized)
        self.assertEqual(Materialized.Terminal, (2, 1, 0))
        self.assertEqual(
            Materialized.Path,
            ((2, 1, 0), (3, 1, 0)),
        )
        self.assertEqual(
            Materialized.Claims,
            Graph.BuildRouteClaims(Materialized.Path),
        )
        self.assertIn(":translated:", Materialized.PortalId)

    def testPortablePortalPositiveWitnessRejectsIdentityAndRegionMismatch(
        self,
    ) -> None:
        Graph, Region, _Context = self.BuildGraph()
        SourcePath = ((0, 1, 0), (1, 1, 0))
        Portal = PinAccessPortal(
            PortalId="Alpha:source",
            Signal="Alpha",
            Terminal=(0, 1, 0),
            Layer=0,
            Path=SourcePath,
            Edges=frozenset((((0, 1, 0), (1, 1, 0)),)),
            Claims=Graph.BuildRouteClaims(SourcePath),
            Length=2,
            BendCount=0,
            ViaCount=0,
            Cost=2,
        )
        Arguments = {
            "Portal": Portal,
            "Signal": "Alpha",
            "Terminal": (2, 1, 0),
            "Layer": 0,
            "Transform": "Identity",
            "Translation": (2, 0, 0),
            "ResourceGraph": Graph,
            "RegionNodes": frozenset(Region.Nodes),
            "RegionEdges": frozenset(Region.Edges),
        }

        self.assertIsNone(
            MaterializeValidatedPortablePortalPositiveWitness(
                **{**Arguments, "Terminal": (3, 1, 0)}
            )
        )
        self.assertIsNone(
            MaterializeValidatedPortablePortalPositiveWitness(
                **{
                    **Arguments,
                    "RegionNodes": frozenset(((2, 1, 0),)),
                }
            )
        )

    def testRawPortalPortableReuseCrossesComponentVariantSignalSets(
        self,
    ) -> None:
        OldPlaced = object()
        OldResources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        AlphaGeometry = (
            "Alpha",
            (0, 1, 0),
            (),
            (),
        )
        BetaGeometry = (
            "Beta",
            (1, 1, 0),
            (),
            (),
        )
        GammaGeometry = (
            "Gamma",
            (2, 1, 0),
            (),
            (),
        )
        Cache = self.BuildRawPortalCache(
            OldPlaced,
            OldResources,
            {"Alpha": 2, "Beta": 2},
            AccessGeometryFingerprint=(
                AlphaGeometry,
                BetaGeometry,
                (
                    "component-channel-a",
                    "component-fingerprint-a",
                ),
            ),
        )

        Plan = SelectRawPortalGeometryReusePlan(
            (Cache,),
            Placed=object(),
            Resources=RoutingResources(
                RoutingStaticGeometry(frozenset(), frozenset())
            ),
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts={"Alpha": 2, "Gamma": 2},
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            AccessGeometryFingerprint=(
                AlphaGeometry,
                GammaGeometry,
                (
                    "component-channel-b",
                    "component-fingerprint-b",
                ),
            ),
            CoordinatedSignals=frozenset(),
            AllowPortableSignalReuse=True,
        )

        self.assertIsNotNone(Plan)
        self.assertTrue(Plan.PortableAcrossPlacement)
        self.assertEqual(Plan.ReusedSignals, frozenset({"Alpha"}))
        self.assertEqual(Plan.GeneratedSignals, frozenset({"Gamma"}))

    def testRawPortalPortableReuseAcceptsExactPlanarRotation(
        self,
    ) -> None:
        CachedGeometry = (
            "Alpha",
            (1, 2, 3),
            ((1, 2, 3), (2, 2, 3)),
            (((4, 2, 3), ((4, 2, 3), (3, 2, 3))),),
        )
        Translation = (20, 0, 10)

        def Rotate(Position):
            return TransformPlanarRoutingPosition(
                Position,
                "Rotate90",
                Translation,
            )

        RequestedGeometry = (
            "Alpha",
            Rotate(CachedGeometry[1]),
            tuple(Rotate(Value) for Value in CachedGeometry[2]),
            tuple(
                (
                    Rotate(Target),
                    tuple(Rotate(Value) for Value in Path),
                )
                for Target, Path in CachedGeometry[3]
            ),
        )
        Cache = self.BuildRawPortalCache(
            object(),
            RoutingResources(
                RoutingStaticGeometry(frozenset(), frozenset())
            ),
            {"Alpha": 2},
            AccessGeometryFingerprint=(CachedGeometry,),
        )

        Plan = SelectRawPortalGeometryReusePlan(
            (Cache,),
            Placed=object(),
            Resources=RoutingResources(
                RoutingStaticGeometry(frozenset(), frozenset())
            ),
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts={"Alpha": 2},
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            AccessGeometryFingerprint=(RequestedGeometry,),
            CoordinatedSignals=frozenset(),
            AllowPortableSignalReuse=True,
        )

        self.assertIsNotNone(Plan)
        self.assertEqual(Plan.ReusedSignals, frozenset(("Alpha",)))
        self.assertEqual(
            Plan.SignalPlanarTransforms,
            (("Alpha", "Rotate90", Translation),),
        )

    def testPortablePortalCompletenessTransformsOnlyClosedSourceKeys(
        self,
    ) -> None:
        Translation = (20, 0, 10)
        ClosedSource = ("Alpha", (1, 2, 3), 0)
        OpenSource = ("Alpha", (4, 2, 3), 0)
        ExactSource = ("Alpha", (7, 2, 3), 0)
        OrdinarySource = ("Beta", (2, 2, 8), 0)

        def TransformKey(Key):
            return (
                Key[0],
                TransformPlanarRoutingPosition(
                    Key[1], "Rotate90", Translation
                ),
                Key[2],
            )

        ClosedCurrent = TransformKey(ClosedSource)
        OpenCurrent = TransformKey(OpenSource)
        ExactCurrent = TransformKey(ExactSource)
        OrdinaryCurrent = TransformKey(OrdinarySource)
        Result = TransformPortableCompletePortalDomainKeys(
            (ClosedSource, ExactSource, OrdinarySource),
            {
                "Alpha": ("Rotate90", Translation),
                "Beta": ("Rotate90", Translation),
            },
            (
                ClosedCurrent,
                OpenCurrent,
                ExactCurrent,
                OrdinaryCurrent,
            ),
            frozenset(((ExactCurrent[0], ExactCurrent[1]),)),
            frozenset(("Alpha",)),
        )

        self.assertEqual(Result, frozenset((OrdinaryCurrent,)))
        self.assertNotIn(ClosedCurrent, Result)
        self.assertNotIn(OpenCurrent, Result)
        self.assertNotIn(ExactCurrent, Result)

    def testPortableExactPlanSignalRebuildsChangedRequestProof(
        self,
    ) -> None:
        PositiveReusable = SelectPortablePortalPositiveReusableSignals(
            ("ExactSignal", "OrdinarySignal")
        )
        Reusable = SelectPortablePortalProofReusableSignals(
            ("ExactSignal", "OrdinarySignal"),
            ("ExactSignal",),
        )
        self.assertEqual(
            PositiveReusable,
            frozenset(("ExactSignal", "OrdinarySignal")),
        )
        self.assertEqual(Reusable, frozenset(("OrdinarySignal",)))

        Arguments = (
            "ExactSignal",
            4,
            1000,
            "guide-input",
            (0, 20, -4, 16),
        )
        SamePortalGeometryOldObstacle = ((
            (1, 2, 3),
            0,
            ((1, 2, 3),),
            ((4, 2, 3),),
            "allowed-obstacle-domain-a",
            2,
            4,
            1000,
        ),)
        SamePortalGeometryNewObstacle = ((
            (1, 2, 3),
            0,
            ((1, 2, 3),),
            ((4, 2, 3),),
            "allowed-obstacle-domain-b",
            2,
            4,
            1000,
        ),)
        OldFingerprint = BuildConfiguredPortalRequestDomainFingerprint(
            *Arguments,
            SamePortalGeometryOldObstacle,
        )
        NewFingerprint = BuildConfiguredPortalRequestDomainFingerprint(
            *Arguments,
            SamePortalGeometryNewObstacle,
        )

        self.assertNotEqual(OldFingerprint, NewFingerprint)
        self.assertNotIn("ExactSignal", Reusable)

    def testRawPortalResourceCacheRetentionIsBoundedAndNewestLast(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        First = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2},
        )
        Second = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 3},
        )
        Third = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 4},
        )

        for Cache in (First, Second, Third):
            RetainRawPortalGeometryCache(
                Resources,
                Cache,
                MaximumEntries=2,
            )
        self.assertEqual(
            Resources.RawPortalGeometryCaches,
            (Second, Third),
        )

        RetainRawPortalGeometryCache(
            Resources,
            Second,
            MaximumEntries=2,
        )
        self.assertEqual(
            Resources.RawPortalGeometryCaches,
            (Third, Second),
        )
        with self.assertRaises(ValueError):
            RetainRawPortalGeometryCache(
                Resources,
                First,
                MaximumEntries=0,
            )

    def testPreparedPortalDomainCacheKeepsLeaseStatesSeparate(self) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        RawCache = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2},
        )
        First = PreparedPortalDomainCache(
            RawPortalCache=RawCache,
            UnreservedPortalMode=False,
            ReservationVariant=0,
            PortalEntries=RawCache.PortalEntries,
            Reservations=(),
            SeedReservationPrepared=True,
        )
        Second = replace(First, ReservationVariant=1)
        RetainPreparedPortalDomainCache(Resources, First, MaximumEntries=2)
        RetainPreparedPortalDomainCache(Resources, Second, MaximumEntries=2)
        self.assertIs(
            SelectPreparedPortalDomainCache(
                Resources.PreparedPortalDomainCaches,
                RawCache,
                False,
                0,
            ),
            First,
        )
        self.assertIs(
            SelectPreparedPortalDomainCache(
                Resources.PreparedPortalDomainCaches,
                RawCache,
                False,
                1,
            ),
            Second,
        )
        self.assertIsNone(
            SelectPreparedPortalDomainCache(
                Resources.PreparedPortalDomainCaches,
                RawCache,
                True,
                0,
            )
        )
        RetainPreparedPortalDomainCache(Resources, First, MaximumEntries=1)
        self.assertEqual(Resources.PreparedPortalDomainCaches, (First,))
        with self.assertRaises(ValueError):
            RetainPreparedPortalDomainCache(Resources, First, MaximumEntries=0)

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

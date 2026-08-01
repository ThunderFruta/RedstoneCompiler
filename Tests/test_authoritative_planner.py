import unittest
from collections import Counter
from dataclasses import replace
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch

from Compiler.Placement.Pcb import PlacementAssignmentConstraintSet
from Compiler.Routing.AuthoritativePlanner import (
    BuildNegotiatedInitialColumns,
    BuildNegotiatedInitialTiles,
    BuildNegotiatedFallbackGuideColumns,
    BuildNegotiatedOffenderHaloEscalation,
    BuildNegotiatedRouteTreeState,
    BuildOptionalPortalSeedWorkCheck,
    BuildRepeaterReadyPortalDomains,
    BuildCandidateStarvationClassFingerprint,
    BuildCompleteMandatoryClaimCutCoverage,
    BuildInvariantRouteRequestGuidePayload,
    BuildInvariantRouteRequestNodePayload,
    BuildDetachedLocalClaimObstacleNodes,
    PartitionLocalClaimSeedComponents,
    BuildBoundedPortfolioPortalSliceAdvanceFailure,
    BuildMandatoryPortalTupleSelfConflictFailure,
    BuildRoutingConflictGraph,
    BuildClusterInterfaceAccessDomainFingerprint,
    BuildClusterInterfaceProblem,
    BuildClusterInterfaceReservationAssignmentFingerprint,
    BuildClusterLeaseSignalPatternFingerprint,
    BuildCapacityAwareGuideInputFingerprint,
    BuildTelemetryRoutingStageError,
    BuildUnavoidableMandatoryClaimCutFailure,
    CandidatePortalShapeRank,
    CandidatePortalTupleIndex,
    CandidateRequestWindowOffset,
    ClusterLeaseCandidateRealizabilityNogood,
    ClaimConflictPositions,
    ChooseRepeatedWorkTransition,
    CountPriorCandidateFailureFingerprint,
    CountPriorCandidateStarvationClassFingerprint,
    CountRoutedComponentGlobalNoTreeAttempts,
    CountExactLegalRetainedJointStates,
    CountJointAssignmentConstraintKinds,
    ExpandNegotiatedTiles,
    ExactAssignmentCompletionSignalOrderKey,
    FindAllUnavoidableMandatoryClaimCuts,
    FrozenComponentBlockedWireNodes,
    FindFirstUnavoidableCandidateDomainPairCut,
    FindPriorCandidateDomainPairExpansion,
    FindUnindexedClaimPositions,
    FindUnavoidableMandatoryClaimCut,
    FindNegotiatedBoundaryTouches,
    GenerateStagedInitialRouteTrees,
    GrowAssignmentExpansionLimit,
    HasRepeatedExactPairCut,
    HasCoveredPairCutAfterEndpointExpansion,
    MandatoryPortalTupleSelfConflictEvidence,
    MayAdvanceStagedCandidateOnExhaustion,
    MandatoryClaimsConflict,
    MergeSignalScopedAvoidancePositions,
    MergeSignalScopedRawPortalEntries,
    PlanNegotiatedRouteTrees,
    NegotiatedColumnsForTiles,
    OptionalPortalSeedSliceExpired,
    PortalTupleConflictsWithFrozenComponentClaims,
    RawPortalProfileMatchesRequestedControls,
    RawPortalGeometryCache,
    PreparedPortalDomainCache,
    RetainPreparedPortalDomainCache,
    RetainRawPortalGeometryCache,
    RequiredRoutingLayerCountForAccess,
    ReserveBoundaryPortals,
    ReserveClusterBoundaryLeases,
    ReserveNegotiatedBoundaryEscapes,
    RetainPartialAssignmentCandidateCache,
    RetainNegotiatedInitialCandidateOption,
    SelectEscalatedRoutingLayerCount,
    SelectExactAssignmentCompletionCutWideRequests,
    SelectPendingExactAssignmentCompletionRequestIndices,
    SelectExactAssignmentCompletionRequestBatch,
    SelectExactAssignmentCompletionReserveMilliseconds,
    ShouldContinueDistinctExactCutFrontier,
    ShouldBuildCapacityAwareGlobalGuidePlan,
    SelectComponentPreparationProfiles,
    ShouldDeferUnreservedCandidateRequestShape,
    ShouldRejectRoutedComponentForeignEscape,
    SelectCandidateRegenerationSignals,
    SelectCandidateRegenerationCoverSignals,
    SelectCandidateRealizabilityProbeSliceSeconds,
    ShouldContinueUniqueAccessDistinctCandidateRealizabilityProof,
    ShouldHandoffContinuedCandidateRealizabilityCut,
    SelectAnonymousMinimumFailurePairRelocationSignals,
    BuildAnonymousCandidateDomainFingerprint,
    SelectPriorityPlacementRelocationSignals,
    SelectCandidateDomainPairScanSliceSeconds,
    SelectConflictAvoidancePositions,
    SelectClusterLeaseOwnershipSignals,
    SelectCoordinatedCandidateExpansionLimit,
    SelectEffectiveCoordinatedCandidateDiversityLevel,
    SelectCoordinatedContinuationRequestWindowLimit,
    SelectCoordinatedInitialRequestWindowLimit,
    SelectPartialAssignmentAvoidancePositions,
    SelectPartialAssignmentBlockerSignals,
    SelectAuthoritativeBaseClaims,
    SelectAccessAwareLocalClaimReleases,
    SelectGraphAccessStarts,
    PortalPathRespectsOutwardAccess,
    SelectInitialRoutingLayerCount,
    SelectMaturePortfolioExactInitialRequestFloor,
    SelectMaturePortfolioPortalLimit,
    SelectNegotiatedExpandedRequestMinimumExpansionCount,
    SelectNegotiatedOffenderHaloLaneDiversityLevel,
    SelectCoordinatedPortalVariantCount,
    SelectOptionalPortalSeedSliceSeconds,
    SelectRawPortalGeometryReusePlan,
    SelectPreparedPortalDomainCache,
    SelectTransactionalLeasePrescreenSignals,
    TransformPlanarRoutingPosition,
    SelectJointHigherOrderConstraintSignals,
    SelectJointPairwiseConstraintSignals,
    ShouldFreezePartialAssignmentForExactCut,
    ShouldGrowAssignmentBudget,
    ShouldRegenerateNewExactConflictSignals,
    ShouldReleaseFrozenPartialAssignment,
    ShouldRetainUnaffectedCandidatesForControl,
    ShouldPrepareOptionalPortalSeed,
    ShouldPrepareMandatoryPortalTuples,
    ShouldLimitRetainedPortfolioPortalDomain,
    ShouldRetainBoundedPortfolioPortalProfile,
    ShouldCapMatureCumulativeJointPortfolio,
    ShouldStageTopologyPressureJointPortfolio,
    ShouldScanCandidateDomainPairCut,
    ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation,
    ShouldAdvanceTopologyCutEpochOnCandidateStarvation,
    ShouldAdvanceAfterCompleteClusterLeasePortfolio,
    ShouldDiversifyStarvedCompleteClusterLeaseEndpoint,
    ShouldContinueCutScopedFixedLegalityWindow,
    ShouldContinueSoleRetainedCutCandidateStarvation,
    ShouldExpandNegotiatedOffenderHalo,
    HasCumulativeJointAssignmentConstraintMaturity,
    ShouldRetryRelocatedCandidateStarvation,
    ShouldRetryCompleteClusterLeaseStateBeforePlacement,
    ShouldRefineCandidateRealizabilityLeaseNogood,
    ShouldRetryNegotiatedExactAssignment,
    ShouldUseNegotiatedRouting,
    ShouldUseMatureStagedInitialCandidateScheduler,
    ShouldRunShapeOptimization,
    _BuildTargetPortalBranches,
    _MaterializeCandidate,
    _ReserveRepeaters,
)
from Compiler.Routing.Actions import PropagateRoutePower
from Compiler.Routing.ChannelPlanner import NetRoutingProfile
from Compiler.Routing.Failures import (
    RoutingAssignmentCut,
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.Models import (
    ClusterInterfaceRealizabilityNogood,
    ClusterInterfaceStateProof,
    RoutingResources,
    RoutingStaticGeometry,
)
from Compiler.Routing.Policy import (
    DefaultPhysicalDesignPolicy,
    LocalFirstPhysicalDesignPolicy,
)
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

    def testFrozenComponentPortalTupleFilterIgnoresSameOwnerOnly(
        self,
    ) -> None:
        CandidateClaims = RoutingResourceClaims(
            WireCells=frozenset(((8, 2, 8),)),
            ElectricalCells=frozenset(((8, 2, 8),)),
        )
        SameOwner = LocalRouteClaim(
            ClusterId=-1,
            Signal="Owned",
            Root=(8, 2, 8),
            ConnectedTargets=((8, 2, 8),),
            Nodes=frozenset(((8, 2, 8),)),
            Edges=frozenset(),
            BoundaryNodes=((8, 2, 8),),
            Claims=CandidateClaims,
        )
        ForeignOwner = LocalRouteClaim(
            ClusterId=-1,
            Signal="Foreign",
            Root=(9, 2, 8),
            ConnectedTargets=((9, 2, 8),),
            Nodes=frozenset(((9, 2, 8),)),
            Edges=frozenset(),
            BoundaryNodes=((9, 2, 8),),
            Claims=RoutingResourceClaims(
                WireCells=frozenset(((9, 2, 8),)),
                ElectricalCells=frozenset(((8, 2, 8),)),
            ),
        )

        self.assertEqual(
            PortalTupleConflictsWithFrozenComponentClaims(
                "Owned",
                CandidateClaims,
                (SameOwner, ForeignOwner),
            ),
            ("Foreign",),
        )
        self.assertEqual(
            PortalTupleConflictsWithFrozenComponentClaims(
                "Owned",
                CandidateClaims,
                (SameOwner,),
            ),
            (),
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

    def testCoordinatedPortalVariantsStayWithinExistingDemandCap(
        self,
    ) -> None:
        self.assertEqual(
            SelectCoordinatedPortalVariantCount(2, 13, True),
            6,
        )
        self.assertEqual(
            SelectCoordinatedPortalVariantCount(2, 4, True),
            4,
        )
        self.assertEqual(
            SelectCoordinatedPortalVariantCount(2, 13, False),
            2,
        )
        self.assertEqual(
            SelectCoordinatedPortalVariantCount(8, 13, True),
            8,
        )

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

    def testMaturePortfolioPortalLimitCapPreservesSmallerDomains(
        self,
    ) -> None:
        self.assertEqual(
            SelectMaturePortfolioPortalLimit(13, True),
            6,
        )
        self.assertEqual(
            SelectMaturePortfolioPortalLimit(6, True),
            6,
        )
        self.assertEqual(
            SelectMaturePortfolioPortalLimit(4, True),
            4,
        )
        self.assertEqual(
            SelectMaturePortfolioPortalLimit(13, False),
            13,
        )
        with self.assertRaises(ValueError):
            SelectMaturePortfolioPortalLimit(0, True)
        with self.assertRaises(ValueError):
            SelectMaturePortfolioPortalLimit(
                13,
                True,
                MaximumMaturePortfolioPortalLimit=0,
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

    def testCompleteEmptyPortalTupleDomainIsExactRenameIndependentCut(
        self,
    ) -> None:
        First = BuildMandatoryPortalTupleSelfConflictFailure(
            (
                MandatoryPortalTupleSelfConflictEvidence(
                    Signal="GeneratedNet26",
                    CompletePortalTupleCount=1,
                    EvaluatedPortalTupleCount=1,
                    TerminalPortalDomainCounts=(1, 1, 1),
                    ConflictResources=(
                        RoutingResourceId(
                            RoutingResourceKind.Support,
                            (20, 4, 31),
                        ),
                        RoutingResourceId(
                            RoutingResourceKind.Air,
                            (21, 5, 31),
                        ),
                    ),
                ),
            ),
            StageTimings={"MandatoryPortalClaimPreScreen": 0.01},
        )
        Renamed = BuildMandatoryPortalTupleSelfConflictFailure(
            (
                MandatoryPortalTupleSelfConflictEvidence(
                    Signal="RenamedEndpoint",
                    CompletePortalTupleCount=1,
                    EvaluatedPortalTupleCount=1,
                    TerminalPortalDomainCounts=(1, 1, 1),
                    ConflictResources=(
                        RoutingResourceId(
                            RoutingResourceKind.Support,
                            (120, 14, 231),
                        ),
                        RoutingResourceId(
                            RoutingResourceKind.Air,
                            (121, 15, 231),
                        ),
                    ),
                ),
            ),
        )

        self.assertEqual(
            First.Reason,
            RoutingFailureReason.NoPinAccessPattern,
        )
        self.assertEqual(First.Stage, "InitialCandidateAssignment")
        self.assertEqual(
            First.Diagnostics["ConflictGraph"]["Classification"],
            "mandatory-access-self-conflict",
        )
        self.assertEqual(
            First.Diagnostics["MandatoryAccessProof"][
                "ConflictFingerprint"
            ],
            Renamed.Diagnostics["MandatoryAccessProof"][
                "ConflictFingerprint"
            ],
        )
        self.assertEqual(
            First.Diagnostics["ConflictFingerprint"],
            Renamed.Diagnostics["ConflictFingerprint"],
        )
        Aggregated = BuildMandatoryPortalTupleSelfConflictFailure((
            MandatoryPortalTupleSelfConflictEvidence(
                Signal="Second",
                CompletePortalTupleCount=1,
                EvaluatedPortalTupleCount=1,
                TerminalPortalDomainCounts=(1, 1),
                ConflictResources=(),
            ),
            MandatoryPortalTupleSelfConflictEvidence(
                Signal="First",
                CompletePortalTupleCount=1,
                EvaluatedPortalTupleCount=1,
                TerminalPortalDomainCounts=(1, 1, 1),
                ConflictResources=(),
            ),
        ))
        self.assertEqual(
            Aggregated.AffectedNets,
            ("First", "Second"),
        )
        self.assertEqual(
            Aggregated.Diagnostics["MandatoryAccessProof"][
                "SignalCount"
            ],
            2,
        )
        self.assertEqual(
            Aggregated.Diagnostics["ConflictGraph"][
                "CandidateCounts"
            ],
            {"First": 0, "Second": 0},
        )
        with self.assertRaises(ValueError):
            MandatoryPortalTupleSelfConflictEvidence(
                Signal="Incomplete",
                CompletePortalTupleCount=2,
                EvaluatedPortalTupleCount=1,
                TerminalPortalDomainCounts=(1, 2),
                ConflictResources=(),
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

    def testOptionalPortalSeedSliceIsCappedAndRemainingAware(
        self,
    ) -> None:
        self.assertEqual(
            SelectOptionalPortalSeedSliceSeconds(8.0),
            0.5,
        )
        self.assertEqual(
            SelectOptionalPortalSeedSliceSeconds(1.0),
            0.25,
        )
        self.assertEqual(
            SelectOptionalPortalSeedSliceSeconds(0.0),
            0.0,
        )
        with self.assertRaises(ValueError):
            SelectOptionalPortalSeedSliceSeconds(
                8.0,
                MaximumSliceSeconds=0.0,
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

    def testOptionalPortalSeedLocalExpiryPreservesSharedCheck(
        self,
    ) -> None:
        SharedChecks = []
        with patch(
            "Compiler.Routing.AuthoritativePlanner.monotonic",
            return_value=10.0,
        ):
            WorkCheck = BuildOptionalPortalSeedWorkCheck(
                9.5,
                lambda Details: SharedChecks.append(dict(Details)),
            )
            with self.assertRaises(OptionalPortalSeedSliceExpired):
                WorkCheck({"Phase": "matching", "Signal": "RenamedSignal"})

        self.assertEqual(
            SharedChecks,
            [{"Phase": "matching", "Signal": "RenamedSignal"}],
        )

    def testOptionalPortalSeedSelectionIsNameIndependent(
        self,
    ) -> None:
        OriginalSignals = [f"Signal{Index}" for Index in range(9)]
        RenamedSignals = [
            f"Arbitrary{Index * 17}"
            for Index in reversed(range(9))
        ]

        self.assertEqual(
            ShouldPrepareOptionalPortalSeed(
                True,
                OriginalSignals,
                False,
            ),
            ShouldPrepareOptionalPortalSeed(
                True,
                RenamedSignals,
                False,
            ),
        )
        self.assertTrue(
            ShouldPrepareOptionalPortalSeed(
                True,
                OriginalSignals,
                False,
            )
        )
        self.assertFalse(
            ShouldPrepareOptionalPortalSeed(
                False,
                OriginalSignals,
                False,
            )
        )
        self.assertFalse(
            ShouldPrepareOptionalPortalSeed(
                True,
                OriginalSignals,
                True,
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

    def testPortalCoverageRegenerationTargetsOnlyReportedEndpoints(
        self,
    ) -> None:
        self.assertFalse(
            ShouldFreezePartialAssignmentForExactCut(
                "portal-coverage-pair-conflict",
                43,
                45,
                True,
            )
        )
        self.assertTrue(
            ShouldFreezePartialAssignmentForExactCut(
                "relocated-higher-order-conflict",
                43,
                45,
                True,
            )
        )
        self.assertTrue(
            ShouldFreezePartialAssignmentForExactCut(
                "relocated-higher-order-conflict",
                1,
                45,
                False,
            )
        )

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

    def testCutOnlyCandidateControlsRetainUnaffectedDomains(self) -> None:
        for Action in (
            "regenerate-affected-candidates",
            "increase-guide-lane-diversity",
            "add-routing-layer",
            "alternate-complete-cluster-interface-lease",
            "exclude-candidate-unrealizable-cluster-lease-template",
        ):
            with self.subTest(Action=Action):
                self.assertTrue(
                    ShouldRetainUnaffectedCandidatesForControl(Action)
                )
        for Action in (
            "alternate-portal-slots",
            "diversify-repeated-candidate-cut",
            "try-bounded-unreserved-portals",
            "final-bounded-unreserved-portals",
            "replace-complete-candidate-domain",
        ):
            with self.subTest(Action=Action):
                self.assertFalse(
                    ShouldRetainUnaffectedCandidatesForControl(Action)
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

    def testMandatoryPortalFailurePreservesEveryExactPairCut(self) -> None:
        Failure = BuildUnavoidableMandatoryClaimCutFailure(
            (
                (("B", "C"), frozenset({(2, 1, 0)})),
                (("A", "B"), frozenset({(1, 1, 0)})),
            ),
            {"PortalGeneration": 1.25},
        )

        self.assertEqual(Failure.AffectedNets, ("A", "B", "C"))
        self.assertEqual(
            Failure.Diagnostics["MandatoryConflictPairCount"],
            2,
        )
        self.assertEqual(
            Failure.Diagnostics["MandatoryConflictPositionCount"],
            2,
        )
        self.assertEqual(
            Failure.Diagnostics["StageTimingsSeconds"],
            {"PortalGeneration": 1.25},
        )
        self.assertEqual(
            Failure.Diagnostics["MandatoryAccessProof"]["Kind"],
            "generated-fixed-portal-domain-exhausted",
        )
        self.assertTrue(
            Failure.Diagnostics["MandatoryAccessProof"]["Complete"]
        )
        self.assertEqual(
            Failure.Diagnostics["ConflictGraph"][
                "PairwiseIncompatibleEdges"
            ],
            [["A", "B"], ["B", "C"]],
        )
        Cut = RoutingAssignmentCut.FromFailure(Failure)
        self.assertIsNotNone(Cut)
        self.assertEqual(
            Cut.PairwiseConflictEdges,
            (("A", "B"), ("B", "C")),
        )
        Constraints = PlacementAssignmentConstraintSet().WithCut(Cut)
        self.assertEqual(
            Constraints.PairwiseConflictEdges,
            (("A", "B"), ("B", "C")),
        )

    def testMandatoryPortalFailureBatchIsOrderIndependent(self) -> None:
        First = BuildUnavoidableMandatoryClaimCutFailure((
            (("Left", "Center"), frozenset({(1, 1, 0)})),
            (("Center", "Right"), frozenset({(2, 1, 0)})),
        ))
        Reordered = BuildUnavoidableMandatoryClaimCutFailure((
            (("Right", "Center"), frozenset({(2, 1, 0)})),
            (("Center", "Left"), frozenset({(1, 1, 0)})),
        ))

        self.assertEqual(
            First.Diagnostics["ConflictGraph"],
            Reordered.Diagnostics["ConflictGraph"],
        )
        self.assertEqual(First.AffectedNets, Reordered.AffectedNets)
        self.assertEqual(First.Locations, Reordered.Locations)
        Translated = BuildUnavoidableMandatoryClaimCutFailure((
            (("RenamedA", "RenamedB"), frozenset({(101, 8, 40)})),
            (("RenamedB", "RenamedC"), frozenset({(102, 8, 40)})),
        ))
        self.assertEqual(
            First.Diagnostics["MandatoryAccessProof"][
                "ConflictFingerprint"
            ],
            Translated.Diagnostics["MandatoryAccessProof"][
                "ConflictFingerprint"
            ],
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

    def BuildPortal(self, Signal, Terminal, Position, Layer=0):
        Claims = RoutingResourceClaims(WireCells=frozenset((Position,)))
        return PinAccessPortal(
            PortalId=f"{Signal}:{Position}", Signal=Signal, Terminal=Terminal,
            Layer=Layer, Path=(Position,), Edges=frozenset(), Claims=Claims,
            Length=0, BendCount=0, ViaCount=0, Cost=0,
        )

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

    def BuildRawPortalCache(
        self,
        Placed,
        Resources,
        VariantCounts,
        *,
        Region=None,
        LayerCount=2,
        AccessGeometryFingerprint=("access",),
        GuidePlan=None,
        GuidePlanPrepared=True,
    ):
        EffectiveRegion = Region if Region is not None else object()
        EffectiveGuidePlan = (
            GuidePlan if GuidePlan is not None else object()
        )
        Entries = []
        for Index, Signal in enumerate(sorted(VariantCounts)):
            Terminal = (Index, 1, 0)
            Entries.append((
                (Signal, Terminal, 0),
                (self.BuildPortal(Signal, Terminal, Terminal),),
            ))
        return RawPortalGeometryCache(
            PlacedIdentity=id(Placed),
            ResourcesIdentity=id(Resources),
            Region=EffectiveRegion,
            LayerCount=LayerCount,
            PortalLimit=6,
            PortalVariantCounts=tuple(sorted(VariantCounts.items())),
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            Context=object(),
            AssignmentIndexed=IndexedRoutingResourceGraph(
                ResourcePositions=(),
                PositionIndices={},
            ),
            PortalEntries=tuple(Entries),
            RequestCount=len(Entries),
            TargetCount=sum(VariantCounts.values()),
            StarvationCount=0,
            AccessGeometryFingerprint=AccessGeometryFingerprint,
            GuidePlanPrepared=GuidePlanPrepared,
            GuidePlan=EffectiveGuidePlan,
            SignalRequestCounts=tuple(
                (Signal, 1) for Signal in sorted(VariantCounts)
            ),
            SignalTargetCounts=tuple(sorted(VariantCounts.items())),
            SignalStarvationCounts=tuple(
                (Signal, 0) for Signal in sorted(VariantCounts)
            ),
        )

    def BuildCandidate(self, Signal, CandidateId, Position):
        Claims = RoutingResourceClaims(WireCells=frozenset((Position,)))
        return NetRouteCandidate(
            CandidateId=CandidateId, Signal=Signal, SourcePortalId="source",
            TargetPortalIds={}, Nodes=frozenset((Position,)), Edges=frozenset(),
            Claims=Claims, Layer=0, Guide=frozenset(), RepeaterWaypoints=(),
            MaterialCost=1, FootprintGrowth=1, Length=1, BendCount=0, ViaCount=0,
        )

    def BuildLocalClaim(self, Signal, Position, NodeCount=1):
        Nodes = frozenset(
            (Position[0] + Index, Position[1], Position[2])
            for Index in range(NodeCount)
        )
        return LocalRouteClaim(
            Signal=Signal,
            ClusterId=0,
            Root=Position,
            ConnectedTargets=(),
            BoundaryNodes=(Position,),
            Nodes=Nodes,
            Edges=frozenset(),
            Claims=RoutingResourceClaims(WireCells=Nodes),
        )

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
        self.assertFalse(
            ShouldDeferUnreservedCandidateRequestShape(
                **{
                    **Arguments,
                    "UnreservedPortalMode": False,
                },
                CompleteCoordinatedSignalWindow=False,
            )
        )

    def testCoordinatedReservedPortalTupleUsesTargetedWindowOffset(
        self,
    ) -> None:
        self.assertEqual(
            CandidatePortalTupleIndex(
                Variant=0,
                PortalPhase=1,
                PortalTupleCount=6,
            ),
            1,
        )
        self.assertEqual(
            CandidatePortalTupleIndex(
                Variant=0,
                PortalPhase=1,
                PortalTupleCount=6,
                CoordinatedRequestWindowOffset=1,
            ),
            2,
        )
        self.assertEqual(
            CandidatePortalTupleIndex(
                Variant=5,
                PortalPhase=3,
                PortalTupleCount=6,
                CoordinatedRequestWindowOffset=1,
            ),
            3,
        )
        with self.assertRaises(ValueError):
            CandidatePortalTupleIndex(0, 0, 0)
        with self.assertRaises(ValueError):
            CandidatePortalTupleIndex(0, 0, 1, -1)

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

    def testClusterInterfaceJointCutRecoversPortalOmittedByPatternBeam(
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
        FirstTerminal = (100, 1, 0)
        SecondTerminal = (200, 1, 0)
        BlockedPositions = tuple(
            (Index, 1, 0) for Index in range(6)
        )
        SafePosition = (9, 1, 0)
        FirstPortals = tuple(
            self.BuildPortal(
                "First",
                FirstTerminal,
                Position,
            )
            for Position in (*BlockedPositions, SafePosition)
        )
        SecondPortal = PinAccessPortal(
            PortalId="Second:blocker",
            Signal="Second",
            Terminal=SecondTerminal,
            Layer=0,
            Path=BlockedPositions,
            Edges=frozenset(),
            Claims=RoutingResourceClaims(
                WireCells=frozenset(BlockedPositions)
            ),
            Length=len(BlockedPositions),
            BendCount=0,
            ViaCount=0,
            Cost=0,
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
                ("First", FirstTerminal, 0): FirstPortals,
                ("Second", SecondTerminal, 0): (SecondPortal,),
            },
            {
                "First": Profile("First", FirstTerminal),
                "Second": Profile("Second", SecondTerminal),
            },
            Resources,
            MaximumExpansions=1_000,
        )

        self.assertEqual(
            next(
                Reservation.FirstSegment
                for Reservation in Reservations
                if Reservation.Signal == "First"
            ),
            (SafePosition,),
        )

    def testClusterInterfaceJointCutRecoversHigherOrderOmittedPortal(
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
        Signals = ("First", "Second", "Third")
        Terminals = {
            Signal: (100 + 10 * Index, 1, 0)
            for Index, Signal in enumerate(Signals)
        }
        Colors = ((0, 1, 0), (4, 1, 0), (8, 1, 0))

        def Portal(Signal, Terminal, Index, Position):
            Claims = RoutingResourceClaims(
                WireCells=frozenset((Position,))
            )
            return PinAccessPortal(
                PortalId=f"{Signal}:{Index}",
                Signal=Signal,
                Terminal=Terminal,
                Layer=0,
                Path=(Position,),
                Edges=frozenset(),
                Claims=Claims,
                Length=0,
                BendCount=0,
                ViaCount=0,
                Cost=0,
            )

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

        _Reserved, Reservations = ReserveClusterBoundaryLeases(
            {
                (Signal, Terminals[Signal], 0): tuple(
                    Portal(
                        Signal,
                        Terminals[Signal],
                        Index,
                        Colors[Index % 2] if Index < 6 else Colors[2],
                    )
                    for Index in range(7)
                )
                for Signal in Signals
            },
            {Signal: Profile(Signal) for Signal in Signals},
            Resources,
            MaximumExpansions=2_000,
        )

        self.assertEqual(
            {
                Reservation.FirstSegment
                for Reservation in Reservations
            },
            {(Color,) for Color in Colors},
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

    def testRawPortalProfileCannotOverrideWiderRetryControls(self) -> None:
        Cache = self.BuildRawPortalCache(
            object(),
            object(),
            {"Alpha": 2, "Beta": 2},
        )

        self.assertTrue(
            RawPortalProfileMatchesRequestedControls(
                Cache,
                6,
                {"Alpha": 2, "Beta": 2},
            )
        )
        self.assertFalse(
            RawPortalProfileMatchesRequestedControls(
                Cache,
                10,
                {"Alpha": 10, "Beta": 10},
            )
        )
        self.assertFalse(
            RawPortalProfileMatchesRequestedControls(
                Cache,
                6,
                {"Alpha": 2, "Beta": 6},
            )
        )
        self.assertFalse(
            RawPortalProfileMatchesRequestedControls(
                None,
                6,
                {"Alpha": 2, "Beta": 2},
            )
        )

    def testRawPortalCacheReusesGuidePlanOnlyForSameGeometryAndLayer(
        self,
    ) -> None:
        Placed = object()
        Resources = object()
        GuidePlan = object()
        Cache = RawPortalGeometryCache(
            PlacedIdentity=id(Placed),
            ResourcesIdentity=id(Resources),
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

    def testRawPortalPartialComponentDomainExpandsAtGlobalHandoff(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        AlphaGeometry = ("Alpha", (0, 1, 0), (), ())
        BetaGeometry = ("Beta", (3, 1, 0), (), ())
        Cache = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2},
            AccessGeometryFingerprint=(
                AlphaGeometry,
                BetaGeometry,
            ),
        )

        Plan = SelectRawPortalGeometryReusePlan(
            (Cache,),
            Placed=Placed,
            Resources=Resources,
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts={"Alpha": 2, "Beta": 2},
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            AccessGeometryFingerprint=(
                AlphaGeometry,
                BetaGeometry,
            ),
            CoordinatedSignals=frozenset(),
        )

        self.assertIsNotNone(Plan)
        self.assertFalse(Plan.ExactMatch)
        self.assertEqual(Plan.ReusedSignals, frozenset({"Alpha"}))
        self.assertEqual(Plan.GeneratedSignals, frozenset({"Beta"}))

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
                    RoutingStaticGeometry(frozenset(), frozenset())
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

    def testSignalScopedPortalMergeEqualsCompleteRegeneration(
        self,
    ) -> None:
        AlphaTerminal = (0, 1, 0)
        BetaTerminal = (1, 1, 0)
        CachedAlpha = self.BuildPortal(
            "Alpha",
            AlphaTerminal,
            AlphaTerminal,
        )
        CachedBeta = self.BuildPortal(
            "Beta",
            BetaTerminal,
            BetaTerminal,
        )
        GeneratedBeta = self.BuildPortal(
            "Beta",
            BetaTerminal,
            (2, 1, 0),
        )
        CachedEntries = (
            (("Alpha", AlphaTerminal, 0), (CachedAlpha,)),
            (("Beta", BetaTerminal, 0), (CachedBeta,)),
        )
        GeneratedEntries = (
            (("Beta", BetaTerminal, 0), (GeneratedBeta,)),
        )

        Merged = MergeSignalScopedRawPortalEntries(
            CachedEntries,
            GeneratedEntries,
            frozenset({"Beta"}),
        )

        self.assertEqual(Merged, (
            (("Alpha", AlphaTerminal, 0), (CachedAlpha,)),
            (("Beta", BetaTerminal, 0), (GeneratedBeta,)),
        ))
        self.assertEqual(CachedEntries[1][1], (CachedBeta,))
        with self.assertRaises(ValueError):
            MergeSignalScopedRawPortalEntries(
                CachedEntries,
                GeneratedEntries,
                frozenset({"Alpha"}),
            )

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
            "Compiler.Routing.AuthoritativePlanner._ReserveRepeaters",
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
            "Compiler.Routing.AuthoritativePlanner._ReserveRepeaters",
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

    def testPortalStartsRemainAnchoredToGraphAccessAndReachRoutingLayer(self) -> None:
        Graph, Region, Context = self.BuildGraph()
        AccessPath = ((-1, 1, 0), (0, 1, 0))
        Starts = SelectGraphAccessStarts(AccessPath, Region.Nodes)
        RoutingTarget = (1, 2, 0)

        self.assertEqual(Starts, ((0, 1, 0),))
        self.assertNotIn((4, 2, 0), Starts)
        self.assertEqual(
            SelectGraphAccessStarts(
                (
                    (0, 1, 0),
                    (1, 1, 0),
                    (2, 1, 0),
                ),
                frozenset({
                    (0, 1, 0),
                    (1, 1, 0),
                    (2, 1, 0),
                }),
                PreferOutermost=True,
            ),
            ((2, 1, 0),),
        )
        AccessPath = (
            (0, 1, 0),
            (0, 1, -1),
            (0, 1, -2),
        )
        self.assertTrue(
            PortalPathRespectsOutwardAccess(
                ((0, 1, 0), (0, 2, -1)),
                AccessPath,
            )
        )
        self.assertFalse(
            PortalPathRespectsOutwardAccess(
                ((0, 1, 0), (-1, 2, 0)),
                AccessPath,
            )
        )
        self.assertTrue(
            PortalPathRespectsOutwardAccess(
                ((0, 1, -1), (0, 2, -2)),
                AccessPath,
            )
        )

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
            Reservation.Position: Reservation.Facing
            for Reservation in Reservations
        }
        Powers = PropagateRoutePower(Trunk[0], Graph, Repeaters)

        self.assertNotIn((13, 1, 0), Repeaters)
        self.assertTrue(all(Powers.get(Target, 0) > 0 for Target in Targets))


class GlobalGuideStageBoundaryTests(unittest.TestCase):
    def testCompleteComponentPreparationDefersGlobalGuidePlanning(
        self,
    ) -> None:
        self.assertFalse(ShouldBuildCapacityAwareGlobalGuidePlan(
            Enabled=True,
            PrepareComponentRoutingProblemOnly=True,
            RequireCompleteClusterInterfaceDomain=True,
            HasInterClusterRoutingChannel=True,
        ))
        for ComponentOnly, Complete, HasChannel in (
            (False, True, True),
            (True, False, True),
            (True, True, False),
        ):
            self.assertTrue(ShouldBuildCapacityAwareGlobalGuidePlan(
                Enabled=True,
                PrepareComponentRoutingProblemOnly=ComponentOnly,
                RequireCompleteClusterInterfaceDomain=Complete,
                HasInterClusterRoutingChannel=HasChannel,
            ))
        self.assertFalse(ShouldBuildCapacityAwareGlobalGuidePlan(
            Enabled=False,
            PrepareComponentRoutingProblemOnly=False,
            RequireCompleteClusterInterfaceDomain=False,
            HasInterClusterRoutingChannel=False,
        ))

    def testComponentPreparationProfilesUsePhysicalInteractionEnvelope(
        self,
    ) -> None:
        def Profile(X: int, Z: int) -> SimpleNamespace:
            Terminal = (X, 1, Z)
            return SimpleNamespace(
                Root=Terminal,
                Targets=(),
                SourceAccessPath=(Terminal,),
                TargetAccessPaths={},
            )

        Profiles = {
            "Owned": Profile(10, 10),
            "NearForeign": Profile(17, 10),
            "FarForeign": Profile(40, 40),
        }
        Channel = SimpleNamespace(Lanes=(
            SimpleNamespace(Cells=((10, 7, 10), (11, 7, 10))),
        ))

        Selected = SelectComponentPreparationProfiles(
            Profiles,
            frozenset(("Owned",)),
            Channel,
            (),
            GuideExpansion=3,
            TrackPitch=3,
        )

        self.assertEqual(
            set(Selected),
            {"Owned", "NearForeign"},
        )
        Renamed = SelectComponentPreparationProfiles(
            {
                "C": Profiles["Owned"],
                "P": Profiles["NearForeign"],
                "Q": Profiles["FarForeign"],
            },
            frozenset(("C",)),
            Channel,
            (),
            GuideExpansion=3,
            TrackPitch=3,
        )
        self.assertEqual(set(Renamed), {"C", "P"})

if __name__ == "__main__":
    unittest.main()

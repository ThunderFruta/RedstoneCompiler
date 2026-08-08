import unittest
from collections import Counter, deque
from dataclasses import replace
from random import Random
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch

import Compiler.Routing.AuthoritativePlanner as AuthoritativePlanner
from Compiler.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from Compiler.Placement.AccessFabric import (
    AttachPlacementAccessFabric,
    BuildPlacementAccessFabric,
)
from Compiler.Placement.Pcb import (
    PlacementAssignmentConstraintSet,
    PlacePcbGraph,
)
from Compiler.Routing.AuthoritativePlanner import (
    BuildNegotiatedInitialColumns,
    BuildConfiguredPortalRequestDomainFingerprint,
    BuildExactPhysicalPortalCertificateIdentityConditions,
    BuildPhysicalExteriorResourceGraphFingerprint,
    BuildFrozenPostClosurePortalHandoffTelemetry,
    ValidateFrozenPhysicalComponentPostClosurePortalHandoff,
    BeginPhysicalAssignmentArcPass,
    BuildNegotiatedInitialTiles,
    BuildNegotiatedFallbackGuideColumns,
    BuildNegotiatedOffenderHaloEscalation,
    BuildNegotiatedRouteTreeState,
    BuildOptionalPortalSeedWorkCheck,
    BuildPinnedOrdinaryPortalReuseColumns,
    BuildRepeaterReadyPortalDomains,
    BuildCandidateStarvationClassFingerprint,
    ClassifySiblingApertureSeamOwnershipConflicts,
    BuildCandidateRequestGeometryIdentity,
    BuildPhysicalCandidateRequestShapeDependencyIdentity,
    BuildCompleteMandatoryClaimCutCoverage,
    BuildInvariantRouteRequestGuidePayload,
    BuildInvariantRouteRequestNodePayload,
    BuildForeignElectricalExclusionsBySignal,
    BuildDetachedLocalClaimObstacleNodes,
    PartitionLocalClaimSeedComponents,
    PortalTupleFeasibilityDomainIsComplete,
    PortalTupleEmptyProofDomainIsComplete,
    ReadPortalBatchCandidatesAndCompletionMask,
    SelectCompletedPortalBatchEntries,
    MergePartialRawPortalBatchWork,
    MergePostClosurePortalCompletionKeys,
    MergePhysicalSignalRouteDomainDescriptorProgress,
    RetainPhysicalSignalRouteDomainDescriptorProgress,
    SelectPendingPhysicalRouteDescriptorRows,
    SelectMatchingPartialPortalReplaySignals,
    BuildBoundedPortfolioPortalSliceAdvanceFailure,
    BuildMandatoryPortalTupleSelfConflictFailure,
    BuildPhysicalBoundaryMandatoryPortalFactorDomains,
    ExactPortalConstraintAssignmentSatisfiesFactors,
    ExactPortalConstraintChoice,
    ExactPortalConstraintVariableDomain,
    ExtractExactPortalConstraintFactors,
    ExtractSparseExactPortalConstraintFactors,
    GetMandatoryPortalPairFeasibilityCertificate,
    CompilePhysicalBoundaryMandatoryPortalPairRelation,
    ProjectExactPortalConstraintFactors,
    GetPhysicalGlobalAssignmentArcIndex,
    IncrementalPhysicalCandidateArcIndex,
    SelectCertifiedMandatoryPortalPairCuts,
    SolveMandatoryPortalPairFeasibility,
    PhysicalBoundaryMandatoryPortalFactorDomain,
    BuildRoutingConflictGraph,
    BuildClusterInterfaceAccessDomainFingerprint,
    BuildClusterInterfaceProblem,
    BuildClusterInterfaceReservationAssignmentFingerprint,
    BuildClusterLeaseSignalPatternFingerprint,
    BuildCapacityAwareGuideInputFingerprint,
    BuildPhysicalAssemblyGuideContractFingerprint,
    BuildFactorizedPhysicalGuideIdentity,
    BuildCertifiedPhysicalComponentApertureDomain,
    BuildPhysicalSignalApertureCandidateDomainIdentity,
    BuildMinimalPhysicalRequestApertureNoGood,
    BuildCompletePhysicalRequestAlternativeApertureNoGoods,
    CompletePhysicalCandidatePairDomainsHaveNoSupport,
    PhysicalSignalLocalCandidateRequestFactorProofComplete,
    FilterPhysicalCandidatesAgainstSiblingApertures,
    PhysicalSignalRouteDomainContinuation,
    PhysicalSignalRouteDomainIsCertifiedEmpty,
    BuildPortablePhysicalSignalRouteDomainIdentity,
    PreparePortablePhysicalSignalRouteDomain,
    SelectPreparedPortablePhysicalSignalRouteDomainContinuation,
    RetainCompletePortablePhysicalSignalRouteDomains,
    SelectPortablePhysicalSignalRouteDomainContinuation,
    RetainPortablePhysicalSignalRouteDomainContinuation,
    SelectPortableReplayTelemetryReason,
    SelectReplayablePhysicalSignalRouteDomainContinuation,
    RetainCompletePhysicalSignalRouteDomainContinuations,
    BuildPhysicalPortCorridorArcSupportIndex,
    BuildPhysicalPortCorridorDomain,
    BuildPhysicalPortNoGoodKeys,
    BuildPhysicalLocalPortPairUnsupportedIndex,
    BuildPhysicalPortApertureContractFingerprint,
    CaptureCompletePhysicalPortCorridorDomains,
    BuildPreparedPhysicalExteriorGuideColumnsBySignal,
    BuildPhysicalExteriorConnectorDistanceField,
    FrozenPhysicalExteriorConnectorSearchRequest,
    BuildPhysicalPortCorridorFactor,
    BuildPhysicalGlobalPlanContinuationState,
    BuildPhysicalGlobalPlanYieldDeadline,
    BuildPhysicalComponentGlobalPortalId,
    BuildTelemetryRoutingStageError,
    BuildUnavoidableMandatoryClaimCutFailure,
    CandidatePortalShapeRank,
    CandidatePortalTupleIndex,
    CandidateRequestShapeDescriptor,
    CandidateRequestWindowOffset,
    ClusterLeaseCandidateRealizabilityNogood,
    ClaimConflictPositions,
    ChooseRepeatedWorkTransition,
    CountPriorCandidateFailureFingerprint,
    CountPriorCandidateRequestDomainFingerprint,
    CountPriorCandidateStarvationClassFingerprint,
    CountRoutedComponentGlobalNoTreeAttempts,
    CountExactLegalRetainedJointStates,
    CountJointAssignmentConstraintKinds,
    ExpandNegotiatedTiles,
    ExactAssignmentCompletionSignalOrderKey,
    ExtendIndexedRoutingResourceGraph,
    FindAllUnavoidableMandatoryClaimCuts,
    FrozenComponentBlockedWireNodes,
    ImmutableRoutingClaimsBlockedWireNodes,
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
    IsPhysicalCandidateRequestDomainComplete,
    PhysicalGlobalAssignmentDomainIsComplete,
    BuildSeamOnlyPhysicalComponentPortReservation,
    PhysicalPortPathsOwnExclusiveSeam,
    PhysicalRouteRequestFactorHasNecessaryConnectivity,
    ConflictClassificationSupportsPhysicalPortPairNoGoods,
    PlanPhysicalGlobalAssignmentAvoidingExactNoGoods,
    PropagatePhysicalPortCorridorArcConsistency,
    SelectReusablePhysicalPortCorridorCandidates,
    RetainIncompletePhysicalGlobalPlan,
    SelectNextRetainedPhysicalGlobalPlan,
    ShouldScheduleRetainedPhysicalGlobalPlan,
    SelectExactNoGoodCspBranch,
    OrderPhysicalPortOptionsByPreferences,
    DecomposePhysicalPortLaneFactors,
    PreparePhysicalSignalLocalFactorDomain,
    MaterializeSupportedPhysicalPortReservation,
    MaterializePhysicalPortFactorPair,
    FilterPhysicalCandidatesToCurrentPortalDomain,
    ClassifyEmptyPhysicalCandidateDomains,
    ApplyPhysicalComponentAssemblyPortalDomains,
    ApplyPlacementAccessFabricPortalDomains,
    SelectGenericPortalTerminalPaths,
    GetPersistentPhysicalComponentPortCspState,
    FindProofQualifiedCompleteDomainNoGoodCore,
    FindProofQualifiedUniversalNoGoodCore,
    PropagateExactNoGoodClauses,
    SelectBinaryExactNoGoodClauses,
    MayAdvanceStagedCandidateOnExhaustion,
    MandatoryClaimsConflict,
    InterleavePhysicalPortSeamsByEgressClass,
    MergeSignalScopedAvoidancePositions,
    MergeSignalScopedRawPortalEntries,
    PlanNegotiatedRouteTrees,
    NegotiatedColumnsForTiles,
    OptionalPortalSeedSliceExpired,
    PortalTupleConflictsWithFrozenComponentClaims,
    RawPortalProfileMatchesRequestedControls,
    RawPortalGeometryCache,
    RawPortalGeometryReusePlan,
    ReadRouteTreeBatchCompletionMask,
    BuildRawPortalPlacementGeometryFingerprint,
    BuildRawPortalResourceGeometryFingerprint,
    BuildTranslatedPortablePortalId,
    MaterializeValidatedPortablePortalPositiveWitness,
    BuildPhysicalGlobalRouteTreeResultCacheKey,
    PreparedPortalDomainCache,
    RetainPreparedPortalDomainCache,
    RetainPhysicalGlobalRouteTreeResults,
    RetainRawPortalGeometryCache,
    RequiredPhysicalAssemblyRoutingLayerCount,
    RequiredRoutingLayerCountForAccess,
    ReserveBoundaryPortals,
    ReserveClusterBoundaryLeases,
    ReserveNegotiatedBoundaryEscapes,
    RetainPartialAssignmentCandidateCache,
    RetainNegotiatedInitialCandidateOption,
    SelectEscalatedRoutingLayerCount,
    SelectAuthoritativeRouteRequestGuide,
    SelectExactAssignmentCompletionCutWideRequests,
    SelectPendingExactAssignmentCompletionRequestIndices,
    SelectExactAssignmentCompletionRequestBatch,
    SelectExactAssignmentCompletionReserveMilliseconds,
    ShouldContinueDistinctExactCutFrontier,
    ShouldBuildCapacityAwareGlobalGuidePlan,
    CanReuseFrozenPhysicalPortGuidePlan,
    SelectComponentPreparationProfiles,
    ShouldDeferUnreservedCandidateRequestShape,
    ShouldCompletePhysicalCandidateRequestWindow,
    LazyCandidateRouteRequest,
    ShouldRejectRoutedComponentForeignEscape,
    SelectCandidateRegenerationSignals,
    SelectCandidateRegenerationCoverSignals,
    SelectCandidateRealizabilityProbeSliceSeconds,
    ShouldContinueUniqueAccessDistinctCandidateRealizabilityProof,
    ShouldHandoffContinuedCandidateRealizabilityCut,
    SelectAnonymousMinimumFailurePairRelocationSignals,
    BuildAnonymousCandidateDomainFingerprint,
    SelectPriorityPlacementRelocationSignals,
    SelectPhysicalGlobalAssignmentSuffixSignals,
    SelectPhysicalGlobalPairSupportSuffixSignals,
    SelectPhysicalGlobalNativePairCutSuffixSignals,
    SelectCompletedPhysicalGlobalPairNoGoodEdges,
    SelectOpenPhysicalGlobalCandidateDomainSignals,
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
    SelectHierarchicalRoutingMaximumLayerCount,
    ValidatePhysicalAssemblyRoutingLayerLimit,
    ValidatePhysicalComponentExactAttachmentPortals,
    SelectMaturePortfolioExactInitialRequestFloor,
    SelectMaturePortfolioPortalLimit,
    SelectNegotiatedExpandedRequestMinimumExpansionCount,
    SelectNegotiatedOffenderHaloLaneDiversityLevel,
    SelectCoordinatedPortalVariantCount,
    SelectOptionalPortalSeedSliceSeconds,
    SelectRawPortalGeometryReusePlan,
    SelectPreparedPortalDomainCache,
    SelectPhysicalExteriorConnectorPath,
    SearchFrozenPhysicalExteriorConnectorBatch,
    SelectTransactionalLeasePrescreenSignals,
    TransformPlanarRoutingPosition,
    TransformPortableCompletePortalDomainKeys,
    SelectPortablePortalPositiveReusableSignals,
    SelectPortablePortalProofReusableSignals,
    PartitionExpectedGenericPortalDomainKeys,
    PartitionPhysicalOwnedTerminalPortalRequests,
    TouchPhysicalGlobalRouteTreeResult,
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
    FilterSourceConnectedTargetBranches,
    _BuildTargetPortalBranches,
    _MaterializeCandidate,
    _ReserveRepeaters,
)
from Compiler.Routing.Models import PhysicalGlobalPlanResumeCursor
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
    PhysicalComponentPortReservation,
    PhysicalComponentBoundaryPortReservation,
    PhysicalComponentChannelReservation,
    PhysicalPortApertureOptionFactor,
    PhysicalLocalPortPairProofRecord,
    PhysicalPortLaneFactor,
    PhysicalPortSeamFactor,
    FrozenPhysicalComponentPostClosurePortalHandoff,
    RoutingResources,
    RoutingStaticGeometry,
    PlacementAccessEscapeStub,
    PlacementAccessFabric,
    PlacementAccessTerminalDomain,
)
from Compiler.Routing.Actions.Geometry import BuildRoutingResources
from Compiler.Routing.Pcb import (
    PrepareRawTrackAssignmentDomain,
    PrepareTrackAssignment,
    RoutePcbDesign,
)
from Compiler.Routing.ComponentPipeline import (
    BuildPhysicalLocalPortPairSupportCertificate,
    BuildPhysicalPortGlobalContractFingerprint,
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


def _LocalPairProofRecords(
    CurrentSignal,
    CurrentContracts,
    CompleteSignal,
    CompleteContract,
):
    return tuple(
        PhysicalLocalPortPairProofRecord(
            CurrentSignal=CurrentSignal,
            CurrentContract=CurrentContract,
            CompleteSignal=CompleteSignal,
            CompleteContract=CompleteContract,
            ProofDomainFingerprint="domain:" + CurrentContract,
            ProofFingerprint="proof:" + CurrentContract,
            Status="architectural-unsatisfiable",
            Complete=True,
            Feasible=False,
        )
        for CurrentContract in CurrentContracts
    )


class AuthoritativePlannerTests(unittest.TestCase):
    def testExactPortalConstraintFactorsCaptureCrossVariableAirTernary(
        self,
    ) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Domains = (
            ExactPortalConstraintVariableDomain(
                Variable="A",
                Signal="Signal",
                Choices=(ExactPortalConstraintChoice(
                    ChoiceId="a",
                    Nodes=frozenset({(0, 1, 0)}),
                ),),
            ),
            ExactPortalConstraintVariableDomain(
                Variable="B",
                Signal="Signal",
                Choices=(ExactPortalConstraintChoice(
                    ChoiceId="b",
                    Nodes=frozenset({(1, 2, 0)}),
                ),),
            ),
            ExactPortalConstraintVariableDomain(
                Variable="C",
                Signal="Signal",
                Choices=(ExactPortalConstraintChoice(
                    ChoiceId="c",
                    Nodes=frozenset({(0, 3, 0)}),
                ),),
            ),
        )

        Extraction = ExtractExactPortalConstraintFactors(Domains, Graph)
        Sparse = ExtractSparseExactPortalConstraintFactors(Domains, Graph)

        self.assertTrue(Extraction.Complete)
        self.assertTrue(Sparse.Complete)
        self.assertEqual(
            Sparse.ForbiddenTuples,
            Extraction.ForbiddenTuples,
        )
        self.assertEqual(Extraction.MaximumForbiddenTupleArity, 3)
        self.assertEqual(
            tuple(Value.Assignments for Value in Extraction.ForbiddenTuples),
            ((('A', 'a'), ('B', 'b'), ('C', 'c')),),
        )
        self.assertEqual(
            Extraction.ForbiddenTuples[0].ConflictPositions,
            frozenset({(0, 2, 0)}),
        )
        # No unary or binary projection sees the air reservation created by
        # A+B colliding with C's support at (0, 2, 0).
        for Assignment in (
            {"A": "a", "B": "b"},
            {"A": "a", "C": "c"},
            {"B": "b", "C": "c"},
        ):
            self.assertTrue(ExactPortalConstraintAssignmentSatisfiesFactors(
                Assignment,
                Extraction.ForbiddenTuples,
            ))
        self.assertFalse(ExactPortalConstraintAssignmentSatisfiesFactors(
            {"A": "a", "B": "b", "C": "c"},
            Extraction.ForbiddenTuples,
        ))

    def testExactPortalConstraintFactorsMatchBruteForceAssignments(
        self,
    ) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Domains = tuple(
            ExactPortalConstraintVariableDomain(
                Variable=Variable,
                Signal="Signal",
                Choices=(
                    ExactPortalConstraintChoice(
                        ChoiceId=Variable.lower() + "0",
                        Nodes=frozenset({First}),
                    ),
                    ExactPortalConstraintChoice(
                        ChoiceId=Variable.lower() + "1",
                        Nodes=frozenset({Second}),
                    ),
                ),
            )
            for Variable, First, Second in (
                ("A", (0, 1, 0), (10, 1, 0)),
                ("B", (1, 2, 0), (12, 1, 0)),
                ("C", (0, 3, 0), (14, 1, 0)),
            )
        )
        Extraction = ExtractExactPortalConstraintFactors(Domains, Graph)

        self.assertTrue(Extraction.Complete)
        for First in Domains[0].Choices:
            for Second in Domains[1].Choices:
                for Third in Domains[2].Choices:
                    Choices = (First, Second, Third)
                    Assignment = {
                        Domain.Variable: Choice.ChoiceId
                        for Domain, Choice in zip(Domains, Choices)
                    }
                    Claims = Graph.BuildRouteClaims(frozenset(
                        Position
                        for Choice in Choices
                        for Position in Choice.Nodes
                    ))
                    BruteForceLegal = not any((
                        Claims.RequiredAirCells & Claims.WireCells,
                        Claims.SupportCells & (
                            Claims.WireCells | Claims.RequiredAirCells
                        ),
                    ))
                    self.assertEqual(
                        ExactPortalConstraintAssignmentSatisfiesFactors(
                            Assignment,
                            Extraction.ForbiddenTuples,
                        ),
                        BruteForceLegal,
                    )

        Projection = ProjectExactPortalConstraintFactors(
            Extraction,
            Domains,
            ("A", "C"),
        )
        ExpectedPairs = tuple(
            (First.ChoiceId, Third.ChoiceId)
            for First in Domains[0].Choices
            for Third in Domains[2].Choices
            if any(
                ExactPortalConstraintAssignmentSatisfiesFactors(
                    {
                        "A": First.ChoiceId,
                        "B": Second.ChoiceId,
                        "C": Third.ChoiceId,
                    },
                    Extraction.ForbiddenTuples,
                )
                for Second in Domains[1].Choices
            )
        )
        self.assertTrue(Projection.Complete)
        self.assertEqual(Projection.SupportedChoicePairs, ExpectedPairs)
        self.assertFalse(ProjectExactPortalConstraintFactors(
            Extraction,
            Domains,
            ("A", "C"),
            ShouldStop=lambda: True,
        ).Complete)

    def testSparsePortalConstraintFactorsMatchReferenceFixtures(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Generator = Random(903_217)
        Positions = tuple(
            (X, Y, Z)
            for X in range(4)
            for Y in range(1, 4)
            for Z in range(2)
        )
        for FixtureIndex in range(20):
            Domains = tuple(
                ExactPortalConstraintVariableDomain(
                    Variable=f"V{VariableIndex}",
                    Signal=(
                        "First"
                        if VariableIndex < 2
                        else "Second"
                    ),
                    Choices=tuple(
                        ExactPortalConstraintChoice(
                            ChoiceId=(
                                f"v{VariableIndex}:c{ChoiceIndex}"
                            ),
                            Nodes=frozenset(Generator.sample(
                                Positions,
                                1 + Generator.randrange(2),
                            )),
                        )
                        for ChoiceIndex in range(2)
                    ),
                )
                for VariableIndex in range(3)
            )
            Reference = ExtractExactPortalConstraintFactors(Domains, Graph)
            Sparse = ExtractSparseExactPortalConstraintFactors(Domains, Graph)
            self.assertTrue(Reference.Complete, FixtureIndex)
            self.assertTrue(Sparse.Complete, FixtureIndex)
            self.assertEqual(
                Sparse.ForbiddenTuples,
                Reference.ForbiddenTuples,
                FixtureIndex,
            )

    def testPostClosurePortalCompletionPreservesPolicyEmptyProofs(
        self,
    ) -> None:
        PolicyKey = ("Alpha", (1, 2, 3), 3)
        RequestKey = ("Alpha", (1, 2, 3), 1)

        self.assertEqual(
            MergePostClosurePortalCompletionKeys(
                (PolicyKey,),
                (RequestKey, RequestKey),
            ),
            tuple(sorted((PolicyKey, RequestKey))),
        )

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
        self.assertTrue(
            Failure.Diagnostics["MandatoryAccessProof"][
                "PortalTupleDomainComplete"
            ]
        )
        self.assertEqual(
            Failure.Diagnostics["MandatoryAccessProof"]["ProofScope"],
            "complete-portal-tuple-domain",
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

    def testPortalTupleCompletenessRequiresEveryEligibleLayer(self) -> None:
        CompleteLayer = {
            "Layer": 0,
            "CompletePortalTupleCount": 16,
            "EvaluatedPortalTupleCount": 16,
            "PortalTupleDomainComplete": True,
        }
        self.assertFalse(PortalTupleFeasibilityDomainIsComplete(
            (CompleteLayer,),
            ExpectedLayers=range(2),
        ))
        self.assertTrue(PortalTupleFeasibilityDomainIsComplete(
            (
                CompleteLayer,
                {**CompleteLayer, "Layer": 1},
            ),
            ExpectedLayers=range(2),
        ))

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

    def testPortalBatchCompletionMaskRequiresCandidateAlignment(self) -> None:
        with self.assertRaises(ValueError):
            ReadPortalBatchCandidatesAndCompletionMask(
                SimpleNamespace(
                    Candidates=((),),
                    CompletedWork=1,
                    DeadlineExceeded=False,
                    CompletionMask=(True, False),
                ),
                2,
            )
        Candidates, CompletionMask = (
            ReadPortalBatchCandidatesAndCompletionMask(
                SimpleNamespace(
                    Candidates=((), ()),
                    CompletedWork=2,
                    DeadlineExceeded=False,
                    CompletionMask=(True, True),
                ),
                2,
            )
        )
        self.assertEqual(len(Candidates), 2)
        self.assertEqual(CompletionMask, (True, True))

        with self.assertRaises(ValueError):
            ReadPortalBatchCandidatesAndCompletionMask(
                SimpleNamespace(
                    Candidates=((), ()),
                    CompletedWork=1,
                    TotalWork=3,
                    DeadlineExceeded=True,
                    CompletionMask=(False, True),
                ),
                2,
            )

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

    def testConfiguredPortalRequestDomainBindsEverySearchInput(self) -> None:
        BaseArguments = (
            "SignalA",
            4,
            1000,
            "guide-input",
            (0, 20, -4, 16),
        )
        FirstRecord = (
            ((1, 2, 3), 0, ((1, 2, 3),), ((4, 2, 3),), "allowed-a", 2, 4, 1000),
        )
        Baseline = BuildConfiguredPortalRequestDomainFingerprint(
            *BaseArguments,
            FirstRecord,
        )
        self.assertNotEqual(
            Baseline,
            BuildConfiguredPortalRequestDomainFingerprint(
                *BaseArguments,
                (
                    ((1, 2, 3), 0, ((1, 2, 3),), ((5, 2, 3),), "allowed-a", 2, 4, 1000),
                ),
            ),
        )
        self.assertNotEqual(
            Baseline,
            BuildConfiguredPortalRequestDomainFingerprint(
                "SignalA",
                4,
                1001,
                "guide-input",
                (0, 20, -4, 16),
                FirstRecord,
            ),
        )

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

    def testFrozenPostClosurePortalHandoffReturnsExactPreparedFabric(
        self,
    ) -> None:
        Region = SimpleNamespace(
            Bounds=(0, 8, 1, 7, -2, 6),
            Nodes=((0, 2, 0), (1, 2, 0)),
            Edges=(((0, 2, 0), (1, 2, 0)),),
        )
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
            LayerCount=2,
            PortalLimit=6,
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
            AssignedColumns=frozenset({(0, 0), (1, 0)}),
            ReservedAccess=frozenset({(0, 2, 0)}),
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
        Plan = SimpleNamespace(
            PlanFingerprint="assembly-plan",
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component-graph",
            ResourceGraphFingerprint=ResourceFingerprint,
            ExteriorRegionFingerprint=RegionFingerprint,
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

        Selected = ValidateFrozenPhysicalComponentPostClosurePortalHandoff(
            Resources,
            Preparation,
            Plan,
        )

        self.assertIs(Selected, Cache)
        self.assertIs(Selected.Region, Region)
        self.assertEqual(Selected.AssignedColumns, Cache.AssignedColumns)
        self.assertEqual(Selected.ReservedAccess, Cache.ReservedAccess)
        self.assertIs(Selected.PortalEntries, Cache.PortalEntries)
        self.assertEqual(
            BuildFrozenPostClosurePortalHandoffTelemetry(
                Resources,
                Preparation,
                Plan,
            ),
            {
                "Applied": True,
                "PreparationDomainFingerprint": "prepared-domain",
                "PhysicalAssemblyPlanFingerprint": "assembly-plan",
                "ExteriorRegionFingerprint": RegionFingerprint,
                "AssignedColumnCount": 2,
                "ReservedAccessCount": 1,
                "PortalEntryCount": 0,
                "PortableProofUsed": False,
            },
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

    def _BuildMandatoryPortalPairFixture(self, AlphaPositions):
        class Graph:
            @staticmethod
            def BuildRouteClaims(Nodes):
                Nodes = frozenset(Nodes)
                return RoutingResourceClaims(
                    WireCells=Nodes,
                    ElectricalCells=Nodes,
                )

        GraphValue = Graph()

        def Portal(Signal, Name, Position):
            Claims = GraphValue.BuildRouteClaims((Position,))
            return PinAccessPortal(
                PortalId=f"{Signal}:{Name}",
                Signal=Signal,
                Terminal=Position,
                Layer=0,
                Path=(Position,),
                Edges=frozenset(),
                Claims=Claims,
                Length=1,
                BendCount=0,
                ViaCount=0,
                Cost=1,
            )

        Domains = {
            "Alpha": ((
                *(
                    Portal("Alpha", str(Index), Position)
                    for Index, Position in enumerate(AlphaPositions)
                ),
            ),),
            "Beta": ((Portal("Beta", "0", (0, 1, 0)),),),
        }
        Fixed = {"Alpha": frozenset(), "Beta": frozenset()}
        return GraphValue, Fixed, Domains

    def testMandatoryPortalPairFactorFindsCompleteWitness(self) -> None:
        Graph, Fixed, Domains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0), (2, 1, 0))
        )
        Certificate = SolveMandatoryPortalPairFeasibility(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="feasible-domain",
        )

        self.assertTrue(Certificate.Complete)
        self.assertTrue(Certificate.Feasible)
        self.assertEqual(
            dict(Certificate.WitnessPortalIds)["Alpha"],
            ("Alpha:1",),
        )
        self.assertEqual(SelectCertifiedMandatoryPortalPairCuts((Certificate,)), ())

    def testMandatoryPortalPairFactorProvesCompleteUnsat(self) -> None:
        Graph, Fixed, Domains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0),)
        )
        Certificate = SolveMandatoryPortalPairFeasibility(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="unsat-domain",
        )

        self.assertTrue(Certificate.Complete)
        self.assertFalse(Certificate.Feasible)
        self.assertTrue(Certificate.ConflictFingerprint)
        self.assertEqual(
            SelectCertifiedMandatoryPortalPairCuts((Certificate,))[0][0],
            ("Alpha", "Beta"),
        )

    def testMandatoryPortalPairFactorCompletesMultiTerminalProductWithMemo(
        self,
    ) -> None:
        Graph, Fixed, BaseDomains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0),)
        )

        def At(Portal, PortalId, Position):
            return replace(
                Portal,
                PortalId=PortalId,
                Terminal=Position,
                Path=(Position,),
                Claims=Graph.BuildRouteClaims((Position,)),
            )

        Alpha = BaseDomains["Alpha"][0][0]
        Beta = BaseDomains["Beta"][0][0]
        Domains = {
            "Alpha": (
                (
                    At(Alpha, "Alpha:a0", (0, 1, 0)),
                    At(Alpha, "Alpha:a0-alias", (0, 1, 0)),
                ),
                (
                    At(Alpha, "Alpha:a1", (2, 1, 0)),
                    At(Alpha, "Alpha:a1-alias", (2, 1, 0)),
                ),
            ),
            "Beta": (
                (
                    At(Beta, "Beta:b0", (0, 1, 0)),
                    At(Beta, "Beta:b0-alias", (0, 1, 0)),
                ),
                (
                    At(Beta, "Beta:b1", (3, 1, 0)),
                    At(Beta, "Beta:b1-alias", (3, 1, 0)),
                ),
            ),
        }
        Certificate = SolveMandatoryPortalPairFeasibility(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="multi-terminal-unsat-domain",
        )

        self.assertTrue(Certificate.Complete)
        self.assertFalse(Certificate.Feasible)
        self.assertGreater(Certificate.MemoizedStateHitCount, 0)
        self.assertLess(Certificate.ExpansionCount, 2 ** 4)
        self.assertEqual(
            SelectCertifiedMandatoryPortalPairCuts((Certificate,))[0][0],
            ("Alpha", "Beta"),
        )

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

    def testMandatoryPortalPairFactorReusesCompleteCertificate(self) -> None:
        Graph, Fixed, Domains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0),)
        )
        Cache = {}
        Arguments = dict(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="cached-domain",
        )
        First, FirstHit = GetMandatoryPortalPairFeasibilityCertificate(
            Cache,
            **Arguments,
        )
        Second, SecondHit = GetMandatoryPortalPairFeasibilityCertificate(
            Cache,
            **Arguments,
            ShouldStop=lambda: True,
        )

        self.assertFalse(FirstHit)
        self.assertTrue(SecondHit)
        self.assertIs(Second, First)

    def _BuildBoundaryMandatoryPairRelationFixture(
        self,
        *,
        SignalNames=("Alpha", "Beta"),
        ReverseDomains=False,
        FrozenClaims=(),
    ):
        class Graph:
            @staticmethod
            def BuildRouteClaims(Nodes):
                Nodes = frozenset(Nodes)
                return RoutingResourceClaims(
                    WireCells=Nodes,
                    ElectricalCells=Nodes,
                )

        GraphValue = Graph()
        FirstSignal, SecondSignal = SignalNames
        Preparation = SimpleNamespace(
            DomainFingerprint="prepared-domain",
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="resource",
            GuideFingerprint="guide",
            ExteriorFabricSetFingerprint="fabric",
            ExteriorRegionFingerprint="region",
            BoundaryPortReservationsBySignal=tuple(
                (
                    Signal,
                    tuple(
                        SimpleNamespace(
                            ApertureContractFingerprint=(
                                f"aperture:{Signal}:{Suffix}"
                            )
                        )
                        for Suffix in ("0", "1")
                    ),
                )
                for Signal in SignalNames
            ),
        )

        def Portal(Signal, Name, Positions):
            Positions = tuple(Positions)
            return PinAccessPortal(
                PortalId=f"{Signal}:{Name}",
                Signal=Signal,
                Terminal=Positions[0],
                Layer=0,
                Path=Positions,
                Edges=frozenset(),
                Claims=GraphValue.BuildRouteClaims(Positions),
                Length=len(Positions),
                BendCount=0,
                ViaCount=0,
                Cost=len(Positions),
            )

        FrozenFingerprint = "frozen:" + str(tuple(
            Claim.Signal for Claim in FrozenClaims
        ))

        def Domain(Signal, Suffix, Positions):
            PortalValue = Portal(Signal, Suffix, Positions)
            return PhysicalBoundaryMandatoryPortalFactorDomain(
                DomainFingerprint=f"domain:{Signal}:{Suffix}",
                PreparedDomainFingerprint="prepared-domain",
                PlacementFingerprint="placement",
                ComponentGraphFingerprint="component",
                ResourceGraphFingerprint="resource",
                TechnologyFingerprint="technology",
                GuideFingerprint="guide",
                ExteriorFabricSetFingerprint="fabric",
                ExteriorRegionFingerprint="region",
                Signal=Signal,
                ApertureOptionFingerprint=f"option:{Signal}:{Suffix}",
                ApertureContractFingerprint=(
                    f"aperture:{Signal}:{Suffix}"
                ),
                GlobalContractFingerprint=f"global:{Signal}:{Suffix}",
                ChannelContractFingerprint=f"channel:{Signal}",
                ChannelLayer=0,
                FixedAccessNodes=frozenset(),
                CommonFixedAccessNodes=frozenset(),
                OptionOverlayNodes=frozenset(Positions),
                OptionOverlayPortalDomainIndex=0,
                PortalDomains=((PortalValue,),),
                GenericPortalDomainFingerprint=f"generic:{Signal}",
                PortalRequestDomainFingerprint=f"request:{Signal}",
                PortalGuideInputFingerprint="portal-guide",
                FrozenComponentClaimsFingerprint=FrozenFingerprint,
                FrozenComponentClaims=tuple(FrozenClaims),
                Complete=True,
            )

        X = (0, 1, 0)
        Y = (2, 1, 0)
        Domains = (
            Domain(FirstSignal, "0", (X,)),
            Domain(FirstSignal, "1", (Y,)),
            Domain(SecondSignal, "0", (X, Y)),
            Domain(SecondSignal, "1", (X,)),
        )
        if ReverseDomains:
            Domains = tuple(reversed(Domains))
        Resources = SimpleNamespace(
            ResourceGraph=GraphValue,
            PhysicalBoundaryMandatoryPortalFactorDomainCache={
                (
                    Value.PreparedDomainFingerprint,
                    Value.Signal,
                    Value.ApertureContractFingerprint,
                ): Value
                for Value in Domains
            },
            PhysicalBoundaryMandatoryPortalPairRelationCache={},
            PhysicalGlobalMandatoryPortalPairCertificateCache={},
        )
        return Preparation, Resources

    def testBoundaryMandatoryPortalPairRelationCompilesFullTwoByTwoDomain(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Beta", "Alpha"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertEqual(Relation.ExpectedOptionPairCount, 4)
        self.assertEqual(len(Relation.Certificates), 4)
        self.assertEqual(len(Relation.UnsatisfiableApertureClauses), 3)
        self.assertEqual(
            sum(
                Value.Certificate.Feasible is True
                for Value in Relation.Certificates
            ),
            1,
        )

    def testBoundaryMandatoryPortalPairRelationMatchesExhaustiveReference(
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
        FactorsByAperture = {
            (Value.Signal, Value.ApertureContractFingerprint): Value
            for Value in Resources
            .PhysicalBoundaryMandatoryPortalFactorDomainCache.values()
        }
        for OptionCertificate in Relation.Certificates:
            First = FactorsByAperture[(
                Relation.Signals[0],
                OptionCertificate.FirstApertureContractFingerprint,
            )]
            Second = FactorsByAperture[(
                Relation.Signals[1],
                OptionCertificate.SecondApertureContractFingerprint,
            )]
            Reference = SolveMandatoryPortalPairFeasibility(
                Signals=Relation.Signals,
                FixedAccessNodesBySignal={
                    First.Signal: First.FixedAccessNodes,
                    Second.Signal: Second.FixedAccessNodes,
                },
                PortalDomainsBySignal={
                    First.Signal: First.PortalDomains,
                    Second.Signal: Second.PortalDomains,
                },
                FrozenComponentClaims=First.FrozenComponentClaims,
                ResourceGraph=Resources.ResourceGraph,
                DomainFingerprint=(
                    OptionCertificate.Certificate.DomainFingerprint
                ),
            )
            with self.subTest(
                First=First.ApertureContractFingerprint,
                Second=Second.ApertureContractFingerprint,
            ):
                self.assertTrue(Reference.Complete)
                self.assertEqual(
                    OptionCertificate.Certificate.Feasible,
                    Reference.Feasible,
                )
                if Reference.Feasible:
                    self.assertTrue(
                        OptionCertificate.Certificate.WitnessPortalIds
                    )

    def testBoundaryMandatoryPortalPairRelationUsesSparseExactProjection(
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
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertEqual(Relation.ExpectedOptionPairCount, 4)
        self.assertEqual(Relation.FactorCertificateCount, 2)
        self.assertEqual(
            len(Relation.Certificates),
            Relation.ExpectedOptionPairCount,
        )
        self.assertEqual(
            [Value.Certificate.Feasible for Value in Relation.Certificates],
            [False, False, False, True],
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

    def testBoundaryMandatoryPortalPairRelationMatchesSelfConflictReference(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )

        class SelfConflictGraph:
            @staticmethod
            def BuildRouteClaims(Nodes):
                Nodes = frozenset(Nodes)
                return RoutingResourceClaims(
                    WireCells=Nodes,
                    SupportCells=Nodes,
                    ElectricalCells=Nodes,
                )

        Resources.ResourceGraph = SelfConflictGraph()
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertTrue(all(
            Value.Certificate.Feasible is False
            for Value in Relation.Certificates
        ))
        self.assertEqual(
            len(Relation.UnsatisfiableApertureClauses),
            Relation.ExpectedOptionPairCount,
        )

    def testBoundaryMandatoryPortalPairRelationMatchesFrozenBlockerReference(
        self,
    ) -> None:
        Graph, _Fixed, _Domains = self._BuildMandatoryPortalPairFixture(())
        Position = (0, 1, 0)
        ForeignClaim = LocalRouteClaim(
            Signal="Foreign",
            ClusterId=0,
            Root=Position,
            ConnectedTargets=(),
            BoundaryNodes=(),
            Nodes=frozenset((Position,)),
            Edges=frozenset(),
            Claims=Graph.BuildRouteClaims((Position,)),
        )
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture(
                FrozenClaims=(ForeignClaim,),
            )
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertGreater(Relation.ForeignDependencyCertificateCount, 0)
        self.assertTrue(any(
            "Foreign" in Value.Certificate.DependencySignals
            for Value in Relation.Certificates
        ))
        FactorsByAperture = {
            (Value.Signal, Value.ApertureContractFingerprint): Value
            for Value in Resources
            .PhysicalBoundaryMandatoryPortalFactorDomainCache.values()
        }
        for Value in Relation.Certificates:
            First = FactorsByAperture[(
                Relation.Signals[0],
                Value.FirstApertureContractFingerprint,
            )]
            Second = FactorsByAperture[(
                Relation.Signals[1],
                Value.SecondApertureContractFingerprint,
            )]
            Reference = SolveMandatoryPortalPairFeasibility(
                Signals=Relation.Signals,
                FixedAccessNodesBySignal={
                    First.Signal: First.FixedAccessNodes,
                    Second.Signal: Second.FixedAccessNodes,
                },
                PortalDomainsBySignal={
                    First.Signal: First.PortalDomains,
                    Second.Signal: Second.PortalDomains,
                },
                FrozenComponentClaims=First.FrozenComponentClaims,
                ResourceGraph=Resources.ResourceGraph,
                DomainFingerprint=Value.Certificate.DomainFingerprint,
            )
            self.assertEqual(Value.Certificate.Feasible, Reference.Feasible)
        self.assertFalse(any(
            frozenset(Value.Certificate.DependencySignals)
            <= frozenset(Relation.Signals)
            and Value.Certificate.Feasible is False
            and frozenset((
                (
                    Relation.Signals[0],
                    Value.FirstApertureContractFingerprint,
                ),
                (
                    Relation.Signals[1],
                    Value.SecondApertureContractFingerprint,
                ),
            )) not in Relation.UnsatisfiableApertureClauses
            for Value in Relation.Certificates
        ))

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

    def testBoundaryMandatoryPortalPairRelationDoesNotProjectForeignDependency(
        self,
    ) -> None:
        Graph, _Fixed, _Domains = self._BuildMandatoryPortalPairFixture(())
        Position = (0, 1, 0)
        Claims = Graph.BuildRouteClaims((Position,))
        ForeignClaim = LocalRouteClaim(
            Signal="Foreign",
            ClusterId=0,
            Root=Position,
            ConnectedTargets=(),
            BoundaryNodes=(),
            Nodes=frozenset((Position,)),
            Edges=frozenset(),
            Claims=Claims,
        )
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture(
                FrozenClaims=(ForeignClaim,),
            )
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertGreater(Relation.ForeignDependencyCertificateCount, 0)
        self.assertLess(
            len(Relation.UnsatisfiableApertureClauses),
            3,
        )

    def testBoundaryMandatoryPortalPairRelationIsOrderAndRenameInvariant(
        self,
    ) -> None:
        FirstPreparation, FirstResources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        SecondPreparation, SecondResources = (
            self._BuildBoundaryMandatoryPairRelationFixture(
                SignalNames=("Left", "Right"),
                ReverseDomains=True,
            )
        )
        First = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            FirstPreparation,
            ("Beta", "Alpha"),
            FirstResources,
        )
        Second = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            SecondPreparation,
            ("Right", "Left"),
            SecondResources,
        )

        self.assertEqual(
            sorted(len(Value) for Value in First.UnsatisfiableApertureClauses),
            sorted(len(Value) for Value in Second.UnsatisfiableApertureClauses),
        )
        self.assertEqual(
            [Value.Certificate.Feasible for Value in First.Certificates],
            [Value.Certificate.Feasible for Value in Second.Certificates],
        )

    def _BuildPreparedBoundaryPortalFactorFixture(
        self,
        AlphaPath,
        *,
        AlphaAttachment=(0, 1, 0),
        ExteriorRegionFingerprint="region",
        AlphaHasExternalTarget=True,
        AlphaOwnedTerminal=(0, 1, 0),
    ):
        class Graph:
            Technology = "technology"

            @staticmethod
            def BuildRouteClaims(Nodes):
                Nodes = frozenset(Nodes)
                return RoutingResourceClaims(
                    WireCells=Nodes,
                    ElectricalCells=Nodes,
                )

        GraphValue = Graph()
        Profiles = {
            "Alpha": NetRoutingProfile(
                Signal="Alpha",
                Root=(0, 1, 0),
                Targets=(
                    ((10, 1, 0),)
                    if AlphaHasExternalTarget
                    else ()
                ),
                Span=10 if AlphaHasExternalTarget else 0,
                Fanout=1 if AlphaHasExternalTarget else 0,
                RetryCount=0,
                Criticality=0,
                IsTrunk=False,
                SourceAccessPath=(),
                TargetAccessPaths=(
                    {(10, 1, 0): ((10, 1, 0),)}
                    if AlphaHasExternalTarget
                    else {}
                ),
            ),
            "Beta": NetRoutingProfile(
                Signal="Beta",
                Root=(4, 1, 0),
                Targets=((12, 1, 0),),
                Span=8,
                Fanout=1,
                RetryCount=0,
                Criticality=0,
                IsTrunk=False,
                SourceAccessPath=(),
                TargetAccessPaths={(12, 1, 0): ((12, 1, 0),)},
            ),
        }

        def Aperture(Signal, Attachment, Path):
            Claims = GraphValue.BuildRouteClaims(Path)
            return PhysicalPortApertureOptionFactor(
                Signal=Signal,
                Direction="output",
                Capacity=1,
                Attachment=Attachment,
                GlobalPath=tuple(Path),
                GlobalClaims=Claims,
                ChannelContractFingerprint=f"channel:{Signal}",
                GlobalContractFingerprint=f"global:{Signal}",
                ApertureContractFingerprint=f"aperture:{Signal}",
                ApertureOptionFingerprint=f"option:{Signal}",
            )

        AlphaAperture = Aperture(
            "Alpha",
            AlphaAttachment,
            AlphaPath,
        )
        BetaAperture = Aperture(
            "Beta",
            (4, 1, 0),
            ((4, 1, 0), (2, 1, 0)),
        )
        Apertures = (AlphaAperture, BetaAperture)
        Boundaries = tuple(
            PhysicalComponentBoundaryPortReservation(
                Signal=Value.Signal,
                Direction=Value.Direction,
                Attachment=Value.Attachment,
                GlobalPath=Value.GlobalPath,
                GlobalClaims=Value.GlobalClaims,
                Capacity=Value.Capacity,
                ChannelContractFingerprint=(
                    Value.ChannelContractFingerprint
                ),
                GlobalContractFingerprint=(
                    Value.GlobalContractFingerprint
                ),
                ApertureContractFingerprint=(
                    Value.ApertureContractFingerprint
                ),
                ReservationFingerprint=f"reservation:{Value.Signal}",
            )
            for Value in Apertures
        )
        Preparation = SimpleNamespace(
            DomainFingerprint="prepared-domain",
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="resource",
            GuideFingerprint="guide",
            ExteriorFabricSetFingerprint="fabric",
            ExteriorRegionFingerprint="region",
            Problem=SimpleNamespace(
                OwnedTerminalDomains=(
                    SimpleNamespace(
                        Signal="Alpha",
                        Terminal=AlphaOwnedTerminal,
                    ),
                    SimpleNamespace(Signal="Beta", Terminal=(4, 1, 0)),
                )
            ),
            AccessCertificate=SimpleNamespace(
                TechnologyFingerprint="technology"
            ),
            ChannelReservations=tuple(
                PhysicalComponentChannelReservation(
                    Signal=Signal,
                    Layer=0,
                    GuideCells=(),
                    ResourceIds=(),
                    Claims=GraphValue.BuildRouteClaims(()),
                    ReservationFingerprint=f"channel:{Signal}",
                )
                for Signal in ("Alpha", "Beta")
            ),
            ApertureFactorsBySignal=tuple(
                (Signal, (Value,))
                for Signal, Value in zip(("Alpha", "Beta"), Apertures)
            ),
            BoundaryPortReservationsBySignal=tuple(
                (Signal, (Value,))
                for Signal, Value in zip(("Alpha", "Beta"), Boundaries)
            ),
        )
        def RawPortal(Signal, Terminal):
            Claims = GraphValue.BuildRouteClaims((Terminal,))
            return PinAccessPortal(
                PortalId=f"raw:{Signal}:{Terminal}",
                Signal=Signal,
                Terminal=Terminal,
                Layer=0,
                Path=(Terminal,),
                Edges=frozenset(),
                Claims=Claims,
                Length=1,
                BendCount=0,
                ViaCount=0,
                Cost=1,
            )

        RawCache = RawPortalGeometryCache(
            PlacementGeometryFingerprint="placement-geometry",
            ResourceGeometryFingerprint="resource-geometry",
            PlacedReference=object(),
            ResourcesReference=object(),
            Region=object(),
            LayerCount=1,
            PortalLimit=1,
            PortalVariantCounts=(("Alpha", 1), ("Beta", 1)),
            GuideExpansion=1,
            StrictMaximumExpansions=1,
            Context=None,
            AssignmentIndexed=None,
            PortalEntries=(
                (
                    ("Alpha", (0, 1, 0), 0),
                    (RawPortal("Alpha", (0, 1, 0)),),
                ),
                (
                    ("Alpha", (10, 1, 0), 0),
                    (RawPortal("Alpha", (10, 1, 0)),),
                ),
                (
                    ("Beta", (12, 1, 0), 0),
                    (RawPortal("Beta", (12, 1, 0)),),
                ),
            ),
            RequestCount=2,
            TargetCount=2,
            StarvationCount=0,
            GuideInputFingerprint="portal-guide",
            CompletePortalDomainKeys=(
                ("Alpha", (0, 1, 0), 0),
                ("Alpha", (10, 1, 0), 0),
                ("Beta", (4, 1, 0), 0),
                ("Beta", (12, 1, 0), 0),
            ),
            PortalRequestDomainFingerprints=(
                ("Alpha", "request:Alpha"),
                ("Beta", "request:Beta"),
            ),
            ExteriorRegionFingerprint=ExteriorRegionFingerprint,
            AuthoritativeResourceGraphFingerprint="resource",
        )
        Domains = BuildPhysicalBoundaryMandatoryPortalFactorDomains(
            Preparation,
            Profiles,
            RawCache,
            GraphValue,
        )
        Resources = SimpleNamespace(
            ResourceGraph=GraphValue,
            PhysicalBoundaryMandatoryPortalFactorDomainCache={
                (
                    Value.PreparedDomainFingerprint,
                    Value.Signal,
                    Value.ApertureContractFingerprint,
                ): Value
                for Value in Domains
            },
            PhysicalBoundaryMandatoryPortalPairRelationCache={},
            PhysicalGlobalMandatoryPortalPairCertificateCache={},
        )
        return Preparation, Resources, Domains

    def testBoundaryPortalFactorSeparatesCommonAndOptionOverlayProvenance(
        self,
    ) -> None:
        _Preparation, _Resources, RootCoveredDomains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((0, 1, 0), (2, 1, 0)),
            )
        )
        _Preparation, _Resources, RootExternalDomains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((10, 1, 0), (8, 1, 0)),
                AlphaAttachment=(10, 1, 0),
                AlphaOwnedTerminal=(10, 1, 0),
            )
        )
        RootCovered = next(
            Value for Value in RootCoveredDomains
            if Value.Signal == "Alpha"
        )
        RootExternal = next(
            Value for Value in RootExternalDomains
            if Value.Signal == "Alpha"
        )

        self.assertEqual(
            RootCovered.CommonFixedAccessNodes,
            frozenset(((10, 1, 0),)),
        )
        self.assertEqual(
            RootCovered.OptionOverlayNodes,
            frozenset(((0, 1, 0), (2, 1, 0))),
        )
        self.assertEqual(
            RootExternal.CommonFixedAccessNodes,
            frozenset(),
        )
        self.assertEqual(
            RootExternal.OptionOverlayNodes,
            frozenset(((10, 1, 0), (8, 1, 0))),
        )
        self.assertEqual(
            RootCovered.FixedAccessNodes,
            RootCovered.CommonFixedAccessNodes
            | RootCovered.OptionOverlayNodes,
        )
        self.assertEqual(
            RootExternal.FixedAccessNodes,
            RootExternal.CommonFixedAccessNodes
            | RootExternal.OptionOverlayNodes,
        )

    def testBoundaryPortalFactorRequiresAttachmentProfileTerminal(self) -> None:
        _Preparation, _Resources, Domains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((8, 1, 0), (2, 1, 0)),
                AlphaAttachment=(8, 1, 0),
                AlphaHasExternalTarget=False,
            )
        )

        self.assertFalse(any(
            Value.Signal == "Alpha" for Value in Domains
        ))

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

    def testBoundaryPortalRegionMismatchCannotPublishCompleteFactor(self) -> None:
        _Preparation, _Resources, Domains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((0, 1, 0), (0, 1, 2)),
                ExteriorRegionFingerprint="different-region",
            )
        )
        self.assertTrue(Domains)
        self.assertTrue(all(not Value.Complete for Value in Domains))

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
            PlacementGeometryFingerprint=(
                BuildRawPortalPlacementGeometryFingerprint(Placed)
            ),
            ResourceGeometryFingerprint=(
                BuildRawPortalResourceGeometryFingerprint(Resources)
            ),
            PlacedReference=Placed,
            ResourcesReference=Resources,
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

    def testPhysicalPortDecompositionDeduplicatesSharedAperture(self) -> None:
        def Claims(Nodes):
            Values = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=Values,
                ElectricalCells=Values,
            )

        GlobalPath = ((4, 1, 0), (5, 1, 0))
        Seams = tuple(
            PhysicalPortSeamFactor(
                FabricAttachment=(0, 1, 0),
                Attachment=(4, 1, 0),
                LocalPath=((0, 1, 0), (LocalX, 1, 0)),
                GlobalPath=GlobalPath,
                Claims=Claims(((0, 1, 0), (LocalX, 1, 0), *GlobalPath)),
                SeamFingerprint=f"seam-{LocalX}",
            )
            for LocalX in (1, 2)
        )
        LaneFactors = {"sum": tuple(
            PhysicalPortLaneFactor(
                Signal="sum",
                Direction="output",
                Capacity=1,
                OwnedTerminals=((0, 1, 0),),
                Domains=(),
                CandidateDomains=(),
                FabricDomainFingerprint=f"fabric-{Index}",
                Seams=(Seam,),
                GuideCells=frozenset(((4, 0), (5, 0))),
                ExternalTerminals=((8, 1, 0),),
            )
            for Index, Seam in enumerate(Seams)
        )}
        Channel = PhysicalComponentChannelReservation(
            Signal="sum",
            Layer=0,
            GuideCells=((4, 0), (5, 0)),
            ResourceIds=(),
            Claims=Claims(GlobalPath),
            ReservationFingerprint="channel-sum",
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

        self.assertEqual(len(Local[0][1]), 2)
        self.assertEqual(len(Apertures[0][1]), 1)
        self.assertEqual(len(Supports[0][1]), 2)
        self.assertEqual(
            {Value.ApertureOptionFingerprint for Value in Supports[0][1]},
            {Apertures[0][1][0].ApertureOptionFingerprint},
        )
        LocalByFingerprint = {
            Value.LocalAccessFingerprint: Value
            for Value in Local[0][1]
        }
        ApertureByFingerprint = {
            Value.ApertureOptionFingerprint: Value
            for Value in Apertures[0][1]
        }
        for Support in Supports[0][1]:
            Port = MaterializeSupportedPhysicalPortReservation(
                LocalByFingerprint[Support.LocalAccessFingerprint],
                ApertureByFingerprint[
                    Support.ApertureOptionFingerprint
                ],
                Support,
                ResourceGraph,
            )
            self.assertEqual(
                Port.ReservationFingerprint,
                Support.ReservationFingerprint,
            )
            self.assertEqual(
                Port.Claims,
                ResourceGraph.BuildRouteClaims(frozenset((
                    *Port.LocalPath,
                    *Port.GlobalPath,
                ))),
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

    @patch(
        "Compiler.Routing.ComponentPipeline."
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

    @patch(
        "Compiler.Routing.ComponentPipeline."
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

    def testPreparedExteriorGuideFabricUnionsEveryLegalSeam(self) -> None:
        Preparation = SimpleNamespace(
            Complete=True,
            LaneFactorsBySignal=((
                "PortA",
                (
                    SimpleNamespace(
                        GuideCells=frozenset(((0, 0), (1, 0))),
                        Seams=(SimpleNamespace(
                            GlobalPath=((1, 2, 0), (2, 2, 0)),
                        ),),
                    ),
                    SimpleNamespace(
                        GuideCells=frozenset(((0, 1),)),
                        Seams=(SimpleNamespace(
                            GlobalPath=((1, 2, 1), (2, 2, 1)),
                        ),),
                    ),
                ),
            ),),
        )

        Expected = {"PortA": frozenset({
            (0, 0), (1, 0), (2, 0),
            (0, 1), (1, 1), (2, 1),
        })}
        self.assertEqual(
            BuildPreparedPhysicalExteriorGuideColumnsBySignal(Preparation),
            Expected,
        )
        ReorderedPreparation = SimpleNamespace(
            Complete=True,
            LaneFactorsBySignal=(
                ("PortA", tuple(reversed(
                    Preparation.LaneFactorsBySignal[0][1]
                ))),
            ),
        )
        self.assertEqual(
            BuildPreparedPhysicalExteriorGuideColumnsBySignal(
                ReorderedPreparation
            ),
            Expected,
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

    def testExactPhysicalAttachmentSkipsGenericPortalPreparation(
        self,
    ) -> None:
        Profile = SimpleNamespace(
            Signal="PortA",
            Root=(0, 7, 0),
            SourceAccessPath=((0, 7, 0), (1, 7, 0)),
            Targets=((8, 7, 0),),
            TargetAccessPaths={
                (8, 7, 0): ((8, 7, 0), (7, 7, 0)),
            },
        )
        Plan = SimpleNamespace(Ports=(SimpleNamespace(
            Signal="PortA",
            Attachment=(0, 7, 0),
        ),))

        self.assertEqual(
            SelectGenericPortalTerminalPaths(Profile, Plan),
            (((8, 7, 0), ((8, 7, 0), (7, 7, 0))),),
        )
        self.assertEqual(
            SelectGenericPortalTerminalPaths(Profile, None),
            (
                ((0, 7, 0), ((0, 7, 0), (1, 7, 0))),
                ((8, 7, 0), ((8, 7, 0), (7, 7, 0))),
            ),
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

    def testReboundCandidatePortalRemainsInCurrentGlobalDomain(self) -> None:
        Attachment = (4, 3, 7)
        GlobalPath = (Attachment, (5, 3, 7))

        def Port(ReservationFingerprint, LocalPath):
            return SimpleNamespace(
                Signal="PortA",
                Direction="output",
                Attachment=Attachment,
                GlobalPath=GlobalPath,
                LocalPath=LocalPath,
                Capacity=1,
                ReservationFingerprint=ReservationFingerprint,
            )

        Channel = SimpleNamespace(Signal="PortA", Layer=1)
        First = Port("local-reservation-a", ((2, 3, 7), Attachment))
        LocalChanged = Port(
            "local-reservation-b",
            ((1, 3, 7), (2, 3, 7), Attachment),
        )

        def Plan(Value):
            return SimpleNamespace(
                Ports=(Value,),
                PlanningChannels=(Channel,),
            )

        ResourceGraph = SimpleNamespace(
            BuildRouteClaims=lambda Path: RoutingResourceClaims(
                WireCells=frozenset(Path),
            ),
        )
        FirstDomains = ApplyPhysicalComponentAssemblyPortalDomains(
            {}, Plan(First), ResourceGraph,
        )
        NextDomains = ApplyPhysicalComponentAssemblyPortalDomains(
            {}, Plan(LocalChanged), ResourceGraph,
        )
        FirstPortalId = next(iter(FirstDomains.values()))[0].PortalId
        self.assertEqual(
            FirstPortalId,
            next(iter(NextDomains.values()))[0].PortalId,
        )
        Candidate = SimpleNamespace(
            CandidateId="bound-global-candidate",
            SourcePortalId=FirstPortalId,
            TargetPortalIds={},
        )
        Retained, Removed = FilterPhysicalCandidatesToCurrentPortalDomain(
            {"PortA": (Candidate,)},
            NextDomains,
        )
        self.assertEqual(Retained, {"PortA": (Candidate,)})
        self.assertEqual(Removed, {})

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

    def testCertifiedApertureDomainKeepsSiblingChangesLocal(self) -> None:
        def Claims(*Nodes):
            NodeSet = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=NodeSet,
                ElectricalCells=NodeSet,
            )

        def Port(Signal, Path, Fingerprint):
            return SimpleNamespace(
                Signal=Signal,
                Direction="output",
                Attachment=Path[0],
                GlobalPath=tuple(Path),
                Capacity=1,
                ReservationFingerprint=Fingerprint,
                Claims=Claims(*Path),
                GlobalClaims=Claims(*Path),
            )

        def Plan(BetaPath=((4, 2, 0), (5, 2, 0))):
            Ports = (
                Port("Alpha", ((0, 2, 0), (1, 2, 0)), "port-a"),
                Port("Beta", BetaPath, "port-b:" + str(BetaPath)),
            )
            return SimpleNamespace(
                Ports=Ports,
                PlanningChannels=tuple(
                    SimpleNamespace(
                        Signal=Value.Signal,
                        ReservationFingerprint="channel:" + Value.Signal,
                    )
                    for Value in Ports
                ),
                GlobalKeepoutNodes=frozenset({
                    (0, 2, 0), (1, 2, 0), (2, 2, 0), (3, 2, 0),
                }),
                ComponentGraphFingerprint="component",
                ResourceGraphFingerprint="resource",
                TechnologyFingerprint="technology",
            )

        First = BuildCertifiedPhysicalComponentApertureDomain(
            Plan(),
            Complete=True,
        )
        Second = BuildCertifiedPhysicalComponentApertureDomain(
            Plan(((4, 2, 1), (5, 2, 1))),
            Complete=True,
        )
        FirstIdentity = BuildPhysicalSignalApertureCandidateDomainIdentity(
            First,
            "Alpha",
            "request-alpha",
            ((2, 2, 0), (3, 2, 0)),
            CoverageCursor=7,
            Complete=False,
        )
        SecondIdentity = BuildPhysicalSignalApertureCandidateDomainIdentity(
            Second,
            "Alpha",
            "request-alpha",
            ((2, 2, 0), (3, 2, 0)),
            CoverageCursor=7,
            Complete=False,
        )

        self.assertTrue(First.Complete)
        self.assertEqual(
            First.StableKeepoutCoreFingerprint,
            Second.StableKeepoutCoreFingerprint,
        )
        self.assertNotEqual(
            First.DomainFingerprint,
            Second.DomainFingerprint,
        )
        self.assertEqual(
            FirstIdentity.DomainFingerprint,
            SecondIdentity.DomainFingerprint,
        )
        ChangedBlockedNodes = (
            BuildPhysicalSignalApertureCandidateDomainIdentity(
                First,
                "Alpha",
                "request-alpha",
                ((2, 2, 0),),
                CoverageCursor=7,
                Complete=False,
            )
        )
        self.assertNotEqual(
            FirstIdentity.DomainFingerprint,
            ChangedBlockedNodes.DomainFingerprint,
        )
        AdvancedIdentity = (
            BuildPhysicalSignalApertureCandidateDomainIdentity(
                First,
                "Alpha",
                "request-alpha",
                ((2, 2, 0), (3, 2, 0)),
                CoverageCursor=8,
                Complete=True,
            )
        )
        self.assertEqual(
            FirstIdentity.StableDomainFingerprint,
            AdvancedIdentity.StableDomainFingerprint,
        )
        self.assertNotEqual(
            FirstIdentity.DomainFingerprint,
            AdvancedIdentity.DomainFingerprint,
        )
        self.assertNotEqual(
            FirstIdentity.StableDomainFingerprint,
            ChangedBlockedNodes.StableDomainFingerprint,
        )

    def testExteriorRouteDomainComposesExactApertureAndChannel(self) -> None:
        def Claims(*Nodes):
            NodeSet = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=NodeSet,
                ElectricalCells=NodeSet,
            )

        def Plan(PortFingerprint, ChannelFingerprint):
            Path = ((0, 2, 0), (1, 2, 0))
            Port = SimpleNamespace(
                Signal="Alpha",
                Direction="output",
                Attachment=Path[0],
                GlobalPath=Path,
                Capacity=1,
                ReservationFingerprint=PortFingerprint,
                Claims=Claims(*Path),
                GlobalClaims=Claims(*Path),
            )
            return SimpleNamespace(
                Ports=(Port,),
                PlanningChannels=(SimpleNamespace(
                    Signal="Alpha",
                    ReservationFingerprint=ChannelFingerprint,
                ),),
                GlobalKeepoutNodes=frozenset({
                    (0, 2, 0), (1, 2, 0), (2, 2, 0),
                }),
                ComponentGraphFingerprint="component",
                ResourceGraphFingerprint="resource",
                TechnologyFingerprint="technology",
            )

        def Identity(PlanValue):
            return BuildPhysicalSignalApertureCandidateDomainIdentity(
                BuildCertifiedPhysicalComponentApertureDomain(
                    PlanValue,
                    Complete=True,
                ),
                "Alpha",
                "authoritative-request-domain",
                ((2, 2, 0),),
                CoverageCursor=3,
                Complete=False,
            )

        Base = Identity(Plan("port-a", "channel-a"))
        ChangedPort = Identity(Plan("port-b", "channel-a"))
        ChangedChannel = Identity(Plan("port-a", "channel-b"))

        self.assertEqual(
            Base.StableDomainFingerprint,
            ChangedPort.StableDomainFingerprint,
        )
        self.assertEqual(
            Base.StableDomainFingerprint,
            ChangedChannel.StableDomainFingerprint,
        )
        self.assertEqual(
            Base.ApertureFingerprint,
            ChangedPort.ApertureFingerprint,
        )
        self.assertNotEqual(
            Base.ChannelReservationFingerprint,
            ChangedChannel.ChannelReservationFingerprint,
        )
        self.assertEqual(
            Base.DomainFingerprint,
            ChangedPort.DomainFingerprint,
        )
        self.assertNotEqual(
            Base.DomainFingerprint,
            ChangedChannel.DomainFingerprint,
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

    def testCompleteApertureDomainRejectsMissingCrossingChannel(self) -> None:
        Port = SimpleNamespace(
            Signal="Alpha",
            Direction="output",
            Attachment=(0, 2, 0),
            GlobalPath=((0, 2, 0), (1, 2, 0)),
            Capacity=1,
            ReservationFingerprint="port-a",
            Claims=RoutingResourceClaims(
                WireCells=frozenset(((0, 2, 0), (1, 2, 0))),
            ),
        )
        Plan = SimpleNamespace(
            Ports=(Port,),
            PlanningChannels=(),
            GlobalKeepoutNodes=frozenset(((0, 2, 0),)),
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="resource",
            TechnologyFingerprint="technology",
        )

        with self.assertRaisesRegex(ValueError, "missing channels"):
            BuildCertifiedPhysicalComponentApertureDomain(
                Plan,
                Complete=True,
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
            "Compiler.Routing.AuthoritativePlanner."
            "BuildPortablePhysicalSignalRouteDomainIdentity"
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
        from Compiler.Routing.AuthoritativePlanner import (
            PortablePhysicalSignalRouteDomainContinuation,
        )
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

    def testExteriorDomainReusesAcrossChangedSiblingGlobalPlan(self) -> None:
        def Claims(*Nodes):
            NodeSet = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=NodeSet,
                ElectricalCells=NodeSet,
            )

        def Plan(BetaPath):
            AlphaPath = ((0, 2, 0), (1, 2, 0))
            Ports = (
                SimpleNamespace(
                    Signal="Alpha",
                    Direction="output",
                    Attachment=AlphaPath[0],
                    GlobalPath=AlphaPath,
                    Capacity=1,
                    ReservationFingerprint="port-alpha",
                    Claims=Claims(*AlphaPath),
                    GlobalClaims=Claims(*AlphaPath),
                ),
                SimpleNamespace(
                    Signal="Beta",
                    Direction="output",
                    Attachment=BetaPath[0],
                    GlobalPath=tuple(BetaPath),
                    Capacity=1,
                    ReservationFingerprint="port-beta:" + str(BetaPath),
                    Claims=Claims(*BetaPath),
                    GlobalClaims=Claims(*BetaPath),
                ),
            )
            return SimpleNamespace(
                Ports=Ports,
                PlanningChannels=tuple(
                    SimpleNamespace(
                        Signal=Value.Signal,
                        ReservationFingerprint="channel:" + Value.Signal,
                    )
                    for Value in Ports
                ),
                GlobalKeepoutNodes=frozenset({
                    (0, 2, 0), (1, 2, 0), (2, 2, 0), (3, 2, 0),
                }),
                ComponentGraphFingerprint="component",
                ResourceGraphFingerprint="resource",
                TechnologyFingerprint="technology",
            )

        def Identity(PlanValue):
            return BuildPhysicalSignalApertureCandidateDomainIdentity(
                BuildCertifiedPhysicalComponentApertureDomain(
                    PlanValue,
                    Complete=True,
                ),
                "Alpha",
                "exterior-region-and-request-domain",
                ((2, 2, 0), (3, 2, 0)),
                CoverageCursor=0,
                Complete=False,
            )

        FirstPlan = Plan(((4, 2, 0), (5, 2, 0)))
        SecondPlan = Plan(((4, 2, 1), (5, 2, 1)))
        FirstIdentity = Identity(FirstPlan)
        SecondIdentity = Identity(SecondPlan)
        OldSiblingOnly = SimpleNamespace(
            CandidateId="old-sibling-only",
            Claims=Claims((4, 2, 0)),
        )
        NewSiblingOnly = SimpleNamespace(
            CandidateId="new-sibling-only",
            Claims=Claims((4, 2, 1)),
        )
        Clear = SimpleNamespace(
            CandidateId="clear",
            Claims=Claims((8, 2, 0)),
        )
        Cache = {
            FirstIdentity.StableDomainFingerprint: (
                MergePhysicalSignalRouteDomainDescriptorProgress(
                    None,
                    PreSiblingDomainFingerprint=(
                        FirstIdentity.StableDomainFingerprint
                    ),
                    Signal="Alpha",
                    RequestDomainFingerprint="request-domain",
                    RequestDescriptorFingerprints=("shape-0",),
                    CompletedDescriptorFingerprints=("shape-0",),
                    Candidates=(),
                    CandidateMetadata={},
                )
            )
        }
        RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint=(
                FirstIdentity.StableDomainFingerprint
            ),
            Signal="Alpha",
            RequestDomainFingerprint="request-domain",
            RequestDescriptorFingerprints=("shape-0",),
            CompletedDescriptorFingerprints=("shape-0",),
            Candidates=(OldSiblingOnly, NewSiblingOnly, Clear),
            CandidateMetadata={
                "old-sibling-only": ("X", 0, 0, 0),
                "new-sibling-only": ("X", 1, 0, 0),
                "clear": ("X", 2, 0, 0),
            },
        )
        RetainCompletePhysicalSignalRouteDomainContinuations(
            Cache,
            {"Alpha": FirstIdentity},
            {"Alpha": ("shape-0",)},
            {"Alpha": "request-domain"},
            {"Alpha": 0},
            {"Alpha": (OldSiblingOnly, NewSiblingOnly, Clear)},
            {"Alpha": {
                "old-sibling-only": ("X", 0, 0, 0),
                "new-sibling-only": ("X", 1, 0, 0),
                "clear": ("X", 2, 0, 0),
            }},
        )

        Restored = SelectReplayablePhysicalSignalRouteDomainContinuation(
            Cache,
            SecondIdentity.StableDomainFingerprint,
            "Alpha",
            "request-domain",
            ("shape-0",),
        )

        self.assertIsNotNone(Restored)
        assert Restored is not None
        self.assertEqual(
            FirstIdentity.StableDomainFingerprint,
            SecondIdentity.StableDomainFingerprint,
        )
        CurrentBeta = next(
            Port for Port in SecondPlan.Ports if Port.Signal == "Beta"
        )
        Filtered = FilterPhysicalCandidatesAgainstSiblingApertures(
            Restored.Candidates,
            (("Beta", CurrentBeta.GlobalClaims),),
        )
        self.assertEqual(
            tuple(Value.CandidateId for Value in Filtered),
            ("clear", "old-sibling-only"),
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

    def testSiblingApertureDiagnosticsSeparateLocalInteriorOwnership(
        self,
    ) -> None:
        def Claims(*Nodes):
            Values = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=Values,
                ElectricalCells=Values,
            )

        CandidateClaims = Claims((0, 0, 0), (10, 0, 0))
        FullSiblingClaims = (
            ("LocalOnly", Claims((0, 0, 0), (1, 0, 0))),
            ("Global", Claims((10, 0, 0), (11, 0, 0))),
        )
        GlobalPathSiblingClaims = (
            ("LocalOnly", Claims((2, 0, 0))),
            ("Global", Claims((10, 0, 0), (11, 0, 0))),
        )

        Full, Global, LocalInteriorOnly = (
            ClassifySiblingApertureSeamOwnershipConflicts(
                CandidateClaims,
                FullSiblingClaims,
                GlobalPathSiblingClaims,
            )
        )

        self.assertEqual(Full, ("Global", "LocalOnly"))
        self.assertEqual(Global, ("Global",))
        self.assertEqual(LocalInteriorOnly, ("LocalOnly",))

    def testCompletePreSiblingPairNoSupportIgnoresThirdApertures(
        self,
    ) -> None:
        def Candidate(Signal, CandidateId, Node):
            return SimpleNamespace(
                Signal=Signal,
                CandidateId=CandidateId,
                Claims=RoutingResourceClaims(
                    WireCells=frozenset((Node,)),
                    ElectricalCells=frozenset((Node,)),
                ),
            )

        First = (Candidate("Alpha", "alpha", (1, 2, 3)),)
        Conflicting = (Candidate("Beta", "beta", (1, 2, 3)),)
        Supported = (Candidate("Beta", "beta-clear", (9, 2, 3)),)

        self.assertTrue(
            CompletePhysicalCandidatePairDomainsHaveNoSupport(
                First,
                Conflicting,
            )
        )
        self.assertFalse(
            CompletePhysicalCandidatePairDomainsHaveNoSupport(
                First,
                (*Conflicting, *Supported),
            )
        )

    def testRequestApertureNoGoodMinimizesRedundantSibling(self) -> None:
        NoGood = BuildMinimalPhysicalRequestApertureNoGood(
            "Alpha",
            "request-alpha",
            (("Beta", "Gamma"), ("Beta",), ("Beta", "Gamma")),
            {
                "Beta": "aperture-beta",
                "Gamma": "aperture-gamma",
            },
        )

        self.assertEqual(NoGood, frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
        )))

    def testCompleteRequestCertifiesAllUnsupportedAlternativeApertures(
        self,
    ) -> None:
        def Claims(*Nodes):
            Values = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=Values,
                ElectricalCells=Values,
            )

        Candidates = (
            SimpleNamespace(Claims=Claims((1, 2, 3))),
            SimpleNamespace(Claims=Claims((2, 2, 3))),
        )
        BoundaryDomains = {
            "Blocker": (
                SimpleNamespace(
                    ApertureContractFingerprint="blocks-both",
                    GlobalClaims=Claims((1, 2, 3), (2, 2, 3)),
                ),
                SimpleNamespace(
                    ApertureContractFingerprint="blocks-one",
                    GlobalClaims=Claims((1, 2, 3)),
                ),
            ),
            "Clear": (
                SimpleNamespace(
                    ApertureContractFingerprint="clear",
                    GlobalClaims=Claims((9, 2, 3)),
                ),
            ),
        }

        Clauses = BuildCompletePhysicalRequestAlternativeApertureNoGoods(
            "Victim",
            "global-victim",
            Candidates,
            BoundaryDomains,
        )

        self.assertEqual(Clauses, (
            frozenset((
                ("Victim", "global-victim"),
                ("Blocker", "blocks-both"),
            )),
        ))

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

    def testPhysicalOrdinaryPortalReusePinsOnlySameKeepoutPaths(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        Cache = replace(
            self.BuildRawPortalCache(
                Placed,
                Resources,
                {"Ordinary": 2, "PhysicalPort": 2},
            ),
            PhysicalGlobalKeepoutFingerprint="keepout-a",
        )
        ReusePlan = RawPortalGeometryReusePlan(
            Cache=Cache,
            ReusedSignals=frozenset((
                "Ordinary",
                "PhysicalPort",
            )),
            GeneratedSignals=frozenset(),
            ExactMatch=False,
            PortableAcrossPlacement=True,
            SignalPlanarTransforms=(
                ("Ordinary", "Identity", (5, 0, 7)),
                ("PhysicalPort", "Identity", (5, 0, 7)),
            ),
        )
        PhysicalTerminal = next(
            Key[1]
            for Key, _Values in Cache.PortalEntries
            if Key[0] == "PhysicalPort"
        )

        Columns = BuildPinnedOrdinaryPortalReuseColumns(
            ReusePlan,
            frozenset(((
                "PhysicalPort",
                (
                    PhysicalTerminal[0] + 5,
                    PhysicalTerminal[1],
                    PhysicalTerminal[2] + 7,
                ),
            ),)),
        )
        OrdinaryTerminal = next(
            Key[1]
            for Key, _Values in Cache.PortalEntries
            if Key[0] == "Ordinary"
        )
        self.assertIn(
            (OrdinaryTerminal[0] + 5, OrdinaryTerminal[2] + 7),
            Columns,
        )
        self.assertNotIn(
            (PhysicalTerminal[0] + 5, PhysicalTerminal[2] + 7),
            Columns,
        )

        self.assertIsNone(SelectRawPortalGeometryReusePlan(
            (Cache,),
            Placed=Placed,
            Resources=Resources,
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts={"Ordinary": 2, "PhysicalPort": 2},
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            AccessGeometryFingerprint=("access",),
            CoordinatedSignals=frozenset(),
            PhysicalGlobalKeepoutFingerprint="keepout-b",
        ))

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

    def testRawPortalSamePlacementReusesUnchangedSignalGeometry(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        OldAlpha = ("Alpha", (0, 1, 0), (), ())
        NewAlpha = (
            "Alpha",
            (0, 1, 0),
            ((0, 1, 0), (1, 1, 0)),
            (),
        )
        Beta = ("Beta", (3, 1, 0), (), ())
        Cache = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2, "Beta": 2},
            AccessGeometryFingerprint=(OldAlpha, Beta),
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
            AccessGeometryFingerprint=(NewAlpha, Beta),
            CoordinatedSignals=frozenset(),
        )

        self.assertIsNotNone(Plan)
        self.assertEqual(Plan.ReusedSignals, frozenset(("Beta",)))
        self.assertEqual(Plan.GeneratedSignals, frozenset(("Alpha",)))
        self.assertTrue(Plan.PortableAcrossPlacement)

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

    def testExactPortalCompletenessSchedulesOnlyMissingSignals(
        self,
    ) -> None:
        AlphaKeys = frozenset((
            ("Alpha", (0, 2, 0), 0),
            ("Alpha", (0, 2, 0), 1),
        ))
        BetaKeys = frozenset((
            ("Beta", (4, 2, 0), 0),
            ("Beta", (4, 2, 0), 1),
        ))
        Missing, ReusedSignals, GeneratedSignals = (
            PartitionExpectedGenericPortalDomainKeys(
                (*AlphaKeys, *BetaKeys),
                (*AlphaKeys, next(iter(BetaKeys))),
            )
        )

        self.assertEqual(len(Missing), 1)
        self.assertEqual({Key[0] for Key in Missing}, {"Beta"})
        self.assertEqual(ReusedSignals, frozenset(("Alpha",)))
        self.assertEqual(GeneratedSignals, frozenset(("Beta",)))

    def testOwnedTerminalPortalPartitionPreservesExactDeferredDomain(
        self,
    ) -> None:
        AlphaTerminal = (0, 2, 0)
        BetaTerminal = (4, 2, 0)
        Requests = [("alpha-0",), ("beta-0",), ("alpha-1",)]
        Metadata = [
            ("Alpha", AlphaTerminal, 0),
            ("Beta", BetaTerminal, 0),
            ("Alpha", AlphaTerminal, 1),
        ]

        OwnedRequests, OwnedMetadata, DeferredRequests, DeferredMetadata = (
            PartitionPhysicalOwnedTerminalPortalRequests(
                Requests,
                Metadata,
                frozenset((("Alpha", AlphaTerminal),)),
            )
        )

        self.assertEqual(OwnedRequests, [("alpha-0",), ("alpha-1",)])
        self.assertEqual(OwnedMetadata, [Metadata[0], Metadata[2]])
        self.assertEqual(DeferredRequests, [("beta-0",)])
        self.assertEqual(DeferredMetadata, [Metadata[1]])
        self.assertEqual(
            [*OwnedMetadata, *DeferredMetadata],
            [Metadata[0], Metadata[2], Metadata[1]],
        )
        with self.assertRaises(ValueError):
            PartitionPhysicalOwnedTerminalPortalRequests(
                Requests,
                Metadata[:-1],
                frozenset(),
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

    def testTargetPortalBranchKeepsSharedAccessForkAsOneChain(self) -> None:
        """A shared access prefix must not turn into a terminal-side jump."""
        Graph, Region, Context = self.BuildGraph()
        Terminal = (0, 1, 0)
        SharedAccessNode = (1, 1, 0)
        AlternateAccessLanding = (2, 1, 0)
        Portal = PinAccessPortal(
            PortalId="A:shared-access-fork",
            Signal="A",
            Terminal=Terminal,
            Layer=0,
            Path=(Terminal, SharedAccessNode),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(),
            Length=2,
            BendCount=0,
            ViaCount=0,
            Cost=2,
        )

        Branch = _BuildTargetPortalBranches(
            (Portal,),
            ((Terminal, SharedAccessNode, AlternateAccessLanding),),
        )[0]

        # The old de-duplication emitted
        # ``shared -> terminal -> alternate``.  The last transition is not a
        # routing edge, so native correctly rejected the whole request.
        self.assertEqual(Branch, [SharedAccessNode, Terminal])
        self.assertTrue(all(
            Graph.BuildPrimitive(First, Second) is not None
            for First, Second in zip(Branch, Branch[1:])
        ))
        Tree = Context.GenerateRouteTree(
            [(4, 1, 0)],
            [Branch],
            sorted(Region.Nodes),
            [(Index, 0) for Index in range(5)],
            1,
            0,
            0,
            0,
            1_000,
        )
        self.assertIn(SharedAccessNode, Tree)
        self.assertIn(Terminal, Tree)

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

    def testPlacementAccessFabricPublishesEveryStubPortalDeterministically(
        self,
    ) -> None:
        """An unfrozen fabric owns the complete terminal escape domain."""
        Graph, _Region, _Context = self.BuildGraph()
        Terminal = (0, 1, 0)

        def BuildStub(
            Ingress: tuple[int, int, int],
            Path: tuple[tuple[int, int, int], ...],
        ) -> PlacementAccessEscapeStub:
            return PlacementAccessEscapeStub(
                Terminal=Terminal,
                Ingress=Ingress,
                Path=Path,
                PhysicalClaims=Graph.BuildRouteClaims(Path),
                CapacityResourceIds=(),
                Complete=True,
            )

        FirstStub = BuildStub(
            (1, 1, 0),
            (Terminal, (1, 1, 0)),
        )
        SecondStub = BuildStub(
            (0, 1, 1),
            (Terminal, (0, 1, 1)),
        )
        ThirdStub = BuildStub(
            (0, 3, 1),
            (Terminal, (0, 2, 1), (0, 3, 1)),
        )
        Fabric = PlacementAccessFabric(
            FabricFingerprint="fabric-domain",
            Nodes=(
                FirstStub.Ingress,
                SecondStub.Ingress,
                ThirdStub.Ingress,
            ),
            Edges=(),
            IngressNodes=(
                FirstStub.Ingress,
                SecondStub.Ingress,
                ThirdStub.Ingress,
            ),
            PhysicalClaims=RoutingResourceClaims(),
            CapacityResourceIds=(),
            TerminalDomains=(
                PlacementAccessTerminalDomain(
                    Signal="Signal",
                    Terminal=Terminal,
                    EscapeStubs=(FirstStub, SecondStub, ThirdStub),
                    Complete=True,
                ),
            ),
            TopologyKind="derived-perimeter-access-v1",
            Complete=True,
        )

        def GenericPortal(
            Layer: int,
            Signal: str = "Signal",
            PortalTerminal: tuple[int, int, int] = Terminal,
        ) -> PinAccessPortal:
            Path = ((9, 1 + 2 * Layer, 0),)
            return PinAccessPortal(
                PortalId=f"generic:{Signal}:{Layer}",
                Signal=Signal,
                Terminal=PortalTerminal,
                Layer=Layer,
                Path=Path,
                Edges=frozenset(),
                Claims=Graph.BuildRouteClaims(Path),
                Length=len(Path),
                BendCount=0,
                ViaCount=0,
                Cost=len(Path),
            )

        UnrelatedKey = ("Other", (4, 1, 0), 0)
        Unrelated = (GenericPortal(
            0,
            Signal="Other",
            PortalTerminal=UnrelatedKey[1],
        ),)
        GenericPortals = {
            ("Signal", Terminal, 0): (GenericPortal(0),),
            ("Signal", Terminal, 1): (GenericPortal(1),),
            UnrelatedKey: Unrelated,
        }

        First = ApplyPlacementAccessFabricPortalDomains(
            GenericPortals,
            Fabric,
            Graph,
            DefaultRedstoneRoutingTechnology,
            0,
            2,
        )
        Second = ApplyPlacementAccessFabricPortalDomains(
            GenericPortals,
            Fabric,
            Graph,
            DefaultRedstoneRoutingTechnology,
            0,
            2,
        )

        self.assertEqual(First, Second)
        self.assertIs(First[UnrelatedKey], Unrelated)
        self.assertEqual(
            [Portal.PortalId for Portal in First[("Signal", Terminal, 0)]],
            [
                "Signal:(0, 1, 0):0:AccessFabricDomain:fabric-domain:0",
                "Signal:(0, 1, 0):0:AccessFabricDomain:fabric-domain:1",
            ],
        )
        self.assertEqual(
            [Portal.PortalId for Portal in First[("Signal", Terminal, 1)]],
            [
                "Signal:(0, 1, 0):1:AccessFabricDomain:fabric-domain:2",
            ],
        )
        self.assertEqual(
            [Portal.Layer for Values in First.values() for Portal in Values
             if Portal.Signal == "Signal"],
            [0, 0, 1],
        )
        self.assertTrue(all(
            "generic:" not in Portal.PortalId
            for Values in First.values()
            for Portal in Values
            if Portal.Signal == "Signal"
        ))

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
            AuthoritativePlanner.BuildCandidateRequestGeometryIdentity
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
            AuthoritativePlanner,
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
        from Compiler.Placement.PcbFlow import (
            BuildDerivedRoutingEnvelopeDomain,
            BuildFrozenEnvelopeRoutingPolicy,
            BuildPlacementAccessDemand,
        )

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
        NativeContext = AuthoritativePlanner.RustRoutingContext

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
            AuthoritativePlanner,
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
        from Compiler.Placement.PcbFlow import (
            BuildDerivedRoutingEnvelopeDomain,
            BuildFrozenEnvelopeRoutingPolicy,
            BuildPlacementAccessDemand,
        )
        from Compiler.Routing.TemplateAssignment import (
            RawTrackAssignmentProblem,
            RawTrackAssignmentTemplate,
            SolveRawTrackAssignmentProblemWithContext,
        )

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

        NativeRoutingContext = AuthoritativePlanner.RustRoutingContext

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
            AuthoritativePlanner,
            "_MaterializeCandidate",
            MaterializeControlledCandidate,
        ), patch.object(
            AuthoritativePlanner,
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
        self.assertEqual(
            Preparation.ToDictionary()["CandidateDomainFingerprint"],
            Preparation.CandidateDomainFingerprint,
        )

        OriginalMaterialize = AuthoritativePlanner._MaterializeCandidate

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
            AuthoritativePlanner,
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

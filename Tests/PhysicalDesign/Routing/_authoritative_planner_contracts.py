"""Shared fixtures and imports for split authoritative planner tests."""

import unittest

from collections import Counter, deque

from dataclasses import replace

from random import Random

from time import monotonic, sleep

from types import SimpleNamespace

from unittest.mock import patch

from PhysicalDesign.Routing.Global.Orchestration import Flow
from PhysicalDesign.Routing.Global.Ports import Portals
from PhysicalDesign.Routing.Global.Assignment import TrackPortfolio

from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR

from PhysicalDesign.Placement.Access.Fabric import AttachPlacementAccessFabric, BuildPlacementAccessFabric

from PhysicalDesign.Placement.Engine.Construction.Commit import PlacePcbGraph

from PhysicalDesign.Placement.Engine.Constraints import PlacementAssignmentConstraintSet

from PhysicalDesign.Routing.Global.Assignment.AssignmentState import BuildNegotiatedInitialColumns, BeginPhysicalAssignmentArcPass, BuildNegotiatedInitialTiles, BuildNegotiatedFallbackGuideColumns, BuildNegotiatedRouteTreeState, GetPhysicalGlobalAssignmentArcIndex, IncrementalPhysicalCandidateArcIndex, BuildPhysicalPortNoGoodKeys, BuildPhysicalLocalPortPairUnsupportedIndex, CandidatePortalShapeRank, CandidatePortalTupleIndex, CandidateRequestShapeDescriptor, CandidateRequestWindowOffset, ExpandNegotiatedTiles, FindNegotiatedBoundaryTouches, IsPhysicalCandidateRequestDomainComplete, PhysicalGlobalAssignmentDomainIsComplete, ConflictClassificationSupportsPhysicalPortPairNoGoods, PlanPhysicalGlobalAssignmentAvoidingExactNoGoods, SelectExactNoGoodCspBranch, OrderPhysicalPortOptionsByPreferences, GetPersistentPhysicalComponentPortCspState, FindProofQualifiedCompleteDomainNoGoodCore, FindProofQualifiedUniversalNoGoodCore, PropagateExactNoGoodClauses, SelectBinaryExactNoGoodClauses, NegotiatedColumnsForTiles, ShouldDeferUnreservedCandidateRequestShape, ShouldCompletePhysicalCandidateRequestWindow, LazyCandidateRouteRequest, SelectPhysicalGlobalAssignmentSuffixSignals, SelectPhysicalGlobalPairSupportSuffixSignals, SelectPhysicalGlobalNativePairCutSuffixSignals, SelectCompletedPhysicalGlobalPairNoGoodEdges, SelectOpenPhysicalGlobalCandidateDomainSignals, ShouldGrowAssignmentBudget

from PhysicalDesign.Routing.Global.Leases.BoundaryLeasePlanning import FindFirstUnavoidableCandidateDomainPairCut, ReserveBoundaryPortals, ReserveNegotiatedBoundaryEscapes, SelectAccessAwareLocalClaimReleases

from PhysicalDesign.Routing.Global.Leases.BoundaryLeases import ReserveClusterBoundaryLeases

from PhysicalDesign.Routing.Global.Candidates.CandidateCache import BuildConfiguredPortalRequestDomainFingerprint, BuildExactPhysicalPortalCertificateIdentityConditions, BuildFrozenPostClosurePortalHandoffTelemetry, ValidateFrozenPhysicalComponentPostClosurePortalHandoff, BuildPinnedOrdinaryPortalReuseColumns, ReadPortalBatchCandidatesAndCompletionMask, SelectCompletedPortalBatchEntries, MergePartialRawPortalBatchWork, MergePostClosurePortalCompletionKeys, SelectMatchingPartialPortalReplaySignals, BuildClusterInterfaceAccessDomainFingerprint, BuildClusterInterfaceProblem, BuildClusterInterfaceReservationAssignmentFingerprint, ChooseRepeatedWorkTransition, ExtendIndexedRoutingResourceGraph, FindUnindexedClaimPositions, GrowAssignmentExpansionLimit, FilterPhysicalCandidatesToCurrentPortalDomain, ClassifyEmptyPhysicalCandidateDomains, MergeSignalScopedRawPortalEntries, RawPortalGeometryReusePlan, ReadRouteTreeBatchCompletionMask, BuildTranslatedPortablePortalId, MaterializeValidatedPortablePortalPositiveWitness, BuildPhysicalGlobalRouteTreeResultCacheKey, PreparedPortalDomainCache, RetainPreparedPortalDomainCache, RetainPhysicalGlobalRouteTreeResults, RetainRawPortalGeometryCache, SelectAuthoritativeBaseClaims, SelectRawPortalGeometryReusePlan, SelectPreparedPortalDomainCache, TransformPlanarRoutingPosition, TransformPortableCompletePortalDomainKeys, SelectPortablePortalPositiveReusableSignals, SelectPortablePortalProofReusableSignals, PartitionExpectedGenericPortalDomainKeys, PartitionPhysicalOwnedTerminalPortalRequests, TouchPhysicalGlobalRouteTreeResult, ShouldRunShapeOptimization

from PhysicalDesign.Routing.Global.Candidates.CandidateDomains import BuildNegotiatedOffenderHaloEscalation, BuildOptionalPortalSeedWorkCheck, BuildCandidateStarvationClassFingerprint, BuildCompleteMandatoryClaimCutCoverage, PortalTupleFeasibilityDomainIsComplete, PortalTupleEmptyProofDomainIsComplete, BuildBoundedPortfolioPortalSliceAdvanceFailure, BuildMandatoryPortalTupleSelfConflictFailure, BuildClusterLeaseSignalPatternFingerprint, BuildTelemetryRoutingStageError, BuildUnavoidableMandatoryClaimCutFailure, CountPriorCandidateFailureFingerprint, CountPriorCandidateRequestDomainFingerprint, CountPriorCandidateStarvationClassFingerprint, CountRoutedComponentGlobalNoTreeAttempts, CountExactLegalRetainedJointStates, CountJointAssignmentConstraintKinds, ExactAssignmentCompletionSignalOrderKey, FindAllUnavoidableMandatoryClaimCuts, FrozenComponentBlockedWireNodes, ImmutableRoutingClaimsBlockedWireNodes, FindPriorCandidateDomainPairExpansion, FindUnavoidableMandatoryClaimCut, GenerateStagedInitialRouteTrees, HasRepeatedExactPairCut, HasCoveredPairCutAfterEndpointExpansion, MayAdvanceStagedCandidateOnExhaustion, RawPortalProfileMatchesRequestedControls, RetainNegotiatedInitialCandidateOption, SelectExactAssignmentCompletionCutWideRequests, SelectPendingExactAssignmentCompletionRequestIndices, SelectExactAssignmentCompletionRequestBatch, SelectExactAssignmentCompletionReserveMilliseconds, ShouldContinueDistinctExactCutFrontier, ShouldRejectRoutedComponentForeignEscape, SelectCandidateRealizabilityProbeSliceSeconds, ShouldContinueUniqueAccessDistinctCandidateRealizabilityProof, ShouldHandoffContinuedCandidateRealizabilityCut, SelectCandidateDomainPairScanSliceSeconds, SelectClusterLeaseOwnershipSignals, SelectCoordinatedCandidateExpansionLimit, SelectEffectiveCoordinatedCandidateDiversityLevel, SelectCoordinatedContinuationRequestWindowLimit, SelectCoordinatedInitialRequestWindowLimit, SelectMaturePortfolioExactInitialRequestFloor, SelectMaturePortfolioPortalLimit, SelectNegotiatedExpandedRequestMinimumExpansionCount, SelectNegotiatedOffenderHaloLaneDiversityLevel, SelectCoordinatedPortalVariantCount, SelectOptionalPortalSeedSliceSeconds, SelectTransactionalLeasePrescreenSignals, SelectJointHigherOrderConstraintSignals, SelectJointPairwiseConstraintSignals, ShouldRetainUnaffectedCandidatesForControl, ShouldPrepareOptionalPortalSeed, ShouldPrepareMandatoryPortalTuples, ShouldLimitRetainedPortfolioPortalDomain, ShouldRetainBoundedPortfolioPortalProfile, ShouldCapMatureCumulativeJointPortfolio, ShouldStageTopologyPressureJointPortfolio, ShouldScanCandidateDomainPairCut, ShouldAdvanceRetainedJointPortfolioOnCandidateStarvation, ShouldAdvanceTopologyCutEpochOnCandidateStarvation, ShouldAdvanceAfterCompleteClusterLeasePortfolio, ShouldDiversifyStarvedCompleteClusterLeaseEndpoint, ShouldContinueCutScopedFixedLegalityWindow, ShouldContinueSoleRetainedCutCandidateStarvation, ShouldExpandNegotiatedOffenderHalo, HasCumulativeJointAssignmentConstraintMaturity, ShouldRetryRelocatedCandidateStarvation, ShouldRetryCompleteClusterLeaseStateBeforePlacement, ShouldRefineCandidateRealizabilityLeaseNogood, ShouldUseNegotiatedRouting, ShouldUseMatureStagedInitialCandidateScheduler

from PhysicalDesign.Routing.Global.Candidates.CandidateGuides import ClassifySiblingApertureSeamOwnershipConflicts, MergePhysicalSignalRouteDomainDescriptorProgress, RetainPhysicalSignalRouteDomainDescriptorProgress, SelectPendingPhysicalRouteDescriptorRows, BuildCapacityAwareGuideInputFingerprint, BuildPhysicalAssemblyGuideContractFingerprint, BuildFactorizedPhysicalGuideIdentity, BuildCertifiedPhysicalComponentApertureDomain, BuildPhysicalSignalApertureCandidateDomainIdentity, BuildMinimalPhysicalRequestApertureNoGood, BuildCompletePhysicalRequestAlternativeApertureNoGoods, CompletePhysicalCandidatePairDomainsHaveNoSupport, PhysicalSignalLocalCandidateRequestFactorProofComplete, FilterPhysicalCandidatesAgainstSiblingApertures, PhysicalSignalRouteDomainContinuation, PhysicalSignalRouteDomainIsCertifiedEmpty, BuildPortablePhysicalSignalRouteDomainIdentity, PreparePortablePhysicalSignalRouteDomain, SelectPreparedPortablePhysicalSignalRouteDomainContinuation, RetainCompletePortablePhysicalSignalRouteDomains, SelectPortablePhysicalSignalRouteDomainContinuation, RetainPortablePhysicalSignalRouteDomainContinuation, SelectPortableReplayTelemetryReason, SelectReplayablePhysicalSignalRouteDomainContinuation, RetainCompletePhysicalSignalRouteDomainContinuations, BuildPhysicalPortCorridorArcSupportIndex, BuildPhysicalPortCorridorDomain, CaptureCompletePhysicalPortCorridorDomains, BuildPreparedPhysicalExteriorGuideColumnsBySignal, BuildPhysicalPortCorridorFactor, BuildPhysicalGlobalPlanContinuationState, BuildPhysicalGlobalPlanYieldDeadline, PropagatePhysicalPortCorridorArcConsistency, SelectReusablePhysicalPortCorridorCandidates, RetainIncompletePhysicalGlobalPlan, SelectNextRetainedPhysicalGlobalPlan, ShouldScheduleRetainedPhysicalGlobalPlan

from PhysicalDesign.Routing.Global.Ports.ExteriorConnectors import BuildPhysicalExteriorConnectorDistanceField, FrozenPhysicalExteriorConnectorSearchRequest, SelectPhysicalExteriorConnectorPath, SearchFrozenPhysicalExteriorConnectorBatch

from PhysicalDesign.Routing.Global.Materialization import SelectComponentPreparationProfiles

from PhysicalDesign.Routing.Global.Negotiation.NegotiatedTrees import PlanNegotiatedRouteTrees

from PhysicalDesign.Routing.Global.Guides.PhysicalGuides import BuildPhysicalExteriorResourceGraphFingerprint, DecomposePhysicalPortLaneFactors, PreparePhysicalSignalLocalFactorDomain, MaterializeSupportedPhysicalPortReservation, MaterializePhysicalPortFactorPair, ShouldBuildCapacityAwareGlobalGuidePlan, CanReuseFrozenPhysicalPortGuidePlan

from PhysicalDesign.Routing.Global.Ports.Portals import BuildRepeaterReadyPortalDomains, ApplyPhysicalComponentAssemblyPortalDomains, ApplyPlacementAccessFabricPortalDomains, SelectGenericPortalTerminalPaths, RequiredPhysicalAssemblyRoutingLayerCount, RequiredRoutingLayerCountForAccess, SelectEscalatedRoutingLayerCount, SelectGraphAccessStarts, PortalPathRespectsOutwardAccess, SelectInitialRoutingLayerCount, SelectHierarchicalRoutingMaximumLayerCount, ValidatePhysicalAssemblyRoutingLayerLimit, ValidatePhysicalComponentExactAttachmentPortals, ShouldRetryNegotiatedExactAssignment, FilterSourceConnectedTargetBranches, _MaterializeCandidate, _ReserveRepeaters

from PhysicalDesign.Routing.Global.Orchestration.RunModels import ClusterLeaseCandidateRealizabilityNogood, MandatoryPortalTupleSelfConflictEvidence, OptionalPortalSeedSliceExpired

from PhysicalDesign.Routing.Global.Assignment.TrackPortfolio import BuildCandidateRequestGeometryIdentity, BuildPhysicalCandidateRequestShapeDependencyIdentity, BuildInvariantRouteRequestGuidePayload, BuildInvariantRouteRequestNodePayload, BuildForeignElectricalExclusionsBySignal, BuildDetachedLocalClaimObstacleNodes, PartitionLocalClaimSeedComponents, BuildRoutingConflictGraph, BuildSeamOnlyPhysicalComponentPortReservation, PhysicalPortPathsOwnExclusiveSeam, PhysicalRouteRequestFactorHasNecessaryConnectivity, InterleavePhysicalPortSeamsByEgressClass, MergeSignalScopedAvoidancePositions, RetainPartialAssignmentCandidateCache, SelectAuthoritativeRouteRequestGuide, SelectCandidateRegenerationSignals, SelectCandidateRegenerationCoverSignals, SelectAnonymousMinimumFailurePairRelocationSignals, BuildAnonymousCandidateDomainFingerprint, SelectPriorityPlacementRelocationSignals, SelectConflictAvoidancePositions, SelectPartialAssignmentAvoidancePositions, SelectPartialAssignmentBlockerSignals, ShouldFreezePartialAssignmentForExactCut, ShouldRegenerateNewExactConflictSignals, ShouldReleaseFrozenPartialAssignment, _BuildTargetPortalBranches

from PhysicalDesign.Routing.Regions.Proofs.Validation import BuildPhysicalPortApertureContractFingerprint

from PhysicalDesign.Contracts.PhysicalInterface import PhysicalGlobalPlanResumeCursor

from PhysicalDesign.Redstone.Rules import PropagateRoutePower

from PhysicalDesign.Routing.Planning.ChannelPlanner import NetRoutingProfile

from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingFailure, RoutingFailureReason, RoutingStageError

from PhysicalDesign.Contracts.Placement import ClusterInterfaceRealizabilityNogood, ClusterInterfaceStateProof, PlacementAccessEscapeStub, PlacementAccessFabric, PlacementAccessTerminalDomain

from PhysicalDesign.Contracts.Component import PhysicalComponentPortReservation, PhysicalComponentBoundaryPortReservation, PhysicalComponentChannelReservation

from PhysicalDesign.Contracts.PhysicalInterface import PhysicalPortApertureOptionFactor, PhysicalLocalPortPairProofRecord, PhysicalPortLaneFactor, PhysicalPortSeamFactor, FrozenPhysicalComponentPostClosurePortalHandoff

from PhysicalDesign.Contracts.Results import RoutingResources

from PhysicalDesign.Contracts.Core import RoutingStaticGeometry

from PhysicalDesign.Constraints.BoundaryRelations import BuildPhysicalBoundaryMandatoryPortalFactorDomains, BuildPhysicalComponentGlobalPortalId, BuildPhysicalPortGlobalContractFingerprint, BuildRawPortalPlacementGeometryFingerprint, BuildRawPortalResourceGeometryFingerprint, CompilePhysicalBoundaryMandatoryPortalPairRelation, GetMandatoryPortalPairFeasibilityCertificate, PhysicalBoundaryMandatoryPortalFactorDomain, RawPortalGeometryCache, SelectCertifiedMandatoryPortalPairCuts, SolveMandatoryPortalPairFeasibility

from PhysicalDesign.Constraints.PhysicalClaims import ClaimConflictPositions, MandatoryClaimsConflict, PortalTupleConflictsWithFrozenComponentClaims

from PhysicalDesign.Constraints.PortalConstraints import ExactPortalConstraintAssignmentSatisfiesFactors, ExactPortalConstraintChoice, ExactPortalConstraintVariableDomain, ExtractExactPortalConstraintFactors, ExtractSparseExactPortalConstraintFactors, ProjectExactPortalConstraintFactors

from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources

from PhysicalDesign.Routing.Pcb import PrepareRawTrackAssignmentDomain, PrepareTrackAssignment, RoutePcbDesign

from PhysicalDesign.Routing.Regions.Proofs.Certification import BuildPhysicalLocalPortPairSupportCertificate

from PhysicalDesign.Policy import DefaultPhysicalDesignPolicy, LocalFirstPhysicalDesignPolicy

from PhysicalDesign.Runtime.Reliability import RoutingDeadline

from PhysicalDesign.Resources.ResourceGraph import IndexedRoutingResourceGraph, LocalRouteClaim, NetRouteCandidate, PinAccessPortal, RoutingResourceClaims, RoutingResourceId, RoutingResourceKind, RoutingResourceGraph, RoutingGraphRegion

from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology

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

class AuthoritativePlannerTestBase(unittest.TestCase):
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

    def BuildPortal(self, Signal, Terminal, Position, Layer=0):
        Claims = RoutingResourceClaims(WireCells=frozenset((Position,)))
        return PinAccessPortal(
            PortalId=f"{Signal}:{Position}", Signal=Signal, Terminal=Terminal,
            Layer=Layer, Path=(Position,), Edges=frozenset(), Claims=Claims,
            Length=0, BendCount=0, ViaCount=0, Cost=0,
        )

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

__all__ = tuple(Name for Name in globals() if not Name.startswith("__"))

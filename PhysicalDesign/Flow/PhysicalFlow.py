"""One bounded phase of the placement and routing flow."""

from __future__ import annotations

from dataclasses import replace
from itertools import islice
from typing import Any, Iterable
from PhysicalDesign.Routing.Pcb import PreparePhysicalComponentEligibility, SolvePreparedPhysicalComponentEligibility
from PhysicalDesign.Contracts.Placement import ClusterInterfacePortfolioProblem, ClusterInterfacePortfolioStateAudit, ClusterInterfacePlacementState, ClusterInterfaceRealizabilityNogood, ClusterInterfaceStateProof
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Execution.Reliability import BuildStableFingerprint, RoutingDeadline
from PhysicalDesign.Placement.Core.Clusters import BuildBoundedInterClusterRoutingChannel, BuildBoundedInterClusterRoutingDeck
from PhysicalDesign.Routing.Regions.Proofs.NoGoods import RecordPhysicalComponentDetailedRoutingNoGood, RecordPhysicalComponentLocalCompilationNoGood, RecordPhysicalComponentSymbolicCapacityEligibilityNoGood
from PhysicalDesign.Routing.Regions.Pipeline import AssembleClosedComponentForGlobalRouting, CompileClosedComponent
from PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains import CompilePhysicalComponentForeignPortalUnaryApertureClauses, CompilePhysicalComponentSymbolicPortPairDomain, CompilePhysicalComponentSymbolicUnaryApertureDomain, ProjectCompletePhysicalPortPairCertificateToApertureClauses, ProveClosedComponentSymbolicCapacityEligibility
from .Candidates import BuildClusterInterfaceStageSchedule, BuildComponentAccessFeedbackPlacementScore, BuildLocalComponentCompilationAdmissionFailure, BuildPhysicalAssemblyPlanningIncompleteFailure, BuildRetainedComponentPlacementSearchDomain, HasDistinctRetainedPhysicalEligibilityState, PcbPlacementCandidate, QueuedPhysicalEligibilityPlacementFingerprints, ReuseRetainedPlacementRoutingResources, SelectFocusedPlacementInterfacePressureSignals
from .Feedback import BuildPlacementFingerprint, SelectInterfaceDiversePlacementStates
from .Preparation import BuildClusterInterfaceComponentStateFingerprint, BuildClusterInterfacePlacementTopologyFingerprint, BuildClusterInterfaceUnsatProof, BuildPlacementRetentionFingerprint
from .Portfolios import (
    ApplyCoordinatedCandidateDiversificationProfile,
    SelectExhaustedRepeaterAccessCutSignals,
)
from .Results import BuildCapacityRepairEndpointClosureClusters, BuildCapacityRepairGeometryFingerprint, BuildComponentRoutabilityCore, BuildPhysicalInterfaceRepairCore, BuildPhysicalOwnedFrontierTopologyRepairCore, BuildSymbolicCapacityRepairEvidence, FreezePhysicalAssemblyGlobalChannels, IsClusterInterfaceStateIncomplete, IsCompletePhysicalAssemblyUnsatisfiable, IsComponentKeepoutGlobalFailure, PhysicalComponentPlacementFeedback, PreparedEligibilityHasDisjointCapacitySeams
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
)
from .AttemptHistory import (
    CapturePortableRawPortalGeometryCaches,
    SeedPortableRawPortalGeometryCaches,
)
from .PhysicalAssembly import (
    AdmitSymbolicLocalCapacity,
    BuildOwnedFrontierTopologyRepairDomainFingerprint,
    EnqueueOwnedFrontierTopologyRepair,
    EnqueueProofGuidedPhysicalPlacement,
    EnqueueSingletonLocalFactorDiversification,
    ProveUnboundOwnedSignalFrontier,
    RecordPhysicalComponentStageTiming,
    ReorderRemainingPlacementsForAccessCore,
    ReplanPhysicalAssemblyWithTiming,
    ReserveAuthoritativeGlobalChannels,
    SelectCompletePhysicalEligibilityRepairEndpointGateNames,
)
from .RoutingAttempts import (
    MaterializeSelectedJointPlacementLocalRouting,
)


def RunPhysicalComponentFlow(Context):
    Context.CandidateRoutingIterable: Iterable[PcbPlacementCandidate] = Context.OrderedPlacements[:1]
    if Context.ExactClusterInterfaceSolveEnabled:
        Context.TopologyDrivenRetainedCandidates = tuple((Candidate for Candidate in Context.OrderedPlacements if Candidate.TopologyDemand is not None and Candidate.TopologyDemand.RequiresJointPortfolio))
        if Context.InterfaceStateCountBound > 6:
            Context.JointStateCandidates = tuple((Candidate for Candidate in Context.TopologyDrivenRetainedCandidates if Candidate.JointPlacementState is not None))
            if Context.JointStateCandidates:
                Context.TopologyDrivenRetainedCandidates = Context.JointStateCandidates
            Context.RawInterfaceCandidates = tuple(sorted(Context.TopologyDrivenRetainedCandidates, key=lambda Candidate: (str(Candidate.InterfaceTopologyFingerprint), Candidate.PlacementFingerprint)))[:Context.InterfaceStateCountBound]
            if len(Context.RawInterfaceCandidates) < Context.InterfaceStateCountBound:
                Context.SupplementalCandidates: list[PcbPlacementCandidate] = []
                Context.KnownFingerprints = {Candidate.PlacementFingerprint for Candidate in Context.RawInterfaceCandidates}
                for Context.Candidate in islice(Context.CandidateRoutingIterable, Context.InterfaceStateCountBound - len(Context.RawInterfaceCandidates)):
                    if Context.Candidate.PlacementFingerprint in Context.KnownFingerprints or Context.Candidate.TopologyDemand is None or (not Context.Candidate.TopologyDemand.RequiresJointPortfolio):
                        continue
                    Context.SupplementalCandidates.append(Context.Candidate)
                    Context.KnownFingerprints.add(Context.Candidate.PlacementFingerprint)
                Context.RawInterfaceCandidates = (Context.RawInterfaceCandidates + tuple(Context.SupplementalCandidates))[:Context.InterfaceStateCountBound]
        else:
            Context.RawInterfaceCandidates = (min(Context.TopologyDrivenRetainedCandidates, key=lambda Candidate: (str(Candidate.InterfaceTopologyFingerprint), Candidate.PlacementFingerprint)),) if Context.TopologyDrivenRetainedCandidates else tuple(islice(Context.CandidateRoutingIterable, Context.InterfaceStateCountBound))
        Context.InterfaceCandidates, Context.InterfacePortfolioAudits = SelectInterfaceDiversePlacementStates(Context.RawInterfaceCandidates, MaximumStates=Context.InterfaceStateCountBound)
        Context.InterfaceCandidates = tuple(sorted(Context.InterfaceCandidates, key=lambda Candidate: (BuildComponentAccessFeedbackPlacementScore(Candidate, (Request.Signal for Request in Candidate.Placement.ClusterBoundaryLeaseRequests or Candidate.Placement.Placed.ClusterBoundaryLeaseRequests or ())), Candidate.JointExactScore if Candidate.JointExactScore else (10 ** 18,), Candidate.BoundaryOverflow, Candidate.PinScarcityCount, Candidate.GuideOverflowPeak, Candidate.GuideOverflowCells, Candidate.PlacementFingerprint)))
        Context.InterfaceGeneratorRejectionAudit: list[dict[str, object]] = []
        for Context.Decision in Context.PlacementGenerationDecisions:
            Context.Result = str(Context.Decision.get('Result', ''))
            Context.Classification = {'duplicate-placement': 'duplicate-access-topology', 'rejected-mandatory-access-conflict': 'mandatory-access-unsat', 'rejected-packed-area-growth': 'pruned-by-scoring-budget', 'skipped-routing-reserve': 'pruned-by-scoring-budget'}.get(Context.Result)
            if Context.Classification is None:
                continue
            Context.InterfaceGeneratorRejectionAudit.append({'Classification': Context.Classification, 'SourceResult': Context.Result, 'CandidateIndex': Context.Decision.get('JointPlacementCandidateIndex'), 'PlacementFingerprint': Context.Decision.get('PlacementFingerprint', ''), 'PlacementRetentionFingerprint': Context.Decision.get('PlacementRetentionFingerprint', ''), 'Detail': 'classified before exact interface portfolio retention'})
        for Context.FailureEntry in Context.PlacementGenerationFailures:
            Context.FailureDiagnostics = Context.FailureEntry.get('Diagnostics', {})
            Context.FailureReason = Context.FailureDiagnostics.get('Reason', '') if isinstance(Context.FailureDiagnostics, dict) else ''
            if Context.FailureReason != RoutingFailureReason.PlacementOverlap.value:
                continue
            Context.InterfaceGeneratorRejectionAudit.append({'Classification': 'geometric-overlap-illegal-placement', 'SourceResult': 'placement-generation-failure', 'CandidateIndex': Context.FailureEntry.get('JointPlacementCandidateIndex'), 'Detail': Context.FailureEntry.get('Failure', '')})
        for Context.Candidate in Context.RawInterfaceCandidates:
            Context.CandidateJointDiagnostics = dict(Context.Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__JointClusterPlacement__', {})
            if not isinstance(Context.CandidateJointDiagnostics, dict):
                continue
            for Context.Attrition in Context.CandidateJointDiagnostics.get('InterfacePortfolioAttrition', ()):
                if not isinstance(Context.Attrition, dict):
                    continue
                Context.Record = {'SearchCandidateIndex': Context.Attrition.get('SearchCandidateIndex'), 'Classification': Context.Attrition.get('Classification', 'pruned-by-scoring-budget'), 'SourceResult': 'joint-interface-portfolio-attrition', 'PlacementFingerprint': '', 'PlacementRetentionFingerprint': Context.Attrition.get('InterfaceOwnershipFingerprint', ''), 'Detail': 'classified during exact joint placement screen'}
                if Context.Record not in Context.InterfaceGeneratorRejectionAudit:
                    Context.InterfaceGeneratorRejectionAudit.append(Context.Record)
        Context.ResolvedPortfolioAudits = list(Context.InterfacePortfolioAudits)
        for Context.Rejection in Context.InterfaceGeneratorRejectionAudit:
            Context.CandidateIndex = Context.Rejection.get('CandidateIndex')
            if not isinstance(Context.CandidateIndex, int):
                continue
            Context.ResolvedPortfolioAudits = [Audit for Audit in Context.ResolvedPortfolioAudits if not (Audit.StateIndex == Context.CandidateIndex and (not Audit.PlacementStateFingerprint))]
            if any((Audit.StateIndex == Context.CandidateIndex for Audit in Context.ResolvedPortfolioAudits)):
                continue
            Context.ResolvedPortfolioAudits.append(ClusterInterfacePortfolioStateAudit(StateIndex=Context.CandidateIndex, Classification=str(Context.Rejection['Classification']), InterfaceTopologyFingerprint=str(Context.Rejection.get('PlacementRetentionFingerprint', '')), Detail=str(Context.Rejection.get('Detail', ''))))
        Context.InterfaceSearchStateCount = Context.InterfaceStateCountBound
        for Context.Candidate in Context.RawInterfaceCandidates:
            Context.CandidateJointDiagnostics = dict(Context.Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__JointClusterPlacement__', {})
            if isinstance(Context.CandidateJointDiagnostics, dict):
                Context.InterfaceSearchStateCount = max(Context.InterfaceSearchStateCount, int(Context.CandidateJointDiagnostics.get('SearchRetentionLimit', 0)))
        Context.ClassifiedSearchIndexes = {Audit.StateIndex for Audit in Context.ResolvedPortfolioAudits}
        for Context.SearchCandidateIndex in range(Context.InterfaceSearchStateCount):
            if Context.SearchCandidateIndex in Context.ClassifiedSearchIndexes:
                continue
            Context.ResolvedPortfolioAudits.append(ClusterInterfacePortfolioStateAudit(StateIndex=Context.SearchCandidateIndex, Classification='pruned-by-scoring-budget', Detail=f'bounded generator stopped after {Context.InterfaceSearchStateCount} legal interface-distinct states'))
        Context.InterfacePortfolioAudits = tuple(sorted(Context.ResolvedPortfolioAudits, key=lambda Audit: (Audit.StateIndex, Audit.Classification)))
        Context.InterfaceFeasibleCandidates: list[tuple[tuple[object, ...], PcbPlacementCandidate, Any]] = []
        Context.InterfaceAttemptDiagnostics: list[dict[str, object]] = []
        Context.InterfaceStateProofs: list[ClusterInterfaceStateProof] = []
        Context.InterfacePlacementStatesByFingerprint: dict[str, ClusterInterfacePlacementState] = {}
        Context.ActiveComponentCutSignals: set[str] = set()
        Context.InterfaceSolveIncompleteError: RoutingStageError | None = None
        Context.LastGlobalHandoffError: RoutingStageError | None = None
        Context.MaximumComponentVariants = 1
        Context.MaximumProofGuidedRetainedPlacements = 0
        Context.MaximumProofGuidedGeneratedPlacements = 4
        Context.MaximumProofGuidedSymbolicCapacityPairPlacements = 3
        Context.ComponentPlacementSearchDomain = BuildRetainedComponentPlacementSearchDomain((Candidate.PlacementFingerprint for Candidate in Context.InterfaceCandidates), MaximumComponentSelections=Context.MaximumComponentVariants)
        Context.RequestedComponentStateFingerprints: set[str] = {BuildClusterInterfaceComponentStateFingerprint(PlacementFingerprint, ComponentVariant) for ComponentVariant, _PlacementIndex, PlacementFingerprint in Context.ComponentPlacementSearchDomain}
        Context.InterfaceStageSchedule = BuildClusterInterfaceStageSchedule(Context.Deadline, (BuildClusterInterfaceComponentStateFingerprint(PlacementFingerprint, ComponentVariant) for ComponentVariant, _PlacementIndex, PlacementFingerprint in Context.ComponentPlacementSearchDomain), LocalCompilationReserveSeconds=min(5.0, Context.Policy.AdaptiveRouting.MaximumRuntimeSeconds), GlobalRoutingReserveSeconds=max(0.0, Context.Policy.MaterialObjective.MinimumRemainingRoutingPercentageSearchSeconds), PublicationReserveSeconds=0.0)
        Context.SharedInterfaceDeadline = RoutingDeadline(StartedAt=Context.Deadline.StartedAt, ExpiresAt=Context.InterfaceStageSchedule.ExpiresAt, ExpirationKind='StageReserveExpired')
        Context.SharedInterfacePlanningDeadline = RoutingDeadline(StartedAt=Context.Deadline.StartedAt, ExpiresAt=Context.InterfaceStageSchedule.PlanningExpiresAt, ExpirationKind='StageReserveExpired')
        Context.ProofGuidedInterfacePlanningDeadline = RoutingDeadline(StartedAt=Context.Deadline.StartedAt, ExpiresAt=Context.InterfaceStageSchedule.ProofGuidedPlanningExpiresAt, ExpirationKind='StageReserveExpired')
        Context.AccessRepairInterfacePlanningDeadline = RoutingDeadline(StartedAt=Context.Deadline.StartedAt, ExpiresAt=Context.InterfaceStageSchedule.AccessRepairPlanningExpiresAt, ExpirationKind='StageReserveExpired')
        Context.AccessRepairInterfaceDeadline = RoutingDeadline(StartedAt=Context.Deadline.StartedAt, ExpiresAt=Context.InterfaceStageSchedule.AccessRepairExpiresAt, ExpirationKind='StageReserveExpired')
        Context.PrimaryTransforms: dict[object, object] = {}
        Context.InterfaceCandidateQueue = [('prepare-eligibility', InterfaceIndex, Context.InterfaceCandidates[InterfaceIndex], 0, ComponentVariant) for ComponentVariant, InterfaceIndex, _PlacementFingerprint in Context.ComponentPlacementSearchDomain]
        Context.SeenComponentSelectionsByPlacement: dict[str, set[str]] = {}
        Context.RoutingResourcesByRetainedPlacementFingerprint: dict[str, Any] = {}
        Context.ComponentVariantPortfolioCache: dict[Any, Any] = {}
        Context.ComponentNetVariantConstructionCache: dict[Any, Any] = {}
        Context.ComponentRouteClaimsConstructionCache: dict[Any, Any] = {}
        Context.ComponentNetVariantDiscoveryStateCache: dict[Any, Any] = {}
        Context.ComponentOwnedSignalFrontierProofCache: dict[str, Any] = {}
        Context.ComponentSymbolicCapacityProofCache: dict[str, Any] = {}
        Context.PhysicalGlobalApertureTemplateCache: dict[str, Any] = {}
        Context.PhysicalLocalSeamEligibilityCache: dict[str, bool] = {}
        Context.PhysicalBoundaryPairSupportCache: dict[str, bool] = {}
        Context.PreferredPhysicalComponentPortUnsatCoreSignals: tuple[str, ...] = ()
        Context.PreparedEligibilityByState: dict[tuple[int, str], Any] = {}
        Context.PhysicalComponentStageTimingsByState: dict[tuple[int, str], list[dict[str, object]]] = {}
        Context.ProofGuidedPlacementFingerprints: set[str] = set()
        Context.GeneratedProofGuidedPlacementFingerprints: set[str] = set()
        Context.ProofGuidedGenerationSourceByPlacementFingerprint: dict[str, tuple[RoutingFailure, PcbPlacementCandidate, int]] = {}
        Context.ProofGuidedRelocationCoreCounts: dict[tuple[str, ...], int] = {}
        Context.RepeatedOwnershipCoreAttempts: dict[tuple[str, str], int] = {}
        Context.CumulativeProofGuidedRelocationSignals: set[str] = set()
        Context.ProofGuidedPlacementPressureEdges: set[tuple[str, str]] = set()
        Context.AppliedProofGuidedPlacementPressureEdges: set[tuple[str, str]] = set()
        Context.ProofGuidedPlacementGenerationCount = 0
        Context.ProofGuidedPlacementGenerationCountByCore: dict[tuple[str, ...], int] = {}
        Context.ProofGuidedRetainedPlacementCount = 0
        Context.PendingProofGuidedPlacementByComponentVariant: dict[int, tuple[RoutingFailure, PcbPlacementCandidate, bool, frozenset[str], bool]] = {}
        Context.CapacityRepairConstraintByPlacementFingerprint: dict[str, PhysicalInterfaceRepairCore] = {}
        Context.CapacityRepairGeometryConstraintByPlacementFingerprint: dict[str, PhysicalInterfaceRepairCore] = {}
        Context.CapacityRepairGeometryFocusByPlacementFingerprint: dict[str, PhysicalComponentPlacementFeedback] = {}
        Context.CapacityRepairPortfolioDiagnostics: list[dict[str, object]] = []
        Context.CapacityRepairGeneratedCountByProofFingerprint: dict[str, int] = {}
        Context.CapacityRepairGeometryKindByPlacementFingerprint: dict[str, str] = {}
        Context.CapacityRepairCandidateByPlacementFingerprint: dict[str, PcbPlacementCandidate] = {}
        Context.CapacityRepairEquivalentGeometryRejectsByProofFingerprint: dict[str, int] = {}
        Context.DequeuedCapacityRepairPlacementFingerprints: set[str] = set()
        Context.LocalFactorDiversificationPortfolioDiagnostics: list[dict[str, object]] = []
        Context.LocalFactorDiversificationRepairKeyByPlacementFingerprint: dict[str, tuple[object, ...]] = {}
        Context.LocalFactorDiversificationNextSignalByRepairKey: dict[tuple[object, ...], str] = {}
        Context.LearnedLocalFactorTransitionPrefetchFingerprints: set[str] = set()
        Context.LocalFactorDiversificationAttemptCountByProofFingerprint: dict[str, int] = {}
        Context.LocalFactorDiversificationCandidateByPlacementFingerprint: dict[str, PcbPlacementCandidate] = {}
        Context.LocalFactorDiversificationSignalByPlacementFingerprint: dict[str, str] = {}
        Context.LocalFactorDiversificationSignalLineageByPlacementFingerprint: dict[str, tuple[str, ...]] = {}
        Context.LocalFactorDiversificationSiblingGroupByPlacementFingerprint: dict[str, str] = {}
        Context.OwnedFrontierTopologyRepairPortfolioDiagnostics: list[dict[str, object]] = []
        Context.OwnedFrontierTopologyRepairAttemptCountByDomainFingerprint: dict[str, int] = {}
        Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint: dict[str, PcbPlacementCandidate] = {}
        Context.OwnedFrontierTopologyRepairKindByPlacementFingerprint: dict[str, str] = {}
        Context.OwnedFrontierTopologyRepairSignalsByPlacementFingerprint: dict[str, tuple[str, ...]] = {}
        Context.OwnedFrontierTopologyRepairEquivalentRejectsByProofFingerprint: dict[str, int] = {}
        while Context.InterfaceCandidateQueue or Context.PendingProofGuidedPlacementByComponentVariant:
            Context.QueuedCapacityRepairSolveFingerprints = frozenset(
                Candidate.PlacementFingerprint
                for Phase, _Index, Candidate, _CutEpoch, _Variant
                in Context.InterfaceCandidateQueue
                if Phase == 'solve-prepared-eligibility'
                and Candidate.PlacementFingerprint
                in Context.CapacityRepairConstraintByPlacementFingerprint
            )
            for Context.PendingComponentVariant in sorted(tuple(Context.PendingProofGuidedPlacementByComponentVariant)):
                Context.PendingFailure, Context.PendingSourceCandidate, Context.PendingRequiresRetainedDrain, Context.PendingBlockerPlacementFingerprints, Context.PendingCapacityRepair = Context.PendingProofGuidedPlacementByComponentVariant[Context.PendingComponentVariant]
                Context.QueuedEligibilityPlacementFingerprints = QueuedPhysicalEligibilityPlacementFingerprints(Context.InterfaceCandidateQueue)
                Context.CompletedEligibilityPlacementFingerprints = frozenset(str(Attempt.get('PlacementFingerprint', '')) for Attempt in Context.InterfaceAttemptDiagnostics if str(Attempt.get('PlacementFingerprint', '')))
                Context.PendingUnresolvedBlockerPlacementFingerprints = Context.PendingBlockerPlacementFingerprints - Context.CompletedEligibilityPlacementFingerprints
                Context.PlacementGenerationDecisions.append({'Result': 'proof-guided-pending-admission', 'ComponentVariant': Context.PendingComponentVariant, 'SourceCandidateId': Context.PendingSourceCandidate.CandidateId, 'CapacityRepair': Context.PendingCapacityRepair, 'RequiresRetainedDrain': Context.PendingRequiresRetainedDrain, 'BlockerPlacementFingerprints': sorted(Context.PendingBlockerPlacementFingerprints), 'CompletedPlacementFingerprints': sorted(Context.CompletedEligibilityPlacementFingerprints), 'UnresolvedBlockerPlacementFingerprints': sorted(Context.PendingUnresolvedBlockerPlacementFingerprints), 'QueuedEligibilityPlacementFingerprints': sorted(Context.QueuedEligibilityPlacementFingerprints), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                if (
                    Context.PendingCapacityRepair
                    and Context.QueuedCapacityRepairSolveFingerprints
                ):
                    Context.PlacementGenerationDecisions.append({
                        'Result': 'proof-guided-pending-capacity-solve-prioritized',
                        'ComponentVariant': Context.PendingComponentVariant,
                        'SourceCandidateId': Context.PendingSourceCandidate.CandidateId,
                        'QueuedCapacityRepairSolveFingerprints': sorted(
                            Context.QueuedCapacityRepairSolveFingerprints
                        ),
                        'ElapsedSeconds': round(
                            Context.Services.monotonic()
                            - Context.Deadline.StartedAt,
                            6,
                        ),
                    })
                    continue
                if (Context.PendingUnresolvedBlockerPlacementFingerprints & Context.QueuedEligibilityPlacementFingerprints) or (Context.PendingRequiresRetainedDrain and HasDistinctRetainedPhysicalEligibilityState(Context.InterfaceCandidateQueue, ComponentVariant=Context.PendingComponentVariant, PlacementFingerprint=Context.PendingSourceCandidate.PlacementFingerprint)):
                    continue
                del Context.PendingProofGuidedPlacementByComponentVariant[Context.PendingComponentVariant]
                EnqueueProofGuidedPhysicalPlacement(Context, Context.PendingFailure, Context.PendingSourceCandidate, Context.PendingComponentVariant)
            if not Context.InterfaceCandidateQueue:
                Context.AttemptedRepairPlacementFingerprints = {str(Attempt.get('PlacementFingerprint', '')) for Attempt in Context.InterfaceAttemptDiagnostics if str(Attempt.get('PlacementFingerprint', ''))}
                Context.AttemptedRepairPlacementFingerprints.update((str(Attempt.get('PlacementFingerprint', '')) for Attempt in Context.CapacityRepairPortfolioDiagnostics if Attempt.get('Result') in {'capacity-pair-repair-dequeued', 'bounded-proof-driven-repair-candidate-failed'} and str(Attempt.get('PlacementFingerprint', ''))))
                Context.UnattemptedCapacityRepairCandidates = tuple(((Fingerprint, Candidate) for Fingerprint, Candidate in sorted(Context.CapacityRepairCandidateByPlacementFingerprint.items(), key=lambda Value: (0 if Context.CapacityRepairGeometryKindByPlacementFingerprint.get(Value[0], '').startswith('widen-') else 1, Value[0])) if Fingerprint not in Context.AttemptedRepairPlacementFingerprints))
                if Context.UnattemptedCapacityRepairCandidates and (not Context.Deadline.IsExpired()):
                    Context.InterfaceCandidateQueue.extend((('prepare-eligibility', len(Context.InterfaceCandidates) + Index, Candidate, 0, 0) for Index, (_Fingerprint, Candidate) in enumerate(Context.UnattemptedCapacityRepairCandidates)))
                    Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-repair-unattempted-candidates-requeued', 'PlacementFingerprints': [Fingerprint for Fingerprint, _Candidate in Context.UnattemptedCapacityRepairCandidates], 'GeometryKinds': [Context.CapacityRepairGeometryKindByPlacementFingerprint.get(Fingerprint, '') for Fingerprint, _Candidate in Context.UnattemptedCapacityRepairCandidates], 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                    continue
                break
            Context.InterfaceWorkPhase, Context.InterfaceIndex, Context.InterfaceCandidate, Context.InterfaceCutEpoch, Context.ComponentVariantForState = Context.InterfaceCandidateQueue.pop(0)
            Context.RetainedBaseInterfaceCandidate = Context.InterfaceCandidate
            Context.RetainedPlacementFingerprint = Context.InterfaceCandidate.PlacementFingerprint
            Context.ComponentStateFingerprint = BuildClusterInterfaceComponentStateFingerprint(Context.RetainedPlacementFingerprint, Context.ComponentVariantForState)
            Context.EligibilityStateKey = (Context.ComponentVariantForState, Context.RetainedPlacementFingerprint)
            Context.PhysicalComponentStageTimings = Context.PhysicalComponentStageTimingsByState.setdefault(Context.EligibilityStateKey, [])
            Context.InterfaceDeadline = Context.SharedInterfacePlanningDeadline
            if Context.RetainedPlacementFingerprint in Context.GeneratedProofGuidedPlacementFingerprints:
                Context.InterfaceDeadline = Context.AccessRepairInterfacePlanningDeadline
            elif Context.RetainedPlacementFingerprint in Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint:
                Context.InterfaceDeadline = Context.ProofGuidedInterfacePlanningDeadline
            if Context.RetainedPlacementFingerprint in Context.CapacityRepairConstraintByPlacementFingerprint:
                Context.InterfaceDeadline = Context.AccessRepairInterfaceDeadline
            Context.StateRealizabilityNogoods: list[ClusterInterfaceRealizabilityNogood] = []
            Context.StateAssignmentFingerprints: list[str] = []
            Context.StateAttemptDiagnostics: list[dict[str, object]] = []
            Context.StateFrozenPatternFingerprints: dict[str, str] = {}
            Context.StateFrozenReservations: tuple[Any, ...] = ()
            Context.StateActiveComponentSignals: set[str] = set()
            Context.CapacityRepairWitnessReserved = False
            Context.CapacityRepairConstraint = Context.CapacityRepairConstraintByPlacementFingerprint.get(Context.RetainedPlacementFingerprint)
            Context.CapacityRepairGeometryConstraint = Context.CapacityRepairGeometryConstraintByPlacementFingerprint.get(Context.RetainedPlacementFingerprint) or Context.CapacityRepairConstraint
            Context.CapacityRepairGeometryFocus = Context.CapacityRepairGeometryFocusByPlacementFingerprint.get(Context.RetainedPlacementFingerprint)
            Context.OwnedFrontierTopologyRepairKind = Context.OwnedFrontierTopologyRepairKindByPlacementFingerprint.get(Context.RetainedPlacementFingerprint, '')
            Context.EffectiveComponentVariant = Context.ComponentVariantForState + (1 if Context.OwnedFrontierTopologyRepairKind == 'relocate-endpoint-cluster' else 0)
            if Context.CapacityRepairConstraint is not None:
                if Context.InterfaceWorkPhase == 'prepare-eligibility' and Context.RetainedPlacementFingerprint in Context.DequeuedCapacityRepairPlacementFingerprints:
                    Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-pair-repair-duplicate-dequeue-suppressed', 'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'SourceProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(Context.CapacityRepairConstraint.Signals), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                    continue
                if Context.InterfaceWorkPhase == 'prepare-eligibility':
                    Context.DequeuedCapacityRepairPlacementFingerprints.add(Context.RetainedPlacementFingerprint)
                    Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-pair-repair-dequeued', 'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'SourceProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(Context.CapacityRepairConstraint.Signals), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            Context.Transforms: dict[object, object] = {}
            Context.NormalizedTransforms: tuple[tuple[str, int, bool], ...] = ()
            Context.TransformFingerprint = ''
            Context.LocalRouteFingerprint = ''
            Context.ChannelFingerprint = ''
            Context.ComponentSelectionFingerprint = ''
            Context.Channel = None
            Context.SelectedComponentClusters = ()
            Context.ComponentProblem = None
            Context.ComponentSolve = None
            Context.ComponentTemplate = None
            Context.RoutedComponentHandoffEntered = False
            Context.RetainedPlacementResourceCacheHit = False
            try:
                Context.MaterializedInterfacePlacement = MaterializeSelectedJointPlacementLocalRouting(Context, Context.InterfaceCandidate, lambda Diagnostics, Candidate=Context.InterfaceCandidate: Context.InterfaceDeadline.RaiseIfExpired('ClusterInterfacePlacementMaterialization', {'CandidateId': Candidate.CandidateId, **Diagnostics}))
                if Context.MaterializedInterfacePlacement is not Context.InterfaceCandidate.Placement:
                    Context.InterfaceCandidate = replace(Context.InterfaceCandidate, Placement=Context.MaterializedInterfacePlacement)
                if Context.CapacityRepairConstraint is not None:
                    Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-pair-repair-local-materialized', 'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'SourceProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(Context.CapacityRepairConstraint.Signals), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                try:
                    Context.CapacityRepairPreferredSignals = Context.CapacityRepairConstraint.Signals if Context.CapacityRepairConstraint is not None else ()
                    Context.CapacityRepairRequiredComponentGateNames = Context.CapacityRepairConstraint.ComponentGateNames if Context.CapacityRepairConstraint is not None else ()
                    Context.CapacityRepairEndpointClosureClusters = BuildCapacityRepairEndpointClosureClusters(Context.MaterializedInterfacePlacement, Context.CapacityRepairConstraint, MaximumClusters=3) if Context.CapacityRepairConstraint is not None else ()
                    Context.PreviewInterfacePlacement = BuildBoundedInterClusterRoutingDeck(Context.MaterializedInterfacePlacement, TrackPitch=Context.Technology.TrackPitch, MaximumAffectedClusters=3, MaximumDeckLanes=12, InterfaceDeckLayer=3, ComponentVariant=Context.EffectiveComponentVariant, PreferredSignals=Context.CapacityRepairPreferredSignals, RequiredComponentGateNames=Context.CapacityRepairRequiredComponentGateNames, ForcedAffectedClusters=Context.CapacityRepairEndpointClosureClusters or None)
                    Context.PreviewChannel = Context.PreviewInterfacePlacement.InterClusterRoutingChannel
                    if Context.PreviewChannel is None:
                        raise ValueError('component envelope preview produced no channel')
                    Context.SelectedComponentClusters = tuple(Context.PreviewChannel.AffectedClusters)
                    Context.MaterializedInterfacePlacement = BuildBoundedInterClusterRoutingChannel(Context.MaterializedInterfacePlacement, TrackPitch=Context.Technology.TrackPitch * 2, MaximumAffectedClusters=3, MaximumBoundaryStrips=2, RoutingLayerCount=3, RequiredComponentGateNames=Context.CapacityRepairRequiredComponentGateNames, ForcedAffectedClusters=Context.SelectedComponentClusters, ChannelClearanceTracks=1 if Context.CapacityRepairConstraint is not None and Context.CapacityRepairConstraint.RepairLevel == 'channel-capacity' else 0, ChannelTopologyVariant=1 if Context.OwnedFrontierTopologyRepairKind == 'relocate-endpoint-cluster' else 0)
                    Context.MaterializedInterfacePlacement = BuildBoundedInterClusterRoutingDeck(Context.MaterializedInterfacePlacement, TrackPitch=Context.Technology.TrackPitch, MaximumAffectedClusters=3, MaximumDeckLanes=12, InterfaceDeckLayer=3, ComponentVariant=Context.EffectiveComponentVariant, PreferredSignals=Context.CapacityRepairPreferredSignals, RequiredComponentGateNames=Context.CapacityRepairRequiredComponentGateNames, ForcedAffectedClusters=Context.SelectedComponentClusters)
                except ValueError as Error:
                    raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceArchitectureUnsatisfiable, Stage='InterClusterRoutingChannelMaterialization', Detail=str(Error), RepairActions=(), Diagnostics={'CandidateId': Context.InterfaceCandidate.CandidateId, 'ComponentFabricConstructionComplete': True, 'ClusterInterfaceDomainComplete': True, 'OwnershipSearchComplete': True, 'BroadFallbackAllowed': False, 'ExecutableLegacyRepairCascade': False})) from Error
                Context.Channel = Context.MaterializedInterfacePlacement.InterClusterRoutingChannel
                Context.MissingCapacityRepairChannelSignals = tuple(sorted(
                    set(Context.CapacityRepairConstraint.Signals)
                    - set(getattr(Context.Channel, 'AffectedSignals', ()))
                )) if Context.CapacityRepairConstraint is not None and Context.CapacityRepairConstraint.RepairLevel == 'local-assembly' else ()
                if Context.MissingCapacityRepairChannelSignals:
                    raise RoutingStageError(RoutingFailure(
                        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
                        Stage='PhysicalCapacityRepairPrecheck',
                        AffectedNets=Context.CapacityRepairConstraint.Signals,
                        Detail='the repaired channel does not expose every signal in the complete symbolic capacity core',
                        Diagnostics={
                            'SymbolicCapacityPlacementFeedback': True,
                            'SymbolicCapacityProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint,
                            'PlacementInterfacePressureSignals': list(Context.CapacityRepairConstraint.Signals),
                            'MissingCapacityRepairChannelSignals': list(Context.MissingCapacityRepairChannelSignals),
                            'SelectedChannelSignals': sorted(map(str, getattr(Context.Channel, 'AffectedSignals', ()))),
                            'CapacityRepairConstraint': Context.CapacityRepairConstraint.ToDictionary(),
                            'PriorityCapacityPrecheck': True,
                            'GlobalPlanningEntered': False,
                            'LocalCompilationEntered': False,
                        },
                    ))
                Context.ChannelFingerprint = Context.Channel.ChannelFingerprint if Context.Channel is not None else ''
                Context.ComponentSelectionFingerprint = BuildStableFingerprint((getattr(Context.Channel, 'ComponentId', None), tuple(sorted(map(str, getattr(Context.Channel, 'AffectedSignals', ())))), tuple(sorted(map(int, getattr(Context.Channel, 'AffectedClusters', ()))))))
                Context.SeenComponentSelections = Context.SeenComponentSelectionsByPlacement.setdefault(Context.RetainedPlacementFingerprint, set())
                Context.CapacityRepairPlacementState = Context.CapacityRepairConstraint is not None
                if Context.InterfaceWorkPhase == 'prepare-eligibility' and (not Context.CapacityRepairPlacementState) and (Context.ComponentSelectionFingerprint in Context.SeenComponentSelections):
                    Context.EquivalentProof = next((Proof for Proof in reversed(Context.InterfaceStateProofs) if Proof.PlacementStateFingerprint == Context.RetainedPlacementFingerprint and Proof.ComponentSelectionFingerprint == Context.ComponentSelectionFingerprint and Proof.Exhaustive), None)
                    if Context.EquivalentProof is not None:
                        Context.InterfaceStateProofs.append(replace(Context.EquivalentProof, ComponentStateFingerprint=Context.ComponentStateFingerprint, ComponentVariant=Context.ComponentVariantForState, ComponentSelectionFingerprint=Context.ComponentSelectionFingerprint))
                        Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'ComponentStateFingerprint': Context.ComponentStateFingerprint, 'ComponentVariant': Context.ComponentVariantForState, 'ComponentSelectionFingerprint': Context.ComponentSelectionFingerprint, 'EquivalentProofComponentStateFingerprint': getattr(Context.EquivalentProof, 'ComponentStateFingerprint', ''), 'Result': 'duplicate-component-selection-proof-reused'})
                        continue
                if Context.InterfaceWorkPhase == 'prepare-eligibility':
                    Context.SeenComponentSelections.add(Context.ComponentSelectionFingerprint)
                Context.ChannelizedPlacementFingerprint = BuildPlacementFingerprint(Context.MaterializedInterfacePlacement, Context.InterfaceCandidate.TopologyDemand.MandatoryAccessOwnershipFingerprint if Context.InterfaceCandidate.TopologyDemand is not None else '')
                if Context.CapacityRepairConstraint is not None:
                    Context.CapacityRepairConstraintByPlacementFingerprint[Context.ChannelizedPlacementFingerprint] = Context.CapacityRepairConstraint
                    Context.CapacityRepairGeometryConstraintByPlacementFingerprint[Context.ChannelizedPlacementFingerprint] = Context.CapacityRepairGeometryConstraint
                    if Context.CapacityRepairGeometryFocus is not None:
                        Context.CapacityRepairGeometryFocusByPlacementFingerprint[Context.ChannelizedPlacementFingerprint] = Context.CapacityRepairGeometryFocus
                Context.InterfaceCandidate = replace(Context.InterfaceCandidate, CandidateId=f'ChannelPlacement-{Context.ChannelizedPlacementFingerprint[:12]}', PlacementFingerprint=Context.ChannelizedPlacementFingerprint, Placement=Context.MaterializedInterfacePlacement, PlacementRetentionFingerprint=BuildPlacementRetentionFingerprint(Context.MaterializedInterfacePlacement, Context.InterfaceCandidate.TopologyDemand.MandatoryAccessOwnershipFingerprint if Context.InterfaceCandidate.TopologyDemand is not None else ''), InterfaceTopologyFingerprint=BuildClusterInterfacePlacementTopologyFingerprint(Context.MaterializedInterfacePlacement, Context.SignalTopologyFingerprints))
                Context.ChannelizedEquivalentProof = next((Proof for Proof in reversed(Context.InterfaceStateProofs) if Proof.PlacementStateFingerprint == Context.ChannelizedPlacementFingerprint and Proof.ComponentSelectionFingerprint == Context.ComponentSelectionFingerprint and Proof.ComponentVariant == Context.ComponentVariantForState and Proof.Exhaustive), None)
                Context.ProofGuidedGenerationSource = Context.ProofGuidedGenerationSourceByPlacementFingerprint.pop(Context.RetainedPlacementFingerprint, None)
                if Context.InterfaceWorkPhase == 'prepare-eligibility' and Context.ChannelizedEquivalentProof is not None:
                    Context.InterfaceStateProofs.append(replace(Context.ChannelizedEquivalentProof, ComponentStateFingerprint=Context.ComponentStateFingerprint, ComponentVariant=Context.ComponentVariantForState, ComponentSelectionFingerprint=Context.ComponentSelectionFingerprint))
                    Context.DuplicateChannelizedPlacementAdvanced = False
                    if Context.ProofGuidedGenerationSource is not None and not Context.AccessRepairInterfacePlanningDeadline.IsExpired():
                        Context.GenerationFailure, Context.GenerationSourceCandidate, Context.GenerationComponentVariant = Context.ProofGuidedGenerationSource
                        Context.DuplicateChannelizedPlacementAdvanced = EnqueueProofGuidedPhysicalPlacement(Context, Context.GenerationFailure, Context.GenerationSourceCandidate, Context.GenerationComponentVariant)
                    Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'SourceCandidateId': Context.RetainedBaseInterfaceCandidate.CandidateId, 'SourcePlacementFingerprint': Context.RetainedPlacementFingerprint, 'PlacementFingerprint': Context.ChannelizedPlacementFingerprint, 'ComponentStateFingerprint': Context.ComponentStateFingerprint, 'ComponentVariant': Context.ComponentVariantForState, 'ComponentSelectionFingerprint': Context.ComponentSelectionFingerprint, 'EquivalentProofComponentStateFingerprint': getattr(Context.ChannelizedEquivalentProof, 'ComponentStateFingerprint', ''), 'PlacementAdvanced': Context.DuplicateChannelizedPlacementAdvanced, 'Result': 'duplicate-channelized-state-proof-reused'})
                    continue
                Context.InterfaceResources, Context.RetainedPlacementResourceCacheHit = ReuseRetainedPlacementRoutingResources(Context.RoutingResourcesByRetainedPlacementFingerprint, Context.RetainedPlacementFingerprint, lambda: Context.Services.BuildRoutingResources(Context.MaterializedInterfacePlacement.Placed, WorkCheck=lambda Diagnostics, Candidate=Context.InterfaceCandidate: Context.InterfaceDeadline.RaiseIfExpired('ClusterInterfaceResourceMaterialization', {'CandidateId': Candidate.CandidateId, **Diagnostics})))
                Context.RoutingResourcesByFingerprint[Context.InterfaceCandidate.PlacementFingerprint] = Context.InterfaceResources
                Context.InterfaceResources.PhysicalGlobalApertureTemplateCache = Context.PhysicalGlobalApertureTemplateCache
                Context.InterfaceResources.PhysicalLocalSeamEligibilityCache = Context.PhysicalLocalSeamEligibilityCache
                Context.InterfaceResources.PhysicalBoundaryPairSupportCache = Context.PhysicalBoundaryPairSupportCache
                Context.InterfaceResources.PreferredPhysicalComponentPortUnsatCoreSignals = Context.PreferredPhysicalComponentPortUnsatCoreSignals
                Context.InterfaceResources.FrozenRoutedComponentTemplate = None
                SeedPortableRawPortalGeometryCaches(Context, Context.InterfaceResources)
                Context.JointDiagnostics = dict(Context.MaterializedInterfacePlacement.Placed.LocalRouteDiagnostics or {}).get('__JointClusterPlacement__', {})
                Context.Transforms = Context.JointDiagnostics.get('SelectedTransforms', {}) if isinstance(Context.JointDiagnostics, dict) else {}
                if Context.InterfaceIndex == 0:
                    Context.PrimaryTransforms = dict(Context.Transforms)
                Context.NormalizedTransforms = tuple(sorted(((str(Cluster), int(Transform.get('Rotation', 0) if isinstance(Transform, dict) else getattr(Transform, 'Rotation', 0)), bool(Transform.get('MirrorX', False) if isinstance(Transform, dict) else getattr(Transform, 'MirrorX', False))) for Cluster, Transform in Context.Transforms.items())))
                Context.TransformFingerprint = BuildStableFingerprint(Context.NormalizedTransforms)
                Context.LocalRouteFingerprint = BuildStableFingerprint(tuple(sorted((str(Template.LocalClaimFingerprint) for Template in getattr(Context.MaterializedInterfacePlacement.Placed, 'ClusterLocalRouteTemplates', ())))) + (Context.ChannelFingerprint,))
                Context.ChangedClusterCount = sum((Context.Transforms.get(Key) != Context.PrimaryTransforms.get(Key) for Key in set(Context.Transforms) | set(Context.PrimaryTransforms)))
                Context.Demand = Context.InterfaceCandidate.TopologyDemand
                Context.InterfacePlacementStatesByFingerprint[Context.InterfaceCandidate.PlacementFingerprint] = ClusterInterfacePlacementState(StateFingerprint=Context.InterfaceCandidate.PlacementFingerprint, ClusterTransforms=Context.NormalizedTransforms, ChangedClusterCount=Context.ChangedClusterCount, LocalRouteFingerprint=Context.LocalRouteFingerprint, Footprint=Context.Demand.GateFootprint if Context.Demand is not None else 0, Hpwl=Context.Demand.Hpwl if Context.Demand is not None else 0, PeakBoundaryPressure=Context.Demand.PeakBoundaryDemand if Context.Demand is not None else 0, TotalBoundaryPressure=Context.Demand.InputTerminalCount + Context.Demand.OutputTerminalCount if Context.Demand is not None else 0, InterfaceTopologyFingerprint=Context.InterfaceCandidate.InterfaceTopologyFingerprint, ChannelFingerprint=Context.ChannelFingerprint, InterClusterChannel=Context.Channel)
                if Context.InterfaceDeadline.IsExpired():
                    raise RoutingStageError(BuildPhysicalAssemblyPlanningIncompleteFailure(Context.InterfaceStageSchedule, RemainingSeconds=Context.InterfaceDeadline.RemainingSeconds(), GlobalPlanningEntered=False))
                Context.InterfaceRemainingSeconds = Context.InterfaceDeadline.RemainingSeconds()
                Context.InterfacePolicy = replace(Context.Policy, RuntimeBudgetSeconds=Context.InterfaceRemainingSeconds, AdaptiveRouting=replace(Context.Policy.AdaptiveRouting, MaximumRuntimeSeconds=min(Context.Policy.AdaptiveRouting.MaximumRuntimeSeconds, Context.InterfaceRemainingSeconds)))
                Context.InterfaceResources.RejectedPhysicalComponentPortAssignmentFingerprints.clear()
                Context.InterfaceResources.RejectedPhysicalComponentPortReservationsBySignal.clear()
                Context.InterfaceResources.RejectedPhysicalComponentPortReservationSets.clear()
                Context.InterfaceResources.ForbiddenPhysicalComponentGlobalCandidateSets.clear()
                Context.InterfaceResources.RejectedPhysicalComponentAssemblyPlanFingerprints.clear()
                Context.InterfaceResources.PreferredPhysicalComponentGlobalContractsBySignal.clear()
                Context.InterfaceResources.PreferredPhysicalComponentApertureContractsBySignal.clear()
                Context.InterfaceResources.PhysicalComponentAperturePortalSlackBySignal.clear()
                Context.InterfaceResources.PreferredPhysicalComponentPortReservationsBySignal.clear()
                Context.InterfaceResources.PhysicalComponentPortCspStateCache.clear()
                if Context.InterfaceWorkPhase == 'prepare-eligibility':
                    Context.EligibilityPreparationStartedAt = Context.Services.monotonic()
                    Context.InterfaceResources.PreferredPhysicalComponentSeamContractsBySignal = {}
                    Context.InterfaceResources.PhysicalComponentCapacityRepairConstraint = Context.CapacityRepairConstraint
                    Context.InterfaceResources.PhysicalComponentBoundaryTraversalPrioritySignals = tuple(sorted(Context.CapacityRepairConstraint.Signals if Context.CapacityRepairConstraint is not None and Context.CapacityRepairConstraint.RepairLevel == 'local-assembly' else Context.ActiveComponentCutSignals))
                    Context.DeferUnboundFrontierToUnaryCompilation = bool(Context.CapacityRepairConstraint is not None or (Context.RetainedPlacementFingerprint in Context.GeneratedProofGuidedPlacementFingerprints and Context.ProofGuidedPlacementGenerationCount >= Context.MaximumProofGuidedGeneratedPlacements))
                    try:
                        Context.PreparedEligibility = PreparePhysicalComponentEligibility(Context.MaterializedInterfacePlacement, Resources=Context.InterfaceResources, Policy=Context.InterfacePolicy, Deadline=Context.InterfaceDeadline, StateFingerprint=Context.InterfaceCandidate.PlacementFingerprint, LocalRouteFingerprint=Context.LocalRouteFingerprint, DeferClusterBoundaryLeaseUntilCapacityPrecheck=True, UnboundOwnedSignalFrontierProofCallback=None if Context.DeferUnboundFrontierToUnaryCompilation else partial(ProveUnboundOwnedSignalFrontier, Context))
                    except Exception:
                        RecordPhysicalComponentStageTiming(Context, 'PhysicalEligibilityPreparation', Context.EligibilityPreparationStartedAt, Result='failed')
                        raise
                    RecordPhysicalComponentStageTiming(Context, 'PhysicalEligibilityPreparation', Context.EligibilityPreparationStartedAt, Result='complete' if Context.PreparedEligibility.Complete else 'incomplete')
                    Context.PreparedMandatoryPortalFactors = tuple((Value for Key, Value in getattr(Context.InterfaceResources, 'PhysicalBoundaryMandatoryPortalFactorDomainCache', {}).items() if isinstance(Key, tuple) and len(Key) == 3 and (Key[0] == Context.PreparedEligibility.DomainFingerprint)))
                    Context.PreparedMandatoryPortalFactorDiagnostics = {'FactorDomainCount': len(Context.PreparedMandatoryPortalFactors), 'CompleteFactorDomainCount': sum((int(bool(getattr(Value, 'Complete', False))) for Value in Context.PreparedMandatoryPortalFactors)), 'SignalCount': len({str(getattr(Value, 'Signal', '')) for Value in Context.PreparedMandatoryPortalFactors}), 'IncompleteSignals': sorted({str(getattr(Value, 'Signal', '')) for Value in Context.PreparedMandatoryPortalFactors if not bool(getattr(Value, 'Complete', False))})}
                    if not Context.PreparedEligibility.Complete:
                        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentEligibility', Detail='the physical component port factor domain is incomplete', Diagnostics={'DomainFingerprint': Context.PreparedEligibility.DomainFingerprint, 'Complete': False, 'Feasible': False, 'MandatoryPortalFactorDomains': Context.PreparedMandatoryPortalFactorDiagnostics}))
                    if not Context.PreparedEligibility.Feasible:
                        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalComponentEligibility', Detail='the complete physical port factor domain has an empty port bank', AffectedNets=tuple((Signal for Signal, Values in Context.PreparedEligibility.LaneFactorsBySignal if not Values)), Diagnostics={'DomainFingerprint': Context.PreparedEligibility.DomainFingerprint, 'Complete': True, 'Feasible': False, 'DomainDiagnosticsBySignal': dict(Context.PreparedEligibility.DiagnosticsBySignal), 'ComponentFabricConstructionComplete': True, 'OwnershipSearchComplete': True, 'ImplicitForeignTransitDomainCount': 0}))
                    Context.AchievedSeamFingerprint = ''
                    if Context.CapacityRepairConstraint is not None and Context.CapacityRepairConstraint.RepairLevel == 'local-assembly':
                        Context.PreparedCapacityWitness = Context.InterfaceResources.PreparedPhysicalComponentCapacityRepairWitness
                        if Context.PreparedCapacityWitness is None:
                            Context.PreparedCapacityWitness = PreparedEligibilityHasDisjointCapacitySeams(Context.PreparedEligibility, Context.CapacityRepairConstraint)
                        Context.HasDisjointSeams, Context.AchievedSeamFingerprint, Context.SelectedSeamAssignment, Context.AvailableSeamClassesBySignal = Context.PreparedCapacityWitness
                        if not Context.HasDisjointSeams:
                            Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-pair-repair-rejected-overlapping-seams', 'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'SourceProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(Context.CapacityRepairConstraint.Signals), 'ProofComplete': True, 'CoreSignalCount': len(Context.CapacityRepairConstraint.Signals), 'AvailableSeamClassesBySignal': [[Signal, list(Seams)] for Signal, Seams in Context.AvailableSeamClassesBySignal], 'GeometryFingerprint': BuildCapacityRepairGeometryFingerprint(Context.InterfaceCandidate, Context.CapacityRepairConstraint.Signals), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalCapacityRepairPrecheck', AffectedNets=Context.CapacityRepairConstraint.Signals, Detail='the repaired placement still has no disjoint local seam capacity for the complete symbolic core', Diagnostics={'SymbolicCapacityPlacementFeedback': True, 'SymbolicCapacityProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint, 'PlacementInterfacePressureSignals': list(Context.CapacityRepairConstraint.Signals), 'LocalCapacityCoreClause': [list(Value) for Value in Context.CapacityRepairConstraint.ForcedSeamClasses], 'CapacityRepairConstraint': Context.CapacityRepairConstraint.ToDictionary(), 'CapacityRepairAchievedSeamFingerprint': '', 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False}))
                        Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-repair-witness-reserved', 'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'SourceProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(Context.CapacityRepairConstraint.Signals), 'ProofComplete': True, 'CoreSignalCount': len(Context.CapacityRepairConstraint.Signals), 'AchievedSeamFingerprint': Context.AchievedSeamFingerprint, 'AvailableSeamClassesBySignal': [[Signal, list(Seams)] for Signal, Seams in Context.AvailableSeamClassesBySignal], 'SelectedSeamAssignment': [list(Value) for Value in Context.SelectedSeamAssignment], 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                        Context.CapacityRepairConstraint = replace(Context.CapacityRepairConstraint, AvailableSeamClassesBySignal=Context.AvailableSeamClassesBySignal, SelectedSeamAssignment=Context.SelectedSeamAssignment)
                        Context.CapacityRepairConstraintByPlacementFingerprint[Context.RetainedPlacementFingerprint] = Context.CapacityRepairConstraint
                        Context.CapacityRepairWitnessReserved = True
                        Context.SelectedSeamsBySignal = dict(Context.SelectedSeamAssignment)
                        Context.InterfaceResources.PreferredPhysicalComponentSeamContractsBySignal = dict(Context.SelectedSeamsBySignal)
                        Context.LocalFactorsBySignal = dict(Context.PreparedEligibility.LocalAccessFactorsBySignal)
                        for Context.Signal, Context.SelectedSeam in Context.SelectedSeamsBySignal.items():
                            for Context.Factor in Context.LocalFactorsBySignal.get(Context.Signal, ()):
                                Context.Seam = str(Context.Factor.SeamContractFingerprint)
                                if Context.Seam != Context.SelectedSeam:
                                    Context.InterfaceResources.RejectedPhysicalComponentLocalSeamReservationSets.add(frozenset(((Context.Signal, Context.Seam),)))
                    Context.PreparedEligibilityByState[Context.EligibilityStateKey] = Context.PreparedEligibility
                    Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'SourceCandidateId': Context.RetainedBaseInterfaceCandidate.CandidateId, 'SourcePlacementFingerprint': Context.RetainedBaseInterfaceCandidate.PlacementFingerprint, 'PlacementFingerprint': Context.InterfaceCandidate.PlacementFingerprint, 'ComponentVariant': Context.ComponentVariantForState, 'Result': 'physical-eligibility-prepared', 'DomainFingerprint': Context.PreparedEligibility.DomainFingerprint, 'Complete': True, 'Feasible': True, 'MandatoryPortalFactorDomains': Context.PreparedMandatoryPortalFactorDiagnostics, 'PreparationStageTimings': dict(Context.PreparedEligibility.PreparationStageTimings), 'PhysicalConnectorDiagnostics': {'CapacityRepairConstraint': Context.CapacityRepairConstraint.ToDictionary() if Context.CapacityRepairConstraint is not None else None, 'CapacityRepairAchievedSeamFingerprint': Context.AchievedSeamFingerprint if Context.CapacityRepairConstraint is not None else '', 'FactorPreparationTimings': dict(Context.PreparedEligibility.FactorPreparationTimings), 'LocalFactorCacheHitSignals': list(Context.PreparedEligibility.LocalFactorCacheHitSignals), 'LocalFactorRebuiltSignals': list(Context.PreparedEligibility.LocalFactorRebuiltSignals), 'LocalFactorPreparationElapsedSeconds': Context.PreparedEligibility.LocalFactorPreparationElapsedSeconds, 'ExteriorFactorPreparationElapsedSeconds': Context.PreparedEligibility.ExteriorFactorPreparationElapsedSeconds, 'CertifiedLocalSeamCandidateCount': sum((int(Diagnostics.get('CertifiedLayerMatchCount', 0)) for _Signal, Diagnostics in Context.PreparedEligibility.DiagnosticsBySignal)), 'CertifiedLocalSeamFeasibleCount': sum((int(Diagnostics.get('CertifiedLaneFactorCount', 0)) for _Signal, Diagnostics in Context.PreparedEligibility.DiagnosticsBySignal)), 'CertifiedUnarySeamInfeasibleCount': sum((int(Diagnostics.get('CertifiedUnarySeamInfeasibleCount', 0)) for _Signal, Diagnostics in Context.PreparedEligibility.DiagnosticsBySignal)), 'CertifiedUnarySeamInfeasibleCountBySignal': {Signal: int(Diagnostics.get('CertifiedUnarySeamInfeasibleCount', 0)) for Signal, Diagnostics in Context.PreparedEligibility.DiagnosticsBySignal if int(Diagnostics.get('CertifiedUnarySeamInfeasibleCount', 0))}, 'LocalSeamEligibilityCacheHitCount': Context.PreparedEligibility.PhysicalLocalSeamEligibilityCacheHitCount, 'LocalSeamEligibilityCacheMissCount': Context.PreparedEligibility.PhysicalLocalSeamEligibilityCacheMissCount, 'LocalSeamEligibilityCacheStoreCount': Context.PreparedEligibility.PhysicalLocalSeamEligibilityCacheStoreCount, 'SearchCount': Context.PreparedEligibility.GlobalConnectorSearchCount, 'PortableCacheHitCount': Context.PreparedEligibility.GlobalConnectorPortableCacheHitCount, 'PortableCacheValidationRejectCount': Context.PreparedEligibility.GlobalConnectorPortableCacheValidationRejectCount, 'PortableCacheStoreCount': Context.PreparedEligibility.GlobalConnectorPortableCacheStoreCount, 'ExpansionCount': Context.PreparedEligibility.GlobalConnectorExpansionCount, 'GuideFieldBuildCount': Context.PreparedEligibility.GlobalGuideFieldBuildCount, 'GuideFieldExpansionCount': Context.PreparedEligibility.GlobalGuideFieldExpansionCount, 'GuideFieldHitCount': Context.PreparedEligibility.GlobalGuideFieldHitCount, 'GuideFieldCanonicalPathCount': Context.PreparedEligibility.GlobalGuideFieldCanonicalPathCount, 'GuideFieldFallbackCount': Context.PreparedEligibility.GlobalGuideFieldFallbackCount, 'NativeBatchWorkItems': Context.PreparedEligibility.NativeConnectorBatchWorkItems, 'NativeBatchActiveWorkerCount': Context.PreparedEligibility.NativeConnectorBatchActiveWorkerCount, 'LaneFactorExpansionCount': Context.PreparedEligibility.LaneFactorExpansionCount, 'AccessFactorExpansionCount': Context.PreparedEligibility.AccessFactorExpansionCount, 'SeamFactorExpansionCount': Context.PreparedEligibility.SeamFactorExpansionCount}, 'PhysicalComponentStageTimings': list(Context.PhysicalComponentStageTimings)})
                    Context.InterfaceCandidateQueue.insert(0, ('solve-prepared-eligibility', Context.InterfaceIndex, Context.RetainedBaseInterfaceCandidate, Context.InterfaceCutEpoch, Context.ComponentVariantForState))
                    continue
                Context.PreparedEligibility = Context.PreparedEligibilityByState.get(Context.EligibilityStateKey)
                if Context.PreparedEligibility is None:
                    raise RuntimeError('physical component solve was scheduled without a complete eligibility domain')
                Context.RemainingQueuedPlacementFingerprints = {QueuedCandidate.PlacementFingerprint for _QueuedPhase, _QueuedIndex, QueuedCandidate, _QueuedCutEpoch, _QueuedComponentVariant in Context.InterfaceCandidateQueue if QueuedCandidate.PlacementFingerprint != Context.RetainedPlacementFingerprint}
                Context.ProofFirstCapacityEligible = bool(Context.ActiveComponentCutSignals or Context.CapacityRepairConstraint is not None)
                if Context.ProofFirstCapacityEligible:
                    Context.ProofFirstStartedAt = Context.Services.monotonic()
                    Context.ProofFirstDeadline = RoutingDeadline(StartedAt=Context.Deadline.StartedAt, ExpiresAt=max(Context.Services.monotonic(), min(Context.InterfaceDeadline.ExpiresAt, Context.Deadline.ExpiresAt - 2.0)), ExpirationKind='StageReserveExpired')
                    Context.ProofFirstAssembly = SolvePreparedPhysicalComponentEligibility(Context.PreparedEligibility, Resources=Context.InterfaceResources, Deadline=Context.ProofFirstDeadline)
                    Context.ProofFirst = ProveClosedComponentSymbolicCapacityEligibility(Context.ProofFirstAssembly.Problem, DeadlineSeconds=Context.ProofFirstDeadline.RemainingSeconds(), WorkCheck=None, CompletedProofCache=Context.ComponentSymbolicCapacityProofCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache, SymbolicNetStateCache=Context.InterfaceResources.PhysicalComponentSymbolicNetStateCache)
                    RecordPhysicalComponentStageTiming(Context, 'PhysicalSymbolicCapacityProofFirst', Context.ProofFirstStartedAt, Result=Context.ProofFirst.Status, PlanFingerprint=Context.ProofFirstAssembly.Plan.PlanFingerprint)
                    Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.InterfaceCandidate.PlacementFingerprint, 'ComponentVariant': Context.ComponentVariantForState, 'Result': 'symbolic-capacity-proof-first', 'Status': Context.ProofFirst.Status, 'ProofComplete': bool(dict(Context.ProofFirst.Diagnostics or {}).get('SymbolicCapacityProofComplete', False)), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                    if Context.ProofFirst.Status == 'architectural-unsatisfiable':
                        Context.ProofFirstDiagnostics = RecordPhysicalComponentSymbolicCapacityEligibilityNoGood(Context.ProofFirst, Context.ProofFirstAssembly.Plan, Context.InterfaceResources, FactorDomain=Context.PreparedEligibility)
                        Context.ProofFirstSignals = tuple(sorted(set(map(str, dict(Context.ProofFirst.Diagnostics or {}).get('LocalUnsatCoreSignals', ())))))
                        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalSymbolicCapacityPlacementFeedback', AffectedNets=Context.ProofFirstSignals, Detail='complete proof-first local capacity core requires geometry repair', Diagnostics={'SymbolicCapacityPlacementFeedback': True, 'PlacementInterfacePressureSignals': list(Context.ProofFirstSignals), 'SelectedComponentClusters': list(Context.SelectedComponentClusters), 'SelectedComponentSignals': sorted(map(str, getattr(Context.Channel, 'AffectedSignals', ()))), **BuildSymbolicCapacityRepairEvidence(Context.ProofFirstDiagnostics, Context.ProofFirstSignals), 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False}))
                Context.UnarySupportStartedAt = Context.Services.monotonic()
                Context.UnarySupportSignals = tuple((Signal for Signal, _Factors in Context.PreparedEligibility.LocalAccessFactorsBySignal))
                Context.UnarySupportClauses, Context.UnarySupportDiagnostics = CompilePhysicalComponentSymbolicUnaryApertureDomain(Context.PreparedEligibility.Problem, Context.PreparedEligibility, Context.UnarySupportSignals, DeadlineSeconds=Context.InterfaceDeadline.RemainingSeconds(), WorkCheck=lambda Diagnostics: Context.InterfaceDeadline.RaiseIfExpired('PhysicalComponentUnarySupportCompilation', Diagnostics), NetStateCache=Context.InterfaceResources.PhysicalComponentSymbolicNetStateCache, CompletedClauseCache=Context.InterfaceResources.PhysicalComponentSymbolicUnaryApertureClauseCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache)
                RecordPhysicalComponentStageTiming(Context, 'PhysicalComponentUnarySupportCompilation', Context.UnarySupportStartedAt, Result='complete' if Context.UnarySupportDiagnostics.get('Complete', False) else 'incomplete')
                if not Context.UnarySupportDiagnostics.get('Complete', False):
                    raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentUnarySupportCompilation', Detail='the complete physical port domain did not finish unary local-support compilation', Diagnostics={**Context.UnarySupportDiagnostics, 'DomainFingerprint': Context.PreparedEligibility.DomainFingerprint, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False, 'ImplicitForeignTransitDomainCount': 0}))
                Context.ExistingUnaryClauses = Context.InterfaceResources.RejectedPhysicalComponentPortReservationSets
                Context.ForeignPortalUnaryClauses: frozenset[frozenset[tuple[str, str]]] = frozenset()
                Context.ForeignPortalUnaryDiagnostics: dict[str, object] = {'Complete': False, 'Reason': 'no-frozen-whole-design-portal-domain'}
                Context.FrozenPortalHandoff = Context.InterfaceResources.FrozenPhysicalComponentPostClosurePortalHandoff
                Context.DeferForeignPortalUnarySupport = bool(Context.InterfaceCandidate.PlacementFingerprint in Context.GeneratedProofGuidedPlacementFingerprints or Context.ActiveComponentCutSignals)
                if Context.FrozenPortalHandoff is not None and (not Context.DeferForeignPortalUnarySupport):
                    Context.ForeignPortalUnaryClauses, Context.ForeignPortalUnaryDiagnostics = CompilePhysicalComponentForeignPortalUnaryApertureClauses(Context.PreparedEligibility, Context.FrozenPortalHandoff.RawPortalGeometryCache, Context.InterfaceResources.ResourceGraph)
                    Context.InterfaceResources.PhysicalComponentAperturePortalSlackBySignal.update({str(Signal): {str(Fingerprint): tuple(map(int, Slack)) for Fingerprint, Slack in dict(Values).items()} for Signal, Values in dict(Context.ForeignPortalUnaryDiagnostics.get('AperturePortalSlackBySignal', {})).items()})
                elif Context.FrozenPortalHandoff is not None:
                    Context.ForeignPortalUnaryDiagnostics = {'Complete': True, 'DeferredForProofGuidedLocalEligibility': True}
                Context.NewUnaryClauses = frozenset((Clause for Clause in (*Context.UnarySupportClauses, *Context.ForeignPortalUnaryClauses) if Clause not in Context.ExistingUnaryClauses))
                Context.ExistingUnaryClauses.update(Context.NewUnaryClauses)
                Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.InterfaceCandidate.PlacementFingerprint, 'ComponentVariant': Context.ComponentVariantForState, 'Result': 'unary-support-compiled', 'UnarySupportDiagnostics': dict(Context.UnarySupportDiagnostics), 'ForeignPortalUnaryDiagnostics': dict(Context.ForeignPortalUnaryDiagnostics), 'PublishedUnaryClauseCount': len(Context.NewUnaryClauses), 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False})
                Context.UnaryResolveStartedAt = Context.Services.monotonic()
                try:
                    Context.PreparedAssembly = SolvePreparedPhysicalComponentEligibility(Context.PreparedEligibility, Resources=Context.InterfaceResources, Deadline=Context.InterfaceDeadline)
                except Exception:
                    RecordPhysicalComponentStageTiming(Context, 'PhysicalEligibilitySolveAfterUnarySupport', Context.UnaryResolveStartedAt, Result='failed')
                    raise
                RecordPhysicalComponentStageTiming(Context, 'PhysicalEligibilitySolveAfterUnarySupport', Context.UnaryResolveStartedAt, Result='complete', PlanFingerprint=Context.PreparedAssembly.Plan.PlanFingerprint)
                if Context.CapacityRepairWitnessReserved:
                    Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-repair-csp-admitted', 'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'SourceProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint, 'SelectedSeamAssignment': [list(Value) for Value in Context.CapacityRepairConstraint.SelectedSeamAssignment], 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                Context.PairSupportStartedAt = Context.Services.monotonic()
                Context.ResourceRelevantPairs: tuple[tuple[str, str], ...] = ()
                Context.PairSupportClauses: set[frozenset[tuple[str, str]]] = set()
                Context.PairSupportDiagnostics = []
                for Context.SignalPair in Context.ResourceRelevantPairs:
                    Context.PairCertificate = CompilePhysicalComponentSymbolicPortPairDomain(Context.PreparedAssembly.Problem, Context.PreparedEligibility, Context.SignalPair, DeadlineSeconds=Context.SharedInterfacePlanningDeadline.RemainingSeconds(), WorkCheck=lambda Diagnostics: Context.SharedInterfacePlanningDeadline.RaiseIfExpired('PhysicalComponentBinarySupportCompilation', Diagnostics), NetStateCache=Context.InterfaceResources.PhysicalComponentSymbolicNetStateCache, CompletedCertificateCache=Context.InterfaceResources.PhysicalComponentSymbolicPortPairCertificateCache, CompleteCompatibilityIndexCache=Context.InterfaceResources.PhysicalComponentSymbolicPairCompatibilityIndexCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache)
                    if not Context.PairCertificate.Complete:
                        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentBinarySupportCompilation', AffectedNets=Context.SignalPair, Detail='a resource-relevant physical port pair did not finish exact support compilation', Diagnostics={'SignalPair': list(Context.SignalPair), 'PairCertificateFingerprint': Context.PairCertificate.DomainFingerprint, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False}))
                    Context.ProjectedPairClauses, Context.ProjectionDiagnostics = ProjectCompletePhysicalPortPairCertificateToApertureClauses(Context.PreparedEligibility, Context.PairCertificate)
                    if not Context.ProjectionDiagnostics.get('ApertureProjectionComplete', False):
                        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentBinarySupportCompilation', AffectedNets=Context.SignalPair, Detail='a complete physical port-pair certificate could not be projected to aperture clauses', Diagnostics=dict(Context.ProjectionDiagnostics)))
                    Context.PairSupportClauses.update(Context.ProjectedPairClauses)
                    Context.PairSupportDiagnostics.append({'SignalPair': list(Context.SignalPair), 'CertificateFingerprint': Context.PairCertificate.ProofFingerprint, **Context.ProjectionDiagnostics})
                Context.ExistingPairClauses = Context.InterfaceResources.RejectedPhysicalComponentPortReservationSets
                Context.NewPairClauses = frozenset((Clause for Clause in Context.PairSupportClauses if Clause not in Context.ExistingPairClauses))
                Context.ExistingPairClauses.update(Context.NewPairClauses)
                RecordPhysicalComponentStageTiming(Context, 'PhysicalComponentBinarySupportCompilation', Context.PairSupportStartedAt, Result='complete', PlanFingerprint=Context.PreparedAssembly.Plan.PlanFingerprint)
                Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.InterfaceCandidate.PlacementFingerprint, 'ComponentVariant': Context.ComponentVariantForState, 'Result': 'binary-support-compiled', 'ResourceRelevantSignalPairs': [list(Pair) for Pair in Context.ResourceRelevantPairs], 'PublishedBinaryClauseCount': len(Context.NewPairClauses), 'PairSupportDiagnostics': Context.PairSupportDiagnostics, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False})
                if Context.NewPairClauses:
                    Context.PairResolveStartedAt = Context.Services.monotonic()
                    Context.PreparedAssembly = SolvePreparedPhysicalComponentEligibility(Context.PreparedEligibility, Resources=Context.InterfaceResources, Deadline=Context.InterfaceDeadline)
                    RecordPhysicalComponentStageTiming(Context, 'PhysicalEligibilityResolveAfterBinarySupport', Context.PairResolveStartedAt, Result='complete', PlanFingerprint=Context.PreparedAssembly.Plan.PlanFingerprint)
                Context.InterfaceDeadline = Context.AccessRepairInterfaceDeadline if Context.CapacityRepairConstraint is not None else Context.SharedInterfacePlanningDeadline
                Context.PhysicalAssemblyPlan = Context.PreparedAssembly.Plan
                Context.ComponentProblem = Context.PreparedAssembly.Problem
                Context.ComponentBasePlacement = Context.MaterializedInterfacePlacement
                Context.ComponentBaseCandidate = Context.InterfaceCandidate
                Context.CumulativeSymbolicCapacityPressureSignals: set[str] = set()
                Context.LatestSymbolicCapacityRepairEvidence: dict[str, object] = {}
                Context.CapacityFeasibleLocalContractFingerprints: set[str] = set()
                Context.CompiledSymbolicCapacityBinaryCores: set[tuple[str, str]] = set()
                Context.SymbolicCapacityAssemblyReplanCount = 0
                Context.MaximumSymbolicCapacityAssemblyReplans = Context.MaximumProofGuidedSymbolicCapacityPairPlacements
                Context.MaximumCompleteBinaryCoreCertificates = 0
                Context.SuccessfulGlobalPlanWasRetained = False
                if not AdmitSymbolicLocalCapacity(Context, Context.PreparedAssembly):
                    Context.PreparedAssembly = ReplanPhysicalAssemblyWithTiming(Context)
                Context.PreparedAssembly, Context.GlobalChannelDesign = ReserveAuthoritativeGlobalChannels(Context, Context.PreparedAssembly)
                Context.PhysicalAssemblyPlan = Context.PreparedAssembly.Plan
                Context.ComponentProblem = Context.PreparedAssembly.Problem
                Context.StateAttemptDiagnostics.append({'Result': 'authoritative-global-channels-reserved', 'GlobalPlanSource': 'retained' if Context.SuccessfulGlobalPlanWasRetained else 'fresh', 'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, 'ExactChannelCount': len(Context.PhysicalAssemblyPlan.Channels), 'ImplicitForeignTransitDomainCount': 0})
                Context.CompiledPhysicalAssemblyPlanFingerprints: set[str] = set()
                while True:
                    Context.RoutedComponentHandoffEntered = False
                    Context.ComponentSolve = None
                    Context.ComponentTemplate = None
                    Context.ActiveComponentDeadline = Context.SharedInterfaceDeadline
                    Context.ActiveComponentRemainingSeconds = Context.ActiveComponentDeadline.RemainingSeconds()
                    if Context.InterfaceDeadline.IsExpired():
                        raise RoutingStageError(BuildPhysicalAssemblyPlanningIncompleteFailure(Context.InterfaceStageSchedule, RemainingSeconds=Context.ActiveComponentRemainingSeconds, GlobalPlanningEntered=True))
                    if Context.ActiveComponentRemainingSeconds <= 0:
                        raise RoutingStageError(BuildLocalComponentCompilationAdmissionFailure(Context.InterfaceStageSchedule, RemainingSeconds=Context.ActiveComponentRemainingSeconds))
                    Context.LocalCompilationStartedAt = Context.Services.monotonic()
                    if Context.PhysicalAssemblyPlan.PlanFingerprint in Context.CompiledPhysicalAssemblyPlanFingerprints:
                        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='DuplicateClosedComponentCompilation', Detail='the closed component compiler was invoked more than once for one immutable physical assembly plan', RepairActions=(), Diagnostics={'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, 'CompiledPhysicalAssemblyPlanFingerprints': sorted(Context.CompiledPhysicalAssemblyPlanFingerprints), 'BroadFallbackAllowed': False}))
                    Context.CompiledPhysicalAssemblyPlanFingerprints.add(Context.PhysicalAssemblyPlan.PlanFingerprint)
                    try:
                        Context.ComponentSolve = CompileClosedComponent(Context.ComponentProblem, AssemblyPlan=Context.PhysicalAssemblyPlan, DeadlineSeconds=Context.ActiveComponentRemainingSeconds, WorkCheck=lambda Diagnostics: Context.ActiveComponentDeadline.RaiseIfExpired('ComponentRoutingSolve', {'CandidateId': Context.ComponentBaseCandidate.CandidateId, **Diagnostics}), VariantPortfolioCache=Context.ComponentVariantPortfolioCache, NetVariantConstructionCache=Context.ComponentNetVariantConstructionCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache, NetVariantDiscoveryStateCache=Context.ComponentNetVariantDiscoveryStateCache)
                    except Exception:
                        RecordPhysicalComponentStageTiming(Context, 'BoundLocalCompilation', Context.LocalCompilationStartedAt, Result='failed', PlanFingerprint=Context.PhysicalAssemblyPlan.PlanFingerprint)
                        raise
                    RecordPhysicalComponentStageTiming(Context, 'BoundLocalCompilation', Context.LocalCompilationStartedAt, Result=str(Context.ComponentSolve.Status), PlanFingerprint=Context.PhysicalAssemblyPlan.PlanFingerprint)
                    if not Context.ComponentSolve.Feasible:
                        Context.Incomplete = Context.ComponentSolve.Status == 'incomplete'
                        if Context.Incomplete:
                            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='ClosedComponentCompilationIncomplete', Detail=Context.ComponentSolve.Detail, RepairActions=(), Diagnostics={'ComponentRoutingProblem': Context.ComponentProblem.ToDictionary(), 'ComponentRoutingSolve': {'Status': Context.ComponentSolve.Status, 'ProofFingerprint': Context.ComponentSolve.ProofFingerprint, 'ExpansionCount': Context.ComponentSolve.ExpansionCount, 'Complete': False, 'Diagnostics': Context.ComponentSolve.Diagnostics}, 'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, 'PerSignalReservationFeedbackUsed': False, 'BroadFallbackAllowed': False, 'ExecutableLegacyRepairCascade': False}))
                        if not Context.ComponentSolve.Diagnostics.get('LocalUnsatCoreComplete', False):
                            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentLocalCompilationUnsatisfiable, Stage='ClosedComponentCompilationUnsatisfiable', Detail=Context.ComponentSolve.Detail, RepairActions=(), Diagnostics={'ComponentRoutingProblem': Context.ComponentProblem.ToDictionary(), 'ComponentRoutingSolve': {'Status': Context.ComponentSolve.Status, 'ProofFingerprint': Context.ComponentSolve.ProofFingerprint, 'ExpansionCount': Context.ComponentSolve.ExpansionCount, 'Complete': True, 'Diagnostics': Context.ComponentSolve.Diagnostics}, 'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, 'CompleteLocalUnsatCore': False, 'PerSignalReservationFeedbackUsed': False, 'BroadFallbackAllowed': False, 'ExecutableLegacyRepairCascade': False}))
                        Context.LocalNoGoodDiagnostics = RecordPhysicalComponentLocalCompilationNoGood(Context.ComponentSolve, Context.PhysicalAssemblyPlan, Context.GlobalChannelDesign, Context.InterfaceResources, Problem=Context.ComponentProblem)
                        if Context.PhysicalAssemblyPlan.PlanFingerprint not in Context.InterfaceResources.RejectedPhysicalComponentAssemblyPlanFingerprints:
                            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentAssemblyIdentityMismatch, Stage='ClosedComponentPlanRejectionIdentity', Detail='the complete local failure did not reject its exact physical assembly plan', RepairActions=(), Diagnostics={'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, 'LocalNoGoodDiagnostics': Context.LocalNoGoodDiagnostics, 'BroadFallbackAllowed': False}))
                        Context.StateAttemptDiagnostics.append({'Result': 'local-unsat-reject-complete-assembly-plan', 'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, 'LocalSolveDetail': Context.ComponentSolve.Detail, 'LocalSolveDiagnostics': dict(Context.ComponentSolve.Diagnostics or {}), 'LocalTemplateReopened': False, 'PerSignalReservationFeedbackUsed': False, **Context.LocalNoGoodDiagnostics, 'ImplicitForeignTransitDomainCount': 0})
                        Context.PreparedAssembly = ReplanPhysicalAssemblyWithTiming(Context)
                        Context.PreparedAssembly, Context.GlobalChannelDesign = ReserveAuthoritativeGlobalChannels(Context, Context.PreparedAssembly)
                        Context.PhysicalAssemblyPlan = Context.PreparedAssembly.Plan
                        Context.ComponentProblem = Context.PreparedAssembly.Problem
                        continue
                    assert Context.ComponentSolve.Template is not None
                    Context.ComponentTemplate = Context.ComponentSolve.Template
                    Context.ComponentAssembly = AssembleClosedComponentForGlobalRouting(Context.ComponentBasePlacement.Placed, Context.ComponentTemplate, PhysicalAssemblyPlan=Context.PhysicalAssemblyPlan, PlacementFingerprint=Context.ComponentBaseCandidate.PlacementFingerprint, LocalTemplateFingerprint=Context.LocalRouteFingerprint)
                    Context.RoutedComponentPlaced = Context.ComponentAssembly.Placed
                    if Context.GlobalChannelDesign is not None:
                        Context.RoutedComponentPlaced = FreezePhysicalAssemblyGlobalChannels(Context.RoutedComponentPlaced, Context.PhysicalAssemblyPlan, Context.GlobalChannelDesign)
                    Context.MaterializedInterfacePlacement = replace(Context.ComponentBasePlacement, Placed=Context.RoutedComponentPlaced)
                    Context.InterfaceCandidate = replace(Context.ComponentBaseCandidate, Placement=Context.MaterializedInterfacePlacement)
                    Context.InterfaceResources.FrozenRoutedComponentTemplate = Context.ComponentTemplate
                    Context.InterfaceResources.FrozenPhysicalComponentAssemblyPlan = Context.PhysicalAssemblyPlan
                    if Context.GlobalChannelDesign is not None and Context.GlobalChannelDesign.RoutingAssignment is not None:
                        Context.SelectedGlobalCandidates = Context.GlobalChannelDesign.RoutingAssignment.SelectedCandidates
                        Context.InterfaceResources.FrozenInterfaceGlobalCandidateCache = {Signal: (Candidate,) for Signal, Candidate in Context.SelectedGlobalCandidates.items()}
                        Context.InterfaceResources.FrozenInterfaceGlobalCandidateMetadata = {Signal: {} for Signal in Context.SelectedGlobalCandidates}
                        Context.InterfaceResources.FrozenInterfaceGlobalCandidatePlacementIdentity = id(Context.RoutedComponentPlaced)
                    Context.HandoffDiagnostics = Context.ComponentAssembly.HandoffDiagnostics
                    Context.RoutedComponentHandoffEntered = True
                    Context.GlobalRemainingSeconds = max(0.001, Context.Deadline.RemainingSeconds())
                    Context.GlobalHandoffPolicy = replace(Context.Policy, RuntimeBudgetSeconds=Context.GlobalRemainingSeconds, AdaptiveRouting=replace(Context.Policy.AdaptiveRouting, MaximumRuntimeSeconds=min(Context.Policy.AdaptiveRouting.MaximumRuntimeSeconds, Context.GlobalRemainingSeconds)))
                    try:
                        Context.PreRoutedDesign = Context.Services.RoutePcbDesign(Context.MaterializedInterfacePlacement, Policy=Context.GlobalHandoffPolicy, Deadline=Context.Deadline, Resources=Context.InterfaceResources, RequireCompleteClusterInterfaceDomain=True)
                        break
                    except RoutingStageError as GlobalError:
                        Context.LastGlobalHandoffError = GlobalError
                        if not Context.Deadline.IsExpired():
                            Context.RepeaterReadyPortalRepairSignals = (
                                SelectExhaustedRepeaterAccessCutSignals(
                                    GlobalError.Failure
                                )
                            )
                            if Context.RepeaterReadyPortalRepairSignals:
                                (
                                    Context.RepeaterReadyPortalRepairApplied,
                                    Context.RepeaterReadyPortalProfileFingerprint,
                                ) = ApplyCoordinatedCandidateDiversificationProfile(
                                    Context.MaterializedInterfacePlacement,
                                    Context.RepeaterReadyPortalRepairSignals,
                                    EnableRepeaterReadyPortalRepair=True,
                                )
                                if Context.RepeaterReadyPortalRepairApplied:
                                    Context.StateAttemptDiagnostics.append({
                                        'Result': (
                                            'repeater-ready-global-route-retry'
                                        ),
                                        'PhysicalAssemblyPlanFingerprint': (
                                            Context.PhysicalAssemblyPlan
                                            .PlanFingerprint
                                        ),
                                        'Signals': sorted(
                                            Context
                                            .RepeaterReadyPortalRepairSignals
                                        ),
                                        'RoutingControlProfileFingerprint': (
                                            Context
                                            .RepeaterReadyPortalProfileFingerprint
                                        ),
                                        'ReusedPlacedGeometry': True,
                                        'ReusedPhysicalAssemblyPlan': True,
                                        'ExecutableLegacyRepairCascade': False,
                                    })
                                    Context.PreRoutedDesign = (
                                        Context.Services.RoutePcbDesign(
                                            Context
                                            .MaterializedInterfacePlacement,
                                            Policy=Context.GlobalHandoffPolicy,
                                            Deadline=Context.Deadline,
                                            Resources=Context.InterfaceResources,
                                            RequireCompleteClusterInterfaceDomain=(
                                                True
                                            ),
                                        )
                                    )
                                    break
                            if IsComponentKeepoutGlobalFailure(GlobalError.Failure, Context.PhysicalAssemblyPlan):
                                raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentDetailedRoutingFailed, Stage='ComponentGlobalKeepoutAdmission', AffectedNets=GlobalError.Failure.AffectedNets, Detail='an ordinary global net disproved the immutable component keepout; advance to another retained placement instead of reopening ports inside the same envelope', RepairActions=(), Diagnostics={'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, 'RejectedComponentEnvelope': [list(Context.PhysicalAssemblyPlan.EnvelopeMinimum), list(Context.PhysicalAssemblyPlan.EnvelopeMaximum)], 'UnderlyingFailure': GlobalError.Failure.ToDictionary(), 'LocalTemplateReopened': False, 'PortPlanReopened': False, 'ImplicitForeignTransitDomainCount': 0, 'BroadFallbackAllowed': False})) from GlobalError
                            Context.DetailedRoutingNoGoodDiagnostics = RecordPhysicalComponentDetailedRoutingNoGood(Context.PhysicalAssemblyPlan, Context.GlobalChannelDesign, Context.InterfaceResources)
                            Context.StateAttemptDiagnostics.append({'Result': 'detailed-failure-reject-physical-plan', 'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, **Context.DetailedRoutingNoGoodDiagnostics, 'UnderlyingFailure': GlobalError.Failure.ToDictionary(), 'LocalTemplateReopened': False, 'ImplicitForeignTransitDomainCount': 0})
                            Context.MaterializedInterfacePlacement = Context.ComponentBasePlacement
                            Context.InterfaceCandidate = Context.ComponentBaseCandidate
                            Context.InterfaceResources.FrozenRoutedComponentTemplate = None
                            Context.PreparedAssembly = ReplanPhysicalAssemblyWithTiming(Context)
                            Context.PreparedAssembly, Context.GlobalChannelDesign = ReserveAuthoritativeGlobalChannels(Context, Context.PreparedAssembly)
                            Context.PhysicalAssemblyPlan = Context.PreparedAssembly.Plan
                            Context.ComponentProblem = Context.PreparedAssembly.Problem
                            continue
                        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentDetailedRoutingFailed, Stage='AuthoritativeDetailedRoutingAfterPhysicalComponentAssembly', AffectedNets=GlobalError.Failure.AffectedNets, Detail='authoritative detailed routing rejected the immutable physical assembly plan', RepairActions=('RejectPhysicalAssemblyPlan',), Diagnostics={'PhysicalAssemblyPlanFingerprint': Context.PhysicalAssemblyPlan.PlanFingerprint, 'RejectedGlobalPlan': Context.PhysicalAssemblyPlan.ToDictionary(), 'UnderlyingFailure': GlobalError.Failure.ToDictionary(), 'LocalTemplateReopened': False, 'ImplicitForeignTransitDomainCount': 0, 'BroadFallbackAllowed': False})) from GlobalError
                Context.PreRoutedClusterInterfaceDesignsByPlacementFingerprint[Context.InterfaceCandidate.PlacementFingerprint] = Context.PreRoutedDesign
                Context.RoutedComponentTemplatesByPlacementFingerprint[Context.InterfaceCandidate.PlacementFingerprint] = Context.ComponentTemplate
                Context.Demand = Context.InterfaceCandidate.TopologyDemand
                Context.Objective = (0, Context.ChangedClusterCount, Context.Demand.PeakBoundaryDemand if Context.Demand is not None else 0, Context.Demand.GateFootprint if Context.Demand is not None else 0, Context.Demand.Hpwl if Context.Demand is not None else 0, Context.ComponentTemplate.RoutedTemplateFingerprint)
                Context.InterfaceFeasibleCandidates.append((Context.Objective, Context.InterfaceCandidate, Context.ComponentTemplate))
                Context.InterfaceStateProofs.append(ClusterInterfaceStateProof(PlacementStateFingerprint=Context.InterfaceCandidate.PlacementFingerprint, ComponentStateFingerprint=Context.ComponentStateFingerprint, ComponentVariant=Context.ComponentVariantForState, ComponentSelectionFingerprint=Context.ComponentSelectionFingerprint, Status='feasible', ChannelFingerprint=Context.ChannelFingerprint, TransformFingerprint=Context.TransformFingerprint, AssignmentFingerprints=(Context.ComponentTemplate.RoutedTemplateFingerprint,), DomainFingerprint=Context.ComponentProblem.ProblemFingerprint, ExpansionCount=Context.ComponentSolve.ExpansionCount, DomainComplete=Context.ComponentProblem.DomainComplete, OwnershipComplete=True, RealizabilityComplete=True, Exhaustive=False))
                Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.InterfaceCandidate.PlacementFingerprint, 'ComponentCutEpoch': Context.InterfaceCutEpoch, 'ComponentVariant': Context.ComponentVariantForState, 'RetainedPlacementResourceCacheHit': Context.RetainedPlacementResourceCacheHit, 'ActiveComponentCutSignals': sorted(Context.ActiveComponentCutSignals), 'Result': 'feasible-routed-component', 'Objective': list(Context.Objective), 'Transforms': Context.Transforms, 'ComponentRoutingProblem': Context.ComponentProblem.ToDictionary(), 'RoutedComponentTemplate': Context.ComponentTemplate.ToDictionary(), 'ComponentRoutingSolve': {'Status': Context.ComponentSolve.Status, 'ProofFingerprint': Context.ComponentSolve.ProofFingerprint, 'ExpansionCount': Context.ComponentSolve.ExpansionCount, 'Diagnostics': Context.ComponentSolve.Diagnostics}, 'OrdinaryGlobalHandoff': {'Entered': True, 'ImmutableClaims': True, **Context.HandoffDiagnostics, 'ExportedPortFingerprint': Context.ComponentTemplate.ExportedPortFingerprint}, 'PhysicalComponentStageTimings': list(Context.PhysicalComponentStageTimings)})
                CapturePortableRawPortalGeometryCaches(Context, Context.InterfaceResources)
                Context.InterfaceSolveIncompleteError = None
                break
            except RoutingStageError as Error:
                Context.PhysicalEligibilityRepairEndpointGateNamesBySignal = {
                    str(Signal): sorted(Names)
                    for Signal in Error.Failure.AffectedNets
                    for Names in (
                        SelectCompletePhysicalEligibilityRepairEndpointGateNames(
                            Error.Failure,
                            str(Signal),
                            Context.MaterializedInterfacePlacement,
                        ),
                    )
                    if Names
                }
                if Context.PhysicalEligibilityRepairEndpointGateNamesBySignal:
                    Error = RoutingStageError(replace(
                        Error.Failure,
                        Diagnostics={
                            **dict(Error.Failure.Diagnostics or {}),
                            'RepairEndpointGateNamesBySignal': (
                                Context
                                .PhysicalEligibilityRepairEndpointGateNamesBySignal
                            ),
                        },
                    ))
                Context.EligibilityRepeaterReadySignals = (
                    SelectExhaustedRepeaterAccessCutSignals(Error.Failure)
                )
                if (
                    Context.InterfaceWorkPhase == 'prepare-eligibility'
                    and Context.EligibilityRepeaterReadySignals
                ):
                    (
                        Context.EligibilityRepeaterReadyApplied,
                        Context.EligibilityRepeaterReadyFingerprint,
                    ) = ApplyCoordinatedCandidateDiversificationProfile(
                        Context.RetainedBaseInterfaceCandidate.Placement,
                        Context.EligibilityRepeaterReadySignals,
                        EnableRepeaterReadyPortalRepair=True,
                    )
                    if Context.EligibilityRepeaterReadyApplied:
                        Context.InterfaceAttemptDiagnostics.append({
                            'CandidateId': (
                                Context.RetainedBaseInterfaceCandidate
                                .CandidateId
                            ),
                            'PlacementFingerprint': (
                                Context.RetainedPlacementFingerprint
                            ),
                            'ComponentVariant': (
                                Context.ComponentVariantForState
                            ),
                            'Result': (
                                'repeater-ready-eligibility-retry'
                            ),
                            'Signals': sorted(
                                Context.EligibilityRepeaterReadySignals
                            ),
                            'RoutingControlProfileFingerprint': (
                                Context
                                .EligibilityRepeaterReadyFingerprint
                            ),
                            'ReusedPlacedGeometry': True,
                            'ExecutableLegacyRepairCascade': False,
                        })
                        Context.InterfaceCandidateQueue.insert(0, (
                            'prepare-eligibility',
                            Context.InterfaceIndex,
                            Context.RetainedBaseInterfaceCandidate,
                            Context.InterfaceCutEpoch,
                            Context.ComponentVariantForState,
                        ))
                        continue
                if Context.CapacityRepairConstraint is not None:
                    Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'bounded-proof-driven-repair-candidate-failed', 'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'SourceProofFingerprint': Context.CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(Context.CapacityRepairConstraint.Signals), 'FailureReason': Error.Failure.Reason.value, 'FailureStage': Error.Failure.Stage, 'FailureDetail': Error.Failure.Detail, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
                Context.CaptureResources = Context.RoutingResourcesByFingerprint.get(Context.InterfaceCandidate.PlacementFingerprint)
                if Context.CaptureResources is not None:
                    CapturePortableRawPortalGeometryCaches(Context, Context.CaptureResources)
                if Context.RoutedComponentHandoffEntered and Context.ComponentProblem is not None and (Context.ComponentSolve is not None) and (Context.ComponentTemplate is not None):
                    Context.LastGlobalHandoffError = Error
                    Context.GlobalHandoffPlacementAdvanced = EnqueueProofGuidedPhysicalPlacement(Context, Error.Failure, Context.InterfaceCandidate, Context.ComponentVariantForState)
                    Context.InterfaceStateProofs.append(ClusterInterfaceStateProof(PlacementStateFingerprint=Context.InterfaceCandidate.PlacementFingerprint, ComponentStateFingerprint=Context.ComponentStateFingerprint, ComponentVariant=Context.ComponentVariantForState, ComponentSelectionFingerprint=Context.ComponentSelectionFingerprint, Status='global-handoff-failed', ChannelFingerprint=Context.ChannelFingerprint, TransformFingerprint=Context.TransformFingerprint, AssignmentFingerprints=(Context.ComponentTemplate.RoutedTemplateFingerprint,), DomainFingerprint=Context.ComponentProblem.ProblemFingerprint, ExpansionCount=Context.ComponentSolve.ExpansionCount, DomainComplete=True, OwnershipComplete=True, RealizabilityComplete=True, Exhaustive=False))
                    Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.InterfaceCandidate.PlacementFingerprint, 'ComponentCutEpoch': Context.InterfaceCutEpoch, 'ComponentVariant': Context.ComponentVariantForState, 'RetainedPlacementResourceCacheHit': Context.RetainedPlacementResourceCacheHit, 'ActiveComponentCutSignals': sorted(Context.ActiveComponentCutSignals), 'Result': 'global-handoff-failed', 'Failure': Error.Failure.ToDictionary(), 'ComponentRoutingProblem': Context.ComponentProblem.ToDictionary(), 'RoutedComponentTemplate': Context.ComponentTemplate.ToDictionary(), 'ComponentRoutingSolve': {'Status': Context.ComponentSolve.Status, 'ProofFingerprint': Context.ComponentSolve.ProofFingerprint, 'ExpansionCount': Context.ComponentSolve.ExpansionCount, 'Diagnostics': Context.ComponentSolve.Diagnostics}, 'OrdinaryGlobalHandoff': {'Entered': True, 'Completed': False, 'ImmutableClaims': True, 'PlacementAdvanced': Context.GlobalHandoffPlacementAdvanced}, 'ComponentCutEpochAttempts': Context.StateAttemptDiagnostics, 'PhysicalComponentStageTimings': list(Context.PhysicalComponentStageTimings)})
                    if bool(Error.Failure.Diagnostics.get('SymbolicCapacityPlacementFeedback', False) if isinstance(Error.Failure.Diagnostics, dict) else False):
                        break
                    continue
                Context.PlacementSliceExpired = bool(Context.InterfaceDeadline is not Context.SharedInterfacePlanningDeadline and Context.InterfaceDeadline.IsExpired() and (not Context.SharedInterfacePlanningDeadline.IsExpired()))
                if Context.PlacementSliceExpired:
                    Context.AllPlacementPressureSignals = tuple(sorted({*map(str, Error.Failure.AffectedNets), *(str(Signal) for Attempt in Context.StateAttemptDiagnostics for Signal in Attempt.get('NoGoodSignals', ()) if str(Signal))}))
                    Context.FocusedPlacementPressureSignals = SelectFocusedPlacementInterfacePressureSignals(Context.StateAttemptDiagnostics)
                    Context.PlacementPressureSignals = Context.FocusedPlacementPressureSignals or Context.AllPlacementPressureSignals
                    Error = RoutingStageError(replace(Error.Failure, Diagnostics={**dict(Error.Failure.Diagnostics or {}), 'PlacementWorkSliceExpired': True, 'PlacementInterfacePressureSignals': list(Context.PlacementPressureSignals), 'AllPlacementInterfacePressureSignals': list(Context.AllPlacementPressureSignals), 'FocusedPlacementInterfacePressureSignals': list(Context.FocusedPlacementPressureSignals), 'SharedPlanningDeadline': Context.SharedInterfacePlanningDeadline.ToDictionary()}))
                Context.FailureDiagnostics = dict(Error.Failure.Diagnostics or {})
                if Context.SelectedComponentClusters:
                    Context.FailureDiagnostics.setdefault(
                        'SelectedComponentClusters',
                        list(Context.SelectedComponentClusters),
                    )
                    Context.FailureDiagnostics.setdefault(
                        'SelectedComponentSignals',
                        sorted(map(str, getattr(
                            Context.Channel,
                            'AffectedSignals',
                            (),
                        ))),
                    )
                    Error = RoutingStageError(replace(
                        Error.Failure,
                        Diagnostics=Context.FailureDiagnostics,
                    ))
                Context.ComponentSolveDiagnostics = Context.FailureDiagnostics.get('ComponentRoutingSolve', {})
                Context.ComponentProblemDiagnostics = Context.FailureDiagnostics.get('ComponentRoutingProblem', {})
                Context.ComponentSolveStatus = str(Context.ComponentSolveDiagnostics.get('Status', '')) if isinstance(Context.ComponentSolveDiagnostics, dict) else ''
                Context.RejectedAssignment = Context.FailureDiagnostics.get('RejectedInterfaceAssignment', {})
                Context.RejectedProblem = Context.RejectedAssignment.get('Problem', {}) if isinstance(Context.RejectedAssignment, dict) else {}
                Context.ExactEmptyTerminalProof = bool(Error.Failure.Stage == 'ClusterBoundaryLease' and Error.Failure.Detail.startswith('boundary lease terminal has no legal portal stem') and (not Context.InterfaceDeadline.IsExpired()))
                Context.CompleteAccessCertificateProof = bool(Error.Failure.Stage == 'ComponentAccessCertification' and Context.FailureDiagnostics.get('Complete', False) and (not Context.FailureDiagnostics.get('Feasible', True)))
                Context.CompletePhysicalAssemblyUnsat = IsCompletePhysicalAssemblyUnsatisfiable(Error.Failure.Reason, Context.FailureDiagnostics)
                Context.DomainComplete = bool(Context.RejectedProblem.get('DomainComplete', False) if isinstance(Context.RejectedProblem, dict) else False) or bool(Context.FailureDiagnostics.get('ClusterInterfaceDomainComplete', False) or Context.FailureDiagnostics.get('ComponentFabricConstructionComplete', False)) or Context.ExactEmptyTerminalProof or Context.CompleteAccessCertificateProof or Context.CompletePhysicalAssemblyUnsat
                if isinstance(Context.ComponentProblemDiagnostics, dict):
                    Context.DomainComplete = bool(Context.DomainComplete or Context.ComponentProblemDiagnostics.get('DomainComplete', False))
                Context.StateIncomplete = IsClusterInterfaceStateIncomplete(FailureReason=Error.Failure.Reason, InterfaceDeadlineExpired=Context.InterfaceDeadline.IsExpired(), ComponentSolveStatus=Context.ComponentSolveStatus, ExplicitCompleteUnsatProof=Context.CompletePhysicalAssemblyUnsat)
                Context.OwnershipCoreFingerprint = str(Context.FailureDiagnostics.get('AuthoritativeCutAccessDomainFingerprint', '') or Context.FailureDiagnostics.get('RepeatedAssignmentFingerprint', '') or BuildStableFingerprint(Context.FailureDiagnostics.get('ConflictGraph', {})))
                Context.DomainFingerprint = str(Context.FailureDiagnostics.get('AuthoritativeAccessDomainFingerprint', '') or Context.FailureDiagnostics.get('CertificateFingerprint', '') or (Context.RejectedProblem.get('ComponentFingerprint', '') if isinstance(Context.RejectedProblem, dict) else ''))
                Context.FinalOwnershipUnsatisfiable = bool(Context.ExactEmptyTerminalProof or Context.CompleteAccessCertificateProof or Context.CompletePhysicalAssemblyUnsat or Context.FailureDiagnostics.get('OwnershipSearchComplete', False) or Context.FailureDiagnostics.get('ComponentFabricConstructionComplete', False) or Context.FailureDiagnostics.get('CompleteAssignmentCutProof', False) or (isinstance(Context.FailureDiagnostics.get('MandatoryAccessProof'), dict) and Context.FailureDiagnostics['MandatoryAccessProof'].get('Complete', False) and (not Context.FailureDiagnostics['MandatoryAccessProof'].get('BudgetExhausted', False)) and (not Context.FailureDiagnostics['MandatoryAccessProof'].get('DeadlineExceeded', False))) or Context.FailureDiagnostics.get('AuthoritativeCutAccessDomainFingerprint', '') or Error.Failure.Detail.startswith('no complete cluster-interface') or Error.Failure.Detail.startswith('candidate-realizability nogoods exhausted'))
                if Context.ComponentSolveStatus:
                    Context.FinalOwnershipUnsatisfiable = Context.ComponentSolveStatus == 'architectural-unsatisfiable'
                Context.RealizabilityComplete = bool(Context.FinalOwnershipUnsatisfiable or Error.Failure.Detail.startswith('candidate-realizability nogoods exhausted'))
                if Context.ComponentSolveStatus:
                    Context.RealizabilityComplete = Context.ComponentSolveStatus == 'architectural-unsatisfiable'
                Context.StateExhaustive = bool(Context.DomainComplete and (not Context.StateIncomplete) and Context.RealizabilityComplete)
                if not Context.StateExhaustive:
                    Context.StateIncomplete = True
                if Context.StateExhaustive and (not Context.StateIncomplete) and Context.FinalOwnershipUnsatisfiable and Context.OwnershipCoreFingerprint:
                    Context.CoreIdentity = Context.ComponentStateFingerprint if Context.ComponentStateFingerprint else Context.InterfaceCandidate.PlacementFingerprint
                    Context.CoreAttemptKey = (Context.CoreIdentity, Context.OwnershipCoreFingerprint)
                    Context.CoreAttempts = Context.RepeatedOwnershipCoreAttempts.get(Context.CoreAttemptKey, 0) + 1
                    Context.RepeatedOwnershipCoreAttempts[Context.CoreAttemptKey] = Context.CoreAttempts
                    Context.PendingOwnedFrontierTopologyRepair = BuildPhysicalOwnedFrontierTopologyRepairCore(Error.Failure, Context.RetainedBaseInterfaceCandidate)
                    Context.PendingOwnedFrontierTopologyRepairDomainFingerprint = BuildOwnedFrontierTopologyRepairDomainFingerprint(Context.PendingOwnedFrontierTopologyRepair.Signals, Context.SignalLocalIncidenceFingerprints) if Context.PendingOwnedFrontierTopologyRepair is not None else ''
                    Context.OwnedFrontierTopologyPortfolioRemaining = bool(Context.PendingOwnedFrontierTopologyRepair is not None and Context.OwnedFrontierTopologyRepairAttemptCountByDomainFingerprint.get(Context.PendingOwnedFrontierTopologyRepairDomainFingerprint, 0) < 2)
                    Context.ProvenRequestedComponentStates = {Proof.ComponentStateFingerprint or Proof.PlacementStateFingerprint for Proof in Context.InterfaceStateProofs}
                    Context.GlobalComponentStateDomainExhausted = bool(not Context.InterfaceCandidateQueue and (not Context.PendingProofGuidedPlacementByComponentVariant) and (Context.RequestedComponentStateFingerprints <= Context.ProvenRequestedComponentStates))
                    if Context.CoreAttempts >= 2 and (not Context.OwnedFrontierTopologyPortfolioRemaining) and Context.GlobalComponentStateDomainExhausted:
                        Context.InterfaceSolveIncompleteError = RoutingStageError(replace(Error.Failure, Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='OwnedFrontierTopologyRepairExhausted' if Context.PendingOwnedFrontierTopologyRepair is not None else 'ClusterInterfaceSolveDuplicateUnsatCore', Detail='complete owned-frontier topology repair portfolio was exhausted without a feasible interface domain' if Context.PendingOwnedFrontierTopologyRepair is not None else Error.Failure.Detail, RepairActions=(), Diagnostics={**Context.FailureDiagnostics, 'RepeatedOwnershipCoreAttempts': Context.CoreAttempts, 'OwnershipUnsatCoreFingerprint': Context.OwnershipCoreFingerprint, 'ComponentStateFingerprint': Context.CoreIdentity, 'DomainFingerprint': Context.DomainFingerprint, 'CompletedComponentStateAttempts': list(Context.InterfaceAttemptDiagnostics), 'PhysicalLocalFactorDiversificationPortfolio': list(Context.LocalFactorDiversificationPortfolioDiagnostics), 'PhysicalOwnedFrontierTopologyRepairPortfolio': list(Context.OwnedFrontierTopologyRepairPortfolioDiagnostics), 'ComponentPlacementSearchOrder': 'component-outer-placement-inner', 'InterfaceSolve': {'Complete': False, 'DomainComplete': Context.DomainComplete, 'OwnershipComplete': True, 'RealizabilityComplete': Context.RealizabilityComplete, 'ExecutableRepairAllowed': False}}))
                        break
                Context.StateStatus = 'incomplete' if Context.StateIncomplete else 'ownership-unsatisfiable' if Context.FinalOwnershipUnsatisfiable else 'realizability-unsatisfiable'
                Context.RoutabilityCore = BuildComponentRoutabilityCore(Error.Failure, PlacementStateFingerprint=Context.InterfaceCandidate.PlacementFingerprint, ComponentStateFingerprint=Context.ComponentStateFingerprint, DomainFingerprint=Context.DomainFingerprint, CoreFingerprint=Context.OwnershipCoreFingerprint, Complete=bool(Context.StateExhaustive and (not Context.StateIncomplete) and Context.FinalOwnershipUnsatisfiable))
                Context.CoreFailure = Error.Failure
                if Context.RoutabilityCore is not None:
                    Context.CoreFailure = replace(Error.Failure, Diagnostics={**Context.FailureDiagnostics, 'ComponentRoutabilityCore': Context.RoutabilityCore.ToDictionary()})
                Context.InterfaceStateProofs.append(ClusterInterfaceStateProof(PlacementStateFingerprint=Context.InterfaceCandidate.PlacementFingerprint, ComponentStateFingerprint=Context.ComponentStateFingerprint, ComponentVariant=Context.ComponentVariantForState, ComponentSelectionFingerprint=Context.ComponentSelectionFingerprint, Status=Context.StateStatus, ChannelFingerprint=Context.ChannelFingerprint, TransformFingerprint=Context.TransformFingerprint, OwnershipUnsatCoreFingerprint=Context.OwnershipCoreFingerprint if Context.FinalOwnershipUnsatisfiable else '', OwnershipUnsatSignals=tuple(Error.Failure.AffectedNets if Context.FinalOwnershipUnsatisfiable else ()), RoutabilityCore=Context.RoutabilityCore, AssignmentFingerprints=tuple(Context.StateAssignmentFingerprints), RealizabilityNogoods=tuple(Context.StateRealizabilityNogoods), DomainFingerprint=Context.DomainFingerprint, ExpansionCount=int(Context.FailureDiagnostics.get('ExpansionCount', 0)), DomainComplete=Context.DomainComplete, OwnershipComplete=Context.DomainComplete and (not Context.StateIncomplete), RealizabilityComplete=Context.RealizabilityComplete, Exhaustive=Context.StateExhaustive))
                Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.InterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.InterfaceCandidate.PlacementFingerprint, 'ComponentCutEpoch': Context.InterfaceCutEpoch, 'ComponentVariant': Context.ComponentVariantForState, 'RetainedPlacementResourceCacheHit': Context.RetainedPlacementResourceCacheHit, 'ActiveComponentCutSignals': sorted(Context.ActiveComponentCutSignals), 'Result': 'incomplete' if Context.StateIncomplete else 'unsatisfiable', 'Failure': {'Reason': Error.Failure.Reason.value, 'Stage': Error.Failure.Stage, 'AffectedNets': list(Error.Failure.AffectedNets), 'Detail': Error.Failure.Detail, 'OwnershipUnsatCoreFingerprint': Context.OwnershipCoreFingerprint, 'ComponentRoutabilityCore': Context.RoutabilityCore.ToDictionary() if Context.RoutabilityCore is not None else None, 'DomainFingerprint': Context.DomainFingerprint, 'ExpansionCount': int(Context.FailureDiagnostics.get('ExpansionCount', 0)), 'DomainComplete': Context.DomainComplete, 'OwnershipComplete': Context.DomainComplete and (not Context.StateIncomplete), 'RealizabilityComplete': Context.RealizabilityComplete, 'Exhaustive': Context.StateExhaustive, 'ComponentRoutingSolve': Context.ComponentSolveDiagnostics, 'ComponentRoutingProblem': Context.ComponentProblemDiagnostics, 'Diagnostics': Context.FailureDiagnostics}, 'Transforms': Context.Transforms, 'RealizabilityAttempts': Context.StateAttemptDiagnostics, 'PhysicalComponentStageTimings': list(Context.PhysicalComponentStageTimings), 'RealizabilityNogoods': [Nogood.ToDictionary() for Nogood in Context.StateRealizabilityNogoods]})
                Context.GeneratedArchitecturePlacementAdvanced = False
                if Error.Failure.Stage == 'InterClusterRoutingChannelMaterialization' and Context.RetainedPlacementFingerprint in Context.ProofGuidedGenerationSourceByPlacementFingerprint and not Context.AccessRepairInterfacePlanningDeadline.IsExpired():
                    Context.GenerationFailure, Context.GenerationSourceCandidate, Context.GenerationComponentVariant = Context.ProofGuidedGenerationSourceByPlacementFingerprint.pop(Context.RetainedPlacementFingerprint)
                    Context.GeneratedArchitecturePlacementAdvanced = EnqueueProofGuidedPhysicalPlacement(Context, Context.GenerationFailure, Context.GenerationSourceCandidate, Context.GenerationComponentVariant)
                    Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.RetainedBaseInterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'ComponentVariant': Context.ComponentVariantForState, 'Result': 'generated-channel-architecture-rejected-advance-repair', 'PlacementAdvanced': Context.GeneratedArchitecturePlacementAdvanced, 'SourceFailureStage': Error.Failure.Stage, 'SourceFailureReason': Error.Failure.Reason.value})
                    if Context.GeneratedArchitecturePlacementAdvanced:
                        continue
                Context.CompleteSymbolicCapacityPlacementFeedback = bool(Context.FailureDiagnostics.get('SymbolicCapacityPlacementFeedback', False) and Context.FailureDiagnostics.get('PlacementInterfacePressureSignals', ()))
                if Context.StateExhaustive and (not Context.StateIncomplete) or Context.CompleteSymbolicCapacityPlacementFeedback:
                    Context.PortDomainSizes = Context.FailureDiagnostics.get('PortDomainSizes', {})
                    Context.PortDomainComplete = Context.FailureDiagnostics.get('PortDomainGenerationComplete', {})
                    Context.CompleteEmptyPortSignals = tuple(sorted((str(Signal) for Signal, Size in (Context.PortDomainSizes.items() if isinstance(Context.PortDomainSizes, dict) else ()) if int(Size) == 0 and isinstance(Context.PortDomainComplete, dict) and bool(Context.PortDomainComplete.get(Signal, False)))))
                    Context.ProvenPortAssignmentCore = tuple(sorted(set(map(str, Context.FailureDiagnostics.get('PortAssignmentUnsatCoreSignals', ())))))
                    if (
                        Context.ProvenPortAssignmentCore
                        and len(Context.ProvenPortAssignmentCore) <= 3
                        and Context.FailureDiagnostics.get(
                            'PortAssignmentProofComplete',
                            False,
                        )
                        and Context.FailureDiagnostics.get(
                            'PortAssignmentUnsatCoreMinimal',
                            False,
                        )
                    ):
                        Context.PreferredPhysicalComponentPortUnsatCoreSignals = Context.ProvenPortAssignmentCore
                    Context.PlacementPressureCore = tuple(sorted(set(map(str, Context.FailureDiagnostics.get('PlacementInterfacePressureSignals', ())))))
                    Context.ComponentAccessCoreSignals = Context.PlacementPressureCore or Context.ProvenPortAssignmentCore or Context.CompleteEmptyPortSignals or tuple(map(str, Error.Failure.AffectedNets))
                    Context.PlacementAdvanced = False
                    if Context.ComponentAccessCoreSignals:
                        Context.ActiveComponentCutSignals.update(Context.ComponentAccessCoreSignals)
                        Context.CumulativeProofGuidedRelocationSignals.update(Context.ComponentAccessCoreSignals)
                        ReorderRemainingPlacementsForAccessCore(Context, Context.RetainedPlacementFingerprint)
                        Context.CurrentCapacityRepairConstraint = BuildPhysicalInterfaceRepairCore(
                            Context.CoreFailure,
                            Context.RetainedBaseInterfaceCandidate,
                        )
                        Context.InheritedCapacityRepairConstraint = Context.CapacityRepairConstraintByPlacementFingerprint.get(
                            Context.RetainedPlacementFingerprint,
                        )
                        Context.CapacityRepairRefinementReady = bool(
                            Context.CurrentCapacityRepairConstraint is not None
                            and Context.InheritedCapacityRepairConstraint is not None
                            and Context.CurrentCapacityRepairConstraint.SourceProofFingerprint
                            != Context.InheritedCapacityRepairConstraint.SourceProofFingerprint
                        )
                        Context.CapacityRepairRefinementAdvanced = (
                            EnqueueProofGuidedPhysicalPlacement(
                                Context,
                                Context.CoreFailure,
                                Context.RetainedBaseInterfaceCandidate,
                                Context.ComponentVariantForState,
                            )
                            if Context.CapacityRepairRefinementReady
                            else False
                        )
                        Context.PendingCapacityRepairReady = any(PendingState[4] for PendingState in Context.PendingProofGuidedPlacementByComponentVariant.values())
                        Context.OwnedFrontierTopologyRepairAdvanced = False if Context.CapacityRepairRefinementAdvanced or Context.PendingCapacityRepairReady else EnqueueOwnedFrontierTopologyRepair(Context, Error.Failure, Context.RetainedBaseInterfaceCandidate, Context.ComponentVariantForState) if Context.StateExhaustive and (not Context.StateIncomplete) else False
                        Context.LocalFactorDiversificationAdvanced = False if Context.CapacityRepairRefinementAdvanced or Context.PendingCapacityRepairReady or Context.OwnedFrontierTopologyRepairAdvanced else EnqueueSingletonLocalFactorDiversification(Context, Error.Failure, Context.RetainedBaseInterfaceCandidate, Context.ComponentVariantForState) if (Context.StateExhaustive and (not Context.StateIncomplete)) or Context.CompleteSymbolicCapacityPlacementFeedback else False
                        Context.PlacementAdvanced = Context.CapacityRepairRefinementAdvanced or Context.PendingCapacityRepairReady or Context.OwnedFrontierTopologyRepairAdvanced or Context.LocalFactorDiversificationAdvanced or EnqueueProofGuidedPhysicalPlacement(Context, Context.CoreFailure, Context.RetainedBaseInterfaceCandidate, Context.ComponentVariantForState)
                        Context.InterfaceAttemptDiagnostics.append({'CandidateId': Context.RetainedBaseInterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.RetainedPlacementFingerprint, 'ComponentCutEpoch': Context.InterfaceCutEpoch, 'ComponentVariant': Context.ComponentVariantForState, 'Result': 'capacity-repair-refinement-prioritized' if Context.CapacityRepairRefinementAdvanced else 'pending-capacity-repair-prioritized' if Context.PendingCapacityRepairReady else 'owned-frontier-topology-repair' if Context.OwnedFrontierTopologyRepairAdvanced else 'singleton-local-factor-diversification' if Context.LocalFactorDiversificationAdvanced else 'component-access-core-ranked-remaining-placements', 'ActiveComponentCutSignals': sorted(Context.ActiveComponentCutSignals), 'RemainingPlacementAccessScores': [{'PlacementFingerprint': Candidate.PlacementFingerprint, 'Score': list(BuildComponentAccessFeedbackPlacementScore(Candidate, Context.ActiveComponentCutSignals))} for Candidate in Context.OrderedPlacements if Candidate.PlacementFingerprint not in Context.ProofGuidedPlacementFingerprints], 'SourceFailureFingerprint': Context.FailureDiagnostics.get('PortAssignmentUnsatCoreFingerprint', Context.OwnershipCoreFingerprint)})
                if Context.StateIncomplete or Context.CompleteSymbolicCapacityPlacementFeedback:
                    Context.PreservePhysicalReason = Error.Failure.Reason in {RoutingFailureReason.ClusterInterfaceSolveIncomplete, RoutingFailureReason.PhysicalComponentAssemblyIncomplete}
                    Context.InterfaceSolveIncompleteError = RoutingStageError(replace(Error.Failure, Reason=Error.Failure.Reason if Context.PreservePhysicalReason else RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage=Error.Failure.Stage if Context.PreservePhysicalReason else 'ClusterInterfaceSolveIncomplete', RepairActions=(), Diagnostics={**Context.FailureDiagnostics, 'PhysicalCapacityRepairPortfolio': list(Context.CapacityRepairPortfolioDiagnostics), 'PhysicalLocalFactorDiversificationPortfolio': list(Context.LocalFactorDiversificationPortfolioDiagnostics), 'PhysicalOwnedFrontierTopologyRepairPortfolio': list(Context.OwnedFrontierTopologyRepairPortfolioDiagnostics), 'CompletedComponentStateAttempts': list(Context.InterfaceAttemptDiagnostics), 'ComponentPlacementSearchOrder': 'component-outer-placement-inner', 'InterfaceSolve': {'Complete': False, 'DomainComplete': Context.DomainComplete, 'OwnershipComplete': False, 'RealizabilityComplete': Context.RealizabilityComplete, 'ExecutableRepairAllowed': False}}))
                    if (Context.CompleteSymbolicCapacityPlacementFeedback or (Context.FailureDiagnostics.get('PlacementWorkSliceExpired', False) and Context.PlacementAdvanced)) and (not Context.InterfaceDeadline.IsExpired()):
                        continue
                    if any((Candidate.PlacementFingerprint in Context.CapacityRepairConstraintByPlacementFingerprint for _QueuedPhase, _QueuedIndex, Candidate, _QueuedCutEpoch, _QueuedComponentVariant in Context.InterfaceCandidateQueue)) and (not Context.AccessRepairInterfacePlanningDeadline.IsExpired()):
                        continue
                    break
        Context.LatestInterfaceProofByComponentState: dict[str, ClusterInterfaceStateProof] = {}
        for Context.StateProof in Context.InterfaceStateProofs:
            Context.LatestInterfaceProofByComponentState[getattr(Context.StateProof, 'ComponentStateFingerprint', '') or Context.StateProof.PlacementStateFingerprint] = Context.StateProof
        Context.InterfaceStateProofs = list(Context.LatestInterfaceProofByComponentState.values())
        Context.PlacementPortfolioDomainComplete = bool(Context.InterfacePortfolioAudits and all((Audit.Classification not in {'pruned-by-scoring-budget', 'pruned-by-work-budget', 'search-incomplete'} for Audit in Context.InterfacePortfolioAudits)))
        Context.InterfaceProof = BuildClusterInterfaceUnsatProof(Context.InterfaceStateProofs, ExpectedComponentStateFingerprints=tuple(sorted(Context.RequestedComponentStateFingerprints)), PlacementPortfolioDomainComplete=Context.PlacementPortfolioDomainComplete)
        if not Context.InterfaceFeasibleCandidates and (not Context.InterfaceProof['Complete']) and (Context.InterfaceSolveIncompleteError is None):
            Context.NamedStateDomainComplete = bool(Context.InterfaceProof.get('NamedComponentStateProofComplete', False))
            Context.InterfaceSolveIncompleteError = RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='ClusterInterfacePlacementPortfolioIncomplete' if Context.NamedStateDomainComplete else 'ClusterInterfaceComponentStateDomainIncomplete', Detail='all named retained placements have complete proofs, but placement generation was bounded' if Context.NamedStateDomainComplete else 'the retained component-placement search domain does not have one complete proof per requested component state', RepairActions=(), Diagnostics={'InterfaceSolve': Context.InterfaceProof, 'ComponentPlacementSearchStateCount': len(Context.RequestedComponentStateFingerprints), 'BroadFallbackAllowed': False, 'ExecutableLegacyRepairCascade': False}))
        Context.InterfaceProofFingerprint = str(Context.InterfaceProof['ProofFingerprint'])
        Context.InterfacePortfolioProblem = ClusterInterfacePortfolioProblem(PlacementStates=tuple(sorted(Context.InterfacePlacementStatesByFingerprint.values(), key=lambda State: State.StateFingerprint)), MaximumPlacementStates=Context.InterfaceSearchStateCount, MaximumAffectedClusters=3, StateAudits=Context.InterfacePortfolioAudits)
        Context.PlacementGenerationDecisions.append({'Result': 'exact-cluster-interface-solve', 'PhysicalResourceModel': 'dedicated-cluster-interface-deck-v1', 'RequestedStateCount': Context.InterfaceSearchStateCount, 'MaximumComponentSelectionCount': Context.MaximumComponentVariants, 'MaximumProofGuidedRetainedPlacementCount': Context.MaximumProofGuidedRetainedPlacements, 'MaximumProofGuidedGeneratedPlacementCount': Context.MaximumProofGuidedGeneratedPlacements, 'ComponentPlacementSearchStateCount': len(Context.RequestedComponentStateFingerprints), 'ComponentSelectionOrder': 'component-outer-placement-inner', 'GeneratedStateCount': len(Context.RawInterfaceCandidates), 'InterfaceDistinctStateCount': len(Context.InterfaceCandidates), 'PortfolioStateAudit': [Audit.ToDictionary() for Audit in Context.InterfacePortfolioAudits], 'GeneratorRejectionAudit': Context.InterfaceGeneratorRejectionAudit, 'PortfolioProblem': Context.InterfacePortfolioProblem.ToDictionary(), 'AttemptedStateCount': len(Context.InterfaceAttemptDiagnostics), 'FeasibleStateCount': len(Context.InterfaceFeasibleCandidates), 'Attempts': Context.InterfaceAttemptDiagnostics, 'StateProofs': Context.InterfaceProof['StateProofs'], 'ProofFingerprint': Context.InterfaceProofFingerprint, 'StageSchedule': Context.InterfaceStageSchedule.ToDictionary(), 'BroadFallbackAllowed': False, 'ExecutableLegacyRepairCascade': False})
        if Context.InterfaceSolveIncompleteError is not None:
            Context.IncompleteFailure = Context.InterfaceSolveIncompleteError.Failure
            Context.IncompleteDiagnostics = dict(Context.IncompleteFailure.Diagnostics or {})
            Context.RepairPlacementFingerprints = frozenset(Context.CapacityRepairCandidateByPlacementFingerprint)
            Context.AttemptedRepairPlacementFingerprints = tuple(sorted({str(Attempt.get('PlacementFingerprint', '')) for Attempt in Context.InterfaceAttemptDiagnostics if str(Attempt.get('PlacementFingerprint', '')) in Context.RepairPlacementFingerprints} | {str(Attempt.get('PlacementFingerprint', '')) for Attempt in Context.CapacityRepairPortfolioDiagnostics if Attempt.get('Result') in {'capacity-pair-repair-dequeued', 'bounded-proof-driven-repair-candidate-failed'} and str(Attempt.get('PlacementFingerprint', '')) in Context.RepairPlacementFingerprints}))
            if Context.AttemptedRepairPlacementFingerprints:
                Context.IncompleteFailure = replace(Context.IncompleteFailure, Detail='bounded proof-driven repair portfolio was evaluated without a feasible assignment')
            Context.InterfaceSolveIncompleteError = RoutingStageError(replace(Context.IncompleteFailure, Diagnostics={**Context.IncompleteDiagnostics, 'PhysicalCapacityRepairPortfolio': list(Context.CapacityRepairPortfolioDiagnostics), 'PhysicalLocalFactorDiversificationPortfolio': list(Context.LocalFactorDiversificationPortfolioDiagnostics), 'PhysicalOwnedFrontierTopologyRepairPortfolio': list(Context.OwnedFrontierTopologyRepairPortfolioDiagnostics), 'CompletedComponentStateAttempts': list(Context.InterfaceAttemptDiagnostics), 'CapacityRepairGeneratedPlacementFingerprints': sorted(Context.RepairPlacementFingerprints), 'CapacityRepairAttemptedPlacementFingerprints': list(Context.AttemptedRepairPlacementFingerprints), 'InterfaceSolve': Context.InterfaceProof}))
            Context.LastRoutingError = Context.InterfaceSolveIncompleteError
            Context.LastStructuredRoutingError = Context.InterfaceSolveIncompleteError
            Context.CandidateRoutingIterable = ()
        elif not Context.InterfaceFeasibleCandidates and Context.LastGlobalHandoffError is not None:
            Context.GlobalHandoffAttempts = tuple((StateAttempt for Attempt in Context.InterfaceAttemptDiagnostics for StateAttempt in (*Attempt.get('RealizabilityAttempts', ()), *Attempt.get('ComponentCutEpochAttempts', ())) if StateAttempt.get('Result') == 'detailed-failure-reject-physical-plan'))
            Context.ComponentPortfolioNogoodFingerprint = BuildStableFingerprint({'InterfaceProofFingerprint': Context.InterfaceProofFingerprint, 'PlacementStateFingerprints': [getattr(Proof, 'ComponentStateFingerprint', '') or Proof.PlacementStateFingerprint for Proof in Context.InterfaceStateProofs], 'GlobalHandoffFailureFingerprints': [BuildStableFingerprint(Attempt.get('UnderlyingFailure', {})) for Attempt in Context.GlobalHandoffAttempts]})
            Context.ComponentPortfolioNogoodRecord = {'Fingerprint': Context.ComponentPortfolioNogoodFingerprint, 'InterfaceProofFingerprint': Context.InterfaceProofFingerprint, 'AffectedClusterLimit': 3, 'RetainedStateCount': len(Context.InterfaceStateProofs), 'GlobalHandoffFailureCount': len(Context.GlobalHandoffAttempts), 'AffectedSignals': sorted({str(Signal) for Attempt in Context.GlobalHandoffAttempts for Signal in Attempt.get('UnderlyingFailure', {}).get('AffectedNets', ())}), 'ActiveSignals': sorted(Context.ActiveComponentCutSignals), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Started, 6)}
            Context.PortfolioFailure = RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentDetailedRoutingFailed, Stage='PhysicalComponentAssemblyDomainExhausted', AffectedNets=tuple(sorted({str(Signal) for Attempt in Context.GlobalHandoffAttempts for Signal in Attempt.get('UnderlyingFailure', {}).get('AffectedNets', ())})), Detail='detailed routing rejected every complete physical assembly plan across the retained placement states', RepairActions=(), Diagnostics={'RejectedPhysicalAssemblyDomain': {**Context.ComponentPortfolioNogoodRecord, 'RejectedPlanFingerprints': sorted({str(Attempt.get('PhysicalAssemblyPlanFingerprint', '')) for Attempt in Context.GlobalHandoffAttempts if Attempt.get('PhysicalAssemblyPlanFingerprint')}), 'BroadFallbackAllowed': False, 'SignalLevelRepairAllowed': False, 'PlacementRegenerationAllowed': False}, 'InterfaceSolve': {**Context.InterfaceProof, 'Attempts': Context.InterfaceAttemptDiagnostics, 'PortfolioProblem': Context.InterfacePortfolioProblem.ToDictionary()}, 'LastGlobalHandoffFailure': Context.LastGlobalHandoffError.Failure.ToDictionary()}))
            Context.LastRoutingError = Context.PortfolioFailure
            Context.LastStructuredRoutingError = Context.PortfolioFailure
            Context.CandidateRoutingIterable = ()
        elif not Context.InterfaceFeasibleCandidates:
            Context.InterfaceFailure = RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='ClusterInterfaceRetainedPlacementDomainIncomplete', Detail='every retained channelized placement was rejected, but the legal placement domain was not exhaustively enumerated', RepairActions=(), Diagnostics={'InterfaceSolve': {**Context.InterfaceProof, 'Attempts': Context.InterfaceAttemptDiagnostics, 'PortfolioProblem': Context.InterfacePortfolioProblem.ToDictionary(), 'PhysicalResourceModel': 'dedicated-cluster-interface-deck-v1', 'NamedPlacementsUnsatisfiable': bool(Context.InterfaceProof.get('NamedComponentStateProofComplete', False)), 'ArchitectureInsufficient': False, 'ArchitecturalUnsatisfiabilityProven': False, 'BroadFallbackAllowed': False}}))
            Context.LastRoutingError = Context.InterfaceFailure
            Context.LastStructuredRoutingError = Context.InterfaceFailure
            Context.CandidateRoutingIterable = ()
        else:
            Context._SelectedInterfaceObjective, Context.SelectedInterfaceCandidate, Context.SelectedRoutedComponentTemplate = min(Context.InterfaceFeasibleCandidates, key=lambda Value: Value[0])
            Context.CandidateRoutingIterable = (Context.SelectedInterfaceCandidate,)
            Context.JointPlacementStateEvents.append({'Status': 'routed-component-template-selected', 'CandidateId': Context.SelectedInterfaceCandidate.CandidateId, 'PlacementFingerprint': Context.SelectedInterfaceCandidate.PlacementFingerprint, 'Objective': list(Context._SelectedInterfaceObjective), 'RoutedTemplateFingerprint': Context.SelectedRoutedComponentTemplate.RoutedTemplateFingerprint, 'ExportedPortFingerprint': Context.SelectedRoutedComponentTemplate.ExportedPortFingerprint, 'ExecutableLegacyRepairCascade': False})

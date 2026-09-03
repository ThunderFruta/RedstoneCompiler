"""One bounded phase of the placement and routing flow."""

from __future__ import annotations

from dataclasses import replace
import os
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Execution.Reliability import RoutingDeadline
from .Feedback import ExpandAnalogousMandatoryRepairSignals, ExtractCompletedEscalationRelocationSignals, ExtractPlacementRelocationSignals, FailureRequestsPlacementAdvance, FailureRequiresPackedAccessRepair, IsHigherOrderAssignmentCut, ShouldDiversifyRepeatedAssignmentCut
from .Portfolios import ApplyCoordinatedCandidateDiversificationProfile, ApplyRemainingExactLegalJointStateCount, AssignmentCutHasBoundedExactCore, AssignmentCutRepeatsAcrossDistinctPlacementOwnership, BoundedAssignmentCutRepeatsAcrossDistinctOwnership, BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership, PlacementCandidateMatchesActiveJointPortfolio, SelectTransactionalEndpointRepairSignals, SelectTransactionalRepairClusterCount, SerializedPlacementAssignmentConstraintsAreActive, ShouldAdmitPostDiversificationOwnershipRepair, ShouldBoundClusterPinBankRepairProbe, ShouldDeferTopologyCutForRetainedPortfolioSibling, ShouldStopTransactionalRepairVariantGeneration, TransactionalCutMayEscalateRepairClusterCount, TransactionalCutRepairSignals, TransactionalCutRequiresCoordinatedClusterRepair, TransactionalCutRevisitsAncestorInterface, TransactionalCutStrictlyNarrowsParentInterface
from .Preparation import BuildFrozenEnvelopeRoutingPolicy, DenseRetainedLeaseProofSliceSeconds, IsAuthoritativeMandatoryAccessConflict, PlacementFeedbackRoutingSlotCount, PromoteAuthoritativeMandatoryAccessConflict, RequiresDenseBoundaryRoutingReserve, RetainedPlacementRoutingSlotCount, TopologyPortfolioRoutingFraction
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
)
from .AttemptHistory import (
    CapturePortableRawPortalGeometryCaches,
    SeedPortableRawPortalGeometryCaches,
    _RecordAssignmentCut,
)
from .PhysicalAssembly import (
    CheckCandidateValidation,
)
from .RoutingAttempts import (
    MaterializeSelectedJointPlacementLocalRouting,
    RecordRoutedCandidate,
    ReportRoutingProgress,
    _RouteWithFailedLocalClaimsReleased,
    _PublishTransactionalClusterEndpointRepair,
    _TransactionalEndpointRepairPortfolioFingerprint,
)


def RoutePlacementCandidates(Context):
    for Context.CandidateRecord in Context.CandidateRoutingIterable:
        try:
            Context.Deadline.RaiseIfExpired('PlacementCandidateSelection', {'PlacementAttempts': Context.PlacementAttemptFailures})
        except RoutingStageError as Error:
            Context.LastRoutingError = Error
            Context.LastStructuredRoutingError = Error
            break
        Context.CandidatePlacement = Context.CandidateRecord.Placement
        Context.Placement = Context.CandidatePlacement
        Context.RoutingSpacing = Context.CandidateRecord.RoutingSpacing
        Context.CandidatePlacement = MaterializeSelectedJointPlacementLocalRouting(Context, Context.CandidateRecord, lambda Diagnostics: Context.Deadline.RaiseIfExpired('PlacementCandidateMaterialization', {'CandidateId': Context.CandidateRecord.CandidateId, **Diagnostics}))
        if not Context.ExactClusterInterfaceSolveEnabled and Context.CandidateRecord.TopologyDemand is not None and RequiresDenseBoundaryRoutingReserve(Context.CandidateRecord.TopologyDemand, Context.Policy) and Context.ConsumedPairedLeaseRepairProfileFingerprints and Context.PlacementCoordinatedCandidateDiversificationSignals and (not dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__ClusterPinBankRepair__', {})):
            Context.PreappliedDenseProfile, Context.PreappliedDenseProfileFingerprint = ApplyCoordinatedCandidateDiversificationProfile(Context.CandidatePlacement, Context.PlacementCoordinatedCandidateDiversificationSignals)
            if Context.PreappliedDenseProfile:
                Context.JointPlacementStateEvents.append({'Status': 'dense-profile-preapplied-before-routing', 'CandidateId': Context.CandidateRecord.CandidateId, 'Signals': sorted(Context.PlacementCoordinatedCandidateDiversificationSignals), 'ProfileFingerprint': Context.PreappliedDenseProfileFingerprint})
        if Context.CandidatePlacement is not Context.CandidateRecord.Placement:
            Context.CandidateRecord = replace(Context.CandidateRecord, Placement=Context.CandidatePlacement)
        if not Context.ExactClusterInterfaceSolveEnabled and Context.TopologyDemand.RequiresJointPortfolio and (Context.CandidateRecord.CutInterfaceDifference > 0):
            Context.CandidatePlacement.Placed.LocalRouteDiagnostics = {**(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}), '__CandidateRealizabilityContinuation__': {'Eligible': Context.CandidateRecord.AccessDistinctCandidateCount == 1, 'CandidateId': Context.CandidateRecord.CandidateId, 'CutInterfaceDifference': Context.CandidateRecord.CutInterfaceDifference, 'AccessDistinctCandidateCount': Context.CandidateRecord.AccessDistinctCandidateCount, 'AssignmentCutFingerprint': Context.CandidateRecord.AssignmentCutFingerprint}}
        Context.Placement = Context.CandidatePlacement
        Context.CandidateResources = Context.RoutingResourcesByCandidateId.get(Context.CandidateRecord.CandidateId) or Context.RoutingResourcesByFingerprint.get(Context.CandidateRecord.PlacementFingerprint)
        if Context.CandidateResources is None:
            Context.CandidateResources = Context.Services.BuildRoutingResources(Context.CandidatePlacement.Placed, WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('PlacementCandidateResourceMaterialization', {'CandidateId': Context.CandidateRecord.CandidateId, **Diagnostics}))
            Context.RoutingResourcesByFingerprint[Context.CandidateRecord.PlacementFingerprint] = Context.CandidateResources
        if Context.ExactClusterInterfaceSolveEnabled:
            Context.FrozenComponentTemplate = Context.RoutedComponentTemplatesByPlacementFingerprint.get(Context.CandidateRecord.PlacementFingerprint)
            if Context.FrozenComponentTemplate is None:
                raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceInvariantViolation, Stage='RoutedComponentHandoff', Detail='the selected placement lost its immutable routed component before global routing', Diagnostics={'CandidateId': Context.CandidateRecord.CandidateId, 'PlacementFingerprint': Context.CandidateRecord.PlacementFingerprint}))
            Context.PlacedTemplates = tuple(getattr(Context.CandidatePlacement.Placed, 'RoutedComponentTemplates', ()) or ())
            if not any((Value.RoutedTemplateFingerprint == Context.FrozenComponentTemplate.RoutedTemplateFingerprint for Value in Context.PlacedTemplates)):
                raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceInvariantViolation, Stage='RoutedComponentHandoff', Detail='the selected placement identity does not contain the selected routed-component template', Diagnostics={'CandidateId': Context.CandidateRecord.CandidateId, 'PlacementFingerprint': Context.CandidateRecord.PlacementFingerprint}))
            Context.CandidateResources.FrozenRoutedComponentTemplate = Context.FrozenComponentTemplate
        SeedPortableRawPortalGeometryCaches(Context, Context.CandidateResources)
        Context.CandidateJointDiagnostics = dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__JointClusterPlacement__', {})
        Context.CandidateJointIndex = Context.CandidateJointDiagnostics.get('SelectedCandidateIndex')
        Context.AttemptedCandidateIds = {str(Entry.get('CandidateId')) for Entry in Context.PlacementAttemptFailures if Entry.get('CandidateId') is not None}
        Context.HasRemainingPlacementAlternative = False if Context.SinglePackedComponent else any((Candidate.CandidateId != Context.CandidateRecord.CandidateId and Candidate.CandidateId not in Context.AttemptedCandidateIds for Candidate in Context.OrderedPlacements)) or bool(Context.PendingJointPlacementStates) or len(Context.ConsumedDeferredRequestIndexes) < len(Context.GenerationPlan.DeferredRequests)
        Context.RemainingRuntimeSeconds = max(0.001, Context.Deadline.RemainingSeconds())
        Context.ActiveRelocatedPortfolioCandidate = PlacementCandidateMatchesActiveJointPortfolio(Context.CandidateRecord, Context.ActiveJointPortfolioIdentityFingerprint)
        Context.RemainingRetainedCandidates = 1 if Context.SinglePackedComponent else sum((Candidate.CandidateId not in Context.AttemptedCandidateIds for Candidate in ([ActiveCandidate for ActiveCandidate in Context.CandidateRecords if PlacementCandidateMatchesActiveJointPortfolio(ActiveCandidate, Context.ActiveJointPortfolioIdentityFingerprint)] if Context.ActiveRelocatedPortfolioCandidate else Context.OrderedPlacements))) + len(Context.PendingJointPlacementStates)
        Context.RemainingStateCountRebound = ApplyRemainingExactLegalJointStateCount(Context.CandidatePlacement, max(1, Context.RemainingRetainedCandidates))
        if Context.RemainingStateCountRebound:
            Context.JointPlacementStateEvents.append({'Status': 'remaining-joint-state-count-rebound', 'CandidateId': Context.CandidateRecord.CandidateId, 'RemainingExactLegalRetainedStateCount': max(1, Context.RemainingRetainedCandidates)})
        Context.HighFanoutFeedbackRoutingSlots = PlacementFeedbackRoutingSlotCount(HasRemainingPlacementAlternative=Context.HasRemainingPlacementAlternative, ReconvergentAccessPressure=Context.TopologyPressure.ReconvergentAccessPressure, AttemptedCandidateCount=len(Context.AttemptedCandidateIds))
        Context.DenseBoundaryLeaseRouting = bool(Context.CandidateRecord.TopologyDemand is not None and RequiresDenseBoundaryRoutingReserve(Context.CandidateRecord.TopologyDemand, Context.Policy))
        Context.CandidateRouteDiagnostics = Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}
        Context.CandidateRelocationDiagnostics = Context.CandidateRouteDiagnostics.get('__PlacementRelocation__', {})
        Context.CandidateJointDiagnostics = Context.CandidateRouteDiagnostics.get('__JointClusterPlacement__', {})
        Context.CandidateSerializedAssignmentConstraints = Context.CandidateJointDiagnostics.get('ActiveAssignmentConstraints', Context.CandidateJointDiagnostics.get('AssignmentConstraints', {})) if isinstance(Context.CandidateJointDiagnostics, dict) else {}
        Context.CandidateHasHigherOrderCutConstraints = bool(isinstance(Context.CandidateSerializedAssignmentConstraints, dict) and (Context.CandidateSerializedAssignmentConstraints.get('ActiveHigherOrderSignalSets') or Context.CandidateSerializedAssignmentConstraints.get('HigherOrderSignalSets')))
        Context.IsBoundedDenseLeaseControlRetry = bool(Context.DenseBoundaryLeaseRouting and isinstance(Context.CandidateRelocationDiagnostics, dict) and (int(Context.CandidateRelocationDiagnostics.get('CoordinatedCandidateDiversificationFixedLevel', 0)) == 1) and (not (isinstance(Context.CandidateJointDiagnostics, dict) and SerializedPlacementAssignmentConstraintsAreActive(Context.CandidateJointDiagnostics.get('ActiveAssignmentConstraints')))))
        Context.HasClusterPinBankRepair = bool(Context.DenseBoundaryLeaseRouting and isinstance(Context.CandidateRouteDiagnostics.get('__ClusterPinBankRepair__', {}), dict) and Context.CandidateRouteDiagnostics.get('__ClusterPinBankRepair__', {}).get('Signals'))
        Context.IsTransactionalEndpointRepair = bool(isinstance(Context.CandidateRouteDiagnostics.get('__PlacementRecipe__', {}), dict) and Context.CandidateRouteDiagnostics.get('__PlacementRecipe__', {}).get('TransactionalClusterEndpointRepair', False))
        Context.ReserveClusterPinBankRepairSeconds = bool(Context.DenseBoundaryLeaseRouting and (not Context.HasClusterPinBankRepair) and isinstance(Context.CandidateJointDiagnostics, dict) and SerializedPlacementAssignmentConstraintsAreActive(Context.CandidateJointDiagnostics.get('ActiveAssignmentConstraints')))
        Context.PlannedRoutingSlots = 1 if Context.SinglePackedComponent else RetainedPlacementRoutingSlotCount(RemainingRetainedCandidates=Context.RemainingRetainedCandidates, HighFanoutFeedbackRoutingSlots=Context.HighFanoutFeedbackRoutingSlots, HasRemainingPlacementAlternative=Context.HasRemainingPlacementAlternative, TopologyPortfolioTriggered=Context.TopologyDemand.RequiresJointPortfolio, AttemptedCandidateCount=len(Context.AttemptedCandidateIds))
        if Context.DenseBoundaryLeaseRouting:
            Context.PlannedRoutingSlots = max(1, Context.RemainingRetainedCandidates) if Context.ActiveRelocatedPortfolioCandidate else 1
        Context.CandidateRoutingSeconds = Context.RemainingRuntimeSeconds if Context.SinglePackedComponent else Context.RemainingRuntimeSeconds * TopologyPortfolioRoutingFraction(HasRemainingPlacementAlternative=Context.HasRemainingPlacementAlternative, AttemptedCandidateCount=len(Context.AttemptedCandidateIds), AuthoritativeMandatoryAccessConflictObserved=Context.TerminalConstraintEpochAuthoritativeAccessConflictObserved) if Context.ActiveRelocatedPortfolioCandidate and Context.TerminalConstraintEpochPortfolioIdentityFingerprint and (Context.CandidateRecord.JointPortfolioIdentityFingerprint == Context.TerminalConstraintEpochPortfolioIdentityFingerprint) and Context.TerminalConstraintEpochAuthoritativeAccessConflictObserved else Context.RemainingRuntimeSeconds / Context.PlannedRoutingSlots if Context.ActiveRelocatedPortfolioCandidate or not Context.CandidateJointDiagnostics or (not Context.TopologyDemand.RequiresJointPortfolio) else Context.RemainingRuntimeSeconds * TopologyPortfolioRoutingFraction(HasRemainingPlacementAlternative=Context.HasRemainingPlacementAlternative, AttemptedCandidateCount=len(Context.AttemptedCandidateIds))
        Context.AdaptiveAttemptRuntimeSeconds = min(Context.Policy.AdaptiveRouting.MaximumRuntimeSeconds, max(0.001, min(Context.CandidateRoutingSeconds, Context.JointPortfolioSliceSeconds) if Context.CandidateJointDiagnostics and Context.JointPortfolioSliceSeconds is not None and (not Context.DenseBoundaryLeaseRouting) else DenseRetainedLeaseProofSliceSeconds(RemainingSeconds=Context.RemainingRuntimeSeconds, RemainingRetainedCandidates=max(1, Context.RemainingRetainedCandidates), PrioritizeHigherOrderCutProof=Context.CandidateHasHigherOrderCutConstraints and int(Context.CandidateJointIndex or 0) == 0) if Context.DenseBoundaryLeaseRouting and Context.ActiveRelocatedPortfolioCandidate else Context.RemainingRuntimeSeconds / Context.PlannedRoutingSlots))
        if Context.CandidateRouteDiagnostics.get('__PlacementRecipe__', {}).get('IsPostPinBankRepairEpoch', False) and Context.CandidateRouteDiagnostics.get('__PlacementRecipe__', {}).get('EnableInternalPinBankGeometryRepair', False):
            Context.AdaptiveAttemptRuntimeSeconds = min(Context.Policy.AdaptiveRouting.MaximumRuntimeSeconds, max(0.001, Context.RemainingRuntimeSeconds - 2.0))
        if Context.ReserveClusterPinBankRepairSeconds and Context.RemainingRuntimeSeconds > 6.0:
            Context.AdaptiveAttemptRuntimeSeconds = min(Context.AdaptiveAttemptRuntimeSeconds, Context.RemainingRuntimeSeconds - 5.0)
        if Context.IsBoundedDenseLeaseControlRetry:
            Context.AdaptiveAttemptRuntimeSeconds = min(Context.AdaptiveAttemptRuntimeSeconds, 1.5)
        elif ShouldBoundClusterPinBankRepairProbe(Context.HasClusterPinBankRepair, Context.IsTransactionalEndpointRepair):
            Context.AdaptiveAttemptRuntimeSeconds = min(Context.AdaptiveAttemptRuntimeSeconds, 5.0)
        if Context.ExactClusterInterfaceSolveEnabled:
            Context.AdaptiveAttemptRuntimeSeconds = min(Context.Policy.AdaptiveRouting.MaximumRuntimeSeconds, max(0.001, Context.RemainingRuntimeSeconds - 2.0))
        if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            print(f'[debug] authoritative: trying placement candidate id={Context.CandidateRecord.CandidateId} claims={len(Context.CandidatePlacement.Placed.LocalRouteClaims or ())} packed={bool(Context.CandidatePlacement.PackedClusters)} spacing={Context.RoutingSpacing}', flush=True)
            print(f'[debug] authoritative: policy budgets overall={Context.Policy.RuntimeBudgetSeconds:.3f}s adaptive_max={Context.AdaptiveAttemptRuntimeSeconds:.3f}s has_alternative={Context.HasRemainingPlacementAlternative}', flush=True)
        Context.AttemptPolicy = replace(Context.Policy, RuntimeBudgetSeconds=Context.AdaptiveAttemptRuntimeSeconds, AdaptiveRouting=replace(Context.Policy.AdaptiveRouting, MaximumRuntimeSeconds=Context.AdaptiveAttemptRuntimeSeconds))
        if Context.CandidateRecord.RoutingEnvelope is not None and len(Context.CandidateRecord.Placement.Clusters) == 1:
            Context.AttemptPolicy = BuildFrozenEnvelopeRoutingPolicy(Context.AttemptPolicy, Context.CandidateRecord.RoutingEnvelope)
        Context.AttemptStarted = Context.Services.monotonic()
        Context.AdaptiveAttemptExpiresAt = min(Context.Deadline.ExpiresAt, Context.AttemptStarted + Context.AdaptiveAttemptRuntimeSeconds)
        Context.AttemptDeadline = RoutingDeadline(StartedAt=Context.Deadline.StartedAt, ExpiresAt=Context.AdaptiveAttemptExpiresAt)
        if Context.CandidateJointDiagnostics:
            Context.JointPlacementStateEvents.append({'CandidateIndex': Context.CandidateJointIndex, 'Status': 'routing', 'CandidateId': Context.CandidateRecord.CandidateId, 'AllocatedRoutingSeconds': round(Context.AdaptiveAttemptRuntimeSeconds, 6), 'Transforms': Context.CandidateJointDiagnostics.get('SelectedTransforms', {})})
        try:
            Context.CandidatePlacement = MaterializeSelectedJointPlacementLocalRouting(Context, Context.CandidateRecord, partial(CheckCandidateValidation, Context))
            if Context.CandidatePlacement is not Context.CandidateRecord.Placement:
                Context.CandidateRecord = replace(Context.CandidateRecord, Placement=Context.CandidatePlacement)
                Context.Placement = Context.CandidatePlacement
            if ApplyRemainingExactLegalJointStateCount(Context.CandidatePlacement, max(1, Context.RemainingRetainedCandidates)):
                Context.JointPlacementStateEvents.append({'Status': 'materialized-remaining-joint-state-count-rebound', 'CandidateId': Context.CandidateRecord.CandidateId, 'RemainingExactLegalRetainedStateCount': max(1, Context.RemainingRetainedCandidates)})
            Context.Services.ValidatePlacedCellElectricalIsolation(Context.CandidatePlacement.Placed, WorkCheck=partial(CheckCandidateValidation, Context))
            if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                print(f'[debug] authoritative: remaining_runtime_for_attempt={Context.AttemptPolicy.RuntimeBudgetSeconds:.3f}s elapsed_from_start={Context.Services.monotonic() - Context.Started:.3f}s', flush=True)
            Context.Routed = Context.PreRoutedClusterInterfaceDesignsByPlacementFingerprint.get(Context.CandidateRecord.PlacementFingerprint)
            if Context.Routed is None:
                Context.FrozenTrackAssignmentPreparation = Context.PrePlacementTrackPreparationByCandidateId.get(Context.CandidateRecord.CandidateId)
                Context.Routed = Context.Services.RoutePcbDesign(Context.CandidatePlacement, ProgressCallback=partial(ReportRoutingProgress, Context), Policy=Context.AttemptPolicy, Deadline=Context.AttemptDeadline, Resources=Context.CandidateResources, FrozenTrackAssignmentPreparation=Context.FrozenTrackAssignmentPreparation)
            CapturePortableRawPortalGeometryCaches(Context, Context.CandidateResources)
            Context.Deadline.RaiseIfExpired('Routing', {'PlacementCandidate': Context.CandidateRecord.CandidateId})
            Context.Deadline.RaiseIfExpired('RoutedValidation', {'Phase': 'before', 'PlacementCandidate': Context.CandidateRecord.CandidateId})
            if Context.RoutedValidationCallback is not None:
                Context.RoutedValidationCallback(Context.Routed)
            Context.Deadline.RaiseIfExpired('RoutedValidation', {'Phase': 'after', 'PlacementCandidate': Context.CandidateRecord.CandidateId})
            if Context.RoutingPercentageSelectionEnabled:
                RecordRoutedCandidate(Context, Context.CandidateRecord, Context.Placement, Context.Routed)
            Context.PlacementAttemptFailures.append({**Context.CandidateRecord.ToDictionary(), 'Result': 'routed', 'AdaptiveRuntimeBudgetSeconds': round(Context.AdaptiveAttemptRuntimeSeconds, 6), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.AttemptStarted, 6)})
            if Context.CandidateJointDiagnostics:
                Context.JointPlacementStateEvents.append({'CandidateIndex': Context.CandidateJointIndex, 'Status': 'routed', 'CandidateId': Context.CandidateRecord.CandidateId, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.AttemptStarted, 6)})
            if not Context.RoutingPercentageSelectionEnabled:
                Context.SelectedCandidate = Context.CandidateRecord
                break
            if Context.Deadline.RemainingSeconds() < Context.Policy.MaterialObjective.MinimumRemainingRoutingPercentageSearchSeconds:
                break
            continue
        except (RoutingStageError, ValueError) as Error:
            CapturePortableRawPortalGeometryCaches(Context, Context.CandidateResources)
            Context.LastRoutingError = Error
            if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                print(f'[debug] authoritative: placement route rejected candidate={Context.CandidateRecord.CandidateId} error_type={type(Error).__name__} error={Error}', flush=True)
            Context.Routed = None
            if isinstance(Error, RoutingStageError):
                Context.LastStructuredRoutingError = Error
            if (
                Context.ExactClusterInterfaceSolveEnabled
                and isinstance(Error, RoutingStageError)
            ):
                Context.DenseAssignmentCut = _RecordAssignmentCut(
                    Context,
                    Error,
                    Context.CandidateRecord,
                )
                if (
                    Context.PendingSamePlacementRoutingControlRetry is not None
                    and Context.PendingSamePlacementRoutingControlRetry
                    .Evidence.ExhaustedRepeaterAccessCut
                ):
                    Context.PlacementAttemptFailures.append({
                        'CandidateId': Context.CandidateRecord.CandidateId,
                        'PlacementFingerprint': (
                            Context.CandidateRecord.PlacementFingerprint
                        ),
                        'Result': 'dense-route-only-retry',
                        'Failure': str(Error),
                        'Diagnostics': Error.Failure.ToDictionary(),
                        'ElapsedSeconds': round(
                            Context.Services.monotonic()
                            - Context.AttemptStarted,
                            6,
                        ),
                    })
                    Context.PlacementGenerationDecisions.append({
                        'Result': 'dense-repeater-ready-route-only-retry',
                        'CandidateId': Context.CandidateRecord.CandidateId,
                        'AssignmentCutFingerprint': (
                            Context.DenseAssignmentCut.ConflictFingerprint
                            if Context.DenseAssignmentCut is not None
                            else ''
                        ),
                        'ReusedPlacedGeometry': True,
                        'ExecutableLegacyRepairCascade': False,
                        'BroadFallbackAllowed': False,
                    })
                    continue
            Context.PlacementAttemptFailures.append({'CandidateId': Context.CandidateRecord.CandidateId, 'PlacementFingerprint': Context.CandidateRecord.PlacementFingerprint, 'Result': 'terminal-fixed-route-failure', 'Failure': str(Error), 'Diagnostics': Error.Failure.ToDictionary() if isinstance(Error, RoutingStageError) else {}, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.AttemptStarted, 6)})
            break
            if Context.ExactClusterInterfaceSolveEnabled:
                if isinstance(Error, RoutingStageError):
                    Context.LastStructuredRoutingError = Error
                Context.PlacementAttemptFailures.append({**Context.CandidateRecord.ToDictionary(), 'RoutingSpacing': Context.RoutingSpacing, 'PackedNandPlacement': bool(Context.CandidatePlacement.PackedClusters), 'Failure': str(Error), 'AdaptiveRuntimeBudgetSeconds': round(Context.AdaptiveAttemptRuntimeSeconds, 6), 'Diagnostics': Error.Failure.ToDictionary() if isinstance(Error, RoutingStageError) else {}, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.AttemptStarted, 6), 'DenseControlPath': 'frozen-cluster-interface-assignment', 'ExecutableLegacyRepairCascade': False})
                Context.PlacementGenerationDecisions.append({'Result': 'dense-routing-failure-terminal', 'CandidateId': Context.CandidateRecord.CandidateId, 'ExecutableLegacyRepairCascade': False, 'BroadFallbackAllowed': False, 'Reason': Error.Failure.Reason.value if isinstance(Error, RoutingStageError) else type(Error).__name__})
                break
            if isinstance(Error, RoutingStageError) and Error.Failure.Reason == RoutingFailureReason.RuntimeBudgetExceeded:
                Context.LastStructuredRoutingError = Error
                Context.TimedOutConflictSignals = ExtractCompletedEscalationRelocationSignals(Error.Failure)
                if Context.TimedOutConflictSignals and Context.TimedOutConflictSignals != Context.PlacementRelocationPrioritySignals:
                    Context.PlacementRelocationPrioritySignals = Context.TimedOutConflictSignals
                    Context.PlacementRelocationSignals = frozenset((*Context.PlacementRelocationSignals, *Context.TimedOutConflictSignals))
                    Context.NeedsFeedbackPlacementGeneration = True
                    Context.PlacementGenerationDecisions.append({'Result': 'routing-timeout-conflict-feedback', 'CandidateId': Context.CandidateRecord.CandidateId, 'PriorityRelocationSignals': sorted(Context.TimedOutConflictSignals), 'RelocationSignals': sorted(Context.PlacementRelocationSignals)})
                Context.PlacementAttemptFailures.append({**Context.CandidateRecord.ToDictionary(), 'RoutingSpacing': Context.RoutingSpacing, 'PackedNandPlacement': bool(Context.CandidatePlacement.PackedClusters), 'Failure': str(Error), 'AdaptiveRuntimeBudgetSeconds': round(Context.AdaptiveAttemptRuntimeSeconds, 6), 'Diagnostics': Error.Failure.ToDictionary(), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.AttemptStarted, 6)})
                if Context.CandidateJointDiagnostics:
                    Context.JointPlacementStateEvents.append({'CandidateIndex': Context.CandidateJointIndex, 'Status': 'slice-expired', 'CandidateId': Context.CandidateRecord.CandidateId, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.AttemptStarted, 6), 'AllocatedRoutingSeconds': round(Context.AdaptiveAttemptRuntimeSeconds, 6), 'Failure': Error.Failure.ToDictionary()})
                if not Context.Deadline.IsExpired():
                    continue
                break
            if isinstance(Error, RoutingStageError):
                Context.ReportedAssignmentCut = RoutingAssignmentCut.FromFailure(Error.Failure, SourceCandidateId=Context.CandidateRecord.CandidateId, MandatoryAccessOwnershipFingerprint=Context.CandidateRecord.TopologyDemand.MandatoryAccessOwnershipFingerprint if Context.CandidateRecord.TopologyDemand is not None else '')
                Context.CandidateIsTransactionalEndpointRepair = Context.CandidateRecord.SourceGenerator == 'transactional-cluster-endpoint-repair'
                Context.ParentTransactionalRepairSignals = frozenset(map(str, dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {}).get('InternalPinBankGeometryRepairSignals', ())))
                Context.CandidateTransactionalRepairSignalHistory = tuple((frozenset(map(str, Signals)) for Signals in dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {}).get('TransactionalRepairSignalHistory', ()) if isinstance(Signals, tuple | list | set | frozenset)))
                Context.ParentTransactionalRepairClusterCount = int(dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {}).get('TransactionalRepairClusterCount', 1) or 1)
                Context.ReportedTransactionalCutSignals = TransactionalCutRepairSignals(Context.ReportedAssignmentCut)
                Context.SameInterfaceClusterEscalation = Context.CandidateIsTransactionalEndpointRepair and TransactionalCutMayEscalateRepairClusterCount(Context.ParentTransactionalRepairSignals, Context.ReportedTransactionalCutSignals, Context.ParentTransactionalRepairClusterCount)
                Context.ReportedTransactionalPriorCuts = (*(() if Context.CandidateIsTransactionalEndpointRepair else tuple(Context.PlacementAssignmentCutHistory)), *tuple((Evidence.AssignmentCut for Evidence in Context.DeferredActivePortfolioAssignmentCuts)))
                Context.RepeatedReportedTransactionalCut = bool(Context.CandidateIsTransactionalEndpointRepair and (Context.SameInterfaceClusterEscalation or ShouldDiversifyRepeatedAssignmentCut(Context.ReportedTransactionalPriorCuts, Context.ReportedAssignmentCut, Context.SignalTopologyFingerprints) or ShouldDiversifyRepeatedAssignmentCut(Context.ReportedTransactionalPriorCuts, Context.ReportedAssignmentCut) or BoundedAssignmentCutRepeatsAcrossDistinctOwnership(Context.ReportedTransactionalPriorCuts, Context.ReportedAssignmentCut) or AssignmentCutRepeatsAcrossDistinctPlacementOwnership(Context.ReportedTransactionalPriorCuts, Context.ReportedAssignmentCut) or BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership(Context.ReportedTransactionalPriorCuts, Context.ReportedAssignmentCut)))
                Context.AssignmentCut = _RecordAssignmentCut(Context, Error, Context.CandidateRecord, DeferTopologyEpochForMaterializedSibling=ShouldDeferTopologyCutForRetainedPortfolioSibling(TopologyRequiresJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio, ActiveRelocatedPortfolioCandidate=Context.ActiveRelocatedPortfolioCandidate, RemainingRetainedActiveCandidates=Context.RemainingRetainedCandidates, Failure=Error.Failure, ActiveTransactionalEndpointPortfolioCandidate=Context.CandidateIsTransactionalEndpointRepair, TransactionalCutStrictlyNarrowsParentInterface=TransactionalCutStrictlyNarrowsParentInterface(Context.ParentTransactionalRepairSignals, Context.ReportedTransactionalCutSignals), TransactionalCutRepeatedAcrossAccessDistinctPlacements=Context.RepeatedReportedTransactionalCut, TransactionalCutRevisitsAncestorInterface=TransactionalCutRevisitsAncestorInterface(Context.CandidateTransactionalRepairSignalHistory, Context.ReportedTransactionalCutSignals) and (not TransactionalCutMayEscalateRepairClusterCount(Context.ParentTransactionalRepairSignals, Context.ReportedTransactionalCutSignals, Context.ParentTransactionalRepairClusterCount)), TransactionalExactPairAfterCoordinatedRepair=Context.ParentTransactionalRepairClusterCount >= 2 and Context.ParentTransactionalRepairSignals == Context.ReportedTransactionalCutSignals and TransactionalCutRequiresCoordinatedClusterRepair(Context.ReportedAssignmentCut)))
                if IsAuthoritativeMandatoryAccessConflict(Error.Failure):
                    Context.PromotedTopologyDemand = PromoteAuthoritativeMandatoryAccessConflict(Context.CandidateRecord.TopologyDemand or Context.TopologyDemand, Error.Failure)
                    Context.TopologyDemandByFingerprint[Context.CandidateRecord.PlacementFingerprint] = Context.PromotedTopologyDemand
                    Context.CandidateRecord = replace(Context.CandidateRecord, TopologyDemand=Context.PromotedTopologyDemand)
                    if Context.ActiveRelocatedPortfolioCandidate and Context.TerminalConstraintEpochPortfolioIdentityFingerprint and (Context.CandidateRecord.JointPortfolioIdentityFingerprint == Context.TerminalConstraintEpochPortfolioIdentityFingerprint):
                        Context.TerminalConstraintEpochAuthoritativeAccessConflictObserved = True
                        Context.JointPlacementStateEvents.append({'Status': 'terminal-authoritative-access-conflict-promoted', 'CandidateId': Context.CandidateRecord.CandidateId, 'MandatoryAccessConflictResources': Context.PromotedTopologyDemand.MandatoryAccessConflictResources, 'MandatoryAccessConflictSignals': list(Context.PromotedTopologyDemand.MandatoryAccessConflictSignals), 'MandatoryAccessConflictFingerprint': Context.PromotedTopologyDemand.MandatoryAccessConflictFingerprint, 'NextAction': 'promote-next-placement-screen-clear-untried-candidate'})
                Context.ReportedAssignmentCutIsActive = Context.AssignmentCut is None or Context.CurrentPlacementAssignmentCut == Context.AssignmentCut
                Context.ConflictSignals = ExtractPlacementRelocationSignals(Error.Failure)
                if IsHigherOrderAssignmentCut(Context.AssignmentCut) and Context.AssignmentCut is not None and Context.AssignmentCut.PriorityRelocationSignals:
                    Context.ConflictSignals = frozenset(Context.AssignmentCut.PriorityRelocationSignals)
                Context.EscalationConflictSignals = ExtractCompletedEscalationRelocationSignals(Error.Failure)
                if not Context.ReportedAssignmentCutIsActive:
                    Context.ConflictSignals = frozenset()
                    Context.EscalationConflictSignals = frozenset()
                if not Context.ConflictSignals:
                    Context.ConflictSignals = Context.EscalationConflictSignals
                Context.ConflictGraph = (Error.Failure.Diagnostics or {}).get('ConflictGraph', {})
                Context.PriorityRelocationSignals = frozenset((str(Signal) for Signal in (Context.ConflictGraph.get('PriorityRelocationSignals', ()) if isinstance(Context.ConflictGraph, dict) else ())))
                if not Context.ReportedAssignmentCutIsActive:
                    Context.PriorityRelocationSignals = frozenset()
                if not Context.PriorityRelocationSignals and Context.EscalationConflictSignals:
                    Context.PriorityRelocationSignals = Context.EscalationConflictSignals
                    Context.NeedsFeedbackPlacementGeneration = True
                if FailureRequestsPlacementAdvance(Error.Failure):
                    Context.NeedsFeedbackPlacementGeneration = True
                if Context.ConflictSignals:
                    Context.PlacementRelocationPrioritySignals = Context.PriorityRelocationSignals if Context.PriorityRelocationSignals else Context.ConflictSignals
                    if FailureRequiresPackedAccessRepair(Error.Failure):
                        Context.RequiredRepairSignals = Context.PriorityRelocationSignals if Context.PriorityRelocationSignals else Context.ConflictSignals
                        Context.RequiredRepairSignals = ExpandAnalogousMandatoryRepairSignals(Context.Netlist.Modules[Context.Netlist.Top], Context.RequiredRepairSignals)
                        Context.PlacementRequiredRelocationSignals = Context.RequiredRepairSignals
                    Context.PlacementRelocationSignals = frozenset((*Context.PlacementRelocationSignals, *Context.ConflictSignals))
                    Context.PlacementGenerationDecisions.append({'Result': 'routing-conflict-feedback', 'CandidateId': Context.CandidateRecord.CandidateId, 'RelocationSignals': sorted(Context.PlacementRelocationSignals), 'AssignmentCut': Context.AssignmentCut.ToDictionary() if Context.AssignmentCut is not None else None})
                if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                    print(f'[debug] authoritative: routing failure reason={Error.Failure.Reason} stage={Error.Failure.Stage} affected={tuple(Error.Failure.AffectedNets)}', flush=True)
                Context.LastStructuredRoutingError = Error
                if not Context.SinglePackedComponent and (not FailureRequestsPlacementAdvance(Error.Failure)):
                    try:
                        Context.Released = _RouteWithFailedLocalClaimsReleased(Context, Context.CandidatePlacement, Context.AttemptPolicy, Context.AttemptDeadline, Error.Failure, AdaptiveStartedAt=Context.AttemptStarted, AdaptiveExpiresAt=Context.AdaptiveAttemptExpiresAt)
                    except (RoutingStageError, ValueError) as ReleaseError:
                        Context.LastRoutingError = ReleaseError
                        if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                            print(f'[debug] authoritative: local-claim recovery rejected signals={list(Error.Failure.AffectedNets)} error_type={type(ReleaseError).__name__} error={ReleaseError}', flush=True)
                        if isinstance(ReleaseError, RoutingStageError):
                            Context.ReleaseAssignmentCut = _RecordAssignmentCut(Context, ReleaseError, Context.CandidateRecord)
                            Context.LastStructuredRoutingError = ReleaseError
                            Context.ReleaseConflictSignals = ExtractPlacementRelocationSignals(ReleaseError.Failure)
                            if Context.ReleaseConflictSignals:
                                Context.PlacementRelocationPrioritySignals = Context.ReleaseConflictSignals if Context.PlacementRelocationSignals else frozenset(ReleaseError.Failure.AffectedNets)
                                Context.PlacementRelocationSignals = frozenset((*Context.PlacementRelocationSignals, *Context.ReleaseConflictSignals))
                                Context.PlacementGenerationDecisions.append({'Result': 'local-claim-recovery-conflict-feedback', 'CandidateId': Context.CandidateRecord.CandidateId, 'RelocationSignals': sorted(Context.PlacementRelocationSignals), 'AssignmentCut': Context.ReleaseAssignmentCut.ToDictionary() if Context.ReleaseAssignmentCut is not None else None})
                        if isinstance(ReleaseError, RoutingStageError) and ReleaseError.Failure.Reason == RoutingFailureReason.RuntimeBudgetExceeded:
                            Context.PlacementAttemptFailures.append({**Context.CandidateRecord.ToDictionary(), 'RoutingSpacing': Context.RoutingSpacing, 'PackedNandPlacement': bool(Context.CandidatePlacement.PackedClusters), 'Failure': str(ReleaseError), 'AdaptiveRuntimeBudgetSeconds': round(Context.AdaptiveAttemptRuntimeSeconds, 6), 'Diagnostics': ReleaseError.Failure.ToDictionary(), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.AttemptStarted, 6)})
                            break
                    else:
                        if Context.Released is not None:
                            Context.Placement, Context.Routed = Context.Released
                            if Context.RoutingPercentageSelectionEnabled:
                                RecordRoutedCandidate(Context, Context.CandidateRecord, Context.Placement, Context.Routed)
                            Context.PlacementAttemptFailures.append({**Context.CandidateRecord.ToDictionary(), 'RoutingSpacing': Context.RoutingSpacing, 'PackedNandPlacement': bool(Context.CandidatePlacement.PackedClusters), 'Failure': str(Error), 'AdaptiveRuntimeBudgetSeconds': round(Context.AdaptiveAttemptRuntimeSeconds, 6), 'AdaptiveAttemptStartedAt': Context.AttemptStarted, 'AdaptiveAttemptExpiresAt': Context.AdaptiveAttemptExpiresAt, 'Recovery': 'released-affected-local-claims', 'ReleasedSignals': list(Error.Failure.AffectedNets)})
                            if not Context.RoutingPercentageSelectionEnabled:
                                Context.SelectedCandidate = Context.CandidateRecord
                                break
                            if Context.Deadline.RemainingSeconds() < Context.Policy.MaterialObjective.MinimumRemainingRoutingPercentageSearchSeconds:
                                break
                            continue
                Context.RepeatedSiblingStarvationCut = bool(TransactionalCutRepairSignals(Context.AssignmentCut).intersection(Context.PlacementRepeatedCandidateStarvationSignals))
                Context.RepeatedAccessDistinctTransactionalCut = bool(Context.RepeatedReportedTransactionalCut or (not Context.CandidateIsTransactionalEndpointRepair and (ShouldDiversifyRepeatedAssignmentCut(tuple(Context.PlacementAssignmentCutHistory[:-1]), Context.AssignmentCut, Context.SignalTopologyFingerprints) or ShouldDiversifyRepeatedAssignmentCut(tuple(Context.PlacementAssignmentCutHistory[:-1]), Context.AssignmentCut) or BoundedAssignmentCutRepeatsAcrossDistinctOwnership(tuple(Context.PlacementAssignmentCutHistory[:-1]), Context.AssignmentCut) or AssignmentCutRepeatsAcrossDistinctPlacementOwnership(tuple(Context.PlacementAssignmentCutHistory[:-1]), Context.AssignmentCut) or BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership(tuple(Context.PlacementAssignmentCutHistory[:-1]), Context.AssignmentCut))))
                Context.CandidateDiversificationFixedLevel = int(dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRelocation__', {}).get('CoordinatedCandidateDiversificationFixedLevel', 0) or 0)
                Context.PostCoordinatedStarvationSignals = frozenset(map(str, Error.Failure.AffectedNets))
                if Context.TopologyDemand.RequiresJointPortfolio and Context.CandidateIsTransactionalEndpointRepair and (Context.ParentTransactionalRepairClusterCount >= 2) and (Context.CandidateDiversificationFixedLevel == 0) and (Error.Failure.Reason == RoutingFailureReason.TrackAssignmentConflict) and (Error.Failure.Stage == 'Candidate') and Context.PostCoordinatedStarvationSignals:
                    Context.PlacementCoordinatedCandidateDiversificationSignals = Context.PostCoordinatedStarvationSignals
                    Context.PlacementGenerationDecisions.append({'Result': 'post-coordinated-cut-candidate-diversification', 'CandidateId': Context.CandidateRecord.CandidateId, 'Signals': sorted(Context.PostCoordinatedStarvationSignals), 'Level': 1})
                Context.CandidateRecipeDiagnostics = dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {})
                Context.CandidateRepairDiagnostics = dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__TransactionalClusterEndpointRepair__', {})
                Context.CandidateHasWitnessedMacroRotation = any((bool(ClusterDiagnostics.get('PriorityEndpointRotationDelta')) for ClusterDiagnostics in dict(Context.CandidateRepairDiagnostics.get('Clusters', {})).values() if isinstance(ClusterDiagnostics, dict)))
                Context.CandidateRepairSignalHistory = tuple((frozenset(map(str, SignalSet)) for SignalSet in Context.CandidateRecipeDiagnostics.get('TransactionalRepairSignalHistory', ())))
                Context.CurrentTransactionalCutSignals = TransactionalCutRepairSignals(Context.AssignmentCut)
                Context.PostDiversificationOwnershipRepair = ShouldAdmitPostDiversificationOwnershipRepair(Context.AssignmentCut, TopologyRequiresJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio, CandidateIsTransactionalEndpointRepair=Context.CandidateIsTransactionalEndpointRepair, ParentTransactionalRepairClusterCount=Context.ParentTransactionalRepairClusterCount, CandidateDiversificationFixedLevel=Context.CandidateDiversificationFixedLevel, ParentTransactionalRepairSignals=Context.ParentTransactionalRepairSignals, TransactionalRepairSignalHistory=Context.CandidateRepairSignalHistory)
                Context.AncestorCutLocalRepair = bool(Context.CandidateRecord.SourceGenerator == 'transactional-cluster-endpoint-repair' and Context.CandidateHasWitnessedMacroRotation and Context.CurrentTransactionalCutSignals and TransactionalCutRevisitsAncestorInterface(tuple((TransactionalCutRepairSignals(PriorCut) for PriorCut in Context.PlacementAssignmentCutHistory[:-1])), Context.CurrentTransactionalCutSignals) and (not TransactionalCutRevisitsAncestorInterface(Context.CandidateRepairSignalHistory, Context.CurrentTransactionalCutSignals)))
                Context.TransactionalEndpointRepairSignals = SelectTransactionalEndpointRepairSignals(Context.AssignmentCut, InternalPinBankGeometryRepairActive=Context.InternalPinBankGeometryRepairActive, PinBankRepairSignals=Context.PlacementClusterPinBankRepairSignals, CandidateIsTransactionalEndpointRepair=Context.CandidateRecord.SourceGenerator == 'transactional-cluster-endpoint-repair', ParentTransactionalRepairSignals=frozenset(map(str, dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {}).get('InternalPinBankGeometryRepairSignals', ()))), RepeatedAccessDistinctTransactionalCut=Context.RepeatedAccessDistinctTransactionalCut, ProvenSiblingStarvationSignals=Context.PlacementRepeatedCandidateStarvationSignals, AncestorTransactionalRepairSignalSets=Context.CandidateTransactionalRepairSignalHistory, ParentTransactionalRepairClusterCount=Context.ParentTransactionalRepairClusterCount, AllowAncestorCutLocalRepair=Context.AncestorCutLocalRepair, AllowPostDiversificationOwnershipRepair=Context.PostDiversificationOwnershipRepair)
                if AssignmentCutHasBoundedExactCore(Context.AssignmentCut) and Context.CandidateIsTransactionalEndpointRepair and (Context.ParentTransactionalRepairClusterCount >= 2):
                    Context.PlacementGenerationDecisions.append({'Result': 'ownership-repair-admission', 'Outcome': 'admitted' if Context.PostDiversificationOwnershipRepair else 'not-required-or-rejected', 'CandidateId': Context.CandidateRecord.CandidateId, 'Signals': sorted(Context.CurrentTransactionalCutSignals), 'ParentSignals': sorted(Context.ParentTransactionalRepairSignals), 'ParentRepairClusterCount': Context.ParentTransactionalRepairClusterCount, 'CandidateDiversificationFixedLevel': Context.CandidateDiversificationFixedLevel, 'AssignmentCutFingerprint': Context.AssignmentCut.ConflictFingerprint if Context.AssignmentCut is not None else '', 'MandatoryAccessOwnershipFingerprint': Context.AssignmentCut.MandatoryAccessOwnershipFingerprint if Context.AssignmentCut is not None else ''})
                if Context.AncestorCutLocalRepair:
                    Context.PlacementGenerationDecisions.append({'Result': 'rotated-macro-ancestor-cut-local-eco', 'CandidateId': Context.CandidateRecord.CandidateId, 'Signals': sorted(Context.TransactionalEndpointRepairSignals), 'AssignmentCutFingerprint': Context.AssignmentCut.ConflictFingerprint if Context.AssignmentCut is not None else ''})
                if Context.CandidateRecord.SourceGenerator == 'transactional-cluster-endpoint-repair' and AssignmentCutHasBoundedExactCore(Context.AssignmentCut) and (not Context.TransactionalEndpointRepairSignals):
                    Context.ParentTransactionalSignals = frozenset(map(str, dict(Context.CandidatePlacement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {}).get('InternalPinBankGeometryRepairSignals', ())))
                    Context.PlacementGenerationDecisions.append({'Result': 'transactional-cut-frontier-not-narrower', 'CandidateId': Context.CandidateRecord.CandidateId, 'ParentSignals': sorted(Context.ParentTransactionalSignals), 'ChildSignals': sorted(TransactionalCutRepairSignals(Context.AssignmentCut)), 'AssignmentCutFingerprint': Context.AssignmentCut.ConflictFingerprint if Context.AssignmentCut is not None else '', 'NextAction': 'route-retained-parent-portfolio-sibling'})
                if Context.TransactionalEndpointRepairSignals:
                    Context.PublishedTransactionalRepair = False
                    Context.TransactionalRepairClusterCount = SelectTransactionalRepairClusterCount(CandidateIsTransactionalEndpointRepair=Context.CandidateRecord.SourceGenerator == 'transactional-cluster-endpoint-repair', RepeatedAccessDistinctTransactionalCut=Context.RepeatedAccessDistinctTransactionalCut or Context.RepeatedSiblingStarvationCut, CutStrictlyNarrowsParentInterface=TransactionalCutStrictlyNarrowsParentInterface(Context.ParentTransactionalRepairSignals, Context.TransactionalEndpointRepairSignals), ExactBoundaryPairCut=TransactionalCutRequiresCoordinatedClusterRepair(Context.AssignmentCut), AllowInitialExactBoundaryCutRepair=Context.TopologyDemand.RequiresJointPortfolio and (not Context.CandidateIsTransactionalEndpointRepair) and (Context.AssignmentCut is not None) and TransactionalCutRequiresCoordinatedClusterRepair(Context.AssignmentCut) and (not any((PriorCut.ConflictFingerprint == Context.AssignmentCut.ConflictFingerprint for PriorCut in Context.PlacementAssignmentCutHistory[:-1]))))
                    Context.TransactionalRepairVariantCount = 3 if Context.CandidateRecord.SourceGenerator == 'transactional-cluster-endpoint-repair' else 6
                    for Context.RepairVariant in range(Context.TransactionalRepairVariantCount):
                        Context.VariantPublished = _PublishTransactionalClusterEndpointRepair(Context, Context.CandidateRecord, Context.TransactionalEndpointRepairSignals, RepairVariant=Context.RepairVariant, RepairClusterCount=Context.TransactionalRepairClusterCount, RepairTerminalPositions=frozenset(Context.AssignmentCut.PriorityRelocationTerminals) if Context.AssignmentCut is not None else frozenset())
                        Context.PublishedTransactionalRepair = Context.VariantPublished or Context.PublishedTransactionalRepair
                        if ShouldStopTransactionalRepairVariantGeneration(CandidateIsTransactionalEndpointRepair=Context.CandidateRecord.SourceGenerator == 'transactional-cluster-endpoint-repair', RepairClusterCount=Context.TransactionalRepairClusterCount, VariantPublished=Context.VariantPublished):
                            break
                    if Context.PublishedTransactionalRepair:
                        Context.ActiveJointPortfolioIdentityFingerprint = _TransactionalEndpointRepairPortfolioFingerprint(Context, Context.CandidateRecord, Context.TransactionalEndpointRepairSignals, Context.TransactionalRepairClusterCount)
                        Context.PlacementGenerationDecisions.append({'Result': 'transactional-cluster-endpoint-portfolio-complete', 'SourceCandidateId': Context.CandidateRecord.CandidateId, 'RequestedVariantCount': Context.TransactionalRepairVariantCount, 'RepairClusterCount': Context.TransactionalRepairClusterCount, 'JointPortfolioIdentityFingerprint': Context.ActiveJointPortfolioIdentityFingerprint, 'RemainingRoutingSeconds': round(max(0.0, Context.Deadline.RemainingSeconds()), 6)})
                    if Context.PostDiversificationOwnershipRepair:
                        Context.PlacementGenerationDecisions.append({'Result': 'ownership-repair-publication', 'Outcome': 'published' if Context.PublishedTransactionalRepair else 'deadline-skipped' if Context.Deadline.IsExpired() else 'rejected-or-deduplicated', 'SourceCandidateId': Context.CandidateRecord.CandidateId, 'Signals': sorted(Context.TransactionalEndpointRepairSignals), 'RequestedRepairClusterCount': Context.TransactionalRepairClusterCount, 'RemainingRoutingSeconds': round(max(0.0, Context.Deadline.RemainingSeconds()), 6)})
            Context.PlacementAttemptFailures.append({**Context.CandidateRecord.ToDictionary(), 'RoutingSpacing': Context.RoutingSpacing, 'PackedNandPlacement': bool(Context.CandidatePlacement.PackedClusters), 'Failure': str(Error), 'AdaptiveRuntimeBudgetSeconds': round(Context.AdaptiveAttemptRuntimeSeconds, 6), 'Diagnostics': Error.Failure.ToDictionary() if isinstance(Error, RoutingStageError) else {}, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.AttemptStarted, 6)})

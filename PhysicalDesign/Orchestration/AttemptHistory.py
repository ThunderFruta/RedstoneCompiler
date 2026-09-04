"""Importable placement-flow helpers with explicit run state."""

from __future__ import annotations

from typing import Any
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingFailure, RoutingStageError
from PhysicalDesign.Geometry.Rotation import RotatedCellSize
from PhysicalDesign.Placement.Engine.Clusters import PcbPlacement
from .Candidates import PcbPlacementCandidate
from .Feedback import BuildCandidateStarvationPlacementEvidence, IsHigherOrderAssignmentCut, RequiresImmediateAssignmentCutRelocation, SelectAssignmentCutGeometrySignals, SelectCumulativeRepeatedAssignmentCutDiversificationSignals, SelectRefinedAssignmentCutDiversificationSignals, SelectRepeatedAssignmentSubcutDiversificationSignals, SelectRepeatedCandidateStarvationDiversificationSignals, SelectRepeatedLeaseRealizabilityGeometrySignals, SelectTopologyCutFrontier, ShouldDeferTopologyCutForMaterializedSibling, ShouldDiversifyRepeatedAssignmentCut, ShouldPreserveCurrentStructuredAssignmentCut
from .Portfolios import AccessDistinctAssignmentCutDiversificationEvidence, AssignmentCutHasBoundedExactCore, BuildCoordinatedCandidateDiversificationProfile, BuildSamePlacementRoutingControlRetryState, BuildTopologyCutEpochIdentity, CompleteAssignmentCutSupersedesLeasePairRetry, DeferredActivePortfolioAssignmentCut, ExtractAccessDistinctLeaseOwnershipFingerprints, HasDenseBoundaryLeaseRepairEligibility, HasTopologyCutEpochRoutingReserve, PlacementMatchesTopologyCutEpoch, SelectExhaustedRepeaterAccessCutSignals, SelectExhaustiveExactPairPinBankRepairSignals, SelectImmediateTopologyPinBankRepairSignals, SelectRepeatedHigherOrderPinBankRepairSignals, SelectRepeatedPairedLeaseSubcutSignals, SelectTopologyCoordinatedCandidateDiversificationSignals, SerializedPlacementAssignmentConstraintsAreActive, ShouldContinuePostPinBankRepairEpoch, ShouldOpenTopologyCutEpoch, TopologyCutEpochAdmissionReserveSeconds, TransactionalCutRepairSignals, TransactionalCutStrictlyNarrowsParentInterface
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
    _PlacementFlowDefault,
)


def SeedPortableRawPortalGeometryCaches(Context, Resources: Any) -> None:
    """Share only bounded immutable raw portal work across topology slots."""
    if not Context.TopologyDemand.RequiresJointPortfolio:
        return
    Combined = (*Context.PortableRawPortalGeometryCaches, *tuple(Resources.RawPortalGeometryCaches))
    Seen: set[int] = set()
    Retained = []
    for Cache in reversed(Combined):
        if id(Cache) in Seen:
            continue
        Seen.add(id(Cache))
        Retained.append(Cache)
        if len(Retained) >= Context.MaximumPortableRawPortalGeometryCaches:
            break
    Resources.RawPortalGeometryCaches = tuple(reversed(Retained))


def CapturePortableRawPortalGeometryCaches(Context, Resources: Any) -> None:
    """Retain newest cross-placement portal templates by object identity."""
    if not Context.TopologyDemand.RequiresJointPortfolio:
        return
    Combined = (*Context.PortableRawPortalGeometryCaches, *tuple(Resources.RawPortalGeometryCaches))
    Seen: set[int] = set()
    Retained = []
    for Cache in reversed(Combined):
        if id(Cache) in Seen:
            continue
        Seen.add(id(Cache))
        Retained.append(Cache)
        if len(Retained) >= Context.MaximumPortableRawPortalGeometryCaches:
            break
    Context.PortableRawPortalGeometryCaches = tuple(reversed(Retained))


def _PackedGateArea(Context, Candidate: PcbPlacement) -> int:
    Gates = Candidate.Placed.PlacedGates
    if not Gates:
        return 0
    MinimumX = min((Gate.X for Gate in Gates))
    MinimumZ = min((Gate.Z for Gate in Gates))
    MaximumX = max((Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1 for Gate in Gates))
    MaximumZ = max((Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1 for Gate in Gates))
    return (MaximumX - MinimumX + 1) * (MaximumZ - MinimumZ + 1)


def _InterClusterSignals(Context, Candidate: PcbPlacement) -> frozenset[str]:
    """Return signals whose endpoints span packed-cluster ownership."""
    ClusterByGate = {Name: ClusterIndex for ClusterIndex, Cluster in enumerate(Candidate.Clusters) for Name in Cluster}
    ProducerCluster = {Signal: ClusterByGate.get(Gate.Name) for Gate in Context.Module.Gates for Signal in Gate.Outputs}
    Result: set[str] = set()
    for Gate in Context.Module.Gates:
        TargetCluster = ClusterByGate.get(Gate.Name)
        if TargetCluster is None:
            continue
        for Signal in Gate.Inputs:
            SourceCluster = ProducerCluster.get(Signal)
            if SourceCluster is None or SourceCluster != TargetCluster:
                Result.add(Signal)
    return frozenset(Result)


def _RecordAssignmentCut(Context, Error: RoutingStageError, Candidate: PcbPlacementCandidate, *, DeferTopologyEpochForMaterializedSibling: bool=False) -> RoutingAssignmentCut | None:
    """Preserve one complete cut and prepare its exact placement repair."""
    OwnershipFingerprint = Candidate.TopologyDemand.MandatoryAccessOwnershipFingerprint if Candidate.TopologyDemand is not None else ''
    DenseLeaseRepairEligible = HasDenseBoundaryLeaseRepairEligibility(Candidate, Context.Policy)
    AccessDistinctLeaseOwnershipFingerprints = ExtractAccessDistinctLeaseOwnershipFingerprints(Error.Failure)
    TopologyAccessRepairEligible = bool(Candidate.TopologyDemand and Candidate.TopologyDemand.RequiresJointPortfolio) or DenseLeaseRepairEligible
    RepeaterReadyPortalRepairSignals = SelectExhaustedRepeaterAccessCutSignals(Error.Failure) if TopologyAccessRepairEligible else frozenset()
    ExistingRepeaterReadyPortalRepair = dict((Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__RepeaterReadyPortalRepair__', {}))
    if ExistingRepeaterReadyPortalRepair:
        RepeaterReadyPortalRepairSignals = frozenset()
    if TopologyAccessRepairEligible:
        Context.JointPortfolioTriggered = True
    AssignmentCut = RoutingAssignmentCut.FromFailure(Error.Failure, SourceCandidateId=Candidate.CandidateId, MandatoryAccessOwnershipFingerprint=OwnershipFingerprint)
    if AssignmentCut is None:
        return None
    Context.PlacementRepeatedCandidateStarvationSignals = frozenset()

    def RecordCandidateStarvationEvidence(Cut: RoutingAssignmentCut, *, CutFingerprint: str, ConstraintFingerprint: str) -> None:
        Evidence = BuildCandidateStarvationPlacementEvidence(Cut, AssignmentCutFingerprint=CutFingerprint, AssignmentConstraintFingerprint=ConstraintFingerprint)
        if Evidence is None:
            return
        if any((Existing.AssignmentCutFingerprint == Evidence.AssignmentCutFingerprint and Existing.AssignmentConstraintFingerprint == Evidence.AssignmentConstraintFingerprint and (Existing.AssignmentCut.MandatoryAccessOwnershipFingerprint == Evidence.AssignmentCut.MandatoryAccessOwnershipFingerprint) and (Existing.AssignmentCut.ConflictFingerprint == Evidence.AssignmentCut.ConflictFingerprint) for Existing in Context.CandidateStarvationPlacementHistory)):
            return
        Context.CandidateStarvationPlacementHistory.append(Evidence)
    Context.CutSourcePlacementByFingerprint[AssignmentCut.ConflictFingerprint] = Candidate.Placement
    DeferCurrentTopologyCut = ShouldDeferTopologyCutForMaterializedSibling(Requested=DeferTopologyEpochForMaterializedSibling, TopologyAccessRepairEligible=TopologyAccessRepairEligible, CommittedHistory=Context.PlacementAssignmentCutHistory, DeferredCuts=(Evidence.AssignmentCut for Evidence in Context.DeferredActivePortfolioAssignmentCuts), Current=AssignmentCut, SignalTopologyFingerprints=Context.SignalTopologyFingerprints, AllowRepeatedCutCommit=not (Candidate.SourceGenerator == 'transactional-cluster-endpoint-repair' and (not TransactionalCutStrictlyNarrowsParentInterface(frozenset(map(str, dict(Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {}).get('InternalPinBankGeometryRepairSignals', ()))), TransactionalCutRepairSignals(AssignmentCut)))))
    if DeferCurrentTopologyCut:
        Context.PlacementRepeatedCandidateStarvationSignals = SelectRepeatedCandidateStarvationDiversificationSignals(Context.CandidateStarvationPlacementHistory, AssignmentCut, AssignmentCutFingerprint=Candidate.AssignmentCutFingerprint, AssignmentConstraintFingerprint=Candidate.AssignmentConstraintFingerprint)
        RecordCandidateStarvationEvidence(AssignmentCut, CutFingerprint=Candidate.AssignmentCutFingerprint, ConstraintFingerprint=Candidate.AssignmentConstraintFingerprint)
        DeferredEvidence = DeferredActivePortfolioAssignmentCut(AssignmentCut=AssignmentCut, SourceCandidateId=Candidate.CandidateId, FailureStage=Error.Failure.Stage, Error=Error, Candidate=Candidate)
        if all((Existing.AssignmentCut.ConflictFingerprint != AssignmentCut.ConflictFingerprint or Existing.AssignmentCut.MandatoryAccessOwnershipFingerprint != AssignmentCut.MandatoryAccessOwnershipFingerprint for Existing in Context.DeferredActivePortfolioAssignmentCuts)):
            Context.DeferredActivePortfolioAssignmentCuts.append(DeferredEvidence)
        Context.LastCompletedAssignmentCutError = Error
        Context.JointPlacementStateEvents.append({'Status': 'active-portfolio-assignment-cut-deferred', 'CandidateId': Candidate.CandidateId, 'AssignmentCutFingerprint': AssignmentCut.ConflictFingerprint, 'MandatoryAccessOwnershipFingerprint': AssignmentCut.MandatoryAccessOwnershipFingerprint, 'DeferredCutCount': len(Context.DeferredActivePortfolioAssignmentCuts), 'RepeatedCandidateStarvationSignals': sorted(Context.PlacementRepeatedCandidateStarvationSignals), 'CandidateStarvationEvidenceEpoch': {'AssignmentCutFingerprint': Candidate.AssignmentCutFingerprint, 'AssignmentConstraintFingerprint': Candidate.AssignmentConstraintFingerprint}, 'NextAction': 'repair-repeated-sibling-starvation' if Context.PlacementRepeatedCandidateStarvationSignals else 'route-materialized-access-distinct-sibling'})
        return AssignmentCut
    if DeferTopologyEpochForMaterializedSibling and (not DeferCurrentTopologyCut):
        Context.JointPlacementStateEvents.append({'Status': 'active-portfolio-repeated-cut-commit-requested', 'CandidateId': Candidate.CandidateId, 'AssignmentCutFingerprint': AssignmentCut.ConflictFingerprint, 'MandatoryAccessOwnershipFingerprint': AssignmentCut.MandatoryAccessOwnershipFingerprint, 'DeferredCutCount': len(Context.DeferredActivePortfolioAssignmentCuts), 'NextAction': 'commit-repeated-cut-and-open-fresh-epoch'})
    if Context.DeferredActivePortfolioAssignmentCuts:
        DeferredCuts = tuple((Evidence.AssignmentCut for Evidence in Context.DeferredActivePortfolioAssignmentCuts))
        for DeferredCut in DeferredCuts:
            Context.PlacementAssignmentCutHistory.append(DeferredCut)
            Context.PlacementAssignmentConstraints = Context.PlacementAssignmentConstraints.WithCut(DeferredCut)
        Context.JointPlacementStateEvents.append({'Status': 'active-portfolio-assignment-cuts-committed', 'CommittedCutCount': len(DeferredCuts), 'AssignmentCutFingerprints': [Cut.ConflictFingerprint for Cut in DeferredCuts], 'SourceCandidateIds': [Evidence.SourceCandidateId for Evidence in Context.DeferredActivePortfolioAssignmentCuts], 'NextAction': 'open-aggregate-geometry-epoch'})
        Context.DeferredActivePortfolioAssignmentCuts.clear()
    RepeatedLeaseGeometrySignals = SelectRepeatedLeaseRealizabilityGeometrySignals(Error.Failure) if TopologyAccessRepairEligible else frozenset()
    Context.PlacementRepeatedLeaseGeometrySignals = RepeatedLeaseGeometrySignals
    PreserveCurrentStructuredCut = ShouldPreserveCurrentStructuredAssignmentCut(Context.CurrentPlacementAssignmentCut, Context.PlacementAssignmentConstraints, AssignmentCut) and (not RepeatedLeaseGeometrySignals)
    ActiveAssignmentCutFingerprint = Context.CurrentPlacementAssignmentCut.ConflictFingerprint if PreserveCurrentStructuredCut and Context.CurrentPlacementAssignmentCut is not None else ''
    ActiveAssignmentConstraintFingerprint = Context.PlacementAssignmentConstraints.Fingerprint if PreserveCurrentStructuredCut else ''
    CandidateStarvationEpochCutFingerprint = ActiveAssignmentCutFingerprint or Candidate.AssignmentCutFingerprint
    CandidateStarvationEpochConstraintFingerprint = ActiveAssignmentConstraintFingerprint or Candidate.AssignmentConstraintFingerprint
    RepeatedAcrossAccessDistinctPlacements = ShouldDiversifyRepeatedAssignmentCut(Context.PlacementAssignmentCutHistory, AssignmentCut, Context.SignalTopologyFingerprints)
    RefinedDiversificationSignals = SelectRefinedAssignmentCutDiversificationSignals(Context.PlacementAssignmentCutHistory, AssignmentCut)
    RepeatedSubcutDiversificationSignals = SelectRepeatedAssignmentSubcutDiversificationSignals(Context.PlacementAssignmentCutHistory, AssignmentCut)
    LeaseRepeatedPairSignals = SelectRepeatedPairedLeaseSubcutSignals(Context.PlacementAssignmentCutHistory, AssignmentCut, Context.SignalTopologyFingerprints) if DenseLeaseRepairEligible else frozenset()
    ExistingRoutingControlDiagnostics = dict((Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRelocation__', {}))
    CandidatePostPinBankRepairEpoch = bool(dict((Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {})).get('IsPostPinBankRepairEpoch', False))
    CandidateTransactionalRepairDiagnostics = dict((Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__TransactionalClusterEndpointRepair__', {}))
    CandidateUsedWitnessedMacroRotation = any((bool(ClusterDiagnostics.get('PriorityEndpointRotationDelta')) for ClusterDiagnostics in dict(CandidateTransactionalRepairDiagnostics.get('Clusters', {})).values() if isinstance(ClusterDiagnostics, dict)))
    ExistingJointRepairDiagnostics = dict((Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__JointClusterPlacement__', {}))
    CandidateHasActiveStructuredJointRepair = bool(SerializedPlacementAssignmentConstraintsAreActive(ExistingJointRepairDiagnostics.get('ActiveAssignmentConstraints')))
    LeasePairRetryAlreadyApplied = bool((Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__ClusterPinBankRepair__', {}))
    LeasePairRetryPending = bool(LeaseRepeatedPairSignals and (not LeasePairRetryAlreadyApplied) and (not CompleteAssignmentCutSupersedesLeasePairRetry(AssignmentCut)))
    LeasePairRetryProfileFingerprint = BuildCoordinatedCandidateDiversificationProfile(LeaseRepeatedPairSignals).Fingerprint if LeasePairRetryPending else ''
    LeasePairRetryAlreadyConsumed = bool(LeasePairRetryProfileFingerprint and LeasePairRetryProfileFingerprint in Context.ConsumedPairedLeaseRepairProfileFingerprints)
    if LeasePairRetryAlreadyConsumed:
        LeasePairRetryPending = False
    RepeatedCandidateStarvationSignals = SelectRepeatedCandidateStarvationDiversificationSignals(Context.CandidateStarvationPlacementHistory, AssignmentCut, AssignmentCutFingerprint=CandidateStarvationEpochCutFingerprint, AssignmentConstraintFingerprint=CandidateStarvationEpochConstraintFingerprint)
    Context.PlacementRepeatedCandidateStarvationSignals = RepeatedCandidateStarvationSignals
    ImmediateTopologyStarvationSignals = SelectImmediateTopologyPinBankRepairSignals(TopologyAccessRepairEligible=TopologyAccessRepairEligible, TopologyRequiresJointPortfolio=bool(Candidate.TopologyDemand and Candidate.TopologyDemand.RequiresJointPortfolio), AssignmentCut=AssignmentCut, Constraints=Context.PlacementAssignmentConstraints)
    RepeatedHigherOrderPinBankRepairSignals = SelectRepeatedHigherOrderPinBankRepairSignals(TopologyAccessRepairEligible=TopologyAccessRepairEligible, RepeatedAcrossAccessDistinctPlacements=RepeatedAcrossAccessDistinctPlacements, CandidatePostPinBankRepairEpoch=CandidatePostPinBankRepairEpoch, AssignmentCut=AssignmentCut)
    ExhaustiveExactPairPinBankRepairSignals = SelectExhaustiveExactPairPinBankRepairSignals(TopologyRequiresJointPortfolio=bool(Candidate.TopologyDemand and Candidate.TopologyDemand.RequiresJointPortfolio), CandidatePostPinBankRepairEpoch=CandidatePostPinBankRepairEpoch, AssignmentCut=AssignmentCut, Failure=Error.Failure)
    PinBankRepairSignals = frozenset((*RepeatedCandidateStarvationSignals, *ImmediateTopologyStarvationSignals, *RepeatedHigherOrderPinBankRepairSignals, *ExhaustiveExactPairPinBankRepairSignals))
    InternalPinBankRetryPending = bool(PinBankRepairSignals and (not LeasePairRetryAlreadyApplied) and (not RepeatedLeaseGeometrySignals))
    InternalPinBankRetryProfileFingerprint = BuildCoordinatedCandidateDiversificationProfile(PinBankRepairSignals).Fingerprint if InternalPinBankRetryPending else ''
    if InternalPinBankRetryProfileFingerprint in Context.ConsumedPairedLeaseRepairProfileFingerprints:
        InternalPinBankRetryPending = False
    RepeatedHigherOrderPinBankRetryPending = bool(InternalPinBankRetryPending and RepeatedHigherOrderPinBankRepairSignals)
    ExhaustiveExactPairPinBankRetryPending = bool(InternalPinBankRetryPending and ExhaustiveExactPairPinBankRepairSignals)
    if (RepeatedHigherOrderPinBankRetryPending or ExhaustiveExactPairPinBankRetryPending) and InternalPinBankRetryProfileFingerprint:
        Context.ConsumedPairedLeaseRepairProfileFingerprints.add(InternalPinBankRetryProfileFingerprint)
    Context.PlacementAssignmentCutHistory.append(AssignmentCut)
    RecordCandidateStarvationEvidence(AssignmentCut, CutFingerprint=CandidateStarvationEpochCutFingerprint, ConstraintFingerprint=CandidateStarvationEpochConstraintFingerprint)
    Context.PlacementAssignmentConstraints = Context.PlacementAssignmentConstraints.WithCut(AssignmentCut)
    if LeasePairRetryAlreadyApplied and (not PreserveCurrentStructuredCut):
        StaleJointFingerprints = tuple((Fingerprint for Fingerprint, (_Source, _Spacing, StalePlacement) in Context.UniquePlacements.items() if dict(StalePlacement.Placed.LocalRouteDiagnostics or {}).get('__JointClusterPlacement__', {}) and str(dict(dict(StalePlacement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {})).get('AssignmentConstraintFingerprint', '')) != Context.PlacementAssignmentConstraints.Fingerprint))
        for Fingerprint in StaleJointFingerprints:
            _DiscardPlacementFingerprint(Context, Fingerprint)
        if StaleJointFingerprints:
            Context.PlacementGenerationDecisions.append({'Result': 'pin-bank-cut-stale-geometry-evicted', 'EvictedPlacementFingerprints': list(StaleJointFingerprints), 'AssignmentConstraintFingerprint': Context.PlacementAssignmentConstraints.Fingerprint})
    TopologyEpochAssignmentCut = Context.CurrentPlacementAssignmentCut if PreserveCurrentStructuredCut and Context.CurrentPlacementAssignmentCut is not None else AssignmentCut
    TopologyCutEpoch = BuildTopologyCutEpochIdentity(TopologyEpochAssignmentCut, Context.PlacementAssignmentConstraints)
    TopologyCutEpochRequested = not LeasePairRetryPending and (bool(RepeatedLeaseGeometrySignals) or bool(ImmediateTopologyStarvationSignals) or ShouldOpenTopologyCutEpoch(TopologyRequiresJointPortfolio=TopologyAccessRepairEligible, AssignmentCut=AssignmentCut, Epoch=TopologyCutEpoch, OpenedEpochs=Context.OpenedTopologyCutEpochs))
    HasEpochRoutingReserve = HasTopologyCutEpochRoutingReserve(RemainingSeconds=Context.Deadline.RemainingSeconds(), Policy=Context.Policy, RequiresDenseBoundaryRouting=Context.TopologyPressure.ScaleGeometryPressure and (not ImmediateTopologyStarvationSignals), HasBoundedExactCutEvidence=AssignmentCutHasBoundedExactCore(AssignmentCut))
    if TopologyCutEpochRequested and HasEpochRoutingReserve:
        RequestedTopologyCutFrontier = SelectTopologyCutFrontier(TopologyEpochAssignmentCut, Context.PlacementAssignmentCutHistory, Enabled=Context.TopologyDemand.RequiresJointPortfolio)
        StaleStateCount = len(Context.PendingJointPlacementStates)
        Context.PendingJointPlacementStates.clear()
        StaleMaterializedFingerprints = tuple((Fingerprint for Fingerprint, (_Source, _Spacing, Placement) in Context.UniquePlacements.items() if Fingerprint != Candidate.PlacementFingerprint and bool(dict(Placement.Placed.LocalRouteDiagnostics or {}).get('__JointClusterPlacement__', {})) and (not PlacementMatchesTopologyCutEpoch(Placement, TopologyCutEpoch))))
        for Fingerprint in StaleMaterializedFingerprints:
            _DiscardPlacementFingerprint(Context, Fingerprint)
        Context.PendingTopologyCutEpoch = TopologyCutEpoch
        Context.NeedsFeedbackPlacementGeneration = True
        Context.PlacementGenerationDecisions.append({'Result': 'topology-cut-epoch-requested', 'AssignmentCutFingerprint': TopologyCutEpoch.AssignmentCutFingerprint, 'AssignmentConstraintFingerprint': TopologyCutEpoch.AssignmentConstraintFingerprint, 'MandatoryAccessOwnershipFingerprint': TopologyCutEpoch.MandatoryAccessOwnershipFingerprint, 'TopologyCutFrontier': [{'AssignmentCutFingerprint': Cut.ConflictFingerprint, 'AssignmentCutWorkFingerprint': Cut.EffectiveWorkFingerprint} for Cut in RequestedTopologyCutFrontier], 'TopologyCutFrontierCutCount': len(RequestedTopologyCutFrontier), 'RepeatedLeaseRealizabilityGeometrySignals': sorted(RepeatedLeaseGeometrySignals), 'CancelledStalePendingStateCount': StaleStateCount, 'CancelledStaleMaterializedStateCount': len(StaleMaterializedFingerprints), 'RemainingRoutingSeconds': round(max(0.0, Context.Deadline.RemainingSeconds()), 6)})
    elif TopologyCutEpochRequested:
        Context.PlacementGenerationDecisions.append({'Result': 'topology-cut-epoch-deferred-routing-reserve', 'AssignmentCutFingerprint': TopologyCutEpoch.AssignmentCutFingerprint, 'MandatoryAccessOwnershipFingerprint': TopologyCutEpoch.MandatoryAccessOwnershipFingerprint, 'RemainingRoutingSeconds': round(max(0.0, Context.Deadline.RemainingSeconds()), 6), 'RequiredRoutingReserveSeconds': round(TopologyCutEpochAdmissionReserveSeconds(Context.Policy, Context.TopologyPressure.ScaleGeometryPressure and (not ImmediateTopologyStarvationSignals), HasBoundedExactCutEvidence=AssignmentCutHasBoundedExactCore(AssignmentCut)), 6), 'Reason': 'retain current exact-legal access-distinct portfolio instead of cancelling it without a full routing slice'})
    if not PreserveCurrentStructuredCut:
        Context.CurrentPlacementAssignmentCut = AssignmentCut
        Context.NeedsCurrentStructuredCutRegeneration = False
    else:
        Context.NeedsCurrentStructuredCutRegeneration = True
    Context.LastCompletedAssignmentCutError = Error
    PairwiseSignals = frozenset((Signal for Edge in AssignmentCut.PairwiseConflictEdges for Signal in Edge))
    CompleteCutSignals = frozenset((*AssignmentCut.RelocationSignals, *AssignmentCut.ConflictSignals, *AssignmentCut.NoCandidateSignals, *PairwiseSignals, *RepeatedLeaseGeometrySignals))
    CutPrioritySignals = frozenset((*AssignmentCut.PriorityRelocationSignals, *RepeatedLeaseGeometrySignals)) or CompleteCutSignals
    GeometryCutSignals = SelectAssignmentCutGeometrySignals(TopologyRequiresJointPortfolio=TopologyAccessRepairEligible, AssignmentCut=AssignmentCut, CompleteCutSignals=CompleteCutSignals, PriorityCutSignals=CutPrioritySignals)
    CurrentCutCoordinatedCandidateDiversificationSignals = SelectTopologyCoordinatedCandidateDiversificationSignals(TopologyRequiresJointPortfolio=TopologyAccessRepairEligible, RepeatedExactCut=RepeatedAcrossAccessDistinctPlacements, CompleteCutSignals=CompleteCutSignals, RepeatedSubcutSignals=frozenset((*RepeatedSubcutDiversificationSignals, *(LeaseRepeatedPairSignals if LeasePairRetryPending else ()))))
    CumulativeRepeatedCutDiversificationSignals = SelectCumulativeRepeatedAssignmentCutDiversificationSignals(Context.PlacementAssignmentCutHistory, Context.PlacementAssignmentConstraints)
    if GeometryCutSignals and (not PreserveCurrentStructuredCut) and (not LeasePairRetryPending):
        Context.PlacementRelocationSignals = GeometryCutSignals if IsHigherOrderAssignmentCut(AssignmentCut) else frozenset((*Context.PlacementRelocationSignals, *GeometryCutSignals))
    if CutPrioritySignals and (not PreserveCurrentStructuredCut) and (not LeasePairRetryPending):
        Context.PlacementRelocationPrioritySignals = CutPrioritySignals
    if RequiresImmediateAssignmentCutRelocation(AssignmentCut) and (not PreserveCurrentStructuredCut) and (not LeasePairRetryPending):
        Context.PlacementRequiredRelocationSignals = CutPrioritySignals or GeometryCutSignals
        Context.NeedsFeedbackPlacementGeneration = True
    if not PreserveCurrentStructuredCut:
        Context.PlacementCoordinatedCandidateDiversificationSignals = CurrentCutCoordinatedCandidateDiversificationSignals
    elif CurrentCutCoordinatedCandidateDiversificationSignals:
        Context.PlacementCoordinatedCandidateDiversificationSignals = frozenset((*Context.PlacementCoordinatedCandidateDiversificationSignals, *CurrentCutCoordinatedCandidateDiversificationSignals))
    if LeasePairRetryPending or InternalPinBankRetryPending:
        Context.PlacementClusterPinBankRepairSignals = frozenset((*LeaseRepeatedPairSignals, *PinBankRepairSignals))
        Context.PlacementCoordinatedCandidateDiversificationSignals = frozenset((*Context.PlacementCoordinatedCandidateDiversificationSignals, *Context.PlacementClusterPinBankRepairSignals))
        Context.PostPinBankRepairEpochActive = True
        Context.InternalPinBankGeometryRepairActive = InternalPinBankRetryPending
        Context.RequiredDistinctPinBankOwnershipFingerprint = Candidate.TopologyDemand.MandatoryAccessOwnershipFingerprint if InternalPinBankRetryPending and Candidate.TopologyDemand is not None else ''
    elif not PreserveCurrentStructuredCut:
        Context.PlacementClusterPinBankRepairSignals = frozenset()
        Context.InternalPinBankGeometryRepairActive = False
        Context.RequiredDistinctPinBankOwnershipFingerprint = ''
        if RepeatedLeaseGeometrySignals:
            Context.PostPinBankRepairEpochActive = False
    if Candidate.SourceGenerator == 'transactional-cluster-endpoint-repair' and CandidateUsedWitnessedMacroRotation and RepeatedAcrossAccessDistinctPlacements and (not PreserveCurrentStructuredCut):
        Context.PostPinBankRepairEpochActive = True
        Context.InternalPinBankGeometryRepairActive = True
        Context.RotatedMacroAncestorTargetedEpochPending = True
        Context.PlacementClusterPinBankRepairSignals = frozenset(AssignmentCut.PriorityRelocationSignals or AssignmentCut.RelocationSignals)
        Context.PlacementGenerationDecisions.append({'Result': 'rotated-macro-ancestor-cut-targeted-epoch', 'CandidateId': Candidate.CandidateId, 'AssignmentCutFingerprint': AssignmentCut.ConflictFingerprint, 'RelocationSignals': sorted(Context.PlacementClusterPinBankRepairSignals)})
    AccessDistinctDiversificationEvidence = AccessDistinctAssignmentCutDiversificationEvidence(RepeatedExactCut=RepeatedAcrossAccessDistinctPlacements, RefinedExactCut=bool(RefinedDiversificationSignals), RepeatedExactSubcut=bool(RepeatedSubcutDiversificationSignals or LeasePairRetryPending or InternalPinBankRetryPending))
    Context.PendingSamePlacementRoutingControlRetry = BuildSamePlacementRoutingControlRetryState(PlacementFingerprint=Candidate.PlacementFingerprint, AssignmentCutFingerprint=AssignmentCut.ConflictFingerprint, Signals=PinBankRepairSignals if ImmediateTopologyStarvationSignals else Context.PlacementCoordinatedCandidateDiversificationSignals, Evidence=AccessDistinctDiversificationEvidence) if (not PreserveCurrentStructuredCut or InternalPinBankRetryPending) and (not CandidatePostPinBankRepairEpoch) and (not RepeatedHigherOrderPinBankRetryPending) and (not ExhaustiveExactPairPinBankRetryPending) and (LeasePairRetryPending or InternalPinBankRetryPending or (not LeasePairRetryAlreadyApplied and (not LeasePairRetryAlreadyConsumed) and (not CandidateHasActiveStructuredJointRepair))) else None
    if RepeaterReadyPortalRepairSignals:
        Context.PendingSamePlacementRoutingControlRetry = BuildSamePlacementRoutingControlRetryState(PlacementFingerprint=Candidate.PlacementFingerprint, AssignmentCutFingerprint=AssignmentCut.ConflictFingerprint, Signals=RepeaterReadyPortalRepairSignals, Evidence=AccessDistinctAssignmentCutDiversificationEvidence(ExhaustedRepeaterAccessCut=True))
        Context.PlacementCoordinatedCandidateDiversificationSignals = RepeaterReadyPortalRepairSignals
        Context.PlacementGenerationDecisions.append({'Result': 'repeater-ready-portal-retry-requested', 'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'AssignmentCutFingerprint': AssignmentCut.ConflictFingerprint, 'Signals': sorted(RepeaterReadyPortalRepairSignals), 'ReusedPlacedGeometry': True, 'NextAction': 'route-same-placement-with-repeater-ready-portals'})
    if Context.PendingSamePlacementRoutingControlRetry is not None and (LeasePairRetryProfileFingerprint or InternalPinBankRetryProfileFingerprint):
        Context.ConsumedPairedLeaseRepairProfileFingerprints.add(LeasePairRetryProfileFingerprint or InternalPinBankRetryProfileFingerprint)
    elif ImmediateTopologyStarvationSignals and InternalPinBankRetryProfileFingerprint:
        Context.ConsumedPairedLeaseRepairProfileFingerprints.add(InternalPinBankRetryProfileFingerprint)
    ContinuePostPinBankRepairEpoch = ShouldContinuePostPinBankRepairEpoch(CandidatePostPinBankRepairEpoch=CandidatePostPinBankRepairEpoch, InternalPinBankRetryPending=InternalPinBankRetryPending, ImmediateTopologyStarvationSignals=ImmediateTopologyStarvationSignals)
    if CandidatePostPinBankRepairEpoch and (not ContinuePostPinBankRepairEpoch):
        Context.PostPinBankRepairEpochActive = False
        Context.InternalPinBankGeometryRepairActive = False
        Context.RequiredDistinctPinBankOwnershipFingerprint = ''
    Context.PlacementGenerationDecisions.append({'Result': 'structured-assignment-cut-feedback', 'CandidateId': Candidate.CandidateId, 'AssignmentCut': AssignmentCut.ToDictionary(), 'ActivePlacementConstraints': Context.PlacementAssignmentConstraints.ToDictionary(), 'RepeatedAcrossAccessDistinctPlacements': RepeatedAcrossAccessDistinctPlacements, 'RepeatedLeaseRealizabilityGeometrySignals': sorted(RepeatedLeaseGeometrySignals), 'RefinedAcrossAccessDistinctPlacements': bool(RefinedDiversificationSignals), 'RepeatedAssignmentSubcutDiversificationSignals': sorted(RepeatedSubcutDiversificationSignals), 'AccessDistinctLeaseOwnershipFingerprints': list(AccessDistinctLeaseOwnershipFingerprints), 'LeaseRepeatedPairDiversificationSignals': sorted(LeaseRepeatedPairSignals), 'ClusterPinBankRepairSignals': sorted(Context.PlacementClusterPinBankRepairSignals), 'LeasePairRetryAlreadyApplied': LeasePairRetryAlreadyApplied, 'LeasePairRetryAlreadyConsumed': LeasePairRetryAlreadyConsumed, 'DenseLeaseRepairEligible': DenseLeaseRepairEligible, 'CumulativeRepeatedAssignmentCutDiversificationSignals': sorted(CumulativeRepeatedCutDiversificationSignals), 'RepeatedCandidateStarvationSignals': sorted(RepeatedCandidateStarvationSignals), 'RepeatedHigherOrderPinBankRepairSignals': sorted(RepeatedHigherOrderPinBankRepairSignals), 'RepeatedHigherOrderPinBankRetryPending': RepeatedHigherOrderPinBankRetryPending, 'ExhaustiveExactPairPinBankRepairSignals': sorted(ExhaustiveExactPairPinBankRepairSignals), 'ExhaustiveExactPairPinBankRetryPending': ExhaustiveExactPairPinBankRetryPending, 'ContinuedPostPinBankRepairEpoch': ContinuePostPinBankRepairEpoch, 'CandidateStarvationEvidenceEpoch': {'AssignmentCutFingerprint': CandidateStarvationEpochCutFingerprint, 'AssignmentConstraintFingerprint': CandidateStarvationEpochConstraintFingerprint} if CandidateStarvationEpochCutFingerprint and CandidateStarvationEpochConstraintFingerprint else None, 'PreservedCurrentStructuredCut': PreserveCurrentStructuredCut, 'CoordinatedCandidateDiversificationSignals': sorted(Context.PlacementCoordinatedCandidateDiversificationSignals), 'CurrentCutCoordinatedCandidateDiversificationSignals': sorted(CurrentCutCoordinatedCandidateDiversificationSignals), 'SamePlacementRoutingControlRetryEligible': Context.PendingSamePlacementRoutingControlRetry is not None, 'SamePlacementRoutingControlRetry': Context.PendingSamePlacementRoutingControlRetry.ToDictionary() if Context.PendingSamePlacementRoutingControlRetry is not None else None, 'NextAction': 'advance-retained-portfolio-with-current-structured-cut' if PreserveCurrentStructuredCut else 'joint-cut-relocation' if RequiresImmediateAssignmentCutRelocation(AssignmentCut) else 'bounded-placement-feedback'})
    return AssignmentCut


def _PlacementFailureWithHistory(Context, Failure: RoutingFailure) -> RoutingFailure:
    Diagnostics = dict(Failure.Diagnostics or {})
    Diagnostics.update({'PlacementGenerationFailures': Context.PlacementGenerationFailures, 'PlacementGenerationDecisions': Context.PlacementGenerationDecisions, 'PlacementAttempts': Context.PlacementAttemptFailures, 'JointPlacementStateEvents': Context.JointPlacementStateEvents, 'AssignmentCutHistory': [AssignmentCut.ToDictionary() for AssignmentCut in Context.PlacementAssignmentCutHistory], 'DeferredActivePortfolioAssignmentCuts': [Evidence.ToDictionary() for Evidence in Context.DeferredActivePortfolioAssignmentCuts], 'CurrentAssignmentCut': Context.CurrentPlacementAssignmentCut.ToDictionary() if Context.CurrentPlacementAssignmentCut is not None else None, 'ActivePlacementConstraints': Context.PlacementAssignmentConstraints.ToDictionary(), 'Deadline': Context.Deadline.ToDictionary()})
    return RoutingFailure(Reason=Failure.Reason, Stage=Failure.Stage, AffectedNets=Failure.AffectedNets, Resources=Failure.Resources, Locations=Failure.Locations, RepairActions=Failure.RepairActions, Detail=Failure.Detail, Diagnostics=Diagnostics)


def _DiscardPlacementFingerprint(Context, Fingerprint: str) -> None:
    """Remove one materialized placement from every identity/cache index."""
    RetentionFingerprint = Context.PlacementRetentionFingerprintByFingerprint.pop(Fingerprint, None)
    if RetentionFingerprint is not None:
        ExistingRetention = Context.RetainedPlacementTopologyFingerprints.get(RetentionFingerprint)
        if ExistingRetention is not None and ExistingRetention[0] == Fingerprint:
            Context.RetainedPlacementTopologyFingerprints.pop(RetentionFingerprint, None)
    Context.UniquePlacements.pop(Fingerprint, None)
    Context.FeedbackByFingerprint.pop(Fingerprint, None)
    Context.RoutingResourcesByFingerprint.pop(Fingerprint, None)
    Context.MaterializedPlacementByFingerprint.pop(Fingerprint, None)
    Context.TopologyDemandByFingerprint.pop(Fingerprint, None)
    Context.JointPlacementStateByPlacementFingerprint.pop(Fingerprint, None)

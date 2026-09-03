"""Importable placement-flow helpers with explicit run state."""

from __future__ import annotations

from dataclasses import replace
import os
from typing import Any, Callable, Iterable
from PhysicalDesign.Routing.Pcb import PrepareRawTrackAssignmentDomain, PrepareTrackAssignment
from PhysicalDesign.Routing.Assignment.TemplateAssignment import RawTrackAssignmentMaterialization, RawTrackAssignmentPortfolioTemplate
from PhysicalDesign.Contracts.Results import RoutedDesign
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Execution.Reliability import BuildStableFingerprint, RoutingDeadline
from PhysicalDesign.Redstone.Actions.Geometry import ForkRoutingResourcesWithSharedStaticGeometry
from PhysicalDesign.Policy import PhysicalDesignPolicy
from PhysicalDesign.Placement.PreRouteInterface import PreRouteInterfaceTemplate, PreRouteInterfaceWitness
from PhysicalDesign.Placement.Access.Fabric import AttachPlacementAccessFabric, BuildPlacementAccessFabric
from PhysicalDesign.Placement.Core.Clusters import PcbPlacement
from PhysicalDesign.Placement.Core.MandatoryAccess import MeasureMandatoryAccessConflictProfile
from .Candidates import PcbPlacementCandidate
from .Demand import MeasurePlacementTopologyDemand
from .Feedback import BuildPlacementFingerprint, ExtractCandidateStarvationSignals, ExtractPlacementRelocationSignals, FailurePrefersDirectOnlyPlacement, FailureRequestsPlacementAdvance, RequiresImmediateAssignmentCutRelocation, SelectCutDrivenClusterRefinementSignals, SelectReleasableLocalClaimSignals
from .Portfolios import ApplyActivePlacementAssignmentConstraints, ApplyCoordinatedCandidateDiversificationProfile, BuildPendingJointPlacementPortfolioFingerprint, BuildPendingJointPlacementPortfolioIdentity, BuildPendingJointPlacementStateKey, BuildSamePlacementRoutingControlRetryState, HasActiveMaterializedJointPlacementCandidate, HasCurrentMaterializedJointPlacementCandidate, HasCurrentPendingJointPlacementState, PendingJointPlacementState, PendingJointPlacementStateMatchesIdentity, PlacementAssignmentConstraintsAreActive, PlacementCandidateMatchesActiveJointPortfolio, PlacementConstraintFingerprintMatchesIdentity, RebindTerminalJointPlacementConstraintEpoch, RetainUnmaterializedJointPlacementStates, RoutingControlAttemptIdentity, SelectNewPendingJointPlacementPortfolioFingerprint, ShouldDeferSamePlacementRoutingControlRetry, ShouldRefreshTerminalActiveJointPlacementConstraintEpoch, ShouldRetrySamePlacementRoutingControl
from .Preparation import BuildDerivedPlacementCandidate, BuildFrozenEnvelopeRoutingPolicy, BuildPlacementRetentionFingerprint, PlacementCandidateIsExactAccessLegal, PlacementPortfolioGenerationNotAfter, RequiresDenseBoundaryRoutingReserve, RequiresExactClusterInterfaceSolve, ShouldEnableClusterBoundaryLeaseInterface, ShouldGiveRankedJointPortfolioLeadSlice, SummarizePreRouteAccessFabric, TopologyPortfolioRoutingFraction
from .Results import PcbProgress
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
    _PlacementFlowDefault,
)
from .AttemptHistory import (
    _DiscardPlacementFingerprint,
    _RecordAssignmentCut,
)
from .PlacementAttempts import (
    _TakeNextDeferredRequest,
    _TryPlacement,
)


def SolvePrePlacementCapacityProblem(Context, Candidates: Iterable[PcbPlacementCandidate]) -> tuple[list[dict[str, object]], list[PcbPlacementCandidate]]:
    """Solve the fixed placement disjunction once before routing."""
    Preparations: list[dict[str, object]] = []
    Feasible: list[PcbPlacementCandidate] = []
    for Candidate in Candidates:
        PreRejected = Context.PreRejectedCandidateEvidence.get(Candidate.CandidateId)
        if PreRejected is not None:
            Preparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'SelectedCandidateIds': [], 'CandidateCounts': [], 'ConflictResourceIndices': [], 'ExpansionCount': 0, **PreRejected})
            continue
        Fabric, AccessAssignment = Context.PlacementAccessEvidenceByCandidateId.get(Candidate.CandidateId, (Candidate.Placement.PlacementAccessFabric, Candidate.Placement.PlacementAccessAssignment))
        if Context.SinglePackedComponent:
            if Fabric is None:
                Preparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'Success': False, 'SelectedCandidateIds': [], 'CandidateCounts': [], 'ConflictSignals': [], 'ConflictResourceIndices': [], 'ExpansionCount': 0, 'Complete': False, 'IncompleteReason': 'missing-access-fabric', 'PlacementAccessFabric': None})
                continue
            Preparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'Success': bool(Fabric.Complete), 'SelectedCandidateIds': [], 'CandidateCounts': [], 'ConflictSignals': [], 'ConflictResourceIndices': [], 'ExpansionCount': int(Fabric.LegalEscapeExpansionCount), 'Complete': bool(Fabric.Complete), 'IncompleteReason': Fabric.IncompleteReason, 'PlacementAccessFabric': SummarizePreRouteAccessFabric(Fabric)})
            if Fabric.Complete:
                Feasible.append(Candidate)
            continue
        if AccessAssignment is not None and (not AccessAssignment.Success):
            Preparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, **AccessAssignment.ToDictionary(), 'PlacementAccessFabric': Fabric.ToDictionary() if Fabric is not None else None})
            continue
        if Fabric is not None and (not Fabric.Complete):
            Preparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'Success': False, 'SelectedCandidateIds': [], 'CandidateCounts': [], 'ConflictSignals': [], 'ConflictResourceIndices': [], 'ExpansionCount': 0, 'Complete': False, 'IncompleteReason': Fabric.IncompleteReason, 'PlacementAccessFabric': Fabric.ToDictionary()})
            continue
        DeferToExactClusterInterfaceSolve = (
            AccessAssignment is None
            and RequiresExactClusterInterfaceSolve(
                Candidate.TopologyDemand,
                Candidate.Placement.Placed,
                Context.Policy,
            )
        )
        if DeferToExactClusterInterfaceSolve:
            FabricComplete = PlacementCandidateIsExactAccessLegal(Candidate)
            Preparations.append({
                'CandidateId': Candidate.CandidateId,
                'PlacementFingerprint': Candidate.PlacementFingerprint,
                'Success': FabricComplete,
                'SelectedCandidateIds': [],
                'CandidateCounts': [],
                'ConflictSignals': [],
                'ConflictResourceIndices': [],
                'ExpansionCount': int(
                    getattr(Fabric, 'LegalEscapeExpansionCount', 0)
                ),
                'Complete': FabricComplete,
                'IncompleteReason': (
                    '' if FabricComplete else 'missing-access-fabric'
                ),
                'DeferredToExactClusterInterfaceSolve': True,
                'PlacementAccessFabric': (
                    Fabric.ToDictionary() if Fabric is not None else None
                ),
            })
            if FabricComplete:
                Feasible.append(Candidate)
            continue
        if AccessAssignment is None:
            CandidateResources = Context.RoutingResourcesByCandidateId.get(Candidate.CandidateId) or Context.RoutingResourcesByFingerprint.get(Candidate.PlacementFingerprint)
            if CandidateResources is None:
                CandidateResources = Context.Services.BuildRoutingResources(Candidate.Placement.Placed)
                Context.RoutingResourcesByFingerprint[Candidate.PlacementFingerprint] = CandidateResources
            try:
                Preparation = PrepareTrackAssignment(
                    Candidate.Placement,
                    Resources=CandidateResources,
                    Policy=Context.Policy,
                    Deadline=Context.Deadline,
                    DeferClusterBoundaryLeaseUntilCapacityPrecheck=(
                        RequiresExactClusterInterfaceSolve(
                            Candidate.TopologyDemand,
                            Candidate.Placement.Placed,
                            Context.Policy,
                        )
                    ),
                )
            except RoutingStageError as Error:
                if not (Error.Failure.Reason == RoutingFailureReason.ClusterInterfaceSolveIncomplete and Error.Failure.Stage == 'LocalClaimReleasePreScreen'):
                    raise
                FailureDiagnostics = dict(Error.Failure.Diagnostics or {})
                Preparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'Success': False, 'SelectedCandidateIds': [], 'CandidateCounts': [], 'ConflictSignals': list(Error.Failure.AffectedNets), 'ConflictResourceIndices': [], 'ExpansionCount': int(dict(FailureDiagnostics.get('LocalClaimReleaseSelection', {})).get('SearchExpansionCount', 0)), 'Complete': False, 'IncompleteReason': 'immutable-local-claim-conflict', 'LocalClaimReleaseSelection': FailureDiagnostics.get('LocalClaimReleaseSelection', {}), 'PlacementAccessFabric': Fabric.ToDictionary() if Fabric is not None else None})
                continue
            Preparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, **Preparation.ToDictionary(), 'PlacementAccessFabric': Fabric.ToDictionary() if Fabric is not None else None, 'RoutingAttemptCount': 0})
            if Preparation.Success and Preparation.Complete:
                Context.PrePlacementTrackPreparationWitnesses[Candidate.CandidateId] = Preparation
                Context.RoutingResourcesByCandidateId[Candidate.CandidateId] = CandidateResources
                Feasible.append(Candidate)
            continue
        Preparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, **AccessAssignment.ToDictionary(), 'PlacementAccessFabric': Fabric.ToDictionary() if Fabric is not None else None})
        if AccessAssignment.Success and AccessAssignment.Complete:
            Feasible.append(Candidate)
    return (Preparations, Feasible)


def PublishPreRouteTemplate(Context, Candidate: PcbPlacementCandidate, Fabric: Any, Assignment: Any) -> PreRouteInterfaceTemplate:
    """Publish one exact pre-route template after its fabric is built."""
    CandidateEnvelope = Candidate.RoutingEnvelope
    if CandidateEnvelope is None:
        raise RuntimeError('pre-route candidate is missing its derived envelope')
    CachedFabric, CachedAssignment = Context.PlacementAccessEvidenceByCandidateId.get(Candidate.CandidateId, (Candidate.Placement.PlacementAccessFabric, Candidate.Placement.PlacementAccessAssignment))
    if Fabric is None:
        Fabric = CachedFabric
    if Assignment is None:
        Assignment = CachedAssignment
    Preparation = None
    DeferredToExactClusterInterfaceSolve = bool(
        not Context.SinglePackedComponent
        and Assignment is None
        and PlacementCandidateIsExactAccessLegal(Candidate)
        and RequiresExactClusterInterfaceSolve(
            Candidate.TopologyDemand,
            Candidate.Placement.Placed,
            Context.Policy,
        )
    )
    Complete = (
        bool(Fabric is not None and Fabric.Complete)
        if Context.SinglePackedComponent
        else DeferredToExactClusterInterfaceSolve
        or bool(Assignment is not None and Assignment.Complete)
    )
    Success = (
        Complete
        if Context.SinglePackedComponent
        else DeferredToExactClusterInterfaceSolve
        or bool(Assignment is not None and Assignment.Success)
    )
    CapacityResources = (
        tuple(sorted(map(str, getattr(Fabric, 'CapacityResourceIds', ()))))
        if Context.SinglePackedComponent
        or DeferredToExactClusterInterfaceSolve
        else tuple(sorted({
            str(Value)
            for Value in (
                getattr(
                    Assignment,
                    'CapacityResourceIds',
                    getattr(Assignment, 'SelectedCapacityResourceIds', ()),
                )
                if Assignment is not None
                else ()
            )
        }))
    )
    FrozenOrdinaryCandidateIds: tuple[tuple[str, str], ...] = ()
    FrozenLocalClaimChoiceIds: tuple[tuple[str, str], ...] = ()
    FrozenLocalClaimDomainFingerprint = ''
    OfferedLocalClaims = Candidate.Placement.DerivedLocalRouteClaims or Candidate.Placement.Placed.DerivedLocalRouteClaims or Candidate.Placement.Placed.LocalRouteClaims or ()
    AccessLength = (
        sum(
            min(len(Stub.Path) for Stub in Domain.EscapeStubs)
            for Domain in getattr(Fabric, 'TerminalDomains', ())
            if Domain.EscapeStubs
        )
        if Context.SinglePackedComponent
        or DeferredToExactClusterInterfaceSolve
        else sum(
            len(Nodes)
            for _Signal, Nodes in getattr(Assignment, 'SignalRoutes', ())
        )
    )
    AccessMaterial = len(getattr(Fabric.PhysicalClaims, 'WireCells', ())) if Context.SinglePackedComponent and Fabric is not None else len(CapacityResources)
    DerivedPlacement = BuildDerivedPlacementCandidate(Candidate, CandidateEnvelope, Complete=Complete, WorkCount=int(getattr(Fabric, 'LegalEscapeExpansionCount', 0)) if Context.SinglePackedComponent else int(getattr(Assignment, 'ExpansionCount', 0)), IncompleteReason='' if Complete else Fabric.IncompleteReason if Context.SinglePackedComponent and Fabric is not None else Assignment.IncompleteReason if Assignment is not None else 'missing-pre-route-witness', FullEnvelopeBounds=tuple(map(int, Fabric.OuterBounds)) if Fabric is not None and Fabric.OuterBounds is not None else None)
    TemplateObjective = (*DerivedPlacement.ObjectivePrefix, AccessMaterial, AccessLength, int(Candidate.EstimatedGlobalExtensionNodes))
    Context.PreRouteObjectiveByCandidateId[Candidate.CandidateId] = TemplateObjective
    Witnesses = (PreRouteInterfaceWitness(WitnessId=BuildStableFingerprint((getattr(Fabric, 'FabricFingerprint', '') if Context.SinglePackedComponent else ('deferred-exact-cluster-interface', Candidate.PlacementFingerprint) if DeferredToExactClusterInterfaceSolve else getattr(Assignment, 'AssignmentFingerprint', ''), FrozenOrdinaryCandidateIds, FrozenLocalClaimChoiceIds, CapacityResources)), CapacityResourceIds=CapacityResources, Objective=TemplateObjective, FrozenContract=(Fabric, None) if Context.SinglePackedComponent else (None, None) if DeferredToExactClusterInterfaceSolve else (Assignment, Preparation)),) if Success and Complete else ()
    Template = PreRouteInterfaceTemplate(ComponentId='__placement__', TemplateId=Candidate.CandidateId, GeometryFingerprint=Candidate.PlacementFingerprint, LocalClaimsFingerprint=BuildStableFingerprint((tuple((str(Value) for Value in OfferedLocalClaims)), FrozenLocalClaimDomainFingerprint)), TerminalDomainFingerprint=Fabric.FabricFingerprint if Fabric is not None else '', SeamDomainFingerprint=BuildStableFingerprint((FrozenOrdinaryCandidateIds, FrozenLocalClaimChoiceIds, CapacityResources)), Witnesses=Witnesses, Complete=Complete, DerivedPlacement=DerivedPlacement, RoutingEnvelope=CandidateEnvelope, AccessRingTrackCount=int(getattr(Fabric, 'AccessRingTrackCount', CandidateEnvelope.AccessRingTrackCount)), AccessRingFingerprint=str(getattr(Fabric, 'AccessRingFingerprint', '')), IncompleteReason=Fabric.IncompleteReason if Context.SinglePackedComponent and Fabric is not None else Assignment.IncompleteReason if Assignment is not None else 'missing-pre-route-witness')
    Context.PreRouteTemplates.append(Template)
    return Template


def MaterializeRawTemplate(Context, Descriptor: RawTrackAssignmentPortfolioTemplate) -> RawTrackAssignmentMaterialization:
    Candidate = Context.CandidateById.get(Descriptor.TemplateId)
    if Candidate is None:
        raise RuntimeError('raw pre-route portfolio is missing its candidate')
    Existing = Context.RawTrackAssignmentMaterializations.get(Descriptor.TemplateId)
    if Existing is not None:
        return Existing
    FabricDescriptor = Context.PreRouteFabricDescriptorsByCandidateId.get(Descriptor.TemplateId)
    if FabricDescriptor is None:
        raise RuntimeError('raw pre-route portfolio is missing its fixed fabric descriptor')
    CandidateResources = ForkRoutingResourcesWithSharedStaticGeometry(FabricDescriptor.StaticResources)
    try:
        Fabric = BuildPlacementAccessFabric(Candidate.Placement, Resources=CandidateResources, Technology=Context.Technology, AccessLength=Candidate.RoutingEnvelope.AccessLength if Candidate.RoutingEnvelope is not None else None, TopologyKind=FabricDescriptor.TopologyKind, AccessRingTrackCount=FabricDescriptor.AccessRingTrackCount, Shell=FabricDescriptor.Shell, CompleteRouteSignals=frozenset(), DeriveLegalEscapeWorkLimit=FabricDescriptor.DeriveLegalEscapeWorkLimit, WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('PrePlacementAccessFabric', Diagnostics))
    except RoutingStageError as Error:
        Result = RawTrackAssignmentMaterialization(TemplateId=Descriptor.TemplateId, Domain=None, Complete=False, IncompleteReason=Error.Failure.Reason.value if hasattr(Error.Failure.Reason, 'value') else str(Error.Failure.Reason), Diagnostics=(('Candidate', Candidate.ToDictionary()), ('PlacementAccessFabricFailure', Error.Failure.ToDictionary()), ('FabricDescriptor', FabricDescriptor.ToDictionary())))
        Context.RawTrackAssignmentMaterializations[Descriptor.TemplateId] = Result
        return Result
    AttachedPlacement = AttachPlacementAccessFabric(Candidate.Placement, Fabric)
    Candidate = replace(Candidate, Placement=AttachedPlacement)
    Context.CandidateById[Candidate.CandidateId] = Candidate
    Context.CandidateRecords[Context.CandidateIndexById[Candidate.CandidateId]] = Candidate
    Context.PlacementAccessEvidenceByCandidateId[Candidate.CandidateId] = (Fabric, None)
    Context.RoutingResourcesByCandidateId[Candidate.CandidateId] = CandidateResources
    Context.PrePlacementTrackPreparations.append({'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'Success': bool(Fabric.Complete), 'SelectedCandidateIds': [], 'CandidateCounts': [], 'ConflictSignals': [], 'ConflictResourceIndices': [], 'ExpansionCount': int(Fabric.LegalEscapeExpansionCount), 'Complete': bool(Fabric.Complete), 'IncompleteReason': Fabric.IncompleteReason, 'PlacementAccessFabric': SummarizePreRouteAccessFabric(Fabric), 'FabricDescriptor': FabricDescriptor.ToDictionary()})
    Template = PublishPreRouteTemplate(Context, Candidate, Fabric, None)
    Context.TemplateById[Candidate.CandidateId] = Template
    if Fabric is None or not Fabric.Complete:
        Result = RawTrackAssignmentMaterialization(TemplateId=Descriptor.TemplateId, Domain=None, Complete=False, IncompleteReason=Fabric.IncompleteReason if Fabric is not None and Fabric.IncompleteReason else 'missing-access-fabric', Diagnostics=(('PlacementAccessFabric', SummarizePreRouteAccessFabric(Fabric)), ('FabricDescriptor', FabricDescriptor.ToDictionary())), ResolvedObjective=Template.Witnesses[0].Objective if Template.Witnesses else ())
        Context.RawTrackAssignmentMaterializations[Descriptor.TemplateId] = Result
        return Result
    CandidatePolicy = BuildFrozenEnvelopeRoutingPolicy(Context.Policy, Candidate.RoutingEnvelope) if Candidate.RoutingEnvelope is not None else Context.Policy
    try:
        RawDomain = PrepareRawTrackAssignmentDomain(Candidate.Placement, Resources=CandidateResources, Policy=CandidatePolicy, Deadline=Context.Deadline)
    except RoutingStageError as Error:
        Result = RawTrackAssignmentMaterialization(TemplateId=Descriptor.TemplateId, Domain=None, Complete=False, IncompleteReason=Error.Failure.Reason.value if hasattr(Error.Failure.Reason, 'value') else str(Error.Failure.Reason), Diagnostics=(('Candidate', Candidate.ToDictionary()), ('RawDomainFailure', Error.Failure.ToDictionary()), ('FabricDescriptor', FabricDescriptor.ToDictionary())), ResolvedObjective=Template.Witnesses[0].Objective if Template.Witnesses else ())
        Context.RawTrackAssignmentMaterializations[Descriptor.TemplateId] = Result
        return Result
    Result = RawTrackAssignmentMaterialization(TemplateId=Descriptor.TemplateId, Domain=RawDomain, Complete=RawDomain.Complete, IncompleteReason=RawDomain.IncompleteReason, Diagnostics=(('PlacementAccessFabric', SummarizePreRouteAccessFabric(Fabric)),), ResolvedObjective=Template.Witnesses[0].Objective if Template.Witnesses else ())
    Context.RawTrackAssignmentMaterializations[Descriptor.TemplateId] = Result
    return Result


def ReportRoutingProgress(Context, Completed: int, Total: int, Workers: int, Valid: int, Failed: int, BestRouted: RoutedDesign | None, Stage: str) -> None:
    if Context.ProgressCallback is None:
        return
    EffectiveTotal = max(1, Total)
    CandidateComplete = Completed >= EffectiveTotal or Valid > 0
    EffectiveCompleted = min(Completed, EffectiveTotal - 1) if CandidateComplete else Completed
    EffectiveValid = 0 if CandidateComplete else Valid
    EffectiveBestRouted = None if CandidateComplete else BestRouted
    EffectiveStage = 'routed candidate awaiting validation' if CandidateComplete and Failed == 0 else Stage
    BestFootprint = None
    BestBlocks = None
    BestWidth = None
    BestDepth = None
    if EffectiveBestRouted is not None:
        BestFootprint, BestBlocks, BestWidth, BestDepth = Context.Services.MeasurePcbDesign(Context.Placement.Placed, EffectiveBestRouted)
    Context.ProgressCallback(PcbProgress(Completed=EffectiveCompleted, Total=EffectiveTotal, Workers=Workers, Valid=EffectiveValid, BestBlocks=BestBlocks, BestWidth=BestWidth, BestDepth=BestDepth, BestFootprint=BestFootprint, Failed=Failed, Stage=f'spacing {Context.RoutingSpacing} | {EffectiveStage}'))


def _RouteWithFailedLocalClaimsReleased(Context, CandidatePlacement: PcbPlacement, AttemptPolicy: PhysicalDesignPolicy, AttemptDeadline: RoutingDeadline, Failure: RoutingFailure, AdaptiveStartedAt: float, AdaptiveExpiresAt: float) -> tuple[PcbPlacement, RoutedDesign] | None:
    """Release only an unextendable local tree and retain every clean tree.

        A packed local claim is an optimization, not a correctness dependency.
        When its boundary cannot be extended, the affected signal is returned
        to normal global routing while claims owned by unrelated signals remain
        authoritative base ownership.
        """
    if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: evaluating local-claim release reason={Failure.Reason} stage={Failure.Stage}', flush=True)
    if FailureRequestsPlacementAdvance(Failure):
        return None
    ReleasableReasons = {RoutingFailureReason.NoBoundaryEscape, RoutingFailureReason.PartialTreeExtensionFailed, RoutingFailureReason.MultiSourceStagnated, RoutingFailureReason.TrackAssignmentConflict, RoutingFailureReason.DetailedSearchExhausted}
    Signals = ExtractPlacementRelocationSignals(Failure)
    if not Signals:
        Signals = frozenset(Failure.AffectedNets)
    if Failure.Reason not in ReleasableReasons or not Signals:
        return None
    Original = CandidatePlacement.Placed
    if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: candidate local claim signals={sorted(Signals)} available={sorted({Claim.Signal for Claim in Original.LocalRouteClaims or ()})}', flush=True)
    ExistingClaims = tuple(Original.LocalRouteClaims or ())
    AllSignals = {Claim.Signal for Claim in ExistingClaims}
    if not AllSignals:
        return None
    Signals = SelectReleasableLocalClaimSignals(Signals, ExistingClaims)
    if not Signals:
        return None
    RetainedClaims = tuple((Claim for Claim in Original.LocalRouteClaims or () if Claim.Signal not in Signals))
    if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        print(f'[debug] authoritative: releasing local claims signals={sorted(Signals)} original={len(Original.LocalRouteClaims or ())} retained={len(RetainedClaims)}', flush=True)
    if len(RetainedClaims) == len(Original.LocalRouteClaims or ()):
        return None
    Context.Deadline.RaiseIfExpired('LocalClaimRelease', {'Phase': 'before-reroute', 'AffectedSignals': sorted(Signals)})
    ReleasedDiagnostics = dict(Original.LocalRouteDiagnostics or {})
    ReleasedDiagnostics['ReleasedLocalClaims'] = {'Signals': sorted(Signals), 'Reason': Failure.Reason.value, 'Stage': Failure.Stage}
    ReleasedPlaced = replace(Original, LocalRouteClaims=RetainedClaims, FrozenNetWires={Signal: Nodes for Signal, Nodes in (Original.FrozenNetWires or {}).items() if Signal not in Signals}, LocalNetBranches={Signal: Nodes for Signal, Nodes in (Original.LocalNetBranches or {}).items() if Signal not in Signals}, LocalNetTargets={Signal: Nodes for Signal, Nodes in (Original.LocalNetTargets or {}).items() if Signal not in Signals}, LocalRouteDiagnostics=ReleasedDiagnostics)
    ReleasedPlacement = replace(CandidatePlacement, Placed=ReleasedPlaced)
    RecoveryStartedAt = Context.Services.monotonic()
    RemainingAdaptiveSeconds = min(Context.Deadline.ExpiresAt, AdaptiveExpiresAt) - RecoveryStartedAt
    if RemainingAdaptiveSeconds <= 0:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.TrackAssignmentConflict, Stage='LocalClaimRelease', Detail='original placement adaptive slice expired before same-candidate local-claim recovery', RepairActions=('AdvancePlacementCandidate',), Diagnostics={'Action': 'advance-placement-adaptive-slice-expired', 'AdaptiveStartedAt': AdaptiveStartedAt, 'AdaptiveExpiresAt': AdaptiveExpiresAt, 'Deadline': Context.Deadline.ToDictionary()}))
    RecoveryPolicy = replace(AttemptPolicy, RuntimeBudgetSeconds=min(AttemptPolicy.RuntimeBudgetSeconds, Context.Deadline.RemainingSeconds(), RemainingAdaptiveSeconds), AdaptiveRouting=replace(AttemptPolicy.AdaptiveRouting, MaximumRuntimeSeconds=min(AttemptPolicy.AdaptiveRouting.MaximumRuntimeSeconds, RemainingAdaptiveSeconds)))
    ReleasedRouted = Context.Services.RoutePcbDesign(ReleasedPlacement, ProgressCallback=partial(ReportRoutingProgress, Context), Policy=RecoveryPolicy, Deadline=AttemptDeadline)
    if Context.Services.monotonic() >= AdaptiveExpiresAt:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.TrackAssignmentConflict, Stage='LocalClaimRelease', Detail='same-candidate local-claim recovery exceeded the original placement adaptive slice', RepairActions=('AdvancePlacementCandidate',), Diagnostics={'Action': 'advance-placement-adaptive-slice-expired', 'AdaptiveStartedAt': AdaptiveStartedAt, 'AdaptiveExpiresAt': AdaptiveExpiresAt, 'RecoveryStartedAt': RecoveryStartedAt, 'Deadline': Context.Deadline.ToDictionary()}))
    Context.Deadline.RaiseIfExpired('Routing', {'Recovery': 'released-affected-local-claims', 'AffectedSignals': sorted(Signals)})
    Context.Deadline.RaiseIfExpired('RoutedValidation', {'Phase': 'before', 'Recovery': 'released-affected-local-claims', 'AffectedSignals': sorted(Signals)})
    if Context.RoutedValidationCallback is not None:
        Context.RoutedValidationCallback(ReleasedRouted)
    Context.Deadline.RaiseIfExpired('RoutedValidation', {'Phase': 'after', 'Recovery': 'released-affected-local-claims', 'AffectedSignals': sorted(Signals)})
    return (ReleasedPlacement, ReleasedRouted)


def RecordRoutedCandidate(Context, Candidate: PcbPlacementCandidate, CandidatePlacement: PcbPlacement, CandidateRouted: RoutedDesign) -> None:
    """Score legal routed placements by final volume, then route share."""
    from PhysicalDesign.Rendering.SchemWriter import BuildLitematicBlockMap
    Composition = BuildLitematicBlockMap(CandidateRouted).Composition
    Score = (Composition.FullFootprint, Composition.RoutingFunctionalShare, Composition.RoutingOwnedFunctionalBlocks, Composition.Footprint, Composition.NonAirBlocks, Composition.Width, Composition.Depth, Candidate.CandidateId)
    Diagnostics: dict[str, object] = {'CandidateId': Candidate.CandidateId, 'RoutingFunctionalShare': Composition.RoutingFunctionalShare, 'RoutingOwnedFunctionalBlocks': Composition.RoutingOwnedFunctionalBlocks, 'NonAirBlocks': Composition.NonAirBlocks, 'Footprint': Composition.Footprint, 'XYFootprint': Composition.XYFootprint, 'FullFootprint': Composition.FullFootprint, 'Width': Composition.Width, 'Height': Composition.Height, 'Depth': Composition.Depth, 'Score': list(Score[:-1])}
    Context.RoutedCandidates.append((Score, Candidate, CandidatePlacement, CandidateRouted, Diagnostics))


def MaterializeSelectedJointPlacementLocalRouting(Context, Candidate: PcbPlacementCandidate, WorkCheck: Callable[[dict[str, object]], None]) -> PcbPlacement:
    """Materialize local routes only for a ranked joint candidate."""
    ScoringPlacement = Candidate.Placement
    ScoringDiagnostics = dict(ScoringPlacement.Placed.LocalRouteDiagnostics or {})
    DeferredDiagnostics = ScoringDiagnostics.get('__DeferredLocalRouting__', {})
    if not (isinstance(DeferredDiagnostics, dict) and bool(DeferredDiagnostics.get('ScoringOnly'))):
        return ScoringPlacement
    Cached = Context.MaterializedPlacementByFingerprint.get(Candidate.PlacementFingerprint)
    if Cached is not None:
        ScoringRelocation = ScoringDiagnostics.get('__PlacementRelocation__', {})
        if isinstance(ScoringRelocation, dict):
            ApplyCoordinatedCandidateDiversificationProfile(Cached, frozenset(map(str, ScoringRelocation.get('CoordinatedCandidateDiversificationSignals', ()))))
        ApplyActivePlacementAssignmentConstraints(Cached, Context.PlacementAssignmentConstraints)
        Context.JointPlacementStateEvents.append({'Status': 'local-routing-materialization-cache-hit', 'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint})
        return Cached
    State = Candidate.JointPlacementState or Context.JointPlacementStateByPlacementFingerprint.get(Candidate.PlacementFingerprint)
    if State is None:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PlacementOverlap, Stage='PlacementLocalRoutingMaterialization', Detail='a scoring-only retained placement was missing its immutable joint recipe state', RepairActions=('InspectJointPlacementStateRetention',), Diagnostics={'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint}))
    MaterializationStarted = Context.Services.monotonic()
    WorkCheck({'Phase': 'local-routing-materialization-start', 'CandidateId': Candidate.CandidateId})
    PackingPolicy = State.Request.PackingPolicy
    PhysicalProofCoreFocusedPlacement = bool(State.PhysicalProofCoreSignals)
    CutDrivenClusterRefinementSignals = (
        State.PhysicalProofCoreSignals
        if PhysicalProofCoreFocusedPlacement
        else SelectCutDrivenClusterRefinementSignals(
            State.AssignmentCut,
            Context.SignalTopologyFingerprints,
            Constraints=State.AssignmentConstraints,
        )
        if Context.TopologyDemand.RequiresJointPortfolio
        else None
    )
    FocusedCutEpochPlacement = bool(
        State.Request.UseCurrentAssignmentCutRelocationSignals
        or PhysicalProofCoreFocusedPlacement
    )
    Materialized = Context.Services.PlacePcbGraph(Context.Netlist, RoutingSpacing=State.RoutingSpacing, PlacementPolicy=Context.Policy.Placement, ClusterPolicy=Context.Policy.Clustering, MaximumBoundaryTerminals=Context.Policy.Organization.MaximumClusterEntrances, MaximumEntrancesPerSignal=Context.Policy.Organization.MaximumClusterEntrancesPerSignal, PackingPolicy=PackingPolicy, RelocationSignals=State.RelocationSignals, RelocationPrioritySignals=State.RelocationPrioritySignals, RequiredRelocationSignals=State.RequiredRelocationSignals, RelocationVariant=State.RelocationVariant, JointPlacementCandidateIndex=State.CandidateIndex, AssignmentCut=State.AssignmentCut, AssignmentConstraints=State.AssignmentConstraints, CoordinatedCandidateDiversificationSignals=State.CoordinatedCandidateDiversificationSignals, EnableClusterLocalRouteReuse=State.EnableClusterLocalRouteReuse or bool(ScoringDiagnostics.get('__ClusterPinBankRepair__', {})) or bool(State.AssignmentCut is not None and len(State.AssignmentCut.PairwiseConflictEdges) >= 2 and (Candidate.TopologyDemand is not None) and RequiresDenseBoundaryRoutingReserve(Candidate.TopologyDemand, Context.Policy)) or bool(Candidate.TopologyDemand is not None and RequiresDenseBoundaryRoutingReserve(Candidate.TopologyDemand, Context.Policy)), EnableClusterBoundaryLeases=ShouldEnableClusterBoundaryLeaseInterface(ScaleGeometryPressure=Context.TopologyPressure.ScaleGeometryPressure, TopologyRequiresJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio, IsPostPinBankRepairEpoch=State.IsPostPinBankRepairEpoch), EnableClusterInterfacePlacementFeasibility=Context.TopologyDemand.RequiresJointPortfolio, CutDrivenClusterRefinementSignals=CutDrivenClusterRefinementSignals, FixedConnectivityClusters=State.FixedConnectivityClusters, EnableInternalPinBankGeometryRepair=State.EnableInternalPinBankGeometryRepair, InternalPinBankGeometryRepairSignals=State.InternalPinBankGeometryRepairSignals, FocusedCutEpochPlacement=FocusedCutEpochPlacement, TopologyCutFrontier=State.TopologyCutFrontier, PlacementScoringOnly=False, WorkCheck=WorkCheck)
    ExpectedTopologyDemand = Candidate.TopologyDemand
    if ExpectedTopologyDemand is None:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PlacementOverlap, Stage='PlacementLocalRoutingMaterialization', Detail='selected local-route materialization lacked its retained topology proof', Diagnostics={'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint}))
    MaterializedFingerprint = BuildPlacementFingerprint(Materialized, ExpectedTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=False)
    MaterializedRetentionFingerprint = BuildPlacementRetentionFingerprint(Materialized, ExpectedTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=False)
    IdentityMatches = MaterializedFingerprint == Candidate.PlacementFingerprint and MaterializedRetentionFingerprint == Candidate.PlacementRetentionFingerprint
    MandatoryConflicts: dict[object, set[str]] = {}
    if IdentityMatches:
        MaterializedTopologyDemand = ExpectedTopologyDemand
        RankingMatches = True
    else:
        Context.Services.ValidatePlacedCellElectricalIsolation(Materialized.Placed, WorkCheck=WorkCheck)
        MandatoryProfile = MeasureMandatoryAccessConflictProfile(Materialized.Placed.PlacedGates, Materialized.SignalOrder, WorkCheck=WorkCheck)
        for Resource, Owners in (*MandatoryProfile.CrossConflicts, *MandatoryProfile.SelfConflicts):
            MandatoryConflicts.setdefault(Resource, set()).update(map(str, Owners))
        MaterializedTopologyDemand = MeasurePlacementTopologyDemand(Context.TopologyDemand, Materialized, MandatoryConflicts=MandatoryConflicts, MandatoryProfile=MandatoryProfile)
        RankingMatches = MaterializedTopologyDemand.JointOrderKey == ExpectedTopologyDemand.JointOrderKey
    if MandatoryConflicts or not RankingMatches or (not IdentityMatches):
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PlacementOverlap, Stage='PlacementLocalRoutingMaterialization', Detail='selected local-route materialization changed exact placement legality, ranking, or geometry identity', RepairActions=('InspectDeferredLocalRoutingIdentity',), Diagnostics={'CandidateId': Candidate.CandidateId, 'PlacementFingerprintExpected': Candidate.PlacementFingerprint, 'PlacementFingerprintMaterialized': MaterializedFingerprint, 'PlacementRetentionFingerprintExpected': Candidate.PlacementRetentionFingerprint, 'PlacementRetentionFingerprintMaterialized': MaterializedRetentionFingerprint, 'MandatoryAccessConflictResourceCount': len(MandatoryConflicts), 'RankingMatches': RankingMatches, 'IdentityMatches': IdentityMatches, 'ExpectedTopologyDemand': ExpectedTopologyDemand.ToDictionary() if ExpectedTopologyDemand is not None else None, 'MaterializedTopologyDemand': MaterializedTopologyDemand.ToDictionary()}))
    MaterializedDiagnostics = dict(Materialized.Placed.LocalRouteDiagnostics or {})
    for DiagnosticKey in ('__PlacementRecipe__', '__TopologyDemandProfile__'):
        if DiagnosticKey in ScoringDiagnostics:
            MaterializedDiagnostics[DiagnosticKey] = ScoringDiagnostics[DiagnosticKey]
    Materialized.Placed.LocalRouteDiagnostics = MaterializedDiagnostics
    ApplyActivePlacementAssignmentConstraints(Materialized, Context.PlacementAssignmentConstraints)
    ScoringRelocation = ScoringDiagnostics.get('__PlacementRelocation__', {})
    if isinstance(ScoringRelocation, dict):
        ApplyCoordinatedCandidateDiversificationProfile(Materialized, frozenset(map(str, ScoringRelocation.get('CoordinatedCandidateDiversificationSignals', ()))))
    if Candidate.TopologyDemand is not None and RequiresDenseBoundaryRoutingReserve(Candidate.TopologyDemand, Context.Policy) and Context.ConsumedPairedLeaseRepairProfileFingerprints and Context.PlacementCoordinatedCandidateDiversificationSignals:
        PreappliedDenseProfile, PreappliedDenseProfileFingerprint = ApplyCoordinatedCandidateDiversificationProfile(Materialized, Context.PlacementCoordinatedCandidateDiversificationSignals)
        if PreappliedDenseProfile:
            Materialized.Placed.LocalRouteDiagnostics.setdefault('__ClusterLocalRouteTemplates__', {})['PreappliedDenseRoutingProfile'] = {'Signals': sorted(Context.PlacementCoordinatedCandidateDiversificationSignals), 'ProfileFingerprint': PreappliedDenseProfileFingerprint}
    WorkCheck({'Phase': 'local-routing-materialization-complete', 'CandidateId': Candidate.CandidateId})
    Context.MaterializedPlacementByFingerprint[Candidate.PlacementFingerprint] = Materialized
    Context.JointPlacementStateEvents.append({'Status': 'local-routing-materialized', 'CandidateId': Candidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'LocalClaimCount': len(Materialized.Placed.LocalRouteClaims or ()), 'ElapsedSeconds': round(Context.Services.monotonic() - MaterializationStarted, 6), 'IdentityVerified': True, 'RankingVerified': True, 'ScoringIsolationProofReused': True, 'ScoringMandatoryAccessProofReused': True})
    return Materialized


def _PlacementCandidatesForRouting(Context):
    AttemptedFingerprints: set[str] = set()
    AttemptedRoutingControlIdentities: set[RoutingControlAttemptIdentity] = set()
    while True:
        if Context.PendingTopologyCutEpoch is not None:
            if Context.Deadline.IsExpired():
                return
            Request = _TakeNextDeferredRequest(Context)
            if Request is None:
                return
            try:
                _TryPlacement(Context, Request, CountPlacementGenerationAttempt=False)
            except RoutingStageError as Error:
                Context.LastRoutingError = Error
                Context.LastStructuredRoutingError = Error
                return
            Context.CandidateRecords = _BuildCandidateRecords(Context)
            Context.OrderedPlacements = Context.CandidateRecords[:RetainedRoutingCandidateLimit(Context, Context.CandidateRecords)]
            Context.PlacementFeedback[:] = [Candidate.ToDictionary() for Candidate in Context.CandidateRecords]
            continue
        CurrentConstraintFingerprint = Context.PlacementAssignmentConstraints.Fingerprint
        ConstraintIdentityActive = PlacementAssignmentConstraintsAreActive(Context.PlacementAssignmentConstraints)
        StaleMaterializedFingerprints = {Fingerprint for Fingerprint, (SourceGenerator, _CandidateSpacing, Candidate) in Context.UniquePlacements.items() if Fingerprint not in AttemptedFingerprints if bool(dict(Candidate.Placed.LocalRouteDiagnostics or {}).get('__JointClusterPlacement__', {})) if not (SourceGenerator == 'row-beam-conflict-relocation' and Context.ActiveJointPortfolioIdentityFingerprint and (str(dict(dict(Candidate.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {})).get('JointPortfolioIdentityFingerprint', '')) == Context.ActiveJointPortfolioIdentityFingerprint)) if not PlacementConstraintFingerprintMatchesIdentity(str(dict(dict(Candidate.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {})).get('AssignmentConstraintFingerprint', '')), CurrentConstraintFingerprint, ConstraintIdentityActive)}
        if StaleMaterializedFingerprints:
            Context.JointPlacementStateEvents.append({'Status': 'stale-materialized-candidates-discarded', 'DiscardedCandidateCount': len(StaleMaterializedFingerprints), 'DiscardedCandidateIds': [f'Placement-{Fingerprint[:12]}' for Fingerprint in sorted(StaleMaterializedFingerprints)], 'CurrentConstraintFingerprint': CurrentConstraintFingerprint})
        RemovedFingerprints = AttemptedFingerprints | StaleMaterializedFingerprints
        if RemovedFingerprints:
            for Fingerprint in RemovedFingerprints:
                _DiscardPlacementFingerprint(Context, Fingerprint)
            Context.CandidateRecords = _BuildCandidateRecords(Context)
            Context.OrderedPlacements = Context.CandidateRecords[:RetainedRoutingCandidateLimit(Context, Context.CandidateRecords)]
        ImmediateStructuredCutRelocation = RequiresImmediateAssignmentCutRelocation(Context.CurrentPlacementAssignmentCut)
        CurrentCutFingerprint = Context.CurrentPlacementAssignmentCut.ConflictFingerprint if Context.CurrentPlacementAssignmentCut is not None else ''
        HasCurrentPendingJointState = HasCurrentPendingJointPlacementState(Context.PendingJointPlacementStates, CurrentCutFingerprint, CurrentConstraintFingerprint)
        HasCurrentMaterializedJointCandidate = HasCurrentMaterializedJointPlacementCandidate(Context.OrderedPlacements, AttemptedFingerprints, CurrentCutFingerprint, CurrentConstraintFingerprint)
        HasActiveMaterializedJointCandidate = HasActiveMaterializedJointPlacementCandidate(Context.CandidateRecords, AttemptedFingerprints, Context.ActiveJointPortfolioIdentityFingerprint)
        HasActivePendingJointState = bool(Context.ActiveJointPortfolioIdentityFingerprint) and any((BuildPendingJointPlacementPortfolioFingerprint(State) == Context.ActiveJointPortfolioIdentityFingerprint for State in Context.PendingJointPlacementStates))
        if Context.ActiveJointPortfolioIdentityFingerprint and (not HasActiveMaterializedJointCandidate) and (not HasActivePendingJointState):
            Context.JointPlacementStateEvents.append({'Status': 'active-joint-portfolio-exhausted', 'PortfolioIdentityFingerprint': Context.ActiveJointPortfolioIdentityFingerprint})
            Context.ActiveJointPortfolioIdentityFingerprint = ''
            if Context.DeferredActivePortfolioAssignmentCuts:
                TerminalEvidence = Context.DeferredActivePortfolioAssignmentCuts.pop()
                Context.JointPlacementStateEvents.append({'Status': 'active-portfolio-exhausted-committing-cuts', 'DeferredCutCount': len(Context.DeferredActivePortfolioAssignmentCuts) + 1, 'TerminalCandidateId': TerminalEvidence.SourceCandidateId, 'NextAction': 'open-aggregate-geometry-epoch'})
                _RecordAssignmentCut(Context, TerminalEvidence.Error, TerminalEvidence.Candidate)
                continue
        if Context.NeedsFeedbackPlacementGeneration and (not HasActiveMaterializedJointCandidate) and (ImmediateStructuredCutRelocation and (not HasCurrentPendingJointState) and (not HasCurrentMaterializedJointCandidate) or (not Context.PendingJointPlacementStates and (not any((Candidate.PlacementFingerprint not in AttemptedFingerprints and Candidate.JointPortfolioCandidate for Candidate in Context.OrderedPlacements))))) and (Context.PlacementRelocationSignals and (Context.PlacementRelocationPrioritySignals != Context.LastRelocationPrioritySignalsUsed or Context.PlacementRequiredRelocationSignals != Context.LastRequiredRelocationSignalsUsed or Context.PlacementAssignmentConstraints.Fingerprint != Context.LastAssignmentConstraintFingerprintUsed) or bool(AttemptedFingerprints)):
            if Context.JointPortfolioPrimaryCandidateId is not None:
                Context.JointPlacementStateEvents.append({'Status': 'portfolio-preempted-exact-assignment-cut' if ImmediateStructuredCutRelocation else 'portfolio-exhausted-global-capacity-cut', 'AttemptedCandidateCount': len(AttemptedFingerprints), 'DeferredGeneratorSuppressed': False, 'NextAction': 'generate-exact-cut-relocation', 'AssignmentCut': Context.CurrentPlacementAssignmentCut.ToDictionary() if Context.CurrentPlacementAssignmentCut is not None else None})
                Context.JointPortfolioPrimaryCandidateId = None
            CurrentConstraintFingerprint = Context.PlacementAssignmentConstraints.Fingerprint
            if CurrentCutFingerprint:
                StalePendingCount = sum((1 for State in Context.PendingJointPlacementStates if not PendingJointPlacementStateMatchesIdentity(State, CurrentCutFingerprint, CurrentConstraintFingerprint)))
                if StalePendingCount:
                    Context.PendingJointPlacementStates[:] = [State for State in Context.PendingJointPlacementStates if PendingJointPlacementStateMatchesIdentity(State, CurrentCutFingerprint, CurrentConstraintFingerprint)]
                    Context.JointPlacementStateEvents.append({'Status': 'stale-cut-portfolios-discarded', 'DiscardedStateCount': StalePendingCount, 'CurrentConflictFingerprint': CurrentCutFingerprint, 'CurrentConstraintFingerprint': CurrentConstraintFingerprint})
            Context.NeedsFeedbackPlacementGeneration = False
            PreferDirectOnly = not ImmediateStructuredCutRelocation and Context.LastStructuredRoutingError is not None and (Context.LastAttemptedCandidate is not None) and FailurePrefersDirectOnlyPlacement(Context.LastStructuredRoutingError.Failure, Context.LastAttemptedCandidate)
            DirectOnlyPrioritySignals = ExtractCandidateStarvationSignals(Context.LastStructuredRoutingError.Failure) if PreferDirectOnly and Context.LastStructuredRoutingError is not None and Context.TopologyDemand.RequiresJointPortfolio else None
            Request = _TakeNextDeferredRequest(Context, PreferRelocation=not PreferDirectOnly, PreferDirectOnly=PreferDirectOnly, RequireExactCutBeforeBroad=ImmediateStructuredCutRelocation or ConstraintIdentityActive)
            GeneratedUniquePlacement = False
            LeadPortfolioConstraintFingerprint = Context.PlacementAssignmentConstraints.Fingerprint
            LeadPortfolioEpochChanged = False
            LeadPortfolioGenerationNotAfter = PlacementPortfolioGenerationNotAfter(Context.Policy, DeadlineExpiresAt=Context.Deadline.ExpiresAt, CurrentTime=Context.Services.monotonic(), RequiresDenseBoundaryRouting=Context.DenseBoundaryRoutingReserve) if Request is not None and Request.PackingPolicy.EnableJointClusterOrientation else None
            if Request is not None:
                try:
                    GeneratedUniquePlacement = _TryPlacement(Context, Request, FixedRelocationPrioritySignals=DirectOnlyPrioritySignals if Request.SourceGenerator == 'row-beam-direct-only' else None, PlacementGenerationNotAfter=LeadPortfolioGenerationNotAfter)
                except RoutingStageError as Error:
                    Context.LastRoutingError = Error
                    Context.LastStructuredRoutingError = Error
                    return
                LeadPortfolioEpochChanged = Context.PlacementAssignmentConstraints.Fingerprint != LeadPortfolioConstraintFingerprint
                while not GeneratedUniquePlacement and CurrentCutFingerprint and (not LeadPortfolioEpochChanged) and (LeadPortfolioGenerationNotAfter is None or Context.Services.monotonic() < LeadPortfolioGenerationNotAfter):
                    MatchingStateIndex = next((Index for Index, State in enumerate(Context.PendingJointPlacementStates) if PendingJointPlacementStateMatchesIdentity(State, CurrentCutFingerprint, CurrentConstraintFingerprint)), None)
                    if MatchingStateIndex is None:
                        break
                    JointState = Context.PendingJointPlacementStates.pop(MatchingStateIndex)
                    Context.JointPlacementStateEvents.append({'CandidateIndex': JointState.CandidateIndex, 'Status': 'materializing-after-lead-prescreen-rejection', 'SourceGenerator': JointState.Request.SourceGenerator, 'RoutingSpacing': JointState.RoutingSpacing, 'ConflictFingerprint': CurrentCutFingerprint})
                    try:
                        GeneratedUniquePlacement = _TryPlacement(Context, JointState.Request, JointPlacementCandidateIndex=JointState.CandidateIndex, FixedRelocationVariant=JointState.RelocationVariant, FixedCandidateSpacing=JointState.RoutingSpacing, FixedRelocationSignals=JointState.RelocationSignals, FixedRelocationPrioritySignals=JointState.RelocationPrioritySignals, FixedRequiredRelocationSignals=JointState.RequiredRelocationSignals, FixedAssignmentCut=JointState.AssignmentCut, FixedAssignmentConstraints=JointState.AssignmentConstraints, FixedCoordinatedCandidateDiversificationSignals=JointState.CoordinatedCandidateDiversificationSignals, FixedTopologyCutFrontier=JointState.TopologyCutFrontier, MaterializeRoutingResources=False, PlacementGenerationNotAfter=LeadPortfolioGenerationNotAfter)
                    except RoutingStageError as Error:
                        Context.LastRoutingError = Error
                        Context.LastStructuredRoutingError = Error
                        return
                    LeadPortfolioEpochChanged = Context.PlacementAssignmentConstraints.Fingerprint != LeadPortfolioConstraintFingerprint
                if not GeneratedUniquePlacement and LeadPortfolioGenerationNotAfter is not None and (Context.Services.monotonic() >= LeadPortfolioGenerationNotAfter):
                    Context.JointPlacementStateEvents.append({'Status': 'lead-portfolio-stopped-routing-floor', 'GenerationNotAfter': LeadPortfolioGenerationNotAfter, 'DeferredStateCount': len(Context.PendingJointPlacementStates)})
                Context.CandidateRecords = _BuildCandidateRecords(Context)
                Context.OrderedPlacements = Context.CandidateRecords[:RetainedRoutingCandidateLimit(Context, Context.CandidateRecords)]
                Context.PlacementFeedback[:] = [Candidate.ToDictionary() for Candidate in Context.CandidateRecords]
                if LeadPortfolioEpochChanged:
                    Context.JointPlacementStateEvents.append({'Status': 'lead-portfolio-constraint-epoch-changed', 'ConstraintFingerprintBefore': LeadPortfolioConstraintFingerprint, 'ConstraintFingerprintAfter': Context.PlacementAssignmentConstraints.Fingerprint, 'NextAction': 'restart-scheduler-before-broad'})
                    continue
        DensePrimaryPendingRouting = bool(Context.DenseBoundaryRoutingReserve and any((Candidate.JointPortfolioCandidate and Candidate.PlacementFingerprint not in AttemptedFingerprints and bool(dict(Candidate.Placement.Placed.LocalRouteDiagnostics or {}).get('__PlacementRecipe__', {}).get('IsPostPinBankRepairEpoch', False)) for Candidate in Context.CandidateRecords)))
        if Context.PendingJointPlacementStates and (not DensePrimaryPendingRouting) and (not HasActiveMaterializedJointCandidate or Context.TerminalConstraintEpochPortfolioNeedsMaterialization):
            MaterializingTerminalConstraintEpochPortfolio = Context.TerminalConstraintEpochPortfolioNeedsMaterialization
            Context.TerminalConstraintEpochPortfolioNeedsMaterialization = False
            PortfolioIdentity = BuildPendingJointPlacementPortfolioIdentity(Context.PendingJointPlacementStates[0])
            PortfolioIdentityFingerprint = BuildStableFingerprint(repr(PortfolioIdentity))
            PortfolioGenerationNotAfter = Context.JointPortfolioGenerationNotAfterByIdentity.setdefault(PortfolioIdentity, PlacementPortfolioGenerationNotAfter(Context.Policy, DeadlineExpiresAt=Context.Deadline.ExpiresAt, CurrentTime=Context.Services.monotonic(), RequiresDenseBoundaryRouting=Context.DenseBoundaryRoutingReserve))
            PortfolioStates = [State for State in Context.PendingJointPlacementStates if BuildPendingJointPlacementPortfolioIdentity(State) == PortfolioIdentity]
            Context.PendingJointPlacementStates[:] = [State for State in Context.PendingJointPlacementStates if BuildPendingJointPlacementPortfolioIdentity(State) != PortfolioIdentity]
            UniquePortfolioStates: list[PendingJointPlacementState] = []
            SeenPortfolioStateKeys: set[tuple[PendingJointPlacementPortfolioIdentity, int]] = set()
            for State in PortfolioStates:
                StateKey = BuildPendingJointPlacementStateKey(State)
                if StateKey in SeenPortfolioStateKeys or StateKey in Context.MaterializedJointPlacementStateKeys:
                    continue
                SeenPortfolioStateKeys.add(StateKey)
                UniquePortfolioStates.append(State)
            PortfolioStates = sorted(UniquePortfolioStates, key=lambda State: State.CandidateIndex)
            if Context.ExactClusterInterfaceSolveEnabled and len(PortfolioStates) > 6:
                TotalPortfolioStateCount = len(PortfolioStates) + 1
                SelectedPortfolioPositions = frozenset((Index * (TotalPortfolioStateCount - 1) // 5 - 1 for Index in range(1, 6)))
                PortfolioStates = [State for Position, State in enumerate(PortfolioStates) if Position in SelectedPortfolioPositions]
            PortfolioConstraintFingerprint = Context.PlacementAssignmentConstraints.Fingerprint
            PortfolioEpochChanged = False
            for PortfolioStateIndex, JointState in enumerate(PortfolioStates):
                if Context.Services.monotonic() >= PortfolioGenerationNotAfter:
                    DeferredStates = PortfolioStates[PortfolioStateIndex:]
                    if DeferredStates:
                        Context.PendingJointPlacementStates[:] = RetainUnmaterializedJointPlacementStates(Context.PendingJointPlacementStates, DeferredStates, Context.MaterializedJointPlacementStateKeys)
                    Context.JointPlacementStateEvents.append({'Status': 'portfolio-materialization-stopped-routing-floor', 'PortfolioIdentityFingerprint': PortfolioIdentityFingerprint, 'StoppedBeforeCandidateIndex': JointState.CandidateIndex, 'UnmaterializedStateCount': len(DeferredStates), 'GenerationNotAfter': PortfolioGenerationNotAfter, 'BroadGenerationDeferred': bool(DeferredStates), 'NextAction': 'materialize-retained-exact-state' if DeferredStates else 'route-materialized-exact-state'})
                    break
                StateKey = BuildPendingJointPlacementStateKey(JointState)
                Context.MaterializedJointPlacementStateKeys.add(StateKey)
                Context.JointPlacementStateEvents.append({'CandidateIndex': JointState.CandidateIndex, 'Status': 'materializing', 'SourceGenerator': JointState.Request.SourceGenerator, 'RoutingSpacing': JointState.RoutingSpacing})
                try:
                    _TryPlacement(Context, JointState.Request, JointPlacementCandidateIndex=JointState.CandidateIndex, FixedRelocationVariant=JointState.RelocationVariant, FixedCandidateSpacing=JointState.RoutingSpacing, FixedRelocationSignals=JointState.RelocationSignals, FixedRelocationPrioritySignals=JointState.RelocationPrioritySignals, FixedRequiredRelocationSignals=JointState.RequiredRelocationSignals, FixedAssignmentCut=JointState.AssignmentCut, FixedAssignmentConstraints=JointState.AssignmentConstraints, FixedCoordinatedCandidateDiversificationSignals=JointState.CoordinatedCandidateDiversificationSignals, FixedTopologyCutFrontier=JointState.TopologyCutFrontier, MaterializeRoutingResources=False, PlacementGenerationNotAfter=PortfolioGenerationNotAfter)
                except RoutingStageError as Error:
                    Context.LastRoutingError = Error
                    Context.LastStructuredRoutingError = Error
                    return
                CurrentConstraintFingerprint = Context.PlacementAssignmentConstraints.Fingerprint
                if CurrentConstraintFingerprint != PortfolioConstraintFingerprint:
                    PortfolioEpochChanged = True
                    Context.JointPlacementStateEvents.append({'Status': 'portfolio-constraint-epoch-changed', 'PortfolioIdentityFingerprint': PortfolioIdentityFingerprint, 'ConstraintFingerprintBefore': PortfolioConstraintFingerprint, 'ConstraintFingerprintAfter': CurrentConstraintFingerprint, 'DiscardedUnmaterializedStateCount': len(PortfolioStates) - len(Context.MaterializedJointPlacementStateKeys & SeenPortfolioStateKeys), 'NextAction': 'restart-exact-cut-placement'})
                    break
                if Context.DenseBoundaryRoutingReserve and (CurrentCutFingerprint or JointState.AssignmentCut is not None):
                    DeferredStates = PortfolioStates[PortfolioStateIndex + 1:]
                    if DeferredStates:
                        Context.PendingJointPlacementStates[:0] = DeferredStates
                    Context.JointPlacementStateEvents.append({'Status': 'dense-endgame-primary-materialized', 'PortfolioIdentityFingerprint': PortfolioIdentityFingerprint, 'CandidateId': Context.CandidateRecords[-1].CandidateId if Context.CandidateRecords else None, 'DeferredStateCount': len(DeferredStates), 'RemainingRoutingSeconds': round(Context.Deadline.RemainingSeconds(), 6)})
                    break
            if PortfolioEpochChanged:
                continue
            Context.CandidateRecords = _BuildCandidateRecords(Context)
            if PortfolioIdentity.SourceGenerator == 'row-beam-conflict-relocation' and any((Candidate.JointPortfolioIdentityFingerprint == PortfolioIdentityFingerprint for Candidate in Context.CandidateRecords)):
                Context.ActiveJointPortfolioIdentityFingerprint = PortfolioIdentityFingerprint
            Context.OrderedPlacements = Context.CandidateRecords[:RetainedRoutingCandidateLimit(Context, Context.CandidateRecords)]
            Context.JointPortfolioSliceSeconds = Context.Deadline.RemainingSeconds() * TopologyPortfolioRoutingFraction(HasRemainingPlacementAlternative=len(Context.OrderedPlacements) > 1, AttemptedCandidateCount=0)
            Context.JointPortfolioPrimaryCandidateId = Context.OrderedPlacements[0].CandidateId if Context.OrderedPlacements else None
            if MaterializingTerminalConstraintEpochPortfolio:
                Context.TerminalConstraintEpochPrimaryCandidateId = Context.JointPortfolioPrimaryCandidateId
            Context.JointPlacementStateEvents.append({'Status': 'portfolio-materialized', 'CandidateCount': len(Context.OrderedPlacements), 'EqualRoutingSliceSeconds': round(Context.JointPortfolioSliceSeconds, 6), 'PrimaryCandidateId': Context.JointPortfolioPrimaryCandidateId, 'PortfolioIdentityFingerprint': PortfolioIdentityFingerprint})
            Context.PlacementFeedback[:] = [Candidate.ToDictionary() for Candidate in Context.CandidateRecords]
        Pending = [Candidate for Candidate in Context.OrderedPlacements if Candidate.PlacementFingerprint not in AttemptedFingerprints]
        ActivePending = [Candidate for Candidate in Context.CandidateRecords if Candidate.PlacementFingerprint not in AttemptedFingerprints and PlacementCandidateMatchesActiveJointPortfolio(Candidate, Context.ActiveJointPortfolioIdentityFingerprint)]
        if ActivePending:
            Pending = ActivePending
        PrescreenRejectedPending = [Candidate for Candidate in Pending if not PlacementCandidateIsExactAccessLegal(Candidate)]
        if PrescreenRejectedPending:
            for Candidate in PrescreenRejectedPending:
                AttemptedFingerprints.add(Candidate.PlacementFingerprint)
                CandidateDemand = Candidate.TopologyDemand
                assert CandidateDemand is not None
                Context.JointPlacementStateEvents.append({'Status': 'exact-mandatory-access-prescreen-rejected', 'CandidateId': Candidate.CandidateId, 'SourceGenerator': Candidate.SourceGenerator, 'MandatoryAccessConflictResources': CandidateDemand.MandatoryAccessConflictResources, 'MandatoryAccessConflictSignals': list(CandidateDemand.MandatoryAccessConflictSignals), 'MandatoryAccessConflictFingerprint': CandidateDemand.MandatoryAccessConflictFingerprint, 'JointOrderKey': list(CandidateDemand.JointOrderKey), 'NextAction': 'route-next-exact-access-legal-candidate'})
                Context.PlacementGenerationDecisions.append({'Result': 'exact-mandatory-access-prescreen-rejected', 'CandidateId': Candidate.CandidateId, 'SourceGenerator': Candidate.SourceGenerator, 'MandatoryAccessConflictResources': CandidateDemand.MandatoryAccessConflictResources, 'MandatoryAccessConflictSignals': list(CandidateDemand.MandatoryAccessConflictSignals), 'MandatoryAccessConflictFingerprint': CandidateDemand.MandatoryAccessConflictFingerprint})
            continue
        if Pending:
            FeedbackPending = [Candidate for Candidate in Pending if Candidate.SourceGenerator == 'row-beam-conflict-relocation']
            if FeedbackPending and (not ActivePending):
                Pending = FeedbackPending
            NextCandidate = Pending[0]
            if Context.TerminalConstraintEpochPortfolioIdentityFingerprint and NextCandidate.JointPortfolioIdentityFingerprint == Context.TerminalConstraintEpochPortfolioIdentityFingerprint:
                IsTerminalRankedPrimary = ShouldGiveRankedJointPortfolioLeadSlice(ActiveRelocatedPortfolioCandidate=True, CandidateId=NextCandidate.CandidateId, PrimaryCandidateId=Context.TerminalConstraintEpochPrimaryCandidateId)
                Context.JointPlacementStateEvents.append({'Status': 'terminal-constraint-epoch-ranked-candidate-selected' if IsTerminalRankedPrimary else 'terminal-constraint-epoch-sibling-selected', 'CandidateId': NextCandidate.CandidateId, 'RankedPrimaryCandidateId': Context.TerminalConstraintEpochPrimaryCandidateId, 'PortfolioIdentityFingerprint': Context.TerminalConstraintEpochPortfolioIdentityFingerprint, 'RemainingRuntimeSeconds': round(Context.Deadline.RemainingSeconds(), 6)})
            if ShouldRefreshTerminalActiveJointPlacementConstraintEpoch(ActivePendingCount=len(ActivePending), CandidateSourceGenerator=NextCandidate.SourceGenerator, CandidateMatchesActivePortfolio=PlacementCandidateMatchesActiveJointPortfolio(NextCandidate, Context.ActiveJointPortfolioIdentityFingerprint), CandidateConstraintFingerprint=NextCandidate.AssignmentConstraintFingerprint, CurrentConstraintFingerprint=Context.PlacementAssignmentConstraints.Fingerprint, RefreshAlreadyPerformed=Context.TerminalConstraintEpochRefreshPerformed) and (not (Context.DenseBoundaryRoutingReserve and bool(ActivePending))):
                OriginalFingerprint = NextCandidate.PlacementFingerprint
                OriginalState = Context.JointPlacementStateByPlacementFingerprint.get(OriginalFingerprint)
                if OriginalState is None:
                    Failure = RoutingFailure(Reason=RoutingFailureReason.PlacementOverlap, Stage='PlacementConstraintEpochRefresh', Detail='the terminal retained placement was missing its immutable recipe state', RepairActions=('InspectJointPlacementStateRetention',), Diagnostics={'CandidateId': NextCandidate.CandidateId, 'PlacementFingerprint': OriginalFingerprint, 'CurrentConstraintFingerprint': Context.PlacementAssignmentConstraints.Fingerprint})
                    Error = RoutingStageError(Failure)
                    Context.PlacementGenerationFailures.append({'SourceGenerator': NextCandidate.SourceGenerator, 'RoutingSpacing': NextCandidate.RoutingSpacing, 'Failure': Failure.Detail, 'Diagnostics': Failure.ToDictionary()})
                    Context.LastRoutingError = Error
                    Context.LastStructuredRoutingError = Error
                    Context.LastCompletedAssignmentCutError = None
                    return
                ReboundState = RebindTerminalJointPlacementConstraintEpoch(OriginalState, Context.CurrentPlacementAssignmentCut, Context.PlacementAssignmentConstraints)
                ReboundPortfolioFingerprint = BuildPendingJointPlacementPortfolioFingerprint(ReboundState)
                Context.TerminalConstraintEpochRefreshPerformed = True
                Context.JointPlacementStateEvents.append({'Status': 'terminal-constraint-epoch-refresh-started', 'CandidateId': NextCandidate.CandidateId, 'CandidateIndex': ReboundState.CandidateIndex, 'PlacementFingerprintBefore': OriginalFingerprint, 'AssignmentConstraintFingerprintBefore': OriginalState.AssignmentConstraints.Fingerprint, 'AssignmentConstraintFingerprintAfter': ReboundState.AssignmentConstraints.Fingerprint, 'PortfolioIdentityFingerprintBefore': Context.ActiveJointPortfolioIdentityFingerprint, 'PortfolioIdentityFingerprintAfter': ReboundPortfolioFingerprint, 'TotalRelocationGenerationCount': Context.TotalRelocationGenerationCount})
                _DiscardPlacementFingerprint(Context, OriginalFingerprint)
                RefreshGenerationNotAfter = PlacementPortfolioGenerationNotAfter(Context.Policy, DeadlineExpiresAt=Context.Deadline.ExpiresAt, CurrentTime=Context.Services.monotonic(), RequiresDenseBoundaryRouting=Context.DenseBoundaryRoutingReserve)
                PendingRefreshStateKeysBefore = frozenset((BuildPendingJointPlacementStateKey(State) for State in Context.PendingJointPlacementStates))
                try:
                    Refreshed = _TryPlacement(Context, ReboundState.Request, JointPlacementCandidateIndex=ReboundState.CandidateIndex, FixedRelocationVariant=ReboundState.RelocationVariant, FixedCandidateSpacing=ReboundState.RoutingSpacing, FixedRelocationSignals=ReboundState.RelocationSignals, FixedRelocationPrioritySignals=ReboundState.RelocationPrioritySignals, FixedRequiredRelocationSignals=ReboundState.RequiredRelocationSignals, FixedAssignmentCut=ReboundState.AssignmentCut, FixedAssignmentConstraints=ReboundState.AssignmentConstraints, FixedCoordinatedCandidateDiversificationSignals=ReboundState.CoordinatedCandidateDiversificationSignals, FixedTopologyCutFrontier=ReboundState.TopologyCutFrontier, MaterializeRoutingResources=False, PlacementGenerationNotAfter=RefreshGenerationNotAfter, CountPlacementGenerationAttempt=False, QueueRetainedJointPortfolioStates=True)
                except RoutingStageError as Error:
                    Context.LastRoutingError = Error
                    Context.LastStructuredRoutingError = Error
                    Context.LastCompletedAssignmentCutError = None
                    return
                RefreshedSiblingPortfolioFingerprint = SelectNewPendingJointPlacementPortfolioFingerprint(Context.PendingJointPlacementStates, PendingRefreshStateKeysBefore, ReboundState.AssignmentConstraints.Fingerprint)
                if not Refreshed and RefreshedSiblingPortfolioFingerprint is not None:
                    Context.ActiveJointPortfolioIdentityFingerprint = RefreshedSiblingPortfolioFingerprint
                    Context.TerminalConstraintEpochPortfolioIdentityFingerprint = RefreshedSiblingPortfolioFingerprint
                    Context.TerminalConstraintEpochPortfolioNeedsMaterialization = True
                    Context.TerminalConstraintEpochPrimaryCandidateId = None
                    Context.JointPlacementStateEvents.append({'Status': 'terminal-constraint-epoch-lead-prescreen-rejected', 'CandidateIndex': ReboundState.CandidateIndex, 'AssignmentConstraintFingerprint': ReboundState.AssignmentConstraints.Fingerprint, 'PortfolioIdentityFingerprint': RefreshedSiblingPortfolioFingerprint, 'RequestedPortfolioIdentityFingerprint': ReboundPortfolioFingerprint, 'PendingSiblingStateCount': sum((BuildPendingJointPlacementPortfolioFingerprint(State) == RefreshedSiblingPortfolioFingerprint for State in Context.PendingJointPlacementStates)), 'NextAction': 'materialize-access-distinct-siblings'})
                    continue
                if not Refreshed:
                    Failure = RoutingFailure(Reason=RoutingFailureReason.PlacementOverlap, Stage='PlacementConstraintEpochRefresh', Detail='the terminal retained placement could not be rematerialized as a unique exact-legal candidate under the current cumulative assignment constraints', RepairActions=('InspectJointPlacementConstraintProjection',), Diagnostics={'CandidateIndex': ReboundState.CandidateIndex, 'AssignmentConstraintFingerprint': ReboundState.AssignmentConstraints.Fingerprint, 'PortfolioIdentityFingerprint': ReboundPortfolioFingerprint, 'TotalRelocationGenerationCount': Context.TotalRelocationGenerationCount})
                    Error = RoutingStageError(Failure)
                    Context.PlacementGenerationFailures.append({'SourceGenerator': ReboundState.Request.SourceGenerator, 'RoutingSpacing': ReboundState.RoutingSpacing, 'JointPlacementCandidateIndex': ReboundState.CandidateIndex, 'Failure': Failure.Detail, 'Diagnostics': Failure.ToDictionary()})
                    Context.LastRoutingError = Error
                    Context.LastStructuredRoutingError = Error
                    Context.LastCompletedAssignmentCutError = None
                    return
                Context.CandidateRecords = _BuildCandidateRecords(Context)
                Context.OrderedPlacements = Context.CandidateRecords[:RetainedRoutingCandidateLimit(Context, Context.CandidateRecords)]
                RefreshedCandidates = [Candidate for Candidate in Context.CandidateRecords if Candidate.SourceGenerator == 'row-beam-conflict-relocation' and Candidate.AssignmentConstraintFingerprint == Context.PlacementAssignmentConstraints.Fingerprint and (Context.JointPlacementStateByPlacementFingerprint.get(Candidate.PlacementFingerprint) is not None) and (Context.JointPlacementStateByPlacementFingerprint[Candidate.PlacementFingerprint].CandidateIndex == ReboundState.CandidateIndex)]
                if not RefreshedCandidates or (len(RefreshedCandidates) != 1 and (not Context.DenseBoundaryRoutingReserve)):
                    Failure = RoutingFailure(Reason=RoutingFailureReason.PlacementOverlap, Stage='PlacementConstraintEpochRefresh', Detail='the terminal constraint-epoch refresh did not publish exactly one active candidate', RepairActions=('InspectJointPlacementPortfolioIdentity',), Diagnostics={'PublishedCandidateCount': len(RefreshedCandidates), 'PortfolioIdentityFingerprint': ReboundPortfolioFingerprint})
                    Error = RoutingStageError(Failure)
                    Context.PlacementGenerationFailures.append({'SourceGenerator': ReboundState.Request.SourceGenerator, 'RoutingSpacing': ReboundState.RoutingSpacing, 'JointPlacementCandidateIndex': ReboundState.CandidateIndex, 'Failure': Failure.Detail, 'Diagnostics': Failure.ToDictionary()})
                    Context.LastRoutingError = Error
                    Context.LastStructuredRoutingError = Error
                    Context.LastCompletedAssignmentCutError = None
                    return
                Context.PlacementFeedback[:] = [Candidate.ToDictionary() for Candidate in Context.CandidateRecords]
                RefreshedCandidate = min(RefreshedCandidates, key=lambda Candidate: (Candidate.JointExactScore, Candidate.PlacementFingerprint))
                Context.ActiveJointPortfolioIdentityFingerprint = RefreshedCandidate.JointPortfolioIdentityFingerprint
                Context.TerminalConstraintEpochPortfolioIdentityFingerprint = Context.ActiveJointPortfolioIdentityFingerprint
                Context.TerminalConstraintEpochPortfolioNeedsMaterialization = bool(Context.PendingJointPlacementStates)
                Context.JointPlacementStateEvents.append({'Status': 'terminal-constraint-epoch-refresh-complete', 'CandidateId': RefreshedCandidate.CandidateId, 'CandidateIndex': ReboundState.CandidateIndex, 'PlacementFingerprintBefore': OriginalFingerprint, 'PlacementFingerprintAfter': RefreshedCandidate.PlacementFingerprint, 'AssignmentConstraintFingerprint': RefreshedCandidate.AssignmentConstraintFingerprint, 'PortfolioIdentityFingerprint': Context.ActiveJointPortfolioIdentityFingerprint, 'RequestedPortfolioIdentityFingerprint': ReboundPortfolioFingerprint, 'TotalRelocationGenerationCount': Context.TotalRelocationGenerationCount, 'DeferredEquivalentCandidateCount': len(RefreshedCandidates) - 1})
                continue
            ActiveConstraintsRebound, ActiveConstraintsFingerprint = ApplyActivePlacementAssignmentConstraints(NextCandidate.Placement, Context.PlacementAssignmentConstraints)
            if ActiveConstraintsRebound:
                Context.JointPlacementStateEvents.append({'Status': 'active-assignment-constraints-rebound', 'CandidateId': NextCandidate.CandidateId, 'PlacementFingerprint': NextCandidate.PlacementFingerprint, 'ActiveAssignmentConstraintFingerprint': ActiveConstraintsFingerprint, 'ActiveAssignmentConstraints': Context.PlacementAssignmentConstraints.ToDictionary()})
            RoutingControlProfileRebound, RoutingControlProfileFingerprint = ApplyCoordinatedCandidateDiversificationProfile(NextCandidate.Placement, (CandidateCoordinatedCandidateDiversificationSignals := Context.PlacementCoordinatedCandidateDiversificationSignals), EnableClusterPinBankRepair=bool(Context.PlacementClusterPinBankRepairSignals) and Context.PlacementClusterPinBankRepairSignals.issubset(CandidateCoordinatedCandidateDiversificationSignals))
            if RoutingControlProfileRebound:
                Context.JointPlacementStateEvents.append({'Status': 'coordinated-routing-control-profile-rebound', 'CandidateId': NextCandidate.CandidateId, 'PlacementFingerprint': NextCandidate.PlacementFingerprint, 'CoordinatedCandidateDiversificationSignals': sorted(CandidateCoordinatedCandidateDiversificationSignals), 'RoutingControlProfileFingerprint': RoutingControlProfileFingerprint})
            if RoutingControlProfileFingerprint:
                AttemptedRoutingControlIdentities.add(RoutingControlAttemptIdentity(PlacementFingerprint=NextCandidate.PlacementFingerprint, RoutingControlProfileFingerprint=RoutingControlProfileFingerprint))
            AttemptedFingerprints.add(NextCandidate.PlacementFingerprint)
            Context.LastAttemptedCandidate = NextCandidate
            yield NextCandidate
            RetryState = Context.PendingSamePlacementRoutingControlRetry
            Context.PendingSamePlacementRoutingControlRetry = None
            EffectiveRetryState = BuildSamePlacementRoutingControlRetryState(PlacementFingerprint=RetryState.AttemptIdentity.PlacementFingerprint, AssignmentCutFingerprint=RetryState.AssignmentCutFingerprint, Signals=(*CandidateCoordinatedCandidateDiversificationSignals, *RetryState.Profile.Signals), Evidence=RetryState.Evidence) if RetryState is not None else None
            HasRemainingActivePortfolioSibling = HasActiveMaterializedJointPlacementCandidate(Context.CandidateRecords, AttemptedFingerprints, Context.ActiveJointPortfolioIdentityFingerprint)
            if ShouldDeferSamePlacementRoutingControlRetry(EffectiveRetryState, HasRemainingActivePortfolioSibling=HasRemainingActivePortfolioSibling) and (not Context.PlacementClusterPinBankRepairSignals):
                Context.PendingSamePlacementRoutingControlRetry = RetryState
                Context.JointPlacementStateEvents.append({'Status': 'same-placement-routing-control-retry-deferred', 'CandidateId': NextCandidate.CandidateId, **EffectiveRetryState.ToDictionary(), 'NextAction': 'route-active-access-distinct-sibling'})
                continue
            if ShouldRetrySamePlacementRoutingControl(EffectiveRetryState, NextCandidate.PlacementFingerprint, AttemptedRoutingControlIdentities):
                assert EffectiveRetryState is not None
                RetryProfileRebound, RetryProfileFingerprint = ApplyCoordinatedCandidateDiversificationProfile(NextCandidate.Placement, frozenset(EffectiveRetryState.Profile.Signals), EnableClusterPinBankRepair=bool(Context.PlacementClusterPinBankRepairSignals) and Context.PlacementClusterPinBankRepairSignals.issubset(EffectiveRetryState.Profile.Signals), EnableRepeaterReadyPortalRepair=EffectiveRetryState.Evidence.ExhaustedRepeaterAccessCut)
                if RetryProfileRebound and RetryProfileFingerprint == EffectiveRetryState.AttemptIdentity.RoutingControlProfileFingerprint:
                    AttemptedRoutingControlIdentities.add(EffectiveRetryState.AttemptIdentity)
                    Context.JointPlacementStateEvents.append({'Status': 'same-placement-routing-control-retry', 'CandidateId': NextCandidate.CandidateId, **EffectiveRetryState.ToDictionary(), 'PreviousRoutingControlProfileFingerprint': RoutingControlProfileFingerprint, 'ReusedPlacedGeometry': True, 'ReusedRoutingResources': NextCandidate.PlacementFingerprint in Context.RoutingResourcesByFingerprint, 'RemainingRuntimeSeconds': round(Context.Deadline.RemainingSeconds(), 6), 'NextAction': 'route-same-placement-before-placement-generation'})
                    Context.LastAttemptedCandidate = NextCandidate
                    yield NextCandidate
                    Context.PendingSamePlacementRoutingControlRetry = None
            continue
        if Context.PendingJointPlacementStates:
            JointState = Context.PendingJointPlacementStates.pop(0)
            Context.JointPlacementStateEvents.append({'CandidateIndex': JointState.CandidateIndex, 'Status': 'materializing', 'SourceGenerator': JointState.Request.SourceGenerator, 'RoutingSpacing': JointState.RoutingSpacing})
            try:
                _TryPlacement(Context, JointState.Request, JointPlacementCandidateIndex=JointState.CandidateIndex, FixedRelocationVariant=JointState.RelocationVariant, FixedCandidateSpacing=JointState.RoutingSpacing, FixedRelocationSignals=JointState.RelocationSignals, FixedRelocationPrioritySignals=JointState.RelocationPrioritySignals, FixedRequiredRelocationSignals=JointState.RequiredRelocationSignals, FixedAssignmentCut=JointState.AssignmentCut, FixedAssignmentConstraints=JointState.AssignmentConstraints, FixedCoordinatedCandidateDiversificationSignals=JointState.CoordinatedCandidateDiversificationSignals, FixedTopologyCutFrontier=JointState.TopologyCutFrontier)
            except RoutingStageError as Error:
                Context.LastRoutingError = Error
                Context.LastStructuredRoutingError = Error
                return
            Context.CandidateRecords = _BuildCandidateRecords(Context)
            Context.OrderedPlacements = Context.CandidateRecords[:RetainedRoutingCandidateLimit(Context, Context.CandidateRecords)]
            Context.PlacementFeedback[:] = [Candidate.ToDictionary() for Candidate in Context.CandidateRecords]
            continue
        if Context.Deadline.IsExpired():
            return
        if Context.ExactClusterInterfaceSolveEnabled:
            Context.PlacementGenerationDecisions.append({'Result': 'dense-broad-generation-disabled', 'ExecutableLegacyRepairCascade': False, 'RemainingDeferredGeneratorCount': len(Context.GenerationPlan.DeferredRequests) - len(Context.ConsumedDeferredRequestIndexes)})
            return
        Request = _TakeNextDeferredRequest(Context)
        if Request is None:
            return
        try:
            _TryPlacement(Context, Request)
        except RoutingStageError as Error:
            Context.LastRoutingError = Error
            Context.LastStructuredRoutingError = Error
            return
        Context.CandidateRecords = _BuildCandidateRecords(Context)
        Context.OrderedPlacements = Context.CandidateRecords[:RetainedRoutingCandidateLimit(Context, Context.CandidateRecords)]
        Context.PlacementFeedback[:] = [Candidate.ToDictionary() for Candidate in Context.CandidateRecords]

from dataclasses import replace
import os
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Execution.Reliability import BuildStableFingerprint
from PhysicalDesign.Placement.Core.Clusters import PcbPlacement
from PhysicalDesign.Placement.Core.Repair import BuildTransactionalClusterEndpointRepair
from .Candidates import PcbPlacementCandidate
from .Demand import MeasurePlacementTopologyDemand
from .Feedback import BuildPlacementFingerprint
from .Portfolios import ApplyActivePlacementAssignmentConstraints, ApplyCoordinatedCandidateDiversificationProfile, TransactionalEndpointRepairIdentityIsFresh
from .Preparation import BuildClusterInterfacePlacementTopologyFingerprint, BuildPlacementRetentionFingerprint, IsDerivedSingleComponentPlacementSource, PlacementCandidateOrder
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
    _PlacementFlowDefault,
)
from .AttemptHistory import (
    _PlacementFailureWithHistory,
)


def _BuildCandidateRecords(Context) -> list[PcbPlacementCandidate]:

    def JointExactScore(Candidate: PcbPlacement) -> tuple[int, ...]:
        """Order retained joint states by materialized access pressure."""
        Diagnostics = dict(Candidate.Placed.LocalRouteDiagnostics or {})
        Joint = dict(Diagnostics.get('__JointClusterPlacement__', {}))
        Exact = dict(Joint.get('ExactPreScreen', {}))
        if not Joint:
            return ()
        return (int(Exact.get('MandatoryAccessConflictResources', 0)), int(Exact.get('BoundaryOverflow', 0)), int(Exact.get('PinScarcityCount', 0)), int(Exact.get('LocalClaimCount', 0)), int(Joint.get('SelectedScore', 0)))
    CandidateRecords: list[PcbPlacementCandidate] = []
    for CandidateIndex, (Fingerprint, (SourceGenerator, CandidateSpacing, Candidate)) in enumerate(sorted(Context.UniquePlacements.items())):
        CandidateTopologyDemand = Context.TopologyDemandByFingerprint.get(Fingerprint, Context.TopologyDemand)
        CandidateDiagnostics = dict(Candidate.Placed.LocalRouteDiagnostics or {})
        CandidateRecipe = dict(CandidateDiagnostics.get('__PlacementRecipe__', {}))
        JointPortfolioCandidate = bool(CandidateDiagnostics.get('__JointClusterPlacement__', {}))
        Feedback = None
        if Context.Policy.Placement.EnableRoutingFeedback and (not bool(os.environ.get('RCS_SKIP_PLACEMENT_FEEDBACK'))) and (not JointPortfolioCandidate) and (not IsDerivedSingleComponentPlacementSource(SourceGenerator)):
            Feedback = Context.FeedbackByFingerprint.get(Fingerprint)
            if Feedback is None:
                raise RoutingStageError(_PlacementFailureWithHistory(Context, RoutingFailure(Reason=RoutingFailureReason.Stagnated, Stage='PlacementFeedback', Detail='retained placement was missing its bounded routing-feedback record', RepairActions=('AdvancePlacementGenerator',), Diagnostics={'PlacementFingerprint': Fingerprint, 'SourceGenerator': SourceGenerator})))
        FeedbackScore = Feedback.Score if Feedback is not None else (CandidateIndex,)
        CandidateRecords.append(PcbPlacementCandidate(CandidateId=f'Placement-{Fingerprint[:12]}', SourceGenerator=SourceGenerator, RoutingSpacing=CandidateSpacing, PlacementFingerprint=Fingerprint, FeedbackScore=tuple(FeedbackScore), BoundaryOverflow=Feedback.BoundaryOverflow if Feedback is not None else 0, PinScarcityCount=Feedback.PinScarcityCount if Feedback is not None else 0, GuideOverflowPeak=Feedback.GuideOverflowPeak if Feedback is not None else 0, GuideOverflowCells=Feedback.GuideOverflowCells if Feedback is not None else 0, PinEscapeConflictCount=Feedback.PinEscapeConflictCount if Feedback is not None else 0, EstimatedGlobalExtensionNodes=Feedback.EstimatedGlobalExtensionNodes if Feedback is not None else 0, EstimatedGlobalExtensionNets=Feedback.EstimatedGlobalExtensionNets if Feedback is not None else 0, PreOwnedNodeCount=Feedback.PreOwnedNodeCount if Feedback is not None else 0, Placement=Candidate, PlacementRetentionFingerprint=Context.PlacementRetentionFingerprintByFingerprint.get(Fingerprint, ''), InterfaceTopologyFingerprint=BuildClusterInterfacePlacementTopologyFingerprint(Candidate, Context.SignalTopologyFingerprints), JointPlacementState=Context.JointPlacementStateByPlacementFingerprint.get(Fingerprint), AssignmentCutFingerprint=str(CandidateRecipe.get('AssignmentCutFingerprint', '')), AssignmentConstraintFingerprint=str(CandidateRecipe.get('AssignmentConstraintFingerprint', '')), JointPortfolioIdentityFingerprint=str(CandidateRecipe.get('JointPortfolioIdentityFingerprint', '')), JointExactScore=JointExactScore(Candidate), TopologyDemand=CandidateTopologyDemand, JointPortfolioCandidate=JointPortfolioCandidate, Feedback=Feedback))
    CandidateRecords.sort(key=lambda Value: PlacementCandidateOrder(Value, Context.ConfiguredRoutingSpacing))
    ActiveCut = Context.CurrentPlacementAssignmentCut
    ReferencePlacement = Context.CutSourcePlacementByFingerprint.get(ActiveCut.ConflictFingerprint) if ActiveCut is not None else None
    if Context.TopologyDemand.RequiresJointPortfolio and ActiveCut is not None and (ReferencePlacement is not None):
        CutSignals = frozenset(ActiveCut.RelocationSignals)
        CutGateNames = frozenset((Gate.Name for Gate in Context.Module.Gates if any((Signal in CutSignals for Signal in Gate.Outputs)) or any((Signal in CutSignals for Signal in Gate.Inputs))))
        ReferenceGeometry = {Gate.Name: (Gate.X, Gate.Y, Gate.Z, Gate.Rotation, Gate.MirrorX) for Gate in ReferencePlacement.Placed.PlacedGates if Gate.Name in CutGateNames}

        def CutInterfaceDifference(Candidate: PcbPlacementCandidate) -> int:
            if Candidate.AssignmentCutFingerprint != ActiveCut.ConflictFingerprint or Candidate.TopologyDemand is None or Candidate.TopologyDemand.MandatoryAccessOwnershipFingerprint == ActiveCut.MandatoryAccessOwnershipFingerprint:
                return 0
            return sum((abs(Gate.X - Reference[0]) + abs(Gate.Y - Reference[1]) + abs(Gate.Z - Reference[2]) + (Gate.Rotation != Reference[3]) + (Gate.MirrorX != Reference[4]) for Gate in Candidate.Placement.Placed.PlacedGates if Gate.Name in CutGateNames for Reference in (ReferenceGeometry.get(Gate.Name),) if Reference is not None))
        CandidateRecords.sort(key=lambda Candidate: (CutInterfaceDifference(Candidate) == 0, -CutInterfaceDifference(Candidate)))
        InterfaceDifferences = {Candidate.PlacementFingerprint: CutInterfaceDifference(Candidate) for Candidate in CandidateRecords}
        AccessDistinctCandidateCount = sum((Difference > 0 for Difference in InterfaceDifferences.values()))
        CandidateRecords = [replace(Candidate, CutInterfaceDifference=InterfaceDifferences[Candidate.PlacementFingerprint], AccessDistinctCandidateCount=AccessDistinctCandidateCount) for Candidate in CandidateRecords]
        Context.PlacementGenerationDecisions.append({'Result': 'topology-cut-interface-diversity-order', 'AssignmentCutFingerprint': ActiveCut.ConflictFingerprint, 'CandidateInterfaceDifferences': [{'CandidateId': Candidate.CandidateId, 'Difference': CutInterfaceDifference(Candidate)} for Candidate in CandidateRecords]})
    return CandidateRecords


def RetainedRoutingCandidateLimit(Context, Candidates: list[PcbPlacementCandidate]) -> int:
    """Keep the complete joint portfolio only when it is actually active."""
    JointPortfolioActive = any((Candidate.JointPortfolioCandidate for Candidate in Candidates))
    return max(1, Context.Policy.NandPacking.RetainedPlacementCandidates, Context.Policy.NandPacking.RetainedJointPlacementCandidates if JointPortfolioActive else 1)


def _TransactionalEndpointRepairPortfolioFingerprint(Context, SourceCandidate: PcbPlacementCandidate, RepairSignals: frozenset[str], RepairClusterCount: int=1) -> str:
    """Identify the bounded local ECO siblings of one exact cut."""
    return BuildStableFingerprint(('transactional-cluster-endpoint-repair', SourceCandidate.PlacementFingerprint, Context.CurrentPlacementAssignmentCut.ConflictFingerprint if Context.CurrentPlacementAssignmentCut is not None else '', Context.PlacementAssignmentConstraints.Fingerprint, tuple(sorted(map(str, RepairSignals))), max(1, RepairClusterCount)))


def _PublishTransactionalClusterEndpointRepair(Context, SourceCandidate: PcbPlacementCandidate, RepairSignals: frozenset[str], RepairVariant: int=0, RepairClusterCount: int=1, RepairTerminalPositions: frozenset[tuple[int, int, int]]=frozenset(), RepairEndpointGateNames: frozenset[str]=frozenset(), AllowStableMandatoryAccessOwnership: bool=False) -> bool:
    """Publish one access-distinct local ECO without global replacement."""
    if not RepairSignals or SourceCandidate.TopologyDemand is None or (not SourceCandidate.TopologyDemand.RequiresJointPortfolio):
        return False
    StartedAt = Context.Services.monotonic()
    try:
        Result = BuildTransactionalClusterEndpointRepair(SourceCandidate.Placement, RepairSignals, BeamWidth=min(16, Context.Policy.NandPacking.BeamWidth), RepairVariant=RepairVariant, RepairClusterCount=RepairClusterCount, RepairTerminalPositions=RepairTerminalPositions, RepairEndpointGateNames=RepairEndpointGateNames, WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('TransactionalClusterEndpointRepair', {'CandidateId': SourceCandidate.CandidateId, **Diagnostics}))
    except RoutingStageError as Error:
        Context.PlacementGenerationDecisions.append({'Result': 'transactional-cluster-endpoint-repair-expired', 'CandidateId': SourceCandidate.CandidateId, 'Signals': sorted(RepairSignals), 'Failure': Error.Failure.ToDictionary(), 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
        return False
    if not Result.Accepted or Result.Placement is None:
        Context.PlacementGenerationDecisions.append({'Result': 'transactional-cluster-endpoint-repair-rejected', 'CandidateId': SourceCandidate.CandidateId, 'Signals': sorted(RepairSignals), 'Diagnostics': Result.Diagnostics, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
        return False
    Candidate = Result.Placement
    CandidateProfile = Candidate.MandatoryAccessPreScreenProfile
    if CandidateProfile is None:
        Context.PlacementGenerationDecisions.append({'Result': 'transactional-cluster-endpoint-repair-rejected', 'CandidateId': SourceCandidate.CandidateId, 'Signals': sorted(RepairSignals), 'Diagnostics': {**Result.Diagnostics, 'Reason': 'missing-mandatory-access-profile'}, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
        return False
    MandatoryConflicts: dict[object, set[str]] = {}
    for Resource, Owners in (*CandidateProfile.CrossConflicts, *CandidateProfile.SelfConflicts):
        MandatoryConflicts.setdefault(Resource, set()).update(map(str, Owners))
    CandidateTopologyDemand = MeasurePlacementTopologyDemand(Context.TopologyDemand, Candidate, MandatoryConflicts=MandatoryConflicts, MandatoryProfile=CandidateProfile)
    StableMandatoryAccessOwnership = CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint == SourceCandidate.TopologyDemand.MandatoryAccessOwnershipFingerprint
    if MandatoryConflicts or (StableMandatoryAccessOwnership and (not AllowStableMandatoryAccessOwnership)):
        Context.PlacementGenerationDecisions.append({'Result': 'transactional-cluster-endpoint-repair-rejected', 'CandidateId': SourceCandidate.CandidateId, 'Signals': sorted(RepairSignals), 'Diagnostics': {**Result.Diagnostics, 'Reason': 'mandatory-conflict-or-stagnant-ownership', 'MandatoryConflictResourceCount': len(MandatoryConflicts), 'StableMandatoryAccessOwnership': StableMandatoryAccessOwnership, 'StableMandatoryAccessOwnershipAllowed': AllowStableMandatoryAccessOwnership}, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
        return False
    CandidateDiagnostics = dict(Candidate.Placed.LocalRouteDiagnostics or {})
    PortfolioIdentityFingerprint = _TransactionalEndpointRepairPortfolioFingerprint(Context, SourceCandidate, RepairSignals, RepairClusterCount)
    SourceRecipe = dict(CandidateDiagnostics.get('__PlacementRecipe__', {}))
    TransactionalRepairSignalHistory = [sorted(frozenset(map(str, Signals))) for Signals in SourceRecipe.get('TransactionalRepairSignalHistory', ()) if isinstance(Signals, tuple | list | set | frozenset) and Signals]
    CurrentRepairSignalSet = sorted(frozenset(map(str, RepairSignals)))
    if CurrentRepairSignalSet not in TransactionalRepairSignalHistory:
        TransactionalRepairSignalHistory.append(CurrentRepairSignalSet)
    EffectiveRepairClusterCount = int(Result.Diagnostics.get('RepairClusterCount', RepairClusterCount))
    CandidateDiagnostics['__PlacementRecipe__'] = {**SourceRecipe, 'SourceGenerator': 'transactional-cluster-endpoint-repair', 'AssignmentCutFingerprint': Context.CurrentPlacementAssignmentCut.ConflictFingerprint if Context.CurrentPlacementAssignmentCut is not None else '', 'AssignmentConstraintFingerprint': Context.PlacementAssignmentConstraints.Fingerprint, 'JointPortfolioIdentityFingerprint': PortfolioIdentityFingerprint, 'IsPostPinBankRepairEpoch': True, 'EnableInternalPinBankGeometryRepair': True, 'InternalPinBankGeometryRepairSignals': sorted(RepairSignals), 'TransactionalRepairSignalHistory': TransactionalRepairSignalHistory, 'RequiredDistinctPinBankOwnershipFingerprint': '' if AllowStableMandatoryAccessOwnership else SourceCandidate.TopologyDemand.MandatoryAccessOwnershipFingerprint, 'ReusedPlacedGeometry': True, 'TransactionalClusterEndpointRepair': True, 'TransactionalRepairClusterCount': EffectiveRepairClusterCount}
    Candidate.Placed.LocalRouteDiagnostics = CandidateDiagnostics
    ApplyActivePlacementAssignmentConstraints(Candidate, Context.PlacementAssignmentConstraints)
    _AppliedProfile, CandidateProfileFingerprint = ApplyCoordinatedCandidateDiversificationProfile(Candidate, RepairSignals)
    Fingerprint = BuildPlacementFingerprint(Candidate, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=False)
    RetentionFingerprint = BuildPlacementRetentionFingerprint(Candidate, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=False)
    if Fingerprint in Context.UniquePlacements or RetentionFingerprint in Context.RetainedPlacementTopologyFingerprints or (not TransactionalEndpointRepairIdentityIsFresh(Fingerprint, RetentionFingerprint, Context.SeenTransactionalEndpointRepairFingerprints, Context.SeenTransactionalEndpointRepairRetentionFingerprints)) or (Fingerprint in Context.RejectedPlacementFingerprints) or (RetentionFingerprint in Context.RejectedPlacementRetentionFingerprints):
        Context.PlacementGenerationDecisions.append({'Result': 'transactional-cluster-endpoint-repair-rejected', 'CandidateId': SourceCandidate.CandidateId, 'Signals': sorted(RepairSignals), 'Diagnostics': {**Result.Diagnostics, 'Reason': 'duplicate-or-rejected-identity', 'PlacementFingerprint': Fingerprint, 'PlacementRetentionFingerprint': RetentionFingerprint}, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
        return False
    try:
        CandidateResources = Context.Services.BuildRoutingResources(Candidate.Placed, WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('TransactionalClusterEndpointResourceMaterialization', {'CandidateId': SourceCandidate.CandidateId, **Diagnostics}))
    except (RoutingStageError, ValueError) as Error:
        Context.PlacementGenerationDecisions.append({'Result': 'transactional-cluster-endpoint-repair-rejected', 'CandidateId': SourceCandidate.CandidateId, 'Signals': sorted(RepairSignals), 'Diagnostics': {**Result.Diagnostics, 'Reason': 'resource-materialization-rejected', 'Validation': str(Error)}, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
        return False
    Context.UniquePlacements[Fingerprint] = ('transactional-cluster-endpoint-repair', SourceCandidate.RoutingSpacing, Candidate)
    Context.PlacementRetentionFingerprintByFingerprint[Fingerprint] = RetentionFingerprint
    Context.RetainedPlacementTopologyFingerprints[RetentionFingerprint] = (Fingerprint, 'transactional-cluster-endpoint-repair')
    Context.TopologyDemandByFingerprint[Fingerprint] = CandidateTopologyDemand
    Context.RoutingResourcesByFingerprint[Fingerprint] = CandidateResources
    Context.MaterializedPlacementByFingerprint[Fingerprint] = Candidate
    Context.SeenTransactionalEndpointRepairFingerprints.add(Fingerprint)
    Context.SeenTransactionalEndpointRepairRetentionFingerprints.add(RetentionFingerprint)
    Context.PendingJointPlacementStates.clear()
    Context.PendingTopologyCutEpoch = None
    Context.NeedsFeedbackPlacementGeneration = False
    Context.InternalPinBankGeometryRepairActive = False
    Context.RequiredDistinctPinBankOwnershipFingerprint = ''
    Context.PlacementGenerationDecisions.append({'Result': 'transactional-cluster-endpoint-repair-published', 'CandidateId': f'Placement-{Fingerprint[:12]}', 'SourceCandidateId': SourceCandidate.CandidateId, 'Signals': sorted(RepairSignals), 'RepairVariant': RepairVariant, 'RequestedRepairClusterCount': RepairClusterCount, 'RepairClusterCount': EffectiveRepairClusterCount, 'PlacementFingerprint': Fingerprint, 'PlacementRetentionFingerprint': RetentionFingerprint, 'CandidateProfileFingerprint': CandidateProfileFingerprint, 'JointPortfolioIdentityFingerprint': PortfolioIdentityFingerprint, 'MandatoryAccessOwnershipFingerprint': CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, 'StableMandatoryAccessOwnership': StableMandatoryAccessOwnership, 'Diagnostics': Result.Diagnostics, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6), 'NextAction': 'route-access-distinct-local-eco'})
    Context.JointPlacementStateEvents.append({'Status': 'transactional-cluster-endpoint-repair-published', 'CandidateId': f'Placement-{Fingerprint[:12]}', 'SourceCandidateId': SourceCandidate.CandidateId, 'RepairVariant': RepairVariant, 'ChangedGateCount': Result.Diagnostics.get('ChangedGateCount', 0), 'InvalidatedSignals': Result.Diagnostics.get('InvalidatedSignals', ()), 'PreservedLocalClaimCount': Result.Diagnostics.get('PreservedLocalClaimCount', 0), 'GlobalEnvelopePreserved': True})
    return True

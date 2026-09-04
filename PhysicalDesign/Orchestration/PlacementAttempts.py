"""Importable placement-flow helpers with explicit run state."""

from __future__ import annotations

from dataclasses import replace
import os
import traceback
from typing import Any
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Placement.Engine.Constraints import PlacementAssignmentConstraintSet
from PhysicalDesign.Placement.Access.Capacity import FixedPlacementPinAccessStatus
from PhysicalDesign.Placement.Engine.MandatoryAccess import MeasureMandatoryAccessConflictProfile, SolveFixedPlacementMandatoryAccess
from .Demand import ApplyJointPlacementPortfolioTrigger, BuildPlacementFailureHistorySnapshot, ExactStatePlacementEvaluation, MeasurePlacementTopologyDemand, ResolveJointPlacementPortfolioTrigger
from .Feedback import BuildPlacementFingerprint, BuildStructuredPlacementRelocationSignals, BuildTopologyCutEpochGeometryConstraints, BuildTopologyCutEpochGeometryRelocationSignals, BuildTopologyCutEpochPinBankRelocationSignals, RequiresImmediateAssignmentCutRelocation, SelectCutDrivenClusterRefinementSignals, SelectTopologyCutFrontier, ShouldUseCurrentAssignmentCutGeometry
from .Portfolios import AddMandatoryAccessPortfolioPairwiseConstraints, AssignmentCutHasBoundedExactCore, BuildMandatoryAccessPairwiseEdges, BuildMandatoryAccessPortfolioExpectedCandidateIndices, BuildMandatoryAccessPortfolioRecipeIdentity, BuildPendingJointPlacementPortfolioFingerprint, BuildPendingJointPlacementPortfolioIdentity, BuildPendingJointPlacementStateKey, BuildTargetedPinBankPackingPolicy, BuildTopologyCutEpochIdentity, EvaluateCompleteMandatoryAccessPortfolio, HasTopologyCutEpochRoutingReserve, MandatoryAccessPortfolioEvidence, MandatoryAccessPortfolioIdentity, MandatoryAccessPortfolioIdentityMatchesCurrent, MandatoryAccessPortfolioRejection, PendingJointPlacementState, PinBankRepairOwnershipIsDistinct, PlacementAssignmentConstraintsAreActive, PlacementGenerationRequest, PlacementGenerationRoutingReserveSeconds, ShouldOpenStrongMandatoryAccessRepair, ShouldPrioritizeCurrentExactCutBeforeBroad, ShouldPrioritizePlacementConflictRelocation, ShouldPrioritizeTopologyCutEpochRelocation, ShouldRejectCutBoundaryEscapePlacement, ShouldUseMandatoryAccessPreScreen, ShouldWidenTopologyCutTerminalShell, TopologyCutEpochAdmissionReserveSeconds
from .Preparation import BuildPlacementRelocationVariant, BuildPlacementRetentionFingerprint, IsDerivedSingleComponentPlacementSource, PrepareDerivedPlacementForFrozenAccessContract, ShouldEnableClusterBoundaryLeaseInterface, UsesDerivedPerimeterTerminals
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
    _PlacementFlowDefault,
)
from .AttemptHistory import (
    _PackedGateArea,
    _PlacementFailureWithHistory,
)


def _TryPlacement(Context, Request: PlacementGenerationRequest, JointPlacementCandidateIndex: int=0, FixedRelocationVariant: int | None=None, FixedCandidateSpacing: int | None=None, FixedRelocationSignals: frozenset[str] | None=None, FixedRelocationPrioritySignals: frozenset[str] | None=None, FixedRequiredRelocationSignals: frozenset[str] | None=None, FixedAssignmentCut: object=_PlacementFlowDefault, FixedAssignmentConstraints: object=_PlacementFlowDefault, FixedCoordinatedCandidateDiversificationSignals: object=_PlacementFlowDefault, FixedTopologyCutFrontier: object=_PlacementFlowDefault, FixedPhysicalProofCoreSignals: frozenset[str]=frozenset(), FixedPhysicalProofFingerprint: str='', FixedConnectivityClusters: tuple[tuple[str, ...], ...]=(), MaterializeRoutingResources: bool=True, SkipMandatoryAccessPreScreen: bool=False, PlacementGenerationNotAfter: float | None=None, CountPlacementGenerationAttempt: bool=True, QueueRetainedJointPortfolioStates: bool=True, UseCompletePlacementGenerationBudget: bool=False, AllowCapacityPairRepair: bool=False) -> bool:
    if FixedAssignmentCut is _PlacementFlowDefault:
        FixedAssignmentCut = Context.AssignmentCutUnspecified
    if FixedAssignmentConstraints is _PlacementFlowDefault:
        FixedAssignmentConstraints = Context.AssignmentCutUnspecified
    if FixedCoordinatedCandidateDiversificationSignals is _PlacementFlowDefault:
        FixedCoordinatedCandidateDiversificationSignals = Context.AssignmentCutUnspecified
    if FixedTopologyCutFrontier is _PlacementFlowDefault:
        FixedTopologyCutFrontier = Context.AssignmentCutUnspecified
    EffectiveAssignmentCut = Context.CurrentPlacementAssignmentCut if FixedAssignmentCut is Context.AssignmentCutUnspecified else FixedAssignmentCut
    EffectiveAssignmentConstraints = Context.PlacementAssignmentConstraints if FixedAssignmentConstraints is Context.AssignmentCutUnspecified else FixedAssignmentConstraints
    if EffectiveAssignmentCut is not None and (not isinstance(EffectiveAssignmentCut, RoutingAssignmentCut)):
        raise TypeError('FixedAssignmentCut must be RoutingAssignmentCut or None')
    if not isinstance(EffectiveAssignmentConstraints, PlacementAssignmentConstraintSet):
        raise TypeError('FixedAssignmentConstraints must be PlacementAssignmentConstraintSet')
    EffectiveAssignmentCutFingerprint = EffectiveAssignmentCut.ConflictFingerprint if EffectiveAssignmentCut is not None else ''
    EffectiveAssignmentConstraintFingerprint = EffectiveAssignmentConstraints.Fingerprint
    EffectiveCoordinatedCandidateDiversificationSignals = Context.PlacementCoordinatedCandidateDiversificationSignals if FixedCoordinatedCandidateDiversificationSignals is Context.AssignmentCutUnspecified else FixedCoordinatedCandidateDiversificationSignals
    if not isinstance(EffectiveCoordinatedCandidateDiversificationSignals, frozenset):
        raise TypeError('FixedCoordinatedCandidateDiversificationSignals must be a frozenset')
    EnableCurrentClusterLocalRouteReuse = bool(Context.PlacementClusterPinBankRepairSignals) and Context.PlacementClusterPinBankRepairSignals.issubset(EffectiveCoordinatedCandidateDiversificationSignals)
    Request = ApplyJointPlacementPortfolioTrigger(Request, Context.JointPortfolioTriggered)
    SourceGenerator = Request.SourceGenerator
    if Request.TerminalLayoutVariantIndex < 0:
        raise ValueError('terminal layout variant index cannot be negative')
    if Request.TerminalLayoutVariantIndex and (not UsesDerivedPerimeterTerminals(SourceGenerator)):
        raise ValueError('terminal layout variants require a derived perimeter placement source')
    if Request.GraphCoreCandidateIndex is not None:
        if Request.GraphCoreCandidateIndex < 0:
            raise ValueError('graph-core candidate index cannot be negative')
        if JointPlacementCandidateIndex != 0:
            raise ValueError('an explicit graph-core request cannot also carry a joint-placement candidate index')
        JointPlacementCandidateIndex = Request.GraphCoreCandidateIndex
    StrongMandatoryAccessRepair = bool(Context.StrongMandatoryAccessRepairMaterializationPending and SourceGenerator == 'row-beam-conflict-relocation' and (JointPlacementCandidateIndex == 0))
    if JointPlacementCandidateIndex == 0 and CountPlacementGenerationAttempt and (Context.PlacementGenerationAttempts >= Context.GenerationPlan.MaximumAttempts) and (not StrongMandatoryAccessRepair) and (not AllowCapacityPairRepair):
        return False
    if JointPlacementCandidateIndex == 0 and CountPlacementGenerationAttempt:
        Context.PlacementGenerationAttempts += 1
    if FixedRelocationVariant is not None:
        RelocationVariant = FixedRelocationVariant
        RelocationSpacingLevel = 0
    elif StrongMandatoryAccessRepair:
        Context.StrongMandatoryAccessRepairMaterializationPending = False
        RelocationVariant = 12
        RelocationSpacingLevel = 0
        Context.RelocationGenerationCount = max(Context.RelocationGenerationCount, 2)
        Context.TotalRelocationGenerationCount += 1
    elif SourceGenerator == 'row-beam-conflict-relocation':
        RelocationInputsChanged = Context.PlacementRelocationSignals != Context.LastRelocationSignalsUsed or Context.PlacementRelocationPrioritySignals != Context.LastRelocationPrioritySignalsUsed or Context.PlacementRequiredRelocationSignals != Context.LastRequiredRelocationSignalsUsed or (EffectiveAssignmentCutFingerprint != Context.LastAssignmentCutFingerprintUsed) or (EffectiveAssignmentConstraintFingerprint != Context.LastAssignmentConstraintFingerprintUsed)
        if RelocationInputsChanged:
            Context.RelocationGenerationCount = 0
        Context.LastRelocationSignalsUsed = Context.PlacementRelocationSignals
        Context.LastRelocationPrioritySignalsUsed = Context.PlacementRelocationPrioritySignals
        Context.LastRequiredRelocationSignalsUsed = Context.PlacementRequiredRelocationSignals
        Context.LastAssignmentCutFingerprintUsed = EffectiveAssignmentCutFingerprint
        Context.LastAssignmentConstraintFingerprintUsed = EffectiveAssignmentConstraintFingerprint
        RelocationVariant = BuildPlacementRelocationVariant(RelocationGenerationCount=Context.RelocationGenerationCount, ReconvergentAccessPressure=Context.TopologyPressure.ReconvergentAccessPressure)
        Context.RelocationGenerationCount += 1
        RelocationSpacingLevel = min(Context.TotalRelocationGenerationCount, Context.Policy.Placement.RoutingSpacingAlternatives)
        if Context.Policy.NegotiatedRouting.Enabled:
            RelocationSpacingLevel = 0
        if Context.ConfiguredRoutingSpacing > Context.Policy.Placement.RoutingSpacing:
            RelocationSpacingLevel = 0
        Context.TotalRelocationGenerationCount += 1
    else:
        RelocationVariant = 0
        RelocationSpacingLevel = 0
    UseCurrentCutGeometry = ShouldUseCurrentAssignmentCutGeometry(Request.UseCurrentAssignmentCutRelocationSignals, SourceGenerator, EffectiveAssignmentCut)
    if UseCurrentCutGeometry:
        EffectiveRelocationSignals = FixedRelocationSignals if FixedRelocationSignals is not None else BuildTopologyCutEpochPinBankRelocationSignals(BuildTopologyCutEpochGeometryRelocationSignals(EffectiveAssignmentCut, Context.PlacementRepeatedLeaseGeometrySignals), Context.PlacementClusterPinBankRepairSignals, Context.InternalPinBankGeometryRepairActive)
        GeometryAssignmentConstraints = BuildTopologyCutEpochGeometryConstraints(EffectiveAssignmentCut, EffectiveAssignmentConstraints)
    else:
        BaseRelocationSignals = Context.PlacementRelocationSignals if FixedRelocationSignals is None else FixedRelocationSignals
        EffectiveRelocationSignals = frozenset((*BaseRelocationSignals, *BuildStructuredPlacementRelocationSignals(EffectiveAssignmentCut, EffectiveAssignmentConstraints)))
        GeometryAssignmentConstraints = EffectiveAssignmentConstraints
    EffectiveTopologyCutFrontier = SelectTopologyCutFrontier(EffectiveAssignmentCut, Context.PlacementAssignmentCutHistory, Enabled=UseCurrentCutGeometry and Context.TopologyDemand.RequiresJointPortfolio) if FixedTopologyCutFrontier is Context.AssignmentCutUnspecified else FixedTopologyCutFrontier
    if not isinstance(EffectiveTopologyCutFrontier, tuple) or any((not isinstance(Cut, RoutingAssignmentCut) for Cut in EffectiveTopologyCutFrontier)):
        raise TypeError('FixedTopologyCutFrontier must be a tuple of RoutingAssignmentCut values')
    EffectiveRelocationPrioritySignals = Context.PlacementRelocationPrioritySignals if FixedRelocationPrioritySignals is None else FixedRelocationPrioritySignals
    EffectiveRequiredRelocationSignals = Context.PlacementRequiredRelocationSignals if FixedRequiredRelocationSignals is None else FixedRequiredRelocationSignals
    EffectiveRelocationPrioritySignals = BuildTopologyCutEpochPinBankRelocationSignals(EffectiveRelocationPrioritySignals, Context.PlacementClusterPinBankRepairSignals, Context.InternalPinBankGeometryRepairActive)
    EffectiveRequiredRelocationSignals = BuildTopologyCutEpochPinBankRelocationSignals(EffectiveRequiredRelocationSignals, Context.PlacementClusterPinBankRepairSignals, Context.InternalPinBankGeometryRepairActive)
    EffectiveInternalPinBankGeometryRepairSignals = Context.PlacementClusterPinBankRepairSignals if Context.InternalPinBankGeometryRepairActive else frozenset()
    GeometryAssignmentCut = EffectiveAssignmentCut
    if SourceGenerator == 'row-beam-direct-only' and (not Context.TopologyDemand.RequiresJointPortfolio):
        GeometryAssignmentCut = None
    CandidateSpacing = FixedCandidateSpacing if FixedCandidateSpacing is not None else Request.RoutingSpacing + RelocationSpacingLevel
    CandidatePacking = Request.PackingPolicy
    CandidatePlacementPolicy = Context.Policy.Placement
    JointPortfolioState = PendingJointPlacementState(Request=Request, CandidateIndex=JointPlacementCandidateIndex, RelocationVariant=RelocationVariant, RoutingSpacing=CandidateSpacing, RelocationSignals=EffectiveRelocationSignals, RelocationPrioritySignals=EffectiveRelocationPrioritySignals, RequiredRelocationSignals=EffectiveRequiredRelocationSignals, AssignmentCut=GeometryAssignmentCut, AssignmentConstraints=GeometryAssignmentConstraints, CoordinatedCandidateDiversificationSignals=EffectiveCoordinatedCandidateDiversificationSignals, EnableClusterLocalRouteReuse=EnableCurrentClusterLocalRouteReuse, IsPostPinBankRepairEpoch=Context.PostPinBankRepairEpochActive, EnableInternalPinBankGeometryRepair=Context.InternalPinBankGeometryRepairActive, InternalPinBankGeometryRepairSignals=EffectiveInternalPinBankGeometryRepairSignals, RequiredDistinctPinBankOwnershipFingerprint=Context.RequiredDistinctPinBankOwnershipFingerprint, TopologyCutFrontier=EffectiveTopologyCutFrontier, PhysicalProofCoreSignals=FixedPhysicalProofCoreSignals, PhysicalProofFingerprint=FixedPhysicalProofFingerprint, FixedConnectivityClusters=FixedConnectivityClusters)
    JointPortfolioIdentity = BuildPendingJointPlacementPortfolioIdentity(JointPortfolioState)
    JointPortfolioIdentityFingerprint = BuildPendingJointPlacementPortfolioFingerprint(JointPortfolioState) if CandidatePacking.EnableJointClusterOrientation else ''
    if CandidatePacking.EnableJointClusterOrientation and PlacementGenerationNotAfter is not None:
        Context.JointPortfolioGenerationNotAfterByIdentity.setdefault(JointPortfolioIdentity, PlacementGenerationNotAfter)
    PlacementStarted = Context.Services.monotonic()
    IsDeferredRequest = Request in Context.GenerationPlan.DeferredRequests
    RemainingGenerationSlots = max(1, Context.GenerationPlan.MaximumAttempts - Context.PlacementGenerationAttempts + 1) if IsDeferredRequest else max(1, len(Context.GenerationPlan.PrimaryRequests) - Context.PlacementGenerationAttempts + 1)
    if PlacementGenerationNotAfter is None:
        RoutingReserveSeconds = min(PlacementGenerationRoutingReserveSeconds(Context.Policy, Context.DenseBoundaryRoutingReserve), max(0.01, Context.Deadline.RemainingSeconds() * 0.5))
        AvailableGenerationSeconds = max(0.0, Context.Deadline.RemainingSeconds() - RoutingReserveSeconds)
    else:
        RoutingReserveSeconds = max(0.0, Context.Deadline.ExpiresAt - PlacementGenerationNotAfter)
        AvailableGenerationSeconds = max(0.0, min(Context.Deadline.ExpiresAt, PlacementGenerationNotAfter) - PlacementStarted)
    if AvailableGenerationSeconds <= 0:
        Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'skipped-routing-reserve', 'RoutingReserveSeconds': round(RoutingReserveSeconds, 6), 'RemainingSeconds': round(Context.Deadline.RemainingSeconds(), 6), 'PlacementAttempts': list(Context.PlacementAttemptFailures)})
        if not Context.UniquePlacements:
            Context.LastStructuredPlacementFailure = RoutingFailure(Reason=RoutingFailureReason.Stagnated, Stage='PlacementGeneration', Detail='placement generation reached the routing reserve before producing an exact-legal candidate', RepairActions=('AdvancePlacementGenerator',), Diagnostics={'SourceGenerator': SourceGenerator, 'RoutingReserveSeconds': RoutingReserveSeconds, 'PlacementAttempts': Context.PlacementAttemptFailures})
            FailureSnapshot = BuildPlacementFailureHistorySnapshot(Context.LastStructuredPlacementFailure)
            Context.PlacementGenerationFailures.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'PackedNandPlacement': bool(CandidatePacking.Enabled), 'Failure': Context.LastStructuredPlacementFailure.Detail, 'PlacementGenerationBudgetSeconds': 0.0, 'ElapsedSeconds': 0.0, 'Diagnostics': FailureSnapshot})
        return False
    PlacementGenerationBudgetSeconds = max(0.001, AvailableGenerationSeconds) if UseCompletePlacementGenerationBudget else max(0.001, AvailableGenerationSeconds / RemainingGenerationSlots)
    if SourceGenerator == 'row-beam-conflict-relocation' and (not UseCompletePlacementGenerationBudget):
        PlacementGenerationBudgetSeconds = max(PlacementGenerationBudgetSeconds, min(36.0, AvailableGenerationSeconds))
    PlacementGenerationExpiresAt = min(Context.Deadline.ExpiresAt, PlacementStarted + PlacementGenerationBudgetSeconds, PlacementGenerationNotAfter if PlacementGenerationNotAfter is not None else Context.Deadline.ExpiresAt)
    DebugPlacementPhase = [None]
    DebugPlacementPhaseStarted = [Context.Services.monotonic()]

    def CheckPlacementGeneration(Diagnostics: dict[str, object]) -> None:
        Current = Context.Services.monotonic()
        Phase = Diagnostics.get('Phase')
        if bool(os.environ.get('RCS_DEBUG_PLACEMENT_PHASES')) and Phase in {'start', 'connectivity-clusters', 'cluster-slots', 'vertical-stacking-start', 'localized-terminal', 'localized-terminal-search-complete', 'terminal-placement-complete', 'local-access-geometry', 'complete', 'placement-construction-complete', 'exact-isolation-complete'} and (Phase != DebugPlacementPhase[0]):
            print(f'[debug] placement phase previous={DebugPlacementPhase[0]} elapsed={Current - DebugPlacementPhaseStarted[0]:.6f}s next={Phase} diagnostics={Diagnostics}', flush=True)
            DebugPlacementPhase[0] = Phase
            DebugPlacementPhaseStarted[0] = Current
        if Current < Context.Deadline.ExpiresAt and Current < PlacementGenerationExpiresAt:
            return
        FailureDiagnostics = {'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'PlacementGenerationAttempt': Context.PlacementGenerationAttempts, 'MaximumPlacementGenerationAttempts': Context.GenerationPlan.MaximumAttempts, 'PlacementGenerationFailures': Context.PlacementGenerationFailures, 'PlacementGenerationDecisions': Context.PlacementGenerationDecisions, 'PlacementAttempts': Context.PlacementAttemptFailures, 'PlacementGenerationDeadline': {'RuntimeBudgetSeconds': round(PlacementGenerationBudgetSeconds, 6), 'ElapsedSeconds': round(Current - PlacementStarted, 6), 'Expired': Current >= PlacementGenerationExpiresAt, 'LimitedByGlobalDeadline': PlacementGenerationExpiresAt >= Context.Deadline.ExpiresAt, 'RoutingReserveSeconds': round(RoutingReserveSeconds, 6)}, **Diagnostics}
        Context.Deadline.RaiseIfExpired('Placement', FailureDiagnostics)
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.Stagnated, Stage='PlacementGeneration', Detail='per-candidate placement generation slice expired; advance to the next deterministic generator', RepairActions=('AdvancePlacementGenerator',), Diagnostics=FailureDiagnostics))
    Fingerprint: str | None = None
    RetentionFingerprint: str | None = None

    def MandatoryConflictMap(Profile: Any) -> dict[object, set[str]]:
        Result: dict[object, set[str]] = {}
        for Resource, Owners in (*Profile.CrossConflicts, *Profile.SelfConflicts):
            Result.setdefault(Resource, set()).update(map(str, Owners))
        return Result
    PhysicalProofCoreFocusedPlacement = bool(FixedPhysicalProofCoreSignals)
    CutDrivenClusterRefinementSignals = (
        FixedPhysicalProofCoreSignals
        if PhysicalProofCoreFocusedPlacement
        else SelectCutDrivenClusterRefinementSignals(
            GeometryAssignmentCut,
            Context.SignalTopologyFingerprints,
            Constraints=GeometryAssignmentConstraints,
        )
        if Context.TopologyDemand.RequiresJointPortfolio
        else None
    )
    FocusedCutEpochPlacement = bool(
        Request.UseCurrentAssignmentCutRelocationSignals
        or PhysicalProofCoreFocusedPlacement
    )
    try:
        CheckPlacementGeneration({'Phase': 'placement-generation-start'})
        UseMandatoryAccessPreScreen = ShouldUseMandatoryAccessPreScreen(SourceGenerator=SourceGenerator, PackingEnabled=CandidatePacking.Enabled, JointOrientationEnabled=CandidatePacking.EnableJointClusterOrientation, HasRelocationSignals=bool(EffectiveRelocationSignals), TopologyRequiresJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio, HasAssignmentCut=EffectiveAssignmentCut is not None, AssignmentConstraintsActive=PlacementAssignmentConstraintsAreActive(GeometryAssignmentConstraints)) and (not SkipMandatoryAccessPreScreen)
        Candidate = Context.Services.PlacePcbGraph(Context.Netlist, RoutingSpacing=CandidateSpacing, PlacementPolicy=CandidatePlacementPolicy, ClusterPolicy=Context.Policy.Clustering, MaximumBoundaryTerminals=Context.Policy.Organization.MaximumClusterEntrances, MaximumEntrancesPerSignal=Context.Policy.Organization.MaximumClusterEntrancesPerSignal, PackingPolicy=CandidatePacking, RelocationSignals=EffectiveRelocationSignals, RelocationPrioritySignals=EffectiveRelocationPrioritySignals, RequiredRelocationSignals=EffectiveRequiredRelocationSignals, RelocationVariant=RelocationVariant, JointPlacementCandidateIndex=JointPlacementCandidateIndex, AssignmentCut=GeometryAssignmentCut, AssignmentConstraints=GeometryAssignmentConstraints, CoordinatedCandidateDiversificationSignals=EffectiveCoordinatedCandidateDiversificationSignals, MandatoryAccessPreScreenOnly=UseMandatoryAccessPreScreen, PlacementScoringOnly=CandidatePacking.EnableJointClusterOrientation and Context.TopologyDemand.RequiresJointPortfolio, PreferAccessRingTerminals=IsDerivedSingleComponentPlacementSource(SourceGenerator), UseDerivedPerimeterTerminals=UsesDerivedPerimeterTerminals(SourceGenerator), DerivedTerminalLayoutVariantIndex=Request.TerminalLayoutVariantIndex, EnableClusterBoundaryLeases=ShouldEnableClusterBoundaryLeaseInterface(ScaleGeometryPressure=Context.TopologyPressure.ScaleGeometryPressure, TopologyRequiresJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio), EnableClusterInterfacePlacementFeasibility=Context.TopologyDemand.RequiresJointPortfolio, CutDrivenClusterRefinementSignals=CutDrivenClusterRefinementSignals, FixedConnectivityClusters=FixedConnectivityClusters, EnableInternalPinBankGeometryRepair=JointPortfolioState.EnableInternalPinBankGeometryRepair, InternalPinBankGeometryRepairSignals=JointPortfolioState.InternalPinBankGeometryRepairSignals, FocusedCutEpochPlacement=FocusedCutEpochPlacement, TopologyCutFrontier=JointPortfolioState.TopologyCutFrontier, WorkCheck=CheckPlacementGeneration)
        PreScreenMandatoryProfile = (Candidate.MandatoryAccessPreScreenProfile if Candidate.MandatoryAccessPreScreenProfile is not None else MeasureMandatoryAccessConflictProfile(Candidate.Placed.PlacedGates, Candidate.SignalOrder, WorkCheck=CheckPlacementGeneration)) if UseMandatoryAccessPreScreen else None
        PreScreenMandatoryConflicts = MandatoryConflictMap(PreScreenMandatoryProfile) if PreScreenMandatoryProfile is not None else None
        if UseMandatoryAccessPreScreen and (not PreScreenMandatoryConflicts):
            Candidate = Context.Services.PlacePcbGraph(Context.Netlist, RoutingSpacing=CandidateSpacing, PlacementPolicy=CandidatePlacementPolicy, ClusterPolicy=Context.Policy.Clustering, MaximumBoundaryTerminals=Context.Policy.Organization.MaximumClusterEntrances, MaximumEntrancesPerSignal=Context.Policy.Organization.MaximumClusterEntrancesPerSignal, PackingPolicy=CandidatePacking, RelocationSignals=EffectiveRelocationSignals, RelocationPrioritySignals=EffectiveRelocationPrioritySignals, RequiredRelocationSignals=EffectiveRequiredRelocationSignals, RelocationVariant=RelocationVariant, JointPlacementCandidateIndex=JointPlacementCandidateIndex, AssignmentCut=GeometryAssignmentCut, AssignmentConstraints=GeometryAssignmentConstraints, CoordinatedCandidateDiversificationSignals=EffectiveCoordinatedCandidateDiversificationSignals, PlacementScoringOnly=CandidatePacking.EnableJointClusterOrientation and Context.TopologyDemand.RequiresJointPortfolio, PreferAccessRingTerminals=IsDerivedSingleComponentPlacementSource(SourceGenerator), UseDerivedPerimeterTerminals=UsesDerivedPerimeterTerminals(SourceGenerator), DerivedTerminalLayoutVariantIndex=Request.TerminalLayoutVariantIndex, EnableClusterBoundaryLeases=ShouldEnableClusterBoundaryLeaseInterface(ScaleGeometryPressure=Context.TopologyPressure.ScaleGeometryPressure, TopologyRequiresJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio), EnableClusterInterfacePlacementFeasibility=Context.TopologyDemand.RequiresJointPortfolio, CutDrivenClusterRefinementSignals=CutDrivenClusterRefinementSignals, FixedConnectivityClusters=FixedConnectivityClusters, EnableInternalPinBankGeometryRepair=JointPortfolioState.EnableInternalPinBankGeometryRepair, InternalPinBankGeometryRepairSignals=JointPortfolioState.InternalPinBankGeometryRepairSignals, FocusedCutEpochPlacement=FocusedCutEpochPlacement, TopologyCutFrontier=JointPortfolioState.TopologyCutFrontier, WorkCheck=CheckPlacementGeneration)
            PreScreenMandatoryProfile = None
            PreScreenMandatoryConflicts = None
        if IsDerivedSingleComponentPlacementSource(SourceGenerator):
            Candidate = PrepareDerivedPlacementForFrozenAccessContract(Candidate)
        RecipeDiagnostics = dict(Candidate.Placed.LocalRouteDiagnostics or {})
        RecipeDiagnostics['__PlacementRecipe__'] = {'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Packed': bool(CandidatePacking.Enabled), 'GraphCoreCandidateIndex': Request.GraphCoreCandidateIndex, 'TerminalLayoutVariantIndex': Request.TerminalLayoutVariantIndex, 'AssignmentCutFingerprint': EffectiveAssignmentCutFingerprint, 'AssignmentConstraintFingerprint': EffectiveAssignmentConstraintFingerprint, 'JointPortfolioIdentityFingerprint': JointPortfolioIdentityFingerprint, 'IsPostPinBankRepairEpoch': Context.PostPinBankRepairEpochActive, 'EnableInternalPinBankGeometryRepair': Context.InternalPinBankGeometryRepairActive, 'RequiredDistinctPinBankOwnershipFingerprint': Context.RequiredDistinctPinBankOwnershipFingerprint}
        Candidate.Placed.LocalRouteDiagnostics = RecipeDiagnostics
        JointDiagnostics = dict(RecipeDiagnostics.get('__JointClusterPlacement__', {}))

        def QueueRetainedJointStates() -> None:
            if not (QueueRetainedJointPortfolioStates and JointPlacementCandidateIndex == 0 and CandidatePacking.Enabled and CandidatePacking.EnableJointClusterOrientation):
                return
            RetainedStates = JointDiagnostics.get('ExactLegalRetainedStates', JointDiagnostics.get('RetainedStates', ()))
            QueuedStates = [State for State in RetainedStates if int(State['CandidateIndex']) != JointPlacementCandidateIndex][:max(0, CandidatePacking.RetainedJointPlacementCandidates * (2 if Context.TopologyDemand.RequiresJointPortfolio else 1) - 1)]
            CandidateStates = [PendingJointPlacementState(Request=Request, CandidateIndex=int(State['CandidateIndex']), RelocationVariant=RelocationVariant, RoutingSpacing=CandidateSpacing, RelocationSignals=EffectiveRelocationSignals, RelocationPrioritySignals=EffectiveRelocationPrioritySignals, RequiredRelocationSignals=EffectiveRequiredRelocationSignals, AssignmentCut=EffectiveAssignmentCut, AssignmentConstraints=EffectiveAssignmentConstraints, CoordinatedCandidateDiversificationSignals=EffectiveCoordinatedCandidateDiversificationSignals, EnableClusterLocalRouteReuse=EnableCurrentClusterLocalRouteReuse, IsPostPinBankRepairEpoch=Context.PostPinBankRepairEpochActive, EnableInternalPinBankGeometryRepair=Context.InternalPinBankGeometryRepairActive, InternalPinBankGeometryRepairSignals=EffectiveInternalPinBankGeometryRepairSignals, RequiredDistinctPinBankOwnershipFingerprint=Context.RequiredDistinctPinBankOwnershipFingerprint, TopologyCutFrontier=EffectiveTopologyCutFrontier, PhysicalProofCoreSignals=FixedPhysicalProofCoreSignals, PhysicalProofFingerprint=FixedPhysicalProofFingerprint, FixedConnectivityClusters=FixedConnectivityClusters) for State in QueuedStates]
            ExistingStateKeys = {*Context.MaterializedJointPlacementStateKeys, *(BuildPendingJointPlacementStateKey(State) for State in Context.PendingJointPlacementStates)}
            NewCandidateStates = [State for State in CandidateStates if BuildPendingJointPlacementStateKey(State) not in ExistingStateKeys]
            Context.PendingJointPlacementStates.extend(NewCandidateStates)
            NewCandidateIndices = {State.CandidateIndex for State in NewCandidateStates}
            QueuedStates = [State for State in QueuedStates if int(State['CandidateIndex']) in NewCandidateIndices]
            Context.JointPlacementStateEvents.extend(({'CandidateIndex': int(State['CandidateIndex']), 'Status': 'queued', 'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Score': State.get('SearchScore'), 'Transforms': State.get('Transforms', {})} for State in QueuedStates))
            Context.PlacementGenerationDecisions.append({'Result': 'queued-joint-placement-states', 'SourceGenerator': SourceGenerator, 'JointPlacementCandidateIndex': JointPlacementCandidateIndex, 'QueuedCandidateIndices': [int(State['CandidateIndex']) for State in QueuedStates]})

        PackedGateArea = _PackedGateArea(Context, Candidate)
        if CandidatePacking.Enabled and SourceGenerator != 'row-beam-conflict-relocation' and (Context.BaselinePackedGateArea is None):
            Context.BaselinePackedGateArea = PackedGateArea
        MaximumPackedGateArea = int(Context.BaselinePackedGateArea * Context.Policy.NegotiatedRouting.MaximumPackedAreaGrowth * (1.1 if RelocationVariant >= 3 and Context.TopologyPressure.ReconvergentAccessPressure else 1.0)) if Context.BaselinePackedGateArea is not None else None
        if CandidatePacking.Enabled and MaximumPackedGateArea is not None and (PackedGateArea > MaximumPackedGateArea):
            QueueRetainedJointStates()
            Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'rejected-packed-area-growth', 'PackedGateArea': PackedGateArea, 'BaselinePackedGateArea': Context.BaselinePackedGateArea, 'MaximumPackedGateArea': MaximumPackedGateArea, 'QueuedCompactSiblingCount': len(Context.PendingJointPlacementStates), 'NextAction': 'materialize-next-retained-exact-state' if Context.PendingJointPlacementStates else 'advance-bounded-placement-generator'})
            return False
        CheckPlacementGeneration({'Phase': 'placement-construction-complete'})
        ExactStatePlacementCacheDiagnostics = dict(JointDiagnostics.get('ExactStatePlacementCache', {}))
        ExactStatePlacementCacheKey = str(ExactStatePlacementCacheDiagnostics.get('Key', ''))
        CachedExactStateEvaluation = Context.ExactStatePlacementEvaluationCache.get(ExactStatePlacementCacheKey) if ExactStatePlacementCacheKey else None
        if CachedExactStateEvaluation is None:
            Context.Services.ValidatePlacedCellElectricalIsolation(Candidate.Placed, WorkCheck=CheckPlacementGeneration)
            CheckPlacementGeneration({'Phase': 'exact-isolation-complete'})
        else:
            CheckPlacementGeneration({'Phase': 'exact-state-evaluation-cache-hit', 'ExactStatePlacementCacheKey': ExactStatePlacementCacheKey})
        CandidateResources = None
        MandatoryProfile = CachedExactStateEvaluation.MandatoryAccessProfile if CachedExactStateEvaluation is not None else (PreScreenMandatoryProfile if PreScreenMandatoryProfile is not None else MeasureMandatoryAccessConflictProfile(Candidate.Placed.PlacedGates, Candidate.SignalOrder, WorkCheck=CheckPlacementGeneration)) if CandidatePacking.Enabled and CandidatePacking.EnableProactiveInterClusterRelocation else None
        MandatoryConflicts = MandatoryConflictMap(MandatoryProfile) if MandatoryProfile is not None else {}
        if MandatoryConflicts:
            Context.JointPortfolioTriggered = ResolveJointPlacementPortfolioTrigger(Context.JointPortfolioTriggered, Context.TopologyDemand, MandatoryAccessConflictObserved=True)
        CandidateTopologyDemand = CachedExactStateEvaluation.TopologyDemand if CachedExactStateEvaluation is not None else MeasurePlacementTopologyDemand(Context.TopologyDemand, Candidate, MandatoryConflicts=MandatoryConflicts, MandatoryProfile=MandatoryProfile)
        if ExactStatePlacementCacheKey and CachedExactStateEvaluation is None:
            Context.ExactStatePlacementEvaluationCache[ExactStatePlacementCacheKey] = ExactStatePlacementEvaluation(MandatoryAccessProfile=MandatoryProfile, TopologyDemand=CandidateTopologyDemand)
        if ExactStatePlacementCacheDiagnostics:
            ExactStatePlacementCacheDiagnostics['EvaluationHit'] = CachedExactStateEvaluation is not None
            JointDiagnostics['ExactStatePlacementCache'] = ExactStatePlacementCacheDiagnostics
            RecipeDiagnostics['__JointClusterPlacement__'] = JointDiagnostics
        RecipeDiagnostics['__TopologyDemandProfile__'] = CandidateTopologyDemand.ToDictionary()
        Candidate.Placed.LocalRouteDiagnostics = RecipeDiagnostics

        if JointDiagnostics:
            ExactPreScreen = {'MandatoryAccessConflictResources': len(MandatoryConflicts), 'MandatoryAccessConflictSignals': sorted({Signal for Owners in MandatoryConflicts.values() for Signal in Owners}), 'BoundaryOverflow': sum((Cluster.BoundaryOverflow for Cluster in Candidate.PackedClusters)), 'PinScarcityCount': sum((Cluster.PinScarcityCount for Cluster in Candidate.PackedClusters)), 'LocalClaimCount': len(Candidate.Placed.LocalRouteClaims or ()), 'MandatoryAccessOwnershipFingerprint': CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, 'MandatoryAccessConflictFingerprint': CandidateTopologyDemand.MandatoryAccessConflictFingerprint, 'JointOrderKey': list(CandidateTopologyDemand.JointOrderKey), 'TopologyDemandProfile': CandidateTopologyDemand.ToDictionary(), 'MandatoryAccessProfile': MandatoryProfile.ToDictionary() if MandatoryProfile is not None else None}
            JointDiagnostics['ExactPreScreen'] = ExactPreScreen
            RecipeDiagnostics['__JointClusterPlacement__'] = JointDiagnostics
            Candidate.Placed.LocalRouteDiagnostics = RecipeDiagnostics
            Context.JointPlacementStateEvents.append({'CandidateIndex': JointPlacementCandidateIndex, 'Status': 'materialized-mandatory-access-conflict' if MandatoryConflicts else 'materialized-exact-legal', 'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Score': JointDiagnostics.get('SelectedScore'), 'Transforms': JointDiagnostics.get('SelectedTransforms', {}), 'ExactPreScreen': ExactPreScreen, 'MandatoryAccessOwnershipFingerprint': CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, 'MandatoryAccessConflictFingerprint': CandidateTopologyDemand.MandatoryAccessConflictFingerprint, 'JointOrderKey': list(CandidateTopologyDemand.JointOrderKey)})
        QueueRetainedJointStates()
        RequiredDistinctOwnership = JointPortfolioState.RequiredDistinctPinBankOwnershipFingerprint
        if not PinBankRepairOwnershipIsDistinct(RequiredDistinctOwnership, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint):
            Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'rejected-stagnant-pin-bank-ownership', 'JointPlacementCandidateIndex': JointPlacementCandidateIndex, 'RequiredDistinctPinBankOwnershipFingerprint': RequiredDistinctOwnership, 'ObservedPinBankOwnershipFingerprint': CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, 'InternalPinBankGeometryRepairSignals': sorted(JointPortfolioState.InternalPinBankGeometryRepairSignals), 'NextAction': 'materialize-next-retained-exact-state' if Context.PendingJointPlacementStates else 'advance-bounded-placement-generator'})
            return False
        CutBoundaryEscapeDiagnostics = RecipeDiagnostics.get('__CutBoundaryEscapeFeasibility__')
        if ShouldRejectCutBoundaryEscapePlacement(TopologyRequiresJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio, Diagnostics=CutBoundaryEscapeDiagnostics):
            assert isinstance(CutBoundaryEscapeDiagnostics, dict)
            Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'rejected-exact-cut-boundary-escape-infeasible', 'JointPlacementCandidateIndex': JointPlacementCandidateIndex, 'AssignmentCutFingerprint': EffectiveAssignmentCutFingerprint, 'AssignmentConstraintFingerprint': EffectiveAssignmentConstraintFingerprint, 'CutBoundaryEscapeFeasibility': dict(CutBoundaryEscapeDiagnostics), 'NextAction': 'materialize-next-retained-exact-state' if Context.PendingJointPlacementStates else 'advance-bounded-placement-generator'})
            return False
        MandatoryAccessPortfolioTracking: dict[str, object] | None = None
        MandatoryAccessPortfolioIdentityDiagnostics: dict[str, object] | None = None
        if MandatoryConflicts and MandatoryProfile is not None and JointDiagnostics:
            ExactScreenFingerprint = str(JointDiagnostics.get('ExactScreenFingerprint', ''))
            PortfolioIdentity = MandatoryAccessPortfolioIdentity(ExactScreenFingerprint=ExactScreenFingerprint, SourceGenerator=SourceGenerator, RoutingSpacing=CandidateSpacing, RelocationVariant=RelocationVariant, AssignmentCutFingerprint=EffectiveAssignmentCutFingerprint, AssignmentConstraintFingerprint=EffectiveAssignmentConstraintFingerprint, CoordinatedSignals=tuple(sorted(EffectiveCoordinatedCandidateDiversificationSignals)))
            MandatoryAccessPortfolioIdentityDiagnostics = {'ExactScreenFingerprint': PortfolioIdentity.ExactScreenFingerprint, 'SourceGenerator': PortfolioIdentity.SourceGenerator, 'RoutingSpacing': PortfolioIdentity.RoutingSpacing, 'RelocationVariant': PortfolioIdentity.RelocationVariant, 'AssignmentCutFingerprint': PortfolioIdentity.AssignmentCutFingerprint, 'AssignmentConstraintFingerprint': PortfolioIdentity.AssignmentConstraintFingerprint, 'CoordinatedSignals': list(PortfolioIdentity.CoordinatedSignals)}
            PortfolioRecipeIdentity = BuildMandatoryAccessPortfolioRecipeIdentity(PortfolioIdentity)
            PortfolioEvidence = Context.MandatoryAccessPortfolioEvidenceByIdentity.get(PortfolioIdentity) or Context.MandatoryAccessPortfolioEvidenceByRecipeIdentity.get(PortfolioRecipeIdentity)
            if PortfolioEvidence is None and JointPlacementCandidateIndex == 0 and ExactScreenFingerprint:
                ExpectedCandidateIndices = BuildMandatoryAccessPortfolioExpectedCandidateIndices(JointDiagnostics, JointPlacementCandidateIndex, CandidatePacking.RetainedJointPlacementCandidates)
                if ExpectedCandidateIndices:
                    PortfolioEvidence = MandatoryAccessPortfolioEvidence(ExpectedCandidateIndices=ExpectedCandidateIndices, RejectionsByCandidateIndex={})
                    Context.MandatoryAccessPortfolioEvidenceByIdentity[PortfolioIdentity] = PortfolioEvidence
                    Context.MandatoryAccessPortfolioEvidenceByRecipeIdentity[PortfolioRecipeIdentity] = PortfolioEvidence
            if PortfolioEvidence is not None and (not PortfolioEvidence.Finalized) and (JointPlacementCandidateIndex in PortfolioEvidence.ExpectedCandidateIndices):
                PortfolioEvidence.RejectionsByCandidateIndex[JointPlacementCandidateIndex] = MandatoryAccessPortfolioRejection(CandidateIndex=JointPlacementCandidateIndex, OwnershipFingerprint=CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, ConflictFingerprint=CandidateTopologyDemand.MandatoryAccessConflictFingerprint, PairwiseConflictEdges=BuildMandatoryAccessPairwiseEdges(MandatoryProfile))
                PortfolioEvaluation = EvaluateCompleteMandatoryAccessPortfolio(PortfolioEvidence, EffectiveAssignmentConstraints)
                MandatoryAccessPortfolioTracking = {'ExactScreenFingerprint': ExactScreenFingerprint, 'ExpectedCandidateIndices': list(PortfolioEvidence.ExpectedCandidateIndices), 'ObservedCandidateIndices': sorted(PortfolioEvidence.RejectionsByCandidateIndex), 'Verdict': PortfolioEvaluation.Verdict, 'MissingCandidateIndices': list(PortfolioEvaluation.MissingCandidateIndices), 'UnexpectedCandidateIndices': list(PortfolioEvaluation.UnexpectedCandidateIndices)}
                if PortfolioEvaluation.Verdict != 'incomplete':
                    PortfolioEvidence.Finalized = True
                    CurrentCutFingerprint = Context.CurrentPlacementAssignmentCut.ConflictFingerprint if Context.CurrentPlacementAssignmentCut is not None else ''
                    IdentityStillCurrent = MandatoryAccessPortfolioIdentityMatchesCurrent(PortfolioIdentity, Context.CurrentPlacementAssignmentCut, Context.PlacementAssignmentConstraints)
                    PreviousConstraints = Context.PlacementAssignmentConstraints
                    PromotedConstraints = PreviousConstraints
                    if PortfolioEvaluation.ShouldPromote and IdentityStillCurrent:
                        PromotedConstraints = AddMandatoryAccessPortfolioPairwiseConstraints(PreviousConstraints, PortfolioEvaluation)
                        Context.PlacementAssignmentConstraints = PromotedConstraints
                        StalePendingJointStates = tuple(
                            State
                            for State in Context.PendingJointPlacementStates
                            if BuildPendingJointPlacementPortfolioIdentity(State)
                            == JointPortfolioIdentity
                        )
                        if StalePendingJointStates:
                            Context.PendingJointPlacementStates[:] = [
                                State
                                for State in Context.PendingJointPlacementStates
                                if BuildPendingJointPlacementPortfolioIdentity(State)
                                != JointPortfolioIdentity
                            ]
                            Context.JointPlacementStateEvents.append({
                                'Status': 'pruned-stale-siblings-after-access-proof-promotion',
                                'SourceGenerator': SourceGenerator,
                                'RoutingSpacing': CandidateSpacing,
                                'AssignmentConstraintFingerprintBefore': PreviousConstraints.Fingerprint,
                                'AssignmentConstraintFingerprintAfter': PromotedConstraints.Fingerprint,
                                'PrunedCandidateIndices': [State.CandidateIndex for State in StalePendingJointStates],
                                'CompleteEvidenceCandidateIndices': list(PortfolioEvidence.ExpectedCandidateIndices),
                            })
                        PromotedSignals = frozenset((Signal for Edge in PortfolioEvaluation.NewPairwiseConflictEdges for Signal in Edge))
                        Context.PlacementRelocationSignals = frozenset((*Context.PlacementRelocationSignals, *PromotedSignals))
                        Context.PlacementRelocationPrioritySignals = PromotedSignals
                        Context.PlacementRequiredRelocationSignals = PromotedSignals
                        Context.NeedsFeedbackPlacementGeneration = True
                        Context.NeedsCurrentStructuredCutRegeneration = False
                    elif IdentityStillCurrent and PortfolioEvaluation.Verdict == 'already-represented':
                        Context.NeedsFeedbackPlacementGeneration = True
                        Context.NeedsCurrentStructuredCutRegeneration = True
                    StrongRepairIdentity = BuildMandatoryAccessPortfolioRecipeIdentity(PortfolioIdentity, AssignmentConstraintFingerprint=PromotedConstraints.Fingerprint)
                    if ShouldOpenStrongMandatoryAccessRepair(PortfolioEvaluation, IdentityStillCurrent=IdentityStillCurrent, AlreadyConsumed=StrongRepairIdentity in Context.ConsumedStrongMandatoryAccessRepairIdentities):
                        Context.ConsumedStrongMandatoryAccessRepairIdentities.add(StrongRepairIdentity)
                        StrongRepairSignals = frozenset((*(Context.CurrentPlacementAssignmentCut.ConflictSignals if Context.CurrentPlacementAssignmentCut is not None else ()), *(Signal for Edge in PortfolioEvaluation.PairwiseConflictEdges for Signal in Edge)))
                        Context.PlacementRelocationSignals = frozenset((*Context.PlacementRelocationSignals, *StrongRepairSignals))
                        Context.PlacementRelocationPrioritySignals = StrongRepairSignals
                        Context.PlacementRequiredRelocationSignals = StrongRepairSignals
                        Context.PlacementClusterPinBankRepairSignals = StrongRepairSignals
                        Context.PlacementCoordinatedCandidateDiversificationSignals = frozenset((*Context.PlacementCoordinatedCandidateDiversificationSignals, *StrongRepairSignals))
                        Context.PostPinBankRepairEpochActive = True
                        Context.InternalPinBankGeometryRepairActive = True
                        Context.PendingStrongMandatoryAccessRepair = True
                        Context.NeedsFeedbackPlacementGeneration = True
                        Context.NeedsCurrentStructuredCutRegeneration = True
                    Context.PlacementGenerationDecisions.append({'Result': 'complete-mandatory-access-portfolio-' + ('promoted' if PortfolioEvaluation.ShouldPromote and IdentityStillCurrent else PortfolioEvaluation.Verdict if IdentityStillCurrent else 'stale-identity'), 'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'RelocationVariant': RelocationVariant, 'ExactScreenFingerprint': ExactScreenFingerprint, 'AssignmentCutFingerprint': PortfolioIdentity.AssignmentCutFingerprint, 'AssignmentCutPreserved': CurrentCutFingerprint == PortfolioIdentity.AssignmentCutFingerprint, 'AssignmentConstraintFingerprintBefore': PreviousConstraints.Fingerprint, 'AssignmentConstraintFingerprintAfter': PromotedConstraints.Fingerprint, 'IdentityStillCurrent': IdentityStillCurrent, 'ExpectedCandidateIndices': list(PortfolioEvidence.ExpectedCandidateIndices), 'ObservedCandidateIndices': sorted(PortfolioEvidence.RejectionsByCandidateIndex), 'OwnershipFingerprints': [PortfolioEvidence.RejectionsByCandidateIndex[Index].OwnershipFingerprint for Index in PortfolioEvidence.ExpectedCandidateIndices], 'ConflictFingerprints': [PortfolioEvidence.RejectionsByCandidateIndex[Index].ConflictFingerprint for Index in PortfolioEvidence.ExpectedCandidateIndices], 'PairwiseConflictEdges': [list(Edge) for Edge in PortfolioEvaluation.PairwiseConflictEdges], 'NewPairwiseConflictEdges': [list(Edge) for Edge in PortfolioEvaluation.NewPairwiseConflictEdges], 'HigherOrderSignalSetsPreserved': [list(Signals) for Signals in PromotedConstraints.HigherOrderSignalSets], 'MissingCandidateIndices': list(PortfolioEvaluation.MissingCandidateIndices), 'UnexpectedCandidateIndices': list(PortfolioEvaluation.UnexpectedCandidateIndices), 'MissingOwnershipCandidateIndices': list(PortfolioEvaluation.MissingOwnershipCandidateIndices), 'DuplicateOwnershipFingerprints': list(PortfolioEvaluation.DuplicateOwnershipFingerprints), 'NextAction': 'generate-exact-cut-relocation' if PortfolioEvaluation.ShouldPromote and IdentityStillCurrent else 'generate-stronger-exact-cut-relocation' if IdentityStillCurrent and PortfolioEvaluation.Verdict == 'already-represented' else 'none'})
        if MandatoryConflicts and JointPlacementCandidateIndex == 0 and CandidatePacking.Enabled and (not CandidatePacking.EnableJointClusterOrientation):
            DynamicRequest = PlacementGenerationRequest(SourceGenerator='row-beam-mandatory-joint', RoutingSpacing=CandidateSpacing, PackingPolicy=replace(CandidatePacking, EnableJointClusterOrientation=True))
            Context.PendingJointPlacementStates.insert(0, PendingJointPlacementState(Request=DynamicRequest, CandidateIndex=0, RelocationVariant=RelocationVariant, RoutingSpacing=CandidateSpacing, RelocationSignals=EffectiveRelocationSignals, RelocationPrioritySignals=EffectiveRelocationPrioritySignals, RequiredRelocationSignals=EffectiveRequiredRelocationSignals, AssignmentCut=EffectiveAssignmentCut, AssignmentConstraints=EffectiveAssignmentConstraints, CoordinatedCandidateDiversificationSignals=EffectiveCoordinatedCandidateDiversificationSignals, EnableClusterLocalRouteReuse=EnableCurrentClusterLocalRouteReuse, IsPostPinBankRepairEpoch=Context.PostPinBankRepairEpochActive, EnableInternalPinBankGeometryRepair=Context.InternalPinBankGeometryRepairActive, InternalPinBankGeometryRepairSignals=EffectiveInternalPinBankGeometryRepairSignals, RequiredDistinctPinBankOwnershipFingerprint=Context.RequiredDistinctPinBankOwnershipFingerprint, TopologyCutFrontier=EffectiveTopologyCutFrontier, PhysicalProofCoreSignals=FixedPhysicalProofCoreSignals, PhysicalProofFingerprint=FixedPhysicalProofFingerprint, FixedConnectivityClusters=FixedConnectivityClusters))
            Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'mandatory-access-enabled-joint-portfolio', 'ConflictResourceCount': len(MandatoryConflicts), 'ConflictSignals': sorted(CandidateTopologyDemand.MandatoryAccessConflictSignals), 'MandatoryAccessOwnershipFingerprint': CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, 'MandatoryAccessConflictFingerprint': CandidateTopologyDemand.MandatoryAccessConflictFingerprint, 'JointOrderKey': list(CandidateTopologyDemand.JointOrderKey), 'TopologyDemandProfile': CandidateTopologyDemand.ToDictionary(), 'MandatoryAccessProfile': MandatoryProfile.ToDictionary() if MandatoryProfile is not None else None, 'MandatoryAccessPortfolioTracking': MandatoryAccessPortfolioTracking, 'Trigger': 'mandatory-access-conflict'})
            return False
        FixedPinAccessSolve = SolveFixedPlacementMandatoryAccess(Candidate.Placed.PlacedGates, WorkCheck=CheckPlacementGeneration)
        FixedPinAccessRejectsCandidate = FixedPinAccessSolve.Status is FixedPlacementPinAccessStatus.Unsatisfiable
        if FixedPinAccessRejectsCandidate and MandatoryProfile is None:
            MandatoryProfile = MeasureMandatoryAccessConflictProfile(Candidate.Placed.PlacedGates, Candidate.SignalOrder, WorkCheck=CheckPlacementGeneration)
            MandatoryConflicts = MandatoryConflictMap(MandatoryProfile)
            CandidateTopologyDemand = MeasurePlacementTopologyDemand(Context.TopologyDemand, Candidate, MandatoryConflicts=MandatoryConflicts, MandatoryProfile=MandatoryProfile)
            RecipeDiagnostics['__TopologyDemandProfile__'] = CandidateTopologyDemand.ToDictionary()
            Candidate.Placed.LocalRouteDiagnostics = RecipeDiagnostics
        if MandatoryProfile is not None and bool(MandatoryConflicts) != FixedPinAccessRejectsCandidate:
            raise ValueError('fixed pin-access solve and mandatory profile disagree')
        if FixedPinAccessRejectsCandidate:
            ConflictSignals = frozenset((Signal for Owners in MandatoryConflicts.values() for Signal in Owners))
            ConflictFingerprint = CandidateTopologyDemand.MandatoryAccessConflictFingerprint
            ConflictKey = (len(MandatoryConflicts), len(ConflictSignals), int(JointDiagnostics.get('SelectedScore', 0)), JointPlacementCandidateIndex, ConflictFingerprint)
            if Context.BestMandatoryAccessConflictKey is None or ConflictKey < Context.BestMandatoryAccessConflictKey:
                Context.BestMandatoryAccessConflictKey = ConflictKey
                if not (RequiresImmediateAssignmentCutRelocation(EffectiveAssignmentCut) or PlacementAssignmentConstraintsAreActive(EffectiveAssignmentConstraints)):
                    Context.PlacementRelocationSignals = ConflictSignals
                    Context.PlacementRelocationPrioritySignals = ConflictSignals
                    Context.PlacementRequiredRelocationSignals = ConflictSignals
            CandidateSelectedForRelocation = ConflictKey == Context.BestMandatoryAccessConflictKey and (not (RequiresImmediateAssignmentCutRelocation(EffectiveAssignmentCut) or PlacementAssignmentConstraintsAreActive(EffectiveAssignmentConstraints)))
            Context.ProactiveRelocationRequested = Context.ProactiveRelocationRequested or CandidateSelectedForRelocation
            Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'rejected-mandatory-access-conflict', 'DecisionBoundary': 'fixed-pin-access-unsatisfiable', 'FixedPinAccessStatus': FixedPinAccessSolve.Status.value, 'JointPlacementCandidateIndex': JointPlacementCandidateIndex, 'ConflictSignals': sorted(ConflictSignals), 'ConflictResourceCount': len(MandatoryConflicts), 'ConflictFingerprint': ConflictFingerprint, 'MandatoryAccessOwnershipFingerprint': CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, 'JointOrderKey': list(CandidateTopologyDemand.JointOrderKey), 'TopologyDemandProfile': CandidateTopologyDemand.ToDictionary(), 'MandatoryAccessProfile': MandatoryProfile.ToDictionary() if MandatoryProfile is not None else None, 'FixedPinAccessSolve': FixedPinAccessSolve.ToDictionary(), 'MandatoryAccessPortfolioTracking': MandatoryAccessPortfolioTracking, 'MandatoryAccessPortfolioIdentity': ({**MandatoryAccessPortfolioIdentityDiagnostics, 'EvidenceFound': PortfolioEvidence is not None} if MandatoryAccessPortfolioIdentityDiagnostics is not None else None), 'SelectedForRelocation': CandidateSelectedForRelocation, 'ElapsedSeconds': round(Context.Services.monotonic() - PlacementStarted, 6)})
            return False
        Fingerprint = BuildPlacementFingerprint(Candidate, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=not CandidatePacking.EnableJointClusterOrientation)
        RetentionFingerprint = BuildPlacementRetentionFingerprint(Candidate, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=not CandidatePacking.EnableJointClusterOrientation)
        CheckPlacementGeneration({'Phase': 'placement-fingerprint-complete'})
        if Fingerprint in Context.RejectedPlacementFingerprints or RetentionFingerprint in Context.RejectedPlacementRetentionFingerprints:
            Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'rejected-placement-repeat', 'PlacementFingerprint': Fingerprint, 'PlacementRetentionFingerprint': RetentionFingerprint, 'ExactStatePlacementEvaluationCacheHit': CachedExactStateEvaluation is not None, 'ElapsedSeconds': round(Context.Services.monotonic() - PlacementStarted, 6)})
            return False
        ExistingRetention = Context.RetainedPlacementTopologyFingerprints.get(RetentionFingerprint)
        if ExistingRetention is not None:
            ExistingFingerprint, ExistingSourceGenerator = ExistingRetention
            Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'duplicate-placement', 'PlacementFingerprint': Fingerprint, 'PlacementRetentionFingerprint': RetentionFingerprint, 'ExactStatePlacementEvaluationCacheHit': CachedExactStateEvaluation is not None, 'DuplicatePlacementFingerprint': ExistingFingerprint, 'DuplicateOf': ExistingSourceGenerator, 'ElapsedSeconds': round(Context.Services.monotonic() - PlacementStarted, 6)})
            if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
                print(f'[debug] authoritative: deduplicated placement source={SourceGenerator} spacing={CandidateSpacing} duplicate_of={ExistingSourceGenerator} elapsed={Context.Services.monotonic() - PlacementStarted:.3f}s', flush=True)
            return False
        if MaterializeRoutingResources and (not CandidatePacking.EnableJointClusterOrientation):
            CandidateResources = Context.Services.BuildRoutingResources(Candidate.Placed, WorkCheck=CheckPlacementGeneration)
            CheckPlacementGeneration({'Phase': 'routing-resource-construction-complete'})
        Feedback = None
        if Context.Policy.Placement.EnableRoutingFeedback and (not bool(os.environ.get('RCS_SKIP_PLACEMENT_FEEDBACK'))) and (not JointDiagnostics) and (not IsDerivedSingleComponentPlacementSource(SourceGenerator)):
            Feedback = Context.Services.MeasurePlacementRoutingFeedback(Candidate, CandidateSpacing, Context.Policy, Context.Technology, CheckPlacementGeneration)
            CheckPlacementGeneration({'Phase': 'placement-feedback-complete'})
        Context.UniquePlacements[Fingerprint] = (SourceGenerator, CandidateSpacing, Candidate)
        Context.PlacementRetentionFingerprintByFingerprint[Fingerprint] = RetentionFingerprint
        Context.RetainedPlacementTopologyFingerprints[RetentionFingerprint] = (Fingerprint, SourceGenerator)
        Context.TopologyDemandByFingerprint[Fingerprint] = CandidateTopologyDemand
        if JointDiagnostics:
            Context.JointPlacementStateByPlacementFingerprint[Fingerprint] = JointPortfolioState
        if CandidateResources is not None:
            Context.RoutingResourcesByFingerprint[Fingerprint] = CandidateResources
        if Feedback is not None:
            Context.FeedbackByFingerprint[Fingerprint] = Feedback
        Context.PlacementGenerationDecisions.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Result': 'unique-placement', 'JointPlacementCandidateIndex': JointPlacementCandidateIndex, 'PlacementFingerprint': Fingerprint, 'PlacementRetentionFingerprint': RetentionFingerprint, 'ExactStatePlacementEvaluationCacheHit': CachedExactStateEvaluation is not None, 'MandatoryAccessOwnershipFingerprint': CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, 'MandatoryAccessConflictFingerprint': CandidateTopologyDemand.MandatoryAccessConflictFingerprint, 'TopologyDemandProfile': CandidateTopologyDemand.ToDictionary(), 'JointOrderKey': list(CandidateTopologyDemand.JointOrderKey), 'RelocationSignals': sorted(EffectiveRelocationSignals), 'PackedGateArea': PackedGateArea, 'BaselinePackedGateArea': Context.BaselinePackedGateArea, 'MaximumPackedGateArea': MaximumPackedGateArea, 'PackedClusters': [{'ClusterId': Cluster.ClusterId, 'Members': list(Cluster.MemberNands), 'StackId': Cluster.StackId, 'StackLevel': Cluster.StackLevel, 'BaseY': Cluster.BaseY, 'OrientationRotation': Cluster.OrientationRotation, 'OrientationMirrorX': Cluster.OrientationMirrorX} for Cluster in Candidate.PackedClusters], 'JointClusterPlacement': RecipeDiagnostics.get('__JointClusterPlacement__', {}), 'PlacementGenerationBudgetSeconds': round(PlacementGenerationBudgetSeconds, 6), 'RoutingReserveSeconds': round(RoutingReserveSeconds, 6), 'ElapsedSeconds': round(Context.Services.monotonic() - PlacementStarted, 6)})
        if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            print(f'[debug] authoritative: generated placement source={SourceGenerator} spacing={CandidateSpacing} variant={RelocationVariant} fingerprint={Fingerprint[:12]} elapsed={Context.Services.monotonic() - PlacementStarted:.3f}s', flush=True)
            print(f'[debug] authoritative: terminal placements values={[(Gate.Name, str(getattr(Gate.Kind, 'value', Gate.Kind)), Gate.X, Gate.Z, Gate.Rotation, Gate.OutputPin, Gate.InputPins) for Gate in Candidate.Placed.PlacedGates if str(getattr(Gate.Kind, 'value', Gate.Kind)) in {'INPUT', 'OUTPUT'}]}', flush=True)
        return True
    except Exception as Error:
        if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            traceback.print_exc()
        if isinstance(Error, ValueError) and JointPlacementCandidateIndex == 0 and CandidatePacking.Enabled and CandidatePacking.EnableJointClusterOrientation and str(Error).startswith('Exact joint placement candidate rejected:'):
            ExistingIndexes = {State.CandidateIndex for State in Context.PendingJointPlacementStates if State.Request == Request and State.RelocationVariant == RelocationVariant and (State.RoutingSpacing == CandidateSpacing) and (State.RelocationSignals == EffectiveRelocationSignals) and (State.RelocationPrioritySignals == EffectiveRelocationPrioritySignals) and (State.RequiredRelocationSignals == EffectiveRequiredRelocationSignals) and (State.AssignmentCut == EffectiveAssignmentCut) and (State.AssignmentConstraints == EffectiveAssignmentConstraints) and (State.CoordinatedCandidateDiversificationSignals == EffectiveCoordinatedCandidateDiversificationSignals)}
            QueuedCandidateIndexes = [CandidateIndex for CandidateIndex in range(1, CandidatePacking.RetainedJointPlacementCandidates * (2 if Context.TopologyDemand.RequiresJointPortfolio else 1)) if CandidateIndex not in ExistingIndexes]
            CandidateStates = [PendingJointPlacementState(Request=Request, CandidateIndex=CandidateIndex, RelocationVariant=RelocationVariant, RoutingSpacing=CandidateSpacing, RelocationSignals=EffectiveRelocationSignals, RelocationPrioritySignals=EffectiveRelocationPrioritySignals, RequiredRelocationSignals=EffectiveRequiredRelocationSignals, AssignmentCut=EffectiveAssignmentCut, AssignmentConstraints=EffectiveAssignmentConstraints, CoordinatedCandidateDiversificationSignals=EffectiveCoordinatedCandidateDiversificationSignals, EnableClusterLocalRouteReuse=EnableCurrentClusterLocalRouteReuse, IsPostPinBankRepairEpoch=Context.PostPinBankRepairEpochActive, EnableInternalPinBankGeometryRepair=Context.InternalPinBankGeometryRepairActive, InternalPinBankGeometryRepairSignals=EffectiveInternalPinBankGeometryRepairSignals, RequiredDistinctPinBankOwnershipFingerprint=Context.RequiredDistinctPinBankOwnershipFingerprint, TopologyCutFrontier=EffectiveTopologyCutFrontier, PhysicalProofCoreSignals=FixedPhysicalProofCoreSignals, PhysicalProofFingerprint=FixedPhysicalProofFingerprint, FixedConnectivityClusters=FixedConnectivityClusters) for CandidateIndex in QueuedCandidateIndexes]
            ExistingStateKeys = {*Context.MaterializedJointPlacementStateKeys, *(BuildPendingJointPlacementStateKey(State) for State in Context.PendingJointPlacementStates)}
            CandidateStates = [State for State in CandidateStates if BuildPendingJointPlacementStateKey(State) not in ExistingStateKeys]
            Context.PendingJointPlacementStates.extend(CandidateStates)
            QueuedCandidateIndexes = [State.CandidateIndex for State in CandidateStates]
            Context.JointPlacementStateEvents.extend(({'CandidateIndex': CandidateIndex, 'Status': 'queued-after-exact-overlap-rejection', 'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'RejectedCandidateIndex': JointPlacementCandidateIndex} for CandidateIndex in QueuedCandidateIndexes))
            Context.PlacementGenerationDecisions.append({'Result': 'retained-joint-states-after-exact-overlap-rejection', 'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'RejectedCandidateIndex': JointPlacementCandidateIndex, 'QueuedCandidateIndices': QueuedCandidateIndexes})
        if Fingerprint is not None:
            Context.RejectedPlacementFingerprints.add(Fingerprint)
        if RetentionFingerprint is not None:
            Context.RejectedPlacementRetentionFingerprints.add(RetentionFingerprint)
        if isinstance(Error, RoutingStageError):
            Failure = Error.Failure
        elif isinstance(Error, ValueError):
            Failure = RoutingFailure(Reason=RoutingFailureReason.PlacementOverlap, Stage='PlacementGeneration', Detail=str(Error), RepairActions=('AdvancePlacementGenerator',), Diagnostics={'ErrorType': type(Error).__name__})
        else:
            Failure = RoutingFailure(Reason=RoutingFailureReason.DetailedSearchExhausted, Stage='PlacementGeneration', Detail=f'unexpected bounded placement-generation failure: {type(Error).__name__}: {Error}', RepairActions=('AdvancePlacementGenerator',), Diagnostics={'ErrorType': type(Error).__name__})
        Context.LastStructuredPlacementFailure = Failure
        FailureSnapshot = BuildPlacementFailureHistorySnapshot(Failure)
        Context.PlacementGenerationFailures.append({'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'JointPlacementCandidateIndex': JointPlacementCandidateIndex, 'PackedNandPlacement': bool(CandidatePacking.Enabled), 'Failure': str(Error), 'PlacementGenerationBudgetSeconds': round(PlacementGenerationBudgetSeconds, 6), 'ElapsedSeconds': round(Context.Services.monotonic() - PlacementStarted, 6), 'Diagnostics': FailureSnapshot})
        if CandidatePacking.Enabled and CandidatePacking.EnableJointClusterOrientation:
            Context.JointPlacementStateEvents.append({'CandidateIndex': JointPlacementCandidateIndex, 'Status': 'materialization-rejected', 'SourceGenerator': SourceGenerator, 'RoutingSpacing': CandidateSpacing, 'Reason': str(Error), 'Failure': FailureSnapshot})
        if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
            print(f'[debug] authoritative: skipped placement candidate spacing={CandidateSpacing} packing={CandidatePacking.Enabled} reason={Error}', f'elapsed={Context.Services.monotonic() - PlacementStarted:.3f}s', flush=True)
        if Failure.Reason == RoutingFailureReason.RuntimeBudgetExceeded:
            raise RoutingStageError(_PlacementFailureWithHistory(Context, Failure)) from Error
        return False


def _TakeNextDeferredRequest(Context, PreferRelocation: bool=False, PreferDirectOnly: bool=False, RequireExactCutBeforeBroad: bool=False, AllowCapacityPairRepair: bool=False) -> PlacementGenerationRequest | None:

    def ConsumeDeferredRequest(RequestIndex: int) -> PlacementGenerationRequest:
        Context.ConsumedDeferredRequestIndexes.add(RequestIndex)
        while Context.DeferredRequestIndex < len(Context.GenerationPlan.DeferredRequests) and Context.DeferredRequestIndex in Context.ConsumedDeferredRequestIndexes:
            Context.DeferredRequestIndex += 1
        return Context.GenerationPlan.DeferredRequests[RequestIndex]
    MaximumFeedbackRounds = Context.Policy.NegotiatedRouting.MaximumPlacementFeedbackRounds if Context.Policy.NegotiatedRouting.Enabled else max(1, Context.Policy.NandPacking.PlacementFeedbackIterations + 1)
    if Context.TopologyDemand.RequiresJointPortfolio:
        MaximumFeedbackRounds = min(MaximumFeedbackRounds, 1)
    elif Context.NandGateCount < 32:
        MaximumFeedbackRounds = min(MaximumFeedbackRounds, 1)
    if Context.PendingTopologyCutEpoch is not None:
        CurrentEpoch = BuildTopologyCutEpochIdentity(Context.CurrentPlacementAssignmentCut, Context.PlacementAssignmentConstraints) if Context.CurrentPlacementAssignmentCut is not None else None
        if CurrentEpoch == Context.PendingTopologyCutEpoch:
            Context.OpenedTopologyCutEpochs.add(Context.PendingTopologyCutEpoch)
            Context.PlacementGenerationDecisions.append({'Result': 'topology-cut-epoch-materializing', 'AssignmentCutFingerprint': Context.PendingTopologyCutEpoch.AssignmentCutFingerprint, 'AssignmentConstraintFingerprint': Context.PendingTopologyCutEpoch.AssignmentConstraintFingerprint, 'MandatoryAccessOwnershipFingerprint': Context.PendingTopologyCutEpoch.MandatoryAccessOwnershipFingerprint, 'RemainingRoutingSeconds': round(max(0.0, Context.Deadline.RemainingSeconds()), 6), 'BroadGenerationDeferred': True, 'TargetedPinBankEpoch': Context.PostPinBankRepairEpochActive and Context.InternalPinBankGeometryRepairActive})
            Context.PendingTopologyCutEpoch = None
            TargetedPinBankEpoch = Context.PostPinBankRepairEpochActive and Context.InternalPinBankGeometryRepairActive or Context.RotatedMacroAncestorTargetedEpochPending
            Context.RotatedMacroAncestorTargetedEpochPending = False
            EpochPackingPolicy = BuildTargetedPinBankPackingPolicy(Context.Policy.NandPacking) if TargetedPinBankEpoch else replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=True)
            return PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(EpochPackingPolicy, TerminalShellLateralSearch=max(Context.Policy.NandPacking.TerminalShellLateralSearch, 4) if not TargetedPinBankEpoch and ShouldWidenTopologyCutTerminalShell(TopologyRequiresJointPortfolio=True, AssignmentCut=Context.CurrentPlacementAssignmentCut, ExternalSignals=(*Context.Module.Inputs, *Context.Module.Outputs)) else Context.Policy.NandPacking.TerminalShellLateralSearch), UseCurrentAssignmentCutRelocationSignals=True)
        Context.PlacementGenerationDecisions.append({'Result': 'topology-cut-epoch-superseded', 'RequestedAssignmentCutFingerprint': Context.PendingTopologyCutEpoch.AssignmentCutFingerprint, 'CurrentAssignmentCutFingerprint': CurrentEpoch.AssignmentCutFingerprint if CurrentEpoch is not None else ''})
        Context.PendingTopologyCutEpoch = None
    if PreferRelocation and Context.PendingStrongMandatoryAccessRepair and (Context.CurrentPlacementAssignmentCut is not None) and Context.PlacementRelocationSignals:
        Context.PendingStrongMandatoryAccessRepair = False
        Context.StrongMandatoryAccessRepairMaterializationPending = True
        Context.NeedsCurrentStructuredCutRegeneration = False
        Context.PlacementGenerationDecisions.append({'Result': 'strong-mandatory-access-pin-bank-epoch', 'Reason': 'complete access-distinct compact portfolio exhausted rigid slot/orientation repair', 'RelocationVariant': 12, 'ConflictSignals': sorted(Context.PlacementClusterPinBankRepairSignals), 'CurrentAssignmentCut': Context.CurrentPlacementAssignmentCut.ToDictionary(), 'ActivePlacementConstraints': Context.PlacementAssignmentConstraints.ToDictionary(), 'BroadGenerationDeferred': True})
        return PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=True, RetainedJointPlacementCandidates=1 if not Context.TopologyDemand.RequiresJointPortfolio else Context.Policy.NandPacking.RetainedJointPlacementCandidates), UseCurrentAssignmentCutRelocationSignals=True)
    if Context.PlacementGenerationAttempts >= Context.GenerationPlan.MaximumAttempts and (not AllowCapacityPairRepair):
        return None
    if Context.TopologyDemand.RequiresJointPortfolio and PreferRelocation and Context.NeedsCurrentStructuredCutRegeneration and Context.PlacementRelocationSignals and (Context.TotalRelocationGenerationCount < MaximumFeedbackRounds):
        Context.NeedsCurrentStructuredCutRegeneration = False
        Context.PlacementGenerationDecisions.append({'Result': 'regenerate-current-structured-cut-portfolio', 'Reason': 'the retained access-distinct portfolio exhausted after a non-promoting candidate-starvation report', 'CurrentAssignmentCut': Context.CurrentPlacementAssignmentCut.ToDictionary() if Context.CurrentPlacementAssignmentCut is not None else None, 'ActivePlacementConstraints': Context.PlacementAssignmentConstraints.ToDictionary()})
        return PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=Context.TopologyDemand.RequiresJointPortfolio))
    if PreferDirectOnly:
        for RequestIndex, Request in enumerate(Context.GenerationPlan.DeferredRequests):
            if RequestIndex not in Context.ConsumedDeferredRequestIndexes and Request.SourceGenerator == 'row-beam-direct-only':
                Context.PlacementGenerationDecisions.append({'Result': 'prioritize-direct-only-after-exact-cut', 'Reason': 'the primary row placement reached an exact higher-order assignment cut without boundary, guide, or pin-access pressure'})
                return ConsumeDeferredRequest(RequestIndex)
    if Context.TopologyDemand.RequiresJointPortfolio and ShouldPrioritizeCurrentExactCutBeforeBroad(Required=RequireExactCutBeforeBroad, PreferRelocation=PreferRelocation, HasCurrentAssignmentCut=Context.CurrentPlacementAssignmentCut is not None, HasRelocationSignals=bool(Context.PlacementRelocationSignals), TotalRelocationGenerationCount=Context.TotalRelocationGenerationCount, MaximumFeedbackRounds=MaximumFeedbackRounds) and (not Context.TopologyDemand.RequiresJointPortfolio or HasTopologyCutEpochRoutingReserve(RemainingSeconds=Context.Deadline.RemainingSeconds(), Policy=Context.Policy, RequiresDenseBoundaryRouting=Context.TopologyPressure.ScaleGeometryPressure, HasBoundedExactCutEvidence=AssignmentCutHasBoundedExactCore(Context.CurrentPlacementAssignmentCut))):
        Context.PlacementGenerationDecisions.append({'Result': 'prioritize-current-exact-cut-before-broad', 'CurrentAssignmentCut': Context.CurrentPlacementAssignmentCut.ToDictionary(), 'ActivePlacementConstraints': Context.PlacementAssignmentConstraints.ToDictionary(), 'TotalRelocationGenerationCount': Context.TotalRelocationGenerationCount, 'MaximumFeedbackRounds': MaximumFeedbackRounds})
        return PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=Context.TopologyDemand.RequiresJointPortfolio))
    if ShouldPrioritizePlacementConflictRelocation(PreferRelocation=PreferRelocation, RelocationSignals=Context.PlacementRelocationSignals, TotalRelocationGenerationCount=Context.TotalRelocationGenerationCount, MaximumFeedbackRounds=MaximumFeedbackRounds, RelocationPrioritySignals=Context.PlacementRelocationPrioritySignals, LastRelocationPrioritySignalsUsed=Context.LastRelocationPrioritySignalsUsed, RequiredRelocationSignals=Context.PlacementRequiredRelocationSignals, LastRequiredRelocationSignalsUsed=Context.LastRequiredRelocationSignalsUsed, CurrentAssignmentCutFingerprint=Context.CurrentPlacementAssignmentCut.ConflictFingerprint if Context.CurrentPlacementAssignmentCut is not None else '', LastAssignmentCutFingerprintUsed=Context.LastAssignmentCutFingerprintUsed, CurrentAssignmentConstraintFingerprint=Context.PlacementAssignmentConstraints.Fingerprint, LastAssignmentConstraintFingerprintUsed=Context.LastAssignmentConstraintFingerprintUsed) and (not Context.TopologyDemand.RequiresJointPortfolio or HasTopologyCutEpochRoutingReserve(RemainingSeconds=Context.Deadline.RemainingSeconds(), Policy=Context.Policy, RequiresDenseBoundaryRouting=Context.TopologyPressure.ScaleGeometryPressure, HasBoundedExactCutEvidence=AssignmentCutHasBoundedExactCore(Context.CurrentPlacementAssignmentCut))):
        return PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=Context.TopologyDemand.RequiresJointPortfolio))
    PrioritizeTopologyCutEpochRelocation = ShouldPrioritizeTopologyCutEpochRelocation(TopologyRequiresJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio, HasRelocationSignals=bool(Context.PlacementRelocationSignals), TotalRelocationGenerationCount=Context.TotalRelocationGenerationCount, MaximumFeedbackRounds=MaximumFeedbackRounds, CurrentAssignmentCutFingerprint=Context.CurrentPlacementAssignmentCut.ConflictFingerprint if Context.CurrentPlacementAssignmentCut is not None else '', LastAssignmentCutFingerprintUsed=Context.LastAssignmentCutFingerprintUsed)
    TopologyEpochRoutingReserveAvailable = HasTopologyCutEpochRoutingReserve(RemainingSeconds=Context.Deadline.RemainingSeconds(), Policy=Context.Policy, RequiresDenseBoundaryRouting=Context.TopologyPressure.ScaleGeometryPressure, HasBoundedExactCutEvidence=AssignmentCutHasBoundedExactCore(Context.CurrentPlacementAssignmentCut))
    if PrioritizeTopologyCutEpochRelocation and TopologyEpochRoutingReserveAvailable:
        Context.PlacementGenerationDecisions.append({'Result': 'prioritize-topology-cut-epoch-relocation', 'CurrentAssignmentCut': Context.CurrentPlacementAssignmentCut.ToDictionary() if Context.CurrentPlacementAssignmentCut is not None else None, 'TotalRelocationGenerationCount': Context.TotalRelocationGenerationCount})
        return PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=True), UseCurrentAssignmentCutRelocationSignals=True)
    if PrioritizeTopologyCutEpochRelocation:
        Context.PlacementGenerationDecisions.append({'Result': 'topology-cut-epoch-relocation-deferred-routing-reserve', 'RemainingRoutingSeconds': round(max(0.0, Context.Deadline.RemainingSeconds()), 6), 'RequiredRoutingReserveSeconds': round(TopologyCutEpochAdmissionReserveSeconds(Context.Policy, Context.TopologyPressure.ScaleGeometryPressure, HasBoundedExactCutEvidence=AssignmentCutHasBoundedExactCore(Context.CurrentPlacementAssignmentCut)), 6), 'Reason': 'preserve the current exact access-distinct state instead of materializing an unfunded replacement relocation'})
    if Context.DeferredRequestIndex < len(Context.GenerationPlan.DeferredRequests):
        Request = Context.GenerationPlan.DeferredRequests[Context.DeferredRequestIndex]
        if Request.SourceGenerator == 'row-beam-conflict-relocation':
            Request = ConsumeDeferredRequest(Context.DeferredRequestIndex)
            if Context.PlacementRelocationSignals and Context.TotalRelocationGenerationCount == 0:
                return Request
    if Context.DeferredRequestIndex < len(Context.GenerationPlan.DeferredRequests):
        Request = Context.GenerationPlan.DeferredRequests[Context.DeferredRequestIndex]
        if Request.SourceGenerator == 'row-beam-direct-only' and Context.TotalRelocationGenerationCount >= 2:
            return ConsumeDeferredRequest(Context.DeferredRequestIndex)
    if Context.DeferredRequestIndex < len(Context.GenerationPlan.DeferredRequests):
        return ConsumeDeferredRequest(Context.DeferredRequestIndex)
    if Context.PlacementRelocationSignals and (AllowCapacityPairRepair or Context.TotalRelocationGenerationCount < MaximumFeedbackRounds) and (Context.PlacementRelocationSignals != Context.LastRelocationSignalsUsed or Context.PlacementRelocationPrioritySignals != Context.LastRelocationPrioritySignalsUsed or Context.PlacementRequiredRelocationSignals != Context.LastRequiredRelocationSignalsUsed or (Context.CurrentPlacementAssignmentCut is not None and Context.CurrentPlacementAssignmentCut.ConflictFingerprint != Context.LastAssignmentCutFingerprintUsed) or (Context.PlacementAssignmentConstraints.Fingerprint != Context.LastAssignmentConstraintFingerprintUsed) or (Context.RelocationGenerationCount < Context.Policy.NegotiatedRouting.MaximumPlacementFeedbackRounds)):
        return PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=Context.TopologyDemand.RequiresJointPortfolio))
    return None

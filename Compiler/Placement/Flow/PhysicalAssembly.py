"""Importable placement-flow helpers with explicit run state."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence
from Compiler.Routing.Pcb import ReplanPhysicalComponentAssembly, ValidatePhysicalComponentForeignPortalSupport
from Compiler.Routing.Contracts.Results import RoutedDesign
from Compiler.Routing.Failures import RoutingAssignmentCut, RoutingFailure, RoutingFailureReason, RoutingStageError
from Compiler.Routing.Reliability import BuildStableFingerprint
from Compiler.Placement.Core.MandatoryAccess import MeasureMandatoryAccessConflictProfile
from Compiler.Routing.Authoritative.CandidateCache import BuildFrozenPostClosurePortalHandoffTelemetry
from Compiler.Routing.Authoritative.CandidateGuides import BuildPhysicalGlobalPlanContinuationState, BuildPhysicalGlobalPlanYieldDeadline, RetainIncompletePhysicalGlobalPlan, SelectNextRetainedPhysicalGlobalPlan, ShouldScheduleRetainedPhysicalGlobalPlan
from Compiler.Routing.Components.Certification import ProveClosedComponentOwnedSignalFrontiers, SelectContractIndependentOwnedSignalFrontierUnsatCore
from Compiler.Routing.Components.GlobalNoGoods import RecordPhysicalComponentGlobalPlanNoGood
from Compiler.Routing.Components.NoGoods import RecordPhysicalComponentSymbolicCapacityEligibilityNoGood
from Compiler.Routing.Components.PhysicalPlanning import BindPhysicalComponentAssemblyGlobalChannels, BindPhysicalComponentAssemblyLocalPortSupports, ClassifyPhysicalComponentGlobalPlanningFailure, PreparePhysicalComponentGlobalPlanningPlacement, PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses, SelectPhysicalAssemblyGlobalBoundaryPorts, SelectPhysicalComponentExactGlobalChannelSignals
from Compiler.Routing.Components.SymbolicDomains import CompilePhysicalComponentSymbolicPortPairDomain, ProjectCompletePhysicalPortPairCertificateToApertureClauses, ProveClosedComponentSymbolicCapacityEligibility
from Compiler.Routing.Components.Validation import BuildPhysicalPortLocalContractFingerprint
from .Candidates import BuildComponentAccessFeedbackPlacementScore, BuildPhysicalAssemblyPlanningIncompleteFailure, BuildPhysicalGlobalPlanResumeCursorFromDiagnostics, ClassifyPhysicalGlobalPlanRetentionAdmission, FindPhysicalGlobalDiagnosticValues, HasDistinctRetainedPhysicalEligibilityState, PcbPlacementCandidate, SelectRetainedPhysicalPlacementForAccessCore
from .Demand import MeasurePlacementTopologyDemand
from .Feedback import BuildPlacementFingerprint
from .Portfolios import BuildTopologyCutEpochIdentity, PlacementGenerationRequest
from .Preparation import BuildClusterInterfaceComponentStateFingerprint, BuildClusterInterfacePlacementTopologyFingerprint, BuildPlacementRetentionFingerprint
from .Results import BuildCapacityRepairGeometryFingerprint, BuildPhysicalComponentPlacementFeedback, BuildPhysicalInterfaceRepairCore, BuildPhysicalLocalFactorDiversificationCore, BuildPhysicalOwnedFrontierTopologyRepairCore, BuildSymbolicCapacityRepairEvidence, FreezePhysicalAssemblyGlobalChannels, PhysicalComponentPlacementFeedback
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
    _PlacementFlowDefault,
)
from .RoutingAttempts import (
    _BuildCandidateRecords,
    _PublishTransactionalClusterEndpointRepair,
)
from .PlacementAttempts import (
    _TakeNextDeferredRequest,
    _TryPlacement,
)


def ReorderRemainingPlacementsForAccessCore(Context, CurrentPlacementFingerprint: str) -> None:
    IndexedQueue = tuple(enumerate(Context.InterfaceCandidateQueue))
    Context.InterfaceCandidateQueue[:] = [Value for _Index, Value in sorted(IndexedQueue, key=lambda Entry: (Entry[1][4], 0 if Entry[1][0] == 'prepare-eligibility' else 1, 1 if Entry[1][2].PlacementFingerprint == CurrentPlacementFingerprint else 0, (0, 0, 0, 0, 0) if Entry[1][2].PlacementFingerprint == CurrentPlacementFingerprint else BuildComponentAccessFeedbackPlacementScore(Entry[1][2], Context.ActiveComponentCutSignals), Entry[0]))]


def EnqueueProofGuidedPhysicalPlacement(Context, Failure: RoutingFailure, SourceCandidate: PcbPlacementCandidate, ComponentVariant: int) -> bool:
    """Turn one complete global-plan cut into a new placement state."""
    Diagnostics = Failure.Diagnostics if isinstance(Failure.Diagnostics, dict) else {}
    CompleteGlobalAssignmentCut = bool(Diagnostics.get('GlobalPlanDomainComplete', False) and Diagnostics.get('CompleteAssignmentCutProof', False))
    PhysicalPlacementFeedback = BuildPhysicalComponentPlacementFeedback(Failure)
    PlacementPressureSignals = tuple(sorted({str(Signal) for Signal in Diagnostics.get('PlacementInterfacePressureSignals', ()) if str(Signal)}))
    SymbolicCapacityFeedback = bool(Diagnostics.get('SymbolicCapacityPlacementFeedback', False) and PlacementPressureSignals)
    CapacityRepairConstraint = BuildPhysicalInterfaceRepairCore(Failure, SourceCandidate)
    InheritedCapacityRepairConstraint = Context.CapacityRepairConstraintByPlacementFingerprint.get(SourceCandidate.PlacementFingerprint)
    if CapacityRepairConstraint is None and InheritedCapacityRepairConstraint is not None:
        CapacityRepairConstraint = InheritedCapacityRepairConstraint
    if CapacityRepairConstraint is not None and PhysicalPlacementFeedback is None:
        PhysicalPlacementFeedback = PhysicalComponentPlacementFeedback(ProofFingerprint=CapacityRepairConstraint.SourceProofFingerprint, RelocationSignals=CapacityRepairConstraint.Signals, DomainFingerprint=CapacityRepairConstraint.RepairDomainFingerprint)
    CapacityRepairActive = bool(CapacityRepairConstraint is not None)
    if CapacityRepairActive:
        Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'interface-repair-epoch-started', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(CapacityRepairConstraint.Signals), 'ProofComplete': True, 'CoreSignalCount': len(CapacityRepairConstraint.Signals), 'RepairLevel': CapacityRepairConstraint.RepairLevel, 'ProofKind': CapacityRepairConstraint.ProofKind, 'ClusterIds': list(CapacityRepairConstraint.ClusterIds), 'BoundaryClasses': list(CapacityRepairConstraint.BoundaryClasses), 'RepairDomainFingerprint': CapacityRepairConstraint.RepairDomainFingerprint, 'ForcedSeamClasses': [list(Value) for Value in CapacityRepairConstraint.ForcedSeamClasses], 'PreemptedCandidateIds': [], 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
    PressureGuidance = bool(Diagnostics.get('PlacementWorkSliceExpired', False) and PlacementPressureSignals)
    UnderlyingFailure = Diagnostics.get('UnderlyingFailure', {})
    UnderlyingDiagnostics = UnderlyingFailure.get('Diagnostics', {}) if isinstance(UnderlyingFailure, dict) else {}
    UnderlyingConflictGraph = UnderlyingDiagnostics.get('ConflictGraph', {}) if isinstance(UnderlyingDiagnostics, dict) else {}
    for Edge in (*(UnderlyingDiagnostics.get('PairwisePortReservationNoGoodEdges', ()) if isinstance(UnderlyingDiagnostics, dict) else ()), *(UnderlyingConflictGraph.get('PairwiseIncompatibleEdges', ()) if isinstance(UnderlyingConflictGraph, dict) else ())):
        if isinstance(Edge, (tuple, list)) and len(Edge) == 2:
            OrderedEdge = tuple(sorted(map(str, Edge)))
            if OrderedEdge[0] and OrderedEdge[0] != OrderedEdge[1]:
                Context.ProofGuidedPlacementPressureEdges.add(OrderedEdge)
    if PhysicalPlacementFeedback is None and (PressureGuidance or SymbolicCapacityFeedback):
        PhysicalPlacementFeedback = PhysicalComponentPlacementFeedback(ProofFingerprint=BuildStableFingerprint(('physical-component-interface-pressure-v1', SourceCandidate.PlacementFingerprint, PlacementPressureSignals)), RelocationSignals=PlacementPressureSignals, DomainFingerprint=str(Diagnostics.get('DomainFingerprint', '')))
    if not CompleteGlobalAssignmentCut and PhysicalPlacementFeedback is None:
        return False
    AssignmentCut = RoutingAssignmentCut.FromFailure(Failure, SourceCandidateId=SourceCandidate.CandidateId, MandatoryAccessOwnershipFingerprint=SourceCandidate.TopologyDemand.MandatoryAccessOwnershipFingerprint if SourceCandidate.TopologyDemand is not None else '') if CompleteGlobalAssignmentCut else None
    if CompleteGlobalAssignmentCut and AssignmentCut is None and (CapacityRepairConstraint is None):
        return False
    RelocationSignals = frozenset(AssignmentCut.PriorityRelocationSignals or AssignmentCut.RelocationSignals or AssignmentCut.ConflictSignals or AssignmentCut.NoCandidateSignals if AssignmentCut is not None else PhysicalPlacementFeedback.RelocationSignals)
    if PlacementPressureSignals:
        RelocationSignals = frozenset(PlacementPressureSignals)
    if CapacityRepairConstraint is not None:
        RelocationSignals = frozenset(CapacityRepairConstraint.Signals)
    if not RelocationSignals:
        return False
    RelocationCore = tuple(sorted(RelocationSignals))
    PriorRelocationCoreCount = Context.ProofGuidedRelocationCoreCounts.get(RelocationCore, 0)
    Context.ProofGuidedRelocationCoreCounts[RelocationCore] = PriorRelocationCoreCount + 1
    RepeatedPlacementLocalCore = bool(PriorRelocationCoreCount > 0)
    ImmediatePhysicalGeometryFeedback = bool(PressureGuidance or SymbolicCapacityFeedback or RepeatedPlacementLocalCore or (SourceCandidate.PlacementFingerprint in Context.GeneratedProofGuidedPlacementFingerprints))
    ImmediateRelocationVariant = 12 + (Context.ProofGuidedPlacementGenerationCount + int(RepeatedPlacementLocalCore)) % 4 if ImmediatePhysicalGeometryFeedback else None
    ImmediateRoutingSpacing = Context.ConfiguredRoutingSpacing + 1 if ImmediatePhysicalGeometryFeedback else None
    if ImmediatePhysicalGeometryFeedback:
        FocusedEdge = tuple(sorted(RelocationSignals)) if len(RelocationSignals) == 2 else None
        if FocusedEdge is not None and FocusedEdge in Context.AppliedProofGuidedPlacementPressureEdges:
            UntriedPressureEdges = tuple((Edge for Edge in Context.ProofGuidedPlacementPressureEdges if Edge not in Context.AppliedProofGuidedPlacementPressureEdges))
            if UntriedPressureEdges:
                FocusedEdge = min(UntriedPressureEdges, key=lambda Edge: (BuildStableFingerprint(('component-interface-pressure-edge-v1', Edge)), Edge))
                RelocationSignals = frozenset(FocusedEdge)
        if FocusedEdge is not None:
            Context.AppliedProofGuidedPlacementPressureEdges.add(FocusedEdge)
        if PressureGuidance or SymbolicCapacityFeedback:
            Context.CumulativeProofGuidedRelocationSignals.clear()
        Context.CumulativeProofGuidedRelocationSignals.update(RelocationSignals)
        RelocationSignals = frozenset(Context.CumulativeProofGuidedRelocationSignals)
    if AssignmentCut is not None and HasDistinctRetainedPhysicalEligibilityState(Context.InterfaceCandidateQueue, ComponentVariant=ComponentVariant, PlacementFingerprint=SourceCandidate.PlacementFingerprint):
        Context.PendingProofGuidedPlacementByComponentVariant[ComponentVariant] = (Failure, SourceCandidate)
        Context.PlacementGenerationDecisions.append({'Result': 'physical-global-proof-guided-placement-deferred-for-retained-state', 'SourceCandidateId': SourceCandidate.CandidateId, 'ComponentVariant': ComponentVariant, 'RelocationSignals': sorted(RelocationSignals), 'ExecutableLegacyRepairCascade': False})
        return True
    KnownFingerprints = {Candidate.PlacementFingerprint for Candidate in Context.RawInterfaceCandidates} | Context.ProofGuidedPlacementFingerprints
    RetainedCandidate = None if ImmediatePhysicalGeometryFeedback or Context.ProofGuidedRetainedPlacementCount >= Context.MaximumProofGuidedRetainedPlacements else SelectRetainedPhysicalPlacementForAccessCore(Context.OrderedPlacements, KnownFingerprints, RelocationSignals)
    if RetainedCandidate is not None:
        Context.ProofGuidedPlacementFingerprints.add(RetainedCandidate.PlacementFingerprint)
        Context.ProofGuidedRetainedPlacementCount += 1
        Context.RequestedComponentStateFingerprints.add(BuildClusterInterfaceComponentStateFingerprint(RetainedCandidate.PlacementFingerprint, ComponentVariant))
        Context.InterfaceCandidateQueue.insert(0, ('prepare-eligibility', len(Context.RawInterfaceCandidates) + Context.ProofGuidedRetainedPlacementCount, RetainedCandidate, 0, ComponentVariant))
        Context.PlacementGenerationDecisions.append({'Result': 'physical-global-proof-guided-retained-placement', 'SourceCandidateId': SourceCandidate.CandidateId, 'ProofKind': 'global-assignment-cut' if AssignmentCut is not None else 'physical-port-unsat-core', 'RelocationSignals': sorted(RelocationSignals), 'PlacementFingerprint': RetainedCandidate.PlacementFingerprint, 'GeneratedNewGeometry': False, 'ExecutableLegacyRepairCascade': False})
        return True
    MaximumPlacementsForRelocationCore = Context.MaximumProofGuidedSymbolicCapacityPairPlacements if CapacityRepairActive else Context.MaximumProofGuidedGeneratedPlacements
    if Context.ProofGuidedPlacementGenerationCountByCore.get(RelocationCore, 0) >= MaximumPlacementsForRelocationCore:
        return False
    if AssignmentCut is not None:
        Context.CurrentPlacementAssignmentCut = AssignmentCut
        Context.PlacementAssignmentCutHistory.append(AssignmentCut)
        Context.PlacementAssignmentConstraints = Context.PlacementAssignmentConstraints.WithCut(AssignmentCut)
        Context.PendingTopologyCutEpoch = BuildTopologyCutEpochIdentity(AssignmentCut, Context.PlacementAssignmentConstraints)
    else:
        Context.CurrentPlacementAssignmentCut = None
        Context.PendingTopologyCutEpoch = None
    CapacityRepairGeometryKind = ''
    CapacityRepairRelocationVariant = ImmediateRelocationVariant
    CapacityRepairRoutingSpacing = ImmediateRoutingSpacing
    if CapacityRepairActive:
        RepairAttempt = Context.CapacityRepairGeneratedCountByProofFingerprint.get(CapacityRepairConstraint.SourceProofFingerprint, 0)
        CapacityRepairGeometryKind = ('widen-channel-deck' if CapacityRepairConstraint.RepairLevel == 'channel-capacity' else 'widen-interface') if RepairAttempt == 0 else 'split-channel-endpoints' if CapacityRepairConstraint.RepairLevel == 'channel-capacity' else 'split-relocate'
        CapacityRepairRelocationVariant = None if RepairAttempt == 0 else ImmediateRelocationVariant
        CapacityRepairRoutingSpacing = Context.ConfiguredRoutingSpacing + 1 if RepairAttempt == 0 else Context.ConfiguredRoutingSpacing + 2
    Context.PlacementRelocationSignals = RelocationSignals
    Context.PlacementRelocationPrioritySignals = RelocationSignals
    Context.PlacementRequiredRelocationSignals = RelocationSignals
    Context.NeedsFeedbackPlacementGeneration = True
    Context.NeedsCurrentStructuredCutRegeneration = AssignmentCut is not None
    Context.JointPortfolioPrimaryCandidateId = None
    Context.PendingJointPlacementStates.clear()
    ExistingPlacementFingerprints = {Candidate.PlacementFingerprint for Candidate in Context.OrderedPlacements} | Context.ProofGuidedPlacementFingerprints
    Request = _TakeNextDeferredRequest(Context, PreferRelocation=True, RequireExactCutBeforeBroad=AssignmentCut is not None, AllowCapacityPairRepair=CapacityRepairActive)
    if CapacityRepairActive:
        Request = PlacementGenerationRequest(SourceGenerator='row-beam-conflict-relocation', RoutingSpacing=Context.ConfiguredRoutingSpacing, PackingPolicy=replace(Context.Policy.NandPacking, GraphBeamEnabled=False, EnableJointClusterOrientation=True))
    GeneratedUniquePlacement = False
    while Request is not None:
        try:
            GeneratedUniquePlacement = _TryPlacement(Context, Request, FixedRelocationVariant=CapacityRepairRelocationVariant, FixedCandidateSpacing=CapacityRepairRoutingSpacing, MaterializeRoutingResources=False, SkipMandatoryAccessPreScreen=True, PlacementGenerationNotAfter=Context.Deadline.ExpiresAt if CapacityRepairActive else Context.SharedInterfacePlanningDeadline.ExpiresAt, UseCompletePlacementGenerationBudget=True, AllowCapacityPairRepair=CapacityRepairActive)
        except RoutingStageError as Error:
            Context.LastRoutingError = Error
            Context.LastStructuredRoutingError = Error
            if Error.Failure.Reason == RoutingFailureReason.Stagnated and 'AdvancePlacementGenerator' in Error.Failure.RepairActions:
                Request = _TakeNextDeferredRequest(Context, PreferRelocation=True, RequireExactCutBeforeBroad=AssignmentCut is not None)
                continue
            return False
        break
    while True:
        Candidate = next((Value for Value in _BuildCandidateRecords(Context) if Value.PlacementFingerprint not in ExistingPlacementFingerprints), None)
        if Candidate is not None:
            if CapacityRepairConstraint is not None and BuildCapacityRepairGeometryFingerprint(Candidate, CapacityRepairConstraint.Signals) == CapacityRepairConstraint.EquivalentGeometryFingerprint:
                Context.PlacementGenerationDecisions.append({'Result': 'capacity-repair-equivalent-geometry-rejected', 'SourceCandidateId': SourceCandidate.CandidateId, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'CapacityRepairConstraint': CapacityRepairConstraint.ToDictionary()})
                Context.CapacityRepairEquivalentGeometryRejectsByProofFingerprint[CapacityRepairConstraint.SourceProofFingerprint] = Context.CapacityRepairEquivalentGeometryRejectsByProofFingerprint.get(CapacityRepairConstraint.SourceProofFingerprint, 0) + 1
                ExistingPlacementFingerprints.add(Candidate.PlacementFingerprint)
                continue
            Context.ProofGuidedPlacementFingerprints.add(Candidate.PlacementFingerprint)
            Context.GeneratedProofGuidedPlacementFingerprints.add(Candidate.PlacementFingerprint)
            if CapacityRepairConstraint is not None:
                Context.CapacityRepairConstraintByPlacementFingerprint[Candidate.PlacementFingerprint] = CapacityRepairConstraint
                Context.CapacityRepairCandidateByPlacementFingerprint[Candidate.PlacementFingerprint] = Candidate
                Context.CapacityRepairGeometryKindByPlacementFingerprint[Candidate.PlacementFingerprint] = CapacityRepairGeometryKind
                Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-pair-repair-generated', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(CapacityRepairConstraint.Signals), 'ProofComplete': True, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'GeometryFingerprint': BuildCapacityRepairGeometryFingerprint(Candidate, CapacityRepairConstraint.Signals), 'GeometryKind': CapacityRepairGeometryKind, 'CoreSignalCount': len(CapacityRepairConstraint.Signals), 'RepairDomainFingerprint': CapacityRepairConstraint.RepairDomainFingerprint, 'EquivalentGeometryRejectCount': Context.CapacityRepairEquivalentGeometryRejectsByProofFingerprint.get(CapacityRepairConstraint.SourceProofFingerprint, 0), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            Context.ProofGuidedPlacementGenerationCount += 1
            if CapacityRepairConstraint is not None:
                Context.CapacityRepairGeneratedCountByProofFingerprint[CapacityRepairConstraint.SourceProofFingerprint] = Context.CapacityRepairGeneratedCountByProofFingerprint.get(CapacityRepairConstraint.SourceProofFingerprint, 0) + 1
            Context.ProofGuidedPlacementGenerationCountByCore[RelocationCore] = Context.ProofGuidedPlacementGenerationCountByCore.get(RelocationCore, 0) + 1
            Context.RequestedComponentStateFingerprints.add(BuildClusterInterfaceComponentStateFingerprint(Candidate.PlacementFingerprint, ComponentVariant))
            Context.InterfaceCandidateQueue.insert(0, ('prepare-eligibility', len(Context.InterfaceCandidates) + Context.ProofGuidedPlacementGenerationCount, Candidate, 0, ComponentVariant))
            Context.PlacementGenerationDecisions.append({'Result': 'physical-global-proof-guided-placement', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourcePlanFingerprint': Diagnostics.get('PhysicalAssemblyPlanFingerprint', ''), 'ProofKind': 'global-assignment-cut' if AssignmentCut is not None else 'physical-port-unsat-core', 'ProofFingerprint': AssignmentCut.ConflictFingerprint if AssignmentCut is not None else PhysicalPlacementFeedback.ProofFingerprint, 'CapacityRepairConstraint': CapacityRepairConstraint.ToDictionary() if CapacityRepairConstraint is not None else None, 'RelocationSignals': sorted(RelocationSignals), 'RelocationVariant': CapacityRepairRelocationVariant, 'ImmediatePhysicalGeometryFeedback': ImmediatePhysicalGeometryFeedback, 'RoutingSpacing': CapacityRepairRoutingSpacing if CapacityRepairRoutingSpacing is not None else Context.ConfiguredRoutingSpacing, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'LivePlacementStateBound': MaximumPlacementsForRelocationCore, 'PendingImmutablePlacementStateCount': len(Context.PendingJointPlacementStates), 'IncrementalPlacementMaterialization': True, 'ExecutableLegacyRepairCascade': False, 'CapacityRepairGeometryKind': CapacityRepairGeometryKind})
            if CapacityRepairActive and CapacityRepairGeometryKind.startswith('widen-'):
                EnqueueProofGuidedPhysicalPlacement(Context, Failure, SourceCandidate, ComponentVariant)
                IndexedRepairQueue = tuple(enumerate(Context.InterfaceCandidateQueue))
                Context.InterfaceCandidateQueue[:] = [Entry for _Index, Entry in sorted(IndexedRepairQueue, key=lambda Value: (0 if Context.CapacityRepairGeometryKindByPlacementFingerprint.get(Value[1][2].PlacementFingerprint, '').startswith('widen-') else 1, Value[0]))]
                Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'capacity-repair-portfolio-prefetched', 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'GeometryKinds': [CapacityRepairGeometryKind, 'split-channel-endpoints' if CapacityRepairConstraint.RepairLevel == 'channel-capacity' else 'split-relocate'], 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            return True
        if not Context.PendingJointPlacementStates:
            if CapacityRepairConstraint is not None:
                Context.CapacityRepairPortfolioDiagnostics.append({'Result': 'bounded-proof-driven-repair-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'SourceProofFingerprint': CapacityRepairConstraint.SourceProofFingerprint, 'Signals': list(CapacityRepairConstraint.Signals), 'EquivalentGeometryFingerprint': CapacityRepairConstraint.EquivalentGeometryFingerprint, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            return False
        JointState = Context.PendingJointPlacementStates.pop(0)
        try:
            GeneratedUniquePlacement = _TryPlacement(Context, JointState.Request, JointPlacementCandidateIndex=JointState.CandidateIndex, FixedRelocationVariant=JointState.RelocationVariant, FixedCandidateSpacing=JointState.RoutingSpacing, FixedRelocationSignals=JointState.RelocationSignals, FixedRelocationPrioritySignals=JointState.RelocationPrioritySignals, FixedRequiredRelocationSignals=JointState.RequiredRelocationSignals, FixedAssignmentCut=JointState.AssignmentCut, FixedAssignmentConstraints=JointState.AssignmentConstraints, FixedCoordinatedCandidateDiversificationSignals=JointState.CoordinatedCandidateDiversificationSignals, FixedTopologyCutFrontier=JointState.TopologyCutFrontier, MaterializeRoutingResources=False, SkipMandatoryAccessPreScreen=True)
        except RoutingStageError as Error:
            Context.LastRoutingError = Error
            Context.LastStructuredRoutingError = Error
            return False


def EnqueueSingletonLocalFactorDiversification(Context, Failure: RoutingFailure, SourceCandidate: PcbPlacementCandidate, ComponentVariant: int) -> bool:
    """Publish at most two access-distinct local ECO factor domains."""
    Core = BuildPhysicalLocalFactorDiversificationCore(Failure, SourceCandidate)
    if Core is None or Context.Deadline.IsExpired():
        return False
    AttemptCount = Context.LocalFactorDiversificationAttemptCountByProofFingerprint.get(Core.SourceProofFingerprint, 0)
    if AttemptCount >= 2:
        Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-portfolio-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
        return False
    ExistingFingerprints = frozenset(Context.UniquePlacements)
    PublishedCandidates: list[PcbPlacementCandidate] = []
    for Variant in range(AttemptCount, 2):
        Context.LocalFactorDiversificationAttemptCountByProofFingerprint[Core.SourceProofFingerprint] = Variant + 1
        Published = _PublishTransactionalClusterEndpointRepair(Context, SourceCandidate, frozenset((Core.Signal,)), RepairVariant=Variant, RepairClusterCount=1)
        if not Published:
            Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-eco-rejected', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'Variant': Variant, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
            continue
        try:
            CandidateRecords = _BuildCandidateRecords(Context)
        except RoutingStageError as Error:
            Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-eco-incomplete', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'Variant': Variant, 'Failure': Error.Failure.ToDictionary()})
            continue
        NewCandidates = tuple((Candidate for Candidate in CandidateRecords if Candidate.PlacementFingerprint not in ExistingFingerprints and Candidate.SourceGenerator == 'transactional-cluster-endpoint-repair'))
        for Candidate in NewCandidates:
            NewGeometryFingerprint = BuildCapacityRepairGeometryFingerprint(Candidate, (Core.Signal,))
            if NewGeometryFingerprint == Core.LocalGeometryFingerprint:
                Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-geometry-unchanged', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'Variant': Variant, 'PlacementFingerprint': Candidate.PlacementFingerprint})
                continue
            ExistingFingerprints = frozenset((*ExistingFingerprints, Candidate.PlacementFingerprint))
            Context.LocalFactorDiversificationCandidateByPlacementFingerprint[Candidate.PlacementFingerprint] = Candidate
            Context.RequestedComponentStateFingerprints.add(BuildClusterInterfaceComponentStateFingerprint(Candidate.PlacementFingerprint, ComponentVariant))
            PublishedCandidates.append(Candidate)
            Context.LocalFactorDiversificationPortfolioDiagnostics.append({'Result': 'singleton-local-factor-eco-published', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'Variant': Variant, 'PlacementFingerprint': Candidate.PlacementFingerprint, 'LocalGeometryFingerprint': NewGeometryFingerprint, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
    if not PublishedCandidates:
        return False
    Context.InterfaceCandidateQueue[0:0] = [('prepare-eligibility', len(Context.InterfaceCandidates) + Index, Candidate, 0, ComponentVariant) for Index, Candidate in enumerate(PublishedCandidates)]
    return True


def EnqueueOwnedFrontierTopologyRepair(Context, Failure: RoutingFailure, SourceCandidate: PcbPlacementCandidate, ComponentVariant: int) -> bool:
    """Publish two fresh cluster-topology candidates for one proof.

            This deliberately regenerates a complete packed placement.  It
            must not reuse the endpoint ECO, because a contract-independent
            empty owned frontier is a component-fabric contradiction rather
            than a pin-access contradiction.
            """
    Core = BuildPhysicalOwnedFrontierTopologyRepairCore(Failure, SourceCandidate)
    if Core is None or Context.Deadline.IsExpired():
        return False
    AttemptCount = Context.OwnedFrontierTopologyRepairAttemptCountByProofFingerprint.get(Core.SourceProofFingerprint, 0)
    if AttemptCount >= 2:
        Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-portfolio-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
        return False
    ExistingFingerprints = frozenset(Context.UniquePlacements)
    ExistingTopologyFingerprints = frozenset((Candidate.InterfaceTopologyFingerprint for Candidate in Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint.values()))
    PublishedCandidates: list[PcbPlacementCandidate] = []
    TopologyCandidateBaseIndex = len(Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint)
    MaximumTopologyCandidateCount = max(1, Context.Policy.NandPacking.RetainedJointPlacementCandidates * 2)
    if TopologyCandidateBaseIndex >= MaximumTopologyCandidateCount:
        Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-retained-domain-exhausted', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'RetainedStateCount': MaximumTopologyCandidateCount, 'ElapsedSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6)})
        return False
    for TopologyCandidateOffset, (Variant, Kind) in enumerate(((3, 'split-interface-cut'), (12, 'relocate-endpoint-cluster'))):
        if AttemptCount >= 2 or Context.Deadline.IsExpired() or TopologyCandidateBaseIndex + TopologyCandidateOffset >= MaximumTopologyCandidateCount:
            break
        AttemptCount += 1
        Context.OwnedFrontierTopologyRepairAttemptCountByProofFingerprint[Core.SourceProofFingerprint] = AttemptCount
        StartedAt = Context.Services.monotonic()
        TopologyCandidateIndex = TopologyCandidateBaseIndex + TopologyCandidateOffset
        try:
            Candidate = Context.Services.PlacePcbGraph(Context.Netlist, RoutingSpacing=SourceCandidate.RoutingSpacing, PlacementPolicy=Context.Policy.Placement, ClusterPolicy=Context.Policy.Clustering, MaximumBoundaryTerminals=Context.Policy.Organization.MaximumClusterEntrances, MaximumEntrancesPerSignal=Context.Policy.Organization.MaximumClusterEntrancesPerSignal, PackingPolicy=Context.Policy.NandPacking, RelocationSignals=frozenset(Core.Signals), RelocationPrioritySignals=frozenset(Core.Signals), RequiredRelocationSignals=frozenset(Core.Signals), RelocationVariant=Variant, JointPlacementCandidateIndex=TopologyCandidateIndex, AssignmentConstraints=Context.PlacementAssignmentConstraints, EnableClusterBoundaryLeases=True, EnableClusterInterfacePlacementFeasibility=True, CutDrivenClusterRefinementSignals=frozenset(Core.Signals), FocusedCutEpochPlacement=True, WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('OwnedFrontierTopologyRepair', {'SourceCandidateId': SourceCandidate.CandidateId, 'CandidateKind': Kind, 'CoreFingerprint': Core.CoreFingerprint, **Diagnostics}))
            Profile = MeasureMandatoryAccessConflictProfile(Candidate.Placed.PlacedGates, Candidate.SignalOrder)
            MandatoryConflicts = {Resource: set(map(str, Owners)) for Resource, Owners in (*Profile.CrossConflicts, *Profile.SelfConflicts)}
            CandidateTopologyDemand = MeasurePlacementTopologyDemand(Context.TopologyDemand, Candidate, MandatoryConflicts=MandatoryConflicts, MandatoryProfile=Profile)
            CandidateDiagnostics = dict(Candidate.Placed.LocalRouteDiagnostics or {})
            CandidateDiagnostics['__OwnedFrontierTopologyRepair__'] = {'Core': Core.ToDictionary(), 'CandidateKind': Kind, 'SourceCandidateId': SourceCandidate.CandidateId}
            Candidate.Placed.LocalRouteDiagnostics = CandidateDiagnostics
            Fingerprint = BuildPlacementFingerprint(Candidate, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=False)
            RetentionFingerprint = BuildPlacementRetentionFingerprint(Candidate, CandidateTopologyDemand.MandatoryAccessOwnershipFingerprint, IncludeLocalClaims=False)
            CandidateTopologyFingerprint = BuildClusterInterfacePlacementTopologyFingerprint(Candidate, Context.SignalTopologyFingerprints)
            if Fingerprint in ExistingFingerprints or RetentionFingerprint in Context.RetainedPlacementTopologyFingerprints or CandidateTopologyFingerprint in ExistingTopologyFingerprints or (CandidateTopologyFingerprint == Core.SourceTopologyFingerprint):
                Context.OwnedFrontierTopologyRepairEquivalentRejectsByProofFingerprint[Core.SourceProofFingerprint] = Context.OwnedFrontierTopologyRepairEquivalentRejectsByProofFingerprint.get(Core.SourceProofFingerprint, 0) + 1
                Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-equivalent-rejected', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'CandidateKind': Kind, 'PlacementFingerprint': Fingerprint, 'TopologyFingerprint': CandidateTopologyFingerprint, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
                continue
            CandidateResources = Context.Services.BuildRoutingResources(Candidate.Placed, WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('OwnedFrontierTopologyRepairResources', {'CandidateKind': Kind, **Diagnostics}))
        except (RoutingStageError, ValueError) as Error:
            Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-incomplete', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'CandidateKind': Kind, 'Failure': Error.Failure.ToDictionary() if isinstance(Error, RoutingStageError) else str(Error), 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
            continue
        Context.UniquePlacements[Fingerprint] = ('proof-driven-owned-frontier-topology-repair', SourceCandidate.RoutingSpacing, Candidate)
        Context.PlacementRetentionFingerprintByFingerprint[Fingerprint] = RetentionFingerprint
        Context.RetainedPlacementTopologyFingerprints[RetentionFingerprint] = (Fingerprint, 'proof-driven-owned-frontier-topology-repair')
        Context.TopologyDemandByFingerprint[Fingerprint] = CandidateTopologyDemand
        Context.RoutingResourcesByFingerprint[Fingerprint] = CandidateResources
        Context.MaterializedPlacementByFingerprint[Fingerprint] = Candidate
        ExistingFingerprints = frozenset((*ExistingFingerprints, Fingerprint))
        ExistingTopologyFingerprints = frozenset((*ExistingTopologyFingerprints, CandidateTopologyFingerprint))
        CandidateRecord = PcbPlacementCandidate(CandidateId=f'Placement-{Fingerprint[:12]}', SourceGenerator='proof-driven-owned-frontier-topology-repair', RoutingSpacing=SourceCandidate.RoutingSpacing, PlacementFingerprint=Fingerprint, FeedbackScore=(Variant,), BoundaryOverflow=0, PinScarcityCount=0, GuideOverflowPeak=0, GuideOverflowCells=0, PinEscapeConflictCount=0, EstimatedGlobalExtensionNodes=0, EstimatedGlobalExtensionNets=0, PreOwnedNodeCount=0, Placement=Candidate, TopologyDemand=CandidateTopologyDemand, PlacementRetentionFingerprint=RetentionFingerprint, InterfaceTopologyFingerprint=CandidateTopologyFingerprint)
        Context.OwnedFrontierTopologyRepairCandidateByPlacementFingerprint[Fingerprint] = CandidateRecord
        Context.OwnedFrontierTopologyRepairKindByPlacementFingerprint[Fingerprint] = Kind
        Context.RequestedComponentStateFingerprints.add(BuildClusterInterfaceComponentStateFingerprint(Fingerprint, ComponentVariant))
        PublishedCandidates.append(CandidateRecord)
        Context.OwnedFrontierTopologyRepairPortfolioDiagnostics.append({'Result': 'owned-frontier-topology-published', 'SourceCandidateId': SourceCandidate.CandidateId, 'Core': Core.ToDictionary(), 'CandidateKind': Kind, 'PlacementFingerprint': Fingerprint, 'TopologyFingerprint': CandidateTopologyFingerprint, 'ElapsedSeconds': round(Context.Services.monotonic() - StartedAt, 6)})
    if not PublishedCandidates:
        return False
    Context.InterfaceCandidateQueue[0:0] = [('prepare-eligibility', len(Context.InterfaceCandidates) + Index, Candidate, 0, ComponentVariant) for Index, Candidate in enumerate(PublishedCandidates)]
    return True


def RecordPhysicalComponentStageTiming(Context, Stage: str, StartedAt: float, *, Result: str, PlanFingerprint: str='') -> None:
    Context.PhysicalComponentStageTimings.append({'Stage': Stage, 'AttemptIndex': sum((Entry.get('Stage') == Stage for Entry in Context.PhysicalComponentStageTimings)), 'Result': Result, 'DurationSeconds': round(Context.Services.monotonic() - StartedAt, 6), 'ElapsedSinceRoutingStartSeconds': round(Context.Services.monotonic() - Context.Deadline.StartedAt, 6), 'PhysicalAssemblyPlanFingerprint': PlanFingerprint})


def ProveUnboundOwnedSignalFrontier(Context, UnboundProblem: Any) -> None:
    FrontierProofStartedAt = Context.Services.monotonic()
    FrontierProof = ProveClosedComponentOwnedSignalFrontiers(UnboundProblem, DeadlineSeconds=Context.InterfaceDeadline.RemainingSeconds(), WorkCheck=lambda Diagnostics: Context.InterfaceDeadline.RaiseIfExpired('PhysicalUnboundOwnedSignalFrontierEligibility', Diagnostics), CompletedProofCache=Context.ComponentOwnedSignalFrontierProofCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache)
    CoreSignals = SelectContractIndependentOwnedSignalFrontierUnsatCore(UnboundProblem, FrontierProof)
    RecordPhysicalComponentStageTiming(Context, 'PhysicalUnboundOwnedSignalFrontierEligibility', FrontierProofStartedAt, Result='contract-independent-unsatisfiable' if CoreSignals else FrontierProof.Status)
    if not CoreSignals:
        return
    FrontierDiagnostics = dict(FrontierProof.Diagnostics or {})
    CoreFingerprint = str(FrontierDiagnostics.get('LocalUnsatCoreFingerprint', FrontierProof.ProofFingerprint))
    raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalComponentLocalEligibility', AffectedNets=CoreSignals, Detail='the unbound placed component has a port-independent empty owned-signal frontier', RepairActions=(), Diagnostics={**FrontierDiagnostics, 'OwnedSignalFrontierProofComplete': True, 'PortAssignmentProofComplete': True, 'PortAssignmentUnsatCoreMinimal': True, 'PortAssignmentUnsatCoreSignals': list(CoreSignals), 'PortAssignmentUnsatCoreFingerprint': CoreFingerprint, 'DomainFingerprint': str(UnboundProblem.ProblemFingerprint), 'PhysicalPortFactorPreparationEntered': False, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False, 'ImplicitForeignTransitDomainCount': 0}))


def AdmitSymbolicLocalCapacity(Context, Assembly: Any) -> bool:
    """Prove selected local support before exterior routing."""
    LocalCapacityContractFingerprint = BuildStableFingerprint(('closed-component-local-capacity-contract-v1', Assembly.Problem.PlacementFingerprint, Assembly.Problem.Fabric.FabricFingerprint, getattr(Assembly.Problem.ResourceGraph, 'GraphVersion', ''), Assembly.Problem.MaximumPowerDistance, tuple(sorted(((Port.Signal, BuildPhysicalPortLocalContractFingerprint(Port)) for Port in Assembly.Plan.Ports)))))
    if LocalCapacityContractFingerprint in Context.CapacityFeasibleLocalContractFingerprints:
        RecordPhysicalComponentStageTiming(Context, 'PhysicalSymbolicCapacityEligibility', Context.Services.monotonic(), Result='capacity-feasible-cache-hit', PlanFingerprint=Assembly.Plan.PlanFingerprint)
        return True
    AdmissionStartedAt = Context.Services.monotonic()
    Proof = ProveClosedComponentSymbolicCapacityEligibility(Assembly.Problem, DeadlineSeconds=Context.InterfaceDeadline.RemainingSeconds(), WorkCheck=lambda Diagnostics: Context.InterfaceDeadline.RaiseIfExpired('PhysicalSymbolicCapacityEligibility', Diagnostics), CompletedProofCache=Context.ComponentSymbolicCapacityProofCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache, SymbolicNetStateCache=Context.InterfaceResources.PhysicalComponentSymbolicNetStateCache)
    RecordPhysicalComponentStageTiming(Context, 'PhysicalSymbolicCapacityEligibility', AdmissionStartedAt, Result=Proof.Status, PlanFingerprint=Assembly.Plan.PlanFingerprint)
    if Proof.Status == 'capacity-feasible':
        Context.CapacityFeasibleLocalContractFingerprints.add(LocalCapacityContractFingerprint)
        return True
    if Proof.Status != 'architectural-unsatisfiable':
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalSymbolicCapacityEligibility', Detail='the selected local capacity proof did not complete before exterior routing', Diagnostics={'SymbolicCapacityStatus': Proof.Status, 'SymbolicCapacityDiagnostics': dict(Proof.Diagnostics or {}), 'PhysicalAssemblyPlanFingerprint': Assembly.Plan.PlanFingerprint, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False, 'ImplicitForeignTransitDomainCount': 0}))
    ProofDiagnostics = dict(Proof.Diagnostics or {})
    BinaryCore = tuple(sorted(set(map(str, ProofDiagnostics.get('LocalUnsatCoreSignals', ())))))
    if len(BinaryCore) == 2 and len(Context.CompiledSymbolicCapacityBinaryCores) < Context.MaximumCompleteBinaryCoreCertificates and (BinaryCore not in Context.CompiledSymbolicCapacityBinaryCores):
        BinaryCompilationStartedAt = Context.Services.monotonic()
        BinaryCertificate = CompilePhysicalComponentSymbolicPortPairDomain(Assembly.Problem, Context.PreparedEligibility, BinaryCore, DeadlineSeconds=Context.SharedInterfacePlanningDeadline.RemainingSeconds(), WorkCheck=lambda Diagnostics: Context.SharedInterfacePlanningDeadline.RaiseIfExpired('PhysicalComponentBinarySupportCompilation', Diagnostics), NetStateCache=Context.InterfaceResources.PhysicalComponentSymbolicNetStateCache, CompletedCertificateCache=Context.InterfaceResources.PhysicalComponentSymbolicPortPairCertificateCache, CompleteCompatibilityIndexCache=Context.InterfaceResources.PhysicalComponentSymbolicPairCompatibilityIndexCache, RouteClaimsConstructionCache=Context.ComponentRouteClaimsConstructionCache)
        if not BinaryCertificate.Complete:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentBinarySupportCompilation', AffectedNets=BinaryCore, Detail='the learned binary local core did not finish complete support compilation'))
        BinaryCoreClauses, BinaryProjectionDiagnostics = ProjectCompletePhysicalPortPairCertificateToApertureClauses(Context.PreparedEligibility, BinaryCertificate)
        if not BinaryProjectionDiagnostics.get('ApertureProjectionComplete', False):
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PhysicalComponentBinarySupportCompilation', AffectedNets=BinaryCore, Detail='the learned complete binary core could not be projected to aperture clauses', Diagnostics=dict(BinaryProjectionDiagnostics)))
        Context.InterfaceResources.RejectedPhysicalComponentPortReservationSets.update(BinaryCoreClauses)
        Context.CompiledSymbolicCapacityBinaryCores.add(BinaryCore)
        RecordPhysicalComponentStageTiming(Context, 'PhysicalComponentBinarySupportCompilation', BinaryCompilationStartedAt, Result='complete', PlanFingerprint=Assembly.Plan.PlanFingerprint)
        Context.StateAttemptDiagnostics.append({'Result': 'binary-support-core-compiled', 'SignalPair': list(BinaryCore), 'PublishedBinaryClauseCount': len(BinaryCoreClauses), 'BinaryCertificateFingerprint': BinaryCertificate.ProofFingerprint, **BinaryProjectionDiagnostics, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False})
    NoGoodDiagnostics = RecordPhysicalComponentSymbolicCapacityEligibilityNoGood(Proof, Assembly.Plan, Context.InterfaceResources, FactorDomain=Context.PreparedEligibility)
    Context.CumulativeSymbolicCapacityPressureSignals.update(map(str, Proof.Diagnostics.get('LocalUnsatCoreSignals', ()) if isinstance(Proof.Diagnostics, dict) else ()))
    Context.LatestSymbolicCapacityRepairEvidence = BuildSymbolicCapacityRepairEvidence(NoGoodDiagnostics, Context.CumulativeSymbolicCapacityPressureSignals)
    Context.StateAttemptDiagnostics.append({'Result': 'symbolic-capacity-reject-before-global', 'PhysicalAssemblyPlanFingerprint': Assembly.Plan.PlanFingerprint, **NoGoodDiagnostics, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False, 'ImplicitForeignTransitDomainCount': 0})
    return False


def ReplanPhysicalAssemblyWithTiming(Context, RequiredGlobalBoundaryPorts: tuple[Any, ...] | None=None) -> Any:
    raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.PhysicalComponentAssemblyIncomplete, Stage='PreRouteInterfaceSelection', AffectedNets=tuple(sorted(Context.CumulativeSymbolicCapacityPressureSignals)), Detail='the frozen pre-route interface contract was rejected; automatic component replanning is disabled', Diagnostics={'PreRouteInterfaceSelection': Context.PreRouteInterfaceResult.ToDictionary(), 'RequiredGlobalBoundaryPortCount': len(RequiredGlobalBoundaryPorts or ()), 'SymbolicCapacityAssemblyReplanCount': Context.SymbolicCapacityAssemblyReplanCount, 'AutomaticReplanDisabled': True, **Context.LatestSymbolicCapacityRepairEvidence}))
    while True:
        if Context.SymbolicCapacityAssemblyReplanCount >= Context.MaximumSymbolicCapacityAssemblyReplans:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable, Stage='PhysicalSymbolicCapacityPlacementFeedback', AffectedNets=tuple(sorted(Context.CumulativeSymbolicCapacityPressureSignals)), Detail='a complete selected local capacity core rejected the bounded assembly-replan portfolio; advancing to targeted placement feedback', Diagnostics={'SymbolicCapacityPlacementFeedback': True, 'PlacementInterfacePressureSignals': sorted(Context.CumulativeSymbolicCapacityPressureSignals), 'SymbolicCapacityAssemblyReplanCount': Context.SymbolicCapacityAssemblyReplanCount, 'MaximumSymbolicCapacityAssemblyReplans': Context.MaximumSymbolicCapacityAssemblyReplans, **Context.LatestSymbolicCapacityRepairEvidence, 'GlobalPlanningEntered': False, 'LocalCompilationEntered': False}))
        ReplanStartedAt = Context.Services.monotonic()
        try:
            Result = ReplanPhysicalComponentAssembly(Context.ComponentBasePlacement, Resources=Context.InterfaceResources, Deadline=Context.InterfaceDeadline, RequiredGlobalBoundaryPorts=RequiredGlobalBoundaryPorts)
        except RoutingStageError as Error:
            RecordPhysicalComponentStageTiming(Context, 'PhysicalAssemblyReplan', ReplanStartedAt, Result='failed')
            if not Context.CumulativeSymbolicCapacityPressureSignals:
                raise
            Diagnostics = dict(Error.Failure.Diagnostics) if isinstance(Error.Failure.Diagnostics, dict) else {}
            raise RoutingStageError(replace(Error.Failure, Diagnostics={**Diagnostics, 'PlacementInterfacePressureSignals': sorted(Context.CumulativeSymbolicCapacityPressureSignals), 'SymbolicCapacityPlacementFeedback': True, **Context.LatestSymbolicCapacityRepairEvidence})) from Error
        except Exception:
            RecordPhysicalComponentStageTiming(Context, 'PhysicalAssemblyReplan', ReplanStartedAt, Result='failed')
            raise
        RecordPhysicalComponentStageTiming(Context, 'PhysicalAssemblyReplan', ReplanStartedAt, Result='complete', PlanFingerprint=Result.Plan.PlanFingerprint)
        Context.SymbolicCapacityAssemblyReplanCount += 1
        if AdmitSymbolicLocalCapacity(Context, Result):
            return Result
        if RequiredGlobalBoundaryPorts is not None:
            RequiredGlobalBoundaryPorts = None


def ReserveAuthoritativeGlobalChannels(Context, Assembly: Any) -> tuple[Any, RoutedDesign | None]:

    def RebuildFrontierDeferrals() -> None:
        PrunedFrontier, _RejectedRetainedPlanFingerprints = PruneRetainedPhysicalGlobalPlansByRejectedApertureClauses(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier, Context.InterfaceResources.RejectedPhysicalComponentPortReservationSets)
        Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier = PrunedFrontier
        Context.InterfaceResources.DeferredPhysicalComponentPortAssignmentFingerprints = {Entry.Assembly.Plan.PortAssignmentFingerprint for Entry in Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier.values()}

    def RetainIncompleteGlobalPlan(CurrentAssembly: Any, Failure: RoutingFailure) -> dict[str, object]:
        Diagnostics = dict(Failure.Diagnostics or {})
        DependencyValues = FindPhysicalGlobalDiagnosticValues(Diagnostics, 'PhysicalGlobalCandidateRequestDependencyFingerprints')
        RequestDependencies = next((Value for Value in reversed(DependencyValues) if isinstance(Value, dict)), {})
        RemainingValues = FindPhysicalGlobalDiagnosticValues(Diagnostics, 'RemainingRequestCounts')
        RemainingCounts = next((Value for Value in reversed(RemainingValues) if isinstance(Value, dict)), {})
        CandidateContinuationValues = FindPhysicalGlobalDiagnosticValues(Diagnostics, 'PhysicalComponentGlobalCandidateContinuations')
        CandidateContinuationRecords = next((Value for Value in reversed(CandidateContinuationValues) if isinstance(Value, list) and Value), [])
        if not RemainingCounts:
            RemainingCounts = {str(Value.get('Signal', '')): max(0, int(Value.get('RemainingRequestCount', 0))) for Value in CandidateContinuationRecords if isinstance(Value, dict) and str(Value.get('Signal', ''))}
        ApertureValues = FindPhysicalGlobalDiagnosticValues(Diagnostics, 'CertifiedPhysicalComponentApertureDomain')
        ApertureDiagnostics = next((Value for Value in reversed(ApertureValues) if isinstance(Value, dict)), {})
        PortsBySignal = {Port.Signal: Port for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(CurrentAssembly.Plan)}
        CorridorBySignal = {}
        for Domain in Context.InterfaceResources.PhysicalPortCorridorDomainCache.values():
            Port = PortsBySignal.get(Domain.Signal)
            if Port is None or Domain.PortReservationFingerprint != Port.ReservationFingerprint:
                continue
            ExpectedDependency = RequestDependencies.get(Domain.Signal)
            if ExpectedDependency and Domain.RequestDependencyFingerprint != ExpectedDependency:
                continue
            CorridorBySignal[Domain.Signal] = Domain
        CertificateFingerprints = tuple(sorted({str(Value) for Key in ('CertificateFingerprint', 'AccessCertificateFingerprint') for Value in FindPhysicalGlobalDiagnosticValues(Diagnostics, Key) if isinstance(Value, str) and Value} | {str(ApertureDiagnostics.get('DomainFingerprint', ''))}))
        ApertureFingerprint = str(ApertureDiagnostics.get('DomainFingerprint', ''))
        ResumeCursor, CompletedWork = BuildPhysicalGlobalPlanResumeCursorFromDiagnostics(CurrentAssembly.Plan.PlanFingerprint, ApertureFingerprint, Diagnostics)
        Continuation = BuildPhysicalGlobalPlanContinuationState(CurrentAssembly.Plan, RequestDependencies, RemainingCounts, CorridorBySignal.values(), CertificateFingerprints, CompletedWork=CompletedWork, ResumeCursor=ResumeCursor)
        ExistingEntry = Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier.get(CurrentAssembly.Plan.PlanFingerprint)
        RetentionAdmission = ClassifyPhysicalGlobalPlanRetentionAdmission(ApertureDiagnostics, Continuation=Continuation, ExistingEntry=ExistingEntry)
        if not RetentionAdmission['Retained']:
            return RetentionAdmission
        Sequence = int(Context.InterfaceResources.PhysicalGlobalPlanFrontierScheduleSequence)
        Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier = RetainIncompletePhysicalGlobalPlan(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier, CurrentAssembly, Continuation, EnqueuedSequence=Sequence)
        Context.InterfaceResources.PhysicalGlobalPlanFrontierScheduleSequence = Sequence + 1
        RebuildFrontierDeferrals()
        return {**RetentionAdmission, 'RetainedGlobalPlanFrontierSize': len(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier)}
    PreviousGlobalPlanWasRetained = False
    CompleteGlobalPlanRejectionCount = 0

    def SelectRetainedAssembly(*, ClearDeferredAssignments: bool) -> Any:
        nonlocal PreviousGlobalPlanWasRetained
        RebuildFrontierDeferrals()
        Frontier = Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier
        if not Frontier:
            raise ValueError('retained physical global-plan frontier is empty')
        if ClearDeferredAssignments:
            Context.InterfaceResources.DeferredPhysicalComponentPortAssignmentFingerprints.clear()
        Sequence = int(Context.InterfaceResources.PhysicalGlobalPlanFrontierScheduleSequence)
        Entry, UpdatedFrontier = SelectNextRetainedPhysicalGlobalPlan(Frontier, ScheduleSequence=Sequence)
        Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier = UpdatedFrontier
        Context.InterfaceResources.PhysicalGlobalPlanFrontierScheduleSequence = Sequence + 1
        PreviousGlobalPlanWasRetained = True
        return Entry.Assembly

    def SelectFreshOrRetainedAssembly() -> Any:
        nonlocal PreviousGlobalPlanWasRetained
        RebuildFrontierDeferrals()
        Frontier = Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier
        if ShouldScheduleRetainedPhysicalGlobalPlan(Frontier, PreviousPlanWasRetained=PreviousGlobalPlanWasRetained):
            return SelectRetainedAssembly(ClearDeferredAssignments=False)
        try:
            Assembly = ReplanPhysicalAssemblyWithTiming(Context)
            PreviousGlobalPlanWasRetained = False
            return Assembly
        except RoutingStageError as Error:
            Diagnostics = dict(Error.Failure.Diagnostics or {})
            if not Diagnostics.get('DeferredPortAssignmentDomainExhausted', False):
                raise
            Frontier = Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier
            if not Frontier:
                raise
            return SelectRetainedAssembly(ClearDeferredAssignments=True)
    CurrentAssembly = Assembly
    while True:
        if not CurrentAssembly.Plan.SelectedLocalPortSupports:
            CurrentAssembly = BindPhysicalComponentAssemblyLocalPortSupports(CurrentAssembly, RequireFrozenGlobalChannels=False)
        Context.InterfaceResources.FrozenPhysicalComponentAssemblyPlan = CurrentAssembly.Plan
        Context.InterfaceResources.PreparedComponentRoutingProblem = CurrentAssembly.Problem
        Context.InterfaceResources.FrozenPreparedPortalDomainCache = None
        Context.InterfaceResources.FrozenInterfaceGlobalCandidateCache = {}
        Context.InterfaceResources.FrozenInterfaceGlobalCandidateMetadata = {}
        Context.InterfaceResources.FrozenInterfaceGlobalCandidatePlacementIdentity = None
        GlobalPlanningAttemptStartedAt = Context.Services.monotonic()
        GlobalPlanningAttemptResult = 'failed'
        GlobalPlanningAttemptRecorded = False
        GlobalPlanningAttemptPlanFingerprint = CurrentAssembly.Plan.PlanFingerprint
        try:
            ExactGlobalSignals = SelectPhysicalComponentExactGlobalChannelSignals(CurrentAssembly.Plan)
            Context.InterfaceResources.PreparingPhysicalComponentGlobalChannels = True
            Context.InterfaceResources.PhysicalComponentExactGlobalChannelSignals = ExactGlobalSignals
            GlobalPlanningPlacement = replace(Context.ComponentBasePlacement, Placed=PreparePhysicalComponentGlobalPlanningPlacement(Context.ComponentBasePlacement.Placed, CurrentAssembly.Problem, CurrentAssembly.Plan, LocalSupportTemplate=None))
            if Context.InterfaceDeadline.IsExpired():
                raise RoutingStageError(BuildPhysicalAssemblyPlanningIncompleteFailure(Context.InterfaceStageSchedule, RemainingSeconds=Context.InterfaceDeadline.RemainingSeconds(), GlobalPlanningEntered=True))
            GlobalPlanningDeadline = BuildPhysicalGlobalPlanYieldDeadline(Context.InterfaceDeadline, len(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier), CurrentPlanWasRetained=PreviousGlobalPlanWasRetained)
            GlobalPlanningSeconds = GlobalPlanningDeadline.RemainingSeconds()
            GlobalPlanningPolicy = replace(Context.Policy, RuntimeBudgetSeconds=GlobalPlanningSeconds, AdaptiveRouting=replace(Context.Policy.AdaptiveRouting, MaximumRuntimeSeconds=min(Context.Policy.AdaptiveRouting.MaximumRuntimeSeconds, GlobalPlanningSeconds)))
            GlobalChannelDesign = Context.Services.RoutePcbDesign(GlobalPlanningPlacement, Policy=GlobalPlanningPolicy, Deadline=GlobalPlanningDeadline, Resources=Context.InterfaceResources)
            BoundAssembly = BindPhysicalComponentAssemblyGlobalChannels(CurrentAssembly, GlobalChannelDesign, Context.InterfaceResources.ResourceGraph)
            Context.InterfaceResources.PreparingPhysicalComponentGlobalChannels = False
            Context.InterfaceResources.FrozenPhysicalComponentAssemblyPlan = BoundAssembly.Plan
            Context.InterfaceResources.PreparedComponentRoutingProblem = BoundAssembly.Problem
            ForeignPortalPlacement = replace(GlobalPlanningPlacement, Placed=FreezePhysicalAssemblyGlobalChannels(GlobalPlanningPlacement.Placed, BoundAssembly.Plan, GlobalChannelDesign))
            ValidatePhysicalComponentForeignPortalSupport(ForeignPortalPlacement, Resources=Context.InterfaceResources, Policy=GlobalPlanningPolicy, Deadline=GlobalPlanningDeadline)
            BoundAssembly = BindPhysicalComponentAssemblyLocalPortSupports(BoundAssembly)
            GlobalPlanningAttemptResult = 'reserved'
        except RoutingStageError as GlobalPlanningError:
            FrozenPostClosurePortalHandoffTelemetry = BuildFrozenPostClosurePortalHandoffTelemetry(Context.InterfaceResources, Context.InterfaceResources.PreparedPhysicalComponentPortFactorDomain, CurrentAssembly.Plan)
            GlobalPlanningFailure = replace(GlobalPlanningError.Failure, Diagnostics={**dict(GlobalPlanningError.Failure.Diagnostics or {}), 'FrozenPostClosurePortalHandoff': FrozenPostClosurePortalHandoffTelemetry})
            ClassifiedFailure = ClassifyPhysicalComponentGlobalPlanningFailure(GlobalPlanningFailure, CurrentAssembly.Plan, DeadlineExpired=Context.InterfaceDeadline.IsExpired())
            if ClassifiedFailure.Reason == RoutingFailureReason.ComponentChannelCapacityUnsatisfiable and (not Context.InterfaceDeadline.IsExpired()) and ClassifiedFailure.Diagnostics.get('AssemblyPlanReassignmentAllowed', True):
                CompleteGlobalPlanRejectionCount += 1
                GlobalPlanningAttemptResult = 'complete-plan-rejected'
                Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier.pop(CurrentAssembly.Plan.PlanFingerprint, None)
                RebuildFrontierDeferrals()
                NoGoodDiagnostics = RecordPhysicalComponentGlobalPlanNoGood(ClassifiedFailure, CurrentAssembly.Plan, Context.InterfaceResources, ShouldStop=Context.InterfaceDeadline.IsExpired)
                Context.StateAttemptDiagnostics.append({'Result': 'global-planning-reject-physical-plan', 'GlobalPlanSource': 'retained' if PreviousGlobalPlanWasRetained else 'fresh', 'PhysicalAssemblyPlanFingerprint': CurrentAssembly.Plan.PlanFingerprint, 'RejectedPortAssignmentFingerprint': CurrentAssembly.Plan.PortAssignmentFingerprint, **NoGoodDiagnostics, 'UnderlyingFailure': ClassifiedFailure.ToDictionary(), 'LocalCompilationEntered': False, 'LocalTemplateReopened': False, 'ImplicitForeignTransitDomainCount': 0})
                RecordPhysicalComponentStageTiming(Context, 'AuthoritativeGlobalReserve', GlobalPlanningAttemptStartedAt, Result=GlobalPlanningAttemptResult, PlanFingerprint=GlobalPlanningAttemptPlanFingerprint)
                GlobalPlanningAttemptRecorded = True
                CurrentAssembly = SelectFreshOrRetainedAssembly()
                continue
            if ClassifiedFailure.Reason == RoutingFailureReason.PhysicalComponentAssemblyIncomplete:
                RetentionDiagnostics = RetainIncompleteGlobalPlan(CurrentAssembly, ClassifiedFailure)
                GlobalPlanningAttemptResult = 'incomplete-plan-retained' if RetentionDiagnostics['Retained'] else 'incomplete-plan-not-retained'
                Context.StateAttemptDiagnostics.append({'Result': 'global-planning-retain-plan' if RetentionDiagnostics['Retained'] else 'global-planning-incomplete-plan-not-retained', 'GlobalPlanSource': 'retained' if PreviousGlobalPlanWasRetained else 'fresh', 'PhysicalAssemblyPlanFingerprint': CurrentAssembly.Plan.PlanFingerprint, 'PortAssignmentFingerprint': CurrentAssembly.Plan.PortAssignmentFingerprint, 'RetainedGlobalPlanFrontierSize': len(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier), 'CursorResumeAvailable': bool(RetentionDiagnostics.get('CursorResumeAvailable', False)), 'CompleteAssignmentCutProof': False, 'NoGoodRecorded': False, 'RetentionAdmission': RetentionDiagnostics, 'UnderlyingFailure': ClassifiedFailure.ToDictionary(), 'ImplicitForeignTransitDomainCount': 0})
                if Context.InterfaceDeadline.IsExpired():
                    SharedPlanningExpired = Context.SharedInterfacePlanningDeadline.IsExpired()
                    raise RoutingStageError(replace(ClassifiedFailure, Diagnostics={**dict(ClassifiedFailure.Diagnostics or {}), 'PlacementWorkSliceExpired': not SharedPlanningExpired, 'PlacementInterfacePressureSignals': list(ClassifiedFailure.AffectedNets), 'SharedPlanningDeadline': Context.SharedInterfacePlanningDeadline.ToDictionary()})) from GlobalPlanningError
                if not RetentionDiagnostics['Retained']:
                    raise RoutingStageError(replace(ClassifiedFailure, Diagnostics={**dict(ClassifiedFailure.Diagnostics or {}), 'RetentionAdmission': RetentionDiagnostics, 'RetainedGlobalPlanFrontierSize': len(Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier)})) from GlobalPlanningError
                RecordPhysicalComponentStageTiming(Context, 'AuthoritativeGlobalReserve', GlobalPlanningAttemptStartedAt, Result=GlobalPlanningAttemptResult, PlanFingerprint=GlobalPlanningAttemptPlanFingerprint)
                GlobalPlanningAttemptRecorded = True
                CurrentAssembly = SelectFreshOrRetainedAssembly()
                continue
            raise RoutingStageError(ClassifiedFailure) from GlobalPlanningError
        finally:
            if not GlobalPlanningAttemptRecorded:
                RecordPhysicalComponentStageTiming(Context, 'AuthoritativeGlobalReserve', GlobalPlanningAttemptStartedAt, Result=GlobalPlanningAttemptResult, PlanFingerprint=GlobalPlanningAttemptPlanFingerprint)
            Context.InterfaceResources.PreparingPhysicalComponentGlobalChannels = False
            Context.InterfaceResources.PhysicalComponentExactGlobalChannelSignals = frozenset()
        Context.InterfaceResources.PreparedPhysicalComponentAssembly = BoundAssembly
        Context.InterfaceResources.PreparedComponentRoutingProblem = BoundAssembly.Problem
        Context.InterfaceResources.FrozenPhysicalComponentAssemblyPlan = BoundAssembly.Plan
        Context.InterfaceResources.RetainedPhysicalGlobalPlanFrontier.pop(BoundAssembly.Plan.PlanFingerprint, None)
        RebuildFrontierDeferrals()
        Context.SuccessfulGlobalPlanWasRetained = PreviousGlobalPlanWasRetained
        return (BoundAssembly, GlobalChannelDesign)


def CheckCandidateValidation(Context, Diagnostics: dict[str, object]) -> None:
    Context.AttemptDeadline.RaiseIfExpired('PlacementCandidateValidation', {'CandidateId': Context.CandidateRecord.CandidateId, 'AdaptiveAttemptStartedAt': Context.AttemptStarted, 'AdaptiveAttemptExpiresAt': Context.AdaptiveAttemptExpiresAt, **Diagnostics})

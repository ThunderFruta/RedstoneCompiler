"""Initialization, candidate generation, and pre-route setup phases."""

from __future__ import annotations

from typing import Any
from PhysicalDesign.Contracts.Placement import ClusterInterfaceAssignment
from PhysicalDesign.Contracts.Component import RoutedComponentTemplate
from PhysicalDesign.Contracts.Results import RoutedDesign
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingFailure, RoutingStageError
from PhysicalDesign.Runtime.Reliability import RoutingDeadline
from PhysicalDesign.Geometry.Placement import PlacedDesign
from PhysicalDesign.Placement.Engine.Clusters import PcbPlacement
from PhysicalDesign.Placement.Engine.Constraints import PlacementAssignmentConstraintSet
from .Demand import BuildPlacementGenerationPlan, BuildTopologyDemandPressureProfile, BuildTopologyDemandProfile, ComputeInterfaceStateCountBound, ExactStatePlacementEvaluation, ResolveJointPlacementPortfolioTrigger
from .Feedback import (
    BuildSignalLocalIncidenceFingerprints,
    BuildSignalTopologyFingerprints,
)
from .Portfolios import DeferredActivePortfolioAssignmentCut, MandatoryAccessPortfolioEvidence, MandatoryAccessPortfolioIdentity, PendingJointPlacementState
from .Preparation import RequiresDenseBoundaryRoutingReserve
from .Results import PcbResult
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
)


def InitializePlacementFlow(Context):
    Context.Module = Context.Netlist.Modules[Context.Netlist.Top]
    Context.NandGateCount = sum(((Context.Kind.value if hasattr(SetPlacementFlowState(Context, 'Kind', getattr(Gate, 'Kind', 'NAND')), 'value') else str(Context.Kind)) == 'NAND' for Gate in Context.Module.Gates))
    Context.TopologyDemand = BuildTopologyDemandProfile(Context.Module)
    Context.SignalTopologyFingerprints = BuildSignalTopologyFingerprints(Context.Module)
    Context.SignalLocalIncidenceFingerprints = (
        BuildSignalLocalIncidenceFingerprints(Context.Module)
    )
    Context.InterfaceStateCountBound = ComputeInterfaceStateCountBound(len(Context.SignalTopologyFingerprints), Context.TopologyDemand, Context.NandGateCount)
    Context.TopologyPressure = BuildTopologyDemandPressureProfile(Context.TopologyDemand, Context.Policy.Organization.MaximumClusterEntrances)
    Context.DenseBoundaryRoutingReserve = RequiresDenseBoundaryRoutingReserve(Context.TopologyDemand, Context.Policy)
    Context.Deadline = RoutingDeadline.Start(Context.Policy.RuntimeBudgetSeconds)
    Context.Started = Context.Deadline.StartedAt
    Context.Services.ValidateNandOnlyDesign(Context.Netlist)
    if not Context.Module.Gates:
        Context.EmptyPlaced = PlacedDesign(Module=Context.Module, PlacedGates=[])
        Context.EmptyRouted = RoutedDesign(Module=Context.Module, PlacedGates=[], Wires=[], Supports=[], RepeaterInputFacings={}, NetWires={})
        return PcbResult(Placed=Context.EmptyPlaced, Routed=Context.EmptyRouted, Footprint=0, EstimatedBlocks=0, Width=0, Depth=0, Policy=Context.Policy, Technology=Context.Technology, RequestedStrategy=Context.RequestedStrategy.value, UsedStrategy=Context.UsedStrategy.value)
    Context.RoutingSpacing = Context.Policy.Placement.RoutingSpacing
    Context.ConfiguredRoutingSpacing = Context.RoutingSpacing
    Context.PlacementGenerationFailures: list[dict[str, object]] = []
    Context.PlacementGenerationDecisions: list[dict[str, object]] = [{'Result': 'topology-demand-profile', 'Trigger': 'reconvergent-access-pressure' if Context.TopologyPressure.ReconvergentAccessPressure else 'scale-geometry-pressure' if Context.TopologyPressure.ScaleGeometryPressure else 'none', 'EnableInitialJointOrientation': Context.TopologyDemand.EnableInitialJointOrientation, 'Profile': Context.TopologyDemand.ToDictionary(), 'Pressure': Context.TopologyPressure.ToDictionary()}]
    Context.LastStructuredPlacementFailure: RoutingFailure | None = None
    Context.LastPlacementAccessIncompleteFailure: RoutingFailure | None = None
    Context.LastPlacementAccessUnsatisfiableFailure: RoutingFailure | None = None
    Context.PlacementAccessDomainsByProblemFingerprint: dict[str, tuple[Any, ...]] = {}
    Context.PlacementAccessSolveResultsByProblemFingerprint: dict[str, Any] = {}
    Context.RejectedPlacementAccessProblemFingerprints: set[str] = set()
    Context.PendingPlacementAccessDirectOnly = False
    Context.UniquePlacements: dict[str, tuple[str, int, PcbPlacement]] = {}
    Context.FeedbackByFingerprint: dict[str, Any] = {}
    Context.RoutingResourcesByFingerprint: dict[str, Any] = {}
    Context.RoutingResourcesByCandidateId: dict[str, Any] = {}
    Context.FrozenClusterInterfaceAssignmentsByPlacementFingerprint: dict[str, ClusterInterfaceAssignment] = {}
    Context.FrozenPreparedPortalDomainCachesByPlacementFingerprint: dict[str, Any] = {}
    Context.PreRoutedClusterInterfaceDesignsByPlacementFingerprint: dict[str, RoutedDesign] = {}
    Context.RoutedComponentTemplatesByPlacementFingerprint: dict[str, RoutedComponentTemplate] = {}
    Context.PortableRawPortalGeometryCaches: tuple[Any, ...] = ()
    Context.MaximumPortableRawPortalGeometryCaches = 8
    Context.MaterializedPlacementByFingerprint: dict[str, PcbPlacement] = {}
    Context.TopologyDemandByFingerprint: dict[str, TopologyDemandProfile] = {}
    Context.PlacementRetentionFingerprintByFingerprint: dict[str, str] = {}
    Context.RetainedPlacementTopologyFingerprints: dict[str, tuple[str, str]] = {}
    Context.RejectedPlacementRetentionFingerprints: set[str] = set()
    Context.SeenTransactionalEndpointRepairFingerprints: set[str] = set()
    Context.SeenTransactionalEndpointRepairRetentionFingerprints: set[str] = set()
    Context.ExactStatePlacementEvaluationCache: dict[str, ExactStatePlacementEvaluation] = {}
    Context.JointPlacementStateByPlacementFingerprint: dict[str, PendingJointPlacementState] = {}
    Context.MandatoryAccessPortfolioEvidenceByIdentity: dict[MandatoryAccessPortfolioIdentity, MandatoryAccessPortfolioEvidence] = {}
    Context.MandatoryAccessPortfolioEvidenceByRecipeIdentity: dict[MandatoryAccessPortfolioIdentity, MandatoryAccessPortfolioEvidence] = {}
    Context.ConsumedStrongMandatoryAccessRepairIdentities: set[MandatoryAccessPortfolioIdentity] = set()
    Context.PendingStrongMandatoryAccessRepair = False
    Context.StrongMandatoryAccessRepairMaterializationPending = False
    Context.PendingJointPlacementStates: list[PendingJointPlacementState] = []
    Context.PendingTopologyCutEpoch: TopologyCutEpochIdentity | None = None
    Context.OpenedTopologyCutEpochs: set[TopologyCutEpochIdentity] = set()
    Context.MaterializedJointPlacementStateKeys: set[tuple[PendingJointPlacementPortfolioIdentity, int]] = set()
    Context.JointPortfolioGenerationNotAfterByIdentity: dict[PendingJointPlacementPortfolioIdentity, float] = {}
    Context.ActiveJointPortfolioIdentityFingerprint = ''
    Context.JointPlacementStateEvents: list[dict[str, object]] = []
    Context.JointPortfolioSliceSeconds: float | None = None
    Context.JointPortfolioPrimaryCandidateId: str | None = None
    Context.PlacementAttemptFailures: list[dict[str, object]] = []
    Context.LastRoutingError: Exception | None = None
    Context.LastStructuredRoutingError: RoutingStageError | None = None
    Context.LastCompletedAssignmentCutError: RoutingStageError | None = None
    Context.PlacementAssignmentCutHistory: list[RoutingAssignmentCut] = []
    Context.DeferredActivePortfolioAssignmentCuts: list[DeferredActivePortfolioAssignmentCut] = []
    Context.CutSourcePlacementByFingerprint: dict[str, PcbPlacement] = {}
    Context.CandidateStarvationPlacementHistory: list[CandidateStarvationPlacementEvidence] = []
    Context.PlacementRepeatedCandidateStarvationSignals: frozenset[str] = frozenset()
    Context.CurrentPlacementAssignmentCut: RoutingAssignmentCut | None = None
    Context.PlacementAssignmentConstraints = PlacementAssignmentConstraintSet()
    Context.PlacementCoordinatedCandidateDiversificationSignals: frozenset[str] = frozenset()
    Context.PlacementClusterPinBankRepairSignals: frozenset[str] = frozenset()
    Context.PlacementRepeatedLeaseGeometrySignals: frozenset[str] = frozenset()
    Context.PostPinBankRepairEpochActive = False
    Context.InternalPinBankGeometryRepairActive = False
    Context.RotatedMacroAncestorTargetedEpochPending = False
    Context.RequiredDistinctPinBankOwnershipFingerprint = ''
    Context.PendingSamePlacementRoutingControlRetry: SamePlacementRoutingControlRetryState | None = None
    Context.ConsumedPairedLeaseRepairProfileFingerprints: set[str] = set()
    Context.NeedsCurrentStructuredCutRegeneration = False
    Context.NeedsFeedbackPlacementGeneration = False
    Context.GenerationPlan = BuildPlacementGenerationPlan(Context.Policy, PreferPackedPlacements=Context.Policy.NegotiatedRouting.Enabled and Context.Policy.NandPacking.Enabled and Context.Policy.NandPacking.DeferUnpackedOracle, PrioritizeSeparatedPacking=Context.TopologyPressure.ScaleGeometryPressure, EnableInitialJointOrientation=Context.TopologyDemand.EnableInitialJointOrientation, EnableCompactDirectOnlyOrientation=False, PreserveDirectOnlyJointPortfolio=Context.TopologyDemand.RequiresJointPortfolio)
    if Context.GenerationPlan.PrimaryRequests:
        Context.ConfiguredRoutingSpacing = Context.GenerationPlan.PrimaryRequests[0].RoutingSpacing
    Context.PlacementGenerationAttempts = 0
    Context.DeferredRequestIndex = 0
    Context.ConsumedDeferredRequestIndexes: set[int] = set()
    Context.PlacementRelocationSignals: frozenset[str] = frozenset()
    Context.PlacementRelocationPrioritySignals: frozenset[str] = frozenset()
    Context.PlacementRequiredRelocationSignals: frozenset[str] = frozenset()
    Context.LastRelocationSignalsUsed: frozenset[str] = frozenset()
    Context.LastRelocationPrioritySignalsUsed: frozenset[str] = frozenset()
    Context.LastRequiredRelocationSignalsUsed: frozenset[str] = frozenset()
    Context.LastAssignmentCutFingerprintUsed = ''
    Context.LastAssignmentConstraintFingerprintUsed = ''
    Context.RelocationGenerationCount = 0
    Context.TotalRelocationGenerationCount = 0
    Context.BaselinePackedGateArea: int | None = None
    Context.RejectedPlacementFingerprints: set[str] = set()
    Context.ProactiveRelocationRequested = False
    Context.BestMandatoryAccessConflictKey: tuple[object, ...] | None = None
    Context.TerminalConstraintEpochRefreshPerformed = False
    Context.TerminalConstraintEpochPortfolioNeedsMaterialization = False
    Context.TerminalConstraintEpochPrimaryCandidateId: str | None = None
    Context.TerminalConstraintEpochPortfolioIdentityFingerprint = ''
    Context.TerminalConstraintEpochAuthoritativeAccessConflictObserved = False
    Context.JointPortfolioTriggered = ResolveJointPlacementPortfolioTrigger(False, Context.TopologyDemand)
    Context.AssignmentCutUnspecified = object()

from dataclasses import replace
from typing import Any
from PhysicalDesign.Contracts.Placement import TrackAssignmentPreparation
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Runtime.Reliability import BuildStableFingerprint
from PhysicalDesign.Redstone.Rules.Geometry import ForkRoutingResourcesWithSharedStaticGeometry
from PhysicalDesign.Placement.PreRouteInterface import DerivedRoutingEnvelope, PlacementAccessDemand
from PhysicalDesign.Placement.Access.Fabric import AttachPlacementAccessFabric, BuildPlacementAccessFabric
from PhysicalDesign.Placement.Access.Geometry import BuildDerivedPerimeterFabricShell, DerivedPerimeterFabricShell
from .Candidates import PcbPlacementCandidate, PreRouteFabricDescriptor
from .Demand import SelectDerivedPrimaryPlacementRequests
from .Preparation import BuildDerivedRoutingEnvelopeDomain, BuildPlacementAccessDemand, IsDerivedSingleComponentPlacementSource, PrepareDerivedPlacementForFrozenAccessContract
from .Results import PcbProgress
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
)
from .RoutingAttempts import (
    _BuildCandidateRecords,
)
from .PlacementAttempts import (
    _TakeNextDeferredRequest,
    _TryPlacement,
)


def MaterializeInitialPendingJointPlacementState(Context) -> bool:
    """Try one retained exact state before declaring placement empty."""
    if (
        Context.UniquePlacements
        or not Context.PendingJointPlacementStates
        or Context.Deadline.IsExpired()
    ):
        return False
    State = Context.PendingJointPlacementStates.pop(0)
    Context.JointPlacementStateEvents.append({
        "CandidateIndex": State.CandidateIndex,
        "Status": "initial-materializing",
        "SourceGenerator": State.Request.SourceGenerator,
        "RoutingSpacing": State.RoutingSpacing,
    })
    _TryPlacement(
        Context,
        State.Request,
        JointPlacementCandidateIndex=State.CandidateIndex,
        FixedRelocationVariant=State.RelocationVariant,
        FixedCandidateSpacing=State.RoutingSpacing,
        FixedRelocationSignals=State.RelocationSignals,
        FixedRelocationPrioritySignals=State.RelocationPrioritySignals,
        FixedRequiredRelocationSignals=State.RequiredRelocationSignals,
        FixedAssignmentCut=State.AssignmentCut,
        FixedAssignmentConstraints=State.AssignmentConstraints,
        FixedCoordinatedCandidateDiversificationSignals=(
            State.CoordinatedCandidateDiversificationSignals
        ),
        FixedTopologyCutFrontier=State.TopologyCutFrontier,
        MaterializeRoutingResources=False,
        SkipMandatoryAccessPreScreen=True,
    )
    return True


def MaterializeInitialConflictRelocation(Context) -> bool:
    """Try one bounded relocation when every initial state conflicts."""
    if (
        Context.UniquePlacements
        or not Context.ProactiveRelocationRequested
        or Context.Deadline.IsExpired()
    ):
        return False
    RetargetInitialConflict = (
        getattr(Context, "TotalRelocationGenerationCount", 0) == 1
        and Context.PlacementRelocationSignals
        != Context.LastRelocationSignalsUsed
    )
    Request = (
        next(
            (
                CandidateRequest
                for CandidateRequest in Context.GenerationPlan.DeferredRequests
                if CandidateRequest.SourceGenerator
                == "row-beam-conflict-relocation"
            ),
            None,
        )
        if RetargetInitialConflict
        else _TakeNextDeferredRequest(Context, PreferRelocation=True)
    )
    if Request is None:
        return False
    if RetargetInitialConflict:
        Context.PlacementGenerationDecisions.append({
            "Result": "initial-conflict-relocation-retargeted",
            "ConflictSignals": sorted(Context.PlacementRelocationSignals),
            "PriorConflictSignals": sorted(Context.LastRelocationSignalsUsed),
        })
    _TryPlacement(Context, Request)
    return True


def MaterializeInitialPlacementAccessDirectOnly(Context) -> bool:
    """Try the existing no-local-route variant after an exact access core."""
    if (
        Context.UniquePlacements
        or not Context.PendingPlacementAccessDirectOnly
        or Context.Deadline.IsExpired()
    ):
        return False
    HasDirectOnlyRequest = any(
        Index not in Context.ConsumedDeferredRequestIndexes
        and Request.SourceGenerator == 'row-beam-direct-only'
        for Index, Request in enumerate(Context.GenerationPlan.DeferredRequests)
    )
    Context.PendingPlacementAccessDirectOnly = False
    if not HasDirectOnlyRequest:
        return False
    Request = _TakeNextDeferredRequest(Context, PreferDirectOnly=True)
    if Request is None or Request.SourceGenerator != 'row-beam-direct-only':
        raise RuntimeError(
            'direct-only placement access repair selected another generator'
        )
    Context.PlacementGenerationDecisions.append({
        'Result': 'placement-access-direct-only-materializing',
        'Reason': (
            'a complete access core implicated legacy pre-owned local routes'
        ),
    })
    _TryPlacement(Context, Request)
    return True


def SelectEmptyPlacementFailure(Context) -> RoutingFailure:
    """Preserve bounded access uncertainty ahead of generic placement errors."""
    return (
        getattr(Context, 'LastPlacementAccessIncompleteFailure', None)
        or getattr(Context, 'LastPlacementAccessUnsatisfiableFailure', None)
        or getattr(Context, 'LastStructuredPlacementFailure', None)
        or RoutingFailure(
            Reason=RoutingFailureReason.PlacementOverlap,
            Stage='Placement',
            Detail='no exact-legal placement candidate was generated',
        )
    )


def GeneratePlacementCandidates(Context):
    if Context.ProgressCallback is not None:
        Context.ProgressCallback(PcbProgress(Completed=0, Total=1, Workers=0, Valid=0, BestBlocks=None, BestWidth=None, BestDepth=None, BestFootprint=None, Failed=0, Stage=f'spacing {Context.RoutingSpacing} | placing clustered NAND graph'))
    Context.IncumbentRequests = tuple(Context.GenerationPlan.PrimaryRequests[:1])
    for Context.Request in Context.IncumbentRequests:
        if Context.Deadline.IsExpired():
            break
        _TryPlacement(Context, Context.Request)
    Context.SinglePackedComponent = bool(Context.UniquePlacements) and all((len(Placement.Clusters) == 1 for _Source, _Spacing, Placement in Context.UniquePlacements.values()))
    Context.IncumbentPlacement = next((Placement for _Source, _Spacing, Placement in Context.UniquePlacements.values())) if Context.SinglePackedComponent and Context.IncumbentRequests else None
    Context.DerivedPrimaryRequests = SelectDerivedPrimaryPlacementRequests(Context.GenerationPlan, Context.SinglePackedComponent, Incumbent=Context.IncumbentPlacement, Module=Context.Module if Context.IncumbentPlacement is not None else None, WorkCheck=(lambda Diagnostics: Context.Deadline.RaiseIfExpired('DerivedGraphCorePortfolio', Diagnostics)) if Context.IncumbentPlacement is not None else None)
    for Context.Request in Context.DerivedPrimaryRequests[len(Context.IncumbentRequests):]:
        if Context.Deadline.IsExpired():
            break
        _TryPlacement(Context, Context.Request, CountPlacementGenerationAttempt=False, QueueRetainedJointPortfolioStates=False)
    while not Context.UniquePlacements:
        if MaterializeInitialPlacementAccessDirectOnly(Context):
            continue
        if MaterializeInitialPendingJointPlacementState(Context):
            continue
        if MaterializeInitialConflictRelocation(Context):
            continue
        break
    if not Context.UniquePlacements:
        Context.BaseFailure = SelectEmptyPlacementFailure(Context)
        Context.FailureDiagnostics = dict(Context.BaseFailure.Diagnostics or {})
        Context.FailureDiagnostics.update({'PlacementGenerationFailures': Context.PlacementGenerationFailures, 'PlacementGenerationDecisions': Context.PlacementGenerationDecisions, 'PlacementAttempts': Context.PlacementAttemptFailures, 'Deadline': Context.Deadline.ToDictionary()})
        raise RoutingStageError(RoutingFailure(Reason=Context.BaseFailure.Reason, Stage=Context.BaseFailure.Stage, AffectedNets=Context.BaseFailure.AffectedNets, Resources=Context.BaseFailure.Resources, Locations=Context.BaseFailure.Locations, RepairActions=Context.BaseFailure.RepairActions, Detail=Context.BaseFailure.Detail, Diagnostics=Context.FailureDiagnostics))
    Context.CandidateRecords = _BuildCandidateRecords(Context)
    Context.FabricCandidateRecords: list[PcbPlacementCandidate] = []
    Context.PreRouteFabricDescriptorsByCandidateId: dict[str, PreRouteFabricDescriptor] = {}
    Context.PlacementAccessEvidenceByCandidateId: dict[str, tuple[Any, Any]] = {}
    Context.PreRejectedCandidateEvidence: dict[str, dict[str, object]] = {}
    Context.PrePlacementTrackPreparationWitnesses: dict[str, TrackAssignmentPreparation] = {}
    Context.CandidateEnvelopeDomains: list[tuple[PcbPlacementCandidate, PlacementAccessDemand, tuple[DerivedRoutingEnvelope, ...]]] = []
    for Context.Candidate in Context.CandidateRecords:
        Context.Demand = BuildPlacementAccessDemand(Context.Candidate.Placement, int(getattr(Context.Candidate.TopologyDemand, 'PeakBoundaryDemand', 0)), Context.Technology)
        Context.Envelopes = BuildDerivedRoutingEnvelopeDomain(Context.Demand, Context.Candidate.Placement)
        Context.CandidateEnvelopeDomains.append((Context.Candidate, Context.Demand, Context.Envelopes))
    for Context.Candidate, Context.Demand, Context.Envelopes in Context.CandidateEnvelopeDomains:
        if len(Context.Candidate.Placement.Clusters) != 1:
            Context.ExistingLayerCount = max(Context.Demand.MinimumRoutingLayerCount, min(Context.Demand.MaximumRoutingLayerCount, int(Context.Candidate.Placement.LayerCount)))
            Context.Envelope = next((Value for Value in Context.Envelopes if Value.RoutingLayerCount == Context.ExistingLayerCount))
            Context.FabricCandidateRecords.append(replace(Context.Candidate, RoutingEnvelope=Context.Envelope))
            continue
        Context.CandidateResources = Context.RoutingResourcesByFingerprint.get(Context.Candidate.PlacementFingerprint)
        if Context.CandidateResources is None:
            Context.CandidateResources = Context.Services.BuildRoutingResources(Context.Candidate.Placement.Placed, Technology=Context.Technology)
        Context.IsCertifiedIncumbent = Context.Candidate.SourceGenerator == 'row-beam'
        Context.IsDerivedPerimeterCandidate = not Context.IsCertifiedIncumbent and IsDerivedSingleComponentPlacementSource(Context.Candidate.SourceGenerator)
        Context.StaticAccessPlacement = PrepareDerivedPlacementForFrozenAccessContract(Context.Candidate.Placement) if Context.IsDerivedPerimeterCandidate else Context.Candidate.Placement
        Context.StaticAccessResources = Context.Services.BuildRoutingResources(Context.StaticAccessPlacement.Placed, Technology=Context.Technology)
        Context.AccessByEnvelopeIdentity: dict[tuple[int, int, str], Any] = {}
        for Context.Envelope in Context.Envelopes:
            Context.EnvelopeCandidateId = f'{Context.Candidate.CandidateId}:layers-{Context.Envelope.RoutingLayerCount}'
            Context.FabricPlacement = replace(Context.Candidate.Placement, LayerCount=Context.Envelope.RoutingLayerCount)
            Context.AccessFabricPlacement = replace(Context.StaticAccessPlacement, LayerCount=Context.Envelope.RoutingLayerCount) if Context.IsDerivedPerimeterCandidate else Context.FabricPlacement
            Context.TopologyKind = 'fixed-access-band-v1' if Context.IsCertifiedIncumbent else 'derived-perimeter-access-v1'
            Context.RingTrackCount = 0 if Context.IsCertifiedIncumbent else Context.Envelope.AccessRingTrackCount
            Context.DescriptorCandidate = replace(Context.Candidate, CandidateId=Context.EnvelopeCandidateId, Placement=Context.AccessFabricPlacement, RoutingEnvelope=Context.Envelope)
            if Context.SinglePackedComponent:
                Context.Shell: DerivedPerimeterFabricShell | None = None
                Context.SlotAssignment: Any | None = None
                if Context.IsDerivedPerimeterCandidate:
                    Context.SlotAssignment = getattr(Context.AccessFabricPlacement, 'DerivedPerimeterSlotAssignment', None) or getattr(Context.AccessFabricPlacement.Placed, 'DerivedPerimeterSlotAssignment', None)
                    if Context.SlotAssignment is not None and bool(getattr(Context.SlotAssignment, 'Success', False)) and bool(getattr(Context.SlotAssignment, 'Complete', False)):
                        Context.Shell = BuildDerivedPerimeterFabricShell(Context.AccessFabricPlacement, Resources=Context.StaticAccessResources, Technology=Context.Technology, AccessRingTrackCount=Context.RingTrackCount, AccessLength=Context.Envelope.AccessLength)
                Context.PrefixBounds = Context.Shell.OuterBounds if Context.Shell is not None else tuple(map(int, getattr(Context.SlotAssignment, 'Bounds', ()))) if Context.IsDerivedPerimeterCandidate and Context.SlotAssignment is not None and (len(getattr(Context.SlotAssignment, 'Bounds', ())) == 4) else Context.Envelope.EnvelopeBounds
                Context.PrefixWidth = Context.PrefixBounds[2] - Context.PrefixBounds[0] + 1
                Context.PrefixDepth = Context.PrefixBounds[3] - Context.PrefixBounds[1] + 1
                Context.Descriptor = PreRouteFabricDescriptor(Candidate=Context.DescriptorCandidate, StaticResources=Context.StaticAccessResources, TopologyKind=Context.TopologyKind, AccessRingTrackCount=Context.RingTrackCount, DeriveLegalEscapeWorkLimit=Context.IsDerivedPerimeterCandidate, ObjectivePrefix=(Context.PrefixWidth * Context.PrefixDepth, max(Context.PrefixWidth, Context.PrefixDepth), Context.Envelope.RoutingLayerCount), MaterializationInputFingerprint=BuildStableFingerprint({'Placement': Context.DescriptorCandidate.PlacementFingerprint, 'Envelope': Context.Envelope.EnvelopeFingerprint, 'TopologyKind': Context.TopologyKind, 'AccessRingTrackCount': Context.RingTrackCount, 'Shell': Context.Shell.ShellFingerprint if Context.Shell is not None else '', 'SlotAssignment': str(getattr(Context.SlotAssignment, 'AssignmentFingerprint', ''))}), Shell=Context.Shell)
                Context.ExistingDescriptor = Context.PreRouteFabricDescriptorsByCandidateId.get(Context.EnvelopeCandidateId)
                if Context.ExistingDescriptor is not None:
                    raise RuntimeError('pre-route fabric portfolio repeats a candidate id')
                Context.PreRouteFabricDescriptorsByCandidateId[Context.EnvelopeCandidateId] = Context.Descriptor
                Context.FabricCandidateRecords.append(Context.DescriptorCandidate)
                continue
            Context.EnvelopeResources = ForkRoutingResourcesWithSharedStaticGeometry(Context.StaticAccessResources)
            Context.AccessDomainKey = (Context.Envelope.RoutingLayerCount, 0 if Context.IsCertifiedIncumbent else Context.Envelope.AccessRingTrackCount, 'fixed-access-band-v1' if Context.IsCertifiedIncumbent else 'derived-perimeter-access-v1')
            Context.Fabric = Context.AccessByEnvelopeIdentity.get(Context.AccessDomainKey)
            if Context.Fabric is None:
                Context.Fabric = BuildPlacementAccessFabric(
                    Context.AccessFabricPlacement,
                    Resources=Context.EnvelopeResources,
                    Technology=Context.Technology,
                    AccessLength=(
                        Context.Technology.AccessLength
                        if Context.Policy.PlacementAccess.Enabled
                        else Context.Envelope.AccessLength
                    ),
                    TopologyKind=Context.TopologyKind,
                    AccessRingTrackCount=Context.RingTrackCount,
                    CompleteRouteSignals=frozenset(),
                    DeriveLegalEscapeWorkLimit=(
                        Context.IsDerivedPerimeterCandidate
                    ),
                    WorkCheck=lambda Diagnostics: (
                        Context.Deadline.RaiseIfExpired(
                            'PrePlacementAccessFabric',
                            Diagnostics,
                        )
                    ),
                    PinAccessWitness=(
                        Context.AccessFabricPlacement
                        .SelectedPinAccessWitness
                    ),
                    FixedPinAccessSolve=(
                        Context.AccessFabricPlacement.PlacementAccessSolve
                    ),
                    RequireSelectedPinAccessWitness=(
                        Context.Policy.PlacementAccess.Enabled
                    ),
                )
                Context.AccessByEnvelopeIdentity[Context.AccessDomainKey] = Context.Fabric
            Context.PlacementAccessEvidenceByCandidateId[Context.EnvelopeCandidateId] = (Context.Fabric, None)
            Context.AttachedPlacement = AttachPlacementAccessFabric(Context.AccessFabricPlacement if Context.IsDerivedPerimeterCandidate else Context.FabricPlacement, Context.Fabric)
            Context.FabricCandidateRecords.append(replace(Context.Candidate, CandidateId=Context.EnvelopeCandidateId, Placement=Context.AttachedPlacement, RoutingEnvelope=Context.Envelope))
            Context.RoutingResourcesByCandidateId[Context.EnvelopeCandidateId] = Context.EnvelopeResources
        Context.RoutingResourcesByFingerprint[Context.Candidate.PlacementFingerprint] = Context.CandidateResources
    Context.CandidateRecords = Context.FabricCandidateRecords
import os
from PhysicalDesign.Routing.Pcb import PrepareTrackAssignment
from PhysicalDesign.Routing.Assignment.TemplateAssignment import RawTrackAssignmentMaterialization, RawTrackAssignmentPortfolio, RawTrackAssignmentPortfolioTemplate, RawTrackAssignmentSelection, SolveRawTrackAssignmentPortfolioWithContext
from PhysicalDesign.Contracts.Results import RoutedDesign
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Runtime.Reliability import BuildStableFingerprint
from PhysicalDesign.Placement.PreRouteInterface import PreRouteInterfaceProblem, PreRouteInterfaceSelection, PreRouteInterfaceTemplate, SolvePreRouteInterfaceProblem
from PhysicalDesign.Placement.Engine.Clusters import PcbPlacement
from .Candidates import PcbPlacementCandidate
from .Preparation import BuildFrozenEnvelopeRoutingPolicy, PlacementNeedsDemandDiversity, RequiresExactClusterInterfaceSolve
from functools import partial
from .State import (
    PlacementFlowState,
    SetPlacementFlowState,
)
from .RoutingAttempts import (
    MaterializeRawTemplate,
    PublishPreRouteTemplate,
    SolvePrePlacementCapacityProblem,
)


def PreparePlacementRouting(Context):
    Context.PreRouteTemplates: list[PreRouteInterfaceTemplate] = []
    Context.PreRouteObjectiveByCandidateId: dict[str, tuple[int, ...]] = {}
    if Context.SinglePackedComponent:
        Context.PrePlacementTrackPreparations: list[dict[str, object]] = []
        Context.PrePlacementTrackFeasible: list[PcbPlacementCandidate] = []
    else:
        Context.PrePlacementTrackPreparations, Context.PrePlacementTrackFeasible = SolvePrePlacementCapacityProblem(Context, Context.CandidateRecords)
        for Context.Candidate in Context.CandidateRecords:
            Context.Fabric, Context.Assignment = Context.PlacementAccessEvidenceByCandidateId.get(Context.Candidate.CandidateId, (Context.Candidate.Placement.PlacementAccessFabric, Context.Candidate.Placement.PlacementAccessAssignment))
            PublishPreRouteTemplate(Context, Context.Candidate, Context.Fabric, Context.PrePlacementTrackPreparationWitnesses.get(Context.Candidate.CandidateId, Context.Assignment))
    Context.RawTrackAssignmentResult: RawTrackAssignmentSelection | None = None
    Context.RawTrackAssignmentMaterializations: dict[str, RawTrackAssignmentMaterialization] = {}
    if Context.SinglePackedComponent:
        if not Context.CandidateRecords:
            Context.PreRouteInterfaceResult = PreRouteInterfaceSelection(ProblemFingerprint=BuildStableFingerprint(('pre-route-interface-incomplete-fabric-domain-v1', tuple((Candidate.CandidateId for Candidate in Context.CandidateRecords)))), SelectionFingerprint='', SelectedTemplateIds=(), SelectedWitnessIds=(), Objective=(), ExpansionCount=0, Success=False, Complete=False, Unsatisfiable=False, IncompleteReason='incomplete-template-domain')
        else:
            Context.TemplateById: dict[str, PreRouteInterfaceTemplate] = {}
            Context.CandidateById = {Candidate.CandidateId: Candidate for Candidate in Context.CandidateRecords}
            Context.CandidateIndexById = {Candidate.CandidateId: Index for Index, Candidate in enumerate(Context.CandidateRecords)}
            Context.RawPortfolioTemplates: list[RawTrackAssignmentPortfolioTemplate] = []
            for Context.Candidate in Context.CandidateRecords:
                Context.FabricDescriptor = Context.PreRouteFabricDescriptorsByCandidateId.get(Context.Candidate.CandidateId)
                if Context.FabricDescriptor is None:
                    raise RuntimeError('pre-route candidate is missing its fixed fabric descriptor')
                Context.RawPortfolioTemplates.append(RawTrackAssignmentPortfolioTemplate(TemplateId=Context.Candidate.CandidateId, Objective=Context.FabricDescriptor.ObjectivePrefix, MaterializationInputFingerprint=BuildStableFingerprint({'FabricDescriptor': Context.FabricDescriptor.MaterializationInputFingerprint, 'TrackAssignmentExpansionCap': Context.Policy.TrackAssignment.MaximumAssignmentExpansions})))
            Context.RawTrackAssignmentResult = SolveRawTrackAssignmentPortfolioWithContext(RawTrackAssignmentPortfolio(Templates=tuple(Context.RawPortfolioTemplates), MaximumAssignmentExpansions=Context.Policy.TrackAssignment.MaximumAssignmentExpansions, NonExhaustiveTemplateDomain=True), partial(MaterializeRawTemplate, Context), Deadline=Context.Deadline, WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('PreRouteInterfaceSelection', Diagnostics))
            Context.SelectedTemplate = Context.TemplateById.get(Context.RawTrackAssignmentResult.SelectedTemplateId)
            Context.SelectedWitness = Context.SelectedTemplate.Witnesses[0] if Context.SelectedTemplate is not None and len(Context.SelectedTemplate.Witnesses) == 1 else None
            Context.PreRouteInterfaceResult = PreRouteInterfaceSelection(ProblemFingerprint=Context.RawTrackAssignmentResult.ProblemFingerprint, SelectionFingerprint=Context.RawTrackAssignmentResult.SelectionFingerprint, SelectedTemplateIds=(('__placement__', Context.SelectedTemplate.TemplateId),) if Context.RawTrackAssignmentResult.Success and Context.SelectedTemplate is not None else (), SelectedWitnessIds=(('__placement__', Context.SelectedWitness.WitnessId),) if Context.RawTrackAssignmentResult.Success and Context.SelectedWitness is not None else (), Objective=Context.RawTrackAssignmentResult.SelectedObjective, ExpansionCount=Context.RawTrackAssignmentResult.ExpansionCount, Success=Context.RawTrackAssignmentResult.Success, Complete=Context.RawTrackAssignmentResult.Complete, Unsatisfiable=Context.RawTrackAssignmentResult.Unsatisfiable, IncompleteReason=Context.RawTrackAssignmentResult.IncompleteReason, FirstConflictResourceIds=tuple(map(str, Context.RawTrackAssignmentResult.FirstConflictResourceIndices)))
    else:
        Context.PreRouteInterfaceResult = SolvePreRouteInterfaceProblem(PreRouteInterfaceProblem(Templates=tuple(Context.PreRouteTemplates), NonExhaustiveDomain=True), WorkCheck=lambda Diagnostics: Context.Deadline.RaiseIfExpired('PreRouteInterfaceSelection', Diagnostics))
    Context.SelectedPreRouteTemplateIds = {TemplateId for _ComponentId, TemplateId in Context.PreRouteInterfaceResult.SelectedTemplateIds}
    Context.SelectedPreRouteCandidates = [Candidate for Candidate in (Context.CandidateRecords if Context.SinglePackedComponent else Context.PrePlacementTrackFeasible) if Candidate.CandidateId in Context.SelectedPreRouteTemplateIds]
    if not Context.PreRouteInterfaceResult.Success or len(Context.SelectedPreRouteCandidates) != 1:
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='PreRouteInterfaceSelection', Detail='the fixed local interface domain did not select exactly one complete placement contract', RepairActions=(), Diagnostics={'PrePlacementTrackPreparations': Context.PrePlacementTrackPreparations, 'PlacementDomainComplete': False, 'PreRouteInterfaceSelection': Context.PreRouteInterfaceResult.ToDictionary(), 'RawTrackAssignmentSelection': Context.RawTrackAssignmentResult.ToDictionary() if Context.RawTrackAssignmentResult is not None else None, 'RawTrackAssignmentMaterializations': [Result.ToDictionary() for _CandidateId, Result in sorted(Context.RawTrackAssignmentMaterializations.items())], 'PreRouteFabricPortfolio': [Descriptor.ToDictionary() for _CandidateId, Descriptor in sorted(Context.PreRouteFabricDescriptorsByCandidateId.items())]}))
    Context.SelectedPreRouteCandidate = Context.SelectedPreRouteCandidates[0]
    Context.SelectedCandidateResources = Context.RoutingResourcesByCandidateId.get(Context.SelectedPreRouteCandidate.CandidateId) or Context.RoutingResourcesByFingerprint.get(Context.SelectedPreRouteCandidate.PlacementFingerprint)
    if Context.SelectedCandidateResources is None:
        Context.SelectedCandidateResources = Context.Services.BuildRoutingResources(Context.SelectedPreRouteCandidate.Placement.Placed, Technology=Context.Technology)
        Context.RoutingResourcesByCandidateId[Context.SelectedPreRouteCandidate.CandidateId] = Context.SelectedCandidateResources
        Context.RoutingResourcesByFingerprint[Context.SelectedPreRouteCandidate.PlacementFingerprint] = Context.SelectedCandidateResources
    Context.SelectedPreparationPolicy = BuildFrozenEnvelopeRoutingPolicy(Context.Policy, Context.SelectedPreRouteCandidate.RoutingEnvelope) if Context.SelectedPreRouteCandidate.RoutingEnvelope is not None and len(Context.SelectedPreRouteCandidate.Placement.Clusters) == 1 else Context.Policy
    Context.SelectedRequiresExactClusterInterfaceSolve = bool(
        not Context.SinglePackedComponent
        and RequiresExactClusterInterfaceSolve(
            Context.SelectedPreRouteCandidate.TopologyDemand,
            Context.SelectedPreRouteCandidate.Placement.Placed,
            Context.Policy,
        )
    )
    if Context.SinglePackedComponent:
        if Context.RawTrackAssignmentResult is None or Context.RawTrackAssignmentResult.Preparation is None:
            raise RuntimeError('selected raw pre-route result is missing its frozen track-assignment witness')
        Context.SelectedTrackPreparation = Context.RawTrackAssignmentResult.Preparation
    else:
        Context.SelectedTrackPreparation = Context.PrePlacementTrackPreparationWitnesses.get(Context.SelectedPreRouteCandidate.CandidateId)
        if Context.SelectedTrackPreparation is None:
            if not Context.SelectedRequiresExactClusterInterfaceSolve:
                try:
                    Context.SelectedTrackPreparation = PrepareTrackAssignment(
                        Context.SelectedPreRouteCandidate.Placement,
                        Resources=Context.SelectedCandidateResources,
                        Policy=Context.SelectedPreparationPolicy,
                        Deadline=Context.Deadline,
                        DeferClusterBoundaryLeaseUntilCapacityPrecheck=False,
                    )
                except RoutingStageError as Error:
                    raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='SelectedPreRouteTrackPreparation', AffectedNets=Error.Failure.AffectedNets, Resources=Error.Failure.Resources, Detail='the selected fixed local-access contract could not build one complete authoritative portal/track domain', RepairActions=(), Diagnostics={'PreRouteInterfaceSelection': Context.PreRouteInterfaceResult.ToDictionary(), 'SelectedCandidate': Context.SelectedPreRouteCandidate.ToDictionary(), 'AuthoritativePreparationFailure': Error.Failure.ToDictionary(), 'PrePlacementTrackPreparations': Context.PrePlacementTrackPreparations, 'PlacementDomainComplete': False})) from Error
    if (
        Context.SelectedTrackPreparation is not None
        and (
            not Context.SelectedTrackPreparation.Success
            or not Context.SelectedTrackPreparation.Complete
        )
    ):
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage='SelectedPreRouteTrackPreparation', AffectedNets=Context.SelectedTrackPreparation.ConflictSignals, Resources=tuple(map(str, Context.SelectedTrackPreparation.ConflictResourceIndices)), Detail='the selected fixed local-access contract has no complete authoritative portal/track witness', RepairActions=(), Diagnostics={'PreRouteInterfaceSelection': Context.PreRouteInterfaceResult.ToDictionary(), 'RawTrackAssignmentSelection': Context.RawTrackAssignmentResult.ToDictionary() if Context.RawTrackAssignmentResult is not None else None, 'SelectedCandidate': Context.SelectedPreRouteCandidate.ToDictionary(), 'SelectedAuthoritativeTrackPreparation': Context.SelectedTrackPreparation.ToDictionary(), 'PrePlacementTrackPreparations': Context.PrePlacementTrackPreparations, 'PlacementDomainComplete': False}))
    if Context.SelectedTrackPreparation is not None:
        Context.PrePlacementTrackPreparationWitnesses[
            Context.SelectedPreRouteCandidate.CandidateId
        ] = Context.SelectedTrackPreparation
    Context.PrePlacementTrackFeasible = [Context.SelectedPreRouteCandidate]
    Context.PrePlacementTrackPreparationByCandidateId = Context.PrePlacementTrackPreparationWitnesses
    Context.OrderedPlacements = Context.PrePlacementTrackFeasible[:1]
    Context.ExactClusterInterfaceSolveEnabled = not Context.SinglePackedComponent and any((RequiresExactClusterInterfaceSolve(Candidate.TopologyDemand, Candidate.Placement.Placed, Context.Policy) for Candidate in Context.CandidateRecords))
    if Context.ExactClusterInterfaceSolveEnabled:
        Context.PlacementGenerationDecisions.append({'Result': 'exact-cluster-interface-gate-enabled', 'ExecutableLegacyRepairCascade': False, 'MaximumPlacementStateCount': min(6, Context.Policy.NandPacking.RetainedJointPlacementCandidates), 'Trigger': 'measured-interface-pressure'})
    Context.PlacementGenerationDecisions.append({'Result': 'deferred-placement-alternatives', 'Reason': 'route exact-legal primary candidates before paying for structure-aware or spacing recovery', 'DemandPressurePresent': PlacementNeedsDemandDiversity(Context.CandidateRecords, Context.ConfiguredRoutingSpacing), 'DeferredCount': len(Context.GenerationPlan.DeferredRequests) - len(Context.ConsumedDeferredRequestIndexes)})
    Context.OrderedPlacements = Context.PrePlacementTrackFeasible[:1]
    Context.PlacementFeedback = [Candidate.ToDictionary() for Candidate in Context.CandidateRecords]
    Context.Placement = Context.OrderedPlacements[0].Placement
    Context.RoutingSpacing = Context.OrderedPlacements[0].RoutingSpacing
    if bool(os.environ.get('RCS_DEBUG_AUTHORITATIVE')):
        for Context.CandidateRecord in Context.OrderedPlacements:
            print(f'[debug] authoritative: retained placement id={Context.CandidateRecord.CandidateId} source={Context.CandidateRecord.SourceGenerator} score={Context.CandidateRecord.FeedbackScore} boundary_overflow={Context.CandidateRecord.BoundaryOverflow} pin_scarcity={Context.CandidateRecord.PinScarcityCount} packed={bool(Context.CandidateRecord.Placement.PackedClusters)}', flush=True)
    Context.Routed = None
    Context.SelectedCandidate: PcbPlacementCandidate | None = None
    Context.LastAttemptedCandidate: PcbPlacementCandidate | None = None
    Context.RoutingPercentageSelectionEnabled = Context.Policy.MaterialObjective.OptimizeRoutingPercentage and Context.NandGateCount >= Context.Policy.MaterialObjective.MinimumRoutingPercentageSelectionNandCount
    Context.RoutedCandidates: list[tuple[tuple[float, int, int, int, int, int, int, str], PcbPlacementCandidate, PcbPlacement, RoutedDesign, dict[str, object]]] = []

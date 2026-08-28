"""Compile the exact domains used by cluster boundary lease assignment.

The first phase normalizes terminal portal choices and retains the legacy
bounded path as a distinct policy.  The complete-domain phase owns exhaustive
certificate handling and typed work-limit outcomes.  Pattern construction and
the final capacity-one search live separately in ``BoundaryLeasePatterns``.
"""

from __future__ import annotations

from ..Contracts.Core import Position3
from ..Contracts.Placement import ClusterInterfaceAssignment
from ..Failures import RoutingFailure
from ..Failures import RoutingFailureReason
from ..Failures import RoutingStageError
from ..ResourceGraph import FindClaimConflicts
from ..ResourceGraph import FindSelfClaimConflicts
from ..ResourceGraph import PinAccessPortal
from ..ResourceGraph import PortalReservation
from ..ResourceGraph import RoutingResourceClaims
from dataclasses import replace
from .CandidateCache import BuildClusterInterfaceAccessDomainFingerprint, BuildClusterInterfaceProblem, BuildClusterInterfaceReservationAssignmentFingerprint
from functools import partial

from .BoundaryLeaseState import (
    BoundaryLeaseReturn,
    BoundaryLeaseState,
    SetBoundaryLeaseState,
)
from .BoundaryLeaseHelpers import (
    AccessPatternFingerprint,
    AccessPatternIsAdmissible,
    AddJointCutPatterns,
    BuildConflictComponents,
    BuildZeroDomainConflictGraph,
    CandidateOffset,
    ClusterInterfaceAccessPattern,
    Compatible,
    CompatiblePatternIndices,
    CompleteForwardDomain,
    CompleteOrderedValues,
    CompleteSelectionAdmissible,
    ComponentSatisfiable,
    DiverseTerminalDomain,
    LegacyCompatible,
    MergePatternClaims,
    PatternSelectionFingerprint,
    Search,
    SearchBundleComponent,
    SearchCompleteDomains,
    SearchLegacy,
    SelectLayerSignatureDiverseIndices,
)


def PrepareBoundaryLeaseDomains(Context):
    """Assign hard access-plus-stem leases without imposing one net layer.

    A boundary bundle needs ownership of its physical exits, not a restriction
    that every endpoint of the net use the same routing layer.  This exact
    capacity-one matcher therefore treats each requested terminal as a domain
    while retaining same-net sharing and rejecting foreign claim overlap.
    """
    if Context.MaximumExpansions < 1:
        raise ValueError('MaximumExpansions must be positive')
    if Context.RequiredInterfaceLayer is not None and Context.RequiredInterfaceLayer < 0:
        raise ValueError('RequiredInterfaceLayer cannot be negative')
    Context.SignalCandidateDomainOffsets = {str(Signal): int(Offset) for Signal, Offset in (Context.SignalCandidateDomainOffsets or {}).items()}
    Context.RequiredPatternFingerprintsBySignal = {str(Signal): str(Fingerprint) for Signal, Fingerprint in (Context.RequiredPatternFingerprintsBySignal or {}).items() if Fingerprint}
    Context.RequiredReservationsBySignal = {Signal: tuple((Reservation for Reservation in Context.RequiredReservations if Reservation.Signal == Signal)) for Signal in {Reservation.Signal for Reservation in Context.RequiredReservations}}
    Context.RequiredReservationByTerminal = {(Reservation.Signal, Reservation.Terminal): Reservation for Reservation in Context.RequiredReservations}
    Context.NogoodPatternFingerprintsBySignal: dict[str, frozenset[str]] = {Signal: frozenset((Nogood.PatternFingerprint for Nogood in Context.CandidateRealizabilityNogoods if Nogood.Signal == Signal)) for Signal in {Nogood.Signal for Nogood in Context.CandidateRealizabilityNogoods}}
    Context.PriorityInterfaceCutSignals = frozenset((str(Signal) for Signal in Context.PriorityInterfaceCutSignals))
    Context.Domains: dict[tuple[str, Position3], tuple[tuple[int, int, PinAccessPortal, RoutingResourceClaims], ...]] = {}
    for Context.Signal, Context.Terminal in sorted({(Key[0], Key[1]) for Key in Context.Portals}):
        Context.Profile = Context.Profiles.get(Context.Signal)
        if Context.Profile is None:
            continue
        Context.Values = []
        for (Context.CandidateSignal, Context.CandidateTerminal, Context.Layer), Context.Candidates in Context.Portals.items():
            if (Context.CandidateSignal, Context.CandidateTerminal) != (Context.Signal, Context.Terminal):
                continue
            if Context.RequireCompleteClusterInterfaceDomain and Context.RequiredInterfaceLayer is not None and (Context.Layer != Context.RequiredInterfaceLayer):
                continue
            for Context.Portal in Context.Candidates:
                Context.FixedAccessPath = (Context.Profile.SourceAccessPath if Context.Terminal == Context.Profile.Root else Context.Profile.TargetAccessPaths.get(Context.Terminal, ())) if Context.UseCompleteClusterInterfaceAccess else ()
                Context.FirstSegment = tuple(dict.fromkeys((*Context.FixedAccessPath, *(Context.Portal.Path if Context.UseCompleteClusterInterfaceAccess else Context.Portal.Path[:2]))))
                Context.Claims = Context.Resources.ResourceGraph.BuildRouteClaims(Context.FirstSegment)
                Context.RequiredReservation = Context.RequiredReservationByTerminal.get((Context.Signal, Context.Terminal))
                if Context.RequiredReservation is not None and (Context.Layer != Context.RequiredReservation.Layer or tuple(Context.Portal.Path) != tuple(Context.RequiredReservation.FirstSegment) or Context.Claims != Context.RequiredReservation.Claims):
                    continue
                if not FindSelfClaimConflicts({Context.Signal: Context.Claims}):
                    Context.Values.append((Context.Portal.Cost, Context.Layer, Context.Portal, Context.Claims))
        if not Context.Values:
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.BoundaryEscapeInfeasible, Stage='ClusterBoundaryLease', AffectedNets=(Context.Signal,), Detail='boundary lease terminal has no legal portal stem', Diagnostics={'ReservationPurpose': 'cluster-boundary-lease', 'Signal': Context.Signal, 'Terminal': list(Context.Terminal), 'CandidateCounts': {Context.Signal: 0}, 'ClusterInterfaceDomainComplete': Context.RequireCompleteClusterInterfaceDomain, 'OwnershipSearchComplete': Context.RequireCompleteClusterInterfaceDomain, 'ConflictGraph': BuildZeroDomainConflictGraph(Context, Context.Signal)}))
        Context.Domains[Context.Signal, Context.Terminal] = tuple(sorted(Context.Values, key=lambda Value: (Value[0], Value[1], Value[2].PortalId)))
    Context.AccessDomainFingerprint = BuildClusterInterfaceAccessDomainFingerprint(Context.Domains)
    Context.InterfaceProblem = BuildClusterInterfaceProblem(Context.Domains, PlacementVariantFingerprint='', OwnershipFingerprint='', DomainComplete=Context.RequireCompleteClusterInterfaceDomain)
    Context.Order = tuple(sorted(Context.Domains, key=lambda Key: (len(Context.Domains[Key]), Key[0], Key[1])))
    Context.DiversifyLayers = len(Context.Order) >= 16
    Context.LayerCount = max((Layer for Values in Context.Domains.values() for _Cost, Layer, _Portal, _Claims in Values), default=-1) + 1
    Context.PreferredLayerByTerminal = {Key: Index % max(1, Context.LayerCount) for Index, Key in enumerate(Context.Order)}


def SolveLegacyBoundaryLeaseDomain(Context):
    if not Context.UseCompleteClusterInterfaceAccess:
        Context.LegacySelected: dict[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]] = {}
        Context.LegacyExpansionCount = 0
        Context.LegacyFailedCut: set[str] = set()
        if not SearchLegacy(Context, 0):
            Context.Affected = tuple(sorted(Context.LegacyFailedCut or {Key[0] for Key in Context.Order}))
            raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.BoundaryEscapeInfeasible, Stage='ClusterBoundaryLease', AffectedNets=Context.Affected, Detail=f'no capacity-one first-segment lease assignment within {Context.MaximumExpansions} deterministic expansions', Diagnostics={'ReservationPurpose': 'cluster-boundary-lease', 'ExpansionCount': Context.LegacyExpansionCount, 'MaximumExpansions': Context.MaximumExpansions, 'LeaseTerminalCount': len(Context.Order), 'CompleteClusterInterfaceAccess': False}))
        Context.LegacyFiltered = {Key: () for Key in Context.Portals}
        Context.LegacyReservations = []
        for Context.SlotIndex, ((Context.Signal, Context.Terminal), (Context._Cost, Context.Layer, Context.Portal, Context.Claims)) in enumerate(sorted(Context.LegacySelected.items())):
            Context.LegacyFiltered[Context.Signal, Context.Terminal, Context.Layer] = (Context.Portal,)
            Context.LegacyReservations.append(PortalReservation(Signal=Context.Signal, Terminal=Context.Terminal, Layer=Context.Layer, SlotIndex=Context.SlotIndex, PortalId=Context.Portal.PortalId, Claims=Context.Claims, Purpose='cluster-boundary-lease', FirstSegment=Context.Portal.Path[:2]))
        raise BoundaryLeaseReturn((Context.LegacyFiltered, tuple(Context.LegacyReservations)))


def PrepareCompleteBoundaryLeaseDomain(Context):
    Context.Selected: dict[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]] = {}
    Context.ExpansionCount = 0
    Context.FailedCut: set[str] = set()
    Context.BundleSignals = tuple(sorted({Signal for Signal, _Terminal in Context.Order}))
    Context.PriorityInterfaceCutSignals = frozenset((Signal for Signal in Context.PriorityInterfaceCutSignals if Signal in Context.BundleSignals))
    Context.FixedAccessClaimsBySignal = {Signal: Context.Resources.ResourceGraph.BuildRouteClaims(tuple(dict.fromkeys((*Context.Profiles[Signal].SourceAccessPath, *(Position for Target in Context.Profiles[Signal].Targets for Position in Context.Profiles[Signal].TargetAccessPaths.get(Target, ())))))) for Signal in Context.BundleSignals}
    Context.FixedAccessConflicts = FindClaimConflicts(Context.FixedAccessClaimsBySignal)
    Context.FixedAccessIncompatibleEdges = tuple(sorted({tuple(sorted((FirstSignal, SecondSignal))) for Owners in Context.FixedAccessConflicts.values() for FirstIndex, FirstSignal in enumerate(Owners) for SecondSignal in Owners[FirstIndex + 1:] if FirstSignal != SecondSignal}))
    Context.MaximumPortalChoicesPerTerminal = 6
    Context.MaximumPatternsPerSignal = min(64, max(16, Context.MaximumExpansions // max(1, len(Context.BundleSignals) * 4)))


def SolveCompleteBoundaryLeaseDomain(Context):
    if Context.RequireCompleteClusterInterfaceDomain:
        Context.CompleteSelected: dict[tuple[str, Position3], tuple[int, int, PinAccessPortal, RoutingResourceClaims]] = {}
        Context.CompleteExpansionCount = 0
        Context.CompleteWorkExhausted = False
        Context.CompleteFailedSignals: set[str] = set()
        Context.TerminalKeysBySignal = {Signal: tuple((Key for Key in Context.Order if Key[0] == Signal)) for Signal in Context.BundleSignals}
        Context.CompleteDomains = {Key: CompleteOrderedValues(Context, Key) for Key in Context.Order}
        Context.CompleteFeasible = SearchCompleteDomains(Context, Context.Order)
        if not Context.CompleteFeasible:
            Context.Affected = tuple(sorted(Context.CompleteFailedSignals or set(Context.BundleSignals)))
            Context.FailureReason = RoutingFailureReason.ClusterInterfaceSolveIncomplete if Context.CompleteWorkExhausted else RoutingFailureReason.BoundaryEscapeInfeasible
            raise RoutingStageError(RoutingFailure(Reason=Context.FailureReason, Stage='ClusterBoundaryLease', AffectedNets=Context.Affected, RepairActions=() if Context.CompleteWorkExhausted else ('RelocateAffectedClusters',), Detail='complete cluster-interface ownership search reached its work limit before a proof' if Context.CompleteWorkExhausted else 'complete fixed-deck terminal domains prove that no capacity-one ownership assignment exists', Diagnostics={'ReservationPurpose': 'cluster-boundary-lease', 'ClusterInterfaceDomainMode': 'complete', 'ClusterInterfaceDomainComplete': not Context.CompleteWorkExhausted, 'OwnershipSearchComplete': not Context.CompleteWorkExhausted, 'AuthoritativeAccessDomainFingerprint': Context.AccessDomainFingerprint, 'InterfaceAssignment': ClusterInterfaceAssignment(Problem=replace(Context.InterfaceProblem, DomainComplete=not Context.CompleteWorkExhausted), Feasible=False, UnsatisfiedTerminalCount=len(Context.Affected)).ToDictionary(), 'ExpansionCount': Context.CompleteExpansionCount, 'MaximumExpansions': Context.MaximumExpansions, 'LeaseTerminalCount': len(Context.Order), 'TerminalDomainCandidateCount': sum((len(Values) for Values in Context.CompleteDomains.values())), 'RequiredInterfaceLayer': Context.RequiredInterfaceLayer, 'ConflictGraph': {'Classification': 'incomplete-interface-search' if Context.CompleteWorkExhausted else 'saturated-boundary-cut', 'ConflictSignals': list(Context.Affected), 'RelocationSignals': [] if Context.CompleteWorkExhausted else list(Context.Affected)}}))
        Context.CompleteReservations = tuple((PortalReservation(Signal=Signal, Terminal=Terminal, Layer=Value[1], SlotIndex=SlotIndex, PortalId=Value[2].PortalId, Claims=Value[3], Purpose='cluster-boundary-lease', FirstSegment=Value[2].Path) for SlotIndex, ((Signal, Terminal), Value) in enumerate(sorted(Context.CompleteSelected.items()))))
        Context.CompleteFiltered = {Key: () for Key in Context.Portals}
        for Context.Reservation in Context.CompleteReservations:
            Context.CompleteFiltered[Context.Reservation.Signal, Context.Reservation.Terminal, Context.Reservation.Layer] = tuple((Portal for Portal in Context.Portals.get((Context.Reservation.Signal, Context.Reservation.Terminal, Context.Reservation.Layer), ()) if Portal.PortalId == Context.Reservation.PortalId))
        Context.CompleteAssignmentFingerprint = BuildClusterInterfaceReservationAssignmentFingerprint(Context.CompleteReservations)
        Context.Resources.PreparedClusterInterfaceAssignment = ClusterInterfaceAssignment(Problem=Context.InterfaceProblem, Feasible=True, AssignmentFingerprint=Context.CompleteAssignmentFingerprint, OwnershipAssignmentFingerprint=Context.CompleteAssignmentFingerprint, Objective=(0, sum((len(Values) == 1 for Values in Context.CompleteDomains.values())), Context.CompleteExpansionCount, Context.CompleteAssignmentFingerprint))
        raise BoundaryLeaseReturn((Context.CompleteFiltered, Context.CompleteReservations))

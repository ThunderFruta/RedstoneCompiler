"""Small orchestrator for exact cluster boundary lease assignment."""

from __future__ import annotations

from ....Contracts.Core import Position3
from ....Contracts.Results import RoutingResources
from ....Resources.ResourceGraph import PinAccessPortal
from ....Resources.ResourceGraph import PortalReservation
from typing import Any
from typing import Callable
from ..Flow.RunModels import ClusterLeaseCandidateRealizabilityNogood
from .BoundaryLeaseState import (
    BoundaryLeaseReturn,
    BoundaryLeaseState,
)
from .BoundaryLeaseDomains import (
    PrepareBoundaryLeaseDomains,
    SolveLegacyBoundaryLeaseDomain,
    PrepareCompleteBoundaryLeaseDomain,
    SolveCompleteBoundaryLeaseDomain,
)
from .BoundaryLeasePatterns import (
    BuildBoundaryLeasePatterns,
    SearchBoundaryLeasePatterns,
    RecoverBoundaryLeaseAssignment,
    FinalizeBoundaryLeaseAssignment,
)


def ReserveClusterBoundaryLeases(Portals: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], Profiles: dict[str, Any], Resources: RoutingResources, ReservationVariant: int=0, SignalCandidateDomainOffsets: dict[str, int] | None=None, CandidateRealizabilityNogoods: tuple[ClusterLeaseCandidateRealizabilityNogood, ...] | list[ClusterLeaseCandidateRealizabilityNogood]=(), ForbiddenOwnershipAssignmentFingerprints: frozenset[str]=frozenset(), RequiredPatternFingerprintsBySignal: dict[str, str] | None=None, RequiredReservations: tuple[PortalReservation, ...]=(), PriorityInterfaceCutSignals: frozenset[str]=frozenset(), MaximumExpansions: int=50000, UseCompleteClusterInterfaceAccess: bool=True, RequireCompleteClusterInterfaceDomain: bool=False, RequiredInterfaceLayer: int | None=None, WorkCheck: Callable[[dict[str, object]], None] | None=None) -> tuple[dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], tuple[PortalReservation, ...]]:
    """Assign hard access-plus-stem leases without imposing one net layer.

A boundary bundle needs ownership of its physical exits, not a restriction
that every endpoint of the net use the same routing layer.  This exact
capacity-one matcher therefore treats each requested terminal as a domain
while retaining same-net sharing and rejecting foreign claim overlap."""
    Context = BoundaryLeaseState(Portals=Portals, Profiles=Profiles, Resources=Resources, ReservationVariant=ReservationVariant, SignalCandidateDomainOffsets=SignalCandidateDomainOffsets, CandidateRealizabilityNogoods=CandidateRealizabilityNogoods, ForbiddenOwnershipAssignmentFingerprints=ForbiddenOwnershipAssignmentFingerprints, RequiredPatternFingerprintsBySignal=RequiredPatternFingerprintsBySignal, RequiredReservations=RequiredReservations, PriorityInterfaceCutSignals=PriorityInterfaceCutSignals, MaximumExpansions=MaximumExpansions, UseCompleteClusterInterfaceAccess=UseCompleteClusterInterfaceAccess, RequireCompleteClusterInterfaceDomain=RequireCompleteClusterInterfaceDomain, RequiredInterfaceLayer=RequiredInterfaceLayer, WorkCheck=WorkCheck)
    try:
        PrepareBoundaryLeaseDomains(Context)
        SolveLegacyBoundaryLeaseDomain(Context)
        PrepareCompleteBoundaryLeaseDomain(Context)
        SolveCompleteBoundaryLeaseDomain(Context)
        BuildBoundaryLeasePatterns(Context)
        SearchBoundaryLeasePatterns(Context)
        RecoverBoundaryLeaseAssignment(Context)
        FinalizeBoundaryLeaseAssignment(Context)
    except BoundaryLeaseReturn as Outcome:
        return Outcome.Value
    raise AssertionError('boundary lease phases must return a result')

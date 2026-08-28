"""Per-run state and control result for boundary lease assignment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BoundaryLeaseState:
    """Mutable values shared by one bounded lease solve."""

    Portals: Any
    Profiles: Any
    Resources: Any
    ReservationVariant: Any
    SignalCandidateDomainOffsets: Any
    CandidateRealizabilityNogoods: Any
    ForbiddenOwnershipAssignmentFingerprints: Any
    RequiredPatternFingerprintsBySignal: Any
    RequiredReservations: Any
    PriorityInterfaceCutSignals: Any
    MaximumExpansions: Any
    UseCompleteClusterInterfaceAccess: Any
    RequireCompleteClusterInterfaceDomain: Any
    RequiredInterfaceLayer: Any
    WorkCheck: Any


class BoundaryLeaseReturn(BaseException):
    """Internal control result preserving an early kernel return."""

    def __init__(self, Value: Any) -> None:
        super().__init__()
        self.Value = Value


def SetBoundaryLeaseState(
    State: BoundaryLeaseState,
    Name: str,
    Value: Any,
) -> Any:
    """Assign and return a value used inside an expression."""
    setattr(State, Name, Value)
    return Value

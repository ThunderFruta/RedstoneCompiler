"""Typed physical-routing failure records and repair recommendations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class RoutingFailureReason(str, Enum):
    InvalidCellContract = "InvalidCellContract"
    NoPinAccessPattern = "NoPinAccessPattern"
    PlacementOverlap = "PlacementOverlap"
    GlobalCapacityOverflow = "GlobalCapacityOverflow"
    NoConnectedGlobalRoute = "NoConnectedGlobalRoute"
    TrackAssignmentConflict = "TrackAssignmentConflict"
    NoLegalLayerTransition = "NoLegalLayerTransition"
    DetailedSearchExhausted = "DetailedSearchExhausted"
    ElectricalConflict = "ElectricalConflict"
    SupportConflict = "SupportConflict"
    HeadroomConflict = "HeadroomConflict"
    NoRepeaterSite = "NoRepeaterSite"
    FinalDrcViolation = "FinalDrcViolation"
    RuntimeBudgetExceeded = "RuntimeBudgetExceeded"
    Stagnated = "Stagnated"
    LocalClaimConflict = "LocalClaimConflict"
    LocalClaimDisconnected = "LocalClaimDisconnected"
    NoBoundaryEscape = "NoBoundaryEscape"
    PartialTreeExtensionFailed = "PartialTreeExtensionFailed"
    ClusterEntranceBudgetExceeded = "ClusterEntranceBudgetExceeded"
    OrganizationPolicyViolation = "OrganizationPolicyViolation"
    LocalMaterialBudgetExceeded = "LocalMaterialBudgetExceeded"
    MultiSourceStagnated = "MultiSourceStagnated"
    BoundaryEscapeInfeasible = "BoundaryEscapeInfeasible"
    GlobalCongestionUnresolved = "GlobalCongestionUnresolved"
    DetailedCongestionUnresolved = "DetailedCongestionUnresolved"
    RepeaterAccessInfeasible = "RepeaterAccessInfeasible"


@dataclass(frozen=True)
class RoutingFailure:
    """Machine-readable stage failure with bounded legal repair actions."""

    Reason: RoutingFailureReason
    Stage: str
    AffectedNets: tuple[str, ...] = ()
    Resources: tuple[str, ...] = ()
    Locations: tuple[tuple[int, int, int], ...] = ()
    RepairActions: tuple[str, ...] = ()
    Detail: str = ""
    Diagnostics: dict[str, object] | None = None

    def ToDictionary(self) -> dict[str, object]:
        return asdict(self)


class RoutingStageError(ValueError):
    """Exception wrapper retaining typed failure information."""

    def __init__(self, Failure: RoutingFailure):
        self.Failure = Failure
        Context = []
        if Failure.AffectedNets:
            Context.append(f"nets={','.join(Failure.AffectedNets)}")
        if Failure.Resources:
            Context.append(f"resources={','.join(Failure.Resources)}")
        if Failure.Locations:
            Context.append(f"locations={Failure.Locations}")
        super().__init__(
            f"{Failure.Stage}:{Failure.Reason.value}: {Failure.Detail}"
            + (f" ({'; '.join(Context)})" if Context else "")
        )

"""Authoritative detailed-routing entrypoint."""

from __future__ import annotations

import os
from typing import Any, Callable

try:
    from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
    except Exception:
        RustRoutingContext = None

from ...Redstone.Rules import BuildRoutingResources
from ..Global.Orchestration.Flow import RouteAuthoritativeResources
from ..Planning.ChannelPlanner import RoutingIterationMetrics
from ...Contracts.Placement import ClusterInterfaceRealizabilityNogood, TrackAssignmentPreparation
from ...Contracts.Component import ComponentRoutingProblem
from ...Contracts.Results import RoutedDesign, RoutingResources
from ...Runtime.Reliability import RoutingDeadline
from ...Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from ...Redstone.Technology import DefaultRedstoneRoutingTechnology, RedstoneRoutingTechnology


def RoutePcbNets(
    Placed: Any,
    SignalOrder: list[str] | None = None,
    SearchMarginX: int = 24,
    SearchMarginZ: int = 24,
    MaximumRoutingHeight: int = 0,
    AccessLength: int = 2,
    ElectricalClearance: int = 0,
    MaximumIterations: int = 40,
    MaximumDetourRatio: float = 20.0,
    MaximumDetourAllowance: int = 256,
    RouteGuidePenalty: int = 2,
    Resources: RoutingResources | None = None,
    IterationProgressCallback: Callable[[int, int], None] | None = None,
    IterationDiagnosticCallback: Callable[
        [RoutingIterationMetrics, str | None], None
    ] | None = None,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    SkipStrictPortalReservation: bool = False,
    ReservationVariant: int = 0,
    PreparePortalGeometryOnly: bool = False,
    PrepareTrackAssignmentOnly: bool = False,
    PrepareRawTrackAssignmentDomainOnly: bool = False,
    FrozenTrackAssignmentPreparation: TrackAssignmentPreparation | None = None,
    ValidateClusterInterfaceForeignAccessOnly: bool = False,
    ValidatePhysicalComponentForeignPortalSupportOnly: bool = False,
    PrepareClusterInterfaceAssignmentOnly: bool = False,
    PrepareComponentRoutingProblemOnly: bool = False,
    PreparePhysicalComponentAssemblyOnly: bool = False,
    PreparePhysicalComponentPortFactorDomainOnly: bool = False,
    DeferClusterBoundaryLeaseUntilCapacityPrecheck: bool = False,
    UnboundOwnedSignalFrontierProofCallback: Callable[
        [ComponentRoutingProblem], None
    ] | None = None,
    RequireCompleteClusterInterfaceDomain: bool = False,
    ClusterInterfaceRealizabilityNogoods: tuple[
        ClusterInterfaceRealizabilityNogood, ...
    ] = (),
    ClusterInterfaceStateFingerprint: str = "",
    ClusterInterfaceLocalRouteFingerprint: str = "",
    ForbiddenClusterInterfaceAssignmentFingerprints: (
        frozenset[str]
    ) = frozenset(),
    ClusterInterfaceFrozenPatternFingerprints: (
        dict[str, str] | None
    ) = None,
    ClusterInterfaceFrozenReservations: tuple[Any, ...] = (),
    Deadline: RoutingDeadline | None = None,
) -> RoutedDesign:
    """Plan and materialize one deterministic capacity-one Rust assignment."""
    del (
        SignalOrder,
        AccessLength,
        ElectricalClearance,
        MaximumIterations,
        MaximumDetourRatio,
        MaximumDetourAllowance,
        RouteGuidePenalty,
    )
    if RustRoutingContext is None:
        raise ValueError("authoritative routing requires the Rust router")
    if Resources is None:
        Resources = BuildRoutingResources(
            Placed,
            WorkCheck=(
                (
                    lambda Diagnostics: Deadline.RaiseIfExpired(
                        "RoutingResourceConstruction",
                        Diagnostics,
                    )
                )
                if Deadline is not None
                else None
            ),
            Technology=Technology,
        )
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")) and Deadline is not None:
        print(
            "[debug] authoritative: detailed-routing deadline "
            f"remaining={Deadline.RemainingSeconds():.3f}s",
            flush=True,
        )
    return RouteAuthoritativeResources(
        Placed,
        Resources,
        SearchMarginX,
        SearchMarginZ,
        MaximumRoutingHeight,
        Policy,
        Technology,
        ProgressCallback=IterationProgressCallback,
        DiagnosticCallback=IterationDiagnosticCallback,
        SkipStrictPortalReservation=SkipStrictPortalReservation,
        ReservationVariant=ReservationVariant,
        PreparePortalGeometryOnly=PreparePortalGeometryOnly,
        PrepareTrackAssignmentOnly=PrepareTrackAssignmentOnly,
        PrepareRawTrackAssignmentDomainOnly=(
            PrepareRawTrackAssignmentDomainOnly
        ),
        FrozenTrackAssignmentPreparation=FrozenTrackAssignmentPreparation,
        ValidateClusterInterfaceForeignAccessOnly=(
            ValidateClusterInterfaceForeignAccessOnly
        ),
        ValidatePhysicalComponentForeignPortalSupportOnly=(
            ValidatePhysicalComponentForeignPortalSupportOnly
        ),
        PrepareClusterInterfaceAssignmentOnly=(
            PrepareClusterInterfaceAssignmentOnly
        ),
        PrepareComponentRoutingProblemOnly=(
            PrepareComponentRoutingProblemOnly
        ),
        PreparePhysicalComponentAssemblyOnly=(
            PreparePhysicalComponentAssemblyOnly
        ),
        PreparePhysicalComponentPortFactorDomainOnly=(
            PreparePhysicalComponentPortFactorDomainOnly
        ),
        DeferClusterBoundaryLeaseUntilCapacityPrecheck=(
            DeferClusterBoundaryLeaseUntilCapacityPrecheck
        ),
        UnboundOwnedSignalFrontierProofCallback=(
            UnboundOwnedSignalFrontierProofCallback
        ),
        RequireCompleteClusterInterfaceDomain=(
            RequireCompleteClusterInterfaceDomain
        ),
        ClusterInterfaceRealizabilityNogoods=(
            ClusterInterfaceRealizabilityNogoods
        ),
        ClusterInterfaceStateFingerprint=(
            ClusterInterfaceStateFingerprint
        ),
        ClusterInterfaceLocalRouteFingerprint=(
            ClusterInterfaceLocalRouteFingerprint
        ),
        ForbiddenClusterInterfaceAssignmentFingerprints=(
            ForbiddenClusterInterfaceAssignmentFingerprints
        ),
        ClusterInterfaceFrozenPatternFingerprints=(
            ClusterInterfaceFrozenPatternFingerprints
        ),
        ClusterInterfaceFrozenReservations=(
            ClusterInterfaceFrozenReservations
        ),
        Deadline=Deadline,
    )

"""Authoritative detailed-routing worker and compatibility entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

try:
    from ...RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
    except Exception:
        RustRoutingContext = None

from ..Actions import BuildRoutingResources
from ..AuthoritativePlanner import RouteAuthoritativeResources
from ..ChannelPlanner import RoutingIterationMetrics
from ..Models import RoutedDesign, RoutingResources
from ..Reliability import RoutingDeadline
from ..Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from ..Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)


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
        Deadline=Deadline,
    )


@dataclass(frozen=True)
class DetailedRoutingWorker:
    """Own one authoritative detailed-routing run."""

    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology

    def Run(self, Placed: Any, **Options: Any) -> RoutedDesign:
        return RoutePcbNets(
            Placed,
            Policy=self.Policy,
            Technology=self.Technology,
            **Options,
        )

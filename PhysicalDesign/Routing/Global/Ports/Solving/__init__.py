"""Public authoritative physical component port-solving API."""

from __future__ import annotations

from typing import Callable

from .....Contracts.Component import PhysicalComponentBoundaryPortReservation
from .....Contracts.PhysicalInterface import PreparedPhysicalComponentAssembly, PreparedPhysicalComponentPortFactorDomain
from .....Contracts.Results import RoutingResources
from .....Execution.Reliability import RoutingDeadline
from .Search import _SolvePreparedPhysicalComponentPortFactorDomain


def SolvePreparedPhysicalComponentPortFactorDomain(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    Resources: RoutingResources,
    *,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    Deadline: RoutingDeadline | None = None,
    DeferLocalCompositeSelection: bool = False,
    RequiredBoundaryPorts: tuple[
        PhysicalComponentBoundaryPortReservation, ...
    ] | None = None,
) -> PreparedPhysicalComponentAssembly:
    return _SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=WorkCheck,
        Deadline=Deadline,
        DeferLocalCompositeSelection=DeferLocalCompositeSelection,
        RequiredBoundaryPorts=RequiredBoundaryPorts,
    )


__all__ = ["SolvePreparedPhysicalComponentPortFactorDomain"]

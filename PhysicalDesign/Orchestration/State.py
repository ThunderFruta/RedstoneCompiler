"""Typed state and injectable services for one placement flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


_PlacementFlowDefault = object()


@dataclass(frozen=True)
class PlacementFlowServices:
    """Call-time dependencies used by placement orchestration."""

    BuildLocalFirstSnapshot: Callable[..., Any]
    BuildRoutingResources: Callable[..., Any]
    MeasurePcbDesign: Callable[..., Any]
    MeasurePlacementRoutingFeedback: Callable[..., Any]
    PlacePcbGraph: Callable[..., Any]
    RoutePcbDesign: Callable[..., Any]
    ValidateNandOnlyDesign: Callable[..., Any]
    ValidatePlacedCellElectricalIsolation: Callable[..., Any]
    monotonic: Callable[..., Any]


@dataclass
class PlacementFlowState:
    """Mutable state owned by one non-reentrant compiler run."""

    Netlist: Any
    ProgressCallback: Any
    Policy: Any
    Technology: Any
    RequestedStrategy: Any
    UsedStrategy: Any
    RoutedValidationCallback: Any
    Services: PlacementFlowServices


def SetPlacementFlowState(
    State: PlacementFlowState,
    Name: str,
    Value: Any,
) -> Any:
    """Assign and return a value used by a stateful expression."""
    setattr(State, Name, Value)
    return Value

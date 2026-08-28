"""Explicit per-run state for physical-port factor preparation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PortPreparationState:
    """Values shared by the bounded preparation phases."""

    Placed: Any
    Problem: Any
    CoarsePlan: Any
    Resources: Any
    LayerCount: Any
    AccessCertificate: Any
    AuthoritativeRegion: Any
    AuthoritativeRegionFingerprint: Any
    Profiles: Any
    FrozenComponentClaims: Any
    TechnologyFingerprint: Any
    WorkCheck: Any


def SetPortPreparationState(
    State: PortPreparationState,
    Name: str,
    Value: Any,
) -> Any:
    """Assign and return a value used inside an expression."""
    setattr(State, Name, Value)
    return Value

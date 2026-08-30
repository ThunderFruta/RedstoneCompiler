"""Data contracts for authoritative Fabric-server validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FabricServerValidationResult:
    """Outcome returned by the Fabric-server validation stage."""

    Status: str
    Backend: str | None = None
    RuntimeSeconds: float = 0.0
    Diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FabricServerLoadResult:
    """Outcome from loading a verified fixture into a running Fabric server."""

    Status: str
    RuntimeSeconds: float = 0.0
    Diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FabricServerControlResult:
    """Outcome from one authenticated running-server control operation."""

    Status: str
    RuntimeSeconds: float = 0.0
    Diagnostics: dict[str, Any] = field(default_factory=dict)

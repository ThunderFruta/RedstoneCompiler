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

    @classmethod
    def NotRun(cls) -> "FabricServerValidationResult":
        """Represent a routed artifact that has not reached a server."""
        return cls(
            Status="not-run",
            Diagnostics={
                "Reason": "fabric-server-integration-not-configured",
            },
        )

"""Backend-neutral contracts for physical redstone validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PhysicalValidationProgress:
    """Observable progress for one physical validation phase."""

    Completed: int
    Total: int
    Stage: str
    Status: str | None = None
    Backend: str | None = None


@dataclass(frozen=True)
class PhysicalValidationResult:
    """Normalized outcome from a physical validation backend."""

    Status: str
    Backend: str | None = None
    RuntimeSeconds: float = 0.0
    Diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhysicalFixtureArtifact:
    """Identity and cardinality of one shared physical fixture."""

    Path: Path
    Sha256: str
    BlockCount: int
    InputCount: int
    OutputCount: int

"""Fabric compatibility names for shared physical fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from Validation.Core.Fixture import BuildPhysicalFixture, CanonicalFixtureBytes, WritePhysicalFixture
from Validation.Core.Models import PhysicalFixtureArtifact


FabricFixtureArtifact = PhysicalFixtureArtifact


def BuildFabricFixture(
    *,
    RoutedDesign: Any,
    Rendered: Any,
    Module: Any,
) -> dict[str, object]:
    """Build the shared physical fixture for a Fabric compatibility caller."""
    return BuildPhysicalFixture(
        RoutedDesign=RoutedDesign,
        Rendered=Rendered,
        Module=Module,
    )


def WriteFabricFixture(
    PathValue: Path,
    Fixture: dict[str, object],
) -> FabricFixtureArtifact:
    """Write a shared physical fixture for a Fabric compatibility caller."""
    return WritePhysicalFixture(PathValue, Fixture)

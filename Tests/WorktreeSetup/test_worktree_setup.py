"""End-to-end contract checks for the managed-worktree setup environment."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys

import pytest

from Validation.Fabric import (
    DefaultFabricServerRoot,
    FabricServerConfiguration,
    FabricServerSupervisor,
)
from Validation.Mchprs import MchprsValidator
from Validation.Physical import PhysicalFixtureArtifact


RepositoryRoot = Path(__file__).resolve().parents[2]
MchprsFixtureRoot = RepositoryRoot / "Tests/Fixtures/Mchprs"


def LoadMchprsCase(Name: str) -> tuple[dict[str, object], Path, Path]:
    """Load one tracked fixture and verify its manifest-bound hashes."""
    Manifest = json.loads(
        (MchprsFixtureRoot / "Manifest.json").read_text(encoding="utf-8"),
    )
    Case = Manifest["Circuits"][Name]
    FixturePath = MchprsFixtureRoot / Case["PhysicalFixture"]["Path"]
    LogicPath = MchprsFixtureRoot / Case["NandLogic"]["Path"]
    for PathValue, Definition in (
        (FixturePath, Case["PhysicalFixture"]),
        (LogicPath, Case["NandLogic"]),
    ):
        assert sha256(PathValue.read_bytes()).hexdigest() == Definition["Sha256"]
    return Case, FixturePath, LogicPath


def test_native_extension_imports_from_this_checkout() -> None:
    """The editable install must not silently import another worktree's module."""
    import RedstoneCompiler.RustRouting as RustRouting

    ModulePath = Path(RustRouting.__file__).resolve()
    assert ModulePath.is_relative_to(RepositoryRoot)
    assert hasattr(RustRouting, "ValidateMchprsFixture")


def test_setup_uses_the_worktree_virtual_environment_when_requested() -> None:
    """Check interpreter provenance during the setup hook, not normal test runs."""
    if os.environ.get("RC_WORKTREE_SETUP_VERIFY") != "1":
        pytest.skip("only enforced by the Codex worktree setup hook")

    ExpectedPrefix = (RepositoryRoot / ".venv").resolve()
    assert Path(sys.prefix).resolve() == ExpectedPrefix
    assert Path(sys.executable).parent == ExpectedPrefix / "bin"
    assert Path(sys.base_prefix).resolve() != ExpectedPrefix


def test_mchprs_fixture_runs_through_the_editable_native_extension() -> None:
    """Run the smallest tracked exhaustive physical-validation fixture."""
    Case, FixturePath, LogicPath = LoadMchprsCase("FullAdder")
    FixtureDocument = json.loads(FixturePath.read_text(encoding="utf-8"))
    Result = MchprsValidator().Validate(
        Fixture=PhysicalFixtureArtifact(
            Path=FixturePath,
            Sha256=Case["PhysicalFixture"]["Sha256"],
            BlockCount=len(FixtureDocument["Blocks"]),
            InputCount=len(FixtureDocument["Inputs"]),
            OutputCount=len(FixtureDocument["Outputs"]),
        ),
        LogicPath=LogicPath,
    )

    assert Result.Status == "passed"
    assert Result.Diagnostics["TestedVectors"] == Case["ExpectedVectors"]


def test_fabric_path_uses_the_explicit_or_shared_runtime_contract() -> None:
    """A new worktree must not require a copied ignored Fabric server payload."""
    DefaultRoot = DefaultFabricServerRoot()
    assert DefaultRoot.is_relative_to(RepositoryRoot)
    assert DefaultRoot.name == "Server"
    assert DefaultRoot.parent.name == "ValidationServerHarness"

    ExplicitRoot = RepositoryRoot / "temporary-fabric-runtime"
    Configuration = FabricServerConfiguration(Root=ExplicitRoot)
    Result = FabricServerSupervisor(Configuration).Validate(
        Fixture=PhysicalFixtureArtifact(
            Path=RepositoryRoot / "missing.FabricFixture.json",
            Sha256="",
            BlockCount=0,
            InputCount=0,
            OutputCount=0,
        ),
        Vectors=[],
    )
    assert Result.Status == "infrastructure-failure"
    assert Result.Diagnostics["Reason"] == "fabric-server-or-harness-not-installed"

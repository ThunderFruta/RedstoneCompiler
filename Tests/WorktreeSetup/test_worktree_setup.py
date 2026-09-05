"""End-to-end contract checks for the managed-worktree setup environment."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tomllib

import pytest

from Validation.Fabric import (
    DefaultFabricServerRoot,
    FabricServerConfiguration,
    FabricServerSupervisor,
)
from Validation.Mchprs import MchprsValidator
from Validation.Physical import PhysicalFixtureArtifact


RepositoryRoot = Path(__file__).resolve().parents[2]
EnvironmentPath = (
    RepositoryRoot / ".codex/environments/redstonecompiler-worktree.toml"
)
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


def test_worktree_setup_configures_an_isolated_linux_environment() -> None:
    """Keep the setup and teardown limited to generated worktree payloads."""
    Environment = tomllib.loads(EnvironmentPath.read_text(encoding="utf-8"))

    assert Environment["version"] == 1
    assert Environment["name"] == "RedstoneCompiler Worktree"
    Setup = Environment["setup"]["linux"]["script"]
    Cleanup = Environment["cleanup"]["linux"]["script"]
    assert "python3 -m venv .venv" in Setup
    assert ".venv/bin/python -m pip install -e ." in Setup
    assert "Tests/WorktreeSetup" in Setup
    assert "rm -rf .venv Kernels/Routing/target" in Cleanup
    assert "Runtime/FabricServer" not in Cleanup
    assert "ValidationServerHarness/Server" not in Cleanup


def test_configured_paths_are_owned_by_this_checkout() -> None:
    """Require tracked inputs and reject a stale path from another checkout."""
    assert (RepositoryRoot / "pyproject.toml").is_file()
    assert (RepositoryRoot / "Kernels/Routing/Cargo.toml").is_file()
    assert (RepositoryRoot / "Tools/Mchprs/TestPhysicalFixture.py").is_file()
    assert (RepositoryRoot / "Validation/Fabric/ServerHarness/build.gradle").is_file()
    assert EnvironmentPath.is_file()
    assert EnvironmentPath.is_relative_to(RepositoryRoot)


def test_native_extension_imports_from_this_checkout() -> None:
    """The editable install must not silently import another worktree's module."""
    import RedstoneCompiler.RustRouting as RustRouting

    ModulePath = Path(RustRouting.__file__).resolve()
    assert ModulePath.suffix == ".so"
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

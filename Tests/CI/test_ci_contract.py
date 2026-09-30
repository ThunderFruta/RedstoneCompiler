"""Observable failure, provenance, template, and publication boundaries for CI."""

from copy import deepcopy
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from Tools.CI.Evidence import (
    CollectAcceptanceEvidence, FabricPrerequisites, FileHash, SealEvidence,
    ValidateProvenance,
)
from Tools.CI.RunChecks import CleanEnvironment, RunStep
from Tools.Routing.RunRouterAcceptance import (
    AcceptanceConfiguration, BuildPlannedRuns, BuildResolvedTemplateInputManifest,
)

RepositoryRoot = Path(__file__).resolve().parents[2]
TemplateModule = RepositoryRoot / "Assets/Templates/__init__.py"


def test_explicit_templates_override_machine_local_preference(monkeypatch, tmp_path):
    for Name in ("Input", "Nand", "Output"):
        (tmp_path / f"{Name}.litematic").write_bytes(Name.encode())
    OriginalIsFile = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda PathValue: (
        True if "PrismLauncher" in str(PathValue) else OriginalIsFile(PathValue)
    ))
    monkeypatch.setenv("RC_TEMPLATE_ROOT", str(tmp_path))
    Catalog = runpy.run_path(str(TemplateModule))["LitematicTemplates"]
    assert Catalog == {Name: tmp_path / f"{Name}.litematic" for Name in ("Input", "Nand", "Output")}


def test_default_template_preference_is_preserved(monkeypatch):
    monkeypatch.delenv("RC_TEMPLATE_ROOT", raising=False)
    OriginalIsFile = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda PathValue: (
        True if "PrismLauncher" in str(PathValue) else OriginalIsFile(PathValue)
    ))
    Module = runpy.run_path(str(TemplateModule))
    assert Module["TemplateDirectory"] == Module["ExternalTemplateDirectory"]


@pytest.mark.parametrize("Root", ["", "missing"])
def test_invalid_explicit_templates_fail_instead_of_falling_back(monkeypatch, tmp_path, Root):
    monkeypatch.setenv("RC_TEMPLATE_ROOT", "" if not Root else str(tmp_path / Root))
    with pytest.raises(ValueError, match="RC_TEMPLATE_ROOT"):
        runpy.run_path(str(TemplateModule))


def test_child_provenance_resolves_current_template_namespace(monkeypatch):
    monkeypatch.setenv("RC_TEMPLATE_ROOT", str(RepositoryRoot / "Assets/Templates"))
    Result = BuildResolvedTemplateInputManifest(RepositoryRoot, Path(sys.executable))
    assert Result["ResolutionSource"] == "configured-child"
    for Name, Record in Result["Templates"].items():
        assert Record["Path"] == f"Assets/Templates/{Name}.litematic"
        assert Record["WithinRepository"] is True
        assert Record["Sha256"] == FileHash(RepositoryRoot / Record["Path"])


@pytest.fixture
def ProvenanceFixture(tmp_path):
    Native = tmp_path / "RedstoneCompiler/RustRouting.so"
    Native.parent.mkdir()
    Native.write_bytes(b"just-built native payload")
    Build = tmp_path / "build.so"
    Build.write_bytes(Native.read_bytes())
    Templates = {}
    for Name in ("Input", "Nand", "Output"):
        PathValue = tmp_path / f"Assets/Templates/{Name}.litematic"
        PathValue.parent.mkdir(parents=True, exist_ok=True)
        PathValue.write_bytes(Name.encode())
        Templates[Name] = {"Path": PathValue.relative_to(tmp_path).as_posix(), "WithinRepository": True, "Sha256": FileHash(PathValue)}
    Provenance = {
        "Git": {"Revision": "a" * 40, "Dirty": False},
        "NativeExtension": {"Path": "RedstoneCompiler/RustRouting.so", "Loaded": True, "WithinRepository": True, "Sha256": FileHash(Native)},
        "PhysicalTemplates": {"ResolutionSource": "configured-child", "Templates": Templates},
    }
    return tmp_path, Build, Provenance


def test_exact_built_native_and_templates_are_required(ProvenanceFixture):
    Root, Build, Provenance = ProvenanceFixture
    ValidateProvenance(Provenance, Root, Build, "a" * 40)
    Build.write_bytes(b"different native build")
    with pytest.raises(ValueError, match="native extension"):
        ValidateProvenance(Provenance, Root, Build, "a" * 40)


@pytest.mark.parametrize("Mutation", ["dirty", "wrong-commit", "external-native", "fallback-probe", "template-hash"])
def test_invalid_provenance_is_non_passing(ProvenanceFixture, Mutation):
    Root, Build, Provenance = ProvenanceFixture
    Changed = deepcopy(Provenance)
    if Mutation == "dirty":
        Changed["Git"]["Dirty"] = True
    elif Mutation == "wrong-commit":
        Changed["Git"]["Revision"] = "b" * 40
    elif Mutation == "external-native":
        Changed["NativeExtension"]["WithinRepository"] = False
    elif Mutation == "fallback-probe":
        Changed["PhysicalTemplates"]["ResolutionSource"] = "repository-module"
    else:
        Changed["PhysicalTemplates"]["Templates"]["Nand"]["Sha256"] = "0" * 64
    with pytest.raises(ValueError):
        ValidateProvenance(Changed, Root, Build, "a" * 40)


def test_injected_pytest_failure_retains_nonzero_exit_and_diagnostic(tmp_path):
    TestFile = tmp_path / "test_intentionally_failing.py"
    TestFile.write_text("def test_failure():\n    assert False, 'injected CI failure'\n")
    Result = RunStep("injected-pytest", [sys.executable, "-m", "pytest", "-q", str(TestFile)], tmp_path, CleanEnvironment())
    assert Result["ReturnCode"] == 1
    assert "injected CI failure" in (tmp_path / "injected-pytest.stdout.log").read_text()
    assert json.loads((tmp_path / "injected-pytest.json").read_text())["ReturnCode"] == 1


def test_missing_fabric_prerequisite_fails_without_creating_runtime(tmp_path):
    Root = tmp_path / "no-runtime"
    with pytest.raises(ValueError, match="missing preprovisioned"):
        FabricPrerequisites(Root)
    assert not Root.exists()


def test_fabric_eula_is_never_accepted_implicitly(tmp_path):
    (tmp_path / "mods").mkdir()
    (tmp_path / "fabric-server-launch.jar").write_bytes(b"launcher")
    (tmp_path / "mods/redstonecompiler-harness.jar").write_bytes(b"harness")
    Eula = tmp_path / "eula.txt"
    Eula.write_text("# eula=true\neula=false\n")
    with pytest.raises(ValueError, match="EULA"):
        FabricPrerequisites(tmp_path)
    assert Eula.read_text() == "# eula=true\neula=false\n"
    Eula.write_text("eula=true\n")
    assert set(FabricPrerequisites(tmp_path)) == {"Launcher", "Harness"}


def test_all_seven_physical_cases_are_scheduled_once_without_baseline(tmp_path):
    Configuration = AcceptanceConfiguration(
        RepositoryRoot=RepositoryRoot, OutputRoot=tmp_path,
        DateLabel="2026-09-30", PythonExecutable=Path(sys.executable), MatrixMode="expanded",
    )
    Runs = BuildPlannedRuns(Configuration)
    assert {Run["Circuit"] for Run in Runs} == {
        "HalfAdder", "FullAdder", "RippleCarryAdder4", "RippleCarryAdder8",
        "DecimalToBinary4", "TFlipFlopLatch", "CarryLookaheadAdder4",
    }
    assert len(Runs) == 7
    assert all(Run["Repetition"] == 1 for Run in Runs)
    assert all("--capture-baseline" not in Run["Command"] and "--compare-baseline" not in Run["Command"] for Run in Runs)


def test_evidence_excludes_worlds_configuration_hidden_and_symlinked_files(tmp_path):
    Source = tmp_path / "private"
    Destination = tmp_path / "publish"
    Source.mkdir()
    (Source / "Summary.txt").write_text("run failure")
    (Source / "Circuit.RoutingFailure.json").write_text('{"Accepted": false}')
    for Name in (".env", "server.properties", "token.txt", "private.json", "world/Summary.txt", "config/Summary.txt"):
        PathValue = Source / Name
        PathValue.parent.mkdir(parents=True, exist_ok=True)
        PathValue.write_text("must not be published")
    External = tmp_path / "external"
    External.mkdir()
    (External / "RawDump.txt").write_text("external secret")
    (Source / "linked").symlink_to(External, target_is_directory=True)
    (Source / "RawDump.txt").symlink_to(External / "RawDump.txt")
    assert CollectAcceptanceEvidence(Source, Destination) == ["Circuit.RoutingFailure.json", "Summary.txt"]
    SealEvidence(Destination)
    Index = json.loads((Destination / "EvidenceIndex.json").read_text())
    assert set(Index["Files"]) == {"Circuit.RoutingFailure.json", "Summary.txt"}
    assert all("must not" not in PathValue.read_text() and "external secret" not in PathValue.read_text() for PathValue in Destination.rglob("*") if PathValue.is_file())


def test_ci_environment_removes_ambient_routing_controls(monkeypatch):
    monkeypatch.setenv("RC_RUN_SCALE_TESTS", "1")
    monkeypatch.setenv("RC_UNRELATED_TOKEN", "never-record")
    monkeypatch.setenv("RCS_PRIVATE", "never-record")
    monkeypatch.setenv("PYTHONPATH", "/other-checkout")
    Environment = CleanEnvironment()
    assert not any(Name in Environment for Name in ("RC_RUN_SCALE_TESTS", "RC_UNRELATED_TOKEN", "RCS_PRIVATE", "PYTHONPATH"))
    assert Environment["RC_TEMPLATE_ROOT"] == str(RepositoryRoot / "Assets/Templates")
    assert Environment["PYTHONHASHSEED"] == "0"

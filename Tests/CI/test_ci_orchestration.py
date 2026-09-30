"""Prove CI orchestration does not lose failure or runtime-ownership boundaries."""

import json
import os
from pathlib import Path
import sys

import pytest

from Tools.CI import RunChecks as CI
from Tools.CI.Evidence import FileHash


@pytest.fixture
def Runner(monkeypatch, tmp_path):
    Repository = tmp_path / "repo"
    Runtime = tmp_path / "dedicated-runtime"
    Output = Repository / "Output/CI/test"
    Repository.mkdir()
    (Runtime / "mods").mkdir(parents=True)
    (Runtime / "fabric-server-launch.jar").write_bytes(b"launcher")
    (Runtime / "mods/redstonecompiler-harness.jar").write_bytes(b"old harness")
    (Runtime / "eula.txt").write_text("eula=true\n")
    (Runtime / "server.jar").write_bytes(b"verified server")
    (Runtime.parent / "runtime-lock.json").write_text(json.dumps({
        "ProvisioningValidated": True, "MinecraftVersion": "26.2", "FabricLoaderVersion": "0.19.3",
        "Jars": {Name: FileHash(Runtime / Name) for Name in ("fabric-server-launch.jar", "server.jar")},
    }))
    Built = Repository / "Cache/Gradle/ServerHarness/Build/libs/validation-server-harness-1.0.0.jar"
    Built.parent.mkdir(parents=True)
    Built.write_bytes(b"current commit harness")
    monkeypatch.setattr(CI, "RepositoryRoot", Repository)
    monkeypatch.setattr(CI, "Path", lambda Value: Runtime if Value == "/opt/redstone-fabric/runtime" else Path(Value))
    monkeypatch.setattr(CI, "FileHash", lambda Value: FileHash(Value) if Value.exists() else "dependency-hash")
    monkeypatch.setattr(CI, "ReadSourceState", lambda Root: {"Revision": "a" * 40, "Dirty": False})
    monkeypatch.setattr(CI, "Provenance", lambda Commit: {"Native": "verified", "Commit": Commit})
    monkeypatch.setattr(os, "environ", dict(os.environ))
    Commands = []
    Failures = set()
    RuntimeState = {"Status": "stopped", "StopStatus": "stopped"}

    def RunStep(Name, Command, Evidence, Environment):
        Commands.append(Name)
        if Name == "fabric-status":
            (Evidence / "fabric-status.stdout.log").write_text(json.dumps(RuntimeState))
        if Name == "fabric-stop":
            (Evidence / "fabric-stop.stdout.log").write_text(json.dumps({"Status": RuntimeState["StopStatus"]}))
        if Name == "fabric-start":
            assert (Runtime / "mods/redstonecompiler-harness.jar").read_bytes() == Built.read_bytes()
        if Name == "pytest":
            assert Environment["RC_SOURCE_ORACLE_EVIDENCE"] == str(Output / "SourceOracle")
            Proof = Path(Environment["RC_SOURCE_ORACLE_EVIDENCE"]) / "injected-counterexample"
            Proof.mkdir(parents=True)
            (Proof / "result.json").write_text('{"Outcome": "counterexample"}')
            (Proof / "proof.ys").write_text("sat -prove mismatch 0")
        return {"Name": Name, "ReturnCode": 1 if Name in Failures else 0}

    monkeypatch.setattr(CI, "RunStep", RunStep)

    def Run(Tier):
        monkeypatch.setattr(sys, "argv", ["RunChecks.py", "--tier", Tier, "--expected-commit", "a" * 40, "--output", str(Output)])
        Exit = CI.Main()
        return Exit, json.loads((Output / "Run.json").read_text())

    return Run, Commands, Failures, Runtime, RuntimeState, Output


def test_aggregate_pytest_failure_fails_and_retains_other_checks(Runner):
    Run, Commands, Failures, Runtime, RuntimeState, Output = Runner
    Failures.add("pytest")
    Exit, Receipt = Run("deterministic")
    assert Exit == 1 and Receipt["Status"] == "failed"
    assert Commands[-1] == "pytest"
    assert Receipt["PhysicalAcceptance"] == "not-run"
    Index = json.loads((Output / "EvidenceIndex.json").read_text())
    assert "SourceOracle/injected-counterexample/result.json" in Index["Files"]
    assert "SourceOracle/injected-counterexample/proof.ys" in Index["Files"]


def test_physical_start_failure_attempts_owned_cleanup_and_never_acceptance(Runner):
    Run, Commands, Failures, Runtime, RuntimeState, Output = Runner
    Failures.add("fabric-start")
    Exit, Receipt = Run("physical")
    assert Exit == 1
    assert Commands[-2:] == ["fabric-start", "fabric-stop"]
    assert "acceptance" not in Commands
    assert Receipt["PhysicalAcceptance"] == "not-run"


def test_already_running_runtime_is_not_replaced_or_stopped(Runner):
    Run, Commands, Failures, Runtime, RuntimeState, Output = Runner
    RuntimeState["Status"] = "running"
    Exit, Receipt = Run("physical")
    assert Exit == 1
    assert "fabric-start" not in Commands and "fabric-stop" not in Commands
    assert (Runtime / "mods/redstonecompiler-harness.jar").read_bytes() == b"old harness"


def test_physical_case_failure_preserves_failure_and_stops_runtime(Runner):
    Run, Commands, Failures, Runtime, RuntimeState, Output = Runner
    Failures.add("acceptance")
    Exit, Receipt = Run("physical")
    assert Exit == 1 and Receipt["Status"] == "failed"
    assert Commands[-3:] == ["fabric-start", "acceptance", "fabric-stop"]
    assert Receipt["PhysicalAcceptance"] == "failed"


def test_missing_runtime_fails_before_any_start_or_harness_replacement(Runner):
    Run, Commands, Failures, Runtime, RuntimeState, Output = Runner
    (Runtime / "fabric-server-launch.jar").unlink()
    Exit, Receipt = Run("physical")
    assert Exit == 1
    assert "fabric-start" not in Commands and "acceptance" not in Commands
    assert Receipt["PhysicalAcceptance"] == "not-run"
    assert (Runtime / "mods/redstonecompiler-harness.jar").read_bytes() == b"old harness"


@pytest.mark.parametrize("StopStatus", ["running", None])
def test_zero_exit_cleanup_without_stopped_receipt_fails(Runner, StopStatus):
    Run, Commands, Failures, Runtime, RuntimeState, Output = Runner
    RuntimeState["StopStatus"] = StopStatus
    Exit, Receipt = Run("physical")
    assert Exit == 1 and Receipt["Status"] == "failed"
    assert "cleanup did not establish" in Receipt["Failure"]
    assert Commands[-1] == "fabric-stop"


def test_harness_source_mutation_cannot_pass(Runner, monkeypatch):
    Run, Commands, Failures, Runtime, RuntimeState, Output = Runner
    Calls = iter([
        {"Revision": "a" * 40, "Dirty": False},
        {"Revision": "a" * 40, "Dirty": True},
    ])
    monkeypatch.setattr(CI, "ReadSourceState", lambda Root: next(Calls))
    Exit, Receipt = Run("harness")
    assert Exit == 1 and Receipt["Status"] == "failed"
    assert Receipt["FinalSourceState"]["Dirty"] is True
    assert Receipt["FinalSourceFailure"] == "source changed during CI execution"


def test_startup_runtime_jar_drift_fails_before_acceptance_and_cleans_up(Runner, monkeypatch):
    Run, Commands, Failures, Runtime, RuntimeState, Output = Runner
    Original = CI.RunStep
    def StartWithDrift(Name, Command, Evidence, Environment):
        Result = Original(Name, Command, Evidence, Environment)
        if Name == "fabric-start":
            (Runtime / "server.jar").write_bytes(b"unexpected downloaded server")
        return Result
    monkeypatch.setattr(CI, "RunStep", StartWithDrift)
    Exit, Receipt = Run("physical")
    assert Exit == 1 and "acceptance" not in Commands
    assert Commands[-1] == "fabric-stop"
    assert "inventory" in Receipt["Failure"]

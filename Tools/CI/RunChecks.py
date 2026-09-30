#!/usr/bin/env python3
"""Run isolated CI tiers, retaining failures without claiming physical acceptance."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
from time import monotonic
import sys

RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from Tools.CI.Evidence import (
    CollectAcceptanceEvidence, FabricPrerequisites, FileHash, SealEvidence,
    ValidateProvenance, WriteJson,
)
from Tools.Routing.RunRouterAcceptance import (
    AcceptanceConfiguration, BuildSourceProvenance, ReadSourceState,
)


def CleanEnvironment() -> dict[str, str]:
    """Discard ambient routing controls and Python import overrides."""
    Environment = {
        Name: Value for Name, Value in os.environ.items()
        if not Name.startswith(("RC_", "RCS_"))
        and Name not in {"PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    }
    Environment.update({
        "PYTHONHASHSEED": "0", "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "RC_TEMPLATE_ROOT": str(RepositoryRoot / "Assets/Templates"),
        "RC_REQUIRE_SOURCE_ORACLE": "1",
        "RC_YOSYS": str(Path(sys.executable).parent / "yowasp-yosys"),
        "PYO3_PYTHON": sys.executable,
        "VIRTUAL_ENV": sys.prefix,
        "CARGO_TARGET_DIR": str(RepositoryRoot / "Cache/Rust/target"),
    })
    return Environment


def RunStep(Name: str, Command: list[str], Output: Path, Environment: dict) -> dict:
    """Capture one command's actual exit, including injected failures and timeouts."""
    print(f"Running {Name}", flush=True)
    Started = monotonic()
    Record = {"Name": Name, "Command": Command, "Status": "running", "ReturnCode": None}
    WriteJson(Output / f"{Name}.json", Record)
    try:
        with (Output / f"{Name}.stdout.log").open("w") as Stdout, (Output / f"{Name}.stderr.log").open("w") as Stderr:
            Process = subprocess.Popen(
                Command, cwd=RepositoryRoot, env=Environment,
                stdout=Stdout, stderr=Stderr, start_new_session=True,
            )
            try:
                ReturnCode = Process.wait(timeout=1200)
                TimedOut = False
            except subprocess.TimeoutExpired:
                os.killpg(Process.pid, signal.SIGKILL)
                Process.wait()
                ReturnCode, TimedOut = 124, True
            Record.update({"ReturnCode": ReturnCode, "TimedOut": TimedOut})
    except OSError as Error:
        Record.update({"ReturnCode": 127, "Failure": str(Error)})
    Record.update({"Status": "passed" if Record["ReturnCode"] == 0 else "failed", "RuntimeSeconds": monotonic() - Started})
    print(f"{Name}: exit {Record['ReturnCode']}", flush=True)
    WriteJson(Output / f"{Name}.json", Record)
    return Record


def Provenance(ExpectedCommit: str) -> dict:
    Configuration = AcceptanceConfiguration(
        RepositoryRoot=RepositoryRoot, OutputRoot=RepositoryRoot / "Output/CI",
        DateLabel=datetime.now(timezone.utc).date().isoformat(),
        PythonExecutable=Path(sys.executable), MatrixMode="expanded",
    )
    Result = BuildSourceProvenance(Configuration, ReadSourceState(RepositoryRoot))
    ValidateProvenance(
        Result, RepositoryRoot, RepositoryRoot / "Cache/Rust/target/release/libRustRouting.so",
        ExpectedCommit,
    )
    return Result


def Main() -> int:
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument("--tier", choices=("deterministic", "harness", "physical"), required=True)
    Parser.add_argument("--expected-commit", required=True)
    Parser.add_argument("--output", type=Path, required=True)
    Arguments = Parser.parse_args()
    Output = Arguments.output.resolve()
    Output.mkdir(parents=True, exist_ok=False)
    Environment = CleanEnvironment()
    Environment["RC_SOURCE_ORACLE_EVIDENCE"] = str(Output / "SourceOracle")
    # The reused provenance probe and acceptance parent must see the same controls.
    os.environ.clear()
    os.environ.update(Environment)
    Receipt = {
        "SchemaVersion": "ci-run-v1", "ExpectedCommit": Arguments.expected_commit,
        "Tier": Arguments.tier, "StartedAtUtc": datetime.now(timezone.utc).isoformat(),
        "Python": platform.python_version(), "Platform": platform.platform(),
        "SourceState": ReadSourceState(RepositoryRoot),
        "PhysicalAcceptance": "not-run", "PerformanceComparison": "not-run",
        "Status": "incomplete", "Steps": [],
        "PythonDependencies": sorted(
            f"{Item.metadata['Name']}=={Item.version}"
            for Item in importlib.metadata.distributions()
        ),
        "DependencyFiles": {
            Name: FileHash(RepositoryRoot / Name)
            for Name in (
                "Tools/CI/requirements.txt", "Tests/Compiler/Synthesis/oracle-requirements.txt",
                "Kernels/Routing/Cargo.lock", "pyproject.toml", ".cargo/config.toml",
                "Validation/Fabric/ServerHarness/gradle.properties",
                "Validation/Fabric/ServerHarness/gradle/wrapper/gradle-wrapper.properties",
                "Validation/Fabric/ServerHarness/build.gradle",
            )
        },
    }
    WriteJson(Output / "Run.json", Receipt)

    def Step(Name: str, Command: list[str]) -> bool:
        Record = RunStep(Name, Command, Output, Environment)
        Receipt["Steps"].append(Record)
        WriteJson(Output / "Run.json", Receipt)
        return Record["ReturnCode"] == 0

    Python = sys.executable
    Cargo = ["cargo", "--version"]
    Gradle = ["./Validation/Fabric/ServerHarness/gradlew", "--no-daemon", "-p", "Validation/Fabric/ServerHarness"]
    try:
        if Receipt["SourceState"] != {"Revision": Arguments.expected_commit, "Dirty": False}:
            raise ValueError("CI requires the exact expected commit and a clean checkout")
        if Arguments.tier == "harness":
            Step("java-version", ["java", "-version"])
            Step("gradle-version", Gradle + ["--version"])
            Step("gradle-test-build", Gradle + ["test", "build"])
            Step("gradle-dependencies", Gradle + ["dependencies"])
        else:
            Step("rust-version", ["rustc", "--version", "--verbose"])
            Step("cargo-version", Cargo)
            Step("rust-format", ["cargo", "fmt", "--manifest-path", "Kernels/Routing/Cargo.toml", "--", "--check"])
            Step("rust-tests", ["cargo", "test", "--manifest-path", "Kernels/Routing/Cargo.toml", "--release", "--locked"])
            if not Step("native-build", [Python, "-m", "maturin", "develop", "--release", "--locked"]):
                raise ValueError("native build failed; Python tests must not use a stale extension")
            Before = Provenance(Arguments.expected_commit)
            WriteJson(Output / "Provenance.before.json", Before)
            if Arguments.tier == "deterministic":
                Step("compileall", [Python, "-m", "compileall", "-q", "PhysicalDesign/Placement", "PhysicalDesign/Routing"])
                Step("structural", [Python, "-m", "pytest", "-q", "Tests/Structural/test_source_structure.py", "Tests/PhysicalDesign/Routing/test_routing_contract_schema.py"])
                Step("collection", [Python, "-m", "pytest", "--collect-only", "-q"])
                Step("pytest", [Python, "-m", "pytest", "-q", "Tests", f"--junitxml={Output / 'pytest.xml'}"])
            else:
                # This path is only scheduled on an administrator-approved disposable runner.
                RuntimeRoot = Path("/opt/redstone-fabric/runtime")
                WriteJson(Output / "FabricPrerequisites.json", FabricPrerequisites(RuntimeRoot))
                Environment["RC_FABRIC_SERVER_ROOT"] = str(RuntimeRoot)
                os.environ["RC_FABRIC_SERVER_ROOT"] = str(RuntimeRoot)
                Step("java-version", ["java", "-version"])
                if not Step("gradle-test-build", Gradle + ["test", "build"]):
                    raise ValueError("current-commit Fabric harness build failed")
                BuiltHarness = RepositoryRoot / "Cache/Gradle/ServerHarness/Build/libs/validation-server-harness-1.0.0.jar"
                # Reject a shared/live runtime before changing its installed harness.
                if not Step("fabric-status", [Python, "Tools/Fabric/ControlFabricServer.py", "status"]):
                    raise ValueError("could not establish stopped Fabric runtime ownership")
                Status = json.loads((Output / "fabric-status.stdout.log").read_text())
                if Status.get("Status") != "stopped":
                    raise ValueError("Fabric runtime must be stopped and dedicated to this job")
                LegacyBuild = RuntimeRoot.parent / "build/libs/validation-server-harness-1.0.0.jar"
                if LegacyBuild.exists():
                    raise ValueError("preprovisioned runtime contains an ambiguous legacy harness build")
                import shutil
                shutil.copyfile(BuiltHarness, RuntimeRoot / "mods/redstonecompiler-harness.jar")
                RuntimeIdentity = FabricPrerequisites(RuntimeRoot)
                if RuntimeIdentity["Harness"]["Sha256"] != FileHash(BuiltHarness):
                    raise ValueError("installed Fabric harness differs from this commit's build")
                WriteJson(Output / "FabricPrerequisites.json", RuntimeIdentity)
                AcceptanceOutput = Output.parent / (Output.name + "-acceptance-private")
                if AcceptanceOutput.exists():
                    raise ValueError("physical acceptance output root must be fresh")
                try:
                    if not Step("fabric-start", [Python, "Tools/Fabric/ControlFabricServer.py", "start"]):
                        raise ValueError("Fabric failed authenticated readiness; acceptance was not run")
                    if FabricPrerequisites(RuntimeRoot) != RuntimeIdentity:
                        raise ValueError("Fabric runtime identity changed during startup")
                    Passed = Step("acceptance", [
                        Python, "Tools/Routing/RunRouterAcceptance.py", "--matrix", "expanded",
                        "--python", Python, "--output-root", str(AcceptanceOutput),
                        "--date", datetime.now(timezone.utc).date().isoformat(),
                    ])
                    Receipt["PhysicalAcceptance"] = "passed" if Passed else "failed"
                finally:
                    Stopped = Step("fabric-stop", [Python, "Tools/Fabric/ControlFabricServer.py", "stop"])
                    CollectAcceptanceEvidence(AcceptanceOutput, Output / "Acceptance")
                    if not Stopped:
                        raise ValueError("Fabric cleanup command failed")
                    StopReceipt = json.loads((Output / "fabric-stop.stdout.log").read_text())
                    if not isinstance(StopReceipt, dict) or StopReceipt.get("Status") != "stopped":
                        raise ValueError("Fabric cleanup did not establish a stopped runtime")
            After = Provenance(Arguments.expected_commit)
            WriteJson(Output / "Provenance.after.json", After)
            if Before != After:
                raise ValueError("source/native/template provenance changed during the run")
        Receipt["Status"] = "passed" if all(Step["ReturnCode"] == 0 for Step in Receipt["Steps"]) else "failed"
    except Exception as Error:
        Receipt["Status"] = "failed"
        Receipt["Failure"] = f"{type(Error).__name__}: {Error}"
        print(Receipt["Failure"], file=sys.stderr)
    finally:
        Receipt["FinalSourceState"] = ReadSourceState(RepositoryRoot)
        if Receipt["FinalSourceState"] != {"Revision": Arguments.expected_commit, "Dirty": False}:
            Receipt["Status"] = "failed"
            Receipt["FinalSourceFailure"] = "source changed during CI execution"
        Receipt["CompletedAtUtc"] = datetime.now(timezone.utc).isoformat()
        WriteJson(Output / "Run.json", Receipt)
        SealEvidence(Output)
    return 0 if Receipt["Status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(Main())

#!/usr/bin/env python3
"""One-off, fixed-source overlap diagnostic; never changes routing budgets."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from hashlib import sha256
import os
from pathlib import Path
import signal
import subprocess
import stat
import sys
from time import monotonic
import traceback

SourceCommit = "097ba27a3b261b346ca53cc5cb65e6e149dd13fd"
SourceTree = "d559bc6a6895cd7963310c35398e3cfd31d5f305"
TestNode = (
    "Tests/Integration/test_deferred_local_routing_access_handoff.py"
    "::test_mandatory_selected_access_overlap_remains_typed_incomplete"
)
DriverRoot = Path(__file__).resolve().parents[2]
Subject = DriverRoot / "subject"
Output = DriverRoot / "Output/CI/overlap"


def WriteJson(PathValue: Path, Value: object) -> None:
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    PathValue.write_text(json.dumps(Value, sort_keys=True, indent=2) + "\n")


EvidenceFiles = frozenset({
    "setup.stdout.log", "setup.stderr.log", "setup-result.txt",
    "native-build.stdout.log", "native-build.stderr.log", "native-build.json",
    "Provenance.before.json", "Provenance.after.json",
    "pytest.stdout.log", "pytest.stderr.log", "pytest.json", "pytest.xml",
    "pytest-phases.jsonl", "runner-traceback.log", "Run.json", "Bootstrap.json",
    "Driver.json",
})


def StageEvidence(Source: Path, Destination: Path) -> None:
    """Fail closed on unexpected entries; publish only regular allowlisted bytes."""
    for Root in (Source, Destination):
        if any(Part.is_symlink() for Part in (Root, *Root.parents)):
            raise ValueError("artifact paths must not traverse symlinks")
    if Destination.exists():
        raise ValueError("artifact staging must be fresh")
    Contents = {}
    for Entry in sorted(Source.iterdir()):
        if Entry.name not in EvidenceFiles or not stat.S_ISREG(Entry.lstat().st_mode):
            raise ValueError("unexpected or nonregular diagnostic evidence")
        Descriptor = os.open(Entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(Descriptor, "rb") as Stream:
            if not stat.S_ISREG(os.fstat(Stream.fileno()).st_mode):
                raise ValueError("diagnostic evidence must be regular files")
            Contents[Entry.name] = Stream.read()
    # Validation precedes any public staging; source-oracle private output,
    # environments, caches and source trees never enter this flat allowlist.
    Destination.mkdir(parents=True, exist_ok=False)
    for Name, Data in Contents.items():
        (Destination / Name).write_bytes(Data)
    WriteJson(Destination / "EvidenceIndex.json", {
        "Files": {Name: sha256(Data).hexdigest() for Name, Data in Contents.items()},
    })


def RunProcess(Command: list[str], Directory: Path, Environment: dict,
               Evidence: Path, Name: str, TimeoutSeconds: float) -> dict:
    """Retain output and kill the whole session on timeout, error, or completion."""
    Evidence.mkdir(parents=True, exist_ok=True)
    Started = monotonic()
    Record = {"Command": Command, "Status": "running", "TimeoutSeconds": TimeoutSeconds}
    WriteJson(Evidence / f"{Name}.json", Record)
    Process = None
    try:
        with (Evidence / f"{Name}.stdout.log").open("w") as Stdout, (Evidence / f"{Name}.stderr.log").open("w") as Stderr:
            Process = subprocess.Popen(Command, cwd=Directory, env=Environment,
                                       stdout=Stdout, stderr=Stderr, start_new_session=True)
            try:
                Record["ReturnCode"] = Process.wait(timeout=TimeoutSeconds)
                Record["TimedOut"] = False
            except subprocess.TimeoutExpired:
                Record.update(ReturnCode=124, TimedOut=True)
    except BaseException as Error:
        Record.update(ReturnCode=1, Failure=f"{type(Error).__name__}: {Error}")
        raise
    finally:
        if Process is not None:
            try:
                os.killpg(Process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            Process.wait()
        Record.update(RuntimeSeconds=monotonic() - Started,
                      Status="passed" if Record.get("ReturnCode") == 0 else "failed")
        WriteJson(Evidence / f"{Name}.json", Record)
    return Record


class PhaseReceipt:
    """Persist completed pytest phases before a later teardown can hang."""

    def pytest_runtest_logreport(self, report):
        Record = {
            "Node": report.nodeid, "Phase": report.when, "Outcome": report.outcome,
            "DurationSeconds": report.duration, "Traceback": str(report.longrepr),
            "CapturedSections": report.sections,
            "RecordedAtUtc": datetime.now(timezone.utc).isoformat(),
        }
        with (Output / "pytest-phases.jsonl").open("a") as Stream:
            Stream.write(json.dumps(Record, sort_keys=True) + "\n")
            Stream.flush()
            os.fsync(Stream.fileno())


def Interrupted(Signum, Frame):
    raise SystemExit(128 + Signum)


def Main() -> int:
    if sys.argv[1:] == ["--stage-evidence"]:
        StageEvidence(Output, Output.with_name("overlap-public"))
        return 0
    # All target/source/deadline choices are fixed; there is no command input surface.
    if sys.argv[1:] not in ([], ["--pytest-child"]):
        raise ValueError("this one-off diagnostic accepts no configurable inputs")
    os.chdir(Subject)
    sys.path.insert(0, str(Subject))
    if sys.argv[1:] == ["--pytest-child"]:
        import pytest
        return pytest.main([
            "-vv", "--setup-show", "--tb=long", "--durations=0", "-rA",
            "-o", "faulthandler_timeout=0", TestNode,
            f"--junitxml={Output / 'pytest.xml'}",
        ], plugins=[PhaseReceipt()])

    for Signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(Signum, Interrupted)
    Output.mkdir(parents=True, exist_ok=True)
    Receipt = {
        "Status": "incomplete", "ExpectedCommit": SourceCommit, "ExpectedTree": SourceTree,
        "Node": TestNode, "PytestWatchdogSeconds": 180, "ProductionBudgetChanged": False,
        "StartedAtUtc": datetime.now(timezone.utc).isoformat(),
        "PhysicalAcceptance": "not-run", "PerformanceComparison": "not-run",
    }
    WriteJson(Output / "Run.json", Receipt)
    try:
        from Tools.CI.RunChecks import CleanEnvironment, Provenance, ReadSourceState
        Environment = CleanEnvironment()
        Environment["RC_SOURCE_ORACLE_EVIDENCE"] = str(Output.with_name("overlap-source-oracle-private"))
        os.environ.clear()
        os.environ.update(Environment)
        if Path(sys.prefix).resolve() != (Subject / ".venv").resolve():
            raise ValueError("the subject must own its Python environment")
        if ReadSourceState(Subject) != {"Revision": SourceCommit, "Dirty": False}:
            raise ValueError("the subject must be the exact clean PR9 source")
        Tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=Subject, text=True).strip()
        if Tree != SourceTree:
            raise ValueError("unexpected source tree")
        Build = RunProcess([sys.executable, "-m", "maturin", "develop", "--release", "--locked"],
                           Subject, Environment, Output, "native-build", 1200)
        if Build["ReturnCode"] != 0:
            raise ValueError("native build failed")
        Before = Provenance(SourceCommit)
        WriteJson(Output / "Provenance.before.json", Before)
        try:
            Test = RunProcess([sys.executable, "-u", str(Path(__file__).resolve()), "--pytest-child"],
                              Subject, Environment, Output, "pytest", 180)
            Receipt["Status"] = "passed" if Test["ReturnCode"] == 0 else "failed"
        finally:
            After = Provenance(SourceCommit)
            WriteJson(Output / "Provenance.after.json", After)
            if Before != After:
                raise ValueError("source/native/templates changed during diagnosis")
    except BaseException as Error:
        Receipt.update(Status="failed", Failure=f"{type(Error).__name__}: {Error}")
        (Output / "runner-traceback.log").write_text(traceback.format_exc())
    finally:
        Receipt["CompletedAtUtc"] = datetime.now(timezone.utc).isoformat()
        WriteJson(Output / "Run.json", Receipt)
    return 0 if Receipt["Status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(Main())

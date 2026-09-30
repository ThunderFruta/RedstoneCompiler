"""The personal-repository workflow reports admission without executing physics."""
import json
import os
from pathlib import Path
import re
import subprocess
import pytest

RepositoryRoot = Path(__file__).resolve().parents[2]
WorkflowPath = RepositoryRoot / ".github/workflows/physical-acceptance.yml"

def WorkflowRunBlocks():
    """Extract literal shell steps without executing GitHub expressions."""
    Lines = WorkflowPath.read_text().splitlines()
    Blocks = []
    for Index, Line in enumerate(Lines):
        if not re.fullmatch(r"\s+run: \|", Line):
            continue
        Indent = len(Line) - len(Line.lstrip()) + 2
        Body = []
        for NextLine in Lines[Index + 1:]:
            if NextLine.strip() and len(NextLine) - len(NextLine.lstrip()) < Indent:
                break
            Body.append(NextLine[Indent:] if NextLine.strip() else "")
        Blocks.append("\n".join(Body) + "\n")
    return Blocks

@pytest.mark.parametrize("Event,Ref,Exit", [
    ("push", "refs/heads/Router-Refactor(R10-N5)", 0),
    ("workflow_dispatch", "refs/heads/Router-Refactor(R10-N5)", 1),
    ("workflow_dispatch", "refs/heads/main", 1),
    ("push", "refs/heads/main", 1),
    ("pull_request", "refs/pull/1/merge", 1),
    ("workflow_dispatch", "$(touch unauthorized-execution)", 1),
])
def test_reporting_never_grants_physical_acceptance(tmp_path, Event, Ref, Exit):
    Environment = {Name: os.environ[Name] for Name in ("PATH", "HOME") if Name in os.environ}
    Environment.update(EVENT_NAME=Event, REQUESTED_REF=Ref, EXPECTED_COMMIT="a" * 40,
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
        READY="ephemeral-v1", RC_PHYSICAL_RUNNER_READY="ephemeral-v1")
    Blocks = WorkflowRunBlocks()
    assert Blocks, "Admission must retain an executable reporting step"
    Results = [subprocess.run(["bash", "-e", "-o", "pipefail", "-c", Block],
        cwd=tmp_path, env=Environment, text=True, capture_output=True, timeout=10) for Block in Blocks]
    assert Results[-1].returncode == Exit
    Receipt = json.loads((tmp_path / "Output/CI/physical-admission/Run.json").read_text())
    assert Receipt["PhysicalAcceptance"] == "not-run"
    assert Receipt["Status"] == "blocked"
    assert Receipt["Accepted"] is False
    assert Receipt["HostedReportingOnly"] is True
    assert Receipt["ExpectedCommit"] == "a" * 40
    assert Receipt["RequestedRef"] == Ref
    assert "not run" in (tmp_path / "summary.md").read_text().lower()
    assert not (tmp_path / "unauthorized-execution").exists()
    assert sorted(P.name for P in tmp_path.iterdir()) == ["Output", "summary.md"]

def test_physical_workflow_has_only_hosted_reporting_capabilities():
    Source = WorkflowPath.read_text()
    Runners = re.findall(r"^\s*runs-on:\s*(.*?)\s*$", Source, re.MULTILINE)
    assert Runners and all(Runner == "ubuntu-24.04" for Runner in Runners)
    Actions = re.findall(r"^\s*uses:\s*(.*?)\s*$", Source, re.MULTILINE)
    assert Actions and all(Action.startswith("actions/upload-artifact@") for Action in Actions)
    for Block in WorkflowRunBlocks():
        assert "RunChecks.py" not in Block
        assert "ControlFabricServer.py" not in Block
        assert "RunRouterAcceptance.py" not in Block
    assert "group: redstone-physical\n" not in Source
    assert "RC_PHYSICAL_RUNNER_READY" not in Source
    assert re.search(r"push:\n\s+branches: \['Router-Refactor\(R10-N5\)'\]", Source)
    assert "workflow_dispatch:" in Source

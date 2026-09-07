from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from Tools.Routing import RunRouterAcceptance as Harness


def _SyntheticManifest(
    Configuration: Harness.AcceptanceConfiguration,
    *,
    Accepted: bool,
    TimedOut: bool = False,
) -> dict[str, object]:
    RunDirectory = Configuration.RecoveryRoot / "FullAdder-Run1"
    RunDirectory.mkdir(parents=True, exist_ok=True)
    (RunDirectory / "stdout.log").write_text("compiler output\n", encoding="utf-8")
    (RunDirectory / "stderr.log").write_text("", encoding="utf-8")
    Status = "PASSED" if Accepted else "FAILED"
    Manifest: dict[str, object] = {
        "SchemaVersion": Harness.AcceptanceManifestSchemaVersion,
        "Status": Status,
        "Accepted": Accepted,
        "MatrixMode": Configuration.MatrixMode,
        "BaselineMode": Configuration.BaselineMode,
        "SourceProvenanceStable": True,
        "SourceProvenance": {
            "SourceContent": {
                "AggregateSha256": "a" * 64,
                "FileCount": 1,
            }
        },
        "Environment": {},
        "Runs": [
            {
                "Sequence": 1,
                "RunName": "FullAdder-Run1",
                "Circuit": "FullAdder",
                "Status": Status,
                "Accepted": Accepted,
                "Evaluation": {
                    "Process": {
                        "WallRuntimeSeconds": 1.0,
                        "ReturnCode": 0 if Accepted else 1,
                        "TimedOut": TimedOut,
                    },
                    "Observed": {
                        "FabricValidationStatus": "passed" if Accepted else "not-run"
                    },
                    "Failures": [] if Accepted else ["synthetic failure"],
                },
            }
        ],
    }
    Harness.WriteManifest(Configuration.ManifestPath, Manifest)
    return Manifest


def _Arguments(OutputRoot: Path, *Extra: str) -> list[str]:
    return [
        "--matrix",
        "default",
        "--date",
        "2026-09-03",
        "--output-root",
        str(OutputRoot),
        *Extra,
    ]


def _ArchiveDirectories(OutputRoot: Path) -> list[Path]:
    ArchiveRoot = OutputRoot / "2026-09-03" / "Archives"
    return sorted(
        (PathValue for PathValue in ArchiveRoot.iterdir() if PathValue.is_dir()),
        key=lambda PathValue: PathValue.name,
    ) if ArchiveRoot.is_dir() else []


@pytest.mark.parametrize(("Accepted", "ExpectedCode"), ((True, 0), (False, 1)))
def test_main_automatically_archives_passes_and_failures(
    tmp_path: Path,
    Accepted: bool,
    ExpectedCode: int,
):
    OutputRoot = tmp_path / ("pass" if Accepted else "fail")

    def Run(Configuration: Harness.AcceptanceConfiguration):
        assert Configuration.ArchiveSessionRoot is not None
        return _SyntheticManifest(Configuration, Accepted=Accepted)

    StandardOutput = StringIO()
    with patch.object(Harness, "RunAcceptance", side_effect=Run), redirect_stdout(
        StandardOutput
    ):
        ReturnCode = Harness.Main(_Arguments(OutputRoot))

    assert ReturnCode == ExpectedCode
    Archives = _ArchiveDirectories(OutputRoot)
    assert len(Archives) == 1
    Archive = Archives[0]
    assert Archive.name in StandardOutput.getvalue()
    assert (Archive / "Summary.txt").is_file()
    assert (Archive / "RawDump.txt").is_file()
    assert (Archive / "AcceptanceManifest.json").is_file()
    assert (Archive / "ArchiveManifest.json").is_file()
    assert (Archive / "SHA256SUMS").is_file()
    ArchiveManifest = json.loads(
        (Archive / "ArchiveManifest.json").read_text(encoding="utf-8")
    )
    assert ArchiveManifest["Publication"]["Status"] == "SEALED"
    assert ArchiveManifest["Benchmark"]["ExitCode"] == ExpectedCode
    assert ArchiveManifest["Benchmark"]["Accepted"] is Accepted


def test_main_no_archive_preserves_disposable_recovery_layout(tmp_path: Path):
    OutputRoot = tmp_path / "disposable"

    def Run(Configuration: Harness.AcceptanceConfiguration):
        assert Configuration.ArchiveSessionRoot is None
        return _SyntheticManifest(Configuration, Accepted=True)

    with patch.object(Harness, "RunAcceptance", side_effect=Run):
        ReturnCode = Harness.Main(_Arguments(OutputRoot, "--no-archive"))

    assert ReturnCode == 0
    assert not _ArchiveDirectories(OutputRoot)
    assert (OutputRoot / "2026-09-03" / "Summary.txt").is_file()


def test_main_dry_run_never_creates_an_archive(tmp_path: Path):
    OutputRoot = tmp_path / "dry"

    def Run(Configuration: Harness.AcceptanceConfiguration):
        assert Configuration.DryRun is True
        Manifest = _SyntheticManifest(Configuration, Accepted=False)
        Manifest["Status"] = "DRY_RUN"
        Harness.WriteManifest(Configuration.ManifestPath, Manifest)
        return Manifest

    with patch.object(Harness, "RunAcceptance", side_effect=Run):
        ReturnCode = Harness.Main(_Arguments(OutputRoot, "--dry-run"))

    assert ReturnCode == 0
    assert not _ArchiveDirectories(OutputRoot)


def test_main_timeout_remains_a_failed_sealed_archive(tmp_path: Path):
    OutputRoot = tmp_path / "timeout"

    def Run(Configuration: Harness.AcceptanceConfiguration):
        return _SyntheticManifest(Configuration, Accepted=False, TimedOut=True)

    with patch.object(Harness, "RunAcceptance", side_effect=Run):
        ReturnCode = Harness.Main(_Arguments(OutputRoot))

    ArchiveManifest = json.loads(
        (_ArchiveDirectories(OutputRoot)[0] / "ArchiveManifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert ReturnCode == 1
    assert ArchiveManifest["Publication"]["Status"] == "SEALED"
    assert ArchiveManifest["Benchmark"]["Runs"][0]["TimedOut"] is True


def test_main_interruption_retains_an_interrupted_archive(tmp_path: Path):
    OutputRoot = tmp_path / "interrupted"

    def Interrupt(Configuration: Harness.AcceptanceConfiguration):
        Configuration.RecoveryRoot.mkdir(parents=True, exist_ok=True)
        (Configuration.RecoveryRoot / "partial.jsonl").write_text(
            '{"partial":true}\n',
            encoding="utf-8",
        )
        raise KeyboardInterrupt()

    with patch.object(Harness, "RunAcceptance", side_effect=Interrupt):
        ReturnCode = Harness.Main(_Arguments(OutputRoot))

    Archive = _ArchiveDirectories(OutputRoot)[0]
    ArchiveManifest = json.loads(
        (Archive / "ArchiveManifest.json").read_text(encoding="utf-8")
    )
    assert ReturnCode == 130
    assert (Archive / "partial.jsonl").is_file()
    assert ArchiveManifest["Publication"]["Status"] == "INTERRUPTED"
    assert ArchiveManifest["Benchmark"]["ExitClassification"] == "interrupted"


def test_reporting_failure_is_archived_as_partial_and_returns_nonzero(
    tmp_path: Path,
):
    OutputRoot = tmp_path / "reporting-failure"

    def Run(Configuration: Harness.AcceptanceConfiguration):
        return _SyntheticManifest(Configuration, Accepted=True)

    with (
        patch.object(Harness, "RunAcceptance", side_effect=Run),
        patch.object(Harness, "WriteRunReport", side_effect=OSError("disk full")),
    ):
        ReturnCode = Harness.Main(_Arguments(OutputRoot))

    ArchiveManifest = json.loads(
        (_ArchiveDirectories(OutputRoot)[0] / "ArchiveManifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert ReturnCode == 1
    assert ArchiveManifest["Publication"]["Status"] == "PARTIAL"
    assert ArchiveManifest["Benchmark"]["ExitClassification"] == "reporting-failure"


def test_archive_write_failure_changes_success_to_nonzero(tmp_path: Path):
    OutputRoot = tmp_path / "archive-failure"

    def Run(Configuration: Harness.AcceptanceConfiguration):
        return _SyntheticManifest(Configuration, Accepted=True)

    StandardOutput = StringIO()
    with (
        patch.object(Harness, "RunAcceptance", side_effect=Run),
        patch.object(
            Harness,
            "PublishBenchmarkArchive",
            side_effect=OSError("archive disk full"),
        ),
        redirect_stdout(StandardOutput),
    ):
        ReturnCode = Harness.Main(_Arguments(OutputRoot))

    assert ReturnCode == 1
    assert "Archiving: write-failed" in StandardOutput.getvalue()


@pytest.mark.parametrize("Mode", ("capture", "compare"))
def test_baseline_modes_keep_fixed_recovery_and_receive_separate_archive_mirror(
    tmp_path: Path,
    Mode: str,
):
    OutputRoot = tmp_path / Mode
    BaselinePath = tmp_path / "reference.json"
    SeenRecoveryRoots: list[Path] = []

    def Run(Configuration: Harness.AcceptanceConfiguration):
        assert Configuration.ArchiveSessionRoot is None
        SeenRecoveryRoots.append(Configuration.RecoveryRoot)
        return _SyntheticManifest(Configuration, Accepted=False)

    Arguments = _Arguments(
        OutputRoot,
        f"--{Mode}-baseline",
        str(BaselinePath),
        "--routing-threads",
        str(Harness.RequiredRegressionRoutingThreads),
        "--python",
        str(Harness.RepositoryRoot / ".venv" / "bin" / "python"),
    )
    # This synthetic archive test never launches Python. Simulate only the
    # required interpreter file instead of depending on a worktree-local venv;
    # keep the production CLI's exact interpreter-path validation intact.
    RequiredPython = Harness.RepositoryRoot / ".venv" / "bin" / "python"
    OriginalIsFile = Path.is_file
    with (
        patch.object(Harness, "RunAcceptance", side_effect=Run),
        patch.object(
            Path, "is_file",
            lambda Value: Value == RequiredPython or OriginalIsFile(Value),
        ),
    ):
        ReturnCode = Harness.Main(Arguments)

    assert ReturnCode == 1
    assert SeenRecoveryRoots[0].name == (
        "BaselineCapture" if Mode == "capture" else "CandidateComparison"
    )
    Archive = _ArchiveDirectories(OutputRoot)[0]
    assert Archive != SeenRecoveryRoots[0]
    assert (Archive / "AcceptanceManifest.json").is_file()
    assert (SeenRecoveryRoots[0] / "AcceptanceManifest.json").is_file()

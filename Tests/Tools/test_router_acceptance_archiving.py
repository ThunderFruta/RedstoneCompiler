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


@pytest.mark.parametrize("InvalidAccepted", (0, 1, "false", None))
def test_main_fails_closed_on_non_boolean_acceptance(
    tmp_path: Path,
    InvalidAccepted: object,
):
    OutputRoot = tmp_path / f"invalid-{type(InvalidAccepted).__name__}"

    def Run(Configuration: Harness.AcceptanceConfiguration):
        Manifest = _SyntheticManifest(Configuration, Accepted=False)
        Manifest["Status"] = "PASSED"
        Manifest["Accepted"] = InvalidAccepted
        Manifest["Runs"][0]["Status"] = "PASSED"
        Manifest["Runs"][0]["Accepted"] = InvalidAccepted
        Harness.WriteManifest(Configuration.ManifestPath, Manifest)
        return Manifest

    with patch.object(Harness, "RunAcceptance", side_effect=Run):
        ReturnCode = Harness.Main(_Arguments(OutputRoot))

    assert ReturnCode == 1
    ArchiveManifest = json.loads(
        (_ArchiveDirectories(OutputRoot)[0] / "ArchiveManifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert ArchiveManifest["Publication"]["Status"] == "PARTIAL"
    assert ArchiveManifest["Benchmark"]["ExitClassification"] == (
        "unexpected-harness-failure"
    )
    assert ArchiveManifest["Benchmark"]["Accepted"] is False
    assert ArchiveManifest["Benchmark"]["Runs"][0]["Accepted"] is False


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

# Report integrity cases use independent bytes and explicitly retained identities.
from hashlib import sha256
from Tests.App.test_routing_failure_artifacts import WriteLiteralPair, LITERAL_HTML, HTML_NAME, RECEIPT_NAME
from Tests.Tools.test_router_acceptance_harness import BuildTestAcceptanceCommand, WriteNestedRoutingFailureArtifact
from Tools.Routing import CaptureRoutingDesignSnapshot as SnapshotTool


def test_acceptance_evaluator_observes_report_without_changing_failure(tmp_path):
    Case = next(Value for Value in Harness.AcceptanceCases if Value.Name == "FullAdder")
    Artifacts = Harness.BuildRunArtifacts(tmp_path / "FullAdder-Run1", "FullAdder-Run1")
    Command = BuildTestAcceptanceCommand(Case, Artifacts)
    Source = WriteNestedRoutingFailureArtifact(Case, Artifacts, RunDirectoryName="current",
        Failure={"Stage": "Candidate", "Reason": "SupportConflict", "Detail": "literal rejection"})
    Arguments = dict(Case=Case, Process=Harness.AcceptanceCommandResult(1, "", "", 1.0),
        Artifacts=Artifacts, ExpectedSeed=0, ExpectedSourceRevision="revision",
        ExpectedPolicyProvenance=Harness.BuildPolicyProvenanceRecord("default"),
        DirectArtifactAbsentBeforeInvocation=True, PriorNestedRunDirectoryNames=frozenset(), ExpectedCommand=Command)
    Missing, _ = Harness.EvaluateRun(**Arguments)
    assert Missing["Observed"]["RoutingFailureReport"]["Status"] == "Unavailable"
    WriteLiteralPair(Source.parent, Source.name, Source.read_bytes())
    Present, _ = Harness.EvaluateRun(**Arguments)
    assert Present["Observed"]["RoutingFailureReport"]["Status"] == "Available"
    assert Present["Artifacts"]["RoutingFailureReport"]["Sha256"] == sha256(LITERAL_HTML).hexdigest()
    assert Present["Failures"] == Missing["Failures"]
    assert Present["Accepted"] is Missing["Accepted"] is False
    (Source.parent / HTML_NAME).write_bytes(LITERAL_HTML + b"changed")
    Changed, _ = Harness.EvaluateRun(**Arguments)
    assert Changed["Observed"]["RoutingFailureReport"]["Status"] == "Rejected"
    assert Changed["Failures"] == Missing["Failures"]


def _RunWithReport(Configuration, Present=True):
    Manifest = _SyntheticManifest(Configuration, Accepted=False)
    Root = Configuration.RecoveryRoot / "FullAdder-Run1" / "Runs" / "compiler"
    Source, Records = WriteLiteralPair(Root)
    if not Present:
        (Root / HTML_NAME).unlink(); (Root / RECEIPT_NAME).unlink()
        Records = {"RoutingFailure": Records["RoutingFailure"]}
    Evaluation = Manifest["Runs"][0]["Evaluation"]
    Evaluation["Artifacts"] = Records
    Evaluation["Observed"]["FailureArtifactResolution"] = {"Status": "nested", "Path": str(Source)}
    Harness.WriteManifest(Configuration.ManifestPath, Manifest)
    return Manifest


@pytest.mark.parametrize("Present", [True, False])
def test_failed_case_automatically_archives_report_pair_or_honest_absence(tmp_path, Present):
    with patch.object(Harness, "RunAcceptance", side_effect=lambda Config: _RunWithReport(Config, Present)):
        Code = Harness.Main(_Arguments(tmp_path))
    assert Code == 1
    Archive = _ArchiveDirectories(tmp_path)[0]
    Manifest = json.loads((Archive / "ArchiveManifest.json").read_text())
    Run = Manifest["Benchmark"]["Runs"][0]
    assert Run["Stage"] == "Candidate" and Run["Reason"] == "SupportConflict"
    assert Run["RoutingFailureReport"]["Status"] == ("Available" if Present else "Unavailable")
    Sealed = SnapshotTool.BuildSealedArchiveEvidence(Archive / "AcceptanceManifest.json")
    Members = Sealed.ObservationsByRelativePath
    Name = "FullAdder-Run1/Runs/compiler/" + HTML_NAME
    assert (Name in Members) == Present
    if Present:
        assert Members[Name].Data == LITERAL_HTML
        assert Members[Name].Sha256 == sha256(LITERAL_HTML).hexdigest()
        assert "FullAdder-Run1/Runs/compiler/" + RECEIPT_NAME in Members


@pytest.mark.parametrize("Damage", ["missing", "altered", "symlink", "duplicate", "unlisted"])
def test_sealed_archive_rejects_damaged_or_unlisted_report_members(tmp_path, Damage):
    with patch.object(Harness, "RunAcceptance", side_effect=lambda Config: _RunWithReport(Config, Damage != "unlisted")):
        assert Harness.Main(_Arguments(tmp_path)) == 1
    Archive = _ArchiveDirectories(tmp_path)[0]
    Report = Archive / "FullAdder-Run1/Runs/compiler" / HTML_NAME
    Source = Report.parent / "Design.RoutingFailure.json"
    Original = Source.read_bytes()
    if Damage == "missing": Report.unlink()
    if Damage == "altered": Report.write_bytes(LITERAL_HTML + b"tamper")
    if Damage == "symlink":
        Outside = tmp_path / "outside"; Outside.write_bytes(LITERAL_HTML)
        Report.unlink(); Report.symlink_to(Outside)
    if Damage == "unlisted": Report.write_bytes(LITERAL_HTML)
    if Damage == "duplicate":
        Seal = Archive / "SHA256SUMS"
        Line = next(Line for Line in Seal.read_text().splitlines() if Line.endswith("/" + HTML_NAME))
        Seal.write_text(Seal.read_text() + Line + "\n")
    with pytest.raises(ValueError):
        SnapshotTool.BuildSealedArchiveEvidence(Archive / "AcceptanceManifest.json")
    assert Source.read_bytes() == Original


def test_archive_mirror_preserves_pair_after_original_directory_is_moved(tmp_path):
    from Tests.App.test_benchmark_archive import _ArchiveContext, _SourceIdentity, _Manifest
    from App.BenchmarkArchive import PublishBenchmarkArchive, BuildArchiveRunSurface
    SourceRoot = tmp_path / "source"
    Source, Records = WriteLiteralPair(SourceRoot / "FullAdder-Run1" / "Runs" / "compiler")
    Manifest = _Manifest()
    Evaluation = Manifest["Runs"][0]["Evaluation"]
    Evaluation["Artifacts"] = Records
    Evaluation["Observed"]["FailureArtifactResolution"] = {"Status": "nested", "Path": str(Source)}
    (SourceRoot / "AcceptanceManifest.json").write_text(json.dumps(Manifest))
    Archive = tmp_path / "archive"
    PublishBenchmarkArchive(_ArchiveContext(SourceRoot, Archive), Manifest,
        CompletedAtUtc="2026-09-27T03:00:00+00:00", WallSeconds=1, ExitCode=1,
        ExitClassification="acceptance-failed", SourceIdentityReader=lambda _: _SourceIdentity())
    SourceRoot.rename(tmp_path / "original-no-longer-at-recorded-path")
    Surface = BuildArchiveRunSurface(Manifest, Archive)
    assert Surface[0]["RoutingFailureReport"]["Status"] == "Available"
    assert Surface[0]["Reason"] == "SupportConflict"
    assert (Archive / "FullAdder-Run1/Runs/compiler" / HTML_NAME).read_bytes() == LITERAL_HTML
    assert SnapshotTool.BuildSealedArchiveEvidence(Archive / "AcceptanceManifest.json")


@pytest.mark.parametrize("Selected", [True, False])
def test_coherently_sealed_extra_pair_is_rejected_as_case_report_evidence(tmp_path, Selected):
    def Run(Configuration):
        Manifest = _RunWithReport(Configuration, True)
        RunRoot = Configuration.RecoveryRoot / "FullAdder-Run1"
        WriteLiteralPair(RunRoot / "Runs" / "older-compiler")
        if not Selected:
            Evaluation = Manifest["Runs"][0]["Evaluation"]
            Evaluation.pop("Artifacts")
            Evaluation["Observed"].pop("FailureArtifactResolution")
        Harness.WriteManifest(Configuration.ManifestPath, Manifest)
        return Manifest
    with patch.object(Harness, "RunAcceptance", side_effect=Run):
        assert Harness.Main(_Arguments(tmp_path)) == 1
    Archive = _ArchiveDirectories(tmp_path)[0]
    Sealed = SnapshotTool.BuildSealedArchiveEvidence(Archive / "AcceptanceManifest.json")
    assert Sealed  # Every file is honestly checksummed; semantic rejection is separate.
    Manifest = json.loads((Archive / "ArchiveManifest.json").read_text())
    assert Manifest["Benchmark"]["Runs"][0]["RoutingFailureReport"]["Status"] == "Rejected"
    if Selected:
        assert Manifest["Benchmark"]["Runs"][0]["Reason"] == "SupportConflict"


def test_captured_self_conflict_survives_real_report_publisher_and_archive(tmp_path):
    from Tests.App.test_routing_failure_report import LiteralFailure, LiteralEvidence
    from App.RoutingFailureReport import PublishRoutingFailureReport
    from App.RoutingFailureArtifacts import ObserveReportPair
    def Run(Configuration):
        Manifest = _SyntheticManifest(Configuration, Accepted=False)
        Root = Configuration.RecoveryRoot / "FullAdder-Run1" / "Runs" / "compiler"
        Root.mkdir(parents=True)
        Source = Root / "Design.RoutingFailure.json"
        Source.write_text(json.dumps(LiteralFailure(LiteralEvidence())))
        Data = Source.read_bytes()
        Identity = {"Path": str(Source), "Bytes": len(Data), "Sha256": sha256(Data).hexdigest()}
        assert PublishRoutingFailureReport(RunDirectory=Root, FailurePath=Source, SourceIdentity=Identity)["Status"] == "Published"
        Pair = ObserveReportPair(Source, Data)
        assert Pair["Status"] == "Available"
        Records = {"RoutingFailure": {"Path": str(Source), "Exists": True,
                    "SizeBytes": len(Data), "Sha256": sha256(Data).hexdigest()},
                   "RoutingFailureReport": Pair["Artifacts"][HTML_NAME],
                   "RoutingFailureReportReceipt": Pair["Artifacts"][RECEIPT_NAME]}
        Evaluation = Manifest["Runs"][0]["Evaluation"]
        Evaluation["Artifacts"] = Records
        Evaluation["Observed"]["FailureArtifactResolution"] = {"Status": "nested", "Path": str(Source)}
        Harness.WriteManifest(Configuration.ManifestPath, Manifest)
        return Manifest
    with patch.object(Harness, "RunAcceptance", side_effect=Run):
        assert Harness.Main(_Arguments(tmp_path)) == 1
    Archive = _ArchiveDirectories(tmp_path)[0]
    Sealed = SnapshotTool.BuildSealedArchiveEvidence(Archive / "AcceptanceManifest.json")
    Report = Sealed.ObservationsByRelativePath["FullAdder-Run1/Runs/compiler/" + HTML_NAME].Data
    Source = json.loads(Sealed.ObservationsByRelativePath["FullAdder-Run1/Runs/compiler/Design.RoutingFailure.json"].Data)
    assert Source["Failure"]["Diagnostics"]["Admission"]["SelfClaimConflictEvidence"] == LiteralEvidence()
    assert b"(4, 2, -1)" in Report and b"Support, Air" in Report
    assert b"ContributorProvenance" in Report and b"Unavailable" in Report
    assert b"not a proven upstream root cause" in Report

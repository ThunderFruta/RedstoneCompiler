"""Independent report membership/byte oracles at public snapshot boundaries."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from Tests.App.test_routing_failure_artifacts import WriteLiteralPair, HTML_NAME, RECEIPT_NAME, LITERAL_HTML
from Tests.Structural.test_routing_design_snapshot import WriteSyntheticFailure
from Tools.Routing import CaptureRoutingDesignSnapshot as Snap

RepositoryRoot = Path(__file__).resolve().parents[2]


def BuildSnapshotWithReport(Root, Present=True):
    SourceRoot = Root / "source"; SourceRoot.mkdir(parents=True)
    Source = WriteSyntheticFailure(SourceRoot)
    if Present:
        WriteLiteralPair(SourceRoot, Source.name, Source.read_bytes())
    Config = Snap.SnapshotConfiguration(RepositoryRoot=RepositoryRoot, OutputRoot=Root / "snapshots",
        CapturedAtUtc=datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc), Cla4FailurePath=Source)
    Snapshot = Snap.BuildRoutingDesignSnapshot(Config)
    return Source, Config, Snapshot


def test_snapshot_copies_verified_pair_and_uses_retained_bytes_after_replacement(tmp_path):
    Source, Config, Snapshot = BuildSnapshotWithReport(tmp_path)
    OriginalFailure = Source.read_bytes()
    assert Snapshot["RoutingFailureReport"]["Status"] == "Available"
    (Source.parent / HTML_NAME).write_bytes(b"changed after observation")
    (Source.parent / RECEIPT_NAME).write_bytes(b"changed receipt")
    Bundle = Snap.WriteSnapshotStaged(Config, Snapshot)
    assert (Bundle / "Artifacts" / HTML_NAME).read_bytes() == LITERAL_HTML
    assert (Bundle / "Artifacts" / Source.name).read_bytes() == OriginalFailure
    Moved = tmp_path / "relocated-snapshot"; Bundle.rename(Moved)
    Readback = Snap.ReadRoutingDesignSnapshot(Moved)
    assert Readback["RoutingFailureReport"]["Status"] == "Available"
    assert Readback["RoutingFailureReport"]["Report"]["Sha256"] == sha256(LITERAL_HTML).hexdigest()
    assert b"(4, 2, -1)" in (Moved / "Artifacts" / HTML_NAME).read_bytes()


def test_old_failure_does_not_acquire_scene_or_report_from_current_code(tmp_path):
    Source, Config, Snapshot = BuildSnapshotWithReport(tmp_path, False)
    Original = Source.read_bytes()
    assert Snapshot["RoutingFailureReport"]["Status"] == "Unavailable"
    Bundle = Snap.WriteSnapshotStaged(Config, Snapshot)
    assert Snap.ReadRoutingDesignSnapshot(Bundle)["RoutingFailureReport"]["Status"] == "Unavailable"
    assert not (Bundle / "Artifacts" / HTML_NAME).exists()
    assert Source.read_bytes() == Original


@pytest.mark.parametrize("Damage", ["missing", "altered", "symlink", "duplicate", "unlisted"])
def test_snapshot_readback_rejects_report_seal_damage(tmp_path, Damage):
    Source, Config, Snapshot = BuildSnapshotWithReport(tmp_path, Damage != "unlisted")
    Bundle = Snap.WriteSnapshotStaged(Config, Snapshot)
    Report = Bundle / "Artifacts" / HTML_NAME
    OriginalFailure = (Bundle / "Artifacts" / Source.name).read_bytes()
    if Damage == "missing": Report.unlink()
    if Damage == "altered": Report.write_bytes(LITERAL_HTML + b"changed")
    if Damage == "symlink":
        Target = tmp_path / "outside"; Target.write_bytes(LITERAL_HTML)
        Report.unlink(); Report.symlink_to(Target)
    if Damage == "unlisted": Report.write_bytes(LITERAL_HTML)
    if Damage == "duplicate":
        Seal = Bundle / "SHA256SUMS"
        Line = next(Line for Line in Seal.read_text().splitlines() if Line.endswith("/" + HTML_NAME))
        Seal.write_text(Seal.read_text() + Line + "\n")
    with pytest.raises(ValueError):
        Snap.ReadRoutingDesignSnapshot(Bundle)
    assert (Bundle / "Artifacts" / Source.name).read_bytes() == OriginalFailure


def test_malformed_report_is_exposed_but_never_copied_as_valid_snapshot_evidence(tmp_path):
    Source, Config, _ = BuildSnapshotWithReport(tmp_path)
    (Source.parent / HTML_NAME).write_bytes(b"malformed report")
    Snapshot = Snap.BuildRoutingDesignSnapshot(Config)
    assert Snapshot["RoutingFailureReport"]["Status"] == "Rejected"
    assert not any(Path(Record["SnapshotPath"]).name in (HTML_NAME, RECEIPT_NAME) for Record in Snapshot["Artifacts"])
    assert Snapshot["Cla4Failure"]["Reason"] == "PlacementOverlap"


def test_snapshot_run_projection_rejects_sealed_report_without_selected_failure(tmp_path):
    from Tests.Structural.test_routing_design_snapshot import WriteSealedAcceptanceFixture
    Run = {"RunName": "Case-Run1", "Status": "FAILED", "Accepted": False,
           "Command": ["compiler", "--routing-strategy", "default"],
           "Evaluation": {"Accepted": False, "Failures": ["original failure"], "Observed": {}}}
    Source, _ = WriteLiteralPair(tmp_path / "Case-Run1")
    ManifestPath = tmp_path / "AcceptanceManifest.json"
    WriteSealedAcceptanceFixture(ManifestPath, {"Accepted": False, "Runs": [Run],
        "SourceState": {"Revision": "literal-fixture", "Dirty": False},
        "MatrixMode": "default", "BaselineMode": None},
        (Source, Source.parent / HTML_NAME, Source.parent / RECEIPT_NAME))
    Archive = Snap.BuildSealedArchiveEvidence(ManifestPath)
    Summary = Snap.BuildAcceptanceRunSummary(Run, RequestedStrategy="default", ResolvedUsedStrategy="default",
        PolicyRecord={"Snapshot": {}}, PolicyIdentity={"Snapshot": {}}, ArchiveEvidence=Archive)
    assert Summary["RoutingFailureReport"]["Status"] == "Rejected"
    assert Summary["EvaluatorFailures"] == ["original failure"]
    assert Summary["Accepted"] is False


def test_snapshot_output_directory_swap_cannot_redirect_report_copy(tmp_path, monkeypatch):
    import os
    Source, Config, Snapshot = BuildSnapshotWithReport(tmp_path)
    Outside = tmp_path / "outside"; Outside.mkdir()
    Moved = tmp_path / "original-output"
    MakeDirectory = os.mkdir
    Swapped = False
    def SwapAfterArtifactDirectory(Name, *Arguments, **Keywords):
        nonlocal Swapped
        Result = MakeDirectory(Name, *Arguments, **Keywords)
        if not Swapped and Path(Name).name == "Artifacts":
            Swapped = True
            Parent = (Path(os.readlink(f"/proc/self/fd/{Keywords['dir_fd']}"))
                      if "dir_fd" in Keywords else Path(Name).parent)
            Mirror = Outside / Parent.name / "Artifacts"
            Mirror.mkdir(parents=True)
            (Mirror / HTML_NAME).write_bytes(b"outside sentinel")
            Config.OutputRoot.rename(Moved)
            Config.OutputRoot.symlink_to(Outside, target_is_directory=True)
        return Result
    monkeypatch.setattr(os, "mkdir", SwapAfterArtifactDirectory)
    with pytest.raises((ValueError, OSError, RuntimeError)):
        Snap.WriteSnapshotStaged(Config, Snapshot)
    assert Swapped
    OutsideFiles = [Value for Value in Outside.rglob("*") if Value.is_file()]
    assert len(OutsideFiles) == 1
    assert OutsideFiles[0].read_bytes() == b"outside sentinel"
    assert Source.exists()

"""Independent literal artifact and CLI-boundary oracles for failure reporting.

Contract frozen in the implementation brief before implementation inspection.
No routing/claim producer is used to manufacture the expected spatial facts.
"""

from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path

import pytest

from App.CompilerCli import Main
from App.RunReporting import WriteRunReport
from App.RoutingFailureReport import PublishRoutingFailureReport
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError


def LiteralEvidence():
    return {
        "SchemaVersion": "materialization-self-conflict-evidence-v1",
        "Scope": "FirstDecisiveMaterializationSelfClaimPredicate",
        "Predicate": "FindSelfClaimConflicts", "Signal": "T",
        "CaptureStatus": "Complete", "ObservedConflictCount": 1,
        "RetainedConflictCount": 1, "ConflictCoverage": "Complete",
        "Conflicts": [{"Kind": "Support", "Position": [4, 2, -1],
                       "ClaimKinds": ["Support", "Air"]}],
        "NodeCount": 19, "ClaimCounts": {"Wire": 19, "Support": 19,
                                         "Air": 6, "Electrical": 150},
        "ContributorProvenance": "Unavailable", "EvaluationIdentity": "NotCaptured",
        "Omissions": [],
    }


def LiteralFailure(Evidence=None):
    return {
        "SchemaVersion": "routing-failure-v1",
        "Failure": {"Stage": "Candidate", "Reason": "ClusterInterfaceSolveIncomplete",
                    "AffectedNets": ["T"], "Resources": ["Support"],
                    "Locations": [[4, 2, -1]], "RepairActions": ["reconsider placement"],
                    "Detail": "P1SelfClaimConflict",
                    "Diagnostics": {} if Evidence is None else {
                        "Admission": {"SelfClaimConflictEvidence": Evidence}}},
        "Deadline": {"Expired": False, "RemainingMilliseconds": 17},
    }


def SaveFailure(Root, Payload):
    Source = Root / "Design.RoutingFailure.json"
    Source.write_text(json.dumps(Payload), encoding="utf-8")
    return Source


def WriteReport(Root, Source, Result="FAILURE"):
    return WriteRunReport(
        RunDirectory=Root, Result=Result, WallSeconds=0.1, CpuSeconds=0.01,
        Summary="original routing result", FailureType="Candidate: ClusterInterfaceSolveIncomplete",
        RepositoryRoot=Root, WorkingDirectory=Root, StartedAtUtc="start", CompletedAtUtc="end",
        Command=["compiler"], RoutingFailurePath=Source,
    )


@pytest.fixture(autouse=True)
def IsolateRuntimeProvenance(monkeypatch):
    monkeypatch.setattr("App.RunReporting.BuildGitIdentity", lambda _: {})
    monkeypatch.setattr("App.RunReporting.BuildRuntimeProvenance", lambda: {})


def test_failed_run_publishes_one_source_bound_report_and_inventories_it(tmp_path):
    Source = SaveFailure(tmp_path, LiteralFailure(LiteralEvidence()))
    Original = Source.read_bytes()
    Report = WriteReport(tmp_path, Source)
    HtmlPaths = list(tmp_path.glob("*.html"))
    assert len(HtmlPaths) == 1
    Html = HtmlPaths[0].read_text()
    assert str(Source) in Html
    assert sha256(Original).hexdigest() in Html
    assert f">{len(Original)}<" in Html
    assert Source.read_bytes() == Original
    assert "(4, 2, -1)" in Html
    assert "Support, Air" in Html
    assert "ContributorProvenance" in Html and "Unavailable" in Html
    assert "not a proven upstream root cause" in Html
    assert "not proof that no other route exists" in Html
    assert "19" in Html and "150" in Html
    assert "Suggested repair actions (not proof of execution)" in Html
    assert "reconsider placement" in Html and "RemainingMilliseconds: 17" in Html
    assert Html.count("not-run — routing failure prevented this phase") == 3
    assert str(HtmlPaths[0]) in Report.SummaryPath.read_text()
    Raw = Report.RawReportPath.read_text()
    assert str(HtmlPaths[0]) in Raw
    assert sha256(HtmlPaths[0].read_bytes()).hexdigest() in Raw
    assert "FAILURE REPORT:" in "\n".join(Report.ResultLines)


def test_missing_spatial_evidence_keeps_typed_details_and_explicit_unavailable(tmp_path):
    Source = SaveFailure(tmp_path, LiteralFailure())
    WriteReport(tmp_path, Source)
    Html = (tmp_path / "RoutingFailureReport.html").read_text()
    assert "ClusterInterfaceSolveIncomplete" in Html and "Candidate" in Html
    assert "Spatial evidence state" in Html and "Unavailable" in Html
    assert "Missing coordinates do not mean no conflict exists" in Html
    assert "<table>" not in Html


@pytest.mark.parametrize("Mutation,Expected", [
    ({"SchemaVersion": "future-v2"}, "Unsupported"),
    ({"Conflicts": [{"Kind": "Support", "Position": [4, 2], "ClaimKinds": ["Support", "Air"]}]}, "Malformed"),
    ({"RetainedConflictCount": 2}, "Malformed"),
    ({"CaptureStatus": "Partial", "ObservedConflictCount": 20, "Omissions": ["ConflictRetentionLimit"]}, "Malformed"),
    ({"Signal": ""}, "Malformed"),
    ({"CaptureStatus": "Partial", "Omissions": []}, "Malformed"),
    ({"Predicate": "GuessedLater"}, "Malformed"),
    ({"ContributorProvenance": "guessed gate"}, "Malformed"),
    ({"ClaimCounts": {"Support": 19}}, "Malformed"),
    ({"CaptureStatus": "Partial", "ObservedConflictCount": 20, "ConflictCoverage": "Partial", "Omissions": ["ConflictRetentionLimit"]}, "Partial"),
    ({"CaptureStatus": "Complete", "Omissions": ["ClaimCellLimit"]}, "Malformed"),
    ({"CaptureStatus": "Unavailable", "Omissions": ["CaptureError"]}, "Unavailable"),
])
def test_scene_states_do_not_claim_false_completeness(tmp_path, Mutation, Expected):
    Evidence = LiteralEvidence()
    Evidence.update(Mutation)
    Source = SaveFailure(tmp_path, LiteralFailure(Evidence))
    WriteReport(tmp_path, Source)
    Html = (tmp_path / "RoutingFailureReport.html").read_text()
    assert f"<pre>{Expected}</pre>" in Html
    if Expected in ("Malformed", "Unsupported", "Unavailable"):
        assert "<table>" not in Html
    else:
        assert "ConflictRetentionLimit" in Html and "(4, 2, -1)" in Html


def test_cli_failure_and_write_failure_preserve_original_result(tmp_path, monkeypatch, capsys):
    Failure = RoutingFailure(Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete,
                             Stage="Candidate", Detail="original physical failure")
    def Compile(**Arguments):
        Root = Arguments["OutputPath"].parent
        Root.mkdir(parents=True, exist_ok=True)
        Source = Arguments["OutputPath"].with_suffix(".RoutingFailure.json")
        Source.write_text(json.dumps(LiteralFailure(LiteralEvidence())))
        raise RoutingStageError(Failure)
    monkeypatch.setattr("App.CompilerCli.CompileSvToLitematic", Compile)
    monkeypatch.setattr("App.CompilerCli.BuildRunId", lambda: "failed-run")
    OriginalReplace = os.replace
    def DenyHtml(Source, Target, **Arguments):
        if str(Target).endswith(".html"):
            raise PermissionError("injected report write failure")
        return OriginalReplace(Source, Target, **Arguments)
    monkeypatch.setattr(os, "replace", DenyHtml)
    Result = Main(["--input", "Assets/Examples/FullAdder.sv", "--output", str(tmp_path / "Design.litematic"),
                   "--defaults-file", str(tmp_path / "missing.json"), "--no-routing-telemetry"])
    assert Result == 1
    Root = tmp_path / "Runs" / "failed-run"
    assert json.loads((Root / "Design.RoutingFailure.json").read_text()) == LiteralFailure(LiteralEvidence())
    assert not list(Root.glob("*.html")) and not list(Root.glob(".RoutingFailureReport-*"))
    assert "FAILURE REPORT: unavailable (PermissionError)" in (Root / "Summary.txt").read_text()
    assert '"Status": "Unavailable"' in (Root / "RawDump.txt").read_text()
    Output = capsys.readouterr()
    assert "original physical failure" in Output.err
    assert "Candidate: ClusterInterfaceSolveIncomplete" in Output.out


def test_cli_automatically_publishes_from_persisted_failure(tmp_path, monkeypatch):
    def Compile(**Arguments):
        Root = Arguments["OutputPath"].parent
        Root.mkdir(parents=True, exist_ok=True)
        Arguments["OutputPath"].with_suffix(".RoutingFailure.json").write_text(json.dumps(LiteralFailure()))
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete, Stage="Candidate"))
    monkeypatch.setattr("App.CompilerCli.CompileSvToLitematic", Compile)
    monkeypatch.setattr("App.CompilerCli.BuildRunId", lambda: "failed-run")
    assert Main(["--input", "Assets/Examples/FullAdder.sv", "--output", str(tmp_path / "Design.litematic"),
                 "--defaults-file", str(tmp_path / "missing.json"), "--no-routing-telemetry"]) == 1
    assert len(list((tmp_path / "Runs" / "failed-run").glob("*.html"))) == 1


def test_success_does_not_publish_even_with_a_stale_failure_artifact(tmp_path):
    Source = SaveFailure(tmp_path, LiteralFailure())
    Report = WriteReport(tmp_path, Source, "SUCCESS")
    assert not list(tmp_path.glob("*.html"))
    assert "FAILURE REPORT:" not in Report.SummaryPath.read_text()


class DocumentTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.Tags = []
        self.Attributes = []
    def handle_starttag(self, Tag, Attributes):
        self.Tags.append(Tag)
        self.Attributes.extend(Attributes)


def test_diagnostic_text_is_inert_and_never_used_as_paths_or_urls(tmp_path):
    Evidence = LiteralEvidence()
    Evidence["Signal"] = '<script>fetch("https://example.invalid/secret")</script>'
    Payload = LiteralFailure(Evidence)
    Payload["Failure"]["Detail"] = '../../escape.html <img src="https://example.invalid/image" onerror="alert(1)">'
    Payload["Failure"]["RepairActions"] = ['<a href="javascript:alert(1)">go</a>']
    Source = SaveFailure(tmp_path, Payload)
    WriteReport(tmp_path, Source)
    Html = (tmp_path / "RoutingFailureReport.html").read_text()
    Parser = DocumentTags(); Parser.feed(Html)
    assert not set(Parser.Tags) & {"script", "img", "iframe", "a", "object", "link"}
    assert not any(Key in ("href", "src", "onerror") for Key, _ in Parser.Attributes)
    assert "&lt;script&gt;" in Html and "../../escape.html" in Html
    assert 'default-src &#x27;none&#x27;' in Html or "default-src 'none'" in Html
    assert len(list(tmp_path.glob("*.html"))) == 1


@pytest.mark.parametrize("Raw,State", [(b"{bad json", "Malformed"),
                                         (b'{"SchemaVersion":"future","Failure":{}}', "Unsupported")])
def test_bad_or_unsupported_artifact_still_has_a_metadata_report(tmp_path, Raw, State):
    Source = tmp_path / "Design.RoutingFailure.json"; Source.write_bytes(Raw)
    WriteReport(tmp_path, Source)
    Html = (tmp_path / "RoutingFailureReport.html").read_text()
    assert State in Html and sha256(Raw).hexdigest() in Html
    assert "not-run — routing failure prevented this phase" not in Html


def test_reader_and_display_caps_are_visible(tmp_path):
    Payload = LiteralFailure()
    Payload["Failure"]["Detail"] = "x" * 10000
    Payload["Failure"]["Diagnostics"] = {"Excess": list(range(9000)),
                                           "SelfClaimConflictEvidence": LiteralEvidence()}
    Source = SaveFailure(tmp_path, Payload)
    WriteReport(tmp_path, Source)
    Html = (tmp_path / "RoutingFailureReport.html").read_text()
    assert "Evidence discovery limited: True" in Html
    assert "Display truncated: True" in Html
    assert len(Html.encode()) < 256 * 1024
    assert "<table>" not in Html


def test_reader_rejects_changed_identity_and_external_symlink(tmp_path):
    Source = SaveFailure(tmp_path, LiteralFailure())
    Identity = {"Path": str(Source), "Bytes": Source.stat().st_size, "Sha256": "0" * 64}
    assert PublishRoutingFailureReport(RunDirectory=tmp_path, FailurePath=Source,
                                       SourceIdentity=Identity)["Status"] == "Unavailable"
    Link = tmp_path / "Link.RoutingFailure.json"; Link.symlink_to(Source)
    Identity["Path"] = str(Link)
    Identity["Sha256"] = sha256(Source.read_bytes()).hexdigest()
    assert PublishRoutingFailureReport(RunDirectory=tmp_path, FailurePath=Link,
                                       SourceIdentity=Identity)["Status"] == "Unavailable"
    assert not list(tmp_path.glob("*.html"))


@pytest.mark.parametrize("Stage", ["MchprsValidation", "FabricFinalCheck"])
def test_later_validation_failure_is_not_relabelled_as_unreached(tmp_path, Stage):
    Payload = LiteralFailure(); Payload["Failure"]["Stage"] = Stage
    WriteReport(tmp_path, SaveFailure(tmp_path, Payload))
    Html = (tmp_path / "RoutingFailureReport.html").read_text()
    assert "later validation failure" in Html
    assert "not-run — routing failure prevented this phase" not in Html


def test_oversize_source_fails_closed_even_after_same_size_replacement(tmp_path):
    Source = tmp_path / "Design.RoutingFailure.json"
    Original = b"x" * (4 * 1024 * 1024 + 1)
    Source.write_bytes(Original)
    Identity = {"Path": str(Source), "Bytes": len(Original), "Sha256": sha256(Original).hexdigest()}
    Source.write_bytes(b"y" * len(Original))
    Publication = PublishRoutingFailureReport(RunDirectory=tmp_path, FailurePath=Source,
                                              SourceIdentity=Identity)
    assert Publication["Status"] == "Unavailable"
    assert Publication["ErrorType"] == "ArtifactReadLimit"
    assert not list(tmp_path.glob("*.html"))
    Report = WriteReport(tmp_path, Source)
    assert "FAILURE REPORT: unavailable (ArtifactReadLimit)" in Report.SummaryPath.read_text()


def test_producer_shaped_nested_evidence_is_discovered(tmp_path):
    Payload = LiteralFailure()
    Payload["Failure"]["Diagnostics"] = {"CandidateDiagnostics": {
        "TypedNativeRouteP1Evidence": [{"MaterializationDiagnostics": {
            "SelfClaimConflictEvidence": LiteralEvidence()}}]}}
    WriteReport(tmp_path, SaveFailure(tmp_path, Payload))
    Html = (tmp_path / "RoutingFailureReport.html").read_text()
    assert "(4, 2, -1)" in Html and "Support, Air" in Html
    assert "CandidateDiagnostics/TypedNativeRouteP1Evidence/0/MaterializationDiagnostics/SelfClaimConflictEvidence" in Html


def test_publication_cancellation_propagates_and_cleans_temporary_file(tmp_path, monkeypatch):
    Source = SaveFailure(tmp_path, LiteralFailure())
    def Cancel(*Arguments, **Keywords):
        raise KeyboardInterrupt()
    monkeypatch.setattr(os, "replace", Cancel)
    with pytest.raises(KeyboardInterrupt):
        WriteReport(tmp_path, Source)
    assert not list(tmp_path.glob("*.html"))
    assert not list(tmp_path.glob(".RoutingFailureReport-*"))
    assert json.loads(Source.read_text()) == LiteralFailure()


def test_repeated_reporting_rejects_report_bound_to_previous_failure(tmp_path):
    Source = SaveFailure(tmp_path, LiteralFailure())
    WriteReport(tmp_path, Source)
    Html = tmp_path / "RoutingFailureReport.html"
    OriginalHtml = Html.read_bytes()
    Source = SaveFailure(tmp_path, LiteralFailure(LiteralEvidence()))
    Report = WriteReport(tmp_path, Source)
    assert Html.read_bytes() == OriginalHtml
    assert "FAILURE REPORT: rejected" in Report.SummaryPath.read_text()
    Raw = Report.RawReportPath.read_text()
    Inventory = json.loads(Raw.split("===== ARTIFACT INVENTORY =====\n", 1)[1])
    assert not any(Entry["Path"].endswith(".html") for Entry in Inventory)


def test_normal_output_parent_segments_match_canonical_inventory(tmp_path):
    (tmp_path / "intermediate").mkdir()
    Root = tmp_path / "run"
    Root.mkdir()
    Source = SaveFailure(Root, LiteralFailure(LiteralEvidence()))
    SpelledRoot = tmp_path / "intermediate" / ".." / "run"
    Report = WriteReport(SpelledRoot, SpelledRoot / Source.name)
    Html = Root / "RoutingFailureReport.html"
    assert Html.is_file()
    assert sha256(Source.read_bytes()).hexdigest() in Html.read_text()
    assert str(Html) in Report.SummaryPath.read_text()


def test_source_change_during_html_publication_is_rejected_in_ordinary_report(tmp_path, monkeypatch):
    Source = SaveFailure(tmp_path, LiteralFailure())
    Before = Source.read_bytes()
    Replace = os.replace
    def ReplaceAndChange(PathValue, Destination, **Arguments):
        Result = Replace(PathValue, Destination, **Arguments)
        if str(Destination).endswith("RoutingFailureReport.html"):
            Source.write_bytes(Before.replace(b"Candidate", b"Corrupted"))
        return Result
    monkeypatch.setattr(os, "replace", ReplaceAndChange)
    Report = WriteReport(tmp_path, Source)
    assert "FAILURE REPORT: rejected" in Report.SummaryPath.read_text()
    assert "Candidate: ClusterInterfaceSolveIncomplete" in Report.SummaryPath.read_text()
    assert "retained source" not in Source.read_text()


def test_directory_swap_during_publication_never_writes_outside_run(tmp_path, monkeypatch):
    Root = tmp_path / "run"; Root.mkdir()
    Source = SaveFailure(Root, LiteralFailure())
    OriginalSource = Source.read_bytes()
    Identity = {"Path": str(Source), "Bytes": len(OriginalSource), "Sha256": sha256(OriginalSource).hexdigest()}
    Outside = tmp_path / "outside"; Outside.mkdir()
    Sentinel = Outside / "RoutingFailureReport.html"; Sentinel.write_bytes(b"must not overwrite")
    Moved = tmp_path / "original-run"
    Open = os.open
    Swapped = False
    def SwapBeforeWrite(Name, Flags, *Arguments, **Keywords):
        nonlocal Swapped
        if not Swapped and Path(Name).name.startswith(".RoutingFailureReport-"):
            Swapped = True
            Root.rename(Moved)
            Root.symlink_to(Outside, target_is_directory=True)
        return Open(Name, Flags, *Arguments, **Keywords)
    monkeypatch.setattr(os, "open", SwapBeforeWrite)
    Result = PublishRoutingFailureReport(RunDirectory=Root, FailurePath=Source, SourceIdentity=Identity)
    assert Swapped
    assert Result["Status"] in ("Unavailable", "Rejected")
    assert Sentinel.read_bytes() == b"must not overwrite"
    assert set(PathValue.name for PathValue in Outside.iterdir()) == {"RoutingFailureReport.html"}
    assert (Moved / Source.name).read_bytes() == OriginalSource


def test_preexisting_pair_directory_swap_is_rejected_before_returning_published(tmp_path, monkeypatch):
    import App.RoutingFailureReport as Publisher
    Root = tmp_path / "run"; Root.mkdir()
    Source = SaveFailure(Root, LiteralFailure())
    WriteReport(Root, Source)
    SourceBytes = Source.read_bytes()
    Identity = {"Path": str(Source), "Bytes": len(SourceBytes), "Sha256": sha256(SourceBytes).hexdigest()}
    Outside = tmp_path / "outside"; Outside.mkdir()
    Moved = tmp_path / "original-run"
    Observe = Publisher.ObserveReportPair
    def ObserveThenSwap(*Arguments, **Keywords):
        Result = Observe(*Arguments, **Keywords)
        assert Result["Status"] == "Available"
        Root.rename(Moved); Root.symlink_to(Outside, target_is_directory=True)
        return Result
    monkeypatch.setattr(Publisher, "ObserveReportPair", ObserveThenSwap)
    Result = PublishRoutingFailureReport(RunDirectory=Root, FailurePath=Source, SourceIdentity=Identity)
    assert Result["Status"] in ("Unavailable", "Rejected")
    assert list(Outside.iterdir()) == []
    assert (Moved / Source.name).read_bytes() == SourceBytes

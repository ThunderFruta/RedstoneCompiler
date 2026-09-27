"""Literal receipt/content oracles for report integrity, not routing authority."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from App.RoutingFailureArtifacts import ValidateReportPair, ObserveReportPair, ProjectListedReport

HTML_NAME = "RoutingFailureReport.html"
RECEIPT_NAME = "RoutingFailureReport.receipt.json"
LITERAL_HTML = b'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>Routing failure report</title></head><body><h1>Observed rejection</h1><pre>Signal T; Support, Air at (4, 2, -1); ContributorProvenance: Unavailable. &lt;script&gt;fetch(&quot;https://example.invalid&quot;)&lt;/script&gt;</pre><p>Not a proven upstream root cause. MCHPRS: not-run. Fabric: not-run.</p></body></html>'''


def LiteralIdentity(Name, Data):
    return {"Name": Name, "SizeBytes": len(Data), "Sha256": sha256(Data).hexdigest()}


def LiteralReceipt(FailureName, FailureBytes, Html=LITERAL_HTML):
    return json.dumps({"SchemaVersion": "routing-failure-report-receipt-v1",
                       "Source": LiteralIdentity(FailureName, FailureBytes),
                       "Report": LiteralIdentity(HTML_NAME, Html)}, sort_keys=True).encode()


def WriteLiteralPair(Root, Name="Design.RoutingFailure.json", FailureBytes=None):
    Root.mkdir(parents=True, exist_ok=True)
    FailureBytes = FailureBytes or b'{"SchemaVersion":"routing-failure-v1","Failure":{"Stage":"Candidate","Reason":"SupportConflict","Diagnostics":{}}}'
    Source = Root / Name
    Source.write_bytes(FailureBytes)
    (Root / HTML_NAME).write_bytes(LITERAL_HTML)
    (Root / RECEIPT_NAME).write_bytes(LiteralReceipt(Name, FailureBytes))
    Records = {}
    for Key, PathValue in (("RoutingFailure", Source), ("RoutingFailureReport", Root / HTML_NAME),
                           ("RoutingFailureReportReceipt", Root / RECEIPT_NAME)):
        Data = PathValue.read_bytes()
        Records[Key] = {"Path": str(PathValue), "Exists": True, "SizeBytes": len(Data), "Sha256": sha256(Data).hexdigest()}
    return Source, Records


def test_literal_pair_binds_exact_source_and_inert_captured_text(tmp_path):
    Source, Records = WriteLiteralPair(tmp_path)
    Result = ObserveReportPair(Source, Source.read_bytes())
    assert Result["Status"] == "Available"
    assert Result["Source"] == LiteralIdentity(Source.name, Source.read_bytes())
    assert Result["Report"] == LiteralIdentity(HTML_NAME, LITERAL_HTML)
    assert b"Support, Air at (4, 2, -1)" in LITERAL_HTML
    assert b"ContributorProvenance: Unavailable" in LITERAL_HTML
    assert b"<script>" not in LITERAL_HTML


@pytest.mark.parametrize("Mutation,Expected", [("html", "Rejected"), ("source", "Rejected"),
                                                ("source-name", "Rejected"), ("duplicate-json", "Malformed"),
                                                ("malformed-receipt", "Malformed")])
def test_corruption_cannot_be_relabelled_as_valid(Mutation, Expected):
    Source = b"original failure bytes"
    Html = LITERAL_HTML
    Receipt = LiteralReceipt("X.RoutingFailure.json", Source)
    if Mutation == "html": Html += b"changed"
    if Mutation == "source": Source += b"changed"
    if Mutation == "source-name": Receipt = LiteralReceipt("../X.RoutingFailure.json", Source)
    if Mutation == "duplicate-json": Receipt = Receipt[:-1] + b',"Report":{}}'
    if Mutation == "malformed-receipt": Receipt = b"not json"
    Result = ValidateReportPair(FailureName="X.RoutingFailure.json", FailureData=Source,
                                ReportData=Html, ReceiptData=Receipt)
    assert Result["Status"] == Expected


@pytest.mark.parametrize("Markup", [b'<script>alert(1)</script>', b'<img src="https://example.invalid">',
                                     b'<a href="javascript:alert(1)">x</a>', b'<base href="https://example.invalid">',
                                     b'<p onclick="alert(1)">x</p>', b'<style>@import "https://example.invalid";</style>'])
def test_even_rehashed_executable_markup_is_malformed(Markup):
    Source = b"literal source"
    Html = LITERAL_HTML.replace(b"</body>", Markup + b"</body>")
    Result = ValidateReportPair(FailureName="X.RoutingFailure.json", FailureData=Source,
                                ReportData=Html, ReceiptData=LiteralReceipt("X.RoutingFailure.json", Source, Html))
    assert Result["Status"] == "Malformed"


def test_missing_historical_report_remains_unavailable(tmp_path):
    Source = tmp_path / "Old.RoutingFailure.json"
    Source.write_text('{"SchemaVersion":"routing-failure-v1","Failure":{"Stage":"Candidate","Reason":"Incomplete"}}')
    Before = Source.read_bytes()
    assert ObserveReportPair(Source, Before)["Status"] == "Unavailable"
    assert Source.read_bytes() == Before
    assert not (tmp_path / HTML_NAME).exists()


@pytest.mark.parametrize("Kind", ["report", "receipt", "parent"])
def test_observer_rejects_symlinks_without_reading_outside(tmp_path, Kind):
    Source, _ = WriteLiteralPair(tmp_path / "run")
    OriginalSource = Source.read_bytes()
    Outside = tmp_path / "outside"
    Outside.write_bytes(LITERAL_HTML if Kind == "report" else LiteralReceipt(Source.name, OriginalSource))
    if Kind == "parent":
        Link = tmp_path / "alias"; Link.symlink_to(Source.parent, target_is_directory=True)
        Source = Link / Source.name
    else:
        Target = Source.parent / (HTML_NAME if Kind == "report" else RECEIPT_NAME)
        Target.unlink(); Target.symlink_to(Outside)
    Result = ObserveReportPair(Source, OriginalSource)
    assert Result["Status"] == "Rejected"
    assert "outside sentinel" not in str(Result)


@pytest.mark.parametrize("Kind", ["unlisted", "duplicate", "duplicate-other-path", "outside", "altered-identity", "missing"])
def test_retained_listing_is_authority_for_report_evidence(tmp_path, Kind):
    Source, Records = WriteLiteralPair(tmp_path)
    Captured = {str(PathValue): PathValue.read_bytes() for PathValue in tmp_path.iterdir()}
    Original = Source.read_bytes()
    if Kind == "unlisted": del Records["RoutingFailureReport"]
    if Kind == "duplicate": Records["SecondReport"] = dict(Records["RoutingFailureReport"])
    if Kind == "duplicate-other-path":
        Records["OtherReport"] = {"Path": str(tmp_path / "other" / HTML_NAME), "Exists": False}
    if Kind == "outside":
        Outside = tmp_path.parent / (tmp_path.name + "-outside")
        Outside.mkdir()
        OutsideReport = Outside / HTML_NAME
        OutsideReport.write_bytes(LITERAL_HTML)
        Captured[str(OutsideReport)] = LITERAL_HTML
        Records["RoutingFailureReport"]["Path"] = str(OutsideReport)
    if Kind == "altered-identity": Records["RoutingFailureReport"]["Sha256"] = "0" * 64
    if Kind == "missing": del Captured[str(tmp_path / HTML_NAME)]
    Result = ProjectListedReport(Records["RoutingFailure"], Records, Captured.get)
    assert Result["Status"] == "Rejected"
    assert Source.read_bytes() == Original


def test_observer_does_not_accept_oversized_report(tmp_path):
    Source, _ = WriteLiteralPair(tmp_path)
    (tmp_path / HTML_NAME).write_bytes(b"x" * (256 * 1024 + 1))
    assert ObserveReportPair(Source, Source.read_bytes())["Status"] == "Rejected"

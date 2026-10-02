"""Sealed diagnostic observations preserve recorded facts without claim authority."""

from copy import deepcopy
from hashlib import sha256
import base64
import json
from pathlib import Path
import zlib

import pytest

from Tests.Structural.test_routing_design_snapshot import (
    BuildLiteralProfileAuthority,
    BuildSyntheticRunReceipt,
    CompleteFailureRoutingIdentity,
    WriteSealedAcceptanceFixture,
)
from Tests.Tools.test_router_acceptance_harness import (
    BuildTestAcceptanceCommand,
    WriteNestedRoutingFailureArtifact,
)
from Tools.Routing import CaptureRoutingDesignSnapshot as Snapshot
from Tools.Routing.RunRouterAcceptance import (
    AcceptanceCase,
    BuildPolicyProvenanceRecord,
    BuildRunArtifacts,
)


# This literal is the exact producer record from the sealed historical TFlip
# source, SHA-256 5b0d99ac89065dc95ec252c1b80391732afff96a35b04dd6810c460827d3aa62.
# The portable outer invocation below is a protocol fixture, not that full run.
HistoricalRawEnvelope = {
    "SchemaVersion": "raw-track-assignment-failure-envelope-v1",
    "Counts": {
        "AttemptCount": 1,
        "CandidatePreparationResultCount": 1,
        "MaterializedTemplateCount": 1,
        "PortfolioTemplateCount": 6,
        "SkippedDominatedTemplateCount": 0,
        "UnattemptedTemplateCount": 5,
    },
    "EvidenceCompleteness": {
        "FullInputManifestIncluded": False,
        "FullMaterializationDiagnosticsIncluded": False,
        "OuterPortfolioComplete": False,
        "SemanticSelectionComplete": False,
        "UnattemptedDescriptorsDominated": False,
    },
    "Omissions": [
        "full-frozen-candidate-input-manifests",
        "full-materialization-diagnostics",
        "raw-domain-values-and-claims",
    ],
    "SemanticResult": {
        "Complete": False,
        "FirstConflictResourceIndices": [],
        "FirstConflictSignals": [],
        "IncompleteReason": "incomplete-template-domain",
        "ProblemFingerprint": "4baa8694a9227cf1",
        "SelectedObjective": [],
        "SelectedTemplateId": "",
        "SelectionFingerprint": "",
        "Success": False,
        "Unsatisfiable": False,
    },
    "SourceIdentities": [{
        "CandidateId": "Placement-b96acf0dd427:layers-2",
        "CandidateInputFingerprint": "722f017f8f258558",
    }],
    "WorkIdentity": {
        "ExpansionCount": 0,
        "WorkControlsFingerprint": "47197b00e1174597",
    },
}


def BuildSealedDiagnosticFixture(
    Root, Diagnostics, *, Circuit="TFlipFlopLatch",
    FailureReason="ClusterInterfaceSolveIncomplete",
):
    """Bind supplied diagnostics to an official per-run case and invocation."""
    ProfileCase = next(
        Case for Case in BuildLiteralProfileAuthority(
            "expanded-seven", BaselineMode=None
        )["CaseTable"] if Case["Name"] == Circuit
    )
    Case = AcceptanceCase(
        Name=Circuit,
        ExamplePath=Path(ProfileCase["ExamplePath"]),
        TopModule=ProfileCase["TopModule"],
        RequiredRuns=ProfileCase["RequiredRuns"],
        TruthTableRows=ProfileCase["MchprsTruthTableRows"],
        RuntimeCeilingSeconds=ProfileCase["RuntimeCeilingSeconds"],
        ValidationVectorCount=ProfileCase["FabricCanaryCount"],
    )
    Artifacts = BuildRunArtifacts(Root / f"{Circuit}Run1", f"{Circuit}Run1")
    Command = BuildTestAcceptanceCommand(Case, Artifacts)
    RepositoryRoot = Path(__file__).resolve().parents[2]
    Command[:2] = [str(RepositoryRoot / ".venv/bin/python"), str(RepositoryRoot / "Main.py")]
    SourceState = {"Revision": "producer-protocol-fixture", "Dirty": False}
    FailurePath = WriteNestedRoutingFailureArtifact(
        Case, Artifacts, RunDirectoryName="selected",
        SourceRevision=SourceState["Revision"], ExpectedCommand=Command,
        Failure={
            "Stage": "PreRouteInterfaceSelection",
            "Reason": FailureReason,
            "Detail": "recorded incomplete fixed interface domain",
            "Diagnostics": deepcopy(Diagnostics),
        },
    )
    Payload = json.loads(FailurePath.read_bytes())
    Payload["SourceState"] = SourceState
    Payload["Reproduction"]["TopModule"] = ProfileCase["TopModule"]
    InputPath = Path(Command[Command.index("--input") + 1])
    InputBytes = InputPath.read_bytes()
    Payload["Reproduction"].update({
        "Input": {
            "Exists": True, "Path": str(InputPath),
            "Sha256": sha256(InputBytes).hexdigest(), "SizeBytes": len(InputBytes),
        },
        "DiagramPath": str(FailurePath.with_name(Artifacts["Diagram"].name)),
        "Workdir": Command[Command.index("--workdir") + 1],
    })
    FailurePath.write_text(json.dumps(Payload), encoding="utf-8")
    Policy = BuildPolicyProvenanceRecord("default")
    Run = BuildSyntheticRunReceipt(
        Policy, "default", RunName=f"{Circuit}Run1", Status="FAILED",
        Accepted=False, RoutingDeadlineSeconds=Case.RoutingDeadlineSeconds,
        FailureRoutingIdentity=CompleteFailureRoutingIdentity(
            Policy, "default", Case.RoutingDeadlineSeconds
        ),
    )
    Run["Circuit"] = Circuit
    Run["Command"] = Command
    Run["Evaluation"].update({
        "Accepted": False,
        "Failures": ["routing failure artifact exists"],
        "Artifacts": {"RoutingFailure": {
            "Exists": True, "Path": str(FailurePath),
            "SizeBytes": len(FailurePath.read_bytes()),
            "Sha256": sha256(FailurePath.read_bytes()).hexdigest(),
        }},
    })
    Run["Evaluation"]["Observed"]["FailureArtifactResolution"] = {
        "Status": "nested", "Path": str(FailurePath),
    }
    Manifest = {
        "Accepted": False, "Status": "FAILED", "Runs": [Run],
        "SourceState": SourceState, "MatrixMode": "expanded", "BaselineMode": None,
    }
    ManifestPath = Root / "AcceptanceManifest.json"
    WriteSealedAcceptanceFixture(ManifestPath, Manifest, (FailurePath,))
    return ManifestPath, FailurePath, Run, ProfileCase, SourceState, Policy


def ReadFixture(Fixture):
    ManifestPath, _, Run, ProfileCase, SourceState, Policy = Fixture
    Archive = Snapshot.BuildSealedArchiveEvidence(ManifestPath)
    # Strip optional producer labels: the receipt protocol uses these exact axes.
    PolicyIdentity = {Key: Policy[Key] for Key in (
        "PolicyVersion", "Seed", "Sha256", "Snapshot"
    )}
    return Snapshot.BuildAcceptanceRunSummary(
        Run, RequestedStrategy="default", ResolvedUsedStrategy="default",
        PolicyRecord=Policy, PolicyIdentity=PolicyIdentity,
        ProfileCase=ProfileCase, ManifestSourceState=SourceState,
        ArchiveEvidence=Archive,
    )


def AssertOriginalFailure(Summary, Reason="ClusterInterfaceSolveIncomplete"):
    assert Summary["Status"] == "FAILED"
    assert Summary["Accepted"] is False
    assert Summary["RoutingIdentityConsistent"] is True
    assert Summary["ActualRoutingIdentity"] is None
    assert Summary["EvaluatorFailures"] == ["routing failure artifact exists"]
    assert Summary["FailureArtifact"]["Stage"] == "PreRouteInterfaceSelection"
    assert Summary["FailureArtifact"]["Reason"] == Reason
    assert Summary["BackendState"] == {
        "State": "not-run", "ProfileChecked": False,
        "UpstreamStage": "PreRouteInterfaceSelection",
        "UpstreamReason": Reason,
    }


def test_historical_raw_record_preserves_exact_partial_observation(tmp_path):
    Summary = ReadFixture(BuildSealedDiagnosticFixture(tmp_path, {
        "RawTrackAssignmentFailureEnvelope": HistoricalRawEnvelope,
    }))
    AssertOriginalFailure(Summary)
    Observation = Summary["FailureArtifact"]["RawTrackAssignmentFailureEnvelope"]
    assert Observation["Status"] == "Available"
    assert Observation["EvidenceCoverage"] == "Partial"
    assert Observation["Record"] == HistoricalRawEnvelope
    assert Summary["FailureArtifact"]["NativePreparationEvidence"]["Status"] == "Unavailable"


@pytest.mark.parametrize("Absence", ["null", "missing", "unknown-schema"])
def test_absent_or_unsupported_raw_record_cannot_acquire_historical_facts(tmp_path, Absence):
    # The actual FullAdder source 188559b612bbb24cb19d9997dd9e74300055d284abdab2eef539d0b547814b84
    # has a null raw envelope and no native companion. Missing/unknown are protocol cases.
    Diagnostics = {}
    if Absence == "null":
        Diagnostics["RawTrackAssignmentFailureEnvelope"] = None
    elif Absence == "unknown-schema":
        Record = deepcopy(HistoricalRawEnvelope)
        Record["SchemaVersion"] = "unsupported-raw-envelope"
        Diagnostics["RawTrackAssignmentFailureEnvelope"] = Record
    Summary = ReadFixture(BuildSealedDiagnosticFixture(
        tmp_path, Diagnostics, Circuit="FullAdder"
    ))
    AssertOriginalFailure(Summary)
    for Name in ("RawTrackAssignmentFailureEnvelope", "NativePreparationEvidence"):
        assert Summary["FailureArtifact"][Name]["Status"] == "Unavailable"
        assert Summary["FailureArtifact"][Name]["Record"] is None


@pytest.mark.parametrize(("Section", "Field", "Invalid"), [
    ("Counts", "AttemptCount", True),
    ("Counts", "PortfolioTemplateCount", -1),
    ("Counts", "UnattemptedTemplateCount", 5.0),
    ("EvidenceCompleteness", "FullInputManifestIncluded", 0),
    ("EvidenceCompleteness", "OuterPortfolioComplete", "false"),
    ("SemanticResult", "Complete", 0),
    ("SemanticResult", "Success", 0),
    ("SemanticResult", "Unsatisfiable", 0),
    ("WorkIdentity", "ExpansionCount", False),
])
def test_supported_raw_record_rejects_wrong_literal_types_without_changing_failure(
    tmp_path, Section, Field, Invalid
):
    Record = deepcopy(HistoricalRawEnvelope)
    Record[Section][Field] = Invalid
    Summary = ReadFixture(BuildSealedDiagnosticFixture(tmp_path, {
        "RawTrackAssignmentFailureEnvelope": Record,
    }))
    AssertOriginalFailure(Summary)
    Observation = Summary["FailureArtifact"]["RawTrackAssignmentFailureEnvelope"]
    assert Observation["Status"] == "Malformed"


def test_unknown_native_companion_keeps_valid_raw_observation(tmp_path):
    Summary = ReadFixture(BuildSealedDiagnosticFixture(tmp_path, {
        "RawTrackAssignmentFailureEnvelope": HistoricalRawEnvelope,
        "NativePreparationEvidence": {
            "SchemaVersion": "unsupported-native-observation",
            "ObservationState": "Observed",
            "ReceiptIdentity": "unverified-arbitrary-field",
        },
    }))
    AssertOriginalFailure(Summary)
    Failure = Summary["FailureArtifact"]
    assert Failure["RawTrackAssignmentFailureEnvelope"]["Status"] == "Available"
    assert Failure["RawTrackAssignmentFailureEnvelope"]["Record"] == HistoricalRawEnvelope
    assert Failure["NativePreparationEvidence"]["Status"] == "Unavailable"
    assert Failure["NativePreparationEvidence"]["Record"] is None


@pytest.mark.parametrize("State", ["NoNativeSubmissionObserved", "Unavailable"])
def test_explicit_no_submission_differs_from_missing_transport(tmp_path, State):
    # Declared top-level protocol cases; no manufactured native v3 batch/receipt.
    Reason = "batch publication unavailable at recorded boundary" if State == "Unavailable" else None
    Record = {
        "SchemaVersion": "native-preparation-evidence-v1",
        "ObservationState": State,
        "UnavailableReason": Reason,
        "CandidateObservations": [],
        "Omissions": [
            "raw-native-canonical-input-bytes",
            "full-raw-domain-values-and-claims",
            "full-frozen-candidate-input-manifests",
        ],
    }
    Summary = ReadFixture(BuildSealedDiagnosticFixture(tmp_path, {
        "NativePreparationEvidence": Record,
    }))
    AssertOriginalFailure(Summary)
    Observation = Summary["FailureArtifact"]["NativePreparationEvidence"]
    if State == "Unavailable":
        assert Observation["Status"] == "Unavailable"
        assert Observation["Reason"] == Reason
    else:
        assert Observation["Status"] == "Available"
        assert Observation["EvidenceCoverage"] == "Partial"
        assert Observation["Record"] == Record


def test_supported_raw_schema_with_missing_required_shape_is_malformed(tmp_path):
    Summary = ReadFixture(BuildSealedDiagnosticFixture(tmp_path, {
        "RawTrackAssignmentFailureEnvelope": {
            "SchemaVersion": "raw-track-assignment-failure-envelope-v1",
        },
    }))
    AssertOriginalFailure(Summary)
    Observation = Summary["FailureArtifact"]["RawTrackAssignmentFailureEnvelope"]
    assert Observation["Status"] == "Malformed"


def test_seal_tamper_rejects_diagnostic_before_authoritative_projection(tmp_path):
    Fixture = BuildSealedDiagnosticFixture(tmp_path, {
        "RawTrackAssignmentFailureEnvelope": HistoricalRawEnvelope,
    })
    FailurePath = Fixture[1]
    FailurePath.write_bytes(FailurePath.read_bytes() + b" ")
    with pytest.raises(ValueError):
        ReadFixture(Fixture)


@pytest.mark.parametrize("Mismatch", ["source", "invocation", "selection", "digest"])
def test_selected_raw_observation_requires_exact_outer_identity(tmp_path, Mismatch):
    Fixture = BuildSealedDiagnosticFixture(tmp_path, {
        "RawTrackAssignmentFailureEnvelope": HistoricalRawEnvelope,
    })
    ManifestPath, FailurePath, Run, _, _, _ = Fixture
    if Mismatch in ("source", "invocation"):
        Payload = json.loads(FailurePath.read_bytes())
        if Mismatch == "source":
            Payload["SourceState"]["Revision"] = "different-producer"
        else:
            Payload["Reproduction"]["Command"] += ["--unrecorded-flag"]
        FailurePath.write_text(json.dumps(Payload), encoding="utf-8")
        Run["Evaluation"]["Artifacts"]["RoutingFailure"].update({
            "SizeBytes": len(FailurePath.read_bytes()),
            "Sha256": sha256(FailurePath.read_bytes()).hexdigest(),
        })
    elif Mismatch == "selection":
        Run["Evaluation"]["Observed"]["FailureArtifactResolution"]["Path"] = str(
            FailurePath.with_name("unselected.RoutingFailure.json")
        )
    else:
        Run["Evaluation"]["Artifacts"]["RoutingFailure"]["Sha256"] = "0" * 64
    Manifest = json.loads(ManifestPath.read_bytes())
    Manifest["Runs"] = [Run]
    WriteSealedAcceptanceFixture(ManifestPath, Manifest, (FailurePath,))
    with pytest.raises(ValueError):
        ReadFixture(Fixture)


def LoadNativeProtocolSpecimen():
    Data = zlib.decompress(base64.b85decode(NativeProtocolSpecimenCompressed))
    assert sha256(Data).hexdigest() == NativeProtocolSpecimenSha256
    return json.loads(Data)


def ReadNativeObservation(Root, Record):
    Summary = ReadFixture(BuildSealedDiagnosticFixture(
        Root, {"NativePreparationEvidence": Record},
        FailureReason="RuntimeBudgetExceeded",
    ))
    AssertOriginalFailure(Summary, "RuntimeBudgetExceeded")
    return Summary["FailureArtifact"]["NativePreparationEvidence"]


def test_native_literal_preserves_reason_axes_identity_and_partial_admissions(tmp_path):
    Record = LoadNativeProtocolSpecimen()
    Observation = ReadNativeObservation(tmp_path, Record)
    assert Observation["Status"] == "Available"
    assert Observation["EvidenceCoverage"] == "Partial"
    assert Observation["Record"] == Record
    Candidate = Observation["Record"]["CandidateObservations"][0]
    assert Candidate["CandidateId"] == "Placement-b96acf0dd427:layers-2"
    assert Candidate["CandidateInputFingerprint"] == "722f017f8f258558"
    assert Candidate["Coverage"] == {
        "NativeBatchCount": 1, "NativeOriginCount": 4, "CanonicalOutcomeCount": 3,
        "PreparationObservationComplete": False,
        "SemanticDomainComplete": None, "OuterPortfolioComplete": None,
    }
    Batch = Candidate["Batches"][0]
    assert [Origin["OriginalOrdinal"] for Origin in Batch["Origins"]] == [0, 1, 2, 3]
    assert [Outcome["NativeOrdinal"] for Outcome in Batch["NativeOutcomes"]] == [0, 1, 2]
    assert len(Batch["Admissions"]) == 2 < len(Batch["Origins"])
    assert all(Admission["Admitted"] is None for Admission in Batch["Admissions"])
    assert all(Admission["PhysicalEvidence"] is None for Admission in Batch["Admissions"])
    assert all(Outcome["CommitEligibility"] == "Ineligible" for Outcome in Batch["NativeOutcomes"])
    Found = Batch["NativeOutcomes"][0]
    assert (Found["Reason"], Found["SearchOutcome"], Found["ClaimStrength"]) == (
        "Found", "Prepared", "Candidate"
    )
    Incomplete = Batch["NativeOutcomes"][2]
    assert (Incomplete["Reason"], Incomplete["SearchOutcome"], Incomplete["ClaimStrength"],
            Incomplete["OutcomePhase"]) == (
        "DetailedSearchIncomplete", "Unresolved", "Continuation", "Proof"
    )
    assert Incomplete["Started"] is True and Incomplete["Settled"] is True
    assert (Incomplete["ExpansionCap"], Incomplete["RouteExpansionCount"],
            Incomplete["ProofExpansionCount"], Incomplete["ActualExpansionCount"]) == (25000, 16, 103, 119)
    assert Incomplete["Identity"]["NativePayloadIdentity"] == (
        "57e5567714e57b2705349cac22f8d4b213f20c6927eeb7e88fcf136a12fb7b32"
    )
    assert Incomplete["Availability"]["Receipt"] == "Verified"
    Equivalent = Batch["Origins"][3]
    Canonical = Batch["Origins"][2]
    assert Equivalent["NativeOrdinal"] is None
    assert Equivalent["CanonicalOriginalOrdinal"] == Canonical["OriginalOrdinal"] == 2
    assert Equivalent["CanonicalOriginIdentity"] == Canonical["OriginIdentity"]
    assert Equivalent["CanonicalReceiptIdentity"] == Incomplete["Identity"]["ReceiptIdentity"]


def test_repeated_request_identity_preserves_distinct_native_ordinals(tmp_path):
    # Protocol-valid association mutation, not an assertion of actual execution.
    Record = LoadNativeProtocolSpecimen()
    Batch = Record["CandidateObservations"][0]["Batches"][0]
    First, Second = Batch["NativeOutcomes"][:2]
    Second["RequestIdentity"] = First["RequestIdentity"]
    for Origin in Batch["Origins"]:
        if Origin["CanonicalOriginalOrdinal"] == 1:
            Origin["CanonicalRequestId"] = First["RequestIdentity"]
    Observation = ReadNativeObservation(tmp_path, Record)
    assert Observation["Status"] == "Available"
    assert Observation["Record"] == Record
    Outcomes = Observation["Record"]["CandidateObservations"][0]["Batches"][0]["NativeOutcomes"]
    assert len(Outcomes) == 3
    assert Outcomes[0]["RequestIdentity"] == Outcomes[1]["RequestIdentity"]
    assert [Outcome["NativeOrdinal"] for Outcome in Outcomes] == [0, 1, 2]
    assert Outcomes[0]["Identity"]["ImmutableInputIdentity"] != Outcomes[1]["Identity"]["ImmutableInputIdentity"]


@pytest.mark.parametrize("Count", ["NativeBatchCount", "NativeOriginCount", "CanonicalOutcomeCount"])
def test_native_coverage_count_contradiction_is_diagnostic_only(tmp_path, Count):
    Record = LoadNativeProtocolSpecimen()
    Record["CandidateObservations"][0]["Coverage"][Count] += 1
    assert ReadNativeObservation(tmp_path, Record)["Status"] == "Malformed"


@pytest.mark.parametrize("Damage", [
    "boolean-count", "numeric-completeness", "null-scope", "empty-input-fingerprint",
    "boolean-native-ordinal", "numeric-started", "boolean-expansion", "null-reason",
    "unhashable-state", "unhashable-phase", "unhashable-ordinal", "unhashable-scope",
])
def test_native_supported_wrong_types_do_not_escape_or_change_original_failure(tmp_path, Damage):
    Record = LoadNativeProtocolSpecimen()
    Candidate = Record["CandidateObservations"][0]
    Batch = Candidate["Batches"][0]
    Outcome = Batch["NativeOutcomes"][2]
    if Damage == "boolean-count":
        Candidate["Coverage"]["NativeOriginCount"] = True
    elif Damage == "numeric-completeness":
        Candidate["Coverage"]["PreparationObservationComplete"] = 0
    elif Damage == "null-scope":
        Candidate["Scope"]["ResourceGraphFingerprint"] = None
    elif Damage == "empty-input-fingerprint":
        Candidate["CandidateInputFingerprint"] = ""
    elif Damage == "boolean-native-ordinal":
        Outcome["NativeOrdinal"] = True
    elif Damage == "numeric-started":
        Outcome["Started"] = 1
    elif Damage == "boolean-expansion":
        Outcome["ActualExpansionCount"] = True
    elif Damage == "null-reason":
        Outcome["Reason"] = None
    elif Damage == "unhashable-state":
        Record["ObservationState"] = {}
    elif Damage == "unhashable-phase":
        Outcome["OutcomePhase"] = []
    elif Damage == "unhashable-ordinal":
        Batch["Origins"][2]["CanonicalOriginalOrdinal"] = []
    else:
        Candidate["Scope"]["ResourceGraphFingerprint"] = []
    assert ReadNativeObservation(tmp_path, Record)["Status"] == "Malformed"


@pytest.mark.parametrize("Damage", [
    "missing-native-outcome", "duplicate-native-ordinal", "origin-native-ordinal",
    "missing-canonical-origin", "wrong-canonical-identity", "wrong-canonical-request",
])
def test_native_ordinal_association_is_required_for_available_observation(tmp_path, Damage):
    Record = LoadNativeProtocolSpecimen()
    Candidate = Record["CandidateObservations"][0]
    Batch = Candidate["Batches"][0]
    if Damage == "missing-native-outcome":
        Batch["NativeOutcomes"].pop()
        Candidate["Coverage"]["CanonicalOutcomeCount"] = 2
    elif Damage == "duplicate-native-ordinal":
        Batch["NativeOutcomes"][2]["NativeOrdinal"] = 0
    elif Damage == "origin-native-ordinal":
        Batch["Origins"][2]["NativeOrdinal"] = 1
    elif Damage == "missing-canonical-origin":
        Batch["Origins"][3]["CanonicalOriginalOrdinal"] = 999
    elif Damage == "wrong-canonical-identity":
        Batch["Origins"][3]["CanonicalOriginIdentity"] = Batch["Origins"][0]["OriginIdentity"]
    else:
        Batch["Origins"][3]["CanonicalRequestId"] = Batch["NativeOutcomes"][0]["RequestIdentity"]
    assert ReadNativeObservation(tmp_path, Record)["Status"] == "Malformed"


def BuildDeclaredPreNativeRecord():
    """Declared pre-raw schema case with real bounded geometry and no receipt."""
    Record = LoadNativeProtocolSpecimen()
    Candidate = Record["CandidateObservations"][0]
    Candidate["CandidateInputFingerprint"] = None
    Candidate["Scope"] = {Key: "" for Key in Candidate["Scope"]}
    Candidate["Coverage"].update(NativeOriginCount=1, CanonicalOutcomeCount=0)
    Batch = Candidate["Batches"][0]
    Reason = "recorded-pre-native-domain-incomplete"
    Batch["PreNativeReason"] = Reason
    Batch["ExecutionScope"] = None
    Batch["ContextValidation"] = None
    Batch["NativeOutcomes"] = []
    Origin = Batch["Origins"][2]
    Batch["Origins"] = [Origin]
    Origin["OriginalOrdinal"] = 0
    Origin.update({
        "NativeOrdinal": None, "CanonicalRequestId": None,
        "CanonicalReceiptIdentity": None, "ExecutionScopeIdentity": None,
        "NativePayloadIdentity": None, "RouteDomainIdentity": None,
        "DeadlineAtMonotonicSeconds": None,
        "CanonicalOriginalOrdinal": Origin["OriginalOrdinal"],
        "CanonicalOriginIdentity": Origin["OriginIdentity"],
        "NativeKind": "PreNativeIncomplete", "PreNativeReason": Reason,
        "ActualExpansionCount": 0, "CancellationRequestedBeforeStart": False,
        "CanonicalExecuted": False, "EquivalentReused": False,
    })
    Counters = Batch["Counters"]
    for Key in Counters:
        if Key != "SchemaVersion":
            Counters[Key] = 0
    Counters.update(Configured=1, Materialized=1, PreNativeIncomplete=1)
    Admission = Batch["Admissions"][0]
    Admission["NativeKind"] = "PreNativeIncomplete"
    Batch["Admissions"] = [Admission]
    return Record


def test_pre_native_record_preserves_reason_without_fabricating_receipt_or_input_attestation(tmp_path):
    Record = BuildDeclaredPreNativeRecord()
    Observation = ReadNativeObservation(tmp_path, Record)
    assert Observation["Status"] == "Available"
    assert Observation["EvidenceCoverage"] == "Partial"
    assert Observation["Record"] == Record
    Candidate = Observation["Record"]["CandidateObservations"][0]
    assert Candidate["CandidateInputFingerprint"] is None
    Batch = Candidate["Batches"][0]
    assert Batch["PreNativeReason"] == "recorded-pre-native-domain-incomplete"
    assert Batch["NativeOutcomes"] == []
    assert Batch["ExecutionScope"] is None and Batch["ContextValidation"] is None
    assert Batch["Origins"][0]["CanonicalReceiptIdentity"] is None
    assert Batch["Admissions"][0]["Admitted"] is None
    assert Batch["Counters"]["CanonicalExecuted"] == 0


@pytest.mark.parametrize("Damage", ["missing-reason", "invented-receipt", "invented-native-ordinal"])
def test_pre_native_receipt_or_missing_reason_is_malformed_diagnostic(tmp_path, Damage):
    Record = BuildDeclaredPreNativeRecord()
    Batch = Record["CandidateObservations"][0]["Batches"][0]
    if Damage == "missing-reason":
        Batch["PreNativeReason"] = None
    elif Damage == "invented-receipt":
        Batch["Origins"][0]["CanonicalReceiptIdentity"] = "invented-receipt"
    else:
        Batch["Origins"][0]["NativeOrdinal"] = 0
    assert ReadNativeObservation(tmp_path, Record)["Status"] == "Malformed"


def test_no_submission_state_cannot_certify_a_recorded_native_batch(tmp_path):
    Record = LoadNativeProtocolSpecimen()
    Record["ObservationState"] = "NoNativeSubmissionObserved"
    assert ReadNativeObservation(tmp_path, Record)["Status"] == "Malformed"


@pytest.mark.parametrize(("Field", "Value"), [
    ("Kind", "not-a-published-native-kind"),
    ("SearchOutcome", "not-a-published-runtime-outcome"),
    ("ClaimStrength", "not-a-published-runtime-claim"),
    ("CommitEligibility", "Eligible"),
])
def test_unknown_fixed_outcome_axis_cannot_be_authoritative_observation(tmp_path, Field, Value):
    Record = LoadNativeProtocolSpecimen()
    Record["CandidateObservations"][0]["Batches"][0]["NativeOutcomes"][2][Field] = Value
    assert ReadNativeObservation(tmp_path, Record)["Status"] == "Malformed"


def test_unknown_observation_state_is_malformed_inside_supported_companion(tmp_path):
    Record = LoadNativeProtocolSpecimen()
    Record["ObservationState"] = "not-a-declared-observation-state"
    assert ReadNativeObservation(tmp_path, Record)["Status"] == "Malformed"


@pytest.mark.parametrize("Field", ["Reason", "OutcomePhase"])
def test_recorded_native_reason_and_phase_strings_are_preserved_without_kind_inference(tmp_path, Field):
    # Protocol string extension case; it does not claim this producer emitted it.
    Record = LoadNativeProtocolSpecimen()
    Record["CandidateObservations"][0]["Batches"][0]["NativeOutcomes"][2][Field] = "producer-recorded-string"
    Observation = ReadNativeObservation(tmp_path, Record)
    assert Observation["Status"] == "Available"
    assert Observation["EvidenceCoverage"] == "Partial"
    assert Observation["Record"] == Record


def test_unknown_nested_batch_version_is_malformed_but_valid_raw_is_preserved(tmp_path):
    Record = LoadNativeProtocolSpecimen()
    Record["CandidateObservations"][0]["Batches"][0]["SchemaVersion"] = "unsupported-nested-batch-version"
    Summary = ReadFixture(BuildSealedDiagnosticFixture(tmp_path, {
        "NativePreparationEvidence": Record,
        "RawTrackAssignmentFailureEnvelope": HistoricalRawEnvelope,
    }, FailureReason="RuntimeBudgetExceeded"))
    AssertOriginalFailure(Summary, "RuntimeBudgetExceeded")
    Failure = Summary["FailureArtifact"]
    assert Failure["NativePreparationEvidence"]["Status"] == "Malformed"
    assert Failure["RawTrackAssignmentFailureEnvelope"]["Status"] == "Available"
    assert Failure["RawTrackAssignmentFailureEnvelope"]["Record"] == HistoricalRawEnvelope


def test_unknown_top_companion_version_does_not_interpret_nested_invalid_fields(tmp_path):
    Record = LoadNativeProtocolSpecimen()
    Record["SchemaVersion"] = "unsupported-top-companion-version"
    Record["ObservationState"] = []
    Record["CandidateObservations"][0]["Batches"][0]["SchemaVersion"] = {}
    Observation = ReadNativeObservation(tmp_path, Record)
    assert Observation["Status"] == "Unavailable"
    assert Observation["Record"] is None


def test_malformed_raw_diagnostic_does_not_suppress_supported_native_observation(tmp_path):
    Record = LoadNativeProtocolSpecimen()
    Summary = ReadFixture(BuildSealedDiagnosticFixture(tmp_path, {
        "NativePreparationEvidence": Record,
        "RawTrackAssignmentFailureEnvelope": {
            "SchemaVersion": "raw-track-assignment-failure-envelope-v1",
        },
    }, FailureReason="RuntimeBudgetExceeded"))
    AssertOriginalFailure(Summary, "RuntimeBudgetExceeded")
    Failure = Summary["FailureArtifact"]
    assert Failure["RawTrackAssignmentFailureEnvelope"]["Status"] == "Malformed"
    assert Failure["NativePreparationEvidence"]["Status"] == "Available"
    assert Failure["NativePreparationEvidence"]["Record"] == Record


@pytest.mark.parametrize("Damage", [
    "configured-total", "execution-equivalence-total", "terminal-kind-total",
    "canonical-outcome-count",
])
def test_inner_native_counters_must_reconcile_independently_of_outer_coverage(tmp_path, Damage):
    Record = LoadNativeProtocolSpecimen()
    Counters = Record["CandidateObservations"][0]["Batches"][0]["Counters"]
    if Damage == "configured-total":
        Counters["Configured"] += 1
    elif Damage == "execution-equivalence-total":
        Counters["EquivalentReused"] += 1
    elif Damage == "terminal-kind-total":
        Counters["Routed"] += 1
    else:
        # Preserve the total equation while contradicting three actual outcomes.
        Counters["CanonicalExecuted"] += 1
        Counters["EquivalentReused"] -= 1
    assert ReadNativeObservation(tmp_path, Record)["Status"] == "Malformed"


def test_consistent_recorded_identity_strings_are_opaque_partial_observations(tmp_path):
    # Declared observation-only string substitution, never a minted native receipt.
    Record = LoadNativeProtocolSpecimen()
    Batch = Record["CandidateObservations"][0]["Batches"][0]
    Identity = Batch["NativeOutcomes"][2]["Identity"]
    OriginalReceipt = Identity["ReceiptIdentity"]
    OriginalPayload = Identity["NativePayloadIdentity"]
    Identity["ReceiptIdentity"] = "ObservedReceipt:MiXeD-7"
    Identity["NativePayloadIdentity"] = "Payload/AbC"
    for Origin in Batch["Origins"]:
        if Origin["CanonicalReceiptIdentity"] == OriginalReceipt:
            Origin["CanonicalReceiptIdentity"] = Identity["ReceiptIdentity"]
        if Origin["NativePayloadIdentity"] == OriginalPayload:
            Origin["NativePayloadIdentity"] = Identity["NativePayloadIdentity"]
    Observation = ReadNativeObservation(tmp_path, Record)
    assert Observation["Status"] == "Available"
    assert Observation["EvidenceCoverage"] == "Partial"
    assert Observation["Record"] == Record


# Synthetic representative protocol specimen: unchanged literal scope/context,
# outcomes 0/1/2, origins 0/16/32/34 and admissions 32/34 selected from authentic
# producer evidence feac138d1f8cdbfc11f35a0358bd3159425c8d8f984b7a95ae695adafce01f6b.
# Aggregate counts/list selection change truthfully, original ordinals normalize
# to 0/1/2/3 with coherent canonical refs; native ordinals remain 0/1/2.
# Existing identity strings are observations, not re-attested native receipts.
# It is not an original
# run or a reconstruction of native canonical bytes. Producer identity includes
# Joint HEAD080e7df + dirty patch296afb7269f9196ad8d50561779f19ea8e6ba7ce74a8bb54336e48f3639bf1e5.
# Complete actual-run/live sealed-reader evidence stays with parent acceptance.
NativeProtocolSpecimenSha256 = "816e658d62d153d9dd82c67bdba0f4e2d1ae164b5e7beabd7836a4aba9900d2d"
NativeProtocolSpecimenCompressed = (
    'c-rk<TaO#LvHmN8o^k-`fGpnU&AYZ2i?bKQ-fRwH_<`4PCmKm<Nt2x{{NGb-Hl>zE^31GfHnGzfKutB-#Ufe!>Z>mG^skez?IJY6'
    'ZsLz_9ouWWX_kxi<l^nGldtT?zmKK-EL=6~^{y&^-E3l*Tr9Tp`E>FFUUnV-uUX)k{>w|-%(rcvOea6K&1JLrHpIoI+1y|Q111=i'
    'j0T|ICnLe|{1F7DIz=LZa-ltdcU(KdLgK<%W{npVzzCH2>ix~S@pk^=x<R=<Rz;4_?|UuvucKdH$M)vgX45uqn+N-ms^hEQ;Cx)!'
    'pJTi3uABUI*(^4*&CM!?S-aeB;>^~0nq9->!}OD9O4|?vI31K%Tq!5Dw^~qbeFSM(B1A;x7!L}Rv`q0prB~i48=W!wvze)P)5+J%'
    '#U}o;`Pt61Id+%*iq?sOLSOYwYgg|Fvtym2+!;ZD3onC?$)(6Nwblyj(VB#zK6oW{6eMXQ@x1an817BriQ?=W$bTKT>%3CQ&esv^'
    'wrz`jVdZ+$ZhcnijbE+?Gc}P(Qzn9Bj4+l$0_`-h38K_Mr~xm$AwtUtj%jbeMb90Pfyu;iIsIhV%M%Up?xpZWg_$op%&zlg{W{>}'
    '94L4&M<4R4ZSj1&E{lbBG@o~i_wB+juU7N8L4zaHJ<7almtpI(gmQMd=#21}=(pW2VOeCku$%1g!}68gyf4j{i_~1=R>5|BI(Z&#'
    'm^X`f)YXf>Z<}j7N0qCtbUJz2%u&m}+wW12w!!iJvzK(Z4=-T9JLWgfypO9*f93x4>-g8`cc<G}L>#H|dRYbq_gMv(;TG4R%xB&e'
    'cvZBmfB#*B7;+#QC%#NswaGI5^LcFFEbMCie!0nKzCxI37MB^b-%h??hB)7u)^y^vk`yr_1~gQ|yfTy%(1@}&c<+J}mKzpWl+L61'
    'Da`~4(h&k)IiJYn-Sq9`r|91=mh<K1%|LPjMDXNEV$NC0xlqP?<24wQObm)!=Owq&DG_a?f^Y=2m=rJ_64kVm{5r0e+jf9U(}@kh'
    'Atc8LbwX3+c|<)(FhqKqandIr0`o)%flkCjDMYQD6@uAL?yEUs=M~Bv2zJ(!Ku}*BRG>mqDnKgKE+V2D>O3_XcQypZZ4zLK(pE8w'
    '+nQ?bbm|1ZiSy1Pvcas^L$#$=gt8zJAGl9Ih|U;pOz^_u7bZYa!Yod7fMAI=mJ=ld(b`h!ps2l^H-0$D#N!p%TG|kzup%0zC1QgB'
    'X9F`S0!c<u2_!J(J<hZh#^ItTq7h6)C-r$;#YKkyn`$=09>5XDC8wSV1fdYY;M|+U1ml|N=qZ(4<KhPEJ&uksTpoc<BohW9Zu0Kk'
    'z0jvK+u6ETAfwNRoj2&L7`H^oHar75<zz~yVmbpp<$Uioo&Yyrgv%k+TZC?-oscYAts#a0N@z41j*DtI+9V|7h<Dmp+(eOO&#lpV'
    'X!L}+fD(Ve9XOCkP^*|E=)iSA`(v6Z!X-ls2$5RQxD{~6G37Ed3L4u9rx^`Fc*YaY&b=D*evarA$aKc1!0$Kv74tJ)*+J^F&G*a2'
    'a+AUN4LbWyG!s%7_P@e#NkJPkoqW5vUV3}z{wEwaj!f7JO@rd};2xyt45O2%zQs9~F>3tc6fr_NDUk6og`QHQOY|w+%VWg~ezSE('
    'zuso{`n-vM-xjh%E4u%<-5`KgI_o#v>~&Ulk^AVJ7`7GYeV$#T8?dgKcf+Zu`oh0oq9&+QlbV>792(wX&9_%q+l_T|^aiW#=CBT_'
    'Q*2hJPwqG4j%ek<K3`thW-+|t!}P$_KJ$NDEdMc&Xs-kEGHCER&&GN)texBD>dglI`epX5d-t3NHjj3(T|IBstL3^WL<~)L^K7+3'
    '2+HF^m)tHh$aT|jh0f{49Eoe4GhBz*eV=D>4>PI#lGqjcO+g5{(Y<x`LCL_}if#C+|A8;PrWcQ3jlEkvbVi5=9zmNU2on$tHzh;='
    '#nEFxav(6QiQY@<1!smwOPvGaq}3)Gjvl~zm^>wR9h%@kcCaAOV<eB5?yXPCNFr6>mIz@IQdD%O-U*J*2y7$@(1B<SG$2_?5cVf^'
    'JH4_u^Q8>~(VDPmd^7=d$Po(ypk@hG(?p?b;uJ@PlaO7JQphS)B-y=K7m+D5$@d#!AQb(XjMM{n90MRm(D8T-1(9u2&oLU*QX>)I'
    '0plbL9?=DbgzQ6VLXn1ZWhMukXeihi87)|(90n&KAdjci1xuO3ZcrM|H>!<v!cdFz?;_3;hGPQ#KB~|2ChViQp5^U;rbW(W=N$p;'
    'LFd(byN-FA^$adI#x1l=#|V@;W3=mT%e~By-c3rkdvDo%vGlgoW?L6R(8weRoR|PWFw{^{;B0fI4YGlR6pT^g2*-F4{|F5dBW#bB'
    '<g6vW&ecg+EKIZ?59)xFcV*jdB&F)jX1Q7oAm&ZnZ07kp#!mU`#<rXKwF`_-Loin(1dkB>B||VljA7VX7|_Z@?~uZ~fNVcUGA?6~'
    'u+Aijagh=UXN(Jt@Jypkiizkx0>P9Sz=;#cJ{j(5r?^M*ZlW~^!6F5W_Be?Y7enK6U>{0K85N>Mu3`El1S6eAh{%W{a)2g5DU^v}'
    'ppHoN5p6t)7!mNq0%A0RCJ<l*het9ZgFXwv0i3{C3nU@v3M|S3(6~I{&~;da6c}9<<&;t;w4uph01C`U6_dg6Dc%Eu>F*4|8UzNV'
    '7#7HYfv%AlmvTp}4H%)K+Z8S{v=8YR1s)uRZ3=@~jI=e9eT)#rSqRRs_<KPxHUF&;jDu+w+j8@9MBy(Rg^kqCn8X~zt;(e&f(e(b'
    'aX^t{YV22et~F!Kc!Js*W;sYjlX4vC)JITQWY&e#O%Vx{1BH^gL9z{yFoZ|>3<vI|0Tm-MG_8|Qlp)mNj3FMtNffr6a-ywFOc4#p'
    'NM%S85MBr}>m+>wW*J2?3@Tv@>m?H4fHQ)86{1h)Q8?E-CvYvKqbv$45!MCd8xX++oSonaml_!IWvwK-qdYypaa=luAqpR-GmgL*'
    'Zh>(Jz~_LN5;lP1=W&B_V<<>BxEcBYbY?m+^rI<h6@(1Z<kdyPu^ip7#b|+3NdwwDMjH+hS)w0H31beYBTypGqO-gW&VO38alM>h'
    '|5kW@SF=HP-mo}{abLx>Y-1;K2!_XZ+e^>8id*?7XN~q&p>2_%i9^RLld%xdTjM6P7!)Gq&|XC)1z_|SI7O=AeR4u#RmWuyh3Dfw'
    'KH612N#r+>-do6%22T$J_*-d;ewhw$><`&p1feB$5`mdu?<yh?CTA1i0-2n0iZM{>5^5|WXbfSEz%MKzmMNzuXS~Wc5lwc}-W;H#'
    'Kqyogz4LreoZwc8&LXBGLq&ul7(9EAEKnusoCX<Ck%Y5uwLo9t#1MI3w)XPiemi1L;AlcFBB(LMD@oDAFx+9rCW%Xnq?aj%^UNd*'
    '%u!{KT7q!6I}tk5fCAqgfj{Bhiek{u<Jz}qy300u<!8S%xZ0DyVD&fK2GQ~2ZN4R|-c8?<DaB7do$+#Dco4e-lmqP!r28!8Qc+s*'
    '?o)K1vinrsr|v#YxvIaoTusa6`DXB2S-O^`Pgy!M<)rE4QPK&dw49`@(}5OMs4fRnF2rLe9P+r{J^6ay65nMU@4i#q)az!cURd&*'
    'Z*a4Gw?uI0SYSrke80RSHzk8p(vEL&sc2o7z936s*Z$SA-Prl9_FVyLf#vo;+4eGS21Dqe{bX+^t}gv_<PRzGK3=IK_;vCkr=Z#l'
    'd0(ST=<<GSN{Ya)0JXrn0<BfFR?%7owF+t#lqy~Y@+y#5fn9<31yXfCHJZM_`$B08wJp@PyDp)&g?@98-Cn7Cht$0^TrQ}EQU$dN'
    'Y8CWzn_WbM!PK*+ty7oRce`5Qr0*v4?AWpGpYifR!*k2n<+j$FF4LV;OkMM%X4$sh9!(Xz%bk2@Z(`dm`geWQf@1D(JDmr7vB>-3'
    ';*Wg#2Ri-3jQ-CBxhO{X(mFJrLJ=<@Ql~wO`G+S`aC1TOu4yhiWbK9EnV({NRnT_V|K#V!R_pvwjecKWLeHVm=M|#7Un*Kx5#i;x'
    '8hu_BT9=1b)1=khXq_8cr<YdiqSf$dHN0LiQX!R8P$gAWGH4^J@^n9n<8kdqDtZ&7)hH`m*T%cvp){7YcUgOvwRc(jlC>{c`;t|%'
    'tdeDsd~dryJL+S*KRe<xc7NPH+x@Le@3Q;5&F8@GMQ8VvKbqaw{?FNcUw_-~<$*`3i}wj&|H!yl%&XUsR1L|XiuVPwcZ;`2(-*3h'
    'lHLIOwou#dx`f)cfW4|ZsH%giI;h&Es$HturK&xs+Jkx^*q<HyvC*Gj#?wZB*sOs4;Z)rN?C-Xn1EZIZ4fgU5u<z?X<T?1xE|3X}'
    ';fU^LZp)5d&%%yh_q^?vQbQ6plB1CveTL+yDA51xdNkkBd_NQO9ZC5}%0DYoKC<bNO@B6QI)AloV=GrUzTrH)!|H;DM48sc6UsO<'
    'Oc+L#)7shm<9FtTP?ky?6%j--aFTnP^P3Kg_op5{dW7W9shrR5VTqdCyOrgC%%AB!Akv66Bje-l>8<lWQhUm@Thvm5Hk2D>MgBn#'
    'r!*1a0#DvbO{KD)8soKt1k!6_JVfbq4Blw1<ww(Q^rLCFNBFhRL(*=K@PnBzDD8#|Ot{YD;!HW2fA0ladlwk)L5>!Hv+s?|&t4Eo'
    'Bw9P?0Rpvx8RbO!4@tYxC;f%mn06b}Ze!YQ49&;Td`!EI5&jtAk7>8@M!}eNJD+xg-H7Ozb{omjNRGy|+i1RH+HEA|BPk!#ZX=r>'
    '+4Lu*-9*wt5+8#TOsL2V(JrTHAOvkl5+O<lK$E4$ddaA;A(5agVaijh#i_L0BP4%L<$NBLcKhT<v!5jGmLE)h%Cy@f{9nrRX*c+2'
    '+U*e@?|eww?Gc`Y{DRVM5V=KR&T>LY{y&<+E9#g}Bywqu4vypZggcf5Zn*^9*g%En$~%$LU1>Lkrb2PEa)cxzwY4PXf3#yH(S$ME'
    '0Rpu~8EQFr)CH23S{j3J76J*AJN|3v?Z<4N@X6$lWZ(Y(DEpS-91pA<csYo2kmayX(2=frxd^6xbTmxd!5}SHIpRTjt@n=jsk_fz'
    'immfVrnnbV({H5vEM*vG;^0I=uBb(eUNt>e^rJ<ynyf1tenQUf5sc;Em$Sna-7WNPsq<o=$=Q9Z{oOe`(qEj9tUvzt+y(Ybs>zaS'
    'v}CUx)B>x8z6xp;)GFxJs+L{kl3pz7rRJmP)uuc34~J8SSK2|{DSbQW+tvP{YgflnhfzmS2T{jRhfqgQ2T=6CH^DqTykmBGdT?i`'
    '<zeGYERQa`i&oxY8J%ZDwy=rcE-wB^I#T)DBA{N#A*t+pKX-?=RNu}@n97As<-+FEvJ#Y3?q5o<E3s0dyIQ)drMp_#)xxf}k8X4o'
    'tgBpI#p)_mSE0Jf)K#Re5_J`5sz6f(nkvxL2+i~n+Sz&P3%oCswouzbZM*9dYTF#4-P_*Js(Nhl=hySJ)gLx10oCEi?h#PkZA6F0'
    'Z$2fWI%)mpjs%4{myOOYv;dN*2qiuV(J8M;$SV}`iiNy_A+LS$+B>g!$SWZ79$~5IA-yX=EwEbHwGmZ$x*x^yxOOA$8^ex3)W%R7'
    '?|Sb6Ue?}a?OoR1W$jDWzGUr7R>`tTmJdVvv!gz?{IerIWBJGJv*q8q^e)T4+k6f!pNhxE{i?w^%kS$4sX62dkNQa5Uae0^&Yk2x'
    'q_Q(ose7TwCmGQJmFo-YBuDBBq84N=bd90*4z+ivy+iE_YF|+Mf+`uRWauT|=3S&ef6TLJfBukHkxtINcgxw?JKinlN$~02a@;=e'
    'mRpzJwOc;Myie_x8GY>CQZ;zY#N5c?M-Kml9DZcqBm4d=+4o4BN80>((dGx_;G8sp5#?#3CdlLj>mC@gp3^8bmZgz0|2I-=D9yL{'
    'DMS!n6L2)Lz@F$0Wv3EA56i)Q>ObcEkIcax*NjQHe`gY|WJLc9a&UN^bJ{sbo+-+W2#5?sP%i>Wj-gp=$%6*Z(9ja66sJx(h{Tf&'
    'BJP1XxZ%70#x&e#n1*`<dl}PkV;XJ@;m0)Gcq8CTz7aql`9=U8({SVcgz<jD@0*6>V;XKu!;NXUF%36z_%RJPvhR_7k7>A(HjlLV'
    'L1{RqEn`9x5jiuK1w|uQfiewNNM%*<+`C{|qEvGrpac_9gHTaHWa{Lja7Nn@Lw-Gz$|F%uYHzim+WH96utcys<rohtrwo|lfl9Bu'
    'Q8qeb^r<u)`;5|X@6PJ_F_DX|eJ|NB7whd+#1Msb@^-Ho?(FH+9A`4$*qu2etxqHb4(ei_O?5qk$*_HXf#S<%aT(iH+hl{7D1bz$'
    'N;-kiLUjN8ad{nEdl}0+Lu(qYycMsvDpnIC-5V@xUU8p7$urTP<=q<tW1DwCTF#qgeS2%2hVly3E)n^oTgUdgEACtE-n4gxCR}&N'
    'p^y)GGMB5WaT#oBcsN=d-Tv<TsRlfnlpK$eYOP6^Sbo(k%I-YX9xXb!q@_Tc@}c{Gsk`=nZZ?a&sShsj))*5MH=YX{yC*$&3wR{j'
    'QkE=m5~Cx!dl_Cqkuw8(9oJo?+P#7Eur{hhannhOdpn?!Pd;?-Cfv_<hbGvtoa+7`<d0X)IvZvN$VqGeQBBfUi<mXpnr7~16Z5-M'
    '6RyK7w+o$mGu#8)xSru^%zW{YeVf!S{}~s9x11I^xVlY>>&<%d?)KK``(Eu$WfrfSP&QeP+yAt%<!!g~gH86~UxG+Y0R'
)

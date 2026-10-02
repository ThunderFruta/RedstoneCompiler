"""Independent native observations through candidate/coordinator diagnostics.

The native producer below is real. Its surrounding v3 origin/admission fixture
is synthetic, constructed through existing public records; it proves transport,
not physical admission. The separate ordinary TFlip pipeline checkpoint proves
the production path and is recorded in the spec-first plan, not a test dependency.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
from time import monotonic
from types import SimpleNamespace

import pytest

from RedstoneCompiler import RustRouting
from PhysicalDesign.Contracts.Failures import (
    RoutingFailure, RoutingFailureReason, RoutingStageError,
)
from PhysicalDesign.Runtime.NativeRouting import ExecuteNativeRouteBatchOutcomesV1
from PhysicalDesign.Orchestration.NativePreparationEvidence import (
    ProjectCoordinatorNativePreparationEvidence,
)
import PhysicalDesign.Orchestration.Setup as PlacementSetup
from PhysicalDesign.Routing.Global.NativePreparationEvidence import (
    BindCandidateNativePreparationEvidence,
    CaptureNativePreparationObservation,
    ObserveNativePreparationOutcomes,
)
from PhysicalDesign.Routing.Global.TypedRouteConsumer import (
    AuthorityIdentity, CALLER_BINDING_NAMES, TypedRouteAdmissionRecord,
    TypedRouteBatchCounters, TypedRouteCallerSnapshot, TypedRouteContextScope,
    TypedRouteExecutionScope, TypedRouteOriginDescriptor, TypedRouteOriginRecord,
)


OMISSIONS = [
    "raw-native-canonical-input-bytes",
    "full-raw-domain-values-and-claims",
    "full-frozen-candidate-input-manifests",
]


def DeclaredScope(Suffix="a"):
    return {
        "PlacementFingerprint": f"placement-{Suffix}",
        "ResourceGraphFingerprint": f"resources-{Suffix}",
        "PortalDomainFingerprint": "",
        "CandidateDomainFingerprint": "",
        "LocalClaimDomainFingerprint": f"local-claims-{Suffix}",
        "PinAccessDomainFingerprint": f"access-domain-{Suffix}",
        "PinAccessWitnessFingerprint": f"access-witness-{Suffix}",
    }


def ExpectedProducerObservation(Value):
    """Read the immutable pre-transport producer, using the frozen wire contract."""
    Receipt = Value.NativeReceipt
    Scope = Value.ExecutionScope
    return {
        "NativeOrdinal": Value.OriginalOrdinal,
        "RequestIdentity": Value.RequestIdentity,
        "Kind": Value.Kind.value,
        "Reason": Value.Reason,
        "SearchOutcome": Value.SearchOutcome.value,
        "ClaimStrength": Value.ClaimStrength.value,
        "CommitEligibility": Value.CommitEligibility.value,
        "OutcomePhase": Receipt.OutcomePhase,
        "Started": Receipt.Started,
        "Settled": Receipt.Settled,
        "CancellationRequested": Receipt.CancellationRequested,
        "CancellationAcknowledged": Receipt.CancellationAcknowledged,
        "SearchStopped": Receipt.SearchStopped,
        "CleanupDisposition": Receipt.CleanupDisposition,
        "DeadlineAtMonotonicSeconds": Scope.DeadlineAtMonotonicSeconds,
        "ExpansionCap": Value.ExpansionCap,
        "ActualExpansionCount": Value.ActualExpansionCount,
        "RouteExpansionCount": Receipt.RouteExpansionCount,
        "ProofExpansionCount": Receipt.ProofExpansionCount,
        "Identity": {
            "BatchIdentity": Scope.BatchIdentity,
            "ContextGraphIdentity": Scope.ContextGraphIdentity,
            "RouteDomainIdentity": Scope.RouteDomainIdentity,
            "CallerSourceIdentity": Scope.CallerSourceIdentity,
            "ImmutableInputIdentity": Scope.ImmutableInputIdentity,
            "ReceiptIdentity": Scope.ReceiptIdentity,
            "NativePayloadIdentity": Value.NativePayloadIdentity,
        },
        "Availability": {
            "ContextGraph": Receipt.ContextGraphIdentityAvailability,
            "RouteDomain": Receipt.RouteDomainIdentityAvailability,
            "ImmutableInput": Receipt.ImmutableInputIdentityAvailability,
            "Receipt": Receipt.ReceiptIdentityAvailability,
            "ReceiptDependency": Receipt.ReceiptIdentityDependency,
            "CallerEcho": Receipt.CallerEchoIdentityAvailability,
        },
    }


def DeclaredDescriptor(Ordinal):
    return TypedRouteOriginDescriptor(
        Signal="signal",
        SourcePortal={"Path": [[0, 0, 0]], "PortalId": "source"},
        TargetPortals=({"Path": [[2, 0, 0]], "PortalId": "target"},),
        Guide=((0, 0), (1, 0), (2, 0)), Layer=0, Axis="X", Lane=0,
        Variant=Ordinal,
        ImmutableFragments={
            "SchemaVersion": "joint-typed-route-immutable-fragments-v1",
            "SourceAccessPath": [[0, 0, 0]],
            "SeedLocalClaims": [],
            "TargetFragments": [{"AccessPath": [[2, 0, 0]]}],
        },
    )


def DeclaredOrigin(Value, OriginalOrdinal, CanonicalOrdinal, Scope, *, Equivalent=False):
    """Synthetic v3 association; actual native receipt values are never rebuilt."""
    CanonicalIdentity = AuthorityIdentity({"DeclaredOrigin": CanonicalOrdinal})
    return TypedRouteOriginRecord(
        OriginIdentity=AuthorityIdentity({"DeclaredOrigin": OriginalOrdinal}),
        OriginDescriptor=DeclaredDescriptor(OriginalOrdinal),
        OriginalOrdinal=OriginalOrdinal,
        CanonicalOriginalOrdinal=CanonicalOrdinal,
        CanonicalOriginIdentity=CanonicalIdentity,
        CanonicalRequestId=Value.RequestIdentity,
        CanonicalReceiptIdentity=Value.ExecutionScope.ReceiptIdentity,
        ExecutionScopeIdentity=Scope.Identity,
        GeometryIdentity=AuthorityIdentity({"DeclaredGeometry": CanonicalOrdinal}),
        NativePayloadIdentity=Value.NativePayloadIdentity,
        RouteDomainIdentity=Value.ExecutionScope.RouteDomainIdentity,
        NativeOrdinal=None if Equivalent else Value.OriginalOrdinal,
        NativeKind=Value.Kind.value,
        PreNativeReason=None,
        ExpansionCap=Value.ExpansionCap,
        ActualExpansionCount=Value.ActualExpansionCount,
        CancellationRequestedBeforeStart=Value.NativeReceipt.CancellationRequested,
        DeadlineAtMonotonicSeconds=Scope.DeadlineAtMonotonicSeconds,
    )


def StateForBatch(Batch, Outcomes, Scope):
    return SimpleNamespace(
        TypedNativeRouteBatches=[Batch],
        NativePreparationOutcomesByInvocationSequence={1: json.dumps({
            "NativeOutcomes": Outcomes,
        })},
        PlacementPinAccessWitness=SimpleNamespace(
            DomainFingerprint=Scope["PinAccessDomainFingerprint"],
            WitnessFingerprint=Scope["PinAccessWitnessFingerprint"],
        ),
    )


@pytest.fixture
def NativeFixture():
    """A real bounded producer plus declared partial v3 outer observations."""
    Nodes = ((0, 0, 0), (1, 0, 0), (2, 0, 0))
    Edges = ((Nodes[0], Nodes[1]), (Nodes[1], Nodes[2]))
    Bounds, PlacementBounds = (0, 0, 0, 2, 0, 0), (0, 0, 2, 0)
    Context = RustRouting.RoutingContext(Bounds, PlacementBounds, list(Nodes), list(Edges))
    Bindings = tuple((Name, AuthorityIdentity({"DeclaredCaller": Name})) for Name in CALLER_BINDING_NAMES)
    Cutoff = monotonic() + 10.0

    def Request(RequestId, *, Cap=32, Cancelled=False, Starts=(Nodes[0],)):
        return RustRouting.RouteTreeCoarseRequestV1(
            RequestId, list(Bindings), Bounds, PlacementBounds, Cancelled,
            list(Starts), [[Nodes[2]]], [(0, 0), (1, 0), (2, 0)], [], [], [],
            0, 0, 0, 0, Cap,
        )

    Requests = (
        Request("repeated-request", Cap=0),
        Request("repeated-request", Cancelled=True),
        Request("repeated-request", Starts=((99, 0, 0),)),
        Request("routed-control"),
    )
    Result = ExecuteNativeRouteBatchOutcomesV1(
        Context, "diagnostic-transport-real-producer", Requests, Cutoff,
    )
    # Independent producer observation is retained before changed transport.
    Expected = [ExpectedProducerObservation(Value) for Value in Result.Results]
    Scope = TypedRouteExecutionScope(
        AuthorityIdentity({"DeclaredInvocation": "transport"}), Cutoff,
        TypedRouteContextScope.FromConstruction(
            Bounds, PlacementBounds, Nodes, Edges, Context.AuthoritativeContextGraphSha256,
        ),
        TypedRouteCallerSnapshot(Bindings),
    )
    Origins = [DeclaredOrigin(Value, Index, Index, Scope)
               for Index, Value in enumerate(Result.Results)]
    Origins.append(DeclaredOrigin(Result.Results[2], 4, 2, Scope, Equivalent=True))
    Admissions = [TypedRouteAdmissionRecord(
        OriginIdentity=Origin.OriginIdentity, NativeKind=Origin.NativeKind,
        Admitted=None, PhysicalEvidence=None, RecoveryAttribution=None,
    ).ToDictionary() for Origin in Origins if Origin.NativeKind != "Routed"]
    KindCounts = Counter(Origin.NativeKind for Origin in Origins)
    Counters = TypedRouteBatchCounters(
        Configured=5, Materialized=5, Filtered=0, CanonicalExecuted=4,
        EquivalentReused=1, Routed=KindCounts["Routed"], CompleteScopedNoPath=0,
        SearchLimitIncomplete=KindCounts["SearchLimitIncomplete"], DeadlineIncomplete=0,
        CancellationIncomplete=KindCounts["CancellationIncomplete"],
        NativeFailure=KindCounts["NativeFailure"], PreNativeIncomplete=0,
        CandidateProduced=0, PhysicallyAccepted=0, PhysicallyRejected=0,
    )
    Batch = {
        "SchemaVersion": "joint-typed-native-route-consumer-v3",
        "InvocationSequence": 1, "ExecutionScope": Scope.ToDictionary(),
        "PreNativeReason": None, "ContextValidation": None,
        "Counters": Counters.ToDictionary(),
        "Origins": [Origin.ToDictionary() for Origin in Origins],
        "Admissions": Admissions,
    }
    return Result, Expected, Batch, DeclaredScope(), Cutoff


def BoundObservation(State, Scope, *, CandidateId="candidate-a", Input="input-a"):
    Observation = CaptureNativePreparationObservation(State, Scope=Scope)
    return BindCandidateNativePreparationEvidence(
        Observation, CandidateId=CandidateId, CandidateInputFingerprint=Input,
        Scope=Scope, SemanticDomainComplete=None,
    )


def Project(Bindings):
    return ProjectCoordinatorNativePreparationEvidence(SimpleNamespace(
        NativePreparationObservationsByCandidateId=Bindings,
    ))


def AssertUnavailable(Document):
    assert Document["ObservationState"] == "Unavailable"
    assert isinstance(Document["UnavailableReason"], str)
    assert Document["UnavailableReason"]


def test_real_native_metadata_and_repeated_ids_reach_coordinator_without_ordinal_loss(NativeFixture):
    Result, Expected, Batch, Scope, Cutoff = NativeFixture
    OriginalResults = Result.Results
    assert [Value.Reason for Value in OriginalResults] == [
        "WorkCapExhausted", "Cancelled", "UnsupportedRequest", "Found",
    ]
    Outcomes = ObserveNativePreparationOutcomes(OriginalResults)
    assert Result.Results is OriginalResults
    State = StateForBatch(Batch, json.loads(Outcomes)["NativeOutcomes"], Scope)
    Evidence = Project({"candidate-a": BoundObservation(State, Scope)})
    assert Evidence["SchemaVersion"] == "native-preparation-evidence-v1"
    assert Evidence["ObservationState"] == "Observed"
    assert Evidence["UnavailableReason"] is None
    assert Evidence["Omissions"] == OMISSIONS
    Candidate = Evidence["CandidateObservations"][0]
    assert Candidate["CandidateId"] == "candidate-a"
    assert Candidate["CandidateInputFingerprint"] == "input-a"
    assert Candidate["Scope"] == Scope
    assert Candidate["Coverage"] == {
        "PreparationObservationComplete": False,
        "NativeBatchCount": 1, "NativeOriginCount": 5, "CanonicalOutcomeCount": 4,
        "SemanticDomainComplete": None, "OuterPortfolioComplete": None,
    }
    PublishedBatch = Candidate["Batches"][0]
    assert PublishedBatch == {**Batch, "NativeOutcomes": Expected}
    assert [Value["NativeOrdinal"] for Value in PublishedBatch["NativeOutcomes"]] == [0, 1, 2, 3]
    assert [Value["RequestIdentity"] for Value in Expected[:3]] == ["repeated-request"] * 3
    assert all(Value["DeadlineAtMonotonicSeconds"] == Cutoff for Value in Expected)
    assert all(Value["CommitEligibility"] == "Ineligible" for Value in Expected)
    assert [Value["ExpansionCap"] for Value in Expected] == [0, 32, 32, 32]
    Canonical, Equivalent = PublishedBatch["Origins"][2], PublishedBatch["Origins"][4]
    assert Equivalent["NativeOrdinal"] is None
    assert Equivalent["OriginalOrdinal"] == 4
    assert Equivalent["CanonicalOriginalOrdinal"] == Canonical["OriginalOrdinal"] == 2
    assert Equivalent["CanonicalOriginIdentity"] == Canonical["OriginIdentity"]
    assert Equivalent["CanonicalReceiptIdentity"] == Expected[2]["Identity"]["ReceiptIdentity"]
    assert len(PublishedBatch["Admissions"]) == 4 < len(PublishedBatch["Origins"])
    assert all(Value["Admitted"] is None for Value in PublishedBatch["Admissions"])
    assert PublishedBatch["Counters"]["PhysicallyAccepted"] == 0
    assert PublishedBatch["Counters"]["PhysicallyRejected"] == 0


def test_candidate_evidence_detaches_nested_sources_and_exported_documents(NativeFixture):
    _Result, Expected, Batch, Scope, _Cutoff = NativeFixture
    State = StateForBatch(Batch, deepcopy(Expected), Scope)
    ExpectedBatch = {**deepcopy(Batch), "NativeOutcomes": deepcopy(Expected)}
    First = BoundObservation(State, Scope)
    SecondScope = DeclaredScope("b")
    SecondState = StateForBatch(Batch, deepcopy(Expected), SecondScope)
    Second = BoundObservation(SecondState, SecondScope, CandidateId="candidate-b", Input="input-b")
    Bindings = {"candidate-b": Second, "candidate-a": First}
    Before = Project(Bindings)

    Batch["Origins"][0]["OriginDescriptor"]["ImmutableFragments"]["SourceAccessPath"][0][0] = 99
    Batch["Origins"][4]["CanonicalOriginalOrdinal"] = 999
    Batch["Admissions"][0]["Admitted"] = True
    Batch["Counters"]["PhysicallyAccepted"] = 100
    State.NativePreparationOutcomesByInvocationSequence[1] = "changed source"
    Scope["PlacementFingerprint"] = "changed source"
    assert Project(Bindings) == Before
    ByCandidate = {Value["CandidateId"]: Value for Value in Before["CandidateObservations"]}
    assert set(ByCandidate) == {"candidate-a", "candidate-b"}
    assert ByCandidate["candidate-a"]["CandidateInputFingerprint"] == "input-a"
    assert ByCandidate["candidate-b"]["CandidateInputFingerprint"] == "input-b"
    assert ByCandidate["candidate-b"]["Scope"] == SecondScope
    assert all(Value["Batches"][0] == ExpectedBatch for Value in ByCandidate.values())

    Before["CandidateObservations"][0]["Batches"][0]["NativeOutcomes"][2]["Reason"] = "lost reason"
    Before["CandidateObservations"][1]["Batches"][0]["Origins"][0]["OriginDescriptor"].clear()
    Again = Project(Bindings)
    assert all(Value["Batches"][0] == ExpectedBatch for Value in Again["CandidateObservations"])


def test_recorded_pre_native_batch_retains_reason_caps_and_no_invented_receipt():
    Scope = DeclaredScope()
    Origin = TypedRouteOriginRecord(
        OriginIdentity=AuthorityIdentity({"DeclaredOrigin": 0}),
        OriginDescriptor=DeclaredDescriptor(0), OriginalOrdinal=0,
        CanonicalOriginalOrdinal=0,
        CanonicalOriginIdentity=AuthorityIdentity({"DeclaredOrigin": 0}),
        CanonicalRequestId=None, CanonicalReceiptIdentity=None,
        ExecutionScopeIdentity=None, GeometryIdentity=AuthorityIdentity({"Geometry": 0}),
        NativePayloadIdentity=None, RouteDomainIdentity=None, NativeOrdinal=None,
        NativeKind="PreNativeIncomplete", PreNativeReason="declared-incomplete-input",
        ExpansionCap=7, ActualExpansionCount=0, CancellationRequestedBeforeStart=False,
        DeadlineAtMonotonicSeconds=None,
    )
    Counters = TypedRouteBatchCounters(
        Configured=2, Materialized=1, Filtered=1, CanonicalExecuted=0,
        EquivalentReused=0, Routed=0, CompleteScopedNoPath=0,
        SearchLimitIncomplete=0, DeadlineIncomplete=0, CancellationIncomplete=0,
        NativeFailure=0, PreNativeIncomplete=1, CandidateProduced=0,
        PhysicallyAccepted=0, PhysicallyRejected=0,
    )
    Batch = {
        "SchemaVersion": "joint-typed-native-route-consumer-v3",
        "InvocationSequence": 1, "ExecutionScope": None,
        "PreNativeReason": "declared-incomplete-input", "ContextValidation": None,
        "Counters": Counters.ToDictionary(), "Origins": [Origin.ToDictionary()],
        "Admissions": [TypedRouteAdmissionRecord(
            Origin.OriginIdentity, "PreNativeIncomplete", None, None, None,
        ).ToDictionary()],
    }
    Evidence = Project({"candidate-a": BoundObservation(
        StateForBatch(Batch, [], Scope), Scope, Input=None,
    )})
    assert Evidence["ObservationState"] == "Observed"
    Candidate = Evidence["CandidateObservations"][0]
    assert Candidate["CandidateInputFingerprint"] is None
    assert Candidate["Batches"] == [{**Batch, "NativeOutcomes": []}]
    assert Candidate["Coverage"]["NativeBatchCount"] == 1
    assert Candidate["Coverage"]["NativeOriginCount"] == 1
    assert Candidate["Coverage"]["CanonicalOutcomeCount"] == 0
    assert Candidate["Coverage"]["SemanticDomainComplete"] is None


def test_explicit_empty_submission_is_distinct_from_absent_or_unpublished_evidence():
    Scope = DeclaredScope()
    Empty = SimpleNamespace(
        TypedNativeRouteBatches=[], NativePreparationOutcomesByInvocationSequence={},
        PlacementPinAccessWitness=SimpleNamespace(
            DomainFingerprint=Scope["PinAccessDomainFingerprint"],
            WitnessFingerprint=Scope["PinAccessWitnessFingerprint"],
        ),
    )
    Observed = Project({"candidate-a": BoundObservation(Empty, Scope, Input=None)})
    assert Observed["ObservationState"] == "NoNativeSubmissionObserved"
    Candidate = Observed["CandidateObservations"][0]
    assert Candidate["Batches"] == []
    assert Candidate["Coverage"] == {
        "PreparationObservationComplete": True, "NativeBatchCount": 0,
        "NativeOriginCount": 0, "CanonicalOutcomeCount": 0,
        "SemanticDomainComplete": None, "OuterPortfolioComplete": None,
    }
    AssertUnavailable(ProjectCoordinatorNativePreparationEvidence(SimpleNamespace()))
    Empty.NativePreparationOutcomesByInvocationSequence[1] = json.dumps({"NativeOutcomes": []})
    AssertUnavailable(Project({"candidate-a": BoundObservation(Empty, Scope, Input=None)}))


@pytest.mark.parametrize("Mismatch", ("scope", "scope-null", "original-request", "missing-outcome"))
def test_mismatched_observation_stays_unavailable_without_candidate_authority(NativeFixture, Mismatch):
    _Result, Expected, Batch, Scope, _Cutoff = NativeFixture
    State = StateForBatch(Batch, Expected, Scope)
    if Mismatch == "original-request":
        Batch["Origins"][2]["CanonicalRequestId"] = "other-request"
    elif Mismatch == "missing-outcome":
        State.NativePreparationOutcomesByInvocationSequence[1] = json.dumps({"NativeOutcomes": Expected[:-1]})
    Observation = CaptureNativePreparationObservation(State, Scope=Scope)
    if Mismatch in {"scope", "scope-null"}:
        Scope = {**Scope, "PlacementFingerprint": None if Mismatch == "scope-null" else "foreign-placement"}
    Encoded = BindCandidateNativePreparationEvidence(
        Observation, CandidateId="candidate-a", CandidateInputFingerprint="input-a",
        Scope=Scope, SemanticDomainComplete=None,
    )
    AssertUnavailable(Project({"candidate-a": Encoded}))


@pytest.mark.parametrize("Malformed", ("invalid-json", "nonserializable", "absent"))
def test_public_preparation_failure_survives_malformed_diagnostic_serialization(monkeypatch, Malformed):
    OriginalDiagnostics = {
        "RawTrackAssignmentSelection": {
            "SchemaVersion": "raw-track-assignment-failure-envelope-v1",
            "SemanticResult": {"Success": False, "Complete": False, "Unsatisfiable": False},
        },
        "Deadline": {"Expired": True, "RemainingMilliseconds": 0},
    }
    Before = deepcopy(OriginalDiagnostics)
    OriginalFailure = RoutingFailure(
        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
        Stage="PreRouteInterfaceSelection", Diagnostics=OriginalDiagnostics,
    )
    OriginalError = RoutingStageError(OriginalFailure)

    def DeclaredCapacityFailure(Context, Candidates):
        raise OriginalError

    Values = {
        "invalid-json": {"candidate-a": "{incomplete-json"},
        "nonserializable": {"candidate-a": object()},
        "absent": {},
    }
    Context = SimpleNamespace(
        SinglePackedComponent=False, CandidateRecords=(),
        NativePreparationObservationsByCandidateId=Values[Malformed],
    )
    monkeypatch.setattr(PlacementSetup, "SolvePrePlacementCapacityProblem", DeclaredCapacityFailure)
    with pytest.raises(RoutingStageError) as Error:
        PlacementSetup.PreparePlacementRouting(Context)
    Published = Error.value.Failure
    assert replace(Published, Diagnostics=OriginalDiagnostics) == OriginalFailure
    assert OriginalFailure.Diagnostics == Before
    assert Published.Reason is RoutingFailureReason.RuntimeBudgetExceeded
    assert Published.Stage == "PreRouteInterfaceSelection"
    assert {Key: Value for Key, Value in Published.Diagnostics.items()
            if Key != "NativePreparationEvidence"} == Before
    AssertUnavailable(Published.Diagnostics["NativePreparationEvidence"])
    assert Error.value.__cause__ is OriginalError

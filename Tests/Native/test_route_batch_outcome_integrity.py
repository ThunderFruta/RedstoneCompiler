"""Adversarial integrity checks for native route-batch outcome authority."""

from __future__ import annotations

import hashlib
from statistics import median
from time import monotonic

import pytest

from RedstoneCompiler import RustRouting
from Tests.Native.test_route_batch_outcomes import _bindings, _coarse, _context, _detailed


class _FalseDigest:
    def hexdigest(self) -> str:
        return "f" * 64


def test_native_digest_authority_ignores_malformed_python_hash_provider(monkeypatch):
    OriginalSha256 = hashlib.sha256
    monkeypatch.setattr(hashlib, "sha256", lambda _Value=b"": _FalseDigest())

    Result = _context(Edges=()).GenerateRouteTreesBatchOutcomesV1(
        "digest-authority",
        (_coarse(),),
        monotonic() + 10,
    )
    Receipt = Result.Receipts[0]

    assert Result.ContextGraphSha256 == OriginalSha256(
        Result.ContextGraphCanonicalJson.encode()
    ).hexdigest()
    assert Receipt.RouteDomainScopeSha256 == OriginalSha256(
        Receipt.RouteDomainScopeCanonicalJson.encode()
    ).hexdigest()
    assert Receipt.ImmutableInputSha256 == OriginalSha256(
        Receipt.ImmutableInputCanonicalJson.encode()
    ).hexdigest()
    assert Receipt.ReceiptScopeSha256 == OriginalSha256(
        Receipt.ReceiptScopeCanonicalJson.encode()
    ).hexdigest()
    assert Receipt.CallerEchoScopeSha256 == OriginalSha256(
        Receipt.CallerEchoScopeCanonicalJson.encode()
    ).hexdigest()
    assert Receipt.NoPathProof.ContextGraphSha256 == Result.ContextGraphSha256
    assert Receipt.NoPathProof.RouteDomainScopeSha256 == Receipt.RouteDomainScopeSha256
    assert Receipt.NoPathProof.ImmutableInputSha256 == Receipt.ImmutableInputSha256
    assert Receipt.NoPathProof.ReceiptScopeSha256 == Receipt.ReceiptScopeSha256
    assert Receipt.SearchOutcome == "ProvenNoPath"


def test_native_digest_authority_ignores_raising_python_hash_provider(monkeypatch):
    def Raise(_Value=b""):
        raise RuntimeError("malformed Python digest provider")

    monkeypatch.setattr(hashlib, "sha256", Raise)

    Receipt = _context(Edges=()).GenerateRouteTreesBatchOutcomesV1(
        "digest-provider-exception",
        (_coarse(),),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome == "ProvenNoPath"
    assert len(Receipt.ContextGraphSha256) == 64
    assert len(Receipt.RouteDomainScopeSha256) == 64


def test_request_objects_expose_the_versioned_contract():
    Request = _coarse()

    assert Request.ContractVersion == "native-route-batch-outcomes/v1"


def test_batch_envelope_requires_exact_builtin_string_and_tuple_types():
    class BatchIdentitySubclass(str):
        pass

    class RequestTupleSubclass(tuple):
        pass

    Context = _context()
    Request = _coarse()
    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "exact-envelope",
        (Request,),
        monotonic() + 10,
    )

    assert Result.BatchIdentity == "exact-envelope"
    assert Result.BatchIdentityRetentionStatus == "SealedUtf8"
    assert Result.TotalRequestCount == 1

    UnicodeResult = Context.GenerateRouteTreesBatchOutcomesV1(
        "exact-envelope-é-🚦",
        (),
        monotonic() + 10,
    )
    assert UnicodeResult.BatchIdentity == "exact-envelope-é-🚦"
    assert UnicodeResult.BatchIdentityRetentionStatus == "SealedUtf8"

    with pytest.raises(TypeError):
        Context.GenerateRouteTreesBatchOutcomesV1(
            BatchIdentitySubclass("subclass-identity"),
            (Request,),
            monotonic() + 10,
        )
    with pytest.raises(TypeError):
        Context.GenerateRouteTreesBatchOutcomesV1(
            "list-envelope",
            [Request],
            monotonic() + 10,
        )
    with pytest.raises(TypeError):
        Context.GenerateRouteTreesBatchOutcomesV1(
            "tuple-subclass-envelope",
            RequestTupleSubclass((Request,)),
            monotonic() + 10,
        )
    with pytest.raises(TypeError):
        Context.GenerateRouteTreesBatchOutcomesV1(
            "iterable-envelope",
            iter((Request,)),
            monotonic() + 10,
        )
    with pytest.raises(UnicodeError):
        Context.GenerateRouteTreesBatchOutcomesV1(
            "live-surrogate-\ud800",
            (Request,),
            monotonic() + 10,
        )


def test_unavailable_domain_identity_is_typed_and_absent():
    Receipt = _context().GenerateRouteTreesBatchOutcomesV1(
        "typed-unavailable-domain",
        (_coarse(Starts=((99, 0, 0),)),),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.RawInputRetentionStatus == "SealedCanonicalBytes"
    assert Receipt.CancellationSnapshotStatus == "Captured"
    assert Receipt.ContextGraphIdentityAvailability == "Verified"
    assert Receipt.ImmutableInputIdentityAvailability == "Verified"
    assert Receipt.CallerEchoIdentityAvailability == "Verified"
    assert Receipt.RouteDomainIdentityAvailability == "UnavailableUnsupportedInput"
    assert Receipt.RouteDomainScopeCanonicalJson is None
    assert Receipt.RouteDomainScopeSha256 is None
    assert Receipt.ReceiptIdentityAvailability == "DependencyUnavailable"
    assert Receipt.ReceiptIdentityDependency == "RouteDomainIdentity"
    assert Receipt.ReceiptScopeCanonicalJson is None
    assert Receipt.ReceiptScopeSha256 is None


def test_expired_three_million_preferred_columns_return_before_unbounded_clone():
    Request = _coarse(
        RequestId="large-preferred-columns",
        PreferredColumns=((Index, 0) for Index in range(3_000_000)),
    )
    Started = monotonic()
    Receipt = _context().GenerateRouteTreesBatchOutcomesV1(
        "large-preferred-columns",
        (Request,),
        monotonic() - 1,
    ).Receipts[0]
    Elapsed = monotonic() - Started

    print(f"three-million-preferred-columns elapsed={Elapsed:.9f}s")
    assert Receipt.Started is False
    assert Receipt.TotalExpansionCount == 0
    assert Receipt.TerminalReason == "DeadlineExhaustedAtEntry"
    assert Receipt.OutcomePhase == "Entry"
    assert Receipt.RouteDomainIdentityAvailability == "UncomputedDueToDeadline"
    assert Receipt.RouteDomainScopeCanonicalJson is None
    assert Receipt.RouteDomainScopeSha256 is None
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_expired_empty_batch_uses_presealed_large_context_identity():
    LargeNodes = [(Index, 0, 0) for Index in range(500_000)]
    Large = RustRouting.RoutingContext(
        (0, 0, 0, 499_999, 0, 0),
        (0, 0, 499_999, 0),
        LargeNodes,
        [],
    )
    Tiny = _context(Edges=(), Nodes=((0, 0, 0),))

    def Samples(Context):
        Values = []
        for _ in range(5):
            Started = monotonic()
            Result = Context.GenerateRouteTreesBatchOutcomesV1(
                "expired-empty-context",
                (),
                monotonic() - 1,
            )
            Values.append(monotonic() - Started)
            assert Result.ContextGraphIdentityAvailability == "Verified"
        return Values

    TinySamples = Samples(Tiny)
    LargeSamples = Samples(Large)

    print(
        "presealed-context empty-batch medians "
        f"tiny={median(TinySamples):.9f}s large={median(LargeSamples):.9f}s"
    )


def test_expired_entry_retains_large_python_batch_identity_without_utf8_sealing():
    BatchIdentity = "batch-" + ("x" * 3_000_000)
    Request = _coarse()
    Started = monotonic()
    Result = _context().GenerateRouteTreesBatchOutcomesV1(
        BatchIdentity,
        (Request,),
        monotonic() - 1,
    )
    Elapsed = monotonic() - Started

    print(f"three-million-byte batch identity elapsed={Elapsed:.9f}s")
    assert Result.BatchIdentity == BatchIdentity
    assert Result.BatchIdentityRetentionStatus == "RetainedPythonObject"
    assert Result.Receipts[0].BatchIdentity == BatchIdentity
    assert Result.Receipts[0].BatchIdentityRetentionStatus == "RetainedPythonObject"
    assert Result.Receipts[0].TerminalReason == "DeadlineExhaustedAtEntry"


def test_expired_entry_does_not_eagerly_utf8_encode_batch_identity():
    BatchIdentity = "expired-surrogate-\ud800"
    Result = _context().GenerateRouteTreesBatchOutcomesV1(
        BatchIdentity,
        (_coarse(),),
        monotonic() - 1,
    )

    assert Result.BatchIdentity == BatchIdentity
    assert Result.BatchIdentityRetentionStatus == "RetainedPythonObject"
    assert Result.Receipts[0].BatchIdentity == BatchIdentity
    assert Result.Receipts[0].TerminalReason == "DeadlineExhaustedAtEntry"


def test_large_request_sequence_retains_every_entry_slot_without_semantic_work():
    Request = _coarse(RequestId="duplicate")
    RequestCount = 10_000
    Result = _context().GenerateRouteTreesBatchOutcomesV1(
        "large-request-sequence",
        (Request,) * RequestCount,
        monotonic() - 1,
    )

    assert Result.TotalRequestCount == RequestCount
    assert Result.SettledReceiptCount == RequestCount
    assert Result.AggregateExpansionCount == 0
    assert [Receipt.OriginalOrdinal for Receipt in Result.Receipts] == list(
        range(RequestCount)
    )
    assert all(Receipt.RequestId == "duplicate" for Receipt in Result.Receipts)
    assert all(
        Receipt.TerminalReason == "DeadlineExhaustedAtEntry"
        and Receipt.OutcomePhase == "Entry"
        and not Receipt.Started
        and Receipt.TotalExpansionCount == 0
        and Receipt.RouteDomainIdentityAvailability == "UncomputedDueToDeadline"
        and Receipt.Candidate is None
        and Receipt.NoPathProof is None
        for Receipt in Result.Receipts
    )


def test_context_mutation_replaces_execution_graph_and_identity_transactionally():
    Context = _context()
    Before = Context.GenerateRouteTreesBatchOutcomesV1(
        "context-before-mutation",
        (),
        monotonic() + 10,
    )

    with pytest.raises(ValueError):
        Context.AddRegion([(3, 0, 0)], [((3, 0, 0), (99, 0, 0))])

    AfterRejectedMutation = Context.GenerateRouteTreesBatchOutcomesV1(
        "context-after-rejected-mutation",
        (),
        monotonic() + 10,
    )
    assert AfterRejectedMutation.ContextGraphCanonicalJson == Before.ContextGraphCanonicalJson
    assert AfterRejectedMutation.ContextGraphSha256 == Before.ContextGraphSha256

    Context.AddRegion([(3, 0, 0)], [((2, 0, 0), (3, 0, 0))])
    AfterCommittedMutation = Context.GenerateRouteTreesBatchOutcomesV1(
        "context-after-committed-mutation",
        (),
        monotonic() + 10,
    )
    assert AfterCommittedMutation.ContextGraphCanonicalJson != Before.ContextGraphCanonicalJson
    assert AfterCommittedMutation.ContextGraphSha256 != Before.ContextGraphSha256


def test_verified_identity_availability_is_monotonic_across_incomplete_outcomes():
    Context = _context()
    Results = [
        Context.GenerateRouteTreesBatchOutcomesV1(
            "availability-cap",
            (_coarse(MaximumExpansionCount=0),),
            monotonic() + 10,
        ).Receipts[0],
        Context.GenerateRouteTreesBatchOutcomesV1(
            "availability-cancel",
            (_coarse(CancellationRequestedBeforeStart=True),),
            monotonic() + 10,
        ).Receipts[0],
    ]

    assert [Receipt.TerminalReason for Receipt in Results] == [
        "WorkCapExhausted",
        "Cancelled",
    ]
    for Receipt in Results:
        assert Receipt.ContextGraphIdentityAvailability == "Verified"
        assert Receipt.ImmutableInputIdentityAvailability == "Verified"
        assert Receipt.CallerEchoIdentityAvailability == "Verified"
        assert Receipt.RouteDomainIdentityAvailability == "Verified"
        assert Receipt.RouteDomainScopeCanonicalJson is not None
        assert Receipt.RouteDomainScopeSha256 is not None
        assert Receipt.Candidate is None
        assert Receipt.NoPathProof is None

    EntryExpired = Context.GenerateRouteTreesBatchOutcomesV1(
        "availability-entry-deadline",
        (_coarse(),),
        monotonic() - 1,
    ).Receipts[0]
    assert EntryExpired.TerminalReason == "DeadlineExhaustedAtEntry"
    assert EntryExpired.OutcomePhase == "Entry"
    assert EntryExpired.ContextGraphIdentityAvailability == "Verified"
    assert EntryExpired.ImmutableInputIdentityAvailability == "Verified"
    assert EntryExpired.CallerEchoIdentityAvailability == "Verified"
    assert EntryExpired.RouteDomainIdentityAvailability == "UncomputedDueToDeadline"
    assert EntryExpired.RouteDomainScopeCanonicalJson is None
    assert EntryExpired.ReceiptIdentityAvailability == "DependencyUnavailable"


@pytest.mark.parametrize(
    ("Detailed", "Request"),
    (
        (False, _coarse(RequestId="large-starts", Starts=((0, 0, 0),) * 128)),
        (False, _coarse(RequestId="large-branch-count", TargetBranches=(((2, 0, 0),),) * 128)),
        (False, _coarse(RequestId="large-branch-length", TargetBranches=(((2, 0, 0),) * 128,))),
        (False, _coarse(RequestId="large-columns", AllowedColumns=((0, 0),) * 128)),
        (False, _coarse(RequestId="large-required", RequiredNodes=((2, 0, 0),) * 128)),
        (False, _coarse(RequestId="large-blocked", BlockedNodeValues=((1, 0, 0),) * 128)),
        (False, _coarse(RequestId="large-preferred", PreferredColumns=((1, 0),) * 128)),
        (True, _detailed(RequestId="large-allowed-nodes", AllowedNodes=((0, 0, 0),) * 128)),
        (True, _detailed(RequestId="large-detailed-blocked", BlockedNodeValues=((1, 0, 0),) * 128)),
        (True, _detailed(RequestId="large-detailed-preferred", PreferredColumns=((1, 0),) * 128)),
        (True, _detailed(RequestId="large-node-costs", NodeCostValues=((((1, 0, 0), 1),) * 128))),
    ),
)
def test_every_large_request_field_class_stops_at_expired_entry(Detailed, Request):
    Context = _context()
    Method = (
        Context.GenerateRouteTreeDetailedBatchOutcomesV1
        if Detailed
        else Context.GenerateRouteTreesBatchOutcomesV1
    )

    Receipt = Method("large-field-deadline", (Request,), monotonic() - 1).Receipts[0]

    assert Receipt.TerminalReason == "DeadlineExhaustedAtEntry"
    assert Receipt.OutcomePhase == "Entry"
    assert Receipt.Started is False
    assert Receipt.TotalExpansionCount == 0
    assert Receipt.RouteDomainIdentityAvailability == "UncomputedDueToDeadline"
    assert Receipt.RouteDomainScopeCanonicalJson is None
    assert Receipt.RouteDomainScopeSha256 is None
    assert Receipt.ImmutableInputIdentityAvailability == "Verified"
    assert Receipt.CallerEchoIdentityAvailability == "Verified"
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


@pytest.mark.parametrize(
    ("FieldName", "Detailed"),
    (
        ("starts", False),
        ("branch-count", False),
        ("branch-length", False),
        ("allowed-columns", False),
        ("required-nodes", False),
        ("blocked-nodes", False),
        ("preferred-columns", False),
        ("allowed-nodes", True),
        ("detailed-blocked", True),
        ("detailed-preferred", True),
        ("node-costs", True),
    ),
)
def test_every_large_request_field_class_respects_positive_tiny_cutoff(
    FieldName, Detailed
):
    Count = 100_000
    if FieldName == "starts":
        Request = _coarse(Starts=((0, 0, 0),) * Count)
    elif FieldName == "branch-count":
        Request = _coarse(TargetBranches=(((2, 0, 0),),) * Count)
    elif FieldName == "branch-length":
        Request = _coarse(TargetBranches=(((2, 0, 0),) * Count,))
    elif FieldName == "allowed-columns":
        Request = _coarse(AllowedColumns=((0, 0),) * Count)
    elif FieldName == "required-nodes":
        Request = _coarse(RequiredNodes=((2, 0, 0),) * Count)
    elif FieldName == "blocked-nodes":
        Request = _coarse(BlockedNodeValues=((1, 0, 0),) * Count)
    elif FieldName == "preferred-columns":
        Request = _coarse(PreferredColumns=((1, 0),) * Count)
    elif FieldName == "allowed-nodes":
        Request = _detailed(AllowedNodes=((0, 0, 0),) * Count)
    elif FieldName == "detailed-blocked":
        Request = _detailed(BlockedNodeValues=((1, 0, 0),) * Count)
    elif FieldName == "detailed-preferred":
        Request = _detailed(PreferredColumns=((1, 0),) * Count)
    else:
        Request = _detailed(NodeCostValues=((((1, 0, 0), 1),) * Count))
    Context = _context()
    Method = (
        Context.GenerateRouteTreeDetailedBatchOutcomesV1
        if Detailed
        else Context.GenerateRouteTreesBatchOutcomesV1
    )

    Result = Method("positive-tiny-field-deadline", (Request,), monotonic() + 0.001)
    Receipt = Result.Receipts[0]

    if Receipt.TerminalReason in {
        "DeadlineExhaustedAtEntry",
        "DeadlineExhaustedDuringValidation",
    }:
        assert Receipt.Started is False
        assert Receipt.TotalExpansionCount == 0
        assert Receipt.RouteDomainIdentityAvailability == "UncomputedDueToDeadline"
        assert Receipt.RouteDomainScopeCanonicalJson is None
        assert Receipt.RouteDomainScopeSha256 is None
        assert Receipt.Candidate is None
        assert Receipt.NoPathProof is None
    elif Receipt.TerminalReason == "UnsupportedRequest":
        assert Receipt.RouteDomainIdentityAvailability == "UnavailableUnsupportedInput"
        assert Receipt.RouteDomainScopeCanonicalJson is None
        assert Receipt.RouteDomainScopeSha256 is None
        assert Receipt.Candidate is None
        assert Receipt.NoPathProof is None
    else:
        assert Receipt.RouteDomainIdentityAvailability == "Verified"
        assert Receipt.RouteDomainScopeCanonicalJson is not None
        assert Receipt.RouteDomainScopeSha256 is not None
        if Receipt.SearchOutcome in {"Found", "ProvenNoPath"}:
            assert Receipt.ReceiptIdentityAvailability == "Verified"
            assert Result.DeadlineExceeded is False


@pytest.mark.parametrize(
    "Edges",
    (
        (((0, 0, 0), (1, 0, 0)), ((1, 0, 0), (2, 0, 0))),
        (),
    ),
)
def test_actual_public_late_terminal_claim_is_cleared_during_finalization(Edges):
    Context = _context(Edges=Edges)
    Request = _coarse()
    Observed = None
    for Allowance in (0.000_010, 0.000_020):
        for _ in range(20_000):
            Cutoff = monotonic() + Allowance
            Receipt = Context.GenerateRouteTreesBatchOutcomesV1(
                "actual-late-finalization",
                (Request,),
                Cutoff,
            ).Receipts[0]
            if (
                Receipt.TerminalReason == "DeadlineExhaustedDuringFinalization"
                and Receipt.Started
            ):
                Observed = Receipt
                break
        if Observed is not None:
            break

    assert Observed is not None
    assert Observed.Started is True
    assert Observed.OutcomePhase == "ReceiptFinalization"
    assert Observed.RouteDomainIdentityAvailability == "Verified"
    assert Observed.ReceiptIdentityAvailability == "UncomputedDueToDeadline"
    assert Observed.ReceiptScopeCanonicalJson is None
    assert Observed.ReceiptScopeSha256 is None
    assert Observed.SearchOutcome == "Incomplete"
    assert Observed.RuntimeSearchOutcome == "Unresolved"
    assert Observed.Candidate is None
    assert Observed.NoPathProof is None


def test_expired_entry_preserves_cancellation_without_acknowledging_unrun_validation():
    Receipt = _context().GenerateRouteTreesBatchOutcomesV1(
        "cancel-precedence",
        (_coarse(MaximumExpansionCount=0, CancellationRequestedBeforeStart=True),),
        monotonic() - 1,
    ).Receipts[0]

    assert Receipt.TerminalReason == "DeadlineExhaustedAtEntry"
    assert Receipt.OutcomePhase == "Entry"
    assert Receipt.CancellationRequested is True
    assert Receipt.CancellationAcknowledged is False
    assert Receipt.SearchStopped is False
    assert Receipt.Started is False
    assert Receipt.TotalExpansionCount == 0


def test_unsupported_slot_preserves_raw_cancellation_snapshot_without_acknowledging():
    Receipt = _context().GenerateRouteTreesBatchOutcomesV1(
        "unsupported-cancellation",
        (
            _coarse(
                Starts=((99, 0, 0),),
                CancellationRequestedBeforeStart=True,
            ),
        ),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.TerminalReason == "UnsupportedRequest"
    assert Receipt.CancellationRequested is True
    assert Receipt.CancellationAcknowledged is False
    assert Receipt.SearchStopped is False
    assert Receipt.Started is False
    assert Receipt.NoPathProof is None


def test_receipt_separates_route_domain_input_and_slot_identities():
    Context = _context()
    Active = Context.GenerateRouteTreesBatchOutcomesV1(
        "identity-batch",
        (_coarse(RequestId="same", CancellationRequestedBeforeStart=False),),
        monotonic() + 10,
    ).Receipts[0]
    Cancelled = Context.GenerateRouteTreesBatchOutcomesV1(
        "identity-batch",
        (_coarse(RequestId="same", CancellationRequestedBeforeStart=True),),
        monotonic() + 10,
    ).Receipts[0]

    assert Active.RouteDomainScopeSha256 == Cancelled.RouteDomainScopeSha256
    assert Active.ImmutableInputSha256 != Cancelled.ImmutableInputSha256
    assert Active.ReceiptScopeSha256 != Cancelled.ReceiptScopeSha256


def test_duplicate_ids_with_different_payloads_have_distinct_input_identity():
    Result = _context().GenerateRouteTreesBatchOutcomesV1(
        "duplicate-different-payload",
        (
            _coarse(RequestId="duplicate", MaximumExpansionCount=1),
            _coarse(RequestId="duplicate", MaximumExpansionCount=32),
        ),
        monotonic() + 10,
    )

    assert [Receipt.OriginalOrdinal for Receipt in Result.Receipts] == [0, 1]
    assert Result.Receipts[0].RouteDomainScopeSha256 != Result.Receipts[1].RouteDomainScopeSha256
    assert Result.Receipts[0].ImmutableInputSha256 != Result.Receipts[1].ImmutableInputSha256
    assert Result.Receipts[0].ReceiptScopeSha256 != Result.Receipts[1].ReceiptScopeSha256


def test_live_entry_can_expire_during_validation_and_preserve_cancellation_snapshot():
    Columns = tuple((Index, 0) for Index in range(100_000))
    Receipt = _context().GenerateRouteTreesBatchOutcomesV1(
        "validation-deadline",
        (
            _coarse(
                AllowedColumns=Columns,
                CancellationRequestedBeforeStart=True,
            ),
        ),
        monotonic() + 0.001,
    ).Receipts[0]

    assert Receipt.TerminalReason == "DeadlineExhaustedDuringValidation"
    assert Receipt.OutcomePhase == "DomainCanonicalization"
    assert Receipt.CancellationRequested is True
    assert Receipt.CancellationAcknowledged is False
    assert Receipt.SearchStopped is False
    assert Receipt.Started is False
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_found_may_win_on_exact_last_admitted_route_expansion():
    Context = _context()
    Limited = Context.GenerateRouteTreesBatchOutcomesV1(
        "exact-found-limited",
        (_coarse(MaximumExpansionCount=2),),
        monotonic() + 10,
    ).Receipts[0]
    Exact = Context.GenerateRouteTreesBatchOutcomesV1(
        "exact-found",
        (_coarse(MaximumExpansionCount=3),),
        monotonic() + 10,
    ).Receipts[0]

    assert Limited.SearchOutcome == "Incomplete"
    assert Limited.TerminalReason == "WorkCapExhausted"
    assert Exact.SearchOutcome == "Found"
    assert Exact.RouteExpansionCount == 3
    assert Exact.TotalExpansionCount == 3


def test_connected_relaxation_with_electrically_incomplete_detailed_search_is_not_proof():
    Nodes = [(0, 0, 0)]
    for Index in range(1, 20):
        X, Y, Z = Nodes[-1]
        Nodes.append((X + (Index % 2), Y, Z + ((Index + 1) % 2)))
    Edges = list(zip(Nodes, Nodes[1:]))
    Context = RustRouting.RoutingContext(
        (0, 0, 0, Nodes[-1][0], 0, Nodes[-1][2]),
        (0, 0, Nodes[-1][0], Nodes[-1][2]),
        Nodes,
        Edges,
    )
    Request = RustRouting.RouteTreeDetailedRequestV1(
        "connected-electrical-incomplete",
        _bindings(),
        (0, 0, 0, Nodes[-1][0], 0, Nodes[-1][2]),
        (0, 0, Nodes[-1][0], Nodes[-1][2]),
        False,
        [Nodes[0]],
        [[Nodes[-1]]],
        Nodes,
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        True,
        512,
    )

    Receipt = Context.GenerateRouteTreeDetailedBatchOutcomesV1(
        "connected-electrical-batch",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome == "Incomplete"
    assert Receipt.TerminalReason == "DetailedSearchIncomplete"
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_multisink_partial_route_cannot_coexist_with_scoped_disconnection_proof():
    D = (3, 0, 0)
    Context = RustRouting.RoutingContext(
        (0, 0, 0, 3, 0, 0),
        (0, 0, 3, 0),
        [
            (0, 0, 0),
            (1, 0, 0),
            (2, 0, 0),
            D,
        ],
        [((0, 0, 0), (1, 0, 0)), ((1, 0, 0), (2, 0, 0))],
    )
    Request = RustRouting.RouteTreeDetailedRequestV1(
        "multisink-partial",
        _bindings(),
        (0, 0, 0, 3, 0, 0),
        (0, 0, 3, 0),
        False,
        [(0, 0, 0)],
        [[(2, 0, 0)], [D]],
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), D],
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        False,
        64,
    )

    Receipt = Context.GenerateRouteTreeDetailedBatchOutcomesV1(
        "multisink-partial-batch",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome == "ProvenNoPath"
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof.UnreachableTargetBranchOrdinals == [1]
    assert Receipt.NoPathProof.UnreachableAttachmentNodes == [D]


def test_second_original_start_prevents_false_disconnection_proof():
    D = (3, 0, 0)
    Context = RustRouting.RoutingContext(
        (0, 0, 0, 3, 0, 0),
        (0, 0, 3, 0),
        [(0, 0, 0), (2, 0, 0), D],
        [((2, 0, 0), D)],
    )
    Request = RustRouting.RouteTreeDetailedRequestV1(
        "second-start",
        _bindings(),
        (0, 0, 0, 3, 0, 0),
        (0, 0, 3, 0),
        False,
        [(0, 0, 0), (2, 0, 0)],
        [[D]],
        [(0, 0, 0), (2, 0, 0), D],
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        False,
        64,
    )

    Receipt = Context.GenerateRouteTreeDetailedBatchOutcomesV1(
        "second-start-batch",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome != "ProvenNoPath"
    assert Receipt.NoPathProof is None


def test_only_semantically_relevant_blockage_changes_route_domain_and_proof():
    D = (0, 0, 1)
    Context = RustRouting.RoutingContext(
        (0, 0, 0, 2, 0, 1),
        (0, 0, 2, 1),
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), D],
        [((0, 0, 0), (1, 0, 0)), ((1, 0, 0), (2, 0, 0))],
    )
    Irrelevant = _coarse(
        AllowedColumns=((0, 0), (1, 0), (2, 0), (0, 1)),
        BlockedNodeValues=(D,),
    )
    Bridge = _coarse(BlockedNodeValues=((1, 0, 0),))

    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "blockage-pair",
        (Irrelevant, Bridge),
        monotonic() + 10,
    )

    assert Result.Receipts[0].SearchOutcome == "Found"
    assert Result.Receipts[1].SearchOutcome == "ProvenNoPath"
    assert (
        Result.Receipts[0].RouteDomainScopeSha256
        != Result.Receipts[1].RouteDomainScopeSha256
    )


def test_mixed_requests_share_exact_expired_cutoff_without_renewal():
    Cutoff = monotonic() - 1
    Result = _context().GenerateRouteTreesBatchOutcomesV1(
        "shared-expired-cutoff",
        (
            _coarse(RequestId="cancelled", CancellationRequestedBeforeStart=True),
            _coarse(RequestId="expired"),
        ),
        Cutoff,
    )

    assert [Receipt.RequestId for Receipt in Result.Receipts] == ["cancelled", "expired"]
    assert [Receipt.TerminalReason for Receipt in Result.Receipts] == [
        "DeadlineExhaustedAtEntry",
        "DeadlineExhaustedAtEntry",
    ]
    assert all(Receipt.OutcomePhase == "Entry" for Receipt in Result.Receipts)
    assert Result.Receipts[0].CancellationRequested is True
    assert Result.Receipts[0].CancellationAcknowledged is False
    assert all(Receipt.DeadlineAtMonotonicSeconds == Cutoff for Receipt in Result.Receipts)
    assert all(Receipt.Started is False for Receipt in Result.Receipts)
    assert all(Receipt.TotalExpansionCount == 0 for Receipt in Result.Receipts)


def test_semantically_equal_coarse_and_detailed_requests_share_route_domain_identity():
    Context = _context()
    Coarse = Context.GenerateRouteTreesBatchOutcomesV1(
        "coarse-equivalence",
        (_coarse(RequestId="coarse"),),
        monotonic() + 10,
    ).Receipts[0]
    Detailed = Context.GenerateRouteTreeDetailedBatchOutcomesV1(
        "detailed-equivalence",
        (
            RustRouting.RouteTreeDetailedRequestV1(
                "detailed",
                _bindings(),
                (0, 0, 0, 2, 0, 0),
                (0, 0, 2, 0),
                False,
                [(0, 0, 0)],
                [[(2, 0, 0)]],
                [(0, 0, 0), (1, 0, 0), (2, 0, 0)],
                [],
                [],
                [],
                0,
                0,
                0,
                0,
                False,
                32,
            ),
        ),
        monotonic() + 10,
    ).Receipts[0]

    assert Coarse.RouteDomainScopeCanonicalJson == Detailed.RouteDomainScopeCanonicalJson
    assert Coarse.RouteDomainScopeSha256 == Detailed.RouteDomainScopeSha256
    assert Coarse.SearchOutcome == Detailed.SearchOutcome == "Found"


def test_context_identity_is_invariant_to_node_and_edge_serialization_order():
    First = _context().GenerateRouteTreesBatchOutcomesV1(
        "context-order-first",
        (_coarse(),),
        monotonic() + 10,
    )
    Permuted = _context(
        Nodes=((2, 0, 0), (0, 0, 0), (1, 0, 0)),
        Edges=(((2, 0, 0), (1, 0, 0)), ((1, 0, 0), (0, 0, 0))),
    ).GenerateRouteTreesBatchOutcomesV1(
        "context-order-permuted",
        (_coarse(),),
        monotonic() + 10,
    )

    assert First.ContextGraphCanonicalJson == Permuted.ContextGraphCanonicalJson
    assert First.ContextGraphSha256 == Permuted.ContextGraphSha256


def test_ordered_start_roles_change_semantic_and_exact_input_identities():
    Context = _context()
    First = Context.GenerateRouteTreesBatchOutcomesV1(
        "ordered-starts-first",
        (_coarse(Starts=((0, 0, 0), (2, 0, 0))),),
        monotonic() + 10,
    ).Receipts[0]
    Reversed = Context.GenerateRouteTreesBatchOutcomesV1(
        "ordered-starts-reversed",
        (_coarse(Starts=((2, 0, 0), (0, 0, 0))),),
        monotonic() + 10,
    ).Receipts[0]

    assert First.RouteDomainScopeSha256 != Reversed.RouteDomainScopeSha256
    assert First.ImmutableInputSha256 != Reversed.ImmutableInputSha256


def test_cancellation_snapshot_is_immutable_and_not_live_polled():
    Request = _coarse(CancellationRequestedBeforeStart=False)
    with pytest.raises(AttributeError):
        Request.CancellationRequestedBeforeStart = True

    Receipt = _context().GenerateRouteTreesBatchOutcomesV1(
        "immutable-cancellation",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.CancellationRequested is False
    assert Receipt.TerminalReason != "Cancelled"


def test_blocked_first_attachment_is_unsupported_even_when_terminal_is_reachable():
    D = (1, 0, 1)
    Context = RustRouting.RoutingContext(
        (0, 0, 0, 2, 0, 1),
        (0, 0, 2, 1),
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), D],
        (
            ((0, 0, 0), D),
            (D, (2, 0, 0)),
            ((1, 0, 0), (2, 0, 0)),
        ),
    )
    Request = RustRouting.RouteTreeDetailedRequestV1(
        "blocked-attachment",
        _bindings(),
        (0, 0, 0, 2, 0, 1),
        (0, 0, 2, 1),
        False,
        [(0, 0, 0)],
        [[(1, 0, 0), (2, 0, 0)]],
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), D],
        [(1, 0, 0)],
        [],
        [],
        0,
        0,
        0,
        0,
        False,
        64,
    )

    Receipt = Context.GenerateRouteTreeDetailedBatchOutcomesV1(
        "blocked-attachment-batch",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome == "Incomplete"
    assert Receipt.TerminalReason == "UnsupportedRequest"
    assert Receipt.NoPathProof is None


def test_complete_multisink_candidate_validates_every_required_path():
    Left = (1, 0, 0)
    Right = (0, 0, 1)
    Context = RustRouting.RoutingContext(
        (0, 0, 0, 1, 0, 1),
        (0, 0, 1, 1),
        [(0, 0, 0), Left, Right],
        [((0, 0, 0), Left), ((0, 0, 0), Right)],
    )
    Request = RustRouting.RouteTreeDetailedRequestV1(
        "complete-multisink",
        _bindings(),
        (0, 0, 0, 1, 0, 1),
        (0, 0, 1, 1),
        False,
        [(0, 0, 0)],
        [[Left], [Right]],
        [(0, 0, 0), Left, Right],
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        False,
        64,
    )

    Receipt = Context.GenerateRouteTreeDetailedBatchOutcomesV1(
        "complete-multisink-batch",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome == "Found"
    assert len(Receipt.Candidate.TargetPaths) == 2
    assert {Target for Target, _Path in Receipt.Candidate.TargetPaths} == {Left, Right}

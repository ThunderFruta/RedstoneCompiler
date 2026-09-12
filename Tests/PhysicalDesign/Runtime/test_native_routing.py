"""Specification-first checks for typed native route-batch projection."""

from __future__ import annotations

from dataclasses import replace
from json import loads
from pathlib import Path
from types import SimpleNamespace
from time import monotonic

import pytest

from RedstoneCompiler import RustRouting
from PhysicalDesign.Runtime.NativeRouting import (
    ExecuteNativeRouteBatchOutcomesV1,
    NativeRouteResultKind,
    _AdaptNativeRouteBatchOutcomesV1,
    _ClassifyReceipt,
)
from PhysicalDesign.Contracts.Runtime import (
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeSearchOutcome,
)
from Tests.Native.test_route_batch_outcomes import (
    C,
    _coarse,
    _coarse_start_connection,
    _context,
    _detailed,
)


REAL_ONE_NAND_FIXTURE = (
    Path(__file__).parents[2] / "Fixtures" / "JointRealOneNandTargetlessR1b.json"
)


def test_adapter_preserves_each_native_terminal_semantic_and_actual_work():
    Requests = (
        _coarse(RequestId="routed"),
        _coarse(
            RequestId="scoped-no-path",
            AllowedColumns=((0, 0), (2, 0)),
            RequiredNodes=(C,),
        ),
        _coarse(RequestId="search-limit", MaximumExpansionCount=1),
        _coarse(
            RequestId="cancelled",
            CancellationRequestedBeforeStart=True,
        ),
        _coarse(RequestId="native-failure", Starts=((99, 0, 0),)),
    )

    Result = ExecuteNativeRouteBatchOutcomesV1(
        _context(),
        "typed-adapter-mixed",
        Requests,
        monotonic() + 10,
    )

    assert tuple(Value.RequestIdentity for Value in Result.Results) == tuple(
        Request.RequestId for Request in Requests
    )
    assert tuple(Value.Kind for Value in Result.Results) == (
        NativeRouteResultKind.Routed,
        NativeRouteResultKind.CompleteScopedNoPath,
        NativeRouteResultKind.SearchLimitIncomplete,
        NativeRouteResultKind.CancellationIncomplete,
        NativeRouteResultKind.NativeFailure,
    )
    assert Result.Results[0].Candidate is not None
    assert Result.Results[1].CompleteScopedNoPathProof.Complete is True
    assert Result.Results[1].CompleteScopedNoPathProof.ClaimScope == (
        "OneOriginalRouteRequest"
    )
    assert all(
        Value.Candidate is None and Value.CompleteScopedNoPathProof is None
        for Value in Result.Results[2:]
    )
    assert tuple(Value.Reason for Value in Result.Results[2:]) == (
        "WorkCapExhausted",
        "Cancelled",
        "UnsupportedRequest",
    )
    assert all(
        Value.ActualExpansionCount <= Value.ExpansionCap
        for Value in Result.Results
    )
    assert Result.AggregateExpansionCount == sum(
        Value.ActualExpansionCount for Value in Result.Results
    )


def test_adapter_preserves_deadline_as_incomplete_without_candidate_or_proof():
    Result = ExecuteNativeRouteBatchOutcomesV1(
        _context(),
        "typed-adapter-deadline",
        (_coarse(RequestId="deadline"),),
        monotonic() - 1,
    )

    Value = Result.Results[0]
    assert Value.Kind is NativeRouteResultKind.DeadlineIncomplete
    assert Value.Reason == "DeadlineExhaustedAtEntry"
    assert Value.ActualExpansionCount == 0
    assert Value.Candidate is None
    assert Value.CompleteScopedNoPathProof is None
    assert Result.DeadlineExceeded is True


def test_adapter_preserves_original_order_from_native_ordinals():
    Requests = (
        _coarse(RequestId="first"),
        _coarse(
            RequestId="second",
            AllowedColumns=((0, 0), (2, 0)),
            RequiredNodes=(C,),
        ),
        _coarse(RequestId="third", MaximumExpansionCount=1),
    )
    Native = _context().GenerateRouteTreesBatchOutcomesV1(
        "typed-adapter-reordered",
        Requests,
        monotonic() + 10,
    )
    Result = _AdaptNativeRouteBatchOutcomesV1(
        Native,
        Requests,
        Native.BatchIdentity,
        Native.DeadlineAtMonotonicSeconds,
        Native.ContextGraphSha256,
    )

    assert tuple(Value.OriginalOrdinal for Value in Result.Results) == (0, 1, 2)
    assert tuple(Value.RequestIdentity for Value in Result.Results) == (
        "first",
        "second",
        "third",
    )
    assert tuple(Value.NativeReceipt.OriginalOrdinal for Value in Result.Results) == (
        0,
        1,
        2,
    )


def test_adapter_projects_worker_failure_without_manufacturing_no_path():
    Receipt = SimpleNamespace(
        SearchOutcome="Incomplete",
        TerminalReason="WorkerFailure",
        NoPathProof=None,
        RuntimeSearchOutcome="Unresolved",
        RuntimeClaimStrength="Continuation",
        RuntimeCommitEligibility="Ineligible",
    )

    assert _ClassifyReceipt(Receipt) == (
        NativeRouteResultKind.NativeFailure,
        RuntimeSearchOutcome.Unresolved,
        RuntimeClaimStrength.Continuation,
        RuntimeCommitEligibility.Ineligible,
    )


def test_native_payload_identity_is_actual_and_not_the_logical_request_identity():
    Requests = (
        _coarse(RequestId="geometry-alpha"),
        _coarse(RequestId="geometry-beta"),
    )

    Result = ExecuteNativeRouteBatchOutcomesV1(
        _context(),
        "typed-adapter-payload-identity",
        Requests,
        monotonic() + 10,
    )

    First, Second = Result.Results
    assert First.RequestIdentity != Second.RequestIdentity
    assert First.NativePayloadIdentity == Second.NativePayloadIdentity
    assert First.NativePayloadCanonicalJson == Second.NativePayloadCanonicalJson
    assert First.ExecutionScope.NativeRequestPayloadIdentity == (
        First.NativePayloadIdentity
    )
    assert First.ExecutionScope.ContextGraphIdentity == (
        Result.NativeBatch.ContextGraphSha256
    )
    assert First.ExecutionScope.RouteDomainIdentity == (
        First.NativeReceipt.RouteDomainScopeSha256
    )


def test_detailed_adapter_uses_the_detailed_entrypoint_and_preserves_cap():
    Result = ExecuteNativeRouteBatchOutcomesV1(
        _context(),
        "typed-detailed-adapter",
        (
            _detailed(RequestId="detailed-routed"),
            _detailed(RequestId="detailed-limited", MaximumExpansionCount=1),
        ),
        monotonic() + 10,
        Detailed=True,
    )

    assert tuple(Value.RequestIdentity for Value in Result.Results) == (
        "detailed-routed",
        "detailed-limited",
    )
    assert Result.Results[0].Kind is NativeRouteResultKind.Routed
    assert Result.Results[1].Kind is NativeRouteResultKind.SearchLimitIncomplete
    assert Result.Results[1].ActualExpansionCount == 1
    assert Result.Results[1].ExpansionCap == 1


def test_adapter_preserves_explicit_start_connection_intent_and_rejects_substitution():
    DeadlineAt = monotonic() + 10
    Request = _coarse_start_connection(
        RequestId="start-connection",
        Starts=((0, 0, 0), (2, 0, 0)),
        RequiredNodes=((2, 0, 0),),
    )
    Context = _context()
    Result = ExecuteNativeRouteBatchOutcomesV1(
        Context,
        "typed-start-connection",
        (Request,),
        DeadlineAt,
    )

    Value = Result.Results[0]
    assert Value.Kind is NativeRouteResultKind.Routed
    assert Value.NativeReceipt.RequestKind == "CoarseStartConnectionV1"
    assert Value.NativeReceipt.Candidate.Nodes == [(0, 0, 0), (1, 0, 0), (2, 0, 0)]
    assert Value.NativeReceipt.Candidate.TargetPaths == []
    assert Value.CompleteScopedNoPathProof is None

    Native = Result.NativeBatch
    OrdinaryWithSameId = _coarse(
        RequestId="start-connection",
        TargetBranches=(),
        RequiredNodes=((2, 0, 0),),
    )
    with pytest.raises(ValueError, match="request kind mismatch"):
        _AdaptNativeRouteBatchOutcomesV1(
            Native,
            (OrdinaryWithSameId,),
            Native.BatchIdentity,
            Native.DeadlineAtMonotonicSeconds,
            Native.ContextGraphSha256,
        )


def test_real_joint_one_nand_targetless_request_matches_legacy_before_admission():
    Fixture = loads(REAL_ONE_NAND_FIXTURE.read_text())
    Record = Fixture["TargetlessRecord"]
    ContextDocument = Record["Context"]
    RequestDocument = Record["LegacyRequest"]
    Control = Record["TypedControl"]
    ExpectedTree = [tuple(Value) for Value in Record["LegacyBatch"]["RouteTree"]]
    DeadlineAt = monotonic() + 10
    Context = RustRouting.RoutingContext(
        tuple(ContextDocument["Bounds"]),
        tuple(ContextDocument["PlacementBounds"]),
        [tuple(Value) for Value in ContextDocument["Nodes"]],
        [
            (tuple(First), tuple(Second))
            for First, Second in ContextDocument["Edges"]
        ],
    )
    LegacyTuple = (
        [tuple(Value) for Value in RequestDocument["Starts"]],
        [],
        [tuple(Value) for Value in RequestDocument["AllowedColumns"]],
        [tuple(Value) for Value in RequestDocument["RequiredNodes"]],
        [tuple(Value) for Value in RequestDocument["BlockedNodes"]],
        [tuple(Value) for Value in RequestDocument["PreferredColumns"]],
        RequestDocument["PreferredRoutingY"],
        RequestDocument["GuidePenalty"],
        RequestDocument["BendPenalty"],
        RequestDocument["ViaPenalty"],
        RequestDocument["MaximumExpansionCount"],
    )
    assert Context.AuthoritativeContextGraphSha256 == (
        ContextDocument["AuthoritativeContextGraphSha256"]
    )
    Legacy = Context.GenerateRouteTreesBounded([LegacyTuple], 1_000)
    assert Legacy.CompletedWork == 1
    assert Legacy.DeadlineExceeded is False
    assert Legacy.RouteTrees == [ExpectedTree]

    Ordinary = RustRouting.RouteTreeCoarseRequestV1(
        Record["TypedRequest"]["RequestId"],
        [tuple(Value) for Value in Control["CallerEchoBindings"]],
        tuple(ContextDocument["Bounds"]),
        tuple(ContextDocument["PlacementBounds"]),
        False,
        *LegacyTuple,
    )
    assert Ordinary.NativePayloadSha256 == Record["TypedRequest"][
        "NativePayloadSha256"
    ]
    OrdinaryResult = ExecuteNativeRouteBatchOutcomesV1(
        Context,
        "joint-real-one-nand-ordinary-control-r1b",
        (Ordinary,),
        DeadlineAt,
    ).Results[0]
    assert OrdinaryResult.Kind is NativeRouteResultKind.NativeFailure
    assert OrdinaryResult.Reason == "UnsupportedRequest"

    Targetless = RustRouting.RouteTreeCoarseRequestV1.ConnectStartsOnlyV1(
        Record["TypedRequest"]["RequestId"],
        [tuple(Value) for Value in Control["CallerEchoBindings"]],
        tuple(ContextDocument["Bounds"]),
        tuple(ContextDocument["PlacementBounds"]),
        False,
        LegacyTuple[0],
        LegacyTuple[2],
        LegacyTuple[3],
        LegacyTuple[4],
        LegacyTuple[5],
        *LegacyTuple[6:],
    )
    Typed = ExecuteNativeRouteBatchOutcomesV1(
        Context,
        Control["BatchIdentity"],
        (Targetless,),
        DeadlineAt,
    ).Results[0]

    assert Targetless.ConnectionIntent == "ConnectStartsOnlyV1"
    assert Typed.Kind is NativeRouteResultKind.Routed
    assert Typed.NativeReceipt.Candidate.Nodes == ExpectedTree
    assert Typed.NativeReceipt.Candidate.TargetPaths == []
    assert Typed.NativeReceipt.NoPathProof is None
    assert Typed.ActualExpansionCount <= Typed.ExpansionCap == 75_000
    assert Typed.NativePayloadIdentity == Targetless.NativePayloadSha256
    assert Typed.NativePayloadIdentity != Ordinary.NativePayloadSha256
    assert Typed.ExecutionScope.ContextGraphIdentity == (
        ContextDocument["AuthoritativeContextGraphSha256"]
    )


def test_public_frozen_result_types_reject_unknown_or_mutable_shapes():
    Result = ExecuteNativeRouteBatchOutcomesV1(
        _context(),
        "typed-result-shapes",
        (_coarse(RequestId="shape"),),
        monotonic() + 10,
    )
    with pytest.raises(TypeError, match="NativeRouteResultKind"):
        replace(Result.Results[0], Kind="Routed")
    with pytest.raises(TypeError, match="exact tuple"):
        replace(Result, Results=list(Result.Results))


def test_execute_rejects_same_id_result_from_different_batch_or_cap():
    DeadlineAt = monotonic() + 10
    Context = _context()
    StaleRequest = _coarse(RequestId="same", MaximumExpansionCount=32)
    Stale = Context.GenerateRouteTreesBatchOutcomesV1(
        "stale-batch",
        (StaleRequest,),
        DeadlineAt,
    )

    class SubstitutingContext:
        AuthoritativeContextGraphSha256 = Context.AuthoritativeContextGraphSha256

        def GenerateRouteTreesBatchOutcomesV1(self, *_Arguments):
            return Stale

    with pytest.raises(TypeError, match="exact native RoutingContext"):
        ExecuteNativeRouteBatchOutcomesV1(
            SubstitutingContext(),
            "expected-batch",
            (_coarse(RequestId="same", MaximumExpansionCount=1),),
            DeadlineAt,
        )

    SameBatch = Context.GenerateRouteTreesBatchOutcomesV1(
        "same-batch",
        (StaleRequest,),
        DeadlineAt,
    )

    with pytest.raises(ValueError, match="expansion cap mismatch"):
        _AdaptNativeRouteBatchOutcomesV1(
            SameBatch,
            (_coarse(RequestId="same", MaximumExpansionCount=1),),
            "same-batch",
            DeadlineAt,
            Context.AuthoritativeContextGraphSha256,
        )


def test_execute_rejects_same_request_proof_from_another_context():
    DeadlineAt = monotonic() + 10
    Request = _coarse(
        RequestId="same-proof",
        AllowedColumns=((0, 0), (2, 0)),
        RequiredNodes=(C,),
    )
    SourceContext = _context()
    Stale = SourceContext.GenerateRouteTreesBatchOutcomesV1(
        "same-proof-batch",
        (Request,),
        DeadlineAt,
    )
    OtherContext = _context(Edges=())

    with pytest.raises(ValueError, match="context graph mismatch"):
        _AdaptNativeRouteBatchOutcomesV1(
            Stale,
            (Request,),
            "same-proof-batch",
            DeadlineAt,
            OtherContext.AuthoritativeContextGraphSha256,
        )


def test_adapter_rejects_contradictory_runtime_axes_and_non_native_batch_proxy():
    Contradictory = SimpleNamespace(
        SearchOutcome="Found",
        TerminalReason="Found",
        NoPathProof=None,
        RuntimeSearchOutcome="Unresolved",
        RuntimeClaimStrength="Continuation",
        RuntimeCommitEligibility="Eligible",
    )
    with pytest.raises(ValueError, match="Runtime outcome axes"):
        _ClassifyReceipt(Contradictory)

    Request = _coarse(RequestId="strict-deadline")
    Native = _context().GenerateRouteTreesBatchOutcomesV1(
        "strict-deadline",
        (Request,),
        monotonic() + 10,
    )
    Malformed = SimpleNamespace(
        ContractVersion=Native.ContractVersion,
        BatchIdentity=Native.BatchIdentity,
        DeadlineAtMonotonicSeconds=Native.DeadlineAtMonotonicSeconds,
        DeadlineExceeded="false",
        ContextGraphIdentityAvailability=Native.ContextGraphIdentityAvailability,
        ContextGraphCanonicalJson=Native.ContextGraphCanonicalJson,
        ContextGraphSha256=Native.ContextGraphSha256,
        TotalRequestCount=Native.TotalRequestCount,
        Receipts=Native.Receipts,
        AggregateExpansionCount=Native.AggregateExpansionCount,
    )
    with pytest.raises(TypeError, match="exact authoritative native batch"):
        _AdaptNativeRouteBatchOutcomesV1(
            Malformed,
            (Request,),
            Native.BatchIdentity,
            Native.DeadlineAtMonotonicSeconds,
            Native.ContextGraphSha256,
        )


def test_empty_batch_preserves_independent_future_and_expired_deadline_state():
    Future = ExecuteNativeRouteBatchOutcomesV1(
        _context(),
        "empty-future",
        (),
        monotonic() + 10,
    )
    Expired = ExecuteNativeRouteBatchOutcomesV1(
        _context(),
        "empty-expired",
        (),
        monotonic() - 1,
    )

    assert Future.Results == ()
    assert Future.DeadlineExceeded is False
    assert Expired.Results == ()
    assert Expired.DeadlineExceeded is True


def test_public_execution_scope_rejects_malformed_identity_and_deadline():
    Result = ExecuteNativeRouteBatchOutcomesV1(
        _context(),
        "typed-scope-shape",
        (_coarse(RequestId="scope"),),
        monotonic() + 10,
    )
    Scope = Result.Results[0].ExecutionScope
    with pytest.raises(TypeError, match="SHA-256 identity"):
        replace(Scope, ContextGraphIdentity="not-a-digest")
    with pytest.raises(TypeError, match="finite non-negative"):
        replace(Scope, DeadlineAtMonotonicSeconds=float("nan"))

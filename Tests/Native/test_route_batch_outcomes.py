"""Independent public-boundary checks for authoritative native route receipts."""

from __future__ import annotations

from collections import deque
from hashlib import sha256
from json import loads
from math import inf, nan
from time import monotonic, sleep

import pytest

from RedstoneCompiler import RustRouting


A = (0, 0, 0)
B = (1, 0, 0)
C = (2, 0, 0)


def _bindings(Suffix: str = "alpha") -> list[tuple[str, str]]:
    return [
        ("ModelIdentity", f"model-{Suffix}"),
        ("TechnologyIdentity", f"technology-{Suffix}"),
        ("ResourceIdentity", f"resource-{Suffix}"),
        ("PlacementIdentity", f"placement-{Suffix}"),
        ("SelectedAccessIdentity", f"access-{Suffix}"),
        ("PolicyIdentity", f"policy-{Suffix}"),
        ("DependencySnapshotIdentity", f"dependency-{Suffix}"),
    ]


def _context(Edges=((A, B), (B, C)), Nodes=(A, B, C)):
    return RustRouting.RoutingContext(
        (0, 0, 0, 2, 0, 0),
        (0, 0, 2, 0),
        list(Nodes),
        list(Edges),
    )


def _coarse(
    RequestId="coarse-alpha",
    MaximumExpansionCount=32,
    AllowedColumns=((0, 0), (1, 0), (2, 0)),
    RequiredNodes=(),
    BlockedNodeValues=(),
    CancellationRequestedBeforeStart=False,
    Starts=(A,),
    TargetBranches=((C,),),
    Bindings=None,
    PreferredColumns=(),
):
    return RustRouting.RouteTreeCoarseRequestV1(
        RequestId,
        _bindings() if Bindings is None else Bindings,
        (0, 0, 0, 2, 0, 0),
        (0, 0, 2, 0),
        CancellationRequestedBeforeStart,
        list(Starts),
        [list(Branch) for Branch in TargetBranches],
        list(AllowedColumns),
        list(RequiredNodes),
        list(BlockedNodeValues),
        list(PreferredColumns),
        0,
        0,
        0,
        0,
        MaximumExpansionCount,
    )


def _detailed(
    RequestId="detailed-alpha",
    MaximumExpansionCount=32,
    AllowedNodes=(A, B, C),
    CancellationRequestedBeforeStart=False,
    Starts=(A,),
    TargetBranches=((C,),),
    BlockedNodeValues=(),
    PreferredColumns=(),
    NodeCostValues=(),
):
    return RustRouting.RouteTreeDetailedRequestV1(
        RequestId,
        _bindings(),
        (0, 0, 0, 2, 0, 0),
        (0, 0, 2, 0),
        CancellationRequestedBeforeStart,
        list(Starts),
        [list(Branch) for Branch in TargetBranches],
        list(AllowedNodes),
        list(BlockedNodeValues),
        list(PreferredColumns),
        list(NodeCostValues),
        0,
        0,
        0,
        0,
        False,
        MaximumExpansionCount,
    )


def _reachable(Nodes, Edges, Starts):
    Adjacency = {Node: set() for Node in Nodes}
    for First, Second in Edges:
        Adjacency[First].add(Second)
        Adjacency[Second].add(First)
    Reached = set(Starts)
    Pending = deque(sorted(Reached))
    while Pending:
        Current = Pending.popleft()
        for Neighbor in sorted(Adjacency[Current]):
            if Neighbor not in Reached:
                Reached.add(Neighbor)
                Pending.append(Neighbor)
    return Reached


def _assert_valid_route(Receipt, Edges, Starts, Terminal):
    Candidate = Receipt.Candidate
    assert Candidate is not None
    NormalizedEdges = {frozenset(Edge) for Edge in Edges}
    Paths = [Path for Target, Path in Candidate.TargetPaths if Target == Terminal]
    assert Paths
    assert any(
        Path[0] in Starts
        and Path[-1] == Terminal
        and all(frozenset(Pair) in NormalizedEdges for Pair in zip(Path, Path[1:]))
        for Path in Paths
    )


@pytest.mark.parametrize("Detailed", (False, True))
def test_connected_cap_boundary_distinguishes_found_from_exhaustion(Detailed):
    Context = _context()
    Method = (
        Context.GenerateRouteTreeDetailedBatchOutcomesV1
        if Detailed
        else Context.GenerateRouteTreesBatchOutcomesV1
    )
    Factory = _detailed if Detailed else _coarse

    Limited = Method("batch-cap-1", (Factory(MaximumExpansionCount=1),), monotonic() + 10)
    Found = Method("batch-cap-32", (Factory(MaximumExpansionCount=32),), monotonic() + 10)

    assert _reachable((A, B, C), ((A, B), (B, C)), (A,)) == {A, B, C}
    assert Limited.Receipts[0].SearchOutcome == "Incomplete"
    assert Limited.Receipts[0].TerminalReason == "WorkCapExhausted"
    assert Limited.Receipts[0].NoPathProof is None
    assert Limited.Receipts[0].TotalExpansionCount <= 1
    assert Found.Receipts[0].SearchOutcome == "Found"
    assert Found.Receipts[0].RuntimeSearchOutcome == "Prepared"
    assert Found.Receipts[0].RuntimeClaimStrength == "Candidate"
    assert Found.Receipts[0].RuntimeCommitEligibility == "Ineligible"
    _assert_valid_route(Found.Receipts[0], ((A, B), (B, C)), (A,), C)


@pytest.mark.parametrize("Detailed", (False, True))
def test_disconnected_domain_requires_complete_relaxed_proof(Detailed):
    Context = _context(Edges=())
    Method = (
        Context.GenerateRouteTreeDetailedBatchOutcomesV1
        if Detailed
        else Context.GenerateRouteTreesBatchOutcomesV1
    )
    Factory = _detailed if Detailed else _coarse

    Result = Method("batch-disconnected", (Factory(),), monotonic() + 10)
    Receipt = Result.Receipts[0]

    assert C not in _reachable((A, B, C), (), (A,))
    assert Receipt.SearchOutcome == "ProvenNoPath"
    assert Receipt.TerminalReason == "RelaxedGraphDisconnected"
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof.Complete is True
    assert Receipt.NoPathProof.UnreachableTargetBranchOrdinals == [0]
    assert Receipt.NoPathProof.UnreachableAttachmentNodes == [C]
    assert Receipt.RuntimeSearchOutcome == "Infeasible"
    assert Receipt.RuntimeClaimStrength == "InfeasibilityProof"
    assert Receipt.RuntimeCommitEligibility == "Ineligible"


def test_coarse_required_node_outside_allowed_columns_is_not_dropped():
    Context = _context()
    Request = _coarse(AllowedColumns=((0, 0), (1, 0)), RequiredNodes=(C,))

    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-required-node",
        (Request,),
        monotonic() + 10,
    )

    assert Result.Receipts[0].SearchOutcome == "Found"
    Scope = loads(Result.Receipts[0].RouteDomainScopeCanonicalJson)
    assert list(C) in Scope[4]
    _assert_valid_route(Result.Receipts[0], ((A, B), (B, C)), (A,), C)


def test_coarse_restricted_domain_proof_does_not_expand_to_wider_graph():
    D = (0, 0, 1)
    Context = _context(Edges=((A, D), (D, C)), Nodes=(A, C, D))
    Request = _coarse(
        AllowedColumns=((0, 0),),
        RequiredNodes=(C,),
        Starts=(A,),
        TargetBranches=((C,),),
    )

    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-restricted-domain",
        (Request,),
        monotonic() + 10,
    )

    assert C in _reachable((A, C, D), ((A, D), (D, C)), (A,))
    Receipt = Result.Receipts[0]
    assert Receipt.SearchOutcome == "ProvenNoPath"
    Scope = loads(Receipt.RouteDomainScopeCanonicalJson)
    assert list(D) not in Scope[4]
    assert Receipt.NoPathProof.ClaimScope == "OneOriginalRouteRequest"


def test_zero_cap_and_predispatch_cancellation_do_no_work():
    Context = _context()
    Zero = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-zero",
        (_coarse(MaximumExpansionCount=0),),
        monotonic() + 10,
    ).Receipts[0]
    Cancelled = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-cancelled",
        (_coarse(CancellationRequestedBeforeStart=True),),
        monotonic() + 10,
    ).Receipts[0]

    assert (Zero.Started, Zero.TerminalReason, Zero.TotalExpansionCount) == (
        False,
        "WorkCapExhausted",
        0,
    )
    assert Cancelled.Started is False
    assert Cancelled.TerminalReason == "Cancelled"
    assert Cancelled.TotalExpansionCount == 0
    assert Cancelled.CancellationRequested is True
    assert Cancelled.CancellationAcknowledged is True
    assert Cancelled.SearchStopped is True
    assert Cancelled.CleanupDisposition == "NotApplicableNoDispatch"


def test_duplicate_ids_preserve_original_ordinals_and_aggregate_actual_work():
    Context = _context()
    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-duplicates",
        (_coarse(RequestId="duplicate"), _coarse(RequestId="duplicate"),),
        monotonic() + 10,
    )

    assert [Receipt.RequestId for Receipt in Result.Receipts] == ["duplicate", "duplicate"]
    assert [Receipt.OriginalOrdinal for Receipt in Result.Receipts] == [0, 1]
    assert Result.AggregateRouteExpansionCount == sum(
        Receipt.RouteExpansionCount for Receipt in Result.Receipts
    )
    assert Result.AggregateProofExpansionCount == sum(
        Receipt.ProofExpansionCount for Receipt in Result.Receipts
    )
    assert Result.AggregateExpansionCount == sum(
        Receipt.TotalExpansionCount for Receipt in Result.Receipts
    )


def test_proof_frontier_exhaustion_on_final_admitted_unit_wins_exact_cap():
    Context = _context(Edges=())

    OneShort = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-short-proof-cap",
        (_coarse(MaximumExpansionCount=1),),
        monotonic() + 10,
    ).Receipts[0]
    Receipt = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-exact-proof-cap",
        (_coarse(MaximumExpansionCount=2),),
        monotonic() + 10,
    ).Receipts[0]

    assert OneShort.SearchOutcome == "Incomplete"
    assert OneShort.TerminalReason == "WorkCapExhausted"
    assert OneShort.RouteExpansionCount == 1
    assert OneShort.ProofExpansionCount == 0
    assert OneShort.NoPathProof is None
    assert Receipt.SearchOutcome == "ProvenNoPath"
    assert Receipt.RouteExpansionCount == 1
    assert Receipt.ProofExpansionCount == 1
    assert Receipt.TotalExpansionCount == 2


def test_mixed_batch_retains_every_original_receipt_and_independent_reason():
    Context = _context()
    Requests = (
        _coarse(RequestId="found", MaximumExpansionCount=32),
        _coarse(
            RequestId="proved",
            MaximumExpansionCount=32,
            AllowedColumns=((0, 0), (2, 0)),
        ),
        _coarse(RequestId="limited", MaximumExpansionCount=1),
        _coarse(RequestId="cancelled", CancellationRequestedBeforeStart=True),
    )

    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-mixed",
        Requests,
        monotonic() + 10,
    )

    assert [Receipt.RequestId for Receipt in Result.Receipts] == [
        "found",
        "proved",
        "limited",
        "cancelled",
    ]
    assert [Receipt.OriginalOrdinal for Receipt in Result.Receipts] == [0, 1, 2, 3]
    assert [Receipt.SearchOutcome for Receipt in Result.Receipts] == [
        "Found",
        "ProvenNoPath",
        "Incomplete",
        "Incomplete",
    ]
    assert [Receipt.TerminalReason for Receipt in Result.Receipts] == [
        "Found",
        "RelaxedGraphDisconnected",
        "WorkCapExhausted",
        "Cancelled",
    ]
    assert Result.SettledReceiptCount == Result.TotalRequestCount == 4
    assert (Result.FoundCount, Result.ProvenNoPathCount, Result.IncompleteCount) == (
        1,
        1,
        2,
    )


def test_native_canonical_scope_hashes_match_standard_sha256_and_are_immutable():
    Context = _context()
    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-hashes",
        (_coarse(),),
        monotonic() + 10,
    )
    Receipt = Result.Receipts[0]

    assert Result.ContextGraphSha256 == sha256(
        Result.ContextGraphCanonicalJson.encode()
    ).hexdigest()
    assert Receipt.RouteDomainScopeSha256 == sha256(
        Receipt.RouteDomainScopeCanonicalJson.encode()
    ).hexdigest()
    assert Receipt.CallerEchoScopeSha256 == sha256(
        Receipt.CallerEchoScopeCanonicalJson.encode()
    ).hexdigest()
    with pytest.raises(AttributeError):
        Receipt.SearchOutcome = "ProvenNoPath"


def test_role_order_is_bound_while_set_like_column_order_is_normalized():
    Context = _context()
    First = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-order-first",
        (_coarse(AllowedColumns=((2, 0), (0, 0), (1, 0))),),
        monotonic() + 10,
    ).Receipts[0]
    NormalizedPermutation = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-order-normalized",
        (_coarse(AllowedColumns=((0, 0), (1, 0), (2, 0))),),
        monotonic() + 10,
    ).Receipts[0]
    ReversedRoles = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-order-roles",
        (_coarse(Starts=(C,), TargetBranches=((A,),)),),
        monotonic() + 10,
    ).Receipts[0]

    assert First.RouteDomainScopeSha256 == NormalizedPermutation.RouteDomainScopeSha256
    assert First.RouteDomainScopeSha256 != ReversedRoles.RouteDomainScopeSha256


def test_caller_echo_identity_changes_without_becoming_producer_validation():
    Context = _context()
    Alpha = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-echo-alpha",
        (_coarse(Bindings=_bindings("alpha")),),
        monotonic() + 10,
    ).Receipts[0]
    Beta = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-echo-beta",
        (_coarse(Bindings=_bindings("beta")),),
        monotonic() + 10,
    ).Receipts[0]

    assert Alpha.RouteDomainScopeSha256 == Beta.RouteDomainScopeSha256
    assert Alpha.CallerEchoScopeSha256 != Beta.CallerEchoScopeSha256
    assert Alpha.ImmutableInputSha256 != Beta.ImmutableInputSha256
    assert Alpha.ReceiptScopeSha256 != Beta.ReceiptScopeSha256
    assert "caller-echo" in loads(Alpha.CallerEchoScopeCanonicalJson)[0]


def test_absolute_deadline_expired_before_entry_starts_no_request():
    Context = _context()
    Cutoff = monotonic() + 0.002
    sleep(0.01)

    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-expired-before-entry",
        (_coarse(),),
        Cutoff,
    )

    Receipt = Result.Receipts[0]
    assert Result.BoundaryMonotonicSampleSeconds >= Cutoff
    assert Result.NativeRemainingNanoseconds == 0
    assert Receipt.DeadlineAtMonotonicSeconds == Cutoff
    assert Receipt.BoundaryMonotonicSampleSeconds == Result.BoundaryMonotonicSampleSeconds
    assert Receipt.NativeRemainingNanoseconds == 0
    assert Receipt.Started is False
    assert Receipt.TerminalReason == "DeadlineExhaustedAtEntry"
    assert Receipt.OutcomePhase == "Entry"
    assert Receipt.TotalExpansionCount == 0


@pytest.mark.parametrize("RemainingSeconds", (0.000_5, 0.050_000_5))
def test_absolute_deadline_bridge_rounds_down_without_renewal(RemainingSeconds):
    Context = _context()
    Before = monotonic()
    Cutoff = Before + RemainingSeconds
    Result = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-absolute-cutoff",
        (),
        Cutoff,
    )
    After = monotonic()

    assert Before <= Result.BoundaryMonotonicSampleSeconds <= After
    ExactRemaining = max(Cutoff - Result.BoundaryMonotonicSampleSeconds, 0.0)
    assert Result.NativeRemainingNanoseconds / 1_000_000_000 <= ExactRemaining
    assert Result.DeadlineAtMonotonicSeconds == Cutoff


@pytest.mark.parametrize("Cutoff", (-1.0, inf, nan, 1e300))
def test_absolute_deadline_rejects_invalid_values(Cutoff):
    with pytest.raises(ValueError):
        _context().GenerateRouteTreesBatchOutcomesV1("batch-invalid-deadline", (), Cutoff)


def test_semantic_invalid_slot_is_unstarted_nonproof_receipt():
    Context = _context()
    Request = _coarse(Starts=((99, 0, 0),))

    Receipt = Context.GenerateRouteTreesBatchOutcomesV1(
        "batch-unsupported",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome == "Incomplete"
    assert Receipt.TerminalReason == "UnsupportedRequest"
    assert Receipt.Started is False
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None
    assert Receipt.RuntimeSearchOutcome == "Unresolved"
    assert Receipt.RuntimeClaimStrength == "Continuation"
    assert Receipt.RuntimeCommitEligibility == "Ineligible"
    assert Receipt.DeadlineAtMonotonicSeconds > 0.0
    RawScope = loads(Receipt.ImmutableInputCanonicalJson)
    assert RawScope[0] == "raw-native-coarse-route-request-v1"
    assert RawScope[2] == [[99, 0, 0]]


@pytest.mark.parametrize(
    "Request",
    (
        _coarse(RequestId="empty-branch", TargetBranches=((),)),
        _coarse(RequestId="missing-branch-edge", TargetBranches=((A, C),)),
        _coarse(RequestId="foreign-required", RequiredNodes=((99, 0, 0),)),
    ),
)
def test_semantic_invalid_members_are_not_silently_filtered(Request):
    Receipt = _context().GenerateRouteTreesBatchOutcomesV1(
        "invalid-member-batch",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.RequestId in {
        "empty-branch",
        "missing-branch-edge",
        "foreign-required",
    }
    assert Receipt.SearchOutcome == "Incomplete"
    assert Receipt.TerminalReason == "UnsupportedRequest"
    assert Receipt.Started is False
    assert Receipt.TotalExpansionCount == 0
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_whole_envelope_type_failure_raises_instead_of_dropping_a_slot():
    with pytest.raises(TypeError):
        _context().GenerateRouteTreesBatchOutcomesV1(
            "malformed-envelope",
            (_coarse(), object(),),
            monotonic() + 10,
        )

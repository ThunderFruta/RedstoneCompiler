"""Specification-first public coarse repair checks with a coordinate-only oracle."""

from __future__ import annotations

from collections import deque
from hashlib import sha256
from json import loads
from time import monotonic

import pytest

from RedstoneCompiler import RustRouting


ROOT = (0, 1, 1)
SECOND_START = (1, 1, 1)
STARTS = (ROOT, SECOND_START)
BRANCH = ((-2, 1, -1), (-1, 1, -1), (0, 1, -1))
# The short path's own steps are legal. Its induced edge from (0, 2, 0)
# to BRANCH[-1] needs air where (0, 3, -1) needs its support.
SHORT_PATH = (
    ROOT,
    (0, 2, 0),
    (0, 3, -1),
    (0, 2, -2),
    (-1, 2, -2),
    (-2, 2, -2),
    *BRANCH,
)
FLAT_PATH = (
    ROOT,
    *((X, 1, 1) for X in range(-1, -7, -1)),
    (-6, 1, 0),
    *((X, 1, -1) for X in range(-6, -1)),
    *BRANCH[1:],
)
NODES = tuple(sorted(set(SHORT_PATH + FLAT_PATH + STARTS)))
EDGES = tuple(
    sorted(
        {
            tuple(sorted((A, B)))
            for Path in (
                SHORT_PATH,
                FLAT_PATH,
                STARTS,
                (BRANCH[-1], SHORT_PATH[1]),
            )
            for A, B in zip(Path, Path[1:])
        }
    )
)
BOUNDS = (-6, 1, -2, 1, 3, 1)
PLACEMENT_BOUNDS = (-6, -2, 1, 1)
BINDINGS = [
    (Name, f"independent-repair-{Name}")
    for Name in (
        "ModelIdentity",
        "TechnologyIdentity",
        "ResourceIdentity",
        "PlacementIdentity",
        "SelectedAccessIdentity",
        "PolicyIdentity",
        "DependencySnapshotIdentity",
    )
]


def _context(Nodes=NODES, Edges=EDGES):
    return RustRouting.RoutingContext(
        BOUNDS, PLACEMENT_BOUNDS, list(Nodes), list(Edges)
    )


def _request(
    Cap=1_000,
    Blocked=(),
    Allowed=NODES,
    Starts=STARTS,
    Branches=(BRANCH,),
    Cancelled=False,
    Detailed=False,
):
    Common = (
        "independent-coarse-repair",
        BINDINGS,
        BOUNDS,
        PLACEMENT_BOUNDS,
        Cancelled,
        list(Starts),
        [list(Branch) for Branch in Branches],
    )
    if Detailed:
        return RustRouting.RouteTreeDetailedRequestV1(
            *Common, list(Allowed), list(Blocked), [], [], 1, 0, 0, 0, False, Cap
        )
    return RustRouting.RouteTreeCoarseRequestV1(
        *Common,
        sorted({(X, Z) for X, _Y, Z in Allowed}),
        [],
        list(Blocked),
        [],
        1,
        0,
        0,
        0,
        Cap,
    )


def _run(Context=None, Deadline=None, Detailed=False, **RequestOptions):
    Context = _context() if Context is None else Context
    Deadline = monotonic() + 10 if Deadline is None else Deadline
    Method = (
        Context.GenerateRouteTreeDetailedBatchOutcomesV1
        if Detailed
        else Context.GenerateRouteTreesBatchOutcomesV1
    )
    Result = Method(
        "independent-coarse-repair-batch",
        (_request(Detailed=Detailed, **RequestOptions),),
        Deadline,
    )
    Receipt = Result.Receipts[0]
    assert Result.DeadlineAtMonotonicSeconds == Deadline
    assert Receipt.DeadlineAtMonotonicSeconds == Deadline
    assert Receipt.TotalExpansionCount == (
        Receipt.RouteExpansionCount + Receipt.ProofExpansionCount
    )
    assert 0 <= Receipt.TotalExpansionCount <= Receipt.MaximumExpansionCount
    assert Result.AggregateExpansionCount == Receipt.TotalExpansionCount
    return Result, Receipt


def _adjacency(Nodes, Edges):
    Adjacency = {Node: set() for Node in Nodes}
    for A, B in Edges:
        if A in Adjacency and B in Adjacency:
            Adjacency[A].add(B)
            Adjacency[B].add(A)
    return Adjacency


def _reachable(Nodes, Edges, Starts):
    Adjacency = _adjacency(Nodes, Edges)
    Seen = set(Starts) & set(Adjacency)
    Queue = deque(Seen)
    while Queue:
        for Neighbor in Adjacency[Queue.popleft()] - Seen:
            Seen.add(Neighbor)
            Queue.append(Neighbor)
    return Seen


def _ordered_paths(Nodes=NODES, Edges=EDGES, Blocked=()):
    """Enumerate the tiny graph, reserving the branch as an ordered suffix."""
    Adjacency = _adjacency(set(Nodes) - set(Blocked), Edges)
    Interior = set(BRANCH[1:])
    Pending = [(ROOT,)] if ROOT in Adjacency else []
    Paths = []
    while Pending:
        Path = Pending.pop()
        if Path[-1] == BRANCH[0]:
            if all(
                A in Adjacency and B in Adjacency[A]
                for A, B in zip(BRANCH, BRANCH[1:])
            ):
                Paths.append(Path + BRANCH[1:])
            continue
        Pending.extend(
            Path + (Neighbor,)
            for Neighbor in sorted(Adjacency[Path[-1]])
            if Neighbor not in Path and Neighbor not in Interior
        )
    return Paths


def _claim_conflicts(Nodes, Edges=EDGES):
    """Derive literal dust/support/air claims, including every induced edge."""
    Wires = set(Nodes)
    Supports = {(X, Y - 1, Z) for X, Y, Z in Wires}
    Air = set()
    for A, B in Edges:
        if A in Wires and B in Wires and A[1] != B[1]:
            assert abs(A[1] - B[1]) == 1
            Lower = min((A, B), key=lambda Node: Node[1])
            Air.add((Lower[0], Lower[1] + 1, Lower[2]))
    return {
        "wire-support": Wires & Supports,
        "wire-air": Wires & Air,
        "support-air": Supports & Air,
    }


def _assert_legal_candidate(
    Receipt, Nodes=NODES, Edges=EDGES, Starts=STARTS, Branches=(BRANCH,), Blocked=()
):
    assert Receipt.SearchOutcome == "Found"
    assert Receipt.RuntimeSearchOutcome == "Prepared"
    assert Receipt.RuntimeClaimStrength == "Candidate"
    assert Receipt.RuntimeCommitEligibility == "Ineligible"
    assert Receipt.NoPathProof is None
    Candidate = Receipt.Candidate
    Wires = set(Candidate.Nodes)
    assert set(Starts) <= Wires <= set(Nodes) - set(Blocked)
    assert _reachable(Wires, Edges, (Starts[0],)) == Wires
    assert not any(_claim_conflicts(Wires, Edges).values())
    EdgeSet = {frozenset(Edge) for Edge in Edges}
    assert len(Candidate.TargetPaths) == len(Branches)
    for Branch in Branches:
        Matching = [
            Path
            for Terminal, Path in Candidate.TargetPaths
            if Terminal == Branch[-1]
        ]
        assert len(Matching) == 1
        Path = Matching[0]
        assert Path[0] in Starts
        assert tuple(Path[-len(Branch):]) == Branch
        assert not (set(Path[:-len(Branch)]) & set(Branch[1:]))
        assert set(Path) <= Wires
        assert all(frozenset(Pair) in EdgeSet for Pair in zip(Path, Path[1:]))


def _assert_exact_scope(Result, Receipt, Allowed=NODES, Blocked=()):
    Graph = loads(Result.ContextGraphCanonicalJson)
    assert Graph == [
        "native-route-context-v1",
        [list(Node) for Node in NODES],
        [[list(A), list(B)] for A, B in EDGES],
    ]
    assert Result.ContextGraphSha256 == sha256(
        Result.ContextGraphCanonicalJson.encode()
    ).hexdigest()
    Scope = loads(Receipt.RouteDomainScopeCanonicalJson)
    assert Scope[1] == [list(Node) for Node in STARTS]
    assert Scope[2] == [[list(Node) for Node in BRANCH]]
    assert Scope[4] == [list(Node) for Node in sorted(Allowed)]
    assert Scope[5] == [list(Node) for Node in sorted(Blocked)]
    assert Receipt.RouteDomainScopeSha256 == sha256(
        Receipt.RouteDomainScopeCanonicalJson.encode()
    ).hexdigest()
    assert Receipt.ContextGraphSha256 == Result.ContextGraphSha256


def test_independent_oracle_identifies_induced_air_conflict_and_legal_alternative():
    assert all(
        abs(A[0] - B[0]) + abs(A[2] - B[2]) == 1 and abs(A[1] - B[1]) <= 1
        for A, B in EDGES
    )
    Paths = _ordered_paths()
    assert set(Paths) == {SHORT_PATH, FLAT_PATH}
    assert len(SHORT_PATH) < len(FLAT_PATH)
    assert _claim_conflicts(SHORT_PATH + STARTS) == {
        "wire-support": set(),
        "wire-air": set(),
        "support-air": {(0, 2, -1)},
    }
    assert not any(_claim_conflicts(FLAT_PATH + STARTS).values())
    # Checking just the chosen step sequence misses the induced-edge failure.
    assert not any(
        _claim_conflicts(
            SHORT_PATH + STARTS, tuple(zip(SHORT_PATH, SHORT_PATH[1:]))
        ).values()
    )


def test_coarse_repairs_self_conflicting_candidate_without_losing_order_or_starts():
    Result, Receipt = _run()

    _assert_exact_scope(Result, Receipt)
    _assert_legal_candidate(Receipt)
    assert set(Receipt.Candidate.Nodes) == set(FLAT_PATH + STARTS)


@pytest.mark.parametrize("Cap", (0, 1, 8, 16, 24, 32, 64))
def test_repair_never_renews_cumulative_expansion_cap_or_publishes_bad_geometry(Cap):
    _Result, Receipt = _run(Cap=Cap)

    assert Receipt.MaximumExpansionCount == Cap
    if Receipt.SearchOutcome == "Found":
        _assert_legal_candidate(Receipt)
    else:
        assert Receipt.SearchOutcome == "Incomplete"
        assert Receipt.TerminalReason == "WorkCapExhausted"
        assert Receipt.RuntimeSearchOutcome == "Unresolved"
        assert Receipt.RuntimeClaimStrength == "Continuation"
        assert Receipt.Candidate is None
        assert Receipt.NoPathProof is None
        assert Receipt.TotalExpansionCount == Cap
    if Cap <= 1:
        assert Receipt.SearchOutcome == "Incomplete"


@pytest.mark.parametrize("Restriction", ("blocked", "outside-domain"))
def test_unavailable_legal_alternative_is_not_a_false_disconnect_proof(Restriction):
    Missing = (-6, 1, 0)
    Blocked = (Missing,) if Restriction == "blocked" else ()
    Allowed = tuple(
        Node for Node in NODES if Restriction == "blocked" or Node != Missing
    )
    assert _ordered_paths(Allowed, EDGES, Blocked) == [SHORT_PATH]
    assert BRANCH[0] in _reachable(set(Allowed) - set(Blocked), EDGES, STARTS)
    Result, Receipt = _run(Allowed=Allowed, Blocked=Blocked)

    _assert_exact_scope(Result, Receipt, Allowed, Blocked)
    assert Receipt.SearchOutcome == "Incomplete"
    assert Receipt.RuntimeSearchOutcome == "Unresolved"
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_expired_absolute_deadline_prevents_repair_even_with_no_work_remaining():
    _Result, Receipt = _run(Cap=0, Deadline=monotonic() - 1)

    assert Receipt.TerminalReason == "DeadlineExhaustedAtEntry"
    assert Receipt.SearchOutcome == "Incomplete"
    assert Receipt.RuntimeSearchOutcome == "Unresolved"
    assert Receipt.Started is False
    assert Receipt.TotalExpansionCount == 0
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_cancellation_before_admission_performs_no_initial_or_repair_search():
    _Result, Receipt = _run(Cancelled=True)

    assert Receipt.TerminalReason == "Cancelled"
    assert Receipt.Started is False
    assert Receipt.CancellationAcknowledged is True
    assert Receipt.TotalExpansionCount == 0
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_already_legal_candidate_does_not_pay_for_a_second_search():
    Nodes = tuple(sorted(set(FLAT_PATH + STARTS)))
    Edges = tuple(Edge for Edge in EDGES if set(Edge) <= set(Nodes))
    Context = _context(Nodes, Edges)
    _DetailedResult, Detailed = _run(Context=Context, Allowed=Nodes, Detailed=True)
    _Result, Coarse = _run(Context=Context, Allowed=Nodes)

    _assert_legal_candidate(Detailed, Nodes, Edges)
    _assert_legal_candidate(Coarse, Nodes, Edges)
    assert Coarse.RouteExpansionCount == Detailed.RouteExpansionCount > 0
    assert Coarse.ProofExpansionCount == Detailed.ProofExpansionCount == 0


def test_detailed_search_cannot_attach_through_ordered_branch_interior():
    # ROOT reaches the terminal, but the attachment has no approach outside the
    # frozen suffix. Walking the suffix backwards cannot satisfy its direction.
    Approach = (0, 1, 0)
    Nodes = tuple(sorted({ROOT, Approach, *BRANCH}))
    Edges = (
        (ROOT, Approach),
        (Approach, BRANCH[-1]),
        *tuple(zip(BRANCH, BRANCH[1:])),
    )
    assert BRANCH[0] in _reachable(Nodes, Edges, (ROOT,))
    assert _ordered_paths(Nodes, Edges) == []
    _Result, Receipt = _run(
        Context=_context(Nodes, Edges), Allowed=Nodes, Starts=(ROOT,), Detailed=True
    )

    assert Receipt.SearchOutcome == "Incomplete"
    assert Receipt.RuntimeSearchOutcome == "Unresolved"
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_completed_repair_replays_at_exact_cumulative_grant_and_not_one_less():
    _Result, Completed = _run()
    _assert_legal_candidate(Completed)
    Work = Completed.TotalExpansionCount
    assert Work > 0
    _Result, Exact = _run(Cap=Work)
    _Result, OneShort = _run(Cap=Work - 1)

    _assert_legal_candidate(Exact)
    assert Exact.TotalExpansionCount == Work
    assert OneShort.SearchOutcome == "Incomplete"
    assert OneShort.TerminalReason == "WorkCapExhausted"
    assert OneShort.TotalExpansionCount == Work - 1
    assert OneShort.Candidate is None
    assert OneShort.NoPathProof is None


def test_repair_preserves_every_ordered_fanout_branch():
    OtherBranch = ((-1, 1, 1), (-2, 1, 1))
    Branches = (BRANCH, OtherBranch)
    _Result, Receipt = _run(Branches=Branches)

    _assert_legal_candidate(Receipt, Branches=Branches)

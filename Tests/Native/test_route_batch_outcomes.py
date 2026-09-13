"""Independent public-boundary checks for authoritative native route receipts."""

from __future__ import annotations

from collections import deque
from hashlib import sha256
from json import loads
from math import inf, nan
from pathlib import Path
from time import monotonic, sleep

import pytest

from RedstoneCompiler import RustRouting


A = (0, 0, 0)
B = (1, 0, 0)
C = (2, 0, 0)
ROOT_ORDINARY_FIXTURE = (
    Path(__file__).parents[1] / "Fixtures" / "JointRootOrdinaryInvalidProducerR1c.json"
)


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


def _coarse_start_connection(
    RequestId="coarse-starts-alpha",
    MaximumExpansionCount=32,
    AllowedColumns=((0, 0), (1, 0), (2, 0)),
    RequiredNodes=(),
    BlockedNodeValues=(),
    CancellationRequestedBeforeStart=False,
    Starts=(A,),
    Bindings=None,
    PreferredColumns=(),
):
    return RustRouting.RouteTreeCoarseRequestV1.ConnectStartsOnlyV1(
        RequestId,
        _bindings() if Bindings is None else Bindings,
        (0, 0, 0, 2, 0, 0),
        (0, 0, 2, 0),
        CancellationRequestedBeforeStart,
        list(Starts),
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


def test_explicit_start_connection_preserves_legacy_targetless_semantics():
    Context = _context()
    Targetless, Ordinary = Context.GenerateRouteTreesBatchOutcomesV1(
        "start-connection-single",
        (
            _coarse_start_connection(RequiredNodes=(C,)),
            _coarse(RequestId="ordinary-target", TargetBranches=((C,),)),
        ),
        monotonic() + 10,
    ).Receipts

    assert Targetless.RequestKind == "CoarseStartConnectionV1"
    assert Targetless.SearchOutcome == "Found"
    assert Targetless.TerminalReason == "Found"
    assert Targetless.Candidate.Nodes == [A]
    assert Targetless.Candidate.TargetPaths == []
    assert Targetless.NoPathProof is None
    assert Ordinary.RequestKind == "CoarseColumnsV1"
    assert Ordinary.Candidate.Nodes == [A, B, C]
    _assert_valid_route(Ordinary, ((A, B), (B, C)), (A,), C)


def test_start_connection_spans_connected_starts_and_keeps_disconnection_unresolved():
    Connected = _context().GenerateRouteTreesBatchOutcomesV1(
        "start-connection-connected",
        (_coarse_start_connection(Starts=(A, C)),),
        monotonic() + 10,
    ).Receipts[0]
    Disconnected = _context(Edges=(), Nodes=(A, C)).GenerateRouteTreesBatchOutcomesV1(
        "start-connection-disconnected",
        (
            _coarse_start_connection(
                Starts=(A, C),
                AllowedColumns=((0, 0), (2, 0)),
            ),
        ),
        monotonic() + 10,
    ).Receipts[0]

    assert Connected.SearchOutcome == "Found"
    assert Connected.Candidate.Nodes == [A, B, C]
    assert Connected.Candidate.TargetPaths == []
    assert Disconnected.SearchOutcome == "Incomplete"
    assert Disconnected.TerminalReason == "StartConnectionIncomplete"
    assert Disconnected.RuntimeSearchOutcome == "Unresolved"
    assert Disconnected.Candidate is None
    assert Disconnected.NoPathProof is None


def test_ordinary_multi_start_candidate_retains_source_connection_nodes():
    D = (3, 0, 0)
    Nodes = (A, B, C, D)
    Edges = ((A, B), (B, C), (C, D))
    Context = RustRouting.RoutingContext(
        (0, 0, 0, 3, 0, 0),
        (0, 0, 3, 0),
        list(Nodes),
        list(Edges),
    )
    LegacyArguments = (
        [A, D],
        [[B], [C]],
        [(0, 0), (1, 0), (2, 0), (3, 0)],
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        64,
    )
    Legacy = Context.GenerateRouteTreesBounded([LegacyArguments], 1_000)
    Raw = Context.GenerateRouteTreeDetailedBounded(
        [A, D],
        [[B], [C]],
        list(Nodes),
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        False,
        64,
        1_000,
    )
    Request = RustRouting.RouteTreeCoarseRequestV1(
        "ordinary-connected-starts",
        _bindings(),
        (0, 0, 0, 3, 0, 0),
        (0, 0, 3, 0),
        False,
        *LegacyArguments,
    )
    Receipt = Context.GenerateRouteTreesBatchOutcomesV1(
        "ordinary-connected-starts-batch",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Legacy.RouteTrees == [list(Nodes)]
    assert Raw.IsRouted is True
    assert Raw.Nodes == list(Nodes)
    assert Raw.SourcePaths == [[A], [A, B, C, D]]
    assert Raw.TargetPaths == [(B, [A, B]), (C, [A, B, C])]
    assert D not in {Node for _Target, Path in Raw.TargetPaths for Node in Path}
    assert _reachable(Raw.Nodes, Edges, (A,)) == set(Nodes)
    assert set((A, D)) <= set(Raw.Nodes)

    assert Receipt.SearchOutcome == "Found"
    assert Receipt.TerminalReason == "Found"
    assert Receipt.Candidate.Nodes == Raw.Nodes
    assert Receipt.Candidate.SourcePaths == Raw.SourcePaths
    assert Receipt.Candidate.TargetPaths == Raw.TargetPaths
    assert Receipt.RouteExpansionCount == Raw.ExpansionCount

    StartConnection = RustRouting.RouteTreeCoarseRequestV1.ConnectStartsOnlyV1(
        "connected-starts-control",
        _bindings("start-control"),
        (0, 0, 0, 3, 0, 0),
        (0, 0, 3, 0),
        False,
        [A, D],
        LegacyArguments[2],
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        64,
    )
    Control = Context.GenerateRouteTreesBatchOutcomesV1(
        "connected-starts-control-batch",
        (StartConnection,),
        monotonic() + 10,
    ).Receipts[0]
    assert Control.SearchOutcome == "Found"
    assert Control.Candidate.Nodes == list(Nodes)
    assert Control.Candidate.SourcePaths == [[A], [A, B, C, D]]
    assert Control.Candidate.TargetPaths == []


def test_ordinary_multi_start_candidate_validates_source_connection_repeater():
    Nodes = tuple((Index, 0, 0) for Index in range(17))
    Edges = tuple(zip(Nodes, Nodes[1:]))
    Context = RustRouting.RoutingContext(
        (0, 0, 0, 16, 0, 0),
        (0, 0, 16, 0),
        list(Nodes),
        list(Edges),
    )
    Request = RustRouting.RouteTreeDetailedRequestV1(
        "ordinary-source-repeater",
        _bindings("source-repeater"),
        (0, 0, 0, 16, 0, 0),
        (0, 0, 16, 0),
        False,
        [Nodes[0], Nodes[-1], Nodes[14]],
        [[Nodes[1]]],
        list(Nodes),
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        True,
        256,
    )
    Receipt = Context.GenerateRouteTreeDetailedBatchOutcomesV1(
        "ordinary-source-repeater-batch",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome == "Found"
    assert Receipt.Candidate.Nodes == list(Nodes)
    assert Receipt.Candidate.SourcePaths == [
        [Nodes[0]],
        list(Nodes),
        list(Nodes[:15]),
    ]
    assert Receipt.Candidate.TargetPaths == [(Nodes[1], [Nodes[0], Nodes[1]])]
    assert Receipt.Candidate.RepeaterReservations == [(Nodes[14], "west")]
    assert Nodes[14] not in Receipt.Candidate.TargetPaths[0][1]


def test_ordinary_source_witness_reconstruction_observes_original_deadline():
    NodeCount = 2_500
    Nodes = tuple((Index, 0, 0) for Index in range(NodeCount))
    Context = RustRouting.RoutingContext(
        (0, 0, 0, NodeCount - 1, 0, 0),
        (0, 0, NodeCount - 1, 0),
        list(Nodes),
        list(zip(Nodes, Nodes[1:])),
    )
    Starts = [Nodes[0], Nodes[-1], *Nodes[1:-1]]

    Result = Context.GenerateRouteTreeDetailedBounded(
        Starts,
        [[Nodes[1]]],
        list(Nodes),
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        False,
        10_000,
        20,
    )

    assert Result.Status == "BudgetExpired"
    assert Result.IsRouted is False
    assert Result.IsBudgetExpired is True
    assert Result.Nodes == []
    assert Result.SourcePaths == []
    assert Result.TargetPaths == []
    assert Result.ExpansionCount <= 10_000


def test_certified_warm_factorized_route_observes_cap_and_keeps_valid_control():
    Context = _context()
    Access = (
        [A],
        [A],
        [[C]],
        [[C]],
        [A, C],
        [],
        [],
        [],
        [],
        [],
    )
    Guide = (
        [(0, 0), (1, 0), (2, 0)],
        [],
        [],
        [[A, B, C]],
        [],
    )

    Limited = Context.GenerateRouteTreesFactorizedBounded(
        [Access],
        [Guide],
        [(0, 0, 0, 0, 0, 0, 1)],
        1_000,
    )
    Valid = Context.GenerateRouteTreesFactorizedBounded(
        [Access],
        [Guide],
        [(0, 0, 0, 0, 0, 0, 3)],
        1_000,
    )

    assert Limited.RouteTrees == [None]
    assert Limited.CompletionMask == [False]
    assert Limited.CompletedWork == 0
    assert Limited.DeadlineExceeded is False
    assert Valid.RouteTrees == [[A, B, C]]
    assert Valid.CompletionMask == [True]
    assert Valid.CompletedWork == 1
    assert Valid.DeadlineExceeded is False


def test_factorized_claim_audit_cannot_publish_after_deadline():
    Context = _context()
    MandatorySupport = [(Index, 100, 0) for Index in range(300_000)]
    Access = (
        [A],
        [A],
        [[C]],
        [[C]],
        [A, C],
        [],
        [],
        MandatorySupport,
        [],
        [],
    )
    Guide = (
        [(0, 0), (1, 0), (2, 0)],
        [],
        [],
        [[A, B, C]],
        [],
    )

    Result = Context.GenerateRouteTreesFactorizedBounded(
        [Access],
        [Guide],
        [(0, 0, 0, 0, 0, 0, 32)],
        7,
    )

    assert Result.RouteTrees == [None]
    assert Result.CompletionMask == [False]
    assert Result.CompletedWork == 0
    assert Result.DeadlineExceeded is True


def test_real_joint_root_ordinary_candidate_passes_independent_graph_oracle():
    Record = loads(ROOT_ORDINARY_FIXTURE.read_text())
    ContextDocument = Record["Context"]
    RequestDocument = Record["Request"]
    Node = lambda Value: tuple(Value)
    Nodes = [Node(Value) for Value in ContextDocument["Nodes"]]
    Edges = [
        (Node(First), Node(Second))
        for First, Second in ContextDocument["Edges"]
    ]
    Starts = [Node(Value) for Value in RequestDocument["Starts"]]
    TargetBranches = [
        [Node(Value) for Value in Branch]
        for Branch in RequestDocument["TargetBranches"]
    ]
    AllowedColumns = [tuple(Value) for Value in RequestDocument["AllowedColumns"]]
    RequiredNodes = [Node(Value) for Value in RequestDocument["RequiredNodes"]]
    BlockedNodes = [Node(Value) for Value in RequestDocument["BlockedNodes"]]
    PreferredColumns = [
        tuple(Value) for Value in RequestDocument["PreferredColumns"]
    ]
    Context = RustRouting.RoutingContext(
        tuple(ContextDocument["Bounds"]),
        tuple(ContextDocument["PlacementBounds"]),
        Nodes,
        Edges,
    )
    LegacyArguments = (
        Starts,
        TargetBranches,
        AllowedColumns,
        RequiredNodes,
        BlockedNodes,
        PreferredColumns,
        RequestDocument["PreferredRoutingY"],
        RequestDocument["GuidePenalty"],
        RequestDocument["BendPenalty"],
        RequestDocument["ViaPenalty"],
        RequestDocument["MaximumExpansionCount"],
    )
    Request = RustRouting.RouteTreeCoarseRequestV1(
        RequestDocument["RequestId"],
        [tuple(Value) for Value in RequestDocument["CallerEchoBindings"]],
        tuple(ContextDocument["Bounds"]),
        tuple(ContextDocument["PlacementBounds"]),
        False,
        *LegacyArguments,
    )

    assert Record["PublicTypedOutcome"]["NativeReceipt"]["TerminalReason"] == (
        "InvalidProducerResult"
    )
    assert Record["IndependentLegacyValidity"][
        "LegacyGraphAndTargetContractValid"
    ] is True
    assert Context.AuthoritativeContextGraphSha256 == ContextDocument[
        "AuthoritativeContextGraphSha256"
    ]
    assert Request.NativePayloadSha256 == RequestDocument["NativePayloadSha256"]

    Receipt = Context.GenerateRouteTreesBatchOutcomesV1(
        "joint-root-ordinary-r1c-replay",
        (Request,),
        monotonic() + 30,
    ).Receipts[0]
    Legacy = Context.GenerateRouteTreesBounded([LegacyArguments], 30_000)

    assert Receipt.SearchOutcome == Receipt.TerminalReason == "Found"
    assert Receipt.Candidate is not None
    assert Receipt.NoPathProof is None
    assert Receipt.RouteExpansionCount == Receipt.Candidate.ExpansionCount == 228
    assert Receipt.ProofExpansionCount == 0
    assert Receipt.TotalExpansionCount <= Receipt.MaximumExpansionCount == 25_000
    assert Receipt.NativePayloadSha256 == RequestDocument["NativePayloadSha256"]
    assert Receipt.ContextGraphSha256 == ContextDocument[
        "AuthoritativeContextGraphSha256"
    ]

    CandidateNodes = set(Receipt.Candidate.Nodes)
    EdgeSet = {frozenset(Edge) for Edge in Edges}
    AllowedColumnSet = set(AllowedColumns)
    assert set(Starts) <= CandidateNodes
    assert set(RequiredNodes) <= CandidateNodes
    assert not CandidateNodes.intersection(BlockedNodes)
    assert all((X, Z) in AllowedColumnSet for X, _Y, Z in CandidateNodes)
    CandidateEdges = [
        (First, Second)
        for First, Second in Edges
        if First in CandidateNodes and Second in CandidateNodes
    ]
    assert _reachable(CandidateNodes, CandidateEdges, (Starts[0],)) == CandidateNodes
    assert len(Receipt.Candidate.TargetPaths) == len(TargetBranches)
    MatchedBranches = set()
    PathNodes = set()
    for Target, Path in Receipt.Candidate.TargetPaths:
        assert Path[0] in Starts
        assert Path[-1] == Target
        assert all(frozenset(Pair) in EdgeSet for Pair in zip(Path, Path[1:]))
        assert all(Value in CandidateNodes for Value in Path)
        PathNodes.update(Path)
        Matches = [
            Index
            for Index, Branch in enumerate(TargetBranches)
            if Branch[-1] == Target and Path[-len(Branch) :] == Branch
        ]
        assert len(Matches) == 1
        MatchedBranches.add(Matches[0])
    assert MatchedBranches == set(range(len(TargetBranches)))
    assert CandidateNodes - PathNodes == set(Starts[1:])
    assert len(Receipt.Candidate.SourcePaths) == len(Starts)
    SourcePathNodes = set()
    for ExpectedStart, Path in zip(Starts, Receipt.Candidate.SourcePaths):
        assert Path[0] == Starts[0]
        assert Path[-1] == ExpectedStart
        assert len(Path) == len(set(Path))
        assert all(frozenset(Pair) in EdgeSet for Pair in zip(Path, Path[1:]))
        SourcePathNodes.update(Path)
    assert SourcePathNodes.union(PathNodes) == CandidateNodes

    ExpectedLegacy = [Node(Value) for Value in Record["LegacyBatch"]["RouteTree"]]
    assert Legacy.CompletedWork == 1
    assert Legacy.DeadlineExceeded is False
    assert Legacy.RouteTrees == [ExpectedLegacy]
    assert len(ExpectedLegacy) == 71
    assert len(CandidateNodes) == 70


@pytest.mark.parametrize(
    "Request",
    (
        _coarse_start_connection(Starts=()),
        _coarse_start_connection(BlockedNodeValues=(A,)),
        _coarse_start_connection(
            AllowedColumns=((1, 0), (2, 0)),
            RequiredNodes=(C,),
        ),
        _coarse(TargetBranches=((),)),
    ),
)
def test_start_connection_and_ordinary_requests_reject_invalid_roots_or_branches(Request):
    Receipt = _context().GenerateRouteTreesBatchOutcomesV1(
        "start-connection-invalid",
        (Request,),
        monotonic() + 10,
    ).Receipts[0]

    assert Receipt.SearchOutcome == "Incomplete"
    assert Receipt.TerminalReason == "UnsupportedRequest"
    assert Receipt.Candidate is None
    assert Receipt.NoPathProof is None


def test_start_connection_preserves_cap_cancellation_and_deadline_outcomes():
    Context = _context()
    Zero = Context.GenerateRouteTreesBatchOutcomesV1(
        "start-connection-zero",
        (_coarse_start_connection(MaximumExpansionCount=0),),
        monotonic() + 10,
    ).Receipts[0]
    Tiny = Context.GenerateRouteTreesBatchOutcomesV1(
        "start-connection-tiny",
        (_coarse_start_connection(Starts=(A, C), MaximumExpansionCount=1),),
        monotonic() + 10,
    ).Receipts[0]
    Cancelled = Context.GenerateRouteTreesBatchOutcomesV1(
        "start-connection-cancelled",
        (_coarse_start_connection(CancellationRequestedBeforeStart=True),),
        monotonic() + 10,
    ).Receipts[0]
    Expired = Context.GenerateRouteTreesBatchOutcomesV1(
        "start-connection-expired",
        (_coarse_start_connection(),),
        monotonic() - 1,
    ).Receipts[0]

    assert (Zero.TerminalReason, Zero.TotalExpansionCount) == ("WorkCapExhausted", 0)
    assert Tiny.SearchOutcome == "Incomplete"
    assert Tiny.TerminalReason == "WorkCapExhausted"
    assert Tiny.TotalExpansionCount == 1
    assert Tiny.NoPathProof is None
    assert (Cancelled.TerminalReason, Cancelled.TotalExpansionCount) == ("Cancelled", 0)
    assert Cancelled.CancellationAcknowledged is True
    assert (Expired.TerminalReason, Expired.TotalExpansionCount) == (
        "DeadlineExhaustedAtEntry",
        0,
    )


def test_start_connection_intent_is_bound_and_set_inputs_remain_canonical():
    Context = _context()
    First, Permuted, Ordinary = Context.GenerateRouteTreesBatchOutcomesV1(
        "start-connection-identities",
        (
            _coarse_start_connection(
                AllowedColumns=((0, 0), (1, 0), (2, 0)),
                RequiredNodes=(C,),
                PreferredColumns=((1, 0), (2, 0)),
            ),
            _coarse_start_connection(
                RequestId="permuted",
                AllowedColumns=((2, 0), (0, 0), (1, 0), (0, 0)),
                RequiredNodes=(C, C),
                PreferredColumns=((2, 0), (1, 0), (2, 0)),
            ),
            _coarse(RequestId="ordinary", TargetBranches=((C,),)),
        ),
        monotonic() + 10,
    ).Receipts

    assert First.NativePayloadSha256 == Permuted.NativePayloadSha256
    assert First.RouteDomainScopeSha256 == Permuted.RouteDomainScopeSha256
    assert First.NativePayloadSha256 != Ordinary.NativePayloadSha256
    assert First.ImmutableInputSha256 != Ordinary.ImmutableInputSha256
    assert First.RouteDomainScopeSha256 != Ordinary.RouteDomainScopeSha256
    assert First.ReceiptScopeSha256 != Ordinary.ReceiptScopeSha256


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


def test_native_payload_identity_excludes_logical_metadata_and_covers_every_control():
    BaseArguments = [
        "logical-alpha",
        _bindings("alpha"),
        (0, 0, 0, 2, 0, 0),
        (0, 0, 2, 0),
        False,
        [A],
        [[C]],
        [(0, 0), (1, 0), (2, 0)],
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        32,
    ]

    MetadataVariant = list(BaseArguments)
    MetadataVariant[0] = "logical-beta"
    MetadataVariant[1] = _bindings("beta")
    MetadataVariant[2] = (-100, -100, -100, 100, 100, 100)
    MetadataVariant[3] = (-100, -100, 100, 100)

    PayloadVariants = []
    for Index, Value in (
        (4, True),
        (5, [B]),
        (6, [[B]]),
        (7, [(0, 0), (1, 0)]),
        (8, [C]),
        (9, [B]),
        (10, [(1, 0)]),
        (11, 1),
        (12, 1),
        (13, 1),
        (14, 1),
        (15, 31),
    ):
        Arguments = list(BaseArguments)
        Arguments[Index] = Value
        PayloadVariants.append(RustRouting.RouteTreeCoarseRequestV1(*Arguments))

    Requests = (
        RustRouting.RouteTreeCoarseRequestV1(*BaseArguments),
        RustRouting.RouteTreeCoarseRequestV1(*MetadataVariant),
        *PayloadVariants,
    )
    Receipts = _context().GenerateRouteTreesBatchOutcomesV1(
        "native-payload-identity",
        Requests,
        monotonic() + 10,
    ).Receipts

    Base = Receipts[0]
    assert Base.NativePayloadSha256 == sha256(
        Base.NativePayloadCanonicalJson.encode()
    ).hexdigest()
    assert Receipts[1].NativePayloadCanonicalJson == Base.NativePayloadCanonicalJson
    assert Receipts[1].NativePayloadSha256 == Base.NativePayloadSha256
    assert Receipts[1].ImmutableInputSha256 != Base.ImmutableInputSha256
    assert all(
        Receipt.NativePayloadSha256 != Base.NativePayloadSha256
        for Receipt in Receipts[2:]
    )

    Permuted = _context().GenerateRouteTreesBatchOutcomesV1(
        "native-payload-canonical-sets",
        (
            _coarse(
                AllowedColumns=((2, 0), (0, 0), (1, 0), (0, 0)),
                RequiredNodes=(C, C),
                BlockedNodeValues=(B, B),
                PreferredColumns=((2, 0), (1, 0), (2, 0)),
            ),
            _coarse(
                AllowedColumns=((0, 0), (1, 0), (2, 0)),
                RequiredNodes=(C,),
                BlockedNodeValues=(B,),
                PreferredColumns=((1, 0), (2, 0)),
            ),
        ),
        monotonic() + 10,
    ).Receipts
    assert Permuted[0].NativePayloadCanonicalJson == (
        Permuted[1].NativePayloadCanonicalJson
    )
    assert Permuted[0].NativePayloadSha256 == Permuted[1].NativePayloadSha256


def test_detailed_native_payload_identity_canonicalizes_sets_and_covers_controls():
    Base = [
        "detailed-logical-alpha",
        _bindings("alpha"),
        (0, 0, 0, 2, 0, 0),
        (0, 0, 2, 0),
        False,
        [A],
        [[C]],
        [A, B, C],
        [],
        [],
        [],
        0,
        0,
        0,
        0,
        False,
        32,
    ]
    PayloadVariants = []
    for Index, Value in (
        (4, True),
        (5, [B]),
        (6, [[B]]),
        (7, [A, B]),
        (8, [B]),
        (9, [(1, 0)]),
        (10, [(B, 1)]),
        (11, 1),
        (12, 1),
        (13, 1),
        (14, 1),
        (15, True),
        (16, 31),
    ):
        Arguments = list(Base)
        Arguments[Index] = Value
        PayloadVariants.append(RustRouting.RouteTreeDetailedRequestV1(*Arguments))
    MetadataVariant = list(Base)
    MetadataVariant[0] = "detailed-logical-beta"
    MetadataVariant[1] = _bindings("beta")
    MetadataVariant[2] = (-10, -10, -10, 10, 10, 10)
    MetadataVariant[3] = (-10, -10, 10, 10)
    Requests = (
        RustRouting.RouteTreeDetailedRequestV1(*Base),
        RustRouting.RouteTreeDetailedRequestV1(*MetadataVariant),
        *PayloadVariants,
    )
    Receipts = _context().GenerateRouteTreeDetailedBatchOutcomesV1(
        "detailed-native-payload-controls",
        Requests,
        monotonic() + 10,
    ).Receipts

    assert Receipts[0].NativePayloadSha256 == Receipts[1].NativePayloadSha256
    assert Receipts[0].ImmutableInputSha256 != Receipts[1].ImmutableInputSha256
    assert all(
        Receipt.NativePayloadSha256 != Receipts[0].NativePayloadSha256
        for Receipt in Receipts[2:]
    )

    CanonicalNodeCosts = list(Base)
    CanonicalNodeCosts[7] = [C, A, B, A]
    CanonicalNodeCosts[8] = [B, B]
    CanonicalNodeCosts[9] = [(2, 0), (1, 0), (2, 0)]
    CanonicalNodeCosts[10] = [(B, 4), (A, 1), (B, 7)]
    NormalizedNodeCosts = list(Base)
    NormalizedNodeCosts[7] = [A, B, C]
    NormalizedNodeCosts[8] = [B]
    NormalizedNodeCosts[9] = [(1, 0), (2, 0)]
    NormalizedNodeCosts[10] = [(A, 1), (B, 7)]
    CanonicalReceipts = _context().GenerateRouteTreeDetailedBatchOutcomesV1(
        "detailed-native-payload-canonical-sets",
        (
            RustRouting.RouteTreeDetailedRequestV1(*CanonicalNodeCosts),
            RustRouting.RouteTreeDetailedRequestV1(*NormalizedNodeCosts),
        ),
        monotonic() + 10,
    ).Receipts
    assert CanonicalReceipts[0].NativePayloadCanonicalJson == (
        CanonicalReceipts[1].NativePayloadCanonicalJson
    )
    assert CanonicalReceipts[0].NativePayloadSha256 == (
        CanonicalReceipts[1].NativePayloadSha256
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

"""Editor-facing fallback declarations for the compiled PyO3 routing extension.

The implementation is RustRouting.cpython-*-*.so.  PyO3 does not emit Python
type information, so this stub tells static analyzers that exported bindings
are provided dynamically by the native module without imposing inaccurate
signatures on the routing API.
"""

from typing import Any, Sequence


class RouteTreeCoarseRequestV1:
    ContractVersion: str
    RequestKind: str
    NativePayloadCanonicalJson: str
    NativePayloadSha256: str
    ImmutableInputSha256: str
    CallerEchoScopeSha256: str
    RequestId: str
    CancellationRequestedBeforeStart: bool
    MaximumExpansionCount: int

    def __init__(
        self,
        RequestId: str,
        CallerEchoBindings: Sequence[tuple[str, str]],
        DeclaredBounds: tuple[int, int, int, int, int, int],
        DeclaredPlacementBounds: tuple[int, int, int, int],
        CancellationRequestedBeforeStart: bool,
        Starts: Sequence[tuple[int, int, int]],
        TargetBranches: Sequence[Sequence[tuple[int, int, int]]],
        AllowedColumns: Sequence[tuple[int, int]],
        RequiredNodes: Sequence[tuple[int, int, int]],
        BlockedNodeValues: Sequence[tuple[int, int, int]],
        PreferredColumns: Sequence[tuple[int, int]],
        PreferredRoutingY: int,
        GuidePenalty: int,
        BendPenalty: int,
        ViaPenalty: int,
        MaximumExpansionCount: int,
    ) -> None: ...


class RouteTreeDetailedRequestV1:
    ContractVersion: str
    RequestKind: str
    NativePayloadCanonicalJson: str
    NativePayloadSha256: str
    ImmutableInputSha256: str
    CallerEchoScopeSha256: str
    RequestId: str
    CancellationRequestedBeforeStart: bool
    MaximumExpansionCount: int

    def __init__(
        self,
        RequestId: str,
        CallerEchoBindings: Sequence[tuple[str, str]],
        DeclaredBounds: tuple[int, int, int, int, int, int],
        DeclaredPlacementBounds: tuple[int, int, int, int],
        CancellationRequestedBeforeStart: bool,
        Starts: Sequence[tuple[int, int, int]],
        TargetBranches: Sequence[Sequence[tuple[int, int, int]]],
        AllowedNodeValues: Sequence[tuple[int, int, int]],
        BlockedNodeValues: Sequence[tuple[int, int, int]],
        PreferredColumns: Sequence[tuple[int, int]],
        NodeCostValues: Sequence[tuple[tuple[int, int, int], int]],
        PreferredRoutingY: int,
        GuidePenalty: int,
        BendPenalty: int,
        ViaPenalty: int,
        EnforceSignalStrength: bool,
        MaximumExpansionCount: int,
    ) -> None: ...


class RouteTreeNoPathProofV1:
    ProofKind: str
    UnreachableTargetBranchOrdinals: list[int]
    UnreachableAttachmentNodes: list[tuple[int, int, int]]
    ReachedNodeCount: int
    ExpansionCount: int
    Complete: bool
    ClaimScope: str
    ContextGraphSha256: str
    RouteDomainScopeSha256: str
    ImmutableInputSha256: str
    ReceiptScopeSha256: str


class RouteTreeRequestReceiptV1:
    ContractVersion: str
    BatchIdentity: str
    BatchIdentityRetentionStatus: str
    RequestKind: str
    RequestId: str
    OriginalOrdinal: int
    SearchOutcome: str
    TerminalReason: str
    RuntimeSearchOutcome: str
    RuntimeClaimStrength: str
    RuntimeCommitEligibility: str
    Started: bool
    Settled: bool
    StartedAtMicroseconds: int | None
    CompletedAtMicroseconds: int | None
    DeadlineAtMonotonicSeconds: float
    BoundaryMonotonicSampleSeconds: float
    NativeRemainingNanoseconds: int
    WorkUnit: str
    MaximumExpansionCount: int
    RouteExpansionCount: int
    ProofExpansionCount: int
    TotalExpansionCount: int
    NativePayloadCanonicalJson: str
    NativePayloadSha256: str
    RawInputRetentionStatus: str
    CancellationSnapshotStatus: str
    OutcomePhase: str
    ContextGraphIdentityAvailability: str
    ContextGraphSha256: str | None
    RouteDomainIdentityAvailability: str
    RouteDomainScopeCanonicalJson: str | None
    RouteDomainScopeSha256: str | None
    ImmutableInputIdentityAvailability: str
    ImmutableInputCanonicalJson: str | None
    ImmutableInputSha256: str | None
    ReceiptIdentityAvailability: str
    ReceiptIdentityDependency: str | None
    ReceiptScopeCanonicalJson: str | None
    ReceiptScopeSha256: str | None
    CallerEchoIdentityAvailability: str
    CallerEchoScopeCanonicalJson: str | None
    CallerEchoScopeSha256: str | None
    Candidate: Any | None
    NoPathProof: RouteTreeNoPathProofV1 | None
    CancellationRequested: bool
    CancellationAcknowledged: bool
    SearchStopped: bool
    CleanupDisposition: str


class RouteTreeBatchOutcomesV1:
    ContractVersion: str
    BatchIdentity: str
    BatchIdentityRetentionStatus: str
    DeadlineAtMonotonicSeconds: float
    BoundaryMonotonicSampleSeconds: float
    NativeRemainingNanoseconds: int
    ContextGraphIdentityAvailability: str
    ContextGraphCanonicalJson: str | None
    ContextGraphSha256: str | None
    Receipts: list[RouteTreeRequestReceiptV1]
    TotalRequestCount: int
    StartedRequestCount: int
    SettledReceiptCount: int
    FoundCount: int
    ProvenNoPathCount: int
    IncompleteCount: int
    AggregateRouteExpansionCount: int
    AggregateProofExpansionCount: int
    AggregateExpansionCount: int
    DeadlineExceeded: bool


class RoutingContext:
    AuthoritativeContextGraphSha256: str

    def GenerateRouteTreesBatchOutcomesV1(
        self,
        BatchIdentity: str,
        Requests: tuple[RouteTreeCoarseRequestV1, ...],
        DeadlineAtMonotonicSeconds: float,
    ) -> RouteTreeBatchOutcomesV1: ...

    def GenerateRouteTreeDetailedBatchOutcomesV1(
        self,
        BatchIdentity: str,
        Requests: tuple[RouteTreeDetailedRequestV1, ...],
        DeadlineAtMonotonicSeconds: float,
    ) -> RouteTreeBatchOutcomesV1: ...

    def __getattr__(self, Name: str) -> Any: ...


def __getattr__(Name: str) -> Any: ...


def ObserveMchprsFixture(RequestJson: str) -> str:
    """Observe one fresh case without expected values; return inclusive raw ticks."""
    ...

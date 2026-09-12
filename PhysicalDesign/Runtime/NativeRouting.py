"""Typed Python projection of authoritative native route-batch receipts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from json import loads
from math import isfinite
from typing import Any

from ..Contracts.Runtime import (
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeSearchOutcome,
)


class NativeRouteResultKind(str, Enum):
    """Convenience view derived from the independent Runtime outcome axes."""

    Routed = "Routed"
    CompleteScopedNoPath = "CompleteScopedNoPath"
    SearchLimitIncomplete = "SearchLimitIncomplete"
    DeadlineIncomplete = "DeadlineIncomplete"
    CancellationIncomplete = "CancellationIncomplete"
    NativeFailure = "NativeFailure"


@dataclass(frozen=True)
class NativeRouteExecutionScopeV1:
    """Bindings required in addition to a request-payload identity.

    ``NativeRequestPayloadIdentity`` is deliberately not a standalone cache,
    freshness, or execution identity. Sharing also requires every other
    binding in this value to match exactly.
    """

    BatchIdentity: str
    ContextGraphIdentity: str
    RouteDomainIdentity: str | None
    CallerSourceIdentity: str
    ImmutableInputIdentity: str
    ReceiptIdentity: str | None
    DeadlineAtMonotonicSeconds: float
    NativeRequestPayloadIdentity: str

    def __post_init__(self) -> None:
        if not isinstance(self.BatchIdentity, str) or not self.BatchIdentity:
            raise TypeError("BatchIdentity must be a non-empty string")
        for Name in (
            "ContextGraphIdentity",
            "CallerSourceIdentity",
            "ImmutableInputIdentity",
            "NativeRequestPayloadIdentity",
        ):
            Value = getattr(self, Name)
            if (
                not isinstance(Value, str)
                or len(Value) != 64
                or any(Character not in "0123456789abcdef" for Character in Value)
            ):
                raise TypeError(f"{Name} must be a lowercase SHA-256 identity")
        for Name in ("RouteDomainIdentity", "ReceiptIdentity"):
            Value = getattr(self, Name)
            if Value is not None and (
                not isinstance(Value, str)
                or len(Value) != 64
                or any(Character not in "0123456789abcdef" for Character in Value)
            ):
                raise TypeError(f"{Name} must be None or a lowercase SHA-256 identity")
        if (
            type(self.DeadlineAtMonotonicSeconds) is not float
            or not isfinite(self.DeadlineAtMonotonicSeconds)
            or self.DeadlineAtMonotonicSeconds < 0.0
        ):
            raise TypeError("DeadlineAtMonotonicSeconds must be a finite non-negative float")


@dataclass(frozen=True)
class NativeRouteRequestResultV1:
    """One logical request bound to its authoritative native receipt."""

    RequestIdentity: str
    NativePayloadIdentity: str
    NativePayloadCanonicalJson: str
    OriginalOrdinal: int
    Kind: NativeRouteResultKind
    SearchOutcome: RuntimeSearchOutcome
    ClaimStrength: RuntimeClaimStrength
    CommitEligibility: RuntimeCommitEligibility
    Reason: str
    ActualExpansionCount: int
    ExpansionCap: int
    ExecutionScope: NativeRouteExecutionScopeV1
    Candidate: Any | None
    CompleteScopedNoPathProof: Any | None
    NativeReceipt: Any

    def __post_init__(self) -> None:
        if not isinstance(self.RequestIdentity, str) or not self.RequestIdentity:
            raise TypeError("RequestIdentity must be a non-empty string")
        if type(self.Kind) is not NativeRouteResultKind:
            raise TypeError("Kind must be a NativeRouteResultKind")
        if type(self.SearchOutcome) is not RuntimeSearchOutcome:
            raise TypeError("SearchOutcome must be a RuntimeSearchOutcome")
        if type(self.ClaimStrength) is not RuntimeClaimStrength:
            raise TypeError("ClaimStrength must be a RuntimeClaimStrength")
        if type(self.CommitEligibility) is not RuntimeCommitEligibility:
            raise TypeError("CommitEligibility must be a RuntimeCommitEligibility")
        if self.CommitEligibility is not RuntimeCommitEligibility.Ineligible:
            raise ValueError("native route results never carry commitment authority")
        if (
            not isinstance(self.NativePayloadIdentity, str)
            or len(self.NativePayloadIdentity) != 64
        ):
            raise TypeError("NativePayloadIdentity must be a SHA-256 hexadecimal string")
        if self.NativePayloadIdentity != self.ExecutionScope.NativeRequestPayloadIdentity:
            raise ValueError("native payload identity does not match execution scope")
        if (
            not isinstance(self.NativePayloadCanonicalJson, str)
            or not self.NativePayloadCanonicalJson
        ):
            raise TypeError("NativePayloadCanonicalJson must be a non-empty string")
        if sha256(self.NativePayloadCanonicalJson.encode()).hexdigest() != (
            self.NativePayloadIdentity
        ):
            raise ValueError("native payload bytes do not match their identity")
        for Name, Value in (
            ("OriginalOrdinal", self.OriginalOrdinal),
            ("ActualExpansionCount", self.ActualExpansionCount),
            ("ExpansionCap", self.ExpansionCap),
        ):
            if isinstance(Value, bool) or not isinstance(Value, int) or Value < 0:
                raise TypeError(f"{Name} must be a non-negative integer")
        if self.ActualExpansionCount > self.ExpansionCap:
            raise ValueError("actual native expansion count exceeds its cap")
        if not isinstance(self.Reason, str) or not self.Reason:
            raise TypeError("Reason must be a non-empty string")
        if self.Kind is NativeRouteResultKind.Routed:
            if self.Candidate is None or self.CompleteScopedNoPathProof is not None:
                raise ValueError("routed results require only a candidate")
        elif self.Kind is NativeRouteResultKind.CompleteScopedNoPath:
            if self.Candidate is not None or self.CompleteScopedNoPathProof is None:
                raise ValueError("complete scoped no-path results require only a proof")
        elif self.Candidate is not None or self.CompleteScopedNoPathProof is not None:
            raise ValueError("incomplete and failed results cannot carry a candidate or proof")


@dataclass(frozen=True)
class NativeRouteBatchResultV1:
    """Original-order typed projection of one native route batch."""

    Results: tuple[NativeRouteRequestResultV1, ...]
    DeadlineExceeded: bool
    AggregateExpansionCount: int
    NativeBatch: Any

    def __post_init__(self) -> None:
        if type(self.Results) is not tuple or any(
            type(Result) is not NativeRouteRequestResultV1 for Result in self.Results
        ):
            raise TypeError("Results must be an exact tuple of native route results")
        if type(self.DeadlineExceeded) is not bool:
            raise TypeError("DeadlineExceeded must be an exact bool")
        if (
            isinstance(self.AggregateExpansionCount, bool)
            or not isinstance(self.AggregateExpansionCount, int)
            or self.AggregateExpansionCount < 0
        ):
            raise TypeError("AggregateExpansionCount must be a non-negative integer")
        if self.AggregateExpansionCount != sum(
            Result.ActualExpansionCount for Result in self.Results
        ):
            raise ValueError("aggregate native expansion count does not match receipts")
        if tuple(Result.OriginalOrdinal for Result in self.Results) != tuple(
            range(len(self.Results))
        ):
            raise ValueError("native results are not in original request order")


def _RequireIdentity(
    CanonicalJson: object,
    Identity: object,
    Name: str,
    *,
    AllowUnavailable: bool = False,
) -> str | None:
    if CanonicalJson is None or Identity is None:
        if AllowUnavailable and CanonicalJson is None and Identity is None:
            return None
        raise ValueError(f"{Name} identity is unavailable")
    if not isinstance(CanonicalJson, str) or not CanonicalJson:
        raise TypeError(f"{Name} canonical JSON must be a non-empty string")
    if not isinstance(Identity, str) or len(Identity) != 64:
        raise TypeError(f"{Name} identity must be a SHA-256 hexadecimal string")
    if sha256(CanonicalJson.encode()).hexdigest() != Identity:
        raise ValueError(f"{Name} canonical JSON does not match its identity")
    return Identity


def _ClassifyReceipt(
    Receipt: Any,
) -> tuple[
    NativeRouteResultKind,
    RuntimeSearchOutcome,
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
]:
    SearchOutcome = str(Receipt.SearchOutcome)
    Reason = str(Receipt.TerminalReason)
    if SearchOutcome == "Found" and Reason == "Found":
        Kind = NativeRouteResultKind.Routed
        ExpectedAxes = (
            RuntimeSearchOutcome.Prepared,
            RuntimeClaimStrength.Candidate,
            RuntimeCommitEligibility.Ineligible,
        )
    elif SearchOutcome == "ProvenNoPath" and Reason == "RelaxedGraphDisconnected":
        Proof = Receipt.NoPathProof
        if (
            Proof is None
            or Proof.Complete is not True
            or Proof.ClaimScope != "OneOriginalRouteRequest"
        ):
            raise ValueError("native no-path result lacks a complete request-scoped proof")
        Kind = NativeRouteResultKind.CompleteScopedNoPath
        ExpectedAxes = (
            RuntimeSearchOutcome.Infeasible,
            RuntimeClaimStrength.InfeasibilityProof,
            RuntimeCommitEligibility.Ineligible,
        )
    elif SearchOutcome == "Incomplete":
        if Reason == "WorkCapExhausted":
            Kind = NativeRouteResultKind.SearchLimitIncomplete
        elif Reason.startswith("DeadlineExhausted"):
            Kind = NativeRouteResultKind.DeadlineIncomplete
        elif Reason == "Cancelled":
            Kind = NativeRouteResultKind.CancellationIncomplete
        else:
            Kind = NativeRouteResultKind.NativeFailure
        ExpectedAxes = (
            RuntimeSearchOutcome.Unresolved,
            RuntimeClaimStrength.Continuation,
            RuntimeCommitEligibility.Ineligible,
        )
    else:
        raise ValueError("native receipt has an unknown search outcome")
    ActualAxes = (
        RuntimeSearchOutcome(Receipt.RuntimeSearchOutcome),
        RuntimeClaimStrength(Receipt.RuntimeClaimStrength),
        RuntimeCommitEligibility(Receipt.RuntimeCommitEligibility),
    )
    if ActualAxes != ExpectedAxes:
        raise ValueError("native receipt contradicts the Runtime outcome axes")
    return (Kind, *ActualAxes)


def _AdaptNativeRouteBatchOutcomesV1(
    NativeBatch: Any,
    ExpectedRequests: tuple[Any, ...],
    ExpectedBatchIdentity: str,
    ExpectedDeadlineAtMonotonicSeconds: float,
    ExpectedContextGraphIdentity: str,
) -> NativeRouteBatchResultV1:
    """Validate one direct native invocation and retain all execution bindings."""

    from RedstoneCompiler import RustRouting

    if type(ExpectedRequests) is not tuple:
        raise TypeError("ExpectedRequests must be an exact tuple")
    if type(NativeBatch) is not RustRouting.RouteTreeBatchOutcomesV1:
        raise TypeError("NativeBatch must be an exact authoritative native batch")
    ExpectedRequestType = (
        RustRouting.RouteTreeDetailedRequestV1
        if ExpectedRequests and ExpectedRequests[0].RequestKind == "DetailedNodesV1"
        else RustRouting.RouteTreeCoarseRequestV1
    )
    if any(type(Request) is not ExpectedRequestType for Request in ExpectedRequests):
        raise TypeError("ExpectedRequests contain a non-native or mixed request type")
    if NativeBatch.ContractVersion != "native-route-batch-outcomes/v1":
        raise ValueError("unsupported native route-batch contract")
    if NativeBatch.BatchIdentity != ExpectedBatchIdentity:
        raise ValueError("native route-batch identity mismatch")
    if NativeBatch.DeadlineAtMonotonicSeconds != ExpectedDeadlineAtMonotonicSeconds:
        raise ValueError("native route-batch deadline mismatch")
    if type(NativeBatch.DeadlineExceeded) is not bool:
        raise TypeError("native route-batch DeadlineExceeded must be an exact bool")
    if NativeBatch.ContextGraphIdentityAvailability != "Verified":
        raise ValueError("native route-batch context graph identity is unavailable")
    ContextGraphIdentity = _RequireIdentity(
        NativeBatch.ContextGraphCanonicalJson,
        NativeBatch.ContextGraphSha256,
        "context graph",
    )
    if ContextGraphIdentity != ExpectedContextGraphIdentity:
        raise ValueError("native route-batch context graph mismatch")
    if NativeBatch.TotalRequestCount != len(ExpectedRequests):
        raise ValueError("native route-batch request count mismatch")
    Receipts = tuple(NativeBatch.Receipts)
    if len(Receipts) != len(ExpectedRequests):
        raise ValueError("native route-batch receipt count mismatch")

    ReceiptsByOrdinal: dict[int, Any] = {}
    for Receipt in Receipts:
        if type(Receipt) is not RustRouting.RouteTreeRequestReceiptV1:
            raise TypeError("native batch contains a non-native receipt")
        Ordinal = Receipt.OriginalOrdinal
        if isinstance(Ordinal, bool) or not isinstance(Ordinal, int):
            raise TypeError("native receipt ordinal must be an integer")
        if Ordinal in ReceiptsByOrdinal:
            raise ValueError("native route-batch contains a duplicate ordinal")
        ReceiptsByOrdinal[Ordinal] = Receipt
    if frozenset(ReceiptsByOrdinal) != frozenset(range(len(ExpectedRequests))):
        raise ValueError("native route-batch ordinal set mismatch")

    Results = []
    for Ordinal, ExpectedRequest in enumerate(ExpectedRequests):
        Receipt = ReceiptsByOrdinal[Ordinal]
        if Receipt.ContractVersion != "native-route-batch-outcomes/v1":
            raise ValueError("native route receipt contract mismatch")
        if Receipt.BatchIdentity != ExpectedBatchIdentity:
            raise ValueError("native route receipt batch identity mismatch")
        if Receipt.DeadlineAtMonotonicSeconds != ExpectedDeadlineAtMonotonicSeconds:
            raise ValueError("native route receipt deadline mismatch")
        if Receipt.ContextGraphIdentityAvailability != "Verified":
            raise ValueError("native route receipt context graph identity is unavailable")
        if Receipt.ContextGraphSha256 != ExpectedContextGraphIdentity:
            raise ValueError("native route receipt context graph mismatch")
        if Receipt.RequestKind != ExpectedRequest.RequestKind:
            raise ValueError("native route receipt request kind mismatch")
        if Receipt.RequestId != ExpectedRequest.RequestId:
            raise ValueError("native route receipt request identity mismatch")
        if Receipt.MaximumExpansionCount != ExpectedRequest.MaximumExpansionCount:
            raise ValueError("native route receipt expansion cap mismatch")
        if (
            Receipt.CancellationRequested
            is not ExpectedRequest.CancellationRequestedBeforeStart
        ):
            raise ValueError("native route receipt cancellation snapshot mismatch")
        if Receipt.ImmutableInputSha256 != ExpectedRequest.ImmutableInputSha256:
            raise ValueError("native route receipt immutable input mismatch")
        if Receipt.ImmutableInputIdentityAvailability != "Verified":
            raise ValueError("native route receipt immutable input identity is unavailable")
        if Receipt.CallerEchoIdentityAvailability != "Verified":
            raise ValueError("native route receipt caller source identity is unavailable")
        if Receipt.CallerEchoScopeSha256 != ExpectedRequest.CallerEchoScopeSha256:
            raise ValueError("native route receipt caller source identity mismatch")
        if Receipt.NativePayloadSha256 != ExpectedRequest.NativePayloadSha256:
            raise ValueError("native route receipt request payload mismatch")
        if (
            Receipt.NativePayloadCanonicalJson
            != ExpectedRequest.NativePayloadCanonicalJson
        ):
            raise ValueError("native route receipt request payload bytes mismatch")
        if Receipt.Settled is not True:
            raise ValueError("native receipt is not settled")
        ActualExpansionCount = Receipt.TotalExpansionCount
        if ActualExpansionCount != (
            Receipt.RouteExpansionCount + Receipt.ProofExpansionCount
        ):
            raise ValueError("native receipt expansion accounting mismatch")

        NativePayloadIdentity = _RequireIdentity(
            Receipt.NativePayloadCanonicalJson,
            Receipt.NativePayloadSha256,
            "native request payload",
        )
        CallerSourceIdentity = _RequireIdentity(
            Receipt.CallerEchoScopeCanonicalJson,
            Receipt.CallerEchoScopeSha256,
            "caller source",
        )
        RouteDomainIdentity = _RequireIdentity(
            Receipt.RouteDomainScopeCanonicalJson,
            Receipt.RouteDomainScopeSha256,
            "route domain",
            AllowUnavailable=True,
        )
        ReceiptIdentity = _RequireIdentity(
            Receipt.ReceiptScopeCanonicalJson,
            Receipt.ReceiptScopeSha256,
            "receipt scope",
            AllowUnavailable=True,
        )
        Kind, SearchOutcome, ClaimStrength, CommitEligibility = _ClassifyReceipt(
            Receipt
        )
        if RouteDomainIdentity is None:
            if Receipt.RouteDomainIdentityAvailability not in {
                "UncomputedDueToDeadline",
                "UnavailableUnsupportedInput",
                "ProducerFailure",
            }:
                raise ValueError("native route-domain availability is incoherent")
        elif Receipt.RouteDomainIdentityAvailability != "Verified":
            raise ValueError("verified native route domain has an invalid availability state")
        if ReceiptIdentity is None:
            if Receipt.ReceiptIdentityAvailability not in {
                "DependencyUnavailable",
                "UncomputedDueToDeadline",
            }:
                raise ValueError("native receipt-scope availability is incoherent")
            if Receipt.ReceiptIdentityAvailability == "DependencyUnavailable" and (
                Receipt.ReceiptIdentityDependency is None
            ):
                raise ValueError("native receipt omits its unavailable identity dependency")
        elif (
            Receipt.ReceiptIdentityAvailability != "Verified"
            or Receipt.ReceiptIdentityDependency is not None
        ):
            raise ValueError("verified native receipt scope has invalid availability facts")
        if Kind in {
            NativeRouteResultKind.Routed,
            NativeRouteResultKind.CompleteScopedNoPath,
        } and (RouteDomainIdentity is None or ReceiptIdentity is None):
            raise ValueError("semantic native result lacks its complete execution scope")
        if Kind is NativeRouteResultKind.CancellationIncomplete:
            if (
                Receipt.CancellationAcknowledged is not True
                or Receipt.SearchStopped is not True
                or Receipt.Started is not False
                or ActualExpansionCount != 0
                or Receipt.CleanupDisposition != "NotApplicableNoDispatch"
            ):
                raise ValueError("native cancellation receipt is not coherently settled")

        Proof = Receipt.NoPathProof
        if Proof is not None:
            if (
                Proof.ProofKind != "RequiredAttachmentDisconnectedInRelaxedGraphV1"
                or Proof.Complete is not True
                or Proof.ClaimScope != "OneOriginalRouteRequest"
                or Proof.ExpansionCount != Receipt.ProofExpansionCount
                or Proof.ContextGraphSha256 != ExpectedContextGraphIdentity
                or Proof.RouteDomainScopeSha256 != RouteDomainIdentity
                or Proof.ImmutableInputSha256 != ExpectedRequest.ImmutableInputSha256
                or Proof.ReceiptScopeSha256 != ReceiptIdentity
            ):
                raise ValueError("native no-path proof contract or identity mismatch")
        if ReceiptIdentity is not None:
            ExpectedReceiptScope = [
                "native-route-receipt-scope-v1",
                "native-route-batch-outcomes/v1",
                ExpectedBatchIdentity,
                Ordinal,
                ExpectedRequest.RequestId,
                ExpectedRequest.ImmutableInputSha256,
                ExpectedDeadlineAtMonotonicSeconds,
                ExpectedContextGraphIdentity,
                RouteDomainIdentity,
                ExpectedRequest.CallerEchoScopeSha256,
            ]
            if loads(Receipt.ReceiptScopeCanonicalJson) != ExpectedReceiptScope:
                raise ValueError("native receipt scope does not bind the expected execution")
        ExecutionScope = NativeRouteExecutionScopeV1(
            BatchIdentity=ExpectedBatchIdentity,
            ContextGraphIdentity=ExpectedContextGraphIdentity,
            RouteDomainIdentity=RouteDomainIdentity,
            CallerSourceIdentity=CallerSourceIdentity,
            ImmutableInputIdentity=ExpectedRequest.ImmutableInputSha256,
            ReceiptIdentity=ReceiptIdentity,
            DeadlineAtMonotonicSeconds=ExpectedDeadlineAtMonotonicSeconds,
            NativeRequestPayloadIdentity=NativePayloadIdentity,
        )
        Results.append(
            NativeRouteRequestResultV1(
                RequestIdentity=ExpectedRequest.RequestId,
                NativePayloadIdentity=NativePayloadIdentity,
                NativePayloadCanonicalJson=Receipt.NativePayloadCanonicalJson,
                OriginalOrdinal=Ordinal,
                Kind=Kind,
                SearchOutcome=SearchOutcome,
                ClaimStrength=ClaimStrength,
                CommitEligibility=CommitEligibility,
                Reason=Receipt.TerminalReason,
                ActualExpansionCount=ActualExpansionCount,
                ExpansionCap=Receipt.MaximumExpansionCount,
                ExecutionScope=ExecutionScope,
                Candidate=Receipt.Candidate,
                CompleteScopedNoPathProof=Proof,
                NativeReceipt=Receipt,
            )
        )

    StartedRequestCount = sum(bool(Receipt.Started) for Receipt in ReceiptsByOrdinal.values())
    FoundCount = sum(Result.Kind is NativeRouteResultKind.Routed for Result in Results)
    ProvenNoPathCount = sum(
        Result.Kind is NativeRouteResultKind.CompleteScopedNoPath for Result in Results
    )
    IncompleteCount = len(Results) - FoundCount - ProvenNoPathCount
    AggregateRouteExpansionCount = sum(
        Receipt.RouteExpansionCount for Receipt in ReceiptsByOrdinal.values()
    )
    AggregateProofExpansionCount = sum(
        Receipt.ProofExpansionCount for Receipt in ReceiptsByOrdinal.values()
    )
    if (
        NativeBatch.StartedRequestCount != StartedRequestCount
        or NativeBatch.SettledReceiptCount != len(Results)
        or NativeBatch.FoundCount != FoundCount
        or NativeBatch.ProvenNoPathCount != ProvenNoPathCount
        or NativeBatch.IncompleteCount != IncompleteCount
        or NativeBatch.AggregateRouteExpansionCount != AggregateRouteExpansionCount
        or NativeBatch.AggregateProofExpansionCount != AggregateProofExpansionCount
    ):
        raise ValueError("native batch counters contradict its receipts")
    AggregateExpansionCount = NativeBatch.AggregateExpansionCount
    if AggregateExpansionCount != sum(
        Result.ActualExpansionCount for Result in Results
    ):
        raise ValueError("native batch aggregate expansion count mismatch")
    HasDeadlineReceipt = any(
        Result.Kind is NativeRouteResultKind.DeadlineIncomplete for Result in Results
    )
    if HasDeadlineReceipt and NativeBatch.DeadlineExceeded is not True:
        raise ValueError("native batch deadline status contradicts its receipts")
    return NativeRouteBatchResultV1(
        Results=tuple(Results),
        DeadlineExceeded=NativeBatch.DeadlineExceeded,
        AggregateExpansionCount=AggregateExpansionCount,
        NativeBatch=NativeBatch,
    )


def ExecuteNativeRouteBatchOutcomesV1(
    Context: Any,
    BatchIdentity: str,
    Requests: tuple[Any, ...],
    DeadlineAtMonotonicSeconds: float,
    *,
    Detailed: bool = False,
) -> NativeRouteBatchResultV1:
    """Execute and verify one exact typed native route batch.

    The legacy coarse and detailed wrappers remain available for existing
    callers. Consumers of this boundary must retain ``ExecutionScope`` and
    must never share or reuse a result by ``NativePayloadIdentity`` alone.
    """

    from RedstoneCompiler import RustRouting

    if type(Context) is not RustRouting.RoutingContext:
        raise TypeError("Context must be an exact native RoutingContext")
    if type(Requests) is not tuple:
        raise TypeError("Requests must be an exact immutable tuple")
    if type(Detailed) is not bool:
        raise TypeError("Detailed must be an exact bool")
    ExpectedKind = "DetailedNodesV1" if Detailed else "CoarseColumnsV1"
    ExpectedRequestType = (
        RustRouting.RouteTreeDetailedRequestV1
        if Detailed
        else RustRouting.RouteTreeCoarseRequestV1
    )
    if any(
        type(Request) is not ExpectedRequestType or Request.RequestKind != ExpectedKind
        for Request in Requests
    ):
        raise TypeError("request kind does not match the selected native entrypoint")
    ExpectedContextGraphIdentity = Context.AuthoritativeContextGraphSha256
    Operation = (
        Context.GenerateRouteTreeDetailedBatchOutcomesV1
        if Detailed
        else Context.GenerateRouteTreesBatchOutcomesV1
    )
    NativeBatch = Operation(
        BatchIdentity,
        Requests,
        DeadlineAtMonotonicSeconds,
    )
    return _AdaptNativeRouteBatchOutcomesV1(
        NativeBatch,
        Requests,
        BatchIdentity,
        DeadlineAtMonotonicSeconds,
        ExpectedContextGraphIdentity,
    )

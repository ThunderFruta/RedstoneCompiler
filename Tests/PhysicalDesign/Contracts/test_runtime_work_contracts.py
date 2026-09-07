"""Specification tests for independent N1 runtime-result axes."""

from dataclasses import FrozenInstanceError, replace
import json

import pytest

from PhysicalDesign.Contracts.Runtime import (
    RuntimeCancellationSnapshot,
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeFreshness,
    RuntimeLifecycle,
    RuntimeWorkAuthority,
    RuntimeProofIdentity,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
    RuntimeWorkRequest,
    RuntimeWorkResult,
    RuntimeWorkScope,
)


def _Scope() -> RuntimeWorkScope:
    return RuntimeWorkScope(
        DomainIdentity="domain-alpha",
        DependencyIdentities=("technology-v3", "placement-17"),
    )


def _Request() -> RuntimeWorkRequest:
    return RuntimeWorkRequest(
        TaskIdentity="task-alpha",
        Operation="prepare-symbolic-state",
        Scope=_Scope(),
        Lifecycle=RuntimeLifecycle.Queued,
        Freshness=RuntimeFreshness.Current,
        DeadlineAt=1234.5,
        WorkCap=11,
        Cancellation=RuntimeCancellationSnapshot(
            Requested=False,
            Identity="cancel-alpha",
        ),
    )


def _Authority() -> RuntimeWorkAuthority:
    return RuntimeWorkAuthority(
        WorkDeadlineAt=1234.5,
        CleanupCutoffAt=1234.5,
        ForceTerminationAuthorized=False,
    )


def _PreparedResult() -> RuntimeWorkResult:
    return RuntimeWorkResult(
        TaskIdentity="task-alpha",
        Operation="prepare-symbolic-state",
        Scope=_Scope(),
        SearchOutcome=RuntimeSearchOutcome.Prepared,
        Lifecycle=RuntimeLifecycle.Completed,
        Freshness=RuntimeFreshness.Current,
        ClaimStrength=RuntimeClaimStrength.Complete,
        CommitEligibility=RuntimeCommitEligibility.Ineligible,
        TerminalReason=RuntimeTerminalReason.Prepared,
        CandidateIdentity="candidate-alpha",
        WorkUnits=7,
        Diagnostics=(
            ("Complete", "true"),
            ("Source", "symbolic-worker"),
        ),
    )


def test_runtime_axes_are_independent_and_candidate_is_not_auto_eligible():
    Result = _PreparedResult()

    assert Result.SearchOutcome is RuntimeSearchOutcome.Prepared
    assert Result.Lifecycle is RuntimeLifecycle.Completed
    assert Result.Freshness is RuntimeFreshness.Current
    assert Result.ClaimStrength is RuntimeClaimStrength.Complete
    assert Result.CommitEligibility is RuntimeCommitEligibility.Ineligible
    assert Result.CandidateIdentity == "candidate-alpha"

    with pytest.raises(FrozenInstanceError):
        Result.CommitEligibility = RuntimeCommitEligibility.Eligible


@pytest.mark.parametrize("Freshness", tuple(RuntimeFreshness))
def test_runtime_result_codec_preserves_each_freshness_axis(Freshness):
    Result = replace(_PreparedResult(), Freshness=Freshness)

    Decoded = RuntimeWorkResult.FromDictionary(Result.ToDictionary())

    assert Decoded.Freshness is Freshness
    assert Decoded.ToDictionary()["Freshness"] == Freshness.value
    assert Decoded.CommitEligibility is RuntimeCommitEligibility.Ineligible


@pytest.mark.parametrize("Eligibility", tuple(RuntimeCommitEligibility))
def test_runtime_result_codec_preserves_each_commit_eligibility_axis(
    Eligibility,
):
    Result = replace(_PreparedResult(), CommitEligibility=Eligibility)

    Decoded = RuntimeWorkResult.FromDictionary(Result.ToDictionary())

    assert Decoded.CommitEligibility is Eligibility
    assert Decoded.ToDictionary()["CommitEligibility"] == Eligibility.value
    assert Decoded.Freshness is RuntimeFreshness.Current


def test_runtime_work_request_is_immutable():
    Request = _Request()

    assert Request == RuntimeWorkRequest.FromDictionary(Request.ToDictionary())

    with pytest.raises(FrozenInstanceError):
        Request.WorkCap = 0


def test_runtime_work_authority_is_frozen_snapshot_with_boolean_polarity():
    Authority = _Authority()

    assert Authority.WorkDeadlineAt == 1234.5
    assert Authority.CleanupCutoffAt == 1234.5
    assert Authority.ForceTerminationAuthorized is False
    assert Authority != RuntimeWorkAuthority(
        WorkDeadlineAt=1234.5,
        CleanupCutoffAt=1234.5,
        ForceTerminationAuthorized=True,
    )

    with pytest.raises(FrozenInstanceError):
        Authority.ForceTerminationAuthorized = True


@pytest.mark.parametrize(
    ("Field", "Timestamp"),
    (
        ("WorkDeadlineAt", 123),
        ("WorkDeadlineAt", 9_007_199_254_740_993),
        ("CleanupCutoffAt", 123),
        ("CleanupCutoffAt", 9_007_199_254_740_993),
    ),
)
def test_runtime_work_authority_constructor_rejects_integer_timestamps(Field, Timestamp):
    Authority = _Authority()
    KeywordArguments = {Field: Timestamp}
    with pytest.raises(TypeError):
        replace(Authority, **KeywordArguments)


@pytest.mark.parametrize("Field", ("WorkDeadlineAt", "CleanupCutoffAt"))
def test_runtime_work_authority_rejects_float_subclasses_without_conversion(Field):
    class ConversionTrapFloat(float):
        ConversionCalls = 0

        def __float__(self):
            type(self).ConversionCalls += 1
            return 9999.0

    Trap = ConversionTrapFloat(1234.5)
    KeywordArguments = {
        "WorkDeadlineAt": 1234.5,
        "CleanupCutoffAt": 1000.0,
        "ForceTerminationAuthorized": False,
    }
    KeywordArguments[Field] = Trap

    with pytest.raises(TypeError):
        RuntimeWorkAuthority(**KeywordArguments)
    assert Trap.ConversionCalls == 0

    Document = {
        "SchemaVersion": "runtime-work-authority-v1",
        **KeywordArguments,
    }
    with pytest.raises(TypeError):
        RuntimeWorkAuthority.FromDictionary(Document)
    assert Trap.ConversionCalls == 0


@pytest.mark.parametrize("Field", ("WorkDeadlineAt", "CleanupCutoffAt"))
def test_runtime_work_authority_rejects_custom_numeric_without_conversion(Field):
    class ConversionTrapNumeric:
        ConversionCalls = 0

        def __float__(self):
            type(self).ConversionCalls += 1
            return 9999.0

    Trap = ConversionTrapNumeric()
    KeywordArguments = {
        "WorkDeadlineAt": 1234.5,
        "CleanupCutoffAt": 1000.0,
        "ForceTerminationAuthorized": False,
    }
    KeywordArguments[Field] = Trap

    with pytest.raises(TypeError):
        RuntimeWorkAuthority(**KeywordArguments)
    assert Trap.ConversionCalls == 0

    Document = {
        "SchemaVersion": "runtime-work-authority-v1",
        **KeywordArguments,
    }
    with pytest.raises(TypeError):
        RuntimeWorkAuthority.FromDictionary(Document)
    assert Trap.ConversionCalls == 0


@pytest.mark.parametrize(
    ("Field", "Timestamp"),
    (
        ("WorkDeadlineAt", 123),
        ("WorkDeadlineAt", 9_007_199_254_740_993),
        ("CleanupCutoffAt", 123),
        ("CleanupCutoffAt", 9_007_199_254_740_993),
    ),
)
def test_runtime_work_authority_decoder_rejects_integer_timestamps(Field, Timestamp):
    Document = _Authority().ToDictionary()
    Document[Field] = Timestamp

    with pytest.raises(TypeError):
        RuntimeWorkAuthority.FromDictionary(Document)


def test_runtime_work_authority_true_false_literal_documents_round_trip():
    FalseDocument = {
        "SchemaVersion": "runtime-work-authority-v1",
        "WorkDeadlineAt": 1234.5,
        "CleanupCutoffAt": 1000.0,
        "ForceTerminationAuthorized": False,
    }
    TrueDocument = {
        "SchemaVersion": "runtime-work-authority-v1",
        "WorkDeadlineAt": 1234.5,
        "CleanupCutoffAt": 1000.0,
        "ForceTerminationAuthorized": True,
    }

    DecodedFalse = RuntimeWorkAuthority.FromDictionary(FalseDocument)
    DecodedTrue = RuntimeWorkAuthority.FromDictionary(TrueDocument)

    assert DecodedFalse == RuntimeWorkAuthority(
        WorkDeadlineAt=1234.5,
        CleanupCutoffAt=1000.0,
        ForceTerminationAuthorized=False,
    )
    assert DecodedFalse.ToDictionary() == FalseDocument
    assert DecodedTrue == RuntimeWorkAuthority(
        WorkDeadlineAt=1234.5,
        CleanupCutoffAt=1000.0,
        ForceTerminationAuthorized=True,
    )
    assert DecodedTrue.ToDictionary() == TrueDocument


@pytest.mark.parametrize("Force", (0, 1, "true", None))
def test_runtime_work_authority_constructor_rejects_non_boolean_force_authority(Force):
    with pytest.raises(TypeError):
        RuntimeWorkAuthority(
            WorkDeadlineAt=1234.5,
            CleanupCutoffAt=1000.0,
            ForceTerminationAuthorized=Force,
        )


@pytest.mark.parametrize(
    "Mutation",
    (
        lambda Document: Document.pop("SchemaVersion"),
        lambda Document: Document.pop("WorkDeadlineAt"),
        lambda Document: Document.pop("CleanupCutoffAt"),
        lambda Document: Document.__setitem__("ForceTerminationAuthorized", 0),
        lambda Document: Document.__setitem__("WorkDeadlineAt", None),
        lambda Document: Document.__setitem__("CleanupCutoffAt", "never"),
        lambda Document: Document.__setitem__("Unexpected", True),
    ),
)
def test_runtime_work_authority_rejects_malformed_documents(Mutation):
    Document = _Authority().ToDictionary()
    Mutation(Document)

    with pytest.raises((TypeError, ValueError)):
        RuntimeWorkAuthority.FromDictionary(Document)


@pytest.mark.parametrize("Scheme", ("runtime-work-authority-v0", "runtime-work-request-v1"))
def test_runtime_work_authority_rejects_unknown_schema_version(Scheme):
    Document = _Authority().ToDictionary()
    Document["SchemaVersion"] = Scheme

    with pytest.raises(ValueError):
        RuntimeWorkAuthority.FromDictionary(Document)


def test_runtime_work_authority_accepts_equal_and_expired_cutoff_values():
    assert RuntimeWorkAuthority(
        WorkDeadlineAt=500.0,
        CleanupCutoffAt=500.0,
        ForceTerminationAuthorized=True,
    ) == RuntimeWorkAuthority.FromDictionary(
        {
            "SchemaVersion": "runtime-work-authority-v1",
            "WorkDeadlineAt": 500.0,
            "CleanupCutoffAt": 500.0,
            "ForceTerminationAuthorized": True,
        }
    )

    assert RuntimeWorkAuthority(
        WorkDeadlineAt=1000.0,
        CleanupCutoffAt=999.0,
        ForceTerminationAuthorized=False,
    ) == RuntimeWorkAuthority.FromDictionary(
        {
            "SchemaVersion": "runtime-work-authority-v1",
            "WorkDeadlineAt": 1000.0,
            "CleanupCutoffAt": 999.0,
            "ForceTerminationAuthorized": False,
        }
    )


def test_runtime_work_authority_round_trips_negative_reverse_order_adjacent_floats():
    Document = {
        "SchemaVersion": "runtime-work-authority-v1",
        "WorkDeadlineAt": -1.0000000000000002,
        "CleanupCutoffAt": -1.0,
        "ForceTerminationAuthorized": True,
    }

    Decoded = RuntimeWorkAuthority.FromDictionary(Document)

    assert Decoded.WorkDeadlineAt.hex() == "-0x1.0000000000001p+0"
    assert Decoded.CleanupCutoffAt.hex() == "-0x1.0000000000000p+0"
    assert Decoded.WorkDeadlineAt < Decoded.CleanupCutoffAt
    assert Decoded.ToDictionary() == Document


def test_runtime_work_authority_rejects_non_finite_cutoff_values():
    with pytest.raises(TypeError):
        RuntimeWorkAuthority(
            WorkDeadlineAt=float("inf"),
            CleanupCutoffAt=10.0,
            ForceTerminationAuthorized=False,
        )
    with pytest.raises(TypeError):
        RuntimeWorkAuthority(
            WorkDeadlineAt=1.0,
            CleanupCutoffAt=float("nan"),
            ForceTerminationAuthorized=False,
        )
    with pytest.raises(TypeError):
        RuntimeWorkAuthority(
            WorkDeadlineAt=float("nan"),
            CleanupCutoffAt=1.0,
            ForceTerminationAuthorized=False,
        )


def test_runtime_work_request_legacy_schema_round_trip_without_authority_keys():
    LegacyDocument = {
        "SchemaVersion": "runtime-work-request-v1",
        "TaskIdentity": "task-legacy",
        "Operation": "prepare-symbolic-state",
        "Scope": _Scope().ToDictionary(),
        "Lifecycle": RuntimeLifecycle.Queued.value,
        "Freshness": RuntimeFreshness.Current.value,
        "DeadlineAt": 5678.5,
        "WorkCap": 9,
        "Cancellation": RuntimeCancellationSnapshot(
            Requested=False,
            Identity="cancel-legacy",
        ).ToDictionary(),
    }

    assert RuntimeWorkRequest.FromDictionary(LegacyDocument) == RuntimeWorkRequest(
        TaskIdentity="task-legacy",
        Operation="prepare-symbolic-state",
        Scope=_Scope(),
        Lifecycle=RuntimeLifecycle.Queued,
        Freshness=RuntimeFreshness.Current,
        DeadlineAt=5678.5,
        WorkCap=9,
        Cancellation=RuntimeCancellationSnapshot(
            Requested=False,
            Identity="cancel-legacy",
        ),
    )
    assert "WorkDeadlineAt" not in LegacyDocument
    assert "CleanupCutoffAt" not in RuntimeWorkRequest.FromDictionary(LegacyDocument).ToDictionary()


def test_unresolved_work_cannot_carry_an_infeasibility_proof():
    Proof = RuntimeProofIdentity(
        ProofIdentity="proof-alpha",
        Scope=_Scope(),
    )

    with pytest.raises(ValueError, match="unresolved.*proof"):
        replace(
            _PreparedResult(),
            SearchOutcome=RuntimeSearchOutcome.Unresolved,
            ClaimStrength=RuntimeClaimStrength.Continuation,
            TerminalReason=RuntimeTerminalReason.WorkCapExhausted,
            CandidateIdentity=None,
            ProofIdentity=Proof,
        )


@pytest.mark.parametrize(
    "Reason",
    (
        RuntimeTerminalReason.AdmissionRejected,
        RuntimeTerminalReason.PayloadLimitExceeded,
        RuntimeTerminalReason.ResultLimitExceeded,
    ),
)
def test_spawned_admission_failures_round_trip_as_unresolved(Reason):
    Result = replace(
        _PreparedResult(),
        SearchOutcome=RuntimeSearchOutcome.Unresolved,
        ClaimStrength=RuntimeClaimStrength.Continuation,
        TerminalReason=Reason,
        CandidateIdentity=None,
    )

    Decoded = RuntimeWorkResult.FromDictionary(Result.ToDictionary())

    assert Decoded.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Decoded.TerminalReason is Reason
    assert Decoded.CommitEligibility is RuntimeCommitEligibility.Ineligible
    assert Decoded.ProofIdentity is None


def test_infeasible_requires_a_complete_proof_over_the_exact_scope():
    OtherScope = RuntimeWorkScope(
        DomainIdentity="domain-beta",
        DependencyIdentities=_Scope().DependencyIdentities,
    )
    WrongProof = RuntimeProofIdentity(
        ProofIdentity="proof-beta",
        Scope=OtherScope,
    )

    with pytest.raises(ValueError, match="exact work scope"):
        replace(
            _PreparedResult(),
            SearchOutcome=RuntimeSearchOutcome.Infeasible,
            ClaimStrength=RuntimeClaimStrength.InfeasibilityProof,
            TerminalReason=RuntimeTerminalReason.InfeasibilityProven,
            CandidateIdentity=None,
            ProofIdentity=WrongProof,
        )


def test_complete_exact_scope_proof_expresses_infeasible_without_candidate():
    Proof = RuntimeProofIdentity(
        ProofIdentity="proof-alpha",
        Scope=_Scope(),
    )
    Result = replace(
        _PreparedResult(),
        SearchOutcome=RuntimeSearchOutcome.Infeasible,
        ClaimStrength=RuntimeClaimStrength.InfeasibilityProof,
        CommitEligibility=RuntimeCommitEligibility.Ineligible,
        TerminalReason=RuntimeTerminalReason.InfeasibilityProven,
        CandidateIdentity=None,
        ProofIdentity=Proof,
    )

    assert Result.SearchOutcome is RuntimeSearchOutcome.Infeasible
    assert Result.ProofIdentity == Proof
    assert Result.CandidateIdentity is None


def test_runtime_work_documents_round_trip_canonically():
    Request = _Request()
    RequestDocument = Request.ToDictionary()
    ResultDocument = _PreparedResult().ToDictionary()

    assert RuntimeWorkRequest.FromDictionary(RequestDocument) == Request
    assert RuntimeWorkResult.FromDictionary(ResultDocument) == _PreparedResult()
    assert json.dumps(
        ResultDocument,
        sort_keys=True,
        separators=(",", ":"),
    ) == json.dumps(
        RuntimeWorkResult.FromDictionary(ResultDocument).ToDictionary(),
        sort_keys=True,
        separators=(",", ":"),
    )


@pytest.mark.parametrize(
    "Mutation",
    (
        lambda Document: Document.pop("SearchOutcome"),
        lambda Document: Document.__setitem__("Lifecycle", "Cancelled"),
        lambda Document: Document.__setitem__("Unexpected", True),
        lambda Document: Document.__setitem__("WorkUnits", -1),
    ),
)
def test_runtime_result_codec_rejects_corrupt_documents(Mutation):
    Document = _PreparedResult().ToDictionary()
    Mutation(Document)

    with pytest.raises((TypeError, ValueError)):
        RuntimeWorkResult.FromDictionary(Document)


def test_cancellation_request_is_not_a_completed_termination():
    Request = RuntimeWorkRequest(
        TaskIdentity="task-cancel",
        Operation="prepare-symbolic-state",
        Scope=_Scope(),
        Lifecycle=RuntimeLifecycle.CancellationRequested,
        Freshness=RuntimeFreshness.Current,
        DeadlineAt=1234.5,
        WorkCap=1,
        Cancellation=RuntimeCancellationSnapshot(
            Requested=True,
            Identity="cancel-requested",
            Reason="caller-requested",
        ),
    )

    assert Request.Lifecycle is RuntimeLifecycle.CancellationRequested
    assert Request.Lifecycle not in {
        RuntimeLifecycle.TerminatedGracefully,
        RuntimeLifecycle.TerminatedForced,
    }

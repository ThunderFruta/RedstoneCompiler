"""Immutable work, lifecycle, and outcome contracts for bounded routing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Generic, TypeVar


class RuntimeSearchOutcome(str, Enum):
    Prepared = "Prepared"
    Infeasible = "Infeasible"
    Unresolved = "Unresolved"


class RuntimeLifecycle(str, Enum):
    Queued = "Queued"
    Running = "Running"
    Yielding = "Yielding"
    Paused = "Paused"
    CancellationRequested = "CancellationRequested"
    TerminationRequested = "TerminationRequested"
    Completed = "Completed"
    TerminatedGracefully = "TerminatedGracefully"
    TerminatedForced = "TerminatedForced"
    Failed = "Failed"


class RuntimeFreshness(str, Enum):
    Current = "Current"
    StaleUnreviewed = "StaleUnreviewed"
    Salvaged = "Salvaged"
    SalvageRejected = "SalvageRejected"


class RuntimeClaimStrength(str, Enum):
    Candidate = "Candidate"
    Feasible = "Feasible"
    Complete = "Complete"
    Optimal = "Optimal"
    InfeasibilityProof = "InfeasibilityProof"
    Continuation = "Continuation"


class RuntimeCommitEligibility(str, Enum):
    Ineligible = "Ineligible"
    RevalidationRequired = "RevalidationRequired"
    Eligible = "Eligible"


class RuntimeTerminalReason(str, Enum):
    Prepared = "Prepared"
    InfeasibilityProven = "InfeasibilityProven"
    AdmissionRejected = "AdmissionRejected"
    PayloadLimitExceeded = "PayloadLimitExceeded"
    ResultLimitExceeded = "ResultLimitExceeded"
    DeadlineExhausted = "DeadlineExhausted"
    WorkCapExhausted = "WorkCapExhausted"
    Cancelled = "Cancelled"
    IncompleteExploration = "IncompleteExploration"
    IncompleteInput = "IncompleteInput"
    WorkerFailure = "WorkerFailure"


def _RequireExactKeys(
    Document: Mapping[str, object],
    Expected: frozenset[str],
    Name: str,
) -> None:
    Actual = frozenset(map(str, Document.keys()))
    if Actual != Expected:
        Missing = ", ".join(sorted(Expected - Actual))
        Unexpected = ", ".join(sorted(Actual - Expected))
        raise ValueError(
            f"invalid {Name} fields; missing=[{Missing}] "
            f"unexpected=[{Unexpected}]"
        )


def _RequireText(Value: object, Name: str, *, AllowEmpty: bool = False) -> str:
    if not isinstance(Value, str) or (not AllowEmpty and not Value):
        raise TypeError(f"{Name} must be a non-empty string")
    return Value


def _RequireInteger(Value: object, Name: str, *, Minimum: int) -> int:
    if isinstance(Value, bool) or not isinstance(Value, int) or Value < Minimum:
        raise TypeError(f"{Name} must be an integer >= {Minimum}")
    return Value


@dataclass(frozen=True)
class RuntimeWorkScope:
    """The exact domain and dependencies to which a result applies."""

    DomainIdentity: str
    DependencyIdentities: tuple[str, ...]

    def __post_init__(self) -> None:
        _RequireText(self.DomainIdentity, "DomainIdentity")
        Dependencies = tuple(sorted(self.DependencyIdentities))
        if any(not isinstance(Value, str) or not Value for Value in Dependencies):
            raise TypeError("DependencyIdentities must contain non-empty strings")
        if len(set(Dependencies)) != len(Dependencies):
            raise ValueError("DependencyIdentities must be unique")
        object.__setattr__(self, "DependencyIdentities", Dependencies)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "DomainIdentity": self.DomainIdentity,
            "DependencyIdentities": list(self.DependencyIdentities),
        }

    @classmethod
    def FromDictionary(cls, Document: object) -> "RuntimeWorkScope":
        if not isinstance(Document, Mapping):
            raise TypeError("runtime work scope must be a mapping")
        _RequireExactKeys(
            Document,
            frozenset(("DomainIdentity", "DependencyIdentities")),
            "runtime work scope",
        )
        Dependencies = Document["DependencyIdentities"]
        if not isinstance(Dependencies, list):
            raise TypeError("DependencyIdentities must be a list")
        return cls(
            DomainIdentity=_RequireText(
                Document["DomainIdentity"],
                "DomainIdentity",
            ),
            DependencyIdentities=tuple(
                _RequireText(Value, "DependencyIdentity")
                for Value in Dependencies
            ),
        )


@dataclass(frozen=True)
class RuntimeProofIdentity:
    """Identity of one complete proof, bound to its exact scope."""

    ProofIdentity: str
    Scope: RuntimeWorkScope

    def __post_init__(self) -> None:
        _RequireText(self.ProofIdentity, "ProofIdentity")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProofIdentity": self.ProofIdentity,
            "Scope": self.Scope.ToDictionary(),
        }

    @classmethod
    def FromDictionary(cls, Document: object) -> "RuntimeProofIdentity":
        if not isinstance(Document, Mapping):
            raise TypeError("runtime proof identity must be a mapping")
        _RequireExactKeys(
            Document,
            frozenset(("ProofIdentity", "Scope")),
            "runtime proof identity",
        )
        return cls(
            ProofIdentity=_RequireText(
                Document["ProofIdentity"],
                "ProofIdentity",
            ),
            Scope=RuntimeWorkScope.FromDictionary(Document["Scope"]),
        )


@dataclass(frozen=True)
class RuntimeCancellationSnapshot:
    """Immutable observation of a cancellation contract at dispatch."""

    Requested: bool
    Identity: str
    Reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.Requested, bool):
            raise TypeError("Requested must be a boolean")
        _RequireText(self.Identity, "Identity")
        _RequireText(self.Reason, "Reason", AllowEmpty=True)
        if not self.Requested and self.Reason:
            raise ValueError("an unrequested cancellation cannot carry a reason")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Requested": self.Requested,
            "Identity": self.Identity,
            "Reason": self.Reason,
        }

    @classmethod
    def FromDictionary(cls, Document: object) -> "RuntimeCancellationSnapshot":
        if not isinstance(Document, Mapping):
            raise TypeError("runtime cancellation snapshot must be a mapping")
        _RequireExactKeys(
            Document,
            frozenset(("Requested", "Identity", "Reason")),
            "runtime cancellation snapshot",
        )
        Requested = Document["Requested"]
        if not isinstance(Requested, bool):
            raise TypeError("Requested must be a boolean")
        return cls(
            Requested=Requested,
            Identity=_RequireText(Document["Identity"], "Identity"),
            Reason=_RequireText(
                Document["Reason"],
                "Reason",
                AllowEmpty=True,
            ),
        )


@dataclass(frozen=True)
class RuntimeWorkAuthority:
    """Immutable authority snapshot for deadline, cleanup, and force policy."""

    SchemaVersion = "runtime-work-authority-v1"

    WorkDeadlineAt: float
    CleanupCutoffAt: float
    ForceTerminationAuthorized: bool

    def __post_init__(self) -> None:
        if (
            type(self.WorkDeadlineAt) is not float
            or not isfinite(self.WorkDeadlineAt)
        ):
            raise TypeError("WorkDeadlineAt must be a finite absolute timestamp")
        if (
            type(self.CleanupCutoffAt) is not float
            or not isfinite(self.CleanupCutoffAt)
        ):
            raise TypeError("CleanupCutoffAt must be a finite absolute timestamp")
        if type(self.ForceTerminationAuthorized) is not bool:
            raise TypeError("ForceTerminationAuthorized must be a boolean")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "WorkDeadlineAt": self.WorkDeadlineAt,
            "CleanupCutoffAt": self.CleanupCutoffAt,
            "ForceTerminationAuthorized": self.ForceTerminationAuthorized,
        }

    @classmethod
    def FromDictionary(cls, Document: object) -> "RuntimeWorkAuthority":
        if not isinstance(Document, Mapping):
            raise TypeError("runtime work authority must be a mapping")
        _RequireExactKeys(
            Document,
            frozenset((
                "SchemaVersion",
                "WorkDeadlineAt",
                "CleanupCutoffAt",
                "ForceTerminationAuthorized",
            )),
            "runtime work authority",
        )
        if Document["SchemaVersion"] != cls.SchemaVersion:
            raise ValueError("unsupported runtime work authority schema")
        WorkDeadlineAt = Document["WorkDeadlineAt"]
        if type(WorkDeadlineAt) is not float:
            raise TypeError("WorkDeadlineAt must be a finite absolute timestamp")
        CleanupCutoffAt = Document["CleanupCutoffAt"]
        if type(CleanupCutoffAt) is not float:
            raise TypeError("CleanupCutoffAt must be a finite absolute timestamp")
        ForceTerminationAuthorized = Document["ForceTerminationAuthorized"]
        if type(ForceTerminationAuthorized) is not bool:
            raise TypeError("ForceTerminationAuthorized must be a boolean")
        return cls(
            WorkDeadlineAt=WorkDeadlineAt,
            CleanupCutoffAt=CleanupCutoffAt,
            ForceTerminationAuthorized=ForceTerminationAuthorized,
        )


@dataclass(frozen=True)
class RuntimeWorkRequest:
    """One immutable bounded work item carrying an absolute deadline."""

    SchemaVersion = "runtime-work-request-v1"

    TaskIdentity: str
    Operation: str
    Scope: RuntimeWorkScope
    Lifecycle: RuntimeLifecycle
    Freshness: RuntimeFreshness
    DeadlineAt: float
    WorkCap: int
    Cancellation: RuntimeCancellationSnapshot

    def __post_init__(self) -> None:
        _RequireText(self.TaskIdentity, "TaskIdentity")
        _RequireText(self.Operation, "Operation")
        if self.Lifecycle not in {
            RuntimeLifecycle.Queued,
            RuntimeLifecycle.CancellationRequested,
        }:
            raise ValueError("a work request must be queued or cancellation-requested")
        if self.Cancellation.Requested != (
            self.Lifecycle is RuntimeLifecycle.CancellationRequested
        ):
            raise ValueError("request lifecycle and cancellation snapshot disagree")
        if isinstance(self.DeadlineAt, bool) or not isinstance(
            self.DeadlineAt,
            int | float,
        ) or not isfinite(float(self.DeadlineAt)):
            raise TypeError("DeadlineAt must be a finite absolute timestamp")
        _RequireInteger(self.WorkCap, "WorkCap", Minimum=0)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "TaskIdentity": self.TaskIdentity,
            "Operation": self.Operation,
            "Scope": self.Scope.ToDictionary(),
            "Lifecycle": self.Lifecycle.value,
            "Freshness": self.Freshness.value,
            "DeadlineAt": float(self.DeadlineAt),
            "WorkCap": self.WorkCap,
            "Cancellation": self.Cancellation.ToDictionary(),
        }

    @classmethod
    def FromDictionary(cls, Document: object) -> "RuntimeWorkRequest":
        if not isinstance(Document, Mapping):
            raise TypeError("runtime work request must be a mapping")
        _RequireExactKeys(
            Document,
            frozenset((
                "SchemaVersion",
                "TaskIdentity",
                "Operation",
                "Scope",
                "Lifecycle",
                "Freshness",
                "DeadlineAt",
                "WorkCap",
                "Cancellation",
            )),
            "runtime work request",
        )
        if Document["SchemaVersion"] != cls.SchemaVersion:
            raise ValueError("unsupported runtime work request schema")
        DeadlineAt = Document["DeadlineAt"]
        if isinstance(DeadlineAt, bool) or not isinstance(DeadlineAt, int | float):
            raise TypeError("DeadlineAt must be numeric")
        return cls(
            TaskIdentity=_RequireText(Document["TaskIdentity"], "TaskIdentity"),
            Operation=_RequireText(Document["Operation"], "Operation"),
            Scope=RuntimeWorkScope.FromDictionary(Document["Scope"]),
            Lifecycle=RuntimeLifecycle(Document["Lifecycle"]),
            Freshness=RuntimeFreshness(Document["Freshness"]),
            DeadlineAt=float(DeadlineAt),
            WorkCap=_RequireInteger(Document["WorkCap"], "WorkCap", Minimum=0),
            Cancellation=RuntimeCancellationSnapshot.FromDictionary(
                Document["Cancellation"]
            ),
        )


def _NormalizeDiagnostics(
    Diagnostics: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    Normalized = tuple(sorted(Diagnostics))
    if any(
        not isinstance(Name, str)
        or not Name
        or not isinstance(Value, str)
        for Name, Value in Normalized
    ):
        raise TypeError("Diagnostics must contain non-empty names and string values")
    if len({Name for Name, _Value in Normalized}) != len(Normalized):
        raise ValueError("Diagnostics names must be unique")
    return Normalized


@dataclass(frozen=True)
class RuntimeWorkResult:
    """Portable terminal result with independent N1 state axes."""

    SchemaVersion = "runtime-work-result-v1"

    TaskIdentity: str
    Operation: str
    Scope: RuntimeWorkScope
    SearchOutcome: RuntimeSearchOutcome
    Lifecycle: RuntimeLifecycle
    Freshness: RuntimeFreshness
    ClaimStrength: RuntimeClaimStrength
    CommitEligibility: RuntimeCommitEligibility
    TerminalReason: RuntimeTerminalReason
    CandidateIdentity: str | None = None
    ProofIdentity: RuntimeProofIdentity | None = None
    WorkUnits: int = 0
    Diagnostics: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _RequireText(self.TaskIdentity, "TaskIdentity")
        _RequireText(self.Operation, "Operation")
        if self.Lifecycle not in {
            RuntimeLifecycle.Completed,
            RuntimeLifecycle.TerminatedGracefully,
            RuntimeLifecycle.TerminatedForced,
            RuntimeLifecycle.Failed,
        }:
            raise ValueError("a runtime result must have a terminal lifecycle")
        if self.CandidateIdentity is not None:
            _RequireText(self.CandidateIdentity, "CandidateIdentity")
        _RequireInteger(self.WorkUnits, "WorkUnits", Minimum=0)
        object.__setattr__(
            self,
            "Diagnostics",
            _NormalizeDiagnostics(self.Diagnostics),
        )
        if self.SearchOutcome is RuntimeSearchOutcome.Prepared:
            if self.CandidateIdentity is None:
                raise ValueError("prepared work requires a candidate identity")
            if self.ProofIdentity is not None:
                raise ValueError("prepared work cannot carry an infeasibility proof")
            if self.ClaimStrength in {
                RuntimeClaimStrength.InfeasibilityProof,
                RuntimeClaimStrength.Continuation,
            }:
                raise ValueError("prepared work has invalid claim strength")
            if self.TerminalReason is not RuntimeTerminalReason.Prepared:
                raise ValueError("prepared work requires the prepared terminal reason")
            if self.Lifecycle is not RuntimeLifecycle.Completed:
                raise ValueError("prepared work must complete normally")
        elif self.SearchOutcome is RuntimeSearchOutcome.Infeasible:
            if self.ProofIdentity is None:
                raise ValueError("infeasible work requires a complete scoped proof")
            if self.ProofIdentity.Scope != self.Scope:
                raise ValueError("infeasibility proof must match the exact work scope")
            if self.CandidateIdentity is not None:
                raise ValueError("infeasible work cannot carry a candidate")
            if self.ClaimStrength is not RuntimeClaimStrength.InfeasibilityProof:
                raise ValueError("infeasible work requires proof claim strength")
            if self.TerminalReason is not RuntimeTerminalReason.InfeasibilityProven:
                raise ValueError("infeasible work requires the proven terminal reason")
            if self.Lifecycle is not RuntimeLifecycle.Completed:
                raise ValueError("infeasibility proof must complete normally")
            if self.CommitEligibility is not RuntimeCommitEligibility.Ineligible:
                raise ValueError("an infeasibility proof is not a committable candidate")
        else:
            if self.ProofIdentity is not None:
                raise ValueError("unresolved work cannot carry an infeasibility proof")
            if self.ClaimStrength is RuntimeClaimStrength.InfeasibilityProof:
                raise ValueError("unresolved work cannot claim infeasibility")
            if self.CommitEligibility is RuntimeCommitEligibility.Eligible:
                raise ValueError("unresolved work cannot be commit eligible")
            if self.TerminalReason in {
                RuntimeTerminalReason.Prepared,
                RuntimeTerminalReason.InfeasibilityProven,
            }:
                raise ValueError("unresolved work requires an unresolved terminal reason")
        if (
            self.TerminalReason is RuntimeTerminalReason.WorkerFailure
            and self.Lifecycle is not RuntimeLifecycle.Failed
        ):
            raise ValueError("worker failure requires the failed lifecycle")
        if (
            self.Lifecycle is RuntimeLifecycle.Failed
            and self.TerminalReason is not RuntimeTerminalReason.WorkerFailure
        ):
            raise ValueError("the failed lifecycle requires worker failure")
        if (
            self.TerminalReason is RuntimeTerminalReason.Cancelled
            and self.Lifecycle not in {
                RuntimeLifecycle.TerminatedGracefully,
                RuntimeLifecycle.TerminatedForced,
            }
        ):
            raise ValueError("cancelled work requires acknowledged termination")
        if (
            self.Lifecycle in {
                RuntimeLifecycle.TerminatedGracefully,
                RuntimeLifecycle.TerminatedForced,
            }
            and self.TerminalReason is not RuntimeTerminalReason.Cancelled
        ):
            raise ValueError("terminated work requires the cancelled reason")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "TaskIdentity": self.TaskIdentity,
            "Operation": self.Operation,
            "Scope": self.Scope.ToDictionary(),
            "SearchOutcome": self.SearchOutcome.value,
            "Lifecycle": self.Lifecycle.value,
            "Freshness": self.Freshness.value,
            "ClaimStrength": self.ClaimStrength.value,
            "CommitEligibility": self.CommitEligibility.value,
            "TerminalReason": self.TerminalReason.value,
            "CandidateIdentity": self.CandidateIdentity,
            "ProofIdentity": (
                None
                if self.ProofIdentity is None
                else self.ProofIdentity.ToDictionary()
            ),
            "WorkUnits": self.WorkUnits,
            "Diagnostics": [list(Value) for Value in self.Diagnostics],
        }

    @classmethod
    def FromDictionary(cls, Document: object) -> "RuntimeWorkResult":
        if not isinstance(Document, Mapping):
            raise TypeError("runtime work result must be a mapping")
        _RequireExactKeys(
            Document,
            frozenset((
                "SchemaVersion",
                "TaskIdentity",
                "Operation",
                "Scope",
                "SearchOutcome",
                "Lifecycle",
                "Freshness",
                "ClaimStrength",
                "CommitEligibility",
                "TerminalReason",
                "CandidateIdentity",
                "ProofIdentity",
                "WorkUnits",
                "Diagnostics",
            )),
            "runtime work result",
        )
        if Document["SchemaVersion"] != cls.SchemaVersion:
            raise ValueError("unsupported runtime work result schema")
        CandidateIdentity = Document["CandidateIdentity"]
        if CandidateIdentity is not None:
            CandidateIdentity = _RequireText(
                CandidateIdentity,
                "CandidateIdentity",
            )
        RawDiagnostics = Document["Diagnostics"]
        if not isinstance(RawDiagnostics, list):
            raise TypeError("Diagnostics must be a list")
        Diagnostics = []
        for Value in RawDiagnostics:
            if not isinstance(Value, list) or len(Value) != 2:
                raise TypeError("each diagnostic must be a two-item list")
            Diagnostics.append((
                _RequireText(Value[0], "DiagnosticName"),
                _RequireText(Value[1], "DiagnosticValue", AllowEmpty=True),
            ))
        return cls(
            TaskIdentity=_RequireText(Document["TaskIdentity"], "TaskIdentity"),
            Operation=_RequireText(Document["Operation"], "Operation"),
            Scope=RuntimeWorkScope.FromDictionary(Document["Scope"]),
            SearchOutcome=RuntimeSearchOutcome(Document["SearchOutcome"]),
            Lifecycle=RuntimeLifecycle(Document["Lifecycle"]),
            Freshness=RuntimeFreshness(Document["Freshness"]),
            ClaimStrength=RuntimeClaimStrength(Document["ClaimStrength"]),
            CommitEligibility=RuntimeCommitEligibility(
                Document["CommitEligibility"]
            ),
            TerminalReason=RuntimeTerminalReason(Document["TerminalReason"]),
            CandidateIdentity=CandidateIdentity,
            ProofIdentity=(
                None
                if Document["ProofIdentity"] is None
                else RuntimeProofIdentity.FromDictionary(
                    Document["ProofIdentity"]
                )
            ),
            WorkUnits=_RequireInteger(
                Document["WorkUnits"],
                "WorkUnits",
                Minimum=0,
            ),
            Diagnostics=tuple(Diagnostics),
        )


Payload = TypeVar("Payload")


@dataclass(frozen=True)
class RuntimeWorkProduct(Generic[Payload]):
    """Producer classification paired with a process-local payload."""

    Value: Payload | None
    SearchOutcome: RuntimeSearchOutcome
    ClaimStrength: RuntimeClaimStrength
    TerminalReason: RuntimeTerminalReason
    CandidateIdentity: str | None = None
    ProofIdentity: RuntimeProofIdentity | None = None
    Diagnostics: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.CandidateIdentity is not None:
            _RequireText(self.CandidateIdentity, "CandidateIdentity")
        object.__setattr__(
            self,
            "Diagnostics",
            _NormalizeDiagnostics(self.Diagnostics),
        )


@dataclass(frozen=True)
class RuntimeWorkExecution(Generic[Payload]):
    """A portable result and its non-serialized process-local value."""

    Result: RuntimeWorkResult
    Value: Payload | None

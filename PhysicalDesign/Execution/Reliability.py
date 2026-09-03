"""Shared bounded-execution and retry-state contracts for physical routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from time import monotonic
from typing import Any

from ..Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError


def BuildStableFingerprint(Value: Any) -> str:
    """Return a deterministic short fingerprint for diagnostics and stagnation."""
    Encoded = json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(Encoded).hexdigest()[:16]


@dataclass(frozen=True)
class RoutingDeadline:
    """One absolute deadline shared by placement and every routing stage."""

    StartedAt: float
    ExpiresAt: float
    ExpirationKind: str = "OverallDeadlineExpired"

    def __post_init__(self) -> None:
        if self.ExpirationKind not in {
            "OverallDeadlineExpired",
            "StageReserveExpired",
        }:
            raise ValueError("invalid routing deadline expiration kind")

    @classmethod
    def Start(cls, MaximumRuntimeSeconds: float) -> "RoutingDeadline":
        if MaximumRuntimeSeconds <= 0:
            raise ValueError("MaximumRuntimeSeconds must be positive")
        StartedAt = monotonic()
        return cls(
            StartedAt=StartedAt,
            ExpiresAt=StartedAt + MaximumRuntimeSeconds,
        )

    def ElapsedSeconds(self) -> float:
        return max(0.0, monotonic() - self.StartedAt)

    def RemainingSeconds(self) -> float:
        return max(0.0, self.ExpiresAt - monotonic())

    def RemainingMilliseconds(self) -> int:
        Remaining = self.RemainingSeconds()
        if Remaining <= 0:
            return 0
        return max(1, int(Remaining * 1000))

    def IsExpired(self) -> bool:
        return monotonic() >= self.ExpiresAt

    def RaiseIfExpired(
        self,
        Stage: str,
        Diagnostics: dict[str, object] | None = None,
    ) -> None:
        if not self.IsExpired():
            return
        DeadlineDiagnostics = dict(Diagnostics or {})
        DeadlineDiagnostics["Deadline"] = self.ToDictionary()
        DeadlineDiagnostics["DeadlineExpirationKind"] = (
            self.ExpirationKind
        )
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.RuntimeBudgetExceeded,
                Stage=Stage,
                Detail=(
                    (
                        "routing stage reserve expired after "
                        if self.ExpirationKind == "StageReserveExpired"
                        else "overall routing deadline expired after "
                    )
                    +
                    f"{self.ElapsedSeconds():.3f}s"
                ),
                Diagnostics=DeadlineDiagnostics,
            )
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ElapsedSeconds": round(self.ElapsedSeconds(), 6),
            "RemainingMilliseconds": self.RemainingMilliseconds(),
            "Expired": self.IsExpired(),
            "ExpirationKind": self.ExpirationKind,
        }


def RemainingRoutingRuntimeMilliseconds(
    Deadline: RoutingDeadline,
    AdaptiveExpiresAt: float,
) -> int:
    """Return the tighter remaining allowance without replacing Deadline."""
    RemainingSeconds = min(
        Deadline.RemainingSeconds(),
        max(0.0, AdaptiveExpiresAt - monotonic()),
    )
    if RemainingSeconds <= 0:
        return 0
    return max(1, int(RemainingSeconds * 1000))


def EnforceRoutingRuntimeLimit(
    Deadline: RoutingDeadline,
    AdaptiveStartedAt: float,
    AdaptiveExpiresAt: float,
    Stage: str,
    Diagnostics: dict[str, object] | None = None,
    NativeDeadlineExceeded: bool = False,
) -> None:
    """Distinguish one placement's slice from the shared absolute deadline."""
    Current = monotonic()
    FailureDiagnostics = dict(Diagnostics or {})
    FailureDiagnostics["AdaptiveDeadline"] = {
        "RuntimeBudgetSeconds": round(
            max(0.0, AdaptiveExpiresAt - AdaptiveStartedAt),
            6,
        ),
        "ElapsedSeconds": round(max(0.0, Current - AdaptiveStartedAt), 6),
        "RemainingMilliseconds": max(
            0,
            int(max(0.0, AdaptiveExpiresAt - Current) * 1000),
        ),
        "Expired": Current >= AdaptiveExpiresAt,
        "LimitedByGlobalDeadline": AdaptiveExpiresAt >= Deadline.ExpiresAt,
    }
    FailureDiagnostics.setdefault("Deadline", Deadline.ToDictionary())
    # The shared deadline always wins when both limits have expired.
    Deadline.RaiseIfExpired(Stage, FailureDiagnostics)
    AdaptiveIsActiveLimit = AdaptiveExpiresAt < Deadline.ExpiresAt
    if Current >= AdaptiveExpiresAt or (
        NativeDeadlineExceeded and AdaptiveIsActiveLimit
    ):
        FailureDiagnostics.update({
            "Action": "advance-placement-adaptive-slice-expired",
            "NativeDeadlineExceeded": NativeDeadlineExceeded,
        })
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage=Stage,
                Detail=(
                    "per-placement adaptive runtime slice expired; advance "
                    "to the next retained placement"
                ),
                Diagnostics=FailureDiagnostics,
            )
        )
    if NativeDeadlineExceeded:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.RuntimeBudgetExceeded,
                Stage=Stage,
                Detail=(
                    "native bounded work exhausted the remaining shared "
                    "routing deadline"
                ),
                Diagnostics=FailureDiagnostics,
            )
        )


@dataclass(frozen=True)
class RoutingEscalationState:
    """Effective controls whose changes must produce genuinely new work."""

    PortalMode: str
    ReservationVariant: int
    LaneDiversityLevel: int
    CandidateDiversityLevel: int
    EffectiveRoutingLayers: int
    AssignmentBudget: int
    CandidateFingerprint: str = ""
    ConflictFingerprint: str = ""

    @property
    def EffectiveKey(self) -> tuple[object, ...]:
        return (
            self.PortalMode,
            self.ReservationVariant,
            self.LaneDiversityLevel,
            self.CandidateDiversityLevel,
            self.EffectiveRoutingLayers,
            self.AssignmentBudget,
            self.CandidateFingerprint,
            self.ConflictFingerprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        return asdict(self)


def BuildRoutingDeadlineDiagnostics(
    Deadline: RoutingDeadline,
    WorkTelemetry: dict[str, object],
    EscalationHistory: tuple[dict[str, object], ...],
    EscalationState: RoutingEscalationState,
    StageTimingsSeconds: dict[str, float] | None = None,
    AdditionalDiagnostics: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the complete diagnostic payload for every deadline exit."""
    Diagnostics = dict(WorkTelemetry)
    Diagnostics.update({
        "EscalationHistory": tuple(EscalationHistory),
        "RoutingEscalationState": EscalationState.ToDictionary(),
        "StageTimingsSeconds": {
            Stage: round(Seconds, 6)
            for Stage, Seconds in (StageTimingsSeconds or {}).items()
        },
        "Deadline": Deadline.ToDictionary(),
    })
    # A typed failure can carry a terminal snapshot that is newer than the
    # enclosing planner closure.  Apply it last so a just-appended escalation
    # entry, state transition, or deadline observation is not replaced by the
    # stale defaults captured before the failure was constructed.
    Diagnostics.update(AdditionalDiagnostics or {})
    return Diagnostics


@dataclass(frozen=True)
class RoutingEscalationDecision:
    """One bounded response selected from an observed failure class."""

    Action: str
    Reason: str


def HasAdaptiveEscalationBudget(
    RemainingSeconds: float,
    ObservedPassSeconds: float,
    HasPriorEscalation: bool,
    MaximumReserveSeconds: float = 5.0,
) -> bool:
    """Return whether another comparable control pass can fit its local slice."""
    if MaximumReserveSeconds <= 0:
        raise ValueError("MaximumReserveSeconds must be positive")
    if not HasPriorEscalation:
        return True
    RequiredSeconds = min(
        MaximumReserveSeconds,
        max(0.001, ObservedPassSeconds),
    )
    return RemainingSeconds >= RequiredSeconds


def ChooseRoutingEscalationAction(
    *,
    Classification: str,
    BudgetExhausted: bool,
    State: RoutingEscalationState,
    MaximumAssignmentBudget: int,
    MaximumReservationVariants: int,
    MaximumLaneDiversityLevels: int,
    MaximumCandidateDiversityLevels: int,
    MaximumEffectiveRoutingLayers: int,
) -> RoutingEscalationDecision:
    """Choose the only legal next control change for a routing failure."""
    if Classification == "mandatory-boundary-capacity-cut":
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason=(
                "every fixed portal/access alternative conflicts; packed "
                "local geometry must change"
            ),
        )
    if Classification == "portal-coverage-pair-conflict":
        return RoutingEscalationDecision(
            Action="RegenerateAffectedCandidates",
            Reason=(
                "the complete portal domain contains a compatible access "
                "pair that the retained route candidates did not materialize"
            ),
        )
    if Classification in {
        "higher-order-placement-conflict",
        "stacked-placement-conflict",
    }:
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason=(
                "the typed physical offender set requires affected-cluster "
                "relocation, not another route-only control pass"
            ),
        )
    if Classification == "relocated-higher-order-conflict":
        if State.EffectiveRoutingLayers < MaximumEffectiveRoutingLayers:
            return RoutingEscalationDecision(
                Action="AddRoutingLayer",
                Reason=(
                    "the relocated placement has physical routing-layer "
                    "capacity available before another cluster move"
                ),
            )
        if (
            State.CandidateDiversityLevel + 1
            < MaximumCandidateDiversityLevels
        ):
            return RoutingEscalationDecision(
                Action="RegenerateAffectedCandidates",
                Reason=(
                    "the fully layered relocated placement has one unused "
                    "affected-net candidate-domain expansion"
                ),
            )
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason=(
                "the fully layered relocated placement has a typed physical "
                "conflict that requires another cluster move"
            ),
        )
    if Classification == "relocated-larger-matching-failure":
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason=(
                "the relocated matching frontier has no pairwise route-only "
                "repair; feed its exact offender set back into placement"
            ),
        )
    if Classification == "relocated-pairwise-incompatibility":
        if State.EffectiveRoutingLayers < MaximumEffectiveRoutingLayers:
            return RoutingEscalationDecision(
                Action="AddRoutingLayer",
                Reason=(
                    "the relocated geometry has an unused physical layer "
                    "before route-shape diversity"
                ),
            )
        if (
            Classification == "relocated-pairwise-incompatibility"
            and State.LaneDiversityLevel + 1
            < MaximumLaneDiversityLevels
        ):
            return RoutingEscalationDecision(
                Action="IncreaseLaneDiversity",
                Reason=(
                    "one residual relocated pair has an unused guide-lane "
                    "shape before another cluster move"
                ),
            )
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason=(
                "the fully layered relocated geometry remains incompatible; "
                "feed the exact offender set back into cluster relocation"
            ),
        )
    if BudgetExhausted and Classification.startswith("relocated-"):
        if State.LaneDiversityLevel + 1 < MaximumLaneDiversityLevels:
            return RoutingEscalationDecision(
                Action="IncreaseLaneDiversity",
                Reason=(
                    "bounded assignment on relocated geometry reached its "
                    "work cap; one unused lane shape remains"
                ),
            )
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason=(
                "bounded assignment exhausted every relocated lane shape"
            ),
        )
    if BudgetExhausted:
        if State.AssignmentBudget < MaximumAssignmentBudget:
            return RoutingEscalationDecision(
                Action="GrowAssignmentBudget",
                Reason="exact assignment stopped at its current work bound",
            )
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason="the derived assignment work cap is exhausted",
        )
    if (
        Classification == "no-candidate"
        and State.CandidateDiversityLevel + 1
        < MaximumCandidateDiversityLevels
    ):
        return RoutingEscalationDecision(
            Action="RegenerateAffectedCandidates",
            Reason="one or more affected signals have no legal candidate",
        )
    if (
        Classification == "relocated-multi-pair-conflict"
        and State.EffectiveRoutingLayers < MaximumEffectiveRoutingLayers
    ):
        return RoutingEscalationDecision(
            Action="AddRoutingLayer",
            Reason=(
                "independent conflicts in relocated geometry require the "
                "full physical layer domain before route-shape variants"
            ),
        )
    if (
        Classification in {
            "multi-pair-placement-conflict",
        }
        and State.CandidateDiversityLevel == 0
    ):
        return RoutingEscalationDecision(
            Action="RegenerateAffectedCandidates",
            Reason=(
                "independent pair conflicts require one broader affected-net "
                "candidate pool before placement relocation"
            ),
        )
    if Classification == "relocated-multi-pair-conflict":
        if (
            State.CandidateDiversityLevel + 1
            < MaximumCandidateDiversityLevels
        ):
            return RoutingEscalationDecision(
                Action="RegenerateAffectedCandidates",
                Reason=(
                    "the fully layered relocated exact cut has one unused "
                    "affected-net candidate-domain expansion"
                ),
            )
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason=(
                "the complete fully layered candidate pool contains "
                "independent exact conflicts; relocate their clusters"
            ),
        )
    if (
        Classification == "multi-pair-placement-conflict"
        and State.EffectiveRoutingLayers >= 4
        and State.LaneDiversityLevel == 0
        and State.LaneDiversityLevel + 1 < MaximumLaneDiversityLevels
    ):
        return RoutingEscalationDecision(
            Action="IncreaseLaneDiversity",
            Reason=(
                "the relocated four-layer graph has one unused lane-diversity "
                "pass before another placement move"
            ),
        )
    if Classification == "multi-pair-placement-conflict":
        return RoutingEscalationDecision(
            Action="AdvancePlacement",
            Reason=(
                "independent pair conflicts survived affected-net candidate "
                "regeneration"
            ),
        )
    if (
        State.PortalMode == "reserved"
        and State.ReservationVariant + 1 < MaximumReservationVariants
    ):
        return RoutingEscalationDecision(
            Action="ChangePortalReservation",
            Reason="matching failure requires different physical portal ownership",
        )
    if State.PortalMode == "reserved":
        return RoutingEscalationDecision(
            Action="TryUnreservedPortals",
            Reason=(
                "bounded reservation alternatives are exhausted; evaluate the "
                "complete generated portal domain before growing route geometry"
            ),
        )
    if State.LaneDiversityLevel + 1 < MaximumLaneDiversityLevels:
        return RoutingEscalationDecision(
            Action="IncreaseLaneDiversity",
            Reason="portal variants are exhausted",
        )
    if State.EffectiveRoutingLayers < MaximumEffectiveRoutingLayers:
        return RoutingEscalationDecision(
            Action="AddRoutingLayer",
            Reason="a physically available routing layer remains",
        )
    return RoutingEscalationDecision(
        Action="AdvancePlacement",
        Reason="all meaningful routing controls are exhausted",
    )


def RetainUnaffectedCandidateCache(
    CandidatesBySignal: dict[str, list[Any]],
    CandidateMetadata: dict[str, dict[str, Any]],
    AffectedSignals: frozenset[str],
) -> tuple[
    dict[str, tuple[Any, ...]],
    dict[str, dict[str, Any]],
]:
    """Freeze only non-offender candidates for a localized regeneration."""
    RetainedCandidates = {
        Signal: tuple(Values)
        for Signal, Values in CandidatesBySignal.items()
        if Values and Signal not in AffectedSignals
    }
    RetainedMetadata = {
        Signal: dict(CandidateMetadata.get(Signal, {}))
        for Signal in RetainedCandidates
    }
    return RetainedCandidates, RetainedMetadata


def SelectBoundedDiverseCandidatePool(
    OrderedCandidates: list[Any],
    MaximumCandidates: int,
    PriorCandidateIds: frozenset[str],
) -> list[Any]:
    """Keep bounded old and newly generated geometry in stable order."""
    if MaximumCandidates <= 0:
        return []
    Selected = list(OrderedCandidates[:MaximumCandidates])
    if not PriorCandidateIds or MaximumCandidates < 2:
        return Selected
    PriorCandidates = [
        Candidate
        for Candidate in OrderedCandidates
        if Candidate.CandidateId in PriorCandidateIds
    ]
    NewCandidates = [
        Candidate
        for Candidate in OrderedCandidates
        if Candidate.CandidateId not in PriorCandidateIds
    ]
    if not PriorCandidates or not NewCandidates:
        return Selected
    SelectedIds = {Candidate.CandidateId for Candidate in Selected}
    RequiredCandidates = []
    if not any(
        Candidate.CandidateId in PriorCandidateIds
        for Candidate in Selected
    ):
        RequiredCandidates.append(PriorCandidates[0])
    if not any(
        Candidate.CandidateId not in PriorCandidateIds
        for Candidate in Selected
    ):
        RequiredCandidates.append(NewCandidates[0])
    for Candidate in RequiredCandidates:
        if Candidate.CandidateId in SelectedIds:
            continue
        Removed = Selected.pop()
        SelectedIds.remove(Removed.CandidateId)
        Selected.append(Candidate)
        SelectedIds.add(Candidate.CandidateId)
    SelectedIdSet = {Candidate.CandidateId for Candidate in Selected}
    return [
        Candidate
        for Candidate in OrderedCandidates
        if Candidate.CandidateId in SelectedIdSet
    ]

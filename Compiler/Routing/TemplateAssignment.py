"""One bounded authoritative track-assignment selection across templates.

Placement alternatives own mutually exclusive resource graphs.  They cannot
be flattened into one ordinary assignment because that would require every
placement to route simultaneously.  This module instead presents those raw
domains as one deterministic selection problem: a complete capacity core for
one template permits the next fixed template to be considered, while work or
deadline exhaustion terminates the whole problem as incomplete.

The existing Rust ``RoutingContext`` assignment binding remains authoritative
for each raw physical domain.  The aggregate selector carries one immutable
work counter and one absolute deadline across those calls; it is not a retry
or a route attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from .AuthoritativePlanner import (
    BuildTrackAssignmentPreparationFromRawDomain,
    RawTrackAssignmentDomain,
)
from .Models import TrackAssignmentPreparation
from .Reliability import BuildStableFingerprint, RoutingDeadline

try:
    from ..RustRouting import (
        SolveTemplateAssignmentDomainsBounded as _SolveTemplateAssignmentDomainsBounded,
    )
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import (
            SolveTemplateAssignmentDomainsBounded as _SolveTemplateAssignmentDomainsBounded,
        )
    except Exception:
        _SolveTemplateAssignmentDomainsBounded = None


@dataclass(frozen=True)
class RawTrackAssignmentTemplate:
    """One immutable, mutually exclusive authoritative assignment domain."""

    TemplateId: str
    Objective: tuple[int, ...]
    Domain: RawTrackAssignmentDomain

    def __post_init__(self) -> None:
        if not self.TemplateId:
            raise ValueError("raw track-assignment template requires an id")
        if any(Value < 0 for Value in self.Objective):
            raise ValueError("raw track-assignment objective cannot be negative")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "Domain": self.Domain.ToDictionary(),
        }


@dataclass(frozen=True)
class RawTrackAssignmentProblem:
    """Fixed finite placement/template capacity problem.

    ``MaximumAssignmentExpansions`` is global to the problem.  Every member
    must carry the same declared cap so adding a geometry member cannot turn
    the cap into an accidental per-template budget multiplier.
    """

    Templates: tuple[RawTrackAssignmentTemplate, ...]
    MaximumAssignmentExpansions: int
    NonExhaustiveTemplateDomain: bool = True

    def __post_init__(self) -> None:
        if self.MaximumAssignmentExpansions < 1:
            raise ValueError("raw template assignment requires a positive work cap")
        TemplateIds = tuple(Value.TemplateId for Value in self.Templates)
        if len(TemplateIds) != len(set(TemplateIds)):
            raise ValueError("raw template assignment repeats a template id")
        MismatchedCaps = tuple(
            Value.TemplateId
            for Value in self.Templates
            if Value.Domain.MaximumAssignmentExpansions
            != self.MaximumAssignmentExpansions
        )
        if MismatchedCaps:
            raise ValueError(
                "raw template assignment members must share one work cap: "
                + ", ".join(MismatchedCaps)
            )
        if not self.NonExhaustiveTemplateDomain:
            TruncatedTemplates = tuple(
                Value.TemplateId
                for Value in self.Templates
                if bool(dict(Value.Domain.Diagnostics).get(
                    "ExcludedConfiguredRequestCounts",
                    (),
                ))
            )
            if TruncatedTemplates:
                raise ValueError(
                    "a raw template with excluded configured request shapes "
                    "cannot be declared exhaustive: "
                    + ", ".join(TruncatedTemplates)
                )

    @property
    def ProblemFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "raw-template-track-assignment-v1",
            "Templates": [
                Value.ToDictionary()
                for Value in sorted(
                    self.Templates,
                    key=lambda Value: (Value.Objective, Value.TemplateId),
                )
            ],
            "MaximumAssignmentExpansions": (
                self.MaximumAssignmentExpansions
            ),
            "NonExhaustiveTemplateDomain": (
                self.NonExhaustiveTemplateDomain
            ),
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "TemplateCount": len(self.Templates),
            "MaximumAssignmentExpansions": (
                self.MaximumAssignmentExpansions
            ),
            "NonExhaustiveTemplateDomain": (
                self.NonExhaustiveTemplateDomain
            ),
        }


@dataclass(frozen=True)
class PredeclaredRawTrackAssignmentMember:
    """A fixed raw-template member declared before materialization.

    ``Objective`` is an immutable selection prefix known before materializing
    the raw domain.  Most callers provide the complete objective.  A
    placement/access portfolio may instead provide its exact geometry/layer
    prefix, then report the remaining material/access terms with the typed
    materialization.  In that form, all descriptors sharing the prefix are
    materialized before the selector chooses their resolved full objective.
    """

    TemplateId: str
    Objective: tuple[int, ...]
    MaterializationInputFingerprint: str

    def __post_init__(self) -> None:
        if not self.TemplateId:
            raise ValueError("predeclared raw member requires an id")
        if any(Value < 0 for Value in self.Objective):
            raise ValueError(
                "predeclared raw member objective cannot be negative"
            )
        if not self.MaterializationInputFingerprint:
            raise ValueError(
                "predeclared raw member requires an input fingerprint"
            )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "MaterializationInputFingerprint": (
                self.MaterializationInputFingerprint
            ),
        }


@dataclass(frozen=True)
class RawTrackAssignmentMemberMaterialization:
    """Typed result of constructing one predeclared raw template domain."""

    TemplateId: str
    Domain: RawTrackAssignmentDomain | None
    Complete: bool
    IncompleteReason: str = ""
    Diagnostics: tuple[tuple[str, object], ...] = ()
    ResolvedObjective: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.TemplateId:
            raise ValueError("raw template materialization requires an id")
        if self.Complete != (self.Domain is not None and self.Domain.Complete):
            raise ValueError(
                "raw template materialization completeness must match its "
                "domain"
            )
        if not self.Complete and not self.IncompleteReason:
            raise ValueError(
                "incomplete raw template materialization requires a reason"
            )
        if any(Value < 0 for Value in self.ResolvedObjective):
            raise ValueError(
                "resolved raw template objective cannot be negative"
            )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "Domain": (
                self.Domain.ToDictionary()
                if self.Domain is not None
                else None
            ),
            "Diagnostics": dict(self.Diagnostics),
            "ResolvedObjective": list(self.ResolvedObjective),
        }


@dataclass(frozen=True)
class RawTrackAssignmentAttempt:
    """One member result inside the aggregate capacity proof."""

    TemplateId: str
    Objective: tuple[int, ...]
    Success: bool
    Complete: bool
    ExpansionCount: int
    CumulativeExpansionCount: int
    ConflictSignals: tuple[str, ...] = ()
    ConflictResourceIndices: tuple[int, ...] = ()
    PairwiseIncompatibleSignals: tuple[tuple[str, str], ...] = ()
    IncompleteReason: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "Success": self.Success,
            "Complete": self.Complete,
            "ExpansionCount": self.ExpansionCount,
            "CumulativeExpansionCount": self.CumulativeExpansionCount,
            "ConflictSignals": list(self.ConflictSignals),
            "ConflictResourceIndices": list(self.ConflictResourceIndices),
            "PairwiseIncompatibleSignals": [
                list(Value) for Value in self.PairwiseIncompatibleSignals
            ],
            "IncompleteReason": self.IncompleteReason,
        }


@dataclass(frozen=True)
class RawTrackAssignmentSelection:
    """Typed terminal result for the one aggregate template selection."""

    ProblemFingerprint: str
    SelectionFingerprint: str
    SelectedTemplateId: str
    SelectedObjective: tuple[int, ...]
    Preparation: TrackAssignmentPreparation | None
    Attempts: tuple[RawTrackAssignmentAttempt, ...]
    ExpansionCount: int
    Success: bool
    Complete: bool
    Unsatisfiable: bool
    IncompleteReason: str = ""
    FirstConflictSignals: tuple[str, ...] = ()
    FirstConflictResourceIndices: tuple[int, ...] = ()
    FirstPairwiseIncompatibleSignals: tuple[tuple[str, str], ...] = ()
    MaterializedTemplateCount: int = 0
    SkippedDominatedTemplateCount: int = 0

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "SelectionFingerprint": self.SelectionFingerprint,
            "SelectedTemplateId": self.SelectedTemplateId,
            "SelectedObjective": list(self.SelectedObjective),
            "Preparation": (
                self.Preparation.ToDictionary()
                if self.Preparation is not None
                else None
            ),
            "Attempts": [Value.ToDictionary() for Value in self.Attempts],
            "ExpansionCount": self.ExpansionCount,
            "Success": self.Success,
            "Complete": self.Complete,
            "Unsatisfiable": self.Unsatisfiable,
            "IncompleteReason": self.IncompleteReason,
            "FirstConflictSignals": list(self.FirstConflictSignals),
            "FirstConflictResourceIndices": list(
                self.FirstConflictResourceIndices
            ),
            "FirstPairwiseIncompatibleSignals": [
                list(Value)
                for Value in self.FirstPairwiseIncompatibleSignals
            ],
            "MaterializedTemplateCount": self.MaterializedTemplateCount,
            "SkippedDominatedTemplateCount": (
                self.SkippedDominatedTemplateCount
            ),
        }


NativeRawAssignmentSolver = Callable[[RawTrackAssignmentDomain, int], Any]
WorkCheck = Callable[[dict[str, object]], None]
def _BuildSelection(
    Problem: RawTrackAssignmentProblem,
    *,
    Attempts: Iterable[RawTrackAssignmentAttempt],
    ExpansionCount: int,
    Success: bool,
    Complete: bool,
    Unsatisfiable: bool,
    SelectedTemplate: RawTrackAssignmentTemplate | None = None,
    Preparation: TrackAssignmentPreparation | None = None,
    IncompleteReason: str = "",
    FirstConflictSignals: tuple[str, ...] = (),
    FirstConflictResourceIndices: tuple[int, ...] = (),
    FirstPairwiseIncompatibleSignals: tuple[tuple[str, str], ...] = (),
    MaterializedTemplateCount: int = 0,
    SkippedDominatedTemplateCount: int = 0,
) -> RawTrackAssignmentSelection:
    AttemptValues = tuple(Attempts)
    SelectionFingerprint = (
        BuildStableFingerprint({
            "ProblemFingerprint": Problem.ProblemFingerprint,
            "SelectedTemplateId": (
                SelectedTemplate.TemplateId
                if SelectedTemplate is not None
                else ""
            ),
            "Preparation": (
                Preparation.ToDictionary()
                if Preparation is not None
                else None
            ),
        })
        if Success and SelectedTemplate is not None and Preparation is not None
        else ""
    )
    return RawTrackAssignmentSelection(
        ProblemFingerprint=Problem.ProblemFingerprint,
        SelectionFingerprint=SelectionFingerprint,
        SelectedTemplateId=(
            SelectedTemplate.TemplateId
            if SelectedTemplate is not None
            else ""
        ),
        SelectedObjective=(
            SelectedTemplate.Objective
            if SelectedTemplate is not None
            else ()
        ),
        Preparation=Preparation,
        Attempts=AttemptValues,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=Complete,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=IncompleteReason,
        FirstConflictSignals=FirstConflictSignals,
        FirstConflictResourceIndices=FirstConflictResourceIndices,
        FirstPairwiseIncompatibleSignals=(
            FirstPairwiseIncompatibleSignals
        ),
        MaterializedTemplateCount=MaterializedTemplateCount,
        SkippedDominatedTemplateCount=SkippedDominatedTemplateCount,
    )


def _EmptyDomainAttempt(
    Template: RawTrackAssignmentTemplate,
    ExpansionCount: int,
) -> RawTrackAssignmentAttempt | None:
    """Return an exact complete empty-domain core, if one is declared."""
    EmptySignals = tuple(
        Signal
        for Signal, Count in Template.Domain.CandidateCounts
        if Count == 0
    )
    if not EmptySignals:
        return None
    return RawTrackAssignmentAttempt(
        TemplateId=Template.TemplateId,
        Objective=Template.Objective,
        Success=False,
        Complete=True,
        ExpansionCount=0,
        CumulativeExpansionCount=ExpansionCount,
        ConflictSignals=EmptySignals,
        IncompleteReason="complete-empty-candidate-domain",
    )


def SolveRawTrackAssignmentProblem(
    Problem: RawTrackAssignmentProblem,
    NativeSolve: NativeRawAssignmentSolver,
    *,
    WorkCheck: WorkCheck | None = None,
) -> RawTrackAssignmentSelection:
    """Select one template and its authoritative witness under one cap.

    ``NativeSolve`` must run the raw domain's exact capacity-one assignment
    using at most the passed *remaining global* expansion count.  A complete
    failed member is a capacity core for that fixed member and permits the
    next, already-materialized member.  Any incomplete member terminates the
    entire non-retrying selection immediately.
    """
    OrderedTemplates = tuple(sorted(
        Problem.Templates,
        key=lambda Value: (Value.Objective, Value.TemplateId),
    ))
    Attempts: list[RawTrackAssignmentAttempt] = []
    Spent = 0
    FirstConflictSignals: tuple[str, ...] = ()
    FirstConflictResourceIndices: tuple[int, ...] = ()

    for TemplateIndex, Template in enumerate(OrderedTemplates):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "raw-template-track-assignment",
                "TemplateIndex": TemplateIndex,
                "TemplateCount": len(OrderedTemplates),
                "TemplateId": Template.TemplateId,
                "ExpansionCount": Spent,
                "MaximumAssignmentExpansions": (
                    Problem.MaximumAssignmentExpansions
                ),
            })
        if not Template.Domain.Complete:
            Attempts.append(RawTrackAssignmentAttempt(
                TemplateId=Template.TemplateId,
                Objective=Template.Objective,
                Success=False,
                Complete=False,
                ExpansionCount=0,
                CumulativeExpansionCount=Spent,
                IncompleteReason=(
                    Template.Domain.IncompleteReason
                    or "incomplete-raw-template-domain"
                ),
            ))
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason="incomplete-template-domain",
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )

        EmptyDomain = _EmptyDomainAttempt(Template, Spent)
        if EmptyDomain is not None:
            Attempts.append(EmptyDomain)
            if not FirstConflictSignals:
                FirstConflictSignals = EmptyDomain.ConflictSignals
            continue

        Remaining = Problem.MaximumAssignmentExpansions - Spent
        if Remaining < 1:
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason="assignment-work-cap",
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )
        NativeResult = NativeSolve(Template.Domain, Remaining)
        ResultExpansionCount = max(
            0,
            int(getattr(NativeResult, "ExpansionCount", 0)),
        )
        Spent = min(
            Problem.MaximumAssignmentExpansions,
            Spent + ResultExpansionCount,
        )
        DeadlineExceeded = bool(
            getattr(NativeResult, "DeadlineExceeded", False)
        )
        BudgetExhausted = bool(
            getattr(NativeResult, "BudgetExhausted", False)
        )
        ResultSuccess = bool(getattr(NativeResult, "Success", False))
        ConflictSignals = tuple(sorted(map(
            str,
            getattr(NativeResult, "ConflictSignals", ()),
        )))
        ConflictResourceIndices = tuple(sorted(map(
            int,
            getattr(NativeResult, "ConflictResourceIndices", ()),
        )))
        ResultComplete = not DeadlineExceeded and not BudgetExhausted
        IncompleteReason = (
            "assignment-deadline"
            if DeadlineExceeded
            else "assignment-work-cap"
            if BudgetExhausted
            else ""
        )
        Attempt = RawTrackAssignmentAttempt(
            TemplateId=Template.TemplateId,
            Objective=Template.Objective,
            Success=ResultSuccess and ResultComplete,
            Complete=ResultComplete,
            ExpansionCount=ResultExpansionCount,
            CumulativeExpansionCount=Spent,
            ConflictSignals=ConflictSignals,
            ConflictResourceIndices=ConflictResourceIndices,
            IncompleteReason=IncompleteReason,
        )
        Attempts.append(Attempt)
        if not FirstConflictSignals and ConflictSignals:
            FirstConflictSignals = ConflictSignals
            FirstConflictResourceIndices = ConflictResourceIndices
        if not ResultComplete:
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason=IncompleteReason,
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )
        if ResultSuccess:
            Preparation = BuildTrackAssignmentPreparationFromRawDomain(
                Template.Domain,
                NativeResult,
            )
            if not Preparation.Success or not Preparation.Complete:
                raise RuntimeError(
                    "complete native raw assignment did not produce a "
                    "complete frozen track witness"
                )
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=True,
                Complete=True,
                Unsatisfiable=False,
                SelectedTemplate=Template,
                Preparation=Preparation,
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )

    Unsatisfiable = not Problem.NonExhaustiveTemplateDomain
    return _BuildSelection(
        Problem,
        Attempts=Attempts,
        ExpansionCount=Spent,
        Success=False,
        Complete=Unsatisfiable,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=(
            "complete-capacity-core"
            if Unsatisfiable
            else "non-exhaustive-template-domain"
        ),
        FirstConflictSignals=FirstConflictSignals,
        FirstConflictResourceIndices=FirstConflictResourceIndices,
    )


def _BuildContextNativeRawAssignmentSolver(
    Context: Any | None,
    Deadline: RoutingDeadline,
) -> NativeRawAssignmentSolver:
    """Bind the existing native assignment API to one absolute deadline."""
    def NativeSolve(
        Domain: RawTrackAssignmentDomain,
        MaximumExpansions: int,
    ) -> Any:
        ActiveContext = (
            Domain.NativeAssignmentContext
            if Domain.NativeAssignmentContext is not None
            else Context
        )
        if ActiveContext is None:
            raise ValueError(
                "raw template assignment requires a native routing context"
            )
        RemainingMilliseconds = Deadline.RemainingMilliseconds()
        if RemainingMilliseconds < 1:
            return SimpleNamespace(
                Success=False,
                SelectedCandidateIds=(),
                ExpansionCount=0,
                BudgetExhausted=False,
                DeadlineExceeded=True,
                ConflictSignals=(),
                ConflictResourceIndices=(),
            )
        CandidateValues = Domain.NativeCandidateValues()
        BaseValues = Domain.NativeBaseValues()
        Arguments = (
            CandidateValues,
            Domain.NativeResourceCount,
            MaximumExpansions,
            RemainingMilliseconds,
        )
        if BaseValues:
            return ActiveContext.PlanAuthoritativeRoutesWithBaseBounded(
                CandidateValues,
                BaseValues,
                Domain.NativeResourceCount,
                MaximumExpansions,
                RemainingMilliseconds,
            )
        return ActiveContext.PlanAuthoritativeRoutesBounded(*Arguments)

    return NativeSolve


def _BuildNativeTemplateDomainPayload(
    Problem: RawTrackAssignmentProblem,
) -> list[tuple[object, ...]]:
    """Encode mutually exclusive raw domains for one native selection call."""
    return [
        (
            Template.TemplateId,
            list(Template.Objective),
            Template.Domain.NativeResourceCount,
            [Signal for Signal, _Count in Template.Domain.CandidateCounts],
            Template.Domain.NativeCandidateValues(),
            Template.Domain.NativeBaseValues(),
        )
        for Template in sorted(
            Problem.Templates,
            key=lambda Value: (Value.Objective, Value.TemplateId),
        )
    ]


def _BuildNativeTemplateSelection(
    Problem: RawTrackAssignmentProblem,
    NativeResult: Any,
) -> RawTrackAssignmentSelection:
    """Adapt the immutable Rust portfolio result to the existing handoff."""
    TemplateById = {
        Template.TemplateId: Template
        for Template in Problem.Templates
    }
    AttemptedIds = tuple(map(
        str,
        getattr(NativeResult, "AttemptedTemplateIds", ()),
    ))
    SelectedTemplateId = str(getattr(
        NativeResult,
        "SelectedTemplateId",
        "",
    ) or "")
    SelectedTemplate = TemplateById.get(SelectedTemplateId)
    Success = bool(getattr(NativeResult, "Success", False))
    Complete = bool(getattr(NativeResult, "Complete", False))
    if Success and (SelectedTemplate is None or not Complete):
        raise RuntimeError(
            "native template assignment returned an invalid frozen witness"
        )
    ExpansionCount = max(0, int(getattr(
        NativeResult,
        "ExpansionCount",
        0,
    )))
    ConflictSignals = tuple(sorted(map(
        str,
        getattr(NativeResult, "ConflictSignals", ()),
    )))
    ConflictResourceIndices = tuple(sorted(map(
        int,
        getattr(NativeResult, "ConflictResourceIndices", ()),
    )))
    PairwiseIncompatibleSignals = tuple(sorted(
        tuple(map(str, Value))
        for Value in getattr(
            NativeResult,
            "PairwiseIncompatibleSignals",
            (),
        )
    ))
    AttemptPairwiseIncompatibleSignals = {
        str(TemplateId): tuple(sorted(
            tuple(map(str, Pair)) for Pair in Values
        ))
        for TemplateId, Values in getattr(
            NativeResult,
            "AttemptPairwiseIncompatibleSignals",
            (),
        )
    }
    Attempts = tuple(
        RawTrackAssignmentAttempt(
            TemplateId=TemplateId,
            Objective=TemplateById[TemplateId].Objective,
            Success=Success and TemplateId == SelectedTemplateId,
            # Rust proves every attempted non-winning member complete before
            # it advances.  A terminal cap/deadline is attributed to the
            # final attempted member only.
            Complete=(
                Complete
                or TemplateIndex + 1 < len(AttemptedIds)
            ),
            ExpansionCount=(
                ExpansionCount
                if TemplateIndex + 1 == len(AttemptedIds)
                else 0
            ),
            CumulativeExpansionCount=(
                ExpansionCount
                if TemplateIndex + 1 == len(AttemptedIds)
                else 0
            ),
            ConflictSignals=(
                ConflictSignals
                if TemplateIndex == 0 and not Success
                else ()
            ),
            ConflictResourceIndices=(
                ConflictResourceIndices
                if TemplateIndex == 0 and not Success
                else ()
            ),
            PairwiseIncompatibleSignals=(
                AttemptPairwiseIncompatibleSignals.get(TemplateId, ())
            ),
            IncompleteReason=(
                str(getattr(NativeResult, "IncompleteReason", ""))
                if TemplateIndex + 1 == len(AttemptedIds) and not Complete
                else ""
            ),
        )
        for TemplateIndex, TemplateId in enumerate(AttemptedIds)
        if TemplateId in TemplateById
    )
    Preparation = (
        BuildTrackAssignmentPreparationFromRawDomain(
            SelectedTemplate.Domain,
            NativeResult,
        )
        if Success and SelectedTemplate is not None
        else None
    )
    if Preparation is not None and (
        not Preparation.Success or not Preparation.Complete
    ):
        raise RuntimeError(
            "native template assignment did not produce a complete witness"
        )
    return _BuildSelection(
        Problem,
        Attempts=Attempts,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=Complete,
        Unsatisfiable=bool(getattr(NativeResult, "Unsatisfiable", False)),
        SelectedTemplate=SelectedTemplate if Success else None,
        Preparation=Preparation,
        IncompleteReason=str(getattr(NativeResult, "IncompleteReason", "")),
        FirstConflictSignals=ConflictSignals,
        FirstConflictResourceIndices=ConflictResourceIndices,
        FirstPairwiseIncompatibleSignals=PairwiseIncompatibleSignals,
        MaterializedTemplateCount=len(AttemptedIds),
    )


def _TrySolveRawTrackAssignmentProblemNatively(
    Problem: RawTrackAssignmentProblem,
    Deadline: RoutingDeadline,
    Context: Any | None,
) -> RawTrackAssignmentSelection | None:
    """Use the one-call native template selector when the binding is present."""
    if _SolveTemplateAssignmentDomainsBounded is None:
        return None
    # Preserve explicit fixture/executor injection.  The production template
    # portfolio will opt into this binding only after all predeclared members
    # are materialized; this raw-problem adapter must not bypass a caller's
    # supplied context during that migration.
    if Context is not None:
        return None
    # The Rust input is intentionally complete-only.  The established Python
    # path publishes a richer typed reason for an incomplete member without
    # serializing it as if it were a capacity core.
    if any(not Template.Domain.Complete for Template in Problem.Templates):
        return None
    RemainingMilliseconds = Deadline.RemainingMilliseconds()
    if RemainingMilliseconds < 1:
        return None
    NativeResult = _SolveTemplateAssignmentDomainsBounded(
        _BuildNativeTemplateDomainPayload(Problem),
        Problem.MaximumAssignmentExpansions,
        RemainingMilliseconds,
        Problem.NonExhaustiveTemplateDomain,
    )
    return _BuildNativeTemplateSelection(Problem, NativeResult)


def SolveRawTrackAssignmentProblemWithContext(
    Problem: RawTrackAssignmentProblem,
    *,
    Context: Any | None = None,
    Deadline: RoutingDeadline,
    WorkCheck: WorkCheck | None = None,
) -> RawTrackAssignmentSelection:
    """Run the aggregate selector through the existing Rust binding.

    The ordinary bounded assignment API is deliberately reused.  A raw domain
    may retain the context that created its local resource index; ``Context``
    is a fallback for synthetic or fixture domains.  Each call receives only
    the global remainder and the same absolute deadline's remaining
    milliseconds, so the outer selector has one work cap and one deadline
    even though template resource indices are local.
    """
    NativeSelection = _TrySolveRawTrackAssignmentProblemNatively(
        Problem,
        Deadline,
        Context,
    )
    if NativeSelection is not None:
        return NativeSelection
    return SolveRawTrackAssignmentProblem(
        Problem,
        _BuildContextNativeRawAssignmentSolver(Context, Deadline),
        WorkCheck=WorkCheck,
    )

"""Focused contracts for one bounded raw template assignment selector."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from PhysicalDesign.Routing.Global.Orchestration.RunModels import RawTrackAssignmentDomain, RawTrackAssignmentValue
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims
from PhysicalDesign.Runtime.Reliability import RoutingDeadline
from PhysicalDesign.Routing.Assignment.TemplateAssignment import RawTrackAssignmentMaterialization, RawTrackAssignmentPortfolio, RawTrackAssignmentPortfolioTemplate, RawTrackAssignmentProblem, RawTrackAssignmentTemplate, SolveRawTrackAssignmentPortfolio, SolveRawTrackAssignmentProblem, SolveRawTrackAssignmentProblemWithContext


def BuildDomain(
    TemplateId: str,
    *,
    MaximumExpansions: int = 16,
    Complete: bool = True,
    Empty: bool = False,
) -> RawTrackAssignmentDomain:
    Position = (len(TemplateId), 1, 0)
    Values = () if Empty else (RawTrackAssignmentValue(
        Signal="Signal",
        CandidateId=f"{TemplateId}-candidate",
        Claims=RoutingResourceClaims(WireCells=frozenset({Position})),
        MaterialCost=1,
        FootprintGrowth=1,
        Length=1,
        BendCount=0,
        ViaCount=0,
    ),)
    return RawTrackAssignmentDomain(
        ResourcePositions=(Position,),
        Values=Values,
        BaseClaims=(),
        CandidateCounts=(("Signal", len(Values)),),
        CandidateDomainFingerprint=f"candidate-{TemplateId}",
        LocalClaimDomainFingerprint=f"local-{TemplateId}",
        PlacementFingerprint=f"placement-{TemplateId}",
        ResourceGraphFingerprint=f"resources-{TemplateId}",
        PortalDomainFingerprint=f"portals-{TemplateId}",
        Complete=Complete,
        IncompleteReason="truncated-domain" if not Complete else "",
        MaximumAssignmentExpansions=MaximumExpansions,
    )


def BuildTemplate(
    TemplateId: str,
    Objective: tuple[int, ...],
    **DomainArguments,
) -> RawTrackAssignmentTemplate:
    return RawTrackAssignmentTemplate(
        TemplateId=TemplateId,
        Objective=Objective,
        Domain=BuildDomain(TemplateId, **DomainArguments),
    )


def NativeResult(
    *,
    Success: bool,
    ExpansionCount: int,
    CandidateId: str = "",
    BudgetExhausted: bool = False,
    DeadlineExceeded: bool = False,
    ConflictSignals: tuple[str, ...] = (),
    ConflictResourceIndices: tuple[int, ...] = (),
):
    return SimpleNamespace(
        Success=Success,
        SelectedCandidateIds=(
            (("Signal", CandidateId),) if CandidateId else ()
        ),
        ExpansionCount=ExpansionCount,
        BudgetExhausted=BudgetExhausted,
        DeadlineExceeded=DeadlineExceeded,
        ConflictSignals=ConflictSignals,
        ConflictResourceIndices=ConflictResourceIndices,
    )


def test_complete_core_advances_inside_one_shared_template_selection():
    Compact = BuildTemplate("compact", (4, 8))
    Separated = BuildTemplate("separated", (5, 7))
    Calls: list[tuple[str, int]] = []

    def Solve(Domain, Remaining):
        Calls.append((Domain.PlacementFingerprint, Remaining))
        if Domain.PlacementFingerprint == "placement-compact":
            return NativeResult(
                Success=False,
                ExpansionCount=3,
                ConflictSignals=("A", "B"),
                ConflictResourceIndices=(0,),
            )
        return NativeResult(
            Success=True,
            ExpansionCount=4,
            CandidateId="separated-candidate",
        )

    Result = SolveRawTrackAssignmentProblem(
        RawTrackAssignmentProblem(
            Templates=(Separated, Compact),
            MaximumAssignmentExpansions=16,
        ),
        Solve,
    )

    assert Calls == [
        ("placement-compact", 16),
        ("placement-separated", 13),
    ]
    assert Result.Success is True
    assert Result.Complete is True
    assert Result.SelectedTemplateId == "separated"
    assert Result.ExpansionCount == 7
    assert Result.Preparation is not None
    assert Result.Preparation.SelectedCandidateIds == (
        ("Signal", "separated-candidate"),
    )
    assert Result.FirstConflictSignals == ("A", "B")


def test_fixed_portfolio_materializes_only_through_first_witness():
    Descriptors = tuple(
        RawTrackAssignmentPortfolioTemplate(
            TemplateId=TemplateId,
            Objective=(Index,),
            MaterializationInputFingerprint=f"input-{TemplateId}",
        )
        for Index, TemplateId in enumerate(("compact", "incumbent", "worse"))
    )
    Materialized: list[str] = []
    NativeCalls: list[tuple[str, int]] = []

    def Materialize(Descriptor):
        Materialized.append(Descriptor.TemplateId)
        return RawTrackAssignmentMaterialization(
            TemplateId=Descriptor.TemplateId,
            Domain=BuildDomain(Descriptor.TemplateId),
            Complete=True,
        )

    def Solve(Domain, Remaining):
        NativeCalls.append((Domain.PlacementFingerprint, Remaining))
        if Domain.PlacementFingerprint == "placement-compact":
            return NativeResult(
                Success=False,
                ExpansionCount=3,
                ConflictSignals=("A", "B"),
            )
        return NativeResult(
            Success=True,
            ExpansionCount=4,
            CandidateId="incumbent-candidate",
        )

    Result = SolveRawTrackAssignmentPortfolio(
        RawTrackAssignmentPortfolio(
            Templates=Descriptors,
            MaximumAssignmentExpansions=16,
        ),
        Materialize,
        Solve,
    )

    assert Materialized == ["compact", "incumbent"]
    assert NativeCalls == [
        ("placement-compact", 16),
        ("placement-incumbent", 13),
    ]
    assert Result.Success is True
    assert Result.SelectedTemplateId == "incumbent"
    assert Result.MaterializedTemplateCount == 2
    assert Result.SkippedDominatedTemplateCount == 1


def test_incomplete_portfolio_materialization_is_terminal():
    Descriptors = tuple(
        RawTrackAssignmentPortfolioTemplate(
            TemplateId=TemplateId,
            Objective=(Index,),
            MaterializationInputFingerprint=f"input-{TemplateId}",
        )
        for Index, TemplateId in enumerate(("compact", "incumbent"))
    )
    Materialized: list[str] = []

    def Materialize(Descriptor):
        Materialized.append(Descriptor.TemplateId)
        return RawTrackAssignmentMaterialization(
            TemplateId=Descriptor.TemplateId,
            Domain=None,
            Complete=False,
            IncompleteReason="fixed-domain-work-cap",
        )

    Result = SolveRawTrackAssignmentPortfolio(
        RawTrackAssignmentPortfolio(
            Templates=Descriptors,
            MaximumAssignmentExpansions=16,
        ),
        Materialize,
        lambda _Domain, _Remaining: (_ for _ in ()).throw(
            AssertionError("incomplete materialization must not reach native")
        ),
    )

    assert Materialized == ["compact"]
    assert Result.Success is False
    assert Result.Complete is False
    assert Result.Unsatisfiable is False
    assert Result.IncompleteReason == "incomplete-template-domain"
    assert Result.MaterializedTemplateCount == 1
    assert Result.SkippedDominatedTemplateCount == 1


def test_equal_objective_incomplete_member_prevents_early_commit():
    """A tied partial member cannot be hidden behind an earlier witness."""
    Descriptors = (
        RawTrackAssignmentPortfolioTemplate(
            TemplateId="compact",
            Objective=(4, 8),
            MaterializationInputFingerprint="input-compact",
        ),
        RawTrackAssignmentPortfolioTemplate(
            TemplateId="compact-tie",
            Objective=(4, 8),
            MaterializationInputFingerprint="input-compact-tie",
        ),
        RawTrackAssignmentPortfolioTemplate(
            TemplateId="worse",
            Objective=(5, 7),
            MaterializationInputFingerprint="input-worse",
        ),
    )
    Materialized: list[str] = []
    NativeCalls: list[str] = []

    def Materialize(Descriptor):
        Materialized.append(Descriptor.TemplateId)
        if Descriptor.TemplateId == "compact-tie":
            return RawTrackAssignmentMaterialization(
                TemplateId=Descriptor.TemplateId,
                Domain=None,
                Complete=False,
                IncompleteReason="fixed-domain-work-cap",
            )
        return RawTrackAssignmentMaterialization(
            TemplateId=Descriptor.TemplateId,
            Domain=BuildDomain(Descriptor.TemplateId),
            Complete=True,
        )

    def Solve(Domain, _Remaining):
        NativeCalls.append(Domain.PlacementFingerprint)
        return NativeResult(
            Success=True,
            ExpansionCount=1,
            CandidateId="compact-candidate",
        )

    Result = SolveRawTrackAssignmentPortfolio(
        RawTrackAssignmentPortfolio(
            Templates=Descriptors,
            MaximumAssignmentExpansions=16,
        ),
        Materialize,
        Solve,
    )

    assert Materialized == ["compact", "compact-tie"]
    assert NativeCalls == ["placement-compact"]
    assert Result.Success is False
    assert Result.Complete is False
    assert Result.Unsatisfiable is False
    assert Result.IncompleteReason == "incomplete-template-domain"
    assert Result.MaterializedTemplateCount == 2
    assert Result.SkippedDominatedTemplateCount == 1


def test_equal_prefix_uses_resolved_material_access_objective():
    """Geometry/layer ties resolve only after every fixed factor is built."""
    Descriptors = (
        RawTrackAssignmentPortfolioTemplate(
            TemplateId="more-access-material",
            Objective=(4, 8, 2),
            MaterializationInputFingerprint="input-more-access-material",
        ),
        RawTrackAssignmentPortfolioTemplate(
            TemplateId="less-access-material",
            Objective=(4, 8, 2),
            MaterializationInputFingerprint="input-less-access-material",
        ),
        RawTrackAssignmentPortfolioTemplate(
            TemplateId="worse-footprint",
            Objective=(5, 7, 1),
            MaterializationInputFingerprint="input-worse-footprint",
        ),
    )
    Materialized: list[str] = []

    def Materialize(Descriptor):
        Materialized.append(Descriptor.TemplateId)
        AccessMaterial = (
            9
            if Descriptor.TemplateId == "more-access-material"
            else 4
        )
        return RawTrackAssignmentMaterialization(
            TemplateId=Descriptor.TemplateId,
            Domain=BuildDomain(Descriptor.TemplateId),
            Complete=True,
            ResolvedObjective=(4, 8, 2, AccessMaterial, 3, 0),
        )

    def Solve(Domain, _Remaining):
        return NativeResult(
            Success=True,
            ExpansionCount=1,
            CandidateId=(
                f"{Domain.PlacementFingerprint.removeprefix('placement-')}"
                "-candidate"
            ),
        )

    Result = SolveRawTrackAssignmentPortfolio(
        RawTrackAssignmentPortfolio(
            Templates=Descriptors,
            MaximumAssignmentExpansions=16,
        ),
        Materialize,
        Solve,
    )

    assert Materialized == [
        "less-access-material",
        "more-access-material",
    ]
    assert Result.Success is True
    assert Result.SelectedTemplateId == "less-access-material"
    assert Result.SelectedObjective == (4, 8, 2, 4, 3, 0)
    assert Result.MaterializedTemplateCount == 2
    assert Result.SkippedDominatedTemplateCount == 1


def test_resolved_objective_cannot_change_declared_selection_prefix():
    Descriptor = RawTrackAssignmentPortfolioTemplate(
        TemplateId="compact",
        Objective=(4, 8, 2),
        MaterializationInputFingerprint="input-compact",
    )

    with pytest.raises(ValueError, match="retain its declared selection prefix"):
        SolveRawTrackAssignmentPortfolio(
            RawTrackAssignmentPortfolio(
                Templates=(Descriptor,),
                MaximumAssignmentExpansions=16,
            ),
            lambda Value: RawTrackAssignmentMaterialization(
                TemplateId=Value.TemplateId,
                Domain=BuildDomain(Value.TemplateId),
                Complete=True,
                ResolvedObjective=(3, 8, 2, 0),
            ),
            lambda _Domain, _Remaining: NativeResult(
                Success=True,
                ExpansionCount=1,
                CandidateId="compact-candidate",
            ),
        )


def test_work_exhaustion_is_terminal_and_does_not_try_a_sibling():
    Compact = BuildTemplate("compact", (4,))
    Separated = BuildTemplate("separated", (5,))
    Calls: list[str] = []

    def Solve(Domain, _Remaining):
        Calls.append(Domain.PlacementFingerprint)
        return NativeResult(
            Success=False,
            ExpansionCount=16,
            BudgetExhausted=True,
        )

    Result = SolveRawTrackAssignmentProblem(
        RawTrackAssignmentProblem(
            Templates=(Compact, Separated),
            MaximumAssignmentExpansions=16,
        ),
        Solve,
    )

    assert Calls == ["placement-compact"]
    assert Result.Success is False
    assert Result.Complete is False
    assert Result.Unsatisfiable is False
    assert Result.IncompleteReason == "assignment-work-cap"


def test_incomplete_template_is_terminal_before_native_assignment():
    Incomplete = BuildTemplate("compact", (4,), Complete=False)
    Other = BuildTemplate("separated", (5,))

    Result = SolveRawTrackAssignmentProblem(
        RawTrackAssignmentProblem(
            Templates=(Incomplete, Other),
            MaximumAssignmentExpansions=16,
        ),
        lambda _Domain, _Remaining: (_ for _ in ()).throw(
            AssertionError("native assignment must not run")
        ),
    )

    assert Result.Success is False
    assert Result.Complete is False
    assert Result.Unsatisfiable is False
    assert Result.IncompleteReason == "incomplete-template-domain"
    assert [Value.TemplateId for Value in Result.Attempts] == ["compact"]


def test_exhaustive_complete_empty_domain_retains_unsatisfiable_contract():
    Result = SolveRawTrackAssignmentProblem(
        RawTrackAssignmentProblem(
            Templates=(BuildTemplate("only", (1,), Empty=True),),
            MaximumAssignmentExpansions=16,
            NonExhaustiveTemplateDomain=False,
        ),
        lambda _Domain, _Remaining: (_ for _ in ()).throw(
            AssertionError("complete empty domain must not call native")
        ),
    )

    assert Result.Success is False
    assert Result.Complete is True
    assert Result.Unsatisfiable is True
    assert Result.FirstConflictSignals == ("Signal",)


def test_excluded_primary_request_shapes_require_nonexhaustive_portfolio():
    Template = BuildTemplate("only", (1,))
    Template = replace(
        Template,
        Domain=replace(
            Template.Domain,
            Diagnostics=((
                "ExcludedConfiguredRequestCounts",
                (("Signal", 1),),
            ),),
        ),
    )

    with pytest.raises(ValueError, match="cannot be declared exhaustive"):
        RawTrackAssignmentProblem(
            Templates=(Template,),
            MaximumAssignmentExpansions=16,
            NonExhaustiveTemplateDomain=False,
        )


def test_existing_native_context_binding_receives_global_remainder():
    First = BuildTemplate("first", (1,))
    Second = BuildTemplate("second", (2,))

    class Context:
        def __init__(self) -> None:
            self.Calls: list[tuple[str, int, int]] = []

        def PlanAuthoritativeRoutesBounded(
            self,
            Values,
            _ResourceCount,
            MaximumExpansions,
            RemainingMilliseconds,
        ):
            CandidateId = Values[0][1]
            self.Calls.append((
                CandidateId,
                MaximumExpansions,
                RemainingMilliseconds,
            ))
            if CandidateId == "first-candidate":
                return NativeResult(Success=False, ExpansionCount=2)
            return NativeResult(
                Success=True,
                ExpansionCount=1,
                CandidateId="second-candidate",
            )

    ContextValue = Context()
    Result = SolveRawTrackAssignmentProblemWithContext(
        RawTrackAssignmentProblem(
            Templates=(First, Second),
            MaximumAssignmentExpansions=16,
        ),
        Context=ContextValue,
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert [Value[:2] for Value in ContextValue.Calls] == [
        ("first-candidate", 16),
        ("second-candidate", 14),
    ]
    assert all(Value[2] > 0 for Value in ContextValue.Calls)
    assert Result.Success is True
    assert Result.SelectedTemplateId == "second"


def test_extracted_domain_context_overrides_fixture_fallback_context():
    """Each placement world may retain its own local-index executor."""
    Template = BuildTemplate("only", (1,))

    class Context:
        def __init__(self) -> None:
            self.Calls = 0

        def PlanAuthoritativeRoutesBounded(
            self,
            Values,
            _ResourceCount,
            _MaximumExpansions,
            _RemainingMilliseconds,
        ):
            self.Calls += 1
            return NativeResult(
                Success=True,
                ExpansionCount=1,
                CandidateId=Values[0][1],
            )

    Attached = Context()
    Fallback = Context()
    Template = replace(
        Template,
        Domain=replace(
            Template.Domain,
            NativeAssignmentContext=Attached,
        ),
    )

    Result = SolveRawTrackAssignmentProblemWithContext(
        RawTrackAssignmentProblem(
            Templates=(Template,),
            MaximumAssignmentExpansions=16,
        ),
        Context=Fallback,
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert Result.Success is True
    assert Attached.Calls == 1
    assert Fallback.Calls == 0

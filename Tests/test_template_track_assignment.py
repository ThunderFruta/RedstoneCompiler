"""Focused contracts for one bounded raw template assignment selector."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

import Compiler.Routing.AuthoritativePlanner as AuthoritativePlanner
import Compiler.Routing.TemplateAssignment as TemplateAssignment

from Compiler.Routing.AuthoritativePlanner import (
    AttachRawTrackAssignmentContractRequirements,
    BuildConditionalRawTrackAssignmentDomain,
    BuildPlacementAccessStubFactorDomain,
    BuildRawRouteGuideFactorDomain,
    BuildTrackAssignmentPreparationFromRawDomain,
    ComposeRawTrackAssignmentFactorDomains,
    PromoteRawTrackAssignmentBaseClaims,
    RawTrackAssignmentBaseClaim,
    RawTrackAssignmentDomain,
    RawTrackAssignmentValue,
    SelectDiverseRouteGuideFactorShapes,
)
from Compiler.Routing.ResourceGraph import (
    IndexedRoutingResourceGraph,
    RoutingResourceClaims,
)
from Compiler.Routing.Reliability import RoutingDeadline
from Compiler.Routing.TemplateAssignment import (
    BuildCompactFactorCatalog,
    CompactFactorMemberSource,
    RawTrackAssignmentProblem,
    RawTrackAssignmentTemplate,
    SolveCompactFactorCatalogPythonOracleForTests,
    SolveCompactFactorCatalogWithContext,
    SolveRawTrackAssignmentProblem,
    SolveRawTrackAssignmentProblemWithContext,
)


def BuildCompactSource(
    TemplateId: str,
    Objective: tuple[int, ...],
    Values: tuple[RawTrackAssignmentValue, ...],
    *,
    RequiredVariables: tuple[str, ...] = ("A", "B"),
    ResourceGraphFingerprint: str = "graph",
    Fabric=None,
) -> CompactFactorMemberSource:
    Positions = tuple(sorted({
        Position
        for Value in Values
        for Cells in (
            Value.Claims.WireCells,
            Value.Claims.SupportCells,
            Value.Claims.RequiredAirCells,
            Value.Claims.ElectricalCells,
        )
        for Position in Cells
    }))
    Domain = RawTrackAssignmentDomain(
        ResourcePositions=Positions,
        Values=Values,
        BaseClaims=(),
        CandidateCounts=tuple(
            (
                Variable,
                sum(Value.Signal == Variable for Value in Values),
            )
            for Variable in RequiredVariables
        ),
        CandidateDomainFingerprint=f"candidate-{TemplateId}",
        LocalClaimDomainFingerprint=f"local-{TemplateId}",
        PlacementFingerprint=f"placement-{TemplateId}",
        ResourceGraphFingerprint=ResourceGraphFingerprint,
        PortalDomainFingerprint=f"portal-{TemplateId}",
        Complete=True,
        MaximumAssignmentExpansions=64,
    )
    Fabric = Fabric or SimpleNamespace(
        Complete=True,
        IncompleteReason="",
        FabricFingerprint="fabric",
        AccessRingFingerprint="shell",
        TerminalDomains=(),
    )
    return CompactFactorMemberSource(
        TemplateId=TemplateId,
        Objective=Objective,
        ContractRequirements=(
            ("core", f"core-{TemplateId}"),
            ("interface", f"interface-{TemplateId}"),
            ("layers", str(Objective[-1])),
            ("member", TemplateId),
        ),
        GuideDomain=Domain,
        Fabric=Fabric,
        FabricFingerprint=Fabric.FabricFingerprint,
    )


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


def test_compose_access_and_guide_factors_preserves_physical_identity():
    GuidePosition = (1, 3, 1)
    AccessPosition = (2, 3, 1)
    UnclaimedGuidePosition = (3, 3, 1)
    Guide = RawTrackAssignmentDomain(
        ResourcePositions=(GuidePosition, UnclaimedGuidePosition),
        Values=(RawTrackAssignmentValue(
            Signal="Signal",
            CandidateId="guide",
            Claims=RoutingResourceClaims(
                WireCells=frozenset({GuidePosition}),
            ),
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
            ValueKind="guide-factor",
        ),),
        BaseClaims=(),
        CandidateCounts=(("Signal", 1),),
        CandidateDomainFingerprint="physical-guide-domain",
        LocalClaimDomainFingerprint="",
        PlacementFingerprint="placement",
        ResourceGraphFingerprint="resources",
        PortalDomainFingerprint="portals",
        Complete=True,
        MaximumAssignmentExpansions=16,
    )
    Access = replace(
        Guide,
        ResourcePositions=(AccessPosition,),
        Values=(RawTrackAssignmentValue(
            Signal="__access_terminal__:Signal:root",
            OwnerSignal="Signal",
            CandidateId="stub",
            Claims=RoutingResourceClaims(
                WireCells=frozenset({AccessPosition}),
            ),
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
            ValueKind="contract-claim",
        ),),
        CandidateCounts=(("__access_terminal__:Signal:root", 1),),
        CandidateDomainFingerprint="access-domain",
        PortalDomainFingerprint="access-portals",
    )

    Composed = ComposeRawTrackAssignmentFactorDomains(
        Guide,
        Access,
        MaximumAssignmentExpansions=16,
    )

    assert Composed.ResourcePositions == (GuidePosition, AccessPosition)
    assert Composed.CandidateDomainFingerprint == "physical-guide-domain"
    assert Composed.CandidateCounts == (
        ("Signal", 1),
        ("__access_terminal__:Signal:root", 1),
    )
    assert dict(Composed.Diagnostics)["ComposedAccessGuideFactors"] is True


def test_raw_value_rejects_two_values_for_one_contract_dimension():
    with pytest.raises(
        ValueError,
        match="requirement names must select exactly one value",
    ):
        RawTrackAssignmentValue(
            Signal="Signal",
            CandidateId="contradictory-guide",
            Claims=RoutingResourceClaims(),
            MaterialCost=0,
            FootprintGrowth=0,
            Length=0,
            BendCount=0,
            ViaCount=0,
            ValueKind="guide-factor",
            ContractRequirements=(
                ("access-stub:Signal:target-0", "0"),
                ("access-stub:Signal:target-0", "1"),
            ),
        )


def test_route_guide_factor_frontier_round_robins_layers_and_axes(
    monkeypatch,
):
    Shapes = tuple(
        SimpleNamespace(Id=f"{Layer}:{Axis}:{Index}", Layer=Layer, Axis=Axis)
        for Layer in range(3)
        for Axis in ("X", "Z")
        for Index in range(3)
    )
    monkeypatch.setattr(
        AuthoritativePlanner,
        "RouteGuideFactorCandidateId",
        lambda Shape: Shape.Id,
    )

    Selected = SelectDiverseRouteGuideFactorShapes(Shapes, 8)

    assert tuple((Value.Layer, Value.Axis) for Value in Selected[:6]) == (
        (0, "X"),
        (0, "Z"),
        (1, "X"),
        (1, "Z"),
        (2, "X"),
        (2, "Z"),
    )
    assert {Value.Layer for Value in Selected} == {0, 1, 2}
    assert {Value.Axis for Value in Selected} == {"X", "Z"}


def test_frozen_preparation_retains_selected_claims_by_logical_owner():
    First = (1, 3, 1)
    Second = (2, 3, 1)
    Domain = RawTrackAssignmentDomain(
        ResourcePositions=(First, Second),
        Values=(
            RawTrackAssignmentValue(
                Signal="Signal",
                CandidateId="guide",
                Claims=RoutingResourceClaims(
                    WireCells=frozenset({First}),
                ),
                MaterialCost=1,
                FootprintGrowth=1,
                Length=1,
                BendCount=0,
                ViaCount=0,
                ValueKind="guide-factor",
            ),
            RawTrackAssignmentValue(
                Signal="__access_terminal__:Signal:root",
                OwnerSignal="Signal",
                CandidateId="stub",
                Claims=RoutingResourceClaims(
                    RequiredAirCells=frozenset({Second}),
                ),
                MaterialCost=1,
                FootprintGrowth=1,
                Length=1,
                BendCount=0,
                ViaCount=0,
                ValueKind="contract-claim",
            ),
        ),
        BaseClaims=(),
        CandidateCounts=(
            ("Signal", 1),
            ("__access_terminal__:Signal:root", 1),
        ),
        CandidateDomainFingerprint="physical-guide-domain",
        LocalClaimDomainFingerprint="",
        PlacementFingerprint="placement",
        ResourceGraphFingerprint="resources",
        PortalDomainFingerprint="portals",
        Complete=True,
        MaximumAssignmentExpansions=16,
    )
    Result = SimpleNamespace(
        Success=True,
        SelectedCandidateIds=(
            ("Signal", "guide"),
            ("__access_terminal__:Signal:root", "stub"),
        ),
        ExpansionCount=2,
        BudgetExhausted=False,
        DeadlineExceeded=False,
        ConflictSignals=(),
        ConflictResourceIndices=(),
    )

    Preparation = BuildTrackAssignmentPreparationFromRawDomain(
        Domain,
        Result,
    )

    ClaimsByOwner = dict(Preparation.SelectedCapacityClaimsByOwner)
    assert tuple(ClaimsByOwner) == ("Signal",)
    assert ClaimsByOwner["Signal"].WireCells == frozenset({First})
    assert ClaimsByOwner["Signal"].RequiredAirCells == frozenset({Second})


def test_route_guide_factor_is_selected_separately_from_route_candidates():
    Position = (3, 2, 4)
    Descriptor = SimpleNamespace(
        Layer=1,
        Axis="X",
        Lane=4,
        Guide=frozenset({(3, 4)}),
        GuideExpansion=2,
        RoutingY=2,
        SourcePortal=SimpleNamespace(PortalId="source"),
        TargetPortals=(SimpleNamespace(PortalId="target"),),
    )
    Factor = RawTrackAssignmentValue(
        Signal="Signal",
        CandidateId="guide-factor",
        SourceCandidateId="guide-factor",
        Claims=RoutingResourceClaims(WireCells=frozenset({Position})),
        MaterialCost=1,
        FootprintGrowth=1,
        Length=1,
        BendCount=0,
        ViaCount=0,
        ValueKind="guide-factor",
        RouteGuideFactorDescriptor=Descriptor,
    )
    Domain = BuildRawRouteGuideFactorDomain(
        ValuesBySignal={"Signal": (Factor,)},
        AssignmentIndexed=IndexedRoutingResourceGraph(
            ResourcePositions=(),
            PositionIndices={},
        ),
        PlacementFingerprint="placement",
        ResourceGraphFingerprint="resources",
        PortalDomainFingerprint="portals",
        Complete=True,
        IncompleteReason="",
        MaximumAssignmentExpansions=8,
    )

    Selected = BuildTrackAssignmentPreparationFromRawDomain(
        Domain,
        NativeResult(
            Success=True,
            ExpansionCount=1,
            CandidateId="guide-factor",
        ),
    )

    assert Selected.SelectedCandidateIds == ()
    assert Selected.SelectedRouteGuideFactorChoiceIds == (
        ("Signal", "guide-factor"),
    )
    assert Selected.SelectedRouteGuideFactorDescriptors == (
        ("Signal", "guide-factor", Descriptor),
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


def test_nonexhaustive_complete_cores_are_terminal_incomplete():
    """A finite compact portfolio is not an exhaustive UNSAT proof."""
    Result = SolveRawTrackAssignmentProblem(
        RawTrackAssignmentProblem(
            Templates=(BuildTemplate("only", (1,), Empty=True),),
            MaximumAssignmentExpansions=16,
            NonExhaustiveTemplateDomain=True,
        ),
        lambda _Domain, _Remaining: (_ for _ in ()).throw(
            AssertionError("complete empty domain must not call native")
        ),
    )

    assert Result.Success is False
    assert Result.Complete is False
    assert Result.Unsatisfiable is False
    assert Result.IncompleteReason == "non-exhaustive-template-domain"


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


def test_native_template_binding_selects_one_fixed_domain_with_one_call(
    monkeypatch,
):
    """The immutable placement choice is one native capacity operation."""
    Compact = BuildTemplate("compact", (4, 8))
    Separated = BuildTemplate("separated", (4, 8))
    Calls: list[tuple[object, ...]] = []

    def Solve(Templates, MaximumExpansions, RemainingMilliseconds, NonExhaustive):
        Calls.append((
            Templates,
            MaximumExpansions,
            RemainingMilliseconds,
            NonExhaustive,
        ))
        return SimpleNamespace(
            Status="Feasible",
            Success=True,
            Complete=True,
            Unsatisfiable=False,
            IncompleteReason="",
            SelectedTemplateId="separated",
            SelectedCandidateIds=(("Signal", "separated-candidate"),),
            ExpansionCount=7,
            BudgetExhausted=False,
            DeadlineExceeded=False,
            ConflictSignals=("A", "B"),
            ConflictResourceIndices=(0,),
            AttemptedTemplateIds=("compact", "separated"),
        )

    monkeypatch.setattr(
        TemplateAssignment,
        "_SolveTemplateAssignmentDomainsBounded",
        Solve,
    )
    Result = SolveRawTrackAssignmentProblemWithContext(
        RawTrackAssignmentProblem(
            Templates=(Separated, Compact),
            MaximumAssignmentExpansions=16,
        ),
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert len(Calls) == 1
    Payload, MaximumExpansions, RemainingMilliseconds, NonExhaustive = Calls[0]
    assert [Value[0] for Value in Payload] == ["compact", "separated"]
    assert MaximumExpansions == 16
    assert RemainingMilliseconds > 0
    assert NonExhaustive is True
    assert Result.Success is True
    assert Result.SelectedTemplateId == "separated"
    assert Result.Preparation is not None
    assert Result.Preparation.SelectedCandidateIds == (
        ("Signal", "separated-candidate"),
    )
    assert Result.ExpansionCount == 7


def test_one_native_call_selects_l2_when_cheaper_l1_conflicts(
    monkeypatch,
):
    """Layer objectives rank feasible worlds; they never prune capacity."""
    if TemplateAssignment._SolveTemplateAssignmentDomainsBounded is None:
        pytest.skip("native template assignment binding is unavailable")
    L1 = BuildTemplate("fixed-l1", (100, 20, 1))
    L1Position = L1.Domain.ResourcePositions[0]
    L1 = replace(
        L1,
        Domain=replace(
            L1.Domain,
            Values=(replace(
                L1.Domain.Values[0],
                Claims=RoutingResourceClaims(
                    WireCells=frozenset({L1Position}),
                    ElectricalCells=frozenset({L1Position}),
                ),
            ),),
            BaseClaims=(RawTrackAssignmentBaseClaim(
                Signal="Existing",
                ClaimId="existing-l1",
                Claims=RoutingResourceClaims(
                    WireCells=frozenset({L1Position}),
                    ElectricalCells=frozenset({L1Position}),
                ),
            ),),
        ),
    )
    L2 = BuildTemplate("fixed-l2", (100, 20, 2))
    NativeSolve = TemplateAssignment._SolveTemplateAssignmentDomainsBounded
    Calls: list[tuple[object, ...]] = []

    def Solve(*Arguments):
        Calls.append(Arguments)
        return NativeSolve(*Arguments)

    monkeypatch.setattr(
        TemplateAssignment,
        "_SolveTemplateAssignmentDomainsBounded",
        Solve,
    )

    Result = SolveRawTrackAssignmentProblemWithContext(
        RawTrackAssignmentProblem(
            Templates=(L1, L2),
            MaximumAssignmentExpansions=16,
        ),
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert len(Calls) == 1
    assert [Value[0] for Value in Calls[0][0]] == [
        "fixed-l1",
        "fixed-l2",
    ]
    assert Result.Success is True
    assert Result.Complete is True
    assert Result.SelectedTemplateId == "fixed-l2"
    assert Result.Preparation is not None
    assert Result.Preparation.SelectedCandidateIds == (
        ("Signal", "fixed-l2-candidate"),
    )


def test_raw_value_encodes_an_optional_conditional_template_key():
    Domain = BuildDomain("template-key")
    Value = replace(Domain.Values[0], TemplateKey="compact-interface")
    Domain = replace(Domain, Values=(Value,))

    Encoded = Domain.NativeCandidateValues()

    assert Encoded[0][-2] == "template=compact-interface"
    assert Encoded[0][-1] == "Signal"
    assert Domain.Values[0].ToDictionary()["TemplateKey"] == (
        "compact-interface"
    )


def test_conditional_raw_domain_selects_one_coherent_interface_member():
    def Member(TemplateId: str, SecondPosition: tuple[int, int, int]):
        FirstPosition = (1, 1, 1)
        Values = (
            RawTrackAssignmentValue(
                Signal="A",
                CandidateId="A",
                Claims=RoutingResourceClaims(
                    WireCells=frozenset({FirstPosition}),
                ),
                MaterialCost=1,
                FootprintGrowth=1,
                Length=1,
                BendCount=0,
                ViaCount=0,
            ),
            RawTrackAssignmentValue(
                Signal="B",
                CandidateId="B",
                Claims=RoutingResourceClaims(
                    ElectricalCells=frozenset({SecondPosition}),
                ),
                MaterialCost=1,
                FootprintGrowth=1,
                Length=1,
                BendCount=0,
                ViaCount=0,
            ),
        )
        return RawTrackAssignmentDomain(
            ResourcePositions=tuple(sorted({FirstPosition, SecondPosition})),
            Values=Values,
            BaseClaims=(),
            CandidateCounts=(("A", 1), ("B", 1)),
            CandidateDomainFingerprint=f"candidates-{TemplateId}",
            LocalClaimDomainFingerprint=f"local-{TemplateId}",
            PlacementFingerprint=f"placement-{TemplateId}",
            ResourceGraphFingerprint=f"resources-{TemplateId}",
            PortalDomainFingerprint=f"portals-{TemplateId}",
            Complete=True,
            MaximumAssignmentExpansions=16,
        )

    # The compact member has A/B on one capacity-one cell; the access
    # separated member has a disjoint B claim.  Mixing A from one template
    # with B from another would falsely succeed without TemplateKey.
    Domain = BuildConditionalRawTrackAssignmentDomain(
        (
            ("compact", Member("compact", (1, 1, 1))),
            ("separated", Member("separated", (2, 1, 1))),
        ),
        MaximumAssignmentExpansions=16,
    )
    Result = SolveRawTrackAssignmentProblemWithContext(
        RawTrackAssignmentProblem(
            Templates=(RawTrackAssignmentTemplate(
                TemplateId="conditional-interface",
                Objective=(1,),
                Domain=Domain,
            ),),
            MaximumAssignmentExpansions=16,
        ),
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert Result.Success is True
    assert Result.Preparation is not None
    assert Result.Preparation.SelectedCandidateIds == (
        ("A", "A"),
        ("B", "B"),
    )
    assert Result.Preparation.SelectedConditionalTemplateKey == "separated"
    assert [
        Value.ToDictionary()["SourceCandidateId"]
        for Value in Domain.Values
        if (
            Value.TemplateKey == "separated"
            and Value.ValueKind == "ordinary"
        )
    ] == ["A", "B"]
    assert {Value.TemplateKey for Value in Domain.Values} == {
        "compact", "separated",
    }


def test_conditional_raw_domain_models_missing_terminal_factor_as_absent_claim():
    """A terminal interior to one world is not an incomplete world."""
    def Member(
        TemplateId: str,
        Signal: str,
    ) -> RawTrackAssignmentDomain:
        return RawTrackAssignmentDomain(
            ResourcePositions=(),
            Values=(RawTrackAssignmentValue(
                Signal=Signal,
                CandidateId=f"{TemplateId}-{Signal}",
                Claims=RoutingResourceClaims(),
                MaterialCost=0,
                FootprintGrowth=0,
                Length=0,
                BendCount=0,
                ViaCount=0,
                ValueKind="contract-claim",
            ),),
            BaseClaims=(),
            CandidateCounts=((Signal, 1),),
            CandidateDomainFingerprint=f"domain-{TemplateId}",
            LocalClaimDomainFingerprint="",
            PlacementFingerprint=TemplateId,
            ResourceGraphFingerprint="",
            PortalDomainFingerprint="",
            Complete=True,
            MaximumAssignmentExpansions=16,
        )

    Aggregate = BuildConditionalRawTrackAssignmentDomain(
        (("north", Member("north", "A")),
         ("south", Member("south", "B"))),
        MaximumAssignmentExpansions=16,
        ContractRequirementsByTemplateId={
            "north": (("member", "north"),),
            "south": (("member", "south"),),
        },
    )

    assert Aggregate.CandidateCounts == (("A", 2), ("B", 2), (
        "__pre_route_contract__:member", 2,
    ))
    assert {
        (Value.TemplateKey, Value.Signal, Value.SourceCandidateId)
        for Value in Aggregate.Values
        if Value.SourceCandidateId.startswith("__absent__:")
    } == {
        ("north", "B", "__absent__:B"),
        ("south", "A", "__absent__:A"),
    }


def test_named_contract_requirements_allow_factored_core_and_interface_values():
    """Values share only the constraints their physical claims depend on."""
    First = RawTrackAssignmentValue(
        Signal="Core",
        CandidateId="core-a",
        Claims=RoutingResourceClaims(),
        MaterialCost=0,
        FootprintGrowth=0,
        Length=0,
        BendCount=0,
        ViaCount=0,
        ValueKind="contract-claim",
        ContractRequirements=(("core", "a"),),
    )
    Compatible = replace(
        First,
        Signal="Interface",
        CandidateId="interface-north",
        ContractRequirements=(("interface", "north"),),
    )
    assert First.EncodedContractRequirements == "core=a"
    assert Compatible.EncodedContractRequirements == "interface=north"
    assert First.ToDictionary()["ValueKind"] == "contract-claim"


def test_native_assignment_accepts_independent_named_contract_dimensions():
    """A core and an interface are compatible unless they constrain one name."""
    Values = (
        RawTrackAssignmentValue(
            Signal="__contract_core__",
            CandidateId="core-a",
            Claims=RoutingResourceClaims(),
            MaterialCost=0,
            FootprintGrowth=0,
            Length=0,
            BendCount=0,
            ViaCount=0,
            ValueKind="contract-claim",
            ContractRequirements=(("core", "a"),),
        ),
        RawTrackAssignmentValue(
            Signal="__contract_interface__",
            CandidateId="north",
            Claims=RoutingResourceClaims(),
            MaterialCost=0,
            FootprintGrowth=0,
            Length=0,
            BendCount=0,
            ViaCount=0,
            ValueKind="contract-claim",
            ContractRequirements=(("interface", "north"),),
        ),
    )
    Domain = RawTrackAssignmentDomain(
        ResourcePositions=((0, 0, 0),),
        Values=Values,
        BaseClaims=(),
        CandidateCounts=(
            ("__contract_core__", 1),
            ("__contract_interface__", 1),
        ),
        CandidateDomainFingerprint="named-contracts",
        LocalClaimDomainFingerprint="",
        PlacementFingerprint="",
        ResourceGraphFingerprint="",
        PortalDomainFingerprint="",
        Complete=True,
        MaximumAssignmentExpansions=16,
    )

    Result = SolveRawTrackAssignmentProblemWithContext(
        RawTrackAssignmentProblem(
            Templates=(RawTrackAssignmentTemplate(
                TemplateId="factored",
                Objective=(0,),
                Domain=Domain,
            ),),
            MaximumAssignmentExpansions=16,
        ),
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert Result.Success is True
    assert Result.Preparation is not None
    assert Result.Preparation.SelectedCandidateIds == ()
    assert Result.Preparation.SelectedContractRequirements == (
        ("core", "a"),
        ("interface", "north"),
    )


def test_base_claim_promotion_makes_member_geometry_conditional():
    Position = (3, 1, 2)
    Domain = RawTrackAssignmentDomain(
        ResourcePositions=(Position,),
        Values=(),
        BaseClaims=(RawTrackAssignmentBaseClaim(
            Signal="Local",
            ClaimId="local",
            Claims=RoutingResourceClaims(WireCells=frozenset({Position})),
        ),),
        CandidateCounts=(),
        CandidateDomainFingerprint="base",
        LocalClaimDomainFingerprint="base",
        PlacementFingerprint="base",
        ResourceGraphFingerprint="base",
        PortalDomainFingerprint="base",
        Complete=True,
        MaximumAssignmentExpansions=16,
    )

    Promoted = PromoteRawTrackAssignmentBaseClaims(
        Domain,
        ContractRequirements=(("core", "compact"),),
    )

    assert Promoted.BaseClaims == ()
    assert Promoted.CandidateCounts == (("Local", 1),)
    assert Promoted.Values[0].ValueKind == "contract-claim"
    assert Promoted.Values[0].Claims.WireCells == frozenset({Position})
    assert Promoted.Values[0].ContractRequirementItems == (
        ("core", "compact"),
    )


def test_contract_decoration_preserves_route_candidate_identity():
    """Named selector contracts cannot invalidate a frozen route cache."""
    Domain = BuildDomain("compact")

    Decorated = AttachRawTrackAssignmentContractRequirements(
        Domain,
        ContractRequirements=(
            ("core", "compact"),
            ("interface", "perimeter"),
            ("layers", "1"),
            ("member", "compact-l1"),
        ),
    )

    assert Decorated.CandidateDomainFingerprint == (
        Domain.CandidateDomainFingerprint
    )
    assert Decorated.DomainFingerprint != Domain.DomainFingerprint
    ContractValues = [
        Value for Value in Decorated.Values
        if Value.ValueKind == "contract-claim"
    ]
    assert len(ContractValues) == 1
    assert ContractValues[0].ContractRequirementItems == (
        ("core", "compact"),
        ("interface", "perimeter"),
        ("layers", "1"),
        ("member", "compact-l1"),
    )


def test_conditional_domain_preserves_named_member_contract_dimensions():
    Domain = RawTrackAssignmentDomain(
        ResourcePositions=((0, 0, 0),),
        Values=(RawTrackAssignmentValue(
            Signal="Signal",
            CandidateId="route",
            Claims=RoutingResourceClaims(),
            MaterialCost=0,
            FootprintGrowth=0,
            Length=0,
            BendCount=0,
            ViaCount=0,
        ),),
        BaseClaims=(),
        CandidateCounts=(("Signal", 1),),
        CandidateDomainFingerprint="member",
        LocalClaimDomainFingerprint="",
        PlacementFingerprint="",
        ResourceGraphFingerprint="",
        PortalDomainFingerprint="",
        Complete=True,
        MaximumAssignmentExpansions=16,
    )
    Aggregate = BuildConditionalRawTrackAssignmentDomain(
        (("member-a", Domain),),
        MaximumAssignmentExpansions=16,
        ContractRequirementsByTemplateId={
            "member-a": (
                ("member", "member-a"),
                ("core", "core-a"),
                ("interface", "north"),
                ("layers", "3"),
            ),
        },
    )

    assert Aggregate.Values[0].ContractRequirementItems == (
        ("core", "core-a"),
        ("interface", "north"),
        ("layers", "3"),
        ("member", "member-a"),
    )


def test_access_stub_factor_uses_one_required_value_per_terminal():
    First = (1, 1, 1)
    Second = (2, 1, 1)
    Fabric = SimpleNamespace(
        Complete=True,
        IncompleteReason="",
        FabricFingerprint="fabric",
        TerminalDomains=(
            SimpleNamespace(
                Signal="A",
                Terminal=First,
                Complete=True,
                EscapeStubs=(
                    SimpleNamespace(
                        PhysicalClaims=RoutingResourceClaims(
                            WireCells=frozenset({First}),
                        ),
                        Path=(First,),
                    ),
                    SimpleNamespace(
                        PhysicalClaims=RoutingResourceClaims(
                            WireCells=frozenset({Second}),
                        ),
                        Path=(First, Second),
                    ),
                ),
            ),
        ),
    )
    Domain = BuildPlacementAccessStubFactorDomain(
        Fabric,
        ContractRequirements=(("core", "a"), ("layers", "2")),
        MaximumAssignmentExpansions=16,
    )

    assert Domain.Complete is True
    assert Domain.CandidateCounts == (("__access_terminal__:0:A", 2),)
    assert all(Value.ValueKind == "contract-claim" for Value in Domain.Values)
    assert tuple(
        Value.ContractRequirementItems for Value in Domain.Values
    ) == (
        (
            ("access-stub:0:A", "0"),
            ("core", "a"),
            ("layers", "2"),
        ),
        (
            ("access-stub:0:A", "1"),
            ("core", "a"),
            ("layers", "2"),
        ),
    )


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


def test_compact_catalog_interns_only_exact_complete_physical_primitives():
    Position = (1, 2, 3)

    def Value(CandidateId: str, Claims: RoutingResourceClaims):
        return RawTrackAssignmentValue(
            Signal="A",
            CandidateId=CandidateId,
            SourceCandidateId="shared-source",
            Claims=Claims,
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
            ValueKind="local-claim",
        )

    First = BuildCompactSource(
        "first",
        (1, 1, 1),
        (Value("a-first", RoutingResourceClaims(
            WireCells=frozenset({Position}),
        )),),
        RequiredVariables=("A",),
    )
    Identical = BuildCompactSource(
        "identical",
        (2, 1, 1),
        (Value("a-identical", RoutingResourceClaims(
            WireCells=frozenset({Position}),
        )),),
        RequiredVariables=("A",),
    )
    ChangedCategory = BuildCompactSource(
        "category",
        (3, 1, 1),
        (Value("a-category", RoutingResourceClaims(
            ElectricalCells=frozenset({Position}),
        )),),
        RequiredVariables=("A",),
    )
    ChangedGraph = BuildCompactSource(
        "graph",
        (4, 1, 1),
        (Value("a-graph", RoutingResourceClaims(
            WireCells=frozenset({Position}),
        )),),
        RequiredVariables=("A",),
        ResourceGraphFingerprint="different-graph",
    )

    Catalog = BuildCompactFactorCatalog(
        (First, Identical, ChangedCategory, ChangedGraph),
        MaximumAssignmentExpansions=64,
    )

    assert len(Catalog.Primitives) == 3
    assert Catalog.PrimitiveCacheHits == 1
    assert Catalog.PrimitiveCacheMisses == 3
    assert len({
        Primitive.PhysicalFingerprint
        for Primitive in Catalog.Primitives
    }) == 3
    Rebuilt = BuildCompactFactorCatalog(
        (First, Identical, ChangedCategory, ChangedGraph),
        MaximumAssignmentExpansions=64,
    )
    assert Rebuilt.CatalogFingerprint == Catalog.CatalogFingerprint


def test_compact_catalog_native_selection_matches_python_oracle(monkeypatch):
    Shared = (1, 1, 1)
    ClearA = (2, 1, 1)
    ClearB = (4, 1, 1)

    def Candidate(
        Signal: str,
        CandidateId: str,
        Claims: RoutingResourceClaims,
    ) -> RawTrackAssignmentValue:
        return RawTrackAssignmentValue(
            Signal=Signal,
            CandidateId=CandidateId,
            Claims=Claims,
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
            ValueKind="local-claim",
        )

    Conflicting = BuildCompactSource(
        "l1",
        (10, 10, 1),
        (
            Candidate("A", "a-l1", RoutingResourceClaims(
                WireCells=frozenset({Shared}),
            )),
            Candidate("B", "b-l1", RoutingResourceClaims(
                ElectricalCells=frozenset({Shared}),
            )),
        ),
    )
    Feasible = BuildCompactSource(
        "l2",
        (10, 10, 2),
        (
            Candidate("A", "a-l2", RoutingResourceClaims(
                WireCells=frozenset({ClearA}),
            )),
            Candidate("B", "b-l2", RoutingResourceClaims(
                WireCells=frozenset({ClearB}),
            )),
        ),
    )
    Catalog = BuildCompactFactorCatalog(
        (Feasible, Conflicting),
        MaximumAssignmentExpansions=64,
    )
    Oracle = SolveCompactFactorCatalogPythonOracleForTests(Catalog)
    if TemplateAssignment._SolveCompactTemplateFactorCatalogBounded is None:
        pytest.skip("native compact catalog binding is unavailable")
    NativeBinding = (
        TemplateAssignment._SolveCompactTemplateFactorCatalogBounded
    )
    Calls = []

    def Solve(*Arguments):
        Calls.append(Arguments)
        return NativeBinding(*Arguments)

    monkeypatch.setattr(
        TemplateAssignment,
        "_SolveCompactTemplateFactorCatalogBounded",
        Solve,
    )
    Native = SolveCompactFactorCatalogWithContext(
        Catalog,
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert len(Calls) == 1
    assert Native.Success is Oracle.Success is True
    assert Native.Complete is Oracle.Complete is True
    assert Native.SelectedTemplateId == Oracle.SelectedTemplateId == "l2"
    assert Native.Preparation is not None
    assert Native.Preparation.SelectedLocalClaimChoiceIds == (
        ("A", "a-l2"),
        ("B", "b-l2"),
    )
    assert Oracle.SelectedCandidateIds == (
        ("A", "a-l2"),
        ("B", "b-l2"),
    )


def test_compact_catalog_nonexhaustive_failure_is_incomplete_not_unsat():
    Shared = (1, 1, 1)
    Values = tuple(
        RawTrackAssignmentValue(
            Signal=Signal,
            CandidateId=f"{Signal}-only",
            Claims=(
                RoutingResourceClaims(WireCells=frozenset({Shared}))
                if Signal == "A"
                else RoutingResourceClaims(
                    ElectricalCells=frozenset({Shared}),
                )
            ),
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
            ValueKind="local-claim",
        )
        for Signal in ("A", "B")
    )
    Catalog = BuildCompactFactorCatalog(
        (BuildCompactSource("conflict", (1,), Values),),
        MaximumAssignmentExpansions=64,
    )
    Oracle = SolveCompactFactorCatalogPythonOracleForTests(Catalog)

    assert Oracle.Success is False
    assert Oracle.Complete is False
    assert Oracle.Unsatisfiable is False
    assert Oracle.IncompleteReason == "non-exhaustive-template-domain"
    if TemplateAssignment._SolveCompactTemplateFactorCatalogBounded is None:
        pytest.skip("native compact catalog binding is unavailable")
    Native = SolveCompactFactorCatalogWithContext(
        Catalog,
        Deadline=RoutingDeadline.Start(1.0),
    )
    assert Native.Success is False
    assert Native.Complete is False
    assert Native.Unsatisfiable is False
    assert Native.IncompleteReason == "non-exhaustive-template-domain"


def test_compact_catalog_same_owner_access_and_route_overlap_is_legal():
    Position = (3, 2, 3)
    Stub = SimpleNamespace(
        PhysicalClaims=RoutingResourceClaims(
            WireCells=frozenset({Position}),
            ElectricalCells=frozenset({Position}),
        ),
        Path=(Position,),
        Ingress=Position,
    )
    Fabric = SimpleNamespace(
        Complete=True,
        IncompleteReason="",
        FabricFingerprint="same-owner-fabric",
        AccessRingFingerprint="same-owner-shell",
        TerminalDomains=(SimpleNamespace(
            Signal="A",
            Terminal=Position,
            LogicalKey="A:root",
            Complete=True,
            EscapeStubs=(Stub,),
        ),),
    )
    Guide = RawTrackAssignmentValue(
        Signal="A",
        OwnerSignal="A",
        CandidateId="guide",
        Claims=Stub.PhysicalClaims,
        MaterialCost=1,
        FootprintGrowth=1,
        Length=1,
        BendCount=0,
        ViaCount=0,
        ValueKind="local-claim",
        ContractRequirements=(("access-stub:A:root", "0"),),
    )
    Catalog = BuildCompactFactorCatalog(
        (BuildCompactSource(
            "same-owner",
            (1,),
            (Guide,),
            RequiredVariables=("A",),
            Fabric=Fabric,
        ),),
        MaximumAssignmentExpansions=16,
    )
    Oracle = SolveCompactFactorCatalogPythonOracleForTests(Catalog)

    assert Oracle.Success is True
    if TemplateAssignment._SolveCompactTemplateFactorCatalogBounded is None:
        pytest.skip("native compact catalog binding is unavailable")
    Native = SolveCompactFactorCatalogWithContext(
        Catalog,
        Deadline=RoutingDeadline.Start(1.0),
    )
    assert Native.Success is True
    assert Native.Preparation is not None
    assert Native.Preparation.SelectedLocalClaimChoiceIds == (
        ("A", "guide"),
    )
    assert Native.Preparation.SelectedContractClaimChoiceIds == (
        ("__access_terminal__:A:root", "stub:0:0"),
    )

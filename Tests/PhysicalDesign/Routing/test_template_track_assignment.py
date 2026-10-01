"""Focused contracts for one bounded raw template assignment selector."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from PhysicalDesign.Routing.Global.Orchestration.RunModels import RawTrackAssignmentDomain, RawTrackAssignmentValue
from PhysicalDesign.Routing.Global.Assignment.TrackPortfolio import BuildTrackAssignmentPreparationFromRawDomain
from PhysicalDesign.Contracts.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims
from PhysicalDesign.Runtime.Reliability import RoutingDeadline
from PhysicalDesign.Routing.Assignment.TemplateAssignment import RawTrackAssignmentCandidateInputManifest, RawTrackAssignmentMaterialization, RawTrackAssignmentPortfolio, RawTrackAssignmentPortfolioTemplate, RawTrackAssignmentProblem, RawTrackAssignmentTemplate, SolveRawTrackAssignmentPortfolio, SolveRawTrackAssignmentProblem, SolveRawTrackAssignmentProblemWithContext


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


def BuildPortfolioTemplate(
    TemplateId: str,
    Objective: tuple[int, ...],
) -> RawTrackAssignmentPortfolioTemplate:
    Manifest = RawTrackAssignmentCandidateInputManifest.Capture({
        "CandidateId": TemplateId,
        "Fixture": "template-track-assignment",
    })
    return RawTrackAssignmentPortfolioTemplate(
        TemplateId=TemplateId,
        Objective=Objective,
        MaterializationInputFingerprint=Manifest.ManifestFingerprint,
        MaterializationInputManifest=Manifest,
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
    FailureNet: str = "",
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
        FailureNet=FailureNet,
    )


def test_raw_domain_promotes_pin_access_identities_into_preparation():
    Domain = replace(
        BuildDomain("pin-access-identity"),
        PinAccessDomainFingerprint="pin-domain",
        PinAccessWitnessFingerprint="pin-witness",
    )
    Preparation = BuildTrackAssignmentPreparationFromRawDomain(
        Domain,
        NativeResult(
            Success=True,
            ExpansionCount=1,
            CandidateId="pin-access-identity-candidate",
        ),
    )

    assert Domain.ToDictionary()["PinAccessDomainFingerprint"] == "pin-domain"
    assert Domain.ToDictionary()["PinAccessWitnessFingerprint"] == "pin-witness"
    assert Preparation.PinAccessDomainFingerprint == "pin-domain"
    assert Preparation.PinAccessWitnessFingerprint == "pin-witness"
    assert Preparation.ToDictionary()["PinAccessDomainFingerprint"] == "pin-domain"
    assert Preparation.ToDictionary()["PinAccessWitnessFingerprint"] == "pin-witness"


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


def test_zero_expansion_empty_native_domain_retains_failure_net():
    Result = SolveRawTrackAssignmentProblem(
        RawTrackAssignmentProblem(
            Templates=(BuildTemplate("only", (1,)),),
            MaximumAssignmentExpansions=16,
        ),
        lambda _Domain, _Remaining: NativeResult(
            Success=False,
            ExpansionCount=0,
            FailureNet="Signal",
        ),
    )

    assert Result.Success is False
    assert Result.Attempts[0].ExpansionCount == 0
    assert Result.Attempts[0].FailureNet == "Signal"
    assert Result.Attempts[0].ToDictionary()["FailureNet"] == "Signal"


def test_fixed_portfolio_materializes_only_through_first_witness():
    Descriptors = tuple(
        BuildPortfolioTemplate(TemplateId, (Index,))
        for Index, TemplateId in enumerate(("compact", "incumbent", "worse"))
    )
    Materialized: list[str] = []
    NativeCalls: list[tuple[str, int]] = []

    def Materialize(Descriptor):
        Materialized.append(Descriptor.TemplateId)
        return RawTrackAssignmentMaterialization(
            TemplateId=Descriptor.TemplateId,
            MaterializationInputFingerprint=(
                Descriptor.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=(
                Descriptor.MaterializationInputManifest
            ),
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
        BuildPortfolioTemplate(TemplateId, (Index,))
        for Index, TemplateId in enumerate(("compact", "incumbent"))
    )
    Materialized: list[str] = []

    def Materialize(Descriptor):
        Materialized.append(Descriptor.TemplateId)
        return RawTrackAssignmentMaterialization(
            TemplateId=Descriptor.TemplateId,
            MaterializationInputFingerprint=(
                Descriptor.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=(
                Descriptor.MaterializationInputManifest
            ),
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
    assert Result.SkippedDominatedTemplateCount == 0


def test_post_materialization_deadline_observation_precedes_native_and_keeps_a_bounded_semantic_record():
    """A returned raw result is observed before any later expensive work."""
    Descriptor = BuildPortfolioTemplate("compact", (1,))
    Observations: list[dict[str, object]] = []
    Materialized: list[str] = []

    def Materialize(Value):
        Materialized.append(Value.TemplateId)
        return RawTrackAssignmentMaterialization(
            TemplateId=Value.TemplateId,
            MaterializationInputFingerprint=(
                Value.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=(
                Value.MaterializationInputManifest
            ),
            Domain=BuildDomain(Value.TemplateId),
            Complete=True,
        )

    def Observe(Diagnostics):
        Observations.append(dict(Diagnostics))
        if Diagnostics["Phase"] == "raw-template-materialization-observed":
            raise RoutingStageError(RoutingFailure(
                Reason=RoutingFailureReason.RuntimeBudgetExceeded,
                Stage="PreRouteInterfaceSelection",
                Diagnostics=Diagnostics,
            ))

    with pytest.raises(RoutingStageError) as Error:
        SolveRawTrackAssignmentPortfolio(
            RawTrackAssignmentPortfolio(
                Templates=(Descriptor,),
                MaximumAssignmentExpansions=16,
            ),
            Materialize,
            lambda _Domain, _Remaining: (_ for _ in ()).throw(
                AssertionError("deadline observation must precede native work")
            ),
            WorkCheck=Observe,
        )

    assert Materialized == ["compact"]
    Published = Error.value.Failure.Diagnostics
    assert Published["TemplateId"] == "compact"
    assert Published["SemanticResult"]["Complete"] is True
    assert Published["SourceIdentity"]["CandidateInputFingerprint"] == (
        Descriptor.MaterializationInputFingerprint
    )
    assert Published["WorkIdentity"]["WorkControlsFingerprint"]
    assert Published["Counts"]["PortfolioTemplateCount"] == 1
    assert Published["EvidenceCompleteness"]["FullInputManifestIncluded"] is False
    assert "MaterializationInputManifest" not in Published
    assert "Payload" not in repr(Published)


def test_post_materialization_deadline_observation_never_retraverses_frozen_input(
    monkeypatch,
):
    """The immediate deadline seam uses identity captured before materialization."""
    Nested: object = {"Leaf": "value"}
    for Index in range(24):
        Nested = {f"Level{Index}": [Nested, {"Repeat": Index}]}
    Manifest = RawTrackAssignmentCandidateInputManifest.Capture({
        "CandidateId": "compact",
        "LargeFrozenInput": Nested,
    })
    Descriptor = RawTrackAssignmentPortfolioTemplate(
        TemplateId="compact",
        Objective=(1,),
        MaterializationInputFingerprint=Manifest.ManifestFingerprint,
        MaterializationInputManifest=Manifest,
    )
    Portfolio = RawTrackAssignmentPortfolio(
        Templates=(Descriptor,),
        MaximumAssignmentExpansions=16,
    )
    OriginalToDictionary = RawTrackAssignmentCandidateInputManifest.ToDictionary
    OriginalFingerprint = RawTrackAssignmentCandidateInputManifest.ManifestFingerprint
    State = {"Armed": False, "Observed": False}

    def TrapToDictionary(Value):
        if State["Armed"]:
            raise AssertionError(
                "post-materialization observation traversed frozen input"
            )
        return OriginalToDictionary(Value)

    def TrapFingerprint(Value):
        if State["Armed"]:
            raise AssertionError(
                "post-materialization observation recomputed manifest identity"
            )
        return OriginalFingerprint.fget(Value)

    monkeypatch.setattr(
        RawTrackAssignmentCandidateInputManifest,
        "ToDictionary",
        TrapToDictionary,
    )
    monkeypatch.setattr(
        RawTrackAssignmentCandidateInputManifest,
        "ManifestFingerprint",
        property(TrapFingerprint),
    )

    def Materialize(Value):
        Result = RawTrackAssignmentMaterialization(
            TemplateId=Value.TemplateId,
            MaterializationInputFingerprint=(
                Value.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=Value.MaterializationInputManifest,
            Domain=BuildDomain(Value.TemplateId),
            Complete=True,
        )
        State["Armed"] = True
        return Result

    def Observe(Diagnostics):
        if Diagnostics["Phase"] == "raw-template-materialization-observed":
            assert State["Armed"] is True
            State["Observed"] = True
            State["Armed"] = False

    Result = SolveRawTrackAssignmentPortfolio(
        Portfolio,
        Materialize,
        lambda _Domain, _Remaining: NativeResult(
            Success=True,
            ExpansionCount=1,
            CandidateId="compact-candidate",
        ),
        WorkCheck=Observe,
    )

    assert State == {"Armed": False, "Observed": True}
    assert Result.Success is True


def test_already_expired_authority_stops_before_raw_materialization():
    """An expired shared authority cannot enter raw construction or native work."""
    Descriptor = BuildPortfolioTemplate("compact", (1,))
    Deadline = RoutingDeadline(StartedAt=0.0, ExpiresAt=0.0)
    Materialized: list[str] = []

    def Materialize(Value):
        Materialized.append(Value.TemplateId)
        raise AssertionError("expired authority entered raw materialization")

    with pytest.raises(RoutingStageError) as Error:
        SolveRawTrackAssignmentPortfolio(
            RawTrackAssignmentPortfolio(
                Templates=(Descriptor,),
                MaximumAssignmentExpansions=16,
            ),
            Materialize,
            lambda _Domain, _Remaining: (_ for _ in ()).throw(
                AssertionError("expired authority entered native assignment")
            ),
            WorkCheck=lambda Diagnostics: Deadline.RaiseIfExpired(
                "PreRouteInterfaceSelection",
                Diagnostics,
            ),
        )

    assert Materialized == []
    assert Error.value.Failure.Reason is (
        RoutingFailureReason.RuntimeBudgetExceeded
    )
    assert Error.value.Failure.Diagnostics["Phase"] == (
        "raw-template-domain-materialization"
    )


def test_incomplete_portfolio_failure_envelope_is_bounded_and_retains_semantic_identity():
    """Failure publication keeps identities and counts, never the frozen input tree."""
    Nested: object = {"Leaf": "value"}
    for Index in range(32):
        Nested = {f"Level{Index}": [Nested, {"Repeat": Index}]}
    Manifest = RawTrackAssignmentCandidateInputManifest.Capture({
        "CandidateId": "incomplete",
        "LargeFrozenInput": Nested,
    })
    Descriptor = RawTrackAssignmentPortfolioTemplate(
        TemplateId="incomplete",
        Objective=(1,),
        MaterializationInputFingerprint=Manifest.ManifestFingerprint,
        MaterializationInputManifest=Manifest,
    )
    Later = BuildPortfolioTemplate("later", (2,))

    Result = SolveRawTrackAssignmentPortfolio(
        RawTrackAssignmentPortfolio(
            Templates=(Later, Descriptor),
            MaximumAssignmentExpansions=16,
        ),
        lambda Value: RawTrackAssignmentMaterialization(
            TemplateId=Value.TemplateId,
            MaterializationInputFingerprint=(
                Value.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=Value.MaterializationInputManifest,
            Domain=None,
            Complete=False,
            IncompleteReason="fixed-domain-work-cap",
        ),
        lambda _Domain, _Remaining: (_ for _ in ()).throw(
            AssertionError("incomplete materialization must not reach native")
        ),
    )

    Envelope = Result.ToBoundedFailureEnvelope()
    assert Envelope["SemanticResult"]["IncompleteReason"] == (
        "incomplete-template-domain"
    )
    assert Envelope["SourceIdentities"] == [{
        "CandidateId": "incomplete",
        "CandidateInputFingerprint": Manifest.ManifestFingerprint,
    }]
    assert Envelope["WorkIdentity"]["WorkControlsFingerprint"]
    assert Envelope["Counts"] == {
        "PortfolioTemplateCount": 2,
        "MaterializedTemplateCount": 1,
        "AttemptCount": 1,
        "CandidatePreparationResultCount": 1,
        "SkippedDominatedTemplateCount": 0,
        "UnattemptedTemplateCount": 1,
    }
    assert Envelope["EvidenceCompleteness"] == {
        "SemanticSelectionComplete": False,
        "OuterPortfolioComplete": False,
        "FullInputManifestIncluded": False,
        "FullMaterializationDiagnosticsIncluded": False,
        "UnattemptedDescriptorsDominated": False,
    }
    assert Envelope["Omissions"] == [
        "full-frozen-candidate-input-manifests",
        "full-materialization-diagnostics",
        "raw-domain-values-and-claims",
    ]
    assert "Payload" not in repr(Envelope)
    assert "LargeFrozenInput" not in repr(Envelope)


def test_incomplete_portfolio_envelope_is_deterministic_under_input_reordering():
    """Descriptor input order cannot change incomplete evidence or dominance."""
    Descriptors = (
        BuildPortfolioTemplate("later", (2,)),
        BuildPortfolioTemplate("first", (1,)),
        BuildPortfolioTemplate("last", (3,)),
    )

    def Materialize(Value):
        return RawTrackAssignmentMaterialization(
            TemplateId=Value.TemplateId,
            MaterializationInputFingerprint=(
                Value.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=Value.MaterializationInputManifest,
            Domain=None,
            Complete=False,
            IncompleteReason="fixed-domain-work-cap",
        )

    Envelopes = []
    for Ordered in (Descriptors, tuple(reversed(Descriptors))):
        Result = SolveRawTrackAssignmentPortfolio(
            RawTrackAssignmentPortfolio(
                Templates=Ordered,
                MaximumAssignmentExpansions=16,
            ),
            Materialize,
            lambda _Domain, _Remaining: (_ for _ in ()).throw(
                AssertionError("incomplete materialization must not reach native")
            ),
        )
        assert Result.SkippedDominatedTemplateCount == 0
        Envelopes.append(Result.ToBoundedFailureEnvelope())

    assert Envelopes[0] == Envelopes[1]


def test_equal_objective_incomplete_member_prevents_early_commit():
    """A tied partial member cannot be hidden behind an earlier witness."""
    Descriptors = (
        BuildPortfolioTemplate("compact", (4, 8)),
        BuildPortfolioTemplate("compact-tie", (4, 8)),
        BuildPortfolioTemplate("worse", (5, 7)),
    )
    Materialized: list[str] = []
    NativeCalls: list[str] = []

    def Materialize(Descriptor):
        Materialized.append(Descriptor.TemplateId)
        if Descriptor.TemplateId == "compact-tie":
            return RawTrackAssignmentMaterialization(
                TemplateId=Descriptor.TemplateId,
                MaterializationInputFingerprint=(
                    Descriptor.MaterializationInputFingerprint
                ),
                MaterializationInputManifest=(
                    Descriptor.MaterializationInputManifest
                ),
                Domain=None,
                Complete=False,
                IncompleteReason="fixed-domain-work-cap",
            )
        return RawTrackAssignmentMaterialization(
            TemplateId=Descriptor.TemplateId,
            MaterializationInputFingerprint=(
                Descriptor.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=(
                Descriptor.MaterializationInputManifest
            ),
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
    assert Result.SkippedDominatedTemplateCount == 0


def test_equal_prefix_uses_resolved_material_access_objective():
    """Geometry/layer ties resolve only after every fixed factor is built."""
    Descriptors = (
        BuildPortfolioTemplate("more-access-material", (4, 8, 2)),
        BuildPortfolioTemplate("less-access-material", (4, 8, 2)),
        BuildPortfolioTemplate("worse-footprint", (5, 7, 1)),
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
            MaterializationInputFingerprint=(
                Descriptor.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=(
                Descriptor.MaterializationInputManifest
            ),
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
    Descriptor = BuildPortfolioTemplate("compact", (4, 8, 2))

    with pytest.raises(ValueError, match="retain its declared selection prefix"):
        SolveRawTrackAssignmentPortfolio(
            RawTrackAssignmentPortfolio(
                Templates=(Descriptor,),
                MaximumAssignmentExpansions=16,
            ),
            lambda Value: RawTrackAssignmentMaterialization(
                TemplateId=Value.TemplateId,
                MaterializationInputFingerprint=(
                    Value.MaterializationInputFingerprint
                ),
                MaterializationInputManifest=(
                    Value.MaterializationInputManifest
                ),
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

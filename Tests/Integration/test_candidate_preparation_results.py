"""Independent candidate/result-table oracle for pre-route preparation."""

from dataclasses import dataclass, replace
from itertools import permutations
from types import MappingProxyType, SimpleNamespace

import pytest

from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from PhysicalDesign.Contracts.Failures import (
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Contracts.Placement import TrackAssignmentPreparation
from PhysicalDesign.Orchestration.AccessEnvelope import (
    BuildPlacementAccessEvaluationControls,
)
from PhysicalDesign.Orchestration.Runner import PlaceAndRoutePcb
from PhysicalDesign.Policy import (
    PlacementAccessPolicy,
    RoutingAwarePlacementAccessPhysicalDesignPolicy,
)
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims
from PhysicalDesign.Routing.Assignment.TemplateAssignment import (
    BuildRawTrackAssignmentWorkControlsFingerprint,
    RawTrackAssignmentCandidateInputManifest,
    RawTrackAssignmentCandidatePreparationOutcome,
    RawTrackAssignmentMaterialization,
    RawTrackAssignmentPortfolio,
    RawTrackAssignmentPortfolioTemplate,
    SolveRawTrackAssignmentPortfolio,
    SolveRawTrackAssignmentPortfolioWithContext,
)
from PhysicalDesign.Routing.Global.Orchestration.RunModels import (
    RawTrackAssignmentDomain,
    RawTrackAssignmentValue,
)
from PhysicalDesign.Runtime.Reliability import RoutingDeadline
import PhysicalDesign.Orchestration.RoutingAttempts as RoutingAttempts
import PhysicalDesign.Orchestration.Setup as PlacementSetup
import PhysicalDesign.Orchestration.AccessEnvelope as AccessEnvelope
import PhysicalDesign.Orchestration.Results as PlacementResults


@dataclass(frozen=True)
class CandidateCase:
    CandidateId: str
    Objective: tuple[int, ...]
    Outcome: RawTrackAssignmentCandidatePreparationOutcome


def BuildCandidateInputManifest(
    CandidateId: str,
) -> RawTrackAssignmentCandidateInputManifest:
    return RawTrackAssignmentCandidateInputManifest.Capture({
        "CandidateId": CandidateId,
        "Fixture": "candidate-preparation-table",
    })


def TaggedMapEntries(Value: dict[str, object]) -> dict[str, object]:
    assert Value["Kind"] == "Map"
    return {
        Entry["Key"]: Entry["Value"]
        for Entry in Value["Entries"]
    }


def BuildSingleNandNetlist() -> NetlistIR:
    Module = ModuleIR(
        Name="CandidatePreparationSingleNand",
        Inputs=["A", "B"],
        Outputs=["Y"],
        Gates=[
            Gate("InputA", GateKind.INPUT, ["A"]),
            Gate("InputB", GateKind.INPUT, ["B"]),
            Gate("Nand0", GateKind.NAND, ["Y"], ["A", "B"]),
            Gate("OutputY", GateKind.OUTPUT, [], ["Y"]),
        ],
    )
    return NetlistIR(Top=Module.Name, Modules={Module.Name: Module})


@pytest.mark.parametrize(
    ("Families", "ExpectedFamilies"),
    (
        (("straight", "planar-jog"), ("planar-jog", "straight")),
        (("planar-jog", "straight"), ("planar-jog", "straight")),
        (("straight",), ("straight",)),
    ),
)
def test_current_validation_controls_canonicalize_enabled_pattern_families(
    Families,
    ExpectedFamilies,
):
    """The evaluated policy identity is order-independent but not broadened."""
    Controls = BuildPlacementAccessEvaluationControls(
        PlacementAccessPolicy(
            Enabled=True,
            EnabledPatternFamilies=Families,
        )
    )

    assert Controls.EnabledPatternFamilies == ExpectedFamilies


@pytest.mark.parametrize(
    "Families",
    (
        ("unsupported",),
        (42,),
        ("straight", 42),
    ),
)
def test_current_validation_controls_leave_malformed_families_to_producer(
    Families,
):
    """Joint neither coerces malformed policy values nor grants an extra family."""
    PlacementAccess = PlacementAccessPolicy(
        Enabled=True,
        EnabledPatternFamilies=Families,
    )

    with pytest.raises((TypeError, ValueError)):
        BuildPlacementAccessEvaluationControls(PlacementAccess)


@pytest.mark.parametrize(
    "FamiliesFactory",
    (
        pytest.param(lambda: ["straight"], id="list"),
        pytest.param(lambda: "straight", id="string"),
        pytest.param(lambda: {"straight"}, id="set"),
        pytest.param(
            lambda: (Family for Family in ("straight",)),
            id="generator",
        ),
    ),
)
def test_current_validation_controls_reject_malformed_outer_containers(
    FamiliesFactory,
):
    """Only an exact tuple can carry enabled-family policy authority."""
    PlacementAccess = SimpleNamespace(
        EnabledPatternFamilies=FamiliesFactory(),
        CatalogVersion="physical-pin-access-catalog-v1",
        MaximumDomainGenerationWork=100_000,
        MaximumAssignmentExpansions=100_000,
    )

    with pytest.raises((TypeError, ValueError)):
        BuildPlacementAccessEvaluationControls(PlacementAccess)


@pytest.mark.parametrize(
    "FamiliesFactory",
    (
        pytest.param(lambda: ["straight"], id="list"),
        pytest.param(lambda: "straight", id="string"),
        pytest.param(lambda: {"straight"}, id="set"),
        pytest.param(
            lambda: (Family for Family in ("straight",)),
            id="generator",
        ),
    ),
)
def test_current_validation_controls_forward_malformed_outer_container(
    monkeypatch,
    FamiliesFactory,
):
    """Joint forwards malformed outer shapes without consuming or replacing them."""
    Families = FamiliesFactory()
    PlacementAccess = SimpleNamespace(
        EnabledPatternFamilies=Families,
        CatalogVersion="physical-pin-access-catalog-v1",
        MaximumDomainGenerationWork=100_000,
        MaximumAssignmentExpansions=100_000,
    )
    Observed = []

    def ObserveControls(**Values):
        Observed.append(Values)
        return SimpleNamespace(**Values)

    monkeypatch.setattr(
        AccessEnvelope,
        "PlacementAccessEvaluationControls",
        ObserveControls,
    )

    Controls = BuildPlacementAccessEvaluationControls(PlacementAccess)

    assert Controls.EnabledPatternFamilies is Families
    assert Observed[0]["EnabledPatternFamilies"] is Families


def test_current_validation_controls_do_not_consume_generator_before_forwarding(
    monkeypatch,
):
    """The producer receives a one-shot family container at its first value."""
    ReadCount = 0

    def GenerateFamilies():
        nonlocal ReadCount
        for Family in ("straight", "planar-jog"):
            ReadCount += 1
            yield Family

    Families = GenerateFamilies()
    PlacementAccess = SimpleNamespace(
        EnabledPatternFamilies=Families,
        CatalogVersion="physical-pin-access-catalog-v1",
        MaximumDomainGenerationWork=100_000,
        MaximumAssignmentExpansions=100_000,
    )
    Observed = []

    def ObserveControls(**Values):
        Observed.append(Values)
        return SimpleNamespace(**Values)

    monkeypatch.setattr(
        AccessEnvelope,
        "PlacementAccessEvaluationControls",
        ObserveControls,
    )

    Controls = BuildPlacementAccessEvaluationControls(PlacementAccess)

    assert Controls.EnabledPatternFamilies is Families
    assert Observed[0]["EnabledPatternFamilies"] is Families
    assert ReadCount == 0
    assert next(Families) == "straight"
    assert ReadCount == 1


def test_public_current_access_callers_use_the_same_normalized_families(
    monkeypatch,
):
    """The pre-route and final-publication gates observe one policy identity."""
    ExpectedFamilies = ("planar-jog", "straight")
    Policy = replace(
        RoutingAwarePlacementAccessPhysicalDesignPolicy,
        PlacementAccess=replace(
            RoutingAwarePlacementAccessPhysicalDesignPolicy.PlacementAccess,
            EnabledPatternFamilies=("straight", "planar-jog"),
        ),
    )
    Observed = []
    OriginalEnvelopeValidation = (
        AccessEnvelope.ValidateCurrentSelectedPlacementAccess
    )
    OriginalFinalizationValidation = (
        PlacementResults.ValidateCurrentSelectedPlacementAccess
    )

    def CaptureEnvelopeValidation(*Args, **Kwargs):
        Observed.append(("envelope", Kwargs["CurrentControls"]))
        return OriginalEnvelopeValidation(*Args, **Kwargs)

    def CaptureFinalizationValidation(*Args, **Kwargs):
        Observed.append(("finalization", Kwargs["CurrentControls"]))
        return OriginalFinalizationValidation(*Args, **Kwargs)

    monkeypatch.setattr(
        AccessEnvelope,
        "ValidateCurrentSelectedPlacementAccess",
        CaptureEnvelopeValidation,
    )
    monkeypatch.setattr(
        PlacementResults,
        "ValidateCurrentSelectedPlacementAccess",
        CaptureFinalizationValidation,
    )

    PlaceAndRoutePcb(
        BuildSingleNandNetlist(),
        Policy=Policy,
    )

    assert {
        (Caller, Controls.EnabledPatternFamilies)
        for Caller, Controls in Observed
    } == {
        ("envelope", ExpectedFamilies),
        ("finalization", ExpectedFamilies),
    }


def EnumerateExpectedDecision(
    Cases: tuple[CandidateCase, ...],
    *,
    OuterPortfolioComplete: bool,
) -> tuple[str, str]:
    """Direct finite-table oracle independent of the production selector."""
    Incomplete = tuple(
        Case
        for Case in Cases
        if Case.Outcome
        is RawTrackAssignmentCandidatePreparationOutcome.Incomplete
    )
    if Incomplete:
        return ("Incomplete", "")
    Feasible = tuple(
        Case
        for Case in Cases
        if Case.Outcome
        is RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible
    )
    if Feasible:
        Selected = min(
            Feasible,
            key=lambda Case: (Case.Objective, Case.CandidateId),
        )
        return ("Feasible", Selected.CandidateId)
    return (
        "ExhaustiveFailed"
        if OuterPortfolioComplete
        else "NonExhaustiveFailed",
        "",
    )


def BuildDomain(Case: CandidateCase, MaximumExpansions: int):
    Position = (len(Case.CandidateId), 1, 0)
    Value = RawTrackAssignmentValue(
        Signal="Signal",
        CandidateId=f"{Case.CandidateId}-witness",
        Claims=RoutingResourceClaims(WireCells=frozenset({Position})),
        MaterialCost=1,
        FootprintGrowth=1,
        Length=1,
        BendCount=0,
        ViaCount=0,
    )
    return RawTrackAssignmentDomain(
        ResourcePositions=(Position,),
        Values=(Value,),
        BaseClaims=(),
        CandidateCounts=(("Signal", 1),),
        CandidateDomainFingerprint=f"domain-{Case.CandidateId}",
        LocalClaimDomainFingerprint=f"local-{Case.CandidateId}",
        PlacementFingerprint=f"placement-{Case.CandidateId}",
        ResourceGraphFingerprint=f"resources-{Case.CandidateId}",
        PortalDomainFingerprint=f"portals-{Case.CandidateId}",
        Complete=True,
        MaximumAssignmentExpansions=MaximumExpansions,
    )


def BuildRequiredSignalDomain() -> RawTrackAssignmentDomain:
    Values = (
        RawTrackAssignmentValue(
            Signal="A",
            CandidateId="a-ordinary",
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(0, 1, 0)})
            ),
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
        ),
        RawTrackAssignmentValue(
            Signal="A",
            CandidateId="a-local",
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(1, 1, 0)})
            ),
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
            ValueKind="local-claim",
        ),
        RawTrackAssignmentValue(
            Signal="B",
            CandidateId="b-ordinary",
            Claims=RoutingResourceClaims(
                WireCells=frozenset({(2, 1, 0)})
            ),
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
        ),
    )
    return RawTrackAssignmentDomain(
        ResourcePositions=((0, 1, 0), (1, 1, 0), (2, 1, 0)),
        Values=Values,
        BaseClaims=(),
        CandidateCounts=(("A", 2), ("B", 1)),
        CandidateDomainFingerprint="required-signal-domain",
        LocalClaimDomainFingerprint="required-signal-local-domain",
        PlacementFingerprint="required-signal-placement",
        ResourceGraphFingerprint="required-signal-resources",
        PortalDomainFingerprint="required-signal-portals",
        Complete=True,
        MaximumAssignmentExpansions=16,
    )


def RunRequiredSignalSelection(
    SelectedCandidateIds,
    *,
    Success: bool,
):
    Manifest = BuildCandidateInputManifest("required-signals")
    Descriptor = RawTrackAssignmentPortfolioTemplate(
        TemplateId="required-signals",
        Objective=(1,),
        MaterializationInputFingerprint=Manifest.ManifestFingerprint,
        MaterializationInputManifest=Manifest,
    )
    Domain = BuildRequiredSignalDomain()
    return SolveRawTrackAssignmentPortfolio(
        RawTrackAssignmentPortfolio(
            Templates=(Descriptor,),
            MaximumAssignmentExpansions=16,
        ),
        lambda Value: RawTrackAssignmentMaterialization(
            TemplateId=Value.TemplateId,
            MaterializationInputFingerprint=(
                Value.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=Value.MaterializationInputManifest,
            Domain=Domain,
            Complete=True,
        ),
        lambda _Domain, _Remaining: SimpleNamespace(
            Success=Success,
            SelectedCandidateIds=SelectedCandidateIds,
            ExpansionCount=1,
            BudgetExhausted=False,
            DeadlineExceeded=False,
            ConflictSignals=(),
            ConflictResourceIndices=(),
            FailureNet=None,
        ),
    )
def RunCandidateTable(
    Cases: tuple[CandidateCase, ...],
    *,
    OuterPortfolioComplete: bool,
):
    MaximumExpansions = 32
    WorkControls = BuildRawTrackAssignmentWorkControlsFingerprint(
        MaximumExpansions
    )
    ById = {Case.CandidateId: Case for Case in Cases}
    Descriptors = tuple(
        RawTrackAssignmentPortfolioTemplate(
            TemplateId=Case.CandidateId,
            Objective=Case.Objective,
            MaterializationInputFingerprint=(
                Manifest.ManifestFingerprint
            ),
            MaterializationInputManifest=Manifest,
        )
        for Case in Cases
        for Manifest in (BuildCandidateInputManifest(Case.CandidateId),)
    )

    def Materialize(Descriptor):
        Case = ById[Descriptor.TemplateId]
        if (
            Case.Outcome
            is RawTrackAssignmentCandidatePreparationOutcome.Incomplete
        ):
            return RawTrackAssignmentMaterialization(
                TemplateId=Case.CandidateId,
                MaterializationInputFingerprint=(
                    Descriptor.MaterializationInputFingerprint
                ),
                MaterializationInputManifest=(
                    Descriptor.MaterializationInputManifest
                ),
                Domain=None,
                Complete=False,
                IncompleteReason="bounded-candidate-preparation",
            )
        return RawTrackAssignmentMaterialization(
            TemplateId=Case.CandidateId,
            MaterializationInputFingerprint=(
                Descriptor.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=(
                Descriptor.MaterializationInputManifest
            ),
            Domain=BuildDomain(Case, MaximumExpansions),
            Complete=True,
        )

    def Solve(Domain, _Remaining):
        CandidateId = Domain.PlacementFingerprint.removeprefix("placement-")
        Case = ById[CandidateId]
        Feasible = (
            Case.Outcome
            is RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible
        )
        return SimpleNamespace(
            Success=Feasible,
            SelectedCandidateIds=(
                (("Signal", f"{Case.CandidateId}-witness"),)
                if Feasible
                else ()
            ),
            ExpansionCount=1,
            BudgetExhausted=False,
            DeadlineExceeded=False,
            ConflictSignals=() if Feasible else ("Signal",),
            ConflictResourceIndices=() if Feasible else (0,),
            FailureNet="" if Feasible else "Signal",
        )

    Portfolio = RawTrackAssignmentPortfolio(
        Templates=Descriptors,
        MaximumAssignmentExpansions=MaximumExpansions,
        WorkControlsFingerprint=WorkControls,
        NonExhaustiveTemplateDomain=not OuterPortfolioComplete,
    )
    return Portfolio, SolveRawTrackAssignmentPortfolio(
        Portfolio,
        Materialize,
        Solve,
    )


@pytest.mark.parametrize(
    ("Cases", "OuterPortfolioComplete"),
    (
        (
            (
                CandidateCase(
                    "failed",
                    (1,),
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
                ),
                CandidateCase(
                    "feasible",
                    (2,),
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
                ),
            ),
            False,
        ),
        (
            (
                CandidateCase(
                    "failed-a",
                    (1,),
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
                ),
                CandidateCase(
                    "failed-b",
                    (2,),
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
                ),
                CandidateCase(
                    "failed-c",
                    (3,),
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
                ),
            ),
            False,
        ),
        (
            (
                CandidateCase(
                    "failed-a",
                    (1,),
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
                ),
                CandidateCase(
                    "failed-b",
                    (2,),
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
                ),
            ),
            True,
        ),
        (
            (
                CandidateCase(
                    "failed",
                    (1,),
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
                ),
                CandidateCase(
                    "incomplete",
                    (2,),
                    RawTrackAssignmentCandidatePreparationOutcome.Incomplete,
                ),
            ),
            False,
        ),
    ),
)
def test_candidate_preparation_results_match_direct_finite_table(
    Cases,
    OuterPortfolioComplete,
):
    Portfolio, Result = RunCandidateTable(
        Cases,
        OuterPortfolioComplete=OuterPortfolioComplete,
    )
    ExpectedDecision, ExpectedCandidateId = EnumerateExpectedDecision(
        Cases,
        OuterPortfolioComplete=OuterPortfolioComplete,
    )

    assert tuple(
        (Value.CandidateId, Value.Outcome)
        for Value in Result.CandidatePreparationResults
    ) == tuple(
        (Case.CandidateId, Case.Outcome)
        for Case in sorted(Cases, key=lambda Case: (Case.Objective, Case.CandidateId))
    )
    assert all(
        Value.CandidateInputManifest
        == BuildCandidateInputManifest(Value.CandidateId)
        and Value.CandidateInputFingerprint
        == Value.CandidateInputManifest.ManifestFingerprint
        and Value.WorkControlsFingerprint == Portfolio.WorkControlsFingerprint
        and Value.PortfolioFingerprint == Portfolio.ProblemFingerprint
        for Value in Result.CandidatePreparationResults
    )
    assert Result.OuterPortfolioComplete is OuterPortfolioComplete

    if ExpectedDecision == "Feasible":
        assert Result.Success is True
        assert Result.SelectedTemplateId == ExpectedCandidateId
    elif ExpectedDecision == "Incomplete":
        assert Result.Success is False
        assert Result.Complete is False
        assert Result.Unsatisfiable is False
    elif ExpectedDecision == "ExhaustiveFailed":
        assert Result.Success is False
        assert Result.Complete is True
        assert Result.Unsatisfiable is True
    else:
        assert ExpectedDecision == "NonExhaustiveFailed"
        assert Result.Success is False
        assert Result.Complete is True
        assert Result.Unsatisfiable is False


def test_candidate_order_permutations_preserve_results_and_selection():
    Cases = (
        CandidateCase(
            "failed-a",
            (1,),
            RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
        ),
        CandidateCase(
            "failed-b",
            (2,),
            RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
        ),
        CandidateCase(
            "feasible",
            (3,),
            RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
        ),
    )
    Results = tuple(
        RunCandidateTable(
            tuple(Permutation),
            OuterPortfolioComplete=False,
        )[1].ToDictionary()
        for Permutation in permutations(Cases)
    )

    assert all(Value == Results[0] for Value in Results[1:])


def test_selected_preparation_rejects_candidate_or_control_substitution():
    Cases = (
        CandidateCase(
            "failed",
            (1,),
            RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
        ),
        CandidateCase(
            "feasible",
            (2,),
            RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
        ),
    )
    Portfolio, Result = RunCandidateTable(
        Cases,
        OuterPortfolioComplete=False,
    )

    Selected = Result.RequireSelectedCandidatePreparation(
        CandidateId="feasible",
        CandidateInputFingerprint=(
            BuildCandidateInputManifest("feasible").ManifestFingerprint
        ),
        CandidateInputManifest=BuildCandidateInputManifest("feasible"),
        WorkControlsFingerprint=Portfolio.WorkControlsFingerprint,
    )
    assert Selected.Preparation is Result.Preparation
    assert Selected.ResultFingerprint

    with pytest.raises(ValueError, match="input identity mismatches"):
        Result.RequireSelectedCandidatePreparation(
            CandidateId="feasible",
            CandidateInputFingerprint=(
                BuildCandidateInputManifest("failed").ManifestFingerprint
            ),
            CandidateInputManifest=BuildCandidateInputManifest("feasible"),
            WorkControlsFingerprint=Portfolio.WorkControlsFingerprint,
        )
    with pytest.raises(ValueError, match="work controls mismatch"):
        Result.RequireSelectedCandidatePreparation(
            CandidateId="feasible",
            CandidateInputFingerprint=(
                BuildCandidateInputManifest("feasible").ManifestFingerprint
            ),
            CandidateInputManifest=BuildCandidateInputManifest("feasible"),
            WorkControlsFingerprint="previous-candidate-controls",
        )
    with pytest.raises(ValueError, match="not the selected candidate"):
        Result.RequireSelectedCandidatePreparation(
            CandidateId="failed",
            CandidateInputFingerprint=(
                BuildCandidateInputManifest("failed").ManifestFingerprint
            ),
            CandidateInputManifest=BuildCandidateInputManifest("failed"),
            WorkControlsFingerprint=Portfolio.WorkControlsFingerprint,
        )


def test_materializer_cannot_return_previous_candidate_input_identity():
    CurrentManifest = BuildCandidateInputManifest("current")
    PreviousManifest = BuildCandidateInputManifest("previous")
    Descriptor = RawTrackAssignmentPortfolioTemplate(
        TemplateId="current",
        Objective=(1,),
        MaterializationInputFingerprint=CurrentManifest.ManifestFingerprint,
        MaterializationInputManifest=CurrentManifest,
    )
    Portfolio = RawTrackAssignmentPortfolio(
        Templates=(Descriptor,),
        MaximumAssignmentExpansions=4,
    )

    with pytest.raises(ValueError, match="mismatched input fingerprint"):
        SolveRawTrackAssignmentPortfolio(
            Portfolio,
            lambda _Descriptor: RawTrackAssignmentMaterialization(
                TemplateId="current",
                MaterializationInputFingerprint=(
                    PreviousManifest.ManifestFingerprint
                ),
                MaterializationInputManifest=PreviousManifest,
                Domain=BuildDomain(
                    CandidateCase(
                        "current",
                        (1,),
                        RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed,
                    ),
                    4,
                ),
                Complete=True,
            ),
            lambda _Domain, _Remaining: SimpleNamespace(
                Success=False,
                ExpansionCount=1,
                BudgetExhausted=False,
                DeadlineExceeded=False,
                ConflictSignals=(),
                ConflictResourceIndices=(),
                FailureNet="",
            ),
        )


def test_context_boundary_binds_exact_deadline_controls_and_result():
    MaximumExpansions = 8
    Deadline = RoutingDeadline.Start(30.0)
    WorkControls = BuildRawTrackAssignmentWorkControlsFingerprint(
        MaximumExpansions,
        Deadline,
    )
    Manifest = BuildCandidateInputManifest("feasible")
    Descriptor = RawTrackAssignmentPortfolioTemplate(
        TemplateId="feasible",
        Objective=(1,),
        MaterializationInputFingerprint=Manifest.ManifestFingerprint,
        MaterializationInputManifest=Manifest,
    )
    Portfolio = RawTrackAssignmentPortfolio(
        Templates=(Descriptor,),
        MaximumAssignmentExpansions=MaximumExpansions,
        WorkControlsFingerprint=WorkControls,
    )

    class NativeContext:
        def PlanAuthoritativeRoutesBounded(
            self,
            Values,
            _ResourceCount,
            _MaximumExpansions,
            _RemainingMilliseconds,
        ):
            return SimpleNamespace(
                Success=True,
                SelectedCandidateIds=((Values[0][0], Values[0][1]),),
                ExpansionCount=1,
                BudgetExhausted=False,
                DeadlineExceeded=False,
                ConflictSignals=(),
                ConflictResourceIndices=(),
                FailureNet="",
            )

    Case = CandidateCase(
        "feasible",
        (1,),
        RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
    )
    Result = SolveRawTrackAssignmentPortfolioWithContext(
        Portfolio,
        lambda Value: RawTrackAssignmentMaterialization(
            TemplateId=Value.TemplateId,
            MaterializationInputFingerprint=(
                Value.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=(
                Value.MaterializationInputManifest
            ),
            Domain=BuildDomain(Case, MaximumExpansions),
            Complete=True,
        ),
        Context=NativeContext(),
        Deadline=Deadline,
    )

    Selected = Result.RequireSelectedCandidatePreparation(
        CandidateId="feasible",
        CandidateInputFingerprint=Manifest.ManifestFingerprint,
        CandidateInputManifest=Manifest,
        WorkControlsFingerprint=WorkControls,
    )
    assert Selected.Preparation is Result.Preparation

    with pytest.raises(ValueError, match="caller-owned deadline"):
        SolveRawTrackAssignmentPortfolioWithContext(
            RawTrackAssignmentPortfolio(
                Templates=(Descriptor,),
                MaximumAssignmentExpansions=MaximumExpansions,
            ),
            lambda _Value: (_ for _ in ()).throw(
                AssertionError("mismatched controls must fail before preparation")
            ),
            Context=NativeContext(),
            Deadline=Deadline,
        )


def test_complete_feasible_requires_directly_enumerated_signal_coverage():
    Domain = BuildRequiredSignalDomain()
    RequiredSignals = {
        Signal for Signal, _Count in Domain.CandidateCounts
    }
    AllowedPairs = {
        (Value.Signal, Value.CandidateId) for Value in Domain.Values
    }
    Selected = (("A", "a-local"), ("B", "b-ordinary"))
    assert {Signal for Signal, _CandidateId in Selected} == RequiredSignals
    assert set(Selected) <= AllowedPairs

    Result = RunRequiredSignalSelection(Selected, Success=True)

    assert Result.Success is True
    CandidateResult = Result.CandidatePreparationResults[0]
    assert CandidateResult.Outcome is (
        RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible
    )
    assert CandidateResult.Preparation.SelectedCandidateIds == (
        ("B", "b-ordinary"),
    )
    assert CandidateResult.Preparation.SelectedLocalClaimChoiceIds == (
        ("A", "a-local"),
    )


@pytest.mark.parametrize(
    ("Selected", "Success"),
    (
        ((), True),
        ((("A", "a-ordinary"),), True),
        (
            (
                ("A", "a-ordinary"),
                ("A", "a-ordinary"),
                ("B", "b-ordinary"),
            ),
            True,
        ),
        (
            (
                ("A", "a-ordinary"),
                ("A", "a-local"),
                ("B", "b-ordinary"),
            ),
            True,
        ),
        (
            (("A", "a-ordinary"), ("C", "c-unknown")),
            True,
        ),
        (
            (("A", "a-unknown"), ("B", "b-ordinary")),
            True,
        ),
        (((1, "a-ordinary"), ("B", "b-ordinary")), True),
        ((("A", 1), ("B", "b-ordinary")), True),
    ),
)
def test_selected_witness_rejects_missing_duplicate_unknown_or_partial_values(
    Selected,
    Success,
):
    with pytest.raises((TypeError, ValueError)):
        RunRequiredSignalSelection(Selected, Success=Success)


def test_failed_native_partial_selection_is_diagnostic_only():
    Partial = (("A", "a-ordinary"),)

    Result = RunRequiredSignalSelection(Partial, Success=False)

    assert Result.Success is False
    assert Result.Complete is True
    CandidateResult = Result.CandidatePreparationResults[0]
    assert CandidateResult.Outcome is (
        RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed
    )
    assert CandidateResult.Preparation is None
    assert CandidateResult.DiagnosticSelectedCandidateIds == Partial
    assert Result.Attempts[0].DiagnosticSelectedCandidateIds == Partial


@pytest.mark.parametrize(
    ("Field", "Malformed"),
    (
        ("Success", 1),
        ("Success", "yes"),
        ("DeadlineExceeded", 0),
        ("DeadlineExceeded", "false"),
        ("BudgetExhausted", 0),
        ("BudgetExhausted", "false"),
        ("ExpansionCount", True),
        ("ExpansionCount", 1.5),
        ("ConflictResourceIndices", (True,)),
        ("ConflictResourceIndices", (1.5,)),
    ),
)
def test_native_outcome_rejects_coercible_values_before_result_construction(
    Field,
    Malformed,
):
    Case = CandidateCase(
        "candidate",
        (1,),
        RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
    )
    Manifest = BuildCandidateInputManifest(Case.CandidateId)
    Descriptor = RawTrackAssignmentPortfolioTemplate(
        TemplateId=Case.CandidateId,
        Objective=Case.Objective,
        MaterializationInputFingerprint=Manifest.ManifestFingerprint,
        MaterializationInputManifest=Manifest,
    )
    Native = {
        "Success": True,
        "SelectedCandidateIds": (("Signal", "candidate-witness"),),
        "ExpansionCount": 1,
        "BudgetExhausted": False,
        "DeadlineExceeded": False,
        "ConflictSignals": (),
        "ConflictResourceIndices": (),
        "FailureNet": "",
    }
    Native[Field] = Malformed
    Materialized = []

    def Materialize(Value):
        Materialized.append(Value.TemplateId)
        return RawTrackAssignmentMaterialization(
            TemplateId=Value.TemplateId,
            MaterializationInputFingerprint=(
                Value.MaterializationInputFingerprint
            ),
            MaterializationInputManifest=Value.MaterializationInputManifest,
            Domain=BuildDomain(Case, 8),
            Complete=True,
        )

    with pytest.raises(TypeError):
        SolveRawTrackAssignmentPortfolio(
            RawTrackAssignmentPortfolio(
                Templates=(Descriptor,),
                MaximumAssignmentExpansions=8,
            ),
            Materialize,
            lambda _Domain, _Remaining: SimpleNamespace(**Native),
        )

    assert Materialized == ["candidate"]


@pytest.mark.parametrize("Malformed", (True, 1.5))
def test_work_caps_require_exact_non_boolean_integers(Malformed):
    with pytest.raises(TypeError):
        BuildRawTrackAssignmentWorkControlsFingerprint(Malformed)

    Manifest = BuildCandidateInputManifest("candidate")
    Descriptor = RawTrackAssignmentPortfolioTemplate(
        TemplateId="candidate",
        Objective=(1,),
        MaterializationInputFingerprint=Manifest.ManifestFingerprint,
        MaterializationInputManifest=Manifest,
    )
    with pytest.raises(TypeError):
        RawTrackAssignmentPortfolio(
            Templates=(Descriptor,),
            MaximumAssignmentExpansions=Malformed,
        )


@pytest.mark.parametrize(
    ("Field", "Malformed"),
    (
        ("Success", 1),
        ("Complete", 1),
        ("Unsatisfiable", 0),
        ("OuterPortfolioComplete", 1),
        ("ExpansionCount", True),
        ("MaterializedTemplateCount", 1.5),
        ("SelectedObjective", (True,)),
        ("FirstConflictResourceIndices", (1.5,)),
    ),
)
def test_selection_axes_and_counts_reject_coercible_values(Field, Malformed):
    _Portfolio, Result = RunCandidateTable(
        (
            CandidateCase(
                "feasible",
                (1,),
                RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
            ),
        ),
        OuterPortfolioComplete=False,
    )

    with pytest.raises(TypeError):
        replace(Result, **{Field: Malformed})


@pytest.mark.parametrize(
    ("Field", "Malformed"),
    (
        ("AvailableAssignmentExpansions", True),
        ("AvailableAssignmentExpansions", 8.0),
        ("ExpansionCount", True),
        ("ExpansionCount", 1.0),
        ("CumulativeExpansionCount", True),
        ("CumulativeExpansionCount", 1.0),
        ("Objective", (True,)),
        ("ConflictResourceIndices", (True,)),
    ),
)
def test_candidate_result_work_accounting_requires_exact_types(
    Field,
    Malformed,
):
    _Portfolio, Selection = RunCandidateTable(
        (
            CandidateCase(
                "feasible",
                (1,),
                RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
            ),
        ),
        OuterPortfolioComplete=False,
    )

    with pytest.raises(TypeError):
        replace(
            Selection.CandidatePreparationResults[0],
            **{Field: Malformed},
        )


def test_materialization_complete_and_objective_require_exact_types():
    Manifest = BuildCandidateInputManifest("candidate")
    Case = CandidateCase(
        "candidate",
        (1,),
        RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
    )
    with pytest.raises(TypeError):
        RawTrackAssignmentMaterialization(
            TemplateId="candidate",
            MaterializationInputFingerprint=Manifest.ManifestFingerprint,
            MaterializationInputManifest=Manifest,
            Domain=BuildDomain(Case, 8),
            Complete=1,
        )
    with pytest.raises(TypeError):
        RawTrackAssignmentPortfolioTemplate(
            TemplateId="candidate",
            Objective=(1.5,),
            MaterializationInputFingerprint=Manifest.ManifestFingerprint,
            MaterializationInputManifest=Manifest,
        )


def test_result_deeply_owns_manifest_and_preparation_payloads():
    SourceList = ["first"]
    SourceDictionary = {"Values": SourceList}
    Manifest = RawTrackAssignmentCandidateInputManifest.Capture({
        "CandidateId": "feasible",
        "Nested": SourceDictionary,
    })
    _Portfolio, BaseSelection = RunCandidateTable(
        (
            CandidateCase(
                "feasible",
                (1,),
                RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
            ),
        ),
        OuterPortfolioComplete=False,
    )
    BaseResult = BaseSelection.CandidatePreparationResults[0]
    DiagnosticList = ["before"]
    DiagnosticDictionary = {"Values": DiagnosticList}
    MutablePreparation = replace(
        BaseResult.Preparation,
        Diagnostics=(("Nested", DiagnosticDictionary),),
    )
    FrozenResult = replace(
        BaseResult,
        CandidateInputFingerprint=Manifest.ManifestFingerprint,
        CandidateInputManifest=Manifest,
        Preparation=MutablePreparation,
    )
    BeforeManifest = FrozenResult.CandidateInputManifest.ToDictionary()
    BeforeResult = FrozenResult.ToDictionary()
    BeforeFingerprint = FrozenResult.ResultFingerprint

    SourceList.append("second")
    SourceDictionary["Added"] = ["later"]
    DiagnosticList.append("after")
    DiagnosticDictionary["Added"] = ["later"]

    assert FrozenResult.CandidateInputManifest.ToDictionary() == BeforeManifest
    assert FrozenResult.ToDictionary() == BeforeResult
    assert FrozenResult.ResultFingerprint == BeforeFingerprint
    assert type(FrozenResult.Preparation.Diagnostics[0][1]) is MappingProxyType

    with pytest.raises(TypeError):
        RawTrackAssignmentCandidateInputManifest(
            Payload=(("Mutable", ["value"]),),
        )
    with pytest.raises(TypeError):
        replace(FrozenResult, ConflictSignals=["Signal"])
    with pytest.raises(TypeError):
        replace(
            BaseResult,
            Preparation=replace(BaseResult.Preparation, Success=1),
        )


def test_tagged_manifest_distinguishes_maps_sequences_sets_and_empty_values():
    MappingManifest = RawTrackAssignmentCandidateInputManifest.Capture({
        "Value": {"A": 1},
    })
    PairSequenceManifest = RawTrackAssignmentCandidateInputManifest.Capture({
        "Value": [["A", 1]],
    })
    RepeatedPairs = RawTrackAssignmentCandidateInputManifest.Capture({
        "Pairs": [["A", 1], ["A", 2]],
    })
    LastPairOnly = RawTrackAssignmentCandidateInputManifest.Capture({
        "Pairs": [["A", 2]],
    })
    EmptyMap = RawTrackAssignmentCandidateInputManifest.Capture({
        "Value": {},
    })
    EmptySequence = RawTrackAssignmentCandidateInputManifest.Capture({
        "Value": [],
    })
    EmptySet = RawTrackAssignmentCandidateInputManifest.Capture({
        "Value": set(),
    })

    DistinctPairs = (
        (MappingManifest, PairSequenceManifest),
        (RepeatedPairs, LastPairOnly),
        (EmptyMap, EmptySequence),
        (EmptyMap, EmptySet),
        (EmptySequence, EmptySet),
    )
    for First, Second in DistinctPairs:
        assert First != Second
        assert First.ManifestFingerprint != Second.ManifestFingerprint
        assert First.ToDictionary() != Second.ToDictionary()

    MappingValue = TaggedMapEntries(
        MappingManifest.ToDictionary()["Payload"]
    )["Value"]
    SequenceValue = TaggedMapEntries(
        PairSequenceManifest.ToDictionary()["Payload"]
    )["Value"]
    assert MappingValue["Kind"] == "Map"
    assert SequenceValue["Kind"] == "Sequence"
    assert len(SequenceValue["Items"]) == 1


def test_tagged_manifest_preserves_nested_sequences_and_full_set_ordering():
    FirstSource = {
        "Nested": {
            "Pairs": [["A", 1], ["A", 2]],
            "Values": {"zeta", "alpha", "middle"},
        },
    }
    SecondSource = {
        "Nested": {
            "Values": set(reversed(("zeta", "alpha", "middle"))),
            "Pairs": [["A", 1], ["A", 2]],
        },
    }
    First = RawTrackAssignmentCandidateInputManifest.Capture(FirstSource)
    Second = RawTrackAssignmentCandidateInputManifest.Capture(SecondSource)

    assert First == Second
    assert First.ManifestFingerprint == Second.ManifestFingerprint
    assert First.ToDictionary() == Second.ToDictionary()
    Nested = TaggedMapEntries(First.ToDictionary()["Payload"])["Nested"]
    NestedEntries = TaggedMapEntries(Nested)
    assert NestedEntries["Pairs"]["Kind"] == "Sequence"
    assert len(NestedEntries["Pairs"]["Items"]) == 2
    assert NestedEntries["Values"]["Kind"] == "Set"


def test_preparation_serialization_preserves_pair_sequences_and_multiplicity():
    _Portfolio, Selection = RunCandidateTable(
        (
            CandidateCase(
                "feasible",
                (1,),
                RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible,
            ),
        ),
        OuterPortfolioComplete=False,
    )
    BaseResult = Selection.CandidatePreparationResults[0]
    Preparation = replace(
        BaseResult.Preparation,
        Diagnostics=(("Repeat", 1), ("Repeat", 2)),
    )
    Result = replace(BaseResult, Preparation=Preparation)
    PreparationPayload = Result.ToDictionary()["Preparation"]
    PreparationEntries = TaggedMapEntries(PreparationPayload)

    assert PreparationEntries["SelectedCandidateIds"]["Kind"] == "Sequence"
    assert PreparationEntries["CandidateCounts"]["Kind"] == "Sequence"
    Diagnostics = PreparationEntries["Diagnostics"]
    assert Diagnostics["Kind"] == "Sequence"
    assert len(Diagnostics["Items"]) == 2
    assert Diagnostics["Items"][0] != Diagnostics["Items"][1]


def test_public_single_nand_consumes_exact_candidate_result(monkeypatch):
    ObservedSelections = []
    OriginalSolve = PlacementSetup.SolveRawTrackAssignmentPortfolioWithContext

    def ObserveSelection(*Args, **Kwargs):
        Result = OriginalSolve(*Args, **Kwargs)
        ObservedSelections.append(Result)
        return Result

    monkeypatch.setattr(
        PlacementSetup,
        "SolveRawTrackAssignmentPortfolioWithContext",
        ObserveSelection,
    )
    Stages = []
    Result = PlaceAndRoutePcb(
        BuildSingleNandNetlist(),
        Strategy="routing-aware-placement-access",
        StageCallback=Stages.append,
    )

    assert len(ObservedSelections) == 1
    Selection = ObservedSelections[0]
    assert Selection.Success is True
    assert len(Selection.CandidatePreparationResults) == 1
    CandidateResult = Selection.CandidatePreparationResults[0]
    assert CandidateResult.Outcome is (
        RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible
    )
    assert CandidateResult.Preparation is Selection.Preparation
    ManifestPayload = CandidateResult.CandidateInputManifest.ToDictionary()[
        "Payload"
    ]
    assert set(TaggedMapEntries(ManifestPayload)) == {
        "Candidate",
        "FabricDescriptor",
        "FrozenEnvelopeRoutingPolicy",
        "Policy",
        "PortfolioMode",
        "ResolvedObjectiveInputs",
        "ResourceModelFingerprint",
        "RoutingEnvelope",
        "SelectedAccess",
        "TechnologyFingerprint",
    }
    assert "physical component interface planning" in Stages
    assert "placement candidate routing" in Stages
    assert "routing result publication" in Stages
    Published = Result.Routed.RoutingControlEffectiveness[
        "PrePlacementCapacitySelection"
    ]["RawTrackAssignmentSelection"]
    assert Published["CandidatePreparationResults"][0][
        "ResultFingerprint"
    ] == CandidateResult.ResultFingerprint


@pytest.mark.parametrize(
    "Corruption",
    ("candidate-input", "work-controls", "missing-result"),
)
def test_public_single_nand_rejects_substituted_selected_result(
    monkeypatch,
    Corruption,
):
    OriginalSolve = PlacementSetup.SolveRawTrackAssignmentPortfolioWithContext

    def CorruptSelection(*Args, **Kwargs):
        Result = OriginalSolve(*Args, **Kwargs)
        CandidateResult = Result.CandidatePreparationResults[0]
        if Corruption == "candidate-input":
            ForeignManifest = RawTrackAssignmentCandidateInputManifest.Capture({
                "CandidateId": CandidateResult.CandidateId,
                "Foreign": True,
            })
            CandidateResults = (
                replace(
                    CandidateResult,
                    CandidateInputFingerprint=(
                        ForeignManifest.ManifestFingerprint
                    ),
                    CandidateInputManifest=ForeignManifest,
                ),
            )
        elif Corruption == "work-controls":
            CandidateResults = (
                replace(
                    CandidateResult,
                    WorkControlsFingerprint="foreign-work-controls",
                ),
            )
        else:
            CandidateResults = ()
        return replace(
            Result,
            CandidatePreparationResults=CandidateResults,
        )

    monkeypatch.setattr(
        PlacementSetup,
        "SolveRawTrackAssignmentPortfolioWithContext",
        CorruptSelection,
    )
    Stages = []
    with pytest.raises(ValueError) as Error:
        PlaceAndRoutePcb(
            BuildSingleNandNetlist(),
            Strategy="routing-aware-placement-access",
            StageCallback=Stages.append,
        )

    Expected = {
        "candidate-input": "input identity mismatches",
        "work-controls": "work controls mismatch",
        "missing-result": "no exact preparation result",
    }
    assert Expected[Corruption] in str(Error.value)
    assert "physical component interface planning" not in Stages
    assert "placement candidate routing" not in Stages
    assert "routing result publication" not in Stages


@pytest.mark.parametrize(
    "Mutation",
    (
        "technology",
        "policy",
        "placement-resource",
        "selected-access-binding",
        "routing-envelope",
        "fabric-descriptor",
        "resolved-objective",
    ),
)
def test_public_materializer_rejects_changed_manifest_input(
    monkeypatch,
    Mutation,
):
    OriginalMaterialize = PlacementSetup.MaterializeRawTemplate
    ManifestComparisons = []

    def MaterializeChanged(Context, Descriptor):
        Candidate = Context.CandidateById[Descriptor.TemplateId]
        if Mutation == "technology":
            Context.Technology = replace(
                Context.Technology,
                TechnologyVersion=(
                    Context.Technology.TechnologyVersion + "-changed"
                ),
            )
        elif Mutation == "policy":
            Context.Policy = replace(
                Context.Policy,
                PlacementAccess=replace(
                    Context.Policy.PlacementAccess,
                    CatalogVersion=(
                        Context.Policy.PlacementAccess.CatalogVersion
                        + "-changed"
                    ),
                ),
            )
        elif Mutation == "placement-resource":
            Gates = list(Candidate.Placement.Placed.PlacedGates)
            Gates[0] = replace(Gates[0], X=Gates[0].X + 100)
            Candidate = replace(
                Candidate,
                Placement=replace(
                    Candidate.Placement,
                    Placed=replace(
                        Candidate.Placement.Placed,
                        PlacedGates=Gates,
                    ),
                ),
            )
            Context.CandidateById[Descriptor.TemplateId] = Candidate
        elif Mutation == "selected-access-binding":
            assert Candidate.PlacementAccessSolveBinding is not None
            Context.CandidateById[Descriptor.TemplateId] = replace(
                Candidate,
                PlacementAccessSolveBinding=None,
            )
        elif Mutation == "routing-envelope":
            assert Candidate.RoutingEnvelope is not None
            Context.CandidateById[Descriptor.TemplateId] = replace(
                Candidate,
                RoutingEnvelope=replace(
                    Candidate.RoutingEnvelope,
                    AccessRingTrackCount=(
                        Candidate.RoutingEnvelope.AccessRingTrackCount + 1
                    ),
                ),
            )
        elif Mutation == "fabric-descriptor":
            FabricDescriptor = (
                Context.PreRouteFabricDescriptorsByCandidateId[
                    Descriptor.TemplateId
                ]
            )
            Context.PreRouteFabricDescriptorsByCandidateId[
                Descriptor.TemplateId
            ] = replace(
                FabricDescriptor,
                AccessRingTrackCount=(
                    FabricDescriptor.AccessRingTrackCount + 1
                ),
            )
        else:
            assert Mutation == "resolved-objective"
            Context.CandidateById[Descriptor.TemplateId] = replace(
                Candidate,
                EstimatedGlobalExtensionNodes=(
                    Candidate.EstimatedGlobalExtensionNodes + 1
                ),
            )
        CurrentCandidate = Context.CandidateById[Descriptor.TemplateId]
        CurrentFabricDescriptor = (
            Context.PreRouteFabricDescriptorsByCandidateId[
                Descriptor.TemplateId
            ]
        )
        CurrentResources = Context.Services.BuildRoutingResources(
            CurrentCandidate.Placement.Placed,
            Technology=Context.Technology,
        )
        CurrentManifest = (
            RoutingAttempts.BuildRawTemplateMaterializationInputManifest(
                Context,
                CurrentCandidate,
                CurrentFabricDescriptor,
                Resources=CurrentResources,
            )
        )
        ManifestComparisons.append(
            CurrentManifest != Descriptor.MaterializationInputManifest
        )
        return OriginalMaterialize(Context, Descriptor)

    monkeypatch.setattr(
        PlacementSetup,
        "MaterializeRawTemplate",
        MaterializeChanged,
    )
    Stages = []
    with pytest.raises((ValueError, RoutingStageError)) as Error:
        PlaceAndRoutePcb(
            BuildSingleNandNetlist(),
            Strategy="routing-aware-placement-access",
            StageCallback=Stages.append,
        )

    if type(Error.value) is ValueError:
        assert "inputs changed before materialization" in str(Error.value)
    else:
        assert Error.value.Failure.Reason is (
            RoutingFailureReason.ClusterInterfaceInvariantViolation
        )
        assert Error.value.Failure.Stage in {
            "PlacementPinAccessHandoff",
            "PlacementAccessFabricHandoff",
        }
    assert ManifestComparisons == [True]

    assert "physical component interface planning" not in Stages
    assert "placement candidate routing" not in Stages
    assert "routing result publication" not in Stages


def test_public_materializer_revalidates_manifest_before_cached_return(
    monkeypatch,
):
    OriginalMaterialize = PlacementSetup.MaterializeRawTemplate
    Revalidated = []

    def MaterializeAndChallengeCache(Context, Descriptor):
        Result = OriginalMaterialize(Context, Descriptor)
        CurrentCandidate = Context.CandidateById[Descriptor.TemplateId]
        Context.CandidateById[Descriptor.TemplateId] = replace(
            CurrentCandidate,
            EstimatedGlobalExtensionNodes=(
                CurrentCandidate.EstimatedGlobalExtensionNodes + 1
            ),
        )
        try:
            with pytest.raises(
                ValueError,
                match="inputs changed before materialization",
            ):
                OriginalMaterialize(Context, Descriptor)
            Revalidated.append(Descriptor.TemplateId)
        finally:
            Context.CandidateById[Descriptor.TemplateId] = CurrentCandidate
        return Result

    monkeypatch.setattr(
        PlacementSetup,
        "MaterializeRawTemplate",
        MaterializeAndChallengeCache,
    )
    Result = PlaceAndRoutePcb(
        BuildSingleNandNetlist(),
        Strategy="routing-aware-placement-access",
    )

    assert Revalidated
    assert Result.Routed.RoutingControlEffectiveness[
        "PrePlacementCapacitySelection"
    ]["RawTrackAssignmentSelection"]["Success"] is True

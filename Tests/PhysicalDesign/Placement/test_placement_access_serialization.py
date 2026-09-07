"""Boundary corruption, round-trip, and handoff checks using real access records."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from types import SimpleNamespace

import pytest

from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Contracts.PlacementAccess import (
    PlacementAccessConflictCore, PlacementAccessSolveResult, PlacementAccessSolveStatus,
    PlacedPinAccessOption, SelectedPlacementPinAccessWitness,
)
from PhysicalDesign.Contracts.PlacementAccessHandoff import (
    PlacementPinAccessStageObservation, PlacementPinAccessStages,
    ValidatePlacementPinAccessHandoff,
)
from PhysicalDesign.Placement.Access.Capacity import SolvePlacedPinAccessOptionDomains
from PhysicalDesign.Placement.Access.Catalog import EnumeratePlacedPinAccessOptionDomains
from PhysicalDesign.Policy import RoutingAwarePlacementAccessPhysicalDesignPolicy as Policy
from Tests.PhysicalDesign.Placement.test_pin_access_catalog import _Nand, _Resources, Technology


@pytest.fixture
def Access():
    Gate = _Nand()
    Graph = _Resources((Gate,))
    Domains = EnumeratePlacedPinAccessOptionDomains(
        (Gate,), ResourceGraph=Graph, Technology=Technology,
        EnabledPatternFamilies=("straight",),
    )
    Solve = replace(SolvePlacedPinAccessOptionDomains(Domains, ResourceGraph=Graph), PolicyVersion=Policy.PolicyVersion)
    return Gate, Graph, Domains, Solve


def _Json(Value):
    return json.loads(json.dumps(Value.ToDictionary()))


def _Leaves(Value, Prefix=()):
    if isinstance(Value, dict):
        for Key, Item in Value.items():
            yield from _Leaves(Item, (*Prefix, Key))
    elif isinstance(Value, list):
        for Index, Item in enumerate(Value):
            yield from _Leaves(Item, (*Prefix, Index))
    else:
        yield Prefix, Value


def _Changed(Value):
    if type(Value) is bool:
        return not Value
    if type(Value) is int:
        return Value + 1
    if type(Value) is str:
        return Value + "-corrupt"
    return "corrupt-null"


def test_solve_and_witness_round_trip_every_status(Access):
    _Gate, Graph, Domains, Solve = Access
    IncompleteDomain = replace(Domains[0], Complete=False, IncompleteReason="catalog-domain-generation-work-cap")
    EmptyDomain = replace(Domains[0], Options=(), GeneratedOptionCount=0)
    Results = (
        Solve,
        SolvePlacedPinAccessOptionDomains((IncompleteDomain, *Domains[1:]), ResourceGraph=Graph),
        SolvePlacedPinAccessOptionDomains((EmptyDomain, *Domains[1:]), ResourceGraph=Graph),
    )
    assert {Value.Status for Value in Results} == set(PlacementAccessSolveStatus)
    for Value in Results:
        assert PlacementAccessSolveResult.FromDictionary(_Json(Value)) == Value
        if Value.ConflictCore is not None:
            assert PlacementAccessConflictCore.FromDictionary(_Json(Value.ConflictCore)) == Value.ConflictCore
    Witness = Solve.SelectedWitness
    assert SelectedPlacementPinAccessWitness.FromDictionary(_Json(Witness)) == Witness
    for Option in Witness.Selections:
        assert PlacedPinAccessOption.FromDictionary(_Json(Option)) == Option


def test_each_serialized_option_field_rejects_single_field_corruption(Access):
    Option = Access[3].SelectedWitness.Selections[0]
    Payload = _Json(Option)
    for Path, Value in _Leaves(Payload):
        Changed = deepcopy(Payload)
        Parent = Changed
        for Key in Path[:-1]:
            Parent = Parent[Key]
        Parent[Path[-1]] = _Changed(Value)
        with pytest.raises(ValueError, match="."):
            PlacedPinAccessOption.FromDictionary(Changed)


@pytest.mark.parametrize("Kind", ("missing", "unknown", "bool-integer", "duplicate", "reordered", "stale-domain", "stale-problem", "incomplete-feasible"))
def test_solve_reader_rejects_malformed_and_stale_evidence(Access, Kind):
    Payload = _Json(Access[3])
    if Kind == "missing":
        del Payload["SelectedWitness"]["ClaimsBySignal"]
    elif Kind == "unknown":
        Payload["SelectedWitness"]["AllowStale"] = True
    elif Kind == "bool-integer":
        Payload["ExpansionCount"] = True
    elif Kind == "duplicate":
        Payload["Domains"].append(deepcopy(Payload["Domains"][0]))
    elif Kind == "reordered":
        Payload["SelectedWitness"]["Selections"].reverse()
    elif Kind == "stale-domain":
        Payload["SelectedWitness"]["DomainFingerprints"][0] = "stale"
    elif Kind == "stale-problem":
        Payload["ProblemFingerprint"] = "stale"
    else:
        Payload["SelectedWitness"]["Complete"] = False
    with pytest.raises(ValueError):
        PlacementAccessSolveResult.FromDictionary(Payload)


def test_reader_detaches_mutable_json_and_preserves_claims(Access):
    Payload = _Json(Access[3])
    Copy = PlacementAccessSolveResult.FromDictionary(Payload)
    Payload["SelectedWitness"]["Selections"][0]["FirstLegNodes"][0][0] += 100
    assert Copy == Access[3]
    with pytest.raises(FrozenInstanceError):
        Copy.SelectedWitness.Complete = False


def _Observations(Solve):
    return tuple(replace(
        PlacementPinAccessStageObservation.FromWitness(Stage, Solve.SelectedWitness, Solve.PolicyVersion),
        CompactionPreserved=True if Stage == "Compaction" else None,
    ) for Stage in PlacementPinAccessStages)


def _Validate(Solve, Observations, **Overrides):
    Witness = Solve.SelectedWitness
    Values = dict(
        SolveResult=Solve, PolicyVersion=Solve.PolicyVersion,
        CatalogVersion=Witness.CatalogVersion, TechnologyFingerprint=Witness.TechnologyFingerprint,
        ResourceModelFingerprint=Witness.ResourceModelFingerprint,
    )
    Values.update(Overrides)
    return ValidatePlacementPinAccessHandoff(Witness, Observations, **Values)


def test_handoff_validates_real_immutable_records(Access):
    Solve = Access[3]
    Evidence = _Validate(Solve, _Observations(Solve))
    assert Evidence.ToDictionary()["SolveResultFingerprint"] == Solve.ToDictionary()["ResultFingerprint"]


@pytest.mark.parametrize("Index", range(5))
@pytest.mark.parametrize("Field,Value", (
    ("WitnessFingerprint", "stale"), ("DomainFingerprint", "stale"),
    ("PolicyVersion", "stale"), ("TechnologyFingerprint", "stale"),
    ("ResourceModelFingerprint", "stale"), ("CatalogVersion", "stale"),
    ("AccessRegenerationCount", 1), ("UnselectedPortalLeakCount", 1),
    ("AccessRegenerationCount", False),
))
def test_every_handoff_stage_rejects_identity_or_count_corruption(Access, Index, Field, Value):
    Solve = Access[3]
    Observations = list(_Observations(Solve))
    Observations[Index] = replace(Observations[Index], **{Field: Value})
    with pytest.raises(RoutingStageError) as Error:
        _Validate(Solve, tuple(Observations))
    assert Error.value.Failure.Reason is RoutingFailureReason.ClusterInterfaceInvariantViolation


def test_handoff_rejects_order_omissions_current_model_and_wrong_solve(Access):
    Solve = Access[3]
    Observations = _Observations(Solve)
    for Invalid in (Observations[:-1], tuple(reversed(Observations)), (*Observations, Observations[0]), (*Observations[:-1], replace(Observations[-1], CompactionPreserved=False))):
        with pytest.raises(RoutingStageError):
            _Validate(Solve, Invalid)
    with pytest.raises(RoutingStageError):
        _Validate(Solve, Observations, ResourceModelFingerprint="new-obstacle")
    Incomplete = replace(Solve, Status=PlacementAccessSolveStatus.Incomplete, SelectedWitness=None, SearchComplete=False, IncompleteReason="cancelled")
    with pytest.raises(RoutingStageError):
        _Validate(Solve, Observations, SolveResult=Incomplete)


def test_stage_one_generation_and_search_deadlines_never_publish_a_core(Access):
    Gate, Graph, Domains, _Solve = Access
    def Expired(_Details):
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete,
            Stage="PlacementAccessSolve", Detail="deadline expired",
        ))
    with pytest.raises(RoutingStageError):
        EnumeratePlacedPinAccessOptionDomains((Gate,), ResourceGraph=Graph, Technology=Technology, WorkCheck=Expired)
    Capped = SolvePlacedPinAccessOptionDomains(Domains, ResourceGraph=Graph, MaximumExpansions=1)
    assert Capped.Status is PlacementAccessSolveStatus.Incomplete
    assert Capped.ConflictCore is None


def test_context_adapter_uses_observed_identities_and_current_resources(Access, monkeypatch):
    from PhysicalDesign.Orchestration import Results
    Gate, Graph, _Domains, Solve = Access
    Observations = _Observations(Solve)
    Witness = Solve.SelectedWitness
    Fabric = SimpleNamespace(PinAccessWitness=Witness, FixedPinAccessSolve=Solve, PinAccessWitnessFingerprint=Witness.WitnessFingerprint, PinAccessDomainFingerprint=Witness.DomainFingerprint)
    Placement = SimpleNamespace(SelectedPinAccessWitness=Witness, PlacementAccessSolve=Solve, PlacementAccessFabric=Fabric, Placed=SimpleNamespace(PlacedGates=(Gate,), FrozenNetWires={}))
    Compaction = Observations[-1].ToDictionary()
    Compaction["ObservedWitnessFingerprint"] = Compaction["WitnessFingerprint"]
    Context = SimpleNamespace(
        Placement=Placement, Policy=Policy, Technology=Technology,
        Deadline=SimpleNamespace(RaiseIfExpired=lambda *_Args: None),
        SelectedTrackPreparation=SimpleNamespace(PinAccessHandoffObservation=Observations[2], PinAccessWitnessFingerprint=Witness.WitnessFingerprint, PinAccessDomainFingerprint=Witness.DomainFingerprint),
        Routed=SimpleNamespace(RoutingControlEffectiveness={"PlacementPinAccessWitness": Observations[3].ToDictionary()}, RoutingFootprintDiagnostics={"PlacementPinAccessWitness": Compaction}),
    )
    monkeypatch.setattr(Results, "BuildRoutingResources", lambda *_Args, **_Kwargs: SimpleNamespace(ResourceGraph=Graph))
    Result = Results.BuildPlacementPinAccessFinalizationDiagnostics(Context)
    assert len(Result["HandoffEvidence"]["Observations"]) == 5
    Contract = Results.BuildPlacementAccessPlanningContract(Placement, Result)["PlacementAccess"]
    assert PlacementAccessSolveResult.FromDictionary(Contract["SolveResult"]) == Solve
    assert SelectedPlacementPinAccessWitness.FromDictionary(Contract["SelectedWitness"]) == Witness
    assert Results.BuildPlacementAccessPlanningContract(None, None) == {}
    Context.SelectedTrackPreparation.PinAccessWitnessFingerprint = "substituted"
    with pytest.raises(RoutingStageError):
        Results.BuildPlacementPinAccessFinalizationDiagnostics(Context)
    Context.SelectedTrackPreparation.PinAccessWitnessFingerprint = Witness.WitnessFingerprint
    DeadlineFailure = RoutingStageError(RoutingFailure(
        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
        Stage="PlacementPinAccessFinalization", Detail="global deadline expired",
    ))
    def Expired(*_Args, **_Kwargs):
        raise DeadlineFailure
    monkeypatch.setattr(Results, "BuildRoutingResources", Expired)
    with pytest.raises(RoutingStageError) as Error:
        Results.BuildPlacementPinAccessFinalizationDiagnostics(Context)
    assert Error.value is DeadlineFailure

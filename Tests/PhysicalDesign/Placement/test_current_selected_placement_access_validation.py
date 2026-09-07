"""Outcome-first coverage for current selected placement-access receipts."""

from collections.abc import Mapping
from dataclasses import replace
import json

import pytest

from Compilation.Ir.Models import Gate, GateKind
from PhysicalDesign.Contracts.PlacementAccess import (
    CurrentSelectedPlacementAccessValidation,
    CurrentSelectedPlacementAccessValidationReason,
    CurrentSelectedPlacementAccessValidationStatus,
    PlacementAccessSolveStatus,
)
from PhysicalDesign.Geometry.Placement import BuildPlacedGate, PlacedDesign
from PhysicalDesign.Placement.Access import ValidateCurrentSelectedPlacementAccess
from PhysicalDesign.Placement.Access.Capacity import SolvePlacedPinAccessOptionDomains
from PhysicalDesign.Placement.Access.Catalog import (
    EnumeratePlacedPinAccessOptionDomains,
    NormalizeFrozenNetWires,
)
from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology


Technology = DefaultRedstoneRoutingTechnology


def _Fixture(*, FrozenNetWires=None, MaximumGenerationWork=100_000):
    Gates = (
        BuildPlacedGate(
            Gate("Source", GateKind.INPUT, ["A"], []), 0, 1, 0, 0, False
        ),
        BuildPlacedGate(
            Gate("Target", GateKind.OUTPUT, [], ["A"]),
            10,
            1,
            10,
            0,
            False,
        ),
    )
    Placed = PlacedDesign(
        Module=None,
        PlacedGates=list(Gates),
        FrozenNetWires=FrozenNetWires or {},
    )
    Resources = BuildRoutingResources(Placed, Technology=Technology)
    Domains = EnumeratePlacedPinAccessOptionDomains(
        Gates,
        ResourceGraph=Resources.ResourceGraph,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal=FrozenNetWires or {},
        MaximumGenerationWork=MaximumGenerationWork,
    )
    Solve = SolvePlacedPinAccessOptionDomains(
        Domains,
        ResourceGraph=Resources.ResourceGraph,
        MaximumExpansions=100,
    )
    return Gates, Resources.ResourceGraph, Domains, Solve


_UseSolveWitness = object()


def _Validate(Gates, Graph, Solve, *, Witness=_UseSolveWitness, TechnologyValue=Technology, FrozenNetWires=None):
    return ValidateCurrentSelectedPlacementAccess(
        Gates,
        Solve.SelectedWitness if Witness is _UseSolveWitness else Witness,
        Solve,
        ResourceGraph=Graph,
        Technology=TechnologyValue,
        FrozenNetWires=FrozenNetWires or {},
    )


def test_current_feasible_receipt_is_deterministic_and_structurally_decodable():
    Gates, Graph, _Domains, Solve = _Fixture()
    assert Solve.Status is PlacementAccessSolveStatus.Feasible

    First = _Validate(Gates, Graph, Solve)
    Second = _Validate(Gates, Graph, Solve)

    assert First.Status is CurrentSelectedPlacementAccessValidationStatus.Verified
    assert First.Reason is CurrentSelectedPlacementAccessValidationReason.Current
    assert First.AccessRegenerationCount == 0
    assert First.ToDictionary() == Second.ToDictionary()
    assert CurrentSelectedPlacementAccessValidation.FromDictionary(
        json.loads(json.dumps(First.ToDictionary()))
    ) == First
    assert First.InputIdentity.WitnessFingerprint == Solve.SelectedWitness.WitnessFingerprint
    assert First.InputIdentity.SolveResultFingerprint == Solve.ToDictionary()["ResultFingerprint"]


@pytest.mark.parametrize("Mutation", (
    lambda Gates: setattr(Gates[1], "InputPins", [(11, 1, 10)]),
    lambda Gates: setattr(Gates[1], "InputDirections", [(1, 0, 0)]),
    lambda Gates: (
        setattr(Gates[1], "Kind", "NAND"),
        setattr(Gates[1], "Outputs", ["Y"]),
        setattr(Gates[1], "Inputs", ["A", "B"]),
        setattr(Gates[1], "InputPins", [(10, 1, 9), (12, 1, 10)]),
        setattr(Gates[1], "InputDirections", [(0, 0, -1), (1, 0, 0)]),
        setattr(Gates[1], "OutputPin", (10, 1, 12)),
        setattr(Gates[1], "OutputDirection", (0, 0, 1)),
    ),
    lambda Gates: setattr(Gates[1], "Inputs", ["B"]),
))
def test_current_terminal_tuple_changes_are_typed_mismatches(Mutation):
    Gates, Graph, _Domains, Solve = _Fixture()
    Mutation(Gates)

    Result = _Validate(Gates, Graph, Solve)

    assert Result.Status is CurrentSelectedPlacementAccessValidationStatus.Mismatch
    assert Result.Reason is (
        CurrentSelectedPlacementAccessValidationReason.TerminalBindingsMismatch
    )


def test_current_resource_and_technology_changes_have_typed_outcomes():
    Gates, Graph, _Domains, Solve = _Fixture()
    DifferentTechnology = replace(Technology, TrackPitch=Technology.TrackPitch + 1)

    GraphMismatch = _Validate(
        Gates, Graph, Solve, TechnologyValue=DifferentTechnology,
    )
    assert GraphMismatch.Reason is (
        CurrentSelectedPlacementAccessValidationReason.
        ResourceGraphTechnologyMismatch
    )

    TechnologyMismatch = _Validate(
        Gates,
        replace(Graph, Technology=DifferentTechnology),
        Solve,
        TechnologyValue=DifferentTechnology,
    )
    assert TechnologyMismatch.Reason is (
        CurrentSelectedPlacementAccessValidationReason.TechnologyFingerprintMismatch
    )

    FrozenPosition = next(iter(Graph.ElectricalBlocks))
    ModelMismatch = _Validate(
        Gates, Graph, Solve, FrozenNetWires={"Foreign": (FrozenPosition,)},
    )
    assert ModelMismatch.Reason is (
        CurrentSelectedPlacementAccessValidationReason.ResourceModelFingerprintMismatch
    )


def test_frozen_position_absent_from_graph_propagates_value_error():
    Gates, Graph, _Domains, Solve = _Fixture()

    with pytest.raises(ValueError, match="absent from the resource graph"):
        _Validate(
            Gates, Graph, Solve, FrozenNetWires={"Foreign": ((999, 1, 999),)},
        )


def test_incomplete_and_unsatisfiable_solves_have_no_fabricated_witness_identity():
    Gates, Graph, _Domains, Incomplete = _Fixture(MaximumGenerationWork=1)
    assert Incomplete.Status is PlacementAccessSolveStatus.Incomplete
    IncompleteReceipt = _Validate(Gates, Graph, Incomplete)
    assert IncompleteReceipt.Status is CurrentSelectedPlacementAccessValidationStatus.Unresolved
    assert IncompleteReceipt.Reason is CurrentSelectedPlacementAccessValidationReason.IncompleteSolve
    assert IncompleteReceipt.InputIdentity.WitnessFingerprint is None
    assert IncompleteReceipt.InputIdentity.DomainFingerprint is None
    assert IncompleteReceipt.InputIdentity.WitnessCatalogVersion is None

    Gates, Graph, Domains, _Feasible = _Fixture()
    EmptyDomains = tuple(
        replace(Domain, Options=(), GeneratedOptionCount=0)
        for Domain in Domains
    )
    Unsatisfiable = SolvePlacedPinAccessOptionDomains(
        EmptyDomains, ResourceGraph=Graph, MaximumExpansions=100,
    )
    assert Unsatisfiable.Status is PlacementAccessSolveStatus.Unsatisfiable
    UnsatisfiableReceipt = _Validate(Gates, Graph, Unsatisfiable)
    assert UnsatisfiableReceipt.Status is CurrentSelectedPlacementAccessValidationStatus.Unresolved
    assert UnsatisfiableReceipt.Reason is CurrentSelectedPlacementAccessValidationReason.UnsatisfiableSolve
    assert UnsatisfiableReceipt.InputIdentity.WitnessFingerprint is None


def test_missing_witness_is_a_mismatch_and_replayed_receipt_is_not_authority():
    Gates, Graph, _Domains, Solve = _Fixture()
    Missing = _Validate(Gates, Graph, Solve, Witness=None)
    assert Missing.Reason is (
        CurrentSelectedPlacementAccessValidationReason.SolveWitnessMismatch
    )

    Receipt = _Validate(Gates, Graph, Solve)
    OldReceipt = CurrentSelectedPlacementAccessValidation.FromDictionary(
        json.loads(json.dumps(Receipt.ToDictionary()))
    )
    Gates[1].InputPins[0] = (11, 1, 10)
    Fresh = _Validate(Gates, Graph, Solve)
    assert OldReceipt.Status is CurrentSelectedPlacementAccessValidationStatus.Verified
    assert Fresh.Status is CurrentSelectedPlacementAccessValidationStatus.Mismatch


def test_public_regeneration_entrypoints_are_not_needed(monkeypatch):
    Gates, Graph, _Domains, Solve = _Fixture()

    def Fail(*_Args, **_Keywords):
        raise AssertionError("validation must not regenerate access")

    monkeypatch.setattr(
        "PhysicalDesign.Placement.Access.Catalog.EnumeratePlacedPinAccessOptionDomains",
        Fail,
    )
    monkeypatch.setattr(
        "PhysicalDesign.Placement.Access.Capacity.SolvePlacedPinAccessOptionDomains",
        Fail,
    )
    Result = _Validate(Gates, Graph, Solve)
    assert Result.Status is CurrentSelectedPlacementAccessValidationStatus.Verified


class _ChangesOnSecondObservation(Mapping):
    def __init__(self, Position):
        self.Position = Position
        self.Observations = 0

    def __iter__(self):
        self.Observations += 1
        return iter(("Foreign",))

    def __len__(self):
        return 1

    def __getitem__(self, Key):
        assert Key == "Foreign"
        return () if self.Observations == 1 else (self.Position,)


def test_mid_validation_public_input_change_cannot_publish_verified():
    Gates, Graph, _Domains, Solve = _Fixture()
    ChangingWires = _ChangesOnSecondObservation(next(iter(Graph.ElectricalBlocks)))

    Result = _Validate(Gates, Graph, Solve, FrozenNetWires=ChangingWires)

    assert Result.Status is CurrentSelectedPlacementAccessValidationStatus.Mismatch
    assert Result.Reason is (
        CurrentSelectedPlacementAccessValidationReason.
        CurrentInputChangedDuringValidation
    )
    assert Result.InputIdentity.FinalObservationFingerprint


def test_receipt_codec_rejects_tampering_and_has_no_external_authority_axes():
    Gates, Graph, _Domains, Solve = _Fixture()
    Receipt = _Validate(Gates, Graph, Solve)
    Document = json.loads(json.dumps(Receipt.ToDictionary()))
    Forbidden = {
        "ProducerRevision", "PolicyVersion", "ExpectedCatalogVersion", "Trusted",
        "Validated", "CandidateId", "RecordedPlacementFingerprint", "Bounds",
        "Envelope", "Lease", "Channel", "Track", "Cache", "Reuse", "Ranking",
        "Readiness", "Commitment",
    }
    assert not (set(Document) | set(Document["InputIdentity"])) & Forbidden
    Document["InputIdentity"]["TerminalBindingFingerprint"] = "tampered"
    with pytest.raises(ValueError):
        CurrentSelectedPlacementAccessValidation.FromDictionary(Document)


def test_block_states_are_part_of_current_resource_model_identity():
    Gates, Graph, _Domains, Solve = _Fixture()
    ChangedGraph = replace(
        Graph,
        BlockStates={(0, 0, 0): {"Block": "minecraft:stone", "Lit": False}},
    )

    OldWitnessReceipt = _Validate(Gates, ChangedGraph, Solve)
    assert OldWitnessReceipt.Status is CurrentSelectedPlacementAccessValidationStatus.Mismatch
    assert OldWitnessReceipt.Reason is (
        CurrentSelectedPlacementAccessValidationReason.ResourceModelFingerprintMismatch
    )

    ChangedDomains = EnumeratePlacedPinAccessOptionDomains(
        Gates,
        ResourceGraph=ChangedGraph,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal={},
    )
    ChangedSolve = SolvePlacedPinAccessOptionDomains(
        ChangedDomains, ResourceGraph=ChangedGraph, MaximumExpansions=100,
    )
    assert ChangedSolve.Status is PlacementAccessSolveStatus.Feasible
    FreshReceipt = _Validate(Gates, ChangedGraph, ChangedSolve)
    assert FreshReceipt.Status is CurrentSelectedPlacementAccessValidationStatus.Verified


@pytest.mark.parametrize("NonFinite", (float("nan"), float("inf"), float("-inf")))
def test_nonfinite_block_states_fail_before_any_receipt(NonFinite):
    Gates, Graph, _Domains, Solve = _Fixture()
    with pytest.raises(TypeError, match="non-finite"):
        ChangedGraph = replace(Graph, BlockStates={(0, 0, 0): NonFinite})
        _Validate(Gates, ChangedGraph, Solve)


def test_duplicate_frozen_position_fails_before_any_receipt():
    Gates, Graph, _Domains, Solve = _Fixture()
    Position = next(iter(Graph.ElectricalBlocks))

    with pytest.raises(ValueError, match="repeat a position"):
        _Validate(
            Gates, Graph, Solve, FrozenNetWires={"Foreign": [Position, Position]},
        )


def test_reordered_unique_frozen_positions_have_the_same_receipt():
    Gates, Graph, _Domains, Solve = _Fixture()
    First, Second = tuple(sorted(Graph.ElectricalBlocks))[:2]
    ListReceipt = _Validate(
        Gates, Graph, Solve, FrozenNetWires={"Foreign": [First, Second]},
    )
    TupleReceipt = _Validate(
        Gates, Graph, Solve, FrozenNetWires={"Foreign": (Second, First)},
    )
    SetReceipt = _Validate(
        Gates, Graph, Solve, FrozenNetWires={"Foreign": {First, Second}},
    )

    assert ListReceipt.ToDictionary() == TupleReceipt.ToDictionary()
    assert ListReceipt.ToDictionary() == SetReceipt.ToDictionary()


def test_one_shot_frozen_wire_iterable_never_returns_verified():
    Gates, Graph, _Domains, Solve = _Fixture()

    with pytest.raises(TypeError, match="one-shot iterable"):
        _Validate(
            Gates, Graph, Solve, FrozenNetWires={"Foreign": (Position for Position in ())},
        )


class _CustomFrozenWireMapping(Mapping):
    def __init__(self, Entries):
        self.Entries = tuple(Entries)

    def items(self):
        return self.Entries

    def __iter__(self):
        return iter(tuple(Signal for Signal, _Positions in self.Entries))

    def __len__(self):
        return len(self.Entries)

    def __getitem__(self, Key):
        return next(
            Positions
            for Signal, Positions in self.Entries
            if Signal == Key
        )


def test_duplicate_custom_mapping_signal_entries_fail_before_receipt_identity():
    Gates, Graph, _Domains, Solve = _Fixture()
    Position = next(iter(Graph.ElectricalBlocks))
    DuplicateEntries = _CustomFrozenWireMapping((
        ("Foreign", ()),
        ("Foreign", (Position,)),
    ))

    with pytest.raises(ValueError, match="repeat a signal"):
        NormalizeFrozenNetWires(DuplicateEntries)
    with pytest.raises(ValueError, match="repeat a signal"):
        _Validate(Gates, Graph, Solve, FrozenNetWires=DuplicateEntries)


def test_benign_custom_mapping_keeps_unique_frozen_wire_behavior():
    Gates, Graph, _Domains, Solve = _Fixture()
    Custom = _CustomFrozenWireMapping((("Foreign", ()),))

    CustomReceipt = _Validate(Gates, Graph, Solve, FrozenNetWires=Custom)
    DictReceipt = _Validate(
        Gates, Graph, Solve, FrozenNetWires={"Foreign": ()},
    )

    assert CustomReceipt.Status is CurrentSelectedPlacementAccessValidationStatus.Verified
    assert CustomReceipt.ToDictionary() == DictReceipt.ToDictionary()


def test_drift_receipts_require_final_observation_and_non_drift_rejects_it():
    Gates, Graph, _Domains, Solve = _Fixture()
    Receipt = _Validate(Gates, Graph, Solve)
    DriftIdentity = replace(
        Receipt.InputIdentity,
        FinalObservationFingerprint="final-observation",
    )
    Drift = CurrentSelectedPlacementAccessValidation(
        Status=CurrentSelectedPlacementAccessValidationStatus.Mismatch,
        Reason=(
            CurrentSelectedPlacementAccessValidationReason.
            CurrentInputChangedDuringValidation
        ),
        InputIdentity=DriftIdentity,
    )
    with pytest.raises(ValueError, match="requires the exact final observation"):
        CurrentSelectedPlacementAccessValidation(
            Status=CurrentSelectedPlacementAccessValidationStatus.Mismatch,
            Reason=(
                CurrentSelectedPlacementAccessValidationReason.
                CurrentInputChangedDuringValidation
            ),
            InputIdentity=replace(DriftIdentity, FinalObservationFingerprint=None),
        )
    with pytest.raises(ValueError, match="requires the exact final observation"):
        CurrentSelectedPlacementAccessValidation(
            Status=CurrentSelectedPlacementAccessValidationStatus.Verified,
            Reason=CurrentSelectedPlacementAccessValidationReason.Current,
            InputIdentity=DriftIdentity,
        )

    MissingFinalDocument = Drift.ToDictionary()
    MissingFinalDocument["InputIdentity"] = replace(
        DriftIdentity,
        FinalObservationFingerprint=None,
    ).ToDictionary()
    with pytest.raises(ValueError):
        CurrentSelectedPlacementAccessValidation.FromDictionary(MissingFinalDocument)

    InappropriateFinalDocument = Receipt.ToDictionary()
    InappropriateFinalDocument["InputIdentity"] = DriftIdentity.ToDictionary()
    with pytest.raises(ValueError):
        CurrentSelectedPlacementAccessValidation.FromDictionary(
            InappropriateFinalDocument
        )


def test_receipt_requires_v3_resource_graph_before_classification():
    Gates, Graph, _Domains, Solve = _Fixture()
    Unsupported = replace(Graph, GraphVersion="abstract-cache-test-v1")

    with pytest.raises(ValueError, match="routing-resource-graph-v3"):
        _Validate(Gates, Unsupported, Solve)

"""Public immutable-envelope replay coverage for v17 placement access."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

import pytest

from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from PhysicalDesign.Contracts.Failures import (
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Contracts.PlacementAccess import (
    PlacementAccessSolveResult,
    PlacementAccessSolveStatus,
    SelectedPlacementPinAccessWitness,
)
from PhysicalDesign.Contracts.PlacementAccessHandoff import (
    PlacementPinAccessStages,
)
from PhysicalDesign.Orchestration.Runner import PlaceAndRoutePcb
from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources
from PhysicalDesign.Redstone.Technology import RepeaterOutputDelta
from PhysicalDesign.Resources.ResourceGraph import (
    FindClaimConflicts,
    RoutingResourceKind,
)
import PhysicalDesign.Orchestration.PlacementAttempts as PlacementAttempts
import PhysicalDesign.Orchestration.PhysicalFlow as PhysicalFlow
import PhysicalDesign.Orchestration.Setup as PlacementSetup
import PhysicalDesign.Orchestration.RoutingAttempts as RoutingAttempts
import PhysicalDesign.Placement.Access.Fabric as AccessFabric
import PhysicalDesign.Routing.Global.Orchestration.Flow as RoutingFlow
import PhysicalDesign.Routing.Planning.ChannelPlanner as ChannelPlanner


def _BuildFanoutTwoNandNetlist() -> NetlistIR:
    Module = ModuleIR(
        Name="PlacementEnvelopeReplay",
        Inputs=["A"],
        Outputs=["T", "Z"],
        Gates=[
            Gate("InputA", GateKind.INPUT, ["A"]),
            Gate("Nand0", GateKind.NAND, ["T"], ["A", "A"]),
            Gate("Nand1", GateKind.NAND, ["Z"], ["T", "T"]),
            Gate("OutputT", GateKind.OUTPUT, [], ["T"]),
            Gate("OutputZ", GateKind.OUTPUT, [], ["Z"]),
        ],
    )
    return NetlistIR(Top=Module.Name, Modules={Module.Name: Module})


def _BuildSingleNandNetlist() -> NetlistIR:
    Module = ModuleIR(
        Name="SingleNandPlacementEnvelopeReplay",
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


def _BuildSeventeenNandChainNetlist() -> NetlistIR:
    Module = ModuleIR(
        Name="SeventeenNandSelectedAccess",
        Inputs=["s0"],
        Outputs=["s17"],
        Gates=[
            Gate("InputA", GateKind.INPUT, ["s0"], []),
            *(
                Gate(
                    f"Nand{Index}",
                    GateKind.NAND,
                    [f"s{Index + 1}"],
                    [f"s{Index}", f"s{Index}"],
                )
                for Index in range(17)
            ),
            Gate("OutputY", GateKind.OUTPUT, [], ["s17"]),
        ],
    )
    return NetlistIR(Top=Module.Name, Modules={Module.Name: Module})


def _CanonicalClaims(Claims) -> tuple[tuple[tuple[int, int, int], ...], ...]:
    return tuple(
        tuple(sorted(getattr(Claims, Field)))
        for Field in (
            "WireCells",
            "SupportCells",
            "RequiredAirCells",
            "ElectricalCells",
        )
    )


def _CanonicalCandidateValues(CandidatesBySignal) -> dict[str, tuple]:
    return {
        str(Signal): tuple(sorted(
            (
                str(Candidate.CandidateId),
                _CanonicalClaims(Candidate.Claims),
            )
            for Candidate in Candidates
        ))
        for Signal, Candidates in sorted(CandidatesBySignal.items())
    }


def _PlacedTerminalGeometry(Placed) -> dict[tuple[str, ...], tuple]:
    Result = {}
    for GateValue in Placed.PlacedGates:
        if GateValue.OutputPin is not None:
            for Signal in GateValue.Outputs:
                Result[(
                    str(Signal),
                    str(GateValue.Name),
                    "Source",
                    "Output0",
                )] = (
                    tuple(GateValue.OutputPin),
                    tuple(GateValue.OutputDirection),
                )
        for InputIndex, Signal in enumerate(GateValue.Inputs):
            Result[(
                str(Signal),
                str(GateValue.Name),
                "Target",
                f"Input{InputIndex}",
            )] = (
                tuple(GateValue.InputPins[InputIndex]),
                tuple(GateValue.InputDirections[InputIndex]),
            )
    return Result


def _Connected(Nodes, Start, End) -> bool:
    Pending = [Start]
    Seen = {Start}
    while Pending:
        Position = Pending.pop()
        if Position == End:
            return True
        for Axis in range(3):
            for Delta in (-1, 1):
                Neighbor = tuple(
                    Value + (Delta if Index == Axis else 0)
                    for Index, Value in enumerate(Position)
                )
                if Neighbor in Nodes and Neighbor not in Seen:
                    Seen.add(Neighbor)
                    Pending.append(Neighbor)
    return False


def _TranslatedGate(GateValue, Delta):
    def Translate(Position):
        if Position is None:
            return None
        return tuple(
            Position[Axis] + Delta[Axis]
            for Axis in range(3)
        )

    return replace(
        GateValue,
        X=GateValue.X + Delta[0],
        Y=GateValue.Y + Delta[1],
        Z=GateValue.Z + Delta[2],
        InputPins=[Translate(Position) for Position in GateValue.InputPins],
        OutputPin=Translate(GateValue.OutputPin),
    )


def _MutateSelectedAccessConsumer(Placement, Technology, Kind, Evidence):
    Placed = Placement.Placed
    Witness = Placement.SelectedPinAccessWitness
    assert Witness is not None
    Evidence["WitnessFingerprint"] = Witness.WitnessFingerprint

    if Kind in {"moved-terminal", "changed-face"}:
        Selection = next(
            Value
            for Value in Witness.Selections
            if Value.Role == "Target" and Value.GateKind == "OUTPUT"
        )
        GateIndex = next(
            Index
            for Index, GateValue in enumerate(Placed.PlacedGates)
            if GateValue.Name == Selection.GateName
        )
        CurrentGate = Placed.PlacedGates[GateIndex]
        InputIndex = int(Selection.PinId.removeprefix("Input"))
        if Kind == "moved-terminal":
            MutatedGate = _TranslatedGate(CurrentGate, (37, 0, 0))
            Evidence["OriginalTerminal"] = Selection.Terminal
            Evidence["CurrentTerminal"] = MutatedGate.InputPins[InputIndex]
            assert Evidence["CurrentTerminal"] != Evidence["OriginalTerminal"]
        else:
            Directions = list(CurrentGate.InputDirections)
            OriginalFace = tuple(Directions[InputIndex])
            Directions[InputIndex] = next(
                Value
                for Value in (
                    (1, 0, 0),
                    (-1, 0, 0),
                    (0, 0, 1),
                    (0, 0, -1),
                )
                if Value != OriginalFace
            )
            MutatedGate = replace(
                CurrentGate,
                InputDirections=Directions,
            )
            Evidence["OriginalTerminal"] = Selection.Terminal
            Evidence["CurrentTerminal"] = MutatedGate.InputPins[InputIndex]
            Evidence["OriginalFace"] = Selection.Face
            Evidence["CurrentFace"] = MutatedGate.InputDirections[InputIndex]
            assert Evidence["CurrentTerminal"] == Evidence["OriginalTerminal"]
            assert Evidence["CurrentFace"] != Evidence["OriginalFace"]
        Gates = list(Placed.PlacedGates)
        Gates[GateIndex] = MutatedGate
        MutatedPlaced = replace(Placed, PlacedGates=Gates)
    elif Kind == "foreign-selected-claim":
        Selection = next(
            Value
            for Value in Witness.Selections
            if Value.Claims.ElectricalCells
        )
        Conflict = min(Selection.Claims.ElectricalCells)
        Frozen = dict(Placed.FrozenNetWires or {})
        Frozen["__foreign_selected_claim__"] = (Conflict,)
        MutatedPlaced = replace(Placed, FrozenNetWires=Frozen)
        Evidence["ConflictingPosition"] = Conflict
        Evidence["SelectedClaimSignal"] = Selection.Signal
        assert Conflict in Selection.Claims.ElectricalCells
    else:
        assert Kind == "changed-technology"
        OriginalTechnology = Technology
        Technology = replace(
            OriginalTechnology,
            TechnologyVersion=(
                OriginalTechnology.TechnologyVersion
                + "-stale-consumer-regression"
            ),
        )
        Evidence["OriginalTechnologyVersion"] = (
            OriginalTechnology.TechnologyVersion
        )
        Evidence["CurrentTechnologyVersion"] = (
            Technology.TechnologyVersion
        )
        assert (
            Evidence["CurrentTechnologyVersion"]
            != Evidence["OriginalTechnologyVersion"]
        )
        MutatedPlaced = Placed

    return replace(Placement, Placed=MutatedPlaced), Technology


def _GuardForbiddenSelectedAccessWork(
    monkeypatch,
    Evidence,
    ForbiddenWork,
    PreMutationWork,
) -> None:
    Boundaries = (
        (
            AccessFabric,
            "BuildPlacementPinAccessWitness",
            "access-fabric-witness-regeneration",
        ),
        (
            ChannelPlanner,
            "BuildPlacementPinAccessWitness",
            "routing-profile-witness-regeneration",
        ),
        (
            PlacementAttempts,
            "EnumeratePlacedPinAccessOptionDomains",
            "catalog-enumeration",
        ),
        (
            PlacementAttempts,
            "SolvePlacedPinAccessOptionDomains",
            "access-solve",
        ),
    )
    for Module, Name, Label in Boundaries:
        Original = getattr(Module, Name)

        def Guard(*Args, _Original=Original, _Label=Label, **Kwargs):
            if Evidence["BoundaryMutationCount"]:
                ForbiddenWork.append(_Label)
                raise AssertionError(
                    f"stale selected access entered {_Label}"
                )
            PreMutationWork[_Label] += 1
            return _Original(*Args, **Kwargs)

        monkeypatch.setattr(Module, Name, Guard)


@pytest.mark.parametrize(
    "Kind",
    (
        "moved-terminal",
        "changed-face",
        "foreign-selected-claim",
        "changed-technology",
    ),
)
def test_public_fanout_rejects_stale_selected_access_before_routing(
    monkeypatch,
    Kind,
) -> None:
    """Reject a selected-access contract changed at its real consumer seam."""
    OriginalValidate = (
        PlacementSetup.ValidateCurrentSelectedPlacementAccessConsumer
    )
    OriginalRawAssignment = RoutingAttempts.PrepareRawTrackAssignmentDomain
    OriginalRouting = RoutingFlow.RunAuthoritativeRoutingPhases
    Evidence = {"BoundaryMutationCount": 0}
    ForbiddenWork = []
    PreMutationWork = defaultdict(int)
    Downstream = []
    Stages = []

    def ValidateStaleConsumer(Placement, **Options):
        Evidence["BoundaryMutationCount"] += 1
        Placement, Options["Technology"] = _MutateSelectedAccessConsumer(
            Placement,
            Options["Technology"],
            Kind,
            Evidence,
        )
        Options["Resources"] = BuildRoutingResources(
            Placement.Placed,
            Technology=Options["Technology"],
        )
        return OriginalValidate(Placement, **Options)

    def ObserveRawAssignment(*Args, **Kwargs):
        if Evidence["BoundaryMutationCount"]:
            Downstream.append("raw-assignment")
            raise AssertionError(
                "stale selected access reached raw assignment"
            )
        return OriginalRawAssignment(*Args, **Kwargs)

    def ObserveRouting(*Args, **Kwargs):
        if Evidence["BoundaryMutationCount"]:
            Downstream.append("detailed-routing")
            raise AssertionError(
                "stale selected access reached detailed routing"
            )
        return OriginalRouting(*Args, **Kwargs)

    _GuardForbiddenSelectedAccessWork(
        monkeypatch,
        Evidence,
        ForbiddenWork,
        PreMutationWork,
    )
    monkeypatch.setattr(
        PlacementSetup,
        "ValidateCurrentSelectedPlacementAccessConsumer",
        ValidateStaleConsumer,
    )
    monkeypatch.setattr(
        RoutingAttempts,
        "PrepareRawTrackAssignmentDomain",
        ObserveRawAssignment,
    )
    monkeypatch.setattr(
        RoutingFlow,
        "RunAuthoritativeRoutingPhases",
        ObserveRouting,
    )

    with pytest.raises(RoutingStageError) as Error:
        PlaceAndRoutePcb(
            _BuildFanoutTwoNandNetlist(),
            Strategy="routing-aware-placement-access",
            StageCallback=Stages.append,
        )

    assert Evidence["BoundaryMutationCount"] == 1, (
        Error.value.Failure.ToDictionary(),
        Stages,
    )
    assert Error.value.Failure.Reason is (
        RoutingFailureReason.ClusterInterfaceInvariantViolation
    )
    ExpectedStage = (
        "PlacementPinAccessHandoff"
        if Kind in {"moved-terminal", "changed-face"}
        else "PlacementAccessFabricHandoff"
    )
    assert Error.value.Failure.Stage == ExpectedStage
    assert ForbiddenWork == []
    assert PreMutationWork["catalog-enumeration"] > 0
    assert PreMutationWork["access-solve"] > 0
    assert Downstream == []
    assert "physical component interface planning" not in Stages
    assert "placement candidate routing" not in Stages
    assert "routing result publication" not in Stages


def test_public_single_nand_raw_materializer_rejects_foreign_selected_claim(
    monkeypatch,
) -> None:
    """Rebuild descriptor resources from current frozen-wire ownership."""
    OriginalMaterialize = PlacementSetup.MaterializeRawTemplate
    OriginalRawAssignment = RoutingAttempts.PrepareRawTrackAssignmentDomain
    OriginalRouting = RoutingFlow.RunAuthoritativeRoutingPhases
    Evidence = {"BoundaryMutationCount": 0}
    ForbiddenWork = []
    PreMutationWork = defaultdict(int)
    Downstream = []
    Stages = []

    def MaterializeStaleConsumer(Context, Descriptor):
        Evidence["BoundaryMutationCount"] += 1
        Candidate = Context.CandidateById[Descriptor.TemplateId]
        Placement, Technology = _MutateSelectedAccessConsumer(
            Candidate.Placement,
            Context.Technology,
            "foreign-selected-claim",
            Evidence,
        )
        assert Technology is Context.Technology
        MutatedCandidate = replace(Candidate, Placement=Placement)
        Context.CandidateById[Descriptor.TemplateId] = MutatedCandidate
        Context.CandidateRecords[
            Context.CandidateIndexById[Descriptor.TemplateId]
        ] = MutatedCandidate
        return OriginalMaterialize(Context, Descriptor)

    def ObserveRawAssignment(*Args, **Kwargs):
        if Evidence["BoundaryMutationCount"]:
            Downstream.append("raw-assignment")
            raise AssertionError(
                "foreign selected-claim ownership reached raw assignment"
            )
        return OriginalRawAssignment(*Args, **Kwargs)

    def ObserveRouting(*Args, **Kwargs):
        if Evidence["BoundaryMutationCount"]:
            Downstream.append("detailed-routing")
            raise AssertionError(
                "foreign selected-claim ownership reached detailed routing"
            )
        return OriginalRouting(*Args, **Kwargs)

    _GuardForbiddenSelectedAccessWork(
        monkeypatch,
        Evidence,
        ForbiddenWork,
        PreMutationWork,
    )
    monkeypatch.setattr(
        PlacementSetup,
        "MaterializeRawTemplate",
        MaterializeStaleConsumer,
    )
    monkeypatch.setattr(
        RoutingAttempts,
        "PrepareRawTrackAssignmentDomain",
        ObserveRawAssignment,
    )
    monkeypatch.setattr(
        RoutingFlow,
        "RunAuthoritativeRoutingPhases",
        ObserveRouting,
    )

    with pytest.raises(RoutingStageError) as Error:
        PlaceAndRoutePcb(
            _BuildSingleNandNetlist(),
            Strategy="routing-aware-placement-access",
            StageCallback=Stages.append,
        )

    assert Evidence["BoundaryMutationCount"] == 1, (
        Error.value.Failure.ToDictionary(),
        Stages,
    )
    assert Error.value.Failure.Reason is (
        RoutingFailureReason.ClusterInterfaceInvariantViolation
    )
    assert Error.value.Failure.Stage == "PlacementAccessFabricHandoff"
    assert ForbiddenWork == []
    assert PreMutationWork["catalog-enumeration"] > 0
    assert PreMutationWork["access-solve"] > 0
    assert Downstream == []
    assert "physical component interface planning" not in Stages
    assert "placement candidate routing" not in Stages
    assert "routing result publication" not in Stages


def test_enabled_selected_access_preserves_ordinary_fabric_value_error(
    monkeypatch,
) -> None:
    Calls = []

    def RaiseOrdinaryFabricError(*_Args, **_Kwargs):
        Calls.append("fabric")
        raise ValueError("injected ordinary fabric programming error")

    monkeypatch.setattr(
        PlacementSetup,
        "BuildPlacementAccessFabric",
        RaiseOrdinaryFabricError,
    )

    with pytest.raises(ValueError) as Error:
        PlaceAndRoutePcb(
            _BuildFanoutTwoNandNetlist(),
            Strategy="routing-aware-placement-access",
        )

    assert type(Error.value) is ValueError
    assert Calls == ["fabric"]


def test_disabled_selected_access_preserves_ordinary_fabric_value_error(
    monkeypatch,
) -> None:
    OriginalMaterialize = PlacementSetup.MaterializeRawTemplate
    Materializations = []

    def MaterializeWithDisabledAccess(Context, Descriptor):
        Materializations.append(Descriptor.TemplateId)
        Context.Policy = replace(
            Context.Policy,
            PlacementAccess=replace(
                Context.Policy.PlacementAccess,
                Enabled=False,
            ),
        )
        return OriginalMaterialize(Context, Descriptor)

    def RaiseOrdinaryFabricError(*_Args, **_Kwargs):
        raise ValueError("injected disabled-access fabric programming error")

    monkeypatch.setattr(
        PlacementSetup,
        "MaterializeRawTemplate",
        MaterializeWithDisabledAccess,
    )
    monkeypatch.setattr(
        RoutingAttempts,
        "BuildPlacementAccessFabric",
        RaiseOrdinaryFabricError,
    )

    with pytest.raises(ValueError) as Error:
        PlaceAndRoutePcb(
            _BuildSingleNandNetlist(),
            Strategy="routing-aware-placement-access",
        )

    assert type(Error.value) is ValueError
    assert len(Materializations) == 1


def test_public_fanout_replays_selected_envelope_candidate_values(
    monkeypatch,
) -> None:
    """Rebuild one frozen physical value domain under its declared envelope."""
    CandidateDomains = []
    PlacementSolves = []

    OriginalSolve = PlacementAttempts.SolvePlacedPinAccessOptionDomains

    def ObserveSolve(*Args, **Kwargs):
        Result = OriginalSolve(*Args, **Kwargs)
        PlacementSolves.append(
            PlacementAccessSolveResult.FromDictionary(Result.ToDictionary())
        )
        return Result

    monkeypatch.setattr(
        PlacementAttempts,
        "SolvePlacedPinAccessOptionDomains",
        ObserveSolve,
    )

    OriginalPhaseRunner = RoutingFlow.RunAuthoritativeRoutingPhases

    def ObservePhaseRunner(State, Services, Phases=None):
        ActivePhases = Phases or RoutingFlow.AUTHORITATIVE_ROUTING_PHASES
        ObservedPhases = []
        for Phase in ActivePhases:
            if Phase.__name__ != "RunAssignmentPreparation":
                ObservedPhases.append(Phase)
                continue

            def ObserveAssignment(CurrentState, CurrentServices, Original=Phase):
                OriginalFingerprint = CurrentServices.Dependencies[
                    "BuildTrackAssignmentCandidateDomainFingerprint"
                ]

                def ObserveFingerprint(Resources, Candidates, LocalChoices):
                    Fingerprint = OriginalFingerprint(
                        Resources,
                        Candidates,
                        LocalChoices,
                    )
                    Frozen = CurrentState.FrozenTrackAssignmentPreparation
                    CandidateDomains.append({
                        "CandidateDomainFingerprint": Fingerprint,
                        "CandidateValues": _CanonicalCandidateValues(Candidates),
                        "PlacementGeometryFingerprint": (
                            CurrentServices
                            .BuildRawPortalPlacementGeometryFingerprint(
                                CurrentState.Placed
                            )
                        ),
                        "ResourceGeometryFingerprint": (
                            CurrentServices
                            .BuildRawPortalResourceGeometryFingerprint(Resources)
                        ),
                        "PinAccessDomainFingerprint": (
                            CurrentState.PlacementPinAccessWitness
                            .DomainFingerprint
                        ),
                        "PinAccessWitnessFingerprint": (
                            CurrentState.PlacementPinAccessWitness
                            .WitnessFingerprint
                        ),
                        "FrozenPreparation": Frozen,
                    })
                    return Fingerprint

                CurrentServices.Dependencies[
                    "BuildTrackAssignmentCandidateDomainFingerprint"
                ] = ObserveFingerprint
                try:
                    return Original(CurrentState, CurrentServices)
                finally:
                    CurrentServices.Dependencies[
                        "BuildTrackAssignmentCandidateDomainFingerprint"
                    ] = OriginalFingerprint

            ObservedPhases.append(ObserveAssignment)
        return OriginalPhaseRunner(State, Services, tuple(ObservedPhases))

    monkeypatch.setattr(
        RoutingFlow,
        "RunAuthoritativeRoutingPhases",
        ObservePhaseRunner,
    )

    Result = PlaceAndRoutePcb(
        _BuildFanoutTwoNandNetlist(),
        Strategy="routing-aware-placement-access",
    )

    FrozenDomain = next(
        Value
        for Value in CandidateDomains
        if Value["FrozenPreparation"] is not None
    )
    FrozenPreparation = FrozenDomain["FrozenPreparation"]
    PreparedDomain = next(
        Value
        for Value in CandidateDomains
        if (
            Value is not FrozenDomain
            and Value["CandidateDomainFingerprint"]
            == FrozenPreparation.CandidateDomainFingerprint
        )
    )

    assert FrozenDomain["CandidateValues"] == PreparedDomain["CandidateValues"]
    for Field in (
        "PlacementGeometryFingerprint",
        "ResourceGeometryFingerprint",
        "PinAccessDomainFingerprint",
        "PinAccessWitnessFingerprint",
    ):
        assert FrozenDomain[Field] == PreparedDomain[Field]
    assert all(
        any(
            Value[0] == CandidateId
            for Value in FrozenDomain["CandidateValues"][Signal]
        )
        for Signal, CandidateId in FrozenPreparation.SelectedCandidateIds
    )

    FinalSelectedCandidates = Result.Routed.RoutingAssignment.SelectedCandidates
    assert {
        (str(Signal), str(Candidate.CandidateId))
        for Signal, Candidate in FinalSelectedCandidates.items()
    } == {
        (str(Signal), str(CandidateId))
        for Signal, CandidateId in FrozenPreparation.SelectedCandidateIds
    }
    FrozenClaimsBySelectedCandidate = {
        (str(Signal), str(CandidateId)): Claims
        for Signal, Values in FrozenDomain["CandidateValues"].items()
        for CandidateId, Claims in Values
    }
    for Signal, Candidate in FinalSelectedCandidates.items():
        assert _CanonicalClaims(Candidate.Claims) == (
            FrozenClaimsBySelectedCandidate[
                (str(Signal), str(Candidate.CandidateId))
            ]
        )

    PlacementAccess = Result.PlanningContracts["PlacementAccess"]
    PublishedSolve = PlacementAccessSolveResult.FromDictionary(
        PlacementAccess["SolveResult"]
    )
    PublishedWitness = SelectedPlacementPinAccessWitness.FromDictionary(
        PlacementAccess["SelectedWitness"]
    )
    OriginalPlacementSolve = next(
        Value
        for Value in PlacementSolves
        if (
            Value.SelectedWitness is not None
            and Value.SelectedWitness.WitnessFingerprint
            == PublishedWitness.WitnessFingerprint
        )
    )
    OriginalWitness = OriginalPlacementSolve.SelectedWitness
    assert OriginalPlacementSolve.Status is PlacementAccessSolveStatus.Feasible
    assert OriginalPlacementSolve.SearchComplete
    assert OriginalWitness is not None
    assert PublishedSolve.PolicyVersion == Result.Policy.PolicyVersion
    assert replace(
        OriginalPlacementSolve,
        PolicyVersion=PublishedSolve.PolicyVersion,
    ) == PublishedSolve
    assert PublishedWitness == OriginalWitness

    PlacedGeometry = _PlacedTerminalGeometry(Result.Placed)
    assert {
        Selection.TerminalIdentity()
        for Selection in OriginalWitness.Selections
    } == set(PlacedGeometry)
    Resources = BuildRoutingResources(
        Result.Placed,
        Technology=Result.Technology,
    )
    SelectedNodesBySignal = defaultdict(set)
    ExpectedResourcesBySignal = defaultdict(set)
    for Selection in OriginalWitness.Selections:
        PlacedTerminal, PlacedFace = PlacedGeometry[
            Selection.TerminalIdentity()
        ]
        assert Selection.Terminal == PlacedTerminal
        assert Selection.Face == PlacedFace
        ExpectedPath = tuple(
            tuple(
                PlacedTerminal[Axis] + PlacedFace[Axis] * Offset
                for Axis in range(3)
            )
            for Offset in range(OriginalWitness.AccessLength)
        )
        ExpectedTrackNode = tuple(
            ExpectedPath[-1][Axis] + PlacedFace[Axis]
            for Axis in range(3)
        )
        ExpectedClaims = Resources.ResourceGraph.BuildRouteClaims(ExpectedPath)
        assert Selection.FirstLegNodes == ExpectedPath
        assert Selection.FirstTrackNode == ExpectedTrackNode
        assert Selection.Claims == ExpectedClaims
        assert set(ExpectedPath) <= set(
            Result.Routed.NetWires[Selection.Signal]
        )
        SelectedNodesBySignal[Selection.Signal].update(ExpectedPath)
        ExpectedResourcesBySignal[Selection.Signal].update(
            Resource
            for Resource in ExpectedClaims.ResourceIds
            if Resource.Kind is not RoutingResourceKind.Electrical
        )
        RepeaterRoles = tuple(
            Position
            for Position, Role in Selection.BlockRoles
            if Role == "repeater"
        )
        assert len(RepeaterRoles) == 1
        assert len(Selection.RepeaterReservations) == 1
        Reservation = Selection.RepeaterReservations[0]
        assert Reservation.Position == RepeaterRoles[0]
        assert Reservation.Position in Result.Routed.NetWires[
            Reservation.Signal
        ]
        assert Result.Routed.RepeaterInputFacings[
            Reservation.Position
        ] == Reservation.InputFacing
        ExpectedOutputDirection = (
            PlacedFace
            if Selection.Role == "Source"
            else tuple(-Value for Value in PlacedFace)
        )
        assert RepeaterOutputDelta(
            Reservation.InputFacing
        ) == ExpectedOutputDirection

    for Signal, ExpectedResources in ExpectedResourcesBySignal.items():
        for Resource in ExpectedResources:
            assert Result.Routed.TrackAssignment.ResourceOwners[Resource] == (
                Signal,
            )

    FinalClaimsBySignal = {
        Signal: Resources.ResourceGraph.BuildRouteClaims(Positions)
        for Signal, Positions in Result.Routed.NetWires.items()
    }
    assert Result.Routed.ZeroResourceConflicts
    assert FindClaimConflicts(FinalClaimsBySignal) == {}

    RawSelectedSignals = {
        Signal
        for Signal, _CandidateId
        in FrozenPreparation.SelectedCandidateIds
    }
    SignalsWithDetailedGeometry = {
        Signal
        for Signal in RawSelectedSignals
        if (
            set(Result.Routed.NetWires[Signal])
            - SelectedNodesBySignal[Signal]
        )
    }
    assert SignalsWithDetailedGeometry
    for Signal in RawSelectedSignals:
        Nodes = set(Result.Routed.NetWires[Signal])
        Selections = tuple(
            Selection
            for Selection in OriginalWitness.Selections
            if Selection.Signal == Signal
        )
        Sources = tuple(
            Selection.FirstLegNodes[-1]
            for Selection in Selections
            if Selection.Role == "Source"
        )
        Targets = tuple(
            Selection.FirstLegNodes[-1]
            for Selection in Selections
            if Selection.Role == "Target"
        )
        assert Sources and Targets
        assert all(
            any(_Connected(Nodes, Source, Target) for Source in Sources)
            for Target in Targets
        )

    Observations = PlacementAccess["HandoffEvidence"]["Observations"]
    assert tuple(
        Observation["Stage"] for Observation in Observations
    ) == PlacementPinAccessStages
    for Observation in Observations:
        assert Observation["WitnessFingerprint"] == (
            OriginalWitness.WitnessFingerprint
        )
        assert Observation["DomainFingerprint"] == (
            OriginalWitness.DomainFingerprint
        )
        assert Observation["AccessRegenerationCount"] == 0
        assert Observation["UnselectedPortalLeakCount"] == 0
        assert Observation["CompactionPreserved"] is (
            True if Observation["Stage"] == "Compaction" else None
        )

    CurrentEnvelopeResult = Result.PlanningContracts[
        "CurrentSelectedAccessEnvelope"
    ]
    assert CurrentEnvelopeResult["Status"] == "Ready"
    assert CurrentEnvelopeResult["Reason"] == "Current"
    assert CurrentEnvelopeResult["HistoricalOnly"] is True
    CurrentEnvelope = CurrentEnvelopeResult["Envelope"]
    assert CurrentEnvelope["Phase"] == "BeforePublication"
    assert CurrentEnvelope["PhysicalValidation"]["Status"] == "Verified"
    assert CurrentEnvelope["PhysicalValidation"]["Reason"] == "Current"
    assert CurrentEnvelope["Candidate"][
        "PlacementFingerprintIncludesLocalClaims"
    ] is False
    assert CurrentEnvelope["SolveResult"] == PublishedSolve.ToDictionary()
    assert CurrentEnvelope["SelectedWitness"] == PublishedWitness.ToDictionary()
    assert CurrentEnvelope["Domains"] == PublishedSolve.ToDictionary()["Domains"]
    assert CurrentEnvelope["SolveBinding"]["ProblemFingerprint"] == (
        PublishedSolve.ProblemFingerprint
    )
    assert CurrentEnvelope["SolveBinding"]["WitnessFingerprint"] == (
        PublishedWitness.WitnessFingerprint
    )
    assert CurrentEnvelope["SolveBinding"]["Policy"]["PolicyFingerprint"] == (
        CurrentEnvelope["Policy"]["PolicyFingerprint"]
    )
    assert CurrentEnvelope["Policy"]["PlacementAccessEnabled"] is True
    assert CurrentEnvelope["Policy"]["CatalogVersion"] == (
        Result.Policy.PlacementAccess.CatalogVersion
    )
    assert CurrentEnvelope["Policy"]["MaximumDomainGenerationWork"] == (
        Result.Policy.PlacementAccess.MaximumDomainGenerationWork
    )
    assert CurrentEnvelope["Policy"]["MaximumAssignmentExpansions"] == (
        Result.Policy.PlacementAccess.MaximumAssignmentExpansions
    )
    assert CurrentEnvelope["Routing"]["InclusiveRoutingBoundsXZ"] == (
        CurrentEnvelope["Routing"]["RoutingEnvelope"]["EnvelopeBounds"]
    )
    assert CurrentEnvelope["Routing"]["TrackPreparation"]["Complete"] is True
    assert CurrentEnvelope["Routing"]["TrackPreparation"][
        "PinAccessDomainFingerprint"
    ] == PublishedWitness.DomainFingerprint
    assert CurrentEnvelope["Routing"]["TrackPreparation"][
        "PinAccessWitnessFingerprint"
    ] == PublishedWitness.WitnessFingerprint
    assert CurrentEnvelope["Routing"]["RawTrackAssignmentApplicable"] is False
    assert CurrentEnvelope["Routing"]["RawTrackAssignment"] is None
    assert "RawTrackAssignment" in CurrentEnvelope["Routing"]["OutOfScopeFields"]
    EnvelopePhases = tuple(
        Observation["Phase"]
        for Observation in CurrentEnvelopeResult["Observations"]
    )
    assert EnvelopePhases == (
        "BeforeRawMaterialization",
        "SelectedTrackSuccessor",
        "BeforePublication",
    )
    EnvelopeObservations = CurrentEnvelopeResult["Observations"]
    assert EnvelopeObservations[0]["PredecessorEnvelopeFingerprint"] == ""
    assert EnvelopeObservations[1]["PredecessorEnvelopeFingerprint"]
    assert EnvelopeObservations[2]["PredecessorEnvelopeFingerprint"] == (
        CurrentEnvelope["PredecessorEnvelopeFingerprint"]
    )
    assert len({
        EnvelopeObservations[1]["PredecessorEnvelopeFingerprint"],
        EnvelopeObservations[2]["PredecessorEnvelopeFingerprint"],
        CurrentEnvelope["EnvelopeFingerprint"],
    }) == 3


def test_public_multicluster_flow_publishes_initial_before_selected_track() -> None:
    Stages = []
    Result = PlaceAndRoutePcb(
        _BuildSeventeenNandChainNetlist(),
        Strategy="routing-aware-placement-access",
        StageCallback=Stages.append,
    )

    assert "physical component interface planning" in Stages
    assert "PlacementAccess" not in Result.PlanningContracts
    CurrentResult = Result.PlanningContracts["CurrentSelectedAccessEnvelope"]
    assert CurrentResult["Status"] == "Ready"
    assert CurrentResult["Reason"] == "Current"
    assert tuple(
        Observation["Transition"]
        for Observation in CurrentResult["Observations"]
    ) == (
        "InitialCandidate",
        "SelectedTrackAssignment",
        "PostRoutingCompaction",
    )


def test_public_forced_multicluster_flow_rebuilds_access_after_channel_deck(
    monkeypatch,
) -> None:
    """Emit only a freshly rebuilt selected-access channel successor."""
    Stages = []
    ChannelObservations = []
    DeckObservations = []
    ChannelEnvelopeRecords = []
    OriginalChannel = PhysicalFlow.BuildBoundedInterClusterRoutingChannel
    OriginalDeck = PhysicalFlow.BuildBoundedInterClusterRoutingDeck
    OriginalEnvelopeBuilder = (
        PhysicalFlow.BuildCandidateCurrentSelectedAccessEnvelope
    )

    def GateOrigins(Placement):
        return tuple(sorted(
            (
                GateValue.Name,
                GateValue.X,
                GateValue.Y,
                GateValue.Z,
            )
            for GateValue in Placement.Placed.PlacedGates
        ))

    def ObserveChannel(Placement, **Options):
        Result = OriginalChannel(Placement, **Options)
        ChannelObservations.append({
            "SourceOrigins": GateOrigins(Placement),
            "ChannelOrigins": GateOrigins(Result),
            "ChannelFingerprint": (
                Result.InterClusterRoutingChannel.ChannelFingerprint
            ),
        })
        return Result

    def ObserveDeck(Placement, **Options):
        Result = OriginalDeck(Placement, **Options)
        if Placement.InterClusterRoutingChannel is None:
            return Result
        DeckObservations.append({
            "ChannelOrigins": GateOrigins(Placement),
            "DeckOrigins": GateOrigins(Result),
            "ChannelCompleteAccess": (
                Placement.CompleteClusterInterfaceAccess
            ),
            "DeckCompleteAccess": Result.CompleteClusterInterfaceAccess,
            "ChannelLeaseRequests": tuple(
                Value.ToDictionary()
                for Value in Placement.ClusterBoundaryLeaseRequests
            ),
            "DeckLeaseRequests": tuple(
                Value.ToDictionary()
                for Value in Result.ClusterBoundaryLeaseRequests
            ),
            "InputChannelFingerprint": (
                Placement.InterClusterRoutingChannel.ChannelFingerprint
            ),
            "DeckChannelFingerprint": (
                Result.InterClusterRoutingChannel.ChannelFingerprint
            ),
        })
        return Result

    def ObserveEnvelope(Candidate, **Options):
        Result = OriginalEnvelopeBuilder(Candidate, **Options)
        if Options["Transition"].value == "ChannelReplacement":
            ChannelEnvelopeRecords.append((
                Candidate.Placement.PlacementAccessSolve,
                Result,
            ))
        return Result

    monkeypatch.setattr(
        PhysicalFlow,
        "BuildBoundedInterClusterRoutingChannel",
        ObserveChannel,
    )
    monkeypatch.setattr(
        PhysicalFlow,
        "BuildBoundedInterClusterRoutingDeck",
        ObserveDeck,
    )
    monkeypatch.setattr(
        PhysicalFlow,
        "BuildCandidateCurrentSelectedAccessEnvelope",
        ObserveEnvelope,
    )
    monkeypatch.setattr(
        PlacementSetup,
        "RequiresExactClusterInterfaceSolve",
        lambda *_Args, **_Options: (
            "physical component interface planning" not in Stages
        ),
    )
    monkeypatch.setattr(
        RoutingAttempts,
        "RequiresExactClusterInterfaceSolve",
        lambda *_Args, **_Options: (
            "physical component interface planning" not in Stages
        ),
    )

    with pytest.raises(RoutingStageError) as Error:
        PlaceAndRoutePcb(
            _BuildSeventeenNandChainNetlist(),
            Strategy="routing-aware-placement-access",
            StageCallback=Stages.append,
        )

    assert "physical component interface planning" in Stages
    assert ChannelObservations
    assert DeckObservations
    assert any(
        Value["SourceOrigins"] != Value["ChannelOrigins"]
        for Value in ChannelObservations
    )
    assert any(
        Value["ChannelCompleteAccess"] != Value["DeckCompleteAccess"]
        or Value["ChannelLeaseRequests"] != Value["DeckLeaseRequests"]
        for Value in DeckObservations
    )
    assert {
        Value["ChannelFingerprint"] for Value in ChannelObservations
    }.intersection(
        Value["InputChannelFingerprint"] for Value in DeckObservations
    )
    Ready = tuple(
        Result
        for Solve, Result in ChannelEnvelopeRecords
        if Solve is not None
        and Solve.Status is PlacementAccessSolveStatus.Feasible
        if Result.Status.value == "Ready"
    )
    assert Ready
    Envelope = Ready[0].Envelope
    assert Envelope is not None
    assert Envelope.Phase.value == "SelectedTrackSuccessor"
    assert Envelope.Transition.value == "ChannelReplacement"
    assert Envelope.PhysicalValidation.Status.value == "Verified"
    assert Envelope.PhysicalValidation.Reason.value == "Current"
    assert Envelope.Routing.TrackPreparation is not None
    assert Envelope.Routing.TrackPreparation.Success
    assert Envelope.Routing.TrackPreparation.Complete
    assert Envelope.Routing.TrackPreparation.PinAccessDomainFingerprint == (
        Envelope.SelectedWitness.DomainFingerprint
    )
    assert Envelope.Routing.TrackPreparation.PinAccessWitnessFingerprint == (
        Envelope.SelectedWitness.WitnessFingerprint
    )
    assert tuple(
        Observation.Transition.value for Observation in Ready[0].Observations
    ) == ("InitialCandidate", "ChannelReplacement")
    assert Envelope.PredecessorEnvelopeFingerprint == (
        Ready[0].Observations[-1].PredecessorEnvelopeFingerprint
    )
    Unsatisfiable = tuple(
        Result
        for Solve, Result in ChannelEnvelopeRecords
        if Solve is not None
        and Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
        and Solve.SearchComplete
    )
    assert Unsatisfiable
    assert all(
        Result.Status.value == "Unsatisfiable"
        and Result.Reason.value == "AccessSolveUnsatisfiable"
        and Result.Envelope is None
        and Result.Observations[-1].PhysicalValidation is not None
        and Result.Observations[-1].PhysicalValidation.Status.value
        == "Unresolved"
        and Result.Observations[-1].PhysicalValidation.Reason.value
        == "UnsatisfiableSolve"
        for Result in Unsatisfiable
    )
    assert Error.value.Failure.Reason is (
        RoutingFailureReason.ClusterInterfaceSolveIncomplete
    )
    assert Error.value.Failure.Diagnostics[
        "CurrentSelectedAccessStatus"
    ] == "Unsatisfiable"
    assert Error.value.Failure.Diagnostics[
        "CurrentSelectedAccessReason"
    ] == "AccessSolveUnsatisfiable"
    assert Error.value.Failure.Diagnostics["InterfaceSolve"]["Complete"] is False
    assert all(
        Result.Reason.value != "MissingReadyPredecessor"
        for _Solve, Result in ChannelEnvelopeRecords
    )

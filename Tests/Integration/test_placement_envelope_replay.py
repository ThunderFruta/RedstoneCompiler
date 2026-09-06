"""Public immutable-envelope replay coverage for v17 placement access."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
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
import PhysicalDesign.Routing.Global.Orchestration.Flow as RoutingFlow


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

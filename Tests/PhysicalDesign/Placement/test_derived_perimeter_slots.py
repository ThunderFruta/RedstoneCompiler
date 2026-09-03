"""Focused contracts for the frozen derived terminal perimeter domain."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from Compiler.Frontend import Sv

from PhysicalDesign.Placement.Core.Commit.Commit import PlacePcbGraph
from PhysicalDesign.Placement.PreRouteInterface import SolveDerivedPerimeterSlotDomain
from PhysicalDesign.Policy import LocalFirstPhysicalDesignPolicy
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly


def BuildFullAdderNetlist():
    with TemporaryDirectory() as Directory:
        return ToNandOnly(OptimizeLogic(Sv.ParseSvToNetlist(
            InputPath=Path("Examples/FullAdder.sv"),
            TopModule="FullAdder",
            Workdir=Path(Directory),
        )))


def BuildDerivedFullAdderPlacement(
    TerminalLayoutVariantIndex: int = 0,
):
    return PlacePcbGraph(
        BuildFullAdderNetlist(),
        RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
        PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
        PackingPolicy=replace(
            LocalFirstPhysicalDesignPolicy.NandPacking,
            GraphBeamEnabled=False,
        ),
        PreferAccessRingTerminals=True,
        UseDerivedPerimeterTerminals=True,
        DerivedTerminalLayoutVariantIndex=TerminalLayoutVariantIndex,
    )


def test_derived_perimeter_slots_are_outward_and_frozen_on_placement():
    First = BuildDerivedFullAdderPlacement()
    Second = BuildDerivedFullAdderPlacement(TerminalLayoutVariantIndex=1)

    Assignment = First.DerivedPerimeterSlotAssignment
    Domain = First.DerivedPerimeterSlotDomain
    assert Assignment is not None
    assert Domain is not None
    assert Assignment.Success is True
    assert Assignment.Complete is True
    assert Domain.Complete is True
    assert Assignment.AssignmentFingerprint
    assert Assignment is First.Placed.DerivedPerimeterSlotAssignment
    assert Domain is First.Placed.DerivedPerimeterSlotDomain

    ExpectedDirections = {
        "north": (0, 0, -1),
        "south": (0, 0, 1),
        "west": (-1, 0, 0),
        "east": (1, 0, 0),
    }
    PlacedByName = {
        Gate.Name: Gate for Gate in First.Placed.PlacedGates
    }
    for Slot in Assignment.SelectedSlots:
        assert Slot.ConnectionDirection == ExpectedDirections[Slot.Face]
        Gate = PlacedByName[Slot.TerminalName]
        assert (Gate.X, Gate.Y, Gate.Z) == Slot.Origin
        assert Gate.Rotation == Slot.Rotation
        assert Gate.MirrorX == Slot.MirrorX
    assert all(
        Reservation.SlotIds
        for Reservation in Assignment.FaceReservations
    )

    # The historical numbered terminal-layout portfolio is not a second
    # derived attempt: a nonzero compatibility index selects the same frozen
    # perimeter-domain member.
    assert Second.DerivedPerimeterSlotAssignment is not None
    assert Assignment.AssignmentFingerprint == (
        Second.DerivedPerimeterSlotAssignment.AssignmentFingerprint
    )


def test_derived_perimeter_slot_work_cap_is_terminal_incomplete():
    Placement = BuildDerivedFullAdderPlacement()
    Domain = Placement.DerivedPerimeterSlotDomain
    assert Domain is not None

    Result = SolveDerivedPerimeterSlotDomain(
        Domain,
        MaximumExpansions=1,
    )

    assert Result.Success is False
    assert Result.Complete is False
    assert Result.IncompleteReason == "work-cap"
    assert Result.AssignmentFingerprint == ""

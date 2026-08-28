from dataclasses import FrozenInstanceError, fields, replace
from types import SimpleNamespace

import pytest

from Compiler.Routing.Components.PhysicalPlanning import (
    BuildPhysicalAssemblyGlobalReuseFingerprint,
    SelectPhysicalAssemblyGlobalBoundaryPorts,
)
from Compiler.Routing.Contracts.Component import (
    PhysicalComponentAssemblyPlan,
    PhysicalComponentBoundaryPortReservation,
    PhysicalComponentSelectedLocalPortSupport,
)
from Compiler.Routing.Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain


def BoundaryPort(**Overrides):
    Values = {
        "Signal": "NandNet3",
        "Direction": "output",
        "Attachment": (4, 7, 2),
        "GlobalPath": ((4, 7, 2), (5, 7, 2)),
        "GlobalClaims": object(),
        "Capacity": 1,
        "ChannelContractFingerprint": "channel",
        "GlobalContractFingerprint": "global",
        "ApertureContractFingerprint": "aperture",
        "ReservationFingerprint": "reservation",
    }
    Values.update(Overrides)
    return PhysicalComponentBoundaryPortReservation(**Values)


def test_boundary_port_reservation_is_global_only_and_immutable():
    Reservation = BoundaryPort()

    FieldNames = {Value.name for Value in fields(Reservation)}
    assert "LocalPath" not in FieldNames
    assert "FabricAttachment" not in FieldNames
    assert "OwnedAccessCandidates" not in FieldNames
    assert Reservation.GlobalPath[0] == Reservation.Attachment
    with pytest.raises(FrozenInstanceError):
        Reservation.Capacity = 2


def test_boundary_port_reservation_validates_capacity_and_attachment():
    with pytest.raises(ValueError, match="capacity must be positive"):
        BoundaryPort(Capacity=0)
    with pytest.raises(ValueError, match="must start at its attachment"):
        BoundaryPort(GlobalPath=((5, 7, 2),))


def test_selected_local_support_is_an_identity_only_reference():
    Support = PhysicalComponentSelectedLocalPortSupport(
        Signal="NandNet3",
        BoundaryReservationFingerprint="reservation",
        LocalContractFingerprint="local-contract",
        LocalAccessFingerprint="local-access",
        SupportFingerprint="support",
    )

    assert {Value.name for Value in fields(Support)} == {
        "Signal",
        "BoundaryReservationFingerprint",
        "LocalContractFingerprint",
        "LocalAccessFingerprint",
        "SupportFingerprint",
    }
    with pytest.raises(ValueError, match="identities must be nonempty"):
        PhysicalComponentSelectedLocalPortSupport(
            Signal="NandNet3",
            BoundaryReservationFingerprint="reservation",
            LocalContractFingerprint="",
            LocalAccessFingerprint="local-access",
            SupportFingerprint="support",
        )


def test_assembly_plan_additively_serializes_port_first_contracts():
    Boundary = BoundaryPort()
    Support = PhysicalComponentSelectedLocalPortSupport(
        Signal="NandNet3",
        BoundaryReservationFingerprint="reservation",
        LocalContractFingerprint="local-contract",
        LocalAccessFingerprint="local-access",
        SupportFingerprint="support",
    )
    Plan = PhysicalComponentAssemblyPlan(
        PlanFingerprint="plan",
        PortAssignmentFingerprint="ports",
        PlacementFingerprint="placement",
        ComponentGraphFingerprint="component-graph",
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        InterfaceFingerprint="interface",
        ComponentId=1,
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(10, 10, 10),
        KeepoutClaims=object(),
        Ports=(),
        Channels=(),
        GlobalBoundaryPorts=(Boundary,),
        SelectedLocalPortSupports=(Support,),
    )

    Serialized = Plan.ToDictionary()
    assert Serialized["SchemaVersion"] == (
        "physical-component-assembly-plan-v1"
    )
    assert Serialized["GlobalBoundaryPorts"][0]["Signal"] == "NandNet3"
    assert Serialized["SelectedLocalPortSupports"][0][
        "BoundaryReservationFingerprint"
    ] == "reservation"
    assert Serialized["ExteriorFabricSetFingerprint"] == ""
    assert Serialized["ExteriorRegionFingerprint"] == ""
    assert Serialized["ExteriorCapacityLedgerFingerprint"] == ""
    PreparedField = PreparedPhysicalComponentPortFactorDomain.__dataclass_fields__[
        "BoundaryPortReservationsBySignal"
    ]
    assert PreparedField.default == ()
    for FieldName in (
        "ExteriorFabricSetFingerprint",
        "ExteriorRegionFingerprint",
        "ExteriorCapacityLedgerFingerprint",
    ):
        assert (
            PreparedPhysicalComponentPortFactorDomain.__dataclass_fields__[
                FieldName
            ].default
            == ""
        )


def test_global_planning_identity_ignores_transitional_local_port_state():
    Boundary = BoundaryPort()
    Composite = SimpleNamespace(
        Signal="NandNet3",
        Direction="output",
        Attachment=(4, 7, 2),
        GlobalPath=((4, 7, 2), (99, 7, 2)),
        Capacity=1,
        LocalPath=((0, 7, 2), (4, 7, 2)),
    )
    Plan = PhysicalComponentAssemblyPlan(
        PlanFingerprint="plan",
        PortAssignmentFingerprint="ports",
        PlacementFingerprint="placement",
        ComponentGraphFingerprint="component-graph",
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        InterfaceFingerprint="interface",
        ComponentId=1,
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(10, 10, 10),
        KeepoutClaims=object(),
        Ports=(Composite,),
        Channels=(),
        GlobalBoundaryPorts=(Boundary,),
    )

    assert SelectPhysicalAssemblyGlobalBoundaryPorts(Plan) == (Boundary,)
    Baseline = BuildPhysicalAssemblyGlobalReuseFingerprint(Plan)
    ChangedComposite = SimpleNamespace(**vars(Composite))
    ChangedComposite.LocalPath = ((1, 7, 2), (4, 7, 2))
    ChangedComposite.GlobalPath = ((4, 7, 2), (88, 7, 2))
    ChangedLocalState = replace(Plan, Ports=(ChangedComposite,))
    assert BuildPhysicalAssemblyGlobalReuseFingerprint(
        ChangedLocalState
    ) == Baseline

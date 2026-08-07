from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile

import pytest

from Compiler.Placement.Geometry import PlacedDesign
from Compiler.Placement.AccessFabric import (
    AttachPlacementAccessFabric,
    BuildPlacementAccessFabric,
    SolvePlacementAccessFabricCapacity,
    _BuildShortestFabricEscapePaths,
)
from Compiler.Placement.Pcb import PcbPlacement, PlacePcbGraph
from Compiler.Placement.PcbFlow import (
    BuildPlacementGenerationPlan,
    SelectFixedPrimaryPlacementRequests,
)
from Compiler.Routing.Policy import LocalFirstPhysicalDesignPolicy
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly
from SVDecoder import Sv
from Compiler.Routing.Models import (
    PlacementAccessEscapeStub,
    PlacementAccessFabric,
    PlacementAccessTerminalDomain,
)
from Compiler.Routing.ResourceGraph import (
    RoutingResourceClaims,
    RoutingResourceId,
    RoutingResourceKind,
)


def BuildTestFabric() -> PlacementAccessFabric:
    Wire = RoutingResourceId(RoutingResourceKind.Wire, (1, 4, 1))
    Support = RoutingResourceId(RoutingResourceKind.Support, (1, 3, 1))
    Claims = RoutingResourceClaims(
        WireCells=frozenset({(1, 4, 1)}),
        SupportCells=frozenset({(1, 3, 1)}),
        RequiredAirCells=frozenset({(1, 5, 1)}),
        ElectricalCells=frozenset({(0, 4, 1), (2, 4, 1)}),
    )
    Stub = PlacementAccessEscapeStub(
        Terminal=(1, 1, 1),
        Ingress=(1, 4, 1),
        Path=((1, 1, 1), (1, 2, 1), (1, 4, 1)),
        PhysicalClaims=Claims,
        CapacityResourceIds=(Wire, Support),
        Complete=True,
    )
    Domain = PlacementAccessTerminalDomain(
        Signal="Signal",
        Terminal=Stub.Terminal,
        EscapeStubs=(Stub,),
        Complete=True,
    )
    return PlacementAccessFabric(
        FabricFingerprint="fabric-fingerprint",
        Nodes=((1, 4, 1), (2, 4, 1)),
        Edges=(((1, 4, 1), (2, 4, 1)),),
        IngressNodes=((1, 4, 1),),
        PhysicalClaims=Claims,
        CapacityResourceIds=(Wire, Support),
        TerminalDomains=(Domain,),
        TopologyKind="fixed-access-band-v1",
        Complete=True,
    )


def test_placement_access_fabric_serializes_physical_capacity_contract():
    Serialized = BuildTestFabric().ToDictionary()

    assert Serialized["FabricFingerprint"] == "fabric-fingerprint"
    assert Serialized["NodeCount"] == 2
    assert Serialized["EdgeCount"] == 1
    assert Serialized["PhysicalClaims"] == {
        "WireCells": 1,
        "SupportCells": 1,
        "RequiredAirCells": 1,
        "ElectricalCells": 2,
    }
    assert Serialized["CapacityResourceIds"] == [
        "Wire:1,4,1",
        "Support:1,3,1",
    ]
    assert Serialized["TerminalDomains"][0]["Complete"] is True
    assert Serialized["TerminalDomains"][0]["EscapeStubs"][0][
        "CapacityResourceIds"
    ] == ["Wire:1,4,1", "Support:1,3,1"]


def test_placement_access_fabric_is_immutable():
    Fabric = BuildTestFabric()

    with pytest.raises(FrozenInstanceError):
        Fabric.Complete = False


def test_incomplete_terminal_domain_retains_typed_reason():
    Domain = PlacementAccessTerminalDomain(
        Signal="Signal",
        Terminal=(0, 1, 0),
        EscapeStubs=(),
        Complete=False,
        IncompleteReason="work-cap-exhausted",
    )

    assert Domain.ToDictionary()["IncompleteReason"] == "work-cap-exhausted"
    assert Domain.ToDictionary()["Complete"] is False


def test_placement_models_leave_access_fabric_inactive_by_default():
    Placed = PlacedDesign(Module=None, PlacedGates=[])
    Placement = PcbPlacement(
        Placed=Placed,
        Clusters=(),
        SignalOrder=(),
        LayerCount=3,
    )

    assert Placed.PlacementAccessFabric is None
    assert Placement.PlacementAccessFabric is None


def test_escape_paths_are_deterministic_and_bounded():
    Paths = _BuildShortestFabricEscapePaths(
        Starts=((0, 0, 0),),
        IngressNodes=frozenset({(2, 0, 0), (0, 0, 2)}),
        Edges=(
            ((0, 0, 0), (1, 0, 0)),
            ((1, 0, 0), (2, 0, 0)),
            ((0, 0, 0), (0, 0, 1)),
            ((0, 0, 1), (0, 0, 2)),
        ),
        MaximumPaths=1,
    )

    assert Paths == (((0, 0, 0), (0, 0, 1), (0, 0, 2)),)


def test_attach_access_fabric_publishes_same_immutable_identity():
    Placement = PcbPlacement(
        Placed=PlacedDesign(Module=None, PlacedGates=[]),
        Clusters=(),
        SignalOrder=(),
        LayerCount=3,
    )
    Fabric = BuildTestFabric()

    Attached = AttachPlacementAccessFabric(Placement, Fabric)

    assert Attached.PlacementAccessFabric is Fabric
    assert Attached.Placed.PlacementAccessFabric is Fabric
    assert Placement.PlacementAccessFabric is None


def test_capacity_work_cap_is_typed_incomplete_not_unsatisfiable():
    Assignment = SolvePlacementAccessFabricCapacity(
        BuildTestFabric(),
        MaximumExpansions=1,
    )

    assert Assignment.Success is False
    assert Assignment.Complete is False
    assert Assignment.IncompleteReason == "work-cap-exhausted"


def test_scale_primary_domain_is_fixed_before_routing():
    Plan = BuildPlacementGenerationPlan(
        LocalFirstPhysicalDesignPolicy,
        PreferPackedPlacements=True,
    )

    Small = SelectFixedPrimaryPlacementRequests(Plan, 32)
    Scale = SelectFixedPrimaryPlacementRequests(Plan, 36)

    assert [Value.SourceGenerator for Value in Small] == [
        "row-beam",
    ]
    assert [Value.SourceGenerator for Value in Scale] == [
        "row-beam",
        "row-beam-direct-only",
    ]
    assert all(
        "conflict-relocation" not in Value.SourceGenerator
        for Value in Scale
    )


def test_full_adder_access_fabric_is_complete_and_deterministic():
    with tempfile.TemporaryDirectory() as Directory:
        Netlist = ToNandOnly(OptimizeLogic(Sv.ParseSvToNetlist(
            InputPath=Path("Examples/FullAdder.sv"),
            TopModule="FullAdder",
            Workdir=Path(Directory),
        )))
    Placement = PlacePcbGraph(
        Netlist,
        RoutingSpacing=(
            LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing
        ),
        PlacementPolicy=replace(
            LocalFirstPhysicalDesignPolicy.Placement,
            EnableDemandAwareInterClusterSpacing=True,
        ),
        PackingPolicy=replace(
            LocalFirstPhysicalDesignPolicy.NandPacking,
            GraphBeamEnabled=False,
        ),
    )

    First = BuildPlacementAccessFabric(Placement)
    Second = BuildPlacementAccessFabric(Placement)

    assert First.Complete is True
    assert First.FabricFingerprint == Second.FabricFingerprint
    assert First.Nodes == Second.Nodes
    assert First.Edges == Second.Edges
    assert len(First.TerminalDomains) > 1
    assert all(Domain.Complete for Domain in First.TerminalDomains)
    assert all(
        Stub.CapacityResourceIds
        for Domain in First.TerminalDomains
        for Stub in Domain.EscapeStubs
    )
    FirstAssignment = SolvePlacementAccessFabricCapacity(First)
    SecondAssignment = SolvePlacementAccessFabricCapacity(Second)
    assert FirstAssignment.Success is True
    assert FirstAssignment.Complete is True
    assert FirstAssignment.AssignmentFingerprint == (
        SecondAssignment.AssignmentFingerprint
    )
    assert FirstAssignment.SelectedStubIndices == (
        SecondAssignment.SelectedStubIndices
    )
    assert FirstAssignment.SignalRoutes == SecondAssignment.SignalRoutes
    assert {Signal for Signal, _Nodes in FirstAssignment.SignalRoutes} == {
        Domain.Signal for Domain in First.TerminalDomains
    }

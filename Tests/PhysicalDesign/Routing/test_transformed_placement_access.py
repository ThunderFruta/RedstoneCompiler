"""Production-bound regression coverage for transformed selected access."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from Compilation.Ir.Models import Gate, GateKind
from PhysicalDesign.Contracts.Failures import (
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Contracts.PlacementAccess import PlacementAccessSolveStatus
from PhysicalDesign.Geometry.Placement import BuildPlacedGate, PlacedDesign
from PhysicalDesign.Orchestration.PhysicalFlow import (
    _RebuildTransformedPlacementSelectedAccess,
)
from PhysicalDesign.Orchestration.Feedback import BuildPlacementFingerprint
from PhysicalDesign.Placement.Access.Capacity import (
    SolvePlacedPinAccessOptionDomains,
)
from PhysicalDesign.Placement.Access.Catalog import (
    EnumeratePlacedPinAccessOptionDomains,
)
from PhysicalDesign.Placement.Engine.Clusters import PcbPlacement
from PhysicalDesign.Policy import RoutingAwarePlacementAccessPhysicalDesignPolicy
from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Runtime.Reliability import RoutingDeadline


def _PlacedAt(TargetX: int) -> PlacedDesign:
    Gates = (
        BuildPlacedGate(
            Gate("Source", GateKind.INPUT, ["A"], []), 0, 1, 0, 0, False,
        ),
        BuildPlacedGate(
            Gate("Target", GateKind.OUTPUT, [], ["A"]), TargetX, 1, 10,
            0, False,
        ),
    )
    return PlacedDesign(Module=None, PlacedGates=list(Gates))


def _Solve(Placed: PlacedDesign):
    Resources = BuildRoutingResources(
        Placed,
        Technology=DefaultRedstoneRoutingTechnology,
    )
    Domains = EnumeratePlacedPinAccessOptionDomains(
        Placed.PlacedGates,
        ResourceGraph=Resources.ResourceGraph,
        Technology=DefaultRedstoneRoutingTechnology,
        EnabledPatternFamilies=("straight",),
        MaximumGenerationWork=100_000,
    )
    return SolvePlacedPinAccessOptionDomains(
        Domains,
        ResourceGraph=Resources.ResourceGraph,
        MaximumExpansions=100_000,
    )


def _Successor(TargetX: int = 22) -> PcbPlacement:
    return PcbPlacement(
        Placed=_PlacedAt(TargetX),
        Clusters=(("Source",), ("Target",)),
        SignalOrder=("A",),
        LayerCount=3,
    )


def _Context(Policy):
    return SimpleNamespace(
        Policy=Policy,
        Technology=DefaultRedstoneRoutingTechnology,
        InterfaceDeadline=RoutingDeadline.Start(30.0),
        Services=SimpleNamespace(BuildRoutingResources=BuildRoutingResources),
    )


def test_terminal_moving_successor_rebuilds_current_access_under_policy():
    Predecessor = _PlacedAt(10)
    PredecessorSolve = _Solve(Predecessor)
    assert PredecessorSolve.Status is PlacementAccessSolveStatus.Feasible
    assert PredecessorSolve.SelectedWitness is not None

    Policy = replace(
        RoutingAwarePlacementAccessPhysicalDesignPolicy,
        PolicyVersion="transformed-access-regression-policy",
    )
    Successor, Resources = _RebuildTransformedPlacementSelectedAccess(
        _Context(Policy),
        _Successor(),
    )

    assert Resources is not None
    assert Successor.PlacementAccessSolve is not None
    assert Successor.PlacementAccessSolve.Status is PlacementAccessSolveStatus.Feasible
    assert Successor.PlacementAccessSolve.PolicyVersion == Policy.PolicyVersion
    assert Successor.SelectedPinAccessWitness is not None
    assert (
        Successor.SelectedPinAccessWitness.WitnessFingerprint
        != PredecessorSolve.SelectedWitness.WitnessFingerprint
    )
    assert Successor.Placed.SelectedPinAccessWitness is (
        Successor.SelectedPinAccessWitness
    )
    assert Successor.Placed.PlacementAccessSolve is Successor.PlacementAccessSolve
    ClearedSuccessor = replace(
        Successor,
        Placed=replace(
            Successor.Placed,
            SelectedPinAccessWitness=None,
            PlacementAccessSolve=None,
        ),
        SelectedPinAccessWitness=None,
        PlacementAccessSolve=None,
    )
    assert BuildPlacementFingerprint(Successor) != BuildPlacementFingerprint(
        ClearedSuccessor,
    )


def test_incomplete_current_resolve_cannot_admit_transformed_successor():
    Policy = replace(
        RoutingAwarePlacementAccessPhysicalDesignPolicy,
        PlacementAccess=replace(
            RoutingAwarePlacementAccessPhysicalDesignPolicy.PlacementAccess,
            MaximumDomainGenerationWork=1,
        ),
    )

    with pytest.raises(RoutingStageError) as Raised:
        _RebuildTransformedPlacementSelectedAccess(
            _Context(Policy),
            _Successor(),
        )

    assert Raised.value.Failure.Reason is (
        RoutingFailureReason.ClusterInterfaceSolveIncomplete
    )
    assert Raised.value.Failure.Stage == "TransformedPlacementAccessRebuild"
    Solve = Raised.value.Failure.Diagnostics["PlacementAccessSolve"]
    assert Solve["Status"] == PlacementAccessSolveStatus.Incomplete.value
    assert Solve["SelectedWitness"] is None

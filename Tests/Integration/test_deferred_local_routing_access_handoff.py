"""Specification-first deferred local-routing selected-access handoff tests."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from PhysicalDesign.Contracts.Failures import (
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Contracts.PlacementAccess import (
    PlacementAccessPatternAttemptReason,
    PlacementAccessPatternAttemptStatus,
    PlacementAccessSolveStatus,
)
from PhysicalDesign.Placement.Access.Capacity import (
    SolvePlacedPinAccessOptionDomains,
)
from PhysicalDesign.Orchestration.AccessEnvelope import (
    BuildCurrentSelectedAccessSolveBinding,
    CurrentSelectedAccessEnvelopeStatus,
)
from PhysicalDesign.Orchestration.Feedback import BuildPlacementFingerprint
from PhysicalDesign.Orchestration.Preparation import (
    BuildPlacementRetentionFingerprint,
)
from PhysicalDesign.Orchestration.Runner import PlaceAndRoutePcb
import PhysicalDesign.Orchestration.PhysicalFlow as PhysicalFlow
import PhysicalDesign.Orchestration.RoutingAttempts as RoutingAttempts


def _BuildFanoutDepthSeventeenNetlist() -> NetlistIR:
    Gates = [Gate("InputA", GateKind.INPUT, ["a"], [])]
    Gates.append(Gate("Root", GateKind.NAND, ["root"], ["a", "a"]))
    Leaves = []
    for BranchIndex in range(4):
        Signal = f"b{BranchIndex}"
        Gates.append(Gate(
            f"Branch{BranchIndex}",
            GateKind.NAND,
            [Signal],
            ["root", "a"],
        ))
        for DepthIndex in range(3 if BranchIndex == 0 else 2):
            NextSignal = f"b{BranchIndex}d{DepthIndex}"
            Gates.append(Gate(
                f"Branch{BranchIndex}Depth{DepthIndex}",
                GateKind.NAND,
                [NextSignal],
                [Signal, Signal],
            ))
            Signal = NextSignal
        Leaves.append(Signal)
    while len(Leaves) > 1:
        Left, Right = Leaves[:2]
        Signal = f"join{len(Gates)}"
        Gates.append(Gate(
            f"Join{len(Gates)}",
            GateKind.NAND,
            [Signal],
            [Left, Right],
        ))
        Leaves = [*Leaves[2:], Signal]
    Gates.append(Gate("Output", GateKind.OUTPUT, [], [Leaves[0]]))
    Module = ModuleIR(
        Name="DeferredLocalRoutingSelectedAccessHandoff",
        Inputs=["a"],
        Outputs=[Leaves[0]],
        Gates=Gates,
    )
    return NetlistIR(Top=Module.Name, Modules={Module.Name: Module})


@pytest.fixture(scope="module")
def ProductionHandoff():
    Captured = {}
    OriginalHandoff = (
        PhysicalFlow.MaterializeSelectedJointPlacementLocalRoutingHandoff
    )
    OriginalDemand = PhysicalFlow.BuildPlacementAccessDemand

    def ObserveHandoff(Context, Candidate, WorkCheck):
        Result = OriginalHandoff(Context, Candidate, WorkCheck)
        if Result.AuthorityDisposition == "rebuilt-current":
            Captured["Context"] = Context
            Captured["SourceCandidate"] = Candidate
            Captured["Handoff"] = Result
        return Result

    def ObserveFirstConsumer(Placement, *Arguments, **Options):
        if "Handoff" in Captured and "ConsumerCandidate" not in Captured:
            Captured["ConsumerCandidate"] = Captured["Context"].InterfaceCandidate
        return OriginalDemand(Placement, *Arguments, **Options)

    PhysicalFlow.MaterializeSelectedJointPlacementLocalRoutingHandoff = (
        ObserveHandoff
    )
    PhysicalFlow.BuildPlacementAccessDemand = ObserveFirstConsumer
    try:
        with pytest.raises(RoutingStageError) as Error:
            PlaceAndRoutePcb(
                _BuildFanoutDepthSeventeenNetlist(),
                Strategy="routing-aware-placement-access",
            )
    finally:
        PhysicalFlow.MaterializeSelectedJointPlacementLocalRoutingHandoff = (
            OriginalHandoff
        )
        PhysicalFlow.BuildPlacementAccessDemand = OriginalDemand

    assert Error.value.Failure.Stage == "CurrentSelectedAccessAfterChannelReplacement"
    assert Error.value.Failure.Reason is (
        RoutingFailureReason.ClusterInterfaceSolveIncomplete
    )
    assert "Handoff" in Captured
    Captured["LaterFailure"] = Error.value.Failure
    return SimpleNamespace(**Captured)


def _Reidentify(Candidate):
    OwnershipFingerprint = (
        Candidate.TopologyDemand.MandatoryAccessOwnershipFingerprint
    )
    return replace(
        Candidate,
        PlacementFingerprint=BuildPlacementFingerprint(
            Candidate.Placement,
            OwnershipFingerprint,
            IncludeLocalClaims=False,
        ),
        PlacementRetentionFingerprint=BuildPlacementRetentionFingerprint(
            Candidate.Placement,
            OwnershipFingerprint,
            IncludeLocalClaims=False,
        ),
        PlacementFingerprintIncludesLocalClaims=False,
    )


def _GatePinGeometry(Placement):
    return tuple(sorted(
        (
            GateValue.Name,
            GateValue.Kind,
            GateValue.X,
            GateValue.Y,
            GateValue.Z,
            GateValue.Rotation,
            GateValue.MirrorX,
            tuple(GateValue.InputPins),
            GateValue.OutputPin,
        )
        for GateValue in Placement.Placed.PlacedGates
    ))


def _ClaimResources(Claims):
    return tuple(
        tuple(sorted(getattr(Claims, Field)))
        for Field in (
            "WireCells",
            "SupportCells",
            "RequiredAirCells",
            "ElectricalCells",
        )
    )


def test_real_production_caller_consumes_current_successor(ProductionHandoff):
    Source = ProductionHandoff.SourceCandidate
    Handoff = ProductionHandoff.Handoff
    Successor = Handoff.Candidate

    assert Handoff.SchemaVersion == (
        "deferred-local-routing-selected-access-handoff-v1"
    )
    assert Handoff.AuthorityDisposition == "rebuilt-current"
    assert Handoff.ReadyForNextStage
    assert Handoff.SelectedAccessResult.Status is (
        CurrentSelectedAccessEnvelopeStatus.Ready
    )
    assert Handoff.SelectedAccessResult.Envelope is not None
    assert Successor.CurrentSelectedAccessEnvelopeResult == (
        Handoff.SelectedAccessResult
    )
    assert Source.CandidateId == Successor.CandidateId
    assert Source.PlacementFingerprint != Successor.PlacementFingerprint
    assert Source.PlacementRetentionFingerprint != (
        Successor.PlacementRetentionFingerprint
    )
    assert Source.Placement.SelectedPinAccessWitness.WitnessFingerprint != (
        Successor.Placement.SelectedPinAccessWitness.WitnessFingerprint
    )
    assert _GatePinGeometry(Source.Placement) == _GatePinGeometry(
        Successor.Placement
    )
    assert Source.Placement.Clusters == Successor.Placement.Clusters
    DomainsByTerminal = {
        (Domain.Signal, Domain.GateName, Domain.Role, Domain.PinId): Domain
        for Domain in Successor.Placement.PlacementAccessSolve.Domains
    }
    for Selection in Successor.Placement.SelectedPinAccessWitness.Selections:
        Domain = DomainsByTerminal[
            (
                Selection.Signal,
                Selection.GateName,
                Selection.Role,
                Selection.PinId,
            )
        ]
        Selected = next(
            Option
            for Option in Domain.Options
            if Option.TemplateId == Selection.TemplateId
        )
        assert Selected.Terminal == Selection.Terminal
        assert _ClaimResources(Selected.Claims) == _ClaimResources(
            Selection.Claims
        )
    assert ProductionHandoff.ConsumerCandidate == Successor
    assert ProductionHandoff.LaterFailure.Stage != (
        "PlacementLocalRoutingMaterialization"
    )


def test_same_geometry_changed_ownership_cannot_reuse_authority(
    ProductionHandoff,
):
    Handoff = ProductionHandoff.Handoff
    Candidate = Handoff.Candidate
    Placement = Candidate.Placement
    Selection = Placement.SelectedPinAccessWitness.Selections[0]
    Node = next(iter(Selection.Claims.WireCells))
    ChangedPlaced = replace(
        Placement.Placed,
        FrozenNetWires={Selection.Signal: (Node,)},
    )
    Changed = replace(Placement, Placed=ChangedPlaced)
    assert ChangedPlaced.PlacedGates == Placement.Placed.PlacedGates

    with pytest.raises(RoutingStageError) as Error:
        RoutingAttempts.RequireCurrentDeferredLocalRoutingSelectedAccess(
            replace(Candidate, Placement=Changed),
            Resources=Handoff.Resources,
            Technology=ProductionHandoff.Context.Technology,
            Policy=ProductionHandoff.Context.Policy,
        )

    assert Error.value.Failure.Reason is (
        RoutingFailureReason.ClusterInterfaceInvariantViolation
    )


def test_foreign_predecessor_binding_is_rejected_before_reuse(
    ProductionHandoff,
):
    Source = ProductionHandoff.SourceCandidate
    Handoff = ProductionHandoff.Handoff
    assert Source.PlacementAccessSolveBinding != (
        Handoff.Candidate.PlacementAccessSolveBinding
    )

    with pytest.raises(RoutingStageError) as Error:
        RoutingAttempts.RequireCurrentDeferredLocalRoutingSelectedAccess(
            replace(
                Handoff.Candidate,
                PlacementAccessSolveBinding=Source.PlacementAccessSolveBinding,
            ),
            Resources=Handoff.Resources,
            Technology=ProductionHandoff.Context.Technology,
            Policy=ProductionHandoff.Context.Policy,
        )

    assert Error.value.Failure.Reason is (
        RoutingFailureReason.ClusterInterfaceInvariantViolation
    )
    assert Error.value.Failure.Diagnostics["CurrentSelectedAccessReason"] == (
        "SolvePolicyMismatch"
    )


def test_foreign_retained_recipe_is_rejected_before_materialization(
    ProductionHandoff,
):
    Context = ProductionHandoff.Context
    Source = ProductionHandoff.SourceCandidate
    ForeignState = replace(
        Source.JointPlacementState,
        CandidateIndex=Source.JointPlacementState.CandidateIndex + 1,
    )
    Foreign = replace(Source, JointPlacementState=ForeignState)

    with pytest.raises(RoutingStageError) as Error:
        RoutingAttempts.MaterializeSelectedJointPlacementLocalRoutingHandoff(
            Context,
            Foreign,
            lambda _Diagnostics: None,
        )

    assert Error.value.Failure.Stage == "PlacementLocalRoutingMaterialization"
    assert Error.value.Failure.Reason is RoutingFailureReason.PlacementOverlap
    assert "retained joint recipe identity" in Error.value.Failure.Detail


def test_incomplete_rebuild_remains_typed_incomplete(ProductionHandoff):
    Handoff = ProductionHandoff.Handoff
    Candidate = Handoff.Candidate
    Solve = replace(
        Candidate.Placement.PlacementAccessSolve,
        Status=PlacementAccessSolveStatus.Incomplete,
        SearchComplete=False,
        OptimalityProven=False,
        SelectedWitness=None,
        IncompleteReason="work-cap",
    )
    Placement = replace(
        Candidate.Placement,
        SelectedPinAccessWitness=None,
        PlacementAccessSolve=Solve,
        Placed=replace(
            Candidate.Placement.Placed,
            SelectedPinAccessWitness=None,
            PlacementAccessSolve=Solve,
        ),
    )
    Incomplete = _Reidentify(replace(
        Candidate,
        Placement=Placement,
        PlacementAccessSolveBinding=BuildCurrentSelectedAccessSolveBinding(
            ProductionHandoff.Context.Policy,
            Solve,
        ),
        CurrentSelectedAccessEnvelopeResult=None,
    ))

    with pytest.raises(RoutingStageError) as Error:
        RoutingAttempts.RequireCurrentDeferredLocalRoutingSelectedAccess(
            Incomplete,
            Resources=Handoff.Resources,
            Technology=ProductionHandoff.Context.Technology,
            Policy=ProductionHandoff.Context.Policy,
        )

    assert Error.value.Failure.Reason is (
        RoutingFailureReason.ClusterInterfaceSolveIncomplete
    )
    assert Error.value.Failure.Diagnostics["CurrentSelectedAccessStatus"] == (
        "Incomplete"
    )
    assert Error.value.Failure.Diagnostics["CurrentSelectedAccessReason"] == (
        "AccessSolveIncomplete"
    )


def test_complete_unsatisfiable_remains_distinct_from_incomplete(
    ProductionHandoff,
):
    Handoff = ProductionHandoff.Handoff
    Candidate = Handoff.Candidate
    RejectedDomains = tuple(
        replace(
            Domain,
            Options=(),
            PatternAttempts=tuple(
                replace(
                    Attempt,
                    Status=PlacementAccessPatternAttemptStatus.Rejected,
                    Reason=(
                        PlacementAccessPatternAttemptReason
                        .TerminalOrBridgeUnavailable
                    ),
                    OptionFingerprint=None,
                )
                for Attempt in Domain.PatternAttempts
            ),
            GeneratedOptionCount=0,
            RejectedOptionCount=len(Domain.PatternAttempts),
        )
        for Domain in Candidate.Placement.PlacementAccessSolve.Domains
    )
    Solve = replace(
        SolvePlacedPinAccessOptionDomains(
            RejectedDomains,
            ResourceGraph=Handoff.Resources.ResourceGraph,
            MaximumExpansions=(
                ProductionHandoff.Context.Policy.PlacementAccess
                .MaximumAssignmentExpansions
            ),
        ),
        PolicyVersion=ProductionHandoff.Context.Policy.PolicyVersion,
    )
    assert Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
    Placement = replace(
        Candidate.Placement,
        SelectedPinAccessWitness=None,
        PlacementAccessSolve=Solve,
        Placed=replace(
            Candidate.Placement.Placed,
            SelectedPinAccessWitness=None,
            PlacementAccessSolve=Solve,
        ),
    )
    Unsatisfiable = _Reidentify(replace(
        Candidate,
        Placement=Placement,
        PlacementAccessSolveBinding=BuildCurrentSelectedAccessSolveBinding(
            ProductionHandoff.Context.Policy,
            Solve,
        ),
        CurrentSelectedAccessEnvelopeResult=None,
    ))

    with pytest.raises(RoutingStageError) as Error:
        RoutingAttempts.RequireCurrentDeferredLocalRoutingSelectedAccess(
            Unsatisfiable,
            Resources=Handoff.Resources,
            Technology=ProductionHandoff.Context.Technology,
            Policy=ProductionHandoff.Context.Policy,
        )

    assert Error.value.Failure.Reason is RoutingFailureReason.NoPinAccessPattern
    assert Error.value.Failure.Diagnostics["CurrentSelectedAccessStatus"] == (
        "Unsatisfiable"
    )
    assert Error.value.Failure.Diagnostics["CurrentSelectedAccessReason"] == (
        "AccessSolveUnsatisfiable"
    )


def test_repeated_supported_materialization_revalidates_cached_successor(
    ProductionHandoff,
):
    Cached = RoutingAttempts.MaterializeSelectedJointPlacementLocalRoutingHandoff(
        ProductionHandoff.Context,
        ProductionHandoff.SourceCandidate,
        lambda _Diagnostics: None,
    )

    assert Cached.AuthorityDisposition == "retained-current"
    assert Cached.ReadyForNextStage
    assert Cached.Candidate.PlacementFingerprint == (
        ProductionHandoff.Handoff.Candidate.PlacementFingerprint
    )
    assert Cached.SelectedAccessResult.Status is (
        CurrentSelectedAccessEnvelopeStatus.Ready
    )


def test_nondeferred_handoff_does_not_advertise_selected_access_readiness(
    ProductionHandoff,
):
    Source = ProductionHandoff.SourceCandidate
    Diagnostics = dict(Source.Placement.Placed.LocalRouteDiagnostics or {})
    Diagnostics.pop("__DeferredLocalRouting__", None)
    Placement = replace(
        Source.Placement,
        Placed=replace(
            Source.Placement.Placed,
            LocalRouteDiagnostics=Diagnostics,
        ),
    )
    Candidate = replace(Source, Placement=Placement)

    Enabled = RoutingAttempts.MaterializeSelectedJointPlacementLocalRoutingHandoff(
        ProductionHandoff.Context,
        Candidate,
        lambda _Diagnostics: None,
    )
    DisabledContext = SimpleNamespace(
        Deadline=ProductionHandoff.Context.Deadline,
        Policy=replace(
            ProductionHandoff.Context.Policy,
            PlacementAccess=replace(
                ProductionHandoff.Context.Policy.PlacementAccess,
                Enabled=False,
            ),
        ),
    )
    Disabled = RoutingAttempts.MaterializeSelectedJointPlacementLocalRoutingHandoff(
        DisabledContext,
        Candidate,
        lambda _Diagnostics: None,
    )

    for Result in (Enabled, Disabled):
        assert Result.AuthorityDisposition == "not-applicable"
        assert Result.SelectedAccessResult is None
        assert Result.ReadyForNextStage is False


def test_disallowed_gate_pin_geometry_drift_fails_real_handoff(
    ProductionHandoff,
    monkeypatch,
):
    Context = ProductionHandoff.Context
    SourceCandidate = ProductionHandoff.SourceCandidate
    Source = SourceCandidate.Placement
    Successor = ProductionHandoff.Handoff.Candidate.Placement
    OriginalServices = Context.Services
    RebuildCalls = []
    OriginalRebuild = RoutingAttempts.RebuildCurrentCandidatePlacementAccess

    def ObserveRebuild(*Arguments, **Options):
        RebuildCalls.append(True)
        return OriginalRebuild(*Arguments, **Options)

    monkeypatch.setattr(
        RoutingAttempts,
        "RebuildCurrentCandidatePlacementAccess",
        ObserveRebuild,
    )
    OriginalCached = Context.MaterializedPlacementByFingerprint.pop(
        SourceCandidate.PlacementFingerprint,
        None,
    )
    OriginalCandidateResource = Context.RoutingResourcesByCandidateId.pop(
        SourceCandidate.CandidateId,
        None,
    )
    OriginalFingerprintResource = Context.RoutingResourcesByFingerprint.pop(
        SourceCandidate.PlacementFingerprint,
        None,
    )

    def DriftRealMaterialization(*Arguments, **Options):
        Placement = OriginalServices.PlacePcbGraph(*Arguments, **Options)
        Gates = list(Placement.Placed.PlacedGates)
        Gates[0] = replace(Gates[0], X=Gates[0].X + 1)
        return replace(
            Placement,
            Placed=replace(Placement.Placed, PlacedGates=Gates),
        )

    Context.Services = replace(
        OriginalServices,
        PlacePcbGraph=DriftRealMaterialization,
    )
    try:
        with pytest.raises(RoutingStageError) as Error:
            RoutingAttempts.MaterializeSelectedJointPlacementLocalRoutingHandoff(
                Context,
                SourceCandidate,
                lambda _Diagnostics: None,
            )
    finally:
        Context.Services = OriginalServices
        if OriginalCached is not None:
            Context.MaterializedPlacementByFingerprint[
                SourceCandidate.PlacementFingerprint
            ] = OriginalCached
        if OriginalCandidateResource is not None:
            Context.RoutingResourcesByCandidateId[
                SourceCandidate.CandidateId
            ] = OriginalCandidateResource
        if OriginalFingerprintResource is not None:
            Context.RoutingResourcesByFingerprint[
                SourceCandidate.PlacementFingerprint
            ] = OriginalFingerprintResource

    assert _GatePinGeometry(Source) == _GatePinGeometry(Successor)
    assert Error.value.Failure.Stage == "PlacementLocalRoutingMaterialization"
    assert Error.value.Failure.Reason is RoutingFailureReason.PlacementOverlap
    assert Error.value.Failure.Diagnostics["GateGeometryMatches"] is False
    assert RebuildCalls == []


def test_original_outer_runtime_authority_is_unchanged(ProductionHandoff):
    Handoff = ProductionHandoff.Handoff

    assert Handoff.OriginalStartedAt == ProductionHandoff.Context.Deadline.StartedAt
    assert Handoff.OriginalExpiresAt == ProductionHandoff.Context.Deadline.ExpiresAt
    assert Handoff.OriginalExpiresAt - Handoff.OriginalStartedAt == 120.0
    assert Handoff.ToDictionary()["RuntimeAuthority"] == {
        "StartedAt": ProductionHandoff.Context.Deadline.StartedAt,
        "ExpiresAt": ProductionHandoff.Context.Deadline.ExpiresAt,
    }

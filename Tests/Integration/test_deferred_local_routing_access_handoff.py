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
from PhysicalDesign.Redstone.Rules import BuildRoutingResources
from PhysicalDesign.Routing.Pcb import (
    BuildPcbRoutingConfigurations,
    PrepareTrackAssignment,
    RoutePcbAttempt,
)
from PhysicalDesign.Runtime.Reliability import RoutingDeadline
import PhysicalDesign.Orchestration.PhysicalFlow as PhysicalFlow
import PhysicalDesign.Orchestration.RoutingAttempts as RoutingAttempts
import PhysicalDesign.Routing.Global.Orchestration.Flow as RoutingFlow


class _EligibilityBoundaryReached(BaseException):
    """Stop the regression after proving physical eligibility was entered."""


class _CandidateMaterializationBoundaryReached(BaseException):
    """Stop after observing one ordinary candidate-domain producer."""


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
    OriginalTrackPreparation = (
        PhysicalFlow.RebuildCurrentCandidateTrackPreparation
    )
    OriginalEligibility = PhysicalFlow.PreparePhysicalComponentEligibility
    OriginalPhaseRunner = RoutingFlow.RunAuthoritativeRoutingPhases

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

    def ObservePhaseRunner(State, Services, Phases=None):
        if Phases is None:
            Phases = OriginalPhaseRunner.__defaults__[0]
        for RunPhase in Phases:
            IsTrackCandidateMaterialization = (
                RunPhase.__name__ == "RunCandidateMaterialization"
                and State.PrepareTrackAssignmentOnly
                and "Handoff" in Captured
            )
            if (
                IsTrackCandidateMaterialization
                and "PortalEvidence" not in Captured
            ):
                Signal = "b0d2"
                Profile = State.Profiles[Signal]
                Portals = State.LegalPortalTuplesBySignalLayer[
                    Signal,
                    0,
                ][0]
                SourcePortal, TargetPortal = Portals
                PortalIds = tuple(Portal.PortalId for Portal in Portals)
                TupleClaims = State.PortalTupleClaimsBySignal[Signal][PortalIds]
                ForeignClaims = (
                    State.ForeignSelectedPinAccessClaimsBySignal[Signal]
                )
                OriginalTargetPath = (
                    Profile.TargetAccessPaths[Profile.Targets[0]][-1],
                    (16, 2, 23),
                )
                OriginalTargetClaims = (
                    State.Resources.ResourceGraph.BuildRouteClaims(
                        OriginalTargetPath
                    )
                )
                Captured["PortalEvidence"] = SimpleNamespace(
                    Signal=Signal,
                    SourceAccessPath=tuple(Profile.SourceAccessPath),
                    TargetAccessPath=tuple(
                        Profile.TargetAccessPaths[Profile.Targets[0]]
                    ),
                    SourcePortal=SourcePortal,
                    TargetPortal=TargetPortal,
                    OriginalTargetPath=OriginalTargetPath,
                    OriginalTargetConflictOwners=tuple(
                        Owner
                        for Owner, Claims in ForeignClaims
                        if Services.ComponentClaimsConflict(
                            OriginalTargetClaims,
                            Claims,
                        )
                    ),
                    CompatiblePortalConflictOwners=tuple(
                        Owner
                        for Owner, Claims in ForeignClaims
                        if Services.ComponentClaimsConflict(
                            TargetPortal.Claims,
                            Claims,
                        )
                    ),
                    CompatibleTupleConflictOwners=tuple(
                        Owner
                        for Owner, Claims in ForeignClaims
                        if Services.ComponentClaimsConflict(
                            TupleClaims,
                            Claims,
                        )
                    ),
                    PortalSelfConflicts=tuple(
                        Services.FindSelfClaimConflicts({Signal: TupleClaims})
                    ),
                    PortalPathsWithinSupportedNodes=all(
                        frozenset(Portal.Path) <= State.Region.Nodes
                        for Portal in Portals
                    ),
                    PortalEdgesWithinSupportedEdges=all(
                        frozenset(Portal.Edges) <= State.Region.Edges
                        for Portal in Portals
                    ),
                    WitnessFingerprint=(
                        State.PlacementPinAccessWitness.WitnessFingerprint
                    ),
                    ConstraintTelemetry=dict(
                        State.WorkTelemetry[
                            "SelectedPinAccessPortalSearchConstraint"
                        ]
                    ),
                )
            try:
                Outcome = RunPhase(State, Services)
            finally:
                if (
                    IsTrackCandidateMaterialization
                    and "CandidateEvidence" not in Captured
                ):
                    Signal = "b0d2"
                    Profile = State.Profiles[Signal]
                    Candidates = tuple(State.CandidatesBySignal.get(Signal, ()))
                    Captured["CandidateEvidence"] = SimpleNamespace(
                        RouteRequestCount=len(
                            State.RouteRequestsBySignal.get(Signal, ())
                        ),
                        CandidateCount=len(Candidates),
                        CandidateSourcePortalIds=tuple(
                            Candidate.SourcePortalId
                            for Candidate in Candidates
                        ),
                        CandidateTargetPortalIds=tuple(
                            Candidate.TargetPortalIds[Profile.Targets[0]]
                            for Candidate in Candidates
                        ),
                        CandidateConflictOwners=tuple(
                            tuple(
                                Owner
                                for Owner, Claims in (
                                    State.ForeignSelectedPinAccessClaimsBySignal[
                                        Signal
                                    ]
                                )
                                if Services.ComponentClaimsConflict(
                                    Candidate.Claims,
                                    Claims,
                                )
                            )
                            for Candidate in Candidates
                        ),
                        Diagnostics=dict(
                            State.CandidateDiagnostics.get(Signal, {})
                        ),
                    )
                    RootSignal = "root"
                    RootProfile = State.Profiles[RootSignal]
                    RootTarget = (4, 1, 6)
                    RootCandidates = tuple(
                        State.CandidatesBySignal.get(RootSignal, ())
                    )
                    RootShapes = tuple(
                        State.PhysicalCandidateRequestShapesBySignal.get(
                            RootSignal,
                            (),
                        )
                    )
                    Captured["RootCandidateEvidence"] = SimpleNamespace(
                        Target=RootTarget,
                        RouteRequestCount=len(
                            State.RouteRequestsBySignal.get(RootSignal, ())
                        ),
                        CandidateCount=len(RootCandidates),
                        CandidateIds=tuple(
                            Candidate.CandidateId
                            for Candidate in RootCandidates
                        ),
                        CandidateTargetPortalIds=tuple(
                            Candidate.TargetPortalIds[RootTarget]
                            for Candidate in RootCandidates
                        ),
                        CandidateAdmissionConflicts=tuple(
                            (
                                tuple(
                                    Services.FindForeignSelectedPinAccessConflictSignals(
                                        RootSignal,
                                        Candidate.Claims,
                                        State.ForeignSelectedPinAccessClaimsBySignal,
                                        Services.ComponentClaimsConflict,
                                    )
                                ),
                                tuple(
                                    Claim.Signal
                                    for Claim in State.FrozenComponentClaims
                                    if Claim.Signal != RootSignal
                                    and Services.ComponentClaimsConflict(
                                        Candidate.Claims,
                                        Claim.Claims,
                                    )
                                ),
                                tuple(
                                    State.AssemblySpecificSiblingApertureConflictSignals(
                                        RootSignal,
                                        Candidate.Claims,
                                    )
                                ),
                            )
                            for Candidate in RootCandidates
                        ),
                        RequestShapeCount=len(RootShapes),
                        GuideExpansions=tuple(sorted({
                            Shape.GuideExpansion for Shape in RootShapes
                        })),
                        RoutePortalVariantCount=(
                            State.RoutePortalVariantCounts[RootSignal]
                        ),
                        LegalTupleTargetPortalIds=tuple(sorted({
                            Portals[
                                1 + RootProfile.Targets.index(RootTarget)
                            ].PortalId
                            for Layer in range(State.LayerCount)
                            for Portals in State.LegalPortalTuplesBySignalLayer.get(
                                (RootSignal, Layer),
                                (),
                            )
                        })),
                        Scheduler=dict(
                            State.WorkTelemetry[
                                "MatureStagedInitialCandidateScheduler"
                            ]
                        ),
                        UnreservedPortalMode=State.UnreservedPortalMode,
                        CoordinatedSignals=tuple(sorted(
                            State.CoordinatedCandidateDiversificationSignals
                        )),
                    )
            if Outcome.Returned:
                return Outcome.Value
        raise RuntimeError(
            "authoritative routing phases completed without a result"
        )

    def ObserveTrackPreparation(Context, Candidate, *, Resources):
        Captured["TrackCandidate"] = Candidate
        Captured["TrackResources"] = Resources
        Result = OriginalTrackPreparation(
            Context,
            Candidate,
            Resources=Resources,
        )
        Captured["TrackPreparation"] = Result
        return Result

    def ObserveEligibility(*Arguments, **Options):
        Captured["EligibilityEntered"] = True
        raise _EligibilityBoundaryReached()

    PhysicalFlow.MaterializeSelectedJointPlacementLocalRoutingHandoff = (
        ObserveHandoff
    )
    PhysicalFlow.BuildPlacementAccessDemand = ObserveFirstConsumer
    PhysicalFlow.RebuildCurrentCandidateTrackPreparation = (
        ObserveTrackPreparation
    )
    PhysicalFlow.PreparePhysicalComponentEligibility = ObserveEligibility
    RoutingFlow.RunAuthoritativeRoutingPhases = ObservePhaseRunner
    try:
        with pytest.raises(_EligibilityBoundaryReached):
            PlaceAndRoutePcb(
                _BuildFanoutDepthSeventeenNetlist(),
                Strategy="routing-aware-placement-access",
            )
    finally:
        PhysicalFlow.MaterializeSelectedJointPlacementLocalRoutingHandoff = (
            OriginalHandoff
        )
        PhysicalFlow.BuildPlacementAccessDemand = OriginalDemand
        PhysicalFlow.RebuildCurrentCandidateTrackPreparation = (
            OriginalTrackPreparation
        )
        PhysicalFlow.PreparePhysicalComponentEligibility = OriginalEligibility
        RoutingFlow.RunAuthoritativeRoutingPhases = OriginalPhaseRunner

    assert "Handoff" in Captured
    assert "PortalEvidence" in Captured
    assert "CandidateEvidence" in Captured
    assert "RootCandidateEvidence" in Captured
    assert "TrackPreparation" in Captured
    assert Captured["EligibilityEntered"] is True
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
    assert ProductionHandoff.EligibilityEntered is True
    Portal = ProductionHandoff.PortalEvidence
    assert Portal.SourceAccessPath == (
        (60, 1, 13),
        (61, 1, 13),
        (62, 1, 13),
    )
    assert Portal.TargetAccessPath == (
        (19, 1, 23),
        (18, 1, 23),
        (17, 1, 23),
    )
    assert Portal.SourcePortal.Path == ((60, 1, 13), (60, 2, 12))
    assert Portal.OriginalTargetPath == ((17, 1, 23), (16, 2, 23))
    assert Portal.OriginalTargetConflictOwners == ("b3d0",)
    assert Portal.TargetPortal.Path == ((17, 1, 23), (17, 2, 24))
    assert Portal.CompatiblePortalConflictOwners == ()
    assert Portal.CompatibleTupleConflictOwners == ()
    assert Portal.PortalSelfConflicts == ()
    assert Portal.PortalPathsWithinSupportedNodes
    assert Portal.PortalEdgesWithinSupportedEdges
    assert Portal.ConstraintTelemetry["AppliedBeforeTargetSelection"] is True
    assert Portal.ConstraintTelemetry["Projection"] == (
        "shared-immutable-routing-claims"
    )
    Candidate = ProductionHandoff.CandidateEvidence
    assert Candidate.RouteRequestCount == 8
    assert Candidate.CandidateCount == 8
    assert set(Candidate.CandidateSourcePortalIds) == {
        Portal.SourcePortal.PortalId,
    }
    assert set(Candidate.CandidateTargetPortalIds) == {
        Portal.TargetPortal.PortalId,
    }
    assert set(Candidate.CandidateConflictOwners) == {()}

    Root = ProductionHandoff.RootCandidateEvidence
    assert Root.RouteRequestCount == 64
    assert Root.RequestShapeCount == Root.RouteRequestCount
    assert Root.GuideExpansions == (3,)
    assert Root.CandidateCount > 1
    assert Root.RoutePortalVariantCount == 8
    assert Root.UnreservedPortalMode is True
    assert Root.CoordinatedSignals == ()
    assert "root:(4, 1, 6):0:Portal:3,2,7" in (
        Root.LegalTupleTargetPortalIds
    )
    assert "root:(4, 1, 6):0:Portal:3,2,7" in (
        Root.CandidateTargetPortalIds
    )
    assert set(Root.CandidateAdmissionConflicts) == {((), (), ())}
    assert Root.Scheduler["PlannedRequestCount"] == 328
    assert Root.Scheduler["ExecutedRequestCount"] == 328
    assert Root.Scheduler["FullPoolGenerated"] is True
    assert Root.Scheduler["EverySignalHasTree"] is True

    Track = ProductionHandoff.TrackPreparation
    assert Track.Complete
    assert Track.Success
    assert dict(Track.CandidateCounts)["b0d2"] == 8
    assert dict(Track.Diagnostics)["FailureNet"] == ""
    SelectedBySignal = dict(Track.SelectedCandidateIds)
    assert SelectedBySignal["root"] in Root.CandidateIds
    assert "Troot:(4, 1, 6):0:Portal:3,2,7" in SelectedBySignal["root"]


def test_ordinary_caller_keeps_staged_seed_as_an_early_stop(
    ProductionHandoff,
    monkeypatch,
):
    Placement = ProductionHandoff.TrackCandidate.Placement
    Resources = BuildRoutingResources(Placement.Placed)
    Deadline = RoutingDeadline.Start(60.0)
    Captured = {}
    OriginalPhaseRunner = RoutingFlow.RunAuthoritativeRoutingPhases

    def StopAfterOrdinaryCandidateDomain(State, Services, Phases=None):
        if Phases is None:
            Phases = OriginalPhaseRunner.__defaults__[0]
        for RunPhase in Phases:
            Outcome = RunPhase(State, Services)
            if RunPhase.__name__ == "RunCandidateMaterialization":
                Captured.update({
                    "PrepareTrackAssignmentOnly": (
                        State.PrepareTrackAssignmentOnly
                    ),
                    "PreparingPhysicalComponentGlobalChannels": (
                        State.Resources.PreparingPhysicalComponentGlobalChannels
                    ),
                    "Scheduler": dict(
                        State.WorkTelemetry[
                            "MatureStagedInitialCandidateScheduler"
                        ]
                    ),
                    "UnreservedPortalMode": State.UnreservedPortalMode,
                    "CoordinatedSignals": tuple(sorted(
                        State.CoordinatedCandidateDiversificationSignals
                    )),
                    "DistinctTupleCountsByVariant": {
                        Signal: {
                            Variant: len({
                                (
                                    SourcePortal.PortalId,
                                    tuple(
                                        Portal.PortalId
                                        for Portal in TargetPortals
                                    ),
                                )
                                for SourcePortal, TargetPortals,
                                _Guide, _Layer, _Axis, _Lane, MetadataVariant
                                in State.RouteMetadataBySignal[Signal]
                                if MetadataVariant == Variant
                            })
                            for Variant in {
                                Metadata[-1]
                                for Metadata
                                in State.RouteMetadataBySignal[Signal]
                            }
                        }
                        for Signal in State.RouteMetadataBySignal
                    },
                })
                raise _CandidateMaterializationBoundaryReached()
            if Outcome.Returned:
                return Outcome.Value
        raise RuntimeError(
            "authoritative routing phases completed without a result"
        )

    monkeypatch.setattr(
        RoutingFlow,
        "RunAuthoritativeRoutingPhases",
        StopAfterOrdinaryCandidateDomain,
    )
    with pytest.raises(_CandidateMaterializationBoundaryReached):
        RoutePcbAttempt(
            Placement,
            BuildPcbRoutingConfigurations(Placement)[0],
            Resources=Resources,
            Policy=ProductionHandoff.Context.Policy,
            Deadline=Deadline,
        )

    Scheduler = Captured["Scheduler"]
    assert Captured["PrepareTrackAssignmentOnly"] is False
    assert Captured["PreparingPhysicalComponentGlobalChannels"] is False
    assert Captured["UnreservedPortalMode"] is False
    assert Captured["CoordinatedSignals"] == ()
    assert any(
        Count > 1
        for Counts in Captured["DistinctTupleCountsByVariant"].values()
        for Count in Counts.values()
    )
    assert Scheduler["EverySignalHasTree"] is True
    assert Scheduler["FullPoolGenerated"] is False
    assert Scheduler["ExecutedRequestCount"] == 6
    assert Scheduler["PlannedRequestCount"] == 24
    assert not Deadline.IsExpired()


def test_coordinated_caller_requests_retained_tuple_tail_within_domain(
    ProductionHandoff,
    monkeypatch,
):
    Placement = ProductionHandoff.TrackCandidate.Placement
    Resources = BuildRoutingResources(Placement.Placed)
    Deadline = RoutingDeadline.Start(60.0)
    Captured = {}
    OriginalPhaseRunner = RoutingFlow.RunAuthoritativeRoutingPhases

    def ObserveCoordinatedCandidateDomain(State, Services, Phases=None):
        if Phases is None:
            Phases = OriginalPhaseRunner.__defaults__[0]
        for RunPhase in Phases:
            if RunPhase.__name__ == "RunCandidateMaterialization":
                State.CoordinatedCandidateDiversificationSignals = frozenset({
                    "root",
                })
                State.ConfiguredCoordinatedCandidateDiversityFixedLevel = 1
            try:
                Outcome = RunPhase(State, Services)
            finally:
                if (
                    RunPhase.__name__ == "RunCandidateMaterialization"
                    and "RequestCount" not in Captured
                ):
                    Signal = "root"
                    Target = (4, 1, 6)
                    Captured.update({
                        "UnreservedPortalMode": State.UnreservedPortalMode,
                        "CoordinatedSignals": tuple(sorted(
                            State.CoordinatedCandidateDiversificationSignals
                        )),
                        "RequestCount": len(
                            State.RouteRequestsBySignal[Signal]
                        ),
                        "RequestShapeCount": len(
                            State.PhysicalCandidateRequestShapesBySignal[Signal]
                        ),
                        "RoutePortalVariantCount": (
                            State.RoutePortalVariantCounts[Signal]
                        ),
                        "RequestedTargetPortalIds": tuple(sorted({
                            TargetPortals[
                                State.Profiles[Signal].Targets.index(Target)
                            ].PortalId
                            for _SourcePortal, TargetPortals, *_Rest
                            in State.RouteMetadataBySignal[Signal]
                        })),
                        "CandidateTargetPortalIds": tuple(sorted({
                            Candidate.TargetPortalIds[Target]
                            for Candidate in State.CandidatesBySignal[Signal]
                        })),
                        "Scheduler": dict(
                            State.WorkTelemetry[
                                "MatureStagedInitialCandidateScheduler"
                            ]
                        ),
                    })
            if Outcome.Returned:
                return Outcome.Value
        raise RuntimeError(
            "authoritative routing phases completed without a result"
        )

    monkeypatch.setattr(
        RoutingFlow,
        "RunAuthoritativeRoutingPhases",
        ObserveCoordinatedCandidateDomain,
    )
    PrepareTrackAssignment(
        Placement,
        Resources=Resources,
        Policy=ProductionHandoff.Context.Policy,
        Deadline=Deadline,
    )

    assert Captured["UnreservedPortalMode"] is False
    assert Captured["CoordinatedSignals"] == ("root",)
    assert Captured["RequestCount"] == 187
    assert Captured["RequestShapeCount"] == Captured["RequestCount"]
    assert Captured["RoutePortalVariantCount"] == 8
    assert Captured["Scheduler"]["FullPoolGenerated"] is True
    assert Captured["Scheduler"]["ExecutedRequestCount"] == (
        Captured["Scheduler"]["PlannedRequestCount"]
    )
    assert "root:(4, 1, 6):0:Portal:3,2,7" in (
        Captured["RequestedTargetPortalIds"]
    )
    assert Captured["CandidateTargetPortalIds"]
    assert not Deadline.IsExpired()


def test_mandatory_selected_access_overlap_remains_typed_incomplete(
    ProductionHandoff,
    monkeypatch,
):
    Candidate = ProductionHandoff.TrackCandidate
    Placement = Candidate.Placement
    Resources = BuildRoutingResources(Placement.Placed)
    Deadline = RoutingDeadline.Start(10.0)
    Captured = {
        "PortalRuns": 0,
        "CandidateRuns": 0,
    }
    OriginalPhaseRunner = RoutingFlow.RunAuthoritativeRoutingPhases

    class WitnessWithOverlappingForeignClaim:
        def __init__(self, Base, ClaimsBySignal):
            self.Base = Base
            self.ClaimsBySignal = ClaimsBySignal

        def __getattr__(self, Name):
            return getattr(self.Base, Name)

    def RunWithMandatoryForeignOverlap(State, Services, Phases=None):
        if Phases is None:
            Phases = OriginalPhaseRunner.__defaults__[0]
        for RunPhase in Phases:
            if RunPhase.__name__ == "RunPortalPreparation":
                Captured["PortalRuns"] += 1
                Signal = "b0d2"
                Owner = "b3d0"
                Profile = State.Profiles[Signal]
                State.Profiles = {Signal: Profile}
                State.RawPortalVariantCounts = {
                    Signal: State.RawPortalVariantCounts[Signal]
                }
                State.RoutePortalVariantCounts = {
                    Signal: State.RoutePortalVariantCounts[Signal]
                }
                MandatoryNode = Profile.TargetAccessPaths[
                    Profile.Targets[0]
                ][-1]
                OriginalWitness = State.PlacementPinAccessWitness
                ChangedClaimsBySignal = []
                ChangedOwnerClaims = None
                for ClaimSignal, Claims in OriginalWitness.ClaimsBySignal:
                    if ClaimSignal == Owner:
                        ChangedOwnerClaims = replace(
                            Claims,
                            WireCells=Claims.WireCells | {MandatoryNode},
                            ElectricalCells=(
                                Claims.ElectricalCells | {MandatoryNode}
                            ),
                        )
                        Claims = ChangedOwnerClaims
                    ChangedClaimsBySignal.append((ClaimSignal, Claims))
                assert ChangedOwnerClaims is not None
                State.PlacementPinAccessWitness = (
                    WitnessWithOverlappingForeignClaim(
                        OriginalWitness,
                        tuple(ChangedClaimsBySignal),
                    )
                )
                MandatoryClaims = State.Resources.ResourceGraph.BuildRouteClaims(
                    (
                        *Profile.SourceAccessPath,
                        *(
                            Position
                            for TargetPath in Profile.TargetAccessPaths.values()
                            for Position in TargetPath
                        ),
                    )
                )
                Captured.update({
                    "Signal": Signal,
                    "Owner": Owner,
                    "MandatoryNode": MandatoryNode,
                    "SourceAccessPath": tuple(Profile.SourceAccessPath),
                    "TargetAccessPath": tuple(
                        Profile.TargetAccessPaths[Profile.Targets[0]]
                    ),
                    "SharedConflict": Services.ComponentClaimsConflict(
                        MandatoryClaims,
                        ChangedOwnerClaims,
                    ),
                    "SharedConflictOwners": (
                        Services.FindForeignSelectedPinAccessConflictSignals(
                            Signal,
                            MandatoryClaims,
                            {Signal: ((Owner, ChangedOwnerClaims),)},
                            Services.ComponentClaimsConflict,
                        )
                    ),
                    "PortalLimit": State.PortalLimit,
                    "RawPortalVariantCount": (
                        State.RawPortalVariantCounts[Signal]
                    ),
                    "StrictMaximumExpansions": (
                        State.Policy.DetailedRouting.StrictMaximumExpansions
                    ),
                    "DeadlineIsOriginal": State.Deadline is Deadline,
                })
            Outcome = RunPhase(State, Services)
            if RunPhase.__name__ == "RunPortalPreparation":
                Cache = State.EffectiveRawPortalCache
                SignalRequests = tuple(
                    Request
                    for (Signal, _Terminal, _Layer), Request in zip(
                        Cache.ConfiguredPortalRequestMetadata,
                        Cache.ConfiguredPortalRequests,
                    )
                    if Signal == Captured["Signal"]
                )
                Captured["MandatoryOverlapTelemetry"] = tuple(
                    tuple(Position)
                    for Positions in State.WorkTelemetry.get(
                        "SelectedPinAccessPortalMandatoryOverlap",
                        {},
                    ).values()
                    for Position in Positions
                )
                Captured["MandatoryNodeRetainedInStarts"] = any(
                    Captured["MandatoryNode"] in Starts
                    for Starts, _Targets, _Allowed, _Y, _Limit, _Work in (
                        SignalRequests
                    )
                )
                Captured["MandatoryNodeRetainedInAllowed"] = any(
                    Captured["MandatoryNode"] in Allowed
                    for _Starts, _Targets, Allowed, _Y, _Limit, _Work in (
                        SignalRequests
                    )
                )
                Captured["PortalLimitAfterConstraint"] = State.PortalLimit
                Captured["RawPortalVariantCountAfterConstraint"] = (
                    State.RawPortalVariantCounts[Captured["Signal"]]
                )
                Captured["StrictMaximumExpansionsAfterConstraint"] = (
                    State.Policy.DetailedRouting.StrictMaximumExpansions
                )
            if RunPhase.__name__ == "RunCandidateMaterialization":
                Captured["CandidateRuns"] += 1
            if Outcome.Returned:
                return Outcome.Value
        raise RuntimeError(
            "authoritative routing phases completed without a result"
        )

    monkeypatch.setattr(
        RoutingFlow,
        "RunAuthoritativeRoutingPhases",
        RunWithMandatoryForeignOverlap,
    )
    Preparation = PrepareTrackAssignment(
        Placement,
        Resources=Resources,
        Policy=ProductionHandoff.Context.Policy,
        Deadline=Deadline,
    )

    assert not Deadline.IsExpired()
    assert Deadline.RemainingSeconds() > 0.0
    assert Captured["SharedConflict"]
    assert Captured["SharedConflictOwners"] == ("b3d0",)
    assert Captured["MandatoryNode"] in Captured["MandatoryOverlapTelemetry"]
    assert Captured["MandatoryNodeRetainedInStarts"]
    assert Captured["MandatoryNodeRetainedInAllowed"]
    assert Captured["SourceAccessPath"] == (
        (60, 1, 13),
        (61, 1, 13),
        (62, 1, 13),
    )
    assert Captured["TargetAccessPath"] == (
        (19, 1, 23),
        (18, 1, 23),
        (17, 1, 23),
    )
    assert Captured["PortalLimit"] == Captured["PortalLimitAfterConstraint"]
    assert Captured["RawPortalVariantCount"] == (
        Captured["RawPortalVariantCountAfterConstraint"]
    )
    assert Captured["StrictMaximumExpansions"] == (
        Captured["StrictMaximumExpansionsAfterConstraint"]
    )
    assert Captured["PortalLimit"] > 0
    assert Captured["RawPortalVariantCount"] > 0
    assert Captured["StrictMaximumExpansions"] > 0
    assert Captured["DeadlineIsOriginal"]
    assert Captured["PortalRuns"] == 1
    assert Captured["CandidateRuns"] == 0
    assert not Preparation.Success
    assert not Preparation.Complete
    assert Preparation.IncompleteReason == "fixed-domain-exhausted"
    assert dict(Preparation.CandidateCounts) == {"b0d2": 0}
    assert Preparation.ConflictSignals == ("b0d2",)


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

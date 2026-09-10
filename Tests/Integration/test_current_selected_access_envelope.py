"""Specification-first current selected-access envelope coverage."""

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from Compilation.Ir.Models import Gate, GateKind, ModuleIR
from PhysicalDesign.Contracts.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Contracts.PlacementAccess import (
    BuildPlacementAccessProblemFingerprint,
    CurrentSelectedPlacementAccessValidationReason,
    CurrentSelectedPlacementAccessValidationStatus,
    PlacementAccessPatternAttemptReason,
    PlacementAccessPatternAttemptStatus,
    PlacementAccessSolveStatus,
)
from PhysicalDesign.Contracts.Placement import (
    ClusterInterfacePlacementState,
    TrackAssignmentPreparation,
)
from PhysicalDesign.Routing.Assignment.TemplateAssignment import (
    RawTrackAssignmentProblem,
    RawTrackAssignmentTemplate,
    SolveRawTrackAssignmentProblem,
)
from PhysicalDesign.Routing.Global.Orchestration.RunModels import (
    RawTrackAssignmentDomain,
    RawTrackAssignmentValue,
)
from PhysicalDesign.Geometry.Placement import BuildPlacedGate, PlacedDesign
from PhysicalDesign.Orchestration.AccessEnvelope import (
    BuildCurrentSelectedAccessSolveBinding,
    BuildCurrentSelectedAccessEnvelope,
    CurrentSelectedAccessEnvelopePhase,
    CurrentSelectedAccessEnvelopeReason,
    CurrentSelectedAccessEnvelopeStatus,
    CurrentSelectedAccessTransition,
    RequireCurrentSelectedAccessEnvelopeReady,
    SelectUnambiguousCurrentSelectedAccessSolveBinding,
)
from PhysicalDesign.Orchestration.Candidates import PcbPlacementCandidate
from PhysicalDesign.Orchestration.PlacementAttempts import (
    RebuildCurrentCandidatePlacementAccess,
)
from PhysicalDesign.Placement.Access.Capacity import (
    SolvePlacedPinAccessOptionDomains,
)
from PhysicalDesign.Placement.Access.Catalog import (
    EnumeratePlacedPinAccessOptionDomains,
)
from PhysicalDesign.Placement.Engine.Channels import ClusterBoundaryLeaseRequest
from PhysicalDesign.Placement.Engine.Clusters import (
    BuildBoundedInterClusterRoutingChannel,
    BuildBoundedInterClusterRoutingDeck,
    ClusterLocalRouteTemplate,
    PackedNandCluster,
    PcbPlacement,
)
from PhysicalDesign.Placement.PreRouteInterface import (
    DerivedRoutingEnvelope,
    PlacementAccessDemand,
)
from PhysicalDesign.Orchestration.Preparation import (
    BuildDerivedRoutingEnvelopeDomain,
    BuildPlacementAccessDemand,
)
from PhysicalDesign.Policy import (
    RoutingAwarePlacementAccessPhysicalDesignPolicy,
)
from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims
from PhysicalDesign.Resources.ResourceGraph import LocalRouteClaim


Technology = DefaultRedstoneRoutingTechnology
Policy = RoutingAwarePlacementAccessPhysicalDesignPolicy
_DefaultSolveBinding = object()
_DefaultPreparation = object()


@dataclass(frozen=True)
class _AttachedAccessFacts:
    FabricFingerprint: str
    Complete: bool = True


@dataclass(frozen=True)
class _RawTrackFacts:
    SelectionFingerprint: str
    Success: bool = True
    Complete: bool = True

    def ToDictionary(self):
        return {
            "SelectionFingerprint": self.SelectionFingerprint,
            "Success": self.Success,
            "Complete": self.Complete,
        }


def _AllPlacementAccessPatternsRejected(Domains):
    return tuple(
        replace(
            Domain,
            Options=(),
            PatternAttempts=tuple(
                replace(
                    Attempt,
                    Status=PlacementAccessPatternAttemptStatus.Rejected,
                    Reason=(
                        PlacementAccessPatternAttemptReason.
                        TerminalOrBridgeUnavailable
                    ),
                    OptionFingerprint=None,
                )
                for Attempt in Domain.PatternAttempts
            ),
            GeneratedOptionCount=0,
            RejectedOptionCount=len(Domain.PatternAttempts),
        )
        for Domain in Domains
    )


def _Fixture(
    *,
    MaximumGenerationWork: int | None = None,
    TargetX: int = 10,
    PolicyValue=Policy,
):
    Gates = [
        BuildPlacedGate(
            Gate("Source", GateKind.INPUT, ["A"], []),
            0,
            1,
            0,
            0,
            False,
        ),
        BuildPlacedGate(
            Gate("Target", GateKind.OUTPUT, [], ["A"]),
            TargetX,
            1,
            TargetX,
            0,
            False,
        ),
    ]
    Placed = PlacedDesign(Module=None, PlacedGates=Gates, FrozenNetWires={})
    Resources = BuildRoutingResources(Placed, Technology=Technology)
    Domains = EnumeratePlacedPinAccessOptionDomains(
        Gates,
        ResourceGraph=Resources.ResourceGraph,
        Technology=Technology,
        EnabledPatternFamilies=PolicyValue.PlacementAccess.EnabledPatternFamilies,
        CatalogVersion=PolicyValue.PlacementAccess.CatalogVersion,
        PreOwnedNodesBySignal={},
        MaximumGenerationWork=(
            PolicyValue.PlacementAccess.MaximumDomainGenerationWork
            if MaximumGenerationWork is None
            else MaximumGenerationWork
        ),
    )
    Solve = replace(
        SolvePlacedPinAccessOptionDomains(
            Domains,
            ResourceGraph=Resources.ResourceGraph,
            MaximumExpansions=(
                PolicyValue.PlacementAccess.MaximumAssignmentExpansions
            ),
        ),
        PolicyVersion=PolicyValue.PolicyVersion,
    )
    Placement = PcbPlacement(
        Placed=Placed,
        Clusters=(("Source", "Target"),),
        SignalOrder=("A",),
        LayerCount=2,
        SelectedPinAccessWitness=Solve.SelectedWitness,
        PlacementAccessSolve=Solve,
    )
    Envelope = DerivedRoutingEnvelope(
        Demand=PlacementAccessDemand(
            ComponentCount=1,
            TerminalCount=2,
            PeakBoundaryDemand=1,
            CoreBounds=(0, 0, TargetX, TargetX),
            TrackPitch=Technology.TrackPitch,
            AccessLength=Technology.AccessLength,
            MinimumRoutingLayerCount=1,
            MaximumRoutingLayerCount=2,
            TechnologyFingerprint=(
                Solve.SelectedWitness.TechnologyFingerprint
                if Solve.SelectedWitness is not None
                else "incomplete-technology"
            ),
        ),
        RoutingLayerCount=2,
        AccessRingTrackCount=1,
        PermittedLayers=(0, 1),
    )
    Preparation = TrackAssignmentPreparation(
        Success=True,
        SelectedCandidateIds=(("A", "candidate-a"),),
        CandidateCounts=(("A", 1),),
        ConflictSignals=(),
        ConflictResourceIndices=(),
        ExpansionCount=1,
        Complete=True,
        SelectedCapacityResourceIds=("resource-a",),
        PinAccessDomainFingerprint=(
            Solve.SelectedWitness.DomainFingerprint
            if Solve.SelectedWitness is not None
            else ""
        ),
        PinAccessWitnessFingerprint=(
            Solve.SelectedWitness.WitnessFingerprint
            if Solve.SelectedWitness is not None
            else ""
        ),
    )
    return Gates, Placement, Resources, Envelope, Preparation


def _RawSelection(Placement, *, Diagnostics=()):
    Witness = Placement.SelectedPinAccessWitness
    assert Witness is not None
    Position = (40, 1, 0)
    Domain = RawTrackAssignmentDomain(
        ResourcePositions=(Position,),
        Values=(RawTrackAssignmentValue(
            Signal="A",
            CandidateId="track-a",
            Claims=RoutingResourceClaims(WireCells=frozenset({Position})),
            MaterialCost=1,
            FootprintGrowth=1,
            Length=1,
            BendCount=0,
            ViaCount=0,
        ),),
        BaseClaims=(),
        CandidateCounts=(("A", 1),),
        CandidateDomainFingerprint="candidate-domain-current",
        LocalClaimDomainFingerprint="local-domain-current",
        PlacementFingerprint="placement-current",
        ResourceGraphFingerprint="resource-current",
        PortalDomainFingerprint="portal-current",
        Complete=True,
        PinAccessDomainFingerprint=Witness.DomainFingerprint,
        PinAccessWitnessFingerprint=Witness.WitnessFingerprint,
        MaximumAssignmentExpansions=10,
        Diagnostics=Diagnostics,
    )
    Problem = RawTrackAssignmentProblem(
        Templates=(RawTrackAssignmentTemplate(
            TemplateId="Placement-current",
            Objective=(1,),
            Domain=Domain,
        ),),
        MaximumAssignmentExpansions=10,
    )
    Selection = SolveRawTrackAssignmentProblem(
        Problem,
        lambda _Domain, _Remaining: SimpleNamespace(
            Success=True,
            SelectedCandidateIds=(("A", "track-a"),),
            ExpansionCount=1,
            BudgetExhausted=False,
            DeadlineExceeded=False,
            ConflictSignals=(),
            ConflictResourceIndices=(),
            FailureNet="",
        ),
    )
    assert Selection.Preparation is not None
    return Selection


def _ChannelFixture(*, TargetX=20, PolicyValue=Policy):
    _Gates, Placement, _Resources, _Envelope, _Preparation = _Fixture(
        TargetX=TargetX,
        PolicyValue=PolicyValue,
    )
    Module = ModuleIR(
        Name="ChannelAccessFixture",
        Gates=[
            Gate("Source", GateKind.INPUT, ["A"], []),
            Gate("Target", GateKind.OUTPUT, [], ["A"]),
        ],
    )
    SourceGate, TargetGate = Placement.Placed.PlacedGates
    Request = ClusterBoundaryLeaseRequest(
        SourceCluster=0,
        TargetCluster=1,
        Signal="A",
        SourceBoundarySide="east",
        TargetBoundarySide="west",
        SourceTerminal=SourceGate.OutputPin,
        TargetTerminals=(TargetGate.InputPins[0],),
        CompletePinAccess=True,
    )
    Source = replace(
        Placement,
        Clusters=(("Source",), ("Target",)),
        LayerCount=3,
        ClusterBoundaryLeaseRequests=(Request,),
        Placed=replace(
            Placement.Placed,
            Module=Module,
            ClusterBoundaryLeaseRequests=(Request,),
        ),
    )
    Resources = BuildRoutingResources(Source.Placed, Technology=Technology)
    return Source, Resources


def _RefreshAccess(Placement, *, PolicyValue=Policy):
    Cleared = replace(
        Placement,
        PlacementAccessFabric=None,
        PlacementAccessAssignment=None,
        SelectedPinAccessWitness=None,
        PlacementAccessSolve=None,
        Placed=replace(
            Placement.Placed,
            PlacementAccessFabric=None,
            PlacementAccessAssignment=None,
            SelectedPinAccessWitness=None,
            PlacementAccessSolve=None,
        ),
    )
    Resources = BuildRoutingResources(Cleared.Placed, Technology=Technology)
    Domains = EnumeratePlacedPinAccessOptionDomains(
        Cleared.Placed.PlacedGates,
        ResourceGraph=Resources.ResourceGraph,
        Technology=Technology,
        EnabledPatternFamilies=PolicyValue.PlacementAccess.EnabledPatternFamilies,
        CatalogVersion=PolicyValue.PlacementAccess.CatalogVersion,
        PreOwnedNodesBySignal=Cleared.Placed.FrozenNetWires or {},
        MaximumGenerationWork=(
            PolicyValue.PlacementAccess.MaximumDomainGenerationWork
        ),
    )
    Solve = replace(
        SolvePlacedPinAccessOptionDomains(
            Domains,
            ResourceGraph=Resources.ResourceGraph,
            MaximumExpansions=(
                PolicyValue.PlacementAccess.MaximumAssignmentExpansions
            ),
        ),
        PolicyVersion=PolicyValue.PolicyVersion,
    )
    Rebound = replace(
        Cleared,
        SelectedPinAccessWitness=Solve.SelectedWitness,
        PlacementAccessSolve=Solve,
        Placed=replace(
            Cleared.Placed,
            SelectedPinAccessWitness=Solve.SelectedWitness,
            PlacementAccessSolve=Solve,
        ),
    )
    return Rebound, Resources


def _EnvelopeFor(Placement):
    Demand = BuildPlacementAccessDemand(Placement, 0, Technology)
    return next(
        Envelope
        for Envelope in BuildDerivedRoutingEnvelopeDomain(Demand, Placement)
        if Envelope.RoutingLayerCount == Placement.LayerCount
    )


def _PreparationFor(Placement, **Changes):
    Witness = Placement.SelectedPinAccessWitness
    assert Witness is not None
    return replace(
        TrackAssignmentPreparation(
            Success=True,
            SelectedCandidateIds=(("A", "candidate-a"),),
            CandidateCounts=(("A", 1),),
            ConflictSignals=(),
            ConflictResourceIndices=(),
            ExpansionCount=1,
            Complete=True,
            SelectedCapacityResourceIds=("resource-a",),
            PinAccessDomainFingerprint=Witness.DomainFingerprint,
            PinAccessWitnessFingerprint=Witness.WitnessFingerprint,
        ),
        **Changes,
    )


def _ReadyChannelCase(*, TargetX=20, PolicyValue=Policy):
    Source, SourceResources = _ChannelFixture(
        TargetX=TargetX,
        PolicyValue=PolicyValue,
    )
    A = _Build(
        Source,
        SourceResources,
        _EnvelopeFor(Source),
        PolicyValue=PolicyValue,
        SolveBinding=BuildCurrentSelectedAccessSolveBinding(
            PolicyValue,
            Source.PlacementAccessSolve,
        ),
    )
    assert A.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    Channel = BuildBoundedInterClusterRoutingChannel(
        Source,
        ForcedAffectedClusters=(0, 1),
    )
    Deck = BuildBoundedInterClusterRoutingDeck(
        Channel,
        ForcedAffectedClusters=(0, 1),
    )
    Rebound, Resources = _RefreshAccess(Deck, PolicyValue=PolicyValue)
    State = ClusterInterfacePlacementState(
        StateFingerprint="placement-channel-successor",
        InterfaceTopologyFingerprint="interface-channel-successor",
        ChannelFingerprint=Rebound.InterClusterRoutingChannel.ChannelFingerprint,
        InterClusterChannel=Rebound.InterClusterRoutingChannel,
    )
    return Source, A, Channel, Deck, Rebound, Resources, State


def _BuildChannelSuccessor(
    Source,
    A,
    Channel,
    Deck,
    Rebound,
    Resources,
    State,
    **Changes,
):
    Preparation = Changes.pop("Preparation", _DefaultPreparation)
    if Preparation is _DefaultPreparation:
        Preparation = _PreparationFor(Rebound)
    return _Build(
        Rebound,
        Resources,
        _EnvelopeFor(Rebound),
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.ChannelReplacement,
        Preparation=Preparation,
        Predecessor=A,
        CandidateId="ChannelPlacement-current",
        PlacementFingerprint="placement-channel-successor",
        PlacementRetentionFingerprint="retention-channel-successor",
        InterfaceTopologyFingerprint="interface-channel-successor",
        PlacementFingerprintIncludesLocalClaims=True,
        TransitionSourceCandidateId="Placement-current",
        TransitionSourcePlacementFingerprint="placement-current",
        TransitionSourcePlacementRetentionFingerprint=(
            "placement-retention-current"
        ),
        TransitionSourcePlacementFingerprintIncludesLocalClaims=False,
        TransitionSourcePlacement=Source,
        ChannelPlacement=Channel,
        TransitionDeckPlacement=Deck,
        TransitionState=State,
        **Changes,
    )


def _ChangedAccessPolicy(Control):
    Access = Policy.PlacementAccess
    Changes = {
        "CatalogVersion": {
            "CatalogVersion": Access.CatalogVersion + "-alternate",
        },
        "EnabledPatternFamilies": {
            "EnabledPatternFamilies": ("planar-jog", "straight"),
        },
        "Enabled": {"Enabled": False},
        "MaximumDomainGenerationWork": {
            "MaximumDomainGenerationWork": Access.MaximumDomainGenerationWork + 1,
        },
        "MaximumAssignmentExpansions": {
            "MaximumAssignmentExpansions": Access.MaximumAssignmentExpansions + 1,
        },
    }[Control]
    return replace(Policy, PlacementAccess=replace(Access, **Changes))


def _Build(
    Placement,
    Resources,
    Envelope,
    *,
    Phase=CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization,
    Transition=CurrentSelectedAccessTransition.InitialCandidate,
    Preparation=None,
    Predecessor=None,
    PolicyValue=Policy,
    TechnologyValue=Technology,
    PlacementFingerprintIncludesLocalClaims=False,
    PlacementFingerprint="placement-current",
    PlacementRetentionFingerprint="placement-retention-current",
    InterfaceTopologyFingerprint="interface-current",
    CandidateId="Placement-current",
    RawTrackAssignment=None,
    RawTrackAssignmentApplicable=False,
    TransitionSourceCandidateId="",
    TransitionSourcePlacementFingerprint="",
    TransitionSourcePlacementRetentionFingerprint="",
    TransitionSourcePlacementFingerprintIncludesLocalClaims=None,
    TransitionSourcePlacement=None,
    ChannelPlacement=None,
    TransitionDeckPlacement=None,
    TransitionState=None,
    SolveBinding=_DefaultSolveBinding,
):
    if SolveBinding is _DefaultSolveBinding:
        SolveBinding = (
            BuildCurrentSelectedAccessSolveBinding(
                Policy,
                Placement.PlacementAccessSolve,
            )
            if Placement.PlacementAccessSolve is not None
            else None
        )
    return BuildCurrentSelectedAccessEnvelope(
        Phase=Phase,
        Transition=Transition,
        CandidateId=CandidateId,
        SourceGenerator="specification-fixture",
        RoutingSpacing=6,
        PlacementFingerprint=PlacementFingerprint,
        PlacementRetentionFingerprint=PlacementRetentionFingerprint,
        InterfaceTopologyFingerprint=InterfaceTopologyFingerprint,
        ObservedPlacementFingerprint=PlacementFingerprint,
        ObservedPlacementRetentionFingerprint=PlacementRetentionFingerprint,
        PlacementFingerprintIncludesLocalClaims=(
            PlacementFingerprintIncludesLocalClaims
        ),
        Placement=Placement,
        ResourceGraph=Resources.ResourceGraph,
        Technology=TechnologyValue,
        Policy=PolicyValue,
        RoutingEnvelope=Envelope,
        SolveBinding=SolveBinding,
        TrackPreparation=Preparation,
        RawTrackAssignment=RawTrackAssignment,
        RawTrackAssignmentApplicable=RawTrackAssignmentApplicable,
        Predecessor=Predecessor,
        TransitionSourceCandidateId=TransitionSourceCandidateId,
        TransitionSourcePlacementFingerprint=(
            TransitionSourcePlacementFingerprint
        ),
        TransitionSourcePlacementRetentionFingerprint=(
            TransitionSourcePlacementRetentionFingerprint
        ),
        TransitionSourcePlacementFingerprintIncludesLocalClaims=(
            TransitionSourcePlacementFingerprintIncludesLocalClaims
        ),
        TransitionSourcePlacement=TransitionSourcePlacement,
        ChannelPlacement=ChannelPlacement,
        TransitionDeckPlacement=TransitionDeckPlacement,
        TransitionState=TransitionState,
    )


def _CandidateForAccessRefresh(Placement):
    return PcbPlacementCandidate(
        CandidateId="Placement-current",
        SourceGenerator="specification-fixture",
        RoutingSpacing=6,
        PlacementFingerprint="placement-current",
        FeedbackScore=(),
        BoundaryOverflow=0,
        PinScarcityCount=0,
        GuideOverflowPeak=0,
        GuideOverflowCells=0,
        PinEscapeConflictCount=0,
        EstimatedGlobalExtensionNodes=0,
        EstimatedGlobalExtensionNets=0,
        PreOwnedNodeCount=0,
        Placement=Placement,
        PlacementFingerprintIncludesLocalClaims=False,
    )


def _ReadyA(Placement, Resources, Envelope):
    Result = _Build(Placement, Resources, Envelope)
    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    return Result


def _ReadyB(
    Placement,
    Resources,
    Envelope,
    Preparation,
    Predecessor,
    *,
    PolicyValue=Policy,
):
    Result = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.SelectedTrackAssignment,
        Preparation=Preparation,
        Predecessor=Predecessor,
        PolicyValue=PolicyValue,
        SolveBinding=BuildCurrentSelectedAccessSolveBinding(
            PolicyValue,
            Placement.PlacementAccessSolve,
        ),
    )
    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    return Result


def test_ready_phases_own_complete_evidence_bounds_and_causal_history():
    Gates, Placement, Resources, Envelope, Preparation = _Fixture()
    A = _ReadyA(Placement, Resources, Envelope)
    Attached = replace(
        Placement,
        PlacementAccessFabric=_AttachedAccessFacts("fabric-current"),
    )
    B = _ReadyB(Attached, Resources, Envelope, Preparation, A)
    C = _Build(
        Attached,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.BeforePublication,
        Transition=CurrentSelectedAccessTransition.PostRoutingCompaction,
        Preparation=Preparation,
        Predecessor=B,
    )
    ARepeat = _ReadyA(Placement, Resources, Envelope)

    assert C.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    assert C.Reason is CurrentSelectedAccessEnvelopeReason.Current
    assert C.Envelope is not None
    assert C.Envelope.PhysicalValidation.Status.value == "Verified"
    assert C.Envelope.PhysicalValidation.Reason.value == "Current"
    assert C.Envelope.SolveResult.SelectedWitness == C.Envelope.SelectedWitness
    assert C.Envelope.Domains == C.Envelope.SolveResult.Domains
    assert C.Envelope.Policy.PolicyVersion == Policy.PolicyVersion
    assert C.Envelope.Policy.EnabledPatternFamilies == ("straight",)
    assert C.Envelope.Routing.InclusiveRoutingBoundsXZ == Envelope.EnvelopeBounds
    assert C.Envelope.Routing.LogicalRoutingLayers == (0, 1)
    assert C.Envelope.Routing.TrackPreparation == Preparation
    assert [Value.Phase for Value in C.Observations] == [
        CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization,
        CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        CurrentSelectedAccessEnvelopePhase.BeforePublication,
    ]
    assert RequireCurrentSelectedAccessEnvelopeReady(
        C, Stage="specification-publication"
    ) is C.Envelope
    assert ARepeat.ToDictionary() == A.ToDictionary()
    assert ARepeat.Envelope.EnvelopeFingerprint == A.Envelope.EnvelopeFingerprint
    assert B.Envelope.PredecessorEnvelopeFingerprint == A.Envelope.EnvelopeFingerprint
    assert C.Envelope.PredecessorEnvelopeFingerprint == B.Envelope.EnvelopeFingerprint
    assert len({
        A.Envelope.EnvelopeFingerprint,
        B.Envelope.EnvelopeFingerprint,
        C.Envelope.EnvelopeFingerprint,
    }) == 3

    OwnedBeforeMutation = A.Envelope.Candidate.PlacementPayload
    Gates[1].Inputs[0] = "Changed"
    assert A.Envelope.Candidate.PlacementPayload == OwnedBeforeMutation
    assert A.Envelope.SelectedWitness == A.Envelope.SolveResult.SelectedWitness


def test_phase_edges_reject_initial_history_skips_repeats_and_identity_changes():
    _Gates, Placement, Resources, Envelope, Preparation = _Fixture()
    A = _ReadyA(Placement, Resources, Envelope)
    B = _ReadyB(Placement, Resources, Envelope, Preparation, A)
    C = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.BeforePublication,
        Transition=CurrentSelectedAccessTransition.PostRoutingCompaction,
        Preparation=Preparation,
        Predecessor=B,
    )
    assert C.Status is CurrentSelectedAccessEnvelopeStatus.Ready

    Results = (
        _Build(Placement, Resources, Envelope, Predecessor=A),
        _Build(
            Placement,
            Resources,
            Envelope,
            Phase=CurrentSelectedAccessEnvelopePhase.BeforePublication,
            Transition=CurrentSelectedAccessTransition.PostRoutingCompaction,
            Preparation=Preparation,
            Predecessor=A,
        ),
        _Build(
            Placement,
            Resources,
            Envelope,
            Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
            Transition=CurrentSelectedAccessTransition.SelectedTrackAssignment,
            Preparation=Preparation,
            Predecessor=B,
        ),
        _Build(
            Placement,
            Resources,
            Envelope,
            Phase=CurrentSelectedAccessEnvelopePhase.BeforePublication,
            Transition=CurrentSelectedAccessTransition.PostRoutingCompaction,
            Preparation=Preparation,
            Predecessor=B,
            CandidateId="Placement-coincident-but-different",
        ),
    )

    assert all(
        Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
        and Result.Reason is CurrentSelectedAccessEnvelopeReason.UnexpectedPhaseDrift
        and Result.Envelope is None
        for Result in Results
    )


@pytest.mark.parametrize("SolvePolicyVersion", ("", "different-policy"))
def test_ready_requires_the_solve_to_name_the_live_policy(SolvePolicyVersion):
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    Binding = BuildCurrentSelectedAccessSolveBinding(
        Policy,
        Placement.PlacementAccessSolve,
    )
    Mismatched = replace(
        Placement,
        PlacementAccessSolve=replace(
            Placement.PlacementAccessSolve,
            PolicyVersion=SolvePolicyVersion,
        ),
    )

    Result = _Build(
        Mismatched,
        Resources,
        Envelope,
        SolveBinding=Binding,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Reason is CurrentSelectedAccessEnvelopeReason.SolvePolicyMismatch
    assert Result.Envelope is None


@pytest.mark.parametrize(
    "Control",
    (
        "CatalogVersion",
        "EnabledPatternFamilies",
        "Enabled",
        "MaximumDomainGenerationWork",
        "MaximumAssignmentExpansions",
    ),
)
def test_initial_gate_rejects_same_version_policy_control_drift(Control):
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    Binding = BuildCurrentSelectedAccessSolveBinding(
        Policy,
        Placement.PlacementAccessSolve,
    )
    ChangedPolicy = _ChangedAccessPolicy(Control)
    assert ChangedPolicy.PolicyVersion == Policy.PolicyVersion

    Result = _Build(
        Placement,
        Resources,
        Envelope,
        PolicyValue=ChangedPolicy,
        SolveBinding=Binding,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Reason is CurrentSelectedAccessEnvelopeReason.SolvePolicyMismatch
    assert Result.Envelope is None


@pytest.mark.parametrize(
    "Control",
    (
        "CatalogVersion",
        "EnabledPatternFamilies",
        "Enabled",
        "MaximumDomainGenerationWork",
        "MaximumAssignmentExpansions",
    ),
)
def test_channel_gate_rejects_same_version_policy_control_drift(Control):
    Source, A, Channel, Deck, Rebound, Resources, State = _ReadyChannelCase()
    ChangedPolicy = _ChangedAccessPolicy(Control)

    Result = _BuildChannelSuccessor(
        Source,
        A,
        Channel,
        Deck,
        Rebound,
        Resources,
        State,
        PolicyValue=ChangedPolicy,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Reason is CurrentSelectedAccessEnvelopeReason.SolvePolicyMismatch
    assert Result.Envelope is None


@pytest.mark.parametrize(
    "Control",
    (
        "CatalogVersion",
        "EnabledPatternFamilies",
        "Enabled",
        "MaximumDomainGenerationWork",
        "MaximumAssignmentExpansions",
    ),
)
def test_coincident_solve_identity_never_selects_a_newer_policy_binding(Control):
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    ChangedPolicy = _ChangedAccessPolicy(Control)
    OldBinding = BuildCurrentSelectedAccessSolveBinding(
        Policy,
        Placement.PlacementAccessSolve,
    )
    NewerBindingWithCoincidentSolve = BuildCurrentSelectedAccessSolveBinding(
        ChangedPolicy,
        Placement.PlacementAccessSolve,
    )
    assert OldBinding.SolveResultPayload == (
        NewerBindingWithCoincidentSolve.SolveResultPayload
    )
    assert OldBinding.BindingFingerprint != (
        NewerBindingWithCoincidentSolve.BindingFingerprint
    )

    Ambiguous = SelectUnambiguousCurrentSelectedAccessSolveBinding(
        (OldBinding, NewerBindingWithCoincidentSolve),
        Placement.PlacementAccessSolve,
    )
    Result = _Build(
        Placement,
        Resources,
        Envelope,
        PolicyValue=ChangedPolicy,
        SolveBinding=Ambiguous,
    )

    assert Ambiguous is None
    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Reason is CurrentSelectedAccessEnvelopeReason.SolvePolicyMismatch
    assert Result.Envelope is None


@pytest.mark.parametrize(
    "Control",
    (
        "CatalogVersion",
        "EnabledPatternFamilies",
        "MaximumDomainGenerationWork",
        "MaximumAssignmentExpansions",
    ),
)
def test_fresh_solve_under_changed_enabled_policy_is_ready(Control):
    ChangedPolicy = _ChangedAccessPolicy(Control)
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture(
        PolicyValue=ChangedPolicy,
    )
    Binding = BuildCurrentSelectedAccessSolveBinding(
        ChangedPolicy,
        Placement.PlacementAccessSolve,
    )

    Result = _Build(
        Placement,
        Resources,
        Envelope,
        PolicyValue=ChangedPolicy,
        SolveBinding=Binding,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    assert Result.Envelope is not None
    assert Result.Envelope.SolveBinding.BindingFingerprint == (
        Binding.BindingFingerprint
    )
    assert Result.Envelope.Policy.PolicyFingerprint == (
        Binding.Policy.PolicyFingerprint
    )


@pytest.mark.parametrize(
    "Control",
    (
        "CatalogVersion",
        "EnabledPatternFamilies",
        "MaximumDomainGenerationWork",
        "MaximumAssignmentExpansions",
    ),
)
def test_fresh_channel_solve_under_changed_enabled_policy_is_ready(Control):
    ChangedPolicy = _ChangedAccessPolicy(Control)
    Source, A, Channel, Deck, Rebound, Resources, State = _ReadyChannelCase(
        PolicyValue=ChangedPolicy,
    )
    Binding = BuildCurrentSelectedAccessSolveBinding(
        ChangedPolicy,
        Rebound.PlacementAccessSolve,
    )

    Result = _BuildChannelSuccessor(
        Source,
        A,
        Channel,
        Deck,
        Rebound,
        Resources,
        State,
        PolicyValue=ChangedPolicy,
        SolveBinding=Binding,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    assert Result.Envelope is not None
    assert Result.Envelope.SolveBinding.BindingFingerprint == (
        Binding.BindingFingerprint
    )
    assert Result.Envelope.Policy.PolicyFingerprint == (
        Binding.Policy.PolicyFingerprint
    )


def test_disabled_fresh_candidate_is_unavailable_and_missing_binding_is_stale():
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    Missing = _Build(
        Placement,
        Resources,
        Envelope,
        SolveBinding=None,
    )
    DisabledPolicy = _ChangedAccessPolicy("Enabled")
    DisabledPlacement = replace(
        Placement,
        SelectedPinAccessWitness=None,
        PlacementAccessSolve=None,
    )
    Disabled = _Build(
        DisabledPlacement,
        Resources,
        Envelope,
        PolicyValue=DisabledPolicy,
        SolveBinding=None,
    )

    assert Missing.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Missing.Reason is CurrentSelectedAccessEnvelopeReason.SolvePolicyMismatch
    assert Disabled.Status is CurrentSelectedAccessEnvelopeStatus.Unavailable
    assert Disabled.Reason is CurrentSelectedAccessEnvelopeReason.AccessPolicyDisabled


def test_binding_for_another_exact_solve_cannot_authorize_the_candidate():
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    (
        _ForeignGates,
        ForeignPlacement,
        _ForeignResources,
        _ForeignEnvelope,
        _ForeignPreparation,
    ) = _Fixture(TargetX=9)
    ForeignBinding = BuildCurrentSelectedAccessSolveBinding(
        Policy,
        ForeignPlacement.PlacementAccessSolve,
    )

    Result = _Build(
        Placement,
        Resources,
        Envelope,
        SolveBinding=ForeignBinding,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Reason is CurrentSelectedAccessEnvelopeReason.SolvePolicyMismatch
    assert Result.Envelope is None


def test_track_preparation_must_name_the_current_access_witness():
    _Gates, Placement, Resources, Envelope, Preparation = _Fixture()
    A = _ReadyA(Placement, Resources, Envelope)
    Foreign = replace(
        Preparation,
        PinAccessWitnessFingerprint="foreign-witness",
    )

    Result = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.SelectedTrackAssignment,
        Preparation=Foreign,
        Predecessor=A,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Reason is CurrentSelectedAccessEnvelopeReason.TrackPreparationMismatch


def test_declared_channel_replacement_gets_a_new_bound_successor_identity():
    Source, SourceResources = _ChannelFixture()
    SourceEnvelope = _EnvelopeFor(Source)
    A = _ReadyA(Source, SourceResources, SourceEnvelope)
    ChannelPlacement = BuildBoundedInterClusterRoutingChannel(
        Source,
        ForcedAffectedClusters=(0, 1),
    )
    DeckPlacement = BuildBoundedInterClusterRoutingDeck(
        ChannelPlacement,
        ForcedAffectedClusters=(0, 1),
    )
    DeckResources = BuildRoutingResources(
        DeckPlacement.Placed,
        Technology=Technology,
    )
    StaleBeforeRefresh = _Build(
        DeckPlacement,
        DeckResources,
        _EnvelopeFor(DeckPlacement),
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.ChannelReplacement,
        Preparation=_PreparationFor(Source),
        Predecessor=A,
        CandidateId="ChannelPlacement-current",
        PlacementFingerprint="placement-channel-successor",
        PlacementRetentionFingerprint="retention-channel-successor",
        InterfaceTopologyFingerprint="interface-channel-successor",
        PlacementFingerprintIncludesLocalClaims=True,
        TransitionSourceCandidateId="Placement-current",
        TransitionSourcePlacementFingerprint="placement-current",
        TransitionSourcePlacementRetentionFingerprint=(
            "placement-retention-current"
        ),
        TransitionSourcePlacementFingerprintIncludesLocalClaims=False,
        TransitionSourcePlacement=Source,
        ChannelPlacement=ChannelPlacement,
        TransitionDeckPlacement=DeckPlacement,
        TransitionState=ClusterInterfacePlacementState(
            StateFingerprint="placement-channel-successor",
            InterfaceTopologyFingerprint="interface-channel-successor",
            ChannelFingerprint=(
                DeckPlacement.InterClusterRoutingChannel.ChannelFingerprint
            ),
            InterClusterChannel=DeckPlacement.InterClusterRoutingChannel,
        ),
    )
    assert StaleBeforeRefresh.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert StaleBeforeRefresh.Reason is (
        CurrentSelectedAccessEnvelopeReason.PhysicalInputMismatch
    )

    Rebound, Resources = _RefreshAccess(DeckPlacement)
    Envelope = _EnvelopeFor(Rebound)
    Preparation = _PreparationFor(Rebound)
    State = ClusterInterfacePlacementState(
        StateFingerprint="placement-channel-successor",
        InterfaceTopologyFingerprint="interface-channel-successor",
        ChannelFingerprint=Rebound.InterClusterRoutingChannel.ChannelFingerprint,
        InterClusterChannel=Rebound.InterClusterRoutingChannel,
    )
    B = _Build(
        Rebound,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.ChannelReplacement,
        Preparation=Preparation,
        Predecessor=A,
        PlacementFingerprintIncludesLocalClaims=True,
        CandidateId="ChannelPlacement-current",
        PlacementFingerprint="placement-channel-successor",
        PlacementRetentionFingerprint="retention-channel-successor",
        InterfaceTopologyFingerprint="interface-channel-successor",
        TransitionSourceCandidateId="Placement-current",
        TransitionSourcePlacementFingerprint="placement-current",
        TransitionSourcePlacementRetentionFingerprint=(
            "placement-retention-current"
        ),
        TransitionSourcePlacementFingerprintIncludesLocalClaims=False,
        TransitionSourcePlacement=Source,
        ChannelPlacement=ChannelPlacement,
        TransitionDeckPlacement=DeckPlacement,
        TransitionState=State,
    )

    assert B.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    assert B.Envelope is not None
    assert B.Envelope.PredecessorEnvelopeFingerprint == A.Envelope.EnvelopeFingerprint
    assert B.Envelope.EnvelopeFingerprint != A.Envelope.EnvelopeFingerprint
    assert B.Envelope.Candidate.PlacementFingerprintIncludesLocalClaims is True
    assert B.Envelope.SelectedWitness.WitnessFingerprint != (
        A.Envelope.SelectedWitness.WitnessFingerprint
    )
    assert B.Envelope.Routing.RoutingEnvelope.EnvelopeBounds != (
        A.Envelope.Routing.RoutingEnvelope.EnvelopeBounds
    )

    SelectedPredecessor = _ReadyB(
        Source,
        SourceResources,
        SourceEnvelope,
        _PreparationFor(Source),
        A,
    )
    WrongSelectedPredecessor = _BuildChannelSuccessor(
        Source,
        SelectedPredecessor,
        ChannelPlacement,
        DeckPlacement,
        Rebound,
        Resources,
        State,
    )
    RepeatedChannel = _BuildChannelSuccessor(
        Source,
        B,
        ChannelPlacement,
        DeckPlacement,
        Rebound,
        Resources,
        State,
    )
    assert all(
        Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
        and Result.Reason
        is CurrentSelectedAccessEnvelopeReason.UnexpectedPhaseDrift
        and Result.Envelope is None
        for Result in (WrongSelectedPredecessor, RepeatedChannel)
    )


@pytest.mark.parametrize(
    (
        "Outcome",
        "ExpectedStatus",
        "ExpectedReason",
        "ExpectedPhysicalStatus",
        "ExpectedPhysicalReason",
        "ExpectedFailureReason",
    ),
    (
        (
            "incomplete",
            CurrentSelectedAccessEnvelopeStatus.Incomplete,
            CurrentSelectedAccessEnvelopeReason.AccessSolveIncomplete,
            CurrentSelectedPlacementAccessValidationStatus.Unresolved,
            CurrentSelectedPlacementAccessValidationReason.IncompleteSolve,
            RoutingFailureReason.ClusterInterfaceSolveIncomplete,
        ),
        (
            "unsatisfiable",
            CurrentSelectedAccessEnvelopeStatus.Unsatisfiable,
            CurrentSelectedAccessEnvelopeReason.AccessSolveUnsatisfiable,
            CurrentSelectedPlacementAccessValidationStatus.Unresolved,
            CurrentSelectedPlacementAccessValidationReason.UnsatisfiableSolve,
            RoutingFailureReason.NoPinAccessPattern,
        ),
        (
            "feasible-missing-track",
            CurrentSelectedAccessEnvelopeStatus.Incomplete,
            CurrentSelectedAccessEnvelopeReason.MissingTrackPreparation,
            CurrentSelectedPlacementAccessValidationStatus.Verified,
            CurrentSelectedPlacementAccessValidationReason.Current,
            RoutingFailureReason.ClusterInterfaceSolveIncomplete,
        ),
    ),
)
def test_channel_successor_classifies_current_solve_before_track_requirements(
    Outcome,
    ExpectedStatus,
    ExpectedReason,
    ExpectedPhysicalStatus,
    ExpectedPhysicalReason,
    ExpectedFailureReason,
):
    Source, A, Channel, Deck, Rebound, Resources, State = _ReadyChannelCase()
    Solve = Rebound.PlacementAccessSolve
    if Outcome == "incomplete":
        Solve = replace(
            Solve,
            Status=PlacementAccessSolveStatus.Incomplete,
            SearchComplete=False,
            OptimalityProven=False,
            SelectedWitness=None,
            ConflictCore=None,
            IncompleteReason="work-cap",
        )
    elif Outcome == "unsatisfiable":
        EmptyDomains = _AllPlacementAccessPatternsRejected(Solve.Domains)
        Solve = replace(
            SolvePlacedPinAccessOptionDomains(
                EmptyDomains,
                ResourceGraph=Resources.ResourceGraph,
                MaximumExpansions=(
                    Policy.PlacementAccess.MaximumAssignmentExpansions
                ),
            ),
            PolicyVersion=Policy.PolicyVersion,
        )
        assert Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
    else:
        assert Outcome == "feasible-missing-track"

    Current = replace(
        Rebound,
        SelectedPinAccessWitness=Solve.SelectedWitness,
        PlacementAccessSolve=Solve,
        Placed=replace(
            Rebound.Placed,
            SelectedPinAccessWitness=Solve.SelectedWitness,
            PlacementAccessSolve=Solve,
        ),
    )
    Result = _BuildChannelSuccessor(
        Source,
        A,
        Channel,
        Deck,
        Current,
        Resources,
        State,
        Preparation=None,
        SolveBinding=BuildCurrentSelectedAccessSolveBinding(Policy, Solve),
    )

    assert Result.Status is ExpectedStatus
    assert Result.Reason is ExpectedReason
    assert Result.Envelope is None
    Physical = Result.Observations[-1].PhysicalValidation
    assert Physical is not None
    assert Physical.Status is ExpectedPhysicalStatus
    assert Physical.Reason is ExpectedPhysicalReason
    with pytest.raises(RoutingStageError) as Error:
        RequireCurrentSelectedAccessEnvelopeReady(
            Result,
            Stage="channel-successor-status-matrix",
        )
    assert Error.value.Failure.Reason is ExpectedFailureReason


@pytest.mark.parametrize(
    "Mutation",
    (
        "unrelated-successor",
        "gate-delta",
        "pin-delta",
        "local-claim-delta",
        "lease-delta",
        "boundary-delta",
        "template-origin-delta",
        "nested-access-mismatch",
        "missing-intermediate",
        "state-channel-mismatch",
        "enum-only",
    ),
)
def test_channel_successor_requires_the_exact_public_transform_relation(Mutation):
    Source, A, Channel, Deck, Rebound, Resources, State = _ReadyChannelCase()
    if Mutation == "unrelated-successor":
        (
            Source,
            _UnrelatedA,
            Channel,
            Deck,
            Rebound,
            Resources,
            State,
        ) = _ReadyChannelCase(TargetX=24)
    elif Mutation == "gate-delta":
        Gates = list(Deck.Placed.PlacedGates)
        Gates[1] = replace(Gates[1], X=Gates[1].X + 1)
        Deck = replace(Deck, Placed=replace(Deck.Placed, PlacedGates=Gates))
    elif Mutation == "pin-delta":
        Gates = list(Deck.Placed.PlacedGates)
        InputPins = list(Gates[1].InputPins)
        X, Y, Z = InputPins[0]
        InputPins[0] = (X + 1, Y, Z)
        Gates[1] = replace(Gates[1], InputPins=InputPins)
        Deck = replace(Deck, Placed=replace(Deck.Placed, PlacedGates=Gates))
    elif Mutation == "local-claim-delta":
        Root = (0, 1, 3)
        Claim = LocalRouteClaim(
            Signal="A",
            ClusterId=0,
            Root=Root,
            ConnectedTargets=(),
            BoundaryNodes=(Root,),
            Nodes=frozenset({Root}),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(WireCells=frozenset({Root})),
        )
        Channel = replace(
            Channel,
            Placed=replace(Channel.Placed, LocalRouteClaims=(Claim,)),
        )
    elif Mutation == "lease-delta":
        Request = Channel.ClusterBoundaryLeaseRequests[0]
        X, Y, Z = Request.SourceTerminal
        Changed = replace(Request, SourceTerminal=(X + 1, Y, Z))
        Channel = replace(Channel, ClusterBoundaryLeaseRequests=(Changed,))
    elif Mutation == "boundary-delta":
        Channel = replace(
            Channel,
            PackedClusters=(PackedNandCluster(
                ClusterId=0,
                MemberNands=(),
                BoundarySignals=("A",),
                InternalSignals=(),
                RelativePlacements={},
                DirectConnections=(),
                BoundaryTerminals=((0, 1, 3),),
            ),),
        )
    elif Mutation == "template-origin-delta":
        Channel = replace(
            Channel,
            ClusterLocalRouteTemplates=(ClusterLocalRouteTemplate(
                ClusterId=0,
                StructuralSignature="fixture",
                Rotation=0,
                MirrorX=False,
                Origin=(1, 1, 1),
                LocalClaimFingerprint="local-current",
                BoundaryTerminalFingerprint="boundary-current",
                ClaimCount=0,
                BoundaryTerminalCount=0,
            ),),
        )
    elif Mutation == "nested-access-mismatch":
        Rebound = replace(
            Rebound,
            Placed=replace(
                Rebound.Placed,
                SelectedPinAccessWitness=None,
                PlacementAccessSolve=None,
            ),
        )
    elif Mutation == "missing-intermediate":
        Channel = None
    elif Mutation == "state-channel-mismatch":
        State = replace(State, ChannelFingerprint="foreign-channel")
    else:
        assert Mutation == "enum-only"
        Source = None
        Channel = None
        Deck = None
        State = None

    Result = _BuildChannelSuccessor(
        Source,
        A,
        Channel,
        Deck,
        Rebound,
        Resources,
        State,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Reason is CurrentSelectedAccessEnvelopeReason.PlacementTransitionMismatch
    assert Result.Envelope is None


def test_complete_raw_assignment_is_owned_and_changed_raw_authority_is_stale():
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    A = _ReadyA(Placement, Resources, Envelope)
    Raw = _RawSelection(Placement)
    Preparation = Raw.Preparation
    B = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.SelectedTrackAssignment,
        Preparation=Preparation,
        Predecessor=A,
        RawTrackAssignment=Raw,
        RawTrackAssignmentApplicable=True,
    )
    assert B.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    assert B.Envelope.Routing.RawTrackAssignment == Raw

    Changed = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.BeforePublication,
        Transition=CurrentSelectedAccessTransition.PostRoutingCompaction,
        Preparation=Preparation,
        Predecessor=B,
        RawTrackAssignment=replace(
            Raw,
            ProblemFingerprint="raw-problem-changed",
        ),
        RawTrackAssignmentApplicable=True,
    )
    assert Changed.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Changed.Envelope is None


def test_truthy_raw_double_is_rejected_and_authoritative_preparation_is_bound():
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    A = _ReadyA(Placement, Resources, Envelope)
    Raw = _RawSelection(Placement)
    assert Raw.Preparation is not None

    with pytest.raises(TypeError):
        _Build(
            Placement,
            Resources,
            Envelope,
            Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
            Transition=CurrentSelectedAccessTransition.SelectedTrackAssignment,
            Preparation=Raw.Preparation,
            Predecessor=A,
            RawTrackAssignment=_RawTrackFacts("truthy-double"),
            RawTrackAssignmentApplicable=True,
        )

    DiagnosticOnly = replace(
        Raw.Preparation,
        Diagnostics=(("Note", {"value": "external-only"}),),
    )
    Current = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.SelectedTrackAssignment,
        Preparation=DiagnosticOnly,
        Predecessor=A,
        RawTrackAssignment=Raw,
        RawTrackAssignmentApplicable=True,
    )
    assert Current.Status is CurrentSelectedAccessEnvelopeStatus.Ready

    DifferentAuthority = replace(
        Raw.Preparation,
        CandidateDomainFingerprint="different-candidate-domain",
    )
    Stale = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.SelectedTrackAssignment,
        Preparation=DifferentAuthority,
        Predecessor=A,
        RawTrackAssignment=Raw,
        RawTrackAssignmentApplicable=True,
    )
    assert Stale.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Stale.Reason is CurrentSelectedAccessEnvelopeReason.RawTrackAssignmentMismatch


def test_fresh_but_different_full_witness_and_solve_are_phase_stale():
    _Gates, Placement, Resources, Envelope, Preparation = _Fixture()
    A = _ReadyA(Placement, Resources, Envelope)
    B = _ReadyB(Placement, Resources, Envelope, Preparation, A)
    (
        _ChangedGates,
        ChangedPlacement,
        ChangedResources,
        ChangedEnvelope,
        ChangedPreparation,
    ) = _Fixture(TargetX=9)

    Result = _Build(
        ChangedPlacement,
        ChangedResources,
        ChangedEnvelope,
        Phase=CurrentSelectedAccessEnvelopePhase.BeforePublication,
        Transition=CurrentSelectedAccessTransition.PostRoutingCompaction,
        Preparation=ChangedPreparation,
        Predecessor=B,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Reason is CurrentSelectedAccessEnvelopeReason.UnexpectedPhaseDrift
    assert Result.Observations[-1].PhysicalValidation.Status.value == "Verified"
    assert Result.Envelope is None


def test_track_diagnostics_are_retained_but_are_not_assignment_authority():
    _Gates, Placement, Resources, Envelope, Preparation = _Fixture()
    A = _ReadyA(Placement, Resources, Envelope)
    WithDiagnostics = replace(
        Preparation,
        Diagnostics=(("Note", {"value": "before"}),),
    )
    B = _ReadyB(Placement, Resources, Envelope, WithDiagnostics, A)
    ChangedDiagnostics = replace(
        WithDiagnostics,
        Diagnostics=(("Note", {"value": "after"}),),
    )
    C = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.BeforePublication,
        Transition=CurrentSelectedAccessTransition.PostRoutingCompaction,
        Preparation=ChangedDiagnostics,
        Predecessor=B,
    )

    assert C.Status is CurrentSelectedAccessEnvelopeStatus.Ready
    assert C.Envelope.Routing.TrackPreparationPayload != (
        B.Envelope.Routing.TrackPreparationPayload
    )
    assert C.Envelope.Routing.TrackPreparationAuthorityPayload == (
        B.Envelope.Routing.TrackPreparationAuthorityPayload
    )


@pytest.mark.parametrize(
    "Mutation",
    (
        "nested-placement",
        "resource-state",
        "frozen-wires",
        "technology",
        "access-policy",
        "solve-result",
        "track-preparation",
    ),
)
def test_fresh_successor_rejects_changed_current_authority(Mutation):
    Gates, Placement, Resources, Envelope, Preparation = _Fixture()
    A = _ReadyA(Placement, Resources, Envelope)
    B = _ReadyB(Placement, Resources, Envelope, Preparation, A)
    CurrentPlacement = Placement
    CurrentResources = Resources
    CurrentTechnology = Technology
    CurrentPolicy = Policy
    CurrentPreparation = Preparation
    CurrentSolveBinding = _DefaultSolveBinding
    if Mutation == "nested-placement":
        Gates[1].InputPins[0] = (11, 1, 10)
    elif Mutation == "resource-state":
        CurrentResources = replace(
            Resources,
            ResourceGraph=replace(
                Resources.ResourceGraph,
                BlockStates={
                    (0, 0, 0): {"Name": "minecraft:stone"},
                },
            ),
        )
    elif Mutation == "frozen-wires":
        Position = next(iter(Resources.ResourceGraph.ElectricalBlocks))
        CurrentPlacement = replace(
            Placement,
            Placed=replace(
                Placement.Placed,
                FrozenNetWires={"Foreign": (Position,)},
            ),
        )
    elif Mutation == "technology":
        CurrentTechnology = replace(Technology, TrackPitch=Technology.TrackPitch + 1)
        CurrentResources = replace(
            Resources,
            ResourceGraph=replace(
                Resources.ResourceGraph,
                Technology=CurrentTechnology,
            ),
        )
    elif Mutation == "access-policy":
        CurrentPolicy = replace(
            Policy,
            PlacementAccess=replace(
                Policy.PlacementAccess,
                MaximumAssignmentExpansions=(
                    Policy.PlacementAccess.MaximumAssignmentExpansions + 1
                ),
            ),
        )
    elif Mutation == "solve-result":
        CurrentSolveBinding = B.Envelope.SolveBinding
        CurrentPlacement = replace(
            Placement,
            PlacementAccessSolve=replace(
                Placement.PlacementAccessSolve,
                PolicyVersion="changed-policy-version",
            ),
        )
    elif Mutation == "track-preparation":
        CurrentPreparation = replace(
            Preparation,
            SelectedCapacityResourceIds=("changed-resource",),
        )

    Result = _Build(
        CurrentPlacement,
        CurrentResources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.BeforePublication,
        Transition=CurrentSelectedAccessTransition.PostRoutingCompaction,
        Preparation=CurrentPreparation,
        Predecessor=B,
        PolicyValue=CurrentPolicy,
        TechnologyValue=CurrentTechnology,
        SolveBinding=CurrentSolveBinding,
    )

    assert Result.Status is CurrentSelectedAccessEnvelopeStatus.Stale
    assert Result.Envelope is None
    with pytest.raises(RoutingStageError) as Error:
        RequireCurrentSelectedAccessEnvelopeReady(
            Result, Stage="specification-publication"
        )
    assert Error.value.Failure.Stage == "specification-publication"
    assert Error.value.Failure.Diagnostics["CurrentSelectedAccessStatus"] == "Stale"


def test_unknown_fingerprint_mode_and_v2_graph_are_unavailable_not_ready():
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    UnknownMode = _Build(
        Placement,
        Resources,
        Envelope,
        PlacementFingerprintIncludesLocalClaims=None,
    )
    UnsupportedGraph = _Build(
        Placement,
        replace(
            Resources,
            ResourceGraph=replace(
                Resources.ResourceGraph,
                GraphVersion="routing-resource-graph-v2",
            ),
        ),
        Envelope,
    )

    assert UnknownMode.Status is CurrentSelectedAccessEnvelopeStatus.Unavailable
    assert UnknownMode.Reason is (
        CurrentSelectedAccessEnvelopeReason.PlacementFingerprintModeUnproven
    )
    assert UnsupportedGraph.Status is CurrentSelectedAccessEnvelopeStatus.Unavailable
    assert UnsupportedGraph.Reason is (
        CurrentSelectedAccessEnvelopeReason.UnsupportedResourceGraphVersion
    )
    assert UnknownMode.Envelope is None
    assert UnsupportedGraph.Envelope is None


def test_missing_predecessor_cannot_fallback():
    _Gates, Placement, Resources, Envelope, Preparation = _Fixture()
    MissingPredecessor = _Build(
        Placement,
        Resources,
        Envelope,
        Phase=CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor,
        Transition=CurrentSelectedAccessTransition.SelectedTrackAssignment,
        Preparation=Preparation,
    )

    assert MissingPredecessor.Status is (
        CurrentSelectedAccessEnvelopeStatus.Unavailable
    )
    assert MissingPredecessor.Reason is (
        CurrentSelectedAccessEnvelopeReason.MissingReadyPredecessor
    )
    assert MissingPredecessor.Envelope is None


@pytest.mark.parametrize(
    ("Outcome", "ExpectedStatus", "ExpectedReason", "ExpectedPhysicalReason"),
    (
        (
            "work-cap",
            CurrentSelectedAccessEnvelopeStatus.Incomplete,
            CurrentSelectedAccessEnvelopeReason.AccessSolveIncomplete,
            CurrentSelectedPlacementAccessValidationReason.IncompleteSolve,
        ),
        (
            "unsatisfiable",
            CurrentSelectedAccessEnvelopeStatus.Unsatisfiable,
            CurrentSelectedAccessEnvelopeReason.AccessSolveUnsatisfiable,
            CurrentSelectedPlacementAccessValidationReason.UnsatisfiableSolve,
        ),
    ),
)
def test_nonfeasible_fresh_access_solve_remains_typed_non_ready(
    Outcome,
    ExpectedStatus,
    ExpectedReason,
    ExpectedPhysicalReason,
):
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    if Outcome == "work-cap":
        Solve = replace(
            Placement.PlacementAccessSolve,
            Status=PlacementAccessSolveStatus.Incomplete,
            SearchComplete=False,
            OptimalityProven=False,
            SelectedWitness=None,
            IncompleteReason="work-cap",
        )
    else:
        EmptyDomains = _AllPlacementAccessPatternsRejected(
            Placement.PlacementAccessSolve.Domains
        )
        Solve = replace(
            SolvePlacedPinAccessOptionDomains(
                EmptyDomains,
                ResourceGraph=Resources.ResourceGraph,
                MaximumExpansions=(
                    Policy.PlacementAccess.MaximumAssignmentExpansions
                ),
            ),
            PolicyVersion=Policy.PolicyVersion,
        )
        assert Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
    Current = replace(
        Placement,
        SelectedPinAccessWitness=None,
        PlacementAccessSolve=Solve,
    )

    Result = _Build(Current, Resources, Envelope)

    assert Result.Status is ExpectedStatus
    assert Result.Reason is ExpectedReason
    assert Result.Observations[-1].PhysicalValidation.Reason is ExpectedPhysicalReason
    assert Result.Envelope is None


@pytest.mark.parametrize("Outcome", ("feasible", "unsatisfiable"))
def test_truncated_same_family_manifest_cannot_authorize_current_access(
    Outcome,
):
    RichPolicy = _ChangedAccessPolicy("EnabledPatternFamilies")
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture(
        PolicyValue=RichPolicy,
    )
    OriginalSolve = Placement.PlacementAccessSolve
    TruncatedDomains = []
    for Domain in OriginalSolve.Domains:
        Manifest = tuple(
            Requirement
            for Requirement in Domain.RequiredPatternManifest
            if not Requirement.TemplateId.endswith("PlanarJogPositive")
        )
        AttemptIds = {Value.AttemptId for Value in Manifest}
        Attempts = tuple(
            Attempt
            for Attempt in Domain.PatternAttempts
            if Attempt.AttemptId in AttemptIds
        )
        Options = tuple(
            Option
            for Option in Domain.Options
            if Option.TemplateId != (
                f"{Option.GateKind}:"
                f"{Option.PinId}PlanarJogPositive"
            )
        )
        if Outcome == "unsatisfiable":
            Attempts = tuple(
                replace(
                    Attempt,
                    Status=PlacementAccessPatternAttemptStatus.Rejected,
                    Reason=(
                        PlacementAccessPatternAttemptReason.
                        TerminalOrBridgeUnavailable
                    ),
                    OptionFingerprint=None,
                )
                for Attempt in Attempts
            )
            Options = ()
        TruncatedDomains.append(replace(
            Domain,
            RequiredPatternManifest=Manifest,
            PatternAttempts=Attempts,
            Options=Options,
            GeneratedOptionCount=(len(Options) if Outcome == "feasible" else 0),
            RejectedOptionCount=(
                len(Attempts) if Outcome == "unsatisfiable" else 0
            ),
        ))
    TruncatedDomains = tuple(sorted(
        TruncatedDomains,
        key=lambda Value: Value.DomainId,
    ))
    if Outcome == "feasible":
        Witness = replace(
            OriginalSolve.SelectedWitness,
            Domains=TruncatedDomains,
            DomainFingerprints=tuple(sorted(
                Domain.DomainFingerprint for Domain in TruncatedDomains
            )),
        )
        Solve = replace(
            OriginalSolve,
            Domains=TruncatedDomains,
            ProblemFingerprint=BuildPlacementAccessProblemFingerprint(
                TruncatedDomains
            ),
            SelectedWitness=Witness,
        )
    else:
        Solve = replace(
            SolvePlacedPinAccessOptionDomains(
                TruncatedDomains,
                ResourceGraph=Resources.ResourceGraph,
                MaximumExpansions=(
                    RichPolicy.PlacementAccess.MaximumAssignmentExpansions
                ),
            ),
            PolicyVersion=RichPolicy.PolicyVersion,
        )
        assert Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
    Current = replace(
        Placement,
        SelectedPinAccessWitness=Solve.SelectedWitness,
        PlacementAccessSolve=Solve,
    )

    Result = _Build(
        Current,
        Resources,
        Envelope,
        PolicyValue=RichPolicy,
        SolveBinding=BuildCurrentSelectedAccessSolveBinding(
            RichPolicy,
            Solve,
        ),
    )

    assert Result.Status not in {
        CurrentSelectedAccessEnvelopeStatus.Ready,
        CurrentSelectedAccessEnvelopeStatus.Unsatisfiable,
    }
    assert Result.Envelope is None
    Physical = Result.Observations[-1].PhysicalValidation
    assert Physical.Status is CurrentSelectedPlacementAccessValidationStatus.Mismatch
    assert Physical.Reason is (
        CurrentSelectedPlacementAccessValidationReason.
        RequiredPatternManifestMismatch
    )
    with pytest.raises(RoutingStageError) as Error:
        RequireCurrentSelectedAccessEnvelopeReady(
            Result,
            Stage="truncated-pattern-manifest",
        )
    assert Error.value.Failure.Reason is not RoutingFailureReason.NoPinAccessPattern


def test_final_candidate_access_refresh_propagates_deadline_failure():
    _Gates, Placement, Resources, _Envelope, _Preparation = _Fixture()
    Candidate = _CandidateForAccessRefresh(Placement)
    Context = SimpleNamespace(
        Policy=Policy,
        Technology=Technology,
        PlacementAccessDomainsByProblemFingerprint={},
        PlacementAccessSolveResultsByProblemFingerprint={},
    )
    Failure = RoutingStageError(RoutingFailure(
        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
        Stage="ClusterInterfacePlacementAccessRefresh",
        Detail="bounded deadline expired",
    ))

    def Stop(_Diagnostics):
        raise Failure

    with pytest.raises(RoutingStageError) as Error:
        RebuildCurrentCandidatePlacementAccess(
            Context,
            Candidate,
            Resources=Resources,
            WorkCheck=Stop,
        )

    assert Error.value is Failure


def test_final_candidate_access_refresh_owns_its_producer_policy_binding():
    ChangedPolicy = _ChangedAccessPolicy("MaximumDomainGenerationWork")
    _Gates, Placement, Resources, _Envelope, _Preparation = _Fixture()
    Candidate = _CandidateForAccessRefresh(Placement)
    Context = SimpleNamespace(
        Policy=ChangedPolicy,
        Technology=Technology,
        PlacementAccessDomainsByProblemFingerprint={},
        PlacementAccessSolveResultsByProblemFingerprint={},
    )

    Refreshed = RebuildCurrentCandidatePlacementAccess(
        Context,
        Candidate,
        Resources=Resources,
        WorkCheck=lambda _Diagnostics: None,
    )

    Binding = Refreshed.PlacementAccessSolveBinding
    assert Binding is not None
    assert Binding.Policy.Policy == ChangedPolicy
    assert Binding.Policy.PolicyFingerprint != (
        BuildCurrentSelectedAccessSolveBinding(
            Policy,
            Placement.PlacementAccessSolve,
        ).Policy.PolicyFingerprint
    )
    assert Binding.SolveResult == Refreshed.Placement.PlacementAccessSolve
    assert Binding.ProblemFingerprint == (
        Refreshed.Placement.PlacementAccessSolve.ProblemFingerprint
    )
    assert Binding.WitnessFingerprint == (
        Refreshed.Placement.SelectedPinAccessWitness.WitnessFingerprint
    )


def test_malformed_nested_authority_propagates_instead_of_becoming_status():
    _Gates, Placement, Resources, Envelope, _Preparation = _Fixture()
    Malformed = replace(
        Placement,
        Placed=replace(
            Placement.Placed,
            FrozenNetWires={"A": (((0, 0, 0) for _Value in range(1)))},
        ),
    )

    with pytest.raises(TypeError):
        _Build(Malformed, Resources, Envelope)

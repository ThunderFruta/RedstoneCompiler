"""Focused contracts for frozen derived-perimeter access geometry."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import Compiler.Placement.AccessFabric as AccessFabricModule

from Compiler.Ir.Models import Gate, GateKind, ModuleIR
from Compiler.Placement.AccessFabric import (
    BuildPlacementAccessAssignmentFromStubFactor,
    _DerivePerimeterRootAccessFace,
    BuildDerivedPerimeterFabricShell,
    BuildPlacementAccessFabric,
    AttachPlacementAccessFabric,
    MeasureDerivedPerimeterInterfaceDemand,
    MeasureDerivedPerimeterInterfaceLaunchDemandByFace,
)
from Compiler.Placement.Geometry import BuildPlacedGate, PlacedDesign
from Compiler.Placement.Pcb import PcbPlacement
from Compiler.Placement.PcbFlow import BuildPlacementAccessDemand
from Compiler.Placement.PreRouteInterface import (
    DerivedPerimeterFaceReservation,
    DerivedPerimeterSlotAssignment,
    DerivedPerimeterTerminalSlot,
    DeriveRoutingEnvelopes,
)
from Compiler.Placement.Rotation import RotatedCellSize
from Compiler.Routing.Actions.Geometry import BuildRoutingResources
from Compiler.Routing.ChannelPlanner import BuildNetRoutingProfiles
from Compiler.Routing.ResourceGraph import FindSelfClaimConflicts
from Compiler.Routing.Models import RoutedDesign
from Compiler.Routing.Pcb import RoutePcbAttempt
from Compiler.Routing.Policy import (
    BuildRoutingAttemptPolicies,
    LocalFirstPhysicalDesignPolicy,
)
from Compiler.Routing.Reliability import RoutingDeadline
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology


def BuildNorthFacingPerimeterFixture() -> tuple[
    PcbPlacement,
    DerivedPerimeterTerminalSlot,
    DerivedPerimeterSlotAssignment,
]:
    """Build one real macro fixture with a north-only terminal aperture."""
    InputGate = Gate("InputIn", GateKind.INPUT, ["In"])
    CoreGate = Gate("Core", GateKind.NAND, ["Result"], ["In", "In"])
    PlacedInput = BuildPlacedGate(
        InputGate,
        0,
        1,
        -5,
        180,
        False,
    )
    PlacedCore = BuildPlacedGate(CoreGate, 0, 1, 0, 0, False)
    assert PlacedInput.OutputPin is not None
    assert PlacedInput.OutputDirection == (0, 0, -1)
    Slot = DerivedPerimeterTerminalSlot(
        SlotId="input-north",
        TerminalName="InputIn",
        Signal="In",
        Face="north",
        Origin=(PlacedInput.X, PlacedInput.Y, PlacedInput.Z),
        Rotation=PlacedInput.Rotation,
        MirrorX=PlacedInput.MirrorX,
        MacroBounds=(0, -5, 0, -3),
        ConnectionPin=PlacedInput.OutputPin,
        ConnectionDirection=PlacedInput.OutputDirection,
        InteriorSpan=0,
    )
    Reservation = DerivedPerimeterFaceReservation(
        Face="north",
        NormalCoordinate=Slot.ConnectionPin[2],
        LateralMinimum=Slot.MacroBounds[0],
        LateralMaximum=Slot.MacroBounds[2],
        TerminalNames=(Slot.TerminalName,),
        SlotIds=(Slot.SlotId,),
    )
    Assignment = DerivedPerimeterSlotAssignment(
        DomainFingerprint="north-only-domain",
        AssignmentFingerprint="north-only-assignment",
        CoreBounds=(0, 0, 2, 3),
        SelectedSlots=(Slot,),
        FaceReservations=(Reservation,),
        Bounds=(0, -5, 2, 3),
        Objective=(1,),
        ExpansionCount=1,
        Success=True,
        Complete=True,
    )
    Module = ModuleIR(
        Name="NorthOnlyPerimeter",
        Inputs=["In"],
        Outputs=["Result"],
        Gates=[InputGate, CoreGate],
    )
    Placed = PlacedDesign(
        Module=Module,
        PlacedGates=[PlacedInput, PlacedCore],
        DerivedPerimeterSlotAssignment=Assignment,
    )
    return (
        PcbPlacement(
            Placed=Placed,
            Clusters=(("Core",),),
            SignalOrder=("In", "Result"),
            LayerCount=1,
            DerivedPerimeterSlotAssignment=Assignment,
        ),
        Slot,
        Assignment,
    )


def BuildSouthFacingPerimeterRootFixture() -> tuple[
    PcbPlacement,
    tuple[int, int, int],
    tuple[int, int, int],
]:
    """Build a core producer feeding one frozen south-facing output slot."""
    CoreGate = Gate("Core", GateKind.NAND, ["Result"], ["In", "In"])
    OutputGate = Gate("OutputResult", GateKind.OUTPUT, [], ["Result"])
    PlacedCore = BuildPlacedGate(CoreGate, 0, 1, 0, 0, False)
    PlacedOutput = BuildPlacedGate(OutputGate, 0, 1, 6, 180, False)
    assert PlacedCore.OutputPin is not None
    assert PlacedCore.OutputDirection == (0, 0, 1)
    assert PlacedOutput.InputPins
    assert PlacedOutput.InputDirections == [(0, 0, 1)]
    OutputWidth, OutputDepth = RotatedCellSize(
        PlacedOutput.Kind,
        PlacedOutput.Rotation,
    )
    CoreWidth, CoreDepth = RotatedCellSize(
        PlacedCore.Kind,
        PlacedCore.Rotation,
    )
    Slot = DerivedPerimeterTerminalSlot(
        SlotId="result-south",
        TerminalName="OutputResult",
        Signal="Result",
        Face="south",
        Origin=(PlacedOutput.X, PlacedOutput.Y, PlacedOutput.Z),
        Rotation=PlacedOutput.Rotation,
        MirrorX=PlacedOutput.MirrorX,
        MacroBounds=(
            PlacedOutput.X,
            PlacedOutput.Z,
            PlacedOutput.X + OutputWidth - 1,
            PlacedOutput.Z + OutputDepth - 1,
        ),
        ConnectionPin=PlacedOutput.InputPins[0],
        ConnectionDirection=PlacedOutput.InputDirections[0],
        InteriorSpan=0,
    )
    Reservation = DerivedPerimeterFaceReservation(
        Face="south",
        NormalCoordinate=Slot.ConnectionPin[2],
        LateralMinimum=Slot.ConnectionPin[0],
        LateralMaximum=Slot.ConnectionPin[0],
        TerminalNames=(Slot.TerminalName,),
        SlotIds=(Slot.SlotId,),
    )
    Assignment = DerivedPerimeterSlotAssignment(
        DomainFingerprint="south-root-domain",
        AssignmentFingerprint="south-root-assignment",
        CoreBounds=(
            PlacedCore.X,
            PlacedCore.Z,
            PlacedCore.X + CoreWidth - 1,
            PlacedCore.Z + CoreDepth - 1,
        ),
        SelectedSlots=(Slot,),
        FaceReservations=(Reservation,),
        Bounds=(
            min(PlacedCore.X, PlacedOutput.X),
            min(PlacedCore.Z, PlacedOutput.Z),
            max(
                PlacedCore.X + CoreWidth - 1,
                PlacedOutput.X + OutputWidth - 1,
            ),
            max(
                PlacedCore.Z + CoreDepth - 1,
                PlacedOutput.Z + OutputDepth - 1,
            ),
        ),
        Objective=(1,),
        ExpansionCount=1,
        Success=True,
        Complete=True,
    )
    Module = ModuleIR(
        Name="SouthFacingPerimeterRoot",
        Inputs=["In"],
        Outputs=["Result"],
        Gates=[CoreGate, OutputGate],
    )
    Placed = PlacedDesign(
        Module=Module,
        PlacedGates=[PlacedCore, PlacedOutput],
        DerivedPerimeterSlotAssignment=Assignment,
    )
    return (
        PcbPlacement(
            Placed=Placed,
            Clusters=(("Core",),),
            SignalOrder=("In", "Result"),
            LayerCount=2,
            DerivedPerimeterSlotAssignment=Assignment,
        ),
        PlacedCore.OutputPin,
        Slot.ConnectionPin,
    )


def BuildOppositeFacePerimeterRootFixture() -> tuple[
    PcbPlacement,
    tuple[int, int, int],
    tuple[int, int, int],
]:
    """Build a north slot whose paired source root faces south.

    This deliberately makes the target aperture and the signal producer use
    opposite perimeter faces.  The source's actual access landing must grow
    the south ring plane even though the frozen I/O slot only reserves north.
    """
    CoreGate = Gate("Core", GateKind.NAND, ["Result"], ["In", "In"])
    OutputGate = Gate("OutputResult", GateKind.OUTPUT, [], ["Result"])
    PlacedCore = BuildPlacedGate(CoreGate, 0, 1, 0, 0, False)
    PlacedOutput = BuildPlacedGate(OutputGate, 0, 1, -6, 0, False)
    assert PlacedCore.OutputPin is not None
    assert PlacedCore.OutputDirection == (0, 0, 1)
    assert PlacedOutput.InputPins
    assert PlacedOutput.InputDirections == [(0, 0, -1)]
    OutputWidth, OutputDepth = RotatedCellSize(
        PlacedOutput.Kind,
        PlacedOutput.Rotation,
    )
    CoreWidth, CoreDepth = RotatedCellSize(
        PlacedCore.Kind,
        PlacedCore.Rotation,
    )
    Slot = DerivedPerimeterTerminalSlot(
        SlotId="result-north",
        TerminalName="OutputResult",
        Signal="Result",
        Face="north",
        Origin=(PlacedOutput.X, PlacedOutput.Y, PlacedOutput.Z),
        Rotation=PlacedOutput.Rotation,
        MirrorX=PlacedOutput.MirrorX,
        MacroBounds=(
            PlacedOutput.X,
            PlacedOutput.Z,
            PlacedOutput.X + OutputWidth - 1,
            PlacedOutput.Z + OutputDepth - 1,
        ),
        ConnectionPin=PlacedOutput.InputPins[0],
        ConnectionDirection=PlacedOutput.InputDirections[0],
        InteriorSpan=0,
    )
    Reservation = DerivedPerimeterFaceReservation(
        Face="north",
        NormalCoordinate=Slot.ConnectionPin[2],
        LateralMinimum=Slot.ConnectionPin[0],
        LateralMaximum=Slot.ConnectionPin[0],
        TerminalNames=(Slot.TerminalName,),
        SlotIds=(Slot.SlotId,),
    )
    Assignment = DerivedPerimeterSlotAssignment(
        DomainFingerprint="opposite-root-domain",
        AssignmentFingerprint="opposite-root-assignment",
        CoreBounds=(
            PlacedCore.X,
            PlacedCore.Z,
            PlacedCore.X + CoreWidth - 1,
            PlacedCore.Z + CoreDepth - 1,
        ),
        SelectedSlots=(Slot,),
        FaceReservations=(Reservation,),
        Bounds=(
            min(PlacedCore.X, PlacedOutput.X),
            min(PlacedCore.Z, PlacedOutput.Z),
            max(
                PlacedCore.X + CoreWidth - 1,
                PlacedOutput.X + OutputWidth - 1,
            ),
            max(
                PlacedCore.Z + CoreDepth - 1,
                PlacedOutput.Z + OutputDepth - 1,
            ),
        ),
        Objective=(1,),
        ExpansionCount=1,
        Success=True,
        Complete=True,
    )
    Module = ModuleIR(
        Name="OppositeFacePerimeterRoot",
        Inputs=["In"],
        Outputs=["Result"],
        Gates=[CoreGate, OutputGate],
    )
    Placed = PlacedDesign(
        Module=Module,
        PlacedGates=[PlacedCore, PlacedOutput],
        DerivedPerimeterSlotAssignment=Assignment,
    )
    return (
        PcbPlacement(
            Placed=Placed,
            Clusters=(("Core",),),
            SignalOrder=("In", "Result"),
            LayerCount=2,
            DerivedPerimeterSlotAssignment=Assignment,
        ),
        PlacedCore.OutputPin,
        Slot.ConnectionPin,
    )


def BuildAsymmetricPerimeterLaunchFixture() -> PcbPlacement:
    """Build two north targets with their one producer root on south.

    The fixture deliberately has uneven demand: two selected target slots
    consume north-face launch capacity, while their shared root contributes
    one distinct south-face launch.  It is a real placed net profile rather
    than a synthetic face map so the focused contract tracks the shell's
    signal-closed endpoint derivation.
    """
    CoreGate = Gate("Core", GateKind.NAND, ["Result"], ["In", "In"])
    LeftOutputGate = Gate(
        "OutputLeft",
        GateKind.OUTPUT,
        [],
        ["Result"],
    )
    RightOutputGate = Gate(
        "OutputRight",
        GateKind.OUTPUT,
        [],
        ["Result"],
    )
    PlacedCore = BuildPlacedGate(CoreGate, 0, 1, 0, 0, False)
    PlacedLeftOutput = BuildPlacedGate(
        LeftOutputGate,
        0,
        1,
        -6,
        0,
        False,
    )
    PlacedRightOutput = BuildPlacedGate(
        RightOutputGate,
        6,
        1,
        -6,
        0,
        False,
    )
    assert PlacedCore.OutputPin is not None
    assert PlacedCore.OutputDirection == (0, 0, 1)

    def BuildNorthSlot(
        GateName: str,
        PlacedGate: object,
    ) -> DerivedPerimeterTerminalSlot:
        Width, Depth = RotatedCellSize(
            PlacedGate.Kind,
            PlacedGate.Rotation,
        )
        assert PlacedGate.InputPins
        assert PlacedGate.InputDirections == [(0, 0, -1)]
        return DerivedPerimeterTerminalSlot(
            SlotId=GateName,
            TerminalName=GateName,
            Signal="Result",
            Face="north",
            Origin=(PlacedGate.X, PlacedGate.Y, PlacedGate.Z),
            Rotation=PlacedGate.Rotation,
            MirrorX=PlacedGate.MirrorX,
            MacroBounds=(
                PlacedGate.X,
                PlacedGate.Z,
                PlacedGate.X + Width - 1,
                PlacedGate.Z + Depth - 1,
            ),
            ConnectionPin=PlacedGate.InputPins[0],
            ConnectionDirection=PlacedGate.InputDirections[0],
            InteriorSpan=0,
        )

    Slots = (
        BuildNorthSlot("OutputLeft", PlacedLeftOutput),
        BuildNorthSlot("OutputRight", PlacedRightOutput),
    )
    Reservation = DerivedPerimeterFaceReservation(
        Face="north",
        NormalCoordinate=Slots[0].ConnectionPin[2],
        LateralMinimum=min(Slot.ConnectionPin[0] for Slot in Slots),
        LateralMaximum=max(Slot.ConnectionPin[0] for Slot in Slots),
        TerminalNames=tuple(Slot.TerminalName for Slot in Slots),
        SlotIds=tuple(Slot.SlotId for Slot in Slots),
    )
    CoreWidth, CoreDepth = RotatedCellSize(
        PlacedCore.Kind,
        PlacedCore.Rotation,
    )
    Assignment = DerivedPerimeterSlotAssignment(
        DomainFingerprint="asymmetric-launch-domain",
        AssignmentFingerprint="asymmetric-launch-assignment",
        CoreBounds=(
            PlacedCore.X,
            PlacedCore.Z,
            PlacedCore.X + CoreWidth - 1,
            PlacedCore.Z + CoreDepth - 1,
        ),
        SelectedSlots=Slots,
        FaceReservations=(Reservation,),
        Bounds=(
            min(
                PlacedCore.X,
                PlacedLeftOutput.X,
                PlacedRightOutput.X,
            ),
            min(
                PlacedCore.Z,
                PlacedLeftOutput.Z,
                PlacedRightOutput.Z,
            ),
            max(
                PlacedCore.X + CoreWidth - 1,
                max(
                    Slot.MacroBounds[2]
                    for Slot in Slots
                ),
            ),
            max(
                PlacedCore.Z + CoreDepth - 1,
                max(
                    Slot.MacroBounds[3]
                    for Slot in Slots
                ),
            ),
        ),
        Objective=(1,),
        ExpansionCount=1,
        Success=True,
        Complete=True,
    )
    Placed = PlacedDesign(
        Module=ModuleIR(
            Name="AsymmetricPerimeterLaunch",
            Inputs=["In"],
            Outputs=["Result"],
            Gates=[CoreGate, LeftOutputGate, RightOutputGate],
        ),
        PlacedGates=[
            PlacedCore,
            PlacedLeftOutput,
            PlacedRightOutput,
        ],
        DerivedPerimeterSlotAssignment=Assignment,
    )
    return PcbPlacement(
        Placed=Placed,
        Clusters=(("Core",),),
        SignalOrder=("In", "Result"),
        LayerCount=2,
        DerivedPerimeterSlotAssignment=Assignment,
    )


def test_derived_perimeter_slot_rejects_an_inward_port_normal():
    with pytest.raises(ValueError, match="point outward"):
        DerivedPerimeterTerminalSlot(
            SlotId="invalid",
            TerminalName="InputIn",
            Signal="In",
            Face="north",
            Origin=(0, 1, -5),
            Rotation=0,
            MirrorX=False,
            MacroBounds=(0, -5, 0, -3),
            ConnectionPin=(0, 1, -2),
            ConnectionDirection=(0, 0, 1),
            InteriorSpan=0,
        )


def test_derived_perimeter_fabric_freezes_face_and_outer_ring_bounds():
    Placement, Slot, Assignment = BuildNorthFacingPerimeterFixture()
    Fabric = BuildPlacementAccessFabric(
        Placement,
        Resources=BuildRoutingResources(Placement.Placed),
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=2,
    )

    assert Fabric.Complete is True
    assert Fabric.PerimeterSlotAssignmentFingerprint == (
        Assignment.AssignmentFingerprint
    )
    # A selected terminal keeps its north-facing ingress contract.  The
    # fabric materializes only faces demanded by selected slots and their
    # paired signal roots; unrelated internal signals retain ordinary portal
    # domains instead of receiving a speculative four-side ring.
    assert Fabric.ActiveFaces == ("north",)
    assert Fabric.OuterBounds is not None
    Envelope = Fabric.FrozenRoutingEnvelope
    assert Envelope is not None
    assert Envelope.RoutingRegionBounds == Fabric.OuterBounds
    assert Envelope.PermittedLayers == tuple(range(Placement.LayerCount))
    assert dict(Envelope.PerimeterFaceTrackCounts) == {
        "north": 2,
        "south": 0,
        "west": 0,
        "east": 0,
    }
    assert Envelope.CanvasBounds[0] <= Envelope.RoutingRegionBounds[0]
    assert Envelope.CanvasBounds[1] <= Envelope.RoutingRegionBounds[1]
    assert Envelope.CanvasBounds[2] >= Envelope.RoutingRegionBounds[2]
    assert Envelope.CanvasBounds[3] >= Envelope.RoutingRegionBounds[3]
    MinimumX, MinimumZ, MaximumX, MaximumZ = Fabric.OuterBounds
    # The active north segment spans its exact lateral ring extent, while
    # inactive faces do not enlarge the physical contract.
    assert MinimumX <= Assignment.Bounds[0]
    assert MaximumX >= Assignment.Bounds[2]
    assert MinimumZ < Assignment.Bounds[1]
    assert MaximumZ == Assignment.Bounds[3]
    assert all(
        MinimumX <= Position[0] <= MaximumX
        and MinimumZ <= Position[2] <= MaximumZ
        for Position in Fabric.Nodes
    )
    assert any(Position[2] == MinimumZ for Position in Fabric.Nodes)
    assert all(Position[2] < Assignment.Bounds[1] for Position in Fabric.Nodes)

    Domain = next(
        Value
        for Value in Fabric.TerminalDomains
        if (Value.Signal, Value.Terminal) == (Slot.Signal, Slot.ConnectionPin)
    )
    assert Domain.Complete is True
    # Every legal entry along the frozen north face remains available to the
    # one capacity solve.  A farther lateral entry can have disjoint capacity
    # claims even when the nearest entry conflicts with another terminal, so
    # minimum stub length alone is not a proof-safe pruning rule.
    assert {Stub.Ingress[2] for Stub in Domain.EscapeStubs} == {
        MinimumZ,
        MinimumZ + 3,
    }
    assert len(Domain.EscapeStubs) > 2
    assert all(
        MinimumX <= Stub.Ingress[0] <= MaximumX
        for Stub in Domain.EscapeStubs
    )
    # Internal NAND terminals remain in the one authoritative portal/track
    # domain.  The placement factor freezes only selected I/O apertures and
    # never starts a second local ring router for interior pins.
    InternalDomains = tuple(
        Value
        for Value in Fabric.TerminalDomains
        if Value.Terminal != Slot.ConnectionPin
    )
    assert InternalDomains == ()
    Serialized = Fabric.ToDictionary()
    assert Serialized["OuterBounds"] == list(Fabric.OuterBounds)
    assert Serialized["ActiveFaces"] == ["north"]
    assert Serialized["PerimeterSlotAssignmentFingerprint"] == (
        Assignment.AssignmentFingerprint
    )
    assert Serialized["FrozenRoutingEnvelope"] == Envelope.ToDictionary()


def test_representative_ingress_factor_is_a_fixed_subset_of_full_ring_domain():
    """Pre-route factoring may reduce ingress values without moving geometry."""
    Placement, Slot, _Assignment = BuildNorthFacingPerimeterFixture()
    Resources = BuildRoutingResources(Placement.Placed)
    Full = BuildPlacementAccessFabric(
        Placement,
        Resources=Resources,
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=2,
    )
    Representatives = BuildPlacementAccessFabric(
        Placement,
        Resources=Resources,
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=2,
        RestrictDerivedIngressToRepresentatives=True,
    )

    FullDomain = next(
        Value for Value in Full.TerminalDomains
        if (Value.Signal, Value.Terminal) == (Slot.Signal, Slot.ConnectionPin)
    )
    RepresentativeDomain = next(
        Value for Value in Representatives.TerminalDomains
        if (Value.Signal, Value.Terminal) == (Slot.Signal, Slot.ConnectionPin)
    )
    assert Representatives.FabricFingerprint != Full.FabricFingerprint
    assert Representatives.OuterBounds == Full.OuterBounds
    assert Representatives.ActiveFaces == Full.ActiveFaces
    assert RepresentativeDomain.Complete is True
    assert 0 < len(RepresentativeDomain.EscapeStubs) < len(
        FullDomain.EscapeStubs
    )
    assert {
        Stub.Path for Stub in RepresentativeDomain.EscapeStubs
    }.issubset({Stub.Path for Stub in FullDomain.EscapeStubs})


def test_derived_perimeter_shell_reuses_fixed_prefabric_geometry(
    monkeypatch: pytest.MonkeyPatch,
):
    Placement, _Slot, Assignment = BuildNorthFacingPerimeterFixture()
    Resources = BuildRoutingResources(Placement.Placed)
    # A shell owns only pre-fabric facts.  Building it must not allocate a
    # routing region or traverse an escape graph.
    with monkeypatch.context() as Patch:
        Patch.setattr(
            Resources.ResourceGraph,
            "BuildRegion",
            lambda *_Arguments, **_Keywords: pytest.fail(
                "shell construction must not build a routing region"
            ),
        )
        Shell = BuildDerivedPerimeterFabricShell(
            Placement,
            Resources=Resources,
            AccessRingTrackCount=2,
        )

    RebuiltShell = BuildDerivedPerimeterFabricShell(
        Placement,
        Resources=Resources,
        AccessRingTrackCount=2,
    )
    assert Shell.ShellFingerprint == RebuiltShell.ShellFingerprint
    assert Shell.PerimeterSlotAssignmentFingerprint == (
        Assignment.AssignmentFingerprint
    )
    assert Shell.Profiles
    assert Shell.TerminalPaths
    assert Shell.RingBounds
    assert Shell.FabricLayers
    assert len(Shell.FabricLayers) == len(Shell.FabricYs)

    # The supplied shell must be consumed as-is, not regenerated from the
    # placement.  The ensuing fabric is the one stage allowed to build a
    # region and legal terminal escape domains.
    with monkeypatch.context() as Patch:
        Patch.setattr(
            AccessFabricModule,
            "BuildNetRoutingProfiles",
            lambda *_Arguments, **_Keywords: pytest.fail(
                "fabric must reuse the supplied perimeter shell"
            ),
        )
        Fabric = BuildPlacementAccessFabric(
            Placement,
            Resources=Resources,
            TopologyKind="derived-perimeter-access-v1",
            AccessRingTrackCount=2,
            Shell=Shell,
        )

    assert Fabric.Complete is True
    assert Shell.Bounds == Fabric.OuterBounds
    assert Shell.OuterBounds == Fabric.OuterBounds
    assert Shell.ActiveFaces == Fabric.ActiveFaces
    assert Shell.PerimeterFaceTrackCounts == (
        ("north", 2),
        ("south", 0),
        ("west", 0),
        ("east", 0),
    )
    assert Shell.SlotFaceByTerminal


def test_derived_perimeter_fabric_honors_asymmetric_face_track_contract():
    Placement = BuildAsymmetricPerimeterLaunchFixture()
    AsymmetricPlacement = replace(Placement, LayerCount=1)
    Demand = BuildPlacementAccessDemand(
        AsymmetricPlacement,
        0,
        Technology=DefaultRedstoneRoutingTechnology,
    )
    Envelope = DeriveRoutingEnvelopes(Demand)[0]
    assert Envelope.AccessRingTrackCount == 2
    assert dict(Demand.PerimeterFaceLaunchDemand) == {
        "north": 2,
        "south": 1,
    }
    PerimeterFaceTrackCounts = (
        ("north", 2),
        ("south", 1),
        ("west", 0),
        ("east", 0),
    )
    Resources = BuildRoutingResources(Placement.Placed)
    Shell = BuildDerivedPerimeterFabricShell(
        AsymmetricPlacement,
        Resources=Resources,
        Technology=DefaultRedstoneRoutingTechnology,
        AccessRingTrackCount=Envelope.AccessRingTrackCount,
        AccessLength=Demand.AccessLength,
        PerimeterFaceTrackCounts=PerimeterFaceTrackCounts,
    )
    Fabric = BuildPlacementAccessFabric(
        AsymmetricPlacement,
        Resources=Resources,
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=Envelope.AccessRingTrackCount,
        Shell=Shell,
    )

    assert Fabric.Complete is True
    assert Fabric.FrozenRoutingEnvelope is not None
    assert Shell.PerimeterFaceTrackCounts == PerimeterFaceTrackCounts
    assert Fabric.FrozenRoutingEnvelope.PerimeterFaceTrackCounts == (
        PerimeterFaceTrackCounts
    )
    assert dict(Fabric.FrozenRoutingEnvelope.PerimeterFaceTrackCounts) == {
        "north": 2,
        "south": 1,
        "west": 0,
        "east": 0,
    }
    assert Fabric.FrozenRoutingEnvelope.RoutingRegionBounds == Shell.OuterBounds


def test_route_attempt_passes_the_frozen_envelope_to_detailed_routing():
    """The derived canvas is a hard route input, not an artifact-only audit."""
    Placement, _Slot, _Assignment = BuildNorthFacingPerimeterFixture()
    Fabric = BuildPlacementAccessFabric(
        Placement,
        Resources=BuildRoutingResources(Placement.Placed),
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
    )
    AttachedPlacement = AttachPlacementAccessFabric(Placement, Fabric)
    SeenEnvelopes = []
    Routed = RoutedDesign(
        Module=AttachedPlacement.Placed.Module,
        PlacedGates=[],
        Wires=[],
        Supports=[],
        Repeaters={},
        NetWires={},
    )

    def Route(*_Arguments, **Options):
        SeenEnvelopes.append(Options["FrozenRoutingEnvelope"])
        return Routed

    with (
        patch("Compiler.Routing.Pcb.RoutePcbNets", side_effect=Route),
        patch(
            "Compiler.Routing.Pcb.CompactRoutedTrees",
            return_value=Routed,
        ),
    ):
        Result = RoutePcbAttempt(
            AttachedPlacement,
            BuildRoutingAttemptPolicies()[0],
            Resources=object(),
            Policy=LocalFirstPhysicalDesignPolicy,
            Deadline=RoutingDeadline.Start(5.0),
        )

    assert Result is Routed
    assert SeenEnvelopes == [Fabric.FrozenRoutingEnvelope]
    assert Routed.RoutingControlEffectiveness[
        "FrozenPerFaceRoutingEnvelope"
    ] == Fabric.FrozenRoutingEnvelope.ToDictionary()


def test_derived_perimeter_shell_rejects_changed_candidate_identity():
    Placement, _Slot, _Assignment = BuildNorthFacingPerimeterFixture()
    Resources = BuildRoutingResources(Placement.Placed)
    Shell = BuildDerivedPerimeterFabricShell(
        Placement,
        Resources=Resources,
        AccessRingTrackCount=1,
    )

    with pytest.raises(ValueError, match="shell input identity"):
        BuildPlacementAccessFabric(
            replace(Placement, LayerCount=Placement.LayerCount + 1),
            Resources=Resources,
            TopologyKind="derived-perimeter-access-v1",
            AccessRingTrackCount=1,
            Shell=Shell,
        )


def test_derived_perimeter_demand_counts_only_signal_closed_interfaces():
    NorthPlacement, _Slot, _Assignment = BuildNorthFacingPerimeterFixture()
    NorthTerminalCount, NorthFaces = MeasureDerivedPerimeterInterfaceDemand(
        NorthPlacement,
    )

    # The input slot is already that signal's root, so no interior core pins
    # become ring demand merely because they consume the same signal.
    assert NorthTerminalCount == 1
    assert NorthFaces == ("north",)

    SouthPlacement, _RootTerminal, _SlotTerminal = (
        BuildSouthFacingPerimeterRootFixture()
    )
    SouthTerminalCount, SouthFaces = MeasureDerivedPerimeterInterfaceDemand(
        SouthPlacement,
    )

    # An output slot is a target.  Its producer root is therefore part of
    # the same frozen source-to-interface factor, but no unrelated pins are.
    assert SouthTerminalCount == 2
    assert SouthFaces == ("south",)


def test_derived_perimeter_launch_demand_by_face_is_immutable_and_root_aware():
    NorthPlacement, _Slot, _Assignment = BuildNorthFacingPerimeterFixture()
    NorthDemand = MeasureDerivedPerimeterInterfaceLaunchDemandByFace(
        NorthPlacement,
    )

    # This selected input is the signal root itself, so it uses its selected
    # north face once rather than acquiring a second root-derived face.
    assert dict(NorthDemand) == {"north": 1}
    with pytest.raises(TypeError):
        NorthDemand["north"] = 2

    SouthPlacement, _RootTerminal, _SlotTerminal = (
        BuildSouthFacingPerimeterRootFixture()
    )
    SouthDemand = MeasureDerivedPerimeterInterfaceLaunchDemandByFace(
        SouthPlacement,
    )
    # A selected output target and its distinct source root both launch on
    # south in this physical orientation.
    assert dict(SouthDemand) == {"south": 2}

    OppositePlacement, _RootTerminal, _SlotTerminal = (
        BuildOppositeFacePerimeterRootFixture()
    )
    OppositeDemand = MeasureDerivedPerimeterInterfaceLaunchDemandByFace(
        OppositePlacement,
    )
    # The chosen target stays north while its paired root uses the producer's
    # actual south-facing source path, not the target's aperture face.
    assert tuple(OppositeDemand.items()) == (("north", 1), ("south", 1))


def test_derived_perimeter_launch_demand_by_face_counts_asymmetric_slots():
    Placement = BuildAsymmetricPerimeterLaunchFixture()

    DemandByFace = MeasureDerivedPerimeterInterfaceLaunchDemandByFace(
        Placement,
    )

    assert tuple(DemandByFace.items()) == (("north", 2), ("south", 1))
    assert MeasureDerivedPerimeterInterfaceDemand(Placement) == (
        3,
        ("north", "south"),
    )


def test_derived_perimeter_launch_demand_by_face_requires_complete_assignment():
    Placement, Slot, Assignment = BuildNorthFacingPerimeterFixture()
    IncompleteAssignment = replace(
        Assignment,
        Success=False,
        Complete=False,
        IncompleteReason="work-cap",
    )
    IncompletePlacement = replace(
        Placement,
        DerivedPerimeterSlotAssignment=IncompleteAssignment,
    )
    assert dict(MeasureDerivedPerimeterInterfaceLaunchDemandByFace(
        IncompletePlacement,
    )) == {}

    InvalidAssignment = SimpleNamespace(
        Success=True,
        Complete=True,
        SelectedSlots=(SimpleNamespace(
            Signal=Slot.Signal,
            ConnectionPin=Slot.ConnectionPin,
            Face="unknown-face",
        ),),
    )
    InvalidPlacement = replace(
        Placement,
        DerivedPerimeterSlotAssignment=InvalidAssignment,
    )
    with pytest.raises(ValueError, match="unknown face"):
        MeasureDerivedPerimeterInterfaceLaunchDemandByFace(
            InvalidPlacement,
        )


def test_perimeter_driven_root_uses_its_source_direction_not_slot_segment():
    Placement, RootTerminal, SlotTerminal = (
        BuildSouthFacingPerimeterRootFixture()
    )
    Fabric = BuildPlacementAccessFabric(
        Placement,
        Resources=BuildRoutingResources(Placement.Placed),
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
        MaximumLegalEscapeExpansions=10_000,
    )

    assert Fabric.Complete is True
    assert Fabric.ActiveFaces == ("south",)
    assert Fabric.OuterBounds is not None
    MaximumZ = Fabric.OuterBounds[3]
    RootDomain = next(
        Domain
        for Domain in Fabric.TerminalDomains
        if Domain.Signal == "Result" and Domain.Terminal == RootTerminal
    )
    SlotDomain = next(
        Domain
        for Domain in Fabric.TerminalDomains
        if Domain.Signal == "Result" and Domain.Terminal == SlotTerminal
    )
    # The source macro points south, so every retained pivot remains on one
    # representative per physical routing layer.  A derived access path may
    # expose several fixed in-region handoff pivots, but it never inherits
    # the output macro's complete lateral slot segment.
    assert len(RootDomain.EscapeStubs) >= Placement.LayerCount
    assert len(SlotDomain.EscapeStubs) > len(RootDomain.EscapeStubs)
    assert {Stub.Ingress[2] for Stub in RootDomain.EscapeStubs} == {MaximumZ}
    assert {Stub.Ingress[2] for Stub in SlotDomain.EscapeStubs} == {MaximumZ}
    assert all(
        Position[2] >= RootDomain.Terminal[2]
        for Stub in RootDomain.EscapeStubs
        for Position in Stub.Path
    )


def test_derived_perimeter_uses_earlier_access_pivot_when_farthest_conflicts(
    monkeypatch: pytest.MonkeyPatch,
):
    """A legal earlier access pivot beats a self-conflicting farthest one.

    The last access position lies directly below an earlier elevated access
    cell.  Keeping it as the sole handoff pivot makes that elevated cell's
    support/air claim self-conflict.  The immediately earlier prefix can
    reach the same fixed south ring node via a legal diagonal transition, so
    an immutable access-factor builder must retain that legal pivot rather
    than erase the terminal domain.
    """
    Placement, _RootTerminal, SlotTerminal = (
        BuildSouthFacingPerimeterRootFixture()
    )
    Placement = replace(Placement, LayerCount=1)
    Resources = BuildRoutingResources(Placement.Placed)
    Profiles = AccessFabricModule.BuildNetRoutingProfiles(Placement.Placed)
    Profile = Profiles["Result"]
    TargetAccessPath = (
        (0, 1, 8),
        (0, 2, 9),
        (0, 2, 10),
        (-1, 1, 10),
        # This farthest in-region point is support-conflicting with the
        # elevated access node above it.  It is a bad handoff pivot, not a
        # proof that the selected terminal has no legal escape.
        (0, 1, 10),
    )
    assert FindSelfClaimConflicts({
        "Result": Resources.ResourceGraph.BuildRouteClaims(TargetAccessPath),
    })
    EarlierPrefix = TargetAccessPath[:-1]
    RingIngress = (0, 2, 11)
    assert not FindSelfClaimConflicts({
        "Result": Resources.ResourceGraph.BuildRouteClaims((
            *EarlierPrefix,
            RingIngress,
        )),
    })
    Profiles["Result"] = replace(
        Profile,
        TargetAccessPaths={SlotTerminal: TargetAccessPath},
    )
    monkeypatch.setattr(
        AccessFabricModule,
        "BuildNetRoutingProfiles",
        lambda *_Arguments, **_Keywords: Profiles,
    )
    # Fix the selected south interface plane.  This makes the test about
    # choosing among finite prefix pivots, not about changing geometry after
    # an access failure.
    monkeypatch.setattr(
        AccessFabricModule,
        "_BuildDerivedPerimeterRingBounds",
        lambda *_Arguments, **_Keywords: (
            ((-2, 4, -2, 11),),
            (-2, 0, 4, 11),
            ("south",),
            {("Result", SlotTerminal): "south"},
            (("north", 0), ("south", 1), ("west", 0), ("east", 0)),
        ),
    )

    Fabric = BuildPlacementAccessFabric(
        Placement,
        Resources=Resources,
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
        MaximumLegalEscapeExpansions=10_000,
    )

    TargetDomain = next(
        Domain
        for Domain in Fabric.TerminalDomains
        if Domain.Signal == "Result" and Domain.Terminal == SlotTerminal
    )
    assert Fabric.Complete is True
    assert Fabric.OuterBounds == (-2, 0, 4, 11)
    assert TargetDomain.Complete is True
    assert TargetDomain.EscapeStubs
    # The retained domain uses the earlier prefix and reaches the same fixed
    # south face; it must not carry the bad farthest pivot into its claims.
    assert any(
        Stub.Ingress == RingIngress
        and TargetAccessPath[-1] not in Stub.Path
        for Stub in TargetDomain.EscapeStubs
    )
    assert all(
        not FindSelfClaimConflicts({"Result": Stub.PhysicalClaims})
        for Stub in TargetDomain.EscapeStubs
    )


def test_derived_perimeter_retains_all_legal_access_prefix_pivots(
    monkeypatch: pytest.MonkeyPatch,
):
    """Every legal fixed prefix remains a capacity option in one domain.

    This complements the failed-farthest regression above: two different
    prefix pivots can both reach the same frozen south ring.  The access
    builder must publish both before its one capacity solve, rather than
    retaining only the first successful pivot as an internal fallback.
    """
    Placement, _RootTerminal, SlotTerminal = (
        BuildSouthFacingPerimeterRootFixture()
    )
    Placement = replace(Placement, LayerCount=1)
    Resources = BuildRoutingResources(Placement.Placed)
    Profiles = AccessFabricModule.BuildNetRoutingProfiles(Placement.Placed)
    Profile = Profiles["Result"]
    TargetAccessPath = (
        (0, 1, 8),
        (1, 2, 9),
        (1, 1, 10),
        (0, 1, 10),
    )
    FarRingIngress = (0, 2, 11)
    EarlierRingIngress = (1, 2, 11)
    assert not FindSelfClaimConflicts({
        "Result": Resources.ResourceGraph.BuildRouteClaims((
            *TargetAccessPath,
            FarRingIngress,
        )),
    })
    assert not FindSelfClaimConflicts({
        "Result": Resources.ResourceGraph.BuildRouteClaims((
            *TargetAccessPath[:-1],
            EarlierRingIngress,
        )),
    })
    Profiles["Result"] = replace(
        Profile,
        TargetAccessPaths={SlotTerminal: TargetAccessPath},
    )
    monkeypatch.setattr(
        AccessFabricModule,
        "BuildNetRoutingProfiles",
        lambda *_Arguments, **_Keywords: Profiles,
    )
    monkeypatch.setattr(
        AccessFabricModule,
        "_BuildDerivedPerimeterRingBounds",
        lambda *_Arguments, **_Keywords: (
            ((-2, 4, -2, 11),),
            (-2, 0, 4, 11),
            ("south",),
            {("Result", SlotTerminal): "south"},
            (("north", 0), ("south", 1), ("west", 0), ("east", 0)),
        ),
    )

    Fabric = BuildPlacementAccessFabric(
        Placement,
        Resources=Resources,
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
        MaximumLegalEscapeExpansions=10_000,
    )

    TargetDomain = next(
        Domain
        for Domain in Fabric.TerminalDomains
        if Domain.Signal == "Result" and Domain.Terminal == SlotTerminal
    )
    StubPaths = {Stub.Path for Stub in TargetDomain.EscapeStubs}
    assert Fabric.Complete is True
    assert TargetDomain.Complete is True
    assert (
        *TargetAccessPath,
        FarRingIngress,
    ) in StubPaths
    assert (
        *TargetAccessPath[:-1],
        EarlierRingIngress,
    ) in StubPaths


def test_opposite_face_root_access_landing_expands_the_frozen_ring():
    Placement, RootTerminal, SlotTerminal = (
        BuildOppositeFacePerimeterRootFixture()
    )
    Resources = BuildRoutingResources(Placement.Placed)
    RootProfile = BuildNetRoutingProfiles(Placement.Placed)["Result"]
    RootLanding = DefaultRedstoneRoutingTechnology.AccessLanding(
        RootProfile.SourceAccessPath,
    )
    assert RootLanding[2] > Placement.DerivedPerimeterSlotAssignment.Bounds[3]

    Fabric = BuildPlacementAccessFabric(
        Placement,
        Resources=Resources,
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
        MaximumLegalEscapeExpansions=10_000,
    )

    assert Fabric.Complete is True
    # The target's fixed aperture is north, whereas its paired producer
    # points south.  Both directions must be part of one frozen factor.
    assert Fabric.ActiveFaces == ("north", "south")
    assert Fabric.OuterBounds is not None
    assert Fabric.OuterBounds[3] >= RootLanding[2]
    RootDomain = next(
        Domain
        for Domain in Fabric.TerminalDomains
        if Domain.Signal == "Result" and Domain.Terminal == RootTerminal
    )
    SlotDomain = next(
        Domain
        for Domain in Fabric.TerminalDomains
        if Domain.Signal == "Result" and Domain.Terminal == SlotTerminal
    )
    assert RootDomain.Complete is True
    assert RootDomain.EscapeStubs
    assert SlotDomain.Complete is True
    assert all(
        Stub.Complete
        and Stub.Path[-1] == Stub.Ingress
        and Stub.Ingress[2] >= RootLanding[2]
        and not FindSelfClaimConflicts({"Result": Stub.PhysicalClaims})
        for Stub in RootDomain.EscapeStubs
    )


def test_perimeter_root_face_uses_first_exact_horizontal_access_step():
    assert _DerivePerimeterRootAccessFace((
        (4, 1, 3),
        (4, 2, 3),
        (4, 2, 4),
    )) == "south"
    assert _DerivePerimeterRootAccessFace((
        (4, 1, 3),
        (4, 2, 3),
    )) is None


def test_derived_legal_escape_work_limit_is_geometry_bound_and_overridable():
    Placement, _RootTerminal, _SlotTerminal = (
        BuildSouthFacingPerimeterRootFixture()
    )
    Resources = BuildRoutingResources(Placement.Placed)
    Derived = BuildPlacementAccessFabric(
        Placement,
        Resources=Resources,
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
        DeriveLegalEscapeWorkLimit=True,
    )

    assert Derived.Complete is True
    assert Derived.LegalEscapeWorkLimitKind == (
        "derived-direction-state-v1"
    )
    assert Derived.LegalEscapeDirectionStateUpperBound is not None
    assert Derived.LegalEscapeExpansionLimit == (
        Derived.LegalEscapeDirectionStateUpperBound
    )
    assert (
        Derived.LegalEscapeExpansionCount
        <= Derived.LegalEscapeDirectionStateUpperBound
    )
    assert Derived.ToDictionary()["LegalEscapeWorkLimitKind"] == (
        "derived-direction-state-v1"
    )

    ExplicitCap = BuildPlacementAccessFabric(
        Placement,
        Resources=Resources,
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
        DeriveLegalEscapeWorkLimit=True,
        MaximumLegalEscapeExpansions=1,
    )

    assert ExplicitCap.Complete is False
    assert ExplicitCap.IncompleteReason == "legal-escape-work-cap"
    assert ExplicitCap.LegalEscapeWorkLimitKind == "explicit"
    assert ExplicitCap.LegalEscapeExpansionLimit == 1
    # The derived graph bound remains diagnostic evidence even when a focused
    # test intentionally provides a smaller terminating cap.
    assert ExplicitCap.LegalEscapeDirectionStateUpperBound == (
        Derived.LegalEscapeDirectionStateUpperBound
    )


def test_incomplete_perimeter_slot_assignment_stays_typed_incomplete():
    Placement, _Slot, Assignment = BuildNorthFacingPerimeterFixture()
    IncompleteAssignment = replace(
        Assignment,
        AssignmentFingerprint="",
        SelectedSlots=(),
        FaceReservations=(),
        Objective=(),
        Success=False,
        Complete=False,
        IncompleteReason="work-cap",
    )
    IncompletePlacement = replace(
        Placement,
        Placed=replace(
            Placement.Placed,
            DerivedPerimeterSlotAssignment=IncompleteAssignment,
        ),
        DerivedPerimeterSlotAssignment=IncompleteAssignment,
    )

    Fabric = BuildPlacementAccessFabric(
        IncompletePlacement,
        Resources=BuildRoutingResources(IncompletePlacement.Placed),
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
    )

    assert Fabric.Complete is False
    assert Fabric.IncompleteReason == "work-cap"
    assert Fabric.OuterBounds == IncompleteAssignment.Bounds
    assert Fabric.PerimeterSlotAssignmentFingerprint == ""


def test_derived_perimeter_without_slot_assignment_preserves_legacy_fabric():
    Placement, _Slot, _Assignment = BuildNorthFacingPerimeterFixture()
    LegacyPlacement = replace(
        Placement,
        Placed=replace(
            Placement.Placed,
            DerivedPerimeterSlotAssignment=None,
        ),
        DerivedPerimeterSlotAssignment=None,
    )

    Fabric = BuildPlacementAccessFabric(
        LegacyPlacement,
        Resources=BuildRoutingResources(LegacyPlacement.Placed),
        TopologyKind="derived-perimeter-access-v1",
        AccessRingTrackCount=1,
    )

    assert Fabric.OuterBounds is None
    assert Fabric.ActiveFaces == ()
    assert Fabric.PerimeterSlotAssignmentFingerprint == ""

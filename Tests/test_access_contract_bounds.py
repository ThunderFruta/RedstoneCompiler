"""Audit contracts for finite placement-access and detailed-routing bounds."""

from Compiler.Ir.Models import Gate, GateKind, ModuleIR
from Compiler.Placement.Geometry import BuildPlacedGate, PlacedDesign
from Compiler.Placement.Pcb import PcbPlacement
from Compiler.Routing.AuthoritativePlanner import (
    ResolvePlacementAccessFabricRegionContract,
)
from Compiler.Routing.Models import (
    DetailedRoutingBounds,
    FrozenPerFaceRoutingEnvelope,
    PlacementAccessEscapeStub,
    PlacementAccessFabric,
    PlacementAccessTerminalDomain,
)
from Compiler.Routing.Pcb import _BuildPlacementAccessRoutingBoundsAudit
from Compiler.Routing.ResourceGraph import RoutingResourceClaims


def _BuildFixedBandFabric() -> PlacementAccessFabric:
    """Build a legacy fixed-band factor with visibly asymmetric stubs."""
    FirstStub = PlacementAccessEscapeStub(
        Terminal=(1, 1, 1),
        Ingress=(10, 4, 1),
        Path=((1, 1, 1), (4, 2, 1), (10, 4, 1)),
        PhysicalClaims=RoutingResourceClaims(),
        CapacityResourceIds=(),
        Complete=True,
    )
    SecondStub = PlacementAccessEscapeStub(
        Terminal=(2, 1, 8),
        Ingress=(13, 4, 12),
        Path=((2, 1, 8), (13, 4, 12)),
        PhysicalClaims=RoutingResourceClaims(),
        CapacityResourceIds=(),
        Complete=True,
    )
    return PlacementAccessFabric(
        FabricFingerprint="access-bounds-fixed-band",
        Nodes=((9, 4, 0), (10, 4, 1), (11, 4, 2)),
        Edges=(((9, 4, 0), (10, 4, 1)),),
        IngressNodes=((10, 4, 1), (13, 4, 12)),
        PhysicalClaims=RoutingResourceClaims(),
        CapacityResourceIds=(),
        TerminalDomains=(
            PlacementAccessTerminalDomain(
                Signal="First",
                Terminal=FirstStub.Terminal,
                EscapeStubs=(FirstStub,),
                Complete=True,
            ),
            PlacementAccessTerminalDomain(
                Signal="Second",
                Terminal=SecondStub.Terminal,
                EscapeStubs=(SecondStub,),
                Complete=True,
            ),
        ),
        TopologyKind="fixed-access-band-v1",
        Complete=True,
    )


def _Contains(
    Bounds: tuple[int, int, int, int],
    Position: tuple[int, int, int],
) -> bool:
    return (
        Bounds[0] <= Position[0] <= Bounds[2]
        and Bounds[1] <= Position[2] <= Bounds[3]
    )


def test_access_contract_bounds_enclose_every_materialized_fixed_band_member():
    Fabric = _BuildFixedBandFabric()

    Bounds = Fabric.AccessContractBounds

    assert Bounds.Bounds == (1, 0, 13, 12)
    assert Bounds.RoutingRegionBounds == Bounds.Bounds
    assert Bounds.DeclaredOuterBounds is None
    assert Bounds.PositionCount == 7
    assert Bounds.FabricNodeCount == 3
    assert Bounds.IngressNodeCount == 2
    assert Bounds.StubCount == 2
    assert Bounds.StubPathPositionCount == 5
    assert all(
        _Contains(Bounds.Bounds, Position)
        for Position in (
            *Fabric.Nodes,
            *Fabric.IngressNodes,
            *(
                Position
                for Domain in Fabric.TerminalDomains
                for Stub in Domain.EscapeStubs
                for Position in Stub.Path
            ),
        )
    )
    assert Fabric.ToDictionary()["AccessContractBounds"] == (
        Bounds.ToDictionary()
    )


def test_frozen_per_face_routing_envelope_requires_one_complete_canvas():
    Envelope = FrozenPerFaceRoutingEnvelope(
        RoutingRegionBounds=(1, 2, 8, 9),
        CanvasBounds=(0, 1, 9, 10),
        YBounds=(0, 5),
        PermittedLayers=(0, 1, 2),
        PerimeterFaceTrackCounts=(
            ("north", 1),
            ("south", 0),
            ("west", 2),
            ("east", 0),
        ),
        EnvelopeFingerprint="frozen-per-face-contract",
    )

    assert Envelope.ToDictionary() == {
        "RoutingRegionBounds": [1, 2, 8, 9],
        "CanvasBounds": [0, 1, 9, 10],
        "YBounds": [0, 5],
        "PermittedLayers": [0, 1, 2],
        "PerimeterFaceTrackCounts": {
            "north": 1,
            "south": 0,
            "west": 2,
            "east": 0,
        },
        "EnvelopeFingerprint": "frozen-per-face-contract",
    }


def test_detailed_routing_bounds_match_the_authoritative_region_then_margin():
    Fabric = _BuildFixedBandFabric()
    AccessBounds = Fabric.AccessContractBounds
    DetailedBounds = DetailedRoutingBounds.FromCoreAndAccessContract(
        (0, 0, 5, 5),
        AccessBounds,
        SearchMarginX=4,
        SearchMarginZ=6,
    )
    Domains = {
        (Domain.Signal, Domain.Terminal): Domain
        for Domain in Fabric.TerminalDomains
    }
    (
        MinimumX,
        MaximumX,
        MinimumZ,
        MaximumZ,
        _Positions,
        _OuterBounds,
    ) = ResolvePlacementAccessFabricRegionContract(
        0,
        5,
        0,
        5,
        Fabric,
        Domains,
    )

    assert DetailedBounds.RoutingRegionBounds == (
        MinimumX,
        MinimumZ,
        MaximumX,
        MaximumZ,
    )
    assert DetailedBounds.CanvasBounds == (-4, -6, 17, 18)
    assert DetailedBounds.CanvasBounds == (
        DetailedBounds.RoutingRegionBounds[0] - 4,
        DetailedBounds.RoutingRegionBounds[1] - 6,
        DetailedBounds.RoutingRegionBounds[2] + 4,
        DetailedBounds.RoutingRegionBounds[3] + 6,
    )


def test_router_audit_uses_the_live_fixed_band_search_margin():
    Fabric = _BuildFixedBandFabric()
    GateValue = Gate(
        "N0",
        GateKind.NAND,
        ["Out"],
        ["A", "B"],
    )
    Module = ModuleIR(Name="AccessBoundsFixture", Gates=[GateValue])
    Placement = PcbPlacement(
        Placed=PlacedDesign(
            Module=Module,
            PlacedGates=[BuildPlacedGate(GateValue, 0, 1, 0, 0, False)],
            PlacementAccessFabric=Fabric,
        ),
        Clusters=(("N0",),),
        SignalOrder=("A", "B", "Out"),
        LayerCount=2,
        PlacementAccessFabric=Fabric,
    )

    DetailedBounds = _BuildPlacementAccessRoutingBoundsAudit(
        Placement,
        Fabric,
        SearchMargin=7,
    )

    assert DetailedBounds is not None
    assert DetailedBounds.AccessContractBounds == Fabric.AccessContractBounds
    assert DetailedBounds.SearchMarginX == 7
    assert DetailedBounds.SearchMarginZ == 7
    assert DetailedBounds.CanvasBounds == (
        DetailedBounds.RoutingRegionBounds[0] - 7,
        DetailedBounds.RoutingRegionBounds[1] - 7,
        DetailedBounds.RoutingRegionBounds[2] + 7,
        DetailedBounds.RoutingRegionBounds[3] + 7,
    )

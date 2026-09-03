from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile

import pytest

import PhysicalDesign.Placement.Access.Capacity as AccessFabricModule

from Compiler.Ir.Models import Gate, GateKind, ModuleIR
from PhysicalDesign.Geometry.Placement import BuildPlacedGate, PlacedDesign
from PhysicalDesign.Placement.Access.Capacity import SolvePlacementAccessFabricCapacity
from PhysicalDesign.Placement.Access.EscapePaths import _BuildDerivedPerimeterCycleRouteNodeSets, _BuildShortestFabricEscapePaths
from PhysicalDesign.Placement.Access.Fabric import AttachPlacementAccessFabric, BuildPlacementAccessFabric
from PhysicalDesign.Placement.Core.Clusters import PcbPlacement
from PhysicalDesign.Placement.Core.Commit.Commit import PlacePcbGraph
from PhysicalDesign.Placement.Core.Compactness import BuildPinAlignedPackedClusterPortfolio
from PhysicalDesign.Geometry.Rotation import RotatedCellSize
from PhysicalDesign.Flow.Demand import BuildDerivedPinAlignedEnvelopeLowerBoundObjective, BuildPlacementGenerationPlan, SelectDerivedPrimaryPlacementRequests
from PhysicalDesign.Placement.PreRouteInterface import DeriveRoutingEnvelopes, PlacementAccessDemand
from PhysicalDesign.Policy import LocalFirstPhysicalDesignPolicy
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly
from Compiler.Frontend import Sv
from PhysicalDesign.Contracts.Placement import PlacementAccessEscapeStub, PlacementAccessFabric, PlacementAccessTerminalDomain
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims, RoutingResourceId, RoutingResourceKind
from PhysicalDesign.Redstone.Actions.Geometry import BuildRoutingResources
from PhysicalDesign.Routing.Global.Ports.Portals import ResolvePlacementAccessFabricRegionContract
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology


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


def BuildImmutableStubCapacityFactorFixture() -> PlacementAccessFabric:
    """Build a terminal-only ring factor with exact pairwise conflicts.

    Source/target domains share ``Alpha``: target option ``N`` owns support
    at source wire ``N`` and therefore cannot coexist with it.  ``Beta``
    option ``N`` electrically excludes that same source wire.  The fixture
    gives the bitset factor a real same-signal union constraint as well as a
    cross-signal capacity constraint, while retaining every alternative.
    """
    OptionCount = 24

    def BuildClaims(
        Wire: tuple[int, int, int],
        Support: tuple[int, int, int],
        Electrical: tuple[int, int, int],
    ) -> RoutingResourceClaims:
        return RoutingResourceClaims(
            WireCells=frozenset({Wire}),
            SupportCells=frozenset({Support}),
            ElectricalCells=frozenset({Electrical}),
        )

    def BuildStub(
        Terminal: tuple[int, int, int],
        Ingress: tuple[int, int, int],
        Claims: RoutingResourceClaims,
    ) -> PlacementAccessEscapeStub:
        return PlacementAccessEscapeStub(
            Terminal=Terminal,
            Ingress=Ingress,
            Path=(Terminal, Ingress),
            PhysicalClaims=Claims,
            CapacityResourceIds=tuple(sorted(Claims.ResourceIds, key=str)),
            Complete=True,
        )

    SourceTerminal = (0, 1, -1)
    TargetTerminal = (1, 1, -1)
    BetaTerminal = (2, 1, -1)
    SourceStubs = tuple(
        BuildStub(
            SourceTerminal,
            (Index, 1, 0),
            BuildClaims(
                (Index, 1, 0),
                (Index, 0, 0),
                (Index, 1, 0),
            ),
        )
        for Index in range(OptionCount)
    )
    TargetStubs = tuple(
        BuildStub(
            TargetTerminal,
            (100 + Index, 1, 0),
            BuildClaims(
                (100 + Index, 1, 0),
                # This is legal alone, but self-conflicts with Alpha source
                # option ``Index`` once their claims are merged.
                (Index, 1, 0),
                (100 + Index, 1, 0),
            ),
        )
        for Index in range(OptionCount)
    )
    BetaStubs = tuple(
        BuildStub(
            BetaTerminal,
            (200 + Index, 1, 0),
            BuildClaims(
                (200 + Index, 1, 0),
                (200 + Index, 0, 0),
                # Beta option ``Index`` conflicts with Alpha source option
                # ``Index`` through ordinary cross-signal electrical claims.
                (Index, 1, 0),
            ),
        )
        for Index in range(OptionCount)
    )
    Domains = (
        PlacementAccessTerminalDomain(
            Signal="Alpha",
            Terminal=SourceTerminal,
            EscapeStubs=SourceStubs,
            Complete=True,
        ),
        PlacementAccessTerminalDomain(
            Signal="Alpha",
            Terminal=TargetTerminal,
            EscapeStubs=TargetStubs,
            Complete=True,
        ),
        PlacementAccessTerminalDomain(
            Signal="Beta",
            Terminal=BetaTerminal,
            EscapeStubs=BetaStubs,
            Complete=True,
        ),
    )
    return PlacementAccessFabric(
        FabricFingerprint="immutable-stub-capacity-factor-fixture",
        Nodes=tuple(
            Stub.Ingress
            for Domain in Domains
            for Stub in Domain.EscapeStubs
        ),
        Edges=(),
        IngressNodes=(),
        PhysicalClaims=RoutingResourceClaims(),
        CapacityResourceIds=(),
        TerminalDomains=Domains,
        TopologyKind="derived-perimeter-access-v1",
        Complete=True,
        AccessRingTrackCount=1,
    )


def BuildGraphPortfolioSelectionFixture() -> tuple[ModuleIR, PcbPlacement]:
    Gates = [
        Gate("N0", GateKind.NAND, ["S0"], ["A", "B"]),
        Gate("N1", GateKind.NAND, ["S1"], ["S0", "A"]),
        Gate("N2", GateKind.NAND, ["Result"], ["S1", "B"]),
    ]
    Module = ModuleIR(Name="GraphPortfolioFixture", Gates=Gates)
    Placement = PcbPlacement(
        Placed=PlacedDesign(
            Module=Module,
            PlacedGates=[
                BuildPlacedGate(Gates[0], 0, 1, 0, 0, False),
                BuildPlacedGate(Gates[1], 8, 1, 0, 0, False),
                BuildPlacedGate(Gates[2], 16, 1, 0, 0, False),
            ],
        ),
        Clusters=(("N0", "N1", "N2"),),
        SignalOrder=("A", "B", "S0", "S1", "Result"),
        LayerCount=3,
    )
    return Module, Placement


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


def test_placement_access_fabric_freezes_catalog_pin_witness():
    _Module, Placement = BuildGraphPortfolioSelectionFixture()

    Fabric = BuildPlacementAccessFabric(Placement)
    Serialized = Fabric.ToDictionary()["PinAccessWitness"]

    assert Fabric.PinAccessWitness is not None
    assert Fabric.PinAccessWitness.Complete is True
    assert Fabric.PinAccessWitness.CatalogMatched is True
    assert Serialized["WitnessFingerprint"] == (
        Fabric.PinAccessWitness.WitnessFingerprint
    )
    assert Serialized["AccessLength"] == 3
    assert Serialized["SelectionCount"] == 9
    FixedSolve = Fabric.ToDictionary()["FixedPinAccessSolve"]
    assert Fabric.FixedPinAccessSolve is not None
    assert FixedSolve["Status"] == "Feasible"
    assert FixedSolve["Complete"] is True
    assert FixedSolve["Success"] is True
    assert len(FixedSolve["SelectedOptionFingerprints"]) == 9


def test_access_fabric_region_contract_encloses_frozen_outer_geometry():
    """A published fabric ingress must be present in the Rust region."""
    Fabric = replace(
        BuildTestFabric(),
        OuterBounds=(-4, -6, 7, 9),
    )
    Domain = Fabric.TerminalDomains[0]

    (
        MinimumX,
        MaximumX,
        MinimumZ,
        MaximumZ,
        Positions,
        OuterBounds,
    ) = ResolvePlacementAccessFabricRegionContract(
        0,
        2,
        0,
        2,
        Fabric,
        {(Domain.Signal, Domain.Terminal): Domain},
    )

    assert (MinimumX, MaximumX, MinimumZ, MaximumZ) == (-4, 7, -6, 9)
    assert OuterBounds == (-4, -6, 7, 9)
    assert set(Fabric.Nodes).issubset(Positions)
    assert set(Domain.EscapeStubs[0].Path).issubset(Positions)


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


def test_immutable_stub_factor_matches_generic_selection_and_work_cap(
    monkeypatch,
):
    """The derived bitset factor preserves every generic stub decision."""
    Fabric = BuildImmutableStubCapacityFactorFixture()
    OriginalClaimsConflict = (
        AccessFabricModule._PlacementAccessClaimsConflict
    )
    PairConflictChecks = 0

    def CountClaimsConflict(*Arguments, **Keywords):
        nonlocal PairConflictChecks
        PairConflictChecks += 1
        return OriginalClaimsConflict(*Arguments, **Keywords)

    monkeypatch.setattr(
        AccessFabricModule,
        "_PlacementAccessClaimsConflict",
        CountClaimsConflict,
    )
    # The generic path has identical terminal-only constraints when no signal
    # tree or leaf validator is requested.  Change only the topology tag to
    # select that reference implementation; the immutable stub domains and
    # fabric fingerprint remain exactly the same.
    Generic = SolvePlacementAccessFabricCapacity(
        replace(Fabric, TopologyKind="fixed-access-band-v1"),
        MaximumExpansions=64,
        RequireCompleteSignalRoutes=False,
    )
    GenericPairConflictChecks = PairConflictChecks
    PairConflictChecks = 0
    Factor = SolvePlacementAccessFabricCapacity(
        Fabric,
        MaximumExpansions=64,
        RequireCompleteSignalRoutes=False,
    )
    FactorPairConflictChecks = PairConflictChecks

    assert Factor == Generic
    assert Factor.Success is True
    assert Factor.SelectedStubIndices == (
        ("Alpha", (0, 1, -1), 0),
        ("Alpha", (1, 1, -1), 1),
        ("Beta", (2, 1, -1), 1),
    )
    # The factor compiles the same pair constraints into bit masks instead of
    # re-evaluating frozenset intersections at every MRV search state.
    assert GenericPairConflictChecks > 0
    assert FactorPairConflictChecks == 0

    GenericCapped = SolvePlacementAccessFabricCapacity(
        replace(Fabric, TopologyKind="fixed-access-band-v1"),
        MaximumExpansions=2,
        RequireCompleteSignalRoutes=False,
    )
    FactorCapped = SolvePlacementAccessFabricCapacity(
        Fabric,
        MaximumExpansions=2,
        RequireCompleteSignalRoutes=False,
    )
    assert FactorCapped == GenericCapped
    assert FactorCapped.Success is False
    assert FactorCapped.Complete is False
    assert FactorCapped.IncompleteReason == "work-cap-exhausted"


def test_capacity_validator_rejects_leaf_within_one_bounded_solve():
    """A downstream frozen-track proof can reject one access leaf in-place."""
    Fabric = BuildTestFabric()
    FirstStub = Fabric.TerminalDomains[0].EscapeStubs[0]
    SecondIngress = (2, 4, 1)
    SecondClaims = RoutingResourceClaims(
        WireCells=frozenset({SecondIngress}),
        SupportCells=frozenset({(2, 3, 1)}),
        RequiredAirCells=frozenset({(2, 5, 1)}),
        ElectricalCells=frozenset({(1, 4, 1), (3, 4, 1)}),
    )
    SecondStub = PlacementAccessEscapeStub(
        Terminal=FirstStub.Terminal,
        Ingress=SecondIngress,
        Path=(FirstStub.Terminal, SecondIngress),
        PhysicalClaims=SecondClaims,
        CapacityResourceIds=tuple(SecondClaims.ResourceIds),
        Complete=True,
    )
    CandidateFabric = replace(
        Fabric,
        TerminalDomains=(replace(
            Fabric.TerminalDomains[0],
            EscapeStubs=(FirstStub, SecondStub),
        ),),
    )
    ValidatedStubIndices: list[int] = []

    def ValidateFrozenTrackContract(Assignment):
        StubIndex = Assignment.SelectedStubIndices[0][2]
        ValidatedStubIndices.append(StubIndex)
        return StubIndex == 1

    Assignment = SolvePlacementAccessFabricCapacity(
        CandidateFabric,
        MaximumExpansions=16,
        AssignmentValidator=ValidateFrozenTrackContract,
    )

    assert Assignment.Success is True
    assert Assignment.Complete is True
    assert Assignment.SelectedStubIndices[0][2] == 1
    assert ValidatedStubIndices == [0, 1]


def test_derived_perimeter_cycle_enumerates_complete_gap_domain():
    Nodes = (
        (0, 1, 0),
        (1, 1, 0),
        (2, 1, 0),
        (2, 1, 1),
        (2, 1, 2),
        (1, 1, 2),
        (0, 1, 2),
        (0, 1, 1),
    )
    Edges = tuple(
        (Nodes[Index], Nodes[(Index + 1) % len(Nodes)])
        for Index in range(len(Nodes))
    )
    Ingresses = (Nodes[0], Nodes[2], Nodes[5])

    Candidates = _BuildDerivedPerimeterCycleRouteNodeSets(
        Ingresses,
        1,
        Edges,
    )

    assert Candidates is not None
    assert len(Candidates) == len(Ingresses)
    assert all(set(Ingresses) <= set(Candidate) for Candidate in Candidates)
    assert Candidates == tuple(sorted(
        Candidates,
        key=lambda Candidate: (len(Candidate), Candidate),
    ))


def test_derived_perimeter_capacity_freezes_complete_signal_tree():
    Nodes = (
        (0, 1, 0),
        (1, 1, 0),
        (2, 1, 0),
        (2, 1, 1),
        (2, 1, 2),
        (1, 1, 2),
        (0, 1, 2),
        (0, 1, 1),
    )
    Edges = tuple(
        (Nodes[Index], Nodes[(Index + 1) % len(Nodes)])
        for Index in range(len(Nodes))
    )

    def BuildStub(
        Terminal: tuple[int, int, int],
        Ingress: tuple[int, int, int],
    ) -> PlacementAccessEscapeStub:
        Claims = RoutingResourceClaims(
            WireCells=frozenset({Ingress}),
            SupportCells=frozenset({(
                Ingress[0],
                Ingress[1] - 1,
                Ingress[2],
            )}),
            RequiredAirCells=frozenset(),
            ElectricalCells=frozenset(),
        )
        return PlacementAccessEscapeStub(
            Terminal=Terminal,
            Ingress=Ingress,
            Path=(Terminal, Ingress),
            PhysicalClaims=Claims,
            CapacityResourceIds=tuple(Claims.ResourceIds),
            Complete=True,
        )

    FirstStub = BuildStub((-2, 1, 0), Nodes[0])
    SecondStub = BuildStub((4, 1, 2), Nodes[4])
    Fabric = PlacementAccessFabric(
        FabricFingerprint="derived-cycle-fabric",
        Nodes=Nodes,
        Edges=Edges,
        IngressNodes=Nodes,
        PhysicalClaims=RoutingResourceClaims(),
        CapacityResourceIds=(),
        TerminalDomains=(
            PlacementAccessTerminalDomain(
                Signal="Signal",
                Terminal=FirstStub.Terminal,
                EscapeStubs=(FirstStub,),
                Complete=True,
            ),
            PlacementAccessTerminalDomain(
                Signal="Signal",
                Terminal=SecondStub.Terminal,
                EscapeStubs=(SecondStub,),
                Complete=True,
            ),
        ),
        TopologyKind="derived-perimeter-access-v1",
        Complete=True,
        Technology=DefaultRedstoneRoutingTechnology,
    )

    Assignment = SolvePlacementAccessFabricCapacity(Fabric)

    assert Assignment.Success is True
    assert Assignment.Complete is True
    assert len(Assignment.SignalRoutes) == 1
    assert Assignment.SignalRoutes[0][0] == "Signal"
    assert {Nodes[0], Nodes[4]} <= set(Assignment.SignalRoutes[0][1])


def test_derived_perimeter_noncycle_route_domain_is_incomplete():
    Nodes = ((0, 1, 0), (1, 1, 0), (2, 1, 0), (1, 1, 1))
    Edges = (
        (Nodes[0], Nodes[1]),
        (Nodes[1], Nodes[2]),
        (Nodes[1], Nodes[3]),
    )

    def BuildStub(Ingress: tuple[int, int, int]) -> PlacementAccessEscapeStub:
        Claims = RoutingResourceClaims(
            WireCells=frozenset({Ingress}),
            SupportCells=frozenset({(
                Ingress[0],
                Ingress[1] - 1,
                Ingress[2],
            )}),
            RequiredAirCells=frozenset(),
            ElectricalCells=frozenset(),
        )
        return PlacementAccessEscapeStub(
            Terminal=Ingress,
            Ingress=Ingress,
            Path=(Ingress,),
            PhysicalClaims=Claims,
            CapacityResourceIds=tuple(Claims.ResourceIds),
            Complete=True,
        )

    FirstStub = BuildStub(Nodes[0])
    SecondStub = BuildStub(Nodes[2])
    Fabric = PlacementAccessFabric(
        FabricFingerprint="derived-noncycle-fabric",
        Nodes=Nodes,
        Edges=Edges,
        IngressNodes=Nodes,
        PhysicalClaims=RoutingResourceClaims(),
        CapacityResourceIds=(),
        TerminalDomains=(
            PlacementAccessTerminalDomain(
                Signal="Signal",
                Terminal=FirstStub.Terminal,
                EscapeStubs=(FirstStub,),
                Complete=True,
            ),
            PlacementAccessTerminalDomain(
                Signal="Signal",
                Terminal=SecondStub.Terminal,
                EscapeStubs=(SecondStub,),
                Complete=True,
            ),
        ),
        TopologyKind="derived-perimeter-access-v1",
        Complete=True,
        Technology=DefaultRedstoneRoutingTechnology,
    )

    Assignment = SolvePlacementAccessFabricCapacity(Fabric)

    assert Assignment.Success is False
    assert Assignment.Complete is False
    assert Assignment.IncompleteReason == (
        "incomplete-derived-perimeter-route-domain"
    )
    assert Assignment.FirstUnroutableSignal == "Signal"


def test_access_ring_width_and_layers_are_demand_derived():
    Demand = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=12,
        PeakBoundaryDemand=13,
        CoreBounds=(0, 0, 14, 28),
        TrackPitch=3,
        AccessLength=3,
        MinimumRoutingLayerCount=3,
        MaximumRoutingLayerCount=5,
        TechnologyFingerprint="technology",
    )

    Envelopes = DeriveRoutingEnvelopes(Demand)

    assert [Value.RoutingLayerCount for Value in Envelopes] == [3, 4, 5]
    assert all(
        Value.PermittedLayers == tuple(range(Value.RoutingLayerCount))
        for Value in Envelopes
    )
    assert all(Value.AccessRingTrackCount >= 1 for Value in Envelopes)
    assert all(Value.BoundaryCorridorPitch == 3 for Value in Envelopes)


def test_scale_primary_domain_is_fixed_before_routing():
    Plan = BuildPlacementGenerationPlan(
        LocalFirstPhysicalDesignPolicy,
        PreferPackedPlacements=True,
    )
    Module, Incumbent = BuildGraphPortfolioSelectionFixture()

    Small = SelectDerivedPrimaryPlacementRequests(
        Plan,
        True,
        Incumbent=Incumbent,
        Module=Module,
    )
    Scale = SelectDerivedPrimaryPlacementRequests(Plan, False)
    Names = tuple(sorted(Incumbent.Clusters[0]))
    InternalByName = {
        GateValue.Name: GateValue
        for GateValue in Module.Gates
        if GateValue.Name in Names
    }
    Portfolio = BuildPinAlignedPackedClusterPortfolio(
        Names,
        InternalByName,
        LocalFirstPhysicalDesignPolicy.NandPacking.BeamWidth,
    )
    # A single-component domain is fully materialized before capacity
    # selection: the incumbent is a domain member, and each derived row-beam
    # or graph-core layout publishes one immutable, physically-derived
    # terminal-slot domain rather than multiplying the same geometry by
    # arbitrary terminal-layout indexes.
    assert len(Small) >= 2
    assert Small[0].SourceGenerator == "row-beam"
    MaximumDerivedMembers = (
        LocalFirstPhysicalDesignPolicy.NandPacking.RetainedPlacementCandidates
        - 1
    )
    ExpectedGraphCoreIndexes = [
        State.CandidateIndex
        for State in sorted(
            Portfolio.States,
            key=BuildDerivedPinAlignedEnvelopeLowerBoundObjective,
        )[:max(0, MaximumDerivedMembers - 1)]
    ]
    ExpectedGeometry = [
        ("derived-perimeter-row-beam", None),
        *(
            ("derived-pin-aligned-core", CandidateIndex)
            for CandidateIndex in ExpectedGraphCoreIndexes
        ),
    ]
    ExpectedDerived = tuple(
        (SourceGenerator, CandidateIndex, 0)
        for SourceGenerator, CandidateIndex in ExpectedGeometry
    )[:MaximumDerivedMembers]
    assert tuple(
        (
            Value.SourceGenerator,
            Value.GraphCoreCandidateIndex,
            Value.TerminalLayoutVariantIndex,
        )
        for Value in Small[1:]
    ) == ExpectedDerived
    assert len(Small) <= (
        LocalFirstPhysicalDesignPolicy.NandPacking.RetainedPlacementCandidates
    )
    assert all(
        all(
            Forbidden not in Value.SourceGenerator
            for Forbidden in ("retry", "relocation", "direct-only")
        )
        for Value in Small
    )
    assert [Value.SourceGenerator for Value in Scale] == [
        "row-beam",
        "row-beam-direct-only",
    ]
    assert all(
        "conflict-relocation" not in Value.SourceGenerator
        for Value in Scale
    )


def test_envelope_pareto_retention_does_not_collapse_to_smallest_core():
    Plan = BuildPlacementGenerationPlan(
        LocalFirstPhysicalDesignPolicy,
        PreferPackedPlacements=True,
    )
    Module, Incumbent = BuildGraphPortfolioSelectionFixture()
    Names = tuple(sorted(Incumbent.Clusters[0]))
    InternalByName = {
        GateValue.Name: GateValue
        for GateValue in Module.Gates
        if GateValue.Name in Names
    }
    Portfolio = BuildPinAlignedPackedClusterPortfolio(
        Names,
        InternalByName,
        LocalFirstPhysicalDesignPolicy.NandPacking.BeamWidth,
    )

    Requests = SelectDerivedPrimaryPlacementRequests(
        Plan,
        True,
        Incumbent=Incumbent,
        Module=Module,
    )
    RetainedIndexes = {
        Request.GraphCoreCandidateIndex
        for Request in Requests
        if Request.SourceGenerator == "derived-pin-aligned-core"
    }
    SmallestCore = min(
        (State.Objective[2], State.Objective[3])
        for State in Portfolio.States
    )
    EnvelopeTradeoff = next(
        State
        for State in Portfolio.States
        if (
            State.Objective[2] > SmallestCore[0]
            and State.Objective[3] < SmallestCore[1]
        )
    )

    # A shorter-but-wider core is not discarded merely because its NAND-only
    # area is larger.  Its four-sided physical envelope stays in the fixed
    # domain for the later exact access-ring objective to compare.
    assert EnvelopeTradeoff.CandidateIndex in RetainedIndexes
    assert (
        BuildDerivedPinAlignedEnvelopeLowerBoundObjective(EnvelopeTradeoff)
        != BuildDerivedPinAlignedEnvelopeLowerBoundObjective(
            min(Portfolio.States, key=lambda State: State.Objective)
        )
    )


def test_single_component_domain_does_not_silently_fall_back_without_geometry():
    Plan = BuildPlacementGenerationPlan(
        LocalFirstPhysicalDesignPolicy,
        PreferPackedPlacements=True,
    )

    with pytest.raises(
        ValueError,
        match="requires its row-beam incumbent and module",
    ):
        SelectDerivedPrimaryPlacementRequests(Plan, True)


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


def test_full_adder_perimeter_ring_has_four_faces_and_frozen_identity():
    with tempfile.TemporaryDirectory() as Directory:
        Netlist = ToNandOnly(OptimizeLogic(Sv.ParseSvToNetlist(
            InputPath=Path("Examples/FullAdder.sv"),
            TopModule="FullAdder",
            Workdir=Path(Directory),
        )))
    Placement = PlacePcbGraph(
        Netlist,
        RoutingSpacing=4,
        PlacementPolicy=replace(
            LocalFirstPhysicalDesignPolicy.Placement,
            RoutingSpacing=4,
            MaximumRoutingLayers=3,
        ),
        PackingPolicy=replace(
            LocalFirstPhysicalDesignPolicy.NandPacking,
            GraphBeamEnabled=False,
            MaximumLocalRouteLength=(
                LocalFirstPhysicalDesignPolicy
                .NandPacking.DirectConnectMaximumLength
            ),
        ),
    )
    First = BuildPlacementAccessFabric(
        Placement,
        TopologyKind="perimeter-access-ring-v1",
        AccessRingTrackCount=1,
    )
    Second = BuildPlacementAccessFabric(
        Placement,
        TopologyKind="perimeter-access-ring-v1",
        AccessRingTrackCount=1,
    )
    Wide = BuildPlacementAccessFabric(
        Placement,
        TopologyKind="perimeter-access-ring-v1",
        AccessRingTrackCount=2,
    )
    Resources = BuildRoutingResources(Placement.Placed)
    MinimumX = min(Gate.X for Gate in Placement.Placed.PlacedGates) - 3
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Placement.Placed.PlacedGates
    ) + 3
    MinimumZ = min(Gate.Z for Gate in Placement.Placed.PlacedGates) - 3
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Placement.Placed.PlacedGates
    ) + 3

    assert First.TopologyKind == "perimeter-access-ring-v1"
    assert First.AccessRingTrackCount == 1
    assert First.AccessRingFingerprint
    assert First.AccessRingFingerprint == Second.AccessRingFingerprint
    assert Wide.AccessRingTrackCount == 2
    assert Wide.AccessRingFingerprint != First.AccessRingFingerprint
    assert len(Wide.Nodes) > len(First.Nodes)
    assert First.Nodes == Second.Nodes
    assert First.PhysicalClaims.WireCells.isdisjoint(
        Resources.ResourceGraph.ActualBlocks
    )
    assert {MinimumX, MaximumX}.issubset({Node[0] for Node in First.Nodes})
    assert {MinimumZ, MaximumZ}.issubset({Node[2] for Node in First.Nodes})

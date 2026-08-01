import ast
from dataclasses import replace
import inspect
import textwrap
from types import SimpleNamespace

import pytest

import Compiler.Routing.ComponentPipeline as ComponentPipeline
import Compiler.Routing.AuthoritativePlanner as AuthoritativePlanner
from Compiler.Placement.Geometry import PlacedDesign
from Compiler.Placement.PcbFlow import (
    BuildRetainedComponentPlacementSearchDomain,
    IsComponentKeepoutGlobalFailure,
    ReuseRetainedPlacementRoutingResources,
    _PlaceAndRoutePcbWithPolicy,
)
from Compiler.Routing.AuthoritativePlanner import (
    BuildPhysicalBoundaryPortAssignmentFingerprint,
    BuildComponentKeepoutAvoidingGlobalGuides,
    BuildComponentKeepoutGuideCellsByLayer,
    BuildExplicitPhysicalComponentFeedthrough,
    BuildPhysicalExteriorApertureFabric,
    BuildPhysicalGlobalApertureSearchKey,
    BuildPortablePhysicalGlobalApertureContract,
    BuildPhysicalComponentAssemblyPlan,
    ExpandPhysicalComponentGuideChannels,
    FindSignalClaimConflicts,
    IterPhysicalBoundaryPortAssignments,
    PropagateLaneFactorArcConsistency,
    PreparePhysicalGlobalApertureStaticContract,
    PreparePhysicalComponentFeedthroughEndpointDomain,
    PreparePhysicalComponentPortFactorDomain,
    MaterializePhysicalGlobalAperturePath,
    NormalizePhysicalGlobalAperturePath,
    RetainPhysicalGlobalAperturePathTemplate,
    RemoveClosedComponentInternalGuides,
    SelectPhysicalFactorBranchSignal,
    SelectPhysicalBoundaryPortAssignment,
    SolvePreparedPhysicalComponentPortFactorDomain,
    TransformPlanarRoutingPosition,
)
from Compiler.Routing.ChannelPlanner import ChannelPlan
from Compiler.Routing.LocalFirst import CoarseGuidePlan
from Compiler.Routing.ComponentPipeline import (
    BindPhysicalComponentAssemblyGlobalChannels,
    BindPhysicalComponentAssemblyLocalPortSupports,
    BuildUniversalPromotedFabricPortAssignmentFailure,
    BuildDirectionalLocalFactorNoGoods,
    BuildPhysicalLocalPortPairSupportCertificate,
    BuildPhysicalLocalPairProofContextFingerprint,
    CertifyDirectionalLocalContractPortfolio,
    BuildPhysicalPortGlobalContractFingerprint,
    BuildPhysicalPortApertureContractFingerprint,
    BuildPhysicalPortLocalContractFingerprint,
    CompileClosedComponent,
    FinalizePhysicalComponentChannelReservations,
    MaterializePreparedPhysicalPortOptionDomains,
    PromoteCoveredLocalContractNoGoods,
    RecordPhysicalComponentLocalCompilationNoGood,
    ValidatePhysicalBoundaryPortHandoff,
)
from Compiler.Routing.ComponentAccess import (
    BuildComponentCutAccessFeasibilityCertificate,
    ValidateComponentAccessCertificateIdentity,
)
from Compiler.Routing.ComponentRouter import (
    AugmentComponentRoutingFabric,
    BuildCompleteComponentNetPortfolioStaticContext,
    BuildComponentRoutingFabric,
    CompileCompleteComponentNetVariantPortfolio,
    CompileCompleteComponentNetVariantPortfolios,
    GetCachedCompleteComponentNetVariantPortfolio,
)
from Compiler.Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.Models import (
    ClosedComponentInterface,
    ComponentFeedthroughContract,
    ComponentInterfacePort,
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    PhysicalComponentChannelReservation,
    PhysicalComponentBoundaryPortReservation,
    PhysicalGlobalAperturePathTemplate,
    PhysicalLocalPortPairProofRecord,
    RoutingResources,
)
from Compiler.Routing.ResourceGraph import (
    RoutingResourceClaims,
    RoutingResourceId,
    RoutingResourceKind,
)
from Compiler.Routing.Pcb import (
    ClassifyPhysicalComponentAssemblyFailure,
    ReplanPhysicalComponentAssembly,
)
from Compiler.Routing.Reliability import RoutingDeadline
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
)


def _Claims(Nodes):
    Nodes = frozenset(Nodes)
    return RoutingResourceClaims(
        WireCells=Nodes,
        SupportCells=frozenset(
            (X, Y - 1, Z) for X, Y, Z in Nodes
        ),
        ElectricalCells=Nodes,
    )


def test_local_contract_fingerprint_is_translation_stable_and_geometry_exact():
    def Port(Delta=(0, 0, 0), LocalZ=0):
        def Move(Position):
            return tuple(
                Position[Index] + Delta[Index]
                for Index in range(3)
            )

        LocalPath = tuple(map(
            Move,
            ((0, 7, 0), (1, 7, LocalZ)),
        ))
        GlobalPath = tuple(map(
            Move,
            ((1, 7, LocalZ), (2, 7, LocalZ)),
        ))
        return SimpleNamespace(
            Direction="output",
            FabricDomainFingerprint="domain",
            FabricAttachment=Move((0, 7, 0)),
            Attachment=Move((1, 7, 0)),
            OwnedTerminals=(Move((0, 7, 0)),),
            LocalPath=LocalPath,
            GlobalPath=GlobalPath,
            Claims=_Claims((*LocalPath, *GlobalPath)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    Base = BuildPhysicalPortLocalContractFingerprint(Port())
    Translated = BuildPhysicalPortLocalContractFingerprint(
        Port((30, 4, 12))
    )
    DifferentLocalSeam = BuildPhysicalPortLocalContractFingerprint(
        Port(LocalZ=1)
    )

    assert Base == Translated
    assert Base != DifferentLocalSeam


def test_global_contract_excludes_component_local_port_geometry():
    def Port(LocalZ):
        LocalPath = ((0, 7, LocalZ), (1, 7, 0))
        GlobalPath = ((1, 7, 0), (2, 7, 0))
        return SimpleNamespace(
            Direction="output",
            FabricDomainFingerprint=f"local-domain-{LocalZ}",
            FabricAttachment=(0, 7, LocalZ),
            Attachment=(1, 7, 0),
            OwnedTerminals=((0, 7, LocalZ),),
            LocalPath=LocalPath,
            GlobalPath=GlobalPath,
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    First = Port(0)
    Second = Port(1)

    assert BuildPhysicalPortGlobalContractFingerprint(First) == (
        BuildPhysicalPortGlobalContractFingerprint(Second)
    )
    assert BuildPhysicalPortLocalContractFingerprint(First) != (
        BuildPhysicalPortLocalContractFingerprint(Second)
    )


def _BoundaryPort(Signal, X):
    Attachment = (X, 7, 0)
    GlobalPath = (Attachment, (X + 1, 7, 0))
    Port = SimpleNamespace(
        Direction="output",
        Attachment=Attachment,
        GlobalPath=GlobalPath,
        GlobalClaims=_Claims(GlobalPath),
        Capacity=1,
    )
    return PhysicalComponentBoundaryPortReservation(
        Signal=Signal,
        Direction=Port.Direction,
        Attachment=Attachment,
        GlobalPath=GlobalPath,
        GlobalClaims=Port.GlobalClaims,
        Capacity=1,
        ChannelContractFingerprint=f"channel:{Signal}",
        GlobalContractFingerprint=(
            BuildPhysicalPortGlobalContractFingerprint(Port)
        ),
        ApertureContractFingerprint=(
            BuildPhysicalPortApertureContractFingerprint(Port)
        ),
        ReservationFingerprint=f"boundary:{X}",
    )


def test_global_boundary_selector_uses_only_compatible_global_claims():
    Conflict = _BoundaryPort("Alpha", 0)
    Alternate = _BoundaryPort("Alpha", 10)
    Beta = _BoundaryPort("Beta", 0)

    Selected = SelectPhysicalBoundaryPortAssignment({
        "Alpha": (Conflict, Alternate),
        "Beta": (Beta,),
    })

    assert Selected is not None
    assert {Value.Signal: Value.Attachment for Value in Selected} == {
        "Alpha": Alternate.Attachment,
        "Beta": Beta.Attachment,
    }


def test_rejected_global_boundary_tuple_advances_without_local_identity():
    First = _BoundaryPort("Alpha", 0)
    Second = _BoundaryPort("Alpha", 10)
    Domains = {"Alpha": (First, Second)}
    Initial = SelectPhysicalBoundaryPortAssignment(Domains)
    assert Initial is not None

    Advanced = SelectPhysicalBoundaryPortAssignment(
        Domains,
        RejectedAssignmentFingerprints=(
            BuildPhysicalBoundaryPortAssignmentFingerprint(Initial),
        ),
    )

    assert Advanced is not None
    assert Advanced != Initial


def test_global_boundary_iteration_prioritizes_retained_global_contract():
    First = _BoundaryPort("Alpha", 0)
    Preferred = _BoundaryPort("Alpha", 10)

    Selected = next(IterPhysicalBoundaryPortAssignments(
        {"Alpha": (First, Preferred)},
        PreferredGlobalContractsBySignal={
            "Alpha": Preferred.GlobalContractFingerprint,
        },
    ))

    assert Selected == (Preferred,)


def test_global_boundary_iteration_prunes_proven_aperture_clause():
    Rejected = _BoundaryPort("Alpha", 0)
    Alternate = _BoundaryPort("Alpha", 10)

    Selected = next(IterPhysicalBoundaryPortAssignments(
        {"Alpha": (Rejected, Alternate)},
        RejectedGlobalApertureClauses=(frozenset((
            ("Alpha", Rejected.ApertureContractFingerprint),
        )),),
    ))

    assert Selected == (Alternate,)


def test_persistent_boundary_iteration_observes_new_global_clause():
    Ports = tuple(_BoundaryPort("Alpha", X) for X in (0, 10, 20))
    Baseline = tuple(IterPhysicalBoundaryPortAssignments({"Alpha": Ports}))
    Clauses = set()
    Frontier = iter(IterPhysicalBoundaryPortAssignments(
        {"Alpha": Ports},
        RejectedGlobalApertureClauses=Clauses,
    ))

    assert next(Frontier) == Baseline[0]
    Clauses.add(frozenset((
        ("Alpha", Baseline[1][0].ApertureContractFingerprint),
    )))
    assert next(Frontier) == Baseline[2]


def _BoundaryAttachmentIndexSequence(Assignments):
    Signals = tuple(sorted(
        Value.Signal
        for Value in Assignments[0]
    ))
    DomainAttachments = {
        Signal: tuple(sorted({
            Value.Attachment
            for Assignment in Assignments
            for Value in Assignment
            if Value.Signal == Signal
        }))
        for Signal in Signals
    }
    return tuple(
        tuple(
            DomainAttachments[Signal].index(next(
                Value.Attachment
                for Value in Assignment
                if Value.Signal == Signal
            ))
            for Signal in Signals
        )
        for Assignment in Assignments
    )


def test_priority_innermost_boundary_iteration_changes_requested_axis():
    Assignments = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": tuple(
                _BoundaryPort("Alpha", X) for X in (20, 10)
            ),
            "Beta": tuple(
                _BoundaryPort("Beta", X) for X in (120, 110)
            ),
        },
        PriorityInnermostSignals=("Alpha",),
    ))

    Indices = _BoundaryAttachmentIndexSequence(Assignments)
    assert Indices[:2] == (
        (0, 0),
        (1, 0),
    )


def test_priority_innermost_boundary_iteration_is_rename_order_invariant():
    First = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": tuple(
                _BoundaryPort("Alpha", X) for X in (30, 20, 10)
            ),
            "Beta": tuple(
                _BoundaryPort("Beta", X) for X in (120, 110)
            ),
        },
        PriorityInnermostSignals=("Alpha",),
    ))
    RenamedAndReordered = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Zulu": tuple(
                _BoundaryPort("Zulu", X) for X in (110, 120)
            ),
            "Able": tuple(
                _BoundaryPort("Able", X) for X in (10, 30, 20)
            ),
        },
        PriorityInnermostSignals=("Able",),
    ))

    assert _BoundaryAttachmentIndexSequence(First) == (
        _BoundaryAttachmentIndexSequence(RenamedAndReordered)
    )


def test_priority_innermost_boundary_iteration_preserves_full_domain():
    Assignments = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": tuple(
                _BoundaryPort("Alpha", X) for X in (10, 20)
            ),
            "Beta": tuple(
                _BoundaryPort("Beta", X) for X in (110, 120, 130)
            ),
        },
        PriorityInnermostSignals=("Alpha",),
    ))

    Indices = _BoundaryAttachmentIndexSequence(Assignments)
    assert len(Indices) == 6
    assert len(set(Indices)) == 6
    assert set(Indices) == {
        (AlphaIndex, BetaIndex)
        for AlphaIndex in range(2)
        for BetaIndex in range(3)
    }


def test_priority_innermost_boundary_iteration_orders_multiple_hints():
    Assignments = tuple(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": tuple(
                _BoundaryPort("Alpha", X) for X in (10, 20)
            ),
            "Beta": tuple(
                _BoundaryPort("Beta", X) for X in (110, 120)
            ),
            "Gamma": tuple(
                _BoundaryPort("Gamma", X) for X in (210, 220)
            ),
        },
        PriorityInnermostSignals=("Alpha", "Beta"),
    ))

    assert _BoundaryAttachmentIndexSequence(Assignments)[:2] == (
        (0, 0, 0),
        (0, 1, 0),
    )


def test_global_boundary_selection_projects_prepared_local_support_only():
    BetaBoundary = _BoundaryPort("Beta", 100)
    AlphaBoundaries = {
        "unsupported": _BoundaryPort("Alpha", 20),
        "supported": _BoundaryPort("Alpha", 30),
        "local-conflict": _BoundaryPort("Alpha", 40),
        "certified-no-good": _BoundaryPort("Alpha", 50),
        "global-conflict": _BoundaryPort("Alpha", 100),
    }

    def Aperture(Boundary, Fingerprint):
        return SimpleNamespace(
            Signal=Boundary.Signal,
            Direction=Boundary.Direction,
            Capacity=Boundary.Capacity,
            Attachment=Boundary.Attachment,
            GlobalPath=Boundary.GlobalPath,
            ChannelContractFingerprint=(
                Boundary.ChannelContractFingerprint
            ),
            GlobalContractFingerprint=(
                Boundary.GlobalContractFingerprint
            ),
            ApertureContractFingerprint=(
                Boundary.ApertureContractFingerprint
            ),
            ApertureOptionFingerprint=Fingerprint,
        )

    def LocalFactor(
        Signal,
        Fingerprint,
        Contract,
        ClaimNode,
    ):
        return SimpleNamespace(
            Signal=Signal,
            LocalAccessFingerprint=Fingerprint,
            LocalContractFingerprint=Contract,
            FabricDomainFingerprint="fabric:" + Fingerprint,
            LocalClaims=_Claims((ClaimNode,)),
        )

    AlphaSupported = LocalFactor(
        "Alpha",
        "local-supported",
        "contract-supported",
        (300, 7, 0),
    )
    AlphaConflict = LocalFactor(
        "Alpha",
        "local-conflict",
        "contract-conflict",
        (400, 7, 0),
    )
    AlphaNoGood = LocalFactor(
        "Alpha",
        "local-no-good",
        "contract-no-good",
        (500, 7, 0),
    )
    BetaLocal = LocalFactor(
        "Beta",
        "local-beta",
        "contract-beta",
        (400, 7, 0),
    )
    Apertures = {
        Name: Aperture(Boundary, "aperture:" + Name)
        for Name, Boundary in AlphaBoundaries.items()
    }
    BetaAperture = Aperture(BetaBoundary, "aperture:beta")
    Supports = (
        SimpleNamespace(
            Signal="Alpha",
            LocalAccessFingerprint=AlphaSupported.LocalAccessFingerprint,
            ApertureOptionFingerprint=(
                Apertures["supported"].ApertureOptionFingerprint
            ),
        ),
        SimpleNamespace(
            Signal="Alpha",
            LocalAccessFingerprint=AlphaConflict.LocalAccessFingerprint,
            ApertureOptionFingerprint=(
                Apertures["local-conflict"].ApertureOptionFingerprint
            ),
        ),
        SimpleNamespace(
            Signal="Alpha",
            LocalAccessFingerprint=AlphaNoGood.LocalAccessFingerprint,
            ApertureOptionFingerprint=(
                Apertures["certified-no-good"].ApertureOptionFingerprint
            ),
        ),
        SimpleNamespace(
            Signal="Alpha",
            LocalAccessFingerprint=AlphaSupported.LocalAccessFingerprint,
            ApertureOptionFingerprint=(
                Apertures["global-conflict"].ApertureOptionFingerprint
            ),
        ),
        SimpleNamespace(
            Signal="Beta",
            LocalAccessFingerprint=BetaLocal.LocalAccessFingerprint,
            ApertureOptionFingerprint=BetaAperture.ApertureOptionFingerprint,
        ),
    )
    BoundaryDomains = {
        "Alpha": tuple(AlphaBoundaries.values()),
        "Beta": (BetaBoundary,),
    }

    GlobalOnly = tuple(IterPhysicalBoundaryPortAssignments(
        BoundaryDomains
    ))
    SupportAware = tuple(IterPhysicalBoundaryPortAssignments(
        BoundaryDomains,
        LocalAccessFactorsBySignal={
            "Alpha": (AlphaSupported, AlphaConflict, AlphaNoGood),
            "Beta": (BetaLocal,),
        },
        ApertureFactorsBySignal={
            "Alpha": tuple(Apertures.values()),
            "Beta": (BetaAperture,),
        },
        LocalApertureSupportBySignal={
            "Alpha": Supports[:-1],
            "Beta": Supports[-1:],
        },
        CertifiedLocalNoGoodClauses=(frozenset((
            ("Alpha", AlphaNoGood.LocalContractFingerprint),
            ("Beta", "local-signal-domain:prepared-solver"),
        )),),
        PortSolverCacheKey="prepared-solver",
    ))

    # Global compatibility independently removes only the claims conflict.
    assert {
        Values[0].ApertureContractFingerprint for Values in GlobalOnly
    } == {
        Boundary.ApertureContractFingerprint
        for Name, Boundary in AlphaBoundaries.items()
        if Name != "global-conflict"
    }
    # The existential local projection removes the unsupported aperture, the
    # locally conflicting pair, and the proof-qualified current-key no-good.
    assert len(SupportAware) == 1
    SelectedBySignal = {
        Value.Signal: Value for Value in SupportAware[0]
    }
    assert SelectedBySignal["Alpha"] == AlphaBoundaries["supported"]
    assert SelectedBySignal["Beta"] == BetaBoundary
    assert not hasattr(SelectedBySignal["Alpha"], "LocalAccessFingerprint")
    assert not hasattr(SelectedBySignal["Alpha"], "LocalClaims")


def test_global_boundary_leaf_projects_only_certified_local_no_goods():
    Boundaries = {
        Signal: _BoundaryPort(Signal, X)
        for Signal, X in (("Alpha", 10), ("Beta", 110))
    }
    Apertures = {
        Signal: SimpleNamespace(
            Signal=Signal,
            Direction=Boundary.Direction,
            Capacity=Boundary.Capacity,
            Attachment=Boundary.Attachment,
            GlobalPath=Boundary.GlobalPath,
            ChannelContractFingerprint=(
                Boundary.ChannelContractFingerprint
            ),
            GlobalContractFingerprint=(
                Boundary.GlobalContractFingerprint
            ),
            ApertureContractFingerprint=(
                Boundary.ApertureContractFingerprint
            ),
            ApertureOptionFingerprint="aperture:" + Signal,
        )
        for Signal, Boundary in Boundaries.items()
    }
    LocalFactors = {
        Signal: SimpleNamespace(
            Signal=Signal,
            LocalAccessFingerprint="local:" + Signal,
            LocalContractFingerprint="contract:" + Signal,
            FabricDomainFingerprint="fabric:" + Signal,
            # Equal claims prove that proof-only projection does not perform
            # a speculative local compatibility solve before global routing.
            LocalClaims=_Claims(((300, 7, 0),)),
        )
        for Signal in Boundaries
    }
    Supports = {
        Signal: (SimpleNamespace(
            Signal=Signal,
            LocalAccessFingerprint=Factor.LocalAccessFingerprint,
            ApertureOptionFingerprint=(
                Apertures[Signal].ApertureOptionFingerprint
            ),
        ),)
        for Signal, Factor in LocalFactors.items()
    }
    Arguments = dict(
        DomainsBySignal={
            Signal: (Boundary,)
            for Signal, Boundary in Boundaries.items()
        },
        LocalAccessFactorsBySignal={
            Signal: (Factor,)
            for Signal, Factor in LocalFactors.items()
        },
        ApertureFactorsBySignal={
            Signal: (Aperture,)
            for Signal, Aperture in Apertures.items()
        },
        LocalApertureSupportBySignal=Supports,
        PortSolverCacheKey="prepared-solver",
        CertifiedNoGoodProjectionOnly=True,
    )

    assert len(tuple(IterPhysicalBoundaryPortAssignments(**Arguments))) == 1
    Clause = frozenset(
        (
            Signal,
            "fabric-domain:" + Factor.FabricDomainFingerprint,
        )
        for Signal, Factor in LocalFactors.items()
    )
    assert tuple(IterPhysicalBoundaryPortAssignments(
        **Arguments,
        CertifiedLocalNoGoodClauses=(Clause,),
    )) == ()


def test_port_seam_ignores_conflicts_owned_only_by_foreign_corridors():
    ForeignNode = (0, 7, 0)
    PortNode = (10, 7, 0)
    Claims = {
        "ForeignA": _Claims((ForeignNode,)),
        "ForeignB": _Claims((ForeignNode,)),
        "Port": _Claims((PortNode,)),
    }

    assert not FindSignalClaimConflicts(Claims, "Port")
    assert FindSignalClaimConflicts(
        {
            **Claims,
            "Port": _Claims((ForeignNode,)),
        },
        "Port",
    )


def test_global_guide_detours_around_component_keepout_without_feedthrough():
    OriginalForeignGuide = frozenset(
        (X, Z)
        for X in range(-8, 9)
        for Z in range(-1, 2)
    )
    ComponentGuide = frozenset(
        (X, 1) for X in range(-1, 2)
    )
    Plan = CoarseGuidePlan(
        Guides={
            "Foreign": OriginalForeignGuide,
            "Port": ComponentGuide,
        },
        Layers={"Foreign": 0, "Port": 0},
        Axes={"Foreign": "X", "Port": "X"},
        Lanes={"Foreign": 0, "Port": 1},
        Usage={},
        Overflow={},
        LocalSignals=frozenset(),
        Iterations=(),
    )

    Result, Detoured = BuildComponentKeepoutAvoidingGlobalGuides(
        Plan,
        ComponentPortSignals=frozenset(("Port",)),
        EnvelopeMinimum=(-1, 0, -1),
        EnvelopeMaximum=(1, 8, 1),
        TrackPitch=3,
        ReservedPortGuideCells=frozenset(((-2, 0),)),
    )

    assert Detoured == ("Foreign",)
    assert Result.Guides["Port"] == ComponentGuide
    assert not any(
        -2 <= X <= 2 and -2 <= Z <= 2
        for X, Z in Result.Guides["Foreign"]
    )
    assert not any(
        abs(X + 2) + abs(Z) <= 3
        for X, Z in Result.Guides["Foreign"]
    )
    Pending = [min(Result.Guides["Foreign"])]
    Reached = {Pending[0]}
    while Pending:
        X, Z = Pending.pop()
        for Neighbor in (
            (X - 1, Z),
            (X + 1, Z),
            (X, Z - 1),
            (X, Z + 1),
        ):
            if (
                Neighbor in Result.Guides["Foreign"]
                and Neighbor not in Reached
            ):
                Reached.add(Neighbor)
                Pending.append(Neighbor)
    assert Reached == set(Result.Guides["Foreign"])


def test_global_guide_detours_around_exterior_port_access_halo():
    Guide = frozenset((X, 6) for X in range(4, 11))
    Plan = CoarseGuidePlan(
        Guides={"Foreign": Guide},
        Layers={"Foreign": 0},
        Axes={"Foreign": "X"},
        Lanes={"Foreign": 6},
        Usage={},
        Overflow={},
        LocalSignals=frozenset(),
        Iterations=(),
    )

    Result, Detoured = BuildComponentKeepoutAvoidingGlobalGuides(
        Plan,
        ComponentPortSignals=frozenset(),
        EnvelopeMinimum=(-1, 0, -1),
        EnvelopeMaximum=(1, 8, 1),
        TrackPitch=3,
        ReservedPortGuideCells=frozenset(((7, 5),)),
    )

    assert Detoured == ("Foreign",)
    assert not any(
        abs(X - 7) + abs(Z - 5) <= 3
        for X, Z in Result.Guides["Foreign"]
    )


def test_global_guide_detour_bounds_include_projected_keepout_extent():
    Guide = frozenset((X, 0) for X in range(-8, 9))
    Plan = CoarseGuidePlan(
        Guides={"Foreign": Guide},
        Layers={"Foreign": 0},
        Axes={"Foreign": "X"},
        Lanes={"Foreign": 0},
        Usage={},
        Overflow={},
        LocalSignals=frozenset(),
        Iterations=(),
    )
    # Model electrical keepout projection extending beyond the logical
    # component envelope on both sides of the guide.  The exterior router
    # must bound its search from this physical claim, not from the smaller
    # logical envelope.
    ProjectedKeepout = frozenset(
        (X, Z)
        for X in range(-2, 3)
        for Z in range(-5, 6)
    )

    Result, Detoured = BuildComponentKeepoutAvoidingGlobalGuides(
        Plan,
        ComponentPortSignals=frozenset(),
        EnvelopeMinimum=(-1, 0, -1),
        EnvelopeMaximum=(1, 8, 1),
        TrackPitch=1,
        ComponentKeepoutGuideCells=ProjectedKeepout,
    )

    assert Detoured == ("Foreign",)
    assert not Result.Guides["Foreign"] & ProjectedKeepout
    assert any(abs(Z) > 6 for _X, Z in Result.Guides["Foreign"])


def test_explicit_physical_feedthrough_freezes_one_declared_fabric_lane():
    FabricNodes = frozenset(
        (X, 1, 0) for X in range(-1, 2)
    )
    FabricEdges = frozenset(
        (
            ((X, 1, 0), (X + 1, 1, 0))
            for X in range(-1, 1)
        )
    )

    Contract, Guide = BuildExplicitPhysicalComponentFeedthrough(
        "Foreign",
        0,
        frozenset((X, 0) for X in range(-4, 5)),
        ComponentKeepoutGuideCells=frozenset(
            (X, 0) for X in range(-1, 2)
        ),
        ReservedPortAccessGuideCells=frozenset(),
        FabricNodes=FabricNodes,
        FabricEdges=FabricEdges,
        FabricIngressNodes=frozenset(((-1, 1, 0), (1, 1, 0))),
        ResourceGraph=_ResourceGraph(),
        MinimumPlacementY=0,
    )

    assert Contract.Signal == "Foreign"
    assert Contract.Capacity == 1
    assert Contract.EndpointPairs == (((-1, 1, 0), (1, 1, 0)),)
    assert Contract.ReservedPathNodes == (
        (-1, 1, 0),
        (0, 1, 0),
        (1, 1, 0),
    )
    assert Contract.Claims == _ResourceGraph().BuildRouteClaims(
        Contract.ReservedPathNodes
    )
    assert Contract.ReservationFingerprint
    assert Guide == frozenset((X, 0) for X in range(-4, 5))


def test_prepared_feedthrough_endpoint_domain_preserves_relative_geometry():
    def Build(DeltaX: int, DeltaZ: int):
        Nodes = frozenset(
            (X + DeltaX, 1, Z + DeltaZ)
            for Z in (0, 3)
            for X in range(-1, 2)
        )
        Edges = frozenset(
            (
                (X + DeltaX, 1, Z + DeltaZ),
                (X + 1 + DeltaX, 1, Z + DeltaZ),
            )
            for Z in (0, 3)
            for X in range(-1, 1)
        )
        Ingress = frozenset(
            (X + DeltaX, 1, Z + DeltaZ)
            for Z in (0, 3)
            for X in (-1, 1)
        )
        return PreparePhysicalComponentFeedthroughEndpointDomain(
            "Foreign",
            0,
            FabricNodes=Nodes,
            FabricEdges=Edges,
            FabricIngressNodes=Ingress,
            FabricFingerprint="relative-fabric",
            ResourceGraph=_ResourceGraph(),
            MinimumPlacementY=0,
        )

    First = Build(0, 0)
    Second = Build(20, -7)

    assert First.Complete is True
    assert len(First.Candidates) == 2
    # A physical assembly domain remains placement-specific so parallel
    # translated lanes are not merged.  Their normalized geometry is still
    # identical for completed-template cache identity.
    assert First.DomainFingerprint != Second.DomainFingerprint
    Normalize = lambda Candidate: tuple(
        (
            Node[0] - Candidate.ReservedPathNodes[0][0],
            Node[1] - Candidate.ReservedPathNodes[0][1],
            Node[2] - Candidate.ReservedPathNodes[0][2],
        )
        for Node in Candidate.ReservedPathNodes
    )
    assert sorted(map(Normalize, First.Candidates)) == sorted(map(
        Normalize,
        Second.Candidates,
    ))


def test_explicit_feedthrough_skips_a_port_blocked_preferred_lane():
    FabricNodes = frozenset(
        (X, 1, Z)
        for Z in (0, 3)
        for X in range(-1, 2)
    )
    FabricEdges = frozenset(
        ((X, 1, Z), (X + 1, 1, Z))
        for Z in (0, 3)
        for X in range(-1, 1)
    )
    # The z=0 lane has the best geometric score, but a selected port-access
    # ring seals it from both exterior guide components.  The complete fixed
    # feedthrough domain must advance to the viable z=3 lane instead of
    # treating the first lane's failed joins as an assembly proof.
    PortAccessRing = frozenset((
        *((X, -1) for X in range(-2, 3)),
        *((X, 1) for X in range(-2, 3)),
        (-2, 0),
        (2, 0),
    ))

    EndpointDomain = PreparePhysicalComponentFeedthroughEndpointDomain(
        "Foreign",
        0,
        FabricNodes=FabricNodes,
        FabricEdges=FabricEdges,
        FabricIngressNodes=frozenset((
            (-1, 1, 0),
            (1, 1, 0),
            (-1, 1, 3),
            (1, 1, 3),
        )),
        FabricFingerprint="two-lane-fabric",
        ResourceGraph=_ResourceGraph(),
        MinimumPlacementY=0,
    )
    Contract, Guide = BuildExplicitPhysicalComponentFeedthrough(
        "Foreign",
        0,
        frozenset((X, 0) for X in range(-4, 5)),
        ComponentKeepoutGuideCells=frozenset(
            (X, 0) for X in range(-1, 2)
        ),
        ReservedPortAccessGuideCells=PortAccessRing,
        FabricNodes=FabricNodes,
        FabricEdges=FabricEdges,
        FabricIngressNodes=frozenset((
            (-1, 1, 0),
            (1, 1, 0),
            (-1, 1, 3),
            (1, 1, 3),
        )),
        ResourceGraph=_ResourceGraph(),
        MinimumPlacementY=0,
        PreparedEndpointDomain=EndpointDomain,
    )

    assert Contract.ReservedPathNodes == (
        (-1, 1, 3),
        (0, 1, 3),
        (1, 1, 3),
    )
    assert frozenset((X, 3) for X in range(-1, 2)) <= Guide
    assert not Guide & PortAccessRing


def test_cyclic_feedthrough_endpoint_domain_is_explicitly_incomplete():
    Nodes = frozenset((
        (0, 1, 0),
        (1, 1, 0),
        (1, 1, 1),
        (0, 1, 1),
    ))
    Edges = frozenset((
        ((0, 1, 0), (1, 1, 0)),
        ((1, 1, 0), (1, 1, 1)),
        ((1, 1, 1), (0, 1, 1)),
        ((0, 1, 1), (0, 1, 0)),
    ))
    Domain = PreparePhysicalComponentFeedthroughEndpointDomain(
        "Foreign",
        0,
        FabricNodes=Nodes,
        FabricEdges=Edges,
        FabricIngressNodes=Nodes,
        FabricFingerprint="cyclic-fabric",
        ResourceGraph=_ResourceGraph(),
        MinimumPlacementY=0,
    )

    assert Domain.Complete is False
    with pytest.raises(RoutingStageError) as Captured:
        BuildExplicitPhysicalComponentFeedthrough(
            "Foreign",
            0,
            frozenset((X, 0) for X in range(-2, 4)),
            ComponentKeepoutGuideCells=frozenset((
                (0, 0),
                (1, 0),
            )),
            ReservedPortAccessGuideCells=frozenset(),
            FabricNodes=Nodes,
            FabricEdges=Edges,
            FabricIngressNodes=Nodes,
            ResourceGraph=_ResourceGraph(),
            MinimumPlacementY=0,
            PreparedEndpointDomain=Domain,
        )

    assert Captured.value.Failure.Reason == (
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete
    )
    assert Captured.value.Failure.Diagnostics[
        "FeedthroughEndpointDomainComplete"
    ] is False


def test_explicit_feedthrough_reports_complete_fixed_candidate_exhaustion():
    BlockedLane = ((0, 1, 0), (1, 1, 0))
    PortAccessRing = frozenset({
        (-1, 0),
        (0, -1),
        (0, 1),
        (2, 0),
        (1, -1),
        (1, 1),
    })

    with pytest.raises(RoutingStageError) as Captured:
        BuildExplicitPhysicalComponentFeedthrough(
            "Foreign",
            0,
            frozenset((X, 0) for X in range(-4, 5)),
            ComponentKeepoutGuideCells=frozenset(
                (X, 0) for X in range(-1, 3)
            ),
            ReservedPortAccessGuideCells=PortAccessRing,
            FabricNodes=frozenset(BlockedLane),
            FabricEdges=frozenset(((BlockedLane[0], BlockedLane[1]),)),
            FabricIngressNodes=frozenset(BlockedLane),
            ResourceGraph=_ResourceGraph(),
            MinimumPlacementY=0,
        )

    Failure = Captured.value.Failure
    assert Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Failure.Diagnostics["FeedthroughCandidateDomainComplete"] is True
    assert Failure.Diagnostics["FabricPathCandidateCount"] == 2
    assert Failure.Diagnostics["OwnershipSearchComplete"] is True
    assert Failure.Diagnostics["ImplicitForeignTransitDomainCount"] == 0
    # This helper proves one fixed port/feedthrough candidate domain.  Only
    # the enclosing boundary-plan enumerator may certify the global cut.
    assert "GlobalPlanDomainComplete" not in Failure.Diagnostics


def test_physical_port_detour_uses_only_external_portal_ownership():
    Source = inspect.getsource(
        SolvePreparedPhysicalComponentPortFactorDomain
    )
    DetourStart = Source.index(
        "ReservedPortGuideCells = frozenset("
    )
    DetourEnd = Source.index(
        "ComponentKeepoutGuideCellsByLayer=",
        DetourStart,
    )
    DetourCall = Source[DetourStart:DetourEnd]

    assert "for Position in Port.GlobalPath" in DetourCall
    assert "Port.LocalPath" not in DetourCall
    assert "Port.Claims" not in DetourCall


def test_feedthrough_endpoint_prescreen_precedes_candidate_detour_search():
    Source = inspect.getsource(
        SolvePreparedPhysicalComponentPortFactorDomain
    )
    Prescreen = Source.index(
        '"FeedthroughEndpointPrescreenComplete": True'
    )
    Detour = Source.index(
        "BuildComponentKeepoutAvoidingGlobalGuides(",
        Prescreen,
    )

    assert Prescreen < Detour


def test_physical_port_seam_has_exclusive_local_and_global_claims():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Port = _Assembly(Problem, Resources).Plan.Ports[0]

    assert frozenset(Port.LocalPath) & frozenset(Port.GlobalPath) == {
        Port.Attachment
    }
    assert Port.LocalClaims == Problem.ResourceGraph.BuildRouteClaims(
        frozenset(Port.LocalPath)
    )
    assert Port.GlobalClaims == Problem.ResourceGraph.BuildRouteClaims(
        frozenset(Port.GlobalPath)
    )
    assert Port.Claims == Problem.ResourceGraph.BuildRouteClaims(frozenset((
        *Port.LocalPath,
        *Port.GlobalPath,
    )))


def test_global_aperture_search_identity_excludes_local_access_witness():
    Arguments = (
        "Alpha",
        (12, 7, 4),
        (1, 0, 0),
        2,
        frozenset(((15, 4), (16, 4))),
        "foreign-claims",
    )

    First = BuildPhysicalGlobalApertureSearchKey(*Arguments)
    Second = BuildPhysicalGlobalApertureSearchKey(*Arguments)

    assert First == Second
    assert (0, 7, 4) not in First
    assert (11, 7, 4) not in First


def test_exterior_aperture_fabric_is_rename_and_order_invariant():
    Arguments = dict(
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(2, 2, 2),
        Technology=DefaultRedstoneRoutingTechnology,
        MinimumPlacementY=0,
        Layer=0,
        KeepoutColumns=((5, 1),),
    )
    First = BuildPhysicalExteriorApertureFabric(
        CompleteCoarseGuideCellsBySignal={
            "Alpha": ((-2, 1), (-1, 1)),
            "Beta": ((3, 1), (4, 1)),
        },
        DeclaredPortalIngressNodesBySignal={
            "Alpha": ((-1, 1, 1),),
            "Beta": ((3, 1, 1),),
        },
        **Arguments,
    )
    RenamedAndReordered = BuildPhysicalExteriorApertureFabric(
        CompleteCoarseGuideCellsBySignal={
            "RenamedBeta": ((4, 1), (3, 1)),
            "RenamedAlpha": ((-1, 1), (-2, 1)),
        },
        DeclaredPortalIngressNodesBySignal={
            "RenamedBeta": ((3, 1, 1),),
            "RenamedAlpha": ((-1, 1, 1),),
        },
        **Arguments,
    )

    assert First.FabricFingerprint == RenamedAndReordered.FabricFingerprint
    assert First.SignalGuideIngressGeometry == (
        RenamedAndReordered.SignalGuideIngressGeometry
    )
    assert First.AllowedNodes == RenamedAndReordered.AllowedNodes


def test_exterior_aperture_fabric_enforces_closed_ownership():
    Fabric = BuildPhysicalExteriorApertureFabric(
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(2, 2, 2),
        CompleteCoarseGuideCellsBySignal={
            "Port": ((-2, 1), (1, 1), (4, 1)),
        },
        DeclaredPortalIngressNodesBySignal={
            "Port": ((-1, 1, 1),),
        },
        Technology=DefaultRedstoneRoutingTechnology,
        MinimumPlacementY=0,
        Layer=0,
        KeepoutColumns=((3, 1),),
    )

    assert Fabric.AllowsNode((-2, 1, 1))
    assert Fabric.AllowsNode((4, 1, 1))
    assert Fabric.AllowsNode((-1, 1, 1))
    assert not Fabric.AllowsNode((0, 1, 1))
    assert not Fabric.AllowsNode((1, 1, 1))
    assert not Fabric.AllowsNode((2, 1, 1))
    assert not Fabric.AllowsNode((3, 1, 1))
    assert (1, 1) not in Fabric.AllowedColumns
    assert (3, 1) not in Fabric.AllowedColumns


def test_portable_global_aperture_contract_reuses_rigid_planar_geometry():
    Attachment = (12, 7, 4)
    Direction = (1, 0, 0)
    Targets = frozenset(((18, 7, 4), (18, 7, 5)))
    EnvelopeMinimum = (8, 5, 1)
    EnvelopeMaximum = (12, 9, 7)
    Blocked = frozenset(((11, 3), (12, 3)))
    Claims = _Claims(((16, 7, 3),))

    Base = BuildPortablePhysicalGlobalApertureContract(
        Attachment,
        Direction,
        2,
        Targets,
        EnvelopeMinimum,
        EnvelopeMaximum,
        Blocked,
        Claims,
        DefaultRedstoneRoutingTechnology,
    )
    Prepared = PreparePhysicalGlobalApertureStaticContract(
        Direction,
        2,
        Targets,
        EnvelopeMinimum,
        EnvelopeMaximum,
        Blocked,
        Claims,
        DefaultRedstoneRoutingTechnology,
    )
    for CandidateAttachment in (
        Attachment,
        (11, 7, 4),
        (12, 7, 5),
    ):
        Direct = BuildPortablePhysicalGlobalApertureContract(
            CandidateAttachment,
            Direction,
            2,
            Targets,
            EnvelopeMinimum,
            EnvelopeMaximum,
            Blocked,
            Claims,
            DefaultRedstoneRoutingTechnology,
        )
        Hoisted = BuildPortablePhysicalGlobalApertureContract(
            CandidateAttachment,
            Direction,
            2,
            Targets,
            EnvelopeMinimum,
            EnvelopeMaximum,
            Blocked,
            Claims,
            DefaultRedstoneRoutingTechnology,
            PreparedStaticContract=Prepared,
        )
        assert Hoisted == Direct

    def Move(Position):
        return TransformPlanarRoutingPosition(
            Position,
            "Rotate90",
            (40, 3, 20),
        )

    RotatedCorners = tuple(
        Move((X, Y, Z))
        for X in (EnvelopeMinimum[0], EnvelopeMaximum[0])
        for Y in (EnvelopeMinimum[1], EnvelopeMaximum[1])
        for Z in (EnvelopeMinimum[2], EnvelopeMaximum[2])
    )
    RotatedMinimum = tuple(
        min(Position[Index] for Position in RotatedCorners)
        for Index in range(3)
    )
    RotatedMaximum = tuple(
        max(Position[Index] for Position in RotatedCorners)
        for Index in range(3)
    )
    RotatedClaims = RoutingResourceClaims(
        WireCells=frozenset(map(Move, Claims.WireCells)),
        SupportCells=frozenset(map(Move, Claims.SupportCells)),
        RequiredAirCells=frozenset(map(Move, Claims.RequiredAirCells)),
        ElectricalCells=frozenset(map(Move, Claims.ElectricalCells)),
    )
    Rotated = BuildPortablePhysicalGlobalApertureContract(
        Move(Attachment),
        TransformPlanarRoutingPosition(Direction, "Rotate90"),
        2,
        frozenset(map(Move, Targets)),
        RotatedMinimum,
        RotatedMaximum,
        frozenset(
            (
                Move((X, Attachment[1], Z))[0],
                Move((X, Attachment[1], Z))[2],
            )
            for X, Z in Blocked
        ),
        RotatedClaims,
        DefaultRedstoneRoutingTechnology,
    )

    assert Base[:2] == Rotated[:2]


def test_portable_global_aperture_path_round_trip_and_bounded_retention():
    Attachment = (12, 7, 4)
    Path = ((12, 7, 4), (13, 7, 4), (14, 7, 4))
    Canonical = NormalizePhysicalGlobalAperturePath(
        Path,
        Attachment,
        "Rotate90",
    )
    assert MaterializePhysicalGlobalAperturePath(
        Canonical,
        Attachment,
        "Rotate90",
    ) == Path

    Cache = {}
    for Index in range(3):
        RetainPhysicalGlobalAperturePathTemplate(
            Cache,
            PhysicalGlobalAperturePathTemplate(
                ContractFingerprint=str(Index),
                CanonicalContract=(Index,),
                CanonicalPath=Canonical,
            ),
            MaximumEntries=2,
        )
    assert tuple(Cache) == ("1", "2")


def test_factor_branching_prioritizes_learned_pair_constraints():
    Selected = SelectPhysicalFactorBranchSignal(
        {"Small": 1, "Left": 12, "Right": 8},
        (
            frozenset((
                ("Left", "local:left"),
                ("Right", "local:right"),
            )),
        ),
    )

    assert Selected == "Right"


def test_component_keepout_projection_is_owned_by_physical_layer():
    ResourceGraph = _ResourceGraph()
    LayerZeroY = ResourceGraph.Technology.RoutingY(0, 0)
    CellsByLayer = BuildComponentKeepoutGuideCellsByLayer(
        ResourceGraph.BuildRouteClaims(((3, LayerZeroY, 5),)),
        ResourceGraph,
        MinimumPlacementY=0,
        LayerCount=2,
    )

    assert (3, 5) in CellsByLayer[0]
    assert (3, 5) not in CellsByLayer[1]


def test_global_guide_rejects_a_foreign_corridor_consumed_by_keepout():
    Plan = CoarseGuidePlan(
        Guides={"Foreign": frozenset(((0, 0), (1, 0)))},
        Layers={"Foreign": 0},
        Axes={"Foreign": "X"},
        Lanes={"Foreign": 0},
        Usage={},
        Overflow={},
        LocalSignals=frozenset(),
        Iterations=(),
    )

    with pytest.raises(RoutingStageError) as Captured:
        BuildComponentKeepoutAvoidingGlobalGuides(
            Plan,
            ComponentPortSignals=frozenset(),
            EnvelopeMinimum=(-1, 0, -1),
            EnvelopeMaximum=(1, 8, 1),
            TrackPitch=3,
        )

    assert Captured.value.Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert (
        Captured.value.Failure.Diagnostics[
            "ImplicitForeignTransitDomainCount"
        ]
        == 0
    )


def test_global_guide_discards_unanchored_keepout_cavity_fragment():
    Guide = frozenset((X, 0) for X in range(-8, 9))
    Plan = ChannelPlan(
        Profiles={},
        SignalOrder=("Foreign",),
        TrunkSignals=frozenset(),
        Guides={"Foreign": Guide},
        CorridorUsage={},
        CorridorCosts={},
        CorridorCapacity=1,
        Layers={"Foreign": 0},
        ResourceUsage={},
        ResourceOverflow={},
        ResourceClaimsBySignal={},
        SourceAccessTransitions={"Foreign": ((-8, 1, 0),)},
        TargetAccessTransitions={
            "Foreign": {(8, 1, 0): ((8, 1, 0),)},
        },
    )
    RingKeepout = frozenset(
        (X, Z)
        for X in range(-2, 3)
        for Z in range(-2, 3)
        if max(abs(X), abs(Z)) == 2
    )

    Result, Detoured = BuildComponentKeepoutAvoidingGlobalGuides(
        Plan,
        ComponentPortSignals=frozenset(),
        EnvelopeMinimum=(-2, 0, -2),
        EnvelopeMaximum=(2, 8, 2),
        TrackPitch=1,
        ComponentKeepoutGuideCells=RingKeepout,
    )

    assert Detoured == ("Foreign",)
    assert (0, 0) not in Result.Guides["Foreign"]
    Pending = [min(Result.Guides["Foreign"])]
    Reached = {Pending[0]}
    while Pending:
        X, Z = Pending.pop()
        for Neighbor in (
            (X - 1, Z),
            (X + 1, Z),
            (X, Z - 1),
            (X, Z + 1),
        ):
            if (
                Neighbor in Result.Guides["Foreign"]
                and Neighbor not in Reached
            ):
                Reached.add(Neighbor)
                Pending.append(Neighbor)
    assert Reached == set(Result.Guides["Foreign"])


def test_closed_component_internal_guides_leave_global_plan():
    Plan = CoarseGuidePlan(
        Guides={
            "Internal": frozenset(((0, 0), (1, 0))),
            "Port": frozenset(((2, 0), (3, 0))),
            "Foreign": frozenset(((4, 0), (5, 0))),
        },
        Layers={"Internal": 0, "Port": 1, "Foreign": 2},
        Axes={"Internal": "X", "Port": "X", "Foreign": "X"},
        Lanes={"Internal": 0, "Port": 1, "Foreign": 2},
        Usage={(0, 0, 0): 1, (1, 2, 0): 1, (2, 4, 0): 1},
        Overflow={(0, 0, 0): 1},
        LocalSignals=frozenset(("Internal", "Port")),
        Iterations=(),
    )

    Result = RemoveClosedComponentInternalGuides(
        Plan,
        frozenset(("Internal",)),
    )

    assert set(Result.Guides) == {"Port", "Foreign"}
    assert set(Result.Layers) == {"Port", "Foreign"}
    assert Result.LocalSignals == frozenset(("Port",))
    assert not Result.Overflow
    assert all(Position[0] != 0 for Position in Result.Usage)


def test_hierarchical_pipeline_has_no_local_portfolio_or_recursive_fallback():
    FunctionTree = ast.parse(textwrap.dedent(
        inspect.getsource(_PlaceAndRoutePcbWithPolicy)
    ))
    Calls = tuple(
        Node
        for Node in ast.walk(FunctionTree)
        if isinstance(Node, ast.Call)
    )
    LocalCompileCalls = tuple(
        Call
        for Call in Calls
        if isinstance(Call.func, ast.Name)
        and Call.func.id == "CompileClosedComponent"
    )
    assert len(LocalCompileCalls) == 1
    assert {
        Keyword.arg
        for Keyword in LocalCompileCalls[0].keywords
    } == {
        "AssemblyPlan",
        "DeadlineSeconds",
        "WorkCheck",
        "VariantPortfolioCache",
        "NetVariantConstructionCache",
        "RouteClaimsConstructionCache",
        "NetVariantDiscoveryStateCache",
    }
    assert not any(
        isinstance(Call.func, ast.Name)
        and Call.func.id == "_PlaceAndRoutePcbWithPolicy"
        for Call in Calls
    )
    DeckCalls = tuple(
        Call
        for Call in Calls
        if isinstance(Call.func, ast.Name)
        and Call.func.id == "BuildBoundedInterClusterRoutingDeck"
    )
    assert len(DeckCalls) == 2
    for DeckCall in DeckCalls:
        ComponentVariant = next(
            Keyword.value
            for Keyword in DeckCall.keywords
            if Keyword.arg == "ComponentVariant"
        )
        assert isinstance(ComponentVariant, ast.Name)
        assert ComponentVariant.id == "ComponentVariantForState"
    assert any(
        Keyword.arg == "ForcedAffectedClusters"
        for Keyword in DeckCalls[1].keywords
    )


def test_physical_port_certificate_filter_uses_each_ports_guide_layer():
    FunctionTree = ast.parse(textwrap.dedent(
        inspect.getsource(PreparePhysicalComponentPortFactorDomain)
    ))
    PortLoop = next(
        Node
        for Node in ast.walk(FunctionTree)
        if isinstance(Node, ast.For)
        and isinstance(Node.target, ast.Name)
        and Node.target.id == "Port"
        and any(
            isinstance(Child, ast.Name)
            and Child.id == "CertifiedCandidate"
            for Child in ast.walk(Node)
        )
    )
    PortLayerAssignments = tuple(
        Node
        for Node in ast.walk(PortLoop)
        if isinstance(Node, ast.Assign)
        and any(
            isinstance(Target, ast.Name)
            and Target.id == "PortLayer"
            for Target in Node.targets
        )
    )
    assert len(PortLayerAssignments) == 1
    CertifiedLayerComparisons = tuple(
        Node
        for Node in ast.walk(PortLoop)
        if isinstance(Node, ast.Compare)
        and isinstance(Node.left, ast.Attribute)
        and Node.left.attr == "Layer"
        and any(
            isinstance(Value, ast.Name) and Value.id == "PortLayer"
            for Value in Node.comparators
        )
    )
    assert CertifiedLayerComparisons
    RoutingYCalls = tuple(
        Node
        for Node in ast.walk(PortLoop)
        if isinstance(Node, ast.Call)
        and isinstance(Node.func, ast.Attribute)
        and Node.func.attr == "RoutingY"
    )
    assert RoutingYCalls
    assert all(
        any(
            isinstance(Argument, ast.Name)
            and Argument.id == "PortLayer"
            for Argument in Call.args
        )
        for Call in RoutingYCalls
    )


def test_placement_domain_is_exhausted_before_selecting_another_component():
    assert BuildRetainedComponentPlacementSearchDomain(
        ("placement-a", "placement-b"),
        MaximumComponentSelections=3,
    ) == (
        (0, 0, "placement-a"),
        (0, 1, "placement-b"),
        (1, 0, "placement-a"),
        (1, 1, "placement-b"),
        (2, 0, "placement-a"),
        (2, 1, "placement-b"),
    )


def test_retained_placement_resources_reuse_identity_across_components():
    Cache = {}
    BuildCount = 0

    def Build():
        nonlocal BuildCount
        BuildCount += 1
        return SimpleNamespace(
            ResourceGraph=object(),
            RawPortalGeometryCaches=("whole-design",),
        )

    First, FirstHit = ReuseRetainedPlacementRoutingResources(
        Cache,
        "placement",
        Build,
    )
    Second, SecondHit = ReuseRetainedPlacementRoutingResources(
        Cache,
        "placement",
        Build,
    )
    Other, OtherHit = ReuseRetainedPlacementRoutingResources(
        Cache,
        "other-placement",
        Build,
    )

    assert not FirstHit
    assert SecondHit
    assert not OtherHit
    assert First is Second
    assert First.ResourceGraph is Second.ResourceGraph
    assert Other is not First
    assert BuildCount == 2


def test_physical_factor_deadline_preserves_typed_stage_and_diagnostics():
    Original = RoutingStageError(RoutingFailure(
        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
        Stage="PhysicalComponentAssembly",
        Detail="deadline",
        RepairActions=("RetrySignal",),
        Diagnostics={
            "Stage": "physical-port-capacity",
            "AssignedPortCount": 3,
            "PortCount": 9,
            "ExpansionCount": 388864,
        },
    ))
    Classified = ClassifyPhysicalComponentAssemblyFailure(
        Original,
        Operation="prepare",
        Resources=SimpleNamespace(
            RejectedPhysicalComponentPortReservationsBySignal={
                "Carry3": {"reservation-b", "reservation-a"},
            },
            RejectedPhysicalComponentPortAssignmentFingerprints={
                "assignment-b",
                "assignment-a",
            },
        ),
    )
    Failure = Classified.Failure
    assert Failure.Reason == (
        RoutingFailureReason.PhysicalComponentAssemblyIncomplete
    )
    assert Failure.Stage == "PhysicalComponentAssemblyIncomplete"
    assert Failure.RepairActions == ()
    assert Failure.Diagnostics["Stage"] == "physical-port-capacity"
    Classification = Failure.Diagnostics[
        "PhysicalComponentAssemblyClassification"
    ]
    assert Classification == {
        "Operation": "prepare",
        "ActiveFactorStage": "physical-port-capacity",
        "Complete": False,
        "FactorDiagnostics": {
            "AssignedPortCount": 3,
            "PortCount": 9,
            "ExpansionCount": 388864,
            "RejectedSignalReservationFingerprintsBySignal": {
                "Carry3": [
                    "reservation-a",
                    "reservation-b",
                ],
            },
            "RejectedSignalReservationCount": 2,
            "RejectedPortAssignmentFingerprints": [
                "assignment-a",
                "assignment-b",
            ],
        },
        "ExecutableRetryAllowed": False,
        "FlatFallbackAllowed": False,
        "SignalLevelFallbackAllowed": False,
    }


def test_ordinary_global_starvation_rejects_component_keepout_not_ports():
    Plan = SimpleNamespace(
        Ports=(SimpleNamespace(Signal="ComponentPort"),),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.DetailedSearchExhausted,
        Stage="Candidate",
        AffectedNets=("OrdinaryGlobal",),
        Detail=(
            "the immutable routed-component state blocked a complete "
            "bounded global candidate window"
        ),
        Diagnostics={
            "Action": "advance-routed-component-global-starvation",
        },
    )

    assert IsComponentKeepoutGlobalFailure(Failure, Plan)
    assert not IsComponentKeepoutGlobalFailure(
        replace(Failure, AffectedNets=("ComponentPort",)),
        Plan,
    )
    assert not IsComponentKeepoutGlobalFailure(
        replace(
            Failure,
            Diagnostics={"Action": "regenerate-affected-candidates"},
        ),
        Plan,
    )


class _ResourceGraph:
    GraphVersion = "test-resource-graph"
    Technology = DefaultRedstoneRoutingTechnology
    Nodes = ()
    Edges = ()

    def BuildRouteClaims(self, Nodes):
        return _Claims(Nodes)

    def BuildPrimitive(self, _First, _Second):
        return object()

def _Problem(Signal="Alpha", Delta=(0, 0, 0)):
    def Move(Position):
        return tuple(
            Position[Index] + Delta[Index] for Index in range(3)
        )

    Cells = tuple(map(Move, (
        (0, 7, 0),
        (1, 7, 0),
        (2, 7, 0),
    )))
    Channel = SimpleNamespace(
        PhysicalModel="test-tree",
        ComponentId=3,
        InterfaceFingerprint="logical-interface",
        DeclaredFeedthroughSignals=(),
        AffectedClusters=(0,),
        AffectedSignals=(Signal,),
        Lanes=(
            SimpleNamespace(
                Cells=Cells,
                IngressNodes=(Cells[0], Cells[-1]),
            ),
        ),
    )
    Fabric = BuildComponentRoutingFabric(Channel)

    def Candidate(Terminal):
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=f"{Signal}:{Terminal}",
            Attachment=Terminal,
            Path=(Terminal,),
            Claims=_Claims((Terminal,)),
        )

    Source = Cells[0]
    Target = Cells[-1]
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="translation-stable-interface",
        ComponentId=3,
        OwnedSignals=(Signal,),
        Ports=(
            ComponentInterfacePort(
                Signal=Signal,
                Direction="output",
                OwnedTerminals=(Source, Target),
                ExternalTerminalCount=1,
            ),
        ),
    )
    return ComponentRoutingProblem(
        ProblemFingerprint="problem",
        PlacementFingerprint=f"placement:{Delta}",
        LocalTemplateFingerprint="local",
        SelectedClusters=(0,),
        ComponentSignals=(Signal,),
        LocalClaims=(),
        Fabric=Fabric,
        OwnedTerminalDomains=(
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Source,
                TerminalRole="source",
                TerminalFingerprint="source",
                Candidates=(Candidate(Source),),
            ),
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Target,
                TerminalRole="target",
                TerminalFingerprint="target",
                Candidates=(Candidate(Target),),
            ),
        ),
        ExternalContinuationTerminals=(
            (Signal, Move((12, 7, 0)), "target"),
        ),
        ForeignEscapeDomains=(),
        MaximumPowerDistance=15,
        DomainComplete=True,
        ResourceGraph=_ResourceGraph(),
        Interface=Interface,
    )


def _Guide(Problem):
    Signal = Problem.ComponentSignals[0]
    Position = Problem.Fabric.Nodes[-1]
    Resource = RoutingResourceId(
        RoutingResourceKind.Wire,
        Position,
    )
    return ChannelPlan(
        Profiles={},
        SignalOrder=(Signal,),
        TrunkSignals=frozenset(),
        Guides={Signal: frozenset(
            (X, Position[2])
            for X in range(Position[0], Position[0] + 21)
        )},
        CorridorUsage={},
        CorridorCosts={},
        CorridorCapacity=1,
        Layers={Signal: 0},
        ResourceUsage={Resource: 1},
        ResourceOverflow={},
        ResourceClaimsBySignal={Signal: frozenset((Resource,))},
        SourceAccessTransitions={},
        TargetAccessTransitions={},
    )


def _Placed(Problem):
    Signal = Problem.ComponentSignals[0]
    return SimpleNamespace(
        ComponentGraph=SimpleNamespace(
            StructuralFingerprint="component-graph",
            Channels=(
                SimpleNamespace(
                    Signal=Signal,
                    FeedthroughComponentIds=(),
                ),
            ),
        ),
    )


def _AccessCertificate(Problem, Placed, Resources):
    MinimumPlacementY = min(
        Value[1] for Value in Problem.Fabric.Nodes
    ) - 7
    return BuildComponentCutAccessFeasibilityCertificate(
        Problem,
        Resources.ResourceGraph,
        LayerCount=1,
        MinimumPlacementY=MinimumPlacementY,
        ComponentGraphFingerprint=(
            Placed.ComponentGraph.StructuralFingerprint
        ),
    )


def _Assembly(Problem, Resources=None):
    Resources = Resources or RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Placed = _Placed(Problem)
    Certificate = _AccessCertificate(Problem, Placed, Resources)
    return BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=Certificate,
    )


def test_production_tree_fabric_has_complete_feedthrough_endpoint_domain():
    Problem = _Problem("Foreign")
    Domain = PreparePhysicalComponentFeedthroughEndpointDomain(
        "Foreign",
        0,
        FabricNodes=frozenset(Problem.Fabric.Nodes),
        FabricEdges=frozenset(Problem.Fabric.Edges),
        FabricIngressNodes=frozenset(Problem.Fabric.IngressNodes),
        FabricFingerprint=Problem.Fabric.FabricFingerprint,
        ResourceGraph=Problem.ResourceGraph,
        MinimumPlacementY=6,
    )

    assert "tree" in Problem.Fabric.TopologyKind
    assert Problem.Fabric.Complete is True
    assert Domain.Complete is True
    assert len(Domain.Candidates) == 1


def _BindAssemblyForLocalCompilation(Assembly):
    Candidates = {}
    for Port in Assembly.Plan.GlobalBoundaryPorts:
        Nodes = frozenset(Port.GlobalPath)
        Candidates[Port.Signal] = SimpleNamespace(
            CandidateId=f"test-global:{Port.Signal}",
            Layer=0,
            Guide=frozenset((Position[0], Position[2]) for Position in Nodes),
            Nodes=Nodes,
            Claims=Assembly.Problem.ResourceGraph.BuildRouteClaims(Nodes),
            SourcePortalId=f"test-source:{Port.Signal}",
            TargetPortalIds={},
            RepeaterWaypoints=(),
        )
    GloballyBound = BindPhysicalComponentAssemblyGlobalChannels(
        Assembly,
        SimpleNamespace(RoutingAssignment=SimpleNamespace(
            SelectedCandidates=Candidates,
        )),
        Assembly.Problem.ResourceGraph,
    )
    return BindPhysicalComponentAssemblyLocalPortSupports(GloballyBound)


def _PreparedFactorDomainFixture(DomainFingerprint, **Domains):
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    return {
        "PreparedPhysicalComponentPortFactorDomain": SimpleNamespace(
            DomainFingerprint=DomainFingerprint,
            Complete=True,
            Feasible=True,
            ComponentGraphFingerprint="component-graph",
            ResourceGraphFingerprint="resource-graph",
            Problem=SimpleNamespace(Fabric=SimpleNamespace(
                FabricFingerprint="fabric",
            )),
            AccessCertificate=SimpleNamespace(
                TechnologyFingerprint="technology",
            ),
        ),
        "PhysicalComponentFactorPortOptionDomainCache": {
            (CacheKey, Signal): tuple(Options)
            for Signal, Options in Domains.items()
        },
    }


def _PairProofRecords(
    CurrentSignal,
    CurrentContracts,
    CompleteSignal,
    CompleteContract,
):
    return tuple(
        PhysicalLocalPortPairProofRecord(
            CurrentSignal=CurrentSignal,
            CurrentContract=CurrentContract,
            CompleteSignal=CompleteSignal,
            CompleteContract=CompleteContract,
            ProofDomainFingerprint="domain:" + CurrentContract,
            ProofFingerprint="proof:" + CurrentContract,
            Status="architectural-unsatisfiable",
            Complete=True,
            Feasible=False,
        )
        for CurrentContract in CurrentContracts
    )


def test_physical_port_factor_preparation_is_complete_and_retained():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )

    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    assert Preparation.Complete
    assert Preparation.Feasible
    assert Preparation.DomainFingerprint
    assert Preparation.LaneFactorsBySignal
    assert (
        Resources.PreparedPhysicalComponentPortFactorDomain
        is Preparation
    )


def test_local_proof_materializes_only_requested_prepared_factor_domain():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    First = MaterializePreparedPhysicalPortOptionDomains(
        Preparation,
        Resources,
        ("Alpha",),
    )
    Second = MaterializePreparedPhysicalPortOptionDomains(
        Preparation,
        Resources,
        ("Alpha",),
    )

    assert First["Alpha"]
    assert Second["Alpha"] is First["Alpha"]
    assert all(Option.Signal == "Alpha" for Option in First["Alpha"])
    LocalFactors = dict(Preparation.LocalAccessFactorsBySignal)["Alpha"]
    ExpectedLocalContracts = {
        Value.LocalContractFingerprint for Value in LocalFactors
    }
    MaterializedLocalContracts = [
        BuildPhysicalPortLocalContractFingerprint(Option)
        for Option in First["Alpha"]
    ]
    assert set(MaterializedLocalContracts) == ExpectedLocalContracts
    assert len(MaterializedLocalContracts) == len(
        ExpectedLocalContracts
    )
    LocalAccessFingerprintByContract = {
        Value.LocalContractFingerprint: Value.LocalAccessFingerprint
        for Value in LocalFactors
    }
    Supports = dict(Preparation.LocalApertureSupportBySignal)["Alpha"]
    ExpectedRepresentativeByContract = {
        Contract: min(
            (
                Value.ReservationFingerprint,
                Value.SupportFingerprint,
            )
            for Value in Supports
            if Value.LocalAccessFingerprint == LocalAccessFingerprint
        )[0]
        for Contract, LocalAccessFingerprint
        in LocalAccessFingerprintByContract.items()
    }
    assert {
        BuildPhysicalPortLocalContractFingerprint(Option): (
            Option.ReservationFingerprint
        )
        for Option in First["Alpha"]
    } == ExpectedRepresentativeByContract
    assert not Resources.PhysicalComponentPortOptionDomainCache
    assert len(Resources.PhysicalComponentFactorPortOptionDomainCache) == 1


def test_selected_plan_local_proof_core_has_complete_prepared_factor_domain(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    monkeypatch.setattr(
        "Compiler.Routing.AuthoritativePlanner."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )
    Assembly = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
    )
    Signals = tuple(Port.Signal for Port in Assembly.Plan.Ports)

    Domains = MaterializePreparedPhysicalPortOptionDomains(
        Preparation,
        Resources,
        Signals,
    )

    assert set(Domains) == set(Signals)
    assert all(Domains.values())
    assert all(
        BuildPhysicalPortLocalContractFingerprint(Port)
        in {
            BuildPhysicalPortLocalContractFingerprint(Option)
            for Option in Domains[Port.Signal]
        }
        for Port in Assembly.Plan.Ports
    )
    assert not Resources.PhysicalComponentPortOptionDomainCache
    assert len(Resources.PhysicalComponentFactorPortOptionDomainCache) == len(
        Signals
    )


def test_physical_guide_layers_are_normalized_to_authoritative_limit():
    Plan = CoarseGuidePlan(
        Guides={"Alpha": frozenset(((0, 0),))},
        Layers={"Alpha": 3},
        Axes={"Alpha": "X"},
        Lanes={"Alpha": 0},
        Usage={},
        Overflow={},
        LocalSignals=frozenset(),
        Iterations=(),
    )

    Normalized = ExpandPhysicalComponentGuideChannels(Plan, 3)

    assert Normalized.Layers["Alpha"] == 2
    assert all(Layer < 3 for Layer in Normalized.Layers.values())


def test_physical_guide_layer_aligns_to_complete_certified_port_domain():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Guide = replace(_Guide(Problem), Layers={"Alpha": 1})

    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    assert Preparation.CoarsePlan.Layers["Alpha"] == 0
    assert dict(Preparation.DiagnosticsBySignal)["Alpha"][
        "CertifiedGuideLayerReassignment"
    ] == {
        "OriginalLayer": 1,
        "AssignedLayer": 0,
        "CertifiedLayers": [0],
    }


def test_physical_port_stem_exits_layer_exact_global_keepout():
    Assembly = _Assembly(_Problem())
    Keepout = frozenset(Assembly.Plan.GlobalKeepoutNodes)

    for Port in Assembly.Plan.Ports:
        assert len(Port.GlobalPath) >= 2
        Endpoint = Port.GlobalPath[-1]
        Previous = Port.GlobalPath[-2]
        Direction = tuple(
            Endpoint[Index] - Previous[Index]
            for Index in range(3)
        )
        Next = tuple(
            Endpoint[Index] + Direction[Index]
            for Index in range(3)
        )
        assert Endpoint not in Keepout
        assert Next not in Keepout


def test_prepared_physical_port_factor_rejects_component_graph_mismatch():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    with pytest.raises(RoutingStageError) as Raised:
        SolvePreparedPhysicalComponentPortFactorDomain(
            replace(
                Preparation,
                ComponentGraphFingerprint="changed-component-graph",
            ),
            Resources,
        )

    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentAssemblyIdentityMismatch
    )
    assert "access-certificate-component-graph" in (
        Raised.value.Failure.Diagnostics["IdentityMismatches"]
    )


def test_prepared_physical_port_factor_resume_does_not_rebuild_lanes(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Events = []
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
        WorkCheck=Events.append,
    )
    LaneEventCount = sum(
        Event.get("Stage") == "physical-port-lane-assignment"
        for Event in Events
    )
    monkeypatch.setattr(
        "Compiler.Routing.AuthoritativePlanner."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )

    Assembly = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=Events.append,
    )

    assert Assembly.Plan.Complete
    assert sum(
        Event.get("Stage") == "physical-port-lane-assignment"
        for Event in Events
    ) == LaneEventCount


def test_port_solver_does_not_run_local_realizability_before_plan():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    Events = []

    Assembly = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=Events.append,
    )

    assert Assembly.Plan.Ports
    SelectedEvent = next(
        Event
        for Event in Events
        if Event.get("Stage") == "physical-port-plan-selected"
    )
    assert SelectedEvent[
        "LocalRealizabilityCheckCountBySignal"
    ] == {}


def test_proven_aperture_cut_prunes_before_global_boundary_selection():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    BoundaryDomains = dict(
        Preparation.BoundaryPortReservationsBySignal
    )
    FirstBoundary = SelectPhysicalBoundaryPortAssignment(
        BoundaryDomains
    )
    assert FirstBoundary is not None
    First = FirstBoundary[0]
    # A completed single-port global-domain proof is applied before boundary
    # enumeration; the rejected aperture must never become an assembly plan.
    Resources.RejectedPhysicalComponentPortReservationsBySignal.setdefault(
        First.Signal,
        set(),
    ).add(First.ApertureContractFingerprint)
    Events = []

    Assembly = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=Events.append,
    )

    RejectedFingerprint = BuildPhysicalBoundaryPortAssignmentFingerprint(
        FirstBoundary
    )
    assert RejectedFingerprint not in (
        Resources
        .RejectedPhysicalComponentBoundaryAssignmentFingerprints
    )
    assert Assembly.Plan.GlobalBoundaryPorts[0].ApertureContractFingerprint != (
        First.ApertureContractFingerprint
    )
    BoundaryEvents = [
        Event for Event in Events
        if Event.get("Stage") == "physical-port-global-boundary-selected"
    ]
    assert BoundaryEvents
    assert BoundaryEvents[0]["LocalCompositePlanningStarted"] is False
    assert BoundaryEvents[0]["BoundaryAssignmentFingerprint"] != (
        RejectedFingerprint
    )


def test_prepared_physical_port_replan_reuses_factorized_domain(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    monkeypatch.setattr(
        "Compiler.Routing.AuthoritativePlanner."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )
    FirstEvents = []
    First = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=FirstEvents.append,
    )
    assert not Resources.PhysicalComponentPortOptionDomainCache
    RejectedPlanClause = frozenset(
        (
            Port.Signal,
            BuildPhysicalPortApertureContractFingerprint(Port),
        )
        for Port in First.Plan.Ports
    )
    Resources.RejectedPhysicalComponentPortReservationSets.add(
        RejectedPlanClause
    )

    SecondEvents = []
    Second = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=SecondEvents.append,
    )

    assert (
        Second.Plan.PortAssignmentFingerprint
        != First.Plan.PortAssignmentFingerprint
    )
    assert not any(
        Event.get("Stage") == "physical-port-option-domain"
        for Event in FirstEvents
    )
    assert not any(
        Event.get("Stage") == "physical-port-option-domain"
        for Event in SecondEvents
    )
    assert not any(
        Event.get("Stage") == "physical-port-option-domain-published"
        for Event in (*FirstEvents, *SecondEvents)
    )
    SelectedEvent = next(
        Event
        for Event in SecondEvents
        if Event.get("Stage") == "physical-port-plan-selected"
    )
    assert SelectedEvent["FactorizedPortSearch"] is True
    assert SelectedEvent["PreparedApertureFactorDomainReused"] is True
    assert SelectedEvent["PersistentPortCspStateReused"] is True

    ColdResources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    ColdResources.RejectedPhysicalComponentPortReservationSets.add(
        RejectedPlanClause
    )
    Cold = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        ColdResources,
    )

    assert Cold.Plan.PortAssignmentFingerprint == (
        Second.Plan.PortAssignmentFingerprint
    )


def test_promoted_local_factor_no_good_prunes_prepared_replan_before_seams():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    First = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
    )
    Port = First.Plan.Ports[0]
    PortSolverCacheKey = (
        ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
            Preparation.DomainFingerprint,
        )
    )
    PromotedFactorKey = (
        Port.Signal,
        "local-factor-domain:"
        + PortSolverCacheKey
        + ":"
        + Port.FabricDomainFingerprint,
    )
    Resources.RejectedPhysicalComponentPortReservationSets.add(
        frozenset((PromotedFactorKey,))
    )

    with pytest.raises(RoutingStageError) as Raised:
        SolvePreparedPhysicalComponentPortFactorDomain(
            Preparation,
            Resources,
        )

    Failure = Raised.value.Failure
    Diagnostics = Failure.Diagnostics
    assert Failure.Reason == (
        RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    )
    assert Diagnostics["PreparedApertureFactorDomainReused"] is True
    assert Diagnostics["PersistentPortCspStateReused"] is True
    assert Diagnostics["PortAssignmentUnsatCoreDirectReuse"] is True
    assert Diagnostics["PortAssignmentUnsatCoreNoGoodKeys"] == [
        list(PromotedFactorKey)
    ]
    assert Diagnostics["FactorDomainPropagationCount"] == 1
    assert Diagnostics["FactorArcClosureCount"] == 0
    assert Diagnostics["SeamFactorExpansionCount"] == 0
    assert Diagnostics["PortOptionGenerationCounts"] == {}
    assert Diagnostics["PortOptionMaterializationComplete"] is False


def test_proof_neutral_port_assignment_deferral_selects_a_distinct_plan(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    monkeypatch.setattr(
        "Compiler.Routing.AuthoritativePlanner."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )
    First = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
    )
    (
        Resources
        .DeferredPhysicalComponentPortAssignmentFingerprints
        .add(First.Plan.PortAssignmentFingerprint)
    )

    Second = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
    )

    assert Second.Plan.PortAssignmentFingerprint != (
        First.Plan.PortAssignmentFingerprint
    )
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Resources.DeferredPhysicalComponentPortAssignmentFingerprints == {
        First.Plan.PortAssignmentFingerprint
    }


def test_physical_channel_finalization_requires_an_exterior_guide():
    Assembly = _Assembly(_Problem())
    Port = Assembly.Plan.Ports[0]
    Channel = next(
        Value
        for Value in Assembly.Plan.Corridors
        if Value.Signal == Port.Signal
    )
    Channel = replace(
        Channel,
        GuideCells=((
            Assembly.Plan.EnvelopeMinimum[0],
            Assembly.Plan.EnvelopeMinimum[2],
        ),),
    )

    with pytest.raises(RoutingStageError) as Raised:
        FinalizePhysicalComponentChannelReservations(
            (Channel,),
            (Port,),
            _ResourceGraph(),
            MinimumPlacementY=0,
            EnvelopeMinimum=Assembly.Plan.EnvelopeMinimum,
            EnvelopeMaximum=Assembly.Plan.EnvelopeMaximum,
        )
    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Raised.value.Failure.AffectedNets == (Port.Signal,)
    assert "does not intersect" in Raised.value.Failure.Detail
    assert Raised.value.Failure.Diagnostics[
        "PortReservationFingerprint"
    ] == Port.ReservationFingerprint


def test_physical_channel_finalization_claims_port_and_preserves_guide():
    Assembly = _Assembly(_Problem())
    Port = Assembly.Plan.Ports[0]
    Source = next(
        Value
        for Value in Assembly.Plan.Corridors
        if Value.Signal == Port.Signal
    )
    GuideCells = tuple(
        (X, Port.GlobalPath[-1][2])
        for X in range(
            Port.GlobalPath[0][0],
            Port.GlobalPath[-1][0] + 3,
        )
    )
    Channel = replace(Source, GuideCells=GuideCells)

    (Finalized,) = FinalizePhysicalComponentChannelReservations(
        (Channel,),
        (Port,),
        _ResourceGraph(),
        MinimumPlacementY=0,
        EnvelopeMinimum=Assembly.Plan.EnvelopeMinimum,
        EnvelopeMaximum=Assembly.Plan.EnvelopeMaximum,
    )

    assert frozenset(Port.GlobalPath) <= Finalized.Claims.WireCells
    assert (
        GuideCells[-1][0],
        Port.GlobalPath[-1][1],
        GuideCells[-1][1],
    ) not in Finalized.Claims.WireCells
    assert Finalized.GuideCells == GuideCells
    assert Finalized.ResourceIds


def test_physical_channel_finalization_excludes_ordinary_keepout_nodes():
    Channel = PhysicalComponentChannelReservation(
        Signal="GlobalOnly",
        Layer=0,
        GuideCells=((-2, 0), (-1, 0), (0, 0)),
        ResourceIds=(),
        Claims=RoutingResourceClaims(),
        Capacity=1,
    )

    (Finalized,) = FinalizePhysicalComponentChannelReservations(
        (Channel,),
        (),
        _ResourceGraph(),
        MinimumPlacementY=0,
        EnvelopeMinimum=(0, 0, -1),
        EnvelopeMaximum=(2, 8, 1),
    )

    assert Finalized.Claims.WireCells == frozenset((
        (-2, 1, 0),
        (-1, 1, 0),
    ))


def test_physical_channel_finalization_fingerprints_final_detoured_contract():
    Common = dict(
        Signal="GlobalOnly",
        Layer=0,
        ResourceIds=(),
        Claims=RoutingResourceClaims(),
        Capacity=1,
    )
    WithInteriorGuide = PhysicalComponentChannelReservation(
        **Common,
        GuideCells=((-2, 0), (-1, 0), (0, 0)),
    )
    ExteriorOnlyGuide = PhysicalComponentChannelReservation(
        **Common,
        GuideCells=((-2, 0), (-1, 0)),
    )

    (FromDetour,) = FinalizePhysicalComponentChannelReservations(
        (WithInteriorGuide,),
        (),
        _ResourceGraph(),
        MinimumPlacementY=0,
        EnvelopeMinimum=(0, 0, -1),
        EnvelopeMaximum=(2, 8, 1),
    )
    (AlreadyExterior,) = FinalizePhysicalComponentChannelReservations(
        (ExteriorOnlyGuide,),
        (),
        _ResourceGraph(),
        MinimumPlacementY=0,
        EnvelopeMinimum=(0, 0, -1),
        EnvelopeMaximum=(2, 8, 1),
    )

    assert FromDetour.GuideCells == AlreadyExterior.GuideCells
    assert FromDetour.Claims == AlreadyExterior.Claims
    assert FromDetour.ResourceIds == AlreadyExterior.ResourceIds
    assert (
        FromDetour.ReservationFingerprint
        == AlreadyExterior.ReservationFingerprint
    )


def test_exact_physical_channel_preserves_candidate_path_and_claims():
    ReservedPathNodes = ((-3, 1, 0), (-2, 2, 0), (-1, 2, 0))
    Claims = _Claims(ReservedPathNodes)
    Channel = PhysicalComponentChannelReservation(
        Signal="GlobalOnly",
        Layer=0,
        # Deliberately unrelated metadata: exact candidate ownership must
        # never be reconstructed from the coarse guide.
        GuideCells=((100, 100),),
        ResourceIds=tuple(map(str, sorted(
            Claims.ResourceIds,
            key=str,
        ))),
        Claims=Claims,
        Capacity=1,
        ReservedPathNodes=ReservedPathNodes,
        RouteCandidateId="candidate-7",
        RouteCandidateFingerprint="candidate-fingerprint-7",
    )

    (Finalized,) = FinalizePhysicalComponentChannelReservations(
        (Channel,),
        (),
        _ResourceGraph(),
        MinimumPlacementY=0,
        EnvelopeMinimum=(0, 0, -1),
        EnvelopeMaximum=(2, 8, 1),
    )

    assert Finalized is Channel
    assert Finalized.GuideCells == ((100, 100),)
    assert Finalized.ReservedPathNodes == ReservedPathNodes
    assert Finalized.Claims is Claims
    assert Finalized.ToDictionary()["RouteCandidateId"] == "candidate-7"


def test_exact_physical_channel_rejects_claims_not_owned_by_path():
    ReservedPathNodes = ((-2, 1, 0), (-1, 1, 0))
    Channel = PhysicalComponentChannelReservation(
        Signal="GlobalOnly",
        Layer=0,
        GuideCells=(),
        ResourceIds=(),
        Claims=_Claims(((-2, 1, 0),)),
        ReservedPathNodes=ReservedPathNodes,
        RouteCandidateId="candidate-8",
        RouteCandidateFingerprint="candidate-fingerprint-8",
    )

    with pytest.raises(RoutingStageError) as Raised:
        FinalizePhysicalComponentChannelReservations(
            (Channel,),
            (),
            _ResourceGraph(),
            MinimumPlacementY=0,
            EnvelopeMinimum=(0, 0, -1),
            EnvelopeMaximum=(2, 8, 1),
        )

    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentAssemblyIdentityMismatch
    )
    assert "claims do not match" in Raised.value.Failure.Detail


def test_exact_physical_channel_rejects_stale_resource_id_projection():
    ReservedPathNodes = ((-2, 1, 0), (-1, 1, 0))
    Claims = _Claims(ReservedPathNodes)
    Channel = PhysicalComponentChannelReservation(
        Signal="GlobalOnly",
        Layer=0,
        GuideCells=(),
        ResourceIds=("stale-resource",),
        Claims=Claims,
        ReservedPathNodes=ReservedPathNodes,
        RouteCandidateId="candidate-stale-resources",
        RouteCandidateFingerprint="candidate-stale-resource-fingerprint",
    )

    with pytest.raises(RoutingStageError) as Raised:
        FinalizePhysicalComponentChannelReservations(
            (Channel,),
            (),
            _ResourceGraph(),
            MinimumPlacementY=0,
            EnvelopeMinimum=(0, 0, -1),
            EnvelopeMaximum=(2, 8, 1),
        )

    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentAssemblyIdentityMismatch
    )
    assert "resource identities do not match" in (
        Raised.value.Failure.Detail
    )


def test_exact_physical_channel_rejects_disconnected_reserved_path():
    ReservedPathNodes = ((-3, 1, 0), (-1, 1, 0))
    Claims = _Claims(ReservedPathNodes)
    Channel = PhysicalComponentChannelReservation(
        Signal="GlobalOnly",
        Layer=0,
        GuideCells=(),
        ResourceIds=tuple(map(str, sorted(
            Claims.ResourceIds,
            key=str,
        ))),
        Claims=Claims,
        ReservedPathNodes=ReservedPathNodes,
        RouteCandidateId="candidate-9",
        RouteCandidateFingerprint="candidate-fingerprint-9",
    )

    with pytest.raises(RoutingStageError) as Raised:
        FinalizePhysicalComponentChannelReservations(
            (Channel,),
            (),
            _ResourceGraph(),
            MinimumPlacementY=0,
            EnvelopeMinimum=(0, 0, -1),
            EnvelopeMaximum=(2, 8, 1),
        )

    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert "disconnected" in Raised.value.Failure.Detail


def test_exact_physical_channel_keepout_is_layer_exact():
    ReservedPathNodes = ((-2, 3, 0), (-1, 3, 0))
    Claims = _Claims(ReservedPathNodes)
    Channel = PhysicalComponentChannelReservation(
        Signal="GlobalOnly",
        Layer=1,
        GuideCells=(),
        ResourceIds=tuple(map(str, sorted(
            Claims.ResourceIds,
            key=str,
        ))),
        Claims=Claims,
        ReservedPathNodes=ReservedPathNodes,
        RouteCandidateId="candidate-layer-exact",
        RouteCandidateFingerprint="candidate-layer-exact-fingerprint",
    )

    (Finalized,) = FinalizePhysicalComponentChannelReservations(
        (Channel,),
        (),
        _ResourceGraph(),
        MinimumPlacementY=0,
        EnvelopeMinimum=(0, 0, -1),
        EnvelopeMaximum=(2, 8, 1),
        GlobalKeepoutNodes=frozenset(((-1, 1, 0),)),
    )

    assert Finalized is Channel

    with pytest.raises(RoutingStageError) as Raised:
        FinalizePhysicalComponentChannelReservations(
            (Channel,),
            (),
            _ResourceGraph(),
            MinimumPlacementY=0,
            EnvelopeMinimum=(0, 0, -1),
            EnvelopeMaximum=(2, 8, 1),
            GlobalKeepoutNodes=frozenset(((-1, 3, 0),)),
        )
    assert "outside its declared passage" in Raised.value.Failure.Detail


def test_physical_channel_finalization_rejects_joint_capacity_conflict():
    First = PhysicalComponentChannelReservation(
        Signal="First",
        Layer=0,
        GuideCells=((-2, 0), (-1, 0)),
        ResourceIds=(),
        Claims=RoutingResourceClaims(),
        Capacity=1,
    )
    Second = replace(First, Signal="Second")

    with pytest.raises(RoutingStageError) as Raised:
        FinalizePhysicalComponentChannelReservations(
            (First, Second),
            (),
            _ResourceGraph(),
            MinimumPlacementY=0,
            EnvelopeMinimum=(0, 0, -1),
            EnvelopeMaximum=(2, 8, 1),
        )

    assert Raised.value.Failure.Reason == (
        RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
    )
    assert Raised.value.Failure.AffectedNets == ("First", "Second")
    assert Raised.value.Failure.Diagnostics["ConflictPairs"] == [
        ["First", "Second"]
    ]


def test_physical_assembly_keeps_noncomponent_guides_as_unowned_corridors():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Placed = _Placed(Problem)
    Guide = _Guide(Problem)
    Guide = replace(
        Guide,
        SignalOrder=(*Guide.SignalOrder, "GlobalOnly"),
        Guides={
            **Guide.Guides,
            "GlobalOnly": frozenset(((100, 0), (101, 0))),
        },
        Layers={**Guide.Layers, "GlobalOnly": 0},
    )

    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    GlobalCorridor = next(
        Value
        for Value in Assembly.Plan.Corridors
        if Value.Signal == "GlobalOnly"
    )

    assert GlobalCorridor.GuideCells
    assert not Assembly.Plan.Channels
    assert not Assembly.Problem.ReservedGlobalClaimsBySignal
    Assembly = _BindAssemblyForLocalCompilation(Assembly)
    CompileResult = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )
    assert CompileResult.Feasible


def test_exact_global_binding_preserves_nonconflicting_ordinary_corridor():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Placed = _Placed(Problem)
    Guide = _Guide(Problem)
    Guide = replace(
        Guide,
        SignalOrder=(*Guide.SignalOrder, "GlobalOnly"),
        Guides={
            **Guide.Guides,
            "GlobalOnly": frozenset(((100, 0), (101, 0))),
        },
        Layers={**Guide.Layers, "GlobalOnly": 0},
    )
    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    Port = Assembly.Plan.Ports[0]
    Nodes = frozenset(Port.GlobalPath)
    Claims = Problem.ResourceGraph.BuildRouteClaims(Nodes)
    Routed = SimpleNamespace(RoutingAssignment=SimpleNamespace(
        SelectedCandidates={
            Port.Signal: SimpleNamespace(
                CandidateId="port-candidate",
                Layer=0,
                Guide=frozenset(((20, 0),)),
                Nodes=Nodes,
                Claims=Claims,
                SourcePortalId="source",
                TargetPortalIds={},
                RepeaterWaypoints=(),
            )
        },
    ))

    Bound = BindPhysicalComponentAssemblyGlobalChannels(
        Assembly,
        Routed,
        Problem.ResourceGraph,
    )

    OriginalGlobal = next(
        Channel
        for Channel in Assembly.Plan.Corridors
        if Channel.Signal == "GlobalOnly"
    )
    BoundGlobal = next(
        Channel
        for Channel in Bound.Plan.Corridors
        if Channel.Signal == "GlobalOnly"
    )
    assert BoundGlobal == OriginalGlobal
    assert not BoundGlobal.RouteCandidateId
    assert "GlobalOnly" not in dict(
        Bound.Problem.ReservedGlobalClaimsBySignal
    )
    assert set(dict(Bound.Problem.ReservedGlobalClaimsBySignal)) == {
        Port.Signal,
    }


def test_exact_global_binding_rejects_unplanned_signal():
    Assembly = _Assembly(_Problem())
    PortSignal = Assembly.Plan.Ports[0].Signal
    Routed = SimpleNamespace(RoutingAssignment=SimpleNamespace(
        SelectedCandidates={
            PortSignal: SimpleNamespace(),
            "Unexpected": SimpleNamespace(),
        },
    ))

    with pytest.raises(ValueError, match="unexpected=.*Unexpected"):
        BindPhysicalComponentAssemblyGlobalChannels(
            Assembly,
            Routed,
            Assembly.Problem.ResourceGraph,
        )


def test_local_support_binding_requires_authoritative_global_channels():
    Assembly = _Assembly(_Problem())

    with pytest.raises(
        ValueError,
        match="before authoritative global channels are frozen",
    ):
        BindPhysicalComponentAssemblyLocalPortSupports(Assembly)


def test_local_support_binding_is_post_global_and_identity_exact():
    Assembly = _Assembly(_Problem())
    OriginalFingerprint = Assembly.Plan.PlanFingerprint
    Bound = _BindAssemblyForLocalCompilation(Assembly)

    assert Bound.Plan.PlanFingerprint != OriginalFingerprint
    assert len(Bound.Plan.Channels) == len(
        Bound.Plan.GlobalBoundaryPorts
    )
    assert len(Bound.Plan.SelectedLocalPortSupports) == len(
        Bound.Plan.GlobalBoundaryPorts
    )
    for Support, Boundary, Port in zip(
        Bound.Plan.SelectedLocalPortSupports,
        Bound.Plan.GlobalBoundaryPorts,
        Bound.Plan.Ports,
    ):
        assert Support.Signal == Boundary.Signal == Port.Signal
        assert (
            Support.BoundaryReservationFingerprint
            == Boundary.ReservationFingerprint
        )
        assert Support.LocalContractFingerprint == (
            BuildPhysicalPortLocalContractFingerprint(Port)
        )


def test_exact_global_binding_fingerprints_layer_and_guide_geometry():
    Assembly = _Assembly(_Problem())
    Port = Assembly.Plan.Ports[0]
    Nodes = frozenset(Port.GlobalPath)
    Claims = Assembly.Problem.ResourceGraph.BuildRouteClaims(Nodes)

    def Bind(*, Layer, Guide):
        Candidate = SimpleNamespace(
            CandidateId="candidate-same-logical-id",
            Layer=Layer,
            Guide=frozenset(Guide),
            Nodes=Nodes,
            Claims=Claims,
            SourcePortalId="source",
            TargetPortalIds={},
            RepeaterWaypoints=(),
        )
        return BindPhysicalComponentAssemblyGlobalChannels(
            Assembly,
            SimpleNamespace(RoutingAssignment=SimpleNamespace(
                SelectedCandidates={Port.Signal: Candidate},
            )),
            Assembly.Problem.ResourceGraph,
        ).Plan.Channels[0]

    Original = Bind(Layer=0, Guide=((20, 0),))
    ChangedGuide = Bind(Layer=0, Guide=((21, 0),))
    ChangedLayer = Bind(Layer=1, Guide=((20, 0),))

    assert Original.RouteCandidateFingerprint != (
        ChangedGuide.RouteCandidateFingerprint
    )
    assert Original.RouteCandidateFingerprint != (
        ChangedLayer.RouteCandidateFingerprint
    )
    assert Original.ReservationFingerprint != (
        ChangedGuide.ReservationFingerprint
    )
    assert Original.ReservationFingerprint != (
        ChangedLayer.ReservationFingerprint
    )


def test_complete_assembly_defers_overlapping_guides_to_global_assignment():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Guide = _Guide(Problem)
    SharedForeignGuide = frozenset(((100, 0), (101, 0)))
    Guide = replace(
        Guide,
        SignalOrder=(
            *Guide.SignalOrder,
            "ForeignA",
            "ForeignB",
        ),
        Guides={
            **Guide.Guides,
            "ForeignA": SharedForeignGuide,
            "ForeignB": SharedForeignGuide,
        },
        Layers={
            **Guide.Layers,
            "ForeignA": 0,
            "ForeignB": 0,
        },
    )

    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    assert Assembly.Plan.Complete
    Corridors = {
        Value.Signal: Value for Value in Assembly.Plan.Corridors
    }
    assert Corridors["ForeignA"].GuideCells == tuple(
        sorted(SharedForeignGuide)
    )
    assert Corridors["ForeignB"].GuideCells == tuple(
        sorted(SharedForeignGuide)
    )
    assert not Corridors["ForeignA"].ReservedPathNodes
    assert not Corridors["ForeignB"].ReservedPathNodes


def test_physical_replan_preserves_access_certificate_identity(monkeypatch):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Certificate = _AccessCertificate(Problem, Placed, Resources)
    ExpectedAssembly = _Assembly(Problem, Resources)
    Guide = _Guide(Problem)
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=Certificate,
    )
    Resources.PreparedPhysicalComponentUnboundProblem = Problem
    Resources.FrozenPhysicalComponentGlobalGuidePlan = Guide
    Resources.PreparedComponentAccessCertificate = Certificate
    Resources.PreparedPhysicalComponentPortFactorDomain = Preparation
    assert Preparation.AccessCertificate is Certificate

    def Solve(
        Value,
        _ResourcesValue,
        *,
        WorkCheck=None,
        DeferLocalCompositeSelection=False,
        RequiredBoundaryPorts=None,
    ):
        assert Value is Preparation
        assert Value.AccessCertificate is Certificate
        assert DeferLocalCompositeSelection
        assert RequiredBoundaryPorts is None
        return ExpectedAssembly

    monkeypatch.setattr(
        "Compiler.Routing.AuthoritativePlanner."
        "SolvePreparedPhysicalComponentPortFactorDomain",
        Solve,
    )

    Result = ReplanPhysicalComponentAssembly(
        SimpleNamespace(Placed=Placed),
        Resources=Resources,
        Deadline=RoutingDeadline.Start(1.0),
    )

    assert Result is ExpectedAssembly
    assert Resources.PreparedPhysicalComponentPortFactorDomain is Preparation
    assert (
        Resources.FrozenPhysicalComponentAssemblyPlan
        is ExpectedAssembly.Plan
    )


def test_physical_plan_binds_exact_port_before_local_compilation():
    Assembly = _Assembly(_Problem())

    assert Assembly.Plan.Complete
    assert Assembly.Problem.Interface is not None
    assert (
        Assembly.Problem.Interface.PhysicalAssemblyPlanFingerprint
        == Assembly.Plan.PlanFingerprint
    )
    assert len(Assembly.Plan.Ports) == 1
    assert Assembly.Plan.Ports[0].Attachment == (
        Assembly.Plan.Ports[0].LocalPath[-1]
    )
    assert Assembly.Plan.AccessCertificateFingerprint
    assert Assembly.Plan.StageOrder == (
        "PhysicalBoundaryPlanning",
        "AuthoritativeGlobalReserve",
        "LocalSupportBinding",
        "ClosedComponentCompilation",
        "AuthoritativeDetailedRouting",
    )
    assert all(
        len(Domain.Candidates) == 1
        for Domain in Assembly.Problem.OwnedTerminalDomains
    )
    assert (
        Assembly.Plan.ToDictionary()[
            "ImplicitForeignTransitDomainCount"
        ]
        == 0
    )
    Assembly = _BindAssemblyForLocalCompilation(Assembly)
    Result = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )
    assert Result.Feasible and Result.Template is not None
    assert Result.Template.ExportedPorts == ((
        "Alpha",
        Assembly.Plan.Ports[0].Attachment,
    ),)


def test_component_access_certificate_is_rename_and_translation_stable():
    Certificates = []
    for Signal, Delta in (
        ("Alpha", (0, 0, 0)),
        ("Renamed", (0, 0, 0)),
        ("Alpha", (30, 4, 12)),
    ):
        Problem = _Problem(Signal, Delta)
        Placed = _Placed(Problem)
        Resources = RoutingResources(
            StaticGeometry=SimpleNamespace(),
            ResourceGraph=Problem.ResourceGraph,
        )
        Certificates.append(
            _AccessCertificate(Problem, Placed, Resources)
        )
    Base, Renamed, Translated = Certificates

    assert Base.StructuralFingerprint == Renamed.StructuralFingerprint
    assert Base.StructuralFingerprint == Translated.StructuralFingerprint
    assert Base.CertificateFingerprint == Renamed.CertificateFingerprint
    assert Base.CertificateFingerprint != Translated.CertificateFingerprint


def test_component_access_certificate_rejects_identity_mismatch():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Certificate = _AccessCertificate(Problem, Placed, Resources)

    with pytest.raises(ValueError, match="identity mismatch"):
        ValidateComponentAccessCertificateIdentity(
            Certificate,
            replace(Problem, PlacementFingerprint="changed-placement"),
            Resources.ResourceGraph,
            ComponentGraphFingerprint=(
                Placed.ComponentGraph.StructuralFingerprint
            ),
        )


def test_component_access_certificate_proves_empty_seam_domain():
    class NoEgressResourceGraph(_ResourceGraph):
        def BuildPrimitive(self, _First, _Second):
            return None

    Problem = replace(
        _Problem(),
        ResourceGraph=NoEgressResourceGraph(),
    )
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )

    Certificate = _AccessCertificate(Problem, Placed, Resources)

    assert Certificate.Complete
    assert not Certificate.Feasible
    assert Certificate.ProofKind == "perimeter-seam-empty"
    assert Certificate.AffectedSignals == ("Alpha",)


def test_physical_plan_leaves_owned_access_to_closed_compiler():
    Problem = _Problem()
    SourceDomain, TargetDomain = Problem.OwnedTerminalDomains
    AlternateAttachment = Problem.Fabric.Nodes[1]
    Alternate = ComponentTerminalAccessCandidate(
        CandidateFingerprint="alternate-source-access",
        Attachment=AlternateAttachment,
        Path=(SourceDomain.Terminal, AlternateAttachment),
        Claims=_Claims((
            SourceDomain.Terminal,
            AlternateAttachment,
        )),
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                SourceDomain,
                Candidates=(
                    *SourceDomain.Candidates,
                    Alternate,
                ),
            ),
            TargetDomain,
        ),
    )

    Assembly = _Assembly(Problem)
    Port = Assembly.Plan.Ports[0]
    assert Port.OwnedCandidateFingerprints == ()
    assert Port.OwnedAccessCandidates == ()
    assert Assembly.Problem.OwnedTerminalDomains == (
        Problem.OwnedTerminalDomains
    )
    assert len(Assembly.Problem.OwnedTerminalDomains[0].Candidates) == 2


def test_physical_plan_defers_local_realizability_to_closed_compiler():
    Problem = _Problem()
    SourceDomain, TargetDomain = Problem.OwnedTerminalDomains
    Rejected = replace(
        SourceDomain.Candidates[0],
        CandidateFingerprint="00-local-unrealizable",
    )
    Accepted = replace(
        SourceDomain.Candidates[0],
        CandidateFingerprint="10-local-realizable",
        Attachment=Problem.Fabric.Nodes[1],
        Path=(
            SourceDomain.Terminal,
            Problem.Fabric.Nodes[1],
        ),
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                SourceDomain,
                Candidates=(Rejected, Accepted),
            ),
            TargetDomain,
        ),
    )

    Assembly = _Assembly(Problem)

    assert Assembly.Plan.Ports[0].OwnedCandidateFingerprints == ()
    assert tuple(
        Candidate.CandidateFingerprint
        for Candidate in Assembly.Problem.OwnedTerminalDomains[0].Candidates
    ) == ("00-local-unrealizable", "10-local-realizable")


def test_distinct_ports_may_share_one_fabric_component():
    Signals = ("Alpha", "Beta")
    Cells = tuple((X, 7, 0) for X in range(31))
    Fabric = BuildComponentRoutingFabric(SimpleNamespace(
        PhysicalModel="test-shared-tree",
        ComponentId=3,
        ChannelFingerprint="shared-tree",
        Lanes=(SimpleNamespace(
            Cells=Cells,
            IngressNodes=(Cells[0], Cells[-1]),
        ),),
    ))

    def Candidate(Signal, Terminal):
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=f"{Signal}:{Terminal}",
            Attachment=Terminal,
            Path=(Terminal,),
            Claims=_Claims((Terminal,)),
        )

    TerminalPairs = {
        "Alpha": (Cells[0], Cells[3]),
        "Beta": (Cells[-4], Cells[-1]),
    }
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="shared-fabric-interface",
        ComponentId=3,
        OwnedSignals=Signals,
        Ports=tuple(
            ComponentInterfacePort(
                Signal=Signal,
                Direction="output",
                OwnedTerminals=TerminalPairs[Signal],
                ExternalTerminalCount=1,
            )
            for Signal in Signals
        ),
    )
    Problem = ComponentRoutingProblem(
        ProblemFingerprint="shared-fabric-problem",
        PlacementFingerprint="shared-fabric-placement",
        LocalTemplateFingerprint="shared-fabric-local",
        SelectedClusters=(0,),
        ComponentSignals=Signals,
        LocalClaims=(),
        Fabric=Fabric,
        OwnedTerminalDomains=tuple(
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Terminal,
                TerminalRole=(
                    "source" if Terminal == Terminals[0] else "target"
                ),
                TerminalFingerprint=f"{Signal}:{Terminal}:terminal",
                Candidates=(Candidate(Signal, Terminal),),
            )
            for Signal, Terminals in TerminalPairs.items()
            for Terminal in Terminals
        ),
        ExternalContinuationTerminals=(
            ("Alpha", (-12, 7, 0), "target"),
            ("Beta", (42, 7, 0), "target"),
        ),
        ForeignEscapeDomains=(),
        MaximumPowerDistance=15,
        DomainComplete=True,
        ResourceGraph=_ResourceGraph(),
        Interface=Interface,
    )
    Guide = ChannelPlan(
        Profiles={},
        SignalOrder=Signals,
        TrunkSignals=frozenset(),
        Guides={
            "Alpha": frozenset(((-3, 0), (-2, 0))),
            "Beta": frozenset(((32, 0), (33, 0))),
        },
        CorridorUsage={},
        CorridorCosts={},
        CorridorCapacity=1,
        Layers={"Alpha": 0, "Beta": 0},
        ResourceUsage={},
        ResourceOverflow={},
        ResourceClaimsBySignal={},
        SourceAccessTransitions={},
        TargetAccessTransitions={},
    )
    Placed = SimpleNamespace(
        ComponentGraph=SimpleNamespace(
            StructuralFingerprint="shared-fabric-graph",
            Channels=tuple(
                SimpleNamespace(
                    Signal=Signal,
                    FeedthroughComponentIds=(),
                )
                for Signal in Signals
            ),
        ),
    )
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )

    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    assert len(Assembly.Plan.Ports) == 2
    assert len({
        Port.FabricDomainFingerprint
        for Port in Assembly.Plan.Ports
    }) == 1
    Assembly = _BindAssemblyForLocalCompilation(Assembly)
    Result = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )
    assert Result.Feasible or (
        Result.Status == "architectural-unsatisfiable"
        and Result.Diagnostics["LocalUnsatCoreComplete"]
        and Result.Diagnostics["ImplicitForeignTransitDomainCount"] == 0
    )


def test_seam_domain_uses_actual_egress_reach_not_fixed_perimeter_band():
    Signal = "Alpha"
    Cells = tuple((X, 7, 0) for X in range(15))
    Fabric = BuildComponentRoutingFabric(SimpleNamespace(
        PhysicalModel="deep-egress-test",
        ComponentId=3,
        ChannelFingerprint="deep-egress",
        Lanes=(SimpleNamespace(
            Cells=Cells,
            IngressNodes=(Cells[0], Cells[-1]),
        ),),
    ))
    Terminals = (Cells[7], Cells[8])

    def Candidate(Terminal):
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=f"access:{Terminal}",
            Attachment=Terminal,
            Path=(Terminal,),
            Claims=_Claims((Terminal,)),
        )

    Problem = replace(
        _Problem(),
        Fabric=Fabric,
        OwnedTerminalDomains=tuple(
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Terminal,
                TerminalRole=(
                    "source" if Terminal == Terminals[0] else "target"
                ),
                TerminalFingerprint=f"terminal:{Terminal}",
                Candidates=(Candidate(Terminal),),
            )
            for Terminal in Terminals
        ),
        Interface=ClosedComponentInterface(
            InterfaceFingerprint="deep-egress-interface",
            ComponentId=3,
            OwnedSignals=(Signal,),
            Ports=(ComponentInterfacePort(
                Signal=Signal,
                Direction="output",
                OwnedTerminals=Terminals,
                ExternalTerminalCount=1,
            ),),
        ),
        ExternalContinuationTerminals=(
            (Signal, (-12, 7, 0), "target"),
        ),
    )
    Guide = replace(
        _Guide(Problem),
        Guides={
            Signal: frozenset(((-2, 0), (-1, 0))),
        },
    )

    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        Guide,
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )
    Port = Assembly.Plan.Ports[0]

    assert Port.FabricAttachment in Cells[4:-4]
    MinimumX = min(Position[0] for Position in Problem.Fabric.Nodes)
    MaximumX = max(Position[0] for Position in Problem.Fabric.Nodes)
    MinimumZ = min(Position[2] for Position in Problem.Fabric.Nodes)
    MaximumZ = max(Position[2] for Position in Problem.Fabric.Nodes)
    LocalEgress = Port.LocalPath[-1]
    assert not (
        MinimumX <= LocalEgress[0] <= MaximumX
        and MinimumZ <= LocalEgress[2] <= MaximumZ
    )


def test_local_witnesses_at_one_seam_reuse_global_aperture_connector():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Certificate = _AccessCertificate(Problem, Placed, Resources)
    (PortDomain,) = Certificate.PortDomains
    First = PortDomain.Candidates[0]
    AlternativeLocalPath = (
        Problem.Fabric.Nodes[1],
        *First.LocalPath,
    )
    Second = replace(
        First,
        CandidateFingerprint="shared-seam-second-local-witness",
        FabricAttachment=AlternativeLocalPath[0],
        LocalPath=AlternativeLocalPath,
        Claims=Problem.ResourceGraph.BuildRouteClaims(
            AlternativeLocalPath
        ),
    )
    Certificate = replace(
        Certificate,
        PortDomains=(replace(
            PortDomain,
            Candidates=(First, Second),
        ),),
    )

    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=Certificate,
    )

    Diagnostics = dict(Preparation.DiagnosticsBySignal)[First.Signal]
    assert Diagnostics["CertifiedLaneFactorCount"] == 2
    assert Preparation.GlobalConnectorSearchCount == 1
    assert len(dict(
        Preparation.LocalAccessFactorsBySignal
    )[First.Signal]) == 2
    assert len(dict(
        Preparation.ApertureFactorsBySignal
    )[First.Signal]) == 1
    assert len(dict(
        Preparation.LocalApertureSupportBySignal
    )[First.Signal]) == 2


def test_positive_global_aperture_template_is_revalidated_and_reused():
    Problem = _Problem()
    Placed = _Placed(Problem)
    SharedTemplates = {}
    FirstResources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
        PhysicalGlobalApertureTemplateCache=SharedTemplates,
    )
    First = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        FirstResources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            FirstResources,
        ),
    )
    assert First.GlobalConnectorPortableCacheStoreCount > 0
    assert SharedTemplates

    SecondResources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
        PhysicalGlobalApertureTemplateCache=SharedTemplates,
    )
    Second = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        SecondResources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            SecondResources,
        ),
    )

    assert Second.GlobalConnectorPortableCacheHitCount > 0
    assert Second.GlobalGuideFieldBuildCount < First.GlobalGuideFieldBuildCount
    assert Second.LaneFactorsBySignal == First.LaneFactorsBySignal


def test_joint_access_self_conflict_is_owned_by_local_compilation(
    monkeypatch,
):
    Problem = _Problem()
    ConflictNodes = frozenset(
        Domain.Terminal
        for Domain in Problem.OwnedTerminalDomains
    )

    class AccessConflictingResourceGraph(_ResourceGraph):
        def BuildRouteClaims(self, Nodes):
            Nodes = frozenset(Nodes)
            Claims = _Claims(Nodes)
            if not ConflictNodes <= Nodes:
                return Claims
            return replace(
                Claims,
                SupportCells=(
                    Claims.SupportCells
                    | frozenset((min(Nodes),))
                ),
            )

    Problem = replace(
        Problem,
        ResourceGraph=AccessConflictingResourceGraph(),
    )
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    monkeypatch.setattr(
        AuthoritativePlanner,
        "BuildComponentEgressPaths",
        lambda *_Arguments, **_Keywords: (_ for _ in ()).throw(
            AssertionError(
                "complete certified seam domain used generated fallback"
            )
        ),
    )

    Assembly = _BindAssemblyForLocalCompilation(
        _Assembly(Problem, Resources)
    )
    Result = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Diagnostics["LocalUnsatCoreComplete"]


def test_factorized_certificate_does_not_bind_local_access_variants():
    Problem = _Problem()
    SourceDomain, TargetDomain = Problem.OwnedTerminalDomains
    Alternatives = tuple(
        replace(
            SourceDomain.Candidates[0],
            CandidateFingerprint=f"source-option-{Index:02d}",
        )
        for Index in range(16)
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(SourceDomain, Candidates=Alternatives),
            TargetDomain,
        ),
    )
    Events = []

    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Assembly = BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
        WorkCheck=Events.append,
    )

    AccessEvents = tuple(
        Event
        for Event in Events
        if Event["Stage"]
        == "physical-terminal-access-assignment"
    )
    assert Assembly.Plan.Complete
    assert AccessEvents == ()
    CertifiedSourceOptions = {
        Fingerprint
        for Port in Assembly.Plan.Ports
        for Fingerprint in Port.OwnedCandidateFingerprints
        if Fingerprint.startswith("source-option-")
    }
    assert CertifiedSourceOptions == {"source-option-00"}
    assert len(
        Assembly.Problem.OwnedTerminalDomains[0].Candidates
    ) == 16
    assert len(AccessEvents) < (
        len(Alternatives)
        * len(Problem.Fabric.Nodes)
        * 2
    )
    CapacityEvent = next(
        Event
        for Event in Events
        if Event["Stage"] == "physical-port-capacity"
    )
    assert {
        "LaneFactorExpansionCount",
        "AccessFactorExpansionCount",
        "SeamFactorExpansionCount",
        "FactorDomainPropagationCount",
        "ForwardSupportCheckCount",
        "ForwardSupportWitnessHitCount",
        "LaneArcConsistencyCheckCount",
        "FactorArcClosureCount",
        "FactorArcClosureCacheHitCount",
        "LaneArcSupportIntersectionCount",
    }.issubset(CapacityEvent)


def test_lane_factor_arc_propagation_reuses_compiled_support_relations():
    Domains = {
        "Alpha": ("a1", "a2"),
        "Beta": ("b1", "b2"),
        "Gamma": ("c1",),
    }
    Support = {
        ("Alpha", "a1", "Beta"): frozenset(("b1",)),
        ("Alpha", "a2", "Beta"): frozenset(("b2",)),
        ("Beta", "b1", "Alpha"): frozenset(("a1",)),
        ("Beta", "b2", "Alpha"): frozenset(("a2",)),
        ("Beta", "b1", "Gamma"): frozenset(("c1",)),
        ("Gamma", "c1", "Beta"): frozenset(("b1",)),
        ("Alpha", "a1", "Gamma"): frozenset(("c1",)),
        ("Alpha", "a2", "Gamma"): frozenset(("c1",)),
        ("Gamma", "c1", "Alpha"): frozenset(("a1", "a2")),
    }

    Propagated, IntersectionCount = (
        PropagateLaneFactorArcConsistency(Domains, Support)
    )

    assert Propagated == {
        "Alpha": ("a1",),
        "Beta": ("b1",),
        "Gamma": ("c1",),
    }
    assert IntersectionCount > 0
    Unsatisfiable, _ = PropagateLaneFactorArcConsistency(
        {"Alpha": ("a2",), "Beta": ("b1",)},
        Support,
    )
    assert Unsatisfiable is None


def test_physical_port_selection_is_rename_and_translation_invariant():
    Original = _Assembly(_Problem("Alpha"))
    Renamed = _Assembly(_Problem("Renamed"))
    Delta = (31, 0, 13)
    Translated = _Assembly(_Problem("Alpha", Delta))

    assert (
        Original.Plan.Ports[0].ReservationFingerprint
        == Renamed.Plan.Ports[0].ReservationFingerprint
        == Translated.Plan.Ports[0].ReservationFingerprint
    )
    assert tuple(
        Translated.Plan.Ports[0].Attachment[Index] - Delta[Index]
        for Index in range(3)
    ) == Original.Plan.Ports[0].Attachment
    assert (
        Translated.Plan.InterfaceFingerprint
        == Original.Plan.InterfaceFingerprint
    )


def test_physical_plan_publishes_global_only_boundary_port_contract():
    Original = _Assembly(_Problem("Alpha"))
    Delta = (31, 0, 13)
    Translated = _Assembly(_Problem("Alpha", Delta))

    assert len(Original.Plan.GlobalBoundaryPorts) == 1
    Boundary = Original.Plan.GlobalBoundaryPorts[0]
    TranslatedBoundary = Translated.Plan.GlobalBoundaryPorts[0]
    assert Boundary.GlobalPath[0] == Boundary.Attachment
    assert not hasattr(Boundary, "LocalPath")
    assert not hasattr(Boundary, "FabricAttachment")
    assert not hasattr(Boundary, "OwnedAccessCandidates")
    assert (
        Boundary.ReservationFingerprint
        == TranslatedBoundary.ReservationFingerprint
    )
    assert Original.Plan.SelectedLocalPortSupports == ()


def _ProblemWithPhysicalPlan(Assembly, Plan):
    return replace(
        Assembly.Problem,
        PhysicalAssemblyPlan=Plan,
    )


@pytest.mark.parametrize("Mutation", ("duplicate", "wrong-signal"))
def test_boundary_handoff_requires_one_global_port_per_exported_signal(
    Mutation,
):
    Assembly = _Assembly(_Problem())
    Boundary = Assembly.Plan.GlobalBoundaryPorts[0]
    if Mutation == "duplicate":
        BoundaryPorts = (
            Boundary,
            Boundary,
        )
    else:
        BoundaryPorts = (replace(Boundary, Signal="NotExported"),)
    ChangedPlan = replace(
        Assembly.Plan,
        GlobalBoundaryPorts=BoundaryPorts,
    )

    with pytest.raises(
        ValueError,
        match="exactly one global boundary port per exported signal",
    ):
        ValidatePhysicalBoundaryPortHandoff(
            _ProblemWithPhysicalPlan(Assembly, ChangedPlan),
            ChangedPlan,
        )


def test_boundary_handoff_rejects_global_path_not_starting_at_attachment():
    Assembly = _Assembly(_Problem())
    Boundary = Assembly.Plan.GlobalBoundaryPorts[0]
    Values = dict(vars(Boundary))
    Values["GlobalPath"] = ((99, 7, 99), *Boundary.GlobalPath)
    ChangedPlan = replace(
        Assembly.Plan,
        GlobalBoundaryPorts=(SimpleNamespace(**Values),),
    )

    with pytest.raises(
        ValueError,
        match="global boundary path must start at its attachment",
    ):
        ValidatePhysicalBoundaryPortHandoff(
            _ProblemWithPhysicalPlan(Assembly, ChangedPlan),
            ChangedPlan,
        )


@pytest.mark.parametrize(
    "Mutation",
    (
        {"GlobalClaims": _Claims(((99, 7, 99),))},
        {"GlobalContractFingerprint": "changed-global-contract"},
        {"ApertureContractFingerprint": "changed-aperture-contract"},
    ),
)
def test_boundary_handoff_rejects_changed_external_contract(Mutation):
    Assembly = _Assembly(_Problem())
    Boundary = Assembly.Plan.GlobalBoundaryPorts[0]
    ChangedPlan = replace(
        Assembly.Plan,
        GlobalBoundaryPorts=(replace(Boundary, **Mutation),),
    )

    with pytest.raises(
        ValueError,
        match="composite port external half",
    ):
        ValidatePhysicalBoundaryPortHandoff(
            _ProblemWithPhysicalPlan(Assembly, ChangedPlan),
            ChangedPlan,
        )


def test_boundary_handoff_rejects_component_local_fields():
    Assembly = _Assembly(_Problem())
    Boundary = Assembly.Plan.GlobalBoundaryPorts[0]
    Values = dict(vars(Boundary))
    Values["LocalPath"] = Assembly.Plan.Ports[0].LocalPath
    ChangedPlan = replace(
        Assembly.Plan,
        GlobalBoundaryPorts=(SimpleNamespace(**Values),),
    )

    with pytest.raises(ValueError, match="component-local fields"):
        ValidatePhysicalBoundaryPortHandoff(
            _ProblemWithPhysicalPlan(Assembly, ChangedPlan),
            ChangedPlan,
        )


def test_boundary_handoff_rejects_unbound_local_support_identity():
    Assembly = _Assembly(_Problem())
    ChangedPlan = replace(
        Assembly.Plan,
        SelectedLocalPortSupports=(SimpleNamespace(),),
    )

    with pytest.raises(
        ValueError,
        match="selected local port support has an incomplete identity",
    ):
        CompileClosedComponent(
            _ProblemWithPhysicalPlan(Assembly, ChangedPlan),
            AssemblyPlan=ChangedPlan,
            DiscoveryVariantLimit=None,
        )


def test_component_compile_rejects_changed_physical_identity():
    Assembly = _Assembly(_Problem())
    Changed = replace(
        Assembly.Plan,
        PlanFingerprint="changed-plan",
    )

    with pytest.raises(
        ValueError,
        match="physical assembly identities differ",
    ):
        CompileClosedComponent(
            Assembly.Problem,
            AssemblyPlan=Changed,
            DiscoveryVariantLimit=None,
        )


def test_component_compile_rejects_seam_inside_keepout_envelope():
    Assembly = _Assembly(_Problem())
    OriginalPort = Assembly.Plan.Ports[0]
    InteriorAttachment = OriginalPort.FabricAttachment
    ChangedPort = replace(
        OriginalPort,
        Attachment=InteriorAttachment,
        LocalPath=(InteriorAttachment,),
        GlobalPath=(
            InteriorAttachment,
            (
                InteriorAttachment[0] + 1,
                InteriorAttachment[1],
                InteriorAttachment[2],
            ),
        ),
    )
    ChangedPlan = replace(
        Assembly.Plan,
        Ports=(ChangedPort,),
    )
    ChangedProblem = replace(
        Assembly.Problem,
        PhysicalAssemblyPlan=ChangedPlan,
        Interface=replace(
            Assembly.Problem.Interface,
            PhysicalPortReservations=(ChangedPort,),
        ),
    )

    with pytest.raises(
        ValueError,
        match="seam ownership is malformed",
    ):
        CompileClosedComponent(
            ChangedProblem,
            AssemblyPlan=ChangedPlan,
            DiscoveryVariantLimit=None,
        )


def test_component_handoff_identity_error_is_typed(monkeypatch):
    Assembly = _BindAssemblyForLocalCompilation(_Assembly(_Problem()))
    Result = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )
    assert Result.Template is not None
    Channel = SimpleNamespace(
        PhysicalModel="test-tree",
        ChannelFingerprint="archived-channel",
        Lanes=(SimpleNamespace(
            Cells=((0, 7, 0), (1, 7, 0), (2, 7, 0)),
            IngressNodes=((0, 7, 0), (2, 7, 0)),
        ),),
    )
    Placed = PlacedDesign(
        Module=SimpleNamespace(),
        PlacedGates=[],
        LocalRouteClaims=(),
        LocalRouteDiagnostics={},
        RoutedComponentTemplates=(),
        InterClusterRoutingChannel=Channel,
    )

    def RejectHandoff(*_Arguments, **_Keywords):
        raise ValueError("test fabric identity mismatch")

    monkeypatch.setattr(
        ComponentPipeline,
        "ValidateRoutedComponentHandoff",
        RejectHandoff,
    )
    with pytest.raises(RoutingStageError) as Error:
        ComponentPipeline.AssembleClosedComponentForGlobalRouting(
            Placed,
            Result.Template,
            PhysicalAssemblyPlan=Assembly.Plan,
            PlacementFingerprint=(
                Assembly.Problem.PlacementFingerprint
            ),
            LocalTemplateFingerprint=(
                Assembly.Problem.LocalTemplateFingerprint
            ),
        )

    assert Error.value.Failure.Reason == (
        RoutingFailureReason.ComponentAssemblyIdentityMismatch
    )
    assert Error.value.Failure.Stage == (
        "ComponentAssemblyIdentityValidation"
    )
    assert "test fabric identity mismatch" in (
        Error.value.Failure.Detail
    )


def test_component_compile_owns_terminal_access_domain_choice():
    Problem = _Problem()
    Domain = Problem.OwnedTerminalDomains[0]
    Reopened = replace(
        Domain.Candidates[0],
        CandidateFingerprint="reopened-access",
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                Domain,
                Candidates=(
                    Domain.Candidates[0],
                    Reopened,
                ),
            ),
            *Problem.OwnedTerminalDomains[1:],
        ),
    )
    Assembly = _BindAssemblyForLocalCompilation(_Assembly(Problem))

    Result = CompileClosedComponent(
        Assembly.Problem,
        AssemblyPlan=Assembly.Plan,
        DiscoveryVariantLimit=None,
    )

    assert Result.Feasible
    assert Result.Template is not None


def test_component_compile_rejects_local_feedback_no_goods():
    Assembly = _BindAssemblyForLocalCompilation(_Assembly(_Problem()))

    with pytest.raises(
        ValueError,
        match="cannot reopen its immutable assembly plan",
    ):
        CompileClosedComponent(
            Assembly.Problem,
            AssemblyPlan=Assembly.Plan,
            ForbiddenExportPortsBySignal={
                "Alpha": (
                    Assembly.Plan.Ports[0].Attachment,
                ),
            },
            DiscoveryVariantLimit=None,
        )


def test_completed_physical_template_cache_reuses_renamed_translation():
    ComponentPipeline._CompletedComponentTemplateCache.clear()
    Original = _BindAssemblyForLocalCompilation(
        _Assembly(_Problem("Original"))
    )
    First = CompileClosedComponent(
        Original.Problem,
        AssemblyPlan=Original.Plan,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible and First.Template is not None
    assert not First.Diagnostics["CompletedTemplateCacheHit"]

    Delta = (31, 0, 13)
    Renamed = _BindAssemblyForLocalCompilation(
        _Assembly(_Problem("Renamed", Delta))
    )
    # The assembly/interface fingerprint is an opaque exact-plan identity.
    # Structural template reuse is governed by the normalized physical port
    # contract, so an equivalent translated plan must not depend on this ID.
    RenamedPlan = replace(
        Renamed.Plan,
        InterfaceFingerprint="translated-opaque-interface",
    )
    RenamedInterface = replace(
        Renamed.Problem.Interface,
        InterfaceFingerprint="translated-opaque-interface",
    )
    Renamed = replace(
        Renamed,
        Plan=RenamedPlan,
        Problem=replace(
            Renamed.Problem,
            Interface=RenamedInterface,
            PhysicalAssemblyPlan=RenamedPlan,
        ),
    )
    Second = CompileClosedComponent(
        Renamed.Problem,
        AssemblyPlan=Renamed.Plan,
        DiscoveryVariantLimit=None,
    )

    assert Second.Feasible and Second.Template is not None
    assert Second.Diagnostics["CompletedTemplateCacheHit"]
    assert (
        Second.Diagnostics["CompletedTemplateTranslationDelta"]
        == list(Delta)
    )
    assert {Net.Signal for Net in Second.Template.Nets} == {
        "Renamed"
    }
    assert Second.Template.ExportedPorts == ((
        "Renamed",
        Renamed.Plan.Ports[0].Attachment,
    ),)


def test_rejected_physical_port_assignment_advances_without_reopening_it():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    First = _Assembly(Problem, Resources)
    Resources.RejectedPhysicalComponentPortAssignmentFingerprints.add(
        First.Plan.PortAssignmentFingerprint
    )

    Second = _Assembly(Problem, Resources)

    assert (
        Second.Plan.PortAssignmentFingerprint
        != First.Plan.PortAssignmentFingerprint
    )
    assert Second.Plan.PlanFingerprint != First.Plan.PlanFingerprint


def test_rejected_signal_port_reservation_prunes_equivalent_plans():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    First = _Assembly(Problem, Resources)
    FirstPort = First.Plan.Ports[0]
    Resources.RejectedPhysicalComponentPortReservationsBySignal.setdefault(
        FirstPort.Signal,
        set(),
    ).add(FirstPort.ReservationFingerprint)

    Second = _Assembly(Problem, Resources)

    assert (
        Second.Plan.Ports[0].ReservationFingerprint
        != FirstPort.ReservationFingerprint
    )


def test_rejected_signal_aperture_contract_prunes_equivalent_plans():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    First = _Assembly(Problem, Resources)
    FirstPort = First.Plan.Ports[0]
    FirstAperture = BuildPhysicalPortApertureContractFingerprint(FirstPort)
    Resources.RejectedPhysicalComponentPortReservationsBySignal.setdefault(
        FirstPort.Signal,
        set(),
    ).add(FirstAperture)

    Second = _Assembly(Problem, Resources)

    assert BuildPhysicalPortApertureContractFingerprint(
        Second.Plan.Ports[0]
    ) != FirstAperture


def test_current_global_blocker_keeps_exact_bound_assignment_scope():
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="assignment",
        Channels=(
            SimpleNamespace(Signal="PortA", RouteCandidateId="route-a"),
            SimpleNamespace(Signal="PortB", RouteCandidateId="route-b"),
        ),
        Ports=(
                SimpleNamespace(
                    Signal="PortA",
                    Direction="input",
                    OwnedTerminals=((0, 1, 0),),
                    OwnedAccessCandidates=(),
                    Capacity=1,
                ReservationFingerprint="reservation-a",
                FabricDomainFingerprint="fabric-a",
                FabricAttachment=(0, 1, 0),
                Attachment=(1, 1, 0),
                LocalPath=((0, 1, 0), (1, 1, 0)),
            ),
                SimpleNamespace(
                    Signal="PortB",
                    Direction="input",
                    OwnedTerminals=((0, 1, 2),),
                    OwnedAccessCandidates=(),
                    Capacity=1,
                ReservationFingerprint="reservation-b",
                FabricDomainFingerprint="fabric-b",
                FabricAttachment=(0, 1, 2),
                Attachment=(1, 1, 2),
                LocalPath=((0, 1, 2), (1, 1, 2)),
            ),
        ),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        ForbiddenPhysicalComponentGlobalCandidateSets=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
    )
    Solve = ComponentRoutingSolveResult(
        Status="architectural-unsatisfiable",
        ProofFingerprint="proof",
        Diagnostics={
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreSignals": ["PortA"],
            "LocalUnsatCoreFingerprint": "core",
        },
    )
    GlobalChannelDesign = SimpleNamespace(
        RoutingAssignment=SimpleNamespace(SelectedCandidates={
            "PortA": SimpleNamespace(CandidateId="route-a"),
            "PortB": SimpleNamespace(CandidateId="route-b"),
        }),
    )

    Diagnostics = RecordPhysicalComponentLocalCompilationNoGood(
        Solve,
        Plan,
        GlobalChannelDesign,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "exact-physical-global-candidate-set"
    )
    assert Diagnostics["GlobalRelaxedLocalProofComplete"] is False
    assert Diagnostics["GlobalRelaxedLocalProofStatus"] == "not-run"
    assert not Resources.RejectedPhysicalComponentPortReservationsBySignal
    assert not Resources.RejectedPhysicalComponentPortReservationSets
    assert Resources.ForbiddenPhysicalComponentGlobalCandidateSets == {
        frozenset((
            ("PortA", "route-a"),
            ("PortB", "route-b"),
        ))
    }
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert Resources.RejectedPhysicalComponentAssemblyPlanFingerprints == {
        "physical-plan"
    }


def test_incomplete_local_core_cannot_prune_a_port_contract():
    Solve = ComponentRoutingSolveResult(
        Status="incomplete",
        ProofFingerprint="proof",
        Diagnostics={
            "LocalUnsatCoreComplete": False,
            "LocalUnsatCoreSignals": ["PortA"],
        },
    )
    with pytest.raises(ValueError, match="complete local proof"):
        RecordPhysicalComponentLocalCompilationNoGood(
            Solve,
            SimpleNamespace(
                PortAssignmentFingerprint="assignment",
                Ports=(),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )


def test_incomplete_global_relaxed_proof_falls_back_to_exact_assignment():
    Plan = SimpleNamespace(
        PlanFingerprint="plan",
        PortAssignmentFingerprint="ports",
        Channels=(SimpleNamespace(
            Signal="PortA",
            RouteCandidateId="route-a",
        ),),
        Ports=(SimpleNamespace(
            Signal="PortA",
            ReservationFingerprint="reservation-a",
        ),),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        ForbiddenPhysicalComponentGlobalCandidateSets=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
    )
    Solve = ComponentRoutingSolveResult(
        Status="architectural-unsatisfiable",
        ProofFingerprint="bound-proof",
        Diagnostics={
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreSignals": ["PortA"],
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalProofStatus": "incomplete",
        },
    )
    GlobalDesign = SimpleNamespace(
        RoutingAssignment=SimpleNamespace(SelectedCandidates={
            "PortA": SimpleNamespace(CandidateId="route-a"),
        }),
    )

    Diagnostics = RecordPhysicalComponentLocalCompilationNoGood(
        Solve,
        Plan,
        GlobalDesign,
        Resources,
    )

    assert Diagnostics["NoGoodScope"] == (
        "exact-physical-global-candidate-set"
    )
    assert Diagnostics["GlobalRelaxedLocalProofStatus"] == "incomplete"
    assert Resources.ForbiddenPhysicalComponentGlobalCandidateSets == {
        frozenset((("PortA", "route-a"),))
    }
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints


def test_global_relaxed_proof_removes_only_reserved_global_claims(monkeypatch):
    Assembly = _Assembly(_Problem())
    Seen = []
    SeenKeywords = []

    def Solve(RelaxedProblem, **Keywords):
        Seen.append(RelaxedProblem)
        SeenKeywords.append(Keywords)
        return ComponentRoutingSolveResult(
            Status="architectural-unsatisfiable",
            ProofFingerprint="relaxed-proof",
            ExpansionCount=7,
            Diagnostics={
                "LocalUnsatCoreComplete": True,
                "LocalUnsatCoreSignals": ["Alpha"],
            },
        )

    monkeypatch.setattr(
        ComponentPipeline,
        "SolveComponentRoutingProblem",
        Solve,
    )

    PortfolioCache = {}
    NetCache = {}
    ClaimsCache = {}
    DiscoveryCache = {}
    Diagnostics = (
        ComponentPipeline.ProveGlobalRelaxedLocalUnsatisfiability(
            Assembly.Problem,
            DeadlineSeconds=1.0,
            VariantPortfolioCache=PortfolioCache,
            NetVariantConstructionCache=NetCache,
            RouteClaimsConstructionCache=ClaimsCache,
            NetVariantDiscoveryStateCache=DiscoveryCache,
        )
    )

    assert Diagnostics["GlobalRelaxedLocalProofComplete"] is True
    assert Diagnostics["GlobalRelaxedLocalCoreComplete"] is True
    assert Diagnostics["GlobalRelaxedLocalProofFingerprint"] == (
        "relaxed-proof"
    )
    assert Diagnostics["GlobalRelaxedLocalUnsatCoreSignals"] == ["Alpha"]
    assert Seen[0].ReservedGlobalClaimsBySignal == ()
    assert Seen[0].ProblemFingerprint != Assembly.Problem.ProblemFingerprint
    assert Seen[0].PhysicalAssemblyPlan == Assembly.Problem.PhysicalAssemblyPlan
    assert SeenKeywords[0]["VariantPortfolioCache"] is PortfolioCache
    assert SeenKeywords[0]["NetVariantConstructionCache"] is NetCache
    assert SeenKeywords[0]["RouteClaimsConstructionCache"] is ClaimsCache
    assert SeenKeywords[0]["NetVariantDiscoveryStateCache"] is DiscoveryCache


def test_local_only_net_portfolio_compiles_exhaustively_without_template_search():
    Problem = _Assembly(_Problem()).Problem
    PortfolioCache = {}
    DiscoveryCache = {}
    WorkEvents = []

    Compiled = CompileCompleteComponentNetVariantPortfolio(
        Problem,
        "Alpha",
        DeadlineSeconds=1.0,
        WorkCheck=WorkEvents.append,
        VariantPortfolioCache=PortfolioCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )

    assert Compiled.Complete
    assert Compiled.Status == "complete"
    assert Compiled.Variants
    assert Compiled.Diagnostics["LocalOnly"] is True
    assert Compiled.Diagnostics["TemplateSearchEntered"] is False
    assert WorkEvents
    assert DiscoveryCache == {}
    assert PortfolioCache

    Reused = CompileCompleteComponentNetVariantPortfolio(
        Problem,
        "Alpha",
        DeadlineSeconds=0.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )
    assert Reused.Complete
    assert Reused.Status == "complete-cached"
    assert Reused.ExpansionCount == 0
    assert Reused.Diagnostics["PortfolioCacheHit"] is True


def test_local_only_net_portfolio_resumes_but_does_not_cache_partial_domain():
    Problem = _Assembly(_Problem()).Problem
    PortfolioCache = {}
    DiscoveryCache = {}
    ConstructionCache = {}
    ClaimsCache = {}

    Incomplete = CompileCompleteComponentNetVariantPortfolio(
        replace(Problem, MaximumWork=0),
        "Alpha",
        DeadlineSeconds=1.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantConstructionCache=ConstructionCache,
        RouteClaimsConstructionCache=ClaimsCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )

    assert not Incomplete.Complete
    assert Incomplete.Status == "incomplete"
    assert PortfolioCache == {}
    assert DiscoveryCache

    Completed = CompileCompleteComponentNetVariantPortfolio(
        replace(Problem, MaximumWork=250_000),
        "Alpha",
        DeadlineSeconds=1.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantConstructionCache=ConstructionCache,
        RouteClaimsConstructionCache=ClaimsCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )

    assert Completed.Complete
    assert Completed.Status == "complete"
    assert PortfolioCache
    assert DiscoveryCache == {}


def _MultiPortfolioFixture():
    Problem = _Assembly(_Problem()).Problem
    BasePort = Problem.Interface.PhysicalPortReservations[0]
    SourceDomain, TargetDomain = Problem.OwnedTerminalDomains

    def Alternate(Candidate, Name):
        Middle = (1, 7, 0)
        Path = (
            (Candidate.Attachment, Middle)
            if Candidate.Attachment != Middle
            else (Middle,)
        )
        return replace(
            Candidate,
            CandidateFingerprint=Name,
            Attachment=Middle,
            Path=Path,
            Claims=_Claims(Path),
        )

    AlternateSource = Alternate(
        SourceDomain.Candidates[0],
        "Alpha:alternate-source",
    )
    AlternateTarget = Alternate(
        TargetDomain.Candidates[0],
        "Alpha:alternate-target",
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                SourceDomain,
                Candidates=(SourceDomain.Candidates[0], AlternateSource),
            ),
            replace(
                TargetDomain,
                Candidates=(TargetDomain.Candidates[0], AlternateTarget),
            ),
        ),
    )
    OriginalPort = replace(
        BasePort,
        OwnedAccessCandidates=(
            SourceDomain.Candidates[0],
            TargetDomain.Candidates[0],
        ),
        OwnedCandidateFingerprints=(
            SourceDomain.Candidates[0].CandidateFingerprint,
            TargetDomain.Candidates[0].CandidateFingerprint,
        ),
        ReservationFingerprint="original",
    )
    AlternatePort = replace(
        BasePort,
        OwnedAccessCandidates=(AlternateSource, AlternateTarget),
        OwnedCandidateFingerprints=(
            AlternateSource.CandidateFingerprint,
            AlternateTarget.CandidateFingerprint,
        ),
        ReservationFingerprint="alternate",
    )
    SupersetPort = replace(
        BasePort,
        OwnedAccessCandidates=(
            SourceDomain.Candidates[0],
            AlternateSource,
            TargetDomain.Candidates[0],
            AlternateTarget,
        ),
        OwnedCandidateFingerprints=tuple(sorted((
            SourceDomain.Candidates[0].CandidateFingerprint,
            AlternateSource.CandidateFingerprint,
            TargetDomain.Candidates[0].CandidateFingerprint,
            AlternateTarget.CandidateFingerprint,
        ))),
        ReservationFingerprint="superset",
    )
    return Problem, OriginalPort, AlternatePort, SupersetPort


def _ExactPortfolioForPort(Problem, Port):
    return CompileCompleteComponentNetVariantPortfolio(
        replace(
            Problem,
            Interface=replace(
                Problem.Interface,
                PhysicalPortReservations=(Port,),
            ),
        ),
        "Alpha",
        DeadlineSeconds=1.0,
    )


def test_multi_contract_portfolios_equal_exact_subset_and_superset_domains():
    Problem, OriginalPort, AlternatePort, SupersetPort = (
        _MultiPortfolioFixture()
    )
    Ports = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (OriginalPort, AlternatePort, SupersetPort)
    }

    Shared = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=1.0,
    )

    assert Shared.Complete
    assert Shared.Diagnostics["SolverCallCount"] == 1
    ExactByContract = {
        Contract: _ExactPortfolioForPort(Problem, Port)
        for Contract, Port in Ports.items()
    }
    ExactBuildCount = sum(
        Portfolio.Diagnostics["VariantDiagnosticsBySignal"]["Alpha"][
            "NetVariantBuildCount"
        ]
        for Portfolio in ExactByContract.values()
    )
    assert Shared.NetVariantBuildCount == 4
    assert Shared.NetVariantBuildCount < ExactBuildCount == 6
    for Contract, Port in Ports.items():
        Exact = ExactByContract[Contract]
        assert Shared.Portfolios[Contract].Complete
        assert Shared.Portfolios[Contract].Variants == Exact.Variants
    OriginalContract = BuildPhysicalPortLocalContractFingerprint(OriginalPort)
    AlternateContract = BuildPhysicalPortLocalContractFingerprint(AlternatePort)
    SupersetContract = BuildPhysicalPortLocalContractFingerprint(SupersetPort)
    assert Shared.Portfolios[OriginalContract].Diagnostics[
        "AccessCombinationCount"
    ] == 1
    assert Shared.Portfolios[AlternateContract].Diagnostics[
        "AccessCombinationCount"
    ] == 1
    assert Shared.Portfolios[SupersetContract].Diagnostics[
        "AccessCombinationCount"
    ] == 4


def test_multi_contract_portfolios_keep_exact_local_paths_separate():
    Problem, OriginalPort, _AlternatePort, _SupersetPort = (
        _MultiPortfolioFixture()
    )
    OtherPathPort = replace(
        OriginalPort,
        LocalPath=((2, 7, 0), (1, 7, 0), (0, 7, 0)),
        ReservationFingerprint="other-path",
    )
    Ports = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (OriginalPort, OtherPathPort)
    }

    Shared = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=1.0,
    )

    assert Shared.Complete
    for Contract, Port in Ports.items():
        assert Shared.Portfolios[Contract].Variants == (
            _ExactPortfolioForPort(Problem, Port).Variants
        )
        assert all(
            frozenset(Port.LocalPath) <= Variant.Nodes
            for Variant in Shared.Portfolios[Contract].Variants
        )


def test_multi_contract_empty_domain_is_complete_and_does_not_leak_union():
    Problem, OriginalPort, _AlternatePort, _SupersetPort = (
        _MultiPortfolioFixture()
    )
    EmptyPort = replace(
        OriginalPort,
        FabricDomainFingerprint="empty-domain",
        OwnedCandidateFingerprints=("missing-candidate",),
        OwnedAccessCandidates=(),
        ReservationFingerprint="empty",
    )
    Ports = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (OriginalPort, EmptyPort)
    }

    Shared = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=1.0,
    )

    EmptyContract = BuildPhysicalPortLocalContractFingerprint(EmptyPort)
    assert Shared.Complete
    assert Shared.Portfolios[EmptyContract].Complete
    assert Shared.Portfolios[EmptyContract].Variants == ()


def test_multi_contract_interruption_resumes_without_publishing_partial_cache():
    Problem, OriginalPort, AlternatePort, _SupersetPort = (
        _MultiPortfolioFixture()
    )
    Ports = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (OriginalPort, AlternatePort)
    }
    PortfolioCache = {}
    DiscoveryCache = {}

    Incomplete = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=0.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )
    assert not Incomplete.Complete
    assert PortfolioCache == {}
    assert DiscoveryCache

    Completed = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        dict(reversed(tuple(Ports.items()))),
        DeadlineSeconds=1.0,
        VariantPortfolioCache=PortfolioCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )
    Fresh = CompileCompleteComponentNetVariantPortfolios(
        Problem,
        "Alpha",
        Ports,
        DeadlineSeconds=1.0,
    )
    assert Completed.Complete
    assert Completed.DomainFingerprint == Fresh.DomainFingerprint
    assert Completed.PortfoliosByContract == Fresh.PortfoliosByContract
    assert PortfolioCache
    assert DiscoveryCache == {}


def test_net_portfolio_static_context_excludes_exact_port_contract():
    Problem = _Assembly(_Problem()).Problem
    Context = BuildCompleteComponentNetPortfolioStaticContext(
        Problem,
        "Alpha",
    )
    Port = Problem.Interface.PhysicalPortReservations[0]
    ChangedPort = replace(
        Port,
        LocalPath=(*Port.LocalPath, (9, 7, 0)),
    )
    ChangedProblem = replace(
        Problem,
        Interface=replace(
            Problem.Interface,
            PhysicalPortReservations=(ChangedPort,),
        ),
    )
    ChangedContext = BuildCompleteComponentNetPortfolioStaticContext(
        ChangedProblem,
        "Alpha",
    )

    assert (
        Context.StaticStructuralFingerprint
        == ChangedContext.StaticStructuralFingerprint
    )
    BasePortfolio = GetCachedCompleteComponentNetVariantPortfolio(
        Problem,
        "Alpha",
        {},
        StaticContext=Context,
    )
    ChangedPortfolio = GetCachedCompleteComponentNetVariantPortfolio(
        ChangedProblem,
        "Alpha",
        {},
        StaticContext=Context,
    )
    assert BasePortfolio.DomainFingerprint != (
        ChangedPortfolio.DomainFingerprint
    )


def test_local_interface_factor_hashes_full_proof_domain_once_per_portfolio(
    monkeypatch,
):
    Assembly = _Assembly(_Problem())
    FullDomainCalls = []

    def FullDomain(_ProblemValue):
        FullDomainCalls.append(True)
        return "full-local-domain"

    def Option(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            OwnedTerminals=((X, 7, 0),),
            OwnedTerminalFingerprints=(f"terminal-{X}",),
            OwnedCandidateFingerprints=(f"candidate-{X}",),
            FabricAttachment=(X, 7, 0),
            Attachment=(X, 7, 0),
            LocalPath=((X, 7, 0),),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 0), Option("Current", 1))
    CompleteOptions = (Option("Complete", 2), Option("Complete", 3))

    def CartesianPortfolio(
        _Plan,
        _CurrentSignal,
        _CompleteSignal,
        _Resources,
        *,
        BuildProofDomainFingerprint,
        EvaluatePair,
        **_Keywords,
    ):
        assert _Keywords["MaximumCompletedRows"] is None
        Domains = [
            BuildProofDomainFingerprint(Current, Complete)
            for Current in CurrentOptions
            for Complete in CompleteOptions
        ]
        assert len(Domains) == 4
        assert len(set(Domains)) == 4
        return {"Complete": False, "ProofDomainFingerprints": Domains}

    monkeypatch.setattr(
        ComponentPipeline,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        FullDomain,
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "CertifyDirectionalLocalContractPortfolio",
        CartesianPortfolio,
    )

    Diagnostics = ComponentPipeline.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        SimpleNamespace(),
        DeadlineSeconds=1.0,
    )

    assert Diagnostics["Complete"] is False
    assert len(FullDomainCalls) == 1


def test_local_interface_factor_reaches_monotonic_proof_fixed_point(
    monkeypatch,
):
    Assembly = _Assembly(_Problem())
    Calls = []

    def Portfolio(
        _Plan,
        _CurrentSignal,
        _CompleteSignal,
        Resources,
        **_Keywords,
    ):
        assert _Keywords["MaximumCompletedRows"] is None
        Calls.append(True)
        ProofCache = getattr(
            Resources,
            "PhysicalComponentLocalInterfaceFactorProofCache",
            None,
        )
        if ProofCache is None:
            ProofCache = {}
            Resources.PhysicalComponentLocalInterfaceFactorProofCache = (
                ProofCache
            )
        if len(Calls) == 1:
            ProofCache["new-proof"] = object()
            return {"Complete": False, "Status": "incomplete"}
        return {"Complete": True, "Status": "complete"}

    monkeypatch.setattr(
        ComponentPipeline,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _ProblemValue: "full-local-domain",
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "CertifyDirectionalLocalContractPortfolio",
        Portfolio,
    )
    Diagnostics = ComponentPipeline.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        SimpleNamespace(
            RejectedPhysicalComponentPortReservationSets=set(),
        ),
        DeadlineSeconds=1.0,
    )

    assert Diagnostics["Complete"] is True
    assert Diagnostics["CertificationPassCount"] == 2


def test_local_interface_factor_compiles_each_complete_contract_once(
    monkeypatch,
):
    Assembly = _Assembly(_Problem())
    StaticContext = object()
    StaticContextCalls = []
    CompileCalls = []
    ContractDomainBuildCalls = []
    BuildContractDomain = (
        ComponentPipeline.BuildCompleteOpposingNetAccessContractDomain
    )

    def Option(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricDomainFingerprint=f"domain-{X}",
            OwnedTerminals=((X, 7, 0),),
            OwnedTerminalFingerprints=(f"terminal-{X}",),
            OwnedCandidateFingerprints=(f"candidate-{X}",),
            OwnedAccessCandidates=(),
            FabricAttachment=(X, 7, 0),
            Attachment=(X, 7, 0),
            LocalPath=((X, 7, 0),),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 0), Option("Current", 1))
    CompleteOptions = (Option("Complete", 2), Option("Complete", 3))

    def CartesianPortfolio(
        _Plan,
        _CurrentSignal,
        _CompleteSignal,
        _Resources,
        *,
        BuildProofDomainFingerprint,
        EvaluatePair,
        **_Keywords,
    ):
        Results = [
            EvaluatePair(
                Current,
                Complete,
                BuildProofDomainFingerprint(Current, Complete),
            )
            for Complete in CompleteOptions
            for Current in CurrentOptions
        ]
        return {"Complete": False, "Results": Results}

    def BuildStaticContext(_ProblemValue, Signal):
        StaticContextCalls.append(Signal)
        return StaticContext

    def Compile(_ProblemValue, Signal, PortsByContract, **Keywords):
        CompileCalls.append((Signal, Keywords.get("StaticContext")))
        return SimpleNamespace(
            Complete=True,
            Portfolios={
                Contract: SimpleNamespace(
                    Complete=True,
                    Variants=(),
                    DomainFingerprint=(
                        f"portfolio-{len(CompileCalls)}-{Contract}"
                    ),
                    Status="complete",
                    ExpansionCount=0,
                    Diagnostics={},
                )
                for Contract in PortsByContract
            },
            CanonicalStateCount=0,
            NetVariantBuildCount=0,
            Diagnostics={},
        )

    def OracleRow(
        _ProblemValue,
        *,
        CurrentSignal,
        CompleteSignal,
        CurrentPortsByContract,
        CompleteLocalContractFingerprint,
        DomainFingerprintsByCurrentContract,
        **_Keywords,
    ):
        return SimpleNamespace(
            Results={
                Contract: SimpleNamespace(
                    CurrentSignal=CurrentSignal,
                    CompleteSignal=CompleteSignal,
                    CurrentLocalContractFingerprint=Contract,
                    CompleteLocalContractFingerprint=(
                        CompleteLocalContractFingerprint
                    ),
                    Complete=True,
                    Status="feasible",
                    Feasible=True,
                    ProofFingerprint="witness",
                    DomainFingerprint=(
                        DomainFingerprintsByCurrentContract[Contract]
                    ),
                    ExpansionCount=0,
                    Detail="feasible",
                    Diagnostics={},
                )
                for Contract in CurrentPortsByContract
            },
            AccessSignatureCount=len(CurrentPortsByContract),
            VariantScanCount=0,
            SignaturePairCheckCount=0,
        )

    ProofDomain = ["full-local-domain"]
    monkeypatch.setattr(
        ComponentPipeline,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _ProblemValue: ProofDomain[0],
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "BuildCompleteComponentNetPortfolioStaticContext",
        BuildStaticContext,
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "CompileCompleteComponentNetVariantPortfolios",
        Compile,
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "BuildCompleteOpposingNetAccessContractDomain",
        lambda *Arguments, **Keywords: (
            ContractDomainBuildCalls.append(True),
            BuildContractDomain(*Arguments, **Keywords),
        )[1],
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "EvaluateCompleteOpposingNetAccessContractRow",
        OracleRow,
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "CertifyDirectionalLocalContractPortfolio",
        CartesianPortfolio,
    )

    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            "portfolio-factor-domain",
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    First = ComponentPipeline.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        Resources,
        DeadlineSeconds=1.0,
    )
    Second = ComponentPipeline.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        Resources,
        DeadlineSeconds=1.0,
    )
    ProofDomain[0] = "changed-full-local-domain"
    Third = ComponentPipeline.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        Resources,
        DeadlineSeconds=1.0,
    )

    assert StaticContextCalls == ["Complete", "Complete"]
    assert CompileCalls == [
        ("Complete", StaticContext),
        ("Complete", StaticContext),
        ("Complete", StaticContext),
    ]
    assert First["PersistentPortfolioContextReused"] is False
    assert Second["PersistentPortfolioContextReused"] is True
    assert Third["PersistentPortfolioContextReused"] is False
    assert First["CachedCompletePortfolioCount"] == 2
    assert Second["CachedCompletePortfolioCount"] == 2
    assert First["CachedOpposingRowContextCount"] == 2
    assert Second["CachedOpposingRowContextCount"] == 2
    assert len(ContractDomainBuildCalls) == 2
    assert First["CurrentAccessContractDomainReused"] is False
    assert Second["CurrentAccessContractDomainReused"] is True


def test_local_interface_factor_reevaluates_incomplete_bulk_row(monkeypatch):
    Assembly = _Assembly(_Problem())
    def Option(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricDomainFingerprint=f"domain-{X}",
            OwnedTerminals=((X, 7, 0),),
            OwnedTerminalFingerprints=(f"terminal-{X}",),
            OwnedCandidateFingerprints=(f"candidate-{X}",),
            OwnedAccessCandidates=(),
            FabricAttachment=(X, 7, 0),
            Attachment=(X, 7, 0),
            LocalPath=((X, 7, 0),),
            Capacity=1,
        )

    Current = Option("Current", 0)
    Complete = Option("Complete", 1)
    BulkCalls = []

    monkeypatch.setattr(
        ComponentPipeline,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _ProblemValue: "full-local-domain",
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "BuildCompleteComponentNetPortfolioStaticContext",
        lambda *_Arguments: object(),
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "CompileCompleteComponentNetVariantPortfolios",
        lambda *_Arguments, **_Keywords: SimpleNamespace(
            Complete=True,
            Portfolios={
                Contract: SimpleNamespace(
                    Complete=True,
                    Variants=(),
                    DomainFingerprint="complete-portfolio",
                    Status="complete",
                    ExpansionCount=0,
                    Diagnostics={},
                )
                for Contract in _Arguments[2]
            },
            CanonicalStateCount=0,
            NetVariantBuildCount=0,
            Diagnostics={},
        ),
    )

    def Bulk(
        _ProblemValue,
        *,
        CurrentSignal,
        CompleteSignal,
        CurrentPortsByContract,
        CompleteLocalContractFingerprint,
        DomainFingerprintsByCurrentContract,
        **_Keywords,
    ):
        BulkCalls.append(True)
        IsComplete = len(BulkCalls) > 1
        return SimpleNamespace(
            Results={
                Contract: SimpleNamespace(
                    CurrentSignal=CurrentSignal,
                    CompleteSignal=CompleteSignal,
                    CurrentLocalContractFingerprint=Contract,
                    CompleteLocalContractFingerprint=(
                        CompleteLocalContractFingerprint
                    ),
                    Complete=IsComplete,
                    Status=(
                        "architectural-unsatisfiable"
                        if IsComplete
                        else "incomplete"
                    ),
                    Feasible=False if IsComplete else None,
                    ProofFingerprint="proof" if IsComplete else "",
                    DomainFingerprint=(
                        DomainFingerprintsByCurrentContract[Contract]
                    ),
                    ExpansionCount=0,
                    Detail="",
                    Diagnostics={},
                )
                for Contract in CurrentPortsByContract
            },
            AccessSignatureCount=1,
            VariantScanCount=0,
            SignaturePairCheckCount=0,
        )

    monkeypatch.setattr(
        ComponentPipeline,
        "EvaluateCompleteOpposingNetAccessContractRow",
        Bulk,
    )
    PortfolioCalls = []

    def Portfolio(
        _Plan,
        _CurrentSignal,
        _CompleteSignal,
        Resources,
        *,
        BuildProofDomainFingerprint,
        EvaluatePair,
        **_Keywords,
    ):
        PortfolioCalls.append(True)
        Proof = EvaluatePair(
            Current,
            Complete,
            BuildProofDomainFingerprint(Current, Complete),
        )
        if len(PortfolioCalls) == 1:
            Resources.PhysicalComponentLocalInterfaceFactorProofCache = {
                "frontier": object()
            }
            return {"Complete": False, "Status": "incomplete"}
        assert Proof["GlobalRelaxedLocalProofComplete"] is True
        return {"Complete": True, "Status": "complete"}

    monkeypatch.setattr(
        ComponentPipeline,
        "CertifyDirectionalLocalContractPortfolio",
        Portfolio,
    )
    Diagnostics = ComponentPipeline.CertifyLocalInterfaceFactorPortfolio(
        Assembly.Problem,
        Assembly.Plan,
        "Current",
        "Complete",
        SimpleNamespace(
            **_PreparedFactorDomainFixture(
                "incomplete-row-factor-domain",
                Current=(Current,),
                Complete=(Complete,),
            ),
            RejectedPhysicalComponentPortReservationSets=set(),
        ),
        DeadlineSeconds=1.0,
    )

    assert Diagnostics["Complete"] is True
    assert len(BulkCalls) == 2


def test_global_relaxed_domain_fingerprint_covers_local_contract_domains():
    Assembly = _Assembly(_Problem())
    Problem = Assembly.Problem
    Plan = Problem.PhysicalAssemblyPlan
    assert Plan is not None
    Base = ComponentPipeline.BuildGlobalRelaxedLocalProofDomainFingerprint(
        Problem
    )
    Port = Plan.Ports[0]
    Feedthrough = SimpleNamespace(
        Signal="Feed",
        EndpointPairs=(((0, 1, 0), (2, 1, 0)),),
        Capacity=1,
        ReservedPathNodes=((0, 1, 0), (1, 1, 0), (2, 1, 0)),
        Claims=_Claims(((0, 1, 0), (1, 1, 0), (2, 1, 0))),
        ReservationFingerprint="feedthrough",
    )
    Transit = SimpleNamespace(
        Signal="Transit",
        PartitionAxis="X",
        PartitionFingerprint="partition",
        Complete=True,
        Candidates=(),
    )
    ChangedProblems = (
        replace(
            Problem,
            PhysicalAssemblyPlan=replace(
                Plan,
                Ports=(
                    replace(
                        Port,
                        OwnedCandidateFingerprints=("selected-access",),
                    ),
                    *Plan.Ports[1:],
                ),
            ),
        ),
        replace(
            Problem,
            ExternalContinuationDomains=(
                Problem.OwnedTerminalDomains[0],
            ),
        ),
        replace(
            Problem,
            ForeignEscapeDomains=(Problem.OwnedTerminalDomains[0],),
        ),
        replace(Problem, ForeignTransitDomains=(Transit,)),
        replace(
            Problem,
            PhysicalAssemblyPlan=replace(
                Plan,
                Feedthroughs=(Feedthrough,),
            ),
        ),
    )

    assert all(
        ComponentPipeline.BuildGlobalRelaxedLocalProofDomainFingerprint(
            Changed
        ) != Base
        for Changed in ChangedProblems
    )


def test_relaxed_complete_two_port_core_prunes_exact_reservation_pair(
    monkeypatch,
):
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="assignment",
        Channels=(
            SimpleNamespace(Signal="PortA", RouteCandidateId="route-a"),
            SimpleNamespace(Signal="PortB", RouteCandidateId="route-b"),
        ),
        Ports=(
            SimpleNamespace(
                Signal="PortA",
                Direction="input",
                OwnedTerminals=((0, 1, 0),),
                OwnedAccessCandidates=(),
                Capacity=1,
                ReservationFingerprint="reservation-a",
                FabricDomainFingerprint="fabric-a",
                FabricAttachment=(0, 1, 0),
                Attachment=(1, 1, 0),
                LocalPath=((0, 1, 0), (1, 1, 0)),
                OwnedCandidateFingerprints=("access-a",),
            ),
            SimpleNamespace(
                Signal="PortB",
                Direction="input",
                OwnedTerminals=((0, 1, 2),),
                OwnedAccessCandidates=(),
                Capacity=1,
                ReservationFingerprint="reservation-b",
                FabricDomainFingerprint="fabric-b",
                FabricAttachment=(0, 1, 2),
                Attachment=(1, 1, 2),
                LocalPath=((0, 1, 2), (1, 1, 2)),
                OwnedCandidateFingerprints=("access-b",),
            ),
            SimpleNamespace(
                Signal="PortC",
                Direction="input",
                OwnedTerminals=((0, 1, 4),),
                OwnedAccessCandidates=(),
                Capacity=1,
                ReservationFingerprint="reservation-c",
                FabricDomainFingerprint="fabric-c",
                FabricAttachment=(0, 1, 4),
                Attachment=(1, 1, 4),
                LocalPath=((0, 1, 4), (1, 1, 4)),
                OwnedCandidateFingerprints=("access-c",),
            ),
        ),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        ForbiddenPhysicalComponentGlobalCandidateSets=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
    )
    Solve = ComponentRoutingSolveResult(
        Status="architectural-unsatisfiable",
        ProofFingerprint="proof",
        Diagnostics={
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreSignals": ["PortA", "PortB"],
            "LocalUnsatCoreFingerprint": "core",
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": "relaxed-proof",
            "GlobalRelaxedLocalDomainFingerprint": "relaxed-domain",
            "GlobalRelaxedLocalUnsatCoreSignals": ["PortA", "PortB"],
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
        },
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _Problem: "relaxed-domain",
    )

    Diagnostics = RecordPhysicalComponentLocalCompilationNoGood(
        Solve,
        Plan,
        SimpleNamespace(),
        Resources,
        Problem=SimpleNamespace(PhysicalAssemblyPlan=Plan),
    )

    assert Diagnostics["GlobalRelaxedLocalProofComplete"] is True
    assert Diagnostics["NoGoodScope"] == (
        "global-relaxed-local-port-core"
    )
    assert Diagnostics["NoGoodSignals"] == ["PortA", "PortB"]
    assert Diagnostics["GlobalRelaxedLocalUnsatCoreSignals"] == [
        "PortA",
        "PortB",
    ]
    assert Diagnostics["GlobalRelaxedLocalCoreComplete"] is True
    assert Diagnostics["GlobalRelaxedLocalProofFingerprint"] == (
        "relaxed-proof"
    )
    assert Diagnostics["GlobalRelaxedLocalDomainFingerprint"] == (
        "relaxed-domain"
    )
    ExpectedReservationKeys = {
        Port.Signal: (
            ComponentPipeline.BuildPhysicalPortLocalContractFingerprint(Port)
        )
        for Port in Plan.Ports
    }
    assert Diagnostics["NoGoodReservationKeys"] == [
        ["PortA", ExpectedReservationKeys["PortA"]],
        ["PortB", ExpectedReservationKeys["PortB"]],
    ]
    assert Diagnostics["RejectedPhysicalAssemblyPlanFingerprint"] == (
        "physical-plan"
    )
    assert Diagnostics["PreferredRetainedGlobalContracts"] == {
        Port.Signal: BuildPhysicalPortGlobalContractFingerprint(Port)
        for Port in Plan.Ports
    }
    assert (
        Resources.PreferredPhysicalComponentGlobalContractsBySignal
        == Diagnostics["PreferredRetainedGlobalContracts"]
    )
    assert "RejectedPortAssignmentFingerprint" not in Diagnostics
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((
            ("PortA", ExpectedReservationKeys["PortA"]),
            ("PortB", ExpectedReservationKeys["PortB"]),
        )),
    }
    assert not Resources.RejectedPhysicalComponentPortReservationsBySignal
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints
    assert not Resources.ForbiddenPhysicalComponentGlobalCandidateSets
    assert Resources.RejectedPhysicalComponentAssemblyPlanFingerprints == {
        "physical-plan"
    }
    assert all(
        Signal != "PortC"
        for RejectedSet
        in Resources.RejectedPhysicalComponentPortReservationSets
        for Signal, _Fingerprint in RejectedSet
    )


def test_relaxed_owned_tree_frontier_prunes_complete_signal_domain(
    monkeypatch,
):
    Port = SimpleNamespace(
        Signal="PortA",
        Direction="output",
        OwnedTerminals=((0, 1, 0),),
        OwnedAccessCandidates=(),
        Capacity=1,
        ReservationFingerprint="reservation-a",
        FabricDomainFingerprint="fabric-a",
        FabricAttachment=(0, 1, 0),
        Attachment=(1, 1, 0),
        LocalPath=((0, 1, 0), (1, 1, 0)),
        GlobalPath=((1, 1, 0), (2, 1, 0)),
        OwnedCandidateFingerprints=("access-a",),
    )
    Plan = SimpleNamespace(
        PlanFingerprint="physical-plan",
        PortAssignmentFingerprint="assignment",
        Channels=(),
        Ports=(Port,),
    )
    Resources = SimpleNamespace(
        RejectedPhysicalComponentPortReservationsBySignal={},
        RejectedPhysicalComponentPortReservationSets=set(),
        RejectedPhysicalComponentPortAssignmentFingerprints=set(),
        ForbiddenPhysicalComponentGlobalCandidateSets=set(),
        RejectedPhysicalComponentAssemblyPlanFingerprints=set(),
        PreparedPhysicalComponentPortFactorDomain=SimpleNamespace(
            Complete=True,
            Feasible=True,
            DomainFingerprint="prepared-domain",
        ),
    )
    Solve = ComponentRoutingSolveResult(
        Status="architectural-unsatisfiable",
        ProofFingerprint="proof",
        Diagnostics={
            "LocalUnsatCoreComplete": True,
            "LocalUnsatCoreSignals": ["PortA"],
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": "relaxed-proof",
            "GlobalRelaxedLocalDomainFingerprint": "relaxed-domain",
            "GlobalRelaxedLocalUnsatCoreSignals": ["PortA"],
            "GlobalRelaxedLocalUnsatCoreKind": (
                "tree-frontier-empty-owned-signal-domain"
            ),
            "LocalUnsatCoreProjectionFingerprint": "owned-domain",
        },
    )
    monkeypatch.setattr(
        ComponentPipeline,
        "BuildGlobalRelaxedLocalProofDomainFingerprint",
        lambda _Problem: "relaxed-domain",
    )

    Diagnostics = RecordPhysicalComponentLocalCompilationNoGood(
        Solve,
        Plan,
        SimpleNamespace(),
        Resources,
        Problem=SimpleNamespace(PhysicalAssemblyPlan=Plan),
    )

    PortSolverCacheKey = (
        ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
            "prepared-domain"
        )
    )
    SignalDomainKey = "local-signal-domain:" + PortSolverCacheKey
    assert Diagnostics["NoGoodScope"] == (
        "global-relaxed-owned-signal-domain"
    )
    assert Diagnostics["NoGoodReservationKeys"] == [
        ["PortA", SignalDomainKey]
    ]
    assert Resources.RejectedPhysicalComponentPortReservationSets == {
        frozenset((("PortA", SignalDomainKey),))
    }
    assert not Resources.RejectedPhysicalComponentPortAssignmentFingerprints


def test_complete_local_contract_pair_cover_promotes_fabric_pair():
    def Option(Signal, Fabric, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricDomainFingerprint=Fabric,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    FirstOptions = (
        Option("First", "fabric-first", 1),
        Option("First", "fabric-first", 2),
    )
    SecondOptions = (
        Option("Second", "fabric-second", 3),
        Option("Second", "fabric-second", 4),
    )
    Plan = SimpleNamespace(Ports=(FirstOptions[0], SecondOptions[0]))
    ExactPairs = {
        frozenset((
            (
                "First",
                BuildPhysicalPortLocalContractFingerprint(First),
            ),
            (
                "Second",
                BuildPhysicalPortLocalContractFingerprint(Second),
            ),
        ))
        for First in FirstOptions
        for Second in SecondOptions
    }
    DomainFingerprint = "complete-factor-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    DirectionalClauses = {
        frozenset((
            ("First", "local-signal-domain:" + CacheKey),
            (
                "Second",
                BuildPhysicalPortLocalContractFingerprint(Second),
            ),
        ))
        for Second in SecondOptions
    }
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            First=FirstOptions,
            Second=SecondOptions,
        ),
        RejectedPhysicalComponentPortReservationSets=(
            set(ExactPairs) | DirectionalClauses
        ),
    )

    Promoted = PromoteCoveredLocalContractNoGoods(
        Plan,
        ("Second", "First"),
        Resources,
    )
    Expected = frozenset((
        (
            "First",
            "local-factor-domain:" + CacheKey + ":fabric-first",
        ),
        (
            "Second",
            "local-factor-domain:" + CacheKey + ":fabric-second",
        ),
    ))
    assert Promoted == (Expected,)
    assert Expected in Resources.RejectedPhysicalComponentPortReservationSets
    assert ExactPairs.isdisjoint(
        Resources.RejectedPhysicalComponentPortReservationSets
    )
    assert DirectionalClauses <= (
        Resources.RejectedPhysicalComponentPortReservationSets
    )

    Resources.RejectedPhysicalComponentPortReservationSets = (
        set(ExactPairs) - {next(iter(ExactPairs))}
    )
    assert PromoteCoveredLocalContractNoGoods(
        Plan,
        ("First", "Second"),
        Resources,
    ) == ()
    assert Expected not in Resources.RejectedPhysicalComponentPortReservationSets


def test_directional_local_factor_no_good_requires_complete_pair_coverage():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    CompleteOptions = (Option("Complete", 3), Option("Complete", 4))
    Plan = SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0]))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildStableFingerprint((
        "physical-component-port-solver-cache-v2",
        DomainFingerprint,
    ))
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )

    # One selected pair proof cannot stand for the other Current contract.
    SelectedPair = frozenset((
        (
            "Current",
            BuildPhysicalPortLocalContractFingerprint(CurrentOptions[0]),
        ),
        (
            "Complete",
            BuildPhysicalPortLocalContractFingerprint(CompleteOptions[0]),
        ),
    ))
    Resources.RejectedPhysicalComponentPortReservationSets.add(SelectedPair)
    assert BuildDirectionalLocalFactorNoGoods(
        Plan,
        "Current",
        "Complete",
        Resources,
    ) == ()

    # Exact proofs covering the full cached Current domain permit resolution
    # to the prepared-domain key while retaining the exact Complete contract.
    for CurrentOption in CurrentOptions:
        Resources.RejectedPhysicalComponentPortReservationSets.add(
            frozenset((
                (
                    "Current",
                    BuildPhysicalPortLocalContractFingerprint(CurrentOption),
                ),
                (
                    "Complete",
                    BuildPhysicalPortLocalContractFingerprint(
                        CompleteOptions[0]
                    ),
                ),
            ))
        )
    NoGoods = BuildDirectionalLocalFactorNoGoods(
        Plan,
        "Current",
        "Complete",
        Resources,
    )
    Expected = frozenset((
        ("Current", "local-signal-domain:" + CacheKey),
        (
            "Complete",
            BuildPhysicalPortLocalContractFingerprint(CompleteOptions[0]),
        ),
    ))
    assert NoGoods == (Expected,)
    assert BuildDirectionalLocalFactorNoGoods(
        Plan,
        "Complete",
        "Current",
        Resources,
    ) == ()

    Resources.PreparedPhysicalComponentPortFactorDomain = SimpleNamespace(
        DomainFingerprint="different-prepared-domain",
    )
    assert BuildDirectionalLocalFactorNoGoods(
        Plan,
        "Current",
        "Complete",
        Resources,
    ) == ()


def test_directional_local_factor_no_good_requires_current_contract_support():
    def Option(LocalX):
        return SimpleNamespace(
            Signal="Current",
            Direction="input",
            FabricDomainFingerprint="fabric-current",
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    Current = Option(1)
    UnsupportedCurrent = Option(2)
    Complete = SimpleNamespace(
        **{
            **vars(Option(3)),
            "Signal": "Complete",
            "FabricDomainFingerprint": "fabric-complete",
        }
    )
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildStableFingerprint((
        "physical-component-port-solver-cache-v2",
        DomainFingerprint,
    ))
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=(UnsupportedCurrent,),
            Complete=(Complete,),
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    assert BuildDirectionalLocalFactorNoGoods(
        SimpleNamespace(Ports=(Current, Complete)),
        "Current",
        "Complete",
        Resources,
    ) == ()


def test_local_interface_factor_portfolio_batches_and_reuses_exact_pairs():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    CompleteOptions = (Option("Complete", 3), Option("Complete", 4))
    Plan = SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0]))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    Evaluated = []

    def Domain(Current, Complete):
        return "proof:" + ":".join((
            BuildPhysicalPortLocalContractFingerprint(Current),
            BuildPhysicalPortLocalContractFingerprint(Complete),
        ))

    def Evaluate(Current, Complete, ProofDomain):
        Evaluated.append(ProofDomain)
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": (
                "proof-result:" + ProofDomain
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalUnsatCoreSignals": [
                "Current",
                "Complete",
            ],
            "GlobalRelaxedLocalDomainFingerprint": ProofDomain,
        }

    First = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=Domain,
        EvaluatePair=Evaluate,
    )

    assert First["Complete"]
    assert First["PairDomainCount"] == 4
    assert First["EvaluatedPairCount"] == 4
    assert First["CertifiedPairCount"] == 4
    assert First["DirectionalNoGoodCount"] == 2
    assert First["PromotedFabricNoGoodCount"] == 1

    Second = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=Domain,
        EvaluatePair=lambda *_Arguments: pytest.fail(
            "certified pair should come from the proof cache"
        ),
    )

    assert Second["Complete"]
    assert Second["EvaluatedPairCount"] == 0
    assert Second["PreviouslyCoveredPairCount"] == 4
    assert len(Evaluated) == 4


def test_local_interface_factor_portfolio_does_not_lift_incomplete_coverage():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    Complete = Option("Complete", 3)
    Plan = SimpleNamespace(Ports=(CurrentOptions[0], Complete))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=(Complete,),
        ),
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    FirstContract = BuildPhysicalPortLocalContractFingerprint(
        CurrentOptions[0]
    )

    def Domain(Current, _Complete):
        return BuildPhysicalPortLocalContractFingerprint(Current)

    def Evaluate(Current, _Complete, ProofDomain):
        if ProofDomain != FirstContract:
            return {
                "GlobalRelaxedLocalProofComplete": False,
                "GlobalRelaxedLocalCoreComplete": False,
                "GlobalRelaxedLocalDomainFingerprint": ProofDomain,
            }
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": (
                "proof-result:" + ProofDomain
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalUnsatCoreSignals": [
                "Current",
                "Complete",
            ],
            "GlobalRelaxedLocalDomainFingerprint": ProofDomain,
        }

    Result = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=Domain,
        EvaluatePair=Evaluate,
    )

    assert not Result["Complete"]
    assert Result["CertifiedPairCount"] == 0
    assert Result["EvaluatedPairCount"] == 1
    assert len(Result["IncompletePairs"]) == 1
    assert Result["DeferredIncompleteRowCount"] == 1
    assert Result["DirectionalNoGoodCount"] == 0
    assert Result["PromotedFabricNoGoodCount"] == 0

    RetryEvaluations = []

    def EvaluateRetry(_Current, _Complete, ProofDomain):
        RetryEvaluations.append(ProofDomain)
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofFingerprint": (
                "proof-result:" + ProofDomain
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalUnsatCoreSignals": [
                "Current",
                "Complete",
            ],
            "GlobalRelaxedLocalDomainFingerprint": ProofDomain,
        }

    Retried = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=Domain,
        EvaluatePair=EvaluateRetry,
    )

    assert Retried["Complete"]
    assert Retried["PreviouslyCoveredPairCount"] == 0
    assert Retried["EvaluatedPairCount"] == 2
    assert len(RetryEvaluations) == 2
    assert Retried["DirectionalNoGoodCount"] == 1


def test_local_interface_factor_portfolio_requires_complete_cartesian_proof():
    def Option(Signal, LocalX, Fabric):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint=Fabric,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (
        Option("Current", 1, "fabric-current"),
        Option("Current", 2, "fabric-current"),
    )
    CompleteOptions = (
        Option("Complete", 3, "fabric-complete"),
        Option("Complete", 4, "fabric-complete"),
    )
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    SeedPair = frozenset((
        (
            "Current",
            BuildPhysicalPortLocalContractFingerprint(CurrentOptions[0]),
        ),
        (
            "Complete",
            BuildPhysicalPortLocalContractFingerprint(CompleteOptions[0]),
        ),
    ))
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        RejectedPhysicalComponentPortReservationSets={SeedPair},
    )
    Plan = SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0]))
    Evaluated = []

    def ProofDomain(Current, Complete):
        return "proof:" + BuildPhysicalPortLocalContractFingerprint(
            Current
        ) + ":" + BuildPhysicalPortLocalContractFingerprint(Complete)

    def Evaluate(Current, Complete, Domain):
        Evaluated.append(Domain)
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofStatus": "architectural-unsatisfiable",
            "GlobalRelaxedLocalProofFingerprint": (
                "proof-result:" + Domain
            ),
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalUnsatCoreSignals": ["Current", "Complete"],
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Diagnostics = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Evaluate,
    )

    assert Diagnostics["Complete"] is True
    assert Diagnostics["PairDomainCount"] == 4
    assert Diagnostics["PreviouslyCoveredPairCount"] == 1
    assert Diagnostics["EvaluatedPairCount"] == 3
    assert Diagnostics["CertifiedPairCount"] == 3
    assert Diagnostics["DirectionalNoGoodCount"] == 2
    assert Diagnostics["PromotedFabricNoGoodCount"] == 1
    assert len(Evaluated) == 3

    Resources.RejectedPhysicalComponentPortReservationSets = {SeedPair}
    CachedDiagnostics = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=lambda *_Args: pytest.fail(
            "identical local proof domains must reuse the portfolio cache"
        ),
    )
    assert CachedDiagnostics["Complete"] is True
    assert CachedDiagnostics["CachedPairCount"] == 3
    assert CachedDiagnostics["EvaluatedPairCount"] == 0

    Resources.RejectedPhysicalComponentPortReservationSets = {SeedPair}
    ChangedDomainEvaluations = []

    def ChangedDomain(Current, Complete):
        return "changed:" + ProofDomain(Current, Complete)

    def EvaluateChanged(Current, Complete, Domain):
        ChangedDomainEvaluations.append(Domain)
        return Evaluate(Current, Complete, Domain)

    ChangedDiagnostics = CertifyDirectionalLocalContractPortfolio(
        Plan,
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ChangedDomain,
        EvaluatePair=EvaluateChanged,
    )
    assert ChangedDiagnostics["EvaluatedPairCount"] == 3
    assert ChangedDiagnostics["CachedPairCount"] == 0
    assert len(ChangedDomainEvaluations) == 3


def test_local_interface_factor_portfolio_stops_at_feasible_pair():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    CompleteOptions = (Option("Complete", 3), Option("Complete", 4))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    Evaluated = []

    def ProofDomain(Current, Complete):
        return "proof:" + BuildPhysicalPortLocalContractFingerprint(
            Current
        ) + ":" + BuildPhysicalPortLocalContractFingerprint(Complete)

    def Feasible(_Current, _Complete, Domain):
        Evaluated.append(Domain)
        return {
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalCoreComplete": False,
            "GlobalRelaxedLocalProofStatus": "feasible",
            "GlobalRelaxedLocalFeasibleWitnessComplete": True,
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Diagnostics = CertifyDirectionalLocalContractPortfolio(
        SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0])),
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Feasible,
    )

    assert Diagnostics["Complete"] is False
    assert Diagnostics["Status"] == "feasible-witness"
    assert Diagnostics["Reason"] == "exact-local-contract-pair-is-feasible"
    assert Diagnostics["FeasibleWitnessCount"] == 1
    assert Diagnostics["FeasibleWitness"] is not None
    assert Diagnostics["EvaluatedPairCount"] == 1
    assert Diagnostics["DirectionalNoGoodCount"] == 0
    assert Diagnostics["PromotedFabricNoGoodCount"] == 0
    assert len(Resources.RejectedPhysicalComponentPortReservationSets) == 0


def test_universal_promoted_fabric_clause_builds_direct_port_unsat_failure():
    def Option(Signal, Fabric, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            FabricDomainFingerprint=Fabric,
            Direction="input",
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    DomainFingerprint = "complete-prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    FirstOptions = (
        Option("First", "fabric-first", 1),
        Option("First", "fabric-first", 2),
    )
    SecondOptions = (
        Option("Second", "fabric-second", 3),
        Option("Second", "fabric-second", 4),
    )
    Clause = frozenset((
        (
            "First",
            "local-factor-domain:" + CacheKey + ":fabric-first",
        ),
        (
            "Second",
            "local-factor-domain:" + CacheKey + ":fabric-second",
        ),
    ))
    Plan = SimpleNamespace(
        Ports=(FirstOptions[0], SecondOptions[0]),
        PlanFingerprint="assembly-plan",
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            First=FirstOptions,
            Second=SecondOptions,
        ),
        RejectedPhysicalComponentPortReservationSets={Clause},
    )
    Diagnostics = {
        "Complete": True,
        "PortSolverCacheKey": CacheKey,
        "PromotedFabricNoGoodCount": 1,
        "PromotedFabricNoGoodKeys": [
            [list(Value) for Value in sorted(Clause)]
        ],
    }

    Failure = BuildUniversalPromotedFabricPortAssignmentFailure(
        Plan,
        Resources,
        Diagnostics,
    )

    assert Failure is not None
    assert Failure.Reason == (
        RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    )
    assert Failure.AffectedNets == ("First", "Second")
    assert Failure.Diagnostics["PortAssignmentProofComplete"]
    assert not Failure.Diagnostics["GlobalReplanEntered"]
    assert Failure.Diagnostics["CompletePortDomainSizes"] == {
        "First": 2,
        "Second": 2,
    }


def test_nonuniversal_promoted_fabric_clause_keeps_global_replan_available():
    DomainFingerprint = "multi-fabric-prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )

    def Option(Signal, Fabric):
        return SimpleNamespace(
            Signal=Signal,
            FabricDomainFingerprint=Fabric,
        )

    FirstOptions = (
        Option("First", "selected-fabric"),
        Option("First", "alternative-fabric"),
    )
    SecondOptions = (Option("Second", "second-fabric"),)
    Clause = frozenset((
        (
            "First",
            "local-factor-domain:" + CacheKey + ":selected-fabric",
        ),
        (
            "Second",
            "local-factor-domain:" + CacheKey + ":second-fabric",
        ),
    ))
    Diagnostics = {
        "Complete": True,
        "PortSolverCacheKey": CacheKey,
        "PromotedFabricNoGoodCount": 1,
        "PromotedFabricNoGoodKeys": [
            [list(Value) for Value in sorted(Clause)]
        ],
    }
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            First=FirstOptions,
            Second=SecondOptions,
        ),
        RejectedPhysicalComponentPortReservationSets={Clause},
    )
    Plan = SimpleNamespace(
        Ports=(FirstOptions[0], SecondOptions[0]),
        PlanFingerprint="assembly-plan",
    )

    assert BuildUniversalPromotedFabricPortAssignmentFailure(
        Plan,
        Resources,
        Diagnostics,
    ) is None


def test_local_interface_factor_portfolio_yields_after_one_complete_row():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (Option("Current", 1), Option("Current", 2))
    CompleteOptions = (Option("Complete", 3), Option("Complete", 4))
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        RejectedPhysicalComponentPortReservationSets=set(),
    )

    def ProofDomain(Current, Complete):
        return "proof:" + BuildPhysicalPortLocalContractFingerprint(
            Current
        ) + ":" + BuildPhysicalPortLocalContractFingerprint(Complete)

    def Unsatisfiable(_Current, _Complete, Domain):
        return {
            "GlobalRelaxedLocalProofComplete": True,
            "GlobalRelaxedLocalCoreComplete": True,
            "GlobalRelaxedLocalProofStatus": "architectural-unsatisfiable",
            "GlobalRelaxedLocalProofFingerprint": "proof-result:" + Domain,
            "GlobalRelaxedLocalUnsatCoreKind": (
                "complete-opposing-net-access-pair"
            ),
            "GlobalRelaxedLocalUnsatCoreSignals": ["Current", "Complete"],
            "GlobalRelaxedLocalCurrentSignal": "Current",
            "GlobalRelaxedLocalCompleteSignal": "Complete",
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Diagnostics = CertifyDirectionalLocalContractPortfolio(
        SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0])),
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Unsatisfiable,
        MaximumCompletedRows=1,
    )

    assert Diagnostics["Status"] == "partial-complete-rows"
    assert Diagnostics["CompletedRowLimitReached"] is True
    assert Diagnostics["CompletedRowCount"] == 1
    assert Diagnostics["ProcessedCompleteContractCount"] == 1
    assert Diagnostics["DeferredRowCount"] == 1
    assert Diagnostics["EvaluatedPairCount"] == 2

    # A global replan over the same frozen preparation must retain the
    # proof-qualified directional row and visit only the deferred row.  The
    # completed row is a monotonic local fact, not an assembly-plan variant.
    Resumed = CertifyDirectionalLocalContractPortfolio(
        SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[1])),
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Unsatisfiable,
    )

    assert Resumed["Complete"] is True
    assert Resumed["PreviouslyCoveredPairCount"] == 2
    assert Resumed["EvaluatedPairCount"] == 2
    assert Resumed["CertifiedPairCount"] == 2
    assert Resumed["CompletedRowCount"] == 1


def test_local_interface_factor_portfolio_does_not_cache_incomplete_proof():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    Current = Option("Current", 1)
    Complete = Option("Complete", 2)
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=(Current,),
            Complete=(Complete,),
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        PhysicalLocalPortPairSupportCertificateCache={},
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    EvaluationCount = 0

    def Incomplete(_Current, _Complete, Domain):
        nonlocal EvaluationCount
        EvaluationCount += 1
        return {
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalCoreComplete": False,
            "GlobalRelaxedLocalProofStatus": "incomplete",
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Arguments = dict(
        Plan=SimpleNamespace(Ports=(Current, Complete)),
        CurrentSignal="Current",
        CompleteSignal="Complete",
        Resources=Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=lambda *_Options: "proof-domain",
        EvaluatePair=Incomplete,
    )
    First = CertifyDirectionalLocalContractPortfolio(**Arguments)
    Second = CertifyDirectionalLocalContractPortfolio(**Arguments)

    assert First["Status"] == Second["Status"] == "incomplete"
    assert First["IncompletePairCount"] == 1
    assert First["DeferredIncompleteRowCount"] == 1
    assert Second["CachedPairCount"] == 0
    assert EvaluationCount == 2
    assert Resources.PhysicalComponentLocalInterfaceFactorProofCache == {}


def test_local_interface_factor_defers_incomplete_rows_and_finds_later_witness():
    def Option(Signal, LocalX):
        return SimpleNamespace(
            Signal=Signal,
            Direction="input",
            FabricDomainFingerprint="fabric-" + Signal,
            FabricAttachment=(0, 1, 0),
            OwnedTerminals=((0, 1, 0),),
            LocalPath=((0, 1, 0), (LocalX, 1, 0)),
            OwnedAccessCandidates=(),
            Capacity=1,
        )

    CurrentOptions = (
        Option("Current", 1),
        Option("Current", 2),
        Option("Current", 3),
    )
    CompleteOptions = (
        Option("Complete", 10),
        Option("Complete", 11),
        Option("Complete", 12),
    )
    CompleteByContract = {
        BuildPhysicalPortLocalContractFingerprint(Value): Value
        for Value in CompleteOptions
    }
    SelectedContract = BuildPhysicalPortLocalContractFingerprint(
        CompleteOptions[0]
    )
    OrderedCompleteContracts = tuple(sorted(
        CompleteByContract,
        key=lambda Value: (Value != SelectedContract, Value),
    ))
    FeasibleContract = OrderedCompleteContracts[-1]
    DomainFingerprint = "prepared-domain"
    CacheKey = ComponentPipeline.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    Resources = SimpleNamespace(
        **_PreparedFactorDomainFixture(
            DomainFingerprint,
            Current=CurrentOptions,
            Complete=CompleteOptions,
        ),
        PhysicalComponentLocalInterfaceFactorProofCache={},
        PhysicalLocalPortPairSupportCertificateCache={},
        RejectedPhysicalComponentPortReservationSets=set(),
    )
    EvaluationCountByCompleteContract = {}

    def ProofDomain(Current, Complete):
        return "proof:" + BuildPhysicalPortLocalContractFingerprint(
            Current
        ) + ":" + BuildPhysicalPortLocalContractFingerprint(Complete)

    def Evaluate(_Current, Complete, Domain):
        CompleteContract = BuildPhysicalPortLocalContractFingerprint(
            Complete
        )
        EvaluationCountByCompleteContract[CompleteContract] = (
            EvaluationCountByCompleteContract.get(CompleteContract, 0) + 1
        )
        if CompleteContract == FeasibleContract:
            return {
                "GlobalRelaxedLocalProofComplete": False,
                "GlobalRelaxedLocalCoreComplete": False,
                "GlobalRelaxedLocalProofStatus": "feasible",
                "GlobalRelaxedLocalFeasibleWitnessComplete": True,
                "GlobalRelaxedLocalProofFingerprint": "witness:" + Domain,
                "GlobalRelaxedLocalDomainFingerprint": Domain,
            }
        return {
            "GlobalRelaxedLocalProofComplete": False,
            "GlobalRelaxedLocalCoreComplete": False,
            "GlobalRelaxedLocalProofStatus": "incomplete",
            "GlobalRelaxedLocalDomainFingerprint": Domain,
        }

    Diagnostics = CertifyDirectionalLocalContractPortfolio(
        SimpleNamespace(Ports=(CurrentOptions[0], CompleteOptions[0])),
        "Current",
        "Complete",
        Resources,
        LocalProofContextFingerprint="local-proof-context",
        BuildProofDomainFingerprint=ProofDomain,
        EvaluatePair=Evaluate,
        MaximumCompletedRows=None,
    )

    assert Diagnostics["Status"] == "feasible-witness"
    assert Diagnostics["FeasibleWitnessCount"] == 1
    assert Diagnostics["FeasibleWitness"] is not None
    assert Diagnostics["IncompletePairCount"] == 2
    assert Diagnostics["DeferredIncompleteRowCount"] == 2
    assert len(set(Diagnostics["DeferredIncompleteRows"])) == 2
    assert Diagnostics["EvaluatedPairCount"] == 3
    assert set(EvaluationCountByCompleteContract) == set(
        OrderedCompleteContracts
    )
    assert set(EvaluationCountByCompleteContract.values()) == {1}
    assert Diagnostics["CertifiedPairCount"] == 0
    assert len(
        Resources.PhysicalComponentLocalInterfaceFactorProofCache
    ) == 1
    assert Resources.RejectedPhysicalComponentPortReservationSets == set()


def test_local_pair_support_certificate_requires_complete_proof_row():
    Preparation = SimpleNamespace(
        Complete=True,
        Feasible=True,
        DomainFingerprint="prepared",
        ComponentGraphFingerprint="component",
        ResourceGraphFingerprint="resource",
        Problem=SimpleNamespace(
            Fabric=SimpleNamespace(FabricFingerprint="fabric"),
        ),
        AccessCertificate=SimpleNamespace(
            TechnologyFingerprint="technology",
        ),
    )
    Arguments = dict(
        Preparation=Preparation,
        PortSolverCacheKey="solver",
        RowSignal="CarryA",
        RowContract="local-a",
        ColumnSignal="CarryB",
        ColumnContracts=("local-b1", "local-b0"),
        LocalProofContextFingerprint="local-proof-context",
        PairProofRecords=_PairProofRecords(
            "CarryB",
            ("local-b1", "local-b0"),
            "CarryA",
            "local-a",
        ),
    )

    First = BuildPhysicalLocalPortPairSupportCertificate(**Arguments)
    Second = BuildPhysicalLocalPortPairSupportCertificate(**Arguments)
    assert First.Complete
    assert First == Second
    assert First.ColumnContracts == ("local-b0", "local-b1")
    assert First.ProofFingerprints == (
        "proof:local-b0",
        "proof:local-b1",
    )

    with pytest.raises(ValueError, match="row is incomplete"):
        BuildPhysicalLocalPortPairSupportCertificate(
            **{
                **Arguments,
                "PairProofRecords": Arguments["PairProofRecords"][:1],
            }
        )
    with pytest.raises(ValueError, match="complete feasible preparation"):
        BuildPhysicalLocalPortPairSupportCertificate(
            **{
                **Arguments,
                "Preparation": SimpleNamespace(
                    **{
                        **vars(Preparation),
                        "Complete": False,
                    }
                ),
            }
        )

    InvalidStatus = replace(
        Arguments["PairProofRecords"][0],
        Status="feasible",
        Feasible=True,
    )
    with pytest.raises(ValueError, match="row is incomplete"):
        BuildPhysicalLocalPortPairSupportCertificate(
            **{
                **Arguments,
                "PairProofRecords": (
                    InvalidStatus,
                    Arguments["PairProofRecords"][1],
                ),
            }
        )


def test_local_pair_proof_context_rejects_feedthrough_mutation():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=_AccessCertificate(
            Problem,
            Placed,
            Resources,
        ),
    )

    Context = BuildPhysicalLocalPairProofContextFingerprint(
        Problem,
        Preparation,
    )
    assert Context

    Feedthrough = ComponentFeedthroughContract(
        Signal="Foreign",
        EndpointPairs=(((0, 7, 0), (2, 7, 0)),),
        ReservedPathNodes=((0, 7, 0), (1, 7, 0), (2, 7, 0)),
        Claims=_Claims(((0, 7, 0), (1, 7, 0), (2, 7, 0))),
        ReservationFingerprint="feedthrough",
    )
    ChangedProblem = replace(
        Problem,
        Interface=replace(
            Problem.Interface,
            Feedthroughs=(Feedthrough,),
        ),
    )
    with pytest.raises(ValueError, match="differs from its prepared domain"):
        BuildPhysicalLocalPairProofContextFingerprint(
            ChangedProblem,
            Preparation,
        )


def test_factor_unsat_proof_does_not_claim_full_option_materialization():
    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    First = _Assembly(Problem, Resources)
    Port = First.Plan.Ports[0]
    Resources.RejectedPhysicalComponentPortReservationSets.add(
        frozenset(((
            Port.Signal,
            "fabric-domain:" + Port.FabricDomainFingerprint,
        ),))
    )

    with pytest.raises(RoutingStageError) as Context:
        _Assembly(Problem, Resources)

    Failure = Context.value.Failure
    assert Failure.Reason == (
        RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    )
    assert Failure.Diagnostics["PortAssignmentProofComplete"]
    assert not Failure.Diagnostics[
        "PortOptionMaterializationComplete"
    ]
    assert Failure.Diagnostics["PortAssignmentUnsatProofBasis"] == (
        "complete-factor-search"
    )
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreSignals"
    ] == ["Alpha"]
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreMinimal"
    ]
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreDirectReuse"
    ]
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreProofBasis"
    ] == "complete-factor-domain-no-good"
    assert Failure.Diagnostics[
        "PortAssignmentUnsatCoreCheckCount"
    ] == 0
    assert set(
        Failure.Diagnostics["PortDomainGenerationStatus"].values()
    ) == {"unvisited"}


def test_component_fabric_preserves_parallel_lane_capacity_domains():
    First = tuple((X, 7, 0) for X in range(3))
    Second = tuple((X, 7, 6) for X in range(3))
    Fabric = BuildComponentRoutingFabric(SimpleNamespace(
        PhysicalModel="parallel-test",
        Lanes=(
            SimpleNamespace(
                Cells=First,
                IngressNodes=(First[0], First[-1]),
            ),
            SimpleNamespace(
                Cells=Second,
                IngressNodes=(Second[0], Second[-1]),
            ),
        ),
    ))

    Connected = AugmentComponentRoutingFabric(
        Fabric,
        (First[0], Second[0]),
        _ResourceGraph(),
    )

    assert Connected.Complete
    assert Connected.TopologyKind == "closed-component-port-forest-v3"
    assert len(Connected.Edges) == len(Connected.Nodes) - 2

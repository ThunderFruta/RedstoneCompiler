"""Global Handoff contracts for physical assembly."""

from ._physical_assembly_contracts import *


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

def test_global_boundary_pair_arc_rejects_incompatible_future_domains():
    SharedClaims = _Claims(((0, 7, 0),))
    Domains = {
        "Alpha": tuple(
            replace(
                _BoundaryPort("Alpha", 10 + Index * 10),
                GlobalClaims=SharedClaims,
            )
            for Index in range(65)
        ),
        "Beta": tuple(
            replace(
                _BoundaryPort("Beta", 1010 + Index * 10),
                GlobalClaims=SharedClaims,
            )
            for Index in range(65)
        ),
        "Gamma": (_BoundaryPort("Gamma", 2010),),
    }
    WorkStages = []

    Assignments = tuple(IterPhysicalBoundaryPortAssignments(
        Domains,
        WorkCheck=lambda Work: WorkStages.append(Work["Stage"]),
    ))

    assert Assignments == ()
    assert "physical-port-boundary-pair-support-propagation" in WorkStages
    assert "physical-port-global-boundary-propagation" not in WorkStages

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
        CertifiedLocalNoGoodClauses=(frozenset((
            ("Alpha", "unused-certified-local-contract"),
        )),),
    )

    PersistentPairSupportCache = {}
    assert len(tuple(IterPhysicalBoundaryPortAssignments(
        **Arguments,
        PersistentPairSupportCache=PersistentPairSupportCache,
    ))) == 1
    CachedEntryCount = len(PersistentPairSupportCache)
    assert CachedEntryCount > 0
    assert len(tuple(IterPhysicalBoundaryPortAssignments(
        **Arguments,
        PersistentPairSupportCache=PersistentPairSupportCache,
    ))) == 1
    assert len(PersistentPairSupportCache) == CachedEntryCount
    ExtendedCertifiedClauses = (
        *Arguments["CertifiedLocalNoGoodClauses"],
        frozenset((("Beta", "unused-certified-local-contract"),)),
    )
    assert len(tuple(IterPhysicalBoundaryPortAssignments(
        **{
            **Arguments,
            "CertifiedLocalNoGoodClauses": ExtendedCertifiedClauses,
        },
        PersistentPairSupportCache=PersistentPairSupportCache,
    ))) == 1
    assert len(PersistentPairSupportCache) > CachedEntryCount
    Clause = frozenset(
        (
            Signal,
            "fabric-domain:" + Factor.FabricDomainFingerprint,
        )
        for Signal, Factor in LocalFactors.items()
    )
    assert tuple(IterPhysicalBoundaryPortAssignments(
        **{
                **Arguments,
                "CertifiedLocalNoGoodClauses": (
                    *ExtendedCertifiedClauses,
                    Clause,
                ),
        },
        PersistentPairSupportCache=PersistentPairSupportCache,
    )) == ()

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

def test_global_aperture_cache_is_scoped_by_connected_component_envelope():
    ResourceGraph = SimpleNamespace(
        Technology=DefaultRedstoneRoutingTechnology,
        BuildPrimitive=lambda _First, _Second: object(),
        BuildRouteClaims=lambda _Path: RoutingResourceClaims(),
    )
    Context = SimpleNamespace(
        ExteriorFabricByLayer={},
        AuthoritativeRegion=None,
        ComponentEnvelopeMinimum=(0, 0, 0),
        ComponentEnvelopeMaximum=(10, 2, 2),
        FabricComponentByNode={
            (0, 1, 0): 0,
            (10, 1, 0): 1,
        },
        FabricEnvelopeBoundsByComponent={
            0: ((0, 0, 0), (2, 2, 2)),
            1: ((0, 0, 0), (10, 2, 2)),
        },
        ResourceGraph=ResourceGraph,
        GlobalConnectorCache={},
        GlobalConnectorCacheHitCount=0,
        GlobalApertureTargetsCache={},
        GlobalApertureTargetContextBuildCount=0,
        GlobalApertureTargetDiagnosticsBySignal={},
        CertifiedStraightExteriorTargetCountBySignal={},
        AccessCertificate=None,
        GlobalConnectorForeignClaimsCache={},
        GlobalConnectorForeignEdgeLegalityCache={},
        ComponentKeepoutGuideCellsByLayer={},
        GlobalApertureStaticContractCache={},
        GlobalApertureStaticContractBuildCount=0,
        Resources=SimpleNamespace(
            PhysicalGlobalApertureTemplateCache={},
        ),
        Problem=SimpleNamespace(PlacementFingerprint="placement"),
        GlobalConnectorPortableCacheHitCount=0,
        GlobalConnectorPortableCacheValidationRejectCount=0,
        GlobalConnectorPortableCacheStoreCount=0,
        GlobalGuideFieldCache={},
        GlobalGuideFieldBuildCount=0,
        GlobalGuideFieldExpansionCount=0,
        GlobalGuideFieldHitCount=0,
        ResourceGraphFingerprint="resource-graph",
        GlobalConnectorSearchCount=0,
        GlobalConnectorExpansionCount=0,
        GlobalGuideFieldCanonicalPathCount=0,
        GlobalGuideFieldFallbackCount=0,
        NativeConnectorBatchWorkItems=0,
        NativeConnectorBatchActiveWorkerCount=0,
        NativeConnectorSearchResults={},
        NativeConnectorResultHitCount=0,
        NativeConnectorEmptyResultCount=0,
        NativeConnectorAcceptedPathCount=0,
        NativeConnectorValidationRejectCount=0,
        GlobalPathRejectionCountsBySignal={},
        WorkCheck=None,
    )
    Arguments = (
        Context,
        (3, 1, 0),
        (1, 0, 0),
        frozenset(((4, 0),)),
        "Port",
        0,
        {},
    )

    NearComponentPath = PhysicalPortPreparationHelpers.BuildGlobalPathToGuide(
        *Arguments,
        FabricAttachment=(0, 1, 0),
    )
    EnclosingComponentPath = (
        PhysicalPortPreparationHelpers.BuildGlobalPathToGuide(
            *Arguments,
            FabricAttachment=(10, 1, 0),
        )
    )

    assert NearComponentPath == ((3, 1, 0), (4, 1, 0))
    assert EnclosingComponentPath == ()
    assert Context.GlobalApertureTargetContextBuildCount == 2
    assert Context.GlobalApertureTargetDiagnosticsBySignal["Port"] == {
        "TargetContextBuildCount": 2,
        "EmptyFinalTargetContextCount": 1,
        "GuideCellCountTotal": 2,
        "OutsideEnvelopeGuideCellCountTotal": 1,
        "ExteriorAllowedGuideCellCountTotal": 1,
        "StraightFallbackTargetCountTotal": 0,
        "FinalTargetCountTotal": 1,
        "EmptyTargetSamples": [{
            "SeamAttachment": [3, 1, 0],
            "FabricAttachment": [10, 1, 0],
            "ComponentEnvelopeMinimum": [0, 0, 0],
            "ComponentEnvelopeMaximum": [10, 2, 2],
            "GuideCellCount": 1,
            "OutsideEnvelopeGuideCellCount": 0,
            "ExteriorAllowedGuideCellCount": 0,
            "StraightFallbackTargetCount": 0,
            "FinalTargetCount": 0,
            "OutsideEnvelopeGuideCellSamples": [],
            "ExteriorRejectedGuideCellSamples": [],
            "ExteriorAllowedGuideCellSamples": [],
            "StraightFallbackTargetSamples": [],
        }],
        "MinimumGuideCellCount": 1,
        "MaximumGuideCellCount": 1,
        "MinimumOutsideEnvelopeGuideCellCount": 0,
        "MaximumOutsideEnvelopeGuideCellCount": 1,
        "MinimumExteriorAllowedGuideCellCount": 0,
        "MaximumExteriorAllowedGuideCellCount": 1,
        "MinimumStraightFallbackTargetCount": 0,
        "MaximumStraightFallbackTargetCount": 0,
        "MinimumFinalTargetCount": 0,
        "MaximumFinalTargetCount": 1,
    }

    ExactRegionPath = (
        (101, 1, 0),
        (102, 1, 0),
        (103, 1, 0),
        (104, 1, 0),
        (104, 1, 1),
        *((X, 1, 1) for X in range(103, 49, -1)),
        (50, 1, 0),
    )
    ExactExteriorFabric = BuildPhysicalExteriorApertureFabric(
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(100, 2, 2),
        CompleteCoarseGuideCellsBySignal={"InteriorGuide": ((50, 0),)},
        DeclaredPortalIngressNodesBySignal={
            "InteriorGuide": ((101, 1, 0),),
        },
        Technology=DefaultRedstoneRoutingTechnology,
        MinimumPlacementY=0,
        Layer=0,
        KeepoutColumns=((0, 0), (100, 0)),
        DeclaredPortalIngressEnvelopeBoundsByNode={
            (101, 1, 0): (((0, 0, 0), (100, 2, 2)),),
        },
        RegionNodes=ExactRegionPath,
        RegionEdges=tuple(zip(ExactRegionPath, ExactRegionPath[1:])),
        RegionFingerprint="exact-interior-guide-region",
        ResourceGraphFingerprint="resource-graph",
        Complete=True,
    )
    Context.ExteriorFabricByLayer = {0: ExactExteriorFabric}
    Context.AuthoritativeRegion = object()
    Context.FabricComponentByNode[(0, 1, 0)] = 2
    Context.FabricEnvelopeBoundsByComponent[2] = (
        (0, 0, 0),
        (100, 2, 2),
    )
    Context.ComponentKeepoutGuideCellsByLayer = {
        0: frozenset(((0, 0), (100, 0))),
    }

    InteriorGuidePath = (
        PhysicalPortPreparationHelpers.BuildGlobalPathToGuide(
            Context,
            (101, 1, 0),
            (1, 0, 0),
            frozenset(((50, 0),)),
            "InteriorGuide",
            0,
            {},
            FabricAttachment=(0, 1, 0),
        )
    )

    assert InteriorGuidePath[:4] == ExactRegionPath[:4]
    assert InteriorGuidePath[-1] == (50, 1, 0)
    assert all(
        ExactExteriorFabric.AllowsNode(Node)
        for Node in InteriorGuidePath
    )
    assert Context.GlobalApertureTargetDiagnosticsBySignal[
        "InteriorGuide"
    ]["OutsideEnvelopeGuideCellCountTotal"] == 0
    assert Context.GlobalApertureTargetDiagnosticsBySignal[
        "InteriorGuide"
    ]["ExteriorAllowedGuideCellCountTotal"] == 1

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

def test_component_egress_contract_honors_guide_facing_sides():
    Directions = SelectGuideFacingComponentEgressDirections(
        (0, 0, 0),
        (10, 5, 10),
        ((-2, 5), (5, 12)),
    )
    Paths = BuildComponentEgressPaths(
        (5, 1, 5),
        TargetY=3,
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(10, 5, 10),
        Directions=Directions,
    )

    assert Directions == ((-1, 0), (0, 1))
    assert {Path[-1] for Path in Paths} == {
        (-1, 3, 5),
        (5, 3, 11),
    }

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

def test_proof_guided_selection_prefers_current_generation():
    Backlog = SimpleNamespace(PlacementFingerprint="backlog")
    Fresh = SimpleNamespace(PlacementFingerprint="fresh")

    assert SelectFreshProofGuidedPlacementCandidate(
        (Backlog, Fresh),
        frozenset(),
        frozenset(("backlog",)),
    ) is Fresh
    assert SelectFreshProofGuidedPlacementCandidate(
        (Backlog, Fresh),
        frozenset(("fresh",)),
        frozenset(("backlog",)),
    ) is Backlog
    assert SelectFreshProofGuidedPlacementCandidate(
        (Backlog,),
        frozenset(),
        frozenset(("backlog",)),
        RequireCurrentGeneration=True,
    ) is None
    assert SelectFreshProofGuidedPlacementCandidate(
        (Backlog, Fresh),
        frozenset(),
        frozenset(("backlog",)),
        RequireCurrentGeneration=True,
    ) is Fresh

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
        ComponentAssemblyPipeline,
        "ValidateRoutedComponentHandoff",
        RejectHandoff,
    )
    with pytest.raises(RoutingStageError) as Error:
        ComponentAssemblyPipeline.AssembleClosedComponentForGlobalRouting(
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
        PhysicalComponentBoundaryAssignmentIteratorCache={
            "active-domain": object(),
        },
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
    assert set(Resources.PhysicalComponentBoundaryAssignmentIteratorCache) == {
        "active-domain",
    }
    assert Diagnostics["BoundaryIteratorContinuationPreserved"] is True
    assert Diagnostics["BoundaryIteratorCacheCleared"] is False
    assert Diagnostics["AssemblyPlanDomainClauseEpoch"] == 1

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
        ComponentCertification,
        "SolveComponentRoutingProblem",
        Solve,
    )

    PortfolioCache = {}
    NetCache = {}
    ClaimsCache = {}
    DiscoveryCache = {}
    Diagnostics = (
        ComponentCertification.ProveGlobalRelaxedLocalUnsatisfiability(
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

def test_global_relaxed_domain_fingerprint_covers_local_contract_domains():
    Assembly = _Assembly(_Problem())
    Problem = Assembly.Problem
    Plan = Problem.PhysicalAssemblyPlan
    assert Plan is not None
    Base = ComponentCertification.BuildGlobalRelaxedLocalProofDomainFingerprint(
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
        ComponentCertification.BuildGlobalRelaxedLocalProofDomainFingerprint(
            Changed
        ) != Base
        for Changed in ChangedProblems
    )

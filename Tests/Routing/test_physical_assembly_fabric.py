"""Fabric contracts for physical assembly."""

from ._physical_assembly_contracts import *


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

def test_boundary_local_support_dp_reuses_aperture_alias_domains():
    Signals = ("Alpha", "Beta", "Gamma")
    Boundaries = {
        Signal: tuple(
            _BoundaryPort(Signal, SignalIndex * 1000 + OptionIndex * 10)
            for OptionIndex in range(6)
        )
        for SignalIndex, Signal in enumerate(Signals)
    }
    Apertures = {
        Signal: tuple(
            SimpleNamespace(
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
                ApertureOptionFingerprint=(
                    "aperture-option:" + Boundary.ReservationFingerprint
                ),
            )
            for Boundary in Values
        )
        for Signal, Values in Boundaries.items()
    }
    LocalFactors = {
        Signal: SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Capacity=1,
            OwnedTerminals=((4000 + Index * 10, 7, 0),),
            FabricDomainFingerprint="fabric:" + Signal,
            FabricAttachment=(4000 + Index * 10, 7, 0),
            LocalPath=((4000 + Index * 10, 7, 0),),
            LocalAccessFingerprint="local:" + Signal,
            LocalContractFingerprint="contract:" + Signal,
            LocalClaims=_Claims(((4000 + Index * 10, 7, 0),)),
        )
        for Index, Signal in enumerate(Signals)
    }
    Supports = {
        Signal: tuple(
            SimpleNamespace(
                Signal=Signal,
                LocalAccessFingerprint=(
                    LocalFactors[Signal].LocalAccessFingerprint
                ),
                ApertureOptionFingerprint=(
                    Aperture.ApertureOptionFingerprint
                ),
            )
            for Aperture in Apertures[Signal]
        )
        for Signal in Signals
    }
    WorkEvents = []
    Arguments = dict(
        DomainsBySignal=Boundaries,
        LocalAccessFactorsBySignal={
            Signal: (Factor,)
            for Signal, Factor in LocalFactors.items()
        },
        ApertureFactorsBySignal=Apertures,
        LocalApertureSupportBySignal=Supports,
        PortSolverCacheKey="alias-domain-test",
        WorkCheck=WorkEvents.append,
    )

    Assignments = tuple(IterPhysicalBoundaryPortAssignments(**Arguments))

    assert len(Assignments) == 6 ** len(Signals)
    # The exact local relation has only one domain per signal.  Aperture names
    # must not cause the support DFS to cross its 64-expansion report boundary.
    assert not any(
        Event.get("Stage")
        == "physical-port-boundary-support-propagation"
        for Event in WorkEvents
    )
    HigherOrderNoGood = frozenset(
        (Signal, Factor.LocalContractFingerprint)
        for Signal, Factor in LocalFactors.items()
    )
    assert tuple(IterPhysicalBoundaryPortAssignments(
        **Arguments,
        CertifiedLocalNoGoodClauses=(HigherOrderNoGood,),
    )) == ()
    MixedApertureSeamNoGood = frozenset((
        (
            "Alpha",
            Boundaries["Alpha"][0].ApertureContractFingerprint,
        ),
        (
            "Beta",
            BuildPhysicalPortSeamContractFingerprint(
                LocalFactors["Beta"]
            ),
        ),
    ))
    MixedAssignments = tuple(IterPhysicalBoundaryPortAssignments(
        **Arguments,
        CertifiedLocalNoGoodClauses=(MixedApertureSeamNoGood,),
    ))
    assert len(MixedAssignments) == 5 * 6 * 6
    assert all(
        next(
            Value for Value in Assignment if Value.Signal == "Alpha"
        ) != Boundaries["Alpha"][0]
        for Assignment in MixedAssignments
    )
    LiveGeneralClauses = set()
    LiveFrontier = iter(IterPhysicalBoundaryPortAssignments(
        **Arguments,
        RejectedGlobalApertureClauses=LiveGeneralClauses,
    ))
    assert next(LiveFrontier)
    LiveGeneralClauses.add(MixedApertureSeamNoGood)
    assert all(
        next(
            Value for Value in Assignment if Value.Signal == "Alpha"
        ) != Boundaries["Alpha"][0]
        for Assignment in LiveFrontier
    )

def test_projection_support_collapses_geometry_distinct_proof_aliases():
    Signals = ("Alpha", "Beta", "Gamma")
    Boundaries = {
        Signal: _BoundaryPort(Signal, 1000 + Index * 100)
        for Index, Signal in enumerate(Signals)
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
        Signal: tuple(
            SimpleNamespace(
                Signal=Signal,
                Direction="output",
                Capacity=1,
                OwnedTerminals=((2000 + Alias, 7, 0),),
                FabricDomainFingerprint="fabric:" + Signal,
                FabricAttachment=(2000 + Alias, 7, 0),
                LocalPath=((2000 + Alias, 7, 0),),
                LocalAccessFingerprint="local:" + Signal,
                LocalContractFingerprint=(
                    f"contract:{Signal}:{Alias}"
                ),
                LocalClaims=_Claims(((2000 + Alias, 7, 0),)),
            )
            for Alias in range(16)
        )
        for Signal in Signals
    }
    Supports = {
        Signal: (SimpleNamespace(
            Signal=Signal,
            LocalAccessFingerprint="local:" + Signal,
            ApertureOptionFingerprint=(
                Apertures[Signal].ApertureOptionFingerprint
            ),
        ),)
        for Signal in Signals
    }
    HigherOrderNoGood = frozenset(
        (Signal, "fabric-domain:fabric:" + Signal)
        for Signal in Signals
    )
    WorkEvents = []

    Assignments = tuple(IterPhysicalBoundaryPortAssignments(
        {
            Signal: (Boundary,)
            for Signal, Boundary in Boundaries.items()
        },
        LocalAccessFactorsBySignal=LocalFactors,
        ApertureFactorsBySignal={
            Signal: (Aperture,)
            for Signal, Aperture in Apertures.items()
        },
        LocalApertureSupportBySignal=Supports,
        CertifiedLocalNoGoodClauses=(HigherOrderNoGood,),
        CertifiedNoGoodProjectionOnly=True,
        WorkCheck=WorkEvents.append,
    ))

    assert Assignments == ()
    # Absolute claim geometry and unreferenced contract identities are
    # intentionally outside the certified projection contract.  All 16
    # aliases per signal therefore form one exact live-clause value and
    # cannot multiply the local DFS.
    assert not any(
        Event.get("Stage")
        == "physical-port-boundary-support-propagation"
        for Event in WorkEvents
    )

def test_retained_boundary_iterator_observes_live_seam_no_goods():
    AlphaBoundaries = (
        _BoundaryPort("Alpha", 10),
        _BoundaryPort("Alpha", 20),
    )
    BetaBoundary = _BoundaryPort("Beta", 110)
    Boundaries = (*AlphaBoundaries, BetaBoundary)
    Apertures = {
        Boundary.ReservationFingerprint: SimpleNamespace(
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
            ApertureOptionFingerprint=(
                "aperture:" + Boundary.ReservationFingerprint
            ),
        )
        for Boundary in Boundaries
    }

    def LocalFactor(Signal, X):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Capacity=1,
            OwnedTerminals=((X, 7, 0),),
            FabricDomainFingerprint="fabric:" + Signal,
            FabricAttachment=(X, 7, 0),
            LocalPath=((X, 7, 0), (X + 1, 7, 0)),
            LocalAccessFingerprint="local:" + Signal,
            LocalContractFingerprint="contract:" + Signal,
            LocalClaims=_Claims(((X, 7, 0),)),
        )

    LocalFactors = {
        "Alpha": LocalFactor("Alpha", 300),
        "Beta": LocalFactor("Beta", 400),
    }
    Supports = {
        "Alpha": tuple(
            SimpleNamespace(
                Signal="Alpha",
                LocalAccessFingerprint="local:Alpha",
                ApertureOptionFingerprint=(
                    Apertures[Boundary.ReservationFingerprint]
                    .ApertureOptionFingerprint
                ),
            )
            for Boundary in AlphaBoundaries
        ),
        "Beta": (SimpleNamespace(
            Signal="Beta",
            LocalAccessFingerprint="local:Beta",
            ApertureOptionFingerprint=(
                Apertures[BetaBoundary.ReservationFingerprint]
                .ApertureOptionFingerprint
            ),
        ),),
    }
    LearnedClauses = set()
    Iterator = iter(IterPhysicalBoundaryPortAssignments(
        {
            "Alpha": AlphaBoundaries,
            "Beta": (BetaBoundary,),
        },
        LocalAccessFactorsBySignal={
            Signal: (Factor,)
            for Signal, Factor in LocalFactors.items()
        },
        ApertureFactorsBySignal={
            "Alpha": tuple(
                Apertures[Boundary.ReservationFingerprint]
                for Boundary in AlphaBoundaries
            ),
            "Beta": (
                Apertures[BetaBoundary.ReservationFingerprint],
            ),
        },
        LocalApertureSupportBySignal=Supports,
        LearnedLocalSeamNoGoodClauses=LearnedClauses,
        CertifiedNoGoodProjectionOnly=True,
    ))

    assert next(Iterator)
    LearnedClauses.add(frozenset(
        (
            Signal,
            BuildPhysicalPortSeamContractFingerprint(Factor),
        )
        for Signal, Factor in LocalFactors.items()
    ))
    assert tuple(Iterator) == ()

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
    assert Contract.EndpointDomainFingerprint
    assert Contract.EndpointCandidateFingerprint
    assert Contract.EndpointCandidateCount == 1
    assert Contract.EndpointPrescreenRetainedCandidateCount == 1
    assert Contract.EndpointPrescreenRejectedCandidateCount == 0
    Serialized = Contract.ToDictionary()
    assert Serialized["EndpointDomainFingerprint"] == (
        Contract.EndpointDomainFingerprint
    )
    assert Serialized["EndpointCandidateFingerprint"] == (
        Contract.EndpointCandidateFingerprint
    )
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
    assert First.ToDictionary()["CandidateCount"] == 2
    assert First.ToDictionary()["Complete"] is True
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
    assert First.GuideIdentityFingerprint == (
        RenamedAndReordered.GuideIdentityFingerprint
    )
    assert First.SignalBindingFingerprint != (
        RenamedAndReordered.SignalBindingFingerprint
    )
    assert not First.Complete

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

def test_exterior_aperture_fabric_uses_exact_disconnected_keepouts():
    RegionNodes = (
        (3, 1, 1),
        (4, 1, 1),
        (50, 1, 1),
        (99, 1, 1),
        # This node is an exact projected keepout for the second component.
        (101, 1, 1),
    )
    Fabric = BuildPhysicalExteriorApertureFabric(
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(102, 2, 2),
        CompleteCoarseGuideCellsBySignal={
            "Port": ((50, 1),),
        },
        DeclaredPortalIngressNodesBySignal={
            "Port": ((3, 1, 1),),
        },
        Technology=DefaultRedstoneRoutingTechnology,
        MinimumPlacementY=0,
        Layer=0,
        KeepoutColumns=((1, 1), (3, 1), (101, 1)),
        DeclaredPortalIngressEnvelopeBoundsByNode={
            (3, 1, 1): (
                ((0, 0, 0), (2, 2, 2)),
                ((100, 0, 0), (102, 2, 2)),
            ),
        },
        RegionNodes=RegionNodes,
        RegionEdges=(
            ((3, 1, 1), (4, 1, 1)),
            ((4, 1, 1), (50, 1, 1)),
            ((50, 1, 1), (99, 1, 1)),
        ),
        RegionFingerprint="disconnected-region",
        ResourceGraphFingerprint="resource-graph",
        Complete=True,
    )

    assert Fabric.PortalIngressEnvelopeBounds == (
        ((3, 1, 1), (0, 0, 0), (2, 2, 2)),
        ((3, 1, 1), (100, 0, 0), (102, 2, 2)),
    )
    assert Fabric.AllowsNode((3, 1, 1))
    assert Fabric.AllowsNode((50, 1, 1))
    assert Fabric.AllowsNode((99, 1, 1))
    assert not Fabric.AllowsNode((101, 1, 1))
    assert Fabric.AllowsEdge((4, 1, 1), (50, 1, 1))

    with pytest.raises(ValueError, match="outside the closed envelope"):
        BuildPhysicalExteriorApertureFabric(
            EnvelopeMinimum=(0, 0, 0),
            EnvelopeMaximum=(102, 2, 2),
            CompleteCoarseGuideCellsBySignal={"Port": ((50, 1),)},
            DeclaredPortalIngressNodesBySignal={
                "Port": ((101, 1, 1),),
            },
            Technology=DefaultRedstoneRoutingTechnology,
            MinimumPlacementY=0,
            Layer=0,
            DeclaredPortalIngressEnvelopeBoundsByNode={
                (101, 1, 1): (
                    ((100, 0, 0), (102, 2, 2)),
                ),
            },
        )

def test_complete_exterior_fabric_retains_every_exterior_region_edge():
    RegionNodes = frozenset((
        (-1, 1, 1),
        (-1, 1, 2),
        (-1, 1, 3),
        (0, 1, 3),
        (1, 1, 3),
        (2, 1, 3),
        (3, 1, 3),
        (4, 1, 3),
        (4, 1, 2),
        (4, 1, 1),
        # Interior Region nodes remain locally owned.
        (1, 1, 1),
    ))
    RegionPath = (
        (-1, 1, 1),
        (-1, 1, 2),
        (-1, 1, 3),
        (0, 1, 3),
        (1, 1, 3),
        (2, 1, 3),
        (3, 1, 3),
        (4, 1, 3),
        (4, 1, 2),
        (4, 1, 1),
    )
    RegionEdges = tuple(zip(RegionPath, RegionPath[1:]))

    Fabric = BuildPhysicalExteriorApertureFabric(
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(2, 2, 2),
        CompleteCoarseGuideCellsBySignal={"Port": ((4, 1),)},
        DeclaredPortalIngressNodesBySignal={
            "Port": ((-1, 1, 1),),
        },
        Technology=DefaultRedstoneRoutingTechnology,
        MinimumPlacementY=0,
        Layer=0,
        RegionNodes=reversed(sorted(RegionNodes)),
        RegionEdges=reversed(RegionEdges),
        RegionFingerprint="region",
        ResourceGraphFingerprint="resource-graph",
        Complete=True,
    )
    Reordered = BuildPhysicalExteriorApertureFabric(
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(2, 2, 2),
        CompleteCoarseGuideCellsBySignal={"Port": ((4, 1),)},
        DeclaredPortalIngressNodesBySignal={
            "Port": ((-1, 1, 1),),
        },
        Technology=DefaultRedstoneRoutingTechnology,
        MinimumPlacementY=0,
        Layer=0,
        RegionNodes=sorted(RegionNodes),
        RegionEdges=RegionEdges,
        RegionFingerprint="region",
        ResourceGraphFingerprint="resource-graph",
        Complete=True,
    )

    assert Fabric.Complete
    assert Fabric == Reordered
    assert Fabric.RegionFingerprint == "region"
    assert Fabric.ResourceGraphFingerprint == "resource-graph"
    assert (1, 1, 1) not in Fabric.AllowedNodes
    # These intermediate nodes are neither a guide nor an ingress.  The
    # explicit Region, rather than a ring/guide intersection, owns them.
    assert (1, 1, 3) in Fabric.AllowedNodes
    assert all(
        Fabric.AllowsEdge(First, Second)
        for First, Second in RegionEdges
    )
    assert Fabric.Neighbors((-1, 1, 1)) == ((-1, 1, 2),)

def test_exterior_fabric_identity_includes_edges_and_rejects_bad_adjacency():
    Arguments = dict(
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(0, 0, 0),
        CompleteCoarseGuideCellsBySignal={"Port": ((2, 0),)},
        DeclaredPortalIngressNodesBySignal={
            "Port": ((-1, 1, 0),),
        },
        Technology=DefaultRedstoneRoutingTechnology,
        MinimumPlacementY=0,
        Layer=0,
        RegionNodes=((-1, 1, 0), (1, 1, 0), (2, 1, 0)),
        RegionFingerprint="region",
        ResourceGraphFingerprint="resource",
        Complete=True,
    )
    Disconnected = BuildPhysicalExteriorApertureFabric(
        RegionEdges=(),
        **Arguments,
    )
    Connected = BuildPhysicalExteriorApertureFabric(
        RegionEdges=(((1, 1, 0), (2, 1, 0)),),
        **Arguments,
    )

    assert Disconnected.FabricFingerprint != Connected.FabricFingerprint
    assert not Disconnected.AllowsEdge((1, 1, 0), (2, 1, 0))
    assert Connected.AllowsEdge((2, 1, 0), (1, 1, 0))
    with pytest.raises(ValueError, match="adjacency differs"):
        replace(Connected, Adjacency=())

def test_certified_unary_infeasible_seam_skips_global_connector_search(
    monkeypatch,
):
    Claims = RoutingResourceClaims()
    AccessCandidate = ComponentTerminalAccessCandidate(
        CandidateFingerprint="owned-access",
        Attachment=(0, 1, 0),
        Path=((0, 1, 0),),
        Claims=Claims,
        Layer=0,
    )
    TerminalDomain = ComponentTerminalAccessDomain(
        Signal="Blocked",
        Terminal=(0, 1, 0),
        TerminalRole="source",
        TerminalFingerprint="terminal",
        Candidates=(AccessCandidate,),
    )
    CertifiedCandidate = ComponentPerimeterPortCandidate(
        CandidateFingerprint="certified-seam",
        Signal="Blocked",
        Direction="output",
        FabricDomainFingerprint="fabric",
        OwnedTerminals=((0, 1, 0),),
        OwnedCandidateFingerprints=("owned-access",),
        FabricAttachment=(0, 1, 0),
        Attachment=(2, 1, 0),
        LocalPath=((1, 1, 0), (2, 1, 0)),
        Claims=Claims,
        Layer=0,
    )
    Problem = SimpleNamespace(
        Interface=SimpleNamespace(Ports=(ComponentInterfacePort(
            Signal="Blocked",
            Direction="output",
            OwnedTerminals=((0, 1, 0),),
            ExternalTerminalCount=1,
        ),)),
        OwnedTerminalDomains=(TerminalDomain,),
        ExternalContinuationTerminals=(),
    )
    Context = SimpleNamespace(
        Problem=Problem,
        CoarsePlan=SimpleNamespace(
            Layers={"Blocked": 0},
            Guides={"Blocked": ((3, 0),)},
        ),
        CertifiedGuideLayerReassignmentsBySignal={},
        ExteriorFabricByLayer={},
        CertifiedPortDomainBySignal={
            "Blocked": ComponentPortBankDomain(
                Signal="Blocked",
                Direction="output",
                Candidates=(CertifiedCandidate,),
                Complete=True,
            ),
        },
        FabricComponentByNode={(0, 1, 0): 0},
        NativeConnectorBatchWorkItems=1,
        NativeConnectorBatchActiveWorkerCount=1,
        GlobalPathRejectionCountsBySignal={},
        AccessCertificate=SimpleNamespace(Complete=True),
        LaneFactorsBySignal={},
        LaneFactorDiagnosticsBySignal={},
        PoweredSeamFabricAdjacency={},
        PoweredSeamFabricParentCache={},
        PoweredSeamRouteClaimsCache={},
        PoweredSeamTreeRepeaterSubproblemCache={},
        PoweredSeamTreeRepeaterCacheStatistics={},
    )
    LocalFilterCalls = []
    GlobalSearchCalls = []

    def RejectLocalSeam(*_Arguments, **_Keywords):
        LocalFilterCalls.append(True)
        return ((),)

    def RecordGlobalSearch(*_Arguments, **_Keywords):
        GlobalSearchCalls.append(True)
        return ()

    monkeypatch.setattr(
        PhysicalPortPreparationHelpers,
        "FilterExternalSourcePoweredSeamCandidateDomains",
        RejectLocalSeam,
    )
    monkeypatch.setattr(
        PhysicalPortPreparationFactors,
        "BuildGlobalPathToGuide",
        RecordGlobalSearch,
    )

    PhysicalPortPreparationInputs.PreparePhysicalPortConnectorSearch(Context)
    BuildPhysicalPortLaneFactors(Context)

    assert LocalFilterCalls == [True]
    assert GlobalSearchCalls == []
    assert Context.NativeConnectorSearchRequests == {}
    assert Context.LaneFactorsBySignal["Blocked"] == ()
    assert (
        Context.LaneFactorDiagnosticsBySignal["Blocked"]
        ["CertifiedUnarySeamInfeasibleCount"]
        == 1
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
        ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
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

def test_fixed_boundary_local_rejections_advance_seams_without_cycling(
    monkeypatch,
):
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    OriginalDecompose = (
        PhysicalPortPreparationFactors.DecomposePhysicalPortLaneFactors
    )

    def DecomposeWithAlternateLocalSupport(*Arguments, **Keywords):
        LocalDomains, ApertureDomains, SupportDomains = (
            OriginalDecompose(*Arguments, **Keywords)
        )
        Signal, LocalFactors = LocalDomains[0]
        LocalByFingerprint = {
            Value.LocalAccessFingerprint: Value
            for Value in LocalFactors
        }
        AlternateLocals = []
        AlternateSupports = []
        for Support in SupportDomains[0][1]:
            Local = LocalByFingerprint[Support.LocalAccessFingerprint]
            Suffix = ":alternate:" + Support.ApertureOptionFingerprint
            AlternateLocal = replace(
                Local,
                FabricDomainFingerprint=(
                    Local.FabricDomainFingerprint + Suffix
                ),
                LocalAccessFingerprint=(
                    Local.LocalAccessFingerprint + Suffix
                ),
                LocalContractFingerprint="",
            )
            AlternateLocal = replace(
                AlternateLocal,
                LocalContractFingerprint=(
                    BuildPhysicalPortLocalContractFingerprint(
                        AlternateLocal
                    )
                ),
            )
            AlternateLocals.append(AlternateLocal)
            AlternateSupports.append(replace(
                Support,
                LocalAccessFingerprint=(
                    AlternateLocal.LocalAccessFingerprint
                ),
                SourceSeamFingerprint=(
                    Support.SourceSeamFingerprint + Suffix
                ),
                ReservationFingerprint=(
                    Support.ReservationFingerprint + Suffix
                ),
                SupportFingerprint=(
                    Support.SupportFingerprint + Suffix
                ),
            ))
        return (
            ((Signal, (*LocalFactors, *AlternateLocals)),),
            ApertureDomains,
            ((Signal, (*SupportDomains[0][1], *AlternateSupports)),),
        )

    monkeypatch.setattr(
        PhysicalPortPreparationFactors,
        "DecomposePhysicalPortLaneFactors",
        DecomposeWithAlternateLocalSupport,
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
        "Compiler.Routing.Components.Reservations."
        "FinalizePhysicalComponentChannelReservations",
        lambda Channels, *_Arguments, **_Keywords: Channels,
    )
    Events = []
    Current = SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=Events.append,
        DeferLocalCompositeSelection=True,
    )
    FixedBoundary = Current.Plan.GlobalBoundaryPorts
    FixedBoundaryFingerprint = (
        BuildPhysicalBoundaryPortAssignmentFingerprint(FixedBoundary)
    )
    FixedGlobalContracts = tuple(
        (
            Port.Signal,
            Port.GlobalContractFingerprint,
            Port.ApertureContractFingerprint,
        )
        for Port in FixedBoundary
    )
    SeenAssignments = set()
    SupportCount = sum(
        len(Supports)
        for _Signal, Supports
        in Preparation.LocalApertureSupportBySignal
    )

    for _Index in range(SupportCount + 1):
        AssignmentFingerprint = Current.Plan.PortAssignmentFingerprint
        assert AssignmentFingerprint not in SeenAssignments
        SeenAssignments.add(AssignmentFingerprint)
        Resources.RejectedPhysicalComponentPortAssignmentFingerprints.add(
            AssignmentFingerprint
        )
        try:
            Current = SolvePreparedPhysicalComponentPortFactorDomain(
                Preparation,
                Resources,
                WorkCheck=Events.append,
                DeferLocalCompositeSelection=True,
                RequiredBoundaryPorts=FixedBoundary,
            )
        except RoutingStageError as Error:
            assert Error.Failure.Reason == (
                RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
            )
            break
        assert BuildPhysicalBoundaryPortAssignmentFingerprint(
            Current.Plan.GlobalBoundaryPorts
        ) == FixedBoundaryFingerprint
        assert tuple(
            (
                Port.Signal,
                Port.GlobalContractFingerprint,
                Port.ApertureContractFingerprint,
            )
            for Port in Current.Plan.GlobalBoundaryPorts
        ) == FixedGlobalContracts
    else:
        pytest.fail(
            "fixed-boundary seam domain did not exhaust after every "
            "prepared support was rejected"
        )

    assert len(SeenAssignments) >= 2
    assert Resources.RejectedPhysicalComponentPortAssignmentFingerprints == (
        SeenAssignments
    )
    SelectedBoundaries = [
        Event["BoundaryAssignmentFingerprint"]
        for Event in Events
        if Event.get("Stage") == "physical-port-global-boundary-selected"
    ]
    assert SelectedBoundaries
    assert set(SelectedBoundaries) == {FixedBoundaryFingerprint}

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
        Deadline=None,
        DeferLocalCompositeSelection=False,
        RequiredBoundaryPorts=None,
    ):
        assert Value is Preparation
        assert Value.AccessCertificate is Certificate
        assert DeferLocalCompositeSelection
        assert RequiredBoundaryPorts is None
        return ExpectedAssembly

    monkeypatch.setattr(
        "Compiler.Routing.Authoritative.PortSolving."
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

def test_component_access_certificate_proves_empty_exterior_guide_domain():
    Problem = replace(_Problem(), ExternalContinuationTerminals=())
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    MinimumPlacementY = min(
        Value[1] for Value in Problem.Fabric.Nodes
    ) - 7

    Certificate = BuildComponentCutAccessFeasibilityCertificate(
        Problem,
        Resources.ResourceGraph,
        LayerCount=1,
        MinimumPlacementY=MinimumPlacementY,
        ComponentGraphFingerprint=(
            Placed.ComponentGraph.StructuralFingerprint
        ),
        RequiredGuideCellsBySignal={
            "Alpha": frozenset(((0, 0), (1, 0), (2, 0))),
        },
        PrioritySignals=("Alpha",),
    )

    assert Certificate.Complete
    assert not Certificate.Feasible
    assert Certificate.ProofKind == (
        "perimeter-seam-exterior-guide-target-empty"
    )
    assert Certificate.AffectedSignals == ("Alpha",)
    assert Certificate.Diagnostics["GuideCellCount"] == 3
    assert Certificate.Diagnostics["ExteriorGuideTargetCount"] == 0
    assert Certificate.Diagnostics["PrioritySignal"] is True

def test_component_access_guide_targets_include_external_continuations():
    Problem = _Problem()

    Targets = BuildComponentAccessGuideTargetColumns(
        Problem,
        {"Alpha": frozenset(((0, 0), (1, 0)))},
    )

    assert Targets["Alpha"] == frozenset(((0, 0), (1, 0), (12, 0)))

def test_component_access_uses_straight_enclosed_continuation_direction():
    assert SelectStraightContinuationEgressDirections(
        ((0, 1, 0), (2, 1, 0)),
        ((12, 7, 0),),
    ) == ((1, 0),)

    Problem = _Problem()
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    MinimumPlacementY = min(
        Value[1] for Value in Problem.Fabric.Nodes
    ) - 7
    Certificate = BuildComponentCutAccessFeasibilityCertificate(
        Problem,
        Resources.ResourceGraph,
        LayerCount=1,
        MinimumPlacementY=MinimumPlacementY,
        RequiredGuideCellsBySignal={
            "Alpha": frozenset(((0, 0), (1, 0), (2, 0))),
        },
    )

    assert Certificate.ProofKind != (
        "perimeter-seam-exterior-guide-target-empty"
    )

def test_certified_straight_seam_gets_one_exterior_target():
    Candidate = SimpleNamespace(
        Attachment=(3, 1, 0),
        LocalPath=((2, 1, 0), (3, 1, 0)),
    )
    Certificate = SimpleNamespace(
        Complete=True,
        Feasible=True,
        PortDomains=(SimpleNamespace(
            Signal="Alpha",
            Candidates=(Candidate,),
        ),),
    )

    assert SelectCertifiedStraightExteriorTargets(
        Certificate,
        "Alpha",
        (3, 1, 0),
        (1, 0, 0),
        (0, 0, -2),
        (5, 3, 2),
        None,
    ) == frozenset(((6, 1, 0),))
    assert not SelectCertifiedStraightExteriorTargets(
        Certificate,
        "Alpha",
        (3, 1, 0),
        (-1, 0, 0),
        (0, 0, -2),
        (5, 3, 2),
        None,
    )

def test_component_access_certificate_uses_signal_guide_facing_side():
    Problem = _Problem()
    Placed = _Placed(Problem)
    Resources = RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    MinimumPlacementY = min(
        Value[1] for Value in Problem.Fabric.Nodes
    ) - 7

    Certificate = BuildComponentCutAccessFeasibilityCertificate(
        Problem,
        Resources.ResourceGraph,
        LayerCount=1,
        MinimumPlacementY=MinimumPlacementY,
        ComponentGraphFingerprint=(
            Placed.ComponentGraph.StructuralFingerprint
        ),
        RequiredGuideCellsBySignal={
            "Alpha": frozenset(((4, 0), (5, 0))),
        },
    )

    assert Certificate.Feasible
    assert Certificate.PortDomains
    assert all(
        Candidate.LocalPath[-1][0] > Certificate.EnvelopeMaximum[0]
        and (
            Candidate.LocalPath[1][0] - Candidate.LocalPath[0][0],
            Candidate.LocalPath[1][2] - Candidate.LocalPath[0][2],
        ) == (1, 0)
        for Domain in Certificate.PortDomains
        for Candidate in Domain.Candidates
    )

def test_component_access_egress_uses_connected_fabric_envelope():
    Problem = _Problem()
    NearCells = tuple(Problem.Fabric.Nodes)
    FarCells = (
        (100, 7, 0),
        (101, 7, 0),
        (102, 7, 0),
    )
    Fabric = BuildComponentRoutingFabric(SimpleNamespace(
        PhysicalModel="two-disconnected-test-trees",
        ComponentId=3,
        InterfaceFingerprint="logical-interface",
        DeclaredFeedthroughSignals=(),
        AffectedClusters=(0, 1),
        AffectedSignals=("Alpha",),
        Lanes=(
            SimpleNamespace(
                Cells=NearCells,
                IngressNodes=(NearCells[0], NearCells[-1]),
            ),
            SimpleNamespace(
                Cells=FarCells,
                IngressNodes=(FarCells[0], FarCells[-1]),
            ),
        ),
    ))
    Problem = replace(Problem, Fabric=Fabric)
    Certificate = BuildComponentCutAccessFeasibilityCertificate(
        Problem,
        Problem.ResourceGraph,
        LayerCount=1,
        MinimumPlacementY=0,
        RequiredGuideCellsBySignal={
            "Alpha": frozenset(((4, 0), (5, 0))),
        },
    )

    assert Certificate.Feasible
    assert Certificate.EnvelopeMaximum[0] == 102
    LocalPaths = tuple(
        Candidate.LocalPath
        for Domain in Certificate.PortDomains
        for Candidate in Domain.Candidates
    )
    assert LocalPaths
    assert max(len(Path) for Path in LocalPaths) <= 10
    assert all(
        abs(Path[-1][0]) < 20 and abs(Path[-1][2]) < 20
        for Path in LocalPaths
    )

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
        PhysicalPortPreparationFactors,
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
    assert CertifiedSourceOptions == set()
    assert all(
        not Port.OwnedAccessCandidates
        for Port in Assembly.Plan.Ports
    )
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

def test_exterior_fabric_handoff_accepts_legacy_empty_and_exact_identity():
    Assembly = _Assembly(_Problem())
    Preparation = Assembly.PortFactorDomain
    assert Preparation is not None

    ValidatePhysicalExteriorFabricHandoff(Assembly.Plan, Preparation)
    Identity = {
        "ExteriorFabricSetFingerprint": "exterior-fabric-set",
        "ExteriorRegionFingerprint": "exterior-region",
        "ExteriorCapacityLedgerFingerprint": "capacity-ledger",
    }
    ValidatePhysicalExteriorFabricHandoff(
        replace(Assembly.Plan, **Identity),
        replace(Preparation, **Identity),
        CurrentResourceGraphFingerprint=(
            Preparation.ResourceGraphFingerprint
        ),
    )

def test_exterior_fabric_handoff_binds_preparation_plan_and_current_resource():
    Assembly = _Assembly(_Problem())
    Preparation = Assembly.PortFactorDomain
    assert Preparation is not None
    ResourceFingerprint = Preparation.ResourceGraphFingerprint

    ValidatePhysicalExteriorFabricHandoff(
        Assembly.Plan,
        Preparation,
        CurrentResourceGraphFingerprint=ResourceFingerprint,
    )
    for ChangedPlan, ChangedPreparation, CurrentFingerprint in (
        (
            replace(
                Assembly.Plan,
                ResourceGraphFingerprint="changed-plan-resource",
            ),
            Preparation,
            ResourceFingerprint,
        ),
        (
            Assembly.Plan,
            replace(
                Preparation,
                ResourceGraphFingerprint="changed-preparation-resource",
            ),
            ResourceFingerprint,
        ),
        (
            Assembly.Plan,
            Preparation,
            "changed-current-resource",
        ),
    ):
        with pytest.raises(
            ValueError,
            match="resource-graph identity mismatch",
        ):
            ValidatePhysicalExteriorFabricHandoff(
                ChangedPlan,
                ChangedPreparation,
                CurrentResourceGraphFingerprint=CurrentFingerprint,
            )

@pytest.mark.parametrize(
    "FieldName",
    (
        "ExteriorFabricSetFingerprint",
        "ExteriorRegionFingerprint",
        "ExteriorCapacityLedgerFingerprint",
    ),
)
def test_exterior_fabric_handoff_rejects_each_identity_mismatch(FieldName):
    Assembly = _Assembly(_Problem())
    Preparation = Assembly.PortFactorDomain
    assert Preparation is not None
    Identity = {
        "ExteriorFabricSetFingerprint": "exterior-fabric-set",
        "ExteriorRegionFingerprint": "exterior-region",
        "ExteriorCapacityLedgerFingerprint": "capacity-ledger",
    }
    ChangedIdentity = {**Identity, FieldName: "changed-identity"}

    with pytest.raises(ValueError, match=FieldName):
        ValidatePhysicalExteriorFabricHandoff(
            replace(Assembly.Plan, **Identity),
            replace(Preparation, **ChangedIdentity),
        )

def test_local_support_handoff_rejects_partial_exterior_identity_first():
    Assembly = _Assembly(_Problem())
    Preparation = Assembly.PortFactorDomain
    assert Preparation is not None
    ChangedPlan = replace(
        Assembly.Plan,
        ExteriorFabricSetFingerprint="exterior-fabric-set",
    )

    with pytest.raises(ValueError, match="complete.*identity triple"):
        BindPhysicalComponentAssemblyLocalPortSupports(
            replace(Assembly, Plan=ChangedPlan),
            Preparation,
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
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
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
    CacheKey = BuildStableFingerprint((
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
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
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
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
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

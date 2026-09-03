import os
from random import Random
import subprocess
import sys
from types import SimpleNamespace

import PhysicalDesign.Routing.Regions.Planning.InterfacePlanning as InterfacePlanning

from PhysicalDesign.Routing.Regions.Planning.InterfacePlanning import BuildComponentCapacityGuide, ComponentPlanningStatus, IterClosedComponentContracts, PlanClosedComponent, SolveComponentInterfaceCsp
from PhysicalDesign.Contracts.Component import PhysicalComponentBoundaryPortReservation
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims


def _Boundary(Signal, X, *, Z=0, Fingerprint=None):
    Fingerprint = Fingerprint or f"{Signal}:{X}:{Z}"
    Path = ((X, 3, Z), (X + 1, 3, Z))
    return PhysicalComponentBoundaryPortReservation(
        Signal=Signal,
        Direction="output",
        Attachment=Path[0],
        GlobalPath=Path,
        GlobalClaims=RoutingResourceClaims(
            WireCells=frozenset(Path),
        ),
        Capacity=1,
        ChannelContractFingerprint="channel:" + Fingerprint,
        GlobalContractFingerprint="global:" + Fingerprint,
        ApertureContractFingerprint="aperture:" + Fingerprint,
        ReservationFingerprint="reservation:" + Fingerprint,
    )


def _Preparation(Domains, *, Capacity=2, Usage=None, Guides=None):
    Signals = tuple(sorted(Domains))
    Problem = SimpleNamespace(
        Interface=SimpleNamespace(Complete=True),
        Fabric=SimpleNamespace(TopologyKind="tree-forest"),
    )
    CoarsePlan = SimpleNamespace(
        CorridorCapacity=Capacity,
        CorridorUsage=dict(Usage or {}),
        CorridorCosts={},
        Guides=dict(Guides or {}),
    )
    return SimpleNamespace(
        Complete=True,
        PlacementFingerprint="placement",
        ResourceGraphFingerprint="resource-graph",
        ExteriorCapacityLedgerFingerprint="ledger",
        AccessCertificate=SimpleNamespace(
            TechnologyFingerprint="technology",
        ),
        BoundaryPortReservationsBySignal=tuple(
            (Signal, tuple(Domains[Signal])) for Signal in Signals
        ),
        CoarsePlan=CoarsePlan,
        Problem=Problem,
    )


def test_capacity_guide_preserves_ordinary_demand_and_replaces_component_guides():
    Preparation = _Preparation(
        {
            "Alpha": (_Boundary("Alpha", 0),),
            "Beta": (_Boundary("Beta", 24),),
        },
        Capacity=2,
        Usage={(0, 0): 2, (2, 0): 1, (9, 9): 1},
        Guides={"Alpha": {(0, 0)}, "Ordinary": {(9, 9)}},
    )

    Guide = BuildComponentCapacityGuide(Preparation, TrackPitch=3)

    assert Guide.Complete
    assert dict(Guide.BaseUsage) == {(0, 0): 1, (2, 0): 1, (9, 9): 1}
    assert Guide.Diagnostics["AllNetCoarseDemandIncluded"] is True
    assert set(Guide.Domains()) == {"Alpha", "Beta"}


def test_native_lease_fixture_cross_checks_python_fallback(monkeypatch):
    """The native serialized lease fixture must retain Python's exact answer."""
    Alpha = (_Boundary("Alpha", 0, Fingerprint="a0"), _Boundary("Alpha", 12, Fingerprint="a1"))
    Beta = (_Boundary("Beta", 24, Fingerprint="b0"), _Boundary("Beta", 36, Fingerprint="b1"))
    Guide = BuildComponentCapacityGuide(
        _Preparation({"Alpha": Alpha, "Beta": Beta}), TrackPitch=3,
    )
    Clauses = (
        frozenset({("Alpha", Alpha[0].ApertureContractFingerprint)}),
        frozenset({
            ("Alpha", Alpha[1].ApertureContractFingerprint),
            ("Beta", Beta[0].ApertureContractFingerprint),
        }),
    )

    monkeypatch.delenv("RC_COMPONENT_LEASE_SOLVER", raising=False)
    Native = SolveComponentInterfaceCsp(Guide, RejectedClauses=Clauses)
    monkeypatch.setenv("RC_COMPONENT_LEASE_SOLVER", "python")
    Python = SolveComponentInterfaceCsp(Guide, RejectedClauses=Clauses)

    assert Native.Diagnostics["NativeLeaseSolver"] is True
    assert Native.Status == Python.Status == ComponentPlanningStatus.Feasible
    assert Native.Contract is not None and Python.Contract is not None
    assert Native.Contract.SelectedOptionFingerprints == (
        Python.Contract.SelectedOptionFingerprints
    )


def test_native_lease_fixture_cross_checks_unsat_and_budget(monkeypatch):
    Alpha = _Boundary("Alpha", 0, Fingerprint="a")
    Beta = _Boundary("Beta", 0, Fingerprint="b")
    Guide = BuildComponentCapacityGuide(
        _Preparation({"Alpha": (Alpha,), "Beta": (Beta,)}, Capacity=1),
        TrackPitch=3,
    )

    monkeypatch.delenv("RC_COMPONENT_LEASE_SOLVER", raising=False)
    NativeUnsat = SolveComponentInterfaceCsp(Guide)
    NativeBudget = SolveComponentInterfaceCsp(Guide, MaximumExpansions=0)
    monkeypatch.setenv("RC_COMPONENT_LEASE_SOLVER", "python")
    PythonUnsat = SolveComponentInterfaceCsp(Guide)
    PythonBudget = SolveComponentInterfaceCsp(Guide, MaximumExpansions=0)

    assert NativeUnsat.Status == PythonUnsat.Status == (
        ComponentPlanningStatus.InterfaceUnsatisfiable
    )
    assert NativeBudget.Status == PythonBudget.Status == (
        ComponentPlanningStatus.SearchIncomplete
    )
    assert NativeBudget.Diagnostics["NativeLeaseBudgetExhausted"] is True


def test_random_small_lease_domains_match_python_oracle(monkeypatch):
    """Cross-check native feasibility and witnesses over bounded domains."""
    for Seed in range(24):
        Generator = Random(Seed)
        Domains = {}
        for SignalIndex in range(3):
            Signal = f"Signal{SignalIndex}"
            Domains[Signal] = tuple(
                _Boundary(
                    Signal,
                    Generator.choice((0, 6, 12, 18)),
                    Fingerprint=f"{Signal}-{OptionIndex}",
                )
                for OptionIndex in range(Generator.randint(1, 3))
            )
        Guide = BuildComponentCapacityGuide(
            _Preparation(Domains, Capacity=1),
            TrackPitch=3,
        )
        ContractKeys = [
            (Signal, Option.ApertureContractFingerprint)
            for Signal, Options in sorted(Domains.items())
            for Option in Options
        ]
        RejectedClauses = []
        if Generator.choice((False, True)):
            RejectedClauses.append(frozenset((Generator.choice(ContractKeys),)))
        if Generator.choice((False, True)):
            First = Generator.choice(ContractKeys)
            OtherSignals = [
                Value for Value in ContractKeys if Value[0] != First[0]
            ]
            RejectedClauses.append(frozenset((
                First,
                Generator.choice(OtherSignals),
            )))

        monkeypatch.delenv("RC_COMPONENT_LEASE_SOLVER", raising=False)
        Native = SolveComponentInterfaceCsp(
            Guide,
            RejectedClauses=RejectedClauses,
        )
        monkeypatch.setenv("RC_COMPONENT_LEASE_SOLVER", "python")
        Python = SolveComponentInterfaceCsp(
            Guide,
            RejectedClauses=RejectedClauses,
        )

        assert Native.Status == Python.Status, Seed
        assert (Native.Contract is None) == (Python.Contract is None), Seed
        if Native.Contract is not None and Python.Contract is not None:
            for Contract in (Native.Contract, Python.Contract):
                SelectedKeys = frozenset(
                    (
                        Port.Signal,
                        Port.ApertureContractFingerprint,
                    )
                    for Port in Contract.SelectedBoundaryPorts
                )
                assert not Contract.Overflow, Seed
                assert not any(
                    Clause <= SelectedKeys for Clause in RejectedClauses
                ), Seed


def test_native_lease_deadline_and_unavailable_fail_closed(monkeypatch):
    Guide = BuildComponentCapacityGuide(
        _Preparation({"Alpha": (_Boundary("Alpha", 0),)}), TrackPitch=3,
    )

    NativeDeadline = SolveComponentInterfaceCsp(
        Guide,
        MaximumRuntimeSeconds=0.0,
    )
    assert NativeDeadline.Status == ComponentPlanningStatus.SearchIncomplete
    assert NativeDeadline.Diagnostics["NativeLeaseDeadlineExceeded"] is True

    monkeypatch.setattr(InterfacePlanning, "_SolveLeaseDomainsBounded", None)
    Unavailable = SolveComponentInterfaceCsp(Guide)
    assert Unavailable.Status == ComponentPlanningStatus.SearchIncomplete
    assert Unavailable.Diagnostics["NativeLeaseUnavailable"] is True


def test_native_lease_assignment_is_stable_across_rayon_thread_counts():
    Script = """
from PhysicalDesign.Routing.Regions.Planning.InterfacePlanning import BuildComponentCapacityGuide, SolveComponentInterfaceCsp
from Tests.PhysicalDesign.Routing.test_component_planning import _Boundary, _Preparation
Guide = BuildComponentCapacityGuide(_Preparation({
    'Alpha': (_Boundary('Alpha', 0, Fingerprint='a0'), _Boundary('Alpha', 12, Fingerprint='a1')),
    'Beta': (_Boundary('Beta', 0, Fingerprint='b0'),),
}, Capacity=1), TrackPitch=3)
Result = SolveComponentInterfaceCsp(Guide)
print(Result.Contract.SelectedOptionFingerprints if Result.Contract else Result.Status.value)
"""
    Results = []
    for ThreadCount in ("1", "4"):
        Environment = dict(os.environ, RC_ROUTING_THREADS=ThreadCount)
        Results.append(subprocess.check_output(
            [sys.executable, "-c", Script],
            text=True,
            env=Environment,
        ).strip())

    assert Results[0] == Results[1]


def test_port_first_capacity_guide_does_not_expand_local_composites():
    Boundary = _Boundary("Alpha", 0)
    Preparation = _Preparation({"Alpha": (Boundary,)})
    LocalFactors = tuple(
        SimpleNamespace(
            LocalAccessFingerprint=f"access-{Index}",
            LocalContractFingerprint=f"local-{Index}",
            SeamContractFingerprint=f"seam-{Index}",
        )
        for Index in range(3)
    )
    Aperture = SimpleNamespace(
        GlobalContractFingerprint=Boundary.GlobalContractFingerprint,
        ApertureContractFingerprint=(
            Boundary.ApertureContractFingerprint
        ),
        ApertureOptionFingerprint="aperture-option",
    )
    Supports = tuple(
        SimpleNamespace(
            LocalAccessFingerprint=Factor.LocalAccessFingerprint,
            SupportFingerprint=f"support-{Index}",
            ReservationFingerprint=f"port-{Index}",
        )
        for Index, Factor in enumerate(LocalFactors)
    )
    Preparation.LocalAccessFactorsBySignal = (("Alpha", LocalFactors),)
    Preparation.ApertureFactorsBySignal = (("Alpha", (Aperture,)),)
    Preparation.LocalApertureSupportsByOption = (
        (("Alpha", "aperture-option"), Supports),
    )

    Composite = BuildComponentCapacityGuide(
        Preparation,
        TrackPitch=3,
        IncludeLocalCompositeFactors=True,
    )
    PortFirst = BuildComponentCapacityGuide(
        Preparation,
        TrackPitch=3,
        IncludeLocalCompositeFactors=False,
    )

    assert len(Composite.Domains()["Alpha"]) == 3
    assert len(PortFirst.Domains()["Alpha"]) == 1
    assert PortFirst.Diagnostics["LocalCompositeFactorsIncluded"] is False


def test_interface_csp_propagates_live_unary_and_binary_no_goods():
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    Beta0 = _Boundary("Beta", 24, Fingerprint="b0")
    Beta1 = _Boundary("Beta", 36, Fingerprint="b1")
    Guide = BuildComponentCapacityGuide(
        _Preparation({
            "Alpha": (Alpha0, Alpha1),
            "Beta": (Beta0, Beta1),
        }),
        TrackPitch=3,
    )
    Clauses = (
        frozenset({("Alpha", Alpha0.ApertureContractFingerprint)}),
        frozenset({
            ("Alpha", Alpha1.ApertureContractFingerprint),
            ("Beta", Beta0.ApertureContractFingerprint),
        }),
    )

    Result = SolveComponentInterfaceCsp(
        Guide,
        RejectedClauses=Clauses,
    )

    assert Result.Status == ComponentPlanningStatus.Feasible
    assert Result.Contract is not None
    assert Result.Contract.Overflow == ()
    assert {
        Port.Signal: Port.ApertureContractFingerprint
        for Port in Result.Contract.SelectedBoundaryPorts
    } == {
        "Alpha": Alpha1.ApertureContractFingerprint,
        "Beta": Beta1.ApertureContractFingerprint,
    }
    assert Result.Diagnostics["PrunedOptionCount"] >= 1
    assert Result.Diagnostics[
        "NeverRevisitedRejectedPartialAssignment"
    ] is True


def test_interface_csp_retains_preferred_contracts_around_failure_pivot():
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    Beta0 = _Boundary("Beta", 24, Fingerprint="b0")
    Beta1 = _Boundary("Beta", 36, Fingerprint="b1")
    Guide = BuildComponentCapacityGuide(
        _Preparation({
            "Alpha": (Alpha0, Alpha1),
            "Beta": (Beta0, Beta1),
        }),
        TrackPitch=3,
    )

    Result = SolveComponentInterfaceCsp(
        Guide,
        PreferredGlobalContractsBySignal={
            "Alpha": Alpha1.GlobalContractFingerprint,
            "Beta": Beta1.GlobalContractFingerprint,
        },
    )

    assert Result.Contract is not None
    assert {
        Port.Signal: Port.GlobalContractFingerprint
        for Port in Result.Contract.SelectedBoundaryPorts
    } == {
        "Alpha": Alpha1.GlobalContractFingerprint,
        "Beta": Beta1.GlobalContractFingerprint,
    }


def test_contract_iterator_observes_live_minimum_delta_preferences():
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    Beta0 = _Boundary("Beta", 24, Fingerprint="b0")
    Beta1 = _Boundary("Beta", 36, Fingerprint="b1")
    Preferred = {}
    Contracts = IterClosedComponentContracts(
        _Preparation({
            "Alpha": (Alpha0, Alpha1),
            "Beta": (Beta0, Beta1),
        }),
        TrackPitch=3,
        PreferredApertureContractsBySignal=Preferred,
    )

    next(Contracts)
    Preferred.update({
        "Alpha": Alpha1.ApertureContractFingerprint,
        "Beta": Beta1.ApertureContractFingerprint,
    })
    Second = next(Contracts)

    assert {
        Port.Signal: Port.GlobalContractFingerprint
        for Port in Second.SelectedBoundaryPorts
    } == {
        "Alpha": Alpha1.GlobalContractFingerprint,
        "Beta": Beta1.GlobalContractFingerprint,
    }


def test_contract_iterator_prefers_larger_foreign_portal_slack():
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    Contract = next(iter(IterClosedComponentContracts(
        _Preparation({"Alpha": (Alpha0, Alpha1)}),
        TrackPitch=3,
        AperturePortalSlackBySignal={
            "Alpha": {
                Alpha0.ApertureContractFingerprint: (1, 10),
                Alpha1.ApertureContractFingerprint: (2, 20),
            },
        },
    )))

    assert Contract.SelectedBoundaryPorts == (Alpha1,)


def test_contract_iterator_exhausts_pivot_before_relaxing_nonpivot():
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    Beta0 = _Boundary("Beta", 24, Fingerprint="b0")
    Beta1 = _Boundary("Beta", 36, Fingerprint="b1")
    Preferred = {}
    Contracts = IterClosedComponentContracts(
        _Preparation({
            "Alpha": (Alpha0, Alpha1),
            "Beta": (Beta0, Beta1),
        }),
        TrackPitch=3,
        PreferredApertureContractsBySignal=Preferred,
    )

    First = next(Contracts)
    FirstPorts = {
        Port.Signal: Port for Port in First.SelectedBoundaryPorts
    }
    # Alpha is the failure pivot, so only Beta is frozen.
    Preferred["Beta"] = (
        FirstPorts["Beta"].ApertureContractFingerprint
    )
    Second = next(Contracts)
    SecondPorts = {
        Port.Signal: Port for Port in Second.SelectedBoundaryPorts
    }

    assert SecondPorts["Beta"].ApertureContractFingerprint == (
        FirstPorts["Beta"].ApertureContractFingerprint
    )
    assert SecondPorts["Alpha"].ApertureContractFingerprint != (
        FirstPorts["Alpha"].ApertureContractFingerprint
    )


def test_interface_csp_indexes_dense_binary_clauses_and_preserves_higher_order():
    DomainSize = 36
    Alpha = tuple(
        _Boundary("Alpha", 10 * Index, Fingerprint=f"a{Index}")
        for Index in range(DomainSize)
    )
    Beta = tuple(
        _Boundary("Beta", 1_000 + 10 * Index, Fingerprint=f"b{Index}")
        for Index in range(DomainSize)
    )
    Gamma = (
        _Boundary("Gamma", 2_000, Fingerprint="g0"),
        _Boundary("Gamma", 2_010, Fingerprint="g1"),
    )
    Guide = BuildComponentCapacityGuide(
        _Preparation({
            "Alpha": Alpha,
            "Beta": Beta,
            "Gamma": Gamma,
        }),
        TrackPitch=3,
    )
    AllowedPair = (Alpha[-1], Beta[-1])
    Clauses = {
        frozenset((
            ("Alpha", AlphaPort.ApertureContractFingerprint),
            ("Beta", BetaPort.ApertureContractFingerprint),
        ))
        for AlphaPort in Alpha
        for BetaPort in Beta
        if (AlphaPort, BetaPort) != AllowedPair
    }
    Clauses.add(frozenset((
        ("Alpha", Alpha[-1].ApertureContractFingerprint),
        ("Beta", Beta[-1].ApertureContractFingerprint),
        ("Gamma", Gamma[0].ApertureContractFingerprint),
    )))

    Result = SolveComponentInterfaceCsp(
        Guide,
        RejectedClauses=Clauses,
    )

    assert Result.Status == ComponentPlanningStatus.Feasible
    assert Result.Contract is not None
    assert {
        Port.Signal: Port.ApertureContractFingerprint
        for Port in Result.Contract.SelectedBoundaryPorts
    } == {
        "Alpha": Alpha[-1].ApertureContractFingerprint,
        "Beta": Beta[-1].ApertureContractFingerprint,
        "Gamma": Gamma[1].ApertureContractFingerprint,
    }
    assert Result.Diagnostics["BinaryLearnedClauseCount"] == (
        DomainSize * DomainSize - 1
    )
    assert Result.Diagnostics["HigherOrderLearnedClauseCount"] == 1
    assert Result.Diagnostics["HigherOrderClauseSubsetCheckCount"] > 0
    # Binary membership work is proportional to the option-pair probes, not
    # the Cartesian product multiplied by every learned clause.
    assert Result.Diagnostics["ClauseIndexLookupCount"] < 100_000


def test_interface_csp_rejects_coarse_overflow_instead_of_returning_contract():
    Guide = BuildComponentCapacityGuide(
        _Preparation(
            {
                "Alpha": (_Boundary("Alpha", 0),),
                "Beta": (_Boundary("Beta", 1),),
            },
            Capacity=1,
        ),
        TrackPitch=3,
    )

    Result = SolveComponentInterfaceCsp(Guide)

    assert Result.Status == ComponentPlanningStatus.InterfaceUnsatisfiable
    assert Result.InterfaceProofComplete
    assert Result.Contract is None


def test_interface_csp_reports_search_incomplete_separately():
    Guide = BuildComponentCapacityGuide(
        _Preparation({"Alpha": (_Boundary("Alpha", 0),)}),
        TrackPitch=3,
    )

    Result = SolveComponentInterfaceCsp(Guide, MaximumExpansions=0)

    assert Result.Status == ComponentPlanningStatus.SearchIncomplete
    assert not Result.InterfaceProofComplete
    assert "work limit" in Result.Detail


def test_incremental_binary_cache_adds_newly_relevant_signal_pair(monkeypatch):
    # Legacy assignment-fingerprint rejections are retained solely for the
    # Python fixture oracle; production uses exact native lease no-goods.
    monkeypatch.setenv("RC_COMPONENT_LEASE_SOLVER", "python")
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    Beta0 = _Boundary("Beta", 100, Fingerprint="b0")
    Beta1 = _Boundary("Beta", 112, Fingerprint="b1")
    Guide = BuildComponentCapacityGuide(
        _Preparation({
            "Alpha": (Alpha0, Alpha1),
            "Beta": (Beta0, Beta1),
        }),
        TrackPitch=3,
    )
    Cache = {}
    Initial = SolveComponentInterfaceCsp(
        Guide,
        PairSupportMaskCache=Cache,
    )
    assert Initial.Feasible

    Learned = SolveComponentInterfaceCsp(
        Guide,
        RejectedClauses=(frozenset((
            ("Alpha", Alpha0.ApertureContractFingerprint),
            ("Beta", Beta0.ApertureContractFingerprint),
        )),),
        RejectedAssignmentFingerprints=(
            Initial.Contract.AssignmentFingerprint,
        ),
        PairSupportMaskCache=Cache,
    )

    assert Learned.Feasible
    assert Learned.Contract is not None
    Selected = {
        Port.Signal: Port.ApertureContractFingerprint
        for Port in Learned.Contract.SelectedBoundaryPorts
    }
    assert Selected != {
        "Alpha": Alpha0.ApertureContractFingerprint,
        "Beta": Beta0.ApertureContractFingerprint,
    }
def test_contract_iterator_reads_live_rejected_aperture_contracts():
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    RejectedBySignal = {"Alpha": set()}
    Contracts = iter(IterClosedComponentContracts(
        _Preparation({"Alpha": (Alpha0, Alpha1)}),
        TrackPitch=3,
        RejectedApertureContractFingerprintsBySignal=(
            RejectedBySignal
        ),
    ))

    First = next(Contracts)
    assert First.SelectedBoundaryPorts == (Alpha0,)
    RejectedBySignal["Alpha"].add(
        Alpha1.ApertureContractFingerprint
    )

    assert next(Contracts, None) is None


def test_contract_iterator_preserves_monotone_frontier_across_live_clauses():
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    Beta0 = _Boundary("Beta", 100, Fingerprint="b0")
    Beta1 = _Boundary("Beta", 112, Fingerprint="b1")
    RejectedClauses = set()
    Contracts = iter(IterClosedComponentContracts(
        _Preparation({
            "Alpha": (Alpha0, Alpha1),
            "Beta": (Beta0, Beta1),
        }),
        TrackPitch=3,
        RejectedClauses=RejectedClauses,
    ))

    First = next(Contracts)
    FirstKeys = frozenset(
        (
            Port.Signal,
            Port.ApertureContractFingerprint,
        )
        for Port in First.SelectedBoundaryPorts
    )
    RejectedClauses.add(FirstKeys)

    Second = next(Contracts)
    assert Second.AssignmentFingerprint != First.AssignmentFingerprint
    SecondBySignal = {
        Port.Signal: Port for Port in Second.SelectedBoundaryPorts
    }
    RejectedClauses.add(frozenset(((
        "Alpha",
        SecondBySignal["Alpha"].ApertureContractFingerprint,
    ),)))

    Third = next(Contracts)
    assert len({
        First.AssignmentFingerprint,
        Second.AssignmentFingerprint,
        Third.AssignmentFingerprint,
    }) == 3
    RejectedClauses.update((
        frozenset((("Alpha", Alpha0.ApertureContractFingerprint),)),
        frozenset((("Alpha", Alpha1.ApertureContractFingerprint),)),
    ))
    assert next(Contracts, None) is None


def test_contract_iterator_clause_order_is_stable():
    Alpha0 = _Boundary("Alpha", 0, Fingerprint="a0")
    Alpha1 = _Boundary("Alpha", 12, Fingerprint="a1")
    Beta0 = _Boundary("Beta", 100, Fingerprint="b0")
    Beta1 = _Boundary("Beta", 112, Fingerprint="b1")
    Preparation = _Preparation({
        "Alpha": (Alpha0, Alpha1),
        "Beta": (Beta0, Beta1),
    })
    Clauses = (
        frozenset((("Alpha", Alpha0.ApertureContractFingerprint),)),
        frozenset(((
            "Alpha",
            Alpha1.ApertureContractFingerprint,
        ), (
            "Beta",
            Beta0.ApertureContractFingerprint,
        ))),
    )

    def Sequence(Values):
        return tuple(
            Contract.AssignmentFingerprint
            for Contract in IterClosedComponentContracts(
                Preparation,
                TrackPitch=3,
                RejectedClauses=Values,
            )
        )

    assert Sequence(Clauses) == Sequence(tuple(reversed(Clauses)))


def test_one_ineligible_placement_does_not_classify_the_design_impossible():
    Preparation = _Preparation({"Alpha": (_Boundary("Alpha", 0),)})
    Preparation.Problem.Interface.Complete = False

    Result = PlanClosedComponent(Preparation, TrackPitch=3)

    assert Result.Status == ComponentPlanningStatus.SearchIncomplete
    assert not Result.PlacementProofComplete
    assert Result.Contract is None
    assert "placement" in Result.Detail
    assert "design" not in Result.Detail

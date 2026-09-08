"""Behavior tests for bounded work and one real symbolic producer."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from time import monotonic
from types import SimpleNamespace

import pytest

from PhysicalDesign.Contracts.Runtime import (
    RuntimeCancellationSnapshot,
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeFreshness,
    RuntimeLifecycle,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
    RuntimeWorkAuthority,
    RuntimeWorkProduct,
    RuntimeWorkRequest,
    RuntimeWorkResult,
    RuntimeWorkScope,
)
from PhysicalDesign.Contracts.Component import ClosedComponentInterface
import PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains as SymbolicDomains
import PhysicalDesign.Routing.Regions.Symbolic.SymbolicWorkers as SymbolicWorkers
from PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains import (
    CompilePhysicalComponentSymbolicPortPairDomain,
    CompilePhysicalComponentSymbolicUnaryApertureDomain,
)
from PhysicalDesign.Routing.Regions.Boundaries.Fabric import (
    BuildComponentRoutingFabric,
)
from PhysicalDesign.Routing.Regions.Symbolic.SymbolicState import (
    PrepareComponentSymbolicNetStateContext,
)
from PhysicalDesign.Routing.Regions.Symbolic.SymbolicWorkers import (
    CompilePreparedComponentSymbolicNetStatesBounded,
)
from PhysicalDesign.Runtime.BoundedWork import ExecuteBoundedRuntimeWork
from PhysicalDesign.Runtime.SpawnedWork import RuntimeSpawnedWorkLimits
import PhysicalDesign.Runtime.Reliability as Reliability
from PhysicalDesign.Runtime.Reliability import RoutingDeadline
from Tests.PhysicalDesign.Routing.test_component_router import (
    _Candidate,
    _Channel,
    _Domain,
    _Problem,
)
from Tests.PhysicalDesign.Routing.test_component_symbolic_higher_order_domain import (
    _Fixture as _PortPairFixture,
)


def _Deadline(Seconds: float = 5.0) -> RoutingDeadline:
    StartedAt = monotonic()
    return RoutingDeadline(
        StartedAt=StartedAt,
        ExpiresAt=StartedAt + Seconds,
        ExpirationKind="StageReserveExpired",
    )


def _Request(
    Deadline: RoutingDeadline,
    *,
    WorkCap: int = 10,
    CancellationRequested: bool = False,
) -> RuntimeWorkRequest:
    return RuntimeWorkRequest(
        TaskIdentity="symbolic-alpha",
        Operation="prepare-symbolic-net-state",
        Scope=RuntimeWorkScope(
            DomainIdentity="symbolic-alpha-domain",
            DependencyIdentities=("problem-alpha", "technology-default"),
        ),
        Lifecycle=(
            RuntimeLifecycle.CancellationRequested
            if CancellationRequested
            else RuntimeLifecycle.Queued
        ),
        Freshness=RuntimeFreshness.Current,
        DeadlineAt=Deadline.ExpiresAt,
        WorkCap=WorkCap,
        Cancellation=RuntimeCancellationSnapshot(
            Requested=CancellationRequested,
            Identity="cancel-alpha",
            Reason="test-request" if CancellationRequested else "",
        ),
    )


def _Product(Value: object = "candidate") -> RuntimeWorkProduct:
    return RuntimeWorkProduct(
        Value=Value,
        SearchOutcome=RuntimeSearchOutcome.Prepared,
        ClaimStrength=RuntimeClaimStrength.Complete,
        TerminalReason=RuntimeTerminalReason.Prepared,
        CandidateIdentity="candidate-alpha",
    )


def _SymbolicInputs():
    Problem = _Problem(External=(("Alpha", (3, 7, 0), "target"),))
    return Problem, PrepareComponentSymbolicNetStateContext(Problem, "Alpha")


def _MutableSymbolicState(Context, NetStateCache):
    return deepcopy({
        "FabricParentCache": Context.FabricParentCache,
        "RouteClaimsConstructionCache": Context.RouteClaimsConstructionCache,
        "TerminalFrontierCache": Context.TerminalFrontierCache,
        "TerminalFrontierBuildCount": Context.TerminalFrontierBuildCount,
        "TerminalFrontierCacheHitCount": Context.TerminalFrontierCacheHitCount,
        "TreeRepeaterSubproblemCache": Context.TreeRepeaterSubproblemCache,
        "TreeRepeaterCacheStatistics": Context.TreeRepeaterCacheStatistics,
        "NetStateCache": NetStateCache,
    })


def _IndependentFingerprint(Value: object) -> str:
    Encoded = json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(Encoded).hexdigest()[:16]


def _RealUnaryInputs(monkeypatch):
    FixtureProblem, FactorDomain, Signals = _PortPairFixture(monkeypatch)
    monkeypatch.undo()
    Lanes = tuple(
        tuple((X, 7, Z) for X in range(3))
        for Z in (0, 4, 8)
    )
    Fabric = BuildComponentRoutingFabric(_Channel(*Lanes))
    OwnedDomains = tuple(
        Domain
        for Signal, Z in zip(Signals, (0, 4, 8))
        for Domain in (
            _Domain(
                Signal,
                (0, 7, Z),
                "source",
                _Candidate(((0, 7, Z),)),
            ),
            _Domain(
                Signal,
                (2, 7, Z),
                "target",
                _Candidate(((2, 7, Z),)),
            ),
        )
    )
    Interface = ClosedComponentInterface(
        InterfaceFingerprint=FixtureProblem.Interface.InterfaceFingerprint,
        ComponentId=0,
        OwnedSignals=Signals,
        Ports=(),
        PhysicalPortReservations=(
            FixtureProblem.Interface.PhysicalPortReservations
        ),
    )
    Problem = replace(
        _Problem(
            "Gamma",
            Fabric=Fabric,
            External=(("Gamma", (3, 7, 8), "target"),),
        ),
        ProblemFingerprint="real-unary-problem",
        PlacementFingerprint="placement",
        ComponentSignals=Signals,
        OwnedTerminalDomains=OwnedDomains,
        Interface=Interface,
        MaximumWork=100_000,
    )
    FactorDomain.Problem = Problem
    PortsBySignal = {
        Port.Signal: Port
        for Port in FixtureProblem.Interface.PhysicalPortReservations
    }
    for Signal, Apertures in FactorDomain.ApertureFactorsBySignal:
        for Aperture in Apertures:
            Aperture.GlobalPath = PortsBySignal[Signal].GlobalPath
            Aperture.Attachment = PortsBySignal[Signal].Attachment
    return Problem, FactorDomain, Signals


def _ExpandingUnaryInputs(monkeypatch, MaximumWork):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    Problem = replace(
        Problem,
        MaximumWork=MaximumWork,
        Interface=replace(
            Problem.Interface,
            PhysicalPortReservations=tuple(
                replace(Port, OwnedCandidateFingerprints=())
                for Port in Problem.Interface.PhysicalPortReservations
            ),
        ),
    )
    FactorDomain.Problem = Problem
    FactorDomain.LocalAccessFactorsBySignal = tuple(
        (
            Signal,
            tuple(
                SimpleNamespace(
                    **{
                        **vars(Factor),
                        "OwnedCandidateFingerprints": (),
                    }
                )
                for Factor in Factors
            ),
        )
        for Signal, Factors in FactorDomain.LocalAccessFactorsBySignal
    )
    return Problem, FactorDomain, Signals


def _UnaryLimits(
    *,
    Queued: int,
    InFlight: int,
    PayloadBytes: int = 64 * 1024 * 1024,
    ResultBytes: int = 64 * 1024 * 1024,
) -> RuntimeSpawnedWorkLimits:
    return RuntimeSpawnedWorkLimits(
        MaximumQueuedTasks=Queued,
        MaximumInFlightTasks=InFlight,
        MaximumPayloadBytes=PayloadBytes,
        MaximumResultBytes=ResultBytes,
    )


def _RuntimeAuthority(Request: RuntimeWorkRequest) -> RuntimeWorkAuthority:
    return RuntimeWorkAuthority(
        WorkDeadlineAt=Request.DeadlineAt,
        CleanupCutoffAt=Request.DeadlineAt + 1.0,
        ForceTerminationAuthorized=True,
    )


def test_real_unary_without_authority_factory_stays_serial(monkeypatch):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    Spawned = False

    def RejectSpawn(*_Arguments, **_KeywordArguments):
        nonlocal Spawned
        Spawned = True
        raise AssertionError(
            "missing RuntimeAuthorityFactory must not invent cleanup policy"
        )

    monkeypatch.setattr(
        SymbolicDomains,
        "ExecuteBoundedSpawnedWorkBatch",
        RejectSpawn,
    )
    Clauses, Diagnostics = CompilePhysicalComponentSymbolicUnaryApertureDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=30.0,
        NetStateCache={},
        RuntimeLimits=_UnaryLimits(Queued=0, InFlight=3),
    )

    assert Spawned is False
    assert Clauses
    assert Diagnostics["Complete"] is True
    assert "UnarySignalSubmittedTaskCount" not in Diagnostics


def test_real_unary_spawned_workers_match_serial_and_worker_count_controls(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    SerialCache = {}
    SerialExpansionObservations = []
    OneWorkerCache = {}
    MultipleWorkerCache = {}

    SerialClauses, SerialDiagnostics = (
        CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=30.0,
            WorkCheck=SerialExpansionObservations.append,
            NetStateCache=SerialCache,
            AllowParallelSignalCompilation=False,
        )
    )
    OneWorkerClauses, OneWorkerDiagnostics = (
        CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=30.0,
            NetStateCache=OneWorkerCache,
            RuntimeLimits=_UnaryLimits(Queued=2, InFlight=1),
            RuntimeAuthorityFactory=_RuntimeAuthority,
        )
    )
    MultipleWorkerClauses, MultipleWorkerDiagnostics = (
        CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=30.0,
            NetStateCache=MultipleWorkerCache,
            RuntimeLimits=_UnaryLimits(Queued=0, InFlight=3),
            RuntimeAuthorityFactory=_RuntimeAuthority,
        )
    )

    ObservableNames = (
        "Complete",
        "SignalCount",
        "CompiledAccessCount",
        "UnsupportedLocalAccessCount",
        "UnsupportedApertureOptionCount",
        "UnsupportedLocalApertureSupportCount",
        "UnaryLocalAccessClauseCount",
        "UnarySeamClauseCount",
        "UnaryApertureClauseCount",
        "UnarySignalWorkUnits",
        "DomainFingerprint",
    )

    def Observable(Diagnostics):
        return {
            Name: Diagnostics[Name]
            for Name in ObservableNames
        }

    # The fixture declares one unsupported local contract, seam, support, and
    # aperture contract per signal.  This finite relation is independent of
    # the compiler's projected clause representation.
    ExpectedClauses = frozenset(
        frozenset(((Signal, Contract),))
        for Signal in Signals
        for Contract in (
            f"local-contract-{Signal}",
            f"seam-{Signal}",
            f"support-{Signal}",
            f"aperture-contract-{Signal}",
        )
    )
    assert SerialClauses == ExpectedClauses
    assert SerialClauses == OneWorkerClauses == MultipleWorkerClauses
    assert Observable(SerialDiagnostics) == Observable(OneWorkerDiagnostics)
    assert Observable(SerialDiagnostics) == Observable(MultipleWorkerDiagnostics)
    assert SerialCache == OneWorkerCache == MultipleWorkerCache
    assert SerialDiagnostics["UnarySignalWorkUnits"] == len(
        SerialExpansionObservations
    )
    assert OneWorkerDiagnostics["UnarySignalPeakInFlightTaskCount"] == 1
    assert OneWorkerDiagnostics["UnarySignalPeakQueuedTaskCount"] == 2
    assert OneWorkerDiagnostics["UnarySignalAdmittedTaskCount"] == 3
    assert OneWorkerDiagnostics["UnarySignalSubmittedTaskCount"] == 3
    assert 0 < OneWorkerDiagnostics["UnarySignalMaximumObservedPayloadBytes"]
    assert OneWorkerDiagnostics["UnarySignalMaximumObservedPayloadBytes"] <= (
        64 * 1024 * 1024
    )
    assert 0 < OneWorkerDiagnostics["UnarySignalMaximumObservedResultBytes"]
    assert OneWorkerDiagnostics["UnarySignalMaximumObservedResultBytes"] <= (
        64 * 1024 * 1024
    )
    assert MultipleWorkerDiagnostics["UnarySignalPeakInFlightTaskCount"] == 3
    assert MultipleWorkerDiagnostics["UnarySignalPeakQueuedTaskCount"] == 0
    assert MultipleWorkerDiagnostics["UnarySignalAdmittedTaskCount"] == 3
    assert MultipleWorkerDiagnostics["UnarySignalSubmittedTaskCount"] == 3


def test_real_unary_cap_one_is_pre_admitted_in_serial_and_spawned_modes(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _ExpandingUnaryInputs(monkeypatch, 1)
    SerialRouteClaimsCache = {}
    SerialNetStateCache = {}
    SerialClauseCache = {}
    SerialExpansionObservations = []

    SerialClauses, SerialDiagnostics = (
        CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=30.0,
            WorkCheck=SerialExpansionObservations.append,
            NetStateCache=SerialNetStateCache,
            CompletedClauseCache=SerialClauseCache,
            RouteClaimsConstructionCache=SerialRouteClaimsCache,
            AllowParallelSignalCompilation=False,
        )
    )
    Captured = {}
    OriginalBatch = SymbolicDomains.ExecuteBoundedSpawnedWorkBatch

    def CaptureBatch(*Arguments, **KeywordArguments):
        Captured["Items"] = Arguments[0]
        Batch = OriginalBatch(*Arguments, **KeywordArguments)
        Captured["Batch"] = Batch
        return Batch

    monkeypatch.setattr(
        SymbolicDomains,
        "ExecuteBoundedSpawnedWorkBatch",
        CaptureBatch,
    )
    SpawnedClauses, SpawnedDiagnostics = (
        CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=30.0,
            NetStateCache={},
            CompletedClauseCache={},
            RouteClaimsConstructionCache={},
            RuntimeLimits=_UnaryLimits(Queued=0, InFlight=3),
            RuntimeAuthorityFactory=_RuntimeAuthority,
        )
    )

    SpawnedResults = tuple(
        Execution.Result
        for _TaskIdentity, Execution in Captured["Batch"].Executions
    )
    assert len(SerialExpansionObservations) == 1
    assert SerialExpansionObservations[0]["ExpansionCount"] == 1
    assert SerialDiagnostics["UnarySignalWorkUnits"] == 1
    assert SerialClauses == frozenset()
    assert SerialDiagnostics["Complete"] is False
    assert SerialRouteClaimsCache == {}
    assert SerialNetStateCache == {}
    assert SerialClauseCache == {}
    assert SpawnedClauses == frozenset()
    assert SpawnedDiagnostics["Complete"] is False
    assert sum(Result.WorkUnits for Result in SpawnedResults) == 1
    assert all(
        Result.WorkUnits <= Item.Request.WorkCap
        for Result, Item in zip(SpawnedResults, Captured["Items"])
    )
    assert Captured["Batch"].SubmittedTaskCount == 1
    assert all(Result.ProofIdentity is None for Result in SpawnedResults)


@pytest.mark.parametrize(
    "MaximumWork, ExpectedComplete",
    ((8, False), (9, True)),
)
def test_real_unary_exact_completion_boundary_is_transactional_in_both_modes(
    monkeypatch,
    MaximumWork,
    ExpectedComplete,
):
    Problem, FactorDomain, Signals = _ExpandingUnaryInputs(
        monkeypatch,
        MaximumWork,
    )
    SerialRouteClaimsCache = {}
    SerialNetStateCache = {}
    SerialClauseCache = {}
    SerialExpansionObservations = []
    SerialClauses, SerialDiagnostics = (
        CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=30.0,
            WorkCheck=SerialExpansionObservations.append,
            NetStateCache=SerialNetStateCache,
            CompletedClauseCache=SerialClauseCache,
            RouteClaimsConstructionCache=SerialRouteClaimsCache,
            AllowParallelSignalCompilation=False,
        )
    )
    Captured = {}
    OriginalBatch = SymbolicDomains.ExecuteBoundedSpawnedWorkBatch

    def CaptureBatch(*Arguments, **KeywordArguments):
        Captured["Items"] = Arguments[0]
        Batch = OriginalBatch(*Arguments, **KeywordArguments)
        Captured["Batch"] = Batch
        return Batch

    monkeypatch.setattr(
        SymbolicDomains,
        "ExecuteBoundedSpawnedWorkBatch",
        CaptureBatch,
    )
    SpawnedClauses, SpawnedDiagnostics = (
        CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=30.0,
            NetStateCache={},
            CompletedClauseCache={},
            RouteClaimsConstructionCache={},
            RuntimeLimits=_UnaryLimits(Queued=0, InFlight=3),
            RuntimeAuthorityFactory=_RuntimeAuthority,
        )
    )
    SpawnedResults = tuple(
        Execution.Result
        for _TaskIdentity, Execution in Captured["Batch"].Executions
    )

    assert len(SerialExpansionObservations) == MaximumWork
    assert SerialDiagnostics["UnarySignalWorkUnits"] == MaximumWork
    assert sum(Result.WorkUnits for Result in SpawnedResults) == MaximumWork
    assert all(
        Result.WorkUnits <= Item.Request.WorkCap
        for Result, Item in zip(SpawnedResults, Captured["Items"])
    )
    assert SerialDiagnostics["Complete"] is ExpectedComplete
    assert SpawnedDiagnostics["Complete"] is ExpectedComplete
    if ExpectedComplete:
        assert SerialClauses == SpawnedClauses
        assert SerialRouteClaimsCache
        assert SerialNetStateCache
        assert SerialClauseCache
    else:
        assert SerialClauses == SpawnedClauses == frozenset()
        assert SerialRouteClaimsCache == {}
        assert SerialNetStateCache == {}
        assert SerialClauseCache == {}
        assert all(Result.ProofIdentity is None for Result in SpawnedResults)


def test_real_unary_spawned_path_preserves_paired_wait_telemetry(monkeypatch):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    Events = []

    def Record(Kind, **Fields):
        Events.append((Kind, Fields))

    monkeypatch.setattr(SymbolicDomains, "EmitTelemetryEvent", Record)
    Clauses, Diagnostics = CompilePhysicalComponentSymbolicUnaryApertureDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=30.0,
        NetStateCache={},
        RuntimeLimits=_UnaryLimits(Queued=2, InFlight=1),
        RuntimeAuthorityFactory=_RuntimeAuthority,
    )
    WaitActionsByTask = {}
    for Kind, Fields in Events:
        if Kind == "wait":
            WaitActionsByTask.setdefault(Fields["Task"], []).append(
                Fields["Action"]
            )

    assert Clauses
    assert Diagnostics["Complete"] is True
    assert len(WaitActionsByTask) == len(Signals)
    assert all(Actions == ["begin", "end"] for Actions in WaitActionsByTask.values())


def test_real_unary_capacity_plus_one_rejects_without_cache_publication(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    NetStateCache = {}
    CompletedClauseCache = {}

    Clauses, Diagnostics = CompilePhysicalComponentSymbolicUnaryApertureDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=30.0,
        NetStateCache=NetStateCache,
        CompletedClauseCache=CompletedClauseCache,
        RuntimeLimits=_UnaryLimits(Queued=1, InFlight=1),
        RuntimeAuthorityFactory=_RuntimeAuthority,
    )

    Result = RuntimeWorkResult.FromDictionary(
        Diagnostics["UnarySignalRuntimeWorkResult"]
    )
    assert Clauses == frozenset()
    assert Diagnostics["Complete"] is False
    assert Diagnostics["UnarySignalAdmissionRejectedTaskCount"] == 1
    assert Diagnostics["UnarySignalSubmittedTaskCount"] == 2
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.AdmissionRejected
    assert Result.CommitEligibility is RuntimeCommitEligibility.Ineligible
    assert Result.ProofIdentity is None
    assert CompletedClauseCache == {}
    assert NetStateCache == {}


def test_real_unary_payload_and_result_limits_do_not_publish_caches(monkeypatch):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)

    for Limits, ExpectedReason in (
        (
            _UnaryLimits(Queued=0, InFlight=3, PayloadBytes=1),
            RuntimeTerminalReason.PayloadLimitExceeded,
        ),
        (
            _UnaryLimits(Queued=0, InFlight=3, ResultBytes=1024),
            RuntimeTerminalReason.ResultLimitExceeded,
        ),
    ):
        NetStateCache = {}
        CompletedClauseCache = {}
        Clauses, Diagnostics = (
            CompilePhysicalComponentSymbolicUnaryApertureDomain(
                Problem,
                FactorDomain,
                Signals,
                DeadlineSeconds=30.0,
                NetStateCache=NetStateCache,
                CompletedClauseCache=CompletedClauseCache,
                RuntimeLimits=Limits,
                RuntimeAuthorityFactory=_RuntimeAuthority,
            )
        )
        Result = RuntimeWorkResult.FromDictionary(
            Diagnostics["UnarySignalRuntimeWorkResult"]
        )

        assert Clauses == frozenset()
        assert Diagnostics["Complete"] is False
        assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
        assert Result.TerminalReason is ExpectedReason
        assert Result.ProofIdentity is None
        assert NetStateCache == {}
        assert CompletedClauseCache == {}


def test_real_unary_expired_deadline_is_rejected_before_spawn(monkeypatch):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)

    Clauses, Diagnostics = CompilePhysicalComponentSymbolicUnaryApertureDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=0.0,
        NetStateCache={},
        RuntimeLimits=_UnaryLimits(Queued=0, InFlight=3),
        RuntimeAuthorityFactory=_RuntimeAuthority,
    )

    Result = RuntimeWorkResult.FromDictionary(
        Diagnostics["UnarySignalRuntimeWorkResult"]
    )
    assert Clauses == frozenset()
    assert Diagnostics["Complete"] is False
    assert Diagnostics["UnarySignalAdmittedTaskCount"] == 0
    assert Diagnostics["UnarySignalSubmittedTaskCount"] == 0
    assert Result.TerminalReason is RuntimeTerminalReason.DeadlineExhausted
    assert Result.ProofIdentity is None


def test_real_unary_spawned_worker_failure_is_unresolved(monkeypatch):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    FirstSignal, FirstFactors = FactorDomain.LocalAccessFactorsBySignal[0]
    BrokenFactor = SimpleNamespace(
        LocalAccessFingerprint=FirstFactors[0].LocalAccessFingerprint,
    )
    FactorDomain.LocalAccessFactorsBySignal = (
        (FirstSignal, (BrokenFactor,)),
        *FactorDomain.LocalAccessFactorsBySignal[1:],
    )
    NetStateCache = {}
    CompletedClauseCache = {}

    Clauses, Diagnostics = CompilePhysicalComponentSymbolicUnaryApertureDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=30.0,
        NetStateCache=NetStateCache,
        CompletedClauseCache=CompletedClauseCache,
        RuntimeLimits=_UnaryLimits(Queued=0, InFlight=3),
        RuntimeAuthorityFactory=_RuntimeAuthority,
    )

    Result = RuntimeWorkResult.FromDictionary(
        Diagnostics["UnarySignalRuntimeWorkResult"]
    )
    assert Clauses == frozenset()
    assert Diagnostics["Complete"] is False
    assert Result.Lifecycle is RuntimeLifecycle.Failed
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.WorkerFailure
    assert Result.ProofIdentity is None
    assert NetStateCache == {}
    assert CompletedClauseCache == {}


def test_real_symbolic_preparation_returns_typed_ineligible_result():
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline()

    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline, WorkCap=100_000),
        Deadline=Deadline,
        SymbolicNetStateCache={},
    )

    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Prepared
    assert Execution.Result.Lifecycle is RuntimeLifecycle.Completed
    assert Execution.Result.ClaimStrength is RuntimeClaimStrength.Complete
    assert Execution.Result.CommitEligibility is RuntimeCommitEligibility.Ineligible
    assert Execution.Result.CandidateIdentity == Execution.Value.CacheKey
    assert Execution.Value.Complete is True
    assert Execution.Value.States is not None


def test_incomplete_symbolic_preparation_stays_unresolved():
    Deadline = _Deadline()

    Execution = ExecuteBoundedRuntimeWork(
        _Request(Deadline),
        Deadline,
        lambda _Control: RuntimeWorkProduct(
            Value=None,
            SearchOutcome=RuntimeSearchOutcome.Unresolved,
            ClaimStrength=RuntimeClaimStrength.Continuation,
            TerminalReason=RuntimeTerminalReason.IncompleteExploration,
        ),
    )

    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.IncompleteExploration
    assert Execution.Result.CommitEligibility is RuntimeCommitEligibility.Ineligible
    assert Execution.Result.ProofIdentity is None


def test_actual_expired_deadline_overrides_a_populated_candidate():
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline(-0.001)
    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline),
        Deadline=Deadline,
        SymbolicNetStateCache={},
    )

    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.DeadlineExhausted
    assert Execution.Result.CommitEligibility is RuntimeCommitEligibility.Ineligible


def test_work_cap_exhaustion_is_unresolved_not_infeasible():
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline()
    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline, WorkCap=1),
        Deadline=Deadline,
        SymbolicNetStateCache={},
    )

    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.WorkCapExhausted
    assert Execution.Result.WorkUnits == 1
    assert Execution.Result.ProofIdentity is None


def test_cap_one_expansion_observer_matches_work_units_without_cache_publication():
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline()
    NetStateCache = {}
    Before = _MutableSymbolicState(Context, NetStateCache)
    ExpansionObservations = []

    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline, WorkCap=1),
        Deadline=Deadline,
        WorkCheck=ExpansionObservations.append,
        SymbolicNetStateCache=NetStateCache,
    )

    assert [
        Observation["ExpansionCount"]
        for Observation in ExpansionObservations
    ] == [1]
    assert Execution.Result.WorkUnits == 1
    assert Execution.Result.WorkUnits <= 1
    assert Execution.Result.TerminalReason is (
        RuntimeTerminalReason.WorkCapExhausted
    )
    assert Execution.Value is None
    assert Execution.Result.ProofIdentity is None
    assert _MutableSymbolicState(Context, NetStateCache) == Before


def test_late_complete_symbolic_work_does_not_commit_mutable_caches(monkeypatch):
    Problem, Context = _SymbolicInputs()
    Clock = SimpleNamespace(Value=100.0)
    Deadline = RoutingDeadline(
        StartedAt=Clock.Value,
        ExpiresAt=105.0,
        ExpirationKind="StageReserveExpired",
    )
    NetStateCache = {}
    Before = _MutableSymbolicState(Context, NetStateCache)
    OriginalCompiler = SymbolicWorkers.CompilePreparedComponentSymbolicNetStates

    monkeypatch.setattr(Reliability, "monotonic", lambda: Clock.Value)

    def FinishAfterDeadline(*Arguments, **KeywordArguments):
        Compilation = OriginalCompiler(*Arguments, **KeywordArguments)
        Clock.Value = 106.0
        return Compilation

    monkeypatch.setattr(
        SymbolicWorkers,
        "CompilePreparedComponentSymbolicNetStates",
        FinishAfterDeadline,
    )
    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline, WorkCap=100_000),
        Deadline=Deadline,
        SymbolicNetStateCache=NetStateCache,
    )

    assert Execution.Result.TerminalReason is (
        RuntimeTerminalReason.DeadlineExhausted
    )
    assert Execution.Value is None
    assert Execution.Result.ProofIdentity is None
    assert _MutableSymbolicState(Context, NetStateCache) == Before


def test_solver_failure_cannot_publish_any_transactional_cache(monkeypatch):
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline()
    NetStateCache = {}
    Before = _MutableSymbolicState(Context, NetStateCache)

    def MutateThenFail(
        _Problem,
        *,
        PreparedSymbolicNetStateContext,
        SymbolicNetStateCache,
        **_KeywordArguments,
    ):
        PreparedSymbolicNetStateContext.FabricParentCache[(99, 99, 99)] = {}
        PreparedSymbolicNetStateContext.RouteClaimsConstructionCache[
            frozenset()
        ] = "mutated"
        PreparedSymbolicNetStateContext.TerminalFrontierCache[
            frozenset()
        ] = "mutated"
        PreparedSymbolicNetStateContext.TerminalFrontierBuildCount += 1
        PreparedSymbolicNetStateContext.TerminalFrontierCacheHitCount += 1
        PreparedSymbolicNetStateContext.TreeRepeaterSubproblemCache[
            (0, 0, "mutated")
        ] = ()
        PreparedSymbolicNetStateContext.TreeRepeaterCacheStatistics[
            "HitCount"
        ] += 1
        SymbolicNetStateCache["mutated"] = "mutated"
        raise RuntimeError("controlled solver failure after mutation")

    monkeypatch.setattr(
        SymbolicWorkers,
        "SolveComponentRoutingProblemDynamic",
        MutateThenFail,
    )
    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline, WorkCap=10),
        Deadline=Deadline,
        SymbolicNetStateCache=NetStateCache,
    )

    assert Execution.Result.TerminalReason is RuntimeTerminalReason.WorkerFailure
    assert Execution.Value is None
    assert Execution.Result.ProofIdentity is None
    assert _MutableSymbolicState(Context, NetStateCache) == Before


def test_live_cancellation_stops_before_next_expansion_and_rolls_back_caches():
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline()
    NetStateCache = {}
    Before = _MutableSymbolicState(Context, NetStateCache)
    CancellationRequested = False
    ExpansionObservations = []

    def ObserveExpansion(Diagnostics):
        nonlocal CancellationRequested
        ExpansionObservations.append(Diagnostics)
        CancellationRequested = True

    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline, WorkCap=10),
        Deadline=Deadline,
        CancellationCheck=lambda: CancellationRequested,
        WorkCheck=ObserveExpansion,
        SymbolicNetStateCache=NetStateCache,
    )

    assert len(ExpansionObservations) == 1
    assert Execution.Result.WorkUnits == 1
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.Cancelled
    assert Execution.Value is None
    assert Execution.Result.ProofIdentity is None
    assert _MutableSymbolicState(Context, NetStateCache) == Before


def test_zero_work_cap_returns_exhaustion_without_dispatch(monkeypatch):
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline()
    WorkerInvoked = False

    def RejectDispatch(*_Arguments, **_KeywordArguments):
        nonlocal WorkerInvoked
        WorkerInvoked = True
        raise AssertionError("zero-cap work must not be dispatched")

    monkeypatch.setattr(
        SymbolicWorkers,
        "CompilePreparedComponentSymbolicNetStates",
        RejectDispatch,
    )
    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline, WorkCap=0),
        Deadline=Deadline,
        SymbolicNetStateCache={},
    )

    assert WorkerInvoked is False
    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.WorkCapExhausted
    assert Execution.Result.WorkUnits == 0
    assert Execution.Result.ProofIdentity is None


def test_cancellation_requires_worker_acknowledgement_before_termination():
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline()
    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        Problem,
        Request=_Request(Deadline, CancellationRequested=True),
        Deadline=Deadline,
        SymbolicNetStateCache={},
    )

    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.Lifecycle is RuntimeLifecycle.TerminatedGracefully
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.Cancelled
    assert Execution.Result.ProofIdentity is None


def test_live_cancellation_is_acknowledged_at_a_checkpoint():
    Deadline = _Deadline()
    Requested = False

    def Operation(Control):
        nonlocal Requested
        Requested = True
        Control.Checkpoint()
        return _Product()

    Execution = ExecuteBoundedRuntimeWork(
        _Request(Deadline),
        Deadline,
        Operation,
        CancellationCheck=lambda: Requested,
    )

    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.Lifecycle is RuntimeLifecycle.TerminatedGracefully
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.Cancelled


def test_worker_failure_is_unresolved_and_does_not_return_candidate():
    Problem, Context = _SymbolicInputs()
    Deadline = _Deadline()
    MismatchedProblem = replace(Problem, MaximumPowerDistance=14)
    Execution = CompilePreparedComponentSymbolicNetStatesBounded(
        Context,
        MismatchedProblem,
        Request=_Request(Deadline),
        Deadline=Deadline,
        SymbolicNetStateCache={},
    )

    assert Execution.Value is None
    assert Execution.Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Execution.Result.Lifecycle is RuntimeLifecycle.Failed
    assert Execution.Result.TerminalReason is RuntimeTerminalReason.WorkerFailure
    assert Execution.Result.ProofIdentity is None
    assert dict(Execution.Result.Diagnostics)["ExceptionType"] == "ValueError"


def test_completion_is_deterministic_for_equivalent_dependency_order():
    Deadline = _Deadline()
    FirstRequest = _Request(Deadline)
    SecondRequest = replace(
        FirstRequest,
        Scope=RuntimeWorkScope(
            DomainIdentity=FirstRequest.Scope.DomainIdentity,
            DependencyIdentities=tuple(
                reversed(FirstRequest.Scope.DependencyIdentities)
            ),
        ),
    )

    First = ExecuteBoundedRuntimeWork(
        FirstRequest,
        Deadline,
        lambda _Control: _Product(),
    )
    Second = ExecuteBoundedRuntimeWork(
        SecondRequest,
        Deadline,
        lambda _Control: _Product(),
    )

    assert First.Result.ToDictionary() == Second.Result.ToDictionary()


@pytest.mark.parametrize(
    "MaximumWork, ExpectedComplete, ExpectedGrants",
    (
        (1, False, (1, 0)),
        (2, True, (2, 1)),
    ),
)
def test_factor_batch_default_problem_cap_is_shared_and_transactional(
    monkeypatch,
    MaximumWork,
    ExpectedComplete,
    ExpectedGrants,
):
    Problem, Context = _SymbolicInputs()
    FirstProblem = replace(Problem, MaximumWork=MaximumWork)
    SecondProblem = replace(
        FirstProblem,
        ProblemFingerprint=f"{FirstProblem.ProblemFingerprint}:second",
    )
    Problems = {"first": FirstProblem, "second": SecondProblem}
    Context.ImmutableEligibleCandidateFingerprintsByDomain = tuple(
        frozenset(("first", "second"))
        for _Domain in Context.ImmutableEligibleCandidateFingerprintsByDomain
    )
    PortsByProblem = {
        id(FirstProblem): SimpleNamespace(
            OwnedCandidateFingerprints=("first",),
            LocalPath=((1, 1, 1),),
        ),
        id(SecondProblem): SimpleNamespace(
            OwnedCandidateFingerprints=("second",),
            LocalPath=((2, 2, 2),),
        ),
    }
    KeysByProblem = {
        id(FirstProblem): "first-key",
        id(SecondProblem): "second-key",
    }
    NetStateCache = {"sentinel": {"nested": ["original"]}}
    Before = _MutableSymbolicState(Context, NetStateCache)
    Grants = []

    monkeypatch.setattr(
        SymbolicWorkers,
        "_BuildPreparedComponentSymbolicNetStateContextFingerprint",
        lambda *_Arguments, **_KeywordArguments: Context.ContextFingerprint,
    )
    monkeypatch.setattr(
        SymbolicWorkers,
        "_BuildPreparedComponentSymbolicNetStateContextIdentity",
        lambda *_Arguments, **_KeywordArguments: {"Identity": "shared"},
    )
    monkeypatch.setattr(
        SymbolicWorkers,
        "SelectComponentSymbolicPhysicalPort",
        lambda ProblemValue, _Signal: PortsByProblem[id(ProblemValue)],
    )
    monkeypatch.setattr(
        SymbolicWorkers,
        "BuildComponentSymbolicNetStateCacheKey",
        lambda ProblemValue, *_Arguments, **_KeywordArguments: (
            KeysByProblem[id(ProblemValue)]
        ),
    )

    def ControlledSolve(
        ProblemValue,
        *,
        PreparedSymbolicNetStateContext,
        SymbolicNetStateCache,
        MaximumWorkOverride,
        **_KeywordArguments,
    ):
        Grants.append(MaximumWorkOverride)
        PreparedSymbolicNetStateContext.FabricParentCache[
            (len(Grants), 0, 0)
        ] = {"mutated": True}
        PreparedSymbolicNetStateContext.RouteClaimsConstructionCache[
            frozenset(((len(Grants), 0, 0),))
        ] = {"mutated": True}
        Key = KeysByProblem[id(ProblemValue)]
        if MaximumWorkOverride:
            Port = PortsByProblem[id(ProblemValue)]
            SymbolicNetStateCache[Key] = (
                (SimpleNamespace(
                    EgressPath=Port.LocalPath,
                    NetFingerprint=f"state-{len(Grants)}",
                ),),
                {"controlled": True},
            )
            return SimpleNamespace(Status="solved", ExpansionCount=1)
        return SimpleNamespace(Status="incomplete", ExpansionCount=0)

    monkeypatch.setattr(
        SymbolicWorkers,
        "SolveComponentRoutingProblemDynamic",
        ControlledSolve,
    )
    Results = SymbolicWorkers.CompilePreparedComponentPhysicalFactorStateBatch(
        Context,
        Problems,
        SymbolicNetStateCache=NetStateCache,
    )

    assert tuple(Grants) == ExpectedGrants
    assert all(Result.Complete for Result in Results.values()) is ExpectedComplete
    if ExpectedComplete:
        assert "first-key" in NetStateCache
        assert "second-key" in NetStateCache
        assert Context.FabricParentCache
        assert Context.RouteClaimsConstructionCache
    else:
        assert _MutableSymbolicState(Context, NetStateCache) == Before


@pytest.mark.parametrize("Relation", ("port-pair", "higher-order"))
def test_whole_symbolic_relation_rolls_back_earlier_complete_signal(
    monkeypatch,
    Relation,
):
    Problem, FactorDomain, Signals = _PortPairFixture(monkeypatch)
    MaximumWork = 2
    object.__setattr__(Problem, "MaximumWork", MaximumWork)
    FactorDomain.Problem = Problem
    NetStateCache = {"sentinel": {"nested": ["net"]}}
    RouteClaimsCache = {frozenset(): {"nested": ["claims"]}}
    CertificateCache = {"sentinel": {"nested": ["certificate"]}}
    NetSentinel = NetStateCache["sentinel"]
    ClaimsSentinel = RouteClaimsCache[frozenset()]
    CertificateSentinel = CertificateCache["sentinel"]
    BeforeNetStateCache = deepcopy(NetStateCache)
    BeforeRouteClaimsCache = deepcopy(RouteClaimsCache)
    BeforeCertificateCache = deepcopy(CertificateCache)
    Calls = []

    monkeypatch.setattr(
        SymbolicDomains,
        "PrepareComponentSymbolicNetStateContext",
        lambda _Problem, Signal, **KeywordArguments: SimpleNamespace(
            Signal=Signal,
            RouteClaimsConstructionCache=KeywordArguments[
                "RouteClaimsConstructionCache"
            ],
        ),
    )

    def ControlledBatch(
        Context,
        ProblemsByAccess,
        *,
        SymbolicNetStateCache,
        MaximumWork,
        WorkObserver,
        **_KeywordArguments,
    ):
        Calls.append((Context.Signal, MaximumWork))
        Context.RouteClaimsConstructionCache[
            frozenset(((len(Calls), 0, 0),))
        ] = {"partial": len(Calls)}
        SymbolicNetStateCache[f"partial-{len(Calls)}"] = {
            "nested": [len(Calls)]
        }
        Complete = len(Calls) == 1
        WorkObserver(1 if Complete else 0)
        return {
            str(Access): SimpleNamespace(
                CacheKey=f"controlled-{Context.Signal}-{Access}",
                States=(
                    (SimpleNamespace(NetFingerprint=f"state-{Access}"),)
                    if Complete
                    else None
                ),
                Complete=Complete,
                CacheHit=False,
                ExpansionCount=1 if Complete else 0,
                Diagnostics={},
            )
            for Access in ProblemsByAccess
        }

    monkeypatch.setattr(
        SymbolicDomains,
        "CompilePreparedComponentPhysicalFactorStateBatch",
        ControlledBatch,
    )
    monkeypatch.setattr(
        SymbolicDomains,
        "_BuildPhysicalComponentSymbolicNetStateFingerprint",
        lambda _States: "controlled-state-domain",
    )
    if Relation == "port-pair":
        monkeypatch.setattr(
            SymbolicDomains,
            "ValidatePhysicalComponentSymbolicPortPairCertificate",
            lambda *_Arguments, **_KeywordArguments: None,
        )
        Certificate = CompilePhysicalComponentSymbolicPortPairDomain(
            Problem,
            FactorDomain,
            Signals[:2],
            DeadlineSeconds=5.0,
            NetStateCache=NetStateCache,
            CompletedCertificateCache=CertificateCache,
            CompleteCompatibilityIndexCache={},
            RouteClaimsConstructionCache=RouteClaimsCache,
        )
    else:
        monkeypatch.setattr(
            SymbolicDomains,
            "ValidatePhysicalComponentSymbolicHigherOrderCertificate",
            lambda *_Arguments, **_KeywordArguments: None,
        )
        Certificate = SymbolicDomains.CompilePhysicalComponentSymbolicHigherOrderDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=5.0,
            NetStateCache=NetStateCache,
            CompletedCertificateCache=CertificateCache,
            RouteClaimsConstructionCache=RouteClaimsCache,
        )

    assert Certificate.Complete is False
    assert len(Calls) == 2
    assert Calls[0][1] == MaximumWork
    assert Calls[1][1] == MaximumWork - 1
    assert NetStateCache == BeforeNetStateCache
    assert RouteClaimsCache == BeforeRouteClaimsCache
    assert CertificateCache == BeforeCertificateCache
    assert NetStateCache["sentinel"] is NetSentinel
    assert RouteClaimsCache[frozenset()] is ClaimsSentinel
    assert CertificateCache["sentinel"] is CertificateSentinel


def _RunPortPairWithUnchangedBoundedInputs(
    monkeypatch,
    *,
    DeadlineSeconds: float,
    MaximumWork: int,
    FailWorker: bool = False,
    FinishAfterDeadline: bool = False,
):
    FixtureProblem, FactorDomain, Signals = _PortPairFixture(monkeypatch)
    FactorDomain.LocalAccessFactorsBySignal = tuple(
        Value
        for Value in FactorDomain.LocalAccessFactorsBySignal
        if Value[0] != "Gamma"
    )
    Lanes = tuple(
        tuple((X, 7, Z) for X in range(3))
        for Z in (0, 4, 8)
    )
    Fabric = BuildComponentRoutingFabric(_Channel(*Lanes))
    OwnedDomains = tuple(
        Domain
        for Signal, Z in zip(Signals, (0, 4, 8))
        for Domain in (
            _Domain(
                Signal,
                (0, 7, Z),
                "source",
                _Candidate(((0, 7, Z),)),
            ),
            _Domain(
                Signal,
                (2, 7, Z),
                "target",
                _Candidate(((2, 7, Z),)),
            ),
        )
    )
    PairInterface = ClosedComponentInterface(
        InterfaceFingerprint=FixtureProblem.Interface.InterfaceFingerprint,
        ComponentId=0,
        OwnedSignals=Signals,
        Ports=(),
        PhysicalPortReservations=tuple(
            Port
            for Port in FixtureProblem.Interface.PhysicalPortReservations
            if Port.Signal in Signals[:2]
        ),
    )
    Problem = replace(
        _Problem(
            "Gamma",
            Fabric=Fabric,
            External=(("Gamma", (3, 7, 8), "target"),),
        ),
        ProblemFingerprint="problem",
        PlacementFingerprint="placement",
        ComponentSignals=Signals,
        OwnedTerminalDomains=OwnedDomains,
        Interface=PairInterface,
        MaximumWork=MaximumWork,
    )
    FactorDomain.Problem = Problem
    OriginalPrepareContext = PrepareComponentSymbolicNetStateContext
    OriginalBoundedWorker = CompilePreparedComponentSymbolicNetStatesBounded
    OriginalCompiler = SymbolicWorkers.CompilePreparedComponentSymbolicNetStates
    Captured = {}

    def PrepareCallerContext(ProblemValue, Signal, **KeywordArguments):
        if Signal != "Gamma":
            return Signal
        Context = OriginalPrepareContext(
            ProblemValue,
            Signal,
            **KeywordArguments,
        )
        Captured["PreparedContext"] = Context
        Captured["PreparedProblem"] = ProblemValue
        return Context

    monkeypatch.setattr(
        SymbolicDomains,
        "PrepareComponentSymbolicNetStateContext",
        PrepareCallerContext,
    )

    Clock = None
    if FinishAfterDeadline:
        Clock = SimpleNamespace(Value=100.0)

        def ReadClock():
            return Clock.Value

        monkeypatch.setattr(SymbolicDomains, "monotonic", ReadClock)
        monkeypatch.setattr(Reliability, "monotonic", ReadClock)

    def ObserveCompiler(Context, ProblemValue, **KeywordArguments):
        Captured["CompilerContext"] = Context
        Captured["CompilerProblem"] = ProblemValue
        if FailWorker:
            raise RuntimeError("controlled worker failure")
        Compilation = OriginalCompiler(
            Context,
            ProblemValue,
            **KeywordArguments,
        )
        if Clock is not None:
            Clock.Value = 106.0
        return Compilation

    monkeypatch.setattr(
        SymbolicWorkers,
        "CompilePreparedComponentSymbolicNetStates",
        ObserveCompiler,
    )

    def ObserveBoundedWorker(Context, ProblemValue, **KeywordArguments):
        Captured["BoundedContext"] = Context
        Captured["BoundedProblem"] = ProblemValue
        Captured["Request"] = KeywordArguments["Request"]
        Execution = OriginalBoundedWorker(
            Context,
            ProblemValue,
            **KeywordArguments,
        )
        Captured["Execution"] = Execution
        return Execution

    monkeypatch.setattr(
        SymbolicDomains,
        "CompilePreparedComponentSymbolicNetStatesBounded",
        ObserveBoundedWorker,
    )
    Events = []
    NetStateCache = {}
    CompletedCertificateCache = {}
    RouteClaimsConstructionCache = {}

    Certificate = CompilePhysicalComponentSymbolicPortPairDomain(
        Problem,
        FactorDomain,
        Signals[:2],
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=Events.append,
        NetStateCache=NetStateCache,
        CompletedCertificateCache=CompletedCertificateCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
    )
    Captured["NetStateCache"] = NetStateCache
    Captured["CompletedCertificateCache"] = CompletedCertificateCache
    Captured["RouteClaimsConstructionCache"] = RouteClaimsConstructionCache

    RuntimeDocuments = [
        Event["RuntimeWorkResult"]
        for Event in Events
        if Event.get("Stage") == "symbolic-runtime-work-result"
    ]
    assert len(RuntimeDocuments) == 1
    return (
        Certificate,
        RuntimeWorkResult.FromDictionary(RuntimeDocuments[0]),
        Captured,
    )


def test_port_pair_caller_preserves_actual_inputs_through_real_worker(
    monkeypatch,
):
    Certificate, Result, Captured = (
        _RunPortPairWithUnchangedBoundedInputs(
            monkeypatch,
            DeadlineSeconds=5.0,
            MaximumWork=10_000,
        )
    )

    assert Captured["BoundedContext"] is Captured["PreparedContext"]
    assert Captured["BoundedProblem"] is Captured["PreparedProblem"]
    assert Captured["CompilerContext"].ContextFingerprint == (
        Captured["BoundedContext"].ContextFingerprint
    )
    assert Captured["CompilerProblem"] is Captured["BoundedProblem"]
    assert Captured["Execution"].Value.Complete is True
    assert Captured["Execution"].Value.States is not None
    assert Certificate.Complete is True
    assert Result.SearchOutcome is RuntimeSearchOutcome.Prepared
    assert Result.CommitEligibility is RuntimeCommitEligibility.Ineligible
    assert Result.Scope == RuntimeWorkScope(
        DomainIdentity=_IndependentFingerprint((
            "symbolic-port-pair-mandatory-signal-v1",
            "prepared",
            "problem",
            "Gamma",
        )),
        DependencyIdentities=(
            Captured["PreparedContext"].ContextFingerprint,
            "prepared",
            "problem",
        ),
    )
    assert Captured["Request"].Scope == Result.Scope


def _RunControlledPortPairOutcome(
    monkeypatch,
    **KeywordArguments,
):
    Certificate, Result, Captured = (
        _RunPortPairWithUnchangedBoundedInputs(
            monkeypatch,
            **KeywordArguments,
        )
    )
    return Certificate, Result, Captured


def _AssertNoPortPairCachePublication(Captured):
    assert Captured["NetStateCache"] == {}
    assert Captured["CompletedCertificateCache"] == {}
    assert Captured["RouteClaimsConstructionCache"] == {}


def test_port_pair_caller_keeps_actual_deadline_exhaustion_unresolved(
    monkeypatch,
):
    Certificate, Result, Captured = _RunControlledPortPairOutcome(
        monkeypatch,
        DeadlineSeconds=0.0,
        MaximumWork=10_000,
    )

    assert Certificate.Complete is False
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.DeadlineExhausted
    assert Result.ProofIdentity is None
    _AssertNoPortPairCachePublication(Captured)


def test_port_pair_caller_keeps_real_work_exhaustion_unresolved(monkeypatch):
    Certificate, Result, Captured = _RunControlledPortPairOutcome(
        monkeypatch,
        DeadlineSeconds=5.0,
        MaximumWork=1,
    )

    assert Certificate.Complete is False
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.WorkCapExhausted
    assert Result.WorkUnits == 1
    assert Result.ProofIdentity is None
    _AssertNoPortPairCachePublication(Captured)


def test_port_pair_caller_preserves_zero_work_cap_without_dispatch(
    monkeypatch,
):
    Certificate, Result, Captured = _RunControlledPortPairOutcome(
        monkeypatch,
        DeadlineSeconds=5.0,
        MaximumWork=0,
    )

    assert Certificate.Complete is False
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.WorkCapExhausted
    assert Result.WorkUnits == 0
    assert Result.ProofIdentity is None
    _AssertNoPortPairCachePublication(Captured)


def test_port_pair_caller_rejects_deterministically_late_completion(
    monkeypatch,
):
    Certificate, Result, Captured = _RunControlledPortPairOutcome(
        monkeypatch,
        DeadlineSeconds=5.0,
        MaximumWork=10_000,
        FinishAfterDeadline=True,
    )

    assert Certificate.Complete is False
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.DeadlineExhausted
    assert Result.WorkUnits > 0
    assert Result.ProofIdentity is None
    _AssertNoPortPairCachePublication(Captured)


def test_port_pair_caller_keeps_worker_failure_unresolved(monkeypatch):
    Certificate, Result, Captured = _RunControlledPortPairOutcome(
        monkeypatch,
        DeadlineSeconds=5.0,
        MaximumWork=10_000,
        FailWorker=True,
    )

    assert Certificate.Complete is False
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.Lifecycle is RuntimeLifecycle.Failed
    assert Result.TerminalReason is RuntimeTerminalReason.WorkerFailure
    assert Result.ProofIdentity is None
    _AssertNoPortPairCachePublication(Captured)

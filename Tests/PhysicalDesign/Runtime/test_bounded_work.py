"""Behavior tests for bounded work and one real symbolic producer."""

from dataclasses import replace
from hashlib import sha256
import json
from time import monotonic
from types import SimpleNamespace

from PhysicalDesign.Contracts.Runtime import (
    RuntimeCancellationSnapshot,
    RuntimeClaimStrength,
    RuntimeCommitEligibility,
    RuntimeFreshness,
    RuntimeLifecycle,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
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


def test_real_unary_spawned_workers_match_serial_and_worker_count_controls(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _RealUnaryInputs(monkeypatch)
    SerialCache = {}
    OneWorkerCache = {}
    MultipleWorkerCache = {}

    SerialClauses, SerialDiagnostics = (
        CompilePhysicalComponentSymbolicUnaryApertureDomain(
            Problem,
            FactorDomain,
            Signals,
            DeadlineSeconds=30.0,
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
    assert Execution.Result.WorkUnits > 1
    assert Execution.Result.ProofIdentity is None


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

    Certificate = CompilePhysicalComponentSymbolicPortPairDomain(
        Problem,
        FactorDomain,
        Signals[:2],
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=Events.append,
    )

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
    assert Captured["CompilerContext"] is Captured["BoundedContext"]
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
    Certificate, Result, _Captured = (
        _RunPortPairWithUnchangedBoundedInputs(
            monkeypatch,
            **KeywordArguments,
        )
    )
    return Certificate, Result


def test_port_pair_caller_keeps_actual_deadline_exhaustion_unresolved(
    monkeypatch,
):
    Certificate, Result = _RunControlledPortPairOutcome(
        monkeypatch,
        DeadlineSeconds=0.0,
        MaximumWork=10_000,
    )

    assert Certificate.Complete is False
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.DeadlineExhausted
    assert Result.ProofIdentity is None


def test_port_pair_caller_keeps_real_work_exhaustion_unresolved(monkeypatch):
    Certificate, Result = _RunControlledPortPairOutcome(
        monkeypatch,
        DeadlineSeconds=5.0,
        MaximumWork=1,
    )

    assert Certificate.Complete is False
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.WorkCapExhausted
    assert Result.WorkUnits > 1
    assert Result.ProofIdentity is None


def test_port_pair_caller_preserves_zero_work_cap_without_dispatch(
    monkeypatch,
):
    Certificate, Result = _RunControlledPortPairOutcome(
        monkeypatch,
        DeadlineSeconds=5.0,
        MaximumWork=0,
    )

    assert Certificate.Complete is False
    assert Result.SearchOutcome is RuntimeSearchOutcome.Unresolved
    assert Result.TerminalReason is RuntimeTerminalReason.WorkCapExhausted
    assert Result.WorkUnits == 0
    assert Result.ProofIdentity is None


def test_port_pair_caller_rejects_deterministically_late_completion(
    monkeypatch,
):
    Certificate, Result = _RunControlledPortPairOutcome(
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


def test_port_pair_caller_keeps_worker_failure_unresolved(monkeypatch):
    Certificate, Result = _RunControlledPortPairOutcome(
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

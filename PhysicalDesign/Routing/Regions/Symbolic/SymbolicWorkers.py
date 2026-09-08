"""Top-level prepared symbolic-state and physical-factor worker entrypoints."""

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import combinations, islice, product
from math import prod as ProductIntegers
from time import monotonic
from typing import Any, Callable, Iterable, Mapping


from ....Contracts.Component import ClosedComponentInterface, ComponentFeedthroughContract, ComponentForeignTransitDomain, ComponentInterfacePort, ComponentRoutingFabric, ComponentRoutingProblem, ComponentRoutingSolveResult, ComponentTerminalAccessCandidate, ComponentTerminalAccessDomain, RoutedComponentNet, RoutedComponentTemplate
from ....Contracts.Core import Position3
from ....Contracts.Runtime import (
    RuntimeClaimStrength,
    RuntimeSearchOutcome,
    RuntimeTerminalReason,
    RuntimeWorkExecution,
    RuntimeWorkProduct,
    RuntimeWorkRequest,
)
from ....Constraints.PhysicalClaims import _MergeClaims, ComponentClaimsCompatibleForOwners, ComponentClaimsConflict
from ....Resources.ResourceGraph import FindSelfClaimConflicts, LocalRouteClaim, PinAccessPortal, RoutingEdge, RoutingReservation, RoutingResourceId, RoutingResourceKind, RoutingResourceClaims
from ....Redstone.Technology import DefaultRedstoneRoutingTechnology
from ....Runtime.BoundedWork import ExecuteBoundedRuntimeWork, RuntimeWorkControl
from ....Runtime.Reliability import RoutingDeadline

try:
    from RedstoneCompiler.RustRouting import (
        BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
    )
    from RedstoneCompiler.RustRouting import BuildRouteClaimsBatch as _BuildRouteClaimsBatch
    from RedstoneCompiler.RustRouting import (
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
    )
    from RedstoneCompiler.RustRouting import GetRoutingThreadCount as _GetRoutingThreadCount
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import (
            BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
        )
        from RedstoneCompiler.RustRouting import (
            BuildRouteClaimsBatch as _BuildRouteClaimsBatch,
        )
        from RedstoneCompiler.RustRouting import (
            BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
        )
        from RedstoneCompiler.RustRouting import (
            GetRoutingThreadCount as _GetRoutingThreadCount,
        )
    except ImportError:
        _BuildFabricSubtreesBatchWithTelemetry = None
        _BuildRouteClaimsBatch = None
        _BuildRouteClaimsBatchWithTelemetry = None
        _GetRoutingThreadCount = None

from ..Solving.DynamicSolver import SolveComponentRoutingProblemDynamic
from .SymbolicState import (
    BuildComponentSymbolicNetStateCacheKey,
    PreparedComponentSymbolicNetStateCompilation,
    PreparedComponentSymbolicNetStateContext,
    SelectComponentSymbolicPhysicalPort,
    _BuildPreparedComponentSymbolicNetStateContextFingerprint,
    _BuildPreparedComponentSymbolicNetStateContextIdentity,
)


@dataclass(frozen=True)
class _PreparedSymbolicMutableTransaction:
    OriginalContext: PreparedComponentSymbolicNetStateContext
    WorkingContext: PreparedComponentSymbolicNetStateContext
    OriginalNetStateCache: dict[str, Any] | None
    WorkingNetStateCache: dict[str, Any]

    def Commit(self) -> None:
        Original = self.OriginalContext
        Working = self.WorkingContext
        for Name in (
            "FabricParentCache",
            "RouteClaimsConstructionCache",
            "TerminalFrontierCache",
            "TreeRepeaterSubproblemCache",
            "TreeRepeaterCacheStatistics",
        ):
            OriginalValue = getattr(Original, Name)
            OriginalValue.clear()
            OriginalValue.update(getattr(Working, Name))
        Original.TerminalFrontierBuildCount = (
            Working.TerminalFrontierBuildCount
        )
        Original.TerminalFrontierCacheHitCount = (
            Working.TerminalFrontierCacheHitCount
        )
        if self.OriginalNetStateCache is not None:
            self.OriginalNetStateCache.clear()
            self.OriginalNetStateCache.update(self.WorkingNetStateCache)


def _BeginPreparedSymbolicMutableTransaction(
    Context: PreparedComponentSymbolicNetStateContext,
    NetStateCache: dict[str, Any] | None,
) -> _PreparedSymbolicMutableTransaction:
    WorkingContext = replace(
        Context,
        FabricParentCache=deepcopy(Context.FabricParentCache),
        RouteClaimsConstructionCache=deepcopy(
            Context.RouteClaimsConstructionCache
        ),
        TerminalFrontierCache=deepcopy(Context.TerminalFrontierCache),
        TreeRepeaterSubproblemCache=deepcopy(
            Context.TreeRepeaterSubproblemCache
        ),
        TreeRepeaterCacheStatistics=deepcopy(
            Context.TreeRepeaterCacheStatistics
        ),
    )
    return _PreparedSymbolicMutableTransaction(
        OriginalContext=Context,
        WorkingContext=WorkingContext,
        OriginalNetStateCache=NetStateCache,
        WorkingNetStateCache=deepcopy(
            NetStateCache if NetStateCache is not None else {}
        ),
    )


def CompilePreparedComponentSymbolicNetStates(
    Context: PreparedComponentSymbolicNetStateContext,
    Problem: ComponentRoutingProblem,
    *,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    SymbolicNetStateCache: dict[str, Any] | None = None,
    ForbiddenExportPorts: tuple[Position3, ...] = (),
    MaximumWork: int | None = None,
    WorkAdmissionCheck: Callable[[], None] | None = None,
) -> PreparedComponentSymbolicNetStateCompilation:
    """Compile one access-bound state domain using reusable static tree data."""
    Signal = Context.Signal
    if (
        _BuildPreparedComponentSymbolicNetStateContextFingerprint(
            Problem,
            Signal,
        )
        != Context.ContextFingerprint
    ):
        raise ValueError(
            "prepared symbolic net-state compilation identity mismatch"
        )
    Forbidden = (
        {Signal: tuple(ForbiddenExportPorts)}
        if ForbiddenExportPorts
        else {}
    )
    Cache = (
        SymbolicNetStateCache
        if SymbolicNetStateCache is not None
        else {}
    )
    CacheKey = BuildComponentSymbolicNetStateCacheKey(
        Problem,
        Signal,
        Forbidden,
    )
    Cached = Cache.get(CacheKey)
    if Cached is not None:
        States, Diagnostics = Cached
        return PreparedComponentSymbolicNetStateCompilation(
            CacheKey=CacheKey,
            States=tuple(States),
            Complete=True,
            CacheHit=True,
            ExpansionCount=0,
            Diagnostics={
                **dict(Diagnostics),
                "SymbolicNetStateCacheHit": True,
                "PreparedSymbolicNetStateContextFingerprint": (
                    Context.ContextFingerprint
                ),
            },
        )
    Result = SolveComponentRoutingProblemDynamic(
        Problem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        ForbiddenExportPortsBySignal=Forbidden,
        RouteClaimsConstructionCache=(
            Context.RouteClaimsConstructionCache
        ),
        SymbolicNetStateCache=Cache,
        RequestedSymbolicStateSignals=frozenset((Signal,)),
        PreparedSymbolicNetStateContext=Context,
        StopAfterOwnedSignalFrontierProof=True,
        MaximumWorkOverride=MaximumWork,
        WorkAdmissionCheck=WorkAdmissionCheck,
    )
    Cached = Cache.get(CacheKey)
    Complete = bool(Result.Status != "incomplete" and Cached is not None)
    States = tuple(Cached[0]) if Cached is not None else None
    Diagnostics = dict(Cached[1]) if Cached is not None else {}
    Diagnostics.update({
        "SymbolicNetStateCacheHit": False,
        "PreparedSymbolicNetStateContextFingerprint": (
            Context.ContextFingerprint
        ),
        "PreparedSymbolicNetStateResultStatus": Result.Status,
    })
    return PreparedComponentSymbolicNetStateCompilation(
        CacheKey=CacheKey,
        States=States,
        Complete=Complete,
        CacheHit=False,
        ExpansionCount=Result.ExpansionCount,
        Diagnostics=Diagnostics,
    )


def CompilePreparedComponentSymbolicNetStatesBounded(
    Context: PreparedComponentSymbolicNetStateContext,
    Problem: ComponentRoutingProblem,
    *,
    Request: RuntimeWorkRequest,
    Deadline: RoutingDeadline,
    CancellationCheck: Callable[[], bool] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    SymbolicNetStateCache: dict[str, Any] | None = None,
    ForbiddenExportPorts: tuple[Position3, ...] = (),
) -> RuntimeWorkExecution[PreparedComponentSymbolicNetStateCompilation]:
    """Compile one symbolic state domain through the bounded N1 adapter."""
    Transaction = _BeginPreparedSymbolicMutableTransaction(
        Context,
        SymbolicNetStateCache,
    )
    ObservedExpansionCount = 0

    def Compile(
        Control: RuntimeWorkControl,
    ) -> RuntimeWorkProduct[PreparedComponentSymbolicNetStateCompilation]:
        def CheckWork(Diagnostics: dict[str, object]) -> None:
            nonlocal ObservedExpansionCount
            ExpansionCount = int(Diagnostics["ExpansionCount"])
            if ExpansionCount < ObservedExpansionCount:
                raise ValueError("symbolic expansion count moved backwards")
            Control.ObservePerformedWork(
                ExpansionCount - ObservedExpansionCount
            )
            ObservedExpansionCount = ExpansionCount
            if WorkCheck is not None:
                WorkCheck(Diagnostics)

        Compilation = CompilePreparedComponentSymbolicNetStates(
            Transaction.WorkingContext,
            Problem,
            DeadlineSeconds=Deadline.RemainingSeconds(),
            WorkCheck=CheckWork,
            SymbolicNetStateCache=Transaction.WorkingNetStateCache,
            ForbiddenExportPorts=ForbiddenExportPorts,
            MaximumWork=Request.WorkCap,
            WorkAdmissionCheck=Control.Checkpoint,
        )
        # The request cap is enforced before each new dynamic-solver expansion;
        # the returned count records the work actually admitted.
        FinalExpansionCount = max(0, int(Compilation.ExpansionCount))
        if FinalExpansionCount < ObservedExpansionCount:
            raise ValueError("symbolic expansion result moved backwards")
        Control.ObservePerformedWork(
            FinalExpansionCount - ObservedExpansionCount
        )
        if not Compilation.Complete:
            Control.StopIfWorkCapReached()
        IsPrepared = bool(
            Compilation.Complete and Compilation.States is not None
        )
        return RuntimeWorkProduct(
            Value=Compilation,
            SearchOutcome=(
                RuntimeSearchOutcome.Prepared
                if IsPrepared
                else RuntimeSearchOutcome.Unresolved
            ),
            ClaimStrength=(
                RuntimeClaimStrength.Complete
                if IsPrepared
                else RuntimeClaimStrength.Continuation
            ),
            TerminalReason=(
                RuntimeTerminalReason.Prepared
                if IsPrepared
                else RuntimeTerminalReason.IncompleteExploration
            ),
            CandidateIdentity=(
                Compilation.CacheKey
                if Compilation.States is not None
                else None
            ),
            Diagnostics=(
                ("Complete", "true" if Compilation.Complete else "false"),
                (
                    "StateCount",
                    str(len(Compilation.States or ())),
                ),
            ),
        )

    Execution = ExecuteBoundedRuntimeWork(
        Request,
        Deadline,
        Compile,
        CancellationCheck=CancellationCheck,
    )
    if Execution.Result.SearchOutcome is RuntimeSearchOutcome.Prepared:
        Transaction.Commit()
    return Execution


def CompilePreparedComponentPhysicalFactorStateBatch(
    Context: PreparedComponentSymbolicNetStateContext,
    ProblemsByAccess: Mapping[str, ComponentRoutingProblem],
    *,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    SymbolicNetStateCache: dict[str, Any] | None = None,
    MaximumWork: int | None = None,
    WorkObserver: Callable[[int], None] | None = None,
) -> dict[str, PreparedComponentSymbolicNetStateCompilation]:
    """Compile exact physical egress factors in shared frontier batches.

    Factors with the same certified owned-candidate domain share one dynamic
    solver invocation.  Results are then partitioned by the immutable local
    egress path and stored under the unchanged per-factor cache identities.
    """
    if (
        MaximumWork is not None
        and (
            isinstance(MaximumWork, bool)
            or not isinstance(MaximumWork, int)
            or MaximumWork < 0
        )
    ):
        raise ValueError("MaximumWork must be a non-negative integer or None")
    Cache = (
        SymbolicNetStateCache
        if SymbolicNetStateCache is not None
        else {}
    )
    ProblemMaximumWork = min(
        (
            max(0, int(Problem.MaximumWork))
            for Problem in ProblemsByAccess.values()
        ),
        default=0,
    )
    EffectiveMaximumWork = (
        ProblemMaximumWork
        if MaximumWork is None
        else min(ProblemMaximumWork, MaximumWork)
    )
    Transaction = _BeginPreparedSymbolicMutableTransaction(Context, Cache)
    Context = Transaction.WorkingContext
    Cache = Transaction.WorkingNetStateCache
    PerformedWork = 0
    StartedAt = monotonic()
    Results: dict[str, PreparedComponentSymbolicNetStateCompilation] = {}
    MissesByCertifiedDomain: dict[
        frozenset[str],
        list[tuple[str, ComponentRoutingProblem, str, Any]],
    ] = defaultdict(list)
    RepresentativeProblem = next(iter(ProblemsByAccess.values()), None)
    ReferenceIdentity: dict[str, object] = {}
    if RepresentativeProblem is not None:
        ReferenceIdentity = (
            _BuildPreparedComponentSymbolicNetStateContextIdentity(
                RepresentativeProblem,
                Context.Signal,
            )
        )
    if RepresentativeProblem is not None:
        if (
            _BuildPreparedComponentSymbolicNetStateContextFingerprint(
                RepresentativeProblem,
                Context.Signal,
            )
            != Context.ContextFingerprint
        ):
            raise ValueError(
                "prepared physical factor batch identity mismatch: "
                "representative context does not match prepared context"
            )

    def BuildContextFieldDiffs(
        Problem: ComponentRoutingProblem,
    ) -> tuple[str, ...]:
        ProblemIdentity = (
            _BuildPreparedComponentSymbolicNetStateContextIdentity(
                Problem,
                Context.Signal,
            )
        )
        if ProblemIdentity == ReferenceIdentity:
            return ()
        return tuple(
            Name
            for Name in sorted(ReferenceIdentity)
            if ReferenceIdentity.get(Name) != ProblemIdentity.get(Name)
        )

    def SharesPreparedContextInputs(
        Problem: ComponentRoutingProblem,
    ) -> tuple[str, ...]:
        if RepresentativeProblem is None:
            return ()
        if (
            _BuildPreparedComponentSymbolicNetStateContextFingerprint(
                Problem,
                Context.Signal,
            )
            != Context.ContextFingerprint
        ):
            return ("context-fingerprint",)
        return BuildContextFieldDiffs(Problem)

    for AccessFingerprint, Problem in sorted(ProblemsByAccess.items()):
        Mismatches = SharesPreparedContextInputs(Problem)
        if Mismatches:
            raise ValueError(
                "prepared physical factor batch identity mismatch: "
                f"semantic context mismatch ({', '.join(Mismatches)})"
            )
        Port = SelectComponentSymbolicPhysicalPort(
            Problem,
            Context.Signal,
        )
        if Port is None:
            raise ValueError(
                "prepared physical factor batch requires a fixed port"
            )
        CacheKey = BuildComponentSymbolicNetStateCacheKey(
            Problem,
            Context.Signal,
            {},
            PreparedContextFingerprint=Context.ContextFingerprint,
        )
        Cached = Cache.get(CacheKey)
        if Cached is not None:
            States, Diagnostics = Cached
            Results[str(AccessFingerprint)] = (
                PreparedComponentSymbolicNetStateCompilation(
                    CacheKey=CacheKey,
                    States=tuple(States),
                    Complete=True,
                    CacheHit=True,
                    ExpansionCount=0,
                    Diagnostics={
                        **dict(Diagnostics),
                        "SymbolicNetStateCacheHit": True,
                        "PhysicalFactorBatchCacheHit": True,
                    },
                )
            )
            continue
        Certified = frozenset(getattr(
            Port,
            "OwnedCandidateFingerprints",
            (),
        ))
        MissesByCertifiedDomain[Certified].append((
            str(AccessFingerprint),
            Problem,
            CacheKey,
            Port,
        ))

    for Certified, Members in sorted(
        MissesByCertifiedDomain.items(),
        key=lambda Value: tuple(sorted(Value[0])),
    ):
        # A certified physical access domain is a hard intersection with
        # every owned terminal domain.  Detect an empty intersection before
        # entering the tree DP: the dynamic solver would reach the same
        # complete empty frontier, but doing so once per distinct rejected
        # access certificate dominated CLA-sized unary/pair compilation.
        CandidateFilterEmpty = bool(
            Certified
            and any(
                not Certified.intersection(Eligible)
                for Eligible in (
                    Context
                    .ImmutableEligibleCandidateFingerprintsByDomain
                )
            )
        )
        if CandidateFilterEmpty:
            Diagnostics = {
                "SymbolicNetStateCacheHit": False,
                "PhysicalFactorBatchCacheHit": False,
                "PhysicalFactorBatchSize": len(Members),
                "PhysicalFactorBatchCertifiedCandidateCount": len(
                    Certified
                ),
                "PhysicalFactorBatchCandidateFilterEmpty": True,
                "PreparedSymbolicNetStateResultStatus": (
                    "architectural-unsatisfiable"
                ),
            }
            for AccessFingerprint, _Problem, CacheKey, _Port in Members:
                Cache[CacheKey] = ((), Diagnostics)
                Results[AccessFingerprint] = (
                    PreparedComponentSymbolicNetStateCompilation(
                        CacheKey=CacheKey,
                        States=(),
                        Complete=True,
                        CacheHit=False,
                        ExpansionCount=0,
                        Diagnostics=Diagnostics,
                    )
                )
            continue
        RemainingDeadline = (
            None
            if DeadlineSeconds is None
            else max(0.0, DeadlineSeconds - (monotonic() - StartedAt))
        )
        RepresentativeProblem = Members[0][1]
        BatchCache: dict[str, Any] = {}
        Result = SolveComponentRoutingProblemDynamic(
            RepresentativeProblem,
            DeadlineSeconds=RemainingDeadline,
            WorkCheck=WorkCheck,
            RouteClaimsConstructionCache=(
                Context.RouteClaimsConstructionCache
            ),
            SymbolicNetStateCache=BatchCache,
            RequestedSymbolicStateSignals=frozenset((Context.Signal,)),
            PreparedSymbolicNetStateContext=Context,
            PreparedPhysicalPortVariants=tuple(
                Member[3] for Member in Members
            ),
            StopAfterOwnedSignalFrontierProof=True,
            MaximumWorkOverride=max(
                0,
                EffectiveMaximumWork - PerformedWork,
            ),
        )
        ResultWork = max(0, int(Result.ExpansionCount))
        if ResultWork > EffectiveMaximumWork - PerformedWork:
            raise RuntimeError("physical factor batch exceeded MaximumWork")
        PerformedWork += ResultWork
        if WorkObserver is not None:
            WorkObserver(ResultWork)
        RepresentativeCacheKey = BuildComponentSymbolicNetStateCacheKey(
            RepresentativeProblem,
            Context.Signal,
            {},
            PreparedContextFingerprint=Context.ContextFingerprint,
        )
        CachedBatch = BatchCache.get(RepresentativeCacheKey)
        BatchComplete = bool(
            Result.Status != "incomplete" and CachedBatch is not None
        )
        BatchStates = (
            tuple(CachedBatch[0]) if CachedBatch is not None else ()
        )
        BatchDiagnostics = (
            dict(CachedBatch[1]) if CachedBatch is not None else {}
        )
        for AccessFingerprint, _Problem, CacheKey, Port in Members:
            if BatchComplete:
                LocalPath = tuple(Port.LocalPath)
                States = tuple(
                    State for State in BatchStates
                    if tuple(State.EgressPath) == LocalPath
                )
                Diagnostics = {
                    **BatchDiagnostics,
                    "SymbolicNetStateCacheHit": False,
                    "PhysicalFactorBatchCacheHit": False,
                    "PhysicalFactorBatchSize": len(Members),
                    "PhysicalFactorBatchCertifiedCandidateCount": len(
                        Certified
                    ),
                    "PreparedSymbolicNetStateResultStatus": Result.Status,
                }
                Cache[CacheKey] = (States, Diagnostics)
            else:
                States = None
                Diagnostics = {
                    "PhysicalFactorBatchCacheHit": False,
                    "PhysicalFactorBatchSize": len(Members),
                    "PreparedSymbolicNetStateResultStatus": Result.Status,
                }
            Results[AccessFingerprint] = (
                PreparedComponentSymbolicNetStateCompilation(
                    CacheKey=CacheKey,
                    States=States,
                    Complete=BatchComplete,
                    CacheHit=False,
                    ExpansionCount=Result.ExpansionCount,
                    Diagnostics=Diagnostics,
                )
            )
        if not BatchComplete:
            break

    for AccessFingerprint, Problem in sorted(ProblemsByAccess.items()):
        if str(AccessFingerprint) in Results:
            continue
        CacheKey = BuildComponentSymbolicNetStateCacheKey(
            Problem,
            Context.Signal,
            {},
            PreparedContextFingerprint=Context.ContextFingerprint,
        )
        Results[str(AccessFingerprint)] = (
            PreparedComponentSymbolicNetStateCompilation(
                CacheKey=CacheKey,
                States=None,
                Complete=False,
                CacheHit=False,
                ExpansionCount=0,
                Diagnostics={
                    "PhysicalFactorBatchDeferredAfterIncompleteGroup": True,
                },
            )
        )
    if (
        len(Results) == len(ProblemsByAccess)
        and all(Compilation.Complete for Compilation in Results.values())
    ):
        Transaction.Commit()
    return Results

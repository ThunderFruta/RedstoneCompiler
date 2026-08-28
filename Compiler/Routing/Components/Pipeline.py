"""Closed-component compilation and authoritative assembly orchestration."""

from __future__ import annotations

from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import product
from math import prod
import multiprocessing
import os
from time import monotonic
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping
from ..Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from ..Contracts.Component import (
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    PhysicalComponentAssemblyPlan,
    PhysicalComponentChannelReservation,
    PhysicalComponentPortReservation,
    PhysicalComponentSelectedLocalPortSupport,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from ..Contracts.Core import Position3
from ..Contracts.PhysicalInterface import (
    PhysicalComponentLocalFactorProjection,
    PhysicalComponentLocalFactorProjectionComparison,
    PhysicalComponentLocalFactorUnsatCertificate,
    PhysicalLocalPortPairProofRecord,
    PhysicalLocalPortPairSupportCertificate,
    PhysicalComponentSymbolicHigherOrderCertificate,
    PhysicalComponentSymbolicPortPairCertificate,
    PhysicalPortCorridorDomain,
    PhysicalPortCorridorFactor,
    PreparedPhysicalComponentAssembly,
    PreparedPhysicalComponentPortFactorDomain,
)
from ..Interfaces import BoundaryRelations
from ..Interfaces.BoundaryRelations import (
    BuildPhysicalPortGlobalContractFingerprint,
    ProjectPhysicalComponentSignalGlobalProfile,
)
from ..Interfaces.PhysicalClaims import ComponentClaimsConflict
from ..ResourceGraph import RoutingResourceClaims
from ..Reliability import BuildStableFingerprint
from .InterfacePlanning import (
    BuildComponentCapacityGuide,
    ComponentCapacityGuide,
    ComponentCapacityGuideOption,
    ComponentInterfaceContract,
    ComponentPlanningResult,
    ComponentPlanningStatus,
    IterClosedComponentContracts,
    PlanClosedComponent,
    SolveComponentInterfaceCsp,
)

from .Core import BuildCompleteComponentNetPortfolioStaticContext
from .SymbolicState import (
    _BuildPreparedComponentSymbolicNetStateContextFingerprint,
    BuildComponentSymbolicNetStateCacheKey,
    PrepareComponentSymbolicNetStateContext,
)
from .SymbolicWorkers import (
    CompilePreparedComponentPhysicalFactorStateBatch,
    CompilePreparedComponentSymbolicNetStates,
)
from .Portfolios import (
    BuildCompleteOpposingNetAccessContractDomain,
    BuildCompleteOpposingNetAccessRowContext,
    CompileCompleteComponentNetVariantPortfolio,
    CompileCompleteComponentNetVariantPortfolios,
    EvaluateCompleteOpposingNetAccessContractRow,
)
from .Solver import (
    MaterializeRoutedComponentTemplate,
    SolveComponentRoutingProblem,
    ValidateRoutedComponentHandoff,
)

from .Cache import (
    _CompletedComponentTemplateCache,
    BuildCompletedComponentTemplateCacheFingerprint,
    _InstantiateCachedTemplate,
)
from .Validation import (
    _Origin,
    _SignalStructuralIdentities,
    _ValidatePhysicalProblemContract,
    _ValidatePhysicalTemplate,
)
@dataclass(frozen=True)
class ComponentAssemblyResult:
    """Frozen component claims and their validated global handoff."""

    Placed: Any
    Template: RoutedComponentTemplate
    HandoffDiagnostics: dict[str, object]
    PhysicalAssemblyPlan: PhysicalComponentAssemblyPlan
def CompileClosedComponent(
    Problem: ComponentRoutingProblem,
    *,
    AssemblyPlan: PhysicalComponentAssemblyPlan | None = None,
    DeadlineSeconds: float | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ForbiddenAssignmentFingerprints: frozenset[str] = frozenset(),
    ForbiddenExportPortsBySignal: dict[
        str, tuple[Position3, ...]
    ] | None = None,
    ForbiddenForeignCandidateFingerprintsBySignal: dict[
        str, frozenset[str]
    ] | None = None,
    ForbiddenForeignAssignmentPairs: tuple[
        frozenset[tuple[str, Position3, str]], ...
    ] = (),
    VariantPortfolioCache: dict[Any, Any] | None = None,
    NetVariantConstructionCache: dict[Any, Any] | None = None,
    RouteClaimsConstructionCache: dict[
        frozenset[Position3], RoutingResourceClaims
    ] | None = None,
    NetVariantDiscoveryStateCache: dict[Any, Any] | None = None,
    DiscoveryVariantLimit: int | None = 8,
    DiscoveryVariantLimitsBySignal: dict[
        str, int | None
    ] | None = None,
    RequiredForeignTransitSignals: frozenset[str] = frozenset(),
) -> ComponentRoutingSolveResult:
    """Compile one closed local component without invoking global retries."""
    if Problem.Interface is None:
        raise ValueError(
            "production component compilation requires a closed interface"
        )
    Declared = Problem.Interface.DeclaredFeedthroughSignals
    Actual = frozenset(
        Value.Signal for Value in Problem.ForeignTransitDomains
    )
    if Actual - Declared:
        raise ValueError(
            "component problem contains implicit foreign transit domains"
        )
    EffectiveAssemblyPlan = (
        AssemblyPlan or Problem.PhysicalAssemblyPlan
    )
    if EffectiveAssemblyPlan is not None:
        if not EffectiveAssemblyPlan.Complete:
            raise ValueError(
                "component compilation requires a complete physical "
                "assembly plan"
            )
        if (
            Problem.PhysicalAssemblyPlan is None
            or Problem.PhysicalAssemblyPlan.PlanFingerprint
            != EffectiveAssemblyPlan.PlanFingerprint
            or Problem.Interface.PhysicalAssemblyPlanFingerprint
            != EffectiveAssemblyPlan.PlanFingerprint
            or Problem.Interface.InterfaceFingerprint
            != EffectiveAssemblyPlan.InterfaceFingerprint
        ):
            raise ValueError(
                "component problem and physical assembly identities differ"
            )
        _ValidatePhysicalProblemContract(
            Problem,
            EffectiveAssemblyPlan,
        )
        if (
            ForbiddenAssignmentFingerprints
            or (ForbiddenExportPortsBySignal or {})
            or (
                ForbiddenForeignCandidateFingerprintsBySignal
                or {}
            )
            or ForbiddenForeignAssignmentPairs
            or RequiredForeignTransitSignals
        ):
            raise ValueError(
                "physical component compilation cannot reopen its "
                "immutable assembly plan"
            )
    CacheEligible = bool(
        not ForbiddenAssignmentFingerprints
        and not (ForbiddenExportPortsBySignal or {})
        and not (
            ForbiddenForeignCandidateFingerprintsBySignal or {}
        )
        and not ForbiddenForeignAssignmentPairs
        and not RequiredForeignTransitSignals
    )
    CacheFingerprint = (
        BuildCompletedComponentTemplateCacheFingerprint(Problem)
        if CacheEligible
        else ""
    )
    CacheKey = CacheFingerprint
    Cached = (
        _CompletedComponentTemplateCache.get(CacheKey)
        if CacheEligible
        else None
    )
    if Cached is not None:
        (
            CachedOrigin,
            CachedTemplate,
            CachedSignalIdentities,
        ) = Cached
        Instantiated = _InstantiateCachedTemplate(
            Problem,
            CachedOrigin,
            CachedTemplate,
            CachedSignalIdentities,
            CacheFingerprint,
        )
        if Instantiated is not None:
            _ValidatePhysicalTemplate(Problem, Instantiated)
            return ComponentRoutingSolveResult(
                Status="feasible",
                Template=Instantiated,
                ProofFingerprint=Instantiated.ProofFingerprint,
                ExpansionCount=0,
                Diagnostics=Instantiated.Diagnostics,
            )
    Result = SolveComponentRoutingProblem(
        Problem,
        DeadlineSeconds=DeadlineSeconds,
        WorkCheck=WorkCheck,
        ForbiddenAssignmentFingerprints=(
            ForbiddenAssignmentFingerprints
        ),
        ForbiddenExportPortsBySignal=ForbiddenExportPortsBySignal,
        ForbiddenForeignCandidateFingerprintsBySignal=(
            ForbiddenForeignCandidateFingerprintsBySignal
        ),
        ForbiddenForeignAssignmentPairs=(
            ForbiddenForeignAssignmentPairs
        ),
        VariantPortfolioCache=VariantPortfolioCache,
        NetVariantConstructionCache=NetVariantConstructionCache,
        RouteClaimsConstructionCache=RouteClaimsConstructionCache,
        NetVariantDiscoveryStateCache=NetVariantDiscoveryStateCache,
        DiscoveryVariantLimit=DiscoveryVariantLimit,
        DiscoveryVariantLimitsBySignal=(
            DiscoveryVariantLimitsBySignal
        ),
        RequiredForeignTransitSignals=RequiredForeignTransitSignals,
    )
    if Result.Feasible and Result.Template is not None:
        _ValidatePhysicalTemplate(Problem, Result.Template)
    if (
        CacheEligible
        and Result.Feasible
        and Result.Template is not None
    ):
        TemplateDiagnostics = {
            **Result.Template.Diagnostics,
            "CompletedTemplateCacheHit": False,
            "CompletedTemplateCacheFingerprint": CacheFingerprint,
            "CompletedTemplateTranslationDelta": [0, 0, 0],
        }
        Template = replace(
            Result.Template,
            Diagnostics=TemplateDiagnostics,
        )
        Result = replace(
            Result,
            Template=Template,
            Diagnostics=TemplateDiagnostics,
        )
        _CompletedComponentTemplateCache[CacheKey] = (
            _Origin(Problem),
            Template,
            _SignalStructuralIdentities(Problem),
        )
    return Result


def AssembleClosedComponentForGlobalRouting(
    Placed: Any,
    Template: RoutedComponentTemplate,
    *,
    PhysicalAssemblyPlan: PhysicalComponentAssemblyPlan,
    PlacementFingerprint: str,
    LocalTemplateFingerprint: str,
) -> ComponentAssemblyResult:
    """Freeze local claims against the immutable port-first assembly plan."""
    if not PhysicalAssemblyPlan.Complete:
        raise ValueError("physical component assembly plan is incomplete")
    if (
        PhysicalAssemblyPlan.PlacementFingerprint
        != PlacementFingerprint
        or Template.InterfaceFingerprint
        != PhysicalAssemblyPlan.InterfaceFingerprint
    ):
        raise ValueError(
            "physical component assembly handoff identity mismatch"
        )
    Diagnostics = dict(
        getattr(Placed, "LocalRouteDiagnostics", {}) or {}
    )
    Diagnostics["__PhysicalComponentAssemblyPlan__"] = (
        PhysicalAssemblyPlan.ToDictionary()
    )
    StagedPlaced = replace(
        Placed,
        LocalRouteDiagnostics=Diagnostics,
    )
    Materialized = MaterializeRoutedComponentTemplate(
        StagedPlaced,
        Template,
    )
    try:
        Handoff = ValidateRoutedComponentHandoff(
            Materialized,
            Template,
            PlacementFingerprint=PlacementFingerprint,
            LocalTemplateFingerprint=LocalTemplateFingerprint,
        )
    except ValueError as Error:
        raise RoutingStageError(RoutingFailure(
            Reason=(
                RoutingFailureReason
                .ComponentAssemblyIdentityMismatch
            ),
            Stage="ComponentAssemblyIdentityValidation",
            Detail=str(Error),
            Diagnostics={
                "PlacementFingerprint": PlacementFingerprint,
                "LocalTemplateFingerprint": (
                    LocalTemplateFingerprint
                ),
                "PhysicalAssemblyPlanFingerprint": (
                    PhysicalAssemblyPlan.PlanFingerprint
                ),
                "InterfaceFingerprint": (
                    PhysicalAssemblyPlan.InterfaceFingerprint
                ),
                "RoutedTemplateFingerprint": (
                    Template.RoutedTemplateFingerprint
                ),
                "FabricFingerprint": Template.FabricFingerprint,
                "ImplicitForeignTransitDomainCount": 0,
            },
        )) from Error
    return ComponentAssemblyResult(
        Placed=Materialized,
        Template=Template,
        HandoffDiagnostics=Handoff,
        PhysicalAssemblyPlan=PhysicalAssemblyPlan,
    )

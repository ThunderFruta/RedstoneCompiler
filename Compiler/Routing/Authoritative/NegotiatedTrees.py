"""Negotiated global route-tree planning."""
from __future__ import annotations
from ..ChannelPlanner import NegotiatedRoutePlan
from ..ChannelPlanner import RoutingIterationMetrics
from ..Contracts.Core import Position2
from ..Contracts.Core import Position3
from ..Contracts.Results import RoutingResources
from ..Failures import RoutingFailure
from ..Failures import RoutingFailureReason
from ..Failures import RoutingStageError
from ..Interfaces.PhysicalClaims import ClaimConflictPositions
from ..Policy import PhysicalDesignPolicy
from ..Reliability import RemainingRoutingRuntimeMilliseconds
from ..Reliability import RoutingDeadline
from ..ResourceGraph import BuildRoutingEnvelope
from ..ResourceGraph import FindClaimConflicts
from ..ResourceGraph import FindSelfClaimConflicts
from ..ResourceGraph import IndexedRoutingResourceGraph
from ..ResourceGraph import NetRouteCandidate
from ..ResourceGraph import RoutingResourceClaims
from ..ResourceGraph import RoutingResourceId
from ..Technology import RedstoneRoutingTechnology
from collections import Counter
from collections import defaultdict
from math import ceil
from time import monotonic
from typing import Any
from typing import Callable
from typing import Iterable
import os
try:
    from RedstoneCompiler.RustRouting import GetRoutingThreadCount as GetRustRoutingThreadCount, RoutingContext as RustRoutingContext, SearchExteriorConnectorsBatchWithTelemetry as _SearchExteriorConnectorsBatchWithTelemetry
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import GetRoutingThreadCount as GetRustRoutingThreadCount, RoutingContext as RustRoutingContext, SearchExteriorConnectorsBatchWithTelemetry as _SearchExteriorConnectorsBatchWithTelemetry
    except Exception:
        RustRoutingContext = None
        _SearchExteriorConnectorsBatchWithTelemetry = None

        def GetRustRoutingThreadCount() -> int:
            return 1
from .AssignmentState import BuildNegotiatedFallbackGuideColumns, BuildNegotiatedInitialColumns, BuildNegotiatedInitialTiles, BuildNegotiatedRouteTreeState, FindNegotiatedBoundaryTouches, NegotiatedColumnsForTiles, NegotiatedRegionState, NegotiatedRouteTreeState, _NegotiatedTileForColumn, _NegotiatedTileIntersectsBounds
from .CandidateDomains import BuildGeneratedFixedPortalDomainExhaustionProof, ExactAssignmentCompletionSignalOrderKey, FindUnavoidableMandatoryClaimCut, RetainNegotiatedInitialCandidateOption, SelectExactAssignmentCompletionCutWideRequests, SelectExactAssignmentCompletionRequestBatch, SelectExactAssignmentCompletionReserveMilliseconds, SelectNegotiatedExpandedRequestMinimumExpansionCount, SelectPendingExactAssignmentCompletionRequestIndices, ShouldContinueDistinctExactCutFrontier
import Compiler.Routing.Authoritative.Portals as PortalOperations
from .Portals import ShouldRetryNegotiatedExactAssignment
from .NegotiatedRouting import NEGOTIATED_ROUTING_PHASES
from .NegotiatedRouting.State import NegotiatedRoutingState
from .RunState import AuthoritativeRoutingServices

def PlanNegotiatedRouteTrees(Context: Any, Profiles: dict[str, Any], RouteRequestsBySignal: dict[str, list[tuple[Any, ...]]], RouteMetadataBySignal: dict[str, list[tuple[Any, ...]]], Region: Any, ReservedAccess: frozenset[Position3], Resources: RoutingResources, Technology: RedstoneRoutingTechnology, Policy: PhysicalDesignPolicy, Deadline: RoutingDeadline, AdaptiveExpiresAt: float, CheckRuntimeBudget: Callable[[str, dict[str, object] | None], None], RegenerateSignals: frozenset[str]=frozenset(), SeedCandidatesBySignal: dict[str, tuple[Any, ...]] | None=None, InitialCandidatesBySignal: dict[str, tuple[Any, ...]] | None=None, LocalClaimReleaseDiagnostics: dict[str, object] | None=None, RequestHigherLayerOnExactCut: bool=False, AdvancePlacementOnExhaustedExactCut: bool=False, CompleteSeedDomain: bool=False) -> NegotiatedRoutePlan:
    """Route one tree per net and negotiate exact Redstone claim conflicts."""
    RunState = NegotiatedRoutingState(Context=Context, Profiles=Profiles, RouteRequestsBySignal=RouteRequestsBySignal, RouteMetadataBySignal=RouteMetadataBySignal, Region=Region, ReservedAccess=ReservedAccess, Resources=Resources, Technology=Technology, Policy=Policy, Deadline=Deadline, AdaptiveExpiresAt=AdaptiveExpiresAt, CheckRuntimeBudget=CheckRuntimeBudget, RegenerateSignals=RegenerateSignals, SeedCandidatesBySignal=SeedCandidatesBySignal, InitialCandidatesBySignal=InitialCandidatesBySignal, LocalClaimReleaseDiagnostics=LocalClaimReleaseDiagnostics, RequestHigherLayerOnExactCut=RequestHigherLayerOnExactCut, AdvancePlacementOnExhaustedExactCut=AdvancePlacementOnExhaustedExactCut, CompleteSeedDomain=CompleteSeedDomain)
    RunServices = AuthoritativeRoutingServices.FromNamespace(globals())
    for RunPhase in NEGOTIATED_ROUTING_PHASES:
        Outcome = RunPhase(RunState, RunServices)
        if Outcome.Returned:
            return Outcome.Value
    raise RuntimeError('negotiated routing phases completed without a result')

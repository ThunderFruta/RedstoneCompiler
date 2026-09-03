"""Negotiated global route-tree planning."""
from __future__ import annotations
from ...Planning.ChannelPlanner import NegotiatedRoutePlan
from ...Planning.ChannelPlanner import RoutingIterationMetrics
from ....Contracts.Core import Position2
from ....Contracts.Core import Position3
from ....Contracts.Results import RoutingResources
from ....Contracts.Failures import RoutingFailure
from ....Contracts.Failures import RoutingFailureReason
from ....Contracts.Failures import RoutingStageError
from ....Interfaces.PhysicalClaims import ClaimConflictPositions
from ....Policy import PhysicalDesignPolicy
from ....Execution.Reliability import RemainingRoutingRuntimeMilliseconds
from ....Execution.Reliability import RoutingDeadline
from ....Resources.ResourceGraph import BuildRoutingEnvelope
from ....Resources.ResourceGraph import FindClaimConflicts
from ....Resources.ResourceGraph import FindSelfClaimConflicts
from ....Resources.ResourceGraph import IndexedRoutingResourceGraph
from ....Resources.ResourceGraph import NetRouteCandidate
from ....Resources.ResourceGraph import RoutingResourceClaims
from ....Resources.ResourceGraph import RoutingResourceId
from ....Redstone.Technology import RedstoneRoutingTechnology
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
from ..Assignment.AssignmentState import BuildNegotiatedFallbackGuideColumns, BuildNegotiatedInitialColumns, BuildNegotiatedInitialTiles, BuildNegotiatedRouteTreeState, FindNegotiatedBoundaryTouches, NegotiatedColumnsForTiles, NegotiatedRegionState, NegotiatedRouteTreeState, _NegotiatedTileForColumn, _NegotiatedTileIntersectsBounds
from ..Candidates.CandidateDomains import BuildGeneratedFixedPortalDomainExhaustionProof, ExactAssignmentCompletionSignalOrderKey, FindUnavoidableMandatoryClaimCut, RetainNegotiatedInitialCandidateOption, SelectExactAssignmentCompletionCutWideRequests, SelectExactAssignmentCompletionRequestBatch, SelectExactAssignmentCompletionReserveMilliseconds, SelectNegotiatedExpandedRequestMinimumExpansionCount, SelectPendingExactAssignmentCompletionRequestIndices, ShouldContinueDistinctExactCutFrontier
import PhysicalDesign.Routing.Global.Ports.Portals as PortalOperations
from ..Ports.Portals import ShouldRetryNegotiatedExactAssignment
from .Engine import NEGOTIATED_ROUTING_PHASES
from .Engine.State import NegotiatedRoutingState
from ..Flow.RunState import AuthoritativeRoutingServices

def PlanNegotiatedRouteTrees(Context: Any, Profiles: dict[str, Any], RouteRequestsBySignal: dict[str, list[tuple[Any, ...]]], RouteMetadataBySignal: dict[str, list[tuple[Any, ...]]], Region: Any, ReservedAccess: frozenset[Position3], Resources: RoutingResources, Technology: RedstoneRoutingTechnology, Policy: PhysicalDesignPolicy, Deadline: RoutingDeadline, AdaptiveExpiresAt: float, CheckRuntimeBudget: Callable[[str, dict[str, object] | None], None], RegenerateSignals: frozenset[str]=frozenset(), SeedCandidatesBySignal: dict[str, tuple[Any, ...]] | None=None, InitialCandidatesBySignal: dict[str, tuple[Any, ...]] | None=None, LocalClaimReleaseDiagnostics: dict[str, object] | None=None, RequestHigherLayerOnExactCut: bool=False, AdvancePlacementOnExhaustedExactCut: bool=False, CompleteSeedDomain: bool=False) -> NegotiatedRoutePlan:
    """Route one tree per net and negotiate exact Redstone claim conflicts."""
    RunState = NegotiatedRoutingState(Context=Context, Profiles=Profiles, RouteRequestsBySignal=RouteRequestsBySignal, RouteMetadataBySignal=RouteMetadataBySignal, Region=Region, ReservedAccess=ReservedAccess, Resources=Resources, Technology=Technology, Policy=Policy, Deadline=Deadline, AdaptiveExpiresAt=AdaptiveExpiresAt, CheckRuntimeBudget=CheckRuntimeBudget, RegenerateSignals=RegenerateSignals, SeedCandidatesBySignal=SeedCandidatesBySignal, InitialCandidatesBySignal=InitialCandidatesBySignal, LocalClaimReleaseDiagnostics=LocalClaimReleaseDiagnostics, RequestHigherLayerOnExactCut=RequestHigherLayerOnExactCut, AdvancePlacementOnExhaustedExactCut=AdvancePlacementOnExhaustedExactCut, CompleteSeedDomain=CompleteSeedDomain)
    RunServices = AuthoritativeRoutingServices.FromNamespace(globals())
    for RunPhase in NEGOTIATED_ROUTING_PHASES:
        Outcome = RunPhase(RunState, RunServices)
        if Outcome.Returned:
            return Outcome.Value
    raise RuntimeError('negotiated routing phases completed without a result')

"""Declared physical component feedthrough-domain compilation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import combinations, islice, product
from math import prod as ProductIntegers
from time import monotonic
from typing import Any, Callable, Iterable, Mapping


from ..Contracts.Component import (
    ClosedComponentInterface,
    ComponentFeedthroughContract,
    ComponentForeignTransitDomain,
    ComponentInterfacePort,
    ComponentRoutingFabric,
    ComponentRoutingProblem,
    ComponentRoutingSolveResult,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    RoutedComponentNet,
    RoutedComponentTemplate,
)
from ..Contracts.Core import Position3
from ..Interfaces.PhysicalClaims import (
    _MergeClaims,
    ComponentClaimsCompatibleForOwners,
    ComponentClaimsConflict,
)
from ..ResourceGraph import (
    FindSelfClaimConflicts,
    LocalRouteClaim,
    PinAccessPortal,
    RoutingEdge,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
    RoutingResourceClaims,
)
from ..Technology import DefaultRedstoneRoutingTechnology

try:
    from ...RustRouting import (
        BuildFabricSubtreesBatchWithTelemetry as _BuildFabricSubtreesBatchWithTelemetry,
    )
    from ...RustRouting import BuildRouteClaimsBatch as _BuildRouteClaimsBatch
    from ...RustRouting import (
        BuildRouteClaimsBatchWithTelemetry as _BuildRouteClaimsBatchWithTelemetry,
    )
    from ...RustRouting import GetRoutingThreadCount as _GetRoutingThreadCount
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

from .Core import _ClaimsFingerprint, _NormalizedEdge, _RelativeGeometry, _StableFingerprint
from .Domains import PruneDominatedComponentNetVariants
from .NetPlanning import _BuildNetVariant
def BuildDeclaredComponentFeedthroughDomains(
    Problem: ComponentRoutingProblem,
    Feedthroughs: Iterable[ComponentFeedthroughContract],
) -> tuple[ComponentForeignTransitDomain, ...]:
    """Compile only the endpoint pairs in an explicit feedthrough contract."""
    FabricNodes = frozenset(Problem.Fabric.Nodes)
    Result = []
    for Feedthrough in sorted(
        Feedthroughs,
        key=lambda Value: (
            tuple(sorted(Value.EndpointPairs)),
            Value.Capacity,
        ),
    ):
        Candidates = []
        RejectionCounts: dict[str, int] = {}
        ImmutableConflictSignals: set[str] = set()
        ReservedPathNodes = tuple(Feedthrough.ReservedPathNodes)
        ReservedPathNodeSet = frozenset(ReservedPathNodes)
        ReservedPathEdges = frozenset(
            _NormalizedEdge(First, Second)
            for First, Second in zip(
                ReservedPathNodes,
                ReservedPathNodes[1:],
            )
        )
        ReservedPathValid = bool(
            not ReservedPathNodes
            or (
                len(ReservedPathNodes) >= 2
                and len(ReservedPathNodeSet) == len(ReservedPathNodes)
                and ReservedPathNodeSet <= FabricNodes
                and all(
                    Edge in Problem.Fabric.Edges
                    for Edge in ReservedPathEdges
                )
                and (
                    Problem.ResourceGraph is None
                    or all(
                        Problem.ResourceGraph.BuildPrimitive(
                            First,
                            Second,
                        ) is not None
                        for First, Second in zip(
                            ReservedPathNodes,
                            ReservedPathNodes[1:],
                        )
                    )
                )
            )
        )
        for Entry, Exit in sorted(set(Feedthrough.EndpointPairs)):
            if (
                Entry == Exit
                or Entry not in FabricNodes
                or Exit not in FabricNodes
                or not ReservedPathValid
                or (
                    ReservedPathNodes
                    and (
                        Entry != ReservedPathNodes[0]
                        or Exit != ReservedPathNodes[-1]
                    )
                )
            ):
                RejectionCounts["invalid-declared-endpoints"] = (
                    RejectionCounts.get(
                        "invalid-declared-endpoints",
                        0,
                    )
                    + 1
                )
                continue

            def AccessCandidate(
                Position: Position3,
            ) -> ComponentTerminalAccessCandidate:
                Claims = (
                    Problem.ResourceGraph.BuildRouteClaims((Position,))
                    if Problem.ResourceGraph is not None
                    else RoutingResourceClaims(
                        WireCells=frozenset((Position,)),
                        SupportCells=frozenset(((
                            Position[0],
                            Position[1] - 1,
                            Position[2],
                        ),)),
                        ElectricalCells=frozenset(
                            DefaultRedstoneRoutingTechnology
                            .BuildElectricalExclusions({Position})
                        ),
                    )
                )
                return ComponentTerminalAccessCandidate(
                    CandidateFingerprint=_StableFingerprint((
                        "declared-feedthrough-endpoint",
                        _RelativeGeometry((Position,)),
                        _ClaimsFingerprint(Claims),
                    )),
                    Attachment=Position,
                    Path=(Position,),
                    Claims=Claims,
                    Layer=3,
                    Cost=1,
                )

            EntryCandidate = AccessCandidate(Entry)
            ExitCandidate = AccessCandidate(Exit)
            Domains = (
                ComponentTerminalAccessDomain(
                    Signal=Feedthrough.Signal,
                    Terminal=Entry,
                    TerminalRole="feedthrough-entry",
                    TerminalFingerprint=_StableFingerprint((
                        "feedthrough-entry",
                        _RelativeGeometry((Entry, Exit)),
                    )),
                    Candidates=(EntryCandidate,),
                ),
                ComponentTerminalAccessDomain(
                    Signal=Feedthrough.Signal,
                    Terminal=Exit,
                    TerminalRole="feedthrough-exit",
                    TerminalFingerprint=_StableFingerprint((
                        "feedthrough-exit",
                        _RelativeGeometry((Entry, Exit)),
                    )),
                    Candidates=(ExitCandidate,),
                ),
            )
            TransitProblem = replace(
                Problem,
                ComponentSignals=tuple(sorted((
                    *Problem.ComponentSignals,
                    Feedthrough.Signal,
                ))),
            )
            Candidate = _BuildNetVariant(
                TransitProblem,
                Feedthrough.Signal,
                Domains,
                (EntryCandidate, ExitCandidate),
                RejectionCounts=RejectionCounts,
                ImmutableConflictSignals=ImmutableConflictSignals,
                PrecomputedFabricSubtree=(
                    (ReservedPathNodeSet, ReservedPathEdges)
                    if ReservedPathNodes
                    else None
                ),
            )
            if (
                Candidate is not None
                and (
                    not ReservedPathNodes
                    or Candidate.Nodes == ReservedPathNodeSet
                )
                and (
                    Feedthrough.Claims is None
                    or Candidate.Claims == Feedthrough.Claims
                )
            ):
                Candidates.append(Candidate)
            elif Candidate is not None:
                RejectionCounts["reserved-path-identity-mismatch"] = (
                    RejectionCounts.get(
                        "reserved-path-identity-mismatch",
                        0,
                    )
                    + 1
                )
        DeltaX = max(
            (
                abs(Exit[0] - Entry[0])
                for Entry, Exit in Feedthrough.EndpointPairs
            ),
            default=0,
        )
        DeltaZ = max(
            (
                abs(Exit[2] - Entry[2])
                for Entry, Exit in Feedthrough.EndpointPairs
            ),
            default=0,
        )
        Axis = "X" if DeltaX >= DeltaZ else "Z"
        Retained = PruneDominatedComponentNetVariants(Candidates)
        Result.append(ComponentForeignTransitDomain(
            Signal=Feedthrough.Signal,
            PartitionAxis=Axis,
            PartitionFingerprint=_StableFingerprint((
                "declared-feedthrough-v1",
                tuple(sorted(
                    _RelativeGeometry((Entry, Exit))
                    for Entry, Exit in Feedthrough.EndpointPairs
                )),
                Feedthrough.Capacity,
                tuple(
                    _RelativeGeometry(Value.Nodes)
                    for Value in Retained
                ),
            )),
            Candidates=Retained,
            Complete=bool(Feedthrough.EndpointPairs),
            Diagnostics={
                "Mode": "declared-feedthrough",
                "DeclaredEndpointPairCount": len(
                    Feedthrough.EndpointPairs
                ),
                "CandidateCount": len(Retained),
                "Capacity": Feedthrough.Capacity,
                "RejectionCounts": dict(sorted(
                    RejectionCounts.items()
                )),
                "ImmutableConflictSignals": sorted(
                    ImmutableConflictSignals
                ),
                "ImplicitForeignTransitDomainCount": 0,
            },
        ))
    return tuple(sorted(
        Result,
        key=lambda Value: (
            Value.PartitionFingerprint,
            Value.PartitionAxis,
        ),
    ))

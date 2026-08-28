"""Foreign transit domains, net-variant pruning, and finite UNSAT subsets."""

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
from .Fabric import BuildComponentEgressPaths, _BuildAdjacency
from .NetPlanning import _BuildNetVariant
def BuildComponentForeignTransitDomains(
    Problem: ComponentRoutingProblem,
    Profiles: dict[str, Any],
    *,
    MaximumCandidatesPerSignal: int = 8,
) -> tuple[ComponentForeignTransitDomain, ...]:
    """Build finite through-component witnesses for geometrically split nets.

    A foreign net qualifies only when its terminals lie on both opposite
    sides of the routed component and their orthogonal span intersects the
    component.  The witness uses the existing finite component fabric and
    fixed egress macros; it is not a whole-design detour search.
    """
    if MaximumCandidatesPerSignal < 1:
        raise ValueError(
            "MaximumCandidatesPerSignal must be positive"
        )
    FabricNodes = frozenset(Problem.Fabric.Nodes)
    if not FabricNodes or not Problem.Fabric.Complete:
        return ()
    MinimumX = min(Position[0] for Position in FabricNodes)
    MaximumX = max(Position[0] for Position in FabricNodes)
    MinimumZ = min(Position[2] for Position in FabricNodes)
    MaximumZ = max(Position[2] for Position in FabricNodes)
    Adjacency = _BuildAdjacency(Problem.Fabric.Edges)
    Remaining = set(FabricNodes)
    FabricComponents: list[frozenset[Position3]] = []
    while Remaining:
        Start = min(Remaining)
        Visited = {Start}
        Pending = deque((Start,))
        while Pending:
            Current = Pending.popleft()
            for Neighbor in sorted(Adjacency.get(Current, ())):
                if Neighbor not in Visited:
                    Visited.add(Neighbor)
                    Pending.append(Neighbor)
        Component = frozenset(Visited)
        FabricComponents.append(Component)
        Remaining.difference_update(Component)

    def CandidateForPath(
        PathFromTerminal: tuple[Position3, ...],
    ) -> ComponentTerminalAccessCandidate:
        Claims = (
            Problem.ResourceGraph.BuildRouteClaims(PathFromTerminal)
            if Problem.ResourceGraph is not None
            else RoutingResourceClaims(
                WireCells=frozenset(PathFromTerminal),
                SupportCells=frozenset(
                    (X, Y - 1, Z)
                    for X, Y, Z in PathFromTerminal
                ),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions(set(
                        PathFromTerminal
                    ))
                ),
            )
        )
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=_StableFingerprint((
                _RelativeGeometry(PathFromTerminal),
                _ClaimsFingerprint(Claims),
            )),
            Attachment=PathFromTerminal[-1],
            Path=PathFromTerminal,
            Claims=Claims,
            Layer=3,
            Cost=len(PathFromTerminal),
        )

    Result = []
    ComponentSignals = frozenset(Problem.ComponentSignals)
    for Signal, Profile in sorted(Profiles.items()):
        if Signal in ComponentSignals:
            continue
        Terminals = tuple(dict.fromkeys((
            tuple(Profile.Root),
            *(tuple(Value) for Value in Profile.Targets),
        )))
        if len(Terminals) < 2:
            continue
        MinimumTerminalX = min(Value[0] for Value in Terminals)
        MaximumTerminalX = max(Value[0] for Value in Terminals)
        MinimumTerminalZ = min(Value[2] for Value in Terminals)
        MaximumTerminalZ = max(Value[2] for Value in Terminals)
        CenterX = (MinimumX + MaximumX) / 2.0
        CenterZ = (MinimumZ + MaximumZ) / 2.0
        AxisScores = []
        if (
            MinimumTerminalX < CenterX
            and MaximumTerminalX > CenterX
            and MinimumTerminalZ <= MaximumZ
            and MaximumTerminalZ >= MinimumZ
        ):
            AxisScores.append((
                -(
                    CenterX - MinimumTerminalX
                    + MaximumTerminalX - CenterX
                ),
                "X",
            ))
        if (
            MinimumTerminalZ < CenterZ
            and MaximumTerminalZ > CenterZ
            and MinimumTerminalX <= MaximumX
            and MaximumTerminalX >= MinimumX
        ):
            AxisScores.append((
                -(
                    CenterZ - MinimumTerminalZ
                    + MaximumTerminalZ - CenterZ
                ),
                "Z",
            ))
        CrossesComponentPartition = bool(AxisScores)
        if not AxisScores:
            ProjectedSpanX = max(
                0,
                min(MaximumTerminalX, MaximumX)
                - max(MinimumTerminalX, MinimumX),
            )
            ProjectedSpanZ = max(
                0,
                min(MaximumTerminalZ, MaximumZ)
                - max(MinimumTerminalZ, MinimumZ),
            )
            AxisScores.append((
                -max(ProjectedSpanX, ProjectedSpanZ),
                "X"
                if ProjectedSpanX >= ProjectedSpanZ
                else "Z",
            ))
        _Score, Axis = min(AxisScores)
        AxisIndex = 0 if Axis == "X" else 2
        GlobalMinimum = MinimumX if Axis == "X" else MinimumZ
        GlobalMaximum = MaximumX if Axis == "X" else MaximumZ
        RootCoordinate = Profile.Root[AxisIndex]
        SourceSide = (
            "minimum"
            if RootCoordinate
            <= ((GlobalMinimum + GlobalMaximum) / 2.0)
            else "maximum"
        )
        Variants = []
        RejectionCounts: dict[str, int] = {}
        ImmutableConflictSignals: set[str] = set()
        AttemptedCandidateCount = 0
        for FabricComponent in sorted(
            FabricComponents,
            key=lambda Value: (
                len(Value),
                tuple(sorted(Value)),
            ),
        ):
            ComponentIngressNodes = tuple(
                Value
                for Value in Problem.Fabric.IngressNodes
                if Value in FabricComponent
            )
            if len(ComponentIngressNodes) < 2:
                continue
            ComponentMinimum = min(
                Value[AxisIndex] for Value in ComponentIngressNodes
            )
            ComponentMaximum = max(
                Value[AxisIndex] for Value in ComponentIngressNodes
            )
            TerminalMinimum = (
                MinimumTerminalX
                if Axis == "X"
                else MinimumTerminalZ
            )
            TerminalMaximum = (
                MaximumTerminalX
                if Axis == "X"
                else MaximumTerminalZ
            )
            DesiredMinimum = max(
                ComponentMinimum,
                min(ComponentMaximum, TerminalMinimum),
            )
            DesiredMaximum = max(
                ComponentMinimum,
                min(ComponentMaximum, TerminalMaximum),
            )
            MinimumAttachmentCoordinate = (
                ComponentMinimum
                if CrossesComponentPartition
                else min(
                    (
                        abs(Value[AxisIndex] - DesiredMinimum),
                        Value[AxisIndex],
                    )
                    for Value in ComponentIngressNodes
                )[1]
            )
            MaximumAttachmentCoordinate = (
                ComponentMaximum
                if CrossesComponentPartition
                else min(
                    (
                        abs(Value[AxisIndex] - DesiredMaximum),
                        Value[AxisIndex],
                    )
                    for Value in ComponentIngressNodes
                )[1]
            )
            if (
                MinimumAttachmentCoordinate
                == MaximumAttachmentCoordinate
            ):
                continue
            if ComponentMinimum == ComponentMaximum:
                continue
            MinimumAttachments = tuple(
                Value
                for Value in ComponentIngressNodes
                if (
                    Value[AxisIndex]
                    == MinimumAttachmentCoordinate
                )
            )
            MaximumAttachments = tuple(
                Value
                for Value in ComponentIngressNodes
                if (
                    Value[AxisIndex]
                    == MaximumAttachmentCoordinate
                )
            )
            for MinimumAttachment in MinimumAttachments:
                MinimumPaths = tuple(
                    tuple(reversed(Path))
                    for Path in BuildComponentEgressPaths(
                        MinimumAttachment
                    )
                    if Path[-1][AxisIndex] < MinimumAttachment[AxisIndex]
                )
                for MaximumAttachment in MaximumAttachments:
                    MaximumPaths = tuple(
                        tuple(reversed(Path))
                        for Path in BuildComponentEgressPaths(
                            MaximumAttachment
                        )
                        if (
                            Path[-1][AxisIndex]
                            > MaximumAttachment[AxisIndex]
                        )
                    )
                    for MinimumPath in MinimumPaths:
                        for MaximumPath in MaximumPaths:
                            MinimumCandidate = CandidateForPath(
                                MinimumPath
                            )
                            MaximumCandidate = CandidateForPath(
                                MaximumPath
                            )
                            Domains = (
                                ComponentTerminalAccessDomain(
                                    Signal=Signal,
                                    Terminal=MinimumPath[0],
                                    TerminalRole=(
                                        "source"
                                        if SourceSide == "minimum"
                                        else "target"
                                    ),
                                    TerminalFingerprint=_StableFingerprint((
                                        "transit-minimum",
                                        _RelativeGeometry(MinimumPath),
                                    )),
                                    Candidates=(MinimumCandidate,),
                                ),
                                ComponentTerminalAccessDomain(
                                    Signal=Signal,
                                    Terminal=MaximumPath[0],
                                    TerminalRole=(
                                        "source"
                                        if SourceSide == "maximum"
                                        else "target"
                                    ),
                                    TerminalFingerprint=_StableFingerprint((
                                        "transit-maximum",
                                        _RelativeGeometry(MaximumPath),
                                    )),
                                    Candidates=(MaximumCandidate,),
                                ),
                            )
                            TransitProblem = replace(
                                Problem,
                                ComponentSignals=tuple(sorted((
                                    *Problem.ComponentSignals,
                                    Signal,
                                ))),
                            )
                            AttemptedCandidateCount += 1
                            Variant = _BuildNetVariant(
                                TransitProblem,
                                Signal,
                                Domains,
                                (
                                    MinimumCandidate,
                                    MaximumCandidate,
                                ),
                                RejectionCounts=RejectionCounts,
                                ImmutableConflictSignals=(
                                    ImmutableConflictSignals
                                ),
                            )
                            if Variant is not None:
                                Variants.append(Variant)
                            if (
                                len(Variants)
                                >= MaximumCandidatesPerSignal
                            ):
                                break
                        if len(Variants) >= MaximumCandidatesPerSignal:
                            break
                    if len(Variants) >= MaximumCandidatesPerSignal:
                        break
                if len(Variants) >= MaximumCandidatesPerSignal:
                    break
            if len(Variants) >= MaximumCandidatesPerSignal:
                break
        Candidates = PruneDominatedComponentNetVariants(Variants)
        PartitionFingerprint = _StableFingerprint((
            Axis,
            tuple(sorted(
                (
                    -1
                    if Value[AxisIndex] < GlobalMinimum
                    else 1
                    if Value[AxisIndex] > GlobalMaximum
                    else 0,
                    Value[
                        2 if Axis == "X" else 0
                    ] - (
                        MinimumZ if Axis == "X" else MinimumX
                    ),
                )
                for Value in Terminals
            )),
            tuple(
                _RelativeGeometry(Candidate.Nodes)
                for Candidate in Candidates
            ),
        ))
        Result.append(ComponentForeignTransitDomain(
            Signal=Signal,
            PartitionAxis=Axis,
            PartitionFingerprint=PartitionFingerprint,
            Candidates=Candidates,
            Complete=True,
            Diagnostics={
                "Mode": (
                    "through-component"
                    if CrossesComponentPartition
                    else "boundary-parallel"
                ),
                "AttemptedCandidateCount": AttemptedCandidateCount,
                "RejectionCounts": dict(sorted(
                    RejectionCounts.items()
                )),
                "ImmutableConflictSignals": sorted(
                    ImmutableConflictSignals
                ),
            },
        ))
    return tuple(sorted(
        Result,
        key=lambda Value: (
            Value.PartitionFingerprint,
            Value.PartitionAxis,
        ),
    ))

def PruneDominatedComponentNetVariants(
    Variants: Iterable[RoutedComponentNet],
) -> tuple[RoutedComponentNet, ...]:
    """Remove physically larger variants with identical external semantics."""
    Retained: list[RoutedComponentNet] = []
    for Candidate in sorted(
        Variants,
        key=lambda Value: (
            len(Value.Claims.ResourceIds),
            len(Value.Nodes),
            len(Value.Repeaters),
            Value.NetFingerprint,
        ),
    ):
        Dominated = any(
            Existing.ExportedPorts == Candidate.ExportedPorts
            and Existing.Claims.WireCells
            <= Candidate.Claims.WireCells
            and Existing.Claims.SupportCells
            <= Candidate.Claims.SupportCells
            and Existing.Claims.RequiredAirCells
            <= Candidate.Claims.RequiredAirCells
            and Existing.Claims.ElectricalCells
            <= Candidate.Claims.ElectricalCells
            for Existing in Retained
        )
        if not Dominated:
            Retained.append(Candidate)
    return tuple(sorted(
        Retained,
        key=lambda Value: Value.NetFingerprint,
    ))


def FindCompleteComponentNetUnsatSubset(
    VariantsBySignal: dict[str, tuple[RoutedComponentNet, ...]],
    *,
    Advance: Callable[[], bool] | None = None,
) -> tuple[str, ...] | None:
    """Prove that a complete subset of component nets cannot coexist.

    Returning a signal tuple is an exact monotone proof: adding the remaining
    component variables cannot make an already-unsatisfiable complete subset
    feasible. ``()`` means the subset is feasible, while ``None`` means the
    bounded proof was interrupted.
    """
    Ordered = tuple(sorted(
        VariantsBySignal.items(),
        key=lambda Value: (
            len(Value[1]),
            tuple(
                Variant.NetFingerprint
                for Variant in Value[1]
            ),
        ),
    ))
    if any(not Variants for _Signal, Variants in Ordered):
        return tuple(
            Signal for Signal, Variants in Ordered if not Variants
        )
    CompatibilityCache: dict[
        tuple[int, int, int],
        frozenset[int],
    ] = {}

    def CompatibleIndexes(
        FirstIndex: int,
        OptionIndex: int,
        SecondIndex: int,
    ) -> frozenset[int]:
        Key = (FirstIndex, OptionIndex, SecondIndex)
        Cached = CompatibilityCache.get(Key)
        if Cached is not None:
            return Cached
        FirstSignal, FirstVariants = Ordered[FirstIndex]
        SecondSignal, SecondVariants = Ordered[SecondIndex]
        FirstClaims = FirstVariants[OptionIndex].Claims
        Result = frozenset(
            SecondOptionIndex
            for SecondOptionIndex, SecondVariant
            in enumerate(SecondVariants)
            if ComponentClaimsCompatibleForOwners(
                FirstSignal,
                FirstClaims,
                SecondSignal,
                SecondVariant.Claims,
            )
        )
        CompatibilityCache[Key] = Result
        return Result

    Interrupted = object()

    def Enforce(
        Domains: dict[int, tuple[int, ...]],
    ) -> dict[int, tuple[int, ...]] | object:
        Result = dict(Domains)
        Queue = deque(
            (FirstIndex, SecondIndex)
            for FirstIndex in Result
            for SecondIndex in Result
            if FirstIndex != SecondIndex
        )
        while Queue:
            FirstIndex, SecondIndex = Queue.popleft()
            SecondDomain = frozenset(Result[SecondIndex])
            Retained = []
            for OptionIndex in Result[FirstIndex]:
                if Advance is not None and not Advance():
                    return Interrupted
                if SecondDomain.intersection(CompatibleIndexes(
                    FirstIndex,
                    OptionIndex,
                    SecondIndex,
                )):
                    Retained.append(OptionIndex)
            RetainedDomain = tuple(Retained)
            if RetainedDomain == Result[FirstIndex]:
                continue
            Result[FirstIndex] = RetainedDomain
            if not RetainedDomain:
                return Result
            Queue.extend(
                (OtherIndex, FirstIndex)
                for OtherIndex in Result
                if (
                    OtherIndex != FirstIndex
                    and OtherIndex != SecondIndex
                )
            )
        return Result

    def Search(
        Domains: dict[int, tuple[int, ...]],
    ) -> bool | object:
        Consistent = Enforce(Domains)
        if Consistent is Interrupted:
            return Interrupted
        assert isinstance(Consistent, dict)
        if any(not Domain for Domain in Consistent.values()):
            return False
        Unassigned = tuple(
            (len(Domain), VariableIndex, Domain)
            for VariableIndex, Domain in Consistent.items()
            if len(Domain) > 1
        )
        if not Unassigned:
            return True
        _Size, SelectedIndex, SelectedDomain = min(Unassigned)
        for OptionIndex in SelectedDomain:
            NextDomains = dict(Consistent)
            NextDomains[SelectedIndex] = (OptionIndex,)
            Result = Search(NextDomains)
            if Result is Interrupted or Result:
                return Result
        return False

    Result = Search({
        VariableIndex: tuple(range(len(Variants)))
        for VariableIndex, (_Signal, Variants)
        in enumerate(Ordered)
    })
    if Result is Interrupted:
        return None
    if Result:
        return ()
    return tuple(Signal for Signal, _Variants in Ordered)

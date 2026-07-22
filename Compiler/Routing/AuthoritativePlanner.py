"""Rust-backed authoritative portal generation and exact route assignment."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from math import ceil, sqrt
from dataclasses import dataclass, replace
import os
from time import monotonic
from typing import Any, Callable

try:
    from ..RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
    except Exception:
        RustRoutingContext = None

from ..Placement.Rotation import RotatedCellSize
from .Actions import (
    BuildPhysicalGraphs,
    MaterializeReservedRepeaters,
    PruneRedundantRepeaterReservations,
    ValidatePhysicalRoutes,
)
from .ChannelPlanner import (
    BuildNetRoutingProfiles,
    CandidateLanes,
    ChannelPlan,
    MeasureRoutingStage,
    RasterizeChannelSegment,
    RoutingIterationMetrics,
    NegotiatedRoutePlan,
)
from .Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from .Models import RoutedDesign, RoutingResources
from .Reliability import (
    BuildRoutingDeadlineDiagnostics,
    BuildStableFingerprint,
    ChooseRoutingEscalationAction,
    EnforceRoutingRuntimeLimit,
    HasAdaptiveEscalationBudget,
    RemainingRoutingRuntimeMilliseconds,
    RetainUnaffectedCandidateCache,
    SelectBoundedDiverseCandidatePool,
    RoutingDeadline,
    RoutingEscalationState,
)
from .Policy import DefaultPhysicalDesignPolicy, PhysicalDesignPolicy
from .ResourceGraph import (
    FindClaimConflicts,
    FindSelfClaimConflicts,
    IndexedRoutingResourceGraph,
    LocalRouteClaim,
    NetRouteCandidate,
    NormalizeRoutingEdge,
    PinAccessPortal,
    PortalReservation,
    RoutingAssignment,
    RoutingReservation,
    RoutingResourceId,
    RoutingResourceKind,
    RoutingResourceClaims,
    ValidateLocalRouteClaims,
)
from ..Placement.Geometry import GetGateInputAccess
from .Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from .TrackAssignment import AssignedTrack, TrackAssignment
from .LocalFirst import (
    BuildCapacityAwareGuidePlan,
    DeriveRoutingBudget,
    EstimateRoutingDemand,
)

Position2 = tuple[int, int]
Position3 = tuple[int, int, int]


@dataclass(frozen=True)
class RepeatedWorkTransition:
    """Bounded response when an escalation reproduces identical work."""

    Action: str
    SkipStrictPortalReservation: bool
    Deadline: RoutingDeadline


def CandidateRequestWindowOffset(
    InitialRequestsPerSignal: int,
    CandidateGrowthFactor: int,
    LayerCount: int,
    CandidateDiversityLevel: int,
) -> int:
    """Return the per-layer request offset not covered by earlier passes."""
    return sum(
        ceil(
            InitialRequestsPerSignal
            * CandidateGrowthFactor ** PreviousLevel
            / LayerCount
        )
        for PreviousLevel in range(CandidateDiversityLevel)
    )


def CandidatePortalShapeRank(
    Variant: int,
    AxisIndex: int,
    LaneIndex: int,
    LayerIndex: int,
    PortalVariantCount: int,
    LaneCount: int,
    RequestWindowOffset: int,
) -> int:
    """Rank portal starts before alternate axes/lanes, rotating on retries."""
    ShapeCount = max(1, PortalVariantCount * 2 * max(1, LaneCount))
    ShapeIndex = LaneIndex + max(1, LaneCount) * (
        Variant + PortalVariantCount * AxisIndex
    )
    return (
        ShapeIndex - RequestWindowOffset - LayerIndex
    ) % ShapeCount


@dataclass(frozen=True)
class RawPortalGeometryCache:
    """Immutable native portal work reusable across routing-control retries."""

    PlacedIdentity: int
    ResourcesIdentity: int
    Region: Any
    LayerCount: int
    PortalLimit: int
    PortalVariantCounts: tuple[tuple[str, int], ...]
    GuideExpansion: int
    StrictMaximumExpansions: int
    Context: Any
    PortalEntries: tuple[
        tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
    ]
    RequestCount: int
    TargetCount: int
    StarvationCount: int

    def Matches(
        self,
        Placed: Any,
        Resources: RoutingResources,
        Region: Any,
        LayerCount: int,
        PortalLimit: int,
        PortalVariantCounts: dict[str, int],
        GuideExpansion: int,
        StrictMaximumExpansions: int,
    ) -> bool:
        RegionIsCompatible = self.Region is Region or (
            getattr(self.Region, "Bounds", None)
            == getattr(Region, "Bounds", None)
            and getattr(self.Region, "Nodes", frozenset())
            <= getattr(Region, "Nodes", frozenset())
            and getattr(self.Region, "Edges", frozenset())
            <= getattr(Region, "Edges", frozenset())
        )
        return (
            self.PlacedIdentity == id(Placed)
            and self.ResourcesIdentity == id(Resources)
            and RegionIsCompatible
            and self.LayerCount == LayerCount
            and self.PortalLimit == PortalLimit
            and self.PortalVariantCounts
            == tuple(sorted(PortalVariantCounts.items()))
            and self.GuideExpansion == GuideExpansion
            and self.StrictMaximumExpansions == StrictMaximumExpansions
        )

    def BuildPortalDictionary(
        self,
    ) -> dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]]:
        return dict(self.PortalEntries)


@dataclass(frozen=True)
class PreparedPortalDomainCache:
    """Reserved or unreserved portal domain for one unchanged raw geometry."""

    RawPortalCache: RawPortalGeometryCache
    UnreservedPortalMode: bool
    ReservationVariant: int
    PortalEntries: tuple[
        tuple[tuple[str, Position3, int], tuple[PinAccessPortal, ...]], ...
    ]
    Reservations: tuple[PortalReservation, ...]

    def Matches(
        self,
        RawPortalCache: RawPortalGeometryCache,
        UnreservedPortalMode: bool,
        ReservationVariant: int,
    ) -> bool:
        return (
            self.RawPortalCache is RawPortalCache
            and self.UnreservedPortalMode == UnreservedPortalMode
            and self.ReservationVariant == ReservationVariant
        )

    def BuildPortalDictionary(
        self,
    ) -> dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]]:
        return dict(self.PortalEntries)


def ChooseRepeatedWorkTransition(
    UnreservedPortalMode: bool,
    Deadline: RoutingDeadline,
) -> RepeatedWorkTransition:
    """Try unreserved portals once before terminating duplicate work."""
    if UnreservedPortalMode:
        return RepeatedWorkTransition(
            Action="Terminate",
            SkipStrictPortalReservation=True,
            Deadline=Deadline,
        )
    return RepeatedWorkTransition(
        Action="TryUnreservedPortals",
        SkipStrictPortalReservation=True,
        Deadline=Deadline,
    )


def SelectAuthoritativeBaseClaims(
    AllLocalClaims: tuple[LocalRouteClaim, ...],
    DisableLocalBaseClaims: bool,
) -> tuple[LocalRouteClaim, ...]:
    """Preserve every enabled placement-owned claim for exact assignment."""
    return () if DisableLocalBaseClaims else AllLocalClaims


def _CollectSignalTargets(Placed: Any) -> dict[str, tuple[Position3, ...]]:
    """Collect required route targets for every driven signal.

    This is the same terminal map used for routing profiles, including module
    outputs via OUTPUT gate inputs.
    """
    Targets: dict[str, set[Position3]] = defaultdict(set)
    for Gate in Placed.PlacedGates:
        for InputIndex, Signal in enumerate(Gate.Inputs):
            try:
                Pin, _Direction = GetGateInputAccess(Gate, InputIndex)
            except Exception:
                continue
            Targets[Signal].add(Pin)
    return {Signal: tuple(sorted(Positions)) for Signal, Positions in Targets.items()}


def GrowAssignmentExpansionLimit(
    CurrentLimit: int,
    MaximumLimit: int,
    GrowthFactor: int,
) -> int:
    """Grow exact-assignment work smoothly without exceeding its budget."""
    if CurrentLimit < 1 or MaximumLimit < 1 or GrowthFactor < 2:
        raise ValueError("assignment growth controls must be positive and growing")
    return min(MaximumLimit, CurrentLimit * GrowthFactor)


def ShouldGrowAssignmentBudget(Result: Any) -> bool:
    """Grow MRV work only when Rust stopped at its explicit work ceiling."""
    return bool(
        getattr(Result, "BudgetExhausted", False)
        and not getattr(Result, "DeadlineExceeded", False)
    )


def ShouldRunShapeOptimization(QualityTarget: str) -> bool:
    """Keep first-legal routing focused on correctness and bounded completion."""
    return QualityTarget != "first-legal"


def _ClaimsConflict(
    FirstSignal: str,
    First: Any,
    SecondSignal: str,
    Second: Any,
) -> bool:
    """Apply exact resource and electrical conflict rules to two claim sets."""
    # Keep this check strict but cheap: support material may not replace another
    # signal's wire or required air, in addition to redstone interference.
    return bool(
        First.WireCells & Second.WireCells
        or First.SupportCells & (
            Second.WireCells | Second.RequiredAirCells
        )
        or Second.SupportCells & (
            First.WireCells | First.RequiredAirCells
        )
        or First.RequiredAirCells & Second.WireCells
        or Second.RequiredAirCells & First.WireCells
        or First.ElectricalCells & Second.WireCells
        or Second.ElectricalCells & First.WireCells
    )


def ReserveBoundaryPortals(
    Portals: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    ReservationVariant: int = 0,
    MaximumExpansions: int = 50_000,
    RequireConflictFree: bool = False,
    StrictTerminalThreshold: int = 64,
) -> tuple[
    dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]],
    tuple[PortalReservation, ...],
]:
    """Allocate one escape stem for every terminal on each layer.

    Pin access is a placement boundary, not a detailed-routing alternative.  A
    route candidate may choose its trunk later, but it must not be allowed to
    choose a foreign-conflicting stem on the way out of a cell.
    For large terminal sets, exact search is demand-capped with deterministic
    greedy fallback so routing does not stall on huge combinatorial reservations.
    """
    if MaximumExpansions < 1:
        raise ValueError("MaximumExpansions must be positive")

    def _ConflictCount(
        Signal: str,
        Candidate: PinAccessPortal,
        ReservedClaims: dict[str, list[RoutingResourceClaims]],
    ) -> int:
        return sum(
            1
            for OtherSignal, ExistingValues in ReservedClaims.items()
            if OtherSignal != Signal
            for Existing in ExistingValues
            if _ClaimsConflict(Signal, Candidate.Claims, OtherSignal, Existing)
        )

    TerminalLayers: dict[tuple[str, Position3], list[int]] = defaultdict(list)
    TerminalCandidateCounts: Counter[tuple[str, Position3]] = Counter()
    for (Signal, Terminal, Layer), Values in Portals.items():
        TerminalLayers[(Signal, Terminal)].append(Layer)
        TerminalCandidateCounts[(Signal, Terminal)] += len(Values)
    EmptyTerminal = next(
        (
            Key
            for Key in sorted(TerminalLayers)
            if TerminalCandidateCounts[Key] == 0
        ),
        None,
    )
    if EmptyTerminal is not None:
        Signal, Terminal = EmptyTerminal
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoBoundaryEscape,
                Stage="PortalReservation",
                AffectedNets=(Signal,),
                Detail="no boundary-portal geometry available on any layer",
                Diagnostics={
                    "Signal": Signal,
                    "Terminal": list(Terminal),
                    "Layers": sorted(TerminalLayers[EmptyTerminal]),
                    "PortalCandidates": 0,
                },
            )
        )

    # Keep empty per-layer domains in the returned mapping. Candidate
    # construction indexes every physical layer and deliberately skips a
    # layer unless all terminals of the signal can reach it. An inaccessible
    # individual layer is not a no-escape failure when another layer remains.
    Filtered: dict[tuple[str, Position3, int], tuple[PinAccessPortal, ...]] = {
        Key: () for Key, Values in Portals.items() if not Values
    }
    Reservations: list[PortalReservation] = []
    KeysByLayer: dict[int, list[tuple[str, Position3, int]]] = defaultdict(list)
    for Key in Portals:
        KeysByLayer[Key[2]].append(Key)
    for Layer in sorted(KeysByLayer):
        Domains = {
            Key: tuple(
                Value for Value in sorted(
                    Portals[Key], key=lambda Value: (Value.Cost, Value.PortalId)
                ) if Value.Path
            )
            for Key in sorted(KeysByLayer[Layer], key=lambda Value: (Value[0], Value[1]))
            if Portals[Key]
        }
        if not Domains:
            continue

        # Exact assignment is expensive and can dominate runtime on larger designs.
        # Enable strict reservation only when demand is bounded.
        StrictMode = RequireConflictFree and (len(Domains) <= StrictTerminalThreshold)
        Selections: dict[tuple[str, Position3, int], PinAccessPortal] = {}
        ReservedClaims: dict[str, list[RoutingResourceClaims]] = defaultdict(list)
        ExpansionCount = 0

        def CompatibleValues(
            Key: tuple[str, Position3, int],
        ) -> tuple[PinAccessPortal, ...]:
            Signal = Key[0]
            Values = tuple(
                Value for Value in Domains[Key]
                if not any(
                    _ClaimsConflict(Signal, Value.Claims, OtherSignal, Existing)
                    for OtherSignal, ExistingValues in ReservedClaims.items()
                    if OtherSignal != Signal
                    for Existing in ExistingValues
                )
            )
            if not Values:
                return ()
            Offset = ReservationVariant % len(Values)
            return (*Values[Offset:], *Values[:Offset])

        def AssignEscapes() -> bool:
            nonlocal ExpansionCount
            if len(Selections) == len(Domains):
                return True
            Available = [
                (len(Values), Key, Values)
                for Key in Domains
                if Key not in Selections
                for Values in (CompatibleValues(Key),)
            ]
            if not Available:
                return False
            _Count, Key, Values = min(
                Available,
                key=lambda Value: (Value[0], Value[1][0], Value[1][1]),
            )
            if not Values:
                return False
            Signal = Key[0]
            for Value in Values:
                ExpansionCount += 1
                if ExpansionCount > MaximumExpansions:
                    return False
                Selections[Key] = Value
                ReservedClaims[Signal].append(Value.Claims)
                if AssignEscapes():
                    return True
                ReservedClaims[Signal].pop()
                if not ReservedClaims[Signal]:
                    del ReservedClaims[Signal]
                del Selections[Key]
            return False

        if StrictMode:
            if not AssignEscapes():
                Unassigned = sorted(Key for Key in Domains if Key not in Selections)
                Affected = tuple(sorted({Key[0] for Key in Unassigned}))
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoBoundaryEscape,
                        Stage="PortalReservation",
                        AffectedNets=Affected,
                        Detail=(
                            "no conflict-free complete pin-escape assignment "
                            f"within {MaximumExpansions} deterministic expansions"
                        ),
                        Diagnostics={
                            "Layer": Layer,
                            "TerminalCount": len(Domains),
                            "ExpansionCount": ExpansionCount,
                            "MaximumExpansions": MaximumExpansions,
                            "UnassignedTerminals": [
                                {"Signal": Key[0], "Terminal": list(Key[1])}
                                for Key in Unassigned[:16]
                            ],
                        },
                    )
                )
        else:
            # Deterministic least-conflict assignment for bounded throughput.
            for Key in sorted(
                Domains,
                key=lambda Value: (len(Domains[Value]), Value[0], Value[1]),
            ):
                Signal = Key[0]
                OrderedValues = sorted(
                    Domains[Key],
                    key=lambda Value: (
                        _ConflictCount(Signal, Value, ReservedClaims),
                        Value.Cost,
                        Value.PortalId,
                    ),
                )
                # Greedy reservation is the production path for larger
                # terminal sets.  Rotate its deterministic preference order
                # as well as the exact-search path so a reservation retry
                # changes physical portal ownership instead of repeating the
                # same work under a different control label.
                Selected = OrderedValues[
                    ReservationVariant % len(OrderedValues)
                ]
                Selections[Key] = Selected
                ReservedClaims[Signal].append(Selected.Claims)

        for Key in sorted(Selections, key=lambda Value: (Value[0], Value[1])):
            Selected = Selections[Key]
            Signal, Terminal, _ = Key
            Filtered[Key] = (Selected,)
            Reservations.append(PortalReservation(
                Signal=Signal,
                Terminal=Terminal,
                Layer=Layer,
                SlotIndex=0,
                PortalId=Selected.PortalId,
                Claims=Selected.Claims,
            ))
    return Filtered, tuple(Reservations)


def BuildRoutingConflictGraph(
    CandidatesBySignal: dict[str, list[NetRouteCandidate]],
    Result: Any,
    ResourcePositions: tuple[Position3, ...],
    Reservations: tuple[PortalReservation, ...],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Classify an assignment failure without circuit-specific knowledge."""
    Signals = tuple(sorted(CandidatesBySignal))
    NoCandidateSignals = [
        Signal for Signal in Signals if not CandidatesBySignal[Signal]
    ]
    PairwiseEdges = []
    TotalSignalPairs = len(Signals) * max(0, len(Signals) - 1) // 2
    CompletedSignalPairs = 0
    CandidatePairChecks = 0
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "start",
            "CompletedSignalPairs": 0,
            "TotalSignalPairs": TotalSignalPairs,
            "CandidatePairChecks": 0,
        })
    for Index, FirstSignal in enumerate(Signals):
        for SecondSignal in Signals[Index + 1:]:
            Compatible = False
            for First in CandidatesBySignal[FirstSignal]:
                for Second in CandidatesBySignal[SecondSignal]:
                    CandidatePairChecks += 1
                    if WorkCheck is not None:
                        WorkCheck({
                            "Phase": "candidate-pairs",
                            "CompletedSignalPairs": CompletedSignalPairs,
                            "TotalSignalPairs": TotalSignalPairs,
                            "CandidatePairChecks": CandidatePairChecks,
                        })
                    if not _ClaimsConflict(
                        FirstSignal,
                        First.Claims,
                        SecondSignal,
                        Second.Claims,
                    ):
                        Compatible = True
                        break
                if Compatible:
                    break
            if not Compatible:
                PairwiseEdges.append([FirstSignal, SecondSignal])
            CompletedSignalPairs += 1
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "signal-pairs",
                    "CompletedSignalPairs": CompletedSignalPairs,
                    "TotalSignalPairs": TotalSignalPairs,
                    "CandidatePairChecks": CandidatePairChecks,
                })
    Hotspots = [
        list(ResourcePositions[Index])
        for Index in sorted(set(getattr(Result, "ConflictResourceIndices", ())))
        if 0 <= Index < len(ResourcePositions)
    ]
    NativeConflictSignals = sorted({
        str(Signal)
        for Signal in getattr(Result, "ConflictSignals", ())
    })
    if len(NativeConflictSignals) >= 3:
        Classification = "higher-order-placement-conflict"
    elif PairwiseEdges:
        Classification = "pairwise-incompatibility"
    elif NoCandidateSignals:
        Classification = "no-candidate"
    elif getattr(Result, "BudgetExhausted", False):
        Classification = "work-budget-exhaustion"
    else:
        Classification = "larger-matching-failure"
    ConflictSignals = sorted({
        *NoCandidateSignals,
        *(
            Signal
            for Pair in PairwiseEdges
            for Signal in Pair
        ),
        *NativeConflictSignals,
        *(
            (str(Result.FailureNet),)
            if getattr(Result, "FailureNet", None)
            else ()
        ),
    })
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "complete",
            "CompletedSignalPairs": CompletedSignalPairs,
            "TotalSignalPairs": TotalSignalPairs,
            "CandidatePairChecks": CandidatePairChecks,
        })
    return {
        "Classification": Classification,
        "FailureNet": getattr(Result, "FailureNet", None),
        "BudgetExhausted": bool(getattr(Result, "BudgetExhausted", False)),
        "ExpansionCount": int(getattr(Result, "ExpansionCount", 0)),
        "CandidateCounts": {
            Signal: len(CandidatesBySignal[Signal]) for Signal in Signals
        },
        "NoCandidateSignals": NoCandidateSignals,
        "NativeConflictSignals": NativeConflictSignals,
        "ConflictSignals": ConflictSignals,
        "PairwiseIncompatibleEdges": PairwiseEdges,
        "ResourceHotspots": Hotspots,
        "PortalReservations": [Value.ToDictionary() for Value in Reservations],
    }


def SelectPlacementRelocationSignals(
    ConflictGraph: dict[str, object],
    MaximumSignals: int = 3,
) -> list[str]:
    """Select the smallest high-pressure net set for physical relocation."""
    if MaximumSignals < 1:
        return []
    Scores: Counter[str] = Counter()
    Edges = ConflictGraph.get("PairwiseIncompatibleEdges", ())
    if isinstance(Edges, tuple | list):
        for Pair in Edges:
            if not isinstance(Pair, tuple | list) or len(Pair) != 2:
                continue
            Scores.update(str(Signal) for Signal in Pair)
    if Scores:
        return sorted(
            Scores,
            key=lambda Signal: (
                -Scores[Signal],
                0 if Signal.startswith("NandNet") else 1,
                Signal,
            ),
        )[:MaximumSignals]
    for Key in (
        "NativeConflictSignals",
        "NoCandidateSignals",
        "ConflictSignals",
    ):
        Values = ConflictGraph.get(Key, ())
        if isinstance(Values, tuple | list) and Values:
            return sorted({str(Value) for Value in Values})[:MaximumSignals]
    return []


def _BuildGuide(
    Terminals: tuple[Position2, ...],
    Axis: str,
    Lane: int,
) -> frozenset[Position2]:
    Result: set[Position2] = set()
    if Axis == "X":
        Minimum = min(Position[0] for Position in Terminals)
        Maximum = max(Position[0] for Position in Terminals)
        Result.update(RasterizeChannelSegment((Minimum, Lane), (Maximum, Lane)))
        for X, Z in Terminals:
            Result.update(RasterizeChannelSegment((X, Z), (X, Lane)))
    else:
        Minimum = min(Position[1] for Position in Terminals)
        Maximum = max(Position[1] for Position in Terminals)
        Result.update(RasterizeChannelSegment((Lane, Minimum), (Lane, Maximum)))
        for X, Z in Terminals:
            Result.update(RasterizeChannelSegment((X, Z), (Lane, Z)))
    return frozenset(Result)


def _BuildTargetPortalBranches(
    TargetPortals: tuple[PinAccessPortal, ...],
) -> list[list[Position3]]:
    """Orient selected target escapes from their outer endpoint inward."""
    return [
        list(reversed(Portal.Path))
        for Portal in TargetPortals
    ]


def SelectGraphAccessStarts(
    AccessPath: tuple[Position3, ...],
    RegionNodes: frozenset[Position3],
) -> tuple[Position3, ...]:
    """Keep only terminal access cells represented by the routing graph."""
    return tuple(Position for Position in AccessPath if Position in RegionNodes)


def RequiredRoutingLayerCountForAccess(
    MinimumY: int,
    AccessPositions: frozenset[Position3],
    GuideExpansion: int,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> int:
    """Return the lowest layer count that can serve the highest terminal.

    A redstone stair changes elevation and horizontal position together. The
    terminal portal envelope can therefore bridge at most ``GuideExpansion``
    vertical cells before reaching a routing plane. This is a necessary,
    deterministic layer floor for vertically stacked placements.
    """
    if GuideExpansion < 0:
        raise ValueError("GuideExpansion cannot be negative")
    if not AccessPositions:
        return Technology.MinimumRoutingLayerCount
    HighestAccessY = max(Position[1] for Position in AccessPositions)
    LowestRoutingY = Technology.RoutingY(MinimumY, 0)
    RequiredRoutingY = max(
        LowestRoutingY,
        HighestAccessY - GuideExpansion,
    )
    AdditionalHeight = max(0, RequiredRoutingY - LowestRoutingY)
    return max(
        Technology.MinimumRoutingLayerCount,
        1 + ceil(AdditionalHeight / Technology.RoutingLayerPitch),
    )


def _PortalFromRust(
    Signal: str,
    Terminal: Position3,
    Layer: int,
    Value: Any,
    Resources: RoutingResources,
) -> PinAccessPortal:
    CandidatePath = tuple(Value.Path)
    if not CandidatePath:
        CandidatePath = (Value.Target,)
    return PinAccessPortal(
        PortalId=f"{Signal}:{Terminal}:{Layer}:{Value.PortalId}",
        Signal=Signal,
        Terminal=Terminal,
        Layer=Layer,
        Path=CandidatePath,
        Edges=frozenset(
            NormalizeRoutingEdge(First, Second)
            for First, Second in zip(CandidatePath, CandidatePath[1:])
        ),
        Claims=Resources.ResourceGraph.BuildRouteClaims(CandidatePath),
        Length=Value.Length,
        BendCount=Value.BendCount,
        ViaCount=Value.ViaCount,
        Cost=Value.Length + Value.BendCount * 10 + Value.ViaCount * 7,
    )


def _CountBends(Path: tuple[Position3, ...]) -> int:
    Directions = [
        (
            Second[0] - First[0],
            Second[1] - First[1],
            Second[2] - First[2],
        )
        for First, Second in zip(Path, Path[1:])
    ]
    return sum(First != Second for First, Second in zip(Directions, Directions[1:]))


def _BuildCandidateGraph(
    Nodes: set[Position3],
    Resources: Any,
) -> dict[Position3, list[Position3]]:
    Result = {Position: [] for Position in Nodes}
    for Position in sorted(Nodes):
        for Neighbor in DefaultRedstoneRoutingTechnology.NeighborPositions(Position):
            if Neighbor not in Nodes:
                continue
            if Resources.BuildPrimitive(Position, Neighbor) is not None:
                Result[Position].append(Neighbor)
    return Result


def _FindPath(
    Graph: dict[Position3, list[Position3]],
    Start: Position3,
    Target: Position3,
) -> tuple[Position3, ...]:
    Parents: dict[Position3, Position3 | None] = {Start: None}
    Pending = deque((Start,))
    while Pending and Target not in Parents:
        Current = Pending.popleft()
        for Neighbor in Graph.get(Current, ()):
            if Neighbor not in Parents:
                Parents[Neighbor] = Current
                Pending.append(Neighbor)
    if Target not in Parents:
        return ()
    Result = []
    Current: Position3 | None = Target
    while Current is not None:
        Result.append(Current)
        Current = Parents[Current]
    return tuple(reversed(Result))


def _FindComponentNodes(
    Graph: dict[Position3, list[Position3]],
    Start: Position3,
) -> set[Position3]:
    """Return the BFS component reachable from Start in Graph."""
    if Start not in Graph:
        return set()
    Result: set[Position3] = {Start}
    Pending = deque((Start,))
    while Pending:
        Current = Pending.popleft()
        for Neighbor in Graph.get(Current, ()):
            if Neighbor in Result:
                continue
            Result.add(Neighbor)
            Pending.append(Neighbor)
    return Result


def _ReserveRepeaters(
    Signal: str,
    Root: Position3,
    Targets: tuple[Position3, ...],
    Graph: dict[Position3, list[Position3]],
    Technology: RedstoneRoutingTechnology,
) -> tuple[tuple[RoutingReservation, ...], dict[Position3, tuple[Position3, ...]]]:
    Reserved: dict[Position3, RoutingReservation] = {}
    Paths = {}
    for Target in Targets:
        Path = _FindPath(Graph, Root, Target)
        if not Path:
            return (), {}
        Paths[Target] = Path
        LastRefresh = 0
        while len(Path) - 1 - LastRefresh >= Technology.MaximumUnrefreshedDustLength:
            Maximum = min(
                len(Path) - 2,
                LastRefresh + Technology.MaximumUnrefreshedDustLength - 1,
            )
            Candidates = []
            for Index in range(LastRefresh + 1, Maximum + 1):
                Previous, Current, Next = Path[Index - 1 : Index + 2]
                if (
                    Previous[1] == Current[1] == Next[1]
                    and (
                        Previous[0] == Current[0] == Next[0]
                        or Previous[2] == Current[2] == Next[2]
                    )
                ):
                    Candidates.append(Index)
            if not Candidates:
                return (), {}
            # Select the latest legal site.  The subsequent power validation
            # remains authoritative, while this avoids the old fixed safety
            # margin placing a repeater early on every long segment.
            Selected = max(Candidates)
            Position = Path[Selected]
            Next = Path[Selected + 1]
            Delta = (Next[0] - Position[0], Next[2] - Position[2])
            Facing = {
                (1, 0): "west",
                (-1, 0): "east",
                (0, 1): "north",
                (0, -1): "south",
            }[Delta]
            Reserved.setdefault(
                Position,
                RoutingReservation(
                    Signal=Signal,
                    Resource=RoutingResourceId(RoutingResourceKind.Wire, Position),
                    Position=Position,
                    Purpose="Repeater",
                    Facing=Facing,
                ),
            )
            LastRefresh = Selected
    Reservations = tuple(Reserved[Position] for Position in sorted(Reserved))
    Reservations = PruneRedundantRepeaterReservations(
        Root,
        Targets,
        Graph,
        Reservations,
        Technology,
    )
    return Reservations, Paths


def _MaterializeCandidate(
    Signal: str,
    Profile: Any,
    SourcePortal: PinAccessPortal,
    TargetPortals: tuple[PinAccessPortal, ...],
    Guide: frozenset[Position2],
    Layer: int,
    Axis: str,
    Lane: int,
    Variant: int,
    RoutedTree: list[Position3] | None,
    Region: Any,
    Resources: RoutingResources,
    Technology: RedstoneRoutingTechnology,
    LengthPenalty: int,
    BendPenalty: int = 0,
    ViaPenalty: int = 0,
    LayerPenalty: int = 0,
    GuideDeviationPenalty: int = 0,
    RepeaterPenalty: int = 2,
    RejectionCounts: Counter[str] | None = None,
) -> NetRouteCandidate | None:
    if RoutedTree is None:
        if RejectionCounts is not None:
            RejectionCounts["NoTree"] += 1
        return None
    Nodes = set(RoutedTree)
    SeedNodes = {
        Position
        for Claim in (Profile.Seed.LocalClaims if Profile.Seed is not None else ())
        for Position in Claim.Nodes
    }
    Nodes.update(Profile.SourceAccessPath)
    for Target, Portal in zip(Profile.Targets, TargetPortals):
        Nodes.update(Profile.TargetAccessPaths[Target])
    Claims = Resources.ResourceGraph.BuildRouteClaims(Nodes)
    if FindSelfClaimConflicts({Signal: Claims}):
        if RejectionCounts is not None:
            RejectionCounts["SelfClaimConflict"] += 1
        return None
    Graph = _BuildCandidateGraph(Nodes, Resources.ResourceGraph)
    RootComponent = _FindComponentNodes(Graph, Profile.Root)
    MissingPaths = [
        Target for Target in Profile.Targets
        if Target not in RootComponent
    ]
    MissingSeedNodes = sorted(SeedNodes - RootComponent)
    if os.environ.get("RCS_DEBUG_MATERIALIZE") and (MissingPaths or False):
        import os as _os_debug
        if _os_debug.environ.get("RCS_DEBUG_MATERIALIZE") == Signal:
            Starts = tuple(dict.fromkeys((*Profile.SourceAccessPath, *SourcePortal.Path)))
            Component = _FindComponentNodes(Graph, Profile.Root)
            MinX = min(Position[0] for Position in RoutedTree) if RoutedTree else None
            MaxX = max(Position[0] for Position in RoutedTree) if RoutedTree else None
            print(
                "[debug] authoritative: materialization connectivity failure "
                f"signal={Signal} root={Profile.Root} missing={tuple(MissingPaths)} "
                f"nodes={len(Nodes)} tree={len(RoutedTree)}",
                flush=True,
            )
            print(
                "[debug] authoritative: materialization bounds "
                f"x=({MinX},{MaxX}) y=({min(Position[1] for Position in RoutedTree)},"
                f"{max(Position[1] for Position in RoutedTree)}) "
                f"rootInTree={Profile.Root in Nodes} "
                f"rootComponent={len(Component)} startCount={len(Starts)}",
                flush=True,
            )
            print(
                f"[debug] authoritative: materialization starts={Starts}",
                flush=True,
            )
            print(
                "[debug] authoritative: materialization targetPaths=" +
                str({
                    Target: Profile.TargetAccessPaths[Target]
                    for Target in Profile.Targets
                }),
                flush=True,
            )
            print(
                "[debug] authoritative: materialization sourcePath=" +
                str(Profile.SourceAccessPath),
                flush=True,
            )
            print(
                "[debug] authoritative: routedTreePrefix="
                f"{tuple(sorted(RoutedTree))[:32]}",
                flush=True,
            )
    if MissingPaths or MissingSeedNodes:
        if RejectionCounts is not None:
            RejectionCounts["Disconnected"] += 1
        return None
    RepeaterReservations, Paths = _ReserveRepeaters(
        Signal,
        Profile.Root,
        Profile.Targets,
        Graph,
        Technology,
    )
    if len(Paths) != len(Profile.Targets):
        if RejectionCounts is not None:
            RejectionCounts["NoRepeater"] += 1
        return None
    Edges = frozenset(
        NormalizeRoutingEdge(Position, Neighbor)
        for Position, Neighbors in Graph.items()
        for Neighbor in Neighbors
        if Position < Neighbor
    )
    Length = len(Nodes)
    BendCount = sum(_CountBends(Path) for Path in Paths.values())
    ViaCount = sum(
        First[1] != Second[1]
        for Path in Paths.values()
        for First, Second in zip(Path, Path[1:])
    )
    SourcePortalId = SourcePortal.PortalId
    TargetPortalIds = tuple(
        Portal.PortalId for Portal in TargetPortals
    )
    TargetPortalSignature = "-".join(
        str(PortalId) for PortalId in TargetPortalIds
    )
    CandidateId = (
        f"{Signal}:L{Layer}:{Axis}:{Lane}:V{Variant}:"
        f"S{SourcePortalId}:T{TargetPortalSignature}"
    )
    IncrementalLength = len(Nodes - SeedNodes)
    IncrementalMaterialCost = (
        IncrementalLength * max(1, LengthPenalty)
        + BendCount * max(0, BendPenalty)
        + ViaCount * max(0, ViaPenalty)
        + Layer * max(0, LayerPenalty)
        + max(0, GuideDeviationPenalty)
        + len(RepeaterReservations) * max(0, RepeaterPenalty)
    )
    return NetRouteCandidate(
        CandidateId=CandidateId,
        Signal=Signal,
        SourcePortalId=SourcePortal.PortalId,
        TargetPortalIds={
            Target: Portal.PortalId
            for Target, Portal in zip(Profile.Targets, TargetPortals)
        },
        Nodes=frozenset(Nodes),
        Edges=Edges,
        Claims=Claims,
        Layer=Layer,
        Guide=Guide,
        RepeaterWaypoints=tuple(
            Reservation.Position for Reservation in RepeaterReservations
        ),
        MaterialCost=IncrementalMaterialCost,
        FootprintGrowth=len(Guide),
        Length=Length,
        BendCount=BendCount,
        ViaCount=ViaCount,
        IncrementalMaterialCost=IncrementalMaterialCost,
        IncrementalLength=IncrementalLength,
        SeedNodeCount=len(SeedNodes),
    )


def PlanNegotiatedRouteTrees(
    Context: Any,
    Profiles: dict[str, Any],
    RouteRequestsBySignal: dict[str, list[tuple[Any, ...]]],
    RouteMetadataBySignal: dict[str, list[tuple[Any, ...]]],
    Region: Any,
    Resources: RoutingResources,
    Technology: RedstoneRoutingTechnology,
    Policy: PhysicalDesignPolicy,
    Deadline: RoutingDeadline,
    AdaptiveExpiresAt: float,
    CheckRuntimeBudget: Callable[[str, dict[str, object] | None], None],
) -> NegotiatedRoutePlan:
    """Route one tree per net and negotiate exact Redstone claim conflicts."""
    Negotiated = Policy.NegotiatedRouting
    NodesByColumn: dict[Position2, list[Position3]] = defaultdict(list)
    for Position in Region.Nodes:
        NodesByColumn[(Position[0], Position[2])].append(Position)
    for Values in NodesByColumn.values():
        Values.sort()

    Selected: dict[str, NetRouteCandidate] = {}
    History: Counter[Position3] = Counter()
    ReroutedSignals: set[str] = set()
    Iterations: list[RoutingIterationMetrics] = []
    OverflowProgression: list[int] = []
    PreviousConflictCount: int | None = None
    StagnationCount = 0
    SignalOrder = tuple(sorted(
        Profiles,
        key=lambda Signal: (
            -Profiles[Signal].Fanout,
            -Profiles[Signal].Criticality,
            -Profiles[Signal].Span,
            Signal,
        ),
    ))

    def CandidateNodeCosts(Signal: str) -> list[tuple[Position3, int]]:
        Costs: Counter[Position3] = Counter(History)
        for OtherSignal, Candidate in Selected.items():
            if OtherSignal == Signal:
                continue
            Present = Negotiated.PresentConflictPenalty
            for Position in (
                Candidate.Claims.ElectricalCells
                | Candidate.Claims.SupportCells
                | Candidate.Claims.RequiredAirCells
            ):
                Costs[Position] += Present
            for X, Y, Z in (
                Candidate.Claims.WireCells
                | Candidate.Claims.RequiredAirCells
            ):
                Costs[(X, Y + 1, Z)] += Present
        Required = {
            Position
            for Path in (
                Profiles[Signal].SourceAccessPath,
                *Profiles[Signal].TargetAccessPaths.values(),
            )
            for Position in Path
        }
        return sorted(
            (Position, Cost)
            for Position, Cost in Costs.items()
            if Cost > 0 and Position not in Required
        )

    def RouteRequest(
        Signal: str,
        RequestIndex: int,
    ) -> NetRouteCandidate | None:
        Requests = RouteRequestsBySignal.get(Signal, ())
        MetadataValues = RouteMetadataBySignal.get(Signal, ())
        if not Requests or not MetadataValues:
            return None
        RequestIndex %= min(len(Requests), len(MetadataValues))
        (
            Starts,
            TargetBranches,
            AllowedColumns,
            RequiredNodes,
            BlockedNodeValues,
            PreferredColumns,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            MaximumExpansionCount,
        ) = Requests[RequestIndex]
        AllowedNodes = {
            Position
            for Column in AllowedColumns
            for Position in NodesByColumn.get(tuple(Column), ())
        }
        AllowedNodes.update(tuple(Position) for Position in RequiredNodes)
        CheckRuntimeBudget(
            "NegotiatedDetailedRouting",
            {"Signal": Signal, "RequestIndex": RequestIndex},
        )
        if not hasattr(Context, "GenerateRouteTreeWithCostsBounded"):
            raise ValueError(
                "negotiated routing requires the incremental Rust routing API"
            )
        RoutedTree = Context.GenerateRouteTreeWithCostsBounded(
            Starts,
            TargetBranches,
            sorted(AllowedNodes),
            BlockedNodeValues,
            PreferredColumns,
            CandidateNodeCosts(Signal),
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            MaximumExpansionCount,
            RemainingRoutingRuntimeMilliseconds(Deadline, AdaptiveExpiresAt),
        )
        SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant = (
            MetadataValues[RequestIndex]
        )
        return _MaterializeCandidate(
            Signal,
            Profiles[Signal],
            SourcePortal,
            TargetPortals,
            Guide,
            Layer,
            Axis,
            Lane,
            Variant,
            RoutedTree,
            Region,
            Resources,
            Technology,
            Policy.DetailedRouting.LengthPenalty,
            Policy.DetailedRouting.CandidateBendWeight,
            Policy.DetailedRouting.CandidateViaWeight,
            Policy.DetailedRouting.LayerPenalty,
            RepeaterPenalty=Policy.DetailedRouting.RepeaterPenalty,
        )

    ConflictSignals: tuple[str, ...] = SignalOrder
    FinalConflicts: dict[RoutingResourceId, tuple[str, ...]] = {}
    for PassIndex in range(Negotiated.MaximumIterations):
        CheckRuntimeBudget(
            "NegotiatedDetailedRouting",
            {"Iteration": PassIndex, "SelectedSignals": len(Selected)},
        )
        SignalsToRoute = SignalOrder if PassIndex == 0 else ConflictSignals
        for SignalIndex, Signal in enumerate(SignalsToRoute):
            Existing = Selected.pop(Signal, None)
            Best: NetRouteCandidate | None = None
            BestScore: tuple[int, int, str] | None = None
            RequestCount = len(RouteRequestsBySignal.get(Signal, ()))
            AttemptCount = min(4, RequestCount)
            for AttemptOffset in range(AttemptCount):
                Candidate = RouteRequest(
                    Signal,
                    PassIndex + SignalIndex + AttemptOffset,
                )
                if Candidate is None:
                    continue
                PairConflicts = sum(
                    _ClaimsConflict(
                        Signal,
                        Candidate.Claims,
                        OtherSignal,
                        Other.Claims,
                    )
                    for OtherSignal, Other in Selected.items()
                )
                Score = (
                    PairConflicts,
                    Candidate.MaterialCost,
                    Candidate.CandidateId,
                )
                if BestScore is None or Score < BestScore:
                    Best = Candidate
                    BestScore = Score
                if PairConflicts == 0:
                    break
            if Best is None:
                if Existing is not None:
                    Selected[Signal] = Existing
                    continue
                raise RoutingStageError(RoutingFailure(
                    Reason=RoutingFailureReason.RepeaterAccessInfeasible,
                    Stage="NegotiatedDetailedRouting",
                    AffectedNets=(Signal,),
                    Detail=(
                        "no portal-aware route tree with legal repeater access "
                        "was found in the negotiated sparse region"
                    ),
                    Diagnostics={
                        "RequestCount": RequestCount,
                        "Iteration": PassIndex,
                        "CachedNodeCount": Resources.ResourceGraph.CachedNodeCount,
                    },
                ))
            Selected[Signal] = Best
            if Existing is not None and Existing.CandidateId != Best.CandidateId:
                ReroutedSignals.add(Signal)

        FinalConflicts = FindClaimConflicts({
            Signal: Candidate.Claims
            for Signal, Candidate in Selected.items()
        })
        ConflictSignals = tuple(sorted({
            Signal
            for Signals in FinalConflicts.values()
            for Signal in Signals
        }))
        ConflictCount = len(FinalConflicts)
        OverflowProgression.append(ConflictCount)
        Iterations.append(RoutingIterationMetrics(
            Iteration=PassIndex + 1,
            Stage="Negotiated detailed routing",
            ConflictCount=ConflictCount,
            ReroutedNets=len(SignalsToRoute) if PassIndex else 0,
            AverageLength=(
                sum(Value.Length for Value in Selected.values())
                / max(1, len(Selected))
            ),
            BendCount=sum(Value.BendCount for Value in Selected.values()),
            ViaCount=sum(Value.ViaCount for Value in Selected.values()),
            ConflictSignals=ConflictSignals,
        ))
        if not FinalConflicts and len(Selected) == len(Profiles):
            return NegotiatedRoutePlan(
                SelectedCandidates=Selected,
                Iterations=tuple(Iterations),
                ReroutedSignals=tuple(sorted(ReroutedSignals)),
                OverflowProgression=tuple(OverflowProgression),
                CachedNodeCount=Resources.ResourceGraph.CachedNodeCount,
                CachedEdgeCount=Resources.ResourceGraph.CachedEdgeCount,
            )
        for Resource, Signals in FinalConflicts.items():
            Increment = Negotiated.HistoryIncrement * max(1, len(Signals) - 1)
            History[Resource.Position] += Increment
            for Neighbor in Technology.NeighborPositions(Resource.Position):
                History[Neighbor] += max(1, Increment // 2)
        if PreviousConflictCount is not None and ConflictCount >= PreviousConflictCount:
            StagnationCount += 1
        else:
            StagnationCount = 0
        PreviousConflictCount = ConflictCount
        if StagnationCount >= Negotiated.StagnationPassLimit:
            break

    Hotspots = tuple(sorted({Resource.Position for Resource in FinalConflicts}))
    raise RoutingStageError(RoutingFailure(
        Reason=RoutingFailureReason.DetailedCongestionUnresolved,
        Stage="NegotiatedDetailedRouting",
        AffectedNets=ConflictSignals,
        Locations=Hotspots[:32],
        RepairActions=("RelocateAffectedClusters", "ExpandCongestedCut"),
        Detail=(
            "negotiated route trees retained capacity-one conflicts after "
            f"{len(Iterations)} deterministic passes"
        ),
        Diagnostics={
            "Algorithm": "negotiated-route-trees-v1",
            "ConflictGraph": {
                "Classification": "detailed-congestion-cut",
                "ConflictSignals": list(ConflictSignals),
                "RelocationSignals": list(ConflictSignals),
                "ResourceHotspots": [list(Value) for Value in Hotspots[:32]],
            },
            "OverflowProgression": OverflowProgression,
            "CachedNodeCount": Resources.ResourceGraph.CachedNodeCount,
            "CachedEdgeCount": Resources.ResourceGraph.CachedEdgeCount,
        },
    ))


def RouteAuthoritativeResources(
    Placed: Any,
    Resources: RoutingResources,
    SearchMarginX: int,
    SearchMarginZ: int,
    MaximumRoutingHeight: int,
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
    ProgressCallback: Callable[[int, int], None] | None = None,
    DiagnosticCallback: Callable[
        [RoutingIterationMetrics, str | None], None
    ] | None = None,
    AdaptiveLayerFloor: int | None = None,
    SharedRoutingStarted: float | None = None,
    EscalationHistory: tuple[dict[str, object], ...] = (),
    ReservationVariant: int = 0,
    LaneDiversityLevel: int = 0,
    CandidateDiversityLevel: int = 0,
    SkipStrictPortalReservation: bool = False,
    EscalationStates: tuple[tuple[object, ...], ...] = (),
    Deadline: RoutingDeadline | None = None,
    RetainedCandidateCache: dict[
        str, tuple[NetRouteCandidate, ...]
    ] | None = None,
    RetainedCandidateMetadata: dict[
        str, dict[str, tuple[str, int, int, int]]
    ] | None = None,
    PriorCandidateCache: dict[
        str, tuple[NetRouteCandidate, ...]
    ] | None = None,
    PriorCandidateMetadata: dict[
        str, dict[str, tuple[str, int, int, int]]
    ] | None = None,
    RegenerateSignals: frozenset[str] = frozenset(),
    RawPortalCache: RawPortalGeometryCache | None = None,
    PreparedPortalCache: PreparedPortalDomainCache | None = None,
) -> RoutedDesign:
    """Generate portals and select complete capacity-one routes in Rust."""
    RoutingCallStarted = monotonic()
    if RustRoutingContext is None:
        raise ValueError("authoritative routing requires the Rust router")
    if Deadline is None:
        Deadline = RoutingDeadline.Start(Policy.RuntimeBudgetSeconds)
    RoutingStarted = (
        SharedRoutingStarted
        if SharedRoutingStarted is not None
        else monotonic()
    )
    PlacementRelocationDiagnostics = (
        (getattr(Placed, "LocalRouteDiagnostics", {}) or {}).get(
            "__PlacementRelocation__",
            {},
        )
    )
    PlacementWasRelocated = bool(PlacementRelocationDiagnostics)
    PlacementRecipeDiagnostics = (
        (getattr(Placed, "LocalRouteDiagnostics", {}) or {}).get(
            "__PlacementRecipe__",
            {},
        )
    )
    AllowRelocatedStarvationLaneRetry = (
        PlacementWasRelocated
        and isinstance(PlacementRecipeDiagnostics, dict)
        and (
            PlacementRecipeDiagnostics.get("SourceGenerator")
            != "row-beam-conflict-relocation"
            or (
                isinstance(PlacementRelocationDiagnostics, dict)
                and int(PlacementRelocationDiagnostics.get("Variant", 0)) >= 4
            )
        )
    )
    PlacementWasBroadlyRelocated = (
        isinstance(PlacementRelocationDiagnostics, dict)
        and (
            len(PlacementRelocationDiagnostics.get("PrioritySignals", ()))
            >= 3
            or len(PlacementRelocationDiagnostics.get("Clusters", ())) > 3
        )
    )
    if PlacementWasRelocated:
        SkipStrictPortalReservation = True
    StageTimings: dict[str, float] = {}
    EscalationState = (
        Policy.QualityTarget,
        AdaptiveLayerFloor if AdaptiveLayerFloor is not None else 0,
        ReservationVariant,
        LaneDiversityLevel,
        CandidateDiversityLevel,
        bool(Policy.AdaptiveRouting.Enabled),
        bool(SkipStrictPortalReservation),
        bool(Policy.GlobalRouting.EnableCapacityAwareGuides),
    )
    if EscalationState in EscalationStates:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.Stagnated,
                Stage="RoutingEscalation",
                Detail=(
                    "routing escalation requested a previously evaluated control "
                    "state; returning to placement without repeating work"
                ),
                Diagnostics={
                    "EscalationHistory": tuple(EscalationHistory),
                    "EscalationStates": [Value for Value in EscalationStates],
                    "EscalationState": EscalationState,
                },
            )
        )
    EscalationStates = (*EscalationStates, EscalationState)

    # The sixth unit is completed by RoutePcbAttempt only after cleanup and
    # compaction.  Keeping it outside this planner prevents a successful
    # assignment from being reported as a fully completed physical design.
    StageCount = 6
    WorkTelemetry: dict[str, object] = {
        "SignalCount": 0,
        "TerminalCount": 0,
        "PortalRequestCount": 0,
        "PortalTargetCount": 0,
        "RouteTreeRequestCount": 0,
        "CandidateDiversityLevel": CandidateDiversityLevel,
        "ReservationVariant": ReservationVariant,
        "LaneDiversityLevel": LaneDiversityLevel,
    }
    RuntimeEscalationState = RoutingEscalationState(
        PortalMode=(
            "unreserved"
            if not Policy.AdaptiveRouting.Enabled or SkipStrictPortalReservation
            else "reserved"
        ),
        ReservationVariant=ReservationVariant,
        LaneDiversityLevel=LaneDiversityLevel,
        CandidateDiversityLevel=CandidateDiversityLevel,
        EffectiveRoutingLayers=max(0, AdaptiveLayerFloor or 0),
        AssignmentBudget=0,
    )
    AssignmentRetryHistory: list[dict[str, object]] = []
    AdaptiveExpiresAt = Deadline.ExpiresAt

    def CurrentRuntimeBudgetDiagnostics(
        AdditionalDiagnostics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return BuildRoutingDeadlineDiagnostics(
            Deadline=Deadline,
            WorkTelemetry=WorkTelemetry,
            EscalationHistory=tuple((
                *EscalationHistory,
                *AssignmentRetryHistory,
            )),
            EscalationState=RuntimeEscalationState,
            StageTimingsSeconds=StageTimings,
            AdditionalDiagnostics=AdditionalDiagnostics,
        )

    def CheckRuntimeBudget(
        Stage: str,
        AdditionalDiagnostics: dict[str, object] | None = None,
    ) -> None:
        Current = monotonic()
        if Current < Deadline.ExpiresAt and Current < AdaptiveExpiresAt:
            return
        EnforceRoutingRuntimeLimit(
            Deadline=Deadline,
            AdaptiveStartedAt=RoutingStarted,
            AdaptiveExpiresAt=AdaptiveExpiresAt,
            Stage=Stage,
            Diagnostics=CurrentRuntimeBudgetDiagnostics(
                AdditionalDiagnostics
            ),
        )
    if ProgressCallback is not None:
        ProgressCallback(0, StageCount)
    DisableLocalBaseClaims = (
        bool(os.environ.get("RCS_DISABLE_LOCAL_BASE_CLAIMS"))
        or bool(os.environ.get("RCS_DISABLE_LOCAL_CLAIMS"))
    )
    AllLocalClaims = tuple(getattr(Placed, "LocalRouteClaims", ()) or ())
    SignalTargets = _CollectSignalTargets(Placed)
    LocalClaimsBySignal: dict[str, tuple[LocalRouteClaim, ...]] = defaultdict(tuple)
    for Claim in AllLocalClaims:
        LocalClaimsBySignal[Claim.Signal] = (
            *LocalClaimsBySignal[Claim.Signal],
            Claim,
        )
    LocalClaims = SelectAuthoritativeBaseClaims(
        AllLocalClaims,
        DisableLocalBaseClaims,
    )
    Profiles = BuildNetRoutingProfiles(
        Placed,
        AccessLength=Policy.Placement.PinEscapeLength,
    )
    Demand = EstimateRoutingDemand(Placed, Profiles)
    AdaptiveBudget = DeriveRoutingBudget(Demand, Policy, Technology)
    AdaptiveExpiresAt = min(
        Deadline.ExpiresAt,
        RoutingStarted + AdaptiveBudget.RuntimeSeconds,
    )
    CheckRuntimeBudget("RoutingBudget")
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
        print(
            "[debug] authoritative: adaptive budget "
            f"candidates={AdaptiveBudget.ClusterCellCeiling} "
            f"layers={AdaptiveBudget.LayerCount} "
            f"portals_per_terminal={AdaptiveBudget.PortalsPerTerminal} "
            f"lanes={AdaptiveBudget.LaneCount} "
            f"candidates_per_net={AdaptiveBudget.CandidatesPerNet} "
            f"candidate_expansions_per_net={AdaptiveBudget.CandidateExpansionsPerNet} "
            f"assignment_expansions={AdaptiveBudget.AssignmentExpansions} "
            f"runtime_seconds={AdaptiveBudget.RuntimeSeconds:.3f}",
            flush=True,
    )
    WorkTelemetry["SignalCount"] = len(Profiles)
    WorkTelemetry["TerminalCount"] = Demand.TerminalCount
    if LocalClaims:
        ValidateLocalRouteClaims(Resources.ResourceGraph, LocalClaims)
    if not Profiles:
        return RoutedDesign(
            Module=Placed.Module,
            PlacedGates=Placed.PlacedGates,
            Wires=[],
            Supports=[],
            Repeaters={},
            NetWires={},
            ZeroResourceConflicts=True,
        )
    # Portal-count diversity remains demand-derived. Vertically stacked
    # terminals may require a higher initial physical layer below; increasing
    # the number of portal variants as well would multiply independent search
    # dimensions without adding a new kind of pin escape.
    PortalLimit = (
        min(
            Policy.TrackAssignment.MaximumPortalsPerTerminal,
            AdaptiveBudget.PortalsPerTerminal,
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.TrackAssignment.MaximumPortalsPerTerminal
    )
    RouteLaneCount = (
        min(
            Policy.GlobalRouting.CandidateLaneCount,
            min(
                AdaptiveBudget.LaneCount,
                Policy.AdaptiveRouting.InitialLaneCount,
            )
            * Policy.AdaptiveRouting.LaneGrowthFactor ** LaneDiversityLevel,
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.GlobalRouting.CandidateLaneCount
    )
    RoutePortalVariantCounts = {
        Signal: (
            min(
                PortalLimit,
                AdaptiveBudget.PortalsPerTerminal,
            )
            if Policy.AdaptiveRouting.Enabled
            else (
                PortalLimit
            )
        )
        for Signal, Profile in Profiles.items()
    }
    MinimumX = min(Gate.X for Gate in Placed.PlacedGates)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Placed.PlacedGates
    )
    MinimumZ = min(Gate.Z for Gate in Placed.PlacedGates)
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Placed.PlacedGates
    )
    MinimumY = min(Gate.Y for Gate in Placed.PlacedGates)
    ReservedAccess = frozenset(
        Position
        for Profile in Profiles.values()
        for Path in (Profile.SourceAccessPath, *Profile.TargetAccessPaths.values())
        for Position in Path
    ) | frozenset(
        Position for Claim in LocalClaims for Position in Claim.Nodes
    )
    MaximumAccessY = max(
        (Position[1] for Position in ReservedAccess),
        default=MinimumY,
    )
    EffectiveRoutingHeight = max(
        MaximumRoutingHeight,
        MaximumAccessY - MinimumY,
    )
    RequiredAccessLayerCount = RequiredRoutingLayerCountForAccess(
        MinimumY,
        ReservedAccess,
        Policy.DetailedRouting.GuideExpansion,
        Technology,
    )
    RouteLayers = getattr(Placed, "RouteLayers", None) or {}
    PolicyLayerLimit = Policy.Placement.MaximumRoutingLayers
    MinimumLayerCount = (
        min(Technology.MinimumRoutingLayerCount, PolicyLayerLimit)
        if PolicyLayerLimit > 0
        else Technology.MinimumRoutingLayerCount
    )
    MaximumLayerCount = (
        min(Technology.MaximumRoutableLayerCount, PolicyLayerLimit)
        if PolicyLayerLimit > 0
        else Technology.MaximumRoutableLayerCount
    )
    # MaximumRoutingHeight is physical headroom, not a request to instantiate
    # every usable routing layer.  Starting with all available elevations made
    # portal work scale with box height (and caused the CLA portal explosion).
    # Adaptive routing starts at the minimum viable layer count and adds one
    # layer only after a concrete guide/assignment failure.
    HeightCapacity = max(
        MinimumLayerCount,
        (EffectiveRoutingHeight - 2) // Technology.RoutingLayerPitch,
    )
    EffectiveMaximumLayerCount = min(MaximumLayerCount, HeightCapacity)
    LayerCount = min(
        EffectiveMaximumLayerCount,
        max(
            MinimumLayerCount,
            RequiredAccessLayerCount,
            (
                AdaptiveBudget.LayerCount
                if Policy.AdaptiveRouting.Enabled
                else MinimumLayerCount
            ),
            AdaptiveLayerFloor or 0,
            max(RouteLayers.values(), default=0) + 1,
            (
                EffectiveMaximumLayerCount
                if "__PlacementRelocation__"
                in (getattr(Placed, "LocalRouteDiagnostics", {}) or {})
                else 0
            ),
        ),
    )
    RuntimeEscalationState = replace(
        RuntimeEscalationState,
        EffectiveRoutingLayers=LayerCount,
    )
    GuidePlanningStarted = monotonic()
    CoarsePlan = (
        BuildCapacityAwareGuidePlan(
            Profiles,
            LayerCount,
            MinimumX,
            MinimumZ,
            Policy.GlobalRouting,
            Technology,
            Policy.Placement.LocalFanoutDistance,
            WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
                "Guide",
                Diagnostics,
            ),
        )
        if Policy.GlobalRouting.EnableCapacityAwareGuides
        else None
    )
    StageTimings["GlobalGuidePlanning"] = monotonic() - GuidePlanningStarted
    CheckRuntimeBudget("Guide")
    if ProgressCallback is not None:
        ProgressCallback(1, StageCount)
    ActiveMaximumY = max(
        MaximumAccessY + 1,
        Technology.RoutingY(MinimumY, LayerCount - 1) + 1,
    )
    PhysicalMaximumY = MinimumY + EffectiveRoutingHeight
    Bounds = (
        MinimumX - SearchMarginX,
        MaximumX + SearchMarginX,
        MinimumY,
        min(PhysicalMaximumY, ActiveMaximumY),
        MinimumZ - SearchMarginZ,
        MaximumZ + SearchMarginZ,
    )
    AssignedColumns: set[Position2] = set()
    # The coarse planner has already selected and capacity-checked its lane.
    # SharedBoundaryEnvelope is a guide-selection cost envelope, not detailed
    # routing geometry.  Adding it here eagerly materialized a second, much
    # wider corridor.  Start with the selected lane and grow by one isolated
    # track per explicit lane-diversity escalation.  BuildRegion reuses the
    # preceding cached region when this domain grows.
    RegionExpansion = (
        Policy.DetailedRouting.GuideExpansion
        + Technology.TrackPitch * LaneDiversityLevel
    )
    for Signal, Profile in Profiles.items():
        if CoarsePlan is not None:
            BaseGuides = (CoarsePlan.Guides[Signal],)
        else:
            BaseGuides = ()
        TerminalColumns = tuple(
            (Path[-1][0], Path[-1][2])
            for Path in (
                Profile.SourceAccessPath,
                *Profile.TargetAccessPaths.values(),
            )
        )
        if Profile.Seed is not None and Profile.Seed.ContinuationNodes:
            TerminalColumns = tuple(dict.fromkeys((
                *((Position[0], Position[2]) for Position in Profile.Seed.ContinuationNodes),
                *TerminalColumns,
            )))
        for Axis in (("X", "Z") if not BaseGuides else ()):
            Coordinates = sorted(
                Z for _X, Z in TerminalColumns
            ) if Axis == "X" else sorted(X for X, _Z in TerminalColumns)
            Center = Coordinates[len(Coordinates) // 2]
            TrackAnchor = MinimumZ if Axis == "X" else MinimumX
            AlignedCenter = TrackAnchor + (
                (Center - TrackAnchor + Technology.TrackPitch // 2)
                // Technology.TrackPitch
            ) * Technology.TrackPitch
            BaseGuide = _BuildGuide(TerminalColumns, Axis, AlignedCenter)
            BaseGuides = (*BaseGuides, BaseGuide)
        for BaseGuide in BaseGuides:
            AssignedColumns.update(
                (GuideX + DeltaX, GuideZ + DeltaZ)
                for GuideX, GuideZ in BaseGuide
                for DeltaX in range(-RegionExpansion, RegionExpansion + 1)
                for DeltaZ in range(-RegionExpansion, RegionExpansion + 1)
                if abs(DeltaX) + abs(DeltaZ) <= RegionExpansion
            )
    ResourceGraphStarted = monotonic()
    Region = Resources.ResourceGraph.BuildRegion(
        Bounds,
        AllowedColumns=frozenset(AssignedColumns),
        AllowedAccess=ReservedAccess,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "ResourceGraph",
            Diagnostics,
        ),
    )
    StageTimings["ResourceGraph"] = monotonic() - ResourceGraphStarted
    if ProgressCallback is not None:
        ProgressCallback(2, StageCount)
    PortalStarted = monotonic()
    CheckRuntimeBudget("Portal")
    CacheMatches = bool(
        RawPortalCache is not None
        and RawPortalCache.Matches(
            Placed,
            Resources,
            Region,
            LayerCount,
            PortalLimit,
            RoutePortalVariantCounts,
            Policy.DetailedRouting.GuideExpansion,
            Policy.DetailedRouting.StrictMaximumExpansions,
        )
    )
    PortalRequests: list[tuple[Any, ...]] = []
    if CacheMatches:
        assert RawPortalCache is not None
        Context = (
            RawPortalCache.Context
            if RawPortalCache.Region is Region
            else RustRoutingContext(
                Bounds,
                (MinimumX, MaximumX, MinimumZ, MaximumZ),
                sorted(Region.Nodes),
                sorted(Region.Edges),
            )
        )
        RawPortals = RawPortalCache.BuildPortalDictionary()
        WorkTelemetry.update({
            "PortalCacheHit": True,
            "PortalRequestCount": RawPortalCache.RequestCount,
            "PortalTargetCount": RawPortalCache.TargetCount,
            "PortalStarvationFallbackCount": RawPortalCache.StarvationCount,
            "PortalCompletedWork": 0,
            "PortalBatchCount": 0,
        })
        EffectiveRawPortalCache = (
            RawPortalCache
            if RawPortalCache.Region is Region
            else replace(
                RawPortalCache,
                Region=Region,
                Context=Context,
            )
        )
    else:
        Context = RustRoutingContext(
            Bounds,
            (MinimumX, MaximumX, MinimumZ, MaximumZ),
            sorted(Region.Nodes),
            sorted(Region.Edges),
        )
        RawPortals: dict[
            tuple[str, Position3, int], tuple[PinAccessPortal, ...]
        ] = {}
        RegionNodeSet = frozenset(Region.Nodes)
        NodesByColumn: dict[Position2, list[Position3]] = defaultdict(list)
        for Position in Region.Nodes:
            NodesByColumn[(Position[0], Position[2])].append(Position)
        NodesByLayer: dict[int, tuple[Position3, ...]] = {
            Technology.RoutingY(MinimumY, LayerIndex): tuple(
                sorted(
                    Position
                    for Position in Region.Nodes
                    if Position[1]
                    == Technology.RoutingY(MinimumY, LayerIndex)
                )
            )
            for LayerIndex in range(LayerCount)
        }
        PortalStarvationCount = 0
        PortalRequestMetadata = []
        for Signal in sorted(Profiles):
            Profile = Profiles[Signal]
            TerminalPaths = (
                (Profile.Root, Profile.SourceAccessPath),
                *(
                    (Target, Profile.TargetAccessPaths[Target])
                    for Target in Profile.Targets
                ),
            )
            for Terminal, AccessPath in TerminalPaths:
                AccessColumns = {(X, Z) for X, _Y, Z in AccessPath}
                AllowedColumns = {
                    (AccessX + DeltaX, AccessZ + DeltaZ)
                    for AccessX, AccessZ in AccessColumns
                    for DeltaX in range(
                        -Policy.DetailedRouting.GuideExpansion,
                        Policy.DetailedRouting.GuideExpansion + 1,
                    )
                    for DeltaZ in range(
                        -Policy.DetailedRouting.GuideExpansion,
                        Policy.DetailedRouting.GuideExpansion + 1,
                    )
                    if abs(DeltaX) + abs(DeltaZ)
                    <= Policy.DetailedRouting.GuideExpansion
                }
                AllowedNodeSet = {
                    Position
                    for Column in AllowedColumns
                    for Position in NodesByColumn.get(Column, ())
                } | set(AccessPath)
                AllowedNodes = sorted(AllowedNodeSet)
                for Layer in range(LayerCount):
                    RoutingY = Technology.RoutingY(MinimumY, Layer)
                    PortalStarts = list(
                        SelectGraphAccessStarts(AccessPath, RegionNodeSet)
                    )
                    PortalAllowedNodes = list(AllowedNodes)
                    PortalTargets = sorted(
                        (
                            Position
                            for Position in AllowedNodes
                            if Position[1] == RoutingY
                        ),
                        key=lambda Position: (
                            min(
                                abs(Position[0] - AccessPosition[0])
                                + abs(Position[1] - AccessPosition[1])
                                + abs(Position[2] - AccessPosition[2])
                                for AccessPosition in AccessPath
                            ),
                            abs(Position[0] - AccessPath[-1][0]),
                            abs(Position[2] - AccessPath[-1][2]),
                            Position,
                        ),
                    )
                    if len(PortalTargets) == 0:
                        # Expand only the target envelope. Starts remain
                        # graph-valid terminal cells; unavailable layers do
                        # not produce detached or wrong-elevation fallbacks.
                        GlobalLayerTargets = list(
                            NodesByLayer.get(RoutingY, ())
                        )
                        if GlobalLayerTargets:
                            AccessTerminal = AccessPath[-1]
                            PortalAllowedNodes = list(sorted(
                                set(PortalAllowedNodes)
                                | set(GlobalLayerTargets)
                            ))
                            PortalTargets = sorted(
                                GlobalLayerTargets,
                                key=lambda Position: (
                                    abs(Position[0] - AccessTerminal[0])
                                    + abs(Position[2] - AccessTerminal[2]),
                                    abs(Position[0] - AccessTerminal[0]),
                                    abs(Position[2] - AccessTerminal[2]),
                                    Position,
                                ),
                            )
                            PortalStarvationCount += 1
                    MaxPortalTargets = max(
                        1,
                        min(
                            len(PortalTargets),
                            RoutePortalVariantCounts[Signal],
                        ),
                    )
                    PortalTargets = PortalTargets[:MaxPortalTargets]
                    PortalRequests.append((
                        list(PortalStarts),
                        PortalTargets,
                        PortalAllowedNodes,
                        RoutingY,
                        PortalLimit,
                        Policy.DetailedRouting.StrictMaximumExpansions,
                    ))
                    PortalRequestMetadata.append((Signal, Terminal, Layer))
        WorkTelemetry["PortalRequestCount"] = len(PortalRequests)
        WorkTelemetry["PortalTargetCount"] = sum(
            len(Request[1]) for Request in PortalRequests
        )
        WorkTelemetry["PortalStarvationFallbackCount"] = PortalStarvationCount
        WorkTelemetry["PortalCacheHit"] = False
        WorkTelemetry["PortalBatchCount"] = 1
        CheckRuntimeBudget("Portal")
        if hasattr(Context, "GeneratePortalCandidateBatchesBounded"):
            PortalBatchResult = Context.GeneratePortalCandidateBatchesBounded(
                PortalRequests,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ),
            )
            WorkTelemetry["PortalCompletedWork"] = (
                PortalBatchResult.CompletedWork
            )
            if PortalBatchResult.DeadlineExceeded:
                EnforceRoutingRuntimeLimit(
                    Deadline=Deadline,
                    AdaptiveStartedAt=RoutingStarted,
                    AdaptiveExpiresAt=AdaptiveExpiresAt,
                    Stage="Portal",
                    Diagnostics=CurrentRuntimeBudgetDiagnostics(),
                    NativeDeadlineExceeded=True,
                )
            PortalResults = PortalBatchResult.Candidates
        else:
            PortalResults = Context.GeneratePortalCandidateBatches(
                PortalRequests
            )
            WorkTelemetry["PortalCompletedWork"] = len(PortalResults)
        for (Signal, Terminal, Layer), Values in zip(
            PortalRequestMetadata,
            PortalResults,
        ):
            RawPortals[(Signal, Terminal, Layer)] = tuple(
                _PortalFromRust(Signal, Terminal, Layer, Value, Resources)
                for Value in Values
            )
        EffectiveRawPortalCache = RawPortalGeometryCache(
            PlacedIdentity=id(Placed),
            ResourcesIdentity=id(Resources),
            Region=Region,
            LayerCount=LayerCount,
            PortalLimit=PortalLimit,
            PortalVariantCounts=tuple(sorted(RoutePortalVariantCounts.items())),
            GuideExpansion=Policy.DetailedRouting.GuideExpansion,
            StrictMaximumExpansions=(
                Policy.DetailedRouting.StrictMaximumExpansions
            ),
            Context=Context,
            PortalEntries=tuple(sorted(RawPortals.items())),
            RequestCount=len(PortalRequests),
            TargetCount=int(WorkTelemetry["PortalTargetCount"]),
            StarvationCount=PortalStarvationCount,
        )

    # Compatibility mode preserves the classic behavior: every generated boundary
    # portal is available to the assignment solver so we do not pre-eliminate
    # legal combinations before exact capacity-one search.
    UnreservedPortalMode = (
        (not Policy.AdaptiveRouting.Enabled) or SkipStrictPortalReservation
    )
    PreparedCacheMatches = bool(
        PreparedPortalCache is not None
        and PreparedPortalCache.Matches(
            EffectiveRawPortalCache,
            UnreservedPortalMode,
            ReservationVariant,
        )
    )
    if PreparedCacheMatches:
        assert PreparedPortalCache is not None
        Portals = PreparedPortalCache.BuildPortalDictionary()
        PortalReservations = PreparedPortalCache.Reservations
        EffectivePreparedPortalCache = PreparedPortalCache
    else:
        Portals = dict(RawPortals)
        if UnreservedPortalMode:
            Portals = {
                Key: tuple(
                    sorted(
                        Value,
                        key=lambda Value: (Value.Cost, Value.PortalId),
                    )
                )
                for Key, Value in sorted(Portals.items())
            }
            PortalReservations = ()
        else:
            Portals, PortalReservations = ReserveBoundaryPortals(
                Portals,
                ReservationVariant=ReservationVariant,
                MaximumExpansions=AdaptiveBudget.AssignmentExpansions,
                RequireConflictFree=False,
                StrictTerminalThreshold=4,
            )
        EffectivePreparedPortalCache = PreparedPortalDomainCache(
            RawPortalCache=EffectiveRawPortalCache,
            UnreservedPortalMode=UnreservedPortalMode,
            ReservationVariant=ReservationVariant,
            PortalEntries=tuple(sorted(Portals.items())),
            Reservations=PortalReservations,
        )

    StageTimings["PortalGeneration"] = monotonic() - PortalStarted
    CheckRuntimeBudget("Portal")
    if ProgressCallback is not None:
        ProgressCallback(3, StageCount)
    CandidateStarted = monotonic()
    CandidateRequestCount = 0
    NegotiatedPlan: NegotiatedRoutePlan | None = None
    if Policy.NegotiatedRouting.Enabled:
        RetainedCandidateCache = None
        RetainedCandidateMetadata = None
        PriorCandidateCache = None
        PriorCandidateMetadata = None

    def GenerateRouteTreesWithDeadline(
        Requests: list[tuple[Any, ...]],
    ) -> list[Any]:
        CheckRuntimeBudget("Candidate")
        if hasattr(Context, "GenerateRouteTreesBounded"):
            BatchResult = Context.GenerateRouteTreesBounded(
                Requests,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ),
            )
            WorkTelemetry["RouteTreeCompletedWork"] = (
                int(WorkTelemetry.get("RouteTreeCompletedWork", 0))
                + BatchResult.CompletedWork
            )
            if BatchResult.DeadlineExceeded:
                EnforceRoutingRuntimeLimit(
                    Deadline=Deadline,
                    AdaptiveStartedAt=RoutingStarted,
                    AdaptiveExpiresAt=AdaptiveExpiresAt,
                    Stage="Candidate",
                    Diagnostics=CurrentRuntimeBudgetDiagnostics(),
                    NativeDeadlineExceeded=True,
                )
            return list(BatchResult.RouteTrees)
        Values = Context.GenerateRouteTrees(Requests)
        CheckRuntimeBudget("Candidate")
        return Values
    InitialRequestLimit = max(
        1,
        (
            Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal
            * Policy.AdaptiveRouting.CandidateGrowthFactor
            ** max(CandidateDiversityLevel, LaneDiversityLevel)
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
    )
    ActiveCandidateSignalCount = sum(
        1
        for Signal in Profiles
        if not (
            Signal not in RegenerateSignals
            and (RetainedCandidateCache or {}).get(Signal)
        )
    )
    ProvisionalCandidateRequestCount = max(
        1,
        InitialRequestLimit * ActiveCandidateSignalCount,
    )
    BaseCandidateExpansionLimit = (
        min(
            AdaptiveBudget.CandidateExpansionsPerNet,
            max(
                Policy.DetailedRouting.MinimumCandidateExpansionLimit,
                Policy.AdaptiveRouting.MaximumCandidateGenerationExpansions
                // ProvisionalCandidateRequestCount,
            ),
        )
        if Policy.AdaptiveRouting.Enabled
        else (
            Policy.DetailedRouting.StrictMaximumExpansions
            if not Profiles
            else max(
                Policy.DetailedRouting.MinimumCandidateExpansionLimit,
                min(
                    Policy.DetailedRouting.StrictMaximumExpansions,
                    12_000_000 // ProvisionalCandidateRequestCount,
                ),
            )
        )
    )
    CandidateExpansionLimits = {
        Signal: min(
            Policy.DetailedRouting.StrictMaximumExpansions,
            BaseCandidateExpansionLimit
            * max(1, (max(1, len(Profile.Targets)) - 1).bit_length()),
        )
        for Signal, Profile in Profiles.items()
    }
    CandidatesBySignal: dict[str, list[NetRouteCandidate]] = defaultdict(list)
    CandidateLimitsBySignal: dict[str, int] = {}
    CandidateDiagnostics: dict[str, dict[str, object]] = {}
    CandidateSignalOrder = sorted(
        Profiles,
        key=lambda Value: (
            -len(Profiles[Value].Targets),
            -max(
                abs(Profiles[Value].Root[0] - Target[0])
                + abs(Profiles[Value].Root[2] - Target[2])
                for Target in Profiles[Value].Targets
            ),
            Value,
        ),
    )
    ProtectedNodesBySignal = {
        Signal: frozenset(
            {
                *Profiles[Signal].SourceAccessPath,
                *(
                    Position
                    for Path in Profiles[Signal].TargetAccessPaths.values()
                    for Position in Path
                ),
                *(
                    Position
                    for Claim in LocalClaimsBySignal.get(Signal, ())
                    for Position in Claim.Nodes
                ),
            }
        )
        for Signal in Profiles
    }
    ForeignBlockedNodesBySignal = {
        Signal: frozenset(
            Technology.BuildElectricalExclusions(
                set().union(*(
                    ProtectedNodesBySignal[OtherSignal]
                    for OtherSignal in Profiles
                    if OtherSignal != Signal
                ))
            )
            - ProtectedNodesBySignal[Signal]
        )
        for Signal in Profiles
    }
    RouteRequestsBySignal = {}
    RouteMetadataBySignal = {}
    DeferredRouteRequestCountsBySignal: Counter[str] = Counter()
    ForeignPortalOverlapBySignal: Counter[str] = Counter()
    CandidateAxisLaneBySignal: dict[str, dict[str, tuple[str, int, int, int]]] = {}
    for Signal, Values in (PriorCandidateMetadata or {}).items():
        if Signal in Profiles:
            CandidateAxisLaneBySignal[Signal] = dict(Values)
    for Signal, Values in (RetainedCandidateCache or {}).items():
        if Signal in Profiles and Signal not in RegenerateSignals:
            CandidatesBySignal[Signal] = list(Values)
            CandidateAxisLaneBySignal[Signal] = dict(
                (RetainedCandidateMetadata or {}).get(Signal, {})
            )
    for Signal in CandidateSignalOrder:
        if CandidatesBySignal.get(Signal):
            RouteRequestsBySignal[Signal] = []
            RouteMetadataBySignal[Signal] = []
            continue
        Profile = Profiles[Signal]
        CandidateExpansionLimit = CandidateExpansionLimits[Signal]
        RouteRequests = []
        RouteMetadata = []
        RoutePriorities: list[tuple[object, ...]] = []
        RequestWindowOffset = CandidateRequestWindowOffset(
            Policy.AdaptiveRouting.InitialCandidateRequestsPerSignal,
            Policy.AdaptiveRouting.CandidateGrowthFactor,
            LayerCount,
            CandidateDiversityLevel,
        )
        UnreservedPerLayerRequestLimit = max(
            1,
            ceil(InitialRequestLimit / LayerCount),
        )
        LayerOrder = tuple(range(LayerCount))
        if CoarsePlan is not None:
            PlannedLayer = CoarsePlan.Layers[Signal]
            LayerOrder = (PlannedLayer,) + tuple(
                Layer for Layer in LayerOrder if Layer != PlannedLayer
            )
        for Layer in LayerOrder:
            LayerPriority = 0 if Layer == LayerOrder[0] else 1
            SourcePortals = Portals[(Signal, Profile.Root, Layer)]
            TargetPortalSets = [Portals[(Signal, Target, Layer)] for Target in Profile.Targets]
            if not SourcePortals or any(not Values for Values in TargetPortalSets):
                continue
            RoutingY = Technology.RoutingY(MinimumY, Layer)
            PhysicalPortalVariantCount = min(
                RoutePortalVariantCounts[Signal],
                max(
                    len(SourcePortals),
                    *(len(Values) for Values in TargetPortalSets),
                ),
            )
            for Variant in range(PhysicalPortalVariantCount):
                SourcePortal = SourcePortals[Variant % len(SourcePortals)]
                BaseTargetPortals = tuple(
                    Values[(Variant + Index) % len(Values)]
                    for Index, Values in enumerate(TargetPortalSets)
                )
                Terminals = tuple(
                    (Portal.Path[-1][0], Portal.Path[-1][2])
                    for Portal in (SourcePortal, *BaseTargetPortals)
                )
                XSpan = max(X for X, _Z in Terminals) - min(X for X, _Z in Terminals)
                ZSpan = max(Z for _X, Z in Terminals) - min(Z for _X, Z in Terminals)
                PreferredAxis = "X" if XSpan >= ZSpan else "Z"
                for AxisIndex, Axis in enumerate(
                    (PreferredAxis, "Z" if PreferredAxis == "X" else "X")
                ):
                    AxisPriority = (
                        0
                        if CoarsePlan is not None
                        and Axis == CoarsePlan.Axes[Signal]
                        else 1
                    )
                    Coordinates = sorted(
                        Z for _X, Z in Terminals
                    ) if Axis == "X" else sorted(X for X, _Z in Terminals)
                    Center = Coordinates[len(Coordinates) // 2]
                    TrackAnchor = MinimumZ if Axis == "X" else MinimumX
                    AlignedCenter = TrackAnchor + (
                        (
                            Center
                            - TrackAnchor
                            + Technology.TrackPitch // 2
                        )
                        // Technology.TrackPitch
                    ) * Technology.TrackPitch
                    if (
                        CoarsePlan is not None
                        and Axis == CoarsePlan.Axes[Signal]
                    ):
                        AlignedCenter = CoarsePlan.Lanes[Signal]
                    LaneValues = CandidateLanes(
                        AlignedCenter,
                        RouteLaneCount,
                        Technology.TrackPitch,
                    )
                    if LaneDiversityLevel:
                        PreviousLaneCount = min(
                            RouteLaneCount,
                            Policy.AdaptiveRouting.InitialLaneCount
                            * Policy.AdaptiveRouting.LaneGrowthFactor ** (
                                LaneDiversityLevel - 1
                            ),
                        )
                        PreviousLanes = frozenset(CandidateLanes(
                            AlignedCenter,
                            PreviousLaneCount,
                            Technology.TrackPitch,
                        ))
                        # Escalation must expose lanes not present in the
                        # previous pass.  Merely increasing the lane-count
                        # ceiling while retaining the centered prefix retries
                        # exactly the same four candidates.
                        LaneValues = tuple(sorted(
                            LaneValues,
                            key=lambda Value: (
                                0 if Value not in PreviousLanes else 1,
                                abs(Value - AlignedCenter),
                                Value,
                            ),
                        ))
                    if UnreservedPortalMode and len(Profiles) <= 32:
                        LaneValues = LaneValues[:1]
                    for LaneIndex, Lane in enumerate(LaneValues):
                        PortalShapeRank = (
                            CandidatePortalShapeRank(
                                Variant,
                                AxisIndex,
                                LaneIndex,
                                Layer,
                                PhysicalPortalVariantCount,
                                len(LaneValues),
                                RequestWindowOffset,
                            )
                            if UnreservedPortalMode
                            else Variant
                        )
                        if (
                            UnreservedPortalMode
                            and PortalShapeRank
                            >= UnreservedPerLayerRequestLimit
                        ):
                            DeferredRouteRequestCountsBySignal[Signal] += 1
                            continue
                        PortalPhase = 1 + AxisIndex * 3 + LaneIndex
                        TargetPortals = tuple(
                            Values[
                                (Variant + PortalPhase * (Index + 1))
                                % len(Values)
                            ]
                            for Index, Values in enumerate(TargetPortalSets)
                        )
                        PortalNodes = {
                            Position
                            for Portal in (SourcePortal, *TargetPortals)
                            for Position in Portal.Path
                        }
                        if PortalNodes & ForeignBlockedNodesBySignal[Signal]:
                            # Portal/access nodes are mandatory candidate
                            # ownership.  Passing one as both required and
                            # blocked makes the native request contradictory.
                            # Keep the overlap visible for diagnostics and let
                            # exact capacity-one assignment reject any actual
                            # inter-net electrical conflict.
                            ForeignPortalOverlapBySignal[Signal] += 1
                        Terminals = tuple(
                            (Portal.Path[-1][0], Portal.Path[-1][2])
                            for Portal in (SourcePortal, *TargetPortals)
                        )
                        Guide = _BuildGuide(Terminals, Axis, Lane)
                        IsPlannedGuide = (
                            CoarsePlan is not None
                            and Layer == CoarsePlan.Layers[Signal]
                            and Axis == CoarsePlan.Axes[Signal]
                            and Lane == CoarsePlan.Lanes[Signal]
                        )
                        GuideExpansion = (
                            Policy.GlobalRouting.IntraClusterEnvelope
                            if IsPlannedGuide and Signal in CoarsePlan.LocalSignals
                            else Policy.DetailedRouting.GuideExpansion
                        )
                        CandidateColumns = {
                            (GuideX + DeltaX, GuideZ + DeltaZ)
                            for GuideX, GuideZ in Guide
                            for DeltaX in range(-GuideExpansion, GuideExpansion + 1)
                            for DeltaZ in range(-GuideExpansion, GuideExpansion + 1)
                            if abs(DeltaX) + abs(DeltaZ) <= GuideExpansion
                        }
                        SeedNodeSet = {
                            Position
                            for Claim in (
                                Profile.Seed.LocalClaims
                                if Profile.Seed is not None
                                else ()
                            )
                            for Position in Claim.Nodes
                        }
                        RequiredNodeSet = (
                            SeedNodeSet
                            | set(Profile.SourceAccessPath)
                            | set(SourcePortal.Path)
                            | {
                                Position
                                for Target in Profile.Targets
                                for Position in Profile.TargetAccessPaths[Target]
                            }
                            | {
                                Position
                                for Portal in TargetPortals
                                for Position in Portal.Path
                            }
                        )
                        RequiredNodes = sorted(RequiredNodeSet)
                        SeedStarts = (
                            tuple(
                                Position
                                for Claim in Profile.Seed.LocalClaims
                                for Position in Claim.Nodes
                            )
                            if Profile.Seed is not None
                            else ()
                        )
                        RouteRequests.append(
                            (
                                list(
                                    dict.fromkeys(
                                        (
                                            *(
                                                SeedStarts
                                            ),
                                            *Profile.SourceAccessPath,
                                            *SourcePortal.Path,
                                        )
                                    )
                                ),
                                _BuildTargetPortalBranches(TargetPortals),
                                sorted(CandidateColumns),
                                RequiredNodes,
                                sorted(
                                    ForeignBlockedNodesBySignal[Signal]
                                    - RequiredNodeSet
                                ),
                                sorted(Guide),
                                RoutingY,
                                Policy.DetailedRouting.MinimumGuidePenalty,
                                Policy.DetailedRouting.StrictBendPenalty,
                                Policy.DetailedRouting.StrictViaPenalty,
                                CandidateExpansionLimit,
                            )
                        )
                        RouteMetadata.append(
                            (SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant)
                        )
                        # Keep the initial pool geometrically diverse: one
                        # axis/lane choice is insufficient to disprove a
                        # capacity-one conflict, while a broad portal-product
                        # search is unnecessarily expensive.
                        RoutePriorities.append((
                            PortalShapeRank,
                            LayerPriority,
                            LaneIndex,
                            AxisPriority,
                            Axis,
                            Lane,
                        ))
        OrderedRequests = sorted(
            zip(RoutePriorities, RouteRequests, RouteMetadata),
            key=lambda Value: (
                Value[0][0],  # new portal starts before repeated shapes
                Value[0][1],  # then layer diversity
                Value[0][2],  # then lane diversity
                Value[0][3],  # then axis preference
                Value[0][4],
                Value[0][5],
            ),
        )
        UniqueOrderedRequests = []
        SeenRequestGeometry: set[tuple[object, ...]] = set()
        for Priority, Request, Metadata in OrderedRequests:
            (
                SourcePortal,
                TargetPortals,
                Guide,
                Layer,
                Axis,
                Lane,
                _Variant,
            ) = Metadata
            RequestGeometry = (
                SourcePortal.PortalId,
                tuple(Portal.PortalId for Portal in TargetPortals),
                tuple(sorted(Guide)),
                Layer,
                Axis,
                Lane,
            )
            if RequestGeometry in SeenRequestGeometry:
                continue
            SeenRequestGeometry.add(RequestGeometry)
            UniqueOrderedRequests.append((Priority, Request, Metadata))
        RouteRequests = [Value[1] for Value in UniqueOrderedRequests]
        RouteMetadata = [Value[2] for Value in UniqueOrderedRequests]
        RouteRequestsBySignal[Signal] = RouteRequests
        RouteMetadataBySignal[Signal] = RouteMetadata

    if Policy.NegotiatedRouting.Enabled:
        NegotiatedPlan = PlanNegotiatedRouteTrees(
            Context,
            Profiles,
            RouteRequestsBySignal,
            RouteMetadataBySignal,
            Region,
            Resources,
            Technology,
            Policy,
            Deadline,
            AdaptiveExpiresAt,
            CheckRuntimeBudget,
        )
        CandidatesBySignal = defaultdict(
            list,
            {
                Signal: [Candidate]
                for Signal, Candidate in NegotiatedPlan.SelectedCandidates.items()
            },
        )
        WorkTelemetry["NegotiatedRouting"] = {
            "Algorithm": "negotiated-route-trees-v1",
            "Iterations": len(NegotiatedPlan.Iterations),
            "OverflowProgression": list(NegotiatedPlan.OverflowProgression),
            "ReroutedSignals": list(NegotiatedPlan.ReroutedSignals),
            "CachedNodeCount": NegotiatedPlan.CachedNodeCount,
            "CachedEdgeCount": NegotiatedPlan.CachedEdgeCount,
        }

    WorkTelemetry["CandidateRequestConstructionSeconds"] = round(
        monotonic() - CandidateStarted,
        6,
    )
    CandidateRequestCount = max(
        1,
        sum(
            min(len(RouteRequestsBySignal[Signal]), InitialRequestLimit)
            for Signal in CandidateSignalOrder
        ),
    )
    WorkTelemetry["InitialCandidateRequestsPerSignal"] = InitialRequestLimit
    WorkTelemetry["InitialRouteTreeRequestCount"] = CandidateRequestCount
    WorkTelemetry["RouteTreeRequestCount"] = CandidateRequestCount
    InitialRequestsBySignal: dict[str, list[tuple[Any, ...]]] = {}
    InitialMetadataBySignal: dict[str, list[tuple[Any, ...]]] = {}
    InitialResultSlices: dict[str, tuple[int, int]] = {}
    BatchedInitialRequests: list[tuple[Any, ...]] = []
    for Signal in CandidateSignalOrder:
        if CandidatesBySignal.get(Signal):
            continue
        SignalRequests = RouteRequestsBySignal[Signal][:InitialRequestLimit]
        SignalMetadata = RouteMetadataBySignal[Signal][:InitialRequestLimit]
        StartIndex = len(BatchedInitialRequests)
        BatchedInitialRequests.extend(SignalRequests)
        InitialRequestsBySignal[Signal] = SignalRequests
        InitialMetadataBySignal[Signal] = SignalMetadata
        InitialResultSlices[Signal] = (
            StartIndex,
            len(BatchedInitialRequests),
        )
    InitialNativeBatchStarted = monotonic()
    BatchedInitialTrees = (
        GenerateRouteTreesWithDeadline(BatchedInitialRequests)
        if BatchedInitialRequests
        else []
    )
    WorkTelemetry["InitialNativeCandidateBatchSeconds"] = round(
        monotonic() - InitialNativeBatchStarted,
        6,
    )
    RouteTreeBatchCount = 1 if BatchedInitialRequests else 0
    CandidateSignalRank = {
        Signal: Index for Index, Signal in enumerate(CandidateSignalOrder)
    }

    def InitialRoutedTreeCount(Signal: str) -> int:
        if CandidatesBySignal.get(Signal):
            return len(CandidatesBySignal[Signal])
        ResultStart, ResultEnd = InitialResultSlices[Signal]
        return sum(
            Value is not None
            for Value in BatchedInitialTrees[ResultStart:ResultEnd]
        )

    # Materialize successful signals before handling a starved signal. The
    # native batch has already completed, so this preserves its reusable work
    # and lets offender-only escalation inherit every unaffected candidate.
    CandidateMaterializationOrder = sorted(
        CandidateSignalOrder,
        key=lambda Signal: (
            -InitialRoutedTreeCount(Signal),
            CandidateSignalRank[Signal],
        ),
    )
    for Signal in CandidateMaterializationOrder:
        if CandidatesBySignal.get(Signal):
            CandidateLimitsBySignal[Signal] = len(CandidatesBySignal[Signal])
            CandidateDiagnostics[Signal] = {
                "Cached": True,
                "Requests": 0,
                "RoutedTrees": 0,
                "Materialized": len(CandidatesBySignal[Signal]),
                "DeferredRequests": 0,
                "SourcePortals": 0,
                "TargetPortals": 0,
                "Rejections": {},
            }
            continue
        Profile = Profiles[Signal]
        CandidateExpansionLimit = CandidateExpansionLimits[Signal]
        RouteRequests = RouteRequestsBySignal[Signal]
        RouteMetadata = RouteMetadataBySignal[Signal]
        InitialRouteRequests = InitialRequestsBySignal[Signal]
        InitialRouteMetadata = InitialMetadataBySignal[Signal]
        ResultStart, ResultEnd = InitialResultSlices[Signal]
        RoutedTrees = BatchedInitialTrees[ResultStart:ResultEnd]
        RejectionCounts: Counter[str] = Counter()
        CandidateDiagnostics[Signal] = {
            "Requests": len(InitialRouteRequests),
            "RoutedTrees": sum(Value is not None for Value in RoutedTrees),
            "Materialized": 0,
            "DeferredRequests": (
                DeferredRouteRequestCountsBySignal[Signal]
                + len(RouteRequests)
                - len(InitialRouteRequests)
            ),
            "SeedNodes": sum(
                len(Claim.Nodes)
                for Claim in (
                    Profile.Seed.LocalClaims if Profile.Seed is not None else ()
                )
            ),
            "SourcePortals": sum(
                len(Portals[(Signal, Profile.Root, Layer)])
                for Layer in range(LayerCount)
            ),
            "TargetPortals": sum(
                len(Portals[(Signal, Target, Layer)])
                for Target in Profile.Targets
                for Layer in range(LayerCount)
            ),
            "ForeignBlockedNodes": len(
                ForeignBlockedNodesBySignal[Signal]
            ),
            "ForeignPortalOverlapRequests": (
                ForeignPortalOverlapBySignal[Signal]
            ),
        }
        def MaterializeBatch(
            Trees: list[Any],
            MetadataValues: list[tuple[Any, ...]],
        ) -> None:
            for RoutedTree, Metadata in zip(Trees, MetadataValues):
                SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant = Metadata
                Candidate = _MaterializeCandidate(
                    Signal,
                    Profile,
                    SourcePortal,
                    TargetPortals,
                    Guide,
                    Layer,
                    Axis,
                    Lane,
                    Variant,
                    RoutedTree,
                    Region,
                    Resources,
                    Technology,
                    Policy.DetailedRouting.LengthPenalty,
                    Policy.DetailedRouting.CandidateBendWeight,
                    Policy.DetailedRouting.CandidateViaWeight,
                    Policy.DetailedRouting.LayerPenalty,
                    (
                        0
                        if CoarsePlan is None
                        else (
                            len(Guide.symmetric_difference(CoarsePlan.Guides[Signal]))
                            + (
                                0
                                if Layer == CoarsePlan.Layers[Signal]
                                else Policy.GlobalRouting.OverflowPenalty
                            )
                        ) * Policy.GlobalRouting.ExistingGuideHintWeight
                    ),
                    Policy.DetailedRouting.RepeaterPenalty,
                    RejectionCounts,
                )
                if Candidate is not None:
                    CandidatesBySignal[Signal].append(Candidate)
                    CandidateDiagnostics[Signal]["Materialized"] += 1
                    CandidateAxisLaneBySignal.setdefault(
                        Signal,
                        {},
                    )[Candidate.CandidateId] = (
                        Axis,
                        Lane,
                        Layer,
                        Candidate.SeedNodeCount,
                    )

        MaterializeBatch(RoutedTrees, InitialRouteMetadata)
        PriorValues = tuple((PriorCandidateCache or {}).get(Signal, ()))
        PriorCandidateIds = frozenset(
            Candidate.CandidateId for Candidate in PriorValues
        )
        ExistingCandidateIds = {
            Candidate.CandidateId for Candidate in CandidatesBySignal[Signal]
        }
        CandidatesBySignal[Signal].extend(
            Candidate
            for Candidate in PriorValues
            if Candidate.CandidateId not in ExistingCandidateIds
        )
        CandidateDiagnostics[Signal]["PriorCandidates"] = len(PriorValues)
        # Do not exhaust a signal's deferred request product inline.  A signal
        # with no materialized candidate is regenerated through the typed,
        # offender-only escalation path below; unaffected candidate sets and
        # raw portal geometry remain reusable under the same deadline.
        CandidateDiagnostics[Signal]["Rejections"] = dict(RejectionCounts)
        if CoarsePlan is None:
            def CandidateOrder(Value: NetRouteCandidate) -> tuple[Any, ...]:
                return (
                    Value.MaterialCost,
                    Value.FootprintGrowth,
                    -CandidateAxisLaneBySignal[Signal][Value.CandidateId][3],
                    Value.IncrementalMaterialCost,
                    Value.IncrementalLength,
                    Value.Length,
                    Value.BendCount,
                    Value.ViaCount,
                    Value.CandidateId,
                )
        else:
            PlannedAxis = CoarsePlan.Axes[Signal]
            PlannedLane = CoarsePlan.Lanes[Signal]
            PlannedLayer = CoarsePlan.Layers[Signal]

            def CandidateOrder(Value: NetRouteCandidate) -> tuple[Any, ...]:
                CandidateAxis, CandidateLane, CandidateLayer, SeedNodes = (
                    CandidateAxisLaneBySignal[Signal][Value.CandidateId]
                )
                return (
                    Value.MaterialCost,
                    0 if CandidateAxis == PlannedAxis else 1,
                    0 if CandidateLane == PlannedLane else 1,
                    0 if CandidateLayer == PlannedLayer else 1,
                    Value.FootprintGrowth,
                    -SeedNodes,
                    Value.IncrementalMaterialCost,
                    Value.IncrementalLength,
                    Value.Length,
                    Value.BendCount,
                    Value.ViaCount,
                    Value.CandidateId,
                )
        CandidatesByTrack: dict[
            tuple[int, frozenset[Position2]], list[NetRouteCandidate]
        ] = defaultdict(list)
        for Candidate in CandidatesBySignal[Signal]:
            Key = (Candidate.Layer, Candidate.Guide)
            CandidatesByTrack[Key].append(Candidate)
        for Values in CandidatesByTrack.values():
            Values.sort(key=CandidateOrder)
        MaximumCandidates = (
            min(
                Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
                AdaptiveBudget.CandidatesPerNet
                * Policy.AdaptiveRouting.CandidateGrowthFactor ** max(
                    0,
                    (AdaptiveLayerFloor or AdaptiveBudget.LayerCount)
                    - AdaptiveBudget.LayerCount,
                ),
            )
            if Policy.AdaptiveRouting.Enabled
            else Policy.TrackAssignment.MaximumRouteCandidatesPerNet
        )
        if Policy.AdaptiveRouting.Enabled:
            ClaimWork = max(1, Profile.Span) * max(1, len(Profile.Targets))
            WorkScale = max(
                1,
                ceil(
                    sqrt(
                        ClaimWork
                        / Policy.AdaptiveRouting.CandidateClaimWorkQuantum
                    )
                ),
            )
            SignalMaximumCandidates = max(
                Policy.AdaptiveRouting.MinimumCandidatesPerNet,
                min(
                    MaximumCandidates * max(1, WorkScale),
                    Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
                ),
            )
        else:
            SignalMaximumCandidates = min(
                len(CandidatesBySignal[Signal]),
                Policy.TrackAssignment.MaximumRouteCandidatesPerNet,
            )
        CandidateLimitsBySignal[Signal] = SignalMaximumCandidates
        PerLayer = max(1, SignalMaximumCandidates // LayerCount)
        DiverseCandidates = []
        if UnreservedPortalMode:
            # Retain every materialized candidate from the bounded batch.
            # Escalation widens the batch or changes lanes/layers under the
            # same absolute deadline instead of front-loading the full portal
            # product.
            for Layer in range(LayerCount):
                LayerTracks = sorted(
                    (
                        Values
                        for (TrackLayer, _Guide), Values in CandidatesByTrack.items()
                        if TrackLayer == Layer
                    ),
                    key=lambda Values: CandidateOrder(Values[0]),
                )
                for Values in LayerTracks:
                    DiverseCandidates.extend(Values)
        else:
            for Layer in range(LayerCount):
                LayerTracks = sorted(
                    (
                        Values
                        for (TrackLayer, _Guide), Values in CandidatesByTrack.items()
                        if TrackLayer == Layer
                    ),
                    key=lambda Values: CandidateOrder(Values[0]),
                )
                LayerValues = []
                VariantIndex = 0
                while len(LayerValues) < PerLayer:
                    Added = False
                    for Values in LayerTracks:
                        if VariantIndex < len(Values):
                            LayerValues.append(Values[VariantIndex])
                            Added = True
                            if len(LayerValues) == PerLayer:
                                break
                    if not Added:
                        break
                    VariantIndex += 1
                DiverseCandidates.extend(LayerValues)
        CandidatesBySignal[Signal] = SelectBoundedDiverseCandidatePool(
            DiverseCandidates,
            SignalMaximumCandidates,
            PriorCandidateIds,
        )
        if not CandidatesBySignal[Signal]:
            Rejections = CandidateDiagnostics[Signal].get("Rejections", {})
            RoutedTreeCount = int(
                CandidateDiagnostics[Signal].get("RoutedTrees", 0)
            )
            SeedRejectedEveryRoutedTree = bool(
                PlacementWasRelocated
                and LocalClaimsBySignal.get(Signal)
                and RoutedTreeCount > 0
                and sum(
                    int(Rejections.get(Reason, 0))
                    for Reason in ("SelfClaimConflict", "NoRepeater")
                )
                >= RoutedTreeCount
            )
            CandidateFailureFingerprint = BuildStableFingerprint({
                "Signal": Signal,
                "Diagnostics": CandidateDiagnostics[Signal],
                "PortalReservations": [
                    Value.ToDictionary() for Value in PortalReservations
                ],
                "LayerCount": LayerCount,
                "LaneCount": RouteLaneCount,
            })

            def RetryCandidateGeneration(
                Action: str,
                *,
                NextReservationVariant: int = ReservationVariant,
                NextLaneDiversityLevel: int = LaneDiversityLevel,
                NextCandidateDiversityLevel: int = CandidateDiversityLevel,
                NextLayerFloor: int | None = AdaptiveLayerFloor,
                NextSkipStrictPortalReservation: bool = (
                    SkipStrictPortalReservation
                ),
            ) -> RoutedDesign:
                if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                    print(
                        "[debug] authoritative: retrying candidate generation "
                        f"signal={Signal} action={Action} "
                        f"reservation_variant={NextReservationVariant} "
                        f"candidate_diversity={NextCandidateDiversityLevel} "
                        f"lane_diversity={NextLaneDiversityLevel} "
                        f"unreserved={NextSkipStrictPortalReservation} "
                        f"elapsed={monotonic() - RoutingStarted:.3f}",
                        flush=True,
                    )
                PreserveUnaffected = Action in {
                    "regenerate-affected-candidates",
                    "increase-guide-lane-diversity",
                    "add-routing-layer",
                }
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        frozenset({Signal}),
                    )
                    if PreserveUnaffected
                    else ({}, {})
                )
                return RouteAuthoritativeResources(
                    Placed,
                    Resources,
                    SearchMarginX,
                    SearchMarginZ,
                    MaximumRoutingHeight,
                    Policy,
                    Technology,
                    ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=NextLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(
                        *EscalationHistory,
                        {
                            "Stage": "CandidateGeneration",
                            "Action": Action,
                            "AffectedSignals": [Signal],
                            "CandidateFailureFingerprint": (
                                CandidateFailureFingerprint
                            ),
                            "Diagnostics": CandidateDiagnostics[Signal],
                        },
                    ),
                    ReservationVariant=NextReservationVariant,
                    LaneDiversityLevel=NextLaneDiversityLevel,
                    CandidateDiversityLevel=NextCandidateDiversityLevel,
                    SkipStrictPortalReservation=(
                        NextSkipStrictPortalReservation
                    ),
                    EscalationStates=EscalationStates,
                    Deadline=Deadline,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    PriorCandidateCache=(
                        {
                            Signal: tuple(CandidatesBySignal[Signal])
                        }
                        if Action in {
                            "increase-guide-lane-diversity",
                            "add-routing-layer",
                        }
                        and CandidatesBySignal.get(Signal)
                        else None
                    ),
                    PriorCandidateMetadata=(
                        {
                            Signal: dict(
                                CandidateAxisLaneBySignal.get(Signal, {})
                            )
                        }
                        if Action in {
                            "increase-guide-lane-diversity",
                            "add-routing-layer",
                        }
                        and CandidatesBySignal.get(Signal)
                        else None
                    ),
                    RegenerateSignals=(
                        frozenset({Signal})
                        if PreserveUnaffected
                        else frozenset()
                    ),
                    RawPortalCache=(
                        EffectiveRawPortalCache
                        if NextLayerFloor is None
                        or NextLayerFloor <= LayerCount
                        else None
                    ),
                    PreparedPortalCache=(
                        EffectivePreparedPortalCache
                        if (
                            (NextLayerFloor is None or NextLayerFloor <= LayerCount)
                            and NextReservationVariant == ReservationVariant
                            and NextSkipStrictPortalReservation
                            == SkipStrictPortalReservation
                        )
                        else None
                    ),
                )

            if (
                Policy.AdaptiveRouting.Enabled
                and CandidateDiagnostics[Signal]["DeferredRequests"] > 0
                and CandidateDiversityLevel + 1
                < Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations
                and not PlacementWasRelocated
            ):
                return RetryCandidateGeneration(
                    "regenerate-affected-candidates",
                    NextCandidateDiversityLevel=CandidateDiversityLevel + 1,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and (
                    CandidateDiversityLevel + 1
                    >= Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations
                    or (
                        PlacementWasRelocated
                        and (
                            not AllowRelocatedStarvationLaneRetry
                            or LaneDiversityLevel >= 1
                        )
                    )
                    or (
                        CandidateDiagnostics[Signal]["DeferredRequests"] == 0
                        and "__PlacementRelocation__"
                        in (
                            getattr(Placed, "LocalRouteDiagnostics", {}) or {}
                        )
                    )
                )
                and (
                    LocalClaimsBySignal.get(Signal)
                    or "__PlacementRelocation__"
                    in (getattr(Placed, "LocalRouteDiagnostics", {}) or {})
                )
            ):
                ConflictGraph = {
                    "Classification": "candidate-starvation-placement-conflict",
                    "ConflictSignals": [Signal],
                    "NoCandidateSignals": [Signal],
                }
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="Candidate",
                        AffectedNets=(Signal,),
                        RepairActions=(
                            "RelocateAffectedClusters",
                            "AdvancePlacementCandidate",
                        ),
                        Detail=(
                            "the fixed placement exhausted bounded candidate "
                            "diversity for one signal"
                        ),
                        Diagnostics={
                            "Action": "advance-placement-candidate-starvation",
                            "ConflictGraph": ConflictGraph,
                            "CandidateDiagnostics": CandidateDiagnostics[Signal],
                            "CandidateFailureFingerprint": (
                                CandidateFailureFingerprint
                            ),
                            "EscalationHistory": tuple(EscalationHistory),
                            "Deadline": Deadline.ToDictionary(),
                        },
                    )
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and not SkipStrictPortalReservation
                and ReservationVariant + 1
                < Policy.AdaptiveRouting.MaximumPortalReservationAlternatives
            ):
                return RetryCandidateGeneration(
                    "alternate-portal-slots",
                    NextReservationVariant=ReservationVariant + 1,
                    NextCandidateDiversityLevel=0,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and not SkipStrictPortalReservation
            ):
                return RetryCandidateGeneration(
                    "try-bounded-unreserved-portals",
                    NextCandidateDiversityLevel=0,
                    NextSkipStrictPortalReservation=True,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and LaneDiversityLevel + 1
                < Policy.AdaptiveRouting.MaximumLaneDiversityEscalations
                and not SeedRejectedEveryRoutedTree
                and (
                    not PlacementWasRelocated
                    or AllowRelocatedStarvationLaneRetry
                )
            ):
                return RetryCandidateGeneration(
                    "increase-guide-lane-diversity",
                    NextLaneDiversityLevel=LaneDiversityLevel + 1,
                    NextCandidateDiversityLevel=CandidateDiversityLevel,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and LayerCount < EffectiveMaximumLayerCount
            ):
                return RetryCandidateGeneration(
                    "add-routing-layer",
                    NextReservationVariant=0,
                    NextLaneDiversityLevel=0,
                    NextCandidateDiversityLevel=0,
                    NextLayerFloor=LayerCount + 1,
                )
            if (
                Policy.AdaptiveRouting.Enabled
                and not SkipStrictPortalReservation
            ):
                return RetryCandidateGeneration(
                    "final-bounded-unreserved-portals",
                    NextReservationVariant=0,
                    NextLaneDiversityLevel=0,
                    NextCandidateDiversityLevel=0,
                    NextSkipStrictPortalReservation=True,
                )
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: candidate generation failed to materialize "
                    f"signal={Signal} reason={CandidateDiagnostics[Signal]}",
                    flush=True,
                )
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.DetailedSearchExhausted,
                    Stage="Candidate",
                    AffectedNets=(Signal,),
                    Detail=(
                        "bounded Rust search produced no complete legal route "
                        f"candidate (candidate_expansion_limit={CandidateExpansionLimit}, "
                        f"diagnostics={CandidateDiagnostics[Signal]})"
                    ),
                    Diagnostics={
                        "CandidateDiagnostics": CandidateDiagnostics[Signal],
                        "CandidateFailureFingerprint": CandidateFailureFingerprint,
                        "EscalationHistory": tuple(EscalationHistory),
                        "Deadline": Deadline.ToDictionary(),
                    },
                )
            )
        CheckRuntimeBudget("Candidate")

    StageTimings["CandidateGeneration"] = monotonic() - CandidateStarted
    WorkTelemetry["RouteTreeRequestCount"] = CandidateRequestCount
    if ProgressCallback is not None:
        ProgressCallback(4, StageCount)
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
        print(
            "[debug] authoritative: candidate generation complete "
            f"signals={len(CandidatesBySignal)} batches={RouteTreeBatchCount} "
            f"request_construction="
            f"{WorkTelemetry['CandidateRequestConstructionSeconds']} "
            f"initial_native="
            f"{WorkTelemetry['InitialNativeCandidateBatchSeconds']} "
            f"total={monotonic() - CandidateStarted:.3f}",
            flush=True,
        )
        for Signal in sorted(CandidatesBySignal):
            Diagnostics = CandidateDiagnostics[Signal]
            print(
                "[debug] authoritative: signal diagnostics "
                f"signal={Signal} requests={Diagnostics['Requests']} "
                f"routed={Diagnostics['RoutedTrees']} materialized={Diagnostics['Materialized']} "
                f"source_portals={Diagnostics['SourcePortals']} "
                f"target_portals={Diagnostics['TargetPortals']} "
                f"self_conflict={Diagnostics.get('Rejections', {}).get('SelfClaimConflict', 0)} "
                f"disconnected={Diagnostics.get('Rejections', {}).get('Disconnected', 0)} "
                f"no_repeater={Diagnostics.get('Rejections', {}).get('NoRepeater', 0)} "
                f"limit={CandidateLimitsBySignal[Signal]} "
                f"final={len(CandidatesBySignal[Signal])}",
                flush=True,
            )

    CandidateLookup = {
        Candidate.CandidateId: Candidate
        for Values in CandidatesBySignal.values()
        for Candidate in Values
    }
    RuntimeEscalationState = replace(
        RuntimeEscalationState,
        CandidateFingerprint=BuildStableFingerprint({
            Signal: [
                Candidate.CandidateId
                for Candidate in CandidatesBySignal[Signal]
            ]
            for Signal in sorted(CandidatesBySignal)
        }),
    )
    BaseLocalClaims = LocalClaims
    AssignmentResourcePositions = tuple(
        sorted(
            {
                Position
                for Candidate in CandidateLookup.values()
                for Positions in (
                    Candidate.Claims.WireCells,
                    Candidate.Claims.SupportCells,
                    Candidate.Claims.RequiredAirCells,
                    Candidate.Claims.ElectricalCells,
                )
                for Position in Positions
            }
            | {
                Position
                for Claim in BaseLocalClaims
                for Positions in (
                    Claim.Claims.WireCells,
                    Claim.Claims.SupportCells,
                    Claim.Claims.RequiredAirCells,
                    Claim.Claims.ElectricalCells,
                )
                for Position in Positions
            }
        )
    )
    AssignmentIndexed = IndexedRoutingResourceGraph(
        ResourcePositions=AssignmentResourcePositions,
        PositionIndices={
            Position: Index
            for Index, Position in enumerate(AssignmentResourcePositions)
        },
    )
    def EncodeCandidateValues(
        CandidateSets: dict[str, list[NetRouteCandidate]],
        CongestionHistory: Counter[Position2] | None = None,
        OptimizeShape: bool = False,
    ) -> list[tuple[Any, ...]]:
        Values = []
        History = CongestionHistory or Counter()
        for Signal in sorted(CandidateSets):
            for Candidate in CandidateSets[Signal]:
                Wire, Support, Air, Electrical = AssignmentIndexed.EncodeClaims(
                    Candidate.Claims
                )
                HistoryCost = sum(
                    History[(X, Z)] for X, _Y, Z in Candidate.Nodes
                ) * Policy.Repair.HistoryIncrement
                MaterialCost = (
                    Candidate.Length * 10_000
                    + Candidate.BendCount * 100
                    + Candidate.ViaCount
                    if OptimizeShape
                    else Candidate.MaterialCost
                )
                Values.append(
                    (
                        Signal,
                        Candidate.CandidateId,
                        list(Wire),
                        list(Support),
                        list(Air),
                        list(Electrical),
                        MaterialCost + HistoryCost,
                        Candidate.FootprintGrowth,
                        Candidate.Length,
                        Candidate.BendCount,
                        Candidate.ViaCount,
                    )
                )
        return Values

    BaseValues: list[tuple[Any, ...]] | None = None

    EscalationLevel = max(
        0,
        LayerCount - AdaptiveBudget.LayerCount,
    )
    AssignmentExpansionLimit = (
        min(
            AdaptiveBudget.AssignmentExpansions,
            Policy.AdaptiveRouting.InitialAssignmentExpansions
            * Policy.AdaptiveRouting.AssignmentGrowthFactor ** EscalationLevel,
        )
        if Policy.AdaptiveRouting.Enabled
        else Policy.TrackAssignment.MaximumAssignmentExpansions
    )
    RuntimeEscalationState = replace(
        RuntimeEscalationState,
        AssignmentBudget=AssignmentExpansionLimit,
    )

    def PlanAssignment(Values: list[tuple[Any, ...]] | None = None) -> Any:
        nonlocal BaseValues
        if Values is None:
            Values = EncodeCandidateValues(CandidatesBySignal)
        if BaseValues is None:
            BaseValues = []
            for Claim in BaseLocalClaims:
                Wire, Support, Air, Electrical = AssignmentIndexed.EncodeClaims(
                    Claim.Claims
                )
                BaseValues.append(
                    (
                        Claim.Signal,
                        list(Wire),
                        list(Support),
                        list(Air),
                        list(Electrical),
                    )
                )
        if BaseValues:
            Arguments = (
                Values,
                BaseValues,
                len(AssignmentIndexed.ResourcePositions),
                AssignmentExpansionLimit,
            )
            if hasattr(Context, "PlanAuthoritativeRoutesWithBaseBounded"):
                return Context.PlanAuthoritativeRoutesWithBaseBounded(
                    *Arguments,
                    RemainingRoutingRuntimeMilliseconds(
                        Deadline,
                        AdaptiveExpiresAt,
                    ),
                )
            try:
                return Context.PlanAuthoritativeRoutesWithBase(
                    *Arguments,
                    RemainingRoutingRuntimeMilliseconds(
                        Deadline,
                        AdaptiveExpiresAt,
                    ) / 1000.0,
                )
            except TypeError as Error:
                if "positional arguments" not in str(Error):
                    raise
                return Context.PlanAuthoritativeRoutesWithBase(*Arguments)
        Arguments = (
            Values,
            len(AssignmentIndexed.ResourcePositions),
            AssignmentExpansionLimit,
        )
        if hasattr(Context, "PlanAuthoritativeRoutesBounded"):
            return Context.PlanAuthoritativeRoutesBounded(
                *Arguments,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ),
            )
        try:
            return Context.PlanAuthoritativeRoutes(
                *Arguments,
                RemainingRoutingRuntimeMilliseconds(
                    Deadline,
                    AdaptiveExpiresAt,
                ) / 1000.0,
            )
        except TypeError as Error:
            if "positional arguments" not in str(Error):
                raise
            return Context.PlanAuthoritativeRoutes(*Arguments)

    def RaiseForNativeAssignmentDeadline(Result: Any) -> None:
        if not getattr(Result, "DeadlineExceeded", False):
            return
        EnforceRoutingRuntimeLimit(
            Deadline=Deadline,
            AdaptiveStartedAt=RoutingStarted,
            AdaptiveExpiresAt=AdaptiveExpiresAt,
            Stage="TrackAssignment",
            Diagnostics=CurrentRuntimeBudgetDiagnostics({
                "CompletedWork": getattr(Result, "CompletedWork", 0),
            }),
            NativeDeadlineExceeded=True,
        )

    AssignmentStarted = monotonic()
    Result = PlanAssignment()
    RaiseForNativeAssignmentDeadline(Result)
    if Policy.AdaptiveRouting.Enabled:
        while (
            not Result.Success
            and ShouldGrowAssignmentBudget(Result)
            and AssignmentExpansionLimit < AdaptiveBudget.AssignmentExpansions
            and not (
                PlacementWasRelocated
                and LaneDiversityLevel >= 1
            )
        ):
            NextAssignmentExpansionLimit = GrowAssignmentExpansionLimit(
                AssignmentExpansionLimit,
                AdaptiveBudget.AssignmentExpansions,
                Policy.AdaptiveRouting.AssignmentGrowthFactor,
            )
            if NextAssignmentExpansionLimit <= AssignmentExpansionLimit:
                break
            AssignmentRetryHistory.append({
                "Stage": "TrackAssignment",
                "Action": "increase-assignment-budget",
                "FromAssignmentExpansionLimit": AssignmentExpansionLimit,
                "ToAssignmentExpansionLimit": NextAssignmentExpansionLimit,
                "ExactExpansions": Result.ExpansionCount,
                "Reason": "exact capacity-one assignment exhausted current budget",
            })
            AssignmentExpansionLimit = NextAssignmentExpansionLimit
            RuntimeEscalationState = replace(
                RuntimeEscalationState,
                AssignmentBudget=AssignmentExpansionLimit,
            )
            CheckRuntimeBudget("Track")
            Result = PlanAssignment()
            RaiseForNativeAssignmentDeadline(Result)
    # Assignment work is intentionally not the first escalation lever.  A
    # bounded physical change (portal, lane, layer, placement) must happen
    # before spending more time on the identical candidate geometry.
    CheckRuntimeBudget("Track")
    StageTimings["Assignment"] = monotonic() - AssignmentStarted
    if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
        print(
            f"[debug] authoritative: assignment result success={Result.Success} "
            f"expansions={Result.ExpansionCount} budget_exhausted={ShouldGrowAssignmentBudget(Result)}",
            flush=True,
        )
    if not Result.Success:
        if Policy.AdaptiveRouting.Enabled:
            ConflictGraph = BuildRoutingConflictGraph(
                CandidatesBySignal,
                Result,
                AssignmentIndexed.ResourcePositions,
                PortalReservations,
                WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
                    "ConflictClassification",
                    Diagnostics,
                ),
            )
            StackedConflictPairs = tuple(
                tuple(Pair)
                for Pair in ConflictGraph["PairwiseIncompatibleEdges"]
                if len(Pair) == 2
                and Pair[0] in Profiles
                and Pair[1] in Profiles
                and (
                    Profiles[Pair[0]].Root[0],
                    Profiles[Pair[0]].Root[2],
                )
                == (
                    Profiles[Pair[1]].Root[0],
                    Profiles[Pair[1]].Root[2],
                )
                and Profiles[Pair[0]].Root[1]
                != Profiles[Pair[1]].Root[1]
            )
            HasPlacementRelocation = PlacementWasRelocated
            if StackedConflictPairs:
                ConflictGraph["Classification"] = (
                    "stacked-placement-conflict"
                )
                ConflictGraph["StackedConflictPairs"] = (
                    StackedConflictPairs
                )
            elif (
                ConflictGraph["Classification"]
                == "higher-order-placement-conflict"
                and HasPlacementRelocation
                and len(ConflictGraph["PairwiseIncompatibleEdges"]) == 1
            ):
                ConflictGraph["Classification"] = (
                    "relocated-pairwise-incompatibility"
                )
                ConflictGraph["ConflictSignals"] = list(
                    ConflictGraph["PairwiseIncompatibleEdges"][0]
                )
            elif (
                ConflictGraph["Classification"]
                == "higher-order-placement-conflict"
                and HasPlacementRelocation
            ):
                ConflictGraph["Classification"] = (
                    "relocated-higher-order-conflict"
                )
            elif (
                ConflictGraph["Classification"]
                == "pairwise-incompatibility"
                and len(ConflictGraph["PairwiseIncompatibleEdges"]) >= 2
            ):
                ConflictGraph["Classification"] = (
                    "relocated-multi-pair-conflict"
                    if HasPlacementRelocation
                    else "multi-pair-placement-conflict"
                )
                ConflictGraph["ConflictSignals"] = sorted({
                    Signal
                    for Pair in ConflictGraph["PairwiseIncompatibleEdges"]
                    for Signal in Pair
                })
            elif (
                HasPlacementRelocation
                and ConflictGraph["Classification"]
                == "larger-matching-failure"
            ):
                ConflictGraph["Classification"] = (
                    "relocated-larger-matching-failure"
                )
            elif (
                HasPlacementRelocation
                and ConflictGraph["Classification"]
                == "pairwise-incompatibility"
            ):
                ConflictGraph["Classification"] = (
                    "relocated-pairwise-incompatibility"
                )
            ConflictGraph["RelocationSignals"] = (
                SelectPlacementRelocationSignals(ConflictGraph)
            )
            HasPairwiseIncompatibility = bool(
                ConflictGraph["PairwiseIncompatibleEdges"]
            )
            AffectedCandidateSignals = frozenset(
                ConflictGraph.get("ConflictSignals", ())
            )
            if not AffectedCandidateSignals:
                AffectedCandidateSignals = frozenset(CandidatesBySignal)
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(
                    "[debug] authoritative: conflict graph classification="
                    f"{ConflictGraph['Classification']} "
                    f"portal_mode={'unreserved' if UnreservedPortalMode else 'reserved'} "
                    f"reservation_variant={ReservationVariant} "
                    f"lane_diversity={LaneDiversityLevel} "
                    f"layers={LayerCount} "
                    f"pairwise={HasPairwiseIncompatibility} "
                    f"edges={ConflictGraph['PairwiseIncompatibleEdges'][:4]} "
                    f"runtime_left={AdaptiveBudget.RuntimeSeconds - (monotonic() - RoutingStarted):.3f}",
                    flush=True,
                )
                if ConflictGraph["PairwiseIncompatibleEdges"]:
                    FirstSignal, SecondSignal = (
                        ConflictGraph["PairwiseIncompatibleEdges"][0]
                    )
                    ConflictLocations: Counter[Position3] = Counter()
                    for FirstCandidate in CandidatesBySignal[FirstSignal]:
                        for SecondCandidate in CandidatesBySignal[SecondSignal]:
                            FirstClaims = FirstCandidate.Claims
                            SecondClaims = SecondCandidate.Claims
                            Locations = (
                                FirstClaims.WireCells & SecondClaims.WireCells
                                | FirstClaims.RequiredAirCells
                                & SecondClaims.WireCells
                                | SecondClaims.RequiredAirCells
                                & FirstClaims.WireCells
                                | FirstClaims.ElectricalCells
                                & SecondClaims.WireCells
                                | SecondClaims.ElectricalCells
                                & FirstClaims.WireCells
                            )
                            ConflictLocations.update(Locations)
                    print(
                        "[debug] authoritative: first pair details "
                        f"signals=({FirstSignal},{SecondSignal}) "
                        f"candidate_counts=("
                        f"{len(CandidatesBySignal[FirstSignal])},"
                        f"{len(CandidatesBySignal[SecondSignal])}) "
                        f"roots=({Profiles[FirstSignal].Root},"
                        f"{Profiles[SecondSignal].Root}) "
                        f"access=({Profiles[FirstSignal].SourceAccessPath},"
                        f"{Profiles[SecondSignal].SourceAccessPath}) "
                        f"target_access=("
                        f"{Profiles[FirstSignal].TargetAccessPaths},"
                        f"{Profiles[SecondSignal].TargetAccessPaths}) "
                        f"coarse=("
                        f"{(CoarsePlan.Layers[FirstSignal], CoarsePlan.Axes[FirstSignal], CoarsePlan.Lanes[FirstSignal]) if CoarsePlan is not None else None},"
                        f"{(CoarsePlan.Layers[SecondSignal], CoarsePlan.Axes[SecondSignal], CoarsePlan.Lanes[SecondSignal]) if CoarsePlan is not None else None}) "
                        f"tracks=("
                        f"{sorted({CandidateAxisLaneBySignal[FirstSignal][Value.CandidateId][:3] for Value in CandidatesBySignal[FirstSignal]})},"
                        f"{sorted({CandidateAxisLaneBySignal[SecondSignal][Value.CandidateId][:3] for Value in CandidatesBySignal[SecondSignal]})}) "
                        f"hotspots={ConflictLocations.most_common(6)}",
                        flush=True,
                    )
        else:
            ConflictGraph = {
                "Classification": "complete candidate set assignment failure",
                "ConflictSignals": tuple(
                    Signal for Signal, Candidates in CandidatesBySignal.items()
                    if not Candidates
                ),
                "PairwiseIncompatibleEdges": (),
                "NoCandidateSignals": [
                    Signal
                    for Signal, Candidates in CandidatesBySignal.items()
                    if not Candidates
                ],
                "ResourceHotspots": [],
                "PortalReservations": [
                    Value.ToDictionary() for Value in PortalReservations
                ],
            }
            HasPairwiseIncompatibility = False
        CandidateFingerprint = BuildStableFingerprint({
            Signal: [
                Candidate.CandidateId
                for Candidate in CandidatesBySignal[Signal]
            ]
            for Signal in sorted(CandidatesBySignal)
        })
        ConflictFingerprint = BuildStableFingerprint(ConflictGraph)
        EffectiveState = RoutingEscalationState(
            PortalMode=("unreserved" if UnreservedPortalMode else "reserved"),
            ReservationVariant=ReservationVariant,
            LaneDiversityLevel=LaneDiversityLevel,
            CandidateDiversityLevel=CandidateDiversityLevel,
            EffectiveRoutingLayers=LayerCount,
            AssignmentBudget=AssignmentExpansionLimit,
            CandidateFingerprint=CandidateFingerprint,
            ConflictFingerprint=ConflictFingerprint,
        )
        EffectiveWorkFingerprint = BuildStableFingerprint({
            "PortalMode": EffectiveState.PortalMode,
            "PortalReservations": [
                Value.ToDictionary() for Value in PortalReservations
            ],
            "LaneCount": RouteLaneCount,
            "LayerCount": LayerCount,
            "AssignmentBudget": AssignmentExpansionLimit,
            "CandidateFingerprint": CandidateFingerprint,
            "ConflictFingerprint": ConflictFingerprint,
        })
        PriorWorkFingerprints = {
            Entry.get("EffectiveWorkFingerprint")
            for Entry in EscalationHistory
            if isinstance(Entry, dict)
        }
        if EffectiveWorkFingerprint in PriorWorkFingerprints:
            RepeatedTransition = ChooseRepeatedWorkTransition(
                UnreservedPortalMode,
                Deadline,
            )
            if RepeatedTransition.Action == "TryUnreservedPortals":
                NoOpReservationEscalation = {
                    "Stage": "TrackAssignment",
                    "Action": "try-bounded-unreserved-portals",
                    "Reason": (
                        "reserved portal retry reproduced identical effective "
                        "work; advance once to the complete generated portal set"
                    ),
                    "FromPortalMode": "reserved",
                    "ToPortalMode": "unreserved",
                    "ReservationVariant": ReservationVariant,
                    "RoutingEscalationState": EffectiveState.ToDictionary(),
                    "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                    "CandidateFingerprint": CandidateFingerprint,
                    "ConflictFingerprint": ConflictFingerprint,
                }
                return RouteAuthoritativeResources(
                    Placed,
                    Resources,
                    SearchMarginX,
                    SearchMarginZ,
                    MaximumRoutingHeight,
                    Policy,
                    Technology,
                    ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(
                        *EscalationHistory,
                        NoOpReservationEscalation,
                    ),
                    ReservationVariant=ReservationVariant,
                    LaneDiversityLevel=LaneDiversityLevel,
                    CandidateDiversityLevel=CandidateDiversityLevel,
                    SkipStrictPortalReservation=(
                        RepeatedTransition.SkipStrictPortalReservation
                    ),
                    EscalationStates=EscalationStates,
                    Deadline=RepeatedTransition.Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                )
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.Stagnated,
                    Stage="TrackAssignment",
                    AffectedNets=tuple(ConflictGraph.get("ConflictSignals", ())),
                    Detail=(
                        "routing escalation reproduced the same portals, "
                        "candidates, conflicts, layers, and assignment budget"
                    ),
                    Diagnostics={
                        "EscalationHistory": tuple(EscalationHistory),
                        "RoutingEscalationState": EffectiveState.ToDictionary(),
                        "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                        "CandidateFingerprint": CandidateFingerprint,
                        "ConflictFingerprint": ConflictFingerprint,
                        "ConflictGraph": ConflictGraph,
                    },
                )
            )
        MaximumLayerCount = (
            min(Technology.MaximumRoutableLayerCount, PolicyLayerLimit)
            if PolicyLayerLimit > 0
            else Technology.MaximumRoutableLayerCount
        )
        PairwiseConflictAttempts = sum(
            1
            for EscalationEntry in EscalationHistory
            if isinstance(EscalationEntry, dict)
            and EscalationEntry.get("Stage") == "TrackAssignment"
            and EscalationEntry.get("ConflictClassification")
            == "pairwise-incompatibility"
        )
        if (
            bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE"))
            and Policy.AdaptiveRouting.Enabled
            and ConflictGraph["Classification"] == "pairwise-incompatibility"
            and ConflictGraph["PairwiseIncompatibleEdges"]
            and PairwiseConflictAttempts >= 6
        ):
            print(
                "[debug] authoritative: pairwise conflict attempt budget reached; "
                f"continuing adaptive escalation (attempts={PairwiseConflictAttempts})",
                flush=True,
            )
        if monotonic() - RoutingStarted < AdaptiveBudget.RuntimeSeconds:
            FailureKind = (
                "assignment work budget remained exhausted"
                if ShouldGrowAssignmentBudget(Result)
                else "complete candidate set has no capacity-one assignment"
            )
            Escalation = {
                "Stage": "TrackAssignment",
                "AssignmentExpansions": Result.ExpansionCount,
                "ExactExpansions": Result.ExpansionCount,
                "BudgetExhausted": ShouldGrowAssignmentBudget(Result),
                "FailureNet": Result.FailureNet,
                "Reason": FailureKind,
                "ConflictClassification": ConflictGraph["Classification"],
                "PairwiseIncompatible": HasPairwiseIncompatibility,
                "RoutingEscalationState": EffectiveState.ToDictionary(),
                "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                "CandidateFingerprint": CandidateFingerprint,
                "ConflictFingerprint": ConflictFingerprint,
            }
            EscalationDecision = ChooseRoutingEscalationAction(
                Classification=ConflictGraph["Classification"],
                BudgetExhausted=bool(
                    getattr(Result, "BudgetExhausted", False)
                ),
                State=EffectiveState,
                MaximumAssignmentBudget=AdaptiveBudget.AssignmentExpansions,
                MaximumReservationVariants=(
                    Policy.AdaptiveRouting.MaximumPortalReservationAlternatives
                    if Policy.AdaptiveRouting.Enabled
                    else ReservationVariant + 1
                ),
                MaximumLaneDiversityLevels=(
                    (
                        Policy.AdaptiveRouting.MaximumLaneDiversityEscalations
                        if (
                            not PlacementWasRelocated
                            or PlacementWasBroadlyRelocated
                            or ConflictGraph["Classification"]
                            == "relocated-pairwise-incompatibility"
                        )
                        else min(
                            2,
                            Policy.AdaptiveRouting.MaximumLaneDiversityEscalations,
                        )
                    )
                    if Policy.AdaptiveRouting.Enabled
                    else LaneDiversityLevel + 1
                ),
                MaximumCandidateDiversityLevels=(
                    Policy.AdaptiveRouting.MaximumCandidateDiversityEscalations
                    if Policy.AdaptiveRouting.Enabled
                    else CandidateDiversityLevel + 1
                ),
                MaximumEffectiveRoutingLayers=(
                    EffectiveMaximumLayerCount
                    if Policy.AdaptiveRouting.Enabled
                    else LayerCount
                ),
            )
            Escalation["Decision"] = EscalationDecision.Action
            Escalation["DecisionReason"] = EscalationDecision.Reason
            AdaptiveElapsedSeconds = monotonic() - RoutingStarted
            AdaptiveRemainingSeconds = max(
                0.0,
                AdaptiveBudget.RuntimeSeconds - AdaptiveElapsedSeconds,
            )
            ObservedPassSeconds = monotonic() - RoutingCallStarted
            if (
                EscalationDecision.Action != "AdvancePlacement"
                and not HasAdaptiveEscalationBudget(
                    AdaptiveRemainingSeconds,
                    ObservedPassSeconds,
                    bool(EscalationHistory),
                )
            ):
                Escalation.update({
                    "Action": "advance-placement-insufficient-adaptive-slice",
                    "AdaptiveElapsedSeconds": round(
                        AdaptiveElapsedSeconds,
                        6,
                    ),
                    "AdaptiveRemainingSeconds": round(
                        AdaptiveRemainingSeconds,
                        6,
                    ),
                    "ObservedPassSeconds": round(ObservedPassSeconds, 6),
                })
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="TrackAssignment",
                        AffectedNets=tuple(
                            sorted(ConflictGraph.get("ConflictSignals", ()))
                        ),
                        Detail=(
                            "the next routing control pass cannot fit the "
                            "remaining per-placement escalation slice"
                        ),
                        RepairActions=("AdvancePlacementCandidate",),
                        Diagnostics={
                            "EscalationHistory": tuple(
                                (*EscalationHistory, Escalation)
                            ),
                            "ConflictGraph": ConflictGraph,
                            "RoutingEscalationState": (
                                EffectiveState.ToDictionary()
                            ),
                            "EffectiveWorkFingerprint": (
                                EffectiveWorkFingerprint
                            ),
                        },
                    )
                )
            if EscalationDecision.Action == "AdvancePlacement":
                Escalation["Action"] = (
                    "advance-placement-conflict-relocation"
                    if ConflictGraph["Classification"]
                    in {
                        "higher-order-placement-conflict",
                        "multi-pair-placement-conflict",
                        "relocated-higher-order-conflict",
                        "relocated-larger-matching-failure",
                        "relocated-multi-pair-conflict",
                        "relocated-pairwise-incompatibility",
                        "stacked-placement-conflict",
                    }
                    else "advance-placement"
                )
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="TrackAssignment",
                        AffectedNets=tuple(
                            sorted(ConflictGraph.get("ConflictSignals", ()))
                        ),
                        RepairActions=(
                            "RelocateAffectedClusters",
                            "AdvancePlacementCandidate",
                        ),
                        Detail=(
                            f"{EscalationDecision.Reason}; the current placement "
                            "has no remaining meaningful routing control change"
                        ),
                        Diagnostics={
                            "EscalationHistory": tuple(
                                (*EscalationHistory, Escalation)
                            ),
                            "ConflictGraph": ConflictGraph,
                            "RoutingEscalationState": EffectiveState.ToDictionary(),
                            "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                        },
                    )
                )
            if (
                EscalationDecision.Action == "RegenerateAffectedCandidates"
            ):
                AffectedSignals = frozenset(
                    ConflictGraph.get("NoCandidateSignals", ())
                )
                if not AffectedSignals:
                    AffectedSignals = frozenset(
                        ConflictGraph.get("ConflictSignals", ())
                    )
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedSignals,
                    )
                )
                Escalation.update({
                    "Action": "regenerate-affected-candidates",
                    "AffectedSignals": sorted(AffectedSignals),
                    "FromCandidateDiversityLevel": CandidateDiversityLevel,
                    "ToCandidateDiversityLevel": CandidateDiversityLevel + 1,
                })
                return RouteAuthoritativeResources(
                    Placed,
                    Resources,
                    SearchMarginX,
                    SearchMarginZ,
                    MaximumRoutingHeight,
                    Policy,
                    Technology,
                    ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=ReservationVariant,
                    LaneDiversityLevel=LaneDiversityLevel,
                    CandidateDiversityLevel=CandidateDiversityLevel + 1,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=SkipStrictPortalReservation,
                    Deadline=Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    RegenerateSignals=AffectedSignals,
                    PreparedPortalCache=EffectivePreparedPortalCache,
                )
            if (
                EscalationDecision.Action == "ChangePortalReservation"
            ):
                LocalizePortalRegeneration = not LocalClaims
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedCandidateSignals,
                    )
                    if LocalizePortalRegeneration
                    else ({}, {})
                )
                Escalation.update({
                    "Action": "alternate-portal-slots",
                    "AffectedSignals": sorted(AffectedCandidateSignals),
                    "LocalizedCandidateRegeneration": (
                        LocalizePortalRegeneration
                    ),
                    "FromReservationVariant": ReservationVariant,
                    "ToReservationVariant": ReservationVariant + 1,
                })
                return RouteAuthoritativeResources(
                    Placed, Resources, SearchMarginX, SearchMarginZ,
                    MaximumRoutingHeight, Policy, Technology, ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=ReservationVariant + 1,
                    LaneDiversityLevel=LaneDiversityLevel,
                    CandidateDiversityLevel=0,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=SkipStrictPortalReservation,
                    Deadline=Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                    PreparedPortalCache=EffectivePreparedPortalCache,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    RegenerateSignals=(
                        AffectedCandidateSignals
                        if LocalizePortalRegeneration
                        else frozenset()
                    ),
                )
            if EscalationDecision.Action == "TryUnreservedPortals":
                LocalizePortalRegeneration = not LocalClaims
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedCandidateSignals,
                    )
                    if LocalizePortalRegeneration
                    else ({}, {})
                )
                Escalation.update({
                    "Action": "try-bounded-unreserved-portals",
                    "AffectedSignals": sorted(AffectedCandidateSignals),
                    "LocalizedCandidateRegeneration": (
                        LocalizePortalRegeneration
                    ),
                    "FromPortalMode": "reserved",
                    "ToPortalMode": "unreserved",
                    "ReservationVariant": ReservationVariant,
                })
                return RouteAuthoritativeResources(
                    Placed, Resources, SearchMarginX, SearchMarginZ,
                    MaximumRoutingHeight, Policy, Technology, ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=ReservationVariant,
                    LaneDiversityLevel=LaneDiversityLevel,
                    CandidateDiversityLevel=CandidateDiversityLevel,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=True,
                    Deadline=Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    RegenerateSignals=(
                        AffectedCandidateSignals
                        if LocalizePortalRegeneration
                        else frozenset()
                    ),
                )
            if (
                EscalationDecision.Action == "IncreaseLaneDiversity"
            ):
                NextLaneDiversityLevel = LaneDiversityLevel + 1
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedCandidateSignals,
                    )
                )
                Escalation.update({
                    "Action": "increase-guide-lane-diversity",
                    "AffectedSignals": sorted(AffectedCandidateSignals),
                    "FromLaneDiversityLevel": LaneDiversityLevel,
                    "ToLaneDiversityLevel": NextLaneDiversityLevel,
                })
                return RouteAuthoritativeResources(
                    Placed, Resources, SearchMarginX, SearchMarginZ,
                    MaximumRoutingHeight, Policy, Technology, ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=AdaptiveLayerFloor,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=ReservationVariant,
                    LaneDiversityLevel=NextLaneDiversityLevel,
                    CandidateDiversityLevel=CandidateDiversityLevel,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=SkipStrictPortalReservation,
                    Deadline=Deadline,
                    RawPortalCache=EffectiveRawPortalCache,
                    PreparedPortalCache=EffectivePreparedPortalCache,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    PriorCandidateCache={
                        Signal: tuple(Values)
                        for Signal, Values in CandidatesBySignal.items()
                        if Values and Signal in AffectedCandidateSignals
                    },
                    PriorCandidateMetadata={
                        Signal: dict(Values)
                        for Signal, Values in CandidateAxisLaneBySignal.items()
                        if Signal in AffectedCandidateSignals
                    },
                    RegenerateSignals=AffectedCandidateSignals,
                )
            if (
                EscalationDecision.Action == "AddRoutingLayer"
            ):
                NextLayerCount = (
                    EffectiveMaximumLayerCount
                    if ConflictGraph["Classification"].startswith(
                        "relocated-"
                    )
                    else LayerCount + 1
                )
                RetainedCandidates, RetainedMetadata = (
                    RetainUnaffectedCandidateCache(
                        CandidatesBySignal,
                        CandidateAxisLaneBySignal,
                        AffectedCandidateSignals,
                    )
                )
                Escalation.update({
                    "Action": "add-routing-layer",
                    "AffectedSignals": sorted(AffectedCandidateSignals),
                    "FromLayerCount": LayerCount,
                    "ToLayerCount": NextLayerCount,
                })
                return RouteAuthoritativeResources(
                    Placed, Resources, SearchMarginX, SearchMarginZ,
                    MaximumRoutingHeight, Policy, Technology, ProgressCallback,
                    DiagnosticCallback,
                    AdaptiveLayerFloor=NextLayerCount,
                    SharedRoutingStarted=RoutingStarted,
                    EscalationHistory=(*EscalationHistory, Escalation),
                    ReservationVariant=0,
                    LaneDiversityLevel=0,
                    CandidateDiversityLevel=0,
                    EscalationStates=EscalationStates,
                    SkipStrictPortalReservation=SkipStrictPortalReservation,
                    Deadline=Deadline,
                    RetainedCandidateCache=RetainedCandidates or None,
                    RetainedCandidateMetadata=RetainedMetadata or None,
                    PriorCandidateCache={
                        Signal: tuple(Values)
                        for Signal, Values in CandidatesBySignal.items()
                        if Values and Signal in AffectedCandidateSignals
                    },
                    PriorCandidateMetadata={
                        Signal: dict(Values)
                        for Signal, Values in CandidateAxisLaneBySignal.items()
                        if Signal in AffectedCandidateSignals
                    },
                    RegenerateSignals=AffectedCandidateSignals,
                )
        Locations = tuple(
            AssignmentIndexed.ResourcePositions[Index]
            for Index in Result.ConflictResourceIndices[:8]
        )
        ZeroCompatibilityPairs = []
        if not Policy.AdaptiveRouting.Enabled:
            Signals = sorted(CandidatesBySignal)
            for SignalIndex, FirstSignal in enumerate(Signals):
                for SecondSignal in Signals[SignalIndex + 1 :]:
                    if not any(
                        not FindClaimConflicts(
                            {
                                FirstSignal: First.Claims,
                                SecondSignal: Second.Claims,
                            },
                            WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
                                "CompatibilityConflictClassification",
                                Diagnostics,
                            ),
                        )
                        for First in CandidatesBySignal[FirstSignal]
                        for Second in CandidatesBySignal[SecondSignal]
                    ):
                        ZeroCompatibilityPairs.append((FirstSignal, SecondSignal))
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="Track",
                AffectedNets=((Result.FailureNet,) if Result.FailureNet else ()),
                Locations=Locations,
                Detail=(
                    "Rust MRV assignment found no exact capacity-one selection "
                    f"after {Result.ExpansionCount} expansions; "
                    f"budget_exhausted={ShouldGrowAssignmentBudget(Result)}; "
                    f"failure_net_candidates="
                    f"{len(CandidatesBySignal.get(Result.FailureNet, ())) if Result.FailureNet else 0}; "
                    f"conflict_classification={ConflictGraph['Classification']}; "
                    f"pairwise_unroutable="
                    f"{(ConflictGraph['PairwiseIncompatibleEdges'] if Policy.AdaptiveRouting.Enabled else ZeroCompatibilityPairs)[:4]}"
                ),
                Diagnostics={
                    **ConflictGraph,
                    "ConflictGraph": ConflictGraph,
                    "EscalationHistory": tuple(EscalationHistory),
                    "RoutingEscalationState": EffectiveState.ToDictionary(),
                    "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                    "CandidateFingerprint": CandidateFingerprint,
                    "ConflictFingerprint": ConflictFingerprint,
                },
            )
        )
    if ProgressCallback is not None:
        ProgressCallback(5, StageCount)
    InitialAssignmentExpansionCount = Result.ExpansionCount
    Selected = {
        Signal: CandidateLookup[CandidateId]
        for Signal, CandidateId in Result.SelectedCandidateIds
    }
    AssignmentExpansionCount = InitialAssignmentExpansionCount
    RepairIterations = []
    ReroutedSignals: set[str] = set()
    if NegotiatedPlan is not None:
        RepairIterations.extend(NegotiatedPlan.Iterations)
        ReroutedSignals.update(NegotiatedPlan.ReroutedSignals)

    def ReportMaterializationStage(Stage: str) -> None:
        if DiagnosticCallback is None:
            return
        Values = tuple(Selected.values())
        DiagnosticCallback(
            RoutingIterationMetrics(
                Iteration=1,
                Stage=Stage,
                ConflictCount=0,
                ReroutedNets=len(ReroutedSignals),
                AverageLength=(
                    sum(Value.Length for Value in Values) / len(Values)
                    if Values
                    else 0.0
                ),
                BendCount=sum(Value.BendCount for Value in Values),
                ViaCount=sum(Value.ViaCount for Value in Values),
            ),
            None,
        )
    if CoarsePlan is not None:
        if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
            print("[debug] authoritative: entering offline repair loop", flush=True)
        CongestionHistory: Counter[Position2] = Counter()

        def SelectionQuality(
            Values: dict[str, NetRouteCandidate],
        ) -> tuple[int, int, int, int]:
            ColumnUsage = Counter(
                (X, Z)
                for Candidate in Values.values()
                for X, _Y, Z in Candidate.Nodes
            )
            return (
                max((Count - 1 for Count in ColumnUsage.values()), default=0),
                sum(Candidate.Length for Candidate in Values.values()),
                sum(Candidate.BendCount for Candidate in Values.values()),
                sum(Candidate.ViaCount for Candidate in Values.values()),
            )

        CurrentQuality = SelectionQuality(Selected)
        StagnationCount = 0
        for PassIndex in range(Policy.GlobalRouting.MaximumRipupPasses):
            CheckRuntimeBudget("CongestionRepair")
            if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                print(f"[debug] authoritative: congestion pass {PassIndex}", flush=True)
            ColumnUsage = Counter(
                (X, Z)
                for Candidate in Selected.values()
                for X, _Y, Z in Candidate.Nodes
            )
            OverflowColumns = {
                Position: Count - 1
                for Position, Count in ColumnUsage.items()
                if Count > 2
            }
            if not OverflowColumns:
                break
            Contributions = Counter(
                {
                    Signal: sum(
                        OverflowColumns.get((X, Z), 0)
                        for X, _Y, Z in Candidate.Nodes
                    )
                    for Signal, Candidate in Selected.items()
                }
            )
            Offenders = tuple(
                Signal
                for Signal, Score in sorted(
                    Contributions.items(),
                    key=lambda Value: (
                        -Value[1],
                        -Selected[Value[0]].Length,
                        -Selected[Value[0]].BendCount,
                        -Selected[Value[0]].ViaCount,
                        Value[0],
                    ),
                )
                if Score > 0
            )[:4]
            if not Offenders:
                break
            CongestionHistory.update(OverflowColumns)
            RepairSets = {
                Signal: (
                    CandidatesBySignal[Signal]
                    if Signal in Offenders
                    else [Selected[Signal]]
                )
                for Signal in Selected
            }
            RepairResult = PlanAssignment(
                EncodeCandidateValues(RepairSets, CongestionHistory)
            )
            RaiseForNativeAssignmentDeadline(RepairResult)
            CheckRuntimeBudget("CongestionRepair")
            if not RepairResult.Success:
                break
            Repaired = {
                Signal: CandidateLookup[CandidateId]
                for Signal, CandidateId in RepairResult.SelectedCandidateIds
            }
            RepairedQuality = SelectionQuality(Repaired)
            RepairIterations.append(
                RoutingIterationMetrics(
                    Iteration=PassIndex + 2,
                    Stage="Localized congestion repair",
                    ConflictCount=0,
                    ReroutedNets=sum(
                        Repaired[Signal].CandidateId != Selected[Signal].CandidateId
                        for Signal in Selected
                    ),
                    AverageLength=(
                        sum(Value.Length for Value in Repaired.values())
                        / len(Repaired)
                    ),
                    BendCount=sum(Value.BendCount for Value in Repaired.values()),
                    ViaCount=sum(Value.ViaCount for Value in Repaired.values()),
                    ConflictSignals=tuple(Offenders),
                )
            )
            if RepairedQuality >= CurrentQuality:
                StagnationCount += 1
                if StagnationCount >= Policy.GlobalRouting.StagnationPassLimit:
                    break
                continue
            StagnationCount = 0
            ReroutedSignals.update(
                Signal
                for Signal in Selected
                if Repaired[Signal].CandidateId != Selected[Signal].CandidateId
            )
            Selected = Repaired
            CurrentQuality = RepairedQuality
            Result = RepairResult

        # Once overflow is bounded, make a small deterministic sweep over the
        # worst-shaped nets.  This is intentionally separate from congestion
        # cleanup so a via/length improvement cannot reintroduce overflow.
        if ShouldRunShapeOptimization(Policy.QualityTarget):
            ShapeOrder = tuple(
                sorted(
                    Selected,
                    key=lambda Signal: (
                        -Selected[Signal].ViaCount,
                        -Selected[Signal].BendCount,
                        -Selected[Signal].Length,
                        Signal,
                    ),
                )
            )
            ShapeBatchSize = max(1, min(6, len(ShapeOrder)))
            for ShapePass in range(Policy.GlobalRouting.MaximumRipupPasses):
                CheckRuntimeBudget("ShapeOptimization")
                if bool(os.environ.get("RCS_DEBUG_AUTHORITATIVE")):
                    print(
                        f"[debug] authoritative: shape pass {ShapePass}",
                        flush=True,
                    )
                Start = ShapePass * ShapeBatchSize
                ShapeOffenders = ShapeOrder[Start:Start + ShapeBatchSize]
                if not ShapeOffenders:
                    break
                RepairSets = {
                    Signal: (
                        CandidatesBySignal[Signal]
                        if Signal in ShapeOffenders
                        else [Selected[Signal]]
                    )
                    for Signal in Selected
                }
                ShapeResult = PlanAssignment(
                    EncodeCandidateValues(RepairSets, OptimizeShape=True)
                )
                RaiseForNativeAssignmentDeadline(ShapeResult)
                CheckRuntimeBudget("ShapeOptimization")
                if not ShapeResult.Success:
                    continue
                Shaped = {
                    Signal: CandidateLookup[CandidateId]
                    for Signal, CandidateId in ShapeResult.SelectedCandidateIds
                }
                ShapedQuality = SelectionQuality(Shaped)
                RepairIterations.append(
                    RoutingIterationMetrics(
                        Iteration=len(RepairIterations) + 2,
                        Stage="Localized shape repair",
                        ConflictCount=0,
                        ReroutedNets=sum(
                            Shaped[Signal].CandidateId
                            != Selected[Signal].CandidateId
                            for Signal in Selected
                        ),
                        AverageLength=(
                            sum(Value.Length for Value in Shaped.values())
                            / len(Shaped)
                        ),
                        BendCount=sum(
                            Value.BendCount for Value in Shaped.values()
                        ),
                        ViaCount=sum(
                            Value.ViaCount for Value in Shaped.values()
                        ),
                        ConflictSignals=tuple(ShapeOffenders),
                    )
                )
                if ShapedQuality >= CurrentQuality:
                    continue
                ReroutedSignals.update(
                    Signal
                    for Signal in Selected
                    if Shaped[Signal].CandidateId
                    != Selected[Signal].CandidateId
                )
                Selected = Shaped
                CurrentQuality = ShapedQuality
                Result = ShapeResult
    CheckRuntimeBudget("Materialization")
    ReportMaterializationStage("Authoritative assignment")
    SelectedClaimsBySignal = {
        Signal: Value.Claims for Signal, Value in Selected.items()
    }
    for Signal, SignalClaims in sorted(LocalClaimsBySignal.items()):
        CheckRuntimeBudget("MaterializationClaims")
        if not SignalClaims:
            continue
        LocalClaimsBySignalResource = frozenset(
            Resource
            for Claim in SignalClaims
            for Resource in Claim.Claims.ResourceIds
        )
        if Signal in SelectedClaimsBySignal:
            SelectedClaimsBySignal[Signal] = RoutingResourceClaims(
                WireCells=(
                    frozenset(SelectedClaimsBySignal[Signal].WireCells)
                    | frozenset(
                        Resource.Position
                        for Resource in LocalClaimsBySignalResource
                        if Resource.Kind == RoutingResourceKind.Wire
                    )
                ),
                SupportCells=(
                    frozenset(SelectedClaimsBySignal[Signal].SupportCells)
                    | frozenset(
                        Resource.Position
                        for Resource in LocalClaimsBySignalResource
                        if Resource.Kind == RoutingResourceKind.Support
                    )
                ),
                RequiredAirCells=(
                    frozenset(SelectedClaimsBySignal[Signal].RequiredAirCells)
                    | frozenset(
                        Resource.Position
                        for Resource in LocalClaimsBySignalResource
                        if Resource.Kind == RoutingResourceKind.Air
                    )
                ),
                ElectricalCells=(
                    frozenset(SelectedClaimsBySignal[Signal].ElectricalCells)
                    | frozenset(
                        Resource.Position
                        for Resource in LocalClaimsBySignalResource
                        if Resource.Kind == RoutingResourceKind.Electrical
                    )
                ),
            )
        else:
            SelectedClaimsBySignal[Signal] = RoutingResourceClaims(
                WireCells=frozenset(
                    Resource.Position
                    for Resource in LocalClaimsBySignalResource
                    if Resource.Kind == RoutingResourceKind.Wire
                ),
                SupportCells=frozenset(
                    Resource.Position
                    for Resource in LocalClaimsBySignalResource
                    if Resource.Kind == RoutingResourceKind.Support
                ),
                RequiredAirCells=frozenset(
                    Resource.Position
                    for Resource in LocalClaimsBySignalResource
                    if Resource.Kind == RoutingResourceKind.Air
                ),
                ElectricalCells=frozenset(
                    Resource.Position
                    for Resource in LocalClaimsBySignalResource
                    if Resource.Kind == RoutingResourceKind.Electrical
                ),
            )
    ClaimsBySignal = SelectedClaimsBySignal
    CheckRuntimeBudget("AssignmentDrc")
    Conflicts = FindClaimConflicts(
        ClaimsBySignal,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "AssignmentDrc",
            Diagnostics,
        ),
    )
    if Conflicts:
        First = min(Conflicts, key=str)
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.FinalDrcViolation,
                Stage="AssignmentDrc",
                AffectedNets=Conflicts[First],
                Resources=(str(First),),
                Locations=(First.Position,),
                Detail="Rust assignment disagrees with authoritative Python claims",
            )
        )
    ReportMaterializationStage("Assignment ownership validation")

    SignalOrder = tuple(sorted(Selected))
    PortalLookup = {
        Portal.PortalId: Portal
        for Values in Portals.values()
        for Portal in Values
    }
    MissingProfileSignals = tuple(sorted(set(Selected) - set(Profiles)))
    if MissingProfileSignals:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Materialization",
                AffectedNets=MissingProfileSignals,
                Detail="selected candidate signal missing routing profile",
                Diagnostics={"Profiles": sorted(Profiles)},
            )
        )
    ResourceClaimsBySignal = {
        Signal: frozenset(
            Resource
            for Resource in Candidate.Claims.ResourceIds
            if Resource.Kind != RoutingResourceKind.Electrical
        )
        for Signal, Candidate in Selected.items()
    }
    ResourceUsage = Counter(
        Resource
        for Claims in ResourceClaimsBySignal.values()
        for Resource in Claims
    )
    Plan = ChannelPlan(
        Profiles=Profiles,
        SignalOrder=SignalOrder,
        TrunkSignals=frozenset(
            Signal for Signal, Profile in Profiles.items() if Profile.IsTrunk
        ),
        Guides={Signal: Candidate.Guide for Signal, Candidate in Selected.items()},
        CorridorUsage={},
        CorridorCosts={},
        CorridorCapacity=1,
        Layers={Signal: Candidate.Layer for Signal, Candidate in Selected.items()},
        ResourceUsage=dict(ResourceUsage),
        ResourceOverflow={},
        ResourceClaimsBySignal=ResourceClaimsBySignal,
        SourceAccessTransitions={
            Signal: tuple(
                dict.fromkeys(
                    (
                        *Profiles[Signal].SourceAccessPath,
                        *PortalLookup[Candidate.SourcePortalId].Path,
                    )
                )
            )
            for Signal, Candidate in Selected.items()
        },
        TargetAccessTransitions={
            Signal: {
                Target: tuple(
                    dict.fromkeys(
                        (
                            *Profiles[Signal].TargetAccessPaths[Target],
                            *PortalLookup[PortalId].Path,
                        )
                    )
                )
                for Target, PortalId in Candidate.TargetPortalIds.items()
            }
            for Signal, Candidate in Selected.items()
        },
    )
    Producers = {
        Signal: Gate
        for Gate in Placed.PlacedGates
        if Gate.OutputPin is not None
        for Signal in Gate.Outputs
    }
    Targets = {
        Signal: list(Profile.Targets) for Signal, Profile in Profiles.items()
    }
    for Signal in set(LocalClaimsBySignal):
        if Signal not in Targets:
            SignalSignalTargets = SignalTargets.get(Signal)
            if SignalSignalTargets:
                Targets[Signal] = list(SignalSignalTargets)
    MissingTargetSignals = tuple(sorted(set(Selected) - set(Targets)))
    if MissingTargetSignals:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Materialization",
                AffectedNets=MissingTargetSignals,
                Detail="selected signal missing routing targets",
                Diagnostics={"Targets": sorted(Targets)},
            )
        )
    CheckRuntimeBudget("RouteMaterialization")
    NetWires = {Signal: set(Candidate.Nodes) for Signal, Candidate in Selected.items()}
    LocalSignalWireClaims = {
        Signal: tuple(Claim.Nodes for Claim in SignalClaims)
        for Signal, SignalClaims in LocalClaimsBySignal.items()
        if Signal in SignalTargets
    }
    for Signal, ClaimNodes in LocalSignalWireClaims.items():
        NetWires.setdefault(Signal, set()).update(*ClaimNodes)
    FinalColumnContributors: dict[Position2, list[str]] = defaultdict(list)
    for Signal, Positions in NetWires.items():
        for Column in {(X, Z) for X, _Y, Z in Positions}:
            FinalColumnContributors[Column].append(Signal)
    FinalColumnOverflowHotspots = [
        {
            "Column": list(Column),
            "Count": len(Signals),
            "Signals": sorted(Signals),
        }
        for Column, Signals in sorted(
            FinalColumnContributors.items(),
            key=lambda Value: (-len(Value[1]), Value[0]),
        )
        if len(Signals) > 2
    ][:8]
    Supports: set[Position3] = set()
    SupportPositionCount = 0
    for Signal, Positions in NetWires.items():
        for X, Y, Z in Positions:
            SupportPositionCount += 1
            if SupportPositionCount % 256 == 0:
                CheckRuntimeBudget(
                    "PhysicalGraphMaterialization",
                    {
                        "Phase": "supports",
                        "Signal": Signal,
                        "ProcessedPositions": SupportPositionCount,
                    },
                )
            Supports.add((X, Y - 1, Z))
    Supports.difference_update(Resources.StaticGeometry.ActualBlocks)
    CheckRuntimeBudget("PhysicalGraphMaterialization")
    PhysicalGraphs = BuildPhysicalGraphs(
        NetWires,
        Resources.StaticGeometry.ActualBlocks,
        Supports,
        Resources.StaticGeometry.SolidBlocks,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "PhysicalGraphMaterialization",
            Diagnostics,
        ),
    )
    MissingSourceSignals = tuple(sorted(set(Selected) - set(Producers)))
    if MissingSourceSignals:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Materialization",
                AffectedNets=MissingSourceSignals,
                Detail="selected signal has no routable source gate output pin",
                Diagnostics={
                    "ProducerCount": len(Producers),
                    "SelectedCount": len(Selected),
                },
            )
        )
    MissingNoOutputSignals = tuple(
        sorted(
            Signal for Signal, Producer in Producers.items()
            if Signal in Selected and Producer.OutputPin is None
        )
    )
    if MissingNoOutputSignals:
        raise RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                Stage="Materialization",
                AffectedNets=MissingNoOutputSignals,
                Detail="selected source gate has no output pin",
            )
        )
    CheckRuntimeBudget("PhysicalConnectivityValidation")
    ValidatePhysicalRoutes(
        PhysicalGraphs,
        Producers,
        Targets,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "PhysicalConnectivityValidation",
            Diagnostics,
        ),
    )
    CheckRuntimeBudget("PhysicalConnectivityValidation")
    ReportMaterializationStage("Physical connectivity validation")
    Tracks = {}
    Owners: dict[RoutingResourceId, list[str]] = defaultdict(list)
    for Signal, Candidate in Selected.items():
        CheckRuntimeBudget("RepeaterPlanning")
        if Candidate.SourcePortalId not in PortalLookup:
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                    Stage="Materialization",
                    AffectedNets=(Signal,),
                    Detail="candidate source portal id missing from portal lookup",
                    Diagnostics={
                        "SourcePortalId": Candidate.SourcePortalId,
                        "CandidateId": Candidate.CandidateId,
                        "PortalCount": len(PortalLookup),
                    },
                )
            )
        Graph = PhysicalGraphs[Signal]
        Reservations, Paths = _ReserveRepeaters(
            Signal,
            Producers[Signal].OutputPin,
            tuple(Targets[Signal]),
            Graph,
            Technology,
        )
        for Resource in ResourceClaimsBySignal[Signal]:
            Owners[Resource].append(Signal)
        SourcePortal = PortalLookup[Candidate.SourcePortalId]
        for Target in Targets[Signal]:
            if Target not in Candidate.TargetPortalIds:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                        Stage="Materialization",
                        AffectedNets=(Signal,),
                        Detail="candidate target missing portal mapping",
                        Diagnostics={
                            "Signal": Signal,
                            "Target": list(Target),
                            "CandidateId": Candidate.CandidateId,
                        },
                    )
                )
            if Candidate.TargetPortalIds[Target] not in PortalLookup:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                        Stage="Materialization",
                        AffectedNets=(Signal,),
                        Detail="candidate target portal id missing from portal lookup",
                        Diagnostics={
                            "Signal": Signal,
                            "Target": list(Target),
                            "PortalId": Candidate.TargetPortalIds[Target],
                        },
                    )
                )
            if Target not in Profiles[Signal].TargetAccessPaths:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoConnectedGlobalRoute,
                        Stage="Materialization",
                        AffectedNets=(Signal,),
                        Detail="target missing profile access path",
                        Diagnostics={
                            "Signal": Signal,
                            "Target": list(Target),
                        },
                    )
                )
        TargetPortals = {
            Target: PortalLookup[Candidate.TargetPortalIds[Target]]
            for Target in Profiles[Signal].Targets
        }
        Tracks[Signal] = AssignedTrack(
            Signal=Signal,
            TrackId=Candidate.CandidateId,
            Layer=Candidate.Layer,
            Guide=Candidate.Guide,
            RepeaterSites=frozenset(
                (Position[0], Position[2]) for Position in Candidate.RepeaterWaypoints
            ),
            RepeaterWaypointsByTarget={Target: () for Target in Targets[Signal]},
            ReservedResources=ResourceClaimsBySignal[Signal],
            RepeaterReservations=Reservations,
            AssignedPathsByTarget=Paths,
            SourcePinAccessPath=tuple(
                dict.fromkeys((*Profiles[Signal].SourceAccessPath, *SourcePortal.Path))
            ),
            TargetPinAccessPathsByTarget={
                Target: tuple(
                    dict.fromkeys(
                        (
                            *reversed(TargetPortals[Target].Path),
                            *reversed(Profiles[Signal].TargetAccessPaths[Target]),
                        )
                    )
                )
                for Target in Targets[Signal]
            },
            SelectedPortalIds=(
                Candidate.SourcePortalId,
                *(Candidate.TargetPortalIds[Target] for Target in Targets[Signal]),
            ),
            OwnedNodes=Candidate.Nodes,
            OwnedEdges=Candidate.Edges,
        )
    ReportMaterializationStage("Repeater reservation planning")
    TrackAssignmentValue = TrackAssignment(
        Tracks=Tracks,
        ResourceOwners={Resource: tuple(Values) for Resource, Values in Owners.items()},
    )
    CheckRuntimeBudget("RepeaterMaterialization")
    Repeaters = MaterializeReservedRepeaters(
        NetWires,
        Producers,
        Targets,
        PhysicalGraphs,
        Tracks,
        Technology,
        WorkCheck=lambda Diagnostics: CheckRuntimeBudget(
            "RepeaterMaterialization",
            Diagnostics,
        ),
    )
    CheckRuntimeBudget("RepeaterMaterialization")
    ReportMaterializationStage("Repeater signal-strength validation")
    Assignment = RoutingAssignment(
        SelectedCandidates=Selected,
        ResourceOwners=TrackAssignmentValue.ResourceOwners,
        ExpansionCount=AssignmentExpansionCount,
        PortalCount=sum(len(Values) for Values in Portals.values()),
        CandidateCount=len(CandidateLookup),
    )
    OwnershipCounts = Counter(
        Resource.Kind.value
        for Claims in ClaimsBySignal.values()
        for Resource in Claims.ResourceIds
    )
    CheckRuntimeBudget("MaterializationComplete")
    StageTimings["Total"] = monotonic() - RoutingStarted
    return RoutedDesign(
        Module=Placed.Module,
        PlacedGates=Placed.PlacedGates,
        Wires=sorted(set().union(*NetWires.values())),
        Supports=sorted(Supports),
        Repeaters=Repeaters,
        NetWires={Signal: sorted(Positions) for Signal, Positions in NetWires.items()},
        RoutingMetrics=MeasureRoutingStage(
            "Authoritative Rust",
            NetWires,
            Plan,
            ReroutedNets=len(ReroutedSignals),
            Iterations=tuple(RepairIterations),
        ),
        GlobalPlan=Plan,
        TrackAssignment=TrackAssignmentValue,
        TechnologyVersion=Technology.TechnologyVersion,
        EffectivePolicy=Policy.ToDictionary(),
        ResourceGraphVersion=Resources.ResourceGraph.GraphVersion,
        ResourceGraphNodeCount=Resources.ResourceGraph.CachedNodeCount,
        ResourceGraphEdgeCount=Resources.ResourceGraph.CachedEdgeCount,
        ResourceOwnershipCounts=dict(OwnershipCounts),
        RepeaterReservationCount=sum(len(Track.RepeaterReservations) for Track in Tracks.values()),
        ZeroResourceConflicts=True,
        RoutingAssignment=Assignment,
        PortalCount=Assignment.PortalCount,
        RouteCandidateCount=Assignment.CandidateCount,
        CandidateRequestCount=CandidateRequestCount,
        CandidateExpansionLimit=max(CandidateExpansionLimits.values()),
        AssignmentExpansionCount=Assignment.ExpansionCount,
        RoutingStageTimings={
            Stage: round(Seconds, 6) for Stage, Seconds in StageTimings.items()
        },
        GlobalGuideDiagnostics=(
            CoarsePlan.ToDictionary() if CoarsePlan is not None else {}
        ),
        NegotiatedRoutingDiagnostics=(
            dict(WorkTelemetry.get("NegotiatedRouting", {}))
            if NegotiatedPlan is not None
            else {}
        ),
        RoutingControlEffectiveness={
            "GuideFirstEnabled": CoarsePlan is not None,
            "StrictLocalGuideCount": (
                len(CoarsePlan.LocalSignals) if CoarsePlan is not None else 0
            ),
            "GuidePlanningPasses": (
                len(CoarsePlan.Iterations) if CoarsePlan is not None else 0
            ),
            "GuideOverflowPeak": (
                CoarsePlan.OverflowPeak if CoarsePlan is not None else 0
            ),
            "CandidateBendWeight": Policy.DetailedRouting.CandidateBendWeight,
            "CandidateViaWeight": Policy.DetailedRouting.CandidateViaWeight,
            "LayerPenalty": Policy.DetailedRouting.LayerPenalty,
            "RoutingDemandEstimate": Demand.ToDictionary(),
            "DerivedRoutingBudget": AdaptiveBudget.ToDictionary(),
            "EffectiveAdaptiveControls": {
                "LayerCount": LayerCount,
                "MaximumPortalsPerTerminal": PortalLimit,
                "LaneCount": RouteLaneCount,
                "MaximumCandidatesPerNet": MaximumCandidates,
                "CandidateLimitsBySignal": dict(sorted(CandidateLimitsBySignal.items())),
            },
            "RoutingEscalationState": RoutingEscalationState(
                PortalMode=("unreserved" if UnreservedPortalMode else "reserved"),
                ReservationVariant=ReservationVariant,
                LaneDiversityLevel=LaneDiversityLevel,
                CandidateDiversityLevel=CandidateDiversityLevel,
                EffectiveRoutingLayers=LayerCount,
                AssignmentBudget=AssignmentExpansionLimit,
                CandidateFingerprint=BuildStableFingerprint({
                    Signal: [
                        Candidate.CandidateId
                        for Candidate in CandidatesBySignal[Signal]
                    ]
                    for Signal in sorted(CandidatesBySignal)
                }),
            ).ToDictionary(),
            "Deadline": Deadline.ToDictionary(),
            "AdaptiveEscalationHistory": [
                *EscalationHistory,
                *AssignmentRetryHistory,
            ],
            "RustAssignmentUsed": True,
            "NativeBatching": {
                "PortalRequestCount": WorkTelemetry["PortalRequestCount"],
                "PortalTargetCount": WorkTelemetry["PortalTargetCount"],
                "RouteTreeRequestCount": CandidateRequestCount,
                "PortalBatchCount": WorkTelemetry["PortalBatchCount"],
                "PortalCacheHit": WorkTelemetry["PortalCacheHit"],
                "RouteTreeBatchCount": RouteTreeBatchCount,
                "InitialCandidateRequestsPerSignal": InitialRequestLimit,
                "CandidateDiagnostics": {
                    Signal: {
                        Key: Value
                        for Key, Value in Values.items()
                        if Key != "Rejections"
                    }
                    for Signal, Values in sorted(CandidateDiagnostics.items())
                },
                "DeterministicRequestOrdering": True,
            },
            "PortalReservations": [
                Value.ToDictionary() for Value in PortalReservations
            ],
            "RustAssignmentExpansionLimit": AssignmentExpansionLimit,
            "RustAssignmentExpansions": InitialAssignmentExpansionCount,
            "LocalizedRepairPasses": len(RepairIterations),
            "LocalizedReroutedNetCount": len(ReroutedSignals),
            "LocalizedRepairOffenders": [
                list(Iteration.ConflictSignals)
                for Iteration in RepairIterations
            ],
            "FinalColumnOverflowHotspots": FinalColumnOverflowHotspots,
            "CandidateRejectionReasons": {
                Signal: Values.get("Rejections", {})
                for Signal, Values in sorted(CandidateDiagnostics.items())
            },
            "LocalGlobalTargetCounts": {
                Signal: {
                    "LocalTargets": (
                        len(Profile.Seed.ConnectedTargets)
                        if Profile.Seed is not None
                        else 0
                    ),
                    "GlobalTargets": len(Profile.Targets),
                }
                for Signal, Profile in sorted(Profiles.items())
            },
            "IncrementalExtensions": {
                Signal: {
                    "FullTreeLength": Candidate.Length,
                    "IncrementalLength": Candidate.IncrementalLength,
                    "IncrementalMaterial": Candidate.IncrementalMaterialCost,
                    "ReusedLocalNodeCount": Candidate.SeedNodeCount,
                    "AvoidedDuplicateTrunkNodes": Candidate.SeedNodeCount,
                }
                for Signal, Candidate in sorted(Selected.items())
            },
            "SameSignalReuseNodeCount": sum(
                Candidate.SeedNodeCount for Candidate in Selected.values()
            ),
            "LayerDeviations": {
                Signal: {
                    "SelectedLayer": Candidate.Layer,
                    "PreferredLayer": (
                        Policy.Organization.PreferredXLayer
                        if ":X:" in Candidate.CandidateId
                        else Policy.Organization.PreferredZLayer
                    ),
                }
                for Signal, Candidate in sorted(Selected.items())
                if Policy.Organization.Enabled
                and Candidate.Layer
                != (
                    Policy.Organization.PreferredXLayer
                    if ":X:" in Candidate.CandidateId
                    else Policy.Organization.PreferredZLayer
                )
            },
        },
    )

"""Circuit-agnostic PCB-style clustering and weighted gate placement."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from functools import lru_cache
from hashlib import sha256
from itertools import permutations
from math import ceil, sqrt
from statistics import median
from typing import Any, Callable, Iterable

from .Rotation import RotatedCellSize
from .Geometry import (
    BuildPlacedGate,
    GateAccessPositions,
    GetGateInputAccess,
    PlacedDesign,
    RectanglesOverlap,
)
from ..Routing.Technology import DefaultRedstoneRoutingTechnology
from ..Routing.Policy import ClusteringPolicy, NandPackingPolicy, PlacementPolicy
from ..Routing.Actions.Geometry import BuildPlacedCellGeometry
from ..Routing.Actions.Validation import (
    BuildPhysicalGraphs,
    ValidatePhysicalRoutes,
    ValidateTemplateIsolation,
)
from ..Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from ..Routing.ResourceGraph import (
    BuildRoutingEnvelope,
    FindClaimConflicts,
    FindSelfClaimConflicts,
    LocalRouteClaim,
    NormalizeRoutingEdge,
    RoutingResourceClaims,
    RoutingResourceGraph,
    ValidateLocalRouteClaims,
)


@dataclass(frozen=True)
class LocalClusterRouteCandidate:
    """One legal placement-owned tree considered by the cluster optimizer."""

    CandidateId: str
    Claim: LocalRouteClaim

    @property
    def LocalizedTargetCount(self) -> int:
        return len(set(self.Claim.ConnectedTargets))

    @property
    def FullVolume(self) -> int:
        Envelope = BuildRoutingEnvelope(
            self.Claim.Nodes,
            self.Claim.Claims.SupportCells,
            (Reservation.Position for Reservation in self.Claim.RepeaterReservations),
        )
        return Envelope.Width * Envelope.Height * Envelope.Depth

    @property
    def RepeaterCount(self) -> int:
        return len(self.Claim.RepeaterReservations)

    @property
    def RouteAndSupportBlocks(self) -> int:
        return (
            self.Claim.ExactRouteSignalBlocks
            + self.Claim.ExactRouteRefreshBlocks
            + self.Claim.ExactRouteSupportBlocks
        )


@dataclass(frozen=True)
class LocalClusterRoutingSelection:
    """Result of bounded exact candidate selection for one packed cluster."""

    Candidates: tuple[LocalClusterRouteCandidate, ...]
    AssignmentExpansions: int
    BudgetExhausted: bool
    RejectionCounts: dict[str, int]


def SelectJointLocalClusterCandidates(
    ResourceGraph: RoutingResourceGraph,
    BaseClaims: tuple[LocalRouteClaim, ...],
    CandidatesBySignal: dict[str, tuple[LocalClusterRouteCandidate, ...]],
    MaximumExpansions: int,
) -> LocalClusterRoutingSelection:
    """Select compatible local trees with deterministic volume-first tie breaks.

    An omitted signal is an explicit option: it remains entirely with the
    authoritative global router.  Every trial is checked against the same
    resource graph used by detailed routing, including support, air and
    electrical-clearance claims.
    """
    Signals = tuple(sorted(CandidatesBySignal))
    Best: tuple[LocalClusterRouteCandidate, ...] = ()
    BestScore: tuple[object, ...] | None = None
    Expansions = 0
    BudgetExhausted = False
    Rejections: dict[str, int] = {}

    def Score(Selected: tuple[LocalClusterRouteCandidate, ...]) -> tuple[object, ...]:
        return (
            -sum(Candidate.LocalizedTargetCount for Candidate in Selected),
            sum(Candidate.FullVolume for Candidate in Selected),
            sum(Candidate.RepeaterCount for Candidate in Selected),
            sum(Candidate.RouteAndSupportBlocks for Candidate in Selected),
            tuple(Candidate.CandidateId for Candidate in Selected),
        )

    def Search(Index: int, Selected: tuple[LocalClusterRouteCandidate, ...]) -> None:
        nonlocal Best, BestScore, Expansions, BudgetExhausted
        if Expansions >= MaximumExpansions:
            BudgetExhausted = True
            return
        Expansions += 1
        if Index == len(Signals):
            CandidateScore = Score(Selected)
            if BestScore is None or CandidateScore < BestScore:
                Best = Selected
                BestScore = CandidateScore
            return
        Signal = Signals[Index]
        # Skip is always legal and preserves global-router authority.
        Search(Index + 1, Selected)
        for Candidate in CandidatesBySignal[Signal]:
            if BudgetExhausted:
                return
            Trial = (*Selected, Candidate)
            try:
                ValidateLocalRouteClaims(
                    ResourceGraph,
                    (*BaseClaims, *(Item.Claim for Item in Trial)),
                )
            except ValueError as Error:
                Reason = str(Error).split(":", 1)[0]
                Rejections[Reason] = Rejections.get(Reason, 0) + 1
                continue
            Search(Index + 1, Trial)

    Search(0, ())
    return LocalClusterRoutingSelection(
        Candidates=Best,
        AssignmentExpansions=Expansions,
        BudgetExhausted=BudgetExhausted,
        RejectionCounts=dict(sorted(Rejections.items())),
    )


@dataclass(frozen=True)
class BoundaryDemandRecord:
    """Required cluster-boundary resources for one unresolved signal."""

    Signal: str
    UnresolvedTargets: int
    RequiredPortalSlots: int
    RequiredCorridorLanes: int
    PreferredBoundarySide: str


@dataclass(frozen=True)
class BoundaryCapacityRecord:
    """Physically available escape capacity on one cluster boundary."""

    BoundarySide: str
    LegalPortalSlots: int
    LegalCorridorLanes: int
    Overflow: int


@dataclass(frozen=True)
class InterClusterBoundaryDemand:
    """Distinct signals that must cross one logical cluster boundary."""

    Axis: str
    BoundaryIndex: int
    Signals: tuple[str, ...]

    @property
    def RequiredCorridorLanes(self) -> int:
        """Return one independently routable lane per distinct signal."""
        return len(self.Signals)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Axis": self.Axis,
            "BoundaryIndex": self.BoundaryIndex,
            "Signals": list(self.Signals),
            "RequiredCorridorLanes": self.RequiredCorridorLanes,
        }


@dataclass(frozen=True)
class InterClusterGapPlan:
    """Optional corridor spacing assigned to each cluster-grid boundary."""

    Enabled: bool
    RoutingSpacing: int
    TrackPitch: int
    ColumnExtraSpacing: tuple[tuple[int, int], ...]
    RowExtraSpacing: tuple[tuple[int, int], ...]
    BoundaryDemand: tuple[InterClusterBoundaryDemand, ...]

    def ColumnSpacingByBoundary(self) -> dict[int, int]:
        return dict(self.ColumnExtraSpacing)

    def RowSpacingByBoundary(self) -> dict[int, int]:
        return dict(self.RowExtraSpacing)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Enabled": self.Enabled,
            "RoutingSpacing": self.RoutingSpacing,
            "TrackPitch": self.TrackPitch,
            "ColumnExtraSpacing": {
                str(Boundary): Spacing
                for Boundary, Spacing in self.ColumnExtraSpacing
            },
            "RowExtraSpacing": {
                str(Boundary): Spacing
                for Boundary, Spacing in self.RowExtraSpacing
            },
            "BoundaryDemand": [
                Record.ToDictionary() for Record in self.BoundaryDemand
            ],
        }


@dataclass(frozen=True)
class HardBoundaryFeasibility:
    """Exact necessary conditions for retaining one placement boundary."""

    ClusterId: int
    RequiredSignals: tuple[str, ...]
    LegalEscapeSlotsBySignal: tuple[
        tuple[str, tuple[tuple[int, int, int], ...]], ...
    ]
    MatchedEntrances: tuple[
        tuple[str, tuple[int, int, int]], ...
    ]
    UniqueLegalSlotCount: int
    RejectionReasons: tuple[str, ...]

    @property
    def IsFeasible(self) -> bool:
        return not self.RejectionReasons


def EvaluateHardBoundaryFeasibility(
    ClusterId: int,
    DemandRecords: tuple[BoundaryDemandRecord, ...],
    LegalEscapeSlotsBySignal: dict[
        str, set[tuple[int, int, int]] | tuple[tuple[int, int, int], ...]
    ],
) -> HardBoundaryFeasibility:
    """Prove only no-escape and capacity-one entrance impossibility.

    Fanout and preferred-side overflow are deliberately excluded. One global
    tree may branch after entering a cluster, so they remain ranking signals
    rather than hard rejection conditions.
    """
    RequiredSignals = tuple(sorted({
        Record.Signal
        for Record in DemandRecords
        if Record.RequiredPortalSlots > 0
    }))
    NormalizedSlots = {
        Signal: tuple(sorted(set(LegalEscapeSlotsBySignal.get(Signal, ()))))
        for Signal in RequiredSignals
    }
    SlotOwner: dict[tuple[int, int, int], str] = {}
    MatchedSlotBySignal: dict[str, tuple[int, int, int]] = {}

    def TryAssign(
        Signal: str,
        SeenSlots: set[tuple[int, int, int]],
    ) -> bool:
        for Slot in NormalizedSlots[Signal]:
            if Slot in SeenSlots:
                continue
            SeenSlots.add(Slot)
            ExistingSignal = SlotOwner.get(Slot)
            if ExistingSignal is not None and not TryAssign(
                ExistingSignal,
                SeenSlots,
            ):
                continue
            SlotOwner[Slot] = Signal
            MatchedSlotBySignal[Signal] = Slot
            return True
        return False

    for Signal in sorted(
        RequiredSignals,
        key=lambda Value: (len(NormalizedSlots[Value]), Value),
    ):
        TryAssign(Signal, set())

    NoEscapeSignals = tuple(
        Signal for Signal in RequiredSignals if not NormalizedSlots[Signal]
    )
    RejectionReasons = [
        f"NoBoundaryEscape:Cluster={ClusterId}:Signal={Signal}"
        for Signal in NoEscapeSignals
    ]
    UnmatchedSignals = tuple(
        Signal
        for Signal in RequiredSignals
        if Signal not in MatchedSlotBySignal
    )
    UniqueLegalSlots = {
        Slot
        for Signal in RequiredSignals
        for Slot in NormalizedSlots[Signal]
    }
    if UnmatchedSignals and not NoEscapeSignals:
        RejectionReasons.append(
            "HardEntranceCapacityExceeded:"
            f"Cluster={ClusterId}:Required={len(RequiredSignals)}:"
            f"Matched={len(MatchedSlotBySignal)}:"
            f"UniqueSlots={len(UniqueLegalSlots)}:"
            f"Unmatched={','.join(UnmatchedSignals)}"
        )
    return HardBoundaryFeasibility(
        ClusterId=ClusterId,
        RequiredSignals=RequiredSignals,
        LegalEscapeSlotsBySignal=tuple(
            (Signal, NormalizedSlots[Signal])
            for Signal in RequiredSignals
        ),
        MatchedEntrances=tuple(sorted(MatchedSlotBySignal.items())),
        UniqueLegalSlotCount=len(UniqueLegalSlots),
        RejectionReasons=tuple(RejectionReasons),
    )


def ValidateHardBoundaryFeasibility(
    Result: HardBoundaryFeasibility,
) -> None:
    """Reject before a staged placement or local claim can be retained."""
    if Result.IsFeasible:
        return
    SlotsBySignal = dict(Result.LegalEscapeSlotsBySignal)
    NoEscapeSignals = tuple(
        Signal
        for Signal in Result.RequiredSignals
        if not SlotsBySignal.get(Signal)
    )
    MatchedSignals = {
        Signal for Signal, _Slot in Result.MatchedEntrances
    }
    AffectedSignals = NoEscapeSignals or tuple(
        Signal
        for Signal in Result.RequiredSignals
        if Signal not in MatchedSignals
    )
    Reason = (
        RoutingFailureReason.NoBoundaryEscape
        if NoEscapeSignals
        else RoutingFailureReason.ClusterEntranceBudgetExceeded
    )
    raise RoutingStageError(
        RoutingFailure(
            Reason=Reason,
            Stage="PlacementBoundaryFeasibility",
            AffectedNets=AffectedSignals,
            Detail=(
                "Hard boundary infeasible: "
                + "; ".join(Result.RejectionReasons)
            ),
            Diagnostics={
                "ClusterId": Result.ClusterId,
                "RequiredSignals": list(Result.RequiredSignals),
                "MatchedEntrances": [
                    [Signal, list(Slot)]
                    for Signal, Slot in Result.MatchedEntrances
                ],
                "UniqueLegalSlotCount": Result.UniqueLegalSlotCount,
                "RejectionReasons": list(Result.RejectionReasons),
            },
        )
    )


def BuildBoundaryCapacityRecords(
    DemandRecords: tuple[BoundaryDemandRecord, ...],
    GeometricCapacityBySide: dict[str, int],
    LegalPortalSlotsBySide: dict[str, int],
) -> tuple[BoundaryCapacityRecord, ...]:
    """Measure soft corridor capacity through physically legal portal slots."""
    RequiredBySide = {
        Side: sum(
            Record.RequiredCorridorLanes
            for Record in DemandRecords
            if Record.PreferredBoundarySide == Side
        )
        for Side in ("West", "East", "North", "South")
    }
    Records = []
    for Side in ("West", "East", "North", "South"):
        LegalPortalSlots = max(0, LegalPortalSlotsBySide.get(Side, 0))
        GeometricCorridorLanes = max(
            0,
            GeometricCapacityBySide.get(Side, 0),
        )
        LegalCorridorLanes = min(
            GeometricCorridorLanes,
            LegalPortalSlots,
        )
        Records.append(
            BoundaryCapacityRecord(
                BoundarySide=Side,
                LegalPortalSlots=LegalPortalSlots,
                LegalCorridorLanes=LegalCorridorLanes,
                Overflow=max(
                    0,
                    RequiredBySide[Side] - LegalCorridorLanes,
                ),
            )
        )
    return tuple(Records)


def AssignBoundaryDemandSides(
    DemandRecords: tuple[BoundaryDemandRecord, ...],
    LegalEscapeSlotsBySignal: dict[
        str, set[tuple[int, int, int]] | tuple[tuple[int, int, int], ...]
    ],
    Bounds: tuple[int, int, int, int],
    CorridorCapacityBySide: dict[str, int],
) -> tuple[BoundaryDemandRecord, ...]:
    """Assign packed boundary signals to legal sides without lane overflow."""
    if not DemandRecords:
        return ()
    MinimumX, MaximumX, MinimumZ, MaximumZ = Bounds
    SideOrder = ("West", "East", "North", "South")

    def SlotSide(Position: tuple[int, int, int]) -> str:
        X, _Y, Z = Position
        return min(
            (
                (abs(X - MinimumX), 0, "West"),
                (abs(X - MaximumX), 1, "East"),
                (abs(Z - MinimumZ), 2, "North"),
                (abs(Z - MaximumZ), 3, "South"),
            )
        )[2]

    AvailableSides = {
        Record.Signal: tuple(
            Side
            for Side in SideOrder
            if Side in {
                SlotSide(Position)
                for Position in LegalEscapeSlotsBySignal.get(Record.Signal, ())
            }
            and CorridorCapacityBySide.get(Side, 0) > 0
        )
        for Record in DemandRecords
    }
    PreferredUsage = {
        Side: sum(
            Record.PreferredBoundarySide == Side
            for Record in DemandRecords
        )
        for Side in SideOrder
    }
    if all(
        Record.PreferredBoundarySide in AvailableSides[Record.Signal]
        for Record in DemandRecords
    ) and all(
        PreferredUsage[Side] <= CorridorCapacityBySide.get(Side, 0)
        for Side in SideOrder
    ):
        return DemandRecords
    OrderedRecords = sorted(
        DemandRecords,
        key=lambda Record: (
            len(AvailableSides[Record.Signal]),
            -Record.UnresolvedTargets,
            Record.Signal,
        ),
    )
    Usage = {Side: 0 for Side in SideOrder}
    Assignment: dict[str, str] = {}
    Best: tuple[int, tuple[str, ...], dict[str, str]] | None = None
    SeenCostByState: dict[tuple[int, tuple[int, ...]], int] = {}

    def Search(Index: int, PreferenceMisses: int) -> None:
        nonlocal Best
        State = (Index, tuple(Usage[Side] for Side in SideOrder))
        PriorCost = SeenCostByState.get(State)
        if PriorCost is not None and PreferenceMisses > PriorCost:
            return
        SeenCostByState[State] = PreferenceMisses
        if Best is not None and PreferenceMisses > Best[0]:
            return
        if Index == len(OrderedRecords):
            StableSides = tuple(
                Assignment[Record.Signal]
                for Record in sorted(DemandRecords, key=lambda Value: Value.Signal)
            )
            Candidate = (PreferenceMisses, StableSides, dict(Assignment))
            if Best is None or Candidate[:2] < Best[:2]:
                Best = Candidate
            return
        Record = OrderedRecords[Index]
        Options = sorted(
            AvailableSides[Record.Signal],
            key=lambda Side: (
                Side != Record.PreferredBoundarySide,
                Usage[Side],
                SideOrder.index(Side),
            ),
        )
        for Side in Options:
            if Usage[Side] >= CorridorCapacityBySide.get(Side, 0):
                continue
            Assignment[Record.Signal] = Side
            Usage[Side] += 1
            Search(
                Index + 1,
                PreferenceMisses + (Side != Record.PreferredBoundarySide),
            )
            Usage[Side] -= 1
            del Assignment[Record.Signal]

    Search(0, 0)
    if Best is None:
        return DemandRecords
    Selected = Best[2]
    return tuple(
        BoundaryDemandRecord(
            Signal=Record.Signal,
            UnresolvedTargets=Record.UnresolvedTargets,
            RequiredPortalSlots=Record.RequiredPortalSlots,
            RequiredCorridorLanes=Record.RequiredCorridorLanes,
            PreferredBoundarySide=Selected[Record.Signal],
        )
        for Record in DemandRecords
    )


def BuildLegalBoundaryEscapeSlots(
    Signals: set[str],
    AccessPositionsBySignal: dict[str, set[tuple[int, int, int]]],
    ResourceGraph: RoutingResourceGraph,
    FixedAccessClaimsBySignal: dict[str, RoutingResourceClaims],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, set[tuple[int, int, int]]]:
    """Enumerate exact one-primitive exits from immutable terminal access."""
    Result: dict[str, set[tuple[int, int, int]]] = {}
    OrderedSignals = sorted(Signals)
    for SignalIndex, Signal in enumerate(OrderedSignals):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "boundary-escape-signal",
                "CompletedSignals": SignalIndex,
                "TotalSignals": len(OrderedSignals),
                "Signal": Signal,
            })
        AllowedAccess = frozenset(AccessPositionsBySignal.get(Signal, ()))
        LegalSlots: set[tuple[int, int, int]] = set()
        for AnchorIndex, Anchor in enumerate(sorted(AllowedAccess)):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "boundary-escape-anchor",
                    "Signal": Signal,
                    "CompletedAnchors": AnchorIndex,
                    "TotalAnchors": len(AllowedAccess),
                })
            if not ResourceGraph.IsLegalNode(Anchor, AllowedAccess):
                continue
            for Neighbor in sorted(
                DefaultRedstoneRoutingTechnology.NeighborPositions(Anchor)
            ):
                if not ResourceGraph.IsLegalNode(Neighbor, AllowedAccess):
                    continue
                Primitive = ResourceGraph.BuildPrimitive(Anchor, Neighbor)
                if Primitive is None:
                    continue
                CandidateClaims = ResourceGraph.BuildRouteClaims(
                    (Anchor, Neighbor)
                )
                if any(
                    FindClaimConflicts({
                        Signal: CandidateClaims,
                        OtherSignal: OtherClaims,
                    })
                    for OtherSignal, OtherClaims in (
                        FixedAccessClaimsBySignal.items()
                    )
                    if OtherSignal != Signal
                ):
                    continue
                LegalSlots.add(Neighbor)
        Result[Signal] = LegalSlots
    return Result


@dataclass(frozen=True)
class PackedNandCluster:
    """Physical packing metadata; members remain independent NAND cells."""

    ClusterId: int
    MemberNands: tuple[str, ...]
    BoundarySignals: tuple[str, ...]
    InternalSignals: tuple[str, ...]
    RelativePlacements: dict[str, tuple[int, int, int, bool]]
    DirectConnections: tuple[str, ...]
    LocalClaimSignals: tuple[str, ...] = ()
    BoundaryTerminals: tuple[tuple[int, int, int], ...] = ()
    ExactLocalRoutingBlocks: int = 0
    GlobalEntrances: int = 0
    RejectionReasons: tuple[str, ...] = ()
    StructuralSignature: str = ""
    ReusedFromClusterId: int | None = None
    StructuralMapping: dict[str, str] | None = None
    StackId: int | None = None
    StackLevel: int = 0
    BaseY: int = 1
    BoundaryDemand: dict[str, int] | None = None
    EstimatedCorridorLanes: int = 0
    LocalClaimCoverage: float = 0.0
    BoundaryDemandRecords: tuple[BoundaryDemandRecord, ...] = ()
    BoundaryCapacityRecords: tuple[BoundaryCapacityRecord, ...] = ()
    BoundaryOverflow: int = 0
    PinScarcityCount: int = 0


@dataclass(frozen=True)
class PackedNandClusterCandidate:
    """Transactional packed placement with locally owned route material."""

    ClusterId: int
    Placements: dict[str, tuple[int, int, int, bool]]
    LocalClaims: tuple[LocalRouteClaim, ...]
    BoundaryTerminals: tuple[tuple[int, int, int], ...]
    RoutingOwnedBlocks: int
    RawDustBlocks: int
    SupportBlocks: int
    Footprint: int
    RejectionReasons: tuple[str, ...] = ()
    BoundaryDemandRecords: tuple[BoundaryDemandRecord, ...] = ()
    BoundaryCapacityRecords: tuple[BoundaryCapacityRecord, ...] = ()
    BoundaryOverflow: int = 0
    LocalClaimCoverage: float = 0.0


@dataclass(frozen=True)
class PcbPlacement:
    """Weighted placement plus global routing metadata."""

    Placed: PlacedDesign
    Clusters: tuple[tuple[str, ...], ...]
    SignalOrder: tuple[str, ...]
    LayerCount: int
    PackedClusters: tuple[PackedNandCluster, ...] = ()


def BuildTopologicalLevels(
    Module: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, int]:
    """Assign every gate to a left-to-right combinational depth."""
    Producers = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    Levels = {
        Gate.Name: 0
        for Gate in Module.Gates
        if Gate.Kind.value == "INPUT"
    }
    Pending = [
        Gate
        for Gate in Module.Gates
        if Gate.Kind.value not in ("INPUT", "OUTPUT")
    ]
    PassIndex = 0
    while Pending:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "topological-levels",
                "PassIndex": PassIndex,
                "PendingGates": len(Pending),
            })
        Remaining = []
        for GateIndex, Gate in enumerate(Pending):
            if WorkCheck is not None and GateIndex % 32 == 0:
                WorkCheck({
                    "Phase": "topological-level-gate",
                    "PassIndex": PassIndex,
                    "CompletedGates": GateIndex,
                    "TotalGates": len(Pending),
                })
            ProducerNames = [
                Producers[Signal].Name
                for Signal in Gate.Inputs
            ]
            if any(Name not in Levels for Name in ProducerNames):
                Remaining.append(Gate)
                continue
            Levels[Gate.Name] = 1 + max(
                Levels[Name]
                for Name in ProducerNames
            )
        if len(Remaining) == len(Pending):
            Names = ", ".join(Gate.Name for Gate in Remaining)
            raise ValueError(
                f"PCB placement found a combinational cycle: {Names}"
            )
        Pending = Remaining
        PassIndex += 1

    OutputLevel = max(Levels.values(), default=0) + 1
    for Gate in Module.Gates:
        if Gate.Kind.value == "OUTPUT":
            Levels[Gate.Name] = OutputLevel
    return Levels


def BuildConnectivityClusters(
    Module: Any,
    MaximumClusterSize: int = 32,
    Policy: ClusteringPolicy | None = None,
    MaximumBoundaryTerminals: int | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[tuple[str, ...], ...]:
    """Agglomerate strongly connected NAND gates without circuit recognition."""
    if WorkCheck is not None:
        WorkCheck({"Phase": "connectivity-clusters-start"})
    Internal = [Gate for Gate in Module.Gates if Gate.Kind.value == "NAND"]
    if not Internal:
        return ()
    InternalNames = {Gate.Name for Gate in Internal}
    Producers = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    EdgeWeights: dict[frozenset[str], int] = {}
    for GateIndex, Gate in enumerate(Internal):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "connectivity-cluster-edges",
                "CompletedGates": GateIndex,
                "TotalGates": len(Internal),
            })
        for Signal in Gate.Inputs:
            Producer = Producers.get(Signal)
            if Producer not in InternalNames or Producer == Gate.Name:
                continue
            Key = frozenset((Producer, Gate.Name))
            EdgeWeights[Key] = EdgeWeights.get(Key, 0) + 1
    Levels = BuildTopologicalLevels(Module, WorkCheck=WorkCheck)
    BoundaryEvaluationCount = 0

    def BoundaryCount(Names: set[str]) -> int:
        nonlocal BoundaryEvaluationCount
        BoundaryEvaluationCount += 1
        if WorkCheck is not None and BoundaryEvaluationCount % 16 == 1:
            WorkCheck({
                "Phase": "connectivity-boundary-count",
                "BoundaryEvaluationCount": BoundaryEvaluationCount,
                "ClusterGateCount": len(Names),
            })
        Result = 0
        for Gate in Module.Gates:
            GateInside = Gate.Name in Names
            for Signal in Gate.Inputs:
                Producer = Producers.get(Signal)
                ProducerInside = Producer in Names
                if GateInside != ProducerInside:
                    Result += 1
            if GateInside and any(
                Signal in Module.Outputs for Signal in Gate.Outputs
            ):
                Result += 1
        return Result

    Clusters = {Index: {Gate.Name} for Index, Gate in enumerate(Internal)}
    MergePass = 0
    while True:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "connectivity-cluster-merge",
                "MergePass": MergePass,
                "ClusterCount": len(Clusters),
            })
        BestPair = None
        BestScore = None
        ClusterIds = sorted(Clusters)
        PairCount = 0
        for FirstIndex, FirstId in enumerate(ClusterIds):
            for SecondId in ClusterIds[FirstIndex + 1 :]:
                PairCount += 1
                if WorkCheck is not None and PairCount % 32 == 1:
                    WorkCheck({
                        "Phase": "connectivity-cluster-pair",
                        "MergePass": MergePass,
                        "CompletedPairs": PairCount - 1,
                    })
                First = Clusters[FirstId]
                Second = Clusters[SecondId]
                if len(First) + len(Second) > MaximumClusterSize:
                    continue
                CrossWeight = sum(
                    Weight
                    for Pair, Weight in EdgeWeights.items()
                    if len(Pair & First) == 1 and len(Pair & Second) == 1
                )
                if CrossWeight <= 0:
                    continue
                Combined = First | Second
                CombinedBoundary = BoundaryCount(Combined)
                if (
                    MaximumBoundaryTerminals is not None
                    and CombinedBoundary > MaximumBoundaryTerminals
                ):
                    continue
                Diameter = (
                    max(Levels[Name] for Name in Combined)
                    - min(Levels[Name] for Name in Combined)
                )
                CutReduction = (
                    BoundaryCount(First)
                    + BoundaryCount(Second)
                    - CombinedBoundary
                )
                AdaptiveScore = (
                    CutReduction * Policy.CutWeight
                    - max(0, Diameter - 4) * Policy.BalanceWeight
                    if Policy is not None
                    else CrossWeight
                )
                Score = (
                    CrossWeight,
                    -(len(First) + len(Second)),
                    -FirstId,
                    -SecondId,
                    AdaptiveScore,
                    -CombinedBoundary,
                    -Diameter,
                )
                if BestScore is None or Score > BestScore:
                    BestScore = Score
                    BestPair = FirstId, SecondId
        if BestPair is None:
            break
        FirstId, SecondId = BestPair
        Clusters[FirstId].update(Clusters.pop(SecondId))
        MergePass += 1

    OriginalOrder = {
        Gate.Name: Index
        for Index, Gate in enumerate(Module.Gates)
    }
    return tuple(
        tuple(sorted(Names, key=OriginalOrder.__getitem__))
        for _ClusterId, Names in sorted(Clusters.items())
    )


def AnalyzeNandClusterStructure(
    Module: Any,
    Names: tuple[str, ...],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[
    str,
    tuple[str, ...],
    dict[str, int],
    dict[str, tuple[Any, ...]],
    frozenset[tuple[str, str, int]],
]:
    """Build a name-independent directed signature for one NAND island."""
    NameSet = set(Names)
    GateByName = {Gate.Name: Gate for Gate in Module.Gates}
    Producers = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    Consumers: dict[str, list[Any]] = {}
    for GateIndex, Gate in enumerate(Module.Gates):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "structural-analysis-consumers",
                "CompletedGates": GateIndex,
                "TotalGates": len(Module.Gates),
            })
        for Signal in Gate.Inputs:
            Consumers.setdefault(Signal, []).append(Gate)
    BoundarySignals = sorted({
        Signal
        for Name in Names
        for Signal in GateByName[Name].Inputs
        if Producers.get(Signal) not in NameSet
    })
    Nodes = tuple(
        [*(f"B:{Signal}" for Signal in BoundarySignals)]
        + [*(f"G:{Name}" for Name in Names)]
    )
    Edges = set()
    for NameIndex, Name in enumerate(Names):
        if WorkCheck is not None and NameIndex % 32 == 0:
            WorkCheck({
                "Phase": "structural-analysis-edges",
                "CompletedNodes": NameIndex,
                "TotalNodes": len(Names),
            })
        Gate = GateByName[Name]
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Producer = Producers.get(Signal)
            Source = (
                f"G:{Producer}"
                if Producer in NameSet
                else f"B:{Signal}"
            )
            Edges.add((Source, f"G:{Name}", InputIndex))
    Incoming: dict[str, list[tuple[str, int]]] = {Node: [] for Node in Nodes}
    Outgoing: dict[str, list[tuple[str, int]]] = {Node: [] for Node in Nodes}
    for Source, Target, InputIndex in Edges:
        Incoming[Target].append((Source, InputIndex))
        Outgoing[Source].append((Target, InputIndex))
    Initial: dict[str, tuple[Any, ...]] = {}
    for Node in Nodes:
        if Node.startswith("B:"):
            Initial[Node] = ("BoundaryInput", len(Outgoing[Node]))
            continue
        Name = Node[2:]
        Gate = GateByName[Name]
        InternalInputs = sum(Source.startswith("G:") for Source, _ in Incoming[Node])
        HasExternalFanout = any(
            Consumer.Name not in NameSet
            for Signal in Gate.Outputs
            for Consumer in Consumers.get(Signal, ())
        )
        Initial[Node] = (
            "NAND",
            InternalInputs,
            len(Incoming[Node]) - InternalInputs,
            len(Outgoing[Node]),
            HasExternalFanout,
        )
    InitialValues = {Value for Value in Initial.values()}
    InitialIds = {Value: Index for Index, Value in enumerate(sorted(InitialValues))}
    Colors = {Node: InitialIds[Initial[Node]] for Node in Nodes}
    for _Pass in range(len(Nodes) + 1):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "structural-analysis-coloring",
                "PassIndex": _Pass,
                "NodeCount": len(Nodes),
            })
        Descriptors = {
            Node: (
                Initial[Node],
                tuple(sorted((InputIndex, Colors[Source]) for Source, InputIndex in Incoming[Node])),
                tuple(sorted((InputIndex, Colors[Target]) for Target, InputIndex in Outgoing[Node])),
            )
            for Node in Nodes
        }
        DescriptorIds = {
            Value: Index
            for Index, Value in enumerate(sorted(set(Descriptors.values())))
        }
        NextColors = {Node: DescriptorIds[Descriptors[Node]] for Node in Nodes}
        if NextColors == Colors:
            break
        Colors = NextColors
    Canonical = (
        tuple(sorted((Initial[Node], Colors[Node]) for Node in Nodes)),
        tuple(sorted(
            (Colors[Source], Colors[Target], InputIndex)
            for Source, Target, InputIndex in Edges
        )),
    )
    Signature = sha256(repr(Canonical).encode("utf-8")).hexdigest()[:16]
    return Signature, Nodes, Colors, Initial, frozenset(Edges)


def FindIsomorphicNandClusterMapping(
    Module: Any,
    ReferenceNames: tuple[str, ...],
    CandidateNames: tuple[str, ...],
    MaximumMappings: int = 4096,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[str, dict[str, str]] | None:
    """Return an exact structural gate mapping without circuit recognition."""
    Reference = AnalyzeNandClusterStructure(
        Module,
        ReferenceNames,
        WorkCheck=WorkCheck,
    )
    Candidate = AnalyzeNandClusterStructure(
        Module,
        CandidateNames,
        WorkCheck=WorkCheck,
    )
    ReferenceSignature, ReferenceNodes, ReferenceColors, ReferenceInitial, ReferenceEdges = Reference
    CandidateSignature, CandidateNodes, CandidateColors, CandidateInitial, CandidateEdges = Candidate
    if ReferenceSignature != CandidateSignature or len(ReferenceNodes) != len(CandidateNodes):
        return None
    ReferenceGroups: dict[tuple[Any, ...], list[str]] = {}
    CandidateGroups: dict[tuple[Any, ...], list[str]] = {}
    for Node in ReferenceNodes:
        ReferenceGroups.setdefault(
            (ReferenceInitial[Node], ReferenceColors[Node]), []
        ).append(Node)
    for Node in CandidateNodes:
        CandidateGroups.setdefault(
            (CandidateInitial[Node], CandidateColors[Node]), []
        ).append(Node)
    if {
        Key: len(Values) for Key, Values in ReferenceGroups.items()
    } != {
        Key: len(Values) for Key, Values in CandidateGroups.items()
    }:
        return None
    Groups = sorted(
        ReferenceGroups,
        key=lambda Key: (len(ReferenceGroups[Key]), repr(Key)),
    )
    AttemptCount = 0
    Mapping: dict[str, str] = {}

    def IsConsistent() -> bool:
        MappedEdges = {
            (Mapping[Source], Mapping[Target], InputIndex)
            for Source, Target, InputIndex in ReferenceEdges
            if Source in Mapping and Target in Mapping
        }
        return MappedEdges.issubset(CandidateEdges)

    def Search(GroupIndex: int) -> bool:
        nonlocal AttemptCount
        if GroupIndex == len(Groups):
            return {
                (Mapping[Source], Mapping[Target], InputIndex)
                for Source, Target, InputIndex in ReferenceEdges
            } == CandidateEdges
        Key = Groups[GroupIndex]
        ReferenceValues = sorted(ReferenceGroups[Key])
        CandidateValues = sorted(CandidateGroups[Key])
        for Permutation in permutations(CandidateValues):
            AttemptCount += 1
            if WorkCheck is not None and AttemptCount % 32 == 1:
                WorkCheck({
                    "Phase": "structural-mapping-search",
                    "AttemptCount": AttemptCount,
                    "MaximumMappings": MaximumMappings,
                    "GroupIndex": GroupIndex,
                })
            if AttemptCount > MaximumMappings:
                return False
            Mapping.update(zip(ReferenceValues, Permutation))
            if IsConsistent() and Search(GroupIndex + 1):
                return True
            for Node in ReferenceValues:
                Mapping.pop(Node, None)
        return False

    if not Search(0):
        return None
    return ReferenceSignature, {
        ReferenceNode[2:]: CandidateNode[2:]
        for ReferenceNode, CandidateNode in Mapping.items()
        if ReferenceNode.startswith("G:")
    }


def OptimizeClusterSlots(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Levels: dict[str, int],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[dict[int, tuple[int, int]], int, int]:
    """Place clusters on a compact grid using weighted net length."""
    Count = len(Clusters)
    if Count == 0:
        return {}, 0, 0
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    Producers = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    DirectedWeights: dict[tuple[int, int], int] = {}
    InputWeights = {Index: 0 for Index in range(Count)}
    OutputWeights = {Index: 0 for Index in range(Count)}
    for GateIndex, Gate in enumerate(Module.Gates):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "cluster-slot-net-weights",
                "CompletedGates": GateIndex,
                "TotalGates": len(Module.Gates),
            })
        TargetCluster = ClusterByGate.get(Gate.Name)
        for Signal in Gate.Inputs:
            Producer = Producers[Signal]
            SourceCluster = ClusterByGate.get(Producer.Name)
            if SourceCluster is None and TargetCluster is not None:
                InputWeights[TargetCluster] += 1
            elif (
                SourceCluster is not None
                and TargetCluster is not None
                and SourceCluster != TargetCluster
            ):
                Key = SourceCluster, TargetCluster
                DirectedWeights[Key] = DirectedWeights.get(Key, 0) + 1
            elif SourceCluster is not None and Gate.Kind.value == "OUTPUT":
                OutputWeights[SourceCluster] += 1

    Incoming = {Index: 0 for Index in range(Count)}
    Outgoing: dict[int, set[int]] = {Index: set() for Index in range(Count)}
    for Source, Target in DirectedWeights:
        if Target not in Outgoing[Source]:
            Outgoing[Source].add(Target)
            Incoming[Target] += 1
    PendingClusters = sorted(Index for Index, Degree in Incoming.items() if Degree == 0)
    TopologicalClusters = []
    while PendingClusters:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "cluster-slot-topology",
                "CompletedClusters": len(TopologicalClusters),
                "TotalClusters": Count,
            })
        Current = PendingClusters.pop(0)
        TopologicalClusters.append(Current)
        for Target in sorted(Outgoing[Current]):
            Incoming[Target] -= 1
            if Incoming[Target] == 0:
                PendingClusters.append(Target)
                PendingClusters.sort()
    IsAcyclic = len(TopologicalClusters) == Count
    if IsAcyclic:
        ClusterLevels = {Index: 0 for Index in range(Count)}
        for Source in TopologicalClusters:
            for Target in Outgoing[Source]:
                ClusterLevels[Target] = max(
                    ClusterLevels[Target], ClusterLevels[Source] + 1
                )
        Columns = max(ClusterLevels.values(), default=0) + 1
        ClustersByColumn: dict[int, list[int]] = {}
        for ClusterIndex, Column in ClusterLevels.items():
            ClustersByColumn.setdefault(Column, []).append(ClusterIndex)
        Rows = max((len(Values) for Values in ClustersByColumn.values()), default=1)
    else:
        # Contracting an acyclic gate graph can create cycles between clusters.
        # Cyclic contracted graphs use a compact weighted layout instead of
        # repeatedly drifting right during precedence relaxation.
        Columns = max(1, ceil(sqrt(2 * Count)))
        Rows = max(1, ceil(Count / Columns))
    Slots = [
        (Column, Row)
        for Column in range(Columns)
        for Row in range(Rows)
    ]
    if IsAcyclic:
        Assignment = {}
        for Column in range(Columns):
            OrderedColumn = sorted(
                ClustersByColumn.get(Column, []),
                key=lambda Index: (
                    min(Levels[Name] for Name in Clusters[Index]),
                    Index,
                ),
            )
            for Row, ClusterIndex in enumerate(OrderedColumn):
                Assignment[ClusterIndex] = Column, Row
    else:
        OrderedClusters = sorted(
            range(Count),
            key=lambda Index: (
                median(Levels[Name] for Name in Clusters[Index]),
                min(Levels[Name] for Name in Clusters[Index]),
                Index,
            ),
        )
        Assignment = {
            ClusterIndex: Slots[Position]
            for Position, ClusterIndex in enumerate(OrderedClusters)
        }

    def PlacementCost(Values: dict[int, tuple[int, int]]) -> int:
        Cost = 0
        for (Source, Target), Weight in DirectedWeights.items():
            SourceX, SourceZ = Values[Source]
            TargetX, TargetZ = Values[Target]
            Cost += Weight * (
                10 * (abs(SourceX - TargetX) + abs(SourceZ - TargetZ))
                + (4 if TargetX < SourceX else 0)
            )
        for ClusterIndex, (Column, _Row) in Values.items():
            Cost += InputWeights[ClusterIndex] * Column * 5
            Cost += OutputWeights[ClusterIndex] * (Columns - 1 - Column) * 5
        return Cost

    for _Pass in range(12):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "cluster-slot-optimization",
                "PassIndex": _Pass,
                "ClusterCount": Count,
                "SlotCount": len(Slots),
            })
        CurrentCost = PlacementCost(Assignment)
        Best = None
        BestCost = CurrentCost
        Occupied = {Slot: Index for Index, Slot in Assignment.items()}
        for ClusterIndex in range(Count):
            CurrentSlot = Assignment[ClusterIndex]
            for SlotIndex, Slot in enumerate(Slots):
                if WorkCheck is not None and SlotIndex % 32 == 0:
                    WorkCheck({
                        "Phase": "cluster-slot-candidate",
                        "PassIndex": _Pass,
                        "ClusterIndex": ClusterIndex,
                        "CompletedSlots": SlotIndex,
                        "TotalSlots": len(Slots),
                    })
                if IsAcyclic and Slot[0] != CurrentSlot[0]:
                    continue
                Other = Occupied.get(Slot)
                if Other == ClusterIndex:
                    continue
                Candidate = dict(Assignment)
                OldSlot = Candidate[ClusterIndex]
                Candidate[ClusterIndex] = Slot
                if Other is not None:
                    Candidate[Other] = OldSlot
                CandidateCost = PlacementCost(Candidate)
                if CandidateCost < BestCost:
                    BestCost = CandidateCost
                    Best = Candidate
        if Best is None:
            break
        Assignment = Best
    return Assignment, Columns, Rows


def BuildRelocationClusterSet(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    RelocationSignals: frozenset[str] = frozenset(),
) -> frozenset[int]:
    """Map routing offenders to every producer/consumer cluster they touch."""
    if not RelocationSignals:
        return frozenset()
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerBySignal = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    Result: set[int] = set()
    for Signal in RelocationSignals:
        ProducerCluster = ClusterByGate.get(ProducerBySignal.get(Signal, ""))
        if ProducerCluster is not None:
            Result.add(ProducerCluster)
        Result.update(
            ClusterByGate[Gate.Name]
            for Gate in Module.Gates
            if Signal in Gate.Inputs and Gate.Name in ClusterByGate
        )
    return frozenset(Result)


def PrioritizeRelocationClusters(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    RelocationSignals: frozenset[str] = frozenset(),
) -> tuple[int, ...]:
    """Rank clusters by how many reported conflict signals touch them."""
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerBySignal = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    Scores: dict[int, int] = {}
    for Signal in RelocationSignals:
        Touched = set()
        ProducerCluster = ClusterByGate.get(ProducerBySignal.get(Signal, ""))
        if ProducerCluster is not None:
            Touched.add(ProducerCluster)
        Touched.update(
            ClusterByGate[Gate.Name]
            for Gate in Module.Gates
            if Signal in Gate.Inputs and Gate.Name in ClusterByGate
        )
        for ClusterIndex in Touched:
            Scores[ClusterIndex] = Scores.get(ClusterIndex, 0) + 1
    return tuple(sorted(Scores, key=lambda Value: (-Scores[Value], Value)))


def RelocateClusterSlots(
    Assignment: dict[int, tuple[int, int]],
    ColumnCount: int,
    RelocationClusters: Iterable[int],
    StackSuppressedClusters: frozenset[int] = frozenset(),
) -> tuple[dict[int, tuple[int, int]], int]:
    """Move a congestion cut into deterministic unoccupied placement rows."""
    Result = dict(Assignment)
    OrderedClusters = (
        tuple(sorted(RelocationClusters))
        if isinstance(RelocationClusters, set | frozenset)
        else tuple(dict.fromkeys(RelocationClusters))
    )
    Pending = [
        ClusterIndex
        for ClusterIndex in OrderedClusters
        if ClusterIndex not in StackSuppressedClusters
    ]
    if not Pending:
        return Result, ColumnCount
    if len(Pending) > 1:
        ExistingSlots = tuple(Result[ClusterIndex] for ClusterIndex in Pending)
        if len(set(ExistingSlots)) == len(ExistingSlots):
            for Offset, ClusterIndex in enumerate(Pending):
                Result[ClusterIndex] = ExistingSlots[(Offset + 1) % len(Pending)]
            return Result, ColumnCount
        # A suppressed vertical stack leaves multiple clusters in the same
        # logical slot.  Rotating identical slots is a no-op, which would
        # later commit physically overlapping NANDs.  Give each member a
        # deterministic dedicated column so the suppression is real geometry
        # rather than a bookkeeping-only placement variant.
        NextColumn = max(
            (Column for Column, _Row in Result.values()),
            default=-1,
        ) + 1
        for Offset, ClusterIndex in enumerate(Pending):
            Result[ClusterIndex] = (NextColumn + Offset, 0)
        return Result, max(ColumnCount, NextColumn + len(Pending))
    NextColumn = max(
        (Column for Column, _Row in Result.values()),
        default=-1,
    ) + 1
    Result[Pending[0]] = (NextColumn, 0)
    return Result, max(ColumnCount, NextColumn + 1)


def BuildInterClusterBoundaryDemand(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Assignment: dict[int, tuple[int, int]],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[InterClusterBoundaryDemand, ...]:
    """Derive topology-only lane demand across final cluster-grid cuts.

    A signal is counted once per crossed X/Z boundary even when it has several
    consumers beyond that cut.  This lets placement reserve one physical lane
    for a shared routed tree instead of scaling spacing with fanout.
    """
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerClusterBySignal = {
        Signal: ClusterByGate[Gate.Name]
        for Gate in Module.Gates
        if Gate.Name in ClusterByGate
        for Signal in Gate.Outputs
    }
    SignalsByBoundary: dict[tuple[str, int], set[str]] = {}
    for GateIndex, Gate in enumerate(Module.Gates):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "inter-cluster-boundary-demand",
                "CompletedGates": GateIndex,
                "TotalGates": len(Module.Gates),
                "GateName": Gate.Name,
            })
        TargetCluster = ClusterByGate.get(Gate.Name)
        if TargetCluster is None:
            continue
        TargetSlot = Assignment.get(TargetCluster)
        if TargetSlot is None:
            continue
        for Signal in Gate.Inputs:
            SourceCluster = ProducerClusterBySignal.get(Signal)
            if SourceCluster is None or SourceCluster == TargetCluster:
                continue
            SourceSlot = Assignment.get(SourceCluster)
            if SourceSlot is None:
                continue
            SourceColumn, SourceRow = SourceSlot
            TargetColumn, TargetRow = TargetSlot
            for BoundaryIndex in range(
                min(SourceColumn, TargetColumn),
                max(SourceColumn, TargetColumn),
            ):
                SignalsByBoundary.setdefault(("X", BoundaryIndex), set()).add(
                    Signal
                )
            for BoundaryIndex in range(
                min(SourceRow, TargetRow),
                max(SourceRow, TargetRow),
            ):
                SignalsByBoundary.setdefault(("Z", BoundaryIndex), set()).add(
                    Signal
                )
    return tuple(
        InterClusterBoundaryDemand(
            Axis=Axis,
            BoundaryIndex=BoundaryIndex,
            Signals=tuple(sorted(Signals)),
        )
        for (Axis, BoundaryIndex), Signals in sorted(SignalsByBoundary.items())
    )


def BuildInterClusterGapPlan(
    BoundaryDemand: tuple[InterClusterBoundaryDemand, ...],
    ColumnCount: int,
    RowCount: int,
    RoutingSpacing: int,
    TrackPitch: int,
    Enabled: bool,
) -> InterClusterGapPlan:
    """Allocate optional spacing without exceeding the configured corridor.

    The fixed placement gap remains outside this plan.  When disabled every
    boundary retains the uniform configured spacing, which exactly preserves
    the previous coordinate construction.
    """
    if RoutingSpacing < 0:
        raise ValueError("RoutingSpacing cannot be negative")
    if TrackPitch < 1:
        raise ValueError("TrackPitch must be positive")
    LanesByBoundary = {
        (Record.Axis, Record.BoundaryIndex): Record.RequiredCorridorLanes
        for Record in BoundaryDemand
    }

    def OptionalSpacing(Axis: str, BoundaryIndex: int) -> int:
        if not Enabled:
            return RoutingSpacing
        return min(
            RoutingSpacing,
            LanesByBoundary.get((Axis, BoundaryIndex), 0) * TrackPitch,
        )

    return InterClusterGapPlan(
        Enabled=Enabled,
        RoutingSpacing=RoutingSpacing,
        TrackPitch=TrackPitch,
        ColumnExtraSpacing=tuple(
            (BoundaryIndex, OptionalSpacing("X", BoundaryIndex))
            for BoundaryIndex in range(max(0, ColumnCount - 1))
        ),
        RowExtraSpacing=tuple(
            (BoundaryIndex, OptionalSpacing("Z", BoundaryIndex))
            for BoundaryIndex in range(max(0, RowCount - 1))
        ),
        BoundaryDemand=BoundaryDemand,
    )


def PlacementWireCost(Placed: PlacedDesign) -> int:
    """Return weighted center-to-center wire length."""
    Producers = {
        Signal: Gate
        for Gate in Placed.PlacedGates
        for Signal in Gate.Outputs
    }
    Fanout: dict[str, int] = {}
    for Gate in Placed.PlacedGates:
        for Signal in Gate.Inputs:
            Fanout[Signal] = Fanout.get(Signal, 0) + 1
    Cost = 0
    for Gate in Placed.PlacedGates:
        TargetWidth, TargetDepth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        TargetCenter = (
            Gate.X + TargetWidth / 2,
            Gate.Z + TargetDepth / 2,
        )
        for Signal in Gate.Inputs:
            Producer = Producers.get(Signal)
            if Producer is None:
                continue
            SourceWidth, SourceDepth = RotatedCellSize(
                Producer.Kind,
                Producer.Rotation,
            )
            SourceCenter = (
                Producer.X + SourceWidth / 2,
                Producer.Z + SourceDepth / 2,
            )
            Cost += max(1, Fanout.get(Signal, 1)) * round(
                abs(SourceCenter[0] - TargetCenter[0])
                + abs(SourceCenter[1] - TargetCenter[1])
            )
    return Cost


def EstimatePlacementRoutingCost(
    Placed: PlacedDesign,
    MaximumLayerCount: int = 4,
) -> tuple[int, int, int]:
    """Sketch cheap multilayer routes and estimate blockage and congestion."""
    Footprints: set[tuple[int, int]] = set()
    for Gate in Placed.PlacedGates:
        Width, Depth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        Footprints.update(
            (X, Z)
            for X in range(Gate.X, Gate.X + Width)
            for Z in range(Gate.Z, Gate.Z + Depth)
        )

    Producers: dict[str, tuple[int, int]] = {}
    Targets: dict[str, list[tuple[int, int]]] = {}
    for Gate in Placed.PlacedGates:
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            PinX, _PinY, PinZ = Gate.OutputPin
            DirectionX, _DirectionY, DirectionZ = Gate.OutputDirection
            Endpoint = (
                PinX + DirectionX * 2,
                PinZ + DirectionZ * 2,
            )
            for Signal in Gate.Outputs:
                Producers[Signal] = Endpoint
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            PinX, _PinY, PinZ = Pin
            DirectionX, _DirectionY, DirectionZ = Direction
            Targets.setdefault(Signal, []).append(
                (
                    PinX + DirectionX * 2,
                    PinZ + DirectionZ * 2,
                )
            )

    Signals = [Signal for Signal in Producers if Targets.get(Signal)]
    if not Signals:
        return (0, 0, 0)
    LayerCount = min(
        MaximumLayerCount,
        max(2, ceil(sqrt(len(Signals)))),
    )
    Occupied = [set() for _Layer in range(LayerCount)]
    ObstaclePressure = 0
    CongestionPressure = 0
    RouteLength = 0
    OrderedSignals = sorted(
        Signals,
        key=lambda Signal: (
            -len(Targets[Signal]),
            -max(
                abs(Producers[Signal][0] - Target[0])
                + abs(Producers[Signal][1] - Target[1])
                for Target in Targets[Signal]
            ),
            Signal,
        ),
    )
    for Signal in OrderedSignals:
        Options = []
        for Layer in range(LayerCount):
            for XFirst in (True, False):
                Guide = BuildSignalGuide(
                    Producers[Signal],
                    Targets[Signal],
                    XFirst,
                )
                ObstacleHits = sum(
                    Position in Footprints
                    for Position in Guide
                )
                Congestion = sum(
                    4 * (Position in Occupied[Layer])
                    + sum(
                        Neighbor in Occupied[Layer]
                        for Neighbor in (
                            (Position[0] + 1, Position[1]),
                            (Position[0] - 1, Position[1]),
                            (Position[0], Position[1] + 1),
                            (Position[0], Position[1] - 1),
                        )
                    )
                    for Position in Guide
                )
                VerticalLength = Layer * 2 * (1 + len(Targets[Signal]))
                Options.append(
                    (
                        ObstacleHits * (LayerCount - Layer),
                        Congestion,
                        len(Guide) + VerticalLength,
                        Layer,
                        Guide,
                    )
                )
        ObstacleCost, Congestion, Length, Layer, Guide = min(Options)
        ObstaclePressure += ObstacleCost
        CongestionPressure += Congestion
        RouteLength += Length
        Occupied[Layer].update(Guide)
    return ObstaclePressure, CongestionPressure, RouteLength


def PlacementCompactKey(
    Placed: PlacedDesign,
) -> tuple[int, int, int, int, int, int]:
    """Score legal placement by routability before occupied bounds."""
    if not Placed.PlacedGates:
        return (0, 0, 0, 0, 0, 0)
    MinimumX = min(Gate.X for Gate in Placed.PlacedGates)
    MinimumZ = min(Gate.Z for Gate in Placed.PlacedGates)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
        for Gate in Placed.PlacedGates
    )
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
        for Gate in Placed.PlacedGates
    )
    Width = MaximumX - MinimumX
    Depth = MaximumZ - MinimumZ
    WireCost = PlacementWireCost(Placed)
    ObstaclePressure, CongestionPressure, RouteLength = (
        EstimatePlacementRoutingCost(Placed)
    )
    Footprint = Width * Depth
    return (
        ObstaclePressure,
        CongestionPressure * 4 + RouteLength,
        Footprint,
        max(Width, Depth),
        Width + Depth,
        WireCost,
    )


@lru_cache(maxsize=4096)
def _PhysicalGateGeometry(
    Kind: str,
    X: int,
    Y: int,
    Z: int,
    Rotation: int,
    MirrorX: bool,
) -> tuple[frozenset[tuple[int, int, int]], frozenset[tuple[int, int, int]]]:
    """Cache exact template occupancy used by packed-cell legalization."""
    Gate = type(
        "CachedPlacedGate",
        (),
        {
            "Name": "CachedCell",
            "Kind": Kind,
            "X": X,
            "Y": Y,
            "Z": Z,
            "Rotation": Rotation,
            "MirrorX": MirrorX,
        },
    )()
    Actual, Electrical, _Solid = BuildPlacedCellGeometry(
        type("CachedPlacement", (), {"PlacedGates": [Gate]})()
    )
    return frozenset(Actual), frozenset(Electrical)


def PcbGatesConflict(First: Any, Second: Any) -> bool:
    """Reject footprint, pin-access, and template electrical conflicts."""

    def AccessSignals(Gate: Any) -> list[tuple[tuple[int, int, int], str]]:
        Values = []
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            X, Y, Z = Gate.OutputPin
            DeltaX, DeltaY, DeltaZ = Gate.OutputDirection
            for Signal in Gate.Outputs:
                Values.extend(
                    (((X + DeltaX * Offset, Y + DeltaY * Offset, Z + DeltaZ * Offset), Signal)
                     for Offset in range(3))
                )
        for Signal, Pin, Direction in zip(Gate.Inputs, Gate.InputPins, Gate.InputDirections):
            X, Y, Z = Pin
            DeltaX, DeltaY, DeltaZ = Direction
            Values.extend(
                (((X + DeltaX * Offset, Y + DeltaY * Offset, Z + DeltaZ * Offset), Signal)
                 for Offset in range(3))
            )
        return Values

    if RectanglesOverlap(First, Second):
        return True
    FirstWidth, FirstDepth = RotatedCellSize(First.Kind, First.Rotation)
    SecondWidth, SecondDepth = RotatedCellSize(Second.Kind, Second.Rotation)
    BroadPhaseMargin = 3
    if (
        First.X + FirstWidth - 1 + BroadPhaseMargin
        < Second.X - BroadPhaseMargin
        or Second.X + SecondWidth - 1 + BroadPhaseMargin
        < First.X - BroadPhaseMargin
        or First.Z + FirstDepth - 1 + BroadPhaseMargin
        < Second.Z - BroadPhaseMargin
        or Second.Z + SecondDepth - 1 + BroadPhaseMargin
        < First.Z - BroadPhaseMargin
    ):
        return False
    FirstActual, FirstElectrical = _PhysicalGateGeometry(
        First.Kind,
        First.X,
        First.Y,
        First.Z,
        First.Rotation,
        First.MirrorX,
    )
    SecondActual, SecondElectrical = _PhysicalGateGeometry(
        Second.Kind,
        Second.X,
        Second.Y,
        Second.Z,
        Second.Rotation,
        Second.MirrorX,
    )
    if (
        DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(
            set(FirstElectrical)
        )
        & set(SecondActual)
    ) or (
        DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(
            set(SecondElectrical)
        )
        & set(FirstActual)
    ):
        return True
    if abs(First.Y - Second.Y) >= 3:
        return False
    FirstWidth, FirstDepth = RotatedCellSize(First.Kind, First.Rotation)
    SecondWidth, SecondDepth = RotatedCellSize(Second.Kind, Second.Rotation)
    FirstAccess = AccessSignals(First)
    SecondAccess = AccessSignals(Second)
    FirstSignalsByPosition = {
        Position: {Signal for Value, Signal in FirstAccess if Value == Position}
        for Position, _Signal in FirstAccess
    }
    SecondSignalsByPosition = {
        Position: {Signal for Value, Signal in SecondAccess if Value == Position}
        for Position, _Signal in SecondAccess
    }
    if any(
        Second.X <= Position[0] < Second.X + SecondWidth
        and Second.Z <= Position[2] < Second.Z + SecondDepth
        and not (
            FirstSignalsByPosition[Position]
            & SecondSignalsByPosition.get(Position, set())
        )
        for Position in FirstSignalsByPosition
    ) or any(
        First.X <= Position[0] < First.X + FirstWidth
        and First.Z <= Position[2] < First.Z + FirstDepth
        and not (
            SecondSignalsByPosition[Position]
            & FirstSignalsByPosition.get(Position, set())
        )
        for Position in SecondSignalsByPosition
    ):
        return True

    for FirstPosition, FirstSignal in AccessSignals(First):
        for SecondPosition, SecondSignal in AccessSignals(Second):
            if FirstSignal == SecondSignal:
                continue
            DeltaX = abs(FirstPosition[0] - SecondPosition[0])
            DeltaY = abs(FirstPosition[1] - SecondPosition[1])
            DeltaZ = abs(FirstPosition[2] - SecondPosition[2])
            HorizontalDistance = DeltaX + DeltaZ
            if (
                (DeltaY == 0 and HorizontalDistance <= 1)
                or (DeltaY == 1 and HorizontalDistance == 1)
            ):
                return True
    return False


def BuildMandatoryAccessClaims(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
) -> dict[str, RoutingResourceClaims]:
    """Build the fixed pin-access claims that every detailed route must own."""
    RequiredSignals = frozenset(Signals)
    NodesBySignal: dict[str, set[tuple[int, int, int]]] = {
        Signal: set() for Signal in RequiredSignals
    }
    for Gate in PlacedGates:
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            for Signal in Gate.Outputs:
                if Signal not in RequiredSignals:
                    continue
                NodesBySignal[Signal].update(
                    (
                        Gate.OutputPin[0] + Gate.OutputDirection[0] * Offset,
                        Gate.OutputPin[1] + Gate.OutputDirection[1] * Offset,
                        Gate.OutputPin[2] + Gate.OutputDirection[2] * Offset,
                    )
                    for Offset in range(
                        DefaultRedstoneRoutingTechnology.AccessLength
                    )
                )
        for InputIndex, Signal in enumerate(Gate.Inputs):
            if Signal not in RequiredSignals:
                continue
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            NodesBySignal[Signal].update(
                (
                    Pin[0] + Direction[0] * Offset,
                    Pin[1] + Direction[1] * Offset,
                    Pin[2] + Direction[2] * Offset,
                )
                for Offset in range(
                    DefaultRedstoneRoutingTechnology.AccessLength
                )
            )
    ClaimBuilder = RoutingResourceGraph(
        ActualBlocks=frozenset(),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
    )
    return {
        Signal: ClaimBuilder.BuildRouteClaims(Nodes)
        for Signal, Nodes in sorted(NodesBySignal.items())
        if Nodes
    }


def CountMandatoryAccessSelfConflicts(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
) -> int:
    """Count exact support/headroom aliases within mandatory pin accesses."""
    return len(FindSelfClaimConflicts(
        BuildMandatoryAccessClaims(PlacedGates, Signals)
    ))


def CountMandatoryAccessConflicts(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
) -> int:
    """Count all fixed pin-access conflicts for the affected signal cut.

    A packed cluster can be individually legal for every signal while two
    different signals still claim the same electrical, support, or headroom
    resource.  Those conflicts are immutable to detailed routing and must be
    removed by the local placement repair before a candidate is published.
    """
    Claims = BuildMandatoryAccessClaims(PlacedGates, Signals)
    return len(FindSelfClaimConflicts(Claims)) + len(FindClaimConflicts(Claims))


def FindMandatoryAccessConflictSignals(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
) -> dict[object, tuple[str, ...]]:
    """Return immutable cross-signal access conflicts before routing begins.

    This is deliberately the same resource-claim model used by detailed
    routing.  It lets placement advance directly to a relocation recipe when
    no router can resolve a fixed pin-access collision.
    """
    Claims = BuildMandatoryAccessClaims(PlacedGates, Signals)
    return {
        Resource: tuple(sorted(Owners))
        for Resource, Owners in sorted(
            FindClaimConflicts(Claims).items(), key=lambda Value: str(Value[0])
        )
    }


def RepairPackedClusterAccess(
    Names: tuple[str, ...] | list[str],
    InternalByName: dict[str, Any],
    LocalPositions: dict[str, tuple[int, int]],
    LocalRotations: dict[str, int],
    LocalMirrors: dict[str, bool],
    RequiredSignals: frozenset[str],
    BeamWidth: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[dict[str, tuple[int, int]], dict[str, bool], dict[str, int]]:
    """Repair fixed access claims inside one cluster without global spreading."""
    ClusterSignals = frozenset(
        Signal
        for Name in Names
        for Signal in (
            *InternalByName[Name].Inputs,
            *InternalByName[Name].Outputs,
        )
        if Signal in RequiredSignals
    )
    if not ClusterSignals:
        return LocalPositions, LocalMirrors, {}

    def BuildGates(
        State: tuple[tuple[str, int, bool], ...],
    ) -> list[Any]:
        Values = {Name: (X, MirrorX) for Name, X, MirrorX in State}
        return [
            BuildPlacedGate(
                InternalByName[Name],
                Values[Name][0],
                1,
                LocalPositions[Name][1],
                LocalRotations[Name],
                Values[Name][1],
            )
            for Name in Names
        ]

    BaselineState = tuple(
        (Name, LocalPositions[Name][0], LocalMirrors.get(Name, False))
        for Name in Names
    )
    BaselineGates = BuildGates(BaselineState)
    BaselineConflicts = CountMandatoryAccessConflicts(
        BaselineGates, ClusterSignals
    )
    if BaselineConflicts == 0:
        return LocalPositions, LocalMirrors, {}

    BaselineMinimumX = min(Gate.X for Gate in BaselineGates)
    BaselineMaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
        for Gate in BaselineGates
    )
    BaselineWidth = BaselineMaximumX - BaselineMinimumX
    MaximumWidth = max(BaselineWidth, 2 * BaselineWidth)
    SearchMinimumX = BaselineMinimumX - BaselineWidth
    SearchMaximumX = BaselineMaximumX + BaselineWidth
    BaselineX = {
        Name: LocalPositions[Name][0] for Name in Names
    }
    EndpointNames = {
        Name
        for Name in Names
        if set((
            *InternalByName[Name].Inputs,
            *InternalByName[Name].Outputs,
        )) & ClusterSignals
    }
    SearchOrder = tuple(sorted(
        Names,
        key=lambda Name: (Name not in EndpointNames, LocalPositions[Name][1], Name),
    ))

    def Score(
        State: tuple[tuple[str, int, bool], ...],
        Gates: list[Any] | None = None,
    ) -> tuple[int, int, int, tuple[tuple[str, int, bool], ...]]:
        Gates = BuildGates(State) if Gates is None else Gates
        MinimumX = min(Gate.X for Gate in Gates)
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in Gates
        )
        Displacement = sum(
            abs(Gate.X - BaselineX[Gate.Name]) for Gate in Gates
        )
        return (
            CountMandatoryAccessConflicts(Gates, ClusterSignals),
            MaximumX - MinimumX,
            Displacement,
            State,
        )

    Beam: list[tuple[tuple[int, int, int, tuple[Any, ...]], tuple[tuple[str, int, bool], ...]]] = [
        (Score(BaselineState, BaselineGates), BaselineState)
    ]
    for PassIndex in range(2):
        for GateIndex, Name in enumerate(SearchOrder):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "packed-access-repair",
                    "Pass": PassIndex,
                    "GateIndex": GateIndex,
                    "GateCount": len(SearchOrder),
                    "BaselineConflictCount": BaselineConflicts,
                })
            Candidates: dict[
                tuple[tuple[str, int, bool], ...],
                tuple[int, int, int, tuple[Any, ...]],
            ] = {}
            for _PreviousScore, PreviousState in Beam:
                Previous = {
                    GateName: (X, MirrorX)
                    for GateName, X, MirrorX in PreviousState
                }
                GateWidth = RotatedCellSize(
                    InternalByName[Name].Kind.value,
                    LocalRotations[Name],
                )[0]
                for CandidateX in range(
                    SearchMinimumX,
                    SearchMaximumX - GateWidth + 1,
                ):
                    for CandidateMirror in (False, True):
                        Candidate = dict(Previous)
                        Candidate[Name] = (CandidateX, CandidateMirror)
                        State = tuple(
                            (GateName, Candidate[GateName][0], Candidate[GateName][1])
                            for GateName in Names
                        )
                        if State in Candidates:
                            continue
                        Gates = BuildGates(State)
                        if any(
                            PcbGatesConflict(First, Second)
                            for FirstIndex, First in enumerate(Gates)
                            for Second in Gates[FirstIndex + 1 :]
                        ):
                            continue
                        MinimumX = min(Gate.X for Gate in Gates)
                        MaximumX = max(
                            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
                            for Gate in Gates
                        )
                        if MaximumX - MinimumX > MaximumWidth:
                            continue
                        Candidates[State] = Score(State, Gates)
            Beam = [
                (CandidateScore, State)
                for State, CandidateScore in sorted(
                    Candidates.items(), key=lambda Value: Value[1]
                )[:BeamWidth]
            ]
            if not Beam:
                break
        if Beam and Beam[0][0][0] == 0:
            break
    if not Beam or Beam[0][0][0] != 0:
        BestConflictCount = Beam[0][0][0] if Beam else BaselineConflicts
        raise ValueError(
            "Could not legalize mandatory packed access claims "
            f"within width envelope: signals={','.join(sorted(ClusterSignals))}:"
            f"baseline={BaselineConflicts}:best={BestConflictCount}:"
            f"maximum_width={MaximumWidth}"
        )
    BestScore, BestState = Beam[0]
    Best = {Name: (X, MirrorX) for Name, X, MirrorX in BestState}
    MinimumX = min(X for X, _MirrorX in Best.values())
    RepairedPositions = dict(LocalPositions)
    RepairedMirrors = dict(LocalMirrors)
    for Name in Names:
        RepairedPositions[Name] = (
            Best[Name][0] - MinimumX,
            LocalPositions[Name][1],
        )
        RepairedMirrors[Name] = Best[Name][1]
    return RepairedPositions, RepairedMirrors, {
        "BaselineConflictCount": BaselineConflicts,
        "FinalConflictCount": BestScore[0],
        "BaselineWidth": BaselineWidth,
        "FinalWidth": BestScore[1],
        "MaximumWidth": MaximumWidth,
    }


def BuildPinAlignedPackedCluster(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[
    dict[str, tuple[int, int]],
    dict[str, int],
    dict[str, bool],
] | None:
    """Embed a NAND graph around connected pins without circuit recognition."""
    NameSet = set(Names)
    ProducerBySignal = {
        Signal: Gate.Name
        for Gate in InternalByName.values()
        for Signal in Gate.Outputs
    }
    ConsumersBySignal: dict[str, list[tuple[str, int]]] = {}
    for Gate in InternalByName.values():
        for InputIndex, Signal in enumerate(Gate.Inputs):
            ConsumersBySignal.setdefault(Signal, []).append((Gate.Name, InputIndex))
    Adjacency = {Name: set() for Name in Names}
    for Signal, Producer in ProducerBySignal.items():
        if Producer not in NameSet:
            continue
        for Consumer, _InputIndex in ConsumersBySignal.get(Signal, ()):
            if Consumer in NameSet and Consumer != Producer:
                Adjacency[Producer].add(Consumer)
                Adjacency[Consumer].add(Producer)
    Start = min(Names, key=lambda Name: (-len(Adjacency[Name]), Name))
    StartGate = BuildPlacedGate(InternalByName[Start], 0, 1, 0, 0, False)
    Beam: list[dict[str, Any]] = [{Start: StartGate}]
    PlacedNames = {Start}

    def ChooseNext() -> str:
        return min(
            NameSet - PlacedNames,
            key=lambda Name: (
                -len(Adjacency[Name] & PlacedNames),
                -len(Adjacency[Name]),
                Name,
            ),
        )

    def Score(State: dict[str, Any]) -> tuple[Any, ...]:
        Endpoints: dict[str, list[tuple[int, int]]] = {}
        PinOwners: list[tuple[tuple[int, int, int], str]] = []
        for Gate in State.values():
            if Gate.OutputPin is not None:
                for Signal in Gate.Outputs:
                    Endpoints.setdefault(Signal, []).append(
                        (Gate.OutputPin[0], Gate.OutputPin[2])
                    )
                    PinOwners.append((Gate.OutputPin, Signal))
            for InputIndex, Signal in enumerate(Gate.Inputs):
                Pin = Gate.InputPins[InputIndex]
                Endpoints.setdefault(Signal, []).append((Pin[0], Pin[2]))
                PinOwners.append((Pin, Signal))
        CrossElectricalPenalty = sum(
            1
            for Index, (FirstPin, FirstSignal) in enumerate(PinOwners)
            for SecondPin, SecondSignal in PinOwners[Index + 1 :]
            if FirstSignal != SecondSignal
            and abs(FirstPin[1] - SecondPin[1]) <= 1
            and (
                abs(FirstPin[0] - SecondPin[0])
                + abs(FirstPin[2] - SecondPin[2])
                <= 1
            )
        )
        Hpwl = sum(
            max(X for X, _Z in Values)
            - min(X for X, _Z in Values)
            + max(Z for _X, Z in Values)
            - min(Z for _X, Z in Values)
            for Values in Endpoints.values()
            if len(Values) > 1
        )
        MinimumX = min(Gate.X for Gate in State.values())
        MinimumZ = min(Gate.Z for Gate in State.values())
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in State.values()
        )
        MaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in State.values()
        )
        Width = MaximumX - MinimumX
        Depth = MaximumZ - MinimumZ
        Stable = tuple(
            (Name, State[Name].X, State[Name].Z, State[Name].Rotation, State[Name].MirrorX)
            for Name in sorted(State)
        )
        return (
            CrossElectricalPenalty,
            Hpwl,
            Width * Depth,
            max(Width, Depth),
            Stable,
        )

    while PlacedNames != NameSet:
        Name = ChooseNext()
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "graph-beam-gate",
                "GateName": Name,
                "CompletedGates": len(PlacedNames),
                "TotalGates": len(NameSet),
                "BeamStates": len(Beam),
            })
        NextBeam = []
        for StateIndex, State in enumerate(Beam):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "graph-beam-state",
                    "GateName": Name,
                    "CompletedStates": StateIndex,
                    "TotalStates": len(Beam),
                })
            Connections = []
            GateValue = InternalByName[Name]
            for InputIndex, Signal in enumerate(GateValue.Inputs):
                ProducerName = ProducerBySignal.get(Signal)
                if ProducerName in State:
                    Connections.append(
                        (State[ProducerName].OutputPin, "Input", InputIndex)
                    )
            for Signal in GateValue.Outputs:
                for ConsumerName, InputIndex in ConsumersBySignal.get(Signal, ()):
                    if ConsumerName in State:
                        Connections.append(
                            (State[ConsumerName].InputPins[InputIndex], "Output", 0)
                        )
            CandidateKeys = set()
            for ExistingPin, PinKind, PinIndex in Connections:
                for Rotation in (0, 90, 180, 270):
                    for MirrorX in (False, True):
                        Origin = BuildPlacedGate(
                            GateValue, 0, 1, 0, Rotation, MirrorX
                        )
                        LocalPin = (
                            Origin.InputPins[PinIndex]
                            if PinKind == "Input"
                            else Origin.OutputPin
                        )
                        for DeltaX, DeltaZ in (
                            (0, 0),
                            (1, 0),
                            (-1, 0),
                            (0, 1),
                            (0, -1),
                            (2, 0),
                            (-2, 0),
                            (0, 2),
                            (0, -2),
                            (1, 1),
                            (1, -1),
                            (-1, 1),
                            (-1, -1),
                        ):
                            CandidateKeys.add(
                                (
                                    ExistingPin[0] + DeltaX - LocalPin[0],
                                    ExistingPin[2] + DeltaZ - LocalPin[2],
                                    Rotation,
                                    MirrorX,
                                )
                            )
            OrderedCandidateKeys = sorted(CandidateKeys)
            for CandidateIndex, (X, Z, Rotation, MirrorX) in enumerate(
                OrderedCandidateKeys
            ):
                if WorkCheck is not None and CandidateIndex % 32 == 0:
                    WorkCheck({
                        "Phase": "graph-beam-candidate",
                        "GateName": Name,
                        "CompletedCandidates": CandidateIndex,
                        "TotalCandidates": len(OrderedCandidateKeys),
                    })
                Candidate = BuildPlacedGate(
                    GateValue, X, 1, Z, Rotation, MirrorX
                )
                if any(
                    PcbGatesConflict(Candidate, Existing)
                    for Existing in State.values()
                ):
                    continue
                CandidateState = dict(State)
                CandidateState[Name] = Candidate
                NextBeam.append((Score(CandidateState), CandidateState))
        if not NextBeam:
            return None
        NextBeam.sort(key=lambda Value: Value[0])
        Beam = [State for _Key, State in NextBeam[:BeamWidth]]
        PlacedNames.add(Name)

    Best = min(Beam, key=Score)
    MinimumX = min(Gate.X for Gate in Best.values())
    MinimumZ = min(Gate.Z for Gate in Best.values())
    return (
        {
            Name: (Gate.X - MinimumX, Gate.Z - MinimumZ)
            for Name, Gate in Best.items()
        },
        {Name: Gate.Rotation for Name, Gate in Best.items()},
        {Name: Gate.MirrorX for Name, Gate in Best.items()},
    )


def CompactWeightedPlacement(
    Module: Any,
    Placed: PlacedDesign,
    MaximumPasses: int = 12,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacedDesign:
    """Pull every template toward its nets and compact bounds legally."""
    SourceByName = {Gate.Name: Gate for Gate in Module.Gates}
    Current = Placed
    for _Pass in range(MaximumPasses):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "placement-compaction-pass",
                "PassIndex": _Pass,
                "MaximumPasses": MaximumPasses,
            })
        Improved = False
        Producers = {
            Signal: Gate
            for Gate in Current.PlacedGates
            for Signal in Gate.Outputs
        }
        Consumers: dict[str, list[Any]] = {}
        for Gate in Current.PlacedGates:
            for Signal in Gate.Inputs:
                Consumers.setdefault(Signal, []).append(Gate)
        CurrentGates = list(Current.PlacedGates)
        for GateIndex, Gate in enumerate(CurrentGates):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "placement-compaction-gate",
                    "PassIndex": _Pass,
                    "CompletedGates": GateIndex,
                    "TotalGates": len(CurrentGates),
                })
            if Gate.Kind != "NAND":
                continue
            Connected = []
            for Signal in Gate.Inputs:
                Producer = Producers.get(Signal)
                if Producer is not None:
                    Connected.append((Producer.X, Producer.Z))
            for Signal in Gate.Outputs:
                Connected.extend(
                    (Consumer.X, Consumer.Z)
                    for Consumer in Consumers.get(Signal, ())
                )
            if not Connected:
                continue
            TargetX = median(Value[0] for Value in Connected)
            TargetZ = median(Value[1] for Value in Connected)
            Directions = []
            if TargetX != Gate.X:
                Directions.append((1 if TargetX > Gate.X else -1, 0))
            if TargetZ != Gate.Z:
                Directions.append((0, 1 if TargetZ > Gate.Z else -1))
            for Direction in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if Direction not in Directions:
                    Directions.append(Direction)
            CurrentKey = PlacementCompactKey(Current)
            BestCandidate = None
            BestKey = CurrentKey
            for DirectionIndex, (DeltaX, DeltaZ) in enumerate(Directions):
                if WorkCheck is not None:
                    WorkCheck({
                        "Phase": "placement-compaction-candidate",
                        "PassIndex": _Pass,
                        "GateName": Gate.Name,
                        "CompletedDirections": DirectionIndex,
                        "TotalDirections": len(Directions),
                    })
                ShiftedGates = [
                    BuildPlacedGate(
                        SourceByName[Other.Name],
                        Other.X + (DeltaX if Other.Name == Gate.Name else 0),
                        Other.Y,
                        Other.Z + (DeltaZ if Other.Name == Gate.Name else 0),
                        Other.Rotation,
                        Other.MirrorX,
                    )
                    for Other in Current.PlacedGates
                ]
                Candidate = PlacedDesign(Module=Module, PlacedGates=ShiftedGates)
                if any(
                    PcbGatesConflict(First, Second)
                    for Index, First in enumerate(ShiftedGates)
                    for Second in ShiftedGates[Index + 1 :]
                ):
                    continue
                CandidateKey = PlacementCompactKey(Candidate)
                if CandidateKey >= BestKey:
                    continue
                BestCandidate = Candidate
                BestKey = CandidateKey
            if BestCandidate is not None:
                Current = BestCandidate
                Improved = True
        if not Improved:
            break
    return Current


def AddGuideLine(
    Values: set[tuple[int, int]],
    Start: tuple[int, int],
    End: tuple[int, int],
) -> None:
    """Rasterize one orthogonal guide segment."""
    if Start[0] == End[0]:
        for Z in range(min(Start[1], End[1]), max(Start[1], End[1]) + 1):
            Values.add((Start[0], Z))
        return
    for X in range(min(Start[0], End[0]), max(Start[0], End[0]) + 1):
        Values.add((X, Start[1]))


def BuildSignalGuide(
    Source: tuple[int, int],
    Targets: list[tuple[int, int]],
    XFirst: bool,
) -> frozenset[tuple[int, int]]:
    """Build a rectilinear fanout tree biased to one preferred direction."""
    Guide = {Source}
    Remaining = list(Targets)
    while Remaining:
        Target = min(
            Remaining,
            key=lambda Value: min(
                abs(Value[0] - Existing[0]) + abs(Value[1] - Existing[1])
                for Existing in Guide
            ),
        )
        Anchor = min(
            Guide,
            key=lambda Value: (
                abs(Target[0] - Value[0]) + abs(Target[1] - Value[1]),
                Value,
            ),
        )
        Corner = (
            (Target[0], Anchor[1])
            if XFirst
            else (Anchor[0], Target[1])
        )
        AddGuideLine(Guide, Anchor, Corner)
        AddGuideLine(Guide, Corner, Target)
        Remaining.remove(Target)
    return frozenset(Guide)


def AddPcbRoutingGuides(
    Placed: PlacedDesign,
    MaximumLayerCount: int = 0,
) -> PcbPlacement:
    """Attach deterministic routing metadata without performing route planning."""
    Signals = {
        Signal
        for Gate in Placed.PlacedGates
        if Gate.OutputPin is not None
        for Signal in Gate.Outputs
    } & {
        Signal
        for Gate in Placed.PlacedGates
        for Signal in Gate.Inputs
    }
    LayerCount = min(
        (
            MaximumLayerCount
            if MaximumLayerCount > 0
            else DefaultRedstoneRoutingTechnology.MaximumRoutableLayerCount
        ),
        max(
            DefaultRedstoneRoutingTechnology.MinimumRoutingLayerCount,
            ceil(sqrt(max(1, len(Signals)))),
        ),
    )
    Guided = PlacedDesign(
        Module=Placed.Module,
        PlacedGates=Placed.PlacedGates,
        RouteGuides={},
        RouteLayers={},
        FrozenNetWires=Placed.FrozenNetWires,
        LocalNetBranches=Placed.LocalNetBranches,
        LocalNetTargets=Placed.LocalNetTargets,
        LocalRouteClaims=Placed.LocalRouteClaims,
        LocalRouteDiagnostics=Placed.LocalRouteDiagnostics,
    )
    return PcbPlacement(
        Placed=Guided,
        Clusters=(),
        SignalOrder=tuple(sorted(Signals)),
        LayerCount=LayerCount,
    )


def PlacePcbGraph(
    Netlist: Any,
    RoutingSpacing: int = 0,
    PlacementPolicy: PlacementPolicy | None = None,
    PackingPolicy: NandPackingPolicy | None = None,
    ClusterPolicy: ClusteringPolicy | None = None,
    MaximumBoundaryTerminals: int | None = None,
    MaximumEntrancesPerSignal: int | None = None,
    RelocationSignals: frozenset[str] = frozenset(),
    RelocationPrioritySignals: frozenset[str] = frozenset(),
    RequiredRelocationSignals: frozenset[str] = frozenset(),
    RelocationVariant: int = 0,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PcbPlacement:
    """Cluster, optimize, legalize, and guide a generic NAND graph."""
    def CheckWork(Phase: str, **Diagnostics: object) -> None:
        if WorkCheck is not None:
            WorkCheck({"Phase": Phase, **Diagnostics})

    CheckWork("start")
    if RoutingSpacing < 0:
        raise ValueError("RoutingSpacing cannot be negative")
    Module = Netlist.Modules[Netlist.Top]
    Levels = BuildTopologicalLevels(Module, WorkCheck=WorkCheck)
    PackedMode = bool(PackingPolicy is not None and PackingPolicy.Enabled)
    NandCount = sum(Gate.Kind.value == "NAND" for Gate in Module.Gates)
    AdaptiveClusterSize = (
        min(
            PackingPolicy.MaximumClusterCells,
            max(
                ClusterPolicy.MinimumCohesiveCells,
                ceil(ClusterPolicy.CohesiveCellScale * sqrt(max(1, NandCount))),
            ),
        )
        if PackedMode and ClusterPolicy is not None
        else (
            PackingPolicy.MaximumClusterCells
            if PackedMode
            else 32
        )
    )
    if (
        PackedMode
        and NandCount > 3 * PackingPolicy.MaximumClusterCells
    ):
        AdaptiveClusterSize = min(
            AdaptiveClusterSize,
            max(
                4,
                PackingPolicy.MaximumClusterCells
                - PackingPolicy.MaximumClusterCells // 8,
            ),
        )
    Clusters = BuildConnectivityClusters(
        Module,
        MaximumClusterSize=AdaptiveClusterSize,
        Policy=ClusterPolicy if PackedMode else None,
        MaximumBoundaryTerminals=(
            MaximumBoundaryTerminals if PackedMode else None
        ),
        WorkCheck=WorkCheck,
    )
    RelocationClusters = BuildRelocationClusterSet(
        Module,
        Clusters,
        RelocationSignals,
    )
    RequiredRelocationClusters = BuildRelocationClusterSet(
        Module,
        Clusters,
        RequiredRelocationSignals,
    )
    BoundaryEscapeRelocationClusters = BuildRelocationClusterSet(
        Module,
        Clusters,
        RelocationPrioritySignals or RelocationSignals,
    )
    RankedRequiredGeometryClusters = PrioritizeRelocationClusters(
        Module,
        Clusters,
        RequiredRelocationSignals,
    )
    LocalGeometryRepairClusters = frozenset(
        (
            RankedRequiredGeometryClusters[
                RelocationVariant % len(RankedRequiredGeometryClusters)
            ],
        )
        if (
            PackedMode
            and PackingPolicy.EnableLocalGeometryRepair
            and RankedRequiredGeometryClusters
        )
        else ()
    )
    CheckWork("connectivity-clusters", ClusterCount=len(Clusters))
    Assignment, ColumnCount, _RowCount = OptimizeClusterSlots(
        Module,
        Clusters,
        Levels,
        WorkCheck=WorkCheck,
    )
    CheckWork("cluster-slots", ClusterCount=len(Clusters))
    InternalByName = {
        Gate.Name: Gate
        for Gate in Module.Gates
        if Gate.Kind.value == "NAND"
    }
    PackedRotation = 0
    DefaultRotation = PackedRotation if PackedMode else 270
    NandWidth, NandDepth = RotatedCellSize("NAND", DefaultRotation)
    CellPitchX = (
        NandWidth + 2
        if PackedMode
        else NandWidth + 3 + RoutingSpacing
    )
    CellPitchZ = (
        NandDepth + 1
        if PackedMode
        else NandDepth + 2 + RoutingSpacing
    )
    LocalPositions: dict[str, tuple[int, int]] = {}
    LocalRotations: dict[str, int] = {}
    LocalMirrors: dict[str, bool] = {}
    ClusterSizes: dict[int, tuple[int, int]] = {}
    ClusterStructuralSignatures: dict[int, str] = {}
    ClusterReuseSources: dict[int, int | None] = {}
    ClusterStructuralMappings: dict[int, dict[str, str]] = {}
    ClusterStackIds: dict[int, int | None] = {}
    ClusterStackLevels: dict[int, int] = {}
    PackedAccessRepairByCluster: dict[int, dict[str, int]] = {}
    StackSuppressedRelocationClusters: set[int] = set()
    PhysicallyRelocatedClusters: frozenset[int] = frozenset()
    SignalProducerNames = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    for ClusterIndex, Names in enumerate(Clusters):
        CheckWork(
            "cluster-placement",
            CompletedClusters=ClusterIndex,
            TotalClusters=len(Clusters),
        )
        ClusterNames = set(Names)
        ReuseAccepted = False
        if PackedMode:
            StructuralSignature = AnalyzeNandClusterStructure(
                Module,
                Names,
                WorkCheck=WorkCheck,
            )[0]
            ClusterStructuralSignatures[ClusterIndex] = StructuralSignature
            ClusterReuseSources[ClusterIndex] = None
            if PackingPolicy.EnableStructuralReuse:
                for ReferenceIndex in range(ClusterIndex):
                    if (
                        ClusterStructuralSignatures.get(ReferenceIndex)
                        != StructuralSignature
                    ):
                        continue
                    Match = FindIsomorphicNandClusterMapping(
                        Module,
                        Clusters[ReferenceIndex],
                        Names,
                        PackingPolicy.MaximumStructuralReuseMappings,
                        WorkCheck=WorkCheck,
                    )
                    if Match is None:
                        continue
                    _Signature, Mapping = Match
                    CandidatePositions = {
                        CandidateName: LocalPositions[ReferenceName]
                        for ReferenceName, CandidateName in Mapping.items()
                    }
                    CandidateRotations = {
                        CandidateName: LocalRotations[ReferenceName]
                        for ReferenceName, CandidateName in Mapping.items()
                    }
                    CandidateMirrors = {
                        CandidateName: LocalMirrors.get(
                            ReferenceName, False
                        )
                        for ReferenceName, CandidateName in Mapping.items()
                    }
                    CandidateGates = [
                        BuildPlacedGate(
                            InternalByName[Name],
                            CandidatePositions[Name][0],
                            1,
                            CandidatePositions[Name][1],
                            CandidateRotations[Name],
                            CandidateMirrors[Name],
                        )
                        for Name in Names
                    ]
                    CandidatePlaced = PlacedDesign(
                        Module=Module,
                        PlacedGates=CandidateGates,
                    )
                    try:
                        if any(
                            PcbGatesConflict(First, Second)
                            for Index, First in enumerate(CandidateGates)
                            for Second in CandidateGates[Index + 1 :]
                        ):
                            raise ValueError("reused NAND placement conflicts")
                        BuildPlacedCellGeometry(CandidatePlaced)
                    except ValueError:
                        continue
                    LocalPositions.update(CandidatePositions)
                    LocalRotations.update(CandidateRotations)
                    LocalMirrors.update(CandidateMirrors)
                    ClusterReuseSources[ClusterIndex] = ReferenceIndex
                    ClusterStructuralMappings[ClusterIndex] = Mapping
                    ReuseAccepted = True
                    break
        LocalLevels: dict[str, int] = {}
        Remaining = set(Names)
        while Remaining:
            CheckWork(
                "cluster-ordering",
                ClusterIndex=ClusterIndex,
                RemainingGates=len(Remaining),
            )
            Progress = False
            for Name in sorted(Remaining):
                Gate = InternalByName[Name]
                Dependencies = {
                    ProducerName
                    for Signal in Gate.Inputs
                    if (ProducerName := SignalProducerNames.get(Signal))
                    in ClusterNames
                }
                if not Dependencies.issubset(LocalLevels):
                    continue
                LocalLevels[Name] = 1 + max(
                    (LocalLevels[Dependency] for Dependency in Dependencies),
                    default=-1,
                )
                Remaining.remove(Name)
                Progress = True
            if Progress:
                continue
            for Name in sorted(Remaining):
                LocalLevels[Name] = 0
            break

        OrderedNames = sorted(
            Names,
            key=lambda Name: (LocalLevels[Name], Name),
        )
        if PackedMode and ReuseAccepted:
            FoldColumns = 1
            FoldRows = 1
            PackedWidth = max(
                LocalPositions[Name][0]
                + RotatedCellSize("NAND", LocalRotations[Name])[0]
                for Name in Names
            )
            PackedDepth = max(
                LocalPositions[Name][1]
                + RotatedCellSize("NAND", LocalRotations[Name])[1]
                for Name in Names
            )
        elif PackedMode:
            NamesByLevel: dict[int, list[str]] = {}
            for Name in OrderedNames:
                NamesByLevel.setdefault(LocalLevels[Name], []).append(Name)
            FoldRows = max(NamesByLevel) + 1
            PackedXByName: dict[str, int] = {}
            PackedMirrorByName: dict[str, bool] = {}
            for Row in range(FoldRows):
                CheckWork(
                    "row-beam",
                    ClusterIndex=ClusterIndex,
                    CompletedRows=Row,
                    TotalRows=FoldRows,
                )
                RowNames = NamesByLevel.get(Row, [])
                RowNames.sort(
                    key=lambda Name: (
                        median(
                            [
                                PackedXByName[Producer] + 1
                                for Signal in InternalByName[Name].Inputs
                                if (Producer := SignalProducerNames.get(Signal))
                                in PackedXByName
                            ]
                            or [0]
                        ),
                        Name,
                    )
                )
                RowBeam: list[
                    tuple[
                        tuple[int, int, tuple[int, ...], int, tuple[int, ...]],
                        dict[str, tuple[int, bool]],
                    ]
                ] = [
                    ((0, 0, (), 0, ()), {})
                ]
                for Name in RowNames:
                    CheckWork(
                        "row-beam-gate",
                        ClusterIndex=ClusterIndex,
                        GateName=Name,
                    )
                    ParentItems = [
                        (
                            InputIndex,
                            PackedXByName[Producer] + 1,
                            LocalLevels[Producer] * CellPitchZ + NandDepth,
                        )
                        for InputIndex, Signal in enumerate(InternalByName[Name].Inputs)
                        if (Producer := SignalProducerNames.get(Signal))
                        in PackedXByName
                    ]
                    ParentOutputs = [Value[1] for Value in ParentItems]
                    CandidateXs = {
                        OutputX + InputAlignment
                        for OutputX in (ParentOutputs or [0])
                        for InputAlignment in (0, -2)
                    }
                    CandidateXs.update(
                        Value + Shift
                        for Value in tuple(CandidateXs)
                        for Shift in (-10, -5, 5, 10)
                    )
                    NextBeam = []
                    for PreviousKey, Assigned in RowBeam:
                        for CandidateX in sorted(CandidateXs):
                            if any(
                                abs(CandidateX - ExistingX) < 4
                                for ExistingX, _ExistingMirror in Assigned.values()
                            ):
                                continue
                            ExistingGates = [
                                BuildPlacedGate(
                                    InternalByName[ExistingName],
                                    ExistingX,
                                    1,
                                    LocalLevels[ExistingName] * CellPitchZ,
                                    PackedRotation,
                                    PackedMirrorByName[ExistingName],
                                )
                                for ExistingName, ExistingX in PackedXByName.items()
                            ]
                            ExistingGates.extend(
                                BuildPlacedGate(
                                    InternalByName[ExistingName],
                                    ExistingX,
                                    1,
                                    Row * CellPitchZ,
                                    PackedRotation,
                                    ExistingMirror,
                                )
                                for ExistingName, (
                                    ExistingX,
                                    ExistingMirror,
                                ) in Assigned.items()
                            )
                            OrientationOptions = []
                            for MirrorX in (False, True):
                                CandidateGate = BuildPlacedGate(
                                    InternalByName[Name],
                                    CandidateX,
                                    1,
                                    Row * CellPitchZ,
                                    PackedRotation,
                                    MirrorX,
                                )
                                if any(
                                    PcbGatesConflict(CandidateGate, ExistingGate)
                                    for ExistingGate in ExistingGates
                                ):
                                    continue
                                Pins = (
                                    (CandidateX, CandidateX + 2)
                                    if not MirrorX
                                    else (CandidateX + 2, CandidateX)
                                )
                                Misses = tuple(
                                    abs(OutputX - Pins[InputIndex])
                                    for InputIndex, OutputX, _OutputZ in ParentItems
                                )
                                InputZ = Row * CellPitchZ - 1
                                CrossPenalty = sum(
                                    1
                                    for InputIndex, OutputX, OutputZ in ParentItems
                                    for OtherIndex, OtherPinX in enumerate(Pins)
                                    if OtherIndex != InputIndex
                                    and OutputZ == InputZ
                                    and abs(OutputX - OtherPinX) <= 1
                                )
                                OrientationOptions.append(
                                    (CrossPenalty, sum(Misses), Misses, MirrorX)
                                )
                            if not OrientationOptions:
                                continue
                            CrossPenalty, Miss, Misses, CandidateMirror = min(
                                OrientationOptions
                            )
                            Candidate = dict(Assigned)
                            Candidate[Name] = (CandidateX, CandidateMirror)
                            Values = tuple(sorted(
                                ExistingX
                                for ExistingX, _MirrorX in Candidate.values()
                            ))
                            Span = max(Values) - min(Values) + NandWidth
                            NextBeam.append(
                                (
                                    (
                                        PreviousKey[0] + CrossPenalty,
                                        PreviousKey[1] + Miss,
                                        PreviousKey[2] + Misses,
                                        Span,
                                        Values,
                                    ),
                                    Candidate,
                                )
                            )
                    NextBeam.sort(key=lambda Value: Value[0])
                    RowBeam = NextBeam[: PackingPolicy.BeamWidth]
                if not RowBeam:
                    raise ValueError(
                        f"Could not pack NAND cluster row {ClusterIndex}:{Row}"
                    )
                PackedXByName.update({
                    Name: X
                    for Name, (X, _MirrorX) in RowBeam[0][1].items()
                })
                PackedMirrorByName.update({
                    Name: MirrorX
                    for Name, (_X, MirrorX) in RowBeam[0][1].items()
                })
            MinimumPackedX = min(PackedXByName.values())
            for Name in OrderedNames:
                LocalPositions[Name] = (
                    PackedXByName[Name] - MinimumPackedX,
                    LocalLevels[Name] * CellPitchZ,
                )
                LocalRotations[Name] = PackedRotation
                LocalMirrors[Name] = PackedMirrorByName[Name]
            BeamPacked = (
                BuildPinAlignedPackedCluster(
                    Names,
                    InternalByName,
                    PackingPolicy.BeamWidth,
                    WorkCheck=WorkCheck,
                )
                if PackingPolicy.GraphBeamEnabled
                else None
            )
            if BeamPacked is not None:
                BeamPositions, BeamRotations, BeamMirrors = BeamPacked
                LocalPositions.update(BeamPositions)
                LocalRotations.update(BeamRotations)
                LocalMirrors.update(BeamMirrors)
            FoldColumns = 1
            PackedWidth = max(
                LocalPositions[Name][0]
                + RotatedCellSize("NAND", LocalRotations[Name])[0]
                for Name in Names
            )
            PackedDepth = max(
                LocalPositions[Name][1]
                + RotatedCellSize("NAND", LocalRotations[Name])[1]
                for Name in Names
            )
        else:
            FoldColumns = max(1, ceil(sqrt(len(OrderedNames))))
            FoldRows = ceil(len(OrderedNames) / FoldColumns)
            for PositionIndex, Name in enumerate(OrderedNames):
                Row = PositionIndex // FoldColumns
                Offset = PositionIndex % FoldColumns
                Column = (
                    Offset
                    if Row % 2 == 0
                    else FoldColumns - 1 - Offset
                )
                LocalPositions[Name] = (
                    Column * CellPitchX,
                    Row * CellPitchZ,
                )
                LocalRotations[Name] = 270 if Row % 2 == 0 else 90
        if PackedMode and RequiredRelocationSignals:
            (
                LocalPositions,
                LocalMirrors,
                AccessRepairDiagnostics,
            ) = RepairPackedClusterAccess(
                Names,
                InternalByName,
                LocalPositions,
                LocalRotations,
                LocalMirrors,
                RequiredRelocationSignals,
                PackingPolicy.BeamWidth,
                WorkCheck=WorkCheck,
            )
            if AccessRepairDiagnostics:
                PackedAccessRepairByCluster[ClusterIndex] = (
                    AccessRepairDiagnostics
                )
                PackedWidth = max(
                    LocalPositions[Name][0]
                    + RotatedCellSize(
                        "NAND", LocalRotations[Name]
                    )[0]
                    for Name in Names
                )
                PackedDepth = max(
                    LocalPositions[Name][1]
                    + RotatedCellSize(
                        "NAND", LocalRotations[Name]
                    )[1]
                    for Name in Names
                )
        if (
            PackedMode
            and ClusterIndex in BoundaryEscapeRelocationClusters
            and (
                len(Clusters) > 4
                or ClusterIndex in LocalGeometryRepairClusters
            )
        ):
            # A fixed-access cut inside one dense cluster needs actual
            # corridor geometry, not another mirror of the same pin layout.
            # Keep the single-cluster perturbation to one routing tile; the
            # flow's existing packed-area ceiling remains the hard limit.
            BoundaryEscapeGap = (
                max(
                    PackingPolicy.LocalGeometryRepairColumnGap,
                    RoutingSpacing,
                )
                if len(Clusters) > 4
                else PackingPolicy.LocalGeometryRepairColumnGap
            )
            DistinctX = sorted({LocalPositions[Name][0] for Name in Names})
            XOffset = {
                Value: Index * BoundaryEscapeGap
                for Index, Value in enumerate(DistinctX)
            }
            for Name in Names:
                LocalX, LocalZ = LocalPositions[Name]
                LocalPositions[Name] = (
                    LocalX + XOffset[LocalX],
                    LocalZ,
                )
            PackedWidth = max(
                LocalPositions[Name][0]
                + RotatedCellSize("NAND", LocalRotations[Name])[0]
                for Name in Names
            )
            PackedDepth = max(
                LocalPositions[Name][1]
                + RotatedCellSize("NAND", LocalRotations[Name])[1]
                for Name in Names
            )
        if PackedMode:
            CandidateGates = [
                BuildPlacedGate(
                    InternalByName[Name],
                    LocalPositions[Name][0],
                    1,
                    LocalPositions[Name][1],
                    LocalRotations[Name],
                    LocalMirrors.get(Name, False),
                )
                for Name in Names
            ]
            if any(
                PcbGatesConflict(First, Second)
                for Index, First in enumerate(CandidateGates)
                for Second in CandidateGates[Index + 1 :]
            ):
                raise ValueError(
                    f"Could not pack NAND cluster {ClusterIndex} legally"
                )
        ClusterSizes[ClusterIndex] = (
            PackedWidth if PackedMode else (FoldColumns - 1) * CellPitchX + NandWidth,
            PackedDepth if PackedMode else (FoldRows - 1) * CellPitchZ + NandDepth,
        )

    if PackedMode and PackingPolicy.EnableVerticalClusterStacking:
        CheckWork("vertical-stacking-start")
        # Feedback identifies every cluster participating in the cut, but it
        # does not make every stacked cluster geometrically illegal.  Breaking
        # the full stack for an unmodified contributor expands a local repair
        # into a placement-wide footprint.  Only a concrete local repair is
        # allowed to alter stack geometry below.
        UnrepairedRequiredRelocationClusters: frozenset[int] = frozenset()
        ClusterByGate = {
            Name: ClusterIndex
            for ClusterIndex, Names in enumerate(Clusters)
            for Name in Names
        }
        InterClusterWeights: dict[tuple[int, int], int] = {}
        for Gate in Module.Gates:
            TargetCluster = ClusterByGate.get(Gate.Name)
            if TargetCluster is None:
                continue
            for Signal in Gate.Inputs:
                SourceCluster = ClusterByGate.get(
                    SignalProducerNames.get(Signal, "")
                )
                if SourceCluster is None or SourceCluster == TargetCluster:
                    continue
                Edge = SourceCluster, TargetCluster
                InterClusterWeights[Edge] = InterClusterWeights.get(Edge, 0) + 1

        MaximumClusterStack = PackingPolicy.MaximumClustersPerStack
        StackByCluster: dict[int, int] = {}
        StackMembers: dict[int, list[int]] = {}
        NextStackId = 0
        RepeatedStructuralClusters = (
            len(Clusters) >= 4
            and len({
                ClusterStructuralSignatures.get(ClusterIndex)
                for ClusterIndex in range(len(Clusters))
            }) == 1
        )
        WeakInterClusterChain = bool(InterClusterWeights) and max(
            InterClusterWeights.values()
        ) <= 2
        PlanarRepeatedClusterPlacement = (
            RepeatedStructuralClusters
            and WeakInterClusterChain
        )
        if (
            PlanarRepeatedClusterPlacement
            and not PackingPolicy.EnableRepeatedStructuralVerticalStacking
        ):
            # Repeated dense clusters connected by only a small boundary cut
            # are not stack-compatible routing resources.  A shared X/Z deck
            # turns their independent pin escapes into mandatory electrical
            # claims.  Make the initial placement planar so its fixed area
            # ceiling is measured against routable geometry, not a stack that
            # can only be repaired by exceeding that ceiling.
            # Keep the optimizer's two-dimensional placement.  The routing
            # defect is overlapping vertical stacks, not adjacency itself;
            # serializing every repeated cluster into a fresh column makes
            # the cut longer without creating a new independent escape.
            # MaximumClusterStack below is the ownership boundary that
            # prevents later stack merging from collapsing this grid.
            MaximumClusterStack = 1

        def StackEndpoints(StackId: int) -> tuple[int, int]:
            Values = StackMembers[StackId]
            return Values[0], Values[-1]

        def AddCluster(
            StackId: int,
            Endpoint: int,
            Candidate: int,
        ) -> None:
            Members = StackMembers[StackId]
            if len(Members) >= MaximumClusterStack:
                return
            if Endpoint == Members[0]:
                Members.insert(0, Candidate)
                Assignment[Candidate] = Assignment[Members[1]]
            elif Endpoint == Members[-1]:
                Members.append(Candidate)
                Assignment[Candidate] = Assignment[Members[-2]]
            else:
                raise ValueError(
                    "Cannot stack cluster on a non-endpoint"
                )
            StackByCluster[Candidate] = StackId

        def MergeStacks(
            SourceStack: int,
            SourceEndpoint: int,
            RightStack: int,
            TargetEndpoint: int,
        ) -> None:
            SourceMembers = StackMembers[SourceStack]
            TargetMembers = StackMembers[RightStack]
            if len(SourceMembers) + len(TargetMembers) > MaximumClusterStack:
                return
            BestMerge: tuple[int, ...] | None = None
            for OrientedSource in (tuple(SourceMembers), tuple(reversed(SourceMembers))):
                if SourceEndpoint not in OrientedSource:
                    continue
                if OrientedSource[-1] != SourceEndpoint:
                    continue
                for OrientedTarget in (tuple(TargetMembers), tuple(reversed(TargetMembers))):
                    if TargetEndpoint not in OrientedTarget:
                        continue
                    if OrientedTarget[0] != TargetEndpoint:
                        continue
                    CandidateStack = OrientedSource + OrientedTarget[1:]
                    if len(set(CandidateStack)) != len(CandidateStack):
                        continue
                    if BestMerge is None or CandidateStack < BestMerge:
                        BestMerge = CandidateStack
            if BestMerge is None:
                return
            StackMembers[SourceStack] = list(BestMerge)
            for Member in BestMerge:
                StackByCluster[Member] = SourceStack
                Assignment[Member] = Assignment[SourceMembers[0]]
            del StackMembers[RightStack]

        OrderedInterClusterWeights = sorted(
            InterClusterWeights.items(),
            key=lambda Value: (-Value[1], Value[0]),
        )
        for EdgeIndex, ((Source, Target), Weight) in enumerate(
            OrderedInterClusterWeights
        ):
            CheckWork(
                "vertical-stacking",
                CompletedEdges=EdgeIndex,
                TotalEdges=len(OrderedInterClusterWeights),
            )
            if (
                Source in UnrepairedRequiredRelocationClusters
                or Target in UnrepairedRequiredRelocationClusters
            ):
                if (
                    ClusterStructuralSignatures.get(Source)
                    == ClusterStructuralSignatures.get(Target)
                ):
                    StackSuppressedRelocationClusters.update((Source, Target))
                continue
            if (
                Weight < 1
                or MaximumClusterStack < 2
                or (
                    ClusterStructuralSignatures.get(Source)
                    != ClusterStructuralSignatures.get(Target)
                )
            ):
                continue
            SourceStack = StackByCluster.get(Source)
            TargetStack = StackByCluster.get(Target)
            if SourceStack is None and TargetStack is None:
                StackId = NextStackId
                NextStackId += 1
                StackMembers[StackId] = [Source, Target]
                StackByCluster[Source] = StackId
                StackByCluster[Target] = StackId
                Assignment[Target] = Assignment[Source]
                continue
            if SourceStack is not None and TargetStack is not None:
                if SourceStack == TargetStack:
                    continue
                SourceFirst, SourceLast = StackEndpoints(SourceStack)
                TargetFirst, TargetLast = StackEndpoints(TargetStack)
                if Source not in (SourceFirst, SourceLast):
                    continue
                if Target not in (TargetFirst, TargetLast):
                    continue
                MergeStacks(
                    SourceStack=SourceStack,
                    SourceEndpoint=Source,
                    RightStack=TargetStack,
                    TargetEndpoint=Target,
                )
                continue
            ActiveStack = SourceStack if SourceStack is not None else TargetStack
            Candidate = Target if SourceStack is not None else Source
            Endpoint = Source if SourceStack is not None else Target
            if len(StackMembers[ActiveStack]) >= MaximumClusterStack:
                continue
            AddCluster(ActiveStack, Endpoint, Candidate)

        for ClusterIndex in range(len(Clusters)):
            StackId = StackByCluster.get(ClusterIndex)
            if StackId is None:
                ClusterStackIds[ClusterIndex] = None
                ClusterStackLevels[ClusterIndex] = 0
            else:
                Members = StackMembers[StackId]
                ClusterStackIds[ClusterIndex] = StackId
                ClusterStackLevels[ClusterIndex] = Members.index(ClusterIndex)

        for ClusterIndex in range(len(Clusters)):
            ClusterStackIds.setdefault(ClusterIndex, None)
            ClusterStackLevels.setdefault(ClusterIndex, 0)

        UsedColumns = sorted({Slot[0] for Slot in Assignment.values()})
        CompactColumn = {Column: Index for Index, Column in enumerate(UsedColumns)}
        Assignment = {
            ClusterIndex: (CompactColumn[Column], Row)
            for ClusterIndex, (Column, Row) in Assignment.items()
        }
        ColumnCount = len(UsedColumns)
    else:
        ClusterStackIds = {Index: None for Index in range(len(Clusters))}
        ClusterStackLevels = {Index: 0 for Index in range(len(Clusters))}
    # A routing conflict between clusters that were never stack-compatible
    # still needs to change physical geometry. Move those clusters into
    # dedicated columns; merely replaying the same slot assignment would turn
    # conflict feedback into a duplicate placement.
    RequiredRelocationPriority = tuple(
        ClusterIndex
        for ClusterIndex in PrioritizeRelocationClusters(
            Module,
            Clusters,
            RequiredRelocationSignals,
        )
        if ClusterIndex not in PackedAccessRepairByCluster
        and ClusterIndex not in LocalGeometryRepairClusters
    )[:1]
    if LocalGeometryRepairClusters:
        RequiredRelocationPriority = ()
    CurrentRelocationPriority = PrioritizeRelocationClusters(
        Module,
        Clusters,
        RelocationPrioritySignals or RelocationSignals,
    )
    OptionalRelocationPriority = tuple(
        ClusterIndex
        for ClusterIndex in CurrentRelocationPriority
        if ClusterIndex not in StackSuppressedRelocationClusters
        and ClusterIndex not in RequiredRelocationPriority
    )
    # Preserve every congestion-cut contributor in feedback, but perturb one
    # additional physical cluster per deterministic round. Moving every
    # contributor at once tears apart a vertical stack and exceeds the fixed
    # two-times packed-area ceiling before the router can evaluate a repair.
    # Variants rotate through the complete ranked cut across the existing
    # placement-feedback rounds.
    MaximumOptionalRelocations = (
        0
        if LocalGeometryRepairClusters
        else min(1, len(OptionalRelocationPriority))
    )
    OptionalRelocationClusters = (
        tuple(
            OptionalRelocationPriority[
                (RelocationVariant + Offset)
                % len(OptionalRelocationPriority)
            ]
            for Offset in range(MaximumOptionalRelocations)
        )
        if OptionalRelocationPriority
        else ()
    )
    RelocationPriority = (
        *RequiredRelocationPriority,
        *OptionalRelocationClusters,
    )
    PhysicallyRelocatedClusters = frozenset(RelocationPriority)
    MirroredRelocationClusters = (
        frozenset(
            ClusterIndex
            for ClusterIndex in RelocationPriority
            if ClusterIndex not in PackedAccessRepairByCluster
        )
        if RelocationVariant > 0 and RelocationPriority
        else frozenset()
    )
    Assignment, ColumnCount = RelocateClusterSlots(
        Assignment,
        ColumnCount,
        RelocationPriority,
    )
    for ClusterIndex in RequiredRelocationPriority:
        ClusterStackIds[ClusterIndex] = None
        ClusterStackLevels[ClusterIndex] = 0
    ColumnWidths = {
        Column: max(
            (
                ClusterSizes[Index][0]
                for Index, Slot in Assignment.items()
                if Slot[0] == Column
            ),
            default=1,
        )
        for Column in range(ColumnCount)
    }
    RowDepths = {
        Row: max(
            (
                ClusterSizes[Index][1]
                for Index, Slot in Assignment.items()
                if Slot[1] == Row
            ),
            default=1,
        )
        for Row in range(max((Slot[1] for Slot in Assignment.values()), default=0) + 1)
    }
    GapPlan = BuildInterClusterGapPlan(
        BuildInterClusterBoundaryDemand(
            Module,
            Clusters,
            Assignment,
            WorkCheck=WorkCheck,
        ),
        ColumnCount=ColumnCount,
        RowCount=len(RowDepths),
        RoutingSpacing=RoutingSpacing,
        TrackPitch=(
            PlacementPolicy.DemandAwareBoundaryTrackPitch
            if (
                PlacementPolicy is not None
                and PlacementPolicy.DemandAwareBoundaryTrackPitch > 0
            )
            else DefaultRedstoneRoutingTechnology.TrackPitch
        ),
        Enabled=bool(
            PlacementPolicy is not None
            and PlacementPolicy.EnableDemandAwareInterClusterSpacing
        ),
    )
    ColumnExtraSpacing = GapPlan.ColumnSpacingByBoundary()
    RowExtraSpacing = GapPlan.RowSpacingByBoundary()
    ColumnOrigins: dict[int, int] = {}
    NextX = 0
    ColumnGap = 2 if PackedMode else 3
    RowGap = 1 if PackedMode else 2
    for Column in range(ColumnCount):
        ColumnOrigins[Column] = NextX
        NextX += ColumnWidths[Column]
        if Column + 1 < ColumnCount:
            NextX += ColumnGap + ColumnExtraSpacing[Column]
    RowOrigins: dict[int, int] = {}
    NextZ = 0
    for Row in sorted(RowDepths):
        RowOrigins[Row] = NextZ
        NextZ += RowDepths[Row]
        if Row + 1 < len(RowDepths):
            NextZ += RowGap + RowExtraSpacing[Row]
    InputMargin = 0
    PlacedGates = []
    for ClusterIndex, Names in enumerate(Clusters):
        CheckWork(
            "placement-commit",
            CompletedClusters=ClusterIndex,
            TotalClusters=len(Clusters),
        )
        SlotX, SlotZ = Assignment[ClusterIndex]
        BaseX = InputMargin + ColumnOrigins[SlotX]
        BaseZ = RowOrigins[SlotZ]
        BaseY = 1 + (
            ClusterStackLevels[ClusterIndex] * PackingPolicy.ClusterDeckPitch
            if PackedMode
            else 0
        )
        CandidateClusterGates = []
        for Name in Names:
            LocalX, LocalZ = LocalPositions[Name]
            Rotation = LocalRotations[Name]
            MirrorX = LocalMirrors.get(Name, False)
            if ClusterIndex in MirroredRelocationClusters:
                GateWidth = RotatedCellSize(
                    InternalByName[Name].Kind.value,
                    Rotation,
                )[0]
                LocalX = ClusterSizes[ClusterIndex][0] - LocalX - GateWidth
                MirrorX = not MirrorX
            CandidateClusterGates.append(
                BuildPlacedGate(
                    InternalByName[Name],
                    BaseX + LocalX,
                    BaseY,
                    BaseZ + LocalZ,
                    Rotation,
                    MirrorX,
                )
            )
        if PackedMode and (
            any(
                PcbGatesConflict(Candidate, Existing)
                for Candidate in CandidateClusterGates
                for Existing in PlacedGates
            )
            or any(
                PcbGatesConflict(First, Second)
                for Index, First in enumerate(CandidateClusterGates)
                for Second in CandidateClusterGates[Index + 1 :]
            )
        ):
            raise ValueError(
                f"Packed NAND cluster {ClusterIndex} conflicts at placement commit"
            )
        PlacedGates.extend(CandidateClusterGates)

    InputGates = [Gate for Gate in Module.Gates if Gate.Kind.value == "INPUT"]
    OutputGates = [Gate for Gate in Module.Gates if Gate.Kind.value == "OUTPUT"]

    if PackedMode:
        ClusterByGate = {
            Name: ClusterIndex
            for ClusterIndex, Names in enumerate(Clusters)
            for Name in Names
        }
        TerminalConsumers: dict[str, list[Any]] = {}
        for ModuleGate in Module.Gates:
            for Signal in ModuleGate.Inputs:
                TerminalConsumers.setdefault(Signal, []).append(ModuleGate)

    InternalMinimumX = min(Gate.X for Gate in PlacedGates)
    InternalMaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in PlacedGates
    )
    InternalMinimumZ = min(Gate.Z for Gate in PlacedGates)
    InternalMaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in PlacedGates
    )

    def PlaceTerminalBank(
        Gates: list[Any],
        BankZ: int,
        OutwardStep: int,
        PortNames: list[str],
    ) -> None:
        """Place terminals in their declared SystemVerilog port order."""
        PortIndexes = {
            Signal: Index
            for Index, Signal in enumerate(PortNames)
        }

        def TerminalSignal(Gate: Any) -> str:
            return (
                Gate.Outputs[0]
                if Gate.Kind.value == "INPUT"
                else Gate.Inputs[0]
            )

        Ordered = sorted(
            Gates,
            key=lambda Gate: (
                PortIndexes[TerminalSignal(Gate)],
                Gate.Name,
            ),
        )
        CenterX = (InternalMinimumX + InternalMaximumX) // 2
        TerminalSpacings = (
            (2, 3)
            if PackedMode
            else
            (4 + RoutingSpacing, 3 + RoutingSpacing)
            if PlacementPolicy is not None
            and PlacementPolicy.PreferWideTerminalBanks
            else (3 + RoutingSpacing, 4 + RoutingSpacing)
        )
        for Spacing in TerminalSpacings:
            CheckWork("terminal-bank-spacing", Spacing=Spacing)
            BankWidth = max(1, 1 + Spacing * (len(Ordered) - 1))
            StartX = CenterX - BankWidth // 2 + (
                PlacementPolicy.TerminalBankOffsetX
                if PlacementPolicy is not None
                and Ordered
                and Ordered[0].Kind.value == "INPUT"
                else 0
            )
            for Setback in range(32):
                CheckWork(
                    "terminal-bank-setback",
                    Spacing=Spacing,
                    Setback=Setback,
                )
                CandidateZ = BankZ + Setback * OutwardStep
                Terminals = [
                    BuildPlacedGate(
                        Gate,
                        StartX + Index * Spacing,
                        1,
                        CandidateZ,
                        0,
                        False,
                    )
                    for Index, Gate in enumerate(Ordered)
                ]
                ConflictsWithPlacement = any(
                    PcbGatesConflict(Terminal, Existing)
                    for Terminal in Terminals
                    for Existing in PlacedGates
                )
                ConflictsWithinBank = any(
                    PcbGatesConflict(First, Second)
                    for Index, First in enumerate(Terminals)
                    for Second in Terminals[Index + 1 :]
                )
                if ConflictsWithPlacement or ConflictsWithinBank:
                    continue
                PlacedGates.extend(Terminals)
                return
        raise ValueError("Could not place grouped terminal bank legally")

    def PlaceLocalizedTerminals(
        Gates: list[Any],
        PortIndexes: dict[str, int],
    ) -> list[Any] | None:
        """Place packed-mode I/O on the exterior shell of the NAND fabric."""
        Producers = {
            Signal: Gate
            for Gate in PlacedGates
            if Gate.OutputPin is not None
            for Signal in Gate.Outputs
        }
        Targets: dict[str, list[tuple[int, int, int]]] = {}
        for Existing in PlacedGates:
            for InputIndex, Signal in enumerate(Existing.Inputs):
                Targets.setdefault(Signal, []).append(Existing.InputPins[InputIndex])

        def TerminalSignal(Gate: Any) -> str:
            return Gate.Outputs[0] if Gate.Kind.value == "INPUT" else Gate.Inputs[0]

        def TerminalCluster(Gate: Any) -> int | None:
            Signal = TerminalSignal(Gate)
            if Gate.Kind.value == "INPUT":
                CandidateClusters = {
                    ClusterByGate[Consumer.Name]
                    for Consumer in TerminalConsumers.get(Signal, ())
                    if Consumer.Name in ClusterByGate
                }
            else:
                Producer = Producers.get(Signal)
                CandidateClusters: set[int] = set()
                if Producer is not None and Producer.Name in ClusterByGate:
                    CandidateClusters.add(ClusterByGate[Producer.Name])
            return min(CandidateClusters) if CandidateClusters else None

        def TerminalOrderKey(Value: Any) -> tuple[Any, ...]:
            ClusterIndex = TerminalCluster(Value)
            return (
                ClusterIndex is None,
                ClusterIndex if ClusterIndex is not None else 10**6,
                PortIndexes[TerminalSignal(Value)],
                Value.Name,
            )

        OptionsByGate: list[tuple[str, list[tuple[tuple[Any, ...], Any]]]] = []
        for Gate in sorted(
            Gates,
            key=TerminalOrderKey,
        ):
            CheckWork("localized-terminal", GateName=Gate.Name)
            Signal = TerminalSignal(Gate)
            DesiredPins = (
                Targets.get(Signal, [])
                if Gate.Kind.value == "INPUT"
                else [Producers[Signal].OutputPin]
            )
            # A high-fanout terminal must not be pinned beside an arbitrary
            # consumer.  In particular, a signal which crosses packed-cluster
            # boundaries needs a balanced escape location: limiting candidates
            # to a radius around every individual sink makes the terminal hug an
            # edge and unnecessarily consumes a tall global routing layer.
            # Keep the search bounded, but include a median anchor for every
            # multi-target terminal so the choice generalizes to any topology.
            TargetXs = sorted(Pin[0] for Pin in DesiredPins)
            TargetZs = sorted(Pin[2] for Pin in DesiredPins)
            TargetMiddle = len(DesiredPins) // 2
            MedianAnchor = (
                (TargetXs[(len(TargetXs) - 1) // 2] + TargetXs[TargetMiddle]) // 2,
                DesiredPins[0][1],
                (TargetZs[(len(TargetZs) - 1) // 2] + TargetZs[TargetMiddle]) // 2,
            )
            CandidatePinPositions = {
                (
                    Pin[0] + DeltaX,
                    Pin[1],
                    Pin[2] + DeltaZ,
                )
                for Pin in DesiredPins
                for DeltaX, DeltaZ in (
                    (0, 0),
                    (1, 0),
                    (-1, 0),
                    (0, 1),
                    (0, -1),
                    (2, 0),
                    (-2, 0),
                    (0, 2),
                    (0, -2),
                )
            }
            if len(DesiredPins) > 1:
                CandidatePinPositions.update(
                    (
                        MedianAnchor[0] + DeltaX,
                        MedianAnchor[1],
                        MedianAnchor[2] + DeltaZ,
                    )
                    for DeltaX in range(-3, 4)
                    for DeltaZ in range(-3, 4)
                    if abs(DeltaX) + abs(DeltaZ) <= 3
                )
            # I/O is an external interface, not a component of the logic
            # interior.  Keep the shell compact by assigning inputs and
            # outputs to opposing exterior faces, aligned with the pins they
            # serve.  This avoids four-sided horizontal sprawl while ensuring
            # every terminal is visible and approachable at the edge of an
            # arbitrary packed graph.
            ShellAnchors = (*DesiredPins, MedianAnchor)
            ShellClearance = PackingPolicy.TerminalShellClearance
            ShellLateralSearch = PackingPolicy.TerminalShellLateralSearch
            ShellZ = (
                InternalMinimumZ - ShellClearance
                if Gate.Kind.value == "INPUT"
                else InternalMaximumZ + ShellClearance
            )
            for Anchor in ShellAnchors:
                CandidatePinPositions.update(
                    (
                        (
                            Anchor[0] + Delta,
                            Anchor[1],
                            ShellZ,
                        )
                        for Delta in range(
                            -ShellLateralSearch,
                            ShellLateralSearch + 1,
                        )
                    )
                )
            Options = []
            for Rotation in (0, 90, 180, 270):
                CheckWork(
                    "localized-terminal-rotation",
                    GateName=Gate.Name,
                    Rotation=Rotation,
                )
                Origin = BuildPlacedGate(Gate, 0, 1, 0, Rotation, False)
                LocalPin = (
                    Origin.OutputPin
                    if Gate.Kind.value == "INPUT"
                    else Origin.InputPins[0]
                )
                for PinPosition in sorted(CandidatePinPositions):
                    Candidate = BuildPlacedGate(
                        Gate,
                        PinPosition[0] - LocalPin[0],
                        PinPosition[1],
                        PinPosition[2] - LocalPin[2],
                        Rotation,
                        False,
                    )
                    if any(
                        PcbGatesConflict(Candidate, Existing)
                        for Existing in PlacedGates
                    ):
                        continue
                    CandidatePin = (
                        Candidate.OutputPin
                        if Gate.Kind.value == "INPUT"
                        else Candidate.InputPins[0]
                    )
                    Distance = sum(
                        abs(CandidatePin[0] - Pin[0])
                        + abs(CandidatePin[2] - Pin[2])
                        for Pin in DesiredPins
                    )
                    MaximumDistance = max(
                        abs(CandidatePin[0] - Pin[0])
                        + abs(CandidatePin[2] - Pin[2])
                        for Pin in DesiredPins
                    )
                    CandidateWidth, CandidateDepth = RotatedCellSize(
                        Candidate.Kind,
                        Candidate.Rotation,
                    )
                    CandidateMaximumX = Candidate.X + CandidateWidth - 1
                    CandidateMaximumZ = Candidate.Z + CandidateDepth - 1
                    IsOutsideCore = (
                        CandidateMaximumX < InternalMinimumX
                        or Candidate.X > InternalMaximumX
                        or CandidateMaximumZ < InternalMinimumZ
                        or Candidate.Z > InternalMaximumZ
                    )
                    if not IsOutsideCore:
                        continue
                    MinimumX = min(
                        [Existing.X for Existing in PlacedGates]
                        + [Candidate.X]
                    )
                    MaximumX = max(
                        [
                            Existing.X
                            + RotatedCellSize(Existing.Kind, Existing.Rotation)[0]
                            for Existing in PlacedGates
                        ]
                        + [Candidate.X + CandidateWidth]
                    )
                    MinimumZ = min(
                        [Existing.Z for Existing in PlacedGates]
                        + [Candidate.Z]
                    )
                    MaximumZ = max(
                        [
                            Existing.Z
                            + RotatedCellSize(Existing.Kind, Existing.Rotation)[1]
                            for Existing in PlacedGates
                        ]
                        + [Candidate.Z + CandidateDepth]
                    )
                    Width = MaximumX - MinimumX
                    Depth = MaximumZ - MinimumZ
                    Options.append(
                        (
                            (
                                MaximumDistance,
                                Distance,
                                Width * Depth,
                                max(Width, Depth),
                                Candidate.X,
                                Candidate.Z,
                                Rotation,
                            ),
                            Candidate,
                        )
                    )
            if not Options:
                return None
            OrderedOptions = sorted(
                Options,
                key=lambda Value: (
                    Value[0][2],
                    Value[0][3],
                    Value[0][0],
                    Value[0][1],
                    Value[0][4:],
                ),
            )

            def ExteriorFace(Option: tuple[tuple[Any, ...], Any]) -> str:
                """Classify the shell face reached by one legal terminal cell."""
                Candidate = Option[1]
                Width, Depth = RotatedCellSize(
                    Candidate.Kind,
                    Candidate.Rotation,
                )
                MaximumX = Candidate.X + Width - 1
                MaximumZ = Candidate.Z + Depth - 1
                if MaximumZ < InternalMinimumZ:
                    return "north"
                if Candidate.Z > InternalMaximumZ:
                    return "south"
                if MaximumX < InternalMinimumX:
                    return "west"
                return "east"

            # Keep one low-cost representative on every exterior face before
            # truncating the bounded candidate pool.  A terminal that looks
            # marginally worse in isolation can avoid increasing the shared
            # X or Z envelope once neighbouring ports have already occupied a
            # different face.  This is topology-agnostic and leaves the exact
            # joint assignment responsible for the final choice.
            FaceRepresentatives: list[tuple[tuple[Any, ...], Any]] = []
            SeenFaces: set[str] = set()
            for Option in OrderedOptions:
                Face = ExteriorFace(Option)
                if Face in SeenFaces:
                    continue
                SeenFaces.add(Face)
                FaceRepresentatives.append(Option)
            SelectedOptions = list(FaceRepresentatives)
            for Option in OrderedOptions:
                if Option in SelectedOptions:
                    continue
                SelectedOptions.append(Option)
                if len(SelectedOptions) >= PackingPolicy.MaximumTerminalPlacementCandidates:
                    break
            OptionsByGate.append((
                Gate.Name,
                SelectedOptions[:PackingPolicy.MaximumTerminalPlacementCandidates],
            ))

        BestSelection: tuple[Any, ...] | None = None
        BestScore: tuple[Any, ...] | None = None
        AssignmentExpansions = 0

        def SelectionScore(
            Selected: tuple[tuple[tuple[Any, ...], Any], ...],
        ) -> tuple[Any, ...]:
            AllGates = (*PlacedGates, *(Candidate for _Key, Candidate in Selected))
            MinimumX = min(Gate.X for Gate in AllGates)
            MaximumX = max(
                Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
                for Gate in AllGates
            )
            MinimumZ = min(Gate.Z for Gate in AllGates)
            MaximumZ = max(
                Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
                for Gate in AllGates
            )
            Width = MaximumX - MinimumX
            Depth = MaximumZ - MinimumZ
            return (
                Width * Depth,
                max(Width, Depth),
                sum(Key[0] for Key, _Candidate in Selected),
                sum(Key[1] for Key, _Candidate in Selected),
                tuple(
                    (Candidate.Name, Candidate.X, Candidate.Z, Candidate.Rotation)
                    for _Key, Candidate in Selected
                ),
            )

        def SearchTerminalAssignments(
            Index: int,
            Selected: tuple[tuple[tuple[Any, ...], Any], ...],
        ) -> None:
            nonlocal AssignmentExpansions, BestSelection, BestScore
            if AssignmentExpansions >= PackingPolicy.MaximumTerminalAssignmentExpansions:
                return
            AssignmentExpansions += 1
            if Index == len(OptionsByGate):
                Score = SelectionScore(Selected)
                if BestScore is None or Score < BestScore:
                    BestScore = Score
                    BestSelection = Selected
                return
            _GateName, Options = OptionsByGate[Index]
            for Option in Options:
                _Key, Candidate = Option
                if any(
                    PcbGatesConflict(Candidate, Existing)
                    for _SelectedKey, Existing in Selected
                ):
                    continue
                SearchTerminalAssignments(Index + 1, (*Selected, Option))

        SearchTerminalAssignments(0, ())
        if BestSelection is None:
            return None
        return [Candidate for _Key, Candidate in BestSelection]

    if PackedMode:
        # Prefer compact, cluster-aware terminals when feasible. If any localized
        # choice conflicts (including overlap with each other), fall back to
        # deterministic side banks for reliability.
        BasePlacement = list(PlacedGates)
        UseLocalizedTerminals = True
        TerminalPortIndexes = {
            Signal: Index
            for Index, Signal in enumerate((*Module.Inputs, *Module.Outputs))
        }
        PlannedTerminals = (
            PlaceLocalizedTerminals(
                [*InputGates, *OutputGates],
                TerminalPortIndexes,
            )
            if UseLocalizedTerminals
            else None
        )
        if PlannedTerminals is not None:
            CandidatePlacement = BasePlacement + PlannedTerminals
            try:
                if any(
                    PcbGatesConflict(First, Second)
                    for Index, First in enumerate(CandidatePlacement)
                    for Second in CandidatePlacement[Index + 1 :]
                ):
                    raise ValueError("localized terminal placement conflicts")
                _ = BuildPlacedCellGeometry(
                    PlacedDesign(Module=Module, PlacedGates=CandidatePlacement)
                )
                PlacedGates = CandidatePlacement
            except ValueError:
                PlannedTerminals = None
        if PlannedTerminals is None:
            PlacedGates = BasePlacement
            PlaceTerminalBank(
                InputGates,
                InternalMinimumZ - 4,
                -1,
                list(Module.Inputs),
            )
            PlaceTerminalBank(
                OutputGates,
                InternalMaximumZ + 2,
                1,
                list(Module.Outputs),
            )
    else:
        PlaceTerminalBank(
            InputGates,
            InternalMinimumZ - 4,
            -1,
            list(Module.Inputs),
        )
        PlaceTerminalBank(
            OutputGates,
            InternalMaximumZ + 2,
            1,
            list(Module.Outputs),
        )

    if PackedMode and any(
        PcbGatesConflict(First, Second)
        for Index, First in enumerate(PlacedGates)
        for Second in PlacedGates[Index + 1 :]
    ):
        raise ValueError("Packed placement conflicts at final commit")
    CheckWork("terminal-placement-complete", GateCount=len(PlacedGates))
    Placed = PlacedDesign(Module=Module, PlacedGates=PlacedGates)
    if PackedMode:
        Producers = {
            Signal: Gate
            for Gate in PlacedGates
            if Gate.OutputPin is not None
            for Signal in Gate.Outputs
        }
        TargetsBySignal: dict[str, list[tuple[int, int, int]]] = {}
        for Gate in PlacedGates:
            CheckWork("local-access-geometry", GateName=Gate.Name)
            for InputIndex, Signal in enumerate(Gate.Inputs):
                TargetsBySignal.setdefault(Signal, []).append(
                    Gate.InputPins[InputIndex]
                )
        FrozenNetWires = {}
        LocalNetBranches = {}
        LocalNetTargets = {}
        LocalRouteClaims = []
        LocalRouteDiagnostics = {}
        JointLocalCandidatesByCluster: dict[
            int, dict[str, list[LocalClusterRouteCandidate]]
        ] = {}
        LocalRouteDiagnostics["__InterClusterGaps__"] = (
            GapPlan.ToDictionary()
        )
        if PackedAccessRepairByCluster:
            LocalRouteDiagnostics["__PackedAccessRepair__"] = {
                str(ClusterIndex): Diagnostics
                for ClusterIndex, Diagnostics in sorted(
                    PackedAccessRepairByCluster.items()
                )
            }
        ActualBlocks, ElectricalBlocks, SolidBlocks = BuildPlacedCellGeometry(Placed)
        LocalResourceGraph = RoutingResourceGraph(
            ActualBlocks=frozenset(ActualBlocks),
            ElectricalBlocks=frozenset(ElectricalBlocks),
            SolidBlocks=frozenset(SolidBlocks),
        )
        ClusterByGate = {
            Name: ClusterIndex
            for ClusterIndex, Names in enumerate(Clusters)
            for Name in Names
        }
        GateByInputPin = {
            Pin: Gate.Name
            for Gate in PlacedGates
            for Pin in Gate.InputPins
        }
        MaximumLength = PackingPolicy.DirectConnectMaximumLength
        # This is a geometric search envelope, not an electrical allowance.
        # ValidateLocalSignalStrength below derives the accepted repeater-free
        # distance from the active routing technology.
        MaximumLocalRouteLength = PackingPolicy.MaximumLocalRouteLength
        MinimumRouteX = min(Gate.X for Gate in PlacedGates) - PackingPolicy.LocalRouteEnvelope
        MaximumRouteX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in PlacedGates
        ) + PackingPolicy.LocalRouteEnvelope
        MinimumRouteZ = min(Gate.Z for Gate in PlacedGates) - PackingPolicy.LocalRouteEnvelope
        MaximumRouteZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in PlacedGates
        ) + PackingPolicy.LocalRouteEnvelope
        MinimumRouteY = min(Gate.Y for Gate in PlacedGates)
        MaximumRouteY = (
            max(Gate.Y for Gate in PlacedGates)
            + PackingPolicy.LocalRouteEnvelope
        )
        AccessBySignal: dict[str, set[tuple[int, int, int]]] = {}
        AccessByClusterSignal: dict[
            tuple[int, str], set[tuple[int, int, int]]
        ] = {}
        BoundaryAccessBySignal: dict[str, set[tuple[int, int, int]]] = {}
        for Gate in PlacedGates:
            GateCluster = ClusterByGate.get(Gate.Name)
            if Gate.OutputPin is not None and Gate.OutputDirection is not None:
                for Signal in Gate.Outputs:
                    OutputAccess = tuple(
                        (
                            Gate.OutputPin[0] + Gate.OutputDirection[0] * Offset,
                            Gate.OutputPin[1] + Gate.OutputDirection[1] * Offset,
                            Gate.OutputPin[2] + Gate.OutputDirection[2] * Offset,
                        )
                        for Offset in range(3)
                    )
                    AccessBySignal.setdefault(Signal, set()).update(OutputAccess)
                    if GateCluster is not None:
                        AccessByClusterSignal.setdefault(
                            (GateCluster, Signal),
                            set(),
                        ).update(OutputAccess)
                    BoundaryAccessBySignal.setdefault(Signal, set()).update(
                        OutputAccess[:2]
                    )
            for Signal, Pin, Direction in zip(
                Gate.Inputs, Gate.InputPins, Gate.InputDirections
            ):
                InputAccess = tuple(
                    (
                        Pin[0] + Direction[0] * Offset,
                        Pin[1] + Direction[1] * Offset,
                        Pin[2] + Direction[2] * Offset,
                    )
                    for Offset in range(3)
                )
                AccessBySignal.setdefault(Signal, set()).update(InputAccess)
                if GateCluster is not None:
                    AccessByClusterSignal.setdefault(
                        (GateCluster, Signal),
                        set(),
                    ).update(InputAccess)
                BoundaryAccessBySignal.setdefault(Signal, set()).update(
                    InputAccess[:2]
                )
        AccessClaimsBySignal = {
            Signal: LocalResourceGraph.BuildRouteClaims(Positions)
            for Signal, Positions in BoundaryAccessBySignal.items()
            if Positions
        }

        def ValidateBoundaryEscapes(Candidate: LocalRouteClaim) -> None:
            """Keep fixed local trees from consuming another net's pin escape."""
            for OtherSignal, AccessClaims in AccessClaimsBySignal.items():
                if OtherSignal == Candidate.Signal:
                    continue
                Conflicts = FindClaimConflicts(
                    {
                        Candidate.Signal: Candidate.Claims,
                        OtherSignal: AccessClaims,
                    }
                )
                if Conflicts:
                    Resource = min(Conflicts, key=str)
                    raise ValueError(
                        "Local route blocks boundary escape at "
                        f"{Resource}: {Candidate.Signal},{OtherSignal}"
                    )

        def FindLocalPath(
            Starts: set[tuple[int, int, int]],
            Target: tuple[int, int, int],
            Signal: str,
        ) -> tuple[tuple[int, int, int], ...]:
            """Find one bounded component-plane extension from an owned tree."""
            OtherClaims = [
                Claim for Claim in LocalRouteClaims if Claim.Signal != Signal
            ]
            Blocked = set().union(
                *(Claim.Claims.ElectricalCells for Claim in OtherClaims)
            ) if OtherClaims else set()
            Parents: dict[tuple[int, int, int], tuple[int, int, int] | None] = {
                Start: None for Start in sorted(Starts)
            }
            Distances = {Start: 0 for Start in Starts}
            Pending = deque(sorted(Starts))
            CompletedNodes = 0
            while Pending and Target not in Parents:
                CompletedNodes += 1
                if CompletedNodes % 256 == 0:
                    CheckWork(
                        "local-path-search",
                        Signal=Signal,
                        CompletedNodes=CompletedNodes,
                        PendingNodes=len(Pending),
                    )
                Current = Pending.popleft()
                Distance = Distances[Current]
                if Distance >= MaximumLocalRouteLength:
                    continue
                for Neighbor in sorted(
                    DefaultRedstoneRoutingTechnology.NeighborPositions(Current)
                ):
                    if Neighbor in Parents:
                        continue
                    if not (
                        MinimumRouteX <= Neighbor[0] <= MaximumRouteX
                        and MinimumRouteZ <= Neighbor[2] <= MaximumRouteZ
                        and MinimumRouteY <= Neighbor[1] <= MaximumRouteY
                    ):
                        continue
                    if Neighbor in ActualBlocks and Neighbor != Target:
                        continue
                    if (
                        Neighbor in LocalResourceGraph.StaticKeepOut
                        and Neighbor not in AccessBySignal.get(Signal, set())
                        and Neighbor != Target
                    ):
                        continue
                    if Neighbor in Blocked and Neighbor != Target:
                        continue
                    if LocalResourceGraph.BuildPrimitive(Current, Neighbor) is None:
                        continue
                    Support = (Neighbor[0], Neighbor[1] - 1, Neighbor[2])
                    if Support in ActualBlocks and Neighbor != Target:
                        continue
                    Parents[Neighbor] = Current
                    Distances[Neighbor] = Distance + 1
                    Pending.append(Neighbor)
            if Target not in Parents:
                return ()
            Result = []
            Current = Target
            while Current is not None and Current not in Starts:
                Result.append(Current)
                Current = Parents[Current]
            if Current is not None:
                Result.append(Current)
            return tuple(reversed(Result))

        def SelectBoundaryNodes(
            Nodes: frozenset[tuple[int, int, int]],
            AllTargets: list[tuple[int, int, int]],
            ConnectedTargets: list[tuple[int, int, int]],
        ) -> tuple[tuple[int, int, int], ...]:
            """Expose only deterministic continuation points for remote sinks."""
            Unresolved = sorted(set(AllTargets) - set(ConnectedTargets))
            return tuple(sorted({
                min(
                    Nodes,
                    key=lambda Position: (
                        abs(Target[0] - Position[0])
                        + abs(Target[1] - Position[1])
                        + abs(Target[2] - Position[2]),
                        Position,
                    ),
                )
                for Target in Unresolved
            }))

        def ValidateLocalSignalStrength(Candidate: LocalRouteClaim) -> None:
            """Reject local trees that require a repeater not yet reserved."""
            Graph: dict[tuple[int, int, int], set[tuple[int, int, int]]] = {
                Position: set() for Position in Candidate.Nodes
            }
            for First, Second in Candidate.Edges:
                Graph[First].add(Second)
                Graph[Second].add(First)
            Distances = {Candidate.Root: 0}
            Pending = deque((Candidate.Root,))
            while Pending:
                if len(Distances) % 256 == 0:
                    CheckWork(
                        "local-signal-strength",
                        Signal=Candidate.Signal,
                        CompletedNodes=len(Distances),
                        PendingNodes=len(Pending),
                    )
                Current = Pending.popleft()
                for Neighbor in Graph.get(Current, ()):
                    if Neighbor in Distances:
                        continue
                    Distances[Neighbor] = Distances[Current] + 1
                    Pending.append(Neighbor)
            MaximumDistance = max(
                (Distances.get(Target, 10**9) for Target in Candidate.ConnectedTargets),
                default=0,
            )
            if (
                MaximumDistance
                >= DefaultRedstoneRoutingTechnology.MaximumUnrefreshedDustLength
                and not Candidate.RepeaterReservations
            ):
                raise ValueError(
                    "Local route requires a repeater before its farthest sink: "
                    f"{Candidate.Signal} distance={MaximumDistance}"
                )

        def ValidateLocalPhysicalConnectivity(
            Candidate: LocalRouteClaim,
        ) -> None:
            """Reject local claims that are connected only in the abstract graph."""
            CandidateProducer = Producers.get(Candidate.Signal)
            if CandidateProducer is None:
                raise ValueError(
                    f"Local route has no producer: {Candidate.Signal}"
                )
            CandidateSupports = set(Candidate.Claims.SupportCells) - ActualBlocks
            PhysicalGraphs = BuildPhysicalGraphs(
                {Candidate.Signal: set(Candidate.Nodes)},
                ActualBlocks,
                CandidateSupports,
                SolidBlocks,
            )
            ValidatePhysicalRoutes(
                PhysicalGraphs,
                {Candidate.Signal: CandidateProducer},
                {
                    Candidate.Signal: list(Candidate.ConnectedTargets),
                },
            )

        def ValidateContinuationPortal(
            Candidate: LocalRouteClaim,
            AllTargets: list[tuple[int, int, int]],
        ) -> None:
            """Require an unclaimed legal frontier for every partial tree."""
            if set(AllTargets).issubset(Candidate.ConnectedTargets):
                return
            if not Candidate.BoundaryNodes:
                raise ValueError(
                    f"Partial local route has no continuation node: {Candidate.Signal}"
                )
            if (
                MaximumEntrancesPerSignal is not None
                and len(Candidate.BoundaryNodes) > MaximumEntrancesPerSignal
            ):
                raise ValueError(
                    "Partial local route exceeds per-signal entrance budget: "
                    f"{Candidate.Signal} entrances={len(Candidate.BoundaryNodes)}"
                )
            ForeignElectrical = set().union(*(
                Claim.Claims.ElectricalCells
                for Claim in LocalRouteClaims
                if Claim.Signal != Candidate.Signal
            )) if LocalRouteClaims else set()
            for Boundary in Candidate.BoundaryNodes:
                for Neighbor in sorted(
                    DefaultRedstoneRoutingTechnology.NeighborPositions(Boundary)
                ):
                    if Neighbor in Candidate.Nodes or Neighbor in ActualBlocks:
                        continue
                    if Neighbor in ForeignElectrical:
                        continue
                    if LocalResourceGraph.BuildPrimitive(Boundary, Neighbor) is not None:
                        return
            raise ValueError(
                f"Partial local route has no legal continuation portal: {Candidate.Signal}"
            )
        for Signal, Targets in sorted(
            TargetsBySignal.items(),
            key=lambda Value: (
                0
                if Producers.get(Value[0]) is not None
                and Producers[Value[0]].Kind == "NAND"
                else 1,
                -len(set(Value[1])),
                Value[0],
            ),
        ):
            CheckWork(
                "local-route-signal",
                Signal=Signal,
                TargetCount=len(Targets),
            )
            Producer = Producers.get(Signal)
            if Producer is None or not Targets:
                continue
            AllTargets = Targets
            ProducerCluster = ClusterByGate.get(Producer.Name)
            # A placement-owned tree is strictly local to its producer's
            # packed cluster.  Remote sinks remain terminal demand for the
            # authoritative global router and are represented only by the
            # continuation portal below.
            if ProducerCluster is not None:
                Targets = [
                    Target for Target in AllTargets
                    if ClusterByGate.get(GateByInputPin.get(Target))
                    == ProducerCluster
                ]
            if not Targets:
                continue
            Root = Producer.OutputPin
            Paths = []
            LocalTargets = []
            for Target in Targets:
                CheckWork(
                    "local-route-direct-target",
                    Signal=Signal,
                    Target=Target,
                )
                DeltaX = Target[0] - Root[0]
                DeltaY = Target[1] - Root[1]
                DeltaZ = Target[2] - Root[2]
                Distance = abs(DeltaX) + abs(DeltaY) + abs(DeltaZ)
                if (
                    Distance > MaximumLength
                    or sum(Value != 0 for Value in (DeltaX, DeltaY, DeltaZ)) > 1
                ):
                    continue
                Step = (
                    0 if DeltaX == 0 else (1 if DeltaX > 0 else -1),
                    0 if DeltaY == 0 else (1 if DeltaY > 0 else -1),
                    0 if DeltaZ == 0 else (1 if DeltaZ > 0 else -1),
                )
                Paths.append(
                    tuple(
                        (
                            Root[0] + Step[0] * Offset,
                            Root[1] + Step[1] * Offset,
                            Root[2] + Step[2] * Offset,
                        )
                        for Offset in range(Distance + 1)
                    )
                )
                LocalTargets.append(Target)
            DirectPaths = list(Paths)
            DirectTargets = list(LocalTargets)
            OwnedNodes = {Position for Path in Paths for Position in Path} or {Root}
            RemainingTargets = sorted(
                set(Targets) - set(LocalTargets),
                key=lambda Target: (
                    min(
                        abs(Target[0] - Position[0])
                        + abs(Target[1] - Position[1])
                        + abs(Target[2] - Position[2])
                        for Position in OwnedNodes
                    ),
                    Target,
                ),
            )
            for Target in (
                RemainingTargets if MaximumLocalRouteLength > MaximumLength else ()
            ):
                CheckWork(
                    "local-route-search-target",
                    Signal=Signal,
                    Target=Target,
                )
                Distance = min(
                    abs(Target[0] - Position[0])
                    + abs(Target[1] - Position[1])
                    + abs(Target[2] - Position[2])
                    for Position in OwnedNodes
                )
                if Distance > MaximumLocalRouteLength:
                    continue
                Path = FindLocalPath(OwnedNodes, Target, Signal)
                if not Path:
                    continue
                Paths.append(Path)
                OwnedNodes.update(Path)
                LocalTargets.append(Target)
            if not Paths:
                continue
            Nodes = frozenset(Position for Path in Paths for Position in Path)
            Edges = frozenset(
                NormalizeRoutingEdge(First, Second)
                for Path in Paths
                for First, Second in zip(Path, Path[1:])
            )
            ClusterCandidates = [
                ClusterByGate[Name]
                for Target in LocalTargets
                if (Name := GateByInputPin.get(Target)) in ClusterByGate
            ]
            ClusterId = (
                ProducerCluster
                if ProducerCluster is not None
                else min(ClusterCandidates, default=-1)
            )
            CandidateClaim = LocalRouteClaim(
                Signal=Signal,
                ClusterId=ClusterId,
                Root=Root,
                ConnectedTargets=tuple(sorted(set(LocalTargets))),
                BoundaryNodes=SelectBoundaryNodes(
                    Nodes, AllTargets, LocalTargets
                ),
                Nodes=Nodes,
                Edges=Edges,
                Claims=LocalResourceGraph.BuildRouteClaims(Nodes),
                ExactRouteSignalBlocks=len(Nodes),
                ExactRouteSupportBlocks=len({
                    (X, Y - 1, Z) for X, Y, Z in Nodes
                } - ActualBlocks),
            )
            TrialClaims = (*LocalRouteClaims, CandidateClaim)
            try:
                ValidateLocalSignalStrength(CandidateClaim)
                ValidateLocalPhysicalConnectivity(CandidateClaim)
                ValidateContinuationPortal(CandidateClaim, AllTargets)
                ValidateBoundaryEscapes(CandidateClaim)
                ValidateLocalRouteClaims(LocalResourceGraph, TrialClaims)
                if any(len(Path) - 1 > MaximumLength for Path in Paths):
                    ValidateTemplateIsolation(
                        {Signal: set(CandidateClaim.Nodes)},
                        ActualBlocks,
                        ElectricalBlocks,
                        SolidBlocks,
                        Producers,
                        TargetsBySignal,
                        AccessBySignal,
                    )
            except ValueError as Error:
                LocalRouteDiagnostics[Signal] = {
                    "AttemptedTargets": len(set(LocalTargets)),
                    "AttemptedNodes": len(Nodes),
                    "Rejected": str(Error),
                }
                if not DirectPaths or len(DirectPaths) == len(Paths):
                    continue
                Paths = DirectPaths
                LocalTargets = DirectTargets
                Nodes = frozenset(
                    Position for Path in Paths for Position in Path
                )
                Edges = frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for Path in Paths
                    for First, Second in zip(Path, Path[1:])
                )
                CandidateClaim = LocalRouteClaim(
                    Signal=Signal,
                    ClusterId=ClusterId,
                    Root=Root,
                    ConnectedTargets=tuple(sorted(set(LocalTargets))),
                    BoundaryNodes=SelectBoundaryNodes(
                    Nodes, AllTargets, LocalTargets
                    ),
                    Nodes=Nodes,
                    Edges=Edges,
                    Claims=LocalResourceGraph.BuildRouteClaims(Nodes),
                    ExactRouteSignalBlocks=len(Nodes),
                    ExactRouteSupportBlocks=len({
                        (X, Y - 1, Z) for X, Y, Z in Nodes
                    } - ActualBlocks),
                )
                try:
                    ValidateLocalSignalStrength(CandidateClaim)
                    ValidateLocalPhysicalConnectivity(CandidateClaim)
                    ValidateContinuationPortal(CandidateClaim, AllTargets)
                    ValidateBoundaryEscapes(CandidateClaim)
                    ValidateLocalRouteClaims(
                        LocalResourceGraph,
                        (*LocalRouteClaims, CandidateClaim),
                    )
                except ValueError:
                    continue
            if (
                PackingPolicy.RequireCompleteLocalFanoutClaims
                and len(Clusters) == 1
                and len(LocalTargets) != len(AllTargets)
            ):
                LocalRouteDiagnostics.setdefault(Signal, {}).update({
                    "ReleasedForCompleteFanout": ClusterId,
                })
                continue
            if (
                len(Clusters) > 4
                and RelocationSignals
                and len(LocalTargets) != len(AllTargets)
            ):
                LocalRouteDiagnostics.setdefault(Signal, {}).update({
                    "ReleasedForGlobalRelocation": ClusterId,
                })
                continue
            # Do not claim the tree greedily.  Keep the complete local tree
            # and its direct-only baseline for the bounded cluster assignment
            # below; the latter is often the compatible choice when a denser
            # neighbouring net needs the same clearance or support resource.
            CandidateChoices = JointLocalCandidatesByCluster.setdefault(
                ClusterId, {}
            ).setdefault(Signal, [])
            CandidateChoices.append(
                LocalClusterRouteCandidate(
                    CandidateId=(
                        f"cluster{ClusterId}:{Signal}:tree:"
                        f"{len(CandidateChoices)}"
                    ),
                    Claim=CandidateClaim,
                )
            )
            if (
                DirectPaths
                and (tuple(DirectTargets) != tuple(LocalTargets))
                and len(CandidateChoices)
                < PackingPolicy.MaximumLocalRouteCandidatesPerSignal
            ):
                DirectNodes = frozenset(
                    Position for Path in DirectPaths for Position in Path
                )
                DirectEdges = frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for Path in DirectPaths
                    for First, Second in zip(Path, Path[1:])
                )
                DirectClaim = LocalRouteClaim(
                    Signal=Signal,
                    ClusterId=ClusterId,
                    Root=Root,
                    ConnectedTargets=tuple(sorted(set(DirectTargets))),
                    BoundaryNodes=SelectBoundaryNodes(
                        DirectNodes, AllTargets, DirectTargets
                    ),
                    Nodes=DirectNodes,
                    Edges=DirectEdges,
                    Claims=LocalResourceGraph.BuildRouteClaims(DirectNodes),
                    ExactRouteSignalBlocks=len(DirectNodes),
                    ExactRouteSupportBlocks=len({
                        (X, Y - 1, Z) for X, Y, Z in DirectNodes
                    } - ActualBlocks),
                )
                try:
                    ValidateLocalSignalStrength(DirectClaim)
                    ValidateLocalPhysicalConnectivity(DirectClaim)
                    ValidateContinuationPortal(DirectClaim, AllTargets)
                    ValidateBoundaryEscapes(DirectClaim)
                    ValidateLocalRouteClaims(
                        LocalResourceGraph, (DirectClaim,)
                    )
                except ValueError as Error:
                    LocalRouteDiagnostics.setdefault(Signal, {}).setdefault(
                        "DirectCandidateRejected", str(Error)
                    )
                else:
                    CandidateChoices.append(
                        LocalClusterRouteCandidate(
                            CandidateId=(
                                f"cluster{ClusterId}:{Signal}:direct:"
                                f"{len(CandidateChoices)}"
                            ),
                            Claim=DirectClaim,
                        )
                    )
            LocalRouteDiagnostics.setdefault(Signal, {}).update({
                "AcceptedTargets": len(set(LocalTargets)),
                "AcceptedNodes": len(Nodes),
                "UsedLongRoute": any(
                    len(Path) - 1 > MaximumLength for Path in Paths
                ),
            })
        if PackingPolicy.EnableJointLocalRouting:
            JointDiagnostics: dict[str, object] = {
                "Enabled": True,
                "CandidateLimitPerSignal": (
                    PackingPolicy.MaximumLocalRouteCandidatesPerSignal
                ),
                "AssignmentExpansionLimit": (
                    PackingPolicy.MaximumLocalClusterAssignmentExpansions
                ),
                "Clusters": {},
            }
            for ClusterId, CandidateMap in sorted(JointLocalCandidatesByCluster.items()):
                BaseClaims = tuple(LocalRouteClaims)
                LimitedCandidateMap = {
                    Signal: tuple(Candidates[:PackingPolicy.MaximumLocalRouteCandidatesPerSignal])
                    for Signal, Candidates in sorted(CandidateMap.items())
                }
                Selection = SelectJointLocalClusterCandidates(
                    LocalResourceGraph,
                    BaseClaims,
                    LimitedCandidateMap,
                    PackingPolicy.MaximumLocalClusterAssignmentExpansions,
                )
                SelectedClaims = tuple(
                    Candidate.Claim for Candidate in Selection.Candidates
                )
                LocalRouteClaims.extend(SelectedClaims)
                JointDiagnostics["Clusters"][str(ClusterId)] = {
                    "AttemptedSignals": len(LimitedCandidateMap),
                    "AttemptedCandidates": sum(
                        len(Candidates)
                        for Candidates in LimitedCandidateMap.values()
                    ),
                    "SelectedCandidates": len(Selection.Candidates),
                    "LocalizedTargets": sum(
                        Candidate.LocalizedTargetCount
                        for Candidate in Selection.Candidates
                    ),
                    "LocalRepeaters": sum(
                        Candidate.RepeaterCount for Candidate in Selection.Candidates
                    ),
                    "RouteAndSupportBlocks": sum(
                        Candidate.RouteAndSupportBlocks
                        for Candidate in Selection.Candidates
                    ),
                    "AssignmentExpansions": Selection.AssignmentExpansions,
                    "BudgetExhausted": Selection.BudgetExhausted,
                    "RejectionCounts": Selection.RejectionCounts,
                }
            FullyLocalizedSignals = {
                Claim.Signal
                for Claim in LocalRouteClaims
                if set(TargetsBySignal.get(Claim.Signal, ())).issubset(
                    Claim.ConnectedTargets
                )
            }
            JointDiagnostics["Aggregate"] = {
                "CandidateCount": sum(
                    sum(len(Candidates) for Candidates in CandidateMap.values())
                    for CandidateMap in JointLocalCandidatesByCluster.values()
                ),
                "LocalClaimCoverageBefore": 0,
                "LocalClaimCoverageAfter": sum(
                    len(Claim.Claims.ResourceIds) for Claim in LocalRouteClaims
                ),
                "SelectedClaimCount": len(LocalRouteClaims),
                "LocalizedTargetCount": sum(
                    len(Claim.ConnectedTargets) for Claim in LocalRouteClaims
                ),
                "GlobalNetCountBefore": len(TargetsBySignal),
                "GlobalNetCountAfter": len(TargetsBySignal) - len(FullyLocalizedSignals),
                "GlobalNetCountReduction": len(FullyLocalizedSignals),
                "EstimatedLocalVolume": sum(
                    LocalClusterRouteCandidate("selected", Claim).FullVolume
                    for Claim in LocalRouteClaims
                ),
            }
            LocalRouteDiagnostics["__JointLocalRouting__"] = JointDiagnostics
        else:
            # Compatibility mode retains the original deterministic signal
            # ordering when a caller explicitly opts out of joint selection.
            for CandidateMap in JointLocalCandidatesByCluster.values():
                for Candidates in CandidateMap.values():
                    if Candidates:
                        LocalRouteClaims.append(Candidates[0].Claim)
        for CandidateClaim in LocalRouteClaims:
            Signal = CandidateClaim.Signal
            LocalNetBranches[Signal] = tuple(sorted(CandidateClaim.Nodes))
            LocalNetTargets[Signal] = tuple(sorted(CandidateClaim.ConnectedTargets))
            if (
                len(CandidateClaim.ConnectedTargets) == len(TargetsBySignal[Signal])
                and len(CandidateClaim.Nodes)
                <= PackingPolicy.MaximumFrozenLocalNetNodes
                and len(CandidateClaim.ConnectedTargets)
                <= PackingPolicy.MaximumFrozenLocalTargets
            ):
                FrozenNetWires[Signal] = LocalNetBranches[Signal]
        Placed.FrozenNetWires = FrozenNetWires
        Placed.LocalNetBranches = LocalNetBranches
        Placed.LocalNetTargets = LocalNetTargets
        Placed.LocalRouteClaims = tuple(LocalRouteClaims)
        if RelocationSignals:
            LocalRouteDiagnostics["__PlacementRelocation__"] = {
                "Signals": sorted(RelocationSignals),
                "PrioritySignals": sorted(RelocationPrioritySignals),
                "RequiredSignals": sorted(RequiredRelocationSignals),
                "Variant": RelocationVariant,
                "Clusters": sorted(PhysicallyRelocatedClusters),
                "MirroredClusters": sorted(MirroredRelocationClusters),
            }
        Placed.LocalRouteDiagnostics = LocalRouteDiagnostics
    if RoutingSpacing == 0:
        Placed = CompactWeightedPlacement(
            Module,
            Placed,
            MaximumPasses=(
                PlacementPolicy.CompactPassLimit
                if PlacementPolicy is not None
                else 32
            ),
            WorkCheck=WorkCheck,
        )
    Guided = AddPcbRoutingGuides(
        Placed,
        MaximumLayerCount=(
            PlacementPolicy.MaximumRoutingLayers
            if PlacementPolicy is not None
            else 0
        ),
    )
    GateByName = {Gate.Name: Gate for Gate in PlacedGates}
    ConsumersBySignal: dict[str, list[Any]] = {}
    ProducersBySignal = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    for Gate in Module.Gates:
        for Signal in Gate.Inputs:
            ConsumersBySignal.setdefault(Signal, []).append(Gate)
    PackedClusters = []
    ClaimsByCluster: dict[int, list[LocalRouteClaim]] = {}
    for Claim in Placed.LocalRouteClaims:
        ClaimsByCluster.setdefault(Claim.ClusterId, []).append(Claim)
    for ClusterIndex, Names in enumerate(Clusters):
        CheckWork(
            "boundary-capacity",
            CompletedClusters=ClusterIndex,
            TotalClusters=len(Clusters),
        )
        NameSet = set(Names)
        Produced = {
            Signal
            for Name in Names
            for Signal in InternalByName[Name].Outputs
        }
        InternalSignals = {
            Signal
            for Signal in Produced
            if any(Gate.Name in NameSet for Gate in ConsumersBySignal.get(Signal, ()))
            and all(Gate.Name in NameSet for Gate in ConsumersBySignal.get(Signal, ()))
        }
        BoundarySignals = {
            Signal
            for Name in Names
            for Signal in (*InternalByName[Name].Inputs, *InternalByName[Name].Outputs)
            if Signal not in InternalSignals
        }
        DirectConnections = []
        for Signal in sorted(InternalSignals):
            Producer = next(
                GateByName[Name]
                for Name in Names
                if Signal in GateByName[Name].Outputs
            )
            if any(
                Producer.OutputPin in Consumer.InputPins
                for Consumer in (GateByName[Gate.Name] for Gate in ConsumersBySignal[Signal])
            ):
                DirectConnections.append(Signal)
        BaseX = min(GateByName[Name].X for Name in Names)
        BaseZ = min(GateByName[Name].Z for Name in Names)
        MaximumClusterX = max(
            GateByName[Name].X
            + RotatedCellSize(
                GateByName[Name].Kind,
                GateByName[Name].Rotation,
            )[0]
            - 1
            for Name in Names
        )
        MaximumClusterZ = max(
            GateByName[Name].Z
            + RotatedCellSize(
                GateByName[Name].Kind,
                GateByName[Name].Rotation,
            )[1]
            - 1
            for Name in Names
        )
        ClusterCenterX = (BaseX + MaximumClusterX) / 2
        ClusterCenterZ = (BaseZ + MaximumClusterZ) / 2

        def PreferredBoundarySide(Signal: str) -> str:
            ExternalGates = [
                GateByName[Gate.Name]
                for Gate in ConsumersBySignal.get(Signal, ())
                if Gate.Name not in NameSet and Gate.Name in GateByName
            ]
            Producer = ProducersBySignal.get(Signal)
            if (
                Producer is not None
                and Producer.Name not in NameSet
                and Producer.Name in GateByName
            ):
                ExternalGates.append(GateByName[Producer.Name])
            if not ExternalGates:
                return "East"
            TargetX = sum(Gate.X for Gate in ExternalGates) / len(ExternalGates)
            TargetZ = sum(Gate.Z for Gate in ExternalGates) / len(ExternalGates)
            DeltaX = TargetX - ClusterCenterX
            DeltaZ = TargetZ - ClusterCenterZ
            if abs(DeltaX) >= abs(DeltaZ):
                return "East" if DeltaX >= 0 else "West"
            return "South" if DeltaZ >= 0 else "North"

        BoundaryDemand = {
            Signal: max(
                1,
                sum(
                    Consumer.Name not in NameSet
                    for Consumer in ConsumersBySignal.get(Signal, ())
                ),
            )
            for Signal in sorted(BoundarySignals)
        }
        BoundaryDemandRecords = tuple(
            BoundaryDemandRecord(
                Signal=Signal,
                UnresolvedTargets=BoundaryDemand[Signal],
                RequiredPortalSlots=1,
                RequiredCorridorLanes=1,
                PreferredBoundarySide=PreferredBoundarySide(Signal),
            )
            for Signal in sorted(BoundarySignals)
        )
        BoundaryPitch = (
            PlacementPolicy.DemandAwareBoundaryTrackPitch
            if (
                PlacementPolicy is not None
                and PlacementPolicy.EnableDemandAwareInterClusterSpacing
                and PlacementPolicy.DemandAwareBoundaryTrackPitch > 0
            )
            else DefaultRedstoneRoutingTechnology.TrackPitch
        )
        BoundaryLayerCapacity = (
            PlacementPolicy.MaximumRoutingLayers
            if PlacementPolicy is not None
            and PlacementPolicy.MaximumRoutingLayers > 0
            else DefaultRedstoneRoutingTechnology.MaximumRoutableLayerCount
        )
        GeometricCapacity = {
            "West": max(1, (MaximumClusterZ - BaseZ + 1) // BoundaryPitch)
            * BoundaryLayerCapacity,
            "East": max(1, (MaximumClusterZ - BaseZ + 1) // BoundaryPitch)
            * BoundaryLayerCapacity,
            "North": max(1, (MaximumClusterX - BaseX + 1) // BoundaryPitch)
            * BoundaryLayerCapacity,
            "South": max(1, (MaximumClusterX - BaseX + 1) // BoundaryPitch)
            * BoundaryLayerCapacity,
        }
        LegalPortalSlotsBySide = dict(GeometricCapacity)
        if PackedMode:
            AccessPositionsBySignal = {
                Signal: set(
                    AccessByClusterSignal.get((ClusterIndex, Signal), ())
                )
                for Signal in BoundarySignals
            }
            for Claim in ClaimsByCluster.get(ClusterIndex, ()):
                if Claim.Signal not in BoundarySignals:
                    continue
                AccessPositionsBySignal.setdefault(
                    Claim.Signal,
                    set(),
                ).update(Claim.BoundaryNodes)
            LegalEscapeSlotsBySignal = BuildLegalBoundaryEscapeSlots(
                BoundarySignals,
                AccessPositionsBySignal,
                LocalResourceGraph,
                AccessClaimsBySignal,
                WorkCheck=WorkCheck,
            )
            HardBoundary = EvaluateHardBoundaryFeasibility(
                ClusterIndex,
                BoundaryDemandRecords,
                LegalEscapeSlotsBySignal,
            )
            ValidateHardBoundaryFeasibility(HardBoundary)
            SlotsBySide = {
                "West": set(),
                "East": set(),
                "North": set(),
                "South": set(),
            }
            for X, Y, Z in {
                Slot
                for Slots in LegalEscapeSlotsBySignal.values()
                for Slot in Slots
            }:
                Side = min(
                    (
                        (abs(X - BaseX), "West"),
                        (abs(X - MaximumClusterX), "East"),
                        (abs(Z - BaseZ), "North"),
                        (abs(Z - MaximumClusterZ), "South"),
                    )
                )[1]
                SlotsBySide[Side].add((X, Y, Z))
            LegalPortalSlotsBySide = {
                Side: len(Slots) for Side, Slots in SlotsBySide.items()
            }
            BoundaryDemandRecords = AssignBoundaryDemandSides(
                BoundaryDemandRecords,
                LegalEscapeSlotsBySignal,
                (BaseX, MaximumClusterX, BaseZ, MaximumClusterZ),
                {
                    Side: min(
                        GeometricCapacity[Side],
                        LegalPortalSlotsBySide[Side],
                    )
                    for Side in GeometricCapacity
                },
            )
        BoundaryCapacityRecords = BuildBoundaryCapacityRecords(
            BoundaryDemandRecords,
            GeometricCapacity,
            LegalPortalSlotsBySide,
        )
        BoundaryOverflow = sum(
            Record.Overflow for Record in BoundaryCapacityRecords
        )
        ScarceSides = {
            Record.BoundarySide
            for Record in BoundaryCapacityRecords
            if Record.Overflow > 0
        }
        PinScarcityCount = sum(
            Record.PreferredBoundarySide in ScarceSides
            for Record in BoundaryDemandRecords
        )
        LocalClaimTargets = sum(
            len(Claim.ConnectedTargets)
            for Claim in ClaimsByCluster.get(ClusterIndex, ())
        )
        BoundaryTargetCount = sum(BoundaryDemand.values())
        PackedClusters.append(
            PackedNandCluster(
                ClusterId=ClusterIndex,
                MemberNands=tuple(Names),
                BoundarySignals=tuple(sorted(BoundarySignals)),
                InternalSignals=tuple(sorted(InternalSignals)),
                RelativePlacements={
                    Name: (
                        GateByName[Name].X - BaseX,
                        GateByName[Name].Z - BaseZ,
                        GateByName[Name].Rotation,
                        GateByName[Name].MirrorX,
                    )
                    for Name in Names
                },
                DirectConnections=tuple(DirectConnections),
                LocalClaimSignals=tuple(sorted({
                    Claim.Signal for Claim in ClaimsByCluster.get(ClusterIndex, ())
                })),
                BoundaryTerminals=tuple(sorted({
                    Position
                    for Claim in ClaimsByCluster.get(ClusterIndex, ())
                    for Position in Claim.BoundaryNodes
                })),
                ExactLocalRoutingBlocks=sum(
                    Claim.ExactRoutingBlocks
                    for Claim in ClaimsByCluster.get(ClusterIndex, ())
                ),
                GlobalEntrances=len(BoundarySignals),
                StructuralSignature=ClusterStructuralSignatures.get(
                    ClusterIndex, ""
                ),
                ReusedFromClusterId=ClusterReuseSources.get(ClusterIndex),
                StructuralMapping=ClusterStructuralMappings.get(ClusterIndex),
                StackId=ClusterStackIds.get(ClusterIndex),
                StackLevel=ClusterStackLevels.get(ClusterIndex, 0),
                BaseY=(
                    1
                    if not PackedMode
                    else 1
                    + ClusterStackLevels.get(ClusterIndex, 0)
                    * PackingPolicy.ClusterDeckPitch
                ),
                BoundaryDemand=BoundaryDemand,
                EstimatedCorridorLanes=sum(BoundaryDemand.values()),
                LocalClaimCoverage=(
                    LocalClaimTargets / max(1, LocalClaimTargets + BoundaryTargetCount)
                ),
                BoundaryDemandRecords=BoundaryDemandRecords,
                BoundaryCapacityRecords=BoundaryCapacityRecords,
                BoundaryOverflow=BoundaryOverflow,
                PinScarcityCount=PinScarcityCount,
            )
        )
    CheckWork("complete", ClusterCount=len(Clusters))
    return PcbPlacement(
        Placed=Guided.Placed,
        Clusters=Clusters,
        SignalOrder=Guided.SignalOrder,
        LayerCount=Guided.LayerCount,
        PackedClusters=tuple(PackedClusters) if PackedMode else (),
    )
